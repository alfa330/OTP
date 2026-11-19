/* Доска «Запросы» раздела «Карточка клиента».
 *
 * Владелец, 19.11.2026: «такую канбан-доску вместо текущего раздела запросы, заголовки можно
 * такие же» — доска, по которой отдел обработки запросов ведёт запросы техподдержки Тез КЦ
 * в Битрикс24. Колонки — стадии запроса в их порядке (client_cards/rules.py), новый запрос из
 * карточки звонка встаёт в «Новое обращение». Карточку перетаскивают в другую колонку или
 * меняют стадию в окне запроса; перенесённая встаёт наверх колонки.
 *
 * Кто что видит, решает сервер: ведущие доску (руководители отдела и названные поимённо) —
 * все запросы отдела и переносят их; оператор — свои запросы, без переноса: следит за
 * стадией и пишет в переписку.
 *
 * Восемь колонок шире экрана: любую можно свернуть в узкую полоску со счётчиком (запоминается
 * в браузере) — как на доске «Воронки найма». Колонка приходит первыми карточками, остальное —
 * «Показать ещё». Доска перечитывается раз в полминуты и при возврате во вкладку; пока
 * карточку тянут или перенос не подтверждён, фоновый ответ не применяется — он мог уйти до
 * переноса и вернул бы карточку на старое место.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronsLeft, ChevronsRight, Loader2, RefreshCw, Search, X } from 'lucide-react';

import { IosModal, iosBtnSecondary, iosCard } from '../ui/ios';
import { Thread } from './RequestsPanel';
import {
    appendToColumn, errorText, fmtWhen, initialsOf, mergeBoard, moveOnBoard, replaceOnBoard,
} from './cardRules';
import { EmptyState, Spinner } from './ui';

const POLL_MS = 30000;
const COLLAPSED_KEY = 'client_cards.board.collapsed';
/* Колонки делят ширину поровну и не уже 184 px: все восемь стадий видны разом уже на экране
   в 1600 px, как на доске в Битриксе; на узком — прокрутка вбок. */
const COLUMN_WIDTH = 'min-w-[184px] flex-1 basis-0';
/* Полоса над колонкой — цвет стадии (как цветные заголовки доски в Битриксе), но цвет только
   у стадий, где он что-то значит; остальные — нейтральные. Тон — тот же, что у бейджа стадии. */
const STRIPES = { new: 'bg-blue-500', needs_info: 'bg-amber-400', failed: 'bg-rose-500', resolved: 'bg-emerald-500' };

const readCollapsed = () => {
    try {
        const raw = JSON.parse(window.localStorage.getItem(COLLAPSED_KEY) || '[]');
        return Array.isArray(raw) ? raw : [];
    } catch {
        return [];
    }
};

const accountLine = (item) => {
    if (item.account) {
        return `${item.account.kind_label} · ${item.account.full_name || `ID ${item.account.tez_id}`}`;
    }
    return item.phone_text || '';
};

/* Карточка запроса: номер и когда создан, причина, что нужно, чей аккаунт; внизу — оператор,
   который создал запрос, и кружок того, кто последним перенёс его по доске. */
const BoardCard = ({ item, userId, showAuthor, draggable, onOpen, onDragStart, onDragEnd }) => {
    // Стадию показывает колонка; бейдж на карточке — только если запрос не дошёл до своей группы.
    const undelivered = item.delivery_status === 'failed' || item.delivery_status === 'pending';
    // «Непрочитано» — счётчик автора запроса: ведущему доску чужая точка ничего не говорит.
    const fresh = item.unread > 0 && Number(item.created_by) === Number(userId);
    const who = accountLine(item);
    return (
        <article
            draggable={draggable}
            onDragStart={draggable ? (event) => onDragStart(event, item) : undefined}
            onDragEnd={draggable ? onDragEnd : undefined}
            onClick={() => onOpen(item.id)}
            onKeyDown={(event) => { if (event.key === 'Enter' && event.target === event.currentTarget) onOpen(item.id); }}
            role="button"
            tabIndex={0}
            className={`${iosCard} relative cursor-pointer p-3 transition hover:ring-slate-300 ${draggable ? 'active:cursor-grabbing' : ''}`}
        >
            <div className="flex items-center gap-1.5">
                {fresh && <span className="h-2 w-2 shrink-0 rounded-full bg-blue-600" aria-label="Есть новое" />}
                <span className="shrink-0 text-[13px] font-semibold text-slate-900">№{item.id}</span>
                {item.destination_title && <span className="min-w-0 truncate text-[12px] text-slate-400">· {item.destination_title}</span>}
                <span className="ml-auto shrink-0 text-[11.5px] tabular-nums text-slate-400">{fmtWhen(item.created_at)}</span>
            </div>
            {item.topic_title && <div className="mt-1 line-clamp-2 text-[12.5px] font-medium leading-snug text-slate-800">{item.topic_title}</div>}
            {item.body && <div className="mt-0.5 line-clamp-2 text-[12px] leading-snug text-slate-500">{item.body}</div>}
            {who && <div className="mt-1.5 truncate text-[12px] text-slate-600">{who}</div>}
            <div className="mt-2 flex items-center gap-2 text-[11.5px] text-slate-400">
                <span className="min-w-0 flex-1 truncate">{showAuthor ? item.created_by_name : ''}</span>
                {undelivered && <span className="shrink-0 font-medium text-rose-600">Не доставлен</span>}
                {item.stage_changed_by_name && (
                    <span title={`Последним перенёс: ${item.stage_changed_by_name}, ${fmtWhen(item.stage_changed_at)}`}
                          className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-slate-100 text-[10.5px] font-semibold text-slate-600">
                        {initialsOf(item.stage_changed_by_name)}
                    </span>
                )}
            </div>
        </article>
    );
};

const Column = ({
    column, canMove, dragging, collapsed, onToggle, onDrop, children, more,
}) => {
    const [over, setOver] = useState(false);
    const target = Boolean(dragging) && dragging.stage !== column.stage;
    const surface = over && target ? 'bg-blue-50 ring-2 ring-blue-400'
        : target ? 'bg-slate-100/80 ring-1 ring-blue-200' : 'bg-slate-100/80';
    const stripe = STRIPES[column.stage] || 'bg-slate-300';
    const dropProps = canMove ? {
        onDragOver: (event) => { event.preventDefault(); setOver(true); },
        onDragLeave: () => setOver(false),
        onDrop: (event) => { event.preventDefault(); setOver(false); onDrop(event, column.stage); },
    } : {};
    if (collapsed) {
        return (
            <button type="button" onClick={onToggle} title={`Развернуть «${column.label}»`} {...dropProps}
                    className={`relative flex min-h-[220px] w-11 min-w-[44px] flex-col items-center gap-2 overflow-hidden rounded-2xl py-3 transition hover:bg-slate-200/70 ${surface}`}>
                <span className={`absolute inset-x-0 top-0 h-[3px] ${stripe}`} aria-hidden="true" />
                <ChevronsRight size={14} className="text-slate-400" />
                <span className="rounded-full bg-white px-1.5 py-0.5 text-[11.5px] font-semibold tabular-nums text-slate-500 ring-1 ring-slate-200/70">{column.total}</span>
                <span className="text-[12.5px] font-semibold text-slate-700 [writing-mode:vertical-rl]">{column.label}</span>
            </button>
        );
    }
    return (
        <section className={`${COLUMN_WIDTH} group/column relative flex max-h-[calc(100vh-230px)] min-h-[220px] flex-col overflow-hidden rounded-2xl p-2 transition ${surface}`}
                 aria-label={column.label} {...dropProps}>
            <span className={`absolute inset-x-0 top-0 h-[3px] ${stripe}`} aria-hidden="true" />
            {/* Счётчик — рядом с названием, как в Битриксе («Передано разработчикам 67»): длинное
                название переносится, а не обрезается. */}
            <header className="flex items-start gap-1 px-1.5 pb-2 pt-1.5">
                <h3 className="min-w-0 flex-1 text-[12.5px] font-semibold leading-snug text-slate-700">
                    {column.label}
                    <span className="ml-1.5 font-medium tabular-nums text-slate-400" data-count>{column.total}</span>
                </h3>
                <button type="button" onClick={onToggle} aria-label={`Свернуть «${column.label}»`} title="Свернуть"
                        className="-mr-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full text-slate-400 opacity-0 transition hover:bg-white hover:text-slate-600 focus-visible:opacity-100 group-hover/column:opacity-100">
                    <ChevronsLeft size={13} />
                </button>
            </header>
            <div className="thin-scroll flex min-h-0 flex-col gap-2 overflow-y-auto px-0.5 pb-1">
                {children}
                {column.items.length === 0 && <div className="py-6 text-center text-[12px] text-slate-400">Пусто</div>}
                {column.total > column.items.length && more}
            </div>
        </section>
    );
};

export default function RequestsBoard({
    api, toast, departmentId = null, canMove = false, userId = null, stages = [], limits = null,
    focusRequestId = null, onFocusHandled = null, onUnread = null,
}) {
    const [query, setQuery] = useState('');
    const [search, setSearch] = useState('');
    const [columns, setColumns] = useState(null);
    const [failed, setFailed] = useState('');
    const [loading, setLoading] = useState(false);
    const [dragging, setDragging] = useState(null);
    const [openId, setOpenId] = useState(null);
    const [collapsed, setCollapsed] = useState(readCollapsed);
    const [moreBusy, setMoreBusy] = useState('');
    const seqRef = useRef(0);
    const movingRef = useRef(0);
    const draggingRef = useRef(false);
    const unreadRef = useRef(onUnread);
    unreadRef.current = onUnread;

    // Поиск — с паузой: запрос на каждую букву ни к чему.
    useEffect(() => {
        const timer = setTimeout(() => setSearch(query.trim()), 350);
        return () => clearTimeout(timer);
    }, [query]);

    const load = useCallback(async (quiet = false) => {
        const seq = ++seqRef.current;
        if (!quiet) setLoading(true);
        try {
            const { data } = await api.board({ department_id: departmentId || undefined, q: search || undefined });
            unreadRef.current?.(data.unread_requests || 0);
            if (seq !== seqRef.current || movingRef.current > 0 || draggingRef.current) return;
            setColumns((prev) => (quiet ? mergeBoard(prev, data.columns) : data.columns));
            setFailed('');
        } catch (error) {
            if (!quiet) setFailed(errorText(error, 'Доска не загрузилась'));
        } finally {
            if (!quiet) setLoading(false);
        }
    }, [api, departmentId, search]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => {
        const tick = () => { if (document.visibilityState !== 'hidden') load(true); };
        const timer = setInterval(tick, POLL_MS);
        document.addEventListener('visibilitychange', tick);
        return () => {
            clearInterval(timer);
            document.removeEventListener('visibilitychange', tick);
        };
    }, [load]);

    // Ссылка из Telegram, колокола или журнала: открыть названный запрос.
    useEffect(() => {
        if (!focusRequestId) return;
        setOpenId(Number(focusRequestId));
        onFocusHandled?.();
    }, [focusRequestId, onFocusHandled]);

    const move = async (item, stage) => {
        const label = (columns || []).find((column) => column.stage === stage)?.label || '';
        movingRef.current += 1;
        setColumns((prev) => moveOnBoard(prev, item.id, stage));
        try {
            const { data } = await api.setRequestStage(item.id, stage);
            setColumns((prev) => replaceOnBoard(prev, data.request));
            if (data.delivery_error) toast(`Не всё дошло до группы: ${data.delivery_error}`, 'error');
            else toast(`Запрос №${item.id} — «${label}»`, 'success');
        } catch (error) {
            toast(errorText(error, 'Запрос не перенесён'), 'error');
        } finally {
            movingRef.current -= 1;
            // Сверка с сервером: и после отказа (карточка вернётся на место), и после успеха.
            if (!movingRef.current) load(true);
        }
    };

    const onDragStart = (event, item) => {
        event.dataTransfer.setData('text/plain', String(item.id));
        event.dataTransfer.effectAllowed = 'move';
        draggingRef.current = true;
        setDragging(item);
    };
    const onDragEnd = () => {
        draggingRef.current = false;
        setDragging(null);
    };
    const onDrop = (event, stage) => {
        draggingRef.current = false;
        setDragging(null);
        const id = Number(event.dataTransfer.getData('text/plain'));
        const item = (columns || []).flatMap((column) => column.items).find((card) => card.id === id);
        if (!item || item.stage === stage) return;
        move(item, stage);
    };

    const showMore = async (column) => {
        const last = column.items[column.items.length - 1];
        if (!last || moreBusy) return;
        setMoreBusy(column.stage);
        try {
            const { data } = await api.requests({
                department_id: departmentId || undefined, stage: column.stage, q: search || undefined,
                before_at: last.last_message_at, before_id: last.id,
            });
            setColumns((prev) => appendToColumn(prev, column.stage, data.items));
        } catch (error) {
            toast(errorText(error), 'error');
        } finally {
            setMoreBusy('');
        }
    };

    // Окно запроса поменяло его (стадию, «непрочитано», переписку) — та же карточка на доске.
    const onThreadChanged = useCallback((request) => {
        if (!request) return;
        setColumns((prev) => {
            const current = (prev || []).flatMap((column) => column.items).find((item) => item.id === request.id);
            const moved = current && current.stage !== request.stage ? moveOnBoard(prev, request.id, request.stage) : prev;
            return replaceOnBoard(moved, request);
        });
    }, []);

    const toggleCollapsed = (stage) => setCollapsed((prev) => {
        const next = prev.includes(stage) ? prev.filter((item) => item !== stage) : [...prev, stage];
        try {
            window.localStorage.setItem(COLLAPSED_KEY, JSON.stringify(next));
        } catch {
            // Приватное окно: свёрнутые колонки просто не запомнятся.
        }
        return next;
    });

    const openItem = useMemo(
        () => (columns || []).flatMap((column) => column.items).find((item) => item.id === openId) || null,
        [columns, openId]);

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                <label className="relative">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)}
                           placeholder="Номер, телефон, ID, текст"
                           className="h-9 w-64 rounded-xl bg-white pl-8 pr-8 text-[13px] text-slate-900 ring-1 ring-slate-200/70 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70" />
                    {query && (
                        <button type="button" aria-label="Очистить поиск" onClick={() => setQuery('')}
                                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                            <X size={14} />
                        </button>
                    )}
                </label>
                <button type="button" onClick={() => load()} disabled={loading} aria-label="Обновить доску" title="Обновить"
                        className="ml-auto grid h-9 w-9 place-items-center rounded-xl bg-white text-slate-500 ring-1 ring-slate-200/70 transition hover:text-slate-700 disabled:opacity-60">
                    <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
                </button>
            </div>

            {failed && !columns && (
                <div className={iosCard}>
                    <EmptyState title={failed}>
                        <button type="button" onClick={() => load()} className={`${iosBtnSecondary} mt-4`}>Повторить</button>
                    </EmptyState>
                </div>
            )}
            {!failed && !columns && <Spinner text="Загружаем доску…" />}
            {columns && (
                /* Нижняя полоса прокрутки — тонкая до наведения (.board-scroll в styles.css), как у «Воронки найма». */
                <div className="board-scroll -mx-3 overflow-x-auto px-3 pb-1 sm:-mx-5 sm:px-5">
                    <div className="flex items-start gap-3">
                        {columns.map((column) => (
                            <Column key={column.stage} column={column} canMove={canMove} dragging={dragging}
                                    collapsed={collapsed.includes(column.stage)} onToggle={() => toggleCollapsed(column.stage)}
                                    onDrop={onDrop}
                                    more={(
                                        <button type="button" onClick={() => showMore(column)} disabled={Boolean(moreBusy)}
                                                className="mx-auto my-1 inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12px] font-medium text-slate-500 hover:bg-white hover:text-slate-700 disabled:opacity-60">
                                            {moreBusy === column.stage && <Loader2 size={12} className="animate-spin" />}
                                            Показать ещё · {column.total - column.items.length}
                                        </button>
                                    )}>
                                {column.items.map((item) => (
                                    <BoardCard key={item.id} item={item} userId={userId} showAuthor={canMove} draggable={canMove}
                                               onOpen={setOpenId} onDragStart={onDragStart} onDragEnd={onDragEnd} />
                                ))}
                            </Column>
                        ))}
                    </div>
                </div>
            )}

            <IosModal open={Boolean(openId)} onClose={() => setOpenId(null)} maxWidth="max-w-3xl"
                      title={openId ? `Запрос №${openId}` : ''}
                      subtitle={openItem?.destination_title ? `Telegram-группа: ${openItem.destination_title}` : ''}
                      bodyClassName="flex min-h-0 flex-1 flex-col sm:h-[min(78vh,760px)] sm:flex-none">
                {openId && (
                    <Thread key={openId} api={api} requestId={openId} variant="window" onChanged={onThreadChanged}
                            toast={toast} limits={limits} userId={userId} stages={stages} />
                )}
            </IosModal>
        </div>
    );
}
