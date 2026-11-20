"""Окно Syntony для CRM «Потока» (владелец, 20.11.2026: окно, которое CRM получала от iCORE,
«переключить на syntony iframe и передать им»).

CRM просит окно своим ключом через вход в окно CRM (wazzup/embed_auth.py, window: "syntony");
учётку за пользователем CRM находит embed_auth, сюда (syntony_window/service.py) приходит её
id. Что держат тесты:

- оператор для Syntony — учётка iCORE: id, ФИО и почта из списка доступа; что бы ни прислала
  CRM в теле, partnerUserId и почту она не подменит, canWrite не уходит;
- окно — только тем, кто в списке доступа (за остальных Syntony не спрашиваем вовсе);
- QR не нужен: сессии портала тут нет, окно из CRM открывается без него (решение владельца
  20.10.2026 для окна CRM) — рядовой оператор из списка получает окно сразу;
- номер водителя можно прислать строкой или числом, scope без номера — «Чаты»;
- ответ Syntony уходит как есть, отметка о связи пишется, как у окна в портале.

SQL не исполняется: подставной склад из tests/test_syntony_window.py.
"""

import contextlib
import unittest

from syntony_window import client, rules, service
from tests.test_syntony_window import (
    CONFIG, FIRED, NOT_LINKED, OK_REPLY, OPERATOR, SV, TRAINEE, WINDOW_URL, FakeStore, Recorder,
)


class CountingDb:
    """База без SQL: только считает открытые курсоры и следит, что сеть идёт без них."""

    def __init__(self):
        self.open = 0
        self.opened = 0

    def _get_cursor(self):
        db = self

        @contextlib.contextmanager
        def cm():
            db.open += 1
            db.opened += 1
            try:
                yield object()
            finally:
                db.open -= 1
        return cm()


class Crm:
    """Запрос окна за учётку так, как его делает вход в окно CRM."""

    def __init__(self, *replies, configured=True):
        self.store = FakeStore()
        self.db = CountingDb()
        self.syntony = Recorder(*(replies or (OK_REPLY,)))
        self.cursors_during_network = []
        self.configured = configured

    def _post(self, url, **kwargs):
        self.cursors_during_network.append(self.db.open)
        return self.syntony(url, **kwargs)

    def window(self, user_id, payload=None):
        return service.open_for_partner(
            self.db, user_id, {} if payload is None else payload,
            load_config=lambda: CONFIG if self.configured else None,
            request_window=lambda config, body: client.request_window(config, body, post=self._post),
            store=self.store)

    @property
    def asked(self):
        return [call['json'] for call in self.syntony.calls]


class CrmWindowTests(unittest.TestCase):
    def test_listed_operator_gets_the_link_as_is_and_without_qr(self):
        crm = Crm()
        crm.store.add(OPERATOR, 'aigerim@icore.kz')
        reply = crm.window(OPERATOR, {'crmUserId': '352', 'window': 'syntony'})
        self.assertEqual((reply.status, reply.body),
                         (200, {'url': WINDOW_URL, 'expiresAt': '2026-11-11T09:12:00Z'}))
        self.assertEqual(crm.asked, [{
            'scope': 'inbox', 'partnerUserId': str(OPERATOR), 'partnerUserName': 'Айгерим Сапарова',
            'partnerUserEmail': 'aigerim@icore.kz', 'theme': 'light', 'locale': 'ru'}])

    def test_crm_cannot_substitute_the_operator(self):
        crm = Crm()
        crm.store.add(SV, 'sv@icore.kz')
        crm.window(SV, {'partnerUserId': '1', 'partnerUserName': 'Супер Админов', 'canWrite': False,
                        'partnerUserEmail': 'super@icore.kz', 'user_id': 1, 'email': 'x@y.kz'})
        sent = crm.asked[0]
        self.assertEqual((sent['partnerUserId'], sent['partnerUserName'], sent['partnerUserEmail']),
                         (str(SV), 'Старший Смены', 'sv@icore.kz'))
        self.assertNotIn('canWrite', sent)
        self.assertEqual(set(sent), {'scope', 'partnerUserId', 'partnerUserName', 'partnerUserEmail',
                                     'theme', 'locale'})

    def test_nobody_outside_the_list_reaches_syntony(self):
        crm = Crm()
        crm.store.add(FIRED, 'gleb@icore.kz')
        for user_id in (OPERATOR, FIRED, 999):
            reply = crm.window(user_id)
            self.assertEqual((reply.status, reply.code), (403, rules.CODE_NO_ACCESS))
        self.assertEqual(crm.asked, [])

    def test_driver_number_as_a_string_a_number_or_a_list(self):
        crm = Crm()
        crm.store.add(TRAINEE)
        crm.window(TRAINEE, {'phone': '+7 701 019 02 19'})
        crm.window(TRAINEE, {'phone': 77010190219})
        crm.window(TRAINEE, {'phones': ['+7 701 019 02 19', '8 (702) 555-44-33']})
        crm.window(TRAINEE, {'scope': 'phone', 'phones': ['87010190219']})
        self.assertEqual([(body['scope'], body.get('phones')) for body in crm.asked], [
            ('phone', ['+7 701 019 02 19']),
            ('phone', ['77010190219']),
            ('phones', ['+7 701 019 02 19', '8 (702) 555-44-33']),
            ('phone', ['87010190219']),
        ])
        self.assertNotIn('partnerUserEmail', crm.asked[0], 'пустая почта не уходит вместо почты')

    def test_inbox_and_leads_never_carry_numbers(self):
        crm = Crm()
        crm.store.add(SV, 'sv@icore.kz')
        crm.window(SV, {'scope': 'inbox', 'phone': '+77010190219'})
        crm.window(SV, {'scope': 'leads', 'phones': ['+77010190219'], 'theme': 'dark'})
        self.assertEqual([(body['scope'], 'phones' in body, body['theme']) for body in crm.asked],
                         [('inbox', False, 'light'), ('leads', False, 'dark')])

    def test_bad_window_or_number_is_refused_before_syntony(self):
        crm = Crm()
        crm.store.add(SV, 'sv@icore.kz')
        for payload in ({'scope': 'admin'}, {'scope': 'phone'}, {'phone': 'нет номера'},
                        {'phones': ['abc']}, {'scope': ['inbox']}):
            reply = crm.window(SV, payload)
            self.assertEqual((reply.status, reply.code), (400, rules.CODE_INVALID_REQUEST), payload)
        self.assertEqual(crm.asked, [])

    def test_refusal_of_syntony_is_passed_on_and_the_link_state_is_remembered(self):
        crm = Crm(NOT_LINKED, OK_REPLY)
        crm.store.add(SV, 'sv@icore.kz')
        reply = crm.window(SV)
        self.assertEqual((reply.status, reply.code), (409, 'operator_not_linked'))
        self.assertEqual(crm.store.access[SV]['link_state'], rules.LINK_NOT_LINKED)
        crm.window(SV)
        self.assertEqual(crm.store.access[SV]['link_state'], rules.LINK_LINKED)

    def test_without_the_key_the_window_is_not_configured(self):
        crm = Crm(configured=False)
        crm.store.add(SV, 'sv@icore.kz')
        reply = crm.window(SV)
        self.assertEqual((reply.status, reply.code), (503, rules.CODE_NOT_CONFIGURED))
        self.assertEqual(crm.asked, [])

    def test_no_database_connection_is_held_while_syntony_answers(self):
        crm = Crm()
        crm.store.add(SV, 'sv@icore.kz')
        crm.window(SV)
        self.assertEqual(crm.cursors_during_network, [0])

    def test_partner_request_ignores_everything_but_the_window(self):
        self.assertEqual(service.partner_window_request(None), {})
        self.assertEqual(service.partner_window_request({'crmUserId': '1', 'window': 'syntony'}),
                         {'scope': 'inbox', 'theme': None})
        self.assertEqual(service.partner_window_request({'phone': True}),
                         {'scope': 'phone', 'theme': None, 'phones': [True]},
                         'True — не номер: дальше его отбросит clean_phones')
        window, error = rules.clean_window_request(service.partner_window_request({'phone': True}))
        self.assertEqual(rules.error_code(error), rules.CODE_INVALID_REQUEST)


if __name__ == '__main__':
    unittest.main()
