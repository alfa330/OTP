# -*- coding: utf-8 -*-
"""Задача типа «Продакшн»: справочник брифа и его проверка (задача #354).

ЗАЧЕМ. Отделу маркетинга ставят задачи на дизайн, видео и баннеры, и до сих
пор постановка была свободным текстом: что именно нужно, где это повиснет, в
каких размерах и цветах — выяснялось перепиской и правками по кругу. Тип
«Продакшн» добавляет к задаче бриф с обязательными полями из ТЗ постановщика:
наименование, адаптация, размеры, цветность. Описание задания — обычное
описание задачи; при этом типе оно тоже обязательно.

КОМУ. Тип доступен, только когда среди исполнителей есть сотрудник отдела
маркетинга (`users.department_id` → `departments.code = 'marketing'`). Ровно
так сказано в ТЗ: «при выборе сотрудника из отдела маркетинга». Проверяет это
database.py (там состав исполнителей), здесь — только сам бриф.

ГДЕ ХРАНИТСЯ. Колонкой `tasks.production_brief` (JSONB), а не отдельной
таблицей: бриф живёт и умирает вместе с задачей, читается всегда целиком, а
список задач обязан оставаться одним запросом «строка = задача» — отдельная
таблица потребовала бы ещё одного пакетного запроса на каждую страницу. В базе
лежат только коды вариантов; подписи берутся отсюда, поэтому переименование
пункта не требует миграции данных.

ТОТ ЖЕ СПРАВОЧНИК живёт во фронте — src/components/tasks/productionBrief.js:
форма показывает варианты оттуда, сервер принимает только эти. Разъехаться им
не даёт tests/test_task_production.py: он сверяет коды, подписи и размеры
обоих файлов буквально.

Модуль — лист: json и ничего больше. Ни Flask, ни базы, поэтому тест
проверяет его целиком.
"""

import json

PRODUCTION_TAG = 'production'
PRODUCTION_DEPARTMENT_CODE = 'marketing'

# ── Справочник ──────────────────────────────────────────────────────────────
# Наименование: что именно нужно сделать. Пункты — дословно из ТЗ, группы —
# по тому, как ТЗ само их перечисляет через точку с запятой.
ITEM_GROUPS = (
    ('print', 'Полиграфия', (
        ('leaflet', 'Лифлет'),
        ('flyer', 'Флаер'),
        ('brochure', 'Брошюра'),
        ('catalog', 'Каталог'),
        ('poster', 'Плакат'),
        ('wobbler', 'Воблер'),
        ('shelf_talker', 'Шелфтокер'),
        ('roll_up', 'Выставочный ролл-ап'),
    )),
    ('digital', 'Digital', (
        ('static_banner', 'Статичный баннер'),
        ('animated_banner', 'Анимированный баннер'),
        ('target_feed', 'Креатив для таргета — лента'),
        ('target_stories', 'Креатив для таргета — Stories'),
        ('target_reels', 'Креатив для таргета — Reels'),
        ('profile_cover', 'Обложка профиля'),
        ('teaser', 'Тизер'),
        ('popup', 'Поп-ап'),
    )),
    ('video', 'Видео и анимация', (
        ('promo_video', 'Промо-ролик'),
        ('preroll', 'Короткий преролл'),
        ('explainer', 'Эксплейнер'),
        ('animation_2d', '2D-анимированный креатив'),
        ('animation_3d', '3D-анимированный креатив'),
    )),
    ('brand', 'Бренд и упаковка', (
        ('packaging', 'Дизайн упаковки'),
        ('label', 'Этикетка'),
        ('merch', 'Фирменный мерч'),
        ('brand_guidelines', 'Гайдлайн по фирменному стилю'),
    )),
)

# Адаптация: где будет размещаться. Можно несколько.
CHANNELS = (
    ('digital', 'Рекламные digital-сети и веб-сайты'),
    ('social', 'Социальные сети и мессенджеры'),
    ('outdoor', 'Наружная реклама'),
    ('retail', 'Точки продаж и ритейл'),
    ('print', 'Полиграфия и пресса'),
    ('merch', 'Брендированная продукция'),
)

# Размеры: готовые форматы, как их показывает Canva, — экранные пропорцией в
# пикселях, печатные листом в миллиметрах. Ориентация видна по самим числам:
# 9:16 и A4 вертикальные, 16:9 и A4 альбомный — горизонтальные. Всё, чего здесь
# нет (баннер 728×90, наклейка, вывеска), задаётся своим размером.
# (код, группа, подпись, ширина, высота, единица)
SIZE_PRESETS = (
    ('1x1', 'screen', '1:1', 1080, 1080, 'px'),
    ('4x5', 'screen', '4:5', 1080, 1350, 'px'),
    ('9x16', 'screen', '9:16', 1080, 1920, 'px'),
    ('16x9', 'screen', '16:9', 1920, 1080, 'px'),
    ('a5', 'print', 'A5', 148, 210, 'mm'),
    ('a4', 'print', 'A4', 210, 297, 'mm'),
    ('a4_landscape', 'print', 'A4 альбомный', 297, 210, 'mm'),
    ('a3', 'print', 'A3', 297, 420, 'mm'),
    ('rollup_85x200', 'print', 'Ролл-ап', 850, 2000, 'mm'),
)
SIZE_UNITS = (('px', 'px'), ('mm', 'мм'))

# Цветность: одно из трёх. «Свои тона» требуют написать, какие.
COLOR_MODES = (
    ('custom', 'Свои тона'),
    ('brandbook', 'Брендбук'),
    ('designer', 'На усмотрение дизайнера'),
)

# Потолки — защита от мусора в запросе мимо формы, а не правило ТЗ.
MAX_CUSTOM_SIZES = 6
MAX_SIDE = 100000
MAX_COLOR_NOTES = 300

ITEM_LABELS = {code: label for _group, _title, items in ITEM_GROUPS for code, label in items}
CHANNEL_LABELS = dict(CHANNELS)
SIZE_PRESET_BY_CODE = {row[0]: row for row in SIZE_PRESETS}
UNIT_LABELS = dict(SIZE_UNITS)
COLOR_LABELS = dict(COLOR_MODES)

# Что не заполнено → как это назвать человеку. Порядок — порядок полей формы.
FIELD_LABELS = (
    ('item', 'наименование'),
    ('channels', 'адаптация'),
    ('sizes', 'размеры'),
    ('color', 'цветность'),
    ('color_notes', 'какие тона'),
    ('description', 'описание задания'),
)

INCOMPLETE_PREFIX = 'PRODUCTION_BRIEF_INCOMPLETE:'
INVALID_BRIEF = 'INVALID_PRODUCTION_BRIEF'
NEEDS_MARKETING_ASSIGNEE = 'PRODUCTION_NEEDS_MARKETING_ASSIGNEE'


def incomplete(fields):
    """Код ошибки «в брифе не заполнено …» с перечнем полей в порядке формы."""
    order = [key for key, _label in FIELD_LABELS]
    picked = [key for key in order if key in set(fields)]
    return INCOMPLETE_PREFIX + ','.join(picked)


def error_message(code):
    """Текст ошибки для человека или None, если код не про бриф.

    Форма проверяет всё сама и подсвечивает поля, поэтому эти тексты видит
    только тот, кто прошёл мимо формы (старая вкладка, прямой запрос к API).
    """
    code = str(code or '')
    if code.startswith(INCOMPLETE_PREFIX):
        keys = [key for key in code[len(INCOMPLETE_PREFIX):].split(',') if key]
        labels = dict(FIELD_LABELS)
        names = [labels.get(key, key) for key in keys]
        return 'В брифе не заполнено: %s' % ', '.join(names) if names else 'Заполните бриф продакшна'
    if code == INVALID_BRIEF:
        return 'В брифе указан вариант, которого нет в списке'
    if code == NEEDS_MARKETING_ASSIGNEE:
        return 'Тип «Продакшн» — только для задачи сотруднику отдела маркетинга'
    return None


def _clean_code(value):
    return str(value or '').strip().lower() if isinstance(value, (str, int)) else ''


def _clean_codes(values, allowed):
    """Список кодов в порядке справочника, без повторов. Чужой код — ошибка.

    Порядок справочника, а не порядок щелчков: одно и то же множество обязано
    давать один и тот же бриф, иначе правка, ничего не поменявшая, считалась
    бы изменением и рассылала уведомления.
    """
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError(INVALID_BRIEF)
    picked = set()
    for value in values:
        code = _clean_code(value)
        if not code:
            continue
        if code not in allowed:
            raise ValueError(INVALID_BRIEF)
        picked.add(code)
    return [code for code in allowed if code in picked]


def _clean_side(value):
    if isinstance(value, bool):
        raise ValueError(INVALID_BRIEF)
    try:
        number = float(str(value).strip().replace(',', '.'))
    except (TypeError, ValueError):
        raise ValueError(INVALID_BRIEF)
    if number != int(number):
        raise ValueError(INVALID_BRIEF)
    number = int(number)
    if number < 1 or number > MAX_SIDE:
        raise ValueError(INVALID_BRIEF)
    return number


def _clean_custom_sizes(values):
    if values is None:
        return []
    if not isinstance(values, (list, tuple)) or len(values) > MAX_CUSTOM_SIZES:
        raise ValueError(INVALID_BRIEF)
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(INVALID_BRIEF)
        unit = _clean_code(value.get('unit'))
        if unit not in UNIT_LABELS:
            raise ValueError(INVALID_BRIEF)
        size = {'width': _clean_side(value.get('width')),
                'height': _clean_side(value.get('height')),
                'unit': unit}
        if size not in result:
            result.append(size)
    return result


def normalize_brief(raw):
    """Бриф из запроса → канонический словарь для базы.

    ValueError(INVALID_BRIEF) — вариант не из справочника или битая форма
    данных: это ошибка клиента, а не человека, поэтому она главнее пропусков.
    ValueError(incomplete([...])) — чего-то не хватает; описание задания здесь
    не проверяется — оно поле задачи, и его проверяет database.py.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ValueError(incomplete(['item', 'channels', 'sizes', 'color']))
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode('utf-8', 'replace')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ValueError(INVALID_BRIEF)
    if not isinstance(raw, dict):
        raise ValueError(INVALID_BRIEF)

    item = _clean_code(raw.get('item'))
    if item and item not in ITEM_LABELS:
        raise ValueError(INVALID_BRIEF)
    channels = _clean_codes(raw.get('channels'), [code for code, _label in CHANNELS])
    sizes = _clean_codes(raw.get('sizes'), [row[0] for row in SIZE_PRESETS])
    custom_sizes = _clean_custom_sizes(raw.get('custom_sizes'))
    color = _clean_code(raw.get('color'))
    if color and color not in COLOR_LABELS:
        raise ValueError(INVALID_BRIEF)
    color_notes = raw.get('color_notes')
    color_notes = str(color_notes).strip()[:MAX_COLOR_NOTES] if isinstance(color_notes, str) else ''

    missing = []
    if not item:
        missing.append('item')
    if not channels:
        missing.append('channels')
    if not sizes and not custom_sizes:
        missing.append('sizes')
    if not color:
        missing.append('color')
    elif color == 'custom' and not color_notes:
        missing.append('color_notes')
    if missing:
        raise ValueError(incomplete(missing))

    return {
        'item': item,
        'channels': channels,
        'sizes': sizes,
        'custom_sizes': custom_sizes,
        'color': color,
        # Тона нужны только при «Свои тона»: при «Брендбуке» строка, оставшаяся
        # от передуманного выбора, читалась бы как указание дизайнеру.
        'color_notes': color_notes if color == 'custom' else None,
    }


def stored_brief(value):
    """Бриф из колонки базы: psycopg2 отдаёт JSONB словарём, старые драйверы — строкой."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode('utf-8', 'replace')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def size_text(width, height, unit):
    # Без разделителя разрядов: дизайнеры пишут «1080×1920», а не «1 080×1 920».
    return '%d×%d %s' % (int(width), int(height), UNIT_LABELS.get(unit, unit))


def item_label(brief):
    brief = stored_brief(brief) or {}
    code = brief.get('item')
    return ITEM_LABELS.get(code, code or '')


def describe(brief):
    """Бриф строками «поле — значение» для выгрузки, Telegram и CLI.

    Неизвестный код (пункт убрали из справочника, а задачи остались) выводится
    как есть: лучше показать код, чем молча потерять, что заказывали.
    """
    brief = stored_brief(brief)
    if not brief:
        return []
    channels = [CHANNEL_LABELS.get(code, code) for code in brief.get('channels') or []]
    sizes = []
    for code in brief.get('sizes') or []:
        preset = SIZE_PRESET_BY_CODE.get(code)
        sizes.append('%s — %s' % (preset[2], size_text(preset[3], preset[4], preset[5])) if preset else code)
    for size in brief.get('custom_sizes') or []:
        try:
            sizes.append(size_text(size['width'], size['height'], size.get('unit')))
        except (KeyError, TypeError, ValueError):
            continue
    color = brief.get('color')
    color_text = COLOR_LABELS.get(color, color or '')
    if color == 'custom' and brief.get('color_notes'):
        color_text = '%s: %s' % (color_text, brief['color_notes'])
    rows = [
        ('Наименование', item_label(brief)),
        ('Адаптация', '; '.join(channels)),
        ('Размеры', '; '.join(sizes)),
        ('Цветность', color_text),
    ]
    return [(label, value) for label, value in rows if value]


def describe_text(brief):
    return '\n'.join('%s: %s' % (label, value) for label, value in describe(brief))
