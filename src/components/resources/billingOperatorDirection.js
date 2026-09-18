// «Биллинг Oktell» → «Операторы»: срез отчёта на одно направление.
//
// Ручка `oktell_billing_operators` отдаёт обе половины сразу (served_in/served_out,
// talk_in_seconds/talk_out_seconds и так далее), поэтому переключатель «Вход / Исход»
// листает уже загруженный ответ и второй раз в Oktell не ходит. Те же правила на
// сервере — `_oktell_billing_operator_direction_report` в bot_schedule2.py: по ним
// режется выгрузка в Excel. Совпадение ключей сторожит
// tests/test_oktell_billing_operator_direction.py.

export const BILLING_OPERATOR_DIRECTIONS = [
  { key: 'incoming', label: 'Вход' },
  { key: 'outgoing', label: 'Исход' },
];

const num = (value) => {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
};

// Занятость оператора считается по дню ЦЕЛИКОМ, а не по выбранному направлению:
// «Готов» (State 10) и «Перерыв» (State 9) в Oktell направления не имеют, и OCC
// от половины активного времени показывал бы занятость в разы ниже фактической.
const activeSeconds = (item) => num(item.talk_in_seconds) + num(item.talk_out_seconds)
  + num(item.postproc_in_seconds) + num(item.postproc_out_seconds)
  + num(item.hold_in_seconds) + num(item.hold_out_seconds)
  + num(item.dial_in_seconds) + num(item.dial_out_seconds)
  + num(item.dial_other_seconds) + num(item.dial_wait_out_seconds);

export const billingOperatorDirectionRow = (item, direction) => {
  const out = direction === 'outgoing';
  const row = {
    served: num(out ? item.served_out : item.served_in),
    // talk_seconds — разговор (ATT), handle_seconds — всё время обработки (AHT).
    talk_seconds: num(out ? item.call_out_seconds : item.call_in_seconds),
    handle_seconds: num(out ? item.handle_out_seconds : item.handle_in_seconds),
    talk_state_seconds: num(out ? item.talk_out_seconds : item.talk_in_seconds),
    postproc_seconds: num(out ? item.postproc_out_seconds : item.postproc_in_seconds),
    hold_seconds: num(out ? item.hold_out_seconds : item.hold_in_seconds),
    dial_wait_seconds: out ? num(item.dial_wait_out_seconds) : 0,
    active_seconds: activeSeconds(item),
    wait_seconds: num(item.wait_seconds),
    pause_seconds: num(item.pause_seconds),
  };
  if (item.operator !== undefined) row.operator = item.operator;
  return row;
};

const SUM_KEYS = ['served', 'talk_seconds', 'handle_seconds', 'talk_state_seconds', 'postproc_seconds',
  'hold_seconds', 'dial_wait_seconds', 'active_seconds', 'wait_seconds', 'pause_seconds'];

const directionRows = (items, direction) => (items || [])
  .map((item) => billingOperatorDirectionRow(item, direction))
  // Оператор без звонков и без разговоров в этом направлении в таблице не нужен:
  // в «Исходе» иначе висел бы весь входящий состав пустыми строками.
  .filter((row) => row.served > 0 || row.talk_state_seconds > 0)
  .sort((a, b) => (b.served - a.served)
    || (b.talk_state_seconds - a.talk_state_seconds)
    || String(a.operator || '').localeCompare(String(b.operator || ''), 'ru'));

const directionTotals = (rows) => SUM_KEYS.reduce((acc, key) => {
  acc[key] = rows.reduce((sum, row) => sum + num(row[key]), 0);
  return acc;
}, {});

// Отчёт целиком -> тот же отчёт по одному направлению. Итоги дня и периода
// пересчитываются по оставшимся операторам, а не берутся из ответа: иначе в
// «Исходе» они включали бы людей, которых в таблице уже нет.
export const billingOperatorDirectionReport = (report, direction) => {
  if (!report) return report;
  const days = [];
  (report.days || []).forEach((day) => {
    const operators = directionRows(day.operators, direction);
    if (!operators.length) return;
    days.push({ ...day, operators, totals: directionTotals(operators) });
  });
  const operators = directionRows(report.operators, direction);
  return { ...report, days, operators, totals: directionTotals(operators) };
};

export const billingOperatorHasDialWait = (report) => Number(report?.totals?.dial_wait_seconds || 0) > 0;
