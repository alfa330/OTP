# -*- coding: utf-8 -*-
"""HTTP «Библиотеки» (Flask Blueprint), задача #282.

    GET    /api/library                      каталог + права смотрящего
    POST   /api/library/books                загрузить .epub           (управляющие)
    GET    /api/library/books/<id>           книга для ридера: оглавление, страницы, место
    DELETE /api/library/books/<id>           удалить книгу              (управляющие)
    GET    /api/library/books/<id>/file      сам файл .epub для epub.js
    PUT    /api/library/books/<id>/saved     {saved: bool} — «Сохранённые»
    PUT    /api/library/books/<id>/progress  {position, percent, page, at_end}
    GET    /api/library/analytics            мониторинг чтения          (управляющие)

КТО УПРАВЛЯЕТ — ровно по ТЗ: супер-админ и тренер (MANAGER_ROLES). Они
загружают и удаляют книги и видят мониторинг.

КТО ВИДИТ РАЗДЕЛ — пока те же двое (READER_ROLES): решение владельца 28.09.2026
перед первой выкладкой — «пусть доступ будет у суперадминов и тренера», «у
других пока даже раздел не будет отображаться». Остальным закрыт и пункт меню
(App.jsx: canAccessLibrarySection), и каждая ручка здесь. Открыть чтение всем —
расширить READER_ROLES и тот предикат вместе.

Фабрика получает зависимости аргументами и не импортирует bot_schedule2 —
тот сам подключает этот модуль (тот же приём, что у news и my_data).
"""

import logging
import re
from functools import wraps

from flask import Blueprint, Response, g, jsonify, request

from . import queries, storage
from .epub import MAX_EPUB_BYTES, EpubError, parse_epub
from .schema import schema_is_ready

# Роли, которым ТЗ даёт загрузку, удаление и мониторинг. Буквально: «Супер-админ
# / Тренер». Админ портала и глава отдела сюда не входят — расширять правило
# без решения владельца нельзя.
MANAGER_ROLES = frozenset({'super_admin', 'trainer'})

# Кому раздел открыт вообще. Пока — только управляющим (см. шапку модуля).
READER_ROLES = MANAGER_ROLES

# Место в книге: «номер главы:доля главы от 0 до 1» — 12:0.4375. Строгий
# формат, а не произвольная строка: иначе в базу можно было бы положить что угодно.
POSITION_RE = re.compile(r'^(\d{1,5}):(0(?:\.\d{1,6})?|1(?:\.0{1,6})?)$')

# Файл книги не меняется никогда: новая загрузка — новая книга с новым id.
# Поэтому браузеру разрешено держать его неделю, и повторное открытие книги
# не качает мегабайты заново. private — ответ адресован одному человеку.
_FILE_CACHE = 'private, max-age=604800, immutable'

# Обложка ужимается до этой стороны: карточка каталога показывает её шириной
# в пару сотен точек, а исходник в EPUB бывает и 3000 по высоте.
COVER_MAX_SIDE = 1200


def can_manage(role):
    return role in MANAGER_ROLES


def can_read(role):
    return role in READER_ROLES


def _prepare_cover(cover):
    """(байты, тип) обложки из книги -> (байты, тип) для бакета или None."""
    if not cover:
        return None
    data, content_type = cover
    try:
        from wiki import images as wiki_images
        converted = wiki_images.to_webp(data, content_type, max_side=COVER_MAX_SIDE)
    except Exception:  # noqa: BLE001 — без Pillow обложка ложится как есть
        converted = None
    if converted:
        return converted[0], converted[1]
    if content_type in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
        return data, content_type
    # SVG и прочее не показываем: <img> из бакета с SVG — лишняя дверь для
    # скрипта, а карточка без обложки рисует свою заглушку.
    return None


def build_library_blueprint(*, db, require_api_key, build_cors_preflight_response,
                            resolve_requester, normalize_role, gcs=None):
    """gcs — {'bucket_name': callable, 'client': callable}. Без него каталог и
    чтение уже загруженного работают, а загрузка честно отвечает 503."""

    bp = Blueprint('library', __name__, url_prefix='/api/library')
    schema_ready_once = []

    def _schema_ready(cursor):
        if schema_ready_once:
            return True
        if schema_is_ready(cursor):
            schema_ready_once.append(True)
            return True
        return False

    def library_route(rule, methods, *, manage=False):
        def decorator(handler):
            @bp.route(rule, methods=[*methods, 'OPTIONS'], endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(**kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    role = normalize_role(requester[3] if requester else None)
                    if not can_read(role):
                        return jsonify({
                            "error": "Раздел «Библиотека» пока открыт только супер-админу и тренеру",
                            "code": "LIBRARY_FORBIDDEN",
                        }), 403
                    if manage and not can_manage(role):
                        return jsonify({
                            "error": "Доступно супер-админу и тренеру",
                            "code": "LIBRARY_MANAGE_FORBIDDEN",
                        }), 403
                    return handler(int(requester_id), role, **kwargs)
                except EpubError as exc:
                    return jsonify({"error": exc.message, "code": exc.code}), 400
                except storage.StorageError as exc:
                    return jsonify({"error": exc.message, "code": exc.code}), exc.status
                except Exception:  # noqa: BLE001
                    logging.exception('library: ошибка в %s %s', request.method, request.path)
                    return jsonify({"error": "Не удалось обработать запрос библиотеки"}), 500

            return wrapper

        return decorator

    def _covers(rows):
        try:
            return storage.cover_urls(gcs, rows) if gcs else {}
        except Exception:  # noqa: BLE001 — без обложек каталог всё равно нужен
            logging.warning('library: обложки не подписались', exc_info=True)
            return {}

    @library_route('', ['GET'])
    def library_catalog(user_id, role):
        with db._get_cursor() as cursor:
            if not _schema_ready(cursor):
                return jsonify({"status": "success", "schema_ready": False, "books": [],
                                "can_manage": can_manage(role)}), 200
            rows = queries.list_books(cursor, user_id)
        covers = _covers(rows)
        return jsonify({
            "status": "success",
            "schema_ready": True,
            "can_manage": can_manage(role),
            "max_upload_mb": MAX_EPUB_BYTES // (1024 * 1024),
            "books": [queries.book_view(row, covers.get(row['id'])) for row in rows],
        }), 200

    @library_route('/books', ['POST'], manage=True)
    def library_upload(user_id, role):
        upload = request.files.get('file')
        if upload is None or not upload.filename:
            return jsonify({"error": "Выберите файл книги", "code": "LIBRARY_FILE_REQUIRED"}), 400
        original_name = str(upload.filename)
        # «Строго .epub» (ТЗ 4.1). Содержимое всё равно проверяет разбор ниже:
        # расширение — первая, а не единственная дверь.
        if not original_name.lower().endswith('.epub'):
            return jsonify({"error": "Можно загрузить только файл .epub",
                            "code": "LIBRARY_EPUB_ONLY"}), 400
        if (request.content_length or 0) > MAX_EPUB_BYTES + 1024 * 1024:
            return jsonify({"error": f"Файл больше {MAX_EPUB_BYTES // (1024 * 1024)} МБ",
                            "code": "LIBRARY_EPUB_TOO_LARGE"}), 413
        data = upload.read(MAX_EPUB_BYTES + 1)
        parsed = parse_epub(data, filename=original_name)

        bucket = storage.bucket_name(gcs)
        file_blob = storage.blob_path_for('books', original_name)
        storage.upload(gcs, bucket, file_blob, data, 'application/epub+zip')
        uploaded = [(bucket, file_blob)]
        cover_blob = cover_type = None
        try:
            cover = _prepare_cover(parsed['cover'])
            if cover:
                cover_blob = storage.blob_path_for('covers', 'cover.webp' if cover[1] == 'image/webp' else 'cover')
                storage.upload_cover(gcs, bucket, cover_blob, cover[0], cover[1])
                cover_type = cover[1]
                uploaded.append((bucket, cover_blob))
            with db._get_cursor() as cursor:
                book_id = queries.insert_book(
                    cursor, parsed=parsed, bucket=bucket, file_blob=file_blob,
                    file_size=len(data), original_name=original_name,
                    cover_blob=cover_blob, cover_type=cover_type, uploaded_by=user_id)
                row = queries.get_book(cursor, book_id, user_id)
        except Exception:
            # Строка не легла — файлы в бакете никому не нужны.
            storage.drop_blobs(gcs, uploaded)
            raise
        logging.info('library: загружена книга #%s «%s» (%s стр.) пользователем %s',
                     book_id, parsed['title'], parsed['total_pages'], user_id)
        covers = _covers([row])
        return jsonify({"status": "success",
                        "book": queries.book_view(row, covers.get(book_id))}), 201

    @library_route('/books/<int:book_id>', ['GET'])
    def library_book(user_id, role, book_id):
        with db._get_cursor() as cursor:
            row = queries.get_book(cursor, book_id, user_id)
        if not row:
            return jsonify({"error": "Книга не найдена", "code": "LIBRARY_BOOK_NOT_FOUND"}), 404
        covers = _covers([row])
        return jsonify({"status": "success",
                        "book": queries.reader_view(row, covers.get(book_id))}), 200

    @library_route('/books/<int:book_id>', ['DELETE'], manage=True)
    def library_delete(user_id, role, book_id):
        with db._get_cursor() as cursor:
            refs = queries.delete_book(cursor, book_id)
        if refs is None:
            return jsonify({"error": "Книга не найдена", "code": "LIBRARY_BOOK_NOT_FOUND"}), 404
        # Блобы — ПОСЛЕ фиксации: откатись удаление, строка ссылалась бы в пустоту.
        storage.drop_blobs(gcs, refs)
        logging.info('library: книга #%s удалена пользователем %s', book_id, user_id)
        return jsonify({"status": "success"}), 200

    @library_route('/books/<int:book_id>/file', ['GET'])
    def library_book_file(user_id, role, book_id):
        with db._get_cursor() as cursor:
            ref = queries.book_file_ref(cursor, book_id)
        if not ref:
            return jsonify({"error": "Книга не найдена", "code": "LIBRARY_BOOK_NOT_FOUND"}), 404
        if not gcs:
            raise storage.StorageError('Хранилище книг не настроено', code='LIBRARY_STORAGE_OFF')
        data = storage.download(gcs, ref['bucket'], ref['file_blob'])
        if data is None:
            return jsonify({"error": "Файл книги пропал из хранилища",
                            "code": "LIBRARY_FILE_MISSING"}), 404
        g.allow_public_cache = True
        response = Response(data, mimetype='application/epub+zip')
        response.headers['Cache-Control'] = _FILE_CACHE
        return response

    @library_route('/books/<int:book_id>/saved', ['PUT'])
    def library_book_saved(user_id, role, book_id):
        payload = request.get_json(silent=True) or {}
        saved = payload.get('saved')
        if not isinstance(saved, bool):
            return jsonify({"error": "Нужно поле saved: true или false",
                            "code": "LIBRARY_BAD_REQUEST"}), 400
        with db._get_cursor() as cursor:
            found = queries.set_saved(cursor, user_id, book_id, saved)
        if not found:
            return jsonify({"error": "Книга не найдена", "code": "LIBRARY_BOOK_NOT_FOUND"}), 404
        return jsonify({"status": "success", "saved": saved}), 200

    @library_route('/books/<int:book_id>/progress', ['PUT'])
    def library_book_progress(user_id, role, book_id):
        payload = request.get_json(silent=True) or {}
        position = str(payload.get('position') or '').strip()
        if not POSITION_RE.match(position):
            return jsonify({"error": "Нет места в книге", "code": "LIBRARY_BAD_REQUEST"}), 400
        try:
            percent = float(payload.get('percent'))
            page = int(payload.get('page'))
        except (TypeError, ValueError):
            return jsonify({"error": "Нужны percent и page", "code": "LIBRARY_BAD_REQUEST"}), 400
        if percent != percent:  # NaN
            return jsonify({"error": "Нужны percent и page", "code": "LIBRARY_BAD_REQUEST"}), 400
        at_end = payload.get('at_end') is True
        with db._get_cursor() as cursor:
            total_pages = queries.book_pages(cursor, book_id)
            if total_pages is None:
                return jsonify({"error": "Книга не найдена", "code": "LIBRARY_BOOK_NOT_FOUND"}), 404
            # Числа приходят из ридера — сервер лишь держит их в границах.
            # Последняя страница пролистана — это 100 % и последняя страница,
            # что бы ни насчитала арифметика внутри экрана.
            percent = 100.0 if at_end else max(0.0, min(99.9, round(percent, 2)))
            page = total_pages if at_end else max(1, min(total_pages, page))
            row = queries.save_progress(cursor, user_id, book_id, position=position,
                                        percent=percent, page=page, at_end=at_end)
        return jsonify({
            "status": "success",
            "progress": {
                "status": queries.status_of(True, row['finished_at']),
                "percent": 100.0 if row['finished_at'] else float(row['percent']),
                "page": int(row['page']),
                "finished_at": queries._iso(row['finished_at']),
                "updated_at": queries._iso(row['progress_updated_at']),
            },
        }), 200

    @library_route('/analytics', ['GET'], manage=True)
    def library_analytics(user_id, role):
        query = str(request.args.get('q') or '').strip()[:100]
        book_id = request.args.get('book_id', type=int)
        department_raw = str(request.args.get('department_id') or '').strip()
        department_id = 'none' if department_raw == 'none' else (
            int(department_raw) if department_raw.isdigit() else None)
        with db._get_cursor() as cursor:
            if not _schema_ready(cursor):
                return jsonify({"status": "success", "rows": [], "books": [],
                                "departments": [], "truncated": False}), 200
            rows, truncated = queries.analytics(cursor, query=query, book_id=book_id,
                                                department_id=department_id)
            books, departments = queries.analytics_filters(cursor)
        return jsonify({
            "status": "success",
            "rows": rows,
            "truncated": truncated,
            "limit": queries.ANALYTICS_LIMIT,
            "books": books,
            "departments": departments,
        }), 200

    return bp
