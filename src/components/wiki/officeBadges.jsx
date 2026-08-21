import React, { useMemo } from 'react';
import { Clock, Coffee } from 'lucide-react';
import { IosBadge } from '../ui/ios';
import { DAY_STATE_PILL, DAY_STATE_TONE } from './officeDayStatus';
import { hasSchedule, officeStatus } from './officeSchedule';

/* Бейджи состояния офиса — на все три места раздела (карточка, строка таблицы,
 * подробности). Собраны в один файл, потому что выбор между ними — одно
 * правило, и оно должно жить рядом, а не повторяться в каждом месте.
 *
 * Отдельно от officeDayStatus.js: тот модуль — чистые правила без JSX, его
 * читают и тесты, и правило показа не должно тащить за собой React.
 */

const STATUS_TONE = { open: 'green', break: 'amber', closed: 'slate' };

/** Живой статус по часам: «Открыто до 19:00». Только для сегодняшнего дня — за
 *  прошедший день это была бы выдумка: что офис закрылся именно в 19:00, никто
 *  не записывал. Отдаёт null, когда графика нет вовсе. */
export const OfficeLiveBadge = ({ schedule, tick }) => {
    // tick только заставляет пересчитать: время берётся из часов, а не из него.
    const status = useMemo(() => officeStatus(schedule), [schedule, tick]);

    if (status.state === 'none') return null;

    return (
        <IosBadge tone={STATUS_TONE[status.state]}>
            {status.state === 'break' ? <Coffee size={11} /> : <Clock size={11} />}
            {status.state === 'open' && `Открыто до ${status.until}`}
            {status.state === 'break' && `Обед до ${status.until}`}
            {status.state === 'closed' && (status.opensAt
                ? `Закрыто · откроется ${status.opensDay ? `${status.opensDay} ` : ''}${status.opensAt}`
                : 'Закрыто')}
        </IosBadge>
    );
};

/** Статус за выбранный день на белом фоне — карточка и подробности. */
export const OfficeDayBadge = ({ status }) => (
    <IosBadge tone={DAY_STATE_TONE[status.state]}>
        {status.state === 'open' && <Clock size={11} />}
        {status.label}
        {status.state === 'open' && status.until && ` · ${status.from}–${status.until}`}
    </IosBadge>
);

/**
 * Бейдж состояния на белом фоне — один вход для карточки и подробностей.
 *
 * Правило выбора здесь, а не в вызывающем коде: раньше каждое место писало
 * `isToday && !marked ? <живой> : <дневной>` само, и оба места пропускали два
 * случая, в которых живой бейдж молча отдаёт null — «офиса в городе нет» и
 * незаполненный график. В карточке из-за этого у Актобе и Костаная не было
 * состояния вообще: пустое место там, где таблица честно писала «Офиса в городе
 * нет» и «Нет графика».
 */
export const OfficeStatusBadge = ({ schedule, status, isToday, marked, tick }) => {
    const live = isToday && !marked
        && status.state !== 'absent' && hasSchedule(schedule);
    return live
        ? <OfficeLiveBadge schedule={schedule} tick={tick} />
        : <OfficeDayBadge status={status} />;
};

/** Тот же статус, но для ЗАЛИТОЙ строки таблицы.
 *
 *  IosBadge здесь не годится: его тона (bg-emerald-50) светлее самой строки, и
 *  бейдж в ней пропадал — ровно то, от чего уводит ТЗ. Тон на ступень плотнее
 *  строки, как в макете, а кружок тот же, что в легенде, чтобы цветовую
 *  кодировку не приходилось учить дважды. */
export const OfficeDayPill = ({ state, label, className = '' }) => {
    const pill = DAY_STATE_PILL[state] || DAY_STATE_PILL.none;
    return (
        <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-[11.5px] font-semibold ${pill.fill} ${className}`}>
            <span className={`h-[7px] w-[7px] shrink-0 rounded-full ${pill.dot}`} />
            {label}
        </span>
    );
};
