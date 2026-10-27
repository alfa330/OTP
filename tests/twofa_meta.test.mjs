// «Коды 2FA» (#373): время, состояния и вкладки экрана — src/components/twofa/twofaMeta.js.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    NEW_GROUP, UNGROUPED_TITLE, accountStateLine, clockOf, defaultGroupChoice, groupAccounts, groupChoiceError,
    groupOptions, groupPayload, journalGroupNote, latestByAccount, manualErrors, peopleCount, tabsFor, whenOf,
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

// Разделы аккаунтов (владелец 27.10.2026): «эти 16 сейчас один раздел», новые — при добавлении.

const GROUPS = [{ id: 7, title: 'Общий маркетинг и рекламные аккаунты' }, { id: 9, title: 'SMM' }];

test('аккаунты по разделам: порядок разделов с сервера, «Без раздела» — в конце', () => {
    const accounts = [
        { id: 1, title: 'Google', group_id: 9 }, { id: 2, title: 'Meta', group_id: 7 },
        { id: 3, title: 'Старый', group_id: null }, { id: 4, title: 'Из удалённого', group_id: 55 },
    ];
    const sections = groupAccounts(accounts, GROUPS);
    assert.deepEqual(sections.map((s) => [s.id, s.title, s.accounts.map((a) => a.id)]), [
        [7, 'Общий маркетинг и рекламные аккаунты', [2]],
        [9, 'SMM', [1]],
        [null, UNGROUPED_TITLE, [3, 4]],
    ]);
    // Пустой раздел — только у главы (его наполняют кнопкой «Изменить»); пустого «Без раздела» нет.
    const onlyGeneral = [{ id: 2, title: 'Meta', group_id: 7 }];
    assert.deepEqual(groupAccounts(onlyGeneral, GROUPS).map((s) => s.id), [7]);
    assert.deepEqual(groupAccounts(onlyGeneral, GROUPS, { withEmpty: true }).map((s) => s.id), [7, 9]);
    assert.deepEqual(groupAccounts([], [], { withEmpty: true }), []);
});

test('поле «Раздел»: варианты, значение по умолчанию, чего не хватает', () => {
    assert.deepEqual(groupOptions(GROUPS), [
        { value: '7', label: 'Общий маркетинг и рекламные аккаунты' },
        { value: '9', label: 'SMM' },
        { value: NEW_GROUP, label: 'Новый раздел…' },
    ]);
    // Один раздел — он; ни одного — сразу новый; несколько — выбрать явно.
    assert.equal(defaultGroupChoice([GROUPS[0]]), '7');
    assert.equal(defaultGroupChoice([]), NEW_GROUP);
    assert.equal(defaultGroupChoice(GROUPS), '');
    assert.equal(groupChoiceError('', ''), 'Выберите раздел');
    assert.equal(groupChoiceError(NEW_GROUP, '  '), 'Укажите название раздела');
    assert.equal(groupChoiceError(NEW_GROUP, 'SMM'), null);
    assert.equal(groupChoiceError('9', ''), null);
    assert.deepEqual(groupPayload(NEW_GROUP, '  SMM '), { group_title: 'SMM' });
    assert.deepEqual(groupPayload('9', 'забытое название'), { group_id: 9 });
    assert.deepEqual(groupPayload('', ''), {});
});

test('журнал о разделах: куда, откуда и как переименован', () => {
    const note = (kind, details) => journalGroupNote({ kind, details });
    assert.equal(note('account_added', { group: 'SMM' }), 'в «SMM»');
    assert.equal(note('account_added', { service: 'Meta' }), '');
    assert.equal(note('account_moved', { from_group: 'Общий', to_group: 'SMM' }), 'из «Общий» в «SMM»');
    assert.equal(note('account_moved', { from_group: null, to_group: 'SMM' }), 'в «SMM»');
    assert.equal(note('account_moved', { from_group: 'Общий', to_group: null }), 'из «Общий» — без раздела');
    assert.equal(note('group_created', { group: 'SMM' }), '«SMM»');
    assert.equal(note('group_renamed', { from: 'SMM', to: 'SMM и таргет' }), '«SMM» → «SMM и таргет»');
    assert.equal(note('group_deleted', { group: 'SMM', accounts: 3 }), '«SMM» · 3 аккаунта — без раздела');
    assert.equal(note('group_deleted', { group: 'SMM', accounts: 0 }), '«SMM»');
    assert.equal(note('groups_introduced', { group: 'Общий маркетинг и рекламные аккаунты', accounts: 16 }),
        '«Общий маркетинг и рекламные аккаунты» · 16 аккаунтов');
    assert.equal(note('account_renamed', { from: 'A', to: 'B' }), '');
    assert.equal(journalGroupNote(null), '');
});
