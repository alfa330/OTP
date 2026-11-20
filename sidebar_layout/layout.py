"""Форма раскладки сайдбара — та же, что в src/components/sidebar/sidebarLayoutModel.js.

    {"v": 1,
     "blocks":  [["Задачи", "folder:fk3x9", ...], ...],   # карточки меню сверху вниз
     "folders": {"fk3x9": {"name": "Чаты", "items": ["Чаты ОП", ...]}}}

Ключ раздела — его подпись в меню. Раскладка ничего не открывает и не прячет:
доступ к разделу решают гейты меню и сервер раздела, а здесь только порядок
того, что человеку и так выдано. Поэтому проверка — про форму и размер, а не
про права: чужая подпись в раскладке просто никогда не встретится в меню.

Пределы те же, что у клиента (их сверяет tests/test_sidebar_layout.py): клиент
режет раскладку до них сам, так что ответ 400 здесь — признак поломки клиента,
а не обычный путь.
"""

import re

LAYOUT_VERSION = 1
FOLDER_PREFIX = 'folder:'
FOLDER_NAME_MAX = 40
KEY_MAX = 80
MAX_FOLDERS = 40
MAX_BLOCKS = 60
MAX_ENTRIES = 600
# Тело PUT: раскладка на пределах выше весит ~50 КБ; больше — это не раскладка.
MAX_BODY_BYTES = 96 * 1024
FOLDER_ID_RE = re.compile(r'^f[a-z0-9]{1,15}$')


class LayoutError(ValueError):
    """Раскладка не по форме — текст ошибки уходит клиенту как есть."""


def _blank(code):
    """Символ, который чистка превращает в пробел, — список тот же, что у клиента
    (isBlankCode в sidebarLayoutModel.js): управляющие C0 и C1 (NEL 0x85 Python
    считает пробелом, JS — нет), BOM 0xFEFF (наоборот) и одиночная половинка
    суррогатной пары (её не закодировать в UTF-8 — база упала бы на записи).
    После этой замены «пробелы» у str.split() и у \\s в JS — один и тот же набор."""
    return code < 0x20 or 0x7F <= code <= 0x9F or code == 0xFEFF or 0xD800 <= code <= 0xDFFF


def clean_text(value, max_len):
    """Непечатное — в пробел, пробелы — схлопнуть, длина — по символам."""
    text = ''.join(' ' if _blank(ord(ch)) else ch for ch in str(value))
    text = ' '.join(text.split())
    return text[:max_len].strip()


def _item_key(value):
    if not isinstance(value, str) or not value or value.startswith(FOLDER_PREFIX):
        raise LayoutError('Ключ раздела — непустая строка')
    if len(value) > KEY_MAX or clean_text(value, KEY_MAX) != value:
        raise LayoutError('Ключ раздела слишком длинный или с управляющими символами')
    return value


def normalize_layout(raw):
    """Проверенная раскладка в каноническом виде или LayoutError.

    Пустые карточки выбрасываются (их не видно и в них не положить), имя папки
    чистится так же, как на клиенте. Дубль ключа, папка без ссылки или ссылка
    на неизвестную папку — ошибка: клиент такого не шлёт.
    """
    version = raw.get('v') if isinstance(raw, dict) else None
    # bool — подкласс int: True == 1, и без этой проверки {"v": true} прошёл бы.
    if not isinstance(version, int) or isinstance(version, bool) or version != LAYOUT_VERSION:
        raise LayoutError('Неизвестная версия раскладки')
    blocks_raw = raw.get('blocks')
    folders_raw = raw.get('folders', {})
    if not isinstance(blocks_raw, list) or not isinstance(folders_raw, dict):
        raise LayoutError('Раскладка без карточек')
    if len(blocks_raw) > MAX_BLOCKS:
        raise LayoutError('Слишком много карточек')
    if len(folders_raw) > MAX_FOLDERS:
        raise LayoutError('Слишком много папок')

    seen = set()
    used_folders = set()
    folders = {}
    blocks = []
    total = 0
    for block_raw in blocks_raw:
        if not isinstance(block_raw, list):
            raise LayoutError('Карточка — список')
        block = []
        for entry in block_raw:
            if isinstance(entry, str) and entry.startswith(FOLDER_PREFIX):
                folder_id = entry[len(FOLDER_PREFIX):]
                folder = folders_raw.get(folder_id)
                # fullmatch, а не match с $: в Python $ совпадает и перед «\n» в конце.
                if not FOLDER_ID_RE.fullmatch(folder_id) or not isinstance(folder, dict):
                    raise LayoutError('Ссылка на неизвестную папку')
                if folder_id in used_folders:
                    raise LayoutError('Папка стоит в раскладке дважды')
                used_folders.add(folder_id)
                raw_name = folder.get('name')
                name = clean_text(raw_name, FOLDER_NAME_MAX) if isinstance(raw_name, str) else ''
                if not name:
                    raise LayoutError('У папки нет названия')
                items_raw = folder.get('items', [])
                if not isinstance(items_raw, list):
                    raise LayoutError('Разделы папки — список')
                items = []
                for key in items_raw:
                    key = _item_key(key)
                    if key in seen:
                        raise LayoutError('Раздел стоит в раскладке дважды')
                    seen.add(key)
                    items.append(key)
                folders[folder_id] = {'name': name, 'items': items}
                block.append(entry)
                total += 1 + len(items)
            else:
                key = _item_key(entry)
                if key in seen:
                    raise LayoutError('Раздел стоит в раскладке дважды')
                seen.add(key)
                block.append(key)
                total += 1
            if total > MAX_ENTRIES:
                raise LayoutError('Слишком длинная раскладка')
        if block:
            blocks.append(block)
    if set(folders_raw) - used_folders:
        raise LayoutError('Папка без места в раскладке')
    return {'v': LAYOUT_VERSION, 'blocks': blocks, 'folders': folders}
