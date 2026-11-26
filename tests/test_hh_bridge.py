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


def wrapped(value, kind='string'):
    """Поле страницы резюме, как его отдаёт hh: часть полей — в обёртке {value, type, block, _attributes}."""
    return {'value': value, 'type': kind, 'block': None, '_attributes': None}


def resume_page(resume_hash, letter='Здравствуйте!\r\nХочу работать у вас.'):
    """Страница резюме, открытая из отклика: форма — как у живой страницы 23.11.2026 (контакты в ней есть — мост их
    не берёт)."""
    state = {
        'resume': {
            'hash': resume_hash, 'age': 27, 'title': wrapped('Оператор call-центра'),
            'salary': wrapped({'amount': 250000, 'currency': 'KZT'}, 'salary'), 'gender': wrapped('female'),
            'area': wrapped({'id': 160, 'title': 'Алматы'}, 'area'), 'metro': wrapped({'id': 1, 'title': 'Алатау'}, 'metro'),
            'citizenship': wrapped([{'id': 40, 'title': 'Казахстан'}], 'list'),
            'relocation': wrapped('no_relocation'), 'businessTripReadiness': wrapped('never'), 'travelTime': wrapped('any'),
            'employment': wrapped([{'string': 'full'}, {'string': 'part'}], 'list'),
            'workSchedule': wrapped([{'string': 'shift'}], 'list'), 'workFormats': wrapped([{'string': 'ON_SITE'}], 'list'),
            'totalExperience': wrapped({'years': 5, 'months': 10}, 'experience'),
            'experience': wrapped([{'companyName': 'ТОО Ромашка', 'companyAreaTitle': 'Алматы', 'position': 'Оператор',
                                    'startDate': '2020-01-01', 'endDate': None, 'interval': {'years': 5, 'months': 10},
                                    'description': 'Входящие звонки\r\n\r\n\r\n\r\nЧаты   '}], 'list'),
            'educationLevel': wrapped('higher'),
            'primaryEducation': wrapped([{'name': 'КазНУ', 'organization': 'Филологический', 'result': 'Филолог',
                                          'year': 2019}], 'list'),
            'additionalEducation': wrapped([{'name': 'Курсы продаж', 'organization': 'Школа', 'result': None,
                                             'year': 2021}], 'list'),
            'language': wrapped([{'title': 'Русский', 'degree': 'l1', 'id': 1}, {'title': 'Английский', 'degree': 'b1'}], 'list'),
            'keySkills': wrapped([{'string': 'Продажи'}, {'string': 'CRM'}], 'list'),
            'skills': wrapped('Ответственная, люблю общаться.'),
            'driverLicenseTypes': wrapped([{'string': 'B'}], 'list'), 'hasMedicalBook': wrapped(True, 'bool'),
            'email': wrapped('candidate@example.com'), 'phone': wrapped([{'formatted': '+7 701 000 00 00'}], 'list'),
            'birthday': wrapped('1998-01-01'), 'addressCoordinates': wrapped([{'lat': 43.2, 'lng': 76.9}], 'list'),
        },
        'employerNegotiations': {'responseLetters': {resume_hash: letter} if letter else {}},
    }
    return '<html><body><template id="HH-Lux-InitialState">%s</template></body></html>' % html_lib.escape(
        json.dumps(state, ensure_ascii=False))


class FakeResponse:
    def __init__(self, status=200, body=None, url='https://almaty.hh.kz/x', headers=None, text=None, set_cookies=None):
        self.status_code = status
        self._body = body
        self.url = url
        self.headers = headers or {}
        if isinstance(body, bytes):                     # картинка (фото кандидата)
            self.text = text if text is not None else body.decode('latin-1')
            self.content = body
        else:
            self.text = text if text is not None else (body if isinstance(body, str) else json.dumps(body or {}))
            self.content = self.text.encode('utf-8')
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
        if handler is None:                     # маршрут-префикс: '/resume/*' — страница любого резюме
            handler = next((value for key, value in self.routes.items()
                            if key.endswith('*') and path.startswith(key[:-1])), None)
            if callable(handler):
                handler = (lambda inner: lambda params: inner(path, params))(handler)
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
        # Задания поиска по базе резюме (hh/1.4.0): обработчик задаёт тест; missing_tasks — портал старой версии.
        self.tasks_handler = None
        self.missing_tasks = False
        service = SimpleNamespace(hh_agent_push=self._push, hh_agent_tasks=self._tasks)
        app = Flask('hh-bridge-test')
        app.register_blueprint(hiring_routes.build_hiring_hh_agent_blueprint(
            service=service, build_cors_preflight_response=lambda: ('', 204)))
        self.client = app.test_client()

    def _push(self, payload):
        self.payloads.append(payload)
        return {'results': [{'topic_id': item['topic_id'], 'outcome': 'created'} for item in payload['items']],
                'settings': {'since': self.since, 'off': list(self.off), 'since_by_vacancy': dict(self.since_by_vacancy)},
                'next_poll_sec': self.next_poll}

    def _tasks(self, payload):
        if self.tasks_handler is None:
            raise AttributeError('портал без заданий')
        return self.tasks_handler(payload)

    def post(self, url, data=None, timeout=None, headers=None):
        if self.down:
            raise hh_bridge.requests.exceptions.ConnectTimeout('портал лежит')
        path = url.split('portal.test', 1)[1]
        if self.missing_tasks and path.endswith('/tasks'):
            return SimpleNamespace(status_code=404, text='Not Found', json=lambda: {})
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
            '/resume/*': lambda path, params: FakeResponse(200, resume_page(path.rsplit('/', 1)[1]),
                                                           url='https://almaty.hh.kz' + path),
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

    def test_new_response_brings_resume_and_letter_without_contacts(self):
        """Владелец 23.11.2026 выбрал «Полное резюме и письмо»: мост открывает каждый новый отклик один раз — страницу
        резюме из отклика (vacancyId и t) — и отдаёт резюме без контактов и письмо с абзацами."""
        bridge = self.bridge()
        bridge.cycle()
        items = self.portal.payloads[1]['items']
        first = items[0]
        resume = first['resume']
        self.assertEqual(resume['title'], 'Оператор call-центра')
        self.assertEqual(resume['salary'], {'amount': 250000, 'currency': 'KZT'})
        self.assertEqual((resume['age'], resume['gender'], resume['area'], resume['metro'], resume['citizenship']),
                         (27, 'female', 'Алматы', 'Алатау', ['Казахстан']))
        self.assertEqual((resume['relocation'], resume['business_trips'], resume['travel_time']), ('no_relocation', 'never', 'any'))
        self.assertEqual((resume['employment'], resume['schedule'], resume['formats']), (['full', 'part'], ['shift'], ['ON_SITE']))
        self.assertEqual(resume['experience_total'], {'years': 5, 'months': 10})
        self.assertEqual(resume['experience'], [{'company': 'ТОО Ромашка', 'area': 'Алматы', 'position': 'Оператор',
                                                 'start': '2020-01-01', 'end': None, 'years': 5, 'months': 10,
                                                 'description': 'Входящие звонки\n\nЧаты'}])
        self.assertEqual((resume['education_level'], resume['education'][0]['name'], resume['courses'][0]['name']),
                         ('higher', 'КазНУ', 'Курсы продаж'))
        self.assertEqual(resume['languages'], [{'title': 'Русский', 'degree': 'l1'}, {'title': 'Английский', 'degree': 'b1'}])
        self.assertEqual((resume['key_skills'], resume['about']), (['Продажи', 'CRM'], 'Ответственная, люблю общаться.'))
        self.assertEqual((resume['driver_license'], resume['medical_book'], resume['has_vehicle']), (['B'], True, None))
        self.assertEqual(first['letter'], 'Здравствуйте!\nХочу работать у вас.')
        text = json.dumps(items, ensure_ascii=False)
        for secret in ('candidate@example.com', '+7 701 000 00 00', '1998-01-01', '43.2'):
            self.assertNotIn(secret, text)                                   # контактов резюме в докладе нет
        # Страница — из отклика (vacancyId и t), по одной на новый отклик.
        resume_calls = [(path, params) for path, params in self.http.calls if path.startswith('/resume/')]
        self.assertEqual(resume_calls, [('/resume/%038x' % 5000000003, {'vacancyId': OPERATOR, 't': '5000000003'}),
                                        ('/resume/%038x' % 5000000002, {'vacancyId': OPERATOR, 't': '5000000002'})])
        # Следующий такт с теми же откликами страниц резюме больше не открывает.
        self.fingerprint = counts(31, 23)
        bridge.cycle()
        self.assertEqual(len([1 for path, _params in self.http.calls if path.startswith('/resume/')]), 2)

    def test_resume_page_failure_still_sends_the_response(self):
        """Страница резюме не открылась (hh ответил 500) — отклик всё равно уходит: карточка важнее. Вход слетел на
        странице резюме — такт прерывается, как на любом запросе, и отклик находится снова в следующий."""
        self.http.routes['/resume/*'] = FakeResponse(500, 'сбой')
        bridge = self.bridge()
        bridge.cycle()
        items = self.portal.payloads[1]['items']
        self.assertEqual([i['topic_id'] for i in items], ['5000000003', '5000000002'])
        self.assertTrue(all('resume' not in i and 'letter' not in i for i in items))
        # hh переделал страницу резюме (поле другого вида) — разбор падает, но отклик всё равно уходит.
        broken = html_lib.escape(json.dumps({'resume': {'experience': {'value': 'не список', 'type': 'list'},
                                                        'salary': {'value': [1, 2], 'type': 'salary'},
                                                        'citizenship': {'value': 'строка', 'type': 'list'}}}))
        self.http.routes['/resume/*'] = FakeResponse(200, '<template id="HH-Lux-InitialState">%s</template>' % broken,
                                                     url='https://almaty.hh.kz/resume/x')
        self.rows.insert(0, response(5000000004 + 4, '2026-11-23T10:50:00+03:00'))
        self.fingerprint = counts(31, 25)
        bridge.cycle()
        self.assertEqual([i['topic_id'] for i in self.portal.payloads[-1]['items']], ['5000000008'])
        self.http.routes['/resume/*'] = FakeResponse(200, '<html/>', url='https://almaty.hh.kz/account/login')
        self.rows.insert(0, response(5000000005, '2026-11-23T11:00:00+03:00'))
        self.fingerprint = counts(32, 25)
        bridge.cycle()
        self.assertEqual((self.portal.payloads[-1]['session'], self.portal.payloads[-1]['items']), ('login_required', []))
        self.http.routes['/resume/*'] = lambda path, params: FakeResponse(200, resume_page(path.rsplit('/', 1)[1], letter=None),
                                                                         url='https://almaty.hh.kz' + path)
        bridge.cycle()
        last = self.portal.payloads[-1]
        self.assertEqual(([i['topic_id'] for i in last['items']], last['session']), (['5000000005'], 'ok'))
        self.assertEqual((last['items'][0]['resume']['title'], last['items'][0]['letter']), ('Оператор call-центра', None))

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


# ─── Поиск по базе резюме (hh/1.4.0, владелец 25.11.2026) ─────────────────────

def search_item(n, *, updated=1795550400000, status='DENIED_NO_FA', topic=None, photo=False):
    """Резюме в выдаче поиска по базе — форма как у живой страницы 25.11.2026. У закрытых имени и контактов нет; у
    доступных без списания hh отдаёт и ФИО, и телефон (проба 26.11: 3 из 20). Фото — адреса относительные, с подписью
    t/h (открываются только под сессией кабинета)."""
    free = status == 'AVAILABLE_WITHOUT_SPEND'
    photos = [{'id': 7000 + n, 'state': 'processed', 'title': None,
               'avatar': '/photo/%d.jpeg?t=1795600000&h=avAtar-%d' % (7000 + n, n),
               'preview': '/photo/%d.jpeg?t=1795600000&h=pRev_%d' % (7100 + n, n),
               'big': '/photo/%d.jpeg?t=1795600000&h=b%d' % (7200 + n, n),
               'large': '/photo/%d.jpeg?t=1795600000&h=l%d' % (7300 + n, n)}] if photo else [None]
    return {
        'photoUrls': photos, 'photoAccess': 'visible' if free else 'can_open',
        'firstName': [{'string': 'Айгерим'}] if free else None, 'lastName': [{'string': 'Стендова'}] if free else None,
        'middleName': [None] if free else None,
        'phone': [{'city': '707', 'country': '7', 'formatted': '123-45-67', 'raw': None, 'number': None}] if free else None,
        '_attributes': {'hash': '%038x' % (0xc0000 + n), 'id': str(900000000 + n), 'updated': updated,
                        'permission': 'view_without_contacts'},
        'title': [{'string': 'Оператор call-центра'}], 'age': [{'string': 20 + n}], 'gender': [{'string': 'female'}],
        'area': [{'string': 160, 'trl': 'Алматы'}], 'salary': [{'amount': 200000, 'currency': 'KZT'}],
        'totalExperience': [{'string': 38}],
        'shortExperience': [{'companyName': 'ТОО Ромашка', 'position': 'Продавец-консультант', 'startDate': '2023-09-01',
                             'endDate': '2026-10-01', 'companyId': 38931}],
        'keySkills': [{'string': 'Телефонные переговоры'}, {'string': 'Навыки продаж'}],
        'jobSearchStatus': [{'jobSearchStatus': {'name': 'active_search', 'lastChangeTime': '2026-11-05T14:09:30Z'}}],
        'lastActivityTime': '2026-11-25T09:30:00.500+03:00', 'isOnline': True,
        'resumeContactsOpening': {'throughCiv': True, 'viewStatus': status},
        'lastTopic': [topic] if topic else [], 'phonePreview': [{'city': '707', 'country': '7'}],
    }


def search_page(items, total=6472, user_type='employer'):
    state = {'userType': user_type, 'resumeSearchResult': {'resumes': items, 'stats': {'totalResults': total}}}
    return '<html><body><template id="HH-Lux-InitialState">%s</template></body></html>' % html_lib.escape(
        json.dumps(state, ensure_ascii=False))


def base_resume_page(resume_hash, opened=False):
    """Страница резюме из базы: без открытых контактов имя, телефон и почта пустые (value: null), как на hh."""
    state = {'resume': {
        'hash': resume_hash, 'title': wrapped('Оператор call-центра'), 'age': 26,
        'language': wrapped([{'title': 'Казахский', 'degree': 'b2'}], 'list'),
        'openedContacts': opened, 'permission': 'view' if opened else 'view_without_contacts',
        'resumeContactsOpening': {'canOpenContactsThroughCIV': not opened,
                                  'viewStatus': 'OPENED' if opened else 'DENIED_NO_FA'},
        'firstName': wrapped('Анна' if opened else None, 'field'), 'lastName': wrapped('Иванова' if opened else None, 'field'),
        'middleName': wrapped(None, 'field'),
        # Форма — как у живой страницы 26.11: в formatted только местная часть, код страны и города — рядом.
        'phone': wrapped([{'city': '707', 'raw': None, 'type': 'cell', 'number': None, 'comment': None, 'country': '7',
                           'verified': True, 'formatted': '123-45-67', 'needVerification': None, 'canVerify': False}]
                         if opened else None, 'field'),
        'email': wrapped('anna@example.kz' if opened else None, 'field'),
        'birthday': wrapped('1999-12-17', 'field'),
    }}
    return '<html><body><template id="HH-Lux-InitialState">%s</template></body></html>' % html_lib.escape(
        json.dumps(state, ensure_ascii=False))


class HhBaseBridgeTests(unittest.TestCase):
    """Задания поиска по базе: поиск страницами с паузой человека, резюме, контакты только после «Взять в работу» и
    только уже открытые; беда со входом — hh не трогается; портал без заданий — мост их не долбит."""

    calls = HhBridgeTests.calls

    def setUp(self):
        HhBridgeTests.setUp(self)
        self.config['tasks'] = True
        self.pages = {0: [search_item(n) for n in range(20)], 1: [search_item(20 + n) for n in range(20)],
                      2: [search_item(40 + n, updated=1700000000000) for n in range(20)]}
        self.opened = set()
        self.http.routes['/search/resume'] = lambda params: FakeResponse(
            200, search_page(self.pages.get(int(params.get('page', 0)), [])))
        self.http.routes['/resume/*'] = lambda path, params: FakeResponse(
            200, base_resume_page(path.rsplit('/', 1)[1], opened=path.rsplit('/', 1)[1] in self.opened))
        self.portal.tasks = []
        self.portal.task_payloads = []
        self.portal.tasks_handler = self._tasks

    def _tasks(self, payload):
        self.portal.task_payloads.append(payload)
        tasks = self.portal.tasks if payload.get('take') is not False else []
        self.portal.tasks = []
        return {'accepted': [item.get('id') for item in payload.get('done') or []], 'tasks': tasks, 'wait_sec': 20}

    def bridge(self):
        site = hh_bridge.Site(self.config, http=self.http, sleep=lambda _s: None)
        return hh_bridge.HhBridge(self.config, site=site, portal_session=self.portal)

    def test_parsing_search_and_resume_pages(self):
        found = hh_bridge.found_from_search(search_item(1, status='AVAILABLE_WITHOUT_SPEND', topic={
            'vacancyId': 138923446, 'topicId': 5000000777, 'initialTopicType': 'INVITATION_BY_EMPLOYER',
            'creationTime': '2026-11-25T09:40:00.100+03:00'}))
        self.assertEqual(found, {
            'hash': '%038x' % 0xc0001, 'resume_id': '900000001', 'title': 'Оператор call-центра', 'age': 21,
            'gender': 'female', 'area': 'Алматы', 'salary': {'amount': 200000, 'currency': 'KZT'}, 'experience_months': 38,
            'experience': [{'company': 'ТОО Ромашка', 'position': 'Продавец-консультант', 'start': '2023-09-01', 'end': '2026-10-01'}],
            'skills': ['Телефонные переговоры', 'Навыки продаж'], 'job_status': 'active_search', 'online': True,
            'last_activity_at': '2026-11-25T09:30:00.500+03:00', 'updated_at': '2026-11-24T20:00:00+00:00', 'access': 'free',
            'topic': {'vacancy_id': '138923446', 'topic_id': '5000000777', 'type': 'INVITATION_BY_EMPLOYER',
                      'at': '2026-11-25T09:40:00.100+03:00'},
            # Имя — раз hh его показал (контакты без списания); телефона в краткой карточке для портала нет.
            'name': {'last_name': 'Стендова', 'first_name': 'Айгерим', 'middle_name': None},
            'photo_path': None})                                     # фото нет; путь — только мосту, порталу не уходит
        self.assertEqual(hh_bridge.found_from_search(search_item(5, photo=True))['photo_path'],
                         '/photo/7105.jpeg?t=1795600000&h=pRev_5')
        self.assertIsNone(hh_bridge.photo_path([{'preview': 'https://evil.example/photo/1.jpeg', 'avatar': '/photo/../x'}]))
        self.assertIsNone(hh_bridge.found_from_search(search_item(2))['name'])       # закрытые — имени нет
        self.assertIsNone(hh_bridge.found_from_search({'_attributes': {'hash': 'чужой'}}))
        items, total = hh_bridge.search_from_state(hh_bridge.initial_state(search_page([search_item(2), {'x': 1}])))
        self.assertEqual((len(items), total, items[0]['access']), (1, 6472, 'closed'))
        with self.assertRaises(hh_bridge.SiteTrouble) as caught:
            hh_bridge.search_from_state(hh_bridge.initial_state(search_page([], user_type='anonymous')))
        self.assertEqual(caught.exception.kind, 'login_required')
        closed = hh_bridge.initial_state(base_resume_page('d' * 38))
        self.assertIsNone(hh_bridge.contacts_from_state(closed))
        self.assertEqual(hh_bridge.access_from_state(closed), 'closed')
        opened = hh_bridge.initial_state(base_resume_page('d' * 38, opened=True))
        self.assertEqual(hh_bridge.contacts_from_state(opened), {'last_name': 'Иванова', 'first_name': 'Анна',
                                                                 'middle_name': None, 'phones': ['+77071234567'],
                                                                 'email': 'anna@example.kz'})
        self.assertEqual(hh_bridge.access_from_state(opened), 'opened')

    def test_phone_is_assembled_from_country_city_and_local_part(self):
        """Живой случай 26.11: кандидат открыт, а в карточку шло «нет казахстанского телефона» — мост отдавал только
        местные 7 цифр. Полный номер в formatted (выдача откликов) — как есть; мусор — None."""
        phone = hh_bridge.phone_text
        self.assertEqual(phone({'country': '7', 'city': '707', 'formatted': '123-45-67', 'number': None}), '+77071234567')
        self.assertEqual(phone({'country': '7', 'city': '701', 'number': '1234567', 'formatted': None}), '+77011234567')
        self.assertEqual(phone({'formatted': '+7 (701) 555-00-01', 'raw': '7701555'}), '+7 (701) 555-00-01')
        self.assertEqual(phone({'raw': '77015550001'}), '77015550001')
        for bad in ({'country': '7', 'city': None, 'formatted': None}, {'formatted': '12-34'}, None, 'строка',
                    {'country': True, 'city': '707', 'formatted': '123'}):
            self.assertIsNone(phone(bad), bad)
        state = {'resume': {'firstName': [{'string': 'Анна'}], 'lastName': {'value': [{'string': 'Иванова'}], 'type': 'list'},
                            'phone': {'value': [{'country': '7', 'city': '707', 'formatted': '123-45-67'},
                                                {'country': '7', 'city': '707', 'formatted': '123 45 67'}], 'type': 'list'}}}
        contacts = hh_bridge.contacts_from_state(state)
        self.assertEqual((contacts['first_name'], contacts['last_name'], contacts['phones']), ('Анна', 'Иванова', ['+77071234567']))
        rules_phone = __import__('hiring.rules', fromlist=['normalize_phone']).normalize_phone
        self.assertEqual(rules_phone(contacts['phones'][0]), '+77071234567')

    def test_tasks_search_resume_and_contacts(self):
        bridge = self.bridge()
        taken, viewed = '%038x' % 0xc0001, '%038x' % 0xc0002
        self.opened.add(taken)
        self.portal.tasks = [
            {'id': '1', 'kind': 'search', 'vacancy_id': OPERATOR, 'pages': 9,
             'params': {'text': 'оператор', 'area': '160', 'search_period': '3', 'order_by': 'publication_time',
                        'hhtmFrom': 'чужое', 'page': 7}, 'stop_before': '2026-11-24T00:00:00+05:00'},
            {'id': '2', 'kind': 'resume', 'hash': taken, 'reason': 'contacts'},
            {'id': '3', 'kind': 'resume', 'hash': viewed, 'reason': 'view'},
            {'id': '4', 'kind': 'resume', 'hash': '../../account', 'reason': 'view'},
        ]
        self.assertEqual(bridge.tasks_round(0), 4)
        searches = self.calls('/search/resume')
        # Третья страница — старше прошлого поиска: дальше не листаем; чужие параметры не уходят на hh.
        self.assertEqual([params.get('page') for params in searches], [None, 1, 2])
        self.assertEqual(searches[0], {'text': 'оператор', 'area': '160', 'search_period': '3', 'order_by': 'publication_time'})
        done = {item['id']: item for payload in self.portal.task_payloads for item in payload['done']}
        self.assertEqual((done['1']['ok'], done['1']['total'], done['1']['pages'], len(done['1']['items'])), (True, 6472, 3, 60))
        self.assertEqual(done['2']['contacts']['phones'], ['+77071234567'])
        self.assertEqual((done['2']['access'], done['2']['reason']), ('opened', 'contacts'))
        self.assertEqual(done['2']['name'], {'last_name': 'Иванова', 'first_name': 'Анна', 'middle_name': None})
        self.assertNotIn('contacts', done['3'])                      # при просмотре контакты не берутся
        self.assertNotIn('name', done['3'])                          # контакты закрыты — hh имени не показал
        self.assertEqual(done['3']['resume']['languages'], [{'title': 'Казахский', 'degree': 'b2'}])
        self.assertEqual((done['4']['ok'], done['4']['error']), (False, 'неверный хеш резюме'))
        resumes = [path for path, _params in self.http.calls if path.startswith('/resume/')]
        self.assertEqual(resumes, ['/resume/' + taken, '/resume/' + viewed])
        self.assertEqual(self.portal.task_payloads[0]['done'], [])       # первым заходом — просто ждать заданий
        self.assertEqual(self.portal.task_payloads[0]['wait'], 0)

    def test_search_brings_photos_once_and_only_to_a_portal_that_takes_them(self):
        """Фото кандидата (владелец 26.11.2026: «чтобы подтянулась и аватарка»): превью из выдачи мост скачивает сразу за
        страницей и отдаёт порталу байтами; адрес картинки порталу не уходит. Прежний портал (без photos в задании) — ни
        одной картинки; уже отданное фото повторно не качается; hh ответил не картинкой — просто без фото."""
        import base64
        jpeg = b'\xff\xd8\xff\xe0' + b'\x10' * 64
        self.pages = {0: [search_item(n, photo=n % 2 == 0) for n in range(20)]}
        self.http.routes['/photo/*'] = lambda path, params: FakeResponse(200, jpeg, headers={'Content-Type': 'image/jpeg'})
        bridge = self.bridge()
        task = {'kind': 'search', 'vacancy_id': OPERATOR, 'pages': 1, 'params': {'text': 'оператор'}}

        def photo_calls():
            return [path for path, _params in self.http.calls if path.startswith('/photo/')]

        def done(task_id):
            return next(item for payload in self.portal.task_payloads for item in payload['done'] if item['id'] == task_id)

        self.portal.tasks = [dict(task, id='1')]
        bridge.tasks_round(0)
        self.assertEqual(photo_calls(), [])
        self.assertFalse(any('photo' in item or 'photo_path' in item for item in done('1')['items']))
        self.portal.tasks = [dict(task, id='2', photos=True)]
        bridge.tasks_round(0)
        self.assertEqual(len(photo_calls()), 10)
        self.assertTrue(all(path.startswith('/photo/71') for path in photo_calls()))          # превью, не 40-пиксельный avatar
        items = done('2')['items']
        with_photo = [item for item in items if 'photo' in item]
        self.assertEqual(len(with_photo), 10)
        self.assertEqual(with_photo[0]['photo'], {'type': 'image/jpeg', 'data': base64.b64encode(jpeg).decode('ascii')})
        self.assertFalse(any('photo_path' in item for item in items))
        # Тот же поиск ещё раз — картинки уже отданы порталу.
        self.portal.tasks = [dict(task, id='3', photos=True)]
        bridge.tasks_round(0)
        self.assertEqual(len(photo_calls()), 10)
        self.assertFalse(any('photo' in item for item in done('3')['items']))
        # Новые резюме, а hh вместо картинки отдал страницу — без фото, мост живёт как жил.
        self.pages = {0: [search_item(100 + n, photo=True) for n in range(3)]}
        self.http.routes['/photo/*'] = lambda path, params: FakeResponse(403, 'нельзя', headers={'Content-Type': 'text/html'})
        self.portal.tasks = [dict(task, id='4', photos=True)]
        bridge.tasks_round(0)
        self.assertEqual(done('4')['ok'], True)
        self.assertFalse(any('photo' in item for item in done('4')['items']))
        self.assertIsNone(bridge.trouble)

    def test_view_of_open_resume_sends_name_but_no_phone(self):
        """Рекрутер открыл резюме, чьи контакты hh уже показывает: порталу уходит имя (подпись карточки), но не телефон
        и не почта — они нужны только после «Взять в работу»."""
        bridge = self.bridge()
        seen = '%038x' % 0xc0005
        self.opened.add(seen)
        self.portal.tasks = [{'id': '7', 'kind': 'resume', 'hash': seen, 'reason': 'view'}]
        self.assertEqual(bridge.tasks_round(0), 1)
        done = {item['id']: item for payload in self.portal.task_payloads for item in payload['done']}
        self.assertEqual(done['7']['name'], {'last_name': 'Иванова', 'first_name': 'Анна', 'middle_name': None})
        self.assertNotIn('contacts', done['7'])
        self.assertNotIn('123-45-67', json.dumps(done['7'], ensure_ascii=False))
        self.assertNotIn('anna@example.kz', json.dumps(done['7'], ensure_ascii=False))

    def test_tasks_not_run_while_login_lost_and_gone_resume(self):
        bridge = self.bridge()
        bridge.trouble = ('login_required', 'hh отправил на страницу входа')
        self.portal.tasks = [{'id': '1', 'kind': 'search', 'params': {'text': 'оператор'}, 'pages': 1}]
        bridge.tasks_round(0)
        done = self.portal.task_payloads[-1]['done']
        self.assertEqual((done[0]['ok'], done[0]['trouble']), (False, 'login_required'))
        self.assertEqual(self.calls('/search/resume'), [])
        bridge.trouble = None
        self.http.routes['/resume/*'] = lambda path, params: None            # 404 — резюме скрыто или удалено
        self.portal.tasks = [{'id': '2', 'kind': 'resume', 'hash': 'e' * 38, 'reason': 'view'}]
        bridge.tasks_round(0)
        done = self.portal.task_payloads[-1]['done']
        self.assertEqual((done[0]['ok'], done[0]['gone'], done[0]['trouble']), (False, True, 'error'))

    def test_rest_polls_tasks_and_backs_off_without_them(self):
        now = [1000.0]
        bridge = self.bridge()
        bridge.clock = lambda: now[0]
        waits = []

        def wait(seconds):
            waits.append(seconds)
            now[0] += seconds
            return False
        bridge.stop_event = SimpleNamespace(wait=wait, is_set=lambda: False)
        held = []

        def round_(hold):
            held.append(hold)
            now[0] += hold
            return 0
        bridge.tasks_round = round_
        self.assertFalse(bridge.rest(45.0))
        self.assertEqual(held, [20.0, 20.0, 5.0])                    # пауза — длинным опросом заданий
        # Портал не знает заданий (старая версия) — час их не спрашиваем, пауза как прежде.
        bridge.tasks_round = hh_bridge.HhBridge.tasks_round.__get__(bridge)
        self.portal.missing_tasks = True
        waits.clear()
        self.assertFalse(bridge.rest(700.0))
        self.assertEqual(waits, [300.0, 300.0, 100.0])
        self.assertGreater(bridge.tasks_resume_at, now[0])


if __name__ == '__main__':
    unittest.main()
