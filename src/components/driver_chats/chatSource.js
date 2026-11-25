/* Что на экране «Чатов водителей» зависит от источника переписки (25.11.2026).
 *
 * С 16–23.11.2026 чаты СЗоВ — в Syntony, и раздел читает их оттуда (driver_chats/syntony.py).
 * Отличий для экрана три:
 *
 * * вложения приходят адресом НАШЕЙ ручки (/api/driver_chats/media/…, подписанным): адрес
 *   API к нему дописывает экран — сайт и API живут на разных адресах;
 * * водитель у Syntony — номер, а не клиент Chat2Desk (client_id пуст): свою только что
 *   поставленную отметку «Передан» экран привязывает к номеру, иначе она показалась бы в чате
 *   следующего найденного водителя того же парка;
 * * «Передан» у Syntony не уходит в сам чат (в API истории нет внутренних заметок) — только
 *   отметкой в журнале и ленте раздела, и тексты экрана говорят ровно это.
 *
 * Без React — чтобы правила проверялись `node --test` (tests/driver_chats_source.test.mjs).
 */

const MEDIA_PREFIX = '/api/driver_chats/media/';
const MEDIA_FIELDS = ['photo', 'video', 'audio', 'pdf'];

function withBase(link, apiBaseUrl) {
    if (typeof link !== 'string' || !link.startsWith(MEDIA_PREFIX)) return link;
    return `${String(apiBaseUrl || '').replace(/\/+$/, '')}${link}`;
}

/* Адрес API — к ссылкам на вложения из ручки /media. Остальные ссылки (Chat2Desk отдаёт
   полный адрес своего хранилища) не трогаем. Объекты не мутируем: ответ сервера живёт и в
   других местах экрана. */
export function absolutizeMediaLinks(chats, apiBaseUrl) {
    return (chats || []).map((chat) => ({
        ...chat,
        messages: (chat.messages || []).map((message) => {
            const next = { ...message };
            MEDIA_FIELDS.forEach((field) => {
                if (next[field]) next[field] = withBase(next[field], apiBaseUrl);
            });
            if (Array.isArray(next.attachments)) {
                next.attachments = next.attachments.map((att) => (
                    att && att.link ? { ...att, link: withBase(att.link, apiBaseUrl) } : att));
            }
            return next;
        }),
    }));
}

/* Кто перед нами: клиент Chat2Desk, а если его нет (Syntony) — номер водителя. */
export function driverKeyOf(result) {
    if (result && result.clientId !== null && result.clientId !== undefined) {
        return `c${result.clientId}`;
    }
    return result && result.phone ? `p${result.phone}` : null;
}

/* Тексты про «Передан» — по тому, уходит ли отметка в сам чат (handoff_to_chat из /context).
   Пока /context не ответил, говорим осторожно: как у Syntony, — пообещать заметку в чате,
   которой не будет, хуже, чем не пообещать. */
export function handoffTexts(handoffToChat) {
    if (handoffToChat === true) {
        return {
            subtitle: 'В чат уйдёт внутренний комментарий. Водитель его не увидит',
            sent: 'Комментарий отправлен — он уже в ленте',
            failed: 'Не удалось отправить комментарий',
        };
    }
    return {
        subtitle: 'Отметка сохранится в журнале раздела. Водитель её не увидит',
        sent: 'Отметка «Передан» сохранена — она уже в ленте',
        failed: 'Не удалось сохранить отметку',
    };
}
