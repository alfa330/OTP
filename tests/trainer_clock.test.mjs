/* Раздел «Тренажёр»: часы речи — сколько текста прозвучало.
 *
 * Пословных меток синтез не отдаёт ни у Vertex, ни у Live API, поэтому позиция
 * в тексте считается из длительности. Тест держит два правила, на которых всё
 * остальное стоит: разрез всегда по границе слова и всегда ВНИЗ. Обе
 * систематические ошибки замера (задержка вывода звука и хвостовая тишина в
 * длительности синтеза) смещают оценку вниз, так что худший исход — собеседник
 * повторит одно слово, а не проглотит его.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { spokenChars, weighText, bargeVerdict, BACKCHANNEL } from '../src/components/trainer/speechClock.js';

test('разрез всегда попадает на границу слова', () => {
    const text = 'Здравствуйте, у меня заказ отменили, а деньги не пришли.';
    for (let played = 0; played <= 4000; played += 37) {
        const chars = spokenChars(text, played, 4000);
        const head = text.slice(0, chars);
        assert.ok(chars >= 0 && chars <= text.length, `позиция вне текста: ${chars}`);
        const before = text[chars - 1] || '';
        const after = text[chars] || '';
        assert.ok(!(/[\p{L}\d]/u.test(before) && /[\p{L}\d]/u.test(after)),
            `слово разрезано пополам: «${head}|${text.slice(chars, chars + 6)}»`);
    }
});

test('прозвучавшее и остаток посимвольно складываются в исходный текст', () => {
    const text = 'Алло, здравствуйте. Деньги не могу вывести никак.';
    for (const played of [0, 200, 900, 1500, 2400]) {
        const chars = spokenChars(text, played, 2500);
        assert.equal(text.slice(0, chars) + text.slice(chars), text);
    }
});

test('позиция не убывает с ростом прозвучавшего времени', () => {
    const text = 'Мне за ожидание ничего не пришло вроде. Разберитесь, пожалуйста.';
    let previous = -1;
    for (let played = 0; played <= 5000; played += 50) {
        const chars = spokenChars(text, played, 5000);
        assert.ok(chars >= previous, `позиция откатилась: ${previous} → ${chars}`);
        previous = chars;
    }
});

test('звук доиграл — открыт весь текст, звука не было — ничего', () => {
    const text = 'Жақсы.';
    assert.equal(spokenChars(text, 5000, 4000), text.length);
    assert.equal(spokenChars(text, 4000, 4000), text.length);
    assert.equal(spokenChars(text, 0, 4000), 0);
    assert.equal(spokenChars(text, 1000, 0), 0);
});

test('точка между фразами весит больше буквы: на половине звука слышно первое предложение', () => {
    const text = 'Здравствуйте. Слушаю вас.';
    const chars = spokenChars(text, 1000, 2000);
    assert.ok(chars >= 'Здравствуйте.'.length - 1 && chars <= 'Здравствуйте. Слушаю'.length,
        `ожидали конец первой фразы, получили «${text.slice(0, chars)}»`);
});

test('цифра весит больше буквы — её произносят словом', () => {
    // «2500» это «две тысячи пятьсот»: четыре знака, а звучат дольше слова.
    assert.ok(weighText('2500').total > weighText('заказ').total,
        'четыре цифры должны весить больше пятибуквенного слова');
});

test('казахское слово не схлопывается в ноль', () => {
    // Ловушка счёта по слогам: ә, і, ө, ұ, ү попадают не во всякий список
    // гласных, и целое слово получало вес 0. Считаем по буквам.
    const kk = 'Тіркелгеніңіз үшін рақмет';
    assert.ok(weighText(kk).total > 20, `казахский текст весит ${weighText(kk).total}`);
    // На половине звука первое слово ещё не договорено — ноль здесь верен.
    assert.equal(spokenChars(kk, 400, 1000), 0);
    const chars = spokenChars(kk, 700, 1000);
    assert.ok(chars > 0 && chars < kk.length, `казахская фраза не режется: ${chars}`);
    assert.ok(!/[\p{L}]/u.test(kk[chars] || ' ') || !/[\p{L}]/u.test(kk[chars - 1] || ' '),
        'разрез внутри казахского слова');
});

test('пока не прозвучало ни сэмпла — перебивать нечего', () => {
    // Ровно тот прод-случай: реплика ещё не зазвучала, а хвост фразы самого
    // человека уже дошёл от распознавания. Девять из десяти перебиваний на
    // проде были именно такими.
    const verdict = bargeVerdict(
        [{ text: 'Помочь?', is_final: true, confidence: 0.95, end_ms: 12000 }],
        { audibleFromMicMs: null, atMs: 12000 });
    assert.equal(verdict.barge, false);
    assert.equal(verdict.rule, 'quiet');
});

test('хвост своей же фразы, договорённый до первого звука, не перебивает', () => {
    const verdict = bargeVerdict(
        [{ text: 'помочь', is_final: true, confidence: 0.95, end_ms: 9900 }],
        { audibleFromMicMs: 9800, graceMs: 250, atMs: 10200 });
    assert.equal(verdict.barge, false);
    assert.equal(verdict.rule, 'tail');
});

test('поддакивание не перебивает, а команда — перебивает одним словом', () => {
    const base = { audibleFromMicMs: 1000, graceMs: 250, atMs: 5000 };
    for (const word of ['ага', 'угу', 'иә', 'мхм', 'жақсы', 'понятно']) {
        const verdict = bargeVerdict(
            [{ text: word, is_final: true, confidence: 0.95, end_ms: 5000 }], base);
        assert.equal(verdict.barge, false, `«${word}» не должно перебивать`);
        assert.equal(verdict.rule, 'backchannel');
        assert.ok(BACKCHANNEL.has(word));
    }
    for (const word of ['стоп', 'тоқта', 'подождите', 'алло']) {
        const verdict = bargeVerdict(
            [{ text: word, is_final: true, confidence: 0.95, end_ms: 5000 }], base);
        assert.equal(verdict.barge, true, `«${word}» обязано перебить`);
        assert.equal(verdict.rule, 'stop');
    }
});

test('черновой токен с низкой уверенностью не перебивает, финальный — перебивает', () => {
    const base = { audibleFromMicMs: 1000, graceMs: 250, atMs: 5000, minConfidence: 0.7 };
    const noisy = bargeVerdict(
        [{ text: 'кхм шшш', is_final: false, confidence: 0.4, end_ms: 5000 }], base);
    assert.equal(noisy.barge, false);
    assert.equal(noisy.rule, 'noise');

    const real = bargeVerdict(
        [{ text: 'подождите секунду пожалуйста', is_final: true, confidence: 0.95,
           start_ms: 4200, end_ms: 5000 }], base);
    assert.equal(real.barge, true);
});

test('собственное эхо собеседника не считается перебиванием', () => {
    const saying = 'Мне за ожидание ничего не пришло вроде';
    const verdict = bargeVerdict(
        [{ text: 'ничего не пришло', is_final: true, confidence: 0.9, end_ms: 5000 }],
        { audibleFromMicMs: 1000, graceMs: 250, atMs: 5000,
          saying, saidChars: saying.length, echoRatio: 0.6 });
    assert.equal(verdict.barge, false);
    assert.equal(verdict.rule, 'echo');
});

test('настоящая речь поверх реплики перебивает', () => {
    const verdict = bargeVerdict(
        [{ text: 'вы меня совсем не поняли', is_final: true, confidence: 0.93,
           start_ms: 4000, end_ms: 5200 }],
        { audibleFromMicMs: 1000, graceMs: 250, atMs: 5200, saying: 'Заказ был вчера' });
    assert.equal(verdict.barge, true);
    assert.equal(verdict.rule, 'speech');
});

test('выключатель возвращает поведение до правки', () => {
    // TRAINER_BARGE_ENABLED=0 — аварийный откат без пересборки фронта.
    const verdict = bargeVerdict(
        [{ text: 'ага', is_final: true, confidence: 0.95, end_ms: 5000 }],
        { enabled: false, audibleFromMicMs: null });
    assert.equal(verdict.barge, true);
    assert.equal(verdict.rule, 'off');
});
