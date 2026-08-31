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
from pathlib import Path

from crm import scenarios as sc

ROOT = Path(__file__).resolve().parents[1]


# Ответы, при которых тематика ничего не блокирует и не закрывает. Нужны как
# точка отсчёта: проверяя валидацию ИИН, нельзя чтобы обращение до неё
# закрылось правилом «документы появились после повторного входа».
NEUTRAL = {
    # §4 инструкции #230: водитель настаивает, что Sapar был выбран вовремя и
    # не менялся. Единственная ветка тематики, которая не закрывается сразу.
    'sapar_docs_missing': {
        'provider_choice': sc.PROVIDER_KEPT,
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


# Снимок статусов офисов — то, что приносит проверка §3.2 ТЗ #201. В тестах он
# кладётся руками ровно потому же, почему в проде его кладёт сервер: варианты
# вопроса «Адрес офиса» существуют только в нём.
OFFICE_SNAPSHOT = {
    'available': True,
    'city': 'Астана',
    'day': '2026-08-27',
    'offices': [
        {'id': 7, 'name': 'Офис Астана', 'address': 'проспект Сарыарка, 31',
         'state': 'open', 'label': 'Открыт', 'note': None, 'closed_until': None},
        {'id': 9, 'name': 'Tez Taxi', 'address': None,
         'state': 'absent', 'label': 'Офиса в городе нет',
         'note': None, 'closed_until': None},
    ],
}


def closed_offices(**overrides):
    """Тот же снимок, но офис в городе закрыт."""
    office = dict(OFFICE_SNAPSHOT['offices'][0],
                  state='closed', label='Закрыт', note='ремонт',
                  closed_until='2026-08-31')
    office.update(overrides)
    return dict(OFFICE_SNAPSHOT, offices=[office])


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
        elif kind == sc.CITY:
            # Тот же город, что в снимке офисов: у статуса офиса они обязаны
            # совпадать, иначе тест проверял бы несуществующую пару.
            answers[key] = OFFICE_SNAPSHOT['city']
        elif kind == sc.OFFICE:
            # Варианты этого вопроса приезжают снимком проверки, а не лежат в
            # сценарии, — значит и в тесте ответ берётся оттуда же.
            answers[sc.OFFICES_ANSWER_KEY] = OFFICE_SNAPSHOT
            answers[key] = str(OFFICE_SNAPSHOT['offices'][0]['id'])
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
             'sapar_sign_status', 'sapar_service_error', 'parcel_location',
             'office_status', 'yandex_termobox'],
        )

    def test_sapar_topics_go_to_the_sapar_group(self):
        for key in ('sapar_docs_missing', 'sapar_sign_error', 'sapar_payment_required',
                    'sapar_sign_status', 'sapar_service_error'):
            self.assertEqual(sc.get(key)['queue_code'], 'itaxi_sapar', key)

    def test_region_topics_go_to_the_regions_group(self):
        """ТЗ #201: обе тематики уходят офис-менеджерам городов, а не в Sapar.

        Одна очередь на две тематики — потому что адресат один: группа «iTaxi
        Вопросы/ответы». Разводить их по двум очередям значило бы дважды
        привязывать один и тот же чат.
        """
        for key in ('parcel_location', 'office_status'):
            self.assertEqual(sc.get(key)['queue_code'], 'regions', key)

    def test_every_topic_explains_when_it_is_used(self):
        for item in sc.SCENARIOS:
            self.assertTrue(item['when_to_use'], item['key'])

    def test_attachment_requirements_match_the_specification(self):
        expected = {
            # Инструкция #230: отсюда в группу ничего не уходит, а скриншот
            # отсутствующих документов нужен был именно группе.
            'sapar_docs_missing': sc.ATTACH_NONE,
            'sapar_sign_error': sc.ATTACH_IMAGE_OR_VIDEO,   # скриншот ИЛИ видео
            'sapar_payment_required': sc.ATTACH_IMAGE,      # скриншот оплаты
            'sapar_sign_status': sc.ATTACH_NONE,            # «скриншот не обязателен»
            'sapar_service_error': sc.ATTACH_IMAGE_OR_VIDEO,  # скриншот или запись экрана
            'parcel_location': sc.ATTACH_NONE,
            'office_status': sc.ATTACH_NONE,                # вопрос по таблице, прикладывать нечего
            'yandex_termobox': sc.ATTACH_IMAGE,             # фото имеющегося термокороба
        }
        for key, kind in expected.items():
            self.assertEqual(sc.get(key)['attachment'], kind, key)


class CommonMandatoryDataTest(unittest.TestCase):
    """Раздел 2 ТЗ: общие обязательные данные."""

    # Точка отсчёта — «оплата за подписание»: там есть и ИИН с периодом, и
    # чек-лист, и обязательное вложение. У «документов не поступило» после
    # инструкции #230 нет ни чек-листа, ни вложения, и заканчивается она не
    # отправкой — общие требования на ней уже не проверишь.
    BASE = 'sapar_payment_required'

    def test_iin_must_be_exactly_twelve_digits(self):
        for bad in ('', '123', '1234567890123', '12345678901a', ' 123456789012 x'):
            result = verdict(self.BASE, full(self.BASE, iin=bad))
            self.assertEqual(result['outcome'], sc.INCOMPLETE, repr(bad))
            self.assertIn('iin', result['missing'], repr(bad))

    def test_twelve_digits_pass(self):
        result = verdict(self.BASE, full(self.BASE, iin='123456789012'))
        self.assertEqual(result['outcome'], sc.READY)

    def test_reporting_period_is_month_and_year(self):
        for bad in ('2026', 'июль', '2026-13', '2026-00'):
            result = verdict(self.BASE, full(self.BASE, period=bad))
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
        result = verdict(self.BASE, full(self.BASE), checks_confirmed=False)
        self.assertEqual(result['outcome'], sc.INCOMPLETE)
        self.assertIn('__checks__', result['missing'])

    def test_nothing_is_sent_without_the_required_attachment(self):
        result = verdict(self.BASE, full(self.BASE), has_attachment=False)
        self.assertEqual(result['outcome'], sc.INCOMPLETE)
        self.assertIn('__attachment__', result['missing'])

    def test_topic_without_attachment_requirement_sends_without_a_file(self):
        result = verdict('sapar_sign_status', full('sapar_sign_status'), has_attachment=False)
        self.assertEqual(result['outcome'], sc.READY)


class DocsMissingTest(unittest.TestCase):
    """Тематика 1 «Документы не поступили» — инструкция #230, §3–§5.

    Инструкция переписала эту тематику целиком. Раньше оператор выбирал её сам и
    отправлял обращение в рабочую группу; теперь сюда ведёт только предпроверка
    («документов за период нет»), а кончается всё разговором: либо консультацией
    водителя, либо передачей данных супервайзеру. В группу отсюда не уходит
    ничего — §5 перечисляет исходы, и отправки среди них нет.
    """

    KEY = 'sapar_docs_missing'

    def test_topic_cannot_be_picked_from_the_list(self):
        """«Тематика открывается сама после проверки по ИИН»."""
        self.assertTrue(sc.get(self.KEY)['entry_only'])
        entry = sc.entry_for_queue('itaxi_sapar')
        self.assertNotIn(self.KEY, entry['categories'])
        self.assertEqual(entry['no_documents'], self.KEY)

    def test_provider_question_comes_first(self):
        """§3.1: «В первую очередь необходимо уточнить у водителя…»."""
        steps = [item['key'] for item in sc.get(self.KEY)['steps']]
        self.assertEqual(steps[4], 'provider_choice')

    def test_provider_options_cover_every_cause_from_the_specification(self):
        step = next(s for s in sc.get(self.KEY)['steps'] if s['key'] == 'provider_choice')
        self.assertEqual(step['options'], [
            sc.PROVIDER_NOT_CHOSEN,   # водитель не выбрал провайдера
            sc.PROVIDER_LATE,         # выбрал, но несвоевременно
            sc.PROVIDER_CHANGED,      # менялся в течение отчётного периода
            sc.PROVIDER_KEPT,         # §4: выбран вовремя и не менялся
            sc.PROVIDER_UNKNOWN,      # «не помню» — не утверждение, а его отсутствие
        ])

    def test_every_cause_closes_with_the_consultation(self):
        """§3.2: не выбрал, выбрал поздно или менял — консультация, не обращение."""
        for choice in (sc.PROVIDER_NOT_CHOSEN, sc.PROVIDER_LATE, sc.PROVIDER_CHANGED,
                       sc.PROVIDER_UNKNOWN):
            result = verdict(self.KEY, full(self.KEY, provider_choice=choice))
            self.assertEqual(result['outcome'], sc.CLOSE, choice)

    def test_consultation_script_says_what_to_tell_the_driver(self):
        """Консультация — содержание экрана, а не пояснение к нему."""
        rules = {item['when'][1]: item for item in sc.get(self.KEY)['rules']}
        script = rules[sc.PROVIDER_NOT_CHOSEN]['script']
        self.assertIs(script, sc.PROVIDER_SCRIPT)
        joined = ' '.join(script)
        self.assertIn('Яндекс Про', joined)                 # инструкция по выбору
        self.assertIn('до конца текущего месяца', joined)   # сохранить и не менять
        self.assertIn('сентябре', joined)                   # прошлый период — позже
        # Текст один на все причины: иначе одному водителю рассказали про сроки,
        # а другому нет.
        for choice in (sc.PROVIDER_LATE, sc.PROVIDER_CHANGED, sc.PROVIDER_UNKNOWN):
            self.assertIs(rules[choice]['script'], sc.PROVIDER_SCRIPT, choice)

    def test_driver_insists_provider_never_changed_goes_to_the_supervisor(self):
        """§4: обращение не отправляется, данные уходят супервайзеру."""
        result = verdict(self.KEY, full(self.KEY, provider_choice=sc.PROVIDER_KEPT))
        self.assertEqual(result['outcome'], sc.ESCALATE)

    def test_date_of_choice_is_asked_only_in_that_case(self):
        """Дата нужна супервайзеру для сверки — в остальных ветках её не спрашивают."""
        keys = {s['key'] for s in sc.visible_steps(
            sc.get(self.KEY), {'provider_choice': sc.PROVIDER_NOT_CHOSEN})}
        self.assertNotIn('provider_picked_at', keys)
        keys = {s['key'] for s in sc.visible_steps(
            sc.get(self.KEY), {'provider_choice': sc.PROVIDER_KEPT})}
        self.assertIn('provider_picked_at', keys)

    def test_date_of_choice_is_mandatory_for_the_supervisor(self):
        answers = full(self.KEY, provider_choice=sc.PROVIDER_KEPT)
        answers.pop('provider_picked_at')
        result = verdict(self.KEY, answers)
        self.assertEqual(result['outcome'], sc.INCOMPLETE)
        self.assertIn('provider_picked_at', result['missing'])

    def test_handoff_carries_everything_the_specification_lists(self):
        """§4 перечисляет, что передать: ИИН, период, парк, город, слова
        водителя и дату, когда, по его информации, был выбран Sapar."""
        answers = full(self.KEY, iin='123456789012', park='iTaxi', city='Алматы',
                       period='2026-07', provider_choice=sc.PROVIDER_KEPT,
                       provider_picked_at='2026-06-20')
        handoff = verdict(self.KEY, answers)['handoff']
        self.assertEqual([row['label'] for row in handoff['rows']],
                         ['ИИН', 'Отчётный период', 'Таксопарк', 'Город',
                          'Со слов водителя', 'Дата выбора Sapar со слов водителя'])
        self.assertIn('июль 2026', handoff['text'])
        self.assertIn('20.06.2026', handoff['text'])
        # Готовый текст и экран — одно и то же: копируют ровно то, что видно.
        for row in handoff['rows']:
            self.assertIn('%s: %s' % (row['label'], row['value']), handoff['text'])

    def test_operator_is_warned_not_to_send_the_driver_back_to_the_provider(self):
        """«До получения информации от супервайзера оператор не должен сообщать
        водителю, что необходимо повторно выбрать провайдера»."""
        answers = full(self.KEY, provider_choice=sc.PROVIDER_KEPT)
        note = verdict(self.KEY, answers)['handoff']['note']
        self.assertIn('не просите водителя выбрать провайдера заново', note)

    def test_nothing_from_this_topic_reaches_the_group(self):
        """§5: у «нет документов» отправки среди исходов нет ни в одной ветке."""
        for choice in (sc.PROVIDER_NOT_CHOSEN, sc.PROVIDER_LATE, sc.PROVIDER_CHANGED,
                       sc.PROVIDER_UNKNOWN, sc.PROVIDER_KEPT):
            result = verdict(self.KEY, full(self.KEY, provider_choice=choice))
            self.assertNotEqual(result['outcome'], sc.READY, choice)


class EntryTest(unittest.TestCase):
    """Вход в тематику: проверка по ИИН раньше выбора категории (§1–§3)."""

    def snapshot(self, **over):
        base = {'available': True, 'month_ready': True, 'documents': [],
                'month': 7, 'year': 2026}
        base.update(over)
        return base

    def test_entry_asks_exactly_the_four_mandatory_fields(self):
        """§1: ИИН водителя, отчётный период, таксопарк, город."""
        entry = sc.entry_for_queue('itaxi_sapar')
        self.assertEqual([item['key'] for item in entry['steps']],
                         ['iin', 'period', 'park', 'city'])

    def test_categories_are_the_ones_from_the_specification(self):
        """§2: пять пунктов инструкции — четыре тематики: «не удаётся подписать»
        и «ошибка при подписании» это одно и то же интервью."""
        entry = sc.entry_for_queue('itaxi_sapar')
        self.assertEqual([sc.get(key)['title'] for key in entry['categories']], [
            'Не удаётся подписать документы / ошибка при подписании',
            'Отображается оплата за подписание документов',
            'Проверить статус подписания документов',
            'Ошибка в работе Sapar',
        ])

    def test_documents_found_opens_the_categories(self):
        state = self.snapshot(documents=[{'signed': False, 'status_label': 'ждёт подписи'}])
        result = sc.sapar_entry_verdict('itaxi_sapar', state)
        self.assertEqual(result['outcome'], sc.ENTRY_DOCUMENTS)
        self.assertIn(sc.ENTRY_DOCUMENTS_TEXT, result['message'])

    def test_no_documents_leads_to_the_consultation_topic(self):
        result = sc.sapar_entry_verdict('itaxi_sapar', self.snapshot())
        self.assertEqual(result['outcome'], sc.ENTRY_NO_DOCUMENTS)
        self.assertIn(sc.ENTRY_NO_DOCUMENTS_TEXT, result['message'])
        self.assertEqual(result['scenario'], 'sapar_docs_missing')

    def test_month_not_closed_needs_no_ticket_at_all(self):
        result = sc.sapar_entry_verdict('itaxi_sapar',
                                        self.snapshot(month_ready=False, month=8))
        self.assertEqual(result['outcome'], sc.CLOSE)
        self.assertIn('по парку ещё не сформированы', result['message'])

    def test_silent_sapar_is_not_read_as_no_documents(self):
        """Молчание сервиса — не ответ: решать нечем, спрашиваем оператора."""
        result = sc.sapar_entry_verdict('itaxi_sapar', {'available': False})
        self.assertEqual(result['outcome'], sc.ENTRY_UNKNOWN)
        self.assertIsNone(result['scenario'])

    def test_category_verdicts_are_counted_on_the_same_snapshot(self):
        """Иначе выбор категории стоил бы второго запроса в Sapar."""
        state = self.snapshot(documents=[{'signed': True, 'status_label': 'подписан'}])
        verdicts = sc.entry_category_verdicts('itaxi_sapar', state)
        self.assertEqual(set(verdicts), set(sc.entry_for_queue('itaxi_sapar')['categories']))
        # Документы подписаны — ошибка подписания больше не актуальна.
        self.assertEqual(verdicts['sapar_sign_error']['outcome'], sc.CLOSE)

    def test_entry_reaches_the_interface(self):
        entry = sc.public_entries()[0]
        self.assertEqual(entry['queue_code'], 'itaxi_sapar')
        self.assertTrue(entry['steps'])
        self.assertTrue(entry['categories'])
        self.assertEqual(entry['no_documents'], 'sapar_docs_missing')


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

    def test_error_gone_after_the_actions_closes_without_sending(self):
        """ТЗ, «когда не отправляет» п.1: «Ошибка исчезла после очистки кэша,
        смены браузера, перезапуска приложений или проверки с другого устройства».

        Правило было потеряно при переносе ТЗ: вопроса о РЕЗУЛЬТАТЕ действий не
        было вовсе, и обращение уходило в группу даже когда проблема решилась.
        """
        result = verdict(self.KEY, self.ok(error_persists='no'))
        self.assertEqual(result['outcome'], sc.CLOSE)
        self.assertIn('исчезла', result['message'])

    def test_solved_problem_wins_over_an_unfinished_check(self):
        """Если всё уже работает, возвращать к невыполненной проверке незачем."""
        result = verdict(self.KEY, self.ok(error_persists='no', cache_cleared='no'))
        self.assertEqual(result['outcome'], sc.CLOSE)

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

    def test_local_cause_sends_the_operator_to_fix_it_first(self):
        """ТЗ, «когда не отправляет» п.3: «Проблема вызвана интернет-соединением
        или устройством водителя — оператору предлагается устранить локальную
        причину». Спрашивать только ФАКТ проверок было недостаточно: правило
        оказалось нечем решать, и оно никогда не срабатывало."""
        result = verdict(self.KEY, self.ok(local_cause_excluded='no'))
        self.assertEqual(result['outcome'], sc.BLOCKED)
        self.assertIn('локальн', result['message'].lower())

    def test_service_recovered_closes_without_sending(self):
        result = verdict(self.KEY, self.ok(error_persists='no'))
        self.assertEqual(result['outcome'], sc.CLOSE)

    def test_recovered_service_wins_over_unfinished_checks(self):
        """В ТЗ «сервис заработал» — ПЕРВЫЙ пункт среди причин не отправлять.

        Раньше правило стояло последним, и оператор, ответивший «повторный вход —
        нет» (незачем, всё уже работает), получал блокировку вместо закрытия.
        """
        result = verdict(self.KEY, self.ok(error_persists='no', relogin_done='no',
                                           waited_5min='no'))
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
    """ТЗ #201, §2: «Уточнение посылки» — вопрос офис-менеджерам городов.

    Тематика переписана целиком: раньше она спрашивала ИИН и город заказа и
    уходила ответственному за посылки, теперь спрашивает то, что перечислено в
    §2.3, и уходит в группу регионов. Старый набор вопросов приходил из первого
    ТЗ (#160), где принимающей стороной были не офис-менеджеры.

    Шестое поле — город: постановщик вернула задачу 27.08.2026 с просьбой
    «рядом с фио, номера ВУ и телефона добавить поле для выбора города». В §2.3
    его нет, поэтому тесты ниже держат обе вещи сразу: пять полей ТЗ на месте, и
    город стоит там, где его просили, а не отдельным экраном.
    """

    KEY = 'parcel_location'

    def test_asks_the_five_fields_of_the_specification_and_the_city(self):
        keys = [s['key'] for s in sc.get(self.KEY)['steps']]
        self.assertEqual(keys, ['driver_name', 'driver_licence', 'contact_number',
                                'parcel_city', 'delivery_date', 'parcel_description'])

    def test_the_city_stands_next_to_the_driver_data(self):
        """Возврат 27.08.2026: «рядом с фио, номера ВУ и телефона».

        Три вещи разом. Город на том же экране, что данные водителя, — своего
        экрана он не заводит: один вопрос на экране это лишнее нажатие «Далее»
        посреди разговора с Яндексом. Спрашивается ВЫБОРОМ из справочника, как и
        просили («поле для выбора»), а не свободной строкой: получателю город
        служит адресом, и «Актау» с «г. Актау» для него два разных города. И он
        обязателен — иначе поле, добавленное ради адресации, осталось бы пустым.
        """
        step = next(s for s in sc.get(self.KEY)['steps'] if s['key'] == 'parcel_city')
        self.assertEqual(step['kind'], sc.CITY)
        self.assertFalse(step.get('optional'))
        self.assertEqual(step['group'], 'Водитель')
        self.assertEqual(sc.all_groups(sc.get(self.KEY)), ['Водитель', 'Посылка'])
        answers = full(self.KEY)
        answers.pop('parcel_city')
        result = verdict(self.KEY, answers, has_attachment=False)
        self.assertEqual(result['outcome'], sc.INCOMPLETE)
        self.assertIn('parcel_city', result['missing'])

    def test_none_of_the_fields_can_be_skipped(self):
        """§2.3: «Без заполнения всех пяти полей отправка недоступна».

        Шестое поле, город, обязательно на тех же правах: его добавили в
        обращение, чтобы группа знала адресата, — «если знаете» тут не работает.
        """
        for key in [s['key'] for s in sc.get(self.KEY)['steps']]:
            answers = full(self.KEY)
            answers.pop(key)
            result = verdict(self.KEY, answers, has_attachment=False)
            self.assertEqual(result['outcome'], sc.INCOMPLETE, key)
            self.assertIn(key, result['missing'], key)

    def test_sends_when_everything_is_filled(self):
        self.assertEqual(verdict(self.KEY, full(self.KEY), has_attachment=False)['outcome'],
                         sc.READY)

    def test_registry_check_is_mandatory(self):
        """§2.2: проверка по реестру невостребованных посылок обязательна.

        Это не откат решения от 11.08.2026 «не включать реестр в проверку»: тогда
        реестром была закрытая Google-таблица, которую бот прочитать не мог.
        Теперь реестр — раздел «Посылки» в самом портале (задача #240), и ТЗ #201
        требует проверку прямо: без совпадения оператор отмечает «информация
        отсутствует» и только тогда заполняет обращение.
        """
        scenario = sc.get(self.KEY)
        self.assertTrue(scenario['checks'])
        self.assertIn('реестр', ' '.join(scenario['checks']).lower())
        result = verdict(self.KEY, full(self.KEY), has_attachment=False,
                         checks_confirmed=False)
        self.assertEqual(result['outcome'], sc.INCOMPLETE)
        self.assertIn('__checks__', result['missing'])

    def test_google_sheet_is_not_back(self):
        """Сторожим ровно то, на чём задача остановилась 11.08.2026.

        Проверять можно только то, что читается изнутри портала. Ссылка на
        Google-таблицу в сценарии означала бы возврат к проверке, которую бот
        выполнить не может, — и снова «сдали и вернули».
        """
        blob = repr(sc.get(self.KEY))
        self.assertNotIn('docs.google.com', blob)
        self.assertNotIn('spreadsheet', blob.lower())

    def test_registry_is_asked_by_all_three_identifiers(self):
        """Телефон, номер ВУ и ФИО — три ключа, все три из тех же пяти полей.

        Города среди них нет намеренно: посылку в реестр заносит офис, а не
        звонящий, и запись по тому же водителю могла лечь в соседний город.
        Сузив поиск городом, мы спрятали бы от оператора то самое совпадение,
        из-за которого обращение не нужно отправлять.
        """
        self.assertEqual(sc.lookup_kind(self.KEY), sc.LOOKUP_PARCELS)
        self.assertEqual(sorted(sc.lookup_inputs(sc.LOOKUP_PARCELS)),
                         ['contact_number', 'driver_licence', 'driver_name'])
        self.assertNotIn('parcel_city', sc.lookup_inputs(sc.LOOKUP_PARCELS))

    def test_a_match_stops_the_operator_but_does_not_decide_for_him(self):
        """§2.2 отменяет обращение при записи, «совпадающей по водителю/посылке».

        Совпадает ли она — суждение человека: у водителя бывает вторая посылка, а
        по ФИО в реестр попадают полные тёзки. Поэтому найденное показывается
        рядом с вопросом (BLOCKED), а не закрывает обращение само (CLOSE).
        """
        found = sc.parcel_registry_verdict({'available': True, 'items': [{
            'received_on': '2026-08-12', 'city': 'Астана',
            'office_name': 'Офис Астана', 'kind': 'parcel',
            'status': 'in_office', 'description': 'синяя коробка Kaspi',
        }]})
        self.assertEqual(found['outcome'], sc.BLOCKED)
        self.assertNotEqual(found['outcome'], sc.CLOSE)
        self.assertEqual(len(found['items']), 1)
        line = found['items'][0]
        for part in ('12.08.2026', 'Астана', 'Офис Астана', 'в офисе', 'синяя коробка'):
            self.assertIn(part, line)

    def test_empty_registry_lets_the_operator_through(self):
        """Пустой реестр ничего не доказывает — он и не должен ничего решать."""
        self.assertEqual(sc.parcel_registry_verdict(
            {'available': True, 'items': []})['outcome'], sc.PASS)
        self.assertEqual(sc.parcel_registry_verdict(None)['outcome'], sc.PASS)

    def test_subject_tells_the_tickets_apart(self):
        """ИИН тематика больше не спрашивает (его нет в §2.3), а «Уточнение
        посылки» одинаково у всех — в списке обращения были бы неразличимы.

        Город в теме — по той же причине, по которой он есть у соседней
        тематики: обращение адресуется офис-менеджеру города, и в списке это
        первое, по чему его разбирают.
        """
        subject = sc.render_subject(self.KEY, full(self.KEY, driver_name='Иванов Иван'))
        self.assertIn('Иванов Иван', subject)
        self.assertIn(OFFICE_SNAPSHOT['city'], subject)

    def test_driver_data_goes_to_the_caption(self):
        """ФИО, ВУ, телефон и город копирует специалист — с картинки этого не сделать.

        Город здесь не украшение подписи: обращение получает группа
        офис-менеджеров ВСЕХ городов, и первым делом они смотрят именно его.
        """
        for key in ('driver_name', 'driver_licence', 'contact_number', 'parcel_city'):
            self.assertIn(key, sc.BODY_DATA, key)
        blocks = sc.body_blocks(self.KEY, full(self.KEY))
        data = next(b for b in blocks if b['kind'] == sc.BLOCK_DATA)
        self.assertEqual([row['label'] for row in data['rows']],
                         ['ФИО водителя', 'Номер ВУ', 'Телефон', 'Город'])


class CardOrTextTest(unittest.TestCase):
    """Чем обращение уходит в группу: картинкой или текстом.

    Решение владельца 28.08.2026: картинка остаётся только у вопросов Sapar —
    там её есть чем наполнить (до восемнадцати строк, плашки, галочки). У
    остальных тематик сообщение короткое, и картинка ради него заставляла
    открывать вложение, чтобы прочитать то, что уместилось бы в тексте.
    """

    def test_only_sapar_questions_go_as_a_card(self):
        card = {item['key'] for item in sc.SCENARIOS if sc.sends_card(item['key'])}
        sapar = {item['key'] for item in sc.SCENARIOS
                 if item['queue_code'] == 'itaxi_sapar'}
        self.assertEqual(card, sapar)
        self.assertFalse(sc.sends_card('parcel_location'))
        self.assertFalse(sc.sends_card('office_status'))
        self.assertFalse(sc.sends_card('yandex_termobox'))

    def test_the_rule_is_set_on_the_queue_not_on_each_topic(self):
        """Шестая категория Sapar поедет карточкой сама — про неё не забудут."""
        self.assertEqual(sc.CARD_QUEUES, frozenset({'itaxi_sapar'}))

    def test_unknown_topic_does_not_pretend_to_have_a_card(self):
        self.assertFalse(sc.sends_card(''))
        self.assertFalse(sc.sends_card('нет такой тематики'))

    def test_short_topics_really_are_short(self):
        """Основание правила: у тематик без картинки сообщение в шесть строк.

        Сторожим саму причину, а не только следствие: если тематика без картинки
        разрастётся до полутора десятков строк, решение придётся пересматривать,
        и лучше узнать об этом от теста.
        """
        for item in sc.SCENARIOS:
            if sc.sends_card(item['key']):
                continue
            body = sc.render_body(item['key'], full(item['key']))
            lines = [line for line in body.split(chr(10)) if line.strip()]
            self.assertLessEqual(len(lines), 8, item['key'])


class OfficeStatusTest(unittest.TestCase):
    """ТЗ #201, §3: «Статус работы офиса»."""

    KEY = 'office_status'

    def test_asks_the_city_the_office_the_phone_and_the_drivers_words(self):
        """§3.3 перечисляет три поля, и третье — фиксированный вопрос.

        Спрашивать его у оператора нечего: ответ всегда один и тот же. Он стоит
        заголовком сообщения в группу — там, где его и читают.

        Зато спрашиваем два вопроса, которых в §3.3 нет. Что именно сказал
        водитель (просьба владельца 27.08.2026): подставленная фраза одинакова у
        всех обращений, а регион разбирается как раз по подробностям. И номер
        водителя/курьера (просьба владельца 28.08.2026) — обращение заводится
        ради расхождения, и закрыть его регион может, только позвонив тому, кто
        стоит у офиса.
        """
        keys = [s['key'] for s in sc.get(self.KEY)['steps']]
        self.assertEqual(keys, ['office_city', 'office', 'driver_phone', 'driver_claim'])
        self.assertEqual(sc.get(self.KEY)['group_title'],
                         'Уточнение — работает офис или нет?')

    def test_the_phone_is_required_and_reaches_the_group(self):
        """Просьба владельца 28.08.2026: «чтобы эта инфа тоже отправлялась».

        Поэтому обязательный и в блоке данных: необязательное поле операторы
        пропускали бы, и менеджер снова остался бы без способа связи. Своего
        экрана номер не заводит — у тематики он один.
        """
        step = next(s for s in sc.get(self.KEY)['steps'] if s['key'] == 'driver_phone')
        self.assertFalse(step.get('optional'))
        self.assertEqual(step['group'], 'Офис')
        self.assertEqual(sc.all_groups(sc.get(self.KEY)), ['Офис'])

        answers = full(self.KEY)
        answers.pop('driver_phone')
        result = verdict(self.KEY, answers, has_attachment=False)
        self.assertEqual(result['outcome'], sc.INCOMPLETE)
        self.assertIn('driver_phone', result['missing'])

        self.assertIn('driver_phone', sc.BODY_DATA)
        blocks = sc.body_blocks(self.KEY, full(self.KEY, driver_phone='+7 777 000 00 00'))
        data = next(b for b in blocks if b['kind'] == sc.BLOCK_DATA)
        self.assertEqual([row['label'] for row in data['rows']],
                         ['Город', 'Адрес офиса', 'Телефон водителя/курьера'])
        self.assertIn('+7 777 000 00 00', [row['value'] for row in data['rows']])

    def test_the_phone_is_asked_after_the_office(self):
        """При закрытом офисе обращение не отправляется вовсе.

        Спроси номер раньше — оператор набирал бы данные для сообщения, которого
        не будет, и узнавал бы об этом после.
        """
        keys = [s['key'] for s in sc.get(self.KEY)['steps']]
        self.assertLess(keys.index('office'), keys.index('driver_phone'))

    def test_the_comment_is_optional(self):
        """Оператор пишет его во время разговора — держать его на поле, когда
        добавить нечего, значило бы менять одну помеху на другую."""
        step = next(s for s in sc.get(self.KEY)['steps'] if s['key'] == 'driver_claim')
        self.assertTrue(step['optional'])
        answers = full(self.KEY)
        answers.pop('driver_claim')
        self.assertEqual(verdict(self.KEY, answers, has_attachment=False)['outcome'],
                         sc.READY)

    def test_open_office_against_the_drivers_word_is_sent(self):
        """§3.2: отправляем только при расхождении таблицы и слов водителя."""
        self.assertEqual(verdict(self.KEY, full(self.KEY), has_attachment=False,
                                 checks_confirmed=False)['outcome'], sc.READY)

    def test_closed_office_needs_no_ticket(self):
        answers = full(self.KEY)
        answers[sc.OFFICES_ANSWER_KEY] = closed_offices()
        result = verdict(self.KEY, answers, has_attachment=False)
        self.assertEqual(result['outcome'], sc.CLOSE)
        self.assertIn('Закрыт', result['message'])
        # Причина и срок — то, что оператор передаст водителю вместо обращения.
        self.assertIn('ремонт', result['message'])
        self.assertIn('31.08', result['message'])

    def test_absent_office_needs_no_ticket(self):
        """«Нет офиса» — такой же готовый ответ таблицы, как «Закрыт»."""
        answers = full(self.KEY, office='9')
        result = verdict(self.KEY, answers, has_attachment=False)
        self.assertEqual(result['outcome'], sc.CLOSE)
        self.assertIn('Офиса в городе нет', result['message'])

    def test_city_without_offices_needs_no_ticket(self):
        answers = full(self.KEY)
        answers[sc.OFFICES_ANSWER_KEY] = dict(OFFICE_SNAPSHOT, city='Риддер', offices=[])
        result = verdict(self.KEY, answers, has_attachment=False)
        self.assertEqual(result['outcome'], sc.CLOSE)
        self.assertIn('Риддер', result['message'])

    def test_a_city_where_everything_is_closed_closes_before_the_choice(self):
        """Выбирать из закрытых офисов оператора не заставляем.

        Сами офисы уезжают отдельными строками, а не внутри фразы: в строке уже
        есть разделители («Закрыт · ремонт · до 31.08»), и вторым уровнем тех же
        точек не видно, где кончается один офис.
        """
        result = sc.office_verdict(closed_offices())
        self.assertEqual(result['outcome'], sc.CLOSE)
        self.assertNotIn('Закрыт', result['message'])
        self.assertEqual(len(result['items']), 1)
        self.assertIn('Закрыт', result['items'][0])
        self.assertIn('ремонт', result['items'][0])

    def test_no_schedule_is_not_read_as_closed(self):
        """«Нет графика» — не ответ, а его отсутствие: спрашивать регион можно."""
        snapshot = dict(OFFICE_SNAPSHOT, offices=[
            dict(OFFICE_SNAPSHOT['offices'][0], state='none', label='Нет графика')])
        self.assertEqual(sc.office_verdict(snapshot, '7')['outcome'], sc.PASS)

    def test_silent_directory_does_not_decide_anything(self):
        """Справочник не ответил — обращение не закрываем и не отправляем мимо
        проверки: оператор просто не может выбрать офис."""
        self.assertEqual(sc.office_verdict({'available': False})['outcome'], sc.PASS)
        result = verdict(self.KEY, {'office_city': 'Астана'}, has_attachment=False)
        self.assertEqual(result['outcome'], sc.INCOMPLETE)
        self.assertIn('office', result['missing'])

    def test_office_answer_must_come_from_the_snapshot(self):
        """Единственный вопрос, где членство в списке проверяется по-настоящему:
        список приезжает тем же снимком, что лежит в ответах."""
        step = next(s for s in sc.get(self.KEY)['steps'] if s['key'] == 'office')
        self.assertIsNone(sc.validate_step(
            step, {sc.OFFICES_ANSWER_KEY: OFFICE_SNAPSHOT, 'office': '7'}))
        self.assertIsNotNone(sc.validate_step(
            step, {sc.OFFICES_ANSWER_KEY: OFFICE_SNAPSHOT, 'office': '404'}))

    def test_message_carries_the_table_and_the_drivers_word(self):
        """В группе должно быть видно и то, и другое: иначе вопрос «работает ли
        офис» при статусе «Открыт» читается как бессмыслица."""
        answers = full(self.KEY)
        answers.pop('driver_claim')
        body = sc.render_body(self.KEY, answers)
        self.assertIn('Адрес офиса: Офис Астана · проспект Сарыарка, 31', body)
        self.assertIn('По таблице статусов офисов:', body)
        self.assertIn('Статус на сегодня: Открыт', body)
        # Не написали — остаётся фраза §3.1, без неё сообщение теряет смысл.
        self.assertIn('Со слов водителя: офис не работает', body)

    def test_the_comment_replaces_the_default_phrase(self):
        body = sc.render_body(self.KEY, full(
            self.KEY, driver_claim='Приехал к 10:00, дверь закрыта, на звонки не отвечают'))
        self.assertIn('Со слов водителя: Приехал к 10:00, дверь закрыта, '
                      'на звонки не отвечают', body)
        self.assertNotIn('офис не работает', body)

    def test_the_comment_does_not_stand_twice(self):
        """Он показывается В ПАРЕ со статусом таблицы. Попав ещё и в общий
        перечень ответов, он оказался бы отдельно от того самого утверждения,
        которому противоречит, — и повторился бы в каждом сообщении."""
        body = sc.render_body(self.KEY, full(self.KEY, driver_claim='дверь закрыта'))
        self.assertEqual(body.count('дверь закрыта'), 1)

    def test_subject_names_the_city(self):
        self.assertIn('Астана', sc.render_subject(self.KEY, full(self.KEY)))


class RenderTest(unittest.TestCase):
    """Готовый текст: его собирает система, и правит его никто."""

    def test_subject_carries_topic_and_iin(self):
        subject = sc.render_subject('sapar_docs_missing', {'iin': '123456789012'})
        self.assertIn('Документы не поступили', subject)
        self.assertIn('123456789012', subject)

    def test_body_opens_with_driver_data_line_by_line(self):
        """ТЗ задачи #206: ИИН, таксопарк, город и период — подписанными
        строками, а не склеенной строкой контекста."""
        answers = full('sapar_docs_missing', iin='123456789012',
                       park='iTaxi', city='Алматы')
        body = sc.render_body('sapar_docs_missing', answers)
        self.assertEqual(body.split(chr(10) * 2)[0].split(chr(10)), [
            'ИИН: 123456789012',
            'Таксопарк: iTaxi',
            'Город: Алматы',
            'Отчётный период: июль 2026',
        ])

    def test_every_topic_says_what_it_asks_of_the_group(self):
        """Заголовок сообщения в группе — просьба, а не название проблемы (#206).

        Забыть её не смертельно (заголовком станет тема обращения), но тема
        несёт ИИН, и он продублировался бы со строкой данных прямо под собой.
        Тематика со своим шаблоном формулирует просьбу сама — ей отдельного
        заголовка не нужно.
        """
        for scenario in sc.SCENARIOS:
            if scenario.get('body_template'):
                continue
            heading = scenario.get('group_title')
            self.assertTrue(heading, scenario['key'])
            self.assertNotIn('ИИН', heading, scenario['key'])

    def test_iin_stands_both_in_the_subject_and_in_the_body(self):
        """Два места — две работы: по теме обращение ищут в iCORE, а специалист
        в группе работает по водителю и ищет ИИН в самом сообщении (#206)."""
        answers = full('sapar_docs_missing', iin='123456789012')
        self.assertIn('123456789012', sc.render_subject('sapar_docs_missing', answers))
        self.assertIn('ИИН: 123456789012', sc.render_body('sapar_docs_missing', answers))

    def test_answered_questions_are_all_there(self):
        answers = full('sapar_payment_required', trips_in_park='yes', corp_or_bonus='no')
        body = sc.render_body('sapar_payment_required', answers)
        self.assertIn('✅ Поездки выполнены в нашем парке', body)
        self.assertIn('❌ Начислялись корпоративные поездки или бонусы', body)

    def test_checked_facts_go_one_per_line_under_a_counted_heading(self):
        """ТЗ задачи #206: пункты проверки видны поштучно, каждый со знаком.

        Склеенные в одну строку («Да: поездки … · комиссия …»), они экономили
        место, но читались сплошным текстом: специалисту в группе не было видно,
        что именно подтвердилось.
        """
        body = sc.render_body('sapar_payment_required', full('sapar_payment_required'))
        self.assertIn('🔍 Проверено оператором: 5 из 5', body)
        self.assertIn('✅ Комиссия парка списывалась', body)
        self.assertNotIn('Да: ', body)

    def test_heading_counts_exactly_the_lines_under_it(self):
        """Заголовок, который не сходится со списком под собой, хуже, чем его
        отсутствие: «5 из 5» над двумя крестиками читается как ошибка."""
        answers = full('sapar_payment_required', corp_or_bonus='no')
        block = next(b for b in sc.render_body('sapar_payment_required',
                                               answers).split(chr(10) * 2)
                     if b.startswith('🔍'))
        lines = block.split(chr(10))
        head, items = lines[0], lines[1:]
        self.assertEqual(head, '🔍 Проверено оператором: %d из %d'
                         % (sum(1 for line in items if line.startswith('✅')), len(items)))

    def test_performed_actions_stay_a_single_line_apart_from_facts(self):
        """«Я это сделал» и «это так» — разные вещи, в один список нельзя.

        Действий у одной тематики до шести, и поштучно они вытеснили бы из
        сообщения то, ради чего его читают.
        """
        body = sc.render_body('sapar_service_error', full('sapar_service_error'))
        self.assertIn('✅ Выполнено: ожидание 5 минут', body)
        self.assertIn('интернет-соединение', body.split('✅ Выполнено:')[1])

    def test_list_items_are_separated_by_a_dot_not_a_comma(self):
        """У подписей внутри бывают запятые, и перечень через запятую читался
        неоднозначно: непонятно, где кончается один пункт."""
        body = sc.render_body('sapar_service_error', full('sapar_service_error'))
        line = next(l for l in body.split(chr(10)) if l.startswith('✅ Выполнено: '))
        self.assertIn(' · ', line)
        self.assertEqual(len(line.split(' · ')), 6, line)

    def test_yes_with_detail_is_readable(self):
        body = sc.render_body('sapar_sign_error', full(
            'sapar_sign_error',
            cache_cleared={'value': 'yes', 'detail': 'без изменений'}))
        self.assertIn('Очистка кэша: да (без изменений)', body)

    def test_datetime_is_written_the_way_people_read_it(self):
        body = sc.render_body('sapar_service_error',
                              full('sapar_service_error', last_try_at='2026-08-17T12:38'))
        self.assertIn('Последняя попытка: 17.08.2026 12:38', body)
        self.assertNotIn('2026-08-17T12:38', body)

    def test_blocks_are_separated_by_a_blank_line(self):
        """Пустая строка — разметка: по ней и Telegram, и карточка делят текст."""
        body = sc.render_body('sapar_service_error', full('sapar_service_error'))
        self.assertIn('\n\n', body)
        for block in body.split('\n\n'):
            self.assertTrue(block.strip(), 'пустой блок в тексте')

    def test_mass_outage_flag_goes_first(self):
        body = sc.render_body('sapar_service_error', full('sapar_service_error'),
                              flags=[sc.FLAG_MASS_OUTAGE])
        self.assertTrue(body.startswith('⚠️ Возможный массовый сбой'))

    def test_hidden_step_is_not_rendered(self):
        body = sc.render_body('sapar_docs_missing',
                              full('sapar_docs_missing',
                                   provider_choice=sc.PROVIDER_NOT_CHOSEN))
        self.assertNotIn('Дата выбора Sapar', body)

    def test_checklist_line_is_gone_on_purpose(self):
        """Строка «✔️ Чек-лист выполнен: N из N» ничего не сообщала.

        Другого значения у неё быть не могло: без подтверждённого чек-листа
        обращение до отправки не доходит. То есть она занимала строку в каждом
        сообщении и всегда говорила одно и то же — ровно тот шум, из-за которого
        сообщение и переделывали (#206). Что проверки были, видно по блоку
        «Проверено оператором».
        """
        body = sc.render_body('sapar_docs_missing', full('sapar_docs_missing'))
        self.assertNotIn('Чек-лист', body)

    def test_preview_is_built_only_for_a_ready_ticket(self):
        """Предпросмотр не должен показывать текст, который никуда не уйдёт:
        иначе оператор правит в голове сообщение, которого не будет.
        """
        answers = full('sapar_payment_required')
        without = sc.evaluate('sapar_payment_required', answers, has_attachment=True,
                              checks_confirmed=False)
        self.assertNotEqual(without['outcome'], sc.READY)
        self.assertIn('__checks__', without['missing'])

        routes = (ROOT / 'crm' / 'routes.py').read_text(encoding='utf-8')
        preview = routes.split("verdict['preview']")[0]
        self.assertIn("if verdict['outcome'] == scenarios.READY:", preview)


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


class RulesAreDecidableTest(unittest.TestCase):
    """Каждое правило должно опираться на существующий вопрос.

    Правило, ссылающееся на ключ, которого нет в steps, — мёртвый текст: оно
    выглядит как перенесённое требование ТЗ, но не срабатывает никогда. Именно
    так потерялись «ошибка исчезла» в тематике 2 и «локальная причина» в
    тематике 5 — обе прошли ревью глазами и обе не работали.
    """

    def test_every_rule_references_an_existing_step(self):
        for scenario in sc.SCENARIOS:
            keys = {step['key'] for step in scenario['steps']}
            for item in scenario.get('rules', []):
                self.assertIn(item['when'][0], keys,
                              '%s: правило по несуществующему вопросу %s'
                              % (scenario['key'], item['when'][0]))

    def test_every_rule_value_is_reachable(self):
        """Значение в правиле должно быть таким, какое вопрос вообще может дать."""
        for scenario in sc.SCENARIOS:
            steps = {step['key']: step for step in scenario['steps']}
            for item in scenario.get('rules', []):
                key, expected = item['when']
                step = steps[key]
                if step['kind'] in (sc.YESNO, sc.YESNO_DATE):
                    self.assertIn(expected, sc.yesno_values(step),
                                  '%s/%s' % (scenario['key'], key))
                elif step['kind'] == sc.CHOICE:
                    self.assertIn(expected, step['options'], '%s/%s' % (scenario['key'], key))

    def test_unknown_is_allowed_only_where_the_question_permits_it(self):
        """Задача #172: третий ответ там, где оператор не может проверить сам.

        Именно у вопроса, а не у типа: там, где ответ обязан быть точным,
        уклониться по-прежнему нельзя, иначе «неизвестно» расползётся по всем
        проверкам и обращение уйдёт в группу пустым.

        Ни одного такого вопроса сейчас нет — единственный был про провайдера, и
        инструкция #230 заменила его списком причин с отдельным «не помнит».
        Механизм проверяем на самом вопросе, а не на тематике: он остаётся в силе
        для следующего вопроса, который оператор не сможет проверить сам.
        """
        lenient = sc.step('sample', 'Пример', sc.YESNO, allow_unknown=True)
        self.assertEqual(sc.yesno_values(lenient), ('yes', 'no', 'unknown'))
        self.assertIsNone(sc.validate_step(lenient, {'sample': {'value': 'unknown'}}))

        strict = next(s for s in sc.get('sapar_payment_required')['steps']
                      if s['key'] == 'trips_in_park')
        self.assertEqual(sc.yesno_values(strict), ('yes', 'no'))
        self.assertIsNotNone(sc.validate_step(strict, {'trips_in_park': 'unknown'}))

    def test_driver_who_does_not_remember_is_not_forced_to_guess(self):
        """То же требование, но в новой форме: §4 инструкции #230 держится на
        том, что водитель УТВЕРЖДАЕТ, а «не помню» — не утверждение.

        Раньше это был третий ответ «да/нет», теперь — вариант в списке причин.
        Заставлять оператора выбрать за водителя один из четырёх точных ответов
        нельзя: в супервайзерскую проверку ушли бы выдуманные данные.
        """
        step = next(s for s in sc.get('sapar_docs_missing')['steps']
                    if s['key'] == 'provider_choice')
        self.assertIn(sc.PROVIDER_UNKNOWN, step['options'])
        result = verdict('sapar_docs_missing',
                         full('sapar_docs_missing', provider_choice=sc.PROVIDER_UNKNOWN))
        self.assertEqual(result['outcome'], sc.CLOSE)
        self.assertNotEqual(result['outcome'], sc.ESCALATE)

    def test_unknown_reaches_the_group_as_a_word(self):
        """В группе должно быть видно «неизвестно», а не код и не «нет»."""
        step = sc.step('sample', 'Пример', sc.YESNO, allow_unknown=True)
        self.assertEqual(sc.format_answer(step, {'sample': 'unknown'}), 'неизвестно')
        self.assertEqual(sc.format_answer(step, {'sample': 'no'}), 'нет')

    def test_device_and_browser_carry_examples(self):
        """Задача #174: без образца операторы пишут «телефон» и «браузер».

        Вопрос повторяется в двух тематиках, поэтому он объявлен один раз:
        разъехавшиеся примеры к одному и тому же вопросу — это две разные
        инструкции оператору.
        """
        for key in ('sapar_sign_error', 'sapar_service_error'):
            steps = {s['key']: s for s in sc.get(key)['steps']}
            self.assertIs(steps['device'], sc.STEP_DEVICE, key)
            self.assertIs(steps['browser'], sc.STEP_BROWSER, key)
        self.assertIn('iPhone 15, iOS 18', sc.STEP_DEVICE['placeholder'])
        self.assertIn('Windows 11', sc.STEP_DEVICE['placeholder'])
        self.assertIn('Samsung Galaxy S24, Android 15', sc.STEP_DEVICE['hint'])
        self.assertIn('Google Chrome', sc.STEP_BROWSER['placeholder'])
        self.assertIn('Яндекс Браузер', sc.STEP_BROWSER['placeholder'])

    def test_catalog_carries_everything_the_wizard_draws(self):
        """Интерфейс рисует поля по каталогу — не доехало туда, значит нет его."""
        catalog = {item['key']: item for item in sc.public_catalog()}
        steps = {s['key']: s for s in catalog['sapar_sign_error']['steps']}
        self.assertTrue(steps['device'].get('placeholder'))

        docs = catalog['sapar_docs_missing']
        # Тематика, в которую ведёт только проверка, помечена — иначе мастер
        # покажет её в списке выбора.
        self.assertTrue(docs['entry_only'])
        self.assertEqual(docs['final_outcome'], sc.ESCALATE)
        provider = {s['key']: s for s in docs['steps']}['provider_choice']
        self.assertIn(sc.PROVIDER_UNKNOWN, provider['options'])
        # Консультация показывается на экране исхода — без неё он пустой.
        consultation = next(r for r in docs['rules']
                            if r['when'][1] == sc.PROVIDER_NOT_CHOSEN)
        self.assertEqual(consultation['script'], sc.PROVIDER_SCRIPT)

    def test_iin_takes_only_plain_digits(self):
        """ИИН стал ключом поиска: «цифра» не из [0-9] сделала бы обращение ненаходимым."""
        step = sc.STEP_IIN
        self.assertIsNone(sc.validate_step(step, {'iin': '060606202020'}))
        self.assertIsNotNone(sc.validate_step(step, {'iin': '٠٦٠٦٠٦٢٠٢٠٢٠'}))

    def test_park_and_city_are_asked_separately(self):
        """Просьба СЗоВ 19.08.2026: одно поле «Парк или регион» — два вопроса.

        Свободное поле делало двойную работу, и на проде в нём лежит ровно то,
        что из этого выходит: «iTaxi, Алматы», «ай», «ноль». Теперь парк и город
        спрашиваются отдельно и оба — выбором из справочника.
        """
        for key in ('sapar_docs_missing', 'sapar_sign_error', 'sapar_payment_required',
                    'sapar_sign_status', 'sapar_service_error'):
            steps = {s['key']: s for s in sc.get(key)['steps']}
            self.assertIn('park', steps, key)
            self.assertIn('city', steps, key)
            self.assertEqual(steps['park']['kind'], sc.TAXI_PARK, key)
            self.assertEqual(steps['city']['kind'], sc.CITY, key)
            # Один объект на все тематики: формулировка одного вопроса не должна
            # жить в пяти местах.
            self.assertIs(steps['park'], sc.STEP_PARK, key)
            self.assertIs(steps['city'], sc.STEP_CITY, key)

    def test_park_and_city_stand_side_by_side(self):
        """Это одно «где», а не два разных вопроса, и места они занимают столько
        же, сколько занимало прежнее общее поле."""
        self.assertTrue(sc.STEP_PARK.get('half'))
        self.assertTrue(sc.STEP_CITY.get('half'))
        scenario = sc.get('sapar_docs_missing')
        screen = [s['key'] for s in sc.steps_of_group(scenario, 'Водитель и период')]
        self.assertEqual(screen[-2:], ['park', 'city'])

    def test_office_city_comes_from_the_same_catalog(self):
        """У статуса офиса город — это город офиса, поэтому вопрос свой.

        Справочник при этом общий: иначе в одной тематике выбирали бы из списка,
        а в другой писали руками, и «алматы» с «Алматы» снова разошлись бы.
        """
        city = next(s for s in sc.get('office_status')['steps']
                    if s['key'] == 'office_city')
        self.assertEqual(city['kind'], sc.CITY)
        self.assertIsNot(city, sc.STEP_CITY)

    def test_reference_answers_are_required_but_not_checked_against_a_list(self):
        """Строгость осталась ровно такой, какой была у свободного поля.

        Членство в справочнике модуль не проверяет и не может: парки лежат в
        базе, а он чистый. Значение вне списка ничего не открывает — это текст
        для специалиста в группе. А вот пустым его оставить по-прежнему нельзя.
        """
        self.assertEqual(sc.validate_step(sc.STEP_PARK, {}), 'Не заполнено')
        self.assertEqual(sc.validate_step(sc.STEP_CITY, {}), 'Не заполнено')
        self.assertIsNone(sc.validate_step(sc.STEP_PARK, {'park': 'iTaxi'}))
        self.assertIsNone(sc.validate_step(sc.STEP_CITY, {'city': 'Алматы'}))

    def test_park_and_city_reach_the_group_as_written(self):
        answers = full('sapar_docs_missing', park='Qazaq', city='Актау')
        body = sc.render_body('sapar_docs_missing', answers)
        self.assertIn('Таксопарк: Qazaq', body)
        self.assertIn('Город: Актау', body)

    def test_templated_topics_lose_no_question(self):
        """Тематика может собирать сообщение сама — но не терять при этом ответы.

        Шаблон фиксирован, а список вопросов растёт: добавят вопрос и забудут
        вписать его в шаблон — ответ оператора просто не доедет до группы, и
        никто этого не заметит. Поэтому каждый вопрос обязан быть либо в теме,
        либо в теле.

        Спрашивается это только с тематик, которые пишут ТЕЛО сообщения сами:
        там шаблон и есть всё сообщение. Тема своим шаблоном ничего не отменяет
        — под ней по-прежнему печатается перечень ответов, и теряться там
        нечему (так у тематик регионов: своя тема, обычное тело).
        """
        for scenario in sc.SCENARIOS:
            if not scenario.get('body_template'):
                continue
            templates = ' '.join(filter(None, (scenario.get('subject_template'),
                                               scenario.get('body_template'))))
            for item in scenario['steps']:
                if item['kind'] == sc.ATTACHMENT:
                    continue
                self.assertTrue('{%s}' % item['key'] in templates
                                or '{%s!l}' % item['key'] in templates,
                                '%s: вопрос «%s» никуда не попадает'
                                % (scenario['key'], item['key']))

    def test_templates_reference_existing_questions(self):
        """Опечатка в ключе шаблона оставила бы в сообщении «{licence}»."""
        import re as _re
        for scenario in sc.SCENARIOS:
            keys = {item['key'] for item in scenario['steps']}
            for template in (scenario.get('subject_template'),
                             scenario.get('body_template')):
                for name in _re.findall(r'\{([a-z_]+)(?:!l)?\}', template or ''):
                    self.assertIn(name, keys, scenario['key'])

    def test_every_flag_references_an_existing_step(self):
        for scenario in sc.SCENARIOS:
            keys = {step['key'] for step in scenario['steps']}
            for item in scenario.get('flags', []):
                self.assertIn(item['when'][0], keys, scenario['key'])

    def test_switch_targets_exist(self):
        for scenario in sc.SCENARIOS:
            for item in scenario.get('rules', []):
                if item.get('switch_to'):
                    self.assertIsNotNone(sc.get(item['switch_to']),
                                         '%s → %s' % (scenario['key'], item['switch_to']))

    def test_attachment_step_exists_where_it_is_required(self):
        """И наоборот: требуешь вложение — спроси его шагом, иначе мастеру нечего показать."""
        for scenario in sc.SCENARIOS:
            kinds = [step['kind'] for step in scenario['steps']]
            if scenario['attachment'] == sc.ATTACH_NONE:
                self.assertNotIn(sc.ATTACHMENT, kinds, scenario['key'])
            else:
                self.assertIn(sc.ATTACHMENT, kinds, scenario['key'])


class TermoboxTest(unittest.TestCase):
    """ТЗ задачи #189: тематика «Термокороб» для Яндекс Доставки."""

    KEY = 'yandex_termobox'

    def base(self, action=None):
        return {
            'termobox_action': action or sc.TERMOBOX_ISSUE,
            'licence': 'LL190044',
            'city': 'Астана',
        }

    def test_goes_to_the_yandex_group(self):
        self.assertEqual(sc.get(self.KEY)['queue_code'], 'yandex_delivery')

    def test_checklist_matches_the_specification(self):
        """Три пункта, и каждый отмечается отдельно — прямое требование ТЗ."""
        scenario = sc.get(self.KEY)
        self.assertTrue(scenario['checks_each'])
        self.assertEqual(len(scenario['checks']), 3)
        joined = ' '.join(scenario['checks']).lower()
        for word in ('таксопарк', 'google doc', 'депозит', 'норм'):
            self.assertIn(word, joined, word)

    def test_nothing_sends_until_every_check_is_ticked(self):
        for done in ([], [0], [0, 1], [0, 1, 5]):
            result = sc.evaluate(self.KEY, self.base(), has_attachment=False,
                                 checks_done=done)
            self.assertEqual(result['outcome'], sc.INCOMPLETE, done)
            self.assertIn('__checks__', result['missing'], done)
            self.assertIn('из 3', result['missing']['__checks__'], done)

    def test_all_three_ticked_sends(self):
        result = sc.evaluate(self.KEY, self.base(), has_attachment=False,
                             checks_done=[0, 1, 2])
        self.assertEqual(result['outcome'], sc.READY)

    def test_two_categories_and_nothing_else(self):
        action = next(s for s in sc.get(self.KEY)['steps'] if s['key'] == 'termobox_action')
        self.assertEqual(action['kind'], sc.CHOICE)
        self.assertEqual(action['options'], [sc.TERMOBOX_ISSUE, sc.TERMOBOX_REPLACE])

    def test_category_is_asked_before_the_fields(self):
        """ТЗ: чек-лист → категория → поля. Категория своим экраном и первым."""
        screens = sc.all_groups(sc.get(self.KEY))
        self.assertEqual(screens[0], 'Что согласуем')
        self.assertEqual([s['key'] for s in sc.steps_of_group(sc.get(self.KEY), screens[0],
                                                              self.base())],
                         ['termobox_action'])

    def test_fields_of_both_categories(self):
        keys = [s['key'] for s in sc.visible_steps(sc.get(self.KEY), self.base())]
        self.assertEqual(keys, ['termobox_action', 'licence', 'city'])
        replace = [s['key'] for s in sc.visible_steps(sc.get(self.KEY),
                                                      self.base(sc.TERMOBOX_REPLACE))]
        self.assertEqual(replace, ['termobox_action', 'licence', 'city', 'termobox_photo'])

    def test_iin_is_not_asked_here(self):
        """В ТЗ его нет, и постановщик подтвердила: группе хватает ВУ и города.

        Плата известная и осознанная — по ИИН такие обращения не найдутся.
        """
        self.assertNotIn('iin', [s['key'] for s in sc.get(self.KEY)['steps']])

    def test_photo_is_required_only_for_replacement(self):
        """При выдаче показывать нечего, и требовать фото значило бы держать
        оператора на шаге, которого в его случае не существует."""
        issue = sc.evaluate(self.KEY, self.base(sc.TERMOBOX_ISSUE), has_attachment=False,
                            checks_done=[0, 1, 2])
        self.assertEqual(issue['outcome'], sc.READY)

        replace = sc.evaluate(self.KEY, self.base(sc.TERMOBOX_REPLACE), has_attachment=False,
                              checks_done=[0, 1, 2])
        self.assertEqual(replace['outcome'], sc.INCOMPLETE)
        self.assertIn('__attachment__', replace['missing'])
        self.assertIn('термокороб', replace['missing']['__attachment__'])

        with_photo = sc.evaluate(self.KEY, self.base(sc.TERMOBOX_REPLACE), has_attachment=True,
                                 checks_done=[0, 1, 2])
        self.assertEqual(with_photo['outcome'], sc.READY)

    def test_actual_city_explains_itself(self):
        """ТЗ прямо просит подсказку: город не из карточки, а где выполнен заказ."""
        city = next(s for s in sc.get(self.KEY)['steps'] if s['key'] == 'city')
        self.assertEqual(city['kind'], sc.CITY)
        self.assertEqual(city['label'], 'Фактический город')
        self.assertIn('последний заказ', city['hint'])

    def test_every_field_is_mandatory(self):
        for key in ('termobox_action', 'licence', 'city'):
            answers = self.base(sc.TERMOBOX_ISSUE)
            answers.pop(key)
            result = sc.evaluate(self.KEY, answers, has_attachment=False,
                                 checks_done=[0, 1, 2])
            self.assertEqual(result['outcome'], sc.INCOMPLETE, key)

    def test_group_message_is_as_short_as_they_write_it_themselves(self):
        """Возврат по задаче 19.08.2026: «обращение вот в таком коротком формате».

        У представителей Яндекс Доставки заведено «Прошу проверить на выдачу
        термокороба / LL190044 Астана», и перечень «вопрос: ответ» им тут мешает.
        """
        self.assertEqual(sc.render_subject(self.KEY, self.base(sc.TERMOBOX_ISSUE)),
                         'Прошу проверить на выдачу термокороба')
        self.assertEqual(sc.render_subject(self.KEY, self.base(sc.TERMOBOX_REPLACE)),
                         'Прошу проверить на замену термокороба')
        self.assertEqual(sc.render_body(self.KEY, self.base()), 'LL190044 · Астана')

    def test_licence_keeps_its_case(self):
        """Понижать регистр у всего подряд уже пробовали: «LL190044» стало «lL190044»."""
        self.assertIn('LL190044', sc.render_body(self.KEY, self.base()))

    def test_one_ticked_item_does_not_count_as_all(self):
        """confirmed_checks приводит оба вида ответа к одному множеству."""
        scenario = sc.get(self.KEY)
        self.assertEqual(sc.confirmed_checks(scenario, checks_done=[0, 1, 2]), {0, 1, 2})
        self.assertEqual(sc.confirmed_checks(scenario, checks_done=['0', '1']), {0, 1})
        self.assertEqual(sc.confirmed_checks(scenario, checks_done=[0, 9, 'нет']), {0})
        self.assertEqual(sc.confirmed_checks(scenario, checks_confirmed=True), {0, 1, 2})


class ScreensTest(unittest.TestCase):
    """Вопросы разложены по экранам — но ни один не потерялся.

    Группировка появилась после замечания владельца: восемнадцать вопросов по
    одному — это восемнадцать нажатий «Далее» во время разговора с водителем.
    Риск группировки ровно один: вопрос, попавший мимо экранов, исчезает с глаз,
    оставаясь обязательным, — и оператор упирается в кнопку, не понимая почему.
    """

    def test_every_step_belongs_to_a_screen(self):
        for scenario in sc.SCENARIOS:
            for item in scenario['steps']:
                self.assertTrue(item.get('group'), '%s/%s' % (scenario['key'], item['key']))

    def test_no_question_is_lost_or_duplicated(self):
        """Порядок группировка меняет осознанно, а вот состав — обязана сохранить."""
        for scenario in sc.SCENARIOS:
            shown = []
            for group in sc.groups_of(scenario):
                shown += [item['key'] for item in sc.steps_of_group(scenario, group)]
            expected = [item['key'] for item in sc.visible_steps(scenario, {})]
            self.assertEqual(sorted(shown), sorted(expected), scenario['key'])
            self.assertEqual(len(shown), len(set(shown)),
                             '%s: вопрос попал на два экрана' % scenario['key'])

    def test_inside_a_screen_questions_keep_the_specification_order(self):
        """Переставлять вопросы внутри блока ТЗ не просило — и мы не переставляем."""
        for scenario in sc.SCENARIOS:
            order = [item['key'] for item in scenario['steps']]
            for group in sc.groups_of(scenario):
                keys = [item['key'] for item in sc.steps_of_group(scenario, group)]
                self.assertEqual(keys, sorted(keys, key=order.index),
                                 '%s/%s' % (scenario['key'], group))

    def test_screens_follow_one_order_everywhere(self):
        """Разный порядок экранов в тематиках заставлял бы искать, с чего начать."""
        for scenario in sc.SCENARIOS:
            groups = sc.groups_of(scenario)
            positions = [sc.GROUP_ORDER.index(name) for name in groups]
            self.assertEqual(positions, sorted(positions), scenario['key'])

    def test_grouping_actually_shortens_the_walk(self):
        """Смысл правки: экранов должно стать заметно меньше, чем вопросов."""
        for scenario in sc.SCENARIOS:
            if len(scenario['steps']) < 8:
                continue
            self.assertLessEqual(len(sc.groups_of(scenario)), 5, scenario['key'])

    def test_attachment_has_its_own_screen(self):
        """У выбора файла своя механика — мешать его с вопросами незачем."""
        for scenario in sc.SCENARIOS:
            for item in scenario['steps']:
                if item['kind'] == sc.ATTACHMENT:
                    self.assertEqual(item['group'], 'Вложение', scenario['key'])
                else:
                    self.assertNotEqual(item['group'], 'Вложение', scenario['key'])

    def test_catalog_carries_every_screen_the_topic_can_show(self):
        """В каталог уезжают ВСЕ экраны тематики, а не нужные при пустых ответах.

        Интерфейс фильтрует присланный список по реально нужным экранам, поэтому
        экрана, которого в списке нет, он не покажет никогда. У термокороба фото
        просят только при замене — посчитай список по пустым ответам, и экран
        «Вложение» стал бы недостижим, а обращение — неотправляемым.
        """
        for item in sc.public_catalog():
            self.assertTrue(item['groups'], item['key'])
            self.assertEqual(item['groups'], sc.all_groups(sc.get(item['key'])), item['key'])
        termobox = next(i for i in sc.public_catalog() if i['key'] == 'yandex_termobox')
        self.assertIn('Вложение', termobox['groups'])
        self.assertNotIn('Вложение', sc.groups_of(sc.get('yandex_termobox')))


if __name__ == '__main__':
    unittest.main()
