import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { ChevronRight, Bot, User2, Shuffle, Loader2, ClipboardList, AlertCircle, RefreshCw, ChevronDown, PhoneIncoming } from 'lucide-react';
import { APPLE_FONT, iosCard, iosBtnPrimary, iosBtnSecondary, IosBadge } from '../ui/ios';
import { isChat, subjectTitle, SOURCE_LABEL, SUBJECT_IMPORTED_CALL } from './subjects';

/* Список уже оценённых ИИ субъектов (реальные данные из кэша), новые сверху,
 * постраничная подгрузка «Показать ещё» — можно посмотреть все. Субъект задаёт
 * вкладка: «Звонки» отдаёт звонки, «Чаты» — переписку своего источника. Кнопки
 * подбора есть только у звонков: у чатов свой подбор в ChatQueue. */

const PAGE = 50;

export default function EvaluationsList(props) {
    const { apiBaseUrl, withAccessTokenHeader, onOpen, showToast, subject = 'call',
            department, canPull = false } = props;
    const chats = isChat(subject);
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [items, setItems] = useState(null);   // null = первичная загрузка
    const [total, setTotal] = useState(0);
    const [error, setError] = useState(null);
    const [moreBusy, setMoreBusy] = useState(false);
    const [busy, setBusy] = useState(false);
    const [pullBusy, setPullBusy] = useState(false);
    const loadRequest = useRef({ id: 0, controller: null });
    const randomRequest = useRef({ id: 0, controller: null });
    const pullRequest = useRef({ id: 0, department: null });
    /* Отдел, открытый СЕЙЧАС. Сравнивать ответ с `department` из замыкания
     * бесполезно: там лежит значение того рендера, в котором запрос ушёл, —
     * ровно оно же записано и в pullRequest. Ref переживает перерисовку и
     * показывает, на что человек смотрит в момент ответа. */
    const departmentRef = useRef(department);
    departmentRef.current = department;

    const fetchPage = (offset, append) => {
        loadRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = loadRequest.current.id + 1;
        loadRequest.current = { id: requestId, controller };
        if (append) setMoreBusy(true); else { setItems(null); setError(null); }
        if (!apiBaseUrl) { setItems([]); setError('Сервис оценок не настроен'); return; }
        axios.get(`${apiBaseUrl}/api/ai-qa/evaluations`,
            { params: { limit: PAGE, offset, subject, ...(department ? { department } : {}) },
              headers: headers(), signal: controller.signal })
            .then((r) => {
                if (requestId !== loadRequest.current.id) return;
                const page = r.data.items || [];
                setTotal(typeof r.data.total === 'number' ? r.data.total : page.length);
                setItems((prev) => (append && Array.isArray(prev) ? [...prev, ...page] : page));
            })
            .catch((requestError) => {
                if (axios.isCancel(requestError) || requestId !== loadRequest.current.id) return;
                if (append) showToast?.('Не удалось подгрузить ещё', 'error');
                else { setItems([]); setError(requestError?.response?.data?.error || 'Не удалось загрузить оценки'); }
            })
            .finally(() => { if (requestId === loadRequest.current.id) setMoreBusy(false); });
    };

    useEffect(() => {
        fetchPage(0, false);
        return () => {
            loadRequest.current.controller?.abort();
            randomRequest.current.controller?.abort();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, subject, department]);

    const randomCall = () => {
        if (!apiBaseUrl) { showToast?.('Бэкенд недоступен', 'error'); return; }
        randomRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = randomRequest.current.id + 1;
        randomRequest.current = { id: requestId, controller };
        setBusy(true);
        axios.get(`${apiBaseUrl}/api/ai-qa/random-call`,
            { params: { ...(department ? { department } : {}) },
              headers: headers(), signal: controller.signal })
            .then((r) => {
                if (requestId === randomRequest.current.id) onOpen?.(r.data.call);
            })
            .catch((e) => {
                if (!axios.isCancel(e) && requestId === randomRequest.current.id) {
                    showToast?.(e?.response?.data?.error || 'Не удалось получить случайный звонок', 'error');
                }
            })
            .finally(() => {
                if (requestId === randomRequest.current.id) setBusy(false);
            });
    };

    /* «Подтянуть из АТС» — сверх пула портала. Пул (звонки, подтянутые в журнал
     * кнопкой «Случайный звонок», но так и не оценённые) конечен; эта кнопка
     * идёт в саму АТС за новым звонком: СЗоВ — Oktell, Тез КЦ — Binotel. Запись
     * уходит в наше хранилище, строка ложится как «не оценён», и оценки в
     * журнале от этого не появляется. У отдела продаж кнопки нет: там записи
     * загружают руками, подтягивать неоткуда. */
    const pullCall = () => {
        if (!apiBaseUrl) { showToast?.('Бэкенд недоступен', 'error'); return; }
        // Запрос идёт в АТС и качает запись — это секунды, за которые человек
        // успевает сменить отдел или уйти со вкладки. Ответ применяем только
        // если он всё ещё про ТОТ отдел: иначе открылась бы карточка чужого.
        // Запрос при этом НЕ отменяем: звонок уже подтянут и лежит в пуле, и
        // обрыв соединения его оттуда не убрал бы — просто не открываем карточку.
        const requestId = pullRequest.current.id + 1;
        pullRequest.current = { id: requestId, department };
        setPullBusy(true);
        axios.post(`${apiBaseUrl}/api/ai-qa/pull-call`,
            { ...(department ? { department } : {}), count: 1 }, { headers: headers() })
            .then((r) => {
                if (requestId !== pullRequest.current.id
                    || departmentRef.current !== pullRequest.current.department) return;
                const call = (r.data?.calls || [])[0] || r.data?.call;
                if (!call) { showToast?.('АТС не вернула подходящий звонок', 'error'); return; }
                showToast?.('Звонок подтянут — оцениваю', 'success');
                onOpen?.({ id: call.id, subject: SUBJECT_IMPORTED_CALL,
                           operator: call.operator_name, datetime: call.datetime });
                fetchPage(0, false);
            })
            .catch((e) => {
                if (requestId !== pullRequest.current.id) return;
                showToast?.(e?.response?.data?.error
                    || 'Не удалось подтянуть звонок из АТС', 'error');
            })
            .finally(() => { if (requestId === pullRequest.current.id) setPullBusy(false); });
    };

    const loaded = Array.isArray(items) ? items.length : 0;
    const hasMore = loaded > 0 && loaded < total;

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-3">
            <div className="flex flex-col items-stretch justify-between gap-3 sm:flex-row sm:items-center">
                <p className="text-[13px] text-slate-500">
                    {chats
                        ? `Переписки, уже оценённые ИИ${total ? ` · всего ${total}` : ''}.`
                        : `Звонки, уже оценённые ИИ${total ? ` · всего ${total}` : ''}. `
                          + (canPull
                              ? 'Случайный звонок берётся из подтянутых, но не оценённых в журнале; «Из АТС» тянет новый.'
                              : 'Можно взять случайный звонок из оценённых человеком и проверить ИИ.')}
                </p>
                <div className="flex shrink-0 items-center gap-2">
                    <button type="button" onClick={() => fetchPage(0, false)} disabled={items === null}
                        className={iosBtnSecondary} title="Обновить список">
                        <RefreshCw size={14} className={items === null ? 'animate-spin' : ''} />
                    </button>
                    {!chats && canPull && (
                        <button type="button" onClick={pullCall} disabled={pullBusy || busy}
                                className={`${iosBtnSecondary} disabled:cursor-not-allowed disabled:opacity-50`}
                                title="Взять новый звонок прямо из АТС (Oktell / Binotel) и оценить его">
                            {pullBusy ? <Loader2 size={14} className="animate-spin" /> : <PhoneIncoming size={14} />}
                            {pullBusy ? 'Тяну из АТС…' : 'Из АТС'}
                        </button>
                    )}
                    {!chats && (
                        <button type="button" onClick={randomCall} disabled={busy || pullBusy} className={iosBtnPrimary}>
                            {busy ? <Loader2 size={15} className="animate-spin" /> : <Shuffle size={15} />}
                            {busy ? 'Подбираю звонок…' : 'Оценить случайный звонок'}
                        </button>
                    )}
                </div>
            </div>

            {items === null ? (
                <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-12 text-slate-500`} role="status">
                    <Loader2 size={20} className="animate-spin" aria-hidden="true" />Загрузка оценок…
                </div>
            ) : error ? (
                <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-12 text-center`} role="alert">
                    <AlertCircle size={25} className="text-rose-500" />
                    <p className="text-[13px] font-medium text-slate-700">{error}</p>
                    <button type="button" onClick={() => fetchPage(0, false)} className={iosBtnSecondary}><RefreshCw size={14} />Повторить</button>
                </div>
            ) : items.length === 0 ? (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <ClipboardList size={26} className="text-slate-300" />
                    <p className="text-[13px] text-slate-500">
                        {chats ? 'Пока ни одна переписка не оценена ИИ.' : 'Пока ни один звонок не оценён ИИ.'}
                    </p>
                    <p className="text-[12px] text-slate-400">
                        {chats
                            ? 'Нажмите кнопку подбора выше, чтобы получить первую оценку.'
                            : 'Нажмите «Оценить случайный звонок», чтобы протестировать оценку.'}
                    </p>
                </div>
            ) : (
                <div className="space-y-2">
                    {items.map((m) => (
                        <button key={m.id} type="button" onClick={() => onOpen?.(m)}
                            className={`${iosCard} flex w-full flex-col items-stretch gap-2.5 p-3.5 text-left transition hover:ring-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 active:scale-[0.995] sm:flex-row sm:items-center sm:gap-3`}>
                            <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2">
                                    <span className="text-[14px] font-semibold text-slate-900">
                                        {subjectTitle(m.subject, m.id)}
                                    </span>
                                    <IosBadge tone="slate">{m.direction}</IosBadge>
                                    <IosBadge tone="green"><Bot size={11} />оценено ИИ</IosBadge>
                                    {/* Откуда субъект: у СЗоВ и Тез КЦ в одном списке
                                        встречаются звонки из журнала и звонки, подтянутые
                                        из АТС без человеческой оценки, — по пустой колонке
                                        «человек» это выглядело бы как потерянная оценка. */}
                                    {m.subject === SUBJECT_IMPORTED_CALL && (
                                        <IosBadge tone="blue" title={SOURCE_LABEL[m.subject]}>
                                            из АТС
                                        </IosBadge>
                                    )}
                                </div>
                                <p className="mt-0.5 text-[12px] text-slate-400">{m.operator} · {m.datetime}</p>
                            </div>
                            <div className="flex items-center gap-3 sm:shrink-0">
                                {m.ai != null && (
                                    <div className="flex items-center gap-1 text-[12.5px]" title="Оценка ИИ">
                                        <Bot size={13} className="text-blue-300" />
                                        <span className="font-semibold tabular-nums text-slate-700">{m.ai}</span>
                                    </div>
                                )}
                                {m.human != null && (
                                    <div className="flex items-center gap-1 text-[12.5px]" title="Оценка человека">
                                        <User2 size={13} className="text-slate-300" />
                                        <span className="font-semibold tabular-nums text-slate-700">{m.human}</span>
                                    </div>
                                )}
                                <ChevronRight size={16} className="text-slate-300" />
                            </div>
                        </button>
                    ))}

                    <div className="flex flex-col items-center gap-2 pt-1">
                        {hasMore ? (
                            <button type="button" onClick={() => fetchPage(loaded, true)} disabled={moreBusy}
                                className={iosBtnSecondary}>
                                {moreBusy ? <Loader2 size={14} className="animate-spin" /> : <ChevronDown size={14} />}
                                {moreBusy ? 'Загрузка…' : `Показать ещё (осталось ${total - loaded})`}
                            </button>
                        ) : null}
                        <p className="text-[11.5px] text-slate-400">Показано {loaded} из {total}</p>
                    </div>
                </div>
            )}
        </div>
    );
}
