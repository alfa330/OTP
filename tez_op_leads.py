"""Чистая логика успешек TEZ ОП: нормализация телефонов и расчёт статуса лида.

Модуль намеренно без БД и без сети — всё, что тут есть, это функции от данных.
Благодаря этому правила начисления успешек проверяются юнит-тестами целиком
(tests/test_tez_op_successes.py), а пересчёт истории идемпотентен: статус лида
однозначно выводится из пары «первая поездка + звонки», сколько бы раз мы его
ни считали.

Правила успешки (согласованы с владельцем, см. память tez-op-successes-project):
  1. Водитель НЕ должен был выполнять заказы в прошлом месяце
     (prev_month_first_order_at пуст) — иначе он уже работал, привлечения нет.
  2. В отчётном месяце заказ есть (month_first_order_at заполнен).
  3. Квалифицирующий звонок = исходящий, отвеченный, billsec >= 10 сек,
     сделанный оператором отдела ОП (operator_id должен быть уже разрезолвен).
  4. Успешка достаётся ПОСЛЕДНЕМУ квалифицирующему звонку перед поездкой.
  5. Если месяц звонка совпадает с месяцем поездки — успешка.
  6. Если звонок был в ПРОШЛОМ месяце, засчитываем только звонки из его
     последних 7 дней; поездка при этом может быть любым днём отчётного месяца.
     Звонок раньше этого окна (или ещё более старый месяц) — «Не засчитана».
  7. Дата успешки = день поездки (Asia/Almaty), а не день обнаружения.

Правило 6 переписано 2026-08-04 по решению владельца: окно из семи дней раньше
висело на стороне ПОЕЗДКИ (успешка только если водитель выехал 1–7 числа), теперь
оно на стороне ЗВОНКА (звонок в последние 7 дней прошлого месяца). Смысл тот же —
привязать звонок к поездке «через границу месяца», но операторы больше не теряют
успешку из-за того, что водитель собрался выехать во второй половине месяца.

TEZ APP отдаёт даты ОКНОМ на два месяца: `month_first_order_at` (первый заказ в
запрошенном месяце) и `previous_month_first_order_at` (первый заказ в предыдущем).
Поэтому дата привязана к месяцу базы лида, а не к водителю «за всё время»: один и
тот же номер в июньской и июльской базе имеет разные даты и считается независимо.
"""

import re
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


# Asia/Almaty без перехода на летнее время — тот же подход, что в tez_binotel_calls.
ALMATY_TZ = ZoneInfo("Asia/Almaty") if ZoneInfo is not None else timezone(timedelta(hours=5))

# Порог длительности разговора. Владелец просил вынести в настройку: на боевой базе
# планируется померить 5/10/15 сек, пересчёт при этом локальный (Binotel не дёргаем).
DEFAULT_MIN_BILLSEC = 10

# Окно «звонок на стыке месяцев»: последние 7 дней прошлого месяца включительно.
# Считается от КОНЦА месяца (в июне это 24–30, в июле 25–31, в феврале 22–28),
# поэтому фиксированного «после 24-го» тут быть не может.
PREV_MONTH_WINDOW_DAYS = 7

# callType в ответе Binotel: 0 = входящий, 1 = исходящий.
CALL_TYPE_INCOMING = 0
CALL_TYPE_OUTGOING = 1

# Статусы лида.
STATUS_NEW = "new"                          # загружен, звонков нет, поездки нет
STATUS_IN_PROGRESS = "in_progress"          # звонки есть, поездки пока нет
STATUS_ALREADY_WORKING = "already_working"  # поездка есть, но квалифицирующего звонка до неё не было
STATUS_SUCCESS = "success"                  # успешка засчитана оператору
STATUS_NOT_COUNTED = "not_counted"          # звонок был, но правило по датам не прошло

# Причины, по которым сработало то или иное правило (пишем в детализацию,
# чтобы оператору можно было объяснить решение, а не показывать «нет успешки»).
RULE_SAME_MONTH = "same_month"                    # звонок в месяце поездки
RULE_PREV_MONTH_LAST_DAYS = "prev_month_last7"    # звонок в последние 7 дней прошлого месяца
REASON_NO_CALL_BEFORE_TRIP = "no_call_before_trip"
REASON_CALL_BEFORE_WINDOW = "call_before_last7"   # звонок раньше окна (или ещё старше)
REASON_ACTIVE_PREV_MONTH = "active_prev_month"    # были заказы в прошлом месяце

# Коды прежнего правила (окно висело на стороне поездки). Новый расчёт их не
# выдаёт, но на закрытых месяцах они остались в БД — лейблы для них живут в
# экспорте и на экране лидов, чтобы старые разборы читались.
LEGACY_RULE_PREV_MONTH_FIRST_WEEK = "prev_month_week1"
LEGACY_REASON_TRIP_TOO_LATE = "trip_after_day7"


def normalize_kz_phone(raw):
    """Приводит телефон к каноническому виду: 11 цифр, '77XXXXXXXXX'.

    Это единственный ключ, которым связываются три системы: база лидов (грузит
    СВ в произвольном формате), Binotel (`externalNumber` = '77476657568') и
    TEZ APP (нормализует к '+7747...'). Ошибка здесь означает молча потерянные
    успешки, поэтому формат проверяется строго, а всё непонятное отбрасывается.

    Возвращает строку из 11 цифр либо None, если номер невалиден.
    """
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None

    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]          # 8 701 ... -> 7 701 ...
    elif len(digits) == 10 and digits[0] == "7":
        digits = "7" + digits              # 701 234 5678 -> 7 701 234 5678

    # У всех казахстанских номеров код страны 7, дальше код оператора/города,
    # который тоже начинается с 7 (мобильные 7XX, Астана 7172 и т.д.).
    if len(digits) != 11 or not digits.startswith("77"):
        return None
    return digits


def to_e164(phone_norm):
    """'77012345678' -> '+77012345678' (формат, который ждёт TEZ APP API)."""
    return ("+" + phone_norm) if phone_norm else None


def as_almaty(value):
    """Приводит datetime к таймзоне Алматы. Наивное время считаем алматинским.

    TEZ APP отдаёт ISO со смещением +05:00, Binotel — unix-таймстемп; сравнивать
    их можно только приведя к одной зоне, иначе правило «звонок до поездки»
    начнёт врать на границах суток.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(int(value), ALMATY_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=ALMATY_TZ)
    return value.astimezone(ALMATY_TZ)


def is_qualifying_call(call, min_billsec=DEFAULT_MIN_BILLSEC):
    """Годится ли звонок как доказательство привлечения водителя.

    Ждём dict с ключами call_type / billsec / operator_id. operator_id заполняется
    снаружи и только для операторов отдела ОП: по решению владельца звонки ТП,
    линии и прочих отделов не должны перехватывать успешку.
    """
    if not call:
        return False
    if int(call.get("call_type", -1)) != CALL_TYPE_OUTGOING:
        return False
    if int(call.get("billsec") or 0) < int(min_billsec):
        return False
    return call.get("operator_id") is not None


def _month_key(value):
    return (value.year, value.month)


def _prev_month_key(value):
    """(год, месяц) месяца, предшествующего месяцу value — с переходом через год."""
    return (value.year - 1, 12) if value.month == 1 else (value.year, value.month - 1)


def _is_in_month_tail(value, days=PREV_MONTH_WINDOW_DAYS):
    """Попадает ли дата в последние `days` дней своего месяца (включительно)."""
    last_day = monthrange(value.year, value.month)[1]
    return value.day > last_day - int(days)


def call_window_for_period(year, month, days=PREV_MONTH_WINDOW_DAYS):
    """Окно дат, в котором звонок вообще может относиться к базе месяца.

    От первого дня «хвоста» прошлого месяца (его последние `days` дней) до
    последнего дня отчётного — ровно то же окно, что и в правиле успешки
    (пункты 5–6 выше). Звонок раньше него успешку дать не может.

    Единая точка правды: по этому окну и качается зеркало звонков, и считается
    «Обзвонено» в воронке, иначе карточка и правило начисления разъедутся.
    """
    year, month = int(year), int(month)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prev_last_day = monthrange(prev_year, prev_month)[1]
    start = date(prev_year, prev_month, prev_last_day - int(days) + 1)
    end = date(year, month, monthrange(year, month)[1])
    return start, end


def compute_lead_outcome(month_first_order_at, prev_month_first_order_at, calls,
                         min_billsec=DEFAULT_MIN_BILLSEC):
    """Считает статус лида по оконным датам заказов и списку его звонков.

    month_first_order_at — первый заказ в отчётном месяце либо None.
    prev_month_first_order_at — первый заказ в предыдущем месяце либо None.
    calls — список dict'ов со started_at / call_type / billsec / operator_id
            (+ произвольные поля вроде general_call_id, они просто прокидываются).

    Возвращает dict: status, rule, operator_id, call, call_at, first_order_at,
    success_date. Для не-успешек operator_id остаётся None — успешка без
    оператора невозможна по определению.
    """
    result = {
        "status": STATUS_NEW,
        "rule": None,
        "operator_id": None,
        "call": None,
        "call_at": None,
        "first_order_at": None,
        "success_date": None,
    }

    qualifying = []
    for call in calls or []:
        if not is_qualifying_call(call, min_billsec):
            continue
        started_at = as_almaty(call.get("started_at"))
        if started_at is None:
            continue
        enriched = dict(call)
        enriched["started_at"] = started_at
        qualifying.append(enriched)
    qualifying.sort(key=lambda c: c["started_at"])

    trip_at = as_almaty(month_first_order_at)
    prev_at = as_almaty(prev_month_first_order_at)

    # Водитель уже работал в прошлом месяце — привлечения не было, что бы ни
    # показывал текущий месяц. Проверяем это ДО всего остального.
    if prev_at is not None:
        result["first_order_at"] = trip_at
        result["status"] = STATUS_ALREADY_WORKING
        result["rule"] = REASON_ACTIVE_PREV_MONTH
        return result

    if trip_at is None:
        result["status"] = STATUS_IN_PROGRESS if qualifying else STATUS_NEW
        return result

    result["first_order_at"] = trip_at

    before_trip = [c for c in qualifying if c["started_at"] < trip_at]
    if not before_trip:
        # Водитель выехал сам — заказ в месяце есть, но нашего звонка до него не было.
        result["status"] = STATUS_ALREADY_WORKING
        result["rule"] = REASON_NO_CALL_BEFORE_TRIP
        return result

    last_call = before_trip[-1]
    result["call"] = last_call
    result["call_at"] = last_call["started_at"]

    # Окно проверяем по ПОСЛЕДНЕМУ звонку и только по нему: более ранние звонки
    # не могут пройти окно, если не прошёл он (внутри месяца поездки успешка
    # безусловна, а окно прошлого месяца упирается в его последний день —
    # значит «позже» всегда не хуже).
    call_month = _month_key(last_call["started_at"])
    if call_month == _month_key(trip_at):
        rule = RULE_SAME_MONTH
    elif call_month == _prev_month_key(trip_at) and _is_in_month_tail(last_call["started_at"]):
        # Звонок на стыке месяцев: день поездки внутри отчётного месяца не важен.
        rule = RULE_PREV_MONTH_LAST_DAYS
    else:
        # Оператор работал, но звонок вне окна — это НЕ «уже работающий»,
        # и смешивать их нельзя: именно такие случаи операторы оспаривают.
        result["status"] = STATUS_NOT_COUNTED
        result["rule"] = REASON_CALL_BEFORE_WINDOW
        return result

    result["status"] = STATUS_SUCCESS
    result["rule"] = rule
    result["operator_id"] = last_call.get("operator_id")
    result["success_date"] = trip_at.date()
    return result


def parse_first_order_at(value):
    """ISO-строка из TEZ APP ('2026-03-14T10:35:21+05:00') -> datetime в Алматы."""
    if not value:
        return None
    if isinstance(value, datetime):
        return as_almaty(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return as_almaty(datetime.fromisoformat(text))
    except ValueError:
        return None
