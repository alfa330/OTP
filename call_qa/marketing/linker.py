# -*- coding: utf-8 -*-
"""Связать субъект оценки со сделкой amoCRM. Заполняет `qa_subject_deals`.

Единственный мост между разговором и сделкой — ТЕЛЕФОН, и он не однозначный:
один водитель оставляет заявку не раз (у 20% сделок «Основы» номер повторяется).
Поэтому правило не «совпал номер», а «совпал номер И разговор попал в окно этой
сделки». Окна стыкуются встык и не перекрываются, так что один звонок никогда не
достаётся двум сделкам.

Правило окон НЕ пишется здесь заново: оно живёт в `cdr.leads.assign_touches` и с
июня 2026 обкатано на «Касаниях» — вместе с допуском на опережение (карточку в
CRM часто заводят уже ПОСЛЕ начала разговора, медиана 10 секунд) и с защитой
«допуск не отбирает у предыдущей сделки её собственный звонок». Своя копия этих
правил разошлась бы с «Касаниями», и два раздела портала отвечали бы по-разному
на один вопрос «чей это звонок».

Что здесь своё
--------------
Только подготовка данных: достать субъекты пяти разных видов с телефоном и
моментом разговора, достать сделки-кандидаты и записать результат. Время везде
приводится к Алматы, потому что `op_funnel_leads` хранит местное время
источника, а `calls.created_at` — UTC; сравнивать их напрямую значит промахнуться
на пять часов, то есть на полсмены.
"""

import json
import logging
from datetime import datetime, timedelta

from cdr import leads as lead_rules
from cdr.touches import norm_phone

from . import brands

log = logging.getLogger(__name__)

# Насколько раньше разговора может быть заведена сделка, чтобы считаться его
# сделкой. Берём у «Касаний»: там этот запас выведен на живых данных.
GRACE = lead_rules.GRACE

# Сколько суток сделок поднимать вокруг разговоров. Заявку оставляют раньше, чем
# по ней звонят, а перезванивают и через неделю; 45 суток закрывают хвост и не
# тянут в память весь год.
LOOKBACK_DAYS = 45

# Редакция правил связки. Связь, посчитанная прежней редакцией, пересчитывается
# сама при ближайшем прогоне (см. _fetch_subjects), поэтому исправление правила
# доезжает и до уже связанных разговоров, без ручного «пересчитать всё».
#   1 — первая редакция (22.09.2026);
#   2 — момент звонка из АТС и загруженного звонка без сдвига на +5 часов.
LINK_RULES_VERSION = 2

# Момент разговора у каждого вида субъекта, уже в Алматы.
#
# Хранят его по-разному, и перевод у каждого свой:
#   * calls.appeal_date — стенные часы Алматы (дата обращения из журнала):
#     переводить нечего;
#   * calls.created_at — naive UTC сервера: объявить UTC, затем в Алматы;
#   * imported_calls.datetime_raw — timestamptz, но записаны в нём стенные часы
#     Алматы С ЯРЛЫКОМ UTC: АТС отдаёт «dd.mm.yyyy hh:mm:ss» по Алматы, а база с
#     поясом UTC так его и подписывает. Снимаем ярлык (AT TIME ZONE 'UTC') и
#     получаем те же стенные часы. Перевод в Алматы, как было в редакции 1,
#     сдвигал разговор на пять часов вперёд: звонок 14:41 (uniqueid Asterisk
#     1789983704) считался звонком 19:42 и мог уйти к чужой, более поздней
#     сделке того же номера;
#   * эпизоды Wazzup/ChatApp — настоящий момент (timestamptz): в Алматы.
_SUBJECT_MOMENT = """COALESCE(
    c.appeal_date,
    (c.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Almaty'),
    (e.ended_at AT TIME ZONE 'Asia/Almaty'),
    (ic.datetime_raw AT TIME ZONE 'UTC'),
    cs.day::timestamp,
    (ce.ended_at AT TIME ZONE 'Asia/Almaty'))"""

# Телефон собеседника у каждого вида. У звонка из АТС есть уже нормализованный
# `phone_normalized`, но он бывает пуст на старых строках — поэтому COALESCE с
# сырым номером, а десять цифр из него добывает norm_phone в Python.
_SUBJECT_PHONE = """COALESCE(
    NULLIF(btrim(c.phone_number), ''),
    NULLIF(btrim(e.contact_phone), ''),
    NULLIF(btrim(ic.phone_normalized), ''),
    NULLIF(btrim(ic.phone_number), ''),
    NULLIF(btrim(cs.client_phone), ''),
    NULLIF(btrim(ce.contact_phone), ''))"""

# DISTINCT ON обязателен: субъект, оценённый несколькими версиями модели, лежит
# в кэше несколькими строками, а в пачке связей один и тот же ключ дважды роняет
# весь INSERT ошибкой «ON CONFLICT DO UPDATE command cannot affect row a second
# time» — та же ловушка, что у upsert_leads в «Воронке ОП».
_SUBJECTS_SQL = """
SELECT DISTINCT ON (rc.subject_kind, rc.call_id)
       rc.subject_kind, rc.call_id, {phone} AS phone, {moment} AS happened_at
  FROM ai_review_cache rc
  LEFT JOIN calls c              ON rc.subject_kind = 'call'          AND c.id  = rc.call_id
  LEFT JOIN wazzup_episodes e    ON rc.subject_kind = 'wz_episode'    AND e.id  = rc.call_id
  LEFT JOIN imported_calls ic    ON rc.subject_kind = 'imported_call' AND ic.id = rc.call_id
  LEFT JOIN c2d_chat_snapshots cs ON rc.subject_kind = 'c2d_snapshot' AND cs.id = rc.call_id
  LEFT JOIN chatapp_episodes ce  ON rc.subject_kind = 'ca_episode'    AND ce.id = rc.call_id
 WHERE (c.id IS NOT NULL OR e.id IS NOT NULL OR ic.id IS NOT NULL
        OR cs.id IS NOT NULL OR ce.id IS NOT NULL)
"""

_LEADS_SQL = """
SELECT direction_code, source, stream_type, lead_key, phone, phones,
       created_at, taken_at
  FROM op_funnel_leads
 WHERE COALESCE(NULLIF(phones, ''), NULLIF(phone, '')) IS NOT NULL
   AND COALESCE(taken_at, created_at) >= %s
   AND COALESCE(taken_at, created_at) <= %s
"""


def _fetch_subjects(cur, only_new=True):
    """Субъекты оценки с телефоном и моментом разговора.

    `only_new` — не трогать уже связанные ТЕКУЩЕЙ редакцией правил. Связь
    прежней редакции считается несвязанной и пересчитывается: так правка
    правила доезжает до истории сама. Полная перелинковка (`only_new=False`)
    остаётся для ручного «пересчитать всё».
    """
    sql = _SUBJECTS_SQL.format(phone=_SUBJECT_PHONE, moment=_SUBJECT_MOMENT)
    params = ()
    if only_new:
        sql += """   AND NOT EXISTS (SELECT 1 FROM qa_subject_deals sd
                                      WHERE sd.subject_kind = rc.subject_kind
                                        AND sd.call_id = rc.call_id
                                        AND sd.rules_version >= %s)
"""
        params = (LINK_RULES_VERSION,)
    cur.execute(sql, params)
    out = []
    for kind, call_id, phone, happened_at in cur.fetchall():
        digits = norm_phone(phone)
        if not digits or happened_at is None:
            # Без номера или без времени связать не с чем. Молча пропускаем:
            # это не ошибка, а обычная переписка без телефона в карточке.
            continue
        out.append({'subject_kind': kind, 'call_id': int(call_id),
                    'phone': digits, 'started_at': happened_at})
    return out


def _fetch_leads(cur, since, until):
    cur.execute(_LEADS_SQL, (since, until))
    rows = []
    for (direction_code, source, stream_type, lead_key,
         phone, phones, created_at, taken_at) in cur.fetchall():
        lead = {
            'key': (direction_code, source, int(stream_type or 0), lead_key),
            'phones': lead_rules.lead_phones({'phones': phones, 'phone': phone}),
            'moment': taken_at or created_at,
        }
        if lead['phones'] and lead['moment'] is not None:
            rows.append(lead)
    return rows


def link(cur, *, only_new=True, now=None):
    """Посчитать связи и записать их. Возвращает сводку для журнала выгрузки.

    Курсор приходит снаружи и должен быть ПИШУЩИМ: функция зовётся из ночной
    джобы и из ручного «пересчитать», а не из показа списка.
    """
    subjects = _fetch_subjects(cur, only_new=only_new)
    if not subjects:
        return {'subjects': 0, 'linked': 0, 'unmatched': 0, 'leads': 0, 'dropped': 0}

    moments = [item['started_at'] for item in subjects]
    since = min(moments) - timedelta(days=LOOKBACK_DAYS)
    until = max(moments) + timedelta(days=1)
    deals = _fetch_leads(cur, since, until)
    if not deals:
        dropped = _drop_stale(cur, subjects, set())
        return {'subjects': len(subjects), 'linked': 0,
                'unmatched': len(subjects), 'leads': 0, 'dropped': dropped}

    # Правая граница окна последней сделки каждого номера: дальше этого момента
    # разговоров у нас всё равно нет.
    window_to = max(moments) + timedelta(days=1)
    assigned = lead_rules.assign_touches(deals, subjects, window_to=window_to,
                                         grace=GRACE)

    by_key = {deal['key']: deal for deal in deals}
    rows = []
    for key, items in assigned.items():
        deal = by_key.get(key)
        if not deal or not items:
            continue
        for subject in items:
            rows.append((
                subject['subject_kind'], subject['call_id'],
                key[0], key[1], key[2], key[3],
                subject['started_at'], subject['phone'],
                int(deal.get('dup_n') or 1), int(deal.get('dup_i') or 1),
            ))

    written = _write(cur, rows)
    # Разговор, который прежняя редакция связала, а нынешняя — нет, обязан
    # потерять связь: иначе ошибочная привязка жила бы вечно и фильтр по
    # сделке продолжал бы показывать чужой звонок.
    dropped = _drop_stale(cur, subjects, {(row[0], row[1]) for row in rows})
    return {'subjects': len(subjects), 'linked': written,
            'unmatched': len(subjects) - written, 'leads': len(deals),
            'dropped': dropped}


def _drop_stale(cur, subjects, linked_keys):
    """Удалить связи обработанных субъектов, которым сделка больше не нашлась.

    Трогает только тех, кого этот прогон и пересчитывал: у нового субъекта
    строки нет вовсе, а связь прежней редакции без пары в текущей — устарела.
    """
    stale = [(item['subject_kind'], item['call_id']) for item in subjects
             if (item['subject_kind'], item['call_id']) not in linked_keys]
    if not stale:
        return 0
    cur.execute(
        """DELETE FROM qa_subject_deals sd
            USING unnest(%s::text[], %s::bigint[]) AS s(subject_kind, call_id)
            WHERE sd.subject_kind = s.subject_kind AND sd.call_id = s.call_id""",
        ([kind for kind, _ in stale], [call_id for _, call_id in stale]))
    return max(0, cur.rowcount or 0)


def _write(cur, rows, page=500):
    """Записать связи пачками.

    ON CONFLICT DO UPDATE, а не DO NOTHING: перелинковка после правки правил
    обязана ИСПРАВИТЬ прежнюю связь, иначе старый ответ жил бы вечно и правило
    меняли бы вхолостую.
    """
    if not rows:
        return 0
    written = 0
    for start in range(0, len(rows), page):
        chunk = rows[start:start + page]
        values = ', '.join(['(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'] * len(chunk))
        args = [field for row in chunk for field in (*row, LINK_RULES_VERSION)]
        cur.execute(
            """
            INSERT INTO qa_subject_deals
                (subject_kind, call_id, direction_code, source, stream_type, lead_key,
                 happened_at, matched_phone, dup_total, dup_index, rules_version)
            VALUES %s
            ON CONFLICT (subject_kind, call_id) DO UPDATE SET
                direction_code = EXCLUDED.direction_code,
                source         = EXCLUDED.source,
                stream_type    = EXCLUDED.stream_type,
                lead_key       = EXCLUDED.lead_key,
                happened_at    = EXCLUDED.happened_at,
                matched_phone  = EXCLUDED.matched_phone,
                dup_total      = EXCLUDED.dup_total,
                dup_index      = EXCLUDED.dup_index,
                rules_version  = EXCLUDED.rules_version,
                linked_at      = NOW()
            """ % values,
            args,
        )
        # rowcount по страницам и складываем: по последней странице он соврал бы
        # на любой пачке длиннее page.
        written += max(0, cur.rowcount or 0)
    return written


def learn_dictionary(cur):
    """Завести в словаре написания парков и каналов, которых там ещё нет.

    Требование ТЗ: «значения подтягиваются из справочника, не хардкодятся», а
    новый таксопарк не должен требовать релиза.

    ПАРК сводится к БРЕНДУ (`brands.park_brand`): «itaxi 2 астана» — это iTaxi,
    и написание уходит алиасом в строку бренда, а не заводит свой «парк».
    Редакция 1 заводила строку на каждое написание, и фильтр «iTaxi» ловил
    около трети сделок iTaxi. Написание без бренда (один город) не заводится:
    сделка остаётся в «Не определено». Перед пополнением словарь чистится от
    таких прежних строк (`normalise_park_dictionary`).

    КАНАЛ приходит уже нормализованным кодом воронки, и незнакомое значение
    заводится своей строкой: склеить его с каналом человек может потом.

    Тихо терять такие значения нельзя: белый список брендов в старой формуле
    «Звонков» именно так молча съедал звонки нового парка.
    """
    learned = normalise_park_dictionary(cur)
    for kind, column in (('park', 'park_name'), ('channel', 'utm_source')):
        cur.execute(
            f"""
            SELECT DISTINCT lower(btrim({column})) AS raw
              FROM op_funnel_leads
             WHERE btrim(COALESCE({column}, '')) <> ''
               AND NOT EXISTS (SELECT 1 FROM qa_marketing_dict d
                                WHERE d.kind = %s AND d.aliases ? lower(btrim({column})))
            """,
            (kind,),
        )
        for (raw,) in cur.fetchall():
            if kind == 'park':
                brand = brands.park_brand(raw)
                if not brand:
                    continue
                code, title = brand
            else:
                code, title = brands.code_of(raw), raw
            if not code:
                continue
            _upsert_aliases(cur, kind, code, title, [raw])
            learned += 1
    if learned:
        log.info('Маркетинговый мониторинг: словарь пополнен, написаний: %d', learned)
    return learned


# Порядок строк, заведённых связывателем. Засеянные бренды ТЗ стоят выше (10–110),
# и по этой границе чистка отличает «заведено автоматически» от «задано нами».
LEARNED_SORT_ORDER = 900


def _upsert_aliases(cur, kind, code, title, aliases):
    """Добавить написания в строку словаря; строки нет — завести её.

    Подпись существующей строки не трогается: засеянный бренд и правленное
    человеком имя важнее того, что вывел классификатор.
    """
    cur.execute(
        """
        INSERT INTO qa_marketing_dict (kind, code, title, sort_order, aliases)
        VALUES (%s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (kind, code) DO UPDATE
           SET aliases = (
               SELECT jsonb_agg(DISTINCT value ORDER BY value)
                 FROM jsonb_array_elements(
                          qa_marketing_dict.aliases || EXCLUDED.aliases) AS value),
               updated_at = NOW()
        """,
        (kind, code, title, LEARNED_SORT_ORDER, json.dumps(sorted(set(aliases)))),
    )


def normalise_park_dictionary(cur):
    """Свести заведённые автоматически строки парков к брендам. Идемпотентно.

    Трогает только строки связывателя (`sort_order >= LEARNED_SORT_ORDER`), и то
    лишь не правленные человеком (`updated_by IS NULL`). Каждое их написание
    уходит алиасом в строку своего бренда; строка, в которой не осталось
    написаний своего бренда, удаляется. Повторный прогон ничего не меняет,
    поэтому зовётся перед каждым пополнением: чистка доезжает до прода сама,
    без ручного шага после деплоя.

    Возвращает, сколько строк словаря изменено или удалено.
    """
    cur.execute(
        """SELECT code, title, aliases, sort_order, updated_by
             FROM qa_marketing_dict WHERE kind = 'park'""")
    rows = cur.fetchall()
    existing = {row[0]: row for row in rows}
    wanted, titles = {}, {}
    learned = [row for row in rows
               if int(row[3] or 0) >= LEARNED_SORT_ORDER and row[4] is None]
    # Код строки лежит в её же алиасах. Составной код («itaxi_2_astana») — не
    # написание из CRM. Однословный («global», «ipartner») бывает и настоящим
    # написанием — его сохраняем, но лишь если он ведёт к тому же бренду, что
    # и написания самой строки: иначе это транслит, и он завёл бы бренд-
    # призрак без сделок («zhanataksi» при «жанатакси» → Jana). Подпись бренда
    # берётся из написаний в первую очередь: «честный» даёт «Честный», а не
    # «Chestnyy». Поэтому два прохода: сперва написания, затем коды.
    def code_is_spelling(code, aliases):
        if '_' in code or code not in aliases:
            return False
        own = brands.park_brand(code)
        if not own:
            return False
        others = {brand[0] for brand in (brands.park_brand(alias)
                                         for alias in aliases if alias != code) if brand}
        return not others or own[0] in others

    passes = (
        [(spelling, code) for code, _t, aliases, _o, _b in learned
         for spelling in (aliases or []) if spelling != code],
        [(code, code) for code, _t, aliases, _o, _b in learned
         if code_is_spelling(code, list(aliases or []))],
    )
    for items in passes:
        for spelling, _row in items:
            brand = brands.park_brand(spelling)
            if brand:
                wanted.setdefault(brand[0], set()).add(spelling)
                titles.setdefault(brand[0], brand[1])

    changed = 0
    for code, _title, aliases, _order, _by in learned:
        if code not in wanted:
            cur.execute("DELETE FROM qa_marketing_dict WHERE kind = 'park' AND code = %s", (code,))
            changed += 1
            continue
        target = sorted(wanted.pop(code))
        if sorted(aliases or []) != target or _title != titles[code]:
            cur.execute(
                """UPDATE qa_marketing_dict SET aliases = %s::jsonb, title = %s,
                          updated_at = NOW()
                    WHERE kind = 'park' AND code = %s""",
                (json.dumps(target), titles[code], code))
            changed += 1
    # Остальное — в чужие строки (засеянный бренд, правленная человеком) или в
    # новые строки брендов.
    for code, spellings in wanted.items():
        current = set((existing.get(code) or (None, None, []))[2] or [])
        if spellings <= current:
            continue
        _upsert_aliases(cur, 'park', code, titles[code], spellings)
        changed += 1
    if changed:
        log.info('Маркетинговый мониторинг: словарь парков сведён к брендам, строк: %d', changed)
    return changed
