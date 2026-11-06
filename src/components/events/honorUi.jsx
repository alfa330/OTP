import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
    Award, BadgeCheck, Car, Check, Crown, Flame, Gem, Headphones, HeartHandshake, Medal, MessageCircleHeart, Rocket,
    Sparkles, Star, Target, TrendingUp, Trophy, Zap,
} from 'lucide-react';
import InfoHint from '../common/InfoHint';
import { HONOR_ICONS, initialsOf } from './honorBoardMeta';

/*
 * Общие кусочки «Доски почёта»: плитка победителя, главная карточка, бейдж
 * номинации, фото и выбор значка. Ими рисуют и доска, и окно тренера —
 * карточка в окне выглядит так же, как её увидят.
 *
 * Плитка — по образцу, который прислали к доработке (цветные карточки с людьми
 * без фона): пастельная заливка, имя и номинация сверху, человек стоит на
 * нижнем крае. Цвета — утилиты Tailwind: тёмный слой портала перекрашивает их
 * сам (theme-dark.css).
 */

const ICONS = {
    Award, BadgeCheck, Car, Crown, Flame, Gem, Headphones, HeartHandshake, Medal, MessageCircleHeart, Rocket,
    Sparkles, Star, Target, TrendingUp, Trophy, Zap,
};

/* Подписи значков — для читалки и подсказки при наведении в выборе значка. */
const ICON_LABELS = {
    Award: 'Награда', Star: 'Звезда', Trophy: 'Кубок', Medal: 'Медаль', Rocket: 'Ракета', Target: 'Цель',
    Zap: 'Молния', TrendingUp: 'Рост', Flame: 'Огонь', Gem: 'Бриллиант', Sparkles: 'Искры',
    HeartHandshake: 'Забота', MessageCircleHeart: 'Чат', Headphones: 'Звонки', BadgeCheck: 'Галочка', Car: 'Машина',
};

export const NominationIcon = ({ name, ...props }) => {
    const Icon = ICONS[name] || Award;
    return <Icon aria-hidden="true" {...props} />;
};

/* Кружок с фото из профиля. Кружок — сетка, а у картинки свой размер в
   пикселях: высокий снимок в `h-full` сетки вставал бы вытянутым и держался
   одной обрезкой. Не загрузилось — инициалы; запоминаем саму негодную ссылку:
   придёт свежая (подпись живёт сутки) — фото вернётся. */
export const HonorPhoto = ({ name, url, size = 88 }) => {
    const [brokenUrl, setBrokenUrl] = useState('');
    const photo = Boolean(url) && url !== brokenUrl;
    return (
        <span
            data-honor-photo={photo ? 'photo' : 'initials'}
            className="inline-grid shrink-0 place-items-center overflow-hidden rounded-full bg-slate-100 font-semibold text-slate-500"
            style={{ width: size, height: size, fontSize: Math.max(11, Math.round(size * 0.34)) }}
        >
            {photo ? (
                <img
                    src={url}
                    alt=""
                    className="block object-cover"
                    style={{ width: size, height: size }}
                    loading="lazy"
                    decoding="async"
                    draggable={false}
                    onError={() => setBrokenUrl(url)}
                />
            ) : initialsOf(name)}
        </span>
    );
};

/* Фото в своей области цветной плитки (photo — из entryPhoto в honorBoardMeta.js):
     cutout   — человек без фона стоит на нижнем крае, ростом figure от области;
     photo    — фото с фоном рамкой, обрезка от верха (голова цела);
     avatar   — фото профиля кружком в белом ободке;
     initials — инициалы в таком же кружке.
   Область — родитель с position: relative; подпись плитки стоит НАД ней, а не
   поверх: имя или номинация в две-три строки не наезжают на голову. */
export const HonorPortrait = ({ photo, name, ink = 'text-slate-500', figure = 0.94, circle = 148, inset = 16 }) => {
    const [brokenUrl, setBrokenUrl] = useState('');
    const kind = photo?.url && photo.url === brokenUrl ? 'initials' : (photo?.kind || 'initials');
    const onError = () => setBrokenUrl(photo?.url || '');
    if (kind === 'cutout') {
        return (
            <img
                src={photo.url}
                alt=""
                data-honor-figure="cutout"
                className="pointer-events-none absolute bottom-0 left-1/2 max-w-[94%] -translate-x-1/2 select-none object-contain object-bottom"
                style={{ height: `${Math.round(figure * 100)}%`, filter: 'drop-shadow(0 18px 24px rgba(15, 23, 42, 0.16))' }}
                loading="lazy"
                decoding="async"
                draggable={false}
                onError={onError}
            />
        );
    }
    if (kind === 'photo') {
        /* Рамка в пропорциях самого снимка, ростом во всю область и по центру:
           широкая плитка не растягивает портрет в полосу (от лица оставалась
           макушка и небо). Снимок шире области — ширина упирается в область,
           и обрезка идёт по бокам, а не по лицу. */
        return (
            <span data-honor-figure="photo" className="absolute inset-x-0 bottom-0 top-1 flex justify-center">
                <span
                    className="block h-full overflow-hidden rounded-t-[22px]"
                    style={{ aspectRatio: String(photo.ratio > 0 ? photo.ratio : 4 / 5), maxWidth: `calc(100% - ${inset * 2}px)` }}
                >
                    <img
                        src={photo.url}
                        alt=""
                        className="h-full w-full select-none object-cover object-[50%_20%]"
                        loading="lazy"
                        decoding="async"
                        draggable={false}
                        onError={onError}
                    />
                </span>
            </span>
        );
    }
    return (
        <span data-honor-figure={kind} className="absolute inset-0 grid place-items-center">
            <span className="rounded-full bg-white/80 p-1.5 shadow-[0_14px_30px_-18px_rgba(15,23,42,0.45)]">
                {kind === 'avatar' ? (
                    <img
                        src={photo.url}
                        alt=""
                        className="block select-none rounded-full object-cover"
                        style={{ width: circle, height: circle }}
                        loading="lazy"
                        decoding="async"
                        draggable={false}
                        onError={onError}
                    />
                ) : (
                    <span
                        className={`grid place-items-center rounded-full bg-white font-semibold ${ink}`}
                        style={{ width: circle, height: circle, fontSize: Math.round(circle * 0.3) }}
                    >
                        {initialsOf(name)}
                    </span>
                )}
            </span>
        </span>
    );
};

/* Бейдж номинации — «название с тематической иконкой» (ТЗ, п. 3). У ключевой
   номинации — золото и корона. Название своей номинации бывает длинным (до
   60 знаков) — оно переносится, а не обрезается многоточием: прочитать его
   больше негде. За что дают номинацию — под «i» рядом, общим InfoHint:
   подсказка браузера по наведению на телефоне не открывается вовсе. */
export const NominationBadge = ({ meta, className = '' }) => (
    <span className={`inline-flex max-w-full items-center gap-1.5 ${className}`}>
        <span
            className={`inline-flex min-w-0 items-start gap-1.5 rounded-[14px] px-2.5 py-1 text-[13px] font-semibold leading-snug ${
                meta.main ? 'bg-amber-100 text-amber-800 ring-1 ring-amber-300' : 'bg-blue-50 text-blue-700'
            }`}
        >
            <NominationIcon name={meta.icon} size={15} strokeWidth={2.2} className="mt-[2px] shrink-0" />
            <span className="min-w-0 break-words">{meta.title}</span>
        </span>
        {meta.reason && <InfoHint text={`За что: ${meta.reason}`} side="left" />}
    </span>
);

/* Плитка победителя (не ключевая номинация): цветная плитка — сверху подпись
   (имя, номинация, значок), под ней область фото; ниже плитки — описание заслуг.
   height — рост цветной плитки в px.

   Имя — <p role="heading">, а не <h3>: мобильная оболочка (mobile-shell.css)
   ставит всем h3 в разделах 16 px, и имя «Лучшего сотрудника» вышло бы мельче
   его же стиха. */
export const HonorTile = ({ entry, meta, tone, photo, height = 320, compact = false, dense = false, className = '', style }) => {
    // compact — превью в окне тренера (без описания); dense — узкая плитка доски
    // (две в ряд на телефоне): та же плитка с подписью и описанием помельче.
    const small = compact || dense;
    return (
        <article
            data-honor-card=""
            className={`flex min-w-0 flex-col overflow-hidden bg-white shadow-[0_18px_44px_-30px_rgba(15,23,42,0.35)] ring-1 ring-slate-200/70 ${
                small ? 'rounded-[22px]' : 'rounded-[28px]'} ${className}`}
            style={style}
        >
            <div className={`flex flex-col overflow-hidden ${tone.tile}`} style={{ height }}>
                {/* grow basis-0, а не flex-1: мобильная оболочка ставит в столбик
                    ряды items-start с flex-1 (mobile-shell.css) — значок уехал бы
                    под название. */}
                <div className={`flex shrink-0 items-start ${small ? 'gap-2 p-3 pb-1' : 'gap-3 p-5 pb-2'}`}>
                    <div className="min-w-0 grow basis-0">
                        <p
                            role="heading"
                            aria-level={3}
                            className={`break-words font-semibold leading-tight text-slate-900 ${small ? 'text-[15px]' : 'text-[19px]'}`}
                        >
                            {entry.name || 'Имя и фамилия'}
                        </p>
                        <p className={`mt-1 break-words font-medium leading-snug ${tone.ink} ${small ? 'text-[12px]' : 'text-[13.5px]'}`}>
                            {meta.title}
                        </p>
                    </div>
                    <span
                        className={`grid shrink-0 place-items-center rounded-full bg-white/85 shadow-sm ${tone.ink} ${small ? 'h-8 w-8' : 'h-10 w-10'}`}
                        title={meta.reason ? `За что: ${meta.reason}` : undefined}
                    >
                        <NominationIcon name={meta.icon} size={small ? 15 : 18} strokeWidth={2.1} />
                    </span>
                </div>
                <div data-honor-photo-area="" className="relative min-h-0 grow basis-0">
                    <HonorPortrait photo={photo} name={entry.name} ink={tone.ink} figure={0.97} circle={small ? 88 : 148}
                        inset={small ? 8 : 16} />
                </div>
            </div>
            {!compact && entry.description && (
                <p className={`whitespace-pre-line break-words leading-relaxed text-slate-600 ${
                    dense ? 'px-3 pb-3.5 pt-3 text-[13px]' : 'px-5 pb-5 pt-4 text-[14.5px]'}`}
                >
                    {entry.description}
                </p>
            )}
        </article>
    );
};

/* Золотая колонка главной карточки с фото: на доске и в превью окна тренера. */
export const HonorMainPhoto = ({ entry, tone, photo, className = '', style }) => (
    <div className={`relative overflow-hidden ${tone.tile} ${className}`} style={style}>
        <HonorPortrait photo={photo} name={entry.name} ink={tone.ink} figure={0.9} circle={196} />
    </div>
);

/* Ключевая номинация — «Лучший сотрудник отдела»: во всю ширину доски, в
   золотой рамке со свечением и с короной на бейдже (ТЗ, п. 3). Фото — слева
   золотой колонкой шириной в одну плитку ряда под ней и того же роста
   (gridStyle и height — из tileLayout), текст — справа; узкая доска — одно
   под другим. */
export const HonorMainCard = ({ entry, meta, tone, photo, gridStyle, height = 320, className = '', style }) => (
    <article
        data-honor-card="main"
        className={`overflow-hidden rounded-[32px] bg-amber-50 ring-2 ring-amber-300 shadow-[0_30px_70px_-38px_rgba(217,119,6,0.75)] ${className}`}
        style={style}
    >
        <div className="grid h-full" style={gridStyle}>
            <HonorMainPhoto entry={entry} tone={tone} photo={photo} style={{ minHeight: height }} />
            <div className="flex min-w-0 flex-col justify-center gap-4 px-6 py-7 sm:px-10 sm:py-10">
                <NominationBadge meta={meta} className="self-start" />
                <p
                    role="heading"
                    aria-level={3}
                    className="break-words text-[28px] font-semibold leading-tight tracking-tight text-slate-900 sm:text-[32px]"
                >
                    {entry.name}
                </p>
                {entry.description && (
                    <p className="whitespace-pre-line break-words text-[16px] leading-relaxed text-slate-700 sm:text-[17px]">
                        {entry.description}
                    </p>
                )}
            </div>
        </div>
    </article>
);

/* Выбор значка своей номинации: кнопка со значком открывает сетку 4 × 4.
   Сетка — порталом в body (окно тренера режет всё, что выходит за его край),
   закрывается щелчком мимо, Escape и прокруткой — как IosMenu. */
const PICKER_WIDTH = 4 * 44 + 3 * 6 + 2 * 10;

export const IconPicker = ({ value, onChange, tone, disabled = false }) => {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState(null);
    const buttonRef = useRef(null);
    const popRef = useRef(null);

    const place = useCallback(() => {
        const rect = buttonRef.current?.getBoundingClientRect();
        if (!rect) return;
        const height = 4 * 44 + 3 * 6 + 2 * 10;
        const below = window.innerHeight - rect.bottom;
        const up = below < height + 12 && rect.top > below;
        setCoords({
            left: Math.max(8, Math.min(window.innerWidth - PICKER_WIDTH - 8, Math.round(rect.left))),
            top: up ? undefined : Math.round(rect.bottom + 6),
            bottom: up ? Math.round(window.innerHeight - rect.top + 6) : undefined,
        });
    }, []);

    useLayoutEffect(() => { if (open) place(); }, [open, place]);

    /* Открыли — фокус на выбранный значок: с клавиатуры сетка проходится
       стрелками, а не теряется за последним элементом страницы. */
    useEffect(() => {
        if (!open || !coords) return;
        const target = popRef.current?.querySelector('[aria-selected="true"]') || popRef.current?.querySelector('button');
        target?.focus({ preventScroll: true });
    }, [open, coords]);

    const close = useCallback((returnFocus = true) => {
        setOpen(false);
        if (returnFocus) buttonRef.current?.focus();
    }, []);

    const onGridKeyDown = (event) => {
        const buttons = [...(popRef.current?.querySelectorAll('button') || [])];
        const index = buttons.indexOf(document.activeElement);
        const steps = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 4, ArrowUp: -4 };
        let next = null;
        if (event.key in steps) next = Math.min(buttons.length - 1, Math.max(0, (index < 0 ? 0 : index) + steps[event.key]));
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = buttons.length - 1;
        else if (event.key === 'Tab') {
            // Tab из сетки — закрыть её и вернуться к кнопке значка, а не уйти
            // по странице с открытой сеткой.
            event.preventDefault();
            close();
            return;
        }
        if (next === null) return;
        event.preventDefault();
        buttons[next]?.focus();
    };

    useEffect(() => {
        if (!open) return undefined;
        const onDown = (event) => {
            if (buttonRef.current?.contains(event.target) || popRef.current?.contains(event.target)) return;
            setOpen(false);
        };
        const onKey = (event) => {
            if (event.key !== 'Escape') return;
            event.stopPropagation();
            close();
        };
        const onScroll = (event) => {
            if (popRef.current?.contains(event.target)) return;
            setOpen(false);
        };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('touchstart', onDown, { passive: true });
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', place);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('touchstart', onDown);
            document.removeEventListener('keydown', onKey, true);
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', place);
        };
    }, [close, open, place]);

    return (
        <>
            <button
                ref={buttonRef}
                type="button"
                onClick={() => setOpen((prev) => !prev)}
                disabled={disabled}
                aria-haspopup="true"
                aria-expanded={open}
                aria-label={`Значок номинации: ${ICON_LABELS[value] || 'Награда'}`}
                title="Сменить значок"
                className={`grid h-[42px] w-[42px] shrink-0 place-items-center rounded-xl ${tone.tile} ${tone.ink} transition hover:brightness-95 active:scale-95 disabled:opacity-50`}
            >
                <NominationIcon name={value} size={19} strokeWidth={2.1} />
            </button>
            {open && coords && createPortal(
                <div
                    ref={popRef}
                    role="listbox"
                    onKeyDown={onGridKeyDown}
                    aria-label="Значок номинации"
                    className="fixed z-[4000] grid grid-cols-4 gap-1.5 rounded-2xl bg-white p-2.5 shadow-[0_18px_50px_-12px_rgba(15,23,42,0.35)] ring-1 ring-slate-200/80 motion-safe:animate-reveal"
                    style={{ left: coords.left, top: coords.top, bottom: coords.bottom, width: PICKER_WIDTH }}
                >
                    {HONOR_ICONS.map((name) => {
                        const selected = name === value;
                        return (
                            <button
                                key={name}
                                type="button"
                                role="option"
                                aria-selected={selected}
                                aria-label={ICON_LABELS[name] || name}
                                title={ICON_LABELS[name] || name}
                                tabIndex={selected ? 0 : -1}
                                onClick={() => {
                                    onChange(name);
                                    close();
                                }}
                                className={`relative grid h-11 w-11 place-items-center rounded-xl transition active:scale-95 ${
                                    selected ? 'bg-blue-50 text-blue-700 ring-1 ring-blue-200' : 'text-slate-600 hover:bg-slate-100'
                                }`}
                            >
                                <NominationIcon name={name} size={19} strokeWidth={2.1} />
                                {selected && (
                                    <Check size={11} strokeWidth={3} className="absolute bottom-1 right-1 text-blue-600" aria-hidden="true" />
                                )}
                            </button>
                        );
                    })}
                </div>,
                document.body,
            )}
        </>
    );
};
