import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import FaIcon from './FaIcon';
import { SCREEN_LEAVE_MS, WINDOW_LEAVE_MS, prefersReducedMotion } from '../ui/ios';
import useIsMobileShell from './useIsMobileShell';
import useScreenBackGesture from './useScreenBackGesture';

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
  /* Системное «назад» закрывает окно — только на телефоне, см. IosModal. */
  const isNarrowShell = useIsMobileShell();
  useScreenBackGesture(isNarrowShell && open, onClose);

  /* Уход, как у IosModal: разметка живёт, пока окно гаснет (компьютер) или
     уезжает экраном (телефон, mobile-shell.css). Работает, когда окно
     закрывают через open={false}; снятое вместе с родителем окно уйти
     анимацией не может — ему не в чем проиграть движение. */
  const [leavingState, setLeaving] = useState(false);
  const wasOpen = useRef(open);
  const delay = isNarrowShell ? SCREEN_LEAVE_MS : (prefersReducedMotion() ? 0 : WINDOW_LEAVE_MS);
  useEffect(() => {
    if (open) { wasOpen.current = true; setLeaving(false); return undefined; }
    if (!wasOpen.current || !delay) { wasOpen.current = false; setLeaving(false); return undefined; }
    wasOpen.current = false;
    setLeaving(true);
    const timer = setTimeout(() => setLeaving(false), delay);
    return () => clearTimeout(timer);
  }, [open, delay]);
  // В такт закрытия окно уже уходит — иначе оно на кадр снималось бы и рисовалось заново (см. IosModal).
  const leaving = leavingState || (!open && wasOpen.current && delay > 0);

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

  /* Пока окно уходит, в нём остаётся то, что было в момент закрытия: хозяин окна
     мог уже сбросить данные, и иначе уходило бы пустое полотно. */
  const kept = useRef(null);
  if (open) kept.current = { icon, title, subtitle, actions, children };
  const shown = !open && kept.current ? kept.current : { icon, title, subtitle, actions, children };

  if (!open && !leaving) return null;

  /* Компьютер: подложка проявляется, полотно приподнимается — как окна IosModal.
     Телефон: экран въезжает и уезжает сам (mobile-shell.css), второго движения нет. */
  const sheetMotion = isNarrowShell ? '' : (leaving ? ' pointer-events-none motion-safe:animate-window-dim-out' : ' motion-safe:animate-window-dim-in');
  const bodyMotion = isNarrowShell ? '' : (leaving ? ' motion-safe:animate-window-out' : ' motion-safe:animate-window-in');

  return (
    <div
      /* otp-modal-root — метка «поверх всего окна» для мобильной оболочки:
         пока окно открыто, угловой колокол прячется, иначе он висел бы поверх
         шапки и накрывал крестик закрытия (см. mobile-shell.css). */
      /* На телефоне полотно непрозрачное: окно там — экран во весь экран, и сквозь
         95 % подложки проступал раздел под ним («Задачи» за «Заметками»). */
      className={`otp-modal-root fixed inset-0 flex ${isNarrowShell ? 'bg-slate-100' : 'bg-slate-100/95 backdrop-blur-sm'}${leaving ? ' is-leaving' : ''}${sheetMotion}`}
      style={{
        zIndex: z,
        // Сдвиг вправо от сайдбара; анимация та же, что у отступа контента.
        ...(offsetLeft ? { left: offsetLeft, transition: 'left 0.3s ease' } : null),
      }}
    >
      <div className={`flex h-full w-full min-w-0 flex-col overflow-hidden${bodyMotion}`}>
        {/* otp-modal-head — метка для мобильной оболочки: там окно едет
             экраном во весь экран, и шапке нужен отступ под вырез. */}
        {isNarrowShell ? (
          /* Телефон: шапка экрана, как у IosModal, — шеврон «Назад» слева,
             заголовок, свои действия справа. Плитка значка и крестик справа на
             390 px не помещались в строку, и крестик падал второй строкой под
             заголовок. Запрет переноса — инлайном: общий слой разделов
             (mobile-shell.css) переносит ряды с gap-* правилом весом (0,6,1),
             и классом его не перебить. */
          <div
            className="otp-modal-head flex items-center gap-1 border-b border-slate-200 bg-white/90 px-2 py-2 backdrop-blur"
            style={{ flexWrap: 'nowrap' }}
          >
            <button
              type="button"
              onClick={onClose}
              aria-label="Назад"
              className="otp-modal-back grid h-9 w-9 shrink-0 place-items-center text-blue-600 active:opacity-60"
            >
              <svg width="17" height="17" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M10 2.5L4.5 8l5.5 5.5" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </button>
            <div className="min-w-0 flex-1">
              <h3 className="truncate text-[15px] font-semibold text-slate-900">{shown.title}</h3>
              {shown.subtitle ? <p className="truncate text-[12px] text-slate-500">{shown.subtitle}</p> : null}
            </div>
            {shown.actions ? <div className="flex shrink-0 items-center gap-2">{shown.actions}</div> : null}
          </div>
        ) : (
          <div className="otp-modal-head flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-slate-900 text-white shadow-sm">
                <FaIcon className={`fas ${shown.icon}`} aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <h3 className="truncate text-base font-semibold text-slate-900">{shown.title}</h3>
                {shown.subtitle ? <p className="truncate text-xs leading-5 text-slate-500">{shown.subtitle}</p> : null}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {shown.actions}
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
        )}
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6 sm:py-6">
          <div className={`mx-auto w-full ${wide ? '' : 'max-w-5xl'}`}>{shown.children}</div>
        </div>
      </div>
    </div>
  );
};

export default FullscreenSheet;
