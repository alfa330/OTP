import React from 'react';
import { buildCardSections } from './employeesPhoneList';
import { historyFieldLabel } from '../modals/historyFieldLabels';
import { runPageTransition, usesViewTransitions } from './pageTransition';
import './employee-card-page.css';

/**
 * Страница сотрудника на компьютере — «Учет сотрудников».
 *
 * 30.09.2026 владелец дважды: сперва «три точки» в конце строки убрать, по
 * нажатию на строку — карточка для чтения, а «Править», «В супервайзеры»,
 * «История» — её кнопки; потом «окно-модалка слишком маленькое — без модалки,
 * переход как на страницу и кнопка назад, чтобы там была вся информация».
 *
 * Поэтому это страница раздела, а не окно поверх него: список уступает ей
 * место, сверху липкая шапка «‹ Сотрудники», ниже шапка человека с действиями
 * и все четыре набора полей таблицы разом — в две колонки, как в «Настройках»
 * macOS. «Изменить», «История», «В операторы» — страницы следующего уровня
 * («‹ Имя»), вопрос перед действием («Повысить?», «Удалить?») раскрывается на
 * самой странице. Ни одного окна поверх.
 *
 * Действия — тот же список, что у телефона (actionsFor в App.jsx): `page` —
 * страница следующего уровня, `confirm` — вопрос перед запуском, `danger` —
 * отдельная красная группа внизу. Страницы правки и перевода рисует App.jsx
 * (`pages`), историю — эта страница сама. Прокрутку раздела (main-content)
 * отдаёт getScrollRoot: список и страница живут в одном прокручиваемом поле.
 */

const HISTORY_PAGE = { title: 'История изменений' };

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
        return <div className="ecp-loading"><Spinner /></div>;
    }
    if (state.status === 'error') {
        return (
            <div className="ecp-empty">
                Не удалось загрузить историю.
                <button
                    type="button"
                    className="ecp-link"
                    onClick={() => { setState({ status: 'loading', items: [] }); setAttempt((n) => n + 1); }}
                >
                    Повторить
                </button>
            </div>
        );
    }

    return (
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
                <section key={group.key} className="ecp-group">
                    <div className="ecp-caption">{group.label}</div>
                    <ul className="ecp-list">
                        {group.items.map((item) => (
                            <li key={item.id} className="ecp-item ecp-hist-item">
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

    const [pageKey, setPageKey] = React.useState(null);
    /* Номер входа на страницу — ключ её разметки. Каждый вход — новый
       экземпляр: «Отмена» в правке и сразу «Изменить» не вернут отменённый
       черновик, а экран въезжает заново. */
    const [pageSeq, setPageSeq] = React.useState(0);
    const [direction, setDirection] = React.useState('forward');
    const [confirmKey, setConfirmKey] = React.useState(null);
    const [runningKey, setRunningKey] = React.useState(null);

    const pageKeyRef = React.useRef(null);
    const pageSeqRef = React.useRef(0);
    pageKeyRef.current = pageKey;
    pageSeqRef.current = pageSeq;
    const rootRef = React.useRef(null);
    const barRef = React.useRef(null);
    const titleRef = React.useRef(null);
    const headingRef = React.useRef(null);
    const cardScrollRef = React.useRef(0);
    const lastConfirmRef = React.useRef(null);
    const historyCacheRef = React.useRef(new Map());
    const aliveRef = React.useRef(true);
    React.useEffect(() => () => { aliveRef.current = false; }, []);

    const pageDef = React.useCallback(
        (key) => (key === 'history' ? HISTORY_PAGE : (pages && pages[key]) || null),
        [pages],
    );
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
        if (key && key !== 'history') pagesRef.current?.[key]?.onLeave?.();
    }, []);

    /* Человек ушёл из списка — повышен, переведён, удалён: страница уходит
       сама. Пока список перечитывается, ждём — строк на это время бывает ноль. */
    React.useEffect(() => {
        if (!employee && !loading) onBack();
    }, [employee, loading, onBack]);

    /* Новый человек — страница с начала. */
    React.useLayoutEffect(() => {
        setPageKey(null);
        setConfirmKey(null);
        setRunningKey(null);
        setDirection('forward');
    }, [employeeId]);

    /* Смена уровня: вглубь — к началу страницы, назад — туда, где человек был
       на карточке. Фокус — на заголовок: с клавиатуры и для читалки это и есть
       «я на новой странице». */
    React.useLayoutEffect(() => {
        const root = scrollRoot();
        if (root) root.scrollTop = pageKey ? 0 : (direction === 'back' ? cardScrollRef.current : 0);
        headingRef.current?.focus({ preventScroll: true });
    }, [pageKey, pageSeq, direction, scrollRoot]);

    /* Имя или заголовок уехали под шапку — они появляются в ней мелко, а под
       шапкой проступает линия, как у крупного заголовка iOS. */
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
    }, [pageKey, pageSeq, employeeId]);

    const pushPage = (key) => {
        const def = pageDef(key);
        if (!def || !person) return;
        cardScrollRef.current = scrollRoot()?.scrollTop || 0;
        runPageTransition('forward', () => {
            def.onOpen?.(person);
            setConfirmKey(null);
            setDirection('forward');
            setPageKey(key);
            setPageSeq((seq) => seq + 1);
        });
    };

    /* По ref, а не по замыканию: «назад» зовут и из долгих операций страницы
       (сохранение формы), а к их концу на экране может быть уже другое. */
    const popPage = React.useCallback(() => {
        const leftKey = pageKeyRef.current;
        if (!leftKey) return;
        runPageTransition('back', () => {
            pageDef(leftKey)?.onLeave?.();
            setDirection('back');
            setPageKey(null);
            setPageSeq((seq) => seq + 1);
        });
    }, [pageDef]);

    /* «Назад» для страницы — только пока на экране ИМЕННО этот её вход: форма,
       сохранявшаяся, пока человек ушёл на «Историю» или открыл правку заново,
       по окончании не снимет чужую страницу. */
    const backFrom = (seq) => () => {
        if (pageSeqRef.current === seq && pageKeyRef.current) popPage();
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
            /* Со страницы формы Escape не уводит: несохранённое терялось бы от
               одной клавиши. Уходят оттуда кнопкой «‹» или «Отмена». */
            if (pageKey && pageDef(pageKey)?.guard) return;
            event.preventDefault();
            goBack();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [confirmKey, pageKey, pageDef, goBack]);

    if (!person) return null;

    const actions = actionsFor ? actionsFor(person) : [];
    const regular = actions.filter((action) => !action.danger);
    const dangers = actions.filter((action) => action.danger);
    const confirmAction = actions.find((action) => action.key === confirmKey && action.confirm) || null;
    if (confirmAction) lastConfirmRef.current = confirmAction;
    const shownConfirm = confirmAction || lastConfirmRef.current;
    const busy = Boolean(runningKey);

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
    const statusTone = code === 'working' ? '' : (code === 'fired' || code === 'dismissal' ? 'is-danger' : 'is-warn');
    /* Набор полей — по отделу самого человека (columnsFor получает его вторым
       аргументом): на странице один человек, а не строки разных отделов. */
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

    const renderCard = () => (
        <>
            <header className="ecp-hero">
                <Photo person={person} Avatar={Avatar} />
                <div className="ecp-hero-main">
                    <h1 ref={(node) => { titleRef.current = node; headingRef.current = node; }} className="ecp-name" tabIndex={-1}>
                        {person?.name || '—'}
                    </h1>
                    <div className="ecp-sub">
                        {subtitle && <span>{subtitle}</span>}
                        <span className={statusTone || undefined}>
                            {statusLabelOf(person?.status)}{isBlacklist(person) ? ' · ЧС' : ''}
                        </span>
                    </div>
                </div>
                {regular.length > 0 && (
                    <div className="ecp-actions">
                        {regular.map((action, index) => {
                            const armed = confirmKey === action.key;
                            return (
                                <button
                                    key={action.key}
                                    type="button"
                                    data-ecp=""
                                    className={`ecp-btn${index === 0 ? ' is-primary' : ' is-tinted'}${armed ? ' is-armed' : ''}`}
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
            {renderConfirm(Boolean(confirmAction && !confirmAction.danger), false)}

            <div className="ecp-sections">
                {sections.map((section) => (
                    <section key={section.key} className="ecp-group" aria-label={section.title}>
                        <div className="ecp-caption">{section.title}</div>
                        <dl className="ecp-list">
                            {section.fields.map((field) => (
                                <div key={field.key} className="ecp-item ecp-field">
                                    <dt className="ecp-label">{field.label}</dt>
                                    <dd className="ecp-value">{field.value}</dd>
                                </div>
                            ))}
                        </dl>
                    </section>
                ))}
            </div>

            {/* Опасное — отдельной группой в самом низу, чтобы его не задеть
                вместо «Изменить». */}
            {dangers.length > 0 && (
                <section className="ecp-group ecp-danger">
                    <ul className="ecp-list">
                        {dangers.map((action) => (
                            <li key={action.key} className="ecp-item">
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
        </>
    );

    const activeDef = pageKey ? pageDef(pageKey) : null;
    const renderSubpage = () => (
        <div className={`ecp-narrow${activeDef?.tone === 'plain' ? ' is-plain' : ''}`}>
            <h1 ref={(node) => { titleRef.current = node; headingRef.current = node; }} className="ecp-large-title" tabIndex={-1}>
                {activeDef?.title}
            </h1>
            <div className="ecp-subpage-body">
                <React.Suspense fallback={<div className="ecp-loading"><Spinner /></div>}>
                    {pageKey === 'history'
                        ? (
                            <HistoryPage
                                key={person?.id}
                                employeeId={person?.id}
                                load={loadHistory}
                                cache={historyCacheRef.current}
                                valueOf={historyValueOf}
                            />
                        )
                        : activeDef?.render?.({ employee: person, back: backFrom(pageSeq) })}
                </React.Suspense>
            </div>
        </div>
    );

    /* Сдвиг экранов делает переход браузера (pageTransition.js); где его нет —
       экран проявляется своей CSS-анимацией. Обе сразу — было бы два движения. */
    const screenMotion = usesViewTransitions() ? '' : ` is-animated is-${direction}`;

    return (
        <div ref={rootRef} className="ecp">
            <div ref={barRef} className="ecp-bar">
                <button type="button" className="ecp-back" onClick={goBack}>
                    <svg width="12" height="20" viewBox="0 0 12 20" fill="none" aria-hidden="true">
                        <path d="M10 2L2 10l8 8" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    <span className="ecp-back-label">{pageKey ? person?.name : backLabel}</span>
                </button>
                <div className="ecp-bar-title" aria-hidden="true">{pageKey ? activeDef?.title : person?.name}</div>
                <span aria-hidden="true" />
            </div>
            <div
                key={`${pageKey || 'card'}:${pageSeq}`}
                className={`ecp-screen${screenMotion}`}
            >
                <div className="ecp-content">
                    {pageKey ? renderSubpage() : renderCard()}
                </div>
            </div>
        </div>
    );
}
