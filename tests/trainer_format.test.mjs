/* Правила показа замеров в разделе «Тренажёр».
 *
 * Тест здесь потому, что эти правила решают, как читается журнал: какая пауза
 * считается плохой и не превращается ли стоимость прогона в «$0,00». В JSX это
 * не проверялось бы ничем.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    fmtCost, fmtDuration, fmtLangs, fmtMs, paceTone, roleLabel, roleSide,
    statusLabel, summarize,
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
