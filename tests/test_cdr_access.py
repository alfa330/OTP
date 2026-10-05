# -*- coding: utf-8 -*-
"""Периметр раздела «Касания».

Проверяется правило, которое легко потерять при правке: раздел принадлежит
отделу продаж, но открыт и глобальным админам, которые ни в каком отделе не
состоят. При этом «глава отдела» — не роль, а признак: назначение главой
ЗАМЕНЯЕТ базовую роль и режет периметр своим отделом (действующая семантика
портала, та же в parcels/access.py и crm/access.py).

Отдельно закреплено, что оператору раздел закрыт: выгрузка — это телефоны
клиентов за период целиком, инструмент разбора работы отдела, а не личный
кабинет. Если однажды понадобится «свои звонки оператору», это будет другой
экран и другое право, а не ослабление этого.

Модуль `cdr.access` чистый — ни базы, ни Flask, поэтому импортируется напрямую.
`cdr.queries` соединений тоже не открывает: его функции берут готовый курсор.
"""

import re
import unittest
from pathlib import Path

from cdr import access, queries

APP_JSX = Path(__file__).resolve().parents[1] / 'src' / 'App.jsx'


def ctx(role='operator', department_code=None, headed_ids=None, headed_codes=None,
        user_id=1):
    return {
        'user_id': user_id, 'name': 'Кто-то', 'role': role,
        'department_id': None, 'department_code': department_code,
        'headed_department_ids': headed_ids or [],
        'headed_department_codes': headed_codes or [],
    }


class RoleTests(unittest.TestCase):
    def test_supervisor_spellings_are_one_role(self):
        self.assertEqual(access.normalize_role('supervisor'), 'sv')
        self.assertEqual(access.normalize_role('SV'), 'sv')
        self.assertEqual(access.normalize_role('superadmin'), 'super_admin')

    def test_unknown_role_falls_to_operator(self):
        """Правильная сторона ошибки: незнакомая роль — закрыто, а не открыто."""
        self.assertEqual(access.normalize_role('директор'), 'operator')
        self.assertFalse(access.can_open_section(ctx(role='директор',
                                                     department_code='op')))


class SectionAccessTests(unittest.TestCase):
    def test_super_admin_always_in(self):
        self.assertTrue(access.can_open_section(ctx(role='super_admin')))

    def test_global_admin_is_in_without_any_department(self):
        self.assertTrue(access.can_open_section(ctx(role='admin')))

    def test_admin_who_heads_another_department_is_out(self):
        """Назначение главой заменяет базовую роль: глава СЗоВ не читает звонки
        отдела продаж."""
        head_of_szov = ctx(role='admin', department_code='szov',
                           headed_ids=[1], headed_codes=['szov'])
        self.assertFalse(access.is_global_admin(head_of_szov))
        self.assertFalse(access.can_open_section(head_of_szov))

    def test_head_of_sales_is_in(self):
        self.assertTrue(access.can_open_section(
            ctx(role='admin', department_code='op', headed_ids=[367],
                headed_codes=['op'])))

    def test_sales_supervisor_is_in(self):
        self.assertTrue(access.can_open_section(ctx(role='sv', department_code='op')))

    def test_supervisor_of_another_department_is_out(self):
        self.assertFalse(access.can_open_section(ctx(role='sv', department_code='szov')))

    def test_sales_operator_is_out(self):
        """Выгрузка — это телефоны клиентов за период целиком, а не свои звонки."""
        self.assertFalse(access.can_open_section(ctx(role='operator',
                                                     department_code='op')))

    def test_trainer_is_out_even_in_sales(self):
        self.assertFalse(access.can_open_section(ctx(role='trainer',
                                                     department_code='op')))

    def test_department_code_is_case_insensitive(self):
        self.assertTrue(access.can_open_section(ctx(role='sv', department_code='OP')))
        self.assertTrue(access.can_open_section(ctx(role='sv', department_code=' op ')))


class SyncRightTests(unittest.TestCase):
    """Право «дозаказать сутки со станции» сейчас равно праву на чтение, но
    названо отдельно: у действия своя цена (минуты работы моста и нагрузка на
    станцию), и сузить его завтра надо будет в одном месте, а не по всем роутам."""

    def test_sync_follows_read_for_everyone(self):
        for who in (ctx(role='super_admin'), ctx(role='admin'),
                    ctx(role='sv', department_code='op'),
                    ctx(role='operator', department_code='op'),
                    ctx(role='trainer', department_code='op')):
            self.assertEqual(access.can_sync(who), access.can_open_section(who),
                             who['role'])


class CapabilitiesTests(unittest.TestCase):
    def test_capabilities_describe_the_same_rules(self):
        caps = access.capabilities(ctx(role='sv', department_code='op'))
        self.assertEqual(caps, {'can_open': True, 'can_sync': True,
                                'is_global_admin': False, 'is_department_head': False})

    def test_capabilities_for_a_stranger_are_all_false(self):
        caps = access.capabilities(ctx(role='operator', department_code='szov'))
        self.assertFalse(caps['can_open'])
        self.assertFalse(caps['can_sync'])


class NamedGrantTests(unittest.TestCase):
    """Поимённый допуск: трое из «Маркетинга» — id 471 и 472 (02.10.2026) и 474
    (05.10.2026) — и глава «Маркетинга», id 415 (05.10.2026).

    Должность рядовых (marketing_manager) раздел не знает и сводит к оператору;
    у главы роль admin, но назначение главой её заменяет. Отдел у всех не ОП —
    по роли и отделу им закрыто. Опасны оба промаха: поимённым не открылось и
    открылось всему «Маркетингу» либо должности главы, а не человеку.
    """

    # Профиль каждого: роль — как в users.role (сервер её нормализует), отдел и
    # главенство — как в контексте queries.load_access_context.
    GRANTED = {
        471: dict(role='marketing_manager', department_code='marketing'),
        472: dict(role='marketing_manager', department_code='marketing'),
        474: dict(role='marketing_manager', department_code='marketing'),
        415: dict(role='admin', department_code='marketing', headed_ids=[1041],
                  headed_codes=['marketing']),
    }

    def test_grant_lists_exactly_the_people_named_by_the_owner(self):
        self.assertEqual(access.EXTRA_ACCESS_USER_IDS, frozenset(self.GRANTED))

    def test_named_people_get_the_whole_section(self):
        for user_id, profile in self.GRANTED.items():
            with self.subTest(user_id=user_id):
                who = ctx(user_id=user_id, **profile)
                self.assertTrue(access.can_open_section(who))
                self.assertTrue(access.can_sync(who))
                caps = access.capabilities(who)
                self.assertTrue(caps['can_open'])
                self.assertTrue(caps['can_sync'])
                # Поимённый — не глобальный админ: эта ветка ему ничего не добавляет.
                self.assertFalse(caps['is_global_admin'])

    def test_it_is_the_list_that_opens_the_section(self):
        """Тот же человек с чужим id закрыт: допуск держится на списке, а не на
        роли, отделе или должности. Для главы это ещё и «человеку, а не месту» —
        сменится глава «Маркетинга», новому раздел сам не откроется."""
        for user_id, profile in self.GRANTED.items():
            with self.subTest(user_id=user_id):
                self.assertFalse(access.can_open_section(ctx(user_id=999, **profile)))
                self.assertFalse(access.can_sync(ctx(user_id=999, **profile)))

    def test_the_rest_of_marketing_stays_out(self):
        """Должность и отдел сами раздел не открывают — только строка списка."""
        self.assertFalse(access.can_open_section(
            ctx(role='marketing_manager', department_code='marketing', user_id=475)))

    def test_named_head_gets_no_global_admin_rights(self):
        """Строка списка открывает раздел, а глобальным админом главу не делает."""
        head = ctx(user_id=415, **self.GRANTED[415])
        self.assertFalse(access.is_global_admin(head))
        self.assertTrue(access.is_department_head(head))
        # Его отдел разделу по-прежнему чужой: главенство само ничего не открывает.
        self.assertFalse(access.belongs_to_sales(head))

    def test_missing_or_broken_id_is_closed_not_crashing(self):
        for user_id in (None, '', 'abc'):
            with self.subTest(user_id=user_id):
                self.assertFalse(access.can_open_section(
                    ctx(role='marketing_manager', department_code='marketing',
                        user_id=user_id)))


class _OneRowCursor:
    """Курсор-двойник: отдаёт одну строку — ту, что вернул бы запрос контекста."""

    def __init__(self, row):
        self.row = row
        self.params = None

    def execute(self, _sql, params=None):
        self.params = params

    def fetchone(self):
        return self.row


class AccessContextTests(unittest.TestCase):
    """Поимённый допуск держится на ОДНОМ поле контекста — user_id. Потеряй его
    настоящий queries.load_access_context, раздел молча закрылся бы всем
    поимённым (пункт в меню есть, сервер отвечает 403), а глава и СВ отдела
    продаж ничего бы не заметили: их пускают роль и отдел."""

    # Строка, как её отдаёт запрос: имя, роль, отдел, код отдела, главенство.
    ROWS = {
        415: ('Кто-то', 'admin', 1041, 'marketing', [1041], ['marketing']),
        471: ('Кто-то', 'marketing_manager', 1041, 'marketing', [], []),
    }

    def test_real_context_carries_the_id_the_grant_needs(self):
        for user_id, row in self.ROWS.items():
            with self.subTest(user_id=user_id):
                cursor = _OneRowCursor(row)
                # id приходит из сессии и строкой тоже — в контексте обязано быть число.
                context = queries.load_access_context(cursor, str(user_id))
                self.assertEqual(cursor.params, {'user_id': user_id})
                self.assertEqual(context['user_id'], user_id)
                self.assertTrue(access.can_open_section(context))
                self.assertTrue(access.can_sync(context))

    def test_same_row_under_another_id_stays_closed(self):
        for user_id, row in self.ROWS.items():
            with self.subTest(user_id=user_id):
                context = queries.load_access_context(_OneRowCursor(row), 999)
                self.assertFalse(access.can_open_section(context))

    def test_unknown_user_gives_no_context(self):
        self.assertIsNone(queries.load_access_context(
            _OneRowCursor((None, None, None, None, [], [])), 415))


class FrontendMirrorTests(unittest.TestCase):
    """Пункт меню рисует фронт по своему списку. Разойдись он с серверным —
    человек увидит пункт и получит 403 или получит допуск без пункта в меню.
    Поведение предиката проверяет tests/cdr_touches_access.test.mjs."""

    def test_menu_list_matches_the_backend(self):
        app = APP_JSX.read_text(encoding='utf-8')
        match = re.search(r'const TOUCHES_EXTRA_ACCESS_USER_IDS = new Set\(\[([\d,\s]*)\]\);', app)
        self.assertIsNotNone(match, 'список TOUCHES_EXTRA_ACCESS_USER_IDS не найден')
        listed = {int(x) for x in match.group(1).split(',') if x.strip()}
        self.assertEqual(listed, set(access.EXTRA_ACCESS_USER_IDS))


if __name__ == '__main__':
    unittest.main()
