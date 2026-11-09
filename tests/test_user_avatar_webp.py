# -*- coding: utf-8 -*-
"""Фото профиля хранится в WebP — что бы ни прислал браузер.

Случай с прода (29.10.2026): Safari не кодирует WebP с холста, окно фото
профиля всё равно называет файл «.webp» и подписывает image/webp — в бакет лёг
PNG. Это фото подставляет «Доска почёта». Сервер теперь сам пересобирает фото
в WebP (user_profile/avatar_image.py); здесь — и сам конвертер, и ручка
POST /api/user/avatar целиком, с подставными базой и хранилищем.
"""

import importlib
import inspect
import io
import math
import os
import sys
import types
import unittest
from unittest import mock

from flask import Flask
from PIL import Image

from user_profile import avatar_image


def image_bytes(fmt='PNG', size=(128, 128), mode='RGB', color=(200, 60, 40), **save):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, fmt, **save)
    return buffer.getvalue()


def image_bytes_of(image, fmt='PNG', **save):
    buffer = io.BytesIO()
    image.save(buffer, fmt, **save)
    return buffer.getvalue()


def noisy_png(size=(300, 300)):
    """PNG с шумом: такой не сжимается в пару сотен байт, и обрезка его портит."""
    buffer = io.BytesIO()
    Image.effect_noise(size, 80).convert('RGB').save(buffer, 'PNG')
    return buffer.getvalue()


def is_real_webp(data):
    return data[:4] == b'RIFF' and data[8:12] == b'WEBP'


class AvatarToWebpTests(unittest.TestCase):
    def assert_webp(self, data, size=None, mode=None):
        self.assertTrue(is_real_webp(data), data[:12])
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format, 'WEBP')
            if size is not None:
                self.assertEqual(image.size, size)
            if mode is not None:
                self.assertEqual(image.mode, mode)

    def test_promised_settings(self):
        # q90, потолок 4096² и сторона 1024 — то, что обещано в шапке модуля.
        self.assertEqual((avatar_image.QUALITY, avatar_image.MAX_PIXELS, avatar_image.MAX_SIDE),
                         (90, 4096 * 4096, 1024))

    def test_png_jpeg_gif_webp_and_phone_mpo_all_become_webp(self):
        for fmt in ('PNG', 'JPEG', 'GIF', 'WEBP'):
            with self.subTest(fmt=fmt):
                self.assert_webp(avatar_image.avatar_to_webp(image_bytes(fmt)), (128, 128), 'RGB')
        # MPO — JPEG с телефона с несколькими кадрами; Pillow называет его так.
        frames = [Image.new('RGB', (64, 48), (200, 10, 10)), Image.new('RGB', (64, 48), (10, 200, 10))]
        buffer = io.BytesIO()
        frames[0].save(buffer, 'MPO', save_all=True, append_images=frames[1:])
        self.assert_webp(avatar_image.avatar_to_webp(buffer.getvalue()), (64, 48), 'RGB')

    def test_photo_quality_is_kept(self):
        # Пересборка q90 почти не трогает фото. Замер на этом кадре (Pillow 10.4):
        # q90 — 52,5 дБ PSNR к исходнику, q60 — 43,6, q5 — 40,1; порог 47 их разводит.
        source = Image.linear_gradient('L').resize((128, 128)).convert('RGB')
        buffer = io.BytesIO()
        source.save(buffer, 'PNG')
        with Image.open(io.BytesIO(avatar_image.avatar_to_webp(buffer.getvalue()))) as image:
            rebuilt = image.convert('RGB')
        diff = [(a - b) ** 2 for a, b in zip(source.tobytes(), rebuilt.tobytes())]
        mse = sum(diff) / len(diff)
        self.assertGreater(10 * math.log10(255 ** 2 / max(mse, 1e-9)), 47)

    def test_transparency_is_kept(self):
        def alpha_at_corner(data):
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.mode, 'RGBA')
                return image.getpixel((0, 0))[3]

        cases = {
            'RGBA': image_bytes(mode='RGBA', color=(10, 20, 30, 0)),
            'серый с каналом (LA)': image_bytes(mode='LA', color=(100, 0)),
        }
        # У палитры, серого и цветного PNG с блоком tRNS прозрачность — цвет, а не канал.
        palette = Image.new('P', (64, 64), 0)
        palette.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
        cases['палитра'] = image_bytes_of(palette, transparency=0)
        cases['серый с tRNS'] = image_bytes_of(Image.new('L', (64, 64), 0), transparency=0)
        cases['цветной с tRNS'] = image_bytes_of(Image.new('RGB', (64, 64), (0, 0, 0)), transparency=(0, 0, 0))
        for name, data in cases.items():
            with self.subTest(name):
                self.assertEqual(alpha_at_corner(avatar_image.avatar_to_webp(data)), 0)

    def test_16_bit_grey_png_keeps_its_brightness(self):
        # Не белый квадрат: convert('RGB') обрезал бы 40000 из 65535 до 255.
        data = avatar_image.avatar_to_webp(image_bytes(mode='I;16', size=(64, 64), color=40000))
        self.assert_webp(data, size=(64, 64))
        with Image.open(io.BytesIO(data)) as image:
            red, green, blue = image.convert('RGB').getpixel((32, 32))
        self.assertTrue(150 <= red <= 160 and red == green == blue, (red, green, blue))

    def test_exif_turn_applied_and_metadata_dropped(self):
        exif = Image.Exif()
        exif[0x0112] = 6        # «повернуть на 90°» — так снимает телефон
        exif[0x0110] = 'Телефон владельца'
        data = avatar_image.avatar_to_webp(image_bytes('JPEG', size=(200, 100), exif=exif.tobytes()))
        self.assert_webp(data, size=(100, 200))
        with Image.open(io.BytesIO(data)) as image:
            self.assertNotIn('exif', image.info)
            self.assertEqual(dict(image.getexif()), {})

    def test_even_real_webp_is_rebuilt(self):
        # Настоящий WebP тоже пересобирается: иначе EXIF и лишние пиксели,
        # присланные мимо окна, легли бы в бакет как есть.
        exif = Image.Exif()
        exif[0x0110] = 'Телефон владельца'
        source = image_bytes('WEBP', size=(2000, 1000), exif=exif.tobytes())
        with Image.open(io.BytesIO(source)) as image:
            self.assertIn('exif', image.info)
        data = avatar_image.avatar_to_webp(source)
        self.assert_webp(data, size=(avatar_image.MAX_SIDE, avatar_image.MAX_SIDE // 2))
        with Image.open(io.BytesIO(data)) as image:
            self.assertNotIn('exif', image.info)

    def test_big_photo_is_cut_to_max_side(self):
        data = avatar_image.avatar_to_webp(image_bytes('JPEG', size=(3000, 1500)))
        self.assert_webp(data, size=(avatar_image.MAX_SIDE, avatar_image.MAX_SIDE // 2))

    def test_refusals(self):
        cases = {
            'пусто': (b'', 'Файл пустой'),
            'не картинка': (b'not an image at all', 'Не удалось прочитать фото — подойдёт'),
            'svg': (b'<svg xmlns="http://www.w3.org/2000/svg"/>', 'Не удалось прочитать фото — подойдёт'),
            'bmp': (image_bytes('BMP'), 'Не удалось прочитать фото — подойдёт'),
            'обрезанный png': (noisy_png()[:4000], 'файл повреждён'),
        }
        for name, (data, message) in cases.items():
            with self.subTest(name):
                with self.assertRaises(avatar_image.AvatarImageError) as caught:
                    avatar_image.avatar_to_webp(data)
                self.assertIn(message, str(caught.exception))

    def test_pixel_bomb_refused_before_decoding(self):
        with mock.patch.object(avatar_image, 'MAX_PIXELS', 128 * 128 - 1):
            with self.assertRaises(avatar_image.AvatarImageError) as caught:
                avatar_image.avatar_to_webp(image_bytes())
        self.assertIn('слишком большое', str(caught.exception))
        self.assert_webp(avatar_image.avatar_to_webp(image_bytes()))
        # Сотни мегапикселей Pillow отбивает ещё при открытии — текст тот же.
        with mock.patch.object(Image, 'MAX_IMAGE_PIXELS', 1000):
            with self.assertRaises(avatar_image.AvatarImageError) as caught:
                avatar_image.avatar_to_webp(image_bytes())
        self.assertIn('слишком большое', str(caught.exception))


# ── Ручка POST /api/user/avatar ─────────────────────────────────────────────

def load_routes():
    """user_profile.routes без настоящей базы: `from database import db` при
    импорте подключается к Postgres, поэтому модуль грузится с заглушкой."""
    stub = types.ModuleType('database')
    stub.db = mock.MagicMock()
    sys.modules.pop('user_profile.routes', None)
    with mock.patch.dict(sys.modules, {'database': stub}):
        return importlib.import_module('user_profile.routes')


routes = load_routes()


class FakeBlob:
    def __init__(self, store, path):
        self.store, self.path, self.cache_control = store, path, None

    def upload_from_string(self, data, content_type=None):
        self.store[self.path] = (data, content_type, self.cache_control)

    def delete(self):
        self.store.pop(self.path, None)


class FakeStorage:
    def __init__(self):
        self.blobs = {}

    def bucket(self, _name):
        return self

    def blob(self, path):
        return FakeBlob(self.blobs, path)


class FakeDb:
    def __init__(self):
        self.saved = None

    def get_user(self, id=None):
        # (id, telegram_id, name, role, direction, hire_date, supervisor_id, ...)
        return (int(id), None, 'Сотрудник', 'operator', None, None, None, None, None, None, None, 'working')

    def get_user_avatar_storage(self, _user_id):
        return {}

    def set_user_avatar(self, **kwargs):
        self.saved = kwargs
        return {'avatar_bucket': kwargs['bucket_name'], 'avatar_blob_path': kwargs['blob_path'],
                'avatar_updated_at': '2026-11-09T12:00:00', 'avatar_file_size': kwargs['file_size']}


class AvatarUploadRouteTests(unittest.TestCase):
    def setUp(self):
        self.storage = FakeStorage()
        self.db = FakeDb()
        deps = {name: mock.MagicMock(name=name)
                for name in inspect.signature(routes.build_user_profile_blueprint).parameters}
        deps.update(
            require_api_key=lambda handler: handler,
            get_gcs_client=lambda: self.storage,
            _build_avatar_signed_url=lambda bucket, path: f'https://photos.example/{path}',
            _normalize_user_role=lambda role: str(role or '').lower(),
        )
        app = Flask(__name__)
        app.register_blueprint(routes.build_user_profile_blueprint(**deps))
        self.client = app.test_client()
        # Папку и суффикс routes читает из окружения при импорте — фиксируем,
        # чтобы тест не зависел от переменных оболочки (стенд, Render).
        for patcher in (mock.patch.object(routes, 'db', self.db),
                        mock.patch.object(routes, 'AVATAR_UPLOAD_FOLDER', 'AvatarUploads/'),
                        mock.patch.object(routes, 'AVATAR_THUMBNAIL_SUFFIX', '128'),
                        mock.patch.dict(os.environ, {'GOOGLE_CLOUD_STORAGE_BUCKET_AVATARS': 'stand'})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def upload(self, avatar, original=None):
        data = {'avatar': (io.BytesIO(avatar[0]), avatar[1], avatar[2])}
        if original:
            data['avatar_original'] = (io.BytesIO(original[0]), original[1], original[2])
        return self.client.post('/api/user/avatar', data=data, headers={'X-User-Id': '5'},
                                content_type='multipart/form-data')

    def stored(self, suffix):
        [(path, value)] = [(path, value) for path, value in self.storage.blobs.items() if suffix in path]
        return path, value

    def test_safari_png_named_webp_is_stored_as_real_webp(self):
        png = image_bytes('PNG')
        response = self.upload((png, 'avatar.webp', 'image/webp'))
        self.assertEqual(response.status_code, 200, response.get_json())
        path, (data, content_type, cache) = self.stored('_128')
        self.assertTrue(path.startswith('AvatarUploads/avatars/5/'), path)
        self.assertTrue(path.endswith('_128.webp'), path)
        self.assertEqual(content_type, 'image/webp')
        self.assertIn('immutable', cache)
        self.assertTrue(is_real_webp(data), data[:12])
        self.assertNotEqual(data, png)
        # Без отдельного исходника исходником остаётся то же фото — тоже WebP.
        original_path, (original, original_type, _cache) = self.stored('_original')
        self.assertTrue(original_path.endswith('_original.webp'), original_path)
        self.assertEqual((original, original_type), (data, 'image/webp'))
        self.assertEqual((self.db.saved['content_type'], self.db.saved['file_size'], self.db.saved['blob_path']),
                         ('image/webp', len(data), path))
        self.assertEqual(response.get_json()['avatar_file_size'], len(data))

    def test_name_and_label_do_not_matter(self):
        for name, label, fmt in (('photo.png', 'image/png', 'PNG'), ('x.jpg', 'image/jpeg', 'JPEG'),
                                 ('avatar.webp', 'image/webp', 'WEBP'), ('avatar', 'image/png', 'JPEG')):
            with self.subTest(name=name, fmt=fmt):
                self.storage.blobs.clear()
                self.db.saved = None
                response = self.upload((image_bytes(fmt), name, label))
                self.assertEqual(response.status_code, 200, response.get_json())
                path, (data, content_type, _cache) = self.stored('_128')
                self.assertTrue(path.endswith('_128.webp'), path)
                self.assertEqual(content_type, 'image/webp')
                self.assertTrue(is_real_webp(data), data[:12])
                # Тип в базе — тоже WebP, а не подпись браузера (его сверяли при переносе).
                self.assertEqual((self.db.saved['content_type'], self.db.saved['file_size']),
                                 ('image/webp', len(data)))
                original_path, (original, _type, _cache) = self.stored('_original')
                self.assertTrue(original_path.endswith('_original.webp'), original_path)
                self.assertEqual(original, data)

    def test_real_webp_sent_past_the_window_is_rebuilt_too(self):
        # Настоящий WebP с EXIF и в 2000 px (не из окна) ручка тоже пересобирает:
        # пропуск «уже WebP» по подписи или по первым байтам оставил бы его как есть.
        exif = Image.Exif()
        exif[0x0110] = 'Телефон владельца'
        source = image_bytes('WEBP', size=(2000, 1000), exif=exif.tobytes())
        response = self.upload((source, 'avatar.webp', 'image/webp'))
        self.assertEqual(response.status_code, 200, response.get_json())
        _path, (data, _type, _cache) = self.stored('_128')
        self.assertNotEqual(data, source)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual((image.format, image.size), ('WEBP', (1024, 512)))
            self.assertNotIn('exif', image.info)

    def test_separate_original_is_kept_as_uploaded(self):
        original = image_bytes('JPEG', size=(800, 600))
        response = self.upload((image_bytes('PNG'), 'avatar.webp', 'image/webp'),
                               (original, 'IMG_0001.jpg', 'image/jpeg'))
        self.assertEqual(response.status_code, 200, response.get_json())
        path, (data, _type, _cache) = self.stored('_128')
        self.assertTrue(is_real_webp(data))
        original_path, (stored, stored_type, _cache) = self.stored('_original')
        self.assertTrue(original_path.endswith('_original.jpg'), original_path)
        self.assertEqual((stored, stored_type), (original, 'image/jpeg'))

    def test_not_an_image_is_refused_and_nothing_is_stored(self):
        for payload in (b'not an image', b'<svg xmlns="http://www.w3.org/2000/svg"/>'):
            with self.subTest(payload=payload[:10]):
                response = self.upload((payload, 'avatar.webp', 'image/webp'))
                self.assertEqual(response.status_code, 400)
                self.assertIn('Не удалось прочитать фото', response.get_json()['error'])
                self.assertEqual(self.storage.blobs, {})
                self.assertIsNone(self.db.saved)


if __name__ == '__main__':
    unittest.main()
