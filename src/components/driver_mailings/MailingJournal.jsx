import React, { useEffect, useMemo, useState } from 'react';
import {
    AlertCircle, ChevronDown, CopyPlus, Inbox, Loader2, Undo2,
} from 'lucide-react';

import {
    IosBadge, IosModal, iosBtnGhost, iosBtnSecondary, iosCard, iosGroupLabel,
} from '../ui/ios';
import MailingPreview from './MailingPreview';
import { formatCount, stripMailingMarkup } from './mailingText';
import {
    describeAudience, formatCountdown, revokeSecondsLeft, statusMeta, targetStatusMeta,
} from './mailingMeta';

/*
 * Журнал рассылок.
 *
 * Своя таблица, а не пересказ кабинета: в кабинете автором всех наших рассылок
 * значится один служебный аккаунт — тот, чьи куки лежат в разделе «Провайдер
 * ЭДО». Кто на самом деле нажал кнопку, знает только портал, и в журнале это
 * первое, что нужно видеть. Прочтения при этом берутся из кабинета: их считает
 * он, и другого источника нет.
 *
 * Строки сгруппированы по дням с липким заголовком дня — как в журнале правок
 * вики. Глубина открывается кнопкой «Показать ещё», а не страницами: рассылки
 * читают сверху вниз, «свежие сначала», и номер страницы здесь ничего не значит.
 */

const dayKey = (value) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' });
};

const timeOf = (value) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

/* Полоса прочтений. Число рядом обязательно: полоса показывает соотношение,
   а решение принимают по абсолютным значениям. */
const ReadBar = ({ sent, read }) => {
    if (!sent) return <span className="text-[11.5px] text-slate-400">—</span>;
    const percent = Math.min(100, Math.round(((read || 0) / sent) * 100));
    return (
        <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-14 overflow-hidden rounded-full bg-slate-100">
                <span className="block h-full rounded-full bg-blue-500" style={{ width: `${percent}%` }} />
            </span>
            <span className="text-[11.5px] tabular-nums text-slate-500">
                {formatCount(read || 0)} / {formatCount(sent)}
            </span>
        </span>
    );
};

const EmptyBlock = ({ children, hint }) => (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
        <Inbox size={22} className="text-slate-300" />
        <div className="text-[13px] text-slate-500">{children}</div>
        {hint && <div className="max-w-[300px] text-[11.5px] leading-snug text-slate-500">{hint}</div>}
    </div>
);

/* Обратный отсчёт окна отзыва. Тикает раз в секунду и только пока окно открыто:
   таймер, работающий на закрытой карточке, — это перерисовка списка впустую. */
const useCountdown = (active) => {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!active) return undefined;
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [active]);
    return now;
};

const MailingCard = ({ item, revokeWindow, onRevoke, onRepeat, revoking, refs }) => {
    const now = useCountdown(true);
    const revokable = (item.targets || []).filter((target) => (
        target.status === 'sent' && target.fleet_mailing_id
        && revokeSecondsLeft(target.sent_at, revokeWindow, now) > 0
    ));
    const secondsLeft = revokable.length
        ? Math.max(...revokable.map((target) => revokeSecondsLeft(target.sent_at, revokeWindow, now)))
        : 0;
    const audience = describeAudience(item.filters, refs);
    const unresolved = (item.targets || []).some((target) => target.status === 'sent' && !target.fleet_mailing_id);

    return (
        <div className="space-y-4">
            <MailingPreview title={item.title} message={item.message} />

            <div>
                <div className={iosGroupLabel}>Кому ушло</div>
                <ul className="mt-1.5 space-y-1 text-[12.5px] text-slate-600">
                    {audience.map((line) => <li key={line}>{line}</li>)}
                </ul>
            </div>

            <div>
                <div className={iosGroupLabel}>Диспетчерские</div>
                <div className={`${iosCard} mt-1.5 overflow-hidden`}>
                    <table className="w-full text-left">
                        <thead>
                            <tr className="border-b border-slate-100">
                                <th className="px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-slate-500">Диспетчерская</th>
                                <th className="px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-slate-500">Состояние</th>
                                <th className="px-3 py-2 text-right text-[11px] font-medium uppercase tracking-wide text-slate-500">Прочли</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-50">
                            {(item.targets || []).map((target) => (
                                <tr key={target.park_id}>
                                    <td className="px-3 py-2 text-[12.5px] text-slate-700">
                                        {target.park_name}
                                        {target.park_city && <span className="text-slate-400"> · {target.park_city}</span>}
                                    </td>
                                    <td className="px-3 py-2">
                                        <IosBadge tone={targetStatusMeta(target.status).tone}>
                                            {targetStatusMeta(target.status).label}
                                        </IosBadge>
                                        {target.error && (
                                            <div className="mt-1 text-[11.5px] text-rose-600">{target.error}</div>
                                        )}
                                    </td>
                                    <td className="px-3 py-2 text-right">
                                        <ReadBar sent={target.sent_to_number} read={target.read_by_number} />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {unresolved && (
                    <div className="mt-1.5 flex items-start gap-1.5 px-1 text-[11.5px] text-amber-700">
                        <AlertCircle size={13} className="mt-px shrink-0" />
                        <span>
                            Рассылка ушла, но связать её с записью в кабинете не удалось — прочтения и отзыв
                            по ней недоступны. Посмотреть можно в самом кабинете диспетчерской.
                        </span>
                    </div>
                )}
            </div>

            <div className="flex flex-wrap items-center gap-2">
                <button type="button" className={iosBtnSecondary} onClick={() => onRepeat(item)}>
                    <CopyPlus size={14} /> Повторить
                </button>
                {revokable.length > 0 && (
                    <button
                        type="button"
                        disabled={revoking}
                        onClick={() => onRevoke(item)}
                        className="inline-flex items-center justify-center gap-1.5 rounded-xl bg-rose-600 px-4 py-2.5 text-[13.5px] font-semibold text-white shadow-sm transition-all hover:bg-rose-700 active:scale-[0.98] disabled:opacity-50"
                    >
                        {revoking ? <Loader2 size={14} className="animate-spin" /> : <Undo2 size={14} />}
                        Отозвать · <span className="tabular-nums">{formatCountdown(secondsLeft)}</span>
                    </button>
                )}
            </div>
            {revokable.length > 0 && (
                <div className="text-[11.5px] text-slate-500">
                    Кабинет разрешает отозвать рассылку в течение пяти минут после отправки. Те, кто уже
                    открыл уведомление, его видели.
                </div>
            )}
        </div>
    );
};

export default function MailingJournal({
    items, total, loading, loadingMore, error, onMore, revokeWindow, onRevoke, onRepeat, revoking, refs,
}) {
    const [openId, setOpenId] = useState(null);
    const open = useMemo(() => items.find((item) => item.id === openId) || null, [items, openId]);

    const days = useMemo(() => {
        const map = new Map();
        items.forEach((item) => {
            const key = dayKey(item.created_at);
            if (!map.has(key)) map.set(key, []);
            map.get(key).push(item);
        });
        return Array.from(map, ([day, rows]) => ({ day, rows }));
    }, [items]);

    if (loading) {
        return (
            <div className={`${iosCard} flex items-center justify-center gap-2 py-16 text-[13px] text-slate-500`}>
                <Loader2 size={15} className="animate-spin" /> Загружаем журнал…
            </div>
        );
    }

    if (error) {
        return (
            <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                <AlertCircle size={20} className="text-rose-400" />
                <div className="text-[13px] text-rose-600">{error}</div>
            </div>
        );
    }

    if (!items.length) {
        return (
            <div className={iosCard}>
                <EmptyBlock hint="Как только отправите первую — она появится здесь вместе со статистикой прочтений.">
                    Рассылок из портала ещё не было
                </EmptyBlock>
            </div>
        );
    }

    return (
        <>
            {/* Без overflow-hidden: любой предок с overflow != visible становится
                для sticky скролл-контейнером, и заголовок дня переставал липнуть.
                Скругление держим на первом и последнем ребёнке. */}
            <div className={`${iosCard} [&>div:first-child>div]:rounded-t-2xl`}>
                {days.map(({ day, rows }) => (
                    <div key={day}>
                        <div className="sticky top-0 z-10 border-b border-slate-100 bg-white/90 px-4 py-1.5 backdrop-blur">
                            <span className={iosGroupLabel}>{day}</span>
                        </div>
                        <ul className="divide-y divide-slate-50">
                            {rows.map((item) => {
                                const meta = statusMeta(item.status);
                                return (
                                    <li key={item.id}>
                                        <button
                                            type="button"
                                            onClick={() => setOpenId(item.id)}
                                            className="flex w-full items-start gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
                                        >
                                            <span className="w-[42px] shrink-0 pt-0.5 text-[11.5px] tabular-nums text-slate-400">
                                                {timeOf(item.created_at)}
                                            </span>
                                            <span className="min-w-0 flex-1">
                                                <span className="block truncate text-[13.5px] font-medium text-slate-900">
                                                    {item.title}
                                                </span>
                                                <span className="mt-0.5 block truncate text-[12px] text-slate-500">
                                                    {stripMailingMarkup(item.message)}
                                                </span>
                                                <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-slate-500">
                                                    <span>{item.created_by_name || 'Неизвестно кто'}</span>
                                                    <span className="text-slate-300">·</span>
                                                    <span>{(item.targets || []).map((target) => target.park_name).join(', ')}</span>
                                                </span>
                                            </span>
                                            <span className="flex shrink-0 flex-col items-end gap-1.5">
                                                {/* Обычная успешная отправка не красится: цвет здесь означает
                                                    «посмотри», а не «состояние». */}
                                                {item.status !== 'sent' && <IosBadge tone={meta.tone}>{meta.label}</IosBadge>}
                                                <ReadBar sent={item.sent_total} read={item.read_total} />
                                            </span>
                                        </button>
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                ))}
            </div>

            <div className="mt-2.5 flex items-center justify-between gap-2 px-1">
                <span className="text-[11.5px] tabular-nums text-slate-500">
                    Показано {formatCount(items.length)} из {formatCount(total)}
                </span>
                {items.length < total && (
                    <button type="button" className={iosBtnSecondary} onClick={onMore} disabled={loadingMore}>
                        {loadingMore ? <Loader2 size={14} className="animate-spin" /> : <ChevronDown size={14} />}
                        Показать ещё ({formatCount(total - items.length)})
                    </button>
                )}
            </div>

            <IosModal
                open={Boolean(open)}
                onClose={() => setOpenId(null)}
                title={open ? open.title : ''}
                subtitle={open ? `${open.created_by_name || 'Неизвестно кто'} · ${timeOf(open.created_at)}` : ''}
                maxWidth="max-w-2xl"
                footer={<button type="button" className={iosBtnGhost} onClick={() => setOpenId(null)}>Закрыть</button>}
            >
                {open && (
                    <MailingCard
                        item={open}
                        refs={refs}
                        revokeWindow={revokeWindow}
                        revoking={revoking}
                        onRevoke={onRevoke}
                        onRepeat={(item) => { setOpenId(null); onRepeat(item); }}
                    />
                )}
            </IosModal>
        </>
    );
}
