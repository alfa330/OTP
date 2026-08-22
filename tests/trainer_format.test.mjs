/* Правила показа замеров в разделе «Тренажёр».
 *
 * Тест здесь потому, что эти правила решают, как читается журнал: какая пауза
 * считается плохой и не превращается ли стоимость прогона в «$0,00». В JSX это
 * не проверялось бы ничем.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    applySpeech, fmtCost, fmtDuration, fmtLangs, fmtMs, mergeMetrics, paceTone,
    roleLabel, roleSide, statusLabel, summarize,
} from '../src/components/trainer/trainerFormat.js';

test('пауза красится только когда требует внимания', () => {
    assert.equal(paceTone(1200), 'good');
    assert.equal(paceTone(2000), 'good');
    assert.equal(paceTone(2900), 'warn');
    assert.equal(paceTone(4001), 'bad');
    assert.equal(paceTone(null), 'muted');
});

test('доли цента не округляются до нуля', () => {
    // Прогон на бесплатных тирах стоит меньше цента: округление до копеек
    // показало бы «$0,00» по всему журналу и обесценило бы колонку.
    assert.equal(fmtCost(0.0032), '$0.0032');
    assert.equal(fmtCost(0.42), '$0.42');
    assert.equal(fmtCost(0), '$0');
    assert.equal(fmtCost(null), '—');
});

test('время показывается человеку, а не в миллисекундах', () => {
    assert.equal(fmtMs(2940), '2,9 с');
    assert.equal(fmtMs(null), '—');
    assert.equal(fmtDuration(192000), '3 мин 12 с');
    assert.equal(fmtDuration(45000), '45 с');
});

test('языковой состав считается в процентах и по убыванию', () => {
    assert.equal(fmtLangs({ ru: 8, kk: 2 }), 'ru 80% · kk 20%');
    assert.equal(fmtLangs({ kk: 3, ru: 7 }), 'ru 70% · kk 30%');
    assert.equal(fmtLangs({}), null);
    assert.equal(fmtLangs(null), null);
});

test('сводка берёт медиану, а не среднее', () => {
    // Одна долгая реплика (например, когда подменялся провайдер) утащила бы
    // среднее и создала впечатление, что тормозит весь разговор.
    const turns = [
        { pace_ms: 1800, llm: { total_ms: 1000 }, tts: { ttfb_ms: 1200 } },
        { pace_ms: 2000, llm: { total_ms: 1200 }, tts: { ttfb_ms: 1300 } },
        { pace_ms: 12000, llm: { total_ms: 9000 }, tts: { ttfb_ms: 1400 }, barge_in: true },
    ];
    const summary = summarize(turns);
    assert.equal(summary.pace, 2000);
    assert.equal(summary.paceWorst, 12000);
    assert.equal(summary.barge, 1);
    assert.equal(summary.turns, 3);
});

test('сводка не падает на пустом разговоре', () => {
    const summary = summarize([]);
    assert.equal(summary.pace, null);
    assert.equal(summary.turns, 0);
});

test('говорящие подписаны по режиму и делятся на две стороны', () => {
    assert.equal(roleLabel('trainee'), 'Стажёр');
    assert.equal(roleLabel('mentor'), 'Наставник');
    assert.equal(roleSide('asker'), 'human');
    assert.equal(roleSide('driver'), 'ai');
});

test('статусы переведены', () => {
    assert.equal(statusLabel('finished'), 'завершён');
    assert.equal(statusLabel('active'), 'идёт');
});

test('модуль захвата микрофона грузится ОТ БАЗЫ СБОРКИ, а не от корня домена', async () => {
    // Прод-дефект 22.08.2026: фронт живёт на GitHub Pages в подпапке /OTP/, а
    // абсолютный '/trainer-worklet.js' уходил в корень домена. Браузер отвечал
    // «Unable to load a worklet's module», локально при базе '/' всё работало.
    const { workletUrl } = await import('../src/components/trainer/voice.js');
    assert.equal(workletUrl('/'), '/trainer-worklet.js');
    assert.equal(workletUrl('/OTP/'), '/OTP/trainer-worklet.js');
    // Пропущенная косая склеила бы путь в '/OTPtrainer-worklet.js', и ошибка
    // выглядела бы точно так же, как отсутствие файла.
    assert.equal(workletUrl('/OTP'), '/OTP/trainer-worklet.js');
    assert.equal(workletUrl(''), '/trainer-worklet.js');
});

/* ── Текст открывается под речь ──────────────────────────────────────────────
 *
 * Владелец просил, чтобы на экране это выглядело как стриминг. Но показывать
 * надо не «что сгенерировала модель», а «что человек УСЛЫШАЛ»: до правки
 * 22.08.2026 текст реплики появлялся целиком в тот момент, когда звука ещё не
 * было вовсе — и в пятой части случаев так и не появлялось.
 */
test('текст открывается по мере речи, а на перебивании обрывается там же, где звук', () => {
    const text = 'Заказ был сегодня, отменил его сам клиент.';
    let turns = [{ id: 7, role: 'driver', text }];

    // До первого сэмпла на экране пусто, а не весь текст.
    turns = applySpeech(turns, 'speech_start', { turn_id: 7 });
    assert.equal(turns[0].shown, '');

    turns = applySpeech(turns, 'said', { turn_id: 7, chars: 17, text: text.slice(0, 17) });
    assert.equal(turns[0].shown, 'Заказ был сегодня');

    turns = applySpeech(turns, 'barge', {
        turn_id: 7, spoken_ms: 1180, spoken_chars: 17, rule: 'speech' });
    assert.equal(turns[0].barge_in, true);
    assert.equal(turns[0].spoken.cut, true);
    // Текст остаётся оборванным ровно там, где оборвался звук.
    assert.equal(turns[0].shown, 'Заказ был сегодня');
});

test('дослушанная реплика показывается целиком', () => {
    const text = 'Жақсы.';
    let turns = [{ id: 8, role: 'driver', text, shown: '' }];
    turns = applySpeech(turns, 'speech_end', {
        turn_id: 8, spoken_ms: 900, spoken_chars: text.length, total_ms: 900, cut: false });
    assert.equal(turns[0].shown, null, 'shown === null значит «показать целиком»');
    assert.equal(turns[0].spoken.cut, false);
});

test('доля дослушанного считается по репликам, где известны обе цифры', () => {
    const heard = summarize([
        { id: 1, spoken: { ms: 1000 }, total_ms: 2000 },
        { id: 2, spoken: { ms: 3000 }, total_ms: 3000 },
        { id: 3 },                                        // без замеров — не в счёт
    ]).heard;
    assert.equal(heard, 75);
    assert.equal(summarize([]).heard, null);
});

test('событие чужой реплики ленту не трогает', () => {
    const turns = [{ id: 7, text: 'а', shown: 'а' }];
    assert.deepEqual(applySpeech(turns, 'said', { turn_id: 99, text: 'б' }), turns);
    assert.deepEqual(applySpeech(turns, 'said', {}), turns);
});

test('склейка двух реплик подряд не теряет замеры первой', () => {
    // Просто перекрыть первую второй нельзя: пропадёт длительность звука (она
    // идёт в стоимость распознавания) и отметка перебивания — тогда
    // подтверждённое перебивание не попадёт ни в реплику, ни в счёт сессии.
    const merged = mergeMetrics(
        { stt_audio_ms: 1200, stt_tokens: 4, barge_in: true, hold_ms: 1200,
          prev: { turn_id: 9 }, stt_lang: 'ru' },
        { stt_audio_ms: 800, stt_tokens: 3, barge_in: false, hold_ms: 300, stt_lang: 'kk' });
    assert.equal(merged.stt_audio_ms, 2000);
    assert.equal(merged.stt_tokens, 7);
    assert.equal(merged.barge_in, true, 'перебивание первой реплики потеряно');
    assert.equal(merged.hold_ms, 300);
    assert.deepEqual(merged.prev, { turn_id: 9 }, 'услышанное прошлой реплики потеряно');
    assert.equal(merged.stt_lang, 'kk');
});
