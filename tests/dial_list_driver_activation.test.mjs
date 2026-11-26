// «Активация водителей» (26.11.2026): чистая логика раздела — подписи поездок и провайдера, этапы журнала,
// итог загрузки, пределы цели. Сервер шлёт подписи готовыми; здесь — что сайт делает без них и с ними.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    ACTIVATION_STAGES, activationStageLabel, activationUploadSummary, checkWarning, providerLabel, providerShort,
    providerTone, saparText, tripsLabel, tripsLeft, tripsProgress, tripsWord, validateTripsGoal,
} from '../src/components/dial_list/driverActivation.js';
import { isAutodialProfile, normalizeProfile } from '../src/components/dial_list/tezAutodial.js';

test('профиль activation узнаётся и считается движком автообзвона', () => {
    assert.equal(normalizeProfile('activation'), 'activation');
    assert.equal(normalizeProfile(' TEZ '), 'tez');
    assert.equal(normalizeProfile('что-то'), 'remote_cc');
    assert.equal(isAutodialProfile('activation'), true);
    assert.equal(isAutodialProfile('tez'), true);
    assert.equal(isAutodialProfile('remote_cc'), false);
});

test('поездки: подпись сервера главнее, без неё — те же правила', () => {
    assert.equal(tripsLabel({ trips_label: '34 из 50 поездок', trips_total: 1 }), '34 из 50 поездок');
    assert.equal(tripsLabel({ trips_total: 34, trips_goal: 50 }), '34 из 50 поездок');
    assert.equal(tripsLabel({ trips_total: 3, trips_goal: 21 }), '3 из 21 поездки');
    assert.equal(tripsLabel({ trips_total: null, trips_goal: 50 }), 'Поездки не проверены');
    assert.equal(tripsLabel({ trips_total: 51, trips_goal: 50, reached_at: '2026-11-26T10:00:00Z' }), 'Добрал 51 поездку');
    assert.equal(tripsLabel({ trips_total: 52, trips_goal: 50 }), 'Добрал 52 поездки');
    assert.deepEqual([1, 2, 5, 11, 21, 104].map(tripsWord), ['поездка', 'поездки', 'поездок', 'поездок', 'поездка', 'поездки']);
    assert.equal(tripsLeft({ trips_total: 34, trips_goal: 50 }), 16);
    assert.equal(tripsLeft({ trips_total: 34, trips_goal: 50, reached_at: 'x' }), null);
    assert.equal(tripsProgress({ trips_total: 25, trips_goal: 50 }), 50);
    assert.equal(tripsProgress({ trips_total: 80, trips_goal: 50 }), 100);
    assert.equal(tripsProgress({ trips_total: null }), null);
});

test('провайдер: короткое имя, «нет», «не знаем» и тон', () => {
    assert.equal(providerShort('Бумажный  документооборот'), 'Бумажный');
    assert.equal(providerLabel({ provider: 'Бумажный документооборот', provider_source: 'file' }), 'ЭДО: Бумажный');
    assert.equal(providerLabel({ provider: '', provider_source: 'none' }), 'ЭДО: нет');
    assert.equal(providerLabel({ provider: '', provider_source: '' }), 'ЭДО: не знаем');
    assert.equal(providerTone({ on_sapar: true, provider: 'Sapar' }), 'green');
    assert.equal(providerTone({ provider: 'Payda' }), 'amber');
    assert.equal(providerTone({ provider: '' }), 'slate');
});

test('переход на Sapar словами', () => {
    assert.equal(saparText({ sapar: null }), '');
    assert.equal(saparText({ sapar: { resolved: false } }), 'Перешёл на Sapar · ждём конца звонка');
    assert.equal(saparText({ sapar: { resolved: true, success: null } }), 'Перешёл на Sapar сам, без разговора');
    assert.equal(saparText({ sapar: { resolved: true, success: { operator: { name: 'Айгерим' } } } }),
        'Перевод на Sapar · успешка Айгерим');
});

test('этапы журнала — как у Тез, цель «Добрали поездки»', () => {
    const signed = ACTIVATION_STAGES.find((s) => s.value === 'signed');
    assert.equal(signed.label, 'Добрали поездки');
    assert.equal(activationStageLabel({ stage: 'signed' }), 'Добрал поездки');
    assert.equal(activationStageLabel({ stage: 'followup' }), 'Обещал ездить');
    assert.equal(activationStageLabel({ stage: 'queue' }), 'В очереди');
});

test('итог загрузки и пределы цели', () => {
    assert.equal(activationUploadSummary({ rows_new: 3, rows_duplicate: 1, rows_goal_reached: 2, trips_goal: 50,
        rows_bad_id: 1, bad_id_rows: [5] }, 'Ноябрь 2026'),
    'Ноябрь 2026: новых 3, повторов 1, уже 50+ поездок — не загружены: 2, без ID водителя 1 (строка 5)');
    assert.equal(validateTripsGoal('50'), '');
    assert.match(validateTripsGoal('0'), /от 1 до 1000/);
    assert.match(validateTripsGoal('abc'), /целое число/);
});

test('предупреждение о проверках', () => {
    assert.equal(checkWarning({ activation_check: { configured: true, fleet_configured: true } }), '');
    assert.match(checkWarning({ activation_check: { configured: false } }), /CRM/);
    assert.match(checkWarning({ activation_check: { configured: true, fleet_configured: false } }), /Fleet/);
    assert.equal(checkWarning({ activation_check: { configured: true, fleet_configured: true, fleet_error: 'протухла' } }),
        'Кабинет Fleet: протухла');
});
