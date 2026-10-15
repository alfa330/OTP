/*
 * Раздел «Удаленный КЦ»: кнопка «Детализация» — звонки отдела за период в Excel (владелец,
 * 15.10.2026). Правила кнопки — в src/components/dial_list/callsExport.js (без React), файл
 * собирает сервер (dial_list/calls_export.py).
 *
 * Что защищаем:
 *   • потолок периода и имя файла совпадают с сервером: период, который кнопка пропустила,
 *     сервер не отклонит, а имя скачанного файла (его ставит сайт) — то же, что у сервера;
 *   • пресеты дают ровно те дни, что написаны на кнопках, и не уходят в будущее;
 *   • число дней считается по календарю, а не по часам (перевод часов его не сдвигает);
 *   • кнопка стоит в шапке раздела и ходит в свою ручку отдела.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    EXPORT_MAX_DAYS, exportFileName, exportPresets, isoDay, maxDaysLabel, rangeDays,
} from '../src/components/dial_list/callsExport.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').split('\r\n').join('\n');

test('потолок периода — тот же, что у сервера', () => {
    const server = read('../dial_list/calls_export.py');
    const match = server.match(/^MAX_DAYS = (\d+)$/m);
    assert.ok(match, 'MAX_DAYS не найден в dial_list/calls_export.py');
    assert.equal(EXPORT_MAX_DAYS, Number(match[1]));
    assert.equal(EXPORT_MAX_DAYS, 31);
    assert.equal(maxDaysLabel(), '31 дня');
    assert.equal(maxDaysLabel(7), '7 дней');
    assert.equal(maxDaysLabel(11), '11 дней');
    assert.equal(maxDaysLabel(21), '21 дня');
});

test('имя файла — как у сервера', () => {
    assert.equal(exportFileName('2026-10-01', '2026-10-15'), 'Детализация звонков 01.10.2026 — 15.10.2026.xlsx');
    assert.equal(exportFileName('2026-10-15', '2026-10-15'), 'Детализация звонков 15.10.2026.xlsx');
    const server = read('../dial_list/calls_export.py');
    // Сервер: f"Детализация звонков {period_label(...)}.xlsx", период — «%d.%m.%Y — %d.%m.%Y».
    assert.match(server, /return f"Детализация звонков \{period_label\(day_from, day_to\)\}\.xlsx"/);
    assert.match(server, /return f"\{day_from:%d\.%m\.%Y\} — \{day_to:%d\.%m\.%Y\}"/);
});

test('число дней — по календарю, обе границы включительно', () => {
    assert.equal(rangeDays('2026-10-01', '2026-10-01'), 1);
    assert.equal(rangeDays('2026-10-01', '2026-10-31'), 31);
    assert.equal(rangeDays('2026-09-01', '2026-10-01'), 31);
    assert.equal(rangeDays('2026-09-01', '2026-10-02'), 32);
    // Через переход на летнее время в Европе (29.03.2026) — сутки не теряются.
    assert.equal(rangeDays('2026-03-28', '2026-03-30'), 3);
    assert.equal(rangeDays('', '2026-10-01'), 0);
    // Февраль високосного года и смена года.
    assert.equal(rangeDays('2028-02-01', '2028-02-29'), 29);
    assert.equal(rangeDays('2026-12-31', '2027-01-01'), 2);
});

test('пресеты: сегодня, вчера, неделя, месяц с первого числа', () => {
    const now = () => new Date(2026, 9, 15, 23, 50); // 15.10.2026, поздний вечер
    const [today, yesterday, week, month] = exportPresets(now);
    assert.deepEqual([today.label, yesterday.label, week.label, month.label], ['Сегодня', 'Вчера', '7 дней', 'Месяц']);
    assert.deepEqual(today.range(), { from: '2026-10-15', to: '2026-10-15' });
    assert.deepEqual(yesterday.range(), { from: '2026-10-14', to: '2026-10-14' });
    assert.deepEqual(week.range(), { from: '2026-10-09', to: '2026-10-15' });
    assert.equal(rangeDays(week.range().from, week.range().to), 7);
    assert.deepEqual(month.range(), { from: '2026-10-01', to: '2026-10-15' });
    // Первое число месяца: «Вчера» — последний день прошлого, «Месяц» — один сегодняшний день.
    const first = () => new Date(2026, 10, 1, 9, 0);
    const [, y2, , m2] = exportPresets(first);
    assert.deepEqual(y2.range(), { from: '2026-10-31', to: '2026-10-31' });
    assert.deepEqual(m2.range(), { from: '2026-11-01', to: '2026-11-01' });
    // Ни один пресет не длиннее потолка и не заходит в завтра.
    for (const preset of exportPresets(now)) {
        const { from, to } = preset.range();
        assert.ok(rangeDays(from, to) <= EXPORT_MAX_DAYS, preset.label);
        assert.ok(to <= isoDay(now()), preset.label);
    }
});

test('пресеты считают день в момент нажатия, а не при загрузке страницы', () => {
    let moment = new Date(2026, 9, 15, 23, 59);
    const [today] = exportPresets(() => moment);
    assert.deepEqual(today.range(), { from: '2026-10-15', to: '2026-10-15' });
    moment = new Date(2026, 9, 16, 0, 1); // раздел открыт со вчера, полночь прошла
    assert.deepEqual(today.range(), { from: '2026-10-16', to: '2026-10-16' });
});

test('кнопка — в шапке раздела и ходит в ручку своего отдела', () => {
    const view = read('../src/components/dial_list/DialListView.jsx');
    assert.match(view, /import DialListCallsExport from '\.\/DialListCallsExport';/);
    // В шапке (до полосы вкладок), только при выбранном отделе, с его id — и только в «Удаленном КЦ»:
    // тот же экран с профилем tez («Автообзвон Тез») кнопки не получает (там успешка — другая).
    const header = view.slice(view.indexOf('{/* Шапка */}'), view.indexOf('<IosSegmented'));
    assert.match(header, /\{selected && !tez && \(\s*<DialListCallsExport[\s\S]*?departmentId=\{selected\.department_id\}/);
    assert.equal((view.match(/<DialListCallsExport\b/g) || []).length, 1);
    const button = read('../src/components/dial_list/DialListCallsExport.jsx');
    assert.match(button, /\/api\/dial_list\/departments\/\$\{departmentId\}\/calls\/export\?/);
    // Эталон формы: календарь сайта с «Подтвердить», а не системный <input type="date">.
    assert.match(button, /<IosDateRangeCalendar/);
    assert.doesNotMatch(button, /type="date"/);
    // Пустой период — спокойной строкой под кнопкой, а не красным тостом «ошибки».
    assert.match(button, /const EMPTY_CODE = 'DIAL_LIST_CALLS_EXPORT_EMPTY';/);
    assert.match(button, /data\?\.code === EMPTY_CODE\) tell\('empty', /);
    assert.match(button, /NOTICE_TONE = \{ empty: 'text-amber-600', error: 'text-rose-500' \}/);
    const server = read('../dial_list/routes.py');
    assert.match(server, /"code": "DIAL_LIST_CALLS_EXPORT_EMPTY"/);
    assert.match(server, /'\/api\/dial_list\/departments\/<int:department_id>\/calls\/export'/);
});
