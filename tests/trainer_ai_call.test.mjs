import assert from 'node:assert/strict';
import test from 'node:test';

import { createAiCall } from '../src/components/trainer/aiCall.js';

/* Звонок ИИ-водителя в учебном рабочем месте: переходник между машиной звонка
   (iCORE Phone) и голосовым разговором раздела «Тренажёр». Сервер и VoiceLink —
   поддельные: проверяем порядок и правила, а не сеть. */

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async (n = 6) => { for (let i = 0; i < n; i += 1) await tick(); };

const fakeServer = ({ slowTurn = false } = {}) => {
    const calls = [];
    let turn = 100;
    let release = null;
    const api = async (path, init = {}) => {
        const body = init.body ? JSON.parse(init.body) : null;
        calls.push([init.method || 'GET', path, body]);
        if (path === '/sessions') return { session_id: 7, opening: 'Алло, здравствуйте.', opening_turn_id: 99 };
        if (path === '/sessions/7/turn') {
            if (slowTurn && !release) await new Promise((resolve) => { release = resolve; });
            turn += 1;
            return { turn_id: turn, role: 'driver', text: `ответ на «${body.text}»` };
        }
        if (path === '/sessions/7/finish') return { review: { score: 7 }, cost: { total: 0.01 } };
        return {};
    };
    return { api, calls, releaseTurn: () => release && release() };
};

const fakeLink = () => {
    const link = { started: 0, stopped: 0, spoken: [], onEvent: null };
    const make = (onEvent) => {
        link.onEvent = onEvent;
        return {
            start: async () => { link.started += 1; },
            stop: () => { link.stopped += 1; },
            speak: async (text, opts) => { link.spoken.push([text, opts.turnId]); return {}; },
        };
    };
    return { link, make };
};

test('ответили на звонок — сессия водителя, микрофон и приветствие', async () => {
    const server = fakeServer();
    const { link, make } = fakeLink();
    const events = [];
    const call = createAiCall({ api: server.api, makeLink: make, scenarioKey: 'payout_angry' });
    call.subscribe((type, payload) => events.push([type, payload.text || '']));

    call.onCall('answer');
    await settle();
    assert.deepEqual(server.calls[0], ['POST', '/sessions', { mode: 'driver', scenario: 'payout_angry', client: {} }]);
    assert.equal(link.started, 1, 'микрофон не включился');
    assert.deepEqual(link.spoken, [['Алло, здравствуйте.', 99]]);
    assert.deepEqual(events.slice(0, 2), [['speech', 'Алло, здравствуйте.'], ['speech_end', '']]);
    // voice.start/stop плеера сессией не правят — иначе удержание рвало бы разговор.
    call.stop();
    assert.equal(link.stopped, 0);
});

test('реплика оператора → ответ водителя; сказанное, пока модель думает, не теряется', async () => {
    const server = fakeServer({ slowTurn: true });
    const { link, make } = fakeLink();
    const call = createAiCall({ api: server.api, makeLink: make, scenarioKey: 'cancel_calm' });
    call.onCall('answer');
    await settle();

    link.onEvent('utterance', { text: 'Здравствуйте, оператор Асель.', at: 1 });
    await settle();
    link.onEvent('utterance', { text: 'Слушаю вас.', at: 2 });
    link.onEvent('utterance', { text: 'Чем помочь?', at: 3 });
    server.releaseTurn();
    await settle(12);

    const turns = server.calls.filter(([, path]) => path === '/sessions/7/turn').map(([, , body]) => body.text);
    assert.deepEqual(turns, ['Здравствуйте, оператор Асель.', 'Слушаю вас. Чем помочь?'],
        'реплики, сказанные пока модель думала, должны уйти одной следующей');
    assert.deepEqual(link.spoken.map(([text]) => text).slice(1),
        ['ответ на «Здравствуйте, оператор Асель.»', 'ответ на «Слушаю вас. Чем помочь?»']);
});

test('на удержании водитель не слышит комнату; сняли удержание — снова слышит', async () => {
    const server = fakeServer();
    const { link, make } = fakeLink();
    const call = createAiCall({ api: server.api, makeLink: make, scenarioKey: 'vague_kk' });
    call.onCall('answer');
    await settle();
    call.onCall('hold');
    link.onEvent('utterance', { text: 'Коллега, глянь ведомость.', at: 5 });
    await settle();
    assert.equal(server.calls.filter(([, path]) => path.endsWith('/turn')).length, 0, 'водитель услышал удержание');
    call.onCall('unhold');
    link.onEvent('utterance', { text: 'Спасибо за ожидание.', at: 6 });
    await settle();
    assert.equal(server.calls.filter(([, path]) => path.endsWith('/turn')).length, 1);
    // Микрофон выключен кнопкой iCORE Phone — водитель тоже не слышит.
    call.onCall('mic', { muted: true });
    link.onEvent('utterance', { text: 'Это я себе.', at: 7 });
    await settle();
    assert.equal(server.calls.filter(([, path]) => path.endsWith('/turn')).length, 1);
    call.onCall('mic', { muted: false });
    link.onEvent('utterance', { text: 'Слушаю.', at: 8 });
    await settle();
    assert.equal(server.calls.filter(([, path]) => path.endsWith('/turn')).length, 2);
});

test('положили трубку: голос гаснет сразу, разбор собирается и отдаётся один раз', async () => {
    const server = fakeServer();
    const { link, make } = fakeLink();
    const reviews = [];
    const call = createAiCall({
        api: server.api, makeLink: make, scenarioKey: 'payout_angry', onReview: (r) => reviews.push(r),
    });
    call.onCall('answer');
    await settle();
    call.onCall('end');
    assert.equal(link.stopped, 1, 'микрофон после трубки не выключен — Soniox продолжает считать');
    await settle();
    assert.ok(server.calls.some(([method, path]) => method === 'POST' && path === '/sessions/7/finish'));
    // Реплика, распознанная уже после трубки, водителю не уходит.
    link.onEvent('utterance', { text: 'Алло?', at: 9 });
    await settle();
    assert.equal(server.calls.filter(([, path]) => path.endsWith('/turn')).length, 0);

    const result = await call.finish();
    assert.deepEqual(result.review, { score: 7 });
    await call.finish();
    call.onCall('finish');
    await settle();
    assert.equal(reviews.length, 1, 'разбор показан дважды');
});

test('трубку положили раньше, чем сервер завёл сессию, — её закрывают сразу; без ответа разбора нет', async () => {
    const server = fakeServer();
    const { link, make } = fakeLink();
    const call = createAiCall({ api: server.api, makeLink: make, scenarioKey: 'payout_angry' });
    call.onCall('answer');
    call.onCall('end');
    await settle();
    assert.equal(link.started, 0, 'микрофон включился после трубки');
    assert.ok(server.calls.some(([, path]) => path === '/sessions/7/finish'));

    const missed = createAiCall({ api: server.api, makeLink: make, scenarioKey: 'payout_angry' });
    missed.onCall('end');
    const result = await missed.finish();
    assert.equal(result.review, null);
    assert.match(result.review_error, /разговора не было/);
});

test('отказ звена виден: сессия не завелась — сообщение, микрофон не висит', async () => {
    const problems = [];
    const { link, make } = fakeLink();
    const call = createAiCall({
        api: async () => { throw new Error('HTTP 503'); },
        makeLink: make, scenarioKey: 'payout_angry', onProblem: (text) => problems.push(text),
    });
    call.onCall('answer');
    await settle();
    assert.deepEqual(problems, ['не удалось начать разговор: HTTP 503']);
    assert.equal(link.started, 0);
    call.dispose();
});
