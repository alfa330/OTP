"""Право водителя на воду. Чистая логика: ни базы, ни сети, ни Flask.

Правила — дословно из ТЗ и ответов постановщика (29–30.09.2026), без
«логичных» обобщений:

  * НОВЫЙ водитель — «тот, кто был зарегистрирован и не выполнил заказ»:
    счётчик выполненных заказов CRM равен нулю. Ему положен приветственный
    блок — ОДИН раз, «только если он будет работать по тарифам Business и
    Ultima» и «у них обязательно должен быть пройден ФК» (30.09.2026). ФК
    берётся из CRM; когда CRM его не знает, пройденный ФК подтверждает
    сотрудник, проверив во Флите, — иначе приветственный не выдать никому.
  * ДЕЙСТВУЮЩИЙ водитель (хотя бы один заказ) получает блок за активность:
    «следующий блок он может получить, когда сделает 20 поездок и не менее
    7 дней чтобы прошло; если за 5 дней сделает 20 поездок, то получит блок
    только когда пройдёт 7 дней». Тарифы программы — те же.
  * В офисе должна быть вода.

Откуда число поездок. «20 поездок с прошлой выдачи» — заказы, сделанные
ПОСЛЕ момента прошлой выдачи:

  * выдача внутри окна CRM «7 дней» (обычный визит на седьмой день) — считаем
    заказы списка `last_7_days`, сделанные позже неё;
  * выдача раньше окна — окно целиком лежит после неё, и счётчик недели уже
    нижняя граница: хватает его — выдаём без лишнего запроса;
  * не хватает — спрашиваем у CRM заказы за даты: со дня выдачи по сегодня
    минус заказы того дня до выдачи (`orders.today` в снимке CRM, который
    выдача хранит целиком). Снимка нет или выдача была на другом аккаунте того
    же человека — день выдачи не считаем вовсе: лучше недосчитать, чем
    засчитать поездки, сделанные до выдачи.

Разницу счётчика `total` не берём: это не «всё время», а окно около полугода,
из которого старые заказы выпадают каждый день. 21.10.2026 водитель Астаны
сделал после выдачи 173 поездки, а счётчик вырос на 6 (3945 → 3951) — и ему
отказали; неделей раньше выдача прошла лишь потому, что счётчик упал
(4010 → 3945) и сработал запасной путь «окно 7 дней».

Если прошлой выдачи не было, берём окно CRM «за последние 7 дней»: это и есть
«20 заказов в неделю» из ТЗ.

Приветственный блок тоже выдача: неделя и поездки после него считаются так
же, как после блока за активность.

«Прошло 7 дней» — по календарным дням Алматы: выдали в понедельник вечером —
следующая с понедельника, а не с понедельника вечера. Так это понимает
человек за стойкой, и так же это видно в журнале (там даты, а не часы).
"""

from datetime import date, datetime, timedelta, timezone

# Время раздела и CRM — местное, Алматы (UTC+5 круглый год с 2024 года).
_ALMATY = timezone(timedelta(hours=5))

BASIS_NEW = 'new'
BASIS_SINCE_LAST = 'since_last'
BASIS_WEEK = 'week'

# ФК (фотоконтроль) нового водителя. «Пройден» — по CRM; «неизвестно» — CRM
# статуса не отдала (поле заполняется только после ручного обновления карточки
# оператором), и тогда пройденный ФК подтверждает сотрудник, проверив во Флите.
FK_PASSED = 'passed'
FK_FAILED = 'failed'
FK_UNKNOWN = 'unknown'
FK_CONFIRMED = 'confirmed'


def plural(count, one, few, many):
    value = abs(int(count)) % 100
    if 11 <= value <= 14:
        return many
    value %= 10
    if value == 1:
        return one
    if 2 <= value <= 4:
        return few
    return many


def trips_word(count):
    return '%d %s' % (count, plural(count, 'поездка', 'поездки', 'поездок'))


def days_word(count):
    return '%d %s' % (count, plural(count, 'день', 'дня', 'дней'))


def blocks_word(count):
    return '%d %s' % (count, plural(count, 'блок', 'блока', 'блоков'))


def blocks_genitive(count):
    """«не больше 1 блока», «не больше 2 блоков» — после «больше/меньше»."""
    return '%d %s' % (count, plural(count, 'блока', 'блоков', 'блоков'))


def ru_date(value):
    day = day_of(value)
    return day.strftime('%d.%m.%Y') if day else ''


def day_of(value):
    """Календарный день из datetime, date или ISO-строки."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def moment_of(value):
    """Момент из datetime, date или строки («2026-10-14 16:36:32», ISO).

    И база раздела, и CRM пишут местное время без пояса — так и сравниваем.
    Значение с поясом приводим к Алматы, негодное — None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(_ALMATY).replace(tzinfo=None)
    return moment


def as_count(value):
    """Неотрицательное целое или None: счётчик из снимка CRM бывает чем угодно."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def tariff_label(code, labels=None):
    return (labels or TARIFF_LABELS).get(code) or code


# Подписи тарифов — коды Флита, в которых их отдаёт CRM. Двойник живёт в
# src/components/water/waterMeta.js (TARIFF_LABELS), их сверяет тест.
# Неизвестный код показывается как есть: лучше код, чем чужое название.
TARIFF_LABELS = {
    'econom': 'Эконом',
    'comfort': 'Комфорт',
    'comfort_plus': 'Комфорт+',
    'business': 'Business',
    'ultimate': 'Premier (Ultima)',
    'maybach': 'Élite',
    'minivan': 'Минивэн',
    'express': 'Доставка',
    'courier': 'Курьер',
    'cargo': 'Грузовой',
    'intercity': 'Межгород',
    'personal_driver': 'Личный водитель',
}


def _program_label(settings):
    codes = list(settings.get('tariffs') or [])
    return ', '.join(tariff_label(code) for code in codes) or 'не заданы'


def evaluate(driver, history, settings, *, today, stock=None, fk_confirmed=None, period=None):
    """Положена ли вода. Возвращает словарь-вердикт.

    driver   — карточка из water.driver.normalize: tariffs, orders.total,
               orders.week, orders.window_from, order_times, account_id;
    history  — прошлые выдачи ЭТОГО человека (по аккаунту или ИИН), новые
               первыми: kind, blocks, created_at, driver_account_id,
               orders_today, office_name;
    settings — min_trips, cooldown_days, welcome_blocks, activity_blocks,
               tariffs;
    stock    — остаток выбранного офиса; None — офис не выбран (колл-центр
               проверяет право без офиса), воду тогда не проверяем;
    fk_confirmed — подтвердил ли сотрудник ФК, когда CRM его не знает. None —
               предпросмотр (проверка без выдачи): подтверждение ещё впереди,
               и отказом это не считается; False на выдаче — отказ.
    period   — ответ CRM на period_request: тот же словарь плюс `count`
               (None — CRM не ответила). Ответ на другой запрос не годится.

    reasons — причины отказа человеческими фразами, в том порядке, в каком их
    стоит читать. Пустой список = можно выдавать.
    """
    history = list(history or [])
    orders = driver.get('orders') or {}
    total = orders.get('total')
    program = [str(code) for code in (settings.get('tariffs') or [])]
    tariffs = [str(code) for code in (driver.get('tariffs') or [])]
    matched = [code for code in tariffs if code in program]

    last = history[0] if history else None
    welcome = next((item for item in history if item.get('kind') == 'welcome'), None)
    reasons = []

    verdict = {
        'kind': None,
        'allowed': False,
        'blocks_max': 0,
        'reasons': reasons,
        'matched_tariffs': matched,
        'trips': None,
        'trips_basis': None,
        'min_trips': int(settings.get('min_trips') or 0),
        'cooldown_days': int(settings.get('cooldown_days') or 0),
        'last_issue': issue_brief(last),
        'welcome_issue': issue_brief(welcome),
        'next_date': None,
        'stock': stock,
        'fk': None,
    }

    if total is None:
        reasons.append('CRM не отдала число заказов водителя — право не проверить. '
                       'Попробуйте ещё раз через минуту')
        return verdict

    is_new = total == 0
    verdict['kind'] = 'welcome' if is_new else 'activity'
    blocks = int(settings.get('welcome_blocks' if is_new else 'activity_blocks') or 1)

    if not matched:
        reasons.append('Тариф не входит в программу — выдаётся на тарифах: %s'
                       % _program_label(settings))

    if is_new:
        verdict['trips'] = 0
        verdict['trips_basis'] = BASIS_NEW
        if welcome:
            reasons.append('Приветственный блок уже выдан %s%s' % (
                ru_date(welcome.get('created_at')),
                (' в офисе «%s»' % welcome['office_name']) if welcome.get('office_name') else ''))
        fk_status = str((driver.get('photo_control') or {}).get('status') or '').strip().lower()
        if fk_status == 'passed':
            verdict['fk'] = FK_PASSED
        elif fk_status:
            verdict['fk'] = FK_FAILED
            reasons.append('ФК не пройден — в CRM статус «%s»' % fk_status)
        elif fk_confirmed:
            verdict['fk'] = FK_CONFIRMED
        else:
            verdict['fk'] = FK_UNKNOWN
            if fk_confirmed is False:
                reasons.append('Подтвердите, что водитель прошёл ФК')
    else:
        state = _activity(driver, last, settings, today)
        verdict['next_date'] = state['next_date']
        if state['wait_reason']:
            reasons.append(state['wait_reason'])
        trips, basis, request = state['trips'], state['basis'], state['request']
        if request is not None and _answers(period, request):
            count = as_count(period.get('count'))
            if count is not None:
                trips, basis = max(0, count - request['subtract']), BASIS_SINCE_LAST
        verdict['trips'] = trips
        verdict['trips_basis'] = basis
        need = verdict['min_trips']
        if need:
            if trips is None:
                if request is not None:
                    reasons.append('CRM не отдала число поездок с прошлой выдачи — право не '
                                   'проверить. Попробуйте ещё раз через минуту')
                elif not state['wait_reason']:
                    reasons.append('CRM не отдала число поездок за неделю — право не проверить')
            elif trips < need:
                window = 'с прошлой выдачи' if basis == BASIS_SINCE_LAST else 'за последние 7 дней'
                reasons.append('Менее %d %s %s — сейчас %d' % (
                    need, plural(need, 'поездки', 'поездок', 'поездок'), window, trips))

    if stock is not None:
        if stock <= 0:
            reasons.append('В офисе нет воды')
        blocks = max(0, min(blocks, stock))

    verdict['blocks_max'] = blocks if not reasons else 0
    verdict['allowed'] = not reasons and blocks > 0
    return verdict


def period_request(driver, history, settings, *, today):
    """Запрос заказов CRM за даты, без которого вердикту не посчитать
    поездки с прошлой выдачи, — или None, если он не нужен.

    Сеть — забота роутов: они спрашивают CRM и передают ответ в evaluate
    (`period`), а сама проверка остаётся чистой.
    """
    history = list(history or [])
    total = (driver.get('orders') or {}).get('total')
    if not history or not total:
        return None
    return _activity(driver, history[0], settings, today)['request']


def _activity(driver, last, settings, today):
    """Выдача за активность: срок с прошлой выдачи и засчитываемые поездки.

    trips None при запросе request — поездки посчитает только CRM за даты.
    Пока не прошли дни, запроса нет: отказ ясен и без него.
    """
    orders = driver.get('orders') or {}
    week = orders.get('week')
    need = int(settings.get('min_trips') or 0)
    cooldown = int(settings.get('cooldown_days') or 0)
    state = {'next_date': None, 'wait_reason': None, 'trips': week, 'basis': BASIS_WEEK,
             'request': None}
    last_day = day_of(last.get('created_at')) if last else None
    since = moment_of(last.get('created_at')) if last else None
    if last_day is None or since is None:
        return state

    if cooldown:
        next_day = last_day + timedelta(days=cooldown)
        state['next_date'] = next_day.isoformat()
        if today < next_day:
            passed = max(0, (today - last_day).days)
            # «прошёл 1 день», но «прошло 3 дня» — глагол по числу.
            verb = 'прошёл' if plural(passed, 1, 2, 5) == 1 else 'прошло'
            state['wait_reason'] = 'С прошлой выдачи %s %s из %d — следующая с %s' % (
                verb, days_word(passed), cooldown, next_day.strftime('%d.%m.%Y'))

    window_from = moment_of(orders.get('window_from'))
    times = driver.get('order_times')
    if times is not None and window_from is not None:
        moments = [moment for moment in map(moment_of, times) if moment is not None]
        start = window_from
        if orders.get('week_truncated'):
            # Список обрезан на 500 свежих заказах — полон он только от
            # самого старого из них.
            start = min(moments) if moments else None
        if start is not None and start <= since:
            state.update(trips=sum(1 for moment in moments if moment > since),
                         basis=BASIS_SINCE_LAST)
            return state
    # Выдача раньше окна: всё окно — после неё, счётчик недели — нижняя
    # граница поездок. Хватает её — запрашивать точное число незачем.
    if (week is not None and window_from is not None and since < window_from
            and week >= need):
        return state
    if not need:
        return state
    state.update(trips=None, basis=None)
    if state['wait_reason']:
        return state

    same_account = (last.get('driver_account_id') or '') == (driver.get('account_id') or '')
    before = as_count(last.get('orders_today')) if same_account else None
    first_day = last_day if before is not None else last_day + timedelta(days=1)
    if first_day > today:
        state.update(trips=0, basis=BASIS_SINCE_LAST)
        return state
    state['request'] = {'issue_id': last.get('id'), 'date_from': first_day.isoformat(),
                        'date_to': today.isoformat(), 'subtract': before or 0}
    return state


def _answers(period, request):
    return isinstance(period, dict) and all(period.get(key) == value
                                            for key, value in request.items())


def issue_brief(item):
    if not item:
        return None
    created = item.get('created_at')
    return {
        'id': item.get('id'),
        'kind': item.get('kind'),
        'blocks': item.get('blocks'),
        'created_at': created.isoformat() if isinstance(created, (datetime, date)) else created,
        'office_name': item.get('office_name'),
        'city': item.get('city'),
    }


def stock_status(stock, low, buy):
    """Статус остатка офиса: 'buy' — требуется закупка, 'low' — низкий,
    'enough' — достаточный. «При достижении минимального остатка» (ТЗ) — то
    есть порог включительно: ровно 10 при пороге 10 — уже закупка."""
    stock = int(stock or 0)
    if stock <= int(buy or 0):
        return 'buy'
    if stock <= int(low or 0):
        return 'low'
    return 'enough'


# Офисы программы (решение владельца 01.10.2026): воду выдают только в Алматы и
# Астане и не в офисах подключения Wolt. По этому правилу справочник «Офис в
# учёт» показывает офисы, и по нему же сервер отказывает добавить другой.
PROGRAM_CITIES = ('Алматы', 'Астана')
EXCLUDED_OFFICE_MARKS = ('wolt',)
# «Убрать из селектора Астана офис для подключения Бизнес» (05.10.2026) — один
# офис справочника вики: 44, Астана, «Офис для подключения тарифа «Бизнес»»,
# проспект Сарыарка 31. По id, а не по названию: офис Алматы называется так же
# и учёт ведёт, а слово «бизнес» в названии другого офиса прятать его не должно.
# В Астане по тому же адресу в справочнике остаётся «Офис Астана».
EXCLUDED_OFFICE_IDS = (44,)


def is_program_office(office):
    """Можно ли завести офис из справочника вики в учёт воды."""
    office = office or {}
    if office.get('id') in EXCLUDED_OFFICE_IDS:
        return False
    city = str(office.get('city') or '').strip().lower()
    name = str(office.get('name') or '').lower()
    if city not in {item.lower() for item in PROGRAM_CITIES}:
        return False
    return not any(mark in name for mark in EXCLUDED_OFFICE_MARKS)
