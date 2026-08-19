import React from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard } from '../ui/ios';
import { WALLBOARD_TONE_TEXT } from './szovWallboardShared';

/*
 * Кирпичи табло СЗоВ: секция, сетка и две плитки. Общие для обоих направлений («Основа» и
 * «Чат») — экран один, и разъезжаться в размерах цифр, отступах и цветах он не должен.
 * Показатель сюда приходит уже готовым (подпись, значение, тон): что именно считать, решают
 * каталог показателей линии и сборщик снимка чатов, а здесь только раскладка.
 */

/*
 * Фон цветных плиток. Два вида цвета, и путать их нельзя: оценочный (good/warn/bad — «в норме
 * или нет») и опознавательный (info/violet — идентичность статуса). Текст берётся из общего
 * WALLBOARD_TONE_TEXT, чтобы цифра и её фон не разошлись.
 */
export const KEY_PALETTE = {
    good: { bg: 'bg-emerald-100/70', text: 'text-emerald-700', hint: 'text-emerald-600/80' },
    warn: { bg: 'bg-amber-100/70', text: 'text-amber-700', hint: 'text-amber-600/80' },
    bad: { bg: 'bg-rose-100/70', text: 'text-rose-700', hint: 'text-rose-600/80' },
    info: { bg: 'bg-blue-100/70', text: 'text-blue-700', hint: 'text-blue-600/80' },
    violet: { bg: 'bg-violet-100/70', text: 'text-violet-700', hint: 'text-violet-600/80' },
    neutral: { bg: 'bg-slate-100', text: 'text-slate-700', hint: 'text-slate-500' },
};

// Два размера цифр: ключевые показатели читаются через зал, показатели дня и операторов — рядом.
const VALUE_SIZE = { key: [3, 4.7, 5], stat: [2.375, 3.5, 3.75] };

export const valueFontSize = (size, scale) => {
    const [min, mid, max] = VALUE_SIZE[size] || VALUE_SIZE.stat;
    return `clamp(${(min * scale).toFixed(3)}rem, ${(mid * scale).toFixed(2)}vw, ${(max * scale).toFixed(3)}rem)`;
};

/*
 * Сегментированный переключатель в стиле остальных разделов. Живёт здесь, а не в разделе:
 * им переключаются и направления табло в шапке, и режим подсказки графика по чатам.
 */
export const SegmentedSwitch = ({ value, options, disabled, onChange, compact = false }) => (
    <div className={`flex rounded-xl bg-slate-100 ${compact ? 'p-0.5' : 'p-1'}`}>
        {options.map((option) => (
            <button
                key={option.key}
                type="button"
                disabled={disabled}
                onClick={() => { if (value !== option.key) onChange(option.key); }}
                title={option.hint}
                className={`rounded-[9px] font-semibold transition-all disabled:opacity-50 ${
                    compact ? 'px-2.5 py-1 text-[12px]' : 'px-3 py-1.5 text-[12.5px]'} ${
                    value === option.key
                        ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                        : 'text-slate-500 hover:text-slate-700'}`}
            >
                {option.label}
            </button>
        ))}
    </div>
);

/** Секция с подписью и иконкой; внутри — сетка плиток с отступами. */
export const Section = ({ icon, title, right = null, children }) => (
    <div className={`${iosCard} p-5`}>
        <div className="mb-4 flex items-center justify-between gap-3 px-0.5">
            <div className="flex items-center gap-2.5 text-[15px] font-semibold text-slate-500">
                <FaIcon className={`fas ${icon}`}></FaIcon>
                <span>{title}</span>
            </div>
            {right}
        </div>
        {children}
    </div>
);

export const Grid = ({ children }) => (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">{children}</div>
);

/** Ключевая плитка: цветной фон, самая крупная цифра. */
export const KeyTile = ({ label, value, hint = null, tone = 'neutral', scale = 1 }) => {
    const palette = KEY_PALETTE[tone] || KEY_PALETTE.neutral;
    return (
        <div className={`flex flex-col items-center gap-2 rounded-2xl px-4 py-6 text-center ${palette.bg}`}>
            <div className={`text-[15px] font-semibold ${palette.text}`}>{label}</div>
            <div
                className={`font-semibold tabular-nums leading-none ${palette.text}`}
                style={{ fontSize: valueFontSize('key', scale) }}
            >
                {value}
            </div>
            {hint ? <div className={`text-[13px] leading-tight ${palette.hint}`}>{hint}</div> : null}
        </div>
    );
};

/*
 * Плитка дня: белая, цвет достаётся только цифре и только когда он что-то значит.
 * secondary — второе число пары («Принято / входящих»), unit — единица измерения:
 * «11,6 мин» целиком крупным шрифтом не влезает и переносится на вторую строку.
 */
export const StatTile = ({ label, value, secondary = null, unit = null, tone = 'neutral', scale = 1 }) => (
    <div className="flex flex-col items-center gap-2.5 rounded-2xl border border-slate-200/80 px-4 py-5 text-center">
        <div className="text-[14px] font-medium text-slate-500">{label}</div>
        <div
            className={`whitespace-nowrap font-semibold tabular-nums leading-none ${
                WALLBOARD_TONE_TEXT[tone] || WALLBOARD_TONE_TEXT.neutral}`}
            style={{ fontSize: valueFontSize('stat', scale) }}
        >
            {value}
            {secondary === null ? null : (
                <span className="font-normal text-slate-400" style={{ fontSize: '0.6em' }}>/{secondary}</span>
            )}
            {unit ? <span className="font-normal text-slate-400" style={{ fontSize: '0.45em' }}> {unit}</span> : null}
        </div>
    </div>
);
