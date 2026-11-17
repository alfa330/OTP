import test from 'node:test';
import assert from 'node:assert/strict';

import {
    OP_CHAT_SHIFT_COLUMNS,
    chatJournalFilterChips,
    chatJournalQuery,
    chatJournalStyle,
    chatLogoutTitle,
    chatShiftsExportFileName,
    filterChatJournal,
} from '../src/components/monitoring/opChatShiftsShared.js';
import { exportQuery, filterShifts } from '../src/components/monitoring/opActivityShared.js';

/*
 * «Отчёт по сменам» · «Чат» (SALES-1, #424). Колонки — строго в порядке ТЗ и его словами (сервер
 * сверяет те же подписи с листом Excel, tests/test_op_chat_shifts.py), журнал — метки-итоги, как у
 * линии, фильтр строк — общий с линией и выгрузкой.
 */

test('колонки — 14 колонок ТЗ в его порядке, у каждой, кроме ФИО, есть «как считается»', () => {
    assert.deepEqual(OP_CHAT_SHIFT_COLUMNS.map((column) => column.label), [
        'Дата', 'Оператор', 'Группа', 'Время входа в систему', 'Среднее время первого ответа',
        'Общее время обработки чатов', 'Активное время работы', 'Время ожидания', 'Время перерывов',
        'Время технического перерыва', 'Количество взятых в работу чатов', 'Количество обработанных чатов',
        'Время выхода из системы', 'Фактически отработанное время',
    ]);
    for (const column of OP_CHAT_SHIFT_COLUMNS) {
        if (column.key === 'name') continue;
        assert.ok(column.hint && column.hint.length > 20, column.key);
    }
    const numeric = OP_CHAT_SHIFT_COLUMNS.filter((column) => column.numeric).map((column) => column.key);
    assert.deepEqual(numeric, ['first_reply', 'handling', 'active', 'waiting', 'break', 'tech', 'taken', 'processed', 'worked']);
});

test('журнал смены ищется по человеку и входу, без конца смены', () => {
    assert.equal(chatJournalQuery({ operator_id: 7, login_at: '2026-11-10T09:00:00', logout_at: '2026-11-10T18:00:00' }),
        'operator_id=7&start=2026-11-10T09%3A00%3A00');
});

test('метки журнала — итоги по видам, пустые не показываются', () => {
    const segments = [
        { kind: 'wait', seconds: 600 }, { kind: 'work', seconds: 300 }, { kind: 'break', seconds: 900 },
        { kind: 'training', seconds: 1200 }, { kind: 'work', seconds: 60 }, { kind: 'logout', seconds: 0 },
    ];
    const chips = chatJournalFilterChips(segments);
    assert.deepEqual(chips.map((chip) => [chip.key, chip.seconds]), [
        ['all', null], ['work', 360], ['wait', 600], ['pause', 2100],
    ]);
    // Выход — мгновение: метка «Вне системы» появляется только со временем вне системы.
    assert.deepEqual(chatJournalFilterChips([...segments, { kind: 'offline', seconds: 1200 }]).map((chip) => [chip.key, chip.seconds]).at(-1),
        ['offline', 1200]);
    assert.deepEqual(chatJournalFilterChips([{ kind: 'wait', seconds: 5 }]).map((chip) => chip.key), ['all', 'wait']);
    assert.deepEqual(filterChatJournal(segments, 'pause').map((s) => s.kind), ['break', 'training']);
    assert.equal(filterChatJournal(segments, 'all').length, segments.length);
    assert.equal(filterChatJournal(segments, 'нет такой').length, segments.length);
});

test('цвета журнала — по виду, незнакомый — нейтральный', () => {
    assert.match(chatJournalStyle('work').bar, /blue/);
    assert.match(chatJournalStyle('offline').chip, /red/);
    assert.equal(chatJournalStyle('что-то'), chatJournalStyle('other'));
});

test('выход, который поставила система, подписан словами', () => {
    assert.equal(chatLogoutTitle({ logout_kind: 'manual' }), undefined);
    assert.match(chatLogoutTitle({ logout_kind: 'auto' }), /портал/);
    assert.match(chatLogoutTitle({ logout_kind: 'cut' }), /не закрыли/);
    assert.match(chatLogoutTitle({ logout_kind: 'phone' }), /iCORE Phone/);
});

test('фильтр строк и запрос выгрузки — общие с линией', () => {
    const rows = [
        { key: 'a', date: '2026-11-10', operator_id: 7, group_key: 'verifier' },
        { key: 'b', date: '2026-11-11', operator_id: 8, group_key: 'verifier' },
    ];
    assert.deepEqual(filterShifts(rows, { group: 'all', operatorIds: [8], days: [] }).map((r) => r.key), ['b']);
    assert.deepEqual(filterShifts(rows, { group: 'verifier', operatorIds: [], days: ['2026-11-10'] }).map((r) => r.key), ['a']);
    assert.equal(exportQuery({ month: '2026-11', group: 'all', operatorIds: [7], days: [] }), 'month=2026-11&operator_ids=7');
    assert.equal(chatShiftsExportFileName('2026-11'), 'smeny_chat_2026-11.xlsx');
});
