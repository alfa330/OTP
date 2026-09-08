import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import axios from 'axios';
import Orb from './Orb.jsx';
import useAssistantChat from './useAssistantChat';
import {
    clampPosition, defaultPosition, movedEnough, panelAnchor, resolveDock, undock,
} from './orbPosition';
import {
    canOpenPipWindow, cloneDocumentStyles, mirrorDocumentChrome, pipWindowTaken,
} from '../../utils/pipWindow';
import './assistant-orb.css';

const AssistantPanel = lazy(() => import('./AssistantPanel.jsx'));

/* Плавающий помощник: шарик поверх портала и мини-чат из него.
 *
 * ЗАЧЕМ ОН ВООБЩЕ ЕСТЬ. База знаний лежит в разделе «Вики», а вопросы к ней
 * возникают в других разделах — оператор считает зарплату, разбирает обращение,
 * берёт смену на аукционе, и именно там ему нужен регламент. Раньше за ответом
 * надо было уйти из раздела, потеряв то, что на экране. Шарик отвечает на месте.
 *
 * ПОЧЕМУ СЛОЙ 84, А НЕ ВЫШЕ. Шкала z-index в проекте плотная (см. src/styles.css
 * и модалки разделов). 84 выбрано так, чтобы шарик САМ уходил под всё, что
 * занимает экран целиком, и не требовал для этого никакого распознавания:
 *
 *     модалка «Ивентов» 85 · ios.jsx sheet 90 · тренажёры вики 95 ·
 *     карточка задачи 110-111 · «Новость дня» и обращения IT 120 ·
 *     полноэкранная проверка низких оценок 135 · FullscreenSheet 140 · тосты 9999
 *
 * — всё это перекрывает шарик просто потому, что лежит выше; отдельного
 * реестра «открыт полноэкранный режим» не нужно, а несуществующих CSS-крючков
 * вроде body.otp-immersive в проекте нет и выдумывать их нельзя. Отдельно гасить
 * приходится только два случая, где оболочка есть, а содержимое чужое: «Журнал
 * оценок» (iframe со своей сборкой) и LMS (свой полноэкранный каркас). Плата за
 * простоту: модалки разделов на 70-80 остаются ПОД шариком. Сознательно — их
 * немного, шарик в углу мешает мало, и его всегда можно отодвинуть.
 *
 * ОТКРЕПЛЕНИЕ В ОКНО ПОВЕРХ ДРУГИХ ОКОН. Шарик снял «уйти из раздела за
 * ответом», но не снял «уйти из портала»: половину рабочего времени оператор
 * проводит в чужих системах — Fleet, CRM, чаты, — и там помощника не было
 * вовсе. Кнопка «открепить» выносит панель в окно Document Picture-in-Picture,
 * которое браузер держит поверх ВСЕГО, включая другие окна и другие программы.
 * Механика окна общая на портал (src/utils/pipWindow.js): такое окно в
 * документе ровно одно, и его уже открывают закреплённая задача и табло СЗоВ.
 *
 * РАЗГОВОР ЖИВЁТ ЗДЕСЬ, А НЕ В ПАНЕЛИ. useAssistantChat поднят в шарик
 * намеренно. Открепление — это createPortal в ДРУГОЙ контейнер, а смена
 * контейнера для React означает размонтирование и монтирование заново: хук
 * внутри панели терял бы открытый чат, ленту и набранный вопрос ровно в тот
 * миг, когда человек нажимает «открепить». Побочно это чинит и старую потерю —
 * свёрнутая тычком в шарик панель больше не забывает разговор.
 *
 * ПОЧЕМУ ПАНЕЛЬ ГРУЗИТСЯ ЛЕНИВО. Шарик висит на каждой странице портала у всех,
 * а чат открывает меньшинство. Мини-чат тянет за собой markdown, DOMPurify и
 * примитивы чата; в общем бандле это байты, которые платит каждый вход в
 * портал. lazy() оставляет в основном коде только сам пузырь.
 */

const STORAGE_PREFIX = 'otp_assistant_orb:';
const PANEL_SIZE = { width: 384, height: 520 };

/* Разделы, где шарика нет.
 *
 * call_evaluation и lms — оболочка есть, а содержимое рисует не она: «Журнал
 * оценок» это iframe со своей сборкой, LMS — свой полноэкранный каркас.
 *
 * wiki — по другой причине, и она главная. В самом разделе помощник УЖЕ есть:
 * вкладка «Помощник» и строка «Спросить Помощника» под выдачей поиска. Шарик
 * поверх них — второй вход в то же самое, и человек, у которого на экране два
 * помощника, начинает выбирать между ними вместо того, чтобы спросить. Заодно
 * это снимает три технические беды одним решением: оверлеи тренажёров,
 * полноэкранное чтение статьи и `zoom` на .wiki-scope, из-за которого
 * getBoundingClientRect внутри вики возвращает координаты, умноженные на
 * масштаб (см. wiki-scale.css) — а шарик считает позицию именно ими. */
const SUPPRESSED_VIEWS = new Set(['wiki', 'call_evaluation', 'lms']);

const storageKey = (userId) => `${STORAGE_PREFIX}${userId}`;

/* Приватный режим и отключённое хранилище: сюда прилетает исключение уже на
   ОБРАЩЕНИИ к localStorage, а не только на записи, поэтому в try завёрнуто всё. */
const readStored = (userId) => {
    try {
        const raw = window.localStorage.getItem(storageKey(userId));
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return null;
        return parsed;
    } catch (error) {
        return null;
    }
};

const writeStored = (userId, value) => {
    try {
        window.localStorage.setItem(storageKey(userId), JSON.stringify(value));
    } catch (error) {
        /* Место кончилось или хранилище закрыто — позиция просто не переживёт
           перезагрузку. Ронять из-за этого виджет нельзя. */
    }
};

const viewportSize = () => ({
    width: window.innerWidth || 1024,
    height: window.innerHeight || 768,
});

export default function AssistantOrb({
    user, view, apiBaseUrl, withAccessTokenHeader, showToast,
    wikiEnabled = true, locked = false, lockChecking = false,
    onRequestQr, onOpenWikiArticle, onOpenWikiAssistant,
}) {
    const userId = user?.id;
    const [position, setPosition] = useState(null);   // null — ещё не примерились к окну
    const [open, setOpen] = useState(false);
    const [dragging, setDragging] = useState(false);
    const [hidden, setHidden] = useState(false);      // вкладка в фоне
    const [viewport, setViewport] = useState(null);
    const [started, setStarted] = useState(false);    // панель хоть раз открывали
    const [pipWindow, setPipWindow] = useState(null);     // окно поверх других окон
    const [pipContainer, setPipContainer] = useState(null);

    const buttonRef = useRef(null);
    const dragRef = useRef(null);

    const base = `${apiBaseUrl}/api/wiki`;
    const headers = useMemo(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    /* ПРОБА. Один дешёвый запрос за сессию, который отвечает сразу на всё:
       есть ли у человека хоть одна статья для помощника, собран ли индекс и
       какое пространство сервер считает для него действующим.

       Без пробы шарик показывался бы и тому, у кого периметр пуст: он открыл бы
       панель и получил 409 «нет доступных статей» — то есть кнопку, которая по
       построению не работает. Такой шарик хуже отсутствующего.

       Пространство спрашиваем у СЕРВЕРА, а не берём из localStorage напрямую.
       Ключ wiki:space пишет переключатель вики, но это лишь память браузера:
       выбор мог устареть, доступ к тому пространству могли отозвать, а тумблер
       «Помощник» в нём — выключить. Присланное значение сервер проверяет и
       возвращает то, по которому будет отвечать на самом деле
       (wiki/routes_ai.py: effective_space). Отвечать же по ОБЪЕДИНЕНИЮ
       пространств нельзя: на вопрос про «Тез» приехал бы абзац из
       «Таксопарков» без признака, что база знаний другая. */
    const [probe, setProbe] = useState(null);

    const rememberedSpace = () => {
        try {
            const value = Number(window.localStorage.getItem('wiki:space'));
            return Number.isFinite(value) && value > 0 ? value : undefined;
        } catch (error) {
            return undefined;
        }
    };

    useEffect(() => {
        // У запертого QR-ом пробы нет: она вернула бы 403, а решение показать
        // ему шарик с замком принято и без сервера.
        if (!userId || !wikiEnabled || locked) return undefined;
        let alive = true;
        axios.get(`${base}/ai/status`, { headers, params: { space_id: rememberedSpace() } })
            .then((r) => {
                if (!alive) return;
                const data = r.data || {};
                setProbe({
                    ok: (data.perimeter?.articles_for_ai || 0) > 0
                        && (data.index?.chunks || 0) > 0,
                    spaceId: data.space_id ?? null,
                });
            })
            .catch((error) => {
                if (!alive) return;
                // Сессия перестала быть подтверждённой уже при открытой вкладке:
                // локально «доступ есть», а сервер отвечает 403. Шарик оставляем
                // — панель откроется замком.
                const code = error?.response?.data?.code;
                setProbe({
                    ok: code === 'SENSITIVE_ACCESS_REQUIRED',
                    spaceId: null,
                    serverLocked: code === 'SENSITIVE_ACCESS_REQUIRED',
                });
            });
        return () => { alive = false; };
    }, [userId, wikiEnabled, locked, base, headers]);

    const spaceId = probe?.spaceId ?? null;
    const detached = !!pipContainer;

    /* Разговор. `started` держит хук выключенным до первого открытия панели:
       шарик висит у всех на каждой странице, а /ai/status с подсказками и
       список чатов нужны только тому, кто помощника действительно открыл. */
    const chat = useAssistantChat({
        base, headers, spaceId, enabled: started && !locked, withSuggestions: true,
    });

    /* Первая примерка к окну. Позицию сохранял, возможно, широкий монитор —
       на ноутбуке те же координаты означают шарик за краем экрана, которого
       не видно и который поэтому нельзя вернуть. */
    useEffect(() => {
        if (!userId) return;
        const size = viewportSize();
        const stored = readStored(userId);
        setViewport(size);
        setPosition(stored
            ? clampPosition(stored, size)
            : defaultPosition(size));
    }, [userId]);

    /* Изменение размера окна. Шарик едет вместе с краем, а не остаётся висеть
       в координатах, которых больше нет. */
    useEffect(() => {
        const onResize = () => {
            const size = viewportSize();
            setViewport(size);
            setPosition((prev) => (prev ? clampPosition(prev, size) : prev));
        };
        window.addEventListener('resize', onResize);
        return () => window.removeEventListener('resize', onResize);
    }, []);

    /* Вкладка ушла в фон — гасим анимацию. Браузер тормозит её и сам, но не
       везде одинаково, а виджет висит всегда и у всех. */
    useEffect(() => {
        const onVisibility = () => setHidden(document.hidden);
        document.addEventListener('visibilitychange', onVisibility);
        return () => document.removeEventListener('visibilitychange', onVisibility);
    }, []);

    useEffect(() => {
        if (!userId || !position || dragging) return;
        writeStored(userId, position);
    }, [userId, position, dragging]);

    /* Тычок в шарик. Откреплённый помощник живёт в отдельном окне, и второй,
       встроенной, панели быть не должно: две ленты одного разговора на экране —
       это выбор «в какую из них писать» вместо вопроса. Поэтому тычок выводит
       окно вперёд, а не открывает копию. */
    const toggleOpen = useCallback(() => {
        if (pipWindow) {
            try {
                pipWindow.focus();
            } catch (error) { /* окно уже закрыто */ }
            return;
        }
        setPosition((prev) => undock(prev, viewport || viewportSize()));
        setStarted(true);
        setOpen((prev) => !prev);
    }, [pipWindow, viewport]);

    /* Перетаскивание. setPointerCapture обязателен: без него курсор, обогнавший
       шарик (а он обгоняет всегда — шарик едет за указателем), уносит события
       на элемент под собой, и перетаскивание срывается на первом же рывке. */
    const onPointerDown = useCallback((event) => {
        if (event.button != null && event.button !== 0) return;
        const start = { x: event.clientX, y: event.clientY };
        dragRef.current = {
            pointerId: event.pointerId,
            start,
            origin: position,
            moved: false,
        };
        try {
            event.currentTarget.setPointerCapture(event.pointerId);
        } catch (error) {
            /* Синтетические события в тестах капчу не поддерживают. */
        }
    }, [position]);

    const onPointerMove = useCallback((event) => {
        const drag = dragRef.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        const current = { x: event.clientX, y: event.clientY };
        if (!drag.moved && !movedEnough(drag.start, current)) return;

        if (!drag.moved) {
            drag.moved = true;
            setDragging(true);
            // Прижатый шарик, который потащили, сначала выезжает на экран целиком.
            drag.origin = undock(drag.origin, viewport || viewportSize());
        }
        const size = viewport || viewportSize();
        setPosition(clampPosition({
            x: drag.origin.x + (current.x - drag.start.x),
            y: drag.origin.y + (current.y - drag.start.y),
            dock: null,
        }, size));
    }, [viewport]);

    const finishDrag = useCallback((event) => {
        const drag = dragRef.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        dragRef.current = null;
        try {
            event.currentTarget.releasePointerCapture(event.pointerId);
        } catch (error) { /* см. выше */ }

        if (!drag.moved) {
            // Нажатие без движения — это клик: открываем или закрываем чат.
            toggleOpen();
            return;
        }
        setDragging(false);
        /* Позицию берём из САМОГО отпускания, а не из последнего pointermove.
           Браузер имеет право не прислать move, совпадающий с отпусканием (а на
           быстром броске он его и не присылает — события схлопываются), и тогда
           шарик, доведённый до края, оставался бы в паре сотен пикселей от него
           и не прилипал. Это ровно тот случай, когда «почти всегда работает»
           означает «не работает у того, кто бросает резко». */
        const size = viewport || viewportSize();
        const dropped = clampPosition({
            x: drag.origin.x + (event.clientX - drag.start.x),
            y: drag.origin.y + (event.clientY - drag.start.y),
            dock: null,
        }, size);
        setPosition(resolveDock(dropped, size));
    }, [viewport, toggleOpen]);

    /* Клавиатура. Перетаскивание живёт на pointer-событиях, а они с клавиатуры
       не приходят вовсе: Enter и пробел на <button> дают сразу click. Без этой
       ветки помощник открывался бы только мышью — то есть для человека, который
       ходит по порталу с клавиатуры, шарика бы не существовало.

       detail === 0 — признак того, что click пришёл НЕ от указателя (мышь
       ставит туда счётчик нажатий). Так две механики не наступают друг другу на
       ноги: мышиный click после pointerup сюда не попадает и панель не
       переоткрывает. */
    const onClick = useCallback((event) => {
        if (event.detail !== 0) return;
        toggleOpen();
    }, [toggleOpen]);

    /* Открепить помощника в окно поверх других окон.
       Окно в документе ровно одно, и запрос при занятом ОТБИРАЕТ его молча —
       поэтому вместо кражи объясняем, почему не открылось: иначе помощник
       схлопнул бы табло линии, за которым следит смена. */
    const detach = useCallback(async () => {
        if (pipWindowTaken()) {
            showToast?.('Окно поверх других уже занято другим виджетом — закройте его '
                        + 'и открепите помощника снова', 'error');
            return;
        }
        try {
            const win = await window.documentPictureInPicture.requestWindow({
                width: PANEL_SIZE.width,
                height: PANEL_SIZE.height,
            });
            win.document.title = 'Помощник';
            cloneDocumentStyles(win);
            mirrorDocumentChrome(win);
            win.document.body.style.margin = '0';
            win.document.body.style.height = '100vh';
            win.document.body.style.overflow = 'hidden';
            const root = win.document.createElement('div');
            root.className = 'aorb-pip-root';
            win.document.body.appendChild(root);
            // Закрыли окно системным крестиком — помощник закрыт, а не «висит невидимым».
            win.addEventListener('pagehide', () => {
                setPipWindow(null);
                setPipContainer(null);
            });
            setOpen(false);
            setStarted(true);
            setPipWindow(win);
            setPipContainer(root);
        } catch (error) {
            // Браузер вправе отказать: например, запрос ушёл без свежего жеста.
            showToast?.('Не удалось открыть окно поверх других — попробуйте ещё раз', 'error');
        }
    }, [showToast]);

    const closePip = useCallback(() => {
        try {
            pipWindow?.close?.();
        } catch (error) {
            /* Окно уже закрыл браузер — гонки при выключении нас не касаются. */
        }
        setPipWindow(null);
        setPipContainer(null);
    }, [pipWindow]);

    /* Вернуть панель в портал: окно закрывается, панель раскрывается на месте
       шарика. Разговор переживает переезд — он лежит в хуке выше, а не в панели. */
    const attach = useCallback(() => {
        closePip();
        setOpen(true);
    }, [closePip]);

    /* Вышли из аккаунта — окно обязано уйти вместе с сессией: в нём лежит
       переписка с базой знаний, а сам портал уже показывает форму входа. */
    useEffect(() => {
        if (userId || !pipWindow) return;
        closePip();
    }, [userId, pipWindow, closePip]);

    /* Размонтировали шарик (выход, смена аккаунта) — окно за собой закрываем. */
    useEffect(() => () => {
        try {
            pipWindow?.close?.();
        } catch (error) { /* см. выше */ }
    }, [pipWindow]);

    const anchor = useMemo(() => {
        if (!position || !viewport) return null;
        return panelAnchor(position, viewport, PANEL_SIZE);
    }, [position, viewport]);

    /* Escape закрывает панель — привычка от всех модалок портала.
       Слушать надо ТО окно, где панель на самом деле. У откреплённого помощника
       фокус в PiP-окне, а это отдельный window: обработчик, повешенный на окно
       вкладки, туда не дотягивается, и Escape в откреплённой панели молчал бы. */
    useEffect(() => {
        const host = pipWindow || (open ? window : null);
        if (!host) return undefined;
        const onKey = (event) => {
            if (event.key !== 'Escape') return;
            if (pipWindow) closePip(); else setOpen(false);
        };
        host.addEventListener('keydown', onKey);
        return () => host.removeEventListener('keydown', onKey);
    }, [open, pipWindow, closePip]);

    /* Источник и «открыть в вике» уводят в раздел, то есть во ВКЛАДКУ портала.
       Из откреплённого окна она сейчас в фоне: без focus() человек нажал бы
       «источник» и не увидел ничего — вкладка сменила бы раздел за его спиной,
       а на экране осталось бы то же окно помощника. */
    const raisePortal = useCallback(() => {
        if (!pipWindow) return;
        try {
            window.focus();
        } catch (error) { /* браузер вправе не поднять фоновую вкладку */ }
    }, [pipWindow]);

    const openArticle = useCallback((slug, quote) => {
        if (!slug) return;
        setOpen(false);
        raisePortal();
        onOpenWikiArticle?.(slug, quote);
    }, [onOpenWikiArticle, raisePortal]);

    const openFull = useCallback(() => {
        setOpen(false);
        raisePortal();
        onOpenWikiAssistant?.();
    }, [onOpenWikiAssistant, raisePortal]);

    /* Кому шарика не видно вовсе. Замок QR сюда НЕ входит: владелец решил, что
       неподтверждённый оператор обязан видеть помощника и понимать, как его
       открыть, — иначе для самой массовой роли портала фичи просто нет.

       Запертому QR-ом шарик положен без пробы; всем остальным — только когда
       сервер подтвердил, что помощнику есть на чём отвечать. */
    const orbVisible = !!userId
        && wikiEnabled
        && !SUPPRESSED_VIEWS.has(view)
        && (locked || !!probe?.ok)
        && !!position;

    /* Откреплённое окно переживает всё перечисленное, и это не недосмотр.
       Уйдя в «Вики», человек теряет шарик — но окно, которое он сам вынес
       поверх Fleet и в котором лежит его разговор, закрываться от смены
       раздела в чужой вкладке не должно. Закрывают его крестиком, Escape,
       кнопкой «вернуть в портал» и выходом из аккаунта — то есть решением
       человека, а не переходом по меню. */
    if (!orbVisible && !detached) return null;

    const docked = !!position?.dock;

    const panel = (
        <Suspense fallback={(
            <div className="flex h-full items-center justify-center text-[12.5px] text-slate-400">
                Открываем помощника…
            </div>
        )}>
            <AssistantPanel
                chat={chat}
                locked={locked}
                lockChecking={lockChecking}
                detached={detached}
                canDetach={canOpenPipWindow()}
                onDetach={detach}
                onAttach={attach}
                onRequestQr={onRequestQr}
                onOpenArticle={openArticle}
                onOpenFullAssistant={openFull}
                onClose={() => setOpen(false)}
                showToast={showToast}
            />
        </Suspense>
    );

    return (
        <>
            {orbVisible && (
                <button
                    ref={buttonRef}
                    type="button"
                    className={`aorb-button${docked ? ' aorb-dock' : ''}${dragging ? ' aorb-dragging' : ''}${hidden ? ' aorb-idle' : ''}`}
                    style={{ left: position.x, top: position.y, zIndex: 84 }}
                    onClick={onClick}
                    onPointerDown={onPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={finishDrag}
                    onPointerCancel={finishDrag}
                    aria-label={detached
                        ? 'Показать окно помощника'
                        : (open ? 'Свернуть помощника' : 'Открыть помощника')}
                    aria-expanded={open || detached}
                    title={detached
                        ? 'Помощник открыт в отдельном окне'
                        : 'Помощник — вопрос по базе знаний'}
                >
                    <Orb animated={!hidden} />
                </button>
            )}

            {!detached && orbVisible && open && anchor && (
                <div
                    className="fixed overflow-hidden rounded-[18px] border border-slate-200/80 bg-white/95 shadow-[0_18px_48px_rgba(15,23,42,0.16),0_2px_8px_rgba(15,23,42,0.06)] backdrop-blur-xl"
                    style={{
                        left: anchor.left,
                        top: anchor.top,
                        width: anchor.width,
                        height: anchor.height,
                        zIndex: 84,
                    }}
                    role="dialog"
                    aria-label="Помощник"
                >
                    {panel}
                </div>
            )}

            {detached && createPortal(panel, pipContainer)}
        </>
    );
}
