import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { seconds } from '../src/components/cdr/touchMeta.js';

/* Колонки «IVR» и «Ожид. в очереди» вместо прежней «Вызов».
 *
 * Прежняя колонка показывала длительность строки станции: у звонка 21.09.2026 19:28:46
 * там стояло 9 секунд — столько же, сколько в «Разговоре», — хотя человек слушал
 * приветствие 26 секунд и ждал в очереди ещё одну. Две величины вместо одной.
 *
 * Отдельно закреплено различие «ноль» и «неизвестно»: у `hms` ноль — это прочерк
 * (разговора не было), а у ожидания ноль означает «ответили в ту же секунду».
 */

test('ноль секунд ожидания — это значение, а не прочерк', () => {
    assert.equal(seconds(0), '0 с');
});

test('неизвестное ожидание — прочерк', () => {
    for (const empty of [null, undefined, '', 'мусор', -3]) {
        assert.equal(seconds(empty), '—');
    }
});

test('секунды до минуты — с подписью, дальше — минуты и секунды', () => {
    assert.equal(seconds(1), '1 с');
    assert.equal(seconds(26), '26 с');
    assert.equal(seconds(59), '59 с');
    assert.equal(seconds(60), '1:00');
    assert.equal(seconds(95), '1:35');
    assert.equal(seconds(197), '3:17');
});

test('в таблице раздела нет «Вызова», а есть приветствие и ожидание', () => {
    const source = readFileSync(new URL('../src/components/cdr/TouchesView.jsx', import.meta.url), 'utf8');
    assert.ok(!/<Th className="text-right">Вызов<\/Th>/.test(source), 'колонка «Вызов» вернулась');
    assert.ok(/<Th className="text-right">IVR<\/Th>/.test(source));
    assert.ok(/<Th className="text-right">Ожид\. в очереди<\/Th>/.test(source));
    assert.ok(/seconds\(touch\.ivr_seconds\)/.test(source), 'IVR должен браться из поля касания');
    assert.ok(/seconds\(touch\.wait_seconds\)/.test(source), 'ожидание — из поля касания');
    assert.ok(!/touch\.dial_seconds/.test(source), 'старое поле больше не показываем');
});

test('подпись под таблицей больше не обещает плечо агента', () => {
    // Плечо агента станция перестала отдавать 09.09.2026, и подпись обещала то,
    // чего в цифрах нет; теперь она объясняет две новые колонки.
    const source = readFileSync(new URL('../src/components/cdr/TouchesView.jsx', import.meta.url), 'utf8');
    assert.ok(!/по плечу самого агента/.test(source));
    assert.ok(/сколько человек слушал приветствие или меню до очереди/.test(source));
});
