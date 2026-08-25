import test from 'node:test';
import assert from 'node:assert/strict';

import {
    RESULT_DROPPED, RESULT_TALK, exportFileName, hms, hours, percent,
    fullDay, prettyPhone, resultTone, shortDay, shortTime, silence,
} from '../src/components/cdr/touchMeta.js';

/* Подписи раздела «Касания».
 *
 * Главное, ради чего файл существует: `hms` обязан совпадать с серверным
 * `cdr/report.py:hms`. Экран и выгрузка показывают одни и те же звонки, и если
 * один напишет «0:42», а второй «42 с», человек решит, что перед ним разные
 * цифры. Набор случаев здесь и в tests/test_cdr_touches.py связан — правя один,
 * правьте второй.
 *
 * Телефоны учебные (7XX555XXXX): настоящих в репозитории быть не должно.
 */

test('нулевой разговор — прочерк, а не 0:00', () => {
    // «0:00» читается как «разговор был и длился ноль», а его не было вовсе.
    assert.equal(hms(0), '—');
    assert.equal(hms(null), '—');
    assert.equal(hms(undefined), '—');
    assert.equal(hms(-5), '—');
    assert.equal(hms('мусор'), '—');
});

test('минуты и секунды — без часов, пока их нет', () => {
    assert.equal(hms(42), '0:42');
    assert.equal(hms(60), '1:00');
    assert.equal(hms(432), '7:12');
    assert.equal(hms(3599), '59:59');
});

test('от часа появляется третья группа с нулями', () => {
    assert.equal(hms(3600), '1:00:00');
    assert.equal(hms(3870), '1:04:30');
    assert.equal(hms(419947), '116:39:07');
});

test('часы округляются до одного знака', () => {
    assert.equal(hours(3600), 1);
    assert.equal(hours(419947), 116.7);
    assert.equal(hours(0), 0);
    assert.equal(hours(null), 0);
});

test('проценты не превращаются в NaN на пустом периоде', () => {
    assert.equal(percent(1413, 3396), 41.6);
    assert.equal(percent(0, 0), 0);
    assert.equal(percent(5, 0), 0);
    assert.equal(percent(1, 3), 33.3);
});

test('телефон показывается так, как его набирают', () => {
    assert.equal(prettyPhone('7015550001'), '+7 701 555 00 01');
});

test('непонятный номер показывается сырым, а не выдуманным', () => {
    // Выдумать формат для значения, которого мы не поняли, хуже, чем показать
    // его как есть: человек хотя бы увидит, что с данными что-то не то.
    assert.equal(prettyPhone('123'), '123');
    assert.equal(prettyPhone(''), '—');
    assert.equal(prettyPhone(null), '—');
});

test('время и день режутся из отметки без разбора даты', () => {
    assert.equal(shortTime('2026-08-24 09:05:41'), '09:05:41');
    assert.equal(shortTime(''), '—');
    assert.equal(shortDay('2026-08-24 09:05:41'), '24.08');
    assert.equal(shortDay('2026-08-24'), '24.08');
    assert.equal(shortDay(''), '—');
    assert.equal(shortDay(null), '—');
});

test('молчание моста показывается порядком величины', () => {
    // На третьи сутки молчания точное число минут ничего не сообщает.
    assert.equal(silence(0), '0 мин');
    assert.equal(silence(12), '12 мин');
    assert.equal(silence(59), '59 мин');
    assert.equal(silence(60), '1 ч');
    assert.equal(silence(200), '3 ч');
    assert.equal(silence(1440), '1 сут');
    assert.equal(silence(4321), '3 сут');
    assert.equal(silence(-5), '0 мин');
});

test('«сброс без разговора» отличается по цвету от «не ответил»', () => {
    // Это не «не дозвонились», а «дозвонились и бросили» — разница видна
    // руководителю, ради которого раздел и делался.
    assert.notEqual(resultTone(RESULT_DROPPED), resultTone('Не ответил'));
    assert.match(resultTone(RESULT_TALK), /emerald/);
    assert.match(resultTone(RESULT_DROPPED), /amber/);
});

test('незнакомый результат не остаётся без плашки', () => {
    assert.ok(resultTone('что-то новое'));
});

test('имя файла собирает фронт — через CORS оно до нас не доходит', () => {
    // С годом: файл лежит в «Загрузках» месяцами, и от такого же за прошлый год
    // его иначе не отличить. Должно совпадать с cdr/report.py:report_filename.
    assert.equal(exportFileName('2026-08-24', '2026-08-24'), 'Касания 24.08.2026.xlsx');
    assert.equal(exportFileName('2026-08-01', '2026-08-31'),
                 'Касания 01.08.2026 — 31.08.2026.xlsx');
    assert.equal(fullDay('2026-08-24'), '24.08.2026');
    assert.equal(fullDay(''), '');
});
