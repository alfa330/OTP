# -*- coding: utf-8 -*-
"""Материалы задачи «Продакшн»: что принимаем, к какой версии файл относится, кто что может.

ЗАЧЕМ. Раздел «Продакшн» (задача #316) — рабочее место маркетинга поверх задач
типа «Продакшн» (#354): исполнитель загружает к задаче готовый ролик, баннер или
макет и отправляет задачу на проверку, постановщик смотрит и принимает или
возвращает. Файлы задачи (`task_attachments`) для этого не годятся: они идут
через наш сервер и ограничены 10 МБ, а ролик весит сотни мегабайт. Материал
поэтому едет из браузера прямо в облачное хранилище, кусками, с докачкой
после обрыва (storage.py), а здесь — правила, которые от хранилища не зависят.

ВЕРСИИ. Новых статусов задачи нет: «отправить на проверку» — это обычная сдача
(`completed`), «правки» — обычный возврат (`returned`). Номер версии поэтому не
хранится отдельным счётчиком, а выводится из истории статусов: сколько раз
задачу сдавали. Пока задача в работе, файлы ложатся в версию «сдач + 1»; после
сдачи эта версия становится той, что на проверке, и не меняется — следующая
загрузка после возврата откроет новую. Отдельный счётчик пришлось бы держать
в согласии с историей во всех дверях, которыми задачу сдают (раздел «Задачи»,
Telegram, CLI), — а история уже пишется ими всеми.

Модуль — лист: ни Flask, ни базы, ни облака. Тест проверяет его целиком.
"""

import base64
import re
import struct
import unicodedata

# ── Пределы ─────────────────────────────────────────────────────────────────
# 2 ГБ на файл — согласовано с владельцем 16.10.2026. Это ролик в полном
# качестве из монтажа; больше — уже исходники, им место не в портале.
MAX_MATERIAL_BYTES = 2 * 1024 * 1024 * 1024
# Обложка ролика — кадр, который снимает браузер. Хватит с большим запасом.
MAX_POSTER_BYTES = 2 * 1024 * 1024
# Сколько материалов в одной версии. Защита от мусора, а не правило процесса:
# у баннера с адаптациями под все каналы набирается два-три десятка файлов.
MAX_MATERIALS_PER_VERSION = 60
MAX_NAME_LENGTH = 255
# Кусок загрузки: кратно 256 КиБ (требование протокола GCS). 8 МиБ — компромисс
# между числом запросов (2 ГБ = 256 кусков) и тем, сколько придётся переслать
# заново после обрыва.
CHUNK_BYTES = 8 * 1024 * 1024
# Срок подписи ссылки на просмотр. Ролик смотрят долго и перематывают — каждый
# запрос перемотки идёт по той же ссылке, и истёкшая подпись выглядела бы как
# «плеер сломался». Столько же у роликов «Ивентов».
VIEW_SIGN_MINUTES = 240
DOWNLOAD_SIGN_MINUTES = 60

BLOB_PREFIX = 'Production/'

KIND_VIDEO = 'video'
KIND_IMAGE = 'image'
KIND_FILE = 'file'
KINDS = (KIND_VIDEO, KIND_IMAGE, KIND_FILE)

# Расширение → (вид, тип содержимого). Тип в хранилище ставим САМИ по
# расширению, а не верим браузеру: macOS отдаёт для .mov то video/quicktime,
# то пустую строку, Windows для .psd — application/octet-stream.
# Вид решает показ: ролик — плеер, картинка — превью, остальное — плитка файла.
TYPES = {
    # Ролики. mkv и avi браузер не играет — их можно только скачать, но
    # принимать их нужно: так приходят исходники с камер.
    'mp4': (KIND_VIDEO, 'video/mp4'),
    'm4v': (KIND_VIDEO, 'video/mp4'),
    'mov': (KIND_VIDEO, 'video/quicktime'),
    'webm': (KIND_VIDEO, 'video/webm'),
    'mkv': (KIND_VIDEO, 'video/x-matroska'),
    'avi': (KIND_VIDEO, 'video/x-msvideo'),
    # Картинки.
    'jpg': (KIND_IMAGE, 'image/jpeg'),
    'jpeg': (KIND_IMAGE, 'image/jpeg'),
    'png': (KIND_IMAGE, 'image/png'),
    'webp': (KIND_IMAGE, 'image/webp'),
    'gif': (KIND_IMAGE, 'image/gif'),
    'svg': (KIND_IMAGE, 'image/svg+xml'),
    'heic': (KIND_IMAGE, 'image/heic'),
    'heif': (KIND_IMAGE, 'image/heif'),
    'tif': (KIND_IMAGE, 'image/tiff'),
    'tiff': (KIND_IMAGE, 'image/tiff'),
    # Макеты и прочее, что сдаёт дизайнер.
    'pdf': (KIND_FILE, 'application/pdf'),
    'psd': (KIND_FILE, 'image/vnd.adobe.photoshop'),
    'ai': (KIND_FILE, 'application/postscript'),
    'eps': (KIND_FILE, 'application/postscript'),
    'zip': (KIND_FILE, 'application/zip'),
    'rar': (KIND_FILE, 'application/vnd.rar'),
    '7z': (KIND_FILE, 'application/x-7z-compressed'),
    'mp3': (KIND_FILE, 'audio/mpeg'),
    'wav': (KIND_FILE, 'audio/wav'),
    'm4a': (KIND_FILE, 'audio/mp4'),
    'aac': (KIND_FILE, 'audio/aac'),
}

# Что браузер покажет сам. Остальное — плитка с «Скачать». HEIC и TIFF Chrome
# не рисует, SVG рисуем только тегом <img> (скрипты внутри него там не работают).
PREVIEW_IMAGE_TYPES = frozenset({'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/svg+xml'})
PLAYABLE_VIDEO_TYPES = frozenset({'video/mp4', 'video/quicktime', 'video/webm'})

# Статусы задачи, в которых исполнитель работает над версией.
WORKING_STATUSES = frozenset({'in_progress', 'returned'})
SUBMITTED_STATUSES = frozenset({'completed', 'accepted'})

UNSUPPORTED_TYPE = 'PRODUCTION_UNSUPPORTED_TYPE'
LIBRARY_ONLY_VIDEO = 'PRODUCTION_LIBRARY_ONLY_VIDEO'
LIBRARY_FOLDER = 'library'
TOO_LARGE = 'PRODUCTION_FILE_TOO_LARGE'
EMPTY_FILE = 'PRODUCTION_FILE_EMPTY'
BAD_NAME = 'PRODUCTION_BAD_NAME'


def extension_of(name):
    """Расширение в нижнем регистре без точки; '' — если его нет."""
    base = str(name or '').rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    if '.' not in base:
        return ''
    return base.rsplit('.', 1)[-1].strip().lower()


def clean_name(name):
    """Имя файла для показа. Кириллицу НЕ теряем.

    werkzeug.secure_filename здесь не годится: он выбрасывает всё не-ASCII, и
    «Промо-ролик.mp4» превратился бы в «mp4» (та же ловушка описана у файлов
    задач). Путь в хранилище имя не задаёт — там UUID, — поэтому здесь
    достаточно убрать каталоги, управляющие символы и лишнюю длину.
    """
    text = str(name or '').replace('\\', '/').rsplit('/', 1)[-1]
    text = unicodedata.normalize('NFC', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch)[0] != 'C')
    text = re.sub(r'\s+', ' ', text).strip().strip('.')
    if not text:
        raise ValueError(BAD_NAME)
    if len(text) > MAX_NAME_LENGTH:
        ext = extension_of(text)
        keep = MAX_NAME_LENGTH - (len(ext) + 1 if ext else 0)
        text = (text[:keep].rstrip() + ('.' + ext if ext else ''))[:MAX_NAME_LENGTH]
    return text


def classify(name, size):
    """(имя, расширение, вид, тип содержимого) или ValueError с кодом."""
    clean = clean_name(name)
    ext = extension_of(clean)
    if ext not in TYPES:
        raise ValueError(UNSUPPORTED_TYPE)
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise ValueError(EMPTY_FILE)
    if size <= 0:
        raise ValueError(EMPTY_FILE)
    if size > MAX_MATERIAL_BYTES:
        raise ValueError(TOO_LARGE)
    kind, content_type = TYPES[ext]
    return clean, ext, kind, content_type


def classify_library(name, size):
    """Для вкладки «Ролики»: то же, что classify, но только ролики — о них и просили."""
    found = classify(name, size)
    if found[2] != KIND_VIDEO:
        raise ValueError(LIBRARY_ONLY_VIDEO)
    return found


def video_extensions():
    return sorted(ext for ext, (kind, _ctype) in TYPES.items() if kind == KIND_VIDEO)


def accepted_extensions():
    """Для атрибута accept у выбора файлов и для текста ошибки."""
    return sorted(TYPES)


def blob_path(task_id, material_id, ext, prefix=BLOB_PREFIX):
    """Путь объекта: папка задачи и UUID материала. Имя файла в путь не идёт —
    оно бывает кириллическим, с пробелами и повторяется от версии к версии."""
    prefix = str(prefix or BLOB_PREFIX)
    if not prefix.endswith('/'):
        prefix += '/'
    return '%s%d/%s.%s' % (prefix, int(task_id), str(material_id), ext)


def library_blob_path(material_id, ext, prefix=BLOB_PREFIX):
    """Ролик без задачи — в своей папке, рядом с папками задач."""
    prefix = str(prefix or BLOB_PREFIX)
    if not prefix.endswith('/'):
        prefix += '/'
    return '%s%s/%s.%s' % (prefix, LIBRARY_FOLDER, str(material_id), ext)


def is_library(material):
    return material.get('task_id') is None


def can_delete_library(material, *, user_id, is_super_admin, heads_marketing):
    """Ролик «Роликов» удаляют загрузивший, глава маркетинга и супер-админ."""
    return bool(is_super_admin or heads_marketing or material.get('uploaded_by') == user_id)


def poster_path(material_blob_path):
    return '%s.poster.jpg' % material_blob_path


def can_preview(kind, content_type):
    if kind == KIND_IMAGE:
        return content_type in PREVIEW_IMAGE_TYPES
    if kind == KIND_VIDEO:
        return content_type in PLAYABLE_VIDEO_TYPES
    return False


# ── Версии ──────────────────────────────────────────────────────────────────

def working_version(status, completed_count):
    """Версия, в которую сейчас ложатся новые файлы, или None.

    Новые файлы принимаются только пока задача в работе: на проверке версия
    уже показана постановщику, и подменять её под ним нельзя.
    """
    if status not in WORKING_STATUSES:
        return None
    return max(0, int(completed_count or 0)) + 1


def rounds_from_history(rows):
    """История статусов → раунды сдачи: что отправили, что ответили.

    rows — (status_code, changed_at, changed_by, changed_by_name, comment) по
    времени. Раунд открывает каждая сдача (`completed`); закрывает его первый
    же ответ постановщика после неё — возврат или приёмка. «Переоткрыть» после
    приёмки раунд не трогает: он уже принят, а следующая сдача откроет новый.
    """
    rounds = []
    for status, changed_at, changed_by, changed_by_name, comment in rows or ():
        if status == 'completed':
            rounds.append({
                'version': len(rounds) + 1,
                'submitted_at': changed_at,
                'submitted_by': changed_by,
                'submitted_by_name': changed_by_name,
                'comment': comment or '',
                'outcome': None,
                'outcome_at': None,
                'outcome_by': None,
                'outcome_by_name': None,
                'outcome_comment': '',
            })
        elif status in ('returned', 'accepted') and rounds and rounds[-1]['outcome'] is None:
            rounds[-1].update({
                'outcome': status,
                'outcome_at': changed_at,
                'outcome_by': changed_by,
                'outcome_by_name': changed_by_name,
                'outcome_comment': comment or '',
            })
    return rounds


# ── Права ───────────────────────────────────────────────────────────────────

def permissions(*, status, is_assignee, can_review, is_super_admin):
    """Что человеку показать в карточке. Обязательную границу переходов держит
    сам маршрут статусов задачи — здесь только «какие кнопки рисовать»."""
    return {
        'can_take': bool(is_assignee and status == 'assigned'),
        'can_upload': bool(is_assignee and status in WORKING_STATUSES),
        'can_submit': bool(is_assignee and status in WORKING_STATUSES),
        'can_review': bool(can_review and status == 'completed'),
        'can_delete_any': bool(is_super_admin),
    }


def is_draft(material, completed_count):
    """Файл новее последней сдачи: он ещё никому не показан."""
    return int(material.get('version') or 0) > max(0, int(completed_count or 0))


def can_delete(material, *, status, completed_count, user_id, is_assignee, is_super_admin):
    """Удалить материал можно, пока его версия не ушла на проверку.

    Отправленная версия — это то, что видел постановщик, и что принято; её не
    правят задним числом. Свой черновик (файл догрузился уже после сдачи — у
    соисполнителя или у самого себя) загрузивший убирает при любом статусе:
    иначе у принятой задачи он висел бы до супер-админа. Супер-админ —
    исключение для всего: случайно загруженный чужой файл должен уметь убрать
    хотя бы кто-то.
    """
    if is_super_admin:
        return True
    if is_draft(material, completed_count) and material.get('uploaded_by') == user_id:
        return True
    if not is_assignee:
        return False
    version = working_version(status, completed_count)
    if version is None:
        return False
    return int(material.get('version') or 0) == version


# ── Проверка того, что доехало ──────────────────────────────────────────────

def crc32c_from_client(value):
    """CRC32C, посчитанный браузером, → число. Принимаем десятичное и hex."""
    if value is None or value == '':
        return None
    try:
        if isinstance(value, str) and value.lower().startswith('0x'):
            number = int(value, 16)
        elif isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{8}', value):
            number = int(value, 16)
        else:
            number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 0xFFFFFFFF:
        return None
    return number


def crc32c_from_gcs(value):
    """blob.crc32c хранилища (base64 четырёх байт, big-endian) → число."""
    if not value:
        return None
    try:
        raw = base64.b64decode(value)
    except (TypeError, ValueError):
        return None
    if len(raw) != 4:
        return None
    return struct.unpack('>I', raw)[0]


# Подписи начала файла: защита от «переименовал .html в .mp4». Отдаём файлы мы
# с объявленным типом, но доверять имени, когда содержимое уже под рукой, —
# лишний риск. Где формат подписи не имеет (SVG, MP3 без ID3), смотрим мягче.
def _is_iso_bmff(head):
    return len(head) >= 12 and head[4:8] in (b'ftyp', b'moov', b'mdat', b'wide', b'free', b'skip', b'pnot')


def _is_riff(head, form):
    return len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == form


_SIGNATURES = {
    'mp4': _is_iso_bmff,
    'm4v': _is_iso_bmff,
    'mov': _is_iso_bmff,
    'm4a': _is_iso_bmff,
    'heic': _is_iso_bmff,
    'heif': _is_iso_bmff,
    'webm': lambda h: h[:4] == b'\x1a\x45\xdf\xa3',
    'mkv': lambda h: h[:4] == b'\x1a\x45\xdf\xa3',
    'avi': lambda h: _is_riff(h, b'AVI '),
    'wav': lambda h: _is_riff(h, b'WAVE'),
    'webp': lambda h: _is_riff(h, b'WEBP'),
    'jpg': lambda h: h[:3] == b'\xff\xd8\xff',
    'jpeg': lambda h: h[:3] == b'\xff\xd8\xff',
    'png': lambda h: h[:8] == b'\x89PNG\r\n\x1a\n',
    'gif': lambda h: h[:4] == b'GIF8',
    'tif': lambda h: h[:4] in (b'II*\x00', b'MM\x00*'),
    'tiff': lambda h: h[:4] in (b'II*\x00', b'MM\x00*'),
    'pdf': lambda h: h[:5] == b'%PDF-',
    'psd': lambda h: h[:4] == b'8BPS',
    # Illustrator сохраняет PDF-совместимый файл; старый — PostScript.
    'ai': lambda h: h[:5] == b'%PDF-' or h[:4] == b'%!PS',
    'eps': lambda h: h[:4] == b'%!PS' or h[:4] == b'\xc5\xd0\xd3\xc6',
    'zip': lambda h: h[:4] in (b'PK\x03\x04', b'PK\x05\x06'),
    'rar': lambda h: h[:7] == b'Rar!\x1a\x07\x00' or h[:8] == b'Rar!\x1a\x07\x01\x00',
    '7z': lambda h: h[:6] == b"7z\xbc\xaf'\x1c",
    'mp3': lambda h: h[:3] == b'ID3' or (len(h) >= 2 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0),
    'aac': lambda h: h[:3] == b'ID3' or (len(h) >= 2 and h[0] == 0xFF and (h[1] & 0xF6) == 0xF0),
    'svg': lambda h: b'<svg' in h[:4096].lower(),
}


def content_matches(ext, head):
    """Похоже ли начало файла на заявленный формат."""
    check = _SIGNATURES.get(ext)
    if check is None:
        return False
    try:
        return bool(check(bytes(head or b'')))
    except Exception:  # noqa: BLE001 — кривые байты = не совпало
        return False


def error_message(code):
    """Текст для человека по коду ошибки."""
    return {
        LIBRARY_ONLY_VIDEO: 'Во вкладку «Ролики» загружаются только ролики: MP4, MOV, WebM, MKV или AVI',
        UNSUPPORTED_TYPE: 'Такой файл не принимается. Подходят ролики (MP4, MOV, WebM), '
                          'картинки (JPG, PNG, WebP, GIF, SVG, HEIC, TIFF), PDF, PSD, AI, EPS, '
                          'архивы ZIP, RAR, 7z и звук (MP3, WAV, M4A, AAC).',
        TOO_LARGE: 'Файл больше 2 ГБ',
        EMPTY_FILE: 'Файл пустой',
        BAD_NAME: 'У файла нет имени',
    }.get(code, code)
