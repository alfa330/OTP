# -*- coding: utf-8 -*-
"""«Аналитика» удалённого КЦ на настоящем PostgreSQL: «ещё доступно для обзвона» у плитки «Обзвонили» (владелец,
24.11.2026: «тех, по которым ещё не производился звонок и ещё не подписали, т.е. тех, кого ещё можно обзвонить, потом
ждут повтора») и «подписали сами» до и после загрузки базы у плитки «Успешки».

Окружение — как у test_dial_list_batch_focus_postgres.py. Запуск:

    DIAL_LIST_TEST_PORT=55493 python -m pytest tests/test_dial_list_analytics_available_postgres.py

Люди и номера выдуманы.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_dial_list_batch_focus_postgres as base  # noqa: E402
from dial_list import analytics as dial_analytics  # noqa: E402

pytestmark = base.pytestmark
env = base.env
RCC, RCC_OP = base.RCC, base.RCC_OP

PH = {k: f'7708{i:07d}' for i, k in enumerate(
    ('fresh', 'cancelled_only', 'no_answer', 'callback', 'in_list_fresh', 'signed_self', 'signed_before',
     'signed_success', 'processing', 'excluded', 'refused', 'exhausted'), start=1)}


def _call(env, phone, disposition='NOANSWER', billsec=0, cancelled=False, state='done'):
    """Строка выдачи водителя с одной попыткой (как после звонка)."""
    lead = env.lead(phone)
    portion = env.one("INSERT INTO dial_list_portions (operator_id, department_id, size) VALUES (%s, %s, 1) "
                      "RETURNING id", (RCC_OP, RCC))[0]
    asg = env.one("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state) "
                  "VALUES (%s, %s, %s, 1, %s) RETURNING id", (portion, lead, RCC_OP, state))[0]
    if disposition is not None:
        env.q("INSERT INTO dial_list_attempts (assignment_id, operator_id, state, disposition, billsec, cancelled) "
              "VALUES (%s, %s, 'finished', %s, %s, %s)", (asg, RCC_OP, disposition, billsec, cancelled))
    return lead


def test_available_to_call_and_self_signed_split(env):
    env.upload(RCC, 'База.xlsx', list(PH.values()))
    lead = {k: env.lead(p) for k, p in PH.items()}
    # Не звонили: свежий; отменённая оператором до ответа попытка — не звонок (как «Обзвонили»); у оператора в
    # списке, но звонка ещё не было.
    _call(env, PH['cancelled_only'], disposition='CANCEL', cancelled=True)
    _call(env, PH['in_list_fresh'], disposition=None, state='issued')
    # Ждут повтора: не взял трубку (попытка 1 из 3); поговорили, итог «перезвонить» вернул в список (попытки с нуля).
    _call(env, PH['no_answer'])
    env.q("UPDATE dial_list_leads SET status = 'in_progress', attempts_total = 1 WHERE id = %s", (lead['no_answer'],))
    _call(env, PH['callback'], disposition='ANSWER', billsec=40)
    env.q("UPDATE dial_list_leads SET status = 'in_progress', attempts_total = 0, answered_at = now() WHERE id = %s",
          (lead['callback'],))
    # Не доступны: подписали (сами после загрузки, сами до загрузки, успешка), на проверке у Яндекса, исключён,
    # обработан (отказ), попытки кончились.
    env.q("UPDATE dial_list_leads SET signed_at = now(), success_resolved_at = now() WHERE id = %s",
          (lead['signed_self'],))
    env.q("UPDATE dial_list_leads SET signed_at = created_at - interval '2 days', success_resolved_at = now() "
          "WHERE id = %s", (lead['signed_before'],))
    att = _call(env, PH['signed_success'], disposition='ANSWER', billsec=60)
    env.q("UPDATE dial_list_leads SET signed_at = now(), success_resolved_at = now(), success_operator_id = %s, "
          "success_attempt_id = (SELECT t.id FROM dial_list_attempts t JOIN dial_list_assignments a "
          "ON a.id = t.assignment_id WHERE a.lead_id = %s) WHERE id = %s", (RCC_OP, att, att))
    env.q("UPDATE dial_list_leads SET sign_status = 'processing' WHERE id = %s", (lead['processing'],))
    env.q("UPDATE dial_list_leads SET status = 'excluded' WHERE id = %s", (lead['excluded'],))
    _call(env, PH['refused'], disposition='ANSWER', billsec=30)
    env.q("UPDATE dial_list_leads SET status = 'done', answered_at = now() WHERE id = %s", (lead['refused'],))
    _call(env, PH['exhausted'])
    env.q("UPDATE dial_list_leads SET status = 'in_progress', attempts_total = 3 WHERE id = %s", (lead['exhausted'],))

    with env.db._get_cursor() as cur:
        t = dial_analytics.department_analytics(cur, RCC, env.month, configured=True, max_attempts=3)['totals']
    assert (t['open_new'], t['open_retry']) == (3, 2)
    # Обзвонили — хоть одна неотменённая попытка: не взял трубку, перезвон, успешка, отказ, попытки кончились.
    assert (t['leads'], t['called']) == (12, 5)
    assert (t['signed'], t['successes'], t['self_signed'], t['signed_before_upload']) == (3, 1, 2, 1)
    # Предел попыток отдела выше — «попытки кончились» снова доступен.
    with env.db._get_cursor() as cur:
        t = dial_analytics.department_analytics(cur, RCC, env.month, configured=True, max_attempts=4)['totals']
    assert (t['open_new'], t['open_retry']) == (3, 3)
    # Ручка раздела отдаёт то же — с пределом попыток отдела (по умолчанию 3).
    data = env.svc.analytics(RCC)
    assert (data['totals']['open_new'], data['totals']['open_retry'], data['is_active_period']) == (3, 2, True)
