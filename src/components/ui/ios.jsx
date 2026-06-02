import React from 'react';

/*
 * Общие iOS / macOS примитивы дизайн-системы.
 * Извлечено из SurveysView, чтобы переиспользовать в разделе «Отделы» и далее.
 * Аккуратно, корпоративно: палитра slate, ring-1 бордеры, мягкие тени,
 * backdrop-blur хедеры/футеры модалок, SF Pro.
 */

export const APPLE_FONT =
    '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, sans-serif';

// Заполненное поле в стиле iOS «grouped form».
export const iosInput =
    'w-full px-3.5 py-2.5 text-[14px] rounded-xl bg-slate-100 border-0 text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70 focus:bg-white transition';

export const iosCard =
    'rounded-2xl bg-white ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)]';

export const iosGroupLabel =
    'px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400';

export const iosBtnPrimary =
    'inline-flex items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-[13.5px] font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed';

export const iosBtnSecondary =
    'inline-flex items-center justify-center gap-2 rounded-xl bg-slate-100 px-4 py-2.5 text-[13.5px] font-semibold text-slate-600 transition-all hover:bg-slate-200 active:scale-[0.98] disabled:opacity-50';

export const iosBtnGhost =
    'inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-medium text-slate-500 transition-all hover:bg-slate-100 active:scale-[0.98]';

export const IosToggle = ({ checked, onChange, disabled = false }) => (
    <button
        type="button"
        role="switch"
        aria-checked={!!checked}
        disabled={disabled}
        onClick={() => { if (!disabled) onChange(!checked); }}
        className={`relative inline-flex h-[26px] w-[44px] shrink-0 items-center rounded-full transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
            checked ? 'bg-emerald-500' : 'bg-slate-300'
        } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
    >
        <span
            className={`inline-block h-[22px] w-[22px] transform rounded-full bg-white shadow-md transition-transform duration-200 ${
                checked ? 'translate-x-[20px]' : 'translate-x-[2px]'
            }`}
        />
    </button>
);

export const IosSection = ({ title, hint, children, right = null }) => (
    <section className="space-y-1.5">
        {(title || right) && (
            <div className="flex items-end justify-between gap-2">
                {title ? <div className={iosGroupLabel}>{title}</div> : <span />}
                {right}
            </div>
        )}
        <div className={`${iosCard} p-4 space-y-3`}>
            {children}
        </div>
        {hint && <div className="px-1 text-[11px] text-slate-400">{hint}</div>}
    </section>
);

const BADGE_TONES = {
    slate: 'bg-slate-100 text-slate-600',
    green: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
    red: 'bg-rose-50 text-rose-600 ring-1 ring-rose-100',
    blue: 'bg-blue-50 text-blue-700 ring-1 ring-blue-100',
    amber: 'bg-amber-50 text-amber-700 ring-1 ring-amber-100',
};

export const IosBadge = ({ tone = 'slate', children, className = '' }) => (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11.5px] font-medium ${BADGE_TONES[tone] || BADGE_TONES.slate} ${className}`}>
        {children}
    </span>
);

/**
 * iOS-модалка: затемнение + backdrop-blur, закруглённый контейнер,
 * липкие хедер и (опц.) футер с размытием.
 */
export const IosModal = ({ open, onClose, title, subtitle, children, footer = null, maxWidth = 'max-w-lg' }) => {
    if (!open) return null;
    return (
        <div
            className="fixed inset-0 z-[90] flex items-stretch justify-center bg-slate-900/40 backdrop-blur-md sm:items-center sm:p-6"
            style={{ fontFamily: APPLE_FONT }}
            onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.(); }}
        >
            <div className={`flex w-full ${maxWidth} flex-col overflow-hidden bg-slate-50 shadow-2xl ring-1 ring-slate-900/10 sm:max-h-[92vh] sm:rounded-3xl`}>
                <div className="relative flex items-center justify-between gap-3 border-b border-slate-200/70 bg-white/80 px-5 py-3.5 backdrop-blur-xl">
                    <div className="min-w-0">
                        <h3 className="truncate text-[15px] font-semibold text-slate-900">{title}</h3>
                        {subtitle && <p className="truncate text-[12px] text-slate-400">{subtitle}</p>}
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 active:scale-95"
                        aria-label="Закрыть"
                    >
                        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1 1l12 12M13 1L1 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg>
                    </button>
                </div>
                <div className="flex-1 overflow-y-auto px-5 py-4">
                    {children}
                </div>
                {footer && (
                    <div className="flex items-center justify-end gap-2 border-t border-slate-200/70 bg-white/80 px-5 py-3 backdrop-blur-xl">
                        {footer}
                    </div>
                )}
            </div>
        </div>
    );
};