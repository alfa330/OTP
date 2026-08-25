# -*- coding: utf-8 -*-
"""Вкладка «Аналитика»: гейт, честный счёт и запись поисковых запросов.

Что здесь важно проверить и почему.

1. ГЕЙТ. Вкладку открывает тот, кто ВЕДЁТ статьи: can_create | can_edit |
   can_publish (плюс отдельно администратор доступов). Читателю она закрыта —
   там видно, кто что читал и кто просрочил ознакомление, поимённо. Гард во
   фронте вкладку прячет, но не защищает запрос: проверка на сервере.

2. ГРАНИЦА ОТДЕЛА — ОТДЕЛЬНАЯ ВЕЩЬ, И ЕЁ ЛЕГКО ПОТЕРЯТЬ. Способность редактора
   отделов не знает: без границы супервайзер СЗоВ с правом публикации увидел бы
   поимённо, кто в отделе продаж просрочил ознакомление. Проверяем, что в
   поимённый запрос уходит список отделов, а у супер-админа и администратора
   доступов — None (границы нет по построению).

   Отдельно закреплено то, ради чего гейт и меняли: пока вкладка гейтилась одной
   способностью can_manage_access, обе ветки _departments возвращали None и
   границы не было вовсе — она недостижима правилом раздела (нет в
   PERMISSION_COLUMNS) и снята у роли admin. Тест на границу тогда проходил бы
   вхолостую, сертифицируя несуществующую защиту.

3. ПРОЧТЕНИЕ ≠ СТРОКА ЖУРНАЛА. Просмотр пишется на каждый GET статьи, включая
   обновление страницы. Дедупликация проверяется НАСТОЯЩИМ запросом на
   синтетических данных: в PostgreSQL CTE перекрывает одноимённую таблицу,
   поэтому заглушки подставляются перед боевым текстом, и исполняется он сам.

4. ОТМЕНЁННОЕ НАЗНАЧЕНИЕ — НЕ ПРОСРОЧКА. У отменённого ознакомления остаются и
   срок, и отсутствие подписи. Без фильтра по 'cancelled' оно вечно висело бы в
   письме супервайзеру. Остальной раздел фильтрует именно так (wiki/ack.py),
   и разъезжаться этим правилам нельзя.

5. ЗАПРОСЫ-ОГРЫЗКИ. Поле поиска ищет по мере набора, поэтому одна фраза
   приезжает шестью запросами. Свёртка проверяется на уровне питона: какие
   параметры уходят в запрос и какой запрос выбирается.
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

from tests import prod_db  # noqa: E402

from wiki import analytics as wiki_analytics  # noqa: E402
from wiki import perimeter as wiki_perimeter  # noqa: E402
from wiki import queries  # noqa: E402
from wiki import search as wiki_search  # noqa: E402
from wiki.access import collect_subjects  # noqa: E402
from wiki.routes import build_wiki_blueprint  # noqa: E402


def make_context(otp_role='operator', department_id=None, wiki_role=True, **caps):
    """Контекст доступа.

    wiki_role=False — способности выводятся из ДОЛЖНОСТИ. Это не украшение:
    непустой список ролей вики ЗАМЕЩАЕТ роль OTP, а не дополняет её, поэтому
    супер-админ с ролью «читатель» теряет мастер-ключ. Проверять права
    супер-админа с приклеенной ролью значило бы проверять не то.
    """
    role = {'id': 5, 'code': 'wiki_role', 'can_read': True, 'can_create': False,
            'can_edit': False, 'can_delete': False, 'can_publish': False,
            'can_approve': False, 'can_manage_users': False,
            'can_manage_structure': False, 'can_manage_access': False}
    role.update(caps)
    return {
        'user_id': 42, 'otp_role': otp_role, 'department_id': department_id,
        'direction_id': None, 'headed_department_ids': [], 'group_ids': [],
        'wiki_roles': [role] if wiki_role else [], 'access_mode': 'auto',
    }


# Сколько значений отдаёт fetchone на каждый запрос отчёта, в порядке вызова:
# итоги чтения (6), итоги ознакомлений (6), итоги поиска (4), дата начала
# журнала (1), итоги помощника (5). Считать их «как получится» нельзя —
# распаковка кортежа не той длины падает, и падает она уже внутри роута.
REPORT_FETCHONE = [(0,) * 6, (0,) * 6, (0,) * 4, (None,), (0,) * 5]


def build_client(test, context, *, fetchone=None, fetchall=()):
    cursor = MagicMock()
    cursor.calls = []

    def execute(sql, params=None):
        cursor.calls.append((sql, params))

    cursor.execute.side_effect = execute
    if fetchone is None:
        fetchone = list(REPORT_FETCHONE)
    if isinstance(fetchone, list):
        # Кортежи разной длины по порядку вызова; когда список кончился,
        # отдаём последний — так тест не разваливается от лишнего запроса.
        queue = list(fetchone)
        cursor.fetchone.side_effect = lambda: queue.pop(0) if len(queue) > 1 else queue[0]
    else:
        cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = list(fetchall)
    db = MagicMock()

    @contextmanager
    def _get_cursor():
        yield cursor

    db._get_cursor = _get_cursor

    def fake_perimeter(_cursor, _ctx, **_kwargs):
        return collect_subjects(user_id=42, otp_role='operator'), {3}, {1, 2}

    patches = [
        (queries, 'load_access_context', lambda _c, _u: dict(context)),
        (queries, 'granted_rule_rights', lambda _c, _s, _u: ({}, [])),
        (queries, 'log_action', lambda *a, **k: None),
        (wiki_perimeter, 'read_perimeter', fake_perimeter),
    ]
    for module, name, replacement in patches:
        original = getattr(module, name)
        setattr(module, name, replacement)
        test.addCleanup(setattr, module, name, original)

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
    return app.test_client(), cursor


@unittest.skipIf(Flask is None, 'flask не установлен')
class GateTest(unittest.TestCase):

    def test_reader_is_refused(self):
        """Читателю вкладка закрыта: там люди поимённо."""
        client, _ = build_client(self, make_context())
        response = client.get('/api/wiki/analytics')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'WIKI_EDITOR_ONLY')

    def test_every_editing_right_opens_the_tab(self):
        """Любое из трёх прав правки открывает вкладку — по отдельности."""
        for right in ('can_create', 'can_edit', 'can_publish'):
            client, _ = build_client(self, make_context(**{right: True}))
            response = client.get('/api/wiki/analytics')
            self.assertEqual(response.status_code, 200, right)

    def test_access_manager_without_editing_still_sees_report(self):
        """У администратора доступов вкладка была и остаётся."""
        client, _ = build_client(self, make_context(can_manage_access=True))
        response = client.get('/api/wiki/analytics')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        for block in ('reading', 'acknowledgements', 'demand', 'notes', 'period'):
            self.assertIn(block, payload)

    def test_period_reaches_every_query(self):
        """Пустые границы уходят как None, заполненные — как есть."""
        client, cursor = build_client(self, make_context(can_manage_access=True))
        client.get('/api/wiki/analytics?since=2026-08-01&until=2026-08-24')
        dated = [p for _sql, p in cursor.calls
                 if isinstance(p, dict) and 'since' in p]
        self.assertTrue(dated, 'ни один запрос не получил период')
        for params in dated:
            self.assertEqual(params['since'], '2026-08-01')
            self.assertEqual(params['until'], '2026-08-24')


@unittest.skipIf(Flask is None, 'flask не установлен')
class DepartmentBoundaryTest(unittest.TestCase):
    """Способность редактора отделов не знает — границу ставим отдельно."""

    def _depts(self, context):
        client, cursor = build_client(self, context)
        response = client.get('/api/wiki/analytics')
        self.assertEqual(response.status_code, 200)
        with_depts = [p for _sql, p in cursor.calls
                      if isinstance(p, dict) and 'depts' in p]
        self.assertTrue(with_depts, 'поимённый список не получил границу отдела')
        return response.get_json(), with_depts[0]['depts']

    def test_supervisor_editor_is_limited_to_own_department(self):
        payload, depts = self._depts(
            make_context(otp_role='sv', department_id=7, can_publish=True))
        self.assertEqual(depts, [7])
        self.assertTrue(payload['scoped'])
        # Оговорка приходит ключом, а не строкой в общем списке: фронт ставит
        # её к поимённому списку, а не подвалом страницы.
        self.assertIn('только по вашим отделам', payload['notes'].get('scoped', ''),
                      'человеку не сказали, что список сужен')

    def test_department_head_sees_headed_departments(self):
        context = make_context(otp_role='admin', department_id=7, can_edit=True)
        context['headed_department_ids'] = [7, 9]
        _payload, depts = self._depts(context)
        self.assertEqual(depts, [7, 9])

    def test_super_admin_has_no_boundary(self):
        payload, depts = self._depts(
            make_context(otp_role='super_admin', department_id=7, wiki_role=False))
        self.assertIsNone(depts)
        self.assertFalse(payload['scoped'])
        self.assertNotIn('scoped', payload['notes'],
                         'без границы оговорки о сужении быть не должно')

    def test_access_manager_has_no_boundary_either(self):
        _payload, depts = self._depts(
            make_context(otp_role='sv', department_id=7, can_manage_access=True))
        self.assertIsNone(depts)


class SearchLogTest(unittest.TestCase):
    """Запись поискового запроса: маска, нормализация, свёртка, короткий запрос."""

    def _capture(self, **kwargs):
        cursor = MagicMock()
        cursor.calls = []
        cursor.execute.side_effect = lambda sql, params=None: cursor.calls.append((sql, params))
        cursor.fetchone.return_value = None      # свернуть не во что
        written = wiki_search.log_query(cursor, **kwargs)
        return written, cursor.calls

    def test_short_query_is_not_written(self):
        written, calls = self._capture(user_id=1, query='а', results_count=0,
                                       perimeter_size=10)
        self.assertFalse(written)
        self.assertEqual(calls, [])

    def test_digits_are_masked_before_writing(self):
        _written, calls = self._capture(user_id=1, query='телефон 87051234567 водителя',
                                        results_count=0, perimeter_size=10)
        params = calls[-1][1]
        self.assertEqual(params['query'], 'телефон # водителя')
        self.assertNotIn('87051234567', params['query'])

    def test_short_numbers_survive(self):
        """Маска бьёт по телефонам и ИИН, а не по любому числу: «форма 2» нужна."""
        _written, calls = self._capture(user_id=1, query='приказ 2024 года',
                                        results_count=3, perimeter_size=10)
        self.assertEqual(calls[-1][1]['query'], 'приказ 2024 года')

    def test_kazakh_letters_are_folded_for_the_report(self):
        _written, calls = self._capture(user_id=1, query='Қазына', results_count=0,
                                        perimeter_size=10)
        params = calls[-1][1]
        self.assertEqual(params['query'], 'Қазына')
        self.assertEqual(params['norm'], 'казына')

    def test_prefix_is_collapsed_instead_of_a_new_row(self):
        """Нашлась строка-огрызок — переписываем её, а не плодим вторую."""
        cursor = MagicMock()
        cursor.calls = []
        cursor.execute.side_effect = lambda sql, params=None: cursor.calls.append((sql, params))
        cursor.fetchone.return_value = (77,)     # UPDATE нашёл, что свернуть
        self.assertTrue(wiki_search.log_query(
            cursor, user_id=1, query='как оформить самозанятость',
            results_count=0, perimeter_size=10))
        self.assertEqual(len(cursor.calls), 1, 'после свёртки INSERT не нужен')
        self.assertIn('UPDATE wiki_search_log', cursor.calls[0][0])

    def test_anonymous_query_is_never_collapsed(self):
        """Без владельца «предыдущая строка за 30 секунд» склеила бы разных людей."""
        _written, calls = self._capture(user_id=None, query='отпуск',
                                        results_count=1, perimeter_size=10)
        self.assertEqual(len(calls), 1)
        self.assertIn('INSERT INTO wiki_search_log', calls[0][0])

    def test_perimeter_is_written_next_to_the_result_count(self):
        """Ноль находок без размера периметра неинтерпретируем."""
        _written, calls = self._capture(user_id=1, query='самозанятость',
                                        results_count=0, perimeter_size=16)
        params = calls[-1][1]
        self.assertEqual(params['found'], 0)
        self.assertEqual(params['perimeter'], 16)


# ── Настоящий SQL на синтетических данных ────────────────────────────────────
#
# Приём тот же, что в tests/test_wiki_ai_perimeter.py: в PostgreSQL CTE
# перекрывает одноимённую таблицу, поэтому заглушки подставляются ПЕРЕД боевым
# текстом запроса, и исполняется он сам, без правок. Соединение read only.

def _stub(sql, stubs):
    """Вклеить заглушки в начало боевого запроса.

    Боевые запросы блока чтения уже начинаются с «WITH reads AS», поэтому
    заглушки дописываются в тот же WITH, а не вторым.
    """
    body = sql.lstrip()
    if body.startswith('WITH '):
        return 'WITH ' + stubs + ',\n' + body[len('WITH '):]
    return 'WITH ' + stubs + '\n' + body


_VIEWS_STUB = """
wiki_article_views_log AS (
    SELECT article_id::int, user_id::int, viewed_at::timestamp,
           snapshot_department_id::int
      FROM (VALUES {rows}) AS t(article_id, user_id, viewed_at, snapshot_department_id)
),
users AS (
    SELECT id::int, department_id::int, status::text
      FROM (VALUES {people}) AS t(id, department_id, status)
),
departments AS (
    SELECT id::int, name::text FROM (VALUES (1, 'СЗоВ')) AS t(id, name)
),
wiki_articles AS (
    SELECT id::int, slug::text, title::text, status::text, updated_at::timestamp
      FROM (VALUES {articles}) AS t(id, slug, title, status, updated_at)
)
"""


@unittest.skipIf(not prod_db.available(), 'нет доступа к базе для прогона SQL')
class ReadDeduplicationTest(unittest.TestCase):
    """Обновление страницы не должно превращаться в три прочтения."""

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def tearDown(self):
        prod_db.rollback()

    def _totals(self, rows, articles=None):
        stubs = _VIEWS_STUB.format(
            rows=', '.join(rows),
            people="(5, 1, 'working'), (6, 1, 'fired')",
            articles=articles
            or "(1, 'a', 'Статья', 'published', '2026-08-01'::timestamp)")
        sql = _stub(wiki_analytics._TOTALS_SQL, stubs)
        with self.conn.cursor() as cursor:
            cursor.execute(sql, {'visible': [1], 'since': None, 'until': None})
            return cursor.fetchone()

    def test_untouched_published_counts_whole(self):
        """Нетронутое считается ЦЕЛИКОМ, а не по длине урезанного списка.

        Плитка «Статей без чтений» брала длину списка, а список режется
        потолком строк: при полусотне нетронутых статей она показывала бы
        ровно столько, сколько влезло в таблицу.
        """
        rows = ["(1, 5, '2026-08-10 10:00:05'::timestamp, 1)"]
        articles = ("(1, 'a', 'Читали', 'published', '2026-08-01'::timestamp), "
                    "(2, 'b', 'Не читали', 'published', '2026-08-01'::timestamp), "
                    "(3, 'c', 'Черновик', 'draft', '2026-08-01'::timestamp)")
        with self.conn.cursor() as cursor:
            cursor.execute(_stub(wiki_analytics._TOTALS_SQL, _VIEWS_STUB.format(
                rows=', '.join(rows), people="(5, 1, 'working')",
                articles=articles)),
                {'visible': [1, 2, 3], 'since': None, 'until': None})
            row = cursor.fetchone()
        published, published_read = row[4], row[5]
        self.assertEqual(published, 2, 'черновик не опубликован')
        self.assertEqual(published_read, 1)
        # Ровно эту разность роут кладёт в totals['unread'].
        self.assertEqual(published - published_read, 1,
                         'нетронутая опубликованная статья ровно одна')

    def test_three_opens_in_one_minute_are_one_read(self):
        rows = ["(1, 5, '2026-08-10 10:00:05'::timestamp, 1)",
                "(1, 5, '2026-08-10 10:00:31'::timestamp, 1)",
                "(1, 5, '2026-08-10 10:00:59'::timestamp, 1)"]
        reads, readers, articles_read, opens, published, _read_pub = self._totals(rows)
        self.assertEqual(opens, 3, 'сырые открытия считаются как есть')
        self.assertEqual(reads, 1, 'обновление страницы — не второе прочтение')
        self.assertEqual(readers, 1)
        self.assertEqual(articles_read, 1)
        self.assertEqual(published, 1)

    def test_next_minute_is_a_second_read(self):
        rows = ["(1, 5, '2026-08-10 10:00:05'::timestamp, 1)",
                "(1, 5, '2026-08-10 10:01:05'::timestamp, 1)"]
        reads, _readers, _articles, opens, _published, _read_pub = self._totals(rows)
        self.assertEqual((opens, reads), (2, 2))

    def test_different_people_are_different_reads(self):
        rows = ["(1, 5, '2026-08-10 10:00:05'::timestamp, 1)",
                "(1, 6, '2026-08-10 10:00:07'::timestamp, 1)"]
        reads, readers, _articles, _opens, _published, _read_pub = self._totals(rows)
        self.assertEqual((reads, readers), (2, 2))

    def test_period_cuts_by_almaty_day_inclusive(self):
        """Верхняя граница включает ВЕСЬ день «по», а не полночь."""
        rows = ["(1, 5, '2026-08-10 23:50:00'::timestamp, 1)"]
        stubs = _VIEWS_STUB.format(
            rows=', '.join(rows),
            people="(5, 1, 'working')",
            articles="(1, 'a', 'Статья', 'published', '2026-08-01'::timestamp)")
        sql = _stub(wiki_analytics._TOTALS_SQL, stubs)
        with self.conn.cursor() as cursor:
            cursor.execute(sql, {'visible': [1], 'since': '2026-08-10',
                                 'until': '2026-08-10'})
            self.assertEqual(cursor.fetchone()[0], 1)


@unittest.skipIf(not prod_db.available(), 'нет доступа к базе для прогона SQL')
class HeadcountTest(unittest.TestCase):
    """Уволенные не должны занижать охват отдела."""

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def tearDown(self):
        prod_db.rollback()

    def test_fired_are_out_of_the_denominator(self):
        stubs = _VIEWS_STUB.format(
            rows="(1, 5, '2026-08-10 10:00:00'::timestamp, 1)",
            people=("(5, 1, 'working'), (6, 1, 'fired'), (7, 1, 'annual_leave'), "
                    "(8, 1, 'dismissal')"),
            articles="(1, 'a', 'Статья', 'published', '2026-08-01'::timestamp)")
        sql = _stub(wiki_analytics._BY_DEPARTMENT_SQL, stubs)
        with self.conn.cursor() as cursor:
            cursor.execute(sql, {'visible': [1], 'since': None, 'until': None})
            row = cursor.fetchone()
        # Штат: работающий и отпускник. Уволенный и уволившийся — нет.
        self.assertEqual(row[5], 2)
        self.assertEqual(row[3], 1, 'читатель один')


_ACK_STUB = """
wiki_ack_assignments AS (
    SELECT article_id::int, user_id::int, status::text, due_at::timestamp,
           acknowledged_at::timestamp, snapshot_department_id::int,
           snapshot_department_name::text
      FROM (VALUES {rows}) AS t(article_id, user_id, status, due_at,
                                acknowledged_at, snapshot_department_id,
                                snapshot_department_name)
)
"""


@unittest.skipIf(not prod_db.available(), 'нет доступа к базе для прогона SQL')
class AckOverdueTest(unittest.TestCase):
    """Отменённое и перевыпущенное назначение не считаются просрочкой."""

    @classmethod
    def setUpClass(cls):
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.conn = prod_db.connection()

    def tearDown(self):
        prod_db.rollback()

    def test_cancelled_and_superseded_are_not_overdue(self):
        rows = [
            # Живая просрочка: срок прошёл, подписи нет.
            "(1, 5, 'not_open', '2020-01-01'::timestamp, NULL, 1, 'СЗоВ')",
            # Отменённое назначение с тем же прошедшим сроком.
            "(1, 6, 'cancelled', '2020-01-01'::timestamp, NULL, 1, 'СЗоВ')",
            # Перевыпущенное — снято выходом новой версии статьи.
            "(1, 7, 'superseded', '2020-01-01'::timestamp, NULL, 1, 'СЗоВ')",
            # Подтверждённое вовремя.
            "(1, 8, 'acknowledged', '2020-01-01'::timestamp, "
            "'2019-12-01'::timestamp, 1, 'СЗоВ')",
        ]
        sql = _stub(wiki_analytics._ACK_TOTALS_SQL,
                    _ACK_STUB.format(rows=', '.join(rows)))
        with self.conn.cursor() as cursor:
            cursor.execute(sql, {'visible': [1]})
            total, done, not_open, overdue, people, articles = cursor.fetchone()
        self.assertEqual(total, 2, 'живых назначений два')
        self.assertEqual(done, 1)
        self.assertEqual(not_open, 1)
        self.assertEqual(overdue, 1, 'просрочка ровно одна — отменённое не в счёт')
        self.assertEqual((people, articles), (2, 1))


if __name__ == '__main__':
    unittest.main()
