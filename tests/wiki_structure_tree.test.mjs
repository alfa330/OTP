import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildSpaceTree, collapseRows, filterRows, highlightParts, reorderPatches,
    sectionSiblings, structureNeedles,
} from '../src/components/wiki/structureTree.js';

/* Фикстура повторяет боевое дерево: внутри одного пространства две ветки
 * отделов, а под каждой — одинаково названная лестница должностей. Именно из-за
 * этого совпадения имён поиск обязан оставлять на экране родителей найденного:
 * «Оператор» без ветки не отвечает на вопрос, который из двух.
 *
 * Раздел 40 — живой ребёнок архивного родителя: такой поднимается в корень
 * пространства, иначе он пропал бы из вкладки вместе с ним. */
const SPACES = [
    { id: 1, name: 'Коммерческий отдел', status: 'active', position: 0 },
    { id: 5, name: 'Общий отдел', status: 'active', position: 1 },
];

const SECTIONS = [
    { id: 1, space_id: 1, parent_section_id: null, name: 'Коммерческий директор', status: 'active', position: 0, visibility_scope: 'restricted', rules_count: 1 },
    { id: 19, space_id: 1, parent_section_id: 1, name: 'СЗоВ', status: 'active', position: 6, visibility_scope: 'restricted', rules_count: 0, department_id: 3, department_name: 'СЗоВ — Служба заботы о водителях' },
    { id: 2, space_id: 1, parent_section_id: 19, name: 'Руководитель группы', status: 'active', position: 1, visibility_scope: 'restricted', rules_count: 1 },
    { id: 3, space_id: 1, parent_section_id: 2, name: 'Супервайзер', status: 'active', position: 2, visibility_scope: 'restricted', rules_count: 2 },
    { id: 4, space_id: 1, parent_section_id: 3, name: 'Оператор', status: 'active', position: 3, visibility_scope: 'restricted', rules_count: 4, can_grant_access: true },
    { id: 28, space_id: 1, parent_section_id: 1, name: 'ОП', status: 'active', position: 9, visibility_scope: 'restricted', rules_count: 0, department_id: 7, department_name: 'Отдел продаж' },
    { id: 29, space_id: 1, parent_section_id: 28, name: 'Оператор', status: 'active', position: 10, visibility_scope: 'restricted', rules_count: 0 },
    { id: 18, space_id: 1, parent_section_id: null, name: 'Старая ветка', status: 'archived', position: 5, visibility_scope: 'restricted', rules_count: 0 },
    { id: 40, space_id: 1, parent_section_id: 18, name: 'Осиротевший', status: 'active', position: 11, visibility_scope: 'restricted', rules_count: 1 },
    { id: 8, space_id: 5, parent_section_id: null, name: 'Общий сотрудник', status: 'active', position: 0, visibility_scope: 'public', rules_count: 4 },
];

const rowsOf = (spaceId, sections = SECTIONS) => buildSpaceTree(SPACES, sections).get(spaceId);
const ids = (rows) => rows.map((r) => r.section.id);

test('дерево строится в порядке обхода и считает глубину', () => {
    assert.deepEqual(ids(rowsOf(1)), [1, 19, 2, 3, 4, 28, 29, 40]);
    assert.deepEqual(rowsOf(1).map((r) => r.depth), [0, 1, 2, 3, 4, 1, 2, 0]);
});

test('архивные в дерево не попадают, а их живые дети поднимаются в корень', () => {
    const row = rowsOf(1).find((r) => r.section.id === 40);
    assert.equal(row.depth, 0);
    assert.equal(ids(rowsOf(1)).includes(18), false);
});

test('на строке видно и прямых потомков, и всю ветку', () => {
    const byId = new Map(rowsOf(1).map((r) => [r.section.id, r]));
    // У «Коммерческого директора» две прямые ветки (СЗоВ и ОП) и шесть строк внутри.
    assert.equal(byId.get(1).childCount, 2);
    assert.equal(byId.get(1).descendants, 6);
    assert.equal(byId.get(4).childCount, 0);
    assert.equal(byId.get(4).descendants, 0);
});

test('край ветки размечен на самой строке', () => {
    const byId = new Map(rowsOf(1).map((r) => [r.section.id, r]));
    // СЗоВ и ОП — соседи: у первого нет «выше», у второго нет «ниже».
    assert.deepEqual([byId.get(19).first, byId.get(19).last], [true, false]);
    assert.deepEqual([byId.get(28).first, byId.get(28).last], [false, true]);
    // Единственный в ветке — сразу и первый, и последний: переставлять некуда.
    assert.deepEqual([byId.get(4).first, byId.get(4).last], [true, true]);
});

test('пустой ответ сервера не роняет дерево', () => {
    assert.deepEqual([...buildSpaceTree(null, null).keys()], []);
    assert.deepEqual(buildSpaceTree(SPACES, null).get(1), []);
});

test('запрос латиницей находит русское название', () => {
    // «op» набирают чаще, чем переключают раскладку ради двух букв.
    assert.equal(structureNeedles('op').includes('оп'), true);
    assert.equal(structureNeedles('').length, 0);
    // Казахские буквы сворачиваются так же, как на сервере.
    assert.deepEqual(structureNeedles('Қазына'), ['казына']);
});

test('поиск оставляет родителей найденного контекстом', () => {
    const found = filterRows(rowsOf(1), { needles: structureNeedles('супервайзер') });
    assert.deepEqual(ids(found), [1, 19, 2, 3]);
    assert.deepEqual(found.map((r) => r.matched), [false, false, false, true]);
    // Контекст помечен отдельно: строку рисуем бледнее, чтобы найденное не терялось.
    assert.deepEqual(found.map((r) => r.context), [true, true, true, false]);
});

test('одноимённые должности находятся обе, каждая со своей веткой', () => {
    assert.deepEqual(ids(filterRows(rowsOf(1), { needles: structureNeedles('оператор') })),
        [1, 19, 2, 3, 4, 28, 29]);
});

test('раздел находится по отделу своей ветки', () => {
    assert.deepEqual(ids(filterRows(rowsOf(1), { needles: structureNeedles('продаж') })), [1, 28]);
});

test('фильтр «без доступа» показывает только разделы без правил', () => {
    const rows = filterRows(rowsOf(1), { focus: 'orphan' });
    assert.deepEqual(ids(rows), [1, 19, 28, 29]);
    // Сами ветки СЗоВ и ОП правил не имеют, но здесь они — контекст найденного.
    assert.deepEqual(rows.filter((r) => r.matched).map((r) => r.section.id), [19, 28, 29]);
});

test('публичный раздел под фильтр «без доступа» не подпадает', () => {
    assert.deepEqual(ids(filterRows(rowsOf(5), { focus: 'orphan' })), []);
    assert.deepEqual(ids(filterRows(rowsOf(5), { focus: 'public' })), [8]);
});

test('строка «выше по структуре» не попадает ни в один быстрый фильтр', () => {
    /* Такую строку сервер шлёт ради ветки: человек её не читает и не
       настраивает (context_only). В счётчике «Без доступа» она выглядела бы
       задачей, которой не существует, — ветка отдела правил не имеет по
       определению, чинить там нечего. */
    const above = SECTIONS.map((s) => (
        s.id === 19 ? { ...s, context_only: true } : s));
    const rows = filterRows(rowsOf(1, above), { focus: 'orphan' });
    assert.deepEqual(rows.filter((r) => r.matched).map((r) => r.section.id), [28, 29]);
    // Из дерева она при этом не исчезает — остаётся контекстом найденного.
    assert.ok(ids(filterRows(rowsOf(1, above),
        { needles: structureNeedles('супервайзер') })).includes(19));
});

test('поиск и фильтр действуют вместе, а не по очереди', () => {
    const rows = filterRows(rowsOf(1), { needles: structureNeedles('оператор'), focus: 'grant' });
    assert.deepEqual(rows.filter((r) => r.matched).map((r) => r.section.id), [4]);
});

test('без запроса и фильтра дерево возвращается целиком', () => {
    const rows = filterRows(rowsOf(1), {});
    assert.deepEqual(ids(rows), ids(rowsOf(1)));
    assert.equal(rows.every((r) => !r.matched && !r.context), true);
});

test('свёрнутая ветка прячет всех потомков, а не только детей', () => {
    assert.deepEqual(ids(collapseRows(rowsOf(1), new Set([19]))), [1, 19, 28, 29, 40]);
    assert.deepEqual(ids(collapseRows(rowsOf(1), new Set([1]))), [1, 40]);
    assert.deepEqual(ids(collapseRows(rowsOf(1), new Set())), ids(rowsOf(1)));
});

test('соседи по ветке — это соседи, а не строки рядом в списке', () => {
    const byId = new Map(SECTIONS.map((s) => [s.id, s]));
    assert.deepEqual(sectionSiblings(SECTIONS, byId.get(19)).map((s) => s.id), [19, 28]);
    assert.deepEqual(sectionSiblings(SECTIONS, byId.get(4)).map((s) => s.id), [4]);
    // Осиротевший поднялся в корень — и соседствует там с «Коммерческим директором».
    assert.deepEqual(sectionSiblings(SECTIONS, byId.get(40)).map((s) => s.id), [1, 40]);
});

test('перестановка меняет местами значения position, а не нумерует ветку заново', () => {
    const branch = [{ id: 19, position: 6 }, { id: 28, position: 9 }];
    assert.deepEqual(reorderPatches(branch, 28, -1),
        [{ id: 28, position: 6 }, { id: 19, position: 9 }]);
    assert.deepEqual(reorderPatches(branch, 19, 1),
        [{ id: 28, position: 6 }, { id: 19, position: 9 }]);
});

test('на краю ветки переставлять нечего', () => {
    const branch = [{ id: 19, position: 6 }, { id: 28, position: 9 }];
    assert.deepEqual(reorderPatches(branch, 19, -1), []);
    assert.deepEqual(reorderPatches(branch, 28, 1), []);
    assert.deepEqual(reorderPatches(branch, 999, -1), []);
});

test('совпавшие position нумеруются заново — иначе обмен ничего не изменит', () => {
    const branch = [{ id: 1, position: 0 }, { id: 2, position: 0 }, { id: 3, position: 0 }];
    assert.deepEqual(reorderPatches(branch, 3, -1),
        [{ id: 3, position: 1 }, { id: 2, position: 2 }]);
});

test('подсветка режет название по найденному, не сдвигая индексы', () => {
    assert.deepEqual(highlightParts('Супервайзер', ['вайз']), [
        { text: 'Супер', hit: false },
        { text: 'вайз', hit: true },
        { text: 'ер', hit: false },
    ]);
    // Казахская буква свёрнута при поиске, но в подписи остаётся исходной.
    assert.deepEqual(highlightParts('Қазына', ['казы']), [
        { text: 'Қазы', hit: true },
        { text: 'на', hit: false },
    ]);
    assert.deepEqual(highlightParts('Оператор', ['супер']), [{ text: 'Оператор', hit: false }]);
    assert.deepEqual(highlightParts('', ['оп']), [{ text: '', hit: false }]);
});
