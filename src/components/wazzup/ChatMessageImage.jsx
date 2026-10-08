import React, { useState } from 'react';
import { ImageOff, Image as ImageIcon } from 'lucide-react';

// The preview keeps the same dimensions before load, on success and on error.
// Natural image dimensions must not change the height of an open conversation.
function ImagePreview({ src, label, light, onOpen }) {
    const [status, setStatus] = useState('loading');
    return <button type="button" onClick={onOpen} title="Открыть изображение"
        aria-label={label || 'Открыть изображение'}
        className={`relative block aspect-[4/3] w-[280px] max-w-full overflow-hidden rounded-xl text-left ${
            light ? 'bg-white/15' : 'bg-slate-100'} ${onOpen ? 'cursor-zoom-in' : ''}`}>
        {status !== 'loaded' && <span aria-hidden="true"
            className={`absolute inset-0 flex flex-col items-center justify-center gap-2 px-3 text-center text-xs ${
                light ? 'text-blue-100' : 'text-slate-400'}`}>
            {status === 'error' ? <ImageOff size={24} /> : <ImageIcon size={24} />}
            {status === 'error' ? 'Не удалось загрузить изображение' : 'Загрузка изображения…'}
        </span>}
        <img src={src} alt={label || 'Фото'} loading="lazy" decoding="async"
            onLoad={() => setStatus('loaded')} onError={() => setStatus('error')}
            className={`absolute inset-0 h-full w-full object-contain ${status === 'loaded' ? '' : 'opacity-0'}`} />
    </button>;
}

export default function ChatMessageImage(props) {
    return <ImagePreview key={props.src} {...props} />;
}
