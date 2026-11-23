# -*- coding: utf-8 -*-
"""Отчёт за день «Воронки найма» (HR-3) через ручки Blueprint на настоящем Postgres (HIRING_TEST_PORT).

Расчёт проверяет tests/test_hiring_day_report_postgres.py; здесь — граница: рекрутер видит только свой отчёт (чужой
recruiter_id не помогает), руководитель — команду и любого рекрутера, тренеру и главе отдела отчёта нет; «Другую работу»
пишет каждый за себя и не в будущее; выгрузка — xlsx, список за цифрой — те же карточки.

    HIRING_TEST_PORT=55432 python -m pytest tests/test_hiring_day_report_routes_postgres.py
"""

import io
from datetime import timedelta

import pytest

from hiring import day_report, rules
from tests.test_hiring_routes_postgres import (  # noqa: F401 — клиент Flask на своей схеме, люди и вакансия
    HR_HEAD, OP_HEAD, PORT, REC1, REC2, TRAINER, as_user, client,
)

pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (HIRING_TEST_PORT)')


def test_day_report_scope_notes_export_and_cards(client):
    boot = client.get('/api/hiring', headers=as_user(REC1)).get_json()
    manual = next(s for s in boot['dictionaries']['sources'] if s['code'] == 'manual')
    created = client.post('/api/hiring/cards', json={'phone': '8 705 321 00 01', 'full_name': 'Дана', 'source_id': manual['id'],
                                                     'vacancy_id': client.world['vacancy_id']}, headers=as_user(REC1)).get_json()['card']
    assert client.post('/api/hiring/cards/%s/take' % created['id'], headers=as_user(REC1)).status_code == 200
    today = rules.now_almaty().date().isoformat()

    own = client.get('/api/hiring/reports/day', headers=as_user(REC1)).get_json()
    assert own['day'] == today and own['is_today'] and own['recruiter']['id'] == REC1 and own['can_note'] is True
    done = next(row for row in own['rows'] if row['key'] == 'done')['cells']
    assert done['manual'] == 1 and done['all'] == 1
    # Рекрутер не открывает чужой отчёт подстановкой recruiter_id — сервер держит его в своём.
    forced = client.get('/api/hiring/reports/day', query_string={'recruiter_id': REC2}, headers=as_user(REC1)).get_json()
    assert forced['recruiter']['id'] == REC1
    # Будущий день — отчёт за сегодня, мусор — тоже.
    future = (rules.now_almaty().date() + timedelta(days=3)).isoformat()
    assert client.get('/api/hiring/reports/day', query_string={'day': future}, headers=as_user(REC1)).get_json()['day'] == today
    assert client.get('/api/hiring/reports/day', query_string={'day': 'вчера'}, headers=as_user(REC1)).get_json()['day'] == today

    team = client.get('/api/hiring/reports/day', headers=as_user(HR_HEAD)).get_json()
    assert team['recruiter'] is None and team['can_note'] is True and [p['id'] for p in team['people']] == [REC1]
    chosen = client.get('/api/hiring/reports/day', query_string={'recruiter_id': REC1}, headers=as_user(HR_HEAD)).get_json()
    assert chosen['recruiter']['id'] == REC1 and chosen['can_note'] is False      # чужой отчёт — только читать
    assert client.get('/api/hiring/reports/day', headers=as_user(TRAINER)).status_code == 403
    assert client.get('/api/hiring/reports/day', headers=as_user(OP_HEAD)).status_code == 403

    # «Другая работа»: своя запись за день; будущее и мусор — нет; чужой не пишет.
    saved = client.put('/api/hiring/reports/day/note', json={'day': today, 'body': 'Обновила вакансии на hh'}, headers=as_user(REC1))
    assert saved.status_code == 200 and saved.get_json()['note']['body'] == 'Обновила вакансии на hh'
    notes = client.get('/api/hiring/reports/day', headers=as_user(HR_HEAD)).get_json()['notes']
    assert [(n['user_id'], n['body'], n['mine']) for n in notes] == [(REC1, 'Обновила вакансии на hh', False)]
    assert client.get('/api/hiring/reports/day', headers=as_user(REC1)).get_json()['notes'][0]['mine'] is True
    assert client.put('/api/hiring/reports/day/note', json={'day': future, 'body': 'x'}, headers=as_user(REC1)).status_code == 400
    assert client.put('/api/hiring/reports/day/note', json={'day': 'завтра', 'body': 'x'}, headers=as_user(REC1)).status_code == 400
    assert client.put('/api/hiring/reports/day/note', json={'day': today, 'body': ['x']}, headers=as_user(REC1)).status_code == 400
    too_long = 'а' * (day_report.NOTE_MAX + 1)
    assert client.put('/api/hiring/reports/day/note', json={'day': today, 'body': too_long}, headers=as_user(REC1)).status_code == 400
    assert client.put('/api/hiring/reports/day/note', json={'day': today, 'body': 'x'}, headers=as_user(TRAINER)).status_code == 403
    cleared = client.put('/api/hiring/reports/day/note', json={'day': today, 'body': ''}, headers=as_user(REC1))
    assert cleared.status_code == 200 and cleared.get_json()['note'] is None
    assert client.get('/api/hiring/reports/day', headers=as_user(REC1)).get_json()['notes'] == []

    export = client.get('/api/hiring/reports/day/export', headers=as_user(HR_HEAD))
    assert export.status_code == 200 and export.data[:2] == b'PK'
    assert "filename*=UTF-8''" in export.headers['Content-Disposition']
    from openpyxl import load_workbook
    sheet = load_workbook(io.BytesIO(export.data))['Отчёт за день']
    assert str(sheet['A1'].value).endswith('· Все рекрутеры')

    cards = client.get('/api/hiring/reports/day/cards', query_string={'key': 'done:manual'}, headers=as_user(REC1)).get_json()
    assert cards['total'] == 1 and cards['items'][0]['id'] == created['id']
    assert client.get('/api/hiring/reports/day/cards', headers=as_user(REC1)).status_code == 400
    # Рекрутер не раскрывает чужую цифру через recruiter_id: его список — по нему самому.
    other = client.get('/api/hiring/reports/day/cards', query_string={'key': 'done:all', 'recruiter_id': REC2},
                       headers=as_user(REC1)).get_json()
    assert other['total'] == 1
