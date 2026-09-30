import React from 'react';
import { createPortal } from 'react-dom';
import { buildCardSections } from './employeesPhoneList';
import { historyFieldLabel } from '../modals/historyFieldLabels';
import './employee-card-sheet.css';

/**
 * Карточка сотрудника на компьютере — «Учет сотрудников».
 *
 * Решение владельца 30.09.2026: меню «три точки» в конце строки убрать;
 * нажатие на строку открывает карточку для чтения, а то, что жило в меню
 * («Править», «В супервайзеры», «История»), — кнопки самой карточки. Стиль
 * iOS/macOS, плавные переходы и НИКАКОЙ второй модалки поверх первой.
 *
 * Поэтому это одно окно со стеком экранов внутри, как лист в iOS:
 *   • карточка — корень: фото, имя, статус, действия плитками и поля всех
 *     четырёх наборов таблицы разом (те же, что у карточки телефона);
 *   • «Изменить», «История», «В операторы» въезжают справа В ЭТО ЖЕ окно, со
 *     стрелкой назад, а высота окна плавно подстраивается под экран;
 *   • вопрос перед действием («Повысить?», «Удалить?») раскрывается прямо под
 *     кнопкой — не окном поверх и не window.confirm.
 *
 * Действия — тот же список, что у телефона (actionsFor в App.jsx): `page` —
 * экран внутри окна, `confirm` — вопрос перед запуском, `danger` — отдельная
 * красная группа внизу. Экраны правки и перевода рисует App.jsx (`pages`),
 * историю — эта карточка сама.
 */

/* Длительности совпадают с employee-card-sheet.css: уход окна и сдвиг экрана. */
const SHEET_LEAVE_MS = 200;
const PAGE_MS = 420;
const BAR_HEIGHT = 52;
const PANEL_MAX = 860;
const MIN_STAGE = 96;

const HISTORY_PAGE = { title: 'История изменений' };

/* Нижний предел — чтобы на низком окне браузера карточка не схлопнулась в
   полоску; выше него окно занимает не больше 88% высоты экрана. */
const maxStageHeight = () => {
    const viewport = typeof window === 'undefined' ? 900 : window.innerHeight;
    return Math.max(240, Math.min(PANEL_MAX, Math.round(viewport * 0.88)) - BAR_HEIGHT);
};

const GLYPHS = {
    edit: (
        <>
            <path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.36a.5.5 0 0 0 .62.62l4.36-1.32a2 2 0 0 0 .83-.5z" />
            <path d="M15 5l4 4" />
        </>
    ),
    history: (
        <>
            <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
            <path d="M3 3v5h5" />
            <path d="M12 7v5l4 2" />
        </>
    ),
    promote: (
        <>
            <path d="M2 21a8 8 0 0 1 13.29-6" />
            <circle cx="10" cy="8" r="5" />
            <path d="M19 22v-6" />
            <path d="M16 19l3-3 3 3" />
        </>
    ),
    demote: (
        <>
            <path d="M2 21a8 8 0 0 1 13.29-6" />
            <circle cx="10" cy="8" r="5" />
            <path d="M19 16v6" />
            <path d="M16 19l3 3 3-3" />
        </>
    ),
};

const Glyph = ({ name }) => (
    <svg className="ecs-glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {GLYPHS[name] || GLYPHS.edit}
    </svg>
);

const Spinner = ({ small = false }) => <span className={`ecs-spinner${small ? ' is-small' : ''}`} aria-hidden="true" />;

const Photo = ({ person, Avatar }) => {
    const name = String(person?.name || '').trim();
    return (
        <span className="ecs-photo" aria-hidden="true">
            {person?.avatar_url
                ? <Avatar src={person.avatar_url} alt="" className="ecs-photo-img" />
                : (name.charAt(0).toUpperCase() || '?')}
        </span>
    );
};

/* Раскрытие по высоте без замера: сетка из одной строки 0fr → 1fr. Пока
   закрыто, содержимое инертно — Tab не должен попадать в невидимые кнопки. */
const Reveal = ({ open, children }) => (
    <div className={`ecs-reveal${open ? ' is-open' : ''}`} inert={open ? undefined : ''}>
        <div className="ecs-reveal-inner">{children}</div>
    </div>
);

const Confirm = ({ action, busy, onCancel, onConfirm }) => (
    <div className="ecs-confirm">
        <p className="ecs-confirm-note">{action.confirm.note}</p>
        <div className="ecs-confirm-btns">
            <button type="button" className="ecs-btn" onClick={onCancel} disabled={busy}>Отмена</button>
            <button
                type="button"
                className={`ecs-btn ${action.danger ? 'is-danger' : 'is-primary'}`}
                onClick={onConfirm}
                disabled={busy}
            >
                {busy && <Spinner small />}
                {action.confirm.label}
            </button>
        </div>
    </div>
);

/* ── История изменений ─────────────────────────────────────────────────── */

const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
    'августа', 'сентября', 'октября', 'ноября', 'декабря'];

/* Ручка отдаёт «YYYY-MM-DD HH:MM:SS» без пояса — это время стены, его и
   показываем. new Date() такую строку в Safari не разбирает вовсе. */
const parseStamp = (raw) => {
    const text = String(raw || '').trim();
    let match = text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
    if (match) {
        return { y: +match[1], m: +match[2], d: +match[3], time: match[4] ? `${match[4]}:${match[5]}` : '' };
    }
    match = text.match(/^(\d{2})[-./](\d{2})[-./](\d{4})(?:[ T](\d{2}):(\d{2}))?/);
    if (match) {
        return { y: +match[3], m: +match[2], d: +match[1], time: match[4] ? `${match[4]}:${match[5]}` : '' };
    }
    return null;
};

const dayLabel = (stamp, now) => {
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const day = new Date(stamp.y, stamp.m - 1, stamp.d);
    const diff = Math.round((today - day) / 86400000);
    if (diff === 0) return 'Сегодня';
    if (diff === 1) return 'Вчера';
    const month = MONTHS_GENITIVE[stamp.m - 1] || '';
    return `${stamp.d} ${month}${stamp.y === now.getFullYear() ? '' : ` ${stamp.y}`}`;
};

/* Значение правки словами: «bs → working» из журнала — это «Б/С → Работает».
   Подписи знает App.jsx (valueOf); пустое — прочерк, а не «Работает». */
const shownValue = (value, field, valueOf) => {
    const text = value == null ? '' : String(value).trim();
    if (!text) return '—';
    return valueOf ? String(valueOf(field, text) ?? text) : text;
};

function HistoryPage({ employeeId, load, cache, valueOf = null }) {
    const cached = cache.get(employeeId);
    const [state, setState] = React.useState(cached ? { status: 'ready', items: cached } : { status: 'loading', items: [] });
    const [query, setQuery] = React.useState('');
    const [attempt, setAttempt] = React.useState(0);
    /* Функция загрузки — из App.jsx и может меняться на каждый рендер; в
       зависимостях эффекта она перезапрашивала бы историю бесконечно. */
    const loadRef = React.useRef(load);
    loadRef.current = load;

    React.useEffect(() => {
        let alive = true;
        Promise.resolve()
            .then(() => loadRef.current(employeeId))
            .then((items) => {
                if (!alive) return;
                const list = Array.isArray(items) ? items : [];
                cache.set(employeeId, list);
                setState({ status: 'ready', items: list });
            })
            .catch(() => {
                if (!alive) return;
                // Уже показанная история лучше плашки об ошибке обновления.
                setState((prev) => (prev.status === 'ready' ? prev : { status: 'error', items: [] }));
            });
        return () => { alive = false; };
    }, [employeeId, cache, attempt]);

    const q = query.trim().toLowerCase();
    const groups = React.useMemo(() => {
        const now = new Date();
        const list = [];
        const byKey = new Map();
        state.items.forEach((entry, index) => {
            const stamp = parseStamp(entry?.changed_at);
            const label = historyFieldLabel(entry?.field);
            const oldValue = shownValue(entry?.old_value, entry?.field, valueOf);
            const newValue = shownValue(entry?.new_value, entry?.field, valueOf);
            if (q) {
                const haystack = [label, entry?.field, oldValue, newValue, entry?.old_value, entry?.new_value, entry?.changed_by, entry?.changed_at]
                    .map((value) => (value == null ? '' : String(value).toLowerCase()));
                if (!haystack.some((value) => value.includes(q))) return;
            }
            const key = stamp ? `${stamp.y}-${stamp.m}-${stamp.d}` : 'none';
            if (!byKey.has(key)) {
                const group = { key, label: stamp ? dayLabel(stamp, now) : 'Без даты', items: [] };
                byKey.set(key, group);
                list.push(group);
            }
            byKey.get(key).items.push({
                id: entry?.id ?? `${key}-${index}`,
                label,
                time: stamp ? stamp.time : String(entry?.changed_at || ''),
                oldValue,
                newValue,
                who: String(entry?.changed_by || '').trim(),
            });
        });
        return list;
    }, [state.items, q, valueOf]);

    if (state.status === 'loading') {
        return <div className="ecs-loading"><Spinner /></div>;
    }
    if (state.status === 'error') {
        return (
            <div className="ecs-empty">
                Не удалось загрузить историю.
                <button
                    type="button"
                    className="ecs-link"
                    onClick={() => { setState({ status: 'loading', items: [] }); setAttempt((n) => n + 1); }}
                >
                    Повторить
                </button>
            </div>
        );
    }

    return (
        <div className="ecs-hist">
            {state.items.length > 0 && (
                <label className="ecs-search">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.2-5.2m0 0A7.5 7.5 0 1 0 5.2 5.2a7.5 7.5 0 0 0 10.6 10.6z" />
                    </svg>
                    <input
                        type="search"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="Поиск по полю, значению или автору"
                        autoComplete="off"
                        spellCheck={false}
                        aria-label="Поиск в истории"
                    />
                </label>
            )}
            {groups.length === 0 ? (
                <div className="ecs-empty">
                    {q ? `Ничего не найдено по запросу «${query.trim()}»` : 'Изменений пока не было'}
                </div>
            ) : groups.map((group) => (
                <section key={group.key} className="ecs-group">
                    <div className="ecs-caption">{group.label}</div>
                    <ul className="ecs-list">
                        {group.items.map((item) => (
                            <li key={item.id} className="ecs-item ecs-hist-item">
                                <div className="ecs-hist-line">
                                    <span className="ecs-hist-field">{item.label}</span>
                                    {item.time && <span className="ecs-hist-time">{item.time}</span>}
                                </div>
                                <div className="ecs-hist-change">
                                    <span className="ecs-hist-old">{item.oldValue}</span>
                                    <svg className="ecs-hist-arrow" viewBox="0 0 16 16" fill="none" aria-label="стало">
                                        <path d="M3 8h9.5M9 4.5L12.5 8 9 11.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                                    </svg>
                                    <span className="ecs-hist-new">{item.newValue}</span>
                                </div>
                                {item.who && <div className="ecs-hist-who">{item.who}</div>}
                            </li>
                        ))}
                    </ul>
                </section>
            ))}
        </div>
    );
}

/* ── Окно ──────────────────────────────────────────────────────────────── */

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export default function EmployeeCardSheet({
    employeeId,
    rows,
    loading = false,
    onClose,
    cardSectionList,
    columnsFor,
    renderValue,
    subtitleOf = null,
    statusCodeOf,
    statusLabelOf,
    isBlacklist,
    actionsFor = null,
    pages = null,
    loadHistory = null,
    historyValueOf = null,
    Avatar,
}) {
    const open = employeeId != null;
    const employee = open
        ? (Array.isArray(rows) ? rows : []).find((row) => Number(row?.id) === Number(employeeId)) || null
        : null;
    /* Окно уезжает дольше, чем живёт выбор: пока идёт уход, ему нужно что
       показывать, иначе содержимое пропадает на полпути. */
    const lastEmployeeRef = React.useRef(null);
    if (employee) lastEmployeeRef.current = employee;
    const person = employee || lastEmployeeRef.current;

    const [mounted, setMounted] = React.useState(open);
    const [leaving, setLeaving] = React.useState(false);
    const [ready, setReady] = React.useState(false);
    const [pageKey, setPageKey] = React.useState(null);
    const [renderedPage, setRenderedPage] = React.useState(null);
    const [pageIn, setPageIn] = React.useState(false);
    const [confirmKey, setConfirmKey] = React.useState(null);
    const [runningKey, setRunningKey] = React.useState(null);
    const [heights, setHeights] = React.useState({ root: 0, page: 0 });
    const [maxStage, setMaxStage] = React.useState(maxStageHeight);

    const panelRef = React.useRef(null);
    const barRef = React.useRef(null);
    const nameRef = React.useRef(null);
    const rootScrollRef = React.useRef(null);
    const rootBodyRef = React.useRef(null);
    const pageScrollRef = React.useRef(null);
    const pageBodyRef = React.useRef(null);
    const returnFocusRef = React.useRef(null);
    const lastConfirmRef = React.useRef(null);
    const historyCacheRef = React.useRef(new Map());
    const aliveRef = React.useRef(true);
    React.useEffect(() => () => { aliveRef.current = false; }, []);

    const pageDef = React.useCallback(
        (key) => (key === 'history' ? HISTORY_PAGE : (pages && pages[key]) || null),
        [pages],
    );

    /* Появление и уход окна. */
    const wasOpenRef = React.useRef(open);
    React.useEffect(() => {
        if (open) {
            wasOpenRef.current = true;
            setMounted(true);
            setLeaving(false);
            return undefined;
        }
        if (!wasOpenRef.current) return undefined;
        wasOpenRef.current = false;
        setReady(false);
        setLeaving(true);
        const timer = setTimeout(() => {
            setMounted(false);
            setLeaving(false);
        }, SHEET_LEAVE_MS);
        const back = returnFocusRef.current;
        if (back && typeof back.focus === 'function' && document.contains(back)) {
            back.focus({ preventScroll: true });
        }
        return () => clearTimeout(timer);
    }, [open]);

    /* Новый человек — карточка с начала: без открытого экрана и вопроса. */
    React.useLayoutEffect(() => {
        if (!open) return;
        const active = document.activeElement;
        if (active && !panelRef.current?.contains(active)) returnFocusRef.current = active;
        setPageKey(null);
        setRenderedPage(null);
        setPageIn(false);
        setConfirmKey(null);
        setRunningKey(null);
        rootScrollRef.current?.scrollTo?.(0, 0);
        const bar = barRef.current;
        if (bar) {
            delete bar.dataset.lined;
            delete bar.dataset.title;
        }
    }, [employeeId, open]);

    /* Высота окна меняется плавно — но не в первый кадр: иначе окно
       вырастало бы из нулевой высоты при каждом открытии. */
    React.useEffect(() => {
        if (!open || !mounted) return undefined;
        panelRef.current?.focus({ preventScroll: true });
        let second = 0;
        const first = requestAnimationFrame(() => {
            second = requestAnimationFrame(() => setReady(true));
        });
        return () => {
            cancelAnimationFrame(first);
            cancelAnimationFrame(second);
        };
    }, [open, mounted, employeeId]);

    /* Человек ушёл из списка — повышен, переведён, удалён: окно закрывается
       само. Пока список перечитывается, ждём — строк на это время бывает ноль. */
    React.useEffect(() => {
        if (open && !employee && !loading) onClose();
    }, [open, employee, loading, onClose]);

    React.useEffect(() => {
        if (!mounted) return undefined;
        const onResize = () => setMaxStage(maxStageHeight());
        window.addEventListener('resize', onResize);
        return () => window.removeEventListener('resize', onResize);
    }, [mounted]);

    /* Высота содержимого каждого экрана: окно подстраивается под активный. */
    React.useLayoutEffect(() => {
        if (!mounted) return undefined;
        const measure = () => {
            const root = rootBodyRef.current ? rootBodyRef.current.offsetHeight : 0;
            const page = pageBodyRef.current ? pageBodyRef.current.offsetHeight : 0;
            setHeights((prev) => (prev.root === root && prev.page === page ? prev : { root, page }));
        };
        measure();
        if (typeof ResizeObserver === 'undefined') return undefined;
        const observer = new ResizeObserver(measure);
        if (rootBodyRef.current) observer.observe(rootBodyRef.current);
        if (pageBodyRef.current) observer.observe(pageBodyRef.current);
        return () => observer.disconnect();
    }, [mounted, renderedPage, employeeId]);

    /* Въезд экрана: стартовая позиция должна попасть в расчёт стилей до
       смены класса, иначе браузер не увидит перехода и экран просто
       появится. */
    React.useLayoutEffect(() => {
        if (!pageKey || pageIn || !pageScrollRef.current) return;
        pageScrollRef.current.getBoundingClientRect();
        setPageIn(true);
        // Фокус — в новый экран: кнопка, открывшая его, осталась под ним.
        pageScrollRef.current.focus({ preventScroll: true });
    }, [pageKey, pageIn, renderedPage]);

    /* Уехавший экран живёт в дереве до конца перехода. */
    React.useEffect(() => {
        if (pageKey || !renderedPage) return undefined;
        const timer = setTimeout(() => setRenderedPage(null), PAGE_MS);
        return () => clearTimeout(timer);
    }, [pageKey, renderedPage]);

    const syncBar = React.useCallback((scroller, isRoot) => {
        const bar = barRef.current;
        if (!bar) return;
        const top = scroller ? scroller.scrollTop : 0;
        if (top > 1) bar.dataset.lined = '1';
        else delete bar.dataset.lined;
        const name = nameRef.current;
        // Имя уехало под шапку — оно появляется в ней, как крупный заголовок iOS.
        if (isRoot && name && top > name.offsetTop + name.offsetHeight - 6) bar.dataset.title = '1';
        else delete bar.dataset.title;
    }, []);

    const pushPage = (key) => {
        const def = pageDef(key);
        if (!def || !person) return;
        def.onOpen?.(person);
        setConfirmKey(null);
        setRenderedPage(key);
        setPageKey(key);
        setPageIn(false);
        syncBar(null, false);
    };

    const popPage = React.useCallback(() => {
        if (!pageKey) return;
        pageDef(pageKey)?.onLeave?.();
        const leftKey = pageKey;
        setPageKey(null);
        setPageIn(false);
        syncBar(rootScrollRef.current, true);
        // Фокус — обратно на кнопку, которая открыла экран.
        requestAnimationFrame(() => {
            panelRef.current?.querySelector(`[data-ecs-action="${leftKey}"]`)?.focus({ preventScroll: true });
        });
    }, [pageDef, pageKey, syncBar]);

    const requestClose = React.useCallback(() => {
        if (pageKey) pageDef(pageKey)?.onLeave?.();
        onClose();
    }, [onClose, pageDef, pageKey]);

    /* Экран с формой (правка, перевод) по щелчку мимо окна не закрывается:
       несохранённое терялось бы от промаха мышью. Окно лишь вздрагивает —
       как лист в macOS, — ответ «я здесь, закройте крестиком». */
    const onDimDown = () => {
        if (pageKey && pageDef(pageKey)?.guard) {
            panelRef.current?.animate?.(
                [{ transform: 'scale(1)' }, { transform: 'scale(1.012)' }, { transform: 'scale(1)' }],
                { duration: 260, easing: 'ease-out' },
            );
            return;
        }
        requestClose();
    };

    React.useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => {
            const panel = panelRef.current;
            if (!panel) return;
            if (event.key === 'Escape') {
                if (event.defaultPrevented) return;
                /* Раскрытый выбор (список, календарь) или кадр фото закрываются
                   своим Escape — окно этот нажим не забирает. */
                if (panel.querySelector('[aria-expanded="true"]:not([data-ecs]), [data-uem-crop]')) return;
                // Заполненный поиск Escape сначала очищает — как поле поиска в macOS.
                const active = document.activeElement;
                if (active?.matches?.('input[type="search"]') && active.value) return;
                event.preventDefault();
                event.stopPropagation();
                if (confirmKey) setConfirmKey(null);
                else if (pageKey) popPage();
                else requestClose();
                return;
            }
            if (event.key !== 'Tab' || !panel.contains(document.activeElement)) return;
            const items = Array.from(panel.querySelectorAll(FOCUSABLE))
                .filter((node) => !node.closest('[inert]') && node.getClientRects().length > 0);
            if (!items.length) return;
            const first = items[0];
            const last = items[items.length - 1];
            if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [open, confirmKey, pageKey, popPage, requestClose]);

    if (!mounted || !person || typeof document === 'undefined') return null;

    const actions = actionsFor ? actionsFor(person) : [];
    const tiles = actions.filter((action) => !action.danger);
    const dangers = actions.filter((action) => action.danger);
    const confirmAction = actions.find((action) => action.key === confirmKey && action.confirm) || null;
    if (confirmAction) lastConfirmRef.current = confirmAction;
    const shownConfirm = confirmAction || lastConfirmRef.current;

    const runAction = (action) => {
        if (action.page) {
            pushPage(action.page);
            return;
        }
        if (action.confirm) {
            setConfirmKey((current) => (current === action.key ? null : action.key));
            return;
        }
        action.onClick?.();
    };

    const confirmRun = async (action) => {
        setRunningKey(action.key);
        try {
            await action.onClick?.();
        } finally {
            if (aliveRef.current) {
                setRunningKey(null);
                setConfirmKey(null);
            }
        }
    };

    const code = statusCodeOf(person?.status);
    const statusTone = code === 'working' ? '' : (code === 'fired' || code === 'dismissal' ? ' is-danger' : ' is-warn');
    /* Набор полей — по отделу самого человека (columnsFor получает его вторым
       аргументом): у карточки один человек, а не строки разных отделов. */
    const sections = buildCardSections(
        cardSectionList,
        (sectionKey) => columnsFor(sectionKey, person),
        (column) => renderValue(column, person),
    );
    /* В шапке — то, чего нет в полях ниже: направление оператора уже стоит
       строкой в «Общем», и повтор был бы тем же словом дважды. */
    const fieldTexts = new Set(sections.flatMap((section) => section.fields).map((field) => field.value));
    const subtitle = (subtitleOf ? subtitleOf(person) : '')
        .split(' · ')
        .filter((part) => part && !fieldTexts.has(part))
        .join(' · ');

    const activeDef = renderedPage ? pageDef(renderedPage) : null;
    const tone = pageKey && activeDef?.tone === 'plain' ? 'plain' : 'grouped';
    const stageHeight = Math.max(MIN_STAGE, Math.min(maxStage, pageKey ? heights.page : heights.root));
    const busy = Boolean(runningKey);

    const renderTile = (action) => {
        const armed = confirmKey === action.key;
        return (
            <button
                key={action.key}
                type="button"
                data-ecs=""
                data-ecs-action={action.page || action.key}
                className={`ecs-tile${armed ? ' is-armed' : ''}`}
                onClick={() => runAction(action)}
                disabled={action.disabled || busy}
                aria-expanded={action.confirm ? armed : undefined}
                title={action.label}
            >
                <Glyph name={action.icon} />
                <span className="ecs-tile-label">{action.short || action.label}</span>
            </button>
        );
    };

    const tileConfirmOpen = Boolean(confirmAction && !confirmAction.danger);
    const dangerConfirmOpen = Boolean(confirmAction && confirmAction.danger);
    const renderConfirm = (isOpen, wantDanger) => (
        <Reveal open={isOpen}>
            {shownConfirm && Boolean(shownConfirm.danger) === wantDanger && (
                <Confirm
                    action={shownConfirm}
                    busy={runningKey === shownConfirm.key}
                    onCancel={() => setConfirmKey(null)}
                    onConfirm={() => confirmRun(shownConfirm)}
                />
            )}
        </Reveal>
    );

    return createPortal(
        /* Метки окна телефонной оболочки здесь нет намеренно: карточка живёт
           только на компьютере, на телефоне у раздела своя (EmployeesMobileView). */
        <div className={`ecs${leaving ? ' is-leaving' : ''}`}>
            <div className="ecs-dim" onMouseDown={onDimDown} aria-hidden="true" />
            <div
                ref={panelRef}
                className="ecs-panel"
                data-tone={tone}
                role="dialog"
                aria-modal="true"
                aria-label={person?.name || 'Сотрудник'}
                tabIndex={-1}
            >
                <div ref={barRef} className={`ecs-bar${pageKey ? ' is-page' : ''}`}>
                    <div className="ecs-bar-side">
                        <button
                            type="button"
                            className="ecs-round ecs-back"
                            onClick={popPage}
                            aria-label="Назад"
                            tabIndex={pageKey ? 0 : -1}
                            aria-hidden={pageKey ? undefined : 'true'}
                        >
                            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                                <path d="M10 2.5L4.5 8l5.5 5.5" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                        </button>
                    </div>
                    <div className="ecs-bar-center">
                        <div className="ecs-bar-title is-root" aria-hidden="true">{person?.name}</div>
                        <div className="ecs-bar-title is-page">
                            <span className="ecs-bar-main">{activeDef?.title || ''}</span>
                            <span className="ecs-bar-sub">{person?.name}</span>
                        </div>
                    </div>
                    <div className="ecs-bar-side is-end">
                        <button type="button" className="ecs-round ecs-close" onClick={requestClose} aria-label="Закрыть">
                            <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                                <path d="M1.5 1.5l9 9M10.5 1.5l-9 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                            </svg>
                        </button>
                    </div>
                </div>

                <div className={`ecs-stage${ready ? ' is-ready' : ''}`} style={{ height: `${stageHeight}px` }}>
                    <div
                        ref={rootScrollRef}
                        className={`ecs-page is-root thin-scroll${pageIn ? ' is-under' : ''}`}
                        onScroll={(event) => syncBar(event.currentTarget, true)}
                        inert={pageKey ? '' : undefined}
                        aria-hidden={pageKey ? 'true' : undefined}
                    >
                        <div ref={rootBodyRef} className="ecs-body">
                            <div className="ecs-hero">
                                <Photo person={person} Avatar={Avatar} />
                                <div ref={nameRef} className="ecs-name" role="heading" aria-level={2}>{person?.name || '—'}</div>
                                <div className="ecs-sub">
                                    {subtitle && <span>{subtitle}</span>}
                                    <span className={statusTone ? statusTone.trim() : undefined}>
                                        {statusLabelOf(person?.status)}{isBlacklist(person) ? ' · ЧС' : ''}
                                    </span>
                                </div>
                            </div>

                            {tiles.length > 0 && (
                                <div className="ecs-tiles" style={{ gridTemplateColumns: `repeat(${tiles.length}, minmax(0, 1fr))` }}>
                                    {tiles.map(renderTile)}
                                </div>
                            )}
                            {renderConfirm(tileConfirmOpen, false)}

                            {sections.map((section) => (
                                <section key={section.key} className="ecs-group" aria-label={section.title}>
                                    <div className="ecs-caption">{section.title}</div>
                                    <dl className="ecs-list">
                                        {section.fields.map((field) => (
                                            <div key={field.key} className="ecs-item ecs-field">
                                                <dt className="ecs-label">{field.label}</dt>
                                                <dd className="ecs-value">{field.value}</dd>
                                            </div>
                                        ))}
                                    </dl>
                                </section>
                            ))}

                            {/* Опасное — отдельной группой в самом низу, чтобы его не
                                задеть вместо «Изменить». */}
                            {dangers.length > 0 && (
                                <section className="ecs-group">
                                    <ul className="ecs-list">
                                        {dangers.map((action) => (
                                            <li key={action.key} className="ecs-item">
                                                <button
                                                    type="button"
                                                    data-ecs=""
                                                    data-ecs-action={action.key}
                                                    className={`ecs-row-btn is-danger${confirmKey === action.key ? ' is-armed' : ''}`}
                                                    onClick={() => runAction(action)}
                                                    disabled={action.disabled || busy}
                                                    aria-expanded={action.confirm ? confirmKey === action.key : undefined}
                                                >
                                                    {action.label}
                                                </button>
                                            </li>
                                        ))}
                                    </ul>
                                    {renderConfirm(dangerConfirmOpen, true)}
                                </section>
                            )}
                        </div>
                    </div>

                    {renderedPage && (
                        <div
                            key={renderedPage}
                            ref={pageScrollRef}
                            className={`ecs-page is-sub thin-scroll${activeDef?.tone === 'plain' ? ' is-plain' : ''}${pageIn ? ' is-in' : ''}`}
                            onScroll={(event) => syncBar(event.currentTarget, false)}
                            inert={pageKey ? undefined : ''}
                            tabIndex={-1}
                        >
                            <div ref={pageBodyRef} className="ecs-body">
                                <React.Suspense fallback={<div className="ecs-loading"><Spinner /></div>}>
                                    {renderedPage === 'history'
                                        ? (
                                            <HistoryPage
                                                key={person?.id}
                                                employeeId={person?.id}
                                                load={loadHistory}
                                                cache={historyCacheRef.current}
                                                valueOf={historyValueOf}
                                            />
                                        )
                                        : activeDef?.render?.({ employee: person, back: popPage })}
                                </React.Suspense>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>,
        document.body,
    );
}
