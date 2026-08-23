import assert from 'node:assert/strict';
import test from 'node:test';

import { browse, currentStep, expectedTap, restart, startRun, tap }
    from '../src/components/wiki/trainers/runner.js';
import shift from '../src/components/wiki/trainers/scenarioOperatorCall.js';
import {
    callNext, canRing, emptyCall, isLive, syncReady, talkClock, talkMs,
} from '../src/components/wiki/trainers/callMachine.js';
import { EVENT_CODES, createEventLog } from '../src/components/wiki/trainers/trainerEvents.js';
import {
    applyEdit, findContractors, formatMoney, parseMoney, prepareCase, shiftDays,
} from '../src/components/wiki/trainers/caseData.js';
import { DEFAULT_CASE } from '../src/components/wiki/trainers/fleetData.js';

/* Режим смены: стажёр встаёт на линию и принимает звонок.
 *
 * Всё, что здесь проверяется, — правила, каждое из которых учебное. Автомат
 * звонка чистый и без таймеров, поэтому его можно прогнать целиком, не открывая
 * браузер: браузер потом проверит, что это ещё и нарисовано.
 */

const ONLINE = { oktLogged: true, oktIn: true, oktStatus: null };

test('режим смены — отдельный сценарий и отдельный ключ', () => {
    assert.equal(shift.key, 'operator-call');
    assert.notEqual(shift.key, 'crm-ticket-create',
        'ключ свободной среды уехал в статьи и переделке не подлежит');
    assert.equal(shift.mode, 'call');
    assert.equal(shift.stage, 'desktop');
});

/* Вступление — одна фраза про порядок работы. Предыстории водителя в ней быть
   не должно: всё, что рассказано заранее, стажёр уже не станет искать. */
test('вступление не рассказывает про водителя', () => {
    const intro = shift.steps[0].msg + shift.steps[1].msg;
    for (const leak of ['комисси', 'удержал', 'termokorob', 'термокороб', 'жалоб']) {
        assert.ok(!intro.toLowerCase().includes(leak),
            `во вступлении проговорена суть дела: «${leak}»`);
    }
});

/* ── Автомат звонка ──────────────────────────────────────────────────────── */

test('без входа в call-центр звонков не бывает', () => {
    assert.equal(canRing({ oktLogged: false, oktIn: false }), false);
    assert.equal(canRing({ oktLogged: true, oktIn: false }), false, 'вход в клиент ≠ линия');
    assert.equal(canRing({ ...ONLINE, oktStatus: 'Перерыв' }), false, 'на перерыве звонков нет');
    assert.equal(canRing(ONLINE), true);

    // Пока не на линии — «позвонить» не срабатывает вовсе.
    const idle = callNext(emptyCall(), 'ring', { phone: '7701' });
    assert.equal(idle.call.state, 'offline');
    assert.deepEqual(idle.events, []);
});

test('перерыв во время вызова гасит линию, а разговор — не обрывает', () => {
    const ringing = { ...emptyCall(), state: 'ringing' };
    assert.equal(syncReady(ringing, { ...ONLINE, oktStatus: 'Перерыв' }).state, 'ended');

    const talking = { ...emptyCall(), state: 'talking', answeredAt: 1000 };
    assert.equal(syncReady(talking, { ...ONLINE, oktStatus: 'Перерыв' }).state, 'talking',
        'перерыв не должен обрывать идущий разговор');
});

test('полный путь звонка проходится и даёт события', () => {
    let call = syncReady(emptyCall(), ONLINE);
    assert.equal(call.state, 'ready');

    const codes = [];
    const step = (action, payload, now) => {
        const r = callNext(call, action, payload, now);
        call = r.call;
        r.events.forEach(([code]) => codes.push(code));
    };

    step('ring', { phone: '+7 701 555 01 42', queue: 'Линия водителей', waitedSec: 34 }, 1000);
    assert.equal(call.state, 'ringing');
    step('answer', {}, 4000);
    assert.equal(call.state, 'talking');
    assert.equal(isLive(call), true);
    step('hold', {}, 10000);
    step('unhold', {}, 16000);
    assert.equal(call.holdMs, 6000, 'время удержания не посчитано');
    step('transfer', { to: 'Оспанов Тимур · СВ (1101)' }, 34000);

    assert.equal(call.state, 'wrapup', 'после перевода остаётся постобработка');
    assert.equal(call.transferredTo, 'Оспанов Тимур · СВ (1101)');
    assert.deepEqual(codes, [
        'call.incoming', 'call.answer', 'call.hold', 'call.unhold', 'call.transfer', 'call.end',
    ]);

    step('finish', {}, 40000);
    assert.equal(call.state, 'ended');
});

/* Таймер идёт от ОТВЕТА: время ожидания в очереди — не разговор, и складывать
   их значит врать в отчёте. */
test('таймер считает разговор, а не ожидание', () => {
    let call = syncReady(emptyCall(), ONLINE);
    call = callNext(call, 'ring', {}, 0).call;
    call = callNext(call, 'answer', {}, 20000).call;      // 20 секунд ждал
    const ended = callNext(call, 'end', { by: 'driver' }, 50000);
    assert.equal(talkMs(ended.call), 30000);
    assert.equal(talkClock(talkMs(ended.call)), '00:30');
    assert.equal(ended.events[0][1].by, 'driver');
});

test('отклонённый вызов не идёт в постобработку', () => {
    let call = syncReady(emptyCall(), ONLINE);
    call = callNext(call, 'ring', {}, 0).call;
    const rejected = callNext(call, 'reject', {}, 2000);
    assert.equal(rejected.call.state, 'ended', 'оформлять после отказа нечего');
    assert.equal(rejected.events[0][0], 'call.reject');
});

test('недопустимый переход ничего не ломает', () => {
    const fresh = emptyCall();
    for (const action of ['answer', 'hold', 'unhold', 'transfer', 'end', 'finish', 'нет-такого']) {
        const r = callNext(fresh, action, {}, 0);
        assert.equal(r.call.state, 'offline', `«${action}» сдвинул автомат из offline`);
        assert.deepEqual(r.events, []);
    }
});

/* ── Итог смены ──────────────────────────────────────────────────────────── */

test('итог смены содержит и карточку, и звонок', () => {
    let run = startRun(shift, { now: new Date(Date.UTC(2026, 7, 23, 6)) });
    run = tap(run, expectedTap(run)).run;
    assert.equal(currentStep(run).key, 'shift');

    let call = syncReady(emptyCall(), ONLINE);
    call = callNext(call, 'ring', { phone: '+7 701 555 01 42' }, 0).call;
    call = callNext(call, 'answer', {}, 1000).call;
    call = callNext(call, 'end', { by: 'operator' }, 61000).call;

    run = browse(run, {
        call,
        saved: true,
        form: { ...run.world.form, source: 'Звонок', phone: '77015550142', cats: ['Водитель'] },
    });

    const result = shift.result(run.world);
    assert.equal(result.call.answered, true);
    assert.equal(result.call.duration_ms, 60000);
    assert.equal(result.call.saved, true);
    const fields = Object.fromEntries(result.fields);
    assert.equal(fields['Звонок/Чат'], 'Звонок');
    assert.equal(result.correct, undefined, 'среда не выносит вердикт');
});

/* ── Лента событий ───────────────────────────────────────────────────────── */

test('лента пишет только известные коды и схлопывает повторы', () => {
    let clock = 0;
    const log = createEventLog({ now: () => clock });
    log.emit('okt.login');
    clock = 100;
    log.emit('ui.open_tab', { tab: 'transactions' });
    clock = 300;
    log.emit('ui.open_tab', { tab: 'transactions' });        // повтор
    clock = 2000;
    log.emit('ui.open_tab', { tab: 'transactions' });        // уже не повтор
    log.emit('ui.выдуманный');                               // неизвестный код

    assert.equal(log.count(), 3);
    assert.equal(log.droppedCount(), 1);
    assert.deepEqual(log.all().map((i) => i.code),
        ['okt.login', 'ui.open_tab', 'ui.open_tab']);
});

test('лента работает без сервера и отдаёт пачку, когда он есть', async () => {
    const offline = createEventLog();
    offline.emit('call.answer', { after_ms: 1200 });
    assert.equal(offline.count(), 1, 'без отправки лента обязана копиться в памяти');
    assert.equal(await offline.flush(), false);

    const batches = [];
    const online = createEventLog({ send: (items) => batches.push(items.length) });
    online.emit('crm.save', {});
    assert.equal(await online.flush(), true);
    assert.deepEqual(batches, [1]);
});

test('коды событий — общий словарь с разбором', () => {
    for (const code of ['okt.login', 'okt.callcenter_in', 'okt.status', 'call.incoming',
        'call.answer', 'call.transfer', 'call.end', 'ui.tab', 'ui.search',
        'ui.open_contractor', 'ui.open_tab', 'ui.action', 'crm.save']) {
        assert.ok(EVENT_CODES.includes(code), `код «${code}» пропал из словаря`);
    }
    assert.equal(new Set(EVENT_CODES).size, EVENT_CODES.length, 'в словаре есть дубли');
});

/* ── Слепок ──────────────────────────────────────────────────────────────── */

test('даты слепка едут за сегодняшним днём', () => {
    assert.equal(shiftDays('2026-08-23', { year: 2026, month: 8, day: 23 }), 0);
    assert.equal(shiftDays('2026-08-23', { year: 2026, month: 8, day: 30 }), 7);

    const same = prepareCase(DEFAULT_CASE, { year: 2026, month: 8, day: 23 });
    assert.equal(same.transactions[0].when, '18 авг., 06:17');

    // Через сто дней та же запись обязана сдвинуться, а не остаться в августе.
    const later = prepareCase(DEFAULT_CASE, { year: 2026, month: 12, day: 1 });
    assert.notEqual(later.transactions[0].when, same.transactions[0].when);
    assert.equal(later.shiftedBy, 100);

    // Порядок событий при сдвиге сохраняется.
    const gap = (c) => c.transactions.length;
    assert.equal(gap(later), gap(same));
});

test('пустой слепок не роняет подготовку', () => {
    const empty = prepareCase(null, { year: 2026, month: 8, day: 23 });
    assert.deepEqual(empty.transactions, []);
    assert.deepEqual(empty.contractors, []);
    assert.ok(empty.crm);
});

test('поиск срабатывает с трёх знаков', () => {
    const people = DEFAULT_CASE.contractors;
    assert.equal(findContractors(people, 'ну').ready, false, 'два знака — ещё не поиск');
    assert.equal(findContractors(people, 'ну').items.length, people.length,
        'до порога список показывается целиком');
    assert.equal(findContractors(people, 'AN000000').items.length, 1, 'поиск по номеру ВУ');
    assert.equal(findContractors(people, 'Асхат').items.length >= 1, true, 'поиск по позывному');
    assert.equal(findContractors(people, 'ЖЖЖ').items.length, 0);
});

/* ── Правки над копией слепка ────────────────────────────────────────────── */

test('деньги разбираются и печатаются как в кабинете', () => {
    assert.equal(parseMoney('−618,95 ₸'), -618.95);
    assert.equal(parseMoney('17 524 ₸'), 17524);
    assert.equal(parseMoney('ерунда'), 0);
    assert.equal(formatMoney(-618.95), '−618,95 ₸');
    assert.equal(formatMoney(18524), '18 524 ₸');
});

test('начисление правит копию слепка и не трогает исходник', () => {
    const id = DEFAULT_CASE.contractor.id;
    const before = DEFAULT_CASE.contractor.balance;

    const after = applyEdit(DEFAULT_CASE, 'balance_add', { id });
    assert.equal(after.contractor.balance, '381,05 ₸');
    assert.equal(after.contractors[0].balance, '381,05 ₸', 'в списке баланс не обновился');
    assert.equal(DEFAULT_CASE.contractor.balance, before, 'исходный слепок мутировали');

    const back = applyEdit(after, 'balance_sub', { id });
    assert.equal(back.contractor.balance, before);
});

test('смена парка и неизвестное действие', () => {
    assert.equal(applyEdit(DEFAULT_CASE, 'switch_park', { to: 'iTaxi' }).park.name, 'iTaxi');
    // «Открыть в WhatsApp» данные не меняет, но падать не должно.
    assert.equal(applyEdit(DEFAULT_CASE, 'whatsapp', {}), DEFAULT_CASE);
});

/* «Заново» обязано начинать ТО ЖЕ дело. Пока слепок терялся, перезапуск молча
   подменял водителя запасным — в браузере это выглядело как «panel_only не
   работает», и тесты этого не видели, потому что зовут startRun напрямую. */
test('перезапуск сохраняет слепок дела', () => {
    const caseData = {
        ...DEFAULT_CASE,
        contractor: { ...DEFAULT_CASE.contractor, panel_only: true, last: 'Иванов' },
    };
    const run = startRun(shift, { now: new Date(Date.UTC(2026, 7, 23, 6)), caseData });
    assert.equal(run.world.case.contractor.panel_only, true);

    const again = restart(run, { caseData });
    assert.equal(again.world.case.contractor.panel_only, true, 'после «Заново» дело подменилось');
    assert.equal(again.world.case.contractor.last, 'Иванов');

    // Без слепка перезапуск честно берёт запасной — это тоже рабочий случай.
    assert.equal(restart(run).world.case.contractor.panel_only, false);
});
