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

import datetime
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

    def build(self, context, granted=None):
        """granted — права, УЖЕ выписанные человеку правилами (см.
        queries.granted_rule_rights). Заглушка обязательна: курсор здесь один на
        все запросы и отвечает всем одними и теми же строками, а расчёт
        способностей ходит в базу первым — без подмены он разобрал бы чужую
        выдачу как свою и сдвинул нумерацию execute()."""
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

        self._orig_granted = queries.granted_rule_rights
        queries.granted_rule_rights = lambda _c, _s, _u: (dict(granted or {}), [])
        self.addCleanup(setattr, queries, 'granted_rule_rights', self._orig_granted)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            # Гейт QR-подтверждения здесь всегда открыт: эти наборы
            # проверяют права раздела, а сам гейт — test_sensitive_section_qr_gate.
            sensitive_access_granted=lambda _user_id, cursor=None: True,
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

    # ── Руководитель не трогает структуру ────────────────────────────────
    def test_head_cannot_touch_structure_even_in_own_department(self):
        """«Не может добавлять разделы и подразделы, не может их удалять либо править».

        Решение владельца 21.08.2026. Раньше глава отдела заводил пространства
        и разделы у себя (гейт был на границе отдела); теперь дерево целиком за
        директором, а руководителю остаётся выдача доступа.
        """
        client, _ = self.build(make_context('admin', headed=[7], department_id=7))
        for method, url in (('post', '/api/wiki/spaces'),
                            ('post', '/api/wiki/sections'),
                            ('patch', '/api/wiki/spaces/1'),
                            ('patch', '/api/wiki/sections/1'),
                            ('delete', '/api/wiki/sections/1')):
            response = getattr(client, method)(
                url, json={'name': 'Своё', 'space_id': 1, 'department_id': 7})
            self.assertEqual(response.status_code, 403, '%s %s' % (method, url))
            self.assertEqual(response.get_json().get('required'), 'can_manage_structure')

    def test_head_keeps_access_granting(self):
        """Отобрана структура, но не выдача доступа: вкладка ему нужна ради неё."""
        client, _ = self.build(make_context('admin', headed=[7], department_id=7))
        self.assertEqual(client.get('/api/wiki/access/people').status_code, 200)
        self.assertEqual(client.get('/api/wiki/access/subjects').status_code, 200)

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
        # Роут читает у пространства СТАТУС: в архивное пространство раздел
        # не заводится. Отдел пространства к правам на раздел отношения
        # больше не имеет — границу держит список wiki_space_departments.
        cursor.fetchone.return_value = ('active',)
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
        cursor.fetchone.return_value = ('active',)
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
        # SELECT в начале обработчика: имя, отдел пространства, space_id,
        # родитель, отдел самого раздела.
        cursor.fetchone.return_value = ('Оператор', None, 1, 5, None)
        response = client.patch('/api/wiki/sections/9', json={'department_id': None})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(captured['department_id'])
        self.assertEqual(captured['section_kind'], 'common')

    def test_subjects_catalog_open_to_the_one_who_grants(self):
        """Справочник нужен форме правила, а её открывает раздающий доступ.

        Способностей can_manage_* у руководителя больше нет вовсе, и пока гейт
        стоял только на них, списки в форме приезжали пустыми.
        """
        client, _ = self.build(make_context('admin', headed=[7], department_id=7))
        self.assertEqual(client.get('/api/wiki/access/subjects').status_code, 200)
        # Гейт правил он проходит по лестнице, спотыкается уже о пустое тело —
        # 400, а не 403. Раньше здесь стоял 403: до появления лестницы правила
        # раздавал только носитель мастер-ключа.
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

    def test_cannot_grant_a_right_you_lack_yourself(self):
        """Выписать можно только то, что умеешь сам.

        До 21.08.2026 эта граница держалась случайно: право сверх способностей
        раздающего всё равно гасло у адресата. Теперь выписанное право работает,
        и «супервайзер выдал оператору удаление, которого у самого супервайзера
        нет» стало бы настоящей выдачей — мимо лестницы GRANT_CEILING.
        """
        self._stub_section(department_id=1)
        client, _ = self.build(make_context('sv', department_id=1))
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 1,
            'can_read': True, 'can_delete': True})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('code'), 'WIKI_GRANT_BEYOND_SELF')
        self.assertIn('can_delete', r.get_json().get('required'))

    def test_grant_of_own_rights_still_passes(self):
        """Обратная половина: то, что у раздающего есть, он выписывает свободно."""
        self._stub_section(department_id=1)
        self.addCleanup(setattr, structure, 'upsert_section_rule', structure.upsert_section_rule)
        structure.upsert_section_rule = lambda cursor, **kw: 7

        client, _ = self.build(make_context('sv', department_id=1))
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 1,
            'can_read': True, 'can_edit': True, 'can_publish': True})
        self.assertEqual(r.status_code, 201, r.get_json())

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

    def test_head_grants_his_own_level_inside_his_department(self):
        """Руководитель СЗоВ выписывает правило «не ниже руководителя» у себя.

        Это та самая жалоба: строка «Руководитель группы» была заперта, и глава
        отдела не мог настроить собственную ветку до конца. Отдел при этом
        по-прежнему сверяется — см. соседний тест.
        """
        self._stub_section(department_id=1)
        captured = {}
        self.addCleanup(setattr, structure, 'upsert_section_rule', structure.upsert_section_rule)
        structure.upsert_section_rule = lambda cursor, **kw: (captured.update(kw), 8)[1]

        client, _ = self.build(make_context('admin', department_id=1, headed=(1,)))
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 1,
            'min_role_level': 40, 'can_read': True})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(captured['min_role_level'], 40)

    def test_head_grants_a_colleague_in_his_department(self):
        """«Выдавать конкретным людям своего отдела» — это весь отдел.

        Раньше коллега-руководитель отсекался по потолку (403 GRANT_CEILING),
        хотя сидит в том же отделе; теперь его отсекает только чужой отдел.
        """
        self._stub_section(department_id=1)
        self.addCleanup(setattr, structure, 'upsert_section_rule', structure.upsert_section_rule)
        structure.upsert_section_rule = lambda cursor, **kw: 11

        client, cursor = self.build(make_context('admin', department_id=1, headed=(1,)))
        cursor.fetchone.return_value = ('admin', 1)   # роль и отдел адресата
        ok = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'user', 'subject_id': 77, 'can_read': True})
        self.assertEqual(ok.status_code, 201)

        cursor.fetchone.return_value = ('admin', 367)  # тот же ранг, чужой отдел
        denied = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'user', 'subject_id': 78, 'can_read': True})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json().get('code'), 'WIKI_DEPARTMENT_SCOPE')

    def test_head_stopped_at_foreign_department(self):
        """Потолок вырос, граница отдела осталась: ОП чужому руководителю не отдаём."""
        self._stub_section(department_id=367)
        client, _ = self.build(make_context('admin', department_id=1, headed=(1,)))
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 367,
            'min_role_level': 40, 'can_read': True})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('code'), 'WIKI_DEPARTMENT_SCOPE')

    def test_head_cannot_grant_at_director_level(self):
        """Свой уровень — да, директорский — нет."""
        self._stub_section(department_id=1)
        client, _ = self.build(make_context('admin', department_id=1, headed=(1,)))
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 1,
            'min_role_level': 50, 'can_read': True})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('code'), 'WIKI_GRANT_CEILING')

    def test_director_has_no_department_border(self):
        self._stub_section(department_id=367)
        self.addCleanup(setattr, structure, 'upsert_section_rule', structure.upsert_section_rule)
        structure.upsert_section_rule = lambda cursor, **kw: 9
        client, _ = self.build(make_context('super_admin', department_id=1))
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 367,
            'min_role_level': 40, 'can_read': True})
        self.assertEqual(r.status_code, 201)

    # ── Граница отдела для АДРЕСАТА правила ─────────────────────────────
    #
    # Раздел проверялся, адресат — нет: на СВОЁМ разделе супервайзер выписывал
    # правило кому угодно. Ниже три двери, которые были открыты.

    def _capture_upsert(self):
        captured = {}
        self.addCleanup(setattr, structure, 'upsert_section_rule',
                        structure.upsert_section_rule)
        structure.upsert_section_rule = lambda cursor, **kw: (captured.update(kw), 12)[1]
        return captured

    def test_supervisor_cannot_open_his_section_to_a_foreign_department(self):
        """Свой раздел — чужому отделу. Порог пуст, потолок такое пропускал."""
        self._stub_section(department_id=1)
        client, _ = self.build(make_context('sv', department_id=1))
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'department', 'subject_id': 367,
            'can_read': True})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('code'), 'WIKI_DEPARTMENT_SCOPE')

    def test_supervisor_cannot_write_a_company_wide_role_rule(self):
        """Правило на должность отдела не знает: оно открывает раздел ВСЕМ.

        Именно этот субъект форма предлагала супервайзеру наравне с остальными,
        так что дойти до «раздел СЗоВ виден всей компании» можно было в два
        нажатия и без всякого умысла.
        """
        self._stub_section(department_id=1)
        client, _ = self.build(make_context('sv', department_id=1))
        for subject in ({'subject_type': 'otp_role', 'subject_role': 'operator'},
                        {'subject_type': 'wiki_role', 'subject_id': 2}):
            r = client.post('/api/wiki/access/section-rules', json=dict(
                subject, section_id=1, can_read=True))
            self.assertEqual(r.status_code, 403, subject)
            self.assertEqual(r.get_json().get('code'), 'WIKI_DEPARTMENT_SCOPE', subject)

    def test_supervisor_grants_his_own_group_but_not_a_foreign_one(self):
        """Группа и направление сверяются по СВОЕМУ department_id."""
        self._stub_section(department_id=1)
        captured = self._capture_upsert()
        client, cursor = self.build(make_context('sv', department_id=1))

        cursor.fetchone.return_value = (1,)          # группа своего отдела
        ok = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'group', 'subject_id': 10,
            'can_read': True})
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(captured['subject_id'], 10)

        cursor.fetchone.return_value = (367,)        # группа отдела продаж
        denied = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'group', 'subject_id': 13,
            'can_read': True})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json().get('code'), 'WIKI_DEPARTMENT_SCOPE')

    def test_director_still_writes_company_wide_rules(self):
        """У директора границы нет — правило на должность остаётся его инструментом."""
        self._stub_section(department_id=1)
        self._capture_upsert()
        client, _ = self.build(make_context('super_admin', department_id=1))
        r = client.post('/api/wiki/access/section-rules', json={
            'section_id': 1, 'subject_type': 'otp_role', 'subject_role': 'operator',
            'can_read': True})
        self.assertEqual(r.status_code, 201)

    def test_form_learns_the_border_from_the_server(self):
        """Границу считает сервер и присылает её форме — оба списка от неё.

        Второй расчёт на клиенте разошёлся бы с первым, и расходится он всегда
        в сторону «показали строку, а сервер ответил 403».
        """
        self.addCleanup(setattr, structure, 'list_section_rules',
                        structure.list_section_rules)
        structure.list_section_rules = lambda cursor, section_id=None: []
        self._stub_section(department_id=1)

        client, _ = self.build(make_context('sv', department_id=1))
        body = client.get('/api/wiki/access/section-rules?section_id=1').get_json()
        self.assertEqual(body['grant_departments'], [1])
        self.assertEqual(body['grant_ceiling'], 10)

        client, _ = self.build(make_context('super_admin', department_id=1))
        director = client.get('/api/wiki/access/section-rules?section_id=1').get_json()
        self.assertIsNone(director['grant_departments'])

    def test_subject_catalog_is_narrowed_to_the_department(self):
        """Справочник субъектов сужается тем же отделом, что и проверка."""
        seen = {}
        self.addCleanup(setattr, structure, 'subject_catalog', structure.subject_catalog)
        structure.subject_catalog = lambda cursor, department_ids=None: (
            seen.update({'departments': department_ids}),
            {'department': [], 'direction': [], 'group': [], 'wiki_role': []})[1]

        client, _ = self.build(make_context('sv', department_id=1))
        body = client.get('/api/wiki/access/subjects').get_json()
        self.assertEqual(seen['departments'], [1])
        # Роль в системе супервайзеру не предлагается вовсе: она действует по
        # всей компании, и сервер такое правило отвергает.
        self.assertEqual(body['otp_role'], [])
        self.assertEqual(body['grant_departments'], [1])

        client, _ = self.build(make_context('super_admin', department_id=1))
        director = client.get('/api/wiki/access/subjects').get_json()
        self.assertIsNone(seen['departments'])
        self.assertTrue(director['otp_role'])

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
        # Раздел, потолок должности, субъект и шесть прав: обработчик забирает
        # правило целиком, чтобы записать в журнал, У КОГО отобрали доступ.
        cursor.fetchone.return_value = (1, 40, 'department', 1, None,
                                        True, False, False, False, False, False)
        r = client.delete('/api/wiki/access/section-rules/5')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('code'), 'WIKI_GRANT_CEILING')


@unittest.skipIf(Flask is None, 'flask не установлен')
class AuditReadTest(_RouteHarness, unittest.TestCase):
    """Чтение журнала: то, из-за чего вкладка была нечитаемой.

    Проверяем не оформление, а контракт данных — время, названия объектов и
    честность фильтра. Каждая из трёх вещей однажды уже была сломана.
    """

    ROW = (305, 2, 'Ядигаров Руслан', 'rule.upsert', 'section', 31,
           'Оператор', None, True, 'Отдел продаж', None, None,
           {'subject_type': 'department'},
           datetime.datetime(2026, 8, 19, 17, 5, 4))

    def _client(self):
        client, cursor = self.build(make_context('admin', wiki_roles=[ADMIN_ROLE]))
        cursor.fetchall.return_value = [self.ROW]
        cursor.fetchone.return_value = (1,)
        return client, cursor

    def test_time_is_iso_string_not_gmt(self):
        """Время отдаём строкой ISO без зоны.

        В базе лежит местное алматинское время (schema._NOW), а datetime Flask
        сериализует как «… GMT» — браузер прибавлял к нему ещё пять часов, и
        журнал показывал события на пять часов вперёд.
        """
        client, _ = self._client()
        item = client.get('/api/wiki/audit').get_json()['items'][0]
        self.assertEqual('2026-08-19T17:05:04', item['created_at'])
        self.assertNotIn('GMT', item['created_at'])

    def test_row_carries_names_not_only_identifiers(self):
        """Название объекта и получателя права — в ответе.

        Без них строка журнала звучала как «выдано право субъекту 367 на
        раздел 31», то есть не сообщала ничего.
        """
        client, _ = self._client()
        item = client.get('/api/wiki/audit').get_json()['items'][0]
        self.assertEqual('Оператор', item['entity_name'])
        self.assertEqual('Отдел продаж', item['subject_name'])
        self.assertTrue(item['entity_alive'])

    # Первый execute — сама выборка; следом идут COUNT и раскладка по группам.
    @staticmethod
    def _list_query(cursor):
        return cursor.execute.call_args_list[0][0]

    def test_group_filter_selects_whole_group(self):
        client, cursor = self._client()
        client.get('/api/wiki/audit?group=access')
        self.assertIn(list(structure.AUDIT_GROUPS['access']), self._list_query(cursor)[1])

    def test_unknown_group_does_not_filter(self):
        """Опечатка в фильтре не должна выдавать пустой журнал за «событий нет»."""
        client, cursor = self._client()
        client.get('/api/wiki/audit?group=нет-такой')
        # Слово WHERE есть и внутри подзапросов, разворачивающих имя субъекта,
        # поэтому смотрим не на текст, а на параметры: кроме окна их быть не
        # должно.
        self.assertEqual([100, 0], self._list_query(cursor)[1])

    def test_one_letter_search_is_ignored(self):
        """По одной букве ILIKE перебирает всю таблицу и возвращает почти всё."""
        client, cursor = self._client()
        client.get('/api/wiki/audit?q=a')
        self.assertNotIn('ILIKE', self._list_query(cursor)[0])
        cursor.execute.reset_mock()
        client.get('/api/wiki/audit?q=ab')
        self.assertIn('ILIKE', self._list_query(cursor)[0])

    def test_broken_date_does_not_reach_sql(self):
        client, cursor = self._client()
        r = client.get('/api/wiki/audit?from=не-дата&to=2026-13-40')
        self.assertEqual(200, r.status_code)
        self.assertNotIn('created_at >=', self._list_query(cursor)[0])

    def test_totals_only_on_first_page(self):
        """Считать COUNT на каждой догруженной странице незачем: фильтр тот же."""
        client, _ = self._client()
        first = client.get('/api/wiki/audit').get_json()
        self.assertIn('total', first)
        self.assertIn('counts', first)
        more = client.get('/api/wiki/audit?offset=50').get_json()
        self.assertNotIn('total', more)


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

    FULL_RULE = {'can_read': True, 'can_create': True, 'can_edit': True,
                 'can_delete': True, 'can_publish': True, 'can_approve': True}

    def test_granted_rule_does_not_open_the_directories(self):
        """Правило раздела — про содержимое РАЗДЕЛА, а справочники общие.

        С 21.08.2026 выписанное правилом право поднимает способность
        (queries.load_capabilities), и без отдельной оговорки персональное
        правило на один раздел вики отдало бы оператору телефоны всех парков и
        офисов компании. Гейт справочников намеренно остался на способностях
        ДОЛЖНОСТИ (routes_parks._may_edit, routes_offices._may_edit).
        """
        client, _ = self.build(make_context('operator'), granted=self.FULL_RULE)
        for method, url in self.WRITE_ROUTES:
            r = getattr(client, method)(url, json={'name': 'x'})
            self.assertEqual(r.status_code, 403, '%s %s' % (method, url))

    def test_granted_rule_does_not_open_mandatory_reading(self):
        """Назначение обязательного чтения — про ЛЮДЕЙ, а не про раздел.

        Эндпоинт раскрывает department_id в весь состав отдела, поэтому он
        объявлен с capability_from_role=True (routes_ack).
        """
        client, _ = self.build(make_context('operator'), granted=self.FULL_RULE)
        r = client.post('/api/wiki/articles/7/ack/assign', json={'department_id': 1})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json().get('required'), 'can_publish')
        self.assertEqual(client.get('/api/wiki/articles/7/ack/report').status_code, 403)

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
