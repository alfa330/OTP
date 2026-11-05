// «Доска почёта» (#318): правила окна тренера и доски.
// Тексты «чего не хватает» — те же, что у сервера (honor_board/catalog.py,
// tests/test_honor_board.py): окно краснеет сразу, не дожидаясь ответа.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    CUSTOM_MAX_NOMINATIONS, CUSTOM_NOMINATION, HONOR_DEPARTMENTS, MAIN_NOMINATION, blankEntry, customSlotsLeft,
    defaultDepartmentKey, entryHasContent, filterPeople, formatMoment, honorDisplayName, initialsOf, managesHonorBoard,
    nominationMeta,
    periodLabel, periodPhrase, publishIssues, serializeEntries, sortPeople, splitBoard,
} from '../src/components/events/honorBoardMeta.js';

test('имя на карточке — без отчества, двойные фамилии не трогаем', () => {
    const cases = [
        ['Рамазан Алдияр Кажымулы', 'Рамазан Алдияр'],
        ['Абенова Дана Маратовна', 'Абенова Дана'],
        ['Петров Илья Сергеевич', 'Петров Илья'],
        ['Смагулова Асель Бауыржанкызы', 'Смагулова Асель'],
        ['Ильина Ольга Кузьминична', 'Ильина Ольга'],
        ['Мамедов Эльдар Рашид оглы', 'Мамедов Эльдар'],
        ['Серикбаев Арман Нурлан улы', 'Серикбаев Арман'],
        ['Ким Виктория', 'Ким Виктория'],
        ['Римский-Корсаков Николай Андрей', 'Римский-Корсаков Николай Андрей'],
        ['  Мулдир   Юсупова ', 'Мулдир Юсупова'],
        ['', ''],
        [null, ''],
    ];
    for (const [full, short] of cases) assert.equal(honorDisplayName(full), short, String(full));
});

test('месяц словами', () => {
    assert.equal(periodLabel('2026-10'), 'Октябрь 2026');
    assert.equal(periodLabel('2026-01'), 'Январь 2026');
    assert.equal(periodPhrase('2026-09'), 'за сентябрь 2026');
    for (const bad of ['', '2026-13', '2026/10', null]) assert.equal(periodLabel(bad), '', String(bad));
});

test('время публикации — по-человечески', () => {
    const now = new Date(2026, 10, 4, 15, 0);
    assert.equal(formatMoment('2026-11-04T10:12:00', now), 'сегодня в 10:12');
    assert.equal(formatMoment('2026-11-03T09:05:00', now), '3 ноя в 09:05');
    assert.equal(formatMoment('2025-12-31T23:59:00', now), '31 дек 2025 в 23:59');
    assert.equal(formatMoment(null, now), '');
});

test('чего не хватает для публикации — как на сервере', () => {
    assert.deepEqual(publishIssues([blankEntry(MAIN_NOMINATION)]),
        [{ index: null, field: null, message: 'Выберите хотя бы одного победителя' }]);
    const entries = [
        { nomination: MAIN_NOMINATION, title: '', user_id: 4, name: 'Дана', description: 'Лучшая' },
        { nomination: CUSTOM_NOMINATION, title: 'Лучший кассир', user_id: null, name: '', description: '' },
        { nomination: CUSTOM_NOMINATION, title: '  ', user_id: 5, name: 'Ерлан', description: 'Быстрый' },
        { nomination: CUSTOM_NOMINATION, title: 'Душа офиса', user_id: null, name: '', description: 'Стих' },
    ];
    assert.deepEqual(publishIssues(entries), [
        { index: 2, field: 'title', message: 'Назовите номинацию' },
        { index: 3, field: 'user', message: 'Выберите сотрудника' },
        { index: 3, field: 'name', message: 'Впишите имя и фамилию' },
    ]);
    // Название с прошлого месяца без победителя — не начатая строка.
    assert.equal(entryHasContent(entries[1]), false);
    assert.equal(entryHasContent({ name: '  ' }), false);
});

test('свои номинации Фронт-офиса — до четырёх вместе с ключевой', () => {
    assert.equal(customSlotsLeft('front_office', [blankEntry(MAIN_NOMINATION), blankEntry(CUSTOM_NOMINATION)]), 2);
    assert.equal(customSlotsLeft('front_office', Array.from({ length: CUSTOM_MAX_NOMINATIONS }, () => ({}))), 0);
    assert.equal(customSlotsLeft('op', [blankEntry(MAIN_NOMINATION)]), 0);
});

test('на сервер уходят только поля записи', () => {
    assert.deepEqual(serializeEntries([
        { _key: 'honor-1', nomination: MAIN_NOMINATION, title: 'Лучший сотрудник отдела', user_id: 4, name: 'Дана', description: 'Стих' },
        { _key: 'honor-2', nomination: CUSTOM_NOMINATION, title: 'Лучший кассир', user_id: 0, name: '', description: '' },
    ]), [
        { nomination: MAIN_NOMINATION, title: '', user_id: 4, name: 'Дана', description: 'Стих' },
        { nomination: CUSTOM_NOMINATION, title: 'Лучший кассир', user_id: null, name: '', description: '' },
    ]);
});

test('номинация карточки: название из записи, значок и «за что» — из справочника', () => {
    assert.deepEqual(nominationMeta('op', 'master_yr', 'Мастер ЯР'), {
        title: 'Мастер ЯР', reason: 'за лучшие показатели в группе Яндекс Регистрации', icon: 'Car', main: false,
    });
    assert.equal(nominationMeta('szov', MAIN_NOMINATION).main, true);
    assert.equal(nominationMeta('szov', MAIN_NOMINATION).icon, 'Crown');
    // Своя номинация и убранная из справочника — своим названием и общим значком.
    assert.deepEqual(nominationMeta('front_office', CUSTOM_NOMINATION, 'Лучший кассир'),
        { title: 'Лучший кассир', reason: '', icon: 'Award', main: false });
    assert.equal(nominationMeta('op', 'retired', 'Старая').icon, 'Award');
});

test('ключевая карточка — отдельно, остальные — в порядке публикации', () => {
    const board = { entries: [
        { nomination: MAIN_NOMINATION, name: 'Дана' },
        { nomination: 'guru_sales', name: 'Арман' },
        { nomination: 'best_verifier', name: 'Виктория' },
    ] };
    const { main, others } = splitBoard(board);
    assert.equal(main.name, 'Дана');
    assert.deepEqual(others.map((entry) => entry.name), ['Арман', 'Виктория']);
    assert.deepEqual(splitBoard(null), { main: null, others: [] });
});

test('направление по умолчанию — своё', () => {
    assert.equal(defaultDepartmentKey({ department_code: 'szov' }), 'szov');
    assert.equal(defaultDepartmentKey({ departmentCode: 'FRONT_OFFICE' }), 'front_office');
    assert.equal(defaultDepartmentKey({ department_code: 'hr' }), HONOR_DEPARTMENTS[0].key);
    assert.equal(defaultDepartmentKey(null), 'op');
});

test('поиск и порядок сотрудников', () => {
    const people = [
        { id: 3, name: 'Ёлкина Вера', direction: 'Линия' },
        { id: 1, name: 'Жумабеков Ерлан', direction: 'Яндекс Регистрация' },
        { id: 2, name: 'Абенова Дана', direction: 'Основа' },
    ];
    assert.deepEqual(sortPeople(people).map((person) => person.id), [2, 3, 1]);
    assert.deepEqual(filterPeople(people, 'елк').map((person) => person.id), [3]);
    assert.deepEqual(filterPeople(people, 'регистрация ерлан').map((person) => person.id), [1]);
    assert.equal(filterPeople(people, '  ').length, 3);
    assert.equal(initialsOf('Абенова Дана'), 'АД');
    assert.equal(initialsOf(''), '?');
});

test('кто ведёт доску — догадка первого кадра', () => {
    assert.equal(managesHonorBoard({ role: 'trainer' }), true);
    assert.equal(managesHonorBoard({ role: 'Super Admin' }), true);
    assert.equal(managesHonorBoard({ role: 'superadmin' }), true);
    assert.equal(managesHonorBoard({ role: 'admin', headed_department_codes: ['front_office'] }), true);
    assert.equal(managesHonorBoard({ role: 'sv', headedDepartmentCode: 'FRONT_OFFICE' }), true);
    assert.equal(managesHonorBoard({ role: 'admin', headed_department_codes: ['op'] }), false);
    assert.equal(managesHonorBoard({ role: 'operator' }), false);
    assert.equal(managesHonorBoard(null), false);
});
