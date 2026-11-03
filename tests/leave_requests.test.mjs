// Заявки на отпуск и Б/С (#358): правила формы на клиенте и начало стажа.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    LEAVE_KIND_LABELS,
    addDaysIso,
    daysText,
    emptyLeaveDraft,
    formatLeavePeriod,
    leaveBellTargetId,
    leaveDraftProblem,
    leaveModalTitle,
    leaveOptionLabel,
    leavePeriodDays,
    leaveSummary,
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
    assert.deepEqual(Object.keys(LEAVE_KIND_LABELS), ['annual_leave', 'bs']);
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
