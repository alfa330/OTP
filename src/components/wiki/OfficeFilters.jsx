import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, RotateCcw, SlidersHorizontal } from 'lucide-react';
import { APPLE_FONT, IosToggle, iosGroupLabel } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';

/* Фильтр офисов одной кнопкой.
 *
 * Раньше условия стояли в строке селектами: два фильтра занимали половину
 * панели, а третьему (архивные) и сортировке места уже не было. Условий стало
 * больше, чем помещается в строку, поэтому они уехали под кнопку — а на самой
 * кнопке видно число активных, чтобы «ничего не находится» не оставалось
 * загадкой.
 *
 * Механика поповера повторяет IosMenu (src/components/ui/ios.jsx): портал в
 * body с fixed-координатами, закрытие по щелчку вне, Escape и прокрутке. Сам
 * IosMenu переиспользовать нельзя — он список действий, а здесь форма.
 */

export const SORT_OPTIONS = [
    { key: 'city_asc', label: 'Город А–Я', grouped: true },
    { key: 'city_desc', label: 'Город Я–А', grouped: true },
    { key: 'name_asc', label: 'Название А–Я', grouped: false },
    { key: 'name_desc', label: 'Название Я–А', grouped: false },
    { key: 'status', label: 'Сначала открытые', grouped: false },
    { key: 'manual', label: 'Как в справочнике', grouped: true },
];

export const DEFAULT_FILTERS = {
    sort: 'city_asc', city: '', parkId: '', showArchived: false,
};

/** Сколько условий отличается от «показать всё». Сортировка условием не считается:
 *  она меняет порядок, а не состав, и число на кнопке врало бы. */
export const activeFilterCount = (value) => (
    (value.city ? 1 : 0) + (value.parkId ? 1 : 0) + (value.showArchived ? 1 : 0)
);

const PANEL_WIDTH = 268;

/* Поповер внутри поповера: список CustomSelect уходит в портал body, то есть
   лежит ВНЕ панели фильтров. Без этой проверки первый же mousedown по строке
   списка считался бы «щелчком мимо», панель фильтров схлопывалась бы вместе с
   селектом — и клик по опции не успевал бы случиться, выбор терялся.
   Ищем по раскрытому listbox и берём его родителя: так внутрь попадают и
   строка поиска, и поля/границы карточки списка, а не только сами опции. */
const isInsideOpenSelect = (target) => Array.from(document.querySelectorAll('[role="listbox"]'))
    .some((list) => list.parentElement?.contains(target));

const Row = ({ label, children }) => (
    <label className="flex items-center justify-between gap-3 py-1">
        <span className="text-[13px] text-slate-700">{label}</span>
        {children}
    </label>
);

export default function OfficeFilters({ value, onChange, cities = [], parks = [], canManage }) {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState(null);
    const btnRef = useRef(null);
    const popRef = useRef(null);

    const count = activeFilterCount(value);
    const isDefault = count === 0 && value.sort === DEFAULT_FILTERS.sort;

    const recompute = useCallback(() => {
        const el = btnRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        const spaceBelow = window.innerHeight - rect.bottom;
        const openUp = spaceBelow < 260 && rect.top > spaceBelow;
        setCoords({
            left: Math.min(window.innerWidth - PANEL_WIDTH - 8,
                           Math.max(8, Math.round(rect.right - PANEL_WIDTH))),
            top: openUp ? undefined : Math.round(rect.bottom + 6),
            bottom: openUp ? Math.round(window.innerHeight - rect.top + 6) : undefined,
            // Высоту не считаем заранее (состав панели меняется) — ограничиваем
            // доступным местом и разрешаем прокрутку внутри.
            maxHeight: Math.max(200, (openUp ? rect.top : spaceBelow) - 16),
        });
    }, []);

    useLayoutEffect(() => { if (open) recompute(); }, [open, recompute]);

    useEffect(() => {
        if (!open) return undefined;
        const onDoc = (e) => {
            if (btnRef.current?.contains(e.target) || popRef.current?.contains(e.target)) return;
            if (isInsideOpenSelect(e.target)) return;
            setOpen(false);
        };
        const onKey = (e) => {
            if (e.key !== 'Escape') return;
            // Escape сначала обслуживает верхний слой: раскрытый список гасит
            // себя сам, и панель фильтров при этом обязана остаться.
            if (document.querySelector('[role="listbox"]')) return;
            setOpen(false);
            requestAnimationFrame(() => btnRef.current?.focus());
        };
        // Прокрутка закрывает, а не тащит панель за собой: кнопка уезжает, и
        // «приклеенная» панель повисла бы над чужим содержимым. Прокрутка
        // внутри раскрытого списка — исключение: она к кнопке не относится.
        const onScroll = (e) => {
            if (isInsideOpenSelect(e.target)) return;
            setOpen(false);
        };
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

    /* Пустая строка — законное значение «без условия»: на ней держится и
       activeFilterCount, и решение не слать параметр в запрос.
       У парка идентификатор приходит с сервера числом, а фильтр всегда хранил
       строку (нативный select иначе не умел) — приводим явно, чтобы тип
       value.parkId не менялся. */
    const cityOptions = useMemo(() => [
        { value: '', label: 'Все города' },
        ...cities.map((item) => ({ value: item.city, label: `${item.city} (${item.count})` })),
    ], [cities]);

    const parkOptions = useMemo(() => [
        { value: '', label: 'Все таксопарки' },
        ...parks.map((park) => ({ value: String(park.id), label: park.name })),
    ], [parks]);

    const patch = (part) => onChange({ ...value, ...part });

    return (
        <>
            <button
                ref={btnRef}
                type="button"
                aria-label="Фильтр и сортировка"
                aria-haspopup="dialog"
                aria-expanded={open}
                onClick={() => setOpen((v) => !v)}
                className={`inline-flex shrink-0 items-center gap-2 rounded-xl px-3.5 py-2.5 text-[13.5px] font-semibold transition-all active:scale-[0.98] ${
                    open || count
                        ? 'bg-blue-50 text-blue-700 ring-1 ring-blue-200'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
            >
                <SlidersHorizontal size={15} />
                Фильтр
                {count > 0 && (
                    <span className="grid h-[18px] min-w-[18px] place-items-center rounded-full bg-blue-600 px-1 text-[11px] font-bold tabular-nums text-white">
                        {count}
                    </span>
                )}
            </button>

            {open && coords && createPortal(
                <div
                    ref={popRef}
                    role="dialog"
                    aria-label="Фильтр офисов"
                    style={{
                        position: 'fixed',
                        left: coords.left,
                        width: PANEL_WIDTH,
                        top: coords.top,
                        bottom: coords.bottom,
                        maxHeight: coords.maxHeight,
                        zIndex: 99999,
                        fontFamily: APPLE_FONT,
                    }}
                    className="overflow-y-auto rounded-2xl bg-white/95 p-3 shadow-[0_14px_40px_rgba(15,23,42,0.18)] ring-1 ring-slate-200/80 backdrop-blur-xl animate-[fadeIn_.12s_ease]"
                >
                    <div className={iosGroupLabel}>Сортировка</div>
                    <div className="mt-1.5 space-y-0.5">
                        {SORT_OPTIONS.map((option) => (
                            <button
                                key={option.key}
                                type="button"
                                onClick={() => patch({ sort: option.key })}
                                className={`flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-[13.5px] transition ${
                                    value.sort === option.key
                                        ? 'bg-blue-50 font-semibold text-blue-700'
                                        : 'text-slate-700 hover:bg-slate-100'
                                }`}
                            >
                                <span className="min-w-0 flex-1 truncate">{option.label}</span>
                                {value.sort === option.key && <Check size={14} className="shrink-0" />}
                            </button>
                        ))}
                    </div>

                    <div className="my-2.5 h-px bg-slate-200/70" />

                    <div className="space-y-2">
                        <div>
                            <div className={iosGroupLabel}>Город</div>
                            <CustomSelect
                                variant="ios"
                                className="mt-1"
                                value={value.city}
                                onChange={(city) => patch({ city })}
                                options={cityOptions}
                                searchable
                                searchPlaceholder="Город…"
                                ariaLabel="Город"
                            />
                        </div>

                        <div>
                            <div className={iosGroupLabel}>Таксопарк</div>
                            <CustomSelect
                                variant="ios"
                                className="mt-1"
                                value={value.parkId}
                                onChange={(parkId) => patch({ parkId })}
                                options={parkOptions}
                                ariaLabel="Таксопарк"
                            />
                        </div>

                        {/* Архивные раньше приезжали управляющему всегда и молча
                            мешались в списке живых офисов. Теперь это выбор. */}
                        {canManage && (
                            <Row label="Показывать архивные">
                                <IosToggle
                                    checked={value.showArchived}
                                    onChange={(next) => patch({ showArchived: next })}
                                />
                            </Row>
                        )}
                    </div>

                    {!isDefault && (
                        <button
                            type="button"
                            onClick={() => onChange({ ...DEFAULT_FILTERS })}
                            className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-xl bg-slate-100 py-2 text-[13px] font-semibold text-slate-600 transition hover:bg-slate-200 active:scale-[0.98]"
                        >
                            <RotateCcw size={13} /> Сбросить
                        </button>
                    )}
                </div>,
                document.body,
            )}
        </>
    );
}
