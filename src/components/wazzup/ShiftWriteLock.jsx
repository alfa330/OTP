import React from 'react';
import { Loader2, Play } from 'lucide-react';
import { statusDotClass } from './workspaceStatus';
import { setWorkspaceStatus, useWorkspace } from './workspaceStore';

/* Строка на месте поля ввода, пока верификатор не в «Активном»: писать клиентам
 * можно только в нём (владелец 19.10.2026; отправку отклоняет и сервер —
 * wazzup/pilot.py). Переписка, заметки и «Ответ не нужен» остаются.
 *
 * Классы рамки и фона — те же, что у поля ввода (ChatPilotComposer): темы окна
 * перекрашивают их сами, и строка не выпадает из темы. Черновик не теряется —
 * поле вернётся с ним вместе с «Активным» (chatDrafts.js). Подпись статуса
 * начала смены приходит с сервера, как и в меню статусов. */
export default function ShiftWriteLock() {
    const { statuses, startKey, current, pending } = useWorkspace();
    const active = statuses.find((item) => item.key === startKey)?.label || 'Активный';

    return (
        <div role="status" data-testid="wazzup-write-lock"
             className="shrink-0 border-t border-slate-200/70 bg-white p-2.5 sm:px-4">
            <div className="mx-auto flex w-full max-w-[1040px] flex-wrap items-center justify-between gap-x-3 gap-y-2">
                <p className="flex min-w-0 items-center gap-2 text-[13.5px] text-slate-600">
                    <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${statusDotClass(current?.tone)}`} />
                    <span>
                        <span className="font-semibold text-slate-800">{current?.label}</span>
                        {` · писать клиентам можно только в статусе «${active}»`}
                    </span>
                </p>
                <button type="button" disabled={pending || !startKey} onClick={() => setWorkspaceStatus(startKey)}
                    className="inline-flex h-9 shrink-0 items-center gap-2 rounded-xl bg-blue-600 px-3.5 text-[13px] font-semibold text-white transition-all hover:bg-blue-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60">
                    {pending ? <Loader2 size={15} className="animate-spin" /> : <Play size={13} fill="currentColor" />}
                    {`Вернуться в «${active}»`}
                </button>
            </div>
        </div>
    );
}
