/*
 * Раздел «Удаленный КЦ», вкладка «Линии»: кого предлагать при назначении линии.
 *
 * Запрос владельца 07.10.2026: «добавлять на линию любого сотрудника по ФИО, доступ у
 * главы отдела СЗоВ и у суперадминов». Право считает сервер (can_seat_anyone) и вместе
 * с ним отдаёт сотрудников других отделов (candidates). Здесь проверяется, что список
 * выбора раскладывается по этому признаку, а без него остаётся прежним — только
 * сотрудники отдела линии. И мелкие правила того же раздела из linePicker.js:
 * сообщение после назначения, операторы для фильтра журнала, сверка названия отдела
 * с названием раздела. Люди в тесте выдуманы.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    NO_DEPARTMENT_GROUP, assignedToast, buildLinePickerOptions, canReleaseHolder, defaultPickedUser,
    operatorsForFilter, sameSectionTitle,
} from '../src/components/dial_list/linePicker.js';

// Без переводов строк Windows: сверки исходника ниже многострочные.
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').split('\r\n').join('\n');

/* users из GET /api/dial_list/departments/<id>/lines: свои и уже сидящие на линиях. */
const OWN_FREE = { id: 10, name: 'Своев Сава', login: 'sava', sip_number: '', guest: false, department_name: '' };
const OWN_SEATED = { id: 11, name: 'Аверин Аким', login: 'akim', sip_number: '905', guest: false, department_name: '' };
const GUEST_SEATED = { id: 77, name: 'Гостев Глеб', login: 'gleb', sip_number: '906', guest: true, department_name: 'СЗоВ' };
const USERS = [OWN_FREE, OWN_SEATED, GUEST_SEATED];

/* candidates: сотрудники других отделов, которых на линиях отдела ещё нет. */
const CANDIDATES = [
    { id: 90, name: 'Яковлев Ян', login: 'yan', department_name: 'СЗоВ' },
    { id: 91, name: 'Борисов Богдан', login: 'bogdan', department_name: 'Отдел продаж' },
    { id: 92, name: 'Никто Ничей', login: '', department_name: '' },
    { id: 93, name: 'Абрамов Артём', login: 'artem', department_name: 'СЗоВ' },
];

/* Поиск в общем списке выбора (src/components/ui/CustomSelect.jsx): подстрока в подписи. */
const search = (options, query) => options
    .filter((o) => String(o.label ?? '').toLowerCase().includes(query.trim().toLowerCase()))
    .map((o) => o.value);

test('без права сажать чужих список прежний: только сотрудники отдела линии', () => {
    const options = buildLinePickerOptions({ users: USERS, candidates: CANDIDATES, canSeatAnyone: false, departmentName: 'Удаленный КЦ' });
    assert.deepEqual(options, [
        { value: '11', label: 'Аверин Аким (@akim)', name: 'Аверин Аким', meta: 'линия 905' },
        { value: '10', label: 'Своев Сава (@sava)', name: 'Своев Сава' },
    ]);
    // Ни чужих сотрудников, ни заголовков отделов: сервер их всё равно не посадит.
    assert.equal(options.some((o) => 'groupLabel' in o), false);
});

test('с правом — свои первыми, чужие по отделам и по алфавиту', () => {
    const options = buildLinePickerOptions({ users: USERS, candidates: CANDIDATES, canSeatAnyone: true, departmentName: 'Удаленный КЦ' });
    assert.deepEqual(options.map((o) => [o.groupLabel, o.label, o.meta ?? '']), [
        ['Удаленный КЦ', 'Аверин Аким (@akim)', 'линия 905'],
        ['Удаленный КЦ', 'Своев Сава (@sava)', ''],
        ['Отдел продаж', 'Борисов Богдан (@bogdan)', ''],
        ['СЗоВ', 'Абрамов Артём (@artem)', ''],
        // Уже сидящий на линии чужой сотрудник стоит в своём отделе, с номером линии:
        // назначение пересадит его.
        ['СЗоВ', 'Гостев Глеб (@gleb)', 'линия 906'],
        ['СЗоВ', 'Яковлев Ян (@yan)', ''],
        [NO_DEPARTMENT_GROUP, 'Никто Ничей', ''],
    ]);
    // Значения — строки: CustomSelect сравнивает выбранное по строкам.
    assert.ok(options.every((o) => typeof o.value === 'string'));
});

test('полные тёзки различимы по логину, и находить можно и по ФИО, и по логину', () => {
    // Находка разбора 07.10.2026: из пунктов пропал логин — двух «Иванов Иван Иванович»
    // в одном отделе было не отличить, а поиск по логину перестал работать.
    const twins = [
        { id: 1, name: 'Иванов Иван Иванович', login: 'ivanov_i', department_name: 'СЗоВ' },
        { id: 2, name: 'Иванов Иван Иванович', login: 'ivanov_i2', department_name: 'СЗоВ' },
    ];
    const options = buildLinePickerOptions({ users: [], candidates: twins, canSeatAnyone: true });
    assert.notEqual(options[0].label, options[1].label);
    assert.deepEqual(search(options, 'иванов ив'), ['1', '2']);
    assert.deepEqual(search(options, 'ivanov_i2'), ['2']);
    // Имя без логина — отдельно: им подписывается сообщение после назначения.
    assert.deepEqual(options.map((o) => o.name), ['Иванов Иван Иванович', 'Иванов Иван Иванович']);
    // Человек без имени не теряется: подпись по id.
    assert.deepEqual(buildLinePickerOptions({ users: [{ id: 5, login: 'x' }] }).map((o) => [o.label, o.name]),
        [['#5 (@x)', '#5']]);
});

test('группы идут подряд: заголовок отдела не повторяется', () => {
    const options = buildLinePickerOptions({ users: USERS, candidates: CANDIDATES, canSeatAnyone: true, departmentName: 'Удаленный КЦ' });
    const starts = options.filter((o, i) => o.groupLabel !== options[i - 1]?.groupLabel).map((o) => o.groupLabel);
    assert.deepEqual(starts, ['Удаленный КЦ', 'Отдел продаж', 'СЗоВ', NO_DEPARTMENT_GROUP]);
});

test('один человек — одна строка, даже если пришёл в обоих списках', () => {
    const options = buildLinePickerOptions({
        users: USERS,
        candidates: [...CANDIDATES, { id: 77, name: 'Гостев Глеб', department_name: 'СЗоВ' }, { id: 10, name: 'Своев Сава', department_name: 'СЗоВ' }],
        canSeatAnyone: true,
        departmentName: 'Удаленный КЦ',
    });
    const values = options.map((o) => o.value);
    assert.equal(new Set(values).size, values.length);
    // Свой сотрудник остаётся в группе своего отдела, сидящий гость — с номером линии.
    assert.equal(options.find((o) => o.value === '10').groupLabel, 'Удаленный КЦ');
    assert.equal(options.find((o) => o.value === '77').meta, 'линия 906');
});

test('название отдела линии не пришло — у своих всё равно есть заголовок', () => {
    const options = buildLinePickerOptions({ users: USERS, candidates: CANDIDATES, canSeatAnyone: true });
    assert.ok(options[0].groupLabel);
    assert.notEqual(options[0].groupLabel, 'СЗоВ');
});

test('непонятный ответ ручки не роняет вкладку', () => {
    for (const payload of [undefined, {}, { users: null, candidates: 'oops', canSeatAnyone: true },
        { users: [null, undefined, { name: 'без id' }], candidates: [null, { name: 'без id' }], canSeatAnyone: true }]) {
        assert.deepEqual(buildLinePickerOptions(payload), []);
    }
    assert.deepEqual(buildLinePickerOptions({ users: [{ id: 5 }], canSeatAnyone: false }),
        [{ value: '5', label: '#5', name: '#5' }]);
});

test('по умолчанию подставляется свободный сотрудник отдела линии, а не чужой', () => {
    assert.equal(defaultPickedUser(USERS), '10');
    assert.equal(defaultPickedUser([OWN_SEATED, GUEST_SEATED]), '');
    // Чужой без линии в users не бывает, но и окажись он там — первым его не подставляем.
    assert.equal(defaultPickedUser([{ id: 5, name: 'Чужой', guest: true, sip_number: '' }]), '');
    assert.equal(defaultPickedUser(null), '');
});

test('сотрудника другого отдела снимает только тот, кто вправе его сажать', () => {
    const guest = { id: 77, name: 'Гостев Глеб', guest: true, department_name: 'СЗоВ' };
    const own = { id: 11, name: 'Аверин Аким', guest: false };
    assert.equal(canReleaseHolder(own, false), true);
    assert.equal(canReleaseHolder(own, true), true);
    assert.equal(canReleaseHolder(guest, false), false);
    assert.equal(canReleaseHolder(guest, true), true);
    assert.equal(canReleaseHolder(null, true), false);
});

test('после назначения сотруднику другого отдела сказано, откуда взять программу', () => {
    // Кнопка «Скачать iCore Phone» приходит с профилем, то есть после обновления
    // страницы: без этого руководитель велел бы войти в программу, которой у человека нет.
    const guest = assignedToast('903', 'Гостев Глеб', true);
    assert.ok(guest.startsWith('Линия 903 назначена: Гостев Глеб.'));
    assert.ok(guest.includes('«Скачать iCore Phone»') && guest.includes('после обновления страницы'));
    assert.ok(guest.includes('логином iCORE'));
    assert.equal(assignedToast('902', 'Своев Сава', false),
        'Линия 902 назначена: Своев Сава. Сотруднику нужно войти в iCORE Phone заново');
});

test('фильтр журнала: нынешний состав и те, кто обзванивал базу раньше', () => {
    // Находка разбора: снятый с линии сотрудник другого отдела пропадал из фильтра
    // «Оператор», хотя его звонки остаются в показателях отдела.
    const data = {
        users: [{ id: 10, name: 'Своев Сава' }, { id: 77, name: 'Гостев Глеб', guest: true }],
        former: [{ id: 78, name: 'Бывшев Борис' }, { id: 10, name: 'Своев Сава' }, null, { name: 'без id' }],
    };
    assert.deepEqual(operatorsForFilter(data), [
        { id: 10, name: 'Своев Сава' },
        { id: 77, name: 'Гостев Глеб', guest: true },
        { id: 78, name: 'Бывшев Борис', former: true },
    ]);
    // Старый сервер former не присылает — список прежний.
    assert.deepEqual(operatorsForFilter({ users: data.users }), data.users);
    assert.deepEqual(operatorsForFilter(null), []);
});

test('название отдела, совпавшее с названием раздела, в шапке не повторяется', () => {
    assert.equal(sameSectionTitle('Удаленный КЦ', 'Удаленный КЦ'), true);
    assert.equal(sameSectionTitle(' Удалённый кц ', 'Удаленный КЦ'), true);   // ё и регистр
    assert.equal(sameSectionTitle('Удаленный КЦ — Астана', 'Удаленный КЦ'), false);
    assert.equal(sameSectionTitle('', ''), false);
    assert.equal(sameSectionTitle(undefined, 'Удаленный КЦ'), false);
});

test('вкладка «Линии» берёт право и список с сервера и раскладывает их этим модулем', () => {
    const panel = read('../src/components/dial_list/DialListLinesPanel.jsx');
    assert.ok(panel.includes("import { assignedToast, buildLinePickerOptions, canReleaseHolder, defaultPickedUser } from './linePicker';"));
    assert.ok(panel.includes('const canSeatAnyone = data?.can_seat_anyone === true;'));
    assert.ok(panel.includes('users: data?.users, candidates: data?.candidates, canSeatAnyone, departmentName,'));
    assert.ok(panel.includes('options={pickerOptions}'));
    // Поиск по ФИО включается на длинном списке — а со всей компанией он длинный всегда.
    assert.ok(panel.includes('searchable={pickerOptions.length > 8}'));
    assert.ok(panel.includes('searchPlaceholder="Поиск по ФИО"'));
    // По умолчанию — свободный сотрудник отдела, а не первый пункт (он может сидеть на линии).
    assert.ok(panel.includes('setPickedUser(defaultPickedUser(users))'));
    assert.ok(panel.includes('canReleaseHolder(line.icore_user, canSeatAnyone) && ('));
    // Чей сотрудник на линии — видно по подписи отдела.
    assert.ok(panel.includes('{line.icore_user.guest && line.icore_user.department_name && ('));
    // Сообщение: имя без логина и признак сотрудника другого отдела — из ответа сервера.
    assert.ok(panel.includes("const name = pickerOptions.find((o) => o.value === String(pickedUser))?.name || '';"));
    assert.ok(panel.includes('toast(assignedToast(line.internal_number, name, result?.guest === true), \'success\');'));
    // Своей копии правила «кто вправе» на фронте нет: ни ролей, ни кодов отделов.
    assert.equal(/super_admin|szov/i.test(panel), false);
    const view = read('../src/components/dial_list/DialListView.jsx');
    assert.ok(view.includes('departmentName={selected.department_name}'));
    assert.ok(/<DialListLinesPanel\s+key=\{selected\.department_id\}/.test(view));
    const journal = read('../src/components/dial_list/DialListJournal.jsx');
    assert.ok(journal.includes("import { operatorsForFilter } from './linePicker';"));
    assert.ok(journal.includes('if (!cancelled) setUsers(operatorsForFilter(data));'));
    assert.ok(journal.includes('...(u.former ? { muted: true } : {})'));
});
