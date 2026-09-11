import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import FullscreenSheet from '../common/FullscreenSheet';
import { IosPager } from '../ui/ios';
import { formatMoment, formatNumber, shortDay } from './funnelFormat';

/**
 * Список конкретных лидов за выбранной причиной, оператором или днём.
 *
 * Это не «дополнительная таблица», а требование приёмки: клик по причине
 * закрытия обязан открывать людей, попавших под неё. Без этого «двадцать
 * отказов по комиссии» невозможно ни проверить, ни отработать — супервайзеру
 * нужны телефоны и комментарии, а не число.
 *
 * Страница тянется при открытии и при смене фильтров, а не держится в памяти:
 * за месяц у «Потока» около ста тысяч лидов, и выкачивать их целиком, чтобы
 * показать пятьдесят, незачем.
 */

const PAGE_SIZE = 50;

/* Колонки для мобильного не режем, а переносим в карточку: на телефоне таблица
   из десяти колонок бесполезна в любом виде. */
const COLUMNS = [
    { key: 'full_name', title: 'Водитель', width: 'min-w-[180px]' },
    { key: 'phone', title: 'Телефон', width: 'min-w-[130px]' },
    { key: 'operator', title: 'Оператор', width: 'min-w-[150px]' },
    { key: 'work_day', title: 'Дата', width: 'min-w-[90px]' },
    { key: 'park_name', title: 'Парк', width: 'min-w-[150px]' },
    { key: 'city', title: 'Город', width: 'min-w-[110px]' },
    { key: 'status', title: 'Статус', width: 'min-w-[150px]' },
    { key: 'reason', title: 'Причина', width: 'min-w-[170px]' },
    { key: 'comment', title: 'Комментарий', width: 'min-w-[220px]' },
];

function statusOf(lead) {
    /* У разных источников статус лежит в разных полях: у «Потока» это пара
       «статус звонка / статус диалога», у платного найма и amoCRM — один
       этап. Склеиваем то, что есть, а не заводим три вида таблицы. */
    const parts = [lead.call_status, lead.dialog_status].filter(Boolean);
    if (parts.length) return parts.join(' · ');
    return lead.stage || '';
}

export default function LeadsSheet({
    open,
    onClose,
    api,
    direction,
    period,
    filters = null,
    title = 'Лиды',
}) {
    const [rows, setRows] = useState([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    /* Ключ запроса: строка из всего, что влияет на выборку. Сравнение по строке,
       а не по объекту в зависимостях, — иначе новый литерал filters на каждом
       рендере родителя гонял бы запрос по кругу (в проекте на этом уже горели
       с нестабильными коллбэками). */
    const scope = useMemo(() => JSON.stringify({
        direction,
        from: period?.from,
        to: period?.to,
        filters: filters || {},
    }), [direction, period?.from, period?.to, filters]);

    const load = useCallback(async (wantedPage) => {
        if (!open || !api || !direction) return;
        setLoading(true);
        setError('');
        try {
            const data = await api.leads({
                direction,
                from: period?.from,
                to: period?.to,
                limit: PAGE_SIZE,
                offset: (wantedPage - 1) * PAGE_SIZE,
                ...(filters || {}),
            });
            if (data === null) return;   // ответ устаревшего запроса, его выбросили
            setRows(data.leads || []);
            setTotal(Number(data.total) || 0);
        } catch (exc) {
            setError(exc?.message || 'Не удалось получить список лидов');
            setRows([]);
            setTotal(0);
        } finally {
            setLoading(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, api, scope]);

    /* Смена фильтров возвращает на первую страницу В ТОМ ЖЕ проходе: иначе
       второй запрос уходит со старым смещением и показывает пустоту. */
    const openedScope = useRef(scope);
    useEffect(() => {
        if (!open) return;
        const changed = openedScope.current !== scope;
        openedScope.current = scope;
        const nextPage = changed ? 1 : page;
        if (changed) {
            // Чужие лиды под новым заголовком — худший вид ошибки: числа
            // выглядят настоящими. Поэтому список гасим ДО запроса, а не после
            // ответа.
            if (page !== 1) setPage(1);
            setRows([]);
            setTotal(0);
        }
        load(nextPage);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, scope]);

    const onPage = (next) => {
        setPage(next);
        load(next);
    };

    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const from = total ? (page - 1) * PAGE_SIZE + 1 : 0;
    const to = Math.min(page * PAGE_SIZE, total);

    return (
        <FullscreenSheet
            open={open}
            onClose={onClose}
            icon="fa-list"
            title={title}
            subtitle={total
                ? `${formatNumber(total)} лидов за ${shortDay(period?.from)} — ${shortDay(period?.to)}`
                : 'Список лидов'}
        >
            {error ? (
                <div className="rounded-2xl bg-rose-50 ring-1 ring-rose-200 p-4 text-sm text-rose-700">
                    {error}
                    <button
                        type="button"
                        onClick={() => load(page)}
                        className="ml-3 underline underline-offset-2"
                    >
                        Повторить
                    </button>
                </div>
            ) : null}

            {loading && !rows.length ? (
                <div className="py-16 text-center text-sm text-slate-500">Загрузка лидов…</div>
            ) : null}

            {!loading && !rows.length && !error ? (
                <div className="py-16 text-center">
                    <FaIcon className="fas fa-inbox text-2xl text-slate-300" />
                    <p className="mt-3 text-sm text-slate-500">
                        Под этот фильтр лидов не нашлось.
                    </p>
                </div>
            ) : null}

            {rows.length ? (
                <>
                    {/* Телефон: таблица не влезает ни в каком виде, поэтому карточки. */}
                    <div className="space-y-2 sm:hidden">
                        {rows.map((lead) => (
                            <article
                                key={`${lead.lead_key}-${lead.work_day}`}
                                className="rounded-2xl bg-white ring-1 ring-slate-200/70 p-3"
                            >
                                <div className="flex items-baseline justify-between gap-2">
                                    <span className="text-[13px] font-medium text-slate-900 truncate">
                                        {lead.full_name || '—'}
                                    </span>
                                    <span className="text-[11px] text-slate-400 tabular-nums shrink-0">
                                        {shortDay(lead.work_day)}
                                    </span>
                                </div>
                                <div className="mt-1 text-[12px] text-slate-600 tabular-nums">
                                    {lead.phone || '—'}
                                </div>
                                <div className="mt-1 text-[12px] text-slate-500 truncate">
                                    {lead.operator || 'Не сопоставлен'} · {statusOf(lead) || '—'}
                                </div>
                                {lead.reason ? (
                                    <div className="mt-1 text-[12px] text-slate-700">{lead.reason}</div>
                                ) : null}
                                {lead.comment ? (
                                    <div className="mt-1 text-[12px] text-slate-500">{lead.comment}</div>
                                ) : null}
                            </article>
                        ))}
                    </div>

                    <div className="hidden sm:block overflow-x-auto rounded-2xl ring-1 ring-slate-200/70 bg-white">
                        <table className="min-w-full text-[13px]">
                            <thead className="sticky top-0 bg-slate-50/95 backdrop-blur">
                                <tr>
                                    {COLUMNS.map((column) => (
                                        <th
                                            key={column.key}
                                            className={`px-3 py-2 text-left font-medium text-slate-500 whitespace-nowrap ${column.width}`}
                                        >
                                            {column.title}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((lead) => (
                                    <tr
                                        key={`${lead.lead_key}-${lead.work_day}`}
                                        className="border-t border-slate-100 hover:bg-slate-50/60"
                                    >
                                        <td className="px-3 py-2 text-slate-900">{lead.full_name || '—'}</td>
                                        <td className="px-3 py-2 text-slate-700 tabular-nums whitespace-nowrap">
                                            {lead.phone || '—'}
                                        </td>
                                        <td className="px-3 py-2 text-slate-700">
                                            {lead.operator || (
                                                <span className="text-amber-700">Не сопоставлен</span>
                                            )}
                                        </td>
                                        <td className="px-3 py-2 text-slate-500 tabular-nums whitespace-nowrap">
                                            {shortDay(lead.work_day)}
                                        </td>
                                        <td className="px-3 py-2 text-slate-600">{lead.park_name || '—'}</td>
                                        <td className="px-3 py-2 text-slate-600">{lead.city || '—'}</td>
                                        <td className="px-3 py-2 text-slate-600">{statusOf(lead) || '—'}</td>
                                        <td className="px-3 py-2 text-slate-700">{lead.reason || '—'}</td>
                                        <td className="px-3 py-2 text-slate-500">{lead.comment || ''}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <div className="mt-3">
                        <IosPager
                            page={page}
                            pageCount={pageCount}
                            total={total}
                            from={from}
                            to={to}
                            onPage={onPage}
                            unit="лиды"
                        />
                    </div>
                    {loading ? (
                        <p className="mt-2 text-center text-xs text-slate-400">Обновляем…</p>
                    ) : null}
                </>
            ) : null}
        </FullscreenSheet>
    );
}
