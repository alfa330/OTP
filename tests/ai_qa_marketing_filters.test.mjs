import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    EMPTY_FILTERS, NONE_BUCKET, MARKETING_LIST_KEYS, countActiveFilters, filtersToParams,
    activeFilterChips, hasMarketingFilters, isLostStage, reasonsAllowed,
} from '../src/components/call_qa/filters.js';

/**
 * Маркетинговые оси панели «ИИ-оценки» (ТЗ #317) — зеркало
 * call_qa/marketing/filters.py. Панель, запросы и чипы обязаны понимать оси
 * ОДИНАКОВО с сервером; разъезд виден не как ошибка, а как «фильтр по каналу
 * выставлен, а список не изменился».
 */

const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

test('пустые маркетинговые оси не отправляют ничего и не считаются фильтром', () => {
    assert.deepEqual(filtersToParams(EMPTY_FILTERS), {});
    assert.equal(hasMarketingFilters(EMPTY_FILTERS), false);
    // Одинокий режим без значений — не фильтр, как и на сервере.
    const lonely = { ...EMPTY_FILTERS, stage_mode: 'at_call', handler_mode: 'crm' };
    assert.deepEqual(filtersToParams(lonely), {});
    assert.equal(countActiveFilters(lonely), 0);
});

test('списки уходят массивами, а не строкой через запятую', () => {
    // Имена этапов и причин сами содержат запятые.
    const f = { ...EMPTY_FILTERS, stages: ['Нет авто (не цел), аренда'], parks: ['itaxi', NONE_BUCKET] };
    const params = filtersToParams(f);
    assert.deepEqual(params.stages, ['Нет авто (не цел), аренда']);
    assert.deepEqual(params.parks, ['itaxi', 'none']);
    assert.equal(typeof params.parks, 'object');
});

test('канал с кампанией — ОДИН фильтр, сотрудник с группой — один, этап с режимом — один', () => {
    const f = { ...EMPTY_FILTERS, channels: ['tiktok'], campaigns: ['spring'],
                handler_ids: [5], handler_group_ids: [7], handler_mode: 'crm',
                stages: ['Закрыто и не реализовано'], stage_mode: 'at_call' };
    assert.equal(countActiveFilters(f), 3);
    const params = filtersToParams(f);
    assert.equal(params.stage_mode, 'at_call');
    assert.equal(params.handler_mode, 'crm');
    assert.deepEqual(params.handler_ids, ['5']);
});

test('причина отказа допустима только при этапе «Закрыто-нереализовано»', () => {
    assert.equal(isLostStage('Закрыто и не реализовано'), true);
    assert.equal(isLostStage('ПРОШЕЛ РЕГИСТРАЦИЮ'), false);
    assert.equal(reasonsAllowed({ ...EMPTY_FILTERS, stages: ['Звонки'] }), false);
    assert.equal(reasonsAllowed({ ...EMPTY_FILTERS, stages: ['Звонки', 'Закрыто и не реализовано'] }), true);
});

test('чипы: одна ось — один чип с подписями из справочника; снятие этапов уносит причину', () => {
    const f = { ...EMPTY_FILTERS, parks: ['itaxi', NONE_BUCKET], channels: ['tiktok'], campaigns: ['spring'],
                stages: ['Закрыто и не реализовано'], stage_mode: 'at_call', reasons: ['Нет авто (не цел)', NONE_BUCKET],
                handler_ids: [5], handler_mode: 'crm', deal_id: '123' };
    const chips = activeFilterChips(f, { marketing: {
        parks: [{ code: 'itaxi', title: 'iTaxi' }],
        channels: [{ code: 'tiktok', title: 'TikTok' }],
        handlers: [{ id: 5, name: 'Петров' }],
    } });
    const byKey = Object.fromEntries(chips.map((chip) => [chip.key, chip]));
    assert.equal(byKey.parks.label, 'iTaxi, не определён');
    assert.equal(byKey.channels.label, 'TikTok, spring');
    assert.equal(byKey.stages.name, 'Этап на момент разговора');
    assert.equal(byKey.reasons.label, 'Нет авто (не цел), не указана');
    assert.equal(byKey.handlers.name, 'Ответственный');
    assert.equal(byKey.handlers.label, 'Петров');
    assert.equal(byKey.deal.label, '№ 123');
    // Крестик на этапах снимает и причину — иначе отбор станет недопустимым.
    assert.deepEqual(byKey.stages.next.reasons, []);
    assert.equal(byKey.stages.next.stage_mode, '');
    // Крестик на канале снимает и кампании.
    assert.deepEqual(byKey.channels.next.campaigns, []);
});

test('оси, режимы и маркеры совпадают с сервером буквально', () => {
    const py = readLf('call_qa/marketing/filters.py');
    for (const key of MARKETING_LIST_KEYS) {
        assert.ok(py.includes(`'${key}'`), `сервер не знает оси ${key}`);
    }
    assert.ok(py.includes("NONE_BUCKET = 'none'"));
    assert.equal(NONE_BUCKET, 'none');
    assert.ok(py.includes("STAGE_MODES = ('current', 'at_call')"));
    assert.ok(py.includes("HANDLER_MODES = ('spoke', 'crm')"));
    const pyMarkers = py.split('LOST_STAGE_MARKERS = (')[1].split(')')[0].match(/'([^']+)'/g).map((s) => s.slice(1, -1)).sort();
    const js = readLf('src/components/call_qa/filters.js');
    const jsMarkers = js.split('LOST_STAGE_MARKERS = [')[1].split('];')[0].match(/'([^']+)'/g).map((s) => s.slice(1, -1)).sort();
    assert.deepEqual(jsMarkers, pyMarkers);
});

test('сервер читает и parks, и parks[] — форма массива у axios', () => {
    const src = readLf('bot_schedule2.py');
    assert.ok(src.includes("request.args.getlist(key + '[]')"));
});
