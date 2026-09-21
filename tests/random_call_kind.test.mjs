import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    CALL_KIND_ANY, CALL_KIND_IN, CALL_KIND_OUT, CALL_KIND_OPTIONS, callKindFlags,
} from '../src/call_evaluation/randomCallKind.js';

/*
 * Тип звонка в окне «Случайный звонок».
 *
 * Ради чего файл существует: до 21.09.2026 в окне стояли две галочки, обе включённые.
 * Супервайзер, которому нужен входящий, нажимал «Входящие» и этим снимал галочку —
 * запрос уходил ровно за противоположным. На проде это видно числом: за 12–19.09 по
 * кнопке пришло 23 исходящих против двух входящих. Теперь выбор ОДИН из трёх, и
 * «нажал Входящие» может означать только входящие.
 */

test('«Входящие» — это только входящие', () => {
    assert.deepEqual(callKindFlags(CALL_KIND_IN), { incoming: true, outgoing: false });
});

test('«Исходящие» — это только исходящие', () => {
    assert.deepEqual(callKindFlags(CALL_KIND_OUT), { incoming: false, outgoing: true });
});

test('«Любые» — оба типа, как было по умолчанию', () => {
    assert.deepEqual(callKindFlags(CALL_KIND_ANY), { incoming: true, outgoing: true });
});

test('незнакомый выбор не превращается в пустой запрос', () => {
    // Сервер отвергает запрос без единого типа («Выберите хотя бы один тип звонка»),
    // поэтому неизвестное значение обязано означать «любые», а не «ничего».
    for (const bad of [undefined, null, '', 'incoming', 42]) {
        assert.deepEqual(callKindFlags(bad), { incoming: true, outgoing: true });
    }
});

test('в окне ровно три варианта и первый — «Любые»', () => {
    assert.deepEqual(CALL_KIND_OPTIONS.map(o => o.value), [CALL_KIND_ANY, CALL_KIND_IN, CALL_KIND_OUT]);
    assert.deepEqual(CALL_KIND_OPTIONS.map(o => o.label), ['Любые', 'Входящие', 'Исходящие']);
});

test('окно журнала больше не хранит два независимых флага', () => {
    // Смысл правки — в том, что состояний три, а не четыре. Вернётся пара
    // setIncoming/setOutgoing — вернётся и ошибка «нажал вход, получил исход».
    const source = readFileSync(new URL('../src/call_evaluation/main.jsx', import.meta.url), 'utf8');
    assert.ok(!/setIncoming\s*\(/.test(source), 'setIncoming вернулся в окно');
    assert.ok(!/setOutgoing\s*\(/.test(source), 'setOutgoing вернулся в окно');
    assert.ok(/callKindFlags\(callKind\)/.test(source), 'флаги запроса должны считаться из выбора');
});
