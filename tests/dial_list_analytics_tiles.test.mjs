// «Аналитика» удалённого КЦ (владелец, 24.11.2026): у «Обзвонили» — ещё доступно для обзвона (не звонили · ждут
// повтора), у «Успешки» — подписали сами до загрузки базы и после. Подписи (src/components/dial_list/analyticsTiles.js)
// и проводка в DialListAnalytics.jsx.
//
// Запуск: node --test tests/dial_list_analytics_tiles.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { availableLines, selfSignedLines } from '../src/components/dial_list/analyticsTiles.js';

const NF = new Intl.NumberFormat('ru-RU');
const fmt = (v) => NF.format(v).replace(/ /g, ' ');

test('«Обзвонили»: ещё доступно — кому ещё не звонили, и отдельно ждут повтора (не сумма)', () => {
    assert.deepEqual(availableLines({ open_new: 663, open_retry: 10770 }, fmt), ['ещё доступно — 663', 'ждут повтора — 10 770']);
    assert.deepEqual(availableLines({ open_new: 0, open_retry: 0 }, fmt), ['ещё доступно — 0', 'ждут повтора — 0']);
    // Прошлый месяц — его обзвон не выдаёт: строк нет. Старый сервер без чисел — тоже.
    assert.deepEqual(availableLines({ open_new: 5, open_retry: 7 }, fmt, false), []);
    assert.deepEqual(availableLines({ called: 10 }, fmt), []);
    assert.deepEqual(availableLines(null, fmt), []);
});

test('«Успешки»: подписали сами — до загрузки базы и после', () => {
    assert.deepEqual(selfSignedLines({ self_signed: 13181, signed_before_upload: 3465 }, fmt),
        ['сами — 13 181', 'до загрузки базы 3 465 · после 9 716']);
    assert.deepEqual(selfSignedLines({ self_signed: 10, signed_before_upload: 0 }, fmt),
        ['сами — 10', 'до загрузки базы 0 · после 10']);
    assert.deepEqual(selfSignedLines({ self_signed: 0, signed_before_upload: 0 }, fmt), []);
    // До загрузки — часть «сами»; больше «сами» не бывает (страховка от рассинхрона чисел).
    assert.deepEqual(selfSignedLines({ self_signed: 5, signed_before_upload: 9 }, fmt),
        ['сами — 5', 'до загрузки базы 5 · после 0']);
});

test('плитки «Обзвонили» и «Успешки» берут эти строки', () => {
    const src = readFileSync(new URL('../src/components/dial_list/DialListAnalytics.jsx', import.meta.url), 'utf8');
    assert.match(src, /import \{ availableLines, selfSignedLines \} from '\.\/analyticsTiles';/);
    assert.match(src, /availableLines\(t, num, data\.is_active_period !== false\)/);
    assert.match(src, /\.\.\.selfSignedLines\(t, num\)/);
    assert.match(src, /help=\{CALLED_HINT\}/);
    assert.match(src, /help=\{SUCCESS_HINT\}/);
});
