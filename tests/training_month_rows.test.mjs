import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/* Тренинги и отклонения месяца в «Графиках работы»: чистая логика из
 * src/components/schedule/trainingMonthRows.js.
 *
 * Модуль исполняется напрямую, без сборщика: в нём нет ни JSX, ни импортов.
 */

const source = readFileSync(new URL('../src/components/schedule/trainingMonthRows.js', import.meta.url), 'utf8');
const module = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const { replaceMonthRows, trackMonthRequest } = module;

const row = (id, operatorId, date, extra = {}) => ({ id, operator_id: operatorId, date, ...extra });
const ids = (list) => (list || []).map((item) => item.id);

/* ── Месяц заменяется целиком ───────────────────────────────────────────── */

test('удалённый на сервере тренинг уходит с экрана', () => {
    const before = { 518: [row(3815, 518, '2026-10-08'), row(3816, 518, '2026-10-09')] };
    const after = replaceMonthRows(before, [row(3816, 518, '2026-10-09')], '2026-10');
    assert.deepEqual(ids(after[518]), [3816]);
});

test('оператор без единого тренинга в месяце пропадает из памяти', () => {
    const before = { 518: [row(3815, 518, '2026-10-08')] };
    const after = replaceMonthRows(before, [], '2026-10');
    assert.deepEqual(after, {});
});

test('новый тренинг появляется, в том числе у оператора, которого в памяти не было', () => {
    const after = replaceMonthRows({ 500: [row(1, 500, '2026-10-01')] },
        [row(1, 500, '2026-10-01'), row(3815, 518, '2026-10-08')], '2026-10');
    assert.deepEqual(ids(after[500]), [1]);
    assert.deepEqual(ids(after[518]), [3815]);
});

test('строки других месяцев не трогаются', () => {
    const before = { 518: [row(10, 518, '2026-09-30'), row(11, 518, '2026-10-01'), row(12, 518, '2026-11-01')] };
    const after = replaceMonthRows(before, [row(13, 518, '2026-10-08')], '2026-10');
    assert.deepEqual(ids(after[518]), [10, 12, 13]);
});

test('правка тренинга заменяет прежнюю строку, а не встаёт рядом', () => {
    const before = { 518: [row(3815, 518, '2026-10-08', { start_time: '11:33' })] };
    const after = replaceMonthRows(before, [row(3815, 518, '2026-10-08', { start_time: '11:40' })], '2026-10');
    assert.equal(after[518].length, 1);
    assert.equal(after[518][0].start_time, '11:40');
});

test('тренинг, перенесённый на другой месяц, не остаётся в прежнем', () => {
    const before = { 518: [row(3815, 518, '2026-10-31')] };
    const afterNovember = replaceMonthRows(before, [row(3815, 518, '2026-11-01')], '2026-11');
    assert.deepEqual(ids(afterNovember[518]), [3815]);
    assert.equal(afterNovember[518][0].date, '2026-11-01');
});

test('строка без оператора отбрасывается', () => {
    const after = replaceMonthRows({}, [row(1, null, '2026-10-01'), row(2, 0, '2026-10-01'), row(3, 'x', '2026-10-01')], '2026-10');
    assert.deepEqual(after, {});
});

test('у оператора, которого месяц не коснулся, остаётся прежний массив', () => {
    const untouched = [row(10, 500, '2026-09-30')];
    const before = { 500: untouched, 518: [row(11, 518, '2026-10-01')] };
    const after = replaceMonthRows(before, [row(12, 518, '2026-10-02')], '2026-10');
    assert.equal(after[500], untouched);
    assert.notEqual(after[518], before[518]);
    assert.notEqual(after, before);
});

test('исходное состояние не меняется', () => {
    const before = { 518: [row(3815, 518, '2026-10-08')] };
    const snapshot = JSON.stringify(before);
    replaceMonthRows(before, [row(1, 518, '2026-10-01')], '2026-10');
    assert.equal(JSON.stringify(before), snapshot);
});

test('пустая память и пустой ответ не падают', () => {
    assert.deepEqual(replaceMonthRows(null, null, '2026-10'), {});
    assert.deepEqual(replaceMonthRows(undefined, [row(1, 518, '2026-10-01')], '2026-10'), { 518: [row(1, 518, '2026-10-01')] });
});

/* ── Запоздавший ответ ──────────────────────────────────────────────────── */

test('ответ, отправленный раньше уже применённого, экран не трогает', () => {
    const requests = {};
    const beforeSave = trackMonthRequest(requests, '2026-10');
    const afterSave = trackMonthRequest(requests, '2026-10');
    assert.equal(afterSave(), true);
    assert.equal(beforeSave(), false, 'иначе только что записанный тренинг пропал бы с экрана');
});

test('ответы, пришедшие по порядку, применяются оба', () => {
    const requests = {};
    const first = trackMonthRequest(requests, '2026-10');
    const second = trackMonthRequest(requests, '2026-10');
    assert.equal(first(), true);
    assert.equal(second(), true);
});

test('упавший последний запрос не гасит годный ответ предыдущего', () => {
    const requests = {};
    const first = trackMonthRequest(requests, '2026-10');
    trackMonthRequest(requests, '2026-10');   // ушёл позже и упал: его проверку никто не позовёт
    assert.equal(first(), true, 'иначе месяц остался бы пустым, а его интервалы — «Ожидает»');
});

test('один ответ применяется один раз', () => {
    const requests = {};
    const only = trackMonthRequest(requests, '2026-10');
    assert.equal(only(), true);
    assert.equal(only(), false);
});

test('месяцы друг другу не мешают', () => {
    const requests = {};
    const october = trackMonthRequest(requests, '2026-10');
    const november = trackMonthRequest(requests, '2026-11');
    assert.equal(november(), true);
    assert.equal(october(), true);
});

test('тренинги и отклонения считают запросы порознь', () => {
    const trainings = {};
    const rejections = {};
    const trainingsAnswer = trackMonthRequest(trainings, '2026-10');
    const rejectionsAnswer = trackMonthRequest(rejections, '2026-10');
    assert.equal(rejectionsAnswer(), true);
    assert.equal(trainingsAnswer(), true);
});
