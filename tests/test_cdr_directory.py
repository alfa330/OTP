# -*- coding: utf-8 -*-
"""Справочник «внутренний номер → сотрудник» раздела «Касания».

Главное, что здесь закреплено: **номер уволившегося отдают новому сотруднику**.
В нашей базе при этом остаётся висеть и старая запись — 15 номеров закреплены
сразу за двумя людьми. Подписать звонок двухмесячной давности именем нынешнего
владельца значит соврать в отчёте, поэтому у такого номера два периода, и
касание подписывается тем, кто владел номером в тот день.

Раньше текущего владельца разбирали руками — словарями `MANUAL`/`CURRENT_OWNER`
прямо в коде офлайн-сборки. Здесь этого нет и быть не должно: репозиторий
публичный, ФИО сотрудников в него не коммитятся. Текущего владельца называет
станция (`/agents/map`), а сверка идёт по словам ФИО сразу в двух видах —
кириллицей и в транслите, потому что станция пишет «zhupan_aruzhan», база —
«Жупан Аружан», а казахские буквы в них разные.

ФИО в этом файле выдуманные.

Модуль `cdr.directory` чистый: ни сети, ни базы.
"""

import unittest

from cdr import directory as D


def db_row(ext, name, hire_date=None, direction='Основа ОП'):
    return {'ext': ext, 'name': name, 'hire_date': hire_date,
            'direction': direction, 'department': 'op'}


class WordTests(unittest.TestCase):
    def test_kazakh_letters_do_not_split_a_name(self):
        self.assertTrue(D.names_match('Құрман Әлібек', 'Курман Алибек'))

    def test_word_order_does_not_matter(self):
        self.assertTrue(D.names_match('Мараткызы Молдир', 'Молдир Мараткызы'))

    def test_transliteration_matches_cyrillic(self):
        self.assertTrue(D.names_match('zhupan_aruzhan', 'Жупан Аружан'))

    def test_one_common_word_is_not_a_match(self):
        """Совпадение по распространённому имени — не совпадение по человеку."""
        self.assertFalse(D.names_match('Аружан Сергеева', 'Аружан Тасболат'))

    def test_digits_and_brackets_are_not_part_of_a_name(self):
        cyrillic, _latin = D.name_words('Nurmakhan 6323 (только на станции)')
        self.assertEqual(cyrillic, {'nurmakhan'})


class SingleOwnerTests(unittest.TestCase):
    def test_database_name_wins_when_the_number_is_not_shared(self):
        built = D.build_directory([db_row('6474', 'Жупан Аружан')],
                                  {'6474': 'zhupan_aruzhan'})
        self.assertEqual(built['6474']['source'], 'база')
        self.assertEqual(D.resolver(built)('6474', '2026-08-24'),
                         ('Жупан Аружан', 'Основа ОП'))

    def test_number_known_only_to_the_station_is_marked_as_such(self):
        """Человека нет в нашей базе. Показать транслит с пометкой честнее, чем
        промолчать: по номеру видно, что звонок был и чей он."""
        built = D.build_directory([], {'6704': 'mekemov_alikhan'})
        name, direction = D.resolver(built)('6704', '2026-08-24')
        self.assertEqual(name, 'Mekemov Alikhan (только на станции)')
        self.assertEqual(direction, D.UNKNOWN_DIRECTION)

    def test_queues_are_not_people(self):
        built = D.build_directory([db_row('3001', 'Очередь продаж')],
                                  {'3001': 'sales_queue'})
        self.assertNotIn('3001', built)

    def test_unknown_number_is_named_honestly(self):
        name, direction = D.resolver({})('6715', '2026-08-24')
        self.assertEqual(name, 'Неизвестный номер 6715')
        self.assertEqual(direction, 'нет в справочнике')


class ReusedNumberTests(unittest.TestCase):
    """Номер уволившегося отдали новому сотруднику."""

    ROWS = [db_row('6656', 'Прежний Владелец', '2024-02-01'),
            db_row('6656', 'Зинеден Аружан', '2025-06-10')]

    def test_station_names_the_current_owner(self):
        built = D.build_directory(self.ROWS, {'6656': 'zineden_aruzhan'})
        self.assertEqual(len(built['6656']['periods']), 2)
        resolve = D.resolver(built)
        self.assertEqual(resolve('6656', '2024-09-01')[0], 'Прежний Владелец')
        self.assertEqual(resolve('6656', '2026-08-24')[0], 'Зинеден Аружан')

    def test_boundary_day_belongs_to_the_new_owner(self):
        built = D.build_directory(self.ROWS, {'6656': 'zineden_aruzhan'})
        resolve = D.resolver(built)
        self.assertEqual(resolve('6656', '2025-06-09')[0], 'Прежний Владелец')
        self.assertEqual(resolve('6656', '2025-06-10')[0], 'Зинеден Аружан')

    def test_without_the_station_the_later_hire_is_assumed(self):
        """Станция молчит — берём того, кого наняли позже: у прежнего владельца
        номер уже отобрали. Хуже, чем факт, но лучше, чем монетка."""
        built = D.build_directory(self.ROWS, {})
        self.assertEqual(D.resolver(built)('6656', '2026-08-24')[0], 'Зинеден Аружан')
        self.assertIn('переиспользован', built['6656']['source'])

    def test_station_naming_the_older_owner_overrides_hire_date(self):
        """Если станция держит номер за прежним сотрудником — значит нового за
        ним ещё не закрепили, и верить надо станции, а не датам найма."""
        built = D.build_directory(self.ROWS, {'6656': 'Prezhniy Vladelets'})
        resolve = D.resolver(built)
        self.assertEqual(resolve('6656', '2026-08-24')[0], 'Прежний Владелец')


class OverrideTests(unittest.TestCase):
    def test_manual_override_wins_over_both_sources(self):
        """Правки живут в базе, а не в коде: в публичный репозиторий ФИО
        сотрудников не коммитятся."""
        built = D.build_directory([db_row('6650', 'Из Базы')],
                                  {'6650': 'iz_stantsii'},
                                  overrides={'6650': {'name': 'Правильное ФИО',
                                                      'direction': 'СВ отдела продаж'}})
        self.assertEqual(D.resolver(built)('6650', '2026-08-24'),
                         ('Правильное ФИО', 'СВ отдела продаж'))
        self.assertEqual(built['6650']['source'], 'правка вручную')


class StationNameTests(unittest.TestCase):
    def test_underscores_become_spaces_and_words_are_capitalised(self):
        built = D.build_directory([], {'6704': 'aman_alan'})
        self.assertTrue(built['6704']['periods'][0]['name'].startswith('Aman Alan'))

    def test_already_readable_name_is_left_alone(self):
        built = D.build_directory([], {'6152': 'Kazenov Kaisar'})
        self.assertTrue(built['6152']['periods'][0]['name'].startswith('Kazenov Kaisar'))


if __name__ == '__main__':
    unittest.main()
