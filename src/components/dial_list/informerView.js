/*
 * Подписи и расчёты вкладки «Автоинформатор» без React — их проверяют тесты
 * (tests/dial_list_informer.test.mjs). Двойники серверных — informer/rules.py.
 */
import { GAP_S, LEAD_IN_S, TAIL_S } from './informerAudio.js';

/* Сколько звонков в минуту вытянет робот — двойник informer/rules.py (line_capacity, line_rate).
   Одновременно — не больше MAX_ACTIVE_CALLS звонков (владелец, 16.10.2026, после того как залп 46 в минуту с общего
   номера кабинета уронил исходящие всем операторам: «жёстких залпов делать не надо вообще, по 3 максимум»; вечером
   того же дня поднял до 10, 15, 20: «давай на 10 увеличим», «ещё до 15», «до 20»). Место звонок держит на время вызова (~12 с до ответа),
   записи и отбоя (~2 с). */
export const CALLS_PER_LINE = 2;
// 20.10.2026: владелец отключил пару линий робота — «максимально звонков поставить на 10, а то у других ошибки выходят».
export const MAX_ACTIVE_CALLS = 10;
export const EXPECTED_RING_S = 12;
export const CALL_OVERHEAD_S = 2;
export const DEFAULT_AUDIO_S = 25;
export const MAX_PER_MINUTE = 600;

export const plural = (n, one, few, many) => {
    const mod100 = Math.abs(n) % 100;
    const mod10 = mod100 % 10;
    if (mod100 > 10 && mod100 < 20) return many;
    if (mod10 === 1) return one;
    if (mod10 > 1 && mod10 < 5) return few;
    return many;
};

const count = (value) => Math.max(0, Math.trunc(Number(value) || 0));

export const lineCapacity = (lines) => Object.values(lines || {}).reduce((sum, info) => sum + count(info?.capacity), 0);

export const callSeconds = (audioMs) => EXPECTED_RING_S + (audioMs > 0 ? audioMs / 1000 : DEFAULT_AUDIO_S)
    + CALL_OVERHEAD_S;

export const lineRate = (channels, audioMs) => {
    const ch = Math.min(count(channels) || CALLS_PER_LINE, MAX_ACTIVE_CALLS);   // линий ещё не видно — как одна линия
    return Math.max(1, Math.min(MAX_PER_MINUTE, Math.floor((ch * 60) / callSeconds(audioMs))));
};

/* Длина будущей записи по текстам, пока озвучки нет: вступление, пауза и хвост склейки плюс ~13 знаков
   речи в секунду (как speechEstimate). Нет текста — 0: темп считается по обычной длине записи. */
export const trackEstimateMs = (texts = {}) => {
    const chars = String(texts.kz || '').trim().length + String(texts.ru || '').trim().length;
    return chars ? Math.round((LEAD_IN_S + GAP_S + TAIL_S + chars / 13) * 1000) : 0;
};

/* Подпись робота: «9 линий · сейчас 0 из 18 звонков». */
export const robotSummary = (lines) => {
    const items = Object.values(lines || {});
    if (!items.length) return '';
    const capacity = lineCapacity(lines);
    const active = items.reduce((sum, info) => sum + count(info?.active), 0);
    return `${items.length} ${plural(items.length, 'линия', 'линии', 'линий')} · сейчас ${active} из ${capacity} `
        + plural(capacity, 'звонка', 'звонков', 'звонков');
};

export const STATUS = {
    draft: { label: 'Черновик', tone: 'slate' },
    active: { label: 'Идёт', tone: 'green' },
    paused: { label: 'На паузе', tone: 'amber' },
    done: { label: 'Завершена', tone: 'blue' },
};

export const RESULT_LABEL = {
    answered: 'Дозвонились',
    no_answer: 'Не ответил',
    busy: 'Занято',
    unavailable: 'Недоступен',
    rejected: 'Сбросил',
    line_busy: 'Линия занята',
    no_link: 'Сеть не пропустила',
    failed: 'Сбой АТС',
    lost: 'Итог не пришёл',
    '': 'Звоним',
};

/* «Почему не дозвонились»: итоги последней настоящей попытки водителей, до которых ещё не дозвонились
   (сервер: analytics.unreached) → строки для баров, самые частые сверху, доля — от всех недозвонов. */
export const unreachedRows = (unreached = {}) => {
    const rows = Object.entries(unreached)
        .map(([key, count]) => ({ key, label: RESULT_LABEL[key] || key, count: Number(count) || 0 }))
        .filter((r) => r.count > 0)
        .sort((a, b) => b.count - a.count);
    const total = rows.reduce((sum, r) => sum + r.count, 0);
    return { total, rows: rows.map((r) => ({ ...r, share: total ? r.count / total : 0 })) };
};

export const STATE_LABEL = {
    pending: 'В очереди',
    calling: 'Звоним',
    done: 'Дозвонились',
    given_up: 'Не дозвонились',
    stopped: 'Не звонили — рассылку завершили',
};

export const LANG_LABEL = { kz: 'казахская', ru: 'русская' };

export const VOICE_SPEEDS = [
    { value: 'slow', label: 'Спокойнее' },
    { value: 'normal', label: 'Обычно' },
    { value: 'fast', label: 'Быстрее' },
];

/* «на какой части положил трубку» — как informer.rules.listen_stage на сервере. */
export const stageLabel = (stage) => {
    if (stage === 'full') return 'дослушал до конца';
    if (stage === 'none' || !stage) return '—';
    return `положил на ${LANG_LABEL[stage] === 'казахская' ? 'казахской' : 'русской'} части`;
};

/* 12 345 мс → «12,3 с»; null → «—». */
export const seconds = (ms) => {
    if (ms == null || Number.isNaN(Number(ms))) return '—';
    const value = Math.round(Number(ms) / 100) / 10;
    return `${String(value).replace('.', ',')} с`;
};

export const percent = (part, total) => (total ? Math.round((part / total) * 100) : null);

/* Доли «где положили трубку» среди дозвонившихся — для полосы: казахская, русская, до конца. */
export const stageShares = (stages = {}) => {
    const kz = Number(stages.kz || 0);
    const ru = Number(stages.ru || 0);
    const full = Number(stages.full || 0);
    const total = kz + ru + full;
    return {
        total,
        parts: [
            { key: 'kz', label: 'Положили на казахской', count: kz, share: total ? kz / total : 0, tone: 'bg-amber-400' },
            { key: 'ru', label: 'Положили на русской', count: ru, share: total ? ru / total : 0, tone: 'bg-sky-400' },
            { key: 'full', label: 'Дослушали до конца', count: full, share: total ? full / total : 0, tone: 'bg-emerald-500' },
        ],
    };
};

/* Сколько примерно будет звучать текст: ~13 знаков в секунду живой речи (замер Soniox 15.10.2026:
   130 знаков казахского — 10,6 с). Подсказка у поля, не обещание. */
export const speechEstimate = (text) => {
    const chars = String(text || '').trim().length;
    if (!chars) return '';
    const sec = Math.max(1, Math.round(chars / 13));
    return `≈ ${sec} с`;
};

/* Пока рассылку можно запустить, поставить на паузу или завершить — какие кнопки показать. */
export const campaignActions = (status) => ({
    start: status === 'draft' || status === 'paused',
    pause: status === 'active',
    finish: status === 'draft' || status === 'active' || status === 'paused',
});

/* Время звонка водителя для таблицы: «15.10, 14:05». */
export const shortDateTime = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

/* Части записи для подписи: «казахская 0,5–13,1 с · русская 13,7–25,2 с». */
export const partsLabel = (parts = []) => parts
    .map((p) => `${LANG_LABEL[p.lang] || p.lang} ${seconds(p.start_ms).replace(' с', '')}–${seconds(p.end_ms)}`)
    .join(' · ');
