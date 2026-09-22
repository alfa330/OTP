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

import logging
from datetime import datetime, timedelta

from cdr import leads as lead_rules
from cdr.touches import norm_phone

log = logging.getLogger(__name__)

# Насколько раньше разговора может быть заведена сделка, чтобы считаться его
# сделкой. Берём у «Касаний»: там этот запас выведен на живых данных.
GRACE = lead_rules.GRACE

# Сколько суток сделок поднимать вокруг разговоров. Заявку оставляют раньше, чем
# по ней звонят, а перезванивают и через неделю; 45 суток закрывают хвост и не
# тянут в память весь год.
LOOKBACK_DAYS = 45

# Момент разговора у каждого вида субъекта, уже в Алматы.
_SUBJECT_MOMENT = """COALESCE(
    (c.appeal_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Almaty'),
    (c.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Almaty'),
    (e.ended_at AT TIME ZONE 'Asia/Almaty'),
    (ic.datetime_raw AT TIME ZONE 'Asia/Almaty'),
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

    `only_new` — не трогать уже связанные. Полная перелинковка нужна редко (после
    правки правил окон), а ежедневный прогон обязан быть дешёвым.
    """
    sql = _SUBJECTS_SQL.format(phone=_SUBJECT_PHONE, moment=_SUBJECT_MOMENT)
    if only_new:
        sql += """   AND NOT EXISTS (SELECT 1 FROM qa_subject_deals sd
                                      WHERE sd.subject_kind = rc.subject_kind
                                        AND sd.call_id = rc.call_id)
"""
    cur.execute(sql)
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
        return {'subjects': 0, 'linked': 0, 'unmatched': 0, 'leads': 0}

    moments = [item['started_at'] for item in subjects]
    since = min(moments) - timedelta(days=LOOKBACK_DAYS)
    until = max(moments) + timedelta(days=1)
    deals = _fetch_leads(cur, since, until)
    if not deals:
        return {'subjects': len(subjects), 'linked': 0,
                'unmatched': len(subjects), 'leads': 0}

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
    return {'subjects': len(subjects), 'linked': written,
            'unmatched': len(subjects) - written, 'leads': len(deals)}


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
        values = ', '.join(['(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'] * len(chunk))
        args = [field for row in chunk for field in row]
        cur.execute(
            """
            INSERT INTO qa_subject_deals
                (subject_kind, call_id, direction_code, source, stream_type, lead_key,
                 happened_at, matched_phone, dup_total, dup_index)
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
    новый таксопарк не должен требовать релиза. Заводим код-заглушку по самому
    написанию: он сразу работает как отдельное значение фильтра, а склеить его с
    существующим брендом человек может потом, дописав алиас.

    Тихо терять такие значения нельзя: белый список брендов в старой формуле
    «Звонков» именно так молча съедал звонки нового парка.
    """
    import json
    import re as _re

    learned = 0
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
            code = _re.sub(r'[^a-z0-9]+', '_', _translit(raw)).strip('_')[:64]
            if not code:
                continue
            cur.execute(
                """
                INSERT INTO qa_marketing_dict (kind, code, title, sort_order, aliases)
                VALUES (%s, %s, %s, 900, %s::jsonb)
                ON CONFLICT (kind, code) DO UPDATE
                   SET aliases = (
                       SELECT jsonb_agg(DISTINCT value)
                         FROM jsonb_array_elements(
                                  qa_marketing_dict.aliases || EXCLUDED.aliases) AS value)
                """,
                (kind, code, raw, json.dumps(sorted({code, raw}))),
            )
            learned += 1
    if learned:
        log.info('Маркетинговый мониторинг: в словарь добавлено %d написаний', learned)
    return learned


_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu',
    'я': 'ya', 'і': 'i', 'ғ': 'g', 'қ': 'q', 'ң': 'n', 'ө': 'o', 'ұ': 'u', 'ү': 'u',
    'һ': 'h', 'ә': 'a',
}


def _translit(text):
    """Кириллица в код: «Ноль такси» → noltaksi. Код уезжает в адресную строку и
    в пресет, и кириллица там превращается в проценты на полстроки."""
    return ''.join(_TRANSLIT.get(char, char) for char in str(text or '').lower())
