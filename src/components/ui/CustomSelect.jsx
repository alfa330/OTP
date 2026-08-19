import React, { useState, useRef, useEffect, useLayoutEffect, useMemo, useId } from 'react';
import { createPortal } from 'react-dom';
import { APPLE_FONT } from './ios';

/*
 * Аккуратный кастомный select (вместо нативного <select>).
 * Раскрывающийся список рендерится в портал (body документа кнопки) с fixed-позицией,
 * чтобы не обрезался скроллом/overflow модалки. Закрывается по клику вне и Esc.
 * При скролле страницы/модалки позиция пересчитывается (список «приклеен» к кнопке);
 * скролл ВНУТРИ самого списка его не закрывает.
 *
 * Props:
 *   value             — текущее значение (примитив)
 *   onChange(v)       — вызывается со значением выбранной опции (НЕ event)
 *   options           — [{ value, label, disabled?, groupLabel? }]
 *                       groupLabel включает заголовки-разделители: они рисуются
 *                       при смене группы и не участвуют в выборе/клавиатуре
 *   placeholder       — текст, когда ничего не выбрано
 *   disabled          — заблокирован
 *   className         — класс на обёртку (для ширины/отступов)
 *   searchable        — показывать строку поиска (также включается авто для длинных списков)
 *   searchPlaceholder — плейсхолдер строки поиска
 *   variant           — `default` либо более мягкий `ios`
 *   ariaLabel         — доступное имя кнопки
 *   id                — id кнопки: с ним <label htmlFor> открывает список
 */
export default function CustomSelect({
  value,
  onChange,
  options = [],
  placeholder = 'Выберите...',
  disabled = false,
  className = '',
  searchable = false,
  searchPlaceholder = 'Поиск…',
  variant = 'default',
  ariaLabel,
  id,
}) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState(null);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(-1);
  const btnRef = useRef(null);
  const popRef = useRef(null);
  const searchRef = useRef(null);
  const listboxId = useId();

  const showSearch = searchable;

  /* Портал, размеры и слушатели берём у ОКНА кнопки, а не у главного: часть
     разделов открывается в отдельном окне (Document Picture-in-Picture), и там
     главное window даёт чужие размеры, а его document.body — чужой документ,
     то есть список просто не появится. */
  const ownerDocument = () => btnRef.current?.ownerDocument || document;

  const recompute = () => {
    const el = btnRef.current;
    if (!el) return;
    const view = el.ownerDocument?.defaultView || window;
    const r = el.getBoundingClientRect();
    const spaceBelow = view.innerHeight - r.bottom;
    const openUp = spaceBelow < 240 && r.top > spaceBelow;
    setCoords({
      left: Math.round(r.left),
      width: Math.round(r.width),
      top: openUp ? undefined : Math.round(r.bottom + 4),
      bottom: openUp ? Math.round(view.innerHeight - r.top + 4) : undefined,
      maxHeight: Math.max(160, Math.round((openUp ? r.top : spaceBelow) - 16)),
    });
  };

  useLayoutEffect(() => {
    if (open) recompute();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Сброс поиска при каждом открытии + фокус на строке поиска.
  useEffect(() => {
    if (!open) { setQuery(''); return; }
    if (showSearch) {
      const id = requestAnimationFrame(() => searchRef.current?.focus());
      return () => cancelAnimationFrame(id);
    }
    return undefined;
  }, [open, showSearch]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (btnRef.current?.contains(e.target) || popRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    // Скролл внутри списка не закрывает; внешний — пересчитывает позицию, чтобы
    // список оставался «приклеен» к кнопке.
    const onScroll = (e) => {
      if (popRef.current && (popRef.current === e.target || popRef.current.contains(e.target))) return;
      recompute();
    };
    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      setOpen(false);
      requestAnimationFrame(() => btnRef.current?.focus());
    };
    const doc = ownerDocument();
    const view = doc.defaultView || window;
    doc.addEventListener('mousedown', onDoc);
    view.addEventListener('scroll', onScroll, true);
    view.addEventListener('resize', recompute);
    doc.addEventListener('keydown', onKey);
    return () => {
      doc.removeEventListener('mousedown', onDoc);
      view.removeEventListener('scroll', onScroll, true);
      view.removeEventListener('resize', recompute);
      doc.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const selected = options.find((o) => String(o.value) === String(value ?? ''));
  const isIos = variant === 'ios';

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!showSearch || !q) return options;
    return options.filter((o) => String(o.label ?? '').toLowerCase().includes(q));
  }, [options, query, showSearch]);

  useEffect(() => {
    if (!open) {
      setActiveIndex(-1);
      return;
    }
    const selectedIndex = filtered.findIndex((option) =>
      !option.disabled && String(option.value) === String(value ?? ''));
    const firstEnabled = filtered.findIndex((option) => !option.disabled);
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : firstEnabled);
  }, [open, filtered, value]);

  const moveActive = (direction) => {
    const enabled = filtered
      .map((option, index) => (!option.disabled ? index : -1))
      .filter((index) => index >= 0);
    if (!enabled.length) return;
    setActiveIndex((current) => {
      const currentPosition = enabled.indexOf(current);
      if (currentPosition < 0) return direction > 0 ? enabled[0] : enabled[enabled.length - 1];
      return enabled[(currentPosition + direction + enabled.length) % enabled.length];
    });
  };

  const pick = (o) => {
    if (o.disabled) return;
    onChange?.(o.value);
    setOpen(false);
    requestAnimationFrame(() => btnRef.current?.focus());
  };

  return (
    <div className={className}>
      <button
        ref={btnRef}
        id={id}
        type="button"
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={!showSearch && open && activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined}
        onClick={() => { if (!disabled) setOpen((v) => !v); }}
        onKeyDown={(event) => {
          if (disabled) return;
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!open) setOpen(true);
            else moveActive(event.key === 'ArrowDown' ? 1 : -1);
            return;
          }
          if ((event.key === 'Enter' || event.key === ' ') && open && !showSearch && activeIndex >= 0) {
            event.preventDefault();
            pick(filtered[activeIndex]);
          }
        }}
        className={isIos
          ? `flex w-full items-center justify-between gap-2 rounded-xl bg-white px-3 py-2 text-left text-[12.5px] font-medium text-slate-700 ring-1 transition-all ${
            disabled
              ? 'cursor-not-allowed opacity-50 ring-slate-200/70'
              : 'cursor-pointer ring-slate-200/70 hover:bg-slate-50 active:scale-[0.99]'
          } ${open ? 'ring-2 ring-blue-500/60' : 'shadow-[0_1px_2px_rgba(15,23,42,0.04)]'}`
          : `flex w-full items-center justify-between gap-2 rounded-lg border bg-white px-3 py-2 text-left text-sm text-gray-900 transition-all ${
            disabled
              ? 'cursor-not-allowed border-gray-200 opacity-60'
              : 'cursor-pointer border-gray-300 hover:border-gray-400'
          } ${open ? 'border-transparent ring-2 ring-blue-500' : 'shadow-sm'}`}
      >
        <span className={`truncate ${selected ? '' : 'text-gray-400'}`}>
          {selected ? selected.label : placeholder}
        </span>
        <svg
          width="14" height="14" viewBox="0 0 20 20" fill="none"
          className={`shrink-0 text-gray-400 transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
        >
          <path d="M5 8l5 5 5-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && coords && createPortal(
        <div
          ref={popRef}
          style={{
            position: 'fixed',
            left: coords.left,
            width: coords.width,
            top: coords.top,
            bottom: coords.bottom,
            maxHeight: coords.maxHeight,
            zIndex: 99999,
            fontFamily: isIos ? APPLE_FONT : undefined,
          }}
          className={isIos
            ? 'flex flex-col overflow-hidden rounded-2xl bg-white shadow-[0_14px_40px_rgba(15,23,42,0.16)] ring-1 ring-slate-200/80 animate-[fadeIn_.12s_ease]'
            : 'flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl animate-[fadeIn_.12s_ease]'}
        >
          {showSearch && (
            <div className={`shrink-0 border-b p-1.5 ${isIos ? 'border-slate-100' : 'border-gray-100'}`}>
              <input
                ref={searchRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                    e.preventDefault();
                    moveActive(e.key === 'ArrowDown' ? 1 : -1);
                  } else if (e.key === 'Enter') {
                    e.preventDefault();
                    if (activeIndex >= 0) pick(filtered[activeIndex]);
                  }
                }}
                placeholder={searchPlaceholder}
                aria-label={searchPlaceholder}
                role="combobox"
                aria-expanded={open}
                aria-controls={listboxId}
                aria-activedescendant={activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined}
                className={isIos
                  ? 'w-full rounded-xl border-0 bg-slate-100 px-2.5 py-2 text-[12.5px] text-slate-900 outline-none placeholder:text-slate-400 focus:bg-white focus:ring-2 focus:ring-blue-500/60'
                  : 'w-full rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-sm text-gray-900 outline-none focus:border-blue-400 focus:bg-white'}
              />
            </div>
          )}

          <div
            id={listboxId}
            className="min-h-0 overflow-auto py-1"
            role="listbox"
            aria-label={ariaLabel ? `${ariaLabel}: варианты` : undefined}
          >
            {filtered.length === 0 ? (
              <div className={`px-3 py-2 ${isIos ? 'text-[12.5px] text-slate-400' : 'text-sm text-gray-400'}`}>
                {query ? 'Ничего не найдено' : 'Нет вариантов'}
              </div>
            ) : (
              filtered.map((o, index) => {
                const isSel = String(o.value) === String(value ?? '');
                const isActive = index === activeIndex;
                const groupLabel = o.groupLabel || '';
                const startsGroup = groupLabel && groupLabel !== (filtered[index - 1]?.groupLabel || '');
                const option = (
                  <button
                    key={String(o.value)}
                    id={`${listboxId}-option-${index}`}
                    type="button"
                    disabled={o.disabled}
                    role="option"
                    aria-selected={isSel}
                    tabIndex={-1}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => pick(o)}
                    className={isIos
                      ? `flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left text-[12.5px] font-medium transition-colors ${
                        isSel
                          ? 'bg-blue-50 text-blue-700'
                          : isActive ? 'bg-slate-100 text-slate-800' : 'text-slate-700 hover:bg-slate-50'
                      } ${o.disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'}`
                      : `flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors ${
                        isSel
                          ? 'bg-blue-50 text-blue-700'
                          : isActive ? 'bg-gray-100 text-gray-900' : 'text-gray-700 hover:bg-gray-50'
                      } ${o.disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'}`}
                  >
                    <span className="truncate">{o.label}</span>
                    {isSel && (
                      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" className="shrink-0">
                        <path d="M5 10l3 3 7-7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </button>
                );
                if (!startsGroup) return option;
                return (
                  <React.Fragment key={`group-${groupLabel}-${String(o.value)}`}>
                    <div
                      role="presentation"
                      className={isIos
                        ? 'px-3 pb-1 pt-2.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400'
                        : 'px-3 pb-1 pt-2.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400'}
                    >
                      {groupLabel}
                    </div>
                    {option}
                  </React.Fragment>
                );
              })
            )}
          </div>
        </div>,
        ownerDocument().body
      )}
    </div>
  );
}
