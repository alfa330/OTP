/* Мелкие общие детали экранов «Карточки клиента»: страница телефона и раздел портала
 * собраны из одних и тех же — иначе запрос выглядел бы по-разному в двух местах.
 * Стиль — общий iOS/macOS портала (components/ui/ios.jsx): палитра slate, цвет только
 * там, где он что-то значит. */
import React, { useEffect, useRef } from 'react';
import { Loader2, Paperclip, X } from 'lucide-react';

import { IosBadge } from '../ui/ios';
import { REQUEST_TONES, STATE_TONES, fmtSize } from './cardRules';

export const Spinner = ({ text = '' }) => (
    <div className="flex items-center justify-center gap-2.5 py-10 text-[13px] text-slate-500">
        <Loader2 size={16} className="animate-spin text-slate-400" aria-hidden="true" />
        {text && <span>{text}</span>}
    </div>
);

export const EmptyState = ({ icon: Icon = null, title, text = '', children = null }) => (
    <div className="flex flex-col items-center px-6 py-10 text-center">
        {Icon && (
            <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                <Icon size={22} aria-hidden="true" />
            </div>
        )}
        <div className="mt-3 text-[14.5px] font-semibold text-slate-800">{title}</div>
        {text && <p className="mt-1 max-w-sm text-[12.5px] leading-relaxed text-slate-500">{text}</p>}
        {children}
    </div>
);

/** Состояние карточки. Заполненную не красим: зелёным в списке была бы каждая строка. */
export const StateBadge = ({ card }) => {
    if (!card || card.state === 'done') return null;
    return <IosBadge tone={STATE_TONES[card.state] || 'slate'}>{card.state_label}</IosBadge>;
};

export const RequestBadge = ({ request }) => (
    <IosBadge tone={REQUEST_TONES[request.status] || 'slate'}>{request.status_label}</IosBadge>
);

/** Выбранные к отправке файлы — строками с размером и крестиком. */
export const PickedFiles = ({ files, onRemove }) => {
    if (!files?.length) return null;
    return (
        <ul className="flex flex-wrap gap-1.5">
            {files.map((file, index) => (
                <li key={`${file.name}:${file.size}:${index}`}
                    className="flex max-w-full items-center gap-1.5 rounded-lg bg-slate-100 py-1 pl-2 pr-1 text-[12px] text-slate-600">
                    <Paperclip size={12} className="shrink-0 text-slate-400" aria-hidden="true" />
                    <span className="min-w-0 truncate">{file.name}</span>
                    <span className="shrink-0 tabular-nums text-slate-400">{fmtSize(file.size)}</span>
                    <button type="button" onClick={() => onRemove(index)} aria-label={`Убрать ${file.name}`}
                            className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-slate-400 hover:bg-slate-200 hover:text-slate-600">
                        <X size={12} />
                    </button>
                </li>
            ))}
        </ul>
    );
};

/** Поле ввода, которое растёт вместе с текстом — до maxRows строк. */
export const GrowingTextarea = ({ value, onChange, maxRows = 6, className = '', ...props }) => {
    const ref = useRef(null);
    useEffect(() => {
        const node = ref.current;
        if (!node) return;
        node.style.height = 'auto';
        const line = parseFloat(window.getComputedStyle(node).lineHeight) || 20;
        node.style.height = `${Math.min(node.scrollHeight, line * maxRows + 22)}px`;
    }, [value, maxRows]);
    return (
        <textarea ref={ref} rows={1} value={value} onChange={(event) => onChange(event.target.value)}
                  className={`resize-none ${className}`} {...props} />
    );
};
