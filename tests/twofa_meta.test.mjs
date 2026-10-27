// «Коды 2FA» (#373): время, состояния и вкладки экрана — src/components/twofa/twofaMeta.js.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    accountStateLine, clockOf, latestByAccount, manualErrors, peopleCount, tabsFor, whenOf,
} from '../src/components/twofa/twofaMeta.js';

test('время берётся из строки Алматы, а не из пояса браузера', () => {
    assert.equal(clockOf('2026-10-27T13:30:05+05:00'), '13:30');
    assert.equal(clockOf('2026-10-27T13:30:05+05:00', true), '13:30:05');
    assert.equal(clockOf(null), '');
    assert.equal(whenOf('2026-10-27T13:30:05+05:00', '2026-10-27T18:00:00+05:00'), '13:30');
    assert.equal(whenOf('2026-10-26T13:30:05+05:00', '2026-10-27T18:00:00+05:00'), '26.10 13:30');
});

test('последний запрос по аккаунту — первый в списке от новых к старым', () => {
    const latest = latestByAccount([
        { id: 3, account_id: 1, status: 'pending' },
        { id: 2, account_id: 2, status: 'approved' },
        { id: 1, account_id: 1, status: 'expired' },
    ]);
    assert.equal(latest[1].id, 3);
    assert.equal(latest[2].id, 2);
});

test('строка состояния аккаунта у сотрудника', () => {
    const server = '2026-10-27T13:35:00+05:00';
    assert.equal(accountStateLine(null, server), null);
    assert.deepEqual(accountStateLine({ status: 'pending', expires_at: '2026-10-27T13:40:05+05:00' }, server),
        { tone: 'amber', text: 'Ждёт подтверждения · сгорит в 13:40' });
    assert.equal(accountStateLine({ status: 'approved', decided_at: '2026-10-27T13:31:00+05:00' }, server).tone, 'green');
    assert.equal(accountStateLine({ status: 'approved', delivery_error: 'blocked' }, server).tone, 'red');
    assert.equal(accountStateLine({ status: 'expired' }, server).tone, 'slate');
});

test('вкладки по правам', () => {
    assert.deepEqual(tabsFor({ can_request: true }).map((t) => t.value), ['codes']);
    assert.deepEqual(tabsFor({ can_manage: true }).map((t) => t.value), ['requests', 'accounts', 'people', 'journal']);
    const both = tabsFor({ can_manage: true, can_request: true });
    assert.deepEqual(both.map((t) => t.value), ['requests', 'codes', 'accounts', 'people', 'journal']);
    assert.equal(both[1].label, 'Мои коды');
    assert.deepEqual(tabsFor({}), []);
});

test('форма ключа: чего не хватает', () => {
    assert.deepEqual(manualErrors({ title: ' ', secret: '' }), {
        title: 'Укажите название', secret: 'Вставьте ключ из настроек 2FA сервиса',
    });
    assert.deepEqual(manualErrors({ title: 'Google', secret: 'JBSW' }), {});
});

test('склонения', () => {
    assert.equal(peopleCount(1), '1 сотрудник');
    assert.equal(peopleCount(3), '3 сотрудника');
    assert.equal(peopleCount(11), '11 сотрудников');
    assert.equal(peopleCount(22), '22 сотрудника');
});
