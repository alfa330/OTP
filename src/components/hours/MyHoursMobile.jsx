import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Calculator, Loader2, MessageSquareText, TriangleAlert } from 'lucide-react';
import { IosBadge, IosModal, iosCard, iosGroupLabel } from '../ui/ios';
import {
  AUCTION_PHONE_BUTTON,
  AuctionPhoneGauge,
  AuctionPhoneGroup,
  AuctionPhoneLinkRow,
  AuctionPhoneMonthHeader,
  useLastPresent,
} from '../resources/ShiftAuctionMobile';
import '../resources/shift-auction-mobile.css';
import './my-hours-mobile.css';
import {
  MY_HOURS_MARKERS,
  MY_HOURS_REQUEST_MAX_LENGTH,
  MY_HOURS_SCALE,
  MY_HOURS_WEEKDAYS_SHORT,
  buildMyHoursBreakdown,
  buildMyHoursDayDetails,
  buildMyHoursMonth,
  formatHours,
  formatHoursNumber,
  formatMoney,
  formatMyHoursMonth,
  formatPercent,
  myHoursCellTone,
  myHoursNormStatus,
  myHoursRequestErrorText,
  shiftMyHoursMonth,
} from './myHoursPhone';

/*
 * Телефонный вид «Моих часов» — только разметка.
 *
 * На компьютере раздел — выпадающий «Выбор месяца», карточка нормы с полосой и
 * полукругом, плитки 2×2, карточка интенсивности и справа календарь с легендой;
 * день открывается окном. На экране в 390 px всё это вставало одной лентой
 * настольных карточек, календарь терял подписи чисел (они жили в подсказке по
 * наведению), а легенда занимала три строки. Здесь раздел — список настроек
 * телефона, как «Мои смены»: крупный заголовок, месяц стрелками, норма одной
 * карточкой, группы строк, календарь ячейками с числом и часами, день —
 * экраном, запрос супервайзеру спрятан за строкой.
 *
 * Все суммы, модели расчёта и подписи зарплаты считает App.jsx теми же
 * помощниками, что настольный вид; правила ячеек и дня — myHoursPhone.js.
 *
 * Ловушки общего слоя разделов (mobile-shell.css): ряды с gap-* и
 * justify-between переносятся, items-start с flex-1 встаёт колонкой,
 * grid-cols-3…6 сводится к двум колонкам. Поэтому строки выровнены по центру,
 * перенос запрещён инлайном, а сетка календаря задана классом в CSS.
 */

const NO_WRAP = { flexWrap: 'nowrap' };

export const MyHoursPhoneHeader = ({ direction = '', month, onMonthChange, disabled = false, children }) => (
  /* Шапка общая с «Моими оценками» (AuctionPhoneMonthHeader): заголовок, под ним
     стрелки парой и подпись месяца с колесом. Здесь — только то, что знает
     раздел: имя, направление и шаг месяца. */
  <AuctionPhoneMonthHeader
    title="Мои часы"
    subtitle={direction}
    month={month}
    label={formatMyHoursMonth(month)}
    onMonthChange={onMonthChange}
    onPrev={() => { const prev = shiftMyHoursMonth(month, -1); if (prev) onMonthChange?.(prev); }}
    onNext={() => { const next = shiftMyHoursMonth(month, 1); if (next) onMonthChange?.(next); }}
    prevDisabled={!shiftMyHoursMonth(month, -1)}
    nextDisabled={!shiftMyHoursMonth(month, 1)}
    disabled={disabled}
  >
    {children}
  </AuctionPhoneMonthHeader>
);

// Перевод посреди месяца: показатели считаются по нескольким направлениям.
export const MyHoursPhoneNotice = ({ segments = [] }) => (
  <div className={`${iosCard} flex items-center gap-3 px-4 py-3`} style={NO_WRAP}>
    <span className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-lg bg-amber-500 text-white">
      <TriangleAlert size={16} aria-hidden="true" />
    </span>
    <span className="min-w-0 flex-1">
      <span className="block text-[15px] font-semibold leading-snug text-slate-900">Показатели месяца — по нескольким направлениям</span>
      {segments.map((segment) => (
        <span key={segment.group_id} className="block text-[13px] tabular-nums text-slate-500">
          {segment.direction_name || segment.group_name} · дни {segment.start_day}–{segment.end_day}
        </span>
      ))}
    </span>
  </div>
);

export const MyHoursPhoneSkeleton = () => (
  <div className="flex flex-col gap-4" aria-hidden="true">
    <div className={`${iosCard} h-[136px] animate-pulse`} />
    <div className={`${iosCard} h-[160px] animate-pulse`} />
    <div className={`${iosCard} h-[340px] animate-pulse`} />
  </div>
);

export const MyHoursPhoneEmpty = () => (
  <div className={`${iosCard} px-4 py-8 text-center text-[16px] text-slate-500`}>
    Нет информации о часах за этот месяц
  </div>
);

/* Полукруг нормы — общий знак разделов (AuctionPhoneGauge), тон = статус нормы. */
const MyHoursNormGauge = ({ percent, tone }) => (
  <AuctionPhoneGauge percent={percent} tone={tone} text={formatPercent(percent)} ariaLabel={`Норма: ${formatPercent(percent)}`} />
);

const NormCard = ({ regular, norm, percent, remaining, overtime }) => {
  const status = myHoursNormStatus(percent, norm);
  const hasNorm = Number(norm) > 0;
  const balance = remaining > 0
    ? `Осталось ${formatHours(remaining)}`
    : overtime > 0 ? `Сверх нормы ${formatHours(overtime)}` : '';
  return (
    <section className={`${iosCard} px-4 pb-3 pt-3.5`}>
      <div className="flex items-center gap-3" style={NO_WRAP}>
        <div className="min-w-0 flex-1">
          <p className="text-[13px] text-slate-500">Отработано</p>
          <p className="mt-0.5 text-[34px] font-bold leading-[40px] tracking-tight tabular-nums text-slate-900">
            {formatHoursNumber(regular)}
            <span className="ml-1 text-[18px] font-semibold text-slate-400">ч</span>
          </p>
          <p className="text-[14px] tabular-nums text-slate-500">{hasNorm ? `из ${formatHours(norm)} нормы` : 'Норма не задана'}</p>
        </div>
        {hasNorm ? <MyHoursNormGauge percent={percent} tone={status.tone} /> : null}
      </div>
      {hasNorm ? (
        <div className="mt-3 flex items-center gap-2 border-t border-slate-100 pt-2.5" style={NO_WRAP}>
          <IosBadge tone={status.tone}>{status.label}</IosBadge>
          <span className="min-w-0 flex-1 truncate text-right text-[14px] tabular-nums text-slate-500">{balance}</span>
        </div>
      ) : null}
    </section>
  );
};

/* Строка «подпись — значение», как в настройках телефона. Значение серое;
   цвет получает только то, у чего есть порог (звонки в час, штрафы). */
const ValueRow = ({ title, value, valueClassName = 'text-slate-500', dotClassName = null, note = null, children = null }) => (
  <div className="sa-m-row px-4 py-3">
    <div className="flex items-center gap-2.5" style={NO_WRAP}>
      {dotClassName ? <span className={`h-2 w-2 shrink-0 rounded-full ${dotClassName}`} aria-hidden="true" /> : null}
      <span className="min-w-0 flex-1 truncate text-[16px] text-slate-900">{title}</span>
      <span className={`shrink-0 text-[16px] tabular-nums ${valueClassName}`}>{value}</span>
    </div>
    {children}
    {note ? <p className="mt-1 text-[13px] leading-snug text-slate-500">{note}</p> : null}
  </div>
);

const planTone = (percent) => (percent >= 100 ? 'green' : percent >= 85 ? 'blue' : 'amber');
const PLAN_TEXT = { green: 'text-green-600', blue: 'text-blue-600', amber: 'text-amber-600' };
const PLAN_BAR = { green: 'bg-green-500', blue: 'bg-blue-500', amber: 'bg-amber-500' };

export const MyHoursPhoneSummary = ({
  regular = 0,
  norm = 0,
  percent = 0,
  remaining = 0,
  overtime = 0,
  breakdown = {},
  isTezOp = false,
  tez = {},
  intensity = {},
  bonuses = 0,
  fines = 0,
  dual = null,
  salary = null,
  calendar = null,
}) => {
  const breakdownRows = buildMyHoursBreakdown(breakdown);
  const tezPlan = tez.plan != null ? Number(tez.plan) : null;
  const tezTone = planTone(Number(tez.percent) || 0);
  return (
    <>
      <NormCard regular={regular} norm={norm} percent={percent} remaining={remaining} overtime={overtime} />

      {breakdownRows.length ? (
        <AuctionPhoneGroup label="Из чего сложились часы">
          {breakdownRows.map((row) => (
            <ValueRow key={row.key} title={row.label} value={formatHours(row.hours)} dotClassName={row.dotClassName} />
          ))}
        </AuctionPhoneGroup>
      ) : null}

      {/* Перевод между моделями посреди месяца: периоды своими карточками —
          тот же блок, что на компьютере, он и так собран карточками iOS. */}
      {dual ? (
        <section>
          <h2 className={`${iosGroupLabel} sa-m-group__label mb-1.5`}>Периоды месяца</h2>
          {dual}
        </section>
      ) : null}

      <AuctionPhoneGroup label={dual ? null : (isTezOp ? 'Успешки и корректировки' : 'Интенсивность и корректировки')}>
        {!dual && isTezOp ? (
          <ValueRow title="Успешки за месяц" value={formatHoursNumber(Math.round(Number(tez.successes) || 0))} />
        ) : null}
        {!dual && isTezOp ? (
          <ValueRow title="План успешек" value={tezPlan != null ? formatHoursNumber(Math.round(tezPlan * 10) / 10) : '—'} />
        ) : null}
        {!dual && isTezOp && tezPlan != null && tezPlan > 0 ? (
          <ValueRow title="Выполнение плана" value={formatPercent(tez.percent)} valueClassName={`font-semibold ${PLAN_TEXT[tezTone]}`} note={tez.caseLabel || null}>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div className={`h-full rounded-full ${PLAN_BAR[tezTone]}`} style={{ width: `${Math.max(0, Math.min(100, Number(tez.percent) || 0))}%` }} />
            </div>
          </ValueRow>
        ) : null}
        {!dual && !isTezOp ? (
          <ValueRow
            title={intensity.perHourLabel}
            value={formatHoursNumber(intensity.perHour)}
            valueClassName={`font-semibold ${intensity.perHourClassName || 'text-slate-900'}`}
          />
        ) : null}
        {!dual && !isTezOp ? (
          <ValueRow title={intensity.totalLabel} value={formatHoursNumber(Math.round(Number(intensity.total) || 0))} />
        ) : null}
        <ValueRow title="Бонусы" value={formatMoney(bonuses)} valueClassName={Number(bonuses) > 0 ? 'text-green-600' : 'text-slate-500'} />
        <ValueRow title="Штрафы" value={formatMoney(fines)} valueClassName={Number(fines) > 0 ? 'text-red-600' : 'text-slate-500'} />
      </AuctionPhoneGroup>

      {salary ? (
        <AuctionPhoneGroup label="Примерная зарплата" hint={salary.note || null}>
          <div className="sa-m-row px-4 py-3">
            <p className="text-[28px] font-bold leading-8 tracking-tight tabular-nums text-slate-900">{formatMoney(salary.amount)}</p>
            {salary.caption ? <p className="mt-1 text-[14px] leading-snug text-slate-500">{salary.caption}</p> : null}
          </div>
          <AuctionPhoneLinkRow icon={Calculator} tileClassName="bg-blue-500" title={salary.calculatorLabel} onClick={salary.onOpenCalculator} />
        </AuctionPhoneGroup>
      ) : null}

      {calendar}
    </>
  );
};

const DetailRow = ({ title, value = null, lines = [], badge = null }) => (
  <div className="sa-m-row px-4 py-2.5">
    <div className="flex items-center gap-2" style={NO_WRAP}>
      <span className="min-w-0 flex-1 truncate text-[16px] font-semibold tabular-nums text-slate-900">{title}</span>
      {value ? <span className="shrink-0 text-[15px] tabular-nums text-slate-500">{value}</span> : null}
    </div>
    {badge ? <p className="mt-1"><IosBadge tone="slate">{badge}</IosBadge></p> : null}
    {lines.filter(Boolean).map((line, index) => (
      // Строки пояснения статичны и не переставляются — индекс здесь честный ключ.
      // eslint-disable-next-line react/no-array-index-key
      <p key={index} className="mt-0.5 text-[14px] leading-snug text-slate-500">{line}</p>
    ))}
  </div>
);

const groupTotal = (text) => <span className="shrink-0 text-[13px] tabular-nums text-slate-500">{text}</span>;
const quoted = (text) => (text ? `«${text}»` : null);

const MyHoursDayScreen = ({ details, composing, onCompose, message, onMessage, sending, error, textareaRef }) => (
  <div className="flex flex-col gap-5">
    <AuctionPhoneGroup>
      <ValueRow title="Итого за день" value={formatHours(details.total)} valueClassName="font-semibold text-slate-900" />
      {details.breakdown.map((row) => (
        <ValueRow key={row.key} title={row.label} value={formatHours(row.hours)} dotClassName={row.dotClassName} />
      ))}
      {details.tezSuccesses !== null ? <ValueRow title="Успешки" value={String(details.tezSuccesses)} /> : null}
    </AuctionPhoneGroup>

    {details.metrics.length ? (
      <AuctionPhoneGroup label="Показатели">
        {details.metrics.map((metric) => <ValueRow key={metric.key} title={metric.label} value={metric.value} />)}
      </AuctionPhoneGroup>
    ) : null}

    {details.trainings.length ? (
      <AuctionPhoneGroup label="Тренинги" right={groupTotal(`в часы ${formatHours(details.trainingCountedHours)}`)}>
        {details.trainings.map((item) => (
          <DetailRow
            key={item.key}
            title={item.time}
            value={item.hours !== null ? formatHours(item.hours) : null}
            badge={item.counted ? null : 'Не засчитывается'}
            lines={[item.reason, quoted(item.comment), item.author ? `Добавил: ${item.author}` : null]}
          />
        ))}
      </AuctionPhoneGroup>
    ) : null}

    {details.technical.length ? (
      <AuctionPhoneGroup label="Технические причины" right={groupTotal(formatHours(details.technicalHours))}>
        {details.technical.map((item) => (
          <DetailRow
            key={item.key}
            title={item.time}
            value={formatHours(item.hours)}
            lines={[item.reason, quoted(item.comment), item.author ? `Добавил: ${item.author}` : null]}
          />
        ))}
      </AuctionPhoneGroup>
    ) : null}

    {details.offline.length ? (
      <AuctionPhoneGroup label="Офлайн активность" right={groupTotal(formatHours(details.offlineHours))}>
        {details.offline.map((item) => (
          <DetailRow
            key={item.key}
            title={item.time}
            value={formatHours(item.hours)}
            lines={[quoted(item.comment), item.author ? `Добавил: ${item.author}` : null]}
          />
        ))}
      </AuctionPhoneGroup>
    ) : null}

    {details.fines.length ? (
      <AuctionPhoneGroup label="Штрафы" right={groupTotal(formatMoney(details.finesTotal))}>
        {details.fines.map((fine) => (
          <DetailRow
            key={fine.key}
            title={fine.reason || 'Штраф'}
            value={formatMoney(fine.amount)}
            lines={[fine.minutes !== null ? `${fine.minutes} мин` : null, quoted(fine.comment)]}
          />
        ))}
      </AuctionPhoneGroup>
    ) : null}

    {details.bonuses.length ? (
      <AuctionPhoneGroup label="Бонусы" right={groupTotal(formatMoney(details.bonusesTotal))}>
        {details.bonuses.map((bonus) => (
          <DetailRow
            key={bonus.key}
            title={bonus.type || 'Бонус'}
            value={formatMoney(bonus.amount)}
            lines={[
              bonus.quantity !== null ? `Количество: ${bonus.quantity}` : null,
              bonus.friendNames ? `Друзья: ${bonus.friendNames}` : null,
              bonus.links || null,
              quoted(bonus.comment),
            ]}
          />
        ))}
      </AuctionPhoneGroup>
    ) : null}

    {/* Запрос спрятан за строкой: день открывают посмотреть чаще, чем оспорить,
        и поле с кнопкой внизу на каждом дне было бы лишним шумом. */}
    {composing ? (
      <AuctionPhoneGroup label="Запрос супервайзеру" hint={`${message.length} / ${MY_HOURS_REQUEST_MAX_LENGTH}`}>
        <div className="px-4 py-3">
          <textarea
            ref={textareaRef}
            value={message}
            onChange={(event) => onMessage(event.target.value.slice(0, MY_HOURS_REQUEST_MAX_LENGTH))}
            maxLength={MY_HOURS_REQUEST_MAX_LENGTH}
            rows={4}
            disabled={sending}
            placeholder="Что не так с часами за этот день"
            className="block w-full resize-none bg-transparent text-[16px] leading-snug text-slate-900 placeholder-slate-400 outline-none"
          />
        </div>
      </AuctionPhoneGroup>
    ) : (
      <AuctionPhoneGroup hint="Если часы за этот день учтены неверно">
        <AuctionPhoneLinkRow icon={MessageSquareText} tileClassName="bg-blue-500" title="Запрос супервайзеру" onClick={onCompose} />
      </AuctionPhoneGroup>
    )}
    {error ? <p className="-mt-3 px-1 text-[13px] leading-snug text-rose-600">{error}</p> : null}
  </div>
);

/*
 * Календарь месяца и экран дня. Состояние (открытый день, текст запроса) живёт
 * здесь, в компоненте модуля: раздел рендерится внутри App, и компонент,
 * объявленный в его теле, терял бы набранный текст на каждом рендере App.
 */
export const MyHoursPhoneCalendar = ({
  op,
  month,
  trainings = [],
  technicalByDay = null,
  offlineByDay = null,
  monthModelCode = 'operator',
  resolveDayModel = null,
  countTrainingHours = null,
  onSendRequest,
  onSent,
}) => {
  const model = useMemo(
    () => buildMyHoursMonth({ op, month, trainings, technicalByDay, offlineByDay, monthModelCode, resolveDayModel, countTrainingHours }),
    [op, month, trainings, technicalByDay, offlineByDay, monthModelCode, resolveDayModel, countTrainingHours],
  );
  const [openDate, setOpenDate] = useState(null);
  const [composing, setComposing] = useState(false);
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const textareaRef = useRef(null);

  const openCell = openDate ? model.cells.find((cell) => cell.date === openDate) || null : null;
  // Экран уезжает ещё треть секунды после закрытия — пусть уезжает с тем же днём.
  const shownCell = useLastPresent(openCell);
  const details = useMemo(() => buildMyHoursDayDetails({ cell: shownCell, op, countTrainingHours }), [shownCell, op, countTrainingHours]);

  useEffect(() => {
    if (composing) textareaRef.current?.focus();
  }, [composing]);

  const openDay = (cell) => {
    setOpenDate(cell.date);
    setComposing(false);
    setMessage('');
    setError('');
  };
  const closeDay = () => {
    if (sending) return;
    setOpenDate(null);
  };
  const send = async () => {
    const text = message.trim();
    if (!openCell || !text || sending) return;
    setSending(true);
    setError('');
    try {
      await onSendRequest?.({ date: openCell.date, hours: Math.round(openCell.hours * 100) / 100, message: text });
      setOpenDate(null);
      setComposing(false);
      setMessage('');
      onSent?.();
    } catch (err) {
      setError(myHoursRequestErrorText(err));
    } finally {
      setSending(false);
    }
  };

  const monthTitle = formatMyHoursMonth(month).split(' ')[0].toLowerCase();

  return (
    <>
      <AuctionPhoneGroup label="Календарь">
        <div className="px-2.5 pb-3 pt-2">
          <div className="mh-m-grid">
            {MY_HOURS_WEEKDAYS_SHORT.map((weekday) => (
              <span key={weekday} className="pb-0.5 text-center text-[12px] font-medium text-slate-400">{weekday}</span>
            ))}
            {Array.from({ length: model.leading }, (_, index) => <span key={`gap-${index}`} aria-hidden="true" />)}
            {model.cells.map((cell) => (
              <button
                key={cell.date}
                type="button"
                onClick={() => openDay(cell)}
                aria-label={`${cell.day}, ${monthTitle}: ${formatHours(cell.hours)}`}
                className={`mh-m-cell flex flex-col items-center justify-center rounded-[10px] ${myHoursCellTone(cell)} ${cell.isToday ? 'ring-2 ring-inset ring-blue-600' : ''}`}
              >
                <span className="text-[11px] font-medium leading-[14px] tabular-nums opacity-75">{cell.day}</span>
                <span className="text-[13px] font-semibold leading-4 tabular-nums">{cell.hours > 0 ? formatHoursNumber(cell.hours) : ' '}</span>
                <span className="mt-1 flex h-1.5 items-center gap-0.5" style={NO_WRAP} aria-hidden="true">
                  {cell.markers.map((marker) => (
                    <span key={marker} className={`h-1.5 w-1.5 rounded-full ${MY_HOURS_MARKERS[marker].className}`} />
                  ))}
                </span>
              </button>
            ))}
          </div>
          {/* Легенда: шкала часов и только те метки, что есть в этом месяце. */}
          <div className="mt-3 flex items-center gap-x-3 gap-y-1.5 border-t border-slate-100 px-1.5 pt-2.5 text-[12px] text-slate-500" style={{ flexWrap: 'wrap' }}>
            <span className="inline-flex items-center gap-1.5" style={NO_WRAP}>
              {MY_HOURS_SCALE.map((step) => (
                <span key={step.key} className="inline-flex items-center gap-0.5 tabular-nums" style={NO_WRAP}>
                  <span className={`h-2.5 w-2.5 rounded-[3px] ${step.className}`} aria-hidden="true" />
                  {step.label}
                </span>
              ))}
              <span>ч</span>
            </span>
            {model.markers.map((marker) => (
              <span key={marker} className="inline-flex items-center gap-1" style={NO_WRAP}>
                <span className={`h-1.5 w-1.5 rounded-full ${MY_HOURS_MARKERS[marker].className}`} aria-hidden="true" />
                {MY_HOURS_MARKERS[marker].label}
              </span>
            ))}
          </div>
        </div>
      </AuctionPhoneGroup>

      <IosModal
        open={Boolean(openCell)}
        onClose={closeDay}
        title={details?.title || ''}
        subtitle={details?.subtitle || null}
        footer={composing ? (
          <button type="button" onClick={send} disabled={sending || !message.trim()} className={AUCTION_PHONE_BUTTON.blue}>
            {sending ? <Loader2 size={18} className="animate-spin" aria-hidden="true" /> : null}
            {sending ? 'Отправка…' : 'Отправить запрос'}
          </button>
        ) : null}
      >
        {details ? (
          <MyHoursDayScreen
            details={details}
            composing={composing}
            onCompose={() => setComposing(true)}
            message={message}
            onMessage={setMessage}
            sending={sending}
            error={error}
            textareaRef={textareaRef}
          />
        ) : null}
      </IosModal>
    </>
  );
};
