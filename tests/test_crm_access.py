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
import io
import unittest
from pathlib import Path

from crm import access, queries, schema

ROOT = Path(__file__).resolve().parents[1]


def ctx(role='operator', user_id=10, department_id=1, headed=(), groups=(),
        department_code='szov', headed_codes=None):
    """Портрет зрителя. По умолчанию — сотрудник СЗоВ: раздел выкатан на него."""
    return {
        'user_id': user_id,
        'name': 'Тест',
        'role': role,
        'department_id': department_id,
        'department_code': department_code,
        'headed_department_ids': list(headed),
        'headed_department_codes': (list(headed_codes) if headed_codes is not None
                                    else (['szov'] if headed else [])),
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
        # Вне СЗоВ: внутри отдела раздел открыт всем, и периметр там общий.
        me = ctx(role='operator', user_id=10, department_code='op')
        self.assertEqual(access.visibility_scope(me), access.SCOPE_OWN)
        self.assertTrue(access.can_view_ticket(me, ticket(created_by=10)))
        self.assertFalse(access.can_view_ticket(me, ticket(created_by=11)))

    def test_trainer_sees_nothing_even_inside_the_department(self):
        """Тренер видит «всё» в других разделах, но переписка — не его дело."""
        me = ctx(role='trainer', user_id=10, department_code='szov')
        self.assertFalse(access.can_open_section(me))
        self.assertEqual(access.visibility_scope(me), access.SCOPE_OWN)
        self.assertFalse(access.can_view_ticket(me, ticket(created_by=11)))

    def test_everyone_admitted_to_the_section_sees_every_ticket(self):
        """Просьба СЗоВ 18.08.2026 (задача #181).

        По одному водителю несколько сотрудников заводили несколько одинаковых
        обращений: найти уже открытое было нельзя, потому что чужое не
        показывалось. Теперь периметр один — вход в раздел.
        """
        for me in (ctx(role='sv', user_id=10, groups=(5,)),
                   ctx(role='admin', user_id=10, headed=(3,)),
                   ctx(role='operator', user_id=20),          # пилотный оператор
                   ctx(role='super_admin', user_id=1)):
            self.assertTrue(access.can_open_section(me), me['role'])
            self.assertEqual(access.visibility_scope(me), access.SCOPE_ALL, me['role'])
            self.assertTrue(access.can_view_ticket(
                me, ticket(created_by=999, department_id=4, author_group_ids=(7,))), me['role'])

    def test_narrow_scopes_still_apply_outside_the_section(self):
        """Периметры «отдел» и «свои группы» никуда не делись.

        Они выключены не потому, что от них отказались, а потому, что сейчас
        раздел выкатан на один отдел и вход в него и есть периметр. Расширится
        выкат — правило снова заработает, поэтому оно проверяется.
        """
        head = ctx(role='admin', user_id=10, headed=(3,), headed_codes=['op'],
                   department_code='op')
        self.assertFalse(access.can_open_section(head))
        self.assertFalse(access.is_global_admin(head))
        self.assertEqual(access.visibility_scope(head), access.SCOPE_DEPARTMENT)
        self.assertTrue(access.can_view_ticket(head, ticket(created_by=11, department_id=3)))
        self.assertFalse(access.can_view_ticket(head, ticket(created_by=11, department_id=4)))

        sv = ctx(role='sv', user_id=10, groups=(5, 6), department_code='op')
        self.assertFalse(access.can_open_section(sv))
        self.assertEqual(access.visibility_scope(sv), access.SCOPE_GROUPS)
        self.assertTrue(access.can_view_ticket(
            sv, ticket(created_by=11, author_group_ids=(6,))))
        self.assertFalse(access.can_view_ticket(
            sv, ticket(created_by=11, author_group_ids=(7,))))

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


class SectionGateTest(unittest.TestCase):
    """Кого пускают в раздел: весь отдел СЗоВ и глобальные админы.

    Проверяется отдельно от видимости обращений: это два разных вопроса —
    «открыт ли раздел» и «что в нём видно».
    """

    def test_every_role_of_the_department_is_let_in(self):
        """Решение владельца 19.08.2026: раздел открыт всему СЗоВ.

        Роль внутри отдела значения не имеет — обращение заводит тот, у кого
        возник вопрос, а не «ответственный за обращения».
        """
        for role in ('operator', 'trainee', 'sv', 'supervisor'):
            self.assertTrue(access.can_open_section(
                ctx(role=role, user_id=99999, department_code='szov')), role)

    def test_other_departments_are_not(self):
        """«Только СЗоВ» — граница отдела строгая, как у «Табло СЗоВ»."""
        for code in ('tez', 'op', 'front_office', 'marketing', None, ''):
            self.assertFalse(access.can_open_section(
                ctx(role='operator', user_id=99999, department_code=code)), repr(code))

    def test_trainer_is_the_only_exception_inside_the_department(self):
        """Тренер видит «всё» в других разделах, но переписка не его дело."""
        self.assertFalse(access.can_open_section(
            ctx(role='trainer', user_id=77, department_code='szov')))

    def test_head_of_the_department_is_let_in(self):
        self.assertTrue(access.can_open_section(
            ctx(role='admin', user_id=1, headed=(1,), headed_codes=['szov'],
                department_code='szov')))

    def test_head_of_another_department_is_not(self):
        self.assertFalse(access.can_open_section(
            ctx(role='admin', user_id=2, headed=(560,), headed_codes=['tez'],
                department_code='tez')))

    def test_global_admins_are_let_in(self):
        for role in ('super_admin', 'admin'):
            self.assertTrue(access.can_open_section(
                ctx(role=role, user_id=5, department_code=None)), role)

    def test_creating_requires_the_section(self):
        """Кнопку «Новое обращение» нельзя дать тому, кому раздел закрыт."""
        self.assertFalse(access.can_create_ticket(
            ctx(role='operator', user_id=99999, department_code='tez')))
        self.assertTrue(access.can_create_ticket(
            ctx(role='operator', user_id=99999, department_code='szov')))

    def test_capabilities_report_the_gate(self):
        """Фронт рисует раздел по этой сводке, а не по роли."""
        self.assertTrue(access.capabilities(ctx(role='super_admin'))['can_open'])
        self.assertFalse(access.capabilities(
            ctx(role='operator', user_id=99999, department_code='tez'))['can_open'])


class ActionsTest(unittest.TestCase):

    def test_queues_are_managed_by_admins_heads_and_supervisors(self):
        """Просьба владельца 21.08.2026: супервайзеру нужны очереди.

        Глава отдела добавлен вместе с ним — иначе супервайзер может то, чего
        не может его руководитель. Оператору очереди по-прежнему закрыты:
        привязка чата отправляет обращения в чужую рабочую группу.
        """
        self.assertTrue(access.can_manage_queues(ctx(role='super_admin')))
        self.assertTrue(access.can_manage_queues(ctx(role='admin')))
        self.assertTrue(access.can_manage_queues(ctx(role='admin', headed=(3,))))
        self.assertTrue(access.can_manage_queues(ctx(role='sv', groups=(1,))))
        self.assertFalse(access.can_manage_queues(ctx(role='operator')))
        self.assertFalse(access.can_manage_queues(ctx(role='trainer')))

    def test_managing_queues_still_needs_the_section(self):
        """Периметр очередей — это периметр раздела: гейт can_open_section в
        декораторе стоит РАНЬШЕ, и супервайзер чужого отдела до очередей не
        доходит вовсе."""
        outsider = ctx(role='sv', groups=(1,), department_code='tez')
        self.assertFalse(access.can_open_section(outsider))

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
        me = ctx(role='operator', user_id=10, department_code='op')
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

    def test_queue_manager_is_not_a_deleter(self):
        """Настраивать очереди СВ и главе отдела дали, удалять обращения — нет.

        Права разъехались осознанно: ошибочная привязка чата чинится второй
        привязкой, а удалённое обращение не возвращается. Проверяем именно эту
        пару, потому что «может настраивать раздел» так и тянет прочитать как
        «может в разделе всё».
        """
        for me in (ctx(role='sv', user_id=10),
                   ctx(role='admin', user_id=10, headed=(1,))):
            self.assertTrue(access.can_manage_queues(me), me['role'])
            self.assertFalse(access.can_delete_ticket(me, ticket(created_by=11)), me['role'])

    def test_unknown_role_deletes_nothing(self):
        """Незнакомая роль сводится к оператору — закрыто, а не открыто."""
        me = ctx(role='директор по чему-нибудь', user_id=10)
        self.assertFalse(access.can_delete_ticket(me, ticket(created_by=10)))

    def test_closed_ticket_is_still_deletable_by_the_admin(self):
        """Прогоны раздела как раз закрыты; запрет на закрытые — правило ОТВЕТА.

        Скопировать его в удаление (соблазн: рядом, в одном файле, похоже
        выглядит) значило бы сделать мусор невыносимым — «Отменено» удалить
        нельзя, а вернуть в работу, чтобы удалить, никто не догадается.
        """
        admin = ctx(role='super_admin', user_id=1)
        for status in ('resolved', 'cancelled'):
            self.assertTrue(access.can_delete_ticket(
                admin, ticket(created_by=10, status=status)), status)

    def test_capabilities_carry_the_admin_flag_for_the_frontend(self):
        """Лента рисует режим отбора по этому признаку, а не по названию роли."""
        self.assertTrue(access.capabilities(ctx(role='super_admin', user_id=1))['is_global_admin'])
        self.assertFalse(access.capabilities(ctx(role='sv', user_id=10))['is_global_admin'])


class VisibilitySqlMatchesPythonTest(unittest.TestCase):
    """Обе формы правила обязаны говорить об одном и том же."""

    def test_global_admin_has_no_filter(self):
        sql, _params = queries.visibility_sql(ctx(role='super_admin'))
        self.assertEqual(sql, 'TRUE')

    def test_own_scope_filters_by_author_only(self):
        sql, params = queries.visibility_sql(
            ctx(role='operator', user_id=10, department_code='op'))
        self.assertIn('t.created_by = %(viewer_id)s', sql)
        self.assertNotIn('group_operator_memberships', sql)
        self.assertNotIn('department_id', sql)
        self.assertEqual(params['viewer_id'], 10)

    def test_section_members_get_no_filter(self):
        """Кого пустили в раздел, тот видит всё — и в списке тоже (#181)."""
        for me in (ctx(role='sv', user_id=10, groups=(5,)),
                   ctx(role='admin', user_id=10, headed=(3,))):
            sql, _params = queries.visibility_sql(me)
            self.assertEqual(sql, 'TRUE', me['role'])

    def test_department_scope_covers_both_sides(self):
        """Как и в Python: и отдел автора, и отдел очереди."""
        sql, params = queries.visibility_sql(
            ctx(role='admin', user_id=10, headed=(3, 4), headed_codes=['op'],
                department_code='op'))
        self.assertIn('t.department_id = ANY(%(headed_departments)s)', sql)
        self.assertIn('q.department_id = ANY(%(headed_departments)s)', sql)
        self.assertEqual(params['headed_departments'], [3, 4])

    def test_group_scope_uses_current_membership_only(self):
        """Ушедший из группы уносит переписку с собой — как в can_view_ticket."""
        sql, params = queries.visibility_sql(
            ctx(role='sv', user_id=10, groups=(5,), department_code='op'))
        self.assertIn('group_operator_memberships', sql)
        self.assertIn('gom.start_date <= CURRENT_DATE', sql)
        self.assertIn('gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE', sql)
        self.assertEqual(params['viewer_groups'], [5])

    def test_author_clause_present_in_every_narrow_scope(self):
        """Свои обращения видны при любом периметре, иначе СВ потерял бы свои же."""
        for me in (ctx(role='operator', user_id=10, department_code='op'),
                   ctx(role='sv', user_id=10, groups=(5,), department_code='op'),
                   ctx(role='admin', user_id=10, headed=(3,), headed_codes=['op'],
                       department_code='op')):
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

    def test_parks_come_from_the_wiki_catalog(self):
        """Второй список парков означал бы, что оператор выбирает из одного
        набора, а справочник компании живёт другим."""
        cursor = RecordingCursor()
        queries.taxi_parks(cursor)
        sql = ' '.join(cursor.queries[0].split())
        self.assertIn('FROM wiki_taxi_parks', sql)
        self.assertIn("p.status = 'active'", sql)
        # В обращении хранится НАЗВАНИЕ парка: обращение остаётся читаемым, даже
        # если парк потом переименуют или уберут из справочника.
        self.assertIn('SELECT p.name', sql)

    def test_reply_link_column_is_migrated(self):
        """Столбец добавлен ALTER'ом: на боевой базе таблица уже существует, и
        CREATE TABLE IF NOT EXISTS её не тронет."""
        migrations = ' '.join(schema._MIGRATIONS)
        self.assertIn('crm_ticket_messages ADD COLUMN IF NOT EXISTS reply_to_tg_message_id',
                      migrations)
        ddl = ' '.join(' '.join(schema._STATEMENTS).split())
        self.assertIn('reply_to_tg_message_id BIGINT', ddl)

    def test_iin_is_indexed_for_search(self):
        """Без индекса поиск по ИИН стал бы проходом по всей таблице."""
        ddl = ' '.join(' '.join(schema._STATEMENTS).split())
        self.assertIn('idx_crm_tickets_iin_trgm', ddl)
        self.assertIn("ON crm_tickets USING gin ((answers ->> 'iin') gin_trgm_ops)", ddl)

    def test_scenario_queues_are_seeded(self):
        """Очередь ищется сценарием по коду: не засеяли — тематика недоступна."""
        codes = {code for code, _title, _descr, _order in schema._SEED_QUEUES}
        self.assertEqual(codes, {'itaxi_sapar', 'parcels', 'yandex_delivery'})

    def test_unread_has_a_partial_index(self):
        """Колокол спрашивает только непрочитанное — полный индекс тут лишний."""
        ddl = ' '.join(schema._STATEMENTS)
        self.assertIn('idx_crm_tickets_unread', ddl)
        self.assertIn('WHERE author_unread_at IS NOT NULL', ddl)

    def test_every_list_filter_has_an_index_with_the_sort_column(self):
        """Индекс без столбца сортировки отдаёт строки, но база их всё равно сортирует."""
        ddl = ' '.join(schema._STATEMENTS)
        for index in ('idx_crm_tickets_author_recent', 'idx_crm_tickets_recent',
                      'idx_crm_tickets_queue_recent', 'idx_crm_tickets_department_recent',
                      'idx_crm_tickets_author_attention'):
            self.assertIn(index, ddl, index)
        # Пять индексов и ровно пять хвостов сортировки: каждый список
        # раздела читается по индексу, а не сортируется базой.
        self.assertEqual(ddl.count('last_message_at DESC, id DESC'), 5)

    def test_unread_first_order_matches_its_index_word_for_word(self):
        """Порядок с ВЫРАЖЕНИЕМ берёт индекс только при дословном совпадении.

        Стоит написать в ORDER BY `author_unread_at IS NOT NULL DESC`, а в индексе
        оставить `IS NULL` — и вместо чтения первых сорока строк база отсортирует
        весь периметр. Ошибки не будет вовсе — только тишина и медленный список.
        """
        ddl = ' '.join(' '.join(schema._STATEMENTS).split())
        self.assertIn('ON crm_tickets(created_by, (author_unread_at IS NULL), '
                      'last_message_at DESC, id DESC)', ddl)
        source = io.open(queries.__file__, encoding='utf-8').read()
        self.assertIn("'(t.author_unread_at IS NULL), t.last_message_at DESC, t.id DESC'", source)

    def test_unread_counter_is_a_column_not_a_subquery(self):
        """Счётчик непрочитанного — столбец, и он обязан приехать МИГРАЦИЕЙ.

        В CREATE TABLE ему нельзя: на развёрнутой базе CREATE TABLE IF NOT EXISTS
        ничего не добавляет, и столбец так и не появится.
        """
        migrations = ' '.join(schema._MIGRATIONS)
        self.assertIn('crm_tickets ADD COLUMN IF NOT EXISTS author_unread_count', migrations)
        # Индекса по нему нет и не надо: столбец только читается вместе со
        # строкой, ни один запрос по нему не фильтрует и не сортирует.
        self.assertNotIn('author_unread_count', ' '.join(schema._STATEMENTS))

    def test_search_index_covers_the_columns_the_query_names(self):
        """Индекс по ВЫРАЖЕНИЮ (COALESCE) не подходит запросу по столбцу: замер
        показал проход по таблице на 124 мс, пока в индексе стоял COALESCE."""
        ddl = ' '.join(schema._STATEMENTS)
        self.assertIn('idx_crm_tickets_search_trgm', ddl)
        self.assertIn('client_name gin_trgm_ops', ddl)
        self.assertNotIn("COALESCE(client_name, '') gin_trgm_ops", ddl)
        self.assertIn('idx_crm_tickets_phone_trgm', ddl)

    def test_trigram_indexes_survive_a_base_without_the_extension(self):
        """На базе без pg_trgm раздел обязан развернуться, просто без ускорения."""
        ddl = ' '.join(schema._STATEMENTS)
        self.assertIn("pg_extension WHERE extname = 'pg_trgm'", ddl)

    def test_sort_column_is_not_nullable(self):
        """NULL заставил бы обернуть ORDER BY в COALESCE и потерять индекс."""
        ddl = ' '.join(schema._STATEMENTS)
        self.assertIn('last_message_at    TIMESTAMP NOT NULL', ddl)

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

    def test_menu_item_is_declared_once_and_gated(self):
        """Пункт объявлен ОДИН раз в общей части сайдбара и закрыт предикатом.

        Продублировать его по ролевым ветвям (админ / глава / СВ / оператор)
        значило бы четыре места, где легко забыть — в проекте это уже случалось:
        раздел выдан, а пункта в меню нет, открывается только по адресу.
        """
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertEqual(app.count("handleSidebarViewNavigation(e, 'crm_tickets')"), 1)
        self.assertIn('{canAccessCrmSection && (', app)
        # Гард видимости пускает только допущенных, но и не выкидывает их
        # allowlist'ом отдела (у оператора СЗоВ он есть).
        self.assertIn("if (view === 'crm_tickets' && canAccessCrmSection) return;", app)
        # Сам раздел тоже за предикатом: спрятанный пункт — это не доступ.
        self.assertIn('view === "crm_tickets" && canAccessCrmSection', app)

    def test_pilot_list_is_gone_from_the_frontend_too(self):
        """Пилот закончился. Оставшийся список id молча сужал бы периметр."""
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertNotIn('CRM_PILOT_USER_IDS', app)

    def test_frontend_gate_has_no_role_condition_beyond_the_trainer(self):
        """Пункт меню и API обязаны сходиться: раздел открыт всему отделу.

        Разойдись они — получим «пункт виден, а API отдаёт 403» либо наоборот,
        и оба случая человек увидит раньше нас.
        """
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        gate = app.split('const canAccessCrmSectionForUser')[1].split('};')[0]
        self.assertIn("role === 'trainer'", gate)
        self.assertNotIn('isSupervisorRole', gate)

    def test_department_code_matches_the_backend(self):
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        self.assertIn("CRM_SECTION_DEPARTMENT_CODE = '%s'" % access.SECTION_DEPARTMENT_CODE, app)

    def test_trainer_list_does_not_carry_the_section(self):
        """Тренер вне периметра выката, и в его списке разделов места ему нет."""
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        trainer_block = re.search(r'TRAINER_ALLOWED_VIEWS = Object\.freeze\(\[(.*?)\]\)', app, re.S)
        self.assertIsNotNone(trainer_block)
        self.assertNotIn("'crm_tickets'", trainer_block.group(1))

    def test_delete_button_asks_the_server_who_may_delete(self):
        """Кнопка удаления в карточке стоит за правом, пришедшим с сервера.

        Право живёт в crm/access.py, и второй его слепок во фронте («роль
        super_admin») разошёлся бы с первым молча: кнопка есть, сервер отвечает
        403 — и человек считает, что раздел сломан.
        """
        view = (ROOT / 'src' / 'components' / 'crm' / 'CrmTicketsView.jsx').read_text(encoding='utf-8')
        card = view.split('const TicketCard = ({')[1].split('\nconst ')[0]
        self.assertIn('permissions.can_delete', card)
        # Своего слепка правила во фронте нет — ни роли, ни списка id.
        self.assertNotIn('super_admin', card)
        # Ровно один путь удаления, и он тот же, что у сервера (DELETE по id).
        self.assertIn('axios.delete(`${apiBaseUrl}/api/crm/tickets/${ticketId}`', card)
        # В файле есть второй axios.delete — удаление ОЧЕРЕДИ. Перепутать их
        # копипастой стоило бы очереди вместе со всеми её обращениями.
        self.assertNotIn('/api/crm/queues/', card)
        # Никакой своей ручки «удалить пачкой» раздел не выдумывает: пачка —
        # это те же одиночные запросы, и права проверяются по каждому.
        self.assertNotIn('/tickets/bulk', view)

    def test_delete_is_confirmed_and_says_what_stays_in_telegram(self):
        """Удаление необратимо, а нить в рабочей группе остаётся жить."""
        view = (ROOT / 'src' / 'components' / 'crm' / 'CrmTicketsView.jsx').read_text(encoding='utf-8')
        warning = re.search(r'const DeleteWarning = \(\{.*?\}\) => \((.*?)\n\);', view, re.S)
        self.assertIsNotNone(warning, 'из раздела пропало предупреждение об удалении')
        self.assertIn('Вернуть нельзя', warning.group(1))
        self.assertIn('Telegram', warning.group(1))
        # Оба вопроса (одно обращение и пачка) объясняют одно и то же одним
        # текстом: два разных объяснения читаются как два разных действия.
        self.assertEqual(view.count('<DeleteWarning'), 2)
        # И согласуется с числом: «переписка по нему» над списком из трёх строк
        # описывает не то, что произойдёт.
        self.assertIn('many={picked.size > 1}', view)

    def test_select_mode_belongs_to_the_global_admin_only(self):
        """Режим отбора к удалению открыт по тому же признаку, что и само право."""
        view = (ROOT / 'src' / 'components' / 'crm' / 'CrmTicketsView.jsx').read_text(encoding='utf-8')
        self.assertIn("const canDelete = !!capabilities?.is_global_admin;", view)
        self.assertIn('canDelete && !!tickets.length', view)

    def test_list_row_stays_a_single_button(self):
        """Кнопка внутри кнопки — невалидная разметка, и браузеры её ломают.

        Отметка к удалению поэтому меняет СМЫСЛ нажатия на строку, а не
        добавляет вторую кнопку внутрь первой. Проверяем счётом, а не глазами:
        соблазн «добавить сюда ещё одну кнопочку» возвращается в каждой правке
        ленты.
        """
        view = (ROOT / 'src' / 'components' / 'crm' / 'CrmTicketsView.jsx').read_text(encoding='utf-8')
        start = view.index('const TicketRow = memo(')
        row = view[start:view.index('/* ─── Сообщение в переписке', start)]
        self.assertEqual(row.count('<button'), 1, 'в строке ленты завелась вторая кнопка')

    def test_feed_column_may_shrink_on_the_phone(self):
        """Запрет на сжатие держит ленте её 360 px — но только в РЯДУ.

        На телефоне колонки встают друг под другом, и там тот же `shrink-0`
        значил «не становись ниже своего содержимого»: лента вырастала до
        высоты всех строк, вылезала за карточку с overflow-hidden и обрезалась,
        а внутренняя прокрутка не включалась никогда (scrollHeight равнялся
        clientHeight). Всё за первым экраном — строки, подвал, кнопка удаления
        отобранных — было недостижимо: 874 px содержимого в 521 px карточки.
        """
        view = (ROOT / 'src' / 'components' / 'crm' / 'CrmTicketsView.jsx').read_text(encoding='utf-8')
        column = re.search(r'className=\{`flex w-full min-h-0[^`]*`\}', view)
        self.assertIsNotNone(column, 'пропала колонка ленты')
        self.assertIn('lg:shrink-0', column.group(0))
        self.assertNotIn(' shrink-0', column.group(0))

    def test_counters_are_recomputed_after_a_delete(self):
        """Иначе в сайдбаре остаётся бейдж, за которым уже ничего нет."""
        view = (ROOT / 'src' / 'components' / 'crm' / 'CrmTicketsView.jsx').read_text(encoding='utf-8')
        self.assertIn('const refreshCounters =', view)
        self.assertIn('refreshCounters();', view)

    def test_section_does_not_open_its_own_realtime_channel(self):
        """Второй поток на пользователя занял бы ещё нить waitress — их считаные."""
        view = (ROOT / 'src' / 'components' / 'crm' / 'CrmTicketsView.jsx').read_text(encoding='utf-8')
        self.assertNotIn('EventSource', view)
        self.assertNotIn('/stream', view)
        self.assertNotIn('setInterval', view)
        app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8')
        # Раздел двигает пульс по ОТПЕЧАТКУ источника, а не по факту перечитки
        # сводки: иначе широковещательный тычок дёргал бы всех разом.
        self.assertIn('crmDigestRef', app)


class RecordingCursor:
    """Курсор, который проверяет запрос на «лишний» процент и ничего не отдаёт.

    psycopg2 подставляет параметры ровно оператором % Python, поэтому забытый
    экранированный процент (ILIKE-шаблон, trim, LIKE в фильтре) ловится тем же
    оператором — без базы и без сети.
    """

    def __init__(self):
        self.queries = []
        self.params = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.queries.append(sql)
        self.params = params
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


class ListContractTest(unittest.TestCase):
    """Список отдаёт порцию и признак «есть ещё» — без точного количества.

    Точное «всего N» означало бы полный проход по периметру на каждый фильтр и
    каждую букву в поиске; на 200 тыс. обращений это измеримо дорого, а человеку
    нужно только «показать ещё».
    """

    def test_asks_for_one_row_more_than_it_returns(self):
        cursor = RecordingCursor()
        queries.list_tickets(cursor, ctx(role='super_admin'), limit=40)
        self.assertEqual(cursor.params['limit'], 41)

    def test_no_count_over_in_the_query(self):
        cursor = RecordingCursor()
        queries.list_tickets(cursor, ctx(role='super_admin'), limit=40)
        self.assertNotIn('COUNT(*) OVER', cursor.queries[0])

    def test_order_is_plain_columns_so_the_index_can_serve_it(self):
        """COALESCE или выражение в ORDER BY отменяют чтение по индексу."""
        cursor = RecordingCursor()
        queries.list_tickets(cursor, ctx(role='super_admin'), limit=40)
        sql = ' '.join(cursor.queries[0].split())
        self.assertIn('ORDER BY t.last_message_at DESC, t.id DESC', sql)
        self.assertNotIn('COALESCE(t.last_message_at', sql)

    def test_digits_search_looks_up_the_number_and_the_phone(self):
        """t.id::text = ... не берёт индекс по id, поэтому номер приводим к числу."""
        cursor = RecordingCursor()
        queries.list_tickets(cursor, ctx(role='super_admin'), search='150000')
        sql = cursor.queries[0]
        self.assertIn('t.id = %(search_id)s', sql)
        self.assertIn('t.client_phone ILIKE', sql)
        self.assertNotIn('t.subject ILIKE', sql)
        self.assertEqual(cursor.params['search_id'], 150000)

    def test_long_digits_are_a_phone_not_a_ticket_number(self):
        cursor = RecordingCursor()
        queries.list_tickets(cursor, ctx(role='super_admin'), search='77001234567')
        self.assertIsNone(cursor.params['search_id'])

    def test_iin_search_finds_every_ticket_of_one_driver(self):
        """Просьба СЗоВ 18.08.2026 (задача #182).

        Двенадцатизначный ИИН — это цифры, поэтому он попадал в числовую ветку,
        а та смотрела только номер обращения и телефон: обращение с этим ИИН в
        теме существовало, а поиск отдавал пусто.
        """
        cursor = RecordingCursor()
        queries.list_tickets(cursor, ctx(role='super_admin'), search='060606202020')
        sql = ' '.join(cursor.queries[0].split())
        self.assertIn("(t.answers ->> 'iin') ILIKE %(search)s", sql)
        self.assertEqual(cursor.params['search'], '%060606202020%')

    def test_search_is_never_narrowed_to_my_own_tickets(self):
        """Просьба СЗоВ 19.08.2026: поиск ищет по всем.

        Поиск нужен ровно для того, чтобы узнать, не завёл ли обращение по этому
        водителю кто-то другой. Суженный до своих, он на этот вопрос отвечает
        «нет» — и человек заводит дубль. Проверяется на сервере: правило, которое
        держится на том, что клиент не прислал параметр, — не правило.
        """
        cursor = RecordingCursor()
        queries.list_tickets(cursor, ctx(role='sv', user_id=10), mine=True,
                             search='060606202020')
        self.assertNotIn('t.created_by = %(viewer_id)s', cursor.queries[0])

        # Без поиска «Мои» работает как работал.
        queries.list_tickets(cursor, ctx(role='sv', user_id=10), mine=True)
        self.assertIn('t.created_by = %(viewer_id)s', cursor.queries[-1])

    def test_iin_search_uses_the_same_expression_as_its_index(self):
        """Индекс по выражению не подходит запросу по столбцу — уже наступали.

        Поэтому условие поиска и определение индекса сверяются буквально: разойдись
        они пробелом или кавычкой — поиск снова пошёл бы проходом по таблице, и
        заметили бы это не тестом, а секундами ожидания на проде.
        """
        cursor = RecordingCursor()
        queries.list_tickets(cursor, ctx(role='super_admin'), search='060606202020')
        sql = ' '.join(cursor.queries[0].split())
        ddl = ' '.join(' '.join(schema._STATEMENTS).split())
        self.assertIn("(answers ->> 'iin') gin_trgm_ops", ddl)
        self.assertIn("(t.answers ->> 'iin')", sql)

    def test_word_search_never_touches_the_number(self):
        """Смешанное ИЛИ заставляло базу идти по таблице: замер 270 мс."""
        cursor = RecordingCursor()
        queries.list_tickets(cursor, ctx(role='super_admin'), search='бонус')
        sql = cursor.queries[0]
        self.assertIn('t.subject ILIKE', sql)
        self.assertIn('t.body ILIKE', sql)
        self.assertIn('t.client_name ILIKE', sql)
        self.assertNotIn('t.id =', sql)

    def test_thread_carries_who_answered_whom(self):
        """В нить падает вся ветка обсуждения, и без этой связи непонятно, кому
        отвечали: сотрудники отвечают и боту, и друг другу."""
        cursor = RecordingCursor()
        queries.list_messages(cursor, 1)
        sql = ' '.join(cursor.queries[0].split())
        self.assertIn('reply_to_tg_message_id', sql)
        # Автор берётся по id из Telegram: имя сотрудник может сменить, а цвет
        # в переписке от этого переезжать не должен.
        self.assertIn('tg_from_id', sql)

    def test_reply_target_is_fetched_within_the_ticket(self):
        """Иначе оператор заставил бы бота ответить на чужое сообщение в группе."""
        cursor = RecordingCursor()
        queries.message_of_ticket(cursor, 1, 2)
        sql = ' '.join(cursor.queries[0].split())
        self.assertIn('WHERE id = %s AND ticket_id = %s', sql)

    def test_thread_has_a_ceiling(self):
        """Ушедшая в обсуждение группа не должна тянуть тысячу сообщений в браузер."""
        cursor = RecordingCursor()
        queries.list_messages(cursor, 1)
        self.assertIn('LIMIT', cursor.queries[0])

    def test_counters_left_only_what_is_displayed(self):
        """Считать то, что не показывают, — прямая плата за ничего."""
        cursor = RecordingCursor()
        result = queries.counters(cursor, ctx(role='super_admin'))
        self.assertEqual(set(result), {'unread'})
        # Условие «мой автор» в WHERE — иначе частичный индекс не включится.
        self.assertIn('t.created_by = %(viewer_id)s', cursor.queries[0])


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
        queries.taxi_parks(cursor)
        queries.find_ticket_by_tg_message(cursor, -1001, 2)
        queries.find_message_attachment(cursor, 1, 2)
        queries.message_of_ticket(cursor, 1, 2)
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


class SchemaOrderTest(unittest.TestCase):
    """Порядок разворота схемы: таблицы → ALTER'ы → индексы.

    Стоит здесь не из любви к порядку. 17.08.2026 выкат сценариев положил раздел
    на проде: индекс uq_crm_queues_code выполнился РАНЬШЕ, чем ALTER TABLE добавил
    столбец code. Внутри SAVEPOINT это откатило весь разворот схемы вместе с
    миграциями, и API отдавал 500 «column q.code does not exist». На пустой базе
    (и в любом тесте, который просто вызывает init) ошибка не воспроизводится —
    поэтому проверяется именно ПОРЯДОК.
    """

    class OrderCursor:
        def __init__(self):
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append(' '.join(str(sql).split()))

        def fetchone(self):
            return None

        def fetchall(self):
            return []

    def _run(self):
        cursor = self.OrderCursor()
        schema.init_crm_schema(cursor)
        return cursor.statements

    def test_columns_are_added_before_indexes_that_use_them(self):
        statements = self._run()
        added = {}
        for index, sql in enumerate(statements):
            match = re.search(r'ADD COLUMN IF NOT EXISTS (\w+)', sql)
            if match:
                added.setdefault(match.group(1), index)
        self.assertTrue(added, 'миграции столбцов пропали из разворота схемы')

        for index, sql in enumerate(statements):
            if 'CREATE INDEX' not in sql.upper() and 'CREATE UNIQUE INDEX' not in sql.upper():
                continue
            for column, added_at in added.items():
                # Ищем столбец как отдельное слово: 'code' не должен ловиться
                # внутри 'scenario_key' или названия индекса.
                if re.search(r'\(\s*%s|,\s*%s' % (column, column), sql):
                    self.assertLess(
                        added_at, index,
                        'индекс создаётся раньше столбца %s: %s' % (column, sql[:90]))

    def test_tables_come_first(self):
        statements = self._run()
        last_table = max(i for i, sql in enumerate(statements) if 'CREATE TABLE' in sql.upper())
        first_alter = min((i for i, sql in enumerate(statements) if sql.startswith('ALTER TABLE')),
                          default=len(statements))
        self.assertLess(last_table, first_alter,
                        'ALTER раньше CREATE TABLE — на чистой базе это падение')

    def test_seed_queues_match_the_scenarios(self):
        """Сценарий ищет очередь по коду: разойдутся — тематика «не настроена»."""
        from crm import scenarios
        seeded = {code for code, _title, _descr, _order in schema._SEED_QUEUES}
        needed = {item['queue_code'] for item in scenarios.SCENARIOS}
        self.assertEqual(needed - seeded, set(),
                         'у сценария нет засеянной очереди: %s' % (needed - seeded))


if __name__ == '__main__':
    unittest.main()
