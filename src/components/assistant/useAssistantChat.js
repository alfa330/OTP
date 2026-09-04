import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { errText } from './assistantThread.jsx';

/* Состояние разговора с помощником — одно на вкладку в вике и на мини-чат шарика.
 *
 * Вынесено сюда не ради стройности, а потому что в этих ста строках сидят три
 * решения, которые при копировании разъедутся молча и проявятся только на проде:
 *
 *   1. ЧАТ СОЗДАЁТСЯ ЛЕНИВО. «Новый вопрос» не ходит на сервер вообще; POST
 *      /ai/chats уходит внутри первого ask. Иначе каждое открытие панели
 *      плодило бы пустой чат в истории — а шарик открывают куда чаще вкладки.
 *   2. ГОНКА ИСТОРИИ. Ответ на старый чат не должен затирать открытый: счётчик
 *      threadRequest, приём взят из Wazzup.
 *   3. ОТКАТ ВОПРОСА. Упал ask — свой пузырь снимается, а текст возвращается в
 *      строку ввода. Перепечатывать вопрос из-за 503 обидно, и человек с
 *      большой вероятностью просто уйдёт, а не повторит.
 *
 * spaceId В ЗАВИСИМОСТЯХ СТОИТ НАМЕРЕННО. В вике он был в теле функций, но не в
 * списке зависимостей (WikiAssistant.jsx до этой правки), и при смене
 * пространства переключателем в шапке /ai/status не перезапрашивался: панель
 * продолжала показывать периметр прошлой базы знаний. Шарику это критично —
 * он живёт во всех разделах, и пространство ему приезжает извне.
 */

const CHATS_LIMIT = 30;

export default function useAssistantChat({ base, headers, spaceId = null, enabled = true,
                                          withSuggestions = false }) {
    const [status, setStatus] = useState(null);
    const [statusError, setStatusError] = useState(null);
    const [chats, setChats] = useState([]);
    const [chatsLoading, setChatsLoading] = useState(true);
    const [activeId, setActiveId] = useState(null);
    const [messages, setMessages] = useState(null);
    const [draft, setDraft] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    const threadRequest = useRef(0);

    const loadStatus = useCallback(() => {
        if (!enabled) return Promise.resolve();
        return axios.get(`${base}/ai/status`, {
            headers,
            // suggest=1 просим только когда есть кому показать подсказки: этот
            // же роут дёргает шарик на каждой загрузке портала ради решения
            // «показываться ли», и лишний запрос к базе там ни к чему.
            params: { space_id: spaceId, suggest: withSuggestions ? 1 : undefined },
        })
            .then((r) => { setStatus(r.data); setStatusError(null); })
            // Ошибку статуса сохраняем, а не гасим в null: по её коду вызывающий
            // отличает «отдел без вики» и «нужен QR» от «сервер прилёг», и
            // рисует замок, а не пустой чат.
            .catch((e) => {
                setStatus(null);
                setStatusError({
                    code: e?.response?.data?.code || null,
                    httpStatus: e?.response?.status || null,
                    text: errText(e, 'Помощник недоступен'),
                });
            });
    }, [base, headers, spaceId, enabled, withSuggestions]);

    const loadChats = useCallback(() => {
        if (!enabled) return Promise.resolve();
        setChatsLoading(true);
        return axios.get(`${base}/ai/chats`, { headers, params: { limit: CHATS_LIMIT } })
            .then((r) => setChats(r.data?.chats || []))
            .catch((e) => setError(errText(e, 'Не удалось загрузить список чатов')))
            .finally(() => setChatsLoading(false));
    }, [base, headers, enabled]);

    useEffect(() => { loadStatus(); loadChats(); }, [loadStatus, loadChats]);

    const startNewChat = useCallback(() => {
        setActiveId(null);
        setMessages([]);
        setDraft('');
        setError('');
    }, []);

    const openChat = useCallback((chatId) => {
        const request = ++threadRequest.current;
        setActiveId(chatId);
        setMessages(null);
        setError('');
        return axios.get(`${base}/ai/chats/${chatId}`, { headers, params: { space_id: spaceId } })
            .then((r) => {
                if (request !== threadRequest.current) return;
                setMessages(r.data?.messages || []);
            })
            .catch((e) => {
                if (request !== threadRequest.current) return;
                setMessages([]);
                setError(errText(e, 'Не удалось открыть чат'));
            });
    }, [base, headers, spaceId]);

    const ask = useCallback(async (question) => {
        if (!question || busy) return;
        setBusy(true);
        setError('');

        const localUser = {
            id: `local-${Date.now()}`, role: 'user', kind: 'question',
            text: question, sources: [], created_at: new Date().toISOString(),
        };
        setMessages((prev) => [...(prev || []), localUser]);
        setDraft('');

        try {
            let chatId = activeId;
            if (!chatId) {
                const created = await axios.post(`${base}/ai/chats`, { space_id: spaceId }, { headers });
                chatId = created.data?.chat?.id;
                setActiveId(chatId);
            }
            const response = await axios.post(
                `${base}/ai/chats/${chatId}/ask`, { question, space_id: spaceId }, { headers });
            const data = response.data || {};
            setMessages((prev) => [...(prev || []), {
                id: data.message_id,
                role: 'assistant',
                kind: data.kind,
                text: data.text,
                sources: data.sources || [],
                model: data.model,
                elapsed_ms: data.elapsed != null ? Math.round(data.elapsed * 1000) : null,
                degraded_search: data.degraded_search,
                created_at: new Date().toISOString(),
            }]);
            loadChats();
        } catch (e) {
            setMessages((prev) => (prev || []).filter((m) => m.id !== localUser.id));
            setDraft(question);      // не теряем набранное: перепечатывать обидно
            setError(errText(e, 'Помощник не ответил'));
        } finally {
            setBusy(false);
        }
    }, [activeId, base, headers, spaceId, busy, loadChats]);

    const sendFeedback = useCallback((messageId, value) => {
        setMessages((prev) => (prev || []).map(
            (m) => (m.id === messageId ? { ...m, feedback: value } : m)));
        return axios.post(`${base}/ai/messages/${messageId}/feedback`,
                          { feedback: value }, { headers });
    }, [base, headers]);

    const removeChat = useCallback((chatId) => (
        axios.delete(`${base}/ai/chats/${chatId}`, { headers })
            .then(() => {
                setChats((prev) => prev.filter((c) => c.id !== chatId));
                if (activeId === chatId) startNewChat();
            })
    ), [activeId, base, headers, startNewChat]);

    const perimeter = status?.perimeter;
    const suggestions = status?.suggestions || [];
    const indexReady = (status?.index?.chunks || 0) > 0;
    const noAccess = !!perimeter && perimeter.articles_for_ai === 0;

    return {
        status, statusError, perimeter, indexReady, noAccess, suggestions,
        canAsk: !noAccess && indexReady,
        chats, chatsLoading, activeId, messages, draft, busy, error,
        setDraft, setError, setMessages,
        loadStatus, loadChats, startNewChat, openChat, ask, sendFeedback, removeChat,
    };
}
