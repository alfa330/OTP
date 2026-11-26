# -*- coding: utf-8 -*-
"""«Табло ОП» · «Чат» по Syntony (26.11.2026): чаты верификаторов переехали из Wazzup.

Что закреплено (решения владельца 26.11.2026):
  * правила табло верификаторов — прежние (op_wallboard/chat.py): диалог до паузы 6 ч, первый
    ответ и ответ внутри чата, задержка — ответившему, ответы других групп верификаторам не идут;
  * ответ ИИ закрывает ожидание клиента и никому не засчитан; ИИ передал чат человеку — клиент
    ждёт человека с передачи, «передаю оператору» ИИ ожидание не закрывает; диалог, который вёл
    только ИИ, — не чат верификаторов ни в итогах, ни на стене;
  * статусы — по ответам: «Онлайн» — ответил клиенту за 15 минут на любом номере сети, «Не в
    системе» — с ухода (последний ответ плюс 15 минут), 16 часов без ответа — «Нет ответов»;
  * закрытие диалога в Syntony — то же, что кнопка «Ответ не нужен» «Чатов ОП»;
  * только Syntony с дня перехода; выгрузка за сутки до перехода — Wazzup; откат —
    OP_CHAT_SOURCE=wazzup;
  * номера верификаторов с номерами СЗоВ не пересекаются; данные замерли — табло говорит словами.
Люди и номера выдуманы.
"""

import re
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from flask import Flask
from openpyxl import load_workbook

from op_wallboard import chat as C, routes as op_routes
from syntony_chats import szov as S, verifiers as V
from tests import source_cache
from tests.test_szov_wallboard import _load_names

ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = source_cache.monolith_flat_path().read_text(encoding='utf-8-sig')
MONITORING = ROOT / 'src' / 'components' / 'monitoring'
ALMATY = ZoneInfo('Asia/Almaty')

DAY = date(2026, 11, 25)
VER_A, VER_B, VER_C, OTHER = 501, 502, 503, 900
NAMES = {VER_A: 'Тлеуқабыл Әсем Маратқызы', VER_B: 'Бекет Нұрлан Серікұлы', VER_C: 'Оразова Дилбар'}
# Как их зовут в Syntony: казахскими буквами и без отчества, порядок — как вписали.
AUTHORS = {'Тлеукабыл Асем': VER_A, 'Нұрлан Бекет': VER_B, 'Оразова Дильбар': VER_C,
           'Сидоров Пётр': OTHER}


def at(hh, mm=0, ss=0, ms=0, day=DAY):
    return (datetime.combine(day, datetime.min.time())
            + timedelta(hours=hh, minutes=mm, seconds=ss, milliseconds=ms))


def _row(when, chat, channel, **fields):
    row = {'channel_id': channel, 'chat_id': chat, 'at': when, 'is_echo': False, 'user_id': None,
           'is_bot': False, 'author_id': None, 'type': None,
           'message_id': f'{chat}-{when.isoformat()}-{fields.get("tag", "")}'}
    fields.pop('tag', None)
    row.update(fields)
    return row


def client(when, chat='c1', channel='ch', conv=None):
    return _row(when, chat, channel, tag='in', conversation_id=conv)


def reply(when, user_id, chat='c1', channel='ch', conv=None):
    return _row(when, chat, channel, tag=f'h{user_id}', is_echo=True, user_id=user_id,
                author_id=f'author-{user_id}', conversation_id=conv)


def ai(when, chat='c1', channel='ch', conv=None):
    return _row(when, chat, channel, tag='ai', is_echo=True, is_ai=True, conversation_id=conv)


def handoff(when, chat='c1', channel='ch', conv=None):
    return _row(when, chat, channel, tag='handoff', is_handoff=True, conversation_id=conv)


def day_block(rows, day=DAY, now=None):
    messages = C.classify(rows, NAMES)
    return C.day_block(C.split_episodes(messages), messages, day, names=NAMES, now=now)


def now_block(rows, now, waiting_since=None):
    return C.now_block(C.split_episodes(C.classify(rows, NAMES)), now, waiting_since=waiting_since)


class AiAndHandoffRulesTests(unittest.TestCase):
    def test_ai_answer_closes_the_wait_and_is_nobody_s(self):
        """ИИ ответил в 10:00:05 — клиент не ждёт; написал снова в 10:03 — верификатор ответил
        через две минуты: это и первый ответ, и ответ внутри чата. ИИ в людях нет."""
        block = day_block([client(at(10)), ai(at(10, 0, 5)), client(at(10, 3)),
                           reply(at(10, 5), VER_A)])
        self.assertEqual(block['today']['chats'], 1)
        self.assertEqual(block['today']['first_reply_seconds'], 120)
        self.assertEqual(block['today']['inner_reply_seconds'], 120)
        self.assertEqual(set(block['operators']), {VER_A})
        self.assertEqual(block['operators'][VER_A]['first_reply_seconds'], 120)

    def test_handoff_starts_the_wait_for_a_human(self):
        """Клиент написал в 10:01, ИИ передал чат в 10:01:30 — ждать человека он начал с передачи."""
        block = day_block([client(at(10)), ai(at(10, 0, 5)), client(at(10, 1)),
                           handoff(at(10, 1, 30)), ai(at(10, 1, 30)), reply(at(10, 3), VER_A)])
        self.assertEqual(block['today']['first_reply_seconds'], 90)
        self.assertEqual(block['today']['inner_reply_seconds'], 90)

    def test_ai_handoff_message_does_not_close_the_wait_in_either_order(self):
        """«Передаю оператору» ИИ пишет в ту же секунду, что передача: чуть раньше или чуть позже
        — ответом клиенту оно не становится."""
        for ai_at in (at(10, 1, 29, ms=900), at(10, 1, 30), at(10, 1, 30, ms=200)):
            with self.subTest(ai_at=ai_at):
                block = day_block([client(at(10, 1)), ai(ai_at), handoff(at(10, 1, 30)),
                                   reply(at(10, 3), VER_A)])
                self.assertEqual(block['today']['first_reply_seconds'], 90)

    def test_dialog_run_only_by_ai_is_not_a_verifier_chat(self):
        rows = [client(at(11)), ai(at(11, 0, 4)), client(at(11, 2)), ai(at(11, 2, 3)),
                client(at(11, 4))]
        self.assertEqual(day_block(rows)['today']['chats'], 0)
        current = now_block(rows, at(11, 5))
        self.assertEqual((current['chats_in_work'], current['chats_waiting']), (0, 0))

    def test_handed_off_and_unanswered_waits_since_the_handoff(self):
        """Передан человеку и не отвечен — чат верификаторов без первого ответа, а на стене клиент
        ждёт с передачи (10 мин), а не с первой реплики (11 мин)."""
        rows = [client(at(12)), ai(at(12, 0, 4)), handoff(at(12, 1)), ai(at(12, 1))]
        today = day_block(rows)['today']
        self.assertEqual((today['chats'], today['first_reply_seconds']), (1, None))
        current = now_block(rows, at(12, 11))
        self.assertEqual((current['chats_waiting'], current['longest_wait_seconds']), (1, 600))
        self.assertEqual(current['chats_in_work'], 1)

    def test_after_a_human_answer_ai_closes_the_wait_again(self):
        """Человек ответил после передачи — «ждёт человека» снято: если потом снова отвечает ИИ,
        клиент на стене не ждёт, а задержки ИИ ни у кого нет."""
        rows = [client(at(10)), ai(at(10, 0, 5)), handoff(at(10, 1)), reply(at(10, 2), VER_A),
                client(at(10, 30)), ai(at(10, 30, 5))]
        current = now_block(rows, at(10, 35))
        self.assertEqual(current['chats_waiting'], 0)
        block = day_block(rows)
        self.assertEqual((block['today']['first_reply_seconds'], block['today']['inner_reply_seconds']), (60, 60))

    def test_handoff_lives_in_its_own_syntony_conversation(self):
        """Передан человеку в диалоге A и не отвечен, A закрыли; новый диалог B того же водителя ведёт
        ИИ — его ответы закрывают ожидание: на стене клиент не ждёт, а позже написавшему верификатору
        «первого ответа» от передачи в A не засчитано (находка разбора 26.11.2026)."""
        rows = [client(at(9), conv='A'), handoff(at(9, 1), conv='A'), client(at(9, 40), conv='B'),
                ai(at(9, 40, 5), conv='B'), client(at(9, 42), conv='B'), ai(at(9, 42, 4), conv='B')]
        current = now_block(rows, at(9, 50))
        self.assertEqual((current['chats_waiting'], current['longest_wait_seconds']), (0, None))
        block = day_block(rows + [reply(at(10), VER_A, conv='B')])
        self.assertIsNone(block['today']['first_reply_seconds'])
        # В том же диалоге после передачи ИИ ожидание по-прежнему не закрывает.
        same = [client(at(9), conv='A'), handoff(at(9, 1), conv='A'), client(at(9, 40), conv='A'),
                ai(at(9, 40, 5), conv='A'), reply(at(9, 45), VER_A, conv='A')]
        self.assertEqual(day_block(same)['today']['first_reply_seconds'], 44 * 60)

    def test_ai_answer_counts_for_the_overnight_rule(self):
        """Клиенту ночью ответил ИИ — утренняя реплика через 7 ч начинает новый диалог, как после
        ответа коллеги (правило «неотвеченный клиент держит диалог 12 ч» — про тех, кому не ответил
        никто). Передал ИИ человеку и никто не ответил — клиент ждал человека: диалог тот же."""
        rows = [client(at(2)), ai(at(2, 0, 10)), client(at(2, 5)), client(at(9)), reply(at(9, 2), VER_A)]
        today = day_block(rows)['today']
        self.assertEqual((today['chats'], today['first_reply_seconds']), (1, 120))
        handed = [client(at(2)), ai(at(2, 0, 10)), handoff(at(2, 1)), ai(at(2, 1)), client(at(9)),
                  reply(at(9, 2), VER_A)]
        today = day_block(handed)['today']
        self.assertEqual((today['chats'], today['first_reply_seconds']), (1, 7 * 3600 + 60))

    def test_lone_handoff_mark_is_not_a_dialog(self):
        """Отметка передачи, оказавшаяся отдельно от реплик (после тишины в 6 ч), — не чат."""
        rows = [client(at(1)), ai(at(1, 0, 5)), handoff(at(8))]
        self.assertEqual(day_block(rows)['today']['chats'], 0)
        current = now_block(rows, at(8, 5))
        self.assertEqual((current['chats_in_work'], current['chats_waiting']), (0, 0))

    def test_other_group_after_ai_is_not_a_verifier_chat(self):
        rows = [client(at(13)), ai(at(13, 0, 3)), handoff(at(13, 1)), reply(at(13, 2), OTHER)]
        self.assertEqual(day_block(rows)['today']['chats'], 0)

    def test_ai_between_people_closes_the_wait_and_credits_nobody(self):
        rows = [client(at(9)), reply(at(9, 1), VER_A), client(at(9, 10)), ai(at(9, 10, 5)),
                client(at(9, 20)), reply(at(9, 22), VER_B)]
        block = day_block(rows)
        self.assertEqual(block['today']['first_reply_seconds'], 60)
        self.assertEqual(block['today']['inner_reply_seconds'], 90)       # (60 + 120) / 2
        self.assertEqual(block['operators'][VER_B]['inner_reply_seconds'], 120)
        self.assertIsNone(block['operators'][VER_B]['first_reply_seconds'])

    def test_closed_dialog_leaves_the_wall_like_reply_not_needed(self):
        rows = [client(at(14)), reply(at(14, 1), VER_A), client(at(14, 30))]
        self.assertEqual(now_block(rows, at(14, 40))['chats_waiting'], 1)
        self.assertEqual(now_block(rows, at(14, 40), {('ch', 'c1'): None})['chats_waiting'], 0)


def _msg(mid, direction, sender, author=None, when=None, chat='k1', channel='ch', deleted=None, conv='cv'):
    return (mid, channel, chat, direction, sender, author, deleted, when or at(10), conv)


def _resolve(author):
    return AUTHORS.get(author) if AUTHORS.get(author) in NAMES else None


class RowsTests(unittest.TestCase):
    def test_message_kinds(self):
        window = V.Window(messages=[
            _msg('1', 'inbound', 'contact', when=at(10)),
            _msg('2', 'inbound', 'system', when=at(10, 0, 1)),            # не водитель — нет строки
            _msg('3', 'outbound', 'human', 'Тлеукабыл Асем', at(10, 1)),
            _msg('4', 'outbound', 'human', 'Сидоров Пётр', at(10, 2)),     # другая группа
            _msg('5', 'outbound', 'human', None, at(10, 3)),               # с телефона мимо Syntony
            _msg('6', 'outbound', 'ai', None, at(10, 4)),
            _msg('7', 'outbound', 'system', None, at(10, 5)),             # строка звонка
            _msg('8', 'outbound', 'human', 'Тлеукабыл Асем', at(10, 6), deleted='unsent'),
        ])
        rows = V.build_rows(window, _resolve)
        self.assertEqual([row['message_id'] for row in rows], ['1', '3', '4', '5', '6'])
        kinds = [item['kind'] for item in C.classify(rows, NAMES)]
        self.assertEqual(kinds, [C.KIND_CLIENT, C.KIND_VERIFIER, C.KIND_OTHER, C.KIND_OTHER, C.KIND_AI])
        self.assertEqual(rows[1]['user_id'], VER_A)

    def test_handoff_row_only_inside_the_window(self):
        window = V.Window(conversations={'cv1': ('ch', 'k1', at(10), None),
                                         'cv2': ('ch', 'k2', at(3), None),
                                         'cv3': ('ch', 'k3', None, None)})
        rows = V.build_rows(window, _resolve, since=at(6), until=at(12))
        self.assertEqual([(row['chat_id'], row['is_handoff']) for row in rows], [('k1', True)])
        self.assertEqual(C.classify(rows, NAMES)[0]['kind'], C.KIND_HANDOFF)

    def test_chat_key_is_the_driver_on_the_number(self):
        self.assertIn("COALESCE(NULLIF(c.contact_id, ''), c.id)", V._WINDOW_SQL)
        self.assertIn('c.channel_id = ANY(%(channels)s)', V._WINDOW_SQL)
        self.assertNotIn('ORDER BY', V._WINDOW_SQL)

    def test_closing_the_dialog_is_reply_not_needed(self):
        window = V.Window(conversations={'cv1': ('ch', 'k1', None, at(10, 30)),
                                         'cv2': ('ch', 'k2', None, at(10, 30)),
                                         'cv3': ('ch', 'k3', None, None)})
        rows = [client(at(10, 20), chat='k1'), client(at(10, 20), chat='k2'),
                client(at(10, 40), chat='k2'), client(at(10, 45), chat='k2'),
                client(at(10, 20), chat='k3')]
        got = V.waiting_since(window, rows, {('ch', 'k1'), ('ch', 'k2'), ('ch', 'k3')})
        self.assertEqual(got, {('ch', 'k1'): None, ('ch', 'k2'): at(10, 40)})
        self.assertEqual(V.waiting_since(window, rows, set()), {})
        # Отметка передачи после закрытия — не реплика клиента: ждать по-прежнему нечего.
        got = V.waiting_since(window, rows + [handoff(at(10, 35), chat='k1')], {('ch', 'k1')})
        self.assertEqual(got, {('ch', 'k1'): None})


class PresenceTests(unittest.TestCase):
    def test_online_offline_and_no_replies(self):
        now = at(15)
        replies = [('Тлеукабыл Асем', at(14, 40)), ('Тлеукабыл Асем', at(14, 50)),
                   ('Тлеукабыл Асем', at(14, 56)), ('Нұрлан Бекет', at(13)),
                   ('Оразова Дильбар', now - timedelta(hours=17)), ('Сидоров Пётр', at(14, 59)),
                   ('Тлеукабыл Асем', at(15, 1))]                       # «из будущего» — не в счёт
        live, events = V.presence(replies, _resolve, NAMES, now)
        self.assertEqual(live[VER_A], {'status_key': V.ONLINE_KEY, 'event_at': '2026-11-25T14:40:00',
                                       'seconds': 20 * 60})
        self.assertEqual(live[VER_B], {'status_key': V.OFFLINE_KEY, 'event_at': '2026-11-25T13:15:00',
                                       'seconds': 105 * 60})
        self.assertNotIn(VER_C, live)                                  # 17 ч без ответа
        self.assertNotIn(OTHER, live)
        self.assertEqual(sorted({event['operator_id'] for event in events}), [VER_A, VER_B, VER_C])
        self.assertEqual([V.status_entry(key)[:2] for key in (V.ONLINE_KEY, V.OFFLINE_KEY, None)],
                         [('Онлайн', 'free'), ('Не в системе', 'offline'), ('Нет ответов', 'unknown')])

    def test_stretch_breaks_on_a_gap_longer_than_the_window(self):
        replies = [('Тлеукабыл Асем', at(14)), ('Тлеукабыл Асем', at(14, 30)), ('Тлеукабыл Асем', at(14, 40))]
        live, _events = V.presence(replies, _resolve, NAMES, at(14, 45))
        self.assertEqual(live[VER_A]['event_at'], '2026-11-25T14:30:00')


class SourceTests(unittest.TestCase):
    def test_verifier_numbers_are_not_szov_numbers(self):
        self.assertEqual(len(V.VERIFIER_CHANNEL_IDS), 48)
        self.assertFalse(V.VERIFIER_CHANNEL_IDS & S.SZOV_CHANNEL_IDS)
        source = (ROOT / 'syntony_chats' / 'verifiers.py').read_text(encoding='utf-8')
        # Полных номеров в публичном репозитории нет — только хвосты (id каналов не в счёт).
        source = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '', source)
        self.assertIsNone(re.search(r'\d{10}', source))

    def test_environment_switches(self):
        on = {'SYNTONY_CHATS_WEBHOOK_SECRET': 'secret'}
        self.assertTrue(V.source_enabled(on))
        self.assertFalse(V.source_enabled({}))                          # брать переписку неоткуда
        self.assertFalse(V.source_enabled(dict(on, OP_CHAT_SOURCE='wazzup')))
        self.assertEqual(V.first_day({}), V.SYNTONY_FROM_DEFAULT)
        self.assertEqual(V.first_day({'OP_CHAT_SYNTONY_FROM': '2026-12-01'}), date(2026, 12, 1))
        self.assertEqual(V.first_day({'OP_CHAT_SYNTONY_FROM': 'завтра'}), V.SYNTONY_FROM_DEFAULT)
        self.assertTrue(V.covers(V.SYNTONY_FROM_DEFAULT, on))
        self.assertFalse(V.covers(V.SYNTONY_FROM_DEFAULT - timedelta(days=1), on))
        self.assertEqual(V.channel_ids({'OP_CHAT_SYNTONY_CHANNEL_IDS': 'a, b'}), frozenset({'a', 'b'}))
        self.assertEqual(V.channel_ids({}), V.VERIFIER_CHANNEL_IDS)

    def test_frozen_data_speaks_like_the_szov_board(self):
        state = {'marks': {}, 'earliest': datetime(2026, 11, 25, 8, tzinfo=ALMATY)}
        with mock.patch.object(S, '_puller_on', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Syntony: сутки неполные — чаты есть с 25.11.2026 08:00'):
                V.check_fresh(state, at(12), at(12))
            self.assertIn('Нет данных Syntony за 25.11.2026', V.coverage_error(state, DAY))
            state['earliest'] = datetime(2026, 11, 17, tzinfo=ALMATY)
            V.check_fresh(state, at(12), at(12))                        # без сторожа — не падает
            self.assertIsNone(V.coverage_error(state, DAY))
        with mock.patch.object(S, '_puller_on', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'первая загрузка'):
                V.check_fresh(state, at(12), at(12))


class _FakeDb:
    def __init__(self):
        self.departments = [{'id': 7, 'code': 'op'}]

    def get_departments(self):
        return self.departments

    def get_user_department_id(self, user_id):
        return None

    def _get_cursor(self):
        @contextmanager
        def cursor():
            yield object()
        return cursor()


def _passthrough_cache(*, fetch, **_ignored):
    return dict(fetch(), stale=False, age_seconds=0)


def _clock_at(moment):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.replace(tzinfo=tz)
    return Clock


class RouteTests(unittest.TestCase):
    def setUp(self):
        now = datetime.now(ALMATY).replace(tzinfo=None, hour=12, minute=0, second=0, microsecond=0)
        self.now = now
        # Сторож API в тестах «не запущен»: свежесть судится только по полноте суток.
        patcher = mock.patch.object(S, '_puller_on', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.today = now.date()
        self.first_day = self.today - timedelta(days=1)
        patcher = mock.patch.object(op_routes, 'datetime', _clock_at(now))
        patcher.start()
        self.addCleanup(patcher.stop)
        minutes = lambda value: now - timedelta(minutes=value)
        self.window = V.Window(messages=[
            # Верификатор А отвечает сейчас — онлайн, чат в работе.
            _msg('1', 'inbound', 'contact', when=minutes(10), chat='d1'),
            _msg('2', 'outbound', 'human', 'Тлеукабыл Асем', minutes(9), chat='d1'),
            # Диалог только ИИ — не чат верификаторов.
            _msg('3', 'inbound', 'contact', when=minutes(30), chat='d2'),
            _msg('4', 'outbound', 'ai', None, minutes(30), chat='d2'),
            # Передан человеку 3 минуты назад и ждёт.
            _msg('5', 'inbound', 'contact', when=minutes(5), chat='d3'),
            _msg('6', 'outbound', 'ai', None, minutes(3), chat='d3'),
            # Писал сотрудник другой группы.
            _msg('7', 'inbound', 'contact', when=minutes(50), chat='d4'),
            _msg('8', 'outbound', 'human', 'Сидоров Пётр', minutes(49), chat='d4'),
        ], conversations={'cv3': ('ch', 'd3', minutes(3), None)}, newest=minutes(3))
        self.replies = [('Тлеукабыл Асем', now - timedelta(hours=3)), ('Тлеукабыл Асем', minutes(9)),
                        ('Нұрлан Бекет', now - timedelta(hours=2))]
        self.state = {'marks': {}, 'earliest': datetime(2026, 11, 17, tzinfo=ALMATY)}
        self.asked = {'window': [], 'wazzup': [], 'phone': 0, 'live': 0}
        for name, value in (
                ('load_chat_verifiers', lambda cursor, day_from, day_to=None: dict(NAMES)),
                ('load_chat_verifier_days', lambda cursor, day_from, day_to=None: (dict(NAMES), {})),
                ('load_syntony_window', self.load_window),
                ('load_syntony_replies', lambda cursor, since, until: list(self.replies)),
                ('load_syntony_state', lambda cursor: dict(self.state)),
                ('load_chat_messages', self.load_wazzup),
                ('load_chat_last_message_at', lambda cursor, account='op': now),
                ('load_chat_waiting', lambda cursor, keys, account='op': {}),
                ('load_phone_events', self.load_phone_events)):
            patcher = mock.patch.object(op_routes, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db = _FakeDb()
        department_id = op_routes.make_department_resolver(db)
        guard = op_routes.make_guard(
            db=db, department_id=department_id,
            get_authenticated_requester=lambda: (10, (None, None, None, 'admin'), None),
            normalize_user_role=lambda role: role,
            is_global_admin_requester=lambda role, uid: True,
            headed_department_id=lambda uid: None, is_supervisor_role=lambda role: False)
        self.covers = lambda day: day >= self.first_day
        app = Flask(__name__)
        self.bp = op_routes.build_op_wallboard_blueprint(
            db=db, require_api_key=lambda fn: fn, build_cors_preflight_response=lambda: ('', 204),
            guard=guard, department_id=department_id, snapshot_with_cache=_passthrough_cache,
            restore_cache=lambda *a, **k: None, persist_cache=lambda *a, **k: None,
            status_entry=lambda key: ('Нет событий', 'unknown', 90),
            load_people=lambda dept, day: [], live_statuses=self.live_statuses,
            chat_syntony={'matcher': lambda names: _resolve, 'covers': lambda day: self.covers(day),
                          'channels': lambda: frozenset({'ch'})})
        app.register_blueprint(self.bp)
        self.client = app.test_client()

    def load_window(self, cursor, since, until, channels, is_test_sql=None):
        self.asked['window'].append((since, until, set(channels)))
        return self.window

    def load_wazzup(self, cursor, since, until, account='op'):
        self.asked['wazzup'].append((since, until))
        return []

    def load_phone_events(self, cursor, ids, day, lookback_hours=0):
        self.asked['phone'] += 1
        return []

    def live_statuses(self, ids):
        self.asked['live'] += 1
        return {}

    def test_snapshot_counts_by_syntony_rules(self):
        body = self.client.get('/api/op_wallboard/chat_snapshot').get_json()
        self.assertEqual((body['source'], body['source_label'], body['statuses_source'], body['journal'],
                          body['presence_minutes']), ('syntony', 'Syntony', 'replies', False, 15))
        # d1 (верификатор) и d3 (передан человеку); d2 — только ИИ, d4 — другая группа.
        self.assertEqual(body['today']['chats'], 2)
        self.assertEqual(body['today']['first_reply_seconds'], 60)
        self.assertEqual((body['now']['chats_waiting'], body['now']['longest_wait_seconds']), (1, 180))
        self.assertEqual(body['now']['chats_in_work'], 2)
        self.assertEqual(body['diagnostics']['other_authors'], ['Сидоров Пётр'])
        # Окно — с 6 часов до полуночи, по номерам верификаторов.
        since, _until, channels = self.asked['window'][-1]
        self.assertEqual(since, datetime.combine(self.today, datetime.min.time()) - timedelta(hours=6))
        self.assertEqual(channels, {'ch'})
        # Статусы смены «Чатов ОП» и события телефонов на Syntony не спрашиваются.
        self.assertEqual((self.asked['phone'], self.asked['live'], self.asked['wazzup']), (0, 0, []))

    def test_statuses_are_by_replies(self):
        people = {row['user_id']: row for row in
                  self.client.get('/api/op_wallboard/chat_snapshot').get_json()['now']['operators']}
        a, b, c = people[VER_A], people[VER_B], people[VER_C]
        self.assertEqual((a['status_label'], a['status_key'], a['status_seconds']), ('Онлайн', 'free', 9 * 60))
        self.assertEqual((b['status_label'], b['status_key']), ('Не в системе', 'offline'))
        self.assertEqual(b['status_seconds'], 105 * 60)
        self.assertEqual((c['status_label'], c['status_key'], c['entry_at']), ('Нет ответов', 'unknown', None))
        # Вход — первый ответ рабочего блока (три часа назад), как первое событие смены.
        self.assertEqual(a['entry_at'], (self.now - timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%S'))
        self.assertEqual(list(people), [VER_A, VER_B, VER_C])          # онлайн сверху

    def test_night_shift_entry_survives_a_quiet_night(self):
        """Ночник отвечал с 22:00, потом пауза 4 ч 45 мин (тихая ночь) — вход всё равно вчерашний."""
        midnight = datetime.combine(self.today, datetime.min.time())
        self.replies = [('Нұрлан Бекет', midnight - timedelta(hours=2)),
                        ('Нұрлан Бекет', midnight - timedelta(minutes=10)),
                        ('Нұрлан Бекет', midnight + timedelta(minutes=10)),
                        ('Нұрлан Бекет', midnight + timedelta(hours=4, minutes=55))]
        people = {row['user_id']: row for row in
                  self.client.get('/api/op_wallboard/chat_snapshot').get_json()['now']['operators']}
        self.assertEqual(people[VER_B]['entry_at'],
                         (midnight - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%S'))

    def test_frozen_syntony_is_an_error_in_words(self):
        self.state['earliest'] = datetime.combine(self.today, datetime.min.time(), tzinfo=ALMATY) + timedelta(hours=3)
        response = self.client.get('/api/op_wallboard/chat_snapshot')
        self.assertEqual(response.status_code, 503)
        self.assertIn('Syntony: сутки неполные', response.get_json()['error'])

    def test_rollback_reads_wazzup(self):
        self.covers = lambda day: False
        body = self.client.get('/api/op_wallboard/chat_snapshot').get_json()
        self.assertNotIn('source_label', body)
        self.assertEqual(len(self.asked['wazzup']), 1)
        self.assertEqual(self.asked['window'], [])

    def test_export_reads_wazzup_before_the_move_and_syntony_after(self):
        before = self.first_day - timedelta(days=1)
        response = self.client.get(f'/api/op_wallboard/chat_export?date_from={before}&date_to={self.first_day}')
        self.assertEqual(response.status_code, 200)
        load_workbook(BytesIO(response.data))
        (wazzup_since, wazzup_until), = self.asked['wazzup']
        self.assertEqual(wazzup_since.date(), before - timedelta(days=1))   # с вечера накануне
        self.assertEqual(wazzup_until, datetime.combine(self.first_day, datetime.min.time()) + timedelta(hours=6))
        (since, _until, _channels), = self.asked['window']
        self.assertEqual(since, datetime.combine(self.first_day, datetime.min.time()) - timedelta(hours=6))

    def test_export_without_syntony_data_is_an_error_not_zeros(self):
        self.state['earliest'] = datetime.combine(self.today, datetime.min.time(), tzinfo=ALMATY)
        response = self.client.get(f'/api/op_wallboard/chat_export?date_from={self.first_day}&date_to={self.first_day}')
        self.assertEqual(response.status_code, 500)
        self.assertIn('Нет данных Syntony', response.get_json()['detail'])

    def test_midnight_broadcast_day_is_syntony(self):
        block = self.bp.chat_day(self.first_day)
        self.assertEqual(block['day'], self.first_day.isoformat())
        self.assertEqual(len(self.asked['window']), 1)


class MonolithTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = _load_names(BOT_SOURCE, {
            '_op_chat_syntony_matcher', '_status_import_operator_name_variants',
            '_status_import_resolve_operator_matches', '_status_import_normalize_operator_name',
            '_status_import_dedupe_operator_infos', '_operator_name_strip_vendor_mark',
            'OPERATOR_NAME_VENDOR_MARK_RE', '_KZ_TO_RU_FOLD',
            '_szov_wallboard_int', '_szov_format_age_ru', '_szov_plural', '_szov_format_seconds_mmss',
            '_szov_broadcast_stale_note', '_op_chat_broadcast_stream_note',
            '_op_chat_broadcast_stale_source',
        }, {'re': re})

    def test_names_match_kazakh_letters_order_patronymic_and_soft_sign(self):
        resolve = self.ns['_op_chat_syntony_matcher'](NAMES)
        for author, expected in AUTHORS.items():
            with self.subTest(author=author):
                self.assertEqual(resolve(author), expected if expected in NAMES else None)
        self.assertIsNone(resolve(None))
        self.assertIsNone(resolve(''))
        # Двое с одинаковыми фамилией и именем — никому: совпадение должно быть единственным.
        twins = self.ns['_op_chat_syntony_matcher']({1: 'Ким Алия', 2: 'Ким Алия Сергеевна'})
        self.assertIsNone(twins('Ким Алия'))

    def test_broadcast_names_syntony(self):
        stream = self.ns['_op_chat_broadcast_stream_note']
        data = {'stream': {'silent': True, 'silent_seconds': 1800}, 'source_label': 'Syntony'}
        self.assertIn('Также Syntony не присылает сообщения уже ', stream(data))
        self.assertIn('Wazzup', stream(dict(data, source_label=None)))
        stale = self.ns['_op_chat_broadcast_stale_source']
        self.assertEqual((stale({'source_label': 'Syntony'}), stale({})), ('Syntony', 'базы iCORE'))

    def test_board_is_wired_with_syntony(self):
        self.assertIn("chat_syntony={'matcher': _op_chat_syntony_matcher, 'is_test_sql': _op_chat_test_is_test_sql},",
                      BOT_SOURCE)
        self.assertIn("'source_label': snapshot.get('source_label') or 'Wazzup',", BOT_SOURCE)


class FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = (MONITORING / 'OpWallboardView.jsx').read_text(encoding='utf-8-sig')
        cls.board = (MONITORING / 'OpChatWallboard.jsx').read_text(encoding='utf-8-sig')
        cls.shared = (MONITORING / 'opWallboardShared.js').read_text(encoding='utf-8-sig')

    def test_source_is_named_by_the_snapshot(self):
        self.assertIn("Чаты верификаторов · {snapshot?.source_label || 'Wazzup'}", self.view)
        self.assertIn("const source = snapshot?.source_label || 'Wazzup';", self.shared)
        self.assertIn("{ key: 'chat', label: 'Чат', hint: 'Чаты верификаторов: Syntony' }", self.shared)
        self.assertNotIn('либо Wazzup перестал', self.view)

    def test_statuses_by_replies_have_no_journal(self):
        self.assertIn('const canOpen = snapshot?.journal !== false', self.board)
        self.assertIn("snapshot?.statuses_source === 'replies'", self.board)
        self.assertIn("byReplies ? 'не отвечавших' : 'не начинавших смену'", self.board)


if __name__ == '__main__':
    unittest.main()
