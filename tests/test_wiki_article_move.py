# -*- coding: utf-8 -*-
"""Перенос статьи из раздела в раздел — пункт «Переместить» в каталоге вики.

Что здесь сторожится и почему именно это.

1. ПРАВО НА ДВА РАЗДЕЛА, А НЕ НА ОДИН. Забрать статью из чужой ветки — то же
   распоряжение её содержимым, что и положить: у соседнего отдела регламент
   просто исчезнет. Симметрию уже держит PATCH (симметричная разность в
   _forbidden_sections), и новая дверь обязана считать так же — иначе она
   становится обходным путём вокруг проверки.

2. НАБОР РАЗДЕЛОВ СЧИТАЕТ СЕРВЕР. Статья лежит сразу в нескольких разделах, и
   перенос трогает РОВНО ОДИН. Присылай клиент весь набор (как в PATCH) — и
   список, успевший устареть на экране, отвязал бы статью от ветки, которую
   коллега подключил минуту назад. Поэтому в запросе два идентификатора, а
   расхождение с базой — отказ, а не молчаливый перенос по старой картинке.

3. ПОРЯДОК ЗАПИСИ. Сначала связь с новым разделом, потом разрыв со старым:
   статья без раздела не видна НИКОМУ, кроме автора, и обратный порядок при
   обрыве транзакции оставил бы документ невидимым.

4. ЖУРНАЛ НАЗЫВАЕТ ОБА РАЗДЕЛА. Запись ищут вопросом «кто увёз регламент из
   моего раздела», и ответ на него — это «из А в Б», а не «изменена статья».

5. ЭКРАН БЕЗ МОДАЛКИ. Решение «куда переложить» принимают, глядя на дерево
   слева и на соседние строки; окно поверх списка закрывает собой ровно то, по
   чему решают. Решения панели видны только в исходнике — их и читаем текстом,
   как это уже делает страж каталога (test_wiki_catalog.py).
"""

import io
import re
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from wiki import articles as wiki_articles  # noqa: E402
from wiki import edit as wiki_edit  # noqa: E402
from wiki import queries  # noqa: E402
from wiki import structure as wiki_structure  # noqa: E402
from wiki.ai import index as ai_index  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402

WIKI = ROOT / 'wiki'
SRC = ROOT / 'src' / 'components' / 'wiki'


def _source(*parts):
    return io.open(str(Path(*parts)), encoding='utf-8').read()


# ─────────────────────────────────────────────────────────────────────────────
# Запись в базу
# ─────────────────────────────────────────────────────────────────────────────

class RecordingCursor(object):
    """Курсор, запоминающий запросы. rowcount положительный — «строка легла»."""

    def __init__(self, rows=None):
        self.calls = []
        self.rowcount = 1
        self._rows = rows or {}
        self._answer = None

    def execute(self, sql, params=None):
        flat = ' '.join(sql.split())
        self.calls.append((flat, params))
        self._answer = None
        for needle, row in self._rows.items():
            if needle in flat:
                self._answer = row
                break

    def fetchone(self):
        return self._answer

    def fetchall(self):
        return [self._answer] if self._answer else []


class MoveSectionSqlTest(unittest.TestCase):
    """wiki_edit.move_section: два запроса, в правильном порядке."""

    def test_new_link_appears_before_the_old_one_is_cut(self):
        cursor = RecordingCursor()
        wiki_edit.move_section(cursor, 7, from_section_id=3, to_section_id=9)
        kinds = [('INSERT' if sql.startswith('INSERT') else 'DELETE')
                 for sql, _ in cursor.calls]
        self.assertEqual(kinds, ['INSERT', 'DELETE'],
                         'разрыв со старым разделом раньше связи с новым оставляет '
                         'статью без раздела, то есть невидимой')

    def test_only_one_link_is_cut(self):
        """DELETE адресный: остальные разделы статьи перенос не трогает."""
        cursor = RecordingCursor()
        wiki_edit.move_section(cursor, 7, from_section_id=3, to_section_id=9)
        delete = next(sql for sql, _ in cursor.calls if sql.startswith('DELETE'))
        self.assertIn('article_id = %s AND section_id = %s', delete)
        self.assertEqual(
            [params for sql, params in cursor.calls if sql.startswith('DELETE')],
            [(7, 3)])

    def test_article_without_a_section_is_only_attached(self):
        cursor = RecordingCursor()
        wiki_edit.move_section(cursor, 7, from_section_id=None, to_section_id=9)
        self.assertEqual([sql.split()[0] for sql, _ in cursor.calls], ['INSERT'])

    def test_set_sections_is_not_reused(self):
        """Перенос не ходит через set_sections — тот ЗАМЕНЯЕТ весь набор."""
        source = _source(WIKI, 'routes_edit.py')
        move = source[source.index("@wiki_route('/articles/<int:article_id>/move'"):]
        move = move[:move.index('# ── Создание')]
        self.assertIn('wiki_edit.move_section', move)
        self.assertNotIn('set_sections', move)


# ─────────────────────────────────────────────────────────────────────────────
# Дверь
# ─────────────────────────────────────────────────────────────────────────────

ADMIN_ROLE = {'id': 5, 'code': 'wiki_admin', 'can_read': True, 'can_create': True,
              'can_edit': True, 'can_delete': True, 'can_publish': True,
              'can_approve': True, 'can_manage_users': True,
              'can_manage_structure': True, 'can_manage_access': True}

EDITOR_ROLE = {'id': 2, 'code': 'editor', 'can_read': True, 'can_create': True,
               'can_edit': True, 'can_delete': False, 'can_publish': False,
               'can_approve': False, 'can_manage_users': False,
               'can_manage_structure': False, 'can_manage_access': False}

FULL_RULE = {'can_read': True, 'can_create': True, 'can_edit': True,
             'can_delete': True, 'can_publish': True, 'can_approve': True}

# Правило без права заводить статьи: читать и править можно, распоряжаться
# содержимым раздела — нет.
NO_CREATE_RULE = dict(FULL_RULE, can_create=False)

ARTICLE = {
    'id': 7, 'slug': 'proshanie', 'title': 'Прощание', 'summary': None,
    'content': '<p>x</p>', 'article_type': 'general', 'status': 'published',
    'visibility_mode': 'inherit', 'strict_mode': False, 'toc': [], 'views': 0,
    'author_id': 99, 'author_name': None, 'owner_user_id': None, 'updated_by': None,
    'updated_at': None, 'created_at': None, 'published_at': None,
    'review_due_at': None, 'section_ids': [3], 'tags': [],
}

SECTION_NAMES = {3: 'Стандарты ведения диалога', 9: 'Клиент', 12: 'ФРОД'}


@unittest.skipIf(Flask is None, 'flask не установлен')
class MoveRouteTest(unittest.TestCase):
    """Каркас тот же, что у test_wiki_edit: слой данных подменён, проверяются
    решения о правах и о расхождении с базой."""

    def build(self, *, rules_by_section, article=None, wiki_roles=(ADMIN_ROLE,)):
        self.logged = []
        self.moved = []
        article = article or dict(ARTICLE)

        cursor = RecordingCursor()
        cursor.fetchone = lambda: None

        class SectionCursor(RecordingCursor):
            """Отвечает на два запроса роута про раздел: строка и имя."""

            def fetchone(self):
                sql = self.calls[-1][0] if self.calls else ''
                params = self.calls[-1][1] if self.calls else None
                wanted = params[0] if isinstance(params, (list, tuple)) and params else None
                if 'FROM wiki_sections' in sql and 'department_id' in sql:
                    return (wanted, SECTION_NAMES.get(wanted, 'раздел'), None) \
                        if wanted in SECTION_NAMES else None
                if 'SELECT name FROM wiki_sections' in sql:
                    return (SECTION_NAMES.get(wanted),) if wanted in SECTION_NAMES else None
                return None

        cursor = SectionCursor()

        context = {
            'user_id': 42, 'otp_role': 'sv', 'department_id': None,
            'direction_id': None, 'headed_department_ids': [], 'group_ids': [],
            'wiki_roles': list(wiki_roles), 'access_mode': 'auto',
        }

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def _move(_cursor, article_id, *, from_section_id, to_section_id):
            self.moved.append((article_id, from_section_id, to_section_id))
            return True

        patches = [
            (queries, 'load_access_context', lambda _c, _u: dict(context)),
            (queries, 'granted_rule_rights',
             lambda _c, _s, _u: (dict(FULL_RULE), [])),
            (queries, 'allowed_section_ids',
             lambda _c, _ctx, _s, **k: set(SECTION_NAMES)),
            (queries, 'section_rules_for_user',
             lambda _c, ids, _s, _u: {sid: rules_by_section.get(sid, [])
                                      for sid in (ids or ())}),
            (queries, 'log_action',
             lambda _c, **kw: self.logged.append(kw)),
            (queries, 'spaces_for_user', lambda *a, **k: [1]),
            (wiki_articles, 'visible_article_ids', lambda *a, **k: {7}),
            (wiki_articles, 'get_article', lambda *a, **k: dict(article)),
            (wiki_articles, 'article_rules_for_user', lambda *a, **k: {}),
            (wiki_edit, 'move_section', _move),
            (ai_index, 'reindex_article', lambda *a, **k: {'action': 'skipped'}),
        ]
        for module, name, replacement in patches:
            original = getattr(module, name)
            setattr(module, name, replacement)
            self.addCleanup(setattr, module, name, original)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
            gcs={'signed_url': lambda *a, **k: 'https://x'},
            session_id_provider=lambda: 'None',
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def move(self, client, **body):
        return client.post('/api/wiki/articles/7/move', json=body)

    # ── Права ────────────────────────────────────────────────────────────

    def test_move_happens_and_is_written_down(self):
        client = self.build(rules_by_section={3: [FULL_RULE], 9: [FULL_RULE]},
                            wiki_roles=(EDITOR_ROLE,))
        response = self.move(client, from_section_id=3, to_section_id=9)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.moved, [(7, 3, 9)])
        record = next(r for r in self.logged if r['action'] == 'article.move')
        self.assertEqual(record['entity_type'], 'article')
        self.assertEqual(record['details']['from_section_name'],
                         'Стандарты ведения диалога')
        self.assertEqual(record['details']['section_name'], 'Клиент')

    def test_target_without_create_right_is_refused(self):
        """Ветка, куда человеку писать нельзя, не принимает статью."""
        client = self.build(rules_by_section={3: [FULL_RULE], 9: [NO_CREATE_RULE]},
                            wiki_roles=(EDITOR_ROLE,))
        response = self.move(client, from_section_id=3, to_section_id=9)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_FORBIDDEN')
        self.assertEqual(self.moved, [])

    def test_source_without_create_right_is_refused(self):
        """Забрать статью из чужого раздела — то же право, что и положить.

        Без этой проверки перенос стал бы обходным путём: статью нельзя
        увезти в чужую ветку, зато можно было бы вынести ИЗ неё.
        """
        client = self.build(rules_by_section={3: [NO_CREATE_RULE], 9: [FULL_RULE]},
                            wiki_roles=(EDITOR_ROLE,))
        response = self.move(client, from_section_id=3, to_section_id=9)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SECTION_FORBIDDEN')
        self.assertIn('забирать статьи из', response.get_json().get('error'))
        self.assertEqual(self.moved, [])

    def test_reader_cannot_move(self):
        client = self.build(rules_by_section={3: [{'can_read': True}],
                                              9: [{'can_read': True}]},
                            wiki_roles=())
        response = self.move(client, from_section_id=3, to_section_id=9)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.moved, [])

    # ── Расхождение с базой ──────────────────────────────────────────────

    def test_target_where_the_article_already_lies(self):
        """Перенос «туда, где статья и так лежит» — это отвязка, а не перенос."""
        client = self.build(rules_by_section={3: [FULL_RULE], 9: [FULL_RULE]},
                            article=dict(ARTICLE, section_ids=[3, 9]),
                            wiki_roles=(EDITOR_ROLE,))
        response = self.move(client, from_section_id=3, to_section_id=9)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json().get('code'), 'WIKI_ALREADY_THERE')
        self.assertEqual(self.moved, [])

    def test_stale_source_is_refused(self):
        """Статью уже переложили, пока человек смотрел на список."""
        client = self.build(rules_by_section={3: [FULL_RULE], 9: [FULL_RULE],
                                              12: [FULL_RULE]},
                            article=dict(ARTICLE, section_ids=[12]),
                            wiki_roles=(EDITOR_ROLE,))
        response = self.move(client, from_section_id=3, to_section_id=9)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SOURCE_CHANGED')
        self.assertEqual(self.moved, [])

    def test_missing_source_when_the_article_has_one(self):
        """Без источника перенос превратился бы в тихое ЗАИМСТВОВАНИЕ.

        Статья осталась бы в прежнем разделе и появилась во втором — то есть
        сделано было бы не то, о чём просили (для этого есть /adopt).
        """
        client = self.build(rules_by_section={3: [FULL_RULE], 9: [FULL_RULE]},
                            wiki_roles=(EDITOR_ROLE,))
        response = self.move(client, to_section_id=9)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SOURCE_CHANGED')
        self.assertEqual(self.moved, [])

    def test_article_outside_the_tree_is_just_put_in(self):
        """Наследие импорта: забирать не из чего, спрашивается один раздел.

        Права на такую статью берутся не от раздела — его нет, — а от
        авторства: наследовать их не от чего (см. шапку
        wiki_edit.default_section_id), и видит такую статью только автор.
        """
        client = self.build(rules_by_section={9: [FULL_RULE]},
                            article=dict(ARTICLE, section_ids=[], author_id=42),
                            wiki_roles=(EDITOR_ROLE,))
        response = self.move(client, to_section_id=9)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.moved, [(7, None, 9)])
        record = next(r for r in self.logged if r['action'] == 'article.move')
        self.assertIsNone(record['details']['from_section_name'])

    def test_unknown_target_is_not_found(self):
        client = self.build(rules_by_section={3: [FULL_RULE]},
                            wiki_roles=(EDITOR_ROLE,))
        response = self.move(client, from_section_id=3, to_section_id=404)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.moved, [])

    def test_target_is_required(self):
        client = self.build(rules_by_section={3: [FULL_RULE]},
                            wiki_roles=(EDITOR_ROLE,))
        self.assertEqual(self.move(client, from_section_id=3).status_code, 400)
        self.assertEqual(self.moved, [])


# ─────────────────────────────────────────────────────────────────────────────
# Журнал
# ─────────────────────────────────────────────────────────────────────────────

class MoveInTheJournalTest(unittest.TestCase):
    """Новое действие обязано быть и подписано, и отфильтровано.

    Про подпись есть общий страж (test_wiki_audit_space), но он проверяет ЛЮБОЕ
    действие; здесь — что запись читается фразой «из А в Б», а не одним лишь
    «Статья перемещена»: именно вопросом «откуда её увезли» журнал и открывают.
    """

    def test_action_is_in_the_articles_chip(self):
        self.assertIn('article.move', wiki_structure.AUDIT_GROUPS['articles'])

    def test_both_sections_are_named_in_the_row(self):
        source = _source(SRC, 'auditEvents.js')
        self.assertIn("'article.move': { label: 'Статья перемещена'", source)
        case = source[source.index("case 'article.move':"):]
        case = case[:case.index('break;')]
        self.assertIn('details.from_section_name', case)
        self.assertIn('details.section_name', case)


# ─────────────────────────────────────────────────────────────────────────────
# Экран
# ─────────────────────────────────────────────────────────────────────────────

class MoveScreenSourceTest(unittest.TestCase):
    """Решения панели переноса, которые видно только в исходнике фронта."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = _source(SRC, 'WikiCatalog.jsx')
        cls.panel = _source(SRC, 'ArticleMovePanel.jsx')
        cls.logic = _source(SRC, 'articleMove.js')
        cls.view = _source(SRC, 'WikiView.jsx')

    def test_the_row_menu_offers_the_move(self):
        self.assertIn("label: 'Переместить'", self.catalog)
        self.assertIn('openMove(article)', self.catalog)

    def test_no_modal_window(self):
        """Ни окна, ни оверлея, ни системного confirm.

        Панель раскрывается в строке: дерево слева и соседние строки остаются
        видны, а по ним и принимают решение. Системный confirm сюда тоже не
        годится — он рисуется браузером и в интерфейсе в стиле macOS выглядит
        деталью из другой программы.
        """
        for source, name in ((self.panel, 'ArticleMovePanel.jsx'),):
            self.assertNotIn('IosModal', source, name)
            self.assertNotIn('aria-modal', source, name)
            self.assertNotIn('fixed inset-0', source, name)
            self.assertNotIn('createPortal', source, name)
        move = self.catalog[self.catalog.index('const openMove'):
                            self.catalog.index('const menuFor')]
        self.assertNotIn('window.confirm', move)

    def test_the_panel_unfolds_instead_of_appearing(self):
        """Раскрытие сеткой 0fr → 1fr — и у панели, и у полосы подтверждения.

        max-height с подобранным на глаз числом ошибается ровно там, где дерево
        длиннее прикидки: анимация заканчивается рывком. Высота по содержимому
        одинаково плавна и на трёх разделах, и на тридцати.
        """
        self.assertIn('grid-rows-[0fr]', self.catalog)
        self.assertIn('grid-rows-[1fr]', self.catalog)
        self.assertIn('grid-rows-[0fr]', self.panel)
        self.assertIn('grid-rows-[1fr]', self.panel)
        # Первый кадр — закрытый, иначе переход не виден вовсе.
        self.assertIn('open: false', self.catalog)
        self.assertIn('requestAnimationFrame', self.catalog)

    def test_the_fold_out_survives_the_closing(self):
        """Состояние снимается ПОСЛЕ анимации, и срок один на разметку и таймер."""
        self.assertRegex(self.catalog, r'const UNFOLD_MS = 300;')
        self.assertIn('duration-300', self.catalog)
        self.assertIn('UNFOLD_MS', self.catalog)

    def test_the_tree_comes_from_structure_with_its_rights(self):
        """Дерево панели — из /structure: только там у раздела есть права.

        Каталог разделы знает, но can_create не несёт, и панель предлагала бы
        ветки, на которые сервер отвечает 403.
        """
        self.assertIn('structure = null', self.catalog)
        self.assertIn("structure?.sections", self.catalog)
        self.assertIn('structure={scopedStructure}', self.view)
        self.assertIn('permissions?.can_create', self.logic)

    def test_the_client_sends_two_sections_not_a_whole_set(self):
        """В запросе два идентификатора, а набор считает сервер.

        Присылай фронт весь section_ids — и устаревший список отвязал бы статью
        от ветки, подключённой коллегой минуту назад.
        """
        self.assertIn('/move`', self.catalog)
        self.assertIn('from_section_id: move.from', self.catalog)
        self.assertIn('to_section_id: move.to', self.catalog)
        submit = self.catalog[self.catalog.index('const submitMove'):
                              self.catalog.index('const menuFor')]
        self.assertNotIn('section_ids', submit)

    def test_the_menu_item_is_hidden_when_there_is_nothing_to_take_from(self):
        """Пункт не открывает панель, в которой любое нажатие — отказ."""
        self.assertIn('mayMove(article, moveSections, sectionNames)', self.catalog)
        self.assertIn('export function mayMove', self.logic)

    def test_the_source_is_chosen_when_the_article_lies_in_several_sections(self):
        """Из какого раздела переносим — спрашиваем, а не берём первый попавшийся.

        Порядок section_ids в ответе API произвольный, и «перенести» молча
        забрало бы статью из той ветки, которая оказалась первой.
        """
        self.assertIn('из какого переносим', self.panel)
        self.assertIn('onFrom', self.panel)

    def test_the_confirmation_names_both_sections_and_the_consequence(self):
        """Подтверждение говорит, что именно случится, а не «вы уверены?»."""
        self.assertIn('пропадёт из', self.panel)
        self.assertIn('правила нового раздела', self.panel)
        self.assertIn('keptNote', self.panel)

    def test_other_sections_of_the_article_are_promised_to_stay(self):
        self.assertIn('export function keptNote', self.logic)
        self.assertIn('Статья останется ещё', self.logic)

    def test_the_panel_hangs_under_the_row_and_not_inside_it(self):
        """Мобильный слой правит строку по МЕСТУ в разметке.

        wiki-mobile.css целится в `.wiki-m-row > span:last-child` (меню) и
        `> button` (заголовок). Панель, положенная внутрь строки, сдвинула бы
        эти правила на чужие элементы — меню уехало бы, а панель получила бы
        размеры кнопки.
        """
        row = self.catalog[self.catalog.index('const ArticleRow'):
                           self.catalog.index('/* Пустой экран')]
        self.assertIn('{panel}', row)
        self.assertLess(row.index('wiki-m-row'), row.index('{panel}'))
        layer = _source(SRC, 'wiki-mobile.css')
        self.assertIn('.wiki-move-row', layer)
        self.assertIn('wiki-move-row', self.panel)


if __name__ == '__main__':
    unittest.main()
