import React from 'react';

/* Строка состояния учебного телефона: часы, беззвучный режим, связь, Wi-Fi и
 * батарея — нарисованы по скриншоту владельца.
 *
 * Зачем возиться с иконками, которых «всё равно не разглядят». Учебный экран
 * работает ровно тем, что человек узнаёт в нём своё устройство: полоски связи,
 * дуги Wi-Fi и капсула батареи с числом внутри — то, что видно на телефоне
 * каждую секунду. Абстрактная надпись «LTE ▮» на их месте выдаёт макет, и
 * дальше экран читается как рисунок, а не как «мой телефон».
 *
 * Всё в SVG и одним цветом currentColor: строка состояния должна перекраситься
 * заодно с экраном, если тот когда-нибудь станет тёмным.
 */

/** Часы + перечёркнутый колокольчик (беззвучный режим) — как на скриншоте. */
export const StatusTime = ({ time }) => (
    <span className="wt-phone__time">
        {time}
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M8.7 5.2A5.6 5.6 0 0 1 17.6 10c0 2.3.5 3.9 1.2 5M6.4 8.9c0 3.6-1 5-1.8 6.1h11.2M10.3 20a2 2 0 0 0 3.4 0" />
            <path d="M3 3.4 20.6 21" />
        </svg>
    </span>
);

/* Полоски связи: четыре столбика, последний бледный — «одно деление в запасе».
   Так это и выглядит на скриншоте, и так это выглядит в жизни чаще всего. */
const SignalBars = () => (
    <svg viewBox="0 0 18 12" width="17" height="11" aria-hidden="true">
        <rect x="0" y="8" width="3" height="4" rx="1" fill="currentColor" />
        <rect x="5" y="5.5" width="3" height="6.5" rx="1" fill="currentColor" />
        <rect x="10" y="3" width="3" height="9" rx="1" fill="currentColor" />
        <rect x="15" y="0" width="3" height="12" rx="1" fill="currentColor" opacity=".28" />
    </svg>
);

const Wifi = () => (
    <svg viewBox="0 0 16 12" width="15" height="11" fill="none" stroke="currentColor"
        strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
        <path d="M1.2 4.1a10.6 10.6 0 0 1 13.6 0" />
        <path d="M3.6 6.9a7 7 0 0 1 8.8 0" />
        <path d="M6 9.6a3.3 3.3 0 0 1 4 0" />
    </svg>
);

/* Батарея с числом ВНУТРИ капсулы — так iOS показывает заряд с 16-й версии, и
   так на скриншоте. Заряд рисуется шириной заливки, число — поверх неё. */
const Battery = ({ level = 90 }) => {
    const safe = Math.max(0, Math.min(100, Number(level) || 0));
    return (
        <svg viewBox="0 0 30 14" width="28" height="13" aria-hidden="true">
            <rect x="0.6" y="0.6" width="25" height="12.8" rx="4" fill="none"
                stroke="currentColor" strokeWidth="1.2" opacity=".38" />
            <rect x="2" y="2" width={22.2 * (safe / 100)} height="10" rx="2.6" fill="currentColor" />
            <path d="M27.4 5.2v3.6a2.3 2.3 0 0 0 0-3.6Z" fill="currentColor" opacity=".38" />
            <text x="13.1" y="10.4" textAnchor="middle" fontSize="8.4" fontWeight="700"
                fill="#fff" style={{ letterSpacing: '-0.02em' }}>{safe}</text>
        </svg>
    );
};

/** Правая группа значков строки состояния. */
export const StatusIcons = ({ battery = 90 }) => (
    <span className="wt-phone__icons">
        <SignalBars />
        <Wifi />
        <Battery level={battery} />
    </span>
);
