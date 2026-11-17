/*
 * «Настройки SIP» → карточка отдела Тез КЦ → «Простой в «Исходе» · отдел продаж».
 *
 * Владелец 17.11.2026: «добавь возможность эту настройку менять» — и «выключить сейчас». Логика
 * формы — src/components/sip/idleOfflineSetting.js (без React): минуты в форме, секунды у сервера,
 * пустое поле — срок по умолчанию, мусор и выход за 1–60 минут — ошибка, а не подставленный срок.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    IDLE_OFFLINE_DEPARTMENT_CODE, IDLE_OFFLINE_MIN_DEFAULT, IDLE_OFFLINE_MIN_MAX, IDLE_OFFLINE_MIN_MIN,
    IDLE_OFFLINE_RESET, hasIdleOfflineSetting, idleOfflineFormFrom, idleOfflineHint, idleOfflineLabel,
    idleOfflineMinutes, idleOfflinePayload,
} from '../src/components/sip/idleOfflineSetting.js';

test('настройка есть только у Тез КЦ', () => {
    assert.equal(IDLE_OFFLINE_DEPARTMENT_CODE, 'tez');
    assert.equal(hasIdleOfflineSetting({ department_code: 'tez' }), true);
    assert.equal(hasIdleOfflineSetting({ department_code: ' TEZ ' }), true);
    for (const dept of [{ department_code: 'remote_cc' }, { department_code: 'op' }, {}, null, undefined]) {
        assert.equal(hasIdleOfflineSetting(dept), false);
    }
});

test('форма из строки отдела: не задано — выключено и пустые минуты', () => {
    assert.deepEqual(idleOfflineFormFrom({}), { idle_offline_enabled: false, idle_offline_min: '' });
    assert.deepEqual(idleOfflineFormFrom({ idle_offline_enabled: null, idle_offline_sec: null }),
        { idle_offline_enabled: false, idle_offline_min: '' });
    assert.deepEqual(idleOfflineFormFrom({ idle_offline_enabled: true, idle_offline_sec: 240 }),
        { idle_offline_enabled: true, idle_offline_min: '4' });
    // Только настоящий true включает: строка 'true' из старого кеша — не включение.
    assert.equal(idleOfflineFormFrom({ idle_offline_enabled: 'true' }).idle_offline_enabled, false);
});

test('в сеть — секунды; пустые минуты — срок по умолчанию', () => {
    assert.deepEqual(idleOfflinePayload({ idle_offline_enabled: true, idle_offline_min: '4' }),
        { idle_offline_enabled: true, idle_offline_sec: 240 });
    assert.deepEqual(idleOfflinePayload({ idle_offline_enabled: true, idle_offline_min: '' }),
        { idle_offline_enabled: true, idle_offline_sec: 'inherit' });
    assert.deepEqual(idleOfflinePayload({ idle_offline_enabled: false, idle_offline_min: ' 3 ' }),
        { idle_offline_enabled: false, idle_offline_sec: 180 });
    assert.deepEqual(idleOfflinePayload({ idle_offline_enabled: true, idle_offline_min: String(IDLE_OFFLINE_MIN_MIN) }),
        { idle_offline_enabled: true, idle_offline_sec: 60 });
    assert.deepEqual(idleOfflinePayload({ idle_offline_enabled: true, idle_offline_min: String(IDLE_OFFLINE_MIN_MAX) }),
        { idle_offline_enabled: true, idle_offline_sec: 3600 });
});

test('мусор и выход за границы — ошибка с понятным текстом, а не подставленный срок', () => {
    for (const raw of ['0', '61', '-3', '2.5', 'три', '3 мин', '1e1']) {
        assert.throws(() => idleOfflinePayload({ idle_offline_enabled: true, idle_offline_min: raw }),
            /Срок простоя/, raw);
    }
});

test('«Вернуть общие» снимает правило: не задано — выключено', () => {
    assert.deepEqual({ ...IDLE_OFFLINE_RESET }, { idle_offline_enabled: 'inherit', idle_offline_sec: 'inherit' });
    assert.ok(Object.isFrozen(IDLE_OFFLINE_RESET));
});

test('подпись в списке отделов — только у включённого правила Тез КЦ', () => {
    assert.equal(idleOfflineLabel({ department_code: 'tez', idle_offline_enabled: true, idle_offline_sec: 240 }),
        '«Офлайн» за простой в «Исходе»: 4 мин');
    assert.equal(idleOfflineLabel({ department_code: 'tez', idle_offline_enabled: true, idle_offline_sec: null }),
        `«Офлайн» за простой в «Исходе»: ${IDLE_OFFLINE_MIN_DEFAULT} мин`);
    assert.equal(idleOfflineLabel({ department_code: 'tez', idle_offline_enabled: false, idle_offline_sec: 240 }), null);
    assert.equal(idleOfflineLabel({ department_code: 'tez' }), null);
    assert.equal(idleOfflineLabel({ department_code: 'op', idle_offline_enabled: true }), null);
    assert.equal(idleOfflineMinutes({ idle_offline_sec: 0 }), IDLE_OFFLINE_MIN_DEFAULT);
});

test('подпись у выключателя говорит, что будет действовать после сохранения', () => {
    assert.equal(idleOfflineHint({ idle_offline_enabled: false, idle_offline_min: '5' }),
        'выключено: «Исход» без звонков не ограничен');
    assert.equal(idleOfflineHint({ idle_offline_enabled: true, idle_offline_min: '' }),
        '«Офлайн» через 3 мин без звонка (по умолчанию)');
    assert.equal(idleOfflineHint({ idle_offline_enabled: true, idle_offline_min: '7' }), '«Офлайн» через 7 мин без звонка');
    assert.equal(idleOfflineHint({ idle_offline_enabled: true, idle_offline_min: '90' }), 'срок — от 1 до 60 минут');
    assert.equal(idleOfflineHint({ idle_offline_enabled: true, idle_offline_min: 'abc' }), 'срок — от 1 до 60 минут');
});
