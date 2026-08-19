# -*- coding: utf-8 -*-
"""Две поломки витрины вики, которые чинились вместе.

1. СТАТЬЯ БЕЗ РАЗДЕЛА. Раздел выбирать было не обязательно, и статья без него
   проваливалась в дыру: visibility_mode='inherit' наследует права от разделов,
   а разделов нет — периметр пуст по построению, и статью не видел никто, кроме
   автора. Ни в оглавлении, ни в поиске, ни в списке черновиков. На проде так
   залипли три штуки. Держался этот случай на переключателе «Всё содержимое» у
   администратора доступов — кнопке, которая выкладывала ему содержимое чужих
   отделов, чтобы можно было починить одну статью. Теперь раздел проставляется
   сам: пусто → «Общий отдел», и чинить нечего.

2. ЗВЁЗДОЧКА В ОДНУ СТОРОНУ. Кнопка «В избранное» всегда слала POST, а вставка
   идёт с ON CONFLICT DO NOTHING: второе нажатие не делало ничего, но отвечало
   «Добавлено в избранное». Убрать статью из избранного было нельзя ниоткуда,
   хотя DELETE на сервере есть и работает.
"""

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
from wiki import perimeter as wiki_perimeter  # noqa: E402
from wiki import queries  # noqa: E402
from wiki.access import collect_subjects  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402


class RecordingCursor:
    """Курсор, который запоминает запросы и отвечает на поиск общего раздела."""

    def __init__(self, fallback_id=8):
        self.fallback_id = fallback_id
        self.calls = []          # [(sql, params), ...]
        self._last = None

    def execute(self, sql, params=None):
        self.calls.append((' '.join(sql.split()), params))
        self._last = sql

    def fetchone(self):
        if 'FROM wiki_sections' in (self._last or ''):
            return (self.fallback_id,) if self.fallback_id else None
        return None

    def fetchall(self):
        return []

    @property
    def inserted_sections(self):
        return [params[1] for sql, params in self.calls
                if sql.startswith('INSERT INTO wiki_article_sections')]


class DefaultSectionTest(unittest.TestCase):
    """set_sections — единственная точка, где статья привязывается к разделам."""

    def test_empty_selection_lands_in_general_department(self):
        cursor = RecordingCursor(fallback_id=8)
        wiki_edit.set_sections(cursor, 7, [])
        self.assertEqual(cursor.inserted_sections, [8],
                         'статья без раздела обязана попасть в общий отдел')

    def test_none_selection_lands_there_too(self):
        """Создание без ключа section_ids приходит как None, а не как []."""
        cursor = RecordingCursor(fallback_id=8)
        wiki_edit.set_sections(cursor, 7, None)
        self.assertEqual(cursor.inserted_sections, [8])

    def test_chosen_section_is_not_touched(self):
        """Выбранный человеком раздел общим отделом НЕ дополняется."""
        cursor = RecordingCursor(fallback_id=8)
        wiki_edit.set_sections(cursor, 7, [3])
        self.assertEqual(cursor.inserted_sections, [3])
        self.assertNotIn(8, cursor.inserted_sections)

    def test_no_general_department_is_survivable(self):
        """Общего отдела в базе может не быть — падать из-за этого нельзя.

        Сами его не создаём: молча заведённый публичный раздел раздал бы права
        шире, чем кто-либо просил.
        """
        cursor = RecordingCursor(fallback_id=None)
        wiki_edit.set_sections(cursor, 7, [])
        self.assertEqual(cursor.inserted_sections, [])

    def test_lookup_prefers_slug_over_space_name(self):
        """Ищем и по слагу раздела, и по названию отдела — слаг вперёд.

        Слаг задаётся переносом и правкам названия не подвержен; название —
        запасной путь для базы, собранной руками.
        """
        cursor = RecordingCursor(fallback_id=8)
        wiki_edit.default_section_id(cursor)
        sql, params = cursor.calls[0]
        self.assertIn('obschiy-sotrudnik', params.values())
        self.assertIn('Общий отдел', params.values())
        self.assertIn('ORDER BY (s.slug = %(slug)s) DESC', sql)


ARTICLE = {
    'id': 7, 'slug': 'test', 'title': 'Тест', 'summary': None, 'content': '<p>x</p>',
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
class FavoriteRouteTest(unittest.TestCase):
    """Звезда обязана работать в обе стороны и говорить о своём состоянии."""

    def build(self, *, favorite_now=False):
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
        self.captured = []

        patches = [
            (queries, 'load_access_context', lambda _c, _u: dict(context)),
            (queries, 'log_action', lambda *a, **k: None),
            # Субъекты собирает боевой collect_subjects: словарь-литерал уже
            # отставал бы от модели, а section_rules_for_user читает его ключи.
            (wiki_perimeter, 'read_perimeter',
             lambda *a, **k: (collect_subjects(user_id=42, otp_role='admin'), {3}, {7})),
            (wiki_articles, 'get_article', lambda *a, **k: dict(ARTICLE)),
            (wiki_articles, 'register_view', lambda *a, **k: None),
            (wiki_articles, 'backlinks', lambda *a, **k: []),
            (wiki_articles, 'is_favorite', lambda *a, **k: favorite_now),
            (wiki_articles, 'set_favorite',
             lambda _c, user_id, article_id, value: self.captured.append(value)),
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
            client_ip=lambda: '127.0.0.1',
            gcs={'signed_url': lambda *a, **k: 'https://x'},
        ))
        app.config['TESTING'] = True
        return app.test_client()

    def test_delete_removes_from_favorites(self):
        client = self.build(favorite_now=True)
        response = client.delete('/api/wiki/articles/7/favorite')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.captured, [False], 'DELETE обязан снимать звезду')
        self.assertFalse(response.get_json()['is_favorite'])

    def test_post_adds_to_favorites(self):
        client = self.build(favorite_now=False)
        response = client.post('/api/wiki/articles/7/favorite')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.captured, [True])
        self.assertTrue(response.get_json()['is_favorite'])

    def test_article_payload_carries_state(self):
        """Без этого поля звезда в шапке не знает, что рисовать."""
        client = self.build(favorite_now=True)
        response = client.get('/api/wiki/articles/test')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['is_favorite'])


if __name__ == '__main__':
    unittest.main()
