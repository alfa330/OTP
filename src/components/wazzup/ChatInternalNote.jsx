import React from 'react';
import { StickyNote } from 'lucide-react';

export default function ChatInternalNote({ note }) {
    const created = new Date(note.createdAt);
    const validDate = Number.isFinite(created.getTime());
    return (
        <div className="mx-auto w-full max-w-[680px] rounded-2xl border border-amber-200/80 bg-amber-50 px-4 py-3 text-amber-950 shadow-sm dark:border-amber-800/60 dark:bg-amber-950/40 dark:text-amber-100"
            data-internal-note-id={note.id}>
            <div className="mb-1.5 flex items-center gap-1.5 text-xs text-amber-800 dark:text-amber-300">
                <StickyNote size={14} aria-hidden="true" />
                <span className="font-medium">Внутренний комментарий</span>
                <span className="ml-auto shrink-0" title={validDate ? created.toLocaleString('ru-RU') : undefined}>
                    {validDate ? created.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : ''}
                </span>
            </div>
            <div className="mb-1 text-xs font-medium text-amber-800 dark:text-amber-300">{note.authorName || 'Сотрудник'}</div>
            <div className="whitespace-pre-wrap break-words text-[15px] leading-relaxed [overflow-wrap:anywhere]">{note.text}</div>
        </div>
    );
}
