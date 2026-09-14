"""Чистая логика успешек TEZ ОП: нормализация телефонов и расчёт статуса лида.

Модуль намеренно без БД и без сети — всё, что тут есть, это функции от данных.
Благодаря этому правила начисления успешек проверяются юнит-тестами целиком
(tests/test_tez_op_successes.py), а пересчёт истории идемпотентен: статус лида
однозначно выводится из пары «первая поездка + звонки», сколько бы раз мы его
ни считали.

Правила успешки (согласованы с владельцем, см. память tez-op-successes-project):
  1. Водитель должен был НЕ РАБОТАТЬ не менее 30 дней перед новым заказом:
     разрыв между last_order_before_at и month_first_order_at, и наш звонок
     обязан лежать МЕЖДУ этими заказами. Если заказов раньше не было вовсе —
     это новый водитель, разрыв не проверяется.
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

Правило 1 переписано 2026-08-11. Раньше успешку снимал ЛЮБОЙ заказ в прошлом
месяце, потому что TEZ APP отдавал только `previous_month_first_order_at` —
ПЕРВЫЙ заказ предыдущего месяца. По нему разрыв не считается: водитель, съездивший
2 и 25 июня и выехавший 5 июля, по первому заказу выглядит «спавшим 33 дня».
В тот же день TEZ APP заменил это поле на `last_order_before_at` (последний
завершённый заказ строго до 1-го числа запрошенного месяца), и правило стало
считаться честно. Периоды раньше RULES_EFFECTIVE_FROM по-прежнему считаются
старой веткой: июль-2026 выплачен, и пересчёт не имеет права его переписать.

Даты привязаны к месяцу базы лида, а не к водителю «за всё время»: один и тот же
номер в июньской и июльской базе имеет разные даты и считается независимо.
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

# Сколько водитель должен не работать, чтобы его возвращение считалось нашей
# заслугой. Календарные дни по Алматы: у заказов произвольное время суток, и
# сравнение по часам дало бы неразрешимые споры «29 дней 23 часа».
DEFAULT_REACTIVATION_GAP_DAYS = 30

# С какого периода действуют правила разрыва. Июль-2026 уже выплачен по старым
# (202 успешки), и пересчёт не имеет права переписать выплаченное: для периодов
# раньше этого месяца работает прежний гейт по prev_month_first_order_at.
RULES_EFFECTIVE_FROM = (2026, 8)

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
RULE_REACTIVATION = "reactivated_30d"             # водитель спал >= 30 дней и вернулся после звонка
REASON_NO_CALL_BEFORE_TRIP = "no_call_before_trip"
REASON_CALL_BEFORE_WINDOW = "call_before_last7"   # звонок раньше окна (или ещё старше)
REASON_ACTIVE_PREV_MONTH = "active_prev_month"    # были заказы в прошлом месяце
REASON_GAP_TOO_SHORT = "gap_under_30d"            # заказ был меньше 30 дней назад — не уходил
REASON_NO_CALL_AFTER_LAST_ORDER = "no_call_after_last_order"  # звонок был, но раньше последнего заказа

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


def reactivation_gap_for_period(year, month, gap_days=DEFAULT_REACTIVATION_GAP_DAYS):
    """Порог разрыва для периода либо None, если период считается по-старому.

    Отдельная функция, а не проверка внутри правила: рубеж вступления в силу —
    это решение владельца, и оно должно быть видно одним местом, а не спрятано
    в ветке расчёта.
    """
    return gap_days if (int(year), int(month)) >= RULES_EFFECTIVE_FROM else None


def compute_lead_outcome(month_first_order_at, prev_month_first_order_at, calls,
                         min_billsec=DEFAULT_MIN_BILLSEC,
                         last_order_before_at=None, reactivation_gap_days=None):
    """Считает статус лида по датам заказов и списку его звонков.

    month_first_order_at — первый заказ в отчётном месяце либо None.
    prev_month_first_order_at — первый заказ в предыдущем месяце либо None
            (поле убрано из ответа TEZ APP 11.08.2026, живёт только на закрытых
            периодах).
    calls — список dict'ов со started_at / call_type / billsec / operator_id
            (+ произвольные поля вроде general_call_id, они просто прокидываются).
    last_order_before_at — последний заказ водителя ДО начала отчётного месяца.
    reactivation_gap_days — порог разрыва в днях. None означает «период
            считается по прежним правилам» (гейт по prev_month_first_order_at):
            так закрытые месяцы не переписываются задним числом.

    ОКНО ЗВОНКА ОТ ПЕРЕНОСА ЛИДА НЕ ЗАВИСИТ (требование владельца, повторено
    несколько раз). Перенесённый лид отличается только тем, что мы продолжаем
    спрашивать, выехал ли водитель; звонок из прошлого месяца засчитывается
    ТОЛЬКО из его последних семи дней — как и у обычного лида. Звонок из
    середины прошлого месяца успешку не даёт, сколько бы месяцев лид ни жил.

    Возвращает dict: status, rule, operator_id, call, call_at, first_order_at,
    last_order_before_at, gap_days, success_date. Для не-успешек operator_id
    остаётся None — успешка без оператора невозможна по определению.
    """
    result = {
        "status": STATUS_NEW,
        "rule": None,
        "operator_id": None,
        "call": None,
        "call_at": None,
        "first_order_at": None,
        "last_order_before_at": None,
        "gap_days": None,
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
    last_at = as_almaty(last_order_before_at)
    result["last_order_before_at"] = last_at

    if reactivation_gap_days is None:
        # Прежние правила: любой заказ в прошлом месяце снимает успешку. Ветка
        # оставлена ради закрытых периодов — пересчёт июля обязан давать те же
        # 202 успешки с теми же кодами, что были выплачены.
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

    # Реактивация: заслуга есть, только если водитель действительно уходил.
    # Разрыв считаем от ПОСЛЕДНЕГО его заказа, а не от первого заказа прошлого
    # месяца: водитель, отработавший 2 и 25 июня и выехавший 5 июля, по первому
    # заказу выглядел бы «спавшим 33 дня», хотя не уходил вовсе.
    reactivated = False
    if reactivation_gap_days is not None and last_at is not None:
        gap_days = (trip_at.date() - last_at.date()).days
        result["gap_days"] = gap_days
        if gap_days < int(reactivation_gap_days):
            result["status"] = STATUS_ALREADY_WORKING
            result["rule"] = REASON_GAP_TOO_SHORT
            return result
        if last_call["started_at"] <= last_at:
            # Между последним заказом и новым нашего звонка не было — водитель
            # вернулся сам. Это не «уже работающий»: оператор по нему работал,
            # и такие случаи приходят оспаривать.
            result["status"] = STATUS_NOT_COUNTED
            result["rule"] = REASON_NO_CALL_AFTER_LAST_ORDER
            return result
        reactivated = True

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
    # Код причины различает два вида успешки: привлекли того, кто никогда не
    # работал (окно звонка), и разбудили уснувшего (разрыв в заказах). Оператору
    # при разборе спора важно именно это, а не то, в каком месяце был звонок.
    result["rule"] = RULE_REACTIVATION if reactivated else rule
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
