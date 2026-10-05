import test from 'node:test';
import assert from 'node:assert/strict';

import {
    PHONE_GROUP_ALL,
    PHONE_THRESHOLD_PRESETS,
    clampPhoneWarn,
    filterPhoneEmployees,
    phoneEmployeeStats,
    phoneGroupLabel,
    phoneGroupOptions,
    phoneGroupTone,
    phoneRuleSummary,
    phoneWarnBlurDecision,
    phoneWarnUpperBound,
    togglePhoneGroup,
} from '../src/components/oktell_guard/oktellGuardPhone.js';
import { fmtMinutes, fmtTime, isoDaysAgo, parseServerTime } from '../src/components/oktell_guard/oktellGuardFormat.js';

/*
 * «Ограничитель Перезвона», часть отдела продаж: автоофлайн iCORE Phone
 * (ТЗ 05.10.2026). Чистая логика панели — фильтры, цифры шапки, границы полей
 * правила. Границы повторяют oktell_guard/phone.py: поле, обещающее больше,
 * чем сервер сохранит, молча срезалось бы при сохранении.
 */

const EMPLOYEES = [
    { id: 1, name: 'Айгуль Основа', sip_number: '6101', status_group: 'osnova', participates: false, kicks_30d: 0 },
    { id: 2, name: 'Ерлан Яров', sip_number: '6202', status_group: 'yar', participates: true, kicks_30d: 3 },
    { id: 3, name: 'Дана Поток', sip_number: '6303', status_group: 'potok', participates: true, kicks_30d: 1 },
    { id: 4, name: 'Без Группы', sip_number: '', status_group: null, participates: false, kicks_30d: 0 },
];

test('в порогах есть 5 минут из ТЗ и все они проходят границы сервера', () => {
    assert.ok(PHONE_THRESHOLD_PRESETS.includes(300));
    assert.deepEqual(PHONE_THRESHOLD_PRESETS.map(fmtMinutes), ['3 мин', '4 мин', '5 мин', '6 мин', '10 мин']);
    for (const value of PHONE_THRESHOLD_PRESETS) {
        assert.ok(value >= 60 && value <= 3600, String(value));
    }
});

test('«Предупреждать за» не может начаться раньше полминуты простоя', () => {
    assert.equal(phoneWarnUpperBound(300), 270);
    assert.equal(phoneWarnUpperBound(3600), 600);
    assert.equal(phoneWarnUpperBound(20), 0);
    assert.equal(clampPhoneWarn('60', 300), 60);
    assert.equal(clampPhoneWarn(999, 300), 270);
    assert.equal(clampPhoneWarn(-5, 300), 0);
    assert.equal(clampPhoneWarn('мусор', 300), 0);
    assert.equal(clampPhoneWarn(59.6, 300), 60);
});

test('галочка группы держит порядок правила и не пускает чужие коды', () => {
    assert.deepEqual(togglePhoneGroup(['yar', 'potok'], 'yar'), ['potok']);
    assert.deepEqual(togglePhoneGroup(['potok'], 'yar'), ['yar', 'potok']);
    assert.deepEqual(togglePhoneGroup([], 'osnova'), []);
    assert.deepEqual(togglePhoneGroup(null, 'potok'), ['potok']);
});

test('цифры шапки — по флагу сервера, а не пересчётом правила', () => {
    assert.deepEqual(phoneEmployeeStats(EMPLOYEES), { total: 4, participating: 2, kickedPeople: 2, kicks: 4 });
    assert.deepEqual(phoneEmployeeStats(null), { total: 0, participating: 0, kickedPeople: 0, kicks: 0 });
});

test('поиск по имени и SIP, фильтр по группе', () => {
    assert.deepEqual(filterPhoneEmployees(EMPLOYEES, { search: 'яров' }).map((r) => r.id), [2]);
    assert.deepEqual(filterPhoneEmployees(EMPLOYEES, { search: '630' }).map((r) => r.id), [3]);
    assert.deepEqual(filterPhoneEmployees(EMPLOYEES, { group: 'osnova' }).map((r) => r.id), [1]);
    assert.deepEqual(filterPhoneEmployees(EMPLOYEES, { group: PHONE_GROUP_ALL }).length, 4);
    assert.deepEqual(phoneGroupOptions(EMPLOYEES).map((o) => o.label), ['Все', 'Основа', 'ЯР', 'Поток']);
    assert.deepEqual(phoneGroupOptions([EMPLOYEES[1]]).map((o) => o.value), [PHONE_GROUP_ALL, 'yar']);
});

test('подписи и цвета групп: от сервера, с запасом на старый ответ', () => {
    assert.equal(phoneGroupLabel('yar', { yar: 'ЯР (рег.)' }), 'ЯР (рег.)');
    assert.equal(phoneGroupLabel('potok', {}), 'Поток');
    assert.equal(phoneGroupLabel('nope', {}), '');
    assert.notEqual(phoneGroupTone('yar'), phoneGroupTone('potok'));
    assert.equal(phoneGroupTone(null), 'slate');
});

test('правило одной строкой', () => {
    assert.equal(
        phoneRuleSummary({ enabled: true, threshold_s: 300, groups: ['yar', 'potok'] }, {}),
        'ЯР и Поток: «Офлайн» после 5 мин без звонков в «Исходе»',
    );
    assert.match(phoneRuleSummary({ enabled: false, threshold_s: 300, groups: ['yar'] }), /выключено/);
    assert.match(phoneRuleSummary({ enabled: true, threshold_s: 300, groups: [] }), /ни одна группа/);
    assert.match(phoneRuleSummary(null), /выключено/);
});

test('время выброса из базы — местное, пометку GMT сериализатора не верим', () => {
    const local = parseServerTime('Mon, 05 Oct 2026 10:07:00 GMT');
    assert.equal(local.getHours(), 10);
    assert.equal(local.getMinutes(), 7);
    assert.equal(fmtTime('Mon, 05 Oct 2026 10:07:00 GMT'), '10:07');
    assert.equal(fmtTime(''), '');
});

test('уход из поля «Предупреждать за» сохраняет только настоящую правку', () => {
    // Ничего не меняли: запроса нет — иначе контролы гасли бы под щелчком по
    // соседнему порогу, а «Изменено … · имя» переписывалось впустую.
    assert.deepEqual(phoneWarnBlurDecision('60', 60, 300), { value: 60, save: false });
    assert.deepEqual(phoneWarnBlurDecision('060', 60, 300), { value: 60, save: false });
    assert.deepEqual(phoneWarnBlurDecision('60.4', 60, 300), { value: 60, save: false });
    // Стёртое поле — не «ноль секунд»: возвращаем сохранённое.
    assert.deepEqual(phoneWarnBlurDecision('', 60, 300), { value: 60, save: false });
    assert.deepEqual(phoneWarnBlurDecision('  ', 45, 300), { value: 45, save: false });
    assert.deepEqual(phoneWarnBlurDecision(null, 60, 300), { value: 60, save: false });
    // Ноль цифрой — осознанное «без предупреждения».
    assert.deepEqual(phoneWarnBlurDecision('0', 60, 300), { value: 0, save: true });
    assert.deepEqual(phoneWarnBlurDecision('0', 0, 300), { value: 0, save: false });
    assert.deepEqual(phoneWarnBlurDecision('90', 60, 300), { value: 90, save: true });
    // Сравниваем уже срезанное значение: 999 при пороге 5 минут — это 270.
    assert.deepEqual(phoneWarnBlurDecision('999', 60, 300), { value: 270, save: true });
    assert.deepEqual(phoneWarnBlurDecision('999', 270, 300), { value: 270, save: false });
    // Сервер отдал число строкой или не отдал вовсе.
    assert.deepEqual(phoneWarnBlurDecision('60', '60', 300), { value: 60, save: false });
    assert.deepEqual(phoneWarnBlurDecision('', undefined, 300), { value: 0, save: false });
});

test('дни отчёта по умолчанию — по местному календарю, а не по UTC', () => {
    // 00:30 местного 6 октября: в Алматы (UTC+5) по UTC ещё 5-е, и дата из
    // toISOString() увела бы «сегодня» во вчера — ночные выбросы пропадали.
    const night = new Date(2026, 9, 6, 0, 30);
    const original = Date.prototype.toISOString;
    // Часовой пояс у машины с тестами любой, поэтому UTC-дату подделываем:
    // функция обязана её не спрашивать.
    Date.prototype.toISOString = () => '1999-01-01T00:00:00.000Z';
    try {
        assert.equal(isoDaysAgo(0, night), '2026-10-06');
        assert.equal(isoDaysAgo(13, night), '2026-09-23');
        // Через границу года и с ведущими нулями.
        assert.equal(isoDaysAgo(5, new Date(2027, 0, 3, 23, 59)), '2026-12-29');
        assert.match(isoDaysAgo(0), /^\d{4}-\d{2}-\d{2}$/);
    } finally {
        Date.prototype.toISOString = original;
    }
    // Переданную дату не портим.
    assert.equal(night.getDate(), 6);
});
