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
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from driver_mailings import access, catalog  # noqa: E402
from driver_mailings.client import MailingRefused, MailingsClient  # noqa: E402
from driver_mailings.schema import DRIVER_MAILINGS_SCHEMA_SQL  # noqa: E402

APP_JSX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'src', 'App.jsx')
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

    def test_send_and_templates_match_view(self):
        allowed = {'id': 476, 'role': 'admin'}
        denied = {'id': 10, 'role': 'admin'}
        self.assertTrue(access.can_send(allowed))
        self.assertTrue(access.can_manage_templates(allowed))
        self.assertFalse(access.can_send(denied))
        self.assertFalse(access.can_manage_templates(denied))

    def test_capabilities_carry_every_key_the_front_reads(self):
        caps = access.capabilities({'id': 476, 'role': 'admin'})
        self.assertEqual(set(caps), {'can_view', 'can_send', 'can_manage_templates'})
        self.assertTrue(all(caps.values()))
        self.assertFalse(any(access.capabilities({'id': 1, 'role': 'sv'}).values()))


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

    def test_no_second_place_for_cabinet_cookies(self):
        # Сессия кабинета одна на портал и живёт в «Провайдере ЭДО».
        self.assertNotIn('cookies', DRIVER_MAILINGS_SCHEMA_SQL)


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

    def test_menu_item_lives_in_both_role_branches(self):
        # Право именное: если этого человека назначат главой отдела, он уйдёт из
        # админской ветки меню, а бэкенд по-прежнему будет его пускать.
        self.assertEqual(
            self.app.count("handleSidebarViewNavigation(e, 'driver_mailings')"), 2)
        self.assertEqual(
            self.app.count('<span className="sidebar-text">Рассылки</span>'), 2)

    def test_view_opens_by_url_and_is_not_bounced(self):
        self.assertIn("(requestedViewFromUrl !== 'driver_mailings' || canAccessDriverMailings)",
                      self.app)
        self.assertIn("if (view === 'driver_mailings' && canAccessDriverMailings) return;",
                      self.app)

    def test_analytics_name_is_registered(self):
        self.assertIn("driver_mailings: 'Driver mailings',", self.app)


if __name__ == '__main__':
    unittest.main()
