import assert from 'node:assert/strict';
import test from 'node:test';

import { aliasesForText } from '../src/components/wiki/searchText.js';


test('compound aliases match only as complete adjacent phrases', () => {
    assert.ok(aliasesForText('Документация render.com').includes('рендер'));
    assert.ok(aliasesForText('Войти через git hub').includes('github'));
    assert.ok(aliasesForText('Настроить wi-fi').includes('wifi'));
    assert.ok(aliasesForText('Каталог land rover').includes('лендровер'));

    assert.ok(!aliasesForText('Ссылка https://example.com').includes('рендер'));
    assert.deepEqual(aliasesForText('com'), []);
    assert.deepEqual(aliasesForText('ровер'), []);
});
