# -*- coding: utf-8 -*-
"""Контрольные точки по сотруднику (задача #86).

Два вида проверок:

  * настоящие юнит-тесты на `trainings/checkpoints.py` — модуль без Flask и без
    базы именно ради этого;
  * сторожа по исходникам монолита и фронта — там, где логика вплетена в ручку
    и вызвать её отдельно нельзя (тот же приём, что у остальных тестов набора:
    `bot_schedule2.py` нельзя импортировать, он поднимает пул к боевой БД).

Самое важное здесь — `OperatorVisibilityTests`. Разделение видимости это не
украшение, а требование постановки: сотрудник не должен видеть вид контроля
(в том числе «испытательный срок»), причину постановки и внутренний
комментарий супервайзера. Тест перечисляет запрещённые ключи ЯВНО, поэтому
новое служебное поле, случайно попавшее в ответ сотруднику, уронит набор.
"""

import unittest
from datetime import date, timedelta
from pathlib import Path

from tests import prod_db, source_cache

from notifications import sources as notif_sources

from trainings import checkpoints as cp
from trainings.schema import CHECKPOINT_KINDS, CHECKPOINT_KIND_LABELS


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DATABASE_PATH = ROOT / "database.py"
SCHEMA_PATH = ROOT / "trainings" / "schema.py"
SOURCES_PATH = ROOT / "notifications" / "sources.py"
MAIN_JSX_PATH = ROOT / "src" / "call_evaluation" / "main.jsx"
TRAININGS_VIEW_PATH = ROOT / "src" / "components" / "trainings" / "TrainingsView.jsx"
CHECKPOINTS_TAB_PATH = ROOT / "src" / "components" / "trainings" / "CheckpointsTab.jsx"
BELL_PATH = ROOT / "src" / "components" / "notifications" / "NotificationsBell.jsx"


def _read(path):
    return path.read_text(encoding="utf-8-sig")


TODAY = date(2026, 8, 27)


def _valid_input(**overrides):
    payload = {
        'enabled': True,
        'kind': 'recheck',
        'reason': 'Третий разбор подряд с ошибкой в приветствии',
        'due_date': '2026-09-03',
        'focus': 'Приветствие по стандарту, работа с возражением',
        'internal_comment': 'Если не выправится — говорим с руководителем',
    }
    payload.update(overrides)
    return payload


class ParseCheckpointInputTests(unittest.TestCase):
    """Разбор блока «Контрольная точка» из окна «Дать ОС»."""

    def test_absent_block_is_not_an_error(self):
        """Старый клиент и любой другой код про точки не знают — и не должны."""
        self.assertIsNone(cp.parse_checkpoint_input(None, today=TODAY))
        self.assertIsNone(cp.parse_checkpoint_input({}, today=TODAY))
        self.assertIsNone(cp.parse_checkpoint_input('нет', today=TODAY))

    def test_disabled_toggle_requires_nothing(self):
        """Прямой критерий приёмки: выключенный блок не мешает сохранить ОС.

        Ни одного обязательного поля нет — при этом всё пусто.
        """
        self.assertIsNone(cp.parse_checkpoint_input(
            {'enabled': False, 'kind': '', 'reason': '', 'due_date': '', 'focus': ''},
            today=TODAY,
        ))

    def test_enabled_block_returns_normalized_fields(self):
        parsed = cp.parse_checkpoint_input(_valid_input(), today=TODAY)
        self.assertEqual(parsed['kind'], 'recheck')
        self.assertEqual(parsed['due_date'], date(2026, 9, 3))
        self.assertEqual(parsed['focus'], 'Приветствие по стандарту, работа с возражением')
        # Про уведомление сотрудника не сказали — предупреждаем: смысл точки в
        # том, чтобы человек знал, что исправить к проверке.
        self.assertTrue(parsed['notify_operator'])

    def test_notify_operator_can_be_switched_off(self):
        parsed = cp.parse_checkpoint_input(_valid_input(notify_operator=False), today=TODAY)
        self.assertFalse(parsed['notify_operator'])

    def test_required_fields_are_checked_only_when_enabled(self):
        for field, message_part in (
            ('reason', 'причину'),
            ('focus', 'проверить'),
        ):
            with self.subTest(field=field):
                with self.assertRaises(cp.CheckpointError) as ctx:
                    cp.parse_checkpoint_input(_valid_input(**{field: '   '}), today=TODAY)
                self.assertIn(message_part, str(ctx.exception).lower())

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(cp.CheckpointError):
            cp.parse_checkpoint_input(_valid_input(kind='fired'), today=TODAY)
        for kind in CHECKPOINT_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(
                    cp.parse_checkpoint_input(_valid_input(kind=kind), today=TODAY)['kind'], kind)

    def test_past_date_is_rejected_but_today_is_allowed(self):
        with self.assertRaises(cp.CheckpointError):
            cp.parse_checkpoint_input(_valid_input(due_date='2026-08-26'), today=TODAY)
        parsed = cp.parse_checkpoint_input(_valid_input(due_date='2026-08-27'), today=TODAY)
        self.assertEqual(parsed['due_date'], TODAY)

    def test_far_future_is_rejected_as_a_typo_in_the_year(self):
        far = (TODAY + timedelta(days=cp.MAX_DAYS_AHEAD + 1)).strftime('%Y-%m-%d')
        with self.assertRaises(cp.CheckpointError):
            cp.parse_checkpoint_input(_valid_input(due_date=far), today=TODAY)

    def test_broken_date_reports_the_format(self):
        with self.assertRaises(cp.CheckpointError) as ctx:
            cp.parse_checkpoint_input(_valid_input(due_date='03.09.2026'), today=TODAY)
        self.assertIn('ГГГГ-ММ-ДД', str(ctx.exception))

    def test_too_long_text_is_rejected(self):
        long_text = 'я' * (cp.MAX_TEXT_LENGTH + 1)
        for field in ('reason', 'focus', 'internal_comment'):
            with self.subTest(field=field):
                with self.assertRaises(cp.CheckpointError):
                    cp.parse_checkpoint_input(_valid_input(**{field: long_text}), today=TODAY)

    def test_error_messages_are_russian(self):
        """Весь текст интерфейса по-русски — включая ошибки."""
        with self.assertRaises(cp.CheckpointError) as ctx:
            cp.parse_checkpoint_input(_valid_input(reason=''), today=TODAY)
        self.assertTrue(any('а' <= ch <= 'я' for ch in str(ctx.exception).lower()))


def _stored_item(**overrides):
    item = {
        'id': 12,
        'operator_id': 340,
        'supervisor_id': 7,
        'feedback_id': 88,
        'call_id': 4821,
        'kind': 'probation',
        'reason': 'Служебная причина, которую сотруднику знать не положено',
        'due_date': '2026-09-03',
        'focus': 'Приветствие по стандарту',
        'internal_comment': 'Внутренняя пометка супервайзера',
        'notify_operator': True,
        'status': 'open',
        'resolved_at': None,
        'resolved_by': None,
        'resolution_comment': '',
        'created_at': '2026-08-27 10:00',
        'updated_at': '2026-08-27 10:00',
        'operator_name': 'Ким Оксана',
        'operator_status': 'working',
        'supervisor_name': 'Ядигаров Руслан',
        'resolved_by_name': '',
    }
    item.update(overrides)
    return item


class OperatorVisibilityTests(unittest.TestCase):
    """Что сотрудник видит, а что нет. Прямое требование постановки задачи."""

    # Поля, которых в ответе сотруднику не должно быть НИКОГДА.
    FORBIDDEN = (
        'kind', 'kind_label',        # в том числе «Испытательный срок»
        'reason',                    # причина постановки на контроль
        'internal_comment',          # пометка супервайзера
        'supervisor_id', 'supervisor_name',
        'resolution_comment', 'resolved_by_name',
        'notify_operator',
    )

    def test_operator_payload_hides_service_fields(self):
        payload = cp.payload_for_operator(_stored_item(), today=TODAY)
        for key in self.FORBIDDEN:
            with self.subTest(key=key):
                self.assertNotIn(key, payload)

    def test_operator_payload_keeps_what_he_needs(self):
        payload = cp.payload_for_operator(_stored_item(), today=TODAY)
        self.assertEqual(payload['due_date'], '2026-09-03')
        self.assertEqual(payload['focus'], 'Приветствие по стандарту')
        self.assertEqual(payload['days_left'], 7)

    def test_no_service_text_leaks_into_operator_payload(self):
        """Даже если поле переименуют — служебный ТЕКСТ не должен доехать."""
        payload = cp.payload_for_operator(_stored_item(), today=TODAY)
        blob = repr(payload)
        self.assertNotIn('Внутренняя пометка', blob)
        self.assertNotIn('Служебная причина', blob)
        self.assertNotIn(CHECKPOINT_KIND_LABELS['probation'], blob)

    def test_operator_sees_nothing_when_notification_is_off(self):
        self.assertIsNone(
            cp.payload_for_operator(_stored_item(notify_operator=False), today=TODAY))

    def test_manager_payload_keeps_everything(self):
        payload = cp.payload_for_manager(_stored_item(), today=TODAY)
        self.assertEqual(payload['kind_label'], CHECKPOINT_KIND_LABELS['probation'])
        self.assertEqual(payload['internal_comment'], 'Внутренняя пометка супервайзера')
        self.assertEqual(payload['reason'], 'Служебная причина, которую сотруднику знать не положено')


class DueDateArithmeticTests(unittest.TestCase):
    def test_days_left(self):
        self.assertEqual(cp.days_left('2026-08-27', today=TODAY), 0)
        self.assertEqual(cp.days_left('2026-08-30', today=TODAY), 3)
        self.assertEqual(cp.days_left('2026-08-25', today=TODAY), -2)
        self.assertIsNone(cp.days_left('', today=TODAY))
        self.assertIsNone(cp.days_left('чепуха', today=TODAY))

    def test_overdue_only_applies_to_open_points(self):
        self.assertTrue(cp.is_overdue(_stored_item(due_date='2026-08-25'), today=TODAY))
        self.assertFalse(cp.is_overdue(_stored_item(due_date='2026-08-27'), today=TODAY))
        # Закрытая точка не «просрочена»: её уже закрыли, догонять нечего.
        self.assertFalse(cp.is_overdue(
            _stored_item(due_date='2026-08-01', status='done'), today=TODAY))


class ScopeClauseTests(unittest.TestCase):
    """Границы видимости. Условие одно на список и на колокол — см. scope_clause."""

    def test_global_admin_has_no_boundary(self):
        params = {}
        self.assertEqual(cp.scope_clause({'scope': 'all'}, params), '')
        self.assertEqual(params, {})

    def test_department_head_is_limited_to_his_departments(self):
        params = {}
        clause = cp.scope_clause({'scope': 'departments', 'department_ids': [4, 9]}, params)
        self.assertIn('op.department_id = ANY(%(cp_departments)s)', clause)
        self.assertEqual(params['cp_departments'], [4, 9])

    def test_supervisor_without_department_sees_his_operators(self):
        params = {}
        clause = cp.scope_clause({'scope': 'supervisor', 'supervisor_id': 7}, params)
        self.assertIn('op.supervisor_id = %(cp_supervisor)s', clause)
        self.assertEqual(params['cp_supervisor'], 7)

    def test_unknown_scope_shows_nothing(self):
        """Неизвестная граница — это «никого», а не «все»."""
        for descriptor in ({}, None, {'scope': 'что-то новое'}, {'scope': 'departments'}):
            with self.subTest(descriptor=descriptor):
                self.assertIn('FALSE', cp.scope_clause(descriptor, {}))

    def test_ids_are_bound_and_not_pasted_into_sql(self):
        params = {}
        clause = cp.scope_clause({'scope': 'self', 'user_id': 340}, params)
        self.assertNotIn('340', clause)
        self.assertEqual(params['cp_self'], 340)

    def test_clause_is_appendable_to_a_where(self):
        """Условие начинается с ' AND ' — вызывающий дописывает его как есть."""
        clause = cp.scope_clause({'scope': 'supervisor', 'supervisor_id': 7}, {})
        self.assertTrue(clause.startswith(' AND '))


class SchemaTests(unittest.TestCase):
    def test_table_is_created_idempotently(self):
        source = _read(SCHEMA_PATH)
        self.assertIn('CREATE TABLE IF NOT EXISTS operator_checkpoints', source)

    def test_only_one_OPEN_checkpoint_per_feedback(self):
        """История проведённых проверок не должна стираться правкой старой ОС."""
        source = _read(SCHEMA_PATH)
        self.assertIn('uq_operator_checkpoints_feedback', source)
        self.assertIn("WHERE feedback_id IS NOT NULL AND status = 'open'", source)

    def test_upsert_touches_only_open_rows(self):
        source = _read(ROOT / 'trainings' / 'checkpoints.py')
        self.assertIn("WHERE feedback_id = %s AND status = 'open' LIMIT 1", source)
        self.assertIn(
            "DELETE FROM operator_checkpoints WHERE feedback_id = %s AND status = 'open'",
            source,
        )

    def test_kind_and_status_are_constrained(self):
        source = _read(SCHEMA_PATH)
        self.assertIn("CHECK (kind IN ('quality', 'probation', 'recheck'))", source)
        self.assertIn("CHECK (status IN ('open', 'done', 'cancelled'))", source)


class BellTriggerTests(unittest.TestCase):
    """Колокол: тычок при постановке и при закрытии точки."""

    def test_two_triggers_because_WHEN_cannot_reference_OLD_on_insert(self):
        """Один триггер на INSERT OR UPDATE с WHEN(OLD…) Postgres не создаст."""
        source = _read(DATABASE_PATH)
        self.assertIn(
            "('trg_bell_checkpoints_insert', 'operator_checkpoints', 'AFTER INSERT', '')",
            source,
        )
        self.assertIn("'trg_bell_checkpoints',", source)
        self.assertIn("'AFTER UPDATE OF status, due_date, operator_id, supervisor_id, '", source)

    def test_operator_is_woken_only_when_he_is_told(self):
        source = _read(DATABASE_PATH)
        self.assertIn("IF NEW.notify_operator THEN", source)
        self.assertIn("targets := targets || ARRAY[NEW.operator_id];", source)

    def test_source_is_registered(self):
        source = _read(SOURCES_PATH)
        self.assertIn("'checkpoints'", source)
        self.assertIn("'checkpoints': checkpoints,", source)
        self.assertIn('def checkpoints(cursor, viewer, limit):', source)

    def test_manager_sees_the_point_only_when_the_date_has_come(self):
        """Иначе точка на месяц вперёд висела бы в колоколе все тридцать дней."""
        source = _read(SOURCES_PATH)
        self.assertIn("AND c.due_date <= %(today)s", source)

    def test_operator_branch_selects_no_service_columns(self):
        """Ветка сотрудника не должна ВЫБИРАТЬ kind, reason и комментарий."""
        source = _read(SOURCES_PATH)
        start = source.index('def checkpoints(cursor, viewer, limit):')
        end = source.index('_HANDLERS = {', start)
        body = source[start:end]
        operator_query_start = body.index('SELECT c.id, c.due_date, c.focus')
        operator_query_end = body.index('LIMIT %(limit)s', operator_query_start)
        operator_query = body[operator_query_start:operator_query_end]
        for forbidden in ('c.kind', 'c.reason', 'c.internal_comment'):
            with self.subTest(column=forbidden):
                self.assertNotIn(forbidden, operator_query)

    def test_bell_ui_knows_the_source(self):
        source = _read(BELL_PATH)
        self.assertIn('checkpoints: {', source)
        self.assertIn("label: 'Контроль'", source)

    def test_checkpoints_cannot_be_dismissed_by_looking_at_the_bell(self):
        """Точка снимается ДЕЙСТВИЕМ (провели проверку), как ознакомление."""
        source = _read(SOURCES_PATH)
        mark_seen = source[source.index('def mark_seen('):]
        self.assertNotIn("== 'checkpoints'", mark_seen)


class FeedbackRouteTests(unittest.TestCase):
    def test_checkpoint_is_parsed_before_any_write(self):
        """Ошибка в блоке не должна оставить сохранённую ОС без точки.

        Разбор обязан стоять ДО `with db._get_cursor()` в ручке сохранения ОС.
        """
        source = _read(BOT_PATH)
        module = source_cache.parse(source)
        func = next(
            node for node in module.body
            if getattr(node, 'name', None) == 'upsert_call_feedback'
        )
        body = source.splitlines()[func.lineno - 1:func.end_lineno]
        text = '\n'.join(body)
        parse_at = text.index('parse_checkpoint_input')
        cursor_at = text.index('with db._get_cursor()')
        self.assertLess(parse_at, cursor_at)

    def test_checkpoint_is_written_in_the_same_transaction_as_feedback(self):
        source = _read(BOT_PATH)
        module = source_cache.parse(source)
        func = next(
            node for node in module.body
            if getattr(node, 'name', None) == 'upsert_call_feedback'
        )
        text = '\n'.join(source.splitlines()[func.lineno - 1:func.end_lineno])
        cursor_at = text.index('with db._get_cursor()')
        apply_at = text.index('_apply_feedback_checkpoint(')
        self.assertLess(cursor_at, apply_at)

    def test_batch_feedback_creates_one_checkpoint_for_the_whole_batch(self):
        source = _read(BOT_PATH)
        module = source_cache.parse(source)
        func = next(
            node for node in module.body
            if getattr(node, 'name', None) == 'create_call_feedback_batch'
        )
        text = '\n'.join(source.splitlines()[func.lineno - 1:func.end_lineno])
        self.assertIn('feedback_id=created_ids[0]', text)

    def test_full_card_goes_only_to_those_who_may_manage_control(self):
        """Умолчание безопасное: не «роль ≠ оператор», а «контроль ему открыт».

        Разница не теоретическая: /api/call_evaluations пускает к чужим оценкам
        ещё и тренера (_authorize_operator_scope), а стажёр — это роль
        'trainee'. Проверка на 'operator' обоих бы не поймала, и они увидели бы
        «испытательный срок» и внутренний комментарий супервайзера.
        """
        source = _read(BOT_PATH)
        module = source_cache.parse(source)
        func = next(
            node for node in module.body
            if getattr(node, 'name', None) == '_attach_checkpoints_to_evaluations'
        )
        text = chr(10).join(source.splitlines()[func.lineno - 1:func.end_lineno])
        self.assertIn('payload_for_operator', text)
        self.assertIn('_can_manage_checkpoints(requester_id, requester)', text)
        self.assertNotIn("== 'operator'", text)

    def test_journal_failure_does_not_break_the_journal(self):
        source = _read(BOT_PATH)
        module = source_cache.parse(source)
        func = next(
            node for node in module.body
            if getattr(node, 'name', None) == '_attach_checkpoints_to_evaluations'
        )
        text = '\n'.join(source.splitlines()[func.lineno - 1:func.end_lineno])
        self.assertIn('except Exception:', text)

    def test_list_and_update_routes_exist(self):
        source = _read(BOT_PATH)
        self.assertIn("@app.route('/api/training_checkpoints', methods=['GET'])", source)
        self.assertIn(
            "@app.route('/api/training_checkpoints/<int:checkpoint_id>', methods=['POST'])",
            source,
        )

    def test_routes_are_closed_to_operators_and_trainers(self):
        source = _read(BOT_PATH)
        module = source_cache.parse(source)
        func = next(
            node for node in module.body
            if getattr(node, 'name', None) == '_can_manage_checkpoints'
        )
        text = '\n'.join(source.splitlines()[func.lineno - 1:func.end_lineno])
        self.assertIn('_is_admin_role', text)
        self.assertIn('_is_supervisor_role', text)
        self.assertIn('_headed_department_id', text)
        self.assertNotIn("'trainer'", text)


class JournalUiTests(unittest.TestCase):
    def test_block_is_collapsed_until_the_toggle_is_on(self):
        source = _read(MAIN_JSX_PATH)
        self.assertIn('const CheckpointBlock = ', source)
        self.assertIn('enabled: false,', source)

    def test_validation_is_silent_while_the_toggle_is_off(self):
        source = _read(MAIN_JSX_PATH)
        start = source.index('const checkpointDraftError = ')
        end = source.index('const checkpointPayload = ', start)
        body = source[start:end]
        self.assertIn("if (!draft || !draft.enabled) return '';", body)

    def test_the_block_tells_what_the_employee_will_see(self):
        """Разделение видимости должно быть свойством экрана, а не документа.

        Текст ушёл из постоянной разметки под «i» (окно ОС разгружали по просьбе
        владельца), но САМА гарантия обязана остаться названной словами —
        иначе супервайзер не знает, что можно писать в служебные поля, и пишет
        обтекаемо. Поэтому проверяем не «есть строка на экране», а «строка
        доступна пользователю через подсказку».
        """
        source = _read(MAIN_JSX_PATH)
        self.assertIn('const CeInfo = ', source)
        for promise in (
            'Вид контроля, причину постановки и внутренний комментарий — не увидит.',
            'Сотруднику он не показывается нигде',
        ):
            with self.subTest(promise=promise):
                self.assertIn(promise, source)
                # Обещание живёт в подсказке, а не в случайном месте файла.
                before = source[:source.index(promise)]
                self.assertIn('<CeInfo', before[-400:])

    def test_permanent_hint_paragraphs_are_gone(self):
        """Разгрузка окна: постоянных абзацев-пояснений в разметке быть не должно."""
        source = _read(MAIN_JSX_PATH)
        for noise in (
            'При сохранении будет автоматически создан/обновлен тренинг',
            'При сохранении будет создан один общий тренинг',
            'Напомним в разделе «Тренинги»',
            'ce-cp-notify-sub',
        ):
            with self.subTest(noise=noise):
                self.assertNotIn(noise, source)

    def test_batch_window_has_the_same_block(self):
        source = _read(MAIN_JSX_PATH)
        self.assertEqual(source.count('<CheckpointBlock'), 2)

    def test_evaluation_carries_its_checkpoint(self):
        source = _read(MAIN_JSX_PATH)
        self.assertIn('checkpoint: ev.checkpoint || null,', source)


class OperatorBannerTests(unittest.TestCase):
    """Плашка сотрудника в его собственных «Оценках» (src/App.jsx).

    Уведомление в колоколе ведёт именно сюда, и без плашки человек попадал бы
    на страницу, где о назначенной проверке не сказано ни слова.
    """

    APP_PATH = ROOT / "src" / "App.jsx"

    def _banner(self):
        source = _read(self.APP_PATH)
        start = source.index('Назначенная повторная проверка (задача #86)')
        end = source.index('{/* KPI cards */}', start)
        return source[start:end]

    def test_banner_exists(self):
        self.assertIn('Повторная проверка качества', self._banner())

    def test_banner_shows_only_date_and_focus(self):
        banner = self._banner()
        self.assertIn('checkpoint.due_date', banner)
        self.assertIn('checkpoint.focus', banner)
        for forbidden in ('checkpoint.kind', 'checkpoint.reason', 'checkpoint.internal_comment'):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, banner)

    def test_banner_hides_when_the_check_is_done(self):
        self.assertIn("item.status === 'open'", self._banner())


class TrainingsTabTests(unittest.TestCase):
    def test_tab_is_registered(self):
        source = _read(TRAININGS_VIEW_PATH)
        self.assertIn('TAB_CHECKPOINTS', source)
        self.assertIn("label: 'Контроль'", source)
        self.assertIn('<CheckpointsTab', source)

    def test_month_picker_is_hidden_on_the_tab(self):
        """Контроль — очередь дел, а не отчёт за месяц."""
        source = _read(TRAININGS_VIEW_PATH)
        self.assertIn('{activeTab !== TAB_CHECKPOINTS && <MonthPicker', source)

    def test_tab_disappears_when_the_server_says_no(self):
        source = _read(TRAININGS_VIEW_PATH)
        self.assertIn('setCheckpointsAllowed(false)', source)
        self.assertIn('checkpointsAllowed', source)

    def test_tab_falls_back_when_the_saved_tab_is_gone(self):
        source = _read(TRAININGS_VIEW_PATH)
        self.assertIn('(tab === TAB_CHECKPOINTS && !checkpointsAllowed) ? TAB_TOPICS : tab', source)

    def test_days_are_recomputed_on_the_client(self):
        """Раздел держат открытым сутками — серверное days_left устареет в полночь."""
        source = _read(CHECKPOINTS_TAB_PATH)
        self.assertIn('checkpointDaysLeft', source)


class RealPostgresTests(unittest.TestCase):
    """Настоящий SQL на боевом Postgres — READ ONLY, по синтетическим данным.

    Текстовых проверок мало: условие видимости можно написать синтаксически
    верно и всё равно пропустить чужой отдел (OR вместо AND читается
    одинаково). Здесь выполняется РОВНО тот SQL, который уходит в прод, а
    таблицы подменены CTE — боевые данные не читаются и не меняются.

    Отдельно проверяется DDL. Читающая роль не создаст таблицу, но Postgres
    РАЗБИРАЕТ инструкцию раньше, чем проверяет права: `syntax error` — это
    настоящая ошибка, `read-only transaction` / `permission denied` — значит
    инструкция разобрана. Цена пропущенной опечатки высокая: разворот схемы
    раздела «Тренинги» идёт под общим SAVEPOINT, и падение одной инструкции
    молча откатывает ВСЮ схему раздела вместе с корпоративными темами.
    """

    class Recorder:
        """Курсор-заглушка: запоминает SQL и параметры, ничего не выполняя."""

        def __init__(self):
            self.sql = None
            self.params = None

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params or {}

        def fetchall(self):
            return []

        def fetchone(self):
            return None

    # Подменяем и точки, и людей: тест про ГРАНИЦУ видимости и порядок, и
    # боевой состав отделов ему не нужен (на нём он был бы плавающим).
    #
    # Даты подставляются в текст, а не параметрами: у fetch_by_feedback_ids
    # параметры ПОЗИЦИОННЫЕ, а у list_for_scope — именованные, и psycopg2 не
    # даст смешать их в одном запросе. Значения генерирует сам тест, так что
    # подстановка безопасна.
    @staticmethod
    def _fixture():
        today = date.today()
        day = lambda shift: (today + timedelta(days=shift)).isoformat()  # noqa: E731
        stamp = today.isoformat() + ' 10:00:00'
        return """
        WITH operator_checkpoints(
            id, operator_id, supervisor_id, feedback_id, call_id, kind, reason,
            due_date, focus, internal_comment, notify_operator, status,
            resolved_at, resolved_by, resolution_comment, created_at, updated_at
        ) AS (
            VALUES
              (1, 101, 7, 88, 4821, 'probation', 'причина', DATE '{d3}', 'что проверить',
               'служебное', TRUE, 'open', NULL::timestamp, NULL::int, NULL::text,
               TIMESTAMP '{ts}', TIMESTAMP '{ts}'),
              (2, 102, 7, 89, 4822, 'quality', 'причина', DATE '{d10}', 'что проверить',
               NULL, TRUE, 'open', NULL::timestamp, NULL::int, NULL::text,
               TIMESTAMP '{ts}', TIMESTAMP '{ts}'),
              (3, 103, 9, 90, 4823, 'recheck', 'причина', DATE '{d5}', 'что проверить',
               NULL, TRUE, 'open', NULL::timestamp, NULL::int, NULL::text,
               TIMESTAMP '{ts}', TIMESTAMP '{ts}'),
              (4, 101, 7, 91, 4824, 'quality', 'причина', DATE '{dm5}', 'что проверить',
               NULL, TRUE, 'done', TIMESTAMP '{ts}', 7, 'итог',
               TIMESTAMP '{ts}', TIMESTAMP '{ts}')
        ), users(id, name, status, department_id, supervisor_id) AS (
            VALUES (101, 'Свой отдел', 'working', 10, 7),
                   (102, 'Свой отдел, другой СВ', 'working', 10, 8),
                   (103, 'Чужой отдел', 'working', 20, 9),
                   (7, 'Супервайзер', 'working', 10, NULL::int)
        )
        """.format(d3=day(3), d5=day(5), d10=day(10), dm5=day(-5), ts=stamp)

    def _run(self, recorder):
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute(self._fixture() + recorder.sql, recorder.params)
            return cursor.fetchall()
        finally:
            prod_db.rollback()
            cursor.close()

    def setUp(self):
        reason = prod_db.skip_reason()
        if reason:
            self.skipTest(reason)

    def test_department_scope_really_filters(self):
        recorder = self.Recorder()
        cp.list_for_scope(recorder, scope={'scope': 'departments', 'department_ids': [10]})
        rows = self._run(recorder)
        self.assertEqual([1, 2], sorted(row[0] for row in rows),
                         'чужой отдел обязан отпасть, а свой — остаться целиком')

    def test_supervisor_scope_really_filters(self):
        recorder = self.Recorder()
        cp.list_for_scope(recorder, scope={'scope': 'supervisor', 'supervisor_id': 7})
        rows = self._run(recorder)
        self.assertEqual([1], [row[0] for row in rows],
                         'СВ без отдела видит только своих операторов')

    def test_global_admin_sees_everything(self):
        recorder = self.Recorder()
        cp.list_for_scope(recorder, scope={'scope': 'all'})
        rows = self._run(recorder)
        self.assertEqual([1, 2, 3], sorted(row[0] for row in rows))

    def test_open_points_come_first_and_by_due_date(self):
        recorder = self.Recorder()
        cp.list_for_scope(recorder, scope={'scope': 'all'},
                          statuses=('open', 'done', 'cancelled'))
        rows = self._run(recorder)
        # Открытые сверху по возрастанию срока (+3, +5, +10), закрытая — в хвосте.
        self.assertEqual([1, 3, 2, 4], [row[0] for row in rows])

    def test_feedback_lookup_returns_one_row_per_feedback(self):
        """DISTINCT ON: у оценки одна строка — живая, а если живой нет, последняя."""
        recorder = self.Recorder()
        cp.fetch_by_feedback_ids(recorder, [88, 89, 91])
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute(self._fixture() + recorder.sql, recorder.params)
            rows = cursor.fetchall()
        finally:
            prod_db.rollback()
            cursor.close()
        self.assertEqual([1, 2, 4], sorted(row[0] for row in rows))

    def test_bell_query_for_manager_runs(self):
        recorder = self.Recorder()
        notif_sources.checkpoints(
            recorder,
            {'user_id': 7, 'checkpoints': {'scope': 'departments',
                                           'department_ids': [10], 'is_manager': True}},
            5,
        )
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute(self._fixture() + recorder.sql, recorder.params)
            rows = cursor.fetchall()
        finally:
            prod_db.rollback()
            cursor.close()
        # Срок ещё не наступил ни у одной открытой точки отдела — колокол молчит.
        self.assertEqual([], rows)

    def test_bell_query_for_operator_returns_only_his_own(self):
        """Ветка сотрудника: своя точка, и в ответе только срок и «что проверить»."""
        recorder = self.Recorder()
        notif_sources.checkpoints(recorder, {'user_id': 101, 'checkpoints': {}}, 5)
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute(self._fixture() + recorder.sql, recorder.params)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
        finally:
            prod_db.rollback()
            cursor.close()
        self.assertEqual([1], [row[0] for row in rows],
                         'закрытая точка того же человека в колокол не идёт')
        self.assertEqual(['id', 'due_date', 'focus', 'total'], columns,
                         'сотруднику не должны выбираться служебные колонки')

    def test_write_statements_parse(self):
        """Пишущие запросы модуля тоже разбираются Postgres.

        Их не выполнить даже по синтетическим данным (CTE не подменяет цель
        UPDATE/INSERT/DELETE), но разбор проверить можно: `undefined_table` и
        `read-only transaction` означают, что до имени таблицы дело дошло, а
        `syntax error` — что нет.
        """
        recorder = self.Recorder()
        recorder.rowcount = 0
        cp.drop_for_feedback(recorder, 88)
        statements = [(recorder.sql, recorder.params)]

        recorder = self.Recorder()
        recorder.fetchone = lambda: [1]
        cp.resolve(recorder, 5, requester_id=7, status='done', comment='итог')
        statements.append((recorder.sql, recorder.params))

        recorder = self.Recorder()
        recorder.fetchone = lambda: [1]
        cp.reopen(recorder, 5, requester_id=7)
        statements.append((recorder.sql, recorder.params))

        for sql, params in statements:
            with self.subTest(sql=sql.strip().splitlines()[0]):
                self._sql_is_parsed(sql, params)

    def _sql_is_parsed(self, statement, params=None):
        """Разбор дошёл до имени таблицы или до прав — значит синтаксис цел."""
        import psycopg2
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute(statement, params)
        except psycopg2.Error as error:
            code = error.pgcode or ''
            # 42P01 undefined_table, 25006 read_only, 42501 insufficient_privilege
            self.assertIn(code, ('42P01', '25006', '42501'),
                          'SQL не разобран: %s' % str(error).strip().splitlines()[0])
            return
        finally:
            prod_db.rollback()
            cursor.close()
        self.fail('запрос выполнился на READ ONLY соединении — проверьте, куда он ушёл')

    def _ddl_is_parsed(self, statement):
        """True, если Postgres РАЗОБРАЛ инструкцию (упёрся в права, а не в синтаксис)."""
        import psycopg2
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute(statement)
        except psycopg2.Error as error:
            code = error.pgcode or ''
            # 25006 read_only_sql_transaction, 42501 insufficient_privilege
            self.assertIn(code, ('25006', '42501'),
                          'SQL не разобран: %s' % str(error).strip().splitlines()[0])
            return True
        finally:
            prod_db.rollback()
            cursor.close()
        # Инструкция ВЫПОЛНИЛАСЬ на «читающем» соединении — это тревога, а не успех.
        self.fail('DDL выполнился на READ ONLY соединении — проверьте, куда он ушёл')
        return False

    def test_new_schema_statements_parse(self):
        from trainings import schema as trainings_schema
        statements = [s for s in trainings_schema._STATEMENTS if 'operator_checkpoints' in s]
        self.assertEqual(4, len(statements), 'ожидались таблица и три индекса')
        for statement in statements:
            with self.subTest(statement=statement.strip().splitlines()[0]):
                self._ddl_is_parsed(statement)

    def test_bell_trigger_when_clause_parses(self):
        """Главная ловушка: WHEN у триггера с INSERT не может ссылаться на OLD."""
        self._ddl_is_parsed("""
            CREATE TRIGGER trg_bell_checkpoints_probe
            AFTER UPDATE OF status, due_date, operator_id, supervisor_id,
                            notify_operator, focus
            ON operator_checkpoints
            FOR EACH ROW
            WHEN (
                OLD.status IS DISTINCT FROM NEW.status
                OR OLD.due_date IS DISTINCT FROM NEW.due_date
                OR OLD.operator_id IS DISTINCT FROM NEW.operator_id
                OR OLD.supervisor_id IS DISTINCT FROM NEW.supervisor_id
                OR OLD.notify_operator IS DISTINCT FROM NEW.notify_operator
                OR OLD.focus IS DISTINCT FROM NEW.focus
            )
            EXECUTE FUNCTION bell_notify_change();
        """)

    def test_deployed_schema_matches_the_code(self):
        """Схема на проде совпадает с тем, что описано в коде.

        До выката тест проверял, что таблицы ещё нет. После выката смысл
        другой и полезнее: сверить РАЗВЁРНУТУЮ схему с ожидаемой. Разворот
        раздела «Тренинги» идёт под одним SAVEPOINT — упавшая инструкция
        откатывает его молча, и «частично применилось» выглядит на проде ровно
        как «применилось». До первого выката пропускается.
        """
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute("SELECT to_regclass('public.operator_checkpoints')")
            if cursor.fetchone()[0] is None:
                self.skipTest('operator_checkpoints ещё не развёрнута на проде')

            cursor.execute("""
                SELECT indexname FROM pg_indexes
                 WHERE tablename = 'operator_checkpoints'
            """)
            indexes = {row[0] for row in cursor.fetchall()}

            cursor.execute("""
                SELECT indexdef FROM pg_indexes
                 WHERE tablename = 'operator_checkpoints'
                   AND indexname = 'uq_operator_checkpoints_feedback'
            """)
            unique_def = (cursor.fetchone() or [''])[0]

            cursor.execute("""
                SELECT tgname FROM pg_trigger
                 WHERE tgrelid = 'public.operator_checkpoints'::regclass
                   AND NOT tgisinternal
            """)
            triggers = {row[0] for row in cursor.fetchall()}

            cursor.execute("""
                SELECT conname FROM pg_constraint
                 WHERE conrelid = 'public.operator_checkpoints'::regclass
                   AND contype = 'c'
            """)
            checks = {row[0] for row in cursor.fetchall()}
        finally:
            prod_db.rollback()
            cursor.close()

        self.assertLessEqual(
            {'uq_operator_checkpoints_feedback', 'idx_operator_checkpoints_open',
             'idx_operator_checkpoints_operator'},
            indexes, 'не все индексы доехали')
        # Частичность индекса — не деталь: без неё повторная постановка на
        # контроль по той же ОС стирала бы проведённую проверку.
        self.assertIn("status)::text = 'open'", unique_def)
        self.assertEqual(
            {'trg_bell_checkpoints_insert', 'trg_bell_checkpoints'}, triggers,
            'колокол не узнает о точке без обоих триггеров')
        self.assertLessEqual(
            {'operator_checkpoints_kind_check', 'operator_checkpoints_status_check'},
            checks)

    def test_trainings_section_schema_survived_the_migration(self):
        """Схема раздела разворачивается ОДНИМ SAVEPOINT.

        Если бы новая таблица уронила его, вместе с ней молча откатились бы
        корпоративные темы и расширенный CHECK на причину тренинга — то есть
        сломался бы соседний раздел, а не мой.
        """
        cursor = prod_db.connection().cursor()
        try:
            cursor.execute("SELECT to_regclass('public.operator_checkpoints')")
            if cursor.fetchone()[0] is None:
                self.skipTest('operator_checkpoints ещё не развёрнута на проде')
            cursor.execute("SELECT to_regclass('public.training_topics')")
            topics_table = cursor.fetchone()[0]
            cursor.execute("""
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                 WHERE conname = 'trainings_reason_check'
            """)
            reason_check = (cursor.fetchone() or [''])[0]
        finally:
            prod_db.rollback()
            cursor.close()

        self.assertIsNotNone(topics_table, 'справочник корпоративных тем пропал')
        self.assertIn('topic_id IS NOT NULL', reason_check,
                      'расширенный CHECK на причину тренинга откатился')


if __name__ == '__main__':
    unittest.main()
