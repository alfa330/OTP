# -*- coding: utf-8 -*-
"""«Дисциплина»: ручки Blueprint целиком — Flask test client, настоящий Postgres,
хранилище файлов подменено словарём. DISCIPLINE_TEST_PORT — порт кластера.

Ловит то, чего не видят тесты сервиса: разбор формы и файлов, коды ответов,
сериализацию дат и сумм в JSON, выгрузки xlsx и DOCX, QR-замок рядовых.

    DISCIPLINE_TEST_PORT=55461 python -m pytest tests/test_discipline_routes_postgres.py
"""
import io
import os
import uuid
import zipfile
from datetime import datetime, timedelta

import pytest

from tests.test_discipline_postgres import (  # noqa: F401 — фикстура db
    ADMIN, HEAD_SZOV, OP_A, OP_B, SUPER, SV_A, SV_B, SZOV, TRAINEE_A, PORT, db, section_open_for_all,
    use_sample_ladder)

flask = pytest.importorskip('flask')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DISCIPLINE_TEST_PORT)')

PDF = b'%PDF-1.4\n' + b'0' * 200
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 200


class _Blob:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def upload_from_string(self, data, content_type=None):
        self.store[self.path] = data

    def exists(self):
        return self.path in self.store

    def download_as_bytes(self):
        return self.store[self.path]

    def delete(self):
        self.store.pop(self.path, None)


class _Bucket:
    def __init__(self, store):
        self.store = store

    def blob(self, path):
        return _Blob(self.store, path)


class _Client:
    def __init__(self, store):
        self.store = store

    def bucket(self, _name):
        return _Bucket(self.store)


class _Sqlite:
    """db._get_cursor() поверх тестовой схемы."""

    def __init__(self, base):
        self.base = base

    def _get_cursor(self):
        return self.base.cursor()


@pytest.fixture
def client(db):
    from flask import Flask, request

    from discipline.routes import build_discipline_blueprint

    store = {}
    sent = []
    granted = {'value': False}

    def resolve_requester():
        user_id = request.headers.get('X-User-Id')
        if not user_id:
            return None, None, ('Unauthorized', 401)
        return int(user_id), None, None

    app = Flask(__name__)
    app.register_blueprint(build_discipline_blueprint(
        db=_Sqlite(db), require_api_key=lambda fn: fn,
        build_cors_preflight_response=lambda: ('', 204), resolve_requester=resolve_requester,
        sensitive_access_granted=lambda _uid: granted['value'],
        send_telegram=lambda chat_id, text, **kw: sent.append((chat_id, text)),
        web_app_base_url='https://portal.example', gcs={'bucket_name': lambda: 'bucket',
                                                         'client': lambda: _Client(store)}))
    test_client = app.test_client()
    test_client.store = store
    test_client.sent = sent
    test_client.granted = granted
    return test_client


def h(user_id):
    return {'X-User-Id': str(user_id)}


def _type_id(client, user_id, operator_id, title='Сон на рабочем месте'):
    response = client.get('/api/discipline/operator-context?operator_id=%d' % operator_id, headers=h(user_id))
    assert response.status_code == 200, response.get_json()
    return next(t['id'] for t in response.get_json()['types'] if t['title'] == title)


def _create(client, user_id, operator_id, at, files=None, **extra):
    data = {'operator_id': str(operator_id), 'type_id': str(_type_id(client, user_id, operator_id)),
            'occurred_at': at.strftime('%Y-%m-%dT%H:%M'), 'shift_label': '09:00–18:00',
            'description': 'Оператор спал на рабочем месте во время смены, видно по камере.'}
    data.update(extra)
    if files:
        data['files'] = files
    return client.post('/api/discipline/records', data=data, headers=h(user_id),
                       content_type='multipart/form-data')


def test_gates(client):
    assert client.get('/api/discipline/ping').status_code == 401
    response = client.get('/api/discipline/ping', headers=h(ADMIN))
    assert response.status_code == 403 and response.get_json()['code'] == 'DISCIPLINE_SECTION_CLOSED'
    # Рядовой: ping без QR (только права и число своих записей), записи — после QR.
    payload = client.get('/api/discipline/ping', headers=h(OP_A)).get_json()
    assert payload['capabilities']['scope'] == 'own' and payload['own_total'] == 0
    assert payload['qr_granted'] is False
    response = client.get('/api/discipline/records', headers=h(OP_A))
    assert response.status_code == 403 and response.get_json()['code'] == 'SENSITIVE_ACCESS_REQUIRED'
    client.granted['value'] = True
    assert client.get('/api/discipline/records', headers=h(OP_A)).status_code == 200
    payload = client.get('/api/discipline/ping', headers=h(HEAD_SZOV)).get_json()
    assert payload['capabilities']['can_settings'] and payload['departments'][0]['id'] == SZOV
    assert client.get('/api/discipline/settings?department_id=%d' % SZOV, headers=h(SV_A)).status_code == 403


def test_full_flow(client, db):
    now = datetime.now().replace(second=0, microsecond=0)
    # Лестница со скриншота: тренинг → объясн. №1 → №2 → №3 + акт — выбрана до первой записи.
    with db.cursor() as cur:
        use_sample_ladder(cur)
    response = _create(client, SV_A, OP_A, now - timedelta(days=3),
                       files=[(io.BytesIO(PNG), 'фото.png')])
    assert response.status_code == 201, response.get_json()
    first = response.get_json()['id']
    assert response.get_json()['number'].startswith('Д-')
    assert len(client.store) == 1
    # Неверный файл: расширение и содержимое спорят — ничего не пишется и не остаётся в хранилище.
    bad = _create(client, SV_A, OP_A, now - timedelta(days=2), files=[(io.BytesIO(b'MZ' + b'0' * 50), 'акт.pdf')])
    assert bad.status_code == 400 and len(client.store) == 1

    second = _create(client, SV_A, OP_A, now - timedelta(days=2)).get_json()['id']
    journal = client.get('/api/discipline/records', headers=h(SV_A)).get_json()
    assert [i['id'] for i in journal['items']] == [second, first]
    assert journal['items'][0]['step_title'] == 'Объясн. №1'
    # СВ чужой группы этих записей не видит.
    assert client.get('/api/discipline/records', headers=h(SV_B)).get_json()['items'] == []
    assert client.get('/api/discipline/records/%d' % first, headers=h(SV_B)).status_code == 403

    card = client.get('/api/discipline/records/%d' % first, headers=h(SV_A)).get_json()
    assert card['files'][0]['kind'] == 'evidence' and card['history'][0]['action'] == 'create_record'
    file_id = card['files'][0]['id']
    preview = client.get('/api/discipline/files/%d?inline=1' % file_id, headers=h(SV_A))
    assert preview.status_code == 200 and preview.data == PNG
    assert preview.headers['Content-Disposition'].startswith('inline')
    assert preview.headers['X-Content-Type-Options'] == 'nosniff'

    # Оператор загружает объяснительную сам — и видит только свои поля.
    client.granted['value'] = True
    own = client.get('/api/discipline/records', headers=h(OP_A)).get_json()['items']
    assert {i['id'] for i in own} == {first, second} and 'description' not in own[0]
    response = client.post('/api/discipline/records/%d/explanation' % second, headers=h(OP_A),
                           data={'file': (io.BytesIO(PDF), 'объяснительная.pdf')},
                           content_type='multipart/form-data')
    assert response.status_code == 200, response.get_json()
    # Чужое доказательство оператору не отдаётся.
    assert client.get('/api/discipline/files/%d' % file_id, headers=h(OP_A)).status_code == 403
    item = client.get('/api/discipline/records/%d' % second, headers=h(SV_A)).get_json()
    assert item['state'] == 'explanation_received'

    # Тренинг на первой ступени.
    response = client.post('/api/discipline/records/%d/training' % first, headers=h(SV_A), json={
        'date': (now - timedelta(days=1)).strftime('%Y-%m-%d'), 'start': '10:00', 'end': '10:30'})
    assert response.status_code == 200, response.get_json()
    response = client.post('/api/discipline/records/%d/close' % first, headers=h(SV_A))
    assert response.status_code == 200, response.get_json()

    third = _create(client, SV_A, OP_A, now - timedelta(days=1)).get_json()['id']
    fourth = _create(client, SV_A, OP_A, now - timedelta(hours=5)).get_json()['id']
    item = client.get('/api/discipline/records/%d' % fourth, headers=h(HEAD_SZOV)).get_json()
    assert item['action'] == 'act' and item['act_full']['number'].startswith('А-')
    act_id = item['act_full']['id']
    summary = client.get('/api/discipline/summary', headers=h(HEAD_SZOV)).get_json()
    # Черновик, заведённый системой, — ещё не составленный акт.
    assert summary['controls'][0]['operator_id'] == OP_A and summary['counters']['acts'] == 0

    draft = client.get('/api/discipline/acts/%d/draft.docx' % act_id, headers=h(SV_A))
    assert draft.status_code == 200 and zipfile.is_zipfile(io.BytesIO(draft.data))
    assert client.post('/api/discipline/acts/%d/file' % act_id, headers=h(SV_A),
                       data={'file': (io.BytesIO(PDF), 'акт.pdf')},
                       content_type='multipart/form-data').status_code == 403
    response = client.post('/api/discipline/acts/%d/file' % act_id, headers=h(HEAD_SZOV),
                           data={'file': (io.BytesIO(PDF), 'акт.pdf')}, content_type='multipart/form-data')
    assert response.status_code == 200, response.get_json()
    summary = client.get('/api/discipline/summary', headers=h(HEAD_SZOV)).get_json()
    assert summary['counters']['acts'] == 1
    response = client.patch('/api/discipline/acts/%d' % act_id, headers=h(HEAD_SZOV), json={'approve': True})
    assert response.status_code == 400 and response.get_json()['code'] == 'DECISION_MEASURE'  # решения ещё нет
    measures = client.get('/api/discipline/controls', headers=h(HEAD_SZOV)).get_json()['measures']
    fine = next(m['id'] for m in measures if m['title'] == 'Лишение переменной части (фиксированная сумма)')
    # Решение с деньгами — без суммы не утверждается: ошибка встаёт у поля суммы.
    response = client.patch('/api/discipline/acts/%d' % act_id, headers=h(HEAD_SZOV),
                            json={'decision_measure_id': fine, 'approve': True})
    assert response.status_code == 400 and response.get_json()['code'] == 'MEASURE_AMOUNT'
    assert client.patch('/api/discipline/acts/%d' % act_id, headers=h(HEAD_SZOV),
                        json={'decision_measure_id': fine, 'decision_amount': '20000',
                              'approve': True}).status_code == 200
    # Мера решением по акту — в выгрузке на удержания и во вкладке «Меры» карточки.
    report_rows = client.get('/api/discipline/reports/measures', headers=h(HEAD_SZOV)).get_json()['rows']
    assert [(r['basis'], r['measure_amount']) for r in report_rows] == [
        ('Акт %s' % item['act_full']['number'], 20000.0)]

    # Отчёты и выгрузки — JSON и xlsx.
    for kind in ('operators', 'types', 'supervisors', 'groups', 'measures', 'controls'):
        response = client.get('/api/discipline/reports/%s' % kind, headers=h(HEAD_SZOV))
        assert response.status_code == 200, (kind, response.get_json())
        xlsx = client.get('/api/discipline/reports/%s?format=xlsx' % kind, headers=h(HEAD_SZOV))
        assert xlsx.status_code == 200 and zipfile.is_zipfile(io.BytesIO(xlsx.data))
    export = client.get('/api/discipline/records/export', headers=h(SV_A))
    assert export.status_code == 200 and zipfile.is_zipfile(io.BytesIO(export.data))
    audit = client.get('/api/discipline/audit', headers=h(HEAD_SZOV)).get_json()
    actions = {row['action'] for row in audit['items']}
    assert {'create_record', 'view_file', 'upload_explanation', 'upload_act', 'export'} <= actions
    assert client.get('/api/discipline/audit', headers=h(SV_A)).status_code == 403

    card = client.get('/api/discipline/operators/%d' % OP_A, headers=h(HEAD_SZOV)).get_json()
    assert card['control'] and len(card['records']) == 4 and card['documents']
    assert {third, fourth} <= {r['id'] for r in card['records']}
    assert [m['measure_amount'] for m in card['measures']] == [20000.0]


def test_settings_roundtrip(client):
    payload = client.get('/api/discipline/settings?department_id=%d' % SZOV, headers=h(HEAD_SZOV)).get_json()
    assert payload['settings']['ladder'] == ['explanation', 'explanation', 'act']
    response = client.put('/api/discipline/settings', headers=h(HEAD_SZOV), json={
        'department_id': SZOV, 'settings': {'ladder': ['training', 'explanation', 'explanation', 'act'],
                                            'period_days': 45}})
    assert response.status_code == 200 and response.get_json()['settings']['period_days'] == 45
    bad = client.put('/api/discipline/settings', headers=h(HEAD_SZOV), json={
        'department_id': SZOV, 'settings': {'ladder': ['nap']}})
    assert bad.status_code == 400
    response = client.post('/api/discipline/types', headers=h(HEAD_SZOV), json={
        'department_id': SZOV, 'title': 'Курение в неположенном месте', 'severity': 'light', 'weight': 1,
        'needs_explanation': True})
    assert response.status_code == 200
    duplicate = client.post('/api/discipline/types', headers=h(HEAD_SZOV), json={
        'department_id': SZOV, 'title': 'курение в неположенном месте', 'severity': 'light', 'weight': 1})
    assert duplicate.status_code == 400 and 'уже есть' in duplicate.get_json()['error']
    response = client.post('/api/discipline/settings/template', headers=h(HEAD_SZOV),
                           data={'department_id': str(SZOV), 'file': (io.BytesIO(b'PK\x03\x04' + b'0' * 50),
                                                                       'шаблон.docx')},
                           content_type='multipart/form-data')
    assert response.status_code == 200, response.get_json()


def test_repeated_submission_returns_the_same_record(client, db):
    """Ответ на «Зафиксировать» потерялся в сети, СВ нажал ещё раз: та же метка
    формы — та же запись. Второе нарушение лестницу бы сдвинуло."""
    now = datetime.now().replace(second=0, microsecond=0)
    token = 'f0e1d2c3-b4a5-4697-8899-aabbccddeeff'
    first = _create(client, SV_A, OP_A, now - timedelta(hours=2), client_token=token,
                    files=[(io.BytesIO(PNG), 'фото.png')])
    assert first.status_code == 201, first.get_json()
    again = _create(client, SV_A, OP_A, now - timedelta(hours=2), client_token=token,
                    files=[(io.BytesIO(PNG), 'фото.png')])
    assert again.status_code == 200, again.get_json()
    assert again.get_json()['id'] == first.get_json()['id']
    assert again.get_json()['number'] == first.get_json()['number']
    # Копия файла из повтора в хранилище не осталась.
    assert len(client.store) == 1
    # Форму после сбоя переписали на другого человека — это уже другая запись.
    other = _create(client, SV_A, TRAINEE_A, now - timedelta(hours=1), client_token=token)
    assert other.status_code == 201 and other.get_json()['id'] != first.get_json()['id']
    with db.cursor() as cur:
        cur.execute('SELECT operator_id, client_token FROM discipline_records ORDER BY id')
        assert cur.fetchall() == [(OP_A, token), (TRAINEE_A, None)]


# ── Человек между отделами: что видит руководитель ───────────────────────────

def _cross_department_act(db):
    """Две записи заведены в ОП, человека перевели в СЗоВ, третья (акт) — в СЗоВ,
    затем человек ушёл в Тез. Глава СЗоВ видит основание акта, но не записи ОП."""
    from tests.test_discipline_postgres import NOW as T0, OP as OP_DEPT, TODAY as D0, create

    with db.cursor() as cur:
        r1 = create(cur, 200, 402, T0 - timedelta(days=10), department=OP_DEPT)
        r2 = create(cur, 200, 402, T0 - timedelta(days=8), department=OP_DEPT)
        cur.execute('UPDATE users SET department_id = %s WHERE id = 402', (SZOV,))
        cur.execute('UPDATE group_operator_memberships SET end_date = %s WHERE operator_id = 402',
                    (D0 - timedelta(days=6),))
        cur.execute('INSERT INTO group_operator_memberships (group_id, operator_id, start_date) VALUES (10, 402, %s)',
                    (D0 - timedelta(days=5),))
        r3 = create(cur, HEAD_SZOV, 402, T0 - timedelta(days=2))
        cur.execute('UPDATE users SET department_id = 3 WHERE id = 402')
        cur.execute('UPDATE group_operator_memberships SET end_date = %s WHERE operator_id = 402 AND end_date IS NULL',
                    (D0 - timedelta(days=1),))
        cur.execute('SELECT id, number FROM discipline_records ORDER BY id')
        numbers = dict(cur.fetchall())
        cur.execute('SELECT id FROM discipline_acts WHERE trigger_record_id = %s', (r3,))
        act_id = cur.fetchone()[0]
    return r1, r2, r3, act_id, numbers


def test_hidden_act_records_are_stubs_but_the_act_draft_lists_all(client, db):
    r1, r2, r3, act_id, numbers = _cross_department_act(db)
    assert client.get('/api/discipline/records/%d' % r1, headers=h(HEAD_SZOV)).status_code == 403
    item = client.get('/api/discipline/records/%d' % r3, headers=h(HEAD_SZOV)).get_json()
    records = item['act_full']['records']
    assert sorted(r['id'] for r in records if r.get('hidden')) == sorted([r1, r2])
    assert all(set(r) == {'id', 'number', 'occurred_at', 'hidden'} for r in records if r.get('hidden'))
    # Черновик акта — документ по всем нарушениям периода: в нём только номер,
    # дата и тип, без описаний, поэтому записи вне доступа в нём остаются.
    from docx import Document
    draft = client.get('/api/discipline/acts/%d/draft.docx' % act_id, headers=h(HEAD_SZOV))
    assert draft.status_code == 200
    document = Document(io.BytesIO(draft.data))
    text = '\n'.join([p.text for p in document.paragraphs]
                     + [cell.text for table in document.tables for row in table.rows for cell in row.cells])
    assert all(numbers[rid] in text for rid in (r1, r2, r3))


def test_card_does_not_show_other_departments_control(client, db):
    from tests.test_discipline_postgres import NOW as T0, create

    with db.cursor() as cur:
        create(cur, HEAD_SZOV, OP_A, T0 - timedelta(days=3))
        cur.execute('UPDATE users SET department_id = 2 WHERE id = %s', (OP_A,))
        cur.execute('UPDATE group_operator_memberships SET end_date = %s WHERE operator_id = %s',
                    (T0.date() - timedelta(days=1), OP_A))
        cur.execute('INSERT INTO group_operator_memberships (group_id, operator_id, start_date) VALUES (20, %s, %s)',
                    (OP_A, T0.date()))
    response = client.post('/api/discipline/controls', headers=h(200), json={
        'operator_id': OP_A, 'source': 'manual', 'reason': 'Решение главы ОП',
        'end_date': (T0.date() + timedelta(days=10)).isoformat()})
    assert response.status_code in (200, 201), response.get_json()
    card = client.get('/api/discipline/operators/%d' % OP_A, headers=h(HEAD_SZOV)).get_json()
    assert card['control'] is None and card['controls'] == []
    assert len(card['records']) == 1


def test_record_payload_brings_its_departments_dictionaries(client):
    now = datetime.now().replace(second=0, microsecond=0)
    rid = _create(client, SV_A, OP_A, now - timedelta(days=1)).get_json()['id']
    item = client.get('/api/discipline/records/%d' % rid, headers=h(HEAD_SZOV)).get_json()
    assert item['measures'] and {m['department_id'] for m in item['measures']} == {SZOV}
    assert item['types'] and {t['department_id'] for t in item['types']} == {SZOV}
    # Оператору справочники не отдаются.
    client.granted['value'] = True
    own = client.get('/api/discipline/records/%d' % rid, headers=h(OP_A)).get_json()
    assert 'measures' not in own and 'types' not in own


def test_deductions_by_approval_date_through_the_route(client, db):
    from tests.test_discipline_postgres import NOW as T0, TODAY as D0, create, ctx_of, measure_id
    from discipline import service as svc

    with db.cursor() as cur:
        for day in (20, 22, 24):
            create(cur, HEAD_SZOV, OP_A, datetime(2026, 9, day, 10, 0))
        fine = measure_id(cur, SZOV, 'Лишение переменной части (фиксированная сумма)')
        rid = create(cur, SV_A, OP_A, datetime(2026, 9, 28, 10, 0), measure_id=fine, measure_amount='15000')
        head = ctx_of(cur, HEAD_SZOV)
        svc.approve_measure(cur, head, svc.load_record(cur, head, rid, D0, for_update=True),
                            now=datetime(2026, 10, 5, 11, 0))
        control_id = svc.create_control(cur, head, {
            'operator_id': OP_B, 'source': 'manual', 'reason': 'Решение руководителя', 'end_date': D0.isoformat()},
            now=T0, today=D0)
        svc.decide_control(cur, head, control_id, {'decision': 'measure', 'measure_id': fine,
                                                   'measure_amount': '20000'}, now=datetime(2026, 10, 6, 9, 0),
                           today=D0)
    september = client.get('/api/discipline/reports/measures?date_from=2026-09-01&date_to=2026-09-30',
                           headers=h(HEAD_SZOV)).get_json()
    october = client.get('/api/discipline/reports/measures?date_from=2026-10-01&date_to=2026-10-31',
                         headers=h(HEAD_SZOV)).get_json()
    assert september['rows'] == []
    assert [(r['basis'].split()[0], r['measure_amount']) for r in october['rows']] == [
        ('Нарушение', 15000.0), ('Решение', 20000.0)]
    assert october['columns'][0]['key'] == 'approved_at'


def test_act_records_in_card_follow_time_not_number(client):
    """Запись задним числом встаёт в списке акта на своё место по времени — как
    в черновике DOCX, — а не в конец по номеру."""
    now = datetime.now().replace(second=0, microsecond=0)
    first = _create(client, SV_A, OP_A, now - timedelta(days=2)).get_json()['id']
    second = _create(client, SV_A, OP_A, now - timedelta(days=1)).get_json()['id']
    backdated = _create(client, SV_A, OP_A, now - timedelta(days=5)).get_json()['id']
    item = client.get('/api/discipline/records/%d' % second, headers=h(HEAD_SZOV)).get_json()
    assert [r['id'] for r in item['act_full']['records']] == [backdated, first, second]


def test_shift_and_timecode_are_times_only(client):
    """Смена и тайм-код — только время: набранное приводится к одному виду,
    свободный текст получает отказ с кодом поля, а не ложится в запись."""
    at = datetime.now().replace(second=0, microsecond=0) - timedelta(days=1)
    for extra, code in (({'shift_label': '1234'}, 'SHIFT_FORMAT'), ({'evidence_note': 'видео 10:15'}, 'EVIDENCE_NOTE')):
        refused = _create(client, SV_A, OP_A, at, **extra)
        assert (refused.status_code, refused.get_json()['code']) == (400, code)
    created = _create(client, SV_A, OP_A, at, shift_label='20:00-8:00', evidence_note='14:32-14:41; 15:05')
    assert created.status_code == 201, created.get_json()
    item = client.get('/api/discipline/records/%d' % created.get_json()['id'], headers=h(SV_A)).get_json()
    assert (item['shift_label'], item['evidence_note']) == ('20:00–08:00', '14:32–14:41, 15:05')


def test_timecode_alone_is_evidence(client, db):
    type_id = _type_id(client, SV_A, OP_A)   # справочник отдела заводится первым запросом
    with db.cursor() as cur:
        cur.execute('UPDATE discipline_types SET needs_evidence = TRUE WHERE id = %s', (type_id,))
        assert cur.rowcount == 1
    at = datetime.now().replace(second=0, microsecond=0) - timedelta(days=1)
    missing = _create(client, SV_A, OP_A, at)
    assert (missing.status_code, missing.get_json()['code']) == (400, 'EVIDENCE')
    assert _create(client, SV_A, OP_A, at, evidence_note='14:32').status_code == 201


def test_edit_keeps_old_free_text_but_checks_new(client, db):
    """Запись со сменой и тайм-кодом свободным текстом (до полей времени)
    правится, не трогая их; новое значение — только время."""
    at = datetime.now().replace(second=0, microsecond=0) - timedelta(days=1)
    record_id = _create(client, SV_A, OP_A, at).get_json()['id']
    with db.cursor() as cur:
        cur.execute("UPDATE discipline_records SET shift_label = 'Линия 2', evidence_note = 'видео 10:15'"
                    " WHERE id = %s", (record_id,))
    url = '/api/discipline/records/%d' % record_id
    kept = client.patch(url, headers=h(SV_A), json={
        'shift_label': 'Линия 2', 'evidence_note': 'видео 10:15',
        'description': 'Оператор спал на рабочем месте около двадцати минут, видно по камере зала.'})
    assert kept.status_code == 200, kept.get_json()
    for body, code in (({'shift_label': 'с утра'}, 'SHIFT_FORMAT'), ({'evidence_note': 'видео 11:00'}, 'EVIDENCE_NOTE')):
        refused = client.patch(url, headers=h(SV_A), json=body)
        assert (refused.status_code, refused.get_json()['code']) == (400, code)
    assert client.patch(url, headers=h(SV_A), json={'shift_label': '10:00-19:00', 'evidence_note': '10:15'}).status_code == 200
    item = client.get(url, headers=h(SV_A)).get_json()
    assert (item['shift_label'], item['evidence_note']) == ('10:00–19:00', '10:15')


def test_rollout_only_super_admins(client, db, monkeypatch):
    """Пока раздел открыт одним супер-админам (владелец, 20.10.2026): остальным —
    отказ на ручках и пусто в колоколе, а пробная запись на живого оператора не
    шлёт ни колокола, ни Telegram ни ему, ни его СВ, ни главе отдела."""
    from discipline import access
    from notifications import sources

    monkeypatch.setattr(access, 'SUPER_ADMIN_ONLY', True)
    for user in (ADMIN, HEAD_SZOV, SV_A, OP_A, TRAINEE_A):
        refused = client.get('/api/discipline/ping', headers=h(user))
        assert (refused.status_code, refused.get_json()['code']) == (403, 'DISCIPLINE_SECTION_CLOSED'), user
    assert client.get('/api/discipline/ping', headers=h(SUPER)).status_code == 200
    created = _create(client, SUPER, OP_A, datetime.now().replace(second=0, microsecond=0) - timedelta(days=1))
    assert created.status_code == 201, created.get_json()
    with db.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM discipline_notifications')
        assert cur.fetchone()[0] == 0
        assert sources.discipline(cur, {'user_id': SV_A}, 10) == (0, [])
    assert client.sent == []
    # Флаг снят — те же люди снова по правилам ТЗ.
    monkeypatch.setattr(access, 'SUPER_ADMIN_ONLY', False)
    assert client.get('/api/discipline/ping', headers=h(SV_A)).status_code == 200

