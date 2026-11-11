import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { AlertTriangle, Download, Loader2, RefreshCw, Search, Trash2, UserPlus } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import useIsMobileShell from '../common/useIsMobileShell';
import { IosMenu, IosModal, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput } from '../ui/ios';
import {
    accessRowState, emailProblem, exportFileName, formatWhen, managerNote, peopleCount, sameEmail, saveBlob,
} from './syntonyRules';

/*
 * «Доступ» к окну Syntony — список людей, которым оно открыто (решение владельца
 * 11.11.2026: «выбираю людей сам»). Ведут его супер-админы.
 *
 * Список — это и есть «операторы iCORE» для Syntony: кого добавили, того сервер сразу
 * записывает у Syntony (ТЗ, задача 6), а её администратор связывает оператора с
 * сотрудником Syntony по почте из этой строки. Поэтому в строке три вещи: кто, почта и
 * что ответила Syntony. Тот же список одной кнопкой уходит файлом (id, ФИО, почта).
 *
 * Одно окно, два уровня (как «Настройки» iOS): список → «Добавить сотрудника».
 * Второй модалки поверх первой нет: два затемнения уводят фон в чёрный.
 */

const SEARCH_DELAY_MS = 250;
const FILTER_FROM = 9;

const PILL_TONES = {
    green: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
    amber: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200/80',
    red: 'bg-rose-50 text-rose-700 ring-1 ring-rose-200/80',
    slate: 'text-slate-500',
};

const errorOf = (failure, fallback) => {
    const error = failure?.response?.data?.error;
    return typeof error === 'string' && error ? error : fallback;
};

const Notice = ({ children }) => (
    <div className="flex items-start gap-2.5 rounded-2xl bg-amber-50 px-3.5 py-3 text-[13px] leading-snug text-amber-900 ring-1 ring-amber-200">
        <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
        <div className="min-w-0 grow basis-0">{children}</div>
    </div>
);

const AccessRow = ({ item, configured, busy, onEmail, onCheck, onRevoke }) => {
    const [draft, setDraft] = useState(item.email);
    const [problem, setProblem] = useState('');
    const [confirming, setConfirming] = useState(false);
    const savedEmail = useRef(item.email);
    // Сервер вернул почту в своём виде (строчными) — нетронутое поле берёт её.
    useEffect(() => {
        if (sameEmail(savedEmail.current, draft) || sameEmail(item.email, draft)) setDraft(item.email);
        savedEmail.current = item.email;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [item.email]);

    const state = accessRowState(item);
    const dirty = !sameEmail(draft, item.email);
    const invalid = emailProblem(draft);

    const save = async () => {
        if (!dirty || busy) return;
        if (invalid) { setProblem(invalid); return; }
        setProblem('');
        try {
            await onEmail(item, draft.trim());
        } catch (failure) {
            setProblem(errorOf(failure, 'Почта не сохранилась'));
        }
    };

    return (
        <li className="px-3.5 py-3" data-testid="syntony-access-row">
            {/* Три колонки одной ширины во всех строках: кто, почта, состояние с меню.
                На телефоне — друг под другом. */}
            <div className="grid grid-cols-1 items-center gap-x-3 gap-y-2 sm:grid-cols-[minmax(0,1fr)_240px_176px]">
                <div className="min-w-0">
                    <div className="truncate text-[13.5px] font-semibold text-slate-900" title={item.name}>{item.name}</div>
                    <div className="truncate text-[12px] text-slate-500">{item.department || 'Без отдела'}</div>
                </div>
                <div className="min-w-0">
                    <input
                        type="email"
                        inputMode="email"
                        autoComplete="off"
                        spellCheck={false}
                        value={draft}
                        disabled={busy || !item.working}
                        placeholder="Почта в Syntony"
                        aria-label={`Почта для Syntony: ${item.name}`}
                        aria-invalid={Boolean(problem) || undefined}
                        onChange={(event) => { setDraft(event.target.value); setProblem(''); }}
                        onBlur={save}
                        onKeyDown={(event) => {
                            if (event.key === 'Enter') { event.preventDefault(); event.currentTarget.blur(); }
                            if (event.key === 'Escape' && dirty) { event.stopPropagation(); setDraft(item.email); setProblem(''); }
                        }}
                        className={`${iosInput} h-9 py-0 text-[13px] ${problem ? 'ring-2 ring-rose-300' : ''}`}
                    />
                </div>
                <div className="flex min-w-0 items-center gap-1.5">
                    <div className="flex min-w-0 flex-1 items-center gap-1.5 sm:justify-end">
                        {busy ? (
                            <span className="inline-flex items-center gap-1.5 text-[12px] text-slate-500">
                                <Loader2 size={13} className="animate-spin" aria-hidden="true" /> Спрашиваем…
                            </span>
                        ) : (
                            <>
                                <span className={`inline-flex items-center rounded-full py-0.5 text-[12px] font-medium ${state.tone === 'slate' ? '' : 'px-2 '}${PILL_TONES[state.tone] || PILL_TONES.slate}`}
                                    title={item.link_checked_at ? `Проверено ${formatWhen(item.link_checked_at)}` : undefined}>
                                    {state.label}
                                </span>
                                {state.hint && <InfoHint text={state.hint} side="left" />}
                            </>
                        )}
                    </div>
                    {/* Место под меню держим и когда его нет: состояние не прыгает вбок. */}
                    <div className="grid h-8 w-8 shrink-0 place-items-center">
                        {!busy && !confirming && (
                            <IosMenu label={`Действия: ${item.name}`} items={[
                                item.working && configured && { key: 'check', label: 'Проверить связь', icon: RefreshCw, onSelect: () => onCheck(item) },
                                { key: 'revoke', label: 'Снять доступ', icon: Trash2, danger: true, onSelect: () => setConfirming(true) },
                            ]} />
                        )}
                    </div>
                </div>
            </div>
            {problem && <p role="alert" className="mt-1.5 px-1 text-[12px] text-rose-600">{problem}</p>}
            {confirming && (
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-xl bg-rose-50 px-3 py-2 text-[12.5px] text-rose-800 ring-1 ring-rose-200/80">
                    <span className="min-w-0 grow basis-0">Снять доступ? Разделы Syntony у сотрудника пропадут.</span>
                    <span className="flex shrink-0 items-center gap-1.5">
                        <button type="button" onClick={() => setConfirming(false)}
                            className="rounded-lg px-2.5 py-1.5 font-medium text-slate-600 transition hover:bg-white/70">Отмена</button>
                        <button type="button" onClick={() => { setConfirming(false); onRevoke(item); }}
                            className="rounded-lg bg-rose-600 px-2.5 py-1.5 font-semibold text-white transition hover:bg-rose-700 active:scale-[0.98]">Снять</button>
                    </span>
                </div>
            )}
        </li>
    );
};

const PeoplePicker = ({ api, onAdded }) => {
    const [query, setQuery] = useState('');
    const [people, setPeople] = useState(null);
    const [problem, setProblem] = useState('');
    const [adding, setAdding] = useState(null);
    // Сколько человек добавили за этот заход: одна строка под поиском вместо тоста на каждого.
    const [added, setAdded] = useState(0);
    const inputRef = useRef(null);
    const seqRef = useRef(0);

    const search = useCallback(async (text) => {
        const seq = ++seqRef.current;
        try {
            const { data } = await api.get('/access/people', { q: text });
            if (seq === seqRef.current) { setPeople(data.people || []); setProblem(''); }
        } catch (failure) {
            if (seq === seqRef.current) setProblem(errorOf(failure, 'Не удалось найти сотрудников'));
        }
    }, [api]);

    useEffect(() => {
        const timer = setTimeout(() => search(query), query ? SEARCH_DELAY_MS : 0);
        return () => clearTimeout(timer);
    }, [query, search]);

    useEffect(() => {
        const timer = setTimeout(() => inputRef.current?.focus(), 60);
        return () => clearTimeout(timer);
    }, []);

    const add = async (person) => {
        if (adding) return;
        setAdding(person.id);
        setProblem('');
        try {
            const { data } = await api.post('/access', { user_id: person.id });
            // Добавленный уходит из выдачи сразу: следующего можно выбрать, не возвращаясь.
            setPeople((list) => (list || []).filter((entry) => entry.id !== person.id));
            setAdded((count) => count + 1);
            onAdded(data);
        } catch (failure) {
            setProblem(errorOf(failure, 'Не удалось открыть доступ'));
        } finally {
            setAdding(null);
        }
    };

    return (
        <div className="space-y-3">
            <div className="relative">
                <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                <input ref={inputRef} className={`${iosInput} pl-9`} value={query} placeholder="Имя или логин"
                    aria-label="Найти сотрудника" onChange={(event) => setQuery(event.target.value)} />
            </div>
            {problem && <p role="alert" className="px-1 text-[12.5px] text-rose-600">{problem}</p>}
            {added > 0 && (
                <p className="px-1 text-[12.5px] text-slate-500" data-testid="syntony-access-added">
                    Добавлено: {added}. Разделы Syntony появятся у них после обновления страницы.
                </p>
            )}
            {people === null && !problem && (
                <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-slate-500">
                    <Loader2 size={15} className="animate-spin" aria-hidden="true" /> Ищем…
                </div>
            )}
            {people && people.length === 0 && (
                <p className="py-8 text-center text-[13px] text-slate-500">
                    {query.trim() ? 'Никого не нашли' : 'Все работающие сотрудники уже в списке'}
                </p>
            )}
            {people && people.length > 0 && (
                <ul className="divide-y divide-slate-100 overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                    {people.map((person) => (
                        <li key={person.id}>
                            <button type="button" onClick={() => add(person)} disabled={Boolean(adding)}
                                className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition hover:bg-slate-50 disabled:opacity-60">
                                <span className="min-w-0 grow basis-0">
                                    <span className="block truncate text-[13.5px] font-semibold text-slate-800">{person.name}</span>
                                    <span className="block truncate text-[12px] text-slate-500">
                                        {[person.department || 'Без отдела', person.card_email].filter(Boolean).join(' · ')}
                                    </span>
                                </span>
                                {adding === person.id
                                    ? <Loader2 size={15} className="shrink-0 animate-spin text-slate-400" aria-hidden="true" />
                                    : <UserPlus size={16} className="shrink-0 text-blue-600" aria-hidden="true" />}
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

const SyntonyAccessSheet = ({ open, onClose, apiBaseUrl, withAccessTokenHeader, showToast, currentUserId = null }) => {
    const headersRef = useRef(withAccessTokenHeader);
    useEffect(() => { headersRef.current = withAccessTokenHeader; }, [withAccessTokenHeader]);
    const api = useMemo(() => {
        const url = (path) => `${apiBaseUrl}/api/syntony${path}`;
        const config = (extra) => ({ headers: headersRef.current ? headersRef.current() : {}, ...extra });
        return {
            get: (path, params) => axios.get(url(path), config({ params })),
            blob: (path) => axios.get(url(path), config({ responseType: 'blob' })),
            post: (path, body) => axios.post(url(path), body || {}, config()),
            patch: (path, body) => axios.patch(url(path), body || {}, config()),
            delete: (path) => axios.delete(url(path), config()),
        };
    }, [apiBaseUrl]);

    const [items, setItems] = useState(null);
    const [configured, setConfigured] = useState(true);
    const [problem, setProblem] = useState('');
    const [adding, setAdding] = useState(false);
    const [filter, setFilter] = useState('');
    const [busyIds, setBusyIds] = useState(() => new Set());
    const [note, setNote] = useState('');
    const [progress, setProgress] = useState(null);
    const [exporting, setExporting] = useState(false);
    // Менялась ли строка самого открывшего окно: тогда его окно Syntony надо открыть заново.
    const selfChanged = useRef(false);
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const narrow = useIsMobileShell();

    const load = useCallback(async () => {
        try {
            const { data } = await api.get('/access');
            if (!mounted.current) return;
            setItems(data.items || []);
            setConfigured(data.configured !== false);
            setProblem('');
        } catch (failure) {
            if (mounted.current) setProblem(errorOf(failure, 'Список доступа не загрузился'));
        }
    }, [api]);

    useEffect(() => {
        if (!open) return;
        selfChanged.current = false;
        setAdding(false);
        setFilter('');
        setNote('');
        setProgress(null);
        setItems(null);
        load();
    }, [open, load]);

    const setBusy = (id, on) => setBusyIds((old) => {
        const next = new Set(old);
        if (on) next.add(id); else next.delete(id);
        return next;
    });

    const touch = (userId) => { if (currentUserId != null && Number(userId) === Number(currentUserId)) selfChanged.current = true; };

    /* Ответ ручки «добавить / почта / проверить»: строка человека и заметка о Syntony. */
    const applyReply = useCallback((data) => {
        const item = data?.item;
        if (item) {
            setItems((list) => {
                const rest = (list || []).filter((entry) => entry.id !== item.id);
                return [...rest, item].sort((a, b) => a.name.localeCompare(b.name, 'ru') || a.id - b.id);
            });
        }
        const syntony = data?.syntony;
        if (syntony && syntony.configured === false) setConfigured(false);
        const text = syntony?.configured === false ? '' : managerNote(syntony?.code, syntony?.message);
        setNote(text);
        return text;
    }, []);

    const saveEmail = async (item, email) => {
        setBusy(item.id, true);
        try {
            const { data } = await api.patch(`/access/${item.id}`, { email });
            touch(item.id);
            applyReply(data);
        } finally {
            if (mounted.current) setBusy(item.id, false);
        }
    };

    const check = async (item) => {
        setBusy(item.id, true);
        try {
            const { data } = await api.post(`/access/${item.id}/check`);
            touch(item.id);
            return applyReply(data);
        } catch (failure) {
            showToast?.(errorOf(failure, 'Не удалось спросить Syntony'), 'error');
            return 'error';
        } finally {
            if (mounted.current) setBusy(item.id, false);
        }
    };

    const revoke = async (item) => {
        setBusy(item.id, true);
        try {
            await api.delete(`/access/${item.id}`);
            touch(item.id);
            setItems((list) => (list || []).filter((entry) => entry.id !== item.id));
        } catch (failure) {
            showToast?.(errorOf(failure, 'Доступ не снялся'), 'error');
        } finally {
            if (mounted.current) setBusy(item.id, false);
        }
    };

    /* Проверка всех — по одному, запросами подряд: у Syntony спрашивают за каждого
       отдельно, и один длинный запрос на двадцать человек держал бы сервер полминуты.
       Ответ «дело не в операторе» (ключ, пауза, сбой) останавливает обход: остальным
       Syntony ответит тем же. */
    const checkAll = async () => {
        const targets = (items || []).filter((item) => item.working);
        if (!targets.length || progress) return;
        for (let index = 0; index < targets.length; index += 1) {
            if (!mounted.current) return;
            setProgress({ done: index, total: targets.length });
            // eslint-disable-next-line no-await-in-loop
            const trouble = await check(targets[index]);
            if (trouble) break;
        }
        if (mounted.current) setProgress(null);
    };

    const exportList = async () => {
        if (exporting) return;
        setExporting(true);
        try {
            const { data } = await api.blob('/access/export');
            saveBlob(data, exportFileName());
        } catch (failure) {
            showToast?.('Файл не скачался. Попробуйте ещё раз.', 'error');
        } finally {
            if (mounted.current) setExporting(false);
        }
    };

    /* На телефоне «назад» со второго уровня возвращает к списку, а не закрывает окно.
       false — «остаюсь»: иначе следующий жест прошёл бы мимо окна (useScreenBackGesture). */
    const close = () => {
        if (narrow && adding) { setAdding(false); return false; }
        onClose?.(selfChanged.current);
        return undefined;
    };

    const shown = useMemo(() => {
        const text = filter.trim().toLowerCase();
        if (!text) return items || [];
        return (items || []).filter((item) => [item.name, item.email, item.department]
            .some((field) => String(field || '').toLowerCase().includes(text)));
    }, [items, filter]);
    const working = (items || []).filter((item) => item.working).length;

    let body;
    if (adding) {
        body = (
            <PeoplePicker api={api}
                onAdded={(data) => { touch(data?.item?.id); applyReply(data); }} />
        );
    } else if (items === null) {
        body = problem ? (
            <div className="py-8 text-center text-[13px] text-slate-500">
                {problem}{' '}
                <button type="button" onClick={load} className="font-semibold text-blue-600 hover:underline">Повторить</button>
            </div>
        ) : (
            <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500">
                <Loader2 size={15} className="animate-spin" aria-hidden="true" /> Загрузка…
            </div>
        );
    } else {
        body = (
            <div className="space-y-3">
                {!configured && (
                    <Notice>
                        На сервере iCORE ещё не задан ключ Syntony. Людей можно добавлять уже сейчас; когда ключ появится,
                        нажмите «Проверить связь» — и все запишутся у Syntony.
                    </Notice>
                )}
                {note && <Notice>{note}</Notice>}
                {items.length >= FILTER_FROM && (
                    <div className="relative">
                        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                        <input className={`${iosInput} pl-9`} value={filter} placeholder="Найти в списке"
                            aria-label="Найти в списке" onChange={(event) => setFilter(event.target.value)} />
                    </div>
                )}
                {items.length === 0 ? (
                    <div className="px-6 py-10 text-center">
                        <div className="text-[15px] font-semibold text-slate-800">Пока никому не открыто</div>
                        <p className="mx-auto mt-1 max-w-sm text-[13px] text-slate-500">
                            Добавьте сотрудников, которые будут работать с чатами и заявками Syntony. Разделы увидят только они.
                        </p>
                    </div>
                ) : shown.length === 0 ? (
                    <p className="py-8 text-center text-[13px] text-slate-500">В списке никого с «{filter.trim()}»</p>
                ) : (
                    <ul className="divide-y divide-slate-100 rounded-2xl bg-white ring-1 ring-slate-200/70">
                        {shown.map((item) => (
                            <AccessRow key={item.id} item={item} configured={configured} busy={busyIds.has(item.id)}
                                onEmail={saveEmail} onCheck={check} onRevoke={revoke} />
                        ))}
                    </ul>
                )}
            </div>
        );
    }

    const footer = adding ? null : (
        <div className="flex w-full flex-wrap items-center justify-between gap-2">
            <button type="button" onClick={exportList} disabled={exporting || !working} className={`${iosBtnGhost} disabled:opacity-50`}
                title="Файл для Syntony: id оператора, ФИО и почта">
                {exporting ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Download size={15} aria-hidden="true" />}
                Скачать список
            </button>
            <div className="flex flex-wrap items-center gap-2">
                {configured && working > 0 && (
                    <button type="button" onClick={checkAll} disabled={Boolean(progress)} className={iosBtnSecondary}>
                        {progress
                            ? <><Loader2 size={15} className="animate-spin" aria-hidden="true" /> Проверяем {progress.done + 1} из {progress.total}…</>
                            : <><RefreshCw size={15} aria-hidden="true" /> Проверить связь</>}
                    </button>
                )}
                <button type="button" onClick={() => { setAdding(true); setNote(''); }} disabled={items === null} className={iosBtnPrimary}>
                    <UserPlus size={15} aria-hidden="true" /> Добавить
                </button>
            </div>
        </div>
    );

    return (
        <IosModal
            open={open}
            onClose={close}
            onBack={adding ? () => setAdding(false) : null}
            title={adding ? 'Добавить сотрудника' : 'Доступ к окну Syntony'}
            subtitle={adding
                ? 'Почта подставится из карточки — её можно поправить в списке'
                : (items && items.length ? `Открыто: ${peopleCount(items.length)}` : null)}
            maxWidth="max-w-3xl"
            sizeKey={adding ? 'add' : (items === null ? 'loading' : 'list')}
            footer={footer}
        >
            {body}
        </IosModal>
    );
};

export default SyntonyAccessSheet;
