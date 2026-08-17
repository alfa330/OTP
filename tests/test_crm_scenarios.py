# -*- coding: utf-8 -*-
"""Сценарии обращений — построчная сверка с ТЗ задачи #160.

Здесь проверяется не код, а РЕГЛАМЕНТ: что обращение не уходит в группу, пока
оператор не выполнил обязательные проверки, что «документы появились после
повторного входа» закрывает вопрос без сообщения коллегам, что ошибка не на том
этапе переводится в соседнюю тематику. Ошибка в любом из этих правил не падает
и не логируется — она просто засоряет рабочий чат специалистов или, наоборот,
молча роняет обращение, которое ждали.

Формулировки правил взяты из двух документов, приложенных к задаче
(iTaxi-Sapar.docx и docx.docx), поэтому тесты названы по разделам ТЗ.
"""

import unittest

from crm import scenarios as sc


# Ответы, при которых тематика ничего не блокирует и не закрывает. Нужны как
# точка отсчёта: проверяя валидацию ИИН, нельзя чтобы обращение до неё
# закрылось правилом «документы появились после повторного входа».
NEUTRAL = {
    'sapar_docs_missing': {
        'trips_in_park': 'yes', 'relogin_done': 'yes', 'docs_after_relogin': 'no',
    },
    'sapar_sign_error': {
        'docs_visible': 'yes', 'sapar_related': 'yes', 'apps_restarted': 'yes',
        'cache_cleared': {'value': 'yes', 'detail': 'без изменений'},
        'other_browser': {'value': 'yes', 'detail': 'та же ошибка'},
        'error_repeats': 'Воспроизводится повторно',
    },
    'sapar_payment_required': {
        'trips_in_park': 'yes', 'park_commission_charged': 'yes',
        'payment_shown': 'yes', 'payment_is_sapar_signing': 'yes',
    },
    'sapar_service_error': {
        'signing_related': 'no', 'waited_5min': 'yes', 'relogin_done': 'yes',
        'other_browser_checked': 'yes', 'internet_checked': 'yes', 'error_persists': 'yes',
    },
}


def full(scenario_key, **overrides):
    """Полный набор ответов, при котором тематика готова к отправке.

    Собирается по самому сценарию, а не переписывается руками: добавят вопрос —
    тест не начнёт врать «всё заполнено».
    """
    scenario = sc.get(scenario_key)
    answers = {}
    for step in scenario['steps']:
        key, kind = step['key'], step['kind']
        if kind == sc.ATTACHMENT:
            continue
        if kind == sc.IIN:
            answers[key] = '123456789012'
        elif kind == sc.PERIOD:
            answers[key] = '2026-07'
        elif kind == sc.CHOICE:
            answers[key] = step['options'][0]
        elif kind in (sc.YESNO, sc.YESNO_DATE):
            answers[key] = 'yes' if kind == sc.YESNO else {'value': 'no'}
        elif kind == sc.DATETIME:
            answers[key] = '2026-07-15T10:00'
        else:
            answers[key] = 'значение'
    answers.update(NEUTRAL.get(scenario_key, {}))
    answers.update(overrides)
    return answers


def verdict(scenario_key, answers, **kwargs):
    kwargs.setdefault('has_attachment', True)
    kwargs.setdefault('checks_confirmed', True)
    return sc.evaluate(scenario_key, answers, **kwargs)


class CatalogTest(unittest.TestCase):
    """Состав каталога: пять тематик Sapar из первого файла и посылки из второго."""

    def test_all_topics_from_the_specification_exist(self):
        self.assertEqual(
            [item['key'] for item in sc.SCENARIOS],
            ['sapar_docs_missing', 'sapar_sign_error', 'sapar_payment_required',
             'sapar_sign_status', 'sapar_service_error', 'parcel_location'],
        )

    def test_sapar_topics_go_to_the_sapar_group(self):
        for key in ('sapar_docs_missing', 'sapar_sign_error', 'sapar_payment_required',
                    'sapar_sign_status', 'sapar_service_error'):
            self.assertEqual(sc.get(key)['queue_code'], 'itaxi_sapar', key)

    def test_parcels_go_elsewhere(self):
        """Посылки — ответственному за посылки, а не в группу Sapar."""
        self.assertEqual(sc.get('parcel_location')['queue_code'], 'parcels')

    def test_every_topic_explains_when_it_is_used(self):
        for item in sc.SCENARIOS:
            self.assertTrue(item['when_to_use'], item['key'])

    def test_attachment_requirements_match_the_specification(self):
        expected = {
            'sapar_docs_missing': sc.ATTACH_IMAGE,          # скриншот раздела без документов
            'sapar_sign_error': sc.ATTACH_IMAGE_OR_VIDEO,   # скриншот ИЛИ видео
            'sapar_payment_required': sc.ATTACH_IMAGE,      # скриншот оплаты
            'sapar_sign_status': sc.ATTACH_NONE,            # «скриншот не обязателен»
            'sapar_service_error': sc.ATTACH_IMAGE_OR_VIDEO,  # скриншот или запись экрана
            'parcel_location': sc.ATTACH_NONE,
        }
        for key, kind in expected.items():
            self.assertEqual(sc.get(key)['attachment'], kind, key)


class CommonMandatoryDataTest(unittest.TestCase):
    """Раздел 2 ТЗ: общие обязательные данные."""

    def test_iin_must_be_exactly_twelve_digits(self):
        for bad in ('', '123', '1234567890123', '12345678901a', ' 123456789012 x'):
            result = verdict('sapar_docs_missing', full('sapar_docs_missing', iin=bad))
            self.assertEqual(result['outcome'], sc.INCOMPLETE, repr(bad))
            self.assertIn('iin', result['missing'], repr(bad))

    def test_twelve_digits_pass(self):
        result = verdict('sapar_docs_missing', full('sapar_docs_missing', iin='123456789012'))
        self.assertEqual(result['outcome'], sc.READY)

    def test_reporting_period_is_month_and_year(self):
        for bad in ('2026', 'июль', '2026-13', '2026-00'):
            result = verdict('sapar_docs_missing', full('sapar_docs_missing', period=bad))
            self.assertIn('period', result['missing'], repr(bad))

    def test_period_hint_mentions_the_commission_screenshot(self):
        """ТЗ требует скриншот, где видно снятие комиссии парка за период."""
        self.assertIn('комиссия парка', sc.STEP_PERIOD['hint'])

    def test_technical_topics_ask_device_and_browser(self):
        """«Дата и время, устройство и браузер — только для технических ошибок»."""
        for key in ('sapar_sign_error', 'sapar_service_error'):
            keys = {s['key'] for s in sc.get(key)['steps']}
            self.assertIn('device', keys, key)
            self.assertIn('browser', keys, key)
            self.assertIn('last_try_at', keys, key)
        # А в «статусе подписания» их быть не должно — там это лишние вопросы.
        keys = {s['key'] for s in sc.get('sapar_sign_status')['steps']}
        self.assertNotIn('device', keys)

    def test_nothing_is_sent_without_confirmed_checks(self):
        """«Без проверки указания этих данных бот не должен отправлять сообщение»."""
        result = verdict('sapar_docs_missing', full('sapar_docs_missing'),
                         checks_confirmed=False)
        self.assertEqual(result['outcome'], sc.INCOMPLETE)
        self.assertIn('__checks__', result['missing'])

    def test_nothing_is_sent_without_the_required_attachment(self):
        result = verdict('sapar_docs_missing', full('sapar_docs_missing'),
                         has_attachment=False)
        self.assertEqual(result['outcome'], sc.INCOMPLETE)
        self.assertIn('__attachment__', result['missing'])

    def test_topic_without_attachment_requirement_sends_without_a_file(self):
        result = verdict('sapar_sign_status', full('sapar_sign_status'), has_attachment=False)
        self.assertEqual(result['outcome'], sc.READY)


class DocsMissingTest(unittest.TestCase):
    """Тематика 1 «Документы не поступили»."""

    KEY = 'sapar_docs_missing'

    def test_ready_when_all_conditions_met(self):
        answers = full(self.KEY, trips_in_park='yes', relogin_done='yes',
                       docs_after_relogin='no')
        self.assertEqual(verdict(self.KEY, answers)['outcome'], sc.READY)

    def test_no_trips_closes_without_sending(self):
        """«Поездок в нашем парке не было — отсутствие документов может быть корректным»."""
        result = verdict(self.KEY, full(self.KEY, trips_in_park='no'))
        self.assertEqual(result['outcome'], sc.CLOSE)
        self.assertIn('корректным', result['message'])

    def test_without_relogin_operator_is_sent_back(self):
        result = verdict(self.KEY, full(self.KEY, trips_in_park='yes', relogin_done='no'))
        self.assertEqual(result['outcome'], sc.BLOCKED)
        self.assertIn('войти заново', result['message'])

    def test_docs_appeared_after_relogin_closes_without_sending(self):
        result = verdict(self.KEY, full(self.KEY, trips_in_park='yes', relogin_done='yes',
                                        docs_after_relogin='yes'))
        self.assertEqual(result['outcome'], sc.CLOSE)

    def test_question_about_docs_after_relogin_appears_only_after_relogin(self):
        """Иначе оператор упирается в обязательный вопрос, которого в его случае нет."""
        answers = {'relogin_done': 'no'}
        keys = {s['key'] for s in sc.visible_steps(sc.get(self.KEY), answers)}
        self.assertNotIn('docs_after_relogin', keys)
        keys = {s['key'] for s in sc.visible_steps(sc.get(self.KEY), {'relogin_done': 'yes'})}
        self.assertIn('docs_after_relogin', keys)


class SignErrorTest(unittest.TestCase):
    """Тематика 2 «Не удаётся подписать документы / ошибка при подписании»."""

    KEY = 'sapar_sign_error'

    def ok(self, **overrides):
        return full(self.KEY, **overrides)

    def test_ready_when_everything_done(self):
        self.assertEqual(verdict(self.KEY, self.ok())['outcome'], sc.READY)

    def test_missing_documents_switch_to_the_first_topic(self):
        """«Документы не отображаются — бот переводит в тематику "Документы не поступили"»."""
        result = verdict(self.KEY, self.ok(docs_visible='no'))
        self.assertEqual(result['outcome'], sc.SWITCH)
        self.assertEqual(result['switch_to'], 'sapar_docs_missing')

    def test_egov_problems_are_not_sent_to_the_group(self):
        """«Проблема относится только к ЭЦП, SMS, биометрии или eGov Mobile»."""
        result = verdict(self.KEY, self.ok(sapar_related='no'))
        self.assertEqual(result['outcome'], sc.CLOSE)
        self.assertIn('eGov', result['message'])

    def test_mandatory_actions_block_until_done(self):
        for field, word in (('cache_cleared', 'кэш'),
                            ('other_browser', 'браузер'),
                            ('apps_restarted', 'eGov Mobile')):
            result = verdict(self.KEY, self.ok(**{field: 'no'}))
            self.assertEqual(result['outcome'], sc.BLOCKED, field)
            self.assertIn(word, result['message'], field)

    def test_one_off_error_closes_without_sending(self):
        result = verdict(self.KEY, self.ok(error_repeats='Возникла один раз'))
        self.assertEqual(result['outcome'], sc.CLOSE)

    def test_second_device_check_is_optional(self):
        """У водителя может не быть второго устройства — ТЗ это оговаривает."""
        answers = self.ok()
        answers.pop('other_device', None)
        self.assertEqual(verdict(self.KEY, answers)['outcome'], sc.READY)

    def test_error_stage_options_match_the_specification(self):
        stage = next(s for s in sc.get(self.KEY)['steps'] if s['key'] == 'error_stage')
        self.assertEqual(len(stage['options']), 5)
        self.assertIn('Переход в eGov Mobile', stage['options'])

    def test_several_drivers_raise_the_mass_outage_flag(self):
        result = verdict(self.KEY, self.ok(multiple_drivers='У нескольких'))
        self.assertEqual(result['outcome'], sc.READY)
        self.assertIn(sc.FLAG_MASS_OUTAGE, result['flags'])


class PaymentTest(unittest.TestCase):
    """Тематика 3 «Отображается оплата за подписание документов»."""

    KEY = 'sapar_payment_required'

    def ok(self, **overrides):
        return full(self.KEY, **overrides)

    def test_ready_when_trips_and_payment_confirmed(self):
        self.assertEqual(verdict(self.KEY, self.ok())['outcome'], sc.READY)

    def test_no_trips_closes(self):
        result = verdict(self.KEY, self.ok(trips_in_park='no'))
        self.assertEqual(result['outcome'], sc.CLOSE)

    def test_park_commission_is_checked_before_sending(self):
        result = verdict(self.KEY, self.ok(park_commission_charged='no'))
        self.assertEqual(result['outcome'], sc.BLOCKED)
        self.assertIn('комиссия парка', result['message'])

    def test_payment_gone_closes(self):
        result = verdict(self.KEY, self.ok(payment_shown='no'))
        self.assertEqual(result['outcome'], sc.CLOSE)

    def test_payment_of_another_service_is_not_sent(self):
        result = verdict(self.KEY, self.ok(payment_is_sapar_signing='no'))
        self.assertEqual(result['outcome'], sc.CLOSE)
        self.assertIn('другому сервису', result['message'])


class SignStatusTest(unittest.TestCase):
    """Тематика 4 «Проверить статус подписания документов»."""

    KEY = 'sapar_sign_status'

    def test_sends_as_soon_as_data_is_filled(self):
        """Единственное условие в ТЗ: «Все обязательные данные заполнены»."""
        self.assertEqual(verdict(self.KEY, full(self.KEY), has_attachment=False)['outcome'],
                         sc.READY)

    def test_incomplete_data_blocks(self):
        answers = full(self.KEY)
        answers.pop('park')
        self.assertEqual(verdict(self.KEY, answers, has_attachment=False)['outcome'],
                         sc.INCOMPLETE)

    def test_status_glossary_is_available(self):
        """«Готовая расшифровка статусов» из ТЗ — чтобы отвечали одинаково."""
        glossary = sc.get(self.KEY)['status_glossary']
        self.assertTrue(glossary)
        status, meaning = glossary[0]
        self.assertIn('подписаны водителем', status.lower())
        self.assertIn('Яндекс', meaning)


class ServiceErrorTest(unittest.TestCase):
    """Тематика 5 «Ошибка в работе Sapar»."""

    KEY = 'sapar_service_error'

    def ok(self, **overrides):
        return full(self.KEY, **overrides)

    def test_ready_after_all_checks(self):
        self.assertEqual(verdict(self.KEY, self.ok())['outcome'], sc.READY)

    def test_signing_stage_switches_to_topic_two(self):
        """«Ошибка на этапе подписания — бот переводит оператора в тематику №2»."""
        result = verdict(self.KEY, self.ok(signing_related='yes'))
        self.assertEqual(result['outcome'], sc.SWITCH)
        self.assertEqual(result['switch_to'], 'sapar_sign_error')

    def test_each_mandatory_check_blocks(self):
        for field in ('waited_5min', 'relogin_done', 'other_browser_checked', 'internet_checked'):
            result = verdict(self.KEY, self.ok(**{field: 'no'}))
            self.assertEqual(result['outcome'], sc.BLOCKED, field)

    def test_service_recovered_closes_without_sending(self):
        result = verdict(self.KEY, self.ok(error_persists='no'))
        self.assertEqual(result['outcome'], sc.CLOSE)

    def test_several_drivers_raise_the_mass_outage_flag(self):
        result = verdict(self.KEY, self.ok(multiple_drivers='У нескольких'))
        self.assertIn(sc.FLAG_MASS_OUTAGE, result['flags'])

    def test_error_types_match_the_specification(self):
        step = next(s for s in sc.get(self.KEY)['steps'] if s['key'] == 'error_type')
        self.assertEqual(len(step['options']), 6)
        self.assertIn('Приложение показывает белый экран', step['options'])

    def test_no_reporting_period_here(self):
        """В ТЗ у этой тематики периода нет — лишний обязательный вопрос стоит времени."""
        keys = {s['key'] for s in sc.get(self.KEY)['steps']}
        self.assertNotIn('period', keys)


class ParcelTest(unittest.TestCase):
    """Второй документ: «Уточнение местонахождения посылки»."""

    KEY = 'parcel_location'

    def test_asks_what_the_specification_lists(self):
        keys = [s['key'] for s in sc.get(self.KEY)['steps']]
        self.assertEqual(keys[:4], ['contact_number', 'parcel_description', 'city', 'order_date'])

    def test_sends_when_data_is_filled(self):
        self.assertEqual(verdict(self.KEY, full(self.KEY), has_attachment=False)['outcome'],
                         sc.READY)

    def test_missing_data_blocks(self):
        answers = full(self.KEY)
        answers.pop('city')
        self.assertEqual(verdict(self.KEY, answers, has_attachment=False)['outcome'],
                         sc.INCOMPLETE)

    def test_registry_check_is_not_part_of_the_flow(self):
        """Постановщик 11.08.2026 попросил не включать реестр: ссылки закрыты.

        Сторожим именно это: возвращать проверку Google-таблицы в сценарий
        нельзя без нового решения — прошлая попытка на ней и остановилась.
        """
        scenario = sc.get(self.KEY)
        blob = repr(scenario)
        self.assertNotIn('docs.google.com', blob)
        self.assertNotIn('spreadsheet', blob.lower())
        self.assertNotIn('реестр', ' '.join(scenario['checks']).lower())


class RenderTest(unittest.TestCase):
    """Готовый текст: его собирает система, и правит его никто."""

    def test_subject_carries_topic_and_iin(self):
        subject = sc.render_subject('sapar_docs_missing', {'iin': '123456789012'})
        self.assertIn('Документы не поступили', subject)
        self.assertIn('123456789012', subject)

    def test_body_lists_every_answered_question(self):
        answers = full('sapar_docs_missing', trips_in_park='yes', relogin_done='yes',
                       docs_after_relogin='no')
        body = sc.render_body('sapar_docs_missing', answers)
        self.assertIn('ИИН водителя: 123456789012', body)
        self.assertIn('Отчётный период: июль 2026', body)
        self.assertIn('Были ли поездки в нашем парке: да', body)
        self.assertIn('Появились ли документы после повторного входа: нет', body)

    def test_yes_with_detail_is_readable(self):
        body = sc.render_body('sapar_docs_missing', full(
            'sapar_docs_missing', provider_changed={'value': 'yes', 'detail': '2026-07-10'}))
        self.assertIn('Менялся ли провайдер: да (2026-07-10)', body)

    def test_mass_outage_flag_goes_first(self):
        body = sc.render_body('sapar_service_error', full('sapar_service_error'),
                              flags=[sc.FLAG_MASS_OUTAGE])
        self.assertTrue(body.startswith('⚠️ Возможный массовый сбой'))

    def test_hidden_step_is_not_rendered(self):
        body = sc.render_body('sapar_docs_missing',
                              full('sapar_docs_missing', relogin_done='no'))
        self.assertNotIn('Появились ли документы', body)

    def test_body_reports_confirmed_checks(self):
        body = sc.render_body('sapar_docs_missing', full('sapar_docs_missing'))
        self.assertIn('обязательные проверки', body)


class CatalogForUiTest(unittest.TestCase):
    """Что уезжает на фронт."""

    def test_catalog_carries_steps_checks_and_rules(self):
        for item in sc.public_catalog():
            self.assertTrue(item['steps'], item['key'])
            self.assertIn('rules', item)
            self.assertIn('attachment', item)

    def test_rules_are_serialisable(self):
        """Правила уходят в JSON, поэтому кортежей в них быть не должно."""
        import json
        json.dumps(sc.public_catalog(), ensure_ascii=False)

    def test_unknown_scenario_is_not_ready(self):
        result = sc.evaluate('нет такого', {}, has_attachment=True, checks_confirmed=True)
        self.assertEqual(result['outcome'], sc.INCOMPLETE)


if __name__ == '__main__':
    unittest.main()
