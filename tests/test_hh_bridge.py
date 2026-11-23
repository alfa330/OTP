# -*- coding: utf-8 -*-
"""Мост hh.kz на VM шлюза (cdr_bridge/hh_bridge.py): кабинет работодателя → «Воронка найма» портала.

Сайт hh — двойник с той же формой ответов, что у живого кабинета 23.11.2026 (страница «Вакансии» с начальным
состоянием HH-Lux-InitialState, счётчики /shards/employer/candidates/counts, выдача
/shards/employer/vacancyresponses/candidates_list). Портал — настоящий Blueprint ручки с поддельным сервисом: подпись
проверяется по-настоящему.

Что закреплено:
  * разбор: кто вошёл и какие вакансии активны; отклик — ФИО, телефон, город, хеш резюме из выдачи, приглашения
    работодателя — мимо; страница входа и чужой тип пользователя — «нужен вход», защита сайта — «не пускает»;
  * первый обмен — за отсечкой: без неё hh не читается; отклики старше отсечки порталу не уходят;
  * полная выдача — только когда сдвинулись счётчики вакансии; выключенную вакансию мост не трогает вовсе;
  * отклик «отдан» только по исходу портала; портал лёг — отклики ждут в очереди, в том числе через перезапуск, а
    hh не читается, пока портал не ответит;
  * пауза — та, что назвал портал (случайная, ночью до двух часов), без своего разброса; беда с hh — свой отступ, но
    не короче паузы портала, и портал о нём знает;
  * hh обновил cookie — они дописаны в файл сессии; пульс — только при живом входе и принятом докладе, в длинной
    паузе — раз в 5 минут.
"""
import html as html_lib
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr_bridge import hh_bridge, signing  # noqa: E402
from hiring import routes as hiring_routes  # noqa: E402

PRIVATE_B64, PUBLIC_B64, KEY_ID = signing.generate()
OPERATOR, MARKETER = '139079696', '139508734'


def vacancies_page(user_type='employer', escaped=True):
    state = {'userType': user_type, 'employerId': '10106941', 'employerName': 'Ресурс Плюс KZ',
             'employerManager': {'id': '17061955', 'firstName': 'Руслан', 'lastName': 'Тестов'},
             'managerVacancies': {'vacanciesGroups': {'list': [
                 {'vacancyId': int(OPERATOR), 'name': 'Оператор call-центра', 'area': {'name': 'Алматы'},
                  'status': {'archived': False}, 'negotiations': {'responsesCount': 152}},
                 {'vacancyId': int(MARKETER), 'name': 'Младший маркетолог', 'area': {'name': 'Алматы'},
                  'status': {'archived': False}, 'negotiations': {'responsesCount': 250}},
                 {'vacancyId': 1, 'name': 'Архивная', 'status': {'archived': True}},
             ]}}}
    text = json.dumps(state, ensure_ascii=False)
    if escaped:
        text = html_lib.escape(text)
    return '<html><body><template id="HH-Lux-InitialState">%s</template></body></html>' % text


def counts(total=31, unread=24):
    return {'counts': {'totalCandidatesByEmployerState': {'RESPONSE': total, 'PHONE_INTERVIEW': 1},
                       'totalUnreadCandidatesByEmployerState': {'RESPONSE': unread}}}


def response(topic, at, phone='+7 (701) 555-00-%02d', invitation=False):
    candidate = {'topicId': topic, 'vacancyId': int(OPERATOR), 'creationTime': at, 'employerState': 'RESPONSE',
                 'initialTopicType': 'INVITATION_BY_EMPLOYER' if invitation else 'RESPONSE_BY_APPLICANT'}
    resume = {'topicId': topic, '_attributes': {'hash': '%038x' % topic},
              'firstName': [{'string': 'Имя%d' % topic}], 'lastName': [{'string': 'Фамилия%d' % topic}],
              'middleName': [{'string': ''}], 'area': [{'string': 160, 'trl': 'Алматы'}],
              'phone': [{'type': 'cell', 'formatted': phone % (topic % 100), 'raw': '7701555', 'verified': True}]}
    return candidate, resume


def page(*rows):
    return {'contactCenterCandidates': {'candidates': [c for c, _r in rows],
                                        'rawEnrichingData': {'resumes': {'resumes': [r for _c, r in rows]}}}}


class FakeResponse:
    def __init__(self, status=200, body=None, url='https://almaty.hh.kz/x', headers=None, text=None, set_cookies=None):
        self.status_code = status
        self._body = body
        self.url = url
        self.headers = headers or {}
        self.text = text if text is not None else (body if isinstance(body, str) else json.dumps(body or {}))
        self.set_cookies = set_cookies or {}

    def json(self):
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError('not json')


class FakeHttp:
    """requests.Session двойник для кабинета hh: ответы по пути, журнал запросов, обновление cookie как Set-Cookie."""

    def __init__(self, routes):
        import requests
        self.routes = routes
        self.cookies = requests.cookies.RequestsCookieJar()
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        path = url.split('hh.kz', 1)[1]
        self.calls.append((path, dict(params or {})))
        handler = self.routes.get(path)
        result = handler(dict(params or {})) if callable(handler) else handler
        if result is None:
            return FakeResponse(404, {'error': 'нет'})
        if not isinstance(result, FakeResponse):
            result = FakeResponse(200, result, url=url)
        for name, value in result.set_cookies.items():
            self.cookies.set(name, value, domain='.hh.kz', path='/')
        return result


class FakePortal:
    """Портал — настоящая ручка Blueprint с поддельным сервисом: подпись проверяется по-настоящему."""

    def __init__(self, test):
        self.test = test
        self.payloads = []
        self.down = False
        self.off = [MARKETER]
        self.since = '2026-11-23T12:08:00+05:00'
        self.since_by_vacancy = {}
        self.next_poll = 180
        service = SimpleNamespace(hh_agent_push=self._push)
        app = Flask('hh-bridge-test')
        app.register_blueprint(hiring_routes.build_hiring_hh_agent_blueprint(
            service=service, build_cors_preflight_response=lambda: ('', 204)))
        self.client = app.test_client()

    def _push(self, payload):
        self.payloads.append(payload)
        return {'results': [{'topic_id': item['topic_id'], 'outcome': 'created'} for item in payload['items']],
                'settings': {'since': self.since, 'off': list(self.off), 'since_by_vacancy': dict(self.since_by_vacancy)},
                'next_poll_sec': self.next_poll}

    def post(self, url, data=None, timeout=None, headers=None):
        if self.down:
            raise hh_bridge.requests.exceptions.ConnectTimeout('портал лежит')
        path = url.split('portal.test', 1)[1]
        answer = self.client.post(path, data=data, headers=headers, content_type='application/json')
        return SimpleNamespace(status_code=answer.status_code, text=answer.get_data(as_text=True),
                               json=lambda: answer.get_json())


class HhBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.session_file = os.path.join(self.tmp.name, 'session.json')
        with open(self.session_file, 'w', encoding='utf-8') as handle:
            json.dump({'user_agent': 'Mozilla/5.0 (Windows NT 10.0) Chrome/151', 'cookies': [
                {'name': 'hhtoken', 'value': 'secret', 'domain': '.hh.kz', 'path': '/', 'expires': 2000000000,
                 'httpOnly': True, 'secure': True},
                {'name': 'crypted_id', 'value': 'abc', 'domain': '.hh.kz', 'path': '/', 'expires': -1}]}, handle)
        self.heartbeat = os.path.join(self.tmp.name, 'heartbeat')
        self.config = {'portal': 'https://portal.test', 'key_file': '', 'private_key': PRIVATE_B64, 'proxy': '',
                       'ca_bundle': '', 'session_file': self.session_file,
                       'state_file': os.path.join(self.tmp.name, 'state.json'), 'host': 'https://almaty.hh.kz',
                       'site_proxy': '', 'interval': 120.0, 'heartbeat_file': self.heartbeat}
        self.fingerprint = counts()
        self.rows = [response(5000000003, '2026-11-23T10:20:00.5+03:00'),       # 12:20 Алматы — после отсечки 12:08
                     response(5000000002, '2026-11-23T10:09:00+03:00'),         # 12:09 — после
                     response(5000000001, '2026-11-23T10:07:59+03:00'),         # 12:07:59 — до отсечки
                     response(5000000004, '2026-11-23T10:30:00+03:00', invitation=True)]
        # Папки выдачи: «Отклики» (без collection) и этапы, куда рекрутер в hh переводит отклики.
        self.folders = {None: self.rows, 'phone_interview': []}
        self.http = FakeHttp({
            '/employer/vacancies': lambda params: FakeResponse(200, vacancies_page(), url='https://almaty.hh.kz/employer/vacancies'),
            '/shards/employer/candidates/counts': lambda params: self.fingerprint,
            '/shards/employer/vacancyresponses/candidates_list': lambda params: (
                page(*self.folders.get(params.get('collection'), [])) if params['page'] == 0 else page()),
        })
        os.environ['CDR_AGENT_KEYS'] = PUBLIC_B64
        self.addCleanup(os.environ.pop, 'CDR_AGENT_KEYS', None)
        self.portal = FakePortal(self)

    def bridge(self):
        site = hh_bridge.Site(self.config, http=self.http, sleep=lambda _s: None)
        return hh_bridge.HhBridge(self.config, site=site, portal_session=self.portal)

    def calls(self, path):
        return [params for called, params in self.http.calls if called == path]

    # ── разбор ───────────────────────────────────────────────────────────────

    def test_parsing(self):
        for escaped in (True, False):
            account, vacancies = hh_bridge.catalog_from_state(hh_bridge.initial_state(vacancies_page(escaped=escaped)))
            self.assertEqual(account, {'manager': 'Руслан Тестов', 'employer': 'Ресурс Плюс KZ', 'employer_id': '10106941'})
            self.assertEqual([(v['id'], v['area'], v['responses']) for v in vacancies],
                             [(OPERATOR, 'Алматы', 152), (MARKETER, 'Алматы', 250)])   # архивная — мимо
        with self.assertRaises(hh_bridge.SiteTrouble) as caught:
            hh_bridge.catalog_from_state(hh_bridge.initial_state(vacancies_page(user_type='anonymous')))
        self.assertEqual(caught.exception.kind, 'login_required')
        self.assertIsNone(hh_bridge.initial_state('<html>без состояния</html>'))
        items = hh_bridge.items_from_page(page(*self.rows), {'id': OPERATOR, 'name': 'Оператор call-центра'})
        self.assertEqual([i['topic_id'] for i in items], ['5000000003', '5000000002', '5000000001'])   # без приглашения
        self.assertEqual(items[0], {'topic_id': '5000000003', 'vacancy_id': OPERATOR, 'vacancy_name': 'Оператор call-центра',
                                    'responded_at': '2026-11-23T10:20:00.5+03:00', 'last_name': 'Фамилия5000000003',
                                    'first_name': 'Имя5000000003', 'middle_name': '', 'phone': '+7 (701) 555-00-03',
                                    'city': 'Алматы', 'resume_hash': '%038x' % 5000000003})
        # Отклик без резюме в выдаче (скрытое, удалённое) — уходит без телефона: портал отметит «без телефона».
        lonely = hh_bridge.items_from_page({'contactCenterCandidates': {'candidates': [self.rows[0][0]]}}, {'id': OPERATOR})
        self.assertIsNone(lonely[0]['phone'])
        with self.assertRaises(hh_bridge.SiteTrouble):
            hh_bridge.items_from_page({}, {'id': OPERATOR})
        self.assertNotEqual(hh_bridge.counts_fingerprint(counts(31, 24)), hh_bridge.counts_fingerprint(counts(31, 23)))
        self.assertEqual(hh_bridge.parse_moment('2026-11-23T10:07:59+03:00'), hh_bridge.parse_moment('2026-11-23T12:07:59'))

    def test_site_trouble_kinds(self):
        site = hh_bridge.Site(self.config, http=self.http, sleep=lambda _s: None)
        self.http.routes['/employer/vacancies'] = FakeResponse(200, '<html/>', url='https://almaty.hh.kz/account/login?backurl=x')
        with self.assertRaises(hh_bridge.SiteTrouble) as caught:
            site.catalog()
        self.assertEqual(caught.exception.kind, 'login_required')
        self.http.routes['/shards/employer/candidates/counts'] = FakeResponse(403, 'Checking your browser… DDoS-Guard',
                                                                             headers={'Server': 'ddos-guard'})
        with self.assertRaises(hh_bridge.SiteTrouble) as caught:
            site.counts(OPERATOR)
        self.assertEqual(caught.exception.kind, 'blocked')
        self.http.routes['/shards/employer/candidates/counts'] = FakeResponse(502, 'bad gateway')
        with self.assertRaises(hh_bridge.SiteTrouble) as caught:
            site.counts(OPERATOR)
        self.assertEqual(caught.exception.kind, 'error')
        # Запросы — с браузером, в котором владелец вошёл, и его cookie.
        self.assertEqual(site.user_agent, 'Mozilla/5.0 (Windows NT 10.0) Chrome/151')
        self.assertEqual(site.http.cookies.get('hhtoken', domain='.hh.kz'), 'secret')

    # ── такт ────────────────────────────────────────────────────────────────

    def test_cycle_brings_only_fresh_responses_once(self):
        bridge = self.bridge()
        wait = bridge.cycle()
        self.assertEqual(wait, 180.0)                       # пауза портала — как есть: случайную выбирает портал
        # Первый обмен — за отсечкой, без чтения hh; второй — каталог и отклики после 12:08 Алматы.
        first, second = self.portal.payloads
        self.assertEqual((first['items'], first['vacancies'], first['session']), ([], None, 'ok'))
        self.assertIsNone(second['pause_sec'])                                # кабинет читается — своего отступа нет
        self.assertEqual([i['topic_id'] for i in second['items']], ['5000000003', '5000000002'])
        self.assertEqual([v['id'] for v in second['vacancies']], [OPERATOR, MARKETER])
        self.assertEqual(second['account']['employer'], 'Ресурс Плюс KZ')
        # Маркетолог выключен порталом — ни счётчиков, ни выдачи.
        self.assertEqual([p['vacancyId'] for p in self.calls('/shards/employer/candidates/counts')], [OPERATOR])
        # Первый взгляд: «Отклики» и непустые этапы (там могли оказаться отклики после отсечки).
        self.assertEqual(self.calls('/shards/employer/vacancyresponses/candidates_list'),
                         [{'vacancyId': OPERATOR, 'order': 'DATE', 'page': 0},
                          {'vacancyId': OPERATOR, 'order': 'DATE', 'page': 0, 'collection': 'phone_interview'}])
        self.assertTrue(os.path.exists(self.heartbeat))
        # Счётчики не сдвинулись — выдачу не читаем, порталу — только «я на связи».
        bridge.cycle()
        self.assertEqual(len(self.calls('/shards/employer/vacancyresponses/candidates_list')), 2)
        self.assertEqual(self.portal.payloads[-1]['items'], [])
        self.assertIsNone(self.portal.payloads[-1]['vacancies'])             # каталог свежий — не повторяем
        # Пришёл новый отклик — уходит только он.
        self.rows.insert(0, response(5000000005, '2026-11-23T11:00:00+03:00'))
        self.fingerprint = counts(32, 25)
        bridge.cycle()
        self.assertEqual([i['topic_id'] for i in self.portal.payloads[-1]['items']], ['5000000005'])
        self.assertEqual(self.calls('/shards/employer/vacancyresponses/candidates_list')[-1].get('collection'), None)
        # Рекрутер в hh успел перевести свежий отклик в «Телефонное интервью» до такта моста: в «Откликах» его нет, но
        # счётчик этапа вырос — мост смотрит и эту папку.
        self.folders['phone_interview'] = [response(5000000009, '2026-11-23T11:10:00+03:00')]
        self.fingerprint = {'counts': {'totalCandidatesByEmployerState': {'RESPONSE': 32, 'PHONE_INTERVIEW': 2},
                                       'totalUnreadCandidatesByEmployerState': {'RESPONSE': 25}}}
        bridge.cycle()
        self.assertEqual([i['topic_id'] for i in self.portal.payloads[-1]['items']], ['5000000009'])
        self.assertEqual(self.calls('/shards/employer/vacancyresponses/candidates_list')[-1].get('collection'),
                         'phone_interview')
        # Ночью портал называет паузу до двух часов — мост выдерживает её как есть.
        self.portal.next_poll = 7200
        self.assertEqual(bridge.cycle(), 7200.0)
        # Перезапуск моста: отданное помнится, повторов нет.
        self.fingerprint = counts(32, 24)
        again = self.bridge()
        again.cycle()
        self.assertEqual([p['items'] for p in self.portal.payloads[-2:]], [[], []])

    def test_portal_down_keeps_queue_across_restart(self):
        bridge = self.bridge()
        bridge.cycle()                                                      # отсечка известна, два отклика отданы
        self.rows.insert(0, response(5000000006, '2026-11-23T11:30:00+03:00'))
        self.fingerprint = counts(33, 26)
        self.portal.down = True
        self.assertEqual(bridge.cycle(), 5.0)                               # пауза портала
        self.assertEqual([i['topic_id'] for i in bridge.pending], ['5000000006'])
        # Очередь — на диске: новый процесс отдаёт её первым же обменом, когда портал вернулся, и выдачу заново не
        # читает (счётчики уже сошлись).
        bridge._save_state()
        self.portal.down = False
        restarted = self.bridge()
        self.assertEqual([i['topic_id'] for i in restarted.pending], ['5000000006'])
        reads = len(self.calls('/shards/employer/vacancyresponses/candidates_list'))
        sent = len(self.portal.payloads)
        restarted.cycle()
        self.assertEqual([[i['topic_id'] for i in p['items']] for p in self.portal.payloads[sent:]], [['5000000006'], []])
        self.assertEqual(restarted.pending, [])
        self.assertEqual(len(self.calls('/shards/employer/vacancyresponses/candidates_list')), reads)
        # Портал лёг, а очередь на диск не попала (обмен не дошёл до записи): отпечатки на диске прежние — новый процесс
        # перечитает выдачу и найдёт отклик заново.
        self.rows.insert(0, response(5000000007, '2026-11-23T11:40:00+03:00'))
        self.fingerprint = counts(34, 27)
        self.portal.down = True
        restarted.cycle()
        self.portal.down = False
        fresh = self.bridge()
        self.assertEqual(fresh.pending, [])
        sent = len(self.portal.payloads)
        fresh.cycle()
        self.assertEqual([[i['topic_id'] for i in p['items']] for p in self.portal.payloads[sent:]], [[], ['5000000007']])

    def test_vacancy_tracked_later_brings_only_responses_after_it(self):
        """Вакансию включили в «Настройках» позже общей отсечки — мост отдаёт порталу только её отклики после минуты
        включения: 12:20 — да, 12:09 (после общей отсечки 12:08, но до включения в 12:15) — нет."""
        self.portal.since_by_vacancy = {OPERATOR: '2026-11-23T12:15:00+05:00'}
        bridge = self.bridge()
        bridge.cycle()
        self.assertEqual([i['topic_id'] for p in self.portal.payloads for i in p['items']], ['5000000003'])
        # Включили ещё раз (момент сдвинулся) — уже отданное не повторяется.
        self.portal.since_by_vacancy = {OPERATOR: '2026-11-23T12:30:00+05:00'}
        self.fingerprint = counts(32, 25)
        bridge.cycle()
        self.assertEqual(self.portal.payloads[-1]['items'], [])

    def test_portal_outage_does_not_speed_up_hh(self):
        """Портал лёг — мост стучится только к нему, hh не читает; ответил — очередь дослана, и до следующего чтения hh
        мост выжидает названную паузу: простой портала не учащает опрос hh, ночью тоже."""
        bridge = self.bridge()
        bridge.cycle()
        self.rows.insert(0, response(5000000006, '2026-11-23T11:30:00+03:00'))
        self.fingerprint = counts(33, 26)
        self.portal.down = True
        self.assertEqual(bridge.cycle(), 5.0)                               # отклик прочитан, доклад не дошёл
        read = len(self.http.calls)
        self.assertEqual([bridge.cycle() for _ in range(3)], [15.0, 30.0, 60.0])
        self.assertEqual(len(self.http.calls), read)                        # в hh — ни запроса
        self.portal.down = False
        self.portal.next_poll = 5400
        self.assertEqual(bridge.cycle(), 5400.0)
        self.assertEqual([i['topic_id'] for i in self.portal.payloads[-1]['items']], ['5000000006'])
        self.assertEqual(len(self.http.calls), read)
        bridge.cycle()                                                      # пауза вышла — снова кабинет
        self.assertGreater(len(self.http.calls), read)

    def test_heartbeat_during_long_pause(self):
        """Ночная пауза — до двух часов, а проверка контейнера ждёт пульс не реже раза в 15 минут: пока такт прошёл как
        надо, пульс обновляется и в паузе; при беде — нет."""
        bridge = self.bridge()
        waits = []
        bridge.stop_event = SimpleNamespace(wait=lambda seconds: waits.append(seconds) or False)
        bridge.healthy = True
        self.assertFalse(bridge.rest(700.0))
        self.assertEqual(waits, [300.0, 300.0, 100.0])
        self.assertTrue(os.path.exists(self.heartbeat))
        os.remove(self.heartbeat)
        bridge.healthy = False
        bridge.rest(700.0)
        self.assertFalse(os.path.exists(self.heartbeat))
        bridge.stop_event = SimpleNamespace(wait=lambda seconds: True)       # остановка посреди паузы — сразу
        self.assertTrue(bridge.rest(7200.0))

    def test_lost_login_is_reported_and_polled_rarely(self):
        bridge = self.bridge()
        bridge.cycle()
        os.remove(self.heartbeat)
        bridge.catalog_at = None
        self.http.routes['/employer/vacancies'] = FakeResponse(200, '<html/>', url='https://almaty.hh.kz/account/login')
        self.assertEqual(bridge.cycle(), 1800.0)                            # вход сам не вернётся — раз в полчаса
        last = self.portal.payloads[-1]
        self.assertEqual((last['session'], last['items'], last['vacancies']), ('login_required', [], None))
        self.assertIn('страницу входа', last['detail'])
        self.assertEqual(last['pause_sec'], 1800.0)                         # портал знает отступ — сторож не тревожит
        self.assertFalse(os.path.exists(self.heartbeat))                    # без входа пульса нет
        self.assertEqual(bridge.cycle(), 1800.0)
        self.http.routes['/employer/vacancies'] = lambda params: FakeResponse(200, vacancies_page(),
                                                                              url='https://almaty.hh.kz/employer/vacancies')
        self.assertEqual(bridge.cycle(), 180.0)
        self.assertEqual((self.portal.payloads[-1]['session'], self.portal.payloads[-1]['pause_sec']), ('ok', None))
        # Защита сайта не пускает — отступить надолго и всё дальше: 15 минут, полчаса, час.
        bridge.catalog_at = None
        self.http.routes['/employer/vacancies'] = FakeResponse(403, 'DDoS-Guard', headers={'Server': 'ddos-guard'})
        self.assertEqual([bridge.cycle() for _ in range(4)], [900.0, 1800.0, 3600.0, 3600.0])
        self.assertEqual(self.portal.payloads[-1]['session'], 'blocked')
        # Ночью отступ не короче ночной паузы портала: сбой hh не учащает опрос.
        self.portal.next_poll = 5400
        self.assertEqual(bridge.cycle(), 5400.0)

    def test_daily_request_cap(self):
        """Предохранитель от ошибки в коде: за сутки (Алматы) не больше DAILY_REQUEST_CAP запросов к hh."""
        now = [1795400000.0]
        site = hh_bridge.Site(self.config, http=self.http, sleep=lambda _s: None, clock=lambda: now[0])
        site.counts(OPERATOR)
        site.requests_today = hh_bridge.DAILY_REQUEST_CAP
        calls = len(self.http.calls)
        with self.assertRaises(hh_bridge.SiteTrouble) as caught:
            site.counts(OPERATOR)
        self.assertIn('суточный предел', str(caught.exception))
        self.assertEqual(len(self.http.calls), calls)                       # запрос не ушёл
        now[0] += 86400                                                     # новые сутки — снова можно
        site.counts(OPERATOR)
        self.assertEqual(site.requests_today, 1)

    def test_refreshed_cookies_are_written_back(self):
        self.http.routes['/shards/employer/candidates/counts'] = lambda params: FakeResponse(
            200, self.fingerprint, set_cookies={'__ddg9_': 'fresh'})
        bridge = self.bridge()
        bridge.cycle()
        with open(self.session_file, encoding='utf-8') as handle:
            saved = json.load(handle)
        names = {cookie['name']: cookie for cookie in saved['cookies']}
        self.assertEqual(names['__ddg9_']['value'], 'fresh')
        self.assertEqual(names['hhtoken']['value'], 'secret')
        self.assertTrue(names['hhtoken']['httpOnly'])
        self.assertEqual(saved['user_agent'], 'Mozilla/5.0 (Windows NT 10.0) Chrome/151')

    def test_unsigned_or_foreign_key_is_refused(self):
        other_private, _other_public, _kid = signing.generate()
        config = dict(self.config, private_key=other_private)
        site = hh_bridge.Site(config, http=self.http, sleep=lambda _s: None)
        stranger = hh_bridge.HhBridge(config, site=site, portal_session=self.portal)
        with self.assertRaises(RuntimeError) as caught:
            stranger.exchange()
        self.assertIn('не принял подпись', str(caught.exception))
        self.assertEqual(self.portal.payloads, [])


if __name__ == '__main__':
    unittest.main()
