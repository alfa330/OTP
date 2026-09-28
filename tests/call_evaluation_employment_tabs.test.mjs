import test from 'node:test';
import assert from 'node:assert/strict';

import {
    TRANSFERRED_TAB,
    buildEmploymentStatusBuckets,
    buildEmploymentStatusTabs,
    classifyAnalyticsOperator,
    describeDismissalChip,
    describeTransferChip,
    getGroupTransfer,
} from '../src/call_evaluation/employmentTabs.js';

/*
 * Вкладки «Аналитики» журнала оценок: Активные / Переведённые / Уволенные.
 *
 * Ради чего файл существует: задача #347 (Кастек Гаухар, 20.09.2026). Во вкладке
 * «Активные» группы «Кастек Гаухар группа Основа» за сентябрь висели:
 *   • Абдрахманова Айман — 08.09 переведена в «Сабыр Азана группа Чат менеджер»,
 *     а в старой группе стояла среди активных с чипом «переведён»;
 *   • Абылғазы Айнұр — уволена 14.09, но «уволенный в этом месяце» оставался в
 *     активных по старому правилу.
 * Сегменты ниже — ровно те, что сервер отдаёт по этим людям (group_segments).
 */

const OSNOVA = 10;   // Кастек Гаухар группа Основа
const CHAT = 6;      // Сабыр Азана группа Чат менеджер
const ELEKOVA = 3;   // Элекова Арайлым Акылбековна группа Основа
const POLAT = 8;     // Полатжанкызы Жанис группа Основа

const seg = (groupId, groupName, startDay, endDay, isCurrent = false) => ({
    group_id: groupId,
    group_name: groupName,
    start_day: startDay,
    end_day: endDay,
    is_current: isCurrent,
});

const abdrakhmanova = {
    id: 330,
    name: 'Абдрахманова Айман Карасаевна',
    status: 'working',
    group_segments: [
        seg(OSNOVA, 'Кастек Гаухар группа Основа', 1, 7),
        seg(CHAT, 'Сабыр Азана группа Чат менеджер', 8, 30, true),
    ],
};

const abylgazy = {
    id: 482,
    name: 'Абылғазы Айнұр Ерланқызы',
    status: 'fired',
    dismissal_date: '2026-09-14',
    group_segments: [seg(OSNOVA, 'Кастек Гаухар группа Основа', 8, 30, true)],
};

const SEPT = { month: '2026-09', groupId: OSNOVA };

test('переведённая ИЗ группы — во вкладке «Переведённые», а не в «Активных»', () => {
    assert.equal(classifyAnalyticsOperator(abdrakhmanova, SEPT).tab, TRANSFERRED_TAB);
});

test('в новой группе та же переведённая — в «Активных»', () => {
    const info = classifyAnalyticsOperator(abdrakhmanova, { month: '2026-09', groupId: CHAT });
    assert.equal(info.tab, 'working');
    assert.deepEqual(info.transfer, { direction: 'in', day: 8, groupName: 'Кастек Гаухар группа Основа' });
});

test('уволенная в просматриваемом месяце — в «Уволенных»', () => {
    assert.equal(classifyAnalyticsOperator(abylgazy, SEPT).tab, 'fired');
});

test('уволенный давно — тоже в «Уволенных», и без чипа с датой', () => {
    const old = { status: 'fired', dismissal_date: '2026-05-13', group_segments: [seg(OSNOVA, 'Основа', 1, 30, true)] };
    assert.equal(classifyAnalyticsOperator(old, SEPT).tab, 'fired');
    assert.equal(describeDismissalChip(old, '2026-09'), null);
});

test('уволенный без даты увольнения — всё равно в «Уволенных»', () => {
    assert.equal(classifyAnalyticsOperator({ status: 'fired' }, SEPT).tab, 'fired');
});

test('устаревшие коды приводятся: dismissal — уволен, unpaid_leave — Б/С', () => {
    assert.equal(classifyAnalyticsOperator({ status: 'dismissal' }, SEPT).tab, 'fired');
    assert.equal(classifyAnalyticsOperator({ status: 'unpaid_leave' }, SEPT).tab, 'bs');
});

test('статус «уволен», но увольнение ПОЗЖЕ месяца — в этом месяце ещё работал', () => {
    // Без записи в истории сервер отдаёт сегодняшний статус вместо статуса месяца.
    const info = classifyAnalyticsOperator(
        { status: 'fired', dismissal_date: '2026-09-10', group_segments: [seg(OSNOVA, 'Основа', 1, 31, true)] },
        { month: '2026-08', groupId: OSNOVA },
    );
    assert.equal(info.tab, 'working');
    assert.equal(info.statusKey, 'working', 'бейджа «Уволен» в августе быть не должно');
});

test('уволенный после перевода — в «Уволенных», увольнение важнее перевода', () => {
    const op = {
        status: 'fired',
        dismissal_date: '2026-09-20',
        group_segments: [seg(OSNOVA, 'Основа', 1, 7), seg(CHAT, 'Чат менеджер', 8, 20)],
    };
    assert.equal(classifyAnalyticsOperator(op, SEPT).tab, 'fired');
});

test('Б/С и два перевода за месяц: в каждой группе своя вкладка', () => {
    // Серикбаева Асел: Чат 01–02.09 → Элекова 03–19.09 → Полатжанкызы с 20.09, статус Б/С.
    const op = {
        status: 'bs',
        group_segments: [
            seg(CHAT, 'Сабыр Азана группа Чат менеджер', 1, 2),
            seg(ELEKOVA, 'Элекова Арайлым Акылбековна группа Основа', 3, 19),
            seg(POLAT, 'Полатжанкызы Жанис группа Основа', 20, 30, true),
        ],
    };
    const inChat = classifyAnalyticsOperator(op, { month: '2026-09', groupId: CHAT });
    assert.equal(inChat.tab, TRANSFERRED_TAB);
    assert.equal(inChat.transfer.day, 3);
    assert.equal(inChat.transfer.groupName, 'Элекова Арайлым Акылбековна группа Основа');

    const inElekova = classifyAnalyticsOperator(op, { month: '2026-09', groupId: ELEKOVA });
    assert.equal(inElekova.tab, TRANSFERRED_TAB);
    assert.equal(inElekova.transfer.groupName, 'Полатжанкызы Жанис группа Основа');
    assert.equal(inElekova.statusKey, 'bs', 'в «Переведённых» остаётся бейдж Б/С');

    const inPolat = classifyAnalyticsOperator(op, { month: '2026-09', groupId: POLAT });
    assert.equal(inPolat.tab, 'bs');
    assert.equal(inPolat.transfer.direction, 'in');
});

test('членство до последнего дня месяца — ещё в группе, не «переведён»', () => {
    const op = { status: 'working', group_segments: [seg(OSNOVA, 'Основа', 1, 30)] };
    assert.equal(classifyAnalyticsOperator(op, SEPT).tab, 'working');
    assert.equal(getGroupTransfer(op, OSNOVA, '2026-09'), null);
});

test('ушёл и вернулся в том же месяце — в «Активных», пришёл из другой группы', () => {
    const op = {
        status: 'working',
        group_segments: [seg(OSNOVA, 'Основа', 1, 10), seg(CHAT, 'Чат менеджер', 11, 20), seg(OSNOVA, 'Основа', 21, 30, true)],
    };
    const info = classifyAnalyticsOperator(op, SEPT);
    assert.equal(info.tab, 'working');
    assert.deepEqual(info.transfer, { direction: 'in', day: 21, groupName: 'Чат менеджер' });
});

test('выведен из группы без новой — тоже «Переведённые», в подсказке без группы', () => {
    const op = { status: 'working', group_segments: [seg(OSNOVA, 'Основа', 1, 12)] };
    const info = classifyAnalyticsOperator(op, SEPT);
    assert.equal(info.tab, TRANSFERRED_TAB);
    assert.deepEqual(info.transfer, { direction: 'out', day: 13, groupName: '' });
    assert.match(describeTransferChip(info.transfer, '2026-09').title, /^Выведен\(а\) из группы с 13\.09\.2026\./);
});

test('членство «конец раньше начала» не считается ни днём в другой группе', () => {
    // На проде 16 таких: перевод отменили в момент заведения (ЯР 17.07–16.07).
    // В группе ЯР человек не был ни дня — значит, и «переведён из ЯР» быть не может.
    const op = {
        status: 'working',
        group_segments: [seg(99, 'группа ЯР', 17, 16), seg(OSNOVA, 'Основа', 17, 31, true)],
    };
    assert.equal(getGroupTransfer(op, OSNOVA, '2026-07'), null);
    assert.equal(classifyAnalyticsOperator(op, { month: '2026-07', groupId: OSNOVA }).tab, 'working');
});

test('весь месяц здесь и ещё в другой группе — отдельный чип, не «в группе с 01»', () => {
    const op = {
        status: 'working',
        group_segments: [seg(OSNOVA, 'Основа', 1, 30, true), seg(CHAT, 'Чат менеджер', 5, 30, true)],
    };
    const info = classifyAnalyticsOperator(op, SEPT);
    assert.equal(info.tab, 'working');
    assert.equal(info.transfer.direction, 'shared');
    assert.equal(describeTransferChip(info.transfer, '2026-09').text, 'две группы');
});

test('без сегментов групп (старый ответ) делим только по статусу', () => {
    assert.equal(classifyAnalyticsOperator({ status: 'working' }, SEPT).tab, 'working');
    assert.equal(classifyAnalyticsOperator({ status: 'bs' }, { month: '2026-09' }).tab, 'bs');
});

test('последний день месяца считается верно и в декабре, и в феврале', () => {
    const dec = { status: 'working', group_segments: [seg(OSNOVA, 'Основа', 1, 31, false)] };
    assert.equal(classifyAnalyticsOperator(dec, { month: '2026-12', groupId: OSNOVA }).tab, 'working');
    const feb = { status: 'working', group_segments: [seg(OSNOVA, 'Основа', 1, 28, false)] };
    assert.equal(classifyAnalyticsOperator(feb, { month: '2027-02', groupId: OSNOVA }).tab, 'working');
    const febLeft = { status: 'working', group_segments: [seg(OSNOVA, 'Основа', 1, 27, false)] };
    assert.equal(classifyAnalyticsOperator(febLeft, { month: '2027-02', groupId: OSNOVA }).tab, TRANSFERRED_TAB);
});

test('чипы: дата перевода и увольнения, слово «переведён» в «Активных» не появляется', () => {
    const out = describeTransferChip(classifyAnalyticsOperator(abdrakhmanova, SEPT).transfer, '2026-09');
    assert.equal(out.text, 'с 08.09');
    assert.match(out.title, /в группу «Сабыр Азана группа Чат менеджер» с 08\.09\.2026/);
    assert.match(out.title, /за месяц целиком/);

    const incoming = describeTransferChip(
        classifyAnalyticsOperator(abdrakhmanova, { month: '2026-09', groupId: CHAT }).transfer, '2026-09');
    assert.equal(incoming.text, 'в группе с 08.09');
    assert.doesNotMatch(incoming.text, /переведён/);

    assert.deepEqual(describeDismissalChip(abylgazy, '2026-09'), { text: 'с 14.09', title: 'Уволен(а) с 14.09.2026' });
});

test('вкладки: «Переведённые» — только когда есть кто-то, место перед «Уволенными»', () => {
    const rows = [
        abdrakhmanova,
        abylgazy,
        { id: 1, status: 'working', group_segments: [seg(OSNOVA, 'Основа', 1, 30, true)] },
        { id: 2, status: 'bs', group_segments: [seg(OSNOVA, 'Основа', 1, 30, true)] },
    ];
    const { buckets, infoByOperator } = buildEmploymentStatusBuckets(rows, SEPT);
    const tabs = buildEmploymentStatusTabs(buckets);
    assert.deepEqual(tabs.map((t) => [t.key, t.label, t.count]), [
        ['working', 'Активные', 1],
        ['bs', 'Б/С', 1],
        [TRANSFERRED_TAB, 'Переведённые', 1],
        ['fired', 'Уволенные', 1],
    ]);
    assert.equal(infoByOperator.get(abdrakhmanova).tab, TRANSFERRED_TAB);

    const quiet = buildEmploymentStatusTabs(buildEmploymentStatusBuckets(
        [{ status: 'working', group_segments: [seg(OSNOVA, 'Основа', 1, 30, true)] }], SEPT).buckets);
    assert.deepEqual(quiet.map((t) => t.key), ['working', 'fired'], 'пустых «Переведённых» нет, опорная пара на месте');
});

test('незнакомый статус получает свою вкладку перед «Уволенными»', () => {
    const { buckets } = buildEmploymentStatusBuckets([{ status: 'maternity' }, abylgazy], SEPT);
    assert.deepEqual(buildEmploymentStatusTabs(buckets).map((t) => t.key), ['working', 'maternity', 'fired']);
});
