import React from 'react';
import TaskDateTimeField from './TaskDateTimeField';
import { dayAfter, deadlinePresetActive, deadlinePresetValue } from './taskDateTime';

/* Быстрые сроки: ставят ДЕНЬ, время остаётся выбранным (без него — 18:00). */
export const DEADLINE_PRESETS = [
  { label: 'Сегодня', days: 0 },
  { label: 'Завтра', days: 1 },
  { label: '3 дня', days: 3 },
  { label: 'Неделя', days: 7 },
];

/**
 * Дедлайн задачи — днём в календаре и временем, а не «через N дней, часов и
 * минут»: считать дни до нужной пятницы человеку приходилось самому. Отдаёт ту
 * же строку «ГГГГ-ММ-ДДTЧЧ:ММ», что TaskDateTimeField ('' — без срока).
 *
 * Быстрые кнопки — над полем, подсвечена та, на чей день стоит срок. Прошедшие
 * дни в календаре погашены; время сегодняшнего дня, которое уже прошло, ловит
 * проверка формы при сохранении (красным у поля), а не молчаливая подмена.
 *
 * `fromDate` («ГГГГ-ММ-ДД») — первый доступный день вместо сегодняшнего: у
 * повторяющейся задачи это день начала ближайшего повтора. Быстрые кнопки
 * считают от сегодня, поэтому там их нет (`presets={false}`).
 */
const TaskDeadlineField = ({
  value = '', onChange, disabled = false, ariaLabel = 'Дедлайн', className = '', presets = true, fromDate = '',
}) => {
  // День берём при каждой отрисовке: форма, открытая до полуночи, не должна
  // подсвечивать «Сегодня» на вчерашнем дне.
  const now = new Date();
  const today = dayAfter(0, now);
  return (
    <div className={`tv-due-field ${className}`}>
      {presets && (
        <div className="tv-composer-presets" role="group" aria-label="Быстрый срок">
          {DEADLINE_PRESETS.map((preset) => {
            const active = deadlinePresetActive(preset.days, value, now);
            return (
              <button
                key={preset.label}
                type="button"
                className={active ? 'is-active' : ''}
                aria-pressed={active}
                disabled={disabled}
                onClick={() => onChange?.(deadlinePresetValue(preset.days, value, new Date()))}
              >
                {preset.label}
              </button>
            );
          })}
        </div>
      )}
      <TaskDateTimeField
        size="form"
        value={value}
        minDate={fromDate && fromDate > today ? fromDate : today}
        disabled={disabled}
        ariaLabel={ariaLabel}
        onChange={onChange}
      />
    </div>
  );
};

export default TaskDeadlineField;
