# -*- coding: utf-8 -*-
"""Защита статьи от копирования (тумблер в редакторе).

Признак живёт в одной колонке — wiki_articles.copy_protected — и проходит через
пять слоёв: миграция, выдача статьи, создание, правка, копия. Порвись он в любом
из них, и экран покажет ЛОЖЬ: тумблер стоит включённым, а текст копируется. Про
такой брак никто не сообщит багом — он выглядит как «защита не работает вообще»
и обнаруживается после утечки.

Отдельный набор, а не строчки в test_wiki_edit: половина проверок здесь читает
фронт ТЕКСТОМ. В этом репозитории так сторожат интерфейсные решения — сборка
пропускает молча и отсутствие тумблера, и класс, применённый не туда.
"""

import ast
import inspect
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
from wiki import schema as wiki_schema  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402

WIKI_SRC = ROOT / 'src' / 'components' / 'wiki'


def strip_comments(source):
    """Исходник без комментариев.

    Проверять КОД по тексту с комментариями нельзя: объяснение «раньше здесь
    стоял user-select на .wiki-prose» удовлетворило бы поиск ровно того, чего в
    коде уже нет. Приём и причина взяты из tests/test_wiki_catalog.py.
    """
    without_blocks = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return '\n'.join(line for line in without_blocks.splitlines()
                     if not line.lstrip().startswith('//'))


# ─────────────────────────────────────────────────────────────────────────────
# СХЕМА И ВЫДАЧА
# ─────────────────────────────────────────────────────────────────────────────
class SchemaTest(unittest.TestCase):

    def test_column_is_added_by_migration_not_by_create_table(self):
        """Колонка появляется ALTER'ом.

        Дописать её в CREATE TABLE wiki_articles бесполезно: на боевой базе
        таблица есть, и CREATE TABLE IF NOT EXISTS её не тронет — тумблер
        молча падал бы с UndefinedColumn у всех, кроме владельца пустого стенда.
        """
        altered = [s for s in wiki_schema._ORG_STATEMENTS
                   if 'copy_protected' in s and 'ADD COLUMN IF NOT EXISTS' in s]
        self.assertEqual(len(altered), 1, 'ожидали ровно один ALTER на колонку')

    def test_default_is_off(self):
        """По умолчанию FALSE: миграция не смеет запереть уже написанные статьи."""
        statement = next(s for s in wiki_schema._ORG_STATEMENTS if 'copy_protected' in s)
        self.assertIn('DEFAULT FALSE', statement)
        self.assertIn('NOT NULL', statement)

    def test_statement_has_no_percent_sign(self):
        """В _ORG_STATEMENTS строка уходит в execute СЫРОЙ, без второго аргумента.

        Любой символ '%' внутри psycopg2 примет за плейсхолдер и уронит всю
        инициализацию схемы — то есть не только эту фичу.
        """
        statement = next(s for s in wiki_schema._ORG_STATEMENTS if 'copy_protected' in s)
        self.assertNotIn('%', statement)

    def test_article_payload_keys_match_the_select(self):
        """Ключи и колонки разбираются ПОЗИЦИОННО (dict(zip(...))).

        Добавить ключ и забыть колонку — значит сдвинуть все поля за ним: toc
        окажется во views, автор — в имени автора. Экран при этом не падает,
        он просто показывает чужие значения.
        """
        source = (ROOT / 'wiki' / 'articles.py').read_text(encoding='utf-8')
        select = re.search(r'def get_article.*?SELECT (.*?)\n\s+FROM wiki_articles a',
                           source, re.S).group(1)
        columns, depth, current = [], 0, ''
        for char in select:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            if char == ',' and depth == 0:
                columns.append(current.strip())
                current = ''
            else:
                current += char
        columns.append(current.strip())
        columns = [' '.join(c.split()) for c in columns if c.strip()]

        self.assertEqual(len(columns), len(wiki_articles._ARTICLE_KEYS),
                         'число колонок в SELECT разошлось с числом ключей')
        position = wiki_articles._ARTICLE_KEYS.index('copy_protected')
        self.assertEqual(columns[position], 'a.copy_protected')

    def test_field_is_updatable(self):
        """Без этого PATCH принял бы поле и молча ничего не записал."""
        self.assertIn('copy_protected', wiki_edit._UPDATABLE)


# ─────────────────────────────────────────────────────────────────────────────
# СОЗДАНИЕ, ПРАВКА, КОПИЯ
# ─────────────────────────────────────────────────────────────────────────────
class WriteSideTest(unittest.TestCase):

    def _cursor(self, rows):
        cursor = MagicMock()
        cursor.fetchone.side_effect = list(rows)
        cursor.fetchall.return_value = []
        cursor.rowcount = 1
        return cursor

    def test_create_writes_the_flag(self):
        """Тумблер стоит в форме НОВОЙ статьи — значит он обязан попасть в INSERT.

        Приди он мимо создания, статья открылась бы читателям незащищённой до
        первого сохранения: интерфейс показал бы применённым решение, которого
        в базе нет.
        """
        cursor = self._cursor([(11,), (1,)])
        for name in ('set_sections', 'set_tags', 'link_content_files',
                     'link_content_articles', 'snapshot_version'):
            original = getattr(wiki_edit, name)
            setattr(wiki_edit, name, lambda *a, **k: None)
            self.addCleanup(setattr, wiki_edit, name, original)

        wiki_edit.create_article(
            cursor, slug='s', title='Т', summary=None, content='<p>x</p>',
            article_type='general', section_ids=[3], tags=[], author_id=1,
            copy_protected=True)

        sql, params = cursor.execute.call_args_list[0][0]
        self.assertIn('copy_protected', sql)
        self.assertIn(True, params)
        # Плейсхолдеров ровно столько, сколько значений: лишний %s — это сдвиг
        # ВСЕХ полей вправо, а не отказ.
        self.assertEqual(sql.count('%s'), len(params))

    def test_fork_carries_the_flag_into_the_copy(self):
        """«Перенести к себе» не должно быть обходным путём вокруг запрета.

        visibility_mode и strict_mode копия сбрасывает нарочно — это про доступ,
        и решает его новый владелец. Защита от копирования — свойство самого
        текста, а текст в копии тот же.
        """
        cursor = self._cursor([('сводка', '<p>x</p>', 'x', 'general', False, True), (12,)])
        for name in ('set_sections', 'link_content_files',
                     'link_content_articles', 'snapshot_version'):
            original = getattr(wiki_edit, name)
            setattr(wiki_edit, name, lambda *a, **k: None)
            self.addCleanup(setattr, wiki_edit, name, original)

        wiki_edit.fork_article(cursor, 7, section_id=3, author_id=1,
                               slug='s-2', title='Копия')

        select_sql = cursor.execute.call_args_list[0][0][0]
        insert_sql, params = cursor.execute.call_args_list[1][0]
        self.assertIn('copy_protected', select_sql)
        self.assertIn('copy_protected', insert_sql)
        self.assertIn(True, params)
        self.assertEqual(insert_sql.count('%s'), len(params))


ARTICLE = {
    'id': 7, 'slug': 'test', 'title': 'Тест', 'summary': None, 'content': '<p>x</p>',
    'article_type': 'general', 'status': 'published', 'visibility_mode': 'inherit',
    'strict_mode': False, 'copy_protected': False, 'toc': [], 'views': 0,
    'author_id': 99, 'author_name': None, 'owner_user_id': None, 'updated_by': None,
    'updated_at': None, 'created_at': None, 'published_at': None,
    'review_due_at': None, 'section_ids': [3], 'tags': [],
}


@unittest.skipIf(Flask is None, 'flask не установлен')
class PatchRightsTest(unittest.TestCase):
    """Кто вправе включить и выключить защиту. Каркас — как в test_wiki_edit."""

    def build(self, *, section_rules):
        # Должностной роли вики человеку НЕ выдаём: способность поднимает само
        # выписанное правило (queries.granted_rule_rights). Иначе роль редактора
        # дала бы can_edit поверх правила, и «читатель» в тесте ниже оказался
        # бы вправе всё — проверка бы не проверяла ничего.
        self.updates = []
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        cursor.rowcount = 1

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def _update(_cursor, _article_id, fields, **_kwargs):
            self.updates.append(dict(fields))
            return True

        patches = [
            (queries, 'load_access_context',
             lambda _c, _u: {'user_id': 42, 'otp_role': 'sv', 'department_id': None,
                             'direction_id': None, 'headed_department_ids': [],
                             'group_ids': [], 'wiki_roles': [],
                             'access_mode': 'auto'}),
            (queries, 'granted_rule_rights',
             lambda _c, _s, _u: (dict(section_rules[0] if section_rules else {}), [])),
            (queries, 'allowed_section_ids', lambda _c, _ctx, _s: {3}),
            (queries, 'section_rules_for_user',
             lambda _c, ids, _s, _u: ({3: section_rules} if ids else {})),
            (queries, 'log_action', lambda *a, **k: None),
            (wiki_articles, 'visible_article_ids', lambda *a, **k: {7}),
            (wiki_articles, 'get_article', lambda *a, **k: dict(ARTICLE)),
            (wiki_articles, 'article_rules_for_user', lambda *a, **k: {}),
            (wiki_edit, 'update_article', _update),
            (wiki_edit, 'set_sections', lambda *a, **k: None),
            (wiki_edit, 'set_tags', lambda *a, **k: None),
        ]
        for module, name, replacement in patches:
            original = getattr(module, name)
            setattr(module, name, replacement)
            self.addCleanup(setattr, module, name, original)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (42, None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
            gcs={'signed_url': lambda *a, **k: 'https://x'},
            session_id_provider=lambda: 'None',
        ))
        app.config['TESTING'] = True
        return app.test_client()

    EDIT_RULE = {'can_read': True, 'can_create': True, 'can_edit': True,
                 'can_delete': False, 'can_publish': False, 'can_approve': False}
    READ_RULE = {'can_read': True, 'can_create': False, 'can_edit': False,
                 'can_delete': False, 'can_publish': False, 'can_approve': False}

    def test_editor_may_switch_it_on(self):
        """Права ПРАВИТЬ достаточно: защита ничего не открывает и не закрывает.

        Требуй мы здесь администратора доступов — автор не смог бы защитить
        собственный регламент, не сходив к нему.
        """
        client = self.build(section_rules=[self.EDIT_RULE])
        response = client.patch('/api/wiki/articles/7', json={'copy_protected': True})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.updates[-1].get('copy_protected'), True)

    def test_reader_may_not(self):
        client = self.build(section_rules=[self.READ_RULE])
        response = client.patch('/api/wiki/articles/7', json={'copy_protected': True})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('required'), 'can_edit')

    def test_switching_it_off_reaches_the_field(self):
        """Выключение — такое же значение, а не отсутствие ключа.

        Обрабатывай сервер только истину, и снять защиту с уже защищённой
        статьи стало бы нельзя ничем, кроме SQL.
        """
        client = self.build(section_rules=[self.EDIT_RULE])
        response = client.patch('/api/wiki/articles/7', json={'copy_protected': False})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn('copy_protected', self.updates[-1])
        self.assertEqual(self.updates[-1]['copy_protected'], False)

    def test_untouched_field_is_not_written(self):
        """Правка заголовка не смеет трогать защиту."""
        client = self.build(section_rules=[self.EDIT_RULE])
        client.patch('/api/wiki/articles/7', json={'title': 'Новое'})
        self.assertNotIn('copy_protected', self.updates[-1])


# ─────────────────────────────────────────────────────────────────────────────
# ФРОНТ. Читается текстом: сборка пропускает молча и отсутствие тумблера, и
# класс, повешенный не на тот блок.
# ─────────────────────────────────────────────────────────────────────────────
class EditorScreenTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = (WIKI_SRC / 'WikiEditor.jsx').read_text(encoding='utf-8')
        cls.code = strip_comments(cls.src)

    def _toggle_block(self):
        """Кусок разметки ВОКРУГ тумблера, без комментариев.

        Искать подпись по всему файлу нельзя, и это проверено мутацией: опечатка
        в видимой строке теста не роняла — фразу он находил в комментарии над
        блоком, а 'IosToggle' в строке импорта. Тест с именем «тумблер назван
        по-человечески» проходил на статье, где подписи нет вовсе.
        """
        block = re.search(r'flex items-start justify-between(.*?)</section>',
                          self.code, re.S)
        self.assertIsNotNone(block, 'не нашли блок тумблера в разметке')
        self.assertIn('IosToggle', block.group(1))
        return block.group(1)

    def test_toggle_exists_and_is_named_for_a_human(self):
        block = self._toggle_block()
        self.assertIn('Защита от копирования', block)

    def test_the_promise_under_the_toggle_names_what_it_does_not_cover(self):
        """Оговорка обязана называть ОСТАВШИЕСЯ двери.

        Тумблер закрывает витрину, но текст той же статьи по-прежнему цитирует
        ИИ-помощник и показывает поиск, а целиком его видит любой, кто вправе
        править. Подпись, умалчивающая об этом, продаёт гарантию, которой нет, —
        и именно на неё сошлются, когда текст всё-таки утечёт.
        """
        block = self._toggle_block()
        for word in ('снимка экрана', 'поиск', 'помощник', 'правку'):
            self.assertIn(word, block, 'оговорка молчит про «%s»' % word)

    def test_payload_carries_the_field_unconditionally(self):
        """Ключ уходит всегда, а не «когда включено».

        Сервер применяет поле по факту его наличия в теле запроса; клади мы
        ключ только у защищённой статьи — снять защиту стало бы нечем.
        """
        self.assertRegex(self.code, r'copy_protected:\s*copyProtected')
        self.assertNotRegex(self.code, r'copyProtected\s*(&&|\?)\s*\{?\s*copy_protected')

    def test_state_starts_from_the_article(self):
        """Открытая на правку статья показывает СВОЁ состояние, а не умолчание."""
        self.assertRegex(self.code, r'useState\(!!article\?\.copy_protected\)')

    def test_toggle_marks_the_form_dirty(self):
        """Иначе бейдж «Есть несохранённые правки» и предупреждение при уходе врут."""
        block = re.search(r'setCopyProtected\(value\);\s*setDirty\(true\)', self.code)
        self.assertIsNotNone(block, 'тумблер не помечает форму изменённой')

    def test_save_depends_on_the_flag(self):
        """Забытая зависимость useCallback — это сохранение прежним значением."""
        deps = re.search(r'\}, \[editor, title, summary, articleType, sectionIds,(.*?)\]\);',
                         self.code, re.S).group(1)
        self.assertIn('copyProtected', deps)


class ArticleScreenTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = (WIKI_SRC / 'WikiArticle.jsx').read_text(encoding='utf-8')
        cls.code = strip_comments(cls.src)

    def test_protection_covers_the_whole_card(self):
        """Запрет на одном теле обходится Ctrl+A: заголовок и оглавление лежат
        в шапке карточки, а не в теле, и скопировались бы как обычно."""
        card = re.search(r'<article\b(.*?)>', self.code, re.S).group(1)
        self.assertIn('wiki-no-copy', card)
        self.assertIn('protectedRef', card)

    def test_body_keeps_its_own_class_only(self):
        """На .wiki-prose запрет вешать нельзя: тот же класс носит РЕДАКТОР
        (TipTap, editorProps), и невыделяемый текст обездвижил бы весь тулбар."""
        body = re.search(r'ref=\{attachBody\}\s*\n\s*className="([^"]+)"', self.code)
        self.assertIsNotNone(body)
        self.assertNotIn('wiki-no-copy', body.group(1))

    def test_no_article_is_quietly_exempt(self):
        """Исключений по типу статьи нет.

        Выведи из-под запрета хоть одну — и у неё тумблер в редакторе включается,
        сохраняется и показывается включённым, а на витрине не действует. Это и
        есть ложь на экране: владелец видит «защита стоит», читатель копирует.
        """
        self.assertRegex(self.code, r'protectText\s*=\s*copyProtected\s*;')

    def test_reader_is_told_why_selection_does_not_work(self):
        """Без объяснения неработающее выделение читается как поломка портала."""
        self.assertIn('Копирование запрещено', self.src)

    def test_print_note_lives_outside_the_card(self):
        """При печати карточка скрыта — подпись внутри неё скрылась бы вместе с ней."""
        self.assertIn('wiki-print-only', self.code)
        self.assertLess(self.code.index('</article>'),
                        self.code.index('wiki-print-only'))

    def test_context_menu_is_not_taken_away(self):
        """Контекстное меню не давим намеренно: «открыть ссылку в новой вкладке»
        витрина обещает прямо, а выделения при запрете и так нет."""
        self.assertNotIn('onContextMenu', self.code)


class EditorRemountTest(unittest.TestCase):
    """Смена статьи у смонтированного редактора.

    «Обновить эту статью» документом подменяет проп article у того же инстанса
    (WikiLibrary: onUpdateExisting). Поля редактора живут в useState с начальным
    значением из article, и без key React оставит прежнее состояние: форма
    покажет тумблер ВЫКЛЮЧЕННЫМ у защищённой статьи, а сохранение унесёт
    copy_protected=false — защита с боевого регламента снимется молча, и никто
    даже не заметит, что что-то трогал.
    """

    def test_editor_is_remounted_when_the_article_changes(self):
        code = strip_comments((WIKI_SRC / 'WikiLibrary.jsx').read_text(encoding='utf-8'))
        editor = re.search(r'<WikiEditor(.*?)/>', code, re.S)
        self.assertIsNotNone(editor, 'не нашли редактор в витрине')
        self.assertRegex(editor.group(1), r"key=\{editing\.id \|\| 'new'\}")


class HistoryScreenTest(unittest.TestCase):
    """История открыта каждому читателю и показывает тот же текст построчно."""

    @classmethod
    def setUpClass(cls):
        cls.code = strip_comments((WIKI_SRC / 'WikiHistory.jsx').read_text(encoding='utf-8'))

    def test_compare_panel_respects_the_flag(self):
        self.assertIn('useCopyGuard', self.code)
        self.assertRegex(self.code, r"article\?\.copy_protected\s*\?\s*' wiki-no-copy'")

    def test_the_guard_wakes_up_only_with_the_modal(self):
        """Компонент рендерится всегда — подписка по одному флагу дала бы на
        документе ВТОРОЙ слушатель поверх сторожа самой статьи, и Ctrl+A на
        защищённой статье отвечал бы двумя одинаковыми тостами подряд."""
        self.assertRegex(self.code,
                         r'useCopyGuard\(open\s*&&\s*!!article\?\.copy_protected')


class ThemeTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.css = (WIKI_SRC / 'wiki-theme.css').read_text(encoding='utf-8')
        cls.code = strip_comments(cls.css)

    def test_selection_is_switched_off_in_every_engine(self):
        block = re.search(r'\.wiki-no-copy \{(.*?)\}', self.code, re.S).group(1)
        for prop in ('-webkit-user-select: none', 'user-select: none',
                     '-webkit-touch-callout: none'):
            self.assertIn(prop, block, 'нет свойства %s' % prop)

    def test_the_editor_class_is_never_touched(self):
        """Правило на .wiki-prose обездвижило бы редактор — он носит тот же класс."""
        self.assertNotRegex(self.code, r'\.wiki-prose[^{]*\{[^}]*user-select:\s*none')

    def test_input_fields_keep_their_selection(self):
        """Safari наследует запрет ВНУТРЬ поля ввода и отнимает выделение прямо
        в нём — человек не может стереть напечатанное слово. А защищать в поле
        нечего: там лежит то, что он сам туда и ввёл."""
        block = re.search(r'\.wiki-no-copy :is\(input, textarea, \[contenteditable\]\) \{(.*?)\}',
                          self.code, re.S)
        self.assertIsNotNone(block, 'поля ввода не выведены из-под запрета')
        self.assertIn('user-select: text', block.group(1))
        self.assertIn('-webkit-user-select: text', block.group(1))

    def test_links_keep_their_long_press_on_ios(self):
        """-webkit-touch-callout гасится на всё поддерево разом, вместе с меню
        ссылки. Адрес соседней статьи — не текст статьи, и отнимать у читателя
        на телефоне «Открыть в новой вкладке» запрет не обещал."""
        self.assertRegex(self.code,
                         r'\.wiki-no-copy a \{[^}]*-webkit-touch-callout:\s*default')

    def test_printing_a_protected_article_gives_the_note_not_the_text(self):
        """«Сохранить как PDF» — то же копирование, только в один шаг."""
        printed = re.search(r'@media print \{(.*?)\n\}', self.code, re.S).group(1)
        self.assertIn('.wiki-no-copy { display: none', printed)
        self.assertIn('.wiki-print-only { display: block', printed)


class CopyGuardSourceTest(unittest.TestCase):
    """Хук запрета. Разбирается AST'ом: тут важна не строка, а что именно он
    слушает и в какой фазе."""

    @classmethod
    def setUpClass(cls):
        cls.code = strip_comments((WIKI_SRC / 'useCopyGuard.js').read_text(encoding='utf-8'))

    def test_listens_for_both_clipboard_commands(self):
        for event in ("'copy'", "'cut'"):
            self.assertIn('document.addEventListener(%s' % event, self.code)

    def test_listeners_are_removed(self):
        """Слушатель документа, переживший закрытие статьи, ломал бы копирование
        на всём портале — и чинился бы только перезагрузкой вкладки."""
        self.assertEqual(self.code.count('document.addEventListener'),
                         self.code.count('document.removeEventListener'))

    def test_capture_phase(self):
        """Чужой обработчик copy не должен успеть положить текст в буфер раньше."""
        for call in re.findall(r'document\.(?:add|remove)EventListener\((.*?)\);',
                               self.code, re.S):
            self.assertTrue(call.strip().endswith('true'), call)

    def test_input_fields_are_let_through(self):
        """Кнопка «Ссылка» копирует адрес через временный <textarea>."""
        self.assertIn('TEXTAREA', self.code)
        self.assertIn('isContentEditable', self.code)

    def test_the_clipboard_of_the_reader_is_not_wiped(self):
        """Запрет НЕ пишет в буфер обмена — он только отменяет запись.

        Отменённое событие copy кладёт в системный буфер содержимое своего
        DataTransfer. Допиши сюда setData('text/plain', '') — и человек,
        скопировавший номер из CRM и открывший защищённую статью, потерял бы
        свой номер: Ctrl+V в мессенджере вставил бы пустоту. Один
        preventDefault буфер не трогает вовсе.
        """
        self.assertNotIn('setData', self.code)

    def test_a_useless_ctrl_c_explains_itself(self):
        """Главный сценарий — Ctrl+C, которому нечего копировать.

        Выделения внутри блока нет (его погасил CSS), значит события copy может
        не быть вовсе, и без этого обработчика нажатие не даёт НИЧЕГО. Молчание
        в ответ на команду читается как поломка портала, а бейдж к этому моменту
        уже уехал за верхний край длинной статьи.
        """
        self.assertIn("document.addEventListener('keydown'", self.code)
        explain = re.search(r'const explain = \(event\) => \{(.*?)\n        \};',
                            self.code, re.S)
        self.assertIsNotNone(explain, 'не нашли обработчик нажатия')
        body = explain.group(1)
        # Ничего не отменяем: копировать и так нечего, а preventDefault отнял бы
        # Ctrl+C у всей страницы.
        self.assertNotIn('preventDefault', body)
        # И молчим, когда человек копирует что-то другое на той же странице.
        self.assertIn('isCollapsed', body)


class AuditJournalTest(unittest.TestCase):
    """Журнал правок пишет ИМЕНА полей, а не значения.

    PATCH кладёт в запись действия sorted(fields.keys()) (routes_edit:
    action='article.update'), а экран журнала переводит ключи словарём
    FIELD_TITLE. Ключа нет — и читатель видит строку «поля: copy_protected»
    латиницей посреди русского журнала: запись есть, а что произошло, не
    сказано.
    """

    def test_the_flag_has_a_russian_title(self):
        source = (WIKI_SRC / 'auditEvents.js').read_text(encoding='utf-8')
        titles = re.search(r'const FIELD_TITLE = \{(.*?)\n\};', source, re.S).group(1)
        self.assertRegex(titles, r"\bcopy_protected:\s*'[^']+'")


class WritersOfTheFlagTest(unittest.TestCase):
    """Кто вправе тронуть колонку — перечислено поимённо.

    Тело статьи пишут четыре функции, и каждая решает про защиту по-своему.
    Тест фиксирует именно расклад решений: молчаливое «а давайте для симметрии»
    в любую сторону здесь стоит либо снятой защиты, либо запрета, включённого
    за владельца.
    """

    def _source(self, name):
        return inspect.getsource(getattr(wiki_edit, name))

    def test_create_and_fork_set_it(self):
        self.assertIn('copy_protected', self._source('create_article'))
        self.assertIn('copy_protected', self._source('fork_article'))

    def test_restore_leaves_it_alone(self):
        """Откат возвращает ТЕКСТ прежней редакции, а не настройки статьи.

        В wiki_article_versions такой колонки нет вовсе, и «восстановить»
        значило бы обнулить защиту в FALSE — то есть снять её откатом.
        """
        self.assertNotIn('copy_protected', self._source('restore_version'))

    def test_import_from_the_old_wiki_does_not_switch_it_on(self):
        """Перенос из Wiki.js оставляет FALSE.

        У переносимой статьи такого признака не существует, и включать защиту
        за владельца, которого ещё не спросили, нельзя.
        """
        source = (ROOT / 'wiki' / 'routes_migration.py').read_text(encoding='utf-8')
        self.assertNotIn('copy_protected', source)


class MemoryOfTheContractTest(unittest.TestCase):
    """Имя поля одно на все слои — инверсии, как у «Поддержки ИИ», здесь нет."""

    def test_no_inverted_twin_appeared(self):
        for path in (ROOT / 'wiki' / 'routes_edit.py', ROOT / 'wiki' / 'edit.py',
                     WIKI_SRC / 'WikiEditor.jsx', WIKI_SRC / 'WikiArticle.jsx'):
            source = path.read_text(encoding='utf-8')
            self.assertNotIn('copy_opt_out', source)
            self.assertNotIn('allow_copy', source)


class PythonSourcesParseTest(unittest.TestCase):
    """Дешёвая страховка: изменённые модули хотя бы разбираются."""

    def test_modules_parse(self):
        for name in ('schema.py', 'articles.py', 'edit.py', 'routes_edit.py'):
            path = ROOT / 'wiki' / name
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))


if __name__ == '__main__':
    unittest.main()
