import { useEffect, useRef } from 'react';
import useChatPilot from './useChatPilot';
import useSharedChatUnread from './useSharedChatUnread';
import { useOutbox } from './sendQueue';
import { publishWaiting, useSectionCountsWaiting } from './chatWaitingBadge';
import { useWorkspace } from './workspaceStore';

/* Сторож счётчика «Чатов ОП» для боковой панели (chatWaitingBadge.js), пока
 * сам раздел не открыт. Живёт в App, как WazzupShiftKeeper, и ничего не рисует.
 * Считает чаты, ждущие ответа команды, а не сообщения (владелец, 18.10.2026).
 *
 * Механика — та же, что у раздела: снимок /api/wazzup/pilot/unread и живой
 * поток, по которому приходят изменения счётчика. Поток — только событий
 * счётчика (?only=unread): сообщения, «печатает» и карточки боковой панели не
 * нужны. Фонового опроса нет: число меняется только событием. Скрытая вкладка
 * поток не держит (useChatPilot), а раздел, открытый на «Чатах», считает сам —
 * тогда сторож молчит, и второго потока у вкладки нет.
 *
 * mayProcess — подсказка «кому вообще есть смысл спрашивать» (верификатор и
 * супер-админ): счётчик ждущих есть только у тех, кто обрабатывает чаты.
 * Решает ответ сервера (/api/wazzup/pilot). Верификатору обработка открывается
 * только после QR — состояние замка берём из общего хранилища смены
 * (workspaceStore.js, его ведут WazzupShiftKeeper и экран доступа раздела):
 * спрошенный один раз при входе в портал, ответ сервера остался бы «закрыто»
 * и после скана, и вне раздела число застыло бы до перезагрузки страницы. */
const IGNORE = () => ({ thread: false, list: false, notes: false });
const NOTHING = () => {};

export default function WazzupWaitingKeeper({ apiBaseUrl, withAccessTokenHeader, mayProcess, operator = false,
    userId }) {
    const tokenHeaders = useRef(withAccessTokenHeader);
    tokenHeaders.current = withAccessTokenHeader;
    const headers = () => (tokenHeaders.current ? tokenHeaders.current() : {});
    // Пока хранилище не загрузилось (или настроено на другого человека), оно «закрыто».
    const workspace = useWorkspace(operator ? userId : undefined);
    const unlocked = !operator || !workspace.locked;
    const allowed = Boolean(mayProcess) && unlocked;
    const sectionCounts = useSectionCountsWaiting();
    const active = allowed && !sectionCounts;
    // Свой ответ, ушедший секунду назад из раздела, гасит ожидание и здесь.
    const outbox = useOutbox();
    const unread = useSharedChatUnread({ enabled: active, selected: null, apiBaseUrl, headers, outbox });
    const pilot = useChatPilot({ apiBaseUrl, mayProcess: allowed, account: 'op', active, headers, selected: null,
        only: 'unread', refreshThread: NOTHING, refreshList: NOTHING, refreshNotes: NOTHING, refreshCards: NOTHING,
        onChanges: (changes) => { unread.apply(changes); return IGNORE(); }, refreshUnread: unread.refresh,
        onTyping: NOTHING, clearTyping: NOTHING, onCards: NOTHING });
    useEffect(() => {
        if (active && pilot.enabled && unread.ready) publishWaiting(unread.chats, 'keeper');
    }, [active, pilot.enabled, unread.ready, unread.chats]);
    /* Числа нет: обработка не открыта (верификатор ещё не прошёл QR или доступ
       закрыли), сервер ответил «не открыта» или закрыл её посреди работы. */
    const denied = !allowed || (!pilot.enabled && (Boolean(pilot.capability) || pilot.connection === 'unavailable'));
    useEffect(() => {
        if (denied) publishWaiting(null, 'keeper');
    }, [denied]);
    useEffect(() => () => publishWaiting(null, 'keeper'), []);
    return null;
}
