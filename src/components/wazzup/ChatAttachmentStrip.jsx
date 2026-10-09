import React from 'react';
import { ChevronLeft, ChevronRight, FileText, Image, Video } from 'lucide-react';
import { attachmentName, attachmentPreviewKind } from './chatAttachments';

const arrowClass = 'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 disabled:opacity-30';
const showActiveThumbnail = (node) => node?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });

export default function ChatAttachmentStrip({ items, selectedId, onSelect, onPrevious, onNext, compact = false }) {
    const index = items.findIndex((item) => item.messageId === selectedId);
    if (items.length < 2 || index < 0) return null;
    return <div className="shrink-0 border-t border-slate-200 bg-white px-2 py-2" aria-label="Вложения из этой группы" data-attachment-navigation>
        <div className="flex items-center gap-2">
            <button type="button" className={arrowClass} onClick={onPrevious} disabled={index === 0}
                aria-label="Предыдущее вложение" title="Предыдущее вложение"><ChevronLeft size={18} /></button>
            {compact && <span className="min-w-0 flex-1 text-center text-xs tabular-nums text-slate-500 md:hidden" aria-live="polite">Вложение {index + 1} / {items.length}</span>}
            <div className={`wazzup-scrollbar min-w-0 flex-1 items-center gap-2 overflow-x-auto px-1 py-1 ${compact ? 'hidden md:flex' : 'flex'}`}>
                {items.map((item, itemIndex) => {
                    const active = item.messageId === selectedId;
                    const kind = attachmentPreviewKind(item);
                    const photo = kind === 'image';
                    const name = attachmentName(item) || (photo ? 'Фото' : kind === 'video' ? 'Видео' : 'Документ');
                    const Icon = kind === 'video' ? Video : FileText;
                    return <button key={item.messageId} type="button" ref={active ? showActiveThumbnail : null}
                        onClick={() => onSelect(item)} aria-pressed={active} aria-label={`Вложение ${itemIndex + 1}: ${name}`}
                        title={name} className={`relative flex h-14 w-16 shrink-0 flex-col items-center justify-center overflow-hidden rounded-lg border bg-slate-100 ${active ? 'border-blue-500 ring-2 ring-blue-500/40' : 'border-slate-200 hover:border-slate-400'}`}>
                        {photo ? <>
                            <Image size={20} className="text-slate-400" aria-hidden="true" />
                            <img src={item.contentUri} alt="" loading="lazy" decoding="async" width="64" height="56"
                                className="absolute inset-0 h-full w-full object-cover" onError={(event) => { event.currentTarget.hidden = true; }} />
                        </> : <>
                            <Icon size={20} className="text-slate-500" aria-hidden="true" />
                            <span className="mt-1 w-full truncate px-1 text-[9px] leading-3 text-slate-600">{name}</span>
                        </>}
                    </button>;
                })}
            </div>
            <button type="button" className={arrowClass} onClick={onNext} disabled={index === items.length - 1}
                aria-label="Следующее вложение" title="Следующее вложение"><ChevronRight size={18} /></button>
        </div>
        <p className={`mt-1 text-center text-[11px] tabular-nums text-slate-500 ${compact ? 'hidden md:block' : ''}`} aria-live="polite">{index + 1} / {items.length}</p>
    </div>;
}
