import React from 'react';
import IosDatePicker from '../ui/DatePicker';
import { IosTimePicker } from '../ui/TimePicker';
import { DEFAULT_DUE_TIME, joinLocalDateTime, splitLocalDateTime } from './taskDateTime';

/* Размеры под место: «sm» — тесный поповер «Оценка и срок» на доске, «md» —
   поля раздела (детали заметки). Цвета и кольцо фокуса — как у полей сайта. */
const SIZES = {
  sm: {
    date: 'flex w-full items-center gap-1.5 rounded-xl bg-slate-100 px-2.5 py-1.5 text-[12.5px] tabular-nums text-slate-900 '
      + 'border-0 transition hover:bg-slate-200/70 focus:outline-none focus:ring-2 focus:ring-blue-500/70 '
      + 'disabled:cursor-not-allowed disabled:opacity-40 [&>span]:flex-1 [&>span]:text-left',
    time: 'w-[84px] rounded-xl border-0 bg-slate-100 px-5 py-1.5 text-center text-[12.5px] tabular-nums text-slate-900 '
      + 'placeholder-slate-400 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70 '
      + 'disabled:cursor-not-allowed disabled:opacity-40',
  },
  md: {
    date: 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3 py-2 text-[13px] tabular-nums text-slate-900 '
      + 'border-0 transition hover:bg-slate-200/70 focus:outline-none focus:ring-2 focus:ring-blue-500/70 '
      + 'disabled:cursor-not-allowed disabled:opacity-40 [&>span]:flex-1 [&>span]:text-left',
    time: 'w-[96px] rounded-xl border-0 bg-slate-100 px-6 py-2 text-center text-[13px] tabular-nums text-slate-900 '
      + 'placeholder-slate-400 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70 '
      + 'disabled:cursor-not-allowed disabled:opacity-40',
  },
};

/**
 * Срок — дата и время пикерами сайта вместо системного datetime-local: его
 * раскрытый календарь рисует браузер, и рядом с карточками раздела это деталь из
 * другой программы. Принимает и отдаёт ту же строку «ГГГГ-ММ-ДДTЧЧ:ММ», что и
 * системное поле, поэтому вокруг ничего не меняется. Время без даты не задать,
 * а дата без времени встаёт на 18:00; «Очистить» в календаре снимает срок целиком.
 */
const TaskDateTimeField = ({ value = '', onChange, disabled = false, size = 'md', ariaLabel = 'Срок', className = '' }) => {
  const { date, time } = splitLocalDateTime(value);
  const styles = SIZES[size] || SIZES.md;
  return (
    <div className={`grid grid-cols-[minmax(0,1fr)_auto] gap-1.5 ${className}`}>
      <IosDatePicker
        value={date}
        allowEmpty
        placeholder="Без срока"
        disabled={disabled}
        className="min-w-0"
        triggerClassName={styles.date}
        ariaLabel={`${ariaLabel}: день`}
        onChange={(nextDate) => onChange?.(joinLocalDateTime(nextDate, time))}
      />
      <IosTimePicker
        value={date ? (time || DEFAULT_DUE_TIME) : ''}
        allowEmpty={false}
        defaultTime={DEFAULT_DUE_TIME}
        disabled={disabled || !date}
        inputClassName={styles.time}
        ariaLabel={`${ariaLabel}: время`}
        onChange={(nextTime) => onChange?.(joinLocalDateTime(date, nextTime))}
      />
    </div>
  );
};

export default TaskDateTimeField;
