/* Вложения чатов Syntony в «Журнале оценок» и «ИИ-оценке» (SZOV-9, 26.11.2026).
 *
 * Ссылка Syntony на файл живёт 5 минут и хранить её нельзя, поэтому в снимке чата лежит
 * метка, а сервер при показе отдаёт адрес НАШЕЙ ручки (/api/syntony_qa/media/…, подписанный
 * на 2–3 часа). Адрес API к нему дописывает экран — сайт и API живут на разных адресах, как в
 * «Чатах водителей» (driver_chats/chatSource.js). Остальные ссылки (Chat2Desk, Wazzup,
 * ChatApp отдают полный адрес своего хранилища) не трогаем, объекты не мутируем.
 *
 * Без React — правила проверяет `node --test` (tests/syntony_qa_media.test.mjs).
 */

export const SYNTONY_QA_MEDIA_PREFIX = '/api/syntony_qa/media/';
const MEDIA_FIELDS = ['photo', 'video', 'audio', 'pdf'];

export function withApiBase(link, apiBaseUrl) {
    if (typeof link !== 'string' || !link.startsWith(SYNTONY_QA_MEDIA_PREFIX)) return link;
    return `${String(apiBaseUrl || '').replace(/\/+$/, '')}${link}`;
}

/* Снимок чата (ответ /api/c2d_eval/snapshots, «Случайный чат») — с полными адресами вложений. */
export function absolutizeSnapshotMedia(snapshot, apiBaseUrl) {
    if (!snapshot || !Array.isArray(snapshot.messages)) return snapshot;
    return {
        ...snapshot,
        messages: snapshot.messages.map((message) => {
            if (!message) return message;
            const next = { ...message };
            MEDIA_FIELDS.forEach((field) => {
                if (next[field]) next[field] = withApiBase(next[field], apiBaseUrl);
            });
            if (Array.isArray(next.attachments)) {
                next.attachments = next.attachments.map((att) => (
                    att && att.link ? { ...att, link: withApiBase(att.link, apiBaseUrl) } : att));
            }
            return next;
        }),
    };
}
