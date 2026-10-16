# -*- coding: utf-8 -*-
"""Раздел «Продакшн» (задача #316): материалы задач продакшна.

Модули task_production.materials и task_production.storage — листья без Flask и
базы, их проверяем целиком. Маршруты (task_production.routes) — настоящим
Flask с подменёнными запросами к базе и хранилищем: видно, какие проверки
стоят перед облаком и что уходит в него. Что ЗНАЧИТ SQL раздела — в
tests/test_task_production_materials_postgres.py на настоящем Postgres.
Проводку в портал (меню, гарды, профиль, удаление задачи) сверяем по исходникам:
монолит и App.jsx тесты не импортируют.
"""

import base64
import json
import re
import struct
import sys
import types
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_production import materials, storage  # noqa: E402

META_JS = ROOT / 'src' / 'components' / 'production' / 'productionMeta.js'
APP_JSX = ROOT / 'src' / 'App.jsx'
TASKS_VIEW = ROOT / 'src' / 'components' / 'tasks' / 'TasksView.jsx'
BOT = ROOT / 'bot_schedule2.py'
DATABASE = ROOT / 'database.py'


def _read(path):
    return path.read_text(encoding='utf-8-sig')


class TypesSyncTests(unittest.TestCase):
    """Выбор файла в браузере и сервер принимают одни и те же типы."""

    def test_js_table_matches_server(self):
        source = _read(META_JS)
        block = source[source.index('/* types:start */'):source.index('/* types:end */')]
        js = {ext: (kind, ctype) for ext, kind, ctype in re.findall(
            r"'?([0-9a-z]+)'?: \['(video|image|file)', '([^']+)'\]", block)}
        self.assertEqual(js, dict(materials.TYPES))

    def test_preview_sets_match(self):
        source = _read(META_JS)
        for name, expected in (('PREVIEW_IMAGE_TYPES', materials.PREVIEW_IMAGE_TYPES),
                               ('PLAYABLE_VIDEO_TYPES', materials.PLAYABLE_VIDEO_TYPES)):
            found = re.search(r'export const %s = new Set\(\[([^\]]+)\]\)' % name, source)
            self.assertIsNotNone(found, name)
            self.assertEqual(set(re.findall(r"'([^']+)'", found.group(1))), set(expected), name)

    def test_library_categories_match_server(self):
        source = _read(META_JS)
        block = source[source.index('/* categories:start */'):source.index('/* categories:end */')]
        js = re.findall(r"\{ value: '(\w+)', label: '([^']+)', kind: '(\w+)'", block)
        self.assertEqual(js, [tuple(row) for row in materials.LIBRARY_CATEGORIES])

    def test_size_limit_matches(self):
        self.assertIn('export const MAX_MATERIAL_BYTES = 2 * 1024 * 1024 * 1024;', _read(META_JS))
        self.assertEqual(materials.MAX_MATERIAL_BYTES, 2 * 1024 ** 3)


class ClassifyTests(unittest.TestCase):

    def test_cyrillic_name_kept(self):
        name, ext, kind, ctype = materials.classify('Промо-ролик «Осень».MP4', 100)
        self.assertEqual(name, 'Промо-ролик «Осень».MP4')
        self.assertEqual((ext, kind, ctype), ('mp4', 'video', 'video/mp4'))

    def test_path_and_control_chars_dropped(self):
        name, *_ = materials.classify('C:\\Users\\x\\Desktop\\мой\x07 файл.png', 10)
        self.assertEqual(name, 'мой файл.png')
        name, *_ = materials.classify('../../etc/обложка.jpg', 10)
        self.assertEqual(name, 'обложка.jpg')

    def test_long_name_keeps_extension(self):
        name, ext, *_ = materials.classify('я' * 400 + '.mov', 10)
        self.assertLessEqual(len(name), materials.MAX_NAME_LENGTH)
        self.assertTrue(name.endswith('.mov'))
        self.assertEqual(ext, 'mov')

    def test_rejections(self):
        cases = [
            (('virus.exe', 10), materials.UNSUPPORTED_TYPE),
            (('без расширения', 10), materials.UNSUPPORTED_TYPE),
            (('ролик.mp4', 0), materials.EMPTY_FILE),
            (('ролик.mp4', 'много'), materials.EMPTY_FILE),
            (('ролик.mp4', materials.MAX_MATERIAL_BYTES + 1), materials.TOO_LARGE),
            (('   ', 10), materials.BAD_NAME),
        ]
        for args, code in cases:
            with self.subTest(args=args):
                with self.assertRaises(ValueError) as caught:
                    materials.classify(*args)
                self.assertEqual(str(caught.exception), code)

    def test_exact_limit_accepted(self):
        materials.classify('ролик.mp4', materials.MAX_MATERIAL_BYTES)

    def test_blob_path_uses_uuid_and_prefix(self):
        mid = uuid.UUID('12345678-1234-5678-1234-567812345678')
        self.assertEqual(materials.blob_path(35, mid, 'mp4'), 'Production/35/%s.mp4' % mid)
        self.assertEqual(materials.blob_path(35, mid, 'mp4', prefix='Production-stand'),
                         'Production-stand/35/%s.mp4' % mid)
        self.assertEqual(materials.poster_path('Production/35/a.mp4'), 'Production/35/a.mp4.poster.jpg')

    def test_library_categories_take_their_kind(self):
        self.assertEqual(materials.classify_library('Монтаж.MOV', 10, 'reels')[2:], ('video', 'video/quicktime', 'reels'))
        self.assertEqual(materials.classify_library('Баннер.PNG', 10, 'banner')[2:], ('image', 'image/png', 'banner'))
        self.assertEqual(materials.classify_library('ролик.mp4', 10, ' AD ')[4], 'ad')
        cases = (
            (('баннер.png', 10, 'reels'), materials.LIBRARY_ONLY_VIDEO),
            (('макет.pdf', 10, 'animation'), materials.LIBRARY_ONLY_VIDEO),
            (('virus.exe', 10, 'ad'), materials.LIBRARY_ONLY_VIDEO),
            (('ролик.mp4', 10, 'banner'), materials.LIBRARY_ONLY_IMAGE),
            # Вид — раньше размера: пустой PDF в «Баннеры» — «это не картинка».
            (('макет.pdf', 0, 'banner'), materials.LIBRARY_ONLY_IMAGE),
            (('ролик.mp4', 0, 'reels'), materials.EMPTY_FILE),
            (('ролик.mp4', materials.MAX_MATERIAL_BYTES + 1, 'reels'), materials.TOO_LARGE),
            (('ролик.mp4', 10, None), materials.LIBRARY_BAD_CATEGORY),
            (('ролик.mp4', 10, ''), materials.LIBRARY_BAD_CATEGORY),
            (('ролик.mp4', 10, 'Рилсы'), materials.LIBRARY_BAD_CATEGORY),
        )
        for args, code in cases:
            with self.subTest(args=args):
                with self.assertRaises(ValueError) as caught:
                    materials.classify_library(*args)
                self.assertEqual(str(caught.exception), code)
        self.assertTrue(materials.library_kind_fits({'kind': 'video'}, 'reels'))
        self.assertTrue(materials.library_kind_fits({'kind': 'image'}, 'banner'))
        self.assertFalse(materials.library_kind_fits({'kind': 'image'}, 'reels'))
        self.assertFalse(materials.library_kind_fits({'kind': 'video'}, 'banner'))
        self.assertFalse(materials.library_kind_fits({'kind': 'video'}, 'нет такой'))
        texts = {materials.error_message(code) for code in (
            materials.LIBRARY_ONLY_VIDEO, materials.LIBRARY_ONLY_IMAGE, materials.LIBRARY_BAD_CATEGORY)}
        self.assertEqual(len(texts), 3)
        self.assertFalse(any(text.startswith('PRODUCTION_') for text in texts))

    def test_library_paths_and_rights(self):
        mid = uuid.UUID('12345678-1234-5678-1234-567812345678')
        self.assertEqual(materials.library_blob_path(mid, 'mp4'), 'Production/library/%s.mp4' % mid)
        self.assertTrue(materials.is_library({'task_id': None}))
        self.assertFalse(materials.is_library({'task_id': 5}))
        own = {'uploaded_by': 7}
        self.assertTrue(materials.can_delete_library(own, user_id=7, is_super_admin=False, heads_marketing=False))
        self.assertFalse(materials.can_delete_library(own, user_id=8, is_super_admin=False, heads_marketing=False))
        self.assertTrue(materials.can_delete_library(own, user_id=8, is_super_admin=False, heads_marketing=True))
        self.assertTrue(materials.can_delete_library(own, user_id=8, is_super_admin=True, heads_marketing=False))

    def test_preview_rules(self):
        self.assertTrue(materials.can_preview('video', 'video/mp4'))
        self.assertFalse(materials.can_preview('video', 'video/x-matroska'))
        self.assertTrue(materials.can_preview('image', 'image/svg+xml'))
        self.assertFalse(materials.can_preview('image', 'image/heic'))
        self.assertFalse(materials.can_preview('file', 'application/pdf'))


class VersionTests(unittest.TestCase):

    def test_working_version_by_status(self):
        self.assertIsNone(materials.working_version('assigned', 0))
        self.assertEqual(materials.working_version('in_progress', 0), 1)
        self.assertEqual(materials.working_version('returned', 1), 2)
        self.assertIsNone(materials.working_version('completed', 1))
        self.assertIsNone(materials.working_version('accepted', 2))
        # Переоткрыли после приёмки — работа идёт в следующей версии.
        self.assertEqual(materials.working_version('in_progress', 2), 3)

    def test_rounds_from_history(self):
        t = datetime(2026, 10, 16, 12, 0)
        rows = [
            ('assigned', t, 1, 'Глава', None),
            ('in_progress', t, 2, 'Видеограф', None),
            ('completed', t, 2, 'Видеограф', 'первая'),
            ('returned', t, 1, 'Глава', 'логотип крупнее'),
            ('in_progress', t, 2, 'Видеограф', None),
            ('completed', t, 2, 'Видеограф', 'вторая'),
            ('accepted', t, 1, 'Глава', None),
            ('reopened', t, 1, 'Глава', 'ещё вариант'),
            ('returned', t, 1, 'Глава', 'не должно прилипнуть'),
            ('completed', t, 2, 'Видеограф', 'третья'),
        ]
        rounds = materials.rounds_from_history(rows)
        self.assertEqual([r['version'] for r in rounds], [1, 2, 3])
        self.assertEqual((rounds[0]['outcome'], rounds[0]['outcome_comment']), ('returned', 'логотип крупнее'))
        self.assertEqual(rounds[1]['outcome'], 'accepted')
        self.assertEqual(rounds[1]['comment'], 'вторая')
        self.assertIsNone(rounds[2]['outcome'])
        self.assertEqual(materials.rounds_from_history([]), [])

    def test_permissions(self):
        p = materials.permissions(status='assigned', is_assignee=True, can_review=False, is_super_admin=False)
        self.assertTrue(p['can_take'])
        self.assertFalse(p['can_upload'])
        p = materials.permissions(status='returned', is_assignee=True, can_review=False, is_super_admin=False)
        self.assertTrue(p['can_upload'] and p['can_submit'])
        p = materials.permissions(status='completed', is_assignee=False, can_review=True, is_super_admin=False)
        self.assertTrue(p['can_review'])
        self.assertFalse(p['can_upload'])
        p = materials.permissions(status='in_progress', is_assignee=False, can_review=True, is_super_admin=False)
        self.assertFalse(p['can_review'])



    def test_permissions_are_for_assignee_and_review_only_on_review(self):
        for status in ('assigned', 'in_progress', 'returned', 'completed', 'accepted'):
            with self.subTest(status=status):
                stranger = materials.permissions(status=status, is_assignee=False, can_review=False,
                                                 is_super_admin=False)
                self.assertEqual([k for k, v in stranger.items() if v], [])
        accepted = materials.permissions(status='accepted', is_assignee=True, can_review=True, is_super_admin=False)
        self.assertEqual([k for k, v in accepted.items() if v], [])

    def test_can_delete(self):
        working = {'version': 2}
        old = {'version': 1}
        self.assertTrue(materials.can_delete(working, status='returned', completed_count=1, user_id=5,
                                             is_assignee=True, is_super_admin=False))
        self.assertFalse(materials.can_delete(old, status='returned', completed_count=1, user_id=5,
                                              is_assignee=True, is_super_admin=False))
        self.assertFalse(materials.can_delete(working, status='completed', completed_count=2, user_id=5,
                                              is_assignee=True, is_super_admin=False))
        self.assertFalse(materials.can_delete(working, status='returned', completed_count=1, user_id=5,
                                              is_assignee=False, is_super_admin=False))
        self.assertTrue(materials.can_delete(old, status='accepted', completed_count=2, user_id=9,
                                             is_assignee=False, is_super_admin=True))


class IntegrityTests(unittest.TestCase):

    def test_crc32c_parsing(self):
        value = 0xE3069283  # CRC32C строки «123456789» (RFC 3720)
        self.assertEqual(materials.crc32c_from_client(str(value)), value)
        self.assertEqual(materials.crc32c_from_client('0xe3069283'), value)
        self.assertEqual(materials.crc32c_from_client('E3069283'), value)
        self.assertIsNone(materials.crc32c_from_client(''))
        self.assertIsNone(materials.crc32c_from_client('-1'))
        self.assertIsNone(materials.crc32c_from_client(str(2 ** 32)))
        self.assertIsNone(materials.crc32c_from_client('abc'))
        gcs = base64.b64encode(struct.pack('>I', value)).decode()
        self.assertEqual(gcs, '4waSgw==')
        self.assertEqual(materials.crc32c_from_gcs(gcs), value)
        self.assertIsNone(materials.crc32c_from_gcs('не base64'))
        self.assertIsNone(materials.crc32c_from_gcs(base64.b64encode(b'12345').decode()))

    def test_signatures(self):
        good = {
            'mp4': b'\x00\x00\x00\x18ftypisom' + b'\x00' * 8,
            'mov': b'\x00\x00\x00\x14ftypqt  ' + b'\x00' * 8,
            'webm': b'\x1a\x45\xdf\xa3' + b'\x00' * 12,
            'png': b'\x89PNG\r\n\x1a\n' + b'\x00' * 8,
            'jpg': b'\xff\xd8\xff\xe0' + b'\x00' * 12,
            'webp': b'RIFF\x00\x00\x00\x00WEBPVP8 ',
            'avi': b'RIFF\x00\x00\x00\x00AVI LIST',
            'pdf': b'%PDF-1.7\n',
            'ai': b'%PDF-1.5\n',
            'psd': b'8BPS\x00\x01',
            'zip': b'PK\x03\x04' + b'\x00' * 8,
            'svg': b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>',
            'mp3': b'ID3\x03\x00',
            'heic': b'\x00\x00\x00\x18ftypheic' + b'\x00' * 8,
        }
        for ext, head in good.items():
            with self.subTest(ext=ext):
                self.assertTrue(materials.content_matches(ext, head))
        html = b'<html><script>alert(1)</script></html>'
        for ext in ('mp4', 'mov', 'png', 'pdf', 'jpg', 'zip'):
            with self.subTest(fake=ext):
                self.assertFalse(materials.content_matches(ext, html))
        self.assertFalse(materials.content_matches('exe', b'MZ'))
        self.assertFalse(materials.content_matches('mp4', b''))

    def test_every_type_has_a_signature(self):
        self.assertEqual(set(materials.TYPES), set(materials._SIGNATURES))


class _Response:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


class StorageTests(unittest.TestCase):

    def test_session_offset(self):
        cases = [
            (_Response(308, {'Range': 'bytes=0-2097151'}), (2097152, False)),
            (_Response(308), (0, False)),
            (_Response(200), (5000, True)),
            (_Response(201), (5000, True)),
            (_Response(410), None),
            (_Response(404), None),
        ]
        for response, expected in cases:
            with self.subTest(status=response.status_code):
                with mock.patch.object(storage.requests, 'put', return_value=response) as put:
                    self.assertEqual(storage.session_offset('https://s', 5000, origin='https://o'), expected)
                headers = put.call_args.kwargs['headers']
                self.assertEqual(headers['Content-Range'], 'bytes */5000')
                self.assertEqual(headers['Origin'], 'https://o')
        with mock.patch.object(storage.requests, 'put', return_value=_Response(503)):
            with self.assertRaises(storage.StorageUnavailable):
                storage.session_offset('https://s', 10)
        with mock.patch.object(storage.requests, 'put', side_effect=storage.requests.ConnectionError('down')):
            with self.assertRaises(storage.StorageUnavailable):
                storage.session_offset('https://s', 10)

    def test_cancel_swallows_network_errors(self):
        with mock.patch.object(storage.requests, 'delete', side_effect=storage.requests.ConnectionError('x')):
            storage.cancel_session('https://s', origin='https://o')

    def test_open_session_is_bounded(self):
        blob = mock.MagicMock()
        blob.create_resumable_upload_session.return_value = 'https://session'
        client = mock.MagicMock()
        client.bucket.return_value.blob.return_value = blob
        url = storage.open_session(client, 'bucket', 'Production/1/x.mp4', content_type='video/mp4',
                                   size=1234, origin='https://alfa330.github.io')
        self.assertEqual(url, 'https://session')
        kwargs = blob.create_resumable_upload_session.call_args.kwargs
        self.assertEqual(kwargs['size'], 1234)
        self.assertEqual(kwargs['origin'], 'https://alfa330.github.io')
        self.assertEqual(kwargs['if_generation_match'], 0)
        self.assertEqual(kwargs['content_type'], 'video/mp4')

    def test_download_disposition(self):
        value = storage._disposition('download', 'Тест ролик.mp4')
        self.assertIn('attachment; filename="file.mp4"', value)
        self.assertIn("filename*=UTF-8''%D0%A2%D0%B5%D1%81%D1%82%20%D1%80%D0%BE%D0%BB%D0%B8%D0%BA.mp4", value)
        self.assertIn('filename="promo_v2.mov"', storage._disposition('download', 'promo v2.mov'))
        self.assertEqual(storage._disposition('view', 'x.mp4'), 'inline')

    def test_signature_cache(self):
        storage._SIGNED.clear()
        blob = mock.MagicMock()
        blob.generate_signed_url.side_effect = ['https://one', 'https://two']
        client = mock.MagicMock()
        client.bucket.return_value.blob.return_value = blob
        first = storage.sign(client, 'b', 'p/1.jpg', minutes=240)
        again = storage.sign(client, 'b', 'p/1.jpg', minutes=240)
        self.assertEqual(first, again)
        self.assertEqual(blob.generate_signed_url.call_count, 1)
        storage.forget_signatures(['p/1.jpg'])
        self.assertEqual(storage.sign(client, 'b', 'p/1.jpg', minutes=240), 'https://two')

    def test_cloud_errors_from_gcs_calls_become_unavailable(self):
        from google.api_core import exceptions as api_exceptions
        blob = mock.MagicMock()
        blob.create_resumable_upload_session.side_effect = api_exceptions.ServiceUnavailable('busy')
        blob.download_as_bytes.side_effect = api_exceptions.TooManyRequests('slow down')
        blob.upload_from_string.side_effect = api_exceptions.Forbidden('no')
        client = mock.MagicMock()
        client.bucket.return_value.blob.return_value = blob
        client.bucket.return_value.get_blob.side_effect = api_exceptions.InternalServerError('oops')
        calls = [
            lambda: storage.open_session(client, 'b', 'p', content_type='video/mp4', size=1, origin=None),
            lambda: storage.object_head(client, 'b', 'p'),
            lambda: storage.put_small(client, 'b', 'p', b'x', 'image/jpeg'),
            lambda: storage.object_info(client, 'b', 'p'),
        ]
        for call in calls:
            with self.assertRaises(storage.StorageUnavailable):
                call()

    def test_client_errors_become_unavailable(self):
        def broken():
            raise RuntimeError('no credentials')
        with self.assertRaises(storage.StorageUnavailable):
            storage.client_of({'client': broken})
        with self.assertRaises(storage.StorageUnavailable):
            storage.bucket_of({'bucket_name': lambda: ''})


# ── Маршруты: настоящий Flask, подменённые база и хранилище ──────────────────

class _FakeCursor:
    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=None):
        self.last = (sql, params)
        if 'SELECT status FROM tasks WHERE id' in sql:
            task = self.store['tasks'].get(int(params[0]))
            self.row = (task['status'],) if task else None
        else:
            self.row = None

    def fetchone(self):
        return self.row


class _FakeDb:
    def __init__(self, store):
        self.store = store
        self.reviewer = False
        self.open_cursors = 0

    def _get_cursor(self):
        store = self.store
        owner = self

        class _Ctx:
            def __enter__(self):
                owner.open_cursors += 1
                return _FakeCursor(store)

            def __exit__(self, *exc):
                owner.open_cursors -= 1
                return False
        return _Ctx()

    def _task_assignee_scope_tx(self, cursor, task_id):
        return [(p['id'], 'marketing_manager', None) for p in self.store['tasks'][task_id]['assignees']]

    def _task_visible_for_requester(self, role, user_id, created_by, scope, requested_by):
        return True

    def _task_can_review(self, role, user_id, created_by, scope, requested_by):
        return self.reviewer


class RouteTests(unittest.TestCase):

    VIDEO, HEAD, OUTSIDER = 7, 3, 99

    def setUp(self):
        from flask import Flask, g, jsonify
        from task_production import queries, routes

        self.queries = queries
        self.store = {
            'tasks': {
                35: {'id': 35, 'tag': 'production', 'status': 'in_progress', 'completed_count': 0,
                     'created_by': self.HEAD, 'requested_by_id': None, 'subject': 'Промо',
                     'description': '', 'priority': 'normal', 'is_backlog': False, 'due_at': None,
                     'created_at': None, 'updated_at': None, 'started_at': None, 'completed_at': None,
                     'created_by_name': 'Глава', 'requested_by_name': None, 'brief': {'item': 'promo_video'},
                     'assignees': [{'id': self.VIDEO, 'name': 'Видеограф'}]},
            },
            'materials': {},
            'history': {35: []},
            'events': [],
        }
        self.user = {'id': self.VIDEO, 'role': 'marketing_manager', 'marketing': True, 'relation': True}
        self.fake_db = _FakeDb(self.store)
        self.gcs_calls = []
        self.client = mock.MagicMock()
        self.blob_info = {'size': 100, 'crc32c': base64.b64encode(struct.pack('>I', 0xE3069283)).decode(),
                          'content_type': 'video/mp4'}
        self.head_bytes = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 8

        store = self.store

        def material_from(mid):
            return dict(store['materials'][str(mid)]) if str(mid) in store['materials'] else None

        patches = {
            'is_marketing_member': lambda cursor, uid: self.user['marketing'],
            'has_production_relation': lambda cursor, uid: self.user['relation'],
            'task': lambda cursor, task_id: dict(store['tasks'][int(task_id)]) if int(task_id) in store['tasks'] else None,
            'history': lambda cursor, task_id: store['history'].get(int(task_id), []),
            'materials_of': lambda cursor, task_id: [dict(m) for m in store['materials'].values()
                                                     if m['task_id'] is not None and m['task_id'] == task_id],
            'material': lambda cursor, mid, lock=False: material_from(mid),
            'count_in_version': lambda cursor, task_id, version: sum(1 for m in store['materials'].values()
                                                                 if m['task_id'] == task_id and m['version'] == version),
            'pending_for': lambda cursor, **kw: None,
            'insert_material': self._insert,
            'mark_ready': self._mark_ready,
            'delete_material': lambda cursor, mid: store['materials'].pop(str(mid), None),
            'stale_uploads': lambda cursor, **kw: [],
            'library': self._library,
            'library_counts': self._library_counts,
            'set_library_category': self._set_category,
            'add_library_event': self._add_event,
            'library_history': self._history,
            'library_materials': lambda cursor, ids: {
                str(i): dict(store['materials'][str(i)]) for i in ids
                if str(i) in store['materials'] and store['materials'][str(i)]['task_id'] is None
                and store['materials'][str(i)]['status'] == 'ready'},
            'uploading_by_others': lambda cursor, task_id, version, user_id: [
                {'name': m['name'], 'uploaded_by_name': 'Коллега'} for m in store['materials'].values()
                if m['task_id'] == task_id and m['version'] == version and m['status'] == 'uploading'
                and m['uploaded_by'] != user_id],
            'set_poster': lambda cursor, mid, path: store['materials'][str(mid)].update(poster_blob_path=path),
        }
        self._patchers = [mock.patch.object(queries, name, fn) for name, fn in patches.items()]
        storage_patches = {
            'open_session': lambda client, bucket, path, **kw: self.gcs_calls.append(('open', path, kw)) or 'https://session/' + path,
            'object_info': lambda client, bucket, path: dict(self.blob_info),
            'object_head': lambda client, bucket, path, length=4096: self.head_bytes,
            'delete_objects': lambda client, bucket, paths: self.gcs_calls.append(('delete', tuple(p for p in paths if p))),
            'cancel_session': lambda url, origin=None: self.gcs_calls.append(('cancel', url)),
            'sign_view': lambda client, bucket, row: {'poster_url': None, 'view_url': 'https://view'},
            'sign': lambda client, bucket, path, **kw: 'https://signed/' + path,
            'put_small': lambda client, bucket, path, data, ctype: self.gcs_calls.append(('poster', path)),
        }
        self._patchers += [mock.patch.object(storage, name, fn) for name, fn in storage_patches.items()]
        for patcher in self._patchers:
            patcher.start()

        app = Flask(__name__)
        app.testing = True
        self._gcs = {'client': lambda: self.client, 'bucket_name': lambda: 'bucket'}

        def task_guard():
            if self.user['id'] == self.OUTSIDER and not self.user['relation']:
                pass
            g.effective_task_role = self.user['role']
            return self.user['id'], (self.user['id'], None, 'Имя', self.user['role']), None, None

        bp = routes.build_production_blueprint(
            db=self.fake_db,
            require_api_key=lambda fn: fn,
            build_cors_preflight_response=lambda: ('', 204),
            task_guard=task_guard,
            full_task_access=lambda role, uid: role == 'super_admin',
            headed_department_codes=lambda uid: self._headed_codes(uid),
            is_super_admin=lambda role: role == 'super_admin',
            allowed_origin=lambda origin: origin in ('https://alfa330.github.io',),
            gcs=self._gcs,
        )
        app.register_blueprint(bp)
        self.http = app.test_client()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()

    def _headed_codes(self, uid):
        # Сам ходит в базу своим соединением — вызывать его, пока занят курсор, нельзя.
        self.cursors_at_heads = getattr(self, 'cursors_at_heads', []) + [self.fake_db.open_cursors]
        return []

    def _insert(self, cursor, **kw):
        self.store['materials'][str(kw['material_id'])] = {
            'id': str(kw['material_id']), 'task_id': kw['task_id'], 'version': kw['version'], 'status': 'uploading',
            'kind': kw['kind'], 'name': kw['name'], 'extension': kw['extension'], 'content_type': kw['content_type'],
            'size': kw['size'], 'bucket': kw['bucket'], 'blob_path': kw['blob_path'],
            'upload_session': kw['upload_session'], 'upload_origin': kw['upload_origin'],
            'poster_blob_path': None, 'duration_seconds': None, 'width': None, 'height': None,
            'uploaded_by': kw['uploaded_by'], 'uploaded_by_name': 'Видеограф', 'created_at': None, 'ready_at': None,
            'fingerprint': kw.get('fingerprint'), 'library_category': kw.get('library_category'),
        }

    def _shelf(self):
        return [dict(m) for m in self.store['materials'].values() if m['task_id'] is None and m['status'] == 'ready']

    def _library(self, cursor, *, query='', category=None, limit=30, offset=0):
        rows = [m for m in self._shelf() if not category or m['library_category'] == category]
        self.library_calls = getattr(self, 'library_calls', []) + [{'query': query, 'category': category}]
        return rows[offset:offset + limit], len(rows)

    def _library_counts(self, cursor):
        counts = {}
        for m in self._shelf():
            counts[m['library_category']] = counts.get(m['library_category'], 0) + 1
        return counts

    def _set_category(self, cursor, mid, category):
        material = self.store['materials'].get(str(mid))
        if material is None or material['task_id'] is not None:
            return 0
        material['library_category'] = category
        return 1

    def _add_event(self, cursor, *, material, action, actor_id, category, from_category=None):
        self.store['events'].append({
            'id': len(self.store['events']) + 1, 'material_id': str(material['id']), 'action': action,
            'name': material['name'], 'kind': material['kind'], 'size': material['size'], 'category': category,
            'from_category': from_category, 'at': None,
            'actor': {'id': actor_id, 'name': 'Сотрудник %s' % actor_id, 'avatar_bucket': None, 'avatar_blob_path': None},
        })

    def _history(self, cursor, *, query='', category=None, limit=50, offset=0):
        rows = [dict(e) for e in reversed(self.store['events'])
                if not category or category in (e['category'], e['from_category'])]
        return rows[offset:offset + limit], len(rows)

    def _mark_ready(self, cursor, mid, *, version, duration, width, height):
        self.store['materials'][str(mid)].update(status='ready', version=version, duration_seconds=duration,
                                                 width=width, height=height, upload_session=None)

    def _start(self, name='ролик.mp4', size=100, origin='https://alfa330.github.io'):
        headers = {'Origin': origin} if origin else {}
        return self.http.post('/api/production/tasks/35/uploads', json={'name': name, 'size': size}, headers=headers)

    def test_closed_section_for_outsider(self):
        self.user.update(id=self.OUTSIDER, role='trainer', marketing=False, relation=False)
        response = self.http.get('/api/production/board')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'PRODUCTION_CLOSED')

    def test_start_upload_happy_path(self):
        response = self._start()
        self.assertEqual(response.status_code, 201, response.get_json())
        body = response.get_json()
        self.assertEqual(body['offset'], 0)
        kind, path, kwargs = self.gcs_calls[0]
        self.assertEqual(kind, 'open')
        self.assertTrue(path.startswith('Production/35/') and path.endswith('.mp4'))
        self.assertEqual(kwargs['origin'], 'https://alfa330.github.io')
        self.assertEqual(kwargs['size'], 100)
        material = self.store['materials'][body['material_id']]
        self.assertEqual((material['version'], material['status']), (1, 'uploading'))
        self.assertEqual(material['name'], 'ролик.mp4')

    def test_start_upload_rejections(self):
        self.assertEqual(self._start(name='x.exe').status_code, 400)
        self.assertEqual(self._start(size=materials.MAX_MATERIAL_BYTES + 1).status_code, 413)
        self.assertEqual(self._start(origin='https://evil.example').get_json()['code'], 'PRODUCTION_ORIGIN')
        self.store['tasks'][35]['status'] = 'completed'
        self.store['tasks'][35]['completed_count'] = 1
        self.assertEqual(self._start().get_json()['code'], 'PRODUCTION_VERSION_CLOSED')
        self.store['tasks'][35]['status'] = 'in_progress'
        self.store['tasks'][35]['assignees'] = [{'id': 1, 'name': 'Другой'}]
        self.assertEqual(self._start().get_json()['code'], 'PRODUCTION_ONLY_ASSIGNEE')
        self.assertFalse([call for call in self.gcs_calls if call[0] == 'open'])

    def test_complete_verifies_before_ready(self):
        mid = self._start().get_json()['material_id']
        ok = self.http.post('/api/production/uploads/%s/complete' % mid,
                            json={'crc32c': str(0xE3069283), 'duration': 31.5, 'width': 1080, 'height': 1920})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        material = self.store['materials'][mid]
        self.assertEqual((material['status'], material['version'], material['duration_seconds']), ('ready', 1, 31.5))

    def test_complete_rejects_and_cleans(self):
        cases = [
            ('size', {'size': 99}, None, 'повреждён'),
            ('crc', {}, str(0x12345678), 'повреждён'),
            ('format', {}, None, 'Это не MP4'),
        ]
        for label, info_change, crc, text in cases:
            with self.subTest(label):
                self.gcs_calls.clear()
                self.blob_info.update({'size': 100})
                self.blob_info.update(info_change)
                self.head_bytes = b'<html></html>' if label == 'format' else b'\x00\x00\x00\x18ftypisom' + b'\x00' * 8
                mid = self._start().get_json()['material_id']
                response = self.http.post('/api/production/uploads/%s/complete' % mid, json={'crc32c': crc})
                self.assertEqual(response.status_code, 422)
                self.assertIn(text, response.get_json()['error'])
                self.assertNotIn(mid, self.store['materials'])
                self.assertTrue(any(call[0] == 'delete' for call in self.gcs_calls))

    def test_file_finished_after_submit_goes_to_next_version(self):
        mid = self._start().get_json()['material_id']
        # Пока файл догружался, задачу сдали: версия 1 уже на проверке.
        self.store['tasks'][35].update(status='completed', completed_count=1)
        response = self.http.post('/api/production/uploads/%s/complete' % mid, json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store['materials'][mid]['version'], 2)

    def test_only_uploader_sees_own_upload_state(self):
        mid = self._start().get_json()['material_id']
        self.user['id'] = 8
        self.assertEqual(self.http.get('/api/production/uploads/%s' % mid).status_code, 404)
        self.assertEqual(self.http.get('/api/production/uploads/not-a-uuid').status_code, 404)

    def test_delete_rules(self):
        mid = self._start().get_json()['material_id']
        self.http.post('/api/production/uploads/%s/complete' % mid, json={})
        # Версию отправили — удалить её файл исполнитель уже не может.
        self.store['tasks'][35].update(status='completed', completed_count=1)
        response = self.http.delete('/api/production/materials/%s' % mid)
        self.assertEqual(response.status_code, 403)
        self.assertIn(mid, self.store['materials'])
        # Супер-админ может.
        self.user['role'] = 'super_admin'
        response = self.http.delete('/api/production/materials/%s' % mid)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(mid, self.store['materials'])

    def test_delete_uploading_cancels_session(self):
        mid = self._start().get_json()['material_id']
        response = self.http.delete('/api/production/materials/%s' % mid)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(call[0] == 'cancel' for call in self.gcs_calls))

    def test_detail_versions_and_states(self):
        t = datetime(2026, 10, 16, 12, 0)
        first = self._start(name='v1.mp4').get_json()['material_id']
        self.http.post('/api/production/uploads/%s/complete' % first, json={})
        self.store['history'][35] = [
            ('completed', t, self.VIDEO, 'Видеограф', 'сдал'),
            ('returned', t, self.HEAD, 'Глава', 'поправь'),
        ]
        self.store['tasks'][35].update(status='returned', completed_count=1)
        second = self._start(name='v2.mp4').get_json()['material_id']  # ещё грузится
        body = self.http.get('/api/production/tasks/35').get_json()
        states = [(v['version'], v['state']) for v in body['versions']]
        self.assertEqual(states, [(2, 'working'), (1, 'returned')])
        self.assertEqual(body['versions'][1]['outcome_comment'], 'поправь')
        self.assertTrue(body['permissions']['can_upload'])
        # Недогруженный файл видит только тот, кто его грузит.
        self.assertIn(second, [m['id'] for m in body['versions'][0]['materials']])
        self.user['id'] = self.HEAD
        self.user['role'] = 'admin'
        body = self.http.get('/api/production/tasks/35').get_json()
        self.assertNotIn(second, [m['id'] for v in body['versions'] for m in v['materials']])
        self.assertFalse(body['permissions']['can_upload'])

    def test_review_button_follows_task_rules(self):
        self.store['tasks'][35].update(status='completed', completed_count=1)
        self.user.update(id=self.HEAD, role='admin')
        self.fake_db.reviewer = False
        self.assertFalse(self.http.get('/api/production/tasks/35').get_json()['permissions']['can_review'])
        self.fake_db.reviewer = True
        self.assertTrue(self.http.get('/api/production/tasks/35').get_json()['permissions']['can_review'])

    # ── Границы и гонки, которые ловят мутации (разбор #316) ──────────────────

    def _ready(self, name='ролик.mp4'):
        mid = self._start(name=name).get_json()['material_id']
        self.assertEqual(self.http.post('/api/production/uploads/%s/complete' % mid, json={}).status_code, 200)
        return mid

    def test_task_of_other_type_is_not_found(self):
        # Маркетинг видит «все» задачи — но только продакшна; кадровая задача по id не читается.
        self.store['tasks'][36] = dict(self.store['tasks'][35], id=36, tag='task', subject='Кадровая')
        self.store['history'][36] = []
        self.assertEqual(self.http.get('/api/production/tasks/36').status_code, 404)
        response = self.http.post('/api/production/tasks/36/uploads', json={'name': 'x.mp4', 'size': 10},
                                  headers={'Origin': 'https://alfa330.github.io'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse([c for c in self.gcs_calls if c[0] == 'open'])

    def test_outsider_with_own_task_cannot_reach_foreign_task(self):
        mid = self._ready()
        # Постановщик другой задачи продакшна: раздел открыт, а задача 35 — чужая.
        self.user.update(id=50, role='operator', marketing=False, relation=True)
        self.assertEqual(self.http.get('/api/production/tasks/35').status_code, 404)
        self.assertEqual(self.http.get('/api/production/materials/%s/download' % mid).status_code, 404)
        self.assertEqual(self.http.delete('/api/production/materials/%s' % mid).status_code, 404)
        self.assertIn(mid, self.store['materials'])

    def test_assignee_outside_marketing_sees_own_task(self):
        self.user.update(marketing=False, relation=True)  # VIDEO — исполнитель задачи 35
        response = self.http.get('/api/production/tasks/35')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['is_assignee'])

    def test_review_needs_task_visibility(self):
        self.store['tasks'][35].update(status='completed', completed_count=1)
        self.user.update(id=self.HEAD, role='admin')
        self.fake_db.reviewer = True
        self.fake_db._task_visible_for_requester = lambda *args: False
        self.assertFalse(self.http.get('/api/production/tasks/35').get_json()['permissions']['can_review'])

    def test_card_version_on_review_is_submitted_one(self):
        self.store['tasks'][35].update(status='completed', completed_count=1)
        self.assertEqual(self.http.get('/api/production/tasks/35').get_json()['task']['version'], 1)
        self.store['tasks'][35].update(status='accepted', completed_count=2)
        self.assertEqual(self.http.get('/api/production/tasks/35').get_json()['task']['version'], 2)

    def test_foreign_upload_cannot_be_cancelled(self):
        mid = self._start().get_json()['material_id']
        self.gcs_calls.clear()
        self.user['id'] = 8  # другой сотрудник маркетинга
        self.assertEqual(self.http.delete('/api/production/materials/%s' % mid).status_code, 403)
        self.assertIn(mid, self.store['materials'])
        self.assertFalse([c for c in self.gcs_calls if c[0] in ('cancel', 'delete')])

    def test_poster_only_by_uploader(self):
        import io
        mid = self._start().get_json()['material_id']
        self.gcs_calls.clear()
        self.user['id'] = 8
        response = self.http.post('/api/production/uploads/%s/poster' % mid,
                                  data={'file': (io.BytesIO(b'\xff\xd8\xff\xe0' + b'\x00' * 32), 'p.jpg', 'image/jpeg')},
                                  content_type='multipart/form-data')
        self.assertEqual(response.status_code, 404)
        self.assertFalse([c for c in self.gcs_calls if c[0] == 'poster'])
        self.assertIsNone(self.store['materials'][mid]['poster_blob_path'])

    def test_submit_while_session_opens_cancels_upload(self):
        store = self.store

        def open_and_submit(client, bucket, path, **kw):
            store['tasks'][35].update(status='completed', completed_count=1)  # сдали в эту секунду
            return 'https://session/' + path

        with mock.patch.object(storage, 'open_session', open_and_submit):
            response = self._start()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'PRODUCTION_VERSION_CLOSED')
        self.assertEqual(self.store['materials'], {})
        self.assertTrue(any(c[0] == 'cancel' for c in self.gcs_calls))

    def test_bigger_object_is_rejected(self):
        mid = self._start().get_json()['material_id']
        self.blob_info['size'] = 101
        response = self.http.post('/api/production/uploads/%s/complete' % mid, json={})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(mid, self.store['materials'])

    def test_version_file_limit(self):
        with mock.patch.object(self.queries, 'count_in_version',
                               lambda cursor, task_id, version: materials.MAX_MATERIALS_PER_VERSION):
            response = self._start()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'PRODUCTION_TOO_MANY')
        self.assertFalse([c for c in self.gcs_calls if c[0] == 'open'])

    def test_origin_is_stored_and_used_to_resume(self):
        mid = self._start().get_json()['material_id']
        pending = dict(self.store['materials'][mid])
        self.assertEqual(pending['upload_origin'], 'https://alfa330.github.io')
        seen = []

        def offset(url, size, origin=None):
            seen.append((url, size, origin))
            return 8 * 1024 * 1024, False

        self.gcs_calls.clear()
        with mock.patch.object(self.queries, 'pending_for', lambda cursor, **kw: dict(pending)), \
                mock.patch.object(storage, 'session_offset', offset):
            body = self._start().get_json()
        self.assertEqual((body['material_id'], body['offset'], body['resumed']), (mid, 8 * 1024 * 1024, True))
        self.assertEqual(seen, [(pending['upload_session'], 100, 'https://alfa330.github.io')])
        self.assertFalse([c for c in self.gcs_calls if c[0] == 'open'])

    # ── Исправления по разбору (16.10.2026) ──────────────────────────────────

    def test_access_check_holds_no_connection_while_asking_heads(self):
        self.http.get('/api/production/board')
        self.http.get('/api/production/tasks/35')
        self.assertTrue(self.cursors_at_heads)
        self.assertEqual(set(self.cursors_at_heads), {0})

    def test_cloud_errors_become_503(self):
        def broken(*args, **kwargs):
            raise storage.StorageUnavailable('ServiceUnavailable: облако занято')
        with mock.patch.object(storage, 'open_session', broken):
            response = self._start()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'PRODUCTION_STORAGE')
        self.assertEqual(self.store['materials'], {})

    def test_non_object_body_is_a_clear_400(self):
        response = self.http.post('/api/production/tasks/35/uploads', json=['не', 'объект'],
                                  headers={'Origin': 'https://alfa330.github.io'})
        self.assertEqual(response.status_code, 400)
        mid = self._start().get_json()['material_id']
        response = self.http.post('/api/production/uploads/%s/complete' % mid, json=[1, 2])
        self.assertEqual(response.status_code, 200)

    def test_removed_assignee_cannot_finish_upload(self):
        mid = self._start().get_json()['material_id']
        self.store['tasks'][35]['assignees'] = [{'id': 1, 'name': 'Другой'}]
        self.user['marketing'] = True  # задачу видит, но уже не исполнитель
        self.gcs_calls.clear()
        response = self.http.post('/api/production/uploads/%s/complete' % mid, json={})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(mid, self.store['materials'])
        self.assertTrue(any(call[0] == 'delete' for call in self.gcs_calls))

    def test_poster_closed_for_submitted_version(self):
        import io
        mid = self._ready()
        self.store['tasks'][35].update(status='completed', completed_count=1)
        response = self.http.post('/api/production/uploads/%s/poster' % mid,
                                  data={'file': (io.BytesIO(b'\xff\xd8\xff\xe0' + b'\x00' * 32), 'p.jpg', 'image/jpeg')},
                                  content_type='multipart/form-data')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'PRODUCTION_POSTER_CLOSED')

    def test_delete_keeps_row_when_storage_is_down(self):
        mid = self._ready()

        def down():
            raise RuntimeError('no credentials')
        self.http.application.config['TESTING'] = True
        with mock.patch.dict(self._gcs, {'client': down}):
            response = self.http.delete('/api/production/materials/%s' % mid)
        self.assertEqual(response.status_code, 503)
        self.assertIn(mid, self.store['materials'])

    def test_own_draft_can_be_removed_after_acceptance(self):
        mid = self._start().get_json()['material_id']
        # Пока файл догружался, версию 1 сдали и приняли: файл лёг черновиком в версию 2.
        self.store['tasks'][35].update(status='accepted', completed_count=1)
        self.assertEqual(self.http.post('/api/production/uploads/%s/complete' % mid, json={}).status_code, 200)
        self.assertEqual(self.store['materials'][mid]['version'], 2)
        body = self.http.get('/api/production/tasks/35').get_json()
        draft = [v for v in body['versions'] if v['version'] == 2][0]
        self.assertEqual(draft['state'], 'draft')
        self.assertTrue(draft['materials'][0]['can_delete'])
        self.assertEqual(self.http.delete('/api/production/materials/%s' % mid).status_code, 200)

    def test_detail_reports_uploads_of_others(self):
        self._start(name='мой.mp4')
        body = self.http.get('/api/production/tasks/35').get_json()
        self.assertEqual(body['others_uploading'], [])
        self.user['id'] = 8
        self.store['tasks'][35]['assignees'].append({'id': 8, 'name': 'Соисполнитель'})
        body = self.http.get('/api/production/tasks/35').get_json()
        self.assertEqual([item['name'] for item in body['others_uploading']], ['мой.mp4'])

    def test_fingerprint_reaches_resume_and_insert(self):
        seen = []
        with mock.patch.object(self.queries, 'pending_for', lambda cursor, **kw: seen.append(kw) or None):
            response = self.http.post('/api/production/tasks/35/uploads',
                                      json={'name': 'ролик.mp4', 'size': 100, 'fingerprint': '1729000000000'},
                                      headers={'Origin': 'https://alfa330.github.io'})
        self.assertEqual(seen[0]['fingerprint'], '1729000000000')
        material = self.store['materials'][response.get_json()['material_id']]
        self.assertEqual(material['fingerprint'], '1729000000000')



    # ── «Медиатека»: просто загрузить файл, без задачи ───────────────────────

    def _library_start(self, name='ролик.mp4', size=100, category='reels'):
        body = {'name': name, 'size': size}
        if category is not None:
            body['category'] = category
        return self.http.post('/api/production/library/uploads', json=body,
                              headers={'Origin': 'https://alfa330.github.io'})

    def _library_ready(self, name='ролик.mp4', category='reels'):
        # Подпись формата должна совпасть с расширением, иначе «завершить» отклонит файл.
        self.head_bytes = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 16 if name.endswith('.png')
                           else b'\x00\x00\x00\x18ftypisom' + b'\x00' * 8)
        mid = self._library_start(name=name, category=category).get_json()['material_id']
        self.assertEqual(self.http.post('/api/production/uploads/%s/complete' % mid, json={}).status_code, 200)
        return mid

    def test_library_upload_needs_a_fitting_category(self):
        for name, category, code in (
                ('ролик.mp4', None, 'PRODUCTION_LIBRARY_BAD_CATEGORY'),
                ('ролик.mp4', 'всё', 'PRODUCTION_LIBRARY_BAD_CATEGORY'),
                ('баннер.png', 'reels', 'PRODUCTION_LIBRARY_ONLY_VIDEO'),
                ('ролик.mp4', 'banner', 'PRODUCTION_LIBRARY_ONLY_IMAGE')):
            with self.subTest(name=name, category=category):
                response = self._library_start(name=name, category=category)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()['code'], code)
        self.assertEqual([c for c in self.gcs_calls if c[0] == 'open'], [])  # до облака не дошло
        response = self._library_start(name='баннер.png', category='banner')
        self.assertEqual(response.status_code, 201, response.get_json())
        material = self.store['materials'][response.get_json()['material_id']]
        self.assertIsNone(material['task_id'])
        self.assertEqual(material['version'], 1)
        self.assertEqual(material['library_category'], 'banner')
        kind, path, kwargs = [c for c in self.gcs_calls if c[0] == 'open'][-1]
        self.assertTrue(path.startswith('Production/library/') and path.endswith('.png'))
        self.assertEqual(kwargs['origin'], 'https://alfa330.github.io')

    def test_library_complete_and_list(self):
        mid = self._library_ready()
        self.assertEqual(self.store['materials'][mid]['status'], 'ready')
        body = self.http.get('/api/production/library').get_json()
        self.assertEqual([item['id'] for item in body['items']], [mid])
        self.assertTrue(body['items'][0]['can_delete'])  # свой ролик
        self.assertEqual(body['items'][0]['category'], 'reels')
        self.assertNotIn(mid, [m['id'] for v in self.http.get('/api/production/tasks/35').get_json()['versions']
                               for m in v['materials']])

    def test_library_complete_writes_history(self):
        mid = self._library_ready(category='ad')
        self.assertEqual([(e['action'], e['material_id'], e['category'], e['actor']['id']) for e in self.store['events']],
                         [('added', mid, 'ad', self.VIDEO)])
        # Повторное «завершить» (сеть переспросила) второй записи не даёт.
        self.http.post('/api/production/uploads/%s/complete' % mid, json={})
        self.assertEqual(len(self.store['events']), 1)

    def test_library_list_by_category_with_counts(self):
        reels = self._library_ready('рилс.mp4', 'reels')
        ad = self._library_ready('реклама.mp4', 'ad')
        banner = self._library_ready('баннер.png', 'banner')
        body = self.http.get('/api/production/library?category=ad&q=рекл').get_json()
        self.assertEqual([item['id'] for item in body['items']], [ad])
        self.assertEqual(self.library_calls[-1], {'query': 'рекл', 'category': 'ad'})
        self.assertEqual(body['counts'], {'': 3, 'banner': 1, 'animation': 0, 'ad': 1, 'reels': 1})
        self.assertEqual(body['category'], 'ad')
        everything = self.http.get('/api/production/library').get_json()
        self.assertEqual({item['id'] for item in everything['items']}, {reels, ad, banner})
        self.assertIsNone(self.library_calls[-1]['category'])
        # Файл, загруженный до категорий, входит в «Все файлы», но ни в одну плитку.
        legacy = self._library_ready('старый.mp4', 'reels')
        self.store['materials'][legacy]['library_category'] = None
        counts = self.http.get('/api/production/library').get_json()['counts']
        self.assertEqual(counts, {'': 4, 'banner': 1, 'animation': 0, 'ad': 1, 'reels': 1})
        wrong = self.http.get('/api/production/library?category=nope')
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.get_json()['code'], 'PRODUCTION_LIBRARY_BAD_CATEGORY')

    def test_library_move_between_categories(self):
        mid = self._library_ready(category='ad')
        response = self.http.patch('/api/production/library/%s' % mid, json={'category': 'reels'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.store['materials'][mid]['library_category'], 'reels')
        moved = self.store['events'][-1]
        self.assertEqual((moved['action'], moved['from_category'], moved['category'], moved['actor']['id']),
                         ('moved', 'ad', 'reels', self.VIDEO))
        # В ту же категорию — без записи в истории.
        self.assertEqual(self.http.patch('/api/production/library/%s' % mid, json={'category': 'reels'}).status_code, 200)
        self.assertEqual(len(self.store['events']), 2)
        # Ролик в баннеры не переносится.
        wrong = self.http.patch('/api/production/library/%s' % mid, json={'category': 'banner'})
        self.assertEqual((wrong.status_code, wrong.get_json()['code']), (400, 'PRODUCTION_LIBRARY_ONLY_IMAGE'))
        bad = self.http.patch('/api/production/library/%s' % mid, json={'category': None})
        self.assertEqual(bad.get_json()['code'], 'PRODUCTION_LIBRARY_BAD_CATEGORY')
        self.assertEqual(self.store['materials'][mid]['library_category'], 'reels')

    def test_library_move_rights(self):
        mid = self._library_ready(category='ad')
        self.user['id'] = 8  # другой сотрудник маркетинга
        response = self.http.patch('/api/production/library/%s' % mid, json={'category': 'reels'})
        self.assertEqual((response.status_code, response.get_json()['code']), (403, 'PRODUCTION_MOVE_FORBIDDEN'))
        self.assertEqual(self.store['materials'][mid]['library_category'], 'ad')
        with mock.patch.object(self, '_headed_codes', lambda uid: ['marketing']):
            self.assertEqual(self.http.patch('/api/production/library/%s' % mid, json={'category': 'reels'}).status_code, 200)
        # Материал задачи и недогруженный файл через «Медиатеку» не двигаются.
        self.user['id'] = self.VIDEO
        task_mid = self._start().get_json()['material_id']
        self.http.post('/api/production/uploads/%s/complete' % task_mid, json={})
        self.assertEqual(self.http.patch('/api/production/library/%s' % task_mid, json={'category': 'reels'}).status_code, 404)
        pending = self._library_start(category='ad').get_json()['material_id']
        self.assertEqual(self.http.patch('/api/production/library/%s' % pending, json={'category': 'reels'}).status_code, 404)
        self.assertEqual(self.http.patch('/api/production/library/not-a-uuid', json={'category': 'reels'}).status_code, 404)
        # Свой охват — «Медиатека» закрыта целиком.
        self.user.update(id=50, role='admin', marketing=False, relation=True)
        closed = self.http.patch('/api/production/library/%s' % mid, json={'category': 'animation'})
        self.assertEqual(closed.get_json()['code'], 'PRODUCTION_LIBRARY_CLOSED')

    def test_library_delete_and_cancel_in_history(self):
        mid = self._library_ready(category='reels')
        self.assertEqual(self.http.delete('/api/production/materials/%s' % mid).status_code, 200)
        self.assertEqual([(e['action'], e['category']) for e in self.store['events']],
                         [('added', 'reels'), ('deleted', 'reels')])
        # Отменённая недогрузка в истории не видна: файла и не было.
        pending = self._library_start(category='ad').get_json()['material_id']
        self.assertEqual(self.http.delete('/api/production/materials/%s' % pending).status_code, 200)
        self.assertEqual(len(self.store['events']), 2)

    def test_library_history(self):
        kept = self._library_ready('остался.mp4', 'reels')
        gone = self._library_ready('удалён.mp4', 'ad')
        self.http.patch('/api/production/library/%s' % kept, json={'category': 'animation'})
        self.http.delete('/api/production/materials/%s' % gone)
        body = self.http.get('/api/production/library/history').get_json()
        self.assertEqual([e['action'] for e in body['events']], ['deleted', 'moved', 'added', 'added'])
        self.assertEqual(body['total'], 4)
        by_action = {e['action']: e for e in body['events']}
        self.assertEqual(by_action['moved']['material']['id'], kept)  # файл на месте — можно открыть
        self.assertIsNone(by_action['deleted']['material'])  # удалённый — только запись
        self.assertEqual(by_action['deleted']['name'], 'удалён.mp4')
        self.assertEqual(by_action['moved']['actor'], {'id': self.VIDEO, 'name': 'Сотрудник 7', 'avatar_url': None})
        self.assertEqual((by_action['moved']['from_category'], by_action['moved']['category']), ('reels', 'animation'))
        self.assertEqual(body['counts'], {'': 1, 'banner': 0, 'animation': 1, 'ad': 0, 'reels': 0})
        # Плитка «Рилсы» показывает и перенос ИЗ неё.
        reels = self.http.get('/api/production/library/history?category=reels').get_json()
        self.assertEqual([e['action'] for e in reels['events']], ['moved', 'added'])
        self.assertEqual(self.http.get('/api/production/library/history?category=x').status_code, 400)
        self.user.update(id=50, role='admin', marketing=False, relation=True)
        self.assertEqual(self.http.get('/api/production/library/history').get_json()['code'], 'PRODUCTION_LIBRARY_CLOSED')

    def test_library_resume_takes_latest_category(self):
        mid = self._library_start(category='ad').get_json()['material_id']
        pending = dict(self.store['materials'][mid])
        with mock.patch.object(self.queries, 'pending_for', lambda cursor, **kw: dict(pending)), \
                mock.patch.object(storage, 'session_offset', lambda url, size, origin=None: (40, False)):
            response = self._library_start(category='reels')
        self.assertEqual(response.get_json()['material_id'], mid)
        self.assertTrue(response.get_json()['resumed'])
        self.assertEqual(self.store['materials'][mid]['library_category'], 'reels')

    def test_library_closed_for_own_scope(self):
        mid = self._library_ready()
        self.user.update(id=50, role='admin', marketing=False, relation=True)  # постановщик своей задачи
        self.assertEqual(self.http.get('/api/production/library').get_json()['code'], 'PRODUCTION_LIBRARY_CLOSED')
        self.assertEqual(self._library_start().get_json()['code'], 'PRODUCTION_LIBRARY_CLOSED')
        self.assertEqual(self.http.get('/api/production/materials/%s/download' % mid).status_code, 404)
        self.assertEqual(self.http.delete('/api/production/materials/%s' % mid).status_code, 404)
        self.assertIn(mid, self.store['materials'])
        access = self.http.get('/api/production/access').get_json()
        self.assertFalse(access['library'])

    def test_library_delete_rules(self):
        mid = self._library_ready()
        self.user['id'] = 8  # другой сотрудник маркетинга
        self.assertEqual(self.http.delete('/api/production/materials/%s' % mid).status_code, 403)
        self.assertIn(mid, self.store['materials'])
        body = self.http.get('/api/production/library').get_json()
        self.assertFalse(body['items'][0]['can_delete'])
        # Глава маркетинга может.
        with mock.patch.object(self, '_headed_codes', lambda uid: ['marketing']):
            self.assertEqual(self.http.delete('/api/production/materials/%s' % mid).status_code, 200)
        self.assertNotIn(mid, self.store['materials'])

    def test_library_poster_allowed_for_own_video(self):
        import io
        mid = self._library_start().get_json()['material_id']
        response = self.http.post('/api/production/uploads/%s/poster' % mid,
                                  data={'file': (io.BytesIO(b'\xff\xd8\xff\xe0' + b'\x00' * 32), 'p.jpg', 'image/jpeg')},
                                  content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.store['materials'][mid]['poster_blob_path'].endswith('.poster.jpg'))

    def test_download_link_is_attachment(self):
        mid = self._start(name='Промо.mp4').get_json()['material_id']
        self.http.post('/api/production/uploads/%s/complete' % mid, json={})
        body = self.http.get('/api/production/materials/%s/download' % mid).get_json()
        self.assertTrue(body['url'].startswith('https://signed/Production/35/'))


class WiringTests(unittest.TestCase):
    """Раздел подключён к порталу во всех местах, где без этого он молча пропал бы."""

    def _has(self, needle, haystack):
        # Исходник монолита — мегабайты: при промахе печатаем искомое, а не весь файл.
        if needle not in haystack:
            self.fail('не найдено в исходнике: %s' % needle)

    @classmethod
    def setUpClass(cls):
        cls.app = _read(APP_JSX)
        cls.bot = _read(BOT)
        cls.database = _read(DATABASE)
        cls.tasks = _read(TASKS_VIEW)

    def test_menu_predicate_is_server_flag(self):
        self._has("const canAccessProductionForUser = (userLike) => userLike?.production_access === true;", self.app)
        self._has("canAccessProductionForUser(user) && personalViewsAllow(user, 'production')", self.app)

    def test_guards_let_the_view_through(self):
        # Восстановление по адресу и гард карты разделов отдела: у «Маркетинга» есть allowlist.
        self._has("(requestedViewFromUrl !== 'production' || canAccessProductionSection) &&", self.app)
        self._has("if (view === 'production' && canAccessProductionSection) return;", self.app)
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'production')"), 1)
        self._has('{( view === "production" && canAccessProductionSection && (', self.app)

    def test_tasks_drawer_links_to_production(self):
        self.assertEqual(self.app.count('onOpenProduction={canAccessProductionSection ? openProductionFromTasks : null}'), 3)
        self._has('onOpenProduction={onOpenProduction}', self.tasks)
        self._has('prev.onOpenProduction === next.onOpenProduction', self.tasks)

    def test_server_profile_and_blueprint(self):
        self._has('"production_access": production_access,', self.bot)
        self._has('production_access = _production_section_open_for(\n        user_id, role, department_code=department_code, headed_codes=headed_department_codes)', self.bot)
        self._has('app.register_blueprint(build_production_blueprint(', self.bot)
        self._has("task_guard=_task_route_guard,", self.bot)

    def test_task_delete_cleans_storage(self):
        self._has("production_blobs = task_production_queries.blobs_of_task(cursor, task_id)", self.database)
        self._has('"production_blobs": production_blobs', self.database)
        self._has("_delete_production_material_blobs(delete_result.get('production_blobs') or [])", self.bot)

    def test_schema_runs_at_start(self):
        self._has('for statement in task_production_schema.STATEMENTS:', self.database)

    def test_status_transition_locks_the_task_row(self):
        # Две одновременные сдачи без замка писали два 'completed' — версии съезжали.
        start = self.database.index('    def update_task_status(self, task_id, requester_id, requester_role, action')
        body = self.database[start:self.database.index('\n    def ', start + 10)]
        select = body[body.index('SELECT'):body.index('row = cursor.fetchone()')]
        self.assertIn('FOR UPDATE', select)

    def test_task_delete_reads_blobs_before_cascade(self):
        # Строки материалов уходят каскадом вместе с задачей: прочитать пути надо ДО DELETE.
        start = self.database.index('    def delete_task(self, task_id, requester_id, requester_role):')
        end = self.database.index('\n    def ', start + 10)
        body = self.database[start:end]
        self.assertLess(body.index('task_production_queries.blobs_of_task(cursor, task_id)'),
                        body.index('DELETE FROM tasks'))

    def test_menu_item_is_behind_the_predicate(self):
        at = self.app.index("onClick={(e) => handleSidebarViewNavigation(e, 'production')}")
        window = self.app[max(0, at - 400):at]
        self.assertIn('{canAccessProductionSection && (', window)

    def test_profile_flag_follows_tasks_access(self):
        """_production_section_open_for исполняется по-настоящему, имена сверены с модулем."""
        import ast
        tree = ast.parse(self.bot)
        top = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('_production_section_open_for', '_production_full_scope')}
        self.assertEqual(set(funcs), {'_production_section_open_for', '_production_full_scope'})
        called = {n.func.id for f in funcs.values() for n in ast.walk(f)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        stubs = {'_normalize_user_role', '_can_access_tasks', '_is_global_admin_requester',
                 '_has_full_task_access', '_headed_department_codes'}
        self.assertTrue(stubs <= top, stubs - top)
        self.assertTrue(called - {'bool', 'int', 'str'} <= stubs | set(funcs), called)

        state = {'tasks': True, 'full': False, 'heads': [], 'member': False, 'relation': False}

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        import task_production.queries as production_queries
        namespace = {
            'logging': mock.MagicMock(),
            '_normalize_user_role': lambda role: role,
            '_can_access_tasks': lambda role, uid: state['tasks'],
            '_is_global_admin_requester': lambda role, uid: False,
            '_has_full_task_access': lambda role, uid: state['full'],
            '_headed_department_codes': lambda uid: state['heads'],
            'db': types.SimpleNamespace(_get_cursor=lambda: _Cur()),
        }
        module = ast.Module(body=list(funcs.values()), type_ignores=[])
        exec(compile(module, str(BOT), 'exec'), namespace)
        open_for = namespace['_production_section_open_for']
        with mock.patch.object(production_queries, 'is_marketing_member', lambda c, u: state['member']), \
                mock.patch.object(production_queries, 'has_production_relation', lambda c, u: state['relation']):
            self.assertFalse(open_for(None, 'admin'))
            self.assertFalse(open_for(5, 'operator'))
            state['relation'] = True
            self.assertTrue(open_for(5, 'operator'))
            state.update(relation=False, member=True)
            self.assertTrue(open_for(5, 'marketing_manager'))
            state.update(member=False, heads=['marketing'])
            self.assertTrue(open_for(5, 'admin'))
            # Нет «Задач» — нет и раздела, даже админу со всеми задачами.
            state.update(tasks=False, full=True)
            self.assertFalse(open_for(5, 'admin'))
            # Отдел из профиля: маркетингу — сразу да, без базы и без второй проверки.
            state.update(tasks=False, full=False, heads=[], member=False, relation=False)
            self.assertTrue(open_for(5, 'marketing_manager', department_code='marketing', headed_codes=[]))
            self.assertTrue(open_for(5, 'admin', department_code='szov', headed_codes=['MARKETING']))
            # Профиль сказал «не маркетинг» — в базу за этим не ходим, только за своими задачами.
            state.update(tasks=True, member=True)
            self.assertFalse(open_for(5, 'admin', department_code='szov', headed_codes=[]))
            state['relation'] = True
            self.assertTrue(open_for(5, 'admin', department_code='szov', headed_codes=[]))


if __name__ == '__main__':
    unittest.main()
