# -*- coding: utf-8 -*-
"""Раздел «Рассылки» (задача #166): права, сборка фильтра, клиент кабинета,
подключение во фронте.

База сюда не поднимается намеренно: driver_mailings.access и .catalog — чистая
логика без flask и database (импорт database открывает пул к боевой базе).
Клиент проверяется на подменённом транспорте, как в tests/test_fleet_edm.py.
"""

import json
import os
import re
import sys
import threading
import time
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from driver_mailings import access, catalog  # noqa: E402
from driver_mailings.client import MailingRefused, MailingsClient  # noqa: E402
from driver_mailings.schema import DRIVER_MAILINGS_SCHEMA_SQL  # noqa: E402
from fleet_edm.client import FleetError  # noqa: E402

APP_JSX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'src', 'App.jsx')
VIEW_JSX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'src', 'components', 'driver_mailings', 'DriverMailingsView.jsx')
PARK = 'fd0a19dbced14faa928e9f38f76dc09f'


class AccessTests(unittest.TestCase):
    """Периметр назван поимённо, а не выведен из должности.

    Проверяем именно то, чем этот раздел отличается от соседей: роль admin сама
    по себе сюда НЕ пускает. Ошибка в эту сторону не видна на глаз — раздел
    просто откроется лишним людям, и узнают об этом по разосланному сообщению.
    """

    def test_super_admin_passes(self):
        self.assertTrue(access.can_view_section({'id': 7, 'role': 'super_admin'}))

    def test_named_person_passes(self):
        self.assertTrue(access.can_view_section({'id': 476, 'role': 'admin'}))

    def test_plain_admin_is_refused(self):
        self.assertFalse(access.can_view_section({'id': 10, 'role': 'admin'}))

    def test_supervisor_and_operator_refused(self):
        for role in ('sv', 'supervisor', 'trainer', 'operator', 'trainee',
                     'hr_manager', 'accounting_manager', 'marketing_manager'):
            self.assertFalse(access.can_view_section({'id': 11, 'role': role}), role)

    def test_department_head_of_szov_is_refused_without_being_named(self):
        # У «Провайдера ЭДО» глава СЗоВ проходит. Здесь — нет: периметр другой.
        self.assertFalse(access.can_view_section({
            'id': 12, 'role': 'admin', 'is_department_head': True,
            'headed_department_code': 'szov',
        }))

    def test_named_person_keeps_access_after_becoming_head(self):
        # Право именное, поэтому назначение главой его не отбирает — иначе
        # человек потерял бы раздел из-за кадрового решения, к рассылкам
        # отношения не имеющего.
        self.assertTrue(access.can_view_section({
            'id': 476, 'role': 'admin', 'is_department_head': True,
            'headed_department_code': 'szov',
        }))

    def test_id_as_string_still_passes(self):
        # Из JSON-контекста id приходит строкой; сравнение строки с числом молча
        # закрыло бы раздел человеку, которому он открыт.
        self.assertTrue(access.can_view_section({'id': '476', 'role': 'admin'}))

    def test_camel_case_fields_are_read(self):
        self.assertTrue(access.can_view_section({'userId': 476, 'role': 'admin'}))

    def test_empty_context_is_refused(self):
        for ctx in (None, {}, {'id': None, 'role': None}, {'role': ''}):
            self.assertFalse(access.can_view_section(ctx), ctx)

    def test_only_super_admin_connects_the_cabinet_account(self):
        # Куки — вход в кабинет с правом писать водителям во всех парках.
        self.assertTrue(access.can_manage_session({'id': 1, 'role': 'super_admin'}))
        self.assertFalse(access.can_manage_session({'id': 476, 'role': 'admin'}))
        self.assertFalse(access.can_manage_session({'id': 2, 'role': 'admin'}))
        self.assertFalse(access.can_manage_session(None))

    def test_send_and_templates_match_view(self):
        allowed = {'id': 476, 'role': 'admin'}
        denied = {'id': 10, 'role': 'admin'}
        self.assertTrue(access.can_send(allowed))
        self.assertTrue(access.can_manage_templates(allowed))
        self.assertFalse(access.can_send(denied))
        self.assertFalse(access.can_manage_templates(denied))

    def test_capabilities_carry_every_key_the_front_reads(self):
        caps = access.capabilities({'id': 476, 'role': 'admin'})
        self.assertEqual(set(caps), {'can_view', 'can_send', 'can_manage_templates', 'requires_qr'})
        self.assertTrue(caps['can_view'] and caps['can_send'] and caps['can_manage_templates'])
        # Админу подтверждать доступ не у кого.
        self.assertFalse(caps['requires_qr'])
        self.assertFalse(any(access.capabilities({'id': 1, 'role': 'sv'}).values()))


class SensitiveQrAccessTests(unittest.TestCase):
    """Рядовой открывает раздел после QR-подтверждения сессии (владелец,
    25.09.2026). Ключ общий с «Вики» и «Посылками»."""

    def test_rank_and_file_needs_qr(self):
        # 523 — сотрудник ООЗ с ролью оператора (задача #359).
        self.assertTrue(access.requires_sensitive_qr({'id': 523, 'role': 'operator'}))
        self.assertTrue(access.capabilities({'id': 523, 'role': 'operator'})['requires_qr'])

    def test_super_admin_and_department_head_do_not(self):
        self.assertFalse(access.requires_sensitive_qr({'id': 1, 'role': 'super_admin'}))
        # Глава ООЗ сама подтверждает своих сотрудников — ей подтверждать не у кого.
        for head in (
            {'id': 476, 'role': 'admin', 'is_department_head': True},
            {'id': 476, 'role': 'admin', 'headed_department_code': 'request_processing_department'},
            {'id': 99, 'role': 'operator', 'is_department_head': True},
        ):
            self.assertFalse(access.requires_sensitive_qr(head), head)
        self.assertFalse(access.requires_sensitive_qr({'id': 476, 'role': 'admin'}))

    def test_role_list_is_the_portal_one(self):
        # Своей копии списка нет: иначе замок на экране (SENSITIVE_QR_GATED_ROLES
        # в App.jsx) и отказ сервера спрашивали бы разных людей.
        from wiki.access import QR_GATED_ROLES
        self.assertIs(access.QR_GATED_ROLES, QR_GATED_ROLES)
        for role in QR_GATED_ROLES:
            self.assertTrue(access.requires_sensitive_qr({'id': 5, 'role': role}), role)

    def test_access_module_still_imports_without_flask_and_database(self):
        import subprocess
        import sys
        code = ("import sys; import driver_mailings.access; "
                "print(any(m in sys.modules for m in ('flask', 'database', 'psycopg2')))")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True,
                             text=True, check=True).stdout.strip()
        self.assertEqual(out, 'False')


class FiltersTests(unittest.TestCase):
    """Сборка фильтра — самое опасное место раздела.

    Кабинет на НЕИЗВЕСТНЫЙ ключ отвечает не ошибкой, а полным охватом парка
    (проверено 07.09.2026: {"blah":"nope"} дал те же 1144 человека, что и пустой
    фильтр). Значит любая дыра в белом списке — это не 400, а рассылка всем.
    """

    def test_unknown_keys_are_dropped(self):
        self.assertEqual(catalog.normalize_filters({'blah': 'nope', 'park_id': PARK}), {})

    def test_known_keys_survive(self):
        got = catalog.normalize_filters({
            'segment': 'active', 'subsegments': ['more_100'],
            'group': 'has_violation_warning',
            'profession_ids': ['taxi/driver'], 'car_categories': ['econom'],
        })
        self.assertEqual(got['segment'], 'active')
        self.assertEqual(got['subsegments'], ['more_100'])
        self.assertEqual(got['group'], 'has_violation_warning')

    def test_subsegments_without_segment_are_dropped(self):
        # Кабинет на такой фильтр отдаёт 0 получателей молча, и человек решил бы,
        # что подходящих водителей нет.
        self.assertEqual(catalog.normalize_filters({'subsegments': ['more_100']}), {})

    def test_city_is_dropped_outside_active_and_churn(self):
        # Повторяем поведение кабинета: город он показывает только у «Активных» и
        # «Оттока» — у остальных города работы ещё нет.
        self.assertNotIn('city_ids', catalog.normalize_filters({'city_ids': ['Алматы']}))
        self.assertNotIn('city_ids', catalog.normalize_filters(
            {'segment': 'candidate', 'city_ids': ['Алматы']}))
        for segment in catalog.SEGMENTS_WITH_CITY:
            self.assertEqual(
                catalog.normalize_filters({'segment': segment, 'city_ids': ['Алматы']})['city_ids'],
                ['Алматы'], segment)

    def test_on_order_status_is_renamed(self):
        # С in_order кабинет отвечает 400 — его собственный интерфейс шлёт on_order.
        got = catalog.normalize_filters({'contractor_statuses': ['in_order']})
        self.assertEqual(got['contractor_statuses'], ['on_order'])

    def test_unknown_status_is_dropped(self):
        self.assertEqual(catalog.normalize_filters({'contractor_statuses': ['in_order_free']}), {})

    def test_unknown_group_is_dropped(self):
        self.assertEqual(catalog.normalize_filters({'group': 'has_secret_flag'}), {})

    def test_unknown_segment_is_dropped(self):
        self.assertEqual(catalog.normalize_filters({'segment': 'whatever'}), {})

    def test_empty_lists_do_not_reach_the_cabinet(self):
        self.assertEqual(catalog.normalize_filters({
            'profession_ids': [], 'car_categories': [], 'car_amenities': [],
        }), {})

    def test_none_and_garbage_are_survivable(self):
        for value in (None, [], 'строка', 42):
            self.assertEqual(catalog.normalize_filters(value), {})


class ReferenceCleanupTests(unittest.TestCase):
    """Справочники кабинета чистятся ровно так же, как в самом кабинете."""

    def test_statuses_match_the_cabinet(self):
        raw = [{'id': 'offline', 'name': 'Офлайн'}, {'id': 'busy', 'name': 'Занят'},
               {'id': 'free', 'name': 'Свободный'},
               {'id': 'in_order_free', 'name': 'На заказе, свободный'},
               {'id': 'in_order_busy', 'name': 'На заказе, занят'},
               {'id': 'in_order', 'name': 'На заказе'},
               {'id': 'online', 'name': 'На линии'}]
        got = [item['id'] for item in catalog.clean_statuses(raw)]
        # Ровно четыре значения из ТЗ: Офлайн, Занят, На заказе, На линии.
        self.assertEqual(sorted(got), sorted(['offline', 'busy', 'on_order', 'online']))

    def test_pool_is_not_a_tariff(self):
        raw = [{'id': 'econom', 'name': 'Эконом'}, {'id': 'pool', 'name': 'Комбо'},
               {'id': 'none', 'name': '—'}]
        self.assertEqual([item['id'] for item in catalog.clean_categories(raw)], ['econom'])

    def test_groups_keep_cabinet_order_and_drop_untranslated(self):
        available = ['platform_newbie', 'has_violation_warning', 'want_to_change_car',
                     'has_contract_issue_depriority', 'no_edm_provider']
        got = catalog.groups_for(available)
        # want_to_change_car кабинет не переводит и в интерфейсе не показывает.
        self.assertNotIn('want_to_change_car', [item['key'] for item in got])
        # Сначала «Ограничения», потом «Предупреждения», потом «Возможности» —
        # порядок наш, а не тот, в котором ответил кабинет.
        self.assertEqual([item['category'] for item in got],
                         ['has_restrictions', 'has_warnings', 'has_warnings',
                          'has_opportunities'])
        self.assertTrue(all(item['label'] for item in got))

    def test_group_labels_cover_every_declared_key(self):
        # Ключ без подписи невидим в интерфейсе; проверяем, что список групп и
        # словарь подписей не разъехались — кроме двух, которых нет и у кабинета.
        untranslated = {'want_to_change_car', 'has_fuec_issues'}
        for keys in catalog.GROUPS_BY_CATEGORY.values():
            for key in keys:
                if key in untranslated:
                    continue
                self.assertIn(key, catalog.GROUP_LABELS, key)


class SeparatorTests(unittest.TestCase):
    """Разделитель двуязычной рассылки живёт в двух местах и обязан совпадать.

    Склейку делает сервер, а «Повторить» разбирает её обратно фронт. Разойдись
    значения — и повтор перестал бы делить текст на языки, свалив обе части в
    русское поле. Заметили бы это не сразу: рассылка ушла бы криво.
    """

    def test_separator_is_three_underscores(self):
        # Только подчёркивания приложение Pro рисует чертой; «─» приезжают
        # водителю обычным текстом.
        self.assertEqual(catalog.BILINGUAL_SEPARATOR, '\n\n___\n\n')

    def test_front_and_back_agree_byte_for_byte(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'src', 'components', 'driver_mailings', 'mailingText.js')
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        found = re.search(r"BILINGUAL_SEPARATOR = '([^']*)'", source)
        self.assertIsNotNone(found, 'на фронте не нашёлся BILINGUAL_SEPARATOR')
        front = found.group(1).encode().decode('unicode_escape')
        self.assertEqual(front, catalog.BILINGUAL_SEPARATOR)


class ErrorMessageTests(unittest.TestCase):
    def test_known_codes_are_translated(self):
        self.assertIn('получател', catalog.error_message('limit_drivers', 500).lower())
        self.assertTrue(catalog.error_message('limit_time'))
        self.assertTrue(catalog.error_message('mailing_not_allowed'))

    def test_unknown_code_still_says_something_and_shows_the_code(self):
        # Незнакомый код не должен превращаться в «что-то пошло не так»: сам код
        # — единственная зацепка, по которой потом чинят.
        message = catalog.error_message('какая_то_новая')
        self.assertIn(catalog.MAILING_ERROR_DEFAULT, message)
        self.assertIn('какая_то_новая', message)
        self.assertEqual(catalog.error_message(''), catalog.MAILING_ERROR_DEFAULT)


class _Response:
    def __init__(self, status_code, payload=None, raw=None):
        self.status_code = status_code
        self.headers = {'content-type': 'application/json'}
        if raw is not None:
            self.content = raw
        else:
            self.content = b'' if payload is None else json.dumps(payload).encode('utf-8')
        self.text = self.content.decode('utf-8', 'replace')
        self._payload = payload

    def json(self):
        if not self.content:
            raise ValueError('пусто')
        return self._payload


class _Session:
    """Подменённый транспорт: отдаёт заготовленные ответы и запоминает вызовы."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.cookies = type('J', (), {'update': lambda self, value: None})()

    def request(self, method, url, headers=None, data=None, timeout=None, params=None):
        self.calls.append({'method': method, 'url': url, 'headers': headers,
                           'data': data, 'params': params})
        return self.responses.pop(0)


def _client(responses):
    return MailingsClient({'Session_id': 'x'}, 'UA', session=_Session(responses))


class ClientTests(unittest.TestCase):
    def test_no_permission_park_is_not_a_failure(self):
        # 85 диспетчерских из 90 отвечают 403 — это нормальный ответ, а не сбой.
        client = _client([_Response(403, {'code': 'no_permissions'})])
        self.assertIsNone(client.mailing_limits(PARK))

    def test_limits_are_flattened_for_the_cache(self):
        payload = {'pro': {'status': 'ok', 'restriction': {
            'is_enabled': True, 'max_message_length': 1500, 'max_title_length': 120,
            'delete_limit': {'seconds': 300}, 'time_limit': {'day': 30}}}}
        got = MailingsClient.pro_limits(payload)
        self.assertEqual(got['max_title'], 120)
        self.assertEqual(got['max_message'], 1500)
        self.assertEqual(got['revoke_seconds'], 300)
        self.assertEqual(got['per_day'], 30)
        self.assertTrue(got['is_enabled'])

    def test_limits_of_a_forbidden_park_do_not_explode(self):
        got = MailingsClient.pro_limits(None)
        self.assertFalse(got['is_enabled'])

    def test_send_success_is_an_empty_204(self):
        session = _Session([_Response(204)])
        client = MailingsClient({'Session_id': 'x'}, 'UA', session=session)
        client.send_mailing(PARK, title='Тема', message='Текст',
                            filters={'segment': 'active'}, idempotency_token='tok-1')
        call = session.calls[0]
        self.assertEqual(call['method'], 'POST')
        self.assertEqual(call['headers']['x-idempotency-token'], 'tok-1')
        body = json.loads(call['data'])
        self.assertEqual(body['type'], 'pro')
        self.assertEqual(body['recipients']['filters'], {'segment': 'active'})

    def test_send_normalizes_filters_a_second_time(self):
        # Роут уже нормализовал фильтр, но цена забытой нормализации — не 400,
        # а рассылка всем, поэтому клиент пересобирает его сам.
        session = _Session([_Response(204)])
        client = MailingsClient({'Session_id': 'x'}, 'UA', session=session)
        client.send_mailing(PARK, title='Тема', message='Текст',
                            filters={'blah': 'nope'}, idempotency_token='tok-2')
        self.assertEqual(json.loads(session.calls[0]['data'])['recipients']['filters'], {})

    def test_refusal_carries_the_cabinet_code(self):
        client = _client([_Response(400, {'code': 'limit_time'})])
        with self.assertRaises(MailingRefused) as caught:
            client.send_mailing(PARK, title='Т', message='М', filters={},
                                idempotency_token='tok-3')
        self.assertEqual(caught.exception.code, 'limit_time')

    def test_revoke_sends_id_as_query_parameter(self):
        session = _Session([_Response(204)])
        client = MailingsClient({'Session_id': 'x'}, 'UA', session=session)
        client.revoke_mailing(PARK, 'abc-123')
        self.assertEqual(session.calls[0]['method'], 'DELETE')
        self.assertEqual(session.calls[0]['params'], {'id': 'abc-123'})
        self.assertIsNone(session.calls[0]['data'])

    def test_recipients_count_reads_the_number(self):
        client = _client([_Response(200, {'count': 1144})])
        self.assertEqual(client.recipients_count(PARK, {}), 1144)


class ResolveMailingIdTests(unittest.TestCase):
    """Кабинет не возвращает id отправленной рассылки — его ищут в журнале.

    Ошибка здесь дороже, чем «не нашли»: чужой id означает, что кнопка
    «Отозвать» ударит по чужой рассылке.
    """

    LIST = {'mailings': [
        {'id': 'new', 'preview': 'Акция', 'sent_at': '2026-09-07T10:00:05+00:00'},
        {'id': 'old', 'preview': 'Акция', 'sent_at': '2026-08-01T09:00:00+00:00'},
        {'id': 'other', 'preview': 'Другая тема', 'sent_at': '2026-09-07T10:00:07+00:00'},
    ]}

    # Так выглядит журнал СРАЗУ после отправки: у только что созданной рассылки
    # поля sent_at НЕТ вовсе — кабинет проставит его позже, когда разошлёт.
    # Снято с живой отправки 07.09.2026; из-за этого первая версия не находила
    # id НИКОГДА, и каждая рассылка оседала в журнале без связи с кабинетом.
    JUST_SENT = {'mailings': [
        {'id': 'brand-new', 'preview': 'Акция', 'type': 'pro', 'is_legacy': False},
        {'id': 'old', 'preview': 'Акция', 'sent_at': '2026-08-01T09:00:00+00:00',
         'status': 'sent'},
    ]}

    def test_fresh_mailing_without_sent_at_is_found_by_snapshot(self):
        client = _client([_Response(200, self.JUST_SENT)])
        got = client.resolve_mailing_id(PARK, 'Акция', before_ids={'old'})
        self.assertEqual(got, 'brand-new')

    def test_snapshot_never_takes_a_mailing_that_was_already_there(self):
        # Своей записи в журнале ещё нет — брать старую с тем же заголовком
        # нельзя: «Отозвать» ударил бы по чужой рассылке.
        client = _client([_Response(200, {'mailings': [self.JUST_SENT['mailings'][1]]})])
        self.assertIsNone(client.resolve_mailing_id(PARK, 'Акция', before_ids={'old'}))

    def test_without_snapshot_a_mailing_without_time_is_no_longer_dropped(self):
        # Запасной путь: снимок снять не удалось. Раньше запись без времени
        # отбрасывалась — то есть отбрасывалась ровно свежая рассылка.
        client = _client([_Response(200, {'mailings': [self.JUST_SENT['mailings'][0]]})])
        got = client.resolve_mailing_id(PARK, 'Акция', since_iso='2026-09-07T09:59:30+00:00')
        self.assertEqual(got, 'brand-new')

    def test_snapshot_reads_ids_of_the_first_page(self):
        client = _client([_Response(200, self.LIST)])
        self.assertEqual(client.journal_ids(PARK), {'new', 'old', 'other'})

    def test_broken_snapshot_is_empty_and_does_not_break_the_send(self):
        client = _client([_Response(500, {'error': 'oops'}),
                          _Response(500, {'error': 'oops'}),
                          _Response(500, {'error': 'oops'})])
        self.assertEqual(client.journal_ids(PARK), set())

    def test_matches_by_title_and_time(self):
        client = _client([_Response(200, self.LIST)])
        got = client.resolve_mailing_id(PARK, 'Акция', '2026-09-07T09:59:30+00:00')
        self.assertEqual(got, 'new')

    def test_old_mailing_with_the_same_title_is_not_taken(self):
        client = _client([_Response(200, {'mailings': [self.LIST['mailings'][1]]})])
        self.assertIsNone(
            client.resolve_mailing_id(PARK, 'Акция', '2026-09-07T09:59:30+00:00'))

    def test_already_claimed_id_is_skipped(self):
        client = _client([_Response(200, self.LIST)])
        got = client.resolve_mailing_id(PARK, 'Акция', '2026-09-07T09:59:30+00:00',
                                        skip_ids={'new'})
        self.assertIsNone(got)

    def test_other_title_is_never_taken(self):
        client = _client([_Response(200, self.LIST)])
        self.assertIsNone(
            client.resolve_mailing_id(PARK, 'Нет такой', '2026-09-07T09:59:30+00:00'))

    def test_broken_journal_does_not_break_the_send(self):
        # Рассылка уже ушла; неудачный поиск id не имеет права её отменить.
        client = _client([_Response(500, {'error': 'oops'}),
                          _Response(500, {'error': 'oops'}),
                          _Response(500, {'error': 'oops'})])
        self.assertIsNone(
            client.resolve_mailing_id(PARK, 'Акция', '2026-09-07T09:59:30+00:00'))


class _SlowSession:
    """Транспорт, который отвечает не сразу и помнит, сколько запросов было в
    полёте одновременно, — чтобы увидеть общий предел клиента."""

    def __init__(self, delay=0.05):
        self.delay = delay
        self.lock = threading.Lock()
        self.now = 0
        self.peak = 0
        self.cookies = type('J', (), {'update': lambda self, value: None})()

    def request(self, method, url, headers=None, data=None, timeout=None, params=None):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
        time.sleep(self.delay)
        with self.lock:
            self.now -= 1
        return _Response(200, {'count': 7})


class GateTests(unittest.TestCase):
    """Общий предел запросов в полёте — на клиент, а не на запрос портала.

    28.09.2026 его не было: пять подсчётов охвата шли разом по восемь потоков
    каждый, и кабинет отбивался 429 больше двадцати тысяч раз за 45 минут."""

    def test_never_more_in_flight_than_the_width(self):
        from concurrent.futures import ThreadPoolExecutor
        session = _SlowSession()
        client = MailingsClient({'Session_id': 'x'}, 'UA', concurrency=3, session=session)
        # Двенадцать потоков — как два запроса портала по шесть.
        with ThreadPoolExecutor(max_workers=12) as pool:
            counts = list(pool.map(lambda _i: client.recipients_count(PARK, {}), range(12)))
        self.assertEqual(counts, [7] * 12)
        self.assertLessEqual(session.peak, 3)
        self.assertGreaterEqual(session.peak, 2)    # но и не по одному

    def test_default_width_is_the_measured_one(self):
        from driver_mailings import client as client_module
        client = MailingsClient({'Session_id': 'x'}, 'UA')
        self.assertEqual(client.concurrency, client_module.CONCURRENCY)
        self.assertEqual(client_module.CONCURRENCY, 8)

    def test_slot_taken_twice_by_one_thread_is_one_slot(self):
        # Маршрут держит место вокруг вызова, а _request берёт его ещё раз.
        # При ширине 1 второе взятие ждало бы само себя вечно.
        client = MailingsClient({'Session_id': 'x'}, 'UA', concurrency=1,
                                session=_SlowSession(delay=0))
        done = []

        def nested():
            with client.slot():
                done.append(client.recipients_count(PARK, {}))

        worker = threading.Thread(target=nested, daemon=True)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), 'повторное взятие места зависло')
        self.assertEqual(done, [7])

    def test_throttle_narrows_and_quiet_brings_the_width_back(self):
        from driver_mailings import client as client_module
        client = MailingsClient({'Session_id': 'x'}, 'UA')
        client._note_throttled()
        self.assertEqual(client.concurrency, 7)
        with client.slot():
            pass
        self.assertEqual(client.concurrency, 7)     # сразу после отказа — узко
        client._throttled_at = time.time() - client_module.QUIET_RESET_SECONDS - 1
        with client.slot():
            pass
        self.assertEqual(client.concurrency, 8)

    def test_transport_log_names_the_section(self):
        # Отказы рассылок раньше ложились в лог как «Провайдер ЭДО».
        import requests

        class _Down(_Session):
            def request(self, *args, **kwargs):
                raise requests.ConnectionError('нет сети')

        client = MailingsClient({'Session_id': 'x'}, 'UA', max_delay=0, session=_Down([]))
        with self.assertLogs(level='WARNING') as logs:
            with self.assertRaises(FleetError):
                client.recipients_count(PARK, {})
        self.assertTrue(all(line.split(':', 2)[2].startswith('Рассылки:')
                            for line in logs.output), logs.output)
        from fleet_edm.client import FleetClient
        self.assertEqual(FleetClient.LOG_LABEL, 'Провайдер ЭДО')


class FlightCacheTests(unittest.TestCase):
    """Один ключ — один запрос в кабинет, сколько бы потоков ни спрашивали."""

    def setUp(self):
        from driver_mailings import memo
        self.memo = memo

    def test_simultaneous_askers_share_one_computation(self):
        cache = self.memo.FlightCache(60)
        release = threading.Event()
        calls = []

        def compute():
            calls.append(1)
            release.wait(5)
            return 42, True

        results = []
        threads = [threading.Thread(target=lambda: results.append(cache.get('k', compute)))
                   for _ in range(6)]
        for thread in threads:
            thread.start()
        time.sleep(0.2)             # пусть все дойдут до ожидания
        release.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(results, [42] * 6)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.peek('k'), (True, 42))

    def test_error_reaches_the_waiters_and_is_not_remembered(self):
        cache = self.memo.FlightCache(60)
        release = threading.Event()
        calls = []

        def failing():
            calls.append(1)
            release.wait(5)
            raise FleetError('429 Too Many Requests')

        errors = []

        def ask():
            try:
                cache.get('k', failing)
            except FleetError as error:
                errors.append(str(error))

        threads = [threading.Thread(target=ask) for _ in range(4)]
        for thread in threads:
            thread.start()
        time.sleep(0.2)
        release.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(len(errors), 4)
        self.assertEqual(len(calls), 1)             # отказ не удвоил запросы
        self.assertEqual(cache.peek('k'), (False, None))
        self.assertEqual(cache.get('k', lambda: (5, True)), 5)

    def test_abandoned_owner_hands_the_key_to_a_waiter(self):
        cache = self.memo.FlightCache(60)
        entered = threading.Event()
        release = threading.Event()
        outcome = {}

        def given_up():
            entered.set()
            release.wait(5)
            raise self.memo.Abandoned()

        def owner():
            try:
                cache.get('k', given_up)
            except self.memo.Abandoned:
                outcome['owner'] = 'abandoned'

        first = threading.Thread(target=owner)
        first.start()
        entered.wait(5)
        second = threading.Thread(target=lambda: outcome.update(
            waiter=cache.get('k', lambda: (9, True))))
        second.start()
        time.sleep(0.2)
        release.set()
        first.join(5)
        second.join(5)
        self.assertEqual(outcome, {'owner': 'abandoned', 'waiter': 9})
        self.assertEqual(cache.peek('k'), (True, 9))

    def test_incomplete_value_is_returned_but_not_kept(self):
        cache = self.memo.FlightCache(60)
        self.assertEqual(cache.get('k', lambda: ('часть', False)), 'часть')
        self.assertEqual(cache.peek('k'), (False, None))

    def test_value_lives_ttl_seconds(self):
        now = [100.0]
        cache = self.memo.FlightCache(10, clock=lambda: now[0])
        cache.get('k', lambda: (1, True))
        now[0] = 109.0
        self.assertEqual(cache.peek('k'), (True, 1))
        now[0] = 110.5
        self.assertEqual(cache.peek('k'), (False, None))
        self.assertEqual(cache.get('k', lambda: (2, True)), 2)

    def test_answer_counted_before_clear_is_not_kept(self):
        # Смена аккаунта посреди подсчёта: ответ под прежним в кэш не ложится.
        cache = self.memo.FlightCache(60)

        def compute():
            cache.clear()
            return 1, True

        self.assertEqual(cache.get('k', compute), 1)
        self.assertEqual(cache.peek('k'), (False, None))

    def test_cache_does_not_grow_without_bound(self):
        cache = self.memo.FlightCache(60, max_items=10)
        for index in range(50):
            cache.get(index, lambda index=index: (index, True))
        self.assertLessEqual(len(cache), 10)
        self.assertEqual(cache.peek(49), (True, 49))    # свежее не выброшено


class RouteHelpersTests(unittest.TestCase):
    """Помощники маршрутов. Оба закрывают найденные ревью дефекты."""

    def test_timestamps_from_base_and_cabinet_are_comparable(self):
        # Кабинет отдаёт строку ISO, база — datetime. Сравнение их строковых
        # видов не срабатывало никогда, и ограничитель глубины при доборе
        # прочтений выбирал все страницы вместо одной.
        from datetime import datetime, timezone

        from driver_mailings import routes

        cabinet = routes._parse_stamp('2026-09-04T12:57:13.770911+00:00')
        ours = routes._parse_stamp(datetime(2026, 9, 5, tzinfo=timezone.utc))
        self.assertIsNotNone(cabinet)
        self.assertIsNotNone(ours)
        self.assertLess(cabinet, ours)
        # Наивное время считаем UTC — иначе сравнение с зоной падает.
        self.assertIsNotNone(routes._parse_stamp('2026-09-04T12:57:13'))
        self.assertIsNone(routes._parse_stamp(None))
        self.assertIsNone(routes._parse_stamp(''))
        self.assertIsNone(routes._parse_stamp('позавчера'))

    def test_sent_total_counts_only_what_actually_went_out(self):
        # Если в одном парке кабинет отказал по суточному лимиту, его водители
        # сообщения не получили — класть их в «ушло» нельзя: по этому числу
        # человек решает, отправлять ли ещё раз.
        from driver_mailings import routes

        detail = {'targets': [
            {'status': 'sent', 'recipients_estimate': 812},
            {'status': 'failed', 'recipients_estimate': 96},
            {'status': 'revoked', 'recipients_estimate': 50},
            {'status': 'sent', 'recipients_estimate': None},
        ]}
        self.assertEqual(routes._sent_total(detail), 812)
        self.assertEqual(routes._sent_total({}), 0)
        self.assertEqual(routes._sent_total(None), 0)


class SchemaTests(unittest.TestCase):
    def test_statuses_in_ddl_match_the_code(self):
        for status in ('sending', 'sent', 'partial', 'failed', 'revoked'):
            self.assertIn("'{}'".format(status), DRIVER_MAILINGS_SCHEMA_SQL)
        for status in ('pending', 'sent', 'failed', 'revoked'):
            self.assertIn("'{}'".format(status), DRIVER_MAILINGS_SCHEMA_SQL)

    def test_everything_is_idempotent(self):
        for statement in DRIVER_MAILINGS_SCHEMA_SQL.split(';'):
            head = statement.strip().upper()
            if head.startswith('CREATE TABLE'):
                self.assertIn('IF NOT EXISTS', head)
            if head.startswith('CREATE INDEX') or head.startswith('CREATE UNIQUE INDEX'):
                self.assertIn('IF NOT EXISTS', head)

    def test_idempotency_key_is_unique(self):
        # Единственная защита от второй рассылки тем же людям при двойном клике.
        self.assertIn('idempotency_key     TEXT UNIQUE', DRIVER_MAILINGS_SCHEMA_SQL)

    def test_own_session_is_a_single_row(self):
        # Своя сессия аккаунта рассылок (23.09.2026) — одна строка, как у ЭДО:
        # два набора кук в одной таблице означали бы «какими из них ходить?».
        block = DRIVER_MAILINGS_SCHEMA_SQL.split('CREATE TABLE IF NOT EXISTS driver_mailing_session')[1]
        block = block.split(');')[0]
        self.assertIn('CHECK (id = 1)', block)
        self.assertIn('cookies', block)
        # Больше кук нигде в схеме раздела нет.
        self.assertEqual(DRIVER_MAILINGS_SCHEMA_SQL.count('cookies'), 1)

    def test_routes_never_write_the_shared_session(self):
        # В fleet_edm_session смысл «куки ЭДО протухли»; наши ошибки туда не пишем.
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'driver_mailings', 'routes.py')
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        for forbidden in ('fleet_session.save_session', 'fleet_session.mark_session_error',
                          'fleet_session.mark_session_ok'):
            self.assertNotIn(forbidden, source)


class ChooseSessionTests(unittest.TestCase):
    """Какими куками ходить в кабинет: своими, а без своих — общими."""

    def setUp(self):
        from driver_mailings import routes
        self.routes = routes
        self.own = {'cookies': [{'name': 'Session_id', 'value': 'own'}], 'account': 'mailings@x'}
        self.shared = {'cookies': [{'name': 'Session_id', 'value': 'edm'}], 'account': 'edm@x'}

    def test_own_session_wins(self):
        session, source = self.routes.choose_session(self.own, self.shared)
        self.assertIs(session, self.own)
        self.assertEqual(source, self.routes.SESSION_OWN)

    def test_expired_own_session_still_wins(self):
        # Молча откатиться на аккаунт с пятью парками — подмена отправителя.
        own = dict(self.own, last_error='Сессия протухла')
        session, source = self.routes.choose_session(own, self.shared)
        self.assertIs(session, own)
        self.assertEqual(source, self.routes.SESSION_OWN)

    def test_shared_until_own_is_connected(self):
        for own in (None, {}, {'cookies': []}):
            session, source = self.routes.choose_session(own, self.shared)
            self.assertIs(session, self.shared)
            self.assertEqual(source, self.routes.SESSION_SHARED)

    def test_nothing_configured(self):
        self.assertEqual(self.routes.choose_session(None, None), (None, None))
        self.assertEqual(self.routes.choose_session({'cookies': []}, {'cookies': []}),
                         (None, None))


class _FakeDb:
    @contextmanager
    def _get_cursor(self):
        yield object()


class _FakeCabinet:
    """Кабинет для маршрутов: какие парки есть и где разрешена рассылка."""

    parks_list = []
    enabled = set()
    check_error = None
    parks_error = None
    created = []

    def __init__(self, cookies, user_agent=None):
        self.cookies = cookies
        _FakeCabinet.created.append(cookies)

    def check(self):
        if _FakeCabinet.check_error:
            raise _FakeCabinet.check_error
        return {'account': 'mailings@example.com', 'parks_count': len(self.parks_list)}

    def parks(self):
        if _FakeCabinet.parks_error:
            raise _FakeCabinet.parks_error
        return list(self.parks_list)

    def mailing_limits(self, park_id):
        if park_id not in self.enabled:
            return None
        return {'pro': {'status': 'ok', 'restriction': {
            'is_enabled': True, 'max_title_length': 120, 'max_message_length': 1500,
            'time_limit': {'day': 30}, 'delete_limit': {'seconds': 300}}}}

    pro_limits = staticmethod(MailingsClient.pro_limits)

    @contextmanager
    def slot(self):
        # Общий предел запросов проверяется на настоящем клиенте (GateTests).
        yield

    # ── отправка: всё пишется в общий журнал событий по порядку ─────────────
    events = []
    expire_on = None        # ('count' | 'send', park_id) — где «протухнуть»

    def _maybe_expire(self, stage, park_id):
        if _FakeCabinet.expire_on == (stage, park_id):
            from fleet_edm.client import FleetSessionExpired
            raise FleetSessionExpired('протухла')

    def recipients_count(self, park_id, filters):
        _FakeCabinet.events.append('count:' + park_id)
        self._maybe_expire('count', park_id)
        return 100

    def journal_ids(self, park_id):
        _FakeCabinet.events.append('snap:' + park_id)
        return {'old-' + park_id}

    def send_mailing(self, park_id, **kwargs):
        _FakeCabinet.events.append('send:' + park_id)
        self._maybe_expire('send', park_id)

    def resolve_mailing_id(self, park_id, title, since_iso=None, skip_ids=None,
                           before_ids=None):
        _FakeCabinet.events.append('resolve:' + park_id)
        # Снимок, снятый до отправок, обязан дойти до поиска id.
        assert before_ids == {'old-' + park_id}, before_ids
        return 'f-' + park_id


class _RouteHarness(unittest.TestCase):
    """Blueprint раздела на подменённых базе и кабинете."""

    def setUp(self):
        from unittest import mock

        from flask import Flask

        from driver_mailings import queries, routes
        from fleet_edm import queries as fleet_queries

        self.routes = routes
        self.requester = {'id': 1, 'name': 'Админ', 'role': 'super_admin'}
        self.own_row = None
        self.shared_row = {'cookies': [{'name': 'Session_id', 'value': 'edm'}]}
        self.calls = []

        _FakeCabinet.parks_list = [{'id': 'a', 'name': 'Anytime', 'city': 'Алматы'},
                                   {'id': 'b', 'name': 'Jana', 'city': 'Астана'},
                                   {'id': 'c', 'name': 'Tenge', 'city': 'Шымкент'}]
        _FakeCabinet.enabled = {'a', 'b', 'c'}
        _FakeCabinet.check_error = None
        _FakeCabinet.parks_error = None
        _FakeCabinet.created = []
        _FakeCabinet.events = []
        _FakeCabinet.expire_on = None

        def record(name, result=None):
            def inner(*args, **kwargs):
                self.calls.append((name, args[1:], kwargs))
                return result() if callable(result) else result
            return inner

        def db_event(name, result=None):
            # Запись в базу — в тот же журнал, что и кабинет: важен порядок.
            def inner(cursor, *args, **kwargs):
                park = kwargs.get('park_id') or (args[1] if len(args) > 1 else '')
                _FakeCabinet.events.append('db:{}:{}'.format(name, park).rstrip(':'))
                self.calls.append((name, args, kwargs))
                return result
            return inner

        park_rows = [dict(park_id=p['id'], name=p['name'], city=p['city'], is_enabled=True,
                          max_title=120, max_message=1500, revoke_seconds=300, per_day=30)
                     for p in _FakeCabinet.parks_list]

        patches = [
            mock.patch.object(routes, 'MailingsClient', _FakeCabinet),
            mock.patch.object(queries, 'access_context', lambda cursor, uid: self.requester),
            mock.patch.object(queries, 'load_session', lambda cursor: self.own_row),
            mock.patch.object(queries, 'session_status', lambda cursor: (
                {'configured': True, 'account': 'mailings@example.com'}
                if self.own_row else {'configured': False})),
            mock.patch.object(queries, 'save_session', record('save_session')),
            mock.patch.object(queries, 'mark_session_ok', record('mark_session_ok')),
            mock.patch.object(queries, 'mark_session_error', record('mark_session_error')),
            mock.patch.object(queries, 'save_parks_cache', record('save_parks_cache')),
            mock.patch.object(queries, 'clear_parks_cache', record('clear_parks_cache')),
            mock.patch.object(fleet_queries, 'load_session', lambda cursor: self.shared_row),
            mock.patch.object(fleet_queries, 'session_status', lambda cursor: {
                'configured': True, 'account': 'edm@example.com'}),
            mock.patch.object(fleet_queries, 'mark_session_error', record('shared_mark_error')),
            mock.patch.object(fleet_queries, 'save_session', record('shared_save')),
            # отправка
            mock.patch.object(queries, 'parks_cache', lambda cursor: park_rows),
            mock.patch.object(queries, 'parks_cache_age', lambda cursor: 10.0),
            mock.patch.object(queries, 'find_mailing_by_key', lambda cursor, key: None),
            mock.patch.object(queries, 'create_mailing', db_event('create', 77)),
            mock.patch.object(queries, 'add_target', record('add_target')),
            mock.patch.object(queries, 'mark_target_sent', db_event('sent')),
            mock.patch.object(queries, 'mark_target_failed', db_event('failed')),
            mock.patch.object(queries, 'link_target', db_event('link')),
            mock.patch.object(queries, 'claimed_fleet_ids', lambda cursor, park_id, since=None: set()),
            mock.patch.object(queries, 'abandon_pending_targets', db_event('abandon')),
            mock.patch.object(queries, 'finish_mailing', lambda cursor, mailing_id: 'sent'),
            mock.patch.object(queries, 'mailing_detail', lambda cursor, mailing_id: {
                'id': mailing_id, 'status': 'sent', 'targets': []}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        # QR-подтверждение сессии: кого спросили и что ответили.
        self.qr_granted = False
        self.qr_checks = []

        def sensitive_access_granted(user_id):
            self.qr_checks.append(user_id)
            return self.qr_granted

        app = Flask(__name__)
        app.register_blueprint(routes.build_driver_mailings_blueprint(
            db=_FakeDb(),
            require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (self.requester['id'], None, None),
            sensitive_access_granted=sensitive_access_granted,
        ))
        self.http = app.test_client()

    def names(self):
        return [name for name, _args, _kwargs in self.calls]


class SessionRouteTests(_RouteHarness):
    """Подключение аккаунта рассылок и честные ошибки про его сессию."""

    def push(self):
        return self.http.post('/api/driver_mailings/session', json={
            'cookies': [{'name': 'Session_id', 'value': 'new'}], 'user_agent': 'UA'})

    def test_push_saves_session_and_rescans_parks(self):
        response = self.push()
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertEqual(body['parks_checked'], 3)
        self.assertEqual(body['parks_enabled'], 3)
        self.assertIsNone(body['scan_error'])
        saved = [kwargs for name, _a, kwargs in self.calls if name == 'save_session'][0]
        self.assertEqual(saved['account'], 'mailings@example.com')
        self.assertEqual(saved['updated_by'], 1)
        # Кэш парков переписан опросом ПОД НОВЫМИ куками, а не старыми.
        self.assertIn('save_parks_cache', self.names())
        self.assertEqual(_FakeCabinet.created, [[{'name': 'Session_id', 'value': 'new'}]])
        # Общую сессию «Провайдера ЭДО» подключение не трогает.
        self.assertNotIn('shared_save', self.names())

    def test_only_super_admin_connects_the_account(self):
        # Дана раздел видит и рассылки отправляет, но аккаунт не подключает.
        self.requester = {'id': 476, 'name': 'Дана', 'role': 'admin'}
        response = self.push()
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('save_session', self.names())
        self.assertEqual(self.http.get('/api/driver_mailings/session').status_code, 403)

    def test_dead_cookies_are_refused_without_blaming_the_old_session(self):
        from fleet_edm.client import FleetSessionExpired
        self.own_row = {'cookies': [{'name': 'Session_id', 'value': 'old'}]}
        _FakeCabinet.check_error = FleetSessionExpired('Кабинет отдал страницу входа')
        response = self.push()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'SESSION_INVALID')
        self.assertNotIn('save_session', self.names())
        self.assertNotIn('mark_session_error', self.names())

    def test_failed_scan_forgets_the_previous_account_parks(self):
        from fleet_edm.client import FleetError
        _FakeCabinet.parks_error = FleetError('500')
        response = self.push()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['scan_error'])
        self.assertIn('save_session', self.names())
        self.assertIn('clear_parks_cache', self.names())
        self.assertNotIn('save_parks_cache', self.names())

    def test_expired_own_session_says_so_and_is_marked(self):
        from fleet_edm.client import FleetSessionExpired
        self.own_row = {'cookies': [{'name': 'Session_id', 'value': 'own'}]}
        _FakeCabinet.parks_error = FleetSessionExpired('протухла')
        response = self.http.post('/api/driver_mailings/parks/refresh')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['error'], self.routes.SESSION_EXPIRED_OWN)
        self.assertIn('mark_session_error', self.names())
        # Ходили СВОИМИ куками, общими — нет.
        self.assertEqual(_FakeCabinet.created, [[{'name': 'Session_id', 'value': 'own'}]])
        self.assertNotIn('shared_mark_error', self.names())

    def test_without_own_session_the_shared_one_is_used_and_left_alone(self):
        from fleet_edm.client import FleetSessionExpired
        _FakeCabinet.parks_error = FleetSessionExpired('протухла')
        response = self.http.post('/api/driver_mailings/parks/refresh')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['error'], self.routes.SESSION_EXPIRED_SHARED)
        self.assertEqual(_FakeCabinet.created, [self.shared_row['cookies']])
        self.assertNotIn('mark_session_error', self.names())
        self.assertNotIn('shared_mark_error', self.names())

    def test_session_status_tells_whose_account_is_in_use(self):
        body = self.http.get('/api/driver_mailings/session').get_json()
        self.assertEqual(body['session']['source'], self.routes.SESSION_SHARED)
        self.assertEqual(body['session']['account'], 'edm@example.com')
        self.own_row = {'cookies': [{'name': 'Session_id', 'value': 'own'}]}
        body = self.http.get('/api/driver_mailings/session').get_json()
        self.assertEqual(body['session']['source'], self.routes.SESSION_OWN)
        self.assertEqual(body['session']['account'], 'mailings@example.com')


class SensitiveQrRouteTests(_RouteHarness):
    """Гейт QR стоит в общем декораторе: закрыт КАЖДЫЙ маршрут раздела, а не
    только экран. Спрятанный замком экран доступом не является."""

    ROUTES = (
        ('get', '/api/driver_mailings/overview'),
        ('get', '/api/driver_mailings/filters'),
        ('post', '/api/driver_mailings/recipients/count'),
        ('post', '/api/driver_mailings/send'),
        ('get', '/api/driver_mailings/journal'),
        ('post', '/api/driver_mailings/journal/5/revoke'),
        ('get', '/api/driver_mailings/templates'),
        ('post', '/api/driver_mailings/parks/refresh'),
    )

    def test_rank_and_file_without_qr_is_refused_everywhere(self):
        self.requester = {'id': 523, 'name': 'Сотрудник ООЗ', 'role': 'operator'}
        for method, path in self.ROUTES:
            with self.subTest(path=path):
                response = getattr(self.http, method)(path, json={})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json()['code'], 'SENSITIVE_ACCESS_REQUIRED')
        # До кабинета и базы раздела дело не дошло ни разу.
        self.assertEqual(self.names(), [])
        self.assertEqual(_FakeCabinet.events, [])

    def test_confirmed_session_opens_the_section(self):
        self.requester = {'id': 523, 'name': 'Сотрудник ООЗ', 'role': 'operator'}
        self.qr_granted = True
        response = self.http.post('/api/driver_mailings/parks/refresh')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.qr_checks, [523])

    def test_not_listed_person_hears_about_the_section_not_about_qr(self):
        # Первый гейт — именной список: предлагать QR тому, кому раздел не
        # выдавали, — тупик, из которого он не выйдет.
        self.requester = {'id': 524, 'name': 'Коллега', 'role': 'operator'}
        response = self.http.get('/api/driver_mailings/overview')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'MAILINGS_SECTION_CLOSED')
        self.assertEqual(self.qr_checks, [])

    def test_super_admin_is_not_asked(self):
        response = self.http.post('/api/driver_mailings/parks/refresh')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.qr_checks, [])


class SendRouteTests(_RouteHarness):
    """Отправка по многим диспетчерским: подготовка параллельно ДО первой
    отправки, отправки строго по очереди, поиск id — после, и даже при обрыве."""

    def send(self, park_ids=('a', 'b', 'c')):
        return self.http.post('/api/driver_mailings/send', json={
            'idempotency_key': 'tok-1', 'title': 'Акция', 'message': 'Текст',
            'park_ids': list(park_ids), 'filters': {}})

    def events(self, prefix):
        return [event for event in _FakeCabinet.events if event.startswith(prefix)]

    def test_prepare_all_then_send_in_order_then_link(self):
        response = self.send()
        self.assertEqual(response.status_code, 200, response.get_json())
        events = _FakeCabinet.events
        first_send = events.index('send:a')
        # Охват и снимки всех парков — до первой отправки и до карточки.
        for park in 'abc':
            self.assertLess(events.index('count:' + park), first_send)
            self.assertLess(events.index('snap:' + park), first_send)
            self.assertLess(events.index('count:' + park), events.index('db:create'))
        # Отправки — строго в порядке выбора, по одной.
        self.assertEqual(self.events('send:'), ['send:a', 'send:b', 'send:c'])
        # Отправленной цель отмечается сразу после своей отправки: от этой
        # минуты кабинет отсчитывает окно отзыва.
        self.assertLess(events.index('db:sent:a'), events.index('send:b'))
        self.assertLess(events.index('db:sent:b'), events.index('send:c'))
        # id ищется после всех отправок и привязывается отдельной записью, не
        # трогая время отправки.
        last_send = events.index('send:c')
        for park in 'abc':
            self.assertGreater(events.index('resolve:' + park), last_send)
        linked = {args[1]: args[2] for name, args, _kw in self.calls if name == 'link'}
        self.assertEqual(linked, {'a': 'f-a', 'b': 'f-b', 'c': 'f-c'})
        sent_with_id = [kw for name, _a, kw in self.calls
                        if name == 'sent' and kw.get('fleet_mailing_id')]
        self.assertEqual(sent_with_id, [])

    def test_expired_session_before_sending_leaves_no_card(self):
        # «Неудачной рассылки», которую никто не начинал, в журнале быть не должно.
        _FakeCabinet.expire_on = ('count', 'b')
        response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.events('send:'), [])
        self.assertNotIn('create', self.names())

    def test_already_sent_parks_get_their_id_when_sending_breaks(self):
        # Обрыв на втором парке: первый уже у водителей и обязан остаться
        # отзываемым — значит его id ищется и в этом случае.
        _FakeCabinet.expire_on = ('send', 'b')
        response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.events('send:'), ['send:a', 'send:b'])
        linked = {args[1]: args[2] for name, args, _kw in self.calls if name == 'link'}
        self.assertEqual(linked, {'a': 'f-a'})
        failed = [kw for name, _a, kw in self.calls if name == 'failed']
        self.assertEqual([kw.get('error_code') for kw in failed], ['session_expired'])
        self.assertIn('abandon', self.names())


class RefsCacheTests(_RouteHarness):
    """Справочники кэшируются по парку: меняется набор — спрашиваем только новые."""

    def setUp(self):
        super().setUp()
        self.asked = []
        harness = self

        def _available_filters(cabinet, park_id):
            harness.asked.append(('filters', park_id))
            return {'segments_and_subsegments': {'active': {'subsegments': []}}}

        def _working_cities(cabinet, park_id, segment):
            harness.asked.append(('cities', park_id, segment))
            return [{'id': park_id + '-city', 'name': 'Город ' + park_id}]

        _FakeCabinet.available_filters = _available_filters
        _FakeCabinet.available_groups = lambda cabinet, park_id: []
        _FakeCabinet.professions = lambda cabinet, park_id: [
            {'id': 'taxi/driver', 'name': 'Водитель такси'}]
        _FakeCabinet.references = lambda cabinet, park_id: {}
        _FakeCabinet.working_cities = _working_cities
        for name in ('available_filters', 'available_groups', 'professions',
                     'references', 'working_cities'):
            self.addCleanup(delattr, _FakeCabinet, name)

    def filters(self, park_ids, segment=''):
        return self.http.get('/api/driver_mailings/filters', query_string={
            'park_ids': ','.join(park_ids), 'segment': segment})

    def test_changing_the_set_asks_only_the_new_parks(self):
        self.assertEqual(self.filters(['a', 'b']).status_code, 200)
        self.assertEqual(sorted(self.asked), [('filters', 'a'), ('filters', 'b')])
        self.asked.clear()
        body = self.filters(['a', 'b', 'c']).get_json()
        self.assertEqual(self.asked, [('filters', 'c')])
        self.assertEqual([s['id'] for s in body['segments']], ['active'])
        self.asked.clear()
        self.filters(['b'])
        self.assertEqual(self.asked, [])

    def test_cities_are_cached_per_segment_and_merged(self):
        body = self.filters(['a', 'b'], segment='active').get_json()
        self.assertEqual(sorted(c['name'] for c in body['cities']), ['Город a', 'Город b'])
        self.asked.clear()
        self.filters(['a', 'b'], segment='churn')
        # Общие справочники уже есть, города для другого сегмента — новые.
        self.assertEqual(sorted(self.asked), [('cities', 'a', 'churn'), ('cities', 'b', 'churn')])

    def test_failed_park_is_not_cached(self):
        from fleet_edm.client import FleetError
        calls = {'n': 0}
        original = _FakeCabinet.available_filters

        def flaky(cabinet, park_id):
            calls['n'] += 1
            if calls['n'] == 1:
                raise FleetError('500')
            return original(cabinet, park_id)

        _FakeCabinet.available_filters = flaky
        self.assertEqual(self.filters(['a']).status_code, 200)
        self.filters(['a'])
        # Первый ответ был неполным и в кэш не лёг — второй раз спросили снова.
        self.assertEqual(calls['n'], 2)


class CountRouteTests(_RouteHarness):
    """Охват: ответ кабинета помнится, одно и то же дважды не спрашивается, а
    новый подсчёт формы гасит прежний.

    28.09.2026 каждый щелчок по фильтрам запускал подсчёт по 89 паркам заново,
    прежний шёл до конца, подсчёты копились десятками — и кабинет отбивался
    429 больше двадцати тысяч раз за 45 минут."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        self.asked = []
        self.fail_once = set()
        self.on_count = None
        harness = self

        def _count(cabinet, park_id, filters):
            harness.asked.append((park_id, json.dumps(filters, sort_keys=True)))
            if park_id in harness.fail_once:
                harness.fail_once.discard(park_id)
                raise FleetError('429 Too Many Requests')
            if harness.on_count:
                harness.on_count(park_id, filters)
            return 100

        patcher = mock.patch.object(_FakeCabinet, 'recipients_count', _count)
        patcher.start()
        self.addCleanup(patcher.stop)

    def count(self, park_ids=('a', 'b', 'c'), filters=None, http=None, **extra):
        body = {'park_ids': list(park_ids), 'filters': filters or {}}
        body.update(extra)
        response = (http or self.http).post('/api/driver_mailings/recipients/count', json=body)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def parks_asked(self, filters=None):
        key = json.dumps(filters or {}, sort_keys=True)
        return sorted(park for park, asked_filters in self.asked if asked_filters == key)

    def test_same_count_again_does_not_ask_the_cabinet(self):
        self.assertEqual(self.count()['total'], 300)
        self.assertEqual(self.parks_asked(), ['a', 'b', 'c'])
        self.asked.clear()
        self.assertEqual(self.count()['total'], 300)
        self.assertEqual(self.asked, [])

    def test_one_more_park_asks_only_that_park(self):
        # «Выбрать все», потом ещё галочка: 88 уже посчитанных не спрашиваем.
        self.count(['a', 'b'])
        self.asked.clear()
        self.assertEqual(self.count(['a', 'b', 'c'])['total'], 300)
        self.assertEqual(self.parks_asked(), ['c'])

    def test_order_of_values_is_the_same_count(self):
        self.count(['a'], {'segment': 'active', 'city_ids': ['Алматы', 'Астана']})
        self.asked.clear()
        self.count(['a'], {'segment': 'active', 'city_ids': ['Астана', 'Алматы']})
        self.assertEqual(self.asked, [])

    def test_other_filter_is_another_count(self):
        self.count(['a'])
        self.asked.clear()
        self.count(['a'], {'segment': 'active'})
        self.assertEqual(self.parks_asked({'segment': 'active'}), ['a'])

    def test_failed_park_is_asked_again(self):
        self.fail_once = {'b'}
        first = self.count()
        rows = {row['park_id']: row for row in first['by_park']}
        self.assertEqual(rows['b'].get('error'), 'не удалось посчитать')
        self.assertEqual(first['total'], 200)
        self.asked.clear()
        second = self.count()
        self.assertEqual(self.parks_asked(), ['b'])
        self.assertEqual(second['total'], 300)

    def test_newer_count_of_the_same_form_stops_the_older_one(self):
        from unittest import mock
        # По одному потоку — чтобы «посередине подсчёта» было детерминированным.
        patcher = mock.patch.object(self.routes, 'COUNT_WORKERS', 1)
        patcher.start()
        self.addCleanup(patcher.stop)
        newer = {}

        def on_count(park_id, filters):
            # Пока прежний подсчёт считает первый парк, форма просит новый.
            if park_id == 'a' and not filters and not newer:
                newer['body'] = self.count(filters={'segment': 'active'}, stream='tab-1',
                                           seq=2, http=self.http.application.test_client())

        self.on_count = on_count
        older = self.count(stream='tab-1', seq=1)
        rows = {row['park_id']: row for row in older['by_park']}
        self.assertEqual(rows['a']['count'], 100)       # начатый парк досчитан
        self.assertTrue(rows['b'].get('skipped'))
        self.assertTrue(rows['c'].get('skipped'))
        self.assertTrue(older['superseded'])
        self.assertEqual(self.parks_asked(), ['a'])     # b и c в кабинет не ушли
        self.assertEqual(newer['body']['total'], 300)
        self.assertNotIn('superseded', newer['body'])

    def test_older_number_arriving_late_is_stopped_at_once(self):
        # Запрос, обогнанный в сети следующим, не гасит его — и сам в кабинет
        # уже не идёт.
        self.count(filters={'segment': 'active'}, stream='tab-1', seq=2)
        self.asked.clear()
        late = self.count(stream='tab-1', seq=1)
        self.assertEqual(self.asked, [])
        self.assertTrue(late['superseded'])
        self.assertTrue(all(row.get('skipped') for row in late['by_park']))

    def test_other_tab_and_other_person_are_not_stopped(self):
        self.count(filters={'segment': 'active'}, stream='tab-1', seq=5)
        self.assertEqual(self.count(stream='tab-2', seq=1)['total'], 300)
        self.requester = {'id': 476, 'name': 'Дана', 'role': 'admin'}
        body = self.count(filters={'segment': 'churn'}, stream='tab-1', seq=1)
        self.assertEqual(body['total'], 300)
        self.assertNotIn('superseded', body)

    def test_without_a_number_nothing_is_stopped(self):
        # Старая сборка фронта, открытая до деплоя, номера не шлёт.
        self.count(filters={'segment': 'active'}, stream='tab-1', seq=9)
        body = self.count()
        self.assertEqual(body['total'], 300)
        self.assertNotIn('superseded', body)

    def test_send_right_after_count_takes_the_counted_reach(self):
        self.count()
        self.asked.clear()
        response = self.http.post('/api/driver_mailings/send', json={
            'idempotency_key': 'tok-2', 'title': 'Акция', 'message': 'Текст',
            'park_ids': ['a', 'b', 'c'], 'filters': {}})
        self.assertEqual(response.status_code, 200, response.get_json())
        # Охват — тот, что человек видел в окне подтверждения, без 89 запросов.
        self.assertEqual(self.asked, [])
        estimates = {kw['park_id']: kw['recipients_estimate']
                     for name, _args, kw in self.calls if name == 'add_target'}
        self.assertEqual(estimates, {'a': 100, 'b': 100, 'c': 100})
        # А снимок журнала — свежий: по нему ищется id своей рассылки.
        self.assertEqual(sorted(e for e in _FakeCabinet.events if e.startswith('snap:')),
                         ['snap:a', 'snap:b', 'snap:c'])

    def test_client_lives_until_the_cookies_change(self):
        self.own_row = {'cookies': [{'name': 'Session_id', 'value': 'one'}]}
        self.count(['a'])
        self.count(['b'])
        self.assertEqual([c[0]['value'] for c in _FakeCabinet.created], ['one'])
        self.own_row = {'cookies': [{'name': 'Session_id', 'value': 'two'}]}
        self.count(['c'])
        self.assertEqual([c[0]['value'] for c in _FakeCabinet.created], ['one', 'two'])

    def test_new_account_forgets_the_counted_reach(self):
        self.count(['a'])
        self.asked.clear()
        push = self.http.post('/api/driver_mailings/session', json={
            'cookies': [{'name': 'Session_id', 'value': 'new'}], 'user_agent': 'UA'})
        self.assertEqual(push.status_code, 200, push.get_json())
        self.count(['a'])
        self.assertEqual(self.parks_asked(), ['a'])


class CountWiringTests(unittest.TestCase):
    """Форма подписывает подсчёт своим номером: без него сервер не отличит
    устаревший подсчёт от нужного, и они снова начнут копиться десятками."""

    def test_count_request_carries_the_form_number(self):
        with open(VIEW_JSX, encoding='utf-8') as handle:
            source = handle.read()
        self.assertIn('const [countStream] = useState(newToken);', source)
        self.assertRegex(source, r"/recipients/count`, \{\s*park_ids: parkKey\.split\(','\), "
                                 r"filters: JSON\.parse\(filtersKey\),\s*stream: countStream, seq,")


class FrontendWiringTests(unittest.TestCase):
    """Раздел, которого нет в меню, «доступ не выдали» — так это выглядит снаружи.

    Тест сторожит все точки подключения в App.jsx: пропуск любой даёт либо
    невидимый раздел, либо раздел, открывающийся только прямым адресом.
    """

    @classmethod
    def setUpClass(cls):
        with open(APP_JSX, encoding='utf-8') as handle:
            cls.app = handle.read()

    def test_component_is_lazy_loaded_and_rendered(self):
        self.assertIn("const DriverMailingsView = lazyWithRetry("
                      "() => import('./components/driver_mailings/DriverMailingsView'));",
                      self.app)
        self.assertIn('view === "driver_mailings" && canAccessDriverMailings', self.app)

    def test_allowlist_matches_the_backend(self):
        # Два списка людей, разошедшиеся между собой, — это раздел, который
        # показывается одному человеку, а пускает другого.
        for user_id in access.SECTION_ALLOWED_USER_IDS:
            self.assertIn(str(user_id), self.app.split(
                'const DRIVER_MAILINGS_ALLOWED_USER_IDS')[1].split(';')[0])

    def test_predicate_refuses_plain_admins_like_the_backend(self):
        gate = self.app.split('const canAccessDriverMailingsForUser')[1].split(';')[0]
        self.assertIn("=== 'super_admin'", gate)
        self.assertIn('DRIVER_MAILINGS_ALLOWED_USER_IDS.has(Number(userLike?.id))', gate)
        # Роль admin сама по себе пускать не должна — как и на бэкенде.
        self.assertNotIn("'admin'", gate.replace("'super_admin'", ''))

    def test_menu_item_lives_in_every_role_branch(self):
        # Право именное: если этого человека назначат главой отдела, он уйдёт из
        # админской ветки меню, а бэкенд по-прежнему будет его пускать. Третья
        # копия — у рядового: с задачи #359 в списке есть сотрудник ООЗ с
        # ролью оператора.
        self.assertEqual(
            self.app.count("handleSidebarViewNavigation(e, 'driver_mailings')"), 3)
        self.assertEqual(
            self.app.count('<span className="sidebar-text">Рассылки</span>'), 3)
        rank_marker = "{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && ("
        rank_menu = self.app.split(rank_marker)[1].split(rank_marker)[0]
        rank_menu = rank_menu.split("\n" + " " * 40 + "</>\n" + " " * 36 + ")}")[0]
        self.assertIn("{canAccessDriverMailings && (", rank_menu)
        self.assertIn("handleSidebarViewNavigation(e, 'driver_mailings')", rank_menu)

    def test_screen_is_locked_until_qr_for_rank_and_file(self):
        # Замок — тот же, что у «Посылок» и «Вики»: предикат портала, а не своя
        # копия правила.
        render = self.app.split('{( view === "driver_mailings" && canAccessDriverMailings && (', 1)[1]
        render = render.split('{( view === "payments"', 1)[0]
        self.assertTrue(render.startswith('sensitiveSectionsLocked ? ('), render[:80])
        self.assertIn('<SensitiveSectionGate', render)
        self.assertIn('sectionTitle="Рассылки"', render)
        self.assertIn('checking={sensitiveSectionsChecking}', render)
        self.assertIn('onRequestQr={requestSensitiveQrAccess}', render)
        self.assertIn('<DriverMailingsView', render)
        # Статус спрашивается при входе в раздел — иначе замок мигнёт тому,
        # кто доступ уже подтвердил.
        self.assertIn("|| view === 'driver_mailings') {\n                    fetchSensitiveAccessStatus();",
                      self.app)

    def test_backend_blueprint_gets_the_qr_check(self):
        # Без аргумента сборка blueprint падает TypeError, а bot_schedule2 ловит
        # это и пишет в лог — раздел молча пропал бы целиком.
        bot_path = os.path.join(os.path.dirname(APP_JSX), '..', 'bot_schedule2.py')
        with open(bot_path, encoding='utf-8') as handle:
            bot = handle.read()
        block = bot.split('app.register_blueprint(build_driver_mailings_blueprint(', 1)[1].split('))', 1)[0]
        self.assertIn('sensitive_access_granted=_sensitive_access_granted_for_user,', block)

    def test_view_opens_by_url_and_is_not_bounced(self):
        self.assertIn("(requestedViewFromUrl !== 'driver_mailings' || canAccessDriverMailings)",
                      self.app)
        self.assertIn("if (view === 'driver_mailings' && canAccessDriverMailings) return;",
                      self.app)

    def test_analytics_name_is_registered(self):
        self.assertIn("driver_mailings: 'Driver mailings',", self.app)


if __name__ == '__main__':
    unittest.main()
