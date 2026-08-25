# -*- coding: utf-8 -*-
"""Ручки моста раздела «Касания»: /api/cdr/agent/*.

Это единственный вход в портал, у которого нет ни cookie, ни JWT: мост живёт в
корпоративной сети и здоровается общим токеном. Значит, здесь же и единственное
место, где ошибка открывает запись в нашу базу кому угодно из интернета.

Что закреплено:

  * токен сверяется `compare_digest`, а не `==` — обычное сравнение утекает его
    по времени;
  * ПУСТОЙ токен на сервере не совпадает с пустым заголовком: ненастроенный
    портал отвечает 503, а не пускает всех подряд;
  * «токен не настроен» (503) и «токен неверен» (401) — разные коды: мост должен
    отличать «портал ещё не готов» от «ключ протух»;
  * присланные касания ЧУЖИХ суток отбрасываются. Мост читает сутки с часовым
    хвостом следующих, чтобы собрать звонок через полночь, — и если хвост
    сложить в базу, касания удвоятся;
  * длинные значения обрезаются при разборе, а не при вставке: чужая строка на
    500 символов должна испортить одну ячейку, а не уронить сутки целиком.

Сети и базы здесь нет: слой `queries` подменяется двойником, Flask поднимается
своим приложением с фальшивыми зависимостями (тот же приём, что в
tests/test_sensitive_section_qr_gate.py).
"""

import json
import unittest
from datetime import date
from unittest import mock

try:
    from flask import Flask, jsonify
except ImportError:  # pragma: no cover
    Flask = None

from cdr import routes as cdr_routes

# Токен ASCII, как в жизни: HTTP-заголовок ходит в latin-1, и кириллицу в него
# не положить. Отдельный тест ниже проверяет, что не-ASCII токен даёт честный
# 401, а не 500 — на этом уже спотыкался агент «Ограничителя Перезвона».
TOKEN = 'test-bridge-token-0123456789'
TODAY = date(2026, 8, 25)


class _FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((' '.join(str(sql).split())[:80], params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakeDb:
    def __init__(self):
        self.cursor = _FakeCursor()

    def _get_cursor(self):
        from contextlib import contextmanager

        @contextmanager
        def scope():
            yield self.cursor
        return scope()


class _Recorder:
    """Двойник слоя SQL: запоминает, что раздел пытался записать."""

    def __init__(self):
        self.days_stored = []
        self.days_done = []
        self.days_failed = []
        self.seen = []
        self.agents = None
        self.claimed = []
        self.cleanup_allowed = False
        self.cleaned = False

    # то, что зовут ручки моста
    def agent_seen(self, cursor, **kwargs):
        self.seen.append(kwargs)

    def claim_days(self, cursor, agent_id, limit=1):
        return self.claimed

    def agent_state(self, cursor):
        return {'connected': True, 'agents_at': None, 'last_seen_at': None}

    def cleanup_due(self, cursor, hours=24):
        return self.cleanup_allowed

    def drop_expired(self, cursor, retention_days=None):
        self.cleaned = True
        return 0

    def replace_day_touches(self, cursor, day, touches):
        self.days_stored.append((day, touches))
        return len(touches)

    def mark_day_done(self, cursor, day, rows_fetched, touches, complete):
        self.days_done.append({'day': day, 'rows': rows_fetched,
                               'touches': touches, 'complete': complete})

    def mark_day_error(self, cursor, day, error):
        self.days_failed.append((day, error))

    def save_station_agents(self, cursor, agents):
        self.agents = agents

    def load_station_agents(self, cursor):
        return self.agents or {}

    def load_directory(self, cursor):
        return {}

    def directory_updated_at(self, cursor):
        return None

    def db_operator_rows(self, cursor):
        return []

    def save_directory(self, cursor, built):
        return len(built)

    def today_almaty(self):
        return TODAY

    def now_almaty(self):
        from datetime import datetime
        return datetime(2026, 8, 25, 12, 0, 0)


@unittest.skipIf(Flask is None, 'flask не установлен')
class AgentRouteTests(unittest.TestCase):
    def setUp(self):
        self.recorder = _Recorder()
        patcher = mock.patch.object(cdr_routes, 'queries', self.recorder)
        patcher.start()
        self.addCleanup(patcher.stop)

        token_patch = mock.patch.object(cdr_routes.config, 'agent_token',
                                        return_value=TOKEN)
        token_patch.start()
        self.addCleanup(token_patch.stop)

        app = Flask(__name__)
        app.register_blueprint(cdr_routes.build_cdr_blueprint(
            db=_FakeDb(),
            require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (1, None, None),
        ))
        app.config['TESTING'] = True
        self.client = app.test_client()

    def post(self, path, payload, token=TOKEN):
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['X-Agent-Token'] = token
        return self.client.post('/api/cdr/agent/' + path,
                                data=json.dumps(payload), headers=headers)

    # ── авторизация ──────────────────────────────────────────────────────────

    def test_without_a_token_the_bridge_is_refused(self):
        response = self.post('poll', {}, token=None)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.recorder.seen, [], 'до базы дойти не должно')

    def test_wrong_token_is_refused(self):
        self.assertEqual(self.post('poll', {}, token='не тот').status_code, 401)

    def test_empty_server_token_refuses_everyone(self):
        """Ненастроенный портал не должен совпадать с пустым заголовком."""
        with mock.patch.object(cdr_routes.config, 'agent_token', return_value=''):
            self.assertEqual(self.post('poll', {}, token='').status_code, 503)
            self.assertEqual(self.post('poll', {}, token=None).status_code, 503)
            self.assertEqual(self.post('poll', {}, token='что угодно').status_code, 503)

    def test_not_configured_and_wrong_key_are_different_codes(self):
        with mock.patch.object(cdr_routes.config, 'agent_token', return_value=''):
            not_ready = self.post('poll', {}).status_code
        wrong = self.post('poll', {}, token='не тот').status_code
        self.assertEqual((not_ready, wrong), (503, 401))

    def test_token_is_compared_in_constant_time(self):
        """Не «работает ли», а «тем ли способом»: обычное == утекает токен."""
        import inspect
        source = inspect.getsource(cdr_routes._same_token)
        self.assertIn('compare_digest', source)

    def test_non_ascii_token_answers_401_and_does_not_crash(self):
        """`compare_digest` на строках с кириллицей бросает TypeError. Такой
        токен всё равно нерабочий (заголовок ходит в latin-1), но отвечать на
        него надо «не авторизован», а не пятисоткой на каждый запрос."""
        self.assertFalse(cdr_routes._same_token('токен', TOKEN))
        self.assertFalse(cdr_routes._same_token(TOKEN, 'токен'))
        self.assertTrue(cdr_routes._same_token('токен', 'токен'))
        with mock.patch.object(cdr_routes.config, 'agent_token', return_value='токен'):
            self.assertEqual(self.post('poll', {}, token='другой').status_code, 401)

    # ── задание ──────────────────────────────────────────────────────────────

    def test_poll_returns_the_window_with_an_hour_of_tail(self):
        self.recorder.claimed = [{'day': '2026-08-24', 'attempts': 1}]
        response = self.post('poll', {'hostname': 'vm-01', 'version': '1.0.0'})
        self.assertEqual(response.status_code, 200)
        job = response.get_json()['jobs'][0]
        self.assertEqual(job['from_dt'], '2026-08-24T00:00:00')
        self.assertEqual(job['to_dt'], '2026-08-25T01:00:00')

    def test_poll_records_the_bridge_as_seen(self):
        response = self.post('poll', {'hostname': 'vm-01', 'version': '1.0.0'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.recorder.seen[0]['hostname'], 'vm-01')

    def test_poll_passes_on_the_bridge_error(self):
        response = self.post('poll', {'hostname': 'vm-01', 'error': 'станция молчит'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.recorder.seen[0]['error'], 'станция молчит')

    def test_cleanup_runs_only_on_an_idle_poll(self):
        """Уборка кэша висит на холостом заходе моста: своего планировщика у
        раздела нет, а чистить базу посреди работы незачем."""
        self.recorder.cleanup_allowed = True
        self.recorder.claimed = [{'day': '2026-08-24', 'attempts': 1}]
        self.assertEqual(self.post('poll', {}).status_code, 200)
        self.assertFalse(self.recorder.cleaned, 'есть работа — не до уборки')

        self.recorder.claimed = []
        self.assertEqual(self.post('poll', {}).status_code, 200)
        self.assertTrue(self.recorder.cleaned)

    def test_cleanup_does_not_run_when_it_is_not_due(self):
        self.recorder.cleanup_allowed = False
        self.assertEqual(self.post('poll', {}).status_code, 200)
        self.assertFalse(self.recorder.cleaned)

    # ── приём суток ──────────────────────────────────────────────────────────

    def _touch(self, **kwargs):
        base = {'linkedid': '1.1', 'phone': '+77015550001',
                'started_at': '2026-08-24 09:00:00', 'answered_at': '',
                'ext': '6650', 'call_type': 'Исходящий', 'result': 'Разговор',
                'talk_seconds': 42, 'dial_seconds': 50, 'queue': '',
                'recording_url': 'http://rec/a.wav', 'legs': 2}
        base.update(kwargs)
        return base

    def test_day_is_stored(self):
        response = self.post('day', {'day': '2026-08-24', 'rows_fetched': 23574,
                                     'touches': [self._touch()]})
        self.assertEqual(response.status_code, 200)
        day, stored = self.recorder.days_stored[0]
        self.assertEqual(day, date(2026, 8, 24))
        self.assertEqual(stored[0]['phone'], '7015550001', 'телефон нормализуется')
        self.assertEqual(self.recorder.days_done[0]['rows'], 23574)

    def test_finished_day_is_marked_complete_and_today_is_not(self):
        self.post('day', {'day': '2026-08-24', 'touches': []})
        self.assertTrue(self.recorder.days_done[0]['complete'])
        self.post('day', {'day': '2026-08-25', 'touches': []})
        self.assertFalse(self.recorder.days_done[1]['complete'],
                         'сегодняшние сутки станция ещё дописывает')

    def test_tail_touches_are_dropped(self):
        """Мост читает час следующих суток, чтобы собрать звонок через полночь.
        Если этот хвост сложить в базу, касания удвоятся."""
        response = self.post('day', {'day': '2026-08-24', 'touches': [
            self._touch(linkedid='1.1', started_at='2026-08-24 23:59:50'),
            self._touch(linkedid='2.2', started_at='2026-08-25 00:30:00'),
        ]})
        self.assertEqual(response.get_json()['stored'], 1)
        _day, stored = self.recorder.days_stored[0]
        self.assertEqual([t['linkedid'] for t in stored], ['1.1'])

    def test_duplicate_touches_are_collapsed(self):
        """`ON CONFLICT DO UPDATE` падает с «cannot affect row a second time»,
        если один ключ встретился в пачке дважды, и уносит с собой ВСЕ сутки."""
        response = self.post('day', {'day': '2026-08-24', 'touches': [
            self._touch(linkedid='1.1', talk_seconds=10),
            self._touch(linkedid='1.1', talk_seconds=99),
            self._touch(linkedid='2.2'),
        ]})
        self.assertEqual(response.get_json()['stored'], 2)
        _day, stored = self.recorder.days_stored[0]
        keys = [(t['linkedid'], t['phone']) for t in stored]
        self.assertEqual(len(keys), len(set(keys)))
        winner = next(t for t in stored if t['linkedid'] == '1.1')
        self.assertEqual(winner['talk_seconds'], 99, 'побеждает последний')

    def test_touch_without_a_phone_or_linkedid_is_dropped(self):
        self.post('day', {'day': '2026-08-24', 'touches': [
            self._touch(phone=''), self._touch(linkedid=''), self._touch(phone='123'),
        ]})
        _day, stored = self.recorder.days_stored[0]
        self.assertEqual(stored, [])

    def test_long_values_are_trimmed_on_parse_not_on_insert(self):
        self.post('day', {'day': '2026-08-24', 'touches': [
            self._touch(result='Р' * 500, queue='3' * 200, ext='6' * 40)]})
        _day, stored = self.recorder.days_stored[0]
        self.assertLessEqual(len(stored[0]['result']), 32)
        self.assertLessEqual(len(stored[0]['queue']), 64)
        self.assertLessEqual(len(stored[0]['ext']), 8)

    def test_absurd_day_is_refused_before_the_database(self):
        payload = {'day': '2026-08-24',
                   'touches': [self._touch() for _ in range(cdr_routes.MAX_TOUCHES_PER_DAY + 1)]}
        response = self.post('day', payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.recorder.days_stored, [])

    def test_touches_must_be_a_list(self):
        self.assertEqual(self.post('day', {'day': '2026-08-24',
                                           'touches': {'a': 1}}).status_code, 400)

    def test_bridge_failure_is_recorded_not_swallowed(self):
        response = self.post('day', {'day': '2026-08-24',
                                     'error': 'timeout: станция не ответила'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.recorder.days_failed[0][0], date(2026, 8, 24))
        self.assertEqual(self.recorder.days_stored, [],
                         'отказ не должен затирать уже собранные сутки')

    def test_bad_day_format_is_a_clear_400(self):
        response = self.post('day', {'day': '24.08.2026', 'touches': []})
        self.assertEqual(response.status_code, 400)
        self.assertIn('ГГГГ-ММ-ДД', response.get_json()['error'])

    # ── справочник ───────────────────────────────────────────────────────────

    def test_station_directory_is_saved(self):
        response = self.post('directory', {'agents': {'6474': 'zhupan_aruzhan'}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.recorder.agents, {'6474': 'zhupan_aruzhan'})

    def test_directory_must_be_a_mapping(self):
        self.assertEqual(self.post('directory', {'agents': []}).status_code, 400)


class CleanTouchTests(unittest.TestCase):
    """Разбор присланного — чистая функция, проверяем её отдельно от Flask."""

    def test_garbage_does_not_raise(self):
        for value in (None, 'строка', 42, [], {}):
            self.assertIsNone(cdr_routes._clean_touch(value, TODAY))

    def test_phone_keeps_the_last_ten_digits(self):
        cleaned = cdr_routes._clean_touch(
            {'linkedid': '1.1', 'phone': '+7 (701) 555-00-01',
             'started_at': '2026-08-25 09:00:00'}, TODAY)
        self.assertEqual(cleaned['phone'], '7015550001')

    def test_missing_fields_get_honest_defaults(self):
        cleaned = cdr_routes._clean_touch(
            {'linkedid': '1.1', 'phone': '7015550001',
             'started_at': '2026-08-25 09:00:00'}, TODAY)
        self.assertEqual(cleaned['talk_seconds'], 0)
        self.assertEqual(cleaned['legs'], 1)
        self.assertIsNone(cleaned['recording_url'])
        self.assertEqual(cleaned['answered_at'], '')

    def test_truncated_timestamp_is_rejected(self):
        self.assertIsNone(cdr_routes._clean_touch(
            {'linkedid': '1.1', 'phone': '7015550001',
             'started_at': '2026-08-25'}, TODAY))


if __name__ == '__main__':
    unittest.main()
