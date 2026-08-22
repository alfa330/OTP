/* Раздел «Тренажёр»: что считается прозвучавшим, когда реплику перебили.
 *
 * Эти случаи нашлись НЕ здесь, а прогоном в настоящем Chrome — и оба раза
 * ошибка была в оркестровке speak(), а не в чистых функциях, которые уже
 * покрыты. Поэтому здесь поднимается поддельная звуковая подсистема с живыми
 * часами: без неё воспроизведение, ожидание конца речи и перебивание никак не
 * проверяются, а именно на них раздел и ломался.
 *
 * Что закреплено:
 *   1. дослушанная реплика отчитывается целиком;
 *   2. перебитая отчитывается ТЕМ, ЧТО УСПЕЛО ПРОЗВУЧАТЬ, а не нулём;
 *   3. текст на экране не откатывается назад после перебивания.
 *
 * Пункт 2 — это ровно тот дефект: проверка «перебили ли» стояла до ожидания
 * конца звука, а перебивают как раз во время него. Реплика отчитывалась как
 * «прозвучало 0 из 81 знака», текст обнулялся, и модель считала, что не
 * сказала ничего, — то есть договаривать было бы нечего.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { VoiceLink } from '../src/components/trainer/voice.js';

const RATE = 24000;
const CHUNK_S = 0.2;                       // короткие куски: тест не должен ждать
const TEXT = 'Заказ был сегодня в два двадцать, отменил его сам клиент, а деньги мне не пришли.';

/** Кусок звука в base64 — содержимое не важно, важна длительность. */
const chunk = () => Buffer.from(new Int16Array(Math.round(RATE * CHUNK_S)).buffer)
    .toString('base64');

/** Звуковая подсистема с настоящими часами и без настоящего звука. */
class FakeContext {
    constructor() {
        this.started = performance.now();
        this.baseLatency = 0;
        this.state = 'running';
        this.destination = { name: 'destination' };
        this.scheduled = [];
    }

    get currentTime() { return (performance.now() - this.started) / 1000; }

    createBuffer(_channels, length, sampleRate) {
        return { duration: length / sampleRate, getChannelData: () => new Float32Array(length) };
    }

    createBufferSource() {
        const source = { connect() {}, start: (at) => { source.at = at; }, stop() {}, onended: null };
        this.scheduled.push(source);
        return source;
    }

    createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
    close() { this.state = 'closed'; }
}

/** Поток озвучки: три куска и 'done', с задержкой между событиями. */
const speakResponse = (gapMs = 20) => ({
    ok: true,
    body: new ReadableStream({
        start(controller) {
            const enc = new TextEncoder();
            const events = [
                { t: 'start', provider: 'vertex', model: 'm', rate: RATE },
                { t: 'audio', b64: chunk() },
                { t: 'audio', b64: chunk() },
                { t: 'audio', b64: chunk() },
                { t: 'done', ttfb_ms: 100, audio_ms: CHUNK_S * 3000, bytes: 1, rate: RATE },
            ];
            let index = 0;
            const push = () => {
                if (index >= events.length) { controller.close(); return; }
                controller.enqueue(enc.encode(`data: ${JSON.stringify(events[index++])}\n\n`));
                setTimeout(push, gapMs);
            };
            push();
        },
    }),
});

/** Канал, поднятый без микрофона и без сети. */
const linkWithFakes = () => {
    const log = [];
    const link = new VoiceLink({
        apiBaseUrl: '', headers: () => ({}),
        onEvent: (type, payload) => log.push({ type, ...payload }),
        telephone: false,                  // тракт проверяется отдельно, в браузере
    });
    link.ctx = new FakeContext();
    link.charsPerSec = 13.3;
    const saved = globalThis.fetch;
    globalThis.fetch = async () => speakResponse();
    return { link, log, restore: () => { globalThis.fetch = saved; } };
};

/** Речь человека так, как её отдаёт Soniox. */
const humanSays = (link, text, lengthMs) => link.onSttMessage({
    data: JSON.stringify({ tokens: [{
        text, is_final: true, confidence: 0.95,
        start_ms: link.micMs + 500, end_ms: link.micMs + 500 + lengthMs,
    }] }),
});

test('дослушанная реплика отчитывается целиком', async () => {
    const { link, log, restore } = linkWithFakes();
    try {
        const result = await link.speak(TEXT, { turnId: 1, since: performance.now() });
        assert.equal(result.spoken.spoken_chars, TEXT.length);
        assert.equal(result.spoken.cut, false);
        const end = log.find((e) => e.type === 'speech_end');
        assert.equal(end.cut, false);
        assert.equal(end.spoken_chars, TEXT.length);
        // Текст открывался постепенно, а не появился разом.
        const said = log.filter((e) => e.type === 'said');
        assert.ok(said.length >= 3, `шагов открытия текста всего ${said.length}`);
    } finally { restore(); }
});

test('перебитая реплика отчитывается прозвучавшим, а не нулём', async () => {
    const { link, log, restore } = linkWithFakes();
    try {
        // Перебиваем в середине речи: куски к этому моменту УЖЕ приехали, и
        // событий 'audio' после перебивания не будет — именно на этом и
        // ломалась прежняя проверка «перебили ли».
        setTimeout(() => humanSays(link, 'вы меня совсем не поняли', 400), 300);
        const result = await link.speak(TEXT, { turnId: 2, since: performance.now() });

        assert.ok(result.spoken, 'услышанное потеряно');
        assert.ok(result.spoken.spoken_ms > 0,
            `прозвучало ${result.spoken.spoken_ms} мс — перебивание обнулило замер`);
        assert.ok(result.spoken.spoken_chars > 0 && result.spoken.spoken_chars < TEXT.length,
            `прозвучало ${result.spoken.spoken_chars} из ${TEXT.length} знаков`);
        assert.equal(result.spoken.cut, true);

        const barge = log.find((e) => e.type === 'barge');
        assert.ok(barge, 'перебивания не случилось вовсе');
        assert.equal(barge.rule, 'speech');
        assert.equal(barge.spoken_chars, result.spoken.spoken_chars);
    } finally { restore(); }
});

test('текст на экране не откатывается назад после перебивания', async () => {
    const { link, log, restore } = linkWithFakes();
    try {
        setTimeout(() => humanSays(link, 'вы меня совсем не поняли', 400), 300);
        await link.speak(TEXT, { turnId: 3, since: performance.now() });
        const steps = log.filter((e) => e.type === 'said').map((e) => e.chars);
        assert.ok(steps.length, 'текст не открывался вовсе');
        for (let i = 1; i < steps.length; i += 1) {
            assert.ok(steps[i] >= steps[i - 1],
                `позиция откатилась: ${steps.join(' → ')}`);
        }
    } finally { restore(); }
});

test('поддакивание речь не обрывает', async () => {
    const { link, log, restore } = linkWithFakes();
    try {
        setTimeout(() => humanSays(link, 'ага', 200), 300);
        const result = await link.speak(TEXT, { turnId: 4, since: performance.now() });
        assert.equal(log.find((e) => e.type === 'barge'), undefined,
            '«ага» посреди речи оборвало реплику');
        assert.equal(result.spoken.cut, false);
    } finally { restore(); }
});
