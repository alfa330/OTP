import assert from 'node:assert/strict';
import test from 'node:test';

import {
    sectionChildren, sectionTreeRows, selectableSections, sectionOptionLabel,
} from '../src/components/wiki/sectionPicker.js';

/* Фикстура повторяет боевой случай 09.08: структуру завели дважды, вторую копию
 * убрали в архив. Имена у копий одинаковые — именно поэтому архив в выпадашке
 * выглядел дублем живого раздела, а не «удалённым». */
const SECTIONS = [
    { id: 4, space_id: 1, name: 'Оператор', status: 'active' },
    { id: 8, space_id: 5, name: 'Общий сотрудник', status: 'active' },
    { id: 12, space_id: 6, name: 'Оператор', status: 'archived' },
    { id: 16, space_id: 10, name: 'Общий сотрудник', status: 'archived' },
];

const ids = (list) => list.map((s) => s.id);

test('архивные разделы в список выбора не попадают', () => {
    assert.deepEqual(ids(selectableSections(SECTIONS)), [4, 8]);
});

test('текущее значение остаётся, даже если оно архивное', () => {
    // Статья «Информация по СМЗ» лежит в архивном разделе 12: пока дубли были
    // неразличимы, его выбирали. Выбросить его из списка — показать пустое
    // поле при заполненном section_ids, то есть соврать про текущее состояние.
    assert.deepEqual(ids(selectableSections(SECTIONS, 12)), [4, 8, 12]);
    assert.deepEqual(ids(selectableSections(SECTIONS, '12')), [4, 8, 12]);
});

test('пустое значение не тянет за собой весь архив', () => {
    for (const empty of [null, undefined, '', 0]) {
        assert.deepEqual(ids(selectableSections(SECTIONS, empty)), [4, 8], `${empty}`);
    }
});

test('пустой ответ сервера не роняет список', () => {
    assert.deepEqual(selectableSections(null), []);
    assert.deepEqual(selectableSections(undefined, 12), []);
});

test('архивный раздел подписан словами', () => {
    assert.equal(sectionOptionLabel(SECTIONS[0]), 'Оператор');
    assert.equal(sectionOptionLabel(SECTIONS[2]), 'Оператор — в архиве');
    assert.equal(sectionOptionLabel(SECTIONS[0], 'Коммерческий отдел'),
        'Коммерческий отдел · Оператор');
    assert.equal(sectionOptionLabel(SECTIONS[2], 'Коммерческий отдел'),
        'Коммерческий отдел · Оператор — в архиве');
});


/* Периметр, начинающийся серединой дерева, — случай руководителя СЗоВ:
 * «Коммерческий директор» над его веткой ему не виден, а сама ветка видна.
 * Пока корнями считались только разделы без родителя, в выпадашке раздела
 * статьи оставался один «Общий сотрудник» — свой раздел выбрать было нельзя. */
const HEAD_VIEW = [
    { id: 19, space_id: 1, parent_section_id: 1, name: 'СЗоВ', status: 'active' },
    { id: 2, space_id: 1, parent_section_id: 19, name: 'Руководитель группы', status: 'active' },
    { id: 3, space_id: 1, parent_section_id: 2, name: 'Супервайзер', status: 'active' },
    { id: 4, space_id: 1, parent_section_id: 3, name: 'Оператор', status: 'active' },
    { id: 8, space_id: 1, parent_section_id: null, name: 'Общий сотрудник', status: 'active' },
];

test('раздел с невидимым родителем становится корнем', () => {
    assert.deepEqual(ids(sectionChildren(HEAD_VIEW, 1)), [19, 8]);
    // Настоящая вложенность внутри видимой части при этом сохраняется.
    assert.deepEqual(ids(sectionChildren(HEAD_VIEW, 1, 19)), [2]);
    assert.deepEqual(ids(sectionChildren(HEAD_VIEW, 1, 3)), [4]);
});

test('свёрнутое дерево показывает корни, развёрнутое — всю ветку', () => {
    const collapsed = sectionTreeRows(HEAD_VIEW, 1, new Set());
    assert.deepEqual(collapsed.map((r) => r.section.id), [19, 8]);
    assert.deepEqual(collapsed.map((r) => r.hasChildren), [true, false]);

    const opened = sectionTreeRows(HEAD_VIEW, 1, new Set([19, 2, 3]));
    assert.deepEqual(opened.map((r) => r.section.id), [19, 2, 3, 4, 8]);
    assert.deepEqual(opened.map((r) => r.depth), [0, 1, 2, 3, 0]);
});

test('архивная ветка в дерево выбора не попадает', () => {
    const withArchived = [...HEAD_VIEW,
        { id: 20, space_id: 1, parent_section_id: 19, name: 'Оператор', status: 'archived' }];
    assert.deepEqual(ids(sectionChildren(withArchived, 1, 19)), [2]);
});

test('пустой список разделов не роняет дерево', () => {
    assert.deepEqual(sectionTreeRows([], 1, new Set()), []);
    assert.deepEqual(sectionTreeRows(null, 1, undefined), []);
});
