import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { AlertTriangle, Loader2 } from 'lucide-react';
import { IosSegmented } from '../ui/ios';
import FullscreenSheet from '../common/FullscreenSheet';
import useIsMobileShell from '../common/useIsMobileShell';
import { groupTasksByDay } from '../tasks/boardGrouping';
import PaymentsFilters from './PaymentsFilters';
import {
    BOARD_CHUNK_SIZES, BOARD_CHUNK_STORAGE_KEY, CARD_RECIPIENT_SHORT, COLUMN_SHEET_PAGE, DOCS_STATUS_META, EMPTY_FILTERS,
    REQUEST_FORMS, activeFilterCount, canDropTo, cardFaces, filtersToParams, fmtDateShort, fmtMoney, normalizeBoardChunk,
    plural, requestType,
} from './paymentsMeta';
import {
    CardFaces, CardNumber, DueChip, FormSelect, MetaChip, NoticeBox, TypePill, errorText, requestCardFrame,
} from './paymentsUi';

/*
 * Канбан-доска раздела: «Согласование закупа», «Бухгалтерия / Оплата счетов»,
 * «Финансовый отдел / Пополнение карт» (ТЗ «Закуп и оплата», пп. 7–9).
 *
 * Доска — не процесс и не копия заявок, а вид на подзадачи исполнителя (п. 2):
 * заявка одна, на доске она стоит в колонке, которая отвечает состоянию её
 * подзадачи. Колонки, их названия и подписи приходят с сервера.
 *
 * Вид и поведение — как у доски раздела «Задачи» (TaskBoardWorkspace.jsx):
 * колонки одной ширины, у пустой — подпись, что в неё попадает; в колонке одна
 * порция карточек (по 20, 40 или 60 — выбор запоминается), остальное — в окне
 * колонки, которое догружается при прокрутке.
 *
 * Перетаскивание работает между РАБОЧИМИ колонками («Новые счета» → «Проверка» →
 * «Готово к оплате» → «На оплате»): это смена статуса без условий. В
 * «Оплачено», «Согласовано», «Требуется уточнение» карточку переносит действие
 * со своими обязательными данными (платёжка, комментарий, причина) — отпущенная
 * над такой колонкой карточка открывает заявку, где это действие делается.
 *
 * На телефоне — одна колонка и переключатель над ней: три-восемь колонок в
 * ширину экрана превращаются в полоски.
 */

const SEARCH_DEBOUNCE_MS = 300;

/* Окно колонки стоит под окном заявки, которую из него открывают. На компьютере
   окно заявки — z 90, окно колонки — ниже (и выше раздела). На телефоне оба —
   слой экранов, 120 (ниже уже лежит затемнение оболочки), и порядок решает
   место в документе: окно колонки рисуется внутри доски, раньше окна заявки. */
const SHEET_Z = 85;
const SHEET_MOBILE_Z = 120;
const SHEET_OFFSET_LEFT = 'var(--app-sidebar-offset, 0px)';

const readChunk = () => {
    try {
        return normalizeBoardChunk(window.localStorage.getItem(BOARD_CHUNK_STORAGE_KEY));
    } catch {
        return normalizeBoardChunk(null);
    }
};

/*
 * Карточка заявки. Сверху — тип заявки (цвет и значок, им же окрашена полоса у
 * левого края: закуп по счёту, закуп на карту, регулярный платёж) и номер;
 * название — главным; под ним серым то, что называют пп. 7 и 9 ТЗ (поставщик
 * или получатель, компания и подразделение); внизу — «инициатор → у кого этап»
 * кружками и срок, справа сумма. Кроме типа, цвет только у того, что требует
 * внимания: срок, уточнение, дубль.
 */
const BoardCard = ({ card, board, draggable = false, dragging = false, soonDays, onOpen, onDragStart, onDragEnd, loadCardNumber, showToast }) => {
    const finance = board.code === 'finance';
    const accounting = board.code === 'accounting';
    const place = [card.legal_entity_name, card.department_name].filter(Boolean).join(' · ');
    const clarify = card.column === 'clarification'
        ? [card.subtask?.clarify_label || 'Ждём ответа инициатора', card.subtask?.clarify_comment].filter(Boolean).join(' — ')
        : '';
    const docs = card.column === 'awaiting_docs' ? (DOCS_STATUS_META[card.closing_docs_status] || DOCS_STATUS_META.none).label : '';
    const supplier = card.counterparty_name || '';
    const type = requestType(card);
    return (
        <article
            data-card={card.id}
            draggable={draggable}
            onDragStart={draggable ? (event) => onDragStart(event, card) : undefined}
            onDragEnd={draggable ? onDragEnd : undefined}
            onClick={() => onOpen(card.id)}
            // С клавиатуры карточка открывается так же, как щелчком; Enter на кнопке внутри неё — не наш.
            tabIndex={0}
            onKeyDown={(event) => { if (event.key === 'Enter' && event.target === event.currentTarget) onOpen(card.id); }}
            className={`group ${requestCardFrame(type)} flex cursor-pointer flex-col py-2.5 pl-4 pr-2.5 outline-none transition hover:border-slate-300 hover:shadow-[0_4px_14px_rgba(15,23,42,0.08)] focus-visible:ring-2 focus-visible:ring-blue-400 ${dragging ? 'opacity-40' : ''}`}
        >
            <div className="flex items-center justify-between gap-2">
                <TypePill request={card} />
                <span className="shrink-0 text-[11px] tabular-nums text-slate-400" title="Номер заявки">№{card.id}</span>
            </div>
            <h4 className="mt-1.5 text-[13px] font-medium leading-snug text-slate-800 line-clamp-2">{card.expense_name}</h4>

            {finance ? (
                <>
                    {/* П. 9: получатель и тип карты («сотрудник/поставщик»); тип — меткой,
                        чтобы длинное имя его не обрезало. */}
                    <div className="mt-1 flex items-center gap-1.5" title={`Получатель: ${card.card_holder_name || 'не указан'}`}>
                        <span className="min-w-0 grow basis-0 truncate text-[12px] text-slate-600">{card.card_holder_name || 'Получатель не указан'}</span>
                        {CARD_RECIPIENT_SHORT[card.card_recipient] && <MetaChip title="Чья карта">{CARD_RECIPIENT_SHORT[card.card_recipient]}</MetaChip>}
                    </div>
                    {/* Полный номер — по кнопке и только здесь: доску финансового отдела
                        открывают те, кому номер положен (п. 5.2), а показ пишется в журнал
                        заявки. Рост строки — по значкам показанного номера: карточка не
                        дёргается, когда номер раскрывают. */}
                    <div className="flex min-h-[24px] items-center text-[12px]" onClick={(event) => event.stopPropagation()} role="presentation">
                        {card.card_mask
                            ? <CardNumber compact mask={card.card_mask} canReveal load={() => loadCardNumber(card.id)} showToast={showToast} />
                            : <span className="text-slate-400">Номер карты не указан</span>}
                    </div>
                    {card.payment_purpose && (
                        <p className="text-[11.5px] leading-snug text-slate-500 line-clamp-2" title={`Назначение перевода: ${card.payment_purpose}`}>{card.payment_purpose}</p>
                    )}
                </>
            ) : supplier && (
                <p className="mt-0.5 truncate text-[12px] text-slate-500" title={supplier}>{supplier}</p>
            )}
            {accounting && card.invoice_number && (
                <p className="truncate text-[11.5px] tabular-nums text-slate-500" title="Счёт поставщика">
                    Счёт №{card.invoice_number}{card.invoice_date ? ` от ${fmtDateShort(card.invoice_date)}` : ''}
                </p>
            )}
            {place && <p className="mt-0.5 truncate text-[11.5px] text-slate-400" title={`Компания и подразделение: ${place}`}>{place}</p>}
            {finance && card.approved_by_name && (
                <p className="truncate text-[11.5px] text-slate-400" title={`Согласовал: ${card.approved_by_name}`}>Согласовал {card.approved_by_name}</p>
            )}
            {docs && <p className="truncate text-[11.5px] text-slate-500" title="Закрывающие документы">{docs}</p>}
            {card.duplicate && (
                <p className="mt-1.5 flex items-center gap-1 text-[11.5px] font-medium text-amber-700" title="Тот же поставщик, номер счёта, сумма и дата уже есть в другой заявке">
                    <AlertTriangle size={11} strokeWidth={2.25} className="shrink-0" aria-hidden="true" />
                    Возможный дубль
                </p>
            )}
            {clarify && <p className="mt-1.5 text-[11.5px] leading-snug text-amber-700 line-clamp-2" title={clarify}>{clarify}</p>}

            {/* Низом карточки: в сетке окна колонки соседние карточки одной высоты, и
                сумма у всех стоит на одной линии. */}
            <div className="mt-auto flex items-center gap-1.5 pt-2">
                <CardFaces faces={cardFaces(card)} />
                <span className="flex min-w-0 grow basis-0 items-center gap-1">
                    <DueChip request={card} soonDays={soonDays} />
                </span>
                <span className="shrink-0 text-[13px] font-semibold tabular-nums text-slate-900" title="Сумма заявки">{fmtMoney(card.amount)}</span>
            </div>
        </article>
    );
};

/*
 * Окно колонки («Посмотреть ещё») — как у «Задач»: вся колонка во весь экран,
 * по дням создания заявки, догружается при прокрутке. Перетаскивания здесь нет:
 * заявку открывают щелчком и действуют в ней.
 */
const ColumnSheet = ({ open, board, column, query, apiBaseUrl, headers, soonDays, isMobile, requestOpen, refreshKey, onOpen, onClose, loadCardNumber, showToast }) => {
    const [cards, setCards] = useState([]);
    const [total, setTotal] = useState(column.count || 0);
    const [loading, setLoading] = useState(true);
    const [failed, setFailed] = useState(false);
    const sentinelRef = useRef(null);
    const ticket = useRef(0);
    const busy = useRef(false);
    const shown = useRef(0);
    shown.current = cards.length;

    const loadPage = useCallback(async (offset, limit = COLUMN_SHEET_PAGE) => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        busy.current = true;
        setLoading(true);
        try {
            const params = new URLSearchParams(query);
            params.set('column', column.key);
            params.set('limit', String(limit));
            params.set('offset', String(offset));
            const response = await axios.get(`${apiBaseUrl}/api/payments/boards/${board.code}?${params}`, { headers: headers() });
            if (ticket.current !== mine) return;
            const got = response.data?.columns?.[0] || { cards: [], count: 0 };
            setTotal(Number(got.count) || 0);
            // Колонка могла сдвинуться, пока листали, — склеиваем по номеру заявки.
            setCards((prev) => (offset === 0 ? got.cards : [...new Map([...prev, ...got.cards].map((card) => [card.id, card])).values()]));
            setFailed(false);
        } catch {
            if (ticket.current === mine) setFailed(true);
        } finally {
            if (ticket.current === mine) {
                busy.current = false;
                setLoading(false);
            }
        }
    }, [apiBaseUrl, headers, board.code, column.key, query]);

    // Открыли (или после действия над заявкой) — перечитываем столько, сколько уже
    // долистали (сервер отдаёт до 100 за раз). Закрытое окно ничего не грузит.
    useEffect(() => {
        if (!open) return;
        loadPage(0, Math.min(100, Math.max(COLUMN_SHEET_PAGE, shown.current)));
    }, [open, loadPage, refreshKey]);

    const hasMore = cards.length < total;
    useEffect(() => {
        const node = sentinelRef.current;
        if (!node || !hasMore || failed) return undefined;
        const observer = new IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting) && !busy.current) loadPage(cards.length);
        }, { rootMargin: '240px' });
        observer.observe(node);
        return () => observer.disconnect();
    }, [hasMore, failed, loadPage, cards.length]);

    const days = useMemo(() => groupTasksByDay(cards), [cards]);

    return (
        <FullscreenSheet
            open={open}
            wide
            z={isMobile ? SHEET_MOBILE_Z : SHEET_Z}
            offsetLeft={SHEET_OFFSET_LEFT}
            // Esc при открытой заявке закрывает заявку, а не оба слоя сразу.
            closeOnEscape={!requestOpen}
            icon="fa-layer-group"
            title={column.label}
            subtitle={total > 0
                ? `${cards.length} из ${plural(total, REQUEST_FORMS)}${isMobile ? '' : ' · Esc чтобы выйти'}`
                : (isMobile ? '' : 'Esc чтобы выйти')}
            onClose={onClose}
        >
            <div className="space-y-5" data-column-sheet={column.key}>
                {!days.length && !loading && !failed && (
                    <div className="grid place-items-center rounded-2xl border border-dashed border-slate-200 px-6 py-12 text-center">
                        <p className="text-[13.5px] font-medium text-slate-600">Пусто</p>
                        <p className="mt-1 max-w-sm text-[12px] leading-relaxed text-slate-400">В колонке «{column.label}» сейчас нет заявок.</p>
                    </div>
                )}

                {days.map((day) => (
                    <section key={day.key}>
                        <header className="mb-2 flex items-baseline gap-2 px-0.5">
                            <h4 className="text-[13px] font-semibold text-slate-800">{day.label}</h4>
                            <span className="text-[11.5px] tabular-nums text-slate-400">{plural(day.tasks.length, REQUEST_FORMS)}</span>
                        </header>
                        {/* Сеткой, а не лентой вбок: за день бывает и пятьдесят заявок. */}
                        <div className="grid gap-2.5 [grid-template-columns:repeat(auto-fill,minmax(252px,1fr))]">
                            {day.tasks.map((card) => (
                                <BoardCard key={card.id} card={card} board={board} soonDays={soonDays} onOpen={onOpen}
                                    loadCardNumber={loadCardNumber} showToast={showToast} />
                            ))}
                        </div>
                    </section>
                ))}

                <div ref={sentinelRef} className="h-px" />

                {loading && (
                    <div className="grid gap-2.5 [grid-template-columns:repeat(auto-fill,minmax(252px,1fr))]">
                        {[0, 1, 2].map((index) => <div key={index} className="h-24 animate-pulse rounded-xl bg-slate-200/70" />)}
                    </div>
                )}
                {failed && (
                    <p className="px-1 text-[12px] text-rose-500">Не удалось загрузить продолжение. Закройте окно и откройте снова.</p>
                )}
                {!hasMore && !loading && days.length > 0 && (
                    <p className="px-1 pb-2 text-[11.5px] text-slate-400">Это все заявки в колонке.</p>
                )}
            </div>
        </FullscreenSheet>
    );
};

const PaymentsBoard = ({ code, apiBaseUrl, headers, dictionaries, users, meta, soonDays, refreshKey, requestOpen = false, onOpen, onChanged, showToast }) => {
    const board = useMemo(() => (meta?.boards || []).find((item) => item.code === code) || null, [meta, code]);
    const isMobile = useIsMobileShell();
    const [columns, setColumns] = useState([]);
    const [scope, setScope] = useState('all');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [query, setQuery] = useState('');
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [chunk, setChunk] = useState(readChunk);
    const [dragged, setDragged] = useState(null);
    const [hover, setHover] = useState(null);
    const [mobileColumn, setMobileColumn] = useState(null);
    /* Окно колонки: какая колонка и открыто ли. Колонка помнится и после закрытия —
       окно гаснет плавно (FullscreenSheet), и ему нужно, что показывать, пока гаснет. */
    const [sheet, setSheet] = useState({ key: null, open: false });

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    useEffect(() => {
        const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [search]);

    // Отбор доски одной строкой: его же берёт окно колонки, чтобы листать ровно то, что на доске.
    const selection = useMemo(() => {
        const params = filtersToParams(filters);
        if (query) params.set('q', query);
        return params.toString();
    }, [filters, query]);

    const ticket = useRef(0);
    const load = useCallback(async () => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        setLoading(true);
        setError('');
        try {
            const params = new URLSearchParams(selection);
            params.set('limit', String(chunk));
            const response = await axios.get(`${apiBaseUrl}/api/payments/boards/${code}?${params}`, { headers: headers() });
            if (ticket.current !== mine) return;
            setColumns(response.data?.columns || []);
            setScope(response.data?.scope || 'all');
        } catch (err) {
            if (ticket.current !== mine) return;
            setError(errorText(err, 'Не удалось загрузить доску'));
        } finally {
            if (ticket.current === mine) setLoading(false);
        }
    }, [apiBaseUrl, headers, code, selection, chunk]);

    useEffect(() => { load(); }, [load, refreshKey]);

    const changeChunk = (value) => {
        const next = normalizeBoardChunk(value);
        setChunk(next);
        try { window.localStorage.setItem(BOARD_CHUNK_STORAGE_KEY, String(next)); } catch { /* приватный режим — запомнится до перезагрузки */ }
    };

    const loadCardNumber = useCallback(async (requestId) => {
        const response = await axios.get(`${apiBaseUrl}/api/payments/requests/${requestId}/card`, { headers: headers() });
        return response.data;
    }, [apiBaseUrl, headers]);

    const endDrag = () => { setDragged(null); setHover(null); };

    const drop = async (columnKey) => {
        const card = dragged;
        endDrag();
        if (!card || card.column === columnKey) return;
        if (!canDropTo(board, card, columnKey)) {
            // В эту колонку ведёт действие — открываем заявку, где оно делается.
            onOpen(card.id);
            return;
        }
        // Карточка встаёт на место сразу; если сервер откажет — доска перечитается.
        setColumns((prev) => prev.map((column) => {
            if (column.key === card.column) return { ...column, count: column.count - 1, cards: column.cards.filter((item) => item.id !== card.id) };
            if (column.key === columnKey) return { ...column, count: column.count + 1, cards: [{ ...card, column: columnKey, subtask: { ...card.subtask, status: columnKey } }, ...column.cards] };
            return column;
        }));
        try {
            const response = await axios.post(`${apiBaseUrl}/api/payments/requests/${card.id}/move`,
                { kind: card.subtask.kind, status: columnKey }, { headers: headers() });
            onChanged?.(response.data?.request);
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось перенести карточку'), 'error');
            load();
        }
    };

    const total = columns.reduce((sum, column) => sum + column.count, 0);
    const activeMobile = columns.find((column) => column.key === mobileColumn) || columns.find((column) => column.count > 0) || columns[0];
    // Перетаскивание браузера — мышиное: на телефоне статус меняют в карточке заявки.
    const canDrag = (card) => Boolean(!isMobile && card.can_act && card.subtask?.state === 'open' && (board?.work || []).includes(card.column));
    const dragHint = !isMobile && columns.some((column) => column.cards.some(canDrag));

    /* На телефоне выбранная колонка могла оказаться за краем полосы-переключателя
       (первая непустая — «Пополнено», пятая по счёту): подкручиваем полосу к ней,
       иначе карточки видны, а чья это колонка — нет. Двигаем только саму полосу. */
    const stripRef = useRef(null);
    const activeKey = activeMobile?.key;
    useEffect(() => {
        const strip = stripRef.current;
        const tab = strip?.querySelector('[aria-selected="true"]');
        if (!strip || !tab) return;
        const stripBox = strip.getBoundingClientRect();
        const tabBox = tab.getBoundingClientRect();
        strip.scrollLeft += (tabBox.left + tabBox.width / 2) - (stripBox.left + stripBox.width / 2);
    }, [isMobile, activeKey, columns.length]);

    if (!board) return null;

    const renderColumn = (column, single = false) => {
        const allowed = !dragged || canDropTo(board, dragged, column.key) || dragged.column === column.key;
        const isHover = hover === column.key && dragged && dragged.column !== column.key;
        const hidden = Math.max(0, column.count - column.cards.length);
        let tray = 'bg-slate-100/70';
        if (isHover) tray = allowed ? 'bg-blue-50/70 ring-1 ring-blue-200' : 'bg-slate-200/70';
        return (
            <section
                key={column.key}
                data-column={column.key}
                onDragOver={(event) => {
                    if (!dragged) return;
                    event.preventDefault();
                    setHover(column.key);
                }}
                onDragLeave={() => setHover((prev) => (prev === column.key ? null : prev))}
                onDrop={(event) => { event.preventDefault(); drop(column.key); }}
                /* Одна колонка на телефоне — без подложки: рамка вокруг единственной
                   колонки во всю ширину экрана ничего не отделяет. */
                className={single ? 'flex w-full flex-col' : `flex w-[268px] shrink-0 flex-col rounded-2xl p-2 transition-colors ${tray}`}
            >
                {/* Имя и счёт колонки на телефоне уже стоят в переключателе над ней. */}
                {!single && (
                    <header className="flex items-center justify-between gap-2 px-1.5 pb-2 pt-1">
                        <span className="flex min-w-0 items-baseline gap-1.5">
                            <span className="truncate text-[12.5px] font-semibold text-slate-700" title={column.label}>{column.label}</span>
                            <span className="text-[11.5px] tabular-nums text-slate-400" title={hidden ? `Показано ${column.cards.length} из ${column.count}` : undefined}>{column.count}</span>
                        </span>
                    </header>
                )}
                <div className="flex min-h-[72px] flex-col gap-1.5">
                    {column.cards.length ? column.cards.map((card) => (
                        <BoardCard key={card.id} card={card} board={board} draggable={canDrag(card)} dragging={dragged?.id === card.id}
                            soonDays={soonDays} onOpen={onOpen}
                            onDragStart={(event, item) => {
                                event.dataTransfer.effectAllowed = 'move';
                                try { event.dataTransfer.setData('text/plain', String(item.id)); } catch { /* Safari */ }
                                setDragged(item);
                            }}
                            onDragEnd={endDrag} loadCardNumber={loadCardNumber} showToast={showToast} />
                    )) : !loading && (
                        <p className="px-1.5 py-3 text-[11.5px] text-slate-400">{column.caption || 'Пусто'}</p>
                    )}
                    {hidden > 0 && (
                        <button
                            type="button"
                            data-more={column.key}
                            onClick={() => setSheet({ key: column.key, open: true })}
                            className="mt-0.5 rounded-lg border border-dashed border-slate-300 px-2 py-2 text-[11.5px] font-medium text-slate-500 transition hover:border-slate-400 hover:bg-white hover:text-slate-700"
                        >
                            Посмотреть ещё · не показано {hidden}
                        </button>
                    )}
                </div>
            </section>
        );
    };

    const sheetColumn = sheet.key ? columns.find((column) => column.key === sheet.key) : null;
    const chunkOptions = BOARD_CHUNK_SIZES.map((size) => ({ value: size, label: `по ${size}` }));

    return (
        <div>
            <PaymentsFilters search={search} onSearch={setSearch} filters={filters} onChange={setFilters} dictionaries={dictionaries} users={users} board>
                {/* Рост — как у строки поиска и «Фильтров» рядом (FormSelect), а не 35 px списка из панели фильтров. */}
                <FormSelect className="w-[112px] shrink-0" ariaLabel="Карточек в колонке" value={chunk} options={chunkOptions} onChange={changeChunk} />
            </PaymentsFilters>

            {error && <div className="mt-3"><NoticeBox text={error} /></div>}
            <div className="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[12.5px] text-slate-500">
                <span className="flex items-center gap-2">
                    {loading && <><Loader2 size={13} className="animate-spin" /> Загружаем доску…</>}
                    {/* Пусто по отбору и пусто вообще — разные вещи; при сбое загрузки не говорим «задач нет». */}
                    {!loading && !error && (total
                        ? `${board.title} · ${scope === 'own' ? 'ваши согласования' : 'задачи подразделения'}`
                        : (query || activeFilterCount(filters)
                            ? 'Ничего не нашлось — измените запрос или фильтры'
                            : 'На доске пусто — задач для вас сейчас нет'))}
                </span>
                {dragHint && <span className="text-[11.5px] text-slate-400">Перетащите карточку между рабочими колонками, чтобы сменить статус</span>}
            </div>

            {isMobile ? (
                <div className="mt-2">
                    <div ref={stripRef} className="-mx-3 overflow-x-auto px-3 [scrollbar-width:none]">
                        <IosSegmented
                            value={activeMobile?.key}
                            options={columns.map((column) => ({ value: column.key, label: column.label, count: column.count }))}
                            onChange={setMobileColumn}
                            ariaLabel="Колонка доски"
                            className="min-w-max"
                        />
                    </div>
                    <div key={activeMobile?.key} className="mt-2 motion-safe:animate-fade-in-soft">{activeMobile && renderColumn(activeMobile, true)}</div>
                </div>
            ) : (
                <div className="-mx-1 mt-2 overflow-x-auto px-1 pb-2">
                    {/* По верху, а не во всю высоту: пустая колонка — короткий лоток с подписью,
                        а не серый столб длиной с соседнюю. */}
                    <div className="flex min-w-max items-start gap-2.5">
                        {columns.map((column) => renderColumn(column))}
                    </div>
                </div>
            )}

            {sheetColumn && (
                <ColumnSheet
                    key={sheetColumn.key}
                    open={sheet.open}
                    board={board}
                    column={sheetColumn}
                    query={selection}
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    soonDays={soonDays}
                    isMobile={isMobile}
                    requestOpen={requestOpen}
                    refreshKey={refreshKey}
                    onOpen={onOpen}
                    onClose={() => setSheet((prev) => ({ ...prev, open: false }))}
                    loadCardNumber={loadCardNumber}
                    showToast={showToast}
                />
            )}
        </div>
    );
};

export default PaymentsBoard;
