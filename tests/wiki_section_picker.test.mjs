import assert from 'node:assert/strict';
import test from 'node:test';

import { selectableSections, sectionOptionLabel } from '../src/components/wiki/sectionPicker.js';

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
