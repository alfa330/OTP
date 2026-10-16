/*
 * «Автоинформатор» во вкладках «Удаленного КЦ» (владелец, 15.10.2026): склейка записи в браузере
 * (src/components/dial_list/informerAudio.js) и подписи вкладки (informerView.js).
 *
 * Что защищаем:
 *   • порядок частей — сначала казахская, потом русская (решение владельца), и границы частей,
 *     по которым сервер считает «на какой части положил трубку»;
 *   • WAV-оригинал ровно того формата, что ждёт сервер (informer/wav.py): 24 кГц, моно, PCM16 — полное
 *     качество голоса (владелец, 15.10.2026); телефонную версию для робота сервер сводит сам;
 *   • громкость частей выравнивается, пик не выше −1 dBFS, тишина по краям срезается;
 *   • «где положил трубку» в интерфейсе совпадает с сервером (informer/rules.py: listen_stage);
 *   • темп в форме сразу — сколько тянут линии робота, той же формулой, что сервер (rules.line_rate;
 *     владелец, 16.10.2026: «без предела»);
 *   • вкладка есть только у удалённого КЦ и ходит в свои ручки отдела.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    GAP_S, LEAD_IN_S, MASTER_RATE, PART_ORDER, PEAK_LIMIT_DB, TAIL_S, buildTrack, encodeWav, mixToMono, peakOf,
    speechLevelDb, trimSilence,
} from '../src/components/dial_list/informerAudio.js';
import {
    CALLS_PER_LINE, CALL_OVERHEAD_S, DEFAULT_AUDIO_S, EXPECTED_RING_S, MAX_PER_MINUTE, RESULT_LABEL, campaignActions,
    lineCapacity,
    lineRate, partsLabel, plural, robotSummary, seconds, speechEstimate, stageLabel, stageShares, trackEstimateMs,
} from '../src/components/dial_list/informerView.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').split('\r\n').join('\n');

/* Синус нужной громкости и длины с тишиной по краям — «речь». */
const tone = (seconds_, amp, padS = 0.3, rate = MASTER_RATE) => {
    const pad = Math.round(rate * padS);
    const n = Math.round(rate * seconds_);
    const out = new Float32Array(pad * 2 + n);
    for (let i = 0; i < n; i += 1) out[pad + i] = amp * Math.sin((2 * Math.PI * 440 * i) / rate);
    return out;
};

test('порядок частей — казахская первой, и сервер ждёт тот же порядок', () => {
    assert.deepEqual(PART_ORDER, ['kz', 'ru']);
    assert.match(read('../informer/rules.py'), /PARTS_ORDER = \('kz', 'ru'\)/);
});

test('склейка: тишина, казахская, пауза, русская, хвост — и границы частей', () => {
    const track = buildTrack([{ lang: 'kz', samples: tone(12, 0.8) }, { lang: 'ru', samples: tone(11, 0.1) }]);
    const [kz, ru] = track.parts;
    assert.equal(kz.lang, 'kz');
    assert.equal(ru.lang, 'ru');
    // Владелец, 16.10.2026: «на начало аудио 2 секунды паузы» — первые 2 с записи тишина.
    assert.equal(LEAD_IN_S, 2);
    assert.ok(track.samples.slice(0, 2 * MASTER_RATE).every((v) => v === 0));
    // Тишина по краям исходников срезана: часть начинается почти сразу после вступления.
    assert.ok(Math.abs(kz.start_ms - LEAD_IN_S * 1000) <= 1);
    assert.ok(kz.end_ms - kz.start_ms >= 12000 && kz.end_ms - kz.start_ms <= 12200);
    assert.ok(Math.abs(ru.start_ms - kz.end_ms - GAP_S * 1000) <= 1);
    assert.ok(Math.abs(track.durationMs - (ru.end_ms + TAIL_S * 1000)) <= 1);
});

test('громкость частей выравнивается, пик не выше −1 dBFS', () => {
    const track = buildTrack([{ lang: 'kz', samples: tone(3, 0.9) }, { lang: 'ru', samples: tone(3, 0.05) }]);
    const rate = MASTER_RATE;
    const kz = track.samples.slice(Math.round((track.parts[0].start_ms * rate) / 1000), Math.round((track.parts[0].end_ms * rate) / 1000));
    const ru = track.samples.slice(Math.round((track.parts[1].start_ms * rate) / 1000), Math.round((track.parts[1].end_ms * rate) / 1000));
    assert.ok(Math.abs(speechLevelDb(kz, rate) - speechLevelDb(ru, rate)) < 0.5);
    assert.ok(20 * Math.log10(peakOf(track.samples)) <= PEAK_LIMIT_DB + 0.01);
});

test('пустая часть — понятный отказ, а не немая рассылка', () => {
    assert.throws(() => buildTrack([{ lang: 'kz', samples: new Float32Array(8000) }, { lang: 'ru', samples: tone(1, 0.5) }]),
        /казахская/);
    assert.equal(trimSilence(new Float32Array(800), MASTER_RATE).length, 0);
});

test('стерео → моно средним: ни один канал не пропадает', () => {
    const left = new Float32Array([1, 0, 0.5]);
    const right = new Float32Array([0, 1, 0.5]);
    const stereo = { numberOfChannels: 2, length: 3, getChannelData: (c) => (c === 0 ? left : right) };
    assert.deepEqual([...mixToMono(stereo)], [0.5, 0.5, 0.5]);
    const mono = { numberOfChannels: 1, length: 3, getChannelData: () => left };
    const copy = mixToMono(mono);
    assert.deepEqual([...copy], [...left]);
    assert.notEqual(copy, left);                    // копия: буфер декодера не держим
});

test('WAV — PCM 16 бит, моно, 24 кГц: оригинал, который принимает сервер', () => {
    const wav = encodeWav(new Float32Array([0, 1, -1, 0.5]), MASTER_RATE);
    const view = new DataView(wav.buffer);
    const ascii = (o, n) => String.fromCharCode(...wav.slice(o, o + n));
    assert.equal(ascii(0, 4), 'RIFF');
    assert.equal(ascii(8, 4), 'WAVE');
    assert.equal(view.getUint16(20, true), 1);      // PCM
    assert.equal(view.getUint16(22, true), 1);      // моно
    assert.equal(view.getUint32(24, true), 24000);
    assert.equal(view.getUint32(28, true), 48000);  // байт в секунду
    assert.equal(view.getUint16(34, true), 16);
    assert.equal(view.getUint32(40, true), 8);      // 4 отсчёта × 2 байта
    assert.equal(view.getInt16(46, true), 32767);
    assert.equal(view.getInt16(48, true), -32768);
    const server = read('../informer/wav.py');
    assert.match(server, new RegExp(`MASTER_RATE = ${MASTER_RATE}\\n`));
    assert.match(server, /PHONE_RATE = 8000/);
    assert.match(server, /CHANNELS = 1/);
    assert.match(server, /BITS = 16/);
});

test('итоги звонков подписаны так же, как на сервере (и «Нет связи с Binotel»)', () => {
    const rules = read('../informer/rules.py');
    const server = Object.fromEntries([...rules.matchAll(/RESULT_([A-Z_]+) = '([a-z_]+)'/g)].map((m) => [m[1], m[2]]));
    for (const [name, code] of Object.entries(server)) {
        const label = rules.match(new RegExp(`RESULT_${name}: '([^']+)'`));
        if (label) assert.equal(RESULT_LABEL[code], label[1], `подпись итога ${code}`);
    }
    assert.equal(RESULT_LABEL.no_link, 'Нет связи с Binotel');
});

test('«где положил трубку» — как на сервере', () => {
    assert.equal(stageLabel('kz'), 'положил на казахской части');
    assert.equal(stageLabel('ru'), 'положил на русской части');
    assert.equal(stageLabel('full'), 'дослушал до конца');
    assert.equal(stageLabel('none'), '—');
    const shares = stageShares({ kz: 1, ru: 1, full: 2 });
    assert.equal(shares.total, 4);
    assert.deepEqual(shares.parts.map((p) => p.share), [0.25, 0.25, 0.5]);
    assert.equal(stageShares({}).total, 0);
});

test('подписи: секунды, части, оценка длительности, кнопки по статусу', () => {
    assert.equal(seconds(25520), '25,5 с');
    assert.equal(seconds(null), '—');
    assert.equal(partsLabel([{ lang: 'kz', start_ms: 500, end_ms: 13050 }, { lang: 'ru', start_ms: 13650, end_ms: 25220 }]),
        'казахская 0,5–13,1 с · русская 13,7–25,2 с');
    assert.equal(speechEstimate(''), '');
    assert.equal(speechEstimate('x'.repeat(130)), '≈ 10 с');
    assert.deepEqual(campaignActions('draft'), { start: true, pause: false, finish: true });
    assert.deepEqual(campaignActions('active'), { start: false, pause: true, finish: true });
    assert.deepEqual(campaignActions('done'), { start: false, pause: false, finish: false });
});

test('темп в форме — сколько тянут линии робота, как на сервере', () => {
    const nine = Object.fromEntries([902, 907, 919, 923, 925, 928, 939, 942, 944]
        .map((n) => [String(n), { capacity: 2, active: 0 }]));
    assert.equal(lineCapacity(nine), 18);
    assert.equal(lineCapacity({ 939: { capacity: 'x' }, 940: null, 941: { capacity: 2 } }), 2);
    // Те же числа, что в tests/test_informer_rules.py: test_line_rate_is_what_robot_lines_fit.
    assert.equal(lineRate(18, 25520), 27);
    assert.equal(lineRate(18, 8120), 48);
    assert.equal(lineRate(18, 0), 27);
    assert.equal(lineRate(0, 25520), 3);
    assert.equal(lineRate(2, 300000), 1);
    assert.equal(lineRate(10000, 1000), MAX_PER_MINUTE);
    // Длина записи по текстам: склейка 2,9 с (пауза 2 с в начале) + ~13 знаков в секунду; без текста — обычная.
    assert.equal(trackEstimateMs({ kz: 'x'.repeat(130), ru: 'y'.repeat(130) }), 22900);
    assert.equal(trackEstimateMs({ kz: '  ', ru: '' }), 0);
    // Двойник: постоянные совпадают с informer/rules.py.
    const rules = read('../informer/rules.py');
    assert.match(rules, new RegExp(`CALLS_PER_LINE = ${CALLS_PER_LINE}\\n`));
    assert.match(rules, new RegExp(`EXPECTED_RING_SECONDS = ${EXPECTED_RING_S}\\n`));
    assert.match(rules, new RegExp(`CALL_OVERHEAD_SECONDS = ${CALL_OVERHEAD_S}\\n`));
    assert.match(rules, new RegExp(`DEFAULT_AUDIO_SECONDS = ${DEFAULT_AUDIO_S} `));
    assert.match(rules, new RegExp(`MAX_PER_MINUTE = ${MAX_PER_MINUTE} `));
    assert.doesNotMatch(rules, /MAX_PER_MINUTE_LAW/);
    // Поле без потолка закона: верх — проверка ввода, значение — расчёт по линиям.
    const panel = read('../src/components/dial_list/DialListInformerPanel.jsx');
    assert.match(panel, /type="number" min=\{1\} max=\{MAX_PER_MINUTE\}/);
    assert.match(panel, /value=\{perMinuteValue\}/);
    assert.doesNotMatch(panel, /max=\{10\}/);
});

test('робот: сводка линий и склонения', () => {
    const nine = Object.fromEntries([902, 907, 919, 923, 925, 928, 939, 942, 944]
        .map((n, i) => [String(n), { capacity: 2, active: i < 3 ? 1 : 0 }]));
    assert.equal(robotSummary(nine), '9 линий · сейчас 3 из 18 звонков');
    assert.equal(robotSummary({ 939: { capacity: 2, active: 0 } }), '1 линия · сейчас 0 из 2 звонков');
    assert.equal(robotSummary({}), '');
    assert.deepEqual([1, 2, 5, 11, 21, 22, 25, 111].map((n) => plural(n, 'линия', 'линии', 'линий')),
        ['линия', 'линии', 'линий', 'линий', 'линия', 'линии', 'линий', 'линий']);
});

test('каждая иконка вкладки есть в наборе сайта (иначе вместо неё пустой кружок)', () => {
    const icons = read('../src/components/common/FaIcon.jsx');
    const used = new Set();
    for (const file of ['../src/components/dial_list/DialListInformerPanel.jsx']) {
        for (const m of read(file).matchAll(/fas (fa-[a-z0-9-]+)/g)) used.add(m[1]);
    }
    const tab = read('../src/components/dial_list/DialListView.jsx').match(/value: 'informer'.*?fas (fa-[a-z0-9-]+)/);
    used.add(tab[1]);
    const missing = [...used].filter((name) => !['fa-spin'].includes(name) && !icons.includes(`'${name}'`));
    assert.deepEqual(missing, []);
});

test('вкладка — только у удалённого КЦ и ходит в свои ручки отдела', () => {
    const view = read('../src/components/dial_list/DialListView.jsx');
    assert.match(view, /\.filter\(\(t\) => !tez \|\| t\.value !== 'informer'\)/);
    assert.match(view, /tab === 'informer' && !tez/);
    const panel = read('../src/components/dial_list/DialListInformerPanel.jsx');
    assert.match(panel, /\/api\/dial_list\/departments\/\$\{departmentId\}\/informer\$\{path\}/);
    // Иконки пунктов меню — компоненты lucide, а не строки: строка роняет страницу (React #130).
    assert.doesNotMatch(panel, /icon: 'fas /);
});
