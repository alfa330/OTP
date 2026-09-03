import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import ChatThread from './ChatThread';
import { iosCard } from '../ui/ios';

/* «Проверки низких оценок» в разделе «Мои оценки» чат-менеджера.
 *
 * Оценку клиента ниже 4 проверяет ОКК (см. «Учёт часов → Низкие оценки»):
 * необоснованную снимают, и она перестаёт влиять на показатель оператора.
 *
 * На странице живёт только компактная сводка. Детали — переписка и вердикты —
 * закрыты тем же QR-ключом, что телефоны и записи разговоров, и открываются
 * в полноэкранном просмотре: та же раскладка, что у проверяющих в «Учёте
 * часов», но строго свои чаты и без возможности вынести вердикт.
 *
 * Цвета согласованы с проверяющей стороной и намеренно «перевёрнуты»:
 * обоснованно (оценка остаётся) — красный, необоснованно (снята) — зелёный.
 *
 * Количества здесь НЕ показываем (задача #272, Сабыр Азана): ни сводки
 * «всего / снято / обоснованно / на проверке», ни числа оценок в шапке журнала.
 * Чат-менеджер видит сами обращения и решения по ним, а сколько их — нет. */

const VERDICTS = {
    invalid: { short: 'Необоснованно', label: 'Необоснованно · снята', pill: 'bg-emerald-50 text-emerald-700 ring-emerald-200' },
    valid: { short: 'Обоснованно', label: 'Обоснованно', pill: 'bg-rose-50 text-rose-700 ring-rose-200' },
    pending: { short: 'На проверке', label: 'На проверке', pill: 'bg-slate-100 text-slate-500 ring-slate-200' },
};

const verdictOf = (row) => VERDICTS[row?.final_status] || VERDICTS.pending;

const formatDateTime = (value) => {
    const text = String(value || '').trim();
    if (!text) return '—';
    const [datePart, timePart = ''] = text.replace('T', ' ').split(' ');
    const [, mm, dd] = datePart.split('-');
    if (!mm || !dd) return text.slice(0, 16);
    return `${dd}.${mm}${timePart ? ` ${timePart.slice(0, 5)}` : ''}`;
};

// ISO-день → 01.07.2026, как в остальных датах сайта.
const formatDay = (value) => {
    const [yyyy, mm, dd] = String(value || '').slice(0, 10).split('-');
    return dd ? `${dd}.${mm}.${yyyy}` : '';
};

export default function MyLowRatings({ apiBaseUrl, withAccessTokenHeader, userId, month, granted }) {
    const [data, setData] = useState(null);
    const [open, setOpen] = useState(false);
    const [selectedId, setSelectedId] = useState('');
    const [chat, setChat] = useState({ id: '', snapshot: null, loading: false, error: '' });
    const [hideService, setHideService] = useState(false);
    const chatCacheRef = useRef({});
    const chatRequestRef = useRef('');

    useEffect(() => {
        if (!userId || !month) return undefined;
        let cancelled = false;
        const headers = withAccessTokenHeader ? withAccessTokenHeader({ 'X-User-Id': userId }) : {};
        axios.get(`${apiBaseUrl}/api/my/low_rating_reviews`, { params: { month }, headers })
            .then((response) => {
                if (cancelled) return;
                setData(response.data || null);
                setSelectedId('');
            })
            .catch((e) => {
                if (cancelled) return;
                // Блок дополнительный: по ошибке молча исчезаем, а не пугаем
                // плашкой всех операторов — у большинства блока и так нет.
                console.error('my low ratings:', e?.response?.data?.error || e?.message);
                setData(null);
            });
        return () => { cancelled = true; };
        // granted в зависимостях: после скана QR строки нужно перезапросить.
    }, [apiBaseUrl, withAccessTokenHeader, userId, month, granted]);

    // Смена месяца закрывает просмотр: строки под ним уже другие.
    useEffect(() => { setOpen(false); }, [month]);

    const fetchChat = useCallback(async (reviewId, { force = false } = {}) => {
        const key = String(reviewId || '');
        if (!key) return;
        chatRequestRef.current = key;
        const cached = chatCacheRef.current[key];
        if (cached && !force) {
            setChat({ id: key, snapshot: cached.snapshot, loading: false, error: cached.error });
            return;
        }
        setChat({ id: key, snapshot: null, loading: true, error: '' });
        try {
            const headers = withAccessTokenHeader ? withAccessTokenHeader({ 'X-User-Id': userId }) : {};
            const response = await axios.get(
                `${apiBaseUrl}/api/chat_manager/low_rating_reviews/${encodeURIComponent(key)}/chat`,
                { headers, withCredentials: true }
            );
            const snapshot = response.data?.snapshot || null;
            const error = snapshot ? '' : 'Переписка не найдена';
            chatCacheRef.current[key] = { snapshot, error };
            // Пока грузили — могли переключиться на другую строку.
            if (chatRequestRef.current !== key) return;
            setChat({ id: key, snapshot, loading: false, error });
        } catch (e) {
            const error = e?.response?.data?.error || 'Не удалось загрузить переписку';
            // Ответ сервера («переписки нет») запоминаем: у чата без снапшота
            // каждый повторный клик — это 2-3 живых запроса в Chat2Desk, а
            // месячная квота там почти выедена синком. Обрыв сети не кэшируем:
            // это не приговор чату, при следующем открытии пробуем снова.
            if (e?.response) chatCacheRef.current[key] = { snapshot: null, error };
            if (chatRequestRef.current !== key) return;
            setChat({ id: key, snapshot: null, loading: false, error });
        }
    }, [apiBaseUrl, withAccessTokenHeader, userId]);

    useEffect(() => {
        if (!open || !selectedId) return;
        fetchChat(selectedId);
    }, [open, selectedId, fetchChat]);

    // Полноэкранный просмотр: Esc закрывает, страница под ним не прокручивается.
    useEffect(() => {
        if (!open || typeof document === 'undefined') return undefined;
        const previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        const onKeyDown = (e) => { if (e.key === 'Escape') setOpen(false); };
        window.addEventListener('keydown', onKeyDown);
        return () => {
            document.body.style.overflow = previousOverflow;
            window.removeEventListener('keydown', onKeyDown);
        };
    }, [open]);

    // Оператору без чат-менеджерской модели блок не показываем совсем: низких
    // оценок клиента у него не бывает, пустая карточка была бы шумом.
    if (!data || !data.is_chat_manager) return null;

    const rows = Array.isArray(data.rows) ? data.rows : [];
    const summary = data.summary || {};
    const total = Number(summary.total || 0);
    const isGranted = Boolean(data.sensitive_access?.granted);
    const selected = rows.find((row) => String(row.id) === String(selectedId)) || null;
    const chatReady = Boolean(selected) && String(chat.id) === String(selected.id);

    return (
        <>
            <section className={`${iosCard} mb-6 p-4`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                        <h3 className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                            <FaIcon className="fas fa-star-half-alt text-amber-500" />
                            Низкие оценки клиентов
                        </h3>
                        <p className="mt-1 text-[12px] leading-5 text-slate-500">
                            Оценку ниже 4 проверяет ОКК. Признанную необоснованной снимают — она не влияет на ваш показатель.
                        </p>
                    </div>
                    {total > 0 && isGranted && (
                        <button
                            type="button"
                            onClick={() => {
                                setOpen(true);
                                // На телефоне сначала список: чат занимает весь экран,
                                // и автовыбор увёл бы туда мимо списка.
                                const wide = typeof window === 'undefined' || window.innerWidth >= 768;
                                if (wide && !selectedId && rows.length) setSelectedId(String(rows[0].id));
                            }}
                            className="inline-flex h-9 shrink-0 items-center gap-2 rounded-full bg-slate-900 px-4 text-[12.5px] font-semibold text-white shadow-sm transition hover:bg-slate-800 active:scale-[0.98]"
                        >
                            <FaIcon className="fas fa-comments" />
                            Смотреть проверки
                        </button>
                    )}
                </div>

                {total === 0 && (
                    <div className="mt-3 rounded-2xl bg-slate-50 px-3 py-2.5 text-[13px] text-slate-500 ring-1 ring-slate-200/70">
                        За этот месяц низких оценок нет.
                    </div>
                )}

                {total > 0 && !isGranted && (
                    <div className="mt-2.5 flex items-start gap-2 px-1 text-[12px] leading-5 text-slate-500">
                        <FaIcon className="fas fa-qrcode mt-0.5 shrink-0 text-slate-400" />
                        <span>Чтобы посмотреть переписки и решения по каждой оценке, покажите QR-код супервайзеру или администратору для сканирования.</span>
                    </div>
                )}
            </section>

            {open && (
                <div className="fixed inset-0 z-[135] flex bg-slate-100">
                    <div className="flex h-full w-full min-w-0 flex-col overflow-hidden">
                        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3 sm:px-5">
                            <div className="flex min-w-0 items-center gap-3">
                                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-slate-900 text-white shadow-sm">
                                    <FaIcon className="fas fa-star-half-alt" aria-hidden="true" />
                                </span>
                                <div className="min-w-0">
                                    <h3 className="text-base font-semibold text-slate-900">Мои низкие оценки</h3>
                                    <p className="text-xs leading-5 text-slate-500">
                                        Переписка чата и решение проверяющих по каждой оценке.
                                    </p>
                                </div>
                            </div>
                            <button
                                type="button"
                                onClick={() => setOpen(false)}
                                className="flex h-9 w-9 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700"
                                aria-label="Закрыть"
                            >
                                <FaIcon className="fas fa-xmark" aria-hidden="true" />
                            </button>
                        </div>

                        <div className="flex min-h-0 flex-1 overflow-hidden">
                            <aside className={`w-full shrink-0 flex-col overflow-hidden border-r border-slate-200 bg-white md:flex md:w-[290px] xl:w-[330px] ${selected ? 'hidden' : 'flex'}`}>
                                <div className="border-b border-slate-200 px-4 py-2.5">
                                    <div className="text-sm font-semibold text-slate-900">Журнал</div>
                                    <div className="truncate text-[11px] text-slate-500">
                                        <span>{`${formatDay(data.start)} — ${formatDay(data.end)}`}</span>
                                    </div>
                                </div>
                                <div className="min-h-0 flex-1 overflow-y-scroll overscroll-contain p-2.5 ios-modal-scroll">
                                    <div className="space-y-2">
                                        {rows.map((row) => {
                                            const isSelected = String(row.id) === String(selectedId);
                                            const verdict = verdictOf(row);
                                            return (
                                                <button
                                                    key={row.id}
                                                    type="button"
                                                    onClick={() => setSelectedId(String(row.id))}
                                                    title="Открыть переписку этого чата"
                                                    className={`w-full rounded-2xl border p-3 text-left transition ${
                                                        isSelected
                                                            ? 'border-slate-900 bg-slate-50 shadow-sm ring-2 ring-slate-900/10'
                                                            : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'
                                                    }`}
                                                >
                                                    <div className="flex items-start justify-between gap-2">
                                                        <div className="min-w-0">
                                                            <div className="truncate text-[13px] font-semibold text-slate-900">
                                                                {row.taxi_park || row.channel_name || '—'}
                                                            </div>
                                                            <div className="mt-0.5 text-[11px] text-slate-500">
                                                                {formatDateTime(row.rated_at || row.day)}
                                                            </div>
                                                        </div>
                                                        <span className="inline-flex h-8 min-w-8 items-center justify-center rounded-full bg-rose-50 px-2 text-sm font-bold text-rose-700 ring-1 ring-rose-200">
                                                            {Number(row.score || 0).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}
                                                        </span>
                                                    </div>
                                                    <div className="mt-2">
                                                        <span className={`inline-flex rounded-full px-2 py-0.5 text-[10.5px] font-bold ring-1 ${verdict.pill}`}>
                                                            {verdict.label}
                                                        </span>
                                                    </div>
                                                </button>
                                            );
                                        })}
                                    </div>
                                </div>
                            </aside>

                            <section className={`min-w-0 flex-1 flex-col overflow-hidden md:flex ${selected ? 'flex' : 'hidden'}`}>
                                {!selected ? (
                                    <div className="flex flex-1 flex-col items-center justify-center gap-3 text-slate-400">
                                        <FaIcon className="fas fa-comments text-3xl" aria-hidden="true" />
                                        <div className="text-sm">Выберите оценку слева — откроется переписка.</div>
                                    </div>
                                ) : (
                                    <>
                                        {/* key по id оценки: переключение чата пересоздаёт шапку целиком,
                                            поэтому подмена текстовых узлов извне (переводчик, расширения)
                                            не может оставить в ней дату прошлого чата. */}
                                        <div key={`mlr-head-${selected.id}`} className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2.5">
                                            <button
                                                type="button"
                                                onClick={() => setSelectedId('')}
                                                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 md:hidden"
                                                aria-label="Назад к списку"
                                            >
                                                <FaIcon className="fas fa-chevron-left" aria-hidden="true" />
                                            </button>
                                            <span className="inline-flex h-9 min-w-9 items-center justify-center rounded-2xl bg-rose-50 px-2 text-base font-bold text-rose-700 ring-1 ring-rose-200">
                                                {Number(selected.score || 0).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}
                                            </span>
                                            <div className="min-w-0">
                                                <div className="truncate text-[11px] text-slate-500">
                                                    <span>{[
                                                        formatDateTime(selected.rated_at || selected.day),
                                                        selected.taxi_park || selected.channel_name || '—',
                                                        selected.phone_number || '—',
                                                    ].join(' · ')}</span>
                                                </div>
                                            </div>
                                            <span className={`ml-auto inline-flex rounded-full px-3 py-1 text-[11px] font-bold ring-1 ${verdictOf(selected).pill}`}>
                                                {verdictOf(selected).label}
                                            </span>
                                            <button
                                                type="button"
                                                onClick={() => setHideService((prev) => !prev)}
                                                className={`inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[11px] font-semibold transition ${
                                                    hideService ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                                }`}
                                                title="Скрыть системные сообщения и автоответы"
                                            >
                                                <FaIcon className={`fas ${hideService ? 'fa-eye' : 'fa-eye-slash'}`} aria-hidden="true" />
                                                {hideService ? 'Автоответы' : 'Без автоответов'}
                                            </button>
                                        </div>

                                        {selected.client_comment && (
                                            <div key={`mlr-comment-${selected.id}`} className="flex items-start gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-[12.5px] leading-5 text-amber-900">
                                                <FaIcon className="fas fa-comment-dots mt-0.5 shrink-0" aria-hidden="true" />
                                                <div className="min-w-0 break-words">
                                                    <span className="font-semibold">Комментарий клиента: </span>
                                                    <span>{selected.client_comment}</span>
                                                </div>
                                            </div>
                                        )}

                                        <div className="flex min-h-0 flex-1 justify-center overflow-hidden bg-[#f2f2f7]">
                                            <div className="flex min-h-0 w-full max-w-4xl flex-col overflow-hidden">
                                                <ChatThread
                                                    snapshot={chatReady ? chat.snapshot : null}
                                                    loading={!chatReady || chat.loading}
                                                    error={chatReady ? chat.error : ''}
                                                    hideService={hideService}
                                                    emptyText="В этом чате нет сообщений"
                                                />
                                            </div>
                                        </div>

                                        <div key={`mlr-verdict-${selected.id}`} className="max-h-[38vh] shrink-0 overflow-y-auto border-t border-slate-200 bg-white px-4 py-3 shadow-[0_-10px_24px_-20px_rgba(15,23,42,0.6)] ios-modal-scroll">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                                                    Решение по оценке
                                                </span>
                                                <span className={`inline-flex rounded-full px-2.5 py-0.5 text-[11px] font-semibold ring-1 ${verdictOf(selected).pill}`}>
                                                    {verdictOf(selected).label}
                                                </span>
                                            </div>
                                            {selected.decisions?.length ? (
                                                <div className="mt-2 grid gap-2 lg:grid-cols-2">
                                                    {selected.decisions.map((decision, index) => (
                                                        <div key={`${selected.id}-decision-${index}`} className="rounded-xl bg-slate-50 px-3 py-2 ring-1 ring-slate-200">
                                                            <div className="flex flex-wrap items-center justify-between gap-2">
                                                                <div className="min-w-0">
                                                                    <div className="truncate text-[13px] font-semibold text-slate-900">{decision.reviewer_name}</div>
                                                                    <div className="text-[10.5px] text-slate-400">{formatDateTime(decision.updated_at)}</div>
                                                                </div>
                                                                <span className={`inline-flex rounded-full px-2 py-0.5 text-[10.5px] font-semibold ring-1 ${(VERDICTS[decision.status] || VERDICTS.pending).pill}`}>
                                                                    {(VERDICTS[decision.status] || VERDICTS.pending).short}
                                                                </span>
                                                            </div>
                                                            <div className="mt-1 whitespace-pre-wrap text-[12.5px] leading-5 text-slate-700">
                                                                {decision.comment || 'Без комментария'}
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <div className="mt-2 rounded-xl bg-slate-50 px-3 py-2 text-[12.5px] text-slate-500 ring-1 ring-slate-200">
                                                    Оценка ещё проверяется — решение появится здесь.
                                                </div>
                                            )}
                                            {selected.final_comment && (
                                                <div className="mt-2 rounded-xl bg-slate-50 px-3 py-2 text-[12.5px] leading-5 text-slate-700 ring-1 ring-slate-200">
                                                    <span className="font-semibold">Решение руководителя: </span>
                                                    <span className="whitespace-pre-wrap">{selected.final_comment}</span>
                                                </div>
                                            )}
                                        </div>
                                    </>
                                )}
                            </section>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}
