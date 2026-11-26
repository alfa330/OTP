// Режим линий (владелец 26.11.2026): переключатель «Удалённый КЦ / Активация» у линий удалённого КЦ —
// подписи вариантов, счёт линий и текст итога переключения.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    MODE_ACTIVATION, MODE_HOST, lineModeOptions, modeCounts, modeResultToast,
} from '../src/components/dial_list/lineModes.js';

const MODES = { host: { id: 1954, name: 'Удаленный КЦ' }, container: { id: 1, name: 'СЗоВ' }, hosted: false };

test('варианты: отдел линий по имени и «Активация»', () => {
    assert.deepEqual(lineModeOptions(MODES), [
        { value: MODE_HOST, label: 'Удаленный КЦ' },
        { value: MODE_ACTIVATION, label: 'Активация' },
    ]);
    assert.equal(lineModeOptions(null)[0].label, 'Удаленный КЦ');
});

test('счёт: только занятые линии, из них в «Активации»', () => {
    const lines = [
        { internal_number: '901', icore_user: { id: 1 }, mode: MODE_ACTIVATION },
        { internal_number: '902', icore_user: { id: 2 }, mode: MODE_HOST },
        { internal_number: '903', icore_user: null, mode: null },
    ];
    assert.deepEqual(modeCounts(lines), { taken: 2, activation: 1 });
    assert.deepEqual(modeCounts(undefined), { taken: 0, activation: 0 });
});

test('итог переключения словами', () => {
    assert.deepEqual(modeResultToast({ mode: MODE_ACTIVATION, changed: 1, skipped: [], released_rows: 0 }, MODES),
        { text: 'Линия переведена в режим «Активация водителей»', kind: 'success' });
    assert.deepEqual(modeResultToast({ mode: MODE_HOST, changed: 3, skipped: [], released_rows: 2 }, MODES),
        { text: 'Линий переведено: 3 в режим «Удаленный КЦ». Водителей вернули в общий список: 2', kind: 'success' });
    const busy = modeResultToast({ mode: MODE_HOST, changed: 0, skipped: [{ user_id: 7, name: 'Айгерим' }] }, MODES);
    assert.equal(busy.kind, 'error');
    assert.match(busy.text, /^Не переключены \(идёт звонок или не поставлен итог\): Айгерим/);
    assert.equal(modeResultToast({ mode: MODE_HOST, changed: 0, skipped: [] }, MODES).text, 'Уже в режиме «Удаленный КЦ»');
});
