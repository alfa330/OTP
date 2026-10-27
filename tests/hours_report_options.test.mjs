// «Учет часов»: окно «Сформировать отчёт» — варианты, их описания и выбор.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    buildHoursReportOptions,
    hoursReportTitle,
    pickHoursReportScope,
} from '../src/components/hours/hoursReportOptions.js';

const byValue = (options, value) => options.find((option) => option.value === value);

test('два отчёта — «По СВ» и «Общий», в этом порядке', () => {
    const options = buildHoursReportOptions({ group: { name: 'Чат менеджер' } });
    assert.deepEqual(options.map((option) => [option.value, option.label]), [['by_sv', 'По СВ'], ['all', 'Общий']]);
    for (const option of options) assert.ok(option.description.length > 0, option.value);
});

test('«По СВ» при выбранной группе называет её и объясняет пометку «др.»', () => {
    const bySv = byValue(buildHoursReportOptions({ group: { name: '  Чат менеджер ' } }), 'by_sv');
    assert.equal(bySv.disabled, false);
    assert.match(bySv.description, /^Операторы группы «Чат менеджер»\./);
    assert.match(bySv.description, /«др\.»/);
});

test('«По СВ» без группы — по выбранному СВ', () => {
    const named = byValue(buildHoursReportOptions({ supervisor: { name: 'Сабыр Азана', isMe: false } }), 'by_sv');
    assert.equal(named.disabled, false);
    assert.equal(named.description, 'Операторы супервайзера Сабыр Азана.');
    const me = byValue(buildHoursReportOptions({ supervisor: { name: 'Сабыр Азана', isMe: true } }), 'by_sv');
    assert.equal(me.description, 'Ваши операторы.');
    const unnamed = byValue(buildHoursReportOptions({ supervisor: { name: '', isMe: false } }), 'by_sv');
    assert.equal(unnamed.description, 'Операторы выбранного супервайзера.');
});

test('группа важнее СВ: сервер при группе строит отчёт по ней', () => {
    const bySv = byValue(buildHoursReportOptions({ group: { name: 'Основа' }, supervisor: { name: 'Кастек Гаухар', isMe: false } }), 'by_sv');
    assert.match(bySv.description, /группы «Основа»/);
    assert.doesNotMatch(bySv.description, /Кастек/);
});

test('группа выбрана, но её нет в списке групп — «По СВ» всё равно доступен', () => {
    // Архивная группа или группа чужого отдела: на неё попадают щелчком по чужому
    // дню переведённого, и сервер отчёт по ней отдаёт.
    const bySv = byValue(buildHoursReportOptions({ group: { name: '' } }), 'by_sv');
    assert.equal(bySv.disabled, false);
    assert.match(bySv.description, /^Операторы выбранной группы\. Дни, когда оператор был в другой группе/);
    assert.equal(pickHoursReportScope(buildHoursReportOptions({ group: {} }), 'by_sv'), 'by_sv');
});

test('ни группы, ни СВ — «По СВ» строить не по чему', () => {
    const bySv = byValue(buildHoursReportOptions({}), 'by_sv');
    assert.equal(bySv.disabled, true);
    assert.match(bySv.description, /выберите группу/);
});

test('«Общий» — отдел или вся компания, со строками СВ там, где сервер их добавит', () => {
    assert.equal(
        byValue(buildHoursReportOptions({}), 'all').description,
        'Все операторы отдела с колонкой «Супервайзер».',
    );
    assert.equal(
        byValue(buildHoursReportOptions({ companyWide: true }), 'all').description,
        'Все операторы компании с колонкой «Супервайзер».',
    );
    const withSv = byValue(buildHoursReportOptions({ companyWide: true, withSupervisorRows: true }), 'all');
    assert.match(withSv.description, /Супервайзеры с часами по Clockster — отдельными строками\.$/);
    assert.equal(withSv.disabled, false);
});

test('выбор окна: прошлый, если доступен, иначе первый доступный', () => {
    const withGroup = buildHoursReportOptions({ group: { name: 'Основа' } });
    assert.equal(pickHoursReportScope(withGroup, 'by_sv'), 'by_sv');
    assert.equal(pickHoursReportScope(withGroup, 'all'), 'all');
    assert.equal(pickHoursReportScope(withGroup, 'unknown'), 'by_sv');
    // Без группы и СВ «По СВ» недоступен — остаётся «Общий», даже если раньше выбирали «По СВ».
    assert.equal(pickHoursReportScope(buildHoursReportOptions({}), 'by_sv'), 'all');
    assert.equal(pickHoursReportScope([], 'by_sv'), null);
    assert.equal(pickHoursReportScope(null, 'by_sv'), null);
});

test('заголовок окна — месяц словами', () => {
    assert.equal(hoursReportTitle('2026-10'), 'Отчёт за октябрь 2026');
    assert.equal(hoursReportTitle('2026-05'), 'Отчёт за май 2026');
    assert.equal(hoursReportTitle('2027-01'), 'Отчёт за январь 2027');
    assert.equal(hoursReportTitle('2026-13'), 'Отчёт');
    assert.equal(hoursReportTitle(''), 'Отчёт');
});
