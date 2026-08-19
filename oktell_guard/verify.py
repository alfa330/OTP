"""Сверка присланных выбросов с историей статусов самого Oktell.

Зачем. Программа стоит на компьютере сотрудника, поэтому сказать она может что
угодно: приписать коллеге двадцать выбросов или, наоборот, доложить, что всё в
порядке, когда её сняли. Общий пароль от этого не защищает — он лежит внутри
файла, который скачивает каждый.

Поэтому единственный источник правды — сама АТС. Каждый присланный факт
проверяется по `A_UserStateHistory`: действительно ли этот оператор в это время
просидел в «Перезвоне» столько, сколько заявлено. Не подтвердилось — в отчёт не
попадает.

Модуль намеренно не знает ни про Flask, ни про наш Postgres: на вход ему дают
функцию запроса к Oktell, на выход он отдаёт вердикт. Так его можно проверять
тестами, не поднимая ни базы, ни сети.
"""

from datetime import datetime, timedelta, timezone

RECALL_STATE = 2      # перерыв
RECALL_ICODE = 2      # подпричина «Перезвон»

CONFIRMED = 'confirmed'
REJECTED = 'rejected'
PENDING = 'pending'

# Часы оператора, сервера и АТС не совпадают до секунды, поэтому у окна поиска
# есть допуск. Полторы минуты — заметно меньше самого порога (три минуты), то
# есть на подделку это запаса не даёт.
CLOCK_SKEW_S = 90

# Заявленное время не обязано совпадать с историей секунда в секунду: агент
# считает по своим часам и округляет. Но «просидел 10 минут» при фактических
# трёх — это уже не погрешность.
DURATION_TOLERANCE = 0.5


LOCAL_TZ_NAME = 'Asia/Almaty'
LOCAL_UTC_OFFSET_HOURS = 5   # запасной вариант, если база часовых поясов недоступна


def to_local(moment: datetime) -> datetime:
    """Момент по Гринвичу → местное время без метки пояса.

    Браузер записывает время как `new Date().toISOString()`, то есть по
    Гринвичу, а история Oktell и наши таблицы живут по Алматы. Без перевода
    сверка искала событие на пять часов раньше и отклоняла КАЖДЫЙ настоящий
    выброс со словами «в это время в Перезвоне не был».
    """
    try:
        from zoneinfo import ZoneInfo

        return moment.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(LOCAL_TZ_NAME)).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 — на машине может не быть базы поясов
        return moment + timedelta(hours=LOCAL_UTC_OFFSET_HOURS)


def _parse_time(value):
    """Разобрать время события. Метка `Z` означает Гринвич — переводим в местное."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else to_local(
            value.astimezone(timezone.utc).replace(tzinfo=None))
    raw = str(value or '').strip()
    is_utc = raw.endswith('Z') or raw.endswith('+00:00')
    text = raw.replace('T', ' ').replace('Z', '').replace('+00:00', '').strip()
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            parsed = datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
        return to_local(parsed) if is_utc else parsed
    return None


def build_history_sql(sip_number: str, moment: datetime, window_s: int) -> str:
    """Запрос к Oktell: состояния оператора вокруг заявленного момента.

    Только SELECT — доступ к базе Oktell у нас на чтение, и это правильно:
    ограничитель не должен уметь править историю, по которой сам же проверяется.
    """
    safe_sip = str(sip_number or '').replace("'", "''")
    since = (moment - timedelta(seconds=window_s + CLOCK_SKEW_S)).strftime('%Y%m%d %H:%M:%S')
    # Строки берём с запасом вперёд: расхождение часов может сдвинуть последнюю
    # запись истории. На подсчёт это не влияет — он всё равно обрывается на моменте.
    until = (moment + timedelta(seconds=CLOCK_SKEW_S)).strftime('%Y%m%d %H:%M:%S')
    return (
        "SELECT TOP 50 h.State, h.ICode, "
        "CONVERT(varchar(19), h.TimeChange, 120) AS time_change "
        "FROM oktell.dbo.A_UserStateHistory h "
        "JOIN oktell_settings.dbo.A_Users u ON u.ID = h.UserId "
        f"WHERE u.Login = '{safe_sip}' "
        f"AND h.TimeChange BETWEEN '{since}' AND '{until}' "
        "ORDER BY h.Enumerator"
    )


def recall_seconds_in_window(rows, moment: datetime, window_s: int) -> int:
    """Сколько секунд оператор был в «Перезвоне» в окне до заявленного момента.

    Считаем по переходам: строка истории — это смена состояния, поэтому
    «Перезвон» длится от своей строки до следующей.
    """
    start = moment - timedelta(seconds=window_s + CLOCK_SKEW_S)
    # Считаем строго ДО момента выброса: сам выброс «Перезвон» и прекращает.
    # Если тянуть окно после него, засчитается время, которого не было, и
    # подтвердится нарушение, не дотянувшее до порога.
    end = moment

    events = []
    for row in rows or []:
        changed = _parse_time(row.get('time_change') if isinstance(row, dict) else None)
        if not changed:
            continue
        is_recall = int(row.get('State') or 0) == RECALL_STATE and int(row.get('ICode') or -1) == RECALL_ICODE
        events.append((changed, is_recall))
    if not events:
        return 0
    events.sort(key=lambda item: item[0])

    total = 0.0
    for index, (changed, is_recall) in enumerate(events):
        if not is_recall:
            continue
        finish = events[index + 1][0] if index + 1 < len(events) else end
        segment_start = max(changed, start)
        segment_end = min(finish, end)
        if segment_end > segment_start:
            total += (segment_end - segment_start).total_seconds()
    return int(total)


def verdict(violation: dict, rows, threshold_s: int = None):
    """Подтверждать ли выброс.

    Возвращает (статус, пояснение). Пояснение хранится рядом с записью, чтобы
    «почему не засчитали» не приходилось выяснять расследованием.
    """
    moment = _parse_time(violation.get('happened_at') or violation.get('at'))
    if not moment:
        return PENDING, 'в присланном факте нет разбираемого времени'

    claimed = int(violation.get('seconds') or 0)
    threshold = int(threshold_s or violation.get('threshold_s') or claimed or 0)
    if threshold <= 0:
        return PENDING, 'неизвестен порог, не с чем сравнивать'

    actual = recall_seconds_in_window(rows, moment, max(claimed, threshold))
    if actual <= 0:
        return REJECTED, 'по истории Oktell оператор в это время в «Перезвоне» не был'
    if actual + CLOCK_SKEW_S < threshold:
        return REJECTED, f'по истории Oktell в «Перезвоне» {actual} с, порог {threshold} с'
    if claimed and actual < claimed * DURATION_TOLERANCE:
        return REJECTED, f'заявлено {claimed} с, по истории Oktell {actual} с'
    return CONFIRMED, f'подтверждено историей Oktell: {actual} с'
