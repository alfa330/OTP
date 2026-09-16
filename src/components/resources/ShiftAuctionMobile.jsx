import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronLeft, ChevronRight, Loader2, Plus } from 'lucide-react';
import { iosCard, iosGroupLabel } from '../ui/ios';

/*
 * Телефонный вид «Аукциона смен» — только разметка.
 *
 * На компьютере период — сетка «ставки × дни» с ячейками по 64 px, полосой дней
 * у нижней грани и карточкой дня поверх неё. На экране в 390 px это давало семь
 * колонок с подписями «9-18» в 10 px, горизонтальную прокрутку по сетке, полосу
 * дней под баром разделов и плашку статуса под колоколом. Здесь период — та же
 * сетка, собранная под палец (AuctionPhoneGrid): неделя в ширину экрана, смена —
 * ячейка с началом и концом в две строки, шапка дней и часы оператора липнут
 * сверху, смена и день открываются нажатием. (Были и списки — один день, потом
 * вся неделя; владелец 15.09.2026: «сделай как на сайте, чтобы смены
 * отображались ячейками», «норма за неделю должна быть закреплена».)
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
 * Статус аукциона карточкой. На компьютере это плашка, прибитая к правому
 * верхнему углу экрана; на телефоне в том же углу висит колокол, и плашка
 * уходила под него. Часы оператора здесь не повторяются — они закреплены над
 * полосой дней (AuctionPhoneNormBar).
 */
export const AuctionPhoneStatusCard = ({ status = 'closed', label, detail = null, period = '', notes = [], alert = null }) => (
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
const useAuctionPhoneStuck = (stripRef, enabled) => {
  const [stuck, setStuck] = useState(false);
  const [viewportTick, setViewportTick] = useState(0);

  useEffect(() => {
    const strip = stripRef.current;
    if (!enabled || !strip || typeof IntersectionObserver === 'undefined') return undefined;
    // Линию берём у самой полосы: в CSS она посчитана с вырезом экрана, а
    // пробником вырез из JS пришлось бы мерить заново.
    const stickTop = parseFloat(window.getComputedStyle(strip).top) || 0;
    const observer = new IntersectionObserver(([entry]) => {
      setStuck(entry.intersectionRatio < 1 && entry.boundingClientRect.top <= stickTop + 1);
    }, { rootMargin: `-${Math.ceil(stickTop) + 1}px 0px 0px 0px`, threshold: [1] });
    observer.observe(strip);
    return () => observer.disconnect();
  }, [enabled, stripRef, viewportTick]);

  // Поворот меняет вырез, а с ним и линию прилипания — наблюдатель пересоздаётся.
  useEffect(() => {
    const handle = () => setViewportTick((value) => value + 1);
    window.addEventListener('orientationchange', handle);
    return () => window.removeEventListener('orientationchange', handle);
  }, []);

  return stuck;
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
  const hasDays = days.length > 0;
  const stuck = useAuctionPhoneStuck(stripRef, hasDays);

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

/*
 * Часы оператора строкой над полосой дней: сколько набрано, полоса, сколько
 * осталось. Липнет вместе с днями — владелец 15.09.2026: оператор должен видеть,
 * сколько ему ещё взять, пока листает неделю, а не возвращаться за этим к
 * карточке статуса наверх.
 */
export const AuctionPhoneNormBar = ({ claimed, ceiling, balance, balanceClassName = 'text-slate-500', barClassName = 'bg-blue-600', progress = 0 }) => (
  <div className="sa-m-norm flex h-11 items-center gap-3 px-4" style={NO_WRAP}>
    <span className="shrink-0 whitespace-nowrap text-[15px] tabular-nums text-slate-500">
      <b className="text-[17px] font-semibold text-slate-900">{claimed}</b> из {ceiling} ч
    </span>
    <span className="sa-m-norm__track h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200" aria-hidden="true">
      <span className={`block h-full rounded-full ${barClassName}`} style={{ width: `${progress}%` }} />
    </span>
    <span className={`shrink-0 whitespace-nowrap text-[14px] font-medium tabular-nums ${balanceClassName}`}>{balance}</span>
  </div>
);

// Неделя в ширину экрана; период длиннее едет вбок: колонка уже 48 px не читается.
const GRID_MAX_FIT_DAYS = 7;
const GRID_COLUMN_MIN = 48;
const GRID_GAP = 4;

const CELL_MARKERS = {
  parts: 'bg-white ring-1 ring-orange-600',
  added: 'bg-violet-600 ring-1 ring-white',
  self: 'bg-teal-500 ring-1 ring-white',
};

// Выходной и закрытый статусом день подкрашены во всю колонку, как на сайте.
const COLUMN_HEAD_TONES = { off: 'bg-blue-50', blocked: 'bg-rose-50' };
const COLUMN_EMPTY_TONES = { off: 'border-blue-100 bg-blue-50/70', blocked: 'border-rose-100 bg-rose-50/70' };

/*
 * Смена-ячейка: начало крупно, конец под ним. «9-18» одной строкой в колонку
 * шириной в палец не помещается даже кеглем 10 px — так и выглядела сетка сайта
 * на телефоне. Цвет приходит из раздела готовым, той же шкалой, что у ячейки на
 * сайте; метка в углу — смена разобрана частями, добавлена руками или своя.
 */
export const AuctionPhoneCell = ({ start, end, style = undefined, className = '', marker = null, busy = false, onClick, ariaLabel }) => (
  <button
    type="button"
    onClick={onClick}
    aria-label={ariaLabel}
    className={`sa-m-cell relative flex h-11 w-full flex-col items-center justify-center overflow-hidden rounded-lg border tabular-nums leading-none transition active:scale-95 ${className}`}
    style={style}
  >
    {busy ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : (
      <>
        <span className="text-[13px] font-semibold">{start}</span>
        <span className="mt-1 text-[11px] font-medium opacity-80">{end}</span>
      </>
    )}
    {marker ? (
      <span className={`pointer-events-none absolute right-1 top-1 h-1.5 w-1.5 rounded-full ${CELL_MARKERS[marker] || ''}`} aria-hidden="true" />
    ) : null}
  </button>
);

/*
 * Неделя сеткой, как на сайте: столбцы — дни, строки — смены по ставкам. Шапка
 * дней липнет под стеклом вместе с часами оператора (header) — это та же
 * полоса .sa-m-strip, только дни в ней стоят ровно над своими колонками.
 * Нажатие на день открывает день, на ячейку — смену; решения о том, что именно
 * откроется, принимает раздел.
 *
 * Шапка и сетка — разные прокрутки: общая горизонтальная прокрутка стала бы
 * окном прокрутки и для вертикали, и шапка перестала бы липнуть. Поэтому у
 * периода длиннее недели они едут вбок вместе, по синхронизации.
 */
export const AuctionPhoneGrid = ({ days = [], groups = [], header = null, onDaySelect, onAdd = null }) => {
  const stripRef = useRef(null);
  const headRef = useRef(null);
  const bodyRef = useRef(null);
  const echoRef = useRef(null);
  const hasDays = days.length > 0;
  const stuck = useAuctionPhoneStuck(stripRef, hasDays);
  if (!hasDays) return null;

  const scrolls = days.length > GRID_MAX_FIT_DAYS;
  const track = {
    display: 'grid',
    gridTemplateColumns: scrolls ? `repeat(${days.length}, ${GRID_COLUMN_MIN}px)` : `repeat(${days.length}, minmax(0, 1fr))`,
    gap: GRID_GAP,
  };
  const scrollStyle = scrolls ? { overflowX: 'auto', scrollbarWidth: 'none' } : undefined;
  // Запись scrollLeft во вторую прокрутку возвращается её же событием — эхо пропускаем.
  const follow = (source, target) => {
    if (!source || !target) return;
    if (echoRef.current === source) {
      echoRef.current = null;
      return;
    }
    if (Math.abs(target.scrollLeft - source.scrollLeft) < 1) return;
    echoRef.current = target;
    target.scrollLeft = source.scrollLeft;
  };

  return (
    <div className="sa-m-grid">
      {/* Со строкой часов шапка прилипает к вырезу экрана, в строку колокола
          (sa-m-strip--bell): пустые 46 px стекла над часами владелец назвал
          слишком большой «чёлкой». Без неё дни встали бы под колокол. */}
      <div ref={stripRef} className={header ? 'sa-m-strip sa-m-strip--bell' : 'sa-m-strip'} data-stuck={stuck ? '' : undefined}>
        <span className="sa-m-strip__glass" aria-hidden="true" />
        {header}
        <div
          ref={headRef}
          className="sa-m-grid__scroll px-4 pb-1.5 pt-0.5"
          style={scrollStyle}
          onScroll={scrolls ? () => follow(headRef.current, bodyRef.current) : undefined}
        >
          <div style={track}>
            {days.map((day) => (
              <button
                key={day.date}
                type="button"
                onClick={() => onDaySelect?.(day.date)}
                aria-label={day.ariaLabel}
                className={`sa-m-grid__day flex w-full min-w-0 flex-col items-center rounded-xl py-0.5 ${COLUMN_HEAD_TONES[day.columnTone] || ''}`}
              >
                <span className={`text-[11px] font-medium leading-[14px] ${day.isToday ? 'text-blue-600' : 'text-slate-500'}`}>{day.weekday}</span>
                <span className={`grid h-6 w-6 place-items-center rounded-full text-[15px] font-semibold tabular-nums ${day.isToday ? 'bg-blue-600 text-white' : 'text-slate-900'}`}>
                  {day.dayNumber}
                </span>
                <span className={`max-w-full truncate text-[11px] font-medium leading-[14px] tabular-nums ${DAY_CAPTION_TONES[day.tone] || DAY_CAPTION_TONES.none}`}>
                  {day.caption || ' '}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
      <div
        ref={bodyRef}
        className="sa-m-grid__scroll sa-m-bleed pb-1"
        style={scrollStyle}
        onScroll={scrolls ? () => follow(bodyRef.current, headRef.current) : undefined}
      >
        {groups.map((group) => (
          <section key={group.id} className="pt-4">
            <h2
              className={`${iosGroupLabel} sa-m-group__label px-1 pb-1.5`}
              style={scrolls ? { position: 'sticky', left: 0, display: 'inline-block' } : undefined}
            >
              {group.title}
            </h2>
            <div style={track}>
              {group.rows.map((row, rowIndex) => row.map((cell, dayIndex) => {
                if (!cell) {
                  return (
                    <span
                      // Пустая клетка — место в сетке, а не данные: адрес «строка-день» и есть её ключ.
                      // eslint-disable-next-line react/no-array-index-key
                      key={`empty-${rowIndex}-${dayIndex}`}
                      className={`h-11 rounded-lg border border-dashed ${COLUMN_EMPTY_TONES[days[dayIndex]?.columnTone] || 'border-slate-200'}`}
                      aria-hidden="true"
                    />
                  );
                }
                const { key, ...cellProps } = cell;
                return <AuctionPhoneCell key={key} {...cellProps} />;
              }))}
              {onAdd ? days.map((day) => (
                <button
                  key={`add-${group.id}-${day.date}`}
                  type="button"
                  onClick={() => onAdd(group.id, day.date)}
                  aria-label={`Добавить смену · ${group.title} · ${day.ariaLabel}`}
                  className="sa-m-cell flex h-8 w-full items-center justify-center rounded-lg border border-dashed border-violet-300 bg-violet-50 text-violet-600"
                >
                  <Plus size={15} aria-hidden="true" />
                </button>
              )) : null}
            </div>
          </section>
        ))}
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

/*
 * Шапка раздела с выбором месяца: крупный заголовок, под ним — стрелки парой и
 * подпись месяца. Подпись прозрачная сверху накрыта системным списком: нажатие
 * открывает колесо месяцев iOS, а не наш выпадающий список.
 *
 * Общая на «Мои часы» и «Мои оценки»: месяц у них выбирается одинаково, и
 * вторая копия этой шапки разъехалась бы с первой на первой же правке.
 * children — <option>ы месяца, их считает раздел.
 */
export const AuctionPhoneMonthHeader = ({
  title,
  subtitle = '',
  month,
  label,
  onMonthChange,
  onPrev = null,
  onNext = null,
  prevDisabled = false,
  nextDisabled = false,
  disabled = false,
  children,
}) => {
  const arrow = 'grid h-8 w-8 shrink-0 place-items-center rounded-full text-blue-600 active:opacity-60 disabled:text-slate-300';
  return (
    <header className="pt-1">
      <h1 className="sa-m-title truncate text-slate-900">{title}</h1>
      {subtitle ? <p className="text-[15px] text-slate-500">{subtitle}</p> : null}
      <div className="mt-3 flex items-center gap-0.5" style={{ flexWrap: 'nowrap' }}>
        <button type="button" onClick={onPrev} disabled={disabled || prevDisabled} aria-label="Предыдущий месяц" className={arrow}>
          <ChevronLeft size={20} aria-hidden="true" />
        </button>
        <button type="button" onClick={onNext} disabled={disabled || nextDisabled} aria-label="Следующий месяц" className={arrow}>
          <ChevronRight size={20} aria-hidden="true" />
        </button>
        <label className="relative ml-1 flex min-w-0 items-center gap-1" style={{ flexWrap: 'nowrap' }}>
          <span className="truncate text-[17px] font-semibold text-slate-900">{label}</span>
          <ChevronDown size={16} className="shrink-0 text-slate-400" aria-hidden="true" />
          <select
            className="sa-m-wheel"
            value={month}
            onChange={(event) => onMonthChange?.(event.target.value)}
            disabled={disabled}
            aria-label="Месяц"
          >
            {children}
          </select>
        </label>
      </div>
    </header>
  );
};

const GAUGE_TONES = {
  green: 'text-green-500',
  blue: 'text-blue-500',
  amber: 'text-amber-500',
  red: 'text-red-500',
  slate: 'text-slate-300',
};
const GAUGE_ARC = 'M 10 58 A 46 46 0 0 1 102 58';

/*
 * Полукруг — тот же знак, что на компьютере, но в SVG: холст (canvas) рисуется
 * в CSS-пикселях и на экране телефона расплывается. Цвет — тон показателя,
 * чтобы дуга и подпись под ней не спорили друг с другом.
 */
export const AuctionPhoneGauge = ({ percent, tone = 'slate', text, ariaLabel }) => {
  const filled = Math.max(0, Math.min(100, Number(percent) || 0));
  return (
    <div className="relative h-[64px] w-[112px] shrink-0" role="img" aria-label={ariaLabel}>
      <svg viewBox="0 0 112 64" width="112" height="64" aria-hidden="true">
        <path d={GAUGE_ARC} fill="none" stroke="currentColor" strokeWidth="9" strokeLinecap="round" pathLength="100" className="text-slate-200" />
        {filled > 0 ? (
          <path
            d={GAUGE_ARC}
            fill="none"
            stroke="currentColor"
            strokeWidth="9"
            strokeLinecap="round"
            pathLength="100"
            strokeDasharray={`${filled} 100`}
            className={GAUGE_TONES[tone] || GAUGE_TONES.slate}
          />
        ) : null}
      </svg>
      <span className="absolute inset-x-0 bottom-0 text-center text-[16px] font-semibold leading-6 tabular-nums text-slate-900">
        {text}
      </span>
    </div>
  );
};
