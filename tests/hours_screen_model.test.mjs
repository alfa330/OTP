import test from 'node:test';
import assert from 'node:assert/strict';

import { hoursScreenModelCode } from '../src/utils/hoursScreenModel.js';

/*
 * «Учёт часов»: по какой модели рисуется экран. Группу можно завести без модели расчёта —
 * тогда сотрудники считаются по модели своего направления, и в одной группе бывают разные
 * модели. Ошибка здесь не падает, а тихо показывает операторам «Чаты» вместо «Звонков»
 * (или наоборот) и запирает загрузку отчёта за день.
 */

const chat = (name) => ({ name, calculation_model_code: 'chat_manager' });
const line = (name) => ({ name, calculation_model_code: 'operator' });

test('своя модель группы решает, какой бы ни был состав', () => {
    const group = { id: 6, calculation_model_code: 'chat_manager' };
    assert.equal(hoursScreenModelCode(group, [line('Абаев'), line('Берик')]), 'chat_manager');
    assert.equal(hoursScreenModelCode(group, []), 'chat_manager');
    assert.equal(hoursScreenModelCode({ id: 6, calculationModelCode: ' tez_op ' }, [line('Абаев')]), 'tez_op');
});

test('группа без модели: экран по модели сотрудников, только если она у всех одна', () => {
    const group = { id: 40, calculation_model_code: null };
    assert.equal(hoursScreenModelCode(group, [chat('Абаев'), chat('Берик')]), 'chat_manager');
    assert.equal(hoursScreenModelCode(group, [line('Абаев'), line('Берик')]), 'operator');
    assert.equal(hoursScreenModelCode(group, [{ name: 'Ян', calculationModelCode: 'op_potok' }]), 'op_potok');
});

test('группа без модели и смешанный состав: операторский экран, а не модель первого по алфавиту', () => {
    const group = { id: 40, calculation_model_code: null };
    assert.equal(hoursScreenModelCode(group, [chat('Абаев'), line('Берик')]), '');
    // порядок строк экран не переключает
    assert.equal(hoursScreenModelCode(group, [line('Берик'), chat('Абаев')]), '');
    assert.equal(hoursScreenModelCode(group, [chat('Абаев'), line('Берик'), line('Ван')]), '');
});

test('группа без модели и без состава — операторский экран', () => {
    assert.equal(hoursScreenModelCode({ id: 40, calculation_model_code: null }, []), '');
    assert.equal(hoursScreenModelCode({ id: 40 }, undefined), '');
    // сотрудник без модели в ответе голоса не имеет
    assert.equal(hoursScreenModelCode({ id: 40 }, [{ name: 'Без модели' }, chat('Абаев')]), 'chat_manager');
});

test('без выбранной группы — как раньше, модель первого сотрудника', () => {
    assert.equal(hoursScreenModelCode(null, [chat('Абаев'), line('Берик')]), 'chat_manager');
    assert.equal(hoursScreenModelCode(undefined, [line('Берик'), chat('Абаев')]), 'operator');
    assert.equal(hoursScreenModelCode(null, []), '');
    assert.equal(hoursScreenModelCode(null, null), '');
});
