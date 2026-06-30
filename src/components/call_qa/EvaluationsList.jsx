import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { ChevronRight, Bot, User2, Shuffle, Loader2, ClipboardList } from 'lucide-react';
import { APPLE_FONT, iosCard, iosBtnPrimary, IosBadge } from '../ui/ios';

/* Оценки: список уже оценённых ИИ звонков (реальные данные из кэша) + кнопка
 * «Случайный звонок» — берёт случайный оценённый человеком звонок ОП и прогоняет ИИ. */

export default function EvaluationsList(props) {
    const { apiBaseUrl, withAccessTokenHeader, onOpen, showToast } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [data, setData] = useState(null);   // null = загрузка
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!apiBaseUrl) { setData([]); return; }
        let live = true;
        axios.get(`${apiBaseUrl}/api/ai-qa/evaluations`, { headers: headers() })
            .then((r) => { if (live) setData(r.data.items || []); })
            .catch(() => { if (live) setData([]); });
        return () => { live = false; };
        // eslint-disable-next-line
    }, [apiBaseUrl]);

    const randomCall = () => {
        if (!apiBaseUrl) { showToast?.('Бэкенд недоступен', 'error'); return; }
        setBusy(true);
        axios.get(`${apiBaseUrl}/api/ai-qa/random-call`, { headers: headers() })
            .then((r) => onOpen?.(r.data.call))
            .catch((e) => showToast?.(e?.response?.data?.error || 'Не удалось получить случайный звонок', 'error'))
            .finally(() => setBusy(false));
    };

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-3">
            <div className="flex items-center justify-between gap-3">
                <p className="text-[13px] text-slate-500">
                    Звонки, уже оценённые ИИ. Можно взять случайный из оценённых человеком и проверить ИИ.
                </p>
                <button onClick={randomCall} disabled={busy} className={iosBtnPrimary + ' shrink-0'}>
                    {busy ? <Loader2 size={15} className="animate-spin" /> : <Shuffle size={15} />}Случайный звонок
                </button>
            </div>

            {data === null ? (
                <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-12 text-slate-400`}>
                    <Loader2 size={20} className="animate-spin" />Загрузка…
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
                        <button key={m.id} onClick={() => onOpen?.(m)}
                            className={`${iosCard} flex w-full items-center gap-3 p-3.5 text-left transition hover:ring-blue-200 active:scale-[0.995]`}>
                            <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2">
                                    <span className="text-[14px] font-semibold text-slate-900">#{m.id}</span>
                                    <IosBadge tone="slate">{m.direction}</IosBadge>
                                    <IosBadge tone="green"><Bot size={11} />оценено ИИ</IosBadge>
                                </div>
                                <p className="mt-0.5 text-[12px] text-slate-400">{m.operator} · {m.datetime}</p>
                            </div>
                            <div className="flex shrink-0 items-center gap-3">
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
                </div>
            )}
        </div>
    );
}
