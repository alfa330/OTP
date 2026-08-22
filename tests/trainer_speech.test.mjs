/* Раздел «Тренажёр»: собеседник не должен перебивать человека.
 *
 * Распознавание ставит точку по паузе в 600 мс, и этого мало — человек посреди
 * предложения думает дольше. На проде 22.08.2026 из-за этого реплики уходили в
 * модель обрывками, и собеседник отвечал на половину фразы: «Ты уверен, что.»,
 * «Нужно делать всего лишь.», «Вижу то, что заказ был полностью на.»,
 * «Акциясы бойынша.». Все они здесь и стоят — как случаи, а не как выдумка.
 *
 * Правило: после точки ждём выдержку. Законченная фраза ждёт чуть-чуть,
 * оборванная — заметно дольше. Продолжил говорить — отправка отменяется, а
 * сказанное приклеивается к тому, что уже накоплено.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { utteranceHold, VoiceLink } from '../src/components/trainer/voice.js';

const LONG = 1200;
const SHORT = 300;

test('оборванная фраза ждёт дольше законченной', () => {
    for (const text of [
        'Ты уверен, что.',
        'Нужно делать всего лишь.',
        'Вижу то, что заказ был полностью на.',
        'Акциясы бойынша.',          // казахский послелог «по/о»
        'Казына —',                  // тире в конце
        'Жеті қазына.',              // два слова: названия мало для реплики
    ]) {
        assert.equal(utteranceHold(text), LONG, `должно ждать дольше: ${text}`);
    }
    for (const text of [
        'Здравствуйте, меня зовут Айгуль, чем могу помочь?',
        'Вы можете подсказать адрес?',
        'Какие документы нужны водителю для регистрации?',
    ]) {
        assert.equal(utteranceHold(text), SHORT, `не должно тормозить: ${text}`);
    }
});

test('выдержка настраивается — её присылает сервер', () => {
    assert.equal(utteranceHold('Ты уверен, что.', { short: 10, long: 40 }), 40);
    assert.equal(utteranceHold('Вы можете подсказать адрес?', { short: 10, long: 40 }), 10);
});

/* ── склейка ────────────────────────────────────────────────────────────── */

const message = (link, tokens) => link.onSttMessage({ data: JSON.stringify({ tokens }) });
const speech = (text) => [{ text, is_final: true, confidence: 0.98, language: 'ru' }];
const ended = (text) => (text ? [...speech(text), { text: '<end>' }] : [{ text: '<end>' }]);
const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

const linkFor = (events) => new VoiceLink({
    apiBaseUrl: '', headers: () => ({}),
    onEvent: (type, payload) => events.push([type, payload]),
    hold: { short: 20, long: 80 },
});

test('точка не отправляет реплику сразу', async () => {
    const events = [];
    const link = linkFor(events);
    message(link, ended('Ты уверен, что'));
    assert.equal(events.filter(([type]) => type === 'utterance').length, 0,
        'реплика ушла, не дождавшись продолжения');
    await wait(140);
    const said = events.filter(([type]) => type === 'utterance');
    assert.equal(said.length, 1);
    assert.equal(said[0][1].text, 'Ты уверен, что');
    link.stop();
});

test('человек продолжил — реплика склеивается, а не режется надвое', async () => {
    const events = [];
    const link = linkFor(events);
    message(link, ended('Ты уверен, что'));
    await wait(30);                                  // меньше длинной выдержки
    message(link, speech(' в статьях этого нет'));   // продолжил говорить
    message(link, ended(''));                        // и замолчал по-настоящему
    await wait(140);
    const said = events.filter(([type]) => type === 'utterance');
    assert.equal(said.length, 1, 'реплика разъехалась на две');
    assert.equal(said[0][1].text, 'Ты уверен, что в статьях этого нет');
    link.stop();
});

test('замеры реплики переживают склейку', async () => {
    const events = [];
    const link = linkFor(events);
    message(link, ended('Ты уверен, что'));
    await wait(30);
    message(link, speech(' в статьях этого нет'));
    message(link, ended(''));
    await wait(140);
    const [, payload] = events.find(([type]) => type === 'utterance');
    assert.equal(payload.metrics.stt_tokens, 2, 'токены обеих частей должны сложиться');
    assert.equal(payload.metrics.stt_lang, 'ru');
    assert.ok(payload.metrics.hold_ms > 0, 'выдержку надо записать в замеры');
    link.stop();
});

test('повторная точка не отодвигает отправку бесконечно', async () => {
    const events = [];
    const link = linkFor(events);
    message(link, ended('Вы можете подсказать адрес?'));
    await wait(12);
    message(link, [{ text: '<end>' }]);      // та же реплика, точка повторно
    await wait(12);
    message(link, [{ text: '<end>' }]);
    await wait(40);
    assert.equal(events.filter(([type]) => type === 'utterance').length, 1,
        'выдержка перезапускалась на каждой точке');
    link.stop();
});

test('законченная фраза не ждёт длинную выдержку', async () => {
    const events = [];
    const link = linkFor(events);
    message(link, ended('Вы можете подсказать адрес?'));
    await wait(45);                          // больше короткой, меньше длинной
    assert.equal(events.filter(([type]) => type === 'utterance').length, 1);
    link.stop();
});

/* ── Реплика человека не должна уходить дважды ────────────────────────────────
 *
 * Прод, сессия 25 от 22.08.2026. Распознавание сначала присылает черновую
 * догадку, и только потом те же слова окончательными. Реплика уходила по
 * черновику, буфер очищался, окончательные падали в пустой буфер — и следующая
 * реплика уносила то же начало:
 *
 *   #19 «Хорошо, я сейчас написал обращение в сторону Яндекса.»        токенов —
 *   #21 «Хорошо, я сейчас написал обращение в сторону Яндекса. Вам …»  токенов 9
 *   #23 «писал обращение в сторону Яндекса. Вам нужно будет …»         токенов 50
 *
 * Пустой stt_tokens у первой отправки — прямая улика: окончательным не был ни
 * один токен, то есть ушёл именно черновик.
 */
test('окончательные токены, догнавшие отправку, не уходят второй раз', () => {
    const link = new VoiceLink({ apiBaseUrl: '', headers: () => ({}), onEvent: () => {} });
    link.sentText = 'Хорошо, я сейчас написал обращение в сторону Яндекса.';
    link.sentAt = performance.now();

    // Те же слова, пришедшие окончательными, плюс продолжение фразы.
    assert.equal(
        link.withoutAlreadySent('Хорошо, я сейчас написал обращение в сторону Яндекса. '
                                + 'Вам нужно будет перезвонить через пятнадцать минут.'),
        'Вам нужно будет перезвонить через пятнадцать минут.');

    // Ровно то же самое и ничего сверх — отправлять нечего.
    assert.equal(
        link.withoutAlreadySent('Хорошо, я сейчас написал обращение в сторону Яндекса.'), '');

    // Пунктуация у окончательного токена другая — сравнение по словам обязано
    // это пережить, иначе рубеж не сработает как раз там, где нужен.
    assert.equal(
        link.withoutAlreadySent('хорошо я сейчас написал обращение в сторону яндекса, '
                                + 'вам нужно перезвонить'),
        'вам нужно перезвонить');
});

test('человек, повторивший фразу через паузу, не проглатывается', () => {
    const link = new VoiceLink({ apiBaseUrl: '', headers: () => ({}), onEvent: () => {} });
    link.sentText = 'Алло, вы меня слышите?';
    // Догнавший хвост приходит за доли секунды; три секунды спустя это уже
    // человек, который действительно повторил вопрос.
    link.sentAt = performance.now() - 5000;
    assert.equal(link.withoutAlreadySent('Алло, вы меня слышите?'), 'Алло, вы меня слышите?');
});

test('первая реплика разговора ничем не обрезается', () => {
    const link = new VoiceLink({ apiBaseUrl: '', headers: () => ({}), onEvent: () => {} });
    assert.equal(link.withoutAlreadySent('Здравствуйте, меня зовут Руслан.'),
                 'Здравствуйте, меня зовут Руслан.');
});
