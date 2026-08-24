# -*- coding: utf-8 -*-
"""Раздел «Тренинги»: справочник корпоративных тем.

Без базы: проверяется схема (порядок разворота, набор литералов CHECK), права
и справочник базовых тем. Раздел до этой работы не был покрыт ни одним
тестом — ни TrainingsView, ни /api/trainings, — поэтому здесь закрепляется
ровно то, что было решено, а не то, что «и так работает».
"""

import os
import re
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trainings import access, queries, schema  # noqa: E402


class ReasonCatalogTest(unittest.TestCase):
    """Справочник базовых тем — один на сервер, клиент и планировщик.

    До этой работы список был выписан руками в четырёх местах и разошёлся:
    модалка раздела знала 9 значений, сервер и планировщик — 11. Из-за этого
    164 записи «Тех. сбой» и 79 «Мониторинг» при открытии на редактирование
    теряли причину, а сохранение подменяло её другой.
    """

    def test_eleven_default_reasons(self):
        self.assertEqual(len(schema.DEFAULT_REASONS), 11)
        self.assertEqual(len(set(schema.DEFAULT_REASONS)), 11, 'дубли в справочнике тем')

    def test_monitoring_is_active(self):
        """«Мониторинг» — живая тема, ей пользуются: 79 записей, последняя 11.08.2026."""
        self.assertIn('Мониторинг', schema.active_default_reasons())

    def test_tech_failure_is_archived(self):
        """«Тех. сбой» виден на старых записях, но не предлагается для новых.

        Раздел «Тех. сбои» появился только в марте 2026 — до этого сбои писали
        тренингом, потому что писать их было некуда (164 записи с сентября
        2025). Переносить нельзя: все они идут в оплачиваемые часы.
        """
        self.assertIn('Тех. сбой', schema.DEFAULT_REASONS)
        self.assertIn('Тех. сбой', schema.ARCHIVED_REASONS)
        self.assertNotIn('Тех. сбой', schema.active_default_reasons())

    def test_archived_is_subset_of_default(self):
        self.assertTrue(set(schema.ARCHIVED_REASONS) <= set(schema.DEFAULT_REASONS))

    def test_call_feedback_reason_is_a_real_reason(self):
        """Разбор звонка из «Журнала оценок» пишет тренинг под этой причиной —
        она обязана оставаться в справочнике, иначе запись упадёт на CHECK."""
        self.assertIn(schema.CALL_FEEDBACK_REASON, schema.DEFAULT_REASONS)


class ReasonCheckSqlTest(unittest.TestCase):
    """Расширение CHECK на trainings.reason.

    Ключевое требование: ни одна из 1648 существующих строк не должна перестать
    проходить констрейнт. Поэтому набор литералов обязан совпасть с боевым
    один в один, а сам CHECK — только РАСШИРИТЬСЯ условием про topic_id.
    """

    def test_condition_allows_topic_rows(self):
        sql = schema.reason_check_sql()
        self.assertTrue(sql.startswith('topic_id IS NOT NULL OR reason IN ('))

    def test_condition_lists_every_default_reason(self):
        sql = schema.reason_check_sql()
        for reason in schema.DEFAULT_REASONS:
            self.assertIn("'%s'" % reason, sql, 'причина %r выпала из CHECK' % reason)

    def test_condition_lists_nothing_else(self):
        """Лишний литерал означал бы, что CHECK разрешает больше, чем справочник."""
        sql = schema.reason_check_sql()
        literals = set(re.findall(r"'([^']+)'", sql))
        self.assertEqual(literals, set(schema.DEFAULT_REASONS))

    def test_quotes_are_escaped(self):
        """Апостроф в названии темы не должен ломать DDL."""
        original = schema.DEFAULT_REASONS
        try:
            schema.DEFAULT_REASONS = ("Разбор о'кей",)
            self.assertIn("'Разбор о''кей'", schema.reason_check_sql())
        finally:
            schema.DEFAULT_REASONS = original


class SchemaOrderTest(unittest.TestCase):
    """Порядок разворота схемы: таблицы → ALTER'ы → индексы и констрейнты.

    Стоит здесь не из любви к порядку. 17.08.2026 выкат сценариев положил
    раздел «Обращения» на проде: индекс uq_crm_queues_code выполнился РАНЬШЕ,
    чем ALTER TABLE добавил столбец code. Внутри SAVEPOINT это откатило весь
    разворот схемы раздела вместе с миграциями. На пустой базе (и в любом
    тесте, который просто вызывает init) ошибка не воспроизводится — поэтому
    проверяется именно ПОРЯДОК.

    Здесь та же ловушка заряжена дважды: частичный индекс idx_trainings_topic
    и FK trainings_topic_id_fkey оба опираются на столбец topic_id, которого на
    боевой базе ещё нет.
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
        schema.init_trainings_schema(cursor)
        return cursor.statements

    def test_tables_come_first(self):
        statements = self._run()
        last_table = max(i for i, sql in enumerate(statements) if 'CREATE TABLE' in sql.upper())
        first_alter = min((i for i, sql in enumerate(statements) if sql.startswith('ALTER TABLE')),
                          default=len(statements))
        self.assertLess(last_table, first_alter,
                        'ALTER раньше CREATE TABLE — на чистой базе это падение')

    def test_columns_are_added_before_indexes_that_use_them(self):
        statements = self._run()
        added = {}
        for index, sql in enumerate(statements):
            match = re.search(r'ADD COLUMN IF NOT EXISTS (\w+)', sql)
            if match:
                added.setdefault(match.group(1), index)
        self.assertIn('topic_id', added, 'миграция столбца topic_id пропала из разворота схемы')

        for index, sql in enumerate(statements):
            if 'CREATE INDEX' not in sql.upper() and 'CREATE UNIQUE INDEX' not in sql.upper():
                continue
            for column, added_at in added.items():
                if re.search(r'\(\s*%s\b|,\s*%s\b' % (column, column), sql):
                    self.assertLess(
                        added_at, index,
                        'индекс создаётся раньше столбца %s: %s' % (column, sql[:90]))

    def test_foreign_key_comes_after_the_column(self):
        statements = self._run()
        column_at = next(i for i, sql in enumerate(statements)
                         if 'ADD COLUMN IF NOT EXISTS topic_id' in sql)
        fk_at = next(i for i, sql in enumerate(statements)
                     if 'trainings_topic_id_fkey' in sql)
        self.assertLess(column_at, fk_at, 'FK ставится раньше столбца topic_id')

    def test_check_is_dropped_before_it_is_added(self):
        """Иначе второй старт упал бы на «constraint already exists»."""
        statements = self._run()
        drop_at = next(i for i, sql in enumerate(statements)
                       if 'DROP CONSTRAINT IF EXISTS trainings_reason_check' in sql)
        add_at = next(i for i, sql in enumerate(statements)
                      if 'ADD CONSTRAINT trainings_reason_check' in sql)
        self.assertLess(drop_at, add_at)

    def test_check_is_added_after_topic_id_exists(self):
        """Условие CHECK ссылается на topic_id — столбец обязан быть раньше."""
        statements = self._run()
        column_at = next(i for i, sql in enumerate(statements)
                         if 'ADD COLUMN IF NOT EXISTS topic_id' in sql)
        add_at = next(i for i, sql in enumerate(statements)
                      if 'ADD CONSTRAINT trainings_reason_check' in sql)
        self.assertLess(column_at, add_at)

    def test_idempotent_statements_only(self):
        """Разворот гоняется при КАЖДОМ импорте database.py — второй прогон
        обязан быть безобидным."""
        for sql in self._run():
            upper = sql.upper()
            if upper.startswith('CREATE TABLE'):
                self.assertIn('IF NOT EXISTS', upper, sql[:80])
            elif upper.startswith('CREATE INDEX') or upper.startswith('CREATE UNIQUE INDEX'):
                self.assertIn('IF NOT EXISTS', upper, sql[:80])
            elif 'ADD COLUMN' in upper:
                self.assertIn('IF NOT EXISTS', upper, sql[:80])
            elif upper.startswith('ALTER TABLE') and 'ADD CONSTRAINT' in upper:
                # ADD CONSTRAINT IF NOT EXISTS в Postgres нет: идемпотентность
                # обеспечивается либо парным DROP ... IF EXISTS, либо DO-блоком.
                name = re.search(r'ADD CONSTRAINT (\w+)', sql).group(1)
                self.assertTrue(
                    any('DROP CONSTRAINT IF EXISTS %s' % name in other for other in self._run()),
                    'констрейнт %s ставится без парного DROP — второй старт упадёт' % name)


class TopicKindTest(unittest.TestCase):
    def test_only_informational_kind(self):
        """Решение владельца: пока один тип. Второй тип без запроса — это поле
        в форме, которое никто не заполняет осмысленно."""
        self.assertEqual(schema.TOPIC_KINDS, ('info',))
        self.assertEqual(schema.TOPIC_KIND_LABELS['info'], 'Информационный')

    def test_kind_check_matches_the_tuple(self):
        create = next(sql for sql in schema._STATEMENTS if 'CREATE TABLE' in sql.upper())
        for kind in schema.TOPIC_KINDS:
            self.assertIn("'%s'" % kind, create)


class AccessTest(unittest.TestCase):
    """Кто ведёт справочник тем. Решение владельца: «И СВ может создавать темы»."""

    def test_read_roles(self):
        for role in ('operator', 'trainee', 'trainer', 'sv', 'admin', 'super_admin'):
            self.assertTrue(access.can_read(role), role)
        self.assertFalse(access.can_read('unknown'))
        self.assertFalse(access.can_read(None))

    def test_supervisor_manages_topics(self):
        self.assertTrue(access.can_manage_topics('sv'))
        self.assertTrue(access.can_manage_topics('supervisor'), 'роль supervisor = sv')

    def test_trainer_reads_but_does_not_manage(self):
        """Тренер проводит тренинги по готовым темам; справочник ведёт тот, кто
        отвечает за отдел."""
        self.assertTrue(access.can_read('trainer'))
        self.assertFalse(access.can_manage_topics('trainer'))

    def test_operator_does_not_manage(self):
        self.assertFalse(access.can_manage_topics('operator'))

    def test_department_head_manages_even_with_a_low_base_role(self):
        """Назначение главой отдела заменяет базовую роль."""
        self.assertTrue(access.can_manage_topics('operator', headed_department_id=367))

    def test_department_head_is_scoped_even_with_admin_base_role(self):
        """Все главы отделов в проде имеют базовую роль admin. Если считать их
        глобальными, глава СЗоВ заведёт тему Отделу продаж."""
        self.assertFalse(access.is_unscoped('admin', headed_department_id=1))
        self.assertTrue(access.is_unscoped('admin', headed_department_id=None))
        self.assertTrue(access.is_unscoped('super_admin', headed_department_id=1))


class WritableDepartmentTest(unittest.TestCase):
    def test_scoped_user_gets_own_department_by_default(self):
        dept, error = access.writable_department_id('sv', None, 367, None)
        self.assertIsNone(error)
        self.assertEqual(dept, 367)

    def test_scoped_user_cannot_write_to_another_department(self):
        dept, error = access.writable_department_id('sv', None, 367, 1)
        self.assertIsNone(dept)
        self.assertEqual(error[1], 403)

    def test_scoped_user_cannot_create_a_company_wide_topic(self):
        """Общая тема (department_id NULL) — только у того, кто без границы:
        иначе один СВ раскатал бы тему на весь портал."""
        dept, error = access.writable_department_id('sv', None, 367, None)
        self.assertEqual(dept, 367, 'СВ обязан получить свой отдел, а не NULL')

    def test_unscoped_admin_may_create_a_company_wide_topic(self):
        dept, error = access.writable_department_id('admin', None, 1, None)
        self.assertIsNone(error)
        self.assertIsNone(dept)

    def test_head_scope_wins_over_own_department(self):
        """Глава отдела пишет в тот отдел, которым руководит, даже если сам
        числится в другом."""
        dept, error = access.writable_department_id('admin', 909, 1, None)
        self.assertIsNone(error)
        self.assertEqual(dept, 909)

    def test_user_without_any_department_is_refused(self):
        dept, error = access.writable_department_id('sv', None, None, None)
        self.assertIsNone(dept)
        self.assertEqual(error[1], 403)


class ReadableDepartmentsTest(unittest.TestCase):
    def test_unscoped_sees_everything(self):
        self.assertIsNone(access.readable_department_ids('admin', None, 1))

    def test_scoped_sees_own_department(self):
        self.assertEqual(access.readable_department_ids('sv', None, 367), frozenset({367}))

    def test_head_sees_headed_department(self):
        self.assertEqual(access.readable_department_ids('admin', 909, 1), frozenset({909}))

    def test_no_department_sees_only_shared_topics(self):
        self.assertEqual(access.readable_department_ids('sv', None, None), frozenset())


class AudiencePredicateTest(unittest.TestCase):
    """Знаменатель охвата и список «кому ещё не провели» считаются одним
    условием: два разных дали бы «осталось 16» при 18 строках в списке."""

    def test_single_predicate_used_by_both_queries(self):
        import inspect
        source = inspect.getsource(queries)
        # Условие должно встречаться в модуле ровно один раз — в определении
        # AUDIENCE_PREDICATE, — а дальше только подстановкой. Руками
        # переписанная копия и есть тот самый риск.
        self.assertEqual(source.count("u.status = 'working'"), 1,
                         'условие аудитории продублировано вместо подстановки')
        # И обе выборки обязаны его подставлять.
        for func in ('list_topics', 'topic_audience', 'department_audience_counts'):
            body = inspect.getsource(getattr(queries, func))
            self.assertIn('audience', body, 'выборка %s не использует AUDIENCE_PREDICATE' % func)

    def test_only_working_employees_count(self):
        """'bs' — без сохранения, человек не работает; 'fired' — уволен."""
        self.assertIn("u.status = 'working'", queries.AUDIENCE_PREDICATE)

    def test_admins_are_out_of_the_audience(self):
        """Раскатка адресована линейным сотрудникам, СВ и тренерам. С админами
        в знаменателе 100 % было бы недостижимо by design."""
        for role in ('admin', 'super_admin'):
            self.assertIn("'%s'" % role, queries.AUDIENCE_PREDICATE)


class TopicUpdateFieldsTest(unittest.TestCase):
    class RecordingCursor:
        def __init__(self):
            self.sql = None
            self.params = None

        def execute(self, sql, params=None):
            self.sql = ' '.join(str(sql).split())
            self.params = params

        def fetchone(self):
            return (1,)

    def test_only_whitelisted_fields_are_written(self):
        cursor = self.RecordingCursor()
        queries.update_topic(cursor, 5, {'title': 'Новая', 'created_by': 999, 'id': 7})
        self.assertIn('title = %s', cursor.sql)
        self.assertNotIn('created_by', cursor.sql)
        self.assertNotIn('id = %s,', cursor.sql)

    def test_updated_at_is_always_touched(self):
        cursor = self.RecordingCursor()
        queries.update_topic(cursor, 5, {'title': 'Новая'})
        self.assertIn('updated_at =', cursor.sql)

    def test_nothing_to_update_returns_none(self):
        cursor = self.RecordingCursor()
        self.assertIsNone(queries.update_topic(cursor, 5, {'unknown': 1}))
        self.assertIsNone(cursor.sql)


if __name__ == '__main__':
    unittest.main()


class WritePathContractTest(unittest.TestCase):
    """Контракт записи занятия: тема приходит ЛИБО причиной, ЛИБО id темы.

    Регресс, найденный ревью уже после выката: в required_fields у POST
    /api/trainings оставался 'reason', и проверка стояла ДО разбора темы —
    поэтому весь корпоративный путь записи отвечал 400 «Missing required
    fields», и по корпоративной теме нельзя было записать ни одного занятия.
    Клиент отправляет topic_id, а причину сервер берёт у темы сам.
    """

    @staticmethod
    def _source():
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'bot_schedule2.py'), encoding='utf-8') as handle:
            return handle.read()

    def _add_training_body(self):
        source = self._source()
        start = source.index('def add_training():')
        end = source.index('def update_training(', start)
        return source[start:end]

    def test_reason_is_not_a_required_field(self):
        body = self._add_training_body()
        head = body[:body.index('raw_operator_ids')]
        self.assertIn("required_fields = ['date', 'start_time', 'end_time']", head)
        self.assertNotIn("'reason'", head.split('required_fields =')[1].split(']')[0] + ']')

    def test_either_reason_or_topic_is_demanded(self):
        body = self._add_training_body()
        self.assertIn("if not data.get('topic_id') and not data.get('reason')", body)

    def test_topic_is_resolved_before_the_reason_allowlist(self):
        """Иначе название корпоративной темы проверялось бы по списку базовых."""
        body = self._add_training_body()
        self.assertLess(body.index('_resolve_training_topic'),
                        body.index('_training_reason_catalog'))


class GroupScopeGuardTest(unittest.TestCase):
    """В ветке ?group_id= у каждой роли должна быть своя область видимости.

    Регресс, найденный ревью: гейт роли расширили тренером и стажёром, а внутри
    блока group_id отсекался только 'operator'. Тренер и стажёр проваливались
    сквозь все проверки и читали любую группу любого отдела.
    """

    @staticmethod
    def _handlers():
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'bot_schedule2.py'), encoding='utf-8') as handle:
            source = handle.read()
        # Границу функции ищем по следующему def на нулевом отступе: 'ORDER BY'
        # встречается уже внутри LATERAL-подзапроса и обрезало бы блок раньше
        # проверяемого места.
        out = []
        for name, after in (('def get_trainings():', 'def _training_reason_catalog'),
                            ('def get_training_rejections():', 'def add_training_rejection')):
            start = source.index(name)
            end = source.index(after, start)
            out.append((name, source[start:end]))
        return out

    def test_no_role_falls_through_the_group_branch(self):
        for name, body in self._handlers():
            block = body[body.index('if group_id is not None:'):]
            block = block[:block.index('where_clauses.append')]
            self.assertIn("elif role == 'trainer':", block, name)
            self.assertIn('else:', block, name)
            self.assertIn('Unsupported target role', block, name)
            self.assertNotIn("elif role == 'operator':", block,
                             '%s: operator-only отсечение снова пропускает тренера и стажёра' % name)

    def test_trainer_is_bounded_by_own_department(self):
        for name, body in self._handlers():
            block = body[body.index('if group_id is not None:'):]
            block = block[:block.index('where_clauses.append')]
            self.assertIn('get_user_department_id', block, name)
            self.assertIn("not your department's group", block, name)


class ArchivedTopicEditTest(unittest.TestCase):
    """Архивная тема запрещена для НОВОГО занятия, но не для правки старого.

    Архив — единственный способ убрать тему с историей (удаление сервер
    запрещает), значит «занятие по архивной теме» — нормальное конечное
    состояние, и опечатку во времени в нём надо уметь починить.
    """

    @staticmethod
    def _source():
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'bot_schedule2.py'), encoding='utf-8') as handle:
            return handle.read()

    def test_resolver_takes_the_records_current_topic(self):
        source = self._source()
        start = source.index('def _resolve_training_topic(')
        end = source.index('def _training_time_to_minutes', start)
        body = source[start:end]
        self.assertIn('current_topic_id=None', body)
        self.assertIn('keeping_own_topic', body)

    def test_put_passes_the_current_topic(self):
        source = self._source()
        start = source.index('def update_training(')
        end = source.index('def delete_training(', start)
        body = source[start:end]
        self.assertIn('current_topic_id=current_topic_id', body)

    def test_post_does_not_pass_it(self):
        """У новой записи текущей темы нет — архивную обязаны отклонить."""
        source = self._source()
        start = source.index('def add_training():')
        end = source.index('def update_training(', start)
        body = source[start:end]
        self.assertIn("_resolve_training_topic(requester, requester_id, data.get('topic_id'))", body)


class SupervisorTargetRolesTest(unittest.TestCase):
    """Кому СВ вправе записать занятие.

    Аудитория корпоративной темы (решение владельца) — все активные сотрудники
    отдела; со старым набором ('operator',) супервайзер не смог бы закрыть охват
    в принципе, а «Отметить всех в списке» отдавало частичный успех с
    непонятными отказами. Граница отдела при этом остаётся.
    """

    @staticmethod
    def _source():
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'bot_schedule2.py'), encoding='utf-8') as handle:
            return handle.read()

    def test_constant_covers_the_whole_audience(self):
        source = self._source()
        line = next(item for item in source.splitlines()
                    if item.startswith('TRAINING_TARGET_ROLES ='))
        for role in ('operator', 'trainee', 'sv', 'trainer'):
            self.assertIn("'%s'" % role, line)

    def test_all_four_training_handlers_use_it(self):
        """POST (одиночный и батч), PUT и DELETE — один набор ролей на всех:
        иначе занятие можно было бы создать, но нельзя удалить."""
        source = self._source()
        start = source.index('def add_training():')
        end = source.index('def get_training_rejections():', start)
        body = source[start:end]
        self.assertEqual(body.count('supervisor_target_roles=TRAINING_TARGET_ROLES'), 4)
        self.assertNotIn("supervisor_target_roles=('operator',)", body)
class GroupBranchOutcomeTest(unittest.TestCase):
    """Ветка ?group_id= прогоняется как код — по одной роли за раз.

    Текстовых проверок выше не хватило: явный else, закрывший операторам чужие
    группы, забрал заодно и админа портала (он не глава отдела, не СВ и не
    тренер), и «Учёт часов» с выбранной группой начал отвечать ему 400
    «Unsupported target role». Здесь блок вынимается из монолита как есть и
    исполняется с заглушками — так регресс виден по ответу, а не по тексту.
    """

    @staticmethod
    def _blocks():
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'bot_schedule2.py'), encoding='utf-8') as handle:
            source = handle.read()
        out = []
        for name, after in (('def get_trainings():', 'def _training_reason_catalog'),
                            ('def get_training_rejections():', 'def add_training_rejection')):
            start = source.index(name)
            body = source[start:source.index(after, start)]
            head = body.index('if group_id is not None:')
            block = body[head:body.index('where_clauses.append', head)]
            # index указывает на сам `if`, отступ первой строки съеден: возвращаем
            # его, иначе dedent посчитает общий префикс равным нулю.
            block = textwrap.dedent(' ' * 8 + block).rstrip()
            eol = chr(10)
            out.append((name, eol.join((
                'def probe():', textwrap.indent(block, '    '), '    return None'))))
        return out

    @staticmethod
    def _run(probe_src, *, role, headed_dept_id=None, group_dept_id=5,
             requester_dept_id=None, sv_period_access=False):
        class Db:
            def get_group(self, group_id):
                return {'id': group_id, 'department_id': group_dept_id}

            def get_user_department_id(self, _requester_id):
                return requester_dept_id

            def supervisor_has_group_access_for_period(self, *_args, **_kwargs):
                return sv_period_access

        namespace = {
            'jsonify': lambda payload: payload,
            'db': Db(),
            'role': role,
            'headed_dept_id': headed_dept_id,
            'requester_id': 1,
            'group_id': 38,
            'period_start': None,
            'period_end': None,
            'where_clauses': [],
            'params': [],
            # Семантика ролевых предикатов монолита, без импорта монолита.
            '_is_admin_role': lambda value: value in ('admin', 'super_admin'),
            '_is_supervisor_role': lambda value: value == 'sv',
            '_is_global_admin_requester': lambda value, _rid=None: (
                value == 'super_admin' or (value == 'admin' and headed_dept_id is None)),
        }
        exec(compile(probe_src, '<group-branch>', 'exec'), namespace)  # noqa: S102
        return namespace['probe']()

    def test_portal_admin_reads_any_group(self):
        """Тот самый отчёт: month + id + group_id от админа отвечал 400."""
        for name, probe in self._blocks():
            self.assertIsNone(
                self._run(probe, role='admin', headed_dept_id=None, group_dept_id=9),
                '%s: админ портала снова получает отказ на выбранную группу' % name)

    def test_super_admin_ignores_the_department_border(self):
        for name, probe in self._blocks():
            self.assertIsNone(
                self._run(probe, role='super_admin', headed_dept_id=5, group_dept_id=9),
                '%s: супер-админ ограничен отделом' % name)

    def test_department_head_is_bounded(self):
        for name, probe in self._blocks():
            self.assertIsNone(
                self._run(probe, role='admin', headed_dept_id=5, group_dept_id=5), name)
            payload, status = self._run(
                probe, role='admin', headed_dept_id=5, group_dept_id=9)
            self.assertEqual(403, status, name)
            self.assertIn("not your department's group", payload['error'], name)

    def test_supervisor_sees_own_department_or_led_group(self):
        for name, probe in self._blocks():
            self.assertIsNone(
                self._run(probe, role='sv', requester_dept_id=5, group_dept_id=5), name)
            self.assertIsNone(
                self._run(probe, role='sv', requester_dept_id=5, group_dept_id=9,
                          sv_period_access=True), name)
            payload, status = self._run(
                probe, role='sv', requester_dept_id=5, group_dept_id=9)
            self.assertEqual(403, status, name)
            self.assertIn('not your group', payload['error'], name)

    def test_trainer_sees_own_department_only(self):
        for name, probe in self._blocks():
            self.assertIsNone(
                self._run(probe, role='trainer', requester_dept_id=5, group_dept_id=5), name)
            payload, status = self._run(
                probe, role='trainer', requester_dept_id=5, group_dept_id=9)
            self.assertEqual(403, status, name)
            self.assertIn("not your department's group", payload['error'], name)
            _payload, status = self._run(
                probe, role='trainer', requester_dept_id=None, group_dept_id=5)
            self.assertEqual(403, status,
                             '%s: тренер без отдела не должен читать группы' % name)

    def test_rank_and_file_still_cut_off(self):
        for name, probe in self._blocks():
            for role in ('operator', 'trainee', 'newrole'):
                payload, status = self._run(probe, role=role, requester_dept_id=5)
                self.assertEqual(400, status, '%s/%s' % (name, role))
                self.assertEqual('Unsupported target role', payload['error'],
                                 '%s/%s' % (name, role))
