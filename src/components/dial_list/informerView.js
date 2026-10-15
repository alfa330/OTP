/*
 * Подписи и расчёты вкладки «Автоинформатор» без React — их проверяют тесты
 * (tests/dial_list_informer_view.test.mjs). Двойники серверных — informer/rules.py.
 */

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
    failed: 'Ошибка АТС',
    lost: 'Итог не пришёл',
    '': 'Звоним',
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
