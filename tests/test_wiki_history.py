# -*- coding: utf-8 -*-
"""История версий статьи: сборка редакций и сравнение.

Почему этот файл существует. Таблица `wiki_article_versions` пишется с первого
дня раздела, но НИКЕМ не читалась: эндпоинт /versions есть, а интерфейса нет —
то есть «кто менял статью» нельзя было спросить нигде. Когда экран появился,
выяснилось главное: строку таблицы нельзя показать как редакцию. Снимок
делается ПЕРЕД правкой, поэтому в одной строке лежит текст ПРОШЛОГО автора и
имя НЫНЕШНЕГО (см. шапку wiki/history.py).

Проверяем ровно это — смысл, а не SQL:
  * сдвиг авторства на строку назад;
  * слияние повторов, которых в проде большинство (дубль «создание → снимок
    перед первой правкой» есть у ВСЕХ 304 статей, где строк хотя бы две);
  * сохранения, не менявшие текст, — они обязаны остаться в виде приписки, а не
    пропасть и не размножить список;
  * авторство текущей редакции при архивировании — оно идёт мимо снимков;
  * время строкой ISO: Flask сериализует datetime как «… GMT», и журнал вики на
    этом уже уезжал на пять часов вперёд (structure.list_audit);
  * сравнение: разбивка тела на строки, пословная разметка и случай «менялась
    только разметка», который иначе выглядит как поломка экрана.
"""

import ast
import datetime
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wiki import history  # noqa: E402


def stamp(minute, day=24):
    return datetime.datetime(2026, 8, day, 16, minute, 0)


def row(id_, number, content_hash, *, editor=1, name='Иванов', minute=0,
        comment=None, title='Статья', summary=None, status='draft',
        restored_from=None, day=24):
    return {'id': id_, 'version_number': number, 'title': title,
            'summary': summary, 'status': status, 'content_hash': content_hash,
            'content_len': len(content_hash), 'change_comment': comment,
            'editor_id': editor, 'editor_name': name,
            'created_at': stamp(minute, day),
            'restored_from_version_id': restored_from}


def current(content_hash, *, title='Статья', summary=None, status='draft',
            updated_by=1, updated_by_name='Иванов', updated_at=None):
    return {'title': title, 'summary': summary, 'status': status,
            'content_hash': content_hash, 'content_len': len(content_hash),
            'updated_by': updated_by, 'updated_by_name': updated_by_name,
            'updated_at': updated_at}


class BuildHistoryTest(unittest.TestCase):

    def test_creation_snapshot_and_first_edit_snapshot_merge(self):
        """Снимок «перед первой правкой» повторяет создание — это одна редакция.

        В проде так у ВСЕХ 304 статей, где есть вторая строка. Покажи мы обе,
        история каждой статьи начиналась бы с двух одинаковых записей.
        """
        rows = [
            row(1, 1, 'AAA', minute=0, comment='Создание статьи'),
            row(2, 2, 'AAA', minute=5, editor=2, name='Петров'),
        ]
        items = history.build_history(rows, current('BBB', updated_by=2,
                                                    updated_by_name='Петров',
                                                    updated_at=stamp(5)))
        self.assertEqual(len(items), 2)
        # Верхняя — текущая, её сделал Петров правкой в 16:05.
        self.assertTrue(items[0]['is_current'])
        self.assertEqual(items[0]['editor_name'], 'Петров')
        # Нижняя — создание, и «ещё одного сохранения» у неё нет: вторая строка
        # это тот же самый текст того же самого автора.
        self.assertTrue(items[1]['is_first'])
        self.assertEqual(items[1]['comment'], 'Создание статьи')
        self.assertEqual(items[1]['extra_saves'], [])
        self.assertEqual(items[1]['saves'], 1)

    def test_author_comes_from_previous_row(self):
        """Текст из строки N написал тот, кто указан в строке N−1."""
        rows = [
            row(1, 1, 'AAA', minute=0, editor=1, name='Иванов', comment='Создание статьи'),
            row(2, 2, 'AAA', minute=5, editor=2, name='Петров'),
            row(3, 3, 'BBB', minute=9, editor=3, name='Сидоров'),
        ]
        items = history.build_history(rows, current('CCC', updated_by=3,
                                                    updated_by_name='Сидоров',
                                                    updated_at=stamp(9)))
        # BBB лежит в строке 3, но написал его Петров — это он правил в 16:05.
        middle = items[1]
        self.assertEqual(middle['content_hash'], 'BBB')
        self.assertEqual(middle['editor_name'], 'Петров')
        self.assertEqual(middle['version_id'], 3)

    def test_silent_saves_are_counted_not_listed(self):
        """Правка тегов и разделов до текста не доходит, а строку версии пишет."""
        rows = [
            row(1, 1, 'AAA', minute=0, comment='Создание статьи'),
            row(2, 2, 'AAA', minute=1),
            row(3, 3, 'AAA', minute=2, editor=2, name='Петров'),
            row(4, 4, 'AAA', minute=3, editor=3, name='Сидоров'),
            row(5, 5, 'BBB', minute=4, editor=4, name='Кузнецов'),
        ]
        items = history.build_history(rows, current('BBB', updated_by=4,
                                                    updated_by_name='Кузнецов',
                                                    updated_at=stamp(4)))
        self.assertEqual(len(items), 2)
        first = items[1]
        # Три сохранения (16:01, 16:02, 16:03) ничего не изменили: они приписка,
        # а не редакции. Дубль создания среди них не считается.
        self.assertEqual(first['saves'], 3)
        self.assertEqual([save['editor_name'] for save in first['extra_saves']],
                         ['Иванов', 'Петров'])

    def test_last_silent_save_is_kept(self):
        """Случай «Всех акций»: последняя правка не тронула текст.

        Такая строка не сливается ни с чем следующим — следующего нет, — и без
        отдельной ветки её автор пропал бы из истории совсем. В проде это
        перенос статьи 18.08.2026 с комментарием «Убран дубль».
        """
        rows = [
            # Иванов создал, Иванов же в 16:01 переписал AAA на BBB…
            row(1, 1, 'AAA', minute=0, comment='Создание статьи'),
            row(2, 2, 'AAA', minute=1),
            # …а Петров и Сидоров сохраняли статью, не трогая текст.
            row(3, 3, 'BBB', minute=2, editor=2, name='Петров'),
            row(4, 4, 'BBB', minute=3, editor=9, name='Сидоров', comment='Убран дубль'),
        ]
        items = history.build_history(rows, current('BBB', updated_by=1,
                                                    updated_by_name='Иванов',
                                                    updated_at=stamp(1)))
        self.assertEqual(len(items), 2)
        top = items[0]
        self.assertTrue(top['is_current'])
        self.assertEqual(top['editor_name'], 'Иванов')
        # Оба «молчаливых» на месте, и последний из них — тот самый, который
        # без отдельной ветки терялся бы вместе со своим комментарием.
        self.assertEqual([save['editor_name'] for save in top['extra_saves']],
                         ['Петров', 'Сидоров'])
        self.assertIn('Убран дубль', [save['comment'] for save in top['extra_saves']])

    def test_archived_article_keeps_its_own_author(self):
        """Архивирование меняет статью БЕЗ снимка — автора берём из статьи.

        delete_article правит только статус и updated_at. Возьми мы автора из
        последней строки версий, архив приписался бы прошлому редактору.
        """
        rows = [
            row(1, 1, 'AAA', minute=0, comment='Создание статьи', status='published'),
            row(2, 2, 'AAA', minute=1, status='published'),
        ]
        items = history.build_history(
            rows, current('AAA', status='archived', updated_by=77,
                          updated_by_name='Администратор', updated_at=stamp(30)))
        self.assertEqual(items[0]['editor_name'], 'Администратор')
        self.assertEqual(items[0]['status'], 'archived')
        self.assertEqual(items[0]['changed'], ['status'])
        self.assertIsNone(items[0]['version_id'])

    def test_restore_marker_lands_on_the_produced_revision(self):
        """Пометка отката стоит на строке ПЕРЕД восстановлением…

        …а описывает редакцию, которую восстановление создало. Значит достаться
        она должна той же редакции, что и авторство, — иначе «Откат» окажется на
        соседней записи.
        """
        rows = [
            row(1, 1, 'AAA', minute=0, comment='Создание статьи'),
            row(2, 2, 'AAA', minute=1),
            row(3, 3, 'BBB', minute=2, comment='Восстановление прежней редакции',
                restored_from=1),
        ]
        items = history.build_history(rows, current('AAA', updated_at=stamp(2)))
        self.assertEqual(items[0]['restored_from_version_id'], 1)
        self.assertIsNone(items[-1]['restored_from_version_id'])

    def test_restore_marker_survives_a_microsecond_gap(self):
        """Снимок и UPDATE могут разойтись на микросекунды — это одна правка.

        В проде отметки совпадают ровно (одна транзакция, один
        CURRENT_TIMESTAMP), но опираться на точное равенство нельзя: на стенде,
        где каждый запрос сам себе транзакция, пометка «Откат» вместе с
        комментарием правки молча пропадала.
        """
        rows = [
            row(1, 1, 'AAA', minute=0, comment='Создание статьи'),
            row(2, 2, 'BBB', minute=1, comment='Восстановление прежней редакции', restored_from=1),
        ]
        state = current('AAA', updated_at=stamp(1) + datetime.timedelta(microseconds=900))
        items = history.build_history(rows, state)
        self.assertEqual(items[0]['restored_from_version_id'], 1)
        self.assertEqual(items[0]['comment'], 'Восстановление прежней редакции')

    def test_stale_snapshot_does_not_lend_its_comment(self):
        """Архивирование через неделю не имеет права забрать чужой комментарий."""
        rows = [
            row(1, 1, 'AAA', minute=0, comment='Создание статьи', status='published'),
            row(2, 2, 'AAA', minute=1, comment='Правка текста', status='published'),
        ]
        items = history.build_history(
            rows, current('AAA', status='archived', updated_at=stamp(1, day=31)))
        self.assertIsNone(items[0]['comment'])
        self.assertEqual(items[0]['changed'], ['status'])

    def test_article_without_versions(self):
        """Строк нет — история всё равно должна открыться одной записью."""
        items = history.build_history([], current('AAA', updated_at=stamp(0)))
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['is_current'])
        self.assertTrue(items[0]['is_first'])
        self.assertEqual(items[0]['changed'], [])

    def test_changed_fields_are_named(self):
        rows = [
            row(1, 1, 'AAA', minute=0, title='Было', summary=None, comment='Создание статьи'),
            row(2, 2, 'AAA', minute=1, title='Было', summary=None),
        ]
        items = history.build_history(
            rows, current('BBB', title='Стало', summary='Появилась',
                          status='published', updated_at=stamp(1)))
        self.assertEqual(items[0]['changed'], ['content', 'title', 'summary', 'status'])

    def test_timestamps_are_plain_iso_strings(self):
        """Страж от «+5 часов».

        Время в базе уже алматинское, а Flask сериализует datetime как «… GMT».
        На журнале вики этот баг уже случался; сериализация обязана происходить
        здесь, а не полагаться на Flask.
        """
        rows = [row(1, 1, 'AAA', minute=0, comment='Создание статьи'),
                row(2, 2, 'AAA', minute=1)]
        items = history.build_history(rows, current('BBB', updated_at=stamp(1)))
        for item in items:
            self.assertIsInstance(item['created_at'], str)
            self.assertNotIn('GMT', item['created_at'])
            for save in item['extra_saves']:
                self.assertIsInstance(save['created_at'], str)


class BlocksTest(unittest.TestCase):

    def test_blocks_follow_paragraphs_lists_and_table_rows(self):
        blocks = history.html_to_blocks(
            '<p>Первый абзац</p><ul><li>раз</li><li>два</li></ul>'
            '<table><tr><td>Тариф</td><td>500</td></tr></table>')
        self.assertEqual(blocks, ['Первый абзац', 'раз', 'два', 'Тариф | 500'])

    def test_inline_markup_does_not_become_a_difference(self):
        """Тег внутри строки снимается без следа.

        Замени мы его пробелом, «<strong>3 секунд</strong>» и «3 секунд»
        разошлись бы, и правка выделения показывалась бы как правка текста.
        """
        self.assertEqual(history.html_to_blocks('<p>в течение <strong>3 секунд</strong></p>'),
                         ['в течение 3 секунд'])

    def test_base64_images_do_not_reach_the_diff(self):
        """Картинка в base64 — 81 % объёма тела в проде и ноль смысла в сравнении."""
        blocks = history.html_to_blocks('<p>До</p><img src="data:image/png;base64,AAAA"><p>После</p>')
        self.assertEqual(blocks, ['До', '[изображение]', 'После'])

    def test_escaped_tag_survives(self):
        """«&lt;p&gt;» в тексте — это текст, а не тег: сущности раскрываем после."""
        self.assertEqual(history.html_to_blocks('<p>Пишите &lt;p&gt; в поле</p>'),
                         ['Пишите <p> в поле'])


    def test_cell_wrapped_in_paragraph_does_not_split_the_row(self):
        """Дефект прода: редактор кладёт в ячейку абзац.

        `<td><p>iTaxi</p></td>` — так у 20 статей, в том числе у «Всех акций».
        «</p>» давал перевод строки раньше, чем «</td>» успевал поставить
        разделитель, и запись на одиннадцать граф уезжала в сравнение
        одиннадцатью строками по значению в каждой: «Все города», «01.08.2026»,
        «Да», «5» — без подписей и без намёка, что это одна строка таблицы.
        """
        blocks = history.html_to_blocks(
            '<table><tbody><tr><td><p>iTaxi</p></td><td><p>Все города</p></td>'
            '<td><p>01.08.2026</p></td></tr></tbody></table>')
        self.assertEqual(blocks, ['iTaxi | Все города | 01.08.2026'])

    def test_table_row_carries_cells_and_column_names(self):
        lines = history.html_to_lines(
            '<table><thead><tr><th>№</th><th>Парк</th><th>Актуальность</th></tr></thead>'
            '<tbody><tr><td><p>4</p></td><td><p>iTaxi</p></td><td><p>Активная</p></td></tr>'
            '</tbody></table>')
        self.assertEqual([line['head'] for line in lines], [True, False])
        self.assertEqual(lines[1]['cells'], ['4', 'iTaxi', 'Активная'])
        # Имена граф берутся из шапки, а не выдумываются экраном.
        self.assertEqual(lines[1]['columns'], ['№', 'Парк', 'Актуальность'])
        # Шапка сама себе имена не раздаёт.
        self.assertIsNone(lines[0]['columns'])

    def test_table_without_head_has_cells_but_no_column_names(self):
        lines = history.html_to_lines('<table><tr><td>Тариф</td><td>500</td></tr></table>')
        self.assertEqual(lines[0]['cells'], ['Тариф', '500'])
        self.assertIsNone(lines[0]['columns'])

    def test_empty_table_row_is_skipped(self):
        self.assertEqual(history.html_to_blocks(
            '<p>А</p><table><tr><td></td><td><p></p></td></tr></table>'), ['А'])


class TableDiffTest(unittest.TestCase):
    """Правка внутри записи таблицы разбирается по графам."""

    HEAD = ('<table><thead><tr><th>№</th><th>Парк</th><th>Город</th>'
            '<th>Актуальность</th></tr></thead><tbody>')

    def row(self, number, park, city, live):
        return ('<tr><td><p>%s</p></td><td><p>%s</p></td><td><p>%s</p></td>'
                '<td><p>%s</p></td></tr>' % (number, park, city, live))

    def table(self, *rows):
        return self.HEAD + ''.join(rows) + '</tbody></table>'

    def test_one_changed_cell_is_named_by_its_column(self):
        before = self.table(self.row(4, 'iTaxi', 'Все города', 'Активная'))
        after = self.table(self.row(4, 'iTaxi', 'Все города', 'Завершена'))
        rows = history.diff_blocks(before, after)['rows']
        change = [r for r in rows if r['op'] == 'change']
        self.assertEqual(len(change), 1)
        # Правка одной графы — это ОДНА строка вывода, а не одиннадцать.
        self.assertEqual([c['name'] for c in change[0]['cells'] if c['changed']],
                         ['Актуальность'])
        cell = [c for c in change[0]['cells'] if c['changed']][0]
        self.assertEqual((cell['before'], cell['after']), ('Активная', 'Завершена'))
        # Нетронутые графы приезжают тоже — экран решает сам, показывать ли их.
        self.assertEqual(len(change[0]['cells']), 4)

    def test_added_row_keeps_its_cells(self):
        before = self.table(self.row(4, 'iTaxi', 'Все города', 'Активная'))
        after = self.table(self.row(4, 'iTaxi', 'Все города', 'Активная'),
                           self.row(5, 'Аманат', 'Астана', 'Активная'))
        rows = history.diff_blocks(before, after)['rows']
        ins = [r for r in rows if r['op'] == 'ins']
        self.assertEqual(len(ins), 1)
        self.assertEqual(ins[0]['cells'], ['5', 'Аманат', 'Астана', 'Активная'])
        self.assertEqual(ins[0]['columns'][1], 'Парк')

    def test_different_cell_count_falls_back_to_text(self):
        """Объединённые ячейки (colspan есть у 30 статей прода).

        Назвать графу номером, когда их разное количество, — соврать: подписи
        разъедутся. Такая пара сравнивается как обычный текст.
        """
        before = ('<table><tr><td>А</td><td>Б</td><td>В</td></tr></table>')
        after = ('<table><tr><td colspan="2">А Б</td><td>Г</td></tr></table>')
        rows = history.diff_blocks(before, after)['rows']
        change = [r for r in rows if r['op'] == 'change']
        self.assertEqual(len(change), 1)
        self.assertNotIn('cells', change[0])
        self.assertIn('before_parts', change[0])


class DiffTest(unittest.TestCase):

    def test_word_level_marks_inside_a_changed_line(self):
        diff = history.diff_blocks('<p>Один два три</p>', '<p>Один ДВА три</p>')
        self.assertEqual([r['op'] for r in diff['rows']], ['change'])
        cut = [p['text'] for p in diff['rows'][0]['before_parts'] if p['op'] == 'cut']
        add = [p['text'] for p in diff['rows'][0]['after_parts'] if p['op'] == 'add']
        self.assertEqual(cut, ['два '])
        self.assertEqual(add, ['ДВА '])

    def test_unrelated_lines_are_not_paired(self):
        """Непохожие строки — это «убрали одну, написали другую», а не правка.

        Пословное сравнение чужих друг другу строк рисует случайные совпадения
        предлогов и читается хуже, чем два честных блока.
        """
        diff = history.diff_blocks('<p>Условия акции для водителей</p>',
                                   '<p>Телефон поддержки 7777</p>')
        self.assertEqual([r['op'] for r in diff['rows']], ['del', 'ins'])

    def test_unchanged_middle_is_folded(self):
        before = '<p>А</p>' + ''.join('<p>строка %d</p>' % i for i in range(30)) + '<p>Я</p>'
        after = '<p>А</p>' + ''.join('<p>строка %d</p>' % i for i in range(30)) + '<p>Конец</p>'
        diff = history.diff_blocks(before, after)
        self.assertIn('gap', [r['op'] for r in diff['rows']])
        # Тридцать две строки текста, а на экране — три строки контекста и правка.
        self.assertLess(len(diff['rows']), 8)

    def test_row_cap_is_reported(self):
        """Обрезали вывод — сказали об этом. Молчаливый потолок читается как
        «различий больше нет»."""
        before = ''.join('<p>было %d</p>' % i for i in range(300))
        after = ''.join('<p>стало %d</p>' % i for i in range(300))
        diff = history.diff_blocks(before, after, max_rows=20)
        self.assertTrue(diff['truncated'])
        self.assertEqual(len(diff['rows']), 20)
        # Счётчики считают ВСЁ, а не показанное: «+20» при трёхстах правках врёт.
        self.assertEqual(diff['added'], 300)
        self.assertEqual(diff['removed'], 300)

    def test_markup_only_change_is_named(self):
        """Абзац завернули в цитату и выделили — слова те же.

        Реальный случай прода (статья «Приветствие», 24.08.2026). Без отдельного
        признака экран сказал бы «различий нет» там, где в списке редакций стоит
        «Текст», и выглядело бы это поломкой.
        """
        before = {'title': 'П', 'summary': None, 'status': 'draft',
                  'content': '<p>«Сервис Tez Taxi, меня зовут [имя]»</p>'}
        after = {'title': 'П', 'summary': None, 'status': 'draft',
                 'content': '<blockquote><p><strong>«Сервис Tez Taxi, меня зовут [имя]»</strong></p></blockquote>'}
        diff = history.diff_states(before, after)
        self.assertTrue(diff['markup_only'])
        self.assertFalse(diff['identical'])
        self.assertEqual(diff['body']['added'], 0)

    def test_identical_states(self):
        state = {'title': 'П', 'summary': 'A', 'status': 'draft', 'content': '<p>Текст</p>'}
        diff = history.diff_states(state, dict(state))
        self.assertTrue(diff['identical'])
        self.assertFalse(diff['markup_only'])

    def test_fields_are_reported_separately(self):
        diff = history.diff_states(
            {'title': 'Было', 'summary': None, 'status': 'draft', 'content': '<p>x</p>'},
            {'title': 'Стало', 'summary': 'Есть', 'status': 'published', 'content': '<p>x</p>'})
        self.assertEqual(diff['title'], {'before': 'Было', 'after': 'Стало'})
        self.assertEqual(diff['summary'], {'before': None, 'after': 'Есть'})
        self.assertEqual(diff['status'], {'before': 'draft', 'after': 'published'})
        self.assertFalse(diff['identical'])


class GuestGuardTest(unittest.TestCase):
    """Страж: обе двери истории обязаны отказывать гостю.

    Гостевой доступ открывает статью человеку со стороны на две недели. Прошлые
    редакции — это ровно то, что из статьи убрали, и такого он не получал.
    Проверяем разбором исходника: обработчики лежат внутри register(), и вызвать
    их в тесте без всего блюпринта нельзя.
    """

    def _handler(self, name):
        source = Path(ROOT, 'wiki', 'routes_edit.py').read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        self.fail('обработчик %s не найден' % name)

    def _calls(self, node):
        return {child.func.id for child in ast.walk(node)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)}

    def test_history_routes_check_guest(self):
        for name in ('wiki_article_history', 'wiki_article_history_diff'):
            self.assertIn('_history_denied', self._calls(self._handler(name)),
                          '%s не спрашивает про гостевой доступ' % name)

    def test_guest_gate_asks_the_grant_itself(self):
        """Гость опознаётся выдачей на эту статью, а не основанием доступа.

        Соблазн проверить `permissions['_reason'] == 'гостевой доступ'` большой:
        именно так витрина решает, рисовать ли бейдж. Но сюда права приходят из
        effective_permissions, а он считает их БЕЗ гостевой поправки
        (permissions_for_articles не передаёт guest_allows_read) — то есть
        такого основания здесь не бывает никогда, и проверка молча пропускала бы
        гостя. Так и было на стенде: гость получал полную историю статьи,
        открытой ему на неделю.
        """
        source = Path(ROOT, 'wiki', 'routes_edit.py').read_text(encoding='utf-8')
        gate = source[source.index('def _history_denied'):source.index('/history\')')]
        self.assertIn('article_grant_expiry', gate)
        self.assertIn("permissions.get('can_read')", gate)
        self.assertNotIn("_reason", gate.split('"""')[2])


class ContractTest(unittest.TestCase):
    """Список редакций не имеет права тянуть тела статей.

    У «Всех акций» тело версии весит 90 КБ; десять таких в одном ответе — это
    мегабайт на открытие модалки. Поэтому шапки версий читаются отпечатком.
    """

    def test_version_headers_select_hash_not_content(self):
        from wiki import edit as wiki_edit
        source = inspect.getsource(wiki_edit.version_headers)
        self.assertIn('md5(COALESCE(v.content', source)
        self.assertNotIn('v.content,\n', source)


if __name__ == '__main__':
    unittest.main()
