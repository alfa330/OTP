"""Серверная сверка: кто пересидел в «Перезвоне», когда правило до него не дошло.

Зачем это отдельно от агента. Правило живёт в окне браузера на машине человека,
и мимо него есть законные пути: открыть Oktell в своём браузере, закрыть наше
окно, снять программу, или — как выяснилось 04.09.2026 — получить в странице
слепое правило, которое стоит и не считает. Во всех этих случаях агент молчит
или бодро рапортует, а человек сидит.

Единственный источник, который нельзя обойти с рабочего места, — история
статусов самой АТС. Поэтому сверка смотрит не на агентов, а на РЕЗУЛЬТАТ:
Oktell говорит, что человек просидел в «Перезвоне» дольше порога, а в нашем
журнале выброса за это время нет — значит ограничитель до него не доехал.
Такая формулировка не зависит от причины и не требует хранить историю живости
агентов: она измеряет ровно то, ради чего всё затевалось.

Модуль намеренно не знает ни про Flask, ни про наш Postgres, ни про Oktell:
на вход ему дают строки истории, на выход он отдаёт отрезки. Так его можно
проверять тестами, не поднимая ни базы, ни сети.
"""

from datetime import datetime, timedelta

RECALL_STATE = 2      # перерыв
RECALL_ICODE = 2      # подпричина «Перезвон»

REASON_UNMANAGED = 'recall_unmanaged'

# Насколько раньше выброса от агента может начаться отрезок, чтобы считать их
# одним и тем же событием. Часы машины оператора и АТС расходятся, а агент ещё
# и округляет — полторы минуты те же, что и в verify.py.
MATCH_SKEW_S = 90


def build_history_sql(since: datetime, until: datetime, cursor_enum: int = 0, limit: int = 1000) -> str:
    """История статусов всех операторов за период, страницами по Enumerator.

    Только SELECT: доступ к базе Oktell у нас на чтение, и это правильно —
    ограничитель не должен уметь править историю, по которой сам проверяется.
    Прокси режет ответ на 1000 строк, поэтому листаем keyset'ом по Enumerator,
    а не OFFSET'ом: за смену строк тысячи, и OFFSET на них разъезжается.
    """
    return (
        f"SELECT TOP {int(limit)} u.Login AS login, h.State AS state, h.ICode AS icode, "
        "CONVERT(varchar(19), h.TimeChange, 120) AS time_change, "
        "h.Enumerator AS enumerator "
        "FROM oktell.dbo.A_UserStateHistory h "
        "JOIN oktell_settings.dbo.A_Users u ON u.ID = h.UserId "
        f"WHERE h.TimeChange >= '{since.strftime('%Y%m%d %H:%M:%S')}' "
        f"AND h.TimeChange < '{until.strftime('%Y%m%d %H:%M:%S')}' "
        f"AND h.Enumerator > {int(cursor_enum)} "
        "ORDER BY h.Enumerator"
    )


def _parse(value):
    text = str(value or '').strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def recall_segments(rows, until: datetime = None):
    """Отрезки «Перезвона» по логинам: [{'login', 'start', 'end', 'seconds'}].

    Строка истории — это СМЕНА состояния, поэтому «Перезвон» длится от своей
    строки до следующей строки того же человека. Последний отрезок не закрыт:
    человек в нём сидит прямо сейчас. Незакрытые не отдаём — иначе один и тот
    же сидящий попадал бы в отчёт на каждом прогоне со всё большим числом
    секунд. Он закроется сам, и тогда его посчитают ровно один раз.
    """
    by_login = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        login = str(row.get('login') or '').strip()
        moment = _parse(row.get('time_change'))
        if not login or not moment:
            continue
        try:
            state = int(row.get('state'))
            icode = int(row.get('icode'))
        except (TypeError, ValueError):
            continue
        order = row.get('enumerator')
        by_login.setdefault(login, []).append(
            (int(order) if str(order).lstrip('-').isdigit() else 0, moment, state, icode)
        )

    out = []
    for login, items in by_login.items():
        items.sort(key=lambda item: (item[0], item[1]))
        for index, (_order, moment, state, icode) in enumerate(items):
            if state != RECALL_STATE or icode != RECALL_ICODE:
                continue
            if index + 1 >= len(items):
                continue          # отрезок ещё не закрыт
            finish = items[index + 1][1]
            seconds = int((finish - moment).total_seconds())
            if seconds <= 0:
                continue
            out.append({'login': login, 'start': moment, 'end': finish, 'seconds': seconds})
    out.sort(key=lambda item: (item['start'], item['login']))
    return out


def overdue(segments, threshold_for):
    """Отрезки, дотянувшие до порога. threshold_for(login) -> секунды или None.

    None означает «этого человека правило не касается» (нет в отделе, выключен,
    нет SIP-номера) — такие отрезки не наши.
    """
    result = []
    for segment in segments or []:
        threshold = threshold_for(segment['login'])
        if not threshold or int(threshold) <= 0:
            continue
        if segment['seconds'] >= int(threshold):
            result.append(dict(segment, threshold_s=int(threshold)))
    return result


def already_known(segment, violations) -> bool:
    """Есть ли уже выброс за этот отрезок — от агента или от прошлой сверки.

    Сравниваем по окну самого отрезка с допуском на расхождение часов: агент
    записывает МОМЕНТ выброса (то есть конец счёта), а не начало отрезка,
    поэтому точного совпадения времени тут не бывает никогда.
    """
    start = segment['start'] - timedelta(seconds=MATCH_SKEW_S)
    end = segment['end'] + timedelta(seconds=MATCH_SKEW_S)
    for item in violations or []:
        login = str(item.get('sip_number') or '').strip()
        if login and login != str(segment['login']):
            continue
        moment = item.get('happened_at')
        if isinstance(moment, str):
            moment = _parse(moment)
        if not moment:
            continue
        if start <= moment <= end:
            return True
    return False


def client_key(segment) -> str:
    """Ключ идемпотентности сверки: логин + начало отрезка.

    Начало, а не конец: конец у одного и того же сидения может уехать на
    секунду между прогонами, и тогда в отчёте появился бы дубль.
    """
    return f"oktell|{segment['login']}|{segment['start'].strftime('%Y-%m-%dT%H:%M:%S')}"


def note(segment) -> str:
    return (
        f"по истории Oktell {segment['seconds']} с в «Перезвоне» "
        f"с {segment['start'].strftime('%H:%M:%S')}, порог {segment.get('threshold_s')} с; "
        "выброса от программы за это время нет — ограничитель до человека не доехал"
    )
