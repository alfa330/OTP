/*
 * История версий: что с чем сравнивать и как читается отметка времени.
 *
 * Отдельным файлом от экрана по той же причине, что и guest_access: сам экран —
 * модалка, которая наполняется по сети, и серверный рендер до этих ветвлений не
 * доходит. А ветвлений ровно столько, сколько крайних случаев: самая старая
 * редакция (сравнивать не с чем слева), текущая (не с чем справа) и статья,
 * которую ещё ни разу не правили.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
    CHANGE_LABELS, comparePair, fmtStamp, plural, stateKey,
} from '../src/components/wiki/historyView.js';

const ITEMS = [
    { key: 'current', version_id: null, is_current: true, created_at: '2026-08-24T16:58:00' },
    { key: 'v692', version_id: 692, is_current: false, created_at: '2026-08-24T16:54:10' },
    { key: 'v687', version_id: 687, is_current: false, created_at: '2026-08-24T16:52:10' },
];

test('отметка времени читается строкой, а не через Date', () => {
    // Сервер отдаёт наивное алматинское время. new Date() разобрал бы его как
    // местное, и у человека в другом поясе соседние правки поменялись бы
    // местами относительно подписей.
    assert.equal(fmtStamp('2026-08-24T16:58:00'), '24.08.2026, 16:58:00');
    assert.equal(fmtStamp('2026-08-24 16:58:00.758553'), '24.08.2026, 16:58:00');
    // Секунды обязательны: правки идут очередями, и две редакции в одной
    // минуте без них выглядят как одна строка, случайно продублированная.
    assert.equal(fmtStamp('2026-08-24T16:39:23'), '24.08.2026, 16:39:23');
    assert.equal(fmtStamp('2026-08-24T16:39'), '24.08.2026, 16:39');
    assert.equal(fmtStamp(null), '—');
    assert.equal(fmtStamp('мусор'), '—');
});

test('ключ редакции: у текущей своего снимка нет', () => {
    assert.equal(stateKey(ITEMS[1]), '692');
    assert.equal(stateKey(ITEMS[0]), 'current');
    assert.equal(stateKey(null), 'current');
});

test('по умолчанию сравниваем с предыдущей редакцией', () => {
    const pair = comparePair(ITEMS, 'v692');
    assert.equal(pair.mode, 'prev');
    assert.equal(pair.from.key, 'v687');   // старая слева
    assert.equal(pair.to.key, 'v692');     // выбранная справа
    assert.equal(pair.canPrev, true);
    assert.equal(pair.canCurrent, true);
});

test('«с текущей» сравнивает выбранную редакцию с верхней', () => {
    const pair = comparePair(ITEMS, 'v687', 'current');
    assert.equal(pair.mode, 'current');
    assert.equal(pair.from.key, 'v687');
    assert.equal(pair.to.key, 'current');
});

test('у текущей редакции «с текущей» невозможно — падаем на предыдущую', () => {
    // Иначе выбор режима, сделанный на старой редакции, оставил бы пустой экран
    // после клика по верхней строке.
    const pair = comparePair(ITEMS, 'current', 'current');
    assert.equal(pair.mode, 'prev');
    assert.equal(pair.canCurrent, false);
    assert.equal(pair.from.key, 'v692');
    assert.equal(pair.to.key, 'current');
});

test('у самой старой редакции нет предыдущей — сравниваем с текущей', () => {
    const pair = comparePair(ITEMS, 'v687');
    assert.equal(pair.mode, 'current');
    assert.equal(pair.from.key, 'v687');
    assert.equal(pair.to.key, 'current');
});

test('статья с одной редакцией: сравнивать не с чем, и это не ошибка', () => {
    const only = [ITEMS[0]];
    const pair = comparePair(only, 'current');
    assert.equal(pair.mode, null);
    assert.equal(pair.from, null);
    assert.equal(pair.to, null);
    assert.equal(pair.entry.key, 'current');
});

test('пропавший выбор не роняет экран', () => {
    // После отката ключ текущей редакции меняется: выбранного может не стать.
    const pair = comparePair(ITEMS, 'v999');
    assert.equal(pair.entry, null);
    assert.equal(pair.mode, null);
});

test('изменённые поля названы по-русски', () => {
    assert.equal(CHANGE_LABELS.content, 'Текст');
    assert.equal(CHANGE_LABELS.status, 'Статус');
});

test('склонение сохранений', () => {
    assert.equal(plural(1, 'сохранение', 'сохранения', 'сохранений'), 'сохранение');
    assert.equal(plural(3, 'сохранение', 'сохранения', 'сохранений'), 'сохранения');
    assert.equal(plural(11, 'строка', 'строки', 'строк'), 'строк');
    assert.equal(plural(22, 'строка', 'строки', 'строк'), 'строки');
});
