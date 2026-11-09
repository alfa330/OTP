import React, { Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Pencil, Trophy } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import lazyWithRetry from '../../utils/lazyWithRetry';
import { HonorMainCard, HonorTile } from './honorUi';
import {
    DENSE_TILE, HONOR_DEPARTMENTS, defaultDepartmentKey, entryPhoto, honorDepartment, managesHonorBoard, nominationMeta,
    boardLayout, periodLabel, periodPhrase, pickDepartment, splitBoard, tileLayout, toneOf,
} from './honorBoardMeta';

/*
 * «Доска почёта» — вкладка раздела «Ивенты» (задача #318).
 *
 * Лучшие сотрудники месяца по трём направлениям: ОП, СЗоВ и Фронт-офис —
 * переключателем, как просит ТЗ. Месяц — выпадающим списком «Месяц / Год»:
 * в нём только те месяцы, за которые итоги опубликованы, пустых месяцев
 * человек не листает. Доска одна для всех сотрудников.
 *
 * Вид — по образцу к доработке 06.11.2026 («немного добавить цветов и
 * использовать всё пространство»): ключевая номинация «Лучший сотрудник
 * отдела» — широкой золотой карточкой со свечением и короной, остальные —
 * цветными плитками, где человек (лучше всего — на фото без фона, которое
 * загрузил тренер) стоит на нижнем крае. Плитки встают рядами по центру:
 * две номинации не жмутся к левому краю пустой сетки.
 *
 * Итоги ведёт тренер (сервер решает can_manage): кнопка «Заполнить итоги»
 * открывает окно ввода. Окно грузится отдельным чанком — остальным
 * сотрудникам его код не нужен.
 */

const HonorBoardEditor = lazyWithRetry(() => import('./HonorBoardEditor'));

/* Память страницы: последний ответ по каждому месяцу. Повторный заход во
   вкладку рисуется сразу готовым, запрос обновляет его фоном. В хранилище
   браузера не кладём — там имена. */
const memory = new Map();
const memoryKey = (userId, period) => `${userId || 0}|${period || ''}`;

/* «Оболочка» вкладки в хранилище браузера: есть ли опубликованные месяцы и
   ведёт ли человек доску — без имён и итогов. По ней первый кадр сразу ставит
   ряд «месяц / Заполнить итоги» на место, а не вставляет его с ответом
   сервера, сдвигая доску вниз (на телефоне ряд стоит отдельной строкой).
   Самый первый заход с устройства решает по роли из профиля (managesHonorBoard). */
const SHELL_KEY = 'otp.honorBoard.shell';
const readShell = (userId) => {
    try {
        const value = JSON.parse(window.localStorage.getItem(`${SHELL_KEY}:${userId || 0}`) || 'null');
        return value && typeof value === 'object' ? value : null;
    } catch {
        return null;
    }
};
const writeShell = (userId, shell) => {
    try {
        window.localStorage.setItem(`${SHELL_KEY}:${userId || 0}`, JSON.stringify(shell));
    } catch {
        /* приватное окно — заготовка просто будет по умолчанию */
    }
};

const DEPARTMENT_OPTIONS = HONOR_DEPARTMENTS.map((item) => ({ value: item.key, label: item.label }));

/* Ширина плитки — доля ряда (tileLayout в honorBoardMeta.js): ряд идёт ровно
   по краям золотой карточки, неполный последний ряд — по центру (flex-wrap). */
const tileStyle = (layout) => ({
    width: layout.columns > 1 ? `calc((100% - ${layout.gap * (layout.columns - 1)}px) / ${layout.columns})` : '100%',
});

/* Место под доску: ширина в px (колонки считаются от неё, а не от ширины
   экрана — сайдбар бывает раскрыт и свёрнут) и где доска начинается на
   странице (сколько экрана над ней занято шапкой). До отрисовки
   (useLayoutEffect), чтобы первый кадр не встал в чужую сетку и не
   перестроился. Скрытая вкладка даёт 0 — его не берём. */
const useBoardBox = () => {
    const ref = useRef(null);
    const [box, setBox] = useState({ width: 0, top: 0 });
    useLayoutEffect(() => {
        const node = ref.current;
        if (!node) return undefined;
        const update = () => {
            const width = node.clientWidth;
            if (!(width > 0)) return;
            const top = Math.round(node.getBoundingClientRect().top + (window.scrollY || 0));
            setBox((prev) => (Math.abs(prev.width - width) >= 1 || Math.abs(prev.top - top) >= 4 ? { width, top } : prev));
        };
        update();
        window.addEventListener('resize', update);
        if (typeof ResizeObserver !== 'function') return () => window.removeEventListener('resize', update);
        const observer = new ResizeObserver(update);
        observer.observe(node);
        return () => {
            observer.disconnect();
            window.removeEventListener('resize', update);
        };
    }, []);
    return [ref, box];
};

/* Высота экрана устройства (окна браузера): по ней плитки не вырастают выше,
   чем помещается на экране. Меняется с размером окна и поворотом телефона. */
const useViewportHeight = () => {
    const read = () => (typeof window === 'undefined' ? 0 : window.innerHeight || 0);
    const [height, setHeight] = useState(read);
    useEffect(() => {
        const update = () => setHeight((prev) => {
            const next = read();
            return Math.abs(prev - next) >= 1 ? next : prev;
        });
        window.addEventListener('resize', update);
        window.addEventListener('orientationchange', update);
        return () => {
            window.removeEventListener('resize', update);
            window.removeEventListener('orientationchange', update);
        };
    }, []);
    return height;
};

/* Естественная высота блока текста — без растяжки сеткой: поля, строки и
   промежутки между ними. */
const naturalHeight = (node) => {
    const style = window.getComputedStyle(node);
    const kids = [...node.children];
    const gap = parseFloat(style.rowGap) || 0;
    return kids.reduce((sum, kid) => sum + kid.getBoundingClientRect().height, 0)
        + gap * Math.max(0, kids.length - 1)
        + (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
};

/* Замер текста, от которого зависит, уместится ли доска на экране: самое
   длинное описание под плитками первого ряда и текст золотой карточки (она не
   ниже своего текста). После каждой отрисовки, до показа кадра. Текст
   переносится по ширине плиток, а ширину замер не меняет (honorBoardMeta.js:
   maxTileFor) — после замера доска не перестраивается по кругу. */
const useBoardText = (ref) => {
    const [text, setText] = useState({ caption: null, main: null });
    useLayoutEffect(() => {
        const node = ref.current;
        if (!node || !(node.clientWidth > 0)) return;
        const tiles = [...node.querySelectorAll('[data-honor-card=""]')];
        let caption = null;
        if (tiles.length) {
            const tops = tiles.map((tile) => tile.getBoundingClientRect().top);
            const first = Math.min(...tops);
            caption = Math.ceil(Math.max(0, ...tiles
                .filter((_, index) => tops[index] - first < 2)
                .map((tile) => tile.querySelector('[data-honor-caption]')?.getBoundingClientRect().height || 0)));
        }
        const mainText = node.querySelector('[data-honor-main-text]');
        const main = mainText ? Math.ceil(naturalHeight(mainText)) : null;
        setText((prev) => (prev.caption === caption && prev.main === main ? prev : { caption, main }));
    });
    return text;
};

const Bone = ({ className = '' }) => <span className={`block animate-pulse rounded-full bg-slate-200/70 ${className}`} />;

/* Заготовка — те же карточки и в тот же рост, что у настоящей доски: вкладка
   не прыгает, когда приходит ответ. */
const BoardSkeleton = ({ layout }) => (
    <div className="space-y-5" aria-busy="true" aria-label="Загрузка">
        <div className="overflow-hidden rounded-[32px] bg-white ring-1 ring-slate-200/70">
            <div className="grid" style={mainGridStyle(layout)}>
                <span className="block animate-pulse bg-slate-200/60" style={{ minHeight: layout.height }} />
                <div className="flex flex-col justify-center gap-4 px-6 py-7 sm:px-10 sm:py-10">
                    <Bone className="h-7 w-56" />
                    <Bone className="h-9 w-72 max-w-full" />
                    <Bone className="h-4 w-full max-w-lg" />
                    <Bone className="h-4 w-3/4 max-w-md" />
                </div>
            </div>
        </div>
        <div className="flex flex-wrap justify-center" style={{ gap: layout.gap }}>
            {Array.from({ length: layout.columns }).map((_, index) => (
                <div key={index} className="overflow-hidden rounded-[28px] bg-white ring-1 ring-slate-200/70" style={tileStyle(layout)}>
                    <span className="block animate-pulse bg-slate-200/60" style={{ height: layout.height }} />
                    <div className="space-y-2.5 px-5 pb-5 pt-4">
                        <Bone className="h-4 w-full" />
                        <Bone className="h-4 w-2/3" />
                    </div>
                </div>
            ))}
        </div>
    </div>
);

/* Золотая карточка: фото шириной в одну плитку ряда, текст — справа.
   Узкая доска — фото над текстом. */
const mainGridStyle = (layout) => (layout.mainPhotoWidth
    ? { gridTemplateColumns: `${Math.round(layout.mainPhotoWidth)}px minmax(0, 1fr)` }
    : { gridTemplateColumns: 'minmax(0, 1fr)' });

const EmptyCard = ({ title, text, action = null }) => (
    <div className={`${iosCard} flex flex-col items-center px-6 py-14 text-center`}>
        <span className="grid h-16 w-16 place-items-center rounded-[20px] bg-amber-100 text-amber-600">
            <Trophy size={30} strokeWidth={2} aria-hidden="true" />
        </span>
        <h3 className="mt-4 text-[18px] font-semibold text-slate-900">{title}</h3>
        <p className="mt-1.5 max-w-md text-[14.5px] leading-relaxed text-slate-500">{text}</p>
        {action}
    </div>
);

/* focus — {period, nonce}: колокол позвал на доску конкретного месяца. */
export default function HonorBoard({ user, apiRoot, authHeaders, notify, focus = null }) {
    const userId = user?.id;
    // Первый кадр — сразу тот месяц и то направление, куда звал колокол (из
    // памяти страницы, если месяц уже открывали): без мелькания прошлого.
    const initialPeriod = focus?.period || '';
    const [data, setData] = useState(() => memory.get(memoryKey(userId, initialPeriod)) || null);
    const [loading, setLoading] = useState(() => !memory.has(memoryKey(userId, initialPeriod)));
    const [error, setError] = useState('');
    const [departmentKey, setDepartmentKey] = useState(() => {
        const own = defaultDepartmentKey(user);
        const cached = memory.get(memoryKey(userId, initialPeriod));
        return cached ? pickDepartment(own, cached.boards, focus?.departments) : own;
    });
    const [editorOpen, setEditorOpen] = useState(false);
    // Что окно редактирует. Не сбрасывается при закрытии: окно уходит анимацией
    // и до конца ухода показывает ту же доску.
    const [editorTarget, setEditorTarget] = useState(null);
    const [shell] = useState(() => readShell(userId));
    const [boardRef, boardBox] = useBoardBox();
    const viewportHeight = useViewportHeight();
    const boardText = useBoardText(boardRef);
    const ticket = useRef(0);

    /* Вышедшие доски видны — колокол больше не зовёт сюда. Только когда сервер
       сказал, что гасить есть что: заход во вкладку не пишет в базу зря. */
    const markSeen = useCallback(() => {
        axios.post(`${apiRoot}/api/events/honor_board/seen`, {}, { headers: authHeaders() }).catch(() => {});
    }, [apiRoot, authHeaders]);

    /* pick — заход во вкладку и зов колокола: показываем направление, о котором
       звал колокол (своё, если оно среди вышедших), а если у выбранного
       направления доски за месяц нет, а у другого есть, — то, где есть, а не
       пустую заглушку. Направление, выбранное руками, не трогаем. */
    const load = useCallback(async (period = '', { pick = false, wanted = [] } = {}) => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        const cached = memory.get(memoryKey(userId, period));
        if (cached) {
            setData(cached);
            setError('');
            if (pick) setDepartmentKey((current) => pickDepartment(current, cached.boards, wanted));
        }
        setLoading(true);
        try {
            const response = await axios.get(`${apiRoot}/api/events/honor_board`, {
                headers: authHeaders(),
                params: period ? { period } : {},
            });
            if (mine !== ticket.current) return;
            const next = response?.data || {};
            if (next.unseen) markSeen();
            const remembered = { ...next, unseen: false };
            memory.set(memoryKey(userId, period), remembered);
            if (next.period) memory.set(memoryKey(userId, next.period), remembered);
            writeShell(userId, {
                months: Array.isArray(next.periods) && next.periods.length > 0,
                manage: Array.isArray(next.manage_departments) && next.manage_departments.length > 0,
            });
            setData(remembered);
            setError('');
            if (pick) {
                setDepartmentKey((current) => pickDepartment(current, next.boards, wanted));
            }
        } catch (requestError) {
            if (mine !== ticket.current) return;
            if (cached) notify?.('Не удалось обновить доску почёта', 'error');
            else setError(requestError?.response?.data?.error || 'Не удалось загрузить доску почёта');
        } finally {
            if (mine === ticket.current) setLoading(false);
        }
    }, [apiRoot, authHeaders, markSeen, notify, userId]);

    const loadRef = useRef(load);
    loadRef.current = load;
    const focusNonce = focus?.nonce || 0;
    const focusRef = useRef(focus);
    focusRef.current = focus;
    useEffect(() => {
        // Первый заход и каждый новый зов колокола: месяц из уведомления.
        loadRef.current(focusRef.current?.period || '', { pick: true, wanted: focusRef.current?.departments || [] });
    }, [focusNonce]);

    const period = data?.period || '';
    const periods = useMemo(() => (Array.isArray(data?.periods) ? data.periods : []), [data]);
    const periodOptions = useMemo(() => periods.map((value) => ({ value, label: periodLabel(value) })), [periods]);
    // Направления, которые человек ведёт: тренер — все, глава Фронт-офиса — своё.
    const manageDepartments = useMemo(() => (Array.isArray(data?.manage_departments)
        ? data.manage_departments : []), [data]);
    const department = honorDepartment(departmentKey) || HONOR_DEPARTMENTS[0];
    const board = data?.boards?.[department.key] || null;
    const { main, others } = useMemo(() => splitBoard(board), [board]);
    const avatars = data?.photos || {};
    const honorPhotos = data?.honor_photos || {};

    const openEditor = useCallback(() => {
        // Направление на экране, если человек его ведёт, иначе первое своё.
        // Опубликованная доска на экране — её и правим; пусто — итоги прошлого
        // месяца, их и подводят (день по Алматы знает сервер).
        const key = manageDepartments.includes(department.key) ? department.key : manageDepartments[0];
        if (!key) return;
        setEditorTarget({
            period: data?.boards?.[key] ? period : (data?.default_period || period),
            department: key,
        });
        setEditorOpen(true);
    }, [data, department.key, manageDepartments, period]);

    /* Окно опубликовало, сняло или откатило доску — вкладка показывает
       её же: тот месяц и то направление, что в окне. */
    const handleBoardChanged = useCallback(({ period: changedPeriod, department: changedDepartment }) => {
        memory.clear();
        if (changedDepartment) setDepartmentKey(changedDepartment);
        loadRef.current(changedPeriod || '');
    }, []);

    // Без ключевой — только ряды плиток; с ней — раскладка всей доски.
    const screen = { viewport: viewportHeight, top: boardBox.top, caption: boardText.caption, main: boardText.main };
    const layout = main
        ? boardLayout(boardBox.width, others.length, screen)
        : { ...tileLayout(boardBox.width, others.length, screen), inline: false };
    // Доска уже места под неё (плитки не шире maxTile) — стоит по центру.
    const frame = (value) => (value.boardWidth > 0 ? { maxWidth: value.boardWidth, marginInline: 'auto' } : undefined);

    let content;
    if (!data && loading) {
        const skeleton = boardLayout(boardBox.width, 3, { viewport: viewportHeight, top: boardBox.top });
        content = <div style={frame(skeleton)}><BoardSkeleton layout={skeleton} /></div>;
    } else if (!data && error) {
        content = (
            <EmptyCard
                title="Доска почёта не загрузилась"
                text={error}
                action={(
                    <button type="button" className={`${iosBtnSecondary} mt-5`} onClick={() => load(period)}>
                        Повторить
                    </button>
                )}
            />
        );
    } else if (!board) {
        content = periods.length === 0 ? (
            <EmptyCard
                title="Доска почёта пока пуста"
                text="Каждый месяц здесь появляются лучшие сотрудники ОП, СЗоВ и Фронт-офиса"
            />
        ) : (
            <EmptyCard
                title={`Итоги ${periodPhrase(period)} ещё не опубликованы`}
                text={`Как только тренер объявит победителей ${department.genitive}, они появятся здесь`}
            />
        );
    } else {
        const mainCard = main && (
            <HonorMainCard
                entry={main}
                meta={nominationMeta(department.key, main.nomination, main.title, main.icon)}
                tone={toneOf(main, 0)}
                photo={entryPhoto(main, honorPhotos, avatars)}
                gridStyle={mainGridStyle(layout)}
                height={layout.height}
                className={layout.inline ? 'h-full' : ''}
            />
        );
        const tiles = others.map((entry, index) => (
            <HonorTile
                key={`${entry.nomination}-${entry.title}-${index}`}
                style={layout.inline ? { width: layout.tileWidth } : tileStyle(layout)}
                className={layout.inline ? 'shrink-0' : ''}
                height={layout.height}
                dense={layout.tileWidth > 0 && layout.tileWidth < DENSE_TILE}
                entry={entry}
                meta={nominationMeta(department.key, entry.nomination, entry.title, entry.icon)}
                tone={toneOf(entry, index)}
                photo={entryPhoto(entry, honorPhotos, avatars)}
            />
        ));
        content = layout.inline ? (
            // Одна-две свои номинации: золотая карточка и плитки — одним рядом.
            <div className="flex items-stretch" style={{ gap: layout.gap, ...frame(layout) }} data-honor-layout="row">
                <div className="min-w-0" style={{ width: layout.mainWidth }}>{mainCard}</div>
                {tiles}
            </div>
        ) : (
            <div className="flex flex-col" style={{ gap: layout.gap, ...frame(layout) }} data-honor-layout="stack">
                {mainCard}
                {others.length > 0 && <div className="flex flex-wrap justify-center" style={{ gap: layout.gap }}>{tiles}</div>}
            </div>
        );
    }

    /* Столбик — на gap, а не space-y: окно тренера лежит в нём же, и отступ
       space-y сдвинул бы его затемнение на 16 px ниже края экрана. */
    return (
        <div className="flex flex-col gap-5">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <IosSegmented
                    value={department.key}
                    options={DEPARTMENT_OPTIONS}
                    onChange={setDepartmentKey}
                    ariaLabel="Направление"
                    className="w-full sm:w-auto"
                    stretch
                    slide
                />
                <div className="flex items-center gap-2 sm:ml-auto">
                    {/* До ответа сервера — те же элементы того же роста (по
                        оболочке): ряд не появляется с ответом и не сдвигает доску. */}
                    {(data ? periodOptions.length > 0 : shell?.months !== false) && (
                        <CustomSelect
                            className="min-w-0 flex-1 sm:w-[200px] sm:flex-none"
                            variant="ios"
                            value={data ? period : ''}
                            onChange={(value) => load(value)}
                            options={periodOptions}
                            placeholder="Месяц"
                            disabled={!data}
                            ariaLabel="Месяц"
                        />
                    )}
                    {/* Одна кнопка с постоянной подписью: и «заполнить новый
                        месяц», и «поправить показанный» — одно окно. Подпись,
                        меняющаяся по направлению, сдвигала бы список месяцев. */}
                    {(data ? manageDepartments.length > 0 : (shell?.manage ?? managesHonorBoard(user))) && (
                        <button
                            type="button"
                            className={`${iosBtnPrimary} shrink-0`}
                            onClick={openEditor}
                            disabled={!data}
                        >
                            <Pencil size={15} aria-hidden="true" />
                            Заполнить итоги
                        </button>
                    )}
                </div>
            </div>

            <div
                ref={boardRef}
                aria-busy={loading || undefined}
                className={`transition-opacity duration-200 ${data && loading ? 'opacity-60' : ''}`}
            >
                {content}
            </div>

            {editorTarget && (
                <Suspense fallback={null}>
                    <HonorBoardEditor
                        open={editorOpen}
                        target={editorTarget}
                        departments={manageDepartments}
                        apiRoot={apiRoot}
                        authHeaders={authHeaders}
                        notify={notify}
                        onClose={() => setEditorOpen(false)}
                        onBoardChanged={handleBoardChanged}
                    />
                </Suspense>
            )}
        </div>
    );
}
