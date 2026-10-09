import { useEffect, useRef } from 'react';
import {
    beatWorkspace, configureWorkspace, loadWorkspace, resetWorkspace, useWorkspace,
} from './workspaceStore';

/* Сторож смены верификатора. Живёт, пока открыт портал, и ничего не рисует.
 *
 * Раз в минуту отмечается на сервере: пока отметки идут, смена считается живой.
 * Закрыл человек портал и не вернулся — сервер сам закроет смену моментом
 * последней отметки (wazzup/shift.py). Смонтирован в App, а не в разделе,
 * потому что раздел размонтируется при уходе в «Вики» или «Мои смены», а смена
 * при этом продолжается.
 *
 * Отметки идут и когда смена не начата: её можно начать в другом окне портала,
 * и это окно узнаёт о ней только из ответа отметки — иначе, закрой человек то
 * окно, смена закрылась бы через десять минут, хотя он работает здесь.
 * Пока доступ не подтверждён, отметок нет вовсе: отмечать нечего. */
export default function WazzupShiftKeeper({ userId, apiBaseUrl, withAccessTokenHeader }) {
    const headers = useRef(withAccessTokenHeader);
    headers.current = withAccessTokenHeader;
    const { ready, locked, error, heartbeatSeconds } = useWorkspace(userId);

    useEffect(() => {
        configureWorkspace({ apiBaseUrl, ownerId: userId, headers: () => headers.current?.() });
        loadWorkspace();
        return resetWorkspace;
    }, [userId, apiBaseUrl]);

    // Неудачная первая загрузка повторяется сама: кнопку «Повторить» человек,
    // не открывавший раздел, не увидит, а без состояния не пошли бы отметки.
    useEffect(() => {
        if (ready || !error) return undefined;
        const timer = setInterval(loadWorkspace, 30000);
        return () => clearInterval(timer);
    }, [ready, error]);

    useEffect(() => {
        if (!ready || locked) return undefined;
        const timer = setInterval(beatWorkspace, Math.max(15, Number(heartbeatSeconds) || 60) * 1000);
        const onVisible = () => { if (document.visibilityState === 'visible') beatWorkspace(); };
        document.addEventListener('visibilitychange', onVisible);
        window.addEventListener('online', beatWorkspace);
        return () => {
            clearInterval(timer);
            document.removeEventListener('visibilitychange', onVisible);
            window.removeEventListener('online', beatWorkspace);
        };
    }, [ready, locked, heartbeatSeconds]);

    return null;
}
