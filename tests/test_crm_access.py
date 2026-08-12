# -*- coding: utf-8 -*-
"""Периметр раздела «Обращения»: кто что видит и кто что может.

Правило видимости существует в ДВУХ формах: access.can_view_ticket проверяет
один загруженный тикет (карточку открывают по прямой ссылке, минуя список), а
queries.visibility_sql фильтрует список в базе. Фильтровать список в Python
нельзя — это значило бы вычитывать всю таблицу, — поэтому две формы неизбежны,
и здесь они сверяются: расхождение не упало бы, а тихо показало бы человеку
чужую переписку либо спрятало его собственную.
"""

import re
import unittest
from pathlib import Path

from crm import access, queries, schema

ROOT = Path(__file__).resolve().parents[1]


def ctx(role='operator', user_id=10, department_id=1, headed=(), groups=()):
    return {
        'user_id': user_id,
        'name': 'Тест',
        'role': role,
        'department_id': department_id,
        'headed_department_ids': list(headed),
        'group_ids': list(groups),
    }


def ticket(created_by=10, department_id=1, queue_department_id=None,
           status='open', author_group_ids=()):
    return {
        'created_by': created_by,
        'department_id': department_id,
        'queue_department_id': queue_department_id,
        'status': status,
        'author_group_ids': list(author_group_ids),
    }


class ScopeTest(unittest.TestCase):
    def test_operator_sees_only_own(self):
        me = ctx(role='operator', user_id=10)
        self.assertEqual(access.visibility_scope(me), access.SCOPE_OWN)
        self.assertTrue(access.can_view_ticket(me, ticket(created_by=10)))
        self.assertFalse(access.can_view_ticket(me, ticket(created_by=11)))

    def test_trainer_and_trainee_are_no_wider_than_operator(self):
        """Тренер видит «всё» в других разделах, но переписка — не его дело."""
        for role in ('trainer', 'trainee'):
            me = ctx(role=role, user_id=10)
            self.assertEqual(access.visibility_scope(me), access.SCOPE_OWN, role)
            self.assertFalse(access.can_view_ticket(me, ticket(created_by=11)), role)

    def test_supervisor_sees_own_group_members(self):
        me = ctx(role='sv', user_id=10, groups=(5, 6))
        self.assertEqual(access.visibility_scope(me), access.SCOPE_GROUPS)
        self.assertTrue(access.can_view_ticket(
            me, ticket(created_by=11, author_group_ids=(6,))))
        self.assertFalse(access.can_view_ticket(
            me, ticket(created_by=11, author_group_ids=(7,))))

    def test_department_head_is_bounded_by_department(self):
        """Назначение главой заменяет базовую роль admin — это семантика портала."""
        head = ctx(role='admin', user_id=10, headed=(3,))
        self.assertFalse(access.is_global_admin(head))
        self.assertEqual(access.visibility_scope(head), access.SCOPE_DEPARTMENT)
        self.assertTrue(access.can_view_ticket(head, ticket(created_by=11, department_id=3)))
        self.assertFalse(access.can_view_ticket(head, ticket(created_by=11, department_id=4)))

    def test_department_head_sees_queue_of_own_department(self):
        """Обращение сотрудника чужого отдела в НАШУ очередь глава видит."""
        head = ctx(role='admin', user_id=10, headed=(3,))
        self.assertTrue(access.can_view_ticket(
            head, ticket(created_by=11, department_id=9, queue_department_id=3)))

    def test_global_admin_sees_everything(self):
        for role in ('super_admin', 'admin'):
            me = ctx(role=role, user_id=10)
            self.assertEqual(access.visibility_scope(me), access.SCOPE_ALL, role)
            self.assertTrue(access.can_view_ticket(me, ticket(created_by=999)), role)


class ActionsTest(unittest.TestCase):
    def test_everyone_can_create(self):
        """Раздел затевался ровно ради этого: обращение заводит любой сотрудник."""
        for role in ('operator', 'trainee', 'trainer', 'sv', 'admin', 'super_admin'):
            self.assertTrue(access.can_create_ticket(ctx(role=role)), role)

    def test_only_global_admin_manages_queues(self):
        self.assertTrue(access.can_manage_queues(ctx(role='super_admin')))
        self.assertTrue(access.can_manage_queues(ctx(role='admin')))
        self.assertFalse(access.can_manage_queues(ctx(role='admin', headed=(3,))))
        self.assertFalse(access.can_manage_queues(ctx(role='sv', groups=(1,))))
        self.assertFalse(access.can_manage_queues(ctx(role='operator')))

    def test_closed_ticket_takes_no_more_replies(self):
        me = ctx(role='operator', user_id=10)
        for status in ('resolved', 'cancelled'):
            self.assertFalse(access.can_reply(me, ticket(created_by=10, status=status)), status)

    def test_closed_ticket_can_still_be_reopened(self):
        """Иначе решённое по ошибке обращение осталось бы закрытым навсегда."""
        me = ctx(role='operator', user_id=10)
        self.assertTrue(access.can_change_status(me, ticket(created_by=10, status='resolved')))

    def test_outsider_writes_nothing(self):
        """Виден тикет или нет — решает периметр; невидимый неприкасаем."""
        me = ctx(role='operator', user_id=10)
        alien = ticket(created_by=11)
        self.assertFalse(access.can_reply(me, alien))
        self.assertFalse(access.can_change_status(me, alien))
        self.assertFalse(access.can_delete_ticket(me, alien))

    def test_operator_never_deletes(self):
        """ТЗ #29: у оператора «без удаления»."""
        me = ctx(role='operator', user_id=10)
        self.assertFalse(access.can_delete_ticket(me, ticket(created_by=10)))
        self.assertTrue(access.can_delete_ticket(
            ctx(role='super_admin', user_id=1), ticket(created_by=10)))


class VisibilitySqlMatchesPythonTest(unittest.TestCase):
    """Обе формы правила обязаны говорить об одном и том же."""

    def test_global_admin_has_no_filter(self):
        sql, _params = queries.visibility_sql(ctx(role='super_admin'))
        self.assertEqual(sql, 'TRUE')

    def test_own_scope_filters_by_author_only(self):
        sql, params = queries.visibility_sql(ctx(role='operator', user_id=10))
        self.assertIn('t.created_by = %(viewer_id)s', sql)
        self.assertNotIn('group_operator_memberships', sql)
        self.assertNotIn('department_id', sql)
        self.assertEqual(params['viewer_id'], 10)

    def test_department_scope_covers_both_sides(self):
        """Как и в Python: и отдел автора, и отдел очереди."""
        sql, params = queries.visibility_sql(ctx(role='admin', user_id=10, headed=(3, 4)))
        self.assertIn('t.department_id = ANY(%(headed_departments)s)', sql)
        self.assertIn('q.department_id = ANY(%(headed_departments)s)', sql)
        self.assertEqual(params['headed_departments'], [3, 4])

    def test_group_scope_uses_current_membership_only(self):
        """Ушедший из группы уносит переписку с собой — как в can_view_ticket."""
        sql, params = queries.visibility_sql(ctx(role='sv', user_id=10, groups=(5,)))
        self.assertIn('group_operator_memberships', sql)
        self.assertIn('gom.start_date <= CURRENT_DATE', sql)
        self.assertIn('gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE', sql)
        self.assertEqual(params['viewer_groups'], [5])

    def test_author_clause_present_in_every_narrow_scope(self):
        """Свои обращения видны при любом периметре, иначе СВ потерял бы свои же."""
        for me in (ctx(role='operator', user_id=10),
                   ctx(role='sv', user_id=10, groups=(5,)),
                   ctx(role='admin', user_id=10, headed=(3,))):
            sql, _params = queries.visibility_sql(me)
            self.assertIn('t.created_by = %(viewer_id)s', sql)


class SchemaContractTest(unittest.TestCase):
    """То, на что опираются роуты, бот и интерфейс."""

    def test_status_set_matches_the_specification(self):
        """Набор статусов взят из ТЗ #29 целиком, чтобы не расширять CHECK потом."""
        self.assertEqual(
            schema.TICKET_STATUSES,
            ('open', 'in_progress', 'answered', 'resolved', 'cancelled'),
        )

    def test_check_constraint_lists_every_status(self):
        ddl = ' '.join(schema._STATEMENTS)
        for status in schema.TICKET_STATUSES:
            self.assertIn("'%s'" % status, ddl)

    def test_incoming_telegram_message_is_unique(self):
        """Без этого повторный апдейт Telegram положил бы ответ в нить дважды."""
        ddl = ' '.join(schema._STATEMENTS)
        self.assertIn('uq_crm_messages_tg', ddl)
        self.assertIn('ON crm_ticket_messages(tg_chat_id, tg_message_id)', ddl)

    def test_one_group_one_queue(self):
        """Иначе ответ в группе невозможно отнести к очереди."""
        ddl = ' '.join(schema._STATEMENTS)
        self.assertIn('uq_crm_queues_chat', ddl)
        self.assertIn('ON crm_queues(chat_id) WHERE chat_id IS NOT NULL', ddl)

    def test_unread_has_a_partial_index(self):
        """Колокол спрашивает только непрочитанное — полный индекс тут лишний."""
        ddl = ' '.join(schema._STATEMENTS)
        self.assertIn('idx_crm_tickets_unread', ddl)
        self.assertIn('WHERE author_unread_at IS NOT NULL', ddl)

    def test_queue_is_not_deleted_with_its_history(self):
        ddl = ' '.join(schema._STATEMENTS)
        self.assertIn('REFERENCES crm_queues(id) ON DELETE RESTRICT', ddl)


class BellSourceTest(unittest.TestCase):
    """Раздел обязан быть в колоколе и обязан иметь триггер."""

    def test_source_registered(self):
        from notifications import sources
        self.assertIn('crm', sources.SOURCES)
        self.assertIn('crm', sources._HANDLERS)

    def test_source_is_not_clearable_by_the_bell(self):
        """«Вам ответили» снимается прочтением ответа, а не взглядом на колокол."""
        from notifications import sources

        class Cursor:
            def execute(self, *_args, **_kwargs):
                raise AssertionError('источник не должен ничего гасить')

        self.assertFalse(sources.mark_seen(Cursor(), 1, 'crm'))

    def test_trigger_wakes_the_author_only(self):
        database = (ROOT / 'database.py').read_text(encoding='utf-8')
        start = database.index('def _init_bell_notify_schema_tx(self, cursor):')
        block = database[start:database.index('def _init_amo_leads_schema_tx', start)]
        self.assertIn("TG_TABLE_NAME = 'crm_tickets'", block)
        self.assertIn('targets := ARRAY[NEW.created_by]', block)

    def test_trigger_ignores_plain_thread_writes(self):
        """Исходящее сообщение двигает last_message_at — колоколу это не событие."""
        database = (ROOT / 'database.py').read_text(encoding='utf-8')
        start = database.index('def _init_bell_notify_schema_tx(self, cursor):')
        block = database[start:database.index('def _init_amo_leads_schema_tx', start)]
        self.assertIn('AFTER UPDATE OF author_unread_at, status, delivery_status', block)
        self.assertIn('OLD.author_unread_at IS DISTINCT FROM NEW.author_unread_at', block)

    def test_missing_table_does_not_kill_other_triggers(self):
        """Схема раздела ставится под своим SAVEPOINT и может не примениться."""
        database = (ROOT / 'database.py').read_text(encoding='utf-8')
        start = database.index('def _init_bell_notify_schema_tx(self, cursor):')
        block = database[start:database.index('def _init_amo_leads_schema_tx', start)]
        self.assertIn('to_regclass', block)


class FrontendContractTest(unittest.TestCase):
    """Подписи статусов на сервере и в интерфейсе — одни и те же слова."""

    def test_view_labels_cover_every_status(self):
        view = (ROOT / 'src' / 'components' / 'crm' / 'CrmTicketsView.jsx').read_text(encoding='utf-8')
        block = re.search(r'const STATUS_META = \{(.*?)\n\};', view, re.S)
        self.assertIsNotNone(block, 'в разделе пропал STATUS_META')
        labelled = set(re.findall(r'^\s{4}([a-z_]+):', block.group(1), re.M))
        self.assertEqual(set(schema.TICKET_STATUSES), labelled)

    def test_view_labels_cover_every_priority(self):
        view = (ROOT / 'src' / 'components' / 'crm' / 'CrmTicketsView.jsx').read_text(encoding='utf-8')
        block = re.search(r'const PRIORITY_META = \{(.*?)\n\};', view, re.S)
        self.assertIsNotNone(block)
        labelled = set(re.findall(r'^\s{4}([a-z_]+):', block.group(1), re.M))
        self.assertEqual(set(schema.TICKET_PRIORITIES), labelled)

    def test_section_is_reachable_for_every_role(self):
        """Пункт меню объявлен в общей части сайдбара, а не в ролевой ветке.

        Раздел открыт всем сотрудникам, и продублировать пункт по ветвям
        (админ / глава / СВ / оператор) значило бы четыре места, где его легко
        забыть — в проекте это уже случалось: раздел выдан, а пункта нет.
        """
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertEqual(app.count("handleSidebarViewNavigation(e, 'crm_tickets')"), 1)
        # Гард видимости не должен выкидывать сотрудника отдела с allowlist'ом.
        self.assertIn("if (view === 'crm_tickets') return;", app)
        # Тренеру раздел тоже нужен, а он ходит по своему списку разделов.
        trainer_block = re.search(r'TRAINER_ALLOWED_VIEWS = Object\.freeze\(\[(.*?)\]\)', app, re.S)
        self.assertIsNotNone(trainer_block)
        self.assertIn("'crm_tickets'", trainer_block.group(1))


class RecordingCursor:
    """Курсор, который проверяет запрос на «лишний» процент и ничего не отдаёт.

    psycopg2 подставляет параметры ровно оператором % Python, поэтому забытый
    экранированный процент (ILIKE-шаблон, trim, LIKE в фильтре) ловится тем же
    оператором — без базы и без сети.
    """

    def __init__(self):
        self.queries = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.queries.append(sql)
        if params is None:
            if '%' in sql:
                raise AssertionError('placeholder без параметров: %r' % sql[:120])
            return
        if isinstance(params, dict):
            sql % {key: "'x'" for key in params}
        else:
            sql % tuple("'x'" for _ in params)

    def fetchall(self):
        return []

    def fetchone(self):
        return tuple([1] + [None] * 40)


class SqlComposesTest(unittest.TestCase):
    """Каждая ветка фильтров обязана давать корректный для psycopg2 запрос.

    Ветка, по которой не ходили руками, — самое подходящее место для лишнего
    процента: раздел работает, а поиск по номеру отдаёт 500 у одного человека.
    """

    def all_scopes(self):
        return (
            ctx(role='operator', user_id=10),
            ctx(role='sv', user_id=10, groups=(5,)),
            ctx(role='admin', user_id=10, headed=(3,)),
            ctx(role='super_admin', user_id=10),
        )

    def test_list_and_counters_compose_in_every_scope(self):
        cursor = RecordingCursor()
        for me in self.all_scopes():
            for search in (None, 'бонус', '123', '100%'):
                queries.list_tickets(cursor, me, status=['open', 'answered'], queue_id=3,
                                     mine=True, unread_only=True, search=search,
                                     limit=10, offset=20)
                queries.list_tickets(cursor, me, search=search)
            queries.counters(cursor, me)
        self.assertTrue(cursor.queries)

    def test_every_write_and_read_composes(self):
        cursor = RecordingCursor()
        queries.load_access_context(cursor, 10)
        queries.get_ticket(cursor, 1, 10)
        queries.list_messages(cursor, 1)
        queries.list_events(cursor, 1)
        queries.list_queues(cursor, include_inactive=True, expose_chat_id=True)
        queries.delivery_payload(cursor, 1)
        queries.bot_chats(cursor)
        queries.find_ticket_by_tg_message(cursor, -1001, 2)
        queries.find_message_attachment(cursor, 1, 2)
        queries.unread_for_bell(cursor, 10, 5)
        queries.touch_inbound(cursor, 1)
        queries.touch_outbound(cursor, 1)
        queries.set_delivery(cursor, 1, status='sent', chat_id=-1001, message_id=2)
        queries.add_message(cursor, ticket_id=1, direction='in', body='ответ',
                            attachment={'kind': 'photo', 'file_id': 'a'})
        queries.add_event(cursor, ticket_id=1, kind='created', payload={'queue': 'iTaxi'})
        queries.mark_seen_by_author(cursor, 1, 10)
        queries.set_status(cursor, 1, 'resolved', actor_user_id=2, actor_name='x',
                           notify_author=True)
        queries.create_queue(cursor, title='iTaxi', chat_id=-1001)
        queries.update_queue(cursor, 1, {'title': 'x', 'is_active': True, 'chat_id': -1001})
        queries.create_topic(cursor, queue_id=1, title='Бонусы')
        queries.update_topic(cursor, 1, {'title': 'x', 'sort_order': 1})
        queries.delete_queue(cursor, 1)
        queries.delete_topic(cursor, 1)
        self.assertGreater(len(cursor.queries), 20)


if __name__ == '__main__':
    unittest.main()
