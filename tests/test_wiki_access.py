# -*- coding: utf-8 -*-
"""Права раздела «Вики»: способности, субъекты правил, эффективные права статьи.

Отдельно проверяем требования владельца, которых в исходной вики НЕТ:
  * право can_delete (в оригинале удаление гейтится только ролью, и «редактором»
    там считаются 8 ролей — то есть любой супервайзер мог снести любую статью);
  * права на уровне отдельной статьи и режим «читают только вот эти»;
  * запрет конкретному человеку внутри разрешённого отдела (mode='deny');
  * strict_mode — статья с корпоративной информацией, невидимая даже
    администратору вики без явного гранта.

Тест импортирует wiki.access НАПРЯМУЮ. Это и есть проверка архитектурного
решения: остальные тесты проекта вынуждены вытаскивать методы из database.py
через ast, потому что его импорт поднимает пул к боевой базе.
"""

import unittest

from wiki.access import (
    ROLE_LEVELS,
    capabilities_from_otp_role,
    collect_subjects,
    expand_otp_roles,
    permissions_only,
    resolve_article_permissions,
    resolve_capabilities,
)
from wiki.schema import CAPABILITY_COLUMNS, OTP_ROLES, PERMISSION_COLUMNS


def grant(**kwargs):
    rule = {name: False for name in PERMISSION_COLUMNS}
    rule['mode'] = 'grant'
    rule.update(kwargs)
    return rule


def deny(**kwargs):
    rule = {name: False for name in PERMISSION_COLUMNS}
    rule['mode'] = 'deny'
    rule.update(kwargs)
    return rule


ALL_CAPS = {name: True for name in CAPABILITY_COLUMNS}


class CapabilitiesTest(unittest.TestCase):
    def test_operator_can_only_read(self):
        caps = capabilities_from_otp_role('operator')
        self.assertTrue(caps['can_read'])
        for name in ('can_create', 'can_edit', 'can_delete', 'can_publish',
                     'can_approve', 'can_manage_access'):
            self.assertFalse(caps[name], name)

    def test_supervisor_role_is_not_forgotten(self):
        # 'supervisor' есть в CHECK на users.role, но её нет в ROLE_HIERARCHY.
        # Если о ней забыть, носители роли не получат вообще ничего.
        caps = capabilities_from_otp_role('supervisor')
        self.assertTrue(caps['can_read'])
        self.assertTrue(caps['can_edit'])

    def test_department_head_is_not_a_global_admin(self):
        # В OTP isAdminLikeRole специально вычитает глав отделов из глобальных
        # админов. Повторяем: структура — да, пользователи и доступы — нет.
        head = capabilities_from_otp_role('admin', is_department_head=True)
        self.assertTrue(head['can_manage_structure'])
        self.assertFalse(head['can_manage_users'])
        self.assertFalse(head['can_manage_access'])

        globaladmin = capabilities_from_otp_role('admin', is_department_head=False)
        self.assertTrue(globaladmin['can_manage_access'])

    def test_wiki_roles_win_over_otp_role(self):
        caps = resolve_capabilities('operator', [{'can_delete': True}])
        self.assertTrue(caps['can_delete'], 'роль вики должна перебивать роль OTP')

    def test_without_wiki_roles_falls_back(self):
        caps = resolve_capabilities('sv', [])
        self.assertTrue(caps['can_edit'])
        self.assertFalse(caps['can_delete'])

    def test_every_otp_role_is_handled(self):
        for role in OTP_ROLES:
            caps = capabilities_from_otp_role(role)
            self.assertTrue(caps['can_read'], 'роль %s осталась без чтения' % role)


class SubjectsTest(unittest.TestCase):
    def test_rule_on_operator_applies_to_everyone_above(self):
        # Замена рекурсии по positions.parent_position_id из оригинала.
        self.assertIn('operator', expand_otp_roles('sv'))
        self.assertIn('operator', expand_otp_roles('admin'))
        self.assertIn('sv', expand_otp_roles('super_admin'))

    def test_rule_on_admin_does_not_leak_down(self):
        self.assertNotIn('admin', expand_otp_roles('operator'))
        self.assertNotIn('super_admin', expand_otp_roles('sv'))

    def test_supervisor_matches_sv_level(self):
        roles = expand_otp_roles('supervisor')
        self.assertIn('supervisor', roles)
        self.assertIn('operator', roles, 'supervisor должен видеть операторские правила')

    def test_department_head_gets_his_departments(self):
        subjects = collect_subjects(
            user_id=7, otp_role='admin', department_id=3,
            headed_department_ids=[3, 9], direction_id=None,
            group_ids=[], wiki_role_ids=[],
        )
        self.assertEqual(subjects['department'], [3, 9])
        self.assertEqual(subjects['user'], [7])

    def test_groups_from_both_memberships(self):
        subjects = collect_subjects(
            user_id=1, otp_role='sv', group_ids=[5, 5, 2], wiki_role_ids=[],
        )
        self.assertEqual(subjects['group'], [2, 5], 'дубликаты должны схлопываться')

    def test_role_levels_match_otp(self):
        # Копия ROLE_HIERARCHY из bot_schedule2.py:1501 — если там поменяют,
        # этот тест не заметит, но зафиксирует ожидаемое значение.
        self.assertEqual(ROLE_LEVELS['operator'], 10)
        self.assertEqual(ROLE_LEVELS['sv'], 30)
        self.assertEqual(ROLE_LEVELS['super_admin'], 50)
        self.assertNotIn('supervisor', ROLE_LEVELS)


class ArticlePermissionsTest(unittest.TestCase):
    def test_inherits_from_sections_by_default(self):
        perms = resolve_article_permissions(
            capabilities=capabilities_from_otp_role('operator'),
            section_rules=[grant(can_read=True)],
        )
        self.assertTrue(perms['can_read'])

    def test_restricted_article_ignores_sections(self):
        """«Некоторые статьи даже читать не должно быть возможности»."""
        perms = resolve_article_permissions(
            capabilities=capabilities_from_otp_role('operator'),
            visibility_mode='restricted',
            section_rules=[grant(can_read=True)],   # раздел разрешён…
            article_rules=[],                        # …но у статьи своего правила нет
        )
        self.assertFalse(perms['can_read'], 'режим restricted обязан игнорировать разделы')

    def test_restricted_article_visible_to_listed_subject(self):
        perms = resolve_article_permissions(
            capabilities=capabilities_from_otp_role('operator'),
            visibility_mode='restricted',
            section_rules=[],
            article_rules=[grant(can_read=True)],
        )
        self.assertTrue(perms['can_read'])

    def test_deny_beats_grant(self):
        """Скрыть от одного человека внутри разрешённого отдела."""
        perms = resolve_article_permissions(
            capabilities=capabilities_from_otp_role('operator'),
            section_rules=[grant(can_read=True)],
            article_rules=[deny(can_read=True)],
        )
        self.assertFalse(perms['can_read'])
        self.assertIn('запрещ', perms['_reason'])

    def test_delete_needs_both_capability_and_rule(self):
        """Наивный перенос дал бы любому супервайзеру право снести любую статью."""
        rules = [grant(can_read=True, can_delete=True)]

        sv = resolve_article_permissions(
            capabilities=capabilities_from_otp_role('sv'), section_rules=rules)
        self.assertFalse(sv['can_delete'], 'у sv нет способности can_delete')

        owner = resolve_article_permissions(
            capabilities=resolve_capabilities('sv', [{'can_read': True, 'can_delete': True}]),
            section_rules=rules)
        self.assertTrue(owner['can_delete'])

    def test_capability_without_rule_is_not_enough(self):
        # Субъект намеренно НЕ администратор вики: у администратора действует
        # короткое замыкание, и оно проверяется отдельным тестом ниже.
        editor = resolve_capabilities('operator', [{'can_read': True, 'can_edit': True}])
        self.assertFalse(editor['can_manage_access'])

        perms = resolve_article_permissions(
            capabilities=editor,
            visibility_mode='restricted',
            article_rules=[grant(can_read=True)],   # правило даёт только чтение
        )
        self.assertTrue(perms['can_read'])
        self.assertFalse(perms['can_edit'], 'способность без правила на объекте не даёт записи')

    def test_wiki_admin_overrides_deny(self):
        perms = resolve_article_permissions(
            capabilities=ALL_CAPS,
            section_rules=[grant(can_read=True)],
            article_rules=[deny(can_read=True)],
            otp_role='admin',
        )
        self.assertTrue(perms['can_read'], 'админ не должен уметь заблокировать сам себя')
        self.assertTrue(perms['_bypassed_restriction'])

    def test_strict_mode_hides_from_wiki_admin(self):
        """Корпоративная информация: строгий режим закрыт даже администратору вики."""
        perms = resolve_article_permissions(
            capabilities=ALL_CAPS,
            strict_mode=True,
            visibility_mode='restricted',
            article_rules=[],
            otp_role='admin',
        )
        self.assertFalse(perms['can_read'])
        self.assertFalse(perms['_bypassed_restriction'])

    def test_strict_mode_lets_super_admin_through_and_flags_it(self):
        perms = resolve_article_permissions(
            capabilities=ALL_CAPS,
            strict_mode=True,
            visibility_mode='restricted',
            article_rules=[],
            otp_role='super_admin',
        )
        self.assertTrue(perms['can_read'])
        self.assertTrue(perms['_bypassed_restriction'],
                        'обход строгого режима обязан быть помечен для журнала')

    def test_strict_mode_grants_explicit_subject(self):
        perms = resolve_article_permissions(
            capabilities=capabilities_from_otp_role('operator'),
            strict_mode=True,
            visibility_mode='restricted',
            article_rules=[grant(can_read=True)],
            otp_role='operator',
        )
        self.assertTrue(perms['can_read'], 'явный грант работает и в строгом режиме')

    def test_guest_access_grants_read_only(self):
        perms = resolve_article_permissions(
            capabilities=capabilities_from_otp_role('operator'),
            guest_allows_read=True,
        )
        self.assertTrue(perms['can_read'])
        self.assertFalse(perms['can_edit'])

    def test_author_keeps_access_to_own_article(self):
        perms = resolve_article_permissions(
            capabilities=capabilities_from_otp_role('sv'),
            visibility_mode='restricted',
            article_rules=[],
            is_article_owner=True,
        )
        self.assertTrue(perms['can_read'])
        self.assertTrue(perms['can_edit'])

    def test_permissions_only_strips_service_fields(self):
        perms = resolve_article_permissions(capabilities=ALL_CAPS, otp_role='operator')
        clean = permissions_only(perms)
        self.assertEqual(set(clean), set(PERMISSION_COLUMNS))

    def test_nothing_by_default(self):
        perms = resolve_article_permissions(capabilities=capabilities_from_otp_role('operator'))
        self.assertFalse(any(permissions_only(perms).values()),
                         'без единого правила статья не видна никому')


if __name__ == '__main__':
    unittest.main()
