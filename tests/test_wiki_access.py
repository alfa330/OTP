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

import re
import unittest

from wiki import access, schema
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

    def test_supervisor_may_publish_but_trainer_may_not(self):
        """Решение владельца 19.08.2026: выпуск содержимого — часть работы СВ.

        До этого способности публиковать у роли не было, и выданное в ПРАВИЛЕ
        РАЗДЕЛА can_publish молча гасилось гейтом способностей: владелец выдал
        право в интерфейсе, правило сохранилось, а портал продолжал отвечать
        «нет права публиковать в этом разделе». Тренер оставлен без выпуска
        намеренно — он ведёт обучение, а не регламенты.
        """
        for role in ('sv', 'supervisor'):
            caps = capabilities_from_otp_role(role)
            self.assertTrue(caps['can_publish'], role)
            self.assertTrue(caps['can_approve'], role)
            # Удаление и раздача доступов супервайзеру по-прежнему не даются.
            self.assertFalse(caps['can_delete'], role)
            self.assertFalse(caps['can_manage_access'], role)
            self.assertFalse(caps['can_manage_users'], role)

        trainer = capabilities_from_otp_role('trainer')
        self.assertTrue(trainer['can_edit'])
        self.assertFalse(trainer['can_publish'])
        self.assertFalse(trainer['can_approve'])

    def test_publish_still_needs_the_section_rule(self):
        """Способность — «вправе в принципе», раздел решает «вправе здесь».

        Ровно этим разделены разделы «Супервайзер»/«Общий сотрудник» (право
        выписано) и любой раздел без такого правила: способность одна и та же,
        а результат разный. Если гейт правила когда-нибудь уберут, супервайзер
        начнёт публиковать во ВСЁМ своём периметре.
        """
        caps = capabilities_from_otp_role('sv')

        allowed = resolve_article_permissions(
            capabilities=caps,
            section_rules=[grant(can_read=True, can_edit=True, can_publish=True)])
        self.assertTrue(allowed['can_publish'])

        forbidden = resolve_article_permissions(
            capabilities=caps,
            section_rules=[grant(can_read=True, can_edit=True)])
        self.assertFalse(forbidden['can_publish'],
                         'без правила раздела способность публиковать не работает')

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


class GrantCeilingTest(unittest.TestCase):
    """Лестница выдачи доступа (решение владельца 18.08.2026).

        Коммерческий директор  → руководитель, СВ, тренер, оператор
        Руководитель группы    → СВ, тренер, оператор
        Супервайзер            → оператор
        Тренер, оператор       → не раздают вовсе

    Проверяется именно ТАБЛИЦА, а не «на ступень ниже»: у супервайзера
    ступенька перепрыгивает тренера, и формула, которая «почти совпадает»,
    разошлась бы с решением молча.
    """

    # Строки формы: подпись → порог правила (у нижней порога нет вовсе).
    ROWS = ((None, 'Оператор'), (20, 'Тренер'), (30, 'Супервайзер'), (40, 'Руководитель'))

    def granted(self, role):
        return [name for level, name in self.ROWS
                if access.may_grant_rule(role, level)]

    def test_commercial_director_grants_everyone(self):
        self.assertEqual(self.granted('super_admin'),
                         ['Оператор', 'Тренер', 'Супервайзер', 'Руководитель'])

    def test_commercial_director_reaches_every_role(self):
        """«Может добавить ЛЮБОГО сотрудника» — включая админов и супер-админов.

        Потолок 40 отрезал от списка пятерых супер-админов, и владелец не смог
        выписать точечное правило на коллегу. Правило «никто не выдаёт своему
        уровню» действует ниже по лестнице, но наверху смысла не имеет: над
        директором никого нет, а супер-админ и так видит все разделы.
        """
        for role in ('operator', 'trainee', 'trainer', 'sv', 'admin', 'super_admin'):
            self.assertTrue(
                access.may_grant_rule('super_admin', None, target_role=role),
                'директор должен уметь адресовать правило роли %s' % role)

    def test_head_grants_everyone_below(self):
        self.assertEqual(self.granted('admin'), ['Оператор', 'Тренер', 'Супервайзер'])

    def test_supervisor_grants_operators_only(self):
        """Тренер супервайзеру НЕ достаётся — это решение, а не недосмотр."""
        self.assertEqual(self.granted('sv'), ['Оператор'])
        self.assertEqual(self.granted('supervisor'), ['Оператор'])

    def test_trainer_and_operator_grant_nothing(self):
        self.assertEqual(self.granted('trainer'), [])
        self.assertEqual(self.granted('operator'), [])
        self.assertEqual(self.granted('trainee'), [])

    def test_middle_ranks_do_not_grant_at_own_level(self):
        """Середина лестницы не открывает раздел собственному уровню.

        Иначе супервайзер выдал бы права всем супервайзерам компании, а
        руководитель — всем руководителям: это уже не делегирование, а
        расширение собственного круга.

        Директор — намеренное исключение: он и есть верх лестницы, и владелец
        сформулировал его право как «любого сотрудника». Раньше этот тест
        включал и его — и тем закреплял ограничение, которого никто не просил.
        """
        for role in ('admin', 'sv'):
            level = access.ROLE_LEVELS[role]
            self.assertFalse(access.may_grant_rule(role, level),
                             '%s не должен выдавать своему уровню' % role)
        self.assertTrue(access.may_grant_rule('super_admin',
                                              access.ROLE_LEVELS['super_admin']))

    def test_personal_rule_checks_target_role(self):
        """Правило на человека проверяется по РОЛИ адресата, а не только по порогу.

        Порог у такого правила обычно пуст, и без роли адресата супервайзер
        выписал бы правило на самого себя — то есть выдал бы себе полный доступ.
        """
        self.assertTrue(access.may_grant_rule('sv', None, target_role='operator'))
        self.assertFalse(access.may_grant_rule('sv', None, target_role='sv'))
        self.assertFalse(access.may_grant_rule('sv', None, target_role='admin'))
        # Руководителю supervisor адресуется, а другой руководитель — нет.
        self.assertTrue(access.may_grant_rule('admin', None, target_role='sv'))
        self.assertFalse(access.may_grant_rule('admin', None, target_role='admin'))

    def test_unbounded_rule_weighs_as_operator(self):
        """Правило без порога открывает раздел всем от оператора — и весит так же.

        Ноль вместо уровня оператора пропустил бы любую проверку.
        """
        self.assertEqual(access.rule_grant_level(None), access.ROLE_LEVELS['operator'])
        self.assertEqual(access.rule_grant_level(30), 30)

    def test_wiki_admin_role_lifts_ceiling(self):
        """Роль вики с can_manage_access назначают руками — она поднимает потолок."""
        self.assertFalse(access.may_grant_rule('sv', 40))
        self.assertTrue(access.may_grant_rule('sv', 40, is_wiki_admin=True))
        # До самого верха: администратор вики не должен упираться в потолок ниже
        # директорского, иначе назначение роли ничего толком не даёт.
        self.assertTrue(access.may_grant_rule('sv', None, is_wiki_admin=True,
                                              target_role='super_admin'))


class DirectoryWriteCapabilityTest(unittest.TestCase):
    """Кто правит справочники «Парки» и «Офисы» (решение владельца 19.08.2026).

    Правит всякий, у кого есть ХОТЬ ЧТО-ТО сверх чтения. Прежний гейт
    can_manage_structure оставлял снаружи супервайзера и тренера: статью они
    завести могли, а поправить телефон парка — нет, хотя это тот же справочный
    контент и следить за ним, кроме них, некому.
    """

    def test_writers_may_edit_directories(self):
        for role in ('super_admin', 'admin', 'sv', 'supervisor', 'trainer'):
            self.assertTrue(
                access.has_write_capability(access.capabilities_from_otp_role(role)),
                '%s должен править справочники' % role)

    def test_readers_may_not(self):
        for role in ('operator', 'trainee', ''):
            self.assertFalse(
                access.has_write_capability(access.capabilities_from_otp_role(role)),
                '%s правит справочники, а не должен' % role)

    def test_department_head_included(self):
        caps = access.capabilities_from_otp_role('admin', is_department_head=True)
        self.assertTrue(access.has_write_capability(caps))

    def test_read_alone_is_not_enough(self):
        """Именно «сверх чтения»: одного can_read мало, любой другой — достаточно."""
        self.assertFalse(access.has_write_capability({'can_read': True}))
        for name in ('can_create', 'can_edit', 'can_delete', 'can_publish',
                     'can_approve', 'can_manage_structure', 'can_manage_access'):
            self.assertTrue(access.has_write_capability({name: True}), name)

    def test_capability_from_wiki_role_counts(self):
        """Способность может прийти от роли вики — такой человек тоже правит.

        Поэтому правило считается по фактическим способностям, а не по списку
        ролей OTP.
        """
        caps = access.resolve_capabilities('operator', [{'can_edit': True}])
        self.assertTrue(access.has_write_capability(caps))


class RuleUniquenessDDLTest(unittest.TestCase):
    """Ключ уникальности правила заводится СРАЗУ с уровнем должности.

    Инцидент 19.08.2026. Базовый DDL создавал ключ БЕЗ уровня, а миграция ниже
    его дропала и ставила правильный. Пока на разделе не появлялось двух правил
    с одним субъектом и разными порогами, расхождение молчало. Стоило владельцу
    добавить строку «Тренер» рядом с «Оператор» на том же отделе — и
    `CREATE UNIQUE INDEX uq_wiki_section_rule_subject` начал падать
    UniqueViolation на КАЖДОМ старте прода.

    Коварство в том, что приложение при этом работает: таблицы на месте, запросы
    идут. Но вся init_wiki_schema обёрнута ОДНИМ савпоинтом, поэтому откатывалась
    схема целиком — то есть ни одна новая миграция раздела больше не применялась
    бы, и узналось бы об этом через недели, по отсутствию новой колонки.

    Тест статический: гоняет текст DDL, базы не требует.
    """

    def ddl(self):
        return '\n'.join(schema._STATEMENTS)

    def test_base_ddl_does_not_create_level_less_unique_index(self):
        created = re.findall(r'CREATE UNIQUE INDEX IF NOT EXISTS (\w+)', self.ddl())
        self.assertNotIn('uq_wiki_section_rule_subject', created,
                         'ключ без уровня снова создаётся базовым DDL — '
                         'схема начнёт откатываться на каждом старте')
        self.assertIn('uq_wiki_section_rule_subject_level', created)

    def test_min_role_level_exists_before_the_index_needs_it(self):
        """Колонка должна появиться раньше индекса по ней, иначе чистая база не встанет."""
        ddl = self.ddl()
        column = ddl.find('min_role_level')
        index = ddl.find('uq_wiki_section_rule_subject_level')
        self.assertGreater(column, -1, 'min_role_level пропал из базового DDL')
        self.assertLess(column, index, 'индекс по min_role_level стоит раньше самой колонки')

    def test_migration_still_drops_the_legacy_index(self):
        """Базам, где старый ключ уже есть, он по-прежнему снимается."""
        self.assertIn('DROP INDEX IF EXISTS uq_wiki_section_rule_subject;',
                      '\n'.join(schema._ORG_STATEMENTS))


if __name__ == '__main__':
    unittest.main()
