"""Правила раздела «Основные средства»: виды и статусы запросов, переходы, проверка данных.

Чистая логика — ни базы, ни Flask: модуль импортируется в тестах напрямую.

ВИДЫ ЗАПРОСОВ (ТЗ, раздел 3 и таблица ролей):
    intake     «Приём в учёт»            бухгалтерия заводит карточку; ОС сразу «На учёте»,
                                         запрос ведёт акт приёма до оригинала
    transfer   «Перемещение»             новый МОЛ и/или место
    writeoff   «Списание»                с проверкой IT или руководителя отдела
    dismissal  «Передача при увольнении» HR передаёт все ОС сотрудника
    need       «Новое ОС»                IT пишет, что нужно, кому и зачем; закрывается
                                         приёмом в учёт

СТАТУСЫ (ТЗ, раздел 5): черновик → на проверке (только списание и «Новое ОС») → на
подписании → ждёт оригинал в бухгалтерии → закрыт; из любого шага — «Отклонён» с причиной.

КОГДА МЕНЯЕТСЯ САМО ОС. Карточка получает нового МОЛ или статус «Списано» в момент
закрытия запроса — когда бухгалтерия отметила «Оригинал получен». До этого запрос можно
отклонить на любом шаге, и откатывать в карточке нечего. Приказ об увольнении при этом
разблокируется раньше — как только приложен скан подписанного акта (SIGNED_STATUSES):
так сказано в ТЗ («подписывается акт, сотрудник прикрепляет скан. После этого HR получает
возможность оформить приказ»).
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

# ─── Виды запросов ───────────────────────────────────────────────────────────

KIND_INTAKE = 'intake'
KIND_TRANSFER = 'transfer'
KIND_WRITEOFF = 'writeoff'
KIND_DISMISSAL = 'dismissal'
KIND_NEED = 'need'

KINDS = (KIND_INTAKE, KIND_TRANSFER, KIND_WRITEOFF, KIND_DISMISSAL, KIND_NEED)
KIND_LABELS = {
    KIND_INTAKE: 'Приём в учёт',
    KIND_TRANSFER: 'Перемещение',
    KIND_WRITEOFF: 'Списание',
    KIND_DISMISSAL: 'Передача при увольнении',
    KIND_NEED: 'Новое ОС',
}
# Запрос, который заводят формой «Новый запрос» (приём в учёт — отдельная форма бухгалтерии).
REQUESTABLE_KINDS = (KIND_TRANSFER, KIND_WRITEOFF, KIND_DISMISSAL, KIND_NEED)
# Запрос занимает ОС: пока он открыт, второй запрос на то же ОС не завести.
LOCKING_KINDS = (KIND_TRANSFER, KIND_WRITEOFF, KIND_DISMISSAL)
# Запрос меняет МОЛ и место.
MOVING_KINDS = (KIND_TRANSFER, KIND_DISMISSAL)
# В запросе есть ОС — значит, и фотографии к ним.
ASSET_KINDS = (KIND_INTAKE, KIND_TRANSFER, KIND_WRITEOFF, KIND_DISMISSAL)

ACT_TITLES = {
    KIND_INTAKE: 'Акт приёма основных средств в учёт',
    KIND_TRANSFER: 'Акт приёма-передачи (перемещения) основных средств',
    KIND_WRITEOFF: 'Акт на списание основных средств',
    KIND_DISMISSAL: 'Акт приёма-передачи основных средств при увольнении',
}
ACT_SHORT = {
    KIND_INTAKE: 'Акт приёма',
    KIND_TRANSFER: 'Акт перемещения',
    KIND_WRITEOFF: 'Акт списания',
    KIND_DISMISSAL: 'Акт передачи',
}

# ─── Статусы запроса ─────────────────────────────────────────────────────────

STATUS_DRAFT = 'draft'
STATUS_REVIEW = 'review'
STATUS_SIGNING = 'signing'
STATUS_ORIGINAL = 'original'
STATUS_CLOSED = 'closed'
STATUS_REJECTED = 'rejected'

STATUSES = (STATUS_DRAFT, STATUS_REVIEW, STATUS_SIGNING, STATUS_ORIGINAL, STATUS_CLOSED, STATUS_REJECTED)
STATUS_LABELS = {
    STATUS_DRAFT: 'Черновик',
    STATUS_REVIEW: 'На проверке',
    STATUS_SIGNING: 'На подписании',
    STATUS_ORIGINAL: 'Ждёт оригинал в бухгалтерии',
    STATUS_CLOSED: 'Закрыт',
    STATUS_REJECTED: 'Отклонён',
}
# Запрос в работе: ушёл от автора и ещё не закрыт и не отклонён.
OPEN_STATUSES = (STATUS_REVIEW, STATUS_SIGNING, STATUS_ORIGINAL)
# Подписанный акт приложен: с этого шага увольнение сотрудника больше не держится запросом.
SIGNED_STATUSES = (STATUS_ORIGINAL, STATUS_CLOSED)

# Кто проверяет запрос на шаге «На проверке».
REVIEW_IT = 'it'
REVIEW_HEAD = 'head'
REVIEW_ACCOUNTING = 'accounting'
REVIEWERS = (REVIEW_IT, REVIEW_HEAD, REVIEW_ACCOUNTING)
REVIEW_LABELS = {
    REVIEW_IT: 'На проверке IT',
    REVIEW_HEAD: 'На проверке у руководителя отдела',
    REVIEW_ACCOUNTING: 'На рассмотрении бухгалтерии',
}
# Кто проверяет ОС категории перед списанием (ТЗ, процесс 3): оргтехнику, компьютеры и
# ноутбуки — IT, мебель и прочее — руководитель отдела.
CHECK_GROUPS = (REVIEW_IT, REVIEW_HEAD)
CHECK_GROUP_LABELS = {REVIEW_IT: 'IT-специалист', REVIEW_HEAD: 'Руководитель отдела'}

# Путь запроса по видам — из него экран рисует ленту шагов.
TRACKS = {
    KIND_INTAKE: (STATUS_SIGNING, STATUS_ORIGINAL, STATUS_CLOSED),
    KIND_TRANSFER: (STATUS_DRAFT, STATUS_SIGNING, STATUS_ORIGINAL, STATUS_CLOSED),
    KIND_DISMISSAL: (STATUS_DRAFT, STATUS_SIGNING, STATUS_ORIGINAL, STATUS_CLOSED),
    KIND_WRITEOFF: (STATUS_DRAFT, STATUS_REVIEW, STATUS_SIGNING, STATUS_ORIGINAL, STATUS_CLOSED),
    KIND_NEED: (STATUS_DRAFT, STATUS_REVIEW, STATUS_CLOSED),
}

# ─── Основное средство ───────────────────────────────────────────────────────

ASSET_ACTIVE = 'active'
ASSET_WRITTEN_OFF = 'written_off'
ASSET_STATUSES = (ASSET_ACTIVE, ASSET_WRITTEN_OFF)
ASSET_STATUS_LABELS = {ASSET_ACTIVE: 'На учёте', ASSET_WRITTEN_OFF: 'Списано'}

DEFAULT_CATEGORIES = (
    ('Компьютеры и ноутбуки', REVIEW_IT),
    ('Оргтехника', REVIEW_IT),
    ('Мебель', REVIEW_HEAD),
    ('Прочее', REVIEW_HEAD),
)

# ─── Файлы ───────────────────────────────────────────────────────────────────

PHOTO_WHOLE = 'photo_whole'      # первое фото: ОС целиком
PHOTO_NUMBER = 'photo_number'    # второе фото: чёткий инвентарный номер
PHOTO_EXTRA = 'photo_extra'
ACT_SCAN = 'act_scan'
PHOTO_KINDS = (PHOTO_WHOLE, PHOTO_NUMBER, PHOTO_EXTRA)
REQUIRED_PHOTOS = (PHOTO_WHOLE, PHOTO_NUMBER)
FILE_KINDS = PHOTO_KINDS + (ACT_SCAN,)
PHOTO_LABELS = {PHOTO_WHOLE: 'ОС целиком', PHOTO_NUMBER: 'инвентарный номер', PHOTO_EXTRA: 'ещё фото'}

MAX_PHOTOS_PER_ASSET = 6
MAX_ASSETS_PER_REQUEST = 30
MAX_SCAN_BYTES = 15 * 1024 * 1024

# ─── Роли «Участников» (поимённые добавки к отделам) ─────────────────────────

MEMBER_ACCOUNTING = 'accounting'
MEMBER_HR = 'hr'
MEMBER_IT = 'it'
MEMBER_OBSERVER = 'observer'
MEMBER_ROLES = (MEMBER_ACCOUNTING, MEMBER_HR, MEMBER_IT, MEMBER_OBSERVER)
MEMBER_ROLE_LABELS = {
    MEMBER_ACCOUNTING: 'Бухгалтерия',
    MEMBER_HR: 'HR',
    MEMBER_IT: 'IT-специалист',
    MEMBER_OBSERVER: 'Просмотр всей истории',
}

# ─── История ─────────────────────────────────────────────────────────────────

EVENT_LABELS = {
    'registered': 'Принято в учёт',
    'corrected': 'Карточка исправлена',
    'submitted': 'Запрос отправлен',
    'checked': 'Проверено',
    'scan_attached': 'Приложен скан подписанного акта',
    'scan_replaced': 'Скан акта заменён',
    'original_received': 'Бухгалтерия получила оригинал акта',
    'closed': 'Запрос закрыт',
    'rejected': 'Запрос отклонён',
    'moved': 'Перемещено',
    'written_off': 'Списано',
    'need_accepted': 'По запросу принято в учёт',
    'barcode_set': 'Привязан штрихкод',
}


def event_label(kind, payload=None):
    """Подпись события в истории. Штрихкод подписан по сути правки: привязан, изменён или снят."""
    if kind == 'barcode_set':
        payload = payload if isinstance(payload, dict) else {}
        if not payload.get('to'):
            return 'Снят штрихкод'
        if payload.get('from'):
            return 'Изменён штрихкод'
    return EVENT_LABELS.get(kind, kind)

# ─── Реквизиты для печатных форм ─────────────────────────────────────────────
# Шапка акта, «Утверждаю» и комиссия формы ДА-3. Хранятся в настройках раздела: в коде
# названий организации и фамилий нет (репозиторий открытый), а на бумаге они обязаны быть.
REQUISITE_TEXTS = (
    ('org_name', 'Организация', 300),
    ('head_name', 'ФИО руководителя', 200),
    ('commission_order_number', 'Номер приказа о комиссии', 40),
    ('chairman_title', 'Должность председателя комиссии', 200),
    ('chairman_name', 'ФИО председателя комиссии', 200),
    ('chief_accountant_name', 'ФИО главного бухгалтера', 200),
)
MAX_COMMISSION_MEMBERS = 8

MAX_BACKDATE_DAYS = 366
CLOCK_SKEW_DAYS = 1
MAX_MONEY = Decimal('999999999999.99')


class FaError(Exception):
    """Ошибка, которую видит человек: текст по-русски, код для экрана, поля с ошибкой."""

    def __init__(self, message, code='FA_INVALID', status=400, fields=None, extra=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.fields = fields or {}
        self.extra = extra or {}


# ─── Разбор значений ─────────────────────────────────────────────────────────

def clean_text(value, limit, *, label=None, required=False):
    """Строка без краевых пробелов; пустая — None."""
    text = '' if value is None else str(value).strip()
    if required and not text:
        raise FaError('Заполните поле «%s»' % (label or 'текст'), code='FA_FIELD_REQUIRED')
    if len(text) > limit:
        raise FaError('Поле «%s» длиннее %d символов' % (label or 'текст', limit), code='FA_FIELD_TOO_LONG')
    return text or None


# Штрихкод с наклейки ОС: EAN-13 бухгалтерии («2000000056784»), Code 128 серийного номера, QR —
# что угодно из букв, цифр и «-./_». Пробелы сканер и человек добавляют сами — их не храним.
BARCODE_MAX = 64
_BARCODE_RE = re.compile(r'^[0-9A-Za-zА-Яа-яЁё./_-]+$')


def clean_barcode(value):
    """Штрихкод карточки: без пробелов; пустой — None."""
    text = re.sub(r'\s+', '', '' if value is None else str(value))
    if not text:
        return None
    if len(text) > BARCODE_MAX or not _BARCODE_RE.match(text):
        raise FaError('Штрихкод — до %d букв и цифр, без пробелов' % BARCODE_MAX, code='FA_BARCODE_INVALID',
                      fields={'barcode': 'Проверьте штрихкод'})
    return text


# Кириллица, похожая на латиницу (строчными): номер «ЕК0000456», набранный латиницей, — тот же номер.
LOOKALIKE_CYRILLIC = 'авекмнорстух'
LOOKALIKE_LATIN = 'abekmhopctyx'
_LOOKALIKE = str.maketrans(LOOKALIKE_CYRILLIC, LOOKALIKE_LATIN)
_UPC_A = re.compile(r'^[0-9]{12}$')
_EAN_13_ZERO = re.compile(r'^0[0-9]{12}$')


def fold_code(value):
    """Код в виде для сравнения: без пробелов, строчными, кириллица-двойник — латиницей. Так ищется и
    номер, заведённый с пробелом («ОС 000123»), и набранный на другой раскладке двойник."""
    return re.sub(r'\s+', '', '' if value is None else str(value)).lower().translate(_LOOKALIKE)


def barcode_variants(value):
    """Один штрихкод UPC-A одни сканеры отдают 12 цифрами, другие — 13 с ведущим нулём (как EAN-13):
    ищем оба написания. Остальные коды — как есть."""
    code = fold_code(value)
    if _UPC_A.match(code):
        return [code, '0' + code]
    if _EAN_13_ZERO.match(code):
        return [code, code[1:]]
    return [code] if code else []


def scan_code(value):
    """Что прочитал сканер (или вбил человек) — для поиска ОС: без пробелов, не длиннее 120."""
    text = re.sub(r'\s+', '', '' if value is None else str(value))[:120]
    return text or None


def clean_int(value, *, label=None, required=False):
    if value in (None, '', 'null'):
        if required:
            raise FaError('Заполните поле «%s»' % (label or 'значение'), code='FA_FIELD_REQUIRED')
        return None
    if isinstance(value, bool):
        raise FaError('Поле «%s»: ожидается число' % (label or 'значение'))
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise FaError('Поле «%s»: ожидается число' % (label or 'значение')) from None
    if number <= 0 or number > 2_000_000_000:
        raise FaError('Поле «%s»: неверное значение' % (label or 'значение'))
    return number


def clean_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'да', 'on')


def clean_date(value, *, label=None, required=False):
    if value in (None, '', 'null'):
        if required:
            raise FaError('Укажите дату: %s' % (label or 'дата'), code='FA_FIELD_REQUIRED')
        return None
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    else:
        text = str(value).strip()[:10]
        day = None
        for pattern in ('%Y-%m-%d', '%d.%m.%Y'):
            try:
                day = datetime.strptime(text, pattern).date()
                break
            except ValueError:
                continue
        if day is None:
            raise FaError('Дата не разобрана: %s' % (label or 'дата'))
    if day.year < 2000 or day.year > 2100:
        raise FaError('Неверная дата: %s' % (label or 'дата'))
    return day


def clean_money(value, *, label='Стоимость'):
    if value in (None, '', 'null'):
        return None
    if isinstance(value, bool):
        raise FaError('Поле «%s»: ожидается сумма' % label)
    text = str(value).replace(' ', '').replace(' ', '').replace(',', '.')
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise FaError('Поле «%s»: ожидается сумма' % label) from None
    if not number.is_finite() or number < 0:
        raise FaError('Поле «%s»: сумма не может быть отрицательной' % label)
    if number > MAX_MONEY:
        raise FaError('Поле «%s»: слишком большая сумма' % label)
    return number.quantize(Decimal('0.01'))


def clean_id_list(value, *, label='ОС', limit=MAX_ASSETS_PER_REQUEST):
    """Список id без повторов, в порядке выбора."""
    if value in (None, ''):
        return []
    if not isinstance(value, (list, tuple)):
        raise FaError('Поле «%s»: ожидается список' % label)
    result = []
    for item in value:
        number = clean_int(item, label=label, required=True)
        if number not in result:
            result.append(number)
    if len(result) > limit:
        raise FaError('В одном запросе не больше %d ОС' % limit, code='FA_TOO_MANY_ASSETS')
    return result


def past_date(day, today, *, label, days_back=MAX_BACKDATE_DAYS):
    """Дата события: не в будущем (запас сутки на разницу часов) и не старше года."""
    if day is None:
        return None
    if day > today + timedelta(days=CLOCK_SKEW_DAYS):
        raise FaError('%s не может быть в будущем' % label, code='FA_DATE_IN_FUTURE')
    if day < today - timedelta(days=days_back):
        raise FaError('%s слишком давно — проверьте год' % label, code='FA_DATE_TOO_OLD')
    return day


def clean_requisites(data):
    """Реквизиты для актов из формы настроек. Все поля необязательны: пустое на бумаге
    остаётся строкой, которую заполняют от руки."""
    data = data if isinstance(data, dict) else {}
    result = {key: clean_text(data.get(key), limit, label=label) for key, label, limit in REQUISITE_TEXTS}
    bin_text = re.sub(r'\s+', '', str(data.get('org_bin') or ''))
    if bin_text and not re.fullmatch(r'\d{12}', bin_text):
        raise FaError('ИИН/БИН — 12 цифр', code='FA_BIN_INVALID', fields={'org_bin': '12 цифр'})
    result['org_bin'] = bin_text or None
    result['commission_order_date'] = clean_date(data.get('commission_order_date'), label='дата приказа о комиссии')
    members = []
    rows = data.get('members')
    if rows not in (None, '') and not isinstance(rows, (list, tuple)):
        raise FaError('Члены комиссии: ожидается список')
    for row in rows or ():
        row = row if isinstance(row, dict) else {}
        title = clean_text(row.get('title'), 200, label='Должность члена комиссии')
        name = clean_text(row.get('name'), 200, label='ФИО члена комиссии')
        if title or name:
            members.append({'title': title, 'name': name})
    if len(members) > MAX_COMMISSION_MEMBERS:
        raise FaError('В комиссии не больше %d человек' % MAX_COMMISSION_MEMBERS, code='FA_TOO_MANY_MEMBERS')
    result['members'] = members
    return result


def service_months(acquired_on, until):
    """Фактический срок эксплуатации в месяцах (графа 5 формы ДА-3).

    Считается по календарным месяцам, как в образце бухгалтерии: принято 13.04.2023, акт от
    01.12.2025 — 32 месяца (с мая 2023 по декабрь 2025 включительно), день месяца не важен."""
    if not acquired_on or not until or until < acquired_on:
        return None
    return (until.year - acquired_on.year) * 12 + (until.month - acquired_on.month)


# ─── Карточка ОС ─────────────────────────────────────────────────────────────

# Что бухгалтерия вправе исправить в карточке, пока открыт запрос приёма (акт ещё не
# закрыт). После закрытия карточку меняет только запрос — «нет обхода». Штрихкода здесь нет:
# его меняет только привязка (POST /assets/<id>/barcode) — она пишет «штрихкод» в историю и
# не трогает списанное ОС.
ASSET_CORRECTABLE = ('name', 'category_id', 'inventory_number', 'serial_number', 'cost', 'acquired_on', 'note')
ASSET_FIELD_LABELS = {
    'name': 'Наименование',
    'category_id': 'Категория',
    'inventory_number': 'Инвентарный номер',
    'serial_number': 'Серийный номер',
    'barcode': 'Штрихкод',
    'cost': 'Стоимость',
    'acquired_on': 'Дата принятия к учёту',
    'note': 'Примечание',
}


def clean_asset(data, today, *, partial=False):
    """Описание ОС из формы «Принять в учёт» (или правки открытого приёма при partial)."""
    data = data if isinstance(data, dict) else {}
    fields = {}

    def take(key, parse):
        if partial and key not in data:
            return
        fields[key] = parse(data.get(key))

    take('name', lambda v: clean_text(v, 300, label='Наименование', required=True))
    take('category_id', lambda v: clean_int(v, label='Категория', required=True))
    take('inventory_number', lambda v: clean_text(v, 64, label='Инвентарный номер', required=True))
    take('serial_number', lambda v: clean_text(v, 120, label='Серийный номер'))
    take('barcode', clean_barcode)
    take('cost', clean_money)
    take('acquired_on', lambda v: past_date(clean_date(v, label='дата принятия к учёту') or today, today,
                                            label='Дата принятия к учёту', days_back=366 * 30))
    take('note', lambda v: clean_text(v, 2000, label='Примечание'))
    return fields


def clean_holder(data):
    """За кем и где числится ОС: МОЛ, отдел, место, «на складе»."""
    data = data if isinstance(data, dict) else {}
    return {
        'user_id': clean_int(data.get('to_user_id'), label='МОЛ', required=True),
        'department_id': clean_int(data.get('to_department_id'), label='Отдел'),
        'location': clean_text(data.get('to_location'), 300, label='Место'),
        'on_stock': clean_bool(data.get('to_stock')),
    }


def next_inventory_number(last):
    """Следующий номер по образцу последнего: «ОС-000123» → «ОС-000124», «0457» → «0458».
    Без цифр в образце подсказки нет — бухгалтер впишет номер сам."""
    text = str(last or '').strip()
    match = re.search(r'(\d+)(?!.*\d)', text)
    if not match:
        return ''
    digits = match.group(1)
    return text[:match.start(1)] + str(int(digits) + 1).zfill(len(digits)) + text[match.end(1):]


def holder_changes(asset, holder):
    """Меняет ли перемещение хоть что-то: МОЛ, отдел, место или «на складе»."""
    return any((
        (asset.get('mol_user_id') or None) != (holder.get('user_id') or None),
        (asset.get('department_id') or None) != (holder.get('department_id') or None),
        (asset.get('location') or None) != (holder.get('location') or None),
        bool(asset.get('on_stock')) != bool(holder.get('on_stock')),
    ))


# ─── Запрос ──────────────────────────────────────────────────────────────────

def clean_request(kind, data):
    """Поля запроса из формы. Полнота проверяется при отправке (missing_for_submit):
    черновик сохраняется и недозаполненным."""
    if kind not in REQUESTABLE_KINDS:
        raise FaError('Неизвестный вид запроса', code='FA_KIND_UNKNOWN')
    data = data if isinstance(data, dict) else {}
    fields = {'comment': clean_text(data.get('comment'), 2000, label='Комментарий')}
    if kind in ASSET_KINDS:
        fields['asset_ids'] = clean_id_list(data.get('asset_ids'))
    if kind in MOVING_KINDS:
        fields['to_user_id'] = clean_int(data.get('to_user_id'), label='Кому')
        fields['to_department_id'] = clean_int(data.get('to_department_id'), label='Отдел')
        fields['to_location'] = clean_text(data.get('to_location'), 300, label='Место')
        fields['to_stock'] = clean_bool(data.get('to_stock'))
    if kind == KIND_DISMISSAL:
        fields['from_user_id'] = clean_int(data.get('from_user_id'), label='Сотрудник', required=True)
    if kind == KIND_NEED:
        fields['need_title'] = clean_text(data.get('need_title'), 300, label='Что нужно')
        fields['need_category_id'] = clean_int(data.get('need_category_id'), label='Категория')
        fields['need_for_user_id'] = clean_int(data.get('need_for_user_id'), label='Кому')
    return fields


def missing_for_submit(request, items):
    """Чего не хватает, чтобы отправить запрос: {поле: текст}. Фото проверяет photo_gaps."""
    kind = request.get('kind')
    missing = {}
    if kind in ASSET_KINDS and not items:
        missing['asset_ids'] = 'Выберите хотя бы одно ОС'
    if kind in MOVING_KINDS and not request.get('to_user_id'):
        missing['to_user_id'] = 'Укажите, кому передаётся ОС'
    if kind == KIND_WRITEOFF and not request.get('comment'):
        missing['comment'] = 'Напишите причину списания'
    if kind == KIND_NEED:
        if not request.get('need_title'):
            missing['need_title'] = 'Напишите, что нужно'
        if not request.get('need_for_user_id'):
            missing['need_for_user_id'] = 'Укажите, кому нужно ОС'
        if not request.get('comment'):
            missing['comment'] = 'Напишите, зачем нужно ОС'
    return missing


def photo_gaps(items, photos):
    """Каких обязательных фото не хватает: [{asset_id, name, inventory_number, missing: [вид]}].

    ТЗ, правило «Фото при любом запросе»: не менее двух — первое показывает ОС целиком,
    второе даёт чёткий инвентарный номер. `photos` — [(asset_id, вид)]."""
    have = {}
    for asset_id, kind in photos or ():
        have.setdefault(int(asset_id), set()).add(kind)
    gaps = []
    for item in items or ():
        missing = [kind for kind in REQUIRED_PHOTOS if kind not in have.get(int(item['asset_id']), ())]
        if missing:
            gaps.append({'asset_id': int(item['asset_id']), 'name': item.get('name'),
                         'inventory_number': item.get('inventory_number'), 'missing': missing})
    return gaps


def photo_gap_message(gaps):
    """Причина, по которой запрос не уходит, — словами (ТЗ: «система показывает причину»)."""
    if not gaps:
        return ''
    parts = []
    for gap in gaps[:3]:
        title = gap.get('name') or 'ОС'
        if gap.get('inventory_number'):
            title = '%s (инв. № %s)' % (title, gap['inventory_number'])
        parts.append('%s — %s' % (title, ' и '.join(PHOTO_LABELS[kind] for kind in gap['missing'])))
    more = len(gaps) - len(parts)
    text = 'Не хватает фото: ' + '; '.join(parts)
    if more > 0:
        text += ' и ещё у %d ОС' % more
    return text + '. К каждому ОС нужны два снимка: ОС целиком и инвентарный номер.'


def after_submit(kind):
    """Куда уходит запрос от автора."""
    if kind in (KIND_WRITEOFF, KIND_NEED):
        return STATUS_REVIEW
    return STATUS_SIGNING


def review_group(kind, check_groups):
    """Кто проверяет запрос на шаге «На проверке». У списания — по категории ОС; оргтехника
    и мебель в одном запросе не сочетаются (у них разные проверяющие)."""
    if kind == KIND_NEED:
        return REVIEW_ACCOUNTING
    groups = {group for group in check_groups if group}
    if len(groups) > 1:
        raise FaError('Оргтехнику и мебель списывают разными запросами: у них разные проверяющие — '
                      'IT и руководитель отдела', code='FA_MIXED_CHECK')
    return next(iter(groups), REVIEW_HEAD)


def status_label(request):
    status = request.get('status')
    if status == STATUS_REVIEW:
        return REVIEW_LABELS.get(request.get('review_by'), STATUS_LABELS[STATUS_REVIEW])
    return STATUS_LABELS.get(status, status or '')


def track(request):
    """Лента шагов запроса: [{code, label, state}] — done / current / todo; отклонённый
    запрос останавливается на шаге, с которого его отклонили."""
    kind = request.get('kind')
    steps = TRACKS.get(kind, ())
    status = request.get('status')
    at = request.get('rejected_from') if status == STATUS_REJECTED else status
    index = steps.index(at) if at in steps else -1
    result = []
    for position, code in enumerate(steps):
        label = STATUS_LABELS[code]
        if code == STATUS_REVIEW:
            label = REVIEW_LABELS.get(request.get('review_by'), label)
        if status == STATUS_CLOSED or position < index:
            state = 'done'
        elif position == index:
            state = 'stopped' if status == STATUS_REJECTED else 'current'
        else:
            state = 'todo'
        result.append({'code': code, 'label': label, 'state': state})
    return result


def signers(request, heads=None):
    """Кто подписывает акт НА БУМАГЕ: [(роль, ФИО)]. ТЗ: акт перемещения подписывают
    отдающий, принимающий и руководители отделов; акт списания — ответственные лица и
    руководители. В самой системе сдающий и принимающий (МОЛ) ничего не нажимают — скан
    подписанного акта прикладывает тот, кто ведёт запрос (access.can_attach_scan)."""
    heads = heads or {}
    kind = request.get('kind')
    from_head = heads.get(request.get('from_department_id'))
    to_head = heads.get(request.get('to_department_id'))
    people = []
    if kind in MOVING_KINDS:
        people.append(('Сдал', request.get('from_name')))
        people.append(('Принял', request.get('to_name')))
        if from_head:
            people.append(('Руководитель отдела (сдающая сторона)', from_head))
        if to_head and to_head != from_head:
            people.append(('Руководитель отдела (принимающая сторона)', to_head))
    elif kind == KIND_WRITEOFF:
        people.append(('Материально ответственное лицо', request.get('from_name')))
        if from_head:
            people.append(('Руководитель отдела', from_head))
        if request.get('checked_by_name'):
            people.append(('Проверил', request.get('checked_by_name')))
    elif kind == KIND_INTAKE:
        people.append(('Принял (МОЛ)', request.get('to_name')))
        if to_head:
            people.append(('Руководитель отдела', to_head))
    people.append(('Бухгалтер', None))
    # Руководитель, который сам сдаёт или принимает ОС, подписывает акт один раз — как сторона.
    sides = {request.get('from_name'), request.get('to_name')} - {None, ''}
    seen, result = set(), []
    for role, name in people:
        key = (role, name)
        if key in seen or (role.startswith('Руководитель отдела') and name in sides):
            continue
        seen.add(key)
        result.append((role, name))
    return result
