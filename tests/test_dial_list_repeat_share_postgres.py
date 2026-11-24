# -*- coding: utf-8 -*-
"""Доля повторов в пачке «Удаленного КЦ» на настоящем PostgreSQL (владелец, 24.11.2026: «таких повторов до
окончания общего списка в выдаваемых пачках должно быть не больше 20%… чтобы операторы не получали пачками
повторы»).

Что проверяется: значение по умолчанию по профилю и сохранение настройки; «Ещё» кладёт повторов не больше доли,
пока есть водители, с которыми ещё не говорили; других мало — пачка короче; других нет — пачка из повторов;
100 % — как было; свой закреплённый повтор тоже в счёт доли; проверка подписания перед «Ещё» берёт тех же.

Окружение — как у test_dial_list_batch_focus_postgres.py (своя схема, Binotel и Sapar подменены). Запуск:

    DIAL_LIST_TEST_PORT=55493 python -m pytest tests/test_dial_list_repeat_share_postgres.py

Люди и номера выдуманы.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_dial_list_batch_focus_postgres as base  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

pytestmark = base.pytestmark
env = base.env
RCC, TEZ, RCC_OP, GUARDED = base.RCC, base.TEZ, base.RCC_OP, base.GUARDED


def _phones(prefix, n):
    return [f'770{prefix}{i:06d}' for i in range(1, n + 1)]


def _make_repeats(env, phones, days_ago=3):
    """Водители, с которыми уже говорили и которых итог вернул в список: попытки обнулены, дозвон был."""
    env.q("""
        UPDATE dial_list_leads
        SET status = 'in_progress', attempts_total = 0, answered_at = now() - make_interval(days => %s),
            last_attempt_at = now() - make_interval(days => %s)
        WHERE phone_norm = ANY(%s)
    """, (days_ago, days_ago, phones))


def _make_no_answer(env, phones):
    """Недозвоны: звонили, трубку не брали — с ними ещё не говорили."""
    env.q("""
        UPDATE dial_list_leads SET status = 'in_progress', attempts_total = 1,
               last_attempt_at = now() - interval '2 days'
        WHERE phone_norm = ANY(%s)
    """, (phones,))


def _settings(env, **values):
    env.svc.save_department_settings(RCC, values, changed_by=1)


def _issue(env):
    return env.svc.issue_next_portion(RCC_OP, phone_version=GUARDED)["portion"]["items"]


def _repeat_count(env, items):
    ids = [i["lead_id"] for i in items]
    return env.one("SELECT COUNT(*) FROM dial_list_leads WHERE id = ANY(%s::uuid[]) AND answered_at IS NOT NULL",
                   (ids,))[0]


def _finish_portion(env):
    """Оператор отработал пачку: строки закрыты, водители обработаны и в пул не вернутся."""
    env.q("""
        UPDATE dial_list_leads l SET status = 'done'
        FROM dial_list_assignments a WHERE a.lead_id = l.id AND a.state = 'issued'
    """)
    env.q("UPDATE dial_list_assignments SET state = 'done', result = 'answered', done_at = now() "
          "WHERE state = 'issued'")
    env.q("UPDATE dial_list_portions SET closed_at = now() WHERE closed_at IS NULL")


def test_default_by_profile_and_saving(env):
    assert env.svc.department_settings(RCC)["repeat_share_pct"] == 20
    env.q("UPDATE dial_list_department_settings SET profile = 'tez' WHERE department_id = %s", (TEZ,))
    assert env.svc.department_settings(TEZ)["repeat_share_pct"] == 100
    _settings(env, max_attempts=4)                                    # форма без поля — значение не трогается
    assert env.one("SELECT repeat_share_pct FROM dial_list_department_settings WHERE department_id = %s",
                   (RCC,)) == (None,)
    _settings(env, repeat_share_pct=35)
    assert env.svc.department_settings(RCC)["repeat_share_pct"] == 35
    _settings(env, portion_size=50)
    assert env.svc.department_settings(RCC)["repeat_share_pct"] == 35
    _settings(env, repeat_share_pct=0)
    assert env.svc.department_settings(RCC)["repeat_share_pct"] == 0
    assert env.refused(env.svc.save_department_settings, RCC, {"repeat_share_pct": 101}) == (
        "Поле repeat_share_pct: допустимо от 0 до 100", 400)
    assert env.refused(env.svc.save_department_settings, RCC, {"repeat_share_pct": "много"}) == (
        "Поле repeat_share_pct: нужно целое число", 400)
    # Переход на профиль Тез без явной доли — снова по профилю (у Тез без ограничения).
    env.svc.save_department_settings(RCC, {"profile": "tez"}, changed_by=1)
    assert env.svc.department_settings(RCC)["repeat_share_pct"] == 100


def test_portion_keeps_repeats_within_share_while_others_last(env):
    fresh, no_answer, repeats = _phones('1', 6), _phones('2', 6), _phones('3', 10)
    env.upload(RCC, 'База.xlsx', fresh + no_answer + repeats)
    _make_no_answer(env, no_answer)
    _make_repeats(env, repeats)
    _settings(env, portion_size=10, retry_after_hours=24)
    # 20 % от 10 — 2 повтора; остальное — новые, потом недозвоны. Повторы обнулены (попыток 0) и в пуле стоят
    # раньше недозвонов, но в пачку идут только два.
    items = _issue(env)
    assert len(items) == 10
    assert _repeat_count(env, items) == 2
    got = set(env.phones_of(items))
    assert set(fresh) <= got and len(got & set(no_answer)) == 2
    # Порядок пачки — порядок пула: новые (не звонили), затем повторы (0 попыток), затем недозвоны.
    order = [env.one("SELECT phone_norm FROM dial_list_leads WHERE id = %s", (i["lead_id"],))[0] for i in items]
    assert set(order[:6]) == set(fresh)
    assert set(order[6:8]) <= set(repeats) and set(order[8:]) <= set(no_answer)

    # Других осталось 4 недозвона — мест под них 8: пачка короче, повторов 4 * 20 // 80 = 1.
    _finish_portion(env)
    items = _issue(env)
    assert (len(items), _repeat_count(env, items)) == (5, 1)

    # Других нет — общий список кончился: пачка из повторов целиком (осталось 7).
    _finish_portion(env)
    items = _issue(env)
    assert (len(items), _repeat_count(env, items)) == (7, 7)


def test_share_100_is_the_old_order(env):
    others, repeats = _phones('4', 5), _phones('5', 8)
    env.upload(RCC, 'База.xlsx', others + repeats)
    _make_no_answer(env, others)
    _make_repeats(env, repeats)
    _settings(env, portion_size=10, retry_after_hours=24, repeat_share_pct=100)
    items = _issue(env)
    # Как до 24.11.2026: пул по порядку — 8 повторов (0 попыток) и 2 недозвона.
    assert (len(items), _repeat_count(env, items)) == (10, 8)


def test_share_zero_and_own_pinned_repeat(env):
    others, repeats = _phones('6', 12), _phones('7', 3)
    env.upload(RCC, 'База.xlsx', others + repeats)
    _make_no_answer(env, others)
    _make_repeats(env, repeats)
    # Свой закреплённый повтор тоже повтор: в счёт доли, но в своей доле — первым.
    env.q("UPDATE dial_list_leads SET pinned_operator_id = %s WHERE phone_norm = %s", (RCC_OP, repeats[2]))
    _settings(env, portion_size=10, retry_after_hours=24, repeat_share_pct=10)
    items = _issue(env)
    assert (len(items), _repeat_count(env, items)) == (10, 1)
    assert env.phones_of(items[:1]) == [repeats[2]]
    _finish_portion(env)
    _settings(env, repeat_share_pct=0)
    items = _issue(env)
    # 0 % — пока есть другие (осталось 3 недозвона), повторов нет.
    assert (len(items), _repeat_count(env, items)) == (3, 0)


def test_precheck_candidates_match_the_claim(env):
    others, repeats = _phones('8', 9), _phones('9', 6)
    env.upload(RCC, 'База.xlsx', others + repeats)
    _make_no_answer(env, others)
    _make_repeats(env, repeats)
    _settings(env, portion_size=5, retry_after_hours=24)
    ctx = env.svc.operator_context(RCC_OP)
    with env.db._get_cursor() as cur:
        rows, head = env.svc._precheck_candidates(cur, ctx, 5, 10)
    # Те же 5, что возьмёт выдача (повтор один — 20 % от 5), и запас; строки — как у _POOL_CANDIDATES_SQL.
    assert len(head) == 5 and len(rows) == 10
    assert all(len(r) == 5 for r in rows)
    claimed = _issue(env)
    assert {i["lead_id"] for i in claimed} == head
    assert _repeat_count(env, claimed) == 1


class _RecordingSapar:
    """Sapar без сети: никто не подписал, а кого спрашивали — запоминаем."""

    def __init__(self):
        self.asked = []

    def configured(self):
        return True

    def check_now(self, leads, *, timeout, workers, budget_sec, max_systemic, now=None):
        self.asked.append([str(lead_id) for lead_id, _iin, _period in leads])
        return {"checked": len(leads), "signed": [], "processing": [], "errors": 0, "systemic": 0,
                "successes": 0, "stopped": "", "elapsed_sec": 0.0}


def test_sign_precheck_asks_about_the_portion_with_the_share(env):
    others, repeats = _phones('8', 9), _phones('9', 6)
    env.upload(RCC, 'База.xlsx', others + repeats)
    _make_no_answer(env, others)
    _make_repeats(env, repeats)
    # ИИН — выдуманный, из номера: без ИИН Sapar не спрашивают.
    env.q("UPDATE dial_list_leads SET iin = '9' || right(phone_norm, 11) WHERE department_id = %s", (RCC,))
    _settings(env, portion_size=5, retry_after_hours=24)
    sapar = _RecordingSapar()
    env.svc.sign_checker = sapar
    totals = env.svc._precheck_signatures(env.svc.operator_context(RCC_OP))
    assert totals["rounds"] == 1 and len(sapar.asked) == 1
    asked = sapar.asked[0]
    # Пачка 5 и запас 5: доля 20 % от 10 — 2 повтора и 8 недозвонов. Без доли (порядок пула) первыми были бы
    # 6 повторов: попыток у них 0, у недозвонов — 1.
    assert len(asked) == 10
    assert env.one("SELECT COUNT(*) FROM dial_list_leads WHERE id = ANY(%s::uuid[]) AND answered_at IS NOT NULL",
                   (asked,))[0] == 2


def test_log_line_names_repeats(env, caplog):
    env.upload(RCC, 'База.xlsx', _phones('1', 4) + _phones('2', 3))
    _make_repeats(env, _phones('2', 3))
    _settings(env, portion_size=5, retry_after_hours=24)
    with caplog.at_level('INFO', logger='dial_list.service'):
        _issue(env)
    assert any('повторов 1 (доля не больше 20 %)' in r.getMessage() for r in caplog.records)
    assert dial_service.REPEAT_SHARE_DEFAULTS[dial_service.PROFILE_REMOTE_CC] == 20
