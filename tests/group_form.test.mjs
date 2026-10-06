import test from 'node:test';
import assert from 'node:assert/strict';

import {
    EMPTY_GROUP_FORM,
    NO_GROUP_MODEL_LABEL,
    createGroupBody,
    groupFormAfterDirectionPick,
    groupModelOptions,
} from '../src/utils/groupForm.js';

/*
 * Форма «Новая группа». Модель расчёта необязательна, как и направление: группа бэк-офиса,
 * где по моделям ничего не считают, заводится без неё. Раньше форма открывалась с
 * «Операторской моделью», и та молча уезжала в каждую новую группу.
 */

const DIRECTIONS = [
    { id: 69, name: 'Чат менеджер', calculation_model_code: 'chat_manager', department_id: 1 },
    { id: 70, name: 'Основа', calculationModelCode: 'operator', departmentId: 1 },
    { id: 87, name: 'Регионы', department_id: 909 },
];
const MODELS = [
    { code: 'operator', name: 'Операторская модель' },
    { code: 'chat_manager', name: 'Модель чат-менеджера' },
];

test('форма открывается без модели — как и без направления', () => {
    assert.deepEqual(EMPTY_GROUP_FORM, { name: '', department_id: '', direction_id: '', calculation_model_code: '' });
});

test('в списке моделей первой стоит «без модели», дальше — каталог как есть', () => {
    assert.deepEqual(groupModelOptions(MODELS), [
        { value: '', label: NO_GROUP_MODEL_LABEL },
        { value: 'operator', label: 'Операторская модель' },
        { value: 'chat_manager', label: 'Модель чат-менеджера' },
    ]);
    // каталог ещё не загружен — выбрать «без модели» всё равно можно
    assert.deepEqual(groupModelOptions(undefined), [{ value: '', label: NO_GROUP_MODEL_LABEL }]);
});

test('пустая модель уходит на сервер как null, а не строкой и не «operator»', () => {
    const body = createGroupBody({ ...EMPTY_GROUP_FORM, name: '  Группа ООЗ  ', department_id: '2008' });
    assert.deepEqual(body, { name: 'Группа ООЗ', calculation_model_code: null, direction_id: null, department_id: 2008 });
});

test('выбранная модель и направление уходят как есть; force — только по запросу', () => {
    const form = { name: 'Чаты', department_id: '1', direction_id: '69', calculation_model_code: 'chat_manager' };
    assert.deepEqual(createGroupBody(form), {
        name: 'Чаты', calculation_model_code: 'chat_manager', direction_id: 69, department_id: 1,
    });
    assert.equal(createGroupBody(form, { force: true }).force, true);
    assert.equal('force' in createGroupBody(form, { force: false }), false);
});

test('«без модели» при выбранном направлении остаётся «без модели»', () => {
    const body = createGroupBody({ name: 'Чаты', department_id: '1', direction_id: '69', calculation_model_code: '' });
    assert.equal(body.calculation_model_code, null);
    assert.equal(body.direction_id, 69);
});

test('выбор направления подставляет его модель и отдел, если отдел ещё не выбран', () => {
    const picked = groupFormAfterDirectionPick({ ...EMPTY_GROUP_FORM, name: 'Чаты' }, '69', DIRECTIONS);
    assert.deepEqual(picked, { name: 'Чаты', department_id: '1', direction_id: '69', calculation_model_code: 'chat_manager' });
    // ключи в верблюжьем регистре читаются так же
    assert.equal(groupFormAfterDirectionPick(EMPTY_GROUP_FORM, '70', DIRECTIONS).calculation_model_code, 'operator');
    assert.equal(groupFormAfterDirectionPick(EMPTY_GROUP_FORM, '70', DIRECTIONS).department_id, '1');
    // у направления всегда есть модель; если справочник её не прислал — операторская
    assert.equal(groupFormAfterDirectionPick(EMPTY_GROUP_FORM, '87', DIRECTIONS).calculation_model_code, 'operator');
});

test('выбранный руками отдел направление не перебивает', () => {
    const form = { ...EMPTY_GROUP_FORM, department_id: '367' };
    assert.equal(groupFormAfterDirectionPick(form, '69', DIRECTIONS).department_id, '367');
});

test('возврат к «без направления» ничего не подставляет и не стирает', () => {
    const withModel = { name: 'Чаты', department_id: '1', direction_id: '69', calculation_model_code: 'chat_manager' };
    assert.deepEqual(groupFormAfterDirectionPick(withModel, '', DIRECTIONS), { ...withModel, direction_id: '' });
    // модели не было — «operator» из воздуха не появляется
    const withoutModel = { ...EMPTY_GROUP_FORM, name: 'Группа HR' };
    assert.deepEqual(groupFormAfterDirectionPick(withoutModel, '', DIRECTIONS), withoutModel);
    assert.deepEqual(groupFormAfterDirectionPick(withoutModel, '', undefined), withoutModel);
});

test('правила не портят исходную форму', () => {
    const form = { ...EMPTY_GROUP_FORM };
    groupFormAfterDirectionPick(form, '69', DIRECTIONS);
    createGroupBody(form, { force: true });
    assert.deepEqual(form, { name: '', department_id: '', direction_id: '', calculation_model_code: '' });
    assert.deepEqual(EMPTY_GROUP_FORM, { name: '', department_id: '', direction_id: '', calculation_model_code: '' });
});
