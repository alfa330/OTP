import React from 'react';
import { PRIORITY_SCORES, priorityScoreTitle } from './backlogOrder';

/*
 * Цвет балла: от красного (1 — важнее всего) к серому (5 — можно позже), как
 * цветные метки в iOS. Цвет здесь несёт смысл — по нему очередь читается с
 * одного взгляда. Классы записаны целиком: сборка Tailwind и тёмный слой видят
 * только литералы.
 */
export const PRIORITY_SCORE_TONES = {
  1: {
    dot: 'bg-rose-500',
    idle: 'text-rose-500 hover:bg-rose-50',
    active: 'bg-rose-500 text-white shadow-[0_1px_3px_rgba(225,29,72,0.35)]',
    chip: 'bg-rose-50 text-rose-700 ring-rose-100',
  },
  2: {
    dot: 'bg-orange-500',
    idle: 'text-orange-500 hover:bg-orange-50',
    active: 'bg-orange-500 text-white shadow-[0_1px_3px_rgba(234,88,12,0.35)]',
    chip: 'bg-orange-50 text-orange-700 ring-orange-100',
  },
  3: {
    dot: 'bg-amber-400',
    idle: 'text-amber-500 hover:bg-amber-50',
    active: 'bg-amber-400 text-white shadow-[0_1px_3px_rgba(217,119,6,0.35)]',
    chip: 'bg-amber-50 text-amber-700 ring-amber-100',
  },
  4: {
    dot: 'bg-sky-500',
    idle: 'text-sky-500 hover:bg-sky-50',
    active: 'bg-sky-500 text-white shadow-[0_1px_3px_rgba(2,132,199,0.35)]',
    chip: 'bg-sky-50 text-sky-700 ring-sky-100',
  },
  5: {
    dot: 'bg-slate-400',
    idle: 'text-slate-400 hover:bg-slate-200/60',
    active: 'bg-slate-400 text-white shadow-[0_1px_3px_rgba(71,85,105,0.30)]',
    chip: 'bg-slate-100 text-slate-600 ring-slate-200/70',
  },
};

/** Цветная точка балла — у подписи группы бэклога и в карточке задачи. */
export const PriorityScoreDot = ({ score, className = '' }) => (
  PRIORITY_SCORE_TONES[score]
    ? <span aria-hidden="true" className={`inline-block h-2 w-2 shrink-0 rounded-full ${PRIORITY_SCORE_TONES[score].dot} ${className}`} />
    : null
);

/**
 * Балл на карточке: только цифра 1–5 в цветной плашке, без слова «Балл» и без
 * нажатия (владелец 06.11.2026). Плашка — скруглённый прямоугольник, как соседние
 * метки карточки (срок, оценка), а не кружок. Стоит в правом нижнем углу; поменять
 * или снять балл — в карточке задачи. Смысл цифры — в подсказке при наведении,
 * диктору — через role="img": у простого span имя из aria-label не читается.
 */
export const PriorityScoreBadge = ({ score, className = '' }) => (
  PRIORITY_SCORE_TONES[score] ? (
    <span
      role="img"
      title={priorityScoreTitle(score)}
      aria-label={priorityScoreTitle(score)}
      className={`tb-score-badge inline-flex h-5 min-w-[22px] shrink-0 items-center justify-center rounded-md px-1.5 text-[11px] font-semibold tabular-nums ring-1 ${PRIORITY_SCORE_TONES[score].chip} ${className}`}
    >
      {score}
    </span>
  ) : null
);

/**
 * Балл приоритета 1–5: сегменты на сером треке, как переключатели iOS, но у
 * каждой цифры свой цвет, а выбранная залита им целиком. Повторное нажатие на
 * выбранный балл снимает его: «не оценена» — это пусто, а не «5». Клик не уходит
 * в карточку под контролом — там он открыл бы задачу. titleSuffix дописывается в
 * подсказку невыбранных цифр (что после балла задача уйдёт в бэклог), clearTitleSuffix —
 * в подсказку выбранной (что без балла задача вернётся в «Новую»). disabled — пока
 * прошлое нажатие не дошло до сервера: два быстрых нажатия иначе ушли бы двумя
 * запросами, и победил бы тот, что пришёл первым, а не последний.
 */
const PriorityScoreControl = ({
  value = null, onChange, className = '', titleSuffix = '', clearTitleSuffix = '', disabled = false,
}) => (
  <span
    role="radiogroup"
    aria-label="Балл приоритета: 1 — важнее всего, 5 — можно позже"
    aria-disabled={disabled || undefined}
    className={`tv-score-control inline-flex items-center gap-[2px] rounded-[9px] bg-slate-100 p-[2px] transition-opacity ${disabled ? 'opacity-60' : ''} ${className}`}
    onClick={(event) => event.stopPropagation()}
  >
    {PRIORITY_SCORES.map((score) => {
      const active = score === value;
      const tone = PRIORITY_SCORE_TONES[score];
      return (
        <button
          key={score}
          type="button"
          role="radio"
          aria-checked={active}
          disabled={disabled}
          title={active
            ? `${priorityScoreTitle(score)} — нажмите ещё раз, чтобы снять${clearTitleSuffix}`
            : `${priorityScoreTitle(score)}${titleSuffix}`}
          onClick={() => { if (!disabled) onChange?.(active ? null : score); }}
          className={`grid h-[22px] w-[24px] place-items-center rounded-[7px] text-[11.5px] font-semibold tabular-nums transition-all duration-150 active:scale-[0.92] ${
            active ? tone.active : tone.idle
          }`}
        >
          {score}
        </button>
      );
    })}
  </span>
);

export default PriorityScoreControl;
