// «Учет часов»: кто ушёл из выбранной группы до конца месяца — вкладка
// «Переведённые». Сегменты ниже — в той форме, в какой их отдаёт сервер в
// режиме группы: обрезаны по месяцу, is_current — сегмент выбранной группы.
import test from 'node:test';
import assert from 'node:assert/strict';

import { getHoursGroupLeave } from '../src/components/hours/hoursGroupLeave.js';

const OCT = '2026-10';
const seg = (groupId, startDay, endDay, inViewedGroup = false) => ({
    group_id: groupId,
    group_name: `Группа ${groupId}`,
    start_day: startDay,
    end_day: endDay,
    is_current: inViewedGroup,
});

test('перевёлся в другую группу посреди месяца — ушёл, со дня перевода', () => {
    const op = { group_segments: [seg(10, 1, 11, true), seg(6, 12, 31)] };
    assert.deepEqual(getHoursGroupLeave(op, OCT), { day: 12, movedToGroup: true });
});

test('весь месяц в группе — не ушёл', () => {
    assert.equal(getHoursGroupLeave({ group_segments: [seg(10, 1, 31, true)] }, OCT), null);
});

test('пришёл из другой группы — остаётся в «Активных»', () => {
    const op = { group_segments: [seg(6, 1, 11), seg(10, 12, 31, true)] };
    assert.equal(getHoursGroupLeave(op, OCT), null);
});

test('ушёл и вернулся в том же месяце — в группе на конец месяца', () => {
    const op = { group_segments: [seg(10, 1, 9, true), seg(6, 10, 19), seg(10, 20, 31, true)] };
    assert.equal(getHoursGroupLeave(op, OCT), null);
});

test('вывели из группы без новой — ушёл, но не переведён в группу', () => {
    assert.deepEqual(getHoursGroupLeave({ group_segments: [seg(10, 1, 15, true)] }, OCT), { day: 16, movedToGroup: false });
});

test('числился в двух группах и вышел из этой — дальше он в той, второй', () => {
    const op = { group_segments: [seg(10, 1, 10, true), seg(6, 5, 31)] };
    assert.deepEqual(getHoursGroupLeave(op, OCT), { day: 11, movedToGroup: true });
});

test('вторая группа закончилась раньше выхода из этой — переводом не считается', () => {
    const op = { group_segments: [seg(6, 1, 4), seg(10, 5, 20, true)] };
    assert.deepEqual(getHoursGroupLeave(op, OCT), { day: 21, movedToGroup: false });
});

test('последний день месяца берётся из самого месяца: 30 в ноябре, 28 в феврале', () => {
    assert.equal(getHoursGroupLeave({ group_segments: [seg(10, 1, 30, true)] }, '2026-11'), null);
    assert.deepEqual(getHoursGroupLeave({ group_segments: [seg(10, 1, 29, true), seg(6, 30, 30)] }, '2026-11'), { day: 30, movedToGroup: true });
    assert.equal(getHoursGroupLeave({ group_segments: [seg(10, 1, 28, true)] }, '2027-02'), null);
    assert.equal(getHoursGroupLeave({ group_segments: [seg(10, 1, 28, true)] }, '2028-02') !== null, true, '2028 — високосный');
});

test('перевёрнутое членство (конец раньше начала) ухода не означает', () => {
    // Призрак «с 14.09 по 10.09»: дней не покрывает, оператор как стоял в списке, так и стоит.
    assert.equal(getHoursGroupLeave({ group_segments: [seg(10, 14, 10, true), seg(3, 11, 30)] }, '2026-09'), null);
    // Рядом с настоящим членством этой группы перевёрнутое не мешает увидеть уход.
    const op = { group_segments: [seg(15, 1, 16, true), seg(15, 17, 16, true), seg(3, 17, 31)] };
    assert.deepEqual(getHoursGroupLeave(op, '2026-07'), { day: 17, movedToGroup: true });
});

test('без сегментов выбранной группы судить не по чему', () => {
    assert.equal(getHoursGroupLeave({ group_segments: [] }, OCT), null);
    assert.equal(getHoursGroupLeave({}, OCT), null);
    assert.equal(getHoursGroupLeave(null, OCT), null);
    // Сегменты без отметки выбранной группы (ответ «Моих часов», старый кэш) — тоже.
    assert.equal(getHoursGroupLeave({ group_segments: [seg(10, 1, 11), seg(6, 12, 31)] }, OCT), null);
});

test('месяц не распознан — ничего не решаем', () => {
    const op = { group_segments: [seg(10, 1, 11, true), seg(6, 12, 31)] };
    for (const month of ['', null, '2026-13', '2026-1', 'октябрь']) {
        assert.equal(getHoursGroupLeave(op, month), null, String(month));
    }
});

test('дни приходят строками — читаются как числа', () => {
    const op = { group_segments: [{ ...seg(10, '1', '11', true) }, { ...seg(6, '12', '31') }] };
    assert.deepEqual(getHoursGroupLeave(op, OCT), { day: 12, movedToGroup: true });
});
