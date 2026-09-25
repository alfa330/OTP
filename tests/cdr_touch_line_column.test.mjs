import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { lineCaption, prettyPhone, splitCallType } from '../src/components/cdr/touchMeta.js';

/* Колонка «Линия» вместо «Очереди»: номер парка, а под ним парк и очередь.
 *
 * Отдел продаж спросил «на какой номер (таксопарк) звонили» — в разделе была только
 * очередь «3034», которая человеку ничего не говорит. Номер и парк приходят с сервера
 * (`line_number`, `line_park`, см. cdr/lines.py); здесь закреплено, как они читаются.
 */

test('под номером — парк и очередь звонка', () => {
    assert.equal(lineCaption('Jana такси', '3034'), 'Jana такси · 3034');
});

test('у заявки автообзвона парк номера и очередь кампании разные — видны оба', () => {
    assert.equal(lineCaption('Центр регистрации', '3016'), 'Центр регистрации · 3016');
});

test('исходящий без очереди — только парк', () => {
    assert.equal(lineCaption('Ноль такси', ''), 'Ноль такси');
});

test('парк без подписи сервер называет очередью — второй раз её не повторяем', () => {
    assert.equal(lineCaption('очередь 3016', '3016'), 'очередь 3016');
});

test('без парка остаётся очередь, без всего — пусто', () => {
    assert.equal(lineCaption('', '3034'), 'очередь 3034');
    assert.equal(lineCaption('', ''), '');
    assert.equal(lineCaption(null, undefined), '');
});

test('перевод между очередями читается через запятую с пробелом', () => {
    assert.equal(lineCaption('Jana такси', '3034,3041'), 'Jana такси · 3034, 3041');
});

test('номер линии показывается как его набирают, пустой — прочерком', () => {
    assert.equal(prettyPhone('7475777778'), '+7 747 577 77 78');
    assert.equal(prettyPhone(''), '—');
});

test('в таблице колонка «Линия» с номером парка, а фильтр — по таксопарку', () => {
    const source = readFileSync(new URL('../src/components/cdr/TouchesView.jsx', import.meta.url), 'utf8');
    assert.ok(/<Th>Линия<\/Th>/.test(source), 'нет колонки «Линия»');
    assert.ok(!/<Th>Очередь<\/Th>/.test(source), 'одинокая очередь вернулась в таблицу');
    assert.ok(/prettyPhone\(touch\.line_number\)/.test(source), 'номер — из поля касания');
    assert.ok(/lineCaption\(touch\.line_park, touch\.queue\)/.test(source));
    assert.ok(/params\.park = filters\.park/.test(source), 'фильтр парка не уходит на сервер');
    assert.ok(/filter_values\?\.parks/.test(source), 'список парков — из данных периода');
});

test('подсказка ячейки доходит до <td>', () => {
    /* Td принимал title и никуда его не передавал — подсказки молча терялись. */
    const source = readFileSync(new URL('../src/components/cdr/TouchesView.jsx', import.meta.url), 'utf8');
    assert.ok(/const Td = \(\{ children, className = '', title \}\)/.test(source));
    assert.ok(/<td title=\{title\}/.test(source));
});

test('не дошедшие до очереди — свой тип в переключателе и отдельная цифра у «Касаний»', () => {
    /* Решение владельца 24.09.2026: такие звонки видны строками, но не касания. */
    const source = readFileSync(new URL('../src/components/cdr/TouchesView.jsx', import.meta.url), 'utf8');
    assert.ok(/value: TYPE_IN_BEFORE_QUEUE, label: 'До очереди'/.test(source), 'нет сегмента «До очереди»');
    assert.ok(/summary\.before_queue/.test(source), 'число не дошедших не показано рядом с «Касаний»');
    assert.ok(/оператору не поступал/.test(source));
});

test('уточнение типа уходит второй строкой — таблица помещается в раздел', () => {
    /* Одной строкой «Входящий (не дошёл до очереди)» был шириной 244px, и таблица
       вылезала за край раздела даже на широком мониторе. */
    assert.deepEqual(splitCallType('Входящий (не дошёл до очереди)'), { base: 'Входящий', note: 'не дошёл до очереди' });
    assert.deepEqual(splitCallType('Входящий (не приняли)'), { base: 'Входящий', note: 'не приняли' });
    assert.deepEqual(splitCallType('Исходящий'), { base: 'Исходящий', note: '' });
    assert.deepEqual(splitCallType(''), { base: '', note: '' });
    const source = readFileSync(new URL('../src/components/cdr/TouchesView.jsx', import.meta.url), 'utf8');
    assert.ok(/max-w-\[1480px\]/.test(source), 'раздел снова зажат по ширине');
    assert.ok(/inline-flex whitespace-nowrap rounded-full/.test(source), 'плашка результата переносится');
});
