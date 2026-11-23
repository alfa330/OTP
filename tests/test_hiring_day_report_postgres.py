# -*- coding: utf-8 -*-
"""Отчёт за день «Воронки найма» (HR-3) на настоящем Postgres: HIRING_TEST_PORT — порт кластера.

Один день работы двух рекрутеров через настоящий сервис раздела: отклики из hh, «Таргета», WhatsApp и внесённые руками
(Kaspi, «Вручную»), взятие в работу, звонки с исходом, переписка, приглашения на собеседование и на обучение, отказы
наш и кандидата, «Не отвечает звонок/чат», возврат в пул руководителем, звонки телефонии и «Другая работа». Проверяется:

  * «Поступило» и «Отработано» по каналам владельца, строки «из них» складываются в «Отработано»;
  * напоминание приглашённому и комментарий руководителя в пуле — не «Отработано»;
  * недозвон снимает сообщение кандидата в WhatsApp после звонка;
  * отчёт за прошлый день — состояние на ЕГО конец, а не нынешнее;
  * границы суток, отчёт одного рекрутера, разрез по рекрутерам, эффективность, список за цифрой, Excel.

    HIRING_TEST_PORT=55432 python -m pytest tests/test_hiring_day_report_postgres.py
"""

import io
from datetime import datetime, time, timedelta

import pytest

from hiring import day_report, queries
from tests.test_hiring_postgres import (  # noqa: F401 — фикстуры раздела: своя схема, люди, вакансия, группа
    LEAD_GRANT, NOW, PORT, REC1, REC2, ctx_of, db, world,
)

pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (HIRING_TEST_PORT)')

D = NOW.date()


def at(day_offset, hour, minute=0, second=0):
    return datetime.combine(D + timedelta(days=day_offset), time(hour, minute, second))


def reason_id(world, kind):
    with world['db']._get_cursor() as cur:
        return next(row['id'] for row in queries.list_reasons(cur) if row['kind'] == kind and not row['requires_comment'])


class Day:
    """Сценарий: часы сервиса двигаются вперёд, карточки — по имени."""

    def __init__(self, world):
        self.world = world
        self.service = world['service']
        self.cards = {}

    def clock(self, moment):
        self.world['clock'].now = moment

    def add(self, name, user, phone, source, moment):
        self.clock(moment)
        payload = {'phone': phone, 'source_id': self.world['sources'][source]['id'], 'full_name': name,
                   'vacancy_id': self.world['vacancy_id']}
        outcome, card = self.service.add_response(ctx_of(self.world, user), payload)
        assert outcome == 'created'
        self.cards[name] = card['id']
        return card['id']

    def intake(self, name, phone, source, moment):
        """Отклик, пришедший сам (вебхук, номер WhatsApp найма): карточку завёл не человек."""
        self.clock(moment)
        with self.world['db']._get_cursor() as cur:
            outcome, card_id = self.service._intake_one(cur, phone=phone, kind='response', source=self.world['sources'][source],
                                                        now=moment, by_name='Площадка', full_name=name)
        assert outcome == 'created'
        self.cards[name] = card_id
        return card_id

    def take(self, name, user, moment):
        self.clock(moment)
        self.service.take(ctx_of(self.world, user), self.cards[name])

    def touch(self, name, user, moment, kind, result=None, body='касание'):
        self.clock(moment)
        payload = {'kind': kind, 'body': body}
        if result:
            payload['result'] = result
        self.service.touch(ctx_of(self.world, user), self.cards[name], payload)

    def move(self, name, user, moment, target, **payload):
        self.clock(moment)
        self.service.move(ctx_of(self.world, user), self.cards[name], target, payload)

    def substage(self, name, user, moment, substage):
        self.clock(moment)
        self.service.set_substage(ctx_of(self.world, user), self.cards[name], substage)

    def wa_inbound(self, name, moment):
        """Кандидат написал на номер WhatsApp найма (чат привязан к карточке)."""
        card_id = self.cards[name]
        key = 'test-%d' % card_id
        with self.world['db']._get_cursor() as cur:
            cur.execute("""
                INSERT INTO hiring_wa_chats (key, jid, card_id, first_at, last_at, last_inbound_at, messages, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s) ON CONFLICT (key) DO NOTHING
            """, (key, key + '@s.whatsapp.net', card_id, moment, moment, moment, moment, moment))
            cur.execute("""
                INSERT INTO hiring_wa_messages (chat_key, wa_id, from_me, sent_at, kind, body, created_at)
                VALUES (%s, %s, FALSE, %s, 'text', 'да, интересно', %s)
            """, (key, 'wa-%d-%s' % (card_id, moment.isoformat()), moment, moment))

    def pbx_call(self, name, user, moment, result, billsec, direction='out', link=False):
        """Звонок из телефонии Binotel с линии рекрутера (как его пишет HiringService._record_call). link — звонок дописал
        последнюю отметку «Позвонить» в ленте карточки (call_id у касания), как при слиянии в _record_call."""
        with self.world['db']._get_cursor() as cur:
            cur.execute("""
                INSERT INTO hiring_calls (general_call_id, card_id, recruiter_id, department_id, internal_number, external_phone,
                                          direction, disposition, result, billsec, waitsec, started_at, recording, source,
                                          created_at, updated_at)
                VALUES (%s, %s, %s, 2200, '8501', '+77050000000', %s, %s, %s, %s, 5, %s, 'none', 'webhook', %s, %s)
                RETURNING id
            """, ('g-%s-%s' % (name, moment.isoformat()), self.cards[name], user, direction,
                  'ANSWER' if result == 'answered' else 'NOANSWER', result, billsec, moment, moment, moment))
            call_id = cur.fetchone()[0]
            if link:
                cur.execute("""
                    UPDATE hiring_touches SET call_id = %s, duration_sec = %s
                     WHERE id = (SELECT id FROM hiring_touches WHERE card_id = %s AND kind = 'call' AND call_id IS NULL
                                  ORDER BY id DESC LIMIT 1)
                """, (call_id, billsec, self.cards[name]))


@pytest.fixture
def day(world):
    """Вчера, сегодня (день отчёта D) и полночь после него — см. разметку ожиданий в тестах."""
    s = Day(world)
    group_id = world['group_id']
    ours, candidate = reason_id(world, 'refusal_ours'), reason_id(world, 'refusal_candidate')

    # За четыре дня до D: «Таргет» руками у второго рекрутера, звонок без ответа, через 72 часа — в «Не отвечает».
    s.add('L', REC2, '8 705 100 00 20', 'target', at(-4, 9))
    s.take('L', REC2, at(-4, 9, 1))
    s.touch('L', REC2, at(-4, 9, 5), 'call', 'no_answer')
    s.clock(at(-1, 10))
    assert s.service.archive_silent() == 1

    # Вчера (D−1): три отклика первого рекрутера.
    s.add('A', REC1, '8 705 100 00 01', 'manual', at(-1, 10))
    s.take('A', REC1, at(-1, 10, 1))
    s.touch('A', REC1, at(-1, 10, 2), 'call', 'no_answer')
    s.add('J', REC1, '8 705 100 00 10', 'hh', at(-1, 10, 10))
    s.take('J', REC1, at(-1, 10, 11))
    s.touch('J', REC1, at(-1, 10, 12), 'call', 'dial')
    s.move('J', REC1, at(-1, 10, 13), 'invited_office', office_visit_at=at(0, 11).isoformat())
    s.add('I', REC1, '8 705 100 00 09', 'hh', at(-1, 10, 20))
    s.take('I', REC1, at(-1, 10, 21))
    s.touch('I', REC1, at(-1, 10, 22), 'call', 'dial')
    s.move('I', REC1, at(-1, 10, 23), 'invited_office', office_visit_at=at(0, 12).isoformat())

    # День D.
    s.add('B', REC1, '8 705 200 00 01', 'hh', at(0, 9))                        # hh руками
    s.take('B', REC1, at(0, 9, 1))
    s.touch('B', REC1, at(0, 9, 2), 'call', 'dial')                           # → В работе
    s.intake('C', '8 705 200 00 02', 'target', at(0, 9, 10))                  # «Таргет» пришёл сам
    s.take('C', REC1, at(0, 9, 11))
    s.touch('C', REC1, at(0, 9, 12), 'whatsapp')
    s.move('C', REC1, at(0, 9, 13), 'invited_office', office_visit_at=at(1, 10).isoformat())    # → собеседование завтра
    s.intake('E1', '8 705 200 00 03', 'whatsapp', at(0, 9, 20))               # написал на номер найма сам
    s.take('E1', REC2, at(0, 9, 21))
    s.touch('E1', REC2, at(0, 9, 22), 'call', 'no_answer')                    # → недозвон
    s.intake('E2', '8 705 200 00 04', 'whatsapp', at(0, 9, 25))
    s.take('E2', REC2, at(0, 9, 25, 30))
    s.touch('E2', REC2, at(0, 9, 26), 'call', 'no_answer')
    s.wa_inbound('E2', at(0, 9, 40))                                           # ответил в WhatsApp → в работе
    s.add('F', REC2, '8 705 200 00 05', 'kaspi', at(0, 9, 30))                # Kaspi руками
    s.take('F', REC2, at(0, 9, 31))
    s.touch('F', REC2, at(0, 9, 32), 'call', 'dial')
    s.move('F', REC2, at(0, 9, 33), 'refused', initiator='ours', reason_id=ours)
    s.add('G', REC1, '8 705 200 00 06', 'manual', at(0, 9, 40))               # «Вручную» — сам позвонил
    s.take('G', REC1, at(0, 9, 41))
    s.touch('G', REC1, at(0, 9, 42), 'comment', body='подумает')
    s.move('G', REC1, at(0, 9, 43), 'refused', initiator='candidate', reason_id=candidate)
    s.add('H', REC1, '8 705 200 00 07', 'hh', at(0, 9, 50))
    s.take('H', REC1, at(0, 9, 51))
    s.touch('H', REC1, at(0, 9, 52), 'call', 'dial')
    s.move('H', REC1, at(0, 9, 53), 'invited_training', group_id=group_id)
    s.touch('A', REC1, at(0, 10), 'call', 'dial')                             # вчерашний недозвон дозвонились → в работе
    s.touch('I', REC1, at(0, 10, 5), 'whatsapp', body='ждём вас в 12:00')       # напоминание приглашённому — не обработка
    s.substage('I', REC1, at(0, 12, 10), 'came')                              # пришёл на собеседование
    s.substage('J', REC1, at(0, 11, 10), 'came')
    s.move('J', REC1, at(0, 11, 20), 'invited_training', group_id=group_id)   # после собеседования — на обучение
    s.intake('K', '8 705 200 00 08', 'target', at(0, 10, 15))
    s.take('K', REC2, at(0, 10, 20))
    s.touch('K', REC2, at(0, 10, 21), 'call', 'dial')
    s.clock(at(0, 10, 30))
    s.service.reassign(ctx_of(world, LEAD_GRANT), s.cards['K'], None, 'вернуть в пул')   # → в пул, в отчёте нет
    s.touch('L', REC2, at(0, 10, 40), 'call', 'no_answer')                    # молчит дальше: «Не отвечает» → недозвон
    s.add('N', REC1, '8 705 200 00 11', 'hh', at(0, 11))                      # остался в пуле
    s.touch('N', LEAD_GRANT, at(0, 11, 5), 'comment', body='посмотреть')      # комментарий в пуле — не обработка
    s.pbx_call('B', REC1, at(0, 14), 'answered', 90)
    s.pbx_call('H', REC1, at(0, 14, 10), 'answered', 30)
    s.pbx_call('C', REC1, at(0, 14, 20), 'no_answer', 0)
    s.pbx_call('B', REC1, at(0, 14, 30), 'answered', 50, direction='in')     # входящий — не «совершено»
    s.pbx_call('E1', REC2, at(0, 14, 40), 'busy', 0)
    s.add('P', REC2, '8 705 200 00 12', 'hh', at(0, 23, 59))                  # последняя секунда дня
    s.take('P', REC2, at(0, 23, 59, 59))
    s.add('Q', REC2, '8 705 200 00 13', 'hh', at(1, 0, 0, 10))                # уже следующий день
    s.take('Q', REC2, at(1, 0, 0, 30))
    with world['db']._get_cursor() as cur:
        day_report.save_note(cur, REC1, D, 'Помогала бухгалтерии с собеседованием', at(0, 18))
    return s


def report(world, when=D, **kwargs):
    with world['db']._get_cursor() as cur:
        return day_report.build(cur, when, today=D + timedelta(days=1), **kwargs)


def row(data, key):
    return next(item for item in data['rows'] if item['key'] == key)['cells']


def person(data, user_id):
    return next(item for item in data['people'] if item['id'] == user_id)


def test_team_day_by_channels_adds_up(world, day):
    data = report(world, viewer_id=REC1)
    assert [channel['code'] for channel in data['channels']] == ['hh', 'target', 'whatsapp', 'manual']
    assert [channel['label'] for channel in data['channels']] == ['HeadHunter', 'Таргет', 'WhatsApp', 'Вручную']
    assert row(data, 'arrived') == {'hh': 4, 'target': 2, 'whatsapp': 2, 'manual': 2, 'all': 10}
    # Отработано: K вернули в пул (нет), I — только напоминание (нет), N — в пуле (нет), Q — завтра (нет).
    assert row(data, 'done') == {'hh': 4, 'target': 2, 'whatsapp': 2, 'manual': 3, 'all': 11}
    assert row(data, 'office') == {'hh': 0, 'target': 1, 'whatsapp': 0, 'manual': 0, 'all': 1}
    assert row(data, 'training') == {'hh': 2, 'target': 0, 'whatsapp': 0, 'manual': 0, 'all': 2}
    assert row(data, 'refused_ours') == {'hh': 0, 'target': 0, 'whatsapp': 0, 'manual': 1, 'all': 1}
    assert row(data, 'refused_candidate') == {'hh': 0, 'target': 0, 'whatsapp': 0, 'manual': 1, 'all': 1}
    assert row(data, 'no_answer') == {'hh': 0, 'target': 1, 'whatsapp': 1, 'manual': 0, 'all': 2}
    assert row(data, 'in_work') == {'hh': 2, 'target': 0, 'whatsapp': 1, 'manual': 1, 'all': 4}
    parts = [item for item in data['rows'] if item['part']]
    assert [item['key'] for item in parts] == list(day_report.BUCKETS)
    for code in ('hh', 'target', 'whatsapp', 'manual', 'all'):
        assert sum(item['cells'][code] for item in parts) == row(data, 'done')[code]
    assert row(data, 'interviews') == {'hh': 2, 'target': 0, 'whatsapp': 0, 'manual': 0, 'all': 2}
    assert data['title'] == '15 октября 2026, четверг' and data['day'] == '2026-10-15' and data['recruiter'] is None
    assert not data['is_today']


def test_people_and_efficiency(world, day):
    data = report(world, viewer_id=REC1)
    assert [item['id'] for item in data['people']] == [REC1, REC2]   # руководитель с комментарием в пуле — не в отчёте
    first = person(data, REC1)
    assert (first['done'], first['office'], first['training'], first['refused_candidate'], first['in_work']) == (6, 1, 2, 1, 2)
    assert (first['refused_ours'], first['no_answer']) == (0, 0)
    # Звонки: три отметки с исходом (B, H, A — дозвон) и три исходящих из телефонии (90 с, 30 с, без ответа).
    assert (first['calls'], first['talked'], first['talk_sec'], first['avg_talk_sec']) == (6, 5, 120, 60)
    assert first['whatsapp'] == 2                                       # C и напоминание I
    second = person(data, REC2)
    assert (second['done'], second['no_answer'], second['in_work'], second['refused_ours']) == (5, 2, 2, 1)
    # E1, E2, L — без ответа, F и K — дозвон; занято из телефонии. Разговоров с длительностью нет — среднего тоже.
    assert (second['calls'], second['talked'], second['talk_sec'], second['avg_talk_sec'], second['whatsapp']) == (6, 2, None, None, 0)
    totals = data['totals']
    assert (totals['done'], totals['calls'], totals['talked'], totals['talk_sec'], totals['avg_talk_sec']) == (11, 12, 7, 120, 60)
    assert [note['body'] for note in data['notes']] == ['Помогала бухгалтерии с собеседованием']
    assert data['notes'][0]['mine'] is True and data['notes'][0]['name'] == 'Первый Рекрутер'
    assert report(world, viewer_id=REC2)['notes'][0]['mine'] is False


def test_single_recruiter_report(world, day):
    data = report(world, recruiter_id=REC1, viewer_id=REC1)
    assert data['recruiter'] == {'id': REC1, 'name': 'Первый Рекрутер'}
    # Пул общий: hh, «Таргет», WhatsApp — все; «Вручную» — только внесённые им (Kaspi второго рекрутера — нет).
    assert row(data, 'arrived') == {'hh': 4, 'target': 2, 'whatsapp': 2, 'manual': 1, 'all': 9}
    assert row(data, 'done') == {'hh': 3, 'target': 1, 'whatsapp': 0, 'manual': 2, 'all': 6}
    assert row(data, 'interviews')['all'] == 2
    assert [item['id'] for item in data['people']] == [REC1]
    assert data['totals']['calls'] == 6 and data['totals']['whatsapp'] == 2
    other = report(world, recruiter_id=REC2)
    assert row(other, 'done')['all'] == 5 and row(other, 'interviews')['all'] == 0
    assert row(other, 'arrived')['manual'] == 1 and other['notes'] == []


def test_past_day_is_its_own_end_of_day(world, day):
    # Вчера: A — недозвон (сегодня дозвонились), J — приглашён на собеседование (сегодня уже на обучении).
    data = report(world, D - timedelta(days=1))
    assert row(data, 'arrived') == {'hh': 2, 'target': 0, 'whatsapp': 0, 'manual': 1, 'all': 3}
    assert row(data, 'done')['all'] == 3
    assert row(data, 'office') == {'hh': 2, 'target': 0, 'whatsapp': 0, 'manual': 0, 'all': 2}
    assert row(data, 'no_answer') == {'hh': 0, 'target': 0, 'whatsapp': 0, 'manual': 1, 'all': 1}
    assert row(data, 'in_work')['all'] == 0 and row(data, 'training')['all'] == 0
    # «Не отвечает» сторож перевёл вчера — это не работа рекрутера: L вчера не отработан.
    assert person(data, REC1)['done'] == 3 and all(item['id'] != REC2 for item in data['people'])
    # За четыре дня до D: L взят и не ответил — недозвон на конец ТОГО дня.
    early = report(world, D - timedelta(days=4))
    assert row(early, 'done') == {'hh': 0, 'target': 1, 'whatsapp': 0, 'manual': 0, 'all': 1}
    assert row(early, 'no_answer')['all'] == 1
    # Полночь: Q — завтра.
    tomorrow = report(world, D + timedelta(days=1))
    assert row(tomorrow, 'arrived')['all'] == 1 and row(tomorrow, 'done')['all'] == 1


def test_cards_behind_a_number_and_excel(world, day):
    cards = day.cards
    with world['db']._get_cursor() as cur:
        assert day_report.card_ids_for(cur, D, 'in_work:hh') == sorted([cards['B'], cards['P']])
        assert day_report.card_ids_for(cur, D, 'rec:%d:no_answer' % REC2) == sorted([cards['E1'], cards['L']])
        assert day_report.card_ids_for(cur, D, 'interviews:all') == sorted([cards['I'], cards['J']])
        assert day_report.card_ids_for(cur, D, 'done:manual', recruiter_id=REC1) == sorted([cards['A'], cards['G']])
        assert day_report.card_ids_for(cur, D, 'nope:all') == []
    from openpyxl import load_workbook
    book = load_workbook(io.BytesIO(day_report.export_day(report(world))))
    values = [[cell for cell in line if cell is not None] for line in book['Отчёт за день'].iter_rows(values_only=True)]
    assert values[0] == ['Воронка найма · отчёт за 15 октября 2026, четверг · Все рекрутеры']
    assert ['Отработано', 4, 2, 2, 3, 11] in values
    assert ['   Недозвон', 0, 1, 1, 0, 2] in values
    assert ['Первый Рекрутер', 6, 5, '1:00', '2:00', 2] in values      # звонки, разговоры, среднее, всего, WhatsApp
    assert ['Второй Рекрутер', 6, 2, '—', '—', 0] in values
    assert ['Первый Рекрутер', 'Помогала бухгалтерии с собеседованием'] in values


def test_new_cycle_forgets_old_no_answer_and_linked_call_counts_once(world):
    s = Day(world)
    # R: первый рекрутер не дозвонился, руководитель вернул в пул; через два дня второй взял и написал в WhatsApp —
    # новый круг без звонков: «В работе», а не недозвон прошлого круга.
    s.add('R', REC1, '8 705 300 00 01', 'manual', at(-2, 10))
    s.take('R', REC1, at(-2, 10, 1))
    s.touch('R', REC1, at(-2, 10, 2), 'call', 'no_answer')
    s.clock(at(-2, 11))
    s.service.reassign(ctx_of(world, LEAD_GRANT), s.cards['R'], None, 'в пул')
    s.take('R', REC2, at(0, 10))
    s.touch('R', REC2, at(0, 10, 1), 'whatsapp')
    # S: звонок с линии дописал отметку «Позвонить» (как HiringService._record_call) — один звонок, а не два.
    s.add('S', REC2, '8 705 300 00 02', 'manual', at(0, 11))
    s.take('S', REC2, at(0, 11, 1))
    s.touch('S', REC2, at(0, 11, 2), 'call', 'dial')
    s.pbx_call('S', REC2, at(0, 11, 2), 'answered', 75, link=True)
    # Ответ без секунды разговора (сбросили сразу) — звонок, но не разговор.
    s.pbx_call('S', REC2, at(0, 11, 30), 'answered', 0)
    # T: вчера отказ, сегодня руководитель вернул в работу (повторная активация) — это не работа рекрутера с откликом.
    s.add('T', REC1, '8 705 300 00 03', 'manual', at(-1, 12))
    s.take('T', REC1, at(-1, 12, 1))
    s.touch('T', REC1, at(-1, 12, 2), 'call', 'dial')
    s.move('T', REC1, at(-1, 12, 3), 'refused', initiator='ours', reason_id=reason_id(world, 'refusal_ours'))
    s.move('T', LEAD_GRANT, at(0, 12), 'in_work', reactivate=True, recruiter_id=REC1)
    # U: руководитель переназначил карточку с первого рекрутера на второго — второй её ещё не трогал.
    s.add('U', REC1, '8 705 300 00 04', 'manual', at(-1, 13))
    s.take('U', REC1, at(-1, 13, 1))
    s.clock(at(0, 12, 30))
    s.service.reassign(ctx_of(world, LEAD_GRANT), s.cards['U'], REC2, 'отпуск')
    data = report(world)
    assert row(data, 'done')['all'] == 2
    assert row(data, 'in_work')['all'] == 2 and row(data, 'no_answer')['all'] == 0
    assert [item['id'] for item in data['people']] == [REC2]
    second = person(data, REC2)
    assert (second['calls'], second['talked'], second['talk_sec'], second['avg_talk_sec'], second['whatsapp']) == (2, 1, 75, 75, 1)
    # Два дня назад: R к концу того дня — снова в пуле, в «Отработано» его нет; звонок первого рекрутера — в счёт.
    earlier = report(world, D - timedelta(days=2))
    assert row(earlier, 'done')['all'] == 0
    assert (person(earlier, REC1)['done'], person(earlier, REC1)['calls']) == (0, 1)


def test_notes_are_one_per_person_and_day(world):
    with world['db']._get_cursor() as cur:
        saved = day_report.save_note(cur, REC1, D, '  черновик ', NOW)
        assert saved['body'] == '  черновик '
        again = day_report.save_note(cur, REC1, D, 'итог дня', NOW + timedelta(minutes=5))
        assert again['body'] == 'итог дня' and again['updated_at'] == NOW + timedelta(minutes=5)
        day_report.save_note(cur, REC2, D, 'своё', NOW)
        day_report.save_note(cur, REC1, D - timedelta(days=1), 'вчера', NOW)
        # По алфавиту имён: «Второй Рекрутер», потом «Первый Рекрутер».
        assert [note['body'] for note in day_report.list_notes(cur, D)] == ['своё', 'итог дня']
        assert [note['body'] for note in day_report.list_notes(cur, D, REC2)] == ['своё']
        assert day_report.save_note(cur, REC1, D, '   ', NOW) is None             # пустой текст — записи нет
        assert [note['user_id'] for note in day_report.list_notes(cur, D)] == [REC2]
        long_text = 'а' * (day_report.NOTE_MAX + 50)
        assert len(day_report.save_note(cur, REC2, D, long_text, NOW)['body']) == day_report.NOTE_MAX
