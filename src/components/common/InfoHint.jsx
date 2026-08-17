import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from './FaIcon';

/**
 * «i» в кружке: прячет пояснение, чтобы оно не занимало место постоянно.
 *
 * Две вещи здесь сделаны не «как проще», а по следам реальных жалоб.
 *
 * 1. ОТКРЫВАЕТСЯ ПО НАВЕДЕНИЮ. Подсказка на то и подсказка, что читается
 *    мимоходом: требовать клик — значит требовать решения «стоит ли оно того»
 *    ещё до того, как человек увидел текст. Клик тоже работает и нужен по делу:
 *    на телефоне наведения не существует вовсе, и подсказка, живущая только на
 *    hover, там недоступна.
 *
 * 2. РИСУЕТСЯ ПОРТАЛОМ В BODY с координатами от кнопки. Раньше поповер лежал
 *    внутри потока с absolute — и внутри модалки её же скроллящееся тело его
 *    обрезало, а раскрытие раздвигало границы окна. С fixed-координатами от
 *    getBoundingClientRect он не влияет на разметку вообще: ничего не двигается
 *    и ничего не обрезается.
 *
 * Координаты живут ровно пока поповер открыт: при скролле и ресайзе они
 * устаревают, поэтому там подсказка закрывается, а не уезжает от своей кнопки.
 *
 * Props:
 *  - title: заголовок (необязательно)
 *  - text: текст подсказки строкой (альтернатива children)
 *  - children: содержимое подсказки
 *  - side: 'left' | 'right' — к какому краю кнопки прижимать
 */

// Потолок ширины, а не ширина: подсказка «Ровно 12 цифр» в пузыре на 288 пикселей
// выглядит как ошибка вёрстки. Реальная ширина подгоняется под текст, а по этому
// значению считается безопасное положение до того, как пузырь измерен.
const MAX_WIDTH = 288;
const MARGIN = 8;
// Задержка перед закрытием: без неё поповер исчезает, пока курсор до него едет.
const CLOSE_DELAY_MS = 120;

const InfoHint = ({ title = '', text = '', children, side = 'right', className = '' }) => {
  const [pos, setPos] = useState(null);
  const btnRef = useRef(null);
  const closeTimer = useRef(null);
  const popId = useId();
  const open = pos !== null;

  const cancelClose = useCallback(() => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  const close = useCallback(() => {
    cancelClose();
    setPos(null);
  }, [cancelClose]);

  const closeSoon = useCallback(() => {
    cancelClose();
    closeTimer.current = setTimeout(() => setPos(null), CLOSE_DELAY_MS);
  }, [cancelClose]);

  const place = useCallback(() => {
    const el = btnRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    // По горизонтали прижимаем к нужному краю кнопки и не даём вылезти за экран.
    const raw = side === 'left' ? rect.left : rect.right - MAX_WIDTH;
    const left = Math.max(MARGIN, Math.min(raw, window.innerWidth - MAX_WIDTH - MARGIN));
    // Сверху места мало — разворачиваем вниз.
    const below = window.innerHeight - rect.bottom;
    const flipUp = below < 180 && rect.top > below;
    cancelClose();
    setPos({ left, top: flipUp ? rect.top - MARGIN : rect.bottom + MARGIN, flipUp });
  }, [side, cancelClose]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => { if (event.key === 'Escape') close(); };
    // capture: подсказка должна закрыться при скролле ЛЮБОГО контейнера,
    // а не только окна — внутри модалки скроллится её тело.
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, close]);

  useEffect(() => cancelClose, [cancelClose]);

  /* Ширину пузырь берёт по содержимому, а знаем мы её только после отрисовки.
     Поэтому позиция уточняется один раз: прижать к правому краю кнопки и не
     дать вылезти за экран можно только по фактическому размеру. */
  const popRef = useRef(null);
  useLayoutEffect(() => {
    if (!pos || pos.measured) return;
    const pop = popRef.current;
    const btn = btnRef.current;
    if (!pop || !btn) return;
    const width = pop.getBoundingClientRect().width;
    const rect = btn.getBoundingClientRect();
    const raw = side === 'left' ? rect.left : rect.right - width;
    const left = Math.max(MARGIN, Math.min(raw, window.innerWidth - width - MARGIN));
    if (Math.abs(left - pos.left) > 1) setPos({ ...pos, left, measured: true });
    else setPos({ ...pos, measured: true });
  }, [pos, side]);

  const body = text || children;

  return (
    <span className={`relative inline-flex ${className}`}>
      <button
        ref={btnRef}
        type="button"
        aria-label="Подробнее"
        aria-expanded={open}
        aria-controls={popId}
        onMouseEnter={place}
        onMouseLeave={closeSoon}
        onFocus={place}
        onBlur={close}
        onClick={(event) => {
          // Клик — для тач-устройств и клавиатуры: там наведения нет.
          event.preventDefault();
          if (open) close(); else place();
        }}
        className={`inline-flex h-5 w-5 items-center justify-center rounded-full border text-[11px] transition ${
          open
            ? 'border-indigo-400 bg-indigo-500 text-white shadow-sm'
            : 'border-slate-300 bg-white text-slate-500 hover:border-indigo-300 hover:text-indigo-600'
        }`}
      >
        <FaIcon className="fas fa-circle-info" aria-hidden="true" />
      </button>
      {open && typeof document !== 'undefined' && createPortal(
        <div
          ref={popRef}
          id={popId}
          role="tooltip"
          onMouseEnter={cancelClose}
          onMouseLeave={closeSoon}
          style={{
            position: 'fixed',
            left: pos.left,
            top: pos.top,
            width: 'max-content',
            maxWidth: `min(${MAX_WIDTH}px, calc(100vw - 16px))`,
            transform: pos.flipUp ? 'translateY(-100%)' : undefined,
          }}
          className="z-[200] rounded-2xl border border-slate-200/80 bg-white/95 p-3.5 text-left text-xs leading-5 text-slate-600 shadow-xl ring-1 ring-black/5 backdrop-blur"
        >
          {title ? <div className="mb-1 text-[13px] font-semibold text-slate-800">{title}</div> : null}
          {body}
        </div>,
        document.body,
      )}
    </span>
  );
};

export default InfoHint;
