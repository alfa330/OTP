/*
 * «Автоинформатор» во вкладках «Удаленного КЦ» (владелец, 15.10.2026): склейка записи в браузере
 * (src/components/dial_list/informerAudio.js) и подписи вкладки (informerView.js).
 *
 * Что защищаем:
 *   • порядок частей — сначала казахская, потом русская (решение владельца), и границы частей,
 *     по которым сервер считает «на какой части положил трубку»;
 *   • WAV ровно того формата, что принимает сервер (informer/wav.py) и играет робот: 8 кГц, моно, PCM16;
 *   • громкость частей выравнивается, пик не выше −1 dBFS, тишина по краям срезается;
 *   • «где положил трубку» в интерфейсе совпадает с сервером (informer/rules.py: listen_stage);
 *   • потолок темпа в форме — тот же, что закон в коде сервера (10 в минуту);
 *   • вкладка есть только у удалённого КЦ и ходит в свои ручки отдела.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    GAP_S, LEAD_IN_S, PART_ORDER, PEAK_LIMIT_DB, TAIL_S, TARGET_RATE, buildTrack, encodeWav, peakOf, speechLevelDb,
    trimSilence,
} from '../src/components/dial_list/informerAudio.js';
import {
    campaignActions, partsLabel, seconds, speechEstimate, stageLabel, stageShares,
} from '../src/components/dial_list/informerView.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').split('\r\n').join('\n');

/* Синус нужной громкости и длины с тишиной по краям — «речь». */
const tone = (seconds_, amp, padS = 0.3, rate = TARGET_RATE) => {
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
    // Тишина по краям исходников срезана: часть начинается почти сразу после вступления.
    assert.ok(Math.abs(kz.start_ms - LEAD_IN_S * 1000) <= 1);
    assert.ok(kz.end_ms - kz.start_ms >= 12000 && kz.end_ms - kz.start_ms <= 12200);
    assert.ok(Math.abs(ru.start_ms - kz.end_ms - GAP_S * 1000) <= 1);
    assert.ok(Math.abs(track.durationMs - (ru.end_ms + TAIL_S * 1000)) <= 1);
});

test('громкость частей выравнивается, пик не выше −1 dBFS', () => {
    const track = buildTrack([{ lang: 'kz', samples: tone(3, 0.9) }, { lang: 'ru', samples: tone(3, 0.05) }]);
    const rate = TARGET_RATE;
    const kz = track.samples.slice(Math.round((track.parts[0].start_ms * rate) / 1000), Math.round((track.parts[0].end_ms * rate) / 1000));
    const ru = track.samples.slice(Math.round((track.parts[1].start_ms * rate) / 1000), Math.round((track.parts[1].end_ms * rate) / 1000));
    assert.ok(Math.abs(speechLevelDb(kz, rate) - speechLevelDb(ru, rate)) < 0.5);
    assert.ok(20 * Math.log10(peakOf(track.samples)) <= PEAK_LIMIT_DB + 0.01);
});

test('пустая часть — понятный отказ, а не немая рассылка', () => {
    assert.throws(() => buildTrack([{ lang: 'kz', samples: new Float32Array(8000) }, { lang: 'ru', samples: tone(1, 0.5) }]),
        /казахская/);
    assert.equal(trimSilence(new Float32Array(800), TARGET_RATE).length, 0);
});

test('WAV — PCM 16 бит, моно, 8 кГц: ровно то, что принимает сервер', () => {
    const wav = encodeWav(new Float32Array([0, 1, -1, 0.5]), TARGET_RATE);
    const view = new DataView(wav.buffer);
    const ascii = (o, n) => String.fromCharCode(...wav.slice(o, o + n));
    assert.equal(ascii(0, 4), 'RIFF');
    assert.equal(ascii(8, 4), 'WAVE');
    assert.equal(view.getUint16(20, true), 1);      // PCM
    assert.equal(view.getUint16(22, true), 1);      // моно
    assert.equal(view.getUint32(24, true), 8000);
    assert.equal(view.getUint16(34, true), 16);
    assert.equal(view.getUint32(40, true), 8);      // 4 отсчёта × 2 байта
    assert.equal(view.getInt16(46, true), 32767);
    assert.equal(view.getInt16(48, true), -32768);
    const server = read('../informer/wav.py');
    assert.match(server, /SAMPLE_RATE = 8000/);
    assert.match(server, /CHANNELS = 1/);
    assert.match(server, /BITS = 16/);
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

test('потолок темпа в форме — закон в коде сервера', () => {
    const panel = read('../src/components/dial_list/DialListInformerPanel.jsx');
    assert.match(panel, /type="number" min=\{1\} max=\{10\}/);
    assert.match(read('../informer/rules.py'), /MAX_PER_MINUTE_LAW = 10/);
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
