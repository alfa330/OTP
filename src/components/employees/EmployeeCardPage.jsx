import React from 'react';
import { buildCardSections } from './employeesPhoneList';
import { historyFieldLabel } from '../modals/historyFieldLabels';
import { runPageTransition, usesViewTransitions } from './pageTransition';
import './employee-card-page.css';

/**
 * Страница сотрудника на компьютере — «Учет сотрудников».
 *
 * 30.09.2026 владелец за день прошёл путь от «три точки убрать, по строке —
 * карточка» через «без модалки, переход как на страницу» к выбору из пяти
 * макетов: вариант 5, «дашборд», с правками — «Связаться» слева, «Изменить»
 * и история — в том же стиле, а не отдельными экранами.
 *
 * Поэтому это страница раздела (список уступает ей место) такого вида:
 *   • шапка — фото, имя, «Изменить» и перевод;
 *   • строка итогов — статус, сколько работает, ставка, руководитель;
 *   • слева «Связаться» (кнопки и все контакты) и последние изменения,
 *     справа «Сведения» с переключателем «Сведения / История»;
 *   • «Изменить» — та же страница в режиме правки, как в «Контактах» iOS:
 *     те же карточки становятся полями, «Отмена» и «Сохранить» — в липкой
 *     шапке на месте «‹ Назад»;
 *   • вопрос перед действием («Повысить?», «Удалить?») — прямо на странице.
 *
 * Действия — тот же список, что у телефона (actionsFor в App.jsx): `page` —
 * «edit» (режим правки), «history» (вкладка «История»), остальные — страница
 * следующего уровня («В операторы»); `confirm` — вопрос перед запуском,
 * `danger` — красная группа внизу слева. Форму правки и перевода рисует
 * App.jsx (`pages`). Прокрутку раздела (main-content) отдаёт getScrollRoot.
 */

const GLYPHS = {
    edit: (
        <>
            <path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.36a.5.5 0 0 0 .62.62l4.36-1.32a2 2 0 0 0 .83-.5z" />
            <path d="M15 5l4 4" />
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
    phone: (
        <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z" />
    ),
    whatsapp: (
        <>
            <path d="M3 21l1.7-4.9A8.5 8.5 0 1 1 8 19.4z" />
            <path d="M9 9.5c0 3 2.5 5.5 5.5 5.5l1.5-1.5-2-1-1 1c-1-.5-2-1.5-2.5-2.5l1-1-1-2z" />
        </>
    ),
    telegram: <path d="M21 4L3 11l6 2 2 6 3-4 5 4z" />,
    mail: (
        <>
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="M3 7l9 6 9-6" />
        </>
    ),
};

const Glyph = ({ name }) => (
    <svg className="ecp-glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {GLYPHS[name] || GLYPHS.edit}
    </svg>
);

const Spinner = ({ small = false }) => <span className={`ecp-spinner${small ? ' is-small' : ''}`} aria-hidden="true" />;

const Photo = ({ person, Avatar }) => {
    const name = String(person?.name || '').trim();
    return (
        <span className="ecp-photo" aria-hidden="true">
            {person?.avatar_url
                ? <Avatar src={person.avatar_url} alt="" className="ecp-photo-img" />
                : (name.charAt(0).toUpperCase() || '?')}
        </span>
    );
};

/* Раскрытие по высоте без замера: сетка из одной строки 0fr → 1fr. Пока
   закрыто, содержимое инертно — Tab не должен попадать в невидимые кнопки. */
const Reveal = ({ open, children }) => (
    <div className={`ecp-reveal${open ? ' is-open' : ''}`} inert={open ? undefined : ''}>
        <div className="ecp-reveal-inner">{children}</div>
    </div>
);

const Confirm = ({ action, busy, onCancel, onConfirm }) => (
    <div className="ecp-confirm">
        <p className="ecp-confirm-note">{action.confirm.note}</p>
        <div className="ecp-confirm-btns">
            <button type="button" className="ecp-btn" onClick={onCancel} disabled={busy}>Отмена</button>
            <button
                type="button"
                className={`ecp-btn ${action.danger ? 'is-danger' : 'is-primary'}`}
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

/* Историю читают двое — последние изменения слева и вкладка «История», — а
   запрос один: пока он в пути, второй читатель ждёт тот же ответ. */
const readHistory = (cache, id, load) => {
    const pendingKey = `pending:${id}`;
    if (cache.has(pendingKey)) return cache.get(pendingKey);
    const request = Promise.resolve()
        .then(() => load(id))
        .then((items) => {
            const list = Array.isArray(items) ? items : [];
            cache.set(id, list);
            return list;
        })
        .finally(() => cache.delete(pendingKey));
    cache.set(pendingKey, request);
    return request;
};

/* Функция загрузки — из App.jsx и может меняться на каждый рендер; в
   зависимостях эффекта она перезапрашивала бы историю бесконечно. */
const useHistory = (employeeId, load, cache, refreshKey) => {
    const loadRef = React.useRef(load);
    loadRef.current = load;
    const [state, setState] = React.useState(() => (
        cache.has(employeeId) ? { status: 'ready', items: cache.get(employeeId) } : { status: 'loading', items: [] }
    ));
    const [attempt, setAttempt] = React.useState(0);
    React.useEffect(() => {
        let alive = true;
        readHistory(cache, employeeId, (id) => loadRef.current(id))
            .then((list) => { if (alive) setState({ status: 'ready', items: list }); })
            // Уже показанная история лучше плашки об ошибке обновления.
            .catch(() => { if (alive) setState((prev) => (prev.status === 'ready' ? prev : { status: 'error', items: [] })); });
        return () => { alive = false; };
    }, [employeeId, cache, refreshKey, attempt]);
    const retry = () => { setState({ status: 'loading', items: [] }); setAttempt((n) => n + 1); };
    return [state, retry];
};

const historyEntries = (items, valueOf) => {
    const now = new Date();
    return items.map((entry, index) => {
        const stamp = parseStamp(entry?.changed_at);
        return {
            id: entry?.id ?? index,
            dayKey: stamp ? `${stamp.y}-${stamp.m}-${stamp.d}` : 'none',
            day: stamp ? dayLabel(stamp, now) : 'Без даты',
            time: stamp ? stamp.time : String(entry?.changed_at || ''),
            label: historyFieldLabel(entry?.field),
            field: entry?.field,
            oldValue: shownValue(entry?.old_value, entry?.field, valueOf),
            newValue: shownValue(entry?.new_value, entry?.field, valueOf),
            rawOld: entry?.old_value,
            rawNew: entry?.new_value,
            who: String(entry?.changed_by || '').trim(),
            at: entry?.changed_at,
        };
    });
};

const HistoryState = ({ state, retry, children }) => {
    if (state.status === 'loading') return <div className="ecp-loading"><Spinner /></div>;
    if (state.status === 'error') {
        return (
            <div className="ecp-empty">
                Не удалось загрузить историю.
                <button type="button" className="ecp-link" onClick={retry}>Повторить</button>
            </div>
        );
    }
    return children;
};

/* Последние изменения — в левой колонке, строкой «что → стало» и кто когда. */
function HistoryPreview({ employeeId, load, cache, valueOf, refreshKey, onShowAll }) {
    const [state, retry] = useHistory(employeeId, load, cache, refreshKey);
    const entries = historyEntries(state.items.slice(0, 4), valueOf);
    return (
        <HistoryState state={state} retry={retry}>
            {entries.length === 0 ? (
                <div className="ecp-panel-empty">Изменений пока не было</div>
            ) : (
                <ul className="ecp-feed">
                    {entries.map((item) => (
                        <li key={item.id} className="ecp-feed-item">
                            <span className="ecp-feed-dot" aria-hidden="true" />
                            <div className="ecp-feed-main">
                                <div className="ecp-feed-what">{item.label}: {item.oldValue} → {item.newValue}</div>
                                <div className="ecp-feed-when">{[`${item.day}${item.time ? `, ${item.time}` : ''}`, item.who].filter(Boolean).join(' · ')}</div>
                            </div>
                        </li>
                    ))}
                </ul>
            )}
            {state.items.length > 0 && (
                <button type="button" className="ecp-link ecp-panel-more" onClick={onShowAll}>
                    Вся история · {state.items.length}
                </button>
            )}
        </HistoryState>
    );
}

/* Вся история — вкладка «История» большой карточки: поиск и дни. */
function HistoryList({ employeeId, load, cache, valueOf, refreshKey }) {
    const [state, retry] = useHistory(employeeId, load, cache, refreshKey);
    const [query, setQuery] = React.useState('');
    const q = query.trim().toLowerCase();
    const groups = React.useMemo(() => {
        const list = [];
        const byKey = new Map();
        historyEntries(state.items, valueOf).forEach((item) => {
            if (q) {
                const haystack = [item.label, item.field, item.oldValue, item.newValue, item.rawOld, item.rawNew, item.who, item.at]
                    .map((value) => (value == null ? '' : String(value).toLowerCase()));
                if (!haystack.some((value) => value.includes(q))) return;
            }
            if (!byKey.has(item.dayKey)) {
                const group = { key: item.dayKey, label: item.day, items: [] };
                byKey.set(item.dayKey, group);
                list.push(group);
            }
            byKey.get(item.dayKey).items.push(item);
        });
        return list;
    }, [state.items, q, valueOf]);

    return (
        <HistoryState state={state} retry={retry}>
            <div className="ecp-hist">
                {state.items.length > 0 && (
                    <label className="ecp-search">
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
                    <div className="ecp-empty">
                        {q ? `Ничего не найдено по запросу «${query.trim()}»` : 'Изменений пока не было'}
                    </div>
                ) : groups.map((group) => (
                    <section key={group.key} className="ecp-hist-day">
                        <div className="ecp-caption">{group.label}</div>
                        <ul className="ecp-hist-list">
                            {group.items.map((item) => (
                                <li key={item.id} className="ecp-hist-item">
                                    <div className="ecp-hist-line">
                                        <span className="ecp-hist-field">{item.label}</span>
                                        {item.time && <span className="ecp-hist-time">{item.time}</span>}
                                    </div>
                                    <div className="ecp-hist-change">
                                        <span className="ecp-hist-old">{item.oldValue}</span>
                                        <svg className="ecp-hist-arrow" viewBox="0 0 16 16" fill="none" aria-label="стало">
                                            <path d="M3 8h9.5M9 4.5L12.5 8 9 11.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                                        </svg>
                                        <span className="ecp-hist-new">{item.newValue}</span>
                                    </div>
                                    {item.who && <div className="ecp-hist-who">{item.who}</div>}
                                </li>
                            ))}
                        </ul>
                    </section>
                ))}
            </div>
        </HistoryState>
    );
}

/* ── Страница ──────────────────────────────────────────────────────────── */

/* Escape не уводит со страницы, пока человек печатает или выбирает: у поля свой
   Escape (поиск очищается), у раскрытого списка и календаря — свой. */
const isTypingTarget = (node) => Boolean(node?.closest?.('input, textarea, select, [contenteditable="true"]'));

export default function EmployeeCardPage({
    employeeId,
    rows,
    loading = false,
    onBack,
    backLabel = 'Назад',
    getScrollRoot = null,
    cardSectionList,
    columnsFor,
    renderValue,
    subtitleOf = null,
    statusCodeOf,
    statusLabelOf,
    isBlacklist,
    summaryOf = null,
    contactActionsOf = null,
    actionsFor = null,
    pages = null,
    loadHistory = null,
    historyValueOf = null,
    Avatar,
}) {
    const employee = (Array.isArray(rows) ? rows : []).find((row) => Number(row?.id) === Number(employeeId)) || null;
    /* Человек на миг пропадает из строк, пока список перечитывается после
       правки, — страница держит последнего, а не мигает пустотой. */
    const lastEmployeeRef = React.useRef(null);
    if (employee) lastEmployeeRef.current = employee;
    const person = employee || lastEmployeeRef.current;

    /* view — сведения, edit — те же карточки полями. */
    const [mode, setMode] = React.useState('view');
    const [editSeq, setEditSeq] = React.useState(0);
    const [mainTab, setMainTab] = React.useState('info');
    /* Страница следующего уровня («В операторы»). Номер входа — ключ её
       разметки: каждый вход — новый экземпляр. */
    const [pageKey, setPageKey] = React.useState(null);
    const [pageSeq, setPageSeq] = React.useState(0);
    const [direction, setDirection] = React.useState('forward');
    const [confirmKey, setConfirmKey] = React.useState(null);
    const [runningKey, setRunningKey] = React.useState(null);
    /* Место в шапке, куда форма правки кладёт «Отмена» и «Сохранить». */
    const [actionsNode, setActionsNode] = React.useState(null);

    const modeRef = React.useRef(mode);
    const editSeqRef = React.useRef(editSeq);
    const pageKeyRef = React.useRef(null);
    const pageSeqRef = React.useRef(0);
    modeRef.current = mode;
    editSeqRef.current = editSeq;
    pageKeyRef.current = pageKey;
    pageSeqRef.current = pageSeq;
    const rootRef = React.useRef(null);
    const barRef = React.useRef(null);
    const titleRef = React.useRef(null);
    const headingRef = React.useRef(null);
    const viewScrollRef = React.useRef(0);
    const lastConfirmRef = React.useRef(null);
    const historyCacheRef = React.useRef(new Map());
    const aliveRef = React.useRef(true);
    React.useEffect(() => () => { aliveRef.current = false; }, []);

    const pageDef = React.useCallback((key) => (pages && pages[key]) || null, [pages]);
    /* Поле прокрутки и описания страниц App.jsx передаёт новыми на КАЖДЫЙ свой
       рендер. В зависимостях эффекта прокрутки такая функция сбрасывала бы
       страницу к началу и уводила фокус с поля формы при любом тосте или
       ответе справочника — поэтому через ref. */
    const getScrollRootRef = React.useRef(getScrollRoot);
    getScrollRootRef.current = getScrollRoot;
    const pagesRef = React.useRef(pages);
    pagesRef.current = pages;
    const scrollRoot = React.useCallback(
        () => (getScrollRootRef.current && getScrollRootRef.current()) || document.scrollingElement || document.documentElement,
        [],
    );

    /* Страница — от края до края поля раздела, шапка липнет к самому верху.
       Сколько «до края», решает отступ поля, а он разный: p-8 в браузере, ноль
       или безопасная зона в установленном приложении (styles.css, standalone).
       Зашитые 32 px в приложении уводили шапку за верх окна — «‹ Назад» было
       не видно, а слева страница наезжала на сайдбар. */
    React.useLayoutEffect(() => {
        const node = rootRef.current;
        if (!node) return undefined;
        const apply = () => {
            const field = scrollRoot();
            if (!field || field === document.documentElement || field === document.scrollingElement) return;
            const style = window.getComputedStyle(field);
            node.style.setProperty('--ecp-pad-top', style.paddingTop);
            node.style.setProperty('--ecp-pad-right', style.paddingRight);
            node.style.setProperty('--ecp-pad-bottom', style.paddingBottom);
            node.style.setProperty('--ecp-pad-left', style.paddingLeft);
        };
        apply();
        window.addEventListener('resize', apply);
        return () => window.removeEventListener('resize', apply);
    }, [scrollRoot]);

    /* Ушли со страницы мимо «‹» — сайдбаром, из колокола: страница уровня всё
       равно прибирает за собой (перевод в операторы снимает цель — иначе его
       окно всплыло бы уже в другом разделе). */
    React.useEffect(() => () => {
        const key = pageKeyRef.current;
        if (key) pagesRef.current?.[key]?.onLeave?.();
        if (modeRef.current === 'edit') pagesRef.current?.edit?.onLeave?.();
    }, []);

    /* Человек ушёл из списка — повышен, переведён, удалён: страница уходит
       сама. Пока список перечитывается, ждём — строк на это время бывает ноль. */
    React.useEffect(() => {
        if (!employee && !loading) onBack();
    }, [employee, loading, onBack]);

    /* Новый человек — страница с начала. */
    React.useLayoutEffect(() => {
        setMode('view');
        setMainTab('info');
        setPageKey(null);
        setConfirmKey(null);
        setRunningKey(null);
        setDirection('forward');
    }, [employeeId]);

    /* Смена экрана (правка, страница уровня): вглубь — к началу, назад — туда,
       где человек был. Фокус — на заголовок: с клавиатуры и для читалки это
       и есть «я на новом экране». */
    React.useLayoutEffect(() => {
        const root = scrollRoot();
        const deeper = mode === 'edit' || Boolean(pageKey);
        if (root) root.scrollTop = deeper ? 0 : (direction === 'back' ? viewScrollRef.current : 0);
        headingRef.current?.focus({ preventScroll: true });
    }, [mode, editSeq, pageKey, pageSeq, direction, scrollRoot]);

    /* Имя или заголовок уехали под шапку — они появляются в ней мелко. */
    React.useEffect(() => {
        const bar = barRef.current;
        const target = titleRef.current;
        if (!bar || !target || typeof IntersectionObserver === 'undefined') return undefined;
        const observer = new IntersectionObserver(([entry]) => {
            if (entry.isIntersecting) delete bar.dataset.scrolled;
            else bar.dataset.scrolled = '1';
        }, { rootMargin: `-${bar.offsetHeight + 8}px 0px 0px 0px` });
        observer.observe(target);
        return () => observer.disconnect();
    }, [mode, pageKey, pageSeq, employeeId]);

    const rememberViewScroll = () => {
        if (modeRef.current === 'view' && !pageKeyRef.current) viewScrollRef.current = scrollRoot()?.scrollTop || 0;
    };

    const enterEdit = () => {
        const def = pageDef('edit');
        if (!def || !person) return;
        rememberViewScroll();
        runPageTransition('swap', () => {
            def.onOpen?.(person);
            setConfirmKey(null);
            setDirection('forward');
            setMode('edit');
            setEditSeq((seq) => seq + 1);
        });
    };

    /* По ref, а не по замыканию: выход зовут и из долгого сохранения формы, а
       к его концу на экране может быть уже другое. */
    const exitEdit = React.useCallback(() => {
        if (modeRef.current !== 'edit') return;
        runPageTransition('swap', () => {
            pagesRef.current?.edit?.onLeave?.();
            setDirection('back');
            setMode('view');
            setEditSeq((seq) => seq + 1);
        });
    }, []);

    /* «Назад» формы — только пока на экране ИМЕННО этот её вход: сохранение,
       закончившееся после «Отмены» и нового «Изменить», не закроет новую. */
    const exitEditFrom = (seq) => () => {
        if (editSeqRef.current === seq && modeRef.current === 'edit') exitEdit();
    };

    const pushPage = (key) => {
        const def = pageDef(key);
        if (!def || !person) return;
        rememberViewScroll();
        runPageTransition('forward', () => {
            def.onOpen?.(person);
            setConfirmKey(null);
            setDirection('forward');
            setPageKey(key);
            setPageSeq((seq) => seq + 1);
        });
    };

    const popPage = React.useCallback(() => {
        const leftKey = pageKeyRef.current;
        if (!leftKey) return;
        runPageTransition('back', () => {
            pagesRef.current?.[leftKey]?.onLeave?.();
            setDirection('back');
            setPageKey(null);
            setPageSeq((seq) => seq + 1);
        });
    }, []);

    const popPageFrom = (seq) => () => {
        if (pageSeqRef.current === seq && pageKeyRef.current) popPage();
    };

    const showTab = (tab) => {
        setConfirmKey(null);
        setMainTab(tab);
    };

    const goBack = React.useCallback(() => {
        if (pageKeyRef.current) {
            popPage();
            return;
        }
        onBack();
    }, [onBack, popPage]);

    React.useEffect(() => {
        const onKey = (event) => {
            if (event.key !== 'Escape' || event.defaultPrevented) return;
            if (isTypingTarget(document.activeElement)) return;
            /* Окно поверх страницы («Новость дня» и другие) закрывается своим
               Escape — страница под ним с места не двигается. */
            if (document.querySelector('[aria-modal="true"]')) return;
            /* Раскрытый выбор, календарь или кадр фото — тоже. Именно
               всплывающие (aria-haspopup): открытый поиск сайдбара тоже
               aria-expanded, но Escape он не забирает. */
            if (document.querySelector('[aria-expanded="true"][aria-haspopup]:not([data-ecp]), [data-uem-crop]')) return;
            if (confirmKey) {
                event.preventDefault();
                setConfirmKey(null);
                return;
            }
            /* С формы Escape не уводит: несохранённое терялось бы от одной
               клавиши. Уходят оттуда «Отменой». */
            if (mode === 'edit') return;
            if (pageKey && pageDef(pageKey)?.guard) return;
            event.preventDefault();
            if (!pageKey && mainTab === 'history') {
                showTab('info');
                return;
            }
            goBack();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [confirmKey, mode, mainTab, pageKey, pageDef, goBack]);

    if (!person) return null;

    const actions = actionsFor ? actionsFor(person) : [];
    // Историю открывает сама страница (слева и вкладкой) — кнопкой в шапке она не нужна.
    const headActions = actions.filter((action) => !action.danger && action.page !== 'history');
    const dangers = actions.filter((action) => action.danger);
    const confirmAction = actions.find((action) => action.key === confirmKey && action.confirm) || null;
    if (confirmAction) lastConfirmRef.current = confirmAction;
    const shownConfirm = confirmAction || lastConfirmRef.current;
    const busy = Boolean(runningKey);

    const runAction = (action) => {
        if (action.page === 'edit') {
            enterEdit();
            return;
        }
        if (action.page === 'history') {
            showTab('history');
            return;
        }
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

    /* Набор полей — по отделу самого человека (columnsFor получает его вторым
       аргументом): на странице один человек, а не строки разных отделов. */
    const sections = buildCardSections(
        cardSectionList,
        (sectionKey) => columnsFor(sectionKey, person),
        (column) => renderValue(column, person),
    );
    // Контакты живут в «Связаться» — в «Сведениях» второй раз они не нужны.
    const contactFields = sections.find((section) => section.key === 'contacts')?.fields || [];
    const infoSections = sections.filter((section) => section.key !== 'contacts');
    /* Группы — в две колонки, каждая следующая в ту, что короче: CSS-колонки
       шли строго по порядку, и левая оставалась полупустой («Общее» — четыре
       строки, «Данные» — шесть, «Корпоративное» уходило под них). */
    const infoColumns = infoSections.reduce((columns, section) => {
        const target = columns[0].size <= columns[1].size ? columns[0] : columns[1];
        target.sections.push(section);
        target.size += section.fields.length + 1; // +1 — подпись группы
        return columns;
    }, [{ sections: [], size: 0 }, { sections: [], size: 0 }]).filter((column) => column.sections.length > 0);
    /* В шапке — то, чего нет в полях ниже: направление оператора уже стоит
       строкой в «Общем», и повтор был бы тем же словом дважды. */
    const fieldTexts = new Set(sections.flatMap((section) => section.fields).map((field) => field.value));
    const subtitle = (subtitleOf ? subtitleOf(person) : '')
        .split(' · ')
        .filter((part) => part && !fieldTexts.has(part))
        .join(' · ');
    const summary = summaryOf ? summaryOf(person) : [];
    const contactActions = contactActionsOf ? contactActionsOf(person) : [];

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

    const setHeading = (node) => { titleRef.current = node; headingRef.current = node; };

    const renderHead = () => (
        <header className="ecp-head">
            <Photo person={person} Avatar={Avatar} />
            <div className="ecp-head-main">
                <h1 ref={setHeading} className="ecp-name" tabIndex={-1}>{person?.name || '—'}</h1>
                {subtitle && <div className="ecp-sub">{subtitle}</div>}
            </div>
            {mode === 'view' && headActions.length > 0 && (
                <div className="ecp-actions">
                    {headActions.map((action, index) => {
                        const armed = confirmKey === action.key;
                        return (
                            <button
                                key={action.key}
                                type="button"
                                data-ecp=""
                                className={`ecp-btn${index === 0 ? ' is-primary' : ' is-plain'}${armed ? ' is-armed' : ''}`}
                                onClick={() => runAction(action)}
                                disabled={action.disabled || busy}
                                aria-expanded={action.confirm ? armed : undefined}
                                title={action.label}
                            >
                                <Glyph name={action.icon} />
                                {action.short || action.label}
                            </button>
                        );
                    })}
                </div>
            )}
        </header>
    );

    const renderDashboard = () => (
        <>
            {renderConfirm(Boolean(confirmAction && !confirmAction.danger), false)}
            {summary.length > 0 && (
                <div className="ecp-kpis">
                    {summary.map((item) => (
                        <div key={item.key} className="ecp-kpi">
                            <div className="ecp-kpi-label">{item.label}</div>
                            <div className={`ecp-kpi-value${item.tone ? ` is-${item.tone}` : ''}`}>{item.value}</div>
                            {item.note && <div className="ecp-kpi-note">{item.note}</div>}
                        </div>
                    ))}
                </div>
            )}

            <div className="ecp-dash">
                <aside className="ecp-side">
                    <section className="ecp-panel" aria-label="Связаться">
                        <h2 className="ecp-panel-title">Связаться</h2>
                        {contactActions.length > 0 && (
                            <div className="ecp-contact-btns">
                                {contactActions.map((item) => (
                                    <a
                                        key={item.key}
                                        className="ecp-contact-btn"
                                        href={item.href}
                                        target={item.href.startsWith('http') ? '_blank' : undefined}
                                        rel={item.href.startsWith('http') ? 'noopener noreferrer' : undefined}
                                    >
                                        <Glyph name={item.icon} />
                                        {item.label}
                                    </a>
                                ))}
                            </div>
                        )}
                        {contactFields.length > 0 ? (
                            <dl className="ecp-contact-list">
                                {contactFields.map((field) => (
                                    <div key={field.key} className="ecp-contact-row">
                                        <dt>{field.label}</dt>
                                        <dd>{field.value}</dd>
                                    </div>
                                ))}
                            </dl>
                        ) : (
                            <div className="ecp-panel-empty">Контакты не заполнены</div>
                        )}
                    </section>

                    {mainTab === 'info' && (
                        <section className="ecp-panel" aria-label="Последние изменения">
                            <h2 className="ecp-panel-title">Последние изменения</h2>
                            <HistoryPreview
                                key={person?.id}
                                employeeId={person?.id}
                                load={loadHistory}
                                cache={historyCacheRef.current}
                                valueOf={historyValueOf}
                                refreshKey={editSeq}
                                onShowAll={() => showTab('history')}
                            />
                        </section>
                    )}

                    {/* Опасное — отдельной карточкой в самом низу, чтобы его не
                        задеть вместо «Изменить». */}
                    {dangers.length > 0 && (
                        <section className="ecp-panel ecp-danger">
                            <ul className="ecp-danger-list">
                                {dangers.map((action) => (
                                    <li key={action.key}>
                                        <button
                                            type="button"
                                            data-ecp=""
                                            className={`ecp-row-btn is-danger${confirmKey === action.key ? ' is-armed' : ''}`}
                                            onClick={() => runAction(action)}
                                            disabled={action.disabled || busy}
                                            aria-expanded={action.confirm ? confirmKey === action.key : undefined}
                                        >
                                            {action.label}
                                        </button>
                                    </li>
                                ))}
                            </ul>
                            {renderConfirm(Boolean(confirmAction && confirmAction.danger), true)}
                        </section>
                    )}
                </aside>

                <section className="ecp-panel ecp-main" aria-label={mainTab === 'info' ? 'Сведения' : 'История изменений'}>
                    <div className="ecp-seg" role="tablist" aria-label="Раздел">
                        <button type="button" role="tab" data-ecp="" aria-selected={mainTab === 'info'} className={`ecp-seg-btn${mainTab === 'info' ? ' is-on' : ''}`} onClick={() => showTab('info')}>
                            Сведения
                        </button>
                        <button type="button" role="tab" data-ecp="" aria-selected={mainTab === 'history'} className={`ecp-seg-btn${mainTab === 'history' ? ' is-on' : ''}`} onClick={() => showTab('history')}>
                            История
                        </button>
                    </div>
                    <div key={mainTab} className="ecp-main-body">
                        {mainTab === 'info' ? (
                            <div className="ecp-info">
                                {infoColumns.map((column) => (
                                    <div key={column.sections[0].key} className="ecp-info-col">
                                        {column.sections.map((section) => (
                                            <section key={section.key} className="ecp-info-group" aria-label={section.title}>
                                                <div className="ecp-caption">{section.title}</div>
                                                <dl className="ecp-info-list">
                                                    {section.fields.map((field) => (
                                                        <div key={field.key} className="ecp-info-row">
                                                            <dt>{field.label}</dt>
                                                            <dd>{field.value}</dd>
                                                        </div>
                                                    ))}
                                                </dl>
                                            </section>
                                        ))}
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <HistoryList
                                key={person?.id}
                                employeeId={person?.id}
                                load={loadHistory}
                                cache={historyCacheRef.current}
                                valueOf={historyValueOf}
                                refreshKey={editSeq}
                            />
                        )}
                    </div>
                </section>
            </div>
        </>
    );

    const editDef = pageDef('edit');
    const renderEdit = () => (
        <div className="ecp-edit">
            <React.Suspense fallback={<div className="ecp-loading"><Spinner /></div>}>
                {editDef?.render?.({ employee: person, back: exitEditFrom(editSeq), actionsNode })}
            </React.Suspense>
        </div>
    );

    const activeDef = pageKey ? pageDef(pageKey) : null;
    const renderSubpage = () => (
        <div className="ecp-narrow">
            <h1 ref={setHeading} className="ecp-large-title" tabIndex={-1}>{activeDef?.title}</h1>
            <React.Suspense fallback={<div className="ecp-loading"><Spinner /></div>}>
                {activeDef?.render?.({ employee: person, back: popPageFrom(pageSeq) })}
            </React.Suspense>
        </div>
    );

    /* Сдвиг экранов делает переход браузера (pageTransition.js); где его нет —
       экран проявляется своей CSS-анимацией. Обе сразу — было бы два движения. */
    const screenMotion = usesViewTransitions() ? '' : ` is-animated is-${direction}`;
    const editing = mode === 'edit' && !pageKey;
    const barTitle = pageKey ? activeDef?.title : (editing ? editDef?.title : person?.name);

    return (
        <div ref={rootRef} className={`ecp${editing ? ' is-editing' : ''}`}>
            <div ref={barRef} className="ecp-bar">
                <button
                    type="button"
                    className="ecp-back"
                    onClick={goBack}
                    tabIndex={editing ? -1 : 0}
                    aria-hidden={editing ? 'true' : undefined}
                >
                    <svg width="12" height="20" viewBox="0 0 12 20" fill="none" aria-hidden="true">
                        <path d="M10 2L2 10l8 8" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    <span className="ecp-back-label">{pageKey ? person?.name : backLabel}</span>
                </button>
                <div className="ecp-bar-title" aria-hidden="true">{barTitle}</div>
                {/* Сюда форма правки кладёт «Отмена» и «Сохранить». */}
                <div ref={setActionsNode} className="ecp-bar-actions" />
            </div>
            <div
                key={pageKey ? `page:${pageKey}:${pageSeq}` : `${mode}:${editSeq}`}
                className={`ecp-screen${screenMotion}`}
            >
                <div className="ecp-content">
                    {pageKey ? renderSubpage() : (
                        <>
                            {renderHead()}
                            {editing ? renderEdit() : renderDashboard()}
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
