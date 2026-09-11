# -*- coding: utf-8 -*-
"""Автосвязка «пользователь amoCRM → сотрудник» (op_funnel/operator_match.py).

Здесь закреплены живые случаи из справочника amoCRM на 11.09.2026 — те самые, на
которых ломается сравнение по имени, и те, где связывать НЕЛЬЗЯ.

Цена ошибки в этом модуле выше обычной: неверная связка ставит чужие сделки в
чужую строку отчёта, и снаружи это выглядит как нормальные цифры. Поэтому правило
одно — связываем только однозначное, всё остальное отдаём человеку.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from op_funnel import operator_match as match

# Сотрудники портала (ФИО как в карточке).
PEOPLE = [
    {'id': 246, 'name': 'Сагидоллаев Нурмахан', 'email': ''},
    {'id': 328, 'name': 'Жолмағанбет Ардақ', 'email': ''},
    {'id': 292, 'name': 'Қыздарбек Дильназ', 'email': ''},
    {'id': 302, 'name': 'Сарсеке Мерей', 'email': ''},
    {'id': 326, 'name': 'Кисапова Айша', 'email': ''},
    {'id': 461, 'name': 'Кенжебай Әділхан', 'email': ''},
    {'id': 374, 'name': 'Жақсылық Нұралы', 'email': 'jaksylyk_nuraly_co@yandextaxi.kz'},
    {'id': 249, 'name': 'Досманбетова Дильназ', 'email': ''},
]


def user(uid, name, email):
    return {'id': uid, 'name': name, 'email': email}


class RoughTests(unittest.TestCase):

    def test_разнописания_транслитерации_сводятся(self):
        # Единой транслитерации нет: одно и то же имя пишут и так, и так.
        self.assertEqual(match.rough('Жолмағанбет'), match.rough('jolmaganbet'))
        self.assertEqual(match.rough('Жолмағанбет'), match.rough('zholmaganbet'))
        self.assertEqual(match.rough('Ардақ'), match.rough('ardaq'))
        self.assertEqual(match.rough('Ардақ'), match.rough('ardak'))
        self.assertEqual(match.rough('Нурмахан'), match.rough('nurmakhan'))
        self.assertEqual(match.rough('Айша'), match.rough('aisha'))

    def test_двойные_буквы_схлопываются(self):
        self.assertEqual(match.rough('Kissapova'), match.rough('Кисапова'))

    def test_казахские_буквы_как_русские(self):
        self.assertEqual(match.rough('Әділхан'), match.rough('Адильхан'))
        self.assertEqual(match.rough('Нұралы'), match.rough('Нуралы'))

    def test_разные_фамилии_не_схлопываются(self):
        self.assertNotEqual(match.rough('Сарсеке'), match.rough('Саркытова'))
        self.assertNotEqual(match.rough('Жакуп'), match.rough('Жомарт'))


class EmailWordsTests(unittest.TestCase):

    def test_служебные_куски_почты_выбрасываются(self):
        words = match.email_words('sarseke_merey_co@yandextaxi.kz')
        self.assertEqual(words, {match.rough('сарсеке'), match.rough('мерей')})

    def test_цифры_однофамильца_не_часть_имени(self):
        # «nurmakhan2» — это второй Нурмахан, а не имя с цифрой.
        words = match.email_words('sagidollayev_nurmakhan2_co@yandextaxi.kz')
        self.assertIn(match.rough('нурмахан'), words)
        self.assertIn(match.rough('сагидоллаев'), words)


class MatchUserTests(unittest.TestCase):

    def link(self, uid, name, email):
        found, how = match.match_user(user(uid, name, email), match.prepare_people(PEOPLE))
        return (found['id'] if found else None), how

    def test_прозвище_вместо_имени_разбирается_по_почте(self):
        # Живые случаи из справочника amoCRM: имя не говорит ничего, почта — всё.
        cases = {
            ('Nurmakhan 6323', 'sagidollayev_nurmakhan2_co@yandextaxi.kz'): 246,
            ('ardak.xo', 'jolmaganbet_ardaq_co@yandextaxi.kz'): 328,
            ('k.dilnaz', 'kyzdarbek_dilnaz_co@yandextaxi.kz'): 292,
            ('merey_sarseke', 'sarseke_merey_co@yandextaxi.kz'): 302,
        }
        for (name, email), expected in cases.items():
            got, how = self.link(1, name, email)
            self.assertEqual(got, expected, '%s / %s' % (name, email))
            self.assertEqual(how, 'email_name')

    def test_латиница_в_имени_и_удвоенная_буква(self):
        got, _ = self.link(2, 'Aisha Kissapova', 'aisha_kisapova_co@yandextaxi.kz')
        self.assertEqual(got, 326)

    def test_порядок_слов_не_важен(self):
        got, _ = self.link(3, 'Айша Кисапова', 'kisapova_aisha_co@yandextaxi.kz')
        self.assertEqual(got, 326)

    def test_казахское_и_русское_написание_фамилии(self):
        got, _ = self.link(4, 'Кенжебай Адильхан', 'kenzebay_adilhan_co@yandextaxi.kz')
        self.assertEqual(got, 461)

    def test_совпадение_почты_буквально(self):
        got, how = self.link(5, 'кто угодно', 'jaksylyk_nuraly_co@yandextaxi.kz')
        self.assertEqual((got, how), (374, 'email'))

    def test_служебная_учётка_не_связывается(self):
        # «ЯР», «Отток группа», «Администратор» — это не люди.
        for name, email in (('ЯР', 'yandex_regisration@gmail.com'),
                            ('Отток группа', 'igroupver@gmail.com'),
                            ('Администратор', 'igroupcc@gmail.com'),
                            ('Фокус группа', 'focus.pokus@yandex.kz')):
            got, how = self.link(6, name, email)
            self.assertIsNone(got, name)
            self.assertEqual(how, 'unknown', name)

    def test_одного_слова_мало(self):
        # «Дана» в отделе не одна: связка по одному слову была бы лотереей.
        got, _ = self.link(7, 'Дана', 'dana@yandextaxi.kz')
        self.assertIsNone(got)

    def test_двое_подходящих_оставляются_человеку(self):
        twins = [
            {'id': 1, 'name': 'Ахметов Дамир', 'email': ''},
            {'id': 2, 'name': 'Ахметов Дамир', 'email': ''},
        ]
        found, how = match.match_user(
            user(8, 'Ахметов Дамир', 'ahmetov_damir_co@yandextaxi.kz'),
            match.prepare_people(twins))
        self.assertIsNone(found)
        self.assertEqual(how, 'ambiguous')

    def test_чужой_человек_не_подставляется(self):
        got, _ = self.link(9, 'Петров Пётр', 'petrov_petr_co@yandextaxi.kz')
        self.assertIsNone(got)


class AutoLinkTests(unittest.TestCase):

    def test_раскладывает_на_три_корзины(self):
        users = [
            user(9357018, 'Nurmakhan 6323', 'sagidollayev_nurmakhan2_co@yandextaxi.kz'),
            user(8403694, 'ЯР', 'yandex_regisration@gmail.com'),
        ]
        result = match.auto_link(users, PEOPLE)
        self.assertEqual(list(result['links']), ['9357018'])
        self.assertEqual(result['links']['9357018']['user_id'], 246)
        self.assertEqual(len(result['unknown']), 1)
        self.assertEqual(result['ambiguous'], [])

    def test_две_учётки_одного_человека_связываются_обе(self):
        # У Кисаповой в amoCRM две учётки — это не ошибка, и сделки обеих должны
        # сложиться в одну строку отчёта.
        users = [
            user(10028670, 'Айша Кисапова', 'kisapova_aisha_co@yandextaxi.kz'),
            user(14126074, 'Aisha Kissapova', 'aisha_kisapova_co@yandextaxi.kz'),
        ]
        result = match.auto_link(users, PEOPLE)
        self.assertEqual(len(result['links']), 2)
        self.assertEqual({item['user_id'] for item in result['links'].values()}, {326})

    def test_пустой_справочник_не_роняет(self):
        self.assertEqual(match.auto_link([], PEOPLE)['links'], {})
        self.assertEqual(match.auto_link(None, None)['links'], {})

    def test_учётка_без_id_пропускается(self):
        result = match.auto_link([user(None, 'Кто-то', 'kto_to@x.kz')], PEOPLE)
        self.assertEqual(result['links'], {})


if __name__ == '__main__':
    unittest.main()
