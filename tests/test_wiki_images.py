# -*- coding: utf-8 -*-
"""Картинка в вики: размер в статье и WebP в хранилище.

Две правки, у которых разные точки отказа, но одна общая беда: и та и другая
ломаются МОЛЧА. Ни автор, ни читатель не увидят ошибки — увидят «размер
сбросился» и «файл стал тяжелее».

1. РАЗМЕР ЖИВЁТ В ТРЁХ БЕЛЫХ СПИСКАХ СРАЗУ. Узел редактора пишет картинке
   data-width, data-align и инлайновый style; тело статьи после этого чистится
   дважды — на сервере (wiki/sanitize.py) и при чтении (SANITIZE_OPTIONS в
   WikiArticle.jsx). Выпади атрибут хоть из одного списка — заданный размер
   исчезнет, и заметят это уже на опубликованной статье.

2. ВЫРАВНИВАНИЕ ДЕРЖИТСЯ НА ДВУХ ОТДЕЛЬНЫХ СВОЙСТВАХ. Санитайзер сверяет ИМЯ
   свойства CSS с белым списком, а сокращённого margin там нет и не будет.
   Напиши узел `margin: 0 auto` — и выравнивание вырежется целиком, а ширина
   останется: картинка уедет к левому краю.

3. КОНВЕРТЕР НЕ ИМЕЕТ ПРАВА ОТМЕНЯТЬ ЗАГРУЗКУ. SVG, HEIC без плагина, битый
   файл, окружение без Pillow — на каждом из этих входов картинка обязана
   доехать до бакета исходным форматом, а не превратиться в 500-ю ошибку у
   человека, который просто вставлял скриншот в инструкцию.

4. ФОРМАТ И ТИП ОБЯЗАНЫ СОВПАСТЬ. content_type пишется в ДВА места одного
   вызова: в сам блоб и в wiki_files. Роут /file/<id> подставляет в подпись
   значение ИЗ БАЗЫ, поэтому разъехавшиеся значения дадут WebP-байты, отданные
   как image/png, — и картинку, которую часть браузеров не покажет.
"""

import io
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover — окружение без Pillow
    Image = ImageDraw = None

from wiki import images as wiki_images  # noqa: E402
from wiki import storage as wiki_storage  # noqa: E402
from wiki.sanitize import sanitize_html  # noqa: E402

WIKI_SRC = ROOT / 'src' / 'components' / 'wiki'

needs_pillow = unittest.skipIf(Image is None, 'Pillow не установлен')


def png_bytes(width=40, height=30, colors=8):
    """Плоская картинка «как скриншот»: мало цветов, резкие границы."""
    img = Image.new('RGB', (width, height), 'white')
    for x in range(width):
        img.putpixel((x, 0), (0, 0, (x * 255 // max(1, colors)) % 256))
    out = io.BytesIO()
    img.save(out, 'PNG')
    return out.getvalue()


def palette_png_with_transparency():
    """PNG с палитрой, где прозрачность задана НОМЕРОМ цвета, а не каналом.

    Так устроен типовой значок и типовой логотип, и отдельного канала A у такой
    картинки нет вовсе — прозрачность лежит в info['transparency'].
    """
    pal = Image.new('P', (60, 60), 0)
    pal.putpalette([255, 255, 255] + [220, 50, 50] + [0, 0, 0] * 254)
    ImageDraw.Draw(pal).ellipse([5, 5, 55, 55], fill=1)
    out = io.BytesIO()
    pal.save(out, 'PNG', transparency=0)
    return out.getvalue()


def jpeg_bytes(width, height, quality=92):
    """Шум, сохранённый JPEG'ом: ведёт себя как фотография, не как скриншот."""
    photo = Image.effect_noise((width // 8, height // 8), 60).convert('RGB')
    out = io.BytesIO()
    photo.resize((width, height)).save(out, 'JPEG', quality=quality)
    return out.getvalue()


def gif_bytes(frames=6, loop=0):
    images = []
    for i in range(frames):
        frame = Image.new('RGB', (60, 40), 'white')
        frame.paste(Image.new('RGB', (10, 10), 'red'), (i * 5, 10))
        images.append(frame)
    out = io.BytesIO()
    extra = {} if loop is None else {'loop': loop}
    images[0].save(out, 'GIF', save_all=True, append_images=images[1:],
                   duration=90, **extra)
    return out.getvalue()


def strip_comments(source):
    """Исходник без комментариев.

    Проверять КОД по тексту с комментариями нельзя: объяснение «раньше здесь
    стояло data-width» удовлетворило бы поиск ровно того, чего в коде уже нет.
    Приём и причина взяты из tests/test_wiki_copy_protection.py.
    """
    without_blocks = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return '\n'.join(line for line in without_blocks.splitlines()
                     if not line.lstrip().startswith('//'))


# ─────────────────────────────────────────────────────────────────────────────
@needs_pillow
class WebpConversionTest(unittest.TestCase):
    """wiki/images.py — что во что переводится и что не трогается."""

    def test_png_becomes_webp_without_losing_pixels(self):
        """Скриншот пакуется БЕЗ ПОТЕРЬ: по мелкому тексту в нём читают
        подписи интерфейса, и мыло вместо букв обесценивает инструкцию."""
        source = png_bytes()
        data, content_type, width, height = wiki_images.to_webp(source, 'image/png')
        self.assertEqual(content_type, 'image/webp')
        self.assertEqual((width, height), (40, 30))
        with Image.open(io.BytesIO(data)) as out:
            self.assertEqual(out.format, 'WEBP')
            self.assertEqual(out.size, (40, 30))
            self.assertEqual(list(out.convert('RGB').getdata()),
                             list(Image.open(io.BytesIO(source)).convert('RGB').getdata()))

    def test_photo_is_shrunk_to_max_side(self):
        """Фотография с телефона ужимается: в колонке шириной 760 пикселей
        показать 4032 всё равно негде, а платит за них читатель трафиком."""
        source = jpeg_bytes(4032, 3024)
        data, content_type, width, height = wiki_images.to_webp(source, 'image/jpeg')
        self.assertEqual(content_type, 'image/webp')
        self.assertEqual(width, wiki_images.MAX_SIDE)
        self.assertEqual(height, wiki_images.MAX_SIDE * 3024 // 4032)
        self.assertLess(len(data), len(source))

    def test_small_picture_is_not_upscaled(self):
        source = png_bytes(20, 12)
        _data, _type, width, height = wiki_images.to_webp(source, 'image/png')
        self.assertEqual((width, height), (20, 12))

    def test_palette_transparency_is_not_lost(self):
        """Прозрачность живёт в двух разных местах, и второе легко не заметить.

        У PNG с палитрой канала A нет: прозрачный цвет записан НОМЕРОМ в
        info['transparency']. Проверка «есть ли канал A» его не находит, и
        значок уезжал бы в RGB — то есть прозрачный фон становился бы сплошным
        прямоугольником. На тёмной подложке статьи это видно сразу.
        """
        data, _type, _w, _h = wiki_images.to_webp(palette_png_with_transparency(),
                                                  'image/png')
        with Image.open(io.BytesIO(data)) as out:
            rgba = out.convert('RGBA')
            self.assertEqual(rgba.getpixel((1, 1))[3], 0, 'фон перестал быть прозрачным')
            self.assertEqual(rgba.getpixel((30, 30)), (220, 50, 50, 255))

    def test_ready_webp_passes_through_untouched(self):
        """Логотип парка браузер пережимает в WebP ещё до отправки. Второй
        проход через кодек только испортил бы готовое."""
        buffer = io.BytesIO()
        Image.open(io.BytesIO(png_bytes())).save(buffer, 'WEBP')
        source = buffer.getvalue()
        data, content_type, width, height = wiki_images.to_webp(source, 'image/webp')
        self.assertIs(data, source)
        self.assertEqual(content_type, 'image/webp')
        self.assertEqual((width, height), (40, 30))

    def test_animation_stays_animation(self):
        """Схлопнуть запись экрана в первый кадр — значит потерять всё, ради
        чего её вставляли, и не сказать об этом ни слова."""
        data, content_type, _w, _h = wiki_images.to_webp(gif_bytes(6), 'image/gif')
        self.assertEqual(content_type, 'image/webp')
        with Image.open(io.BytesIO(data)) as out:
            self.assertEqual(out.format, 'WEBP')
            self.assertEqual(out.n_frames, 6)

    def test_one_shot_animation_does_not_become_endless(self):
        """У GIF без блока NETSCAPE зацикливания нет — он проигрывается один
        раз. Подстановка нуля («крутить вечно») превратила бы разовую анимацию
        в вечно мигающую картинку посреди инструкции."""
        once = wiki_images.to_webp(gif_bytes(4, loop=None), 'image/gif')[0]
        with Image.open(io.BytesIO(once)) as out:
            self.assertEqual(out.info.get('loop'), 1)
        endless = wiki_images.to_webp(gif_bytes(4, loop=0), 'image/gif')[0]
        with Image.open(io.BytesIO(endless)) as out:
            self.assertEqual(out.info.get('loop'), 0)

    def test_huge_picture_is_left_alone(self):
        """Распакованный кадр занимает 4 байта на пиксель независимо от веса
        файла: 45 мегапикселей — это 180 МБ в куче рабочего процесса. Уронить
        портал пережатием одной картинки дороже, чем положить её как есть."""
        huge = io.BytesIO()
        Image.new('RGB', (9000, 5000), 'white').save(huge, 'PNG')
        self.assertIsNone(wiki_images.to_webp(huge.getvalue(), 'image/png'))

    def test_big_photo_is_decoded_at_reduced_size(self):
        """JPEG выше потолка всё равно проходит: у него декодер умеет отдать
        уменьшенный кадр сразу (draft), и в память разворачивается вчетверо
        меньше. Иначе фотография с современного телефона отсекалась бы
        потолком, ради которого он и ставился."""
        result = wiki_images.to_webp(jpeg_bytes(8000, 6000), 'image/jpeg')
        self.assertIsNotNone(result, 'фотография не пережалась')
        self.assertEqual(result[2], wiki_images.MAX_SIDE)

    def test_too_long_animation_is_left_alone(self):
        """Каждый кадр кодируется отдельно, а соединение из пула вики всё это
        время занято. Длинную запись честнее положить как есть."""
        original = wiki_images.MAX_FRAMES
        wiki_images.MAX_FRAMES = 3
        self.addCleanup(setattr, wiki_images, 'MAX_FRAMES', original)
        self.assertIsNone(wiki_images.to_webp(gif_bytes(6), 'image/gif'))

    def test_result_that_got_heavier_is_thrown_away(self):
        """Пережали, а стало тяжелее — значит не пережимали, а испортили.

        Случай не гипотетический: картинка, прошедшая через мессенджер,
        приезжает JPEG'ом невысокого качества, и второй проход через кодек даёт
        файл в полтора раза больше исходного и вдобавок хуже — потери
        накладываются на потери. Замер: JPEG q=50 весом 218 КБ превращался в
        WebP на 308 КБ.
        """
        source = jpeg_bytes(1200, 900, quality=45)
        data, content_type, width, height = wiki_images.to_webp(source, 'image/jpeg')
        self.assertIs(data, source)
        self.assertEqual(content_type, 'image/jpeg')
        self.assertEqual((width, height), (1200, 900))

    def test_the_same_photo_becomes_webp_when_it_wins(self):
        """Обратная сторона того же правила: где WebP выигрывает — он и едет."""
        source = jpeg_bytes(1200, 900, quality=95)
        data, content_type, _w, _h = wiki_images.to_webp(source, 'image/jpeg')
        self.assertEqual(content_type, 'image/webp')
        self.assertLess(len(data), len(source))

    def test_multipage_scan_is_not_mistaken_for_animation(self):
        """Кадров больше одного бывает не только у анимации: так устроен снимок
        с двухкамерного телефона (MPO внутри JPEG) и многостраничный скан TIFF.
        Такой файл уехал бы в бакет мигающей анимацией вместо картинки."""
        pages = [Image.new('RGB', (300, 400), shade) for shade in ('white', '#eeeeee', '#dddddd')]
        out = io.BytesIO()
        pages[0].save(out, 'TIFF', save_all=True, append_images=pages[1:])
        data, _type, _w, _h = wiki_images.to_webp(out.getvalue(), 'image/tiff')
        with Image.open(io.BytesIO(data)) as webp:
            self.assertEqual(getattr(webp, 'n_frames', 1), 1)

    def test_sixteen_bit_grey_is_left_alone(self):
        """Pillow при переводе I;16 в RGB не масштабирует диапазон, а ОБРЕЗАЕТ
        его: всё выше 255 становится белым. Ошибки при этом нет, и в бакет лёг
        бы почти сплошной белый прямоугольник."""
        out = io.BytesIO()
        Image.new('I;16', (40, 30), 40000).save(out, 'PNG')
        self.assertIsNone(wiki_images.to_webp(out.getvalue(), 'image/png'))

    def test_colour_profile_survives_but_exif_does_not(self):
        """Снимки телефонов размечены Display P3: без профиля браузер считает
        их sRGB, и фирменный цвет уходит в перенасыщение. EXIF, наоборот,
        выбрасываем намеренно — там GPS-координаты, а статью читает весь
        портал."""
        photo = Image.new('RGB', (200, 150), '#3366cc')
        exif = photo.getexif()
        exif[274] = 1
        out = io.BytesIO()
        photo.save(out, 'JPEG', quality=95, icc_profile=b'fake-icc-profile', exif=exif.tobytes())
        data, _type, _w, _h = wiki_images.to_webp(out.getvalue(), 'image/jpeg')
        with Image.open(io.BytesIO(data)) as webp:
            self.assertEqual(webp.info.get('icc_profile'), b'fake-icc-profile')
            self.assertFalse(webp.info.get('exif'))

    def test_rotated_photo_is_straightened(self):
        """WebP поля ориентации не хранит: не повернув кадр здесь, положим в
        статью фотографию боком."""
        photo = Image.new('RGB', (200, 100), 'red')
        exif = photo.getexif()
        exif[274] = 6                      # снято с поворотом на 90 градусов
        out = io.BytesIO()
        photo.save(out, 'JPEG', quality=95, exif=exif.tobytes())
        _data, _type, width, height = wiki_images.to_webp(out.getvalue(), 'image/jpeg')
        self.assertEqual((width, height), (100, 200))

    def test_svg_is_not_touched(self):
        """SVG — разметка, а не растр: перевод в пиксели убил бы его
        единственное достоинство."""
        self.assertIsNone(wiki_images.to_webp(b'<svg xmlns="..."/>', 'image/svg+xml'))

    def test_broken_file_does_not_raise(self):
        """Подпись соврала про содержимое — это не повод отменить загрузку."""
        self.assertIsNone(wiki_images.to_webp(b'\x89PNG not really', 'image/png'))

    def test_not_a_picture_is_ignored(self):
        self.assertIsNone(wiki_images.to_webp(b'%PDF-1.4', 'application/pdf'))
        self.assertIsNone(wiki_images.to_webp(b'', 'image/png'))

    def test_content_type_with_charset_is_understood(self):
        """Браузер присылает тип с довеском — на нём отвалилось бы сравнение."""
        result = wiki_images.to_webp(png_bytes(), 'image/PNG; charset=binary')
        self.assertIsNotNone(result)
        self.assertEqual(result[1], 'image/webp')


class WebpNameTest(unittest.TestCase):
    """Расширение обязано поменяться вместе с содержимым: из имени складывается
    путь в бакете, и «схема.png» с WebP внутри — это полчаса чужого времени."""

    def test_extension_is_replaced(self):
        self.assertEqual(wiki_images.webp_name('Скриншот 2026-08-28.PNG'),
                         'Скриншот 2026-08-28.webp')

    def test_nameless_file_gets_a_name(self):
        """У картинок из docx имя захардкожено строкой 'image' — расширения у
        них не было вовсе (wiki/routes_import.py)."""
        self.assertEqual(wiki_images.webp_name('image'), 'image.webp')
        self.assertEqual(wiki_images.webp_name(''), 'image.webp')
        self.assertEqual(wiki_images.webp_name(None), 'image.webp')

    def test_path_is_dropped(self):
        self.assertEqual(wiki_images.webp_name('/tmp/a/b/логотип.jpeg'), 'логотип.webp')


# ─────────────────────────────────────────────────────────────────────────────
class _StorageHarness(unittest.TestCase):
    """Одна дверь на всех, кто грузит: редактор, импорт документа, логотип."""

    def setUp(self):
        self.blob = MagicMock()
        bucket = MagicMock()
        bucket.blob.return_value = self.blob
        client = MagicMock()
        client.bucket.return_value = bucket
        self.gcs = {'bucket_name': lambda: 'otp-files', 'client': lambda: client}
        self.cursor = MagicMock()
        self.registered = {}

        def register_file(cursor, **kwargs):
            self.registered.update(kwargs)
            return 'file-uuid'

        from wiki import articles as wiki_articles
        original = wiki_articles.register_file
        wiki_articles.register_file = register_file
        self.addCleanup(setattr, wiki_articles, 'register_file', original)

    def store(self, data, filename, content_type):
        return wiki_storage.store_file(
            self.cursor, self.gcs, data=data, filename=filename,
            content_type=content_type, uploaded_by=7)


@needs_pillow
class StoreFileTest(_StorageHarness):

    def test_picture_reaches_the_bucket_as_webp(self):
        file_id, url = self.store(png_bytes(), 'Скриншот.png', 'image/png')
        self.assertEqual((file_id, url), ('file-uuid', '/api/wiki/file/file-uuid'))
        # Тип совпадает в ОБОИХ местах: в блобе и в базе. Роут /file/<id>
        # подставляет в подпись значение из базы.
        self.assertEqual(self.blob.upload_from_string.call_args.kwargs['content_type'],
                         'image/webp')
        self.assertEqual(self.registered['content_type'], 'image/webp')
        self.assertTrue(self.registered['blob_path'].endswith('.webp'),
                        self.registered['blob_path'])
        self.assertEqual(self.registered['original_name'], 'Скриншот.webp')

    def test_dimensions_reach_the_database(self):
        """Колонки width/height в wiki_files есть с самого начала и до сих пор
        заполнялись значением NULL — а размер кадра здесь и так известен."""
        self.store(png_bytes(40, 30), 'a.png', 'image/png')
        self.assertEqual((self.registered['width'], self.registered['height']), (40, 30))

    def test_stored_bytes_are_the_converted_ones(self):
        source = png_bytes()
        self.store(source, 'a.png', 'image/png')
        stored = self.blob.upload_from_string.call_args.args[0]
        self.assertNotEqual(stored, source)
        with Image.open(io.BytesIO(stored)) as out:
            self.assertEqual(out.format, 'WEBP')

    def test_size_written_to_the_database_is_the_size_in_the_bucket(self):
        """file_size обязан считаться ПОСЛЕ конвертации: иначе в базе останется
        вес исходника, и любая сводка по хранилищу будет врать."""
        self.store(png_bytes(), 'a.png', 'image/png')
        stored = self.blob.upload_from_string.call_args.args[0]
        self.assertEqual(self.registered['file_size'], len(stored))


@needs_pillow
class StoreFileKeepsHonestNamesTest(_StorageHarness):
    """Имя файла обязано описывать то, что внутри."""

    def test_name_is_not_renamed_when_the_format_did_not_change(self):
        """Конвертер вправе вернуть исходные байты — например, когда пережатие
        вышло только в минус. Назвать такой файл «.webp» значило бы соврать про
        содержимое и сбить с толку любого, кто заглянет в бакет."""
        source = jpeg_bytes(1200, 900, quality=45)
        self.store(source, 'фото.jpg', 'image/jpeg')
        self.assertEqual(self.registered['content_type'], 'image/jpeg')
        self.assertEqual(self.registered['original_name'], 'фото.jpg')
        self.assertTrue(self.registered['blob_path'].endswith('.jpg'),
                        self.registered['blob_path'])
        # Размеры при этом всё равно известны и всё равно пишутся.
        self.assertEqual((self.registered['width'], self.registered['height']), (1200, 900))


class StoreFileFallbackTest(_StorageHarness):
    """То, что конвертер не осилил, всё равно доезжает до бакета."""

    def test_svg_is_stored_as_is(self):
        source = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        self.store(source, 'схема.svg', 'image/svg+xml')
        self.assertEqual(self.blob.upload_from_string.call_args.args[0], source)
        self.assertEqual(self.registered['content_type'], 'image/svg+xml')
        self.assertTrue(self.registered['blob_path'].endswith('.svg'))

    def test_conversion_failure_does_not_break_upload(self):
        """Отказать в загрузке из-за конвертера хуже, чем сохранить исходник."""
        original = wiki_images.to_webp
        wiki_images.to_webp = lambda *a, **kw: None
        self.addCleanup(setattr, wiki_images, 'to_webp', original)
        file_id, url = self.store(b'\x89PNG', 'a.png', 'image/png')
        self.assertEqual(file_id, 'file-uuid')
        self.assertEqual(url, '/api/wiki/file/file-uuid')
        self.assertEqual(self.registered['content_type'], 'image/png')

    def test_no_bucket_means_no_record(self):
        self.gcs = {'bucket_name': lambda: None}
        self.assertEqual(self.store(b'x', 'a.png', 'image/png'), (None, None))
        self.assertEqual(self.registered, {})


# ─────────────────────────────────────────────────────────────────────────────
class ImageSizeSurvivesSanitizerTest(unittest.TestCase):
    """Всё, чем узел редактора описывает размер, обязано пережить сервер."""

    SAVED = ('<img src="/api/wiki/file/abc" alt="" data-width="45" data-align="center" '
             'style="width: 45%; margin-left: auto; margin-right: auto">')

    def test_size_and_alignment_survive(self):
        out = sanitize_html(self.SAVED)
        for token in ('data-width="45"', 'data-align="center"', 'width: 45%',
                      'margin-left: auto', 'margin-right: auto'):
            self.assertIn(token, out, token)

    def test_alignment_must_not_be_written_as_a_shorthand(self):
        """Санитайзер сверяет ИМЯ свойства; сокращённого margin в списке нет.
        Тест держит узел от соблазна написать `margin: 0 auto`."""
        out = sanitize_html('<img src="x" style="margin: 0 auto; width: 45%">')
        self.assertNotIn('margin', out)
        self.assertIn('width: 45%', out)

    def test_display_and_float_stay_forbidden(self):
        """Оба свойства сознательно вне списка: display прячет куски регламента
        от читателя, float ломает поток статьи. Поэтому блочность выровненной
        картинки задаётся правилом в wiki-theme.css, а не инлайновым стилем."""
        out = sanitize_html('<img src="x" style="display: none; float: left; width: 45%">')
        self.assertNotIn('display', out)
        self.assertNotIn('float', out)
        self.assertIn('width: 45%', out)

    def test_picture_still_cannot_carry_code(self):
        out = sanitize_html('<img src="x" onerror="alert(1)" data-width="45">')
        self.assertNotIn('onerror', out.lower())
        self.assertIn('data-width="45"', out)


# ─────────────────────────────────────────────────────────────────────────────
class EditorNodeTest(unittest.TestCase):
    """Узел картинки в редакторе — то, чего у стокового расширения нет."""

    @classmethod
    def setUpClass(cls):
        cls.node = strip_comments((WIKI_SRC / 'WikiImageNode.jsx').read_text(encoding='utf-8'))
        cls.sizes = strip_comments((WIKI_SRC / 'imageSize.js').read_text(encoding='utf-8'))
        cls.editor = strip_comments((WIKI_SRC / 'WikiEditor.jsx').read_text(encoding='utf-8'))

    def test_editor_uses_the_node_and_not_the_stock_extension(self):
        """Стоковый Image умеет только вставить <img>: подключи его обратно — и
        размер снова станет нечем задать."""
        self.assertIn('WikiImage.configure(', self.editor)
        self.assertNotIn("from '@tiptap/extension-image'", self.editor)

    def test_size_is_written_in_per_cent(self):
        """Пиксели здесь означали бы «на телефоне картинка встала иначе».

        Сама арифметика проверяется в tests/wiki_image_size.test.mjs — она для
        того и вынесена в отдельный модуль без JSX, что узел тянет за собой
        React и TipTap и до `node --test` без сборки не доезжает."""
        self.assertIn("width: ${width}%", self.sizes)
        self.assertNotIn('px', self.sizes)

    def test_node_does_not_keep_its_own_copy_of_the_arithmetic(self):
        """Второй экземпляр clampSize/styleFor разошёлся бы с проверенным
        первым на первой же правке — и разошёлся бы молча."""
        self.assertIn("from './imageSize'", self.node)
        self.assertNotIn('const clampSize', self.node)
        self.assertNotIn('const styleFor', self.node)

    def test_size_and_alignment_go_out_in_both_forms(self):
        """data-* читает редактор при следующем открытии, style — все места,
        где HTML статьи показывают как есть (история версий, сравнение
        редакций, ответ ИИ-помощника)."""
        self.assertIn("'data-width'", self.node)
        self.assertIn("'data-align'", self.node)
        self.assertIn("margin-left: auto", self.sizes)
        self.assertIn("margin-right: auto", self.sizes)

        # Мало убедиться, что styleFor умеет собрать строку: смотрим, что
        # renderHTML её и правда подмешивает в тег. Иначе стиль перестал бы
        # уезжать в HTML, а тест остался бы зелёным — строки-то в модуле на месте.
        render = re.search(r'renderHTML\(\{ node, HTMLAttributes \}\) \{(.*?)\n    \},',
                           self.node, re.S)
        self.assertIsNotNone(render, 'не нашли renderHTML узла')
        self.assertIn('styleFor(node.attrs)', render.group(1))
        self.assertIn('{ style }', render.group(1))

    def test_drag_handle_is_reachable_with_a_finger(self):
        """Без touch-action браузер через пару миллиметров решает, что палец на
        ручке — это прокрутка, и забирает жест себе: тяга обрывается на
        полпути, а на планшете размер картинки становится нечем менять."""
        css = (WIKI_SRC / 'wiki-theme.css').read_text(encoding='utf-8')
        handle = re.search(r'\.wiki-image-node__resize \{(.*?)\n\}', css, re.S)
        self.assertIsNotNone(handle, 'не нашли правило ручки')
        self.assertIn('touch-action: none', handle.group(1))

    def test_untouched_picture_has_no_size_at_all(self):
        """Подставить всем 100 % значило бы растянуть каждый мелкий значок на
        всю колонку и превратить его в мыло.

        Ищем в объявлении САМОГО поля size: 'default: null' стоит и у
        выравнивания, и поиск по всему файлу проходил бы, даже поставь кто-то
        ширине сотню."""
        block = re.search(r'\bsize: \{(.*?)\n            \},', self.node, re.S)
        self.assertIsNotNone(block, 'не нашли объявление атрибута size')
        self.assertIn('default: null', block.group(1))

    def test_files_are_uploaded_one_by_one(self):
        """Пачка брошенных разом картинок не должна уходить одновременно.

        Каждая загрузка занимает соединение из пула вики и пережимает картинку
        в WebP: десяток файлов — это десяток занятых соединений и десяток
        одновременных кодирований. И порядок: позиция вставки считается один
        раз, поэтому параллельные вставки уложили бы картинки задом наперёд.
        """
        block = re.search(r'const insertImageFiles = useStableCallback\((.*?)\n    \}\);',
                          self.editor, re.S)
        self.assertIsNotNone(block, 'не нашли insertImageFiles')
        self.assertIn('reduce', block.group(1))
        self.assertNotIn('forEach', block.group(1))

    def test_there_are_buttons_and_not_only_a_handle(self):
        """Ручку не потянуть ни с клавиатуры, ни толком на тачскрине."""
        for title in ('Уменьшить', 'Увеличить', 'Вернуть исходный размер',
                      'По центру', 'Удалить картинку'):
            self.assertIn(title, self.node, title)

    def test_drag_listeners_come_off_on_every_ending(self):
        """Отпускание — это три разных события.

        pointerup не приходит, если жест отменила система (звонок, свайп
        «назад» на тачскрине), а узел могли и вовсе удалить посреди тяги. В
        обоих случаях слушатель остался бы на window навсегда и продолжал бы
        менять документ при обычном движении мыши, без нажатой кнопки.
        """
        self.assertIn("'pointercancel'", self.node)
        self.assertIn('useEffect(() => () => dragRef.current?.(), [])', self.node)

    def test_button_keeps_quiet_until_the_picture_is_measured(self):
        """Пока картинка не загрузилась, ширины у неё нет. Раньше на это место
        подставлялось «считаем, что 100 %», и первое же нажатие «−» превращало
        мелкий значок в картинку на 95 % колонки."""
        block = re.search(r'const currentSize = useCallback\((.*?)\}, \[size\]\);',
                          self.node, re.S)
        self.assertIsNotNone(block, 'не нашли currentSize')
        self.assertIn('return null', block.group(1))
        # Замер НЕ зажимается в диапазон: clampSize подтянул бы долю мелкого
        # значка к нижней границе, и «уменьшить» от неё эту картинку удваивало.
        self.assertNotIn('clampSize((shown', block.group(1))

    def test_pasted_picture_goes_to_the_bucket(self):
        """Без этого вставленный скриншот ложится в текст статьи строкой
        base64: мимо хранилища, мимо WebP и на мегабайты в тело статьи."""
        self.assertIn('handlePaste', self.editor)
        self.assertIn('handleDrop', self.editor)
        self.assertIn('insertImageFiles', self.editor)

    def test_copied_fragment_is_not_stripped_to_its_picture(self):
        """В буфере от копирования куска документа лежат И текст, И картинка.
        Забрать оттуда одну картинку — значит потерять абзац."""
        self.assertIn("getData('text/plain')", self.editor)


class ReaderWhitelistTest(unittest.TestCase):
    """Второй санитайзер, при чтении. Разойдись он с серверным — размер
    сбросится уже на витрине, и опять молча."""

    def test_dompurify_keeps_the_size_attributes(self):
        article = strip_comments((WIKI_SRC / 'WikiArticle.jsx').read_text(encoding='utf-8'))
        options = article[article.index('SANITIZE_OPTIONS'):article.index('const wrapTables')]
        self.assertIn("'data-width'", options)
        self.assertIn("'data-align'", options)

    def test_panel_of_the_topmost_picture_moves_inside(self):
        """У картинки первым блоком статьи места над ней нет: панель уехала бы
        на тулбар редактора. Отсчёт обязан идти от .wiki-prose, а НЕ от
        :first-child самого узла — узел всегда единственный ребёнок
        технической обёртки .react-renderer, которую ставит
        ReactNodeViewRenderer, и такое условие было бы истинно у КАЖДОЙ
        картинки: панель уехала бы внутрь у всех.
        """
        css = (WIKI_SRC / 'wiki-theme.css').read_text(encoding='utf-8')
        self.assertIn('.wiki-prose > :first-child .wiki-image-node__tools', css)
        self.assertNotIn('.wiki-image-node:first-child', css)

    def test_theme_makes_a_sized_picture_a_block(self):
        """margin: auto не работает на строчном элементе, а display в белый
        список CSS не входит — значит блочность обязана прийти из темы.

        И размер её тоже требует, не только выравнивание: две картинки по 45 %
        без выравнивания встали бы в статье в ОДНУ СТРОКУ, а в редакторе друг
        под другом — узел там всегда отдельный блок."""
        css = (WIKI_SRC / 'wiki-theme.css').read_text(encoding='utf-8')
        self.assertIn('.wiki-prose img[data-align],\n.wiki-prose img[data-width] '
                      '{ display: block; }', css)
        self.assertIn(".wiki-prose img[data-align='center']", css)


if __name__ == '__main__':
    unittest.main()
