import React, { useEffect, useRef, useState } from 'react';
import { ChevronRight, Loader2 } from 'lucide-react';
import { iosCard, iosGroupLabel } from '../ui/ios';

/*
 * Телефонный вид «Аукциона смен» — только разметка.
 *
 * На компьютере период — сетка «ставки × дни» с ячейками по 64 px, полосой дней
 * у нижней грани и карточкой дня поверх неё. На экране в 390 px это давало семь
 * колонок с подписями «9-18» в 10 px, горизонтальную прокрутку по сетке, полосу
 * дней под баром разделов и плашку статуса под колоколом. Здесь период — полоса
 * дней сверху и список смен выбранного дня, как в календаре телефона.
 *
 * Данные и решения («можно ли взять», «почему нельзя», сколько часов) считает
 * ShiftAuctionView.jsx теми же помощниками, что и сетку: здесь нет ни одного
 * правила аукциона, только то, как строка выглядит. Какой из веток ячейки
 * соответствует строка — решает shiftAuctionPhoneRows.js.
 *
 * ЦВЕТ — утилитами Tailwind, а не своими hex в CSS: тёмный слой портала
 * перекрашивает утилиты, а собственные цвета файла стилей он не видит.
 *
 * ОБЩИЙ СЛОЙ РАЗДЕЛОВ (mobile-shell.css) переносит ряды с gap по строкам, ставит
 * колонкой .flex.items-start с .flex-1 внутри, режет ширину у всего с «w-[» и
 * обнуляет минимальную ширину у всего с «min-w-[». Поэтому строки здесь
 * выровнены по центру (items-center), середина строки — flex-1 с нулевой
 * основой (перенос тогда не срабатывает), минимальные ширины заданы в
 * shift-auction-mobile.css, а лентам, которым переноситься нельзя, перенос
 * запрещён инлайном.
 */

const NO_WRAP = { flexWrap: 'nowrap', scrollbarWidth: 'none' };

// Главные кнопки экранов: во всю ширину и под большой палец — 44 px и больше.
export const AUCTION_PHONE_BUTTON = {
  blue: 'flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 text-[17px] font-semibold text-white transition active:scale-[0.98] disabled:opacity-50',
  orange: 'flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-orange-500 px-4 text-[17px] font-semibold text-white transition active:scale-[0.98] disabled:opacity-50',
  gray: 'flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-slate-200 px-4 text-[17px] font-semibold text-slate-800 transition active:scale-[0.98] disabled:opacity-50',
};

export const AUCTION_PHONE_TIME_INPUT = 'sa-m-time h-9 shrink-0 rounded-lg bg-slate-100 px-3 text-[16px] font-semibold tabular-nums text-slate-900 outline-none focus:ring-2 focus:ring-blue-500/60 disabled:text-slate-400';

/*
 * Группа строк: подпись над карточкой, пояснение под ней — как в настройках
 * телефона. Без строк карточка не рисуется вовсе: у свёрнутой группы остаётся
 * одна подпись с кнопкой справа.
 */
export const AuctionPhoneGroup = ({ label, right = null, hint = null, children, className = '' }) => {
  const hasRows = React.Children.toArray(children).length > 0;
  return (
    <section className={`sa-m-group ${className}`}>
      {label || right ? (
        <div className="mb-1.5 flex items-center gap-2 px-1">
          {label ? <h2 className={`${iosGroupLabel} sa-m-group__label min-w-0 flex-1 truncate`}>{label}</h2> : <span className="flex-1" />}
          {right}
        </div>
      ) : null}
      {hasRows ? <div className={`${iosCard} overflow-hidden`}>{children}</div> : null}
      {hint ? <p className="mt-1.5 px-1 text-[13px] leading-snug text-slate-500">{hint}</p> : null}
    </section>
  );
};

/*
 * Строка списка. Кнопкой становится вся строка, только когда у неё нет своей
 * кнопки справа: кнопка внутри кнопки — недопустимая разметка, и нажатие на
 * «Взять» заодно открывало бы строку.
 */
export const AuctionPhoneRow = ({
  title,
  subtitle = null,
  note = null,
  leading = null,
  trailing = null,
  onClick = null,
  muted = false,
  chevron = false,
  ariaLabel,
}) => {
  const body = (
    <>
      {leading}
      <span className="min-w-0 flex-1">
        <span className={`block truncate text-[16px] font-semibold tabular-nums ${muted ? 'text-slate-400' : 'text-slate-900'}`}>
          {title}
        </span>
        {subtitle ? <span className="block truncate text-[13px] text-slate-500">{subtitle}</span> : null}
        {note ? <span className="mt-0.5 block text-[13px] leading-snug text-slate-500">{note}</span> : null}
      </span>
      {trailing}
      {chevron ? <ChevronRight size={18} className="shrink-0 text-slate-300" aria-hidden="true" /> : null}
    </>
  );
  const className = 'sa-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left';
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={className} aria-label={ariaLabel}>
        {body}
      </button>
    );
  }
  return <div className={className}>{body}</div>;
};

const PILL_TONES = {
  blue: 'bg-blue-600 text-white',
  orange: 'bg-orange-500 text-white',
  rose: 'bg-rose-50 text-rose-600',
};

export const AuctionPhonePill = ({ label, tone = 'blue', onClick, busy = false, disabled = false, ariaLabel }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled || busy}
    aria-label={ariaLabel}
    className={`sa-m-pill flex h-8 shrink-0 items-center justify-center rounded-full px-3.5 text-[15px] font-semibold transition disabled:opacity-60 ${PILL_TONES[tone] || PILL_TONES.blue}`}
  >
    {busy ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : label}
  </button>
);

// Строка-переход со значком в цветной плитке — как пункт настроек телефона.
export const AuctionPhoneLinkRow = ({ icon: Icon, tileClassName = 'bg-blue-500', title, subtitle = null, onClick, disabled = false }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    className="sa-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left disabled:opacity-50"
  >
    <span className={`grid h-[30px] w-[30px] shrink-0 place-items-center rounded-lg text-white ${tileClassName}`}>
      <Icon size={17} aria-hidden="true" />
    </span>
    <span className="min-w-0 flex-1">
      <span className="block truncate text-[16px] text-slate-900">{title}</span>
      {subtitle ? <span className="block truncate text-[13px] text-slate-500">{subtitle}</span> : null}
    </span>
    <ChevronRight size={18} className="shrink-0 text-slate-300" aria-hidden="true" />
  </button>
);

const STATUS_DOTS = {
  open: 'bg-emerald-500',
  scheduled: 'bg-blue-500',
  paused: 'bg-amber-500',
  closed: 'bg-slate-400',
  disabled: 'bg-amber-500',
};

/*
 * Статус и часы одной карточкой. На компьютере это плашка, прибитая к правому
 * верхнему углу экрана; на телефоне в том же углу висит колокол, и плашка
 * уходила под него вместе с «осталось N ч».
 */
export const AuctionPhoneStatusCard = ({ status = 'closed', label, detail = null, period = '', workload = null, notes = [], alert = null }) => (
  <div className={`${iosCard} px-4 py-3`}>
    <div className="flex items-center gap-2">
      <span className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOTS[status] || STATUS_DOTS.closed}`} aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate text-[16px] font-semibold text-slate-900">{label}</span>
    </div>
    {/* Отсчёт — второй строкой: в одной строке с «Аукцион открыт» отсчёт на
        дни («до закрытия 2 д 11:28:33») обрезал сам статус до «Аукцион отк…». */}
    {detail || period ? (
      <div className="mt-0.5 pl-4 text-[14px] tabular-nums text-slate-500">
        {detail}
        {detail && period ? ' · ' : ''}
        {period}
      </div>
    ) : null}
    {workload ? (
      <div className="mt-3">
        <div className="flex items-baseline gap-1.5">
          <span className="text-[24px] font-semibold leading-none tabular-nums text-slate-900">{workload.claimed}</span>
          <span className="text-[15px] text-slate-500">из {workload.ceiling} ч</span>
          <span className={`ml-auto shrink-0 text-[14px] font-medium tabular-nums ${workload.balanceClassName}`}>{workload.balance}</span>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
          <div className={`h-full rounded-full ${workload.barClassName}`} style={{ width: `${workload.progress}%` }} />
        </div>
        {workload.caption ? <div className="mt-1.5 text-[13px] text-slate-500">{workload.caption}</div> : null}
      </div>
    ) : null}
    {notes.filter(Boolean).map((note, index) => (
      // Заметки статичны и не переставляются — индекс здесь честный ключ.
      // eslint-disable-next-line react/no-array-index-key
      <p key={index} className="mt-2 border-t border-slate-100 pt-2 text-[14px] leading-snug text-slate-600">{note}</p>
    ))}
    {alert}
  </div>
);

const DAY_CAPTION_TONES = {
  shift: 'text-emerald-600',
  off: 'text-blue-600',
  blocked: 'text-rose-600',
  done: 'text-emerald-600',
  none: 'text-slate-500',
};

/*
 * Полоса дней периода. Неделя помещается целиком (день — седьмая часть ширины),
 * длинный период прокручивается вбок, выбранный день подъезжает в середину.
 * Полоса липкая: в дне бывает за три десятка смен, и возвращаться к ней
 * прокруткой наверх — лишний ход ради каждого переключения дня.
 */
export const AuctionPhoneDayStrip = ({ days = [], activeDate, onSelect, header = null }) => {
  const stripRef = useRef(null);
  const scrollRef = useRef(null);
  const firstRunRef = useRef(true);
  const [stuck, setStuck] = useState(false);
  const [viewportTick, setViewportTick] = useState(0);
  const hasDays = days.length > 0;

  /*
   * Прилипла ли полоса. Над прилипшей полосой остаётся полоса под колокол
   * (46 px + вырез), и страница ехала бы там на виду — заголовки и кнопки
   * просвечивали над днями. Прилипнув, полоса продолжает стекло до верхней
   * грани экрана (слой .sa-m-strip__glass в shift-auction-mobile.css).
   *
   * Наблюдатель за самой полосой, а не обработчик прокрутки: линия прилипания
   * сдвинута внутрь на пиксель, и у прилипшей полосы верхний край оказывается за
   * ней — видимая доля падает ниже единицы. Доля падает и у полосы, уехавшей за
   * НИЖНИЙ край, поэтому второе условие — её верх уже на линии.
   */
  useEffect(() => {
    const strip = stripRef.current;
    if (!strip || typeof IntersectionObserver === 'undefined') return undefined;
    // Линию берём у самой полосы: в CSS она посчитана с вырезом экрана, а
    // пробником вырез из JS пришлось бы мерить заново.
    const stickTop = parseFloat(window.getComputedStyle(strip).top) || 0;
    const observer = new IntersectionObserver(([entry]) => {
      setStuck(entry.intersectionRatio < 1 && entry.boundingClientRect.top <= stickTop + 1);
    }, { rootMargin: `-${Math.ceil(stickTop) + 1}px 0px 0px 0px`, threshold: [1] });
    observer.observe(strip);
    return () => observer.disconnect();
  }, [hasDays, viewportTick]);

  // Поворот меняет вырез, а с ним и линию прилипания — наблюдатель пересоздаётся.
  useEffect(() => {
    const handle = () => setViewportTick((value) => value + 1);
    window.addEventListener('orientationchange', handle);
    return () => window.removeEventListener('orientationchange', handle);
  }, []);

  useEffect(() => {
    const strip = scrollRef.current;
    const node = strip?.querySelector('[aria-selected="true"]');
    if (!strip || !node) return;
    // Двигаем только саму полосу: scrollIntoView прокрутил бы заодно страницу,
    // и выбор дня уводил бы человека от списка к полосе.
    const target = node.offsetLeft - (strip.clientWidth - node.offsetWidth) / 2;
    const left = Math.max(0, Math.min(target, strip.scrollWidth - strip.clientWidth));
    // Первое появление — без анимации: полоса не должна «ехать» при входе в раздел.
    if (typeof strip.scrollTo === 'function') {
      strip.scrollTo({ left, behavior: firstRunRef.current ? 'auto' : 'smooth' });
    } else {
      strip.scrollLeft = left;
    }
    firstRunRef.current = false;
  }, [activeDate, days.length]);

  if (!days.length) return null;
  return (
    <div ref={stripRef} className="sa-m-strip" data-stuck={stuck ? '' : undefined}>
      <span className="sa-m-strip__glass" aria-hidden="true" />
      {/* Строка над днями («‹ 8 — 14 сентября ›» в «Моих сменах») липнет
          вместе с ними: листать недели, вернувшись к верху страницы, —
          лишний ход. У аукциона её нет: период там выбирают чипами. */}
      {header}
      <div ref={scrollRef} role="tablist" aria-label="Дни периода" className="sa-m-strip__scroll flex gap-1 overflow-x-auto px-3 py-1.5" style={NO_WRAP}>
        {days.map((day) => {
          const active = day.date === activeDate;
          return (
            <button
              key={day.date}
              type="button"
              role="tab"
              aria-selected={active}
              aria-label={day.ariaLabel}
              onClick={() => onSelect?.(day.date)}
              className={`sa-m-day flex flex-col items-center rounded-2xl px-0.5 pb-1.5 pt-1 transition-colors ${active ? 'bg-blue-600 text-white' : 'text-slate-900'}`}
            >
              <span className={`text-[12px] font-medium ${active ? 'text-white/80' : 'text-slate-500'}`}>{day.weekday}</span>
              <span className="text-[19px] font-semibold leading-6 tabular-nums">{day.dayNumber}</span>
              <span className={`max-w-full truncate text-[12px] font-medium leading-4 tabular-nums ${active ? 'text-white' : (DAY_CAPTION_TONES[day.tone] || DAY_CAPTION_TONES.none)}`}>
                {day.caption || ' '}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
};

// Ряд «чипов» — периоды аукциона. Ряд не переносится: периодов бывает полтора
// десятка, и перенос занял бы полэкрана над списком смен.
export const AuctionPhoneChips = ({ items = [], onSelect, ariaLabel }) => {
  const scrollRef = useRef(null);
  const activeId = items.find((item) => item.active)?.id;

  useEffect(() => {
    const strip = scrollRef.current;
    const node = strip?.querySelector('[aria-pressed="true"]');
    if (!strip || !node) return;
    strip.scrollLeft = Math.max(0, node.offsetLeft - (strip.clientWidth - node.offsetWidth) / 2);
  }, [activeId]);

  if (!items.length) return null;
  return (
    <div ref={scrollRef} role="group" aria-label={ariaLabel} className="sa-m-chips flex gap-2 overflow-x-auto" style={NO_WRAP}>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          aria-pressed={item.active}
          onClick={() => onSelect?.(item)}
          disabled={item.disabled}
          className={`flex shrink-0 flex-col rounded-2xl px-3.5 py-1.5 text-left transition disabled:opacity-60 ${
            item.active ? 'bg-slate-900 text-white' : 'bg-white text-slate-800 ring-1 ring-slate-200'
          }`}
        >
          <span className="whitespace-nowrap text-[15px] font-semibold">{item.label}</span>
          {item.caption ? (
            <span className={`whitespace-nowrap text-[12px] ${item.active ? 'text-white/70' : 'text-slate-500'}`}>{item.caption}</span>
          ) : null}
        </button>
      ))}
    </div>
  );
};

// Поле формы строкой: подпись слева, значение справа — как в настройках телефона.
export const AuctionPhoneField = ({ label, children }) => (
  <label className="sa-m-row flex w-full items-center gap-3 px-4 py-2">
    <span className="min-w-0 flex-1 text-[16px] text-slate-900">{label}</span>
    {children}
  </label>
);

/*
 * Последнее непустое значение. Экран и лист уезжают ещё треть секунды после
 * того, как раздел очистил выбранную смену, и без этого уезжали бы пустыми.
 */
export const useLastPresent = (value) => {
  const ref = useRef(value);
  if (value) ref.current = value;
  return value || ref.current;
};
