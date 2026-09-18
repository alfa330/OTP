import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import FaIcon from '../common/FaIcon';
import {
  AUCTION_PHONE_BUTTON,
  AUCTION_PHONE_TIME_INPUT,
  AuctionPhoneCheckRow,
  AuctionPhoneChips,
  AuctionPhoneDayStrip,
  AuctionPhoneField,
  AuctionPhoneGroup,
  AuctionPhoneLinkRow,
  AuctionPhonePill,
  AuctionPhoneRow,
  AuctionPhoneSearch,
  AuctionPhoneTabs,
  useLastPresent,
} from '../resources/ShiftAuctionMobile';
import '../resources/shift-auction-mobile.css';
import './work-schedules-mobile.css';

/*
 * Телефонный вид «Графиков работы» (планировщика смен) — только разметка.
 *
 * На компьютере раздел — сетка «люди × дни»: слева столбец имён на 256 px,
 * справа колонки дней, над ними линейка часов, слева от сетки календарь и
 * четыре фильтра, правка дня — окно на 720 px с четырьмя вкладками. На экране
 * в 390 px от сетки видно полтора дня, столбец имён занимает две трети ширины,
 * а окно правки открывается на четыре экрана прокрутки. Здесь раздел — список
 * настроек телефона: период переключателем, дни полосой, люди списком по
 * направлениям, день человека — экраном с теми же четырьмя вкладками.
 *
 * Данные и решения (что считается сменой, сколько часов, можно ли править)
 * считает App.jsx теми же помощниками, что и настольная сетка: здесь нет ни
 * одного правила раздела, только то, как строка выглядит. Что показать в
 * ячейке и как подписать период — workSchedulesPhone.js.
 *
 * Полоса дней, строки, капсулы и поля — общие с «Аукционом смен» (sa-m-*),
 * файл стилей аукциона подключается отсюда же: планировщик живёт в главном
 * чанке, а аукцион грузится лениво, и без этого импорта полоса дней осталась
 * бы без стекла и прилипания.
 *
 * ЦВЕТ — утилитами Tailwind, а не своими hex в CSS: тёмный слой портала
 * перекрашивает утилиты, а собственные цвета файла стилей он не видит.
 *
 * ОБЩИЙ СЛОЙ РАЗДЕЛОВ (mobile-shell.css) переносит ряды с gap по строкам,
 * ставит колонкой .flex.items-start, режет ширину у всего с «w-[» и обнуляет
 * минимальную ширину у «min-w-[». Поэтому строки здесь выровнены по центру,
 * середина строки — flex-1 с нулевой основой, а имена классов не содержат
 * «-actions», «-tabs», «-filters», «toolbar» и «topbar»: по этим подстрокам
 * общий слой прячет настольные панели.
 */

const NO_WRAP = { flexWrap: 'nowrap' };

export {
  AUCTION_PHONE_BUTTON as WS_PHONE_BUTTON,
  AUCTION_PHONE_TIME_INPUT as WS_PHONE_TIME_INPUT,
  AuctionPhoneCheckRow as WsPhoneCheckRow,
  AuctionPhoneChips as WsPhoneChips,
  AuctionPhoneDayStrip as WsPhoneDayStrip,
  AuctionPhoneField as WsPhoneField,
  AuctionPhoneGroup as WsPhoneGroup,
  AuctionPhoneLinkRow as WsPhoneLinkRow,
  AuctionPhonePill as WsPhonePill,
  AuctionPhoneRow as WsPhoneRow,
  AuctionPhoneSearch as WsPhoneSearch,
  AuctionPhoneTabs as WsPhoneTabs,
  useLastPresent,
};

const CAPTION_TONES = {
  shift: 'text-slate-900',
  off: 'text-sky-600',
  blocked: 'text-rose-600',
  none: 'text-slate-400',
};

/*
 * Строка над полосой дней: стрелки парой слева, подпись периода за ними,
 * «Сегодня» справа и только пока выбран не сегодняшний день. Подписи стрелок
 * приходят снаружи — в «Месяце» это уже не «неделя».
 */
export const WorkSchedulesPeriodHeader = ({
  label,
  onPrev,
  onNext,
  onToday,
  showToday = false,
  prevLabel = 'Предыдущий период',
  nextLabel = 'Следующий период',
}) => (
  <div className="ws-m-period flex items-center gap-0.5" style={NO_WRAP}>
    <button
      type="button"
      onClick={onPrev}
      aria-label={prevLabel}
      className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-blue-600 active:opacity-60"
    >
      <ChevronLeft size={20} aria-hidden="true" />
    </button>
    <button
      type="button"
      onClick={onNext}
      aria-label={nextLabel}
      className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-blue-600 active:opacity-60"
    >
      <ChevronRight size={20} aria-hidden="true" />
    </button>
    <span className="min-w-0 flex-1 truncate text-[15px] font-semibold text-slate-900">{label}</span>
    {showToday ? (
      <button
        type="button"
        onClick={onToday}
        className="shrink-0 rounded-full px-2 text-[15px] font-semibold text-blue-600 active:opacity-60"
      >
        Сегодня
      </button>
    ) : null}
  </div>
);

/*
 * Итог периода строкой под заголовком: люди, смены, часы. Числа крупнее
 * подписей — в настольном подвале их читают так же, сверху вниз.
 */
export const WorkSchedulesSummary = ({ items = [] }) => {
  const visible = items.filter(Boolean);
  if (!visible.length) return null;
  return (
    <div className="ws-m-summary flex gap-2" style={NO_WRAP}>
      {visible.map((item) => (
        <div key={item.label} className="ws-m-summary__cell flex-1 rounded-2xl bg-white px-3 py-2">
          <div className="truncate text-[20px] font-semibold tabular-nums text-slate-900">{item.value}</div>
          <div className="truncate text-[12px] text-slate-500">{item.label}</div>
        </div>
      ))}
    </div>
  );
};

/*
 * Человек в списке: имя, направление и ставка слева, смена дня (или сводка
 * периода) справа. Ставка стоит рядом с направлением, как в столбце имён на
 * компьютере, и пишется только когда задана.
 */
export const WorkSchedulesOperatorRow = ({
  name,
  direction = null,
  rate = null,
  caption = '—',
  tone = 'none',
  note = null,
  marker = null,
  onClick,
}) => (
  <button
    type="button"
    onClick={onClick}
    className="sa-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left"
  >
    <span className="min-w-0 flex-1">
      <span className="block truncate text-[16px] font-semibold text-slate-900">{name}</span>
      <span className="block truncate text-[13px] text-slate-500">
        {direction || 'Без направления'}
        {rate ? <span className="text-slate-400"> · ставка {rate}</span> : null}
      </span>
      {marker ? <span className="block truncate text-[13px] text-amber-600">{marker}</span> : null}
    </span>
    {/* Часы — второй строкой ПОД временем, а не под направлением: слева уже две
        строки о человеке, и «9 ч» там читалось как часть направления. */}
    <span className="shrink-0 text-right">
      <span className={`block text-[15px] font-semibold tabular-nums ${CAPTION_TONES[tone] || CAPTION_TONES.none}`}>
        {caption}
      </span>
      {note ? <span className="block text-[12px] tabular-nums text-slate-400">{note}</span> : null}
    </span>
    <ChevronRight size={18} className="shrink-0 text-slate-300" aria-hidden="true" />
  </button>
);

/*
 * Строка «подпись — значение»: отбор, сортировка, длительность смены. Подпись
 * обычного начертания, значение серым у правого края — как в «Настройках»
 * телефона. Общий примитив строки (AuctionPhoneRow) пишет подпись полужирным:
 * там это заголовок записи, а здесь — название поля.
 */
export const WorkSchedulesValueRow = ({ label, value = null, children = null, chevron = false, onClick = null }) => {
  const body = (
    <>
      <span className="min-w-0 flex-1 truncate text-[16px] text-slate-900">{label}</span>
      {children}
      {value !== null ? (
        <span className="shrink-0 truncate text-[16px] text-slate-500" style={{ maxWidth: '60%' }}>{value}</span>
      ) : null}
      {chevron ? <ChevronRight size={18} className="shrink-0 text-slate-300" aria-hidden="true" /> : null}
    </>
  );
  const className = 'sa-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left';
  if (onClick) {
    return <button type="button" onClick={onClick} className={className}>{body}</button>;
  }
  return <div className={className}>{body}</div>;
};

/*
 * День в экране человека: дата слева, смена справа — та же строка, только
 * первым идёт день, а не имя. Без onClick строка перестаёт быть кнопкой и
 * теряет шеврон: у тренера раздел только на просмотр, и стрелка обещала бы
 * переход, которого нет.
 */
export const WorkSchedulesDayRow = ({ title, subtitle = null, caption = '—', tone = 'none', hours = null, onClick = null }) => {
  const body = (
    <>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[16px] text-slate-900">{title}</span>
        {subtitle ? <span className="block truncate text-[13px] text-slate-500">{subtitle}</span> : null}
      </span>
      <span className="shrink-0 text-right">
        <span className={`block text-[15px] font-semibold tabular-nums ${CAPTION_TONES[tone] || CAPTION_TONES.none}`}>
          {caption}
        </span>
        {hours ? <span className="block text-[12px] tabular-nums text-slate-400">{hours}</span> : null}
      </span>
      {onClick ? <ChevronRight size={18} className="shrink-0 text-slate-300" aria-hidden="true" /> : null}
    </>
  );
  const className = 'sa-m-row flex w-full items-center gap-3 px-4 py-2.5 text-left';
  if (!onClick) return <div className={className}>{body}</div>;
  return <button type="button" onClick={onClick} className={className}>{body}</button>;
};

const SHIFT_BADGE_TONES = {
  night: 'bg-amber-100 text-amber-700',
  practice: 'bg-emerald-100 text-emerald-700',
  phone: 'bg-cyan-50 text-indigo-700',
  editing: 'bg-blue-600 text-white',
};

/*
 * Смена в экране дня: время крупно, часы рядом, под ними метки («+1 день»,
 * «Практика в офисе»). Кнопки правки и удаления — иконками справа: подписи
 * «Изменить» и «Удалить» занимали бы половину строки.
 */
export const WorkSchedulesShiftRow = ({
  time,
  hours,
  badges = [],
  breaks = [],
  onEdit = null,
  onRemove = null,
  busy = false,
}) => (
  <div className="sa-m-row px-4 py-3">
    <div className="flex items-center gap-2.5" style={NO_WRAP}>
      <span className="min-w-0 flex-1 truncate text-[17px] font-semibold tabular-nums text-slate-900">{time}</span>
      <span className="shrink-0 text-[15px] tabular-nums text-slate-500">{hours}</span>
      {onEdit ? (
        <button
          type="button"
          onClick={onEdit}
          aria-label="Изменить смену"
          className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-600 active:opacity-60"
        >
          <FaIcon className="fas fa-pen text-[12px]" />
        </button>
      ) : null}
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          disabled={busy}
          aria-label="Удалить смену"
          className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-rose-50 text-rose-600 active:opacity-60 disabled:opacity-50"
        >
          <FaIcon className={`fas ${busy ? 'fa-spinner fa-spin' : 'fa-trash-alt'} text-[12px]`} />
        </button>
      ) : null}
    </div>
    {badges.length ? (
      <div className="mt-1 flex flex-wrap gap-1.5">
        {badges.map((badge) => (
          <span
            key={badge.label}
            className={`rounded-full px-2 py-0.5 text-[12px] font-semibold ${SHIFT_BADGE_TONES[badge.tone] || SHIFT_BADGE_TONES.night}`}
          >
            {badge.label}
          </span>
        ))}
      </div>
    ) : null}
    {breaks.length ? (
      <p className="mt-1.5 text-[13px] leading-snug tabular-nums text-slate-500">
        <FaIcon className="fas fa-mug-hot mr-1.5 text-amber-500" />
        {breaks.join(' · ')}
      </p>
    ) : null}
  </div>
);

const FLAG_TONES = {
  pending: 'bg-amber-100 text-amber-700',
  penalty: 'bg-rose-100 text-rose-700',
  agreed: 'bg-sky-100 text-sky-700',
  confirmed: 'bg-emerald-100 text-emerald-700',
  rejected: 'bg-slate-200 text-slate-600',
};

/*
 * Отметка автоконтроля во вкладке «Контроль»: название, минуты и решение.
 * Кнопки стоят второй строкой во всю ширину — на 390 px пара кнопок рядом с
 * подписью «Тех.причина (по статусам)» ужималась до двух букв.
 */
export const WorkSchedulesFlagRow = ({ label, minutes, statusLabel, statusTone = 'pending', actions = [], note = null }) => (
  <div className="sa-m-row px-4 py-3">
    <div className="flex items-center gap-2.5" style={NO_WRAP}>
      <span className="min-w-0 flex-1 truncate text-[16px] text-slate-900">{label}</span>
      <span className="shrink-0 text-[15px] tabular-nums text-slate-500">{minutes}</span>
      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[12px] font-semibold ${FLAG_TONES[statusTone] || FLAG_TONES.pending}`}>
        {statusLabel}
      </span>
    </div>
    {note ? <p className="mt-1 text-[13px] leading-snug text-slate-500">{note}</p> : null}
    {actions.length ? (
      <div className="mt-2 flex gap-2">
        {actions.map((action) => (
          <button
            key={action.label}
            type="button"
            onClick={action.onClick}
            disabled={action.disabled}
            className={`flex h-9 flex-1 items-center justify-center rounded-xl px-3 text-[15px] font-semibold transition active:scale-[0.98] disabled:opacity-40 ${
              action.tone === 'danger'
                ? 'bg-rose-50 text-rose-600'
                : action.tone === 'primary'
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-100 text-slate-700'
            }`}
          >
            {action.busy ? <FaIcon className="fas fa-spinner fa-spin" /> : action.label}
          </button>
        ))}
      </div>
    ) : null}
  </div>
);

/* Пустое место списка — значок, строка и объяснение, как в «Настройках». */
export const WorkSchedulesEmpty = ({ icon = 'fa-calendar-xmark', title, hint = null }) => (
  <div className="px-4 py-9 text-center">
    <div className="mx-auto mb-2 grid h-11 w-11 place-items-center rounded-2xl bg-slate-100 text-slate-400">
      <FaIcon className={`fas ${icon}`} />
    </div>
    <div className="text-[15px] font-semibold text-slate-700">{title}</div>
    {hint ? <div className="mt-0.5 text-[13px] leading-snug text-slate-500">{hint}</div> : null}
  </div>
);

/* Сообщение об ошибке — одной плашкой, как в «Моих сменах». */
export const WorkSchedulesError = ({ text }) => (
  <p className="rounded-xl bg-rose-50 px-4 py-3 text-[14px] leading-snug text-rose-700">{text}</p>
);
