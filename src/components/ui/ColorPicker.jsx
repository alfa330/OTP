import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { IosSegmented, iosBtnGhost, iosBtnPrimary } from './ios';
import {
  GRID_COLUMNS,
  gridPalette,
  hexToHsv,
  hsvToHex,
  isLightColor,
  normalizeHex,
} from './colorMath';

/*
 * Выбор цвета панелью сайта — как системный пикер iOS/macOS: «Сетка» готовых
 * оттенков, чтобы быстро взять «примерно такой», и «Спектр», где тот же цвет
 * доводят до своего — тон полосой, насыщенность и яркость квадратом. Внизу
 * образец и код #RRGGBB: его можно и вписать (код из брендбука).
 *
 * Системный `<input type="color">` не годится: его окно рисует браузер, и в
 * каждом своё — деталь из другой программы рядом с карточками сайта.
 *
 * Панель — в ПОРТАЛЕ с fixed-координатами, как у IosDatePicker: пикер живёт
 * внутри модалок, где absolute обрезало бы его скроллом окна. Esc закрывает
 * только панель — необработанный он закрыл бы саму модалку с формой.
 *
 * Props:
 *   anchor      — элемент, к которому панель прижимается (кнопка, образец)
 *   value       — цвет #RRGGBB, с которого начинаем; '' — с жёлтого по умолчанию
 *   onApply(hex)— «Добавить» / «Готово»: отдаёт #RRGGBB в верхнем регистре
 *   onClose()   — закрыть без выбора (мимо панели, Esc, «Отмена»)
 *   onRemove    — если передан, в панели есть «Удалить» (правка уже выбранного)
 *   applyLabel  — подпись главной кнопки
 */

const PANEL_WIDTH = 288;
const EDGE_GAP = 8;
const ANCHOR_GAP = 8;
const DEFAULT_COLOR = '#FFD400';
const GRID = gridPalette();

const clamp01 = (value) => Math.min(1, Math.max(0, value));

/* Перетаскивание по прямоугольнику: отдаёт доли 0…1 по x и y. Захват указателя —
   чтобы ведение не обрывалось, когда палец или мышь выходят за край полосы. */
const useDrag = (onMove) => {
  const ref = useRef(null);
  const moveRef = useRef(onMove);
  moveRef.current = onMove;
  const report = (event) => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return;
    moveRef.current(
      clamp01((event.clientX - rect.left) / rect.width),
      clamp01((event.clientY - rect.top) / rect.height),
    );
  };
  const onPointerDown = (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    event.preventDefault();
    ref.current?.setPointerCapture?.(event.pointerId);
    report(event);
  };
  const onPointerMove = (event) => {
    if (!ref.current?.hasPointerCapture?.(event.pointerId)) return;
    report(event);
  };
  return { ref, onPointerDown, onPointerMove };
};

const Knob = ({ color, style }) => (
  <span
    aria-hidden="true"
    className="pointer-events-none absolute h-[18px] w-[18px] -translate-x-1/2 -translate-y-1/2 rounded-full border-[3px] border-white shadow-[0_1px_4px_rgba(15,23,42,0.35)]"
    style={{ backgroundColor: color, ...style }}
  />
);

export default function IosColorPopover({
  anchor,
  value = '',
  onApply,
  onClose,
  onRemove = null,
  applyLabel = 'Готово',
}) {
  const start = normalizeHex(value) || DEFAULT_COLOR;
  const [hsv, setHsv] = useState(() => hexToHsv(start));
  // Набранное в поле кода, пока человек печатает; null — поле показывает цвет.
  const [draft, setDraft] = useState(null);
  const [tab, setTab] = useState('grid');
  const [coords, setCoords] = useState(null);
  const panelRef = useRef(null);
  const hex = hsvToHex(hsv);

  const setColor = useCallback((nextHex) => {
    const normalized = normalizeHex(nextHex);
    if (!normalized) return;
    setHsv((prev) => {
      const next = hexToHsv(normalized);
      // У серых тона нет: не сбрасываем полосу тона на красный при выборе серого.
      return next.s === 0 || next.v === 0 ? { ...next, h: prev.h } : next;
    });
  }, []);

  const setHsvPart = useCallback((patch) => {
    setDraft(null);
    setHsv((prev) => ({ ...prev, ...patch }));
  }, []);

  const doc = anchor?.ownerDocument || (typeof document !== 'undefined' ? document : null);
  const view = doc?.defaultView || (typeof window !== 'undefined' ? window : null);

  const place = useCallback(() => {
    if (!anchor || !view) return;
    const rect = anchor.getBoundingClientRect();
    const height = panelRef.current?.offsetHeight || 420;
    const below = view.innerHeight - rect.bottom - ANCHOR_GAP - EDGE_GAP;
    const above = rect.top - ANCHOR_GAP - EDGE_GAP;
    const up = below < height && above > below;
    let top = up ? rect.top - ANCHOR_GAP - height : rect.bottom + ANCHOR_GAP;
    top = Math.max(EDGE_GAP, Math.min(top, view.innerHeight - EDGE_GAP - height));
    let left = rect.left;
    if (left + PANEL_WIDTH > view.innerWidth - EDGE_GAP) left = view.innerWidth - EDGE_GAP - PANEL_WIDTH;
    left = Math.max(EDGE_GAP, left);
    setCoords((prev) => (prev && prev.top === Math.round(top) && prev.left === Math.round(left)
      ? prev : { top: Math.round(top), left: Math.round(left) }));
  }, [anchor, view]);

  // Первый расчёт — по прикидке высоты, второй — уже по нарисованной панели.
  useLayoutEffect(() => { place(); }, [place]);
  useLayoutEffect(() => { if (coords) place(); }, [coords, tab, place]);

  useEffect(() => {
    if (!doc || !view) return undefined;
    const onDown = (event) => {
      if (panelRef.current?.contains(event.target) || anchor?.contains?.(event.target)) return;
      onClose?.();
    };
    const onKey = (event) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      event.preventDefault();
      onClose?.();
    };
    const onScroll = (event) => {
      if (panelRef.current?.contains(event.target)) return;
      place();
    };
    doc.addEventListener('pointerdown', onDown, true);
    doc.addEventListener('keydown', onKey, true);
    view.addEventListener('scroll', onScroll, true);
    view.addEventListener('resize', place);
    return () => {
      doc.removeEventListener('pointerdown', onDown, true);
      doc.removeEventListener('keydown', onKey, true);
      view.removeEventListener('scroll', onScroll, true);
      view.removeEventListener('resize', place);
    };
  }, [doc, view, anchor, onClose, place]);

  const square = useDrag((x, y) => setHsvPart({ s: x, v: 1 - y }));
  const hueBar = useDrag((x) => setHsvPart({ h: Math.min(359.9, x * 360) }));

  const nudge = (event, patch) => {
    event.preventDefault();
    setHsvPart(patch);
  };
  const onSquareKey = (event) => {
    const step = event.shiftKey ? 0.1 : 0.02;
    if (event.key === 'ArrowLeft') nudge(event, { s: clamp01(hsv.s - step) });
    if (event.key === 'ArrowRight') nudge(event, { s: clamp01(hsv.s + step) });
    if (event.key === 'ArrowUp') nudge(event, { v: clamp01(hsv.v + step) });
    if (event.key === 'ArrowDown') nudge(event, { v: clamp01(hsv.v - step) });
  };
  const onHueKey = (event) => {
    const step = event.shiftKey ? 15 : 2;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') nudge(event, { h: (hsv.h - step + 360) % 360 });
    if (event.key === 'ArrowRight' || event.key === 'ArrowUp') nudge(event, { h: (hsv.h + step) % 360 });
  };

  const apply = () => onApply?.(hex);
  const pureHue = hsvToHex({ h: hsv.h, s: 1, v: 1 });

  if (!doc || !anchor) return null;

  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label="Выбор цвета"
      className="motion-safe:animate-popover-in rounded-2xl bg-white p-3 shadow-[0_14px_40px_rgba(15,23,42,0.18)] ring-1 ring-slate-200/80"
      style={{
        position: 'fixed',
        top: coords?.top ?? -9999,
        left: coords?.left ?? -9999,
        width: PANEL_WIDTH,
        zIndex: 99999,
        visibility: coords ? 'visible' : 'hidden',
      }}
    >
      <IosSegmented
        value={tab}
        onChange={setTab}
        stretch
        slide
        ariaLabel="Как выбирать цвет"
        options={[
          { value: 'grid', label: 'Сетка' },
          { value: 'spectrum', label: 'Спектр' },
        ]}
      />

      {tab === 'grid' ? (
        <div
          className="mt-3 grid overflow-hidden rounded-xl ring-1 ring-slate-200/70"
          style={{ gridTemplateColumns: `repeat(${GRID_COLUMNS}, minmax(0, 1fr))` }}
          role="listbox"
          aria-label="Готовые оттенки"
        >
          {GRID.flat().map((color) => {
            const picked = color === hex;
            return (
              <button
                key={color}
                type="button"
                role="option"
                aria-selected={picked}
                aria-label={color}
                title={color}
                onClick={() => { setDraft(null); setColor(color); }}
                onDoubleClick={() => onApply?.(color)}
                className="relative aspect-square transition-transform duration-100 hover:z-10 hover:scale-[1.18] hover:rounded-[4px] hover:shadow-[0_2px_6px_rgba(15,23,42,0.25)] focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                style={{ backgroundColor: color }}
              >
                {picked && (
                  <span
                    aria-hidden="true"
                    className={`absolute inset-[2px] rounded-[3px] border-2 ${isLightColor(color) ? 'border-slate-900/70' : 'border-white'}`}
                  />
                )}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <div
            ref={square.ref}
            role="slider"
            tabIndex={0}
            aria-label="Насыщенность и яркость"
            aria-valuetext={`насыщенность ${Math.round(hsv.s * 100)}%, яркость ${Math.round(hsv.v * 100)}%`}
            onPointerDown={square.onPointerDown}
            onPointerMove={square.onPointerMove}
            onKeyDown={onSquareKey}
            className="relative h-[168px] cursor-crosshair touch-none select-none overflow-hidden rounded-xl ring-1 ring-black/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            style={{
              backgroundColor: pureHue,
              backgroundImage: 'linear-gradient(to top, #000, rgba(0,0,0,0)), linear-gradient(to right, #fff, rgba(255,255,255,0))',
            }}
          >
            <Knob color={hex} style={{ left: `${hsv.s * 100}%`, top: `${(1 - hsv.v) * 100}%` }} />
          </div>
          <div
            ref={hueBar.ref}
            role="slider"
            tabIndex={0}
            aria-label="Тон"
            aria-valuemin={0}
            aria-valuemax={360}
            aria-valuenow={Math.round(hsv.h)}
            onPointerDown={hueBar.onPointerDown}
            onPointerMove={hueBar.onPointerMove}
            onKeyDown={onHueKey}
            className="relative h-3.5 cursor-pointer touch-none select-none rounded-full ring-1 ring-black/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            style={{ backgroundImage: 'linear-gradient(to right, #f00, #ff0 16.66%, #0f0 33.33%, #0ff 50%, #00f 66.66%, #f0f 83.33%, #f00)' }}
          >
            <Knob color={pureHue} style={{ left: `${(hsv.h / 360) * 100}%`, top: '50%' }} />
          </div>
        </div>
      )}

      <div className="mt-3 flex items-center gap-2">
        <span
          aria-hidden="true"
          className="h-9 w-9 shrink-0 rounded-[10px] ring-1 ring-inset ring-black/10"
          style={{ backgroundColor: hex }}
        />
        <input
          value={draft ?? hex}
          maxLength={7}
          spellCheck={false}
          autoComplete="off"
          aria-label="Код цвета"
          onChange={(event) => {
            const raw = event.target.value.toUpperCase().replace(/[^#0-9A-F]/g, '').replace(/(?!^)#/g, '');
            const text = (raw.startsWith('#') ? raw : `#${raw}`).slice(0, 7);
            setDraft(text);
            // Цвет меняется, как только набраны все шесть знаков кода.
            if (/^#[0-9A-F]{6}$/.test(text)) setColor(text);
          }}
          onBlur={() => setDraft(null)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            onApply?.(normalizeHex(draft ?? hex) || hex);
          }}
          className="min-w-0 flex-1 rounded-[10px] bg-slate-100 px-3 py-2 font-mono text-[13px] uppercase tracking-wide text-slate-900 outline-none transition focus:bg-white focus:ring-2 focus:ring-blue-500/70"
        />
      </div>

      <div className="mt-3 flex items-center gap-2">
        {onRemove ? (
          <button type="button" className={`${iosBtnGhost} !text-rose-600 hover:!bg-rose-50`} onClick={onRemove}>
            Удалить
          </button>
        ) : (
          <button type="button" className={iosBtnGhost} onClick={() => onClose?.()}>
            Отмена
          </button>
        )}
        <button type="button" className={`${iosBtnPrimary} ml-auto !py-2`} onClick={apply}>
          {applyLabel}
        </button>
      </div>
    </div>,
    doc.body,
  );
}
