// «Воронка ОП» — разрез вкладки «Аналитика» раздела «Воронка ОП Tez» (задача #407, 03.11.2026): чистая
// логика сайта (src/components/dial_list/tezFunnel.js) — запрос с фильтрами, плитки шагов и конверсий,
// «ждём данные TEZ APP» вместо нуля, варианты выбора, подписи причин и подсказка о настройке «Отказа».
//
// Запуск: node --test tests/dial_list_tez_funnel.test.mjs

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    CITY_NONE, REGISTRATION_PENDING, batchOptions, cityLabel, cityOptions, funnelConversions, funnelQuery,
    funnelSteps, reasonLabel, refusalSetupNotice, registrationPending,
} from '../src/components/dial_list/tezFunnel.js';

const NF = new Intl.NumberFormat('ru-RU');

const SAMPLE = {
    registration_available: false,
    totals: { base: 7195, already_registered: null, dialog: 3275, registered_after: null, orders_after: 320 },
    rates: { dialog_to_registration: null, dialog_to_orders: 9.8 },
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

test('шаги: пять в порядке задачи, регистрации без данных — «—» и «ждём данные TEZ APP», а не ноль', () => {
    const steps = funnelSteps(SAMPLE);
    assert.deepEqual(steps.map((s) => s.label), [
        'База', 'Уже зарегистрированы', 'Состоялся диалог', 'Зарегистрировались после контакта', 'Вышли к заказам']);
    assert.equal(steps[0].value, NF.format(7195));
    assert.deepEqual([steps[1].value, steps[1].hint], ['—', REGISTRATION_PENDING]);
    assert.deepEqual([steps[3].value, steps[3].hint], ['—', REGISTRATION_PENDING]);
    assert.equal(steps[2].hint, 'разговор от 10 с');
    assert.equal(steps[4].tone, 'good');
    assert.ok(steps.every((s) => s.help && s.help.length > 20), 'у каждого шага — пояснение под «i»');
    // Нет поездок — нейтрально, без зелёного.
    assert.equal(funnelSteps({ totals: { orders_after: 0 } })[4].tone, null);
});

test('регистрация придёт от TEZ APP — шаги покажут числа', () => {
    const ready = { registration_available: true,
        totals: { base: 10, already_registered: 3, dialog: 5, registered_after: 2, orders_after: 1 },
        rates: { dialog_to_registration: 40, dialog_to_orders: 20 } };
    assert.equal(registrationPending(ready), false);
    const steps = funnelSteps(ready);
    assert.deepEqual([steps[1].value, steps[1].hint, steps[3].value, steps[3].hint], ['3', null, '2', null]);
    const conv = funnelConversions(ready);
    assert.deepEqual(conv.map((c) => c.value), ['40%', '20%']);
    assert.equal(conv[0].hint, 'зарегистрировались после ÷ диалог');
    assert.equal(registrationPending(null), true);
    assert.equal(registrationPending({ registration_available: true, totals: {} }), true);
});

test('конверсии: формула в подписи, нет диалогов — 0%', () => {
    const conv = funnelConversions(SAMPLE);
    assert.deepEqual(conv.map((c) => [c.label, c.value, c.hint]), [
        ['Из диалога в регистрацию', '—', REGISTRATION_PENDING],
        ['Из диалога в выход к заказам', '9,8%', 'вышли к заказам ÷ диалог'],
    ]);
    assert.equal(funnelConversions({ rates: { dialog_to_orders: 0 } })[1].value, '0%');
    assert.equal(funnelConversions({})[1].value, '0%');
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
