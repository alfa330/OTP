/* Текст голосовых в ленте «Чатов ОП» (wazzup/voice.py). Чистые правила хука
 * useVoiceTranscripts: о каких голосовых спросить сервер, какие ещё ждать и как
 * сложить ответ. Статусы — как на сервере; 'none' — у сообщения текста не будет
 * (не голосовое этого чата, расшифровка выключена, просить больше нельзя). */

export const VOICE_STATUSES = new Set(['pending', 'ready', 'no_speech', 'failed', 'unavailable', 'skipped']);

// Опрос ждущих: первые ~2 минуты часто (обычно текст готов за секунды), дальше —
// раз в 30 с, пока чат открыт. Повторы сервера идут через 20 с, 2 и 10 мин, а
// очередь при наплыве старых голосовых может стоять дольше, поэтому предел —
// ~27 минут; каждый пришедший ответ начинает отсчёт заново. Не дождались — строка
// «Расшифровываем…» убирается: вечный индикатор хуже, чем ничего.
export const FAST_ROUNDS = 20;
export const POLL_ROUNDS = FAST_ROUNDS + 50;
export const pollDelay = (round) => (round < FAST_ROUNDS ? Math.min(2000 + round * 500, 8000) : 30000);
// Столько голосовых сервер принимает за раз (wazzup/voice.py, MAX_IDS); длинная
// лента спрашивается порциями, начиная со свежих.
export const VOICE_BATCH = 200;

export const voiceTranscriptKey = (account, chat) => (chat?.channelId && chat?.chatId && !chat.isNew
    ? JSON.stringify([account || 'op', chat.channelId, chat.chatId]) : '');

/* Голосовые ленты: своё неподтверждённое (msg.local) — не архивное, у него текста нет. */
export const voiceMessageIds = (thread) => (thread || [])
    .filter((message) => message?.type === 'audio' && message.contentUri && message.messageId && !message.local)
    .map((message) => message.messageId);

export const unknownIds = (ids, known) => ids.filter((id) => !known[id]);
export const pendingIds = (ids, known) => ids.filter((id) => known[id]?.status === 'pending');

/* Ответ сервера поверх известного. Спрошенное, но не вернувшееся — 'none':
 * повторно о нём не спрашиваем. */
export const mergeVoiceTranscripts = (known, asked, items) => {
    const next = { ...known };
    const returned = new Set();
    for (const item of Array.isArray(items) ? items : []) {
        if (!item?.messageId || !VOICE_STATUSES.has(item.status)) continue;
        returned.add(item.messageId);
        next[item.messageId] = { status: item.status, text: typeof item.text === 'string' ? item.text : '' };
    }
    for (const id of asked) if (!returned.has(id)) next[id] = { status: 'none', text: '' };
    return next;
};
