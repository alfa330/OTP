# -*- coding: utf-8 -*-
"""«Дисциплина»: потолок тела запроса — каркас роутов отказывает ДО разбора формы.

Без базы: настоящий Blueprint, подменены только схема и контекст доступа. Тело
приходит потоком-ловушкой — тест видит, читал ли его сервер вообще. Раньше файл
читался в память целиком (оператор слал объяснительную на сотни МБ) и лишь потом
получал «больше 10 МБ». Те же ручки на настоящем Postgres —
tests/test_discipline_routes_postgres.py.
"""
import io
import re
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from discipline import access, catalog, files, queries, schema

try:
    import flask
except ImportError:  # pragma: no cover
    flask = None

ROUTES = Path(__file__).resolve().parents[1] / 'discipline' / 'routes.py'
MB = catalog.MB
OPERATOR, SV, HEAD = 400, 300, 100
ROLES = {OPERATOR: ('operator', ()), SV: ('sv', ()), HEAD: ('sv', (1,))}
UPLOAD_ROUTES = (
    ('/records', 'evidence', SV),
    ('/records/1/evidence', 'evidence', SV),
    ('/records/1/explanation', 'explanation', OPERATOR),
    ('/records/1/refusal', 'refusal_act', SV),
    ('/acts/1/file', 'act', SV),
    ('/settings/template', 'act_template', HEAD),
)


class _Tripped(Exception):
    pass


class _Tripwire(io.RawIOBase):
    """Тело запроса заданной длины: помнит, читали ли его, и первым же чтением
    обрывает запрос. Длину тест-клиент werkzeug меряет перемоткой."""

    def __init__(self, size):
        super().__init__()
        self.size, self.pos, self.touched = size, 0, False

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        self.pos = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence] + offset
        return self.pos

    def readinto(self, _buffer):
        self.touched = True
        raise _Tripped()


def _ctx(_cursor, user_id, _today):
    role, headed = ROLES[user_id]
    return {'user_id': user_id, 'role': role, 'department_id': 1, 'headed_department_ids': list(headed),
            'group_ids': [5], 'section_departments': {'szov': 1, 'op': 2, 'tez': 3}}


class _Store:
    """Бакет в словаре: client().bucket(...).blob(path)."""

    def __init__(self):
        self.blobs = {}

    def bucket(self, _name):
        return self

    def blob(self, path):
        store = self.blobs

        class _Blob:
            def upload_from_string(self, data, content_type=None):
                store[path] = data

            def delete(self):
                store.pop(path, None)

        return _Blob()


@unittest.skipIf(flask is None, 'needs flask')
class RequestSizeCapTests(unittest.TestCase):
    def setUp(self):
        from discipline.routes import build_discipline_blueprint

        for patch in (mock.patch.object(access, 'SUPER_ADMIN_ONLY', False),
                      mock.patch.object(schema, 'schema_is_ready', lambda _cursor: True),
                      mock.patch.object(queries, 'load_access_context', _ctx)):
            patch.start()
            self.addCleanup(patch.stop)

        class _Db:
            @contextmanager
            def _get_cursor(self):
                yield None

        def resolve_requester():
            return int(flask.request.headers['X-User-Id']), None, None

        self.store = _Store()
        app = flask.Flask(__name__)
        app.register_blueprint(build_discipline_blueprint(
            db=_Db(), require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=resolve_requester,
            gcs={'bucket_name': lambda: 'bucket', 'client': lambda: self.store}))
        self.client = app.test_client()

    def post(self, path, user, length, content_type='multipart/form-data; boundary=x', method='post'):
        stream = _Tripwire(length)
        response = getattr(self.client, method)('/api/discipline' + path, input_stream=stream,
                                                content_type=content_type, headers={'X-User-Id': str(user)})
        return response, stream

    def assertRefusedUnread(self, response, stream, text=None):
        self.assertEqual(response.status_code, 413, response.get_json())
        self.assertEqual(response.get_json()['code'], 'DISCIPLINE_REQUEST_TOO_LARGE')
        self.assertFalse(stream.touched, 'тело читалось до отказа')
        if text:
            self.assertEqual(response.get_json()['error'], text)

    def assertLetThrough(self, response, stream):
        # Пропущенный запрос доходит до разбора тела: ловушка обрывает его (500).
        self.assertNotEqual(response.status_code, 413)
        self.assertTrue(stream.touched)

    def test_operator_explanation_of_a_gigabyte_is_not_read(self):
        self.assertRefusedUnread(*self.post('/records/1/explanation', OPERATOR, 1024 * MB), 'Файл больше 10 МБ')

    def test_each_upload_route_stops_right_above_its_cap(self):
        for path, kind, user in UPLOAD_ROUTES:
            limit = catalog.request_max_bytes(kind)
            with self.subTest(path=path):
                self.assertRefusedUnread(*self.post(path, user, limit + 1), catalog.request_too_large_text(kind))
                with self.assertLogs(level='ERROR'):
                    self.assertLetThrough(*self.post(path, user, limit))

    def test_operator_cannot_push_evidence_sized_bodies(self):
        # Оператор присылает только объяснительную: на роутах с другими файлами его
        # потолок — как у JSON, а не 251 МБ «доказательств», которые потом дали бы 403.
        for path, kind, _user in UPLOAD_ROUTES:
            if kind in ('explanation', 'act_template'):
                continue
            with self.subTest(path=path):
                self.assertRefusedUnread(*self.post(path, OPERATOR, 2 * MB), 'Запрос слишком большой')
        with self.assertLogs(level='ERROR'):
            self.assertLetThrough(*self.post('/records/1/explanation', OPERATOR, 5 * MB))

    def test_json_routes_keep_a_small_cap(self):
        for path, user in (('/records/1/comment', SV), ('/records/1/cancel', SV), ('/controls', HEAD),
                           ('/settings', HEAD)):
            with self.subTest(path=path):
                # JSON раздела — несколько КБ; 2 МБ — уже не честный запрос.
                self.assertRefusedUnread(*self.post(path, user, 2 * MB, 'application/json',
                                                    method='put' if path == '/settings' else 'post'),
                                         'Запрос слишком большой')

    def test_file_is_read_no_further_than_its_kind_allows(self):
        limit = catalog.FILE_KINDS['explanation']['max_bytes']
        seen = []
        real = files.check

        def spy(kind, *, filename, data):
            seen.append(len(data))
            return real(kind, filename=filename, data=data)

        def send(size):
            body = b'%PDF-1.4\n' + b'0' * (size - 9)
            return self.client.post('/api/discipline/records/1/explanation', headers={'X-User-Id': str(OPERATOR)},
                                    data={'file': (io.BytesIO(body), 'объяснительная.pdf')},
                                    content_type='multipart/form-data')

        with mock.patch.object(files, 'check', spy):
            # Больше границы, но в потолке запроса: читается граница + 1 байт — на отказ хватает.
            response = send(limit + 512 * 1024)
            self.assertEqual((response.status_code, response.get_json()['code']), (400, 'DISCIPLINE_FILE_TOO_LARGE'))
            # Ровно граница — проходит проверку и уходит в хранилище; дальше нет базы,
            # и откат стирает залитое.
            with self.assertLogs(level='ERROR'):
                self.assertEqual(send(limit).status_code, 500)
        self.assertEqual(seen, [limit + 1, limit])
        self.assertEqual(self.store.blobs, {})


class RequestSizeRulesTests(unittest.TestCase):
    def test_caps_follow_file_kinds(self):
        self.assertEqual(catalog.request_max_bytes(), catalog.JSON_MAX_BYTES)
        self.assertEqual(catalog.request_max_bytes('nope'), catalog.JSON_MAX_BYTES)
        self.assertEqual(catalog.file_max_bytes('evidence'), 50 * MB)
        self.assertEqual(catalog.request_max_bytes('explanation'), 11 * MB)
        self.assertEqual(catalog.request_max_bytes('evidence'), 251 * MB)
        self.assertEqual(catalog.request_too_large_text('evidence'),
                         'Не больше 5 файлов за раз, файл — до 10 МБ, видео — до 50 МБ')

    @unittest.skipIf(flask is None, 'needs flask')
    def test_largest_honest_form_fits_under_the_cap(self):
        # Самая большая честная форма: все файлы вида по верхней границе, имена по
        # 255 знаков кириллицей, длинное описание. Накладные multipart меряются на
        # пустых файлах — от содержимого они не зависят.
        from werkzeug.test import EnvironBuilder

        fields = {'operator_id': '400', 'type_id': '12', 'occurred_at': '2026-10-20T12:00',
                  'shift_label': '21:00–09:00', 'evidence_note': '14:32–14:41, 15:05',
                  'description': 'Оператор спал на рабочем месте. ' * 600, 'department_id': '1',
                  'witness_ids': '300,301', 'client_request_id': 'c' * 64}
        name = ('Объяснительная ' * 17)[:251] + '.mp4'
        for kind, spec in catalog.FILE_KINDS.items():
            with self.subTest(kind=kind):
                parts = [(io.BytesIO(b''), name) for _ in range(spec['max_files'])]
                environ = EnvironBuilder(method='POST', data=dict(fields, files=parts)).get_environ()
                overhead = int(environ['CONTENT_LENGTH'])
                self.assertLess(overhead * 10, catalog.REQUEST_RESERVE_BYTES)
                self.assertLessEqual(spec['max_files'] * catalog.file_max_bytes(kind) + overhead,
                                     catalog.request_max_bytes(kind))

    def test_operator_sends_only_explanations(self):
        def ctx(role, headed=()):
            return {'user_id': 10, 'role': role, 'department_id': 1, 'headed_department_ids': list(headed),
                    'group_ids': [5], 'section_departments': {'szov': 1, 'op': 2, 'tez': 3}}

        for kind in catalog.FILE_KINDS:
            with self.subTest(kind=kind):
                allowed = kind == 'explanation'
                self.assertEqual(access.can_send_files(ctx('operator'), kind), allowed)
                self.assertEqual(access.can_send_files(ctx('trainee'), kind), allowed)
                for who in (ctx('sv'), ctx('admin', headed=[1]), ctx('super_admin')):
                    self.assertTrue(access.can_send_files(who, kind), who['role'])

    def test_every_file_route_declares_its_kind(self):
        # Потолок берётся из upload= у роута: роут, который читает файлы вида X, обязан
        # объявить upload='X' — иначе честный файл упрётся в потолок JSON (или наоборот).
        source = ROUTES.read_text(encoding='utf-8')
        found = []
        for block in re.split(r'\n    @route\(', source)[1:]:
            decorator = block.split('\n    def ', 1)[0]
            kinds = set(re.findall(r"_take_files\('(\w+)'", block))
            with self.subTest(route=decorator):
                self.assertLessEqual(len(kinds), 1)
                self.assertEqual(set(re.findall(r"upload='(\w+)'", decorator)), kinds)
            found.extend(kinds)
        self.assertEqual(sorted(found), sorted(kind for _path, kind, _user in UPLOAD_ROUTES))


if __name__ == '__main__':
    unittest.main()
