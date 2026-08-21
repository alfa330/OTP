import React from 'react';
import { createPortal } from 'react-dom';

/*
 * Общие iOS / macOS примитивы дизайн-системы.
 * Извлечено из SurveysView, чтобы переиспользовать в разделе «Отделы» и далее.
 * Аккуратно, корпоративно: палитра slate, ring-1 бордеры, мягкие тени,
 * backdrop-blur хедеры/футеры модалок, SF Pro.
 */

export const APPLE_FONT =
    '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, sans-serif';

/*
 * Моторика оверлеев портала — единый набор для всех модалок раздела.
 *
 * Тайминги сняты с оригинальной вики (frontend/src/components/search-modal.tsx):
 * панель там въезжала за 0.12 с ease-out, выпадашка — за 0.15 с со сдвигом
 * y:10 и scale 0.99. Кривая 0.16/1/0.3/1 — та самая «.animate-scaleUp» из
 * globals.css оригинала, узаконенная там как кривая раскрытия модалки: быстрый
 * старт, мягкое приземление, характер macOS.
 *
 * Выход короче входа: закрытие не должно заставлять ждать.
 *
 * Затемнение и панель — РАЗНЫЕ слои. Если навесить прозрачность на общий
 * корень, opacity родителя перемножится с потомками и панель будет выцветать
 * вместе с фоном: получится не раскрытие, а общее проявление. В оригинале
 * backdrop тоже отдельный motion-слой.
 */
/* Именованные варианты, а не инлайновые объекты: только с именами
   onAnimationComplete получает строку и вход можно отличить от выхода
   (по завершении входа мы ставим фокус в поле поиска). */
export const IOS_MODAL_MOTION = {
    backdrop: {
        initial: 'hidden',
        animate: 'visible',
        exit: 'hidden',
        variants: {
            hidden: { opacity: 0, transition: { duration: 0.12, ease: 'easeIn' } },
            visible: { opacity: 1, transition: { duration: 0.18, ease: 'easeOut' } },
        },
    },
    panel: {
        initial: 'hidden',
        animate: 'visible',
        exit: 'hidden',
        variants: {
            hidden: {
                opacity: 0, scale: 0.98, y: 8,
                transition: { duration: 0.12, ease: 'easeIn' },
            },
            visible: {
                opacity: 1, scale: 1, y: 0,
                transition: { duration: 0.16, ease: [0.16, 1, 0.3, 1] },
            },
        },
    },
};

/* prefers-reduced-motion: CSS-переходы глушит правило в теме, но framer пишет
   инлайновые стили через rAF и под это правило не подпадает — нужен свой
   набор с нулевой длительностью. Имена вариантов те же, чтобы код компонента
   не разветвлялся. */
export const IOS_MODAL_MOTION_REDUCED = {
    backdrop: {
        initial: 'hidden',
        animate: 'visible',
        exit: 'hidden',
        variants: {
            hidden: { opacity: 0, transition: { duration: 0 } },
            visible: { opacity: 1, transition: { duration: 0 } },
        },
    },
    panel: {
        initial: 'hidden',
        animate: 'visible',
        exit: 'hidden',
        variants: {
            hidden: { opacity: 0, transition: { duration: 0 } },
            visible: { opacity: 1, transition: { duration: 0 } },
        },
    },
};

// Заполненное поле в стиле iOS «grouped form».
export const iosInput =
    'w-full px-3.5 py-2.5 text-[14px] rounded-xl bg-slate-100 border-0 text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70 focus:bg-white transition';

export const iosCard =
    'rounded-2xl bg-white ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)]';

export const iosGroupLabel =
    'px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500';

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
        {hint && <div className="px-1 text-[11px] text-slate-500">{hint}</div>}
    </section>
);

/**
 * Подсказка «i»: пояснение, которое нужно один раз, а место занимает всегда.
 *
 * Открывается и по наведению, и по клику — не для симметрии: на телефоне
 * наведения не существует вовсе, и подсказка, живущая только на hover, там
 * недоступна. Закрывается по уходу мыши, повторному клику и Escape.
 *
 * Тёмный пузырёк на светлой карточке выбран намеренно: белая подсказка поверх
 * белой карточки читается как часть содержимого, а не как всплывающее пояснение.
 */
export const IosHint = ({ text, label = 'Подробнее', align = 'left' }) => {
    const [open, setOpen] = React.useState(false);
    return (
        <span
            className="relative inline-flex"
            onMouseEnter={() => setOpen(true)}
            onMouseLeave={() => setOpen(false)}
        >
            <button
                type="button"
                aria-label={label}
                aria-expanded={open}
                onClick={(event) => { event.preventDefault(); setOpen((v) => !v); }}
                onKeyDown={(event) => { if (event.key === 'Escape') setOpen(false); }}
                className="grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full bg-slate-200/70 text-[11px] font-semibold leading-none text-slate-500 transition hover:bg-slate-300/80 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60"
            >
                i
            </button>
            {open && (
                <span
                    role="tooltip"
                    className={`absolute top-[24px] z-30 w-64 rounded-xl bg-slate-900/95 px-3 py-2 text-[11.5px] font-normal leading-snug text-white shadow-lg backdrop-blur ${
                        align === 'right' ? 'right-0' : 'left-0'
                    }`}
                >
                    {text}
                </span>
            )}
        </span>
    );
};

/**
 * Сегментный контрол iOS: выбор одного из нескольких режимов.
 *
 * size='lg' — когда контрол работает панелью вкладок и должен читаться как
 * навигация, а не как мелкая настройка рядом с содержимым. Именно на этом
 * спотыкались в «Опросах»: вкладки были размером с подпись, и человек не
 * понимал, что по ним надо нажимать.
 *
 * options: [{ value, label, icon?, count? }]
 */
export const IosSegmented = ({ value, options = [], onChange, size = 'md', stretch = false, className = '', ariaLabel }) => {
    const large = size === 'lg';
    const wide = stretch || large;
    return (
        <div
            role="tablist"
            aria-label={ariaLabel}
            className={`${wide ? 'flex w-full' : 'inline-flex'} ${large ? 'gap-1 rounded-[12px] p-[4px]' : 'rounded-[10px] p-[3px]'} bg-slate-100 ${className}`}
        >
            {options.filter(Boolean).map((option) => {
                const active = option.value === value;
                return (
                    <button
                        key={option.value}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        onClick={() => onChange?.(option.value)}
                        className={`relative inline-flex items-center justify-center gap-1.5 whitespace-nowrap transition-all active:scale-[0.98] ${
                            wide ? 'flex-1' : ''
                        } ${
                            large
                                ? 'rounded-[9px] px-3.5 py-2 text-[13.5px] font-semibold'
                                : 'rounded-[8px] px-3 py-[5px] text-[12.5px] font-medium'
                        } ${
                            active
                                ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.10)]'
                                : 'text-slate-500 hover:text-slate-800'
                        }`}
                    >
                        {option.icon}
                        <span>{option.label}</span>
                        {Number.isFinite(option.count) && option.count > 0 && (
                            <span className={`text-[11.5px] tabular-nums ${active ? 'text-slate-400' : 'text-slate-400'}`}>
                                {option.count}
                            </span>
                        )}
                    </button>
                );
            })}
        </div>
    );
};

const BADGE_TONES = {
    slate: 'bg-slate-100 text-slate-600',
    green: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
    red: 'bg-rose-50 text-rose-600 ring-1 ring-rose-100',
    blue: 'bg-blue-50 text-blue-700 ring-1 ring-blue-100',
    amber: 'bg-amber-50 text-amber-700 ring-1 ring-amber-100',
};

/** Балл 0..100 → тон бейджа: один и тот же порог в очереди и в карточке ревью. */
export const scoreTone = (value) => (
    value == null ? 'slate' : value >= 70 ? 'green' : value >= 50 ? 'amber' : 'red');

export const IosBadge = ({ tone = 'slate', children, className = '', ...props }) => (
    <span {...props} className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11.5px] font-medium ${BADGE_TONES[tone] || BADGE_TONES.slate} ${className}`}>
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
                <div className="relative flex items-center justify-between gap-3 border-b border-slate-200/70 bg-white/80 px-4 py-3 backdrop-blur-xl sm:px-5 sm:py-3.5">
                    <div className="min-w-0">
                        <h3 className="truncate text-[15px] font-semibold text-slate-900">{title}</h3>
                        {subtitle && <p className="truncate text-[12px] text-slate-500">{subtitle}</p>}
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
                <div className="flex-1 overflow-y-auto overscroll-contain px-4 py-4 sm:px-5">
                    {children}
                </div>
                {/* flex-wrap в подвале: там бывает не только «Отмена/Сохранить»,
                    но и ряд действий над записью — на телефоне он не влезал в
                    строку и выдавливал главную кнопку за край. */}
                {footer && (
                    <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-200/70 bg-white/80 px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur-xl sm:px-5">
                        {footer}
                    </div>
                )}
            </div>
        </div>
    );
};


/**
 * Меню действий за «тремя точками» — как в macOS/iOS.
 *
 * Зачем оно вместо ряда иконок: в строке списка четыре круглые кнопки читаются
 * как украшение, а не как действия. Что делает каждая — понятно только по
 * наведению, на телефоне наведения нет вовсе, и мишени 32×32 стоят вплотную,
 * так что «в архив» ловится вместо «изменить». Одна точка входа и подписанные
 * пункты снимают всё это разом.
 *
 * Меню рендерится в ПОРТАЛ с fixed-позицией: строки списков лежат в карточке
 * с overflow-hidden, и вложенное меню она бы обрезала. Механика позиционирования
 * повторяет CustomSelect — общий приём раздела, а не второй способ делать то же.
 *
 * items: [{ key, label, icon, onSelect, danger?, hint?, separatorBefore? }]
 */
export const IosMenu = ({ items = [], label = 'Действия', align = 'right', disabled = false }) => {
    const [open, setOpen] = React.useState(false);
    const [coords, setCoords] = React.useState(null);
    const btnRef = React.useRef(null);
    const popRef = React.useRef(null);

    // Пункты бывают условными (`cond && {...}`), и когда доступных не осталось,
    // кнопка не должна оставаться пустой заглушкой — её просто нет.
    const shown = items.filter(Boolean);

    const recompute = React.useCallback(() => {
        const el = btnRef.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        const width = 232;
        // Высота меню известна заранее: пункты одинаковые. Считать по факту
        // нельзя — на первом кадре меню ещё не отрисовано.
        const height = shown.length * 40 + 12;
        const spaceBelow = window.innerHeight - r.bottom;
        const openUp = spaceBelow < height + 16 && r.top > spaceBelow;
        setCoords({
            width,
            left: align === 'right'
                ? Math.max(8, Math.round(r.right - width))
                : Math.min(window.innerWidth - width - 8, Math.round(r.left)),
            top: openUp ? undefined : Math.round(r.bottom + 6),
            bottom: openUp ? Math.round(window.innerHeight - r.top + 6) : undefined,
        });
    }, [align, shown.length]);

    React.useLayoutEffect(() => { if (open) recompute(); }, [open, recompute]);

    React.useEffect(() => {
        if (!open) return undefined;
        const onDoc = (e) => {
            if (btnRef.current?.contains(e.target) || popRef.current?.contains(e.target)) return;
            setOpen(false);
        };
        const onKey = (e) => {
            if (e.key !== 'Escape') return;
            setOpen(false);
            requestAnimationFrame(() => btnRef.current?.focus());
        };
        // Прокрутка закрывает, а не тащит меню за собой: строка списка уезжает
        // из-под пальца, и «приклеенное» меню оказалось бы над чужой строкой.
        // Обработчик именованный: снять слушатель можно только по той же ссылке,
        // а стрелка в removeEventListener создала бы новую и слушатель остался бы.
        const onScroll = () => setOpen(false);
        document.addEventListener('mousedown', onDoc);
        document.addEventListener('keydown', onKey);
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', recompute);
        return () => {
            document.removeEventListener('mousedown', onDoc);
            document.removeEventListener('keydown', onKey);
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', recompute);
        };
    }, [open, recompute]);

    if (!shown.length) return null;

    return (
        <>
            <button
                ref={btnRef}
                type="button"
                disabled={disabled}
                aria-label={label}
                aria-haspopup="menu"
                aria-expanded={open}
                onClick={() => setOpen((v) => !v)}
                className={`grid h-8 w-8 shrink-0 place-items-center rounded-full transition active:scale-95 disabled:opacity-40 ${
                    open ? 'bg-slate-200 text-slate-700' : 'text-slate-400 hover:bg-slate-100 hover:text-slate-700'
                }`}
            >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                    <circle cx="3" cy="8" r="1.5" /><circle cx="8" cy="8" r="1.5" /><circle cx="13" cy="8" r="1.5" />
                </svg>
            </button>

            {open && coords && createPortal(
                <div
                    ref={popRef}
                    role="menu"
                    style={{
                        position: 'fixed',
                        left: coords.left,
                        width: coords.width,
                        top: coords.top,
                        bottom: coords.bottom,
                        zIndex: 99999,
                        fontFamily: APPLE_FONT,
                    }}
                    className="overflow-hidden rounded-2xl bg-white/95 p-1.5 shadow-[0_14px_40px_rgba(15,23,42,0.18)] ring-1 ring-slate-200/80 backdrop-blur-xl animate-[fadeIn_.12s_ease]"
                >
                    {shown.map(({ key, label: text, icon: Icon, onSelect, danger, hint, separatorBefore }) => (
                        <React.Fragment key={key}>
                            {separatorBefore && <div className="my-1.5 h-px bg-slate-200/70" />}
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => { setOpen(false); onSelect?.(); }}
                                className={`flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-[13.5px] transition ${
                                    danger
                                        ? 'text-rose-600 hover:bg-rose-50'
                                        : 'text-slate-800 hover:bg-slate-100'
                                }`}
                            >
                                {Icon && <Icon size={15} className={danger ? 'text-rose-500' : 'text-slate-400'} />}
                                <span className="min-w-0 flex-1 truncate">{text}</span>
                                {hint && <span className="shrink-0 text-[11.5px] text-slate-400">{hint}</span>}
                            </button>
                        </React.Fragment>
                    ))}
                </div>,
                document.body,
            )}
        </>
    );
};
