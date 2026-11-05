// «Воронка ОП» — разрез вкладки «Аналитика» раздела «Воронка ОП Tez» (задача #407, 03.11.2026): чистая
// логика сайта (src/components/dial_list/tezFunnel.js) — запрос с фильтрами, плитки шагов и конверсий,
// предупреждение о водителях, по которым TEZ APP ещё не ответил, варианты выбора, подписи причин и
// подсказка о настройке «Отказа».
//
// Запуск: node --test tests/dial_list_tez_funnel.test.mjs

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    CITY_NONE, batchOptions, cityLabel, cityOptions, funnelConversions, funnelQuery, funnelSteps, reasonLabel,
    refusalSetupNotice, uncheckedNotice,
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
