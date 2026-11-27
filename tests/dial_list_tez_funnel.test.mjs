// «Воронка ОП» — разрез вкладки «Аналитика» раздела «Воронка ОП Tez» (задача #407, 03.11.2026): чистая
// логика сайта (src/components/dial_list/tezFunnel.js) — запрос с фильтрами, плитки шагов и конверсий,
// предупреждение о водителях, по которым TEZ APP ещё не ответил, варианты выбора, подписи причин и
// подсказка о настройке «Отказа».
//
// Запуск: node --test tests/dial_list_tez_funnel.test.mjs

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    CITY_NONE, OUTCOME_COLOR_DEFAULT, OUTCOMES_PENDING_TEXT, agreementSetupNotice, batchOptions, callWindowText,
    REPORT_SPLIT_MIN_PX, REPORT_THREE_MIN_PX, cityLabel, cityOptions, funnelConversions, funnelQuery, funnelSteps,
    monthOptions, outcomeRows, outcomeTiles, outcomesReady, outcomesTotal, periodHint, reasonLabel, refusalSetupNotice,
    BARS_MIN_COLUMN_PX, FILTERS_INLINE_MIN_PX, filtersInline, listColumnWidth, reportLayout, reportShowsBars, splitInTwo,
    stepTileClass, uncheckedNotice,
} from '../src/components/dial_list/tezFunnel.js';

const NF = new Intl.NumberFormat('ru-RU');

const SAMPLE = {
    totals: { base: 7195, already_registered: 2410, dialog: 3275, registered_after: 512, orders_after: 320 },
    rates: { dialog_to_registration: 15.6, dialog_to_orders: 9.8 },
    tez_unchecked: 0,
};

test('запрос: пустые фильтры не уходят, месяц базы — YYYY-MM', () => {
    assert.equal(funnelQuery(), '');
    assert.equal(funnelQuery({ period: '2026-10-01' }), '?period=2026-10');
    assert.equal(
        funnelQuery({ period: '2026-10', batchId: 'b-1', dateFrom: '2026-10-01', dateTo: '2026-10-15', city: 'алматы' }),
        '?period=2026-10&batch_id=b-1&date_from=2026-10-01&date_to=2026-10-15&city=%D0%B0%D0%BB%D0%BC%D0%B0%D1%82%D1%8B',
    );
    assert.equal(funnelQuery({ city: CITY_NONE }), '?city=__none__');
});

test('шаги: пять в порядке задачи, у каждого — число и чем он отмерен', () => {
    const steps = funnelSteps(SAMPLE);
    assert.deepEqual(steps.map((s) => s.label), [
        'База', 'Уже зарегистрированы', 'Состоялся диалог', 'Зарегистрировались после контакта', 'Вышли к заказам']);
    assert.deepEqual(steps.map((s) => s.value), [7195, 2410, 3275, 512, 320].map((n) => NF.format(n)));
    assert.deepEqual(steps.map((s) => s.hint || null), [
        null, 'до первого звонка', 'разговор от 10 с', 'после диалога', 'после диалога']);
    assert.equal(steps[4].tone, 'good');
    assert.ok(steps.every((s) => s.help && s.help.length > 20), 'у каждого шага — пояснение под «i»');
    assert.ok(steps.every((s) => !/ждём/.test(`${s.value} ${s.hint || ''} ${s.help}`)), 'данные TEZ APP уже есть');
    // Нет поездок — нейтрально, без зелёного; пустой ответ — нули, а не «undefined».
    assert.equal(funnelSteps({ totals: { orders_after: 0 } })[4].tone, null);
    assert.deepEqual(funnelSteps(null).map((s) => s.value), ['0', '0', '0', '0', '0']);
});

test('конверсии: формула в подписи, нет диалогов — 0%', () => {
    const conv = funnelConversions(SAMPLE);
    assert.deepEqual(conv.map((c) => [c.label, c.value, c.hint]), [
        ['Из диалога в регистрацию', '15,6%', 'зарегистрировались после ÷ диалог'],
        ['Из диалога в выход к заказам', '9,8%', 'вышли к заказам ÷ диалог'],
    ]);
    assert.deepEqual(funnelConversions({ rates: { dialog_to_registration: 0, dialog_to_orders: 0 } }).map((c) => c.value),
        ['0%', '0%']);
    assert.deepEqual(funnelConversions({}).map((c) => c.value), ['0%', '0%']);
});

test('TEZ APP ответил не обо всех — предупреждение с числами; обо всех — молчим', () => {
    assert.equal(uncheckedNotice(SAMPLE), '');
    assert.equal(uncheckedNotice({}), '');
    assert.equal(uncheckedNotice(null), '');
    const notice = uncheckedNotice({ ...SAMPLE, tez_unchecked: 1200 });
    assert.match(notice, new RegExp(`по ${NF.format(1200)} из ${NF.format(7195)} водителей`));
    assert.match(notice, /регистрации и поездки в воронке пока не учтены/);
    // Что с этим делать — тоже сказано.
    assert.match(notice, /«Сверить сейчас» во вкладке «Успешки»/);
});

test('месяц базы — из базы успешек: свежие сверху, с числом водителей', () => {
    const data = { options: { months: [
        { period: '2026-11-01', base: 0 }, { period: '2026-10-01', base: 11168 }, { period: '2026-09-01', base: 4195 },
    ] } };
    assert.deepEqual(monthOptions(data), [
        { value: '2026-11-01', label: 'Ноябрь 2026', meta: '0' },
        { value: '2026-10-01', label: 'Октябрь 2026', meta: NF.format(11168) },
        { value: '2026-09-01', label: 'Сентябрь 2026', meta: NF.format(4195) },
    ]);
    assert.deepEqual(monthOptions(null), []);
});

test('дни звонков: без выбора — окно месяца базы, как у «Дозвонились»', () => {
    const data = { call_window: { from: '2026-09-24', to: '2026-10-31' } };
    assert.equal(callWindowText(data), '24.09–31.10');
    assert.match(periodHint(data), /окно месяца базы 24\.09–31\.10, как у «Дозвонились»/);
    assert.match(periodHint(data), /«Уже зарегистрированы» от дней не зависят/);
    // Сервер без окна — подсказка без дат, но не «undefined».
    assert.equal(callWindowText({}), '');
    assert.doesNotMatch(periodHint({}), /undefined/);
});

test('выбор города и файла: «все» первыми, «не указан» словами, у файла — дата загрузки', () => {
    const data = { options: {
        cities: [{ key: 'алматы', name: 'Алматы', base: 1200 }, { key: CITY_NONE, name: '', base: 7 }],
        batches: [{ id: 'b-2', file_name: 'добор.xlsx', created_at: '2026-10-15T10:40:00+05:00' },
            { id: 'b-1', file_name: '', created_at: null }],
    } };
    assert.deepEqual(cityOptions(data), [
        { value: '', label: 'Все города' },
        { value: 'алматы', label: 'Алматы', meta: NF.format(1200) },
        { value: CITY_NONE, label: 'Город не указан', meta: '7' },
    ]);
    const batches = batchOptions(data);
    assert.deepEqual(batches.map((b) => [b.value, b.label]), [['', 'Все загрузки'], ['b-2', 'добор.xlsx'], ['b-1', 'файл']]);
    assert.match(batches[1].meta, /^\d{2}\.\d{2}$/);
    assert.equal(batches[2].meta, '');
    assert.deepEqual(cityOptions(null), [{ value: '', label: 'Все города' }]);
    assert.equal(cityLabel('  '), 'Город не указан');
    assert.equal(cityLabel(' Тараз '), 'Тараз');
});

test('причины: без типа — «Без причины»; подсказка о настройке «Отказа»', () => {
    assert.equal(reasonLabel({ name: 'Дорого' }), 'Дорого');
    assert.equal(reasonLabel({ name: '' }), 'Без причины');
    assert.equal(reasonLabel(null), 'Без причины');
    assert.match(refusalSetupNotice({ refusals: { outcomes: [] } }), /нет итога «Отказ»/);
    assert.match(refusalSetupNotice({ refusals: { outcomes: [{ name: 'Отказ', reasons: 0 }] } }), /нет причин/);
    assert.equal(refusalSetupNotice({ refusals: { outcomes: [{ name: 'Отказ', reasons: 3 }] } }), '');
    // Сервер без отчёта об отказах — молчим, а не пугаем.
    assert.equal(refusalSetupNotice({}), '');
});

// Итоги звонков (27.11.2026): все итоги настроек Тез КЦ — «Согласен, Перезвон, Отказ, Недоступен, Уже работает,
// Сброс, Подумает» (прислал владелец), согласия и отказы — главными плитками.
const OUTCOMES = { outcomes: { total: 60, agreements: 30, refusals: 24, items: [
    { outcome_id: 'o1', name: 'Согласен', color: '#34C759', kind: 'agreement', is_active: true, count: 30, subtypes: [] },
    { outcome_id: 'o2', name: 'Перезвон', color: '#FF9F0A', kind: '', is_active: true, count: 0,
        subtypes: [{ id: null, name: '', count: 0 }] },
    { outcome_id: 'o3', name: 'Отказ', color: '#FF3B30', kind: 'refusal', is_active: true, count: 24,
        subtypes: [{ id: 's1', name: 'Нет авто', count: 20 }, { id: null, name: '', count: 4 }] },
    { outcome_id: 'o4', name: 'Недоступен', color: 'серый', kind: '', is_active: true, count: 0, subtypes: [] },
    { outcome_id: 'o5', name: 'Уже работает', color: '#5856D6', kind: '', is_active: false, count: 1,
        subtypes: [{ id: 's2', name: 'В другом парке', count: 1 }] },
    { outcome_id: 'o7', name: 'Подумает', color: '#8E8E93', kind: '', is_active: true, count: 5,
        subtypes: [{ id: null, name: ' ', count: 5 }] },
] } };

test('итоги звонков: все итоги настроек по порядку и с нулём; типы отказа — не здесь, а в причинах', () => {
    const rows = outcomeRows(OUTCOMES);
    assert.deepEqual(rows.map((r) => [r.name, r.count, r.inactive]), [
        ['Согласен', 30, false], ['Перезвон', 0, false], ['Отказ', 24, false], ['Недоступен', 0, false],
        ['Уже работает', 1, true], ['Подумает', 5, false]]);
    // Цвет — из настроек; негодный — серый.
    assert.equal(rows[3].color, OUTCOME_COLOR_DEFAULT);
    assert.equal(rows[0].color, '#34C759');
    // Типы: у отказа — пусто (отдельный отчёт рядом), нулевые — не строки, без имени — «Без типа».
    assert.deepEqual(rows[2].subtypes, []);
    assert.deepEqual(rows[1].subtypes, []);
    assert.deepEqual(rows[4].subtypes, [{ key: 's2', name: 'В другом парке', count: 1 }]);
    // Итог без типов: одна запись «без типа» на всё число — не строка, иначе «Подумает 5 → Без типа 5» (стенд 27.11).
    assert.deepEqual(rows[5].subtypes, []);
    // Типы выбирали не всегда — остаток «Без типа» строкой, чтобы типы сходились с итогом.
    const mixed = outcomeRows({ outcomes: { items: [{ outcome_id: 'm', name: 'Перезвон', kind: '', count: 3,
        subtypes: [{ id: 't1', name: 'Занят', count: 2 }, { id: null, name: '', count: 1 }] }] } });
    assert.deepEqual(mixed[0].subtypes, [{ key: 't1', name: 'Занят', count: 2 }, { key: 'none', name: 'Без типа', count: 1 }]);
    assert.equal(outcomesTotal(OUTCOMES), 60);
    // Сервер без итогов (старый) — пусто и ноль, а не «undefined».
    assert.deepEqual(outcomeRows({}), []);
    assert.equal(outcomesTotal(null), 0);
});

test('согласия и отказы — главные плитки: число и доля от всех итогов', () => {
    const tiles = outcomeTiles(OUTCOMES);
    assert.deepEqual(tiles.map((t) => [t.label, t.value, t.hint]), [
        ['Согласились', '30', '50% от всех итогов'], ['Отказались', '24', '40% от всех итогов']]);
    assert.ok(tiles.every((t) => t.help.length > 40), 'у каждой плитки — пояснение под «i»');
    assert.match(tiles[1].help, /их сумма равна этому числу/);
    // Разбор 27.11: в «Автообзвоне» «Согласились» — кто хоть раз согласился; разница названа под «i».
    assert.match(tiles[0].help, /В разрезе «Автообзвон» «Согласились» считают иначе/);
    // Итогов за дни нет — нули без деления на ноль.
    assert.deepEqual(outcomeTiles({ outcomes: { total: 0, items: [] } }).map((t) => [t.value, t.hint]),
        [['0', '0% от всех итогов'], ['0', '0% от всех итогов']]);
    const odd = outcomeTiles({ outcomes: { total: 3, agreements: 1, refusals: 0, items: [] } });
    // Целыми процентами — как полоса доли в «Итогах звонков».
    assert.equal(odd[0].hint, '33% от всех итогов');
});

test('нет согласия среди итогов — предупреждение; есть или итогов нет — молчим', () => {
    assert.equal(agreementSetupNotice(OUTCOMES), '');
    assert.equal(agreementSetupNotice({}), '');
    assert.equal(agreementSetupNotice({ outcomes: { items: [] } }), '');
    const noAgreement = { outcomes: { items: OUTCOMES.outcomes.items.filter((i) => i.kind !== 'agreement') } };
    assert.match(agreementSetupNotice(noAgreement), /нет согласия/);
    assert.match(agreementSetupNotice(noAgreement), /«Настройки» → «Итоги»/);
});

test('сервер ещё без итогов (сайт выехал раньше) — не «ноль согласий», а «ещё не считает»', () => {
    assert.equal(outcomesReady({}), false);
    assert.equal(outcomesReady({ outcomes: {} }), false);
    assert.equal(outcomesReady(OUTCOMES), true);
    assert.deepEqual(outcomeTiles({ totals: {} }), []);
    assert.match(OUTCOMES_PENDING_TEXT, /после обновления сервера/);
});

// Раскладка (27.11.2026, владелец: «всё симметрично», «в один экран», «адаптив под любой экран»).
test('раскладка — от ширины блока: три колонки, друг под другом, телефон', () => {
    assert.equal(reportLayout(1536), 'three');            // окно 1680, сайдбар свёрнут
    assert.equal(reportLayout(1316), 'three');            // окно 1680, сайдбар открыт
    assert.equal(reportLayout(REPORT_THREE_MIN_PX), 'three');
    assert.equal(reportLayout(REPORT_THREE_MIN_PX - 1), 'stack');
    assert.equal(reportLayout(916), 'stack');             // окно 1280, сайдбар открыт
    assert.equal(reportLayout(REPORT_SPLIT_MIN_PX), 'stack');
    assert.equal(reportLayout(366), 'single');            // телефон
    assert.equal(reportLayout(0), 'single');              // до замера и без данных
    assert.equal(reportLayout(undefined), 'single');
});

test('причины — две колонки по порядку: 14 → 7+7 (вровень с семью итогами), 13 → 7+6', () => {
    const list = (n) => Array.from({ length: n }, (_, i) => i + 1);
    assert.deepEqual(splitInTwo(list(14)).map((c) => c.length), [7, 7]);
    assert.deepEqual(splitInTwo(list(13)), [[1, 2, 3, 4, 5, 6, 7], [8, 9, 10, 11, 12, 13]]);
    assert.deepEqual(splitInTwo(list(1)), [[1], []]);
    assert.deepEqual(splitInTwo(null), [[], []]);
});

test('плитки шагов — ровные ряды: телефон 2+2+1, планшет 3+2, компьютер в ряд', () => {
    const five = [0, 1, 2, 3, 4].map((i) => stepTileClass(i, 5).split(' '));
    // Телефон (2 колонки): последняя нечётная — во всю ширину.
    assert.deepEqual(five.map((c) => c.includes('col-span-2')), [false, false, false, false, true]);
    // Планшет (сетка на 6): три по 2 и две по 3 — оба ряда во всю ширину.
    assert.deepEqual(five.map((c) => c.find((x) => x.startsWith('sm:'))),
        ['sm:col-span-2', 'sm:col-span-2', 'sm:col-span-2', 'sm:col-span-3', 'sm:col-span-3']);
    assert.ok(five.every((c) => c.includes('lg:col-span-1')));
    // Четыре плитки — по две на планшете, на телефоне без растяжки.
    const four = [0, 1, 2, 3].map((i) => stepTileClass(i, 4));
    assert.ok(four.every((c) => c.includes('sm:col-span-3') && !c.split(' ').includes('col-span-2')));
});

test('полосы долей — только когда в узкой колонке имя, число и полоса встают в строку', () => {
    // Окно 1680 (сайдбар свёрнут / открыт), 1440 со свёрнутым — три колонки шире 400 px: полосы есть.
    assert.equal(reportShowsBars(1536, true), true);
    assert.equal(reportShowsBars(1316, true), true);
    assert.equal(reportShowsBars(1296, true), true);
    // 1440 с открытым сайдбаром (1076) и 1280 со свёрнутым (1136) — три узкие колонки: процент без полосы.
    assert.equal(reportShowsBars(1076, true), false);
    assert.equal(reportShowsBars(1136, true), false);
    // Друг под другом: причины в две колонки по половине ширины.
    assert.equal(listColumnWidth(900), 450);
    assert.equal(reportShowsBars(900, true), true);
    assert.equal(reportShowsBars(600, false), false);
    // Телефон: строка во всю ширину, шрифт мельче — полосы есть.
    assert.equal(listColumnWidth(366), 366);
    assert.equal(reportShowsBars(366, false), true);
    assert.ok(BARS_MIN_COLUMN_PX.big > BARS_MIN_COLUMN_PX.base);
});

test('фильтры одной строкой — только на крупном шрифте и в широком разделе', () => {
    assert.equal(filtersInline(1536, true), true);     // 1680, сайдбар свёрнут
    assert.equal(filtersInline(1316, true), true);     // 1680, сайдбар открыт
    assert.equal(filtersInline(1076, true), false);    // 1440 с открытым — «Ноябрь…» и «Ве…» обрезались бы
    assert.equal(filtersInline(FILTERS_INLINE_MIN_PX, true), true);
    assert.equal(filtersInline(1536, false), false);   // мелкий шрифт (окно уже 1280) — подписи над полями
    assert.equal(filtersInline(undefined, true), false);
});
