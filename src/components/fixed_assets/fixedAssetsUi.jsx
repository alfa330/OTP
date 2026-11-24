import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    ArrowRightLeft, Camera, Check, ChevronDown, FileText, FileUp, Loader2, Package, PackagePlus, PackageX, Plus, ScanLine,
    Search, ShoppingCart, UserMinus, X,
} from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosLightbox, IosMenu, IosSegmented } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import { PHOTO_ACCEPT, preparePhoto } from '../parcels/parcelPhoto';
import DocumentScanner from '../discipline/DocumentScanner';
import { scanFormats } from '../discipline/docScan';
import {
    KIND_LABELS, assetFlag, fileSizeLabel, fmtDay, initials, placeText, requestSubject, routeText, shortName,
} from './fixedAssetsMeta';

/*
 * Детали интерфейса раздела «Основные средства».
 *
 * Вид — тот, что владелец выбрал для «Задач» 16–17.11.2026 (и по которому 17.11 попросил
 * переделать этот раздел: «много монотонного текста… надо, чтобы было аккуратно»):
 *   • белые плитки в один ряд, цвет — только у кружка со значком;
 *   • записи — карточками в сетке, а не строками таблицы;
 *   • статус — словом в своём цвете и кружком, без капсул;
 *   • сведения — строками «подпись — значение» в белых группах, как в «Настройках» iOS;
 *   • пояснений на экране нет: что делать, говорит главная кнопка.
 * Цвета — утилитами Tailwind из общей палитры: по ним собирается тёмный слой портала.
 */

/* Белая поверхность: карточка, плитка, группа строк. */
export const SURFACE = 'rounded-2xl bg-white shadow-[0_1px_2px_rgba(15,23,42,0.05),0_0_0_1px_rgba(15,23,42,0.04)]';
const SURFACE_HOVER = 'hover:shadow-[0_8px_20px_rgba(15,23,42,0.09),0_0_0_1px_rgba(15,23,42,0.05)]';

/* Кнопки шапки раздела — капсулы, как «Новая задача». */
export const CAPSULE_PRIMARY = 'inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-full bg-blue-600 px-[18px] text-[14px] font-semibold text-white shadow-[0_1px_2px_rgba(37,99,235,0.25),0_4px_12px_rgba(37,99,235,0.18)] transition hover:bg-blue-700 active:scale-[0.98] disabled:opacity-50';
export const CAPSULE = 'inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-full bg-white px-[18px] text-[14px] font-semibold text-slate-700 shadow-[0_0_0_1px_rgba(15,23,42,0.08),0_1px_2px_rgba(15,23,42,0.06)] transition hover:bg-slate-50 active:scale-[0.98] disabled:opacity-50';
/* Тихая кнопка в панели над списком («Excel»). */
export const FILL_BTN = 'inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-xl bg-slate-100 px-3.5 text-[14px] font-semibold text-slate-700 transition hover:bg-slate-200 active:scale-[0.98] disabled:opacity-50';

/* ── Тона ──────────────────────────────────────────────────────────────────── */
const TONE = {
    blue: { fill: 'bg-blue-500', word: 'text-blue-700', soft: 'bg-blue-50 text-blue-600', ring: 'border-blue-500' },
    amber: { fill: 'bg-amber-500', word: 'text-amber-700', soft: 'bg-amber-50 text-amber-600', ring: 'border-amber-500' },
    green: { fill: 'bg-emerald-500', word: 'text-emerald-700', soft: 'bg-emerald-50 text-emerald-600', ring: 'border-emerald-500' },
    rose: { fill: 'bg-rose-500', word: 'text-rose-700', soft: 'bg-rose-50 text-rose-600', ring: 'border-rose-500' },
    violet: { fill: 'bg-violet-500', word: 'text-violet-700', soft: 'bg-violet-50 text-violet-600', ring: 'border-violet-500' },
    slate: { fill: 'bg-slate-400', word: 'text-slate-500', soft: 'bg-slate-100 text-slate-500', ring: 'border-slate-300' },
    muted: { fill: 'bg-slate-300', word: 'text-slate-400', soft: 'bg-slate-100 text-slate-400', ring: 'border-slate-300' },
};
export const toneOf = (name) => TONE[name] || TONE.slate;

/* Значок в цветном кружке — единственное цветное пятно плитки. soft — мягкий вариант для
   лент, где кружков десятки. */
export const IconCircle = ({ icon: Icon, tone = 'slate', size = 34, soft = false, className = '' }) => (
    <span
        aria-hidden="true"
        className={`grid shrink-0 place-items-center rounded-full ${soft ? toneOf(tone).soft : `${toneOf(tone).fill} text-white`} ${className}`}
        style={{ width: size, height: size }}
    >
        {Icon && <Icon size={Math.round(size * 0.5)} strokeWidth={2.1} />}
    </span>
);

/* Человек кружком с инициалами: у ОС всегда есть «за кем». */
export const Avatar = ({ name, size = 28, className = '' }) => (
    <span
        aria-hidden="true"
        className={`grid shrink-0 place-items-center rounded-full bg-slate-100 font-semibold text-slate-500 ${className}`}
        style={{ width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.38)) }}
    >
        {initials(name) || '—'}
    </span>
);

/* ── Плитки над списком: число и отбор сразу ──────────────────────────────────
   На компьютере — один ряд на всю ширину; на телефоне ряд листается вбок. Выбранная плитка —
   мягким голубым, как выбор везде на сайте.

   Раскладка задана стилем, а не классами: оболочка телефона (mobile-shell.css) переносит
   flex-ряды с gap и схлопывает сетки grid-cols-[…], а здесь нужна ровно одна строка. */
const TILES_STRIP = { gridAutoFlow: 'column', gridAutoColumns: 'max-content' };
export const StatTiles = ({ items = [], value, onChange, ariaLabel }) => {
    const phone = useIsMobileShell();
    const stripRef = useRef(null);
    useEffect(() => {
        const strip = stripRef.current;
        const active = strip?.querySelector('[aria-pressed="true"]');
        if (!strip || !active || strip.scrollWidth <= strip.clientWidth) return;
        const box = strip.getBoundingClientRect();
        const tile = active.getBoundingClientRect();
        if (tile.left < box.left || tile.right > box.right) strip.scrollLeft += (tile.left + tile.width / 2) - (box.left + box.width / 2);
    }, [value, items.length, phone]);
    return (
        <div
            ref={stripRef}
            role="group"
            aria-label={ariaLabel}
            className={phone ? 'grid gap-2.5 overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden' : 'grid gap-3'}
            style={phone ? TILES_STRIP : { gridTemplateColumns: `repeat(${Math.max(1, items.length)}, minmax(0, 1fr))` }}
        >
            {items.map((item) => {
                const active = item.key === value;
                return (
                    <button
                        key={item.key || 'all'}
                        type="button"
                        aria-pressed={active}
                        onClick={() => onChange?.(item.key)}
                        className={`flex min-h-[66px] min-w-0 items-center gap-3 rounded-[18px] py-3 pl-3.5 pr-4 text-left transition active:scale-[0.99] ${active
                            ? 'bg-blue-50 ring-1 ring-inset ring-blue-200'
                            : 'bg-white shadow-[0_1px_2px_rgba(15,23,42,0.05),0_0_0_1px_rgba(15,23,42,0.04)] hover:bg-slate-50'}`}
                    >
                        <IconCircle icon={item.icon} tone={item.tone} />
                        <span className={`min-w-0 flex-1 text-[14px] font-semibold leading-tight ${phone ? 'whitespace-nowrap pr-2' : ''} ${active ? 'text-blue-700' : 'text-slate-600'}`}>{item.label}</span>
                        <span className="shrink-0 text-[26px] font-bold leading-none tabular-nums text-slate-900">{item.count ?? '—'}</span>
                    </button>
                );
            })}
        </div>
    );
};

/* ── Статус: кружок, как отметка в «Напоминаниях», и слово в своём цвете ─────── */
const MARK_DOT = { amber: 'bg-amber-500', blue: 'bg-blue-500', violet: 'bg-violet-500' };
export const StatusMark = ({ tone = 'slate', size = 20 }) => {
    const filled = tone === 'green' || tone === 'rose';
    return (
        <span
            aria-hidden="true"
            className={`grid shrink-0 place-items-center rounded-full border-2 text-white ${toneOf(tone).ring} ${filled ? toneOf(tone).fill : ''}`}
            style={{ width: size, height: size }}
        >
            {tone === 'green' && <Check size={Math.round(size * 0.6)} strokeWidth={3.2} />}
            {tone === 'rose' && <X size={Math.round(size * 0.6)} strokeWidth={3.2} />}
            {MARK_DOT[tone] && <span className={`rounded-full ${MARK_DOT[tone]}`} style={{ width: Math.round(size * 0.4), height: Math.round(size * 0.4) }} />}
        </span>
    );
};

export const StatusWord = ({ tone = 'slate', children, className = '' }) => (
    <span className={`text-[13px] font-semibold ${toneOf(tone).word} ${className}`}>{children}</span>
);

/* ── Поиск и фильтры ───────────────────────────────────────────────────────── */
/* Ширина поиска в панели над списком: на телефоне — вся строка (оболочка телефона снимает
   min-width, и поле сжималось в точку рядом с фильтрами), на компьютере — до 420 px. */
export const SEARCH_WIDTH = 'w-full sm:w-auto sm:min-w-[220px] sm:flex-1 sm:max-w-[420px]';
export const SearchField = ({ value, onChange, placeholder, ariaLabel, className = '', autoFocus = false }) => (
    <div className={`relative ${className}`}>
        <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
            type="search"
            autoFocus={autoFocus}
            className="h-10 w-full rounded-xl border-0 bg-slate-100 pl-9 pr-3 text-[14px] text-slate-900 placeholder-slate-400 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
            placeholder={placeholder}
            aria-label={ariaLabel || placeholder}
            value={value}
            onChange={(event) => onChange(event.target.value)}
        />
    </div>
);

/* Фильтр — кнопка-меню: одна кнопка на признак. Выбранное видно в подписи и мягким голубым;
   первый пункт («Все …») снимает фильтр. */
const FILTER_BTN = 'inline-flex h-10 max-w-[240px] items-center gap-1.5 rounded-xl pl-3.5 pr-2.5 text-[14px] font-semibold transition';
export const FilterMenu = ({ title, allLabel, value, options = [], onChange, align = 'left' }) => {
    const current = options.find((option) => option.value === value) || null;
    return (
        <IosMenu
            label={current ? `${title}: ${current.label}` : `${title}: все`}
            align={align}
            trigger={{
                /* Без «!»: в раскрытом виде IosMenu дописывает кнопке bg-slate-100 и text-slate-900, но
                   синие утилиты стоят в таблице стилей позже серых и остаются сверху — и в тёмном слое
                   портала тоже (он знает bg-blue-50, а «!bg-blue-50» не знает). */
                className: `${FILTER_BTN} ${current
                    ? 'bg-blue-50 text-blue-700 ring-1 ring-inset ring-blue-200'
                    : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`,
                text: (
                    <>
                        <span className="truncate">{current ? current.label : title}</span>
                        <ChevronDown size={15} strokeWidth={2.2} className="shrink-0 opacity-60" aria-hidden="true" />
                    </>
                ),
            }}
            items={[
                { key: '__all', label: allLabel, checked: !current, onSelect: () => onChange('') },
                ...options.map((option, index) => ({
                    key: String(option.value),
                    label: option.label,
                    checked: option.value === value,
                    separatorBefore: index === 0,
                    onSelect: () => onChange(option.value),
                })),
            ]}
        />
    );
};

/* ── Группы строк, как в «Настройках» iOS ──────────────────────────────────── */
export const Group = ({ children, className = '' }) => (
    <div className={`${SURFACE} divide-y divide-slate-100 ${className}`}>{children}</div>
);

const blank = (value) => value === null || value === undefined || value === '' || value === false;

/* Строка «подпись — значение». Пустое значение строку не рисует. stacked — длинный текст
   (причина, примечание) под подписью, а не справа. */
export const GroupRow = ({ label, children, stacked = false }) => {
    if (blank(children)) return null;
    if (stacked) {
        return (
            <div className="px-4 py-2.5">
                <div className="text-[12.5px] text-slate-500">{label}</div>
                <div className="mt-0.5 whitespace-pre-line break-words text-[14px] leading-snug text-slate-900">{children}</div>
            </div>
        );
    }
    return (
        <div className="flex min-h-[44px] items-center justify-between gap-4 px-4 py-2">
            <span className="shrink-0 text-[14px] text-slate-500">{label}</span>
            <span className="min-w-0 break-words text-right text-[14px] text-slate-900">{children}</span>
        </div>
    );
};

/* Человек строкой: лицо, роль над именем, отдел справа. */
export const PersonRow = ({ label, name, note = '' }) => {
    if (!name) return null;
    return (
        <div className="flex items-center gap-3 px-4 py-2.5">
            <Avatar name={name} size={36} />
            <div className="min-w-0 flex-1">
                <div className="text-[12.5px] text-slate-500">{label}</div>
                <div className="truncate text-[14.5px] font-medium text-slate-900">{name}</div>
            </div>
            {note && <div className="max-w-[42%] shrink-0 truncate text-right text-[13px] text-slate-500">{note}</div>}
        </div>
    );
};

export const GroupTitle = ({ children, right = null, className = '' }) => (
    <div className={`mb-1.5 flex items-end justify-between gap-2 px-1 ${className}`}>
        <div className="text-[13px] font-medium text-slate-500">{children}</div>
        {right}
    </div>
);

/* Заголовок части страницы — крупный, как в списках iOS. */
export const PageTitle = ({ children, className = '' }) => (
    <h2 className={`text-[20px] font-bold tracking-tight text-slate-900 ${className}`}>{children}</h2>
);

/* Сноска под группой: одно короткое следствие действия. */
export const Footnote = ({ children, className = '' }) => (
    <p className={`px-1 text-[12.5px] leading-snug text-slate-500 ${className}`}>{children}</p>
);

export const ErrorNote = ({ text }) => (text
    ? <div role="alert" className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-[13px] leading-snug text-rose-700">{text}</div>
    : null);

/* Тело вкладки карточки: на компьютере окно держит рост, и короткая вкладка («Сведения») не
   сжимает его вдвое по сравнению с длинной («Акты»). На телефоне окно — экран, расти нечему. */
export const SHEET_BODY = 'sm:min-h-[340px]';

/* Вкладки внутри карточки: сегменты во всю ширину. Одна вкладка — без переключателя. */
export const SheetTabs = ({ value, onChange, options = [], ariaLabel }) => {
    const shown = options.filter(Boolean);
    if (shown.length < 2) return null;
    return <IosSegmented stretch slide value={value} onChange={onChange} options={shown} ariaLabel={ariaLabel} />;
};

/* ── Поля формы ────────────────────────────────────────────────────────────── */
export const FIELD = 'w-full rounded-xl border-0 bg-slate-100 px-3.5 py-2.5 text-[14px] text-slate-900 placeholder-slate-400 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70';
export const FIELD_INVALID = 'ring-2 ring-rose-300';
const SELECT_TEXT = '!px-3.5 !py-2.5 text-[14px] text-slate-900';
const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 text-[14px] tabular-nums text-slate-900 border-0 transition hover:bg-slate-200/70 focus:outline-none focus:ring-2 focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left';

/* Подпись поля; чего не хватает — красным под полем и только после попытки отправить. */
export const Field = ({ label, error = '', required = false, children, className = '' }) => (
    <div className={`block space-y-1 ${className}`}>
        <span className="flex min-h-5 items-center gap-1 px-1 text-[12.5px] font-medium text-slate-500">
            {label}
            {required && <span className="text-slate-400" aria-hidden="true">*</span>}
        </span>
        {children}
        {error && <span className="block px-1 text-[12px] text-rose-600">{error}</span>}
    </div>
);

export const FormSelect = ({ invalid = false, ...props }) => (
    <CustomSelect variant="ios" textClassName={SELECT_TEXT} invalid={invalid} {...props} />
);

export const FormDate = ({ invalid = false, ...props }) => (
    <IosDatePicker triggerClassName={`${DATE_TRIGGER} ${invalid ? FIELD_INVALID : ''}`} {...props} />
);

/* Выбор сотрудника: список с поиском, отдел — приглушённо справа. */
export const PersonSelect = ({ people = [], value, onChange, placeholder = 'Выберите сотрудника', exclude = [], invalid = false, ariaLabel }) => {
    const options = useMemo(() => (people || [])
        .filter((person) => !exclude.includes(person.id) || person.id === value)
        .map((person) => ({ value: person.id, label: person.name, meta: person.department_name || '' })), [people, exclude, value]);
    return (
        <FormSelect value={value} onChange={(next) => onChange?.(next || null)} options={options} placeholder={placeholder}
            searchable searchPlaceholder="Фамилия…" ariaLabel={ariaLabel || placeholder} invalid={invalid} />
    );
};

/* ── Обложка ОС ────────────────────────────────────────────────────────────── */
export const Thumb = ({ url, size = 44, className = '' }) => {
    const [broken, setBroken] = useState(false);
    useEffect(() => { setBroken(false); }, [url]);
    return (
        <span
            className={`grid shrink-0 place-items-center overflow-hidden rounded-xl bg-slate-100 text-slate-300 ${className}`}
            style={{ width: size, height: size }}
        >
            {url && !broken
                ? <img src={url} alt="" loading="lazy" onError={() => setBroken(true)} className="h-full w-full object-cover" />
                : <Package size={Math.round(size * 0.42)} strokeWidth={1.6} />}
        </span>
    );
};

/* ── Просмотр фото во весь экран ───────────────────────────────────────────── */
export const usePhotoViewer = (photos) => {
    const [index, setIndex] = useState(-1);
    const list = photos || [];
    const open = useCallback((id) => setIndex(list.findIndex((photo) => photo.id === id)), [list]);
    const close = useCallback(() => setIndex(-1), []);
    const current = index >= 0 ? list[index] : null;
    const viewer = (
        <IosLightbox
            url={current?.url || ''}
            alt={current?.label || 'Фото ОС'}
            onClose={close}
            onPrev={list.length > 1 ? () => setIndex((prev) => (prev - 1 + list.length) % list.length) : undefined}
            onNext={list.length > 1 ? () => setIndex((prev) => (prev + 1) % list.length) : undefined}
            counter={list.length > 1 && current ? `${index + 1} / ${list.length}` : ''}
        />
    );
    return { open, viewer };
};

/* Снимки плитками: в ряд (size) или сеткой во всю ширину (grid). Плитка открывает снимок. */
export const PhotoStrip = ({ photos = [], onOpen, size = 64, labels = null, grid = false }) => {
    if (!photos.length) return null;
    return (
        <div className={grid ? 'grid gap-2' : 'flex flex-wrap gap-1.5'}
             style={grid ? { gridTemplateColumns: 'repeat(auto-fill, minmax(132px, 1fr))' } : undefined}>
            {photos.map((photo) => (
                <button
                    key={photo.id}
                    type="button"
                    onClick={() => onOpen?.(photo.id)}
                    title={labels?.[photo.kind] || ''}
                    className={`overflow-hidden bg-slate-100 transition hover:brightness-95 active:scale-[0.98] ${grid ? 'aspect-square w-full rounded-2xl' : 'rounded-xl'}`}
                    style={grid ? undefined : { width: size, height: size }}
                    aria-label={`Открыть фото${labels?.[photo.kind] ? `: ${labels[photo.kind]}` : ''}`}
                >
                    <img src={grid ? (photo.url || photo.thumb_url) : (photo.thumb_url || photo.url)} alt="" loading="lazy" className="h-full w-full object-cover" />
                </button>
            ))}
        </div>
    );
};

/* ── Слот фото в форме ─────────────────────────────────────────────────────────
   У ОС два обязательных снимка — у каждого своё место с подписью, чтобы было видно, какого
   не хватает. value: { url } — снимок уже на сервере, { file, preview } — только что выбран. */
export const PhotoSlot = ({ label, value = null, onPick, onClear, invalid = false, optional = false, disabled = false }) => {
    const inputRef = useRef(null);
    const [busy, setBusy] = useState(false);
    const [issue, setIssue] = useState('');
    const pick = async (event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (!file) return;
        setBusy(true);
        setIssue('');
        try {
            // Кадр с телефона ужимается ещё в браузере — уходит за секунду, а не за минуту.
            const prepared = await preparePhoto(file);
            if (!prepared.ok) { setIssue(prepared.issue); return; }
            const blob = prepared.blob;
            const ready = blob instanceof File ? blob : new File([blob], prepared.name || file.name || 'photo.webp', { type: blob.type || 'image/webp' });
            onPick?.({ file: ready, preview: URL.createObjectURL(ready) });
        } finally {
            setBusy(false);
        }
    };
    const shown = value?.preview || value?.thumb_url || value?.url || '';
    return (
        <div className="w-[132px]">
            <div className="relative">
                <button
                    type="button"
                    disabled={disabled || busy}
                    onClick={() => inputRef.current?.click()}
                    aria-label={`${shown ? 'Заменить фото' : 'Добавить фото'}: ${label}`}
                    className={`relative grid h-[96px] w-[132px] place-items-center overflow-hidden rounded-2xl text-center transition active:scale-[0.98] disabled:cursor-default ${shown
                        ? 'bg-slate-100'
                        : (invalid ? 'bg-rose-50 text-rose-500 ring-1 ring-inset ring-rose-200' : 'bg-slate-100 text-slate-400 hover:bg-slate-200 hover:text-slate-500')}`}
                >
                    {shown ? <img src={shown} alt="" className="h-full w-full object-cover" /> : (
                        <span className="flex flex-col items-center gap-1.5 px-2">
                            {busy ? <Loader2 size={20} className="animate-spin" /> : (optional ? <Plus size={20} /> : <Camera size={20} />)}
                            <span className="text-[12px] font-medium leading-tight">{label}</span>
                        </span>
                    )}
                    {shown && busy && <span className="absolute inset-0 grid place-items-center bg-white/70"><Loader2 size={18} className="animate-spin text-slate-500" /></span>}
                </button>
                {shown && onClear && !disabled && (
                    <button
                        type="button"
                        onClick={onClear}
                        aria-label={`Убрать фото: ${label}`}
                        className="absolute right-1.5 top-1.5 grid h-6 w-6 place-items-center rounded-full bg-slate-900/60 text-white transition hover:bg-slate-900/80 active:scale-95"
                    >
                        <X size={13} strokeWidth={2.4} />
                    </button>
                )}
            </div>
            {shown && !optional && <div className="mt-1 truncate px-0.5 text-[12px] text-slate-500">{label}</div>}
            {issue && <div className="mt-0.5 px-0.5 text-[12px] leading-tight text-rose-600">{issue}</div>}
            <input ref={inputRef} type="file" accept={PHOTO_ACCEPT} className="hidden" onChange={pick} tabIndex={-1} />
        </div>
    );
};

/* ── Скан акта (PDF) ─────────────────────────────────────────────────────────
   Подписанный акт сканируется прямо здесь — камерой телефона или ноутбука, тем же сканером, что
   в «Дисциплинах» (владелец 17.11.2026: «возможность сразу сканировать документ везде, как в
   дисциплинах»): лист находится сам, края выпрямляются, страницы складываются в один PDF. Готовый
   PDF с компьютера — второй кнопкой. Сервер принимает только PDF (files.check_scan). */
const SCAN_FORMATS = scanFormats('application/pdf,.pdf');
const PICK_TILE = 'flex min-h-[76px] flex-col items-start justify-center gap-1 rounded-2xl px-3.5 py-3 text-left transition active:scale-[0.99]';
const PICK_GRID = { display: 'grid', gap: 8, gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' };
const FILE_ROW = { display: 'grid', gap: 12, alignItems: 'center', gridTemplateColumns: 'auto minmax(0, 1fr) auto' };

export const PdfPicker = ({ file, onChange, invalid = false, maxBytes = 15 * 1024 * 1024 }) => {
    const inputRef = useRef(null);
    const returnRef = useRef(null);
    const [issue, setIssue] = useState('');
    const [scanning, setScanning] = useState(false);
    const take = (next) => {
        if (!next) return;
        const isPdf = next.type === 'application/pdf' || /\.pdf$/i.test(next.name || '');
        if (!isPdf) { setIssue('Скан акта принимается в PDF — нажмите «Сделать скан» или выберите PDF.'); return; }
        if (next.size > maxBytes) { setIssue(`Файл больше ${Math.round(maxBytes / 1024 / 1024)} МБ`); return; }
        setIssue('');
        onChange?.(next);
    };
    const pick = (event) => {
        const next = event.target.files?.[0];
        event.target.value = '';
        take(next);
    };
    const chooseFile = () => inputRef.current?.click();
    const scan = (event) => { returnRef.current = event?.currentTarget || null; setScanning(true); };
    const missing = invalid && !file;
    return (
        <div>
            {file ? (
                <div className="rounded-2xl bg-slate-100 px-3.5 py-3" style={FILE_ROW}>
                    <span className="grid h-10 w-10 place-items-center rounded-xl bg-rose-500 text-white"><FileText size={18} /></span>
                    <span className="min-w-0">
                        <span className="block truncate text-[14px] font-medium text-slate-900">{file.name}</span>
                        <span className="block text-[12.5px] text-slate-500">{fileSizeLabel(file.size)}</span>
                    </span>
                    <span className="flex items-center">
                        <button type="button" onClick={scan}
                                className="rounded-lg px-2 py-1.5 text-[13px] font-semibold text-blue-600 transition hover:bg-blue-50">Переснять</button>
                        <button type="button" onClick={chooseFile}
                                className="rounded-lg px-2 py-1.5 text-[13px] font-medium text-slate-500 transition hover:bg-slate-200">Файл</button>
                    </span>
                </div>
            ) : (
                <div style={PICK_GRID}>
                    <button type="button" onClick={scan}
                            className={`${PICK_TILE} ${missing ? 'bg-rose-50 ring-1 ring-inset ring-rose-200' : 'bg-blue-50 hover:bg-blue-100'}`}>
                        <ScanLine size={20} className={missing ? 'text-rose-500' : 'text-blue-600'} />
                        <span className={`text-[14px] font-semibold ${missing ? 'text-rose-700' : 'text-blue-700'}`}>Сделать скан</span>
                        <span className="text-[12px] text-slate-500">камерой, в PDF</span>
                    </button>
                    <button type="button" onClick={chooseFile}
                            className={`${PICK_TILE} ${missing ? 'bg-rose-50 ring-1 ring-inset ring-rose-200' : 'bg-slate-100 hover:bg-slate-200'}`}>
                        <FileUp size={20} className={missing ? 'text-rose-500' : 'text-slate-500'} />
                        <span className="text-[14px] font-semibold text-slate-800">Выбрать PDF</span>
                        <span className="text-[12px] text-slate-500">до {Math.round(maxBytes / 1024 / 1024)} МБ</span>
                    </button>
                </div>
            )}
            {issue && <div className="mt-1 px-1 text-[12px] text-rose-600">{issue}</div>}
            <input ref={inputRef} type="file" accept="application/pdf,.pdf" className="hidden" onChange={pick} tabIndex={-1} />
            {scanning && (
                <DocumentScanner
                    formats={SCAN_FORMATS}
                    onClose={() => setScanning(false)}
                    onDone={(scanned) => { setScanning(false); take(scanned); }}
                    returnFocus={() => returnRef.current?.focus?.({ preventScroll: true })}
                />
            )}
        </div>
    );
};

/* ── Путь запроса ─────────────────────────────────────────────────────────────
   Тонкая полоса из отрезков: пройденные и текущий — синие, будущие — серые; отклонённый
   запрос останавливается красным. Закрытый — весь зелёный. Подпись выделена у текущего шага. */
const STEP_SHORT = { draft: 'Черновик', review: 'Проверка', signing: 'Подписание', original: 'Оригинал', closed: 'Закрыт' };
export const Stepper = ({ steps = [] }) => {
    const finished = steps.length > 0 && steps.every((step) => step.state === 'done');
    return (
        <ol className="flex gap-1.5" aria-label="Путь запроса">
            {steps.map((step) => {
                const current = step.state === 'current';
                const stopped = step.state === 'stopped';
                const done = step.state === 'done';
                const bar = stopped ? 'bg-rose-500' : finished ? 'bg-emerald-500' : (done || current) ? 'bg-blue-500' : 'bg-slate-200';
                const text = stopped ? 'font-semibold text-rose-700' : current ? 'font-semibold text-slate-900'
                    : done ? 'text-slate-500' : 'text-slate-400';
                return (
                    <li key={step.code} className="min-w-0 flex-1" aria-current={current ? 'step' : undefined} title={step.label}>
                        <span className={`block h-1 rounded-full ${bar}`} />
                        <span className={`mt-1.5 block truncate text-[11.5px] ${text}`}>{STEP_SHORT[step.code] || step.label}</span>
                    </li>
                );
            })}
        </ol>
    );
};

/* ── Карточки списков ──────────────────────────────────────────────────────── */
/* Сетка задана стилем, а не классом grid-cols-[…]: такие сетки телефонная оболочка схлопывает. */
export const CARD_GRID = { display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fill, minmax(288px, 1fr))' };
/* «Снимок слева — текст справа». Тоже стилем: flex items-start с растяжимым соседом оболочка
   телефона ставит колонкой (её правило для каркасов экранов), и снимок вставал над названием. */
export const LEAD_ROW = { gridTemplateColumns: 'auto minmax(0, 1fr)' };

/* ОС карточкой: снимок, название, номер; внизу — за кем (или где) и метка, если ОС не в обычном
   состоянии. holder=false — «Мои ОС»: вместо себя самого человек видит место. */
export const AssetTile = ({ asset, onOpen, holder = true }) => {
    const flag = assetFlag(asset);
    const gone = asset.status === 'written_off';
    return (
        <button type="button" onClick={() => onOpen(asset.id)}
                className={`${SURFACE} ${SURFACE_HOVER} flex min-h-[118px] w-full flex-col gap-3 p-3.5 text-left transition active:scale-[0.99]`}>
            <span className="grid items-start gap-3" style={LEAD_ROW}>
                <Thumb url={asset.cover_url} size={56} className={`!rounded-[14px] ${gone ? 'opacity-60 grayscale' : ''}`} />
                <span className="min-w-0">
                    <span className={`line-clamp-2 text-[15px] font-semibold leading-snug ${gone ? 'text-slate-500' : 'text-slate-900'}`}>{asset.name}</span>
                    <span className="mt-0.5 block truncate text-[12.5px] tabular-nums text-slate-500">{asset.inventory_number}</span>
                </span>
            </span>
            <span className="mt-auto flex items-center gap-2">
                {holder ? (
                    <>
                        <Avatar name={asset.mol_name} size={22} />
                        <span className="min-w-0 flex-1 truncate text-[13px] text-slate-600">{shortName(asset.mol_name) || '—'}</span>
                    </>
                ) : (
                    <span className="min-w-0 flex-1 truncate text-[13px] text-slate-500">{placeText(asset) || asset.category_name}</span>
                )}
                {flag && <StatusWord tone={flag.tone} className="shrink-0">{flag.label}</StatusWord>}
            </span>
        </button>
    );
};

/* Вид запроса значком в цветном кружке — у карточки доски без снимка и в шапке карточки запроса. */
export const KIND_LOOK = {
    intake: [PackagePlus, 'green'], transfer: [ArrowRightLeft, 'blue'], writeoff: [PackageX, 'slate'],
    dismissal: [UserMinus, 'violet'], need: [ShoppingCart, 'amber'],
};

/* Карточка доски запросов — как карточка задачи на доске «Задач»: белая, компактная. Слева снимок
   ОС из запроса (нет снимка — значок вида), справа вид и номер и что именно; внизу — кто кому и
   справа одно: «Ждёт вас», если следующий шаг за смотрящим, у кого запрос на проверке — или дата,
   когда запрос пришёл в колонку (since): по ней видно, сколько он в ней лежит. Статус не пишется —
   его говорит колонка. Раскладка — сеткой через style: оболочка телефона переносит flex-ряды с gap. */
const REVIEW_SHORT = { it: 'У IT', head: 'У руководителя', accounting: 'У бухгалтерии' };
const CARD_FOOT = { display: 'grid', gap: 8, alignItems: 'center', gridTemplateColumns: 'minmax(0, 1fr) auto' };
export const RequestBoardCard = ({ item, onOpen, draggable = false, dragging = false, onDragStart, onDragEnd }) => {
    const [Icon, tone] = KIND_LOOK[item.kind] || KIND_LOOK.transfer;
    const open = () => onOpen(item.id);
    let marker = <span className="text-[11.5px] tabular-nums text-slate-400">{fmtDay(item.since || item.submitted_at || item.created_at)}</span>;
    if (item.waiting) marker = <span className="text-[12px] font-semibold text-blue-600">Ждёт вас</span>;
    else if (item.status === 'review' && REVIEW_SHORT[item.review_by]) {
        marker = <span className="text-[12px] font-semibold text-amber-700">{REVIEW_SHORT[item.review_by]}</span>;
    }
    return (
        <div
            role="button"
            tabIndex={0}
            draggable={draggable}
            onDragStart={draggable ? (event) => onDragStart?.(event, item) : undefined}
            onDragEnd={draggable ? onDragEnd : undefined}
            onClick={open}
            onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } }}
            aria-label={`${KIND_LABELS[item.kind] || 'Запрос'} № ${item.id}`}
            className={`rounded-[14px] bg-white p-2.5 shadow-[0_1px_2px_rgba(15,23,42,0.06),0_0_0_1px_rgba(15,23,42,0.04)] outline-none transition hover:shadow-[0_6px_16px_rgba(15,23,42,0.1),0_0_0_1px_rgba(15,23,42,0.06)] focus-visible:ring-2 focus-visible:ring-blue-500/70 ${
                draggable ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer'} ${dragging ? 'opacity-40' : ''}`}
        >
            <span className="grid items-start gap-2.5" style={LEAD_ROW}>
                {item.cover_url
                    ? <Thumb url={item.cover_url} size={40} className="!rounded-[10px]" />
                    : <IconCircle icon={Icon} tone={tone} size={40} soft />}
                <span className="min-w-0">
                    <span className="block text-[13.5px] font-semibold leading-snug text-slate-900">
                        {KIND_LABELS[item.kind]} <span className="whitespace-nowrap font-medium tabular-nums text-slate-400">№&nbsp;{item.id}</span>
                    </span>
                    <span className="mt-0.5 block text-[12.5px] leading-snug text-slate-600 line-clamp-2">{requestSubject(item)}</span>
                </span>
            </span>
            <span className="mt-2" style={CARD_FOOT}>
                <span className="truncate text-[12px] text-slate-500">{routeText(item)}</span>
                {marker}
            </span>
        </div>
    );
};

export const Pager = ({ page, pageSize, total, onPage, loading = false, unit = '' }) => {
    if (!total) return null;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const current = Math.min(Math.max(1, page), pages);
    const button = 'grid h-9 w-9 place-items-center rounded-full bg-white text-[16px] leading-none text-slate-600 shadow-[0_0_0_1px_rgba(15,23,42,0.08)] transition hover:bg-slate-50 disabled:opacity-35';
    return (
        <div className="flex flex-wrap items-center justify-between gap-2 px-1">
            <span className="text-[12.5px] tabular-nums text-slate-400">{unit}</span>
            {pages > 1 && (
                <span className="flex items-center gap-2">
                    <button type="button" disabled={current <= 1 || loading} onClick={() => onPage(current - 1)} className={button} aria-label="Предыдущая страница">‹</button>
                    <span className="text-center text-[12.5px] tabular-nums text-slate-500" style={{ minWidth: 54 }}>{current} из {pages}</span>
                    <button type="button" disabled={current >= pages || loading} onClick={() => onPage(current + 1)} className={button} aria-label="Следующая страница">›</button>
                </span>
            )}
        </div>
    );
};

/* Пусто: крупный значок и одна фраза. */
export const EmptyState = ({ icon: Icon = Package, title, children = null }) => (
    <div className="flex flex-col items-center px-4 py-16 text-center">
        <span className="grid h-14 w-14 place-items-center rounded-full bg-slate-100 text-slate-400"><Icon size={26} strokeWidth={1.7} /></span>
        <p className="mt-3 text-[16px] font-semibold text-slate-900">{title}</p>
        {children && <p className="mt-1 max-w-sm text-[13.5px] text-slate-500">{children}</p>}
    </div>
);

/* Заготовка карточек списка, пока данные не пришли. */
export const CardsSkeleton = ({ count = 8, height = 118 }) => (
    <div style={CARD_GRID} role="status" aria-busy="true" aria-label="Загружаем">
        {Array.from({ length: count }, (_, index) => (
            <div key={index} className={`${SURFACE} flex gap-3 p-3.5`} style={{ height }}>
                <span className="h-14 w-14 shrink-0 animate-pulse rounded-[14px] bg-slate-100" />
                <span className="flex-1 space-y-2 pt-1">
                    <span className="block h-3.5 w-4/5 animate-pulse rounded bg-slate-100" />
                    <span className="block h-3 w-2/5 animate-pulse rounded bg-slate-100" />
                </span>
            </div>
        ))}
    </div>
);

/* Строка поиска с задержкой: запрос уходит, когда человек перестал печатать. */
export const useDebounced = (value, delay = 300) => {
    const [settled, setSettled] = useState(value);
    useEffect(() => {
        const timer = setTimeout(() => setSettled(value), delay);
        return () => clearTimeout(timer);
    }, [value, delay]);
    return settled;
};
