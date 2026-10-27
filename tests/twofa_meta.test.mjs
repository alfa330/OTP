// «Коды 2FA» (#373): время, состояния и вкладки экрана — src/components/twofa/twofaMeta.js.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    ALL_SECTIONS, MONOGRAM_TONES, NEW_GROUP, edgeMask, UNGROUPED, UNGROUPED_TITLE, accountStateLine, clockOf, defaultGroupChoice,
    filterAccounts, filterPeople, groupAccounts, groupChoiceError, groupOptions, groupPayload, journalGroupNote, journalKind,
    latestByAccount, manualErrors, matchesQuery, monogramOf, pageOf, peopleCount, sectionTiles, tabsFor, whenOf,
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

// Вид раздела (владелец 27.10.2026): «аккуратно и удобно… не забудь использовать пагинацию».

test('страница списка: диапазон «1–12 из 30», номер вне списка прижимается к краю', () => {
    const list = Array.from({ length: 30 }, (_, index) => index + 1);
    const first = pageOf(list, 1, 12);
    assert.deepEqual([first.items.length, first.page, first.pageCount, first.from, first.to, first.total], [12, 1, 3, 1, 12, 30]);
    const last = pageOf(list, 3, 12);
    assert.deepEqual([last.items, last.from, last.to], [[25, 26, 27, 28, 29, 30], 25, 30]);
    // Последнюю строку страницы удалили — не остаёмся на пустой странице.
    assert.equal(pageOf(list.slice(0, 24), 3, 12).page, 2);
    assert.equal(pageOf(list, 0, 12).page, 1);
    assert.equal(pageOf(list, undefined, 12).page, 1);
    assert.deepEqual(pageOf([], 4, 12), { items: [], page: 1, pageCount: 1, total: 0, from: 0, to: 0 });
});

test('поиск: без регистра, «ё» = «е», по названию и сервису', () => {
    const accounts = [
        { id: 1, title: 'Ёлки · реклама', service: 'Google' },
        { id: 2, title: 'promo.team@example.com', service: 'Tilda' },
        { id: 3, title: 'Кабинет', service: null },
    ];
    assert.deepEqual(filterAccounts(accounts, 'елки').map((a) => a.id), [1]);
    assert.deepEqual(filterAccounts(accounts, 'TILDA').map((a) => a.id), [2]);
    assert.deepEqual(filterAccounts(accounts, '   ').map((a) => a.id), [1, 2, 3]);
    assert.equal(matchesQuery('кабинет', null, undefined, 'Кабинет'), true);
    const people = [{ id: 1, name: 'Анна Маркетолог', department: 'Маркетинг' }, { id: 2, name: 'Борис', department: 'СЗоВ' }];
    assert.deepEqual(filterPeople(people, 'сзов').map((p) => p.id), [2]);
});

test('плитки разделов: «Все», разделы по порядку, «Без раздела» — только если там что-то есть', () => {
    const groups = [{ id: 7, title: 'Общий маркетинг' }, { id: 9, title: 'SMM' }];
    const accounts = [{ id: 1, group_id: 7 }, { id: 2, group_id: 7 }, { id: 3, group_id: 9 }, { id: 4, group_id: null }];
    assert.deepEqual(sectionTiles(accounts, groups).map((t) => [t.key, t.count]), [
        [ALL_SECTIONS, 4], ['7', 2], ['9', 1], [UNGROUPED, 1],
    ]);
    assert.equal(sectionTiles(accounts.slice(0, 3), groups).some((t) => t.key === UNGROUPED), false);
    // Счётчики — по найденному поиском, а плитка «Без раздела» не пропадает, пока там есть аккаунты.
    const matched = [accounts[2]];
    assert.deepEqual(sectionTiles(accounts, groups, matched).map((t) => [t.key, t.count]), [
        [ALL_SECTIONS, 1], ['7', 0], ['9', 1], [UNGROUPED, 0],
    ]);
});

test('монограмма аккаунта: буква сервиса, одинаковый сервис — одинаковый цвет', () => {
    const google = monogramOf({ service: 'Google', title: 'Google Ads — кабинет' });
    assert.equal(google.letter, 'G');
    assert.deepEqual(monogramOf({ service: 'google', title: 'другое' }), google);
    assert.ok(MONOGRAM_TONES.includes(google.tone));
    assert.equal(monogramOf({ service: null, title: '  «Яндекс Директ»' }).letter, 'Я');
    assert.equal(monogramOf({}).letter, '•');
});

test('строка журнала: значок и цвет по виду события', () => {
    assert.deepEqual(journalKind('code_failed'), { icon: 'alert', tone: 'red' });
    assert.deepEqual(journalKind('approved'), { icon: 'approve', tone: 'green' });
    assert.deepEqual(journalKind('group_created'), { icon: 'folder', tone: 'blue' });
    assert.deepEqual(journalKind('что-то новое'), { icon: 'dot', tone: 'slate' });
});

test('край ленты плиток гаснет только там, где за ним что-то есть; розового в монограммах нет', () => {
    assert.equal(edgeMask(false, false), undefined);
    const right = edgeMask(false, true);
    assert.match(right.maskImage, /^linear-gradient\(to right, #000 0, #000 0px, #000 calc\(100% - 20px\), transparent 100%\)$/);
    assert.equal(right.WebkitMaskImage, right.maskImage);
    assert.match(edgeMask(true, false).maskImage, /transparent 0, #000 20px, .* #000 100%\)$/);
    assert.equal(MONOGRAM_TONES.some((tone) => tone.includes('rose')), false);
});
