# -*- coding: utf-8 -*-
"""CLI задач: вложения, история, «ждёт меня».

Тесты герметичные: сеть не трогаем вовсе, документы для разбора собираем здесь же.
Настоящие вложения из прода в фикстуры не кладём — это данные заказчика.

Отдельно сторожим расхождение с сервером: пределы загрузки, список переходов,
принимающих файлы, и набор причин «задача ждёт меня» продублированы в четырёх
местах, и молча разъехаться им нельзя.
"""

import io
import os
import re
import sys
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import task_board as cli  # noqa: E402


# ─────────────── Документы-фикстуры ───────────────

def make_docx(paragraphs):
    """Минимальный, но настоящий пакет Word — его читает тот же mammoth, что и в вики."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="word/document.xml"/></Relationships>'
    )
    body = ''.join('<w:p><w:r><w:t>%s</w:t></w:r></w:p>' % text for text in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>%s</w:body></w:document>' % body
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('_rels/.rels', rels)
        archive.writestr('word/document.xml', document)
    return buffer.getvalue()


def make_xlsx(rows):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_pptx(slides):
    """Пакет достаточной полноты: наш разбор читает ppt/slides/slideN.xml напрямую."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        for index, texts in enumerate(slides, start=1):
            runs = ''.join('<a:t>%s</a:t>' % text for text in texts)
            archive.writestr(
                'ppt/slides/slide%d.xml' % index,
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
                ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                '%s</p:sld>' % runs,
            )
    return buffer.getvalue()


PNG_BYTES = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 32)


# ─────────────── Опознание формата ───────────────

class ExtensionRecoveryTests(unittest.TestCase):
    """Расширение восстанавливается тремя ступенями: имя → content_type → сигнатура."""

    def test_extension_from_name_wins(self):
        self.assertEqual(cli.attachment_extension('ТЗ.docx', 'text/plain'), '.docx')

    def test_content_type_used_when_name_has_no_extension(self):
        # Ровно случай задачи #160: вложение в базе называется буквально «docx».
        self.assertEqual(
            cli.attachment_extension(
                'docx',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            ),
            '.docx',
        )

    def test_content_type_with_charset_suffix(self):
        self.assertEqual(cli.attachment_extension('файл', 'text/plain; charset=utf-8'), '.txt')

    def test_signature_distinguishes_office_packages(self):
        # docx/xlsx/pptx — все zip: без загляда внутрь их не различить.
        self.assertEqual(cli.attachment_extension('x', 'application/octet-stream', make_docx(['а'])), '.docx')
        self.assertEqual(cli.attachment_extension('x', 'application/octet-stream', make_xlsx([['а']])), '.xlsx')
        self.assertEqual(cli.attachment_extension('x', 'application/octet-stream', make_pptx([['а']])), '.pptx')

    def test_signature_for_pdf_and_png_and_plain_zip(self):
        self.assertEqual(cli.attachment_extension('x', '', b'%PDF-1.7 ...'), '.pdf')
        self.assertEqual(cli.attachment_extension('x', '', PNG_BYTES), '.png')
        empty_zip = io.BytesIO()
        zipfile.ZipFile(empty_zip, 'w').close()
        self.assertEqual(cli.attachment_extension('x', '', empty_zip.getvalue()), '.zip')

    def test_unknown_stays_empty(self):
        self.assertEqual(cli.attachment_extension('x', 'application/octet-stream', b'\x01\x02\x03'), '')


class SafeFilenameTests(unittest.TestCase):
    def test_cyrillic_name_collapsed_by_server_gets_extension_back(self):
        # werkzeug прогоняет имя через secure_filename и выбрасывает не-ASCII:
        # «проверка.xlsx» доезжает до базы как «xlsx». Расширение дописываем сами.
        name = cli.safe_attachment_filename(
            {'id': 59, 'file_name': 'xlsx', 'content_type': None},
            make_xlsx([['а', 1]]),
        )
        self.assertEqual(name, '59_xlsx.xlsx')

    def test_id_prefix_keeps_files_apart(self):
        first = cli.safe_attachment_filename({'id': 1, 'file_name': 'act.pdf'})
        second = cli.safe_attachment_filename({'id': 2, 'file_name': 'act.pdf'})
        self.assertEqual((first, second), ('1_act.pdf', '2_act.pdf'))

    def test_path_traversal_is_stripped(self):
        for raw in ('../../etc/passwd', r'..\..\windows\system32\cfg.ini', '/abs/path/file.txt'):
            name = cli.safe_attachment_filename({'id': 7, 'file_name': raw})
            self.assertNotIn('..', name)
            self.assertNotIn('/', name)
            self.assertNotIn('\\', name)

    def test_missing_name_falls_back_to_id(self):
        self.assertTrue(cli.safe_attachment_filename({'id': 8, 'file_name': ''}).startswith('8_'))

    def test_cyrillic_name_survives(self):
        self.assertEqual(
            cli.safe_attachment_filename({'id': 9, 'file_name': 'Постановка.docx'}),
            '9_Постановка.docx',
        )


class DispositionTests(unittest.TestCase):
    def test_utf8_variant_preferred_over_ascii_stub(self):
        # Flask отдаёт оба имени, и ASCII-огрызок идёт первым: если читать его,
        # «Задачи_2026-08-18.xlsx» превращается в «_2026-08-18.xlsx».
        header = ("attachment; filename=_2026-08-18.xlsx; "
                  "filename*=UTF-8''%D0%97%D0%B0%D0%B4%D0%B0%D1%87%D0%B8_2026-08-18.xlsx")
        self.assertEqual(cli.filename_from_disposition(header), 'Задачи_2026-08-18.xlsx')

    def test_plain_ascii_filename(self):
        self.assertEqual(cli.filename_from_disposition('attachment; filename="report.xlsx"'), 'report.xlsx')

    def test_missing_header(self):
        self.assertEqual(cli.filename_from_disposition(None), '')


# ─────────────── Текст вложения ───────────────

class ExtractTextTests(unittest.TestCase):
    def test_docx_read_through_wiki_importer(self):
        data = make_docx(['Общая логика обращения', 'Оператор выбирает одну тематику.'])
        text, note = cli.extract_attachment_text('docx', 'application/octet-stream', data)
        self.assertIn('Word', note)
        self.assertIn('Общая логика обращения', text)
        self.assertIn('Оператор выбирает одну тематику.', text)

    def test_xlsx_rows_become_text(self):
        text, note = cli.extract_attachment_text('x.xlsx', '', make_xlsx([['дата', 'линия'], ['30.07', 83]]))
        self.assertIn('Excel', note)
        self.assertIn('линия', text)
        self.assertIn('83', text)

    def test_pptx_slides_are_numbered(self):
        text, note = cli.extract_attachment_text('x.pptx', '', make_pptx([['Слайд про ТЗ'], ['Второй']]))
        self.assertEqual(note, 'презентация')
        self.assertIn('── Слайд 1 ──', text)
        self.assertIn('── Слайд 2 ──', text)
        self.assertIn('Слайд про ТЗ', text)

    def test_cp1251_text_decoded(self):
        text, note = cli.extract_attachment_text('note.txt', 'text/plain', 'Кириллица'.encode('cp1251'))
        self.assertEqual(text, 'Кириллица')
        self.assertIn('cp1251', note)

    def test_image_returns_no_text_but_says_what_to_do(self):
        text, note = cli.extract_attachment_text('shot.png', 'image/png', PNG_BYTES)
        self.assertIsNone(text)
        self.assertIn('посмотри', note)

    def test_video_returns_no_text(self):
        text, note = cli.extract_attachment_text('screen.mp4', 'video/mp4', b'\x00\x00\x00\x18ftyp')
        self.assertIsNone(text)

    def test_zip_lists_entries(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('внутри/файл.txt', 'данные')
        text, note = cli.extract_attachment_text('пакет.zip', 'application/zip', buffer.getvalue())
        self.assertIn('архив', note)
        self.assertIn('внутри/файл.txt', text)

    def test_broken_document_degrades_without_raising(self):
        # Битый docx — не авария: список вложений должен дорисоваться до конца.
        text, note = cli.extract_attachment_text('bad.docx', '', b'PK\x03\x04' + 'ломаный'.encode())
        self.assertIsNone(text)
        self.assertTrue(note)

    def test_binary_without_text_reports_honestly(self):
        text, note = cli.extract_attachment_text('x.bin', 'application/octet-stream', bytes(range(256)))
        self.assertIsNone(text)
        self.assertIn('двоичный', note)


# ─────────────── Отправка файлов ───────────────

class UploadPayloadTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()

    def _make(self, name, size=16):
        path = os.path.join(self.dir, name)
        with open(path, 'wb') as handle:
            handle.write(b'x' * size)
        return path

    def test_none_when_nothing_attached(self):
        self.assertIsNone(cli.upload_payload(None))
        self.assertIsNone(cli.upload_payload([]))

    def test_field_name_and_guessed_mime(self):
        # Сервер читает request.files.getlist('files') — имя поля ровно такое.
        payload = cli.upload_payload([self._make('a.pdf')])
        field, (name, data, content_type) = payload[0]
        self.assertEqual(field, 'files')
        self.assertEqual(name, 'a.pdf')
        self.assertEqual(data, b'x' * 16)
        self.assertEqual(content_type, 'application/pdf')

    def test_unknown_extension_falls_back_to_octet_stream(self):
        payload = cli.upload_payload([self._make('a.какое-то')])
        self.assertEqual(payload[0][1][2], 'application/octet-stream')

    def test_too_many_files_rejected_before_request(self):
        paths = [self._make('f%d.txt' % index) for index in range(cli.MAX_UPLOAD_FILES + 1)]
        with self.assertRaises(SystemExit):
            cli.upload_payload(paths)

    def test_oversized_file_rejected_before_request(self):
        with self.assertRaises(SystemExit):
            cli.upload_payload([self._make('big.bin', cli.MAX_UPLOAD_BYTES + 1)])

    def test_missing_file_reported(self):
        with self.assertRaises(SystemExit):
            cli.upload_payload([os.path.join(self.dir, 'нет-такого.txt')])


# ─────────────── «Задача ждёт меня» ───────────────

ME = 2
OTHER = 99


def task(**fields):
    base = {
        'id': 1,
        'status': 'assigned',
        'is_backlog': False,
        'assignee': {'id': ME},
        'creator': {'id': OTHER},
        'requested_by': None,
        'due_at': None,
        'updated_at': '2026-08-18T10:00:00',
        'action_seen': None,
    }
    base.update(fields)
    return base


class ActionNeedTests(unittest.TestCase):
    """Правила продублированы в четырёх местах — здесь стоит их питонья копия."""

    def setUp(self):
        self.now = datetime(2026, 8, 18, 12, 0)
        self.past = (self.now - timedelta(hours=5)).isoformat()
        self.future = (self.now + timedelta(hours=5)).isoformat()

    def test_fresh_when_assigned_and_not_started(self):
        self.assertEqual(cli.task_action_need(task(), ME, self.now), 'fresh')

    def test_overdue_beats_returned_and_fresh(self):
        self.assertEqual(cli.task_action_need(task(due_at=self.past), ME, self.now), 'overdue')
        self.assertEqual(
            cli.task_action_need(task(status='returned', due_at=self.past), ME, self.now),
            'overdue',
        )

    def test_returned_when_deadline_still_ahead(self):
        self.assertEqual(
            cli.task_action_need(task(status='returned', due_at=self.future), ME, self.now),
            'returned',
        )

    def test_in_progress_without_deadline_waits_for_nobody(self):
        self.assertIsNone(cli.task_action_need(task(status='in_progress'), ME, self.now))

    def test_review_goes_to_requester_not_creator(self):
        # Приёмку закрывает поручитель, а постановщик — только если поручителя нет.
        handed_over = task(status='completed', assignee={'id': OTHER},
                           creator={'id': OTHER}, requested_by={'id': ME})
        self.assertEqual(cli.task_action_need(handed_over, ME, self.now), 'review')
        by_creator = task(status='completed', assignee={'id': OTHER}, creator={'id': ME})
        self.assertEqual(cli.task_action_need(by_creator, ME, self.now), 'review')

    def test_review_not_for_bystander(self):
        alien = task(status='completed', assignee={'id': OTHER}, creator={'id': OTHER})
        self.assertIsNone(cli.task_action_need(alien, ME, self.now))

    def test_accepted_tells_assignee(self):
        self.assertEqual(cli.task_action_need(task(status='accepted'), ME, self.now), 'accepted')

    def test_self_accepted_says_nothing(self):
        # Себе принял — сообщать человеку о его же клике незачем.
        mine = task(status='accepted', creator={'id': ME})
        self.assertIsNone(cli.task_action_need(mine, ME, self.now))

    def test_backlog_never_waits(self):
        self.assertIsNone(cli.task_action_need(task(is_backlog=True, due_at=self.past), ME, self.now))

    def test_accepted_ignores_backlog_flag(self):
        # Принятая задача из работы вышла, но исполнителю о приёмке сказать надо.
        self.assertEqual(
            cli.task_action_need(task(status='accepted', is_backlog=True), ME, self.now),
            'accepted',
        )

    def test_not_my_task(self):
        self.assertIsNone(cli.task_action_need(task(assignee={'id': OTHER}), ME, self.now))

    def test_no_user_no_verdict(self):
        self.assertIsNone(cli.task_action_need(task(), 0, self.now))


class ActionSeenTests(unittest.TestCase):
    def test_seen_only_counts_for_its_own_kind(self):
        item = task(action_seen={'kind': 'overdue', 'seen_at': '2026-08-18T11:00:00'})
        self.assertTrue(cli.is_action_need_seen(item, 'overdue'))
        self.assertFalse(cli.is_action_need_seen(item, 'fresh'))

    def test_touching_the_task_burns_the_mark(self):
        item = task(action_seen={'kind': 'fresh', 'seen_at': '2026-08-18T09:00:00'},
                    updated_at='2026-08-18T10:00:00')
        self.assertFalse(cli.is_action_need_seen(item, 'fresh'))

    def test_terminal_mark_never_burns(self):
        # Принятую задачу правка отчёта не должна воскрешать.
        item = task(status='accepted', action_seen={'kind': 'accepted', 'seen_at': '2026-01-01T00:00:00'},
                    updated_at='2026-08-18T10:00:00')
        self.assertTrue(cli.is_action_need_seen(item, 'accepted'))

    def test_no_mark_at_all(self):
        self.assertFalse(cli.is_action_need_seen(task(), 'fresh'))


class ActionBucketTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 18, 12, 0)

    def test_seen_terminal_disappears_but_others_stay_marked(self):
        seen_accepted = task(id=1, status='accepted',
                             action_seen={'kind': 'accepted', 'seen_at': '2026-01-01T00:00:00'})
        seen_fresh = task(id=2, action_seen={'kind': 'fresh', 'seen_at': '2026-08-18T11:00:00'},
                          updated_at='2026-08-18T10:00:00')
        buckets = cli.action_needs_by_kind([seen_accepted, seen_fresh], ME, self.now)
        self.assertEqual(buckets['accepted'], [])
        self.assertEqual([(item['id'], seen) for item, seen in buckets['fresh']], [(2, True)])

    def test_sorted_by_deadline_soonest_first(self):
        late = task(id=1, due_at=(self.now + timedelta(days=3)).isoformat())
        soon = task(id=2, due_at=(self.now + timedelta(hours=2)).isoformat())
        undated = task(id=3)
        buckets = cli.action_needs_by_kind([late, undated, soon], ME, self.now)
        self.assertEqual([item['id'] for item, _ in buckets['fresh']], [2, 1, 3])

    def test_every_kind_has_a_bucket(self):
        buckets = cli.action_needs_by_kind([], ME, self.now)
        self.assertEqual(set(buckets), set(cli.ACTION_KIND_LABELS))


# ─────────────── Прочие мелочи вывода ───────────────

class FormattingTests(unittest.TestCase):
    def test_attachments_merge_initial_then_result(self):
        merged = cli.all_attachments({
            'attachments': [{'id': 1}, {'id': 2}],
            'completion_attachments': [{'id': 3}],
        })
        self.assertEqual([item['id'] for item in merged], [1, 2, 3])

    def test_attachments_tolerate_missing_keys(self):
        self.assertEqual(cli.all_attachments({}), [])

    def test_bytes_are_human(self):
        self.assertEqual(cli.format_bytes(0), '0 Б')
        self.assertEqual(cli.format_bytes(512), '512 Б')
        self.assertEqual(cli.format_bytes(13068), '13 КБ')
        self.assertEqual(cli.format_bytes(5 * 1024 * 1024), '5.0 МБ')

    def test_recurrence_label_only_for_regulations(self):
        self.assertIsNone(cli._recurrence_label({'is_regulation': False}))
        label = cli._recurrence_label({
            'is_regulation': True, 'recurrence_type': 'weekly', 'recurrence_interval': 2,
            'regulation_iteration': 3, 'regulation_parent_id': 99,
        })
        self.assertIn('раз в 2 нед', label)
        self.assertIn('итерация 3', label)
        self.assertIn('шаблон #99', label)

    def test_reminder_over_a_day_refused(self):
        # Предел суток стоит и в CHECK-констрейнте, и в UI — CLI не должен врать.
        with self.assertRaises(SystemExit):
            cli.parse_reminder_argument('2d')
        self.assertEqual(cli.parse_reminder_argument('за день'), 1440)
        self.assertEqual(cli.parse_reminder_argument('off'), 0)


# ─────────────── Сторожа расхождения с сервером ───────────────

class ServerContractTests(unittest.TestCase):
    """Константы CLI обязаны совпадать с сервером: разъехавшись, они дают 400 без причины."""

    @classmethod
    def setUpClass(cls):
        cls.bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8-sig')
        cls.needs_js = (ROOT / 'src' / 'components' / 'tasks' / 'taskActionNeeds.js').read_text(encoding='utf-8-sig')

    def test_upload_limits_match_server(self):
        files = int(re.search(r'^TASK_MAX_FILES\s*=\s*(\d+)', self.bot, re.M).group(1))
        self.assertEqual(cli.MAX_UPLOAD_FILES, files)
        size = re.search(r'^TASK_MAX_FILE_SIZE_BYTES\s*=\s*(\d+)\s*\*\s*(\d+)\s*\*\s*(\d+)', self.bot, re.M)
        expected = int(size.group(1)) * int(size.group(2)) * int(size.group(3))
        self.assertEqual(cli.MAX_UPLOAD_BYTES, expected)

    def test_actions_accepting_files_match_server(self):
        raw = re.search(r"actions_with_files\s*=\s*\{([^}]*)\}", self.bot).group(1)
        server = set(re.findall(r"'([a-z_]+)'", raw))
        self.assertEqual(set(cli.STATUS_ACTIONS_WITH_FILES), server)

    def test_action_kinds_match_the_frontend(self):
        raw = re.search(r"ACTION_NEED_KINDS\s*=\s*\[([^\]]*)\]", self.needs_js).group(1)
        frontend = [name for name in re.findall(r"'([a-z]+)'", raw)]
        self.assertEqual(list(cli.ACTION_KIND_LABELS), frontend)

    def test_attachment_kinds_match_the_schema(self):
        database = (ROOT / 'database.py').read_text(encoding='utf-8-sig')
        raw = re.search(r"attachment_kind VARCHAR\(16\).*?CHECK \(attachment_kind IN \(([^)]*)\)\)",
                        database, re.S).group(1)
        kinds = set(re.findall(r"'([a-z]+)'", raw))
        self.assertEqual(kinds, {'initial', 'result'})

    def test_download_route_still_where_the_cli_looks(self):
        self.assertIn("/api/tasks/attachments/<int:attachment_id>/download", self.bot)

    def test_upload_field_name_still_files(self):
        # Поле называется `files` в обоих роутах; переименуют — загрузка станет
        # тихо пустой, потому что getlist на отсутствующем ключе вернёт [].
        self.assertGreaterEqual(len(re.findall(r"request\.files\.getlist\('files'\)", self.bot)), 2)

    def test_required_checklist_still_blocks_handover(self):
        # SKILL.md обещает: незакрытый обязательный пункт не даёт сдать задачу.
        database = (ROOT / 'database.py').read_text(encoding='utf-8-sig')
        self.assertIn('CHECKLIST_INCOMPLETE', database)
        self.assertIn('is_required = TRUE', database)

    def test_export_filter_values_match_server(self):
        database = (ROOT / 'database.py').read_text(encoding='utf-8-sig')
        mine = set(re.findall(r"mine_norm not in \{([^}]*)\}", database)[0].replace("'", '').split(', '))
        self.assertEqual(mine | {'any'}, {'any', 'assignee', 'creator'})
        scope = re.findall(r"person_scope_norm not in \{([^}]*)\}", database)[0]
        self.assertEqual(set(re.findall(r"'(\w+)'", scope)), {'incoming', 'outgoing', 'any'})

    def test_task_id_filter_still_supported(self):
        # cmd_show ходит точечным запросом; без этого фильтра он снова начнёт
        # тянуть весь список и промахиваться на задачах за пределами limit.
        self.assertIn("request.args.get('task_id')", self.bot)


if __name__ == '__main__':
    unittest.main(verbosity=2)
