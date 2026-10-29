# -*- coding: utf-8 -*-
"""Ручка знаний для партнёрского ИИ (knowledge_api): ключ, парк, фрагменты, журнал.

Что здесь сторожится и почему:

* **До ключа не делается ничего.** Нет ключа, неверный, отозванный — один ответ
  401 и ни одного обращения к поиску; схема не развёрнута — 503.
* **Один парк на вызов.** Фрагменты про чужой парк не отдаются; общие и «все
  парки, кроме…» — отдаются (решения владельца 29.10.2026).
* **«Данных нет» — тоже ответ**: status no_data, вопрос уходит в «Вопросы
  операторов», в журнале — строка с question_id.
* **Журнал — строкой на вызов**, включая плохой запрос и предел; ключ в базе
  только хешем, в ответе на выпуск — один раз.

Сети и базы нет: поиск подменяется, курсор — двойник. Правила, живущие в SQL и
JSX, проверяются чтением исходника — приём разделов «Новости» и «Вики».
"""

import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from knowledge_api import keys, parks as park_match, queries, routes, schema  # noqa: E402
from knowledge_api import search as knowledge_search  # noqa: E402
from knowledge_api.admin_routes import build_knowledge_admin_blueprint  # noqa: E402
from knowledge_api.routes import build_knowledge_blueprint  # noqa: E402

BOT = ROOT / 'bot_schedule2.py'
README = ROOT / 'README.md'
DOC = ROOT / 'docs' / 'SYNTONY_KNOWLEDGE_API.md'

SEARCH_URL = '/api/knowledge/search'
# Ключ выдуман; настоящий живёт только в журнале выпуска.
KEY = 'otpk_FAKE-key-for-tests-0123456789-abcdefghijk'
KEY_ROW = (7, 'Syntony', keys.mark(KEY), 'operator', 1, 'СЗоВ — Служба заботы о водителях',
           'szov', 3, 'Админ', None, None, 5, None, None, None)
REVOKED_ROW = KEY_ROW[:12] + ('2026-10-29T10:00:00', 3, 'Админ')
PARK_ROW = (5, 'global', 'Global', None, 'active', 11)

PARKS = [
    {'id': 1, 'slug': 'itaxi', 'name': 'iTaxi'},
    {'id': 4, 'slug': 'chestnyy-taksopark', 'name': 'Честный'},
    {'id': 5, 'slug': 'global', 'name': 'Global'},
    {'id': 8, 'slug': 'halyk', 'name': 'Халык'},
    {'id': 12, 'slug': 'jana-taksi', 'name': 'Jana такси'},
    {'id': 13, 'slug': '2dongelek', 'name': '2dongelek'},
    {'id': 14, 'slug': 'tenge-taksi', 'name': 'Тенге Такси'},
    {'id': 16, 'slug': 'nol-taksi', 'name': 'Ноль такси'},
    {'id': 17, 'slug': 'itaxi-vip', 'name': 'iTaxi VIP'},
    {'id': 19, 'slug': 'centr-registraciy-regiony', 'name': 'Центр Регистраций (Регионы)'},
]


def article_row(**extra):
    row = {'chunk_id': 4812, 'article_id': 33, 'title': 'Все акции', 'slug': 'vse-akcii',
           'heading_path': 'Акции СМЗ > ВСЕ ПАРКИ', 'text': 'Парк: Все парки; Даты: ∞.',
           'requires_ack': False, 'score': 0.31, 'similarity': 0.84, 'found_by': [0, 1],
           'historical': False, 'park_match': 'all_parks'}
    row.update(extra)
    return row


def result(status='ok', fragments=None):
    rows = [article_row()] if fragments is None else fragments
    return {
        'status': status,
        'park': None if status == 'park_not_found' else {'id': 5, 'slug': 'global', 'name': 'Global'},
        'fragments': [knowledge_search.fragment(index, row) for index, row in enumerate(rows, 1)]
        if status == 'ok' else [],
        'notes': [], 'degraded': False, 'vector_error': None, 'branches': {},
        'perimeter': {'articles': 50, 'hash': 'x', 'space_id': 11} if status in ('ok', 'no_data') else None,
    }


# ── Ключ ────────────────────────────────────────────────────────────────────────

class KeyTests(unittest.TestCase):
    def test_generated_key_has_prefix_and_is_long_random_ascii(self):
        first, second = keys.generate(), keys.generate()
        self.assertTrue(first.startswith(keys.PREFIX))
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), keys.MIN_LENGTH)
        self.assertTrue(first.isascii())
        self.assertIsNotNone(keys.presented(first))

    def test_mark_is_the_start_of_the_hash_and_never_the_key(self):
        self.assertEqual(keys.mark(KEY), keys.digest(KEY)[:keys.MARK_LENGTH])
        self.assertNotIn(keys.mark(KEY), KEY)

    def test_values_that_are_not_keys_do_not_reach_the_database(self):
        for value in ('', None, 'short', 'ключ-кириллицей-' * 4, 'with space ' * 5,
                      'x' * (keys.MAX_LENGTH + 1)):
            self.assertIsNone(keys.presented(value), repr(value))


# ── Парк во фрагменте ───────────────────────────────────────────────────────────

class ParkMatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = park_match.ParkMatcher(PARKS)

    def classify(self, text, park_id=5):
        return self.matcher.classify(park_id, text)

    def test_requested_park_named_is_kept_even_next_to_others(self):
        self.assertEqual(self.classify('Таксопарк: Global; Номер WhatsApp: 77001234567'), 'named')
        self.assertEqual(self.classify('условия в Global и iTaxi VIP одинаковые'), 'named')
        self.assertEqual(self.classify('Парк: Глобал такси; Город: Алматы'), 'named')

    def test_only_other_parks_named_is_dropped(self):
        self.assertEqual(self.classify('№: 3; Парк: Jana Алматы; Город: Алматы'), 'other')
        self.assertEqual(self.classify('Парк: Tenge Taxi; Даты: ∞'), 'other')
        self.assertEqual(self.classify('от Anytime iTaxi — цена за день'), 'other')
        self.assertEqual(self.classify('Скрипт обзвона водителей таксопарка Ноль такси'), 'other')
        self.assertEqual(self.classify('Парк: 2Донгелек; Город: все'), 'other')

    def test_all_parks_and_exclusion_lists_are_handed_over_as_they_are(self):
        self.assertEqual(self.classify('Парк: Все парки, кроме Eki Dongelek, Anytime, Ноль такси'),
                         'all_parks')
        self.assertEqual(self.classify('ПАРКИ ПАРТНЕРЫ ТАКСИ ПРО Исключение 2Донгелек'), 'all_parks')
        self.assertEqual(self.classify('Таксопарки iGroup: 17 парков'), 'all_parks')

    def test_text_without_parks_is_general(self):
        self.assertEqual(self.classify('Как включить цепочку заказов в Яндекс Про'), 'general')

    def test_everyday_words_are_not_parks_without_park_context(self):
        self.assertEqual(self.classify('честный ответ оператора'), 'general')
        self.assertEqual(self.classify('Бонус 5000 тенге за 100 поездок'), 'general')
        self.assertEqual(self.classify('Пополнение через Halyk Bank или Kaspi'), 'general')
        self.assertEqual(self.classify('регионы Казахстана'), 'general')
        self.assertEqual(self.classify('Таксопарк: Халык; Номер WhatsApp: 7700'), 'other')
        self.assertEqual(self.classify('Парк: Честный; Город: Все города'), 'other')
        self.assertEqual(self.classify('Парк: Честный; Город: Все города', park_id=4), 'named')

    def test_longer_name_wins_over_its_prefix(self):
        self.assertEqual(self.matcher.mentions('условия iTaxi VIP'), {17: 'iTaxi VIP'})
        self.assertEqual(self.matcher.mentions('условия iTaxi'), {1: 'iTaxi'})

    def test_other_parks_in_the_question_are_named_for_the_note(self):
        self.assertEqual(self.matcher.others_in(5, 'Акции Jana и Global'), ['Jana такси'])
        self.assertEqual(self.matcher.others_in(5, 'Акции Global'), [])

    def test_normalize_keeps_positions(self):
        text = 'Парк: «Global» — Алматы!'
        self.assertEqual(len(park_match.normalize(text)), len(text))


# ── Поиск без базы ──────────────────────────────────────────────────────────────

class SearchHelpersTests(unittest.TestCase):
    def test_private_links_are_stripped_from_fragments(self):
        text = 'Инструкция (http://192.168.88.186:3000/ru/actions) и сайт https://taxi.yandex.kz/tariff'
        cleaned = knowledge_search.clean_text(text)
        self.assertNotIn('192.168', cleaned)
        self.assertIn('https://taxi.yandex.kz/tariff', cleaned)

    def test_fragment_names_branches_and_hides_negative_chunk_ids(self):
        item = knowledge_search.fragment(1, article_row(found_by=[0, 1, 4]))
        self.assertEqual(item['found_by'], ['words', 'meaning', 'directory'])
        self.assertEqual(item['kind'], 'article')
        self.assertEqual(item['chunk_id'], 4812)
        self.assertEqual(item['park_match'], 'all_parks')
        self.assertFalse(item['outdated'])
        directory = knowledge_search.fragment(2, article_row(chunk_id=-1, source_kind='office',
                                                             office={'id': 3, 'kind': 'partner',
                                                                     'partner_label': 'Брендирование'}))
        self.assertIsNone(directory['chunk_id'])
        self.assertEqual(directory['kind'], 'office')
        self.assertEqual(directory['office']['note'], 'Партнёрская точка, не офис таксопарка')

    def test_park_reads_stay_inside_the_key_spaces(self):
        """Граница пространства — в самом SQL (страж tests/test_wiki_directory_space.py)."""
        cursor = _Cursor(park_row=None)
        self.assertIsNone(knowledge_search.resolve_park(cursor, spaces=[11], park_id=5))
        text, params = cursor.calls[-1]
        self.assertIn('space_id = ANY(%(spaces)s)', text)
        self.assertEqual(params['spaces'], [11])
        self.assertIsNone(knowledge_search.resolve_park(cursor, spaces=[], park='global'))
        text, params = cursor.calls[-1]
        self.assertIn('space_id = ANY(%(spaces)s)', text)
        self.assertEqual(params['spaces'], [-1])

    def test_journal_keeps_references_but_not_text(self):
        journal = knowledge_search.journal_fragments([knowledge_search.fragment(1, article_row())])
        self.assertEqual(journal[0]['article_id'], 33)
        self.assertNotIn('text', journal[0])

    def test_offices_pass_the_park_sieve(self):
        rows = [
            {'source_kind': 'office', 'ref_id': 1, 'text': 'свой'},
            {'source_kind': 'office', 'ref_id': 2, 'text': 'чужой'},
            {'source_kind': 'office', 'ref_id': 3, 'text': 'общий'},
            {'source_kind': 'office', 'ref_id': 4, 'text': 'для всех'},
            {'source_kind': 'city', 'ref_id': 9, 'text': 'город'},
        ]
        offices = [
            {'id': 1, 'kind': 'park', 'all_parks': False, 'parks': [{'park_id': 5}]},
            {'id': 2, 'kind': 'park', 'all_parks': False, 'parks': [{'park_id': 12}]},
            {'id': 3, 'kind': 'partner', 'partner_label': 'Брендирование', 'all_parks': False, 'parks': []},
            {'id': 4, 'kind': 'park', 'all_parks': True, 'parks': []},
        ]
        kept = knowledge_search.narrow_offices(rows, offices, {'id': 5})
        self.assertEqual([row['ref_id'] for row in kept], [1, 3, 4, 9])
        self.assertEqual([row['park_match'] for row in kept], ['named', 'general', 'named', 'general'])
        self.assertEqual(kept[1]['office'], {'id': 3, 'kind': 'partner', 'partner_label': 'Брендирование'})


class RateLimiterTests(unittest.TestCase):
    def test_minute_window_refuses_the_extra_call_and_names_the_wait(self):
        clock = {'now': 100.0}
        limiter = routes.RateLimiter(2, clock=lambda: clock['now'])
        self.assertEqual(limiter.allow(7), (True, 0))
        self.assertEqual(limiter.allow(7), (True, 0))
        allowed, wait = limiter.allow(7)
        self.assertFalse(allowed)
        self.assertGreaterEqual(wait, 1)
        self.assertEqual(limiter.allow(8), (True, 0))  # другой ключ — своя минута
        clock['now'] += 61
        self.assertEqual(limiter.allow(7), (True, 0))


# ── Курсор-двойник и сборка ручки ───────────────────────────────────────────────

class _Cursor:
    """Помнит SQL и параметры; отвечает на то, что ручка спрашивает у базы."""

    def __init__(self, *, ready=True, key_row=KEY_ROW, park_row=PARK_ROW):
        self.calls = []
        self.ready = ready
        self.key_row = key_row
        self.park_row = park_row
        self._one = None
        self._all = []

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        self.calls.append((text, params))
        self._one, self._all = None, []
        if 'to_regclass' in text:
            self._one = (self.ready,)
        elif 'FROM knowledge_api_keys k' in text and 'WHERE k.key_hash' in text:
            self._one = self.key_row if params and params[0] == keys.digest(KEY) else None
        elif 'FROM knowledge_api_keys k' in text and 'WHERE k.id' in text:
            self._one = self.key_row
        elif 'FROM knowledge_api_keys k' in text:
            self._all = [self.key_row] if self.key_row else []
        elif 'FROM wiki_taxi_parks' in text and 'WHERE id = %s' in text:
            self._one = self.park_row if params and params[0] == 5 else None
        elif 'FROM wiki_taxi_parks' in text and 'lower(slug)' in text:
            self._one = self.park_row if params and params['value'].lower() == 'global' else None
        elif text.startswith('INSERT INTO knowledge_api_calls'):
            self._one = (101,)
        elif text.startswith('INSERT INTO knowledge_api_keys'):
            self._one = (8,)
        elif text.startswith('UPDATE knowledge_api_keys') and 'revoked_at' in text:
            self._one = (7,)
        elif text.startswith('SELECT id, name FROM departments'):
            self._one = (1, 'СЗоВ')
        elif 'count(*) FROM knowledge_api_calls' in text:
            self._one = (0,)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._all)

    @property
    def rowcount(self):
        return 1

    def journal(self):
        return [params for text, params in self.calls if text.startswith('INSERT INTO knowledge_api_calls')]

    def touched(self):
        return [params for text, params in self.calls
                if text.startswith('UPDATE knowledge_api_keys') and 'calls_count' in text]


@unittest.skipIf(Flask is None, 'flask не установлен')
class Harness(unittest.TestCase):
    def build(self, *, ready=True, key_row=KEY_ROW, park_row=PARK_ROW, result_=None,
              raise_=None, per_minute=None, lanes=None):
        self.cursor = _Cursor(ready=ready, key_row=key_row, park_row=park_row)
        self.searches = []
        self.escalations = []
        outcome = result() if result_ is None else result_

        def fake_search(cursor, *, key, question, park_id, park, limit):
            self.searches.append({'key': key['id'], 'park_id': park_id, 'park': park,
                                  'question': question, 'limit': limit})
            if raise_:
                raise raise_
            return outcome

        def fake_escalate(cursor, **fields):
            self.escalations.append(fields)
            return 55

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield self.cursor

        db._get_cursor = _get_cursor
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.register_blueprint(build_knowledge_blueprint(
            db=db, client_ip=lambda: '203.0.113.7', search=fake_search, environ={},
            per_minute=per_minute, lanes=lanes, escalate=fake_escalate))
        return self.app.test_client()

    def post(self, client, body, key=KEY):
        headers = {keys.HEADER: key} if key is not None else {}
        return client.post(SEARCH_URL, data=json.dumps(body), content_type='application/json',
                           headers=headers)


class DoorTests(Harness):
    def test_without_schema_the_door_is_closed_not_open(self):
        client = self.build(ready=False)
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_NOT_CONFIGURED')
        self.assertEqual(self.searches, [])

    def test_missing_wrong_and_revoked_keys_get_the_same_refusal(self):
        for key, row in ((None, KEY_ROW), ('otpk_wrong-key-of-the-right-length-0123456789', KEY_ROW),
                         (KEY, REVOKED_ROW)):
            client = self.build(key_row=row)
            response = self.post(client, {'park_id': 5, 'question': 'акции'}, key=key)
            self.assertEqual(response.status_code, 401, key)
            self.assertEqual(response.get_json(), {'error': 'Ключ не принят',
                                                   'code': 'KNOWLEDGE_API_UNAUTHORIZED'})
            self.assertEqual(self.searches, [])
            self.assertEqual(self.cursor.journal(), [])

    def test_response_is_never_cached(self):
        client = self.build()
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.headers.get('Cache-Control'), 'no-store')


class RequestShapeTests(Harness):
    def test_bad_bodies_are_refused_and_journaled(self):
        client = self.build()
        cases = [
            (None, 'JSON'), ({'park_id': 5}, 'question'), ({'question': 'акции'}, 'park'),
            ({'park_id': 'five', 'question': 'акции'}, 'park_id'),
            ({'park_id': 5, 'question': 'акции', 'limit': 0}, 'limit'),
            ({'park_id': 5, 'question': 'x' * 1001}, '1000'),
        ]
        for body, word in cases:
            response = client.post(SEARCH_URL, data='not json' if body is None else json.dumps(body),
                                   content_type='application/json', headers={keys.HEADER: KEY})
            self.assertEqual(response.status_code, 400, word)
            payload = response.get_json()
            self.assertEqual(payload['code'], 'KNOWLEDGE_API_BAD_REQUEST')
            self.assertIn(word, payload['error'])
            self.assertIn('request_id', payload)
        self.assertEqual(self.searches, [])
        self.assertEqual([entry['outcome'] for entry in self.cursor.journal()],
                         ['bad_request'] * len(cases))

    def test_unknown_park_is_404_and_points_to_the_list(self):
        client = self.build(result_=result('park_not_found'))
        response = self.post(client, {'park_id': 99, 'question': 'акции'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_PARK_NOT_FOUND')
        self.assertEqual(self.searches[0]['park_id'], 99)
        entry = self.cursor.journal()[0]
        self.assertEqual((entry['outcome'], entry['status'], entry['error'], entry['park']),
                         ('bad_request', 404, 'park_not_found', None))

    def test_park_can_be_named_by_slug(self):
        client = self.build()
        response = self.post(client, {'park': 'Global', 'question': 'акции'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.searches[0]['park_id'], self.searches[0]['park']), (None, 'Global'))


class AnswerTests(Harness):
    def test_fragments_are_handed_over_and_the_call_is_journaled(self):
        client = self.build()
        response = self.post(client, {'park_id': 5, 'question': ' Какие  акции? ', 'limit': 3})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['park'], {'id': 5, 'slug': 'global', 'name': 'Global'})
        self.assertEqual(payload['question'], 'Какие акции?')
        self.assertEqual(payload['fragments'][0]['title'], 'Все акции')
        self.assertEqual(payload['perimeter'], {'articles': 50})
        self.assertEqual(self.searches, [{'key': 7, 'park_id': 5, 'park': None,
                                          'question': 'Какие акции?', 'limit': 3}])
        entry = self.cursor.journal()[0]
        self.assertEqual((entry['outcome'], entry['status'], entry['count'], entry['park'], entry['ip']),
                         ('ok', 200, 1, 5, '203.0.113.7'))
        self.assertEqual(json.loads(entry['fragments'])[0]['article_id'], 33)
        self.assertNotIn('text', json.loads(entry['fragments'])[0])
        self.assertEqual(self.cursor.touched(), [(7,)])
        self.assertEqual(self.escalations, [])

    def test_no_data_goes_to_the_supervisors_and_says_so(self):
        client = self.build(result_=result('no_data'))
        response = self.post(client, {'park_id': 5, 'question': 'Есть ли парковка у офиса?'})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['status'], 'no_data')
        self.assertEqual(payload['fragments'], [])
        self.assertEqual(payload['message'], 'Данных нет')
        self.assertTrue(payload['question_logged'])
        self.assertEqual(self.escalations, [{
            'key_id': 7, 'key_name': 'Syntony', 'department_id': 1, 'space_id': 11,
            'park_id': 5, 'park_name': 'Global', 'question': 'Есть ли парковка у офиса?'}])
        entry = self.cursor.journal()[0]
        self.assertEqual((entry['outcome'], entry['question_id']), ('no_data', 55))

    def test_an_archived_park_is_403(self):
        client = self.build(result_=result('park_unavailable'))
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_PARK_UNAVAILABLE')
        self.assertEqual(self.cursor.journal()[0]['outcome'], 'park_unavailable')

    def test_a_broken_search_is_500_and_still_journaled(self):
        client = self.build(raise_=RuntimeError('провайдер лёг'))
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_FAILED')
        self.assertNotIn('провайдер', response.get_data(as_text=True))
        entry = self.cursor.journal()[0]
        self.assertEqual((entry['outcome'], entry['error']), ('error', 'RuntimeError'))


class LimitTests(Harness):
    def test_the_minute_limit_answers_429_with_retry_after(self):
        client = self.build(per_minute=1)
        self.assertEqual(self.post(client, {'park_id': 5, 'question': 'акции'}).status_code, 200)
        response = self.post(client, {'park_id': 5, 'question': 'акции'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()['code'], 'KNOWLEDGE_API_LIMIT')
        self.assertTrue(int(response.headers['Retry-After']) >= 1)
        self.assertEqual(len(self.searches), 1)
        self.assertEqual([entry['outcome'] for entry in self.cursor.journal()], ['ok', 'limit'])


class PingTests(Harness):
    def test_ping_names_the_key_and_its_rights(self):
        client = self.build()
        response = client.get('/api/knowledge/ping', headers={keys.HEADER: KEY})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['key'], 'Syntony')
        self.assertEqual(payload['acts_as']['department_code'], 'szov')
        self.assertEqual(payload['limits']['max_question_chars'], routes.MAX_QUESTION_CHARS)

    def test_ping_without_key_is_401(self):
        client = self.build()
        self.assertEqual(client.get('/api/knowledge/ping').status_code, 401)


# ── Ключи для супер-админа ──────────────────────────────────────────────────────

@unittest.skipIf(Flask is None, 'flask не установлен')
class AdminTests(unittest.TestCase):
    def build(self, role='super_admin'):
        self.cursor = _Cursor()
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield self.cursor

        db._get_cursor = _get_cursor

        def passthrough(handler):
            return handler

        app = Flask(__name__)
        app.config['TESTING'] = True
        app.register_blueprint(build_knowledge_admin_blueprint(
            db=db, require_api_key=passthrough, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (3, (3, 'admin', 'Админ', role), None),
            normalize_role=lambda value: str(value or '').strip().lower()))
        return app.test_client()

    def test_only_the_super_admin_issues_keys(self):
        client = self.build(role='admin')
        self.assertEqual(client.get('/api/admin/knowledge-keys').status_code, 403)
        self.assertEqual(client.post('/api/admin/knowledge-keys', json={'name': 'X'}).status_code, 403)
        self.assertEqual(self.cursor.calls, [])

    def test_the_list_describes_whose_rights_the_key_has(self):
        client = self.build()
        response = client.get('/api/admin/knowledge-keys')
        self.assertEqual(response.status_code, 200)
        item = response.get_json()['keys'][0]
        self.assertEqual(item['name'], 'Syntony')
        self.assertEqual(item['acts_as']['department_code'], 'szov')
        self.assertTrue(item['active'])
        self.assertNotIn('key_hash', item)

    def test_issue_shows_the_secret_once_and_stores_only_its_hash(self):
        client = self.build()
        self.assertEqual(client.post('/api/admin/knowledge-keys', json={'name': '  '}).status_code, 400)
        response = client.post('/api/admin/knowledge-keys', json={'name': ' Syntony '})
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        secret = payload['secret']
        self.assertTrue(secret.startswith(keys.PREFIX))
        self.assertEqual(payload['header'], keys.HEADER)
        inserted = [params for text, params in self.cursor.calls
                    if text.startswith('INSERT INTO knowledge_api_keys')][0]
        self.assertEqual(inserted['hash'], keys.digest(secret))
        self.assertEqual(inserted['mark'], keys.mark(secret))
        self.assertEqual((inserted['name'], inserted['role'], inserted['department'], inserted['by']),
                         ('Syntony', 'operator', 1, 3))
        self.assertNotIn(secret, json.dumps(inserted))

    def test_wider_rights_than_an_operator_are_refused(self):
        client = self.build()
        response = client.post('/api/admin/knowledge-keys', json={'name': 'X', 'otp_role': 'admin'})
        self.assertEqual(response.status_code, 400)

    def test_revoke_answers_with_the_key(self):
        client = self.build()
        response = client.post('/api/admin/knowledge-keys/7/revoke')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['key']['id'], 7)


# ── Проводка ────────────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = BOT.read_text(encoding='utf-8-sig')

    def test_the_external_entry_is_registered_without_the_session_decorator(self):
        start = self.bot.index('build_knowledge_blueprint(')
        call = self.bot[start:self.bot.index('))', start)]
        self.assertIn('db=db', call)
        self.assertIn('client_ip=_client_ip', call)
        self.assertNotIn('require_api_key', call)

    def test_the_admin_entry_needs_the_session(self):
        start = self.bot.index('build_knowledge_admin_blueprint(\n')
        call = self.bot[start:self.bot.index('))', start)]
        for token in ('require_api_key=require_api_key', 'resolve_requester=_resolve_requester',
                      'normalize_role=_normalize_user_role'):
            self.assertIn(token, call)

    def test_browsers_cannot_send_the_key_cross_origin(self):
        for line in self.bot.splitlines():
            if 'allow_headers' in line or 'Access-Control-Allow-Headers' in line:
                self.assertNotIn(keys.HEADER.lower(), line.lower())

    def test_the_journal_is_cleaned_daily(self):
        self.assertIn("id='knowledge_api_calls_retention_daily'", self.bot)
        self.assertIn('knowledge_queries.cleanup_calls(cursor, days)', self.bot)

    def test_the_schema_is_deployed_under_its_own_savepoint(self):
        database = (ROOT / 'database.py').read_text(encoding='utf-8-sig')
        self.assertIn('self._init_knowledge_api_schema_tx(cursor)', database)
        self.assertIn('SAVEPOINT knowledge_api_schema', database)

    def test_the_partner_document_and_the_readme_name_the_contract(self):
        doc = DOC.read_text(encoding='utf-8')
        readme = README.read_text(encoding='utf-8')
        for token in (SEARCH_URL, '/api/knowledge/parks', '/api/knowledge/ping', keys.HEADER,
                      'no_data', 'park_match', 'all_parks', 'KNOWLEDGE_API_UNAUTHORIZED',
                      'KNOWLEDGE_API_PARK_NOT_FOUND', 'KNOWLEDGE_API_LIMIT', 'Retry-After'):
            self.assertIn(token, doc, token)
        for token in (SEARCH_URL, keys.HEADER, 'KNOWLEDGE_API_PER_MINUTE', 'KNOWLEDGE_API_LANES',
                      'KNOWLEDGE_API_CALLS_DAYS', 'docs/SYNTONY_KNOWLEDGE_API.md'):
            self.assertIn(token, readme, token)
        for kind in ('article', 'park', 'office', 'offices', 'city', 'cities', 'classifier'):
            self.assertIn('`%s`' % kind, doc, kind)
        for name in knowledge_search.FOUND_BY.values():
            self.assertIn('`%s`' % name, doc, name)

    def test_outcomes_are_the_contract(self):
        self.assertEqual(schema.OUTCOMES,
                         ('ok', 'no_data', 'park_unavailable', 'bad_request', 'limit', 'error'))

    def test_partner_questions_have_a_source_and_a_park_in_the_queue(self):
        from wiki import questions as wiki_questions
        for key in ('source', 'source_label', 'park_name', 'asked_count'):
            self.assertIn(key, wiki_questions._ROW_KEYS)
        wiki_schema = (ROOT / 'wiki' / 'schema.py').read_text(encoding='utf-8')
        for column in ("source VARCHAR(16) NOT NULL DEFAULT 'assistant'", 'source_label VARCHAR(120)',
                       'park_id INTEGER', 'park_name VARCHAR(255)', 'asked_count INTEGER NOT NULL DEFAULT 1',
                       'ALTER COLUMN asker_id DROP NOT NULL'):
            self.assertIn(column, wiki_schema, column)
        jsx = (ROOT / 'src' / 'components' / 'wiki' / 'WikiQuestions.jsx').read_text(encoding='utf-8')
        self.assertIn("item.source === 'api'", jsx)
        sources = (ROOT / 'notifications' / 'sources.py').read_text(encoding='utf-8')
        self.assertIn('COALESCE(asker.name, q.source_label)', sources)

    def test_the_sessions_section_shows_the_keys_tab_to_the_super_admin_only(self):
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertIn("import('./components/knowledge/KnowledgeKeysPanel')", app)
        self.assertIn('canManageKeys={isSuperAdmin}', app)
        self.assertIn("{ value: 'keys', label: 'Ключи' }", app)


if __name__ == '__main__':
    unittest.main()
