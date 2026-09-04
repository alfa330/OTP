"""Прогон серверной сверки: сложить историю Oktell с нашим журналом выбросов.

Чистая логика лежит в sweep.py и проверяется тестами без базы и сети. Здесь —
только связывание: сходить в Oktell, сходить в наш Postgres, записать найденное.

Что именно записывается. Отрезок «Перезвона» из истории АТС, который дотянул до
порога, но которому не соответствует ни один выброс от программы. Причина у
такого отрезка может быть любая — человек открыл Oktell в своём браузере, снял
программу, закрыл наше окно, или правило в окне оказалось слепым, — и сверке
она не важна: она фиксирует результат, а не способ.

Такие записи идут с reason='recall_unmanaged' и verified='confirmed': источник
у них — сама АТС, то есть ровно то, чем проверяются факты от программы.
Перепроверять историю по истории незачем.
"""

import logging
from datetime import datetime, timedelta

from . import queries, sweep

# Прокси Oktell отдаёт не больше 1000 строк на запрос. Листаем keyset'ом,
# но не бесконечно: страховка от запроса, который случайно охватил месяц.
MAX_PAGES = 40


def fetch_history(oktell_query, since: datetime, until: datetime) -> list:
    """Все смены статусов за период, страницами по Enumerator."""
    rows: list = []
    cursor_enum = 0
    for _ in range(MAX_PAGES):
        page = oktell_query(sweep.build_history_sql(since, until, cursor_enum))
        if not page:
            break
        rows.extend(page)
        last = page[-1].get('enumerator')
        try:
            cursor_enum = int(last)
        except (TypeError, ValueError):
            break
        if len(page) < 1000:
            break
    else:
        logging.warning(
            "Ограничитель Перезвона: история Oktell не дочитана за %s страниц — "
            "часть периода в сверку не попала", MAX_PAGES
        )
    return rows


def run_patrol(db, oktell_query, since: datetime, until: datetime,
               department_code=None, dry_run: bool = False) -> dict:
    """Один прогон сверки. Возвращает сводку для лога.

    dry_run здесь означает «посчитай и покажи, но в журнал не пиши» — это для
    ручной проверки руками, а не режим обкатки самого ограничителя.
    """
    rows = fetch_history(oktell_query, since, until)
    segments = sweep.recall_segments(rows)

    with db._get_cursor() as cursor:
        people, ambiguous = queries.thresholds_by_sip(cursor, department_code)
        known = queries.violations_between(cursor, since - timedelta(minutes=5),
                                           until + timedelta(minutes=5))
    if ambiguous:
        # Молчать нельзя: эти люди выпадают из сверки целиком, и без строки в
        # логе «у нас всё чисто» означало бы «мы их просто не смотрели».
        logging.warning(
            "Ограничитель Перезвона: SIP-номера на нескольких действующих сотрудниках "
            "(%s) — по ним сверка не считает, чей это выброс. Чинить в справочнике.",
            ", ".join(f"{sip}: {ids}" for sip, ids in sorted(ambiguous.items())),
        )

    def threshold_for(login):
        person = people.get(str(login))
        return person['threshold_s'] if person else None

    candidates = sweep.overdue(segments, threshold_for)
    missed = [item for item in candidates if not sweep.already_known(item, known)]

    saved = 0
    if not dry_run and missed:
        with db._get_cursor() as cursor:
            for item in missed:
                person = people.get(str(item['login'])) or {}
                created = queries.record_violation(cursor, {
                    'user_id': person.get('user_id'),
                    'sip_number': str(item['login'])[:64],
                    # Момент выброса — когда порог был пройден, а не когда
                    # человек встал: так запись сопоставима с фактами от
                    # программы, которые пишутся ровно в этот момент.
                    'happened_at': item['start'] + timedelta(seconds=item['threshold_s']),
                    'seconds': item['seconds'],
                    'threshold_s': item['threshold_s'],
                    'reason': sweep.REASON_UNMANAGED,
                    'hostname': '', 'windows_user': '', 'agent_version': '',
                    'dry_run': False,
                    'client_key': sweep.client_key(item),
                    'verified': 'confirmed',
                    'verified_note': sweep.note(item),
                    'reported_by': None,
                })
                saved += 1 if created else 0

    summary = {
        'rows': len(rows),
        'segments': len(segments),
        'people': len(people),
        'ambiguous_sip': sorted(ambiguous),
        'overdue': len(candidates),
        'missed': len(missed),
        'saved': saved,
        'dry_run': bool(dry_run),
    }
    logging.info(
        "Ограничитель Перезвона, сверка %s–%s: строк %s, отрезков «Перезвона» %s, "
        "с превышением %s, без выброса от программы %s, записано %s",
        since.strftime('%d.%m %H:%M'), until.strftime('%d.%m %H:%M'),
        summary['rows'], summary['segments'], summary['overdue'],
        summary['missed'], summary['saved'],
    )
    return summary
