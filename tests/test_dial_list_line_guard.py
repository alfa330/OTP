# -*- coding: utf-8 -*-
"""Защита линии обзвона (владелец 18.11.2026: «много подобных ошибок… операторы теряют время»).

За 17–18.11.2026 «Линия не на связи» получал каждый пятый запрос звонка удалённого КЦ. Причин две:
линию держит другой телефон (АТС помнит одну регистрацию — последнюю) и ответ телефона на плечо
не доходит до АТС. Сервер здесь делает две вещи:
  - отдаёт телефону правила защиты линии — блок line_guard состояния обзвона (исполняет их
    телефон 3.22.59+, docs/DIAL-LINE-GUARD.md в его репозитории);
  - при быстром отказе кода 150 говорит оператору, КТО держит линию: его же телефон на другом
    компьютере или телефон прежнего оператора линии.
Без базы. Люди выдуманы.
"""
import contextlib
import os
import time
import unittest
from datetime import date
from unittest import mock

from dial_list import service as dial_service

GUARD_ENVS = (dial_service.LINE_CLAIM_ENV, dial_service.LINE_RETRIES_ENV, dial_service.NO_ACK_DROP_ENV)
LINE_150 = "Binotel calls/internal-number-to-external-number status='error' code=150: Can't call to the ext"
# Справочник итогов отдела не пуст — иначе сервис заводит его заново (строка — как в test_dial_list).
OUTCOMES = ('FROM dial_list_outcomes o', {'all': [('o-1', 'Отказ', '#FF3B30', 1, False, True, 0, [])]})


def guard(env=None):
    """Блок line_guard при заданных переменных; остальные рубильники из окружения убраны."""
    with mock.patch.dict('os.environ', dict(env or {})):
        for name in GUARD_ENVS:
            if name not in (env or {}):
                os.environ.pop(name, None)
        return dial_service.line_guard()


class _Cursor:
    """Курсор-подделка: ответ — по первой подстроке SQL из rules."""

    def __init__(self, rules=()):
        self.rules, self.executed, self._current = list(rules), [], {}

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._current = next((answer for sub, answer in self.rules if sub in sql), {})
        if isinstance(self._current, Exception):
            raise self._current

    def fetchone(self):
        return self._current.get('one')

    def fetchall(self):
        return list(self._current.get('all', []))

    def sql_with(self, fragment):
        return [(sql, params) for sql, params in self.executed if fragment in sql]


class _Db:
    def __init__(self, cursor):
        self.cursor = cursor

    @contextlib.contextmanager
    def _get_cursor(self):
        yield self.cursor


def _ctx(uid, profile='remote_cc'):
    return {"user_id": uid, "department_id": 1954, "name": "Оператор", "internal_number": "946",
            "settings": {"portion_size": 20, "period": "2026-11-01", "period_label": "Ноябрь 2026",
                         "_period": date(2026, 11, 1), "retry_after_hours": 24, "max_attempts": 3,
                         "caller_id_for_employee": "", "profile": profile}}


class LineGuardBlockTests(unittest.TestCase):
    def test_defaults(self):
        """Линию занимать, отказ повторять дважды. Неподтверждённое плечо НЕ класть: проба на АТС
        18.11.2026 показала, что BYE телефона она не слушает и ждёт свои 30 с, а плечи, которые
        соединяются с опозданием, сброс оборвал бы."""
        block = guard()
        self.assertEqual(block, {"claim": True, "retries": 2, "no_ack_drop_sec": 0})
        # Типы — как их читает телефон.
        self.assertIs(type(block["claim"]), bool)
        self.assertIs(type(block["retries"]), int)
        self.assertIs(type(block["no_ack_drop_sec"]), int)

    def test_switches(self):
        self.assertIs(guard({dial_service.LINE_CLAIM_ENV: '0'})["claim"], False)
        self.assertIs(guard({dial_service.LINE_CLAIM_ENV: 'off'})["claim"], False)
        self.assertIs(guard({dial_service.LINE_CLAIM_ENV: '1'})["claim"], True)
        self.assertEqual(guard({dial_service.LINE_RETRIES_ENV: '0'})["retries"], 0)
        self.assertEqual(guard({dial_service.LINE_RETRIES_ENV: '1'})["retries"], 1)
        # 0 — не класть; меньше трёх секунд нельзя: ответ на плечо к этому времени ушёл всего дважды.
        self.assertEqual(guard({dial_service.NO_ACK_DROP_ENV: '0'})["no_ack_drop_sec"], 0)
        self.assertEqual(guard({dial_service.NO_ACK_DROP_ENV: '4'})["no_ack_drop_sec"], 4)
        self.assertEqual(guard({dial_service.NO_ACK_DROP_ENV: '1'})["no_ack_drop_sec"], 3)
        self.assertEqual(guard({dial_service.NO_ACK_DROP_ENV: '7'})["no_ack_drop_sec"], 7)

    def test_garbage_and_bounds(self):
        self.assertEqual(guard({dial_service.LINE_RETRIES_ENV: 'много'})["retries"], 2)
        self.assertEqual(guard({dial_service.LINE_RETRIES_ENV: '99'})["retries"], 3)
        self.assertEqual(guard({dial_service.LINE_RETRIES_ENV: '-5'})["retries"], 0)
        self.assertEqual(guard({dial_service.NO_ACK_DROP_ENV: 'x'})["no_ack_drop_sec"], 0)
        self.assertEqual(guard({dial_service.NO_ACK_DROP_ENV: '600'})["no_ack_drop_sec"], 15)


class StateCarriesGuardTests(unittest.TestCase):
    def _state(self, profile, phone_version):
        svc = dial_service.DialListService(db=_Db(_Cursor([OUTCOMES])))
        svc.operator_context = lambda uid: _ctx(uid, profile)
        svc.reconcile = lambda ctx: None
        svc._wa_phone_block = lambda settings, name: {}
        with mock.patch.dict('os.environ', {}):
            for name in GUARD_ENVS:
                os.environ.pop(name, None)
            return svc.get_state(7, phone_version=phone_version)

    def test_calls_through_the_pbx_get_the_block(self):
        """Удалённый КЦ и Тез со старым телефоном звонят через АТС — плечо есть, правила нужны."""
        for profile, version in (('remote_cc', '3.22.59'), ('remote_cc', '3.22.58'), ('remote_cc', None),
                                 ('tez', '3.22.45')):
            state = self._state(profile, version)
            self.assertEqual(state["line_guard"], {"claim": True, "retries": 2, "no_ack_drop_sec": 0},
                             (profile, version))

    def test_direct_dial_has_no_leg_and_no_block(self):
        """Прямой звонок Тез: телефон набирает номер сам — плеча от АТС нет, защищать нечего."""
        state = self._state('tez', '3.22.59')
        self.assertIs(state["direct_dial"], True)
        self.assertNotIn("line_guard", state)


class LineTakenHintTests(unittest.TestCase):
    def _hint(self, answer):
        cur = _Cursor([('FROM user_sessions', answer)])
        svc = dial_service.DialListService(db=_Db(cur))
        return svc._line_taken_hint(_ctx(149)), cur

    def test_own_phone_on_another_computer(self):
        hint, cur = self._hint({'one': (1, None)})
        self.assertIn("iCORE Phone под вашей учётной записью открыт ещё на одном компьютере", hint)
        self.assertIn("Закройте его там и позвоните ещё раз", hint)
        sql, params = cur.sql_with('FROM user_sessions')[0]
        # Только живые сессии телефона: не отозванные, с его user_agent, виденные недавно.
        self.assertIn("revoked_at IS NULL", sql)
        self.assertIn("user_agent LIKE %s", sql)
        self.assertIn("AT TIME ZONE 'Asia/Almaty'", sql)          # last_seen_at — местное время
        self.assertEqual(params, ("iCORE Phone%", 15, 149, 149, 1954, "946", 149, 24))
        # «Другой телефон» — сессия, которую видели ПОСЛЕ последнего входа сотрудника: выход из аккаунта
        # сессию не отзывает, и свежая сессия прошлого входа с этого же компьютера — не второй телефон.
        self.assertIn("SELECT max(created_at) AS entered_at FROM live WHERE user_id = %s", sql)
        self.assertIn("live.created_at < own.entered_at", sql)
        self.assertIn("live.last_seen_at > own.entered_at", sql)

    def test_own_phone_wins_over_previous_holder(self):
        hint, _ = self._hint({'one': (2, 'Петров Пётр')})
        self.assertIn("ещё на одном компьютере", hint)
        self.assertNotIn("Петров", hint)

    def test_previous_holder_still_registered(self):
        hint, cur = self._hint({'one': (0, 'Петров Пётр')})
        self.assertIn("Похоже, её ещё держит телефон прежнего оператора линии (Петров Пётр)", hint)
        self.assertIn("пусть перезапустит iCORE Phone", hint)
        sql = cur.sql_with('FROM user_sessions')[0][0]
        # Прежний оператор — снятый с ЭТОЙ линии недавно, и его телефон жив.
        self.assertIn("m.department_id = %s AND m.internal_number = %s AND m.user_id <> %s", sql)
        self.assertIn("m.released_at > CURRENT_TIMESTAMP - make_interval(hours => %s)", sql)
        self.assertIn("EXISTS (SELECT 1 FROM live WHERE live.user_id = m.user_id)", sql)

    def test_nobody_else(self):
        self.assertEqual(self._hint({'one': (0, None)})[0], "")
        self.assertEqual(self._hint({'one': (None, None)})[0], "")
        self.assertEqual(self._hint({'one': None})[0], "")

    def test_database_error_never_breaks_the_call_error(self):
        with self.assertLogs('dial_list.service', level='WARNING'):
            hint, _ = self._hint(RuntimeError("нет соединения"))
        self.assertEqual(hint, "")


class StartCallRefusalTextTests(unittest.TestCase):
    """Что видит оператор при коде 150: быстрый отказ — кто держит линию; долгий — что с линией."""

    def _refuse(self, waited_sec, sessions=None):
        rules = [('FOR UPDATE OF a', {'one': ('asg-1', 'issued', 0, '77010000001', 'Иванов', 'lead-1', False)}),
                 ('INSERT INTO dial_list_attempts', {'one': ('att-1', None)}), OUTCOMES]
        if sessions is not None:
            rules.append(('FROM user_sessions', {'one': sessions}))
        cur = _Cursor(rules)
        svc = dial_service.DialListService(db=_Db(cur))
        svc.operator_context = _ctx
        svc._precheck_call_signature = lambda ctx, assignment_id: None
        svc._line_status_hint = lambda dep, num: 'Binotel при этом видит линию в сети — повторите через минуту.'
        clock = {'now': 1000.0}

        class Client:
            def originate_internal_to_external(self, internal_number, external_number, **extra):
                clock['now'] += waited_sec          # столько Binotel думал над отказом
                raise RuntimeError(LINE_150)

        svc._client = lambda dep: Client()
        with mock.patch.object(time, 'monotonic', lambda: clock['now']), \
                self.assertLogs('dial_list.service', level='WARNING'), \
                self.assertRaises(dial_service.DialListError) as ctx:
            svc.start_call(149, 'asg-1')
        return ctx.exception, cur

    def test_fast_refusal_names_the_other_phone(self):
        exc, cur = self._refuse(3, sessions=(1, None))
        self.assertEqual((exc.kind, exc.status), ('line', 409))
        text = str(exc)
        self.assertTrue(text.startswith("Линия 946 не на связи: АТС не смогла до неё дозвониться. Её занял другой "
                                        "телефон"), text)
        self.assertIn("ещё на одном компьютере", text)
        self.assertNotIn("видит линию в сети", text)
        # Водителю отказ не в счёт — как и раньше.
        self.assertEqual(len(cur.sql_with('SET attempts = GREATEST(attempts - 1, 0)')), 1)

    def test_fast_refusal_without_other_phone_keeps_the_old_hint(self):
        exc, cur = self._refuse(3, sessions=(0, None))
        self.assertIn("видит линию в сети", str(exc))
        self.assertEqual(len(cur.sql_with('FROM user_sessions')), 1)

    def test_slow_refusal_is_not_about_another_phone(self):
        """АТС ждала ответа телефона 30 с — линию никто не отбирал: сессии даже не читаем."""
        exc, cur = self._refuse(31, sessions=(1, None))
        self.assertIn("видит линию в сети", str(exc))
        self.assertNotIn("другой телефон", str(exc))
        self.assertEqual(cur.sql_with('FROM user_sessions'), [])
        self.assertLess(dial_service.LINE_REFUSED_FAST_SEC, 25)   # отказ «не дождалась» — от 30 с


if __name__ == '__main__':
    unittest.main()
