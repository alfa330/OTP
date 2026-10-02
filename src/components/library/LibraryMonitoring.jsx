import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { ChevronRight, Loader2, Search } from 'lucide-react';
import { iosBtnSecondary, iosCard, iosGroupLabel } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import useIsMobileShell from '../common/useIsMobileShell';
import {
    STATUS_FINISHED, STATUS_LABELS, formatActivity, formatDay, formatPercent,
} from './libraryMeta';

/*
 * «Мониторинг» (ТЗ, раздел 5): кто, что и когда читает — прогресс сейчас,
 * когда открыл книгу впервые, когда закончил и когда читал последний раз. Только тем, кто ведёт
 * библиотеку, — вкладку прячет фронт, а ручку закрывает сервер (library/routes.py: can_manage).
 *
 * Фильтры — по ТЗ: сотрудник (поиск по ФИО и логину), книга, отдел. Все три
 * считает сервер: строк бывает сотни, и тащить их все ради фильтра на клиенте
 * незачем. Отдел — общий выбор раздела (справа от вкладок), своего второго
 * выбора отдела здесь нет: два переключателя одного и того же разъезжались бы.
 *
 * Над списком — статистика (29.09.2026, «общую и раздельную»): плитки итогов
 * по всей библиотеке или по выбранному отделу и, для всей библиотеки, та же
 * статистика по каждому отделу. Нажатие на отдел выбирает его в разделе.
 * Считает её отдельная ручка: список перезапрашивается на каждую букву
 * поиска, а статистика от фильтров списка не зависит.
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

/* Плитка итога: подпись, число и, где без неё число читается двояко, пояснение. */
const StatTile = ({ label, value, note, className = '' }) => (
    <div className={`${iosCard} px-4 py-3 ${className}`}>
        <div className="truncate text-[12px] text-slate-500">{label}</div>
        <div className="mt-1 text-[24px] font-semibold leading-none tracking-tight tabular-nums text-slate-900">{value}</div>
        {note && <div className="mt-1 truncate text-[11.5px] text-slate-400">{note}</div>}
    </div>
);

const EMPTY_IDS = new Set();

const averageText = (value) => (value === null || value === undefined ? '—' : formatPercent(value));

const SummaryTiles = ({ total, scoped }) => (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatTile label="Книг в библиотеке" value={total.books} note={scoped ? 'выдано отделу' : 'без архива'} />
        <StatTile label="Читателей" value={total.readers} note="открыли хотя бы одну" />
        {/* Считаются прочтения — пары «сотрудник × книга», с книгами из архива:
            три человека, дочитавшие одну книгу, — это три прочтения, а не
            «три книги». Подпись говорит ровно это. */}
        <StatTile label="В процессе" value={total.in_progress} note="начатых прочтений" />
        <StatTile label="Закончено" value={total.finished} note="прочтений завершено" />
        {/* Пятая плитка на телефоне — во всю ширину: одна в последнем ряду
            читалась бы как недогрузившаяся соседка. */}
        <StatTile className="col-span-2 sm:col-span-1" label="Средний прогресс"
                  value={averageText(total.avg_percent)} note="по всем прочтениям" />
    </div>
);

/* Та же статистика по каждому отделу. Отдел — отдел читателя; книги — выданные
   отделу. Нажимается только строка отдела, который есть в выборе раздела:
   «Без отдела» (управляющие бывают без отдела) и выключенный отдел без книг
   выбрать там нельзя, и нажатие сузило бы данные под подписью «Все отделы». */
const DepartmentStats = ({ rows, narrow, onPick, selectableIds }) => {
    const pickable = (row) => row.id !== null && row.id !== undefined && selectableIds.has(row.id);
    const keyPick = (event, row) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onPick(row.id);
        }
    };
    if (narrow) {
        return (
            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                {rows.map((row) => (
                    <div
                        key={row.id ?? 'none'}
                        role={pickable(row) ? 'button' : undefined}
                        tabIndex={pickable(row) ? 0 : undefined}
                        onClick={pickable(row) ? () => onPick(row.id) : undefined}
                        onKeyDown={pickable(row) ? (event) => keyPick(event, row) : undefined}
                        className={`flex items-center gap-2 px-3.5 py-2.5 ${pickable(row) ? 'cursor-pointer active:bg-slate-100' : ''}`}
                    >
                        <div className="min-w-0 flex-1">
                            <div className="flex items-baseline justify-between gap-2">
                                <span className="truncate text-[13.5px] font-medium text-slate-900">{row.name}</span>
                                <span className="shrink-0 text-[12.5px] tabular-nums text-slate-600">{averageText(row.avg_percent)}</span>
                            </div>
                            <div className="mt-0.5 truncate text-[12px] tabular-nums text-slate-500">
                                {[
                                    row.books !== null && `книг ${row.books}`,
                                    `читателей ${row.readers}`,
                                    `в процессе ${row.in_progress}`,
                                    `закончено ${row.finished}`,
                                ].filter(Boolean).join(' · ')}
                            </div>
                        </div>
                        {pickable(row) && <ChevronRight size={16} className="shrink-0 text-slate-300" />}
                    </div>
                ))}
            </div>
        );
    }
    return (
        <div className={`${iosCard} overflow-hidden`}>
            <table className="w-full table-fixed text-left text-[13px]">
                <thead>
                    <tr className="border-b border-slate-100 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                        <th className="w-[30%] px-4 py-2.5">Отдел</th>
                        <th className="w-[10%] px-3 py-2.5 text-right">Книг</th>
                        <th className="w-[12%] px-3 py-2.5 text-right">Читателей</th>
                        <th className="w-[12%] px-3 py-2.5 text-right">В процессе</th>
                        <th className="w-[12%] px-3 py-2.5 text-right">Закончено</th>
                        <th className="w-[24%] px-4 py-2.5">Средний прогресс</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row) => (
                        <tr
                            key={row.id ?? 'none'}
                            tabIndex={pickable(row) ? 0 : undefined}
                            onClick={pickable(row) ? () => onPick(row.id) : undefined}
                            onKeyDown={pickable(row) ? (event) => keyPick(event, row) : undefined}
                            title={pickable(row) ? 'Открыть статистику отдела' : undefined}
                            className={`group border-b border-slate-100 last:border-0 ${
                                pickable(row) ? 'cursor-pointer hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/60' : ''}`}
                        >
                            <td className="px-4 py-2.5">
                                <div className="flex items-center gap-1">
                                    <span className="truncate font-medium text-slate-900">{row.name}</span>
                                    {pickable(row) && (
                                        <ChevronRight size={14} className="shrink-0 text-slate-300 group-hover:text-slate-500" />
                                    )}
                                </div>
                            </td>
                            <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{row.books ?? '—'}</td>
                            <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{row.readers}</td>
                            <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{row.in_progress}</td>
                            <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{row.finished}</td>
                            <td className="px-4 py-2.5">
                                {row.avg_percent === null || row.avg_percent === undefined ? (
                                    <span className="text-slate-400">—</span>
                                ) : (
                                    <div className="flex items-center gap-2">
                                        <ProgressBar percent={row.avg_percent} finished={false} />
                                        <span className="w-10 shrink-0 text-right text-[12px] tabular-nums text-slate-600">
                                            {formatPercent(row.avg_percent)}
                                        </span>
                                    </div>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

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

const LibraryMonitoring = ({
    apiBaseUrl, headers, reloadKey, departmentId = '', selectableDepartmentIds, onPickDepartment,
}) => {
    const isNarrow = useIsMobileShell();
    const [query, setQuery] = useState('');
    const [debounced, setDebounced] = useState('');
    /* «Все» — пустая строка, а не null: у CustomSelect пункт со значением null
       не отмечается выбранным, и фильтр выглядел бы незаполненным полем. */
    const [bookId, setBookId] = useState('');
    const [data, setData] = useState({ rows: [], books: [], truncated: false, limit: 0 });
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState('');
    const [retry, setRetry] = useState(0);
    const requestRef = useRef(0);
    const [summary, setSummary] = useState(null);
    const [summaryError, setSummaryError] = useState('');
    const [summaryRetry, setSummaryRetry] = useState(0);
    const summaryRequestRef = useRef(0);

    useEffect(() => {
        const ticket = summaryRequestRef.current + 1;
        summaryRequestRef.current = ticket;
        const params = new URLSearchParams();
        if (departmentId) params.set('department_id', String(departmentId));
        setSummaryError('');
        axios.get(`${apiBaseUrl}/api/library/analytics/summary?${params.toString()}`, { headers: headers() })
            .then((response) => {
                if (summaryRequestRef.current !== ticket) return;
                const body = response.data || {};
                /* Сводка помнит, для какого отдела посчитана: пока идёт запрос
                   нового, прежние цифры под новой подписью не показываются. */
                setSummary(body.total
                    ? { scope: departmentId, total: body.total, departments: body.departments || null }
                    : null);
            })
            .catch((error) => {
                if (summaryRequestRef.current !== ticket) return;
                // Цифры прежнего отдела под новым выбором читались бы как его.
                setSummary(null);
                setSummaryError(error?.response?.data?.error || 'Не удалось посчитать статистику');
            });
    }, [apiBaseUrl, departmentId, headers, reloadKey, summaryRetry]);

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
                    scope: departmentId,
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
        ...data.books.map((book) => ({
            value: book.id,
            label: book.title,
            meta: book.archived ? 'в архиве' : undefined,
        })),
    ], [data.books]);

    const filtersActive = Boolean(debounced || bookId);
    /* Строки другого отдела, пока грузится выбранный, не показываются: у них
       уже спрятана колонка «Отдел», и они читались бы как люди нового отдела.
       Смена поиска или книги прежние строки держит — список не мигает на
       каждой букве. */
    const rows = data.scope === departmentId ? data.rows : [];
    const summaryFresh = Boolean(summary) && summary.scope === departmentId;
    /* При выбранном отделе колонка «Отдел» повторяла бы одно и то же в каждой
       строке. */
    const showDepartment = !departmentId;

    return (
        <div className="space-y-5">
            {summaryError ? (
                <div className={`${iosCard} flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-[13px] text-slate-600`}>
                    {summaryError}
                    <button type="button" className={`${iosBtnSecondary} !py-1.5`} onClick={() => setSummaryRetry((value) => value + 1)}>
                        Повторить
                    </button>
                </div>
            ) : summaryFresh ? (
                <SummaryTiles total={summary.total} scoped={Boolean(departmentId)} />
            ) : (
                <div className="flex items-center justify-center gap-2 py-6 text-[13px] text-slate-500">
                    <Loader2 size={15} className="animate-spin" /> Считаем статистику…
                </div>
            )}

            {summaryFresh && !departmentId && summary.departments?.length > 0 && (
                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>По отделам</div>
                    <DepartmentStats
                        rows={summary.departments}
                        narrow={isNarrow}
                        selectableIds={selectableDepartmentIds || EMPTY_IDS}
                        onPick={(id) => onPickDepartment?.(id)}
                    />
                </section>
            )}

            <section className="space-y-2">
                <div className={iosGroupLabel}>Кто читает</div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
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
                        {filtersActive ? 'Ничего не нашлось'
                            : departmentId ? 'В отделе пока никто не открывал книги' : 'Пока никто не открывал книги из библиотеки'}
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
                                        {[row.login, showDepartment && row.department_name].filter(Boolean).join(' · ')}
                                    </div>
                                    <div className="mt-2 truncate text-[13px] text-slate-700">
                                        {row.book_title}
                                        {row.book_archived && <span className="text-slate-400"> · в архиве</span>}
                                    </div>
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
                                    {showDepartment && <th className="w-[12%] px-3 py-2.5">Отдел</th>}
                                    <th className={`${showDepartment ? 'w-[19%]' : 'w-[31%]'} px-3 py-2.5`}>Книга</th>
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
                                            {showDepartment && (
                                                <td className="truncate px-3 py-2.5 text-slate-600">{row.department_name || '—'}</td>
                                            )}
                                            <td className="px-3 py-2.5">
                                                <div className="truncate text-slate-800">{row.book_title}</div>
                                                {(row.book_author || row.book_archived) && (
                                                    <div className="truncate text-[12px] text-slate-500">
                                                        {[row.book_author, row.book_archived && 'в архиве'].filter(Boolean).join(' · ')}
                                                    </div>
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
            </section>
        </div>
    );
};

export default LibraryMonitoring;
