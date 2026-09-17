import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronLeft, ChevronRight, ChevronsUpDown } from 'lucide-react';
import { iosCard, iosGroupLabel } from '../ui/ios';
import {
  buildPhoneCalendarMonth,
  formatPhoneMonthTitle,
  nextPhoneRangeSelection,
  parsePhoneIso,
  phoneTileSpans,
  toPhoneIso,
} from './resourceFtePhone';

/*
 * Телефонный вид «Расчета ресурсов» (линия и чат) и планировщика графиков —
 * только разметка.
 *
 * На компьютере раздел — панель на всю ширину окна: шапка с полями периода и
 * тремя кнопками по 240 px, шесть вкладок в ряд, плитки показателей по шесть в
 * строку, графики с двумя осями, таблицы на двенадцать колонок. На экране в
 * 390 px это вставало столбиком: шапка в 380 px, вкладки в три ряда, плитки по
 * одной на экран. Здесь то же содержимое собрано под палец: крупный заголовок,
 * вкладки лентой, плитки двумя колонками, списки вместо таблиц там, где строка
 * читается, календари и окна — экранами.
 *
 * Данные, запросы и формулы — те же, что у компьютера, их считает
 * ResourceFteView.jsx. Правила без React (подписи периодов, календарь, время
 * смены) — resourceFtePhone.js.
 *
 * ЦВЕТ — утилитами Tailwind, а не hex в CSS: тёмный слой портала перекрашивает
 * утилиты, а собственные цвета файла стилей он не видит.
 *
 * ОБЩИЙ СЛОЙ РАЗДЕЛОВ (mobile-shell.css) переносит ряды с gap по строкам, ставит
 * колонкой .flex.items-start с .flex-1 внутри, обнуляет min-w-[…], сводит
 * grid-cols-3…6 к двум колонкам и переносит ряды, у которых в имени класса есть
 * «-tabs/-actions/-filters». Поэтому строки здесь выровнены по центру, ленты
 * запрещают перенос инлайном, сетки заданы стилем, а имена классов ни одну из
 * этих подстрок не содержат.
 */

const NO_WRAP = { flexWrap: 'nowrap', scrollbarWidth: 'none' };

// Главные кнопки экранов — во всю ширину и под большой палец.
export const RF_PHONE_BUTTON = {
  blue: 'flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 text-[17px] font-semibold text-white transition active:scale-[0.98] disabled:opacity-50',
  gray: 'flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-slate-200 px-4 text-[17px] font-semibold text-slate-800 transition active:scale-[0.98] disabled:opacity-50',
  white: 'flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-white px-4 text-[17px] font-semibold text-blue-600 ring-1 ring-slate-200/70 transition active:scale-[0.98] disabled:opacity-50',
};

// Оси графиков на телефоне: мелкий серый кегль, без линий осей — как в «Здоровье».
export const RF_PHONE_AXIS_TICK = { fontSize: 10, fill: '#94a3b8' };
export const RF_PHONE_CHART_MARGIN = { top: 8, right: 4, left: 0, bottom: 0 };

const TONE_TEXT = {
  slate: 'text-slate-900',
  blue: 'text-blue-600',
  emerald: 'text-emerald-600',
  rose: 'text-rose-600',
  amber: 'text-amber-600',
  violet: 'text-violet-600',
  muted: 'text-slate-400',
};

export const rfPhoneToneText = (tone) => TONE_TEXT[tone] || TONE_TEXT.slate;

/* Крупный заголовок раздела и круглая кнопка справа — как «Аукцион смен». */
export const RfPhoneHeader = ({ title, caption = null, action = null }) => (
  <header className="flex items-center gap-3 pt-1">
    <div className="min-w-0 flex-1">
      <h1 className="rf-m-title truncate text-slate-900">{title}</h1>
      {caption ? <p className="truncate text-[15px] text-slate-500">{caption}</p> : null}
    </div>
    {action}
  </header>
);

export const RfPhoneRoundButton = ({ icon: Icon, onClick, ariaLabel, spinning = false, disabled = false }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    aria-label={ariaLabel}
    className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-white text-blue-600 shadow-sm ring-1 ring-slate-200/70 disabled:opacity-50"
  >
    <Icon size={19} className={spinning ? 'animate-spin' : ''} aria-hidden="true" />
  </button>
);

/*
 * Лента вкладок. Шесть вкладок в сегментный переключатель не помещаются
 * (у него на экран в 358 px по 60 px на подпись), поэтому это ряд пилюль,
 * который едет вбок; выбранная всегда доезжает до середины экрана.
 */
export const RfPhonePills = ({ items = [], value, onChange, ariaLabel }) => {
  const scrollRef = useRef(null);
  useEffect(() => {
    const strip = scrollRef.current;
    const node = strip?.querySelector('[aria-selected="true"]');
    if (!strip || !node) return;
    strip.scrollLeft = Math.max(0, node.offsetLeft - (strip.clientWidth - node.offsetWidth) / 2);
  }, [value]);
  if (!items.length) return null;
  return (
    <div ref={scrollRef} role="tablist" aria-label={ariaLabel} className="rf-m-pills rf-m-bleed flex gap-2 overflow-x-auto" style={NO_WRAP}>
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange?.(item.value)}
            className={`flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-4 text-[15px] font-semibold transition ${
              active ? 'bg-slate-900 text-white' : 'bg-white text-slate-700 ring-1 ring-slate-200/70'
            }`}
          >
            {item.label}
            {Number(item.count) > 0 ? (
              <span className={`text-[13px] tabular-nums ${active ? 'text-white/60' : 'text-slate-400'}`}>{item.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
};

/*
 * Группа строк: подпись над карточкой, пояснение под ней — как в настройках
 * телефона. Без строк карточка не рисуется: остаётся подпись с кнопкой справа.
 */
export const RfPhoneGroup = ({ label = null, right = null, hint = null, children, className = '' }) => {
  const hasRows = React.Children.toArray(children).filter(Boolean).length > 0;
  return (
    <section className={className}>
      {label || right ? (
        <div className="mb-1.5 flex items-center gap-2 px-1">
          {label ? <h2 className={`${iosGroupLabel} rf-m-group__label min-w-0 flex-1 truncate`}>{label}</h2> : <span className="flex-1" />}
          {right}
        </div>
      ) : null}
      {hasRows ? <div className={`${iosCard} overflow-hidden`}>{children}</div> : null}
      {hint ? <div className="mt-1.5 px-1 text-[13px] leading-snug text-slate-500">{hint}</div> : null}
    </section>
  );
};

/*
 * Период стрелками: ‹ 21.09 — 27.09 ›. Стрелки двигают период целиком на его
 * шаг (неделю), нажатие на подпись открывает календарь — так прогноз и график
 * листаются одним пальцем, без экрана выбора на каждую неделю.
 */
export const RfPhonePeriodStepper = ({
  label = null,
  right = null,
  value,
  caption = null,
  hint = null,
  onPrev,
  onNext,
  onOpen,
  prevLabel = 'Предыдущий период',
  nextLabel = 'Следующий период',
}) => (
  <section>
    {label || right ? (
      <div className="mb-1.5 flex items-center gap-2 px-1">
        {label ? <h2 className={`${iosGroupLabel} rf-m-group__label min-w-0 flex-1 truncate`}>{label}</h2> : <span className="flex-1" />}
        {right}
      </div>
    ) : null}
    <div className={`${iosCard} flex items-center gap-1 px-1 py-1`} style={NO_WRAP}>
      <button type="button" onClick={onPrev} disabled={!onPrev} aria-label={prevLabel} className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-blue-600 disabled:opacity-30">
        <ChevronLeft size={22} aria-hidden="true" />
      </button>
      <button type="button" onClick={onOpen} disabled={!onOpen} className="min-w-0 flex-1 px-1 py-1 text-center">
        <span className="block truncate text-[17px] font-semibold tabular-nums text-slate-900">{value}</span>
        {caption ? <span className="block truncate text-[13px] text-slate-500">{caption}</span> : null}
      </button>
      <button type="button" onClick={onNext} disabled={!onNext} aria-label={nextLabel} className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-blue-600 disabled:opacity-30">
        <ChevronRight size={22} aria-hidden="true" />
      </button>
    </div>
    {hint ? <div className="mt-1.5 px-1 text-[13px] leading-snug text-slate-500">{hint}</div> : null}
  </section>
);

/* Кнопка-подпись справа от заголовка группы («Все», «Сбросить», «Пересчитать»). */
export const RfPhoneGroupAction = ({ label, onClick, disabled = false, busy = false }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled || busy}
    className="h-7 shrink-0 text-[15px] font-semibold text-blue-600 disabled:opacity-50"
  >
    {busy ? '…' : label}
  </button>
);

/*
 * Строка списка. Кнопкой становится вся строка, только когда у неё нет своей
 * кнопки справа: кнопка внутри кнопки — недопустимая разметка.
 */
export const RfPhoneRow = ({
  title,
  subtitle = null,
  value = null,
  valueClassName = 'text-slate-500',
  leading = null,
  trailing = null,
  onClick = null,
  chevron = false,
  strong = false,
  muted = false,
  disabled = false,
  ariaLabel,
  children = null,
}) => {
  const body = (
    <>
      {leading}
      <span className="min-w-0 flex-1">
        <span className={`block truncate text-[16px] ${strong ? 'font-semibold' : ''} ${muted ? 'text-slate-400' : 'text-slate-900'}`}>
          {title}
        </span>
        {subtitle ? <span className="block truncate text-[13px] tabular-nums text-slate-500">{subtitle}</span> : null}
        {children}
      </span>
      {value !== null && value !== undefined && value !== '' ? (
        <span className={`shrink-0 text-right text-[16px] tabular-nums ${valueClassName}`}>{value}</span>
      ) : null}
      {trailing}
      {chevron ? <ChevronRight size={18} className="shrink-0 text-slate-300" aria-hidden="true" /> : null}
    </>
  );
  const className = 'rf-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left';
  if (onClick) {
    return (
      <button type="button" onClick={onClick} disabled={disabled} className={`${className} disabled:opacity-50`} aria-label={ariaLabel}>
        {body}
      </button>
    );
  }
  return <div className={className}>{body}</div>;
};

/*
 * Плитки показателей двумя колонками. Сетка задана стилем: общий слой разделов
 * переписывает grid-cols-* и сломал бы раскладку.
 */
export const RfPhoneTiles = ({ items = [] }) => {
  const visible = items.filter(Boolean);
  const spans = phoneTileSpans(visible.length);
  if (!visible.length) return null;
  return (
    <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
      {visible.map((item, index) => {
        const Tag = item.onClick ? 'button' : 'div';
        return (
          <Tag
            key={item.key || item.label}
            type={item.onClick ? 'button' : undefined}
            onClick={item.onClick}
            className={`${iosCard} min-w-0 px-3.5 py-3 text-left`}
            style={spans[index] ? { gridColumn: '1 / -1' } : undefined}
          >
            {/* Подпись в две строки, а не с обрывом: «Чатнико-часы прогноза» в
                половину экрана не помещается, а обрезанная подпись теряет смысл. */}
            <span className="block text-[13px] leading-4 text-slate-500">{item.label}</span>
            <span className={`mt-0.5 block truncate text-[22px] font-semibold leading-7 tabular-nums ${rfPhoneToneText(item.tone)}`}>
              {item.value}
            </span>
            {item.hint ? <span className="mt-0.5 block truncate text-[12px] text-slate-400">{item.hint}</span> : null}
          </Tag>
        );
      })}
    </div>
  );
};

/* Карточка графика или блока: заголовок строкой, справа — действие или число. */
export const RfPhoneCard = ({ title = null, subtitle = null, right = null, children, className = '' }) => (
  <section className={`${iosCard} px-4 py-3.5 ${className}`}>
    {title || right ? (
      <div className="mb-2 flex items-center gap-3">
        <div className="min-w-0 flex-1">
          {title ? <h2 className="rf-m-card-title truncate text-slate-900">{title}</h2> : null}
          {subtitle ? <p className="truncate text-[13px] text-slate-500">{subtitle}</p> : null}
        </div>
        {right}
      </div>
    ) : null}
    {children}
  </section>
);

export const RfPhoneChart = ({ height = 200, children }) => (
  <div className="rf-m-chart w-full" style={{ height }}>
    {children}
  </div>
);

/*
 * Легенда графика. На компьютере серии объясняет всплывающая подсказка под
 * курсором; на телефоне курсора нет, и без легенды цвета ничего не говорят.
 * Если дан onToggle — легенда заодно включает и выключает серии.
 */
export const RfPhoneLegend = ({ items = [], onToggle = null }) => {
  const visible = items.filter(Boolean);
  if (!visible.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1.5">
      {visible.map((item) => {
        const active = item.active !== false;
        const swatch = (
          <span
            className={`inline-block shrink-0 ${item.shape === 'bar' ? 'h-2.5 w-2.5 rounded-sm' : 'h-0.5 w-3.5 rounded-full'}`}
            style={{
              background: item.shape === 'dashed'
                ? `repeating-linear-gradient(90deg, ${item.color} 0 4px, transparent 4px 7px)`
                : item.color,
              opacity: active ? 1 : 0.35,
            }}
            aria-hidden="true"
          />
        );
        const label = <span className={active ? 'text-slate-600' : 'text-slate-400 line-through'}>{item.label}</span>;
        if (!onToggle) {
          return (
            <span key={item.key || item.label} className="inline-flex items-center gap-1.5 text-[12px]">
              {swatch}
              {label}
            </span>
          );
        }
        return (
          <button
            key={item.key || item.label}
            type="button"
            onClick={() => onToggle(item.key, !active)}
            aria-pressed={active}
            className="inline-flex h-7 items-center gap-1.5 text-[12px]"
          >
            {swatch}
            {label}
          </button>
        );
      })}
    </div>
  );
};

/* Полоса доли. Из span, а не div: её кладут внутрь строки-кнопки, а блочный
   элемент внутри button — недопустимая разметка. */
export const RfPhoneBar = ({ percent = 0, className = 'bg-blue-600' }) => (
  <span className="mt-1.5 block h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
    <span className={`block h-full rounded-full ${className}`} style={{ width: `${Math.max(0, Math.min(100, Number(percent) || 0))}%` }} />
  </span>
);

/* Раздел страницы: подпись капителью, под ней — плитки или карточки без общей
   подложки (группа строк — RfPhoneGroup). */
export const RfPhoneSection = ({ label, right = null, hint = null, children, className = '' }) => (
  <section className={className}>
    <div className="mb-1.5 flex items-center gap-2 px-1">
      <h2 className={`${iosGroupLabel} rf-m-group__label min-w-0 flex-1 truncate`}>{label}</h2>
      {right}
    </div>
    <div className="flex flex-col gap-2">{children}</div>
    {hint ? <div className="mt-1.5 px-1 text-[13px] leading-snug text-slate-500">{hint}</div> : null}
  </section>
);

/*
 * Последнее непустое значение. Экран уезжает ещё треть секунды после того, как
 * раздел очистил выбранный день, и без этого уезжал бы пустым.
 */
export const useRfPhoneLastPresent = (value) => {
  const ref = useRef(value);
  if (value) ref.current = value;
  return value || ref.current;
};

export const RfPhoneEmpty = ({ title, text = null, action = null }) => (
  <div className={`${iosCard} px-5 py-7 text-center`}>
    <div className="text-[16px] font-semibold text-slate-900">{title}</div>
    {text ? <p className="mt-1 text-[14px] leading-snug text-slate-500">{text}</p> : null}
    {action ? <div className="mt-4">{action}</div> : null}
  </div>
);

const NOTE_TONES = {
  amber: 'bg-amber-50 text-amber-800',
  rose: 'bg-rose-50 text-rose-700',
  blue: 'bg-blue-50 text-blue-800',
  slate: 'bg-slate-200/60 text-slate-600',
};

export const RfPhoneNote = ({ tone = 'slate', title = null, children }) => (
  <div className={`rounded-xl px-4 py-3 text-[14px] leading-snug ${NOTE_TONES[tone] || NOTE_TONES.slate}`}>
    {title ? <div className="font-semibold">{title}</div> : null}
    {children}
  </div>
);

/*
 * Полоса дней: день — плашка с днём недели, числом и значением. Выбранный день
 * всегда доезжает до середины. Цветная черта внизу — признак дня (у прогноза:
 * хватает ли истории, у планировщика — закрытие потребности).
 */
export const RfPhoneDayStrip = ({ days = [], onSelect, ariaLabel }) => {
  const scrollRef = useRef(null);
  const activeKey = days.find((day) => day.active)?.key;
  useEffect(() => {
    const strip = scrollRef.current;
    const node = strip?.querySelector('[aria-pressed="true"]');
    if (!strip || !node) return;
    strip.scrollLeft = Math.max(0, node.offsetLeft - (strip.clientWidth - node.offsetWidth) / 2);
  }, [activeKey]);
  if (!days.length) return null;
  return (
    <div ref={scrollRef} role="group" aria-label={ariaLabel} className="rf-m-bleed flex gap-2 overflow-x-auto pb-0.5" style={NO_WRAP}>
      {days.map((day) => (
        <button
          key={day.key}
          type="button"
          aria-pressed={Boolean(day.active)}
          aria-label={day.ariaLabel}
          onClick={() => onSelect?.(day)}
          className={`rf-m-day relative flex shrink-0 flex-col items-center overflow-hidden rounded-2xl px-2 pb-2.5 pt-1.5 transition ${
            day.active ? 'bg-slate-900 text-white' : 'bg-white text-slate-900 ring-1 ring-slate-200/70'
          }`}
          style={day.style}
        >
          <span className={`text-[12px] ${day.active ? 'text-white/70' : 'text-slate-500'}`}>{day.top}</span>
          <span className="text-[19px] font-semibold leading-6 tabular-nums">{day.main}</span>
          {day.bottom ? (
            <span className={`whitespace-nowrap text-[11px] tabular-nums ${day.active ? 'text-white/80' : 'text-slate-500'}`}>{day.bottom}</span>
          ) : null}
          {day.accentClassName ? (
            <span className={`absolute inset-x-3 bottom-1 h-[3px] rounded-full ${day.accentClassName}`} aria-hidden="true" />
          ) : null}
        </button>
      ))}
    </div>
  );
};

/*
 * Календарь периода для экрана. Первое касание — начало, второе — конец
 * (nextPhoneRangeSelection). dayTone отдаёт класс дня: у прогноза линии зелёным
 * отмечены дни, для которых хватает истории. markedDates — точка под числом
 * («есть отчёт», «есть чаты»).
 */
export const RfPhoneCalendar = ({
  startValue,
  endValue,
  onRangeChange,
  markedDates = null,
  dayTone = null,
  quickActions = [],
  todayIso = '',
}) => {
  const [draftStart, setDraftStart] = useState('');
  const initial = parsePhoneIso(startValue) || parsePhoneIso(todayIso) || new Date();
  const [month, setMonth] = useState(() => new Date(initial.getFullYear(), initial.getMonth(), 1));
  const weeks = useMemo(() => buildPhoneCalendarMonth(month), [month]);
  const marked = useMemo(() => (
    markedDates instanceof Set ? markedDates : new Set(Array.isArray(markedDates) ? markedDates : [])
  ), [markedDates]);
  const shownStart = draftStart || startValue || '';
  const shownEnd = draftStart ? '' : endValue || '';
  const dayCount = shownStart && shownEnd
    ? Math.round((parsePhoneIso(shownEnd) - parsePhoneIso(shownStart)) / 86400000) + 1
    : 0;

  const pick = (iso) => {
    const next = nextPhoneRangeSelection(draftStart, iso);
    setDraftStart(next.draftStart);
    if (next.range) onRangeChange?.(next.range[0], next.range[1]);
  };
  const moveMonth = (delta) => setMonth((current) => new Date(current.getFullYear(), current.getMonth() + delta, 1));

  return (
    <div className={`${iosCard} px-3 pb-3 pt-2`}>
      <div className="flex items-center gap-1">
        <button type="button" onClick={() => moveMonth(-1)} aria-label="Предыдущий месяц" className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-blue-600">
          <ChevronLeft size={20} aria-hidden="true" />
        </button>
        <div className="min-w-0 flex-1 text-center text-[17px] font-semibold text-slate-900">{formatPhoneMonthTitle(month)}</div>
        <button type="button" onClick={() => moveMonth(1)} aria-label="Следующий месяц" className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-blue-600">
          <ChevronRight size={20} aria-hidden="true" />
        </button>
      </div>
      <div className="mt-1 grid text-center text-[12px] font-medium uppercase text-slate-400" style={{ gridTemplateColumns: 'repeat(7, minmax(0, 1fr))' }}>
        {['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map((label) => <span key={label} className="py-1">{label}</span>)}
      </div>
      <div className="grid gap-y-1" style={{ gridTemplateColumns: 'repeat(7, minmax(0, 1fr))' }}>
        {weeks.flat().map((cell) => {
          const isEdge = cell.iso === shownStart || cell.iso === shownEnd;
          const inRange = shownStart && shownEnd && cell.iso > shownStart && cell.iso < shownEnd;
          const tone = dayTone ? dayTone(cell.iso) : '';
          const isToday = cell.iso === todayIso;
          return (
            <button
              key={cell.iso}
              type="button"
              onClick={() => pick(cell.iso)}
              aria-pressed={isEdge}
              className={`relative flex h-11 items-center justify-center text-[17px] tabular-nums ${inRange ? 'bg-blue-50' : ''}`}
            >
              <span
                className={`grid h-9 w-9 place-items-center rounded-full ${
                  isEdge
                    ? 'bg-blue-600 font-semibold text-white'
                    : tone || (cell.outside ? 'text-slate-300' : isToday ? 'font-semibold text-blue-600' : 'text-slate-900')
                }`}
              >
                {cell.day}
              </span>
              {marked.has(cell.iso) ? (
                <span className={`absolute bottom-0.5 h-1 w-1 rounded-full ${isEdge ? 'bg-blue-600' : 'bg-emerald-500'}`} aria-hidden="true" />
              ) : null}
            </button>
          );
        })}
      </div>
      <div className="mt-2 flex items-center gap-3 border-t border-slate-100 px-1 pt-2">
        <span className="min-w-0 flex-1 truncate text-[14px] text-slate-500">
          {draftStart ? 'Выберите конец периода' : dayCount > 0 ? `${dayCount} дн. в периоде` : 'Выберите начало периода'}
        </span>
        {quickActions.filter(Boolean).map((action) => (
          <button
            key={action.label}
            type="button"
            onClick={() => {
              setDraftStart('');
              action.onClick();
            }}
            className="h-8 shrink-0 text-[15px] font-semibold text-blue-600"
          >
            {action.label}
          </button>
        ))}
      </div>
    </div>
  );
};

/*
 * Таблица, которой место в таблице (биллинг, почасовой прогноз): прокрутка
 * вбок внутри карточки, первая колонка стоит на месте. Минимальную ширину
 * таблицы возвращает resource-fte-mobile.css — общий слой разделов её обнуляет,
 * и двенадцать колонок сжимались в ширину экрана.
 */
export const RfPhoneTable = ({ children, className = '' }) => (
  <div className={`rf-m-table ${iosCard} overflow-hidden ${className}`}>
    <div className="rf-m-table__scroll overflow-x-auto">{children}</div>
  </div>
);

/* Числовое поле строкой: подпись слева, поле справа, кегль 16 px — ниже него
   iOS приближает страницу при фокусе.
   ПОЛЕ ТЕКСТОВОЕ, А НЕ type="number": русская раскладка iOS даёт на цифровой
   клавиатуре запятую, и числовое поле на «0,9» отдаёт пустую строку — значение
   молча стиралось бы. Запятая приводится к точке здесь же, до раздела. */
export const RfPhoneInputRow = ({ label, subtitle = null, value, onChange, inputMode = 'decimal', placeholder = '', disabled = false }) => (
  <label className="rf-m-row flex w-full items-center gap-3 px-4 py-2">
    <span className="min-w-0 flex-1">
      <span className="block text-[16px] text-slate-900">{label}</span>
      {subtitle ? <span className="block text-[13px] leading-snug text-slate-500">{subtitle}</span> : null}
    </span>
    <input
      type="text"
      inputMode={inputMode}
      value={value ?? ''}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(event) => onChange?.(String(event.target.value).replace(',', '.'))}
      className="rf-m-input h-9 shrink-0 rounded-lg bg-slate-100 px-3 text-right text-[16px] font-semibold tabular-nums text-slate-900 outline-none focus:ring-2 focus:ring-blue-500/60 disabled:text-slate-400"
    />
  </label>
);

/*
 * Выбор из списка строкой: видна подпись значения, поверх неё — прозрачный
 * системный список (на iOS это колесо). Тот же приём, что у месяца в «Моих часах».
 */
export const RfPhoneSelectRow = ({ label, subtitle = null, value, options = [], onChange, disabled = false }) => {
  const current = options.find(([optionValue]) => String(optionValue) === String(value));
  return (
    <div className="rf-m-row relative flex w-full items-center gap-3 px-4 py-2.5">
      <span className="min-w-0 flex-1">
        <span className="block text-[16px] text-slate-900">{label}</span>
        {subtitle ? <span className="block text-[13px] leading-snug text-slate-500">{subtitle}</span> : null}
      </span>
      <span className="flex shrink-0 items-center gap-1 text-[16px] text-slate-500">
        {current ? current[1] : '—'}
        <ChevronsUpDown size={15} className="text-slate-400" aria-hidden="true" />
      </span>
      <select
        value={value ?? ''}
        disabled={disabled}
        onChange={(event) => onChange?.(event.target.value)}
        aria-label={typeof label === 'string' ? label : undefined}
        className="rf-m-wheel"
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={String(optionValue)} value={optionValue}>{optionLabel}</option>
        ))}
      </select>
    </div>
  );
};

/* Переключатель строкой. Сам тумблер — общий IosToggle, его передаёт раздел. */
export const RfPhoneToggleRow = ({ title, subtitle = null, toggle }) => (
  <div className="rf-m-row flex w-full items-center gap-3 px-4 py-2.5">
    <span className="min-w-0 flex-1">
      <span className="block text-[16px] text-slate-900">{title}</span>
      {subtitle ? <span className="block text-[13px] leading-snug text-slate-500">{subtitle}</span> : null}
    </span>
    {toggle}
  </div>
);

/* Строка с галочкой — выбор из списка, как «Язык» в настройках телефона. */
export const RfPhoneCheckRow = ({ title, subtitle = null, checked = false, onClick, trailing = null }) => (
  <button type="button" onClick={onClick} aria-pressed={checked} className="rf-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left">
    <span className="min-w-0 flex-1">
      <span className="block truncate text-[16px] text-slate-900">{title}</span>
      {subtitle ? <span className="block truncate text-[13px] text-slate-500">{subtitle}</span> : null}
    </span>
    {trailing}
    <span className="grid h-6 w-6 shrink-0 place-items-center text-blue-600" aria-hidden="true">
      {checked ? <Check size={20} strokeWidth={2.5} /> : null}
    </span>
  </button>
);

/* Точка статуса перед строкой — цвет только со смыслом (выдержал / не выдержал). */
export const RfPhoneDot = ({ className = 'bg-slate-300' }) => (
  <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${className}`} aria-hidden="true" />
);

export { toPhoneIso };
