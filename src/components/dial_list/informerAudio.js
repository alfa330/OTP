/*
 * Запись автоинформатора — в браузере руководителя: две записи (казахская, русская) → один WAV,
 * который робот на шлюзе играет в звонок (informer/wav.py проверяет формат на сервере).
 *
 * Порядок — решение владельца 15.10.2026: сначала казахская часть, потом русская.
 * Формат — телефонный: 8 кГц, моно, PCM 16 бит. Тишина 0,5 с в начале (первые доли секунды после
 * ответа теряются в сети), пауза 0,6 с между частями, 0,3 с в хвосте. Громкость частей выравнивается
 * по речи, общий пик не выше −1 dBFS — обе части звучат одинаково громко.
 *
 * Те же шаги проверены живыми звонками 15.10.2026 (скрипт build_informer_track.py): водители
 * дослушивали запись целиком, звук без хрипа. Чистые функции здесь — для тестов
 * (tests/dial_list_informer_audio.test.mjs); браузерное — decode и пересчёт частоты.
 */

export const TARGET_RATE = 8000;
export const LEAD_IN_S = 0.5;
export const GAP_S = 0.6;
export const TAIL_S = 0.3;
export const SILENCE_DB = -45;
export const TARGET_SPEECH_DB = -18;
export const PEAK_LIMIT_DB = -1;
export const MAX_TRACK_S = 5 * 60;
export const PART_ORDER = ['kz', 'ru'];

const WINDOW_S = 0.02;
const dbOf = (rms) => 20 * Math.log10(rms + 1e-12);

/* Окна по 20 мс → уровень каждого в dBFS. */
const frameLevels = (samples, rate) => {
    const win = Math.max(1, Math.round(rate * WINDOW_S));
    const levels = [];
    for (let start = 0; start + win <= samples.length; start += win) {
        let sum = 0;
        for (let i = start; i < start + win; i += 1) sum += samples[i] * samples[i];
        levels.push(dbOf(Math.sqrt(sum / win)));
    }
    return { levels, win };
};

/* Срез тишины по краям: 50 мс запаса до первого звука и 80 мс после последнего. */
export const trimSilence = (samples, rate) => {
    const { levels, win } = frameLevels(samples, rate);
    const first = levels.findIndex((db) => db > SILENCE_DB);
    if (first < 0) return new Float32Array(0);
    let last = levels.length - 1;
    while (last > first && levels[last] <= SILENCE_DB) last -= 1;
    const start = Math.max(0, first * win - Math.round(rate * 0.05));
    const stop = Math.min(samples.length, (last + 1) * win + Math.round(rate * 0.08));
    return samples.slice(start, stop);
};

/* Уровень речи: RMS окон, которые заметно громче тишины. */
export const speechLevelDb = (samples, rate) => {
    const { levels, win } = frameLevels(samples, rate);
    let sum = 0;
    let count = 0;
    levels.forEach((db, idx) => {
        if (db <= SILENCE_DB + 10) return;
        for (let i = idx * win; i < (idx + 1) * win; i += 1) sum += samples[i] * samples[i];
        count += win;
    });
    return count ? dbOf(Math.sqrt(sum / count)) : -Infinity;
};

export const scale = (samples, gainDb) => {
    const k = 10 ** (gainDb / 20);
    const out = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i += 1) out[i] = samples[i] * k;
    return out;
};

export const peakOf = (samples) => {
    let peak = 0;
    for (let i = 0; i < samples.length; i += 1) {
        const v = Math.abs(samples[i]);
        if (v > peak) peak = v;
    }
    return peak;
};

/*
 * Части (уже 8 кГц, моно) → одна дорожка и границы частей в миллисекундах.
 * parts: [{ lang, samples }] в порядке звучания. Громкость: каждая часть к TARGET_SPEECH_DB,
 * потом общий множитель, чтобы пик не превысил PEAK_LIMIT_DB, — уровни частей остаются равны.
 */
export const buildTrack = (parts, rate = TARGET_RATE) => {
    const levelled = parts.map(({ lang, samples }) => {
        const trimmed = trimSilence(samples, rate);
        if (!trimmed.length) throw new Error(`В записи «${lang === 'kz' ? 'казахская' : 'русская'}» нет звука`);
        return { lang, samples: scale(trimmed, TARGET_SPEECH_DB - speechLevelDb(trimmed, rate)) };
    });
    const peak = Math.max(...levelled.map((p) => peakOf(p.samples)));
    const limit = 10 ** (PEAK_LIMIT_DB / 20);
    const common = peak > limit ? limit / peak : 1;

    const silence = (seconds) => Math.round(rate * seconds);
    const total = silence(LEAD_IN_S) + silence(TAIL_S) + silence(GAP_S) * (levelled.length - 1)
        + levelled.reduce((n, p) => n + p.samples.length, 0);
    const out = new Float32Array(total);
    const bounds = [];
    let pos = silence(LEAD_IN_S);
    levelled.forEach((part, idx) => {
        if (idx > 0) pos += silence(GAP_S);
        const start = pos;
        for (let i = 0; i < part.samples.length; i += 1) out[pos + i] = part.samples[i] * common;
        pos += part.samples.length;
        bounds.push({ lang: part.lang, start_ms: Math.round((start * 1000) / rate), end_ms: Math.round((pos * 1000) / rate) });
    });
    const durationMs = Math.floor((total * 1000) / rate);
    if (durationMs > MAX_TRACK_S * 1000) throw new Error('Запись вместе дольше пяти минут — сократите её');
    return { samples: out, parts: bounds, durationMs };
};

/* Float32 [-1, 1] → WAV PCM 16 бит моно. */
export const encodeWav = (samples, rate = TARGET_RATE) => {
    const dataBytes = samples.length * 2;
    const buffer = new ArrayBuffer(44 + dataBytes);
    const view = new DataView(buffer);
    const ascii = (offset, text) => { for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i)); };
    ascii(0, 'RIFF');
    view.setUint32(4, 36 + dataBytes, true);
    ascii(8, 'WAVE');
    ascii(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);         // PCM
    view.setUint16(22, 1, true);         // моно
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    ascii(36, 'data');
    view.setUint32(40, dataBytes, true);
    for (let i = 0; i < samples.length; i += 1) {
        const v = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(44 + i * 2, v < 0 ? Math.round(v * 0x8000) : Math.round(v * 0x7fff), true);
    }
    return new Uint8Array(buffer);
};

/* ── браузер ───────────────────────────────────────────────────────────────── */

/* Файл → моно 8 кГц. Пересчёт частоты — OfflineAudioContext: у браузера свой фильтр от наложения
   частот, и он лучше, чем всё, что уместилось бы здесь. */
export const decodeToPhoneRate = async (file) => {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const OfflineCtx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!AudioCtx || !OfflineCtx) throw new Error('Браузер не умеет читать аудио — откройте раздел в Chrome');
    const bytes = await file.arrayBuffer();
    const ctx = new AudioCtx();
    let decoded;
    try {
        decoded = await ctx.decodeAudioData(bytes.slice(0));
    } catch {
        throw new Error(`Не удалось прочитать «${file.name}» — нужен MP3, WAV или M4A`);
    } finally {
        ctx.close?.();
    }
    if (decoded.duration > MAX_TRACK_S) throw new Error(`«${file.name}» длиннее пяти минут`);
    const offline = new OfflineCtx(1, Math.ceil(decoded.duration * TARGET_RATE), TARGET_RATE);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    const rendered = await offline.startRendering();
    return rendered.getChannelData(0).slice(0);
};

/* { kz: File, ru: File } → { blob, parts, durationMs } — то, что уходит на сервер. */
export const prepareInformerTrack = async (files) => {
    const parts = [];
    for (const lang of PART_ORDER) {
        if (!files[lang]) throw new Error(lang === 'kz' ? 'Добавьте казахскую запись' : 'Добавьте русскую запись');
        parts.push({ lang, samples: await decodeToPhoneRate(files[lang]) });
    }
    const track = buildTrack(parts, TARGET_RATE);
    return {
        blob: new Blob([encodeWav(track.samples, TARGET_RATE)], { type: 'audio/wav' }),
        parts: track.parts,
        durationMs: track.durationMs,
    };
};
