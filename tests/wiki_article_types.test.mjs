import assert from 'node:assert/strict';
import test from 'node:test';

import { groupByType, typePlural, normalizeType } from '../src/components/wiki/articleTypes.js';

const A = (id, article_type) => ({ id, article_type });

const shape = (groups) => groups.map((g) => [g.key, g.items.map((a) => a.id)]);

test('в разделе с одним типом полосок нет', () => {
    // Полоска «Должностные инструкции» над списком, где все статьи и так
    // должностные инструкции, повторяла бы название раздела.
    assert.equal(groupByType([A(1, 'job_description'), A(2, 'job_description')]), null);
    assert.equal(groupByType([A(1, 'general')]), null);
    assert.equal(groupByType([]), null);
});

test('смешанный раздел разложен по типам в заданном порядке', () => {
    // Порядок фиксирован: сначала то, чему подчиняются, обычные статьи — в конце.
    // Во входном списке он намеренно обратный.
    const groups = groupByType([
        A(1, 'general'), A(2, 'tool_description'), A(3, 'instruction'),
        A(4, 'regulation'), A(5, 'job_description'), A(6, 'job_description'),
    ]);
    assert.deepEqual(shape(groups), [
        ['job_description', [5, 6]],
        ['regulation', [4]],
        ['instruction', [3]],
        ['tool_description', [2]],
        ['general', [1]],
    ]);
});

test('порядок статей внутри группы не меняется', () => {
    const groups = groupByType([A(9, 'job_description'), A(3, 'general'), A(7, 'job_description')]);
    assert.deepEqual(shape(groups), [['job_description', [9, 7]], ['general', [3]]]);
});

test('незнакомый тип не теряет статью, а падает в «Статьи»', () => {
    // Тип мог прийти из будущей версии схемы или из битой записи. Пропасть из
    // оглавления статья при этом не имеет права.
    assert.equal(normalizeType('policy'), 'general');
    assert.equal(normalizeType(undefined), 'general');
    const groups = groupByType([A(1, 'job_description'), A(2, 'policy'), A(3, undefined)]);
    assert.deepEqual(shape(groups), [['job_description', [1]], ['general', [2, 3]]]);
});

test('подпись группы — во множественном числе', () => {
    assert.equal(typePlural('job_description'), 'Должностные инструкции');
    assert.equal(typePlural('general'), 'Статьи');
});
