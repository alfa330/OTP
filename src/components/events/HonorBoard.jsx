import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Pencil, Trophy } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import lazyWithRetry from '../../utils/lazyWithRetry';
import { HonorPhoto, NominationBadge } from './honorUi';
import {
    HONOR_DEPARTMENTS, defaultDepartmentKey, honorDepartment, managesHonorBoard, nominationMeta, periodLabel, periodPhrase,
    splitBoard,
} from './honorBoardMeta';

/*
 * «Доска почёта» — вкладка раздела «Ивенты» (задача #318).
 *
 * Лучшие сотрудники месяца по трём направлениям: ОП, СЗоВ и Фронт-офис —
 * переключателем, как просит ТЗ. Месяц — выпадающим списком «Месяц / Год»:
 * в нём только те месяцы, за которые итоги опубликованы, пустых месяцев
 * человек не листает. Доска одна для всех сотрудников.
 *
 * Ключевая номинация «Лучший сотрудник отдела» стоит первой отдельной
 * карточкой: крупнее, в золотой рамке со свечением и с короной на бейдже.
 *
 * Итоги ведёт тренер (сервер решает can_manage): кнопка «Изменить» открывает
 * окно ввода. Окно грузится отдельным чанком — остальным сотрудникам его код
 * не нужен.
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

const MainCard = ({ entry, departmentKey, photoUrl }) => {
    const meta = nominationMeta(departmentKey, entry.nomination, entry.title);
    return (
        <article
            data-honor-card="main"
            className="rounded-3xl bg-white px-5 py-7 ring-1 ring-amber-300/70 shadow-[0_24px_56px_-30px_rgba(217,119,6,0.65)] sm:px-8"
        >
            <div className="flex flex-col items-center gap-5 text-center sm:flex-row sm:gap-8 sm:text-left">
                {/* Золотое кольцо — заливкой акцента, а не градиентом: тёмная
                    тема портала акценты не перекрашивает, и кольцо остаётся
                    золотым в обеих темах. */}
                <span className="shrink-0 rounded-full bg-amber-400 p-[3px] shadow-[0_10px_28px_-12px_rgba(217,119,6,0.8)]">
                    <span className="block rounded-full bg-white p-[3px]">
                        <HonorPhoto name={entry.name} url={photoUrl} size={124} />
                    </span>
                </span>
                <div className="min-w-0 flex-1">
                    <NominationBadge meta={meta} />
                    <h3 className="mt-3 break-words text-[24px] font-semibold leading-tight tracking-tight text-slate-900 sm:text-[27px]">
                        {entry.name}
                    </h3>
                    {entry.description && (
                        <p className="mt-3 whitespace-pre-line break-words text-[15px] leading-relaxed text-slate-700 sm:text-[16px]">
                            {entry.description}
                        </p>
                    )}
                </div>
            </div>
        </article>
    );
};

const HonorCard = ({ entry, departmentKey, photoUrl }) => {
    const meta = nominationMeta(departmentKey, entry.nomination, entry.title);
    return (
        <article data-honor-card="" className={`${iosCard} flex flex-col items-center px-5 pb-6 pt-7 text-center`}>
            <HonorPhoto name={entry.name} url={photoUrl} size={92} />
            <NominationBadge meta={meta} className="mt-4" />
            <h3 className="mt-2.5 break-words text-[18px] font-semibold leading-snug text-slate-900">{entry.name}</h3>
            {entry.description && (
                <p className="mt-2.5 whitespace-pre-line break-words text-[14.5px] leading-relaxed text-slate-600">
                    {entry.description}
                </p>
            )}
        </article>
    );
};

/* Сетка подстраивается под число карточек: две — в два столбца, одна — по
   центру. Три колонки при двух карточках оставляли бы справа пустую дыру. */
const gridClass = (count) => {
    if (count >= 3) return 'grid gap-4 sm:grid-cols-2 lg:grid-cols-3';
    if (count === 2) return 'grid gap-4 sm:grid-cols-2';
    return 'grid gap-4 sm:mx-auto sm:w-full sm:max-w-md';
};

const Bone = ({ className = '' }) => <span className={`block animate-pulse rounded-full bg-slate-200/70 ${className}`} />;

/* Заготовка — те же карточки и в тот же рост, что у настоящей доски: вкладка
   не прыгает, когда приходит ответ. */
const BoardSkeleton = () => (
    <div className="space-y-4" aria-busy="true" aria-label="Загрузка">
        <div className="rounded-3xl bg-white px-5 py-7 ring-1 ring-slate-200/70 sm:px-8">
            <div className="flex flex-col items-center gap-5 sm:flex-row sm:gap-8">
                <span className="block h-[136px] w-[136px] shrink-0 animate-pulse rounded-full bg-slate-200/70" />
                <div className="flex w-full flex-1 flex-col items-center gap-3 sm:items-start">
                    <Bone className="h-6 w-48" />
                    <Bone className="h-7 w-64 max-w-full" />
                    <Bone className="h-4 w-full max-w-md" />
                    <Bone className="h-4 w-3/4 max-w-sm" />
                </div>
            </div>
        </div>
        <div className={gridClass(3)}>
            {[0, 1, 2].map((index) => (
                <div key={index} className={`${iosCard} flex flex-col items-center gap-3 px-5 pb-6 pt-7`}>
                    <span className="block h-[92px] w-[92px] animate-pulse rounded-full bg-slate-200/70" />
                    <Bone className="mt-1 h-6 w-36" />
                    <Bone className="h-5 w-44" />
                    <Bone className="h-4 w-full" />
                    <Bone className="h-4 w-2/3" />
                </div>
            ))}
        </div>
    </div>
);

const EmptyCard = ({ title, text, action = null }) => (
    <div className={`${iosCard} flex flex-col items-center px-6 py-12 text-center`}>
        <span className="grid h-14 w-14 place-items-center rounded-2xl bg-amber-50 text-amber-500">
            <Trophy size={26} strokeWidth={2} aria-hidden="true" />
        </span>
        <h3 className="mt-4 text-[17px] font-semibold text-slate-900">{title}</h3>
        <p className="mt-1.5 max-w-md text-[14px] leading-relaxed text-slate-500">{text}</p>
        {action}
    </div>
);

export default function HonorBoard({ user, apiRoot, authHeaders, notify }) {
    const userId = user?.id;
    const [data, setData] = useState(() => memory.get(memoryKey(userId, '')) || null);
    const [loading, setLoading] = useState(() => !memory.has(memoryKey(userId, '')));
    const [error, setError] = useState('');
    const [departmentKey, setDepartmentKey] = useState(() => defaultDepartmentKey(user));
    const [editorOpen, setEditorOpen] = useState(false);
    // Что окно редактирует. Не сбрасывается при закрытии: окно уходит анимацией
    // и до конца ухода показывает ту же доску.
    const [editorTarget, setEditorTarget] = useState(null);
    const [shell] = useState(() => readShell(userId));
    const ticket = useRef(0);

    const load = useCallback(async (period = '') => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        const cached = memory.get(memoryKey(userId, period));
        if (cached) {
            setData(cached);
            setError('');
        }
        setLoading(true);
        try {
            const response = await axios.get(`${apiRoot}/api/events/honor_board`, {
                headers: authHeaders(),
                params: period ? { period } : {},
            });
            if (mine !== ticket.current) return;
            const next = response?.data || {};
            memory.set(memoryKey(userId, period), next);
            if (next.period) memory.set(memoryKey(userId, next.period), next);
            writeShell(userId, {
                months: Array.isArray(next.periods) && next.periods.length > 0,
                manage: Array.isArray(next.manage_departments) && next.manage_departments.length > 0,
            });
            setData(next);
            setError('');
        } catch (requestError) {
            if (mine !== ticket.current) return;
            if (cached) notify?.('Не удалось обновить доску почёта', 'error');
            else setError(requestError?.response?.data?.error || 'Не удалось загрузить доску почёта');
        } finally {
            if (mine === ticket.current) setLoading(false);
        }
    }, [apiRoot, authHeaders, notify, userId]);

    const loadRef = useRef(load);
    loadRef.current = load;
    useEffect(() => {
        loadRef.current('');
    }, []);

    const period = data?.period || '';
    const periods = useMemo(() => (Array.isArray(data?.periods) ? data.periods : []), [data]);
    const periodOptions = useMemo(() => periods.map((value) => ({ value, label: periodLabel(value) })), [periods]);
    // Направления, которые человек ведёт: тренер — все, глава Фронт-офиса — своё.
    const manageDepartments = useMemo(() => (Array.isArray(data?.manage_departments)
        ? data.manage_departments : []), [data]);
    const department = honorDepartment(departmentKey) || HONOR_DEPARTMENTS[0];
    const board = data?.boards?.[department.key] || null;
    const { main, others } = useMemo(() => splitBoard(board), [board]);
    const photos = data?.photos || {};

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

    let content;
    if (!data && loading) {
        content = <BoardSkeleton />;
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
        content = (
            <div className="space-y-4">
                {main && <MainCard entry={main} departmentKey={department.key} photoUrl={photos[main.user_id]} />}
                {others.length > 0 && (
                    <div className={gridClass(others.length)}>
                        {others.map((entry, index) => (
                            <HonorCard
                                key={`${entry.nomination}-${index}`}
                                entry={entry}
                                departmentKey={department.key}
                                photoUrl={photos[entry.user_id]}
                            />
                        ))}
                    </div>
                )}
            </div>
        );
    }

    /* Столбик — на gap, а не space-y: окно тренера лежит в нём же, и отступ
       space-y сдвинул бы его затемнение на 16 px ниже края экрана. */
    return (
        <div className="flex flex-col gap-4">
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
