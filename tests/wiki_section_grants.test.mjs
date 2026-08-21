/*
 * Какие галочки раздающий вправе поставить (третье измерение выдачи).
 *
 * Сервер с 21.08.2026 отказывает кодом WIKI_GRANT_BEYOND_SELF, если в правиле
 * выписано право, которого нет у самого раздающего (wiki/routes_structure.py).
 * Форма обязана знать об этом ЗАРАНЕЕ: иначе супервайзер ставит «Удалять»,
 * жмёт «Сохранить» и получает 403 на заполненной форме — тот самый молчаливый
 * отказ, только с обратной стороны стола.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { grantableCheck, presetIsGrantable, PERMISSION_KEYS }
    from '../src/components/wiki/sectionGrants.js';

const SV = ['can_read', 'can_create', 'can_edit', 'can_publish', 'can_approve'];

test('право вне списка гасится', () => {
    const may = grantableCheck(SV);
    assert.equal(may('can_delete'), false);
    assert.equal(may('can_edit'), true);
});

test('список не приехал — не гасим ничего', () => {
    for (const value of [null, undefined, []]) {
        const may = grantableCheck(value);
        for (const key of PERMISSION_KEYS) assert.equal(may(key), true, key);
    }
});

test('пресет предлагается, только если по силам целиком', () => {
    const may = grantableCheck(SV);
    const author = { permissions: { can_read: true, can_create: true, can_edit: true } };
    const full = { permissions: { can_read: true, can_create: true, can_edit: true,
                                  can_publish: true, can_delete: true } };
    assert.equal(presetIsGrantable(author, may), true);
    assert.equal(presetIsGrantable(full, may), false,
                 'пресет с удалением предложен тому, кто удалять не вправе');
});

test('пустой пресет «Нет» доступен всегда — им снимают доступ', () => {
    assert.equal(presetIsGrantable({ permissions: {} }, grantableCheck(['can_read'])), true);
});
