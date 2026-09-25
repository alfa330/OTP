import React, { useMemo, useState } from 'react';
import { Check, FileSpreadsheet, Loader2, Lock, Pencil, Plus, RefreshCw, Trash2, TriangleAlert } from 'lucide-react';
import { APPLE_FONT, IosBadge, IosModal, IosSegmented, iosCard } from '../ui/ios';
import {
  AUCTION_PHONE_BUTTON,
  AuctionPhoneChips,
  AuctionPhoneGroup,
  AuctionPhoneRow,
  AuctionPhoneMonthHeader,
  useLastPresent,
} from '../resources/ShiftAuctionMobile';
import '../resources/shift-auction-mobile.css';
import './hours-accounting-mobile.css';
import {
  HOURS_PHONE_BONUS_TYPES,
  HOURS_PHONE_CHAT_FIELDS,
  HOURS_PHONE_FINE_REASONS,
  HOURS_PHONE_MARKERS,
  HOURS_PHONE_WEEKDAYS,
  buildHoursPhoneDayFields,
  buildHoursPhoneMonth,
  buildHoursPhoneOperatorTotals,
  buildHoursPhoneSectionTotals,
  formatHoursPhoneDay,
  formatHoursPhoneHours,
  formatHoursPhoneMoney,
  formatHoursPhoneMonth,
  hoursPhoneBonusHasQuantity,
  hoursPhoneBonusQuantityLabel,
  hoursPhoneDirectionsLabel,
  hoursPhoneFineAmount,
  hoursPhoneFineHint,
  hoursPhoneFineIsAuto,
  hoursPhoneItemTime,
  hoursPhoneMonthOptions,
  hoursPhoneOperatorRows,
  hoursPhoneOperatorValue,
  shiftHoursPhoneMonth,
} from './hoursAccountingPhone';

/*
 * Телефонный вид «Учета часов» — только разметка.
 *
 * На компьютере раздел — таблица шириной в тридцать шесть колонок: имя, ставка,
 * норма, дни месяца и итоги справа. На экране 390 px от неё видно два столбца,
 * а листать её приходится вбок внутри вертикальной прокрутки — самый неудобный
 * жест, какой бывает. Поэтому здесь раздел разложен по уровням, как список
 * настроек телефона: отбор строками, операторы списком с ОДНИМ числом
 * выбранного показателя, месяц оператора — экраном с ячейками, день — экраном
 * с правкой. Числа считает hoursAccountingPhone.js теми же формулами, что
 * настольная строка и подвал; данные, права и запросы остаются в App.jsx.
 *
 * Ловушки общего слоя разделов (mobile-shell.css): ряды с gap-* переносятся,
 * items-start с flex-1 встаёт колонкой, сетки из утилит сводятся к одной
 * колонке, а классы со словами toolbar/-actions/-tabs/-filters получают
 * flex-wrap. Поэтому строки выровнены по центру, перенос запрещён инлайном,
 * сетка календаря задана классом из CSS, и таких слов в именах классов нет.
 */

const NO_WRAP = { flexWrap: 'nowrap' };

/* Экран выбора из списка: строки с галочкой у выбранного — как «Язык» или
   «Страна» в настройках телефона. Один экран на группу, направления, отчёт и
   показатель: четыре одинаковых списка, разошедшихся бы поодиночке. */
const HoursPhonePicker = ({ open, onClose, title, subtitle, options = [], isSelected, onPick, hint = null }) => (
  <IosModal open={open} onClose={onClose} title={title} subtitle={subtitle}>
    <AuctionPhoneGroup hint={hint}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onPick(option.value)}
          className="sa-m-row flex w-full items-center gap-3 px-4 py-3 text-left"
          style={NO_WRAP}
        >
          <span className="min-w-0 flex-1 truncate text-[16px] text-slate-900">{option.label}</span>
          {option.caption ? <span className="shrink-0 text-[14px] tabular-nums text-slate-400">{option.caption}</span> : null}
          <span className="grid h-5 w-5 shrink-0 place-items-center">
            {isSelected(option.value) ? <Check size={18} className="text-blue-600" aria-hidden="true" /> : null}
          </span>
        </button>
      ))}
    </AuctionPhoneGroup>
  </IosModal>
);

/* Строка отбора: подпись слева, выбранное значение и шеврон справа. */
const HoursPhoneFilterRow = ({ title, value, onClick, disabled = false, note = null }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    className="sa-m-row flex w-full items-center gap-3 px-4 py-3 text-left disabled:opacity-40"
    style={NO_WRAP}
  >
    <span className="min-w-0 flex-1">
      <span className="block truncate text-[16px] text-slate-900">{title}</span>
      {note ? <span className="block truncate text-[13px] text-slate-500">{note}</span> : null}
    </span>
    <span className="shrink-0 max-w-[52%] truncate text-right text-[16px] text-slate-500">{value}</span>
    <svg width="8" height="13" viewBox="0 0 8 13" fill="none" className="shrink-0 text-slate-300" aria-hidden="true">
      <path d="M1.5 1.5L6.5 6.5l-5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  </button>
);

/* Карточка итога раздела: крупное число выбранного показателя и остальные
   колонки подвала строками под ним. Если у показателя итога нет (КВЗ по
   отделу на компьютере не считается) — крупного числа не рисуем вовсе. */
const HoursPhoneTotals = ({ totals, rowsCount }) => (
  <section className={`${iosCard} px-4 pb-1 pt-3.5`}>
    {totals.value === '—' ? null : (
      <>
        <p className="text-[13px] text-slate-500">{totals.caption}</p>
        <p className="mt-0.5 text-[32px] font-bold leading-[38px] tracking-tight tabular-nums text-slate-900">{totals.value}</p>
      </>
    )}
    <p className={totals.value === '—' ? 'text-[13px] text-slate-500' : 'mt-0.5 text-[13px] text-slate-500'}>
      {rowsCount === 1 ? '1 сотрудник' : `Сотрудников: ${rowsCount}`}
    </p>
    <div className="mt-2.5 border-t border-slate-100">
      {totals.rows.map((row) => (
        <div key={row.key} className="flex items-center gap-3 py-2" style={NO_WRAP}>
          <span className="min-w-0 flex-1 truncate text-[15px] text-slate-500">{row.label}</span>
          <span className={`shrink-0 text-[15px] tabular-nums ${row.valueClassName || 'text-slate-900'}`}>{row.value}</span>
        </div>
      ))}
    </div>
  </section>
);

/* Сетка месяца оператора. Число дня сверху, значение показателя под ним,
   метки точками — как на компьютере, только в три строки вместо одной. */
const HoursPhoneCalendar = ({ model, month, onOpenDay, onOpenForeign }) => (
  <AuctionPhoneGroup label="Календарь">
    <div className="px-2.5 pb-3 pt-2">
      <div className="ha-m-grid">
        {HOURS_PHONE_WEEKDAYS.map((weekday) => (
          <span key={weekday} className="pb-0.5 text-center text-[12px] font-medium text-slate-400">{weekday}</span>
        ))}
        {Array.from({ length: model.leading }, (_, index) => <span key={`gap-${index}`} aria-hidden="true" />)}
        {model.cells.map((cell) => (
          <button
            key={cell.date}
            type="button"
            onClick={() => (cell.locked ? onOpenForeign(cell) : onOpenDay(cell))}
            aria-label={cell.locked
              ? `${cell.day}: другая группа${cell.lockedGroup ? `, ${cell.lockedGroup}` : ''}`
              : `${cell.day}: ${cell.text || 'нет данных'}`}
            className={`ha-m-cell flex flex-col items-center justify-center rounded-[10px] ${cell.tone} ${cell.isToday ? 'ring-2 ring-inset ring-blue-600' : ''}`}
          >
            <span className="text-[11px] font-medium leading-[14px] tabular-nums opacity-75">{cell.day}</span>
            {cell.locked
              ? <Lock size={11} className="mt-0.5" aria-hidden="true" />
              : <span className="text-[13px] font-semibold leading-4 tabular-nums">{cell.text || ' '}</span>}
            <span className="mt-1 flex h-1.5 items-center gap-0.5" style={NO_WRAP} aria-hidden="true">
              {cell.markers.map((marker) => (
                <span key={marker} className={`h-1.5 w-1.5 rounded-full ${HOURS_PHONE_MARKERS[marker].className}`} />
              ))}
            </span>
          </button>
        ))}
      </div>
      {model.markers.length ? (
        <div className="mt-3 flex items-center gap-x-3 gap-y-1.5 border-t border-slate-100 px-1.5 pt-2.5 text-[12px] text-slate-500" style={{ flexWrap: 'wrap' }}>
          {model.markers.map((marker) => (
            <span key={marker} className="inline-flex items-center gap-1" style={NO_WRAP}>
              <span className={`h-1.5 w-1.5 rounded-full ${HOURS_PHONE_MARKERS[marker].className}`} aria-hidden="true" />
              {HOURS_PHONE_MARKERS[marker].label}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  </AuctionPhoneGroup>
);

/* Поле-число строкой: подпись слева, поле справа — как «Порт» в настройках. */
const HoursPhoneNumberRow = ({ label, unit = '', value, onChange, step = '0.01', disabled = false, hint = null, readOnly = false }) => (
  <label className="sa-m-row flex w-full items-center gap-3 px-4 py-2" style={NO_WRAP}>
    <span className="min-w-0 flex-1">
      <span className="block text-[16px] text-slate-900">{label}</span>
      {hint ? <span className="block text-[13px] leading-snug text-slate-500">{hint}</span> : null}
    </span>
    <input
      type="number"
      inputMode="decimal"
      step={step}
      className="ha-m-num"
      value={value}
      disabled={disabled}
      readOnly={readOnly}
      onChange={(event) => onChange?.(event.target.value)}
    />
    {/* Подпись единицы рисуется всегда, даже пустой: иначе поля в строках
        без единицы («Звонки») стояли бы правее соседних. */}
    <span className="ha-m-unit shrink-0 text-[15px] text-slate-400">{unit}</span>
  </label>
);

/* Пустая группа: одна приглушённая строка внутри карточки, а не подпись под
   пустотой. Пять «записей нет» подряд без карточек читались бы как обрыв
   списка, а не как пять разделов, в которых пока пусто. */
const HoursPhoneEmptyRow = ({ text }) => (
  <div className="sa-m-row flex items-center px-4 py-3 text-[15px] text-slate-400">{text}</div>
);

/* Запись активности (тренинг, техсбой, офлайн): время и длительность строкой,
   причина и комментарий под ней, действия — кнопками у правого края. */
const HoursPhoneActivityRow = ({ item, hours, badge = null, readOnlyNote = '', onEdit = null, onDelete = null, busy = false }) => (
  <div className="sa-m-row px-4 py-2.5">
    <div className="flex items-center gap-2" style={NO_WRAP}>
      <span className="min-w-0 flex-1 truncate text-[16px] font-semibold tabular-nums text-slate-900">{hoursPhoneItemTime(item)}</span>
      <span className="shrink-0 text-[15px] tabular-nums text-slate-500">{formatHoursPhoneHours(hours)}</span>
      {onEdit ? (
        <button type="button" onClick={onEdit} disabled={busy} className="ha-m-icon text-blue-600" aria-label="Изменить">
          <Pencil size={17} aria-hidden="true" />
        </button>
      ) : null}
      {onDelete ? (
        <button type="button" onClick={onDelete} disabled={busy} className="ha-m-icon text-rose-600" aria-label="Удалить">
          <Trash2 size={17} aria-hidden="true" />
        </button>
      ) : null}
    </div>
    {item.reason ? <p className="mt-0.5 text-[14px] leading-snug text-slate-600">{item.reason}</p> : null}
    {item.comment ? <p className="mt-0.5 text-[14px] leading-snug text-slate-500">«{item.comment}»</p> : null}
    <div className="mt-1 flex items-center gap-1.5" style={{ flexWrap: 'wrap' }}>
      {badge}
      {readOnlyNote ? <IosBadge tone="green">{readOnlyNote}</IosBadge> : null}
      {item.created_by_name ? <span className="text-[13px] text-slate-400">Добавил: {item.created_by_name}</span> : null}
    </div>
  </div>
);

const GroupAddButton = ({ onClick, busy = false, label = 'Добавить' }) => (
  <button type="button" onClick={onClick} disabled={busy} className="ha-m-add text-blue-600">
    {busy ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Plus size={15} aria-hidden="true" />}
    {label}
  </button>
);

/* Экран дня: те же поля, тренинги, техсбои, офлайн, штрафы и бонусы, что в
   настольном окне ячейки, — но списком телефона, а не сеткой из шести колонок. */
const HoursPhoneDayScreen = ({ day, activities, isChatModel, isTezOpContext, successes }) => {
  const model = day.cellModel;
  const operatorId = day.selectedCell?.operator?.operator_id;
  const dayNumber = day.selectedCell?.day;
  const fields = useMemo(() => buildHoursPhoneDayFields({ isChatModel }), [isChatModel]);
  const trainings = activities.getTrainings(operatorId, dayNumber);
  const technical = activities.getTechnical(operatorId, dayNumber);
  const offline = activities.getOffline(operatorId, dayNumber);

  return (
    <div className="flex flex-col gap-5">
      <AuctionPhoneGroup label="Показатели дня">
        {fields.map((field) => (
          <HoursPhoneNumberRow
            key={field.key}
            label={field.label}
            unit={field.unit}
            step={field.step}
            value={model[field.key] ?? ''}
            onChange={(value) => day.updateField(field.key, value)}
          />
        ))}
        {isChatModel ? HOURS_PHONE_CHAT_FIELDS.map((field) => (
          <HoursPhoneNumberRow
            key={field.key}
            label={field.label}
            unit={field.unit}
            step={field.step}
            value={model.chat_metrics?.[field.key] ?? ''}
            onChange={(value) => day.updateChatMetric(field.key, value)}
          />
        )) : null}
        {isChatModel ? (
          <HoursPhoneNumberRow
            label="Средняя оценка"
            unit="из 5"
            step="0.01"
            value={model.chat_metrics?.avg_score ?? ''}
            readOnly
            disabled
            hint="Считается по данным Chat2Desk"
          />
        ) : null}
        {isTezOpContext ? (
          <div className="sa-m-row flex items-center gap-3 px-4 py-3" style={NO_WRAP}>
            <span className="min-w-0 flex-1">
              <span className="block text-[16px] text-slate-900">Успешки</span>
              <span className="block text-[13px] text-slate-500">Считает ночной пересчёт по базе лидов</span>
            </span>
            <span className="shrink-0 text-[16px] font-semibold tabular-nums text-slate-900">{successes}</span>
          </div>
        ) : null}
      </AuctionPhoneGroup>

      <AuctionPhoneGroup
        label="Тренинги"
        right={<GroupAddButton onClick={() => activities.onAddTraining(operatorId, dayNumber)} busy={activities.trainingBusy} />}
      >
        {trainings.length ? null : <HoursPhoneEmptyRow text="Тренингов нет" />}
        {trainings.map((item) => (
          <HoursPhoneActivityRow
            key={item.id}
            item={item}
            hours={activities.trainingDuration(item)}
            badge={item.count_in_hours === false ? <IosBadge tone="amber">Не засчитывается</IosBadge> : <IosBadge tone="green">В часы</IosBadge>}
            readOnlyNote={(item.read_only || item.is_practice_shift) ? 'Из графика смен' : ''}
            onEdit={(item.read_only || item.is_practice_shift) ? null : () => activities.onEditTraining(operatorId, dayNumber, item)}
            onDelete={(item.read_only || item.is_practice_shift) ? null : () => activities.onDeleteTraining(item.id)}
            busy={activities.trainingBusy}
          />
        ))}
      </AuctionPhoneGroup>

      <AuctionPhoneGroup label="Технические причины">
        {technical.length ? null : <HoursPhoneEmptyRow text="Технических причин нет" />}
        {technical.map((item, index) => (
          <HoursPhoneActivityRow
            key={`${item.id || 'tech'}-${index}`}
            item={item}
            hours={activities.technicalDuration(item)}
            onDelete={() => activities.onDeleteTechnical(item.id)}
            busy={activities.technicalBusy}
          />
        ))}
      </AuctionPhoneGroup>

      <AuctionPhoneGroup
        label="Офлайн активность"
        right={<GroupAddButton onClick={() => activities.onAddOffline(operatorId, dayNumber)} busy={activities.offlineBusy} />}
      >
        {offline.length ? null : <HoursPhoneEmptyRow text="Офлайн активности нет" />}
        {offline.map((item, index) => (
          <HoursPhoneActivityRow
            key={`${item.id || 'offline'}-${index}`}
            item={item}
            hours={activities.offlineDuration(item)}
            readOnlyNote={(item.read_only || item.is_practice_shift) ? 'Из графика смен' : ''}
            onEdit={(item.read_only || item.is_practice_shift) ? null : () => activities.onEditOffline(operatorId, dayNumber, item)}
            onDelete={(item.read_only || item.is_practice_shift) ? null : () => activities.onDeleteOffline(item.id)}
            busy={activities.offlineBusy}
          />
        ))}
      </AuctionPhoneGroup>

      <AuctionPhoneGroup
        label="Штрафы"
        right={<GroupAddButton onClick={day.addFine} />}
      >
        {(model.fines || []).length ? null : <HoursPhoneEmptyRow text="Штрафов нет" />}
        {(model.fines || []).map((fine, index) => (
          <div key={fine.id ?? index} className="sa-m-row px-4 py-2.5">
            <div className="flex items-center gap-2 pb-1" style={NO_WRAP}>
              <span className="min-w-0 flex-1 truncate text-[16px] font-semibold text-slate-900">
                {formatHoursPhoneMoney(hoursPhoneFineAmount(fine))}
              </span>
              <button type="button" onClick={() => day.removeFine(index)} className="ha-m-icon text-rose-600" aria-label={`Удалить штраф ${index + 1}`}>
                <Trash2 size={17} aria-hidden="true" />
              </button>
            </div>
            <select
              className="ha-m-select ha-m-select--wide"
              value={fine.reason ?? ''}
              onChange={(event) => day.updateFine(index, 'reason', event.target.value)}
              aria-label={`Причина штрафа ${index + 1}`}
            >
              <option value="">Причина не выбрана</option>
              {HOURS_PHONE_FINE_REASONS.map((reason) => <option key={reason} value={reason}>{reason}</option>)}
            </select>
            {String(fine.reason ?? '') === 'Опоздание' ? (
              <label className="mt-2 flex items-center gap-3" style={NO_WRAP}>
                <span className="min-w-0 flex-1 text-[15px] text-slate-500">Минут опоздания</span>
                <input
                  type="number"
                  inputMode="numeric"
                  min="0"
                  className="ha-m-num"
                  value={fine.minutes ?? 0}
                  onChange={(event) => day.updateFine(index, 'minutes', event.target.value)}
                />
              </label>
            ) : null}
            {!hoursPhoneFineIsAuto(fine) ? (
              <label className="mt-2 flex items-center gap-3" style={NO_WRAP}>
                <span className="min-w-0 flex-1 text-[15px] text-slate-500">Сумма, ₸</span>
                <input
                  type="number"
                  inputMode="numeric"
                  className="ha-m-num"
                  value={fine.amount ?? 0}
                  onChange={(event) => day.updateFine(index, 'amount', event.target.value)}
                />
              </label>
            ) : null}
            <input
              type="text"
              className="ha-m-text mt-2"
              value={fine.comment ?? ''}
              placeholder="Комментарий"
              onChange={(event) => day.updateFine(index, 'comment', event.target.value)}
              aria-label={`Комментарий к штрафу ${index + 1}`}
            />
            {hoursPhoneFineHint(fine) ? <p className="mt-1 text-[13px] leading-snug text-slate-500">{hoursPhoneFineHint(fine)}</p> : null}
          </div>
        ))}
      </AuctionPhoneGroup>

      <AuctionPhoneGroup
        label="Бонусы"
        right={<GroupAddButton onClick={day.addBonus} />}
      >
        {(model.bonuses || []).length ? null : <HoursPhoneEmptyRow text="Бонусов нет" />}
        {(model.bonuses || []).map((bonus, index) => {
          const type = String(bonus?.type || '').trim();
          const isTraining = type === 'Обучение';
          const amount = day.bonusAmount(type, isTraining ? 1 : Number(bonus?.quantity ?? 1), bonus?.amount, Number(bonus?.training_hours ?? 0));
          return (
            <div key={bonus.id ?? index} className="sa-m-row px-4 py-2.5">
              <div className="flex items-center gap-2 pb-1" style={NO_WRAP}>
                <span className="min-w-0 flex-1 truncate text-[16px] font-semibold text-slate-900">{formatHoursPhoneMoney(amount)}</span>
                <button type="button" onClick={() => day.removeBonus(index)} className="ha-m-icon text-rose-600" aria-label={`Удалить бонус ${index + 1}`}>
                  <Trash2 size={17} aria-hidden="true" />
                </button>
              </div>
              <select
                className="ha-m-select ha-m-select--wide"
                value={type}
                onChange={(event) => day.updateBonus(index, 'type', event.target.value)}
                aria-label={`Тип бонуса ${index + 1}`}
              >
                <option value="">Тип не выбран</option>
                {HOURS_PHONE_BONUS_TYPES.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
              {hoursPhoneBonusHasQuantity(type) ? (
                <label className="mt-2 flex items-center gap-3" style={NO_WRAP}>
                  <span className="min-w-0 flex-1 text-[15px] text-slate-500">{hoursPhoneBonusQuantityLabel(type)}</span>
                  <input
                    type="number"
                    inputMode="decimal"
                    min={isTraining ? '0' : '1'}
                    step={isTraining ? '0.1' : '1'}
                    className="ha-m-num"
                    value={isTraining ? (bonus.training_hours ?? 0) : (bonus.quantity ?? 1)}
                    onChange={(event) => day.updateBonus(index, isTraining ? 'training_hours' : 'quantity', event.target.value)}
                  />
                </label>
              ) : null}
              {type === 'Приведи друга' ? (
                <input
                  type="text"
                  className="ha-m-text mt-2"
                  value={bonus.friend_names ?? ''}
                  placeholder="Имена друзей"
                  onChange={(event) => day.updateBonus(index, 'friend_names', event.target.value)}
                  aria-label={`Имена друзей, бонус ${index + 1}`}
                />
              ) : null}
              {type === 'Съемки' ? (
                <input
                  type="text"
                  className="ha-m-text mt-2"
                  value={bonus.video_links ?? ''}
                  placeholder="Ссылки на видео"
                  onChange={(event) => day.updateBonus(index, 'video_links', event.target.value)}
                  aria-label={`Ссылки на видео, бонус ${index + 1}`}
                />
              ) : null}
              <input
                type="text"
                className="ha-m-text mt-2"
                value={bonus.comment ?? ''}
                placeholder="Комментарий"
                onChange={(event) => day.updateBonus(index, 'comment', event.target.value)}
                aria-label={`Комментарий к бонусу ${index + 1}`}
              />
            </div>
          );
        })}
      </AuctionPhoneGroup>

      <button type="button" onClick={day.clearFields} className={AUCTION_PHONE_BUTTON.gray}>
        Очистить показатели дня
      </button>
    </div>
  );
};

/* Экран офлайн-активности: на компьютере это окно поверх окна дня, здесь —
   третий экран. Своё, а не настольное: там сетка из двух колонок и поля с
   кеглем 12 px, ниже порога, за которым iOS приближает страницу при фокусе. */
const HoursPhoneOfflineScreen = ({ state, error, onChange, onSave, onClose, busy }) => (
  <IosModal
    open={Boolean(state?.open)}
    onClose={busy ? () => {} : onClose}
    title={state?.activity?.id ? 'Офлайн активность' : 'Новая офлайн активность'}
    subtitle={state?.date ? formatHoursPhoneDay(String(state.date).slice(0, 7), String(state.date).slice(8, 10)) : ''}
    footer={(
      <button type="button" onClick={onSave} disabled={busy} className={AUCTION_PHONE_BUTTON.blue}>
        {busy ? <Loader2 size={18} className="animate-spin" aria-hidden="true" /> : null}
        {busy ? 'Сохраняем…' : 'Сохранить'}
      </button>
    )}
  >
    <div className="flex flex-col gap-5">
      <AuctionPhoneGroup>
        <label className="sa-m-row flex w-full items-center gap-3 px-4 py-2" style={NO_WRAP}>
          <span className="min-w-0 flex-1 text-[16px] text-slate-900">Дата</span>
          <input type="date" className="ha-m-time" value={state?.date || ''} onChange={(event) => onChange('date', event.target.value)} />
        </label>
        <label className="sa-m-row flex w-full items-center gap-3 px-4 py-2" style={NO_WRAP}>
          <span className="min-w-0 flex-1 text-[16px] text-slate-900">Начало</span>
          <input type="time" className="ha-m-time" value={state?.start_time || ''} onChange={(event) => onChange('start_time', event.target.value)} />
        </label>
        <label className="sa-m-row flex w-full items-center gap-3 px-4 py-2" style={NO_WRAP}>
          <span className="min-w-0 flex-1 text-[16px] text-slate-900">Окончание</span>
          <input type="time" className="ha-m-time" value={state?.end_time || ''} onChange={(event) => onChange('end_time', event.target.value)} />
        </label>
      </AuctionPhoneGroup>

      <AuctionPhoneGroup label="Комментарий">
        <div className="px-4 py-3">
          <textarea
            className="block w-full resize-none bg-transparent text-[16px] leading-snug text-slate-900 placeholder-slate-400 outline-none"
            rows={4}
            value={state?.comment || ''}
            placeholder="Например: обзвон, сверка, ручная обработка"
            onChange={(event) => onChange('comment', event.target.value)}
          />
        </div>
      </AuctionPhoneGroup>

      {error ? <p className="-mt-3 px-1 text-[13px] leading-snug text-rose-600">{error}</p> : null}
    </div>
  </IosModal>
);

export const HoursAccountingPhone = ({
  month,
  onMonthChange,
  isLoading = false,
  emptyText = '',
  scope,
  metrics,
  data,
  actions,
  day,
  activities,
  offlineModal,
  trainingModal = null,
}) => {
  const [picker, setPicker] = useState(null);
  const [openOperatorId, setOpenOperatorId] = useState(null);

  const monthOptions = useMemo(() => hoursPhoneMonthOptions(month), [month]);
  const selectedTabLabel = (metrics.tabs.find((tab) => tab.key === metrics.selectedTab) || {}).label || '';
  const groupName = (scope.groups.find((group) => String(group.id) === String(scope.selectedGroupId)) || {}).name || '';
  const scopeLabel = scope.reportScope === 'all' ? 'Общий отчёт' : (groupName || 'Отчёт по СВ');

  /* Итоги операторов считаем один раз на рендер: одни и те же числа стоят в
     строке списка, в шапке экрана оператора и в подвале раздела. */
  const rows = useMemo(() => {
    const out = [];
    for (const direction of Object.keys(data.groupedByDirection || {})) {
      for (const op of data.groupedByDirection[direction] || []) {
        const totals = buildHoursPhoneOperatorTotals({
          op,
          month,
          trainingsByDay: data.trainingsMap[op.operator_id],
          technicalByDay: data.technicalIssuesMap[op.operator_id],
          offlineByDay: data.offlineActivitiesMap[op.operator_id],
          successesByDay: data.tezSuccessMap?.[String(op.operator_id)],
          isChatModel: data.isChatModel,
          helpers: data.helpers,
        });
        out.push({ direction, op, totals });
      }
    }
    return out;
  }, [data, month]);

  const footer = useMemo(() => ({
    ...data.footer,
    sumBreakTime: rows.reduce((sum, row) => sum + row.totals.breakTime, 0),
  }), [data.footer, rows]);

  const sectionTotals = useMemo(() => buildHoursPhoneSectionTotals({
    tab: metrics.selectedTab,
    footer,
    isChatModel: data.isChatModel,
    isTezOpContext: data.isTezOpContext,
  }), [metrics.selectedTab, footer, data.isChatModel, data.isTezOpContext]);

  const openRow = rows.find((row) => String(row.op.operator_id) === String(openOperatorId)) || null;
  /* Экран уезжает ещё треть секунды после закрытия — пусть уезжает с тем же
     оператором, а не пустым. */
  const shownRow = useLastPresent(openRow);

  const calendar = useMemo(() => {
    if (!shownRow) return null;
    return buildHoursPhoneMonth({
      op: shownRow.op,
      month,
      days: data.days,
      tab: metrics.selectedTab,
      monthRelation: data.monthRelation,
      todayDay: data.todayDay,
      trainingsByDay: data.trainingsMap[shownRow.op.operator_id],
      technicalByDay: data.technicalIssuesMap[shownRow.op.operator_id],
      offlineByDay: data.offlineActivitiesMap[shownRow.op.operator_id],
      successesByDay: data.tezSuccessMap?.[String(shownRow.op.operator_id)],
      selectedGroupId: scope.selectedGroupId,
      scales: data.scales,
      helpers: data.helpers,
    });
  }, [shownRow, month, data, metrics.selectedTab, scope.selectedGroupId]);

  const operatorValue = (row) => hoursPhoneOperatorValue({
    tab: metrics.selectedTab,
    totals: row.totals,
    isChatModel: data.isChatModel,
    isTezOpContext: data.isTezOpContext,
    chatAverage: data.chatAverageFor(row.op),
    responseAverage: data.responseAverageFor(row.op),
  });

  const closePicker = () => setPicker(null);
  const dayCell = day.selectedCell;

  /* Корень раздела: sa-m-root включает общий слой списков телефона (заголовок,
     подписи групп, разделители строк, колесо месяцев), ha-m-root — свой. */
  return (
    <div className="sa-m-root ha-m-root min-h-screen bg-slate-100" style={{ fontFamily: APPLE_FONT }}>
      <AuctionPhoneMonthHeader
        title="Учет часов"
        subtitle={scopeLabel}
        month={month}
        label={formatHoursPhoneMonth(month)}
        onMonthChange={onMonthChange}
        onPrev={() => { const prev = shiftHoursPhoneMonth(month, -1); if (prev) onMonthChange(prev); }}
        onNext={() => { const next = shiftHoursPhoneMonth(month, 1); if (next) onMonthChange(next); }}
        prevDisabled={!shiftHoursPhoneMonth(month, -1)}
        nextDisabled={!shiftHoursPhoneMonth(month, 1)}
        disabled={isLoading}
      >
        {monthOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </AuctionPhoneMonthHeader>

      {/* Показатель — полосой «чипов»: их до четырнадцати, и выпадающими
          меню, как на компьютере, до нужного добираться в два нажатия. */}
      <AuctionPhoneChips
        ariaLabel="Показатель"
        items={metrics.tabs.map((tab) => ({
          id: tab.key,
          label: tab.label,
          caption: tab.unit || null,
          active: tab.key === metrics.selectedTab,
        }))}
        onSelect={(item) => metrics.onSelectTab(item.id)}
      />

      <IosSegmented
        ariaLabel="Сотрудники"
        stretch
        size="lg"
        value={scope.operatorsTab}
        onChange={scope.onOperatorsTab}
        options={[
          { value: 'active', label: 'Активные', count: scope.activeCount },
          { value: 'fired', label: 'Уволенные', count: scope.firedCount },
        ]}
      />

      <HoursPhoneTotals totals={sectionTotals} rowsCount={rows.length} />

      <AuctionPhoneGroup label="Отбор">
        <HoursPhoneFilterRow
          title="Отчёт"
          value={scope.reportScope === 'all' ? 'Общий' : 'По СВ'}
          onClick={() => setPicker('scope')}
        />
        {scope.peopleKinds ? (
          <HoursPhoneFilterRow
            title="Сотрудники"
            value={(scope.peopleKinds.find((item) => item.value === scope.peopleKind) || scope.peopleKinds[0]).label}
            onClick={() => setPicker('people')}
          />
        ) : null}
        {scope.groups.length ? (
          <HoursPhoneFilterRow
            title="Группа"
            value={groupName || 'Без группы'}
            disabled={scope.reportScope === 'all'}
            onClick={() => setPicker('group')}
          />
        ) : null}
        <HoursPhoneFilterRow
          title="Направления"
          value={hoursPhoneDirectionsLabel(scope.selectedDirections)}
          onClick={() => setPicker('directions')}
        />
      </AuctionPhoneGroup>

      {isLoading ? (
        <div className="flex flex-col gap-4" aria-hidden="true">
          <div className={`${iosCard} h-[180px] animate-pulse`} />
          <div className={`${iosCard} h-[280px] animate-pulse`} />
        </div>
      ) : rows.length === 0 ? (
        <div className={`${iosCard} px-4 py-8 text-center text-[16px] text-slate-500`}>{emptyText || 'Операторы не найдены'}</div>
      ) : (
        Object.keys(data.groupedByDirection).map((direction) => (
          <AuctionPhoneGroup
            key={direction}
            label={direction}
            right={<span className="shrink-0 text-[13px] tabular-nums text-slate-500">{(data.groupedByDirection[direction] || []).length}</span>}
          >
            {rows.filter((row) => row.direction === direction).map((row) => {
              const value = operatorValue(row);
              return (
                <AuctionPhoneRow
                  key={row.op.operator_id}
                  title={row.op.name}
                  subtitle={`Ставка ${row.op.rate ?? '—'} · норма ${formatHoursPhoneHours(row.totals.norm)}`}
                  onClick={() => setOpenOperatorId(row.op.operator_id)}
                  chevron
                  trailing={(
                    <span className="shrink-0 text-right">
                      <span className="block text-[16px] font-semibold tabular-nums text-slate-900">{value.text}</span>
                      {data.isFired(row.op) ? <span className="block text-[12px] text-rose-600">Уволен</span> : null}
                    </span>
                  )}
                />
              );
            })}
          </AuctionPhoneGroup>
        ))
      )}

      <AuctionPhoneGroup label="Действия">
        <button type="button" onClick={actions.onRefresh} className="sa-m-row flex w-full items-center gap-3 px-4 py-3 text-left" style={NO_WRAP}>
          <span className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-lg bg-blue-500 text-white">
            <RefreshCw size={17} aria-hidden="true" />
          </span>
          <span className="min-w-0 flex-1 truncate text-[16px] text-slate-900">Обновить данные</span>
        </button>
        <button
          type="button"
          onClick={actions.onDownloadReport}
          disabled={actions.isDownloadingReport}
          className="sa-m-row flex w-full items-center gap-3 px-4 py-3 text-left disabled:opacity-50"
          style={NO_WRAP}
        >
          <span className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-lg bg-emerald-500 text-white">
            {actions.isDownloadingReport ? <Loader2 size={17} className="animate-spin" aria-hidden="true" /> : <FileSpreadsheet size={17} aria-hidden="true" />}
          </span>
          <span className="min-w-0 flex-1 truncate text-[16px] text-slate-900">
            {actions.isDownloadingReport ? 'Формируем отчёт…' : 'Сформировать отчёт'}
          </span>
        </button>
      </AuctionPhoneGroup>

      <HoursPhonePicker
        open={picker === 'scope'}
        onClose={closePicker}
        title="Тип отчёта"
        options={[{ value: 'by_sv', label: 'По СВ' }, { value: 'all', label: 'Общий' }]}
        isSelected={(value) => value === scope.reportScope}
        onPick={(value) => { scope.onReportScope(value); closePicker(); }}
        hint="Общий отчёт собирает всех операторов сразу и не спрашивает группу."
      />

      {scope.peopleKinds ? (
        <HoursPhonePicker
          open={picker === 'people'}
          onClose={closePicker}
          title="Сотрудники"
          options={scope.peopleKinds}
          isSelected={(value) => value === scope.peopleKind}
          onPick={(value) => { scope.onPeopleKind(value); closePicker(); }}
          hint="Часы супервайзеров считаются по отметкам Clockster за вычетом перерыва."
        />
      ) : null}

      <HoursPhonePicker
        open={picker === 'group'}
        onClose={closePicker}
        title="Группа"
        options={[
          { value: '', label: 'Без группы (по СВ)' },
          ...scope.groups.map((group) => ({ value: String(group.id), label: group.name })),
        ]}
        isSelected={(value) => String(value) === String(scope.selectedGroupId || '')}
        onPick={(value) => { scope.onSelectGroup(value); closePicker(); }}
      />

      <HoursPhonePicker
        open={picker === 'directions'}
        onClose={closePicker}
        title="Направления"
        subtitle="Можно выбрать несколько"
        options={[
          { value: 'all', label: 'Все направления' },
          ...scope.directionOptions.map((direction) => ({ value: direction, label: direction })),
        ]}
        isSelected={(value) => scope.selectedDirections.includes(value)}
        onPick={(value) => {
          if (value === 'all') { scope.onSelectDirections(['all']); closePicker(); return; }
          scope.onToggleDirection(value);
        }}
      />

      {/* Экран оператора: месяц одного человека — крупное число показателя,
          остальные итоги строками, норма правится здесь же, дни ячейками. */}
      <IosModal
        open={Boolean(openRow)}
        onClose={() => setOpenOperatorId(null)}
        title={shownRow?.op?.name || ''}
        subtitle={shownRow ? `${shownRow.direction} · ${formatHoursPhoneMonth(month)}` : ''}
      >
        {shownRow ? (
          <div className="flex flex-col gap-5">
            <section className={`${iosCard} px-4 pb-3 pt-3.5`}>
              <p className="text-[13px] text-slate-500">{operatorValue(shownRow).caption}</p>
              <p className="mt-0.5 text-[32px] font-bold leading-[38px] tracking-tight tabular-nums text-slate-900">
                {operatorValue(shownRow).text}
              </p>
              {data.isFired(shownRow.op) ? (
                <p className="mt-1.5">
                  <IosBadge tone="red">
                    {data.dismissalDate(shownRow.op) ? `Уволен(а) с ${data.dismissalDate(shownRow.op)}` : 'Уволен(а)'}
                  </IosBadge>
                </p>
              ) : null}
              {(shownRow.op.group_segments || []).length > 1 ? (
                <p className="mt-1.5 flex items-center gap-2 text-[13px] leading-snug text-amber-700" style={NO_WRAP}>
                  <TriangleAlert size={14} className="shrink-0" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    Переведён: {shownRow.op.group_segments.map((segment) => `${segment.group_name}, дни ${segment.start_day}–${segment.end_day}`).join('; ')}
                  </span>
                </p>
              ) : null}
            </section>

            <AuctionPhoneGroup label="Итоги месяца">
              {hoursPhoneOperatorRows({
                tab: metrics.selectedTab,
                totals: shownRow.totals,
                plan: data.planFor(shownRow.op, shownRow.totals),
                isTezOpContext: data.isTezOpContext,
              }).map((row) => (
                <div key={row.key} className="sa-m-row flex items-center gap-3 px-4 py-3" style={NO_WRAP}>
                  <span className="min-w-0 flex-1 truncate text-[16px] text-slate-900">{row.label}</span>
                  <span className={`shrink-0 text-[16px] tabular-nums ${row.valueClassName || 'text-slate-500'}`}>{row.value}</span>
                </div>
              ))}
              {actions.isNormLocked?.(shownRow.op) ? (
                <div className="sa-m-row flex items-center gap-3 px-4 py-3" style={NO_WRAP}>
                  <span className="min-w-0 flex-1 truncate text-[16px] text-slate-900">Норма часов</span>
                  <span className="shrink-0 text-[16px] tabular-nums text-slate-500">{formatHoursPhoneHours(shownRow.op.norm_hours ?? 0)} ч</span>
                </div>
              ) : (
                <HoursPhoneNumberRow
                  label="Норма часов"
                  unit="ч"
                  step="0.1"
                  value={shownRow.op.norm_hours ?? 0}
                  onChange={(value) => actions.onNormChange(shownRow.op.operator_id, value)}
                />
              )}
            </AuctionPhoneGroup>

            {calendar ? (
              <HoursPhoneCalendar
                model={calendar}
                month={month}
                onOpenDay={(cell) => day.onOpenDay(shownRow.op, cell.day)}
                onOpenForeign={(cell) => day.onOpenForeignDay(shownRow.op, cell.day)}
              />
            ) : null}
          </div>
        ) : null}
      </IosModal>

      {/* Экран дня — поверх экрана оператора: закрывается шевроном и системным
          «назад», под ним остаётся месяц того же человека. */}
      <IosModal
        open={Boolean(dayCell && day.cellModel)}
        onClose={day.onClose}
        title={dayCell ? formatHoursPhoneDay(month, dayCell.day) : ''}
        subtitle={day.cellModel?.name || ''}
        footer={(
          <button type="button" onClick={day.onSave} disabled={day.isSaving} className={AUCTION_PHONE_BUTTON.blue}>
            {day.isSaving ? <Loader2 size={18} className="animate-spin" aria-hidden="true" /> : null}
            {day.isSaving ? 'Сохраняем…' : 'Сохранить'}
          </button>
        )}
      >
        {dayCell && day.cellModel ? (
          <HoursPhoneDayScreen
            day={day}
            activities={activities}
            isChatModel={data.isChatModel}
            isTezOpContext={data.isTezOpContext}
            successes={data.tezSuccessMap?.[String(day.cellModel.operator_id)]?.[String(day.cellModel.day)] || 0}
          />
        ) : null}
      </IosModal>

      <HoursPhoneOfflineScreen
        state={offlineModal.state}
        error={offlineModal.error}
        onChange={offlineModal.onChange}
        onSave={offlineModal.onSave}
        onClose={offlineModal.onClose}
        busy={offlineModal.busy}
      />

      {/* Окно тренинга — чужое (window.TrainingModal в App.jsx, общее с
          компьютером). Свой слой у него z-50, а экран дня стоит на 120 и
          накрывал его целиком: окно открывалось, но его не было видно.
          Обёртка display:contents не добавляет коробки в колонку раздела. */}
      {trainingModal ? <div className="ha-m-training">{trainingModal}</div> : null}
    </div>
  );
};

export default HoursAccountingPhone;
