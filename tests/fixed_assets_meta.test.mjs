/*
 * «Основные средства» (ACC-1): правила экрана без React — что форма считает «не хватает», как
 * называет состояние ОС и запроса, как пишет даты и строки истории. Тексты пунктов — те же, что
 * у сервера (fixed_assets/rules.py); подписи кодов сверяет
 * tests/test_fixed_assets_rules.py::FrontendMirrorTests.
 *
 * Запуск: node --test tests/fixed_assets_meta.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
    ASSET_SLICES, BOARD_COLUMNS, EVENT_OPTIONS, MOVE_STEPS, PERIOD_OPTIONS, PHOTO_EXTRA, PHOTO_NUMBER, PHOTO_WHOLE, assetFlag,
    assetState, boardColumns, dropTargets, stepTarget,
    blankRequest, blankRequisites, countText, eventFiles, eventLines, fileSizeLabel, fmtDate, fmtDateTime, fmtDay, fmtDayLong,
    fmtMoney, fmtTime, groupByDay, hasAssets, initials, intakeIssues, isMoving, periodRange, photoGapText, photoGaps, placeText,
    plural, requestIssues, requestPayload, requestSubject, requestTitle, requestToForm, requisitesPayload, routeText, shiftDay,
    shortName, todayIso,
} from '../src/components/fixed_assets/fixedAssetsMeta.js';
import * as meta from '../src/components/fixed_assets/fixedAssetsMeta.js';

const LAPTOP = { id: 1, name: 'Ноутбук Lenovo' };
const CHAIR = { id: 2, name: 'Кресло' };
const SHOT = { file: {} };

test('даты режутся из строки сервера, а не пересчитываются в пояс браузера', () => {
    // Сервер отдаёт настенное время Алматы без пояса: «23:30» обязано остаться «23:30» в любом браузере.
    assert.equal(fmtDateTime('2026-11-17T23:30:15'), '17.11.2026 23:30');
    assert.equal(fmtDate('2026-11-17'), '17.11.2026');
    assert.equal(fmtDate(''), '');
    assert.equal(fmtDateTime('2026-11-17'), '17.11.2026');
    assert.equal(fmtDay('2026-11-05T09:00:00', '2026-11-17'), '5 ноя');
    assert.equal(fmtDay('2025-12-31T09:00:00', '2026-11-17'), '31 дек 2025');
    assert.equal(fmtTime('2026-11-17T23:30:15'), '23:30');
    assert.equal(fmtTime('2026-11-17'), '');
});

test('история собрана по дням: «Сегодня», «Вчера», дальше дата', () => {
    assert.equal(fmtDayLong('2026-11-17T08:00:00', '2026-11-17'), 'Сегодня');
    assert.equal(fmtDayLong('2026-11-16', '2026-11-17'), 'Вчера');
    assert.equal(fmtDayLong('2026-11-05', '2026-11-17'), '5 ноября');
    assert.equal(fmtDayLong('2025-12-31', '2026-11-17'), '31 декабря 2025');
    assert.equal(fmtDayLong('2026-12-31', '2027-01-01'), 'Вчера');           // через границу года
    const groups = groupByDay([
        { id: 3, created_at: '2026-11-17T12:00:00' }, { id: 2, created_at: '2026-11-17T09:00:00' },
        { id: 1, created_at: '2026-11-14T18:30:00' },
    ]);
    assert.deepEqual(groups.map((group) => [group.day, group.events.map((event) => event.id)]),
        [['2026-11-17', [3, 2]], ['2026-11-14', [1]]]);
    assert.deepEqual(groupByDay([]), []);
});

test('отрезки истории превращаются в даты для сервера', () => {
    assert.equal(shiftDay('2026-03-01', -1), '2026-02-28');
    assert.equal(shiftDay('2026-12-31', 1), '2027-01-01');
    assert.deepEqual(periodRange('today', '2026-11-17'), { date_from: '2026-11-17', date_to: '2026-11-17' });
    assert.deepEqual(periodRange('week', '2026-11-17'), { date_from: '2026-11-11', date_to: '2026-11-17' });
    assert.deepEqual(periodRange('month', '2026-11-17'), { date_from: '2026-10-19', date_to: '2026-11-17' });
    assert.deepEqual(periodRange('this_month', '2026-11-17'), { date_from: '2026-11-01', date_to: '2026-11-17' });
    assert.deepEqual(periodRange('last_month', '2026-01-10'), { date_from: '2025-12-01', date_to: '2025-12-31' });
    assert.deepEqual(periodRange('', '2026-11-17'), {});
    // Каждый пункт меню «Период» даёт отрезок.
    for (const option of PERIOD_OPTIONS) assert.ok(periodRange(option.value, '2026-11-17').date_from, option.value);
});

test('сегодня считается по Алматы: после 19:00 UTC там уже завтра', () => {
    assert.equal(todayIso(new Date('2026-11-17T18:59:00Z')), '2026-11-17');
    assert.equal(todayIso(new Date('2026-11-17T19:00:00Z')), '2026-11-18');
});

test('деньги, счёт и размеры файлов', () => {
    assert.equal(fmtMoney(389900), '389 900 ₸');
    assert.equal(fmtMoney('1234.5'), '1 234,50 ₸');
    assert.equal(fmtMoney(null), '');
    assert.equal(plural(1, ['запрос', 'запроса', 'запросов']), 'запрос');
    assert.equal(plural(3, ['запрос', 'запроса', 'запросов']), 'запроса');
    assert.equal(plural(11, ['запрос', 'запроса', 'запросов']), 'запросов');
    assert.equal(countText(22, ['запись', 'записи', 'записей']), '22 записи');
    assert.equal(fileSizeLabel(110 * 1024), '110 КБ');
    assert.equal(fileSizeLabel(1.5 * 1024 * 1024), '1,5 МБ');
});

test('ФИО в строках списков сокращается, одно слово остаётся как есть', () => {
    assert.equal(shortName('Ахметов Тимур Русланович'), 'Ахметов Т. Р.');
    assert.equal(shortName('Ли Сергей'), 'Ли С.');
    assert.equal(shortName('Ли'), 'Ли');
    assert.equal(shortName(''), '');
    assert.equal(initials('Ахметов Тимур Русланович'), 'АТ');
    assert.equal(initials('ли'), 'Л');
    assert.equal(initials(''), '');
});

test('состояние ОС одной меткой: списано → в запросе → на складе → на учёте', () => {
    assert.deepEqual(assetState({ status: 'written_off', open_request: { kind: 'transfer' } }), { label: 'Списано', tone: 'muted' });
    const busy = assetState({ status: 'active', on_stock: true, open_request: { id: 7, kind: 'writeoff' } });
    assert.equal(busy.label, 'Списание');
    assert.equal(busy.tone, 'amber');
    assert.equal(busy.request.id, 7);
    assert.deepEqual(assetState({ status: 'active', on_stock: true }), { label: 'На складе', tone: 'blue' });
    // Обычное состояние не красится: цвет — только там, где он что-то значит.
    assert.deepEqual(assetState({ status: 'active' }), { label: 'На учёте', tone: 'slate' });
    assert.equal(placeText({ on_stock: true, location: 'Алматы, каб. 3' }), 'Склад · Алматы, каб. 3');
    assert.equal(placeText({ on_stock: true, location: 'Алматы, склад IT' }), 'Алматы, склад IT');     // склад уже назван
    assert.equal(placeText({ on_stock: true }), 'Склад');
    assert.equal(placeText({ location: 'каб. 204' }), 'каб. 204');
});

test('в карточке списка метка есть только у необычного состояния', () => {
    // «На учёте» у каждого ОС читалось бы шумом — обычное ОС идёт без метки.
    assert.equal(assetFlag({ status: 'active' }), null);
    assert.equal(assetFlag({ status: 'active', on_stock: true }).label, 'На складе');
    assert.equal(assetFlag({ status: 'written_off' }).label, 'Списано');
    assert.equal(assetFlag({ status: 'active', open_request: { id: 7, kind: 'dismissal' } }).label, 'Передача');
    assert.equal(assetFlag({ status: 'active', open_request: { id: 7, kind: 'writeoff' } }).tone, 'amber');
});

test('плитки над реестром: первая — «все», у каждой свой счётчик', () => {
    assert.equal(ASSET_SLICES[0].key, '');
    assert.equal(ASSET_SLICES[0].counter, 'all');
    assert.equal(new Set(ASSET_SLICES.map((tile) => tile.key)).size, ASSET_SLICES.length);
    assert.ok(ASSET_SLICES.every((tile) => tile.label && tile.counter && tile.tone));
});

test('доска запросов: колонка на статус, в порядке пути запроса', () => {
    // Статусы — те же, что у сервера (rules.STATUSES), и в том же порядке.
    assert.deepEqual(BOARD_COLUMNS.map((column) => column.status), ['draft', 'review', 'signing', 'original', 'closed', 'rejected']);
    assert.ok(BOARD_COLUMNS.every((column) => column.title && column.tone));
    const shown = (totals, options) => boardColumns(totals, options).map((column) => column.status);
    // Черновики — только когда они есть; остальные колонки стоят и пустыми.
    assert.deepEqual(shown({}), ['review', 'signing', 'original', 'closed', 'rejected']);
    assert.deepEqual(shown({ draft: 2 }), ['draft', 'review', 'signing', 'original', 'closed', 'rejected']);
    // «Ждут меня»: закрытых и отклонённых нет — их не ждут.
    assert.deepEqual(shown({ draft: 1, closed: 4 }, { waiting: true }), ['draft', 'review', 'signing', 'original']);
    // Пока карточку несут, колонка, куда её можно бросить, видна и в «Ждут меня».
    assert.deepEqual(shown({}, { waiting: true, extra: ['rejected'] }), ['review', 'signing', 'original', 'rejected']);
});

test('перетаскивание: колонка принимает карточку, только если есть шаг', () => {
    assert.deepEqual(MOVE_STEPS, ['submit', 'check', 'accept', 'scan', 'receive', 'reject']);
    assert.equal(stepTarget('submit', 'writeoff'), 'review');
    assert.equal(stepTarget('submit', 'need'), 'review');
    assert.equal(stepTarget('submit', 'transfer'), 'signing');
    assert.equal(stepTarget('submit', 'dismissal'), 'signing');
    assert.equal(stepTarget('check', 'writeoff'), 'signing');
    assert.equal(stepTarget('accept', 'need'), 'closed');
    assert.equal(stepTarget('scan', 'intake'), 'original');
    assert.equal(stepTarget('receive', 'transfer'), 'closed');
    assert.equal(stepTarget('reject', 'transfer'), 'rejected');
    assert.equal(stepTarget('act', 'transfer'), null);
    // Бухгалтерия на «Ждёт оригинал»: в «Закрыты» — оригиналом, в «Отклонены» — отказом.
    assert.deepEqual(dropTargets({ kind: 'transfer', status: 'original', actions: ['act', 'receive', 'reject', 'rescan'] }),
        { closed: 'receive', rejected: 'reject' });
    // Черновик списания уходит на проверку, черновик перемещения — сразу на подписание.
    assert.deepEqual(dropTargets({ kind: 'writeoff', status: 'draft', actions: ['edit', 'submit', 'delete'] }), { review: 'submit' });
    assert.deepEqual(dropTargets({ kind: 'transfer', status: 'draft', actions: ['edit', 'submit', 'delete'] }), { signing: 'submit' });
    // У сотрудника с ОС шагов нет — его карточки не перетаскиваются никуда.
    assert.deepEqual(dropTargets({ kind: 'transfer', status: 'signing', actions: ['act'] }), {});
    assert.deepEqual(dropTargets({ kind: 'transfer', status: 'signing' }), {});
    assert.deepEqual(dropTargets(null), {});
});

test('шага «вызвать HR» на экране нет', () => {
    // Решение владельца 17.11.2026: передачу при увольнении запускает сам HR.
    assert.ok(!EVENT_OPTIONS.some((option) => option.value === 'hr_called'));
    assert.equal(meta.waitingText, undefined);          // и подсказок «ваш шаг» тоже
    assert.deepEqual(eventLines({ kind: 'hr_called', payload: { user_name: 'Кто-то' } }), []);
});

test('строка «кто — кому» по виду запроса', () => {
    assert.equal(routeText({ kind: 'transfer', from_name: 'Ахметов Тимур Русланович', to_name: 'Садыкова Камила Ринатовна' }),
        'Ахметов Т. Р. → Садыкова К. Р.');
    assert.equal(routeText({ kind: 'dismissal', from_name: 'Байжанов Арман', to_name: 'Омаров Данияр', to_stock: true }),
        'Байжанов А. → Омаров Д. (склад)');
    assert.equal(routeText({ kind: 'writeoff', from_name: 'Ахметов Тимур Русланович' }), 'МОЛ: Ахметов Т. Р.');
    assert.equal(routeText({ kind: 'intake', to_name: 'Омаров Данияр Кайратович' }), 'МОЛ: Омаров Д. К.');
    assert.equal(routeText({ kind: 'need', need_for_name: 'Садыкова Камила Ринатовна' }), 'кому: Садыкова К. Р.');
    assert.equal(requestSubject({ kind: 'need', need_title: 'Ноутбук 15"' }), 'Ноутбук 15"');
    assert.equal(requestSubject({ kind: 'transfer', items_title: 'Стол, Кресло' }), 'Стол, Кресло');
    assert.equal(requestTitle({ kind: 'writeoff', id: 32 }), 'Списание № 32');
});

test('к каждому ОС нужны оба снимка — и причина отказа называет, какого нет', () => {
    const photos = { 1: { [PHOTO_WHOLE]: SHOT, [PHOTO_NUMBER]: SHOT }, 2: { [PHOTO_WHOLE]: SHOT, extra: [SHOT, SHOT] } };
    const gaps = photoGaps([LAPTOP, CHAIR], photos);
    assert.deepEqual(gaps.map((gap) => [gap.asset.id, gap.missing]), [[2, [PHOTO_NUMBER]]]);
    // Дополнительные снимки обязательный не заменяют.
    assert.equal(photoGaps([LAPTOP], { 1: { extra: [SHOT, SHOT, SHOT] } })[0].missing.length, 2);
    assert.equal(photoGapText([]), '');
    const text = photoGapText(photoGaps([LAPTOP, CHAIR], {}));
    assert.ok(text.startsWith('Не хватает фото: Ноутбук Lenovo — ОС целиком и инвентарный номер; Кресло — '));
    assert.ok(text.endsWith('К каждому ОС нужны два снимка: ОС целиком и инвентарный номер.'));
    const many = Array.from({ length: 5 }, (_, index) => ({ id: index + 1, name: `ОС ${index + 1}` }));
    assert.ok(photoGapText(photoGaps(many, {})).includes('и ещё у 2 ОС'));
});

test('чего не хватает запросу перед отправкой — по видам', () => {
    assert.deepEqual(Object.keys(requestIssues(blankRequest('transfer'), [])), ['asset_ids', 'to_user_id']);
    assert.deepEqual(requestIssues(blankRequest('transfer', { to_user_id: 5 }), [LAPTOP]), {});
    assert.deepEqual(Object.keys(requestIssues(blankRequest('writeoff'), [LAPTOP])), ['comment']);
    assert.deepEqual(Object.keys(requestIssues(blankRequest('dismissal'), [])), ['asset_ids', 'to_user_id', 'from_user_id']);
    assert.deepEqual(Object.keys(requestIssues(blankRequest('need'), [])), ['need_title', 'need_for_user_id', 'comment']);
    assert.deepEqual(requestIssues(blankRequest('need', { need_title: 'Монитор', need_for_user_id: 3, comment: 'Нужен' }), []), {});
    assert.equal(hasAssets('need'), false);
    assert.equal(isMoving('writeoff'), false);
    assert.equal(isMoving('dismissal'), true);
});

test('на сервер уходят только поля своего вида запроса', () => {
    const transfer = requestPayload(blankRequest('transfer', { asset_ids: [1, 2], to_user_id: 5, to_location: ' каб. 206 ', comment: ' Переезд ', need_title: 'лишнее' }));
    assert.deepEqual(transfer, { kind: 'transfer', comment: 'Переезд', asset_ids: [1, 2], to_user_id: 5, to_location: 'каб. 206', to_stock: false });
    const need = requestPayload(blankRequest('need', { need_title: ' Монитор ', need_for_user_id: 3, comment: 'Нужен', asset_ids: [9] }));
    assert.deepEqual(need, { kind: 'need', comment: 'Нужен', need_title: 'Монитор', need_category_id: null, need_for_user_id: 3 });
    assert.equal(requestPayload(blankRequest('dismissal', { from_user_id: 8, to_user_id: 7 })).from_user_id, 8);
});

test('черновик возвращается в форму теми же полями', () => {
    const form = requestToForm({
        request: { kind: 'transfer', to_user_id: 5, to_location: 'каб. 206', to_stock: true, comment: 'Переезд' },
        items: [{ asset_id: 4 }, { asset_id: 9 }],
    });
    assert.deepEqual(form.asset_ids, [4, 9]);
    assert.equal(form.to_stock, true);
    assert.equal(form.kind, 'transfer');
});

test('приём в учёт: название, категория, номер, МОЛ и оба фото', () => {
    const issues = intakeIssues({ name: ' ', category_id: null, inventory_number: '', to_user_id: null }, {});
    assert.deepEqual(Object.keys(issues), ['name', 'category_id', 'inventory_number', 'to_user_id', 'photos']);
    assert.equal(issues.photos, 'Не хватает фото: ОС целиком и инвентарный номер');
    const filled = { name: 'Ноутбук', category_id: 1, inventory_number: 'ОС-1', to_user_id: 8 };
    assert.deepEqual(intakeIssues(filled, { [PHOTO_WHOLE]: SHOT, [PHOTO_NUMBER]: SHOT }), {});
    assert.equal(intakeIssues(filled, { [PHOTO_WHOLE]: SHOT, extra: [SHOT] }).photos, 'Не хватает фото: инвентарный номер');
});

test('строки истории: что изменилось — коротко, словами', () => {
    const moved = eventLines({ kind: 'moved', payload: {
        from: { mol_name: 'Ахметов Тимур Русланович', department_name: 'Отдел продаж', location: 'каб. 204' },
        to: { mol_name: 'Омаров Данияр Кайратович', department_name: 'IT', on_stock: true, location: 'склад IT' } } });
    assert.deepEqual(moved, ['Ахметов Т. Р. → Омаров Д. К.', 'каб. 204 → склад IT']);
    const toStock = eventLines({ kind: 'moved', payload: {
        from: { mol_name: 'Ахметов Тимур', location: 'каб. 204' }, to: { mol_name: 'Омаров Данияр', on_stock: true, location: 'каб. 3' } } });
    assert.deepEqual(toStock, ['Ахметов Т. → Омаров Д.', 'каб. 204 → склад, каб. 3']);
    // Место не менялось — второй строки нет.
    const sameRoom = eventLines({ kind: 'moved', payload: {
        from: { mol_name: 'Ахметов Тимур', location: 'каб. 204' }, to: { mol_name: 'Садыкова Камила', location: 'каб. 204' } } });
    assert.deepEqual(sameRoom, ['Ахметов Т. → Садыкова К.']);
    assert.deepEqual(eventLines({ kind: 'registered', payload: { to: { mol_name: 'Нурланова Алия Бауыржановна', location: 'каб. 201' } } }),
        ['Нурланова А. Б. · каб. 201']);
    assert.deepEqual(eventLines({ kind: 'submitted', payload: { from: { name: 'Ахметов Тимур' }, to: { name: 'Садыкова Камила', on_stock: true }, status_label: 'На подписании' } }),
        ['Ахметов Т. → Садыкова К. (склад)']);
    const corrected = eventLines({ kind: 'corrected', payload: {
        changes: [{ label: 'Серийный номер', from: 'A', to: 'B' }, { label: 'Стоимость', from: null, to: 100 }], reason: 'Опечатка' } });
    assert.deepEqual(corrected, ['Серийный номер: A → B', 'Стоимость: — → 100', 'Опечатка']);
    assert.deepEqual(eventLines({ kind: 'rejected', payload: { reason: 'Не согласовано' } }), ['Не согласовано']);
    assert.deepEqual(eventLines({ kind: 'original_received', payload: { handed_by_name: 'Ахметов Тимур', handed_on: '2026-11-15' } }),
        ['Оригинал передал Ахметов Т., 15.11.2026']);
    assert.deepEqual(eventLines({ kind: 'scan_attached', payload: { signed_on: '2026-11-14' } }), ['Акт подписан 14.11.2026']);
    const files = eventFiles({ payload: { files: [{ id: 1, name: 'akt.pdf', kind: 'act_scan' }, { id: 2 }] } });
    assert.equal(files.length, 1);
    assert.equal(PHOTO_EXTRA, 'photo_extra');
});

test('реквизиты актов: форма из сохранённого и обратно на сервер', () => {
    const blank = blankRequisites();
    assert.deepEqual(blank.members, []);
    assert.equal(blank.org_name, '');
    const form = blankRequisites({
        org_name: 'ТОО «Образец»', org_bin: '123456789012', commission_order_date: '2026-11-02T00:00:00',
        members: [{ title: 'Главный бухгалтер', name: 'Главбухова Г.' }, { title: null, name: null }],
    });
    assert.equal(form.commission_order_date, '2026-11-02');
    assert.equal(form.members[1].title, '');
    const body = requisitesPayload({ ...form, org_bin: '123 456 789 012', commission_order_date: '' });
    assert.equal(body.org_bin, '123456789012');
    assert.equal(body.commission_order_date, null);
    assert.deepEqual(body.members, [{ title: 'Главный бухгалтер', name: 'Главбухова Г.' }]);   // пустая строка не уходит
});
