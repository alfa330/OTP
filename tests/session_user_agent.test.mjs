import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { parseUserAgent, DEVICE_LABELS, roleLabel, sessionWord, personWord, addressWord } from '../src/components/sessions/userAgent.js';

const here = dirname(fileURLToPath(import.meta.url));
const corpus = JSON.parse(readFileSync(join(here, 'data', 'session_user_agents.json'), 'utf8'));

/**
 * Тип устройства в разделе «Сессии» считается ДВАЖДЫ: в SQL (по нему фильтруют
 * и считают плашки) и здесь (по нему рисуют строку и карточку). Разъехавшиеся
 * копии дают самое неприятное — плашка «Планшет 12» с пустым списком. Корпус
 * общий с серверным тестом, поэтому расхождение видно сразу с обеих сторон.
 */
test('тип устройства совпадает с ожидаемым по всему корпусу', () => {
    for (const { ua, type } of corpus.cases) {
        assert.equal(parseUserAgent(ua).type, type, ua || '(пустой user-agent)');
    }
});

test('порядок проверок: бот важнее планшета, планшет важнее телефона', () => {
    // Android-бот: строка подходит и под «android без mobile» (планшет), и под бот.
    assert.equal(parseUserAgent('Mozilla/5.0 (Linux; Android 13) AdsBot-Google').type, 'bot');
    // Android без mobile — планшет, хотя MOBILE_RE тоже сработала бы на 'android'.
    assert.equal(parseUserAgent('Mozilla/5.0 (Linux; Android 13; SM-X200) Chrome/124 Safari').type, 'tablet');
});

test('пустой user-agent не притворяется ПК', () => {
    assert.equal(parseUserAgent('').type, 'unknown');
    assert.equal(parseUserAgent(null).type, 'unknown');
    assert.equal(parseUserAgent(undefined).label, 'Неизвестно');
});

test('система и браузер вынимаются для показа', () => {
    const mac = parseUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/17.4 Safari/605.1.15');
    assert.equal(mac.os, 'macOS 10.15');
    assert.equal(mac.browser, 'Safari');

    const android = parseUserAgent('Mozilla/5.0 (Linux; Android 13; SM-A536E) Chrome/124.0.0.0 Mobile Safari/537.36');
    assert.equal(android.os, 'Android 13');
    assert.equal(android.browser, 'Chrome');

    // Edge и Яндекс маскируются под Chrome — их проверяем раньше.
    assert.equal(parseUserAgent('Mozilla/5.0 (Windows NT 10.0) Chrome/124 Safari/537.36 Edg/124').browser, 'Edge');
    assert.equal(parseUserAgent('Mozilla/5.0 (Windows NT 10.0) Chrome/122 YaBrowser/24.4 Safari/537.36').browser, 'Яндекс');
});

test('подписи типов и ролей — по-русски', () => {
    assert.equal(DEVICE_LABELS.desktop, 'ПК');
    assert.equal(roleLabel('super_admin'), 'Админ', 'super_admin показывается админом, как и в списке');
    assert.equal(roleLabel('sv'), 'Супервайзер');
    assert.equal(roleLabel(null), '—');
});

test('русские числительные — раздел не пишет «42 сессий» и «2 адрес»', () => {
    assert.equal(sessionWord(1), 'сессия');
    assert.equal(sessionWord(2), 'сессии');
    assert.equal(sessionWord(5), 'сессий');
    assert.equal(sessionWord(11), 'сессий', '11–14 — исключение, не «сессия»');
    assert.equal(sessionWord(21), 'сессия');
    assert.equal(sessionWord(42), 'сессии');
    assert.equal(sessionWord(0), 'сессий');

    assert.equal(personWord(1), 'сотрудник');
    assert.equal(personWord(3), 'сотрудника');
    assert.equal(personWord(9), 'сотрудников');
    assert.equal(personWord(112), 'сотрудников');

    assert.equal(addressWord(1), 'адрес');
    assert.equal(addressWord(2), 'адреса');
    assert.equal(addressWord(7), 'адресов');
});
