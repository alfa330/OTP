# -*- coding: utf-8 -*-
"""Фотографии вещи в карточке посылки — то, что ломается молча.

Проверяется не «работает ли загрузка вообще», а решения, потеря которых не
видна на экране:

  * файл, который Pillow не открыл, ОТКЛОНЯЕТСЯ, а не кладётся в бакет как
    принесли (иначе PDF с заголовком image/jpeg лёг бы в хранилище и
    раздавался бы оттуда подписанной ссылкой);
  * лимит в десять снимков держится при двух одновременных загрузках — то есть
    карточка блокируется, а не пересчитывается «на глазок»;
  * снятие ищет строку по ОБОИМ ключам: без parcel_id идентификатор снимка сам
    стал бы ключом доступа к чужой карточке;
  * блобы удаляются ПОСЛЕ коммита, а неудача их удаления не превращается в 500
    на успешно удалённой записи;
  * без таблицы `parcel_photos` реестр продолжает работать целиком;
  * подпись адресов строит клиент хранилища ОДИН раз и кэширует результат —
    иначе на карточке было бы двадцать разборов приватного ключа, а браузер
    заново качал бы миниатюры при каждом открытии.

Базы и сети здесь нет: курсор подменён, хранилище — двойник.
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

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

from parcels import photos  # noqa: E402
from parcels import queries as parcels_queries  # noqa: E402
from parcels import schema as parcels_schema  # noqa: E402
from parcels.routes import build_parcels_blueprint  # noqa: E402
from wiki import images as wiki_images  # noqa: E402


ACTOR = {'user_id': 427, 'name': 'Аликулова Айдана'}
PHOTOS_PY = (ROOT / 'parcels' / 'photos.py').read_text(encoding='utf-8')
ROUTES_PY = (ROOT / 'parcels' / 'routes.py').read_text(encoding='utf-8')
FRONT_META = (ROOT / 'src' / 'components' / 'parcels' / 'parcelPhoto.js').read_text(encoding='utf-8')
PHOTOS_JSX = (ROOT / 'src' / 'components' / 'parcels' / 'ParcelPhotos.jsx').read_text(encoding='utf-8')
IOS_JSX = (ROOT / 'src' / 'components' / 'ui' / 'ios.jsx').read_text(encoding='utf-8')


def code_only(source):
    """Исходник без комментариев и строковых литералов.

    Сторожа ниже запрещают ВЫЗОВЫ, а не упоминания: пояснение «abort() здесь
    запрещён» само содержит запрещённую подстроку, и наивная проверка требовала
    бы удалить из кода объяснение, ради которого правило и записано.
    """
    import tokenize
    kept = []
    reader = io.StringIO(source).readline
    for token in tokenize.generate_tokens(reader):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        kept.append(token.string)
    return ' '.join(kept)


def png_bytes(width=1200, height=900, mode='RGB'):
    """Настоящая картинка в память — фикстур с фотографиями в репозитории нет."""
    image = Image.new(mode, (width, height), (40, 110, 200) if mode == 'RGB' else (40, 110, 200, 255))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def jpeg_bytes(width=1200, height=900):
    image = Image.new('RGB', (width, height))
    pixels = image.load()
    for y in range(0, height, 6):
        for x in range(0, width, 6):
            colour = ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
            for dy in range(6):
                for dx in range(6):
                    if x + dx < width and y + dy < height:
                        pixels[x + dx, y + dy] = colour
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=92)
    return buffer.getvalue()


class _RecordingCursor:
    """Курсор-двойник: помнит запросы и отдаёт заранее подготовленные ответы."""

    def __init__(self, rows=None):
        self.statements = []
        self.rows = list(rows or [])
        self.rowcount = 1

    def execute(self, statement, params=None):
        self.statements.append((' '.join(str(statement).split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return self.rows.pop(0) if self.rows else []

    def events(self):
        found = []
        for statement, params in self.statements:
            if 'INSERT INTO parcel_events' not in statement:
                continue
            found.append((params[1], json.loads(params[4])))
        return found

    def sql(self):
        return [statement for statement, _params in self.statements]


class FakeBlob:
    def __init__(self, store, bucket, path):
        self.store = store
        self.bucket = bucket
        self.path = path
        self.cache_control = None

    def upload_from_string(self, data, content_type=None):
        self.store.log.append(('upload', self.bucket, self.path, self.cache_control))
        self.store.objects[(self.bucket, self.path)] = data

    def delete(self):
        self.store.log.append(('delete', self.bucket, self.path, None))
        if (self.bucket, self.path) not in self.store.objects:
            raise RuntimeError('404 No such object')
        self.store.objects.pop((self.bucket, self.path))

    def generate_signed_url(self, **_kwargs):
        self.store.signed += 1
        return 'https://storage.example/%s/%s?sig=%d' % (self.bucket, self.path, self.store.signed)


class FakeBucket:
    def __init__(self, store, name):
        self.store = store
        self.name = name

    def blob(self, path):
        return FakeBlob(self.store, self.name, path)


class FakeStorage:
    """Двойник хранилища: пишет всё в общий журнал вызовов."""

    def __init__(self, bucket='parcels-test'):
        self.bucket_name = bucket
        self.objects = {}
        self.log = []
        self.clients = 0
        self.signed = 0

    def client(self):
        self.clients += 1
        return self

    def bucket(self, name):
        return FakeBucket(self, name)

    def as_gcs(self):
        return {'bucket_name': lambda: self.bucket_name, 'client': self.client}

    def kinds(self):
        return [entry[0] for entry in self.log]


class SchemaTests(unittest.TestCase):
    """DDL: таблица, порядок разворота и признак готовности."""

    def setUp(self):
        self.ddl = '\n'.join(parcels_schema._STATEMENTS)

    def test_table_exists_and_is_owned_by_the_parcel(self):
        self.assertIn('CREATE TABLE IF NOT EXISTS parcel_photos', self.ddl)
        self.assertIn('parcel_id        INTEGER NOT NULL REFERENCES parcels(id) ON DELETE CASCADE',
                      self.ddl)

    def test_index_is_not_mistaken_for_a_table(self):
        """`_is_table` ищет подстроку CREATE TABLE тупо. Попади она в текст
        индекса — индекс выполнился бы в первом проходе, то есть раньше
        миграций: ровно та ошибка порядка, что уронила «Обращения» 17.08.2026."""
        index = [s for s in parcels_schema._STATEMENTS if 'idx_parcel_photos_parcel' in s]
        self.assertEqual(len(index), 1)
        self.assertNotIn('CREATE TABLE', index[0].upper())

    def test_index_goes_after_the_table(self):
        statements = parcels_schema._STATEMENTS
        table = next(i for i, s in enumerate(statements)
                     if 'CREATE TABLE IF NOT EXISTS parcel_photos' in s)
        index = next(i for i, s in enumerate(statements) if 'idx_parcel_photos_parcel' in s)
        self.assertLess(table, index)

    def test_history_knows_the_two_new_events(self):
        self.assertIn('photo_added', parcels_schema.EVENT_KINDS)
        self.assertIn('photo_removed', parcels_schema.EVENT_KINDS)

    def test_readiness_of_the_registry_does_not_depend_on_photos(self):
        """Иначе осечка на необязательной таблице спрятала бы работающий реестр."""
        registry = _RecordingCursor(rows=[(True,)])
        parcels_schema.schema_is_ready(registry)
        self.assertIn('public.parcels', registry.sql()[0])
        self.assertNotIn('parcel_photos', registry.sql()[0])

        pictures = _RecordingCursor(rows=[(True,)])
        parcels_schema.photos_ready(pictures)
        self.assertIn('public.parcel_photos', pictures.sql()[0])

    def test_no_migration_was_added_for_photos(self):
        """Таблица целиком новая — ALTER'ы по живой базе ей не нужны."""
        self.assertNotIn('parcel_photos', '\n'.join(parcels_schema._MIGRATIONS))


class PreparePolicyTests(unittest.TestCase):
    """Что принимаем, что отклоняем и во что превращаем."""

    def _swap(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def test_unreadable_file_is_refused_not_stored(self):
        """ГЛАВНОЕ отличие от вики. Там файл, который не открылся, кладётся как
        принесли — он вложение статьи. Здесь единственный смысл строки —
        показать картинку, и байты раздаются прямо из бакета: запрос с
        Content-Type image/jpeg и телом PDF прошёл бы и белый список, и
        проверку веса."""
        self._swap(wiki_images, 'to_webp', lambda _data, _kind: None)
        with self.assertRaises(photos.PhotoError) as refusal:
            photos.prepare(b'%PDF-1.4 not a picture', filename='akt.jpg',
                           content_type='image/jpeg')
        self.assertEqual(refusal.exception.code, 'PARCEL_PHOTO_UNREADABLE')
        self.assertEqual(refusal.exception.status, 400)

    def test_original_bytes_back_from_the_converter_are_not_a_refusal(self):
        """Кортеж с исходными байтами означает «Pillow открыл, пережимать
        нечего» — уже WebP в габаритах либо пережатие вышло в минус."""
        self._swap(wiki_images, 'to_webp',
                   lambda data, _kind: (data, 'image/jpeg', 800, 600))
        self._swap(photos, 'make_thumb', lambda _data, _kind: None)
        out = photos.prepare(b'jpeg bytes', filename='box.jpg', content_type='image/jpeg')
        self.assertEqual(out['content_type'], 'image/jpeg')
        self.assertEqual(out['original_name'], 'box.jpg')

    def test_converted_frame_is_renamed_to_webp(self):
        self._swap(wiki_images, 'to_webp',
                   lambda _data, _kind: (b'webp bytes', 'image/webp', 640, 480))
        self._swap(photos, 'make_thumb', lambda _data, _kind: (b'thumb', 320, 240))
        out = photos.prepare(b'jpeg bytes', filename='коробка.jpg', content_type='image/jpeg')
        self.assertEqual(out['content_type'], 'image/webp')
        self.assertTrue(out['original_name'].endswith('.webp'))
        self.assertEqual((out['width'], out['height']), (640, 480))
        self.assertEqual((out['thumb_width'], out['thumb_height']), (320, 240))

    def test_every_refusal_says_why(self):
        cases = {
            'application/pdf': 'JPEG',
            'image/svg+xml': 'JPEG',
            'image/heic': 'HEIC',
            '': 'формат',
        }
        for kind, expected in cases.items():
            issue = photos.photo_issue(filename='f', content_type=kind, size=100)
            self.assertIsNotNone(issue, kind)
            self.assertIn(expected, issue, kind)

        self.assertIn('пустой', photos.photo_issue(
            filename='f.jpg', content_type='image/jpeg', size=0))
        self.assertIn('20 МБ', photos.photo_issue(
            filename='f.jpg', content_type='image/jpeg', size=photos.MAX_BYTES + 1))
        self.assertIsNone(photos.photo_issue(
            filename='f.jpg', content_type='image/jpeg', size=1024))

    def test_blob_path_is_ours_and_unique(self):
        first = photos.blob_path_for('коробка №1.jpg')
        second = photos.blob_path_for('коробка №1.jpg')
        self.assertTrue(first.startswith('parcels/photos/'), first)
        self.assertNotEqual(first, second)
        self.assertNotIn('№', first)
        self.assertIn('_thumb', photos.blob_path_for('a.jpg', thumb=True))

    def test_blob_path_uses_almaty_date(self):
        """На Render процесс живёт в UTC, и снятое ночью по Алматы легло бы во
        вчерашнюю папку — как это делает blob_path_for вики."""
        import datetime
        self._swap(photos, 'now_almaty', lambda: datetime.datetime(2026, 9, 1, 2, 30))
        self.assertIn('parcels/photos/2026/09/01/', photos.blob_path_for('a.jpg'))


@unittest.skipIf(Image is None, 'нет Pillow')
class ConvertTests(unittest.TestCase):
    """Миниатюра — то, чего в вики нет и что нельзя проверить её тестами."""

    def test_big_frame_becomes_a_small_webp(self):
        source = jpeg_bytes(3000, 2000)
        thumb = photos.make_thumb(source, 'image/jpeg')
        self.assertIsNotNone(thumb)
        data, width, height = thumb
        self.assertLessEqual(max(width, height), photos.THUMB_SIDE)
        self.assertLess(len(data), len(source))
        self.assertEqual(Image.open(io.BytesIO(data)).format, 'WEBP')

    def test_small_frame_is_not_stretched(self):
        """Растянутая плитка выглядит хуже, а весит столько же: пусть плитка
        покажет сам кадр."""
        self.assertIsNone(photos.make_thumb(jpeg_bytes(300, 200), 'image/jpeg'))

    def test_transparency_does_not_turn_black(self):
        image = Image.new('RGBA', (900, 700), (0, 0, 0, 0))
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        thumb = photos.make_thumb(buffer.getvalue(), 'image/png')
        self.assertIsNotNone(thumb)
        small = Image.open(io.BytesIO(thumb[0]))
        self.assertIn(small.mode, ('RGBA', 'RGB'))

    def test_giant_frame_is_left_alone(self):
        """Распакованный кадр занимает 4 байта на пиксель независимо от веса
        файла; съесть на нём память воркера Render нельзя."""
        big = Image.new('RGB', (6000, 5000))
        buffer = io.BytesIO()
        big.save(buffer, format='PNG')
        self.assertIsNone(photos.make_thumb(buffer.getvalue(), 'image/png'))

    def test_the_whole_way_through_for_a_phone_shot(self):
        source = jpeg_bytes(2400, 1800)
        out = photos.prepare(source, filename='IMG_0042.jpg', content_type='image/jpeg')
        self.assertEqual(out['content_type'], 'image/webp')
        self.assertLess(out['file_size'], len(source))
        self.assertTrue(out['thumb'])


class SignTests(unittest.TestCase):
    """Подписанные адреса: цена, кэш и что уходит наружу."""

    def setUp(self):
        photos._SIGNED.clear()
        self.addCleanup(photos._SIGNED.clear)
        self.store = FakeStorage()
        self.rows = [{
            'id': 'photo-%d' % i, 'bucket': 'b', 'blob_path': 'full-%d' % i,
            'thumb_blob_path': 'thumb-%d' % i, 'content_type': 'image/webp',
            'file_size': 1000, 'width': 800, 'height': 600, 'thumb_width': 480,
            'thumb_height': 360, 'sort_order': i, 'created_at': None,
            'uploaded_by_name': 'Менеджер',
        } for i in range(10)]

    def test_one_client_for_the_whole_card(self):
        """get_gcs_client не мемоизирован: каждый вызов — json.loads учётных
        данных и разбор приватного ключа RSA. На карточке с десятью снимками
        это было бы двадцать таких разборов."""
        photos.sign_urls(self.store.as_gcs(), self.rows)
        self.assertEqual(self.store.clients, 1)
        self.assertEqual(self.store.signed, 20)

    def test_second_open_reuses_the_same_address(self):
        """Подпись v4 кладёт в адрес момент подписания — каждая новая строка
        отличается, и браузер качал бы миниатюры заново."""
        first = photos.sign_urls(self.store.as_gcs(), self.rows)
        second = photos.sign_urls(self.store.as_gcs(), self.rows)
        self.assertEqual(self.store.signed, 20)
        self.assertEqual(first[0]['url'], second[0]['url'])
        # Клиент во второй раз не понадобился вовсе.
        self.assertEqual(self.store.clients, 1)

    def test_missing_thumbnail_falls_back_to_the_full_frame(self):
        row = dict(self.rows[0], thumb_blob_path=None)
        out = photos.sign_urls(self.store.as_gcs(), [row])
        self.assertEqual(out[0]['thumb_url'], out[0]['url'])

    def test_a_broken_signature_is_a_null_not_an_exception(self):
        class Broken(FakeStorage):
            def bucket(self, name):
                raise RuntimeError('нет приватного ключа')

        broken = Broken()
        out = photos.sign_urls(broken.as_gcs(), [self.rows[0]])
        self.assertIsNone(out[0]['url'])

    def test_the_bucket_never_leaves_the_server(self):
        out = photos.sign_urls(self.store.as_gcs(), self.rows)
        self.assertNotIn('bucket', out[0])
        self.assertNotIn('blob_path', out[0])
        self.assertNotIn('thumb_blob_path', out[0])

    def test_deleting_an_absent_object_is_not_an_error(self):
        """Повторное удаление и файл, снятый руками, — успех, а не сбой."""
        removed = photos.drop_blobs(self.store.as_gcs(), [('b', 'never-existed')])
        self.assertEqual(removed, 0)

    def test_drop_blobs_never_raises(self):
        """Оно вызывается ПОСЛЕ коммита: исключение здесь означало бы 500 на
        успешно выполненной операции."""
        class Broken(FakeStorage):
            def client(self):
                raise RuntimeError('учётные данные не читаются')

        self.assertEqual(photos.drop_blobs(Broken().as_gcs(), [('b', 'p')]), 0)


class QueryTests(unittest.TestCase):
    """SQL: атомарность лимита, защита от чужого снимка, история."""

    def test_limit_is_counted_under_a_lock(self):
        """Без FOR UPDATE проверка «меньше десяти» не атомарна: на READ
        COMMITTED счётчик не видит незакоммиченных вставок соседней
        транзакции, и две вкладки при девяти увидели бы девять."""
        cursor = _RecordingCursor(rows=[(1,), (9,)])
        self.assertEqual(parcels_queries.lock_parcel_photos(cursor, 42), 9)
        self.assertIn('FOR UPDATE', cursor.sql()[0])

    def test_missing_parcel_is_told_apart_from_an_empty_one(self):
        cursor = _RecordingCursor(rows=[None])
        self.assertIsNone(parcels_queries.lock_parcel_photos(cursor, 42))

    def test_insert_writes_the_row_and_the_event_together(self):
        cursor = _RecordingCursor(rows=[('11111111-2222-4333-8444-555555555555',)])
        parcels_queries.insert_photo(
            cursor, 42,
            prepared={'content_type': 'image/webp', 'file_size': 100, 'width': 8,
                      'height': 6, 'thumb_width': 4, 'thumb_height': 3,
                      'original_name': 'box.webp'},
            bucket='b', blob_path='p', thumb_blob_path='t', sort_order=0, actor=ACTOR)
        self.assertTrue(any('INSERT INTO parcel_photos' in s for s in cursor.sql()))
        kinds = cursor.events()
        self.assertEqual([kind for kind, _p in kinds], ['photo_added'])
        # Лента отдаётся всем читателям раздела — путям в бакете там не место.
        self.assertEqual(set(kinds[0][1]), {'photo_id'})

    def test_deleting_checks_both_keys(self):
        """Без parcel_id идентификатор снимка сам стал бы ключом доступа:
        правкой своей карточки можно было бы стереть чужую фотографию."""
        cursor = _RecordingCursor(rows=[('b', 'full', 'thumb')])
        refs = parcels_queries.delete_photo(cursor, 42, 'photo-1', actor=ACTOR)
        statement = cursor.sql()[0]
        self.assertIn('id = %s', statement)
        self.assertIn('parcel_id = %s', statement)
        self.assertEqual(refs, [('b', 'full'), ('b', 'thumb')])
        self.assertEqual([kind for kind, _p in cursor.events()], ['photo_removed'])

    def test_nothing_deleted_means_no_event(self):
        cursor = _RecordingCursor(rows=[None])
        self.assertIsNone(parcels_queries.delete_photo(cursor, 42, 'photo-1', actor=ACTOR))
        self.assertEqual(cursor.events(), [])

    def test_refs_collect_both_the_frame_and_the_thumbnail(self):
        cursor = _RecordingCursor(rows=[[('b', 'f1', 't1'), ('b', 'f2', None)]])
        self.assertEqual(parcels_queries.photo_blob_refs(cursor, 42),
                         [('b', 'f1'), ('b', 't1'), ('b', 'f2')])

    def test_photos_stay_out_of_the_parcel_column_list(self):
        """Дописанное в _PARCEL_FIELDS имя дало бы `p.photos` в SELECT и 500 на
        списке, чтении, выгрузке и внутри update_parcel — авария 25.08.2026."""
        for name in ('photos', 'photo_count', 'parcel_photos'):
            self.assertNotIn(name, parcels_queries._PARCEL_FIELDS)
            self.assertNotIn(name, parcels_queries._INSERT_FIELDS)
            self.assertNotIn(name, parcels_queries._EDITABLE_FIELDS)


@unittest.skipIf(Flask is None, 'нет Flask')
class RouteTests(unittest.TestCase):
    """Двери: коды ответов, уборка за собой и порядок действий."""

    def build(self, *, context=None, storage=None, photos_ready=True, taken=0,
              parcel_exists=True, insert_raises=False):
        context = context or {'user_id': 5, 'name': 'Менеджер фронт-офиса',
                              'role': 'operator', 'department_id': 7,
                              'department_code': 'front_office', 'city': 'Алматы',
                              'headed_department_ids': [], 'headed_department_codes': []}
        cursor = MagicMock()
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def _patch(module, name, value):
            original = getattr(module, name)
            setattr(module, name, value)
            self.addCleanup(setattr, module, name, original)

        def _insert(*_args, **_kwargs):
            if insert_raises:
                raise RuntimeError('строка не вставилась')
            return 'photo-new'

        _patch(parcels_queries, 'load_access_context', lambda _c, _uid: dict(context))
        _patch(parcels_schema, 'schema_is_ready', lambda _c: True)
        _patch(parcels_schema, 'photos_ready', lambda _c: photos_ready)
        _patch(parcels_queries, 'lock_parcel_photos',
               lambda _c, _pid: (taken if parcel_exists else None))
        _patch(parcels_queries, 'insert_photo', _insert)
        _patch(parcels_queries, 'list_photos', lambda _c, _pid: [])
        _patch(parcels_queries, 'list_events', lambda _c, _pid: [])
        _patch(parcels_queries, 'read_parcel', lambda _c, _pid: {'id': 1, 'status': 'in_office'})
        _patch(parcels_queries, 'status_counters', lambda _c, **_k: {})
        _patch(parcels_queries, 'photo_blob_refs', lambda _c, _pid: [('b', 'f'), ('b', 't')])
        _patch(parcels_queries, 'delete_parcel', lambda _c, _pid: True)

        app = Flask(__name__)
        app.register_blueprint(build_parcels_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=lambda _uid: True,
            gcs=storage.as_gcs() if storage else None,
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def payload(self, data=b'binary', name='box.jpg', kind='image/jpeg'):
        return {'file': (io.BytesIO(data), name, kind)}

    def _accept_any_picture(self):
        original = photos.prepare
        self.addCleanup(setattr, photos, 'prepare', original)
        photos.prepare = lambda data, *, filename, content_type: {
            'data': b'webp', 'content_type': 'image/webp', 'original_name': 'box.webp',
            'file_size': 4, 'width': 8, 'height': 6, 'thumb': b'th',
            'thumb_width': 4, 'thumb_height': 3,
        }

    def test_upload_without_storage_says_so(self):
        client = self.build(storage=None)
        response = client.post('/api/parcels/1/photos', data=self.payload())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['code'], 'PARCEL_PHOTO_STORAGE_OFF')

    def test_upload_without_a_file_is_a_plain_refusal(self):
        client = self.build(storage=FakeStorage())
        response = client.post('/api/parcels/1/photos', data={})
        self.assertEqual(response.status_code, 400)

    def test_an_oversized_body_is_refused_before_it_is_read(self):
        store = FakeStorage()
        client = self.build(storage=store)
        # Заголовок подменяем в environ: тестовый клиент считает Content-Length
        # сам по телу, и обычный headers= его бы не пересилил.
        response = client.post(
            '/api/parcels/1/photos', data=b'x' * 32, content_type='image/jpeg',
            environ_overrides={'CONTENT_LENGTH': str(photos.MAX_BYTES + 1)})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(store.log, [])

    def test_a_request_without_content_length_does_not_blow_up(self):
        """`None > int` дал бы TypeError, то есть 500 вместо честного отказа."""
        store = FakeStorage()
        client = self.build(storage=store)
        environ = {'CONTENT_LENGTH': ''}
        response = client.post('/api/parcels/1/photos', data={}, environ_overrides=environ)
        self.assertNotEqual(response.status_code, 500)

    def test_a_pdf_pretending_to_be_a_photo_never_reaches_the_bucket(self):
        store = FakeStorage()
        client = self.build(storage=store)
        response = client.post('/api/parcels/1/photos',
                               data=self.payload(b'%PDF-1.4 not a picture'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'PARCEL_PHOTO_UNREADABLE')
        self.assertEqual(store.log, [])

    def test_a_full_card_gets_its_blobs_cleaned_up(self):
        """Лимит проверяется уже ПОСЛЕ заливки (пережатие держать на слоте пула
        нельзя), поэтому отказ обязан убрать за собой оба файла."""
        store = FakeStorage()
        self._accept_any_picture()
        client = self.build(storage=store, taken=photos.MAX_PER_PARCEL)
        response = client.post('/api/parcels/1/photos', data=self.payload())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'PARCEL_PHOTO_LIMIT')
        self.assertEqual(store.kinds(), ['upload', 'upload', 'delete', 'delete'])
        self.assertEqual(store.objects, {})

    def test_a_vanished_parcel_gets_its_blobs_cleaned_up(self):
        store = FakeStorage()
        self._accept_any_picture()
        client = self.build(storage=store, parcel_exists=False)
        response = client.post('/api/parcels/1/photos', data=self.payload())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(store.objects, {})

    def test_a_failed_insert_gets_its_blobs_cleaned_up(self):
        store = FakeStorage()
        self._accept_any_picture()
        client = self.build(storage=store, insert_raises=True)
        response = client.post('/api/parcels/1/photos', data=self.payload())
        self.assertEqual(response.status_code, 500)
        self.assertEqual(store.objects, {})

    def test_a_good_upload_answers_with_the_whole_list(self):
        store = FakeStorage()
        self._accept_any_picture()
        client = self.build(storage=store)
        response = client.post('/api/parcels/1/photos', data=self.payload())
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertIn('photos', body)
        self.assertIn('events', body)
        self.assertEqual(store.kinds(), ['upload', 'upload'])
        # Кэш браузера без этого не работает даже при стабильном адресе.
        self.assertTrue(all(entry[3] for entry in store.log))

    def test_a_broken_photo_id_never_reaches_the_database(self):
        client = self.build(storage=FakeStorage())
        response = client.delete('/api/parcels/1/photos/abc')
        self.assertEqual(response.status_code, 404)

    def test_someone_elses_photo_is_simply_not_found(self):
        store = FakeStorage()
        client = self.build(storage=store)
        original = parcels_queries.delete_photo
        self.addCleanup(setattr, parcels_queries, 'delete_photo', original)
        parcels_queries.delete_photo = lambda _c, _pid, _photo, actor=None: None
        response = client.delete('/api/parcels/1/photos/11111111-2222-4333-8444-555555555555')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['code'], 'PARCEL_PHOTO_NOT_FOUND')
        self.assertEqual(store.log, [])

    def test_dropping_a_photo_removes_the_files(self):
        store = FakeStorage()
        store.objects[('b', 'full')] = b'x'
        store.objects[('b', 'thumb')] = b'y'
        client = self.build(storage=store)
        original = parcels_queries.delete_photo
        self.addCleanup(setattr, parcels_queries, 'delete_photo', original)
        parcels_queries.delete_photo = (
            lambda _c, _pid, _photo, actor=None: [('b', 'full'), ('b', 'thumb')])
        response = client.delete('/api/parcels/1/photos/11111111-2222-4333-8444-555555555555')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(store.objects, {})

    def test_deleting_a_card_survives_a_broken_storage(self):
        """Иначе 500 на успешно удалённой записи: фронт не убрал бы строку, а
        повторная попытка дала бы 404."""
        class Broken(FakeStorage):
            def client(self):
                raise RuntimeError('учётные данные не читаются')

        client = self.build(storage=Broken(),
                            context={'user_id': 1, 'name': 'Админ', 'role': 'admin',
                                     'department_id': None, 'department_code': None,
                                     'city': None, 'headed_department_ids': [],
                                     'headed_department_codes': []})
        response = client.delete('/api/parcels/1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'deleted'})

    def test_reading_a_card_keeps_photos_out_of_the_item(self):
        client = self.build(storage=FakeStorage())
        body = client.get('/api/parcels/1').get_json()
        self.assertIn('photos', body)
        self.assertNotIn('photos', body['item'])
        self.assertNotIn('photo_count', body['item'])

    def test_without_the_table_the_registry_still_works(self):
        """Главный регресс: необязательная возможность не должна ронять раздел."""
        store = FakeStorage()
        client = self.build(storage=store, photos_ready=False)

        card = client.get('/api/parcels/1')
        self.assertEqual(card.status_code, 200)
        self.assertEqual(card.get_json()['photos'], [])

        ping = client.get('/api/parcels/ping').get_json()
        self.assertFalse(ping['photos_ready'])

        self._accept_any_picture()
        upload = client.post('/api/parcels/1/photos', data=self.payload())
        self.assertEqual(upload.status_code, 409)
        self.assertEqual(store.objects, {})

        drop = client.delete('/api/parcels/1/photos/11111111-2222-4333-8444-555555555555')
        self.assertEqual(drop.status_code, 404)

    def test_ping_needs_both_the_table_and_the_bucket(self):
        without_bucket = self.build(storage=None)
        self.assertFalse(without_bucket.get('/api/parcels/ping').get_json()['photos_ready'])
        with_both = self.build(storage=FakeStorage())
        self.assertTrue(with_both.get('/api/parcels/ping').get_json()['photos_ready'])


class GuardTests(unittest.TestCase):
    """Сторожа решений, которые легко нарушить следующей правкой."""

    def test_the_bucket_is_touched_only_from_photos_py(self):
        for path in (ROOT / 'parcels').glob('*.py'):
            code = code_only(path.read_text(encoding='utf-8'))
            self.assertNotIn('wiki.storage', code, path.name)
            self.assertNotIn('store_file', code, path.name)

    def test_the_converter_is_one_for_the_whole_project(self):
        self.assertIn('to_webp', PHOTOS_PY)
        self.assertIn('from wiki import images', PHOTOS_PY)

    def test_signing_is_ours_because_the_monolith_helper_costs_a_client_per_link(self):
        code = code_only(PHOTOS_PY)
        self.assertNotIn('_lms_signed_url', code)
        self.assertNotIn('_lms_delete_blob_refs', code)

    def test_no_abort_in_the_section(self):
        """`except Exception` в декораторе превратил бы 400 и 413 во
        «Внутреннюю ошибку раздела»."""
        self.assertNotIn('abort (', code_only(ROUTES_PY))

    def test_route_handler_names_are_unique(self):
        """Дубль имени роняет bp.route, а сборка блюпринта обёрнута в
        try/except — весь раздел молча отвечал бы 404."""
        import ast
        names = [node.name for node in ast.walk(ast.parse(ROUTES_PY))
                 if isinstance(node, ast.FunctionDef)
                 and any(getattr(d.func, 'id', '') == 'parcels_route'
                         for d in node.decorator_list if isinstance(d, ast.Call))]
        self.assertTrue(names)
        self.assertEqual(len(names), len(set(names)), names)


class LimitsMatchTheFrontTests(unittest.TestCase):
    """Расхождение читалось бы как «форма приняла, а сервер отказал»."""

    def test_count_and_weight(self):
        self.assertIn('PHOTO_MAX_COUNT = %d;' % photos.MAX_PER_PARCEL, FRONT_META)
        self.assertIn('PHOTO_MAX_BYTES = %d * 1024 * 1024;' % (photos.MAX_BYTES // (1024 * 1024)),
                      FRONT_META)

    def test_accepted_formats(self):
        for kind in photos.PHOTO_TYPES:
            self.assertIn("'%s'" % kind, FRONT_META, kind)


class FrontendTests(unittest.TestCase):
    """Решения интерфейса, которые проверяются только чтением исходника."""

    def test_the_dropzone_does_not_borrow_task_styles(self):
        """Цвета классов `tv-*` объявлены только внутри `.tv-root`: снаружи
        рамка сбрасывается целиком, а `--accent` подхватывает глобальный
        зелёный из styles.css."""
        self.assertNotIn('tv-file-dropzone', PHOTOS_JSX)

    def test_capture_is_not_forced(self):
        """`capture` на Android принудительно открывает камеру и убирает выбор
        из галереи — дослать снимок, сделанный раньше, стало бы нельзя."""
        self.assertNotIn('capture=', PHOTOS_JSX)

    def test_drag_leave_does_not_flicker(self):
        """Уход курсора на вложенную плитку браузер тоже считает dragleave."""
        self.assertIn('currentTarget.contains(', PHOTOS_JSX)

    def test_the_lightbox_escapes_the_modal(self):
        block = IOS_JSX.split('export const IosLightbox')[1]
        self.assertIn('createPortal', block)
        self.assertIn('stopImmediatePropagation', block)

    def test_a_broken_address_cannot_start_a_request_loop(self):
        """<img> зовёт onError -> перезапрос карточки -> новое состояние ->
        перерисовка -> тот же onError. Без ограничителя битая ссылка завалила бы
        сервер запросами ровно в том случае, ради которого обработчик и писался."""
        view = (ROOT / 'src' / 'components' / 'parcels' / 'ParcelsView.jsx').read_text(encoding='utf-8')
        self.assertIn('photoRetry', view)
        self.assertIn('photoRetry.current === opened.id', view)

    def test_the_lightbox_does_not_survive_the_card(self):
        """Карточка смонтирована всегда, и состояние переживает её закрытие."""
        card = (ROOT / 'src' / 'components' / 'parcels' / 'ParcelCard.jsx').read_text(encoding='utf-8')
        self.assertIn('if (!open) setZoom(null)', card)

    def test_no_second_way_to_build_a_photo_address(self):
        """Адреса приходят подписанными в ответе карточки; прокси-роута нет."""
        self.assertNotIn('photoUrl', FRONT_META)
        self.assertNotIn('/api/parcels/photo/', FRONT_META)


if __name__ == '__main__':
    unittest.main()
