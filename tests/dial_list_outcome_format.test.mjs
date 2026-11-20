/*
 * Типы итогов звонка (запрос владельца 29.09.2026): подпись «Отказ · Дорого»,
 * сборка списка итогов с типами для PUT и для «есть несохранённые изменения»,
 * чипы типов в журнале. Логика — в src/components/dial_list/outcomeFormat.js
 * (без React), бейдж итога дополнительно отрисовывается через react-dom/server.
 *
 * Что защищаем:
 *   • формат подписи — тот же строит телефон, разойтись им нельзя;
 *   • типы уходят на сервер ВСЕГДА и целиком: для сервера список полный, и тип,
 *     которого в нём нет, выключается;
 *   • снимок «сохранено» и снимок рабочей копии совпадают сразу после загрузки,
 *     а любая правка типа делает список «изменённым»;
 *   • чип с нулём не показываем, выбранный — показываем, «Без типа» — отдельно.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

import {
    SUBTYPES_MAX,
    SUBTYPE_NONE,
    SUBTYPE_SEP,
    activeSubtypeCount,
    applyIdMap,
    isNewId,
    savedIdMap,
    outcomeLabel,
    outcomeSubtypeName,
    outcomesSnapshot,
    remapExpanded,
    rowKey,
    serializeOutcomes,
    serverHasSubtypes,
    subtypeChips,
    subtypesOpen,
    subtypesToggleLabel,
    toEditable,
    validateOutcomes,
} from '../src/components/dial_list/outcomeFormat.js';

const REFUSAL = { id: 'o-1', name: 'Отказ', color: '#FF3B30' };

/* То, что отдаёт GET /api/dial_list/departments/<id>/outcomes. */
const SERVER_OUTCOMES = [
    {
        id: 'o-1', name: 'Отказ', color: '#FF3B30', position: 1, requeue: false, is_active: true, used: 17,
        subtypes: [
            { id: 's-1', name: 'Дорого', position: 1, is_active: true, used: 5 },
            { id: 's-2', name: 'Уже работает в другом парке', position: 2, is_active: true, used: 2 },
            { id: 's-3', name: 'Не интересно', position: 3, is_active: false, used: 1 },
        ],
    },
    {
        id: 'o-2', name: 'Перезвонить позже', color: '#FF9F0A', position: 2, requeue: true, is_active: true, used: 3,
        subtypes: [],
    },
];

test('подпись: итог и тип через среднюю точку', () => {
    assert.equal(SUBTYPE_SEP, ' · ');
    assert.equal(outcomeLabel({ ...REFUSAL, subtype: { id: 's-1', name: 'Дорого' } }), 'Отказ · Дорого');
    assert.equal(outcomeLabel({ ...REFUSAL, subtype: null }), 'Отказ');
    assert.equal(outcomeLabel(REFUSAL), 'Отказ');
    assert.equal(outcomeLabel({ ...REFUSAL, subtype: { id: 's-1', name: '  ' } }), 'Отказ');
    assert.equal(outcomeLabel(null), '');
    assert.equal(outcomeLabel({ name: '' , subtype: { name: 'Дорого' } }), '');
    assert.equal(outcomeSubtypeName({ subtype: { name: ' Дорого ' } }), 'Дорого');
    assert.equal(outcomeSubtypeName({}), '');
});

test('новые строки редактора — по префиксу new-', () => {
    assert.equal(isNewId('new-1727600000000-0'), true);
    assert.equal(isNewId('b0f1c7de-0000-4000-8000-000000000000'), false);
    assert.equal(isNewId(undefined), false);
});

test('toEditable: у каждого итога свой массив типов, старый сервер — пустой', () => {
    const list = toEditable(SERVER_OUTCOMES);
    assert.notEqual(list[0].subtypes, SERVER_OUTCOMES[0].subtypes);
    assert.notEqual(list[0].subtypes[0], SERVER_OUTCOMES[0].subtypes[0]);
    assert.deepEqual(list[0].subtypes, SERVER_OUTCOMES[0].subtypes);
    const old = toEditable([{ id: 'o-1', name: 'Отказ', color: '#FF3B30', requeue: false, is_active: true }]);
    assert.deepEqual(old[0].subtypes, []);
    assert.deepEqual(toEditable(undefined), []);
});

test('старый сервер без типов: раскрывашку не предлагаем, иначе PUT молча их выбросит', () => {
    assert.equal(serverHasSubtypes(SERVER_OUTCOMES), true);
    assert.equal(serverHasSubtypes([{ ...SERVER_OUTCOMES[1], subtypes: [] }]), true);
    assert.equal(serverHasSubtypes([{ id: 'o-1', name: 'Отказ', color: '#FF3B30', requeue: false, is_active: true }]), false);
    assert.equal(serverHasSubtypes([]), true);
    assert.equal(serverHasSubtypes(undefined), true);
});

test('serializeOutcomes: полный вложенный список, новые — без id', () => {
    const items = toEditable(SERVER_OUTCOMES);
    items[0].subtypes.push({ id: 'new-1-0', name: '  Далеко от дома  ', is_active: true, used: 0 });
    items.push({ id: 'new-2-1', name: ' Не дозвонились ', color: '#8E8E93', requeue: false, is_active: true, used: 0, subtypes: [] });
    const payload = serializeOutcomes(items);
    assert.deepEqual(payload[0], {
        id: 'o-1', name: 'Отказ', color: '#FF3B30', requeue: false, is_active: true,
        subtypes: [
            { id: 's-1', name: 'Дорого', is_active: true },
            { id: 's-2', name: 'Уже работает в другом парке', is_active: true },
            { id: 's-3', name: 'Не интересно', is_active: false },
            { id: undefined, name: 'Далеко от дома', is_active: true },
        ],
    });
    // У итога без типов ключ всё равно есть: пустой список, а не «не трогать».
    assert.deepEqual(payload[1].subtypes, []);
    assert.equal(payload[2].id, undefined);
    assert.equal(payload[2].name, 'Не дозвонились');
    // В JSON id новых строк пропадает вовсе — сервер их вставит.
    const json = JSON.parse(JSON.stringify({ items: payload }));
    assert.equal('id' in json.items[0].subtypes[3], false);
    assert.equal('id' in json.items[2], false);
    assert.ok(json.items.every((o) => Array.isArray(o.subtypes)));
    // Длинное название режется до 64, как на сервере.
    const long = serializeOutcomes([{ ...items[1], subtypes: [{ id: 'new-3', name: 'я'.repeat(80), is_active: true }] }]);
    assert.equal(long[0].subtypes[0].name.length, 64);
});

test('снимок: после загрузки чисто, любая правка типа — изменено', () => {
    const saved = outcomesSnapshot(toEditable(SERVER_OUTCOMES));
    const items = toEditable(SERVER_OUTCOMES);
    assert.equal(outcomesSnapshot(items), saved);
    // used в снимок не входит: число меняется от звонков, а не от правки.
    items[0].subtypes[0].used = 99;
    assert.equal(outcomesSnapshot(items), saved);

    const renamed = toEditable(SERVER_OUTCOMES);
    renamed[0].subtypes[0].name = 'Очень дорого';
    assert.notEqual(outcomesSnapshot(renamed), saved);

    const off = toEditable(SERVER_OUTCOMES);
    off[0].subtypes[1].is_active = false;
    assert.notEqual(outcomesSnapshot(off), saved);

    const moved = toEditable(SERVER_OUTCOMES);
    [moved[0].subtypes[0], moved[0].subtypes[1]] = [moved[0].subtypes[1], moved[0].subtypes[0]];
    assert.notEqual(outcomesSnapshot(moved), saved);

    // Пустая новая строка — черновик (20.11.2026, редактор сохраняет сам): правкой не считается и на
    // сервер не уходит; вписали название — правка.
    const draft = toEditable(SERVER_OUTCOMES);
    draft[1].subtypes.push({ id: 'new-9', name: '', is_active: true, used: 0 });
    assert.equal(outcomesSnapshot(draft), saved);
    const added = toEditable(SERVER_OUTCOMES);
    added[1].subtypes.push({ id: 'new-9', name: 'Позже', is_active: true, used: 0 });
    assert.notEqual(outcomesSnapshot(added), saved);
    const draftOutcome = toEditable(SERVER_OUTCOMES);
    draftOutcome.push({ id: 'new-8', name: ' ', color: '#8E8E93', requeue: false, is_active: true, subtypes: [] });
    assert.equal(outcomesSnapshot(draftOutcome), saved);

    // Прежние поля итога по-прежнему отслеживаются.
    const requeueOff = toEditable(SERVER_OUTCOMES);
    requeueOff[1].requeue = false;
    assert.notEqual(outcomesSnapshot(requeueOff), saved);
});

test('validateOutcomes: те же правила, что у сервера', () => {
    const ok = serializeOutcomes(toEditable(SERVER_OUTCOMES));
    assert.equal(validateOutcomes(ok), '');

    const noName = serializeOutcomes([{ ...SERVER_OUTCOMES[1], name: '  ' }]);
    assert.equal(validateOutcomes(noName), 'У каждого итога должно быть название');

    // Новый тип без названия — черновик: не уходит и сохранению не мешает. Стёрли название у
    // сохранённого — ошибка, как у сервера.
    const draftSub = serializeOutcomes([{ ...SERVER_OUTCOMES[0], subtypes: [{ id: 'new-1', name: ' ', is_active: true }] }]);
    assert.deepEqual(draftSub[0].subtypes, []);
    assert.equal(validateOutcomes(draftSub), '');
    const emptySub = serializeOutcomes([{ ...SERVER_OUTCOMES[0], subtypes: [{ id: 's-1', name: ' ', is_active: true }] }]);
    assert.match(validateOutcomes(emptySub), /Отказ/);
    assert.match(validateOutcomes(emptySub), /названи/);
    // Повтор названия итога и «все выключены» — до запроса, как у сервера.
    assert.equal(validateOutcomes(serializeOutcomes([SERVER_OUTCOMES[0], { ...SERVER_OUTCOMES[1], name: ' отказ' }])),
        'Итог «отказ» повторяется');
    assert.equal(validateOutcomes(serializeOutcomes(SERVER_OUTCOMES.map((o) => ({ ...o, is_active: false })))),
        'Хотя бы один итог должен быть включён');

    const dup = serializeOutcomes([{
        ...SERVER_OUTCOMES[0],
        subtypes: [{ id: 's-1', name: 'Дорого', is_active: true }, { id: 'new-1', name: 'ДОРОГО ', is_active: true }],
    }]);
    assert.equal(validateOutcomes(dup), 'Тип «ДОРОГО» у итога «Отказ» повторяется');

    // Одинаковые названия у разных итогов — можно.
    const sameAcross = serializeOutcomes([
        { ...SERVER_OUTCOMES[0], subtypes: [{ id: 'new-1', name: 'Другое', is_active: true }] },
        { ...SERVER_OUTCOMES[1], subtypes: [{ id: 'new-2', name: 'Другое', is_active: true }] },
    ]);
    assert.equal(validateOutcomes(sameAcross), '');

    const many = Array.from({ length: SUBTYPES_MAX + 1 }, (_, i) => ({ id: `new-${i}`, name: `Тип ${i}`, is_active: i % 2 === 0 }));
    assert.equal(validateOutcomes(serializeOutcomes([{ ...SERVER_OUTCOMES[0], subtypes: many }])), 'У итога «Отказ» не больше 20 типов');
    assert.equal(validateOutcomes(serializeOutcomes([{ ...SERVER_OUTCOMES[0], subtypes: many.slice(0, SUBTYPES_MAX) }])), '');
});

test('после автосохранения: временные id → настоящие, набранное во время запроса не пропадает', () => {
    // Ушло на сервер: новая причина «Дорого» у «Отказа», новый итог «Думает» с причиной, черновик.
    const sent = toEditable(SERVER_OUTCOMES);
    sent[0].subtypes.push({ id: 'new-1', name: 'Дорого ', is_active: true, used: 0 });
    sent[0].subtypes[0].name = 'Очень дорого';
    sent.push({ id: 'new-2', name: 'Думает', color: '#8E8E93', requeue: false, is_active: true, used: 0,
                subtypes: [{ id: 'new-3', name: 'Спросит жену', is_active: true, used: 0 },
                           { id: 'new-4', name: '', is_active: true, used: 0 }] });
    // Ответ сервера: выключенные — в конце, у новых — настоящие id.
    const server = [
        { ...SERVER_OUTCOMES[0], subtypes: [
            { id: 's-1', name: 'Очень дорого', is_active: true }, { id: 's-2', name: 'Уже работает в другом парке', is_active: true },
            { id: 's-9', name: 'Дорого', is_active: true }, { id: 's-3', name: 'Не интересно', is_active: false }] },
        SERVER_OUTCOMES[1],
        { id: 'o-7', name: 'Думает', color: '#8E8E93', requeue: false, is_active: true,
          subtypes: [{ id: 's-10', name: 'Спросит жену', is_active: true }] },
    ];
    const idMap = savedIdMap(sent, server);
    assert.deepEqual(idMap, { 'new-1': 's-9', 'new-2': 'o-7', 'new-3': 's-10' });
    // Пока шёл ответ, «Дорого» дописали до «Дорого для меня»: id настоящий, текст — набранный.
    const working = toEditable(sent);
    working[0].subtypes[3].name = 'Дорого для меня';
    const mapped = applyIdMap(working, idMap);
    assert.deepEqual(mapped[0].subtypes.map((s) => [s.id, s.name]), [
        ['s-1', 'Очень дорого'], ['s-2', 'Уже работает в другом парке'], ['s-3', 'Не интересно'],
        ['s-9', 'Дорого для меня']]);
    assert.deepEqual(mapped[2].subtypes.map((s) => s.id), ['s-10', 'new-4']);
    // Сохранённое = отправленное с настоящими id: без новой правки — чисто, с ней — изменено.
    const savedNow = outcomesSnapshot(applyIdMap(sent, idMap));
    assert.equal(outcomesSnapshot(applyIdMap(toEditable(sent), idMap)), savedNow);
    assert.notEqual(outcomesSnapshot(mapped), savedNow);
    // Повторное сохранение — уже без «new-»: сервер обновит, а не заведёт вторые «Дорого» и «Думает».
    assert.ok(serializeOutcomes(mapped).every((o) => o.id && o.subtypes.every((s) => s.id)));
});

test('ключ строки не меняется, когда автосохранение даёт настоящий id (курсор не выпадает из поля)', () => {
    // Новая строка: _key = временный id. После сохранения id настоящий, ключ — прежний.
    const working = toEditable(SERVER_OUTCOMES);
    working[0].subtypes.push({ id: 'new-7', _key: 'new-7', name: 'Хоч', is_active: true, used: 0 });
    working.push({ id: 'new-8', _key: 'new-8', name: 'Подумает', color: '#8E8E93', requeue: false, is_active: true,
                   used: 0, subtypes: [] });
    const mapped = applyIdMap(working, { 'new-7': 's-21', 'new-8': 'o-22' });
    assert.equal(mapped[0].subtypes[3].id, 's-21');
    assert.equal(rowKey(mapped[0].subtypes[3]), 'new-7');
    assert.equal(rowKey(mapped[2]), 'new-8');
    // Строки с сервера — ключ по id, как раньше.
    assert.equal(rowKey(mapped[0].subtypes[0]), 's-1');
    assert.equal(rowKey(mapped[0]), 'o-1');
    // На сервер и в снимок «сохранено» ключ не попадает.
    assert.ok(!JSON.stringify(serializeOutcomes(mapped)).includes('_key'));
    assert.equal(outcomesSnapshot(mapped), outcomesSnapshot(mapped.map((o) => ({
        ...o, _key: undefined, subtypes: o.subtypes.map((s) => ({ ...s, _key: undefined })) }))));
});

test('список типов раскрыт, если типы есть и его не сворачивали руками', () => {
    const refusal = SERVER_OUTCOMES[0];
    const empty = SERVER_OUTCOMES[1];
    assert.equal(subtypesOpen(refusal, {}), true);
    assert.equal(subtypesOpen(refusal, undefined), true);
    assert.equal(subtypesOpen(empty, {}), false);
    assert.equal(subtypesOpen(refusal, { 'o-1': false }), false);
    assert.equal(subtypesOpen(empty, { 'o-2': true }), true);
    assert.equal(subtypesOpen({ id: 'o-3' }, {}), false);
});

test('раскрывашка типов: «Типы · N» по включённым', () => {
    assert.equal(activeSubtypeCount(SERVER_OUTCOMES[0]), 2);
    assert.equal(subtypesToggleLabel(SERVER_OUTCOMES[0]), 'Типы · 2');
    assert.equal(subtypesToggleLabel(SERVER_OUTCOMES[1]), 'Добавить типы');
    assert.equal(subtypesToggleLabel(SERVER_OUTCOMES[1], false), '');
    const allOff = { subtypes: [{ id: 's-3', name: 'Не интересно', is_active: false }] };
    assert.equal(subtypesToggleLabel(allOff), 'Типы');
    assert.equal(subtypesToggleLabel(allOff, false), 'Типы');
    assert.equal(subtypesToggleLabel({}), 'Добавить типы');
});

test('раскрытый новый итог остаётся раскрытым после сохранения', () => {
    const before = [
        { id: 'o-1', name: 'Отказ' },
        { id: 'new-5', name: ' Думает ' },
        { id: 'o-2', name: 'Перезвонить позже' },
    ];
    const after = [
        { id: 'o-1', name: 'Отказ' },
        { id: 'o-9', name: 'Думает' },
        { id: 'o-2', name: 'Перезвонить позже' },
    ];
    assert.deepEqual(remapExpanded(before, { 'new-5': true, 'o-2': false }, after), { 'o-9': true });
    assert.deepEqual(remapExpanded(before, { 'o-1': true }, after), { 'o-1': true });
    assert.deepEqual(remapExpanded(before, {}, after), {});
});

/* Элемент by_outcome из GET …/leads. */
const BY_OUTCOME = {
    id: 'o-1', name: 'Отказ', color: '#FF3B30', is_active: true, count: 12,
    subtypes: [
        { id: 's-1', name: 'Дорого', is_active: true, count: 5 },
        { id: 's-2', name: 'Уже работает в другом парке', is_active: true, count: 0 },
        { id: 's-3', name: 'Не интересно', is_active: false, count: 3 },
    ],
    none_count: 4,
};

test('чипы типов: без нулей, выбранный остаётся, «Без типа» в конце', () => {
    assert.deepEqual(subtypeChips(BY_OUTCOME).map((c) => [c.id, c.count]), [['s-1', 5], ['s-3', 3], [SUBTYPE_NONE, 4]]);
    const none = subtypeChips(BY_OUTCOME).at(-1);
    assert.equal(none.name, 'Без типа');
    assert.equal(none.none, true);
    assert.equal(subtypeChips(BY_OUTCOME)[1].is_active, false);

    // Выбранный тип с нулём не пропадает — иначе фильтр не снять.
    assert.deepEqual(subtypeChips(BY_OUTCOME, 's-2').map((c) => c.id), ['s-1', 's-2', 's-3', SUBTYPE_NONE]);
    // Выбранный «Без типа» остаётся и при нуле.
    assert.deepEqual(subtypeChips({ ...BY_OUTCOME, none_count: 0 }, SUBTYPE_NONE).map((c) => [c.id, c.count]),
        [['s-1', 5], ['s-3', 3], [SUBTYPE_NONE, 0]]);
    assert.deepEqual(subtypeChips({ ...BY_OUTCOME, none_count: 0 }).map((c) => c.id), ['s-1', 's-3']);
});

test('чипы типов: у итога без типов второго ряда нет; старый сервер — тоже', () => {
    assert.deepEqual(subtypeChips({ id: 'o-2', count: 7, subtypes: [], none_count: 7 }), []);
    assert.deepEqual(subtypeChips({ id: 'o-2', count: 7 }), []);
    assert.deepEqual(subtypeChips(undefined), []);
    // Типы есть, но никто их не выбирал: остаётся один «Без типа» — это правда.
    const onlyNone = subtypeChips({ ...BY_OUTCOME, subtypes: BY_OUTCOME.subtypes.map((s) => ({ ...s, count: 0 })) });
    assert.deepEqual(onlyNone.map((c) => c.id), [SUBTYPE_NONE]);
    // Ничего нет — ряда нет.
    assert.deepEqual(subtypeChips({ ...BY_OUTCOME, none_count: 0, subtypes: BY_OUTCOME.subtypes.map((s) => ({ ...s, count: 0 })) }), []);
});

/* ─── бейдж итога: настоящий компонент через react-dom/server ─────────── */

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');
const { mkdirSync } = require('node:fs');
const { join } = require('node:path');

async function loadPanelModule() {
    /* Панель тянет FaIcon и UI-кит — собираем её одним бандлом, React и иконки
       оставляем внешними. Бандл кладём внутрь проекта: из системной временной
       папки `import 'react'` не разрешается (так же сделан custom_select_render). */
    const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
    mkdirSync(dir, { recursive: true });
    const file = join(dir, 'DialListOutcomesPanel.mjs');
    buildSync({
        entryPoints: [new URL('../src/components/dial_list/DialListOutcomesPanel.jsx', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
        bundle: true,
        format: 'esm',
        platform: 'node',
        target: 'node18',
        outfile: file,
        loader: { '.js': 'jsx', '.jsx': 'jsx' },
        jsx: 'transform',
        external: ['react', 'react-dom', 'react-dom/*', 'lucide-react'],
        logLevel: 'silent',
    });
    return import(`file://${file.replace(/\\/g, '/')}`);
}

const { OutcomeBadge } = await loadPanelModule();
const render = (props) => renderToStaticMarkup(React.createElement(OutcomeBadge, props));

test('OutcomeBadge: «Отказ · Дорого», полная подпись в title', () => {
    const html = render({ outcome: { ...REFUSAL, subtype: { id: 's-1', name: 'Дорого' } }, small: true });
    assert.match(html, /title="Отказ · Дорого"/);
    assert.match(html, />Отказ<span class="opacity-75"> · Дорого<\/span>/);
});

test('OutcomeBadge: без типа — как раньше', () => {
    const html = render({ outcome: { ...REFUSAL, subtype: null } });
    assert.match(html, /title="Отказ"/);
    assert.doesNotMatch(html, /·/);
    assert.equal(render({ outcome: null }), '');
});
