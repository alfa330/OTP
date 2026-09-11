# -*- coding: utf-8 -*-
"""Границы доступа раздела «Воронка ОП» (op_funnel/access.py).

Раздел показывает телефоны водителей, разбор работы коллег и поимённый рейтинг —
то есть инструмент руководителя, а не личный кабинет. Тест закрепляет обе
границы, которые легко потерять правкой:

1. **По отделу.** Глава чужого отдела и его СВ в воронку продаж не входят, даже
   если базовая роль у них `admin`: назначение главой ЗАМЕНЯЕТ роль и режет
   периметр своим отделом — действующая семантика портала.
2. **По направлению.** Супервайзер видит только свои направления. Подмена
   `?direction=` руками не должна открывать чужое: гейт стоит в роуте, а роут
   спрашивает именно эти функции.

Модуль чистый, поэтому тест не поднимает ни базу, ни Flask.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from op_funnel import access
from op_funnel.schema import DIRECTION_CODES


def ctx(**over):
    """Контекст в том виде, в каком его отдаёт queries.load_access_context."""
    base = {
        'user_id': 1,
        'role': 'operator',
        'department_id': None,
        'department_code': '',
        'headed_department_ids': [],
        'headed_department_codes': [],
        'supervisor_group_ids': [],
        'group_directions': {},
        'direction_code': '',
    }
    base.update(over)
    return base


ADMIN = ctx(user_id=2, role='super_admin')
GLOBAL_ADMIN = ctx(user_id=3, role='admin')
SALES_HEAD = ctx(user_id=244, role='admin', department_id=367, department_code='op',
                 headed_department_ids=[367], headed_department_codes=['op'])
SZOV_HEAD = ctx(user_id=5, role='admin', department_id=1, department_code='szov',
                headed_department_ids=[1], headed_department_codes=['szov'])
# СВ «Потока»: группы 14 (Поток-1) и 38 (Поток-2) — одно направление, две базы.
POTOK_SV = ctx(user_id=243, role='sv', department_id=367, department_code='op',
               supervisor_group_ids=[14, 38],
               group_directions={14: 'op_potok', 38: 'op_potok', 13: 'op_verificator'})
VERIFICATOR_SV = ctx(user_id=402, role='sv', department_id=367, department_code='op',
                     supervisor_group_ids=[13],
                     group_directions={13: 'op_verificator', 14: 'op_potok'})
SZOV_SV = ctx(user_id=7, role='sv', department_id=1, department_code='szov')
OPERATOR = ctx(user_id=8, role='operator', department_id=367, department_code='op')
TRAINER = ctx(user_id=9, role='trainer', department_id=367, department_code='op')


class SectionGateTests(unittest.TestCase):

    def test_админам_открыт(self):
        self.assertTrue(access.can_open_section(ADMIN))
        self.assertTrue(access.can_open_section(GLOBAL_ADMIN))

    def test_главе_продаж_открыт(self):
        self.assertTrue(access.can_open_section(SALES_HEAD))

    def test_главе_чужого_отдела_закрыт(self):
        # Даже с базовой ролью admin: назначение главой заменяет роль и режет
        # периметр своим отделом.
        self.assertFalse(access.can_open_section(SZOV_HEAD))

    def test_св_продаж_открыт(self):
        self.assertTrue(access.can_open_section(POTOK_SV))
        self.assertTrue(access.can_open_section(VERIFICATOR_SV))

    def test_св_чужого_отдела_закрыт(self):
        self.assertFalse(access.can_open_section(SZOV_SV))

    def test_оператору_и_тренеру_закрыт(self):
        # В выгрузке телефоны водителей и разбор работы коллег.
        self.assertFalse(access.can_open_section(OPERATOR))
        self.assertFalse(access.can_open_section(TRAINER))

    def test_пустой_контекст_не_открывает_раздел(self):
        self.assertFalse(access.can_open_section({}))


class VisibleDirectionsTests(unittest.TestCase):

    def test_админ_и_глава_видят_все_четыре(self):
        self.assertEqual(access.visible_directions(ADMIN), DIRECTION_CODES)
        self.assertEqual(access.visible_directions(SALES_HEAD), DIRECTION_CODES)

    def test_св_видит_только_свои(self):
        self.assertEqual(access.visible_directions(POTOK_SV), ('op_potok',))
        self.assertEqual(access.visible_directions(VERIFICATOR_SV), ('op_verificator',))

    def test_две_группы_одного_направления_не_дублируются(self):
        # Группы 14 и 38 — «Поток» и «Поток 2», одно направление.
        self.assertEqual(len(access.visible_directions(POTOK_SV)), 1)

    def test_порядок_направлений_стабильный(self):
        many = ctx(user_id=10, role='sv', department_code='op',
                   supervisor_group_ids=[13, 14, 36],
                   group_directions={13: 'op_verificator', 14: 'op_potok', 36: 'op_osnova'})
        result = access.visible_directions(many)
        self.assertEqual(result, tuple(c for c in DIRECTION_CODES if c in set(result)))

    def test_ключи_групп_строками_тоже_работают(self):
        # Контекст мог приехать через JSON (кеш, лог, фикстура), а там int-ключи
        # словаря становятся строками.
        json_like = ctx(user_id=11, role='sv', department_code='op',
                        supervisor_group_ids=['14'],
                        group_directions={'14': 'op_potok'})
        self.assertEqual(access.visible_directions(json_like), ('op_potok',))

    def test_св_без_групп_видит_своё_направление(self):
        # Штатный случай: группу архивировали, членство закрыли датой, человека
        # только что перевели. Пустой экран он объяснить не сможет.
        lonely = ctx(user_id=12, role='sv', department_code='op', direction_code='op_osnova')
        self.assertEqual(access.visible_directions(lonely), ('op_osnova',))

    def test_закрытому_разделу_направления_не_выдаются(self):
        self.assertEqual(access.visible_directions(OPERATOR), ())
        self.assertEqual(access.visible_directions(SZOV_HEAD), ())


class DirectionGateTests(unittest.TestCase):

    def test_св_не_откроет_чужое_направление_подменой_адреса(self):
        self.assertTrue(access.can_see_direction(POTOK_SV, 'op_potok'))
        self.assertFalse(access.can_see_direction(POTOK_SV, 'op_osnova'))
        self.assertFalse(access.can_see_direction(VERIFICATOR_SV, 'op_potok'))

    def test_неизвестный_код_это_отказ_а_не_показать_всё(self):
        for value in ('', None, 'op_unknown', 'DROP TABLE', 'op_potok; --'):
            self.assertFalse(access.can_see_direction(SALES_HEAD, value), repr(value))

    def test_глава_видит_любое_направление_своего_отдела(self):
        for code in DIRECTION_CODES:
            self.assertTrue(access.can_see_direction(SALES_HEAD, code), code)


class RightsTests(unittest.TestCase):

    def test_нормы_правит_только_руководитель(self):
        # Норма — это обязательство, а не настройка экрана: СВ не должен менять
        # себе план.
        self.assertTrue(access.can_edit_targets(ADMIN))
        self.assertTrue(access.can_edit_targets(SALES_HEAD))
        self.assertFalse(access.can_edit_targets(POTOK_SV))

    def test_сопоставлять_операторов_может_и_св(self):
        # Своих людей он знает лучше всех, а без сопоставления его таб пустой.
        self.assertTrue(access.can_map_operators(POTOK_SV))
        self.assertTrue(access.can_map_operators(SALES_HEAD))
        self.assertFalse(access.can_map_operators(OPERATOR))

    def test_выгрузку_запускает_тот_кто_читает(self):
        # Выгрузка — не привилегия, а способ увидеть данные, которых ещё нет.
        self.assertEqual(access.can_sync(POTOK_SV), access.can_open_section(POTOK_SV))
        self.assertFalse(access.can_sync(OPERATOR))

    def test_экспорт_как_чтение(self):
        self.assertTrue(access.can_export(POTOK_SV))
        self.assertFalse(access.can_export(SZOV_SV))

    def test_ручную_загрузку_делает_св_своего_направления(self):
        self.assertTrue(access.can_import_manual(VERIFICATOR_SV))
        self.assertFalse(access.can_import_manual(OPERATOR))


class CapabilitiesTests(unittest.TestCase):

    def test_сводка_для_фронта_собирается(self):
        caps = access.capabilities(POTOK_SV)
        self.assertTrue(caps['can_open'])
        self.assertFalse(caps['can_edit_targets'])
        self.assertIn('op_potok', caps['directions'])

    def test_у_закрытого_раздела_всё_ложно(self):
        caps = access.capabilities(OPERATOR)
        self.assertFalse(caps['can_open'])
        self.assertEqual(tuple(caps['directions']), ())

    def test_кнопки_рисуются_по_этой_сводке_а_не_по_роли(self):
        # Один источник правды: правило меняется в access.py, а не в трёх местах
        # интерфейса. Значит все права раздела обязаны быть в сводке.
        caps = access.capabilities(SALES_HEAD)
        for key in ('can_open', 'can_sync', 'can_export', 'can_map_operators',
                    'can_edit_targets', 'can_import_manual', 'directions'):
            self.assertIn(key, caps, key)


if __name__ == '__main__':
    unittest.main()
