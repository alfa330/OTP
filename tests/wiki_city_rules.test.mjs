import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

import {
    cityGenitive, cityMatches, cityOffices, cityTariffs, cityUpdatedAt, cityZone,
    commissionRange, formatDate, formatPercent, formatTime, hasPhoneOrders, optionIconKey,
    serviceIconKey, sourceHost, yandexExtras, zoneHubs, zonePalette, ZONE_COLORS,
} from '../src/components/wiki/cityRules.js';
import {
    CITY_COORDS, MAP_HEIGHT, MAP_WIDTH, cityPoint,
} from '../src/components/wiki/kazakhstanMap.js';
import { KAZAKHSTAN_CITY_OPTIONS, regionOfCity } from '../src/utils/kazakhstanCities.js';

/* Правила вкладки «Города» (задача #322). Карточка, список, схема и редактор
 * обязаны видеть одни и те же тарифы и одни и те же зоны — поэтому правила
 * живут в cityRules.js одной копией и проверяются здесь без браузера. */

const interval = (options = [], rows = [{ label: 'Минимальная стоимость', value: '400 ₸', note: null }]) => ({
    name: 'Тариф «Круглосуточно»', title: 'ежедневно', rows, options, routes: [],
});

const almaty = {
    id: 4,
    name: 'Алматы',
    yandex_data: {
        phone: '+7 (775) 911-11-11',
        tariffs: [
            { class: 'econom', name: 'Эконом', from: '400 ₸', by_phone: true, phone_diff: [],
              intervals: [interval([
                  { label: 'Общаюсь текстом', value: '0 ₸', free: true },
                  { label: 'Детское кресло', value: '300 ₸', free: false },
              ])] },
            { class: 'business', name: 'Комфорт', from: '490 ₸', by_phone: false, phone_diff: [],
              intervals: [interval([
                  { label: 'Детское кресло', value: '350 ₸', free: false },
                  { label: 'Перевозка домашнего животного', value: '300 ₸', free: false },
              ])] },
            { class: 'vip', name: 'Business', from: '840 ₸', by_phone: false, phone_diff: [],
              intervals: [interval([{ label: 'Подача с парковки аэропорта', value: '265 ₸', free: false }])] },
        ],
    },
    tariff_meta: {
        econom: { commission: 12, requirement: 'Авто от 2007 года', hidden: false },
        business: { commission: 14, requirement: null, hidden: false },
        vip: { commission: 30, requirement: null, hidden: true },
    },
    extra_tariffs: [{ name: 'Свой тариф', commission: 9, requirement: null, price: 'от 350 ₸' }],
};

test('тарифы: ручные поля ложатся на тариф по КОДУ, а не по названию', () => {
    // У Яндекса «Комфорт» внутри — business, «Бизнес» — vip: привязка по
    // названию переехала бы на соседний тариф.
    const tariffs = cityTariffs(almaty);
    const comfort = tariffs.find((tariff) => tariff.code === 'business');
    assert.equal(comfort.name, 'Комфорт');
    assert.equal(comfort.commission, 14);
    assert.equal(tariffs.find((tariff) => tariff.code === 'econom').requirement, 'Авто от 2007 года');
});

test('тарифы: скрытый не показывается, но редактор видит все', () => {
    assert.equal(cityTariffs(almaty).some((tariff) => tariff.code === 'vip'), false);
    assert.equal(cityTariffs(almaty, { includeHidden: true }).some((tariff) => tariff.code === 'vip'), true);
});

test('тарифы: свои тарифы идут после Яндекса и без раскрытия', () => {
    const tariffs = cityTariffs(almaty);
    const own = tariffs[tariffs.length - 1];
    assert.equal(own.source, 'manual');
    assert.equal(own.from, 'от 350 ₸');
    assert.equal(own.detail, null);
});

test('комиссия: диапазон только по показанным тарифам', () => {
    // Скрытый vip с 30% в диапазон не попадает — иначе плитка врала бы.
    assert.equal(commissionRange(cityTariffs(almaty)), '9–14%');
    assert.equal(commissionRange([{ commission: 12 }]), '12%');
    assert.equal(commissionRange([{ commission: null }]), '');
    assert.equal(formatPercent(4.5), '4,5%');
    assert.equal(formatPercent(null), '');
});

test('услуги Яндекса: только платные опции показанных тарифов, без повторов', () => {
    const extras = yandexExtras(cityTariffs(almaty));
    assert.deepEqual(extras, ['Детское кресло', 'Перевозка домашнего животного']);
    // «Подача с парковки аэропорта» — у скрытого тарифа, «Общаюсь текстом» — бесплатная.
    assert.equal(extras.includes('Подача с парковки аэропорта'), false);
    assert.equal(extras.includes('Общаюсь текстом'), false);
});

test('заказ по телефону: признак города — хоть один тариф по телефону', () => {
    assert.equal(hasPhoneOrders(cityTariffs(almaty)), true);
    assert.equal(hasPhoneOrders(cityTariffs({ yandex_data: { tariffs: [] } })), false);
});

test('зоны: зону задаёт только обслуживающий офис', () => {
    const cities = [
        { id: 1, name: 'Астана', has_office: true, serving_office_city: '' },
        { id: 2, name: 'Экибастуз', has_office: false, serving_office_city: 'Астана' },
        { id: 3, name: 'Павлодар', has_office: true, serving_office_city: '' },
    ];
    const hubs = zoneHubs(cities);
    assert.deepEqual([...hubs], ['Астана']);
    assert.equal(cityZone(cities[1], hubs), 'Астана');
    // Центр зоны сам в неё входит — на него указывают другие города.
    assert.equal(cityZone(cities[0], hubs), 'Астана');
    // Офис есть, но зону не назначали — серая точка, а не своя «радуга».
    assert.equal(cityZone(cities[2], hubs), '');
});

test('зоны: цвета макета закреплены, остальные — стабильно по алфавиту', () => {
    const palette = zonePalette([
        { serving_office_city: 'Шымкент' }, { serving_office_city: 'Астана' },
        { serving_office_city: 'Павлодар' }, { serving_office_city: 'Атырау' },
    ]);
    assert.equal(palette['Астана'], ZONE_COLORS[1]);
    assert.equal(palette['Шымкент'], ZONE_COLORS[2]);
    assert.notEqual(palette['Павлодар'], palette['Атырау']);
    // Цвета закреплённых зон не отдаются остальным.
    assert.equal([palette['Павлодар'], palette['Атырау']].includes(ZONE_COLORS[1]), false);
    const again = zonePalette([
        { serving_office_city: 'Атырау' }, { serving_office_city: 'Павлодар' },
        { serving_office_city: 'Астана' }, { serving_office_city: 'Шымкент' },
    ]);
    assert.deepEqual(again, palette);
});

test('родительный падеж для легенды', () => {
    const cases = {
        'Алматы': 'Алматы', 'Астана': 'Астаны', 'Шымкент': 'Шымкента', 'Актобе': 'Актобе',
        'Караганда': 'Караганды', 'Кызылорда': 'Кызылорды', 'Семей': 'Семея', 'Костанай': 'Костаная',
        'Рудный': 'Рудного', 'Атырау': 'Атырау', 'Усть-Каменогорск': 'Усть-Каменогорска',
        'Павлодар': 'Павлодара', 'Кокшетау': 'Кокшетау',
    };
    for (const [name, expected] of Object.entries(cases)) {
        assert.equal(cityGenitive(name), expected, name);
    }
});

test('иконки услуг по словам названия — с кириллической границей слова', () => {
    assert.equal(serviceIconKey('Аренда авто'), 'car');
    assert.equal(serviceIconKey('Аренда электровелосипеда'), 'bike');
    assert.equal(serviceIconKey('Мойка по скидке'), 'wash');
    assert.equal(serviceIconKey('СТО партнёра'), 'repair');
    assert.equal(serviceIconKey('Страхование'), 'insurance');
    // «сто» внутри слова — не СТО.
    assert.notEqual(serviceIconKey('Стоянка'), 'repair');
    assert.equal(serviceIconKey('Что-то своё'), 'other');
    assert.equal(optionIconKey('Детское кресло'), 'child');
    assert.equal(optionIconKey('Подача с парковки аэропорта'), 'airport');
});

test('«Обновлено» — позднее из правки руками и изменения у Яндекса', () => {
    assert.equal(cityUpdatedAt({ updated_at: '2026-09-04T10:00:00', yandex_changed_at: '2026-09-20T05:20:00' }),
        '2026-09-20T05:20:00');
    assert.equal(cityUpdatedAt({ updated_at: '2026-09-21T10:00:00', yandex_changed_at: null }),
        '2026-09-21T10:00:00');
    assert.equal(cityUpdatedAt({}), null);
    const now = new Date(2026, 8, 24);
    assert.equal(formatDate('2026-09-04T10:00:00', now), '4 сентября');
    assert.equal(formatDate('2025-12-31T10:00:00', now), '31 декабря 2025');
    assert.equal(formatTime('2026-09-24T05:20:14.1'), '05:20');
    assert.equal(sourceHost('https://taxi.yandex.kz/ru_kz/chimkent/tariff'), 'taxi.yandex.kz');
});

test('куда направлять водителя: обслуживающий офис или свои офисы города', () => {
    const offices = [
        { id: 5, city: 'Алматы', no_office: false, kind: 'park', status: 'active' },
        { id: 7, city: 'Алматы', no_office: false, kind: 'partner', status: 'active' },
        { id: 9, city: 'Астана', no_office: false, kind: 'park', status: 'active' },
        { id: 16, city: 'Балхаш', no_office: true, kind: 'park', status: 'active' },
    ];
    assert.deepEqual(cityOffices({ name: 'Алматы' }, offices).map((o) => o.id), [5]);
    assert.deepEqual(cityOffices({ name: 'Экибастуз', serving_office_id: 9 }, offices).map((o) => o.id), [9]);
    assert.deepEqual(cityOffices({ name: 'Балхаш' }, offices), []);
});

test('поиск города без учёта регистра и ё', () => {
    assert.equal(cityMatches({ name: 'Семей' }, 'сем'), true);
    assert.equal(cityMatches({ name: 'Алматы' }, ''), true);
    assert.equal(cityMatches({ name: 'Алматы' }, 'аст'), false);
});

/* Города, которые заводит схема (wiki/cities.py: DEFAULT_CITIES), обязаны
   существовать в общем справочнике — иначе у карточки нет области — и на
   схеме страны, иначе города нет на карте. */
const seededCities = () => {
    const python = readFileSync(new URL('../wiki/cities.py', import.meta.url), 'utf8');
    const block = /DEFAULT_CITIES = \(([\s\S]*?)\n\)/.exec(python);
    assert.ok(block, 'DEFAULT_CITIES не найден в wiki/cities.py');
    return [...block[1].matchAll(/\('([^']+)', 'https:/g)].map((m) => m[1]);
};

test('города постановки есть в справочнике, с областью и точкой на схеме', () => {
    const names = seededCities();
    assert.equal(names.length, 24);
    const known = new Set(KAZAKHSTAN_CITY_OPTIONS.map((option) => option.value));
    for (const name of names) {
        assert.ok(known.has(name), `нет в справочнике городов: ${name}`);
        assert.ok(regionOfCity(name), `нет области: ${name}`);
        const point = cityPoint(name);
        assert.ok(point, `нет на схеме: ${name}`);
        assert.ok(point.x > 0 && point.x < MAP_WIDTH && point.y > 0 && point.y < MAP_HEIGHT, name);
    }
    assert.equal(regionOfCity('Экибастуз'), 'Павлодарская область');
    assert.equal(regionOfCity('Астана'), 'Город республиканского значения');
    assert.equal(regionOfCity('Нет такого'), '');
});

test('координаты схемы — в пределах страны', () => {
    for (const [name, [lat, lon]] of Object.entries(CITY_COORDS)) {
        assert.ok(lat > 40.5 && lat < 55.5 && lon > 46.5 && lon < 87.5, name);
    }
});
