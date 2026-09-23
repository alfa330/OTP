"""Перерывы вне графика у Тез КЦ и отбивка о них в Telegram (возврат задачи #292).

Правило сверки общее с СЗоВ (_szov_break_classify) — здесь стережём то, что у Тез своё:
источник факта (события телефона iCORE Phone вместо истории Oktell), одно направление на
оба табло отдела и отбивка, которая молчит, когда нарушений нет.

Функции достаём из bot_schedule2.py через ast и исполняем в подготовленном namespace: так
проверяется настоящая логика. Импортировать модуль нельзя — на старте он поднимает пул к
боевой БД (тот же приём в test_szov_break_violations.py).
"""
import ast
import asyncio
import functools
import html
import logging
import os
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
DB_SOURCE = (ROOT / "database.py").read_text(encoding="utf-8-sig")
VIEW = (ROOT / "src" / "components" / "monitoring" / "TezWallboardView.jsx").read_text(encoding="utf-8-sig")
SZOV_VIEW = (ROOT / "src" / "components" / "monitoring" / "SzovWallboardView.jsx").read_text(encoding="utf-8-sig")

NAMES = {
    '_env_int',
    'SZOV_BREAK_MIN_MINUTES', 'SZOV_BREAK_TOLERANCE_MINUTES', 'SZOV_BREAK_MERGE_GAP_MINUTES',
    'SZOV_BREAK_SCAN_LOOKBACK_HOURS', 'SZOV_BREAK_REPORT_MAX_AGE_HOURS',
    'SZOV_BREAK_KIND_OFF_SCHEDULE', 'SZOV_BREAK_KIND_NOT_PLANNED', 'SZOV_BREAK_KIND_NO_SHIFT',
    'SZOV_BREAK_DIRECTION_LINE', 'SZOV_BREAK_DIRECTION_TEZ',
    '_szov_break_merge_episodes', '_szov_break_planned_for_day', '_szov_break_on_shift',
    '_szov_break_classify', '_szov_break_violation_detail',
    '_TEZ_WALLBOARD_MODEL_BY_DIRECTION', 'TEZ_BREAK_NOTE_LIMIT',
    '_tez_break_status_keys', '_tez_break_violations_scan', '_tez_break_broadcast_text',
    # Личная отбивка админам: джоба, её адресаты и право на строку «лично мне».
    'SZOV_BROADCAST_DIRECTION_TEZ', 'SZOV_BROADCAST_DIRECTION_LINE',
    '_tez_broadcast_personal_recipients', 'tez_break_broadcast_job',
    '_szov_broadcast_personal_owner', '_szov_broadcast_personal_state',
    '_normalize_user_role', 'ROLE_HIERARCHY', '_get_role_level', '_has_min_role',
    '_is_admin_role',
}

PARSE = '%Y-%m-%d %H:%M:%S'
NOW = datetime(2026, 9, 22, 18, 0, 0)


def _dt(value):
    return datetime.strptime(value, PARSE)


class _FakeDb:
    """База ровно в том объёме, в каком её трогает разбор перерывов Тез."""

    def __init__(self, episodes=None, planned=None, shifts=None):
        self.episodes = episodes or []
        self.planned = planned or {}
        self.shifts = shifts or {}
        self.saved = []
        self.episode_calls = []

    def _status_profile_for_calculation_model(self, model_code):
        # Настоящий профиль моделей Тез: словарь кабинета плюс словарь телефона.
        return {'break': {'break in work', 'перерыв', 'авто'}}

    def get_phone_break_episodes(self, operator_ids, window_from, window_to, status_keys=None):
        self.episode_calls.append({
            'operator_ids': set(operator_ids),
            'window_from': window_from,
            'window_to': window_to,
            'status_keys': set(status_keys or ()),
        })
        return list(self.episodes)

    def get_planned_breaks_for_days(self, operator_ids, days):
        return self.planned, self.shifts

    def save_szov_break_violations(self, rows):
        self.saved.extend(rows)
        return len(rows)


def _namespace(db=None, people=None, extra=None):
    tree = source_cache.parse(SOURCE)
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NAMES:
            body.append(node)
        elif isinstance(node, ast.Assign):
            if {t.id for t in node.targets if isinstance(t, ast.Name)} & NAMES:
                body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    roster = people if people is not None else {
        'tp': [{'id': 1, 'name': 'Тлеу Аскар'}],
        'op': [{'id': 2, 'name': 'Ким Дана'}],
    }
    ns = {
        'os': os, 're': re, 'logging': logging, 'html': html,
        'datetime': datetime, 'timedelta': timedelta,
        '_env_int': lambda name, default, minimum=None, maximum=None: default,
        'db': db if db is not None else _FakeDb(),
        # Состав отдела берётся у табло — здесь подменяем, чтобы не ходить в базу.
        '_tez_wallboard_people': lambda *args, **kwargs: roster,
    }
    ns.update(extra or {})
    exec(compile(module, "<tez-breaks>", "exec"), ns)
    missing = sorted(name for name in NAMES if name not in ns)
    if missing:
        raise AssertionError(f"не найдено в bot_schedule2.py: {missing}")
    return ns


class TezBreakScanTests(unittest.TestCase):
    """Разбор перерывов отдела: что берём за факт и что из этого нарушение."""

    def test_status_keys_come_from_the_calculation_model(self):
        """Перерыв в журнале нарушений и перерыв в учёте часов — одно и то же множество.

        Разъедься они, и человек попадал бы в нарушители за минуты, которых в его же
        часах нет."""
        db = _FakeDb()
        ns = _namespace(db=db)
        self.assertEqual(ns['_tez_break_status_keys'](), {'break in work', 'перерыв', 'авто'})
        self.assertEqual(ns['_TEZ_WALLBOARD_MODEL_BY_DIRECTION']['tp'], 'tez_line')

    def test_break_matching_the_schedule_is_not_a_violation(self):
        """Вышел тогда, когда перерыв стоит в графике, — молчим."""
        db = _FakeDb(
            episodes=[{'operator_id': 1, 'started_at': _dt('2026-09-22 14:00:00'),
                       'ended_at': _dt('2026-09-22 14:15:00')}],
            planned={(1, '2026-09-22'): [(14 * 60, 14 * 60 + 15)]},
            shifts={(1, '2026-09-22'): [(9 * 60, 19 * 60)]},
        )
        ns = _namespace(db=db)
        self.assertEqual(ns['_tez_break_violations_scan'](now=NOW), 0)
        self.assertEqual(db.saved, [])

    def test_break_off_the_schedule_is_written_with_the_tez_direction(self):
        """Сдвинулся дальше допуска — нарушение, и лежит оно в своём направлении."""
        db = _FakeDb(
            episodes=[{'operator_id': 1, 'started_at': _dt('2026-09-22 16:00:00'),
                       'ended_at': _dt('2026-09-22 16:20:00')}],
            planned={(1, '2026-09-22'): [(14 * 60, 14 * 60 + 15)]},
            shifts={(1, '2026-09-22'): [(9 * 60, 19 * 60)]},
        )
        ns = _namespace(db=db)
        self.assertEqual(ns['_tez_break_violations_scan'](now=NOW), 1)
        row = db.saved[0]
        self.assertEqual(row['direction'], ns['SZOV_BREAK_DIRECTION_TEZ'])
        self.assertEqual(row['direction'], 'tez')
        self.assertEqual(row['kind'], ns['SZOV_BREAK_KIND_OFF_SCHEDULE'])
        self.assertEqual(row['operator_name'], 'Тлеу Аскар')
        self.assertEqual(row['deviation_minutes'], 120)

    def test_both_directions_of_the_department_are_scanned(self):
        """ТП и ОП сверяются вместе: перерыв стоит в графике у человека, а не у линии."""
        db = _FakeDb(
            episodes=[{'operator_id': 2, 'started_at': _dt('2026-09-22 13:00:00'),
                       'ended_at': _dt('2026-09-22 13:30:00')}],
            shifts={(2, '2026-09-22'): [(9 * 60, 19 * 60)]},
        )
        ns = _namespace(db=db)
        self.assertEqual(ns['_tez_break_violations_scan'](now=NOW), 1)
        self.assertEqual(db.episode_calls[0]['operator_ids'], {1, 2})
        self.assertEqual(db.saved[0]['operator_name'], 'Ким Дана')
        self.assertEqual(db.saved[0]['kind'], ns['SZOV_BREAK_KIND_NOT_PLANNED'])

    def test_short_flicker_is_not_a_break(self):
        """Статус мигнул на минуту — это не перерыв, а мерцание: порог общий с СЗоВ."""
        db = _FakeDb(
            episodes=[{'operator_id': 1, 'started_at': _dt('2026-09-22 16:00:00'),
                       'ended_at': _dt('2026-09-22 16:01:00')}],
            shifts={(1, '2026-09-22'): [(9 * 60, 19 * 60)]},
        )
        ns = _namespace(db=db)
        self.assertEqual(ns['_tez_break_violations_scan'](now=NOW), 0)

    def test_window_is_the_shared_lookback(self):
        """Окно разбора и перекрытие — те же, что у СЗоВ: правило одно на компанию."""
        db = _FakeDb()
        ns = _namespace(db=db)
        ns['_tez_break_violations_scan'](now=NOW)
        call = db.episode_calls[0]
        self.assertEqual(call['window_from'],
                         NOW - timedelta(hours=ns['SZOV_BREAK_SCAN_LOOKBACK_HOURS']))
        # Верхняя граница чуть впереди «сейчас»: иначе только что начавшийся перерыв не
        # попал бы в окно вовсе.
        self.assertGreater(call['window_to'], NOW)
        self.assertEqual(call['status_keys'], {'break in work', 'перерыв', 'авто'})

    def test_empty_roster_does_not_touch_the_database(self):
        """Состав пуст — сверять не с чем; молча писать нули в журнал нельзя."""
        db = _FakeDb()
        ns = _namespace(db=db, people={'tp': [], 'op': []})
        self.assertEqual(ns['_tez_break_violations_scan'](now=NOW), 0)
        self.assertEqual(db.episode_calls, [])


class TezBreakBroadcastTextTests(unittest.TestCase):
    """Текст сообщения: молчание без нарушений и человеческие формулировки."""

    def setUp(self):
        self.ns = _namespace()

    def _violation(self, name, started, kind='not_planned', planned_start=None):
        return {'operator_name': name, 'started_at': started, 'violation_date': started[:10],
                'kind': kind, 'planned_start_minutes': planned_start}

    def test_no_violations_means_no_message(self):
        """Пустой текст — это и есть «писать не о чем»: джоба тогда молчит."""
        self.assertEqual(self.ns['_tez_break_broadcast_text']([]), '')
        self.assertEqual(self.ns['_tez_break_broadcast_text'](None), '')

    def test_line_names_the_person_the_time_and_the_reason(self):
        text = self.ns['_tez_break_broadcast_text']([
            self._violation('Тлеу Аскар', '2026-09-22 16:00:00'),
        ])
        self.assertIn('Тез КЦ', text)
        self.assertIn('Тлеу Аскар', text)
        self.assertIn('22.09', text)
        self.assertIn('16:00', text)
        self.assertIn('перерывов в графике на этот день нет', text)

    def test_wording_is_the_same_as_in_the_szov_broadcast(self):
        """Одно нарушение обязано читаться одинаково, в какой бы отдел про него ни написали."""
        row = self._violation('Тлеу Аскар', '2026-09-22 16:00:00',
                              kind='off_schedule', planned_start=840)
        text = self.ns['_tez_break_broadcast_text']([row])
        self.assertIn(self.ns['_szov_break_violation_detail'](row), text)

    def test_long_list_is_cut_with_a_counter(self):
        """Поимённо — до предела, остальные счётчиком: сообщение читают с телефона."""
        limit = self.ns['TEZ_BREAK_NOTE_LIMIT']
        rows = [self._violation(f'Оператор {i}', '2026-09-22 16:00:00') for i in range(limit + 3)]
        text = self.ns['_tez_break_broadcast_text'](rows)
        self.assertEqual(text.count('•'), limit)
        self.assertIn('и ещё 3', text)

    def test_names_are_escaped_for_html(self):
        """Сообщение уходит с parse_mode HTML: имя с «<» иначе уронило бы отправку."""
        text = self.ns['_tez_break_broadcast_text']([
            self._violation('Тлеу <Аскар>', '2026-09-22 16:00:00'),
        ])
        self.assertIn('&lt;Аскар&gt;', text)
        self.assertNotIn('<Аскар>', text)


class TezBreakEpisodeQueryTests(unittest.TestCase):
    """Запрос за эпизодами: что именно считается фактом перерыва."""

    def setUp(self):
        self.body = DB_SOURCE[DB_SOURCE.index('def get_phone_break_episodes('):]
        self.body = self.body[:self.body.index('\n    def ', 10)]

    def test_only_live_phone_events_are_taken(self):
        """Ночная выгрузка кабинета размечает ПРОШЕДШИЕ сутки: её «break in work»
        приехал бы эпизодом в полсмены и сделал бы нарушением каждый обед."""
        self.assertIn('e.client_event_id IS NOT NULL', self.body)
        self.assertIn("e.event_kind = 'status'", self.body)

    def test_end_of_the_episode_is_the_next_event_of_the_same_person(self):
        """Фильтр по статусу — СНАРУЖИ подзапроса: изнутри «следующим» оказался бы
        следующий перерыв, а не возврат на линию."""
        self.assertIn('LEAD(e.event_at) OVER (PARTITION BY e.operator_id', self.body)
        subquery = self.body[self.body.index('FROM ('):self.body.index(') x')]
        self.assertNotIn('status_key) = ANY', subquery)
        self.assertIn('WHERE regexp_replace(lower(btrim(x.status_key))', self.body)


class TezBreakWiringTests(unittest.TestCase):
    """Проводка: расписание, ручка журнала, получатели и кнопки на экране."""

    def test_scan_runs_every_hour_around_the_clock(self):
        """Смена отдела начинается затемно, а окно захода — три часа: гоняй разбор только
        по часам отбивки, и перерыв в 05:22 не разобрал бы никто."""
        self.assertIn('tez_break_scan_job,', SOURCE)
        self.assertIn("id='tez_break_scan'", SOURCE)
        self.assertIn("CronTrigger(hour='*', minute=TEZ_BREAK_SCAN_MINUTE,", SOURCE)
        # А сообщения — по своему расписанию, в рабочие часы.
        self.assertIn('tez_break_broadcast_job,', SOURCE)
        self.assertIn("id=f'tez_break_broadcast_{_hour:02d}{_minute:02d}'", SOURCE)
        self.assertIn('def _tez_break_broadcast_send_times():', SOURCE)

    def test_journal_fills_even_without_recipients(self):
        """Журнал в iCore обязан наполняться и тогда, когда отбивку никто не получает."""
        scan = SOURCE[SOURCE.index('async def tez_break_scan_job():'):]
        scan = scan[:scan.index('\nasync def ', 10)]
        self.assertIn('_tez_break_violations_scan', scan)
        self.assertNotIn('get_szov_broadcast_chats', scan)
        job = SOURCE[SOURCE.index('async def tez_break_broadcast_job():'):]
        job = job[:job.index('\n@app.route')]
        # Отправка разбор НЕ повторяет: он идёт своей почасовой джобой.
        self.assertNotIn('_tez_break_violations_scan', job)
        self.assertIn('if not recipients:', job)
        # Помечаем прочитанными только после удачной доставки: разом упавшая отправка
        # иначе проглотила бы предупреждения.
        self.assertIn('if delivered:', job)
        self.assertIn('mark_szov_break_violations_reported', job)

    def test_journal_endpoint_is_registered_and_guarded(self):
        self.assertIn(
            "@app.route('/api/tez_wallboard/break_violations', methods=['GET', 'OPTIONS'])",
            SOURCE)
        handler = SOURCE[SOURCE.index('def api_tez_wallboard_break_violations():'):]
        handler = handler[:handler.index('\ndef ', 10)]
        # Права те же, что у самого табло: журнал смотрят те же СВ и руководитель отдела.
        self.assertIn('requester_id, err = _tez_wallboard_guard()', handler)
        self.assertIn("if request.method == 'OPTIONS':", handler)
        self.assertIn('direction=SZOV_BREAK_DIRECTION_TEZ', handler)

    def test_direction_is_allowed_everywhere_it_is_checked(self):
        """Список направлений лежит в четырёх местах, и разъехаться им нельзя."""
        self.assertIn("SZOV_BROADCAST_DIRECTION_TEZ = 'tez'", SOURCE)
        self.assertIn('SZOV_BROADCAST_DIRECTION_OP, SZOV_BROADCAST_DIRECTION_TEZ)', SOURCE)
        self.assertIn("SZOV_BROADCAST_DIRECTIONS = ('osnova', 'chat', 'op', 'tez')", DB_SOURCE)
        self.assertIn("CHECK (direction IN ('osnova', 'chat', 'op', 'tez'))", DB_SOURCE)
        self.assertIn("CHECK (direction IN ('line', 'chat', 'tez'))", DB_SOURCE)

    def test_broadcast_settings_belong_to_the_tez_department_head(self):
        guard = SOURCE[SOURCE.index('def _szov_broadcast_guard():'):]
        guard = guard[:guard.index('\ndef ', 10)]
        self.assertIn('elif direction == SZOV_BROADCAST_DIRECTION_TEZ:', guard)
        self.assertIn("globals().get('_tez_wallboard_department_id')", guard)
        self.assertIn('const canManageTezBroadcastForUser',
                      (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8-sig'))

    def test_test_send_writes_even_when_there_is_nothing_to_report(self):
        """Кнопку нажал человек и ждёт ответа: молчание тут читалось бы как поломка."""
        send = SOURCE[SOURCE.index('async def _tez_break_broadcast_send('):]
        send = send[:send.index('\nasync def ', 10)]
        self.assertIn('сообщение проверочное', send)
        self.assertNotIn('mark_szov_break_violations_reported', send)
        self.assertIn('SZOV_BROADCAST_DIRECTION_TEZ: _tez_break_broadcast_send,', SOURCE)

    def test_screen_has_both_buttons_and_hides_the_modes(self):
        """Режимы получателя у Тез бессмысленны: сообщение и так уходит только при
        нарушениях, а выбор, который ни на что не влияет, — обещание без содержания."""
        self.assertIn('<BreakViolationsControls', VIEW)
        self.assertIn('<BroadcastControls', VIEW)
        self.assertIn('withModes={false}', VIEW)
        self.assertIn('canManageBroadcast ?', VIEW)
        # Журнал открыт всем, кто видит табло, — он стоит вне проверки прав на отбивку.
        self.assertLess(VIEW.index('<BreakViolationsControls'), VIEW.index('canManageBroadcast ?'))


class _FakeBot:
    """Бот, который запоминает отправленное и умеет «быть заблокированным» адресатом."""

    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    async def send_message(self, chat_id, text, parse_mode=None):
        if chat_id in self.fail_for:
            raise RuntimeError('Forbidden: bot was blocked by the user')
        self.sent.append((chat_id, text, parse_mode))


class _BroadcastDb:
    """База ровно в том объёме, в каком её трогает плановая отбивка Тез."""

    def __init__(self, chats=(), people=(), violations=(), people_error=None, enabled_for=()):
        self.chats = list(chats)
        self.people = list(people)
        self.violations = list(violations)
        self.people_error = people_error
        self.enabled_for = set(enabled_for)
        self.people_calls = []
        self.violation_reads = 0
        self.marked = []

    def get_szov_broadcast_chats(self, direction):
        assert direction == 'tez', direction
        return list(self.chats)

    def get_tez_broadcast_personal_recipients(self, tez_department_id=None):
        self.people_calls.append(tez_department_id)
        if self.people_error:
            raise self.people_error
        return list(self.people)

    def get_unreported_szov_break_violations(self, max_age_hours, direction=None):
        assert direction == 'tez', direction
        self.violation_reads += 1
        return list(self.violations)

    def mark_szov_break_violations_reported(self, ids):
        self.marked.extend(ids)

    def get_tez_broadcast_personal(self, user_id):
        return user_id in self.enabled_for


VIOLATION = {'id': 11, 'operator_name': 'Тлеу Аскар', 'started_at': '2026-09-22 16:00:00',
             'violation_date': '2026-09-22', 'kind': 'not_planned', 'planned_start_minutes': None}
TEZ_DEPARTMENT_ID = 7


def _run_broadcast_job(db, bot):
    ns = _namespace(db=db, extra={
        'asyncio': asyncio, 'functools': functools, 'bot': bot, 'executor_pool': None,
        '_tez_wallboard_department_id': lambda: TEZ_DEPARTMENT_ID,
    })
    asyncio.run(ns['tez_break_broadcast_job']())
    return ns


class TezPersonalBroadcastJobTests(unittest.TestCase):
    """Плановая отбивка: админ, включивший её себе, получает то же, что и группы."""

    def test_admin_gets_the_message_even_without_any_group(self):
        """Личная отбивка не зависит от групп: у отдела может не быть ни одной."""
        db = _BroadcastDb(people=[{'id': 5, 'name': 'Админ', 'telegram_id': 555}],
                          violations=[VIOLATION])
        bot = _FakeBot()
        _run_broadcast_job(db, bot)
        self.assertEqual([item[0] for item in bot.sent], [555])
        self.assertIn('Тлеу Аскар', bot.sent[0][1])
        self.assertEqual(bot.sent[0][2], 'HTML')
        self.assertEqual(db.marked, [11])
        # Граница отдела та же, что у формы: адресатов судят по id Тез КЦ.
        self.assertEqual(db.people_calls, [TEZ_DEPARTMENT_ID])

    def test_groups_and_admins_get_one_and_the_same_message(self):
        db = _BroadcastDb(
            chats=[{'chat_id': -100, 'is_enabled': True}, {'chat_id': -200, 'is_enabled': False}],
            people=[{'id': 5, 'name': 'Админ', 'telegram_id': 555}],
            violations=[VIOLATION])
        bot = _FakeBot()
        _run_broadcast_job(db, bot)
        self.assertEqual(sorted(item[0] for item in bot.sent), [-100, 555])
        self.assertEqual(len({item[1] for item in bot.sent}), 1)

    def test_blocked_bot_does_not_cost_the_others(self):
        """Человек заблокировал бота — группа всё равно получает, нарушения помечаются."""
        db = _BroadcastDb(chats=[{'chat_id': -100, 'is_enabled': True}],
                          people=[{'id': 5, 'name': 'Админ', 'telegram_id': 555}],
                          violations=[VIOLATION])
        bot = _FakeBot(fail_for={555})
        _run_broadcast_job(db, bot)
        self.assertEqual([item[0] for item in bot.sent], [-100])
        self.assertEqual(db.marked, [11])

    def test_failing_personal_lookup_does_not_silence_the_groups(self):
        db = _BroadcastDb(chats=[{'chat_id': -100, 'is_enabled': True}],
                          people_error=RuntimeError('column does not exist'),
                          violations=[VIOLATION])
        bot = _FakeBot()
        with self.assertLogs(level='ERROR'):
            _run_broadcast_job(db, bot)
        self.assertEqual([item[0] for item in bot.sent], [-100])

    def test_nobody_to_write_means_violations_are_not_read(self):
        db = _BroadcastDb(chats=[{'chat_id': -100, 'is_enabled': False}], violations=[VIOLATION])
        bot = _FakeBot()
        _run_broadcast_job(db, bot)
        self.assertEqual(bot.sent, [])
        self.assertEqual(db.violation_reads, 0)
        self.assertEqual(db.marked, [])

    def test_nothing_new_means_silence_for_admins_too(self):
        """«Нарушений нет» раз в час — шум и в личке, не только в группе."""
        db = _BroadcastDb(people=[{'id': 5, 'name': 'Админ', 'telegram_id': 555}])
        bot = _FakeBot()
        _run_broadcast_job(db, bot)
        self.assertEqual(bot.sent, [])
        self.assertEqual(db.marked, [])


def _owner_namespace(requester, db=None):
    """requester — кортеж как у db.get_user: (id, telegram_id, name, role)."""
    return _namespace(db=db or _BroadcastDb(), extra={
        '_get_authenticated_requester': lambda: (requester[0], requester, None),
    })


class TezPersonalBroadcastOwnerTests(unittest.TestCase):
    """Кому форма показывает строку «лично мне». Гейт отбивки к этому моменту пройден:
    админ — глава чужого отдела табло Тез не видит и сюда не попадает."""

    def test_admin_and_super_admin_get_the_row(self):
        for role in ('admin', 'super_admin', 'superadmin', 'Super Admin'):
            with self.subTest(role=role):
                ns = _owner_namespace((300, 777, 'Глава', role))
                self.assertIsNotNone(ns['_szov_broadcast_personal_owner']('tez'))

    def test_roles_below_admin_do_not(self):
        """Форму открывает любой глава Тез КЦ, но лично себе получает только админ."""
        for role in ('sv', 'supervisor', 'operator', 'trainer', ''):
            with self.subTest(role=role):
                ns = _owner_namespace((3, 777, 'СВ', role))
                self.assertIsNone(ns['_szov_broadcast_personal_owner']('tez'))
                self.assertIsNone(ns['_szov_broadcast_personal_state']('tez'))

    def test_only_the_tez_wallboard_has_it(self):
        ns = _owner_namespace((300, 777, 'Админ', 'admin'))
        for direction in ('osnova', 'chat', 'op'):
            with self.subTest(direction=direction):
                self.assertIsNone(ns['_szov_broadcast_personal_owner'](direction))
                self.assertIsNone(ns['_szov_broadcast_personal_state'](direction))

    def test_state_says_whether_telegram_is_linked(self):
        db = _BroadcastDb(enabled_for={300})
        linked = _owner_namespace((300, 777, 'Админ', 'admin'), db=db)
        self.assertEqual(linked['_szov_broadcast_personal_state']('tez'),
                         {'enabled': True, 'telegram_connected': True})
        unlinked = _owner_namespace((301, None, 'Админ', 'admin'), db=db)
        self.assertEqual(unlinked['_szov_broadcast_personal_state']('tez'),
                         {'enabled': False, 'telegram_connected': False})


class TezPersonalBroadcastWiringTests(unittest.TestCase):
    """Ручки, схема и форма личной отбивки."""

    def _handler(self, name):
        body = SOURCE[SOURCE.index(f'def {name}():'):]
        return body[:body.index('\n@app.route')]

    def test_settings_endpoint_toggles_it_behind_the_broadcast_guard(self):
        handler = self._handler('api_szov_wallboard_broadcast')
        self.assertLess(handler.index('requester_id, err = _szov_broadcast_guard()'),
                        handler.index("elif 'personal' in payload:"))
        branch = handler[handler.index("elif 'personal' in payload:"):handler.index('            else:')]
        self.assertIn('owner = _szov_broadcast_personal_owner(direction)', branch)
        self.assertIn('if owner is None:', branch)
        # Включить без Telegram нельзя — писать некуда; выключить можно всегда.
        self.assertIn('if enabled and not owner[1]:', branch)
        self.assertIn('db.set_tez_broadcast_personal(requester_id, enabled)', branch)
        # Личная настройка — не общий список получателей: в «кто менял» не пишется.
        self.assertNotIn('_log_szov_broadcast_change', branch)
        self.assertIn('"personal": _szov_broadcast_personal_state(direction),', handler)

    def test_test_send_writes_only_to_the_one_who_pressed(self):
        handler = self._handler('api_szov_wallboard_broadcast_test')
        branch = handler[handler.index("if payload.get('personal'):"):handler.index('    else:')]
        self.assertIn('owner = _szov_broadcast_personal_owner(direction)', branch)
        self.assertIn('chat_id = int(owner[1])', branch)
        self.assertNotIn("payload.get('chat_id')", branch)

    def test_schema_and_recipient_rule(self):
        self.assertIn('ALTER TABLE admin_profiles ADD COLUMN IF NOT EXISTS '
                      'tez_broadcast_personal_enabled BOOLEAN NOT NULL DEFAULT FALSE;', DB_SOURCE)
        method = DB_SOURCE[DB_SOURCE.index('def get_tez_broadcast_personal_recipients('):]
        method = method[:method.index('\n# Initialize database')]
        self.assertIn('u.telegram_id IS NOT NULL', method)
        self.assertIn("NOT IN ('fired', 'dismissal')", method)
        # Лестница раздела: супер-админ, админ без отдела, админ — глава Тез КЦ.
        self.assertIn("IN ('super_admin', 'superadmin', 'super-admin', 'super admin')", method)
        self.assertIn("LOWER(COALESCE(u.role, '')) = 'admin'", method)
        self.assertIn('NOT EXISTS (', method)
        self.assertIn('AND d.id = %s', method)

    def test_form_shows_the_row_only_when_the_server_sends_it(self):
        self.assertIn("const personal = state?.personal || null;", SZOV_VIEW)
        self.assertIn('{personal ? (', SZOV_VIEW)
        self.assertIn('Лично мне в Telegram', SZOV_VIEW)
        self.assertIn("{ personal: next }", SZOV_VIEW)
        self.assertIn('sendNow({ personal: true })', SZOV_VIEW)
        self.assertIn('sendNow({ chat_id: item.chat_id })', SZOV_VIEW)
        # Переключатель гаснет только на включение: выключить без Telegram можно.
        self.assertIn('disabled={busy || (!personal.enabled && !personal.telegram_connected)}', SZOV_VIEW)


if __name__ == '__main__':
    unittest.main()
