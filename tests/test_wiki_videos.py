# -*- coding: utf-8 -*-
"""Ролики в вики («загружать ролики как статью», владелец 18.11.2026).

Ролик проходит те же рубежи, что картинка, и на каждом ломается МОЛЧА:

  1. правила файла — одни на сайте (videoUpload.js) и на сервере (videos.py):
     разойдись они, ролик, который пропустил браузер, отказал бы сервер через
     минуту загрузки;
  2. ручки загрузки (routes_videos.py) — сверка размера, CRC32C и подписи
     формата на завершении; недогруженный файл не отдаёт никто;
  3. серверный санитайзер — <video> только с адресом самой вики, без autoplay;
  4. DOMPurify витрины и узел редактора — те же атрибуты, что у санитайзера;
  5. всё, что переписывает тело статьи: Word, черновик новости, правка ИИ.
     Ролик там не должен пропасть без следа.

Половина проверок читает фронт ТЕКСТОМ — так в этом репозитории сторожат
интерфейсные решения (см. шапку tests/test_wiki_blocks.py).
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

from news import article_draft  # noqa: E402
from wiki import articles as wiki_articles  # noqa: E402
from wiki import queries  # noqa: E402
from wiki import routes_articles  # noqa: E402,F401 — блюпринт регистрирует его сам
from wiki import schema as wiki_schema  # noqa: E402
from wiki import storage as wiki_storage  # noqa: E402
from wiki import videos  # noqa: E402
from wiki.ai import revise  # noqa: E402
from wiki.sanitize import sanitize_html, to_plain_text  # noqa: E402
from task_production import storage as production_storage  # noqa: E402

WIKI_SRC = ROOT / 'src' / 'components' / 'wiki'
UPLOAD_JS = (WIKI_SRC / 'videoRules.js').read_text(encoding='utf-8')
NODE_JSX = (WIKI_SRC / 'WikiVideoNode.jsx').read_text(encoding='utf-8')
EDITOR_JSX = (WIKI_SRC / 'WikiEditor.jsx').read_text(encoding='utf-8')
ARTICLE_JSX = (WIKI_SRC / 'WikiArticle.jsx').read_text(encoding='utf-8')
TYPES_JS = (WIKI_SRC / 'articleTypes.js').read_text(encoding='utf-8')
THEME_CSS = (WIKI_SRC / 'wiki-theme.css').read_text(encoding='utf-8')
QUEUE_JS = (ROOT / 'src' / 'components' / 'production' / 'productionUpload.js').read_text(encoding='utf-8')

VIDEO_ID = '0b8f6c2e-1111-4222-8333-944455556666'
POSTER_ID = '0b8f6c2e-1111-4222-8333-944455556667'
VIDEO_HTML = ('<video src="/api/wiki/file/%s" poster="/api/wiki/file/%s" controls="" '
              'preload="metadata" playsinline="" width="1920" height="1080" '
              'data-duration="83.4"></video>' % (VIDEO_ID, POSTER_ID))


def strip_comments(source):
    """Исходник без комментариев: объяснение «здесь стоял poster» не должно
    удовлетворять поиску того, чего в коде уже нет."""
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    source = re.sub(r'(?m)^\s*//.*$', '', source)
    source = re.sub(r'\{/\*.*?\*/\}', '', source, flags=re.S)
    return source


# ── 1. Правила файла ─────────────────────────────────────────────────────────

class VideoRulesTest(unittest.TestCase):

    def test_playable_formats_pass(self):
        for name, mime in (('Как принять заказ.mp4', 'video/mp4'), ('clip.M4V', 'video/mp4'),
                           ('IMG_0042.MOV', 'video/quicktime'), ('screen.webm', 'video/webm')):
            clean, _ext, content_type = videos.classify(name, 10)
            self.assertEqual(content_type, mime, name)
            self.assertEqual(clean, name)

    def test_unplayable_video_gets_its_own_answer(self):
        """MKV браузер не сыграет — человеку нужно «пересохраните в MP4», а не
        «это не ролик»: файл-то ролик."""
        with self.assertRaises(ValueError) as caught:
            videos.classify('camera.mkv', 10)
        self.assertEqual(str(caught.exception), videos.TYPE_UNPLAYABLE)
        self.assertIn('MP4', videos.error_message(videos.TYPE_UNPLAYABLE))

    def test_not_a_video(self):
        with self.assertRaises(ValueError) as caught:
            videos.classify('Регламент.pdf', 10)
        self.assertEqual(str(caught.exception), videos.TYPE_REFUSED)

    def test_size_limits(self):
        with self.assertRaises(ValueError) as caught:
            videos.classify('a.mp4', 0)
        self.assertEqual(str(caught.exception), videos.EMPTY)
        with self.assertRaises(ValueError) as caught:
            videos.classify('a.mp4', videos.MAX_VIDEO_BYTES + 1)
        self.assertEqual(str(caught.exception), videos.TOO_LARGE)
        self.assertEqual(videos.classify('a.mp4', videos.MAX_VIDEO_BYTES)[1], 'mp4')

    def test_title_from_file_name(self):
        self.assertEqual(videos.title_from_name('Как_принять  заказ 18.11.mp4'),
                         'Как принять заказ 18.11')
        self.assertEqual(videos.title_from_name('C:\\Users\\x\\Ролик.mov'.replace('\\', '/')), 'Ролик')

    def test_blob_path_by_month_and_stand_prefix(self):
        import datetime
        path = videos.blob_path(VIDEO_ID, 'mp4', today=datetime.date(2026, 11, 18))
        self.assertEqual(path, 'wiki/videos/2026/11/%s.mp4' % VIDEO_ID)
        stand = videos.blob_path(VIDEO_ID, 'mov', today=datetime.date(2026, 1, 2), root='/Wiki-stand/')
        self.assertEqual(stand, 'Wiki-stand/2026/01/%s.mov' % VIDEO_ID)

    def test_numbers_from_the_browser(self):
        self.assertEqual(videos.positive_float('83.4567', 100), 83.457)
        self.assertIsNone(videos.positive_float('nan', 100))
        self.assertIsNone(videos.positive_float(-1, 100))
        self.assertIsNone(videos.positive_float(101, 100))
        self.assertEqual(videos.positive_int('1080.4', 5000), 1080)
        self.assertIsNone(videos.positive_int('широкий', 5000))

    def test_describe_ready_and_uploading(self):
        row = {'id': VIDEO_ID, 'poster_file_id': POSTER_ID, 'original_name': 'Ролик.mp4',
               'content_type': 'video/mp4', 'file_size': 5, 'duration_seconds': 3.5,
               'width': 1280, 'height': 720, 'upload_state': None}
        body = videos.describe(row)
        self.assertEqual(body['url'], '/api/wiki/file/%s' % VIDEO_ID)
        self.assertEqual(body['poster_url'], '/api/wiki/file/%s' % POSTER_ID)
        self.assertEqual(body['status'], 'ready')
        self.assertEqual(body['title'], 'Ролик')
        self.assertEqual(videos.describe(dict(row, upload_state='uploading'))['status'], 'uploading')


class FrontAgreesWithServerTest(unittest.TestCase):

    def test_video_types_are_the_same_list(self):
        block = re.search(r'/\* video-types:start \*/(.*?)/\* video-types:end \*/', UPLOAD_JS, re.S)
        self.assertIsNotNone(block, 'нет отметок списка в videoUpload.js')
        front = dict(re.findall(r"(\w+):\s*'([^']+)'", block.group(1)))
        self.assertEqual(front, videos.VIDEO_TYPES)

    def test_limit_is_the_same(self):
        self.assertIn('2 * 1024 * 1024 * 1024', UPLOAD_JS)
        self.assertEqual(videos.MAX_VIDEO_BYTES, 2 * 1024 * 1024 * 1024)

    def test_article_type_exists_on_both_sides(self):
        self.assertIn('video', wiki_schema.ARTICLE_TYPES)
        self.assertIn("value: 'video'", TYPES_JS)

    def test_every_type_has_a_place_in_the_table_of_contents(self):
        """groupByType раскладывает статьи по GROUP_ORDER и молча теряет тип,
        которого там нет: статьи-ролики исчезли бы из оглавления раздела."""
        order = re.search(r'GROUP_ORDER = \[(.*?)\]', TYPES_JS, re.S).group(1)
        listed = set(re.findall(r"'(\w+)'", order))
        self.assertEqual(listed, set(wiki_schema.ARTICLE_TYPES))

    def test_article_type_check_knows_video(self):
        statement = wiki_schema._article_type_check_statement()
        self.assertIn("'video'", statement)


# ── 3–4. Санитайзер, витрина, узел ──────────────────────────────────────────

class VideoSanitizeTest(unittest.TestCase):

    def test_video_survives_as_is(self):
        self.assertEqual(sanitize_html(VIDEO_HTML), VIDEO_HTML)

    def test_page_cannot_start_the_video_itself(self):
        cleaned = sanitize_html(VIDEO_HTML.replace('<video ', '<video autoplay loop muted onplay="x()" '))
        for word in ('autoplay', 'loop', 'muted', 'onplay'):
            self.assertNotIn(word, cleaned)

    def test_preload_auto_is_turned_into_metadata(self):
        """auto скачивал бы ролик целиком у каждого, кто просто открыл статью."""
        cleaned = sanitize_html(VIDEO_HTML.replace('preload="metadata"', 'preload="auto"'))
        self.assertIn('preload="metadata"', cleaned)

    def test_foreign_source_drops_the_whole_video(self):
        for src in ('https://evil.example/a.mp4', 'data:video/mp4;base64,AAAA',
                    '/api/wiki/file/not-a-uuid', '//evil.example/api/wiki/file/%s' % VIDEO_ID):
            html = '<p>до</p><video src="%s" controls></video><p>после</p>' % src
            self.assertEqual(sanitize_html(html), '<p>до</p><p>после</p>', src)

    def test_foreign_poster_is_dropped_but_video_stays(self):
        cleaned = sanitize_html(VIDEO_HTML.replace('/api/wiki/file/%s' % POSTER_ID,
                                                   'https://tracker.example/p.jpg'))
        self.assertIn('src="/api/wiki/file/%s"' % VIDEO_ID, cleaned)
        self.assertNotIn('tracker', cleaned)

    def test_nothing_runs_inside(self):
        cleaned = sanitize_html('<video src="/api/wiki/file/%s"><source src="https://x">'
                                '<script>alert(1)</script></video>' % VIDEO_ID)
        self.assertNotIn('<script', cleaned)
        self.assertNotIn('<source', cleaned)

    def test_video_cannot_cover_the_portal(self):
        """Класс у ролика идёт тем же фильтром, что у всех тегов (разбор 18.11)."""
        cleaned = sanitize_html(VIDEO_HTML.replace(
            '<video ', '<video class="fixed inset-0 z-[9999] w-full" style="position: fixed" '))
        self.assertIn('class="inset-0 w-full"', cleaned)
        self.assertNotIn('fixed', cleaned)
        self.assertNotIn('z-[', cleaned)

    def test_full_address_of_our_file_becomes_permanent(self):
        """Так тело отдаёт редактор на правку ИИ — адреса раскрыты до домена API.
        Ролик не должен выпасть; хост при этом не сохраняется."""
        absolute = VIDEO_HTML.replace('"/api/wiki/file/', '"https://api.icorehub.kz/api/wiki/file/')
        self.assertEqual(sanitize_html(absolute), VIDEO_HTML)
        foreign = VIDEO_HTML.replace('"/api/wiki/file/', '"https://evil.example/api/wiki/file/')
        self.assertEqual(sanitize_html(foreign), VIDEO_HTML)

    def test_video_adds_nothing_to_search_text(self):
        self.assertEqual(to_plain_text('<p>Описание</p>' + VIDEO_HTML), 'Описание')


class VideoFrontTextTest(unittest.TestCase):

    def test_reader_allows_the_same_attributes(self):
        code = strip_comments(ARTICLE_JSX)
        tags = re.search(r'ADD_TAGS:\s*\[([^\]]*)\]', code).group(1)
        self.assertIn("'video'", tags)
        attrs = re.search(r'ADD_ATTR:\s*\[(.*?)\]', code, re.S).group(1)
        for name in ('poster', 'controls', 'preload', 'playsinline', 'data-duration'):
            self.assertIn("'%s'" % name, attrs, name)

    def test_server_allows_what_the_node_renders(self):
        from wiki.sanitize import ALLOWED_ATTRIBUTES
        rendered = {'src', 'poster', 'width', 'height', 'data-duration', 'controls', 'preload',
                    'playsinline'}
        self.assertTrue(rendered <= ALLOWED_ATTRIBUTES['video'])
        code = strip_comments(NODE_JSX)
        for name in ("'data-duration'", 'controls:', 'preload:', 'playsinline:', 'poster:',
                     'width:', 'height:'):
            self.assertIn(name, code, name)

    def test_node_is_registered_in_the_editor(self):
        code = strip_comments(EDITOR_JSX)
        self.assertRegex(code, r'extensions:\s*\[[\s\S]*?\bWikiVideo(\.configure\(\{ base \}\))?,')

    def test_saving_waits_for_the_upload(self):
        code = strip_comments(EDITOR_JSX)
        self.assertIn('Ролик ещё загружается — ${whenReady}', code)
        self.assertIn("unfinishedVideo('сохраните статью, когда он догрузится')", code)
        # Та же проверка — у правки ИИ и у импорта: они заменяют текст целиком.
        self.assertIn("blocked: unfinishedVideo('дождитесь, пока он догрузится')", code)
        self.assertIn("unfinishedVideo('импортируйте, когда он догрузится')", code)
        self.assertIn('!node.attrs.src', code)
        draft = strip_comments((WIKI_SRC / 'WikiAiDraft.jsx').read_text(encoding='utf-8'))
        self.assertEqual(draft.count('blockedBy('), 3)  # правка, обновление, черновик

    def test_poster_address_is_expanded_for_display(self):
        file_urls = (WIKI_SRC / 'fileUrls.js').read_text(encoding='utf-8')
        self.assertEqual(file_urls.count('(src|href|poster)'), 2)

    def test_reader_frames_every_video(self):
        self.assertIn('prepareVideos(wrapTables(DOMPurify.sanitize(', ARTICLE_JSX)
        self.assertIn('/file/${fileId}/url', ARTICLE_JSX)
        self.assertIn('.wiki-prose video {', THEME_CSS)

    def test_queue_keeps_production_addresses_by_default(self):
        """Очередь общая с «Продакшном»: без своих адресов строка обязана идти
        туда же, куда шла до вики."""
        for path in ('/library/uploads', '/tasks/${item.taskId}/uploads', '/uploads/${id}',
                     '/uploads/${id}/poster', '/uploads/${id}/complete', '/materials/${id}'):
            self.assertIn(path, QUEUE_JS, path)
        self.assertIn('return keys;', QUEUE_JS)


# ── 2. Ручки загрузки ────────────────────────────────────────────────────────

WRITER = {'can_read': True, 'can_create': True, 'can_edit': True}
READER = {'can_read': True}


def _capabilities_setter(capabilities):
    def setter(_cursor, ctx, _subjects):
        ctx['role_capabilities'] = dict(capabilities)
        return dict(capabilities)
    return setter


class _FakeFiles:
    """wiki_files в памяти — через функции videos.py, которыми ходят ручки."""

    def __init__(self):
        self.rows = {}
        self.posters = []
        self.deleted = []

    def install(self, case):
        for name in ('insert_upload', 'upload_row', 'pending_for', 'mark_ready', 'set_poster',
                     'poster_row', 'delete_rows', 'stale_uploads'):
            original = getattr(videos, name)
            setattr(videos, name, getattr(self, name))
            case.addCleanup(setattr, videos, name, original)

    def insert_upload(self, _cursor, *, file_id, bucket, blob_path, name, content_type, size,
                      session_url, origin, uploaded_by, fingerprint=None):
        self.rows[str(file_id)] = {
            'id': str(file_id), 'article_id': None, 'bucket': bucket, 'blob_path': blob_path,
            'original_name': name, 'content_type': content_type, 'file_size': size,
            'width': None, 'height': None, 'uploaded_by': uploaded_by,
            'upload_state': videos.UPLOADING, 'upload_session': session_url,
            'upload_origin': origin, 'client_fingerprint': fingerprint,
            'duration_seconds': None, 'poster_file_id': None,
        }

    def upload_row(self, _cursor, file_id, *, lock=False):
        row = self.rows.get(str(file_id))
        return dict(row) if row else None

    def pending_for(self, _cursor, *, user_id, name, size, fingerprint=None, lock=False):
        self.pending_locked = lock
        for row in self.rows.values():
            if (row['uploaded_by'] == user_id and row['upload_state'] and row['original_name'] == name
                    and row['file_size'] == size and (row['client_fingerprint'] or '') == (fingerprint or '')):
                return dict(row)
        return None

    def mark_ready(self, _cursor, file_id, *, duration, width, height):
        self.rows[str(file_id)].update({'upload_state': None, 'upload_session': None,
                                        'upload_origin': None, 'duration_seconds': duration,
                                        'width': width, 'height': height})

    def set_poster(self, _cursor, file_id, poster_file_id):
        self.rows[str(file_id)]['poster_file_id'] = str(poster_file_id)

    def poster_row(self, _cursor, poster_file_id):
        if not poster_file_id:
            return None
        return {'id': str(poster_file_id), 'bucket': 'otp-files', 'blob_path': 'wiki/files/p.webp',
                'article_id': None}

    def delete_rows(self, _cursor, file_ids):
        for file_id in file_ids or ():
            self.deleted.append(str(file_id))
            self.rows.pop(str(file_id), None)

    def stale_uploads(self, _cursor, *, limit=20):
        return []


class _Storage:
    """Хранилище «Продакшна», которым пользуются ручки вики, — подменённое."""

    def __init__(self):
        self.sessions = []
        self.canceled = []
        self.dropped = []
        self.offset = (0, False)
        self.object = {'size': 2048, 'crc32c': None, 'content_type': 'video/mp4'}
        self.head = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 4

    def install(self, case):
        def open_session(_client, bucket, path, *, content_type, size, origin):
            self.sessions.append({'bucket': bucket, 'path': path, 'content_type': content_type,
                                  'size': size, 'origin': origin})
            return 'https://storage.example/upload?upload_id=%d' % len(self.sessions)

        replacements = {
            'client_of': lambda _gcs: MagicMock(),
            'bucket_of': lambda _gcs: 'otp-files',
            'open_session': open_session,
            'session_offset': lambda _url, _size, origin=None: self.offset,
            'cancel_session': lambda url, origin=None: self.canceled.append(url),
            'object_info': lambda _client, _bucket, _path: self.object,
            'object_head': lambda _client, _bucket, _path, length=4096: self.head,
            'delete_objects': lambda _client, bucket, paths: self.dropped.extend(paths),
        }
        for name, value in replacements.items():
            original = getattr(production_storage, name)
            setattr(production_storage, name, value)
            case.addCleanup(setattr, production_storage, name, original)


@unittest.skipIf(Flask is None, 'flask не установлен')
class VideoRoutesTest(unittest.TestCase):

    ORIGIN = 'https://alfa330.github.io'

    def build(self, capabilities=WRITER, user_id=42):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor
        context = {
            'user_id': user_id, 'otp_role': 'sv', 'department_id': 560, 'direction_id': None,
            'headed_department_ids': [], 'group_ids': [], 'wiki_roles': [], 'access_mode': 'auto',
        }
        for name, value in (('load_access_context', lambda _c, _u: dict(context)),
                            ('load_capabilities', _capabilities_setter(capabilities))):
            original = getattr(queries, name)
            setattr(queries, name, value)
            self.addCleanup(setattr, queries, name, original)

        self.files = _FakeFiles()
        self.files.install(self)
        self.storage = _Storage()
        self.storage.install(self)

        from wiki.routes import build_wiki_blueprint
        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (user_id, None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
            gcs={'bucket_name': lambda: 'otp-files', 'client': MagicMock,
                 'signed_url': lambda *a, **kw: 'https://storage.example/signed'},
            allowed_origin=lambda origin: origin == self.ORIGIN,
        ))
        app.config['TESTING'] = True
        return app.test_client(), cursor

    def start(self, client, **body):
        payload = {'name': 'Как принять заказ.mp4', 'size': 2048, 'fingerprint': '1731900000'}
        payload.update(body)
        return client.post('/api/wiki/videos', json=payload, headers={'Origin': self.ORIGIN})

    def test_reader_cannot_upload(self):
        client, _ = self.build(capabilities=READER)
        response = self.start(client)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.storage.sessions, [])

    def test_refusals_come_before_the_session(self):
        client, _ = self.build()
        response = self.start(client, name='camera.mkv')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], videos.TYPE_UNPLAYABLE)
        response = self.start(client, size=videos.MAX_VIDEO_BYTES + 1)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.storage.sessions, [])

    def test_foreign_site_gets_no_session(self):
        client, _ = self.build()
        response = client.post('/api/wiki/videos', json={'name': 'a.mp4', 'size': 10},
                               headers={'Origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.storage.sessions, [])

    def test_start_opens_a_session_with_the_page_origin(self):
        client, _ = self.build()
        response = self.start(client)
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        # Поля общей очереди (productionUpload.js читает именно их).
        for key in ('material_id', 'session_url', 'offset', 'finished', 'chunk_bytes'):
            self.assertIn(key, body)
        session = self.storage.sessions[0]
        self.assertEqual(session['origin'], self.ORIGIN)
        self.assertEqual(session['content_type'], 'video/mp4')
        self.assertTrue(session['path'].startswith('wiki/videos/'))
        self.assertTrue(session['path'].endswith('%s.mp4' % body['material_id']))
        row = self.files.rows[body['material_id']]
        self.assertEqual(row['upload_state'], videos.UPLOADING)
        self.assertEqual(row['uploaded_by'], 42)

    def test_same_file_resumes_where_it_stopped(self):
        client, _ = self.build()
        first = self.start(client).get_json()
        self.assertTrue(self.files.pending_locked, 'докачка ищет строку без блокировки')
        self.storage.offset = (1024, False)
        again = self.start(client)
        self.assertEqual(again.status_code, 200)
        body = again.get_json()
        self.assertTrue(body['resumed'])
        self.assertEqual(body['offset'], 1024)
        self.assertEqual(body['material_id'], first['material_id'])
        self.assertEqual(len(self.storage.sessions), 1)

    def test_expired_session_starts_over(self):
        client, _ = self.build()
        first = self.start(client).get_json()
        self.storage.offset = None
        again = self.start(client).get_json()
        self.assertNotEqual(again['material_id'], first['material_id'])
        self.assertIn(first['material_id'], self.files.deleted)

    def test_state_of_someone_elses_upload_is_not_found(self):
        client, _ = self.build()
        file_id = self.start(client).get_json()['material_id']
        self.files.rows[file_id]['uploaded_by'] = 7
        self.assertEqual(client.get('/api/wiki/videos/%s' % file_id).status_code, 404)

    def test_state_reports_offset_and_expiry(self):
        client, _ = self.build()
        file_id = self.start(client).get_json()['material_id']
        self.storage.offset = (512, False)
        body = client.get('/api/wiki/videos/%s' % file_id).get_json()
        self.assertEqual((body['offset'], body['finished']), (512, False))
        self.storage.offset = None
        self.assertEqual(client.get('/api/wiki/videos/%s' % file_id).status_code, 410)

    def complete(self, client, file_id, **body):
        payload = {'duration': 83.4, 'width': 1920, 'height': 1080}
        payload.update(body)
        return client.post('/api/wiki/videos/%s/complete' % file_id, json=payload)

    def test_complete_marks_ready_and_describes(self):
        client, _ = self.build()
        file_id = self.start(client).get_json()['material_id']
        response = self.complete(client, file_id)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['url'], '/api/wiki/file/%s' % file_id)
        self.assertEqual((body['width'], body['height'], body['duration']), (1920, 1080, 83.4))
        self.assertIsNone(self.files.rows[file_id]['upload_state'])
        # Повтор «завершить» безвреден: ответ тот же, без второй проверки.
        self.assertEqual(self.complete(client, file_id).get_json()['url'], body['url'])

    def test_not_yet_uploaded(self):
        client, _ = self.build()
        file_id = self.start(client).get_json()['material_id']
        self.storage.object = None
        self.assertEqual(self.complete(client, file_id).status_code, 409)
        self.assertIn(file_id, self.files.rows)

    def test_size_mismatch_is_refused_and_cleaned(self):
        client, _ = self.build()
        file_id = self.start(client).get_json()['material_id']
        self.storage.object = {'size': 100, 'crc32c': None}
        response = self.complete(client, file_id)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(file_id, self.files.rows)
        self.assertTrue(self.storage.dropped)

    def test_checksum_mismatch_is_refused(self):
        client, _ = self.build()
        file_id = self.start(client).get_json()['material_id']
        self.storage.object = {'size': 2048, 'crc32c': 'AAAAAA=='}  # 0
        response = self.complete(client, file_id, crc32c='12345')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()['code'], 'WIKI_VIDEO_CORRUPT')

    def test_renamed_html_is_not_a_video(self):
        """Переименованный .html под .mp4: подпись формата не совпала."""
        client, _ = self.build()
        file_id = self.start(client).get_json()['material_id']
        self.storage.head = b'<!doctype html><script>alert(1)</script>'
        response = self.complete(client, file_id)
        self.assertEqual(response.status_code, 422)
        self.assertIn('MP4', response.get_json()['error'])
        self.assertNotIn(file_id, self.files.rows)

    def test_cancel_only_while_uploading(self):
        client, _ = self.build()
        file_id = self.start(client).get_json()['material_id']
        response = client.delete('/api/wiki/videos/%s' % file_id)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(file_id, self.files.rows)
        self.assertEqual(len(self.storage.canceled), 1)

        # Догрузился, но ещё ни в одной статье («Отменить» во время проверки):
        # убирается вместе с объектом.
        other = self.start(client, name='Другой.mp4').get_json()['material_id']
        self.complete(client, other)
        self.assertEqual(client.delete('/api/wiki/videos/%s' % other).status_code, 200)
        self.assertNotIn(other, self.files.rows)

        # Уже стоит в статье — не удаляется.
        linked = self.start(client, name='Третий.mp4').get_json()['material_id']
        self.complete(client, linked)
        self.files.rows[linked]['article_id'] = 575
        self.assertEqual(client.delete('/api/wiki/videos/%s' % linked).status_code, 409)
        self.assertIn(linked, self.files.rows)

    def test_poster_must_be_a_jpeg_and_becomes_a_wiki_picture(self):
        client, _ = self.build()
        file_id = self.start(client).get_json()['material_id']
        bad = client.post('/api/wiki/videos/%s/poster' % file_id,
                          data={'file': (io.BytesIO(b'<svg onload=x>'), 'poster.jpg')},
                          content_type='multipart/form-data')
        self.assertEqual(bad.status_code, 400)

        stored = {}

        def store_file(_cursor, _gcs, **kwargs):
            stored.update(kwargs)
            return POSTER_ID, '/api/wiki/file/%s' % POSTER_ID

        original = wiki_storage.store_file
        wiki_storage.store_file = store_file
        self.addCleanup(setattr, wiki_storage, 'store_file', original)
        good = client.post('/api/wiki/videos/%s/poster' % file_id,
                           data={'file': (io.BytesIO(b'\xff\xd8\xff\xe0' + b'0' * 40), 'poster.jpg')},
                           content_type='multipart/form-data')
        self.assertEqual(good.status_code, 201)
        self.assertEqual(stored['content_type'], 'image/jpeg')
        self.assertEqual(stored['uploaded_by'], 42)
        self.assertEqual(self.files.rows[file_id]['poster_file_id'], POSTER_ID)
        body = self.complete(client, file_id).get_json()
        self.assertEqual(body['poster_url'], '/api/wiki/file/%s' % POSTER_ID)


@unittest.skipIf(Flask is None, 'flask не установлен')
class FileRouteSkipsUnfinishedTest(unittest.TestCase):
    """Ручка /file/<id> и свежая подпись /file/<id>/url не отдают недогруженное."""

    def build(self, record):
        cursor = MagicMock()
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor
        context = {'user_id': 42, 'otp_role': 'sv', 'department_id': 560, 'direction_id': None,
                   'headed_department_ids': [], 'group_ids': [], 'wiki_roles': [],
                   'access_mode': 'auto'}
        for module, name, value in (
                (queries, 'load_access_context', lambda _c, _u: dict(context)),
                (queries, 'load_capabilities', _capabilities_setter(WRITER)),
                (wiki_articles, 'get_file', lambda _c, _id: dict(record))):
            original = getattr(module, name)
            setattr(module, name, value)
            self.addCleanup(setattr, module, name, original)

        from wiki.routes import build_wiki_blueprint
        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (42, None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            gcs={'bucket_name': lambda: 'otp-files', 'client': MagicMock,
                 'signed_url': lambda *a, **kw: 'https://storage.example/signed?x=1'},
        ))
        return app.test_client()

    RECORD = {'id': VIDEO_ID, 'article_id': None, 'bucket': 'otp-files',
              'blob_path': 'wiki/videos/2026/11/%s.mp4' % VIDEO_ID, 'original_name': 'a.mp4',
              'content_type': 'video/mp4', 'uploaded_by': 42}

    def test_uploading_file_is_not_found_even_for_its_owner(self):
        client = self.build(dict(self.RECORD, upload_state='uploading'))
        self.assertEqual(client.get('/api/wiki/file/%s' % VIDEO_ID).status_code, 404)
        self.assertEqual(client.get('/api/wiki/file/%s/url' % VIDEO_ID).status_code, 404)

    def test_ready_file_gets_a_fresh_signature_as_json(self):
        client = self.build(dict(self.RECORD, upload_state=None))
        self.assertEqual(client.get('/api/wiki/file/%s' % VIDEO_ID).status_code, 302)
        response = client.get('/api/wiki/file/%s/url' % VIDEO_ID)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['url'].startswith('https://storage.example/'))

    def test_someone_elses_unlinked_file_is_not_found(self):
        client = self.build(dict(self.RECORD, upload_state=None, uploaded_by=7))
        original = routes_articles.wiki_parks.logo_space_ids
        routes_articles.wiki_parks.logo_space_ids = lambda _c, _id: set()
        self.addCleanup(setattr, routes_articles.wiki_parks, 'logo_space_ids', original)
        self.assertEqual(client.get('/api/wiki/file/%s/url' % VIDEO_ID).status_code, 404)


# ── 5. Всё, что переписывает тело статьи ────────────────────────────────────

class VideoSurvivesRewritesTest(unittest.TestCase):

    BODY = VIDEO_HTML + '<p>Описание ролика с опечаткой.</p>'

    @staticmethod
    def reply(body):
        return ('ПРАВКИ ТАБЛИЦ:\nнет\nИЗМЕНЕНИЯ:\n- правка\nВОПРОСЫ:\nнет\nСТАТЬЯ:\n' + body, {})

    def test_ai_edit_keeps_the_video_byte_for_byte(self):
        seen = {}

        def generate(_system, prompt, max_tokens=None):
            seen['prompt'] = prompt
            return self.reply('<p>[[РОЛИК-1]]</p><p>Описание ролика без опечатки.</p>')

        result = revise.edit_by_instruction(current_title='Ролик', current_html=self.BODY,
                                            instruction='исправь опечатку', generate_fn=generate)
        self.assertIn('[[РОЛИК-1]]', seen['prompt'])
        self.assertNotIn('/api/wiki/file/', seen['prompt'])
        self.assertTrue(result['content'].startswith('<video '))
        self.assertIn('src="/api/wiki/file/%s"' % VIDEO_ID, result['content'])
        self.assertIn('poster="/api/wiki/file/%s"' % POSTER_ID, result['content'])
        self.assertIn('data-duration="83.4"', result['content'])
        self.assertFalse([w for w in result['warnings'] if 'ролик' in w.lower()])

    def test_ai_edit_from_the_editor_keeps_the_video(self):
        """Редактор шлёт тело с раскрытыми адресами — ролик всё равно на месте
        (разбор 18.11: санитайзер выкидывал его из результата молча)."""
        absolute = self.BODY.replace('"/api/wiki/file/', '"https://api.icorehub.kz/api/wiki/file/')
        result = revise.edit_by_instruction(
            current_title='Ролик', current_html=absolute, instruction='исправь опечатку',
            generate_fn=lambda *_a, **_k: self.reply('<p>[[РОЛИК-1]]</p><p>Описание.</p>'))
        content = result['content']
        self.assertTrue(content.startswith('<video '))
        self.assertIn('src="/api/wiki/file/%s"' % VIDEO_ID, content)
        self.assertIn('poster="/api/wiki/file/%s"' % POSTER_ID, content)
        self.assertIn('data-duration="83.4"', content)
        self.assertNotIn('icorehub', content)

    def test_markers_do_not_leak_into_the_change_list(self):
        self.assertEqual(revise.humanize('перенёс [[РОЛИК-1]] выше [[КАРТИНКА-2]]'),
                         'перенёс ролик 1 выше картинка 2')

    def test_lost_marker_puts_the_video_at_the_end_with_a_warning(self):
        result = revise.edit_by_instruction(
            current_title='Ролик', current_html=self.BODY, instruction='сократи',
            generate_fn=lambda *_a, **_k: self.reply('<p>Коротко.</p>'))
        self.assertIn('<video', result['content'])
        self.assertTrue(result['content'].endswith('</video>'))
        self.assertTrue(any('потерял ролик' in w for w in result['warnings']))

    def test_explicit_removal_is_respected_and_said(self):
        result = revise.edit_by_instruction(
            current_title='Ролик', current_html=self.BODY, instruction='убери ролик',
            generate_fn=lambda *_a, **_k: self.reply('<p>[[РОЛИК-1 убрать]]</p><p>Текст.</p>'))
        self.assertNotIn('<video', result['content'])
        self.assertTrue(any('убрал ролик' in w for w in result['warnings']))

    def test_news_gets_the_poster_and_a_line_instead_of_the_video(self):
        draft = article_draft.draft({'title': 'Ролик', 'content': '<p>Вводка</p>' + VIDEO_HTML})
        self.assertEqual(draft['images'], ['/api/wiki/file/%s' % POSTER_ID])
        self.assertIn('▶ Ролик · 1:23 — смотрите в статье вики', draft['body'])
        self.assertNotIn('<video', draft['body'])

    def test_word_gets_the_poster_and_a_caption(self):
        try:
            from wiki import docx_export
        except ImportError:  # pragma: no cover
            self.skipTest('python-docx не установлен')
        import base64
        png = base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM'
            'IQAAAABJRU5ErkJggg==')
        asked = []

        def fetch(file_id):
            asked.append(file_id)
            return png, 'image/png'

        stream, _stats = docx_export.build_document(
            {'title': 'Ролик', 'slug': 'rolik', 'content': self.BODY},
            portal_url='https://portal.example/', fetch_image=fetch)
        from docx import Document
        document = Document(io.BytesIO(stream.getvalue()))
        text = '\n'.join(p.text for p in document.paragraphs)
        self.assertIn('▶ Ролик · 1:23', text)
        self.assertIn('смотреть в вики', text)
        self.assertEqual(asked, [POSTER_ID])

    def test_history_shows_the_video_as_a_line(self):
        from wiki import history
        self.assertEqual(history.html_to_blocks(self.BODY),
                         ['[ролик]', 'Описание ролика с опечаткой.'])
        rows = history.diff_blocks('<p>Описание ролика с опечаткой.</p>', self.BODY)
        self.assertIn('[ролик]', str(rows))

    def test_word_knows_the_video_type_and_never_pins_its_poster(self):
        from bs4 import BeautifulSoup
        from wiki import docx_export
        self.assertEqual(docx_export.TYPE_WORDS.get('video'), 'Ролик')
        builder = docx_export._Builder()
        block = BeautifulSoup('<details><summary>Как</summary>' + VIDEO_HTML
                              + '<p>Текст</p></details>', 'html.parser').details
        self.assertFalse(builder._can_keep_whole(block))

    def test_duration_words(self):
        from wiki.docx_export import duration_words
        self.assertEqual(duration_words('83.4'), '1:23')
        self.assertEqual(duration_words(3725), '1:02:05')
        self.assertEqual(duration_words(None), '')


if __name__ == '__main__':
    unittest.main()
