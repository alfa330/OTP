import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import { browse, currentStep, expectedTap, restart, startRun, tap }
    from '../src/components/wiki/trainers/runner.js';
import shift from '../src/components/wiki/trainers/scenarioOperatorCall.js';
import {
    callNext, canRing, emptyCall, isLive, syncReady, talkClock, talkMs,
} from '../src/components/wiki/trainers/callMachine.js';
import { EVENT_CODES, createEventLog } from '../src/components/wiki/trainers/trainerEvents.js';
import { parseMoney, prepareCase, shiftDays } from '../src/components/wiki/trainers/caseData.js';
import { DEFAULT_CASE } from '../src/components/wiki/trainers/crmPeople.js';
import { createDeskWorld } from '../src/components/wiki/trainers/deskWorld.js';
import { findCrmAccounts } from '../src/components/wiki/trainers/crmModel.js';
import { orderCard } from '../src/components/wiki/trainers/crmCard.js';

/* Режим смены: стажёр на линии и принимает звонок; рабочее место — одна CRM.
 *
 * Всё, что здесь проверяется, — правила, каждое из которых учебное. Автомат
 * звонка чистый и без таймеров, поэтому его можно прогнать целиком, не открывая
 * браузер: браузер потом проверит, что это ещё и нарисовано.
 */

const ONLINE = { onLine: true };

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

test('звонки приходят только тому, кто на линии', () => {
    assert.equal(canRing({ onLine: false }), false);
    assert.equal(canRing({}), false, 'без линии звонков нет — в курсе и аттестации их и не бывает');
    assert.equal(canRing(ONLINE), true);

    // Пока не на линии — «позвонить» не срабатывает вовсе.
    const idle = callNext(emptyCall(), 'ring', { phone: '7701' });
    assert.equal(idle.call.state, 'offline');
    assert.deepEqual(idle.events, []);
});

test('уход с линии во время вызова гасит звонок, а разговор — не обрывает', () => {
    const ringing = { ...emptyCall(), state: 'ringing' };
    assert.equal(syncReady(ringing, { onLine: false }).state, 'ended');

    const talking = { ...emptyCall(), state: 'talking', answeredAt: 1000 };
    assert.equal(syncReady(talking, { onLine: false }).state, 'talking',
        'уход с линии не должен обрывать идущий разговор');
});

test('смена начинается на линии: клиента телефонии в рабочем месте нет', () => {
    const run = startRun(shift, { now: new Date(Date.UTC(2026, 7, 23, 6)) });
    assert.equal(run.world.onLine, true);
    assert.equal(run.world.tab, 'crm');
    assert.equal(syncReady(emptyCall(), run.world).state, 'ready', 'звонок не придёт');
    for (const gone of ['fleetView', 'oktLogged', 'oktIn']) {
        assert.equal(run.world[gone], undefined, `в мире осталось поле ${gone}`);
    }
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
    log.emit('call.incoming');
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
        ['call.incoming', 'ui.open_tab', 'ui.open_tab']);
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
    for (const code of ['call.incoming', 'call.answer', 'call.transfer', 'call.end', 'ui.tab',
        'crm.save', 'crm.section', 'crm.driver_search', 'crm.driver_open', 'crm.driver_tab',
        'crm.ya_send', 'crm.ya_reply']) {
        assert.ok(EVENT_CODES.includes(code), `код «${code}» пропал из словаря`);
    }
    assert.equal(new Set(EVENT_CODES).size, EVENT_CODES.length, 'в словаре есть дубли');
});

/* ── Слепок ──────────────────────────────────────────────────────────────── */

test('даты слепка едут за сегодняшним днём', () => {
    assert.equal(shiftDays('2026-08-23', { year: 2026, month: 8, day: 23 }), 0);
    assert.equal(shiftDays('2026-08-23', { year: 2026, month: 8, day: 30 }), 7);

    const same = prepareCase(DEFAULT_CASE, { year: 2026, month: 8, day: 23 });
    assert.equal(same.transactions[0].when, '22.08.2026 06:17', 'подпись не как в CRM');
    assert.equal(same.transactions[0].day, '2026-08-22');

    // Через сто дней та же запись обязана сдвинуться, а не остаться в августе.
    const later = prepareCase(DEFAULT_CASE, { year: 2026, month: 12, day: 1 });
    assert.equal(later.transactions[0].when, '30.11.2026 06:17');
    assert.equal(later.shiftedBy, 100);
    assert.equal(later.orders[0].stages[2].when, '30.11.2026 06:08:52', 'этапы заказа — с секундами');
});

test('пустой слепок не роняет подготовку', () => {
    const empty = prepareCase(null, { year: 2026, month: 8, day: 23 });
    assert.deepEqual(empty.transactions, []);
    assert.deepEqual(empty.orders, []);
    assert.ok(empty.crm);
});

test('деньги кабинета читаются числом', () => {
    assert.equal(parseMoney('−618,95 ₸'), -618.95);
    assert.equal(parseMoney('17 524 ₸'), 17524);
    assert.equal(parseMoney('ерунда'), 0);
    assert.equal(parseMoney(-50), -50);
});

/* «Заново» обязано начинать ТО ЖЕ дело. Пока слепок терялся, перезапуск молча
   подменял водителя запасным — тесты этого не видели, потому что зовут
   startRun напрямую. */
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

    // Без слепка перезапуск честно берёт учебное дело — это тоже рабочий случай.
    assert.ok(!restart(run).world.case.contractor.panel_only);
});

/* ── Стык двух дорожек ───────────────────────────────────────────────────────
 *
 * Слепки голосового тренажёра пишет другая дорожка (voice_trainer/cases) — по
 * контракту в форме кабинета Диспетчерской. Рабочее место теперь одна CRM, и
 * эти дела обязаны открываться в ней: звонящий находится по номеру из звонка,
 * а у заказа с этапами есть карточка. Проверяем на НАСТОЯЩИХ файлах, а не на
 * своём DEFAULT_CASE: своя копия всегда подходит сама себе.
 */
const voiceCases = () => {
    const dir = new URL('../voice_trainer/cases/', import.meta.url);
    try {
        return fs.readdirSync(dir).filter((name) => name.endsWith('.json'))
            .map((name) => [name, JSON.parse(fs.readFileSync(new URL(name, dir), 'utf8'))]);
    } catch {
        return []; // Дел ещё нет — это не повод краснеть.
    }
};

test('слепки соседней дорожки читаются в CRM, и в них находится звонящий', () => {
    for (const [name, raw] of voiceCases()) {
        const pub = raw.public || raw;
        const world = createDeskWorld({ today: { year: 2026, month: 8, day: 23 }, caseData: pub });
        const c = world.case;
        assert.ok(c.transactions.every((t) => t.when && Number.isFinite(t.amount)), `${name}: ведомость не подготовлена`);

        // Первое настоящее действие смены — найти звонящего по номеру.
        const found = findCrmAccounts(world, c.call.phone);
        assert.equal(found.length, 1, `${name}: номер из звонка не даёт одну учётку`);
        assert.equal(found[0].accountId, c.contractor.id, `${name}: нашли не того человека`);

        // Ответ остаётся на сервере: в public его быть не должно.
        const text = JSON.stringify(pub);
        for (const banned of ['"answer"', '"expected"', '"checks"', '"solution"']) {
            assert.ok(!text.includes(banned), `${name}: в public утёк ${banned}`);
        }
    }
});

test('готовые дела соседней дорожки дают карточку заказа с этапами', () => {
    let withStages = 0;
    for (const [name, raw] of voiceCases()) {
        const world = createDeskWorld({ today: { year: 2026, month: 8, day: 23 }, caseData: raw.public });
        for (const order of world.case.orders.filter((o) => o.stages.length)) {
            withStages += 1;
            const card = orderCard(world.case, order.number);
            assert.ok(card, `${name}: у заказа ${order.number} нет карточки`);
            assert.ok(card.order.stages.every((stage) => stage.at), `${name}: этапы заказа без времени`);
        }
    }
    if (voiceCases().length) assert.ok(withStages > 0, 'ни в одном деле нет заказа с этапами');
});

/* ── Карточка заказа ─────────────────────────────────────────────────────── */

test('допуск к линии объясняет «Диагностика»', () => {
    assert.ok(DEFAULT_CASE.contractor.diagnostics, 'нет поля diagnostics');
    assert.ok(DEFAULT_CASE.contractor.priority, 'нет поля priority');
});

/* Ожидание клиента = «В пути» − «На месте». Отдельного поля нет — оно
   выводится из этапов карточки заказа, и стажёр обязан это уметь. */
test('по этапам карточки заказа считается ожидание клиента', () => {
    const world = createDeskWorld({ today: { year: 2026, month: 8, day: 23 } });
    const card = orderCard(world.case, '11837902');
    const stage = (name) => card.order.stages.find((item) => item.label === name);
    assert.equal(stage('В пути').after, '00:11', 'ожидание клиента должно считаться посекундно');
    assert.equal(stage('На месте').after, '06:12');
    assert.match(card.order.id, /^[0-9a-f]{32}$/, 'ID заказа не 32 hex');
});
