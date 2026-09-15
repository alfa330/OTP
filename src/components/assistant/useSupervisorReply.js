import { useEffect, useRef } from 'react';
import { subscribeNewsPoke } from '../news/newsShared';
import { createCoalescedReload } from '../notifications/coalescedReload.js';

/* Ожидание ответа супервайзера в разговоре с помощником (задача #321).
 *
 * Отказ помощника оператору уходит супервайзеру отдела, а ответ приходит в ЭТОТ
 * ЖЕ чат — и нередко, пока человек смотрит на ленту. Ждём его без опроса по
 * таймеру: тем же тычком канала колокола, что поднимает окно новости
 * (newsShared.subscribeNewsPoke), плюс возврат во вкладку. Своего канала нет:
 * слотов SSE на портал ровно BELL_STREAM_LIMIT, и второй на вкладку срезал бы
 * ёмкость вдвое.
 *
 * Общий для вкладки «Помощник» в вике и мини-чата шарика: у них разные
 * владельцы ленты (WikiAssistant.jsx и useAssistantChat.js), и копия этого
 * правила в каждом разошлась бы молча.
 *
 * Подписка живёт, ТОЛЬКО пока в ленте есть неотвеченная передача: тычок
 * широковещателен и приходит на любое событие колокола, и перечитывать чат на
 * каждое чужое уведомление без повода было бы платой ни за что.
 */

export const awaitsSupervisor = (messages) => (messages || [])
    .some((message) => message?.escalation?.status === 'open');

export default function useSupervisorReply(messages, reload) {
    const waiting = awaitsSupervisor(messages);
    const reloadRef = useRef(reload);
    reloadRef.current = reload;

    useEffect(() => {
        if (!waiting) return undefined;
        /* Склейка, а не пропуск по времени: тычок, пришедший посреди
           перечитки, просит ровно один повтор. Выбросить его нельзя — это мог
           быть сам ответ. */
        const request = createCoalescedReload(() => Promise.resolve(reloadRef.current?.()));
        const onWake = () => {
            if (document.visibilityState === 'visible') request();
        };
        const unsubscribe = subscribeNewsPoke(request);
        window.addEventListener('focus', onWake);
        document.addEventListener('visibilitychange', onWake);
        return () => {
            unsubscribe();
            window.removeEventListener('focus', onWake);
            document.removeEventListener('visibilitychange', onWake);
        };
    }, [waiting]);
}
