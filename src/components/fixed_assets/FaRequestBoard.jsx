import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Inbox } from 'lucide-react';
import { IosSegmented } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import { BOARD_COLUMNS, BOARD_PAGE, KIND_LABELS, boardColumns, dropTargets, errorText } from './fixedAssetsMeta';
import { ErrorNote, EmptyState, FilterMenu, RequestBoardCard, SEARCH_WIDTH, SearchField, useDebounced } from './fixedAssetsUi';

/*
 * «Запросы» канбан-доской (владелец 17.11.2026: «запросы надо бы оформить в виде канбан доски»).
 * Колонка — статус запроса, по пути запроса: черновики → на проверке → на подписании → ждут
 * оригинал → закрыты; отклонённые — последней колонкой. Вид колонок и карточек — как у доски
 * «Задач»: серая подложка в длину своих карточек, белые карточки, счётчик в шапке.
 *
 * Перетащить карточку в колонку — значит сделать шаг запроса: в «Ждут оригинал» — приложить скан,
 * в «Закрыты» — отметить оригинал, в «Отклонены» — отказать. Бросок открывает карточку запроса
 * сразу на этом шаге (скан, дата, причина отказа — там). Колонки, куда шага нет, гаснут. Права —
 * со списка действий сервера, экран их не решает. На телефоне перетаскивания нет (оно мышиное):
 * колонка одна, выбор колонки — над ней, шаги — кнопками в карточке.
 */
const KIND_OPTIONS = Object.entries(KIND_LABELS).map(([value, label]) => ({ value, label }));
const DOT = {
    slate: 'bg-slate-400', amber: 'bg-amber-500', blue: 'bg-blue-500', violet: 'bg-violet-500', green: 'bg-emerald-500',
    rose: 'bg-rose-500',
};
/* Сетка колонок — стилем: оболочка телефона схлопывает сетки grid-cols-[…] и переносит flex-ряды. */
const columnsGrid = (count) => ({
    display: 'grid', gap: 12, alignItems: 'start', gridTemplateColumns: `repeat(${count}, minmax(236px, 1fr))`,
});
const SWITCH_ROW = { display: 'grid', gap: 8, gridAutoFlow: 'column', gridAutoColumns: 'max-content' };
const EMPTY = { columns: {}, waiting: 0 };

export default function FaRequestBoard({ api, context, version, onOpenRequest, onStep }) {
    const phone = useIsMobileShell();
    const [scope, setScope] = useState('all');
    const [kind, setKind] = useState('');
    const [search, setSearch] = useState('');
    const query = useDebounced(search.trim());
    const [board, setBoard] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [loadingMore, setLoadingMore] = useState('');
    const [dragged, setDragged] = useState(null);       // { item, targets: { колонка: шаг } }
    const [hover, setHover] = useState('');
    const [phoneColumn, setPhoneColumn] = useState('');

    const filters = useMemo(() => {
        const params = {};
        if (scope === 'waiting') params.waiting = 1;
        if (kind) params.kind = kind;
        if (query) params.q = query;
        return params;
    }, [scope, kind, query]);

    /* Ответ на прежний отбор, пришедший позже нового, не должен затереть доску. */
    const ticket = useRef(0);
    const load = useCallback(async () => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        setLoading(true);
        try {
            const { data } = await api.get('/requests/board', filters);
            if (ticket.current !== mine) return;
            const columns = {};
            (data.columns || []).forEach((column) => { columns[column.status] = { total: column.total || 0, items: column.items || [] }; });
            setBoard({ columns, waiting: Number(data.counters?.waiting) || 0 });
            setError('');
        } catch (requestError) {
            if (ticket.current === mine) setError(errorText(requestError, 'Запросы не загрузились'));
        } finally {
            if (ticket.current === mine) setLoading(false);
        }
    }, [api, filters]);
    useEffect(() => { load(); }, [load, version]);

    /* «Посмотреть ещё»: следующая страница одной колонки, к уже показанным. */
    const loadMore = async (status) => {
        const column = board?.columns?.[status];
        if (!column || loadingMore) return;
        const mine = ticket.current;
        setLoadingMore(status);
        try {
            const { data } = await api.get('/requests/board', { ...filters, status, offset: column.items.length, limit: BOARD_PAGE });
            if (ticket.current !== mine) return;
            const page = (data.columns || [])[0];
            if (!page) return;
            setBoard((prev) => {
                const current = prev.columns[status] || { total: 0, items: [] };
                const known = new Set(current.items.map((item) => item.id));
                const items = [...current.items, ...(page.items || []).filter((item) => !known.has(item.id))];
                return { ...prev, columns: { ...prev.columns, [status]: { total: Math.max(page.total || 0, items.length), items } } };
            });
        } catch (requestError) {
            if (ticket.current === mine) setError(errorText(requestError, 'Не удалось показать ещё'));
        } finally {
            setLoadingMore('');
        }
    };

    const data = board || EMPTY;
    const totals = useMemo(() => Object.fromEntries(BOARD_COLUMNS.map((column) => [
        column.status, Number(data.columns[column.status]?.total || 0)])), [data]);
    const columns = boardColumns(totals, { waiting: scope === 'waiting', extra: dragged ? Object.keys(dragged.targets) : [] });
    const everything = BOARD_COLUMNS.reduce((sum, column) => sum + totals[column.status], 0);
    const movable = !phone && Object.values(data.columns).some((column) => column.items.some((item) => Object.keys(dropTargets(item)).length));
    const narrowed = Boolean(kind || query);

    /* Несомая карточка — ещё и в ref: dragover и drop приходят раньше, чем React перерисует колонки
       по новому состоянию, и без ref первый бросок после быстрого рывка терялся. */
    const draggedRef = useRef(null);
    const startDrag = (event, item) => {
        const next = { item, targets: dropTargets(item) };
        draggedRef.current = next;
        setDragged(next);
        event.dataTransfer.effectAllowed = 'move';
        try { event.dataTransfer.setData('text/plain', String(item.id)); } catch (dragError) { /* Safari */ }
    };
    const endDrag = () => { draggedRef.current = null; setDragged(null); setHover(''); };
    const drop = (status) => {
        const payload = draggedRef.current;
        endDrag();
        const step = payload?.targets?.[status];
        if (step) onStep(payload.item.id, step);
    };

    const renderColumn = (column, single = false) => {
        const holder = data.columns[column.status] || { total: 0, items: [] };
        const hidden = Math.max(0, holder.total - holder.items.length);
        const accepts = Boolean(dragged?.targets?.[column.status]);
        const home = dragged?.item?.status === column.status;
        const lit = accepts && hover === column.status;
        return (
            <section
                key={column.status}
                aria-label={column.title}
                onDragOver={(event) => {
                    const carried = draggedRef.current;
                    if (!carried) return;
                    if (carried.targets[column.status]) {
                        event.preventDefault();
                        event.dataTransfer.dropEffect = 'move';
                    }
                    if (hover !== column.status) setHover(column.status);
                }}
                onDragLeave={(event) => {
                    if (!event.currentTarget.contains(event.relatedTarget)) setHover((prev) => (prev === column.status ? '' : prev));
                }}
                onDrop={(event) => { event.preventDefault(); drop(column.status); }}
                className={single ? 'min-w-0' : `min-w-0 rounded-[18px] p-2 transition ${
                    lit ? 'bg-blue-50 ring-1 ring-blue-300' : accepts ? 'bg-blue-50/60 ring-1 ring-blue-200' : 'bg-slate-200/50'} ${
                    dragged && !accepts && !home ? 'opacity-45' : ''}`}
            >
                {!single && (
                    <header className="flex items-center gap-2 px-1.5 pb-2.5 pt-1">
                        <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT[column.tone]}`} />
                        <span className="truncate text-[14px] font-semibold tracking-[-0.01em] text-slate-900">{column.title}</span>
                        <span className="rounded-full bg-white px-1.5 py-[1px] text-[11.5px] font-semibold tabular-nums text-slate-500 shadow-[0_0_0_1px_rgba(15,23,42,0.06)]">
                            {holder.total}
                        </span>
                    </header>
                )}
                <div className="flex min-h-[72px] flex-col gap-2">
                    {holder.items.length === 0 && (
                        <p className="px-1.5 py-3 text-[12px] text-slate-400">{accepts ? 'Отпустите здесь' : 'Пусто'}</p>
                    )}
                    {holder.items.map((item) => {
                        const canMove = !phone && Object.keys(dropTargets(item)).length > 0;
                        return (
                            <RequestBoardCard key={item.id} item={item} onOpen={onOpenRequest} draggable={canMove}
                                              dragging={dragged?.item?.id === item.id} onDragStart={startDrag} onDragEnd={endDrag} />
                        );
                    })}
                    {hidden > 0 && (
                        <button type="button" onClick={() => loadMore(column.status)} disabled={loadingMore === column.status}
                                className="mt-0.5 rounded-xl px-2 py-2 text-[12.5px] font-semibold text-blue-600 transition hover:bg-white/80 disabled:opacity-60">
                            {loadingMore === column.status ? 'Загружаем…' : `Посмотреть ещё · не показано ${hidden}`}
                        </button>
                    )}
                </div>
            </section>
        );
    };

    /* Телефон: одна колонка во всю ширину и выбор колонки над ней — как на доске «Задач». Пока
       колонку не выбрали — первая непустая из рабочих. */
    const firstBusy = columns.find((column) => !column.done && totals[column.status] > 0) || columns[0];
    const phoneActive = columns.find((column) => column.status === phoneColumn) || firstBusy;

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
                <div className={phone ? 'w-full' : 'w-[300px]'}>
                    <IosSegmented
                        stretch
                        value={scope}
                        onChange={setScope}
                        ariaLabel="Какие запросы показать"
                        options={[
                            { value: 'all', label: 'Все запросы' },
                            { value: 'waiting', label: 'Ждут меня', count: data.waiting || Number(context.waiting) || undefined },
                        ]}
                    />
                </div>
                <SearchField className={SEARCH_WIDTH} value={search} onChange={setSearch} placeholder="Номер, ОС, сотрудник" ariaLabel="Поиск запроса" />
                <FilterMenu title="Вид" allLabel="Все виды" value={kind} options={KIND_OPTIONS} onChange={setKind} />
                {movable && (
                    <span className="ml-auto hidden text-[12px] text-slate-400 lg:inline">Карточку можно перетащить на следующий шаг</span>
                )}
            </div>

            <ErrorNote text={error} />

            {!board && loading && (
                <div className="overflow-x-auto pb-1">
                    <div style={columnsGrid(5)}>
                        {[0, 1, 2, 3, 4].map((index) => (
                            <div key={index} className="h-48 animate-pulse rounded-[18px] bg-slate-200/50" />
                        ))}
                    </div>
                </div>
            )}

            {board && everything === 0 && !dragged && (
                <EmptyState icon={Inbox} title={narrowed ? 'Ничего не нашлось' : (scope === 'waiting' ? 'Вас ничего не ждёт' : 'Запросов пока нет')}>
                    {narrowed ? 'Измените запрос или отбор.' : null}
                </EmptyState>
            )}

            {board && everything > 0 && !phone && (
                <div className={`overflow-x-auto pb-1 transition-opacity ${loading ? 'opacity-60' : ''}`}>
                    <div style={columnsGrid(columns.length)}>
                        {columns.map((column) => renderColumn(column))}
                    </div>
                </div>
            )}

            {board && everything > 0 && phone && phoneActive && (
                <div className="space-y-3">
                    <div className="overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" role="tablist" aria-label="Колонки доски">
                        <div style={SWITCH_ROW}>
                            {columns.map((column) => {
                                const active = column.status === phoneActive.status;
                                return (
                                    <button key={column.status} type="button" role="tab" aria-selected={active}
                                            onClick={() => setPhoneColumn(column.status)}
                                            className={`inline-flex h-9 items-center gap-2 whitespace-nowrap rounded-full px-3.5 text-[13.5px] font-semibold transition ${active
                                                ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12),0_0_0_1px_rgba(15,23,42,0.05)]'
                                                : 'bg-slate-200/60 text-slate-600'}`}>
                                        <span aria-hidden="true" className={`h-2 w-2 rounded-full ${DOT[column.tone]}`} />
                                        {column.title}
                                        <span className="tabular-nums text-slate-400">{totals[column.status]}</span>
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                    {renderColumn(phoneActive, true)}
                </div>
            )}
        </div>
    );
}
