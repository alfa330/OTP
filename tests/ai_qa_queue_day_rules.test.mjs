import test from 'node:test';
import assert from 'node:assert/strict';

import {
    plural, dayTitle, monthGroups, dayNeighbours, nextDayToReview, timeOf, itemKey, commonReasons, commonRowMeta,
    rowReasons, applyReviewed, queueSummary, dayPageRequest, mergeDayPage, DAY_PAGE, DAY_PAGE_OVERLAP, NO_DAY,
    STALE_MARK,
} from '../src/components/call_qa/queueDayRules.js';

/**
 * Очередь ревью по дням: подписи дня и месяца, плитки по месяцам, соседние дни,
 * какие причины показывать у строки, догрузка строк дня и как сводка дня меняется,
 * когда разговор только что проверили.
 */

const NOW = new Date(2026, 8, 30, 12, 0);   // среда, 30 сентября 2026

test('склонения', () => {
    assert.equal(plural(1, 'день', 'дня', 'дней'), 'день');
    assert.equal(plural(3, 'день', 'дня', 'дней'), 'дня');
    assert.equal(plural(5, 'день', 'дня', 'дней'), 'дней');
    assert.equal(plural(11, 'день', 'дня', 'дней'), 'дней');
    assert.equal(plural(21, 'день', 'дня', 'дней'), 'день');
    assert.equal(plural(112, 'день', 'дня', 'дней'), 'дней');
});

test('подпись дня: дата, день недели, «сегодня/вчера», год — только чужой', () => {
    assert.deepEqual(dayTitle('2026-09-30', NOW),
        { title: 'Среда, 30 сентября', date: '30 сентября', weekday: 'Среда', relative: 'Сегодня' });
    assert.equal(dayTitle('2026-09-29', NOW).relative, 'Вчера');
    assert.deepEqual(dayTitle('2026-09-23', NOW),
        { title: 'Среда, 23 сентября', date: '23 сентября', weekday: 'Среда', relative: null });
    assert.equal(dayTitle('2025-12-31', NOW).title, 'Среда, 31 декабря 2025');
    // Первое число месяца: «вчера» считается через границу месяца.
    assert.equal(dayTitle('2026-09-30', new Date(2026, 9, 1)).relative, 'Вчера');
    assert.deepEqual(dayTitle(NO_DAY, NOW), { title: 'Без даты', date: 'Без даты', weekday: null, relative: null });
});

test('плитки дней — по месяцам, день без даты — в конце, итоги месяца — по ждущим', () => {
    const groups = monthGroups([
        { day: '2026-09-21', open: 2, critical: 1 },
        { day: '2026-09-17', open: 0, critical: 1 },      // проверен целиком: в итог не идёт
        { day: NO_DAY, open: 3, critical: 0 },
        { day: '2026-08-23', open: 1, critical: 0 },
        { day: '2025-12-31', open: 4, critical: 2 },
    ], NOW);
    assert.deepEqual(groups.map((g) => [g.title, g.days.length, g.open, g.critical]), [
        ['Сентябрь', 2, 2, 1],
        ['Август', 1, 1, 0],
        ['Декабрь 2025', 1, 4, 2],
        ['Без даты', 1, 3, 0],
    ]);
    assert.deepEqual(monthGroups([], NOW), []);
});

test('соседние дни для стрелок и следующий день с работой', () => {
    const days = [
        { day: '2026-09-30', open: 3 },
        { day: '2026-09-21', open: 0 },
        { day: '2026-09-17', open: 1 },
        { day: '2026-09-15', open: 0 },
    ];
    // Лента идёт от свежих к старым: «раньше» — следующий в списке.
    assert.deepEqual(dayNeighbours(days, '2026-09-21'), { earlier: '2026-09-17', later: '2026-09-30' });
    assert.deepEqual(dayNeighbours(days, '2026-09-30'), { earlier: '2026-09-21', later: null });
    assert.deepEqual(dayNeighbours(days, 'нет такого'), { earlier: null, later: null });
    // Проверили день — дальше по ленте, пропуская закрытые; в конце — назад к свежим.
    assert.equal(nextDayToReview(days, '2026-09-30'), '2026-09-17');
    assert.equal(nextDayToReview(days, '2026-09-17'), '2026-09-30');
    assert.equal(nextDayToReview([{ day: '2026-09-30', open: 0 }], '2026-09-30'), null);
});

test('время строки берётся из подписи; у заявки Chat2Desk его нет', () => {
    assert.equal(timeOf('23.09 12:04'), '12:04');
    assert.equal(timeOf('23.09'), null);
    assert.equal(timeOf(null), null);
    assert.equal(itemKey({ subject: 'imported_call', id: 5 }), 'imported_call-5');
    assert.equal(itemKey({ id: 5 }), 'call-5');
});

test('причина, которая есть у всех ждущих дня, названа один раз, а у строк не повторяется', () => {
    const day = { day: '2026-09-23', open: 5, reasons: { critical: 1, lowconf: 4, pending: 5 } };
    // «Спорное» — у четырёх из пяти: это различие, и у строк оно остаётся.
    assert.deepEqual(commonReasons([day]), ['pending']);
    assert.deepEqual(rowReasons(['critical', 'lowconf', 'pending'], commonReasons([day])), ['critical', 'lowconf']);
    assert.deepEqual(rowReasons(['pending'], commonReasons([day])), []);
    // «Критическое» общим не бывает: это то, что открывают первым.
    assert.deepEqual(commonReasons([{ day: 'd', open: 2, reasons: { critical: 2, lowconf: 2 } }]), ['lowconf']);
    // Один разговор — не «у всех»; проверенный целиком день — тоже.
    assert.deepEqual(commonReasons([{ day: 'd', open: 1, reasons: { lowconf: 1 } }]), []);
    assert.deepEqual(commonReasons([{ day: 'd', open: 0, reasons: {} }]), []);
    assert.deepEqual(commonReasons([]), []);
    // «Без флагов» и «Новое» — не причины: их нет ни у строки, ни в общей строке.
    assert.deepEqual(rowReasons(['ok'], []), []);
    assert.deepEqual(rowReasons(['new'], []), []);
    assert.deepEqual(commonReasons([{ day: 'd', open: 2, reasons: { ok: 2 } }]), []);
});

test('«устарела» у всех — по загруженным строкам, одна свежая возвращает метку остальным', () => {
    const days = [{ day: '2026-09-23', open: 3, reasons: { lowconf: 3 } }];
    const stale = [{ id: 1, stale: true }, { id: 2, stale: true }];
    assert.deepEqual(commonReasons(days, stale), ['lowconf', STALE_MARK]);
    assert.deepEqual(commonReasons(days, [...stale, { id: 3, stale: false }]), ['lowconf']);
    // «Не удалось определить» (null) — не «устарела».
    assert.deepEqual(commonReasons(days, [...stale, { id: 3, stale: null }]), ['lowconf']);
    // Одна строка — не «у всех».
    assert.deepEqual(commonReasons(days, [{ id: 1, stale: true }]), ['lowconf']);
});

test('одинаковые у всех строк источник и направление в строке не повторяются', () => {
    const szov = [{ subject: 'imported_call', direction: 'Основа' }, { subject: 'imported_call', direction: 'Основа' }];
    assert.deepEqual(commonRowMeta(szov), { subject: true, direction: true });
    // Звонки журнала вперемешку с АТС: «из АТС» различает независимые id.
    assert.deepEqual(commonRowMeta([...szov, { subject: 'call', direction: 'Основа' }]),
        { subject: false, direction: true });
    assert.deepEqual(commonRowMeta([...szov, { subject: 'imported_call', direction: 'Поток' }]),
        { subject: true, direction: false });
    // Одна строка — не «у всех»: у неё всё остаётся на месте.
    assert.deepEqual(commonRowMeta([szov[0]]), { subject: false, direction: false });
    assert.deepEqual(commonRowMeta(null), { subject: false, direction: false });
});

test('«Показать ещё» берёт с перекрытием и не теряет разговор, если день сдвинулся', () => {
    assert.deepEqual(dayPageRequest(0), { offset: 0, limit: DAY_PAGE });
    assert.deepEqual(dayPageRequest(5), { offset: 0, limit: DAY_PAGE + 5 });
    assert.deepEqual(dayPageRequest(60), { offset: 60 - DAY_PAGE_OVERLAP, limit: DAY_PAGE + DAY_PAGE_OVERLAP });
    // Сервер отдаёт не больше 200 строк за раз.
    assert.ok(dayPageRequest(1000).limit <= 200);

    // Загружены строки 0–4 из 10. Пока человек работал, другой проверяющий закрыл
    // строку 2, а «Переоценить» унесло строку 0 в конец дня.
    const row = (id) => ({ subject: 'call', id });
    const loaded = [0, 1, 2, 3, 4].map(row);
    const server = [1, 3, 4, 5, 6, 7, 8, 9, 0].map(row);
    const { offset, limit } = dayPageRequest(loaded.length);
    const merged = mergeDayPage(loaded, server.slice(offset, offset + limit), offset, server.length);
    // Строки 5 и 6 на месте: запрос ровно со смещения 5 начал бы уже с 7.
    assert.deepEqual(merged.items.map((item) => item.id), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    assert.equal(merged.total - merged.end, 0);
    // Большой день: страница за страницей — весь день, повторы перекрытия отсеяны.
    const big = Array.from({ length: 130 }, (_, i) => row(i));
    let state = mergeDayPage([], big.slice(0, DAY_PAGE), 0, big.length);
    while (state.total - state.end > 0) {
        const req = dayPageRequest(state.items.length);
        state = mergeDayPage(state.items, big.slice(req.offset, req.offset + req.limit), req.offset, big.length);
    }
    assert.deepEqual(state.items.map((item) => item.id), big.map((item) => item.id));
});

test('проверенный разговор меняет сводку своего дня на месте', () => {
    const days = [
        { day: '2026-09-23', open: 2, critical: 1, reasons: { critical: 1, lowconf: 2 }, evaluated: 4, reviewed: 2, corrected: 0 },
        { day: '2026-09-22', open: 1, critical: 0, reasons: { lowconf: 1 }, evaluated: 1, reviewed: 0, corrected: 0 },
    ];
    const next = applyReviewed(days, [
        { key: 'call-1', day: '2026-09-23', reasons: ['critical', 'lowconf'], corrected: true },
        { key: 'call-9', day: null, reasons: ['lowconf'] },      // открыт не из очереди — день неизвестен
    ]);
    assert.deepEqual(next[0], { day: '2026-09-23', open: 1, critical: 0, reasons: { lowconf: 1 },
                                evaluated: 4, reviewed: 3, corrected: 1, human_n: 0, human_avg: null });
    assert.equal(next[1], days[1]);
    // Проверили всех — день остаётся, но ждущих нет.
    const done = applyReviewed(days, [{ key: 'call-2', day: '2026-09-22', reasons: ['lowconf'] }]);
    assert.equal(done[1].open, 0);
    assert.equal(done[1].reviewed, 1);
    assert.deepEqual(queueSummary(done), { open: 2, critical: 1, days: 1 });
});

test('новый балл человека входит в средний дня', () => {
    const day = { day: '2026-09-23', open: 3, critical: 0, reasons: { lowconf: 3 }, evaluated: 3, reviewed: 0,
                  corrected: 0, human_avg: 80, human_n: 2 };
    const [next] = applyReviewed([day], [
        { key: 'call-1', day: '2026-09-23', reasons: ['lowconf'], human: 50 },
        { key: 'call-2', day: '2026-09-23', reasons: ['lowconf'], human: null },   // балл у разговора уже был
    ]);
    assert.equal(next.open, 1);
    assert.equal(next.human_n, 3);
    assert.equal(next.human_avg, 70);
    // Первая оценка дня — средний из неё одной.
    const [first] = applyReviewed([{ ...day, human_avg: null, human_n: 0 }],
        [{ key: 'call-1', day: '2026-09-23', reasons: [], human: 64 }]);
    assert.equal(first.human_avg, 64);
    assert.equal(first.human_n, 1);
});
