import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Minus, Plus, RefreshCw, Search, X } from 'lucide-react';

import { iosBtnSecondary, iosCard, iosGroupLabel, IosHint } from '../ui/ios';
import { extraSaver } from './extraSaver';
import { formatTime, roleLabel } from './journalMeta';
import {
    byName, grantNote, groupPeople, isExhausted, mergeRefresh, reachedIds, SEARCH_FROM, stepExtra,
    todayLimit,
} from './settingsMeta';

/* Вкладка «Настройки» раздела «Чаты водителей» — поиски сверх лимита на сегодня.
 *
 * Владелец 27.10.2026: «св и выше добавь в настройках возможность добавлять
 * дополнительные попытки операторам до 5 попыток», на уточнение — только на
 * сегодня. Оператор упёрся в лимит и пишет супервайзеру; тот находит его здесь
 * и добавляет +1…+5. В полночь по Алматы лимит снова обычный — сам собой.
 *
 * Устройство — как список настроек iOS: группы с подписью, строка «человек —
 * степпер», изменение сохраняется сразу, без кнопки «Сохранить»: она была бы
 * лишним шагом в каждом обращении оператора, а отменять нечего — «−» и есть
 * отмена. Очередь сохранения — extraSaver.js, правила экрана — settingsMeta.js.
 *
 * ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ — показа списка из памяти прошлого открытия. Он
 * рисовался мгновенно, но через долю секунды свежий ответ переставлял строки
 * между группами (кто-то дошёл до лимита, пока вкладка была закрыта), а утром
 * встречал вчерашними добавками. Неверный список хуже заготовки: открываемся
 * заготовкой той же раскладки, и свежий список встаёт на её место.
 */

/* Сколько строк рисовать в заготовке — сколько было в прошлый раз. Помним
   только число: оно не устаревает настолько, чтобы врать. */
let lastCount = null;

const STEP_BUTTON = 'grid h-9 w-9 place-items-center rounded-[10px] text-slate-600 transition '
    + 'hover:bg-white active:scale-95 focus:outline-none focus-visible:ring-2 '
    + 'focus-visible:ring-blue-500/60 aria-disabled:cursor-default aria-disabled:opacity-30 '
    + 'aria-disabled:hover:bg-transparent aria-disabled:active:scale-100';

/* Ячейка и строка — как в «Настройках» «Учёта воды»: отступ у ячейки,
   разделитель у строки внутри, поэтому линия начинается от текста, как в
   списках iOS. С xl строки идут в две колонки, с 2xl — в три: на широком
   экране список во всю ширину раздела, но степпер не уезжает от имени на
   тысячу пикселей. Подпись группы стоит вровень с текстом строк: её px-1 плюс
   отступ обёртки. */
const CELL = 'min-w-0 px-4 xl:px-5';
/* Высота строки одна у всех: две строки текста. Подпись «кто добавил» живёт в
   подсказке у степпера, а не третьей строкой — та появлялась с первым «+» и
   двигала всё, что ниже, на 19 px. */
const ROW = 'flex h-full min-h-[64px] items-center gap-3 border-slate-100 py-2.5';
const LABEL_INSET = 'px-3 xl:px-4';
const GRID = 'grid xl:grid-cols-2 2xl:grid-cols-3';
const SEARCH_FIELD = 'h-10 w-full rounded-xl bg-white ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)]';

/* Разделитель над строкой: на телефоне — у всех, кроме первой; с xl строки
   идут парами, с 2xl — тройками, и линии нет у первого ряда. */
const rowEdge = (index) => [
    index > 0 ? 'border-t' : '',
    index === 1 ? 'xl:border-t-0' : '',
    index === 2 ? '2xl:border-t-0' : '',
].join(' ');

/* Оболочка телефона переносит ряды `flex … gap-*` по строкам, когда им тесно,
   а класс `flex-nowrap` сама же переводит в перенос. Строка «человек —
   степпер» рваться не должна: степпер под именем читается как чужой. */
const NOWRAP = { flexWrap: 'nowrap' };

const hintText = (dailyLimit, maxExtra) => (
    `Сотрудник упёрся в лимит — добавьте ему до ${maxExtra} поисков сверх ${dailyLimit}. `
    + `Добавленное действует только сегодня: в полночь по Алматы лимит снова ${dailyLimit}. `
    + 'Кто и сколько добавил, видно в журнале. '
    + 'Лимит бережёт общую квоту Chat2Desk — от неё зависят табло и метрики отдела.'
);

/* Фоновое обновление — не чаще раза в несколько секунд: возврат в окно
   приходит сразу двумя событиями (focus и visibilitychange). */
const REFRESH_GAP_MS = 4000;

const DriverChatsSettings = ({ apiBaseUrl, headers, toast }) => {
    const [state, setState] = useState(null);
    const [error, setError] = useState('');
    const [query, setQuery] = useState('');
    const [reloadKey, setReloadKey] = useState(0);
    // Перерисовка по событиям очереди сохранения: строки читают её view().
    const [, setTick] = useState(0);
    const refreshing = useRef({ busy: false, at: 0 });

    const fetchList = useCallback(async () => {
        const response = await fetch(`${apiBaseUrl}/api/driver_chats/settings`, {
            headers: headers(), credentials: 'include',
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || 'Не удалось открыть настройки');
        extraSaver.seed(payload.people || []);
        lastCount = (payload.people || []).length;
        return payload;
    }, [apiBaseUrl, headers]);

    // Открытие вкладки: группы и порядок считаются здесь, один раз.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const payload = await fetchList();
                if (cancelled) return;
                const people = byName(payload.people);
                refreshing.current.at = Date.now();
                setError('');
                setState({
                    dailyLimit: payload.daily_limit,
                    maxExtra: payload.max_extra,
                    people,
                    reached: reachedIds(people, payload.daily_limit),
                });
            } catch (err) {
                if (!cancelled) {
                    setError(err instanceof TypeError
                        ? 'Сеть недоступна. Настройки не загрузились' : err.message);
                }
            }
        })();
        return () => { cancelled = true; };
    }, [fetchList, reloadKey]);

    /* Вернулись в окно — числа освежаются, а строки остаются на местах. Список
       держат открытым часами, и «12 из 40» утра к обеду было бы неправдой. */
    const ready = Boolean(state);
    useEffect(() => {
        if (!ready) return undefined;
        const refresh = async () => {
            const mark = refreshing.current;
            if (document.visibilityState === 'hidden' || mark.busy
                || Date.now() - mark.at < REFRESH_GAP_MS) return;
            mark.busy = true;
            try {
                const payload = await fetchList();
                setState((prev) => (prev ? {
                    ...prev, people: mergeRefresh(prev.people, payload.people),
                } : prev));
            } catch {
                /* молча: на экране остаются прежние числа, следующее возвращение повторит */
            } finally {
                mark.busy = false;
                mark.at = Date.now();
            }
        };
        window.addEventListener('focus', refresh);
        document.addEventListener('visibilitychange', refresh);
        return () => {
            window.removeEventListener('focus', refresh);
            document.removeEventListener('visibilitychange', refresh);
        };
    }, [ready, fetchList]);

    /* Очередь сохранения живёт в модуле и переживает уход с вкладки; экран
       подставляет ей свой запрос и слушателя при каждом показе. */
    useEffect(() => {
        extraSaver.configure({
            send: async (userId, extra, expected) => {
                const response = await fetch(
                    `${apiBaseUrl}/api/driver_chats/settings/extra/${userId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json', ...headers() },
                        credentials: 'include',
                        body: JSON.stringify({ extra, expected }),
                    });
                return { status: response.status, payload: await response.json().catch(() => ({})) };
            },
            listener: (event) => {
                setTick((n) => n + 1);
                if (event.type === 'conflict') {
                    toast(event.message || 'Добавку уже поменяли — строка обновлена', 'error');
                } else if (event.type === 'failed') {
                    toast(event.message || 'Сеть недоступна. Изменение не сохранено', 'error');
                }
            },
        });
        return () => extraSaver.configure({ listener: null });
    }, [apiBaseUrl, headers, toast]);

    const step = useCallback((person, delta) => {
        if (!state) return;
        const current = extraSaver.view(person).extra_today || 0;
        const next = stepExtra(current, delta, state.maxExtra);
        if (next === current) return;
        // Число на экране меняется сразу — как у системного степпера.
        extraSaver.set(person.user_id, next);
        setTick((n) => n + 1);
    }, [state]);

    const groups = useMemo(
        () => (state ? groupPeople(state.people, state.reached, query) : []),
        [state, query]);

    if (error && !state) {
        return (
            <div className={`${iosCard} flex flex-col items-center px-6 py-10 text-center`}>
                <AlertCircle size={22} className="mb-2 text-rose-500" />
                <div className="text-sm font-medium text-slate-700">{error}</div>
                <button type="button" onClick={() => { setError(''); setReloadKey((n) => n + 1); }}
                        className={`${iosBtnSecondary} mt-4`}>
                    <RefreshCw size={15} /> Повторить
                </button>
            </div>
        );
    }

    if (!state) return <SettingsSkeleton count={lastCount} />;

    const total = state.people.length;
    return (
        <div className="space-y-5">
            {total >= SEARCH_FROM && (
                <div className="relative">
                    <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="Поиск по имени"
                        aria-label="Поиск сотрудника по имени"
                        className={`${SEARCH_FIELD} pl-10 pr-9 text-[14px] text-slate-900 outline-none transition placeholder:text-slate-400 focus:ring-2 focus:ring-blue-500/60`}
                    />
                    {Boolean(query) && (
                        <button
                            type="button"
                            aria-label="Очистить поиск"
                            onClick={() => setQuery('')}
                            className="absolute right-2.5 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                        >
                            <X size={12} />
                        </button>
                    )}
                </div>
            )}

            {groups.map((group, index) => (
                <section key={group.key} className="space-y-1.5">
                    <div className={`flex items-center gap-1.5 ${LABEL_INSET}`}>
                        <span className={iosGroupLabel}>{group.title}</span>
                        {/* Пояснение одно на экран, у первой группы: строкой под
                            списком оно занимало бы место всегда, а нужно один
                            раз — тому, кто открыл вкладку впервые. */}
                        {index === 0 && <IosHint text={hintText(state.dailyLimit, state.maxExtra)} />}
                    </div>
                    <ul className={`${iosCard} ${GRID}`}>
                        {group.people.map((person, rowIndex) => (
                            <PersonRow
                                key={person.user_id}
                                person={extraSaver.view(person)}
                                edge={rowEdge(rowIndex)}
                                dailyLimit={state.dailyLimit}
                                maxExtra={state.maxExtra}
                                onStep={step}
                            />
                        ))}
                    </ul>
                </section>
            ))}

            {!groups.length && (
                <div className={`${iosCard} px-6 py-10 text-center text-sm text-slate-500`}>
                    {total
                        ? `Никого не нашли по «${query.trim()}»`
                        : 'Добавлять поиски некому: в отделе нет операторов, стажёров и тренеров'}
                </div>
            )}
        </div>
    );
};

const PersonRow = ({ person, edge, dailyLimit, maxExtra, onStep }) => {
    const extra = person.extra_today || 0;
    const exhausted = isExhausted(person, dailyLimit);
    const note = grantNote(person, formatTime);
    return (
        <li className={CELL}>
            <div className={`${ROW} ${edge}`} style={NOWRAP}>
                <div className="min-w-0 flex-1">
                    <div className="truncate text-[14.5px] font-medium text-slate-900">
                        {person.name || `№${person.user_id}`}
                    </div>
                    <div className="mt-0.5 truncate text-[12.5px] text-slate-500">
                        {roleLabel(person.role)}
                        {' · '}
                        {/* Красным — только «искать нечем»: единственное
                            состояние строки, которое требует действия. */}
                        <span className={`tabular-nums ${exhausted ? 'font-medium text-rose-600' : ''}`}>
                            {person.used_today || 0} из {todayLimit(person, dailyLimit)}
                        </span>
                        {' сегодня'}
                    </div>
                </div>
                {/* Края степпера — aria-disabled, а не disabled: погашенная
                    кнопка теряет фокус, и человек с клавиатуры, дойдя до «+5»,
                    выпадал бы из списка в начало страницы. */}
                <div
                    role="group"
                    aria-label={`Поиски сверх лимита на сегодня: ${person.name || ''}`}
                    title={note || undefined}
                    className="inline-flex shrink-0 items-center rounded-xl bg-slate-100 p-0.5"
                >
                    <button type="button" className={STEP_BUTTON} aria-label="Убрать один поиск"
                            aria-disabled={extra <= 0 || undefined} onClick={() => onStep(person, -1)}>
                        <Minus size={15} />
                    </button>
                    <output aria-live="polite"
                            className={`w-10 text-center text-[15px] font-semibold tabular-nums ${
                                extra ? 'text-blue-600' : 'text-slate-400'}`}>
                        {extra ? `+${extra}` : '0'}
                    </output>
                    <button type="button" className={STEP_BUTTON} aria-label="Добавить один поиск"
                            aria-disabled={extra >= maxExtra || undefined} onClick={() => onStep(person, 1)}>
                        <Plus size={15} />
                    </button>
                </div>
            </div>
        </li>
    );
};

/* Заготовка ТОЙ ЖЕ раскладки, что список: поле поиска, подпись группы теми
   же буквами (текст прозрачный), строки той же высоты и столько, сколько
   было в прошлый раз. Иначе свежий список при подмене сдвигал всё на высоту
   поля поиска — ровно тот рывок, ради которого заготовка и заведена. */
const SettingsSkeleton = ({ count }) => {
    const rows = count ?? 8;
    /* Число людей ещё неизвестно (первое открытие на странице) — поле поиска
       рисуем: в отделе их десятки, и без места под поле свежий список
       сдвинул бы всё на его высоту. */
    const withSearch = count === null || count === undefined || count >= SEARCH_FROM;
    return (
        <div className="space-y-5" aria-busy="true">
            {withSearch && <div className={SEARCH_FIELD} />}
            <section className="space-y-1.5">
                <div className={`flex items-center gap-1.5 ${LABEL_INSET}`}>
                    <span className={iosGroupLabel}>
                        <span className="rounded bg-slate-200/70 text-transparent">Сотрудники</span>
                    </span>
                    {/* Место под «i» той же высоты: строка подписи выше
                        строки текста, и без него карточка сдвигалась на 1 px. */}
                    <span className="h-[18px] w-[18px]" />
                </div>
                <ul className={`${iosCard} ${GRID}`}>
                    {Array.from({ length: Math.max(rows, 1) }, (_, index) => (
                        <li key={index} className={CELL}>
                            <div className={`${ROW} ${rowEdge(index)}`} style={NOWRAP}>
                                <div className="min-w-0 flex-1 space-y-2">
                                    <div className="h-3.5 w-40 max-w-full rounded bg-slate-200/70" />
                                    <div className="h-3 w-28 max-w-full rounded bg-slate-100" />
                                </div>
                                <div className="h-10 w-[116px] shrink-0 rounded-xl bg-slate-100" />
                            </div>
                        </li>
                    ))}
                </ul>
            </section>
        </div>
    );
};

export default DriverChatsSettings;
