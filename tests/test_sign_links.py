# -*- coding: utf-8 -*-
"""Раздел «Ссылка на подписание»: ИИН, периметр, разбор ответа генератора,
роуты, журнал, схема и прошивка в App.jsx.

Что здесь сторожится и почему именно это:

* **ИИН.** Генератор Sapar на опечатку отвечает тем же «Нет документов», что и
  на чужой ИИН, — оператор уйдёт с ответом «документов нет» там, где надо было
  переспросить цифру. Контрольная сумма и дата проверяются у нас, до вендора.
* **Периметр.** Раздел даёт рядовому оператору ссылку на документы ЛЮБОГО
  водителя по ИИН. Каждая строка матрицы прав проверяется отдельно, журнал —
  отдельно от входа.
* **Адрес генератора не уходит наружу.** Требование владельца: оператор до
  ссылки на генератор добираться не должен. Ни один ответ роутов и ни одно
  сообщение об ошибке не содержат его хост.
* **Ссылка не хранится.** В журнале — факт и домен, самой ссылки в DDL нет.
* **Двойники.** Коды ошибок ИИН и коды исходов живут и в питоне, и в js.

Сети и базы здесь нет: генератор подменяется, курсор — двойник.
QR-гейт проверяется здесь же, тем же приёмом, что в
tests/test_sensitive_section_qr_gate.py.
"""

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

from sign_links import access, iin, queries, sapar_link, schema  # noqa: E402
from sign_links import routes as routes_module  # noqa: E402
from sign_links.routes import build_sign_links_blueprint  # noqa: E402

APP_JSX = ROOT / 'src' / 'App.jsx'
IIN_JS = ROOT / 'src' / 'components' / 'sign_links' / 'iin.js'
META_JS = ROOT / 'src' / 'components' / 'sign_links' / 'signLinkMeta.js'
VIEW_JSX = ROOT / 'src' / 'components' / 'sign_links' / 'SignLinksView.jsx'
FAICON_JSX = ROOT / 'src' / 'components' / 'common' / 'FaIcon.jsx'

# Синтетические ИИН — собраны по алгоритму, живых людей за ними нет.
VALID = '900101300007'          # 01.01.1990, контрольная с первого прохода
VALID_SECOND_PASS = '900101300811'  # первый проход даёт 10, решает второй
IMPOSSIBLE_PREFIX = '90010130080'   # оба прохода дают 10

GENERATOR_HOST = 'silt.kz'


def ctx(role='operator', user_id=10, department_code='szov', headed=(), headed_codes=None):
    """Портрет сотрудника. По умолчанию — оператор СЗоВ."""
    return {
        'user_id': user_id,
        'name': 'Тест',
        'role': role,
        'department_id': 1,
        'department_code': department_code,
        'headed_department_ids': list(headed),
        'headed_department_codes': (list(headed_codes) if headed_codes is not None
                                   else ([department_code] if headed else [])),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ИИН
# ─────────────────────────────────────────────────────────────────────────────

class IinTests(unittest.TestCase):
    def test_valid_iin_passes_and_is_returned_as_digits(self):
        self.assertEqual(iin.validate(VALID), (VALID, None))
        self.assertEqual(iin.validate(VALID_SECOND_PASS), (VALID_SECOND_PASS, None))
        self.assertTrue(iin.is_valid(VALID))

    def test_separators_are_dropped_letters_are_not(self):
        self.assertEqual(iin.validate('900101 300 007')[0], VALID)
        self.assertEqual(iin.validate('900101-300-007')[0], VALID)
        self.assertEqual(iin.validate(' 900101300007 ')[0], VALID)
        self.assertEqual(iin.validate('9001O1300007')[1], 'digits')

    def test_every_defect_has_its_own_code(self):
        self.assertEqual(iin.validate('')[1], 'empty')
        self.assertEqual(iin.validate(None)[1], 'empty')
        self.assertEqual(iin.validate('90010130000')[1], 'length')
        self.assertEqual(iin.validate('9001013000071')[1], 'length')
        self.assertEqual(iin.validate('901301300007')[1], 'date')
        self.assertEqual(iin.validate('900230300007')[1], 'date')
        self.assertEqual(iin.validate('900101700007')[1], 'century')
        self.assertEqual(iin.validate('900101300008')[1], 'checksum')

    def test_a_swap_of_neighbouring_digits_is_caught(self):
        """Ровно то, что случается, когда ИИН диктуют по телефону."""
        self.assertEqual(iin.validate('090101300007')[1], 'checksum')

    def test_leap_day_needs_a_leap_year_when_the_century_is_known(self):
        self.assertEqual(iin.validate('000229300000')[1], 'date')      # 1900 — не високосный
        self.assertNotEqual(iin.validate('000229000000')[1], 'date')   # век не проставлен

    def test_impossible_prefix_fails_with_every_control_digit(self):
        for control in range(10):
            self.assertEqual(iin.validate(IMPOSSIBLE_PREFIX + str(control))[1], 'checksum')

    def test_bin_of_a_company_is_not_an_iin(self):
        """У БИН пятая цифра 4–6 — «день» 40+ не бывает."""
        self.assertEqual(iin.validate('230140006818')[1], 'date')

    def test_messages_are_russian_and_cover_every_code(self):
        for code in iin.ERRORS:
            self.assertRegex(iin.error_message(code), '[А-Яа-яЁё]')
        self.assertEqual(iin.error_message('whatever'), iin.ERRORS['checksum'])

    def test_frontend_twin_carries_the_same_codes_and_words(self):
        js = IIN_JS.read_text(encoding='utf-8')
        for code, message in iin.ERRORS.items():
            self.assertIn("%s: '%s'" % (code, message), js, code)


# ─────────────────────────────────────────────────────────────────────────────
# Периметр
# ─────────────────────────────────────────────────────────────────────────────

class SectionEntryTests(unittest.TestCase):
    def test_both_departments_get_in_in_any_role(self):
        for code in ('szov', 'front_office'):
            for role in ('operator', 'trainee', 'sv', 'supervisor'):
                self.assertTrue(access.can_open_section(ctx(role=role, department_code=code)),
                                '%s/%s' % (code, role))
                self.assertTrue(access.can_generate(ctx(role=role, department_code=code)))

    def test_other_departments_do_not(self):
        for code in ('op', 'tez', 'marketing', 'hr', 'accounting', None, ''):
            self.assertFalse(access.can_open_section(ctx(department_code=code)),
                             'отдел %r не должен попадать в раздел' % code)

    def test_trainer_is_out_even_from_a_permitted_department(self):
        self.assertFalse(access.can_open_section(ctx(role='trainer', department_code='szov')))
        self.assertFalse(access.can_open_section(ctx(role='trainer', department_code='front_office')))

    def test_global_admin_gets_in_from_anywhere(self):
        self.assertTrue(access.can_open_section(ctx(role='super_admin', department_code=None)))
        self.assertTrue(access.can_open_section(ctx(role='admin', department_code='op')))

    def test_head_of_a_foreign_department_stays_out(self):
        """Назначение главой ЗАМЕНЯЕТ базовую admin-роль и режет периметр отделом."""
        head_of_sales = ctx(role='admin', department_code='op', headed=[367], headed_codes=['op'])
        self.assertFalse(access.is_global_admin(head_of_sales))
        self.assertFalse(access.can_open_section(head_of_sales))

    def test_heads_of_both_departments_get_in(self):
        szov = ctx(role='admin', department_code='szov', headed=[1], headed_codes=['szov'])
        front = ctx(role='admin', department_code='front_office', headed=[909],
                    headed_codes=['front_office'])
        self.assertTrue(access.can_open_section(szov))
        self.assertTrue(access.can_open_section(front))


class SensitiveQrTests(unittest.TestCase):
    def test_operators_of_both_departments_need_qr(self):
        self.assertTrue(access.requires_sensitive_qr(ctx(department_code='szov')))
        self.assertTrue(access.requires_sensitive_qr(ctx(department_code='front_office')))

    def test_those_who_confirm_are_not_asked(self):
        self.assertFalse(access.requires_sensitive_qr(ctx(role='sv')))
        self.assertFalse(access.requires_sensitive_qr(ctx(role='super_admin', department_code=None)))
        self.assertFalse(access.requires_sensitive_qr(
            ctx(role='admin', department_code='front_office', headed=[909],
                headed_codes=['front_office'])))

    def test_unknown_role_is_closed_not_open(self):
        self.assertTrue(access.requires_sensitive_qr(ctx(role='какая-то-новая')))


class JournalPerimeterTests(unittest.TestCase):
    """«Журнал… для админов» — глобальные админы целиком, главы — свой отдел."""

    def test_global_admin_sees_everything(self):
        for who in (ctx(role='super_admin', department_code=None),
                    ctx(role='admin', department_code='op')):
            self.assertTrue(access.can_view_journal(who))
            self.assertIsNone(access.journal_scope(who))

    def test_head_sees_only_own_department(self):
        front = ctx(role='admin', department_code='front_office', headed=[909],
                    headed_codes=['front_office'])
        self.assertTrue(access.can_view_journal(front))
        self.assertEqual(access.journal_scope(front), ['front_office'])

    def test_rank_and_file_and_supervisors_do_not_see_the_journal(self):
        for role in ('operator', 'trainee', 'sv', 'supervisor'):
            who = ctx(role=role, department_code='szov')
            self.assertFalse(access.can_view_journal(who), role)
            self.assertEqual(access.journal_scope(who), [])

    def test_capabilities_carry_every_key_the_frontend_reads(self):
        payload = access.capabilities(ctx())
        for key in ('can_open', 'can_generate', 'can_view_journal', 'journal_scope',
                    'requires_qr', 'is_global_admin', 'is_department_head'):
            self.assertIn(key, payload)
        view = VIEW_JSX.read_text(encoding='utf-8')
        self.assertIn('can_view_journal', view)
        self.assertIn('journal_scope', view)


# ─────────────────────────────────────────────────────────────────────────────
# Разбор ответа генератора
# ─────────────────────────────────────────────────────────────────────────────

_FORM = ('<div id="form-container-MnuGenerateLinkToSignWithEgovMobile-x-wrapper">'
         '<div id="form-container-MnuGenerateLinkToSignWithEgovMobile-x">'
         '<form id="0" method="post"><input name="__RequestVerificationToken" '
         'type="hidden" value="TOKEN" />'
         '<input type="input" name="flDriverIin" value="%s" class="form-control "/>'
         '<a href="/ru/auth/login">Вход</a>'
         '<input type="submit" value="Сгенерировать ссылку"/></form></div></div>'
         ' </div> <!-- end col -->')

_CHROME = ('<head><link href="/Theme/x.css"><script src="https://cdnjs.cloudflare.com/x.js">'
           '</script></head><body><a href="/kz/link-generator">KZ</a>'
           '<img src="https://pip.silt.kz/img/logo.svg">')


def page(postback=None, body='', iin=VALID, with_title_marker=True):
    """Страница генератора, как она устроена живьём: колонка с alert'ом и формой."""
    alert = ('<div class="alert alert-info" role="alert" id="postback-message">%s</div>'
             % postback) if postback else ''
    marker = '<!-- end page title -->' if with_title_marker else ''
    return (_CHROME + marker + '<div class="row"><div class="col-xl-12">' + alert + body
            + (_FORM % iin) + '</div></div><!-- container --></body>')


class ParseResponseTests(unittest.TestCase):
    def test_no_documents_alert_is_a_calm_answer_not_an_error(self):
        # Дословно то, что генератор отдал 16.09.2026 на несуществующий ИИН.
        outcome, link, message = sapar_link.parse_response(page(
            '&#x41D;&#x435;&#x442; &#x434;&#x43E;&#x43A;&#x443;&#x43C;&#x435;&#x43D;&#x442;'
            '&#x43E;&#x432; &#x43D;&#x430; &#x43F;&#x43E;&#x434;&#x43F;&#x438;&#x441;&#x430;'
            '&#x43D;&#x438;&#x435;. &#x414;&#x43E;&#x43A;&#x443;&#x43C;&#x435;&#x43D;&#x442;'
            '&#x44B; &#x43F;&#x43E;&#x434;&#x43F;&#x438;&#x441;&#x430;&#x43D;&#x44B;'))
        self.assertEqual(outcome, sapar_link.NO_DOCUMENTS)
        self.assertIsNone(link)
        self.assertEqual(message, 'Нет документов на подписание. Документы подписаны')

    def test_link_in_an_anchor_is_found_and_page_chrome_is_not(self):
        outcome, link, _message = sapar_link.parse_response(page(
            body='<div id="postback-message"><a href="https://sign.example.kz/s/abc?x=1&amp;y=2">'
                 'Открыть</a></div>'))
        self.assertEqual(outcome, sapar_link.LINK)
        self.assertEqual(link, 'https://sign.example.kz/s/abc?x=1&y=2')

    def test_bare_url_in_the_message_is_a_link_too(self):
        outcome, link, message = sapar_link.parse_response(page(
            postback='Ссылка для подписания: https://egov.example/mobile/sign/777 действует сутки'))
        self.assertEqual(outcome, sapar_link.LINK)
        self.assertEqual(link, 'https://egov.example/mobile/sign/777')
        # Адреса из текста сообщения вычищены — оператору он уходит отдельно.
        self.assertNotIn('http', message)

    def test_link_in_a_readonly_field_is_found(self):
        outcome, link, _ = sapar_link.parse_response(page(
            body='<input readonly value="https://sign.example.kz/q/9" class="form-control">'))
        self.assertEqual(outcome, sapar_link.LINK)
        self.assertEqual(link, 'https://sign.example.kz/q/9')

    def test_other_message_without_a_link_is_a_rejection(self):
        outcome, link, message = sapar_link.parse_response(page('Водитель не найден в парке'))
        self.assertEqual(outcome, sapar_link.REJECTED)
        self.assertIsNone(link)
        self.assertEqual(message, 'Водитель не найден в парке')

    def test_empty_form_is_unknown_format_not_a_link(self):
        """Страница без ответа — это сбой, а не «ссылка = страница входа»."""
        outcome, link, message = sapar_link.parse_response(page())
        self.assertEqual(outcome, sapar_link.UNAVAILABLE)
        self.assertIsNone(link)
        self.assertIsNone(message)
        self.assertEqual(sapar_link.parse_response('')[0], sapar_link.UNAVAILABLE)

    def test_without_the_title_marker_the_alert_and_the_form_still_anchor_the_region(self):
        """Вендор убрал комментарии в разметке — разбор не ослеп."""
        outcome, _link, message = sapar_link.parse_response(page(
            'Нет документов на подписание', with_title_marker=False))
        self.assertEqual(outcome, sapar_link.NO_DOCUMENTS)
        self.assertEqual(message, 'Нет документов на подписание')
        outcome, link, _ = sapar_link.parse_response(page(
            body='<a href="https://sign.example.kz/s/2">x</a>', with_title_marker=False))
        # Ссылка ПЕРЕД alert'ом и формой без якоря заголовка не видна — это
        # осознанная плата за то, что ссылкой не считается что попало.
        self.assertEqual((outcome, link), (sapar_link.UNAVAILABLE, None))
        outcome, link, _ = sapar_link.parse_response(page(
            postback='<a href="https://sign.example.kz/s/3">Открыть</a>', with_title_marker=False))
        self.assertEqual((outcome, link), (sapar_link.LINK, 'https://sign.example.kz/s/3'))

    def test_iin_echoed_in_the_form_never_leaks_as_a_link(self):
        outcome, link, _ = sapar_link.parse_response(page('Отказ', iin='https://evil.example'))
        self.assertEqual(outcome, sapar_link.REJECTED)
        self.assertIsNone(link)

    def test_link_host_is_a_domain_only(self):
        self.assertEqual(sapar_link.link_host('https://Sign.Example.kz/s/abc'), 'Sign.Example.kz')
        self.assertIsNone(sapar_link.link_host(None))


class _FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError('HTTP %s' % self.status_code)


class _FakeSession:
    """Двойник requests.Session: запоминает GET и POST, отвечает заготовками."""

    def __init__(self, get_text, post_text, fail_get=False, fail_post=False):
        self.headers = {}
        self.calls = []
        self._get_text = get_text
        self._post_text = post_text
        self._fail_get = fail_get
        self._fail_post = fail_post

    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs))
        if self._fail_get:
            raise RuntimeError('timeout')
        return _FakeResponse(self._get_text)

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        if self._fail_post:
            raise RuntimeError('reset by peer')
        return _FakeResponse(self._post_text)


class GenerateFlowTests(unittest.TestCase):
    def setUp(self):
        self.original = sapar_link.requests.Session
        self.session = None

    def tearDown(self):
        sapar_link.requests.Session = self.original

    def _install(self, **kwargs):
        session = _FakeSession(**kwargs)
        sapar_link.requests.Session = lambda: session
        self.session = session
        return session

    def test_get_then_post_with_the_token_and_the_iin(self):
        session = self._install(get_text=page(iin=''),
                                post_text=page(body='<a href="https://sign.example.kz/s/1">x</a>'))
        result = sapar_link.generate(VALID)
        self.assertEqual(result['outcome'], sapar_link.LINK)
        self.assertEqual(result['link'], 'https://sign.example.kz/s/1')
        self.assertEqual([call[0] for call in session.calls], ['GET', 'POST'])
        fields = session.calls[1][2]['files']
        self.assertEqual(fields['__RequestVerificationToken'][1], 'TOKEN')
        self.assertEqual(fields['flDriverIin'][1], VALID)
        self.assertEqual(fields['yoda_form_id'][1], sapar_link.FORM_ID)
        self.assertIn('OTP-Portal', session.headers['User-Agent'])
        self.assertGreaterEqual(result['latency_ms'], 0)

    def test_page_without_a_token_is_unavailable_not_a_post(self):
        session = self._install(get_text='<html>maintenance</html>', post_text='')
        result = sapar_link.generate(VALID)
        self.assertEqual(result['outcome'], sapar_link.UNAVAILABLE)
        self.assertEqual([call[0] for call in session.calls], ['GET'])
        self.assertIn('токен', result['error'])

    def test_network_failures_never_raise(self):
        self._install(get_text='', post_text='', fail_get=True)
        self.assertEqual(sapar_link.generate(VALID)['outcome'], sapar_link.UNAVAILABLE)
        self._install(get_text=page(iin=''), post_text='', fail_post=True)
        self.assertEqual(sapar_link.generate(VALID)['outcome'], sapar_link.UNAVAILABLE)

    def test_unknown_answer_is_unavailable(self):
        self._install(get_text=page(iin=''), post_text=page())
        result = sapar_link.generate(VALID)
        self.assertEqual(result['outcome'], sapar_link.UNAVAILABLE)
        self.assertIsNone(result['link'])

    def test_no_documents_flows_through(self):
        self._install(get_text=page(iin=''), post_text=page('Нет документов на подписание'))
        result = sapar_link.generate(VALID)
        self.assertEqual(result['outcome'], sapar_link.NO_DOCUMENTS)
        self.assertEqual(result['message'], 'Нет документов на подписание')


# ─────────────────────────────────────────────────────────────────────────────
# Роуты
# ─────────────────────────────────────────────────────────────────────────────

class _RecordingCursor:
    """Курсор-двойник: запоминает SQL и параметры, отвечает заготовками по очереди."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = list(answers or [])
        self._last = None

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        self.calls.append((text, params))
        self._last = self.answers.pop(0) if self.answers else None

    def fetchone(self):
        answer = self._last
        if isinstance(answer, list):
            return answer[0] if answer else None
        return answer

    def fetchall(self):
        answer = self._last
        return list(answer) if isinstance(answer, list) else []

    def inserted(self):
        return [params for text, params in self.calls if text.startswith('INSERT INTO sign_link_requests')]


class _GateRecorder:
    def __init__(self, granted):
        self.granted = granted
        self.calls = []

    def __call__(self, user_id, cursor=None):
        self.calls.append(user_id)
        return self.granted


@unittest.skipIf(Flask is None, 'flask не установлен')
class RouteHarness(unittest.TestCase):
    def build(self, context, granted=False, answers=None, generate=None, ready=True):
        cursor = _RecordingCursor(answers)
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor
        self._patch(queries, 'load_access_context', lambda _c, _uid: dict(context))
        self._patch(schema, 'schema_is_ready', lambda _c: ready)
        self.generated = []

        def fake_generate(value):
            self.generated.append(value)
            return generate(value) if callable(generate) else (generate or {
                'outcome': 'link', 'link': 'https://sign.example.kz/s/abc',
                'message': None, 'error': None, 'latency_ms': 812})

        gate = _GateRecorder(granted)
        app = Flask(__name__)
        app.register_blueprint(build_sign_links_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=gate,
            client_ip=lambda: '10.0.0.7',
            generate=fake_generate,
        ))
        app.config['TESTING'] = True
        return app.test_client(), cursor, gate

    def _patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, original if value is None else value)
        self.addCleanup(setattr, module, name, original)


class QrGateRouteTests(RouteHarness):
    def test_operator_without_confirmation_is_stopped_everywhere(self):
        client, _cursor, _gate = self.build(ctx())
        for method, url in (('get', '/api/sign_links/ping'),
                            ('post', '/api/sign_links/generate'),
                            ('get', '/api/sign_links/journal')):
            response = getattr(client, method)(url, json={'iin': VALID})
            self.assertEqual(response.status_code, 403, url)
            self.assertEqual(response.get_json().get('code'), 'SENSITIVE_ACCESS_REQUIRED', url)
        self.assertEqual(len(self.generated), 0)

    def test_closed_section_answers_before_the_qr_question(self):
        """«Раздел вам не выдан» обязан отвечать раньше, чем «нужен QR»."""
        client, _cursor, gate = self.build(ctx(department_code='op'))
        response = client.get('/api/sign_links/ping')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'SIGN_LINKS_SECTION_CLOSED')
        self.assertEqual(gate.calls, [])

    def test_operator_with_confirmation_passes(self):
        # Единственный запрос к курсору — дневной счётчик: schema_is_ready подменена.
        client, _cursor, gate = self.build(ctx(), granted=True, answers=[(3,)])
        response = client.get('/api/sign_links/ping')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(gate.calls, [10])
        payload = response.get_json()
        self.assertTrue(payload['capabilities']['can_generate'])
        self.assertFalse(payload['capabilities']['can_view_journal'])
        self.assertEqual(payload['limits'], {'per_day': routes_module.DAILY_LIMIT,
                                             'used_today': 3,
                                             'left_today': routes_module.DAILY_LIMIT - 3})

    def test_supervisor_head_and_admin_are_not_asked(self):
        for who in (ctx(role='sv'),
                    ctx(role='admin', department_code='front_office', headed=[909],
                        headed_codes=['front_office']),
                    ctx(role='super_admin', department_code=None)):
            client, _cursor, gate = self.build(who, granted=False, answers=[(0,)])
            self.assertEqual(client.get('/api/sign_links/ping').status_code, 200, who['role'])
            self.assertEqual(gate.calls, [], who['role'])


class GenerateRouteTests(RouteHarness):
    def test_link_is_returned_once_and_logged_without_the_link(self):
        client, cursor, _gate = self.build(ctx(), granted=True,
                                           answers=[(0,), (17, None), (1,)])
        response = client.post('/api/sign_links/generate', json={'iin': '900101 300 007'})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['outcome'], 'link')
        self.assertEqual(payload['link'], 'https://sign.example.kz/s/abc')
        self.assertEqual(payload['iin'], VALID)
        self.assertEqual(payload['request_id'], 17)
        # К вендору ушёл нормализованный ИИН.
        self.assertEqual(self.generated, [VALID])
        # В журнале — факт и домен, самой ссылки нет.
        rows = cursor.inserted()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['outcome'], 'link')
        self.assertTrue(row['link_issued'])
        self.assertEqual(row['link_host'], 'sign.example.kz')
        self.assertEqual(row['iin'], VALID)
        self.assertEqual(row['user_id'], 10)
        self.assertEqual(row['department_code'], 'szov')
        self.assertEqual(row['ip'], '10.0.0.7')
        self.assertEqual(row['latency_ms'], 812)
        self.assertNotIn('sign.example.kz/s/abc', repr(row))

    def test_invalid_iin_is_refused_logged_and_never_sent_to_the_vendor(self):
        client, cursor, _gate = self.build(ctx(), granted=True, answers=[(5, None)])
        response = client.post('/api/sign_links/generate', json={'iin': '900101300008'})
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload['code'], 'IIN_INVALID')
        self.assertEqual(payload['reason'], 'checksum')
        self.assertEqual(payload['error'], iin.ERRORS['checksum'])
        self.assertEqual(self.generated, [])
        rows = cursor.inserted()
        self.assertEqual(rows[0]['outcome'], 'invalid')
        self.assertEqual(rows[0]['error_text'], 'checksum')
        self.assertEqual(rows[0]['iin'], '900101300008')

    def test_empty_iin_is_refused_without_a_journal_row(self):
        client, cursor, _gate = self.build(ctx(), granted=True)
        response = client.post('/api/sign_links/generate', json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['reason'], 'empty')
        self.assertEqual(cursor.inserted(), [])

    def test_no_documents_is_a_200_with_the_vendor_words(self):
        client, cursor, _gate = self.build(
            ctx(), granted=True, answers=[(0,), (18, None), (1,)],
            generate={'outcome': 'no_documents', 'link': None,
                      'message': 'Нет документов на подписание. Документы подписаны',
                      'error': None, 'latency_ms': 300})
        response = client.post('/api/sign_links/generate', json={'iin': VALID})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['outcome'], 'no_documents')
        self.assertIsNone(payload['link'])
        self.assertIn('Нет документов', payload['message'])
        self.assertEqual(cursor.inserted()[0]['outcome'], 'no_documents')
        self.assertFalse(cursor.inserted()[0]['link_issued'])

    def test_unavailable_is_a_502_without_the_generator_address(self):
        client, cursor, _gate = self.build(
            ctx(), granted=True, answers=[(0,), (19, None), (1,)],
            generate={'outcome': 'unavailable', 'link': None, 'message': None,
                      'error': 'форма не открылась: https://tps-public.silt.kz timeout',
                      'latency_ms': 20000})
        response = client.post('/api/sign_links/generate', json={'iin': VALID})
        self.assertEqual(response.status_code, 502)
        payload = response.get_json()
        self.assertEqual(payload['code'], 'SIGN_LINK_UNAVAILABLE')
        self.assertNotIn(GENERATOR_HOST, response.get_data(as_text=True))
        # А в журнале техническая причина есть — админу.
        self.assertIn('timeout', cursor.inserted()[0]['error_text'])

    def test_daily_limit_stops_the_vendor_call_and_is_logged(self):
        client, cursor, _gate = self.build(
            ctx(), granted=True, answers=[(routes_module.DAILY_LIMIT,), (20, None)])
        response = client.post('/api/sign_links/generate', json={'iin': VALID})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()['code'], 'SIGN_LINKS_DAILY_LIMIT')
        self.assertEqual(self.generated, [])
        self.assertEqual(cursor.inserted()[0]['outcome'], 'limit')

    def test_head_without_own_department_is_logged_under_the_headed_one(self):
        """Иначе глава не увидел бы свои же запросы в журнале своего отдела."""
        head = ctx(role='admin', department_code=None, headed=[909], headed_codes=['front_office'])
        client, cursor, _gate = self.build(head, answers=[(0,), (21, None), (1,)])
        self.assertEqual(client.post('/api/sign_links/generate', json={'iin': VALID}).status_code, 200)
        self.assertEqual(cursor.inserted()[0]['department_code'], 'front_office')


class JournalRouteTests(RouteHarness):
    def test_rank_and_file_get_403_even_with_qr(self):
        client, _cursor, _gate = self.build(ctx(), granted=True)
        response = client.get('/api/sign_links/journal')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'SIGN_LINKS_JOURNAL_CLOSED')
        self.assertEqual(client.get('/api/sign_links/journal').status_code, 403)

    def test_supervisor_gets_403(self):
        client, _cursor, _gate = self.build(ctx(role='sv'))
        self.assertEqual(client.get('/api/sign_links/journal').status_code, 403)

    def test_head_is_scoped_to_own_department_in_every_query(self):
        head = ctx(role='admin', department_code='front_office', headed=[909],
                   headed_codes=['front_office'])
        client, cursor, _gate = self.build(head, answers=[[], (0, 0, 0, 0, 0, 0), []])
        response = client.get('/api/sign_links/journal?department=szov&user_id=4')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['scope'], ['front_office'])
        journal_calls = [(text, params) for text, params in cursor.calls
                         if 'FROM sign_link_requests' in text]
        self.assertEqual(len(journal_calls), 3)
        for text, params in journal_calls:
            self.assertEqual(params['scope'], ['front_office'], text[:60])
        # Чужой отдел в фильтре не расширяет границу, а сужает до пустого.
        self.assertEqual(journal_calls[0][1]['department_code'], '__none__')
        self.assertEqual(journal_calls[0][1]['user_id'], 4)

    def test_global_admin_has_no_scope_and_gets_filters_through(self):
        client, cursor, _gate = self.build(
            ctx(role='super_admin', department_code=None),
            answers=[[], (7, 5, 1, 1, 3, 4), [(10, 'Тест', 7)]])
        response = client.get('/api/sign_links/journal?date_from=2026-09-01&date_to=2026-09-16'
                              '&outcomes=link,bogus&iin=9001&page=2&page_size=25')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIsNone(payload['scope'])
        self.assertEqual(payload['page'], 2)
        self.assertEqual(payload['summary'], {'requests': 7, 'links': 5, 'no_documents': 1,
                                              'failures': 1, 'people': 3, 'drivers': 4})
        self.assertEqual(payload['people'], [{'user_id': 10, 'name': 'Тест', 'requests': 7}])
        params = cursor.calls[1][1]
        self.assertIsNone(params['scope'])
        self.assertEqual(params['outcomes'], ['link'])
        self.assertEqual(params['iin_prefix'], '9001%')
        self.assertEqual(params['limit'], 25)
        self.assertEqual(params['offset'], 25)
        # Верхняя граница — начало СЛЕДУЮЩИХ суток.
        self.assertEqual(params['date_to'].day, 17)

    def test_no_response_ever_carries_the_generator_address(self):
        client, _cursor, _gate = self.build(ctx(role='super_admin', department_code=None),
                                            answers=[(0,)])
        text = client.get('/api/sign_links/ping').get_data(as_text=True)
        self.assertNotIn(GENERATOR_HOST, text)


# ─────────────────────────────────────────────────────────────────────────────
# Схема и SQL
# ─────────────────────────────────────────────────────────────────────────────

class SchemaTests(unittest.TestCase):
    def test_tables_come_before_indexes(self):
        statements = schema._STATEMENTS
        last_table = max(i for i, text in enumerate(statements) if schema._is_table(text))
        first_index = min(i for i, text in enumerate(statements) if not schema._is_table(text))
        self.assertLess(last_table, first_index)

    def test_the_link_itself_is_not_stored(self):
        ddl = '\n'.join(schema._STATEMENTS)
        self.assertIn('link_issued', ddl)
        self.assertIn('link_host', ddl)
        for forbidden in ('link_url', 'link TEXT', 'link VARCHAR', 'sign_link TEXT'):
            self.assertNotIn(forbidden, ddl)

    def test_a_deleted_account_keeps_its_journal_rows(self):
        ddl = '\n'.join(schema._STATEMENTS)
        self.assertIn('REFERENCES users(id) ON DELETE SET NULL', ddl)

    def test_outcomes_are_shared_with_the_client_and_the_daily_limit(self):
        for code in sapar_link.LINK, sapar_link.NO_DOCUMENTS, sapar_link.REJECTED, sapar_link.UNAVAILABLE:
            self.assertIn(code, schema.OUTCOMES)
            self.assertIn(code, schema.VENDOR_OUTCOMES)
        self.assertNotIn('invalid', schema.VENDOR_OUTCOMES)
        self.assertNotIn('limit', schema.VENDOR_OUTCOMES)
        meta = META_JS.read_text(encoding='utf-8')
        for code in schema.OUTCOMES:
            self.assertIn("%s: '" % code, meta, code)

    def test_journal_sql_carries_the_scope_in_every_query(self):
        for sql in (queries._JOURNAL_PAGE_SQL, queries._JOURNAL_COUNT_SQL,
                    queries._JOURNAL_PEOPLE_SQL):
            self.assertIn('%(scope)s IS NULL OR r.department_code = ANY(%(scope)s)', sql)

    def test_page_and_count_share_the_same_where(self):
        self.assertIn(queries._JOURNAL_WHERE, queries._JOURNAL_PAGE_SQL)
        self.assertIn(queries._JOURNAL_WHERE, queries._JOURNAL_COUNT_SQL)

    def test_row_mapping_matches_the_select_order(self):
        """Строка, где значение каждой колонки — её же имя: сдвиг виден сразу."""
        columns = [c.strip().split('.')[-1] for c in
                   queries._JOURNAL_PAGE_SQL.split('SELECT')[1].split('FROM')[0].split(',')]
        from datetime import datetime
        row = list(columns)
        row[columns.index('link_issued')] = True
        row[columns.index('created_at')] = datetime(2026, 9, 16, 10, 0)
        mapped = queries._row(row)
        for key in ('user_name', 'iin', 'outcome', 'vendor_message', 'link_host',
                    'error_text', 'ip_address'):
            self.assertEqual(mapped[key], key)
        self.assertTrue(mapped['link_issued'])
        self.assertEqual(mapped['created_at'], '2026-09-16T10:00:00')


# ─────────────────────────────────────────────────────────────────────────────
# Прошивка во фронте
# ─────────────────────────────────────────────────────────────────────────────

class FrontendWiringTests(unittest.TestCase):
    """Пункт меню, отрисовка раздела и гард видимости — три РАЗНЫХ места.

    «Ссылка на подписание», как «Посылки», объявлена ОДИН раз в общей части
    меню; два вхождения означали бы копию по ролевым ветвям.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JSX.read_text(encoding='utf-8-sig')

    def test_menu_item_is_declared_exactly_once(self):
        self.assertEqual(self.source.count("handleSidebarViewNavigation(e, 'sign_links')"), 1)
        self.assertEqual(self.source.count('<span className="sidebar-text">Ссылка на подписание</span>'), 1)

    def test_menu_item_is_gated_by_the_section_predicate_and_the_department_filter(self):
        self.assertIn('{canAccessSignLinksSection && (', self.source)
        self.assertIn('const canAccessSignLinksSection = canAccessSignLinksSectionForUser(user);',
                      self.source)
        self.assertIn('<SidebarDeptScope section="sign_links"', self.source)
        self.assertIn("sign_links: ['front_office', 'szov'],", self.source)

    def test_view_is_rendered_and_wrapped_into_the_qr_gate(self):
        self.assertIn('view === "sign_links" && canAccessSignLinksSection', self.source)
        self.assertIn('sectionTitle="Ссылка на подписание"', self.source)
        self.assertIn("import('./components/sign_links/SignLinksView')", self.source)

    def test_visibility_guard_lets_the_section_through(self):
        """Без этой строки гард отдела выкинул бы оператора фронт-офиса обратно."""
        self.assertIn("if (view === 'sign_links' && canAccessSignLinksSection) return;",
                      self.source)

    def test_qr_status_is_requested_before_the_section_is_drawn(self):
        self.assertIn("|| view === 'driver_chats' || view === 'sign_links') {", self.source)

    def test_both_departments_are_named_in_the_predicate(self):
        self.assertIn("SIGN_LINKS_SECTION_DEPARTMENT_CODES = ['front_office', 'szov']", self.source)
        self.assertEqual(sorted(access.SECTION_DEPARTMENT_CODES), ['front_office', 'szov'])

    def test_menu_icon_token_exists(self):
        self.assertIn("'fa-file-signature':", FAICON_JSX.read_text(encoding='utf-8-sig'))

    def test_generator_address_is_not_in_the_frontend(self):
        for path in (APP_JSX, VIEW_JSX, META_JS, IIN_JS):
            self.assertNotIn(GENERATOR_HOST, path.read_text(encoding='utf-8-sig'), path.name)


if __name__ == '__main__':
    unittest.main()
