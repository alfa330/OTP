// «Воронка найма», отчёт за день (HR-3): логика экрана — dayReportMeta.js. Цифры считает сервер (hiring/day_report.py),
// здесь — какой вид открыть, подписи длительности, запрос, ключи цифр и что раскрывается списком.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    NOTE_COUNTER_FROM, NOTE_MAX, REPORT_VIEW_KEY, cellKey, cellOpens, dayReportQuery, firstPartIndex, noteCounter, personKey,
    pickReportView, reportViews, scopeLabel, splitNotes, talkClock, totalKey,
} from '../src/components/hiring/dayReportMeta.js';

test('report views by rights: «За день» first for recruiters and the lead, not for a department head', () => {
    assert.deepEqual(reportViews({ recruiter: true }).map((view) => view.value), ['day', 'dashboard', 'motivation']);
    assert.deepEqual(reportViews({ lead: true, recruiter: true }).map((view) => view.value), ['day', 'dashboard', 'motivation']);
    assert.deepEqual(reportViews({ dept_head: true }).map((view) => view.value), ['dashboard', 'motivation']);
    assert.equal(reportViews({ recruiter: true })[0].label, 'За день');
    assert.equal(REPORT_VIEW_KEY, 'hiring.reports.view');
});

test('stored view is kept while it is available, otherwise the first one', () => {
    const views = reportViews({ recruiter: true });
    assert.equal(pickReportView('dashboard', views), 'dashboard');
    assert.equal(pickReportView('', views), 'day');
    assert.equal(pickReportView('nope', views), 'day');
    assert.equal(pickReportView('day', reportViews({ dept_head: true })), 'dashboard');
    assert.equal(pickReportView('day', []), 'dashboard');
});

test('talk duration reads as m:ss or h:mm:ss, nothing to show is null', () => {
    assert.equal(talkClock(0), '0:00');
    assert.equal(talkClock(59), '0:59');
    assert.equal(talkClock(89), '1:29');
    assert.equal(talkClock(89.6), '1:30');
    assert.equal(talkClock(3725), '1:02:05');
    for (const value of [null, undefined, '', 'x']) assert.equal(talkClock(value), null);
});

test('query: day and recruiter, empty parts are not sent', () => {
    assert.deepEqual(dayReportQuery('2026-11-23', ''), { day: '2026-11-23' });
    assert.deepEqual(dayReportQuery('2026-11-23', 521), { day: '2026-11-23', recruiter_id: '521' });
    assert.deepEqual(dayReportQuery('', null), {});
});

test('cell keys match the server and only non-zero cells open a list', () => {
    assert.equal(cellKey('done', 'hh'), 'done:hh');
    assert.equal(cellKey('in_work', 'all'), 'in_work:all');
    assert.equal(personKey(521, 'no_answer'), 'rec:521:no_answer');
    assert.equal(totalKey('done'), 'done:all');
    assert.equal(cellOpens('done', 3, {}), true);
    assert.equal(cellOpens('done', 0, { reports: true }), false);
    // Пул общий: рекрутер без прав руководителя не раскрывает «Поступило» — чужие взятые карточки ему не видны.
    assert.equal(cellOpens('arrived', 5, {}), false);
    assert.equal(cellOpens('arrived', 5, { reports: true }), true);
});

test('«из них» stands before the first part row', () => {
    const rows = [{ key: 'arrived' }, { key: 'done' }, { key: 'office', part: true }, { key: 'training', part: true }];
    assert.equal(firstPartIndex(rows), 2);
    assert.equal(firstPartIndex([{ key: 'done' }]), -1);
    assert.equal(firstPartIndex(null), -1);
});

test('scope label: team or recruiter, today with the time of the snapshot', () => {
    assert.equal(scopeLabel({ recruiter: null }), 'Все рекрутеры');
    assert.equal(scopeLabel({ recruiter: { id: 5, name: 'Дана' }, as_of: '18:40' }), 'Дана · на 18:40');
    assert.equal(scopeLabel({ recruiter: { id: 5, name: '' } }), 'Рекрутер');
    assert.equal(scopeLabel(null), 'Все рекрутеры');
});

test('notes: own one goes to the editor, others are shown only when not empty', () => {
    const { mine, others } = splitNotes([
        { user_id: 1, body: 'моё', mine: true }, { user_id: 2, body: 'их', mine: false }, { user_id: 3, body: '  ', mine: false },
    ]);
    assert.equal(mine.body, 'моё');
    assert.deepEqual(others.map((note) => note.user_id), [2]);
    assert.deepEqual(splitNotes(undefined), { mine: null, others: [] });
    assert.equal(noteCounter('а'.repeat(NOTE_COUNTER_FROM - 1)), '');
    assert.equal(noteCounter('а'.repeat(NOTE_COUNTER_FROM)), `${NOTE_COUNTER_FROM} / ${NOTE_MAX}`);
    assert.equal(NOTE_MAX, 2000);
});
