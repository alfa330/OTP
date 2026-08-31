# -*- coding: utf-8 -*-
"""Периметр раздела «Посылки»: два отдела с разными правами на одни записи.

Раздел не похож на остальные тем, что вход в него открыт ДВУМ отделам, а запись
— только одному. Такой разрыв легко потерять при рефакторинге: достаточно
заменить `can_edit` на `can_open_section` в одном декораторе, и оператор СЗоВ
начнёт править карточки чужого офиса — молча, без ошибки.

Поэтому проверяются именно границы:
  * фронт-офис пишет, СЗоВ только читает — в любой роли, включая супервайзера;
  * тренер не входит вовсе;
  * глава отдела своим отделом и ограничен;
  * QR спрашивается у операторов ОБОИХ отделов (прямое требование постановки);
  * удаление — только у глобального админа.
"""

import unittest
from pathlib import Path

from parcels import access, queries, schema

ROOT = Path(__file__).resolve().parents[1]
APP_JSX = ROOT / 'src' / 'App.jsx'


def ctx(role='operator', user_id=10, department_code='front_office',
        headed=(), headed_codes=None, city=None):
    """Портрет сотрудника. По умолчанию — менеджер фронт-офиса."""
    return {
        'user_id': user_id,
        'name': 'Тест',
        'role': role,
        'department_id': 909,
        'department_code': department_code,
        'city': city,
        'headed_department_ids': list(headed),
        'headed_department_codes': (list(headed_codes) if headed_codes is not None
                                   else ([department_code] if headed else [])),
    }


class SectionEntryTests(unittest.TestCase):
    def test_both_departments_get_in(self):
        self.assertTrue(access.can_open_section(ctx(department_code='front_office')))
        self.assertTrue(access.can_open_section(ctx(department_code='szov')))

    def test_other_departments_do_not(self):
        for code in ('op', 'tez', 'marketing', None, ''):
            self.assertFalse(access.can_open_section(ctx(department_code=code)),
                             'отдел %r не должен попадать в раздел' % code)

    def test_trainer_is_out_even_from_a_permitted_department(self):
        """Тренер видит «всё» в других разделах, но телефоны водителей не его дело."""
        self.assertFalse(access.can_open_section(
            ctx(role='trainer', department_code='front_office')))
        self.assertFalse(access.can_open_section(
            ctx(role='trainer', department_code='szov')))
        self.assertFalse(access.can_edit(ctx(role='trainer', department_code='front_office')))

    def test_global_admin_gets_in_from_anywhere(self):
        self.assertTrue(access.can_open_section(ctx(role='super_admin', department_code=None)))
        self.assertTrue(access.can_open_section(ctx(role='admin', department_code=None)))

    def test_head_of_a_foreign_department_stays_out(self):
        """Назначение главой ЗАМЕНЯЕТ базовую admin-роль и режет периметр отделом."""
        head_of_sales = ctx(role='admin', department_code='op', headed=[367],
                            headed_codes=['op'])
        self.assertFalse(access.is_global_admin(head_of_sales))
        self.assertFalse(access.can_open_section(head_of_sales))
        self.assertFalse(access.can_edit(head_of_sales))

    def test_head_of_front_office_writes_head_of_szov_reads(self):
        front = ctx(role='admin', department_code='front_office', headed=[909],
                    headed_codes=['front_office'])
        szov = ctx(role='admin', department_code='szov', headed=[1], headed_codes=['szov'])
        self.assertTrue(access.can_open_section(front))
        self.assertTrue(access.can_edit(front))
        self.assertTrue(access.can_open_section(szov))
        self.assertFalse(access.can_edit(szov))


class WriteBoundaryTests(unittest.TestCase):
    def test_front_office_writes_in_any_role(self):
        for role in ('operator', 'trainee', 'sv', 'supervisor'):
            self.assertTrue(access.can_edit(ctx(role=role, department_code='front_office')),
                            'фронт-офис в роли %s должен вести реестр' % role)

    def test_szov_never_writes_not_even_a_supervisor(self):
        """«Операторам нет необходимости давать возможность редактировать записи».

        Супервайзер СЗоВ здесь тоже читатель: он не видел посылку своими глазами,
        а карточка — свидетельство о вещи в конкретном офисе.
        """
        for role in ('operator', 'trainee', 'sv', 'supervisor'):
            szov = ctx(role=role, department_code='szov')
            self.assertTrue(access.can_open_section(szov))
            self.assertFalse(access.can_edit(szov),
                             'СЗоВ в роли %s не должен править реестр' % role)

    def test_only_global_admin_deletes(self):
        self.assertTrue(access.can_delete(ctx(role='super_admin', department_code=None)))
        self.assertTrue(access.can_delete(ctx(role='admin', department_code=None)))
        self.assertFalse(access.can_delete(ctx(department_code='front_office')))
        self.assertFalse(access.can_delete(
            ctx(role='admin', department_code='front_office', headed=[909],
                headed_codes=['front_office'])))


class SensitiveQrTests(unittest.TestCase):
    """QR спрашивается у операторов ОБОИХ отделов — требование постановки #240."""

    def test_operators_of_both_departments_need_qr(self):
        self.assertTrue(access.requires_sensitive_qr(ctx(department_code='front_office')))
        self.assertTrue(access.requires_sensitive_qr(ctx(department_code='szov')))

    def test_those_who_confirm_are_not_asked(self):
        # Супервайзер и админ подтверждают доступ сами — им подтверждать не у кого.
        self.assertFalse(access.requires_sensitive_qr(ctx(role='sv', department_code='szov')))
        self.assertFalse(access.requires_sensitive_qr(ctx(role='super_admin', department_code=None)))
        self.assertFalse(access.requires_sensitive_qr(
            ctx(role='admin', department_code='front_office', headed=[909],
                headed_codes=['front_office'])))

    def test_unknown_role_is_closed_not_open(self):
        """Незнакомая роль сводится к оператору — правильная сторона ошибки."""
        self.assertTrue(access.requires_sensitive_qr(
            ctx(role='какая-то-новая', department_code='front_office')))


class DefaultCityTests(unittest.TestCase):
    """«У менеджера, у которого указан город, город выбран автоматом»."""

    def test_city_of_the_manager_is_offered(self):
        self.assertEqual(access.default_city(ctx(city='Шымкент')), 'Шымкент')

    def test_empty_city_offers_nothing(self):
        self.assertIsNone(access.default_city(ctx(city=None)))
        self.assertIsNone(access.default_city(ctx(city='   ')))

    def test_reader_gets_no_default_he_has_no_form(self):
        self.assertIsNone(access.default_city(ctx(department_code='szov', city='Алматы')))


class CapabilitiesTests(unittest.TestCase):
    """Фронт рисует кнопки по этой сводке, а не по роли — одно место правды."""

    def test_reader_sees_read_only_capabilities(self):
        payload = access.capabilities(ctx(department_code='szov'))
        self.assertTrue(payload['can_open'])
        self.assertFalse(payload['can_edit'])
        self.assertFalse(payload['can_delete'])
        self.assertTrue(payload['requires_qr'])

    def test_writer_sees_write_capabilities(self):
        payload = access.capabilities(ctx(department_code='front_office', city='Тараз'))
        self.assertTrue(payload['can_edit'])
        self.assertEqual(payload['default_city'], 'Тараз')

    def test_every_key_the_frontend_reads_is_present(self):
        payload = access.capabilities(ctx())
        for key in ('can_open', 'can_edit', 'can_delete', 'requires_qr', 'default_city'):
            self.assertIn(key, payload)


class SchemaTests(unittest.TestCase):
    def test_statuses_and_kinds_match_the_ddl_check(self):
        """CHECK в DDL и кортеж в модуле — одна и та же правда.

        Расхождение не упало бы на старте: раздел принял бы статус, который база
        отвергнет уже при вставке, и менеджер увидел бы «внутреннюю ошибку» на
        сохранении.
        """
        ddl = '\n'.join(schema._STATEMENTS)
        for status in schema.PARCEL_STATUSES:
            self.assertIn("'%s'" % status, ddl)
        for kind in schema.PARCEL_KINDS:
            self.assertIn("'%s'" % kind, ddl)

    def test_tables_come_before_indexes_in_the_rollout(self):
        """Порядок разворота: таблицы → миграции → индексы.

        На «Обращениях» обратный порядок уронил прод 17.08.2026: индекс по новому
        столбцу выполнился раньше ALTER'а, и откат SAVEPOINT'а отменил весь
        разворот схемы молча.
        """
        statements = schema._STATEMENTS
        last_table = max(index for index, text in enumerate(statements)
                         if schema._is_table(text))
        first_index = min(index for index, text in enumerate(statements)
                          if not schema._is_table(text))
        self.assertLess(last_table, first_index)

    def test_office_link_survives_a_deleted_office(self):
        """Удалённый из справочника офис не должен утаскивать карточки посылок."""
        ddl = '\n'.join(schema._STATEMENTS)
        self.assertIn('REFERENCES wiki_offices(id) ON DELETE SET NULL', ddl)
        # Ради этого рядом и лежит снимок названия с адресом.
        self.assertIn('office_name', ddl)
        self.assertIn('office_address', ddl)


class FrontendAccessTests(unittest.TestCase):
    """Пункт меню, отрисовка раздела и гард видимости — три РАЗНЫХ места.

    Постоянная ловушка портала: предикат доступа возвращает true, бэкенд отдаёт
    данные, раздел открывается прямым адресом — но пункта в меню нет, и снаружи
    это выглядит как «доступ не выдаётся». Так уже было с «Ботом опозданий».

    «Посылки», как «Вики» и «Обращения», объявлены ОДИН раз в общей части меню,
    а не по ролевым ветвям — поэтому тест сторожит и это: два вхождения означали
    бы, что пункт продублировали по ветвям и одна из них рассинхронизируется.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JSX.read_text(encoding='utf-8-sig')

    def test_menu_item_is_declared_exactly_once(self):
        self.assertEqual(
            self.source.count("handleSidebarViewNavigation(e, 'parcels')"), 1,
            'пункт «Посылки» должен стоять один раз, в общей части меню',
        )

    def test_menu_item_is_gated_by_the_section_predicate(self):
        self.assertIn('{canAccessParcelsSection && (', self.source)
        self.assertIn('const canAccessParcelsSection = canAccessParcelsSectionForUser(user);',
                      self.source)

    def test_view_is_rendered_and_wrapped_into_the_qr_gate(self):
        self.assertIn('view === "parcels" && canAccessParcelsSection', self.source)
        self.assertIn('sectionTitle="Посылки"', self.source)

    def test_visibility_guard_lets_the_section_through(self):
        """Без этой строки гард отдела выкинул бы оператора фронт-офиса обратно.

        У front_office жёсткий allowlist разделов, а у СЗоВ его нет вовсе —
        поэтому раздел проходит своим предикатом, как «Обращения».
        """
        self.assertIn("if (view === 'parcels' && canAccessParcelsSection) return;",
                      self.source)

    def test_qr_status_is_requested_before_the_section_is_drawn(self):
        """Иначе замок мигнёт тому, кто доступ уже подтвердил."""
        self.assertIn("view === 'crm_tickets' || view === 'wiki' || view === 'parcels'",
                      self.source)

    def test_both_departments_are_named_in_the_predicate(self):
        self.assertIn("PARCELS_SECTION_DEPARTMENT_CODES = ['front_office', 'szov']",
                      self.source)


class _RecordingCursor:
    def __init__(self, rows=()):
        self.calls = []
        self.rows = list(rows)

    def execute(self, sql, params=None):
        self.calls.append((' '.join(str(sql).split()), params))

    def fetchall(self):
        return list(self.rows)


class DirectorySpaceTests(unittest.TestCase):
    """Справочник офисов принадлежит ПРОСТРАНСТВУ вики (24.08.2026).

    Форма «Посылки» читает его прямым SQL, и без границы в списке городов стоял
    «Tez Taxi» в Туркестане — точка пространства «Тез», на которую фронт-офис
    Таксопарков посылку не принимает.
    """

    def test_both_departments_of_the_section_are_asked(self):
        """Именно ОБА: посылку принимает фронт-офис, а ищет её СЗоВ, и офис у
        них один. Спросив один код, раздел показал бы разным людям разное."""
        cursor = _RecordingCursor([(11,)])
        self.assertEqual(queries.section_space_ids(cursor), [11])
        sql, params = cursor.calls[0]
        self.assertIn('wiki_space_departments', sql)
        self.assertEqual(params, (sorted(access.SECTION_DEPARTMENT_CODES),))

    def test_the_office_query_carries_the_space(self):
        cursor = _RecordingCursor()
        queries.list_offices(cursor, space_ids=[11])
        sql, params = cursor.calls[0]
        self.assertIn('o.space_id = ANY(%(spaces)s)', sql)
        self.assertEqual(params['spaces'], [11])

    def test_without_a_space_the_directory_is_empty_not_global(self):
        """Пусто ≠ «все»: подставить вместо границы весь справочник значит
        вернуть ту же утечку под другим именем. Форма на пустой список отвечает
        понятным «нет офисов в справочнике — заведите офис в разделе «Вики»»."""
        cursor = _RecordingCursor()
        self.assertEqual(queries.list_offices(cursor, space_ids=[]), [])
        self.assertEqual(queries.offices_in_city(cursor, 'Туркестан', space_ids=[]), [])
        self.assertEqual(cursor.calls, [])


if __name__ == '__main__':
    unittest.main()
