"""Обои «Чатов ОП» — своя картинка на фоне переписки и её вид (кнопка
«Кастомизация» раздела, просьба владельца 23.10.2026).

Тему окна по-прежнему помнит браузер (src/components/wazzup/chatThemes.js), а
обои — сервер: картинку загружают один раз и видят на любом компьютере, и только
так админ может посмотреть, у кого какие стоят.

Кто что может:
  * свои обои — каждый, кому раздел открыт (любой режим _wazzup_chat_access);
  * учёткам STANDARD_ONLY обоев нет: загрузка закрыта, сохранённые не отдаются;
  * чужие обои и их список — только админам (роль admin и выше).

Картинку пересжимаем здесь, даже если браузер уже ужал её сам: в хранилище не
попадают метаданные снимка (место съёмки в EXIF), и формат всегда один. Работа с
хранилищем — вне транзакций базы, как у вложений (wazzup/uploads.py).
"""
import io
import json
import logging
import math
import threading
import uuid
from datetime import datetime

from flask import Blueprint, Response, g, jsonify, request
from PIL import Image, ImageOps
from werkzeug.exceptions import RequestEntityTooLarge

from .attachments import AttachmentError, _rate

# Учётки, которым раздел показывает только стандартный вид: одна стандартная
# тема и без обоев. Зеркало STANDARD_ONLY в src/components/wazzup/chatThemes.js —
# равенство сторожит тест.
STANDARD_ONLY = frozenset()

MAX_UPLOAD = 12 * 1024 * 1024
MAX_BODY = MAX_UPLOAD + 64 * 1024
MAX_PIXELS = 40_000_000
# Длинная сторона хранимой картинки: окно переписки шире не бывает даже на
# мониторе 2560, а миниатюра — для списка у админа.
MAX_SIDE = 2560
THUMB_SIDE = 640
PREFIX = 'wazzup/appearance/'
LIST_LIMIT = 500

# Вид обоев: (наименьшее, наибольшее, по умолчанию). Яркость — затемнение
# (минус) или высветление (плюс) в процентах, размытие — пиксели, насыщенность —
# проценты, x и y — какая часть картинки видна (background-position).
SETTINGS = {
    'brightness': (-60, 60, 0),
    'blur': (0, 20, 0),
    'saturation': (0, 200, 100),
    'x': (0, 100, 50),
    'y': (0, 100, 50),
}
DEFAULT_SETTINGS = {key: spec[2] for key, spec in SETTINGS.items()}
_POSITION = ('x', 'y')
# Пересжатие занимает процессор на полсекунды-секунду: по одной картинке за раз.
_slots = threading.BoundedSemaphore(1)
_COLUMNS = 'wallpaper_id, width, height, settings, updated_at'


class AppearanceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def _number(value, low, high):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and low <= value <= high)


def clean_settings(value):
    """Настройки из запроса: только известные поля, только в границах."""
    if value is None:
        return dict(DEFAULT_SETTINGS)
    if not isinstance(value, dict):
        raise AppearanceError('Некорректные настройки обоев')
    result = {}
    for key, (low, high, default) in SETTINGS.items():
        item = value.get(key, default)
        if not _number(item, low, high):
            raise AppearanceError('Некорректные настройки обоев')
        result[key] = round(float(item), 1) if key in _POSITION else int(round(item))
    return result


def stored_settings(raw):
    """Настройки из базы: чего нет (поле добавили позже) — по умолчанию."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    result = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        for key, (low, high, _default) in SETTINGS.items():
            if _number(raw.get(key), low, high):
                result[key] = raw[key]
    return result


def _encode(picture, side, quality):
    copy = picture.copy()
    copy.thumbnail((side, side), Image.LANCZOS)
    out = io.BytesIO()
    copy.save(out, 'WEBP', quality=quality, method=4)
    return out.getvalue(), copy.size


def prepare_wallpaper(data):
    """Байты файла -> {'full', 'thumb', 'width', 'height'} в WebP без метаданных."""
    if not data:
        raise AppearanceError('Файл пустой')
    if len(data) > MAX_UPLOAD:
        raise AppearanceError('Картинка больше 12 МБ', 413)
    unreadable = AppearanceError('Не удалось открыть картинку. Подойдут JPG, PNG или WebP.', 415)
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ('JPEG', 'PNG', 'WEBP'):
                raise unreadable
            if probe.width * probe.height > MAX_PIXELS:
                raise AppearanceError('Картинка слишком большая: до 40 мегапикселей', 413)
            probe.verify()
        with Image.open(io.BytesIO(data)) as source:
            if source.format == 'JPEG':
                source.draft('RGB', (MAX_SIDE, MAX_SIDE))
            source.load()
            # Снимок с телефона лежит боком, а поворот записан в EXIF.
            picture = ImageOps.exif_transpose(source)
            alpha = (picture.mode in ('RGBA', 'LA', 'PA')
                     or (picture.mode == 'P' and 'transparency' in picture.info))
            picture = picture.convert('RGBA' if alpha else 'RGB')
            full, size = _encode(picture, MAX_SIDE, 82)
            thumb, _ = _encode(picture, THUMB_SIDE, 78)
    except AppearanceError:
        raise
    except Exception:
        # Битый файл падает чем угодно: UnidentifiedImageError, OSError у
        # обрезанного, struct.error у кривого EXIF, DecompressionBombError.
        raise unreadable from None
    return {'full': full, 'thumb': thumb, 'width': size[0], 'height': size[1]}


def _json(status=200, **values):
    response = jsonify(**values)
    response.status_code = status
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def wallpaper_item(row):
    wallpaper_id, width, height, _settings, updated_at = row
    return {'id': str(wallpaper_id), 'width': width, 'height': height, 'updatedAt': _iso(updated_at)}


def _bucket_name(gcs):
    bucket = gcs['bucket_name']() if gcs and callable(gcs.get('bucket_name')) else None
    if not bucket or not callable(gcs.get('client')):
        raise AppearanceError('Хранилище обоев не настроено', 503)
    return bucket


def _drop(gcs, bucket, paths):
    """Старые файлы — после коммита и без гарантий: не удалились — останутся сиротами."""
    for path in paths:
        if not path or not path.startswith(PREFIX):
            continue
        try:
            gcs['client']().bucket(bucket).blob(path).delete(timeout=10, retry=None)
        except Exception as error:
            if getattr(error, 'code', None) != 404:
                logging.warning('Chat wallpaper cleanup failed error_type=%s', type(error).__name__)


def build_appearance_blueprint(*, db, require_api_key, preflight, chat_access, is_admin, gcs=None):
    """chat_access() -> режим «Чатов ОП» человека запроса (_wazzup_chat_access
    монолита); is_admin(user_id) -> роль admin и выше."""
    bp = Blueprint('wazzup_appearance', __name__, url_prefix='/api/wazzup/appearance')

    def person():
        state = chat_access() or {}
        user_id = state.get('user_id')
        if user_id is None or state.get('mode') is None:
            return None, _json(403, error='Раздел «Чаты ОП» вам не открыт')
        return int(user_id), None

    def state_of(user_id):
        if user_id in STANDARD_ONLY:
            return {'wallpaper': None, 'settings': dict(DEFAULT_SETTINGS), 'mayUpload': False}
        with db._get_cursor() as cursor:
            cursor.execute('SELECT ' + _COLUMNS + ' FROM wazzup_chat_wallpapers WHERE user_id=%s', (user_id,))
            row = cursor.fetchone()
        return {'wallpaper': wallpaper_item(row) if row else None,
                'settings': stored_settings(row[3]) if row else dict(DEFAULT_SETTINGS),
                'mayUpload': True}

    @bp.route('', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    def appearance():
        if request.method == 'OPTIONS':
            return preflight()
        user_id, error = person()
        if error:
            return error
        if request.method == 'GET':
            return _json(200, **state_of(user_id))
        # PUT — вид тех обоев, что стоят сейчас: wallpaperId не совпал — картинку
        # уже сменили в другой вкладке, и чужие настройки на неё не ложатся.
        if user_id in STANDARD_ONLY:
            return _json(403, error='Обои недоступны')
        if request.content_length and request.content_length > 4096:
            return _json(413, error='Слишком большой запрос')
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _json(400, error='Некорректный запрос')
        try:
            settings = clean_settings(body.get('settings'))
            wallpaper_id = str(uuid.UUID(str(body.get('wallpaperId'))))
        except AppearanceError as problem:
            return _json(problem.status, error=problem.message)
        except (ValueError, TypeError, AttributeError):
            return _json(400, error='Некорректные обои')
        with db._get_cursor() as cursor:
            cursor.execute('UPDATE wazzup_chat_wallpapers SET settings=%s::jsonb, updated_at=now() '
                           'WHERE user_id=%s AND wallpaper_id=%s RETURNING 1',
                           (json.dumps(settings), user_id, wallpaper_id))
            updated = cursor.fetchone()
        if not updated:
            return _json(409, error='Обои уже сменились. Откройте кастомизацию ещё раз.', **state_of(user_id))
        return _json(200, **state_of(user_id))

    @bp.route('/wallpaper', methods=['POST', 'DELETE', 'OPTIONS'])
    @require_api_key
    def wallpaper():
        if request.method == 'OPTIONS':
            return preflight()
        user_id, error = person()
        if error:
            return error
        if request.method == 'DELETE':
            with db._get_cursor() as cursor:
                cursor.execute('DELETE FROM wazzup_chat_wallpapers WHERE user_id=%s '
                               'RETURNING bucket, blob_path, thumb_path', (user_id,))
                old = cursor.fetchone()
            if old:
                _drop(gcs, old[0], old[1:])
            return _json(200, **state_of(user_id))
        if user_id in STANDARD_ONLY:
            return _json(403, error='Обои недоступны')
        acquired = False
        incoming = None
        try:
            if request.content_length is None or request.content_length > MAX_BODY:
                raise AppearanceError('Картинка больше 12 МБ', 413)
            if request.mimetype != 'multipart/form-data':
                raise AppearanceError('Передайте одну картинку')
            # Свой ограниченный разборщик, как у вложений: общий предел
            # приложения не трогаем.
            parser = request.make_form_data_parser()
            parser.max_content_length = MAX_BODY
            parser.max_form_memory_size = 64 * 1024
            parser.max_form_parts = 4
            _, form, incoming = parser.parse(request.stream, request.mimetype,
                                             request.content_length, request.mimetype_params)
            files = list(incoming.items(multi=True))
            if len(files) != 1 or files[0][0] != 'file':
                raise AppearanceError('Передайте одну картинку')
            try:
                settings = clean_settings(json.loads(form.get('settings') or 'null'))
            except ValueError:
                raise AppearanceError('Некорректные настройки обоев') from None
            _rate(user_id, 'chat-wallpaper', 6)
            acquired = _slots.acquire(blocking=False)
            if not acquired:
                raise AppearanceError('Обои уже сохраняются. Повторите через несколько секунд.', 429)
            prepared = prepare_wallpaper(files[0][1].read(MAX_UPLOAD + 1))
            bucket = _bucket_name(gcs)
            identifier = str(uuid.uuid4())
            base = '%s%d/%s' % (PREFIX, user_id, identifier)
            paths = (base + '.webp', base + '.thumb.webp')
            stored = []
            try:
                for path, data in zip(paths, (prepared['full'], prepared['thumb'])):
                    gcs['client']().bucket(bucket).blob(path).upload_from_string(
                        data, content_type='image/webp', timeout=30, if_generation_match=0)
                    stored.append(path)
            except Exception as problem:
                logging.warning('Chat wallpaper upload failed error_type=%s', type(problem).__name__)
                _drop(gcs, bucket, stored)
                raise AppearanceError('Не удалось сохранить обои. Попробуйте ещё раз.', 503) from None
            # Коммит может пройти, даже если ответ базы потерялся: после этой
            # точки новые файлы не удаляем — лучше сирота, чем битая строка.
            with db._get_cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext('wazzup-chat-wallpaper'), %s)",
                               (user_id,))
                cursor.execute('SELECT bucket, blob_path, thumb_path FROM wazzup_chat_wallpapers '
                               'WHERE user_id=%s', (user_id,))
                old = cursor.fetchone()
                cursor.execute("""
                    INSERT INTO wazzup_chat_wallpapers
                        (user_id, wallpaper_id, bucket, blob_path, thumb_path,
                         width, height, file_size, settings, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                    ON CONFLICT (user_id) DO UPDATE SET
                        wallpaper_id = EXCLUDED.wallpaper_id, bucket = EXCLUDED.bucket,
                        blob_path = EXCLUDED.blob_path, thumb_path = EXCLUDED.thumb_path,
                        width = EXCLUDED.width, height = EXCLUDED.height,
                        file_size = EXCLUDED.file_size, settings = EXCLUDED.settings,
                        updated_at = now()
                """, (user_id, identifier, bucket, paths[0], paths[1], prepared['width'],
                      prepared['height'], len(prepared['full']), json.dumps(settings)))
            if old:
                _drop(gcs, old[0], old[1:])
            return _json(201, **state_of(user_id))
        except AppearanceError as problem:
            return _json(problem.status, error=problem.message)
        except AttachmentError as problem:
            return _json(problem.status, error=problem.message)
        except RequestEntityTooLarge:
            return _json(413, error='Картинка больше 12 МБ')
        except Exception:
            logging.exception('Chat wallpaper save failed')
            return _json(503, error='Не удалось сохранить обои. Попробуйте ещё раз.')
        finally:
            if acquired:
                _slots.release()
            if incoming is not None:
                for _, file in incoming.items(multi=True):
                    file.close()

    @bp.route('/wallpaper/<wallpaper_id>', methods=['GET', 'OPTIONS'])
    @require_api_key
    def wallpaper_file(wallpaper_id):
        """Сама картинка: своя — владельцу, чужая — админу. Остальным — как
        будто её нет. Адрес у каждой картинки свой и неизменный, поэтому
        браузер держит её в своём кэше, а не качает при каждом входе."""
        if request.method == 'OPTIONS':
            return preflight()
        user_id, error = person()
        if error:
            return error
        try:
            wallpaper_id = str(uuid.UUID(wallpaper_id))
        except (ValueError, TypeError, AttributeError):
            return _json(404, error='Обои не найдены')
        with db._get_cursor() as cursor:
            cursor.execute('SELECT user_id, bucket, blob_path, thumb_path FROM wazzup_chat_wallpapers '
                           'WHERE wallpaper_id=%s', (wallpaper_id,))
            row = cursor.fetchone()
        if (not row or row[0] in STANDARD_ONLY
                or (row[0] != user_id and not is_admin(user_id))):
            return _json(404, error='Обои не найдены')
        path = row[3] if request.args.get('size') == 'thumb' else row[2]
        try:
            data = gcs['client']().bucket(row[1]).blob(path).download_as_bytes(timeout=20)
        except Exception as problem:
            gone = getattr(problem, 'code', None) == 404
            logging.warning('Chat wallpaper fetch failed status=%d error_type=%s',
                            404 if gone else 503, type(problem).__name__)
            return _json(404 if gone else 503, error='Обои не найдены' if gone else 'Хранилище недоступно')
        # Монолит ставит no-store на каждый ответ /api/ — здесь кэш нужен.
        g.allow_public_cache = True
        response = Response(data, content_type='image/webp')
        response.headers['Cache-Control'] = 'private, max-age=31536000, immutable'
        return response

    @bp.route('/wallpapers', methods=['GET', 'OPTIONS'])
    @require_api_key
    def wallpapers():
        """У кого какие обои — для админа: свежие сверху."""
        if request.method == 'OPTIONS':
            return preflight()
        user_id, error = person()
        if error:
            return error
        if not is_admin(user_id):
            return _json(403, error='Список обоев виден только админам')
        with db._get_cursor() as cursor:
            cursor.execute("""
                SELECT w.user_id, u.name, d.name, w.wallpaper_id, w.width, w.height,
                       w.settings, w.updated_at
                  FROM wazzup_chat_wallpapers w
                  LEFT JOIN users u ON u.id = w.user_id
                  LEFT JOIN departments d ON d.id = u.department_id
                 ORDER BY w.updated_at DESC, w.user_id
                 LIMIT %s
            """, (LIST_LIMIT,))
            rows = cursor.fetchall()
        items = [{'userId': row[0], 'name': row[1] or '', 'department': row[2] or '',
                  'wallpaper': wallpaper_item(row[3:6] + (None, row[7])),
                  'settings': stored_settings(row[6])}
                 for row in rows if row[0] not in STANDARD_ONLY]
        return _json(200, items=items)

    return bp
