// Доля повторов в пачке «Удаленного КЦ» (владелец, 24.11.2026: «таких повторов до окончания общего списка в
// выдаваемых пачках должно быть не больше 20%… добавь такую настройку»). Поле настроек
// (src/components/dial_list/repeatShare.js) и проводка в панели настроек отдела.
//
// Запуск: node --test tests/dial_list_repeat_share.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { REPEAT_SHARE_DEFAULT, REPEAT_SHARE_MAX, repeatShareHint, repeatShareValue } from '../src/components/dial_list/repeatShare.js';

test('значение поля: пустое и мусор не превращаются в 0, края — 0 и 100', () => {
    assert.equal(REPEAT_SHARE_DEFAULT, 20);
    assert.equal(REPEAT_SHARE_MAX, 100);
    assert.equal(repeatShareValue('20'), 20);
    assert.equal(repeatShareValue(' 35 '), 35);
    assert.equal(repeatShareValue(''), 20);
    assert.equal(repeatShareValue('', 100), 100);
    assert.equal(repeatShareValue('abc', 40), 40);
    assert.equal(repeatShareValue('0'), 0);
    assert.equal(repeatShareValue('150'), 100);
    assert.equal(repeatShareValue('-5'), 0);
    assert.equal(repeatShareValue('12.6'), 13);
});

test('подпись под полем — что это значит для пачки отдела', () => {
    assert.equal(repeatShareHint('20', '100'), 'до 20 из 100 в пачке');
    assert.equal(repeatShareHint('20', '20'), 'до 4 из 20 в пачке');
    assert.equal(repeatShareHint('100', '100'), 'без ограничения');
    assert.equal(repeatShareHint('0', '100'), 'без повторов, пока есть другие водители');
    assert.equal(repeatShareHint('10', '5'), 'без повторов, пока есть другие водители');
    assert.equal(repeatShareHint('', '100'), 'без ограничения');
});

test('панель удалённого КЦ: поле есть, уходит на сервер тем же ключом', () => {
    const src = readFileSync(new URL('../src/components/sip/DialListSettings.jsx', import.meta.url), 'utf8');
    assert.match(src, /label="Повторов в пачке, не больше, %"/);
    assert.match(src, /numberInput\('repeat_share_pct', 0, REPEAT_SHARE_MAX\)/);
    assert.match(src, /repeat_share_pct: repeatShareValue\(form\.repeat_share_pct, settings\?\.repeat_share_pct \?\? REPEAT_SHARE_MAX\)/);
    assert.match(src, /repeat_share_pct: String\(settings\.repeat_share_pct \?\? REPEAT_SHARE_MAX\)/);
    // Пояснение — под «i», а под полем — только число словами.
    assert.match(src, /hint=\{repeatShareHint\(form\.repeat_share_pct, form\.portion_size\)\}/);
});
