// Закреплённые водители в журнале «Удаленного КЦ» (владелец, 18.11.2026; сервер — dial_list/pins.py): чистая
// логика «Ответственного» (src/components/dial_list/leadPin.js) и проводка в строке списка и карточке водителя.
//
// Запуск: node --test tests/dial_list_lead_pin.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { PIN_SUB, responsibleOf, responsibleSub } from '../src/components/dial_list/leadPin.js';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

const FRONT = { id: 801, name: 'Фронтова Фарида' };
const SAVA = { id: 520, name: 'Своев Сава' };

test('в списке сейчас — он; закреплён за ним же — подпись говорит и об этом', () => {
    const lead = { responsible: FRONT, responsible_is_current: true, pinned_operator: { ...FRONT } };
    const resp = responsibleOf(lead);
    assert.deepEqual(resp, { person: FRONT, kind: 'current', pinned: true });
    assert.equal(responsibleSub(resp, '10:15'), `в списке сейчас · ${PIN_SUB}`);
    // id приходят числом и строкой одинаково.
    assert.equal(responsibleOf({ ...lead, pinned_operator: { id: '801', name: FRONT.name } }).pinned, true);
    const other = responsibleOf({ responsible: SAVA, responsible_is_current: true, pinned_operator: FRONT });
    assert.deepEqual(other, { person: SAVA, kind: 'current', pinned: false });
    assert.equal(responsibleSub(other), 'в списке сейчас');
});

test('ни у кого в списке — тот, за кем закреплён, даже если последним звонил другой', () => {
    const resp = responsibleOf({ responsible: SAVA, responsible_is_current: false, pinned_operator: FRONT });
    assert.deepEqual(resp, { person: FRONT, kind: 'pinned', pinned: true });
    assert.equal(responsibleSub(resp, 'по последнему звонку'), PIN_SUB);
    assert.equal(PIN_SUB, 'закреплён');
});

test('без закрепления — как было: кто звонил последним, подпись своя у строки и у карточки', () => {
    const resp = responsibleOf({ responsible: SAVA, responsible_is_current: false, pinned_operator: null });
    assert.deepEqual(resp, { person: SAVA, kind: 'last', pinned: false });
    assert.equal(responsibleSub(resp, '10:15'), '10:15');
    assert.equal(responsibleSub(resp, 'по последнему звонку'), 'по последнему звонку');
    assert.equal(responsibleSub(resp), '');
    for (const empty of [undefined, null, {}, { responsible: null, pinned_operator: null }]) {
        assert.deepEqual(responsibleOf(empty), { person: null, kind: 'none', pinned: false });
    }
    assert.equal(responsibleSub(responsibleOf({})), '');
    assert.equal(responsibleSub(null), '');
});

test('строка списка и карточка водителя берут ответственного из leadPin.js', () => {
    const src = read('src/components/dial_list/DialListJournal.jsx');
    assert.match(src, /import \{ responsibleOf, responsibleSub \} from '\.\/leadPin';/);
    // Строка списка: колонка оператора — тот же ответственный, подпись — время последнего звонка по умолчанию.
    assert.match(src, /const responsible = responsibleOf\(lead\);\s*const operatorSub = responsibleSub\(responsible, fmtWhen\(lead\.last_call\?\.at\)\);/);
    assert.match(src, /\{responsible\.person && <div className="truncate text-\[13px\] text-slate-800">\{responsible\.person\.name\}<\/div>\}/);
    // Карточка: «Ответственный» с подписью «по последнему звонку» для незакреплённого.
    assert.match(src, /sub=\{responsibleSub\(responsibleOf\(lead\), 'по последнему звонку'\)\}/);
    assert.match(src, /\{responsibleOf\(lead\)\.person\?\.name \|\| <span className="text-slate-400">ещё не выдавался<\/span>\}/);
    // Старой ветки «lead.responsible_is_current ? 'в списке сейчас'» в разметке не осталось — правило одно.
    assert.doesNotMatch(src, /lead\.responsible_is_current \? 'в списке сейчас'/);
});
