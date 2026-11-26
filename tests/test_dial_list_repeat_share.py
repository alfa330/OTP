# -*- coding: utf-8 -*-
"""Доля повторов в пачке «Удаленного КЦ» (владелец, 24.11.2026: «таких повторов до окончания общего списка в
выдаваемых пачках должно быть не больше 20%… чтобы операторы не получали пачками повторы»).

Повтор — водитель, с которым уже говорили (answered_at) и которого итог вернул в список. Пока есть другие
(новые и недозвоны) — повторов в пачке не больше доли; других нет — пачка из повторов. Без базы: чистые функции
и текст запросов. На настоящем Postgres — test_dial_list_repeat_share_postgres.py."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import service as S  # noqa: E402

T0 = datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc)


def row(lead_id, pinned=None, attempts=0, last=None, created=T0):
    return (lead_id, pinned, attempts, last, created)


class RepeatQuotaTests(unittest.TestCase):
    def test_quota_by_share_and_others(self):
        quota = S.repeat_quota
        # Других хватает — 20 % пачки.
        self.assertEqual(quota(100, 100, 20), 20)
        self.assertEqual(quota(100, 80, 20), 20)
        # Других меньше, чем мест под них, — пачка короче, доля та же (строго не больше).
        self.assertEqual(quota(100, 79, 20), 19)
        self.assertEqual(quota(100, 30, 20), 7)
        self.assertEqual(quota(100, 3, 20), 0)
        # Других нет вовсе — общий список кончился: без ограничения.
        self.assertIsNone(quota(100, 0, 20))
        # 100 % — как было до 24.11.2026.
        self.assertIsNone(quota(100, 50, 100))
        # 0 % — пока есть другие, повторов нет.
        self.assertEqual(quota(100, 50, 0), 0)
        self.assertEqual(quota(20, 20, 20), 4)
        # Граница «других ровно на свои места»: доля целиком, а не пересчёт по другим — при крупной доле пересчёт
        # дал бы повторов больше, чем мест (9 при пачке 9 и одном другом).
        self.assertEqual(quota(9, 1, 90), 8)
        self.assertEqual(quota(10, 8, 20), 2)
        # Пересчёт по другим никогда не больше доли от полной пачки.
        for size in range(1, 41):
            for share in range(0, 100, 5):
                full = size * share // 100
                for others in range(1, size + 1):
                    got = quota(size, others, share)
                    self.assertLessEqual(got, full)
                    if got:
                        self.assertLessEqual(got * 100, (min(others, size - got) + got) * share)

    def test_share_defaults_by_profile(self):
        # «Активация водителей» (26.11.2026) — движок Тез, свои сроки перезвона: доля не ограничена.
        self.assertEqual(S.REPEAT_SHARE_DEFAULTS, {S.PROFILE_REMOTE_CC: 20, S.PROFILE_TEZ: 100,
                                                   S.PROFILE_ACTIVATION: 100})
        self.assertEqual(S.repeat_share_of(None, 'activation'), 100)
        self.assertEqual(S.repeat_share_of(None, 'remote_cc'), 20)
        self.assertEqual(S.repeat_share_of(None, 'tez'), 100)
        self.assertEqual(S.repeat_share_of(None, None), 20)
        self.assertEqual(S.repeat_share_of(35, 'tez'), 35)
        self.assertEqual(S.repeat_share_of(0, 'remote_cc'), 0)
        self.assertEqual(S.repeat_share_of(150, 'remote_cc'), 100)


class ComposePortionTests(unittest.TestCase):
    def test_others_fill_and_repeats_are_capped(self):
        others = [row(f'o{i:02d}', attempts=1, last=T0 - timedelta(days=2)) for i in range(100)]
        repeats = [row(f'r{i:02d}', last=T0 - timedelta(days=3)) for i in range(100)]
        chosen = S.compose_portion(others, repeats, 100, 20, 7)
        ids = [r[0] for r in chosen]
        self.assertEqual(len(ids), 100)
        self.assertEqual(sum(i.startswith('r') for i in ids), 20)
        # Первые по порядку пула из каждой выборки: повторы r00…r19, другие o00…o79.
        self.assertEqual(sorted(i for i in ids if i.startswith('r')), [f'r{i:02d}' for i in range(20)])
        self.assertEqual(sorted(i for i in ids if i.startswith('o')), [f'o{i:02d}' for i in range(80)])
        # Порядок пачки — порядок пула: у повторов попыток 0 (итог обнулил), они выше недозвонов.
        self.assertEqual(ids[:20], [f'r{i:02d}' for i in range(20)])

    def test_few_repeats_leave_room_for_others(self):
        others = [row(f'o{i}', attempts=1, last=T0) for i in range(100)]
        chosen = S.compose_portion(others, [row('r0', last=T0)], 100, 20, 7)
        self.assertEqual((len(chosen), sum(r[0].startswith('r') for r in chosen)), (100, 1))

    def test_general_list_over_gives_repeats(self):
        repeats = [row(f'r{i}', last=T0) for i in range(150)]
        self.assertEqual(len(S.compose_portion([], repeats, 100, 20, 7)), 100)
        # Других мало — пачка короче, повторов не больше доли.
        chosen = S.compose_portion([row('o1'), row('o2'), row('o3')], repeats, 100, 20, 7)
        self.assertEqual([r[0] for r in chosen], ['o1', 'o2', 'o3'])
        chosen = S.compose_portion([row(f'o{i}') for i in range(30)], repeats, 100, 20, 7)
        self.assertEqual((len(chosen), sum(r[0].startswith('r') for r in chosen)), (37, 7))

    def test_share_100_is_just_the_pool_order(self):
        # Без ограничения — первые строки пула, как одна выборка: новые (не звонили) раньше повторов.
        fresh = [row(f'n{i}', last=None, created=T0 - timedelta(days=1)) for i in range(10)]
        repeats = [row(f'r{i}', last=T0 - timedelta(days=3)) for i in range(10)]
        chosen = S.compose_portion(fresh, repeats, 10, 100, 7)
        self.assertEqual([r[0] for r in chosen], [f'n{i}' for i in range(10)])
        chosen = S.compose_portion(fresh[:4], repeats, 10, 100, 7)
        self.assertEqual([r[0] for r in chosen], [f'n{i}' for i in range(4)] + [f'r{i}' for i in range(6)])

    def test_order_key_mirrors_pool_sql(self):
        # ORDER BY COALESCE(pinned = оператор) DESC, attempts ASC, last_attempt ASC NULLS FIRST, created ASC.
        rows = [row('late', attempts=0, last=T0, created=T0 - timedelta(days=9)),
                row('never-new', attempts=0, last=None, created=T0),
                row('never-old', attempts=0, last=None, created=T0 - timedelta(days=1)),
                row('two', attempts=2, last=T0 - timedelta(days=5)),
                row('one', attempts=1, last=T0 - timedelta(days=5)),
                row('mine', pinned=7, attempts=2, last=T0),
                row('foreign-pin', pinned=8, attempts=0, last=None)]
        got = [r[0] for r in sorted(rows, key=lambda r: S.pool_order_key(r, 7))]
        self.assertEqual(got, ['mine', 'never-old', 'foreign-pin', 'never-new', 'late', 'one', 'two'])
        self.assertIn("ORDER BY COALESCE(l.pinned_operator_id = %s, FALSE) DESC,\n"
                      "                 l.attempts_total ASC, l.last_attempt_at ASC NULLS FIRST, l.created_at ASC",
                      S.DialListService._POOL_SQL)


class PoolSplitSqlTests(unittest.TestCase):
    def test_split_is_the_pool_with_one_more_condition(self):
        D = S.DialListService
        self.assertEqual(D._POOL_SQL.count('ORDER BY'), 1)
        for answered in ('NULL', 'NOT NULL'):
            sql = D._POOL_SPLIT_SQL.format(lock='FOR UPDATE SKIP LOCKED', extra='', answered=answered)
            self.assertTrue(sql.strip().startswith('SELECT ' + D._POOL_KEY_COLUMNS + '\n'))
            self.assertEqual(sql.count(f'AND l.answered_at IS {answered}\n'), 1)
            # Всё остальное — текст пула выдачи слово в слово, с тем же порядком параметров.
            pool = D._POOL_SQL.format(lock='FOR UPDATE SKIP LOCKED')
            self.assertEqual(sql.replace(f'          AND l.answered_at IS {answered}\n', '')
                             .split('FROM dial_list_leads l', 1)[1], pool.split('FROM dial_list_leads l', 1)[1])
            self.assertEqual(sql.count('%s'), 7)
        pre = D._POOL_SPLIT_SQL.format(lock='', extra=D._PRECHECK_EXTRA_COLUMNS, answered='NULL')
        self.assertIn('SELECT ' + D._POOL_KEY_COLUMNS + ', l.iin, l.period, l.sign_checked_at, l.sign_error\n', pre)
        self.assertNotIn('FOR UPDATE', pre)

    def test_share_off_keeps_the_old_single_query(self):
        D = S.DialListService
        self.assertEqual(D._repeat_share({"settings": {}}), 100)
        self.assertEqual(D._repeat_share({"settings": {"repeat_share_pct": 20}}), 20)


if __name__ == '__main__':
    unittest.main()
