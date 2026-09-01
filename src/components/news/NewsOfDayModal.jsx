import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Bell, Check, Loader2, Lock, X } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import { initialsOf, publishedLabel, roleTitle, subscribeNewsPoke } from './newsShared';
import './news-modal.css';

/* Окно «Новость дня».
 *
 * Живёт в корне портала, а не внутри раздела «Вики», хотя пишут новость там.
 * Причина из постановки: увидеть новость обязан и тот, кому вики не выдана и
 * чья сессия не подтверждена QR. Поэтому и данные берутся из своего
 * /api/news/*, мимо блюпринта вики с его двумя дверями.
 *
 * КАК ОКНО УЗНАЁТ О НОВОСТИ. Двумя способами и без единого опроса по таймеру:
 *   вход в портал   — первый запрос /pending при появлении пользователя;
 *   открытая сессия — «тычок» SSE-канала колокола. Своего канала окно НЕ
 *                     открывает: каждый поток занимает нить waitress, лимит на
 *                     портал — BELL_STREAM_LIMIT (50), и второй канал на
 *                     вкладку срезал бы ёмкость вдвое.
 * Плюс возврат во вкладку — тем же обработчиком, что у колокола.
 *
 * Тычок приходит ПОДПИСКОЙ МОДУЛЯ (subscribeNewsPoke), а не пропом. Пропом он
 * не доезжал бы: окно смонтировано внутри sidebarTree = useMemo(...) в App.jsx,
 * и значение из состояния App замерзало бы на первом рендере — забыть его в
 * списке зависимостей того useMemo (там под сорок значений) проще, чем
 * вспомнить, а отказ был бы молчаливым.
 *
 * ЗАДЕРЖКУ КНОПКИ СЧИТАЕТ СЕРВЕР. Здесь она только отображается: сервер
 * присылает remaining_seconds и он же отвергает раннее подтверждение (409).
 * Клиентский таймер без серверной проверки — это честное слово браузера, а не
 * гарантия, что новость прочитали.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

export default function NewsOfDayModal({ apiBaseUrl, user, getHeaders }) {
    const [queue, setQueue] = useState([]);
    const [remaining, setRemaining] = useState(0);
    const [sending, setSending] = useState(false);
    const [error, setError] = useState('');
    /* Уже показанное в этой вкладке. Нужно, чтобы перезапрос (тычок канала,
       возврат во вкладку) не сбрасывал отсчёт у открытой карточки: сервер
       считает остаток от ПЕРВОГО показа, а человек в этот момент читает. */
    const shownRef = useRef(new Set());

    const headers = useMemo(() => (getHeaders ? getHeaders() : {}), [getHeaders]);
    const current = queue[0] || null;
    /* Когда ходили последний раз. Тычок канала колокола широковещателен и
       приходит на ЛЮБОЕ его событие — чужую задачу, опрос, ивент, — а не только
       на публикацию новости; плюс возврат во вкладку поднимает сразу два
       события (focus и visibilitychange). Без гарда один переход между
       вкладками стоил бы двух-трёх запросов подряд. */
    const lastLoadRef = useRef(0);

    const load = useCallback((force = false) => {
        if (!user?.id) return;
        const now = performance.now();
        if (!force && now - lastLoadRef.current < 4000) return;
        lastLoadRef.current = now;
        axios.get(`${apiBaseUrl}/api/news/pending`, { headers })
            .then((r) => {
                const items = Array.isArray(r.data?.items) ? r.data.items : [];
                setQueue((prev) => {
                    /* Сливаем, а не заменяем: карточка, которую человек читает
                       прямо сейчас, обязана остаться на месте со своим
                       отсчётом. Заменой её вытеснила бы более срочная новость
                       ровно в тот момент, когда кнопка вот-вот загорится. */
                    const head = prev[0];
                    const rest = items.filter((item) => item.id !== head?.id);
                    return head ? [head, ...rest] : rest;
                });
            })
            .catch(() => { /* молчим: окно — не повод показывать ошибку сети */ });
    }, [apiBaseUrl, headers, user?.id]);

    // Вход в портал — без гарда: первый запрос обязан уйти.
    useEffect(() => { load(true); }, [load]);
    // Тычок канала колокола: новость опубликовали, пока вкладка открыта.
    useEffect(() => subscribeNewsPoke(() => load()), [load]);
    // Возврат во вкладку — тот же приём, что у колокола: канал мог лежать.
    useEffect(() => {
        if (!user?.id) return undefined;
        const onWake = () => { if (document.visibilityState === 'visible') load(); };
        window.addEventListener('focus', onWake);
        document.addEventListener('visibilitychange', onWake);
        return () => {
            window.removeEventListener('focus', onWake);
            document.removeEventListener('visibilitychange', onWake);
        };
    }, [user?.id, load]);

    /* Отсчёт начинается один раз на новость: сервер прислал остаток от первого
       показа, и повторные ответы /pending его не двигают. */
    useEffect(() => {
        if (!current) { setRemaining(0); return; }
        if (shownRef.current.has(current.id)) return;
        shownRef.current.add(current.id);
        setRemaining(Math.max(0, Number(current.remaining_seconds) || 0));
        setError('');
    }, [current]);

    useEffect(() => {
        if (remaining <= 0) return undefined;
        const timer = setTimeout(() => setRemaining((value) => Math.max(0, value - 1)), 1000);
        return () => clearTimeout(timer);
    }, [remaining]);

    /* Пока окно открыто, портал за ним не прокручивается: обязательное
       объявление не должно уезжать вверх вместе со страницей. */
    useEffect(() => {
        if (!current) return undefined;
        const previous = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => { document.body.style.overflow = previous; };
    }, [current]);

    const dropCurrent = useCallback(() => {
        setQueue((prev) => prev.slice(1));
        setError('');
    }, []);

    const confirm = useCallback(() => {
        if (!current || sending || remaining > 0) return;
        setSending(true);
        axios.post(`${apiBaseUrl}/api/news/${current.id}/read`, {}, { headers })
            .then(() => {
                dropCurrent();
                /* Сервер отмечает «показали» только ТОЙ новости, что человек
                   реально видит, — иначе в журнале «открыл» стояло бы у всей
                   очереди разом, а отсчёт кнопки у второй новости шёл бы, пока
                   читают первую. Значит следующей отметки ещё нет: просим
                   свежую выдачу, она её и поставит. */
                load(true);
            })
            .catch((e) => {
                // 409 — сервер считает, что читали слишком быстро. Не спорим:
                // берём его остаток и досчитываем. Расхождение бывает от
                // рассинхрона часов, и правым здесь всегда сервер.
                const left = Number(e?.response?.data?.remaining_seconds);
                if (Number.isFinite(left) && left > 0) {
                    setRemaining(left);
                    setError('');
                } else {
                    setError(errText(e, 'Не удалось отправить отметку'));
                }
            })
            .finally(() => setSending(false));
    }, [apiBaseUrl, current, dropCurrent, headers, remaining, sending]);

    /* Необязательную новость закрывают крестиком, и это ТОЖЕ отметка о
       прочтении: иначе она возвращалась бы при каждом заходе, а «закрыл» —
       это и есть ответ человека. Обязательную не закрывает ничто, кроме
       кнопки: ни крестика, ни Esc, ни клика по фону у неё нет. */
    const dismiss = useCallback(() => {
        if (!current || current.is_mandatory) return;
        axios.post(`${apiBaseUrl}/api/news/${current.id}/read`, {}, { headers })
            .then(() => load(true))
            .catch(() => { /* закрытие важнее отметки: окно уходит в любом случае */ });
        dropCurrent();
    }, [apiBaseUrl, current, dropCurrent, headers]);

    useEffect(() => {
        if (!current || current.is_mandatory) return undefined;
        const onKey = (e) => { if (e.key === 'Escape') dismiss(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [current, dismiss]);

    if (!current) return null;

    const ready = remaining <= 0;
    const authorRole = roleTitle(current.author_role);
    const meta = [publishedLabel(current.published_at), current.author_department]
        .filter(Boolean).join(' · ');

    return (
        <div
            className="fixed inset-0 z-[120] flex items-stretch justify-center bg-slate-900/45 backdrop-blur-md sm:items-center sm:p-6"
            style={{ fontFamily: APPLE_FONT }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="news-of-day-title"
        >
            <div className="flex w-full max-w-xl flex-col overflow-hidden bg-white shadow-2xl ring-1 ring-slate-900/10 sm:max-h-[90vh] sm:rounded-3xl">
                <div className="flex items-center justify-between gap-3 px-5 pt-5">
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 px-2.5 py-1 text-[12px] font-medium text-indigo-700">
                        <Bell className="h-3.5 w-3.5" aria-hidden="true" />
                        Новость дня
                    </span>
                    {current.is_mandatory ? (
                        <span className="text-[12px] text-slate-400">обязательно к прочтению</span>
                    ) : (
                        <button
                            type="button"
                            onClick={dismiss}
                            className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 active:scale-95"
                            aria-label="Закрыть"
                        >
                            <X className="h-4 w-4" aria-hidden="true" />
                        </button>
                    )}
                </div>

                <div className="px-5 pt-3.5">
                    <h2 id="news-of-day-title" className="text-[18px] font-semibold leading-snug text-slate-900">
                        {current.title}
                    </h2>
                    <div className="mt-3 flex items-center gap-2.5">
                        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-indigo-50 text-[12px] font-medium text-indigo-700">
                            {initialsOf(current.author_name)}
                        </span>
                        <div className="min-w-0">
                            <p className="truncate text-[13px] text-slate-900">
                                {current.author_name || 'Автор не указан'}
                                {authorRole ? ` · ${authorRole}` : ''}
                            </p>
                            {meta && <p className="truncate text-[12px] text-slate-400">{meta}</p>}
                        </div>
                    </div>
                </div>

                <div className="mt-3.5 flex-1 overflow-y-auto overscroll-contain border-t border-slate-100 px-5 py-4">
                    <div className="news-body" dangerouslySetInnerHTML={{ __html: current.body || '' }} />
                </div>

                {/* На телефоне кнопка во всю ширину и НАД подписью: при
                    flex-wrap она уезжала на вторую строку к левому краю и
                    читалась как второстепенная — при том что она единственная
                    в окне. flex-col-reverse ставит её первой по вертикали,
                    оставляя подпись там же, где она стоит на широком экране. */}
                <div className="flex flex-col-reverse gap-2 border-t border-slate-100 px-5 py-3.5 pb-[max(0.875rem,env(safe-area-inset-bottom))] sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
                    {/* Подпись про «нельзя закрыть» — только у обязательной и
                        только пока это правда: у необязательной есть крестик,
                        и замок рядом с ним читался бы как обман. */}
                    {current.is_mandatory ? (
                        <span className="inline-flex items-center gap-1.5 text-[12px] text-slate-400 max-sm:justify-center">
                            <Lock className="h-3.5 w-3.5" aria-hidden="true" />
                            Окно нельзя закрыть или свернуть
                        </span>
                    ) : <span />}
                    <div className="flex items-center gap-2 max-sm:w-full max-sm:flex-col-reverse max-sm:items-stretch">
                        {error && <span className="text-[12px] text-rose-600 max-sm:text-center">{error}</span>}
                        <button
                            type="button"
                            onClick={confirm}
                            disabled={!ready || sending}
                            className={`inline-flex h-10 items-center justify-center gap-1.5 rounded-xl px-5 text-[14px] font-medium transition active:scale-[0.98] sm:h-9 ${
                                ready && !sending
                                    ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                                    : 'cursor-not-allowed bg-slate-100 text-slate-400'
                            }`}
                            /* Причина неактивности — в самой кнопке, а не
                               отдельной строкой рядом: подсказка «кнопка
                               загорится через 8 с» в углу читается позже, чем
                               человек успевает по ней щёлкнуть. */
                            aria-live="polite"
                        >
                            {sending
                                ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                                : ready && <Check className="h-4 w-4" aria-hidden="true" />}
                            {ready ? 'Прочитал' : `Прочитал · ${remaining} с`}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
