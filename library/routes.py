# -*- coding: utf-8 -*-
"""HTTP «Библиотеки» (Flask Blueprint), задача #282.

    GET    /api/library                      каталог + жанры + права (+ отделы управляющим)
    POST   /api/library/books                загрузить .epub + отделы и жанры (управляющие)
    GET    /api/library/books/<id>           книга для ридера: оглавление, страницы, место
    PATCH  /api/library/books/<id>           {department_ids?, genre_ids?, archived?} (управляющие)
    DELETE /api/library/books/<id>           удалить книгу из архива    (управляющие)
    GET    /api/library/books/<id>/file      сам файл .epub для ридера
    PUT    /api/library/books/<id>/saved     {saved: bool} — «Сохранённые»
    PUT    /api/library/books/<id>/progress  {position, percent, page, at_end}
    GET    /api/library/analytics            мониторинг: кто что читает (управляющие)
    GET    /api/library/analytics/summary    мониторинг: общая и по отделам (управляющие)
    POST   /api/library/genres               {name} — создать жанр      (управляющие)
    PATCH  /api/library/genres/<id>          {name} — переименовать     (ведущие все отделы)
    DELETE /api/library/genres/<id>          удалить жанр, книги остаются (ведущие все отделы)

КТО ВЕДЁТ БИБЛИОТЕКУ (can_manage) — загружает и удаляет книги, правит их
отделы и жанры, ведёт справочник жанров, видит архив и мониторинг. По ТЗ это
были супер-админ и тренер; с 02.10.2026 — ещё админы (руководители) и СВ.
Тренер, руководители и супер-админ ведут ВСЕ отделы (FULL_MANAGER_ROLES), СВ —
только СВОЙ (DEPARTMENT_MANAGER_ROLES, класс Access):
  - видит книги, выданные его отделу, и архив среди них;
  - меняет (жанры, архив, удаление) только книгу одного своего отдела: книга,
    выданная и другим отделам, у него только для чтения — иначе «в архив»
    убрало бы её и у чужих (LIBRARY_BOOK_SHARED);
  - выдаёт книгу только своему отделу;
  - в мониторинге — только люди его отдела;
  - жанр создаёт, а переименовать и удалить его — у ведущих все отделы:
    справочник общий, и правка задела бы книги чужих отделов.

ОТДЕЛЫ (29.09.2026). Книга выдаётся одному или нескольким отделам — при
загрузке их выбирают обязательно; с 02.10.2026 — любому действующему отделу
(queries._DEPARTMENT_SELECTABLE). Управляющие видят книги всех отделов и
выбирают отдел в разделе сами; читатель — только книги своего отдела не из
архива (queries._READER_SEES), и каждая ручка одной книги проходит ту же
дверь (queries.open_book): чужая или архивная книга отвечает 404, как
несуществующая.

ЖАНРЫ (02.10.2026). Общий справочник библиотеки: жанры создают управляющие —
в окне книги или в окне «Жанры», — и у книги их сколько угодно, в том числе
ни одного. Каталог отдаёт справочник всем: по жанрам делится полка.

АРХИВ. Книгу убирают в архив, а не удаляют: закладки и прогресс остаются, в
мониторинге видно, кто её читал. Удалить насовсем можно только книгу из
архива — 409 иначе.

КТО ВИДИТ РАЗДЕЛ — с 02.10.2026 все сотрудники: кто не ведёт библиотеку,
тот её читает (READER_ROLES). Читатель видит только книги своего отдела не из
архива, сохраняет их и читает; управление ему закрыто. Роль вне шкалы
(пустая, незнакомая) не пускается никуда: ни пунктом меню (App.jsx:
canAccessLibrarySectionForUser), ни одной ручкой здесь.

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

# Кто ведёт библиотеку, по роли. Решения владельца 02.10.2026: «начиная св и
# выше отдай доступ на редактирование, архив, мониторинг и т. д.», затем
# «сделай так, чтобы св был виден свой раздел и только, а у тренера ко всем,
# руководителям и выше тоже выдать полный доступ».
#   все отделы — тренер (по ТЗ, с 28.09.2026), руководители (admin: главы
#   отделов) и супер-админ;
FULL_MANAGER_ROLES = frozenset({'super_admin', 'admin', 'trainer'})
#   только свой отдел — СВ (отдел — users.department_id).
DEPARTMENT_MANAGER_ROLES = frozenset({'sv'})
MANAGER_ROLES = FULL_MANAGER_ROLES | DEPARTMENT_MANAGER_ROLES
# Кто читает: «обычным операторам и сотрудникам — на чтение» — все остальные
# роли шкалы. Вместе с MANAGER_ROLES — ровно вся шкала (тест сверяет: новая
# роль без решения не останется). То же объединение — в App.jsx (LIBRARY_ROLES).
READER_ROLES = frozenset({'operator', 'trainee', 'hr_manager', 'accounting_manager', 'marketing_manager'})

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
    """Ведёт ли смотрящий библиотеку (все отделы или свой)."""
    return role in MANAGER_ROLES


class Access:
    """Что смотрящему можно в библиотеке — решено один раз на запрос.

    department_id — отдел, которым ограничен ведущий (СВ); None у ведущего
    все отделы. У читателя manager=False: его книги отбирает _READER_SEES.
    """

    __slots__ = ('manager', 'department_id')

    def __init__(self, manager=False, department_id=None):
        self.manager = bool(manager)
        self.department_id = int(department_id) if manager and department_id is not None else None

    @property
    def manages_all(self):
        return self.manager and self.department_id is None

    def can_edit(self, department_ids):
        """Можно ли менять книгу с этими отделами: ведущему все — любую, СВ —
        только выданную одному его отделу."""
        if not self.manager:
            return False
        if self.department_id is None:
            return True
        ids = {int(value) for value in (department_ids or [])}
        return ids == {self.department_id}


def access_for(role, department_id):
    """Роль и отдел смотрящего -> Access. СВ без отдела ничего не ведёт и
    читает как все — то есть ничего не видит: книги выдаются отделам."""
    if role in FULL_MANAGER_ROLES:
        return Access(True)
    if role in DEPARTMENT_MANAGER_ROLES and department_id:
        return Access(True, department_id)
    return Access(False)


def can_read(role):
    """Открыт ли раздел смотрящему: тем, кто ведёт, и читателям."""
    return role in MANAGER_ROLES or role in READER_ROLES


def parse_ids(values):
    """Отделы или жанры из запроса -> список id или None, если прислали не числа.

    Приходят списком (JSON) или повтором поля формы (загрузка книги идёт
    multipart). Пустой список — не ошибка разбора: отделов без него не
    бывает (это решает вызывающий), а жанров у книги может и не быть.
    """
    if not isinstance(values, (list, tuple)):
        return None
    ids = []
    for value in values:
        if isinstance(value, bool):
            return None
        text = str(value).strip()
        # isascii: «²» и «①» проходят isdigit(), а int() на них падает — 500
        # вместо 400.
        if not (text.isascii() and text.isdigit()) or len(text) > 9:
            return None
        ids.append(int(text))
    return sorted(set(ids))


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

    def library_route(rule, methods, *, manage=False, manage_all=False):
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
                            "error": "Раздел «Библиотека» вам пока не открыт",
                            "code": "LIBRARY_FORBIDDEN",
                        }), 403
                    # Отдел нужен только СВ — один короткий запрос; остальным
                    # всё решает роль.
                    department_id = None
                    if role in DEPARTMENT_MANAGER_ROLES:
                        with db._get_cursor() as cursor:
                            department_id = queries.user_department_id(cursor, requester_id)
                    access = access_for(role, department_id)
                    if (manage or manage_all) and not access.manager:
                        return jsonify({
                            "error": "Доступно только тем, кто ведёт библиотеку",
                            "code": "LIBRARY_MANAGE_FORBIDDEN",
                        }), 403
                    if manage_all and not access.manages_all:
                        return jsonify({
                            "error": "Жанры общие для всех отделов — переименовать или удалить "
                                     "жанр может тренер или руководитель",
                            "code": "LIBRARY_MANAGE_ALL_ONLY",
                        }), 403
                    # Ручкам уходит готовое решение, а не роль: правило «кто
                    # ведёт и что» живёт в одном месте.
                    return handler(int(requester_id), access, **kwargs)
                except EpubError as exc:
                    return jsonify({"error": exc.message, "code": exc.code}), 400
                except storage.StorageError as exc:
                    return jsonify({"error": exc.message, "code": exc.code}), exc.status
                except Exception:  # noqa: BLE001
                    logging.exception('library: ошибка в %s %s', request.method, request.path)
                    return jsonify({"error": "Не удалось обработать запрос библиотеки"}), 500

            return wrapper

        return decorator

    def _department_error(cursor, department_ids, access, *, book_id=None):
        """Ответ 400/403 о неверных отделах или None. Проверка — ДО любой
        записи: файл в бакет и строка книги уходят только с годным набором."""
        if department_ids is None:
            return jsonify({"error": "Отделы переданы неверно", "code": "LIBRARY_BAD_REQUEST"}), 400
        if not department_ids:
            return jsonify({"error": "Выберите хотя бы один отдел",
                            "code": "LIBRARY_DEPARTMENTS_REQUIRED"}), 400
        if not access.can_edit(department_ids):
            return jsonify({"error": "Книгу можно выдать только своему отделу",
                            "code": "LIBRARY_DEPARTMENT_NOT_YOURS"}), 403
        if queries.unknown_departments(cursor, department_ids, book_id=book_id):
            return jsonify({"error": "Такого отдела больше нет — обновите страницу",
                            "code": "LIBRARY_DEPARTMENT_UNKNOWN"}), 400
        return None

    def _genre_error(cursor, genre_ids):
        """Ответ 400 о неверных жанрах или None — так же ДО любой записи."""
        if genre_ids is None:
            return jsonify({"error": "Жанры переданы неверно", "code": "LIBRARY_BAD_REQUEST"}), 400
        if queries.unknown_genres(cursor, genre_ids):
            return jsonify({"error": "Такого жанра больше нет — обновите страницу",
                            "code": "LIBRARY_GENRE_UNKNOWN"}), 400
        return None

    def _genre_name():
        """Имя жанра из тела запроса -> (имя, None) или (None, ответ 400)."""
        payload = request.get_json(silent=True)
        name = queries.normalize_genre_name(payload.get('name') if isinstance(payload, dict) else None)
        if not name:
            return None, (jsonify({
                "error": f"Название жанра — от 1 до {queries.GENRE_NAME_MAX} знаков",
                "code": "LIBRARY_GENRE_NAME",
            }), 400)
        return name, None

    def _not_found():
        return jsonify({"error": "Книга не найдена", "code": "LIBRARY_BOOK_NOT_FOUND"}), 404

    def _edit_error(cursor, book_id, access):
        """Ответ 404/403, если эту книгу смотрящему не изменить, или None.

        Ведущему все отделы — любую существующую. СВ — только книгу своего
        отдела (иначе 404, как несуществующую), и только выданную ему одному:
        «в архив» и «удалить» общей книги задели бы чужие отделы. Строка книги
        берётся под замок — до правки набор отделов уже не сменится.
        """
        if access.manages_all:
            return None
        department_ids = queries.lock_book_departments(cursor, book_id)
        if department_ids is None or access.department_id not in department_ids:
            return _not_found()
        if not access.can_edit(department_ids):
            return jsonify({
                "error": "Книга выдана и другим отделам — изменить её может тренер или руководитель",
                "code": "LIBRARY_BOOK_SHARED",
            }), 403
        return None

    def _view(row, access, cover_url=None):
        view = queries.book_view(row, cover_url)
        # Можно ли менять книгу — решает сервер: фронт прячет меню «···» у
        # общей книги СВ, а не гадает по чужим отделам.
        view['can_edit'] = access.can_edit(view['department_ids'])
        return view

    def _covers(rows):
        try:
            return storage.cover_urls(gcs, rows) if gcs else {}
        except Exception:  # noqa: BLE001 — без обложек каталог всё равно нужен
            logging.warning('library: обложки не подписались', exc_info=True)
            return {}

    @library_route('', ['GET'])
    def library_catalog(user_id, access):
        with db._get_cursor() as cursor:
            if not _schema_ready(cursor):
                return jsonify({"status": "success", "schema_ready": False, "books": [],
                                "departments": [], "genres": [], "can_manage": access.manager,
                                "manage_all": access.manages_all}), 200
            rows = queries.list_books(cursor, user_id, manager=access.manager,
                                      department_id=access.department_id)
            # Отделы нужны тому, кто выбирает: переключатель отдела и окно
            # публикации. Читатель видит свой отдел, выбирать ему нечего; СВ —
            # только свой отдел.
            departments = (queries.library_departments(cursor, department_id=access.department_id)
                           if access.manager else [])
            # Жанры — всем: по ним делится полка и у читателя.
            genres = queries.list_genres(cursor)
        covers = _covers(rows)
        return jsonify({
            "status": "success",
            "schema_ready": True,
            "can_manage": access.manager,
            "manage_all": access.manages_all,
            "max_upload_mb": MAX_EPUB_BYTES // (1024 * 1024),
            "departments": departments,
            "genres": genres,
            "books": [_view(row, access, covers.get(row['id'])) for row in rows],
        }), 200

    @library_route('/books', ['POST'], manage=True)
    def library_upload(user_id, access):
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
        department_ids = parse_ids(request.form.getlist('department_ids'))
        genre_ids = parse_ids(request.form.getlist('genre_ids'))
        with db._get_cursor() as cursor:
            error = _department_error(cursor, department_ids, access) or _genre_error(cursor, genre_ids)
        if error:
            return error
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
                    cover_blob=cover_blob, cover_type=cover_type, uploaded_by=user_id,
                    department_ids=department_ids, genre_ids=genre_ids)
                row = queries.get_book(cursor, book_id, user_id)
        except Exception:
            # Строка не легла — файлы в бакете никому не нужны.
            storage.drop_blobs(gcs, uploaded)
            raise
        logging.info('library: загружена книга #%s «%s» (%s стр.) пользователем %s, отделы %s, жанры %s',
                     book_id, parsed['title'], parsed['total_pages'], user_id, department_ids, genre_ids)
        covers = _covers([row])
        return jsonify({"status": "success",
                        "book": _view(row, access, covers.get(book_id))}), 201

    @library_route('/books/<int:book_id>', ['GET'])
    def library_book(user_id, access, book_id):
        with db._get_cursor() as cursor:
            if queries.open_book(cursor, book_id, user_id, manager=access.manager,
                                department_id=access.department_id) is None:
                return _not_found()
            row = queries.get_book(cursor, book_id, user_id)
        if not row:
            return _not_found()
        covers = _covers([row])
        return jsonify({"status": "success",
                        "book": queries.reader_view(row, covers.get(book_id))}), 200

    @library_route('/books/<int:book_id>', ['PATCH'], manage=True)
    def library_update(user_id, access, book_id):
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Неверный запрос", "code": "LIBRARY_BAD_REQUEST"}), 400
        department_ids = None
        if 'department_ids' in payload:
            department_ids = parse_ids(payload.get('department_ids'))
        genre_ids = None
        if 'genre_ids' in payload:
            genre_ids = parse_ids(payload.get('genre_ids'))
        archived = payload.get('archived')
        if archived is not None and not isinstance(archived, bool):
            return jsonify({"error": "Поле archived: true или false", "code": "LIBRARY_BAD_REQUEST"}), 400
        if 'department_ids' not in payload and 'genre_ids' not in payload and archived is None:
            return jsonify({"error": "Нечего менять", "code": "LIBRARY_BAD_REQUEST"}), 400
        with db._get_cursor() as cursor:
            error = _edit_error(cursor, book_id, access)
            if not error and 'department_ids' in payload:
                error = _department_error(cursor, department_ids, access, book_id=book_id)
            if not error and 'genre_ids' in payload:
                error = _genre_error(cursor, genre_ids)
            if error:
                return error
            if not queries.update_book(cursor, book_id, user_id, department_ids=department_ids,
                                       genre_ids=genre_ids, archived=archived):
                return _not_found()
            row = queries.get_book(cursor, book_id, user_id)
        logging.info('library: книга #%s изменена пользователем %s (отделы %s, жанры %s, архив %s)',
                     book_id, user_id, department_ids, genre_ids, archived)
        covers = _covers([row])
        return jsonify({"status": "success",
                        "book": _view(row, access, covers.get(book_id))}), 200

    @library_route('/books/<int:book_id>', ['DELETE'], manage=True)
    def library_delete(user_id, access, book_id):
        with db._get_cursor() as cursor:
            error = _edit_error(cursor, book_id, access)
            if error:
                return error
            outcome, refs = queries.delete_book(cursor, book_id)
        if outcome == queries.DELETE_NOT_FOUND:
            return _not_found()
        if outcome == queries.DELETE_NOT_ARCHIVED:
            return jsonify({"error": "Сначала уберите книгу в архив",
                            "code": "LIBRARY_DELETE_NOT_ARCHIVED"}), 409
        # Блобы — ПОСЛЕ фиксации: откатись удаление, строка ссылалась бы в пустоту.
        storage.drop_blobs(gcs, refs)
        logging.info('library: книга #%s удалена пользователем %s', book_id, user_id)
        return jsonify({"status": "success"}), 200

    @library_route('/books/<int:book_id>/file', ['GET'])
    def library_book_file(user_id, access, book_id):
        with db._get_cursor() as cursor:
            if queries.open_book(cursor, book_id, user_id, manager=access.manager,
                                department_id=access.department_id) is None:
                return _not_found()
            ref = queries.book_file_ref(cursor, book_id)
        if not ref:
            return _not_found()
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
    def library_book_saved(user_id, access, book_id):
        payload = request.get_json(silent=True) or {}
        saved = payload.get('saved')
        if not isinstance(saved, bool):
            return jsonify({"error": "Нужно поле saved: true или false",
                            "code": "LIBRARY_BAD_REQUEST"}), 400
        with db._get_cursor() as cursor:
            if queries.open_book(cursor, book_id, user_id, manager=access.manager,
                                department_id=access.department_id) is None:
                return _not_found()
            queries.set_saved(cursor, user_id, book_id, saved)
        return jsonify({"status": "success", "saved": saved}), 200

    @library_route('/books/<int:book_id>/progress', ['PUT'])
    def library_book_progress(user_id, access, book_id):
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
            total_pages = queries.open_book(cursor, book_id, user_id, manager=access.manager,
                                department_id=access.department_id)
            if total_pages is None:
                return _not_found()
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

    def _department_arg(access):
        """Отдел мониторинга: у СВ — всегда его отдел, что бы ни прислали."""
        if access.department_id is not None:
            return access.department_id
        raw = str(request.args.get('department_id') or '').strip()
        return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 9 else None

    @library_route('/analytics', ['GET'], manage=True)
    def library_analytics(user_id, access):
        query = str(request.args.get('q') or '').strip()[:100]
        book_id = request.args.get('book_id', type=int)
        with db._get_cursor() as cursor:
            if not _schema_ready(cursor):
                return jsonify({"status": "success", "rows": [], "books": [], "truncated": False}), 200
            rows, truncated = queries.analytics(cursor, query=query, book_id=book_id,
                                                department_id=_department_arg(access))
            books = queries.analytics_books(cursor, department_id=access.department_id)
        return jsonify({
            "status": "success",
            "rows": rows,
            "truncated": truncated,
            "limit": queries.ANALYTICS_LIMIT,
            "books": books,
        }), 200

    # Сводка — отдельной ручкой: список «кто читает» перезапрашивается на
    # каждую букву поиска, а сводке фильтры списка не нужны — она меняется
    # только с отделом.
    @library_route('/analytics/summary', ['GET'], manage=True)
    def library_analytics_summary(user_id, access):
        with db._get_cursor() as cursor:
            if not _schema_ready(cursor):
                return jsonify({"status": "success", "total": None, "departments": None}), 200
            summary = queries.analytics_summary(cursor, department_id=_department_arg(access))
        return jsonify({"status": "success", **summary}), 200

    @library_route('/genres', ['POST'], manage=True)
    def library_genre_create(user_id, access):
        name, error = _genre_name()
        if error:
            return error
        with db._get_cursor() as cursor:
            genre, created = queries.create_genre(cursor, name, user_id)
        if created:
            logging.info('library: жанр #%s «%s» создан пользователем %s', genre['id'], genre['name'], user_id)
        # Уже был — отдаём его же: окно книги просто отметит существующий.
        return jsonify({"status": "success", "genre": genre, "created": created}), 201 if created else 200

    @library_route('/genres/<int:genre_id>', ['PATCH'], manage_all=True)
    def library_genre_rename(user_id, access, genre_id):
        name, error = _genre_name()
        if error:
            return error
        taken = jsonify({"error": "Жанр с таким названием уже есть", "code": "LIBRARY_GENRE_EXISTS"}), 409
        try:
            with db._get_cursor() as cursor:
                outcome, genre = queries.rename_genre(cursor, genre_id, name)
        except Exception as exc:  # noqa: BLE001
            # Два переименования в одно имя разом: проверку прошли оба, а
            # уникальный индекс пустил одно — второму тот же 409, не 500.
            if getattr(exc, 'pgcode', None) == '23505':
                return taken
            raise
        if outcome == queries.GENRE_NAME_TAKEN:
            return taken
        if outcome == queries.GENRE_NOT_FOUND:
            return jsonify({"error": "Жанр не найден", "code": "LIBRARY_GENRE_NOT_FOUND"}), 404
        logging.info('library: жанр #%s переименован в «%s» пользователем %s', genre_id, name, user_id)
        return jsonify({"status": "success", "genre": genre}), 200

    @library_route('/genres/<int:genre_id>', ['DELETE'], manage_all=True)
    def library_genre_delete(user_id, access, genre_id):
        with db._get_cursor() as cursor:
            deleted = queries.delete_genre(cursor, genre_id)
        if not deleted:
            return jsonify({"error": "Жанр не найден", "code": "LIBRARY_GENRE_NOT_FOUND"}), 404
        logging.info('library: жанр #%s удалён пользователем %s', genre_id, user_id)
        return jsonify({"status": "success"}), 200

    return bp
