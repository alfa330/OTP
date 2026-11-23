# -*- coding: utf-8 -*-
"""Создание заявки Syntony через форму (syntony_leads/form.py) и запись пропущенного звонка
(cdr/missed_syntony.py) — SALES-4.

Сети нет: сессия requests заменена двойником, который запоминает запрос и отдаёт заготовленный
ответ. Договор — «Syntony: API заявок, создание и получение», версия 1 от 19.11.2026.

Что закреплено:
  * POST на /api/leads/public/<ключ формы>, тело JSON: телефон, комментарий, метки; без
    Accept: text/html (иначе страница для посетителя) и без переадресаций;
  * 202 — принята; 200 — только с {"status": "accepted"} (страница сайта тоже приходит с 200);
  * 400 — повтор не поможет (final); 403/404/429/503, обрыв и таймаут — повторить позже;
  * ни ключ, ни адрес не попадают в текст ошибки — даже когда requests кладёт адрес в своё
    исключение;
  * кривой ключ в окружении (пробел, перевод строки) — «ключа нет», а не запрос;
  * запись пропущенного: +7 и десять цифр, текст примечания робота, метки icore/missed_call,
    без имени клиента.

Телефоны учебные (7XX555XXXX), ключ выдуманный.
"""

import unittest
from datetime import datetime
from unittest import mock

import requests

from cdr import missed_config, missed_syntony, touches as T
from syntony_leads import form

KEY = 'sf_a1b2c3d4e5f6a7b8c9d0e1f2'


class _Response(object):
    def __init__(self, status, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else ('' if body is None else str(body))

    def json(self):
        if self._body is None:
            raise ValueError('not json')
        return self._body


class _Session(object):
    def __init__(self, response=None, fail=None):
        self.response = response or _Response(202, {'status': 'accepted'})
        self.fail = fail
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail:
            raise self.fail
        return self.response


class CreateLeadTests(unittest.TestCase):
    def send(self, response=None, fail=None, **kwargs):
        session = _Session(response, fail)
        params = {'phone': '+77015550001', 'comment': 'Пропущенный входящий звонок…',
                  'utm': {'utm_source': 'icore', 'utm_medium': 'missed_call', 'utm_term': ''}}
        params.update(kwargs)
        form.create_lead(KEY, session=session, **params)
        return session

    def test_request_is_the_documented_post(self):
        session = self.send()
        url, kwargs = session.calls[0]
        self.assertEqual(url, 'https://app.syntony.kz/api/leads/public/' + KEY)
        self.assertEqual(kwargs['json'], {
            'phone': '+77015550001', 'comment': 'Пропущенный входящий звонок…',
            # пустая метка не уходит вовсе
            'utm': {'utm_source': 'icore', 'utm_medium': 'missed_call'}})
        self.assertEqual(kwargs['headers'], {'Accept': 'application/json'})
        self.assertNotIn('Origin', kwargs['headers'])
        self.assertFalse(kwargs['allow_redirects'])
        self.assertTrue(kwargs['timeout'])

    def test_accepted_answers(self):
        self.send(_Response(202, None))                      # 202 — договор, тело не важно
        self.send(_Response(200, {'status': 'accepted'}))
        self.send(_Response(201, {'status': 'accepted'}))

    def test_200_without_accepted_is_not_taken_for_success(self):
        # Страница для посетителя сайта приходит с 200 — это не «принята».
        with self.assertRaises(form.SyntonyFormError) as caught:
            self.send(_Response(200, None, text='<html>Спасибо!</html>'))
        self.assertFalse(caught.exception.final)

    def test_400_is_final_with_the_code(self):
        with self.assertRaises(form.SyntonyFormError) as caught:
            self.send(_Response(400, {'error': 'phone_required'}))
        self.assertTrue(caught.exception.final)
        self.assertIn('HTTP 400 phone_required', str(caught.exception))

    def test_form_and_capacity_problems_are_retried(self):
        for status, code, words in ((403, 'form_disabled', 'не принимает'),
                                    (404, 'unknown_form', 'не найдена'),
                                    (429, None, '30 заявок в минуту'),
                                    (503, 'no_pipeline', 'нет рабочей воронки'),
                                    (500, None, 'не приняла')):
            with self.subTest(status=status):
                body = {'error': code} if code else None
                with self.assertRaises(form.SyntonyFormError) as caught:
                    self.send(_Response(status, body))
                self.assertFalse(caught.exception.final)
                self.assertIn(words, str(caught.exception))
                self.assertIn('HTTP %d' % status, str(caught.exception))

    def test_redirect_is_not_followed(self):
        with self.assertRaises(form.SyntonyFormError) as caught:
            self.send(_Response(302, None))
        self.assertIn('переадресация', str(caught.exception))

    def test_foreign_error_text_is_not_echoed(self):
        with self.assertRaises(form.SyntonyFormError) as caught:
            self.send(_Response(500, {'error': 'Traceback: ' + KEY}))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertNotIn('Traceback', str(caught.exception))

    def test_network_failures_are_retryable_and_never_show_the_key(self):
        url = 'https://app.syntony.kz/api/leads/public/' + KEY
        for exc in (requests.exceptions.ConnectTimeout('timed out ' + url),
                    requests.exceptions.ReadTimeout('read timed out ' + url),
                    requests.exceptions.ConnectionError('Max retries exceeded with url: ' + url),
                    requests.exceptions.InvalidHeader('bad header ' + KEY),
                    RuntimeError('boom ' + url)):
            with self.subTest(exc=type(exc).__name__):
                with self.assertRaises(form.SyntonyFormError) as caught:
                    self.send(fail=exc)
                self.assertFalse(caught.exception.final)
                self.assertNotIn(KEY, str(caught.exception))
                # Исходное исключение (с адресом) не печатается и в трассировке.
                self.assertIsNone(caught.exception.__cause__)
                self.assertTrue(caught.exception.__suppress_context__)

    def test_no_request_without_a_phone_or_a_key(self):
        session = _Session()
        for phone in ('', None, '12345', '1' * 31):
            with self.subTest(phone=phone):
                with self.assertRaises(form.SyntonyFormError) as caught:
                    form.create_lead(KEY, phone, session=session)
                self.assertTrue(caught.exception.final)
        with self.assertRaises(form.SyntonyFormError):
            form.create_lead('sf_ кривой', '+77015550001', session=session)
        self.assertEqual(session.calls, [])

    def test_own_session_is_closed(self):
        session = _Session()
        session.close = mock.Mock()
        with mock.patch.object(form.requests, 'Session', return_value=session):
            form.create_lead(KEY, '+77015550001')
        session.close.assert_called_once_with()


class FormKeyTests(unittest.TestCase):
    def test_key_from_the_environment(self):
        with mock.patch.dict('os.environ', {form.ENV_FORM_KEY: '  %s\n' % KEY}):
            self.assertEqual(form.form_key(), KEY)
            self.assertTrue(form.is_configured())
        for bad in ('', 'slk_' + 'a' * 20, 'sf_ a1b2c3d4e5f6', 'sf_a1b2\nc3d4e5f6a7', 'sf_abc'):
            with self.subTest(bad=bad), mock.patch.dict('os.environ', {form.ENV_FORM_KEY: bad}):
                self.assertIsNone(form.form_key())

    def test_bad_key_is_logged_once_without_its_value(self):
        form._reported_bad_keys.discard(form.ENV_FORM_KEY)
        with mock.patch.dict('os.environ', {form.ENV_FORM_KEY: 'sf_ секрет-с-пробелом'}), \
                self.assertLogs('syntony_leads.form', level='ERROR') as logs:
            for _ in range(4):                       # робот спрашивает каждые 15 секунд
                self.assertIsNone(form.form_key())
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn('секрет', '\n'.join(logs.output))


def missed_touch(**over):
    touch = {
        'linkedid': '1790571600.1', 'phone': '7015550001', 'started_at': datetime(2026, 11, 23, 10, 0, 0),
        'line_number': '7475550078', 'queue': '3010', 'wait_seconds': 40, 'result': T.RESULT_BUSY,
    }
    touch.update(over)
    return touch


class MissedWriterTests(unittest.TestCase):
    def test_payload_is_phone_note_and_marks(self):
        phone, comment, utm = missed_syntony.build_payload(missed_touch(), 'Центр регистрации')
        self.assertEqual(phone, '+77015550001')
        self.assertEqual(comment, 'Пропущенный входящий звонок 23.11.2026 в 10:00:00 на линию '
                                  '+7 747 555 00 78 (Центр регистрации). Ждал в очереди 40 с, '
                                  'оператор не ответил. За минуту после звонка клиенту не '
                                  'ответили — нужно перезвонить.')
        self.assertEqual(utm, missed_config.SYNTONY_UTM)
        self.assertIsNot(utm, missed_config.SYNTONY_UTM, 'общий словарь меток не отдаём наружу')

    def test_writer_sends_without_a_client_name(self):
        session = _Session()
        writer = missed_syntony.SyntonyMissedWriter(key=KEY, session=session)
        writer.send(missed_touch(), 'Jana такси')
        body = session.calls[0][1]['json']
        self.assertEqual(set(body), {'phone', 'comment', 'utm'})
        self.assertIn('(Jana такси)', body['comment'])

    def test_no_phone_is_final_without_a_request(self):
        session = _Session()
        writer = missed_syntony.SyntonyMissedWriter(key=KEY, session=session)
        with self.assertRaises(missed_syntony.SyntonyFormError) as caught:
            writer.send(missed_touch(phone=''), '')
        self.assertTrue(caught.exception.final)
        self.assertEqual(session.calls, [])

    def test_writer_needs_a_form_key(self):
        with mock.patch.dict('os.environ', {form.ENV_FORM_KEY: ''}):
            with self.assertRaises(missed_syntony.SyntonyFormError):
                missed_syntony.SyntonyMissedWriter()
        with mock.patch.dict('os.environ', {form.ENV_FORM_KEY: KEY}):
            self.assertEqual(missed_syntony.SyntonyMissedWriter().key, KEY)

    def test_marks_do_not_fall_into_any_lead_source(self):
        # Отбивка не считает наши заявки ещё и запросом (db/amo_leads.py), но и без него метки
        # не должны совпадать ни с одним правилом источников.
        from amocrm import leads as amo_leads
        row = {'tags': '', 'utm_source': missed_config.SYNTONY_UTM['utm_source']}
        counts = amo_leads.count_by_source([row])
        self.assertEqual(counts['Общее'], 0, counts)


if __name__ == '__main__':
    unittest.main()
