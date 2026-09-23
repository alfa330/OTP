"""«Опубликовать статью и как новость» (просьба владельца 23.09.2026).

Роут отдаёт ЧЕРНОВИК новости из статьи: заголовок, текст в разметке новости,
картинки статьи — уже переложенными в кадры новости, и тренажёр статьи.
Публикует его человек в обычной форме «Новостей»: адресатов, тип и запуск
выбирает он, и все проверки выпуска — те же, что у любой новости.

ПОЧЕМУ ДВЕРЬ В ВИКЕ, А НЕ В НОВОСТЯХ. Вопрос «можно ли этому человеку читать
эту статью» знает только вика (периметр, правила разделов, строгий режим), и
файлы статьи отдаёт она же — тем же правилом, что ручка /file/<id>. Вторая
копия периметра в пакете новостей разошлась бы с первой молча. Обратное
направление вике уже знакомо: «Вопросы операторов» тоже выпускают новость
(wiki/routes_questions.py).

ПОЧЕМУ КАРТИНКИ КОПИРУЮТСЯ. Адрес картинки статьи — /api/wiki/file/<id>, то
есть ручка за двумя дверями вики (тумблер отдела и QR). Новость уходит и тем,
у кого вики нет, и окну Oktell, — там такой адрес не откроется. Поэтому кадр
переезжает в бакет новостей тем же конвейером, что и снимок из формы
(news/photos.py: пережатие в WebP), и ложится «ничьим» — к новости его
привяжет сохранение формы, как и любой загруженный в ней кадр.

КУРСОР ДЕРЖИТСЯ, ПОКА КАДРЫ ЕДУТ, и это тот же осознанный размен, что у
импорта документов (wiki/routes_import.py): отдельный слой проверки прав ради
того, чтобы отпустить соединение на пару секунд, дороже самого соединения.
Действие редкое — несколько раз в день, — и кадров не больше десяти.
"""

import base64
import logging
import re

from flask import jsonify

from news import access as news_access
from news import article_draft
from news import photos as news_photos
from news import queries as news_queries
from news import schema as news_schema

from . import articles as wiki_articles
from . import file_urls as wiki_file_urls
from . import perimeter as wiki_perimeter

# Картинки старых статей, перенесённые из прежней вики строкой data: (см.
# WikiEditor.jsx: allowBase64). Внешние адреса НЕ качаем вовсе: сервер,
# скачивающий по ссылке из тела статьи, — это дверь в чужую сеть.
_DATA_URI = re.compile(r'^data:(image/[a-z0-9.+-]+);base64,(.+)$', re.I | re.S)


def register(bp, wiki_route, db, log_ip, gcs):
    """gcs — тот же словарь, что у статей, плюс news_bucket_name: кадры
    новости живут в бакете новостей (bot_schedule2.py: _news_bucket_name)."""
    news_gcs = {'bucket_name': gcs.get('news_bucket_name'), 'client': gcs.get('client')}

    def _storage_ready():
        getter = news_gcs.get('bucket_name')
        return bool(callable(getter) and getter() and callable(news_gcs.get('client')))

    def _file_allowed(ctx, row, visible):
        """То же правило, что у ручки /file/<id> и у подписи адресов статьи
        (routes_articles._display_urls): файл статьи — если статья в периметре,
        ничей — только загрузившему."""
        owner_article = row.get('article_id')
        if owner_article:
            return owner_article in visible
        return row.get('uploaded_by') == ctx['user_id']

    def _image_bytes(client_of, src, rows, ctx, visible):
        """(байты, тип, имя) картинки статьи. None — брать нечего или нельзя."""
        ref = wiki_file_urls.FILE_REF.search(src)
        if ref:
            row = rows.get(ref.group(1).lower())
            if not row or not _file_allowed(ctx, row, visible):
                return None
            client = client_of()
            if client is None:
                return None
            data = client.bucket(row['bucket']).blob(row['blob_path']).download_as_bytes()
            return data, row.get('content_type') or '', row.get('original_name') or 'image'
        found = _DATA_URI.match(src)
        if not found:
            return None
        try:
            data = base64.b64decode(found.group(2), validate=False)
        except (ValueError, TypeError):
            return None
        return data, found.group(1).lower(), 'image'

    def _copy_images(cursor, ctx, sources, visible):
        """Картинки статьи → «ничьи» кадры новости. (кадры для формы, пропущено)."""
        if not sources:
            return [], 0
        if not (_storage_ready() and news_schema.photos_ready(cursor)):
            return [], len(sources)
        # Место — и в карусели одной новости, и в потолке «ничьих» кадров
        # человека: тот же потолок держит обычная загрузка (news/routes.py).
        free = max(0, min(news_schema.MAX_PHOTOS_PER_POST,
                          news_schema.MAX_LOOSE_PHOTOS_PER_USER
                          - news_queries.count_loose_photos(cursor, ctx['user_id'])))
        ids = [ref.group(1).lower() for ref in
               (wiki_file_urls.FILE_REF.search(src) for src in sources) if ref]
        rows = {str(row['id']).lower(): row
                for row in wiki_articles.files_for_display(cursor, ids)}
        # Имени файла files_for_display не отдаёт — кадр назовётся «image».
        # В карусели имя не видно никому, а лишний запрос ради него не нужен.
        held = []

        def client_of():
            if not held:
                try:
                    held.append(news_gcs['client']())
                except Exception:  # noqa: BLE001
                    logging.warning('Статья как новость: клиент хранилища недоступен',
                                    exc_info=True)
                    held.append(None)
            return held[0]

        stored, skipped = [], 0
        for src in sources:
            if len(stored) >= free:
                skipped += 1
                continue
            try:
                found = _image_bytes(client_of, src, rows, ctx, visible)
                if not found:
                    skipped += 1
                    continue
                data, content_type, name = found
                prepared = news_photos.prepare(data, filename=name, content_type=content_type)
                bucket, blob_path = news_photos.upload(news_gcs, prepared)
            except news_photos.PhotoError:
                # Кадр, который конвейер не принял (не картинка, больше 25 Мп),
                # — пропущенный, а не причина отказать во всей новости.
                skipped += 1
                continue
            except Exception:  # noqa: BLE001
                logging.exception('Статья как новость: картинка не переехала в новость')
                skipped += 1
                continue
            stored.append(news_queries.insert_loose_photo(
                cursor, prepared=prepared, bucket=bucket, blob_path=blob_path,
                uploaded_by=ctx['user_id']))
        return news_photos.sign_urls(news_gcs, stored), skipped

    @wiki_route('/articles/<int:article_id>/news-draft', methods=('POST',))
    def wiki_article_news_draft(cursor, ctx, article_id):
        """Черновик новости из статьи. Сама новость здесь НЕ создаётся."""
        _subjects, _sections, visible = wiki_perimeter.read_perimeter(cursor, ctx)
        article = wiki_articles.get_article(cursor, article_id=article_id)
        # 404, а не 403 — как у чтения статьи: «нет доступа» само по себе
        # сказало бы, что статья с таким номером есть.
        if not article or article['id'] not in visible:
            return jsonify({"error": "Статья не найдена"}), 404
        if article.get('status') != 'published':
            return jsonify({"error": "Новостью публикуется опубликованная статья — "
                                     "сначала опубликуйте её",
                            "code": "WIKI_NEWS_ARTICLE_DRAFT"}), 409
        if not news_schema.schema_is_ready(cursor):
            return jsonify({"error": "Раздел «Новости» ещё разворачивается",
                            "code": "WIKI_NEWS_NOT_READY"}), 503
        # Право выпуска — лестница новостей, а не права статьи: править статью
        # можно и поимённо выданным правилом, а адресовать новость отделу — нет.
        viewer = news_queries.load_viewer_context(cursor, ctx['user_id'])
        ceiling = (news_access.publish_ceiling(
            viewer['otp_role'], is_wiki_admin=news_queries.is_wiki_admin(cursor, ctx['user_id']))
            if viewer else None)
        if ceiling is None:
            return jsonify({"error": "Публиковать новости может супервайзер и выше",
                            "code": "WIKI_NEWS_FORBIDDEN"}), 403

        result = article_draft.draft(article)
        photos, skipped = _copy_images(cursor, ctx, result['images'], visible)
        key, problem = news_access.normalize_trainer_key(result['trainer_key'])
        return jsonify({
            'title': result['title'],
            'body': result['body'],
            'photos': photos,
            'trainer_key': None if problem else key,
            # Сколько картинок статьи в новость не попало: больше десяти, не
            # открылась, внешний адрес. Форма скажет об этом автору.
            'skipped_photos': skipped,
            'article_id': article['id'],
        })
