"""Правила полей «Моих данных»: кто вправе, что принимаем и в каком виде храним.

Модуль без Flask и без базы — его целиком проверяют тесты. Зеркало на фронте —
src/components/profile/myData.js; списки отделов и курсов сверяет тест, и
меняются они вместе.
"""

import re

# Кому раздел открыт — решение владельца 24.09.2026: операторы СЗоВ, ОП и Тез.
# Стажёрам не нужен, поэтому роль ровно одна, без 'trainee'.
ELIGIBLE_ROLE = 'operator'
ELIGIBLE_DEPARTMENT_CODES = frozenset({'szov', 'op', 'tez'})

# Шесть полей постановки — и ни одного больше. Имена колонок совпадают и в
# users, и в user_hr_profiles, поэтому одно имя служит обеим таблицам и строке
# истории: «Учет сотрудников» пишет в user_history те же ключи.
FIELDS = (
    'phone',
    'telegram_nick',
    'card_number',
    'study_place',
    'study_specialty',
    'study_course',
)

# Курс выбирается из списка. Цифрой — как уже записано у 174 из 198 человек,
# кто курс указал (прод, 24.09.2026); магистратура отдельными строками, потому
# что «1 курс» магистранта и бакалавра — разные люди, а в базе есть и те и другие.
COURSE_OPTIONS = (
    '1', '2', '3', '4', '5', '6',
    'Магистратура, 1 курс',
    'Магистратура, 2 курс',
)

# Колонки учёбы — VARCHAR(255); больше не влезет, и лучше сказать об этом
# словами, чем уронить запрос.
TEXT_MAX_LENGTH = 255

# Цифры — только ASCII. В Python \d и \D понимают любые цифры Юникода, и
# «４４００…» или «٤٤٠٠…» прошли бы проверку и легли в базу непригодным
# номером; фронт (JS \D) такие знаки отбрасывает, сервер не должен быть мягче.
_NOT_DIGIT = re.compile(r'[^0-9]')

CARD_DIGITS = 16

_TELEGRAM_LINK_PREFIX = re.compile(r'^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/', re.IGNORECASE)
# Алфавит и длина — как у ников Telegram: латиница, цифры и подчёркивание,
# от 5 до 32 знаков. Остальные тонкости Telegram не проверяем: ник, которого нет,
# не поймает никакая проверка формата, а лишняя строгость отбила бы настоящие.
_TELEGRAM_USERNAME = re.compile(r'^[A-Za-z0-9_]{5,32}$')
_SPACES = re.compile(r'\s+')

ERROR_PHONE = 'Телефон: +7 и 10 цифр, например +7 701 234 56 78'
ERROR_TELEGRAM = 'Ник в Telegram: латиница, цифры и «_», от 5 до 32 знаков'
ERROR_CARD = 'Номер карты — 16 цифр'
ERROR_COURSE = 'Курс выбирается из списка'
ERROR_TEXT_TOO_LONG = f'Не длиннее {TEXT_MAX_LENGTH} знаков'


def is_eligible(role, department_code):
    """Открыт ли раздел человеку с этой ролью и отделом (роль уже нормализована)."""
    return (str(role or '').strip().lower() == ELIGIBLE_ROLE
            and str(department_code or '').strip().lower() in ELIGIBLE_DEPARTMENT_CODES)


def normalize_phone(value):
    """Телефон → '+7XXXXXXXXXX' — тот же вид, что требует «Учет сотрудников».

    Принимаем, как человек привык писать: «8 701…», «+7 (701) …», «701…».
    Пусто — стереть. Возвращает (значение, ошибка).
    """
    raw = str(value or '').strip()
    if not raw:
        return None, None
    digits = _NOT_DIGIT.sub('', raw)
    if len(digits) == 11 and digits[0] in '78':
        digits = digits[1:]
    if len(digits) != 10:
        return None, ERROR_PHONE
    return '+7' + digits, None


def normalize_telegram(value):
    """Ник → '@username'. Ссылку t.me/… и лишнюю «@» снимаем сами."""
    raw = str(value or '').strip()
    if not raw:
        return None, None
    username = _TELEGRAM_LINK_PREFIX.sub('', raw).strip().strip('/').lstrip('@').strip()
    if not _TELEGRAM_USERNAME.match(username):
        return None, ERROR_TELEGRAM
    return '@' + username, None


def normalize_card(value):
    """Номер карты → ровно 16 цифр подряд; пробелы и дефисы между группами снимаем.

    Пустое значение — ошибка, а не «стереть»: полный номер оператору не
    показывается, поле ввода всегда пустое, и пустота в нём значит «не менял».
    Интерфейс такой ключ не шлёт вовсе; прислали — значит, что-то не так.
    """
    digits = re.sub(r'[\s-]', '', str(value or ''))
    if not re.fullmatch(r'[0-9]{%d}' % CARD_DIGITS, digits):
        return None, ERROR_CARD
    return digits, None


def normalize_text(value):
    """Университет, специальность: лишние пробелы схлопываем, пусто — стереть."""
    text = _SPACES.sub(' ', str(value or '')).strip()
    if not text:
        return None, None
    if len(text) > TEXT_MAX_LENGTH:
        return None, ERROR_TEXT_TOO_LONG
    return text, None


def normalize_course(value):
    text = str(value or '').strip()
    if not text:
        return None, None
    if text not in COURSE_OPTIONS:
        return None, ERROR_COURSE
    return text, None


_NORMALIZERS = {
    'phone': normalize_phone,
    'telegram_nick': normalize_telegram,
    'card_number': normalize_card,
    'study_place': normalize_text,
    'study_specialty': normalize_text,
    'study_course': normalize_course,
}


def validate(payload):
    """Тело запроса → (чистые значения, ошибки по полям, чужие ключи).

    Чужой ключ — всё, чего нет в FIELDS, включая user_id: правка чужих данных и
    полей вне постановки отклоняется целиком, а не молча пропускается, иначе
    ошибка в интерфейсе выглядела бы как «сохранилось».
    """
    if not isinstance(payload, dict):
        return {}, {}, ['<body>']
    unknown = sorted(str(key) for key in payload if key not in _NORMALIZERS)
    clean, errors = {}, {}
    for field, normalize in _NORMALIZERS.items():
        if field not in payload:
            continue
        value, error = normalize(payload[field])
        if error:
            errors[field] = error
        else:
            clean[field] = value
    return clean, errors, unknown


def card_last4(card_number):
    """Последние 4 цифры карты — единственное, что оператор видит из номера."""
    digits = _NOT_DIGIT.sub('', str(card_number or ''))
    return digits[-4:] if len(digits) >= 4 else None


def public_view(row):
    """Что уходит оператору. Полного номера карты здесь нет и быть не должно."""
    row = row or {}
    return {
        'phone': row.get('phone'),
        'telegram_nick': row.get('telegram_nick'),
        'has_card': bool(str(row.get('card_number') or '').strip()),
        'card_last4': card_last4(row.get('card_number')),
        'study_place': row.get('study_place'),
        'study_specialty': row.get('study_specialty'),
        'study_course': row.get('study_course'),
    }
