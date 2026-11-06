# -*- coding: utf-8 -*-
"""«Доска почёта» во вкладке «Ивентов» (задача #318 и доработка 06.11.2026).

Что сторожится:
  * справочник направлений и номинаций один у сервера и у фронта
    (honor_board/catalog.py ↔ src/components/events/honorBoardMeta.js);
  * номинации у всех трёх направлений устроены одинаково: ключевая закреплена,
    остальные тренер называет сам, меняет им значок, добавляет — вместе с
    ключевой до восьми; номинации ТЗ у ОП и СЗоВ — заготовка первой доски;
  * доски, заполненные до доработки (коды guru_sales…), читаются своими
    номинациями без «есть правки» и без переписывания базы;
  * фото для доски: загрузка, проверка файла, права, ссылка из записи только
    на снимок того же сотрудника;
  * колокол: первая публикация доски будит всех, правки опубликованной — нет,
    публикующему — тишина, заход на доску гасит.

SQL здесь не исполняется — слой запросов подменён памятью; настоящий SQL
проверяет tests/test_honor_board_postgres.py на локальном Postgres.
"""

import io
import json
import shutil
import subprocess
import sys
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

from honor_board import catalog  # noqa: E402
from honor_board import photos as honor_photos  # noqa: E402
from honor_board import queries as honor_queries  # noqa: E402
from honor_board import routes as honor_routes  # noqa: E402

META_JS = ROOT / 'src' / 'components' / 'events' / 'honorBoardMeta.js'
TODAY = date(2026, 11, 4)

# Люди выдуманы.
PEOPLE = {
    1: {'name': 'Тренерова Алия Маратовна', 'role': 'trainer'},
    2: {'name': 'Админов Сергей', 'role': 'super_admin'},
    3: {'name': 'Операторова Дина', 'role': 'operator'},
    4: {'name': 'Абенова Дана Маратовна', 'role': 'operator', 'department_id': 20},
    5: {'name': 'Жумабеков Ерлан Нурланович', 'role': 'operator', 'department_id': 20},
    6: {'name': 'Ким Виктория', 'role': 'operator', 'department_id': 20},
    7: {'name': 'Руководов Руслан', 'role': 'admin'},
    8: {'name': 'Супервайзерова Сауле', 'role': 'sv'},
    9: {'name': 'Главова Фарида', 'role': 'admin'},       # глава отдела «Фронт-офис»
    10: {'name': 'Главов Олжас', 'role': 'admin'},        # глава отдела ОП
    11: {'name': 'Бекова Жанна', 'role': 'operator', 'department_id': 30},
    12: {'name': 'Уволенов Борис', 'role': 'operator', 'department_id': 20, 'fired': True},
}
HEADED = {9: ['front_office'], 10: ['op']}
DEPARTMENT_CODES = {20: 'op', 30: 'front_office'}


def entry(nomination, **fields):
    return {'nomination': nomination, **fields}


class CatalogMirrorTests(unittest.TestCase):
    """Справочник фронта — тот же, что у сервера: доска не назовёт номинацию иначе."""

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required')
    def test_frontend_catalog_matches_server(self):
        script = (
            "import('" + META_JS.as_uri() + "').then((m) => console.log(JSON.stringify({"
            "departments: m.HONOR_DEPARTMENTS, main: m.MAIN_NOMINATION, custom: m.CUSTOM_NOMINATION,"
            "mainTitle: m.MAIN_TITLE, mainReason: m.MAIN_REASON, mainIcon: m.MAIN_ICON, defaultIcon: m.DEFAULT_ICON,"
            "icons: m.HONOR_ICONS,"
            "limits: [m.NAME_MAX, m.TITLE_MAX, m.DESCRIPTION_MAX, m.MAX_NOMINATIONS],"
            "managers: m.HONOR_MANAGER_ROLES, heads: m.HONOR_HEAD_MANAGED_CODES})))"
        )
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script],
                                capture_output=True, text=True, encoding='utf-8', check=True)
        front = json.loads(result.stdout)
        # Догадка фронта для первого кадра — то же правило «кто ведёт», что у сервера.
        self.assertEqual(sorted(front['managers']), sorted(honor_routes.MANAGER_ROLES))
        self.assertEqual(front['heads'], sorted(set(honor_routes.HEAD_MANAGED_DEPARTMENTS.values())))
        self.assertEqual(
            (front['main'], front['custom'], front['mainTitle'], front['mainReason'], front['mainIcon'],
             front['defaultIcon']),
            (catalog.MAIN_NOMINATION, catalog.CUSTOM_NOMINATION, catalog.MAIN_TITLE, catalog.MAIN_REASON,
             catalog.MAIN_ICON, catalog.DEFAULT_ICON),
        )
        self.assertEqual(front['icons'], list(catalog.ICONS))
        self.assertEqual(front['limits'], [catalog.NAME_MAX, catalog.TITLE_MAX, catalog.DESCRIPTION_MAX,
                                           catalog.MAX_NOMINATIONS])
        self.assertEqual(
            [(d['key'], d['label'], d['title']) for d in front['departments']],
            [(d['key'], d['label'], d['title']) for d in catalog.DEPARTMENTS],
        )
        for front_department, server_department in zip(front['departments'], catalog.DEPARTMENTS):
            self.assertEqual(
                [(n['code'], n['title'], n['icon'], n['reason']) for n in front_department['presets']],
                [tuple(n) for n in server_department['presets']],
                server_department['key'],
            )

    def test_tz_nominations_are_presets(self):
        # ТЗ: номинации ОП и СЗоВ — заготовка первой доски; у Фронт-офиса их нет.
        self.assertEqual(catalog.DEPARTMENT_KEYS, ('op', 'szov', 'front_office'))
        self.assertEqual([title for title, _icon in catalog.presets('op')],
                         ['Гуру продаж', 'Мастер ЯР', 'Лучший верификатор'])
        self.assertEqual([title for title, _icon in catalog.presets('szov')],
                         ['Чемпион по продуктивности', 'Первоклассный менеджер', 'Звезда чат-направления'])
        self.assertEqual(catalog.presets('front_office'), [])
        self.assertEqual(catalog.presets('hr'), [])
        # Прежние коды досок до 06.11.2026 — все шесть, с названиями ТЗ.
        self.assertEqual(catalog.LEGACY_NOMINATIONS['guru_sales'], ('Гуру продаж', 'TrendingUp'))
        self.assertEqual(len(catalog.LEGACY_NOMINATIONS), 6)
        # Корона — только у ключевой: в выборе значка её нет.
        self.assertNotIn(catalog.MAIN_ICON, catalog.ICONS)
        self.assertIn(catalog.DEFAULT_ICON, catalog.ICONS)
        for _code, _title, icon, _reason in [n for d in catalog.DEPARTMENTS for n in d['presets']]:
            self.assertIn(icon, catalog.ICONS)

    def test_reason_only_for_main_and_unchanged_preset(self):
        self.assertEqual(catalog.nomination_reason('op', 'best_employee', ''), catalog.MAIN_REASON)
        self.assertIn('продажах', catalog.nomination_reason('op', 'custom', 'Гуру продаж'))
        # Переименовали — «за что» из ТЗ уже не про неё.
        self.assertEqual(catalog.nomination_reason('op', 'custom', 'Гуру продаж 2.0'), '')
        # Название ТЗ другого направления — не своё.
        self.assertEqual(catalog.nomination_reason('szov', 'custom', 'Гуру продаж'), '')


class PeriodTests(unittest.TestCase):
    def test_parse_period_bounds(self):
        self.assertEqual(catalog.parse_period('2026-10', TODAY), date(2026, 10, 1))
        self.assertEqual(catalog.parse_period(' 2025-01 ', TODAY), date(2025, 1, 1))
        # Текущий месяц ещё не закончился: итоги — «по итогам прошедшего месяца».
        for raw in ('2026-11', '2026-12', '2024-12', '2026-13', '2026-1', '2026/10', '', None, 'abc'):
            self.assertIsNone(catalog.parse_period(raw, TODAY), raw)

    def test_default_period_is_previous_month(self):
        self.assertEqual(catalog.default_period(TODAY), date(2026, 10, 1))
        self.assertEqual(catalog.default_period(date(2027, 1, 15)), date(2026, 12, 1))

    def test_selectable_periods_start_at_last_closed_month(self):
        periods = catalog.selectable_periods(TODAY)
        self.assertEqual(periods[0], date(2026, 10, 1))
        self.assertEqual(periods[-1], catalog.FIRST_PERIOD)
        self.assertEqual(len(periods), 22)
        self.assertEqual(len(catalog.selectable_periods(date(2030, 6, 1))), catalog.SELECTABLE_MONTHS)

    def test_period_label(self):
        self.assertEqual(catalog.period_label(date(2026, 9, 1)), 'Сентябрь 2026')
        self.assertEqual(catalog.period_key(date(2026, 9, 1)), '2026-09')
        self.assertEqual(catalog.period_genitive(date(2026, 10, 1)), 'октября')


class EntryRulesTests(unittest.TestCase):
    def test_main_first_own_nominations_in_trainer_order(self):
        entries = catalog.normalize_entries('op', [
            entry('custom', title='  Лучший   верификатор ', icon='BadgeCheck', user_id=6, name=' Виктория   Ким '),
            entry('best_employee', title='Чужое название', icon='Star', user_id='4', name='Дана Абенова',
                  description='Строка 1   \r\n\r\n\r\n\tСтрока 2  '),
            entry('custom', title='Гуру продаж'),
        ])
        self.assertEqual([(e['nomination'], e['title'], e['icon']) for e in entries], [
            ('best_employee', 'Лучший сотрудник отдела', 'Crown'),
            ('custom', 'Лучший верификатор', 'BadgeCheck'),
            ('custom', 'Гуру продаж', 'Award'),
        ])
        self.assertEqual(entries[0]['user_id'], 4)
        self.assertEqual(entries[0]['description'], 'Строка 1\n\n Строка 2')
        self.assertEqual(entries[1]['name'], 'Виктория Ким')
        # Ключевой в запросе нет — строка всё равно первая и пустая.
        only_custom = catalog.normalize_entries('szov', [entry('custom', title='Свое')])
        self.assertEqual(only_custom[0], catalog.blank_entry('best_employee', catalog.MAIN_TITLE))

    def test_icon_outside_the_list_falls_back(self):
        entries = catalog.normalize_entries('front_office', [
            entry('custom', title='А', icon='Skull'), entry('custom', title='Б', icon=12),
            entry('custom', title='В', icon=' Gem '), entry('custom', title='Г', icon='Crown'),
        ])
        self.assertEqual([e['icon'] for e in entries[1:]], ['Award', 'Award', 'Gem', 'Award'])

    def test_legacy_codes_are_own_nominations(self):
        # Окно старой сборки (до обновления страницы) шлёт прежние коды ОП.
        entries = catalog.normalize_entries('op', [
            entry('best_employee', user_id=4, name='Дана'),
            entry('master_yr', title='Мастер ЯР', user_id=5, name='Ерлан'),
            entry('best_verifier'),
        ])
        self.assertEqual([(e['nomination'], e['title'], e['icon']) for e in entries], [
            ('best_employee', 'Лучший сотрудник отдела', 'Crown'),
            ('custom', 'Мастер ЯР', 'Car'),
            ('custom', 'Лучший верификатор', 'BadgeCheck'),
        ])

    def test_photo_belongs_to_a_chosen_person(self):
        entries = catalog.normalize_entries('op', [
            entry('best_employee', user_id=4, photo_id='17'),
            entry('custom', title='Без человека', photo_id=18),
        ])
        self.assertEqual((entries[0]['photo_id'], entries[1]['photo_id']), (17, None))

    def test_bad_entries_are_rejected(self):
        eight = [entry('custom', title=f'Номинация {index}') for index in range(catalog.MAX_NOMINATIONS)]
        cases = [
            ('op', [entry('best_employee'), entry('best_employee')], 'повторяется'),
            ('op', [entry('retired_award', title='Старая')], 'Неизвестная'),
            ('op', {'nomination': 'best_employee'}, 'неверно'),
            ('op', ['строка'], 'неверно'),
            ('op', [entry('best_employee', user_id=True)], 'Сотрудник'),
            ('op', [entry('best_employee', user_id=-3)], 'Сотрудник'),
            ('op', [entry('best_employee', user_id='①')], 'Сотрудник'),
            ('op', [entry('best_employee', user_id=4, photo_id='x')], 'Фото'),
            ('op', [entry('best_employee', user_id=4, photo_id=0)], 'Фото'),
            ('op', [entry('best_employee', name='я' * (catalog.NAME_MAX + 1))], 'Имя'),
            ('op', [entry('best_employee', description='я' * (catalog.DESCRIPTION_MAX + 1))], 'Описание'),
            ('szov', [entry('custom', title='я' * (catalog.TITLE_MAX + 1))], 'Название'),
            # Не строки в текстовых полях — ошибка, а не str() от объекта.
            ('op', [entry('best_employee', name={'a': 1})], 'неверно'),
            ('op', [entry('best_employee', description=12)], 'неверно'),
            ('front_office', [entry('custom', title=['Лучший'])], 'неверно'),
            # Восемь своих и ключевая — девять номинаций, больше потолка.
            ('op', eight, 'не больше'),
            ('op', eight + [entry('best_employee')], 'Слишком много'),
            ('hr', [], 'направление'),
        ]
        for department_key, raw, fragment in cases:
            with self.subTest(raw=str(raw)[:60]):
                with self.assertRaises(catalog.EntriesError) as caught:
                    catalog.normalize_entries(department_key, raw)
                self.assertIn(fragment, str(caught.exception))

    def test_every_department_holds_up_to_eight(self):
        # Просьба 06.11.2026: у колл-центра «только 4» — добавлять самим, если нужно больше.
        own = [entry('custom', title=f'Номинация {index}') for index in range(catalog.MAX_NOMINATIONS - 1)]
        for department_key in catalog.DEPARTMENT_KEYS:
            entries = catalog.normalize_entries(department_key, own + [entry('best_employee', user_id=4)])
            self.assertEqual(len(entries), catalog.MAX_NOMINATIONS, department_key)
            self.assertEqual(entries[0]['nomination'], 'best_employee')
        self.assertEqual(catalog.MAX_NOMINATIONS, 8)

    def test_blank_slots(self):
        # Первая доска ОП и СЗоВ — номинации ТЗ; Фронт-офиса — одна пустая своя.
        self.assertEqual([(e['title'], e['icon']) for e in catalog.blank_slots('szov')], [
            ('Лучший сотрудник отдела', 'Crown'), ('Чемпион по продуктивности', 'Zap'),
            ('Первоклассный менеджер', 'HeartHandshake'), ('Звезда чат-направления', 'MessageCircleHeart'),
        ])
        self.assertEqual(catalog.blank_slots('front_office'),
                         [catalog.blank_entry('best_employee', catalog.MAIN_TITLE), catalog.blank_entry('custom')])
        # Свои номинации прошлого месяца — без победителей, пустые названия не в счёт,
        # не больше потолка; значки — свои.
        previous = [('Лучший кассир', 'Star'), ('', 'Gem'), ('  ', 'Gem'), 'А', ('Б', 'Нет такого'),
                    ('В', 'Rocket'), ('Г', 'Zap'), ('Д', 'Zap'), ('Е', 'Zap'), ('Ж', 'Zap')]
        slots = catalog.blank_slots('op', previous)
        self.assertEqual([(e['title'], e['icon']) for e in slots], [
            ('Лучший сотрудник отдела', 'Crown'), ('Лучший кассир', 'Star'), ('А', 'Award'), ('Б', 'Award'),
            ('В', 'Rocket'), ('Г', 'Zap'), ('Д', 'Zap'), ('Е', 'Zap'),
        ])
        self.assertTrue(all(not catalog.entry_has_content(e) for e in slots))
        self.assertEqual(catalog.blank_slots('hr'), [])

    def test_restore_slots_reads_legacy_and_skips_what_does_not_fit(self):
        stored = [
            {'nomination': 'retired_award', 'user_id': 4, 'name': 'Дана'},
            {'nomination': 'guru_sales', 'title': 'Гуру продаж', 'user_id': 5, 'name': 'Ерлан',
             'description': 'я' * 999},
            {'nomination': 'master_yr', 'title': 'Мастер ЯР', 'user_id': 6, 'name': 'Виктория'},
            {'nomination': 'best_employee', 'title': 'Лучший сотрудник отдела', 'user_id': 6, 'name': 'Виктория'},
            'мусор',
        ]
        entries = catalog.restore_slots('op', stored)
        # Описание длиннее нынешнего предела не роняет окно — строка просто пропущена.
        self.assertEqual([(e['nomination'], e['title'], e['icon'], e['user_id']) for e in entries], [
            ('best_employee', 'Лучший сотрудник отдела', 'Crown', 6),
            ('custom', 'Мастер ЯР', 'Car', 6),
        ])
        self.assertEqual(catalog.restore_slots('op', None), catalog.blank_slots('op'))
        self.assertEqual(catalog.restore_slots('op', []), [catalog.blank_entry('best_employee', catalog.MAIN_TITLE)])
        self.assertEqual(catalog.restore_slots('hr', stored), [])

    def test_revert_keeps_own_nomination_without_winner(self):
        for department_key in ('front_office', 'op'):
            draft = catalog.normalize_entries(department_key, [
                entry('best_employee', user_id=4, name='Дана', description='Черновой стих'),
                entry('custom', title='Лучший наставник', icon='Medal', user_id=5, name='Ерлан', description='Учит'),
                entry('custom', title='Скорость ответа', icon='Zap'),
            ])
            published = catalog.published_snapshot(catalog.normalize_entries(department_key, [
                entry('best_employee', user_id=4, name='Дана', description='Стих'),
                entry('custom', title='Лучший наставник', icon='Medal', user_id=5, name='Ерлан', description='Учит'),
            ]))
            reverted = catalog.revert_slots(department_key, published, draft)
            self.assertEqual([(e['title'], e['icon'], e['description']) for e in reverted], [
                ('Лучший сотрудник отдела', 'Crown', 'Стих'), ('Лучший наставник', 'Medal', 'Учит'),
                ('Скорость ответа', 'Zap', ''),
            ], department_key)
            self.assertEqual(catalog.published_snapshot(reverted), published)

    def test_revert_does_not_double_a_nomination_and_keeps_the_cap(self):
        published = catalog.published_snapshot(catalog.normalize_entries('op', [
            entry('best_employee', user_id=4, name='Дана', description='Стих'),
            entry('custom', title='X', icon='Gem', user_id=5, name='Ерлан', description='Учит'),
        ]))
        # В черновике у «X» победителя убрали, название осталось.
        draft = catalog.normalize_entries('op', [entry('best_employee', user_id=4), entry('custom', title='X')])
        reverted = catalog.revert_slots('op', published, draft)
        self.assertEqual([(e['title'], e['user_id']) for e in reverted],
                         [('Лучший сотрудник отдела', 4), ('X', 5)])
        full = catalog.published_snapshot(catalog.normalize_entries('op', [entry('best_employee', user_id=4, name='Д', description='С')] + [
            entry('custom', title=f'П{index}', user_id=5, name='Е', description='С') for index in range(7)]))
        many = catalog.normalize_entries('op', [entry('custom', title=f'Пустая {index}') for index in range(7)])
        self.assertEqual(len(catalog.revert_slots('op', full, many)), catalog.MAX_NOMINATIONS)

    def test_publish_issues(self):
        self.assertEqual(catalog.publish_issues(catalog.blank_slots('op')),
                         [{'index': None, 'field': None, 'message': 'Выберите хотя бы одного победителя'}])
        entries = catalog.normalize_entries('op', [
            entry('best_employee', user_id=4, name='Дана Абенова', description='Лучшая'),
            # Название с прошлого месяца без победителя — не в счёт, на доску не выйдет.
            entry('custom', title='Лучший кассир'),
            # Победитель есть, а названия нет.
            entry('custom', title='', user_id=5, name='Ерлан', description='Быстрый'),
            entry('custom', title='Душа офиса', description='Стих'),
        ])
        self.assertEqual(catalog.publish_issues(entries), [
            {'index': 2, 'field': 'title', 'message': 'Назовите номинацию'},
            {'index': 3, 'field': 'user', 'message': 'Выберите сотрудника'},
            {'index': 3, 'field': 'name', 'message': 'Впишите имя и фамилию'},
        ])

    def test_snapshot_keeps_only_filled_rows(self):
        entries = catalog.normalize_entries('op', [
            entry('custom', title='Мастер ЯР', icon='Car', user_id=5, name='Ерлан Жумабеков', description='Анкеты',
                  photo_id=9),
            entry('custom', title='Пустая'),
        ])
        self.assertEqual(catalog.published_snapshot(entries), [{
            'nomination': 'custom', 'title': 'Мастер ЯР', 'icon': 'Car', 'user_id': 5,
            'name': 'Ерлан Жумабеков', 'description': 'Анкеты', 'photo_id': 9,
        }])

    def test_legacy_published_board_is_not_changed(self):
        """Доска, выложенная до доработки: снимок и черновик с кодами ОП. Окно
        тренера не должно показывать «есть правки» только оттого, что код
        guru_sales теперь читается своей номинацией со значком."""
        draft = [
            {'nomination': 'best_employee', 'title': 'Лучший сотрудник отдела', 'user_id': 4, 'name': 'Дана',
             'description': 'Стих'},
            {'nomination': 'guru_sales', 'title': 'Гуру продаж', 'user_id': 5, 'name': 'Ерлан', 'description': 'Рекорд'},
            {'nomination': 'master_yr', 'title': 'Мастер ЯР', 'user_id': None, 'name': '', 'description': ''},
        ]
        published = draft[:2]
        self.assertEqual(honor_routes.board_state('op', draft, published, datetime(2026, 11, 5)), 'published')
        cards = honor_routes.public_entries(published)
        self.assertEqual([(c['nomination'], c['title'], c['icon'], c['main'], c['photo_id']) for c in cards], [
            ('best_employee', 'Лучший сотрудник отдела', 'Crown', True, None),
            ('custom', 'Гуру продаж', 'TrendingUp', False, None),
        ])
        changed = [dict(draft[0], description='Новый стих')] + draft[1:]
        self.assertEqual(honor_routes.board_state('op', changed, published, datetime(2026, 11, 5)), 'changed')
        self.assertIsNone(honor_routes.board_state('op', [draft[2]], None, None))


@unittest.skipIf(Image is None, 'Pillow is required')
class PhotoPrepareTests(unittest.TestCase):
    @staticmethod
    def image_bytes(mode='RGBA', size=(300, 400), fmt='PNG', figure=True, exif=None):
        image = Image.new(mode, size, (0, 0, 0, 0) if mode == 'RGBA' else (200, 210, 220))
        if figure:
            for x in range(100, 200):
                for y in range(80, size[1]):
                    image.putpixel((x, y), (180, 90, 60, 255) if mode == 'RGBA' else (180, 90, 60))
        out = io.BytesIO()
        kwargs = {'exif': exif} if exif is not None else {}
        image.save(out, fmt, **kwargs)
        return out.getvalue()

    def decode(self, data):
        return Image.open(io.BytesIO(data))

    def test_cutout_keeps_transparency(self):
        data, width, height, cutout = honor_photos.prepare_photo(self.image_bytes(), cutout=True)
        image = self.decode(data)
        self.assertEqual((image.format, width, height, cutout), ('WEBP', 300, 400, True))
        self.assertEqual(image.mode, 'RGBA')
        self.assertEqual(image.getpixel((5, 5))[3], 0)

    def test_without_cutout_flag_or_alpha_it_is_a_plain_photo(self):
        _data, _w, _h, cutout = honor_photos.prepare_photo(self.image_bytes(), cutout=False)
        self.assertFalse(cutout)
        data, _w, _h, cutout = honor_photos.prepare_photo(self.image_bytes(mode='RGB', fmt='JPEG'), cutout=True)
        self.assertFalse(cutout)
        self.assertEqual(self.decode(data).mode, 'RGB')

    def test_transparent_file_saved_with_background_is_white_not_black(self):
        data, _w, _h, cutout = honor_photos.prepare_photo(self.image_bytes(fmt='WEBP'), cutout=False)
        image = self.decode(data)
        self.assertFalse(cutout)
        self.assertEqual(image.mode, 'RGB')
        corner = image.getpixel((5, 5))
        self.assertTrue(all(channel > 240 for channel in corner), corner)
        figure = image.getpixel((150, 300))
        self.assertTrue(abs(figure[0] - 180) < 12 and abs(figure[1] - 90) < 12, figure)

    def test_big_photo_is_scaled_and_exif_is_dropped(self):
        exif = Image.Exif()
        exif[0x0110] = 'Phone Model X'   # модель камеры
        exif[0x0112] = 6                 # повёрнут на 90°: ширина и высота меняются местами
        raw = self.image_bytes(mode='RGB', size=(3200, 2400), fmt='JPEG', figure=False, exif=exif.tobytes())
        data, width, height, _cutout = honor_photos.prepare_photo(raw, cutout=False)
        self.assertEqual((width, height), (1200, 1600))
        image = self.decode(data)
        self.assertNotIn(0x0110, image.getexif())

    def test_bad_files_are_refused(self):
        gif = io.BytesIO()
        Image.new('RGB', (100, 100)).save(gif, 'GIF')
        cases = [
            (b'', 'пустой'),
            (b'not an image at all', 'прочитать'),
            (gif.getvalue(), 'прочитать'),
            (self.image_bytes(mode='RGB', size=(40, 400), fmt='PNG', figure=False), 'маленькое'),
            (self.image_bytes(figure=False), 'никого не осталось'),
            (b'\0' * (honor_photos.MAX_UPLOAD_BYTES + 1), 'больше'),
        ]
        for raw, fragment in cases:
            with self.subTest(fragment=fragment):
                with self.assertRaises(honor_photos.PhotoError) as caught:
                    honor_photos.prepare_photo(raw, cutout=True)
                self.assertIn(fragment, str(caught.exception))

    def test_pixel_bomb_is_refused_before_decoding(self):
        # Окно присылает не больше 1600 px; 20 Мп — уже не снимок из окна.
        raw = io.BytesIO()
        Image.new('L', (5000, 4000)).save(raw, 'PNG')
        with self.assertRaises(honor_photos.PhotoError) as caught:
            honor_photos.prepare_photo(raw.getvalue(), cutout=False)
        self.assertIn('слишком большое', str(caught.exception))
        self.assertLessEqual(honor_photos.MAX_PIXELS, 4096 * 4096)


class Store:
    """Память вместо базы: ровно те функции queries, что зовут ручки."""

    def __init__(self):
        self.boards = {}
        self.next_id = 1
        self.departments = {'op': {'id': 20, 'name': 'Отдел продаж'}, 'front_office': {'id': 30, 'name': 'Фронт-офис'}}
        self.photos = {}
        self.reads = {}
        self.clock = datetime(2026, 11, 4, 10, 0)

    def tick(self):
        self.clock = self.clock.replace(minute=self.clock.minute + 1)
        return self.clock

    def _view(self, row):
        names = {key: value['name'] for key, value in PEOPLE.items()}
        return {
            'id': row['id'], 'period_month': row['period_month'], 'department_key': row['department_key'],
            'draft_entries': json.loads(json.dumps(row['draft_entries'])), 'revision': row['revision'],
            'updated_at': row['updated_at'], 'updated_by_name': names.get(row['updated_by']),
            'published_entries': json.loads(json.dumps(row['published_entries'])) if row['published_entries'] is not None else None,
            'published_at': row['published_at'], 'published_by_name': names.get(row['published_by']),
            'first_published_at': row['first_published_at'],
        }

    def board(self, _cursor, period, department_key, *, lock=False):
        row = self.boards.get((period, department_key))
        return self._view(row) if row else None

    def published_periods(self, _cursor):
        return sorted({period for (period, _key), row in self.boards.items() if row['published_at']}, reverse=True)

    def published_boards(self, _cursor, period):
        return [{'department_key': key, 'published_entries': row['published_entries'],
                 'published_at': row['published_at']}
                for (row_period, key), row in self.boards.items() if row_period == period and row['published_at']]

    def boards_between(self, _cursor, first, last):
        return [{'period_month': period, 'department_key': key, 'draft_entries': row['draft_entries'],
                 'published_entries': row['published_entries'], 'published_at': row['published_at']}
                for (period, key), row in self.boards.items() if first <= period <= last]

    def previous_custom_nominations(self, _cursor, period, department_key):
        earlier = sorted((p for (p, key) in self.boards if key == department_key and p < period), reverse=True)
        if not earlier:
            return []
        draft = self.boards[(earlier[0], department_key)]['draft_entries']
        return catalog.custom_nominations(catalog.restore_slots(department_key, draft))

    def insert_board(self, _cursor, period, department_key, entries, actor_id):
        if (period, department_key) in self.boards:
            return None
        board_id = self.next_id
        self.next_id += 1
        self.boards[(period, department_key)] = {
            'id': board_id, 'period_month': period, 'department_key': department_key,
            'draft_entries': json.loads(json.dumps(entries)), 'revision': 1,
            'updated_at': self.tick(), 'updated_by': actor_id,
            'published_entries': None, 'published_at': None, 'published_by': None, 'first_published_at': None,
        }
        return board_id

    def _row(self, board_id):
        return next(row for row in self.boards.values() if row['id'] == board_id)

    def update_draft(self, _cursor, board_id, entries, actor_id):
        row = self._row(board_id)
        row['draft_entries'] = json.loads(json.dumps(entries))
        row['revision'] += 1
        row['updated_by'] = actor_id

    def publish(self, _cursor, board_id, snapshot, actor_id):
        row = self._row(board_id)
        row['published_entries'] = json.loads(json.dumps(snapshot))
        row['published_at'] = self.tick()
        row['published_by'] = actor_id
        row['first_published_at'] = row['first_published_at'] or row['published_at']

    def unpublish(self, _cursor, board_id):
        row = self._row(board_id)
        row['published_entries'] = row['published_at'] = row['published_by'] = None

    def existing_user_ids(self, _cursor, user_ids):
        return {value for value in user_ids if value in PEOPLE}

    def user_avatars(self, _cursor, user_ids):
        return [(value, 'stand', f'avatars/{value}.webp') for value in sorted(user_ids) if value in PEOPLE]

    def people_cards(self, _cursor, user_ids):
        return [{'id': value, 'name': PEOPLE[value]['name'], 'role': PEOPLE[value]['role'],
                 'avatar_bucket': 'stand', 'avatar_blob_path': f'avatars/{value}.webp', 'direction_name': 'Основа'}
                for value in sorted(user_ids) if value in PEOPLE]

    def department_by_code(self, _cursor, code):
        return self.departments.get(code)

    def candidates(self, _cursor, department_id):
        return [{'id': value, 'name': person['name'], 'role': person['role'], 'avatar_bucket': 'stand',
                 'avatar_blob_path': f'avatars/{value}.webp' if value != 6 else None, 'direction_name': 'Основа'}
                for value, person in PEOPLE.items()
                if person.get('department_id') == department_id and not person.get('fired')]

    def user_department_code(self, _cursor, user_id):
        person = PEOPLE.get(int(user_id))
        if not person or person.get('fired'):
            return None
        return DEPARTMENT_CODES.get(person.get('department_id'))

    def insert_photo(self, _cursor, *, user_id, bucket, blob_path, content_type, width, height, file_size, cutout,
                     actor_id):
        photo_id = len(self.photos) + 1
        self.photos[photo_id] = {'id': photo_id, 'user_id': user_id, 'bucket': bucket, 'blob_path': blob_path,
                                 'width': width, 'height': height, 'cutout': cutout, 'content_type': content_type,
                                 'file_size': file_size, 'uploaded_by': actor_id}
        return photo_id

    def photos_by_ids(self, _cursor, photo_ids):
        return {value: dict(self.photos[value]) for value in photo_ids if value in self.photos}

    def _used_photo_ids(self):
        used = set()
        for row in self.boards.values():
            for entries in (row['draft_entries'], row['published_entries'] or []):
                used.update(entry.get('photo_id') for entry in entries if entry.get('photo_id'))
        return used

    def latest_photos(self, _cursor, user_ids):
        used = self._used_photo_ids()
        result = {}
        for photo in sorted(self.photos.values(), key=lambda item: item['id']):
            if photo['user_id'] in user_ids and photo['id'] in used:
                result[photo['user_id']] = dict(photo)
        return result

    def user_on_boards(self, _cursor, user_id, department_keys):
        for (_period, key), row in self.boards.items():
            if key not in department_keys:
                continue
            for entries in (row['draft_entries'], row['published_entries'] or []):
                if any(entry.get('user_id') == user_id for entry in entries):
                    return True
        return False

    def _fresh_boards(self, user_id):
        seen = self.reads.get(user_id)
        return [(period, key, row['first_published_at']) for (period, key), row in self.boards.items()
                if row['published_at'] and row['first_published_at']
                and (seen is None or row['first_published_at'] > seen)]

    def unseen_periods(self, _cursor, user_id, limit):
        grouped = {}
        for period, key, moment in self._fresh_boards(user_id):
            keys, last = grouped.get(period, ([], None))
            grouped[period] = (sorted(keys + [key]), max(filter(None, [last, moment])))
        periods = sorted(grouped, reverse=True)
        return len(periods), [(period, grouped[period][0], grouped[period][1]) for period in periods[:limit]]

    def has_unseen(self, _cursor, user_id):
        return bool(self._fresh_boards(user_id))

    def mark_seen(self, _cursor, user_id):
        self.reads[user_id] = self.tick()


class _Db:
    @contextmanager
    def _get_cursor(self):
        yield object()


QUERY_NAMES = (
    'board', 'published_periods', 'published_boards', 'boards_between', 'previous_custom_nominations',
    'insert_board', 'update_draft', 'publish', 'unpublish', 'existing_user_ids', 'user_avatars',
    'people_cards', 'department_by_code', 'candidates', 'user_department_code', 'insert_photo',
    'photos_by_ids', 'latest_photos', 'user_on_boards', 'unseen_periods', 'has_unseen', 'mark_seen',
)


class _Blob:
    def __init__(self, storage, path):
        self.storage, self.path, self.cache_control = storage, path, None

    def upload_from_string(self, data, content_type=None):
        if self.storage.fail_upload:
            raise RuntimeError('GCS 403')
        self.storage.blobs[self.path] = (data, content_type, self.cache_control)

    def delete(self):
        self.storage.blobs.pop(self.path, None)


class FakeStorage:
    def __init__(self):
        self.blobs = {}
        self.fail_upload = False

    def client(self):
        return self

    def bucket(self, _name):
        return self

    def blob(self, path):
        return _Blob(self, path)


@unittest.skipIf(Flask is None, 'Flask is required')
class HonorBoardRoutesTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.storage = FakeStorage()
        self.requester_id = 1
        patches = [mock.patch.object(honor_queries, name, getattr(self.store, name)) for name in QUERY_NAMES]
        patches.append(mock.patch.object(honor_routes, 'schema_is_ready', lambda _cursor: True))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        def resolve_requester():
            person = PEOPLE[self.requester_id]
            return self.requester_id, (self.requester_id, None, person['name'], person['role']), None

        def avatar_url(bucket, blob):
            if not blob:
                return None
            if blob.endswith('/5.webp'):
                raise RuntimeError('GCS 403')
            return f'https://photos.example/{blob}'

        app = Flask(__name__)
        app.register_blueprint(honor_routes.build_honor_board_blueprint(
            db=_Db(), require_api_key=lambda handler: handler,
            build_cors_preflight_response=lambda: ('', 204), resolve_requester=resolve_requester,
            normalize_role=lambda role: str(role or '').lower(), avatar_url=avatar_url,
            headed_department_codes=lambda user_id: HEADED.get(user_id, []),
            photo_storage={'bucket_name': lambda: 'stand-bucket', 'client': self.storage.client},
            today=lambda: TODAY,
        ))
        self.client = app.test_client()

    def as_user(self, user_id):
        self.requester_id = user_id

    def put(self, entries, revision, period='2026-10', department='op'):
        return self.client.put('/api/events/honor_board/edit', json={
            'period': period, 'department': department, 'entries': entries, 'revision': revision})

    def post(self, action, revision=None, period='2026-10', department='op'):
        body = {'period': period, 'department': department}
        if revision is not None:
            body['revision'] = revision
        return self.client.post(f'/api/events/honor_board/{action}', json=body)

    def upload(self, user_id, raw, cutout='1', filename='photo.png'):
        return self.client.post('/api/events/honor_board/photos', data={
            'user_id': str(user_id), 'cutout': cutout, 'photo': (io.BytesIO(raw), filename)},
            content_type='multipart/form-data')

    FULL = [
        entry('best_employee', user_id=4, name='Дана Абенова', description='Звонок за звонком'),
        entry('custom', title='Мастер ЯР', icon='Car', user_id=5, name='Ерлан Жумабеков', description='Анкеты'),
    ]

    def test_everyone_sees_board_only_trainer_and_super_admin_manage(self):
        for user_id, manager in ((3, False), (8, False), (7, False), (1, True), (2, True)):
            with self.subTest(role=PEOPLE[user_id]['role']):
                self.as_user(user_id)
                response = self.client.get('/api/events/honor_board')
                self.assertEqual(response.status_code, 200)
                self.assertIs(response.get_json()['can_manage'], manager)
                edit = self.client.get('/api/events/honor_board/edit?period=2026-10&department=op')
                self.assertEqual(edit.status_code, 200 if manager else 403)
                if not manager:
                    self.assertEqual(edit.get_json()['code'], 'HONOR_MANAGE_FORBIDDEN')
                    self.assertEqual(self.put(self.FULL, 0).status_code, 403)
                    self.assertEqual(self.post('publish', 0).status_code, 403)
                    self.assertEqual(self.client.get('/api/events/honor_board/candidates?department=op').status_code, 403)
                    self.assertEqual(self.upload(4, PhotoPrepareTests.image_bytes()).status_code, 403)
        self.assertEqual(self.store.boards, {})
        self.assertEqual(self.storage.blobs, {})

    def test_new_board_and_default_period(self):
        response = self.client.get('/api/events/honor_board')
        data = response.get_json()
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual((data['periods'], data['period'], data['boards'], data['honor_photos'], data['unseen']),
                         ([], None, {}, {}, False))
        self.assertEqual(data['default_period'], '2026-10')
        self.assertEqual(data['manage_departments'], ['op', 'szov', 'front_office'])
        board = self.client.get('/api/events/honor_board/edit?period=2026-10&department=szov').get_json()
        self.assertEqual(board['board']['revision'], 0)
        self.assertIs(board['board']['saved'], False)
        self.assertEqual([e['title'] for e in board['board']['entries']], [
            'Лучший сотрудник отдела', 'Чемпион по продуктивности', 'Первоклассный менеджер', 'Звезда чат-направления'])
        self.assertEqual(board['photos'], {})
        self.assertEqual(board['months'][0], {'period': '2026-10', 'states': {'op': None, 'szov': None, 'front_office': None}})
        for query in ('period=2026-11&department=op', 'period=2026-12&department=op',
                      'period=2026-10&department=hr', 'department=op'):
            self.assertEqual(self.client.get(f'/api/events/honor_board/edit?{query}').status_code, 400, query)

    def test_draft_revisions_and_conflicts(self):
        first = self.put(self.FULL[:1], 0)
        self.assertEqual(first.status_code, 200)
        board = first.get_json()['board']
        self.assertEqual((board['revision'], board['state'], board['saved']), (1, 'draft', True))
        self.assertEqual(first.get_json()['people']['4']['name'], 'Абенова Дана Маратовна')
        # Вторая вкладка с устаревшей revision не затирает, а получает свежую доску.
        stale = self.put(self.FULL, 0)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()['code'], 'HONOR_CONFLICT')
        self.assertEqual(stale.get_json()['board']['revision'], 1)
        self.assertIn('photos', stale.get_json())
        # То же содержимое — revision не растёт (окно сохраняет само, по ходу ввода).
        self.assertEqual(self.put(self.FULL[:1], 1).get_json()['board']['revision'], 1)
        self.assertEqual(self.put(self.FULL, 1).get_json()['board']['revision'], 2)
        self.assertEqual(self.put(self.FULL, 7).status_code, 409)
        missing = self.put([entry('best_employee', user_id=999)], 2)
        self.assertEqual((missing.status_code, missing.get_json()['code']), (400, 'HONOR_UNKNOWN_USER'))
        bad = self.put([entry('nope')], 2)
        self.assertEqual((bad.status_code, bad.get_json()['code']), (400, 'HONOR_BAD_ENTRIES'))
        self.assertEqual(self.put(self.FULL, None).status_code, 400)
        self.assertEqual(self.store.boards[(date(2026, 10, 1), 'op')]['revision'], 2)

    def test_rename_icon_and_more_nominations_for_op(self):
        """Просьба 06.11.2026: названия номинаций ОП и СЗоВ — как у Фронт-офиса, и больше четырёх."""
        own = [entry('custom', title=f'Номинация {index}', icon='Star') for index in range(6)]
        saved = self.put([self.FULL[0], dict(self.FULL[1], title='Король ЯР', icon='Rocket')] + own, 0)
        self.assertEqual(saved.status_code, 200)
        entries = saved.get_json()['board']['entries']
        self.assertEqual(len(entries), catalog.MAX_NOMINATIONS)
        self.assertEqual((entries[1]['title'], entries[1]['icon']), ('Король ЯР', 'Rocket'))
        self.assertEqual(self.post('publish', 1).status_code, 200)
        self.as_user(3)
        cards = self.client.get('/api/events/honor_board').get_json()['boards']['op']['entries']
        self.assertEqual([(c['title'], c['icon'], c['main']) for c in cards],
                         [('Лучший сотрудник отдела', 'Crown', True), ('Король ЯР', 'Rocket', False)])
        # Девятая номинация — отказ.
        self.as_user(1)
        nine = self.put([self.FULL[0], self.FULL[1]] + own + [entry('custom', title='Девятая')], 1)
        self.assertEqual((nine.status_code, nine.get_json()['code']), (400, 'HONOR_BAD_ENTRIES'))

    def test_publish_edit_revert_unpublish(self):
        self.assertEqual(self.post('publish', 0).status_code, 400)
        self.put([{**self.FULL[0], 'description': ''}], 0)
        incomplete = self.post('publish', 1)
        self.assertEqual(incomplete.status_code, 400)
        self.assertEqual(incomplete.get_json()['issues'],
                         [{'index': 0, 'field': 'description', 'message': 'Опишите заслуги'}])
        self.assertIsNone(self.store.boards[(date(2026, 10, 1), 'op')]['published_at'])

        self.put(self.FULL, 1)
        self.assertEqual(self.post('publish', 1).status_code, 409)   # опубликовать можно только то, что на экране
        published = self.post('publish', 2)
        self.assertEqual(published.status_code, 200)
        self.assertEqual(published.get_json()['board']['state'], 'published')

        self.as_user(3)
        view = self.client.get('/api/events/honor_board').get_json()
        self.assertEqual((view['periods'], view['period']), (['2026-10'], '2026-10'))
        cards = view['boards']['op']['entries']
        self.assertEqual([(card['nomination'], card['main']) for card in cards],
                         [('best_employee', True), ('custom', False)])
        # Фото профиля — одним списком; отказ подписи (5) не роняет доску.
        self.assertEqual(view['photos'], {'4': 'https://photos.example/avatars/4.webp'})

        self.as_user(1)
        self.put([{**self.FULL[0], 'description': 'Черновой стих'}, self.FULL[1]], 2)
        edited = self.client.get('/api/events/honor_board/edit?period=2026-10&department=op').get_json()['board']
        self.assertEqual(edited['state'], 'changed')
        self.as_user(3)
        still = self.client.get('/api/events/honor_board?period=2026-10').get_json()['boards']['op']['entries'][0]
        self.assertEqual(still['description'], 'Звонок за звонком')   # правка до публикации не видна

        # Месяц, который сняли или которого нет среди опубликованных, — последний опубликованный.
        self.assertEqual(self.client.get('/api/events/honor_board?period=2026-09').get_json()['period'], '2026-10')

        self.as_user(1)
        reverted = self.post('revert', 3).get_json()['board']
        self.assertEqual(reverted['state'], 'published')
        self.assertEqual(reverted['entries'][0]['description'], 'Звонок за звонком')
        unpublished = self.post('unpublish')
        self.assertEqual(unpublished.get_json()['board']['state'], 'draft')
        self.assertEqual(self.post('unpublish').status_code, 409)
        self.as_user(3)
        self.assertEqual(self.client.get('/api/events/honor_board').get_json()['periods'], [])

    def test_new_month_takes_last_month_nominations_in_every_department(self):
        for department in ('front_office', 'op'):
            self.put([
                entry('best_employee', user_id=4, name='Дана', description='Лучшая'),
                entry('custom', title='Лучший кассир', icon='Gem', user_id=5, name='Ерлан', description='Быстрый'),
                entry('custom', title='Скорость ответа', icon='Zap'),
            ], 0, period='2026-09', department=department)
            # Опубликована только номинация с победителем — названия всё равно берутся все.
            self.assertEqual(self.post('publish', 1, period='2026-09', department=department).status_code, 200)
            board = self.client.get(f'/api/events/honor_board/edit?period=2026-10&department={department}').get_json()['board']
            self.assertEqual([(e['nomination'], e['title'], e['icon'], e['user_id']) for e in board['entries']], [
                ('best_employee', 'Лучший сотрудник отдела', 'Crown', None),
                ('custom', 'Лучший кассир', 'Gem', None),
                ('custom', 'Скорость ответа', 'Zap', None),
            ], department)

    def test_legacy_board_reads_as_published(self):
        """Доска, выложенная до доработки (коды ОП, без значков), — «опубликовано», а не «есть правки»."""
        legacy = [
            {'nomination': 'best_employee', 'title': 'Лучший сотрудник отдела', 'user_id': 4, 'name': 'Дана',
             'description': 'Стих'},
            {'nomination': 'guru_sales', 'title': 'Гуру продаж', 'user_id': 5, 'name': 'Ерлан', 'description': 'Рекорд'},
            {'nomination': 'master_yr', 'title': 'Мастер ЯР', 'user_id': None, 'name': '', 'description': ''},
        ]
        self.store.boards[(date(2026, 10, 1), 'op')] = {
            'id': 77, 'period_month': date(2026, 10, 1), 'department_key': 'op', 'draft_entries': legacy,
            'revision': 3, 'updated_at': datetime(2026, 11, 3, 9, 0), 'updated_by': 1,
            'published_entries': legacy[:2], 'published_at': datetime(2026, 11, 3, 9, 5), 'published_by': 1,
            'first_published_at': datetime(2026, 11, 3, 9, 5),
        }
        board = self.client.get('/api/events/honor_board/edit?period=2026-10&department=op').get_json()['board']
        self.assertEqual(board['state'], 'published')
        self.assertEqual([(e['nomination'], e['title'], e['icon']) for e in board['entries']], [
            ('best_employee', 'Лучший сотрудник отдела', 'Crown'), ('custom', 'Гуру продаж', 'TrendingUp'),
            ('custom', 'Мастер ЯР', 'Car'),
        ])
        # Сохранить то же самое — не правка: revision не растёт.
        self.assertEqual(self.put(board['entries'], 3).get_json()['board']['revision'], 3)
        months = self.client.get('/api/events/honor_board/edit?period=2026-10&department=op').get_json()['months']
        self.assertEqual(months[0]['states']['op'], 'published')

    def test_front_office_head_manages_only_front_office(self):
        # ТЗ, п. 2: номинации Фронт-офиса указывает «тренер или руководитель».
        self.as_user(9)
        view = self.client.get('/api/events/honor_board').get_json()
        self.assertEqual((view['can_manage'], view['manage_departments']), (True, ['front_office']))
        edit = self.client.get('/api/events/honor_board/edit?period=2026-10&department=front_office')
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.get_json()['months'][0]['states'], {'front_office': None})
        self.assertEqual(self.put([entry('best_employee', user_id=11, name='Жанна', description='Лучшая')],
                                  0, department='front_office').status_code, 200)
        self.assertEqual(self.post('publish', 1, department='front_office').status_code, 200)
        for response in (
            self.client.get('/api/events/honor_board/edit?period=2026-10&department=op'),
            self.put(self.FULL, 0),
            self.post('publish', 0),
            self.client.get('/api/events/honor_board/candidates?department=szov'),
        ):
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/api/events/honor_board/candidates?department=front_office').status_code, 200)
        # Фото — только сотрудникам своего направления.
        self.assertEqual(self.upload(11, PhotoPrepareTests.image_bytes()).status_code, 200)
        refused = self.upload(4, PhotoPrepareTests.image_bytes())
        self.assertEqual((refused.status_code, refused.get_json()['code']), (403, 'HONOR_MANAGE_FORBIDDEN'))
        # Глава другого отдела (ОП) по ТЗ доску не ведёт.
        self.as_user(10)
        self.assertEqual(self.client.get('/api/events/honor_board').get_json()['manage_departments'], [])
        self.assertEqual(self.put(self.FULL, 0).status_code, 403)

    def test_body_must_be_an_object(self):
        for body in ([1, 2], 'abc', 123):
            for path, method in (('edit', self.client.put), ('publish', self.client.post),
                                 ('unpublish', self.client.post), ('revert', self.client.post)):
                with self.subTest(path=path, body=body):
                    response = method(f'/api/events/honor_board/{path}', json=body)
                    self.assertEqual((response.status_code, response.get_json()['code']), (400, 'HONOR_BAD_REQUEST'))
        bad = self.put([entry('best_employee', name={'a': 1})], 0)
        self.assertEqual((bad.status_code, bad.get_json()['code']), (400, 'HONOR_BAD_ENTRIES'))
        self.assertEqual(self.store.boards, {})

    def test_months_carry_state_per_department(self):
        self.put(self.FULL, 0)
        self.post('publish', 1)
        self.put(self.FULL[:1], 0, department='szov')
        months = self.client.get('/api/events/honor_board/edit?period=2026-10&department=op').get_json()['months']
        october = next(month for month in months if month['period'] == '2026-10')
        self.assertEqual(october['states'], {'op': 'published', 'szov': 'draft', 'front_office': None})
        self.assertEqual(months[0]['period'], '2026-10')

    def test_candidates_carry_last_board_photo(self):
        data = self.client.get('/api/events/honor_board/candidates?department=op').get_json()
        self.assertEqual(data['department_name'], 'Отдел продаж')
        photos = {person['id']: person['photo_url'] for person in data['people']}
        # Уволенный (12) в победители не предлагается.
        self.assertEqual(photos, {4: 'https://photos.example/avatars/4.webp', 5: None, 6: None})
        self.assertTrue(all(person['honor_photo'] is None for person in data['people']))
        first = self.upload(4, PhotoPrepareTests.image_bytes()).get_json()['photo']
        second = self.upload(4, PhotoPrepareTests.image_bytes(), cutout='0').get_json()['photo']
        candidates = lambda: {person['id']: person for person in  # noqa: E731
                              self.client.get('/api/events/honor_board/candidates?department=op').get_json()['people']}
        # Загрузили, но ни на одну доску не поставили — не подставляется.
        self.assertIsNone(candidates()[4]['honor_photo'])
        self.put([dict(self.FULL[0], photo_id=first['id'])], 0)
        self.assertEqual(candidates()[4]['honor_photo']['id'], first['id'])
        self.put([dict(self.FULL[0], photo_id=second['id'])], 1)
        people = candidates()
        self.assertEqual((people[4]['honor_photo']['id'], people[4]['honor_photo']['cutout']), (second['id'], False))
        self.assertIsNone(people[5]['honor_photo'])
        # «Убрать» своё фото: снятое ни с какой доски больше не подставляется.
        self.put([dict(self.FULL[0], photo_id=None)], 2)
        self.assertIsNone(candidates()[4]['honor_photo'])
        self.assertEqual(self.client.get('/api/events/honor_board/candidates?department=szov').get_json()['people'], [])
        self.assertEqual(self.client.get('/api/events/honor_board/candidates?department=x').status_code, 400)

    def test_photo_upload(self):
        response = self.upload(4, PhotoPrepareTests.image_bytes())
        self.assertEqual(response.status_code, 200)
        photo = response.get_json()['photo']
        self.assertEqual((photo['id'], photo['user_id'], photo['cutout'], photo['width'], photo['height']),
                         (1, 4, True, 300, 400))
        [(path, (data, content_type, cache))] = self.storage.blobs.items()
        self.assertTrue(path.startswith('HonorBoard/4/'), path)
        self.assertTrue(path.endswith('.webp'))
        self.assertEqual(content_type, 'image/webp')
        self.assertIn('immutable', cache)
        self.assertEqual(photo['url'], f'https://photos.example/{path}')
        self.assertEqual(self.store.photos[1]['uploaded_by'], 1)
        self.assertEqual(Image.open(io.BytesIO(data)).format, 'WEBP')

        for user_id, code, status in ((12, 'HONOR_MANAGE_FORBIDDEN', 403), (999, 'HONOR_UNKNOWN_USER', 400),
                                      (3, 'HONOR_MANAGE_FORBIDDEN', 403)):
            with self.subTest(user_id=user_id):
                refused = self.upload(user_id, PhotoPrepareTests.image_bytes())
                self.assertEqual((refused.status_code, refused.get_json()['code']), (status, code))
        bad = self.upload(4, b'not an image')
        self.assertEqual((bad.status_code, bad.get_json()['code']), (400, 'HONOR_BAD_PHOTO'))
        no_file = self.client.post('/api/events/honor_board/photos', data={'user_id': '4'},
                                   content_type='multipart/form-data')
        self.assertEqual(no_file.status_code, 400)
        self.assertEqual(len(self.storage.blobs), 1)
        # Хранилище отказало — строки фото нет, ответ 500, а не тихий успех.
        self.storage.fail_upload = True
        self.assertEqual(self.upload(4, PhotoPrepareTests.image_bytes()).status_code, 500)
        self.assertEqual(len(self.store.photos), 1)

    def test_photo_upload_unavailable_without_storage(self):
        app = Flask(__name__)
        app.register_blueprint(honor_routes.build_honor_board_blueprint(
            db=_Db(), require_api_key=lambda handler: handler, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (1, (1, None, 'Тренер', 'trainer'), None),
            normalize_role=lambda role: str(role or '').lower(), today=lambda: TODAY,
        ))
        response = app.test_client().post('/api/events/honor_board/photos', data={
            'user_id': '4', 'photo': (io.BytesIO(PhotoPrepareTests.image_bytes()), 'p.png')},
            content_type='multipart/form-data')
        self.assertEqual((response.status_code, response.get_json()['code']), (503, 'HONOR_PHOTO_UNAVAILABLE'))

    def test_board_photo_must_be_of_the_same_person(self):
        dana = self.upload(4, PhotoPrepareTests.image_bytes()).get_json()['photo']['id']
        erlan = self.upload(5, PhotoPrepareTests.image_bytes(), cutout='0').get_json()['photo']['id']
        saved = self.put([dict(self.FULL[0], photo_id=dana), dict(self.FULL[1], photo_id=erlan)], 0)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(sorted(saved.get_json()['photos']), [str(dana), str(erlan)])
        self.assertEqual(saved.get_json()['people']['4']['honor_photo']['id'], dana)
        # Фото Даны в номинации Ерлана — отказ, черновик не тронут.
        foreign = self.put([dict(self.FULL[0], photo_id=dana), dict(self.FULL[1], photo_id=dana)], 1)
        self.assertEqual((foreign.status_code, foreign.get_json()['code']), (400, 'HONOR_BAD_PHOTO'))
        missing = self.put([dict(self.FULL[0], photo_id=99)], 1)
        self.assertEqual((missing.status_code, missing.get_json()['code']), (400, 'HONOR_BAD_PHOTO'))
        self.assertEqual(self.store.boards[(date(2026, 10, 1), 'op')]['revision'], 1)
        # Выложили — карточка несёт фото, ответ доски — его ссылку и вырезан ли фон.
        self.post('publish', 1)
        self.as_user(3)
        view = self.client.get('/api/events/honor_board').get_json()
        self.assertEqual([card['photo_id'] for card in view['boards']['op']['entries']], [dana, erlan])
        self.assertEqual({key: value['cutout'] for key, value in view['honor_photos'].items()},
                         {str(dana): True, str(erlan): False})

    def test_photo_for_a_past_winner_who_left_the_department(self):
        """Доску месяца правят и потом: победитель с тех пор уволен или в другом отделе."""
        self.put([entry('best_employee', user_id=12, name='Борис', description='Лучший')], 0)
        self.assertEqual(self.upload(12, PhotoPrepareTests.image_bytes()).status_code, 200)
        # Глава Фронт-офиса не загрузит фото человеку, который стоит только на доске ОП.
        self.as_user(9)
        self.assertEqual(self.upload(12, PhotoPrepareTests.image_bytes()).status_code, 403)

    def test_photo_upload_cleans_up_and_refuses_oversized_requests(self):
        # База отказала после загрузки в хранилище — файл убран, а не брошен в бакете.
        with mock.patch.object(honor_queries, 'insert_photo', side_effect=RuntimeError('db down')):
            self.assertEqual(self.upload(4, PhotoPrepareTests.image_bytes()).status_code, 500)
        self.assertEqual(self.storage.blobs, {})
        # Тело больше потолка — отказ по Content-Length, до чтения файла и хранилища.
        huge = b'\0' * (honor_routes.PHOTO_REQUEST_MAX + 1)
        refused = self.upload(4, huge)
        self.assertEqual((refused.status_code, refused.get_json()['code']), (413, 'HONOR_BAD_PHOTO'))
        self.assertEqual(self.storage.blobs, {})

    def test_new_photo_or_icon_on_published_board_is_a_change(self):
        """Сфотографировали уже объявленного победителя: новое фото выходит к людям
        следующей публикацией, а до неё доска — «есть правки»."""
        self.put(self.FULL, 0)
        self.post('publish', 1)
        photo = self.upload(4, PhotoPrepareTests.image_bytes()).get_json()['photo']['id']
        self.put([dict(self.FULL[0], photo_id=photo), self.FULL[1]], 1)
        edit = self.client.get('/api/events/honor_board/edit?period=2026-10&department=op').get_json()
        self.assertEqual(edit['board']['state'], 'changed')
        self.assertEqual(edit['months'][0]['states']['op'], 'changed')
        self.as_user(3)
        self.assertIsNone(self.client.get('/api/events/honor_board').get_json()['boards']['op']['entries'][0]['photo_id'])
        self.as_user(1)
        self.post('publish', 2)
        self.as_user(3)
        self.assertEqual(self.client.get('/api/events/honor_board').get_json()['boards']['op']['entries'][0]['photo_id'],
                         photo)
        # Только значок своей номинации — тоже правка.
        self.as_user(1)
        self.put([dict(self.FULL[0], photo_id=photo), dict(self.FULL[1], icon='Rocket')], 2)
        state = self.client.get('/api/events/honor_board/edit?period=2026-10&department=op').get_json()['board']['state']
        self.assertEqual(state, 'changed')

    def test_bell_rings_on_first_publication_only(self):
        self.put(self.FULL, 0)
        self.post('publish', 1)
        # Тренер выложил сам — ему колокол не звонит.
        self.assertFalse(self.client.get('/api/events/honor_board').get_json()['unseen'])
        self.as_user(3)
        self.assertTrue(self.client.get('/api/events/honor_board').get_json()['unseen'])
        self.assertEqual(self.client.post('/api/events/honor_board/seen', json={}).status_code, 200)
        self.assertFalse(self.client.get('/api/events/honor_board').get_json()['unseen'])
        # Правки уже выложенной доски — не новая доска, второй раз не звоним.
        self.as_user(1)
        self.put([{**self.FULL[0], 'description': 'Новый стих'}, self.FULL[1]], 1)
        self.post('publish', 2)
        self.post('unpublish')
        self.put(self.FULL, 3)
        self.post('publish', 4)
        self.as_user(3)
        self.assertFalse(self.client.get('/api/events/honor_board').get_json()['unseen'])
        # Новая доска (СЗоВ того же месяца) — звоним.
        self.as_user(2)
        self.put([entry('best_employee', user_id=6, name='Виктория', description='Лучшая')], 0, department='szov')
        self.post('publish', 1, department='szov')
        self.as_user(3)
        self.assertTrue(self.client.get('/api/events/honor_board').get_json()['unseen'])


class BellSourceTests(unittest.TestCase):
    def test_source_items(self):
        from notifications import sources
        rows = (2, [(date(2026, 10, 1), ['op', 'szov'], datetime(2026, 11, 5, 11, 0)),
                    (date(2026, 9, 1), ['front_office'], datetime(2026, 10, 2, 10, 0))])
        with mock.patch.object(honor_queries, 'unseen_periods', return_value=rows) as call:
            total, items = sources.honor_board(object(), {'user_id': 3}, 5)
        call.assert_called_once()
        self.assertEqual(total, 2)
        self.assertEqual([(i['title'], i['body'], i['target'], i['view'], i['id']) for i in items], [
            ('Доска почёта: итоги октября', 'ОП, СЗоВ', 'honor:2026-10:op,szov', 'events', 202610),
            ('Доска почёта: итоги сентября', 'Фронт-офис', 'honor:2026-09:front_office', 'events', 202609),
        ])
        self.assertEqual(items[0]['at'], '2026-11-05T11:00:00')
        self.assertIn('honor_board', sources.SOURCES)
        self.assertEqual(sources.SOURCE_VIEWS['honor_board'], ('events',))
        # Личный набор без «Ивентов» — доска молчит, как и лента.
        self.assertIn('honor_board', sources.sources_outside_views(['tasks']))

    def test_mark_seen(self):
        from notifications import sources
        with mock.patch.object(honor_queries, 'mark_seen') as call:
            self.assertTrue(sources.mark_seen(object(), 3, 'honor_board'))
        call.assert_called_once()


class WiringTests(unittest.TestCase):
    """Доска подключена к приложению, к развёртыванию схемы и к колоколу."""

    def test_blueprint_and_schema_are_wired(self):
        bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
        self.assertIn('from honor_board.routes import build_honor_board_blueprint', bot)
        self.assertIn('avatar_url=_build_avatar_signed_url,', bot)
        self.assertIn("photo_storage={'bucket_name': _honor_board_photo_bucket, 'client': get_gcs_client},", bot)
        schema = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8-sig')
        self.assertIn('self._init_honor_board_schema_tx(cursor)', schema)
        self.assertIn('from honor_board.schema import init_honor_board_schema', schema)
        # Триггеры колокола: выход доски — всем, водяной знак — самому человеку.
        self.assertIn("'trg_bell_honor_boards',", schema)
        self.assertIn("'AFTER UPDATE OF first_published_at, published_at',", schema)
        self.assertIn("('trg_bell_honor_board_reads', 'honor_board_reads', 'AFTER INSERT OR UPDATE', '')", schema)
        self.assertIn("IN ('events', 'four_you_images', 'news_posts', 'honor_boards')", schema)
        # Колокол знает схему раньше триггеров: доска разворачивается до них.
        self.assertLess(schema.index('self._init_honor_board_schema_tx(cursor)'),
                        schema.index('self._init_bell_notify_schema_tx(cursor)'))

    def test_manager_roles_are_literal_tz(self):
        # ТЗ: вести доску — роль «Тренер»; супер-админ — владельцы портала; Фронт-офис —
        # ещё и его руководитель. Других руководителей ТЗ не называет.
        self.assertEqual(honor_routes.MANAGER_ROLES, frozenset({'trainer', 'super_admin'}))
        self.assertEqual(honor_routes.HEAD_MANAGED_DEPARTMENTS, {'front_office': 'front_office'})
        self.assertEqual(honor_routes.managed_departments('admin', ['FRONT_OFFICE', 'op']), ('front_office',))
        self.assertEqual(honor_routes.managed_departments('sv', []), ())
        self.assertEqual(honor_routes.managed_departments('trainer', []), ('op', 'szov', 'front_office'))
        bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
        self.assertIn('headed_department_codes=_headed_department_codes,', bot)

    def test_events_view_has_honor_tab_and_bell_focus(self):
        events = (ROOT / 'src' / 'components' / 'events' / 'EventsView.jsx').read_text(encoding='utf-8')
        self.assertIn("{ value: 'honor', label: 'Доска почёта' }", events)
        self.assertIn('<HonorBoard user={user}', events)
        self.assertIn('focus={honorFocus}', events)
        # Раздел открывается лентой (вход гасит бейдж новых постов) — кроме зова
        # колокола: тогда сразу доской, без кадра ленты.
        self.assertIn("const [tab, setTab] = useState(() => (bellFocus ? 'honor' : 'feed'));", events)
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertEqual(app.count('focusRequest={eventsFocusRequest}'), app.count('<EventsView'))
        self.assertIn("/^honor:(\\d{4}-\\d{2})(?::([a-z_,]+))?$/", app)
        self.assertIn("if (view !== 'events') setEventsFocusRequest(null);", app)
        bell = (ROOT / 'src' / 'components' / 'notifications' / 'NotificationsBell.jsx').read_text(encoding='utf-8')
        self.assertIn("honor_board: { label: 'Доска почёта'", bell)
        self.assertIn("'honor_board'", bell[bell.index('const CLEARABLE'):bell.index('const CLEARABLE') + 120])


if __name__ == '__main__':
    unittest.main()
