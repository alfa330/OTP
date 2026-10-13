# -*- coding: utf-8 -*-
"""Внешний вход раздела «Ссылка на подписание»: ключ, пачка, пределы, журнал.

Сторонний сервис присылает список ИИН и получает ссылки на документы водителей.
У него нет ни учётки, ни QR — вся защита в одном ключе и в том, что стоит вокруг
него. Что здесь сторожится и почему именно это:

* **Не настроено — закрыто.** Ключ не задан или задан криво — 503, а не «пускаем
  всех». Слабый ключ сервер не принимает за ключ вовсе.
* **До ключа не делается ничего.** Ни разбор тела, ни база, ни генератор.
* **Ключ ходит только заголовком X-Api-Key.** Не Authorization (его читает вход
  сотрудников и пишет в лог обработчик 401), не адрес и не тело.
* **«Не спросили» ≠ «не найдено».** Генератор лёг, не успели, ответил
  незнакомыми словами — это error, а не not_found.
* **Пределы.** ИИН в запросе, ИИН в сутки, одновременные запросы, одновременные
  походы к генератору.
* **Журнал.** Каждый спрошенный ИИН — строка; ссылки и ключа в ней нет; журнал не
  записался — ссылки не отдаются.

Сети и базы здесь нет: генератор подменяется, курсор — двойник. Настоящий SQL
исполняется в tests/test_sign_links_external_postgres.py.
"""

import json
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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

from common import log_secrets  # noqa: E402
from sign_links import api_key, batch, external, iin, queries, sapar_link  # noqa: E402
from sign_links.external import build_sign_links_external_blueprint  # noqa: E402

BOT = ROOT / 'bot_schedule2.py'
README = ROOT / 'README.md'
META_JS = ROOT / 'src' / 'components' / 'sign_links' / 'signLinkMeta.js'
VIEW_JSX = ROOT / 'src' / 'components' / 'sign_links' / 'SignLinksView.jsx'

URL = '/api/sign_links/external/links'
GENERATOR_HOST = 'silt.kz'

# Ключи выдуманы; настоящий живёт только в окружении сервера.
KEY = 'FAKE-key-for-tests-0123456789-abcdefghij'
OTHER_KEY = 'FAKE-second-key-for-tests-9876543210-zyx'


def synthetic_iins(count):
    """Верные по алгоритму ИИН на 01.01.1990 — живых людей за ними нет."""
    found = []
    serial = 0
    while len(found) < count:
        head = '9001013%04d' % serial
        serial += 1
        control = iin._checksum(head)
        if control is not None:
            found.append(head + str(control))
    return found


A, B, C, D, E, F, G, H, I, J = synthetic_iins(10)


def link_for(value):
    return {'outcome': 'link', 'link': 'https://sign.example.kz/s/' + value,
            'message': None, 'error': None, 'latency_ms': 700}


NO_DOCS = {'outcome': 'no_documents', 'link': None, 'error': None, 'latency_ms': 300,
           'message': 'Нет документов на подписание. Документы подписаны'}
DOWN = {'outcome': 'unavailable', 'link': None, 'message': None, 'latency_ms': 10000,
        'error': 'форма не открылась: https://tps-public.silt.kz timeout'}
REJECTED = {'outcome': 'rejected', 'link': None, 'error': None, 'latency_ms': 400,
            'message': 'Ошибка формирования ссылки'}


# ─────────────────────────────────────────────────────────────────────────────
# Ключ
# ─────────────────────────────────────────────────────────────────────────────

class KeyParsingTests(unittest.TestCase):
    def test_no_setting_means_no_keys_and_no_error(self):
        self.assertEqual(api_key.parse(''), [])
        self.assertEqual(api_key.parse(None), [])
        self.assertEqual(api_key.configured({}), ([], None))

    def test_a_key_is_kept_as_a_digest_with_a_short_fingerprint(self):
        keys = api_key.parse(KEY)
        self.assertEqual(len(keys), 1)
        mark, digest = keys[0]
        self.assertRegex(mark, r'^[0-9a-f]{8}$')
        self.assertEqual(mark, api_key.fingerprint(KEY))
        self.assertEqual(len(digest), 32)
        # Самого ключа в разобранной настройке нет — только хеш и отпечаток.
        self.assertNotIn(KEY, repr(keys))
        self.assertNotIn(KEY[:12], mark)

    def test_several_keys_for_rotation_any_separator_duplicates_collapse(self):
        for raw in ('%s,%s' % (KEY, OTHER_KEY), '%s %s' % (KEY, OTHER_KEY),
                    '%s;\n%s' % (KEY, OTHER_KEY), ' %s , %s , %s ' % (KEY, OTHER_KEY, KEY)):
            self.assertEqual([mark for mark, _ in api_key.parse(raw)],
                             [api_key.fingerprint(KEY), api_key.fingerprint(OTHER_KEY)], raw)

    def test_a_weak_key_is_a_configuration_error_not_a_key(self):
        weak = {
            'short': 'FAKE-short-key-0123456789',
            'monotone': 'a' * 40,
            'repeated word': 'test' * 10,
            'cyrillic': 'FAKE-ключ-для-тестов-0123456789-abcdefgh',
            'too long': 'FAKE-' + '0123456789abcdefghij' * 11,
        }
        for label, value in weak.items():
            with self.assertRaises(api_key.KeyConfigError, msg=label) as caught:
                api_key.parse(value)
            # Текст ошибки уходит в лог — ключа в нём быть не должно.
            self.assertNotIn(value, str(caught.exception), label)

    def test_one_bad_key_closes_the_whole_list(self):
        """Иначе опечатка в новом ключе незаметно оставила бы рабочим только старый."""
        with self.assertRaises(api_key.KeyConfigError):
            api_key.parse('%s,%s' % (KEY, 'short'))

    def test_a_broken_setting_yields_no_keys_and_is_logged_once(self):
        environ = {api_key.ENV_NAME: 'короткий'}
        with self.assertLogs(level='ERROR') as logs:
            self.assertEqual(api_key.configured(environ)[0], [])
        self.assertIn(api_key.ENV_NAME, logs.output[0])
        self.assertNotIn('короткий', logs.output[0])
        # Второй запрос с той же настройкой лог не засоряет.
        with self.assertNoLogs(level='ERROR'):
            keys, error = api_key.configured(environ)
        self.assertEqual(keys, [])
        self.assertTrue(error)

    def test_a_changed_setting_is_picked_up(self):
        self.assertEqual(api_key.configured({api_key.ENV_NAME: KEY})[0][0][0],
                         api_key.fingerprint(KEY))
        self.assertEqual(api_key.configured({api_key.ENV_NAME: OTHER_KEY})[0][0][0],
                         api_key.fingerprint(OTHER_KEY))


class KeyMatchingTests(unittest.TestCase):
    def setUp(self):
        self.keys = api_key.parse('%s,%s' % (KEY, OTHER_KEY))

    def test_the_holder_is_recognised_by_either_key(self):
        self.assertEqual(api_key.match(KEY, self.keys), api_key.fingerprint(KEY))
        self.assertEqual(api_key.match(OTHER_KEY, self.keys), api_key.fingerprint(OTHER_KEY))
        self.assertEqual(api_key.match('  %s\n' % KEY, self.keys), api_key.fingerprint(KEY))

    def test_nobody_else_is(self):
        for presented in (None, '', ' ', KEY[:-1], KEY + 'x', KEY.lower(), KEY.upper(),
                          api_key.fingerprint(KEY), 'ключ с кириллицей', '\udcff'):
            self.assertIsNone(api_key.match(presented, self.keys), repr(presented))

    def test_without_configured_keys_nothing_matches(self):
        self.assertIsNone(api_key.match(KEY, []))
        self.assertIsNone(api_key.match('', []))

    def test_comparison_is_constant_time(self):
        source = (ROOT / 'sign_links' / 'api_key.py').read_text(encoding='utf-8')
        self.assertIn('hmac.compare_digest(candidate, digest)', source)

    def test_the_log_scrubber_covers_the_setting(self):
        """Имя переменной подпадает под чистку логов, и ключ допустимой длины —
        тоже: иначе случайно напечатанный ключ остался бы в логе Render."""
        self.assertTrue(log_secrets._SECRET_NAME_RE.search(api_key.ENV_NAME))
        self.assertGreaterEqual(api_key.MIN_KEY_LENGTH, log_secrets._MIN_SECRET_LEN)
        self.assertLessEqual(api_key.MAX_KEY_LENGTH, log_secrets._MAX_SECRET_LEN)
        try:
            log_secrets.refresh_env_secrets({api_key.ENV_NAME: KEY})
            self.assertNotIn(KEY, log_secrets.scrub('заголовок запроса: %s' % KEY))
        finally:
            import os
            log_secrets.refresh_env_secrets(os.environ)


# ─────────────────────────────────────────────────────────────────────────────
# Пачка
# ─────────────────────────────────────────────────────────────────────────────

class BatchTests(unittest.TestCase):
    def setUp(self):
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.gate = threading.Event()
        self.addCleanup(self.executor.shutdown, wait=True)
        self.addCleanup(self.gate.set)

    def test_every_iin_gets_its_own_answer(self):
        plan = {A: link_for(A), B: NO_DOCS, C: REJECTED}
        results, stopped = batch.run([A, B, C], lambda value: plan[value], executor=self.executor)
        self.assertEqual(stopped, '')
        self.assertEqual(results[A]['link'], link_for(A)['link'])
        self.assertEqual(results[B]['outcome'], 'no_documents')
        self.assertEqual(results[C]['outcome'], 'rejected')
        for value in (A, B, C):
            self.assertTrue(results[value]['asked'])
            self.assertIsNone(results[value]['skipped'])

    def test_no_more_than_lanes_calls_run_at_once(self):
        lock = threading.Lock()
        state = {'now': 0, 'peak': 0}
        step = threading.Event()

        def generate(value):
            with lock:
                state['now'] += 1
                state['peak'] = max(state['peak'], state['now'])
            step.wait(0.02)
            with lock:
                state['now'] -= 1
            return link_for(value)

        iins = synthetic_iins(12)
        results, stopped = batch.run(iins, generate, executor=self.executor, lanes=2)
        self.assertEqual((len(results), stopped), (12, ''))
        self.assertEqual(state['peak'], 2)

    def test_a_dead_generator_stops_the_batch_instead_of_a_hundred_timeouts(self):
        asked = []

        def generate(value):
            asked.append(value)
            return dict(DOWN)

        iins = synthetic_iins(40)
        results, stopped = batch.run(iins, generate, executor=self.executor, lanes=1)
        self.assertEqual(stopped, batch.VENDOR_DOWN)
        self.assertEqual(asked, iins[:batch.MAX_UNAVAILABLE_IN_ROW])
        self.assertEqual(len(results), 40)
        for value in iins[batch.MAX_UNAVAILABLE_IN_ROW:]:
            self.assertEqual(results[value]['outcome'], 'unavailable')
            self.assertFalse(results[value]['asked'])
            self.assertEqual(results[value]['skipped'], batch.VENDOR_DOWN)

    def test_an_answer_between_failures_keeps_the_batch_going(self):
        """Останавливают отказы ПОДРЯД: один упрямый ИИН пачку не роняет."""
        iins = synthetic_iins(9)
        plan = {value: (dict(DOWN) if index % 3 else link_for(value))
                for index, value in enumerate(iins)}
        results, stopped = batch.run(iins, lambda value: plan[value],
                                     executor=self.executor, lanes=1)
        self.assertEqual(stopped, '')
        self.assertTrue(all(result['asked'] for result in results.values()))

    def test_the_budget_cuts_the_batch_and_tells_asked_from_not_asked(self):
        started = threading.Semaphore(0)

        def generate(value):
            started.release()
            self.gate.wait(5)
            return link_for(value)

        iins = synthetic_iins(6)
        results, stopped = batch.run(iins, generate, executor=self.executor, lanes=4,
                                     budget_sec=0.3)
        self.assertEqual(stopped, batch.BUDGET)
        for _ in range(4):
            self.assertTrue(started.acquire(timeout=2))
        in_flight = [value for value in iins if results[value]['asked']]
        waiting = [value for value in iins if not results[value]['asked']]
        # Четыре места заняты — их генератор уже спрошен; до двух оставшихся
        # очередь не дошла.
        self.assertEqual((len(in_flight), len(waiting)), (4, 2))
        for value in iins:
            self.assertEqual(results[value]['outcome'], 'unavailable')
            self.assertEqual(results[value]['skipped'], batch.BUDGET)
            self.assertIsNone(results[value]['link'])

    def test_a_crashing_client_is_an_unavailable_answer_without_the_iin_in_the_log(self):
        def generate(value):
            raise RuntimeError('не разобрали страницу для %s' % value)

        with self.assertLogs(level='WARNING') as logs:
            results, _stopped = batch.run([A], generate, executor=self.executor)
        self.assertEqual(results[A]['outcome'], 'unavailable')
        self.assertTrue(results[A]['asked'])
        self.assertIn('RuntimeError', results[A]['error'])
        self.assertNotIn(A, results[A]['error'])
        self.assertNotIn(A, '\n'.join(logs.output))

    def test_the_shared_lanes_are_one_small_pool_for_the_whole_process(self):
        pool = batch.shared_executor()
        self.assertIs(pool, batch.shared_executor())
        self.assertEqual(pool._max_workers, batch.LANES)
        self.assertLessEqual(batch.LANES, 4)


class GeneratorTimeoutTests(unittest.TestCase):
    """Пачке нужен срок короче, чем оператору: sapar_link.generate берёт его аргументом."""

    class _Response:
        text = '<html></html>'
        status_code = 200

        def raise_for_status(self):
            return None

    class _Session:
        def __init__(self):
            self.headers = {}
            self.timeouts = []

        def get(self, url, **kwargs):
            self.timeouts.append(kwargs.get('timeout'))
            response = GeneratorTimeoutTests._Response()
            response.text = '<input name="__RequestVerificationToken" value="T" />'
            return response

        def post(self, url, **kwargs):
            self.timeouts.append(kwargs.get('timeout'))
            return GeneratorTimeoutTests._Response()

    def _run(self, **kwargs):
        session = self._Session()
        original = sapar_link.requests.Session
        sapar_link.requests.Session = lambda: session
        try:
            with self.assertLogs(level='WARNING'):  # пустая страница — «незнакомый ответ»
                sapar_link.generate(A, **kwargs)
        finally:
            sapar_link.requests.Session = original
        return session.timeouts

    def test_the_timeout_reaches_both_requests(self):
        self.assertEqual(self._run(timeout=7), [7, 7])

    def test_without_it_the_operator_default_stays(self):
        self.assertEqual(self._run(), [sapar_link.TIMEOUT, sapar_link.TIMEOUT])
        self.assertLess(batch.CALL_TIMEOUT, sapar_link.TIMEOUT)


# ─────────────────────────────────────────────────────────────────────────────
# Ручка
# ─────────────────────────────────────────────────────────────────────────────

class _Cursor:
    """Курсор-двойник: помнит SQL и параметры, на счёт за сегодня отвечает `used`."""

    def __init__(self, used=0, fail_insert=False):
        self.calls = []
        self.used = used
        self.fail_insert = fail_insert
        self._last = None

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        self.calls.append((text, params))
        if text.startswith('INSERT') and self.fail_insert:
            raise RuntimeError('база недоступна')
        self._last = (self.used,) if 'count(*)' in text else None

    def fetchone(self):
        return self._last

    def inserted(self):
        return [params for text, params in self.calls
                if text.startswith('INSERT INTO sign_link_requests')]


@unittest.skipIf(Flask is None, 'flask не установлен')
class Harness(unittest.TestCase):
    def build(self, *, plan=None, used=0, fail_insert=False, environ=None,
              max_iins=None, daily_limit=None, generate='fake'):
        """Клиент ручки. plan — {ИИН: исход | функция}; чего в нём нет — ссылка."""
        self.plan = plan or {}
        self.asked = []
        lock = threading.Lock()

        def fake_generate(value):
            with lock:
                self.asked.append(value)
            answer = self.plan.get(value)
            if callable(answer):
                return answer(value)
            return dict(answer) if answer else link_for(value)

        self.cursor = _Cursor(used=used, fail_insert=fail_insert)
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield self.cursor

        db._get_cursor = _get_cursor
        executor = ThreadPoolExecutor(max_workers=4)
        self.addCleanup(executor.shutdown, wait=True)
        self.app = Flask(__name__)
        self.app.register_blueprint(build_sign_links_external_blueprint(
            db=db,
            client_ip=lambda: '203.0.113.7',
            generate=fake_generate if generate == 'fake' else generate,
            executor=executor,
            environ={api_key.ENV_NAME: KEY} if environ is None else environ,
            max_iins=max_iins,
            daily_limit=daily_limit,
        ))
        self.app.config['TESTING'] = True
        return self.app.test_client()

    def post(self, client, iins, key=KEY):
        headers = {api_key.HEADER: key} if key is not None else {}
        return client.post(URL, json={'iins': iins}, headers=headers)

    def assert_untouched(self):
        """Ни генератора, ни базы: запрос остановлен на входе."""
        self.assertEqual(self.asked, [])
        self.assertEqual(self.cursor.calls, [])


class DoorTests(Harness):
    def test_without_a_key_on_the_server_the_door_is_closed_not_open(self):
        client = self.build(environ={})
        for key in (None, '', KEY, 'что угодно'):
            response = self.post(client, [A], key=key)
            self.assertEqual(response.status_code, 503, repr(key))
            self.assertEqual(response.get_json()['code'], 'SIGN_LINKS_API_NOT_CONFIGURED')
        self.assert_untouched()

    def test_a_weak_key_on_the_server_does_not_open_the_door_even_to_itself(self):
        weak = 'FAKE-short-key'
        client = self.build(environ={api_key.ENV_NAME: weak})
        with self.assertLogs(level='ERROR'):
            response = self.post(client, [A], key=weak)
        self.assertEqual(response.status_code, 503)
        self.assert_untouched()

    def test_a_missing_and_a_wrong_key_get_the_same_refusal(self):
        client = self.build()
        answers = []
        for key in (None, '', OTHER_KEY, KEY[:-1], KEY + '0'):
            response = self.post(client, [A], key=key)
            self.assertEqual(response.status_code, 401, repr(key))
            answers.append(response.get_data(as_text=True))
        # Чужому незачем знать, на чём он споткнулся.
        self.assertEqual(len(set(answers)), 1)
        self.assertEqual(json.loads(answers[0])['code'], 'SIGN_LINKS_API_UNAUTHORIZED')
        self.assert_untouched()

    def test_the_key_travels_in_its_own_header_only(self):
        """Authorization читает вход сотрудников, адрес оседает в журналах прокси."""
        client = self.build()
        body = {'iins': [A]}
        attempts = (
            client.post(URL, json=body, headers={'Authorization': 'Bearer ' + KEY}),
            client.post(URL, json=body, headers={'Authorization': KEY}),
            client.post(URL + '?api_key=' + KEY, json=body),
            client.post(URL + '?key=' + KEY + '&X-Api-Key=' + KEY, json=body),
            client.post(URL, json=dict(body, key=KEY, api_key=KEY)),
            client.post(URL, json=body, headers={'X-Auth-Token': KEY}),
        )
        for response in attempts:
            self.assertEqual(response.status_code, 401)
        self.assert_untouched()
        self.assertEqual(api_key.HEADER, 'X-Api-Key')

    def test_nothing_is_read_before_the_key(self):
        """Кривое и огромное тело от чужого — тот же 401, а не разбор."""
        client = self.build()
        wrong = {api_key.HEADER: OTHER_KEY}
        self.assertEqual(client.post(URL, data='{не json', headers=wrong).status_code, 401)
        self.assertEqual(client.post(URL, data='x' * (external.MAX_BODY_BYTES + 10),
                                     headers=wrong).status_code, 401)
        self.assertEqual(self.post(client, synthetic_iins(60), key=OTHER_KEY).status_code, 401)
        self.assert_untouched()

    def test_only_post_is_served(self):
        client = self.build()
        for method in ('get', 'put', 'delete', 'patch'):
            response = getattr(client, method)(URL, headers={api_key.HEADER: KEY})
            self.assertEqual(response.status_code, 405, method)

    def test_either_key_opens_it_during_rotation(self):
        client = self.build(environ={api_key.ENV_NAME: '%s,%s' % (KEY, OTHER_KEY)})
        self.assertEqual(self.post(client, [A], key=KEY).status_code, 200)
        self.assertEqual(self.post(client, [B], key=OTHER_KEY).status_code, 200)
        marks = [row['user_name'] for row in self.cursor.inserted()]
        self.assertIn(api_key.fingerprint(KEY), marks[0])
        self.assertIn(api_key.fingerprint(OTHER_KEY), marks[1])


class AnswerTests(Harness):
    def test_every_iin_gets_a_link_or_not_found_in_the_order_sent(self):
        client = self.build(plan={B: NO_DOCS})
        response = self.post(client, [A, B, C])
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['results'], [
            {'iin': A, 'status': 'link', 'link': 'https://sign.example.kz/s/' + A},
            {'iin': B, 'status': 'not_found',
             'message': 'Нет документов на подписание. Документы подписаны'},
            {'iin': C, 'status': 'link', 'link': 'https://sign.example.kz/s/' + C},
        ])
        self.assertEqual(payload['summary'],
                         {'total': 3, 'link': 2, 'not_found': 1, 'invalid': 0, 'error': 0})
        self.assertEqual(sorted(self.asked), sorted([A, B, C]))

    def test_not_asked_is_never_reported_as_not_found(self):
        """Генератор лёг или ответил незнакомыми словами — это «повторите», а не
        «документов нет»: на втором водителю отвечают, что подписывать нечего."""
        client = self.build(plan={A: DOWN, B: REJECTED})
        results = self.post(client, [A, B]).get_json()['results']
        self.assertEqual(results[0], {'iin': A, 'status': 'error', 'reason': 'unavailable',
                                      'message': external.UNAVAILABLE_MESSAGE})
        self.assertEqual(results[1], {'iin': B, 'status': 'error', 'reason': 'rejected',
                                      'message': 'Ошибка формирования ссылки'})
        self.assertNotIn('not_found', [item['status'] for item in results])

    def test_a_link_outcome_without_a_link_is_an_error_not_an_empty_link(self):
        client = self.build(plan={A: {'outcome': 'link', 'link': None, 'message': None,
                                      'error': None, 'latency_ms': 5}})
        item = self.post(client, [A]).get_json()['results'][0]
        self.assertEqual((item['status'], item['reason']), ('error', 'unavailable'))
        self.assertNotIn('link', item)
        row = self.cursor.inserted()[0]
        self.assertEqual((row['outcome'], row['link_issued']), (['unavailable'], [False]))

    def test_what_is_not_an_iin_is_answered_without_the_generator(self):
        client = self.build()
        response = self.post(client, ['900101300008', '12345', 'привет', '', None, True,
                                      {'iin': A}, 12.5])
        self.assertEqual(response.status_code, 200)
        results = response.get_json()['results']
        self.assertEqual([item['status'] for item in results], ['invalid'] * 8)
        self.assertEqual([item['reason'] for item in results],
                         ['checksum', 'length', 'digits', 'empty', 'empty', 'empty',
                          'empty', 'empty'])
        self.assertEqual(results[0]['message'], iin.ERRORS['checksum'])
        self.assertEqual(results[3]['message'], external.EMPTY_MESSAGE)
        self.assertEqual(self.asked, [])

    def test_garbage_no_database_accepts_does_not_sink_the_batch(self):
        """NUL и непарный суррогат из чужого JSON: ни Postgres, ни UTF-8 их не
        примут, и из-за одной такой строки упала бы запись всей пачки — вместе
        с уже выданными ссылками."""
        client = self.build()
        body = '{"iins": ["%s", "12\\u000034", "\\ud800", "ab\\u0007cd"]}' % A
        response = client.post(URL, data=body,
                               headers={api_key.HEADER: KEY, 'User-Agent': 'bot\x07/1'})
        self.assertEqual(response.status_code, 200)
        results = response.get_json()['results']
        self.assertEqual([(item['status'], item.get('reason')) for item in results],
                         [('link', None), ('invalid', 'length'), ('invalid', 'empty'),
                          ('invalid', 'digits')])
        row = self.cursor.inserted()[0]
        self.assertEqual(row['iin'], [A, '1234', 'abcd'])
        self.assertEqual(row['ua'], 'bot/1')
        for value in row['iin'] + [row['ua'], row['user_name']]:
            self.assertTrue(value.isprintable(), ascii(value))

    def test_digits_of_other_scripts_do_not_reach_the_generator(self):
        client = self.build()
        results = self.post(client, ['9001013٠٠٠٠7', '９００１０１３００００７']).get_json()['results']
        self.assertEqual([(item['status'], item['reason']) for item in results],
                         [('invalid', 'digits')] * 2)
        self.assertEqual(self.asked, [])

    def test_separators_and_numbers_are_accepted_and_echoed_as_sent(self):
        spaced = '%s %s %s' % (A[:6], A[6:9], A[9:])
        client = self.build()
        results = self.post(client, [spaced, int(B)]).get_json()['results']
        self.assertEqual([(item['iin'], item['status']) for item in results],
                         [(spaced, 'link'), (B, 'link')])
        self.assertEqual(sorted(self.asked), sorted([A, B]))

    def test_a_repeated_iin_is_asked_once_and_answered_every_time(self):
        client = self.build()
        payload = self.post(client, [A, B, A, A]).get_json()
        self.assertEqual([item['status'] for item in payload['results']], ['link'] * 4)
        self.assertEqual(payload['results'][0]['link'], payload['results'][3]['link'])
        self.assertEqual(sorted(self.asked), sorted([A, B]))
        self.assertEqual(self.cursor.inserted()[0]['iin'], [A, B])
        self.assertEqual(payload['limits']['used_today'], 2)

    def test_a_dead_generator_gives_errors_for_the_whole_list_quickly(self):
        iins = synthetic_iins(30)
        client = self.build(plan={value: DOWN for value in iins})
        payload = self.post(client, iins).get_json()
        self.assertEqual(payload['summary']['error'], 30)
        self.assertEqual({item['reason'] for item in payload['results']}, {'unavailable'})
        # Спросили несколько, а не все тридцать; в журнале — ровно спрошенные.
        self.assertLess(len(self.asked), 12)
        self.assertEqual(sorted(self.cursor.inserted()[0]['iin']), sorted(self.asked))

    def test_what_did_not_fit_the_budget_is_an_error_to_retry(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        original = batch.BUDGET_SEC
        batch.BUDGET_SEC = 0.3
        self.addCleanup(setattr, batch, 'BUDGET_SEC', original)

        def stuck(value):
            gate.wait(5)
            return link_for(value)

        iins = synthetic_iins(6)
        client = self.build(plan={value: stuck for value in iins})
        payload = self.post(client, iins).get_json()
        self.assertEqual({(item['status'], item['reason']) for item in payload['results']},
                         {('error', 'timeout')})
        self.assertEqual({item['message'] for item in payload['results']},
                         {external.TIMEOUT_MESSAGE})
        # В журнале — четверо, кого генератор уже спрашивал; двое не начаты.
        row = self.cursor.inserted()[0]
        self.assertEqual(len(row['iin']), 4)
        self.assertEqual(set(row['outcome']), {'unavailable'})
        self.assertEqual(payload['limits']['used_today'], 4)

    def test_no_answer_carries_the_generator_address_the_reason_or_the_key(self):
        client = self.build(plan={A: DOWN, B: REJECTED, C: NO_DOCS})
        answers = [
            self.post(client, [A, B, C, '123']),
            self.post(client, [A], key=OTHER_KEY),
            client.post(URL, data='{', headers={api_key.HEADER: KEY}),
        ]
        for response in answers:
            text = response.get_data(as_text=True)
            self.assertNotIn(GENERATOR_HOST, text)
            self.assertNotIn('timeout', text)
            self.assertNotIn(KEY, text)
            self.assertNotIn(api_key.fingerprint(KEY), text)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        # А в журнале техническая причина есть — админу.
        self.assertIn('timeout', self.cursor.inserted()[0]['error_text'][0])

    def test_the_default_client_asks_the_generator_with_the_batch_timeout(self):
        seen = []
        original = sapar_link.generate

        def recording(value, timeout=None):
            seen.append((value, timeout))
            return link_for(value)

        sapar_link.generate = recording
        self.addCleanup(setattr, sapar_link, 'generate', original)
        client = self.build(generate=None)
        self.assertEqual(self.post(client, [A]).status_code, 200)
        self.assertEqual(seen, [(A, batch.CALL_TIMEOUT)])


class RequestShapeTests(Harness):
    def _raw(self, client, data, **headers):
        return client.post(URL, data=data, headers={api_key.HEADER: KEY, **headers})

    def test_the_body_is_a_json_object_with_a_list(self):
        client = self.build()
        for data in ('', '{', 'null', '[]', '"%s"' % A, json.dumps([A]),
                     json.dumps({'iin': A}), json.dumps({'iins': A}),
                     json.dumps({'iins': {'0': A}}), json.dumps({'iins': []})):
            response = self._raw(client, data)
            self.assertEqual(response.status_code, 400, data)
            self.assertEqual(response.get_json()['code'], 'SIGN_LINKS_API_BAD_REQUEST', data)
        self.assert_untouched()

    def test_hostile_json_is_a_400_not_a_500(self):
        client = self.build()
        for data in ('[' * 30000, '{"iins": [' + '9' * 5000 + ']}', b'\xff\xfe\x00'):
            self.assertEqual(self._raw(client, data).status_code, 400)
        self.assert_untouched()

    def test_the_content_type_does_not_matter(self):
        client = self.build()
        response = self._raw(client, json.dumps({'iins': [A]}), **{'Content-Type': 'text/plain'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['results'][0]['status'], 'link')

    def test_too_many_iins_are_refused_whole_with_the_limit_named(self):
        client = self.build(max_iins=5)
        response = self.post(client, synthetic_iins(6))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()['code'], 'SIGN_LINKS_API_TOO_MANY')
        self.assertEqual(response.get_json()['max_iins'], 5)
        self.assert_untouched()
        self.assertEqual(self.post(client, synthetic_iins(5)).status_code, 200)

    def test_an_oversized_body_is_refused_before_parsing(self):
        client = self.build()
        padded = json.dumps({'iins': [A], 'pad': 'x' * external.MAX_BODY_BYTES})
        response = self._raw(client, padded)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()['code'], 'SIGN_LINKS_API_TOO_LARGE')
        self.assert_untouched()

    def test_a_body_of_undeclared_length_is_read_no_further_than_the_cap(self):
        """Без Content-Length (chunked) заявленного размера нет — потолок держит само чтение."""
        client = self.build()
        chunked = {'headers': {api_key.HEADER: KEY, 'Transfer-Encoding': 'chunked'},
                   'environ_overrides': {'wsgi.input_terminated': True}}
        padded = json.dumps({'iins': [A], 'pad': 'x' * external.MAX_BODY_BYTES})
        response = client.post(URL, data=padded, **chunked)
        self.assertEqual(response.status_code, 413)
        self.assert_untouched()
        response = client.post(URL, data=json.dumps({'iins': [A]}), **chunked)
        self.assertEqual(response.status_code, 200)

    def test_the_defaults_fit_one_answer_into_the_budget(self):
        """Полсотни ИИН в четыре места — заметно быстрее срока пачки, а самый
        длинный запрос к генератору короче его."""
        self.assertLessEqual(external.MAX_IINS, external.MAX_IINS_CEILING)
        self.assertLess(batch.CALL_TIMEOUT, batch.BUDGET_SEC)
        self.assertGreaterEqual(external.MAX_BODY_BYTES, external.MAX_IINS_CEILING * 64)


class LimitTests(Harness):
    def test_the_daily_limit_is_checked_before_the_generator(self):
        client = self.build(used=9, daily_limit=10)
        response = self.post(client, [A, B])
        self.assertEqual(response.status_code, 429)
        payload = response.get_json()
        self.assertEqual(payload['code'], 'SIGN_LINKS_API_DAILY_LIMIT')
        self.assertEqual(payload['limits'], {'per_day': 10, 'used_today': 9, 'left_today': 1})
        self.assertEqual(self.asked, [])
        self.assertEqual(self.cursor.inserted(), [])
        # Один оставшийся ИИН проходит.
        payload = self.post(client, [A]).get_json()
        self.assertEqual(payload['results'][0]['status'], 'link')
        self.assertEqual(payload['limits'], {'per_day': 10, 'used_today': 10, 'left_today': 0})

    def test_the_limit_counts_what_is_logged_typos_included(self):
        """Иначе утёкший ключ завалил бы журнал мусором, не тратя предел."""
        client = self.build(used=9, daily_limit=10)
        self.assertEqual(self.post(client, [A, '12345']).status_code, 429)
        sql, params = self.cursor.calls[0]
        self.assertIn('r.user_id IS NULL', sql)
        self.assertNotIn('outcome', sql)
        self.assertEqual(params['api_role'], queries.API_ROLE)

    def test_a_list_of_blanks_touches_neither_the_base_nor_the_limit(self):
        client = self.build(used=10, daily_limit=10)
        response = self.post(client, ['', None])
        self.assertEqual(response.status_code, 200)
        self.assert_untouched()

    def test_the_fourth_simultaneous_request_is_refused_not_queued(self):
        gate = threading.Event()
        entered = threading.Semaphore(0)

        def slow(value):
            entered.release()
            gate.wait(5)
            return link_for(value)

        client = self.build(plan={A: slow, B: slow, C: slow})
        statuses = []

        def call(value):
            response = self.app.test_client().post(
                URL, json={'iins': [value]}, headers={api_key.HEADER: KEY})
            statuses.append(response.status_code)

        threads = [threading.Thread(target=call, args=(value,)) for value in (A, B, C)]
        for thread in threads:
            thread.start()
        try:
            for _ in threads:
                self.assertTrue(entered.acquire(timeout=5))
            response = self.post(client, [D])
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.get_json()['code'], 'SIGN_LINKS_API_BUSY')
            self.assertEqual(response.headers['Retry-After'], str(external.RETRY_AFTER_SEC))
            self.assertNotIn(D, self.asked)
        finally:
            gate.set()
            for thread in threads:
                thread.join(5)
        self.assertEqual(statuses, [200, 200, 200])
        self.assertEqual(external.MAX_IN_FLIGHT, 3)
        # Место освободилось — следующий запрос проходит.
        self.assertEqual(self.post(client, [D]).status_code, 200)

    def test_a_refused_request_gives_its_place_back(self):
        """Отказ по дневному пределу не должен съедать место навсегда."""
        client = self.build(used=10, daily_limit=10)
        for _ in range(external.MAX_IN_FLIGHT + 2):
            self.assertEqual(self.post(client, [A]).status_code, 429)
            self.assertEqual(self.post(client, [A]).get_json()['code'],
                             'SIGN_LINKS_API_DAILY_LIMIT')


class JournalTests(Harness):
    def test_every_asked_iin_is_one_row_without_the_link_and_without_the_key(self):
        client = self.build(plan={B: NO_DOCS, C: DOWN})
        response = client.post(URL, json={'iins': [A, B, C, '12345']},
                               headers={api_key.HEADER: KEY, 'User-Agent': 'partner-bot/2.1'})
        self.assertEqual(response.status_code, 200)
        rows = self.cursor.inserted()
        self.assertEqual(len(rows), 1)  # одна пачка — один INSERT
        row = rows[0]
        self.assertEqual(row['iin'], [A, B, C, '12345'])
        self.assertEqual(row['outcome'], ['link', 'no_documents', 'unavailable', 'invalid'])
        self.assertEqual(row['link_issued'], [True, False, False, False])
        self.assertEqual(row['link_host'], ['sign.example.kz', None, None, None])
        self.assertEqual(row['latency_ms'], [700, 300, 10000, None])
        self.assertEqual(row['error_text'][3], 'length')
        self.assertIn('Нет документов', row['vendor_message'][1])
        # Кто: не сотрудник, а внешний сервис с отпечатком ключа.
        self.assertEqual(row['user_role'], queries.API_ROLE)
        self.assertEqual(row['user_name'],
                         queries.API_CLIENT_NAME % api_key.fingerprint(KEY))
        self.assertEqual(row['ip'], '203.0.113.7')
        self.assertEqual(row['ua'], 'partner-bot/2.1')
        # Ни ссылки, ни ключа в журнал не уходит.
        self.assertNotIn('sign.example.kz/s/', repr(row))
        self.assertNotIn(KEY, repr(row))
        self.assertEqual(response.get_json()['limits']['used_today'], 4)

    def test_the_row_is_written_for_nobody_and_no_department(self):
        """Отдела нет — глава отдела такие строки не видит, только глобальный админ."""
        sql = ' '.join(queries._API_INSERT_SQL.split())
        self.assertIn('SELECT NULL::integer, %(user_name)s, %(user_role)s, NULL::integer, NULL,',
                      sql)
        for forbidden in ('link_url', 't.link,', '%(link)s'):
            self.assertNotIn(forbidden, sql)

    def test_without_the_journal_no_link_leaves(self):
        client = self.build(fail_insert=True)
        with self.assertLogs(level='ERROR'):
            response = self.post(client, [A])
        self.assertEqual(response.status_code, 500)
        text = response.get_data(as_text=True)
        self.assertEqual(response.get_json()['code'], 'SIGN_LINKS_API_FAILED')
        self.assertNotIn('sign.example.kz', text)
        self.assertNotIn('база недоступна', text)

    def test_the_log_line_counts_but_names_neither_iin_nor_link(self):
        client = self.build(plan={B: NO_DOCS})
        with self.assertLogs(level='INFO') as logs:
            self.post(client, [A, B])
        text = '\n'.join(logs.output)
        self.assertIn(api_key.fingerprint(KEY), text)
        for secret in (A, B, 'sign.example.kz', KEY):
            self.assertNotIn(secret, text)


class JournalSourceFilterTests(unittest.TestCase):
    """Запросы внешнего сервиса лежат в общем журнале и отделяются отбором «Источник»."""

    def test_the_source_becomes_a_three_way_flag(self):
        self.assertIs(queries._journal_params({'source': 'api'})['api_rows'], True)
        self.assertIs(queries._journal_params({'source': 'staff'})['api_rows'], False)
        for value in (None, '', 'bogus'):
            self.assertIsNone(queries._journal_params({'source': value})['api_rows'], value)
        self.assertEqual(queries._journal_params({})['api_role'], queries.API_ROLE)
        self.assertEqual(queries.SOURCES, ('staff', 'api'))

    def test_page_and_count_filter_by_the_source_alike(self):
        condition = ('(%(api_rows)s IS NULL OR (r.user_role IS NOT DISTINCT FROM %(api_role)s) '
                     '= %(api_rows)s)')
        for sql in (queries._JOURNAL_PAGE_SQL, queries._JOURNAL_COUNT_SQL):
            self.assertIn(condition, ' '.join(sql.split()))

    def test_the_frontend_twin_names_the_same_role_and_sources(self):
        meta = META_JS.read_text(encoding='utf-8')
        self.assertIn("export const API_ROLE = '%s';" % queries.API_ROLE, meta)
        for source in queries.SOURCES:
            self.assertIn("    %s: '" % source, meta, source)
        view = VIEW_JSX.read_text(encoding='utf-8')
        self.assertIn("search.set('source', filters.source)", view)


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = BOT.read_text(encoding='utf-8-sig')

    def test_the_blueprint_is_registered_without_the_session_decorator(self):
        """require_api_key — вход сотрудника по сессии; у внешнего сервиса её нет,
        и с этим декоратором ручка отвечала бы 401 на верный ключ."""
        start = self.bot.index('build_sign_links_external_blueprint(\n')
        call = self.bot[start:self.bot.index('))', start)]
        self.assertIn('db=db', call)
        self.assertIn('client_ip=_client_ip', call)
        self.assertNotIn('require_api_key', call)
        self.assertNotIn('generate', call)

    def test_browsers_cannot_send_the_key_cross_origin(self):
        """Заголовка ключа нет в списке CORS: чужая страница его не отправит."""
        for line in self.bot.splitlines():
            if 'allow_headers' in line or 'Access-Control-Allow-Headers' in line:
                self.assertNotIn(api_key.HEADER.lower(), line.lower())

    def test_the_readme_names_the_setting_the_header_and_the_address(self):
        readme = README.read_text(encoding='utf-8')
        for token in (api_key.ENV_NAME, api_key.HEADER, URL, 'SIGN_LINKS_API_MAX_IINS',
                      'SIGN_LINKS_API_DAILY_LIMIT'):
            self.assertIn(token, readme, token)

    def test_statuses_are_the_contract(self):
        self.assertEqual(external.STATUSES, ('link', 'not_found', 'invalid', 'error'))
        readme = README.read_text(encoding='utf-8')
        for status in external.STATUSES:
            self.assertIn('`%s`' % status, readme, status)


if __name__ == '__main__':
    unittest.main()
