import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, Search } from 'lucide-react';
import { iosBtnSecondary, iosCard } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import useIsMobileShell from '../common/useIsMobileShell';
import {
    STATUS_FINISHED, STATUS_LABELS, formatActivity, formatDay, formatPercent,
} from './libraryMeta';

/*
 * «Мониторинг» (ТЗ, раздел 5): кто, что и когда читает — прогресс сейчас,
 * когда открыл книгу впервые, когда закончил и когда читал последний раз. Только супер-админ и тренер —
 * вкладку прячет фронт, а ручку закрывает сервер (library/routes.py).
 *
 * Фильтры — по ТЗ: сотрудник (поиск по ФИО и логину), книга, отдел. Все три
 * считает сервер: строк бывает сотни, и тащить их все ради фильтра на клиенте
 * незачем.
 */

const SEARCH_DEBOUNCE_MS = 300;

/* Поиск стоит В ОДНОМ РЯДУ с двумя CustomSelect variant="ios", поэтому и вид у
   него их: белое поле с тонким контуром, а не серое iosInput — два языка полей
   в одной строке читаются как брак. */
const SEARCH_FIELD = 'w-full rounded-xl bg-white py-2 pl-9 pr-3 text-[12.5px] text-slate-800 '
    + 'placeholder-slate-400 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] '
    + 'transition focus:outline-none focus:ring-2 focus:ring-blue-500/60';

/* Полоса занимает всё место рядом с процентом (flex-1), а не 100 % ширины:
   с w-full процент на телефоне уезжал под неё отдельной строкой. */
const ProgressBar = ({ percent, finished }) => (
    <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-100">
        <div
            className={`h-full rounded-full ${finished ? 'bg-emerald-500' : 'bg-blue-500'}`}
            style={{ width: `${Math.max(0, Math.min(100, percent))}%` }}
        />
    </div>
);

/* Статус, а у законченной книги — и день, когда её закончили. */
const StatusText = ({ status, finishedAt }) => (
    <span className="block min-w-0">
        <span className={`block truncate text-[12.5px] ${status === STATUS_FINISHED ? 'font-medium text-emerald-600' : 'text-slate-600'}`}>
            {STATUS_LABELS[status] || '—'}
        </span>
        {status === STATUS_FINISHED && finishedAt && (
            <span className="block text-[12px] tabular-nums text-slate-500">{formatDay(finishedAt)}</span>
        )}
    </span>
);

const LibraryMonitoring = ({ apiBaseUrl, headers, reloadKey }) => {
    const isNarrow = useIsMobileShell();
    const [query, setQuery] = useState('');
    const [debounced, setDebounced] = useState('');
    /* «Все» — пустая строка, а не null: у CustomSelect пункт со значением null
       не отмечается выбранным, и фильтр выглядел бы незаполненным полем. */
    const [bookId, setBookId] = useState('');
    const [departmentId, setDepartmentId] = useState('');
    const [data, setData] = useState({ rows: [], books: [], departments: [], truncated: false, limit: 0 });
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState('');
    const [retry, setRetry] = useState(0);
    const requestRef = useRef(0);

    useEffect(() => {
        const timer = setTimeout(() => setDebounced(query.trim()), SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [query]);

    useEffect(() => {
        const ticket = requestRef.current + 1;
        requestRef.current = ticket;
        const params = new URLSearchParams();
        if (debounced) params.set('q', debounced);
        if (bookId) params.set('book_id', String(bookId));
        if (departmentId) params.set('department_id', String(departmentId));
        setLoading(true);
        setLoadError('');
        axios.get(`${apiBaseUrl}/api/library/analytics?${params.toString()}`, { headers: headers() })
            .then((response) => {
                if (requestRef.current !== ticket) return;
                const body = response.data || {};
                setData({
                    rows: body.rows || [],
                    books: body.books || [],
                    departments: body.departments || [],
                    truncated: Boolean(body.truncated),
                    limit: body.limit || 0,
                });
            })
            .catch((error) => {
                if (requestRef.current !== ticket) return;
                // Строки прежних фильтров под новым фильтром читались бы как ответ
                // на него, а пустой список — как «никто не читает». Ни то ни другое.
                setData((prev) => ({ ...prev, rows: [], truncated: false }));
                setLoadError(error?.response?.data?.error || 'Не удалось загрузить мониторинг');
            })
            .finally(() => {
                if (requestRef.current === ticket) setLoading(false);
            });
    }, [apiBaseUrl, bookId, debounced, departmentId, headers, reloadKey, retry]);

    const bookOptions = useMemo(() => [
        { value: '', label: 'Все книги' },
        ...data.books.map((book) => ({ value: book.id, label: book.title })),
    ], [data.books]);
    const departmentOptions = useMemo(() => [
        { value: '', label: 'Все отделы' },
        ...data.departments.map((department) => ({ value: department.id, label: department.name })),
    ], [data.departments]);

    const filtersActive = Boolean(debounced || bookId || departmentId);
    const rows = data.rows;

    return (
        <div className="space-y-3">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)]">
                <label className="relative block">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="search"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="Сотрудник: ФИО или логин"
                        className={SEARCH_FIELD}
                        aria-label="Поиск по сотруднику"
                    />
                </label>
                <CustomSelect
                    value={bookId}
                    onChange={(value) => setBookId(value || '')}
                    options={bookOptions}
                    placeholder="Все книги"
                    searchable={data.books.length > 8}
                    variant="ios"
                    ariaLabel="Книга"
                />
                <CustomSelect
                    value={departmentId}
                    onChange={(value) => setDepartmentId(value || '')}
                    options={departmentOptions}
                    placeholder="Все отделы"
                    variant="ios"
                    ariaLabel="Отдел"
                />
            </div>

            {loadError ? (
                <div className={`${iosCard} flex flex-col items-center gap-3 px-4 py-10 text-center text-[13.5px] text-slate-600`}>
                    {loadError}
                    <button type="button" className={iosBtnSecondary} onClick={() => setRetry((value) => value + 1)}>
                        Повторить
                    </button>
                </div>
            ) : loading && !rows.length ? (
                <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500">
                    <Loader2 size={15} className="animate-spin" /> Загружаем мониторинг…
                </div>
            ) : !rows.length ? (
                <div className={`${iosCard} px-4 py-10 text-center text-[13.5px] text-slate-500`}>
                    {filtersActive ? 'Ничего не нашлось' : 'Пока никто не открывал книги из библиотеки'}
                </div>
            ) : isNarrow ? (
                <div className="space-y-2">
                    {rows.map((row) => {
                        const finished = row.status === STATUS_FINISHED;
                        return (
                            <div key={`${row.user_id}-${row.book_id}`} className={`${iosCard} p-3.5`}>
                                <div className="flex items-baseline justify-between gap-2">
                                    <span className={`truncate text-[14px] font-medium ${row.fired ? 'text-slate-400' : 'text-slate-900'}`}>
                                        {row.user_name}
                                    </span>
                                    <span className={`shrink-0 text-[12.5px] ${finished ? 'font-medium text-emerald-600' : 'text-slate-600'}`}>
                                        {STATUS_LABELS[row.status] || '—'}
                                    </span>
                                </div>
                                <div className="truncate text-[12px] text-slate-500">
                                    {[row.login, row.department_name].filter(Boolean).join(' · ')}
                                </div>
                                <div className="mt-2 truncate text-[13px] text-slate-700">{row.book_title}</div>
                                <div className="mt-1.5 flex items-center gap-2">
                                    <ProgressBar percent={row.percent} finished={finished} />
                                    <span className="w-11 shrink-0 text-right text-[12px] tabular-nums text-slate-600">
                                        {formatPercent(row.percent)}
                                    </span>
                                </div>
                                <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] tabular-nums text-slate-500">
                                    <span>Начал {formatDay(row.started_at)}</span>
                                    {finished && row.finished_at && <span>Закончил {formatDay(row.finished_at)}</span>}
                                    <span>Читал {formatActivity(row.updated_at)}</span>
                                </div>
                            </div>
                        );
                    })}
                </div>
            ) : (
                <div className={`${iosCard} overflow-hidden`}>
                    <table className="w-full table-fixed text-left text-[13px]">
                        <thead>
                            <tr className="border-b border-slate-100 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                <th className="w-[18%] px-4 py-2.5">Сотрудник</th>
                                <th className="w-[12%] px-3 py-2.5">Отдел</th>
                                <th className="w-[19%] px-3 py-2.5">Книга</th>
                                <th className="w-[14%] px-3 py-2.5">Прогресс</th>
                                <th className="w-[12%] px-3 py-2.5">Статус</th>
                                <th className="w-[10%] px-3 py-2.5">Начал</th>
                                <th className="w-[15%] whitespace-nowrap px-4 py-2.5 text-right">Последнее чтение</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((row) => {
                                const finished = row.status === STATUS_FINISHED;
                                return (
                                    <tr key={`${row.user_id}-${row.book_id}`} className="border-b border-slate-100 last:border-0">
                                        <td className="px-4 py-2.5">
                                            <div className={`truncate font-medium ${row.fired ? 'text-slate-400' : 'text-slate-900'}`}>
                                                {row.user_name}
                                            </div>
                                            <div className="truncate text-[12px] text-slate-500">{row.login}</div>
                                        </td>
                                        <td className="truncate px-3 py-2.5 text-slate-600">{row.department_name || '—'}</td>
                                        <td className="px-3 py-2.5">
                                            <div className="truncate text-slate-800">{row.book_title}</div>
                                            {row.book_author && (
                                                <div className="truncate text-[12px] text-slate-500">{row.book_author}</div>
                                            )}
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <div className="flex items-center gap-2">
                                                <ProgressBar percent={row.percent} finished={finished} />
                                                <span className="w-10 shrink-0 text-right text-[12px] tabular-nums text-slate-600">
                                                    {formatPercent(row.percent)}
                                                </span>
                                            </div>
                                        </td>
                                        <td className="px-3 py-2.5"><StatusText status={row.status} finishedAt={row.finished_at} /></td>
                                        <td className="whitespace-nowrap px-3 py-2.5 text-[12px] tabular-nums text-slate-500">
                                            {formatDay(row.started_at)}
                                        </td>
                                        <td className="whitespace-nowrap px-4 py-2.5 text-right text-[12px] tabular-nums text-slate-500">
                                            {formatActivity(row.updated_at)}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
            {data.truncated && (
                <p className="px-1 text-[12px] text-slate-500">
                    Показаны последние {data.limit} записей — сузьте выборку фильтрами.
                </p>
            )}
        </div>
    );
};

export default LibraryMonitoring;
