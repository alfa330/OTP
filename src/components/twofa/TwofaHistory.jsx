import React, { useEffect, useRef, useState } from 'react';
import {
    Check, Circle, Clock, FolderOpen, KeyRound, Pencil, ScrollText, Send, Trash2, TriangleAlert, Users, X,
} from 'lucide-react';
import { IosPager, iosCard } from '../ui/ios';
import { errorOf, journalGroupNote, journalKind, toneOfStatus, whenOf } from './twofaMeta';
import { EmptyState, GroupLabel, Monogram, Pill, Shimmer } from './twofaUi';

/*
 * История запросов и журнал «Кодов 2FA» — страницами с сервера (владелец 27.10.2026: «не
 * забудь использовать пагинацию»). Номер страницы держит экран, строки и «всего» отдаёт
 * сервер (/history, /journal). Перечитка раздела (refresh — тычок колокола, решение,
 * возврат на вкладку) перечитывает текущую страницу, не сбрасывая её на первую.
 *
 * Пока идёт ответ, прежняя страница стоит на месте приглушённой — список не прыгает; новая
 * въезжает с той стороны, куда листали, как экраны iOS.
 */

const viaLabel = (via) => (via === 'telegram' ? 'в Telegram' : via === 'portal' ? 'в портале' : '');

export const useServerPages = (api, path, params, refresh) => {
    const [page, setPage] = useState(1);
    const [direction, setDirection] = useState('next');
    const [state, setState] = useState({ data: null, loading: true, error: '' });
    const run = useRef(0);
    const paramsKey = JSON.stringify(params || {});

    useEffect(() => {
        const current = run.current + 1;
        run.current = current;
        setState((prev) => ({ ...prev, loading: true }));
        api.get(path, { ...JSON.parse(paramsKey), page })
            .then(({ data }) => {
                if (current !== run.current) return;
                // Страница опустела (строки ушли, пока её листали) — на последнюю непустую.
                const pages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 1)));
                if (page > pages) {
                    setPage(pages);
                    return;
                }
                setState({ data, loading: false, error: '' });
            })
            .catch((requestError) => {
                if (current === run.current) {
                    setState((prev) => ({ ...prev, loading: false, error: errorOf(requestError, 'Список не открылся') }));
                }
            });
    }, [api, path, paramsKey, page, refresh]);

    const go = (next) => {
        setDirection(next > page ? 'next' : 'prev');
        setPage(next);
    };
    const data = state.data;
    const size = data?.page_size || 1;
    const total = data?.total || 0;
    return {
        ...state,
        page,
        go,
        direction,
        pageCount: Math.max(1, Math.ceil(total / size)),
        from: total ? (page - 1) * size + 1 : 0,
        to: Math.min(page * size, total),
    };
};

/* Страница списка: приглушается, пока идёт ответ; новая въезжает по ходу листания. */
const PageBody = ({ pages, children }) => (
    <div key={pages.data ? `${pages.data.page}` : 'empty'}
         className={`transition-opacity duration-200 ${pages.loading ? 'opacity-60' : 'opacity-100'} ${
             pages.direction === 'next' ? 'motion-safe:animate-push-in' : 'motion-safe:animate-pop-in'
         }`}>
        {children}
    </div>
);

const RowsSkeleton = ({ rows = 3 }) => (
    <div className="space-y-1.5">
        {Array.from({ length: rows }, (_, index) => <Shimmer key={index} className="h-[52px] rounded-2xl" />)}
    </div>
);

const Pager = ({ pages, unit }) => (
    <div className="mt-2">
        <IosPager page={pages.page} pageCount={pages.pageCount} total={pages.data?.total || 0}
                  from={pages.from} to={pages.to} onPage={pages.go} unit={unit} />
    </div>
);

/* ── История запросов ────────────────────────────────────────────────── */

const HistoryRow = ({ request, serverTime, showRequester }) => {
    const decided = request.decided_at ? whenOf(request.decided_at, serverTime) : '';
    return (
        <li className={`${iosCard} flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3.5 py-2.5`}>
            <Monogram account={{ service: request.service, title: request.account_title }} size="sm" />
            <span className="min-w-0 grow basis-[200px]">
                {showRequester && <span className="block truncate text-[13.5px] font-semibold text-slate-900">{request.requester_name}</span>}
                <span className={`block truncate ${showRequester ? 'text-[12.5px] text-slate-500' : 'text-[13.5px] text-slate-800'}`}>
                    «{request.account_title}»
                </span>
            </span>
            <span className="flex shrink-0 items-center gap-2 text-[12.5px] text-slate-500">
                {request.delivery_error && request.status === 'approved'
                    ? <Pill tone="red">Код не дошёл</Pill>
                    : <Pill tone={toneOfStatus(request.status)}>{request.status_label}</Pill>}
                <span className="tabular-nums">{decided || whenOf(request.created_at, serverTime)}</span>
                {request.decided_via && request.status !== 'expired' && request.status !== 'cancelled' && (
                    <span className="hidden sm:inline">{viaLabel(request.decided_via)}</span>
                )}
            </span>
        </li>
    );
};

/* mine — свои запросы (вкладка кодов), иначе все решённые (глава). Пусто — блока нет. */
export const HistoryList = ({ api, title, mine = false, showRequester = false, serverTime, refresh }) => {
    const pages = useServerPages(api, '/history', mine ? { mine: 1 } : null, refresh);
    if (pages.data && !pages.data.total) return null;
    return (
        <section className="mt-7">
            <GroupLabel right={pages.data?.total ? <span className="text-[11px] tabular-nums text-slate-400">{pages.data.total}</span> : null}>
                {title}
            </GroupLabel>
            {!pages.data ? (
                pages.error ? <p className="px-1 text-[12.5px] text-rose-600">{pages.error}</p> : <RowsSkeleton />
            ) : (
                <>
                    <PageBody pages={pages}>
                        <ul className="space-y-1.5">
                            {pages.data.items.map((request) => (
                                <HistoryRow key={request.id} request={request} serverTime={serverTime} showRequester={showRequester} />
                            ))}
                        </ul>
                    </PageBody>
                    <Pager pages={pages} unit="запросы" />
                </>
            )}
        </section>
    );
};

/* ── Журнал ──────────────────────────────────────────────────────────── */

const KIND_ICONS = {
    request: Send, approve: Check, code: KeyRound, reject: X, clock: Clock, alert: TriangleAlert,
    account: KeyRound, edit: Pencil, remove: Trash2, folder: FolderOpen, access: Users, dot: Circle,
};

const KIND_TONES = {
    blue: 'bg-blue-50 text-blue-600',
    green: 'bg-emerald-50 text-emerald-600',
    red: 'bg-rose-50 text-rose-600',
    slate: 'bg-slate-100 text-slate-500',
};

const JournalRow = ({ item, serverTime }) => {
    const kind = journalKind(item.kind);
    const Icon = KIND_ICONS[kind.icon] || Circle;
    const groupNote = journalGroupNote(item);
    return (
        <li className={`${iosCard} flex items-start gap-3 px-3.5 py-2.5`}>
            <span className={`mt-px grid h-7 w-7 shrink-0 place-items-center rounded-full ${KIND_TONES[kind.tone] || KIND_TONES.slate}`}>
                <Icon size={14} strokeWidth={2.2} />
            </span>
            {/* break-words: почта без пробелов («marketing.team…@…») иначе выходила за карточку. */}
            <span className="min-w-0 grow basis-0 break-words text-[13.5px] leading-snug text-slate-800">
                {/* «Система», а не «Раздел»: разделы теперь — группы аккаунтов. */}
                <span className="font-semibold">{item.system ? 'Система' : (item.actor_name || '—')}</span>
                {' — '}{item.label}
                {item.account_title && <> · «{item.account_title}»</>}
                {groupNote && <span className="text-slate-500"> · {groupNote}</span>}
                {item.subject_name && item.subject_name !== item.actor_name && <> · {item.subject_name}</>}
                {item.details?.valid_until && <span className="text-slate-500"> · действовал до {item.details.valid_until}</span>}
                {item.details?.via && <span className="text-slate-500"> · {viaLabel(item.details.via)}</span>}
                {item.details?.error && <span className="text-rose-600"> · {item.details.error}</span>}
            </span>
            <span className="shrink-0 pt-0.5 text-[12px] tabular-nums text-slate-400">{whenOf(item.at, serverTime)}</span>
        </li>
    );
};

export const JournalList = ({ api, serverTime, refresh }) => {
    const pages = useServerPages(api, '/journal', null, refresh);
    if (!pages.data) {
        return pages.error
            ? <div className={iosCard}><EmptyState icon={ScrollText} title="Журнал не открылся">{pages.error}</EmptyState></div>
            : <RowsSkeleton rows={8} />;
    }
    if (!pages.data.total) {
        return (
            <div className={iosCard}>
                <EmptyState icon={ScrollText} title="Журнал пуст">
                    Каждый запрос, решение и выдача кода попадут сюда. Журнал не правится.
                </EmptyState>
            </div>
        );
    }
    return (
        <>
            <PageBody pages={pages}>
                <ul className="space-y-1.5">
                    {pages.data.items.map((item) => <JournalRow key={item.id} item={item} serverTime={serverTime} />)}
                </ul>
            </PageBody>
            <Pager pages={pages} unit="записи" />
        </>
    );
};
