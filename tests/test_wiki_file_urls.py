# -*- coding: utf-8 -*-
"""Картинки вики на телефоне: подписанный адрес вместо ручки раздела.

ЧТО СЛОМАЛОСЬ. Картинка статьи и логотип парка запрашиваются тегом <img>, а он
не отправляет заголовков — авторизовать его может только кука сессии. Мобильному
UA портал выдаёт куку с SameSite=Lax и без Partitioned (_cookie_options в
bot_schedule2.py), страница живёт на GitHub Pages, API — на Render: сайты
разные, значит куки в запросе картинки нет вовсе. Замер на боевом 11.09.2026:

    POST /api/login, UA iPhone → Set-Cookie: ...; Secure; SameSite=Lax
    POST /api/login, UA macOS  → Set-Cookie: ...; Secure; SameSite=None; Partitioned
    GET  /api/wiki/file/<id> без куки → 401 Unauthorized

На телефоне пропадали ВСЕ картинки из бакета: логотипы парков и всё, что
загружено в статью файлом. Оставались только base64 из старой вики — они часть
текста, и запроса за ними не идёт. Отсюда жалоба «не видно НЕКОТОРЫХ фоток».

ЧТО СТОРОЖИТ ЭТОТ НАБОР — три вещи, и каждая ломается молча:

1. ПОДПИСЬ НЕ РАСШИРЯЕТ ДОСТУП. Тело статьи вправе ссылаться на файл чужой
   статьи (картинку переносят копированием разметки), и подпись «на всё, что
   упомянуто в тексте» раздавала бы закрытое. Правило то же, что у ручки
   /file/<id>, и повторено оно в _display_urls намеренно.
2. ПОДПИСЬ НЕ ПОПАДАЕТ В ТЕЛО СТАТЬИ. Редактор сохраняет то, что ему показали:
   подписанный адрес в базе через три часа стал бы битой картинкой навсегда, а
   файл с таким адресом перестал бы привязываться к статье (link_content_files
   ищет /api/wiki/file/<id>) — то есть был бы виден одному загрузившему.
3. ПОДПИСЕЙ НЕ БОЛЬШЕ, ЧЕМ НУЖНО. get_gcs_client() не мемоизирован — каждый
   вызов это разбор учётных данных и ключа RSA, а в галерее кадров двадцать.
"""

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
except ImportError:  # pragma: no cover — окружение без Flask
    Flask = None

from wiki import articles as wiki_articles  # noqa: E402
from wiki import edit as wiki_edit  # noqa: E402
from wiki import file_urls as wiki_file_urls  # noqa: E402
from wiki import perimeter as wiki_perimeter  # noqa: E402
from wiki import queries  # noqa: E402
from wiki.access import collect_subjects  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402

WIKI_SRC = ROOT / 'src' / 'components' / 'wiki'

MINE = '0928be99-b7fb-4a44-a7f7-00b6efe057e6'      # файл этой статьи
FOREIGN = '9214e203-ed02-468b-a8b6-22fdcd78128e'   # файл чужой статьи
LOOSE = '07276f05-a7f8-4f7c-b2ce-9fdfd8c52b67'     # ничей: загружен в редакторе


def _row(file_id, *, article_id=None, uploaded_by=None, blob='wiki/2026/kadr.webp'):
    return {'id': file_id, 'article_id': article_id, 'bucket': 'otp-files',
            'blob_path': blob, 'content_type': 'image/webp',
            'uploaded_by': uploaded_by}


class IdsInTest(unittest.TestCase):
    """Что считается ссылкой на файл."""

    def setUp(self):
        wiki_file_urls._SIGNED.clear()

    def test_finds_pictures_and_links(self):
        html = ('<p><img src="/api/wiki/file/%s"></p>'
                '<a href="/api/wiki/file/%s">вложение</a>' % (MINE, FOREIGN))
        self.assertEqual(wiki_file_urls.ids_in(html), [MINE, FOREIGN])

    def test_same_picture_twice_is_one_id(self):
        """Иначе галерея из одного кадра, повторённого десять раз, подписывалась
        бы десять раз — и это была бы десятикратная плата за один и тот же URL."""
        html = '<img src="/api/wiki/file/%s"><img src="/api/wiki/file/%s">' % (
            MINE, MINE.upper())
        self.assertEqual(wiki_file_urls.ids_in(html), [MINE])

    def test_garbage_is_not_an_id(self):
        for html in ('', None, '<img src="/api/wiki/file/сам-ты-файл">',
                     '<img src="/api/wiki/files/%s">' % MINE):
            self.assertEqual(wiki_file_urls.ids_in(html), [], repr(html))

    def test_the_regexp_is_the_same_one_that_links_files(self):
        """Одно описание на весь раздел: разойдись они — показанная картинка и
        привязанный к статье файл перестали бы быть одним множеством."""
        self.assertIs(wiki_edit._FILE_REF, wiki_file_urls.FILE_REF)


class SignFilesTest(unittest.TestCase):
    """Подпись адресов: что подписывается, чем и сколько раз."""

    def setUp(self):
        wiki_file_urls._SIGNED.clear()
        self.calls = []

        def sign(bucket, blob_path, **kwargs):
            self.calls.append((bucket, blob_path, kwargs))
            return 'https://storage.example/%s?X-Goog-Signature=%d' % (
                blob_path, len(self.calls))

        self.gcs = {'signed_url': sign}

    def test_map_is_keyed_by_file_id(self):
        urls = wiki_file_urls.sign_files(self.gcs, [_row(MINE)])
        self.assertEqual(list(urls), [MINE])
        self.assertTrue(urls[MINE].startswith('https://storage.example/'))

    def test_signature_carries_type_and_opens_in_place(self):
        """Тип берётся ИЗ БАЗЫ, как и у ручки /file/<id>: иначе WebP-байты
        уехали бы читателю с чужим типом. inline — чтобы картинка показалась,
        а не скачалась файлом."""
        wiki_file_urls.sign_files(self.gcs, [_row(MINE)])
        _bucket, _path, kwargs = self.calls[0]
        self.assertEqual(kwargs['response_type'], 'image/webp')
        self.assertEqual(kwargs['response_disposition'], 'inline')
        self.assertEqual(kwargs['expires_minutes'], wiki_file_urls.SIGN_MINUTES)

    def test_same_blob_is_signed_once(self):
        """Подпись v4 кладёт в адрес момент подписания: без кэша один и тот же
        кадр получал бы новый адрес на каждое открытие статьи — и перекачивался
        бы заново, мимо кэша браузера."""
        first = wiki_file_urls.sign_files(self.gcs, [_row(MINE)])
        second = wiki_file_urls.sign_files(self.gcs, [_row(MINE)])
        self.assertEqual(first[MINE], second[MINE])
        self.assertEqual(len(self.calls), 1)

    def test_expiring_signature_is_renewed_early(self):
        """Переподписываем ДО истечения: между выдачей адреса и загрузкой кадра
        проходит время, а у открытой вкладки оно ещё и складывается с чтением."""
        wiki_file_urls.sign_files(self.gcs, [_row(MINE)])
        key = ('otp-files', 'wiki/2026/kadr.webp')
        url, _until = wiki_file_urls._SIGNED[key]
        import time
        wiki_file_urls._SIGNED[key] = (url, time.time() + 60)
        wiki_file_urls.sign_files(self.gcs, [_row(MINE)])
        self.assertEqual(len(self.calls), 2)

    def test_failed_signature_drops_out_of_the_map(self):
        """Витрина подставит такому файлу прежний адрес ручки — то есть станет
        ровно тем, чем была. Ошибка подписи не должна ронять всю статью."""
        def boom(*_a, **_k):
            raise RuntimeError('нет приватного ключа')

        urls = wiki_file_urls.sign_files({'signed_url': boom}, [_row(MINE)])
        self.assertEqual(urls, {})

    def test_nothing_to_sign_is_not_an_error(self):
        self.assertEqual(wiki_file_urls.sign_files({}, [_row(MINE)]), {})
        self.assertEqual(wiki_file_urls.sign_files(self.gcs, []), {})
        self.assertEqual(wiki_file_urls.sign_files(self.gcs, [_row(MINE, blob='')]), {})


ARTICLE = {
    'id': 7, 'slug': 'test', 'title': 'Тест', 'summary': None,
    'content': '<p><img src="/api/wiki/file/%s"><img src="/api/wiki/file/%s">'
               '<img src="/api/wiki/file/%s"></p>' % (MINE, FOREIGN, LOOSE),
    'status': 'published', 'visibility_mode': 'inherit', 'strict_mode': False,
    'author_id': 42, 'owner_user_id': None, 'section_ids': [3], 'tags': [],
    'views': 1, 'toc': None,
}


def make_context():
    return {
        'user_id': 42, 'otp_role': 'admin', 'department_id': None,
        'direction_id': None, 'headed_department_ids': [], 'group_ids': [],
        'wiki_roles': [{'id': 5, 'code': 'wiki_admin', 'can_read': True,
                        'can_create': True, 'can_edit': True, 'can_delete': True,
                        'can_publish': True, 'can_approve': True,
                        'can_manage_users': True, 'can_manage_structure': True,
                        'can_manage_access': True}],
        'access_mode': 'auto',
    }


@unittest.skipIf(Flask is None, 'flask не установлен')
class ArticleRouteTest(unittest.TestCase):
    """Ответ статьи: карта подписанных адресов и её граница."""

    def build(self, *, files=(), content=None):
        wiki_file_urls._SIGNED.clear()
        self.asked = []

        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        cursor.rowcount = 1

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor
        context = make_context()
        article = dict(ARTICLE)
        if content is not None:
            article['content'] = content

        def files_for_display(_cursor, ids):
            self.asked.append(list(ids))
            return [dict(row) for row in files]

        patches = [
            (queries, 'load_access_context', lambda _c, _u: dict(context)),
            (queries, 'log_action', lambda *a, **k: None),
            # Периметр: видна только статья 7. Чужая статья 99 — вне его, и
            # именно на ней проверяется, что подпись не расширяет доступ.
            (wiki_perimeter, 'read_perimeter',
             lambda *a, **k: (collect_subjects(user_id=42, otp_role='admin'), {3}, {7})),
            (wiki_articles, 'get_article', lambda *a, **k: dict(article)),
            (wiki_articles, 'register_view', lambda *a, **k: None),
            (wiki_articles, 'backlinks', lambda *a, **k: []),
            (wiki_articles, 'is_favorite', lambda *a, **k: False),
            (wiki_articles, 'files_for_display', files_for_display),
        ]
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
            gcs={'signed_url': lambda bucket, blob, **k: 'https://storage.example/' + blob},
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def test_own_picture_gets_a_signed_address(self):
        client = self.build(files=[_row(MINE, article_id=7, blob='a.webp')])
        body = client.get('/api/wiki/articles/test').get_json()
        self.assertEqual(body['file_urls'], {MINE: 'https://storage.example/a.webp'})

    def test_picture_of_a_foreign_article_is_not_signed(self):
        """Ключевая проверка набора: разметку с картинкой переносят
        копированием, и подпись «на всё, что в тексте» раздавала бы файлы из
        статей, которых читателю не видно. Ему остаётся ручка /file/<id>,
        которая ответит тем же отказом, что и до правки."""
        client = self.build(files=[_row(MINE, article_id=7, blob='a.webp'),
                                   _row(FOREIGN, article_id=99, blob='b.webp')])
        body = client.get('/api/wiki/articles/test').get_json()
        self.assertIn(MINE, body['file_urls'])
        self.assertNotIn(FOREIGN, body['file_urls'])

    def test_unlinked_picture_is_signed_only_to_the_one_who_uploaded_it(self):
        """Файл без статьи — это картинка, загруженная в редакторе до первого
        сохранения. Правило то же, что у ручки: видит загрузивший."""
        client = self.build(files=[_row(LOOSE, uploaded_by=42, blob='c.webp')])
        self.assertIn(LOOSE, client.get('/api/wiki/articles/test').get_json()['file_urls'])

        client = self.build(files=[_row(LOOSE, uploaded_by=7, blob='c.webp')])
        self.assertEqual(client.get('/api/wiki/articles/test').get_json()['file_urls'], {})

    def test_article_without_files_asks_the_database_nothing(self):
        """Лишний запрос на каждое открытие статьи — это соединение из общего
        пула портала, того же, что держит поток событий аукциона."""
        client = self.build(content='<p>Одни буквы</p>')
        body = client.get('/api/wiki/articles/test').get_json()
        self.assertEqual(body['file_urls'], {})
        self.assertEqual(self.asked, [])

    def test_body_of_the_article_keeps_the_permanent_address(self):
        """Тело уезжает и в редактор, а он сохраняет то, что ему показали."""
        client = self.build(files=[_row(MINE, article_id=7, blob='a.webp')])
        body = client.get('/api/wiki/articles/test').get_json()
        self.assertIn('/api/wiki/file/%s' % MINE, body['content'])
        self.assertNotIn('storage.example', body['content'])


class UploadCachingTest(unittest.TestCase):
    """Срок годности кадра для браузера ставится при загрузке."""

    def test_uploaded_picture_may_be_cached_for_an_hour(self):
        """Без явного значения GCS отдаёт непубличный объект с max-age=0, и
        каждое открытие статьи перекачивало бы все её кадры заново — теперь
        прямо из бакета и мобильным трафиком читателя."""
        from wiki import storage as wiki_storage

        blob = MagicMock()
        client = MagicMock()
        client.bucket.return_value.blob.return_value = blob
        cursor = MagicMock()
        cursor.fetchone.return_value = (MINE,)

        wiki_storage.store_file(
            cursor, {'bucket_name': lambda: 'otp-files', 'client': lambda: client},
            data=b'\x89PNG\r\n\x1a\n', filename='kadr.png',
            content_type='image/png', uploaded_by=42)
        self.assertEqual(blob.cache_control, 'private, max-age=3600')
        blob.upload_from_string.assert_called_once()


class FrontendTest(unittest.TestCase):
    """Фронт читаем текстом: сборка молча пропустит и потерянную карту адресов,
    и подпись, уехавшую в редактор."""

    def source(self, name):
        return (WIKI_SRC / name).read_text(encoding='utf-8')

    def test_viewer_passes_the_signed_map(self):
        text = self.source('WikiArticle.jsx')
        self.assertRegex(
            text,
            r'absolutizeFileUrls\([^;]*base,\s*article\.file_urls\)',
            'витрина обязана показывать подписанные адреса, иначе на телефоне '
            'картинок снова не будет')

    def test_editor_does_not_take_signed_addresses(self):
        """Если карта доедет до редактора, подпись уедет в базу при сохранении:
        через три часа — битая картинка навсегда и отвязанный от статьи файл."""
        self.assertNotIn('file_urls', self.source('WikiEditor.jsx'))

    def test_signed_address_is_escaped_for_the_markup(self):
        """Подпись — это query через `&`, а вставляем мы её в разметку, которую
        браузер разбирает сам: голый `&copy…` стал бы символом «©», и адрес
        перестал бы совпадать с подписанным."""
        text = self.source('fileUrls.js')
        self.assertIn("replace(/&/g, '&amp;')", text)
        self.assertRegex(text, r'attrValue\(signed\)')

    def test_editor_still_folds_addresses_back_before_saving(self):
        """Свойство «показали → сохранили → тот же адрес» держит всю схему."""
        self.assertIn('relativizeFileUrls(editor.getHTML())',
                      self.source('WikiEditor.jsx'))


if __name__ == '__main__':
    unittest.main()
