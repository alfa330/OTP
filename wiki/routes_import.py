"""Эндпоинты импорта документов и загрузки файлов.

Сессий импорта нет намеренно. В оригинале документ складывался в
document_import_sessions, правился во внешнем ONLYOFFICE и только потом
становился статьёй; ONLYOFFICE у нас нет (в проде вики он тоже выключен —
переменные не заданы), а джобы очистки сессий не существовало вовсе: на момент
дампа семь из восьми висели в статусе active с конца июля.

Здесь файл разбирается в HTML и сразу отдаётся в редактор. Сохраняет человек.

Импорта два, и разница между ними не в качестве, а в том, уходит ли документ во
внешний API:
  * /import — только разбор формата. Ничего никуда не отправляется;
  * /import/ai — тот же разбор плюс сборка статьи моделью и проверка на дубль;
  * /articles/similar — та же проверка на дубль, но по тому, что уже набрано в
    редакторе (кнопка «Такая статья уже есть?»). Живёт здесь, а не в routes_ai,
    потому что делит с импортом одну реализацию поиска похожего.

Сборка включается флажком «Поддержка ИИ» в редакторе, и без него /import/ai
отказывает: отправка чужого документа наружу должна быть осознанным действием
редактора, а не следствием того, что он нажал привычную кнопку.

Модель здесь вызывается, пока держится курсор из wiki_route, и это осознанный
размен. Сборка статьи занимает 3-5 секунд против 1-4 у ответа в чате, зато
случается несколько раз в день, а не постоянно: два занятых соединения из
сорока на несколько секунд дешевле, чем второй, отдельно написанный слой
проверки прав ради их экономии.
"""

import os
import re

from flask import jsonify, request

from . import articles as wiki_articles
from . import importer as wiki_importer
from . import perimeter as wiki_perimeter
from . import queries
from . import sanitize as wiki_sanitize
from .ai import authoring as ai_authoring
from .ai import embed as ai_embed
from .ai import providers as ai_providers
from .ai import similar as ai_similar

# Картинки и PDF модель читает сама — см. шапку wiki/ai/authoring.py про то,
# почему для них нет другого пути.
_VISION_MIME = {
    '.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg', '.webp': 'image/webp',
}
_TRUE = ('1', 'true', 'yes', 'on', 'да')

# Предел для файла, который уходит В МОДЕЛЬ. Он ниже общего предела импорта (25 МБ)
# не из осторожности: inline-данные едут base64, то есть вырастают на треть, а
# запрос к Vertex ограничен примерно 20 МБ. На 25-мегабайтном PDF пользователь
# получил бы невнятную ошибку транспорта вместо понятного «файл слишком большой».
_VISION_MAX_BYTES = 12 * 1024 * 1024


def register(bp, wiki_route, db, log_ip, gcs):

    def _store_file(cursor, *, data, filename, content_type, uploaded_by,
                    article_id=None):
        """Кладёт файл в GCS и заводит запись, возвращая постоянный адрес.

        Постоянный, а не подписанный: подпись живёт часы, а статья — годами.
        Именно поэтому картинки внутри уроков LMS со временем перестают
        открываться.
        """
        bucket = gcs['bucket_name']() if gcs.get('bucket_name') else None
        if not bucket:
            return None

        blob_path = wiki_importer.blob_path_for(filename, content_type)
        client = gcs['client']()
        client.bucket(bucket).blob(blob_path).upload_from_string(
            data, content_type=content_type or 'application/octet-stream')

        file_id = wiki_articles.register_file(
            cursor, article_id=article_id, bucket=bucket, blob_path=blob_path,
            original_name=str(filename or 'file')[:255],
            content_type=content_type, file_size=len(data),
            width=None, height=None, uploaded_by=uploaded_by,
        )
        return '/api/wiki/file/%s' % file_id

    # ── Импорт документа ─────────────────────────────────────────────────
    @wiki_route('/import', methods=('POST',), capability='can_create')
    def wiki_import(cursor, ctx):
        uploaded = request.files.get('file')
        if not uploaded or not uploaded.filename:
            return jsonify({"error": "Файл не выбран"}), 400

        data = uploaded.read()
        try:
            result = wiki_importer.convert(
                uploaded.filename, data,
                store_image=lambda blob, content_type: _store_file(
                    cursor, data=blob, filename='image', content_type=content_type,
                    uploaded_by=ctx['user_id']),
            )
        except wiki_importer.ImportError_ as exc:
            return jsonify({"error": str(exc), "code": "WIKI_IMPORT_FAILED"}), 400

        queries.log_action(cursor, actor_id=ctx['user_id'], action='article.import',
                           entity_type='article', entity_id=None,
                           details={'file': uploaded.filename, 'kind': result['kind'],
                                    'images': len(result['images'])},
                           ip_address=log_ip())
        return jsonify(result)

    # ── Импорт документа через ИИ ────────────────────────────────────────
    def _document_words(plain):
        """Слова документа для лексической ветки поиска дублей.

        dict.fromkeys сохраняет порядок первого появления: начало документа
        описывает тему точнее, чем его хвост, а брать все слова статьи на 17
        тысяч знаков (максимум корпуса) — значит утопить редкие слова в частых.
        """
        words = re.findall(r'[^\W\d_]{4,}', str(plain or '').lower(), re.UNICODE)
        return list(dict.fromkeys(words))[:40]

    def _duplicates(cursor, ctx, *, title, content, exclude_id=None,
                    allow_vector=True):
        """Есть ли уже такая статья. Пустой ответ — не доказательство отсутствия.

        allow_vector=False — смысловая ветка не считается ВООБЩЕ. Это не
        оптимизация: вектор считает внешний сервис, то есть текст статьи уходит
        наружу, а панель обещает ровно обратное, пока флажок «Поддержка ИИ»
        выключен. Обещание, которое нарушается там, где этого не видно, хуже
        отсутствующего. По названию и словам текста проверка при этом работает —
        она целиком у нас в базе.

        Вектор считается по названию и НАЧАЛУ текста, а не по всей статье: у
        документа на 17 тысяч знаков (максимум корпуса) вектор целого текста
        размывается до бессмысленного, а тема живёт в первых абзацах.
        """
        _subjects, _sections, visible = wiki_perimeter.read_perimeter(cursor, ctx)
        indexed = wiki_perimeter.eligible_article_ids(cursor, visible)
        plain = wiki_sanitize.to_plain_text(content)
        probe = ('%s. %s' % (title or '', plain[:1200])).strip()

        vector = None
        if allow_vector:
            try:
                vector = ai_embed.embed_query(probe)
            except Exception:
                vector = None      # лексика справится и одна, см. wiki/ai/similar.py

        found = ai_similar.find_duplicates(
            cursor, visible_ids=visible, indexed_ids=indexed,
            title=title, text_words=_document_words(plain), vector=vector,
            exclude_id=exclude_id)
        # degraded — про сбой эмбеддингов, а не про выключенный флажок: это
        # разные причины неполноты, и смешивать их значит врать в обеих.
        found['degraded'] = allow_vector and vector is None
        found['ai_support'] = bool(allow_vector)
        return found

    @wiki_route('/import/ai', methods=('POST',), capability='can_create')
    def wiki_import_ai(cursor, ctx):
        if str(request.form.get('ai_support') or '').strip().lower() not in _TRUE:
            return jsonify({
                "error": "Включите «Поддержка ИИ», чтобы собрать статью моделью",
                "code": "WIKI_AI_DISABLED",
            }), 400

        uploaded = request.files.get('file')
        if not uploaded or not uploaded.filename:
            return jsonify({"error": "Файл не выбран"}), 400

        data = uploaded.read()
        if not data:
            return jsonify({"error": "Пустой файл"}), 400
        if len(data) > wiki_importer.MAX_FILE_BYTES:
            return jsonify({"error": "Файл больше %d МБ"
                                     % (wiki_importer.MAX_FILE_BYTES // (1024 * 1024))}), 400

        ext = os.path.splitext(str(uploaded.filename))[1].lower()
        vision_mime = _VISION_MIME.get(ext)
        if vision_mime and len(data) > _VISION_MAX_BYTES:
            return jsonify({
                "error": "Файл больше %d МБ — модель столько за раз не прочитает. "
                         "Разделите документ на части"
                         % (_VISION_MAX_BYTES // (1024 * 1024)),
                "code": "WIKI_AI_FILE_TOO_BIG",
            }), 400
        images, kind = [], wiki_importer.SUPPORTED.get(ext, 'Файл')

        source_html = source_text = ''
        if not vision_mime:
            # Формат со сеткой внутри: разбираем программой, таблицы не отдаём модели.
            try:
                parsed = wiki_importer.convert(
                    uploaded.filename, data,
                    store_image=lambda blob, content_type: _store_file(
                        cursor, data=blob, filename='image', content_type=content_type,
                        uploaded_by=ctx['user_id']))
            except wiki_importer.ImportError_ as exc:
                return jsonify({"error": str(exc), "code": "WIKI_IMPORT_FAILED"}), 400
            source_html = parsed['content']
            source_text = wiki_sanitize.to_plain_text(source_html)
            images, kind = parsed['images'], parsed['kind']
        elif ext == '.pdf':
            # Текстовый слой PDF нужен НЕ для статьи, а для сверки чисел: статью
            # собирает модель по самому файлу, иначе таблицы не собрать.
            try:
                source_text = wiki_sanitize.to_plain_text(
                    wiki_importer.convert(uploaded.filename, data)['content'])
            except wiki_importer.ImportError_:
                source_text = ''      # скан: сверять числа будет не с чем
            kind = 'PDF'
        else:
            kind = 'Изображение'

        try:
            draft = ai_authoring.compose(
                filename=uploaded.filename, kind=kind,
                source_html=source_html, source_text=source_text,
                generate_fn=ai_providers.generate,
                blob=data if vision_mime else None, mime=vision_mime,
                generate_file_fn=ai_providers.generate_document)
        except ai_providers.ProviderError as error:
            return jsonify({"error": "ИИ недоступен", "detail": str(error)[:300],
                            "code": "WIKI_AI_UNAVAILABLE"}), 503

        if not draft['title']:
            draft['title'] = wiki_importer.title_from_filename(uploaded.filename)
        if not vision_mime and not source_text:
            draft['warnings'].append('Из файла не удалось получить текст для сверки '
                                     'чисел — проверьте цифры вручную')
        if ext in ('.png', '.jpg', '.jpeg', '.webp'):
            draft['warnings'].append('Источник — изображение: сверить числа с '
                                     'документом программа не может, проверьте их сами')

        duplicates = _duplicates(cursor, ctx, title=draft['title'],
                                 content=draft['content'])

        meta = draft.get('meta') or {}
        queries.log_action(
            cursor, actor_id=ctx['user_id'], action='article.ai_draft',
            entity_type='article', entity_id=None,
            details={'file': uploaded.filename, 'kind': kind,
                     'tables': draft['tables'], 'warnings': len(draft['warnings']),
                     'duplicate_verdict': duplicates.get('verdict'),
                     'provider': meta.get('provider'), 'model': meta.get('model')},
            ip_address=log_ip())

        return jsonify({
            'title': draft['title'], 'summary': draft['summary'],
            'content': draft['content'], 'warnings': draft['warnings'],
            'kind': kind, 'images': images, 'tables': draft['tables'],
            'duplicates': duplicates,
            'provider': meta.get('provider'), 'model': meta.get('model'),
            'elapsed': meta.get('elapsed'),
        })

    # ── Проверка «такая статья уже есть?» без документа ───────────────────
    @wiki_route('/articles/similar', methods=('POST',), capability='can_create')
    def wiki_articles_similar(cursor, ctx):
        data = request.get_json(silent=True) or {}
        title = ' '.join(str(data.get('title') or '').split())[:255]
        content = str(data.get('content') or '')
        if not title and not content.strip():
            return jsonify({"error": "Нужно название или текст"}), 400
        exclude = data.get('exclude_id')
        try:
            exclude = int(exclude) if exclude else None
        except (TypeError, ValueError):
            exclude = None
        # Флажок выключен — ищем только у себя в базе, наружу ничего не отдаём.
        allow_vector = str(data.get('ai_support', True)).strip().lower() in _TRUE
        return jsonify(_duplicates(cursor, ctx, title=title, content=content,
                                   exclude_id=exclude, allow_vector=allow_vector))

    # ── Загрузка картинки из редактора ───────────────────────────────────
    @wiki_route('/upload', methods=('POST',), capability='can_create')
    def wiki_upload(cursor, ctx):
        uploaded = request.files.get('file')
        if not uploaded or not uploaded.filename:
            return jsonify({"error": "Файл не выбран"}), 400

        data = uploaded.read()
        if len(data) > wiki_importer.MAX_FILE_BYTES:
            return jsonify({
                "error": "Файл больше %d МБ" % (wiki_importer.MAX_FILE_BYTES // (1024 * 1024)),
            }), 400

        content_type = (uploaded.mimetype or '').strip()
        if not content_type.startswith('image/'):
            return jsonify({"error": "Можно загружать только изображения"}), 400

        # Файл можно сразу привязать к статье. Без привязки он виден только
        # загрузившему — это защита на время, пока картинка ещё не в тексте.
        # Значит при миграции и при вставке в существующую статью привязку надо
        # указать явно, иначе картинки увидит один человек.
        article_id = None
        raw_article_id = request.form.get('article_id')
        if raw_article_id:
            try:
                article_id = int(raw_article_id)
            except (TypeError, ValueError):
                return jsonify({"error": "Неверный article_id"}), 400

            from . import access as wiki_access
            from . import queries as wiki_queries
            subjects = wiki_access.collect_subjects(
                user_id=ctx['user_id'], otp_role=ctx['otp_role'],
                department_id=ctx['department_id'],
                headed_department_ids=ctx['headed_department_ids'],
                direction_id=ctx['direction_id'], group_ids=ctx['group_ids'],
                wiki_role_ids=[r.get('id') for r in ctx['wiki_roles']],
            )
            sections = wiki_queries.allowed_section_ids(cursor, ctx, subjects)
            article = wiki_articles.get_article(cursor, article_id=article_id)
            if not article:
                return jsonify({"error": "Статья не найдена"}), 404
            permissions = wiki_articles.effective_permissions(
                cursor, ctx, article, subjects, sections,
                wiki_queries.section_rules_for_user)
            if not permissions.get('can_edit'):
                return jsonify({"error": "Нет права править эту статью"}), 403

        url = _store_file(cursor, data=data, filename=uploaded.filename,
                          content_type=content_type, uploaded_by=ctx['user_id'],
                          article_id=article_id)
        if not url:
            return jsonify({"error": "Хранилище файлов не настроено"}), 503
        return jsonify({"url": url}), 201
