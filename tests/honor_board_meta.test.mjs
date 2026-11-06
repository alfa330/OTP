// «Доска почёта» (#318): правила окна тренера и доски.
// Тексты «чего не хватает» — те же, что у сервера (honor_board/catalog.py,
// tests/test_honor_board.py): окно краснеет сразу, не дожидаясь ответа.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    CUSTOM_NOMINATION, HONOR_DEPARTMENTS, HONOR_TONES, MAIN_NOMINATION, MAIN_TONE, MAX_NOMINATIONS, blankEntry,
    boardPlace, defaultDepartmentKey, entryHasContent, entryPhoto, filterPeople, formatMoment, honorDisplayName,
    initialsOf, isPeriodKey, managesHonorBoard, nominationMeta, nominationSlotsLeft, periodLabel, periodPhrase,
    boardLayout, pickDepartment, publishIssues, serializeEntries, sortPeople, splitBoard, tileLayout, toneOf,
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
    assert.equal(isPeriodKey('2026-10'), true);
    assert.equal(isPeriodKey('2026-13'), false);
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

test('номинаций у любого направления — до восьми вместе с ключевой', () => {
    assert.equal(MAX_NOMINATIONS, 8);
    assert.equal(nominationSlotsLeft([blankEntry(MAIN_NOMINATION), blankEntry(CUSTOM_NOMINATION)]), 6);
    assert.equal(nominationSlotsLeft(Array.from({ length: MAX_NOMINATIONS }, () => ({}))), 0);
    assert.equal(nominationSlotsLeft(Array.from({ length: MAX_NOMINATIONS + 2 }, () => ({}))), 0);
    assert.equal(nominationSlotsLeft(null), MAX_NOMINATIONS);
});

test('на сервер уходят только поля записи', () => {
    assert.deepEqual(serializeEntries([
        { _key: 'honor-1', nomination: MAIN_NOMINATION, title: 'Лучший сотрудник отдела', icon: 'Star', user_id: 4,
            name: 'Дана', description: 'Стих', photo_id: 7 },
        { _key: 'honor-2', nomination: CUSTOM_NOMINATION, title: 'Лучший кассир', icon: 'Gem', user_id: 0, name: '',
            description: '', photo_id: 9 },
        { _key: 'honor-3', nomination: CUSTOM_NOMINATION, title: 'Своя', icon: 'Skull', user_id: 5, name: 'Ерлан',
            description: 'Стих' },
    ]), [
        // У ключевой название и значок — не тренера: корона и название ТЗ.
        { nomination: MAIN_NOMINATION, title: '', icon: 'Crown', user_id: 4, name: 'Дана', description: 'Стих', photo_id: 7 },
        // Фото без человека не уходит.
        { nomination: CUSTOM_NOMINATION, title: 'Лучший кассир', icon: 'Gem', user_id: null, name: '', description: '', photo_id: null },
        // Значок не из списка — общий.
        { nomination: CUSTOM_NOMINATION, title: 'Своя', icon: 'Award', user_id: 5, name: 'Ерлан', description: 'Стих', photo_id: null },
    ]);
    assert.deepEqual(blankEntry(CUSTOM_NOMINATION, 'Звезда'), {
        nomination: CUSTOM_NOMINATION, title: 'Звезда', icon: 'Award', user_id: null, name: '', description: '', photo_id: null,
    });
    assert.equal(blankEntry(MAIN_NOMINATION).icon, 'Crown');
});

test('номинация карточки: название и значок из записи, «за что» — у ключевой и у номинации ТЗ', () => {
    assert.deepEqual(nominationMeta('op', CUSTOM_NOMINATION, 'Мастер ЯР', 'Car'), {
        title: 'Мастер ЯР', reason: 'за лучшие показатели в группе Яндекс Регистрации', icon: 'Car', main: false,
    });
    const main = nominationMeta('szov', MAIN_NOMINATION, '', 'Star');
    assert.deepEqual([main.main, main.icon, main.title], [true, 'Crown', 'Лучший сотрудник отдела']);
    assert.match(main.reason, /ключевым метрикам/);
    // Переименованная номинация ТЗ и своя — без «за что», значок из записи или общий.
    assert.deepEqual(nominationMeta('op', CUSTOM_NOMINATION, 'Король ЯР', 'Rocket'),
        { title: 'Король ЯР', reason: '', icon: 'Rocket', main: false });
    assert.deepEqual(nominationMeta('front_office', CUSTOM_NOMINATION, 'Лучший кассир'),
        { title: 'Лучший кассир', reason: '', icon: 'Award', main: false });
    assert.equal(nominationMeta('szov', CUSTOM_NOMINATION, 'Мастер ЯР', 'Car').reason, '');
});

test('фото карточки: своё для доски, иначе из профиля, иначе инициалы', () => {
    const honor = { 7: { url: 'https://x/7.webp', cutout: true }, 8: { url: 'https://x/8.webp', cutout: false }, 9: { url: '' } };
    const avatars = { 4: 'https://x/a4.webp' };
    assert.deepEqual(entryPhoto({ user_id: 4, photo_id: 7 }, honor, avatars), { kind: 'cutout', url: 'https://x/7.webp', ratio: 0 });
    assert.deepEqual(entryPhoto({ user_id: 4, photo_id: 8 }, honor, avatars), { kind: 'photo', url: 'https://x/8.webp', ratio: 0 });
    // Пропорции снимка — для рамки фото с фоном.
    assert.equal(entryPhoto({ user_id: 4, photo_id: 5 }, { 5: { url: 'u', cutout: false, width: 1067, height: 1600 } }).ratio,
        1067 / 1600);
    // Ссылка на фото не подписалась — фото профиля.
    assert.deepEqual(entryPhoto({ user_id: 4, photo_id: 9 }, honor, avatars), { kind: 'avatar', url: 'https://x/a4.webp' });
    assert.deepEqual(entryPhoto({ user_id: 4, photo_id: 99 }, honor, avatars), { kind: 'avatar', url: 'https://x/a4.webp' });
    assert.deepEqual(entryPhoto({ user_id: 5 }, honor, avatars), { kind: 'initials', url: '' });
    assert.deepEqual(entryPhoto(null), { kind: 'initials', url: '' });
});

test('цвет плитки: ключевая — золото, остальные — по месту на доске; окно красит строку так же', () => {
    assert.equal(toneOf({ nomination: MAIN_NOMINATION }, 3), MAIN_TONE);
    assert.equal(toneOf({ nomination: CUSTOM_NOMINATION }, 0), HONOR_TONES[0]);
    assert.equal(toneOf({ nomination: CUSTOM_NOMINATION }, HONOR_TONES.length + 1), HONOR_TONES[1]);
    // Все цвета — утилиты Tailwind: их перекрашивает тёмный слой портала.
    for (const tone of [...HONOR_TONES, MAIN_TONE]) {
        assert.match(tone.tile, /^bg-[a-z]+-100$/);
        assert.match(tone.ink, /^text-[a-z]+-700$/);
    }
    // На доску выходят только заполненные номинации: пустая вторая строка не сдвигает цвет третьей.
    const entries = [
        { nomination: MAIN_NOMINATION, user_id: 1 },
        { nomination: CUSTOM_NOMINATION, title: 'Пустая' },
        { nomination: CUSTOM_NOMINATION, title: 'Первая на доске', user_id: 2 },
        { nomination: CUSTOM_NOMINATION, title: 'Вторая', name: 'Ерлан' },
    ];
    assert.deepEqual(entries.map((_, index) => boardPlace(entries, index)), [0, 0, 0, 1]);
});

test('ключевая карточка — отдельно, остальные — в порядке публикации', () => {
    const board = { entries: [
        { nomination: CUSTOM_NOMINATION, name: 'Арман' },
        { nomination: MAIN_NOMINATION, name: 'Дана' },
        { nomination: CUSTOM_NOMINATION, name: 'Виктория' },
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

test('направление после зова колокола: своё среди вышедших, иначе первое вышедшее; без зова — где есть доска', () => {
    const boards = { op: {}, szov: {}, front_office: {} };
    // Колокол звал про Фронт-офис — оператор ОП попадает на Фронт-офис, а не на свою доску.
    assert.equal(pickDepartment('op', boards, ['front_office']), 'front_office');
    // Своё среди вышедших — своё.
    assert.equal(pickDepartment('szov', boards, ['op', 'szov']), 'szov');
    assert.equal(pickDepartment('front_office', boards, ['op', 'szov']), 'op');
    // Звал про доску, которую уже сняли, — обычное правило.
    assert.equal(pickDepartment('op', { op: {} }, ['szov']), 'op');
    // Без зова: у своего направления доски нет — первое, где есть.
    assert.equal(pickDepartment('front_office', { szov: {} }), 'szov');
    assert.equal(pickDepartment('op', {}, []), 'op');
    assert.equal(pickDepartment('op', null, null), 'op');
});

test('плитки во всю ширину доски: ряды ровные, не меньше двух в ряд, рост от плитки', () => {
    const cols = (width, count) => tileLayout(width, count).columns;
    // Три номинации на широкой доске — три колонки во всю ширину, а не три из четырёх.
    assert.equal(cols(1240, 3), 3);
    assert.equal(cols(1680, 3), 3);
    // Ряды выравниваются: 5 — 3 + 2, 7 — 4 + 3, 8 — 4 + 4.
    assert.equal(cols(1240, 5), 3);
    assert.equal(cols(1240, 7), 4);
    assert.equal(cols(1240, 8), 4);
    // «Минимум две плиты на одной строке» — и на узкой доске, и на телефоне, и у
    // единственной номинации (она — в половину ряда).
    assert.equal(cols(700, 3), 2);
    assert.equal(cols(358, 3), 2);
    assert.equal(cols(358, 1), 2);
    assert.equal(cols(1240, 1), 2);
    for (const width of [320, 358, 390, 700, 1024, 1440, 2560]) {
        for (const count of [1, 2, 3, 5, 7]) {
            const layout = tileLayout(width, count);
            assert.ok(layout.tileWidth <= (width - layout.gap) / 2 + 0.001, `${width}/${count}`);
        }
    }
    // Ширина плитки — ровно доля ряда без промежутков: края ряда = края карточки.
    const layout = tileLayout(1240, 3);
    assert.equal(layout.tileWidth * 3 + 20 * 2, 1240);
    // На телефоне промежуток меньше: две плитки по ~180 px.
    const phone = tileLayout(390, 3);
    assert.equal(phone.gap, 12);
    assert.equal(phone.tileWidth, 189);
    // Рост — от ширины: узкая выше своей ширины, широкая ниже; в пределах 240–380.
    assert.equal(layout.height, 350);
    assert.equal(phone.height, 255);
    assert.equal(tileLayout(1680, 1).height, 380);
    // До замера (ширина 0) — заготовка, а не деление на ноль.
    assert.deepEqual(tileLayout(0, 5), { columns: 3, tileWidth: 0, height: 320, gap: 20 });
});

test('одна-две свои номинации — одним рядом с золотой карточкой, больше — рядами под ней', () => {
    // 1430 px: влезает 4 колонки; одна своя — ряд «фото · текст · плитка», равные колонки.
    const one = boardLayout(1430, 1);
    assert.equal(one.inline, true);
    assert.equal(one.columns, 3);
    assert.equal(one.mainWidth + 20 + one.tileWidth, 1430);
    assert.equal(one.mainPhotoWidth, one.tileWidth);
    // Две — тоже рядом (4 колонки), три — уже рядами под карточкой.
    assert.equal(boardLayout(1430, 2).inline, true);
    assert.equal(boardLayout(1430, 2).columns, 4);
    const three = boardLayout(1430, 3);
    assert.deepEqual([three.inline, three.columns], [false, 3]);
    assert.equal(three.mainPhotoWidth, three.tileWidth);
    // Узкая доска (влезает 2 колонки): одна своя — под карточкой в половину ряда,
    // фото карточки — шириной в эту половину.
    const narrow = boardLayout(700, 1);
    assert.deepEqual([narrow.inline, narrow.columns], [false, 2]);
    assert.equal(narrow.mainPhotoWidth, (700 - 20) / 2);
    // Телефон: плитки по две в ряд, фото золотой карточки над текстом.
    const phone = boardLayout(390, 2);
    assert.deepEqual([phone.inline, phone.columns, phone.mainPhotoWidth], [false, 2, 0]);
    // Только ключевая — карточка во всю ширину, фото — как плитка из ряда в три.
    assert.equal(boardLayout(1240, 0).inline, false);
    assert.equal(boardLayout(1240, 0).mainPhotoWidth, (1240 - 40) / 3);
});
