# -*- coding: utf-8 -*-
"""Строка без обработки возвращается в общий список — на настоящем PostgreSQL (владелец, 24.11.2026: «сделай так,
чтобы если водитель в течение 8 часов лежит у кого-то в списке без обработки, возвращались в список»).

Что проверяется: строка, выданная больше 8 ч назад, без звонков за эти 8 ч и без возврата руководителем, закрывается
без результата, водитель снова в пуле и достаётся другому оператору; свежая строка, строка со свежим звонком,
недавно возвращённая руководителем и занятая звонком — остаются; выдача закрывается, когда в ней нечего
обрабатывать; проверка из состояния телефона — не чаще раза в 5 минут, у Тез её нет; такая строка не попадает на
вкладку «Не дозвонились».

Окружение — как у test_dial_list_batch_focus_postgres.py. Запуск:

    DIAL_LIST_TEST_PORT=55493 python -m pytest tests/test_dial_list_idle_release_postgres.py

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
RCC, TEZ, RCC_OP, GUARDED = base.RCC, base.TEZ, base.RCC_OP, base.GUARDED
OTHER_OP = 540


env = base.env


@pytest.fixture
def idle_env(env):
    """Окружение базового модуля плюс второй оператор удалённого КЦ (OTHER_OP) — тот, кому достаются возвращённые."""
    env.q("INSERT INTO users (id, name, login, role, status, department_id, sip_number) "
          "VALUES (%s, 'Второва Вера', 'vera', 'operator', 'working', %s, '903')", (OTHER_OP, RCC))
    svc = env.svc
    plain = svc.operator_context

    def context(uid):
        if int(uid) == OTHER_OP:
            return {"user_id": OTHER_OP, "name": "Второва Вера", "department_id": RCC, "internal_number": '903',
                    "settings": svc.department_settings(RCC)}
        return plain(uid)

    svc.operator_context = context
    return env


def _phones(n, prefix='1'):
    return [f'7707{prefix}{i:06d}' for i in range(1, n + 1)]


def _issue(env, operator=RCC_OP):
    return env.svc.issue_next_portion(operator, phone_version=GUARDED)["portion"]["items"]


def _age_rows(env, operator, hours):
    env.q("UPDATE dial_list_assignments SET created_at = now() - make_interval(hours => %s) "
          "WHERE operator_id = %s AND state = 'issued'", (hours, operator))
    env.q("UPDATE dial_list_portions SET issued_at = now() - make_interval(hours => %s) "
          "WHERE operator_id = %s AND closed_at IS NULL", (hours, operator))


def _open_rows(env, operator):
    return env.one("SELECT COUNT(*) FROM dial_list_assignments WHERE operator_id = %s AND state = 'issued'",
                   (operator,))[0]


def _settings(env, **values):
    env.svc.save_department_settings(RCC, values, changed_by=1)


def test_idle_rows_go_back_and_another_operator_gets_them(idle_env):
    env = idle_env
    env.upload(RCC, 'База.xlsx', _phones(4))
    _settings(env, portion_size=10, retry_after_hours=24)
    assert len(_issue(env)) == 4
    # Свежие (час) — остаются у оператора.
    _age_rows(env, RCC_OP, 1)
    assert env.svc.release_idle_rows(RCC) == 0
    assert _open_rows(env, RCC_OP) == 4
    # 9 часов без единого звонка — все четыре снова в общем списке, выдача закрыта (обрабатывать в ней нечего).
    _age_rows(env, RCC_OP, 9)
    assert env.svc.release_idle_rows(RCC) == 4
    assert _open_rows(env, RCC_OP) == 0
    # Звонков по строкам не было — строк нет вовсе, как будто их не выдавали: «Мой прогресс» их не считает.
    assert env.one("SELECT COUNT(*) FROM dial_list_assignments WHERE operator_id = %s", (RCC_OP,))[0] == 0
    month = env.svc.operator_progress(RCC_OP)["month"]
    assert (month["issued"], month["done"]) == (0, 0)
    assert env.one("SELECT COUNT(*) FROM dial_list_portions WHERE operator_id = %s AND closed_at IS NULL",
                   (RCC_OP,))[0] == 0
    # Водители снова в пуле: их получает другой оператор.
    got = _issue(env, OTHER_OP)
    assert sorted(env.phones_of(got)) == _phones(4)
    # На вкладках прежнего оператора их нет: звонка не было — это не «Не дозвонились».
    worked = env.svc.operator_worked(RCC_OP)
    assert [t["count"] for t in worked["tabs"] if t["key"] == dial_service.WORKED_NO_ANSWER] == [0]
    assert worked["items"] == []


def test_recent_call_recent_requeue_and_locked_row_stay(idle_env):
    env = idle_env
    phones = _phones(4, '2')
    env.upload(RCC, 'База.xlsx', phones)
    _settings(env, portion_size=10, retry_after_hours=24)
    _issue(env)
    _age_rows(env, RCC_OP, 9)
    ids = {p: env.one("SELECT a.id FROM dial_list_assignments a JOIN dial_list_leads l ON l.id = a.lead_id "
                      "WHERE l.phone_norm = %s AND a.state = 'issued'", (p,))[0] for p in phones}
    # Звонили 2 часа назад (отказ АТС — строка осталась в работе): обработка идёт, строка остаётся.
    env.q("INSERT INTO dial_list_attempts (assignment_id, operator_id, state, requested_at) "
          "VALUES (%s, %s, 'failed', now() - interval '2 hours')", (ids[phones[0]], RCC_OP))
    # Звонили 9 часов назад — после этого 8 часов без обработки: возвращается.
    env.q("INSERT INTO dial_list_attempts (assignment_id, operator_id, state, requested_at) "
          "VALUES (%s, %s, 'failed', now() - interval '9 hours')", (ids[phones[1]], RCC_OP))
    # Руководитель вернул водителя в список час назад (строка переоткрыта со старой датой) — остаётся у оператора.
    env.q("INSERT INTO dial_list_lead_events (lead_id, department_id, actor_id, kind, note, created_at) "
          "SELECT id, %s, 1, 'requeue', '', now() - interval '1 hour' FROM dial_list_leads WHERE phone_norm = %s",
          (RCC, phones[2]))
    # Четвёртую строку держит идущий звонок (start_call — FOR UPDATE строки): пропускается, а не ждёт.
    hold = env.db.connect()
    with hold.cursor() as cur:
        cur.execute("SELECT id FROM dial_list_assignments WHERE id = %s FOR UPDATE", (ids[phones[3]],))
        assert env.svc.release_idle_rows(RCC) == 1
    hold.rollback()
    hold.close()
    open_phones = [r[0] for r in env.q(
        "SELECT l.phone_norm FROM dial_list_assignments a JOIN dial_list_leads l ON l.id = a.lead_id "
        "WHERE a.operator_id = %s AND a.state = 'issued' ORDER BY 1", (RCC_OP,))]
    assert open_phones == sorted([phones[0], phones[2], phones[3]])
    # Звонок кончился — четвёртая тоже возвращается; строка со звонком «Ошибка АТС» (звонили 9 ч назад) закрыта,
    # а не удалена: звонок остаётся в истории, строка — на «Не дозвонились».
    assert env.svc.release_idle_rows(RCC) == 1
    assert env.one("SELECT state, result FROM dial_list_assignments WHERE id = %s", (ids[phones[1]],)) == (
        'done', 'other')
    assert env.one("SELECT COUNT(*) FROM dial_list_assignments WHERE id = %s", (ids[phones[3]],))[0] == 0
    worked = env.svc.operator_worked(RCC_OP)
    assert [i["lead_id"] for i in worked["items"]] == [env.lead(phones[1])]


def test_previous_base_rows_are_left_alone(idle_env):
    env = idle_env
    # Строка водителя прошлой базы месяца (телефон её не показывает, _dial_base) — правило её не трогает.
    prev = (env.month.replace(day=1) - dial_service.timedelta(days=1)).replace(day=1)
    env.upload(RCC, 'Прошлая база.xlsx', _phones(1, '5'), period=prev)
    lead = env.lead(_phones(1, '5')[0])
    portion = env.one("INSERT INTO dial_list_portions (operator_id, department_id, size, issued_at) "
                      "VALUES (%s, %s, 1, now() - interval '20 days') RETURNING id", (RCC_OP, RCC))[0]
    env.q("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, created_at) "
          "VALUES (%s, %s, %s, 1, now() - interval '20 days')", (portion, lead, RCC_OP))
    assert env.svc.release_idle_rows(RCC) == 0
    assert _open_rows(env, RCC_OP) == 1


def test_sweep_from_phone_state_is_throttled_and_remote_cc_only(idle_env):
    env = idle_env
    env.upload(RCC, 'База.xlsx', _phones(2, '3'))
    _settings(env, portion_size=10, retry_after_hours=24)
    _issue(env)
    _age_rows(env, RCC_OP, 9)
    # «Ещё» только что проверял отдел — состояние телефона в эти 5 минут его не проверяет.
    env.svc.get_state(OTHER_OP, phone_version=GUARDED)
    assert _open_rows(env, RCC_OP) == 2
    # Прошло 5 минут — состояние телефона другого оператора проверяет отдел, строки вернулись.
    env.svc._idle_sweep_at[RCC] -= dial_service.IDLE_ROW_SWEEP_SEC + 1
    state = env.svc.get_state(OTHER_OP, phone_version=GUARDED)
    assert _open_rows(env, RCC_OP) == 0
    assert state["pool_available"] == 2
    # Сразу снова — не проверяем (раз в IDLE_ROW_SWEEP_SEC); «Ещё» проверяет всегда.
    _issue(env, OTHER_OP)
    _age_rows(env, OTHER_OP, 9)
    ctx = env.svc.operator_context(RCC_OP)
    assert env.svc._release_idle_rows_for(ctx) == 0
    assert _open_rows(env, OTHER_OP) == 2
    assert env.svc._release_idle_rows_for(ctx, force=True) == 2
    # У Тез проверки нет вовсе.
    tez_ctx = {"department_id": TEZ, "settings": {"profile": "tez"}}
    assert env.svc._release_idle_rows_for(tez_ctx, force=True) == 0
    assert dial_service.IDLE_ROW_RELEASE_HOURS == 8 and dial_service.IDLE_ROW_SWEEP_SEC == 300


def test_next_portion_releases_idle_rows_first(idle_env):
    env = idle_env
    env.upload(RCC, 'База.xlsx', _phones(3, '4'))
    _settings(env, portion_size=10, retry_after_hours=24)
    _issue(env)
    _age_rows(env, RCC_OP, 9)
    # Пул пуст, пока строки у первого оператора; «Ещё» второго сначала возвращает их — и выдаёт ему.
    got = _issue(env, OTHER_OP)
    assert sorted(env.phones_of(got)) == _phones(3, '4')


def test_tez_rows_are_left_alone(idle_env):
    env = idle_env
    # Автообзвон Тез — свои сроки перезвона оператора: правило 8 часов его строки не трогает.
    env.q("UPDATE dial_list_department_settings SET profile = 'tez' WHERE department_id = %s", (TEZ,))
    env.upload(TEZ, 'База Тез.xlsx', _phones(2, '6'))
    env.svc.issue_next_portion(base.TEZ_OP, phone_version=GUARDED)
    _age_rows(env, base.TEZ_OP, 9)
    ctx = env.svc.operator_context(base.TEZ_OP)
    assert ctx["settings"]["profile"] == 'tez'
    assert env.svc._release_idle_rows_for(ctx, force=True) == 0
    assert _open_rows(env, base.TEZ_OP) == 2


def test_row_taken_away_without_a_call_is_not_a_no_answer(idle_env):
    env = idle_env
    # Строку без единого звонка закрыли без результата (отдали другому — закрепление): на «Не дозвонились» её нет.
    env.upload(RCC, 'База.xlsx', _phones(1, '7'))
    _settings(env, portion_size=10, retry_after_hours=24)
    _issue(env)
    env.q("UPDATE dial_list_assignments SET state = 'done', result = 'other', done_at = now() "
          "WHERE operator_id = %s AND state = 'issued'", (RCC_OP,))
    worked = env.svc.operator_worked(RCC_OP)
    assert worked["items"] == []
