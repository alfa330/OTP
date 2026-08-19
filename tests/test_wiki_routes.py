# -*- coding: utf-8 -*-
"""Гейтинг прав на уровне HTTP раздела «Вики».

Проверяем не SQL, а то, что декоратор wiki_route действительно закрывает
эндпоинты: оператор не должен уметь ни создать раздел, ни выдать себе правило,
ни прочитать журнал. В оригинальной вике управление правилами не имело CRUD
вообще — единственным писателем был сид, поэтому проверять там было нечего;
у нас эндпоинты есть, и их гейтинг надо фиксировать тестом.

Отдельно проверяется граница главы отдела: админ с возглавляемым отделом НЕ
является глобальным админом (штатное правило портала), и структуру чужого
отдела править не может.
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

from wiki import queries, structure  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402


def make_context(role, *, wiki_roles=(), headed=(), department_id=None, mode='auto'):
    return {
        'user_id': 42,
        'otp_role': role,
        'department_id': department_id,
        'direction_id': None,
        'headed_department_ids': list(headed),
        'group_ids': [],
        'wiki_roles': list(wiki_roles),
        'access_mode': mode,
    }


ADMIN_ROLE = {'id': 5, 'code': 'wiki_admin', 'can_read': True, 'can_create': True,
              'can_edit': True, 'can_delete': True, 'can_publish': True,
              'can_approve': True, 'can_manage_users': True,
              'can_manage_structure': True, 'can_manage_access': True}


class _RouteHarness:
    """Поднимает блюпринт вики на подменённом курсоре. Общий для наборов ниже.

    Вынесен из WikiRouteGuardTest намеренно: наследование от чужого TestCase
    тянет за собой и все его тесты, и они начинают исполняться дважды.
    """

    def build(self, context):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        cursor.rowcount = 0

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        self._orig_load = queries.load_access_context
        queries.load_access_context = lambda _cursor, _uid: dict(context)
        self.addCleanup(setattr, queries, 'load_access_context', self._orig_load)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            client_ip=lambda: '127.0.0.1',
        ))
        app.config['TESTING'] = True
        return app.test_client(), cursor


@unittest.skipIf(Flask is None, 'flask не установлен')
class WikiRouteGuardTest(_RouteHarness, unittest.TestCase):
    # ── Оператор не должен уметь ничего из управления ────────────────────
    def test_operator_cannot_manage_structure(self):
        client, _ = self.build(make_context('operator'))
        for method, url in (('post', '/api/wiki/spaces'),
                            ('post', '/api/wiki/sections'),
                            ('patch', '/api/wiki/spaces/1'),
                            ('delete', '/api/wiki/sections/1')):
            response = getattr(client, method)(url, json={'name': 'x', 'space_id': 1})
            self.assertEqual(response.status_code, 403, '%s %s' % (method, url))
            self.assertEqual(response.get_json().get('code'), 'WIKI_FORBIDDEN')

    def test_operator_cannot_touch_access_rules(self):
        client, _ = self.build(make_context('operator'))
        for method, url in (('get', '/api/wiki/access/section-rules'),
                            ('post', '/api/wiki/access/section-rules'),
                            ('delete', '/api/wiki/access/section-rules/1'),
                            ('get', '/api/wiki/access/subjects'),
                            ('get', '/api/wiki/access/effective'),
                            ('get', '/api/wiki/audit')):
            response = getattr(client, method)(url, json={})
            self.assertEqual(response.status_code, 403, '%s %s' % (method, url))

    def test_operator_can_read_own_perimeter(self):
        client, _ = self.build(make_context('operator'))
        for url in ('/api/wiki/ping', '/api/wiki/me', '/api/wiki/structure'):
            self.assertEqual(client.get(url).status_code, 200, url)

    # ── Супервайзер: правит, но не раздаёт права ─────────────────────────
    def test_supervisor_cannot_manage_access(self):
        client, _ = self.build(make_context('sv'))
        self.assertEqual(client.get('/api/wiki/audit').status_code, 403)
        self.assertEqual(client.post('/api/wiki/spaces', json={'name': 'x'}).status_code, 403)

    # ── Администратор вики ───────────────────────────────────────────────
    def test_wiki_admin_passes_guards(self):
        client, cursor = self.build(make_context('admin', wiki_roles=[ADMIN_ROLE]))
        self.assertEqual(client.get('/api/wiki/access/subjects').status_code, 200)
        self.assertEqual(client.get('/api/wiki/audit').status_code, 200)
        # без user_id эндпоинт объяснения прав обязан ругаться, а не падать
        self.assertEqual(client.get('/api/wiki/access/effective').status_code, 400)

    def test_create_space_requires_name(self):
        client, _ = self.build(make_context('admin', wiki_roles=[ADMIN_ROLE]))
        response = client.post('/api/wiki/spaces', json={})
        self.assertEqual(response.status_code, 400)

    # ── Граница главы отдела ─────────────────────────────────────────────
    def test_department_head_cannot_create_space_in_foreign_department(self):
        """Админ с возглавляемым отделом — не глобальный админ.

        Если это правило не повторить, глава одного отдела получил бы власть
        над структурой всех остальных.
        """
        client, _ = self.build(make_context('admin', headed=[7], department_id=7))
        response = client.post('/api/wiki/spaces', json={'name': 'Чужое', 'department_id': 9})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json().get('code'), 'WIKI_DEPARTMENT_SCOPE')

    def test_department_head_can_create_space_in_own_department(self):
        client, cursor = self.build(make_context('admin', headed=[7], department_id=7))
        cursor.fetchone.return_value = (123,)
        response = client.post('/api/wiki/spaces', json={'name': 'Своё', 'department_id': 7})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json().get('id'), 123)

    # ── Preflight ────────────────────────────────────────────────────────
    def test_options_never_requires_permissions(self):
        client, _ = self.build(make_context('operator'))
        for url in ('/api/wiki/audit', '/api/wiki/spaces', '/api/wiki/access/section-rules'):
            self.assertEqual(client.options(url).status_code, 204, url)


@unittest.skipIf(Flask is None, 'flask не установлен')
class ArticleCreateStatusTest(_RouteHarness, unittest.TestCase):
    """Создание статьи с публикацией. Раньше статус молча терялся.

    create_article всегда пишет 'draft', а кнопка «Опубликовать» в редакторе
    присылает status='published' — статья оставалась черновиком, но интерфейс
    рапортовал «Статья опубликована». Ложный успех хуже отказа: человек уходит
    уверенным, что дело сделано, и узнаёт правду случайно, как и вышло у
    владельца со статьёй «Реестр акций».
    """

    def _client(self, *, can_publish):
        from unittest.mock import patch

        from wiki import articles as wiki_articles
        from wiki import edit as wiki_edit
        from wiki import queries as wiki_queries

        client, _cursor = self.build(make_context('admin', wiki_roles=[ADMIN_ROLE]))
        self.updates = []

        patches = [
            patch.object(wiki_edit, 'slug_is_free', return_value=True),
            patch.object(wiki_edit, 'create_article', return_value=777),
            patch.object(wiki_edit, 'update_article',
                         side_effect=lambda _c, aid, fields, **kw:
                             self.updates.append((aid, dict(fields))) or True),
            patch.object(wiki_articles, 'visible_article_ids', return_value={777}),
            patch.object(wiki_articles, 'get_article',
                         return_value={'id': 777, 'slug': 'reestr', 'title': 'Реестр',
                                       'visibility_mode': 'inherit', 'strict_mode': False,
                                       'author_id': 42, 'owner_user_id': None,
                                       'section_ids': []}),
            patch.object(wiki_articles, 'article_rules_for_user', return_value={}),
            patch.object(wiki_articles, 'effective_permissions',
                         return_value={'can_edit': True, 'can_publish': can_publish}),
            patch.object(wiki_queries, 'allowed_section_ids', return_value={1}),
            patch.object(wiki_queries, 'section_rules_for_user', return_value={}),
            patch.object(wiki_queries, 'log_action', return_value=None),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return client

    def test_published_request_actually_publishes(self):
        client = self._client(can_publish=True)
        response = client.post('/api/wiki/articles',
                               json={'title': 'Реестр акций', 'status': 'published'})
        self.assertEqual(201, response.status_code)
        self.assertEqual('published', response.get_json()['status'])
        self.assertEqual([(777, {'status': 'published'})], self.updates)

    def test_without_publish_right_stays_draft_and_says_so(self):
        """Отказ должен быть ВИДЕН: статус в ответе, а не тихий черновик."""
        client = self._client(can_publish=False)
        response = client.post('/api/wiki/articles',
                               json={'title': 'Реестр акций', 'status': 'published'})
        self.assertEqual(201, response.status_code)
        self.assertEqual('draft', response.get_json()['status'])
        self.assertEqual([], self.updates)

    def test_saving_refreshes_the_assistant_index(self):
        """Публикация обязана обновлять индекс помощника — иначе он статьи не видит.

        Именно на этом споткнулся владелец: статья «Реестр акций таксопарка
        iGroup» была опубликована и входила в периметр помощника, но кусков у неё
        было ноль — индекс наполнялся только вручную, и со стороны это выглядело
        как «ИИ игнорирует статью».
        """
        from unittest.mock import patch

        from wiki.ai import embed as ai_embed
        from wiki.ai import index as ai_index

        client = self._client(can_publish=True)
        with patch.object(ai_index, 'reindex_article',
                          return_value={'action': 'indexed', 'chunks': 7}) as reindex,              patch.object(ai_embed, 'embed_missing',
                          return_value={'embedded': 7}) as embed:
            response = client.post('/api/wiki/articles',
                                   json={'title': 'Реестр акций', 'status': 'published'})
        self.assertEqual(1, reindex.call_count)
        self.assertEqual(1, embed.call_count)
        self.assertEqual('indexed', response.get_json()['ai_index']['action'])

    def test_index_failure_does_not_lose_the_article(self):
        """Недоступный эмбеддер не должен стоить человеку правки."""
        from unittest.mock import patch

        from wiki.ai import index as ai_index

        client = self._client(can_publish=True)
        with patch.object(ai_index, 'reindex_article',
                          side_effect=RuntimeError('эмбеддер недоступен')):
            response = client.post('/api/wiki/articles',
                                   json={'title': 'Реестр акций', 'status': 'published'})
        self.assertEqual(201, response.status_code)
        self.assertEqual('published', response.get_json()['status'])
        self.assertEqual('failed', response.get_json()['ai_index']['action'])

    def test_draft_creation_does_not_touch_status(self):
        client = self._client(can_publish=True)
        response = client.post('/api/wiki/articles', json={'title': 'Реестр акций'})
        self.assertEqual('draft', response.get_json()['status'])
        self.assertEqual([], self.updates)


class SlugTest(unittest.TestCase):
    def test_cyrillic_is_transliterated(self):
        from wiki.routes_structure import _slugify
        self.assertEqual(_slugify('Общий отдел'), 'obschiy-otdel')
        self.assertEqual(_slugify('Аренда транспорта'), 'arenda-transporta')

    def test_never_empty(self):
        from wiki.routes_structure import _slugify
        self.assertTrue(_slugify('!!!'))
        self.assertTrue(_slugify(''))

    def test_no_leading_or_trailing_dashes(self):
        from wiki.routes_structure import _slugify
        slug = _slugify('  — Тест — ')
        self.assertFalse(slug.startswith('-'))
        self.assertFalse(slug.endswith('-'))


class RulePermissionNormalisationTest(unittest.TestCase):
    """Право записи без чтения бессмысленно — правило должно само это чинить."""

    @unittest.skipIf(Flask is None, 'flask не установлен')
    def test_write_permission_implies_read(self):
        captured = {}

        def fake_upsert(cursor, **kwargs):
            captured.update(kwargs)
            return 1

        original = structure.upsert_section_rule
        structure.upsert_section_rule = fake_upsert
        self.addCleanup(setattr, structure, 'upsert_section_rule', original)

        original_exists = structure.section_exists
        structure.section_exists = lambda cursor, sid: 1
        self.addCleanup(setattr, structure, 'section_exists', original_exists)

        guard = WikiRouteGuardTest('test_options_never_requires_permissions')
        guard.addCleanup = self.addCleanup
        client, _ = guard.build(make_context('admin', wiki_roles=[ADMIN_ROLE]))

        response = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'otp_role', 'subject_role': 'operator',
            'can_read': False, 'can_edit': True,
        })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(captured['permissions']['can_read'],
                        'правка без чтения — противоречие, can_read должен включиться сам')


@unittest.skipIf(Flask is None, 'flask не установлен')
class SectionDepartmentBranchTest(_RouteHarness, unittest.TestCase):
    """Отдел ветки у раздела: «ОП» и «СЗоВ» помечаются отделом, а не должностью.

    Поле уже было и было снято (b264a080) — теперь оно вернулось как переключатель
    в форме раздела: доступ внутри ветки считается по ЭТОМУ отделу, иначе правило
    на роль 'sv' пробило бы границу и супервайзер продаж увидел бы ОТП.

    Проверяем не UI, а контракт: department_id доезжает до SQL, section_kind
    выводится из него (вторым полем не приходит) и повтор отдела у соседа
    отвечает внятной ошибкой, а не 500 от уникального индекса.
    """

    def _admin(self):
        return self.build(make_context('admin', wiki_roles=[ADMIN_ROLE]))

    def test_create_section_stores_department_and_kind(self):
        captured = {}

        def fake_create(cursor, **kwargs):
            captured.update(kwargs)
            return 77

        self.addCleanup(setattr, structure, 'create_section', structure.create_section)
        structure.create_section = fake_create
        self.addCleanup(setattr, structure, 'free_section_slug', structure.free_section_slug)
        structure.free_section_slug = lambda cursor, space_id, base, exclude_id=None: base
        self.addCleanup(setattr, structure, 'department_branch_taken',
                        structure.department_branch_taken)
        structure.department_branch_taken = lambda cursor, **kwargs: None

        client, cursor = self._admin()
        cursor.fetchone.return_value = (None,)   # пространство без отдела
        response = client.post('/api/wiki/sections',
                               json={'name': 'ОП', 'space_id': 1, 'department_id': 367})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(captured['department_id'], 367)

    def test_section_kind_follows_department(self):
        """Вид раздела выводится из отдела, а не задаётся вторым полем.

        Два независимых поля разъезжаются: «ветка без отдела» проскочила бы мимо
        уникального индекса uq_wiki_section_department и перестала быть веткой.
        """
        self.assertEqual(structure.section_kind_of(367), 'department')
        self.assertEqual(structure.section_kind_of(None), 'common')
        self.assertEqual(structure.section_kind_of(0), 'common')

    def test_duplicate_department_branch_answers_clearly(self):
        """Повтор отдела у соседа — 400 с именем занявшей ветки, а не 500.

        На (space_id, parent, department_id) висит частичный UNIQUE. Без явной
        проверки человек видел бы «Внутреннюю ошибку раздела Вики» — ровно та
        история, что была со слагом раздела (2817ebcc).
        """
        self.addCleanup(setattr, structure, 'department_branch_taken',
                        structure.department_branch_taken)
        structure.department_branch_taken = lambda cursor, **kwargs: 'ОП'

        client, cursor = self._admin()
        cursor.fetchone.return_value = (None,)
        response = client.post('/api/wiki/sections',
                               json={'name': 'Продажи', 'space_id': 1, 'department_id': 367})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get('code'), 'WIKI_DEPARTMENT_BRANCH_TAKEN')
        self.assertIn('ОП', response.get_json().get('error', ''))

    def test_patch_clears_department_when_null(self):
        """Пустой отдел снимает пометку ветки, а не молча ничего не делает.

        Форма шлёт ключ всегда, в том числе пустым: без этого раздел, у которого
        отдел сняли, остался бы веткой навсегда.
        """
        captured = {}

        self.addCleanup(setattr, structure, 'update_section', structure.update_section)
        structure.update_section = lambda cursor, sid, fields: (captured.update(fields), True)[1]
        self.addCleanup(setattr, structure, 'department_branch_taken',
                        structure.department_branch_taken)
        structure.department_branch_taken = lambda cursor, **kwargs: None

        client, cursor = self._admin()
        # SELECT в начале обработчика: имя, отдел пространства, space_id, родитель.
        cursor.fetchone.return_value = ('Оператор', None, 1, 5)
        response = client.patch('/api/wiki/sections/9', json={'department_id': None})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(captured['department_id'])
        self.assertEqual(captured['section_kind'], 'common')

    def test_subjects_catalog_open_to_structure_manager(self):
        """Справочник отделов нужен форме раздела, а её открывает глава отдела.

        Глава отдела мастер-ключа can_manage_access не носит
        (department-head-permission-semantics), и пока гейт был только на нём,
        селектор «Отдел ветки» у него оставался пустым.
        """
        client, _ = self.build(make_context('admin', headed=[7], department_id=7))
        self.assertEqual(client.get('/api/wiki/access/subjects').status_code, 200)
        # Гейт правил он теперь проходит (потолок 30), спотыкается уже о пустое
        # тело — 400, а не 403. Раньше здесь стоял 403: до появления лестницы
        # правила раздавал только носитель мастер-ключа.
        self.assertEqual(
            client.post('/api/wiki/access/section-rules', json={}).status_code, 400)


@unittest.skipIf(Flask is None, 'flask не установлен')
class GrantLadderRouteTest(_RouteHarness, unittest.TestCase):
    """Лестница выдачи на уровне HTTP.

    Чистая логика проверена в test_wiki_access; здесь важно другое — что гейт
    реально стоит на эндпоинте. Фронт гасит недоступные строки, но запрос можно
    послать и мимо него, а «зернистость», которую держит только интерфейс, —
    это отсутствие зернистости.
    """

    def _stub_section(self, department_id=1):
        """Раздел существует и лежит в ветке указанного отдела."""
        self.addCleanup(setattr, structure, 'section_exists', structure.section_exists)
        structure.section_exists = lambda cursor, sid: 1
        self.addCleanup(setattr, structure, 'section_branch_department',
                        structure.section_branch_department)
        structure.section_branch_department = lambda cursor, sid: department_id

    def test_operator_and_trainer_cannot_grant(self):
        for role in ('operator', 'trainer'):
            client, _ = self.build(make_context(role, department_id=1))
            r = client.post('/api/wiki/access/section-rules', json={
                'section_id': 1, 'subject_type': 'department', 'subject_id': 1})
            self.assertEqual(r.status_code, 403, role)
            self.assertEqual(r.get_json().get('code'), 'WIKI_FORBIDDEN')
            # И списка сотрудников им тоже не полагается.
            self.assertEqual(client.get('/api/wiki/access/people').status_code, 403, role)

    def test_supervisor_grants_operator_but_not_supervisor(self):
        self._stub_section(department_id=1)
        captured = {}
        self.addCleanup(setattr, structure, 'upsert_section_rule', structure.upsert_section_rule)
        structure.upsert_section_rule = lambda cursor, **kw: (captured.update(kw), 7)[1]

        client, _ = self.build(make_context('sv', department_id=1))
        ok = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 1,
            'can_read': True})
        self.assertEqual(ok.status_code, 201)
        self.assertIsNone(captured['min_role_level'])

        denied = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 1,
            'min_role_level': 30, 'can_read': True})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json().get('code'), 'WIKI_GRANT_CEILING')

    def test_supervisor_cannot_write_personal_rule_on_himself(self):
        """Дыра, ради которой проверяется роль адресата, а не только порог.

        Порог у правила на человека пуст, поэтому одна лишь проверка порога
        пропустила бы «СВ выписывает правило на себя» — выдачу себе полного
        доступа к разделу своего же отдела.
        """
        self._stub_section(department_id=1)
        client, cursor = self.build(make_context('sv', department_id=1))
        cursor.fetchone.return_value = ('sv', 1)      # роль и отдел адресата
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'user', 'subject_id': 42,
            'can_read': True, 'can_delete': True})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('code'), 'WIKI_GRANT_CEILING')

    def test_supervisor_stopped_at_foreign_department(self):
        """Граница отдела: СЗоВ не настраивает ветки продаж."""
        self._stub_section(department_id=367)        # раздел в чужом отделе
        client, _ = self.build(make_context('sv', department_id=1))
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 1,
            'can_read': True})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('code'), 'WIKI_DEPARTMENT_SCOPE')

    def test_director_has_no_department_border(self):
        self._stub_section(department_id=367)
        self.addCleanup(setattr, structure, 'upsert_section_rule', structure.upsert_section_rule)
        structure.upsert_section_rule = lambda cursor, **kw: 9
        client, _ = self.build(make_context('super_admin', department_id=1))
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 367,
            'min_role_level': 40, 'can_read': True})
        self.assertEqual(r.status_code, 201)

    def test_delete_checks_ladder_too(self):
        """Снять чужое правило — такое же вмешательство, как его выписать.

        Без этой проверки супервайзер отобрал бы доступ у руководителя своего
        отдела: удаление гейтилось только мастер-ключом, которого у него нет,
        а после открытия эндпоинта он попал бы туда беспрепятственно.
        """
        self.addCleanup(setattr, structure, 'section_branch_department',
                        structure.section_branch_department)
        structure.section_branch_department = lambda cursor, sid: 1
        client, cursor = self.build(make_context('sv', department_id=1))
        cursor.fetchone.return_value = (1, 40)       # правило на уровень руководителя
        r = client.delete('/api/wiki/access/section-rules/5')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('code'), 'WIKI_GRANT_CEILING')


@unittest.skipIf(Flask is None, 'flask не установлен')
class DirectoryRouteTest(_RouteHarness, unittest.TestCase):
    """Гейт справочников на уровне HTTP.

    Чистое правило проверено в test_wiki_access; здесь важно, что оно реально
    стоит на КАЖДОЙ точке записи. Их семь (парк, акция, офис — создание,
    правка, архив, плюс разбор ссылки 2ГИС), и раньше половина гейтилась
    декоратором, половина проверкой внутри: пропустить одну легко.
    """

    WRITE_ROUTES = (
        ('post', '/api/wiki/parks'),
        ('patch', '/api/wiki/parks/1'),
        ('delete', '/api/wiki/parks/1'),
        ('post', '/api/wiki/promotions'),
        ('patch', '/api/wiki/promotions/1'),
        ('delete', '/api/wiki/promotions/1'),
        ('post', '/api/wiki/offices'),
        ('patch', '/api/wiki/offices/1'),
        ('delete', '/api/wiki/offices/1'),
        ('post', '/api/wiki/offices/resolve-map'),
    )

    def test_operator_is_refused_everywhere(self):
        client, _ = self.build(make_context('operator'))
        for method, url in self.WRITE_ROUTES:
            r = getattr(client, method)(url, json={'name': 'x'})
            self.assertEqual(r.status_code, 403, '%s %s' % (method, url))
            self.assertEqual(r.get_json().get('code'), 'WIKI_FORBIDDEN',
                             '%s %s' % (method, url))

    def test_supervisor_passes_the_gate_everywhere(self):
        """Супервайзер проходит гейт: дальше он спотыкается о данные, не о права.

        Проверяем именно «не 403»: за гейтом идут запросы к подменённому
        курсору, и осмысленного 2xx там не выйдет — важно, что отказ по правам
        больше не наступает.
        """
        client, _ = self.build(make_context('sv'))
        for method, url in self.WRITE_ROUTES:
            r = getattr(client, method)(url, json={'name': 'Парк'})
            self.assertNotEqual(r.status_code, 403, '%s %s' % (method, url))

    def test_trainer_passes_the_gate_too(self):
        client, _ = self.build(make_context('trainer'))
        r = client.post('/api/wiki/parks', json={'name': 'Парк'})
        self.assertNotEqual(r.status_code, 403)

    def test_can_manage_flag_follows_the_same_rule(self):
        """Флаг для интерфейса и гейт считаются одинаково.

        Разойдись они — супервайзер получил бы экран без кнопок «Изменить»
        (или наоборот, кнопки с отказом по нажатию).
        """
        for role, expected in (('sv', True), ('trainer', True), ('operator', False)):
            client, cursor = self.build(make_context(role))
            cursor.fetchall.return_value = []
            for url in ('/api/wiki/parks', '/api/wiki/offices'):
                body = client.get(url).get_json()
                self.assertEqual(body.get('can_manage'), expected,
                                 '%s %s' % (role, url))


if __name__ == '__main__':
    unittest.main()
