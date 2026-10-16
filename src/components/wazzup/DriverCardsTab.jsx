import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { AlertCircle, Loader2, RefreshCw, Search } from 'lucide-react';
import { iosBtnGhost, iosCard, iosInput, IosBadge } from '../ui/ios';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import { AmoStatus } from './ChatDriverCard';
import { fmtCardTime } from './driverCards';

/* «Карточки» — только карточки водителей, без переписки: по одной (текущей)
 * на чат, свежие сверху. Строка открывает чат с этой карточкой. Чат, ушедший
 * по сроку хранения переписки (45 дней), не открывается, но карточка остаётся
 * (wazzup/driver_cards.py хранит имя и номер в самой строке). */

const PAGE = 50;
const daysAgo = (days) => {
    const date = new Date();
    date.setDate(date.getDate() - days);
    return isoDate(date);
};
export const cardsCount = (count) => {
    const tail = count % 100;
    const last = count % 10;
    const word = tail >= 11 && tail <= 14 ? 'карточек'
        : last === 1 ? 'карточка' : last >= 2 && last <= 4 ? 'карточки' : 'карточек';
    return `${count} ${word}`;
};
const phoneOf = (card) => {
    const phone = card.contactPhone || card.chatId;
    return /^\d+$/.test(String(phone || '')) ? `+${phone}` : phone;
};

/* Догруженная страница поверх уже показанных: пока листали, сохранили новые
   карточки, и сдвинутая страница повторила бы последние строки. Чат — одна
   строка: первой (свежей) остаётся та, что пришла раньше. */
export const appendCards = (previous, page) => {
    const seen = new Set((previous || []).map((card) => `${card.channelId}/${card.chatId}`));
    return [...(previous || []), ...(page || []).filter((card) => !seen.has(`${card.channelId}/${card.chatId}`))];
};

export default function DriverCardsTab({ apiBaseUrl, headers, onOpenChat, active = true }) {
    const [search, setSearch] = useState('');
    const [query, setQuery] = useState('');
    const [from, setFrom] = useState(daysAgo(29));
    const [to, setTo] = useState(isoDate(new Date()));
    // Период не трогали — «последние 30 дней» считаются заново при каждом открытии:
    // портал живёт днями, и вчерашнее «сегодня» прятало бы сегодняшние карточки.
    const periodTouched = useRef(false);
    const [items, setItems] = useState(null);
    const [total, setTotal] = useState(0);
    const [error, setError] = useState('');
    const [moreError, setMoreError] = useState('');
    const [loadingMore, setLoadingMore] = useState(false);
    const request = useRef({ id: 0, controller: null });
    const debounce = useRef(null);

    const load = ({ reset = true, q = query, range = { from, to } } = {}) => {
        request.current.controller?.abort();
        const controller = new AbortController();
        const id = request.current.id + 1;
        request.current = { id, controller };
        const offset = reset ? 0 : (items?.length || 0);
        setMoreError('');
        if (reset) { setItems(null); setError(''); } else setLoadingMore(true);
        axios.get(`${apiBaseUrl}/api/wazzup/pilot/driver-cards`, {
            headers: headers(), signal: controller.signal, timeout: 20000,
            params: { account: 'op', q: q || undefined, from: range.from || undefined, to: range.to || undefined,
                      limit: PAGE, offset },
        }).then(({ data }) => {
            if (id !== request.current.id) return;
            setTotal(Number(data.total) || 0);
            setItems((previous) => (reset ? (data.items || []) : appendCards(previous, data.items)));
        }).catch((failure) => {
            if (axios.isCancel?.(failure) || failure.name === 'CanceledError' || id !== request.current.id) return;
            // Догрузка не удалась — показанное остаётся, ошибка у кнопки.
            if (!reset) { setMoreError('Не удалось загрузить ещё — попробуйте снова'); return; }
            setItems((previous) => previous || []);
            // Портал выкатился раньше сервера: ручки ещё нет (HTML-страница 404).
            const missingRoute = failure.response?.status === 404 && typeof failure.response?.data?.error !== 'string';
            setError(missingRoute ? 'Карточки появятся после обновления сервера — откройте раздел позже'
                : failure.response?.data?.error || 'Не удалось загрузить карточки');
        }).finally(() => { if (id === request.current.id) setLoadingMore(false); });
    };

    // Вкладка скрывается, а не размонтируется: свежий список — при каждом открытии.
    useEffect(() => {
        if (!active) return;
        if (periodTouched.current) { load(); return; }
        const range = { from: daysAgo(29), to: isoDate(new Date()) };
        setFrom(range.from); setTo(range.to);
        load({ range });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [active, apiBaseUrl]);
    useEffect(() => () => { request.current.controller?.abort(); clearTimeout(debounce.current); }, []);

    const onSearch = (value) => {
        setSearch(value);
        clearTimeout(debounce.current);
        debounce.current = setTimeout(() => { setQuery(value.trim()); load({ q: value.trim() }); }, 350);
    };

    return (
        <div className="space-y-3" data-testid="wazzup-driver-cards">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <IosDateRangePicker from={from} to={to} max={isoDate(new Date())}
                        onChange={({ from: f, to: t }) => {
                            periodTouched.current = true;
                            setFrom(f); setTo(t); load({ range: { from: f, to: t } });
                        }} />
                    {items && <IosBadge tone="slate">{cardsCount(total)}</IosBadge>}
                </div>
                <div className="flex items-center gap-2">
                    <div className="relative">
                        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={search} onChange={(event) => onSearch(event.target.value)}
                            placeholder="ID, телефон, имя, парк…" aria-label="Поиск по карточкам"
                            className={`${iosInput} w-56 py-2 pl-8 text-[13px]`} />
                    </div>
                    <button type="button" onClick={() => load()} className={iosBtnGhost}>
                        <RefreshCw size={13} /> Обновить
                    </button>
                </div>
            </div>

            {items === null && !error && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-[13px] text-slate-400`}>
                    <Loader2 size={15} className="animate-spin" /> Загрузка…
                </div>
            )}
            {error && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-10 text-[13px] text-rose-500`}>
                    <AlertCircle size={15} /> {error}
                </div>
            )}
            {items && !error && (
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="max-h-[calc(100vh-260px)] overflow-auto">
                        <table className="w-full border-collapse text-[13px]">
                            <thead className="sticky top-0 z-10 bg-white/85 backdrop-blur-xl">
                                <tr className="border-b border-slate-200/70 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                    <th className="whitespace-nowrap px-3 py-2.5">Обновлена</th>
                                    <th className="whitespace-nowrap px-3 py-2.5">Водитель</th>
                                    <th className="whitespace-nowrap px-3 py-2.5">ID водителя</th>
                                    <th className="whitespace-nowrap px-3 py-2.5">Таксопарк</th>
                                    <th className="whitespace-nowrap px-3 py-2.5">Город</th>
                                    <th className="whitespace-nowrap px-3 py-2.5">Заполнил(а)</th>
                                    <th className="whitespace-nowrap px-3 py-2.5">amoCRM</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {items.length === 0 && (
                                    <tr><td colSpan={7} className="px-4 py-10 text-center text-[13px] text-slate-400">
                                        {query ? 'Ничего не нашлось' : 'За выбранный период карточек нет'}
                                    </td></tr>
                                )}
                                {items.map((card) => {
                                    const open = card.chatAvailable ? () => onOpenChat?.(card) : null;
                                    return (
                                        <tr key={card.id} onClick={open || undefined}
                                            title={open ? 'Открыть чат' : 'Переписка старше 45 дней — чат в портале уже не открыть'}
                                            className={`transition-colors ${open ? 'cursor-pointer hover:bg-slate-50/80' : ''}`}>
                                            <td className="whitespace-nowrap px-3 py-2 text-slate-500">{fmtCardTime(card.createdAt)}</td>
                                            <td className="max-w-[220px] px-3 py-2">
                                                {open ? (
                                                    <button type="button" onClick={(event) => { event.stopPropagation(); open(); }}
                                                        className="block max-w-full truncate text-left font-semibold text-slate-900 hover:underline">
                                                        {card.contactName || phoneOf(card)}
                                                    </button>
                                                ) : <div className="truncate font-semibold text-slate-900">{card.contactName || phoneOf(card)}</div>}
                                                {card.contactName && <div className="truncate text-[11.5px] text-slate-400">{phoneOf(card)}</div>}
                                            </td>
                                            <td className="whitespace-nowrap px-3 py-2 font-mono text-[12px] text-slate-700">{card.driverId || <span className="font-sans text-slate-400">—</span>}</td>
                                            <td className="whitespace-nowrap px-3 py-2 text-slate-700">{card.park?.name || <span className="text-slate-400">—</span>}</td>
                                            <td className="whitespace-nowrap px-3 py-2 text-slate-700">{card.city?.name || <span className="text-slate-400">—</span>}</td>
                                            <td className="whitespace-nowrap px-3 py-2 text-slate-600">{card.authorName}</td>
                                            <td className="whitespace-nowrap px-3 py-2" onClick={(event) => event.stopPropagation()}>
                                                <AmoStatus card={card} live={false} />
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                    {items.length < total && (
                        <button type="button" onClick={() => load({ reset: false })} disabled={loadingMore}
                            className="block w-full border-t border-slate-100 py-2.5 text-center text-[12px] font-semibold text-blue-600 hover:bg-slate-50 disabled:opacity-50">
                            {loadingMore ? 'Загрузка…' : `Показать ещё (${items.length} из ${total})`}
                        </button>
                    )}
                    {moreError && <p role="alert" className="pb-2.5 text-center text-[12px] text-rose-600">{moreError}</p>}
                </div>
            )}
        </div>
    );
}
