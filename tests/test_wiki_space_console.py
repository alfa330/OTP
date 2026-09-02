# -*- coding: utf-8 -*-
"""Управление пространствами: кто видит карту границ и что уезжает в шапку.

Пространство — это ГРАНИЦА между отделами и клиентами, а не элемент структуры
(решение владельца 21.08.2026, см. routes_structure._may_manage_space). Отсюда
две вещи, которые обязаны держаться сами:

* полный список пространств — включая архивные и чужие — это карта этих границ,
  и отдавать её можно ровно тому, кто границы двигает;
* строка переключателя в шапке несёт ИМЯ и ПОДПИСЬ пространства, но не список
  отделов: кому ещё выдана вика — вопрос конструктора, а не выпадающего меню.

Плюс сторожа для фронта: часть решений по интерфейсу закреплена не .test.mjs,
а здесь — они читают .jsx как текст (см. tests/test_survey_scheduled_tests.py).
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask  # noqa: F401
except ImportError:  # pragma: no cover
    Flask = None

from wiki import queries, structure  # noqa: E402

from test_wiki_routes import ADMIN_ROLE, _RouteHarness, make_context  # noqa: E402

SRC = ROOT / 'src' / 'components' / 'wiki'

# Роль вики, которой выписали структуру, но НЕ мастер-ключ. Такую назначают
# руками редактору-администратору внутри одного пространства: дерево разделов
# он строит, а границы между пространствами не двигает.
STRUCTURE_ONLY_ROLE = dict(ADMIN_ROLE, id=6, code='wiki_structure',
                           can_manage_access=False)

SPACE_ROW = {
    'id': 11, 'code': 'igroup', 'name': 'iGroup', 'description': 'База знаний компании',
    'icon': '🚕', 'department_id': None, 'department_name': None, 'status': 'active',
    'position': 0, 'sections_count': 7, 'department_ids': [3, 5],
    'features': {'parks': False},
}


@unittest.skipIf(Flask is None, 'flask не установлен')
class SpaceListGateTest(_RouteHarness, unittest.TestCase):
    """Карту границ отдаём тому же, кто её и чертит."""

    def test_structure_admin_cannot_read_all_spaces(self):
        """Способности can_manage_structure для чтения списка мало.

        До 02.09.2026 GET /spaces был закрыт только ею, и назначенный руками
        администратор вики получал в ответе ВСЕ пространства — включая чужого
        клиента и архивные. Заводить и править он их не мог (POST и PATCH
        спрашивают _may_manage_space), но список уже отвечал на вопрос
        «а что там рядом лежит», ради закрытия которого пространства и завели.
        """
        client, _ = self.build(make_context('admin', wiki_roles=[STRUCTURE_ONLY_ROLE]))
        response = client.get('/api/wiki/spaces')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SPACE_ADMIN_ONLY')

    def test_super_admin_reads_archive(self):
        """Супер-админу список нужен целиком: из него он возвращает архивное.

        Архивация в конструкторе была односторонней дверью — пространство
        пропадает из /ping и /structure (spaces_for_user берёт только
        status='active'), и вернуть его из интерфейса было нечем.
        """
        client, _ = self.build(make_context('super_admin'))
        archived = dict(SPACE_ROW, id=12, name='Старое', status='archived')
        self._patch(structure, 'list_spaces',
                    lambda _c, include_archived=False: [SPACE_ROW, archived])
        response = client.get('/api/wiki/spaces')
        self.assertEqual(response.status_code, 200)
        names = [sp['status'] for sp in response.get_json()['items']]
        self.assertIn('archived', names)

    def test_options_still_free(self):
        # Preflight обязан проходить до любого гейта, иначе браузер не отправит
        # ни одного POST с Content-Type: application/json.
        client, _ = self.build(make_context('operator'))
        self.assertEqual(client.options('/api/wiki/spaces').status_code, 204)

    def _patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)


@unittest.skipIf(Flask is None, 'flask не установлен')
class SpaceFieldLimitsTest(_RouteHarness, unittest.TestCase):
    """Длины полей — по колонкам таблицы, а не «255 на всё»."""

    def test_icon_trimmed_to_column_width(self):
        """wiki_spaces.icon — VARCHAR(64).

        Общий предел в 255 символов означал, что значок длиннее колонки доезжал
        до базы и валил запрос ошибкой драйвера вместо понятного отказа. Теперь
        значок ставится из конструктора, то есть путь стал живым.
        """
        client, cursor = self.build(make_context('super_admin'))
        # Роут сперва читает имя пространства — без строки он честно ответит 404.
        cursor.fetchone.return_value = ('iGroup',)
        captured = {}

        def fake_update(_cursor, space_id, fields):
            captured.update(fields)
            return True

        original = structure.update_space
        structure.update_space = fake_update
        self.addCleanup(setattr, structure, 'update_space', original)
        original_log = queries.log_action
        queries.log_action = lambda *_a, **_k: None
        self.addCleanup(setattr, queries, 'log_action', original_log)

        response = client.patch('/api/wiki/spaces/11', json={'icon': 'x' * 300})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(captured['icon']), 64)


@unittest.skipIf(Flask is None, 'flask не установлен')
class SpaceSwitcherPayloadTest(_RouteHarness, unittest.TestCase):
    """Что уезжает в шапку раздела вместе со списком пространств."""

    def test_ping_carries_description_but_not_departments(self):
        """Подпись — да, граница — нет.

        Описание стоит в меню второй строкой и отвечает на «а это которая из
        них», когда имена похожи («Тез КЦ» и «Тез ОП»). Список отделов —
        другой разговор: он говорит, кому ЕЩЁ выдана вика, и читателю знать
        его незачем. Полную карточку конструктору отдаёт /structure.
        """
        client, _ = self.build(make_context('operator'), spaces=(11,))

        # Без готовой схемы ping отвечает одной лишь диагностикой: список
        # пространств собирается только когда таблицы на месте.
        original_ready = queries.schema_is_ready
        queries.schema_is_ready = lambda _c: True
        self.addCleanup(setattr, queries, 'schema_is_ready', original_ready)
        original_spaces = structure.list_spaces
        structure.list_spaces = lambda _c, include_archived=False: [dict(SPACE_ROW)]
        self.addCleanup(setattr, structure, 'list_spaces', original_spaces)
        original_counters = queries.counters
        queries.counters = lambda *_a, **_k: {}
        self.addCleanup(setattr, queries, 'counters', original_counters)

        payload = client.get('/api/wiki/ping').get_json()
        space = payload['spaces'][0]
        self.assertEqual(space['description'], 'База знаний компании')
        self.assertEqual(space['icon'], '🚕')
        self.assertNotIn('department_ids', space)
        self.assertNotIn('sections_count', space)


class SpaceSwitcherFrontendTest(unittest.TestCase):
    """Решения по интерфейсу переключателя, которые не видны из кода раздела."""

    def setUp(self):
        self.switch = (SRC / 'WikiSpaceSwitch.jsx').read_text(encoding='utf-8')
        self.view = (SRC / 'WikiView.jsx').read_text(encoding='utf-8')
        self.modal = (SRC / 'WikiSpaceModal.jsx').read_text(encoding='utf-8')

    def test_menu_lives_in_portal(self):
        """Шапка лежит внутри прокручиваемых контейнеров.

        Абсолютно спозиционированный список обрезался бы первым же предком с
        overflow — ровно поэтому в портале живут IosMenu и просмотрщик картинок.
        """
        self.assertIn('createPortal', self.switch)

    def test_gear_edits_its_own_row(self):
        """Шестерёнка правит СВОЁ пространство, а не открытое.

        Прежний «карандаш» в шапке правил текущее: чтобы поправить соседнее,
        надо было сначала в него переключиться и дождаться перезагрузки раздела.
        """
        self.assertIn('onEdit?.(cardOf(space))', self.switch)
        # А раздел обязан принять это пространство, а не подставить активное.
        self.assertIn("spaceModal?.mode === 'edit' ? spaceModal.space : null", self.view)

    def test_header_has_no_second_door_to_spaces(self):
        """Кнопок «+» и «карандаш» в шапке больше нет — всё внутри меню.

        Действия редкие (пространство заводят раз в квартал), а место занимали
        всегда и на телефоне переносили ряд на вторую строку.
        """
        self.assertNotIn('aria-label="Новое пространство"', self.view)
        self.assertNotIn('aria-label="Настроить пространство"', self.view)

    def test_archive_list_asked_only_of_those_who_may(self):
        """GET /spaces закрыт супер-админом.

        Запрос «на всякий случай» добавлял бы всем остальным красную строку в
        консоль на каждом открытии меню.
        """
        self.assertIn('!open || !canManage', self.switch)

    def test_modal_reuses_shared_segmented_control(self):
        """Своя пара кнопок «Всем / Выбранным» была третьей копией примитива.

        На первой же правке палитры копия осталась бы прежней.
        """
        self.assertIn('IosSegmented', self.modal)
