import React from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard } from '../ui/ios';

/*
 * Детали «Профиля» на компьютере — в духе «Системных настроек» macOS.
 *
 * Владелец 25.09.2026, после варианта с градиентными плитками и пастельными
 * кружками: «выглядит мультяшно, сделай приятнее, аккуратно и удобно в стиле
 * ios/macos». Поэтому: белые карточки с тонкой обводкой на сером полотне,
 * цвет — только в маленьких квадратных значках, как у пунктов «Настроек»
 * (зелёный телефон, красный календарь…), числа — тёмные, цветом отмечен лишь
 * значимый порог.
 *
 * Классы цветов перечислены целиком: Tailwind видит только буквальные имена.
 */

export const ICON_BG = {
    blue: 'bg-blue-500',
    sky: 'bg-sky-500',
    indigo: 'bg-indigo-500',
    purple: 'bg-purple-500',
    green: 'bg-green-500',
    emerald: 'bg-emerald-500',
    teal: 'bg-teal-500',
    orange: 'bg-orange-500',
    amber: 'bg-amber-500',
    red: 'bg-red-500',
    slate: 'bg-slate-700',
};

// Квадратный значок «Настроек»: белый знак на цвете, скругление как у иконок
// приложений. Два размера: строка группы (32 px) и карточка показателя (36 px).
// Крупно намеренно: владелец 25.09.2026 на большом мониторе — «слишком всё
// мелкое, столько свободного пространства осталось».
const ICON_SIZE = {
    row: 'h-8 w-8 rounded-[9px] text-[15px]',
    stat: 'h-9 w-9 rounded-[10px] text-[17px]',
};
export const IconSquare = ({ icon, tone = 'blue', size = 'row' }) => (
    <span
        className={`grid shrink-0 place-items-center text-white ${ICON_SIZE[size] || ICON_SIZE.row} ${ICON_BG[tone] || ICON_BG.blue}`}
        aria-hidden="true"
    >
        <FaIcon className={`fas ${icon}`} />
    </span>
);

// Подпись группы над карточкой — та же капитель, что в остальных iOS-разделах.
// Высота строгая (32 px), а не «не меньше»: в правке «Сохранить» выше надписи
// «Изменить», и шапка «Моих данных» распирала бы строку — карточка съезжала
// бы ниже соседней «Работы».
export const GroupHeader = ({ id, title, accessory = null }) => (
    <div className="mb-2 flex h-9 items-center justify-between gap-2 pl-5 pr-1">
        <h3 id={id} className="text-[12px] font-semibold uppercase tracking-wider text-slate-500">{title}</h3>
        {accessory}
    </div>
);

// Группа: подпись и карточка. Карточка тянется на всю высоту ячейки сетки, чтобы
// «Работа» и «Мои данные», стоящие рядом, кончались на одной линии.
export const DeskGroup = ({ id, title, accessory = null, className = '', as: Tag = 'section', children, ...rest }) => (
    <Tag aria-labelledby={id} className={`flex flex-col ${className}`} {...rest}>
        <GroupHeader id={id} title={title} accessory={accessory} />
        <div className={`${iosCard} flex-1 overflow-hidden`}>{children}</div>
    </Tag>
);

/**
 * Строка группы: значок, подпись, значение справа — как в «Настройках».
 * onClick превращает строку в кнопку, accessory — то, что стоит у правого края
 * («Изменить»), note — строка пояснения под полем правки.
 */
export const DeskRow = ({ icon, tone, label, children, onClick = null, accessory = null, note = null, noteTone = 'muted', alignTop = false, className = '' }) => {
    const body = (
        <>
            <div className={`flex min-h-[60px] flex-1 gap-3 px-4 py-3 2xl:gap-4 2xl:px-5 ${alignTop ? 'items-start' : 'items-center'}`}>
                <span className={alignTop ? 'pt-1' : ''}><IconSquare icon={icon} tone={tone} /></span>
                {/* Колонка подписей: 128 px хватает на «Специальность»; шире — только
                    на больших экранах, иначе на 1366 px длинное название вуза
                    переносилось бы, и строки соседних колонок расходились. */}
                <span className={`w-32 shrink-0 text-[16px] text-slate-900 2xl:w-40 ${alignTop ? 'pt-1.5' : ''}`}>{label}</span>
                <span className="flex min-w-0 flex-1 items-center justify-end gap-2 text-right">
                    {children}
                    {accessory}
                </span>
            </div>
            {note && (
                <p className={`-mt-2 px-5 pb-3 text-right text-[13px] ${noteTone === 'error' ? 'text-rose-600' : 'text-slate-400'}`}>{note}</p>
            )}
        </>
    );
    return typeof onClick === 'function' ? (
        <button
            type="button"
            onClick={onClick}
            className={`flex w-full flex-col text-left transition-colors hover:bg-slate-50 active:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/60 ${className}`}
        >
            {body}
        </button>
    ) : (
        // Колонка: основная строка тянется (flex-1) на высоту ячейки, а пояснение
        // под полем правки стоит ниже неё, а не выталкивается за край карточки.
        <div className={`flex flex-col ${className}`}>{body}</div>
    );
};

// Значение строки: серое справа, как в «Настройках»; длинное — переносится.
export const RowValue = ({ children, empty = 'Не указано' }) => (
    children
        ? <span className="min-w-0 break-words text-[16px] text-slate-500 tabular-nums">{children}</span>
        : <span className="text-[16px] text-slate-400">{empty}</span>
);

export const RowSkeleton = () => <span className="h-4 w-28 animate-pulse rounded-full bg-slate-100" aria-hidden="true" />;

// Кнопки шапки групп и «Быстрых действий» — спокойные, как в панелях macOS.
export const textButton = 'inline-flex items-center gap-1.5 rounded-lg px-3 py-1 text-[15px] font-medium text-blue-600 transition hover:bg-blue-50 active:scale-[0.98] disabled:opacity-50';
export const ghostButton = 'inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[15px] font-medium text-slate-500 transition hover:bg-slate-100 active:scale-[0.98] disabled:opacity-50';
export const primaryButton = 'inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-1.5 text-[15px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.98] disabled:opacity-60';
export const pillButton = 'inline-flex items-center gap-2 rounded-xl bg-slate-100 px-4 py-2.5 text-[15px] font-medium text-slate-700 transition hover:bg-slate-200 active:scale-[0.98]';
