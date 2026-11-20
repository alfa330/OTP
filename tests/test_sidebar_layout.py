"""Личная раскладка сайдбара: свой порядок разделов и папки (20.11.2026).

Границы, которые держат тесты:

- чья: только своя — личность из сессии, номера сотрудника ручка не берёт, и
  тело с лишним ключом отклоняется целиком;
- какая: только по форме и в пределах — те же пределы, что у клиента
  (src/components/sidebar/sidebarLayoutModel.js), их сверка — MirrorTests;
- куда: строка на учётку, повторное сохранение перезаписывает, DELETE
  возвращает стандартное меню.
"""

import contextlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from sidebar_layout import layout as rules
from sidebar_layout.layout import LayoutError, normalize_layout
from sidebar_layout.routes import build_sidebar_layout_blueprint

ROOT = Path(__file__).resolve().parents[1]


def sample(**overrides):
    data = {
        'v': 1,
        'blocks': [['Задачи', 'folder:fa1'], ['Отметки']],
        'folders': {'fa1': {'name': 'Каждый день', 'items': ['Вики', 'Курсы']}},
    }
    data.update(overrides)
    return data


class LayoutRuleTests(unittest.TestCase):
    def test_valid_layout_comes_back_in_canonical_form(self):
        self.assertEqual(normalize_layout(sample()), sample())

    def test_empty_blocks_are_dropped_and_folder_name_is_cleaned(self):
        result = normalize_layout(sample(
            blocks=[[], ['Задачи', 'folder:fa1'], []],
            folders={'fa1': {'name': '  Каждый\n  день ', 'items': []}},
        ))
        self.assertEqual(result['blocks'], [['Задачи', 'folder:fa1']])
        self.assertEqual(result['folders']['fa1']['name'], 'Каждый день')

    def test_shape_errors_are_rejected(self):
        broken = [
            None,
            [],
            {'v': 2, 'blocks': []},
            sample(blocks='не список'),
            sample(blocks=[['Задачи', 'Задачи']]),
            sample(blocks=[['Вики', 'folder:fa1']]),          # «Вики» и в папке
            sample(blocks=[['folder:fa1', 'folder:fa1']]),
            sample(blocks=[['folder:zz']]),                    # неизвестная папка
            sample(blocks=[['Задачи']]),                       # папка без места
            sample(blocks=[['folder:BAD']], folders={'BAD': {'name': 'x', 'items': []}}),
            sample(folders={'fa1': {'name': '   ', 'items': []}}),
            sample(blocks=[['За\x07дачи', 'folder:fa1']]),
            sample(blocks=[[' Задачи', 'folder:fa1']]),
            sample(blocks=[[7, 'folder:fa1']]),
            sample(blocks=[['x' * (rules.KEY_MAX + 1), 'folder:fa1']]),
        ]
        for raw in broken:
            with self.subTest(raw=raw), self.assertRaises(LayoutError):
                normalize_layout(raw)

    def test_limits(self):
        with self.assertRaises(LayoutError):
            normalize_layout({'v': 1, 'blocks': [['a']] * (rules.MAX_BLOCKS + 1), 'folders': {}})
        many = {f'f{i}': {'name': 'x', 'items': []} for i in range(rules.MAX_FOLDERS + 1)}
        with self.assertRaises(LayoutError):
            normalize_layout({'v': 1, 'blocks': [[f'folder:{k}' for k in many]], 'folders': many})
        with self.assertRaises(LayoutError):
            normalize_layout({'v': 1, 'blocks': [[f'Раздел {i}' for i in range(rules.MAX_ENTRIES + 1)]], 'folders': {}})
        ok = {'v': 1, 'blocks': [[f'Раздел {i}' for i in range(rules.MAX_ENTRIES)]], 'folders': {}}
        self.assertEqual(len(normalize_layout(ok)['blocks'][0]), rules.MAX_ENTRIES)

    def test_strict_shape_edges(self):
        """Края, которые Python пропускал, а клиент — нет (разбор 20.11)."""
        with self.assertRaises(LayoutError):  # True == 1 в Python
            normalize_layout(sample(v=True))
        with self.assertRaises(LayoutError):  # $ в Python совпадает перед «\n»
            normalize_layout({'v': 1, 'blocks': [['folder:fa1\n']], 'folders': {'fa1\n': {'name': 'x', 'items': []}}})
        with self.assertRaises(LayoutError):  # имя не строкой не превращается в «None»
            normalize_layout(sample(folders={'fa1': {'name': None, 'items': ['Вики']}}))
        with self.assertRaises(LayoutError):
            normalize_layout(sample(folders={'fa1': {'name': 'x', 'items': 'Вики'}}))

    def test_folder_members_count_toward_the_entry_limit(self):
        items = [f'Раздел {i}' for i in range(rules.MAX_ENTRIES)]
        with self.assertRaises(LayoutError):
            normalize_layout({'v': 1, 'blocks': [['folder:fa1']], 'folders': {'fa1': {'name': 'x', 'items': items}}})
        fits = items[:rules.MAX_ENTRIES - 1]
        result = normalize_layout({'v': 1, 'blocks': [['folder:fa1']], 'folders': {'fa1': {'name': 'x', 'items': fits}}})
        self.assertEqual(len(result['folders']['fa1']['items']), rules.MAX_ENTRIES - 1)

    def test_invisible_characters_are_cleaned_like_on_the_client(self):
        """NEL (0x85) Python считает пробелом, а JS — нет; BOM (0xFEFF) — наоборот;
        одиночная половинка суррогатной пары не кодируется. Обе стороны чистят их
        одинаково — в пробел."""
        for code in (0x85, 0xFEFF, 0xD800, 0x9F, 0x07):
            with self.subTest(code=hex(code)):
                name = normalize_layout(sample(folders={'fa1': {'name': f'Мои{chr(code)}разделы', 'items': []}}))['folders']['fa1']['name']
                self.assertEqual(name, 'Мои разделы')
                with self.assertRaises(LayoutError):
                    normalize_layout(sample(blocks=[[f'Зада{chr(code)}чи', 'folder:fa1']]))
        # Неразрывный пробел — пробел и там и там: схлопывается.
        name = normalize_layout(sample(folders={'fa1': {'name': f'Мои{chr(0xA0)}{chr(0xA0)}разделы', 'items': []}}))['folders']['fa1']['name']
        self.assertEqual(name, 'Мои разделы')

    def test_folder_name_is_cut_by_characters_not_bytes(self):
        name = normalize_layout(sample(folders={'fa1': {'name': '😀' * 60, 'items': []}}))['folders']['fa1']['name']
        self.assertEqual(len(name), rules.FOLDER_NAME_MAX)


class FakeCursor:
    """Понимает ровно тот SQL, что пишет sidebar_layout.routes."""

    def __init__(self, db):
        self.db = db
        self._pending = None

    def execute(self, sql, params=None):
        text = ' '.join(str(sql).split())
        if text.startswith('SELECT layout, updated_at FROM user_sidebar_layouts'):
            stored = self.db.rows.get(params[0])
            self._pending = None if stored is None else (json.loads(stored[0]), stored[1])
        elif text.startswith('INSERT INTO user_sidebar_layouts'):
            self.assertIn_jsonb(text)
            self.db.clock += 1
            stamp = f'2026-11-20T10:00:{self.db.clock:02d}+00:00'
            self.db.rows[params[0]] = (params[1], stamp)
            self._pending = (stamp,)
        elif text.startswith('DELETE FROM user_sidebar_layouts'):
            self.db.rows.pop(params[0], None)
        else:  # pragma: no cover - защитный край заглушки
            raise AssertionError(f'неожиданный SQL: {text}')

    @staticmethod
    def assertIn_jsonb(text):
        if '%s::jsonb' not in text or 'ON CONFLICT (user_id) DO UPDATE' not in text:
            raise AssertionError('раскладка пишется не upsert-ом в JSONB')
        if 'RETURNING updated_at' not in text:
            raise AssertionError('новая отметка версии не возвращается клиенту')

    def fetchone(self):
        return self._pending


class FakeDb:
    def __init__(self):
        self.rows = {}
        self.clock = 0

    @contextlib.contextmanager
    def _get_cursor(self):
        yield FakeCursor(self)


def client_for(*, requester_id=42, error=None):
    db = FakeDb()
    app = Flask(__name__)
    app.register_blueprint(build_sidebar_layout_blueprint(
        db=db,
        require_api_key=lambda handler: handler,
        build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (None, None, error) if error else (requester_id, (requester_id,), None),
    ))
    return app.test_client(), db


class RouteTests(unittest.TestCase):
    def test_no_layout_means_standard_menu(self):
        client, _db = client_for()
        response = client.get('/api/sidebar_layout')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()['layout'])

    def test_put_saves_own_layout_and_get_returns_it(self):
        client, db = client_for(requester_id=42)
        response = client.put('/api/sidebar_layout', json={'layout': sample()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['layout'], sample())
        first_stamp = response.get_json()['updated_at']
        self.assertTrue(first_stamp, 'клиенту нужна отметка версии — по ней он не затрёт чужое сохранение')
        self.assertEqual(list(db.rows), [42], 'строка — только за учёткой из сессии')
        got = client.get('/api/sidebar_layout').get_json()
        self.assertEqual(got['layout'], sample())
        self.assertEqual(got['updated_at'], first_stamp)
        # Повторное сохранение перезаписывает, а не плодит строки, и двигает отметку.
        second = client.put('/api/sidebar_layout', json={'layout': sample(blocks=[['folder:fa1', 'Задачи'], ['Отметки']])})
        self.assertEqual(len(db.rows), 1)
        self.assertNotEqual(second.get_json()['updated_at'], first_stamp)
        self.assertEqual(client.get('/api/sidebar_layout').get_json()['layout']['blocks'][0], ['folder:fa1', 'Задачи'])

    def test_broken_unicode_is_400_not_500(self):
        """Одиночная половинка суррогатной пары не кодируется в UTF-8: без
        проверки запись падала бы в базе (500), а клиент повторял бы её вечно."""
        client, db = client_for()
        broken = sample(blocks=[['Задачи' + chr(0xD800), 'folder:fa1'], ['Отметки']])
        response = client.put('/api/sidebar_layout', json={'layout': broken})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(db.rows, {})

    def test_someone_elses_id_in_body_is_rejected_whole(self):
        client, db = client_for(requester_id=42)
        response = client.put('/api/sidebar_layout', json={'layout': sample(), 'user_id': 7})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(db.rows, {})

    def test_invalid_layout_is_rejected_with_code(self):
        client, db = client_for()
        response = client.put('/api/sidebar_layout', json={'layout': sample(blocks=[['Задачи', 'Задачи']])})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'SIDEBAR_LAYOUT_INVALID')
        self.assertEqual(db.rows, {})

    def test_oversized_body_is_refused_before_parsing(self):
        client, _db = client_for()
        body = json.dumps({'layout': sample(), 'pad': 'x' * (rules.MAX_BODY_BYTES + 10)})
        response = client.put('/api/sidebar_layout', data=body, content_type='application/json')
        self.assertEqual(response.status_code, 413)

    def test_delete_returns_standard_menu(self):
        client, db = client_for()
        client.put('/api/sidebar_layout', json={'layout': sample()})
        response = client.delete('/api/sidebar_layout')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()['layout'])
        self.assertIsNone(response.get_json()['updated_at'])
        self.assertEqual(db.rows, {})
        self.assertIsNone(client.get('/api/sidebar_layout').get_json()['updated_at'])

    def test_auth_error_passes_through(self):
        client, _db = client_for(error=('Unauthorized', 401))
        self.assertEqual(client.get('/api/sidebar_layout').status_code, 401)
        self.assertEqual(client.put('/api/sidebar_layout', json={'layout': sample()}).status_code, 401)

    def test_stale_row_of_unknown_shape_opens_standard_menu(self):
        client, db = client_for(requester_id=42)
        db.rows[42] = (json.dumps({'v': 99}), '2026-11-20T10:00:00+00:00')
        self.assertIsNone(client.get('/api/sidebar_layout').get_json()['layout'])


class MirrorTests(unittest.TestCase):
    """Клиент режет раскладку до тех же пределов, что проверяет сервер."""

    JS = (ROOT / 'src' / 'components' / 'sidebar' / 'sidebarLayoutModel.js').read_text(encoding='utf-8')

    def js_const(self, name):
        match = re.search(rf'export const {name} = ([^;]+);', self.JS)
        self.assertIsNotNone(match, f'{name} не найден в sidebarLayoutModel.js')
        return match.group(1).strip()

    def test_limits_match(self):
        for name in ('LAYOUT_VERSION', 'FOLDER_NAME_MAX', 'KEY_MAX', 'MAX_FOLDERS', 'MAX_BLOCKS', 'MAX_ENTRIES'):
            with self.subTest(name=name):
                self.assertEqual(int(self.js_const(name)), getattr(rules, name))

    def test_folder_prefix_and_id_shape_match(self):
        self.assertEqual(self.js_const('FOLDER_PREFIX').strip("'"), rules.FOLDER_PREFIX)
        js_re = re.search(r'const FOLDER_ID_RE = /(.+)/;', self.JS).group(1)
        self.assertEqual(js_re, rules.FOLDER_ID_RE.pattern)


NODE = shutil.which('node')

CONTRACT_JS = r"""
import { readFileSync } from 'node:fs';
const model = await import(process.argv[2]);
const cases = JSON.parse(readFileSync(0, 'utf8'));
const out = cases.map((raw) => {
    // Что клиент делает с раскладкой перед отправкой: имена — через renameFolder,
    // затем normalizeLayout (forSave в хранилище).
    let layout = model.normalizeLayout(raw);
    for (const [id, folder] of Object.entries(raw.folders || {})) {
        if (layout && layout.folders[id]) layout = model.renameFolder(layout, id, folder.name);
    }
    return model.normalizeLayout(layout);
});
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, 'node не установлен')
class ClientServerContractTests(unittest.TestCase):
    """То, что клиент считает правильной раскладкой и отправляет по «Готово»,
    сервер принимает без изменений. Расхождение здесь — это «Готово», которое
    молча не сохраняется: копия помечается отправленной, а при следующем входе
    меню откатывается к серверной версии."""

    def test_client_normalized_layouts_are_accepted_as_is(self):
        odd = [chr(c) for c in (0x85, 0xFEFF, 0xA0, 0x2028, 0x3000, 0xD83D, 0x1F600, 0x9F)]
        cases = []
        for i, ch in enumerate(odd):
            cases.append({
                'v': 1,
                'blocks': [[f'Раздел {i}', 'folder:fa1', f'Ключ{ch}{i}'], ['Вики']],
                'folders': {'fa1': {'name': f'  Папка{ch}{ch}{i} ' + ch * 3, 'items': [f'Чаты {i}']}},
            })
        cases.append({'v': 1, 'blocks': [['А' * 200]], 'folders': {}})
        cases.append({'v': 1, 'blocks': [['folder:fb2']], 'folders': {'fb2': {'name': '😀' * 60, 'items': []}}})
        script = Path(tempfile.mkdtemp()) / 'contract.mjs'
        script.write_text(CONTRACT_JS, encoding='utf-8')
        model = (ROOT / 'src' / 'components' / 'sidebar' / 'sidebarLayoutModel.js').resolve().as_uri()
        result = subprocess.run(
            [NODE, str(script), model],
            input=json.dumps(cases, ensure_ascii=True),
            capture_output=True, text=True, encoding='utf-8', timeout=60, check=True,
        )
        for raw, client_out in zip(cases, json.loads(result.stdout)):
            with self.subTest(raw=json.dumps(raw, ensure_ascii=True)[:80]):
                self.assertIsNotNone(client_out)
                self.assertEqual(normalize_layout(client_out), client_out)


class WiringTests(unittest.TestCase):
    def test_schema_and_blueprint_are_connected(self):
        schema = (ROOT / 'db' / 'schema.py').read_text(encoding='utf-8')
        self.assertIn('self._init_sidebar_layout_schema_tx(cursor)', schema)
        self.assertIn('from sidebar_layout.schema import init_sidebar_layout_schema', schema)
        bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        self.assertIn('from sidebar_layout.routes import build_sidebar_layout_blueprint', bot)
        self.assertIn('resolve_requester=_resolve_requester,', bot[bot.index('build_sidebar_layout_blueprint('):])

    def test_table_is_one_row_per_user_and_goes_with_the_account(self):
        ddl = (ROOT / 'sidebar_layout' / 'schema.py').read_text(encoding='utf-8')
        self.assertIn('user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE', ddl)
        self.assertIn('layout JSONB NOT NULL', ddl)


if __name__ == '__main__':
    unittest.main()
