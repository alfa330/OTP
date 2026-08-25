# -*- coding: utf-8 -*-
"""Логотип («аватарка») таксопарка: загрузка, проверка id и видимость файла.

Три вещи, которые тут закрыты тестами, и каждая — отдельный класс отказа:

1. ЗАГРУЗКА идёт своей дверью (/parks/logo), а не общим /upload редактора: у
   того гейт can_create, у справочника — «что-то сверх чтения», и супервайзер с
   одним правом на правку получил бы отказ уже после выбора файла.
2. ID ФАЙЛА — ключ доступа. Роут /file/<id> открывает логотип каждому, кому
   видно пространство, поэтому вписать в парк произвольный uuid нельзя: иначе
   картинка из чужой статьи раздавалась бы своему пространству без загрузки.
3. ВИДИМОСТЬ. Непривязанный к статье файл роут отдаёт только загрузившему —
   на логотипе это значило бы битую картинку в рельсе витрины у всех
   остальных. Границей служит пространство самого справочника.
"""

import io
import json
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
from wiki import parks as wiki_parks  # noqa: E402
from wiki import queries  # noqa: E402
from wiki import routes_parks  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402

SPACE = 12
FILE_ID = '1ebdec21-42f3-4413-827b-6c6180da7317'

WRITER = {'can_read': True, 'can_create': False, 'can_edit': True}
READER = {'can_read': True}


class _RecordingCursor:
    """Курсор, который запоминает запросы и отдаёт заданные строки."""

    def __init__(self, rows=()):
        self.calls = []
        self.rows = list(rows)
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append((' '.join(str(sql).split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


class LogoSpacesTest(unittest.TestCase):
    """parks.logo_space_ids отвечает «чей это файл» — по обеим картинкам справочника."""

    def test_asks_both_directory_pictures(self):
        cursor = _RecordingCursor([(SPACE,), (13,)])
        self.assertEqual(wiki_parks.logo_space_ids(cursor, FILE_ID), {SPACE, 13})
        sql = cursor.calls[-1][0]
        self.assertIn('wiki_taxi_parks', sql)
        # Баннер акции — та же яма: колонка есть в схеме с самого начала.
        self.assertIn('wiki_promotions', sql)

    def test_unknown_file_belongs_to_nobody(self):
        self.assertEqual(wiki_parks.logo_space_ids(_RecordingCursor(), FILE_ID), set())


class LogoFieldTest(unittest.TestCase):
    """Что принимается в поле logo_file_id парка."""

    def test_garbage_never_reaches_the_database(self):
        """Мусор отсекается разбором uuid: иначе строка «сам ты логотип» уехала
        бы в колонку типа UUID и вернулась пятисоткой вместо понятного отказа."""
        for value in (None, '', 'сам ты логотип', 123, {'id': FILE_ID}):
            cursor = _RecordingCursor()
            self.assertIsNone(routes_parks._logo_file(cursor, value,
                                                      user_id=42, space_id=SPACE))
            self.assertEqual(cursor.calls, [])

    def test_own_fresh_file_is_accepted(self):
        cursor = _RecordingCursor([(1,)])
        self.assertEqual(
            routes_parks._logo_file(cursor, FILE_ID, user_id=42, space_id=SPACE),
            FILE_ID)

    def test_query_refuses_article_pictures_and_asks_about_the_space(self):
        """Условие запроса и есть защита: файл не должен быть привязан к статье
        (у неё своя граница), а чужой — обязан уже стоять логотипом ЗДЕСЬ."""
        cursor = _RecordingCursor([(1,)])
        routes_parks._logo_file(cursor, FILE_ID, user_id=42, space_id=SPACE)
        sql, params = cursor.calls[-1]
        self.assertIn('article_id IS NULL', sql)
        self.assertIn('uploaded_by', sql)
        self.assertIn('space_id', sql)
        self.assertEqual(params['space'], SPACE)
        self.assertEqual(params['user'], 42)

    def test_foreign_file_is_dropped(self):
        """Запрос ничего не нашёл — значит логотипа нет, а не «сохраним как
        есть»: молча записанный чужой uuid и был бы утечкой."""
        self.assertIsNone(routes_parks._logo_file(_RecordingCursor(), FILE_ID,
                                                  user_id=42, space_id=SPACE))


def _capabilities_setter(capabilities):
    def load(_cursor, ctx, _subjects):
        ctx['capabilities'] = dict(capabilities)
        ctx['role_capabilities'] = dict(capabilities)
        ctx['publish_sections'] = []
        return dict(capabilities)
    return load


class LogoFrameTest(unittest.TestCase):
    """Ракурс: какая часть картинки видна в плитке.

    Приезжает из тела запроса, то есть от человека, — значит проверяется здесь,
    а не рисованием: «ракурс» с zoom=1e9 показал бы в плитке один пиксель.
    """

    def test_frame_is_taken_as_four_numbers(self):
        frame = routes_parks._logo_frame({'zoom': 1.6, 'x': 0.32, 'y': 0.6, 'ratio': 1.5})
        self.assertEqual(frame, {'zoom': 1.6, 'x': 0.32, 'y': 0.6, 'ratio': 1.5})

    def test_numbers_are_clamped(self):
        frame = routes_parks._logo_frame({'zoom': 1e9, 'x': -5, 'y': 42, 'ratio': 1e9})
        self.assertEqual(frame, {'zoom': routes_parks._LOGO_ZOOM_MAX, 'x': 0.0,
                                 'y': 1.0, 'ratio': 20.0})

    def test_garbage_falls_back_to_the_defaults(self):
        """Мусор в одном ключе не должен ронять весь ракурс: остальные числа
        осмысленные, и отбросить их значило бы потерять уже выбранный кадр."""
        self.assertEqual(routes_parks._logo_frame({'zoom': 'близко', 'x': 0.2,
                                                   'y': None, 'ratio': 2}),
                         {'zoom': 1.0, 'x': 0.2, 'y': 0.5, 'ratio': 2.0})

    def test_center_without_zoom_is_no_frame_at_all(self):
        """Иначе в базе лежали бы две записи одного и того же: NULL и «ракурс»,
        который ничего не меняет."""
        self.assertIsNone(routes_parks._logo_frame({'zoom': 1, 'x': 0.5, 'y': 0.5,
                                                    'ratio': 1}))
        self.assertIsNone(routes_parks._logo_frame({}))
        self.assertIsNone(routes_parks._logo_frame('ракурс'))

    def test_frame_goes_to_the_database_as_json(self):
        """psycopg словарь в JSONB сам не адаптирует — молча упало бы при
        сохранении первого же ракурса."""
        self.assertEqual(json.loads(routes_parks._frame_json({'zoom': 2})), {'zoom': 2})
        self.assertIsNone(routes_parks._frame_json(None))


class _Harness:
    """Блюпринт вики на подменённом курсоре, с заданными правами и бакетом."""

    def build(self, *, capabilities=WRITER, spaces=(SPACE,), fetchone=None,
              rowcount=0):
        cursor = MagicMock()
        cursor.fetchone.return_value = fetchone if fetchone is not None else (FILE_ID,)
        cursor.fetchall.return_value = []
        cursor.rowcount = rowcount

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        context = {
            'user_id': 42, 'otp_role': 'sv', 'department_id': 560,
            'direction_id': None, 'headed_department_ids': [], 'group_ids': [],
            'wiki_roles': [], 'access_mode': 'auto',
        }
        for name, value in (
            ('load_access_context', lambda _c, _u: dict(context)),
            # load_capabilities не возвращает, а ПРОСТАВЛЯЕТ ключи в ctx —
            # подмена обязана делать то же, иначе гейт справочника не найдёт
            # способностей и упадёт вместо ответа.
            ('load_capabilities', _capabilities_setter(capabilities)),
            ('spaces_for_user', lambda _c, _ctx: list(spaces)),
        ):
            original = getattr(queries, name)
            setattr(queries, name, value)
            self.addCleanup(setattr, queries, name, original)

        self.blob = MagicMock()
        client = MagicMock()
        client.bucket.return_value.blob.return_value = self.blob

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (42, None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
            gcs={
                'bucket_name': lambda: 'otp-files',
                'client': lambda: client,
                'signed_url': lambda *a, **kw: 'https://storage.example/signed',
            },
        ))
        app.config['TESTING'] = True
        return app.test_client(), cursor

    @staticmethod
    def png(size=64):
        return {'file': (io.BytesIO(b'\x89PNG' + b'0' * size), 'logo.png', 'image/png')}


@unittest.skipIf(Flask is None, 'flask не установлен')
class LogoUploadRouteTest(_Harness, unittest.TestCase):

    URL = '/api/wiki/parks/logo?space_id=%d' % SPACE

    def test_reader_cannot_upload(self):
        client, _ = self.build(capabilities=READER)
        response = client.post(self.URL, data=self.png(), content_type='multipart/form-data')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_FORBIDDEN')

    def test_foreign_space_is_not_found(self):
        """Та же граница, что у самого справочника: чужое пространство — 404."""
        client, _ = self.build(spaces=(13,))
        response = client.post(self.URL, data=self.png(), content_type='multipart/form-data')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json().get('code'), 'WIKI_SPACE_NOT_FOUND')

    def test_only_pictures(self):
        """SVG — исполняемый документ, а не картинка; в справочнике, который
        правит половина отдела, ему не место."""
        client, _ = self.build()
        for content_type in ('image/svg+xml', 'application/pdf', 'text/html'):
            response = client.post(
                self.URL,
                data={'file': (io.BytesIO(b'<svg/>'), 'logo.svg', content_type)},
                content_type='multipart/form-data')
            self.assertEqual(response.status_code, 400, content_type)

    def test_too_big_is_refused(self):
        client, _ = self.build()
        response = client.post(
            self.URL,
            data={'file': (io.BytesIO(b'0' * (routes_parks._LOGO_MAX_BYTES + 1)),
                           'logo.png', 'image/png')},
            content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)
        self.assertIn('больше', response.get_json()['error'])

    def test_upload_returns_id_and_address(self):
        """file_id отдельно от url: в парк форма кладёт именно id, а адрес ей
        нужен только чтобы показать картинку до сохранения."""
        client, cursor = self.build()
        response = client.post(self.URL, data=self.png(), content_type='multipart/form-data')
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body['file_id'], FILE_ID)
        self.assertEqual(body['url'], '/api/wiki/file/%s' % FILE_ID)
        self.blob.upload_from_string.assert_called_once()
        self.assertEqual(self.blob.upload_from_string.call_args.kwargs['content_type'],
                         'image/png')
        # Статьи у логотипа нет и не будет — видимость ему даёт справочник.
        insert = [call for call in cursor.execute.call_args_list
                  if 'INSERT INTO wiki_files' in ' '.join(str(call.args[0]).split())]
        self.assertEqual(len(insert), 1)
        self.assertIsNone(insert[0].args[1][0])


@unittest.skipIf(Flask is None, 'flask не установлен')
class LogoFrameRouteTest(_Harness, unittest.TestCase):
    """Ракурс живёт рядом с логотипом и уходит вместе с ним."""

    def _patch(self, body, *, park_exists=True):
        # rowcount=1 — «строка обновилась»: иначе роут честно отвечает
        # «Нечего обновлять», и до проверки самого ракурса дело не доходит.
        client, cursor = self.build(fetchone=(1,) if park_exists else None,
                                    rowcount=1)
        response = client.patch('/api/wiki/parks/3?space_id=%d' % SPACE, json=body)
        updates = [' '.join(str(call.args[0]).split()) for call in cursor.execute.call_args_list
                   if 'UPDATE wiki_taxi_parks' in ' '.join(str(call.args[0]).split())]
        values = [call.args[1] for call in cursor.execute.call_args_list
                  if 'UPDATE wiki_taxi_parks' in ' '.join(str(call.args[0]).split())]
        return response, (updates[0] if updates else ''), (values[0] if values else [])

    def test_frame_is_written_next_to_the_logo(self):
        response, sql, values = self._patch({'logo_file_id': FILE_ID,
                                             'logo_frame': {'zoom': 1.8, 'x': 0.2,
                                                            'y': 0.4, 'ratio': 2}})
        self.assertEqual(response.status_code, 200)
        self.assertIn('logo_frame = %s', sql)
        self.assertIn(json.dumps({'zoom': 1.8, 'x': 0.2, 'y': 0.4, 'ratio': 2.0}), values)

    def test_removing_the_logo_removes_the_frame(self):
        """Кадр без картинки достался бы следующей загрузке — и она открылась
        бы в чужом ракурсе."""
        _response, sql, values = self._patch({'logo_file_id': None,
                                              'logo_frame': {'zoom': 2, 'x': 0.1,
                                                             'y': 0.1, 'ratio': 2}})
        self.assertIn('logo_frame = %s', sql)
        self.assertIsNone(values[list(values).index(None)])
        self.assertEqual([v for v in values if isinstance(v, str) and 'zoom' in v], [])


@unittest.skipIf(Flask is None, 'flask не установлен')
class LogoVisibilityRouteTest(_Harness, unittest.TestCase):
    """/file/<id> отдаёт логотип всем, кому видно пространство, — и только им."""

    def _serve(self, *, logo_spaces, spaces=(SPACE,), uploaded_by=7):
        client, _ = self.build(spaces=spaces)
        original_file = wiki_articles.get_file
        original_logo = wiki_parks.logo_space_ids
        wiki_articles.get_file = lambda _c, _f: {
            'id': FILE_ID, 'article_id': None, 'bucket': 'otp-files',
            'blob_path': 'wiki/files/logo.png', 'original_name': 'logo.png',
            'content_type': 'image/png', 'uploaded_by': uploaded_by,
        }
        wiki_parks.logo_space_ids = lambda _c, _f: set(logo_spaces)
        self.addCleanup(setattr, wiki_articles, 'get_file', original_file)
        self.addCleanup(setattr, wiki_parks, 'logo_space_ids', original_logo)
        return client.get('/api/wiki/file/%s' % FILE_ID)

    def test_logo_of_my_space_is_served(self):
        """Иначе аватарку видел бы один загрузивший, а у остальных в рельсе
        витрины стояла бы битая картинка."""
        self.assertEqual(self._serve(logo_spaces={SPACE}).status_code, 302)

    def test_logo_of_a_foreign_space_is_not_found(self):
        self.assertEqual(self._serve(logo_spaces={13}).status_code, 404)

    def test_plain_upload_of_another_person_stays_hidden(self):
        """Файл, который справочнику не принадлежит, остаётся при прежнем
        правиле: пока привязки к статье нет, он виден только загрузившему."""
        self.assertEqual(self._serve(logo_spaces=set()).status_code, 404)


if __name__ == '__main__':
    unittest.main()
