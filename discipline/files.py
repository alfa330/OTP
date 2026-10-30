# -*- coding: utf-8 -*-
"""Файлы раздела «Дисциплина»: доказательства, объяснительные, акты, шаблон акта.

Хранение — GCS, как у вложений задач и «Оплаты счетов»; клиент и имя бакета
приходят фабрикой (`gcs={'bucket_name': fn, 'client': fn}`).

Что требует ТЗ (п. 8.1, 9, 13) и как это сделано:

* **Прямого доступа по ссылке нет** — только своя ручка скачивания с обычной
  авторизацией. Подписанные ссылки (как у фото задач) открывались бы без входа
  и мимо журнала просмотров, а ТЗ п. 14 требует знать, кто открывал файлы.
* **Файл хранится как есть, с хешем SHA-256.** Объяснительная и акт — документы
  с подписью: пережатие в WebP (как у фото задач) меняло бы подлинник.
* **Типы — белый список ТЗ по виду файла**, сверка расширения и сигнатуры.
  Антивируса в инфраструктуре портала нет — отступление от п. 13 названо в
  отчёте; опасные для браузера типы не принимаются вовсе, а отдаётся всё
  вложением с nosniff.
* **Ничего не удаляется**: новая версия того же вида закрывает прежнюю
  (superseded_at), ошибочный файл помечается недействительным с причиной.
* **Имя не выдаёт один тип за другой**: невидимые и «переворачивающие» символы
  вычищаются и при загрузке, и при показе уже загруженного (clean_name).
* **DOCX без того, что Word сделает сам при открытии** (check_docx): внешний
  шаблон или объект по сети, макросы, ActiveX — такой документ не принимается.
  Шаблон акта, кроме того, ограничен по объёму распакованного XML: сервер
  разбирает его при каждом черновике.
"""

import hashlib
import logging
import re
import unicodedata
import uuid
import zipfile
import zlib
from datetime import datetime
from io import BytesIO
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from lxml import etree

from . import catalog

ALMATY = ZoneInfo('Asia/Almaty')
PREFIX = 'discipline'

_CONTENT_TYPES = {
    'pdf': 'application/pdf',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'heic': 'image/heic',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'mp4': 'video/mp4',
}


# DOC — тоже строго: RTF, HTML или XML с расширением .doc Word открывает по
# содержимому, и проверка DOCX обходилась бы простым «Сохранить как .doc».
_STRICT_SIGNATURE = ('pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'mp4')


class FileError(Exception):
    def __init__(self, message, code='DISCIPLINE_FILE_INVALID', status=400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


# Буквы, которые рисуются пустотой: заполнители хангыля и пустая ячейка Брайля.
_BLANK_LETTERS = frozenset('ᅟᅠㅤﾠ⠀')
NAME_MAX = 200


def clean_name(text):
    """Имя файла для показа — таким, каким его прочтёт человек.

    Убраны управляющие, форматные (U+202E разворачивает строку: «акт‮fdp.doc»
    на экране читается как «актcod.pdf»; изоляты и символы нулевой ширины),
    частные, неназначенные и «пустые» символы; любой пробел (неразрывный,
    узкий, табуляция) — обычный, подряд — один: длинная полоса пробелов
    уводила настоящее расширение за край строки. Имя приводится к NFC — macOS
    присылает «й» двумя символами."""
    out = []
    for char in unicodedata.normalize('NFC', str(text or '')):
        if char.isspace():
            out.append(' ')
        elif char not in _BLANK_LETTERS and unicodedata.category(char) not in ('Cc', 'Cf', 'Cs', 'Co', 'Cn'):
            out.append(char)
    return re.sub(' {2,}', ' ', ''.join(out)).strip()


def safe_name(filename, fallback='file'):
    """Имя для человека и для пути: без путей, управляющих и невидимых
    символов, кириллица СОХРАНЯЕТСЯ (secure_filename её выбрасывает: «акт.pdf»
    → «pdf»). Длинное имя режется по основе — расширение остаётся на месте."""
    base = str(filename or '').replace('\\', '/').split('/')[-1]
    base = clean_name(re.sub(r'["<>|*?:]', '', base)).strip('. ') or fallback
    if len(base) <= NAME_MAX:
        return base
    stem, dot, ext = base.rpartition('.')
    if dot and stem and 0 < len(ext) <= 10:
        return stem[:NAME_MAX - len(ext) - 1].rstrip('. ') + '.' + ext
    return base[:NAME_MAX]


def extension(filename):
    name = str(filename or '')
    return name.rsplit('.', 1)[-1].lower() if '.' in name else ''


def _sniff(data):
    """Расширение по сигнатуре: файл с телефона нередко приходит без расширения."""
    head = bytes(data[:16] or b'')
    if head.startswith(b'%PDF'):
        return 'pdf'
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if head.startswith(b'\x89PNG'):
        return 'png'
    if head[4:8] == b'ftyp':
        brand = head[8:12]
        if brand in (b'heic', b'heix', b'mif1', b'msf1', b'heim', b'heis'):
            return 'heic'
        return 'mp4'
    if head.startswith(b'\xd0\xcf\x11\xe0'):
        return 'doc'
    if head.startswith(b'PK\x03\x04'):
        return 'docx'
    return ''


def check(kind, *, filename, data):
    """Проверка файла вида kind. Возвращает (имя, расширение, content_type)."""
    spec = catalog.FILE_KINDS.get(kind)
    if not spec:
        raise FileError('Неизвестный вид файла')
    name = safe_name(filename)
    ext = extension(name)
    sniffed = _sniff(data)
    if ext not in spec['ext']:
        if sniffed and sniffed in spec['ext']:
            ext = sniffed
            name = '%s.%s' % (name, ext) if not name.lower().endswith('.' + ext) else name
        else:
            allowed = ', '.join(e.upper() for e in spec['ext'] if e != 'jpeg')
            raise FileError('«%s»: для вида «%s» подходят %s' % (name, spec['label'].lower(), allowed))
    # Сигнатура не должна спорить с расширением: «акт.pdf», внутри которого
    # исполняемый файл, — не акт. У PDF, JPG, PNG, DOC, DOCX и MP4 сигнатура
    # есть всегда и обязана совпасть. HEIC бывает с редкими заголовками — ему
    # незнакомая сигнатура прощается, но не исполняемая.
    head = bytes((data or b'')[:4])
    if head.startswith(b'MZ') or head.startswith(b'\x7fELF') or head.startswith(b'#!'):
        raise FileError('«%s»: такой файл не принимается' % name)
    family = {'jpeg': 'jpg'}
    expected = family.get(ext, ext)
    if ext in _STRICT_SIGNATURE:
        if family.get(sniffed, sniffed) != expected:
            if ext == 'doc':
                raise FileError('«%s»: это не документ Word 97–2003 — сохраните его как DOCX или PDF' % name)
            raise FileError('«%s»: содержимое не совпадает с расширением' % name)
    elif sniffed and sniffed != expected and not (ext == 'heic' and sniffed == 'mp4'):
        raise FileError('«%s»: содержимое не совпадает с расширением' % name)
    size = len(data or b'')
    if size <= 0:
        raise FileError('Файл «%s» пустой' % name)
    limit = spec.get('video_max_bytes') if ext == 'mp4' else spec['max_bytes']
    if size > limit:
        raise FileError('Файл «%s» больше %d МБ' % (name, limit // catalog.MB), code='DISCIPLINE_FILE_TOO_LARGE')
    if ext == 'docx':
        check_docx(data, name=name, template=kind == 'act_template')
    return name, ext, _CONTENT_TYPES.get(ext, 'application/octet-stream')


# ── DOCX: что Word сделает сам, когда документ откроют ──────────────────────
# DOCX — ZIP с XML. Опасен не сам файл, а то, что Word по нему подгрузит или
# запустит у руководителя, который его откроет: шаблон с макросами по ссылке в
# сети (внешний шаблон — известный приём), внешние объекты и рамки, макросы и
# ActiveX. Антивируса нет (решение владельца 23.10.2026), поэтому такой
# документ не принимается вовсе — файл хранится как есть (подлинник), и
# вырезать из него ссылку мы не вправе. Гиперссылки и шаблон на диске самого
# автора (file:///C:/…, так пишет Word у документа из своего шаблона)
# безвредны и встречаются в обычных документах — их пропускаем.

_REL_NS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
_CT_NS = '{http://schemas.openxmlformats.org/package/2006/content-types}'
_WORD_MAIN = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
DOCX_MAX_ENTRIES = 1000
DOCX_MAX_UNPACKED = 100 * catalog.MB   # всё содержимое в распакованном виде
DOCX_MAX_SERVICE_PART = 1 * catalog.MB  # .rels и [Content_Types].xml — служебные, крошечные
# Шаблон акта python-docx разбирает целиком при каждом черновике: архив на
# 99 КБ с XML на 22 МБ съедал 412 МБ памяти (замер 30.10.2026). Настоящий
# шаблон — сотни КБ XML и несколько тысяч элементов, пределы с запасом в разы.
TEMPLATE_MAX_UNPACKED = 25 * catalog.MB
TEMPLATE_MAX_XML = 4 * catalog.MB
TEMPLATE_MAX_ELEMENTS = 100_000
# Чем zipfile отвечает на испорченный архив: кроме BadZipFile — ошибки zlib,
# неизвестный метод сжатия, обрыв потока.
_BROKEN = (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, NotImplementedError, RuntimeError,
           ValueError, EOFError, OSError)


def _docx_error(name, text):
    return FileError('«%s»: %s' % (name, text), code='DISCIPLINE_DOCX_REFUSED')


def _parse_service_xml(data):
    """Служебный XML пакета без сущностей, DTD и сети: в DOCX их не бывает."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError('DOCTYPE в служебной части')
    return root


def _read_capped(archive, info, limit):
    """Часть архива не больше limit байт. Заявленный размер zipfile не
    превышает при чтении (обрезает и сверяет CRC), поэтому ему можно верить."""
    if info.file_size > limit:
        return None
    with archive.open(info) as handle:
        return handle.read(limit + 1)


def remote_target(target):
    """Ссылка уводит с компьютера того, кто открыл документ: http(s), ftp,
    сетевая папка (\\\\сервер, //сервер, file://сервер/…) и любая другая схема.
    Путь на своём диске (C:\\…, file:///C:/…) и относительный путь внутри
    пакета — нет."""
    # Табуляцию и перевод строки внутри адреса («ht\ttp://…») читатель ссылки
    # выбрасывает, а регулярку схемы они ломали бы — убираем до разбора. Пробел
    # не трогаем: путь Word для Mac («Macintosh HD:Users:…») — свой диск.
    value = re.sub(r'[\x00-\x1f\x7f]', '', unquote(str(target or ''))).strip().replace('\\', '/').lower()
    if value.startswith('//'):
        return True
    match = re.match(r'([a-z][a-z0-9+.\-]*):', value)
    if not match:
        return False
    scheme = match.group(1)
    if len(scheme) == 1:
        return False  # буква диска: c:/…
    if scheme != 'file':
        return True
    # file: — свой, если хоста нет: file:///C:/…, file:///Users/… (Mac), file:/…;
    # file://сервер/… и file:////сервер/… (UNC) — сетевая папка.
    rest = value[len('file:'):]
    slashes = len(rest) - len(rest.lstrip('/'))
    if slashes >= 4:
        return True
    if slashes == 2:
        return rest[2:].split('/', 1)[0] not in ('', 'localhost')
    return False


def check_docx(data, *, name, template=False):
    """DOCX, который можно отдать людям, или FileError с понятной причиной.

    template — шаблон акта: сервер разбирает его при каждом черновике, поэтому
    ещё и пределы на распакованный XML (иначе один файл занимает память и
    процессор сервера минутами)."""
    try:
        archive = zipfile.ZipFile(BytesIO(data))
        infos = archive.infolist()
    except _BROKEN:
        raise _docx_error(name, 'файл повреждён или это не документ Word') from None
    if len(infos) > DOCX_MAX_ENTRIES:
        raise _docx_error(name, 'внутри слишком много частей — это не обычный документ Word')
    lowered = [info.filename.lower() for info in infos]
    # Имена частей в пакете не различают регистр; дубль — либо порча, либо
    # попытка показать проверке одну часть, а Word — другую.
    if len(set(lowered)) != len(lowered) or any(info.flag_bits & 0x1 for info in infos):
        raise _docx_error(name, 'файл повреждён или это не документ Word')
    unpacked = sum(info.file_size for info in infos)
    unpacked_limit = TEMPLATE_MAX_UNPACKED if template else DOCX_MAX_UNPACKED
    if unpacked > unpacked_limit:
        raise _docx_error(name, 'в распакованном виде больше %d МБ — это не обычный документ Word'
                          % (unpacked_limit // catalog.MB))
    by_name = dict(zip(lowered, infos))
    try:
        content_types = _check_content_types(archive, by_name, name)
        for lower, info in zip(lowered, infos):
            if lower.endswith('vbaproject.bin') or lower.endswith('vbadata.xml') or '/activex/' in lower:
                raise _docx_error(name, _MACROS_TEXT)
            if lower.endswith('.rels'):
                _check_relationships(archive, info, name)
        if template:
            _check_template_size(archive, infos, name, content_types)
    except _BROKEN + (etree.XMLSyntaxError,):
        raise _docx_error(name, 'файл повреждён или это не документ Word') from None


def _check_content_types(archive, by_name, name):
    info = by_name.get('[content_types].xml')
    data = _read_capped(archive, info, DOCX_MAX_SERVICE_PART) if info else None
    if not data or len(data) > DOCX_MAX_SERVICE_PART:
        raise _docx_error(name, 'файл повреждён или это не документ Word')
    root = _parse_service_xml(data)
    problem = content_types_problem(item.get('ContentType') for item in root.iter(_CT_NS + 'Override', _CT_NS + 'Default'))
    if problem:
        raise _docx_error(name, problem)
    defaults = {str(item.get('Extension') or '').strip().lower(): str(item.get('ContentType') or '').strip().lower()
                for item in root.iter(_CT_NS + 'Default')}
    overrides = {str(item.get('PartName') or '').strip().lower(): str(item.get('ContentType') or '').strip().lower()
                 for item in root.iter(_CT_NS + 'Override')}
    return defaults, overrides


def _is_xml_part(filename, content_types):
    """Часть, которую читатель разберёт как XML: по имени или по типу из
    [Content_Types].xml — python-docx разбирает часть по типу, и
    word/document.dat с типом документа — такой же XML."""
    lower = filename.lower()
    if lower.endswith(('.xml', '.rels')):
        return True
    defaults, overrides = content_types
    kind = overrides.get('/' + lower.lstrip('/'))
    if kind is None:
        base = lower.rsplit('/', 1)[-1]
        kind = defaults.get(base.rsplit('.', 1)[-1], '') if '.' in base else ''
    # SVG — тоже XML, но для python-docx это картинка: он её не разбирает.
    return kind.endswith('xml') and not kind.startswith('image/')


_MACROS_TEXT = ('в документе макросы или элементы ActiveX — такой файл не принимается. '
                'Сохраните его как обычный документ Word или PDF')


def content_types_problem(content_types):
    """Почему пакет с такими типами частей — не обычный документ Word, или None."""
    types = [str(kind or '').strip().lower() for kind in content_types]
    if any('macroenabled' in kind or 'vbaproject' in kind or 'activex' in kind for kind in types):
        return _MACROS_TEXT
    if _WORD_MAIN not in types:
        return 'это не документ Word (DOCX)'
    return None


def _check_relationships(archive, info, name):
    data = _read_capped(archive, info, DOCX_MAX_SERVICE_PART)
    if data is None or len(data) > DOCX_MAX_SERVICE_PART:
        raise _docx_error(name, 'файл повреждён или это не документ Word')
    for rel in _parse_service_xml(data).iter(_REL_NS + 'Relationship'):
        problem = relationship_problem(rel.get('Type'), rel.get('Target'), rel.get('TargetMode'))
        if problem:
            raise _docx_error(name, problem)


def relationship_problem(rel_type, target, target_mode=None):
    """Почему связь пакета не годится, или None. Внутренняя связь (часть того
    же файла) годится всегда; внешняя — только гиперссылка и шаблон на своём
    диске. Ссылка в сеть без пометки «внешняя» считается внешней."""
    kind = str(rel_type or '').rstrip('/').rsplit('/', 1)[-1]
    if kind == 'aFChunk':
        # Кусок HTML, RTF или другого документа, который Word вливает в
        # документ при открытии, — со своими ссылками; сам Word таких не пишет.
        return ('документ при открытии подмешивает в себя содержимое другого формата. '
                'Сохраните его заново в Word или как PDF')
    external = str(target_mode or '').strip().lower() == 'external'
    if not external and not remote_target(target):
        return None
    if kind == 'hyperlink':
        return None
    if kind == 'attachedTemplate':
        if not remote_target(target):
            return None
        return ('документ подключает шаблон из сети — Word загрузил бы его при открытии. '
                'Сохраните документ без шаблона, как PDF или приложите скан')
    return ('документ ссылается на внешний объект — Word загрузил бы его при открытии. '
            'Сохраните документ как PDF или приложите скан')


def _check_template_size(archive, infos, name, content_types):
    """Шаблон акта: распакованный XML и число элементов — то, что python-docx
    держит в памяти. Элементы считаются потоком, без дерева в памяти."""
    xml_parts = [info for info in infos if _is_xml_part(info.filename, content_types)]
    if sum(info.file_size for info in xml_parts) > TEMPLATE_MAX_XML:
        raise _docx_error(name, 'шаблон слишком большой: текста и разметки в нём больше %d МБ. '
                                'Сохраните в шаблоне только сам акт' % (TEMPLATE_MAX_XML // catalog.MB))
    elements = 0
    for info in xml_parts:
        with archive.open(info) as handle:
            for _event, element in etree.iterparse(handle, events=('end',), resolve_entities=False,
                                                   no_network=True, load_dtd=False, huge_tree=False):
                elements += 1
                if elements > TEMPLATE_MAX_ELEMENTS:
                    raise _docx_error(name, 'шаблон слишком сложный: в нём больше %d элементов разметки. '
                                            'Сохраните в шаблоне только сам акт' % TEMPLATE_MAX_ELEMENTS)
                element.clear()
                parent = element.getparent()
                while parent is not None and element.getprevious() is not None:
                    del parent[0]


def sha256(data):
    return hashlib.sha256(data or b'').hexdigest()


def blob_path_for(kind, filename):
    stamp = datetime.now(ALMATY).strftime('%Y/%m')
    return '%s/%s/%s/%s_%s' % (PREFIX, kind, stamp, uuid.uuid4().hex, safe_name(filename))


def storage_ready(gcs):
    try:
        return bool(gcs and gcs.get('bucket_name') and gcs['bucket_name']())
    except Exception:  # noqa: BLE001
        return False


def upload(gcs, *, kind, data, filename, content_type):
    """Кладёт файл в бакет. Возвращает (bucket, blob_path)."""
    bucket = gcs['bucket_name']() if gcs and gcs.get('bucket_name') else None
    if not bucket:
        raise FileError('Хранилище файлов не настроено', code='DISCIPLINE_STORAGE_OFF', status=503)
    client = gcs['client']()
    blob_path = blob_path_for(kind, filename)
    blob = client.bucket(bucket).blob(blob_path)
    blob.upload_from_string(data, content_type=content_type or 'application/octet-stream')
    return bucket, blob_path


def download(gcs, bucket, blob_path):
    client = gcs['client']()
    blob = client.bucket(bucket).blob(blob_path)
    if not blob.exists():
        return None
    return blob.download_as_bytes()


def drop(gcs, refs):
    """Стирает блобы, которые не дошли до базы (откат транзакции после загрузки).
    Ошибки — только в лог."""
    if not refs:
        return
    try:
        client = gcs['client']()
    except Exception:  # noqa: BLE001
        logging.warning('Дисциплина: клиент хранилища недоступен, файлы не стёрты', exc_info=True)
        return
    for bucket, blob_path in refs:
        try:
            client.bucket(bucket).blob(blob_path).delete()
        except Exception:  # noqa: BLE001
            logging.warning('Дисциплина: не удалось стереть %s/%s', bucket, blob_path, exc_info=True)
