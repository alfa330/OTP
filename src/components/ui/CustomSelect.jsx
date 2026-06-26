import React, { useState, useRef, useEffect, useLayoutEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';

/*
 * Аккуратный кастомный select (вместо нативного <select>).
 * Раскрывающийся список рендерится в портал (document.body) с fixed-позицией,
 * чтобы не обрезался скроллом/overflow модалки. Закрывается по клику вне и Esc.
 * При скролле страницы/модалки позиция пересчитывается (список «приклеен» к кнопке);
 * скролл ВНУТРИ самого списка его не закрывает.
 *
 * Props:
 *   value             — текущее значение (примитив)
 *   onChange(v)       — вызывается со значением выбранной опции (НЕ event)
 *   options           — [{ value, label, disabled? }]
 *   placeholder       — текст, когда ничего не выбрано
 *   disabled          — заблокирован
 *   className         — класс на обёртку (для ширины/отступов)
 *   searchable        — показывать строку поиска (также включается авто для длинных списков)
 *   searchPlaceholder — плейсхолдер строки поиска
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
}) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState(null);
  const [query, setQuery] = useState('');
  const btnRef = useRef(null);
  const popRef = useRef(null);
  const searchRef = useRef(null);

  const showSearch = searchable;

  const recompute = () => {
    const el = btnRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const spaceBelow = window.innerHeight - r.bottom;
    const openUp = spaceBelow < 240 && r.top > spaceBelow;
    setCoords({
      left: Math.round(r.left),
      width: Math.round(r.width),
      top: openUp ? undefined : Math.round(r.bottom + 4),
      bottom: openUp ? Math.round(window.innerHeight - r.top + 4) : undefined,
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
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', recompute);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', recompute);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const selected = options.find((o) => String(o.value) === String(value ?? ''));

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!showSearch || !q) return options;
    return options.filter((o) => String(o.label ?? '').toLowerCase().includes(q));
  }, [options, query, showSearch]);

  const pick = (o) => {
    if (o.disabled) return;
    onChange?.(o.value);
    setOpen(false);
  };

  return (
    <div className={className}>
      <button
        ref={btnRef}
        type="button"
        disabled={disabled}
        onClick={() => { if (!disabled) setOpen((v) => !v); }}
        className={`w-full flex items-center justify-between gap-2 px-3 py-2 text-left text-sm rounded-lg border bg-white text-gray-900 transition-all ${
          disabled
            ? 'opacity-60 cursor-not-allowed border-gray-200'
            : 'border-gray-300 hover:border-gray-400 cursor-pointer'
        } ${open ? 'ring-2 ring-blue-500 border-transparent' : 'shadow-sm'}`}
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
          }}
          className="flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl animate-[fadeIn_.12s_ease]"
        >
          {showSearch && (
            <div className="shrink-0 border-b border-gray-100 p-1.5">
              <input
                ref={searchRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') { e.preventDefault(); if (filtered.length) pick(filtered[0]); }
                }}
                placeholder={searchPlaceholder}
                className="w-full rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-sm text-gray-900 outline-none focus:border-blue-400 focus:bg-white"
              />
            </div>
          )}

          <div className="min-h-0 overflow-auto py-1">
            {filtered.length === 0 ? (
              <div className="px-3 py-2 text-sm text-gray-400">{query ? 'Ничего не найдено' : 'Нет вариантов'}</div>
            ) : (
              filtered.map((o) => {
                const isSel = String(o.value) === String(value ?? '');
                return (
                  <button
                    key={String(o.value)}
                    type="button"
                    disabled={o.disabled}
                    onClick={() => pick(o)}
                    className={`w-full flex items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors ${
                      isSel ? 'bg-blue-50 text-blue-700' : 'text-gray-700 hover:bg-gray-50'
                    } ${o.disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                  >
                    <span className="truncate">{o.label}</span>
                    {isSel && (
                      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" className="shrink-0">
                        <path d="M5 10l3 3 7-7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
