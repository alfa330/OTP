# -*- coding: utf-8 -*-
"""Перенос статей из старой корпоративной вики и очередь их модерации.

Что здесь проверяется и почему именно это — по одному пункту на каждое
требование постановки (задача #234), плюс два на ловушки реализации.

1. НИ ОДНА ПЕРЕНЕСЁННАЯ СТАТЬЯ НЕ СТАНОВИТСЯ ВИДИМОЙ САМА. Эндпоинт переноса
   обязан быть физически неспособен опубликовать статью: параметра `status` он
   не читает вовсе. Тест шлёт `status='published'` и требует, чтобы статья
   осталась черновиком. Это главное требование постановки, и проверять его
   надо не на «по умолчанию выключено», а на «сделать нельзя».

2. ПОВТОРНЫЙ ПРОГОН НЕ ПЛОДИТ КОПИИ. Скрипт переноса запускается несколько раз
   (план, пробный прогон, полный, дозаливка). Второй раз та же страница
   источника обязана вернуть УЖЕ созданную статью, а не завести вторую.

3. ПРОВЕРКА НА ДУБЛЬ — ОДИН КОД С РЕДАКТОРОМ. Раньше реализация жила локальным
   замыканием внутри routes_import, и перенос вынужден был бы написать вторую.
   Тест держит границу: обе двери зовут одну функцию.

4. РЕШЕНИЕ ПРИНИМАЕТСЯ ОДИН РАЗ. Два человека открыли очередь одновременно —
   второй не должен переписать решение первого. Держится на `reviewed_at IS NULL`
   в UPDATE, и тест проверяет именно ответ, а не только SQL.

5. УЖЕ ОПУБЛИКОВАННУЮ НЕ «ПУБЛИКУЮТ» ВТОРОЙ РАЗ. Статья могла приехать до
   появления очереди и жить на витрине. Решение по ней — подтвердить, и лишней
   версии в истории с комментарием «Публикация после переноса» появляться не
   должно: это соврало бы о том, что текст в этот момент выпускали.

6. ССЫЛКИ И КАРТИНКИ ПЕРЕПИСЫВАЮТСЯ. 367 ссылок корпуса ведут обратно в старую
   вику по четырём разным адресам. Не переписать их — значит перенести статьи,
   которые ссылаются на то, что выключат.
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
except ImportError:  # pragma: no cover
    Flask = None

from wiki import articles as wiki_articles  # noqa: E402
from wiki import edit as wiki_edit  # noqa: E402
from wiki import migration as wiki_migration  # noqa: E402
from wiki import perimeter as wiki_perimeter  # noqa: E402
from wiki import queries  # noqa: E402
from wiki import routes_import  # noqa: E402
from wiki import routes_migration  # noqa: E402
from wiki import schema as wiki_schema  # noqa: E402
from wiki.access import collect_subjects  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts'))
import migrate_wikijs  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Схема
# ─────────────────────────────────────────────────────────────────────────────

class SchemaTest(unittest.TestCase):
    """DDL против кода: списки значений не должны существовать в двух местах."""

    def _ddl(self):
        return '\n'.join(s for s in wiki_schema._MIGRATION_STATEMENTS
                         if isinstance(s, str))

    def test_verdicts_in_ddl_match_the_code(self):
        match = re.search(r'dedup_verdict IN \((.*?)\)', self._ddl(), re.S)
        self.assertIsNotNone(match, 'в DDL нет CHECK по вердикту дедупликации')
        from_ddl = set(re.findall(r"'([a-z]+)'", match.group(1)))
        # 'unique' в VERDICT_OF_LABEL не входит: это ответ «ничего не нашли», а
        # не подпись находки. Проверяем через VERDICT_ORDER — он покрывает все.
        self.assertEqual(from_ddl, set(wiki_migration.VERDICT_ORDER),
                         'вердикты в базе и в коде разошлись')

    def test_review_actions_in_ddl_match_the_code(self):
        match = re.search(r'review_action IN \((.*?)\)', self._ddl(), re.S)
        self.assertIsNotNone(match)
        from_ddl = set(re.findall(r"'([a-z]+)'", match.group(1)))
        self.assertEqual(from_ddl, set(wiki_migration.REVIEW_ACTIONS))

    def test_repeated_import_is_blocked_by_a_unique_index(self):
        """Защита от второго переноса той же страницы — в базе, а не в скрипте."""
        ddl = self._ddl()
        self.assertIn('CREATE UNIQUE INDEX', ddl)
        self.assertIn('(source, source_id)', ddl)

    def test_decision_and_its_author_arrive_together(self):
        """reviewed_at без review_action означало бы «решено неизвестно как»."""
        self.assertIn('wiki_article_imports_review_check', self._ddl())

    def test_migration_statements_are_applied_by_init(self):
        """Таблица, которую забыли создать, — это 500 на открытии очереди."""
        source = (ROOT / 'wiki' / 'schema.py').read_text(encoding='utf-8')
        body = source[source.index('def init_wiki_schema('):]
        self.assertIn('_MIGRATION_STATEMENTS', body)

    def test_new_status_was_not_invented(self):
        """«Ждёт модерации» — состояние РАБОТЫ, а не текста.

        Седьмой статус пришлось бы разложить по корзинам, периметру ИИ,
        счётчикам и четырём местам, где статусы перечислены руками.
        """
        statuses = [s for statuses in wiki_schema.ARTICLE_BUCKETS.values()
                    for s in statuses]
        self.assertEqual(len(statuses), 6)
        self.assertNotIn('migrated', statuses)
        self.assertNotIn('pending_review', statuses)


class VerdictTest(unittest.TestCase):
    """Свод ответа поиска дублей к строке очереди."""

    def test_nothing_found_is_unique(self):
        verdict = wiki_migration.verdict_of({'items': []})
        self.assertEqual(verdict['verdict'], 'unique')
        self.assertIsNone(verdict['match_id'])

    def test_top_finding_wins_and_carries_the_section(self):
        """Раздел в подписи обязателен: на проде три пары статей с одним именем."""
        verdict = wiki_migration.verdict_of({'items': [
            {'article_id': 24, 'title': 'Рабочие сайты', 'section': 'Оператор',
             'verdict': 'дубль', 'score': 0.93},
            {'article_id': 11, 'title': 'Рабочие сайты', 'section': 'Общий сотрудник',
             'verdict': 'похоже', 'score': 0.87},
        ]})
        self.assertEqual(verdict['verdict'], 'duplicate')
        self.assertEqual(verdict['match_id'], 24)
        self.assertIn('Оператор', verdict['note'])

    def test_degraded_is_carried_over(self):
        """«Не нашли» и «не смогли посмотреть» — разные ответы."""
        self.assertTrue(wiki_migration.verdict_of(
            {'items': [], 'degraded': True})['degraded'])

    def test_unknown_label_does_not_become_a_duplicate(self):
        """Переформулировали подпись в similar.py — вердикт обязан стать мягче,
        а не превратить всё подряд в дубли."""
        verdict = wiki_migration.verdict_of({'items': [
            {'article_id': 1, 'title': 'X', 'verdict': 'совсем новое', 'score': 0.83},
        ]})
        self.assertEqual(verdict['verdict'], 'nearby')

    def test_review_action_is_validated(self):
        with self.assertRaises(ValueError):
            wiki_migration.mark_reviewed(MagicMock(), 1, action='выпустить',
                                         reviewer_id=1)


class SharedDuplicateProbeTest(unittest.TestCase):
    """Проверка на дубль — одна реализация на три двери."""

    def test_import_routes_no_longer_hold_their_own_copy(self):
        source = (ROOT / 'wiki' / 'routes_import.py').read_text(encoding='utf-8')
        self.assertNotIn('def _duplicates(', source,
                         'вернулась вторая реализация поиска дублей')
        self.assertIn('wiki_migration.duplicate_probe(', source)

    def test_migration_route_uses_the_same_function(self):
        source = (ROOT / 'wiki' / 'routes_migration.py').read_text(encoding='utf-8')
        self.assertIn('wiki_migration.duplicate_probe(', source)


# ─────────────────────────────────────────────────────────────────────────────
# Роуты
# ─────────────────────────────────────────────────────────────────────────────

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
class MigrationRouteTest(unittest.TestCase):
    def setUp(self):
        self.created = []
        self.updated = []
        self.recorded = []
        self.reviewed = []
        self.deleted = []
        self.probe_calls = []
        self.import_row = None
        self.article = {'id': 500, 'title': 'Тарифы', 'slug': 'tarify',
                        'status': 'draft', 'section_ids': [3]}
        self.permissions = {'can_read': True, 'can_edit': True, 'can_publish': True,
                            'can_delete': True}
        self.mark_ok = True

        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def fake_perimeter(_cursor, _ctx, **kwargs):
            return collect_subjects(user_id=42, otp_role='admin'), {3}, {500}

        def fake_probe(_cursor, _ctx, **kwargs):
            self.probe_calls.append(kwargs)
            return {'items': [{'article_id': 11, 'title': 'Рабочие сайты',
                               'section': 'Оператор', 'verdict': 'дубль',
                               'score': 0.93}],
                    'degraded': False}

        def fake_create(_cursor, **kwargs):
            self.created.append(kwargs)
            return 500

        def fake_update(_cursor, article_id, fields, **kwargs):
            self.updated.append((article_id, dict(fields), kwargs.get('comment')))
            return True

        def fake_record(_cursor, **kwargs):
            self.recorded.append(kwargs)

        def fake_pending_row(_cursor, article_id):
            return self.import_row

        def fake_mark(_cursor, article_id, **kwargs):
            self.reviewed.append((article_id, kwargs))
            return self.mark_ok

        patches = [
            (queries, 'load_access_context', lambda _c, _u: make_context()),
            (queries, 'granted_rule_rights', lambda _c, _s, _u: ({}, [])),
            (queries, 'log_action', lambda *a, **k: None),
            (queries, 'spaces_for_user', lambda *a, **k: [1]),
            (wiki_perimeter, 'read_perimeter', fake_perimeter),
            # routes_edit считает периметр СВОЕЙ парой запросов (_perimeter), а
            # не через wiki_perimeter — подменяем и её, иначе загрузка статьи с
            # правами отвечает 404 «не найдена» на пустом MagicMock-курсоре.
            (queries, 'allowed_section_ids', lambda *a, **k: {3}),
            (queries, 'section_rules_for_user', lambda *a, **k: {}),
            (wiki_articles, 'visible_article_ids', lambda *a, **k: {500}),
            (wiki_migration, 'duplicate_probe', fake_probe),
            (wiki_migration, 'already_imported',
             lambda _c, **k: getattr(self, 'already', None)),
            (wiki_migration, 'record', fake_record),
            (wiki_migration, 'pending_row', fake_pending_row),
            (wiki_migration, 'mark_reviewed', fake_mark),
            (wiki_migration, 'queue', lambda *a, **k: []),
            (wiki_migration, 'totals',
             lambda *a, **k: {'imported': 3, 'pending': 2, 'duplicates': 1,
                              'reviewed': 1}),
            (wiki_edit, 'create_article', fake_create),
            (wiki_edit, 'update_article', fake_update),
            (wiki_edit, 'delete_article',
             lambda _c, article_id: self.deleted.append(article_id)),
            (wiki_edit, 'slug_is_free', lambda _c, _slug: True),
            (wiki_edit, 'default_section_id', lambda *a, **k: 3),
            (wiki_articles, 'get_article', lambda _c, article_id=None: dict(self.article)),
            (wiki_articles, 'effective_permissions',
             lambda *a, **k: dict(self.permissions)),
        ]
        for module, name, replacement in patches:
            original = getattr(module, name)
            setattr(module, name, replacement)
            self.addCleanup(setattr, module, name, original)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (42, None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
            gcs={'signed_url': lambda *a, **k: 'https://x'},
        ))
        app.config['TESTING'] = True
        self.client = app.test_client()

    # ── Требование 1: ничего не публикуется ──────────────────────────────
    def test_import_cannot_publish_even_if_asked(self):
        answer = self.client.post('/api/wiki/migration/import', json={
            'title': 'Тарифы', 'content': '<p>текст</p>',
            'source_id': 77, 'status': 'published', 'is_published': True,
        })
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(answer.get_json()['status'], 'draft')
        self.assertEqual(
            [f for _id, f, _c in self.updated if f.get('status')], [],
            'перенос выпустил статью — этого не должно быть НИКОГДА')

    def test_imported_article_is_registered_with_its_verdict(self):
        self.client.post('/api/wiki/migration/import', json={
            'title': 'Рабочие сайты', 'content': '<p>x</p>', 'source_id': 25,
            'source_slug': 'Ссылки/Рабочиесайты', 'source_status': 'published',
        })
        self.assertEqual(len(self.recorded), 1)
        row = self.recorded[0]
        self.assertEqual(row['source_id'], 25)
        self.assertEqual(row['source_slug'], 'Ссылки/Рабочиесайты')
        self.assertEqual(row['dedup']['verdict'], 'duplicate')
        self.assertEqual(row['dedup']['match_id'], 11)

    # ── Требование 2: повторный прогон ───────────────────────────────────
    def test_second_run_returns_the_same_article(self):
        self.already = 500
        answer = self.client.post('/api/wiki/migration/import', json={
            'title': 'Тарифы', 'content': '<p>x</p>', 'source_id': 77})
        self.assertEqual(answer.status_code, 200)
        body = answer.get_json()
        self.assertEqual(body['id'], 500)
        self.assertFalse(body['created'])
        self.assertEqual(self.created, [], 'создали вторую копию той же страницы')

    def test_title_is_required(self):
        answer = self.client.post('/api/wiki/migration/import', json={'content': 'x'})
        self.assertEqual(answer.status_code, 400)

    # ── Требование 4: решение принимается один раз ────────────────────────
    def test_publish_marks_the_row_reviewed(self):
        self.import_row = {'article_id': 500, 'source': 'wikijs', 'source_id': 77,
                           'source_title': 'Тарифы', 'review_action': None,
                           'reviewed': False}
        answer = self.client.post('/api/wiki/migration/500/publish', json={})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.get_json()['review_action'], 'published')
        self.assertEqual(self.updated[0][1]['status'], 'published')
        self.assertEqual(self.reviewed[0][1]['action'], 'published')

    def test_already_reviewed_is_reported_not_repeated(self):
        self.import_row = {'article_id': 500, 'source': 'wikijs', 'source_id': 77,
                           'source_title': 'Тарифы', 'review_action': 'published',
                           'reviewed': True}
        answer = self.client.post('/api/wiki/migration/500/publish', json={})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.get_json()['status'], 'already_reviewed')
        self.assertEqual(self.updated, [], 'решение переписали задним числом')

    def test_article_that_was_never_migrated_is_not_decidable(self):
        self.import_row = None
        answer = self.client.post('/api/wiki/migration/500/publish', json={})
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer.get_json()['code'], 'WIKI_NOT_MIGRATED')

    # ── Требование 5: уже опубликованную только подтверждают ─────────────
    def test_already_published_is_confirmed_without_a_new_version(self):
        self.article = dict(self.article, status='published')
        self.import_row = {'article_id': 500, 'source': 'wikijs', 'source_id': 77,
                           'source_title': 'Тарифы', 'review_action': None,
                           'reviewed': False}
        answer = self.client.post('/api/wiki/migration/500/publish', json={})
        self.assertEqual(answer.get_json()['review_action'], 'kept')
        self.assertEqual(self.updated, [],
                         'выпустили заново уже опубликованную статью')
        self.assertEqual(self.reviewed[0][1]['action'], 'kept')

    # ── «Удалить» = архив ────────────────────────────────────────────────
    def test_discard_archives_and_never_deletes_rows(self):
        self.import_row = {'article_id': 500, 'source': 'wikijs', 'source_id': 77,
                           'source_title': 'Тарифы', 'review_action': None,
                           'reviewed': False}
        answer = self.client.post('/api/wiki/migration/500/discard', json={})
        self.assertEqual(answer.get_json()['review_action'], 'discarded')
        self.assertEqual(self.deleted, [500])

    # ── Права считаются по статье, а не по доступу к очереди ─────────────
    def test_publish_needs_the_right_on_that_article(self):
        self.permissions = dict(self.permissions, can_publish=False)
        self.import_row = {'article_id': 500, 'source': 'wikijs', 'source_id': 77,
                           'source_title': 'Тарифы', 'review_action': None,
                           'reviewed': False}
        answer = self.client.post('/api/wiki/migration/500/publish', json={})
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer.get_json()['required'], 'can_publish')
        self.assertEqual(self.reviewed, [], 'строку закрыли при отказе в праве')

    def test_discard_needs_the_delete_right(self):
        self.permissions = dict(self.permissions, can_delete=False)
        self.import_row = {'article_id': 500, 'source': 'wikijs', 'source_id': 77,
                           'source_title': 'Тарифы', 'review_action': None,
                           'reviewed': False}
        answer = self.client.post('/api/wiki/migration/500/discard', json={})
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(self.deleted, [])

    # ── Очередь ──────────────────────────────────────────────────────────
    def test_queue_returns_only_pending_by_default(self):
        seen = []
        original = wiki_migration.queue
        wiki_migration.queue = lambda *a, **k: seen.append(k) or []
        self.addCleanup(setattr, wiki_migration, 'queue', original)
        self.client.get('/api/wiki/migration')
        self.assertTrue(seen[0]['pending_only'])
        self.client.get('/api/wiki/migration?all=1')
        self.assertFalse(seen[1]['pending_only'])

    def test_queue_carries_the_remaining_work(self):
        body = self.client.get('/api/wiki/migration').get_json()
        self.assertEqual(body['totals']['pending'], 2)


# ─────────────────────────────────────────────────────────────────────────────
# Скрипт переноса: чистые функции
# ─────────────────────────────────────────────────────────────────────────────

class ScriptRewriteTest(unittest.TestCase):
    PAGES = [
        {'source_id': 1, 'path': 'Тарифы', 'title': 'Тарифы', 'content': ''},
        {'source_id': 2, 'path': 'yandexservice/термокороб', 'title': 'Термокороб',
         'content': ''},
    ]
    SLUGS = {1: 'tarify', 2: 'termokorob'}

    def setUp(self):
        self.mapping = migrate_wikijs.link_map(self.PAGES, self.SLUGS)

    def test_all_four_old_hosts_are_rewritten(self):
        """Одна и та же вика встречается в ссылках по четырём адресам."""
        stats = {'links': 0}
        html = ''.join(
            '<a href="http://%s/ru/%s">т</a>' % (host, 'Тарифы')
            for host in migrate_wikijs.OLD_WIKI_HOSTS)
        result = migrate_wikijs.rewrite_links(html, self.mapping, stats)
        self.assertNotIn('192.168.88.186', result)
        self.assertNotIn('217.11.79.62', result)
        self.assertEqual(result.count('?view=wiki&article=tarify'),
                         len(migrate_wikijs.OLD_WIKI_HOSTS))

    def test_relative_and_locale_prefixed_links_are_rewritten(self):
        stats = {'links': 0}
        result = migrate_wikijs.rewrite_links(
            '<a href="/Тарифы">a</a><a href="/ru/yandexservice/термокороб">b</a>',
            self.mapping, stats)
        self.assertIn('article=tarify', result)
        self.assertIn('article=termokorob', result)
        self.assertEqual(stats['links'], 2)

    def test_percent_encoded_paths_are_rewritten(self):
        """В корпусе кириллические пути встречаются и в процентах."""
        stats = {'links': 0}
        encoded = '/ru/%D0%A2%D0%B0%D1%80%D0%B8%D1%84%D1%8B'   # /ru/Тарифы
        result = migrate_wikijs.rewrite_links(
            '<a href="%s">т</a>' % encoded, self.mapping, stats)
        self.assertIn('article=tarify', result)

    def test_anchors_and_query_do_not_break_the_match(self):
        stats = {'links': 0}
        result = migrate_wikijs.rewrite_links(
            '<a href="/ru/Тарифы#эконом">т</a>', self.mapping, stats)
        self.assertIn('article=tarify', result)

    def test_external_links_are_left_alone(self):
        """345 ссылок на Google Docs и 287 на кабинет Яндекса — не наши."""
        stats = {'links': 0}
        html = ('<a href="https://docs.google.com/x">d</a>'
                '<a href="https://fleet.taxi.yandex.ru/y">f</a>')
        self.assertEqual(migrate_wikijs.rewrite_links(html, self.mapping, stats), html)
        self.assertEqual(stats['links'], 0)

    def test_link_to_a_page_we_did_not_transfer_stays_as_is(self):
        """Слага нет — значит переписывать не на что, и врать адресом нельзя."""
        stats = {'links': 0}
        html = '<a href="/ru/НетТакой">x</a>'
        self.assertEqual(migrate_wikijs.rewrite_links(html, self.mapping, stats), html)

    def test_branch_of_path(self):
        self.assertEqual(migrate_wikijs.branch_of('yandexservice/standarts/microsip'),
                         'yandexservice')
        self.assertEqual(migrate_wikijs.branch_of('Тарифы'), 'Тарифы')

    def test_only_big_branches_become_sections(self):
        """70 ветвей источника, 60 из них по одной странице: подраздел на каждую
        превратил бы дерево в плоский список с отступами."""
        pages = ([{'path': 'actions/%d' % n} for n in range(5)]
                 + [{'path': 'Одна'}, {'path': 'Другая'}])
        branches = migrate_wikijs.plan_sections(pages)
        self.assertEqual(branches, {'actions': 5})

    def test_relative_images_are_left_alone(self):
        """Файлов по этим путям нет уже и в источнике — выдумывать нечего."""
        stats = {'images': 0, 'image_failed': 0}
        api = MagicMock()
        api.dry_run = False
        html = '<img src="/6.jpg">'
        self.assertEqual(
            migrate_wikijs.rewrite_images(api, html, 1, stats), html)
        self.assertEqual(api.call.call_count, 0)
        self.assertEqual(stats['images'], 0)

    def test_base64_image_goes_to_our_storage(self):
        stats = {'images': 0, 'image_failed': 0}
        api = MagicMock()
        api.dry_run = False       # MagicMock правдив, и без этого путь не тот
        api.call.return_value = {'url': '/api/wiki/file/abc'}
        # Однопиксельный GIF — реальный base64, чтобы проверялся и декод.
        src = ('data:image/gif;base64,'
               'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')
        result = migrate_wikijs.rewrite_images(api, '<img src="%s">' % src, 7, stats)
        self.assertIn('/api/wiki/file/abc', result)
        self.assertNotIn('base64', result)
        self.assertEqual(stats['images'], 1)
        self.assertEqual(api.call.call_args[0][1], '/api/wiki/upload')

    def test_broken_base64_is_counted_not_crashed(self):
        stats = {'images': 0, 'image_failed': 0}
        api = MagicMock()
        api.dry_run = False
        html = '<img src="data:image/png;base64,!!!не base64!!!">'
        migrate_wikijs.rewrite_images(api, html, 7, stats)
        self.assertEqual(stats['image_failed'], 1)

    def test_dry_run_writes_nothing(self):
        """Холостой прогон обязан быть режимом по умолчанию и не писать."""
        api = migrate_wikijs.Api('http://x', 'l', 'p', dry_run=True)
        answer = api.call('POST', '/api/wiki/migration/import', {'title': 'т'},
                          quiet=True)
        self.assertTrue(answer['id'])
        self.assertTrue(str(answer['slug']).startswith('dry-run'))


class ScriptSafetyTest(unittest.TestCase):
    """Скрипт не должен уметь опубликовать статью — даже по недосмотру."""

    def test_script_never_sends_a_published_status(self):
        source = (ROOT / 'scripts' / 'migrate_wikijs.py').read_text(encoding='utf-8')
        body = source[source.index('def transfer('):]
        self.assertNotIn("'status': 'published'", body)
        self.assertNotIn('"status": "published"', body)

    def test_snapshot_is_kept_outside_the_repository(self):
        """Слепок — данные компании, и в git им не место."""
        self.assertNotIn(str(ROOT), migrate_wikijs.SNAPSHOT_DIR)

    def test_transferred_articles_land_closed(self):
        source = (ROOT / 'scripts' / 'migrate_wikijs.py').read_text(encoding='utf-8')
        body = source[source.index('def ensure_structure('):]
        self.assertIn("'visibility_scope': 'restricted'", body)
        self.assertNotIn("'visibility_scope': 'public'", body)


if __name__ == '__main__':
    unittest.main()
