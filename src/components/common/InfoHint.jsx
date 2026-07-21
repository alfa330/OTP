import React, { useEffect, useId, useRef, useState } from 'react';
import FaIcon from './FaIcon';

/**
 * «i» в кружке: прячет пояснительный текст, чтобы он не занимал пол-экрана.
 * По клику разворачивается лёгкий поповер в стиле macOS/iOS (матовый фон,
 * скруглённые углы, мягкая тень). Закрывается кликом вне и по Esc.
 *
 * Props:
 *  - title: заголовок поповера (необязательно)
 *  - children: содержимое подсказки
 *  - side: 'left' | 'right' — к какому краю прижать поповер (по умолчанию right)
 */
const InfoHint = ({ title = '', children, side = 'right', className = '' }) => {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const popId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const onDocClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <span ref={wrapRef} className={`relative inline-flex ${className}`}>
      <button
        type="button"
        aria-label="Подробнее"
        aria-expanded={open}
        aria-controls={popId}
        onClick={() => setOpen((v) => !v)}
        className={`inline-flex h-5 w-5 items-center justify-center rounded-full border text-[11px] transition ${
          open
            ? 'border-indigo-400 bg-indigo-500 text-white shadow-sm'
            : 'border-slate-300 bg-white text-slate-500 hover:border-indigo-300 hover:text-indigo-600'
        }`}
      >
        <FaIcon className="fas fa-circle-info" aria-hidden="true" />
      </button>
      {open && (
        <div
          id={popId}
          role="tooltip"
          className={`absolute top-7 z-[200] w-72 max-w-[85vw] rounded-2xl border border-slate-200/80 bg-white/95 p-3.5 text-left text-xs leading-5 text-slate-600 shadow-xl ring-1 ring-black/5 backdrop-blur ${
            side === 'left' ? 'left-0' : 'right-0'
          }`}
        >
          {title ? <div className="mb-1 text-[13px] font-semibold text-slate-800">{title}</div> : null}
          {children}
        </div>
      )}
    </span>
  );
};

export default InfoHint;
