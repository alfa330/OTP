# -*- coding: utf-8 -*-
"""«Дресс-код» (#418) на настоящем Postgres: DRESS_CODE_TEST_PORT — порт локального кластера.

Настоящие схема и запросы, сценарии сервиса и ручек поверх них: приём фото (годный кадр,
плохой кадр и вторая попытка, ИИ недоступен и повтор фоном), одно первое фото в день,
решения руководителя с журналом, повторное нарушение за 10 дней, «Ознакомлен», граница
отдела, «не прислали фото», чистка через месяц. ИИ, бакет и Telegram подставные.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным именем,
в конце она удаляется. Люди выдуманы.

    DRESS_CODE_TEST_PORT=55491 python -m pytest tests/test_dress_code_postgres.py
"""

import contextlib
import io
import os
import uuid
from datetime import date, datetime, time as dt_time, timedelta

import pytest

from dress_code import AI_OK, AI_PENDING, AI_UNCLEAR, AI_VIOLATION, QUALITY_HINTS, report, schema
from dress_code import routes as dc_routes
from dress_code import service
from dress_code.ai import DressCodeAIError

PORT = os.environ.get('DRESS_CODE_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DRESS_CODE_TEST_PORT)')

FRONT, OTHER = 909, 367
SUPER, HEAD, ANNA, BORIS, TRAINEE, SV, STRANGER, FIRED = 1, 532, 30, 31, 32, 33, 50, 60
TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 10, 0)

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text DEFAULT 'working', department_id integer, city text,
                        telegram_id bigint);
    CREATE TABLE work_shifts (id serial PRIMARY KEY, operator_id integer NOT NULL, shift_date date NOT NULL,
                              start_time time NOT NULL, end_time time NOT NULL,
                              shift_type text NOT NULL DEFAULT 'regular');
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        (909, 'front_office', 'Фронт офисы', 532), (367, 'op', 'Отдел продаж', NULL);
    INSERT INTO users (id, name, role, status, department_id, city, telegram_id) VALUES
        (1, 'Супер Админов', 'super_admin', 'working', NULL, NULL, NULL),
        (532, 'Глава Фронта', 'admin', 'working', 909, NULL, 700532),
        (30, 'Анна Фронт', 'operator', 'working', 909, 'Алматы', 700030),
        (31, 'Борис Фронт', 'operator', 'working', 909, 'Астана', NULL),
        (32, 'Стажёр Фронта', 'trainee', 'working', 909, 'Алматы', NULL),
        (33, 'Старший Фронта', 'sv', 'working', 909, 'Алматы', NULL),
        (50, 'Чужой Продавец', 'operator', 'working', 367, 'Алматы', NULL),
        (60, 'Уволенный Фронт', 'operator', 'fired', 909, 'Алматы', NULL);
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '20s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def _get_cursor(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


class FakeBlob:
    def __init__(self, store, bucket, path):
        self.store, self.bucket, self.path = store, bucket, path
        self.cache_control = None

    def upload_from_string(self, data, content_type=None):
        self.store[(self.bucket, self.path)] = data

    def download_as_bytes(self):
        return self.store[(self.bucket, self.path)]

    def delete(self):
        if (self.bucket, self.path) not in self.store:
            raise RuntimeError('404 not found')
        del self.store[(self.bucket, self.path)]

    def generate_signed_url(self, **kwargs):
        return 'https://signed/%s/%s' % (self.bucket, self.path)


class FakeGcs:
    def __init__(self):
        self.store = {}

    def bucket(self, name):
        store = self.store

        class _Bucket:
            def blob(self, path):
                return FakeBlob(store, name, path)
        return _Bucket()

    def as_factory(self):
        return {'bucket_name': lambda: 'test-bucket', 'client': lambda: self}


def _jpeg(width=480, height=640):
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (width, height), (90, 110, 140)).save(out, 'JPEG')
    return out.getvalue()


def _ai(verdict='ok', quality='ok', violations=None, note=''):
    return {'quality': quality, 'verdict': verdict, 'visible': ['рубашка'], 'violations': violations or [],
            'remarks': [], 'note': note, 'model': 'test-model'}


class FakeAI:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, data, rules):
        self.calls += 1
        answer = self.answers.pop(0) if self.answers else _ai()
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture()
def db(monkeypatch):
    import psycopg2
    name = 'dress_code_' + uuid.uuid4().hex[:12]
    admin = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres', connect_timeout=3)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    store = _Db(name)
    monkeypatch.setattr(service, 'now_almaty', lambda: NOW)
    try:
        with store._get_cursor() as cursor:
            cursor.execute(BASE_DDL)
            schema.init_dress_code_schema(cursor)
            # Повторный разворот ничего не ломает и не плодит строк.
            schema.init_dress_code_schema(cursor)
        yield store
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)
        admin.close()


@pytest.fixture()
def gcs():
    return FakeGcs()


def _submit(db, gcs, user_id, answer=None, **kwargs):
    return service.submit(db, gcs.as_factory(), user_id, _jpeg(), analyze=answer or FakeAI(_ai()), **kwargs)


def _sql(db, query, params=()):
    with db._get_cursor() as cursor:
        cursor.execute(query, params)
        try:
            return cursor.fetchall()
        except Exception:
            return None


def _insert_check(db, user_id, day, *, decision=None, ai_status=AI_VIOLATION, reasons=True):
    rows = _sql(db, """
        INSERT INTO dress_code_checks (user_id, department_id, day, photo_bucket, photo_path,
                                       ai_status, ai_reasons, decision)
        VALUES (%s, 909, %s, 'test-bucket', %s, %s, %s, %s) RETURNING id
    """, (user_id, day, 'old/%s.jpg' % uuid.uuid4().hex, ai_status,
          '[{"sign": "обычное худи", "rule": "п. 1"}]' if reasons else '[]', decision))
    return rows[0][0]


# ── Схема и настройка ───────────────────────────────────────────────────────

def test_schema_seeds_front_office_and_one_rules_version(db):
    with db._get_cursor() as cursor:
        settings = service.load_settings(cursor)
        cursor.execute('SELECT COUNT(*) FROM dress_code_rules')
        versions = cursor.fetchone()[0]
    assert settings['department_ids'] == [FRONT]
    assert settings['roles'] == ['operator', 'trainee']
    assert versions == 1 and 'фирменные худи' in settings['rules']['body']


def test_who_must_photograph(db):
    with db._get_cursor() as cursor:
        settings = service.load_settings(cursor)
        flags = {uid: service.must_photograph(service.user_row(cursor, uid), settings)
                 for uid in (ANNA, TRAINEE, SV, HEAD, STRANGER, FIRED)}
    assert flags == {ANNA: True, TRAINEE: True, SV: False, HEAD: False, STRANGER: False, FIRED: False}


def test_rules_version_only_on_real_change_and_scope_is_cleaned(db):
    with db._get_cursor() as cursor:
        first = service.load_settings(cursor)['rules']['version']
        service.save_settings(cursor, SUPER, rules_body=service.load_settings(cursor)['rules']['body'])
        assert service.load_settings(cursor)['rules']['version'] == first
        saved = service.save_settings(cursor, SUPER, rules_body='Только деловой стиль.',
                                      department_ids=[FRONT, 999999, 'x'], roles=['operator', 'super_admin'])
    assert saved['rules']['version'] == first + 1
    assert saved['rules']['created_by'] == 'Супер Админов'
    assert saved['department_ids'] == [FRONT] and saved['roles'] == ['operator']


# ── Приём фото ──────────────────────────────────────────────────────────────

def test_good_photo_is_stored_and_closes_the_day(db, gcs):
    result = _submit(db, gcs, ANNA)
    assert result['retake'] is False
    check = result['check']
    assert check['ai_status'] == AI_OK and check['day'] == TODAY.isoformat() and check['city'] == 'Алматы'
    assert len(gcs.store) == 1
    with db._get_cursor() as cursor:
        state = service.my_state(cursor, gcs.as_factory(), ANNA)
    assert state['can_capture'] is False and state['today'][0]['status'] == 'ok'
    assert state['today'][0]['photo_url'].startswith('https://signed/')


def test_second_primary_photo_same_day_is_refused(db, gcs):
    _submit(db, gcs, ANNA)
    with pytest.raises(service.DressCodeError) as error:
        _submit(db, gcs, ANNA)
    assert error.value.status == 409
    assert len(gcs.store) == 1


def test_race_of_two_sends_keeps_one_photo_and_drops_the_loser_blob(db, gcs, monkeypatch):
    # Обе отправки прошли проверку «фото сегодня ещё нет» до того, как первая записалась.
    monkeypatch.setattr(service, '_target', lambda cursor, user_id, today, retake_of: None)
    _submit(db, gcs, ANNA)
    with pytest.raises(service.DressCodeError) as error:
        _submit(db, gcs, ANNA)
    assert error.value.code == 'DRESS_CODE_ALREADY_SENT'
    assert len(gcs.store) == 1
    assert _sql(db, 'SELECT COUNT(*) FROM dress_code_checks')[0][0] == 1


def test_bad_frame_asks_to_retake_then_saves_as_unclear(db, gcs):
    bad = _ai(verdict=AI_UNCLEAR, quality='head_cropped', note=QUALITY_HINTS['head_cropped'])
    first = _submit(db, gcs, ANNA, FakeAI(bad), attempt=1)
    assert first == {'retake': True, 'attempt': 1, 'quality': 'head_cropped', 'hint': QUALITY_HINTS['head_cropped']}
    assert gcs.store == {} and _sql(db, 'SELECT COUNT(*) FROM dress_code_checks')[0][0] == 0
    second = _submit(db, gcs, ANNA, FakeAI(bad), attempt=2)
    assert second['retake'] is False
    assert second['check']['ai_status'] == AI_UNCLEAR and second['check']['ai_quality'] == 'head_cropped'
    assert service.needs_review(second['check'])


def test_ai_down_keeps_photo_pending_and_background_finishes_it(db, gcs):
    result = _submit(db, gcs, ANNA, FakeAI(DressCodeAIError('HTTP 503')))
    assert result['check']['ai_status'] == AI_PENDING
    became = service.analyze_pending(db, gcs.as_factory(), analyze=FakeAI(
        _ai(verdict=AI_VIOLATION, violations=[{'sign': 'обычное худи', 'rule': 'п. 1'}])))
    assert [c['id'] for c in became] == [result['check']['id']]
    assert became[0]['ai_status'] == AI_VIOLATION and became[0]['rules_version'] == 1


def test_long_ai_outage_goes_to_manual_review(db, gcs):
    result = _submit(db, gcs, ANNA, FakeAI(DressCodeAIError('HTTP 503')))
    became = []
    for _ in range(10):
        became += service.analyze_pending(db, gcs.as_factory(), analyze=FakeAI(DressCodeAIError('down')))
    assert [c['id'] for c in became] == [result['check']['id']]
    assert became[0]['ai_status'] == AI_UNCLEAR and 'ручная проверка' in became[0]['ai_note']


def test_not_required_people_cannot_send(db, gcs):
    for user_id in (STRANGER, SV, FIRED):
        with pytest.raises(service.DressCodeError) as error:
            _submit(db, gcs, user_id)
        assert error.value.status == 403
    assert gcs.store == {}


def test_not_an_image_is_refused_before_ai(db, gcs):
    from dress_code.photos import PhotoError
    ai = FakeAI()
    with pytest.raises(PhotoError):
        service.submit(db, gcs.as_factory(), ANNA, b'<html>', analyze=ai)
    assert ai.calls == 0 and gcs.store == {}


# ── Решения руководителя ────────────────────────────────────────────────────

def _decide(db, check_id, decision, comment=None, department_ids=None):
    with db._get_cursor() as cursor:
        return service.decide(cursor, check_id, department_ids=department_ids, actor_id=HEAD,
                              actor_name='Глава Фронта', decision=decision, comment=comment)


def test_confirm_keeps_ai_result_writes_journal_and_tells_the_employee(db, gcs):
    check_id = _submit(db, gcs, ANNA, FakeAI(_ai(verdict=AI_VIOLATION, violations=[{'sign': 'майка'}])))['check']['id']
    outcome = _decide(db, check_id, 'confirmed', 'Переоденьтесь')
    check = outcome['check']
    assert check['decision'] == 'confirmed' and check['ai_status'] == AI_VIOLATION
    assert [e['kind'] for e in outcome['events']] == ['employee_confirmed']
    with db._get_cursor() as cursor:
        detail = service.check_detail(cursor, gcs.as_factory(), check_id)
    assert [(j['decision'], j['actor']) for j in detail['journal']] == [('confirmed', 'Глава Фронта')]
    # Повторное то же решение — в журнал, но второй раз сотруднику не пишем.
    assert _decide(db, check_id, 'confirmed')['events'] == []


def test_confirm_without_ai_reasons_needs_a_comment(db, gcs):
    check_id = _submit(db, gcs, ANNA)['check']['id']
    with pytest.raises(service.DressCodeError) as error:
        _decide(db, check_id, 'confirmed')
    assert error.value.code == 'DRESS_CODE_COMMENT_REQUIRED'
    assert _decide(db, check_id, 'confirmed', 'Худи без логотипа')['check']['decision'] == 'confirmed'


def test_repeat_within_ten_days(db, gcs):
    _insert_check(db, ANNA, TODAY - timedelta(days=3), decision='confirmed')
    _insert_check(db, BORIS, TODAY - timedelta(days=11), decision='confirmed')
    anna = _submit(db, gcs, ANNA, FakeAI(_ai(verdict=AI_VIOLATION, violations=[{'sign': 'майка'}])))['check']['id']
    boris = _submit(db, gcs, BORIS, FakeAI(_ai(verdict=AI_VIOLATION, violations=[{'sign': 'майка'}])))['check']['id']
    anna_outcome = _decide(db, anna, 'confirmed')
    boris_outcome = _decide(db, boris, 'confirmed')
    assert anna_outcome['check']['is_repeat'] is True
    assert [e['kind'] for e in anna_outcome['events']] == ['employee_confirmed', 'repeat']
    assert boris_outcome['check']['is_repeat'] is False
    # Отмена снимает признак повторного.
    assert _decide(db, anna, 'no_violation')['check']['is_repeat'] is False


def test_late_decision_marks_the_later_violation_as_repeat(db, gcs):
    earlier = _insert_check(db, ANNA, TODAY - timedelta(days=5))
    later = _insert_check(db, ANNA, TODAY - timedelta(days=2), decision='confirmed')
    _decide(db, earlier, 'confirmed')
    assert _sql(db, 'SELECT is_repeat FROM dress_code_checks WHERE id = %s', (later,))[0][0] is True


def test_acknowledge_only_confirmed_and_only_own(db, gcs):
    check_id = _submit(db, gcs, ANNA, FakeAI(_ai(verdict=AI_VIOLATION, violations=[{'sign': 'майка'}])))['check']['id']
    with db._get_cursor() as cursor:
        with pytest.raises(service.DressCodeError):
            service.acknowledge(cursor, check_id, ANNA)        # ещё не подтверждено
    _decide(db, check_id, 'confirmed')
    with db._get_cursor() as cursor:
        with pytest.raises(service.DressCodeError):
            service.acknowledge(cursor, check_id, BORIS)       # чужое
        service.acknowledge(cursor, check_id, ANNA)
        state = service.my_state(cursor, gcs.as_factory(), ANNA)
    assert state['today'][0]['acknowledged_at']
    # Новое подтверждение после отмены — снова без «ознакомлен».
    _decide(db, check_id, 'no_violation')
    assert _decide(db, check_id, 'confirmed')['check']['acknowledged_at'] is None


def test_retake_request_opens_one_retake_for_today(db, gcs):
    original = _submit(db, gcs, ANNA, FakeAI(_ai(verdict=AI_UNCLEAR, note='?')))['check']['id']
    _decide(db, original, 'retake', 'Встаньте к свету')
    with db._get_cursor() as cursor:
        state = service.my_state(cursor, gcs.as_factory(), ANNA)
    assert state['can_capture'] is True and state['retake_for'] == original
    retake = _submit(db, gcs, ANNA, retake_of=original)['check']
    assert retake['retake_of'] == original and retake['day'] == TODAY.isoformat()
    second = FakeAI(_ai())
    with pytest.raises(service.DressCodeError):
        _submit(db, gcs, ANNA, second, retake_of=original)
    assert second.calls == 0          # отказ до похода в ИИ и в бакет
    with pytest.raises(service.DressCodeError) as error:
        _decide(db, original, 'retake')
    assert error.value.code == 'DRESS_CODE_ALREADY_SENT'
    with db._get_cursor() as cursor:
        assert service.my_state(cursor, gcs.as_factory(), ANNA)['can_capture'] is False


def test_yesterdays_retake_request_does_not_open_a_retake_today(db, gcs):
    yesterday = _insert_check(db, ANNA, TODAY - timedelta(days=1), decision='retake', ai_status=AI_UNCLEAR)
    with db._get_cursor() as cursor:
        state = service.my_state(cursor, gcs.as_factory(), ANNA)
    assert state['can_capture'] is True and state['retake_for'] is None
    _submit(db, gcs, ANNA)
    with db._get_cursor() as cursor:
        state = service.my_state(cursor, gcs.as_factory(), ANNA)
    assert state['can_capture'] is False and state['retake_for'] is None
    with pytest.raises(service.DressCodeError):
        _submit(db, gcs, ANNA, retake_of=yesterday)


def test_retake_without_request_or_for_someone_else_is_refused(db, gcs):
    anna = _submit(db, gcs, ANNA)['check']['id']
    with pytest.raises(service.DressCodeError):
        _submit(db, gcs, ANNA, retake_of=anna)            # не просили
    _decide(db, anna, 'retake', 'ещё раз')
    with pytest.raises(service.DressCodeError):
        _submit(db, gcs, BORIS, retake_of=anna)           # чужое фото


def test_head_of_another_department_does_not_see_or_decide(db, gcs):
    check_id = _submit(db, gcs, ANNA)['check']['id']
    with db._get_cursor() as cursor:
        assert service.check_detail(cursor, gcs.as_factory(), check_id, department_ids=[OTHER]) is None
    with pytest.raises(service.DressCodeError) as error:
        _decide(db, check_id, 'no_violation', department_ids=[OTHER])
    assert error.value.status == 404
    assert _decide(db, check_id, 'no_violation', department_ids=[FRONT])['check']['decision'] == 'no_violation'


# ── Список, сводка, «не прислали», чистка ───────────────────────────────────

def test_list_summary_and_filters(db, gcs):
    _submit(db, gcs, ANNA, FakeAI(_ai(verdict=AI_VIOLATION, violations=[{'sign': 'майка'}])))
    _submit(db, gcs, BORIS)
    _submit(db, gcs, TRAINEE, FakeAI(DressCodeAIError('down')))
    with db._get_cursor() as cursor:
        everything = service.list_checks(cursor, gcs.as_factory(), date_from=TODAY, date_to=TODAY)
        waiting = service.list_checks(cursor, gcs.as_factory(), date_from=TODAY, date_to=TODAY, decision='waiting')
        astana = service.list_checks(cursor, gcs.as_factory(), date_from=TODAY, date_to=TODAY, city='Астана')
        named = service.list_checks(cursor, gcs.as_factory(), date_from=TODAY, date_to=TODAY, q='анна')
    assert everything['summary'] == {'photos': 3, 'analyzed': 2, 'ai_ok': 1, 'ai_violation': 1, 'ai_unclear': 0,
                                     'ai_pending': 1, 'waiting': 1, 'confirmed': 0, 'no_violation': 0,
                                     'retake': 0, 'repeat': 0}
    assert [r['employee'] for r in waiting['rows']] == ['Анна Фронт']
    assert waiting['summary']['photos'] == 3          # сводка — до фильтра по статусу
    assert [r['employee'] for r in astana['rows']] == ['Борис Фронт']
    assert [r['employee'] for r in named['rows']] == ['Анна Фронт']
    assert all('photo_path' not in r and r['photo_url'] for r in everything['rows'])


def test_missing_photos_counts_only_started_shifts(db, gcs):
    _sql(db, """
        INSERT INTO work_shifts (operator_id, shift_date, start_time, end_time) VALUES
            (30, %s, '09:00', '18:00'), (31, %s, '09:00', '18:00'), (32, %s, '14:00', '22:00'),
            (31, %s, '09:00', '18:00'), (50, %s, '09:00', '18:00'), (33, %s, '09:00', '18:00'),
            (60, %s, '09:00', '18:00')
    """, (TODAY, TODAY, TODAY, TODAY - timedelta(days=1), TODAY, TODAY, TODAY))
    _submit(db, gcs, ANNA)
    with db._get_cursor() as cursor:
        settings = service.load_settings(cursor)
        missing = service.missing_photos(cursor, settings, date_from=TODAY - timedelta(days=1), date_to=TODAY)
    # Анна прислала; стажёр ещё не начал смену (14:00); чужой отдел, СВ и уволенный фото не обязаны.
    assert [(r['day'], r['employee']) for r in missing['rows']] == [
        (TODAY.isoformat(), 'Борис Фронт'), ((TODAY - timedelta(days=1)).isoformat(), 'Борис Фронт')]
    assert missing['shifts'] == 3


def test_cleanup_drops_month_old_rows_and_photos(db, gcs):
    fresh = _submit(db, gcs, ANNA)['check']
    old_day = TODAY - timedelta(days=40)
    old = _insert_check(db, BORIS, old_day)
    old_path = _sql(db, 'SELECT photo_path FROM dress_code_checks WHERE id = %s', (old,))[0][0]
    gcs.store[('test-bucket', old_path)] = b'old'
    assert service.cleanup(db, gcs.as_factory()) == 1
    assert ('test-bucket', old_path) not in gcs.store
    assert [r[0] for r in _sql(db, 'SELECT id FROM dress_code_checks')] == [fresh['id']]


def test_excel_has_photos_missing_and_summary(db, gcs):
    from openpyxl import load_workbook
    _submit(db, gcs, ANNA, FakeAI(_ai(verdict=AI_VIOLATION, violations=[{'sign': 'майка'}])))
    with db._get_cursor() as cursor:
        listing = service.list_checks(cursor, None, date_from=TODAY, date_to=TODAY)
    data = report.build(listing['rows'], {'rows': [{'day': TODAY.isoformat(), 'employee': 'Борис Фронт'}],
                                          'shifts': 2}, listing['summary'],
                        date_from=TODAY, date_to=TODAY, base_url='https://portal')
    book = load_workbook(io.BytesIO(data))
    assert book.sheetnames == ['Сводка', 'Фото', 'Не прислали фото']
    photos_sheet = book['Фото']
    assert photos_sheet['C2'].value == 'Анна Фронт' and photos_sheet['I2'].value == 'майка'
    assert photos_sheet['K2'].value == 'Нет решения'
    assert photos_sheet['S2'].value.startswith('https://portal/?view=group_late_bot&dc_check=')


# ── Ручки ───────────────────────────────────────────────────────────────────

def _client(db, gcs, *, requester=ANNA, reviewer=(HEAD, [FRONT]), analyze=None, sent=None):
    from flask import Flask, jsonify
    app = Flask(__name__)

    def reviewer_access():
        if reviewer is None:
            return None, None, (jsonify({'error': 'forbidden'}), 403)
        return reviewer[0], reviewer[1], None

    bp = dc_routes.build_dress_code_blueprint(
        db=db, require_api_key=lambda f: f, build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (requester, None, None), reviewer_access=reviewer_access,
        send_telegram=lambda chat, text, reply_markup=None: (sent.append((chat, text)) if sent is not None else None),
        web_app_base_url='https://portal', gcs=gcs.as_factory(), analyze=analyze or FakeAI(_ai()))
    app.register_blueprint(bp)
    return app.test_client()


def test_routes_employee_flow_and_head_notified(db, gcs):
    sent = []
    client = _client(db, gcs, analyze=FakeAI(_ai(verdict=AI_VIOLATION, violations=[{'sign': 'майка'}])), sent=sent)
    assert client.get('/api/dress_code/me').get_json()['can_capture'] is True
    response = client.post('/api/dress_code/photo', data={'photo': (io.BytesIO(_jpeg()), 'p.jpg'), 'attempt': '1'},
                           content_type='multipart/form-data')
    body = response.get_json()
    assert response.status_code == 200 and body['accepted']['status'] == 'review'
    assert body['accepted']['reasons'] == [] and body['accepted']['comment'] is None
    assert body['can_capture'] is False
    # Главе отдела — одно сообщение со ссылкой, без фото.
    assert [chat for chat, _ in sent] == [700532]
    assert 'Анна Фронт' in sent[0][1] and 'майка' in sent[0][1]


def test_routes_reviewer_scope_decision_and_settings(db, gcs):
    check_id = _submit(db, gcs, ANNA, FakeAI(_ai(verdict=AI_VIOLATION, violations=[{'sign': 'майка'}])))['check']['id']
    sent = []
    head = _client(db, gcs, sent=sent)
    listing = head.get('/api/dress_code/checks?date_from=%s' % TODAY).get_json()
    assert [r['id'] for r in listing['rows']] == [check_id] and listing['full_access'] is False
    decided = head.post('/api/dress_code/checks/%d/decision' % check_id,
                        json={'decision': 'confirmed', 'comment': 'Переоденьтесь'})
    assert decided.status_code == 200 and decided.get_json()['check']['journal'][0]['decision'] == 'confirmed'
    assert [chat for chat, _ in sent] == [700030]          # сотруднику
    # Глава меняет правила, но не круг отделов.
    assert head.put('/api/dress_code/settings', json={'rules_body': 'Новые правила'}).status_code == 200
    assert head.put('/api/dress_code/settings', json={'department_ids': [OTHER]}).status_code == 403
    other = _client(db, gcs, reviewer=(1, [OTHER]))
    assert other.get('/api/dress_code/checks/%d' % check_id).status_code == 404
    outsider = _client(db, gcs, reviewer=None)
    assert outsider.get('/api/dress_code/checks').status_code == 403
    export = head.get('/api/dress_code/export?date_from=%s' % TODAY)
    assert export.status_code == 200 and export.data[:2] == b'PK'


def test_routes_bad_period_and_unknown_decision(db, gcs):
    head = _client(db, gcs)
    assert head.get('/api/dress_code/checks?date_from=13.11.2026').status_code == 400
    assert head.get('/api/dress_code/checks?date_from=2026-01-01&date_to=2026-12-31').status_code == 400
    check_id = _submit(db, gcs, ANNA)['check']['id']
    assert head.post('/api/dress_code/checks/%d/decision' % check_id, json={'decision': 'maybe'}).status_code == 400
