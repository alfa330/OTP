# -*- coding: utf-8 -*-
"""Приложения к статье: загрузка, привязка, скачивание.

Проверяется то, что нельзя увидеть глазами в интерфейсе:

  * приложение загружается НИЧЕЙНЫМ и хозяина получает при сохранении статьи —
    иначе файл был бы виден читателям раньше текста, к которому его прикладывают;
  * чужой файл нельзя «приложить» к своей статье по одному лишь UUID: это была
    бы дыра, через которую документ закрытого отдела читается в обход прав;
  * «скачать» и «открыть» — разные ответы сервера, потому что заголовок ставит
    подпись GCS, а не наш редирект;
  * исполняемые файлы не прикладываются вовсе.
"""

import io
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
from wiki.routes import build_wiki_blueprint  # noqa: E402

FILE_ID = '11111111-1111-1111-1111-111111111111'
OTHER_ID = '22222222-2222-2222-2222-222222222222'

EDITOR_ROLE = {'id': 2, 'code': 'editor', 'can_read': True, 'can_create': True,
               'can_edit': True, 'can_delete': False, 'can_publish': False,
               'can_approve': False, 'can_manage_users': False,
               'can_manage_structure': False, 'can_manage_access': False}


def make_context(role, wiki_roles=()):
    return {
        'user_id': 42, 'otp_role': role, 'department_id': None, 'direction_id': None,
        'headed_department_ids': [], 'group_ids': [], 'wiki_roles': list(wiki_roles),
        'access_mode': 'auto',
    }


class FakeCursor:
    """Курсор, который запоминает запросы. Нужен там, где важен именно SQL."""

    def __init__(self, rowcount=1):
        self.calls = []
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.calls.append((' '.join(str(sql).split()), params))

    def fetchone(self):
        return (FILE_ID,)

    def fetchall(self):
        return []


@unittest.skipIf(Flask is None, 'flask не установлен')
class AttachmentRouteTest(unittest.TestCase):
    """Загрузка приложения и отдача файла — через настоящие роуты."""

    def build(self, context, *, file_record=None):
        cursor = MagicMock()
        cursor.fetchone.return_value = (FILE_ID,)
        cursor.fetchall.return_value = []
        cursor.rowcount = 1

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        self.signed = []

        def signed_url(bucket, blob_path, **kwargs):
            self.signed.append({'bucket': bucket, 'blob_path': blob_path, **kwargs})
            return 'https://storage.example/signed'

        patches = [
            (queries, 'load_access_context', lambda _c, _u: dict(context)),
            (queries, 'log_action', lambda *a, **k: None),
            (wiki_articles, 'register_file', lambda *a, **k: FILE_ID),
        ]
        if file_record is not None:
            patches.append((wiki_articles, 'get_file', lambda _c, _id: dict(file_record)))
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
            gcs={'signed_url': signed_url,
                 'bucket_name': lambda: 'wiki-bucket',
                 'client': lambda: MagicMock()},
            session_id_provider=lambda: None,
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def _post(self, client, name, data=b'PK\x03\x04'):
        return client.post(
            '/api/wiki/attachments',
            data={'file': (io.BytesIO(data), name)},
            content_type='multipart/form-data')

    def test_operator_cannot_attach(self):
        """Право то же, что у загрузки картинки: без can_create — отказ."""
        client = self.build(make_context('operator'))
        response = self._post(client, 'Заявление.docx')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('required'), 'can_create')

    def test_upload_returns_card_and_stays_unowned(self):
        """Ответ описывает файл целиком: редактор рисует строку до сохранения."""
        client = self.build(make_context('sv', [EDITOR_ROLE]))
        response = self._post(client, 'Заявление на отпуск.docx', b'0123456789')
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body['id'], FILE_ID)
        self.assertEqual(body['name'], 'Заявление на отпуск.docx')
        self.assertEqual(body['size'], 10)
        self.assertEqual(body['url'], '/api/wiki/file/%s' % FILE_ID)
        self.assertEqual(body['download_url'], '/api/wiki/file/%s?download=1' % FILE_ID)

    def test_executable_is_refused(self):
        client = self.build(make_context('sv', [EDITOR_ROLE]))
        for name in ('setup.exe', 'Скрипт.BAT', 'lib.dll'):
            response = self._post(client, name)
            self.assertEqual(response.status_code, 400, name)
            self.assertEqual(response.get_json().get('code'), 'WIKI_FILE_FORBIDDEN', name)

    def test_too_big_is_refused_with_reason(self):
        client = self.build(make_context('sv', [EDITOR_ROLE]))
        response = self._post(client, 'скан.pdf', b'x' * (26 * 1024 * 1024))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get('code'), 'WIKI_FILE_TOO_BIG')
        self.assertIn('25 МБ', response.get_json().get('error'))

    def test_empty_file_is_refused(self):
        client = self.build(make_context('sv', [EDITOR_ROLE]))
        response = self._post(client, 'пусто.docx', b'')
        self.assertEqual(response.status_code, 400)

    # ── Отдача файла ─────────────────────────────────────────────────────
    RECORD = {'id': FILE_ID, 'article_id': None, 'bucket': 'wiki-bucket',
              'blob_path': 'wiki/files/2026/08/20/ab_zayavlenie.docx',
              'original_name': 'Заявление на отпуск.docx',
              'content_type': 'application/vnd.openxmlformats-officedocument'
                              '.wordprocessingml.document',
              'uploaded_by': 42}

    def test_open_is_inline(self):
        client = self.build(make_context('sv', [EDITOR_ROLE]), file_record=self.RECORD)
        response = client.get('/api/wiki/file/%s' % FILE_ID)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.signed[-1]['response_disposition'], 'inline')

    def test_download_carries_readable_name(self):
        """Кириллица едет в filename* — иначе браузер сохранит путь блоба."""
        client = self.build(make_context('sv', [EDITOR_ROLE]), file_record=self.RECORD)
        response = client.get('/api/wiki/file/%s?download=1' % FILE_ID)
        self.assertEqual(response.status_code, 302)
        disposition = self.signed[-1]['response_disposition']
        self.assertTrue(disposition.startswith('attachment; '), disposition)
        self.assertIn("filename*=UTF-8''", disposition)
        # ASCII-часть остаётся читаемой заменой, а не путём в бакете.
        self.assertIn('.docx"', disposition)
        self.assertNotIn('wiki/files', disposition)


class FileInTextTest(unittest.TestCase):
    """Файл, вставленный В ТЕКСТ статьи (карточка-ссылка из редактора).

    Держится он на одном классе у <a>, поэтому проверяем ровно то, что может
    его отнять: серверную чистку и привязку файла к статье.
    """

    CARD = ('<p>Заполните <a class="wiki-file wiki-file--doc" '
            'href="/api/wiki/file/%s?download=1" target="_blank" '
            'rel="noreferrer">Заявление.docx · 238 КБ</a> и отдайте СВ.</p>' % FILE_ID)

    def test_class_survives_sanitizer(self):
        """Без класса карточка станет синей строчкой с адресом — и молча."""
        from wiki.sanitize import sanitize_html
        clean = sanitize_html(self.CARD)
        self.assertIn('class="wiki-file wiki-file--doc"', clean)
        self.assertIn('href="/api/wiki/file/%s?download=1"' % FILE_ID, clean)
        self.assertIn('target="_blank"', clean)

    def test_file_in_text_gets_linked_to_the_article(self):
        """Иначе файл виден одному автору: непривязанный доступен только ему."""
        cursor = FakeCursor()
        linked = wiki_edit.link_content_files(cursor, 7, self.CARD)
        self.assertEqual(linked, 1)
        sql, params = cursor.calls[0]
        self.assertIn('UPDATE wiki_files SET article_id', sql)
        self.assertEqual(params[1], [FILE_ID])

    def test_inline_file_is_not_an_attachment(self):
        """Файл из текста не должен дублироваться в списке под статьёй."""
        cursor = FakeCursor()
        wiki_edit.link_content_files(cursor, 7, self.CARD)
        self.assertNotIn('is_attachment', cursor.calls[0][0])


class SetAttachmentsTest(unittest.TestCase):
    """Привязка списка — единственное место, где решается «чей это файл»."""

    def test_missing_ones_are_detached(self):
        cursor = FakeCursor()
        wiki_edit.set_attachments(cursor, 7, [FILE_ID], uploaded_by=42)
        detach_sql, params = cursor.calls[0]
        self.assertIn('article_id = NULL', detach_sql)
        self.assertIn('is_attachment = FALSE', detach_sql)
        self.assertEqual(params, (7, [FILE_ID]))

    def test_order_follows_the_list(self):
        cursor = FakeCursor()
        attached, _detached = wiki_edit.set_attachments(
            cursor, 7, [OTHER_ID, FILE_ID], uploaded_by=42)
        self.assertEqual(attached, 2)
        self.assertEqual([call[1][:3] for call in cursor.calls[1:]],
                         [(7, 0, OTHER_ID), (7, 1, FILE_ID)])

    def test_foreign_file_cannot_be_grabbed(self):
        """Привязка разрешена только своему ничейному файлу или файлу статьи."""
        cursor = FakeCursor()
        wiki_edit.set_attachments(cursor, 7, [FILE_ID], uploaded_by=42)
        attach_sql, params = cursor.calls[1]
        self.assertIn('article_id IS NULL AND uploaded_by = %s', attach_sql)
        self.assertEqual(params, (7, 0, FILE_ID, 7, 42))

    def test_garbage_ids_are_ignored(self):
        """В списке приходит то, что прислал браузер, — мусор до SQL не доходит."""
        cursor = FakeCursor()
        wiki_edit.set_attachments(
            cursor, 7, ['', None, 'DROP TABLE', FILE_ID, FILE_ID.upper()],
            uploaded_by=42)
        self.assertEqual(len(cursor.calls), 2, 'дубликат и мусор не должны привязываться')
        self.assertEqual(cursor.calls[1][1][2], FILE_ID)

    def test_empty_list_detaches_everything(self):
        cursor = FakeCursor()
        attached, _ = wiki_edit.set_attachments(cursor, 7, [], uploaded_by=42)
        self.assertEqual(attached, 0)
        self.assertEqual(cursor.calls[0][1], (7, []))


if __name__ == '__main__':
    unittest.main()
