/* Раздел «Тренажёр»: правила показа замеров.
 *
 * Вынесено из разметки и покрыто тестом по правилу проекта: поведение, решённое
 * внутри JSX, не проверяется ничем. Здесь только чистые функции — ни запросов,
 * ни состояния.
 */

/** Роль реплики → подпись. Зависит от режима: в «наставнике» говорящие другие. */
export const roleLabel = (role) => ({
    trainee: 'Стажёр',
    driver: 'Водитель',
    asker: 'Вы',
    mentor: 'Наставник',
}[role] || role);

/** Реплики человека и реплики ИИ красятся по-разному — но только они две. */
export const roleSide = (role) => (role === 'trainee' || role === 'asker' ? 'human' : 'ai');

/**
 * Тон паузы до ответа.
 *
 * Пороги взяты не с потолка: до 2 с разговор ощущается живым, к 4 с появляется
 * заметная задержка, но для тренажёра она терпима (живой водитель на линии тоже
 * думает), а дальше диалог разваливается. Красим только то, что требует
 * внимания: «хорошо» остаётся нейтральным, иначе журнал превращается в светофор.
 */
export const paceTone = (ms) => {
    if (ms == null) return 'muted';
    if (ms <= 2000) return 'good';
    if (ms <= 4000) return 'warn';
    return 'bad';
};

/** Секунды с одним знаком: «2,9 с». Миллисекунды человеку не нужны. */
export const fmtMs = (ms) => (ms == null ? '—' : `${(ms / 1000).toFixed(1).replace('.', ',')} с`);

/** Длительность разговора: «3 мин 12 с», без ведущего нуля у минут. */
export const fmtDuration = (ms) => {
    if (!ms && ms !== 0) return '—';
    const total = Math.round(ms / 1000);
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return minutes ? `${minutes} мин ${seconds} с` : `${seconds} с`;
};

/**
 * Стоимость прогона. Доли цента показываем четырьмя знаками: на этих объёмах
 * округление до цента превратило бы весь журнал в «$0,00».
 */
export const fmtCost = (usd) => {
    if (usd == null) return '—';
    if (usd === 0) return '$0';
    return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
};

/** Языковой состав реплики: {ru: 12, kk: 3} → «ru 80% · kk 20%». */
export const fmtLangs = (langs) => {
    if (!langs || typeof langs !== 'object') return null;
    const entries = Object.entries(langs).filter(([, count]) => count > 0);
    if (!entries.length) return null;
    const total = entries.reduce((sum, [, count]) => sum + count, 0);
    return entries
        .sort((a, b) => b[1] - a[1])
        .map(([code, count]) => `${code} ${Math.round((count / total) * 100)}%`)
        .join(' · ');
};

/**
 * Сводка по репликам для шапки разговора.
 *
 * Медиана, а не среднее: одна долгая реплика (например, когда провайдер
 * подменялся) утаскивает среднее и создаёт впечатление, что тормозит всё.
 */
export const summarize = (turns = []) => {
    const paces = turns.map((t) => t.pace_ms ?? t.voice_to_voice_ms)
        .filter((value) => typeof value === 'number');
    const sorted = [...paces].sort((a, b) => a - b);
    const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : null;
    const llm = turns.map((t) => t.llm?.total_ms).filter((v) => typeof v === 'number');
    const tts = turns.map((t) => t.tts?.ttfb_ms).filter((v) => typeof v === 'number');
    // Доля дослушанного. Синтезировано ≠ услышано, и это главная цифра раздела
    // после правки 22.08.2026: до неё пятая часть реплик собеседника не звучала
    // вовсе, и отличить их от прозвучавших было нечем.
    const heard = turns
        .map((t) => [t.spoken?.ms, t.total_ms ?? t.tts?.audio_ms])
        .filter(([ms, total]) => typeof ms === 'number' && total > 0);
    return {
        turns: turns.length,
        pace: median,
        paceWorst: sorted.length ? sorted[sorted.length - 1] : null,
        llm: llm.length ? Math.round(llm.reduce((a, b) => a + b, 0) / llm.length) : null,
        tts: tts.length ? Math.round(tts.reduce((a, b) => a + b, 0) / tts.length) : null,
        barge: turns.filter((t) => t.barge_in).length,
        heard: heard.length
            ? Math.round(heard.reduce((sum, [ms, total]) => sum + Math.min(1, ms / total), 0)
                / heard.length * 100)
            : null,
    };
};

/**
 * Применяет события речи собеседника к ленте реплик.
 *
 * Живёт здесь, а не в JSX, по правилу проекта: поведение, решённое внутри
 * разметки, ничем не проверяется. Здесь оно чистое и покрыто тестом.
 *
 * Что за события:
 *   speech_start — пошёл первый сэмпл; до него на экране пусто, а не весь текст;
 *   said         — открылось столько-то знаков (это и есть «стриминг»);
 *   speech_end   — реплика кончилась; cut = оборвана на полуслове;
 *   barge        — человек перебил, дальше текста не будет.
 *
 * `shown` — сколько текста показывать. null означает «показать целиком»: так
 * ведёт себя реплика, у которой озвучка отказала вовсе, — текст человек всё
 * равно должен увидеть.
 */
export const applySpeech = (turns = [], type, payload = {}) => {
    const id = payload.turn_id;
    if (id == null) return turns;
    const patch = {
        speech_start: () => ({ shown: '' }),
        said: () => ({ shown: payload.text ?? '' }),
        speech_end: () => ({
            shown: payload.cut ? undefined : null,
            spoken: { ms: payload.spoken_ms, chars: payload.spoken_chars, cut: !!payload.cut },
            total_ms: payload.total_ms,
        }),
        barge: () => ({
            barge_in: true,
            spoken: { ms: payload.spoken_ms, chars: payload.spoken_chars, cut: true },
        }),
    }[type];
    if (!patch) return turns;
    const next = patch();
    return turns.map((turn) => {
        if (turn.id !== id) return turn;
        const merged = { ...turn };
        Object.entries(next).forEach(([key, value]) => {
            if (value !== undefined) merged[key] = value;
        });
        return merged;
    });
};

/**
 * Склейка замеров двух реплик человека, сказанных подряд.
 *
 * Просто перекрыть первую второй нельзя: пропали бы stt_audio_ms (он идёт в
 * стоимость распознавания), число токенов и — главное — отметка перебивания,
 * из-за чего подтверждённое перебивание не попадало бы ни в реплику, ни в счёт
 * сессии. Длительности складываем, флаги берём по «хоть где-то было».
 */
export const mergeMetrics = (first = {}, second = {}) => ({
    ...first,
    ...second,
    stt_audio_ms: (first.stt_audio_ms || 0) + (second.stt_audio_ms || 0) || null,
    stt_tokens: (first.stt_tokens || 0) + (second.stt_tokens || 0) || null,
    hold_ms: second.hold_ms ?? first.hold_ms ?? null,
    barge_in: !!(first.barge_in || second.barge_in),
    prev: second.prev ?? first.prev ?? null,
});

/** Итог сессии одной строкой для журнала. */
export const statusLabel = (status) => ({
    active: 'идёт',
    finished: 'завершён',
    error: 'с ошибкой',
    abandoned: 'брошен',
}[status] || status);
