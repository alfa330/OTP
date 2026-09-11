# -*- coding: utf-8 -*-
"""Фиксация суточных итогов и ночное окно выгрузки (op_funnel).

Тест закрепляет дефект, который сделал бы раздел бесполезным с первого же дня, и
две поправки рядом с ним. Все три нашло ревью перед выкладкой.

**Главный.** Ночная выгрузка шла в 05:20 за окно «вчера и сегодня». Сегодняшние
сутки в 05:20 пусты по определению: дневная смена ещё не звонила, часы не
посчитаны. Они записывались нулями, а назавтра приходили как «вчера», натыкались
на существующий ключ и молча пропускались — `ON CONFLICT DO NOTHING`. Каждый день
навсегда оставался пустым, раздел показывал нули по всем четырём направлениям, а
журнал прогонов при этом честно писал «ок».

Лечится двумя независимыми правками, и нужны ОБЕ: окно смотрит только на закрытые
сутки, а незакрытые сутки перезаписываются всегда — иначе кнопка «Обновить»
посреди смены зафиксировала бы полдня как итог.
"""

import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from op_funnel import metrics, queries, sync


class FakeCursor:
    """Курсор, который помнит вставленные строки. Базы здесь нет намеренно."""

    def __init__(self, existing=None):
        self.existing = existing or {}
        self.statements = []
        self._rows = []
        self.description = None
        self.rowcount = 0

    def execute(self, sql, args=None):
        self.statements.append((' '.join(sql.split()), args))
        text = sql.strip().upper()
        if text.startswith('SELECT') and 'OP_FUNNEL_DAILY' in sql.upper():
            names = ['direction_code', 'work_day', 'user_id'] + list(queries.DRIFT_METRICS)
            self.description = [(name,) for name in names]
            self._rows = [
                tuple([key[0], key[1], key[2]] + [row.get(m, 0) for m in queries.DRIFT_METRICS])
                for key, row in self.existing.items()
            ]
        else:
            self.description = None
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def inserted_into(self, table):
        return [sql for sql, _ in self.statements if 'INSERT INTO %s' % table in sql]


def daily_row(day, user_id=7, **over):
    row = {
        'direction_code': 'op_potok', 'work_day': day, 'user_id': user_id,
        'rate': 0.5, 'shift_kind': 'day', 'group_id': 14,
        'work_hours': 8.0, 'hours_source': 'phone',
        'handled': 10, 'reached': 5, 'not_reached': 5, 'agreed': 3, 'succeeded': 1,
        'rejected': 1, 'untargeted': 1, 'callbacks': 0, 'inbound': 0,
        'plan_reached': 160.0, 'plan_agreed': 40.0, 'chats': 0, 'tickets': 0,
        'chat_reply_seconds': None, 'ticket_handle_seconds': None, 'quality_score': None,
        'extra': {},
    }
    row.update(over)
    return row


TODAY = date(2026, 9, 11)
YESTERDAY = TODAY - timedelta(days=1)


class FreezeTests(unittest.TestCase):

    def test_новые_закрытые_сутки_фиксируются(self):
        cursor = FakeCursor()
        result = queries.freeze_daily(cursor, [daily_row(YESTERDAY)], today=TODAY)
        self.assertEqual(result['frozen'], 1)
        self.assertTrue(cursor.inserted_into('op_funnel_daily'))

    def test_зафиксированные_закрытые_сутки_не_трогаются_без_force(self):
        # Цифру, названную на планёрке, задним числом менять нельзя.
        existing = {('op_potok', YESTERDAY, 7): {'handled': 10, 'reached': 5}}
        cursor = FakeCursor(existing)
        result = queries.freeze_daily(cursor, [daily_row(YESTERDAY, handled=99)], today=TODAY)
        self.assertEqual((result['frozen'], result['redone']), (0, 0))
        self.assertEqual(cursor.inserted_into('op_funnel_daily'), [])

    def test_с_force_перезаписывает_и_пишет_расхождение(self):
        existing = {('op_potok', YESTERDAY, 7): {'handled': 10, 'reached': 5}}
        cursor = FakeCursor(existing)
        result = queries.freeze_daily(cursor, [daily_row(YESTERDAY, reached=34)],
                                      force=True, today=TODAY)
        self.assertEqual(result['redone'], 1)
        self.assertGreaterEqual(result['drift'], 1)
        self.assertTrue(cursor.inserted_into('op_funnel_drift'))

    def test_РЕГРЕССИЯ_идущие_сутки_перезаписываются_всегда(self):
        """Без этого раздел показывал бы нули каждый день.

        Ночная выгрузка записывала сегодняшние сутки пустыми, а следующий прогон
        не мог их обновить: ключ уже существовал. Незакрытые сутки — это снимок,
        а не итог, и он обязан обновляться без всякого force.
        """
        existing = {('op_potok', TODAY, 7): {'handled': 0, 'reached': 0}}
        cursor = FakeCursor(existing)
        result = queries.freeze_daily(cursor, [daily_row(TODAY, handled=120, reached=60)],
                                      today=TODAY)
        self.assertEqual(result['redone'], 1, 'сегодняшние сутки обязаны перезаписаться')
        self.assertTrue(cursor.inserted_into('op_funnel_daily'))

    def test_у_идущих_суток_расхождения_не_журналируются(self):
        # Это не дрейф источника, а просто более свежий снимок: журнал дрейфа
        # завален такими записями стал бы бесполезен.
        existing = {('op_potok', TODAY, 7): {'handled': 0, 'reached': 0}}
        cursor = FakeCursor(existing)
        result = queries.freeze_daily(cursor, [daily_row(TODAY, handled=120)], today=TODAY)
        self.assertEqual(result['drift'], 0)
        self.assertEqual(cursor.inserted_into('op_funnel_drift'), [])


class NightlyWindowTests(unittest.TestCase):

    def test_РЕГРЕССИЯ_ночное_окно_не_берёт_сегодняшний_день(self):
        """В 05:20 сегодняшние сутки пусты по определению — фиксировать нечего."""
        captured = []

        def fake_sync(db, direction_code, day_from, day_to, **kwargs):
            captured.append((day_from, day_to))
            return {'direction_code': direction_code, 'status': 'ok'}

        original = sync.sync_direction
        sync.sync_direction = fake_sync
        try:
            sync.sync_all(db=None, directions=('op_potok',))
        finally:
            sync.sync_direction = original

        day_from, day_to = captured[0]
        self.assertLess(day_to, date.today(), 'ночное окно обязано кончаться вчера')
        self.assertEqual((day_to - day_from).days + 1, sync.NIGHTLY_WINDOW_DAYS)


class MergeLoadTests(unittest.TestCase):
    """Чаты и тикеты нескольких несопоставленных операторов не должны теряться."""

    def test_РЕГРЕССИЯ_несопоставленные_складываются_а_не_затирают(self):
        # Все они идут под user_id = 0, и присваивание оставляло бы одного.
        rows = [
            {'user_id': None, 'work_day': YESTERDAY, 'chats': 10, 'reply_seconds': 100},
            {'user_id': None, 'work_day': YESTERDAY, 'chats': 30, 'reply_seconds': 200},
        ]
        merged = sync._merge_load(rows, 'chats', 'reply_seconds')
        self.assertEqual(merged[(0, YESTERDAY)]['chats'], 40)

    def test_среднее_время_взвешивается_числом_чатов(self):
        # Иначе автор с двумя чатами тянет среднее так же, как автор с двумястами.
        rows = [
            {'user_id': None, 'work_day': YESTERDAY, 'chats': 2, 'reply_seconds': 300},
            {'user_id': None, 'work_day': YESTERDAY, 'chats': 198, 'reply_seconds': 100},
        ]
        merged = sync._merge_load(rows, 'chats', 'reply_seconds')
        self.assertAlmostEqual(merged[(0, YESTERDAY)]['reply_seconds'], 102.0, places=1)

    def test_сопоставленные_не_смешиваются(self):
        rows = [
            {'user_id': 7, 'work_day': YESTERDAY, 'chats': 10, 'reply_seconds': 100},
            {'user_id': 9, 'work_day': YESTERDAY, 'chats': 30, 'reply_seconds': 200},
        ]
        merged = sync._merge_load(rows, 'chats', 'reply_seconds')
        self.assertEqual(merged[(7, YESTERDAY)]['chats'], 10)
        self.assertEqual(merged[(9, YESTERDAY)]['chats'], 30)

    def test_без_времени_среднее_пустое(self):
        rows = [{'user_id': 7, 'work_day': YESTERDAY, 'chats': 3, 'reply_seconds': None}]
        merged = sync._merge_load(rows, 'chats', 'reply_seconds')
        self.assertIsNone(merged[(7, YESTERDAY)]['reply_seconds'])


class UpsertLeadsTests(unittest.TestCase):

    def test_РЕГРЕССИЯ_дубль_лида_в_одной_пачке_не_роняет_вставку(self):
        """Postgres не даёт ON CONFLICT DO UPDATE тронуть строку дважды в одном
        операторе (ошибка 21000). А дубли неизбежны: СРМ дописывает лиды прямо во
        время обхода страниц, и лид приезжает и на своей странице, и на соседней.
        То есть ронял бы выгрузку ровно тот случай, ради которого ON CONFLICT и
        поставили."""
        row = {name: None for name in queries._LEAD_COLUMNS}
        row.update({'direction_code': 'op_potok', 'source': 'crm_stream',
                    'stream_type': 1, 'lead_key': '42', 'work_day': YESTERDAY})
        cursor = FakeCursor()
        written = queries.upsert_leads(cursor, [dict(row), dict(row, owner_raw='свежее')])
        self.assertEqual(written, 1, 'дубль обязан схлопнуться до вставки')
        self.assertEqual(len(cursor.inserted_into('op_funnel_leads')), 1)


class PlanTests(unittest.TestCase):
    """План задаётся тремя разными способами — нельзя знать только один."""

    def test_норма_в_час(self):
        plan = metrics.plan_for_day(6, {'reached_per_hour': 20, 'agreed_per_hour': 5})
        self.assertEqual(plan, {'plan_reached': 120.0, 'plan_agreed': 30.0})

    def test_РЕГРЕССИЯ_месячный_план_на_ставку(self):
        # У «Основы» и «Верификатора» норм в час нет, и план выходил нулевым
        # всегда — колонка «% плана» была вечным прочерком.
        plan = metrics.plan_for_day(8, {'plan_per_fte': 440, 'norm_hours_fte': 176})
        self.assertEqual(plan['plan_agreed'], 20.0)

    def test_РЕГРЕССИЯ_целевая_конверсия(self):
        # У «Яндекс Регистрации» план считается от фактических дозвонов.
        plan = metrics.plan_for_day(8, {'target_conversion': 0.5}, reached=40)
        self.assertEqual(plan['plan_agreed'], 20.0)

    def test_норма_в_час_сильнее_месячного_плана(self):
        plan = metrics.plan_for_day(6, {'agreed_per_hour': 5, 'plan_per_fte': 440,
                                        'norm_hours_fte': 176})
        self.assertEqual(plan['plan_agreed'], 30.0)


class AnomalyNoiseTests(unittest.TestCase):

    def test_РЕГРЕССИЯ_несопоставленная_строка_не_поднимает_флаг(self):
        """У строки «не сопоставлен» часов нет по определению — флаг «0 часов при
        N лидах» горел бы на ней каждый день и приучил бы не смотреть на аномалии
        вовсе. Про сам пробел раздел сообщает счётчиком в шапке."""
        found = metrics.detect_anomalies([
            {'user_id': 0, 'work_day': YESTERDAY, 'work_hours': 0, 'handled': 41,
             'reached': 20, 'plan_reached': 0},
        ])
        self.assertEqual(found, [])

    def test_у_настоящего_оператора_флаг_остаётся(self):
        found = metrics.detect_anomalies([
            {'user_id': 7, 'work_day': YESTERDAY, 'work_hours': 0, 'handled': 41,
             'reached': 20, 'plan_reached': 0},
        ])
        self.assertIn(metrics.ANOMALY_ZERO_HOURS, [item['kind'] for item in found])


if __name__ == '__main__':
    unittest.main()
