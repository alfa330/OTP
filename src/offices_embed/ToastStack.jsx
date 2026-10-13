/* Тосты страницы офисов.
 *
 * Свои, а не портальные: тост портала — местная функция App.jsx и тянет за собой
 * весь App. WikiOffices зовёт showToast(текст, 'success' | 'error') — ровно этот
 * вызов здесь и обслуживается. Внизу экрана и во всю ширину: вкладка телефона
 * узкая, а угловой тост в 243 px закрыл бы половину карточки офиса.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { CircleAlert, CircleCheck, X } from 'lucide-react';

// Сколько тост висит. Ошибку дольше: её читают, а «скопировано» — видят.
const TOAST_MS = { success: 4000, info: 4000, error: 5500 };
// Больше трёх разом — уже не уведомление, а лента; старые уходят первыми.
const MAX_TOASTS = 3;

let toastSeq = 0;

export function useToastStack() {
    const [toasts, setToasts] = useState([]);
    const timersRef = useRef(new Map());

    const dismiss = useCallback((id) => {
        clearTimeout(timersRef.current.get(id));
        timersRef.current.delete(id);
        setToasts((list) => list.filter((toast) => toast.id !== id));
    }, []);

    /* Функция стабильна (useCallback без меняющихся зависимостей): WikiOffices и
       так оборачивает её в useStableCallback, но держать её ровной дешевле, чем
       помнить об этом. */
    const push = useCallback((message, type = 'info') => {
        const text = String(message || '').trim();
        if (!text) return;
        const kind = TOAST_MS[type] ? type : 'info';
        toastSeq += 1;
        const id = toastSeq;
        setToasts((list) => {
            const next = [...list, { id, text, type: kind }];
            next.slice(0, Math.max(0, next.length - MAX_TOASTS)).forEach((old) => {
                clearTimeout(timersRef.current.get(old.id));
                timersRef.current.delete(old.id);
            });
            return next.slice(-MAX_TOASTS);
        });
        timersRef.current.set(id, setTimeout(() => dismiss(id), TOAST_MS[kind]));
    }, [dismiss]);

    useEffect(() => () => {
        timersRef.current.forEach((timer) => clearTimeout(timer));
        timersRef.current.clear();
    }, []);

    return { toasts, push, dismiss };
}

export function ToastStack({ toasts, onDismiss }) {
    if (!toasts.length) return null;
    return (
        <div
            className="pointer-events-none fixed inset-x-3 bottom-3 z-[120] mx-auto flex max-w-md flex-col gap-2"
            role="status"
            aria-live="polite"
        >
            {toasts.map((toast) => (
                <div
                    key={toast.id}
                    className="pointer-events-auto flex items-start gap-2 rounded-2xl bg-slate-900/90 px-3.5 py-2.5 text-[13px] leading-snug text-white shadow-lg backdrop-blur"
                >
                    {toast.type === 'error'
                        ? <CircleAlert size={16} className="mt-px shrink-0 text-rose-300" aria-hidden="true" />
                        : <CircleCheck size={16} className="mt-px shrink-0 text-emerald-300" aria-hidden="true" />}
                    <span className="min-w-0 flex-1 break-words">{toast.text}</span>
                    <button
                        type="button"
                        onClick={() => onDismiss(toast.id)}
                        className="-mr-1 grid h-5 w-5 shrink-0 place-items-center rounded-full text-white/60 hover:text-white"
                        aria-label="Скрыть"
                    >
                        <X size={13} />
                    </button>
                </div>
            ))}
        </div>
    );
}
