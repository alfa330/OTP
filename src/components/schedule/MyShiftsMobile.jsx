import React, { useState } from 'react';
import { ArrowLeftRight, CalendarPlus, Check, ChevronLeft, ChevronRight, Coffee, Plus, Scissors, UserRound } from 'lucide-react';
import FaIcon from '../common/FaIcon';
import { IosBadge, IosToggle, iosCard } from '../ui/ios';
import {
  AUCTION_PHONE_BUTTON,
  AUCTION_PHONE_TIME_INPUT,
  AuctionPhoneChips,
  AuctionPhoneDayStrip,
  AuctionPhoneField,
  AuctionPhoneGroup,
  AuctionPhoneLinkRow,
  AuctionPhonePill,
  AuctionPhoneRow,
  useLastPresent,
} from '../resources/ShiftAuctionMobile';
import { resolveTrackTap } from './myShiftsTrackLayout';
import '../resources/shift-auction-mobile.css';
import './my-shifts-mobile.css';

/*
 * Телефонный вид «Моих смен» — только разметка.
 *
 * На компьютере раздел — панель с календарём слева, четыре вкладки в полосе,
 * «День / Неделя / Месяц», карточка статуса и список дней; запрос на замену —
 * окно на две колонки с таймлайном, списком к отправке и поиском кандидатов.
 * На экране в 390 px это давало полосу вкладок с горизонтальной прокруткой,
 * три ряда кнопок над содержимым и окно, в котором «Отправить» стоит после
 * четырёх экранов прокрутки. Здесь раздел — список настроек телефона: вкладки
 * переключателем, неделя полосой дней, день — группой строк, запрос — экраном
 * с одной кнопкой внизу.
 *
 * Данные и решения (что считается сменой, можно ли обменять, кто кандидат)
 * считает App.jsx теми же помощниками, что и настольный вид: здесь нет ни
 * одного правила раздела, только то, как строка выглядит. Полоса дней, строки,
 * капсулы и лист подтверждения — общие с «Аукционом смен» (sa-m-*), файл
 * стилей аукциона подключается отсюда же: раздел «Мои смены» живёт в главном
 * чанке, а аукцион грузится лениво, и без этого импорта полоса дней осталась
 * бы без стекла и прилипания.
 *
 * Ловушки общего слоя разделов (mobile-shell.css) те же, что у аукциона:
 * строки выровнены по центру (items-start ставится колонкой), ленты, которым
 * нельзя переноситься, получают запрет инлайном, минимальные ширины — в CSS.
 */

const NO_WRAP = { flexWrap: 'nowrap' };

export {
  AUCTION_PHONE_BUTTON as PHONE_BUTTON,
  AUCTION_PHONE_TIME_INPUT as PHONE_TIME_INPUT,
  AuctionPhoneChips as PhoneChips,
  AuctionPhoneDayStrip as PhoneDayStrip,
  AuctionPhoneField as PhoneField,
  AuctionPhoneGroup as PhoneGroup,
  AuctionPhoneLinkRow as PhoneLinkRow,
  AuctionPhonePill as PhonePill,
  AuctionPhoneRow as PhoneRow,
  useLastPresent,
};

export const PHONE_ICONS = {
  plus: Plus,
  exchange: ArrowLeftRight,
  replace: UserRound,
  shorten: Scissors,
  extra: CalendarPlus,
};

/* Строка над полосой дней — как в календаре на компьютере: стрелки парой слева,
   подпись недели за ними, «Сегодня» справа и только пока выбран другой день. */
export const MyShiftsWeekHeader = ({ label, onPrev, onNext, onToday, showToday = false }) => (
  <div className="ms-m-week flex items-center gap-0.5" style={NO_WRAP}>
    <button
      type="button"
      onClick={onPrev}
      aria-label="Предыдущая неделя"
      className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-blue-600 active:opacity-60"
    >
      <ChevronLeft size={20} aria-hidden="true" />
    </button>
    <button
      type="button"
      onClick={onNext}
      aria-label="Следующая неделя"
      className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-blue-600 active:opacity-60"
    >
      <ChevronRight size={20} aria-hidden="true" />
    </button>
    <span className="min-w-0 flex-1 truncate pl-1 text-[15px] font-semibold text-slate-900">{label}</span>
    {showToday ? (
      <button type="button" onClick={onToday} className="h-8 shrink-0 px-1 text-[15px] font-semibold text-blue-600 active:opacity-60">
        Сегодня
      </button>
    ) : null}
  </div>
);

/*
 * «Сейчас»: статус одной строкой с цветной плиткой, под ним — напоминание о
 * перерыве переключателем. Пояснение про разрешение на уведомления — под
 * карточкой, серым, как в настройках телефона.
 */
export const MyShiftsStatusCard = ({
  tileClassName,
  iconClassName,
  label,
  live = false,
  hint = null,
  subHint = null,
  reminderEnabled = false,
  onToggleReminder,
  leadMinutes,
  leadOptions = [],
  onLeadMinutes,
  permissionNote = null,
}) => (
  <section>
    <div className={`${iosCard} overflow-hidden`}>
      <div className="flex items-center gap-3 px-4 py-3">
        <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-[12px] text-white ${tileClassName}`}>
          <FaIcon className={`fas ${iconClassName} text-[16px]`}></FaIcon>
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-[16px] font-semibold text-slate-900">{label}</span>
            {live ? (
              <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500"></span>
              </span>
            ) : null}
          </span>
          {hint ? <span className="block truncate text-[14px] tabular-nums text-slate-500">{hint}</span> : null}
          {subHint ? <span className="block truncate text-[13px] text-slate-400">{subHint}</span> : null}
        </span>
      </div>
      <div className="sa-m-row flex items-center gap-3 px-4 py-2.5">
        <FaIcon className="fas fa-bell text-[15px] text-amber-500"></FaIcon>
        <span className="min-w-0 flex-1 text-[16px] text-slate-900">Напомнить о перерыве</span>
        <IosToggle checked={reminderEnabled} onChange={() => onToggleReminder?.()} />
      </div>
      {reminderEnabled ? (
        <div className="flex items-center gap-2.5 px-4 pb-3" style={NO_WRAP}>
          <span className="shrink-0 text-[14px] text-slate-500">за</span>
          <div className="flex min-w-0 flex-1 rounded-lg bg-slate-100 p-0.5" style={NO_WRAP}>
            {leadOptions.map((minutes) => (
              <button
                key={minutes}
                type="button"
                onClick={() => onLeadMinutes?.(minutes)}
                className={`flex-1 rounded-[7px] py-1.5 text-[14px] font-semibold tabular-nums transition ${
                  leadMinutes === minutes ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500'
                }`}
              >
                {minutes}
              </button>
            ))}
          </div>
          <span className="shrink-0 text-[14px] text-slate-500">мин</span>
        </div>
      ) : null}
    </div>
    {permissionNote ? <p className="mt-1.5 px-1 text-[13px] leading-snug text-slate-500">{permissionNote}</p> : null}
  </section>
);

/* Перерывы смены — списком под её строкой, каждый со своей длительностью. */
export const MyShiftsBreakList = ({ breaks = [], className = '' }) => {
  if (!breaks.length) return <p className={`text-[13px] text-slate-400 ${className}`}>Без перерывов</p>;
  return (
    <ul className={`space-y-1 ${className}`}>
      {breaks.map((item, index) => (
        // Перерывы приходят упорядоченными и не переставляются — индекс здесь честный ключ.
        // eslint-disable-next-line react/no-array-index-key
        <li key={index} className="flex items-center gap-2 text-[14px] tabular-nums" style={NO_WRAP}>
          <Coffee size={14} className="shrink-0 text-amber-500" aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate text-slate-700">{item.label}</span>
          <span className="shrink-0 text-slate-400">{item.duration}</span>
        </li>
      ))}
    </ul>
  );
};

/* Смена дня: точка типа, время, часы справа, подпись типа и перерывы. */
export const MyShiftsShiftRow = ({ dotClassName, time, hours, typeLabel, breaks = [] }) => (
  <div className="sa-m-row px-4 py-3">
    <div className="flex items-center gap-2.5" style={NO_WRAP}>
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dotClassName}`} aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate text-[17px] font-semibold tabular-nums text-slate-900">{time}</span>
      <span className="shrink-0 text-[15px] tabular-nums text-slate-500">{hours}</span>
    </div>
    {typeLabel ? <p className="mt-0.5 pl-5 text-[13px] text-slate-500">{typeLabel}</p> : null}
    <MyShiftsBreakList breaks={breaks} className="mt-2 pl-5" />
  </div>
);

/*
 * Таймлайн дня — та же лента 00–24, что на компьютере: смены цветными полосами,
 * перерывы янтарём внутри них, тех. сбои и офлайн тонкими линиями у нижней
 * грани, красная черта — «сейчас». Владелец попросил оставить его на телефоне
 * (14.09.2026: «ты убрал таймлайн, он пусть остается»). Подсказок по наведению
 * здесь нет — наведения на телефоне нет; кто на что нажал, видно по строкам под
 * лентой. Полоса становится кнопкой только когда ей дан onClick (в запросе на
 * замену тап выбирает смену целиком).
 */
export const MyShiftsTimeline = ({
  parts = [],
  overlays = [],
  lines = [],
  nowPercent = null,
  emptyLabel = null,
  emptyClassName = 'text-slate-400',
  caption = null,
  marks = [0, 6, 12, 18, 24],
}) => (
  <div>
    <div className="relative mb-1 h-4">
      {marks.map((hour) => (
        <span
          key={hour}
          className="absolute top-0 text-[11px] leading-none tabular-nums text-slate-400"
          style={{ left: `${(hour / 24) * 100}%`, transform: hour === 0 ? 'none' : hour === 24 ? 'translateX(-100%)' : 'translateX(-50%)' }}
        >
          {String(hour).padStart(2, '0')}
        </span>
      ))}
    </div>
    <div className="relative h-11 overflow-hidden rounded-xl bg-slate-100">
      {marks.filter((hour) => hour > 0 && hour < 24).map((hour) => (
        <div key={hour} className="absolute inset-y-0 w-px bg-slate-200" style={{ left: `${(hour / 24) * 100}%` }} />
      ))}
      {parts.map((part) => {
        const style = { left: `${part.left}%`, width: `${part.width}%`, background: part.background };
        const inner = (
          <>
            {(part.breaks || []).map((brk, index) => (
              // Перерывы внутри полосы упорядочены и не переставляются — индекс честный ключ.
              // eslint-disable-next-line react/no-array-index-key
              <span key={index} className="absolute inset-y-0 bg-amber-300/95" style={{ left: `${brk.left}%`, width: `${brk.width}%` }} />
            ))}
            {part.label ? (
              <span className="relative z-10 block truncate px-1 text-center text-[11px] font-semibold tabular-nums leading-9 text-white">{part.label}</span>
            ) : null}
          </>
        );
        const className = `absolute inset-y-1 overflow-hidden rounded-lg ${part.className || ''}`;
        return part.onClick ? (
          <button key={part.key} type="button" onClick={part.onClick} aria-label={part.ariaLabel} className={className} style={style}>{inner}</button>
        ) : (
          <div key={part.key} className={className} style={style}>{inner}</div>
        );
      })}
      {overlays.map((item) => (
        <div key={item.key} className={`pointer-events-none absolute inset-y-0 ${item.className}`} style={{ left: `${item.left}%`, width: `${item.width}%` }} />
      ))}
      {lines.map((item) => (
        <div key={item.key} className={`pointer-events-none absolute z-20 h-1 rounded-full ${item.className}`} style={{ left: `${item.left}%`, width: `${item.width}%` }} />
      ))}
      {!parts.length && emptyLabel ? (
        <div className={`absolute inset-0 z-10 flex items-center justify-center text-[13px] font-medium ${emptyClassName}`}>{emptyLabel}</div>
      ) : null}
      {nowPercent !== null && Number.isFinite(nowPercent) ? (
        <div className="pointer-events-none absolute inset-y-0 z-30 w-[2px] -translate-x-1/2 bg-rose-500" style={{ left: `${nowPercent}%` }}>
          <div className="absolute -left-[3px] top-0 h-2 w-2 rounded-full bg-rose-500" />
        </div>
      ) : null}
    </div>
    {caption ? <p className="mt-2 text-[12px] leading-snug text-slate-400">{caption}</p> : null}
  </div>
);

/* Палец не попадает в отрезок шириной в пиксель: у каждого отрезка зона касания
   не уже этого числа пикселей (правило — в pickTrackSegment, myShiftsTrackLayout.js). */
const TRACK_TAP_TARGET_PX = 14;
/* Обе подписи одной высоты строки — название мельче выбранного отрезка, и без
   этого полоса под ними при касании сдвигалась бы на полтора пикселя. */
const TRACK_ROW_TITLE = 'min-w-0 flex-1 truncate text-[11px] font-semibold uppercase leading-[18px] tracking-wider text-slate-400';
/* Строка заголовка с кнопкой «Обновить» (24 px) держит эту высоту и тогда,
   когда кнопка спрятана под выбранный отрезок. */
const TRACK_ROW_WITH_BUTTON = { ...NO_WRAP, minHeight: 24 };

/* Подпись полосы. Пока отрезок не выбран — её название; после касания на его
   месте стоит выбранный отрезок («12:20–13:05 · 45 мин · Перерыв»), как
   значение над графиком в «Экранном времени»: строка та же, вёрстка не прыгает. */
const TrackRowTitle = ({ label, picked }) => (picked ? (
  <span className="flex min-w-0 flex-1 items-center gap-1.5" style={NO_WRAP} aria-live="polite">
    {picked.background || picked.dotClassName ? (
      <span
        className={`h-2 w-2 shrink-0 rounded-full ${picked.dotClassName || ''}`}
        style={picked.background ? { background: picked.background } : undefined}
        aria-hidden="true"
      />
    ) : null}
    <span className="min-w-0 truncate text-[12px] font-medium leading-[18px] tabular-nums text-slate-700">{picked.text}</span>
  </span>
) : (
  <span className={TRACK_ROW_TITLE}>{label}</span>
));

/*
 * Две полосы факта под лентой смены: статусы и несоответствия графику.
 *
 * Обе стоят ровно под сменой и за её края не выходят — это та же смена, только
 * разложенная на «что было» и «где не сошлось» (решение владельца, 07.10.2026).
 * Раньше статусы тянулись на все сутки: смену внутри серого «Выключен» было не
 * разглядеть, а главное — где человек отошёл от графика — приходилось искать
 * глазами.
 *
 *   Статусы — цвет из общего справочника, поэтому «перерыв» у оператора и у
 *   руководителя в «Графиках работы» один и тот же. Опоздание — красным в
 *   начале смены. Работа до начала и после конца — зелёной меткой снаружи:
 *   она тоньше полосы, чтобы не читаться её продолжением.
 *   Несоответствия — красным те минуты смены, где статус не совпал с графиком;
 *   их сумма и есть «100% минус совпадение».
 *
 * Что и где рисовать, считает myShiftsTrackLayout.js, здесь только разметка.
 * Подписи под полосами обязательны: наведения на телефоне нет. Там же отрезок
 * выбирают касанием; на компьютере у отрезков подсказка по наведению.
 * Совсем узкие статусы («без телефона» на полминуты) держим на 2 px, иначе они
 * исчезают с ленты вовсе.
 */
export const MyShiftsStatusTrack = ({
  label = 'Статусы',
  mismatchTitle = 'Несоответствия',
  spans = [],
  outside = [],
  totals = [],
  mismatchLabel = null,
  note = null,
  showHours = false,
  asOf = '',
  refreshing = false,
  onRefresh = null,
  touch = false,
  defaultPicked = null,
}) => {
  // В состоянии — только «какой отрезок выбран» ({ row, key }): подпись берётся
  // из свежих данных, иначе после обновления живых статусов в заголовке висел
  // бы старый «Готов • 10:09 — 10:51» при уже выросшем отрезке.
  const [pickedRef, setPickedRef] = useState(defaultPicked);
  if (!spans.length && !outside.length) return null;

  // Статусы выбираются сплошными участками одного цвета, а не по одному:
  // «Готов» и «Занят» чередуются каждые несколько минут и на полосе слиты.
  const segmentsOf = {
    status: () => [
      ...spans.flatMap((span) => [
        ...span.late.map((item) => ({ ...item, dotClassName: 'bg-rose-500' })),
        ...span.runs,
      ]),
      ...outside.map((item) => ({ ...item, dotClassName: 'bg-emerald-500' })),
    ],
    // У несоответствий точки цвета в подписи нет: ряд одноцветный, а её место
    // нужнее словам — «13:05–13:45 · 40 мин · Готов, Занят вместо перерыва».
    mismatch: () => spans.flatMap((span) => span.mismatches),
  };
  const pickedSegment = pickedRef ? (segmentsOf[pickedRef.row]().find((item) => item.key === pickedRef.key) || null) : null;
  const picked = pickedSegment
    ? { row: pickedRef.row, key: pickedSegment.key, text: pickedSegment.caption, background: pickedSegment.background || null, dotClassName: pickedSegment.dotClassName || '' }
    : null;
  // Касание выбирает отрезок по горизонтали — куда бы по высоте ряда ни попал
  // палец: полоса несоответствий высотой в десять пикселей сама не мишень.
  // Второе касание того же отрезка или касание мимо — снять выбор.
  const pickFrom = (row) => (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const tap = { row, segments: segmentsOf[row](), offsetX: event.clientX - rect.left, width: rect.width, targetPx: TRACK_TAP_TARGET_PX };
    setPickedRef((current) => resolveTrackTap({ ...tap, current }));
  };
  const dimmed = (row, key) => (picked && picked.row === row && picked.key !== key ? ' opacity-30' : '');
  const hasMismatch = spans.some((span) => span.mismatches.length > 0);
  // На компьютере подробности даёт подсказка по наведению, на телефоне — касание.
  const tooltipOf = (item) => (touch ? undefined : (item.tooltip || undefined));
  /* Узкие статусы рисуются последними, то есть поверх широких. Минимальная
     ширина в 2 px выводит узкий кусок за его настоящий край, и сосед, нарисованный
     позже, закрывал бы этот запас: «без телефона» на минуту оставался бы на
     ленте полупиксельной ниткой, а подсказка над ним показывала соседа. */
  const widestFirst = (pieces) => pieces.slice().sort((a, b) => b.width - a.width);
  const statusPiece = (bar, tooltip) => (
    <div
      key={bar.key}
      className="absolute inset-y-0"
      // Полпикселя внахлёст: соседние статусы округляются до пикселя каждый
      // сам по себе, и между ними просвечивала подложка.
      style={{ left: `${bar.left}%`, width: `calc(${bar.width}% + 0.5px)`, minWidth: '2px', background: bar.background }}
      data-schedule-tooltip={tooltip}
    />
  );
  const latePiece = (item, tooltip) => (
    <div
      key={item.key}
      className="absolute inset-y-0 bg-rose-500"
      style={{ left: `${item.left}%`, width: `${item.width}%`, minWidth: '2px' }}
      data-schedule-tooltip={tooltip}
    />
  );

  return (
    <div data-my-status-track>
      <div className={touch ? 'ms-m-tap' : undefined} onClick={touch ? pickFrom('status') : undefined}>
        {/* «на 11:25» и обновление стоят в строке заголовка, а не над полосой:
            отдельная панель ради двух знаков — лишний ряд на телефоне. Время
            появляется только там, где день посчитан не до конца смены. */}
        {/* Выбранный отрезок занимает строку целиком — время и кнопка уступают ему
            место: «09:06–10:02 · 56 мин · Готов, Занят» рядом с ними на
            телефоне обрезалось. Высота строки при этом держится по кнопке,
            иначе полоса под ней подпрыгивала бы на каждое касание. */}
        <div className="mb-1 flex items-center gap-2" style={onRefresh ? TRACK_ROW_WITH_BUTTON : NO_WRAP}>
          <TrackRowTitle label={label} picked={picked?.row === 'status' ? picked : null} />
          {asOf && picked?.row !== 'status' ? (
            <span className="shrink-0 text-[11px] tabular-nums text-slate-400">на {asOf}</span>
          ) : null}
          {onRefresh && picked?.row !== 'status' ? (
            <button
              type="button"
              onClick={(event) => { event.stopPropagation(); onRefresh(); }}
              disabled={refreshing}
              className="grid h-6 w-6 shrink-0 place-items-center rounded-full text-slate-400 transition active:scale-95 disabled:opacity-50"
              title="Обновить статусы"
              aria-label="Обновить статусы"
            >
              <FaIcon className={`fas ${refreshing ? 'fa-spinner fa-spin' : 'fa-arrows-rotate'} text-[11px]`}></FaIcon>
            </button>
          ) : null}
        </div>
        {/* Свою шкалу часов рисуем только когда над полосой нет ленты смен (день без
            смены): два одинаковых ряда цифр подряд читаются как ошибка вёрстки. */}
        {showHours ? (
          <div className="relative mb-1 h-4">
            {[0, 6, 12, 18, 24].map((hour) => (
              <span
                key={hour}
                className="absolute top-0 text-[11px] leading-none tabular-nums text-slate-400"
                style={{ left: `${(hour / 24) * 100}%`, transform: hour === 0 ? 'none' : hour === 24 ? 'translateX(-100%)' : 'translateX(-50%)' }}
              >
                {String(hour).padStart(2, '0')}
              </span>
            ))}
          </div>
        ) : null}
        <div className="relative h-5">
          {/* У дня без смены полосе не на что опереться — метки работы лежат на
              светлой дорожке суток, иначе они висели бы в пустоте. */}
          {!spans.length ? <div className="absolute inset-0 rounded-md bg-slate-100" /> : null}
          {spans.map((span) => (
            <div
              key={span.key}
              className="absolute inset-y-0 overflow-hidden rounded-md bg-slate-200"
              style={{ left: `${span.left}%`, width: `${span.width}%` }}
            >
              {/* Гаснет слой целиком, а выбранный участок рисуется поверх ещё раз.
                  Гасить отрезки по одному нельзя: они лежат на полпикселя внахлёст,
                  и полупрозрачные стыки складывались в частокол полосок. */}
              <div className={`absolute inset-0 transition-opacity${picked?.row === 'status' ? ' opacity-30' : ''}`}>
                {widestFirst(span.bars).map((bar) => statusPiece(bar, tooltipOf(bar)))}
                {span.late.map((item) => latePiece(item, tooltipOf(item)))}
              </div>
              {picked?.row === 'status' ? (
                <>
                  {span.bars.filter((bar) => bar.runKey === picked.key).map((bar) => statusPiece(bar))}
                  {span.late.filter((item) => item.key === picked.key).map((item) => latePiece(item))}
                </>
              ) : null}
            </div>
          ))}
          {outside.map((item) => (
            <div
              key={item.key}
              className={`absolute top-1/2 h-2 -translate-y-1/2 rounded-full bg-emerald-500 transition-opacity${dimmed('status', item.key)}`}
              // Метка «раньше смены» держится за свой правый край: минимальная
              // ширина растит её влево, от смены, а не внутрь полосы.
              style={item.kind === 'before'
                ? { right: `${100 - item.left - item.width}%`, width: `${item.width}%`, minWidth: '3px' }
                : { left: `${item.left}%`, width: `${item.width}%`, minWidth: '3px' }}
              data-schedule-tooltip={tooltipOf(item)}
            />
          ))}
        </div>
      </div>
      {mismatchLabel != null && spans.length ? (
        <div className={touch ? 'ms-m-tap mt-2' : 'mt-2'} onClick={touch ? pickFrom('mismatch') : undefined}>
          <div className="mb-1 flex items-center gap-2" style={NO_WRAP}>
            <TrackRowTitle label={mismatchTitle} picked={picked?.row === 'mismatch' ? picked : null} />
            {/* Итог уступает место выбранному отрезку: «13:05–13:45 · 40 мин ·
                Готов, Занят вместо перерыва» занимает всю строку телефона. */}
            {picked?.row === 'mismatch' ? null : (
              <span className={`shrink-0 text-[12px] tabular-nums ${hasMismatch ? 'font-semibold text-rose-600' : 'text-slate-400'}`}>{mismatchLabel}</span>
            )}
          </div>
          {/* Третья лента — той же высоты и формы, что лента статусов, а итог при
              несоответствиях набран красным и жирнее: тонкая серая нитка с
              волосками читалась второстепенной, хотя ради неё день и открывают
              (владелец, 07.10.2026: «сделай более заметной»). */}
          <div className="relative h-5">
            {spans.map((span) => (
              <div
                key={span.key}
                className="absolute inset-y-0 overflow-hidden rounded-md bg-slate-200"
                style={{ left: `${span.left}%`, width: `${span.width}%` }}
              >
                {span.mismatches.map((item) => (
                  <div
                    key={item.key}
                    className={`absolute inset-y-0 bg-rose-500 transition-opacity${dimmed('mismatch', item.key)}`}
                    style={{ left: `${item.left}%`, width: `${item.width}%`, minWidth: '2px' }}
                    data-schedule-tooltip={tooltipOf(item)}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {totals.length ? (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] leading-none">
          {totals.map((item) => (
            <span key={item.key} className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: item.background }} aria-hidden="true" />
              <span className="text-slate-600">{item.label}</span>
              <span className="tabular-nums text-slate-400">{item.value}</span>
            </span>
          ))}
        </div>
      ) : null}
      {note ? <div className="mt-1.5 text-[12px] leading-snug text-slate-500">{note}</div> : null}
    </div>
  );
};

/* Строка с плиткой-значком: «Выходной», «Смен нет», статус графика. */
export const MyShiftsNoteRow = ({ tileClassName = 'bg-slate-300', iconClassName = 'fa-calendar-xmark', title, subtitle = null, note = null }) => (
  <div className="sa-m-row flex items-center gap-3 px-4 py-3">
    <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-[10px] text-white ${tileClassName}`}>
      <FaIcon className={`fas ${iconClassName} text-[14px]`}></FaIcon>
    </span>
    <span className="min-w-0 flex-1">
      <span className="block truncate text-[16px] font-semibold text-slate-900">{title}</span>
      {subtitle ? <span className="block text-[13px] leading-snug text-slate-500">{subtitle}</span> : null}
      {note ? <span className="mt-0.5 block text-[13px] leading-snug text-slate-500">{note}</span> : null}
    </span>
  </div>
);

/* Короткая строка списка: подпись слева, значение справа (тех. сбой, офлайн). */
export const MyShiftsPlainRow = ({ dotClassName = 'bg-slate-400', title, trailing = null, note = null }) => (
  <div className="sa-m-row px-4 py-2.5">
    <div className="flex items-center gap-2.5" style={NO_WRAP}>
      <span className={`h-2 w-2 shrink-0 rounded-full ${dotClassName}`} aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate text-[15px] tabular-nums text-slate-800">{title}</span>
      {trailing ? <span className="shrink-0 text-[14px] text-slate-400">{trailing}</span> : null}
    </div>
    {note ? <p className="mt-0.5 pl-[18px] text-[13px] leading-snug text-slate-500">{note}</p> : null}
  </div>
);

/*
 * Карточка запроса (замена, обмен, заявка руководителю): значок в плитке и
 * заголовок, под ними статус и период одной переносимой строкой, пояснения и
 * ряд действий.
 * Действия — капсулы в отдельной строке: в заголовке им места нет, а у
 * ожидающего запроса они и есть главное.
 */
export const MyShiftsRequestCard = ({
  icon: Icon = ArrowLeftRight,
  tileClassName = 'bg-blue-600',
  title,
  badge = null,
  badgeTone = 'slate',
  subtitle = null,
  notes = [],
  actions = [],
}) => (
  <div className="sa-m-row px-4 py-3">
    <div className="flex items-center gap-3" style={NO_WRAP}>
      <span className={`grid h-[30px] w-[30px] shrink-0 place-items-center rounded-lg text-white ${tileClassName}`}>
        <Icon size={16} aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1 truncate text-[16px] font-semibold text-slate-900">{title}</span>
    </div>
    {/* Статус — в начале второй строки, а период за ним ПЕРЕНОСИТСЯ, а не режется.
        Рядом с заголовком бейдж «На рассмотрении» забирал треть ширины, и в
        карточке оставалось «Сокращение см…» и «09:00 — 1…» — ровно то, ради чего
        её открывают (замер 15.09.2026, 390 px). */}
    {badge || subtitle ? (
      <p className="mt-1 pl-[42px] text-[14px] leading-snug tabular-nums text-slate-500">
        {badge ? <IosBadge tone={badgeTone} className="mr-1.5 align-[1px]">{badge}</IosBadge> : null}
        {subtitle}
      </p>
    ) : null}
    {notes.filter(Boolean).map((note, index) => (
      // Пояснения статичны — индекс честный ключ.
      // eslint-disable-next-line react/no-array-index-key
      <p key={index} className="mt-1 pl-[42px] text-[13px] leading-snug text-slate-500">{note}</p>
    ))}
    {actions.length ? (
      <div className="mt-2.5 flex items-center gap-2 pl-[42px]" style={NO_WRAP}>
        {actions.map((action) => (
          <AuctionPhonePill key={action.key} label={action.label} tone={action.tone} busy={action.busy} disabled={action.disabled} onClick={action.onClick} />
        ))}
      </div>
    ) : null}
  </div>
);

/*
 * Кандидат на замену: имя, супервайзер, приметы («приоритет», «стык», «выходной»)
 * и его смены в этот день. Выбор — галочка справа, вся строка — кнопка.
 */
export const MyShiftsCandidateRow = ({ name, supervisor = null, tags = [], shiftsLabel = null, selected = false, onClick }) => (
  <button type="button" onClick={onClick} aria-pressed={selected} className="sa-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left">
    <span className="min-w-0 flex-1">
      <span className={`block truncate text-[16px] ${selected ? 'font-semibold text-blue-600' : 'text-slate-900'}`}>{name}</span>
      {supervisor || tags.length ? (
        <span className="flex items-center gap-1.5 text-[13px] text-slate-500" style={NO_WRAP}>
          {supervisor ? <span className="min-w-0 truncate">{supervisor}</span> : null}
          {tags.map((tag) => (
            <span key={tag.key} className={`shrink-0 rounded-full px-1.5 py-px text-[11px] font-medium ${tag.className}`}>{tag.label}</span>
          ))}
        </span>
      ) : null}
      {shiftsLabel ? <span className="block text-[13px] leading-snug tabular-nums text-slate-500">{shiftsLabel}</span> : null}
    </span>
    {selected ? <Check size={20} className="shrink-0 text-blue-600" aria-hidden="true" /> : null}
  </button>
);

/* Смена коллеги для обмена — отмечаемая строка второго экрана. */
export const MyShiftsCheckRow = ({ title, subtitle = null, checked = false, onClick }) => (
  <button type="button" onClick={onClick} aria-pressed={checked} className="sa-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left">
    <span className="min-w-0 flex-1">
      <span className={`block truncate text-[16px] tabular-nums ${checked ? 'font-semibold text-blue-600' : 'text-slate-900'}`}>{title}</span>
      {subtitle ? <span className="block truncate text-[13px] text-slate-500">{subtitle}</span> : null}
    </span>
    {checked ? <Check size={20} className="shrink-0 text-blue-600" aria-hidden="true" /> : null}
  </button>
);

const COLLEAGUE_CHIP = {
  regular: 'bg-blue-100 text-blue-800',
  night: 'bg-orange-100 text-orange-800',
  practice: 'bg-emerald-100 text-emerald-800',
  phone: 'bg-cyan-100 text-indigo-800',
};

/* Коллега на выбранный день: имя, супервайзер, справа — смены чипами того же
   цвета, что в настольной сетке, либо «Вых.» / «—». */
export const MyShiftsColleagueRow = ({ name, supervisor = null, shifts = [], isDayOff = false }) => (
  <div className="sa-m-row flex items-center gap-3 px-4 py-2.5">
    <span className="min-w-0 flex-1">
      <span className={`block truncate text-[16px] ${shifts.length ? 'text-slate-900' : 'text-slate-400'}`}>{name}</span>
      {supervisor ? <span className="block truncate text-[13px] text-slate-500">{supervisor}</span> : null}
    </span>
    <span className="flex shrink-0 flex-col items-end gap-1">
      {shifts.length ? shifts.map((shift) => (
        <span key={shift.key} className={`rounded-md px-2 py-0.5 text-[13px] font-semibold tabular-nums ${COLLEAGUE_CHIP[shift.kind] || COLLEAGUE_CHIP.regular}`}>
          {shift.label}
        </span>
      )) : (
        <span className={`text-[14px] ${isDayOff ? 'font-medium text-sky-600' : 'text-slate-300'}`}>{isDayOff ? 'Вых.' : '—'}</span>
      )}
    </span>
  </div>
);
