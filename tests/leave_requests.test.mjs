// Заявки на отпуск, Б/С и больничный (#358, #346): правила формы на клиенте и начало стажа.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    LEAVE_KINDS,
    LEAVE_KIND_LABELS,
    LEAVE_KIND_SEGMENTS,
    addDaysIso,
    daysText,
    emptyLeaveDraft,
    formatLeaveDateTime,
    formatLeavePeriod,
    leaveBellTargetId,
    leaveDraftProblem,
    leaveEarliestStart,
    leaveJournalFileName,
    leaveJournalKinds,
    leaveJournalQuery,
    leaveKindHints,
    leaveKindsShortList,
    leaveKindsShortTitle,
    leaveKindsText,
    leaveKindsTitle,
    leaveModalTitle,
    leaveMonthLabel,
    leaveOptionHint,
    leaveOptionLabel,
    leavePeriodDays,
    leaveSummary,
    shiftLeaveMonth,
} from '../src/components/leave/leaveRequests.js';
import { tenureStartDate } from '../src/components/profile/profileFormat.js';

const TODAY = '2026-11-02';
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('дни периода — календарные, оба края включительно', () => {
    assert.equal(leavePeriodDays('2026-12-01', '2026-12-14'), 14);
    assert.equal(leavePeriodDays('2026-12-28', '2027-01-10'), 14);
    assert.equal(leavePeriodDays('2026-11-02', '2026-11-02'), 1);
    assert.equal(leavePeriodDays('2026-12-14', '2026-12-01'), 0);
    assert.equal(leavePeriodDays('', '2026-12-01'), 0);
    // Переход на летнее время в браузере не сдвигает счёт: считаем в UTC.
    assert.equal(leavePeriodDays('2027-03-01', '2027-04-30'), 61);
});

test('склонение дней совпадает с сервером', () => {
    const cases = { 1: '1 день', 2: '2 дня', 4: '4 дня', 5: '5 дней', 11: '11 дней', 14: '14 дней',
        21: '21 день', 22: '22 дня', 111: '111 дней' };
    for (const [count, text] of Object.entries(cases)) assert.equal(daysText(Number(count)), text);
});

test('период пишется без года, пока год текущий', () => {
    assert.equal(formatLeavePeriod('2026-12-01', '2026-12-14', TODAY), '01.12 — 14.12');
    assert.equal(formatLeavePeriod('2026-12-28', '2027-01-10', TODAY), '28.12 — 10.01.2027');
    assert.equal(formatLeavePeriod('2026-11-05', '2026-11-05', TODAY), '05.11');
    assert.equal(leaveSummary({ kind: 'bs', startDate: '2026-12-01', endDate: '2026-12-03' }, TODAY),
        'Б/С · 01.12 — 03.12 · 3 дня');
    assert.equal(addDaysIso('2026-12-31', 1), '2027-01-01');
});

test('форма говорит одной строкой, чего не хватает — по порядку полей', () => {
    const kinds = ['annual_leave', 'bs'];
    const draft = emptyLeaveDraft(kinds);
    assert.equal(draft.kind, null, 'вид не выбран — не «отпуск по умолчанию»');
    assert.equal(leaveDraftProblem(draft, { today: TODAY, kinds }), 'Выберите: отпуск или Б/С');
    const withKind = { ...draft, kind: 'bs' };
    assert.equal(leaveDraftProblem(withKind, { today: TODAY, kinds }), 'Выберите первый день');
    const withStart = { ...withKind, startDate: '2026-12-01' };
    assert.equal(leaveDraftProblem(withStart, { today: TODAY, kinds }), 'Выберите последний день');
    const full = { ...withStart, endDate: '2026-12-03' };
    assert.equal(leaveDraftProblem(full, { today: TODAY, kinds }), 'Напишите причину');
    assert.equal(leaveDraftProblem({ ...full, reason: '  ' }, { today: TODAY, kinds }), 'Напишите причину');
    assert.equal(leaveDraftProblem({ ...full, reason: 'Семья' }, { today: TODAY, kinds }), '');
    assert.match(leaveDraftProblem({ ...full, startDate: '2026-11-01', reason: 'x' }, { today: TODAY, kinds }), /прошло/);
    assert.match(leaveDraftProblem({ ...full, endDate: '2026-11-30', reason: 'x' }, { today: TODAY, kinds }), /раньше первого/);
    assert.match(leaveDraftProblem({ ...full, endDate: '2027-12-31', reason: 'x' }, { today: TODAY, kinds }), /не больше 365/);
});

test('один разрешённый вид выбран заранее и назван в заголовке', () => {
    assert.equal(emptyLeaveDraft(['bs']).kind, 'bs');
    assert.equal(leaveOptionLabel(['annual_leave', 'bs']), 'Отпуск или Б/С');
    assert.equal(leaveOptionLabel(['annual_leave']), 'Взять отпуск');
    assert.equal(leaveModalTitle({ id: null }, ['bs']), 'Заявка на Б/С');
    assert.equal(leaveModalTitle({ id: 5 }, ['bs']), 'Изменить заявку');
    assert.deepEqual(Object.keys(LEAVE_KIND_LABELS), ['annual_leave', 'bs', 'sick_leave']);
    assert.equal(leaveModalTitle({ id: null }, ['sick_leave']), 'Заявка на больничный');
    assert.equal(leaveOptionLabel(['sick_leave']), 'Больничный');
});

test('подписи собираются из видов отдела: без больничного — ни слова о нём', () => {
    const all = ['annual_leave', 'bs', 'sick_leave'];
    assert.equal(leaveKindsText(all), 'отпуск, Б/С и больничный');
    assert.equal(leaveKindsTitle(all, 'или'), 'Отпуск, Б/С или больничный');
    assert.equal(leaveOptionLabel(all), 'Отпуск, Б/С или больничный');
    assert.equal(leaveModalTitle({ id: null }, all), 'Отпуск, Б/С или больничный');
    assert.equal(leaveKindsText(['bs', 'annual_leave']), 'отпуск и Б/С', 'порядок — как в LEAVE_KINDS');
    assert.equal(leaveKindsText(['sick_leave']), 'больничный');
    assert.equal(leaveKindsText([]), 'отпуск, Б/С и больничный', 'без списка — все виды');
    assert.ok(!leaveOptionHint(['annual_leave', 'bs']).includes('больничн'));
    assert.match(leaveOptionHint(all), /^Отпуск, Б\/С или больничный — на период/);
});

test('пояснение под «i» называет ровно варианты сегментов', () => {
    for (const kinds of [LEAVE_KINDS, ['annual_leave', 'sick_leave'], ['bs', 'sick_leave']]) {
        const lines = leaveKindHints(kinds);
        assert.equal(lines.length, kinds.length);
        lines.forEach((line, index) => assert.ok(line.startsWith(`${LEAVE_KIND_SEGMENTS[kinds[index]]} — `), line));
    }
});

test('больничный подают задним числом — до 30 дней, отпуск и Б/С — нет', () => {
    const kinds = ['annual_leave', 'bs', 'sick_leave'];
    const sick = { id: null, kind: 'sick_leave', startDate: '2026-10-28', endDate: '2026-11-04', reason: 'Больничный лист' };
    assert.equal(leaveEarliestStart('sick_leave', TODAY), '2026-10-03');
    assert.equal(leaveEarliestStart('annual_leave', TODAY), TODAY);
    assert.equal(leaveDraftProblem(sick, { today: TODAY, kinds }), '');
    assert.equal(leaveDraftProblem({ ...sick, startDate: '2026-10-03' }, { today: TODAY, kinds }), '');
    assert.match(leaveDraftProblem({ ...sick, startDate: '2026-10-02' }, { today: TODAY, kinds }),
        /задним числом не больше чем на 30 дней/);
    assert.match(leaveDraftProblem({ ...sick, kind: 'annual_leave' }, { today: TODAY, kinds }), /прошло/);
    assert.equal(leaveDraftProblem({ ...sick, kind: null }, { today: TODAY, kinds }),
        'Выберите: отпуск, Б/С или больничный');
});

test('цель уведомления колокола — «leave:<id>»', () => {
    assert.equal(leaveBellTargetId('leave:12'), 12);
    assert.equal(leaveBellTargetId('12'), null);
    assert.equal(leaveBellTargetId(null), null);
});

test('начало стажа сдвигается на дни вне стажа', () => {
    assert.equal(tenureStartDate('2025-07-20', 0), '2025-07-20');
    assert.equal(tenureStartDate('2025-07-20', undefined), '2025-07-20');
    assert.equal(tenureStartDate('2025-07-20', 42), '2025-08-31');
    assert.equal(tenureStartDate('2025-12-25', 10), '2026-01-04');
    assert.equal(tenureStartDate(null, 10), null);
});

test('«i» в окнах заявок — общий InfoHint: пузырь поверх окна, край карточки его не режет', () => {
    // Серый IosHint раскрывался внутри карточки с overflow-hidden и обрезался
    // её краем: от пояснения оставалась тёмная полоска.
    for (const file of ['LeaveSettingsPanel.jsx', 'LeaveReviewPanel.jsx', 'LeaveRequestFields.jsx']) {
        const source = read(`../src/components/leave/${file}`);
        assert.ok(source.includes('<InfoHint'), file);
        assert.ok(!source.includes('IosHint'), file);
    }
    // Те же окна «Запросов» по сменам: два вида «i» в одном окне — шум.
    const app = read('../src/App.jsx');
    for (const anchor of [
        'Сократить можно только внутри уже назначенной смены',
        'Одобрение сразу правит график',
        'Без подписки уведомления о заявке',
    ]) {
        const at = app.indexOf(anchor);
        assert.ok(at > 0, anchor);
        assert.ok(app.slice(app.lastIndexOf('<', at), at).startsWith('<InfoHint'), anchor);
    }
    const label = app.indexOf('>Что нужно</span>');
    assert.ok(app.slice(label, label + 200).includes('<InfoHint side="left">'));
});

test('на телефоне вкладка очереди — короткой подписью, на компьютере — полной', () => {
    // Сегмент на 390 px вмещает 152 px текста; «Отпуск, Б/С и больничный» — 164 px.
    const app = read('../src/App.jsx');
    assert.equal(leaveKindsShortTitle(['annual_leave', 'bs', 'sick_leave']), 'Отпуск и больничный');
    assert.equal(leaveKindsShortTitle(['annual_leave', 'bs']), 'Отпуск и Б/С');
    // Строка «Новый запрос» на телефоне: 254 px места, с «Б/С» перечень — 297 px.
    assert.equal(leaveKindsShortList(['annual_leave', 'bs', 'sick_leave']), 'отпуск, больничный');
    assert.equal(leaveKindsShortList(['bs', 'sick_leave']), 'Б/С, больничный');
    assert.match(app, /`Смена, выходной, \$\{leaveKindsShortList\(myLeave\.kinds\)\}`/);
    const tab = app.slice(app.indexOf("value: 'leave',"), app.indexOf('count: leaveReview.pendingCount'));
    assert.match(tab, /className="sm:hidden">\{leaveKindsShortTitle\(leaveReview\.kinds\)\}/);
    assert.match(tab, /className="hidden sm:inline">\{leaveKindsTitle\(leaveReview\.kinds\)\}/);
});

test('журнал: месяц стрелками, подпись месяца и отбор без пустых полей', () => {
    assert.equal(leaveMonthLabel('2026-11'), 'Ноябрь 2026');
    assert.equal(leaveMonthLabel('2027-01'), 'Январь 2027');
    assert.equal(leaveMonthLabel(''), '');
    assert.equal(shiftLeaveMonth('2026-12', 1), '2027-01');
    assert.equal(shiftLeaveMonth('2026-01', -1), '2025-12');
    assert.equal(shiftLeaveMonth('2026-11', 0), '2026-11');
    assert.deepEqual(leaveJournalQuery({ month: '2026-11', kind: '', status: '' }), { month: '2026-11' });
    assert.deepEqual(leaveJournalQuery({ month: '2026-11', kind: 'bs', status: 'approved' }),
        { month: '2026-11', kind: 'bs', status: 'approved' });
    assert.equal(leaveJournalFileName('2026-11'), 'Журнал заявок 2026-11.xlsx');
});

test('журнал: виды в отборе — те, что принимает отдел; время шага с годом, только если он не текущий', () => {
    assert.deepEqual(leaveJournalKinds(['annual_leave', 'bs']).map((o) => o.label), ['Все виды', 'Ежегодный отпуск', 'Б/С']);
    assert.deepEqual(leaveJournalKinds([]).map((o) => o.value), ['', 'annual_leave', 'bs', 'sick_leave']);
    assert.equal(formatLeaveDateTime('2026-11-02T14:05:33', TODAY), '02.11 14:05');
    assert.equal(formatLeaveDateTime('2025-12-30T09:00:00', TODAY), '30.12.2025 09:00');
    assert.equal(formatLeaveDateTime(null, TODAY), '');
});

test('очередь: второй сегмент — журнал, файл скачивается в том же окне', () => {
    const panel = read('../src/components/leave/LeaveReviewPanel.jsx');
    const journal = read('../src/components/leave/LeaveJournal.jsx');
    assert.match(panel, /\{ value: 'journal', label: 'Журнал' \}/);
    assert.doesNotMatch(panel, /value: 'all'/);
    assert.match(journal, /saveLeaveFile\(await api\.journalFile/);
    assert.doesNotMatch(journal, /window\.open/);
    // Пояснений на экране журнала нет: подписи — только у отборов и пустого списка.
    assert.doesNotMatch(journal, /<InfoHint|<p /);
});
