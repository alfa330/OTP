# -*- coding: utf-8 -*-
"""Внутренние ссылки между статьями вики.

Почему этот файл существует и почему его написали ДО правки. Таблица
wiki_article_links лежала в схеме с первого дня раздела, читалась функцией
wiki_articles.backlinks и уезжала в ответ /articles/<slug> — но в неё НИКТО
никогда не писал. Обратные ссылки поэтому всегда были пусты, и ни один тест
этого не заметил: стенд роута отдаёт `cursor.fetchall() -> []`, то есть
«пустой список» и есть ожидаемый ответ пустой таблицы. Фича уехала в прод
мёртвой и молча.

Значит проверять надо ровно то, что тот стенд проверить не может:
  * разбор тела статьи (чистая функция, без базы);
  * ЗАПИСЬ связей — на курсоре-регистраторе, потому что настоящий INSERT
    проверить негде: боевое соединение read only по построению (prod_db.py);
  * границу периметра на ЧТЕНИИ — обе стороны связи;
  * страж: новая функция чтения связей обязана требовать периметр.
"""

import ast
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wiki import articles as wiki_articles  # noqa: E402
from wiki import edit as wiki_edit  # noqa: E402
from wiki import links as wiki_links  # noqa: E402


class ParseTest(unittest.TestCase):
    """Что считается ссылкой на статью, а что нет."""

    def test_relative_link_is_internal(self):
        self.assertEqual(
            wiki_links.article_slugs('<p>см <a href="?view=wiki&amp;article=tarify">Тарифы</a></p>'),
            ['tarify'])

    def test_view_may_be_absent(self):
        """Фронт проверяет view только если он задан — сервер обязан так же.

        Разойдись правила, и '?article=x' открывался бы по клику в тексте, но не
        показывался бы в «Связанных материалах». Расхождение, которое ничем не
        объяснить читателю.
        """
        self.assertEqual(wiki_links.article_slugs('<a href="?article=x">X</a>'), ['x'])

    def test_foreign_view_is_not_an_article(self):
        self.assertEqual(wiki_links.article_slugs('<a href="?view=tasks&amp;article=x">X</a>'), [])

    def test_percent_encoded_cyrillic(self):
        """buildArticleLink кодирует кириллицу, а слагов с ней в проде 25."""
        html = '<a href="?view=wiki&amp;article=%D1%82%D0%B0%D1%80%D0%B8%D1%84%D1%8B">Т</a>'
        self.assertEqual(wiki_links.article_slugs(html), ['тарифы'])

    def test_raw_cyrillic(self):
        self.assertEqual(
            wiki_links.article_slugs('<a href="?view=wiki&amp;article=тарифы">Т</a>'),
            ['тарифы'])

    def test_own_absolute_link(self):
        """Ссылку чаще всего получают кнопкой «Скопировать» — она абсолютная."""
        html = '<a href="https://alfa330.github.io/OTP?view=wiki&amp;article=x">X</a>'
        self.assertEqual(wiki_links.article_slugs(html), ['x'])

    def test_foreign_host_is_rejected(self):
        self.assertEqual(
            wiki_links.article_slugs('<a href="https://evil.example/OTP?article=x">X</a>'), [])

    def test_protocol_relative_host_is_rejected(self):
        """'//чужой/...' проходит санитайзер насквозь — схемы в строке нет.

        Разбор, который ищет в теле подстроку 'article=', записал бы это как
        внутреннюю связь, и чужая ссылка получила бы вид своей.
        """
        self.assertEqual(
            wiki_links.article_slugs('<a href="//evil.example/OTP?view=wiki&amp;article=x">X</a>'),
            [])

    def test_empty_anchor_is_not_a_link(self):
        """Пустой якорь — канал НЕВИДИМОЙ врезки в чужую статью.

        Санитайзер пропускает и '<a href=...></a>', и текст под font-size:0.
        В тексте автора не видно ничего, а у цели в «Сюда ссылаются» появляется
        его заголовок. Связью считаем только то, что читатель видит.
        """
        self.assertEqual(wiki_links.article_slugs('<a href="?article=secret"></a>'), [])

    def test_nbsp_only_anchor_is_not_a_link(self):
        self.assertEqual(wiki_links.article_slugs('<a href="?article=secret">&nbsp;</a>'), [])

    def test_image_anchor_is_visible(self):
        self.assertEqual(
            wiki_links.article_slugs('<a href="?article=x"><img src="/a.png"></a>'), ['x'])

    def test_toc_anchor_is_skipped(self):
        self.assertEqual(wiki_links.article_slugs('<a href="#glava-2">Глава</a>'), [])

    def test_mailto_is_skipped(self):
        self.assertEqual(wiki_links.article_slugs('<a href="mailto:a@b.c?article=x">почта</a>'), [])

    def test_service_characters_are_not_a_slug(self):
        """Слаг уходит в путь запроса — '/', '.', '%' там недопустимы."""
        self.assertEqual(wiki_links.article_slugs('<a href="?article=a/b">X</a>'), [])

    def test_duplicates_collapse_and_order_is_kept(self):
        """В проде 42 повторные ссылки внутри одного тела.

        Порядок — как в тексте: «Связанные материалы» читаются как оглавление.
        """
        html = ('<a href="?article=b">1</a><a href="?article=a">2</a>'
                '<a href="?article=b">3</a>')
        self.assertEqual(wiki_links.article_slugs(html), ['b', 'a'])

    def test_cap_limits_runaway_bodies(self):
        """Длина тела не ограничена ничем, троттлинга в разделе нет.

        Без потолка одним сохранением можно посадить свой заголовок в «Сюда
        ссылаются» у всех статей портала разом.
        """
        html = ''.join('<a href="?article=s%d">x</a>' % i for i in range(500))
        self.assertEqual(len(wiki_links.article_slugs(html)),
                         wiki_links.MAX_LINKS_PER_ARTICLE)

    def test_broken_markup_does_not_raise(self):
        """Связи — производная, текст — главное: разбор не имеет права падать."""
        self.assertIsInstance(wiki_links.article_slugs('<a href="?article=x">не закрыт'), list)

    def test_empty_content(self):
        for value in ('', None):
            self.assertEqual(wiki_links.article_slugs(value), [])


class RecordingCursor:
    """Курсор-регистратор: запоминает SQL и отдаёт заранее заданные строки."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []
        self.rowcount = 0
        self._last = ''

    def execute(self, sql, params=None):
        self._last = ' '.join(sql.split())
        self.calls.append((self._last, params))
        if self._last.startswith('SELECT id FROM wiki_articles'):
            self.rowcount = len(self.rows)
        else:
            self.rowcount = 1

    def fetchall(self):
        return list(self.rows) if self._last.startswith('SELECT') else []

    def fetchone(self):
        return None

    def sql_of(self, prefix):
        return [(sql, params) for sql, params in self.calls if sql.startswith(prefix)]


class WriteTest(unittest.TestCase):
    """link_content_articles — та самая запись, которой не было."""

    def test_writes_resolved_targets(self):
        cursor = RecordingCursor(rows=[(7,), (9,)])
        added, removed = wiki_edit.link_content_articles(
            cursor, 1, '<a href="?article=a">A</a><a href="?article=b">B</a>', editor_id=42)

        select = cursor.sql_of('SELECT id FROM wiki_articles')
        self.assertEqual(len(select), 1, 'слаги разрешаются ОДНИМ запросом')
        self.assertEqual(select[0][1], (['a', 'b'], 1))

        insert = cursor.sql_of('INSERT INTO wiki_article_links')
        self.assertEqual(len(insert), 1)
        self.assertIn('ON CONFLICT (source_id, target_id) DO NOTHING', insert[0][0])
        self.assertEqual(insert[0][1], (1, 42, [7, 9]))
        self.assertEqual((added, removed), (1, 1))

    def test_conflict_never_updates(self):
        """DO UPDATE снял бы признак ручной связи и падал бы на дублях.

        «cannot affect row a second time» уронило бы СОХРАНЕНИЕ СТАТЬИ целиком —
        транзакция в разделе одна на запрос.
        """
        cursor = RecordingCursor(rows=[(7,)])
        wiki_edit.link_content_articles(cursor, 1, '<a href="?article=a">A</a>')
        insert = cursor.sql_of('INSERT INTO wiki_article_links')[0][0]
        self.assertNotIn('DO UPDATE', insert)

    def test_stale_links_are_removed_but_manual_survive(self):
        cursor = RecordingCursor(rows=[(7,)])
        wiki_edit.link_content_articles(cursor, 1, '<a href="?article=a">A</a>')
        delete = cursor.sql_of('DELETE FROM wiki_article_links')
        self.assertEqual(len(delete), 1)
        self.assertIn('NOT is_manual', delete[0][0],
                      'подборка, собранная человеком, не должна сниматься пересборкой')
        self.assertEqual(delete[0][1], (1, [7]))

    def test_body_without_links_clears_auto_links(self):
        """«Убрали все ссылки» — законный случай, а не повод ничего не делать."""
        cursor = RecordingCursor()
        wiki_edit.link_content_articles(cursor, 1, '<p>без ссылок</p>')
        self.assertEqual(len(cursor.sql_of('DELETE FROM wiki_article_links')), 1)
        self.assertEqual(cursor.sql_of('INSERT INTO wiki_article_links'), [])

    def test_self_link_is_excluded_in_sql(self):
        cursor = RecordingCursor(rows=[(1,)])
        wiki_edit.link_content_articles(cursor, 1, '<a href="?article=self">S</a>')
        self.assertIn('id <> %s', cursor.sql_of('SELECT id FROM wiki_articles')[0][0])

    def test_perimeter_is_not_applied_on_write(self):
        """Периметр на записи сделал бы граф зависимым от того, кто сохранил.

        Один и тот же текст, сохранённый супервайзером и администратором вики,
        дал бы разный набор строк, а пересохранение более узким человеком МОЛЧА
        снесло бы чужие связи.
        """
        # Смотрим ИСПОЛНЯЕМОЕ тело, без строки документации: она как раз обязана
        # объяснять, почему периметра здесь нет, и упоминает visible_ids.
        tree = ast.parse(inspect.getsource(wiki_edit.link_content_articles))
        body = tree.body[0].body
        if (isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        code = '\n'.join(ast.dump(node) for node in body)
        self.assertNotIn('visible', code)


class WritersCoverageTest(unittest.TestCase):
    """Все пути записи тела обязаны пересобирать связи.

    Тел статьи в разделе четыре писателя, и один из них (restore_version) уже
    забывал производную работу: там не звался ни link_content_files, ни
    _sync_ai_index. Забудь его снова — и после отката версии блок «Сюда
    ссылаются» у чужой статьи показывал бы ссылку из текста, который человек
    только что откатил.
    """

    WRITERS = ('create_article', 'update_article', 'restore_version', 'fork_article')

    def test_every_body_writer_rebuilds_links(self):
        for name in self.WRITERS:
            with self.subTest(writer=name):
                source = inspect.getsource(getattr(wiki_edit, name))
                self.assertIn('link_content_articles', source)

    def test_every_body_writer_relinks_files(self):
        """Заодно закрываем соседний дефект: откат терял привязку картинок."""
        for name in self.WRITERS:
            with self.subTest(writer=name):
                source = inspect.getsource(getattr(wiki_edit, name))
                self.assertIn('link_content_files', source)

    def test_update_parses_sanitized_body(self):
        """Разбирать надо то, что легло в базу.

        Санитайзер экранирует амперсанд и может выбросить ссылку целиком; разбор
        присланного текста дал бы связи на то, чего в сохранённой статье нет.
        """
        source = inspect.getsource(wiki_edit.update_article)
        self.assertIn('link_content_articles(cursor, article_id, clean', source)
        self.assertNotIn("link_content_files(cursor, article_id, fields['content'])", source)


class ReadPerimeterTest(unittest.TestCase):
    """Обе стороны связи сужаются периметром читателя."""

    def test_related_keeps_text_order(self):
        cursor = RecordingCursor(rows=[(9, 'b', 'Б', 'published'),
                                       (7, 'a', 'А', 'draft')])
        rows = wiki_articles.related_articles(
            cursor, '<a href="?article=a">A</a><a href="?article=b">B</a>', 1, [7, 9])
        self.assertEqual([r['slug'] for r in rows], ['a', 'b'],
                         'порядок — как в тексте, а не как вернул Postgres')
        self.assertEqual(rows[1]['status'], 'published')

    def test_related_filters_by_perimeter(self):
        cursor = RecordingCursor(rows=[(7, 'a', 'А', 'draft')])
        wiki_articles.related_articles(cursor, '<a href="?article=a">A</a>', 1, [7])
        sql, params = cursor.calls[0]
        self.assertIn('a.id = ANY(%s)', sql)
        self.assertEqual(params, (['a'], [7], 1))

    def test_related_hides_target_outside_perimeter(self):
        """Заголовок закрытой статьи не должен раскрываться блоком."""
        cursor = RecordingCursor(rows=[])
        self.assertEqual(
            wiki_articles.related_articles(cursor, '<a href="?article=secret">S</a>', 1, [7]),
            [])

    def test_empty_perimeter_costs_no_query(self):
        cursor = RecordingCursor()
        self.assertEqual(wiki_articles.related_articles(cursor, '<a href="?article=a">A</a>',
                                                        1, []), [])
        self.assertEqual(cursor.calls, [])

    def test_body_without_links_costs_no_query(self):
        cursor = RecordingCursor()
        self.assertEqual(wiki_articles.related_articles(cursor, '<p>текст</p>', 1, [7]), [])
        self.assertEqual(cursor.calls, [])

    def test_backlinks_report_status(self):
        cursor = RecordingCursor(rows=[(7, 'a', 'А', 'draft')])
        rows = wiki_articles.backlinks(cursor, 1, [7])
        self.assertEqual(rows[0]['status'], 'draft')

    def test_backlinks_exclude_self(self):
        cursor = RecordingCursor(rows=[])
        wiki_articles.backlinks(cursor, 1, [7])
        self.assertIn('a.id <> %s', cursor.calls[0][0])


class PerimeterGuardTest(unittest.TestCase):
    """Страж: читать связи без периметра нельзя.

    Существующий страж границы пространства (test_wiki_directory_space) сюда не
    годится — он требует у функции параметр space_id и знает только справочники.
    Здесь правило своё: КАЖДАЯ функция, чей SQL упоминает wiki_article_links,
    обязана принимать visible_ids. Иначе дыра вернётся тем же способом, каким
    приходила в справочники офисов, — следующей добавленной функцией чтения.

    Разбор идёт по AST файла на диске, а не по номерам строк: файл живёт, и
    привязка к строкам протухла бы на первой же правке соседа.
    """

    def test_every_reader_of_links_takes_the_perimeter(self):
        source = Path(wiki_articles.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        checked = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(source, node) or ''
            if 'wiki_article_links' not in body:
                continue
            args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            checked.append(node.name)
            self.assertIn('visible_ids', args,
                          '%s читает wiki_article_links без периметра' % node.name)
        self.assertTrue(checked, 'страж не нашёл ни одной функции — проверьте разбор')


if __name__ == '__main__':
    unittest.main()
