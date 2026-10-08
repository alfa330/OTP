import React from 'react';
import { Loader2, MessagesSquare, Play } from 'lucide-react';
import { APPLE_FONT } from '../ui/ios';
import { autoCloseNote } from './workspaceStatus';
import { setWorkspaceStatus, useWorkspace } from './workspaceStore';

/* Экран до начала смены: одна кнопка посередине. Чаты за ней открываются только
 * после нажатия — так первое, что делает верификатор в разделе, это отмечает
 * начало смены, и учёт часов получает её начало без отдельного шага.
 *
 * Статус, с которого смена начинается («Активный»), называет сервер: второй
 * копии правила здесь нет. */
export default function ShiftStartScreen({ onStartIntent }) {
    const { startKey, current, pending, statusError } = useWorkspace();
    const note = autoCloseNote(current);

    /* О намерении сообщаем ДО запроса: окно появляется в тот же миг, когда
       сервер записал статус, и к этому мигу родитель уже должен знать, что его
       открыли кнопкой. Не записался — намерение снимаем. */
    const start = async () => {
        if (!startKey || pending) return;
        onStartIntent?.(true);
        if (!await setWorkspaceStatus(startKey)) onStartIntent?.(false);
    };

    return (
        <div className="wz-start grid min-h-full place-items-center px-4 py-8" style={{ fontFamily: APPLE_FONT }}>
            <div className="flex w-full max-w-sm flex-col items-center text-center">
                <div className="grid h-[72px] w-[72px] place-items-center rounded-[22px] bg-gradient-to-b from-blue-500 to-blue-600 text-white shadow-[0_10px_28px_rgba(37,99,235,0.32)]">
                    <MessagesSquare size={32} strokeWidth={1.8} />
                </div>
                <h2 className="mt-5 text-[22px] font-semibold tracking-tight text-slate-900">Чаты ОП</h2>
                <p className="mt-1.5 text-[14px] text-slate-500">
                    {note || 'Смена не начата'}
                </p>
                <button type="button" onClick={start} disabled={!startKey || pending}
                    className="mt-7 inline-flex h-12 min-w-[220px] items-center justify-center gap-2.5 rounded-2xl bg-blue-600 px-7 text-[15.5px] font-semibold text-white shadow-[0_8px_22px_rgba(37,99,235,0.28)] transition-all hover:bg-blue-700 active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60">
                    {pending ? <Loader2 size={18} className="animate-spin" /> : <Play size={17} fill="currentColor" />}
                    Начать смену
                </button>
                {statusError && (
                    <p role="alert" className="mt-4 text-[13px] text-rose-600">{statusError}</p>
                )}
            </div>
        </div>
    );
}
