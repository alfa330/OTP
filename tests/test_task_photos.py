# -*- coding: utf-8 -*-
"""Фотографии во вложениях задач — то, что ломается молча.

Проверяется не «работает ли загрузка вообще», а решения, потеря которых на
экране не видна сразу:

  * картинка ложится в бакет WebP'ом ВСЕГДА, даже когда WebP вышел тяжелее
    исходника (правило владельца; у вики правило обратное);
  * всё, что не перевелось, уходит как есть — вложение важнее плитки;
  * миниатюра грузится рядом, попадает в запись и в уборку при сбое, а её
    осечка не отменяет загрузку;
  * удаление задачи убирает из бакета и миниатюру;
  * адреса картинок отдаются одним запросом с той же проверкой доступа, что у
    скачивания файла, а выданная ссылка живёт дольше, чем фронт держит ответ;
  * .webp уходит в Telegram документом, а не стикером.

Базы и сети здесь нет: курсор подменён, хранилище — двойник.
"""

import ast
import copy
import io
import logging
import os
import re
import sys
import textwrap
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

import task_photos  # noqa: E402
from tests import source_cache  # noqa: E402
from wiki import images as wiki_images  # noqa: E402

BOT_PATH = ROOT / 'bot_schedule2.py'
DATABASE_PATH = ROOT / 'database.py'
BOT_PY = source_cache.read(BOT_PATH)
DATABASE_PY = source_cache.read(DATABASE_PATH)
TASKS_DIR = ROOT / 'src' / 'components' / 'tasks'
VIEW = (TASKS_DIR / 'TasksView.jsx').read_text(encoding='utf-8')
FRONT = (TASKS_DIR / 'taskPhotos.js').read_text(encoding='utf-8')
HOOK = (TASKS_DIR / 'useTaskPhotoPreviews.js').read_text(encoding='utf-8')
MOBILE_CSS = (TASKS_DIR / 'tasks-mobile.css').read_text(encoding='utf-8')
IOS_JSX = (ROOT / 'src' / 'components' / 'ui' / 'ios.jsx').read_text(encoding='utf-8')


def _image_bytes(fmt, size=(900, 600), mode='RGB', **options):
    color = (40, 120, 200, 128) if mode == 'RGBA' else (40, 120, 200)
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format=fmt, **options)
    return out.getvalue()


def _noisy_jpeg():
    """Пережатый шумный JPEG: WebP на нём выходит тяжелее исходника."""
    out = io.BytesIO()
    Image.effect_noise((128, 128), 90).convert('RGB').save(out, format='JPEG', quality=10)
    return out.getvalue()


def _bot_function(name, namespace):
    node = source_cache.function_copy(BOT_PATH, name)
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, str(BOT_PATH), 'exec'), namespace)
    return namespace[name]


def _database_method(name):
    source = DATABASE_PY
    node = source_cache.function_node(DATABASE_PATH, name, class_name='Database')
    lines = source.splitlines(keepends=True)
    code = textwrap.dedent(''.join(lines[node.lineno - 1:node.end_lineno]))
    namespace = {'normalize_role_value': lambda role: str(role or '').strip().lower()}
    exec(code, namespace)
    return namespace[name]


@unittest.skipIf(Image is None, 'Pillow не установлен')
class PrepareTest(unittest.TestCase):
    def test_jpeg_becomes_webp_with_a_readable_name(self):
        result = task_photos.prepare(_image_bytes('JPEG'), filename='IMG_1234.JPG',
                                     content_type='image/jpeg')
        self.assertEqual(result['content_type'], 'image/webp')
        self.assertEqual(result['file_name'], 'IMG_1234.webp')
        with Image.open(io.BytesIO(result['data'])) as img:
            self.assertEqual(img.format, 'WEBP')

    def test_webp_even_when_it_is_heavier_than_the_source(self):
        """Правило владельца — формат, а не вес. У вики на том же файле JPEG."""
        source = _noisy_jpeg()
        self.assertEqual(wiki_images.to_webp(source, 'image/jpeg')[1], 'image/jpeg')
        result = task_photos.prepare(source, filename='tg.jpg', content_type='image/jpeg')
        self.assertEqual(result['content_type'], 'image/webp')
        self.assertGreater(len(result['data']), len(source))

    def test_the_wiki_rule_itself_is_untouched(self):
        """Флаг по умолчанию выключен: вики и «Посылки» живут как жили."""
        source = _noisy_jpeg()
        self.assertEqual(wiki_images.to_webp(source, 'image/jpeg'),
                         (source, 'image/jpeg', 128, 128))
        self.assertEqual(wiki_images.to_webp(source, 'image/jpeg', keep_smaller=False)[1],
                         'image/webp')

    def test_cyrillic_name_does_not_turn_into_jpg_webp(self):
        """secure_filename сделал бы из «фото.jpg» имя «jpg», а из него «jpg.webp»."""
        result = task_photos.prepare(_image_bytes('PNG'), filename='фото.png',
                                     content_type='image/png')
        self.assertEqual(result['file_name'], 'photo.webp')
        self.assertEqual(task_photos.webp_file_name('C:\\Users\\a\\скрин 1.png'), '1.webp')
        self.assertEqual(task_photos.webp_file_name('...'), 'photo.webp')

    def test_transparency_survives(self):
        result = task_photos.prepare(_image_bytes('PNG', mode='RGBA'), filename='a.png',
                                     content_type='image/png')
        with Image.open(io.BytesIO(result['data'])) as img:
            self.assertIn('A', img.getbands())

    def test_octet_stream_is_recognised_by_extension(self):
        """CLI и часть программ не называют тип — снимок всё равно в WebP."""
        result = task_photos.prepare(_image_bytes('JPEG'), filename='scan.jpeg',
                                     content_type='application/octet-stream')
        self.assertEqual(result['content_type'], 'image/webp')
        self.assertIsNone(task_photos.prepare(b'PK\x03\x04', filename='a.docx',
                                              content_type='application/octet-stream'))

    def test_what_did_not_convert_goes_as_is(self):
        self.assertIsNone(task_photos.prepare(b'%PDF-1.4', filename='a.pdf',
                                              content_type='application/pdf'))
        self.assertIsNone(task_photos.prepare(b'%PDF-1.4', filename='a.jpg',
                                              content_type='image/jpeg'))
        self.assertIsNone(task_photos.prepare(b'<svg xmlns="http://www.w3.org/2000/svg"/>',
                                              filename='a.svg', content_type='image/svg+xml'))
        self.assertIsNone(task_photos.prepare(b'', filename='a.png', content_type='image/png'))

    def test_large_frame_gets_a_thumbnail_small_one_does_not(self):
        big = task_photos.prepare(_image_bytes('JPEG', size=(2000, 1000)), filename='a.jpg',
                                  content_type='image/jpeg')
        with Image.open(io.BytesIO(big['thumb'])) as thumb:
            self.assertEqual(thumb.format, 'WEBP')
            self.assertEqual(thumb.size, (480, 480), 'квадрат — ровно то, что показывает плитка')
        small = task_photos.prepare(_image_bytes('PNG', size=(300, 200)), filename='a.png',
                                    content_type='image/png')
        self.assertIsNone(small['thumb'])

    def test_documents_in_image_formats_are_not_flattened(self):
        """Исходник после перевода не хранится — значит, переводим только то,
        что ничего не теряет: многостраничный скан, PSD и схема draw.io уходят
        как есть."""
        from PIL import PngImagePlugin
        pages = io.BytesIO()
        Image.new('RGB', (400, 300)).save(pages, format='TIFF', save_all=True,
                                          append_images=[Image.new('RGB', (400, 300))] * 2)
        for content_type in ('image/tiff', 'application/octet-stream'):
            self.assertIsNone(task_photos.prepare(pages.getvalue(), filename='scan.tif',
                                                  content_type=content_type))
        self.assertIsNone(task_photos.prepare(b'8BPS', filename='a.psd',
                                              content_type='image/vnd.adobe.photoshop'))
        info = PngImagePlugin.PngInfo()
        info.add_text('mxfile', '%3Cmxfile%3E')
        diagram = io.BytesIO()
        Image.new('RGB', (400, 300)).save(diagram, format='PNG', pnginfo=info)
        self.assertIsNone(task_photos.prepare(diagram.getvalue(), filename='flow.png',
                                              content_type='image/png'))
        self.assertIsNone(task_photos.prepare(_image_bytes('PNG'), filename='flow.drawio.png',
                                              content_type='image/png'))

    def test_long_screenshot_keeps_its_resolution(self):
        """Потолок вики (2560) сделан под колонку статьи; вложение задачи
        скачивают, и 1170×9000 не должен стать нечитаемой полоской 333×2560."""
        result = task_photos.prepare(_image_bytes('PNG', size=(1170, 9000)), filename='long.png',
                                     content_type='image/png')
        with Image.open(io.BytesIO(result['data'])) as img:
            self.assertEqual(img.size, (1170, 9000))
        with Image.open(io.BytesIO(result['thumb'])) as thumb:
            self.assertEqual(thumb.size, (480, 480))
        self.assertEqual(wiki_images.to_webp(_image_bytes('PNG', size=(1170, 9000)), 'image/png')[2:],
                         (333, 2560), 'у вики потолок прежний')

    def test_thumbnail_sits_next_to_the_frame(self):
        self.assertEqual(task_photos.thumb_blob_path('T/tasks/initial/2026/09/24/ab_x.webp'),
                         'T/tasks/initial/2026/09/24/ab_x_thumb.webp')


class _Blob:
    def __init__(self, bucket, path):
        self.bucket, self.path = bucket, path

    def upload_from_string(self, data, content_type=None):
        if self.bucket.fail_on and self.bucket.fail_on in self.path:
            raise RuntimeError('storage down')
        self.bucket.uploaded[self.path] = (data, content_type)

    def delete(self):
        self.bucket.deleted.append(self.path)

    def generate_signed_url(self, **kwargs):
        self.bucket.signed.append((self.path, kwargs))
        return f'https://signed/{self.path}?n={len(self.bucket.signed)}'


class _Bucket:
    def __init__(self, fail_on=None):
        self.uploaded, self.deleted, self.signed = {}, [], []
        self.fail_on = fail_on

    def blob(self, path):
        return _Blob(self, path)


class _Client:
    def __init__(self, bucket):
        self._bucket = bucket

    def bucket(self, _name):
        return self._bucket


class _Upload:
    def __init__(self, filename, data, mimetype):
        self.filename, self._data, self.mimetype = filename, data, mimetype

    def read(self):
        return self._data


@unittest.skipIf(Image is None, 'Pillow не установлен')
class UploadTest(unittest.TestCase):
    """Настоящая _upload_task_attachments_to_gcs из монолита, бакет — двойник."""

    def setUp(self):
        self.bucket = _Bucket()
        os.environ['GOOGLE_CLOUD_STORAGE_BUCKET_TASKS'] = 'tasks-bucket'
        self.addCleanup(os.environ.pop, 'GOOGLE_CLOUD_STORAGE_BUCKET_TASKS', None)
        self.upload = _bot_function('_upload_task_attachments_to_gcs', {
            'os': os, 'uuid': uuid, 'datetime': datetime, 'logging': logging,
            'TASK_MAX_FILES': 10, 'TASK_MAX_FILE_SIZE_BYTES': 10 * 1024 * 1024,
            'TASK_ATTACHMENTS_UPLOAD_FOLDER': 'TaskAttachments/',
            'get_gcs_client': lambda: _Client(self.bucket),
            'secure_filename': lambda name: re.sub(r'[^A-Za-z0-9._-]+', '', name).strip('.'),
        })

    def test_photo_goes_as_webp_with_a_thumbnail(self):
        attachments, paths, _bucket = self.upload(
            [_Upload('фото.jpg', _image_bytes('JPEG', size=(1600, 1200)), 'image/jpeg')])
        [item] = attachments
        self.assertEqual(item['content_type'], 'image/webp')
        self.assertEqual(item['file_name'], 'photo.webp')
        self.assertTrue(item['gcs_blob_path'].endswith('_photo.webp'))
        self.assertEqual(item['thumb_blob_path'], task_photos.thumb_blob_path(item['gcs_blob_path']))
        data, content_type = self.bucket.uploaded[item['gcs_blob_path']]
        self.assertEqual(content_type, 'image/webp')
        self.assertEqual(item['file_size'], len(data))
        self.assertEqual(self.bucket.uploaded[item['thumb_blob_path']][1], 'image/webp')
        # Миниатюра — в списке уборки: при сбое записи в базу она не осиротеет.
        self.assertEqual(paths, [item['gcs_blob_path'], item['thumb_blob_path']])

    def test_document_is_untouched(self):
        attachments, paths, _bucket = self.upload(
            [_Upload('ТЗ.docx', b'PK\x03\x04 docx', 'application/vnd.openxmlformats')])
        [item] = attachments
        self.assertEqual(item['content_type'], 'application/vnd.openxmlformats')
        self.assertIsNone(item['thumb_blob_path'])
        self.assertEqual(self.bucket.uploaded[item['gcs_blob_path']][0], b'PK\x03\x04 docx')
        self.assertEqual(len(paths), 1)

    def test_thumbnail_failure_does_not_cancel_the_upload(self):
        self.bucket.fail_on = '_thumb.webp'
        attachments, paths, _bucket = self.upload(
            [_Upload('a.jpg', _image_bytes('JPEG', size=(1600, 1200)), 'image/jpeg')])
        self.assertIsNone(attachments[0]['thumb_blob_path'])
        self.assertEqual(paths, [attachments[0]['gcs_blob_path']])

    def test_converter_crash_stores_the_original(self):
        original = task_photos.prepare
        task_photos.prepare = MagicMock(side_effect=RuntimeError('codec'))
        self.addCleanup(setattr, task_photos, 'prepare', original)
        source = _image_bytes('JPEG')
        with self.assertLogs(level='ERROR'):
            attachments, _paths, _bucket = self.upload([_Upload('a.jpg', source, 'image/jpeg')])
        self.assertEqual(attachments[0]['content_type'], 'image/jpeg')
        self.assertEqual(self.bucket.uploaded[attachments[0]['gcs_blob_path']][0], source)

    def test_size_limit_is_checked_on_the_original(self):
        with self.assertRaises(ValueError):
            self.upload([_Upload('a.jpg', b'x' * (10 * 1024 * 1024 + 1), 'image/jpeg')])


class DeleteBlobsTest(unittest.TestCase):
    def test_task_deletion_removes_the_thumbnail_too(self):
        bucket = _Bucket()
        delete = _bot_function('_delete_task_attachment_blobs_from_gcs', {
            'logging': logging, 'get_gcs_client': lambda: _Client(bucket),
        })
        warnings = delete([
            {'storage_type': 'gcs', 'gcs_bucket': 'b', 'gcs_blob_path': 'p/a.webp',
             'thumb_blob_path': 'p/a_thumb.webp', 'file_name': 'a.webp'},
            {'storage_type': 'gcs', 'gcs_bucket': 'b', 'gcs_blob_path': 'p/doc.pdf',
             'thumb_blob_path': None, 'file_name': 'doc.pdf'},
        ])
        self.assertEqual(warnings, [])
        self.assertEqual(bucket.deleted, ['p/a.webp', 'p/a_thumb.webp', 'p/doc.pdf'])

    def test_delete_query_brings_the_thumbnail_path(self):
        method = DATABASE_PY.split('    def delete_task(', 1)[1].split('\n    def ', 1)[0]
        self.assertIn('thumb_blob_path', method)
        self.assertIn('"thumb_blob_path": row[6]', method)


class SignPreviewsTest(unittest.TestCase):
    def setUp(self):
        task_photos._SIGNED.clear()
        self.addCleanup(task_photos._SIGNED.clear)
        self.bucket = _Bucket()
        self.builds = 0

    def client(self):
        self.builds += 1
        return _Client(self.bucket)

    def test_thumbnail_falls_back_to_the_frame_and_paths_stay_private(self):
        rows = [
            {'id': 1, 'content_type': 'image/webp', 'gcs_bucket': 'b',
             'gcs_blob_path': 'p/1.webp', 'thumb_blob_path': 'p/1_thumb.webp'},
            {'id': 2, 'content_type': 'image/jpeg', 'gcs_bucket': 'b',
             'gcs_blob_path': 'p/2.jpg', 'thumb_blob_path': None},
        ]
        out = task_photos.sign_previews(self.client, rows)
        self.assertEqual([item['id'] for item in out], [1, 2])
        self.assertIn('p/1_thumb.webp', out[0]['thumb_url'])
        self.assertEqual(out[1]['thumb_url'], out[1]['url'])
        self.assertEqual(set(out[0]), {'id', 'url', 'thumb_url'})
        path, kwargs = self.bucket.signed[0]
        self.assertEqual(kwargs['response_disposition'], 'inline')
        self.assertEqual(kwargs['method'], 'GET')

    def test_one_client_per_call_and_stable_addresses(self):
        rows = [{'id': i, 'content_type': 'image/webp', 'gcs_bucket': 'b',
                 'gcs_blob_path': f'p/{i}.webp', 'thumb_blob_path': f'p/{i}_t.webp'}
                for i in range(1, 6)]
        first = task_photos.sign_previews(self.client, rows)
        self.assertEqual(self.builds, 1)
        second = task_photos.sign_previews(self.client, rows)
        self.assertEqual(first, second)
        self.assertEqual(self.builds, 1, 'всё уже в кэше — клиент не нужен вовсе')

    def test_unsigned_row_is_left_out_instead_of_an_empty_tile(self):
        def broken_client():
            raise RuntimeError('no private key')
        rows = [{'id': 1, 'content_type': 'image/png', 'gcs_bucket': 'b',
                 'gcs_blob_path': 'p/1.png', 'thumb_blob_path': None}]
        with self.assertLogs(level='WARNING'):
            self.assertEqual(task_photos.sign_previews(broken_client, rows), [])

    def test_issued_link_outlives_the_frontend_cache(self):
        """Кэш подписей отдаёт ссылку, пока ей жить больше RESIGN_BEFORE; фронт
        держит ответ PHOTO_PREVIEW_TTL_MS. Иначе повторно открытая карточка
        брала бы из памяти мёртвые адреса."""
        match = re.search(r'PHOTO_PREVIEW_TTL_MS\s*=\s*(\d+)\s*\*\s*60\s*\*\s*1000', FRONT)
        self.assertIsNotNone(match)
        frontend_minutes = int(match.group(1))
        self.assertLess(task_photos.RESIGN_BEFORE_MINUTES, task_photos.SIGNED_MINUTES)
        self.assertGreater(task_photos.RESIGN_BEFORE_MINUTES, frontend_minutes)

    def test_preview_types_match_the_frontend(self):
        match = re.search(r'PHOTO_PREVIEW_TYPES\s*=\s*\[([^\]]*)\]', FRONT)
        front_types = tuple(re.findall(r"'([^']+)'", match.group(1)))
        self.assertEqual(front_types, task_photos.PREVIEW_TYPES)


class _Cursor:
    def __init__(self, task_row, rows):
        self.task_row, self.rows = task_row, rows
        self.queries = []
        self._one = None

    def execute(self, query, params=None):
        self.queries.append((' '.join(query.split()), params))
        self._one = self.task_row if 'FROM tasks WHERE id' in query else None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self.rows


class _Ctx:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *exc):
        return False


class PhotoQueryTest(unittest.TestCase):
    """Настоящий метод базы — доступ ровно как у скачивания одного файла."""

    def setUp(self):
        self.method = _database_method('list_task_photo_attachments_for_requester')

    def run_query(self, *, visible, colleague=False, task_row=(10, None), rows=()):
        cursor = _Cursor(task_row, list(rows))
        fake = MagicMock()
        fake._get_cursor = lambda: _Ctx(cursor)
        fake._task_assignee_scope_tx = lambda _cur, _task_id: []
        fake._task_visible_for_requester = lambda *args: visible
        fake._task_observable_by_colleague_tx = lambda *args: colleague
        result = self.method(fake, 5, 20, 'sv', task_photos.PREVIEW_TYPES)
        return result, cursor

    def test_stranger_is_refused(self):
        with self.assertRaises(PermissionError):
            self.run_query(visible=False)

    def test_colleague_sv_reads_like_the_card(self):
        result, _cursor = self.run_query(visible=False, colleague=True)
        self.assertEqual(result, [])

    def test_missing_task_is_none(self):
        result, _cursor = self.run_query(visible=True, task_row=None)
        self.assertIsNone(result)

    def test_rows_and_filter(self):
        result, cursor = self.run_query(visible=True, rows=[
            (7, 'image/webp', 'b', 'p/7.webp', 'p/7_thumb.webp'),
        ])
        self.assertEqual(result, [{'id': 7, 'content_type': 'image/webp', 'gcs_bucket': 'b',
                                   'gcs_blob_path': 'p/7.webp', 'thumb_blob_path': 'p/7_thumb.webp'}])
        query, params = cursor.queries[-1]
        self.assertIn("COALESCE(storage_type, 'db') = 'gcs'", query)
        self.assertIn('split_part', query)
        self.assertEqual(params, (5, list(task_photos.PREVIEW_TYPES)))


class WiringTest(unittest.TestCase):
    def test_every_insert_keeps_the_thumbnail(self):
        inserts = re.findall(r'INSERT INTO task_attachments \((.*?)\)', DATABASE_PY, flags=re.S)
        self.assertEqual(len(inserts), 3)
        for columns in inserts:
            self.assertIn('thumb_blob_path', columns)

    def test_schema_adds_the_column_idempotently(self):
        self.assertIn('ALTER TABLE task_attachments ADD COLUMN IF NOT EXISTS thumb_blob_path TEXT',
                      DATABASE_PY)

    def test_route_uses_the_task_guard(self):
        route = BOT_PY.split("@app.route('/api/tasks/<int:task_id>/photos'", 1)[1][:1800]
        self.assertIn('@require_api_key', route)
        self.assertIn('_task_route_guard()', route)
        self.assertIn('list_task_photo_attachments_for_requester', route)
        self.assertIn('sign_previews(get_gcs_client', route)

    def test_webp_is_not_turned_into_a_sticker(self):
        sender = BOT_PY.split('def _send_task_completion_attachments_to_telegram', 1)[1][:3500]
        self.assertIn("data['disable_content_type_detection'] = 'true'", sender)


class FrontendTest(unittest.TestCase):
    def test_every_file_block_of_the_card_shows_photos(self):
        """Постановка, результат и уточнения — все три места карточки."""
        self.assertEqual(VIEW.count('<TaskFileGroup'), 3)
        self.assertIn('fileBtnStyle={RESULT_FILE_BTN_STYLE}', VIEW)

    def test_shared_lightbox_not_a_private_copy(self):
        self.assertIn("import { IosLightbox } from '../ui/ios';", VIEW)
        self.assertNotIn('function ImageLightbox', VIEW)

    def test_one_request_per_card_and_no_proxy(self):
        self.assertEqual(VIEW.count('/photos`'), 1)
        self.assertNotIn("responseType: 'blob'", VIEW.split('const loadTaskPhotos', 1)[1][:400])

    def test_load_is_held_in_a_ref(self):
        """Иначе новая функция из раздела на каждом рендере снова шла бы на сервер."""
        self.assertIn('loadRef.current = load', HOOK)
        effect_deps = HOOK.split('return () => { cancelled = true; };', 1)[1][:80]
        self.assertNotIn('load', effect_deps.replace('reloadTick', ''))

    def test_expired_link_is_retried_once(self):
        self.assertIn('retriedRef.current === key', HOOK)
        self.assertIn('onError={photoState.refresh}', VIEW)

    def test_added_photo_does_not_blank_the_shown_ones(self):
        """Дослали файл в открытую карточку — показанные плитки и открытый
        просмотр не мигают: адреса той же задачи держатся до ответа."""
        self.assertIn('previews: prev.taskId === taskId ? prev.previews : {}', HOOK)
        self.assertIn('previews: current || sameTask ? state.previews : {}', HOOK)

    def test_lightbox_takes_focus_and_gives_it_back(self):
        """Иначе пробел «нажимал» плитку под затемнением и перекидывал на первый снимок."""
        block = IOS_JSX.split('export const IosLightbox')[1].split('\nexport ', 1)[0]
        self.assertIn('rootRef.current?.focus({ preventScroll: true })', block)
        self.assertIn('opener.focus({ preventScroll: true })', block)
        self.assertIn("event.key === 'Tab'", block)
        self.assertIn('}, [open]);', block)

    def test_phone_bubble_with_photos_has_its_width_upfront(self):
        self.assertIn("hasPhotos ? 'has-photos' : ''", VIEW)
        self.assertIn('.tv-drawer-screen .tv-clar-bubble.has-photos', MOBILE_CSS)

    def test_phone_grid_is_locked_to_the_phone(self):
        self.assertIn('body.mobile-shell .tv-root .tv-drawer-screen .tv-photo-grid', MOBILE_CSS)
        self.assertIn('aspect-ratio: 1 / 1', MOBILE_CSS)

    def test_lightbox_keeps_its_old_contract(self):
        """Новости и «Посылки» зовут просмотр с тремя свойствами — он обязан
        остаться прежним без новых."""
        block = IOS_JSX.split('export const IosLightbox')[1].split('\nexport ', 1)[0]
        self.assertIn('createPortal', block)
        self.assertIn('stopImmediatePropagation', block)
        self.assertIn("paged || onDownload ? 'max-h-[calc(100dvh-144px)]' : 'max-h-[86vh]'", block)
        self.assertIn('useScreenBackGesture(isNarrow && open, onClose)', block)
        self.assertIn('disabled={!onPrev}', block)


if __name__ == '__main__':
    unittest.main()
