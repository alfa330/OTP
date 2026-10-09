import test from 'node:test';
import assert from 'node:assert/strict';

import {
    CHAT_ACCESS_CODE_DIGITS, autoCloseNote, cleanAccessCode, clockOf, elapsedNow, formatCountdown,
    formatShiftElapsed, isChatAccessQr, newClientEventId, statusDotClass, statusShowsTimer,
} from '../src/components/wazzup/workspaceStatus.js';

/* Чистые функции рабочего места верификатора в «Чатах ОП»: подписи статуса,
   счётчик времени и разбор кода сканера. */

test('время в статусе: минуты и часы, без секунд', () => {
    assert.equal(formatShiftElapsed(0), 'меньше минуты');
    assert.equal(formatShiftElapsed(59), 'меньше минуты');
    assert.equal(formatShiftElapsed(60), '1 мин');
    assert.equal(formatShiftElapsed(12 * 60 + 30), '12 мин');
    assert.equal(formatShiftElapsed(3600), '1 ч');
    assert.equal(formatShiftElapsed(3600 + 24 * 60), '1 ч 24 мин');
    // «Неизвестно» — это не ноль: пустое значение не должно стать «меньше минуты».
    for (const unknown of [null, undefined, '', NaN, -5, 'abc']) {
        assert.equal(formatShiftElapsed(unknown), '', String(unknown));
    }
});

test('время досчитывается от ответа сервера, а не по часам браузера', () => {
    const current = { elapsedSeconds: 300 };
    assert.equal(elapsedNow(current, 1_000_000, 1_000_000), 300);
    assert.equal(elapsedNow(current, 1_000_000, 1_090_000), 390);
    // Часы браузера ушли назад — время в статусе не уменьшается.
    assert.equal(elapsedNow(current, 1_000_000, 900_000), 300);
    assert.equal(elapsedNow({ elapsedSeconds: null }, 0, 1000), null);
    assert.equal(elapsedNow(null, 0, 1000), null);
});

test('счётчик на кнопке — только у пауз', () => {
    assert.equal(statusShowsTimer('work'), false);
    assert.equal(statusShowsTimer('off'), false);
    assert.equal(statusShowsTimer(undefined), false);
    for (const tone of ['break', 'training', 'tech']) assert.equal(statusShowsTimer(tone), true, tone);
});

test('у каждого тона сервера свой цвет, у незнакомого — серый', () => {
    const tones = ['work', 'break', 'training', 'tech'];
    assert.equal(new Set(tones.map(statusDotClass)).size, tones.length);
    assert.equal(statusDotClass('совсем новый'), statusDotClass('other'));
    assert.equal(statusDotClass(undefined), statusDotClass('other'));
});

test('время сервера читается без сдвига пояса', () => {
    assert.equal(clockOf('2026-10-08T18:42:07'), '18:42');
    assert.equal(clockOf('2026-10-08T00:05:00.123456'), '00:05');
    assert.equal(clockOf(''), '');
    assert.equal(clockOf(null), '');
});

test('о смене, закрытой без человека, говорим один раз и с временем', () => {
    assert.equal(autoCloseNote({ onShift: false, auto: true, since: '2026-10-08T18:42:00' }),
        'Прошлая смена закрыта автоматически в 18:42');
    assert.equal(autoCloseNote({ onShift: false, auto: true, since: null }),
        'Прошлая смена закрыта автоматически');
    assert.equal(autoCloseNote({ onShift: false, auto: false, since: '2026-10-08T18:42:00' }), '');
    assert.equal(autoCloseNote({ onShift: true, auto: true, since: '2026-10-08T18:42:00' }), '');
    assert.equal(autoCloseNote(null), '');
});

test('id нажатия уникален и годится серверу', () => {
    const ids = new Set(Array.from({ length: 200 }, newClientEventId));
    assert.equal(ids.size, 200);
    for (const id of ids) assert.match(id, /^[0-9A-Za-z_-]{8,59}$/);
});

test('код верификатора узнаётся по знаку, обычный — нет', () => {
    assert.equal(isChatAccessQr('OTPW:ABCDEF'), true);
    assert.equal(isChatAccessQr('  otpw:abcdef  '), true);
    assert.equal(isChatAccessQr('OTPQ:ABCDEF'), false);
    assert.equal(isChatAccessQr('ABCDEF'), false);
    assert.equal(isChatAccessQr(''), false);
    assert.equal(isChatAccessQr(null), false);
});

test('в поле кода остаются только цифры и не больше длины кода', () => {
    assert.equal(CHAT_ACCESS_CODE_DIGITS, 6);
    assert.equal(cleanAccessCode('482 915'), '482915');
    assert.equal(cleanAccessCode('48-29-15'), '482915');
    assert.equal(cleanAccessCode('код 4829159999'), '482915');
    assert.equal(cleanAccessCode('abc'), '');
    assert.equal(cleanAccessCode(null), '');
});

test('обратный отсчёт до повторной отправки', () => {
    assert.equal(formatCountdown(60), '1:00');
    assert.equal(formatCountdown(45), '0:45');
    assert.equal(formatCountdown(4.2), '0:05');
    assert.equal(formatCountdown(0), '0:00');
    assert.equal(formatCountdown(-3), '0:00');
    assert.equal(formatCountdown(undefined), '0:00');
});
