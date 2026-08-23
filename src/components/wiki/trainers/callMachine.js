/* Конечный автомат входящего звонка.
 *
 * Чистый модуль без React и без таймеров: его гоняют тесты обычным node.
 * Всё, что знает про DOM, звук и время, живёт в панели звонка.
 *
 * offline → ready → ringing → talking → held → wrapup → ended
 *
 * Каждое правило здесь — учебный момент, а не формальность:
 *
 *   ready требует oktLogged && oktIn && !oktStatus. Не вошёл в call-центр —
 *   звонков нет. Поставил «Перерыв» — звонков нет. На экране Okapp это уже
 *   написано словами, и теперь это правда, а не надпись.
 *
 *   таймер разговора идёт от ОТВЕТА, а не от начала вызова: время ожидания
 *   в очереди — не разговор, и складывать их значит врать в отчёте.
 *
 *   «Отклонить» уводит сразу в ended, минуя постобработку: оформлять нечего.
 *   А перевод коллеге — наоборот, в wrapup: звонок ушёл, но обращение всё
 *   равно заводит тот, кто его принял.
 */

export const CALL_STATES = ['offline', 'ready', 'ringing', 'talking', 'held', 'wrapup', 'ended'];

/** Пустое состояние звонка — то, с чего начинается попытка. */
export const emptyCall = () => ({
    state: 'offline',
    phone: '',
    queue: '',
    waitedSec: 0,
    ringingAt: null,
    answeredAt: null,
    endedAt: null,
    holdStartedAt: null,
    holdMs: 0,
    transferredTo: null,
    reason: null,
    muted: false,
});

/** Можно ли сейчас принимать звонки. */
export const canRing = (world) => Boolean(
    world && world.oktLogged && world.oktIn && !world.oktStatus,
);

/**
 * Привести готовность в соответствие с Okapp.
 *
 * Зовётся после каждого изменения мира: стажёр может уйти на перерыв прямо во
 * время ожидания звонка, и тогда линия обязана погаснуть. А вот идущий
 * РАЗГОВОР перерыв не обрывает — в жизни он тоже не обрывается.
 */
export const syncReady = (call, world) => {
    const state = call.state;
    if (state === 'offline' || state === 'ready') {
        const next = canRing(world) ? 'ready' : 'offline';
        return next === state ? call : { ...call, state: next };
    }
    if (state === 'ringing' && !canRing(world)) {
        // Ушёл со связи, пока телефон звонил — вызов сорвался.
        return { ...call, state: 'ended', reason: 'missed', endedAt: null };
    }
    return call;
};

const at = (now) => (typeof now === 'number' ? now : Date.now());

/**
 * Переход автомата. Возвращает { call, events } — новое состояние и список
 * событий для ленты (см. таблицу кодов в trainerEvents.js).
 *
 * Недопустимый переход НЕ бросает исключение и ничего не меняет: панель может
 * прислать «ответить» вторым нажатием по уже снятой трубке, и падать на этом
 * посреди урока незачем.
 */
export const callNext = (call, action, payload = {}, now = undefined) => {
    const time = at(now);
    const no = { call, events: [] };

    switch (action) {
    case 'ring':
        if (call.state !== 'ready') return no;
        return {
            call: {
                ...call,
                state: 'ringing',
                phone: payload.phone || '',
                queue: payload.queue || '',
                waitedSec: Number(payload.waitedSec) || 0,
                ringingAt: time,
            },
            events: [['call.incoming', { phone: payload.phone || '', queue: payload.queue || '' }]],
        };

    case 'answer':
        if (call.state !== 'ringing') return no;
        return {
            call: { ...call, state: 'talking', answeredAt: time },
            events: [['call.answer', { after_ms: call.ringingAt ? time - call.ringingAt : null }]],
        };

    case 'reject':
        if (call.state !== 'ringing') return no;
        return {
            call: { ...call, state: 'ended', endedAt: time, reason: 'rejected' },
            events: [['call.reject', { after_ms: call.ringingAt ? time - call.ringingAt : null }]],
        };

    case 'hold':
        if (call.state !== 'talking') return no;
        return {
            call: { ...call, state: 'held', holdStartedAt: time },
            events: [['call.hold', { at_ms: call.answeredAt ? time - call.answeredAt : null }]],
        };

    case 'unhold':
        if (call.state !== 'held') return no;
        return {
            call: {
                ...call,
                state: 'talking',
                holdStartedAt: null,
                holdMs: call.holdMs + (call.holdStartedAt ? time - call.holdStartedAt : 0),
            },
            events: [['call.unhold', { at_ms: call.answeredAt ? time - call.answeredAt : null }]],
        };

    case 'transfer':
        if (call.state !== 'talking' && call.state !== 'held') return no;
        return {
            call: {
                ...call,
                state: 'wrapup',
                endedAt: time,
                transferredTo: payload.to || '',
                reason: 'transferred',
            },
            events: [
                ['call.transfer', { to: payload.to || '' }],
                ['call.end', { by: 'operator', duration_ms: talkMs({ ...call, endedAt: time }) }],
            ],
        };

    case 'end':
        if (call.state !== 'talking' && call.state !== 'held') return no;
        return {
            call: { ...call, state: 'wrapup', endedAt: time, reason: payload.by || 'operator' },
            events: [['call.end', {
                by: payload.by === 'driver' ? 'driver' : 'operator',
                duration_ms: talkMs({ ...call, endedAt: time }),
            }]],
        };

    case 'finish':
        // Постобработка закончена: попытку можно закрывать.
        if (call.state !== 'wrapup') return no;
        return { call: { ...call, state: 'ended' }, events: [] };

    case 'mute':
        return { call: { ...call, muted: !call.muted }, events: [] };

    default:
        return no;
    }
};

/** Сколько шёл разговор. Считается от ОТВЕТА, ожидание в очереди не входит. */
export const talkMs = (call, now = undefined) => {
    if (!call || !call.answeredAt) return 0;
    const end = call.endedAt || at(now);
    return Math.max(0, end - call.answeredAt);
};

/** «03:41» — так время разговора выглядит в софтфоне. */
export const talkClock = (ms) => {
    const total = Math.floor(Math.max(0, ms) / 1000);
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
};

/** Идёт ли разговор прямо сейчас (для индикатора «Говорит ИИ» и микрофона). */
export const isLive = (call) => call.state === 'talking' || call.state === 'held';
