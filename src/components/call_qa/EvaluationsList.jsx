import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { ChevronRight, Bot, User2, Shuffle, Loader2, ClipboardList, AlertCircle, RefreshCw } from 'lucide-react';
import { APPLE_FONT, iosCard, iosBtnPrimary, iosBtnSecondary, IosBadge } from '../ui/ios';

/* Оценки: список уже оценённых ИИ звонков (реальные данные из кэша) + кнопка
 * «Случайный звонок» — берёт случайный оценённый человеком звонок ОП и прогоняет ИИ. */

export default function EvaluationsList(props) {
    const { apiBaseUrl, withAccessTokenHeader, onOpen, showToast } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [data, setData] = useState(null);   // null = загрузка
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const loadRequest = useRef({ id: 0, controller: null });
    const randomRequest = useRef({ id: 0, controller: null });

    const load = () => {
        loadRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = loadRequest.current.id + 1;
        loadRequest.current = { id: requestId, controller };
        setData(null); setError(null);
        if (!apiBaseUrl) {
            setData([]); setError('Сервис оценок не настроен');
            return;
        }
        axios.get(`${apiBaseUrl}/api/ai-qa/evaluations`, { headers: headers(), signal: controller.signal })
            .then((r) => {
                if (requestId === loadRequest.current.id) setData(r.data.items || []);
            })
            .catch((requestError) => {
                if (!axios.isCancel(requestError) && requestId === loadRequest.current.id) {
                    setData([]);
                    setError(requestError?.response?.data?.error || 'Не удалось загрузить оценки');
                }
            });
    };

    useEffect(() => {
        load();
        return () => {
            loadRequest.current.controller?.abort();
            randomRequest.current.controller?.abort();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl]);

    const randomCall = () => {
        if (!apiBaseUrl) { showToast?.('Бэкенд недоступен', 'error'); return; }
        randomRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = randomRequest.current.id + 1;
        randomRequest.current = { id: requestId, controller };
        setBusy(true);
        axios.get(`${apiBaseUrl}/api/ai-qa/random-call`, { headers: headers(), signal: controller.signal })
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

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-3">
            <div className="flex flex-col items-stretch justify-between gap-3 sm:flex-row sm:items-center">
                <p className="text-[13px] text-slate-500">
                    Звонки, уже оценённые ИИ. Можно взять случайный из оценённых человеком и проверить ИИ.
                </p>
                <button type="button" onClick={randomCall} disabled={busy} className={iosBtnPrimary + ' shrink-0'}>
                    {busy ? <Loader2 size={15} className="animate-spin" /> : <Shuffle size={15} />}
                    {busy ? 'Подбираю звонок…' : 'Оценить случайный звонок'}
                </button>
            </div>

            {data === null ? (
                <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-12 text-slate-500`} role="status">
                    <Loader2 size={20} className="animate-spin" aria-hidden="true" />Загрузка оценок…
                </div>
            ) : error ? (
                <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-12 text-center`} role="alert">
                    <AlertCircle size={25} className="text-rose-500" />
                    <p className="text-[13px] font-medium text-slate-700">{error}</p>
                    <button type="button" onClick={load} className={iosBtnSecondary}><RefreshCw size={14} />Повторить</button>
                </div>
            ) : data.length === 0 ? (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <ClipboardList size={26} className="text-slate-300" />
                    <p className="text-[13px] text-slate-500">Пока ни одного звонка не оценено ИИ.</p>
                    <p className="text-[12px] text-slate-400">Нажмите «Случайный звонок», чтобы протестировать оценку.</p>
                </div>
            ) : (
                <div className="space-y-2">
                    {data.map((m) => (
                        <button key={m.id} type="button" onClick={() => onOpen?.(m)}
                            className={`${iosCard} flex w-full flex-col items-stretch gap-2.5 p-3.5 text-left transition hover:ring-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 active:scale-[0.995] sm:flex-row sm:items-center sm:gap-3`}>
                            <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2">
                                    <span className="text-[14px] font-semibold text-slate-900">#{m.id}</span>
                                    <IosBadge tone="slate">{m.direction}</IosBadge>
                                    <IosBadge tone="green"><Bot size={11} />оценено ИИ</IosBadge>
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
                    {data.length >= 100 && (
                        <p className="px-1 pt-1 text-[11.5px] text-slate-500">Показаны последние 100 оценок.</p>
                    )}
                </div>
            )}
        </div>
    );
}
