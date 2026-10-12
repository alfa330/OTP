import React from 'react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput } from '../ui/ios';

/*
 * Размеры раздела «Учёт воды» на компьютере.
 *
 * Владелец 12.10.2026: «раздел учёта воды какой-то мелкий, то есть шрифты и
 * размеры, на большом экране смотрится не очень». Раздел был набран кеглем
 * 11–13 px в колонке 1180 px: на мониторе 1920 px он занимал две трети ширины,
 * на 2560 px — меньше половины, а вокруг оставалось пустое поле.
 *
 * Две ступени — ДОБАВКАМИ к классам, которые уже стояли в разметке (базовые
 * классы перенесены сюда как были, новое — только с префиксом брейкпоинта):
 *   до 1280 px            как было, пиксель в пиксель. Это телефон в любом
 *                         положении, планшет и узкое окно. Первая ступень — xl,
 *                         а не md: оболочка телефона включается и у телефона
 *                         боком (до 956 px ширины), и ровно на 768 px, где md
 *                         уже действует, а в окне 1024–1279 px таблицам тесно
 *                         и с прежним кеглем;
 *   компьютер (с xl)      окно от 1280 px: кегль на ступень крупнее, поле,
 *                         список, чип периода, кнопка и вкладки одного роста —
 *                         41 px, формы раскладываются в несколько колонок во
 *                         всю ширину раздела;
 *   большой экран (2xl)   окно от 1536 px: кегль «Профиля» — 16 px строка,
 *                         14 px подпись, — поля, кнопки и вкладки ростом 48 px,
 *                         колонка раздела до 1800 px.
 *
 * Порог большого экрана — 1536 px: с него в колонку раздела (окно минус
 * сайдбар и поля, от 1390 px) журнал с семью столбцами входит крупным кеглем.
 * На ноутбуке 1366 px он бы не поместился, поэтому там ступень мягче.
 *
 * Добавка с префиксом не спорит с базовым классом того же свойства: её
 * правило лежит в медиазапросе и стоит в CSS позже. Утилита БЕЗ префикса
 * поверх готового ios-класса такой гарантии не даёт — побеждает порядок
 * правил в сборке, а не порядок в строке.
 */

/* Кегль текста по роли. Базовый размер остаётся в разметке у самого элемента. */
export const T = {
    title: 'xl:text-[24px] xl:tracking-tight 2xl:text-[30px]',
    lead: 'xl:text-[14px] 2xl:text-[16px]',
    body: 'xl:text-[14px] 2xl:text-[16px]',
    sub: 'xl:text-[13px] 2xl:text-[14px]',
    note: 'xl:text-[12px] 2xl:text-[13px]',
    caps: 'xl:text-[11.5px] 2xl:text-[12.5px]',
};

export const wLabel = `${iosGroupLabel} ${T.caps}`;

/* Поле, кнопка, список и чип периода стоят в одних рядах — рост у них общий:
   41 px на компьютере (с xl) и 48 px на большом экране. */
export const wInput = `${iosInput} 2xl:px-4 2xl:py-3 2xl:text-[16px]`;

const BUTTON = 'xl:text-[14px] 2xl:px-5 2xl:py-3 2xl:text-[15px] 2xl:leading-6';
export const wBtnPrimary = `${iosBtnPrimary} ${BUTTON}`;
export const wBtnSecondary = `${iosBtnSecondary} ${BUTTON}`;
export const wBtnDanger = 'inline-flex items-center justify-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 '
    + 'text-[13.5px] font-semibold text-white transition hover:bg-red-700 active:scale-[0.98] disabled:opacity-50 '
    + BUTTON;
export const wBtnGhost = `${iosBtnGhost} xl:text-[13.5px] 2xl:px-4 2xl:py-2.5 2xl:text-[15px]`;

/* Чип периода (кнопка IosDateRangePicker): значок и шеврон внутри рисует сам
   пикер, поэтому их размер задаётся отсюда, через потомков кнопки. */
export const wChip = 'xl:px-3.5 xl:py-2.5 xl:text-[14px] 2xl:gap-2.5 2xl:px-4 2xl:py-3 2xl:text-[16px] '
    + '2xl:[&>svg]:size-4';

export const wPill = '2xl:px-2.5 2xl:py-1 2xl:text-[13px]';

/* Значки lucide: размер приходит атрибутами (size), класс их перекрывает. */
export const wIcon = '2xl:size-[18px]';

/* Вкладки раздела и переключатель внутри карточки — тот же IosSegmented;
   кнопки в нём свои, размер им задаётся через потомков контейнера. Полоса
   вкладок ростом с поле: 41 px на компьютере, 48 px на большом экране. */
export const wTabs = 'xl:[&>button]:px-4 xl:[&>button]:py-[7px] xl:[&>button]:text-[14px] '
    + '2xl:rounded-[13px] 2xl:p-1 2xl:[&>button]:rounded-[10px] 2xl:[&>button]:px-6 2xl:[&>button]:py-2 '
    + '2xl:[&>button]:text-[15px] 2xl:[&>button]:leading-6';
export const wSegments = 'xl:[&>button]:text-[13.5px] 2xl:[&>button]:py-[7px] 2xl:[&>button]:text-[14.5px]';

/* Таблицы растут только на большом экране. В окне 1280–1535 px семь столбцов
   журнала входят в карточку лишь прежним кеглем 13 px: с 14 px и полями шире
   таблица на 1280 px уезжала вбок на 45 px (замер), а на Windows, где шрифт
   шире, — и на 1366 px. Поэтому до 2xl таблица прежняя, включая перенос
   заголовков: он и даёт ей войти. С 2xl заголовок не переносится — иначе при
   длинном названии офиса «Расход в день» вставал в две строки. */
export const wTable = 'w-full text-left text-[13px] 2xl:text-[16px]';
export const wThead = 'bg-slate-50/80 text-[11.5px] uppercase tracking-wide text-slate-500 2xl:text-[12.5px]';
export const wTh = 'px-3.5 py-2.5 font-semibold 2xl:whitespace-nowrap 2xl:px-5 2xl:py-3.5';
export const wTd = 'px-3.5 py-2.5 2xl:px-5 2xl:py-4';
/* Вторая строка ячейки (адрес, сотрудник, тарифы): её базовый кегль стоит у
   самого элемента. */
export const wTdSub = '2xl:text-[14px]';

/* Заголовок и подзаголовок окна IosModal: передаются узлом, чтобы шапка окна
   не оставалась мельче его содержимого. */
export const ModalTitle = ({ children }) => <span className="2xl:text-[18px]">{children}</span>;
export const ModalSubtitle = ({ children }) => <span className={T.sub}>{children}</span>;

/* Группа «подпись — карточка — пояснение»: двойник IosSection с размерами
   раздела. className — место группы в сетке большого экрана. */
export const WaterGroup = ({ title, hint, children, className = '' }) => (
    <section className={`space-y-1.5 2xl:space-y-2 ${className}`}>
        {title && <div className={wLabel}>{title}</div>}
        <div className={`${iosCard} space-y-3 p-4 xl:p-5 2xl:space-y-4 2xl:p-6`}>
            {children}
        </div>
        {hint && <div className={`px-1 text-[11px] text-slate-500 ${T.note}`}>{hint}</div>}
    </section>
);
