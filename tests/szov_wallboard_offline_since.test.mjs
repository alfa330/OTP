import test from 'node:test';
import assert from 'node:assert/strict';

import { formatOfflineSince } from '../src/components/monitoring/szovWallboardShared.js';

/*
 * Подпись под ФИО на странице «Не в системе» табло СЗоВ (направление «Чат»). Сутки берутся из
 * снимка, а не из часов браузера: стена работает и после полуночи, и «сегодня» обязано значить
 * те же сутки, за которые сервер собрал данные.
 */

test('вышел сегодня — время выхода', () => {
    assert.equal(formatOfflineSince('2026-10-02 18:59:52', '2026-10-02'), 'с 18:59');
    assert.equal(formatOfflineSince('2026-10-02 00:05:00', '2026-10-02'), 'с 00:05');
});

test('вышел раньше — дата, а не время', () => {
    // У того, кто не заходит неделю, час выхода ничего не говорит.
    assert.equal(formatOfflineSince('2026-10-01 22:09:27', '2026-10-02'), 'с 01.10');
    assert.equal(formatOfflineSince('2026-09-23 01:00:01', '2026-10-02'), 'с 23.09');
    assert.equal(formatOfflineSince('2025-12-31 23:00:00', '2026-01-01'), 'с 31.12');
});

test('момент неизвестен — подписи нет, а не «с —» или выдуманное время', () => {
    for (const since of ['', null, undefined, 'вчера', '18:59']) {
        assert.equal(formatOfflineSince(since, '2026-10-02'), '');
    }
});

test('без суток снимка время не выдаётся за сегодняшнее', () => {
    assert.equal(formatOfflineSince('2026-10-02 18:59:52', undefined), 'с 02.10');
});
