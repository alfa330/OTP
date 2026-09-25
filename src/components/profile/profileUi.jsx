import React from 'react';
import useIsMobileShell from '../common/useIsMobileShell';
import { iosCard } from '../ui/ios';
// Строки и подписи групп телефона — общие с «Моими часами», «Моими сменами» и
// «Аукционом» (sa-m-*): одна высота строки, один разделитель от текста, один
// кегль подписи. Все правила там заперты на body.mobile-shell.
import '../resources/shift-auction-mobile.css';

/*
 * Примитивы «Профиля»: группа в духе «Настроек» iOS/macOS и строка
 * «подпись слева — значение справа».
 *
 * Все группы раздела — и «Работа», и «Мои данные» — собраны из этих двух
 * деталей, поэтому у них одна высота строки, одна линия подписей и один край
 * значений. Значение прижато вправо, как в «Настройках»: в карточке подпись и
 * значение стоят зеркально, и группа читается симметрично.
 *
 * Раскладка строки задана ИНЛАЙН-СТИЛЕМ намеренно. Эвристики мобильной
 * оболочки (mobile-shell.css) ставят в колонку любой `.flex.items-start`, у
 * которого внутри есть `.flex-1`, и схлопывают `grid-cols-[…]` в одну колонку —
 * так они чинят двухпанельные экраны. Строка профиля под них попадала и на
 * телефоне разваливалась на «подпись над значением». Инлайн-стиль этими
 * селекторами не ловится.
 */

// Колонка подписей нужна полям правки: с ней поля «Моих данных» начинаются с
// одной линии. На телефоне шире — там подписи набраны 16 px.
const labelColumn = (isMobileShell) => (isMobileShell ? '8.5rem' : '9rem');

const rowGridStyle = (isMobileShell) => ({
    display: 'grid',
    gridTemplateColumns: `${labelColumn(isMobileShell)} minmax(0, 1fr)`,
    columnGap: '12px',
    alignItems: 'start',
});

const VALUE_LINE_STYLE = {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'flex-end',
    minHeight: 36,
    gap: 8,
};

export const textSize = (isMobileShell) => (isMobileShell ? 'text-[16px] leading-[22px]' : 'text-[14px] leading-5');

// Подпись группы. Не h3: оболочка на телефоне растягивает все h3 раздела до
// 16px, и мелкая капитель превращалась в крупный заголовок. На телефоне кегль
// 13 px даёт общий класс sa-m-group__label.
export const ProfileGroupTitle = ({ id, children }) => (
    <div
        id={id}
        role="heading"
        aria-level={3}
        className="sa-m-group__label text-[11px] font-semibold uppercase tracking-wider text-slate-500"
    >
        {children}
    </div>
);

export const ProfileGroup = ({ id, title, accessory = null, children, as: Tag = 'section', ...rest }) => {
    const isMobileShell = useIsMobileShell();
    return (
        <Tag aria-labelledby={id} {...rest}>
            {/* Подпись группы стоит на линии текста строк (px-4) — как в «Настройках». */}
            <div
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', minHeight: 32, gap: 8 }}
                className="pb-1 pl-4 pr-1"
            >
                <ProfileGroupTitle id={id}>{title}</ProfileGroupTitle>
                {accessory}
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                {/* Разделители: на телефоне — общие sa-m-row (отступают от края
                    до текста), на компьютере — тонкая линия во всю ширину. */}
                <div className={isMobileShell ? '' : 'divide-y divide-slate-100'}>{children}</div>
            </div>
        </Tag>
    );
};

// Значение строки. Переносится, а не режется многоточием: длинное название
// вуза — свои же данные человека, он должен видеть их целиком.
export const ProfileValue = ({ children }) => {
    const isMobileShell = useIsMobileShell();
    return (
        <span className={`min-w-0 break-words py-2 text-right text-slate-900 tabular-nums ${textSize(isMobileShell)}`}>
            {children}
        </span>
    );
};

export const ProfileEmpty = ({ children = 'Не указано' }) => {
    const isMobileShell = useIsMobileShell();
    return <span className={`py-2 text-right text-slate-400 ${textSize(isMobileShell)}`}>{children}</span>;
};

export const ProfileSkeletonValue = ({ width = 'w-24' }) => (
    <span className={`h-3.5 ${width} animate-pulse rounded-full bg-slate-100`} aria-hidden="true" />
);

// Строка правки на телефоне: подпись над полем, поле во всю ширину — как ячейки
// форм iOS. В строку с колонкой подписей поле на 390 px получало 178 px: номер
// карты уезжал за край, а «Магистратура, …» не различала первый и второй курс.
const STACKED_GRID_STYLE = { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', rowGap: 4 };

/**
 * Строка группы. onClick превращает её в кнопку (с подсветкой нажатия), а
 * accessory — это то, что iOS ставит у правого края: «Изменить», шеврон.
 * stacked — подпись над значением (поля правки на телефоне).
 */
export const ProfileRow = ({ label, children, note = null, noteTone = 'muted', onClick = null, accessory = null, stacked = false }) => {
    const isMobileShell = useIsMobileShell();
    const body = (
        <>
            <div className={stacked ? 'px-1 pt-1 text-[13px] leading-[18px] text-slate-500' : `py-2 text-slate-500 ${textSize(isMobileShell)}`}>{label}</div>
            <div className="min-w-0">
                <div style={stacked ? { ...VALUE_LINE_STYLE, justifyContent: 'stretch' } : VALUE_LINE_STYLE}>
                    {children}
                    {accessory && <span style={{ flexShrink: 0 }}>{accessory}</span>}
                </div>
                {note && (
                    // Пояснение бывает только у поля правки — и стоит на линии его текста.
                    <p className={`mt-1 px-3 pb-1 text-[12px] ${noteTone === 'error' ? 'text-rose-600' : 'text-slate-400'}`}>
                        {note}
                    </p>
                )}
            </div>
        </>
    );
    const rowClass = `${isMobileShell ? 'sa-m-row ' : ''}w-full px-4 ${stacked ? 'py-2' : 'py-1.5'} text-left`;
    const gridStyle = stacked ? STACKED_GRID_STYLE : rowGridStyle(isMobileShell);
    if (typeof onClick === 'function') {
        return (
            <button
                type="button"
                onClick={onClick}
                style={gridStyle}
                className={`${rowClass} transition-colors hover:bg-slate-50 active:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500/60`}
            >
                {body}
            </button>
        );
    }
    return <div style={gridStyle} className={rowClass}>{body}</div>;
};
