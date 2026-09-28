import React, { useEffect, useState } from 'react';
import { ChevronDown, Loader2 } from 'lucide-react';

/* История разбора низкой оценки: кто и когда менял вердикт и кому засчитана
 * оценка (задача #286). Свёрнута по умолчанию и показывается, только если в
 * ней что-то есть: текущие голоса и так видны карточками выше, журнал нужен,
 * когда надо понять, как к ним пришли. Грузится при раскрытии. */

const STATUS_LABEL = { valid: 'Обоснованно', invalid: 'Необоснованно' };
// Цвета как у вердиктов раздела: обоснованно — красный, необоснованно — зелёный.
const STATUS_TONE = { valid: 'text-rose-700', invalid: 'text-emerald-700' };

const names = (operators) => (operators || []).map((op) => op?.name || 'Без имени').join(', ');

// Время уже приведено к Алматы на сервере — строку не пересчитываем.
export const historyStamp = (iso) => {
    const text = String(iso || '');
    if (!text) return '';
    const [datePart, timePart = ''] = text.split('T');
    const [, mm, dd] = datePart.split('-');
    return `${dd}.${mm} ${timePart.slice(0, 5)}`.trim();
};

export const historyLines = (event) => {
    const lines = [];
    const status = event?.status || null;
    const prev = event?.prev_status || null;
    const label = status ? STATUS_LABEL[status] || status : 'Вердикт снят';
    const tone = status ? STATUS_TONE[status] || 'text-slate-700' : 'text-slate-500';
    const was = prev && prev !== status ? ` · было: ${STATUS_LABEL[prev] || prev}` : '';
    lines.push({ key: 'status', text: `${event?.action === 'final' ? 'Итог руководителя: ' : ''}${label}${was}`, tone });
    if (event?.operators) {
        const was = event?.prev_operators ? ` · было: ${names(event.prev_operators)}` : '';
        lines.push({ key: 'operators', text: `Засчитана: ${names(event.operators)}${was}`, tone: 'text-slate-700' });
    }
    if (event?.comment != null) {
        lines.push({
            key: 'comment',
            text: event.comment ? `«${event.comment}»` : 'Комментарий удалён',
            tone: event.comment ? 'text-slate-600' : 'text-slate-400',
        });
    }
    if ((event?.final_status || null) !== (event?.prev_final_status || null) && event?.action !== 'final') {
        lines.push({
            key: 'final',
            text: event?.final_status ? `Итог: ${STATUS_LABEL[event.final_status] || event.final_status}` : 'Итог снят — проверки снова расходятся',
            tone: event?.final_status ? STATUS_TONE[event.final_status] || 'text-slate-700' : 'text-slate-500',
        });
    }
    return lines;
};

export default function LowRatingHistory({ reviewId, count = 0, loadHistory }) {
    const [open, setOpen] = useState(false);
    const [state, setState] = useState({ items: [], loading: false, error: '' });

    // Другая оценка — журнал свёрнут: чужая история под новой карточкой сбивала бы.
    useEffect(() => { setOpen(false); setState({ items: [], loading: false, error: '' }); }, [reviewId]);

    useEffect(() => {
        if (!open || !reviewId || !loadHistory) return undefined;
        let cancelled = false;
        setState((prev) => ({ ...prev, loading: true, error: '' }));
        loadHistory(reviewId)
            .then((items) => { if (!cancelled) setState({ items: items || [], loading: false, error: '' }); })
            .catch((error) => {
                if (!cancelled) setState({ items: [], loading: false, error: error?.message || 'Не удалось загрузить историю' });
            });
        return () => { cancelled = true; };
        // count в зависимостях: после сохранения событий стало больше — перечитываем.
    }, [open, reviewId, count, loadHistory]);

    if (!count) return null;

    return (
        <div className="mt-2 rounded-xl bg-slate-50 ring-1 ring-slate-200">
            <button
                type="button"
                onClick={() => setOpen((value) => !value)}
                aria-expanded={open}
                className="flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-left text-[12px] font-semibold text-slate-600 transition hover:bg-slate-100"
            >
                <span>
                    История изменений
                    <span className="ml-1.5 tabular-nums text-slate-400">{count}</span>
                </span>
                <ChevronDown size={14} className={`shrink-0 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>
            {open && (
                <div className="max-h-56 space-y-2 overflow-y-auto border-t border-slate-200 px-3 py-2 ios-modal-scroll">
                    {state.loading && !state.items.length && (
                        <div className="flex items-center gap-2 py-1 text-[12px] text-slate-400">
                            <Loader2 size={13} className="animate-spin" /> Загружаем…
                        </div>
                    )}
                    {state.error && <div className="py-1 text-[12px] text-rose-600">{state.error}</div>}
                    {state.items.map((event) => (
                        <div key={event.id} className="text-[12px] leading-5">
                            <div className="flex flex-wrap items-baseline gap-x-2">
                                <span className="font-semibold text-slate-800">{event.actor_name || 'Проверяющий'}</span>
                                <span className="tabular-nums text-[11px] text-slate-400">{historyStamp(event.created_at)}</span>
                            </div>
                            {historyLines(event).map((line) => (
                                <div key={line.key} className={`whitespace-pre-wrap break-words ${line.tone}`}>{line.text}</div>
                            ))}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
