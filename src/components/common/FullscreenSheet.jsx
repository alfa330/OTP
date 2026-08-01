import React, { useEffect, useLayoutEffect } from 'react';
import FaIcon from './FaIcon';

/* Сколько окон сейчас стоит рядом с сайдбаром (offsetLeft). Считаем, а не держим
   флаг: закрытие одного окна не должно снимать класс, пока открыто другое. */
let sheetsBesideSidebar = 0;

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
 *  - wide: снять ограничение ширины контента (для таблиц/таймлайнов на весь экран)
 *  - closeOnEscape: реагировать на Esc (выключают, когда поверх окна открыто своё
 *    окно — например карточка задачи: Esc должен закрывать её, а не оба слоя)
 *  - offsetLeft: отступ слева, чтобы окно заняло только область контента и не
 *    накрывало сайдбар приложения (например 'var(--app-sidebar-offset, 0px)')
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
  wide = false,
  closeOnEscape = true,
  offsetLeft = null,
}) => {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape' && closeOnEscape) onClose?.();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose, closeOnEscape]);

  /* Окно рисуется выше сайдбара, поэтому, встав рядом с ним, оно всё равно
     накрывало бы то, что выходит за его ширину: кнопку сворачивания (она висит
     на -right-4) и свёрнутый сайдбар, который разворачивается по наведению.
     Пока такое окно открыто, класс поднимает сайдбар над ним — см. src/styles.css.
     Именно layout-эффект: сдвиг окна попадает в первый же кадр (инлайновый стиль),
     и с обычным useEffect этот кадр рисовался бы с сайдбаром ещё под окном —
     кнопка сворачивания на миг пропадала бы. */
  useLayoutEffect(() => {
    if (!open || !offsetLeft) return undefined;
    sheetsBesideSidebar += 1;
    document.body.classList.add('sheet-beside-sidebar');
    return () => {
      sheetsBesideSidebar = Math.max(0, sheetsBesideSidebar - 1);
      if (sheetsBesideSidebar === 0) document.body.classList.remove('sheet-beside-sidebar');
    };
  }, [open, offsetLeft]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 flex bg-slate-100/95 backdrop-blur-sm"
      style={{
        zIndex: z,
        // Сдвиг вправо от сайдбара; анимация та же, что у отступа контента.
        ...(offsetLeft ? { left: offsetLeft, transition: 'left 0.3s ease' } : null),
      }}
    >
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
          <div className={`mx-auto w-full ${wide ? '' : 'max-w-5xl'}`}>{children}</div>
        </div>
      </div>
    </div>
  );
};

export default FullscreenSheet;
