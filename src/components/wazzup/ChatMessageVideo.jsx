import React, { useEffect, useRef } from 'react';
import { Play } from 'lucide-react';

export default function ChatMessageVideo({ src, label = 'Видео', onOpen }) {
    const video = useRef(null);
    useEffect(() => {
        const element = video.current;
        if (element && !element.getAttribute('src')) element.src = src;
        return () => {
            element?.pause();
            element?.removeAttribute('src');
            element?.load();
        };
    }, [src]);
    return <button type="button" onClick={onOpen} aria-label={`Открыть видео: ${label}`}
        title="Посмотреть видео" className="relative block aspect-video w-[280px] max-w-full overflow-hidden rounded-xl bg-slate-900 text-white">
        <video key={src} ref={video} src={src} preload="metadata" muted playsInline tabIndex={-1} aria-hidden="true"
            className="pointer-events-none h-full w-full object-cover" />
        <span className="pointer-events-none absolute inset-0 grid place-items-center bg-black/10">
            <span className="grid h-12 w-12 place-items-center rounded-full bg-black/50 ring-1 ring-white/40">
                <Play size={23} fill="currentColor" className="translate-x-px" aria-hidden="true" />
            </span>
        </span>
        <span className="pointer-events-none absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/70 px-3 pb-2 pt-6 text-left text-xs">{label}</span>
    </button>;
}
