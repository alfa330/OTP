# -*- coding: utf-8 -*-
"""Центр уведомлений: порядок, изоляция источников и правила «прочитано».

Отдельного внимания стоит последний класс. Список того, что колокол разрешает
гасить, существует в двух местах — в Python (mark_seen) и в JSX (CLEARABLE), —
и это ровно тот вид раздвоения, на котором ломаются такие вещи: расхождение не
даст ни ошибки, ни падения, просто кнопка «отметить прочитанным» начнёт
обнулять счётчик обязательного документа, ничего с ним не сделав. Поэтому
списки сверяются по исходникам.
"""

import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from notifications import realtime, sources
from tests import prod_db

ROOT = Path(__file__).resolve().parents[1]
BELL_JSX = ROOT / 'src' / 'components' / 'notifications' / 'NotificationsBell.jsx'


class FakeCursor:
    """Курсор, которому важны только SAVEPOINT'ы: сами источники подменяются."""

    def __init__(self):
        self.commands = []

    def execute(self, sql, params=None):
        self.commands.append(sql.strip().split()[0].upper())

    def fetchall(self):
        return []

    def fetchone(self):
        return None


def _item(source, title, tone='default', at=None):
    return {'source': source, 'id': 1, 'title': title, 'body': '', 'at': at,
            'view': source, 'target': None, 'tone': tone}


class CollectOrderTest(unittest.TestCase):
    """Порядок в общем списке."""

    def setUp(self):
        self.original = dict(sources._HANDLERS)

    def tearDown(self):
        sources._HANDLERS.clear()
        sources._HANDLERS.update(self.original)

    def _stub(self, mapping):
        sources._HANDLERS.clear()
        sources._HANDLERS.update(mapping)

    def test_overdue_goes_first(self):
        """Просроченное поднимается ВОПРЕКИ порядку источников.

        Важно, какой источник помечен просроченным: wiki_ack идёт первым в
        SOURCES, и тест с ним проходил бы даже при полностью удалённой
        сортировке — порядок обеспечила бы сама константа. Поэтому «горит»
        здесь four_you, последний в SOURCES: подняться наверх он может только
        сортировкой.
        """
        self._stub({
            'wiki_ack': lambda c, v: (1, [_item('wiki_ack', 'Регламент')]),
            'events': lambda c, v: (1, [_item('events', 'Новый пост')]),
            'four_you': lambda c, v: (1, [_item('four_you', 'Просрочено', tone='warning')]),
        })
        _, items = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual('Просрочено', items[0]['title'])
        # Остальные обязаны сохранить свой порядок — сортировка устойчивая.
        self.assertEqual(['Просрочено', 'Регламент', 'Новый пост'],
                         [i['title'] for i in items])

    def test_sources_keep_their_own_order(self):
        """Внутри источника порядок задаёт его ORDER BY и трогать его нельзя.

        У ознакомлений и опросов `at` — это СРОК, а не время события. Сортировка
        всего списка по дате подняла бы наверх самый дальний дедлайн.
        """
        self._stub({'wiki_ack': lambda c, v: (3, [
            _item('wiki_ack', 'Завтра', at='2026-08-10T00:00:00'),
            _item('wiki_ack', 'Через месяц', at='2026-09-10T00:00:00'),
            _item('wiki_ack', 'Без срока', at=None),
        ])})
        _, items = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual(['Завтра', 'Через месяц', 'Без срока'],
                         [i['title'] for i in items])

    def test_total_is_sum_of_sources(self):
        self._stub({
            'events': lambda c, v: (2, []),
            'lms': lambda c, v: (3, []),
        })
        counts, _ = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual(5, counts['total'])

    def test_broken_source_does_not_kill_the_rest(self):
        """Сломанный раздел даёт ноль, а не 500 на весь колокол."""
        def explode(cursor, viewer):
            raise RuntimeError('таблицы ещё нет')

        self._stub({'wiki_ack': explode, 'lms': lambda c, v: (4, [_item('lms', 'Урок')])})
        with self.assertLogs(level='ERROR'):
            counts, items = sources.collect(FakeCursor(), {'user_id': 1})
        self.assertEqual(0, counts['wiki_ack'])
        self.assertEqual(4, counts['lms'])
        self.assertEqual(4, counts['total'])
        self.assertEqual(['Урок'], [i['title'] for i in items])

    def test_broken_source_rolls_back_to_savepoint(self):
        """Иначе упавший источник оставил бы транзакцию в aborted-состоянии."""
        def explode(cursor, viewer):
            cursor.execute('SELECT неверно')
            raise RuntimeError('boom')

        self._stub({'events': explode})
        cursor = FakeCursor()
        with self.assertLogs(level='ERROR'):
            sources.collect(cursor, {'user_id': 1})
        self.assertIn('ROLLBACK', cursor.commands)
        self.assertEqual(cursor.commands.count('SAVEPOINT'), cursor.commands.count('RELEASE'))

    def test_hidden_source_is_skipped_entirely(self):
        called = []
        self._stub({'four_you': lambda c, v: (called.append(1), (9, []))[1]})
        counts, _ = sources.collect(FakeCursor(), {'user_id': 1, 'hidden_sources': ('four_you',)})
        self.assertEqual(0, counts['four_you'])
        self.assertEqual([], called)


class SurveyWindowTimezoneTest(unittest.TestCase):
    """Окно теста считается во времени Алматы, а не в UTC базы.

    starts_at/ends_at хранятся наивными во времени Алматы: их пишет
    _parse_survey_schedule_value без tzinfo, а сравнивает survey_test_status с
    datetime.now() — процесс живёт в Asia/Almaty. База стоит в UTC, поэтому
    голый CURRENT_TIMESTAMP в запросе даёт сдвиг ровно на 5 часов, и колокол
    показывает тест открытым ещё пять часов после закрытия.

    Дефект молчаливый: ошибки нет, цифра просто неверная. Поэтому проверяется и
    исходник (чтобы CURRENT_TIMESTAMP не вернулся), и поведение на настоящей
    базе.
    """

    SOURCE = (ROOT / 'notifications' / 'sources.py').read_text(encoding='utf-8')

    def test_window_does_not_use_bare_current_timestamp(self):
        block = re.search(r'def surveys\(.*?\n    \)\n', self.SOURCE, re.S)
        self.assertIsNotNone(block, 'не найдено тело запроса опросов')
        sql = block.group(0)
        self.assertNotRegex(
            sql, r'(starts_at|ends_at)\s*[<>]=?\s*CURRENT_TIMESTAMP',
            'окно теста снова сравнивается с UTC-временем базы — сдвиг 5 часов',
        )
        self.assertIn('%(now)s', sql, 'время должно приходить параметром')

    def test_almaty_now_matches_process_clock(self):
        """Тот же вызов, что и у Database.survey_test_status."""
        self.assertAlmostEqual(
            sources._almaty_now().timestamp(), datetime.now().timestamp(), delta=2,
        )

    def test_closed_window_is_closed_on_real_postgres(self):
        """Окно, закрывшееся два часа назад, обязано считаться закрытым."""
        reason = prod_db.skip_reason()
        if reason:
            self.skipTest(reason)
        now = sources._almaty_now()
        closed = now - timedelta(hours=2)
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute('SELECT %(ends)s::timestamp > %(now)s::timestamp',
                           {'ends': closed, 'now': now})
            self.assertFalse(cursor.fetchone()[0],
                             'закрывшийся тест не должен считаться открытым')

            # Показываем, чем именно был дефект: сдвиг между часами процесса и
            # часами базы. Утверждение делаем только если сдвиг реально есть —
            # на машине разработчика в UTC его не будет, и падать тут не за что.
            cursor.execute('SELECT CURRENT_TIMESTAMP::timestamp')
            db_now = cursor.fetchone()[0]
            shift_hours = (now - db_now).total_seconds() / 3600
            if shift_hours <= 2:
                self.skipTest('часы процесса и базы совпадают (сдвиг %.1f ч) — '
                              'сравнивать не с чем' % shift_hours)
            cursor.execute('SELECT %(ends)s::timestamp > CURRENT_TIMESTAMP',
                           {'ends': closed})
            self.assertTrue(
                cursor.fetchone()[0],
                'при сдвиге %.1f ч голый CURRENT_TIMESTAMP обязан был бы считать '
                'закрытый тест открытым — иначе дефект был не в этом' % shift_hours,
            )
        finally:
            prod_db.rollback()
            cursor.close()


class MarkSeenRulesTest(unittest.TestCase):
    """Что вообще можно погасить просмотром, а что только действием."""

    def test_action_bound_sources_are_not_clearable(self):
        cursor = FakeCursor()
        for source in ('wiki_ack', 'surveys', 'tasks'):
            self.assertFalse(
                sources.mark_seen(cursor, 1, source),
                'источник %s нельзя гасить: он снимается действием, иначе счётчик '
                'обязательного документа обнулялся бы просмотром колокола' % source,
            )
        self.assertEqual([], cursor.commands)

    def test_watermark_sources_are_clearable(self):
        for source in ('events', 'four_you', 'lms'):
            cursor = FakeCursor()
            self.assertTrue(sources.mark_seen(cursor, 1, source))
            self.assertTrue(cursor.commands, 'должен был выполнить запрос')

    def test_unknown_source_is_ignored(self):
        self.assertFalse(sources.mark_seen(FakeCursor(), 1, 'нет-такого'))


class TasksSourceRulesTest(unittest.TestCase):
    """Источник «Задачи» — третья копия правил «задача ждёт вас».

    Первые две — SQL бейджа (database.py::get_task_action_needs_summary) и
    клиентские правила раздела (taskActionNeeds.js). Дрейф копий не падает и
    не ошибается — числа на экране просто молча расходятся, поэтому маркеры
    правил сверяются по исходнику, как это уже делает
    test_task_backlog_board.ActionNeedsBadgeTests для первых двух.
    """

    SOURCE = (ROOT / 'notifications' / 'sources.py').read_text(encoding='utf-8')

    def _block(self):
        start = self.SOURCE.index('def tasks(cursor, viewer):')
        return self.SOURCE[start:self.SOURCE.index('_HANDLERS = {', start)]

    def test_rules_match_badge_sql(self):
        block = self._block()
        # Просрочка — только у исполнителя и только по живым статусам.
        self.assertIn("t.status IN ('assigned', 'in_progress', 'returned')", block)
        # Приёмку ждёт поручитель, а если его нет — постановщик.
        self.assertIn("COALESCE(t.requested_by_id, t.created_by) = %(user_id)s", block)
        self.assertIn("t.status = 'completed'", block)
        # Бэклог не считается: это очередь планирования.
        self.assertIn("t.is_backlog = FALSE", block)
        # У каждой причины своя проверка отметки «просмотрено».
        for kind in ('overdue', 'returned', 'review', 'fresh'):
            self.assertIn(
                "r.kind <> '%s' OR r.seen_at < t.updated_at" % kind, block,
            )

    def test_now_is_process_clock_not_db_clock(self):
        """due_at хранится наивным во времени Алматы, база — в UTC.

        Сравнение с голым CURRENT_TIMESTAMP давало бы сдвиг на 5 часов, как это
        уже было с окнами тестов у опросов: задача считалась бы просроченной на
        5 часов раньше срока.
        """
        block = self._block()
        self.assertNotRegex(
            block, r'due_at\s*[<>]=?\s*CURRENT_TIMESTAMP',
            'дедлайн снова сравнивается с UTC-временем базы — сдвиг 5 часов',
        )
        self.assertIn('%(now)s', block, 'время должно приходить параметром')

    def test_items_shape_and_tone(self):
        """overdue горит и показывает срок; остальные — момент события."""
        rows = [
            (7, 'Отчёт по сменам', datetime(2026, 8, 1), datetime(2026, 7, 20), 'overdue', 2),
            (9, 'Витрина KPI', None, datetime(2026, 8, 8), 'fresh', 2),
        ]

        class Cursor(FakeCursor):
            def fetchall(self):
                return rows

        total, items = sources.tasks(Cursor(), {'user_id': 1})
        self.assertEqual(2, total)
        self.assertEqual(['warning', 'default'], [i['tone'] for i in items])
        self.assertEqual('2026-08-01T00:00:00', items[0]['at'])   # срок
        self.assertEqual('2026-08-08T00:00:00', items[1]['at'])   # когда поручили
        self.assertEqual(['tasks', 'tasks'], [i['view'] for i in items])
        self.assertEqual([7, 9], [i['target'] for i in items])
        self.assertEqual('Просрочена', items[0]['body'])


class FailureResponseTest(unittest.TestCase):
    """Отказ инфраструктуры обязан выглядеть отказом, а не пустой сводкой.

    Клиент пишет counts прямо в бейджи сайдбара. Отдай сервер 200 с нулями при
    исчерпанном пуле — у человека погасли бы «Ивенты» и «4 You», а просроченный
    документ под обязательное ознакомление исчез бы с экрана, и отличить это от
    честного «ничего нет» было бы нельзя ни ему, ни фронту.
    """

    def _client(self, *, resolver=None, viewer_ctx=None, db=None):
        from flask import Flask
        from notifications.routes import build_notifications_blueprint

        class DeadDb:
            def _get_cursor(self):
                raise TimeoutError('POSTGRES_POOL_ACQUIRE_TIMEOUT')

        app = Flask(__name__)
        app.register_blueprint(build_notifications_blueprint(
            db=db or DeadDb(),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=resolver or (lambda: (2, None, None)),
            viewer_context=viewer_ctx or (lambda rid, r: {'user_id': rid}),
        ))
        return app.test_client()

    def test_pool_exhaustion_is_503_not_empty_summary(self):
        with self.assertLogs(level='ERROR'):
            response = self._client().get('/api/notifications')
        self.assertEqual(503, response.status_code)
        self.assertNotIn('counts', response.get_json())

    def test_failure_inside_viewer_context_is_also_json(self):
        """Периметр зрителя тоже ходит в базу — раньше отсюда летел HTML-500."""
        def boom(requester_id, requester):
            raise TimeoutError('POSTGRES_POOL_ACQUIRE_TIMEOUT')

        with self.assertLogs(level='ERROR'):
            response = self._client(viewer_ctx=boom).get('/api/notifications')
        self.assertEqual(503, response.status_code)
        self.assertEqual('application/json', response.headers['Content-Type'].split(';')[0])

    def test_auth_error_is_not_swallowed_into_503(self):
        client = self._client(resolver=lambda: (None, None, ('Unauthorized', 401)))
        response = client.get('/api/notifications')
        self.assertEqual(401, response.status_code)


class SeenEndpointTest(unittest.TestCase):
    """Разбор тела запроса у POST /seen — на подставном курсоре, без базы."""

    def setUp(self):
        from flask import Flask
        from notifications.routes import build_notifications_blueprint

        self.executed = []
        outer = self

        class Cursor:
            def execute(self, sql, params=None):
                outer.executed.append(sql.strip().split()[0].upper())

        class Db:
            def _get_cursor(self):
                import contextlib

                @contextlib.contextmanager
                def cm():
                    yield Cursor()
                return cm()

        app = Flask(__name__)
        app.register_blueprint(build_notifications_blueprint(
            db=Db(), require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (2, None, None),
            viewer_context=lambda rid, r: {'user_id': rid},
        ))
        self.client = app.test_client()

    def post(self, payload):
        return self.client.post('/api/notifications/seen', json=payload)

    def test_duplicates_are_collapsed(self):
        """Иначе лишний UPDATE и ответ вида ["events", "events"]."""
        response = self.post({'sources': ['events', 'events', 'four_you']})
        self.assertEqual(200, response.status_code)
        self.assertEqual(['events', 'four_you'], response.get_json()['marked'])
        self.assertEqual(2, len(self.executed), 'на каждый источник ровно один запрос')

    def test_unknown_source_is_rejected_without_touching_db(self):
        response = self.post({'sources': ['нет-такого']})
        self.assertEqual(400, response.status_code)
        self.assertEqual([], self.executed)

    def test_single_source_form_works(self):
        self.assertEqual(['lms'], self.post({'source': 'lms'}).get_json()['marked'])

    def test_empty_body_is_rejected(self):
        self.assertEqual(400, self.post({}).status_code)

    def test_action_bound_source_is_accepted_but_marks_nothing(self):
        """Фронт его не шлёт, но ответ обязан быть честным: ничего не погашено."""
        response = self.post({'sources': ['wiki_ack']})
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.get_json()['marked'])
        self.assertEqual([], self.executed)


class RealtimeStateMixin:
    """Модуль realtime держит состояние процесса — тесты обязаны его вернуть."""

    def setUp(self):
        self._saved = (list(realtime._ticks), realtime._seq,
                       realtime._active_streams, realtime._listener_started)
        realtime._ticks.clear()
        realtime._seq = 0
        realtime._active_streams = 0
        # Слушатель в тестах не поднимается: базы нет, поток крутился бы в
        # цикле переподключений до конца прогона.
        realtime._listener_started = True

    def tearDown(self):
        ticks, seq, streams, started = self._saved
        realtime._ticks.clear()
        realtime._ticks.extend(ticks)
        realtime._seq = seq
        realtime._active_streams = streams
        realtime._listener_started = started


class RealtimeTicksTest(RealtimeStateMixin, unittest.TestCase):
    """Разбор payload'ов триггеров и матчинг тычков по адресату."""

    def test_payload_parsing(self):
        self.assertIsNone(realtime._parse_payload('{"b":1}'), 'широковещательный')
        self.assertEqual(frozenset({3, 7}), realtime._parse_payload('{"u":[3,7]}'))
        # Мусор игнорируется, а не будит всех: сломанный payload не повод
        # устраивать всем клиентам массовую перечитку.
        self.assertIs(False, realtime._parse_payload('не-json'))
        self.assertIs(False, realtime._parse_payload('[1,2]'))
        self.assertIs(False, realtime._parse_payload('{"u":[]}'))
        self.assertIs(False, realtime._parse_payload(''))

    def test_targeted_tick_reaches_only_its_user(self):
        realtime._publish(frozenset({5}))
        poked, seq = realtime.wait_for_tick(0, 5, 0.1)
        self.assertTrue(poked)
        poked_other, _ = realtime.wait_for_tick(0, 6, 0.1)
        self.assertFalse(poked_other, 'чужой адресный тычок не должен будить')
        # Курсор продвинулся — при следующем ожидании тот же тычок не всплывёт.
        poked_again, _ = realtime.wait_for_tick(seq, 5, 0.1)
        self.assertFalse(poked_again)

    def test_broadcast_reaches_everyone(self):
        realtime._publish(None)
        for user_id in (1, 99):
            poked, _ = realtime.wait_for_tick(0, user_id, 0.1)
            self.assertTrue(poked)

    def test_buffer_gap_forces_reload_even_without_matching_tick(self):
        """Вытеснённый адресный тик мог быть нашим — угадывать нельзя."""
        for _ in range(realtime.TICK_BUFFER_MAXLEN + 1):
            realtime._publish(frozenset({999}))

        poked, seq = realtime.wait_for_tick(0, 5, 0.1)

        self.assertTrue(poked, 'разрыв курсора обязан принудить полную сверку')
        self.assertEqual(realtime.current_seq(), seq)

    def test_subscribe_broadcasts_resync_after_listen(self):
        commands = []

        class Cursor:
            def execute(self, sql):
                commands.append(sql)

        class Connection:
            def set_session(self, **kwargs):
                self.session = kwargs

            def cursor(self):
                return Cursor()

        connection = Connection()
        realtime._subscribe(connection)

        self.assertEqual({'autocommit': True}, connection.session)
        self.assertEqual(['LISTEN %s' % realtime.BELL_NOTIFY_CHANNEL], commands)
        for user_id in (1, 99):
            poked, _ = realtime.wait_for_tick(0, user_id, 0.1)
            self.assertTrue(poked, 'после LISTEN все живые потоки сверяют сводку')

    def test_stream_slots_are_bounded(self):
        self.assertTrue(realtime.try_acquire_stream_slot(2))
        self.assertTrue(realtime.try_acquire_stream_slot(2))
        self.assertFalse(realtime.try_acquire_stream_slot(2), 'мест ровно limit')
        realtime.release_stream_slot()
        self.assertTrue(realtime.try_acquire_stream_slot(2))


class StreamEndpointTest(RealtimeStateMixin, unittest.TestCase):
    """SSE-канал: выключен без базы, ограничен слотами, тычок будит клиента."""

    def _client(self, *, listen_connect=None, stream_limit=50):
        from flask import Flask
        from notifications.routes import build_notifications_blueprint

        app = Flask(__name__)
        app.register_blueprint(build_notifications_blueprint(
            db=object(),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (2, None, None),
            viewer_context=lambda rid, r: {'user_id': rid},
            listen_connect=listen_connect,
            stream_limit=stream_limit,
        ))
        return app.test_client()

    def test_disabled_without_listener_factory(self):
        """Юнит-тесты и сломанная фабрика: канал честно 503, колокол на фокусе."""
        response = self._client().get('/api/notifications/stream')
        self.assertEqual(503, response.status_code)

    def test_over_capacity_is_503_with_retry_after(self):
        client = self._client(listen_connect=lambda: None, stream_limit=1)
        self.assertTrue(realtime.try_acquire_stream_slot(1))  # место занято
        response = client.get('/api/notifications/stream')
        self.assertEqual(503, response.status_code)
        self.assertEqual('300', response.headers.get('Retry-After'))
        # Отказ не должен съесть слот.
        self.assertEqual(1, realtime.active_stream_count())

    def test_tick_wakes_stream_and_slot_returns_on_close(self):
        client = self._client(listen_connect=lambda: None, stream_limit=1)
        response = client.get('/api/notifications/stream', buffered=False)
        self.assertEqual(200, response.status_code)
        self.assertEqual('text/event-stream', response.mimetype)
        stream = response.response if hasattr(response.response, '__next__') \
            else iter(response.response)
        first = next(stream)
        self.assertIn(b'connected', first)
        self.assertEqual(1, realtime.active_stream_count(), 'поток держит слот')

        realtime._publish(frozenset({2}))  # адресный тычок нашему зрителю
        self.assertIn(b'event: reload', next(stream))

        response.close()
        self.assertEqual(0, realtime.active_stream_count(),
                         'слот обязан вернуться при закрытии ответа')

    def test_stream_periodically_reconciles_clock_driven_sources(self):
        client = self._client(listen_connect=lambda: None, stream_limit=1)
        original_reconcile = realtime.RECONCILE_SECONDS
        original_heartbeat = realtime.HEARTBEAT_SECONDS
        realtime.RECONCILE_SECONDS = 0.01
        realtime.HEARTBEAT_SECONDS = 0.01
        try:
            response = client.get('/api/notifications/stream', buffered=False)
            stream = response.response if hasattr(response.response, '__next__') \
                else iter(response.response)
            self.assertIn(b'connected', next(stream))
            self.assertIn(b'event: reload', next(stream))
            response.close()
        finally:
            realtime.RECONCILE_SECONDS = original_reconcile
            realtime.HEARTBEAT_SECONDS = original_heartbeat


class RealtimeTriggersPinnedTest(unittest.TestCase):
    """Триггеры в схеме и слушатель обязаны говорить об одном канале."""

    DATABASE = (ROOT / 'database.py').read_text(encoding='utf-8')

    def test_channel_name_matches_listener(self):
        self.assertIn("BELL_EVENTS_NOTIFY_CHANNEL = '%s'" % realtime.BELL_NOTIFY_CHANNEL,
                      self.DATABASE)
        block = self._trigger_block()
        self.assertIn("pg_notify('%s'" % realtime.BELL_NOTIFY_CHANNEL, block)

    def _trigger_block(self):
        start = self.DATABASE.index('def _init_bell_notify_schema_tx(self, cursor):')
        return self.DATABASE[start:self.DATABASE.index('def _init_amo_leads_schema_tx', start)]

    def test_every_source_table_has_a_trigger(self):
        """Таблица без триггера = источник без реалтайма, молча."""
        block = self._trigger_block()
        for table in ('events', 'four_you_images', 'lms_notifications',
                      'survey_assignments', 'wiki_ack_assignments',
                      'tasks', 'task_action_reads', 'event_reads',
                      'four_you_reads'):
            self.assertIn("'%s'" % table, block)

    def test_watermark_updates_wake_only_the_same_user(self):
        block = self._trigger_block()
        self.assertIn("TG_TABLE_NAME IN ('event_reads', 'four_you_reads')", block)
        self.assertIn("targets := ARRAY[NEW.user_id]", block)

    def test_survey_and_wiki_progress_updates_are_filtered(self):
        block = self._trigger_block()
        self.assertIn('AFTER UPDATE OF operator_id, survey_id, status', block)
        self.assertIn(
            'AFTER UPDATE OF user_id, article_id, due_at, acknowledged_at, status',
            block,
        )
        self.assertIn("(COALESCE(OLD.status, '') = 'completed') IS NOT DISTINCT FROM", block)
        self.assertIn('OLD.acknowledged_at IS NULL', block)
        self.assertIn("COALESCE(OLD.status IN ('superseded', 'cancelled'), FALSE)", block)
        self.assertIn('OLD.due_at IS NOT DISTINCT FROM NEW.due_at', block)

    def test_trigger_never_breaks_the_write(self):
        """Ошибка тычка не должна откатывать само действие пользователя."""
        block = self._trigger_block()
        self.assertIn('EXCEPTION WHEN OTHERS THEN', block)
        # И вся установка триггеров — под SAVEPOINT в _init_db.
        self.assertIn('SAVEPOINT sp_bell_notify', self.DATABASE)

    def test_bell_client_keeps_focus_fallback(self):
        """SSE — ускорение, а не замена: обновление по фокусу обязано остаться."""
        source = BELL_JSX.read_text(encoding='utf-8')
        self.assertIn('/api/notifications/stream', source)
        self.assertIn("visibilityState === 'hidden'", source)
        self.assertIn('REFRESH_GAP_MS', source)


class FrontendAgreesWithBackendTest(unittest.TestCase):
    """Списки гасимых источников во фронте и в бэке обязаны совпадать."""

    def test_clearable_lists_match(self):
        source = BELL_JSX.read_text(encoding='utf-8')
        match = re.search(r"const CLEARABLE = \[([^\]]*)\]", source)
        self.assertIsNotNone(match, 'в компоненте пропал список CLEARABLE')
        frontend = set(re.findall(r"'([a-z_]+)'", match.group(1)))

        backend = {name for name in sources.SOURCES
                   if sources.mark_seen(FakeCursor(), 1, name)}
        self.assertEqual(
            backend, frontend,
            'фронт предлагает гасить не то, что умеет гасить сервер: '
            'кнопка «отметить прочитанным» будет обнулять счётчик впустую',
        )

    def test_every_source_has_a_label_in_the_bell(self):
        """Иначе в колоколе вместо раздела покажется его технический код."""
        source = BELL_JSX.read_text(encoding='utf-8')
        block = re.search(r"const SOURCE_META = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(block)
        labelled = set(re.findall(r"^\s{4}([a-z_]+):", block.group(1), re.M))
        self.assertEqual(set(sources.SOURCES), labelled)


if __name__ == '__main__':
    unittest.main()
