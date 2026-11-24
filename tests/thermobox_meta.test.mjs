// «Термокороба» (#363): проверка чисел, «было → стало», отбор, даты.
// Пары с сервером (подписи, пределы, имя файла) сверяет tests/test_thermoboxes.py.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
    COUNT_FIELDS, MEMO_KINDS, availabilityOf, changedFields, guessMemoKind, memoKindOf, splitMemo, citiesOf, depositShort, draftErrors, draftOf, exportFileName,
    fieldError, fmtStamp, latestUpdate, matchesRow, ordersLabel, parseWhole, periodLabel,
    periodShort, sourceNotOlder, sourceState, valueOf,
} from '../src/components/thermoboxes/thermoboxMeta.js';

// 1 октября 2026, 15:00 в Алматы (UTC+5).
const NOW = new Date(Date.UTC(2026, 9, 1, 10, 0));

const ROW = {
    id: 1, city: 'Алматы', name: 'Офис', address: '7-й микрорайон, 5',
    free_boxes: 28, thermo_bags: 0, used_boxes: 0,
    tariff: 'auto_couriers', min_orders: 20, period_days: 7, deposit_tenge: 5000, special_condition: null,
};

test('только целые неотрицательные числа', () => {
    assert.equal(parseWhole('12'), 12);
    assert.equal(parseWhole(' 1 200 '), 1200);
    assert.equal(parseWhole(''), null);
    for (const bad of ['-1', '1.5', '1,5', 'пять', '1e3']) assert.ok(Number.isNaN(parseWhole(bad)), bad);
});

test('ошибка поля говорит, что не так', () => {
    assert.equal(fieldError('free_boxes', '5'), '');
    assert.equal(fieldError('free_boxes', '-2'), 'Не может быть меньше нуля');
    assert.equal(fieldError('free_boxes', '2.5'), 'Только целое число');
    assert.equal(fieldError('free_boxes', ''), 'Укажите число');
    assert.equal(fieldError('free_boxes', '100001'), 'Слишком большое число');
    assert.equal(fieldError('period_days', '0'), 'Не меньше 1');
    assert.equal(fieldError('tariff', 'нечто'), 'Выберите тариф');
    assert.equal(fieldError('special_condition', 'я'.repeat(201)), 'Не длиннее 200 знаков');
});

test('те же числа — не изменение', () => {
    const draft = draftOf(ROW);
    assert.deepEqual(changedFields(ROW, draft), {});
    assert.deepEqual(changedFields(ROW, { ...draft, free_boxes: '28 ' }), {});
    assert.deepEqual(changedFields(ROW, { ...draft, free_boxes: '26', special_condition: '  НЕ  новички ' }),
        { free_boxes: 26, special_condition: 'НЕ новички' });
    // Пустое условие — null, как у сервера, а не пустая строка.
    assert.equal(valueOf('special_condition', '   '), null);
});

test('ошибки черновика только по своим полям', () => {
    const draft = { ...draftOf(ROW), thermo_bags: '-1', min_orders: 'x' };
    assert.deepEqual(Object.keys(draftErrors(draft, COUNT_FIELDS)), ['thermo_bags']);
    assert.deepEqual(Object.keys(draftErrors(draft)).sort(), ['min_orders', 'thermo_bags']);
    assert.deepEqual(draftErrors(undefined, COUNT_FIELDS), {});
});

test('отбор — пара серверного rules.matches', () => {
    const row = { ...ROW, special_condition: 'НЕ новички' };
    assert.ok(matchesRow(row, 'алматы', ''));
    assert.ok(!matchesRow(row, 'Астана', ''));
    assert.ok(matchesRow(row, '', 'микрорайон новички'));
    assert.ok(!matchesRow(row, '', 'микрорайон депозит'));
    assert.ok(matchesRow({ city: 'Семей', address: 'Каюма Мухамедханова' }, '', 'мухамедханова'));
    assert.ok(matchesRow({ city: 'Талдыкорган', address: 'Ёлочная' }, '', 'елочная'));
});

test('города — без повторов и по алфавиту', () => {
    assert.deepEqual(citiesOf([{ city: 'Шымкент' }, { city: 'Алматы' }, { city: 'Алматы' }, { city: '' }]),
        ['Алматы', 'Шымкент']);
});

test('«Обновлено» — самая свежая правка', () => {
    assert.equal(latestUpdate([]), null);
    assert.deepEqual(latestUpdate([
        { updated_at: '2026-09-30T18:00:00', updated_by_name: 'А' },
        { updated_at: '2026-10-01T09:15:00', updated_by_name: 'Б' },
        { updated_at: null },
    ]), { at: '2026-10-01T09:15:00', by: 'Б' });
});

test('время — часы Алматы как есть, без сдвига на пояс браузера', () => {
    assert.equal(fmtStamp('2026-10-01T14:20:05', NOW), 'сегодня в 14:20');
    assert.equal(fmtStamp('2026-09-30T23:50:00', NOW), 'вчера в 23:50');
    assert.equal(fmtStamp('2026-09-28T09:05:00.123456', NOW), '28 сент. в 09:05');
    assert.equal(fmtStamp('2025-12-03T10:00:00', NOW), '3 дек. 2025 в 10:00');
    assert.equal(fmtStamp(null, NOW), '');
});

test('подписи как в листе', () => {
    assert.equal(ordersLabel(15), '15+ заказов');
    assert.equal(ordersLabel(0), 'Без нормы');
    assert.equal(periodLabel(7), 'Неделя (7 дней)');
    assert.equal(periodShort(7), 'за неделю');
    assert.equal(periodShort(14), 'за 14 дней');
    assert.equal(depositShort(5000), '5 000 ₸');
    assert.equal(depositShort(0), 'Нет');
});

test('имя файла — по дате Алматы', () => {
    // 23:30 UTC 30.09 — в Алматы уже 1 октября.
    assert.equal(exportFileName(new Date(Date.UTC(2026, 8, 30, 23, 30))), 'Термокороба 01.10.2026.xlsx');
});

test('наличие — три состояния из самих чисел', () => {
    assert.equal(availabilityOf({ free_boxes: 28 }).key, 'free');
    assert.equal(availabilityOf({ free_boxes: 28 }).counted, '28 коробов');
    assert.equal(availabilityOf({ free_boxes: 31 }).counted, '31 короб');
    assert.equal(availabilityOf({ free_boxes: 0, used_boxes: 1 }).key, 'partial');
    assert.equal(availabilityOf({ free_boxes: 0, thermo_bags: 6 }).key, 'partial');
    assert.equal(availabilityOf({ free_boxes: 0, thermo_bags: 0, used_boxes: 0 }).key, 'none');
    assert.equal(availabilityOf({}).key, 'none');
});

test('значок правила — свой, а без него угадан по тексту', () => {
    assert.equal(memoKindOf({ kind: 'check', text: 'нельзя' }), 'check');
    assert.equal(memoKindOf({ kind: 'череп', text: 'Депозит списывается' }), 'money');
    assert.equal(guessMemoKind('В офисах СНЯТЬ деньги с баланса НЕЛЬЗЯ.'), 'forbidden');
    assert.equal(guessMemoKind('Ошибка ведёт к КРИТИЧЕСКИМ последствиям'), 'warning');
    assert.equal(guessMemoKind('Не поняли данные? НЕ ГАДАЙТЕ.'), 'question');
    assert.equal(guessMemoKind('Информация должна соответствовать таблице'), 'data');
    assert.ok(MEMO_KINDS.includes(guessMemoKind('что угодно')));
});

test('пункт памятки: первая строка — суть, остальное — пояснение', () => {
    assert.deepEqual(splitMemo('Депозит списывается.\nПеред визитом — деньги на балансе.'),
        { head: 'Депозит списывается.', rest: 'Перед визитом — деньги на балансе.' });
    assert.deepEqual(splitMemo('Одна строка'), { head: 'Одна строка', rest: '' });
});

test('источник цифр — когда лист сверили и что делать, если не читается', () => {
    assert.equal(sourceState(null), null);
    const fresh = sourceState({ status: 'ok', read_at: '2026-10-01T14:55:00', changed_at: '2026-09-30T09:10:00' }, NOW);
    assert.deepEqual(fresh, {
        label: 'Сверено с Google-таблицей сегодня в 14:55',
        hint: 'Цифры последний раз менялись вчера в 09:10',
        warning: '',
    });
    const broken = sourceState({ status: 'error', read_at: '2026-10-01T13:00:00', changed_at: null }, NOW);
    assert.equal(broken.label, 'Сверено с Google-таблицей сегодня в 13:00');
    assert.equal(broken.warning, 'Google-таблица сейчас не читается — показаны цифры последней сверки');
    const never = sourceState({ status: 'error', read_at: null, changed_at: null }, NOW);
    assert.equal(never.label, 'Google-таблица ещё не прочитана');
    assert.equal(never.warning, 'Google-таблица пока не читается — цифры могут быть устаревшими');
    assert.equal(sourceState({ status: null }, NOW).warning, '');
});

// ── Правка в Google-таблице на открытом экране (24.11.2026) ────────────────

test('что поменялось между двумя ответами экрана', async () => {
    const { rowChanges, TRACKED_FIELDS } = await import('../src/components/thermoboxes/thermoboxMeta.js');
    const before = [
        { ...ROW, id: 1, free_boxes: 28 },
        { ...ROW, id: 2, city: 'Астана', special_condition: 'Не новички' },
        { ...ROW, id: 3, city: 'Атырау' },
    ];
    const after = [
        { ...ROW, id: 1, free_boxes: 18 },
        { ...ROW, id: 2, city: 'Астана', special_condition: 'НЕ новички', deposit_tenge: 0 },
        { ...ROW, id: 4, city: 'Актобе' },
    ];
    assert.deepEqual(rowChanges(before, after), {
        changed: { 1: { free_boxes: 28 }, 2: { deposit_tenge: 5000, special_condition: 'Не новички' } },
        added: [4],
        removed: [3],
    });
    // Без правок — пусто; убранная строка (is_active=false) на экране не считается.
    assert.deepEqual(rowChanges(after, after.map((row) => ({ ...row }))), { changed: {}, added: [], removed: [] });
    assert.deepEqual(rowChanges([{ ...ROW, is_active: false }], [{ ...ROW, is_active: false }]),
        { changed: {}, added: [], removed: [] });
    assert.ok(TRACKED_FIELDS.includes('free_boxes') && TRACKED_FIELDS.includes('special_condition'));
});

test('тост называет обновлённые офисы, а без правок молчит', async () => {
    const { changesToast, memoChanged } = await import('../src/components/thermoboxes/thermoboxMeta.js');
    const rows = [{ id: 1, city: 'Алматы' }, { id: 2, city: 'Алматы' }, { id: 4, city: 'Актобе' }];
    assert.equal(changesToast({ changed: { 1: { free_boxes: 28 }, 2: { used_boxes: 0 } }, added: [4], removed: [] }, rows),
        'Обновлено из Google-таблицы: Алматы, Актобе');
    assert.equal(changesToast({ changed: {}, added: [], removed: [3, 5] }, rows), 'Обновлено из Google-таблицы: 2 офиса убраны');
    assert.equal(changesToast({ changed: {}, added: [], removed: [] }, rows, true), 'Обновлено из Google-таблицы: памятка');
    assert.equal(changesToast({ changed: {}, added: [], removed: [] }, rows), '');
    const many = [1, 2, 3, 4, 5].map((id) => ({ id, city: `Город ${id}` }));
    assert.equal(changesToast({ changed: Object.fromEntries(many.map((row) => [row.id, { free_boxes: 0 }])), added: [], removed: [] }, many),
        'Обновлено из Google-таблицы: Город 1, Город 2, Город 3 и ещё 2');
    const memo = { title: 'Памятка', items: [{ text: 'Пункт', owner: 'kc', kind: 'money' }] };
    assert.equal(memoChanged(memo, { ...memo, updated_at: '2026-11-24T12:00:00', updated_by_name: 'Google-таблица' }), false);
    assert.equal(memoChanged(memo, { ...memo, items: [{ ...memo.items[0], text: 'Пункт 2' }] }), true);
});

test('подсказка у подсвеченной ячейки — было и стало', async () => {
    const { freshHint } = await import('../src/components/thermoboxes/thermoboxMeta.js');
    assert.equal(freshHint('free_boxes', 28, 18), 'Было 28 → стало 18 — правка в Google-таблице');
    assert.equal(freshHint('used_boxes', null, 1), 'Было 0 → стало 1 — правка в Google-таблице');
    assert.equal(freshHint('deposit_tenge', 5000, 0), 'Было 5 000 ₸ → стало без депозита — правка в Google-таблице');
    assert.equal(freshHint('min_orders', 20, 15), 'Было 20+ заказов → стало 15+ заказов — правка в Google-таблице');
    assert.equal(freshHint('special_condition', null, 'НЕ новички'), 'Было — → стало НЕ новички — правка в Google-таблице');
});

test('опоздавший ответ не ложится поверх нового: сначала цифры, потом метка «Сверено …»', () => {
    const at = (changed, checked) => ({ changed_at: changed, checked_at: checked });
    const now = at('2026-11-24T17:51:00', '2026-11-24T17:53:00');
    assert.equal(sourceNotOlder(now, at('2026-11-24T17:51:00', '2026-11-24T17:54:00')), true, 'то же, свежее сверено');
    assert.equal(sourceNotOlder(now, now), true, 'тот же ответ');
    assert.equal(sourceNotOlder(now, at('2026-11-24T17:51:00', '2026-11-24T17:52:00')), false, 'метка не уходит назад');
    assert.equal(sourceNotOlder(now, at('2026-11-24T17:50:00', '2026-11-24T17:59:00')), false, 'старые цифры — нет');
    assert.equal(sourceNotOlder(now, at('2026-11-24T17:55:00', '2026-11-24T17:55:00')), true, 'новые цифры');
    assert.equal(sourceNotOlder(at(null, '2026-11-24T17:53:00'), at('2026-11-24T17:55:00', '2026-11-24T17:55:00')), true,
        'первые цифры после «ещё не читали»');
    assert.equal(sourceNotOlder(null, now), true, 'не зеркало — не с чем сравнивать');
    assert.equal(sourceNotOlder(now, null), true);
});
