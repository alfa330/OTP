# -*- coding: utf-8 -*-
"""Итог «перезвонить» в счёт попыток на настоящем PostgreSQL (владелец, 24.11.2026: «Сброс тоже нужно перевести на 3
попытки»). Итог с флагом requeue_counts возвращает водителя в список, но попытки не обнуляет: третья попытка — и водитель
обработан, ему больше не звонят. Без флага («Перезвонить позже») — как было: попытки с нуля, возврат без конца.

Порядок «итог раньше исхода АТС» и «позже» равноправен. Окружение — как у test_dial_list_batch_focus_postgres.py. Запуск:

    DIAL_LIST_TEST_PORT=55493 python -m pytest tests/test_dial_list_requeue_counts_postgres.py

Люди и номера выдуманы.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_dial_list_batch_focus_postgres as base  # noqa: E402

pytestmark = base.pytestmark
env = base.env
RCC, RCC_OP = base.RCC, base.RCC_OP


@pytest.fixture
def counted(env):
    env.q("INSERT INTO dial_list_outcomes (department_id, name, position, requeue, requeue_counts) "
          "VALUES (%s, 'Сброс', 3, TRUE, TRUE)", (RCC,))
    ids = {r[0]: str(r[1]) for r in env.q("SELECT name, id FROM dial_list_outcomes WHERE department_id = %s", (RCC,))}
    return env, ids


def _talk(env, phone, outcome_id, outcome_first):
    """Один разговор оператора с водителем: строка выдачи, плечо, разговор; итог — до исхода АТС или после."""
    lead = env.lead(phone)
    portion = env.one("INSERT INTO dial_list_portions (operator_id, department_id, size) VALUES (%s, %s, 1) "
                      "RETURNING id", (RCC_OP, RCC))[0]
    asg = env.one("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position) "
                  "VALUES (%s, %s, %s, 1) RETURNING id", (portion, lead, RCC_OP))[0]
    att = str(env.one("INSERT INTO dial_list_attempts (assignment_id, operator_id, state, leg_answered_at, "
                      "talk_detected, phone_ended_at) VALUES (%s, %s, 'ended', now(), TRUE, now()) RETURNING id",
                      (asg, RCC_OP))[0])
    if outcome_first:
        env.svc.set_attempt_outcome(RCC_OP, att, outcome_id)
        env.svc._finish_attempt(att, 'ANSWER', 35, 4, 'webhook')
    else:
        env.svc._finish_attempt(att, 'ANSWER', 35, 4, 'webhook')
        env.svc.set_attempt_outcome(RCC_OP, att, outcome_id)
    return env.one("SELECT status, attempts_total FROM dial_list_leads WHERE id = %s", (lead,))


@pytest.mark.parametrize("outcome_first", [False, True], ids=["итог после исхода АТС", "итог до исхода АТС"])
def test_counted_requeue_ends_after_three(counted, outcome_first):
    env, ids = counted
    phone = '77090000001'
    env.upload(RCC, 'База.xlsx', [phone])
    # Каждый «Сброс» — попытка: водитель возвращается, пока попытки не кончились (3 у отдела по умолчанию).
    assert _talk(env, phone, ids['Сброс'], outcome_first) == ('in_progress', 1)
    assert _talk(env, phone, ids['Сброс'], outcome_first) == ('in_progress', 2)
    assert _talk(env, phone, ids['Сброс'], outcome_first) == ('done', 3)


@pytest.mark.parametrize("outcome_first", [False, True], ids=["итог после исхода АТС", "итог до исхода АТС"])
def test_plain_requeue_still_starts_over(counted, outcome_first):
    env, ids = counted
    phone = '77090000002'
    env.upload(RCC, 'База.xlsx', [phone])
    for _ in range(4):
        assert _talk(env, phone, ids['Перезвонить позже'], outcome_first) == ('in_progress', 0)


def test_counted_requeue_after_no_answers_and_other_limit(counted):
    env, ids = counted
    phone = '77090000003'
    env.upload(RCC, 'База.xlsx', [phone])
    # Два недозвона до разговора — «Сброс» третьей попыткой заканчивает обзвон.
    env.q("UPDATE dial_list_leads SET status = 'in_progress', attempts_total = 2 WHERE phone_norm = %s", (phone,))
    assert _talk(env, phone, ids['Сброс'], False) == ('done', 3)
    # Предел попыток отдела выше — тот же «Сброс» ещё возвращает.
    phone2 = '77090000004'
    env.upload(RCC, 'База.xlsx', [phone2])
    env.svc.save_department_settings(RCC, {"max_attempts": 5}, changed_by=1)
    env.q("UPDATE dial_list_leads SET status = 'in_progress', attempts_total = 2 WHERE phone_norm = %s", (phone2,))
    assert _talk(env, phone2, ids['Сброс'], True) == ('in_progress', 3)


def test_outcome_editor_keeps_and_sets_the_flag(counted):
    env, ids = counted
    rows, version = env.svc.outcomes(RCC, with_version=True)
    flags = {o["name"]: (o["requeue"], o["requeue_counts"]) for o in rows}
    assert flags == {'Отказ': (False, False), 'Перезвонить позже': (True, False), 'Сброс': (True, True)}
    # Сборка сайта без поля — флаг не трогается.
    plain = [{k: o[k] for k in ("id", "name", "color", "requeue", "is_active")} for o in rows]
    rows2, version2 = env.svc.save_outcomes(RCC, plain, changed_by=1, version=version, with_version=True)
    assert {o["name"]: o["requeue_counts"] for o in rows2}['Сброс'] is True
    assert version2 == version
    # Новая сборка — снимает и ставит; версия справочника меняется.
    edited = [dict(o, requeue_counts=(o["name"] == 'Перезвонить позже')) for o in plain]
    rows3, version3 = env.svc.save_outcomes(RCC, edited, changed_by=1, version=version2, with_version=True)
    assert {o["name"]: o["requeue_counts"] for o in rows3} == {'Отказ': False, 'Перезвонить позже': True,
                                                                'Сброс': False}
    assert version3 != version2
    # Новый итог с флагом.
    rows4 = env.svc.save_outcomes(RCC, edited + [{"name": 'Бросил трубку', "color": '#FF9500', "requeue": True,
                                                  "requeue_counts": True, "is_active": True}], changed_by=1)
    assert {o["name"]: o["requeue_counts"] for o in rows4}['Бросил трубку'] is True
