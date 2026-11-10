/*
 * «Удаленный КЦ» → «Аналитика» → «По дням» (владелец, 10.11.2026): таблицу сделать понятнее —
 * «Подписали» перенести в конец и назвать «Подписали в общ.», «Успешки» назвать
 * «Подписали после звонка».
 *
 * Что защищаем:
 *   • порядок и названия столбцов таблицы;
 *   • числа в строке дня стоят под своими заголовками: после звонка — successes, в общ. — signed;
 *   • тот же показатель в переключателе над таблицей называется так же, а не «Успешки».
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').split('\r\n').join('\n');

const source = read('../src/components/dial_list/DialListAnalytics.jsx');
const card = source.slice(source.indexOf('const ByDayCard'), source.indexOf('const DialListAnalytics'));
const table = card.slice(card.indexOf('<table'), card.indexOf('</table>'));

const headText = (cell) => cell
    .replace(/<\/?WrapHead>/g, '')
    .replace(/&nbsp;/g, ' ')
    .trim();

test('столбцы «По дням»: подписавшие после звонка, в конце — все подписавшие', () => {
    assert.ok(card && table, 'карточка «По дням» и таблица найдены');
    const thead = table.slice(table.indexOf('<thead'), table.indexOf('</thead>'));
    const heads = [...thead.matchAll(/<Th(?: right)?>(.*?)<\/Th>/g)].map((m) => headText(m[1]));
    assert.deepEqual(heads, [
        'День', 'Звонки', 'Разговоры ≥10 с', 'Время разговоров', 'Подписали после звонка', 'Подписали в общ.',
    ]);
});

test('числа строки дня — под своими заголовками', () => {
    const tbody = table.slice(table.indexOf('<tbody'), table.indexOf('</tbody>'));
    const cells = [...tbody.matchAll(/<Td(?: right)?(?: muted)?>\{(.*?)\}<\/Td>/g)].map((m) => m[1]);
    assert.deepEqual(cells, [
        'dayLabel(d.day)', 'num(d.attempts)', 'num(d.talked)', 'fmtTalk(d.talk_sec)', 'num(d.successes)', 'num(d.signed)',
    ]);
});

test('переключатель над таблицей называет показатель так же, как столбец', () => {
    const metrics = source.slice(source.indexOf('const METRICS'), source.indexOf('];', source.indexOf('const METRICS')));
    assert.match(metrics, /\{ value: 'successes', label: 'Подписали после звонка', name: 'Подписавших после звонка' \}/);
    assert.doesNotMatch(metrics, /'Успешки'/);
});
