// «Воронка ОП Tez», вкладка «Успешки» (03.11.2026): чистая логика сайта
// (src/components/dial_list/tezSuccesses.js) — плитки воронки и плана, календарь месяца,
// форматы дат, проверка формы плана и тексты итогов действий.
//
// Запуск: node --test tests/dial_list_tez_successes.test.mjs

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    STATUS_FILTER_OPTIONS, STATUS_LABELS, STATUS_TONES, batchActionSummary, batchCheckPending, batchCheckText,
    batchEventText, batchReasonError, calendarCells, calendarTone, closureTone, currentMonthKey, dayTitle, exportFileName, fmtClock,
    fmtDay, fmtNumber, fmtSeconds, fmtWhen, funnelTiles, monthParts, num, pctText, planFormError, planNote,
    planPayload, planTiles, plural, recomputeSummary, ruleLabel,
} from '../src/components/dial_list/tezSuccesses.js';

const NF = new Intl.NumberFormat('ru-RU');

test('статусы: подпись и тон у каждого, отбор начинается со «Все статусы»', () => {
    assert.deepEqual(Object.keys(STATUS_TONES).sort(), Object.keys(STATUS_LABELS).sort());
    assert.equal(STATUS_TONES.success, 'green');
    assert.equal(STATUS_TONES.not_counted, 'red');
    assert.equal(STATUS_TONES.already_working, 'amber');
    // Нейтральное не красим: «Новый» и «В работе» — серые.
    assert.equal(STATUS_TONES.new, 'slate');
    assert.equal(STATUS_TONES.in_progress, 'slate');
    assert.deepEqual(STATUS_FILTER_OPTIONS[0], { value: '', label: 'Все статусы' });
    assert.equal(STATUS_FILTER_OPTIONS.length, Object.keys(STATUS_LABELS).length + 1);
    assert.equal(ruleLabel('reactivated_30d'), 'Не работал 30+ дней и вернулся после звонка');
    assert.equal(ruleLabel('нет такого'), '');
});

test('даты: момент со смещением — по Алматы, без смещения — как записан', () => {
    // 09:05 UTC = 14:05 Алматы (UTC+5).
    assert.equal(fmtWhen('2026-10-15T09:05:00+00:00'), '15.10, 14:05');
    assert.equal(fmtWhen('2026-10-15T09:05:00Z'), '15.10, 14:05');
    // Сервер уже перевёл (AT TIME ZONE 'Asia/Almaty') — смещения нет, часовой пояс браузера не трогаем.
    assert.equal(fmtWhen('2026-10-15T14:05:33'), '15.10, 14:05');
    assert.equal(fmtWhen('2026-10-15 14:05'), '15.10, 14:05');
    assert.equal(fmtWhen(null), '—');
    assert.equal(fmtClock('2026-10-15T14:05:33'), '14:05');
    assert.equal(fmtClock(''), '—');
    assert.equal(fmtDay('2026-06-24'), '24.06');
    assert.equal(fmtDay(''), '');
    assert.equal(fmtSeconds(9.6), '10');
    assert.equal(fmtSeconds(null), '—');
    assert.equal(dayTitle('2026-11-03'), '3 ноября, Вт');
    assert.equal(dayTitle('2026-11-01'), '1 ноября, Вс');
    assert.equal(dayTitle(''), '');
});

test('числа: разряды и запятая по-русски', () => {
    assert.equal(num(7195), NF.format(7195));
    assert.equal(num(undefined), '0');
    assert.equal(fmtNumber(5.5), '5,5');
    assert.equal(fmtNumber(null), '—');
    assert.equal(fmtNumber('abc'), '—');
    assert.equal(pctText(3.5), '3,5%');
    assert.equal(pctText(undefined), '0%');
    const leads = (n) => plural(n, 'лид', 'лида', 'лидов');
    assert.deepEqual([0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 111, 101].map(leads),
        ['лидов', 'лид', 'лида', 'лида', 'лидов', 'лидов', 'лидов', 'лидов', 'лид', 'лида', 'лидов', 'лидов', 'лид']);
});

test('месяц: ключ, части и имя файла выгрузки как у сервера', () => {
    assert.equal(currentMonthKey(new Date(2026, 0, 31)), '2026-01');
    assert.deepEqual(monthParts('2026-11'), { year: 2026, month: 11 });
    assert.equal(monthParts('2026-13'), null);
    assert.equal(monthParts(''), null);
    assert.equal(exportFileName(2026, 9), 'tez_leads_2026_09.xlsx');
});

test('календарь: неделя с понедельника, каждый день месяца, число успешек по дате', () => {
    // Ноябрь 2026 начинается в воскресенье: шесть пустых клеток перед 1-м.
    const cells = calendarCells(2026, 11, [{ date: '2026-11-03', successes: 4 }, { date: '2026-11-30', successes: '2' }]);
    assert.equal(cells.filter((c) => c === null).length, 6);
    const days = cells.filter(Boolean);
    assert.equal(days.length, 30);
    assert.deepEqual(days[0], { day: 1, date: '2026-11-01', successes: 0 });
    assert.equal(days.find((c) => c.date === '2026-11-03').successes, 4);
    assert.equal(days.find((c) => c.date === '2026-11-30').successes, 2);
    // Февраль невисокосного года и понедельник первым днём — без пустых клеток.
    assert.equal(calendarCells(2027, 2, []).length, 28);
    assert.equal(calendarCells(2027, 2, null)[0].day, 1);
});

test('календарь: пустой день не красится, дальше три ступени от пикового', () => {
    assert.equal(calendarTone(0, 10), 'empty');
    assert.equal(calendarTone(3, 10), 'low');
    assert.equal(calendarTone(5, 10), 'mid');
    assert.equal(calendarTone(10, 10), 'high');
});

test('воронка: пять плиток, окно звонков называет даты, перенос — только когда он есть', () => {
    const funnel = {
        leads_total: 7195, duplicates: 12, dialed: 5187, attempts: 14028, reached: 3275, went_online: 320,
        active_prev_month: 40, already_working: 61, successes: 202, carried_in: 0, carried_out: 6993,
        workable: 7134, conversion: 2.8, conversion_all: 2.8, not_counted: 15,
        call_window_start: '2026-06-24', call_window_end: '2026-07-31',
    };
    const tiles = funnelTiles(funnel);
    assert.deepEqual(tiles.map((t) => t.label), ['Загружено лидов', 'Обзвонено', 'Дозвонились', 'Заказ в этом месяце', 'Успешки']);
    assert.equal(tiles[0].value, NF.format(7195));
    assert.equal(tiles[0].hint, 'дублей при загрузке: 12');
    assert.equal(tiles[1].hint, `попыток: ${NF.format(14028)}`);
    assert.match(tiles[1].help, /за 24\.06 — 31\.07/);
    assert.equal(tiles[3].hint, 'работали и в прошлом: 40');
    assert.match(tiles[3].help, /«уже работающих».*61/);
    const success = tiles[4];
    assert.equal(success.tone, 'good');
    assert.equal(success.hint, 'конверсия 2,8%');
    assert.doesNotMatch(success.help, /дали лиды прошлого месяца/);
    assert.match(success.help, /дорабатываются в следующем: 6/);

    const carried = funnelTiles({ ...funnel, carried_in: 11, carried_out: 0 })[4];
    assert.equal(carried.hint, 'конверсия 2,8% · из них переносом 11');
    assert.match(carried.help, /Из них 11 дали лиды прошлого месяца/);
    assert.doesNotMatch(carried.help, /дорабатываются в следующем/);

    // Сервер без окна звонков и пустая воронка: без «за  — », без зелёного у нуля.
    const empty = funnelTiles(null);
    assert.doesNotMatch(empty[1].help, /Учитываются исходящие/);
    assert.equal(empty[4].tone, null);
    assert.equal(empty[4].value, '0');
});

test('план: заданное всегда, сводка — только когда отдел её посчитал', () => {
    const plan = { plan_per_fte: 80, norm_hours_fte: 168 };
    assert.deepEqual(planTiles(plan, null).map((t) => [t.label, t.value]), [['План на 1 FTE', '80'], ['Норма часов на 1 FTE', '168']]);
    assert.equal(planTiles({ plan_per_fte: 0, norm_hours_fte: 176 }, null)[0].value, '—');
    const summary = {
        plan_per_fte: 80, fte_total: 5.5, coefficient: 0.8, plan_total: 352, actual_hours: 612.25,
        norm_hours_fte: 168, actual_fte: 3.6443, actual_plan: 233.24, successes_total: 120, closure_pct: 51.4,
    };
    const tiles = planTiles(plan, summary);
    assert.deepEqual(tiles.map((t) => t.label),
        ['План на 1 FTE', 'Норма часов на 1 FTE', 'Групповой план', 'Фактический план', 'Факт продаж', 'Выполнение']);
    assert.equal(tiles[2].value, '352');
    assert.match(tiles[2].help, /\(80\) × FTE на 1 число \(5,5\) × 0,8/);
    assert.match(tiles[3].help, /612,25 ч ÷ 168 ч на 1 FTE = 3,6443 FTE/);
    assert.equal(tiles[4].value, '120');
    assert.equal(tiles[5].value, '51,4%');
    assert.equal(tiles[5].tone, 'bad');
    assert.equal(planTiles(plan, { ...summary, closure_pct: null })[5].value, '—');
});

test('план: тон выполнения и оговорка о том, чего не хватает', () => {
    assert.equal(closureTone(100), 'good');
    assert.equal(closureTone(60), 'warn');
    assert.equal(closureTone(59.9), 'bad');
    assert.equal(closureTone(null), null);
    assert.equal(planNote(null), '');
    assert.equal(planNote({ fte_total: null }), 'FTE будет зафиксирован при наступлении месяца.');
    assert.equal(planNote({ fte_total: 0, plan_per_fte: 80 }), 'На 1 число в отделе нет FTE — фактический план не рассчитывается.');
    assert.equal(planNote({ fte_total: 5, plan_per_fte: 0 }), 'План на 1 FTE за этот месяц не задан.');
    assert.equal(planNote({ fte_total: 5, plan_per_fte: 80 }), '');
});

test('форма плана: пределы сервера, запятая как точка', () => {
    assert.equal(planFormError('80', '168'), '');
    assert.equal(planFormError('0', '0,5'), '');
    assert.equal(planFormError('12,5', '744'), '');
    assert.match(planFormError('', '168'), /План на 1 FTE/);
    assert.match(planFormError('-1', '168'), /План на 1 FTE/);
    assert.match(planFormError('abc', '168'), /План на 1 FTE/);
    assert.match(planFormError('80', '0'), /Норма часов/);
    assert.match(planFormError('80', '745'), /Норма часов/);
    assert.match(planFormError('80', ''), /Норма часов/);
    assert.deepEqual(planPayload('560', 2026, 11, '12,5', '168'),
        { department_id: 560, year: 2026, month: 11, plan_per_fte: 12.5, norm_hours_fte: 168 });
});

test('сверка: тост сходится с плиткой «Успешки» и называет недокачанные дни', () => {
    assert.equal(recomputeSummary({ outcomes: { success: 10, carried_success: 0, already_working: 3 } }),
        'Сверка выполнена: успешек 10, уже работающих 3');
    assert.equal(
        recomputeSummary({ outcomes: { success: 10, carried_success: 4, already_working: 3 }, calls_mirror: { days_left: 2 } }),
        'Сверка выполнена: успешек 14 (из них 4 переносом с прошлого месяца), уже работающих 3. '
        + 'Звонки за 2 дн. ещё не выгружены — докачаются ночью или следующей сверкой',
    );
    assert.equal(recomputeSummary(null), 'Сверка выполнена: успешек 0, уже работающих 0');
});

test('загрузки: причина удаления обязательна, проверка «уже работающих» и итоги действий', () => {
    assert.equal(batchReasonError('delete', '  '), 'Укажите причину удаления');
    assert.equal(batchReasonError('delete', 'не тот файл'), '');
    assert.equal(batchReasonError('restore', ''), '');
    assert.match(batchReasonError('restore', 'x'.repeat(2001)), /2000/);
    assert.equal(batchCheckPending({ check_status: 'pending' }), true);
    assert.equal(batchCheckPending({ check_status: 'running' }), true);
    // Удалённую загрузку больше не ждём: её проверка никому не нужна.
    assert.equal(batchCheckPending({ check_status: 'pending', is_deleted: true }), false);
    assert.equal(batchCheckPending({ check_status: 'done' }), false);
    assert.equal(batchCheckPending(null), false);
    assert.equal(batchCheckText({ check_status: 'done', already_working: 5 }), 'уже работают 5');
    assert.equal(batchCheckText({ check_status: 'error' }), 'ошибка проверки');
    assert.equal(batchCheckText({ check_status: 'pending' }), 'проверка…');
    assert.equal(
        batchActionSummary('delete', { file_name: 'база.xlsx' }, { leads_removed: 5, successes_removed: 1, leads_kept: 2 }),
        'База «база.xlsx» удалена: лидов убрано 5, успешек снято 1, оставлено (есть в других загрузках) 2. Вернуть можно кнопкой «Восстановить».',
    );
    assert.equal(
        batchActionSummary('restore', { file_name: 'база.xlsx' }, { leads_restored: 5, successes_restored: 1, leads_merged: 0 }),
        'База «база.xlsx» восстановлена: лидов 5, успешек 1. Нажмите «Сверить сейчас», чтобы пересчитать успешки.',
    );
    assert.match(batchActionSummary('restore', {}, { leads_merged: 3 }), /слито с уже существующими 3/);
    assert.equal(batchEventText({ action: 'delete', leads_removed: 5, leads_kept: 2, successes_removed: 1 }),
        'лидов убрано 5, оставлено 2, успешек 1');
    assert.equal(batchEventText({ action: 'restore', leads_restored: 5, successes_restored: 1, leads_merged: 0 }),
        'лидов возвращено 5, успешек 1');
    assert.equal(batchEventText({ action: 'restore', leads_restored: 5, successes_restored: 1, leads_merged: 2 }),
        'лидов возвращено 5, успешек 1, слито 2');
});
