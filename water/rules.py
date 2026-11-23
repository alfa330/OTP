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
    засчитать поездки, сделанные до выдачи (и в отказе так и пишем).

Время заказа — `booked_at`: по нему CRM задаёт и окно «7 дней», и
`orders.today` (проверено 21.10.2026 на 4782 заказах из 48 снимков выдач).
Счёт по списку сравнивает время заказа с моментом выдачи, и задержка CRM ему
не мешает. Счёт за даты вычитает `orders.today` снимка, а о части заказов CRM
узнаёт с опозданием: в 4 выдачах из 18 она позже узнала ещё об одном заказе,
сделанном до выдачи, и тогда за даты выходит на поездку больше. Точнее по
данным CRM не посчитать; путь редкий — выдача больше недели назад и меньше
20 поездок за последнюю неделю.

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
        _check_welcome(driver, welcome, verdict, reasons, fk_confirmed)
    else:
        state = _activity(driver, last, settings, today)
        verdict['next_date'] = state['next_date']
        if state['wait_reason']:
            reasons.append(state['wait_reason'])
        trips, basis, request = state['trips'], state['basis'], state['request']
        partial = False
        if request is not None and answers(period, request):
            count = as_count(period.get('count'))
            # Заказов за даты меньше, чем было до выдачи по её же снимку, —
            # данные CRM противоречат друг другу: такое число не засчитываем.
            if count is not None and count >= request['subtract']:
                trips, basis = count - request['subtract'], BASIS_SINCE_LAST
                partial = request['partial']
        verdict['trips'] = trips
        verdict['trips_basis'] = basis
        need = verdict['min_trips']
        if need:
            if trips is None:
                if state['wait_reason']:
                    pass  # отказ по дням ясен и без числа поездок
                elif last:
                    reasons.append('CRM не отдала число поездок с прошлой выдачи — право не '
                                   'проверить. Попробуйте ещё раз через минуту')
                else:
                    reasons.append('CRM не отдала число поездок за неделю — право не проверить')
            elif trips < need:
                window = 'с прошлой выдачи' if basis == BASIS_SINCE_LAST else 'за последние 7 дней'
                reasons.append('Менее %d %s %s — сейчас %d%s' % (
                    need, plural(need, 'поездки', 'поездок', 'поездок'), window, trips,
                    # Число без дня выдачи — нижняя граница, а не точный счёт.
                    ', не считая дня прошлой выдачи' if partial else ''))

    if stock is not None:
        if stock <= 0:
            reasons.append('В офисе нет воды')
        blocks = max(0, min(blocks, stock))

    verdict['blocks_max'] = blocks if not reasons else 0
    verdict['allowed'] = not reasons and blocks > 0
    return verdict


def _check_welcome(driver, welcome, verdict, reasons, fk_confirmed):
    """Условия приветственного блока, кроме «ни одного заказа»: блок один на
    человека, ФК пройден. Общие для выдачи у стойки и выдачи задним числом —
    второй копии этих условий быть не должно."""
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


def _orders_list(driver):
    """Список заказов CRM «7 дней» и с какого момента он полон.

    `start` — начало окна списка (у обрезанного на 500 заказах — самый старый
    из них): заказы не раньше него в списке все. `complete` — в списке вообще
    все заказы аккаунта: первый заказ CRM (`first_order_at`) не раньше начала
    списка, то есть до окна заказов не было (новый водитель). Нет списка
    (старый ответ CRM) — None: «нет данных», а не «ноль заказов».
    """
    times = driver.get('order_times')
    if times is None:
        return None
    orders = driver.get('orders') or {}
    moments = [item for item in map(moment_of, times) if item is not None]
    truncated = bool(orders.get('week_truncated'))
    start = (min(moments) if moments else None) if truncated else moment_of(orders.get('window_from'))
    first = moment_of(orders.get('first_order_at'))
    complete = bool(not truncated and start is not None and first is not None and first >= start)
    return {'moments': moments, 'start': start, 'complete': complete}


def _covers(listing, since):
    """Все ли заказы начиная с `since` есть в списке."""
    if listing is None:
        return False
    return listing['complete'] or (listing['start'] is not None and listing['start'] <= since)


def _kind_verdict(kind, driver, settings, reasons):
    program = [str(code) for code in (settings.get('tariffs') or [])]
    tariffs = [str(code) for code in (driver.get('tariffs') or [])]
    matched = [code for code in tariffs if code in program]
    if not matched:
        reasons.append('Тариф не входит в программу — выдаётся на тарифах: %s'
                       % _program_label(settings))
    return {
        'kind': kind,
        'allowed': False,
        'blocks_max': 0,
        'reasons': reasons,
        'matched_tariffs': matched,
        'trips': None,
        'trips_basis': None,
        'fk': None,
    }


def backfill_verdict(driver, history, settings, *, kind='welcome', moment=None, prev=None,
                     prev_account=None, fk_confirmed=None, period=None):
    """Выдача задним числом: можно ли её внести.

    Владелец 23.11.2026: супер-админ вносит блок задним числом, «если офисник
    забыл нажать кнопку, а водитель уже выполняет заказы»; в тот же день —
    «да, сделай» и для блока за активность. Хватало ли воды в офисе на тот
    момент, проверяет роут по истории офиса — от неё и считается остаток.

    ПРИВЕТСТВЕННЫЙ. У стойки такого водителя система считает действующим
    (заказов больше нуля), и приветственный ему не выдать. Здесь это условие
    снято — и только оно: заказы водитель делал уже после выдачи. Остальное —
    как у стойки: тариф программы, один блок на человека, ФК. И выдачи на этом
    же аккаунте раньше момента быть не должно: тогда аккаунт уже не был новым.
    Выдача на ДРУГОМ аккаунте того же человека не мешает — в новом парке у
    водителя новый аккаунт без заказов, и приветственный ему у стойки положен.

    ЗА АКТИВНОСТЬ. Те же условия, что у стойки, — на момент выдачи: тариф
    программы, не меньше `cooldown_days` календарных дней с прошлой выдачи
    человека до этой и `min_trips` поездок — с прошлой выдачи до этой, а без
    прошлой — за 7 дней до неё. Поездки — по времени заказов из списка CRM, а
    чего в списке нет — запросом CRM за даты (backfill_period_request, `period`).
    Отказ — только доказанный: точное число меньше порога или «не больше N»
    меньше порога. Где доказать нечем, «нет данных» запретом не считается —
    экран показывает «не меньше N» (`trips_exact` = False).

    history — живые выдачи человека (по аккаунту или ИИН), новые первыми.
    moment  — когда выдали; None — время ещё не выбрано (предпросмотр): у
              блока за активность тогда нет ни дней, ни поездок (`needs_moment`).
    prev    — последняя живая выдача человека до момента (queries.issues_before);
    prev_account — она же, но только на этом аккаунте.
    fk_confirmed — как в evaluate: None — предпросмотр, False на записи — отказ.
    period  — ответ CRM на backfill_period_request (только блоку за активность).
    """
    history = list(history or [])
    reasons = []
    verdict = _kind_verdict(kind, driver, settings, reasons)
    when = moment_of(moment)
    if kind == 'activity':
        verdict.update(_backfill_activity(driver, settings, when, prev, reasons, period=period))
        blocks = int(settings.get('activity_blocks') or 1)
    else:
        welcome = next((item for item in history if item.get('kind') == 'welcome'), None)
        verdict.update(trips=0, trips_basis=BASIS_NEW, welcome_issue=issue_brief(welcome))
        _check_welcome(driver, welcome, verdict, reasons, fk_confirmed)
        if when is not None and not welcome and prev_account:
            reasons.append('На этом аккаунте вода уже выдавалась %s — приветственный блок может '
                           'быть только раньше первой выдачи' % ru_date(prev_account.get('created_at')))
        blocks = int(settings.get('welcome_blocks') or 1)
    ready = when is not None or kind != 'activity'
    verdict['blocks_max'] = blocks if ready and not reasons else 0
    verdict['allowed'] = ready and not reasons
    return verdict


def _list_trips(listing, since, frm, when):
    """Поездки по списку CRM: с прошлой выдачи (since) или с начала недели (frm)
    до момента. None — списка нет. Точное ли это число, решает _covers."""
    if listing is None:
        return None
    if since is not None:
        return sum(1 for item in listing['moments'] if since < item < when)
    return sum(1 for item in listing['moments'] if frm <= item < when)


def backfill_period_request(driver, settings, *, moment, prev):
    """Запрос CRM «заказы за даты» для поездок блока за активность задним
    числом — или None, если он ничего не даст.

    Блок за активность выдают не чаще раза в `cooldown_days` дней, а список
    CRM — это последние 7 дней. Поэтому отрезок «с прошлой выдачи до этой»
    почти никогда не лежит в списке целиком. Его досчитывает CRM за календарные
    даты, как у стойки (_activity / period_request): со дня прошлой выдачи по
    день этой, минус заказы дня прошлой ДО неё (`orders.today` её снимка) и
    заказы дня этой ПОСЛЕ неё (по списку — если он доходит до момента).
    `bound` — что даст ответ: 'exact' — точное число; 'lower' — не меньше
    (чего-то из двух вычетов не знаем — день отбрасывается целиком); 'upper' —
    не больше (без прошлой выдачи: «7 суток до выдачи» по датам не отрезать,
    первый день идёт целиком).

    В отличие от стойки, точное число спрашивается и тогда, когда порог уже
    набран по списку: внос задним числом редок, а в колонке «Заказы» журнала
    должно стоять число, а не «—». Не спрашивается, когда ответ ничего не
    добавит: список и так точен; «не меньше» по CRM при пороге, уже набранном
    списком; «не больше» без порога или при пороге, уже набранном списком.
    """
    when = moment_of(moment)
    if when is None:
        return None
    need = int(settings.get('min_trips') or 0)
    listing = _orders_list(driver)
    since = moment_of(prev.get('created_at')) if prev else None
    if prev and since is None:
        return None
    frm = since if since is not None else when - timedelta(days=7)
    if _covers(listing, frm):
        return None
    lower = _list_trips(listing, since, frm, when)
    proven = bool(need and lower is not None and lower >= need)
    after = (sum(1 for item in listing['moments'] if item >= when and item.date() == when.date())
             if _covers(listing, when) else None)
    if since is not None:
        same = (prev.get('driver_account_id') or '') == (driver.get('account_id') or '')
        before = as_count(prev.get('orders_today')) if same else None
        first_day = since.date() if before is not None else since.date() + timedelta(days=1)
        bound = 'exact' if before is not None and after is not None else 'lower'
        if bound == 'lower' and proven:
            return None
    else:
        # Без прошлой выдачи нужен конец ровно в момент: иначе ни «не больше», ни «не меньше».
        if after is None or not need or proven:
            return None
        before = None
        first_day = frm.date()
        bound = 'upper'
    last_day = when.date() if after is not None else when.date() - timedelta(days=1)
    if first_day > last_day:
        return None
    return {'issue_id': prev.get('id') if prev else None, 'date_from': first_day.isoformat(),
            'date_to': last_day.isoformat(), 'subtract': (before or 0) + (after or 0), 'bound': bound}


def _backfill_activity(driver, settings, when, prev, reasons, period=None):
    """Блок за активность на момент `when` — дни и поездки (см. backfill_verdict).

    period — ответ CRM на backfill_period_request (тот же словарь плюс `count`,
    None — CRM не ответила); ответ на другой запрос не годится."""
    need = int(settings.get('min_trips') or 0)
    cooldown = int(settings.get('cooldown_days') or 0)
    state = {'last_issue': issue_brief(prev), 'needs_moment': when is None, 'min_trips': need,
             'cooldown_days': cooldown, 'trips_exact': None, 'trips_from': None, 'trips_upper': None}
    if when is None:
        return state
    since = moment_of(prev.get('created_at')) if prev else None
    if since is not None and cooldown:
        passed = (when.date() - since.date()).days
        if passed < cooldown:
            verb = 'прошёл' if plural(passed, 1, 2, 5) == 1 else 'прошло'
            reasons.append('К этой выдаче с прошлой (%s) %s %s из %d' % (
                ru_date(since), verb, days_word(max(0, passed)), cooldown))
    frm = since if since is not None else when - timedelta(days=7)
    where = 'с прошлой выдачи до этой' if since is not None else 'за 7 дней до этой выдачи'
    state['trips_basis'] = BASIS_SINCE_LAST if since is not None else BASIS_WEEK
    listing = _orders_list(driver)
    trips = _list_trips(listing, since, frm, when)
    exact = _covers(listing, frm)
    upper = None
    request = None if exact else backfill_period_request(
        driver, settings, moment=when, prev=prev)
    if request and answers(period, request):
        count = as_count(period.get('count'))
        value = count - request['subtract'] if count is not None else None
        # Заказов за даты меньше, чем вычитаем, — данные CRM противоречат друг
        # другу: такое число не берём (как у стойки).
        if value is not None and value >= 0:
            if request['bound'] == 'exact':
                trips, exact = value, True
            elif request['bound'] == 'lower':
                trips = max(trips or 0, value)
            else:
                upper = value
    state.update(trips=trips, trips_exact=exact, trips_upper=upper)
    if not exact and listing is not None and listing['start'] is not None:
        state['trips_from'] = listing['start'].isoformat()
    if not need:
        return state
    if exact and trips is not None and trips < need:
        reasons.append('Менее %d %s %s — было %d' % (
            need, plural(need, 'поездки', 'поездок', 'поездок'), where, trips))
    elif upper is not None and upper < need:
        reasons.append('Менее %d %s %s — было не больше %d' % (
            need, plural(need, 'поездки', 'поездок', 'поездок'), where, upper))
    return state


def orders_at(driver, moment):
    """Заказы водителя на момент выдачи задним числом — из списка CRM «7 дней».

    Выдача хранит снимок CRM, и следующая выдача берёт из него `orders.today`
    — сколько заказов водитель сделал в день выдачи ДО неё (_activity). У
    выдачи задним числом снимок снят позже, и его `today` — заказы другого дня.
    Поэтому день восстанавливается по времени заказов: (`today`, `total`) на
    тот момент. Где список не покрывает нужного отрезка (выдача раньше окна
    CRM или список обрезан), число не выдумывается — None, «нет данных»:
    следующая выдача тогда день прошлой не считает вовсе.
    """
    when = moment_of(moment)
    listing = _orders_list(driver)
    if when is None or listing is None:
        return None, None
    moments = listing['moments']
    day_start = datetime(when.year, when.month, when.day)
    today = (sum(1 for item in moments if day_start <= item < when)
             if _covers(listing, day_start) else None)
    total = (driver.get('orders') or {}).get('total')
    total = (max(0, int(total) - sum(1 for item in moments if item >= when))
             if total is not None and _covers(listing, when) else None)
    return today, total


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
        # Выдача сегодня, а заказов этого дня до неё не знаем (другой аккаунт,
        # снимок без today): счесть нечем — «нет данных», а не выдуманный ноль.
        return state
    state['request'] = {'issue_id': last.get('id'), 'date_from': first_day.isoformat(),
                        'date_to': today.isoformat(), 'subtract': before or 0,
                        'partial': before is None}
    return state


def answers(period, request):
    """Ответ CRM за даты годится, только если он на этот самый запрос."""
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
