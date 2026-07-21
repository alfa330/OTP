import React, { useEffect } from 'react';
import FaIcon from './FaIcon';

/**
 * Полноэкранное окно в стиле macOS/iOS: матовый фон, крупная шапка со скруглённой
 * иконкой, заголовок + подзаголовок, справа — свои действия и кнопка закрытия.
 * Тот же визуальный язык, что у полноэкранной проверки низких оценок.
 *
 * Props:
 *  - open: показывать ли окно
 *  - onClose: закрыть
 *  - icon: класс FontAwesome для иконки в шапке (напр. 'fa-users')
 *  - title, subtitle: тексты шапки
 *  - actions: узлы-кнопки справа (перед крестиком)
 *  - children: содержимое
 *  - z: z-index (по умолчанию 140)
 */
const FullscreenSheet = ({
  open,
  onClose,
  icon = 'fa-square',
  title = '',
  subtitle = '',
  actions = null,
  children,
  z = 140,
}) => {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 flex bg-slate-100/95 backdrop-blur-sm" style={{ zIndex: z }}>
      <div className="flex h-full w-full min-w-0 flex-col overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-slate-900 text-white shadow-sm">
              <FaIcon className={`fas ${icon}`} aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <h3 className="truncate text-base font-semibold text-slate-900">{title}</h3>
              {subtitle ? <p className="truncate text-xs leading-5 text-slate-500">{subtitle}</p> : null}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {actions}
            <button
              type="button"
              onClick={onClose}
              aria-label="Закрыть"
              className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:bg-slate-50 hover:text-slate-700"
            >
              <FaIcon className="fas fa-xmark" aria-hidden="true" />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6 sm:py-6">
          <div className="mx-auto w-full max-w-5xl">{children}</div>
        </div>
      </div>
    </div>
  );
};

export default FullscreenSheet;
