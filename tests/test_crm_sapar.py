# -*- coding: utf-8 -*-
"""Предпроверка обращения по API Sapar.

Половину интервью оператору проводить незачем: по ИИН и периоду Sapar сам
говорит, есть ли у водителя документы за месяц и подписаны ли они. Здесь
проверяется решение («что это значит для обращения») отдельно от транспорта
(«как спросить») — ровно так они и разделены в коде.

Сеть тут не нужна ни разу: правила чистые, а клиенту подсовывается фальшивый
requests. Тест, ходящий в чужой сервис, красный в тот день, когда сервис лежит.
"""

import unittest

from crm import sapar, scenarios as sc


def snapshot(**over):
    base = {'available': True, 'month_ready': True, 'month': 2, 'year': 2026,
            'documents': [], 'park_documents': [], 'driver_name': None,
            'error': None, 'iin': '060606060606'}
    base.update(over)
    return base


def document(status='НаПодписанииУВодителя', signed=False):
    return {'source': 'yandex', 'status': status, 'signed': signed,
            'status_label': sapar.status_label(status), 'sum': 5760.0,
            'driver_name': 'Кенжебаев Бекет'}


class VerdictTest(unittest.TestCase):
    def test_month_not_ready_closes_every_topic(self):
        """Пока Яндекс не выгрузил документы за месяц, их нет НИ У КОГО.

        Это ответ водителю, а не повод занимать рабочую группу: иначе в первые
        дни месяца в неё летят десятки одинаковых обращений.
        """
        state = snapshot(month_ready=False)
        for key in sc.SAPAR_PRECHECK:
            verdict = sc.sapar_verdict(key, state)
            self.assertEqual(verdict['outcome'], sc.CLOSE, key)
            self.assertIn('февраль 2026', verdict['message'], key)

    def test_documents_found_lets_the_signing_topics_through(self):
        """«Есть документы — только тогда к личным проверкам» (просьба владельца)."""
        state = snapshot(documents=[document()])
        for key in ('sapar_sign_error', 'sapar_payment_required'):
            self.assertEqual(sc.sapar_verdict(key, state)['outcome'], sc.PASS, key)

    def test_no_documents_stops_the_signing_topics(self):
        state = snapshot(documents=[])
        # Подписывать нечего — это другая тематика, и мастер переводит туда.
        error = sc.sapar_verdict('sapar_sign_error', state)
        self.assertEqual(error['outcome'], sc.SWITCH)
        self.assertEqual(error['switch_to'], 'sapar_docs_missing')

        payment = sc.sapar_verdict('sapar_payment_required', state)
        self.assertEqual(payment['outcome'], sc.CLOSE)

    def test_already_signed_closes_the_signing_error(self):
        state = snapshot(documents=[document('Подписано', signed=True)])
        verdict = sc.sapar_verdict('sapar_sign_error', state)
        self.assertEqual(verdict['outcome'], sc.CLOSE)
        self.assertIn('подписан', verdict['message'])

    def test_docs_missing_works_the_other_way_round(self):
        """У «документы не поступили» логика зеркальная остальным тематикам.

        Документов НЕТ — жалоба подтвердилась, идём по проверкам и пишем в
        группу. Документы ЕСТЬ — они поступили, и вопрос уже не «почему их
        нет», а «почему водитель их не видит»: мастер переводит в «Ошибку в
        работе Sapar», а не закрывает обращение (просьба владельца 21.08.2026 —
        закрытое обращение заставляло оператора начинать заново).
        """
        self.assertEqual(sc.sapar_verdict('sapar_docs_missing',
                                          snapshot(documents=[]))['outcome'], sc.PASS)
        found = sc.sapar_verdict('sapar_docs_missing', snapshot(documents=[document()]))
        self.assertEqual(found['outcome'], sc.SWITCH)
        self.assertEqual(found['switch_to'], 'sapar_service_error')
        # Сначала обновить страницу — иначе перевод превратится в «завести
        # второе обращение по тому же поводу».
        self.assertIn('обновить страницу', found['message'])

    def test_status_topic_is_answered_without_the_group(self):
        """Вопрос тематики — «какой статус». На него есть машинный ответ."""
        verdict = sc.sapar_verdict('sapar_sign_status',
                                   snapshot(documents=[document('Подписано', signed=True)]))
        self.assertEqual(verdict['outcome'], sc.CLOSE)
        self.assertIn('подписан', verdict['message'])

    def test_silence_is_not_an_answer(self):
        """Sapar не ответил — идём по вопросам. Принять молчание за «документов
        нет» значило бы закрыть обращение по выводу, которого никто не делал."""
        for key in sc.SAPAR_PRECHECK:
            self.assertEqual(
                sc.sapar_verdict(key, snapshot(available=False, documents=[]))['outcome'],
                sc.PASS, key)

    def test_topic_without_precheck_passes(self):
        self.assertEqual(sc.sapar_verdict('sapar_service_error', snapshot())['outcome'], sc.PASS)
        self.assertEqual(sc.sapar_verdict('parcel_location', snapshot())['outcome'], sc.PASS)
        self.assertEqual(sc.sapar_verdict('', snapshot())['outcome'], sc.PASS)

    def test_every_message_is_filled_in(self):
        """Незакрытая подстановка доехала бы до оператора как «{period}»."""
        states = [snapshot(month_ready=False), snapshot(documents=[]),
                  snapshot(documents=[document()]),
                  snapshot(documents=[document('Подписано', signed=True)])]
        for key in sc.SAPAR_PRECHECK:
            for state in states:
                message = sc.sapar_verdict(key, state)['message'] or ''
                self.assertNotIn('{', message, (key, message))


class PrecheckedTopicsTest(unittest.TestCase):
    def test_the_list_of_prechecked_topics_is_explicit(self):
        """Добавили тематику Sapar — решите, спрашивать ли по ней сервис.

        «Ошибки в работе Sapar» тут нет намеренно: у неё нет отчётного периода,
        а без периода спрашивать документы не о чем.
        """
        self.assertEqual(sorted(sc.SAPAR_PRECHECK), [
            'sapar_docs_missing', 'sapar_payment_required',
            'sapar_sign_error', 'sapar_sign_status',
        ])

    def test_prechecked_topic_always_asks_iin_and_period(self):
        for key in sc.SAPAR_PRECHECK:
            keys = {step['key'] for step in sc.get(key)['steps']}
            self.assertIn('iin', keys, key)
            self.assertIn('period', keys, key)

    def test_switch_target_exists(self):
        for key, rules in sc.SAPAR_PRECHECK.items():
            for _name, rule in rules:
                if len(rule) > 2 and rule[2]:
                    self.assertIsNotNone(sc.get(rule[2]), (key, rule[2]))

    def test_catalog_tells_the_wizard_which_topics_ask_sapar(self):
        flags = {item['key']: item['sapar'] for item in sc.public_catalog()}
        self.assertEqual({key for key, value in flags.items() if value},
                         set(sc.SAPAR_PRECHECK))


class FakeResponse(object):
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class SaparClientCase(unittest.TestCase):
    """Обвязка: фальшивый requests и токен. Своих проверок не несёт.

    Отдельным классом, а не наследованием от набора тестов: иначе каждый
    наследник прогонял бы ещё и все тесты родителя.
    """

    DOCUMENTS = {
        'Response': {
            'YandexDocuments': [
                {'ServiceId': 334408009649000000, 'DriverIin': '880317351347',
                 'DriverFio': 'Кенжебаев Бекет Маратбекулы', 'Status': 'Подписано',
                 'Sum': 28488.8, 'NdsSum': 4558.21},
            ],
            'TaxiParkDocuments': [
                {'AvrId': 1, 'Month': 7, 'Year': 2026, 'Iin': '880317351347',
                 'Name': 'Кенжебаев Б.', 'AvrSum': 100.0, 'Status': 'Pending'},
            ],
        },
        'Code': 200, 'Message': 'Ok',
    }

    def setUp(self):
        self._real_request = sapar.requests.request
        self.calls = []
        sapar.reset_cache()
        import os
        self._had_token = os.environ.get('SAPAR_API')
        os.environ['SAPAR_API'] = 'test-token'

    def tearDown(self):
        sapar.requests.request = self._real_request
        sapar.reset_cache()
        import os
        if self._had_token is None:
            os.environ.pop('SAPAR_API', None)
        else:
            os.environ['SAPAR_API'] = self._had_token

    def answer(self, payload):
        def fake(method, url, **kwargs):
            self.calls.append({'method': method, 'url': url, **kwargs})
            return FakeResponse(payload() if callable(payload) else payload)
        sapar.requests.request = fake

    def paths(self):
        return [call['url'].split('/')[-1].split('?')[0] for call in self.calls]


class ClientTest(SaparClientCase):
    """Разбор ответа Sapar. Формат снят с живого API 21.08.2026."""

    def test_documents_are_the_yandex_ones(self):
        """Решают закрывающие документы Яндекса; АВР парка лежит отдельно."""
        self.answer(self.DOCUMENTS)
        state = sapar.driver_snapshot('880317351347', 7, 2026)

        self.assertTrue(state['available'])
        self.assertEqual(len(state['documents']), 1)
        self.assertEqual(state['documents'][0]['status_label'], 'подписан')
        self.assertTrue(state['documents'][0]['signed'])
        self.assertEqual(len(state['park_documents']), 1)
        self.assertEqual(state['park_documents'][0]['status_label'], 'ожидает')
        self.assertEqual(state['driver_name'], 'Кенжебаев Бекет Маратбекулы')
        # Документы есть — значит месяц заведомо сформирован, второй запрос лишний.
        self.assertTrue(state['month_ready'])
        self.assertEqual(len(self.calls), 1)

    def test_park_avr_alone_is_not_arrived_documents(self):
        """Из-за этого раздел мог закрыть верную жалобу.

        АВР парка приходит и тем, кому подписывать нечего: на выборке
        21.08.2026 у 7 водителей из 25 с ArrivalStatus=NotArrived ручка всё
        равно вернула строку АВР. Считать её документом — значит сказать
        «документы есть» примерно четверти тех, у кого их нет, и закрыть
        обращение «Документы не поступили» по верной жалобе.
        """
        def payload(*_args):
            # Документов Яндекса нет — клиент спросит ещё и готовность месяца.
            path = self.calls[-1]['url'] if self.calls else ''
            if 'are-docs-ready-to-sign' in path:
                return {'Response': {'AreDocsReadyForSign': True}, 'Code': 200}
            return {'Response': {'YandexDocuments': [],
                                 'TaxiParkDocuments': [{'AvrId': 2, 'Status': 'Active',
                                                        'Name': 'Иванов И.', 'AvrSum': 0.0}]},
                    'Code': 200}
        self.answer(payload)
        state = sapar.driver_snapshot('640516301600', 7, 2026)

        self.assertEqual(state['documents'], [])
        self.assertEqual(len(state['park_documents']), 1)
        # И тематики видят именно «документов нет».
        self.assertEqual(sc.sapar_verdict('sapar_sign_error', state)['outcome'], sc.SWITCH)
        self.assertEqual(sc.sapar_verdict('sapar_docs_missing', state)['outcome'], sc.PASS)

    def test_iin_and_period_reach_the_service(self):
        self.answer(self.DOCUMENTS)
        sapar.driver_snapshot('880317351347', 7, 2026)
        self.assertEqual(self.calls[0]['json'],
                         {'DriverIin': '880317351347', 'Month': 7, 'Year': 2026})
        self.assertIn('get-driver-documents-by-iin', self.calls[0]['url'])
        self.assertTrue(self.calls[0]['headers']['Authorization'].startswith('Bearer '))

    def test_empty_answer_asks_the_park_about_the_month(self):
        def payload(*_args):
            path = self.calls[-1]['url'] if self.calls else ''
            if 'are-docs-ready-to-sign' in path:
                return {'Response': {'AreDocsReadyForSign': False}, 'Code': 200}
            return {'Response': {'YandexDocuments': [], 'TaxiParkDocuments': []}, 'Code': 200}
        self.answer(payload)
        state = sapar.driver_snapshot('000000000000', 12, 2099)

        self.assertTrue(state['available'])
        self.assertEqual(state['documents'], [])
        self.assertIs(state['month_ready'], False)
        # Документы водителя → месяц выбранный → месяц предыдущий (не идёт ли
        # подписание за него). Больше ничего не спрашиваем.
        self.assertEqual(self.paths(),
                         ['get-driver-documents-by-iin', 'are-docs-ready-to-sign',
                          'are-docs-ready-to-sign'])

    def test_park_answer_is_asked_once_for_everyone(self):
        """Ответ по парку один на всех, и в начале месяца его спрашивают
        десятки операторов подряд."""
        self.answer({'Response': {'AreDocsReadyForSign': True}, 'Code': 200})
        self.assertTrue(sapar.signing_period_open(7, 2026))
        self.assertTrue(sapar.signing_period_open(7, 2026))
        self.assertEqual(len(self.calls), 1)
        # Другой месяц — другой ответ, кэш на него не распространяется.
        sapar.signing_period_open(8, 2026)
        self.assertEqual(len(self.calls), 2)

    def test_service_error_is_not_an_empty_list(self):
        """Отказ обязан отличаться от «документов нет»: по второму обращение
        закрывают, по первому — продолжают работать."""
        self.answer({'Response': None, 'Code': 400, 'Message': 'Invalid driverIin'})
        state = sapar.driver_snapshot('abc', 7, 2026)
        self.assertFalse(state['available'])
        self.assertEqual(state['error'], 'Invalid driverIin')
        self.assertEqual(sc.sapar_verdict('sapar_sign_error', state)['outcome'], sc.PASS)

    def test_network_failure_never_raises(self):
        def boom(*_args, **_kwargs):
            raise RuntimeError('таймаут')
        sapar.requests.request = boom
        state = sapar.driver_snapshot('880317351347', 7, 2026)
        self.assertFalse(state['available'])
        self.assertIn('таймаут', state['error'])

    def test_without_a_token_nothing_is_asked(self):
        import os
        os.environ.pop('SAPAR_API', None)
        self.answer(self.DOCUMENTS)
        state = sapar.driver_snapshot('880317351347', 7, 2026)
        self.assertFalse(state['available'])
        self.assertEqual(self.calls, [])


class ClosedPeriodTest(SaparClientCase):
    """Флаг `are-docs-ready-to-sign` — про ОТКРЫТОЕ подписание, а не про выгрузку.

    Замер 21.08.2026: `true` только у июля, при этом документы за июнь есть у
    15 500 водителей, за май у 11 185. Значит `false` означает два
    противоположных случая — «месяц ещё не наступил» и «подписание закрыто», —
    и различать их можно только по календарю. Ошибка здесь стоит закрытого
    обращения по верной жалобе, причём закрытого неправдой.
    """

    EMPTY = {'Response': {'YandexDocuments': [], 'TaxiParkDocuments': []}, 'Code': 200}

    def setUp(self):
        super(ClosedPeriodTest, self).setUp()
        self._real_today = sapar._today

    def tearDown(self):
        sapar._today = self._real_today
        super(ClosedPeriodTest, self).tearDown()

    def at(self, year, month, day=21):
        import datetime
        sapar._today = lambda: datetime.date(year, month, day)

    def silent_park(self):
        def payload(*_args):
            path = self.calls[-1]['url'] if self.calls else ''
            if 'are-docs-ready-to-sign' in path:
                return {'Response': {'AreDocsReadyForSign': False}, 'Code': 200}
            return self.EMPTY
        self.answer(payload)

    def test_month_that_has_not_finished_is_reported_as_not_issued(self):
        self.at(2026, 8)
        self.silent_park()
        state = sapar.driver_snapshot('060606060606', 8, 2026)
        self.assertIs(state['month_ready'], False)
        self.assertEqual(sc.sapar_verdict('sapar_docs_missing', state)['outcome'], sc.CLOSE)

    def test_closed_past_month_claims_nothing_about_the_park(self):
        """Июнь: подписание закрыто, флаг погашен, а документы у людей есть."""
        self.at(2026, 8)
        self.silent_park()
        state = sapar.driver_snapshot('060606060606', 6, 2026)
        self.assertIsNone(state['month_ready'])
        # Раз про парк сказать нечего — работаем по водителю: документов у него
        # нет, жалоба подтвердилась, идём к проверкам.
        self.assertEqual(sc.sapar_verdict('sapar_docs_missing', state)['outcome'], sc.PASS)
        self.assertEqual(sc.sapar_verdict('sapar_sign_error', state)['outcome'], sc.SWITCH)

    def test_open_period_is_taken_as_issued(self):
        def payload(*_args):
            path = self.calls[-1]['url'] if self.calls else ''
            if 'are-docs-ready-to-sign' in path:
                return {'Response': {'AreDocsReadyForSign': True}, 'Code': 200}
            return self.EMPTY
        self.at(2026, 8)
        self.answer(payload)
        state = sapar.driver_snapshot('060606060606', 7, 2026)
        self.assertIs(state['month_ready'], True)
        self.assertEqual(sc.sapar_verdict('sapar_docs_missing', state)['outcome'], sc.PASS)


class SnapshotReachesTheGroupTest(unittest.TestCase):
    """Специалист в группе первым делом лезет в Sapar сам. Раз мы уже
    спросили — ответ должен быть в обращении, а не только у оператора."""

    def answers(self, **over):
        base = {'iin': '060606060606', 'period': '2026-02', 'park': 'iTaxi',
                'city': 'Алматы', 'trips_in_park': 'yes', 'commission_charged': 'yes',
                'corp_or_bonus': 'yes', 'provider_changed': {'value': 'no', 'detail': ''},
                'relogin_done': 'yes', 'docs_after_relogin': 'no'}
        base.update(over)
        return base

    def test_park_avr_stays_out_of_the_group_message(self):
        """АВР парка отвечает не на тот вопрос, ради которого пишут в группу."""
        state = snapshot(documents=[], park_documents=[document('Active')])
        answers = self.answers(**{sc.SAPAR_ANSWER_KEY: state})
        body = sc.render_body('sapar_docs_missing', answers)
        self.assertIn('Документы: за этот период не поступали', body)
        self.assertNotIn('активен', body)

    def test_snapshot_becomes_a_block(self):
        answers = self.answers(**{sc.SAPAR_ANSWER_KEY: snapshot(documents=[document()])})
        kinds = [block['kind'] for block in sc.body_blocks('sapar_docs_missing', answers)]
        self.assertIn(sc.BLOCK_SAPAR, kinds)
        body = sc.render_body('sapar_docs_missing', answers)
        self.assertIn('По данным Sapar:', body)
        self.assertIn('ждёт подписи водителя', body)

    def test_absent_snapshot_adds_nothing(self):
        body = sc.render_body('sapar_docs_missing', self.answers())
        self.assertNotIn('Sapar', body)

    def test_unavailable_snapshot_adds_nothing(self):
        """«Мы не спросили» — не факт о водителе, и в группе ему не место."""
        answers = self.answers(**{sc.SAPAR_ANSWER_KEY: snapshot(available=False)})
        self.assertNotIn('Sapar', sc.render_body('sapar_docs_missing', answers))

    def test_service_key_is_not_mistaken_for_an_answer(self):
        """Служебный ключ не должен попасть в перечень ответов строкой."""
        answers = self.answers(**{sc.SAPAR_ANSWER_KEY: snapshot(documents=[document()])})
        self.assertNotIn(sc.SAPAR_ANSWER_KEY, sc.render_body('sapar_docs_missing', answers))


if __name__ == '__main__':
    unittest.main()


class OpenPeriodHintTest(SaparClientCase):
    """Отчётный период — месяц, ЗА который документы, а не месяц ожидания.

    Документы за июль приходят и подписываются в августе. Оператор спрашивает
    водителя «за какой месяц», слышит «за август» — и получает «за август
    документов нет». Формально верно и выглядит поломкой сервиса, поэтому в
    ответе обязано быть сказано, какой период подписывают сейчас.
    """

    def setUp(self):
        super(OpenPeriodHintTest, self).setUp()
        self._real_today = sapar._today
        import datetime
        sapar._today = lambda: datetime.date(2026, 8, 21)

    def tearDown(self):
        sapar._today = self._real_today
        super(OpenPeriodHintTest, self).tearDown()

    def answer_august_empty_july_signed(self):
        def payload(*_args):
            call = self.calls[-1] if self.calls else {}
            path, body = call.get('url', ''), call.get('json') or {}
            if 'are-docs-ready-to-sign' in path:
                return {'Response': {'AreDocsReadyForSign':
                                     (call.get('params') or {}).get('month') == 7},
                        'Code': 200}
            if body.get('Month') == 7:
                return {'Response': {'YandexDocuments': [
                    {'ServiceId': 1, 'DriverIin': body['DriverIin'], 'DriverFio': 'Иванов И.',
                     'Status': 'Подписано', 'Sum': 1.0}], 'TaxiParkDocuments': []}, 'Code': 200}
            return {'Response': {'YandexDocuments': [], 'TaxiParkDocuments': []}, 'Code': 200}
        self.answer(payload)

    def test_snapshot_carries_the_period_being_signed_now(self):
        self.answer_august_empty_july_signed()
        state = sapar.driver_snapshot('060606060606', 8, 2026)

        self.assertIs(state['month_ready'], False)
        self.assertEqual(state['open_period']['month'], 7)
        self.assertEqual(len(state['open_period']['documents']), 1)

    def test_message_names_it_instead_of_a_bare_refusal(self):
        self.answer_august_empty_july_signed()
        state = sapar.driver_snapshot('060606060606', 8, 2026)
        message = sc.sapar_verdict('sapar_docs_missing', state)['message']

        self.assertIn('август 2026', message)
        self.assertIn('июль 2026', message)
        self.assertIn('у водителя они есть', message)
        self.assertNotIn('{', message)

    def test_closed_month_asks_nothing_extra(self):
        """Про прошлый закрытый месяц подсказка не нужна и не запрашивается."""
        self.answer_august_empty_july_signed()
        state = sapar.driver_snapshot('060606060606', 6, 2026)
        self.assertIsNone(state['open_period'])
        self.assertIsNone(state['month_ready'])

    def test_the_period_field_warns_about_the_trap(self):
        """Подсказка у вопроса — первая линия обороны от этой путаницы."""
        self.assertIn('ЗА который', sc.STEP_PERIOD['hint'])
