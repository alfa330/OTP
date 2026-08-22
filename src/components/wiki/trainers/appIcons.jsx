import React from 'react';

/* Знаки и значки чужих приложений — по реальным скриншотам подписания.
 *
 * Зачем отдельным файлом. Экран узнаётся не текстом, а знаками: ромбовидная
 * решётка Sapar, «g» eGov с сине-жёлтым кругом, скруглённый домик Такси.Про в
 * нижней панели. Пока на их месте стояли эмодзи (⚙ ◍ 🔒), учебный экран
 * читался макетом: эмодзи рисуются шрифтом системы и на каждой машине выглядят
 * по-своему, а цвет им не задать. Здесь всё в SVG и в currentColor — значок
 * перекрашивается вместе с состоянием кнопки (активная вкладка, ловушка,
 * подсветка цели).
 *
 * Размер задаётся снаружи через CSS (width/height у svg), поэтому в разметке
 * значки идут без размеров: одна и та же иконка стоит и в панели 20 px, и в
 * карточке документа 16 px.
 */

/* ── Sapar ───────────────────────────────────────────────────────────────── */

/** Знак Sapar: решётка из девяти ромбов на тонком кресте — крупный центр,
 *  два таких же слева и справа, четыре по диагоналям и по одному сверху и
 *  снизу. Именно эта фигура, а не слово «sapar», опознаётся первой. */
export const SaparMark = ({ className = '' }) => (
    <svg className={className} viewBox="0 0 48 48" aria-hidden="true" fill="currentColor">
        {/* Тонкий каркас: вертикаль и две перекладины, на которых держатся ромбы. */}
        <rect x="22.6" y="6" width="2.8" height="36" rx="1.4" />
        <rect x="14" y="12.6" width="20" height="2.8" rx="1.4" />
        <rect x="14" y="32.6" width="20" height="2.8" rx="1.4" />
        {/* Центр и его соседи слева-справа — крупные. */}
        <rect x="16.2" y="16.2" width="15.6" height="15.6" rx="2.6" transform="rotate(45 24 24)" />
        <rect x="1.8" y="17.8" width="12.4" height="12.4" rx="2.4" transform="rotate(45 8 24)" />
        <rect x="33.8" y="17.8" width="12.4" height="12.4" rx="2.4" transform="rotate(45 40 24)" />
        {/* Сверху, снизу и по диагоналям — помельче. */}
        <rect x="18.7" y="1.7" width="10.6" height="10.6" rx="2.2" transform="rotate(45 24 7)" />
        <rect x="18.7" y="35.7" width="10.6" height="10.6" rx="2.2" transform="rotate(45 24 41)" />
        <rect x="8.7" y="8.7" width="10.6" height="10.6" rx="2.2" transform="rotate(45 14 14)" />
        <rect x="28.7" y="8.7" width="10.6" height="10.6" rx="2.2" transform="rotate(45 34 14)" />
        <rect x="8.7" y="28.7" width="10.6" height="10.6" rx="2.2" transform="rotate(45 14 34)" />
        <rect x="28.7" y="28.7" width="10.6" height="10.6" rx="2.2" transform="rotate(45 34 34)" />
    </svg>
);

/** Логотип кабинета: знак и слово под ним — ровно так он стоит в шапке. */
export const SaparLogo = () => (
    <span className="wt-sp__logo">
        <SaparMark className="wt-sp__logo-mark" />
        <b>sapar</b>
    </span>
);

/* Нижняя панель кабинета: четыре значка, обведённые линией одной толщины. */
const SP_NAV_ICONS = {
    profile: (
        <>
            <circle cx="12" cy="8" r="3.6" />
            <path d="M5.4 20a6.6 6.6 0 0 1 13.2 0" />
        </>
    ),
    docs: (
        <>
            <path d="M6.5 3.2h7L18 7.7v13.1H6.5Z" />
            <path d="M13.2 3.2v4.6H18" />
            <path d="M12.6 11.6v3.6M12.6 17.8v.2" />
        </>
    ),
    help: (
        <>
            <circle cx="12" cy="12" r="8.6" />
            <path d="M9.7 9.6a2.4 2.4 0 1 1 2.9 2.5v1.4M12.6 16.6v.2" />
        </>
    ),
    exit: (
        <>
            <circle cx="12" cy="12" r="8.6" />
            <path d="M8 12h7.4M12.6 8.9 15.9 12l-3.3 3.1" />
        </>
    ),
};

/** Значок нижней панели кабинета Sapar. */
export const SpNavIcon = ({ name }) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {SP_NAV_ICONS[name]}
    </svg>
);

/** Кружок-ярлык группы документов в листе подписания (Яндекс / таксопарк). */
export const SpGroupIcon = ({ kind }) => (
    <span className={`wt-sp__group-ico is-${kind}`} aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round">
            {kind === 'park' ? (
                <>
                    <path d="M5 21V5.5h9V21M14 10.5h5V21M3.5 21h17" />
                    <path d="M8 9h3M8 13h3M8 17h3M16.5 14h1M16.5 17.5h1" />
                </>
            ) : (
                <>
                    <path d="M7 3.4h7L18 7.6V20.6H7Z" />
                    <path d="M13.6 3.4v4.2H18" />
                </>
            )}
        </svg>
    </span>
);

/* ── eGov Mobile ─────────────────────────────────────────────────────────── */

/** Знак eGov mobile: чёрные «e», «o» и «v», а вместо петли «g» — кольцо, у
 *  которого ЛЕВАЯ сторона выкрашена тремя дугами: синей снизу, золотой слева и
 *  тёмно-синей сверху. Именно этот трёхцветный сегмент и опознаётся — без него
 *  надпись «egov» ничем не отличается от любой другой. */
export const EgovLogo = () => (
    <span className="wt-eg__logo" aria-label="eGov mobile">
        <svg viewBox="0 0 152 64" aria-hidden="true">
            {/* «e»: кольцо с перекладиной и вырезом справа снизу. */}
            <path
                fill="#111827"
                d="M20 8a16 16 0 1 0 14.5 22.6h-8.2A9.1 9.1 0 0 1 12 27.2h24.3A16 16 0 0 0 20 8Zm0 6.6a9.4 9.4 0 0 1 8.9 6.4H11.1A9.4 9.4 0 0 1 20 14.6Z"
            />
            {/* Кольцо «g»: сначала чёрное целиком, потом три цветные дуги слева. */}
            <circle cx="55" cy="24" r="15" fill="none" stroke="#111827" strokeWidth="8" />
            <path d="M47.5 37A15 15 0 0 1 40 24" fill="none" stroke="#2c3fa5" strokeWidth="8" />
            <path d="M40 24a15 15 0 0 1 2.6-8.4" fill="none" stroke="#c9a227" strokeWidth="8" />
            <path d="M42.6 15.6A15 15 0 0 1 51.7 9.4" fill="none" stroke="#12325f" strokeWidth="8" />
            {/* Выносной элемент «g»: вниз и крючком налево. */}
            <path
                d="M66.6 33.5v7.4A11.6 11.6 0 0 1 46 48"
                fill="none"
                stroke="#111827"
                strokeWidth="8"
            />
            {/* «o» — кольцо, «v» — сплошной клин. */}
            <circle cx="95" cy="24" r="12.6" fill="none" stroke="#111827" strokeWidth="8" />
            <path fill="#111827" d="M113.5 10h8.8l7.2 19.7 7.2-19.7h8.8l-11.6 28h-8.8Z" />
            <text x="98" y="59" fontSize="14" fontWeight="700" fill="#111827"
                fontFamily="Helvetica, Arial, sans-serif" textAnchor="middle">mobile</text>
        </svg>
    </span>
);

/** Отпечаток пальца на клавиатуре кода — он там есть, и его ищут глазами. */
export const FingerprintIcon = () => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.35"
        strokeLinecap="round" aria-hidden="true">
        <path d="M4.2 10.4a8.6 8.6 0 0 1 15.6 0" />
        <path d="M6.6 13.6a5.9 5.9 0 0 1 1.2-4.3 5.6 5.6 0 0 1 8.6 0 5.9 5.9 0 0 1 1.2 4.3" />
        <path d="M9.1 15.6a3.3 3.3 0 0 1 .3-4.6 3.2 3.2 0 0 1 5.3 2.4c0 1.5-.2 3.3-.8 4.8" />
        <path d="M11.6 12.9c0 2.6-.2 4.6-.9 6.6" />
        <path d="M6.9 18.6c.7-1.3 1-2.6 1.1-4" />
        <path d="M16.8 17.4c.3-1.1.4-2.2.4-3.2" />
    </svg>
);

/** Стирающая клавиша ⌫ — пятиугольник с крестиком, как в eGov. */
export const BackspaceIcon = () => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M8.6 5.4h11.2a1.6 1.6 0 0 1 1.6 1.6v10a1.6 1.6 0 0 1-1.6 1.6H8.6L2.6 12Z" />
        <path d="m11.6 9.4 5 5.2M16.6 9.4l-5 5.2" />
    </svg>
);

/** Зелёная «звезда-печать» с галочкой — экран «Подписание выполнено успешно!». */
export const SignedBadge = () => (
    <svg className="wt-eg__badge" viewBox="0 0 96 96" aria-hidden="true">
        <defs>
            <linearGradient id="wt-signed" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stopColor="#8fdc4a" />
                <stop offset="1" stopColor="#3faf25" />
            </linearGradient>
        </defs>
        {/* Волнистый контур печати: 12 лепестков по кругу. */}
        <path
            fill="url(#wt-signed)"
            d="M48 8.5c3.6-4.6 10.8-3.4 12.8 2.1 1.4 3.9 5.9 5.6 9.5 3.6 5-2.8 10.9 1.5 9.9 7.2-.7 4.1 2.4 7.8 6.5 7.9 5.8.2 8.5 7.3 4.5 11.5-2.8 3-2.2 7.8 1.2 10 4.9 3.1 4.1 10.5-1.3 12.5-3.8 1.4-5.4 5.9-3.4 9.4 2.9 5-1.5 11-7.1 10-4.1-.7-7.9 2.4-8 6.5-.2 5.7-7.2 8.4-11.4 4.4-3-2.8-7.8-2.2-10 1.2-3.2 4.9-10.5 4.1-12.5-1.3-1.5-3.8-5.9-5.4-9.4-3.4-5 2.9-11-1.5-10-7.1.7-4.1-2.4-7.9-6.5-8-5.7-.2-8.4-7.2-4.4-11.4 2.8-3 2.2-7.8-1.2-10-4.9-3.2-4.1-10.5 1.3-12.5 3.8-1.5 5.4-5.9 3.4-9.4-2.9-5 1.5-11 7.1-10 4.1.7 7.9-2.4 8-6.5.2-5.7 7.2-8.4 11.4-4.4 3 2.8 7.8 2.2 10-1.2Z"
        />
        <path d="m31 49.5 11.5 11.5L66 36.5" fill="none" stroke="#fff" strokeWidth="8"
            strokeLinecap="round" strokeLinejoin="round" />
        {/* Искры — они есть в приложении и делают экран узнаваемым. */}
        <path fill="#f7d117" d="M18 30.5 21 22l3 8.5 8.5 3-8.5 3-3 8.5-3-8.5-8.5-3Z" />
        <path fill="#f7d117" d="M25 66.5 27 61l2 5.5 5.5 2-5.5 2-2 5.5-2-5.5-5.5-2Z" />
    </svg>
);

/* ── Такси.Про ───────────────────────────────────────────────────────────── */

const TP_NAV_ICONS = {
    home: <path d="M4 11.2 12 4.4l8 6.8V19a1.2 1.2 0 0 1-1.2 1.2h-4.2v-5.4h-5.2v5.4H5.2A1.2 1.2 0 0 1 4 19Z" />,
    kaspi: (
        <>
            <rect x="3.4" y="3.4" width="6.4" height="6.4" rx="1.2" />
            <rect x="14.2" y="3.4" width="6.4" height="6.4" rx="1.2" />
            <rect x="3.4" y="14.2" width="6.4" height="6.4" rx="1.2" />
            <path d="M14.2 14.2h2.6v2.6h-2.6ZM18.4 14.2h2.2M14.2 18.6h2.6M18.8 18.4v2.2M20.8 17h-2" />
        </>
    ),
    baiga: (
        <>
            <path d="M7 4.2h10v4.4a5 5 0 0 1-10 0Z" />
            <path d="M7 5.6H4.6v1.8A3 3 0 0 0 7 10.3M17 5.6h2.4v1.8A3 3 0 0 1 17 10.3" />
            <path d="M12 13.6v3.6M8.6 20.2h6.8" />
        </>
    ),
    docs: (
        <>
            <rect x="7.4" y="3.6" width="10" height="13.2" rx="2" />
            <path d="M14.4 20.4H6.6a2 2 0 0 1-2-2V7.6" />
        </>
    ),
    profile: (
        <>
            <circle cx="10.6" cy="8.2" r="3.4" />
            <path d="M4.6 20a6 6 0 0 1 9.6-4.8" />
            <circle cx="17.6" cy="17.4" r="2.6" />
            <path d="M17.6 13.6v1M17.6 21.2v1M21.4 17.4h-1M14.8 17.4h-1" />
        </>
    ),
};

/** Значок нижней панели Такси.Про. */
export const TpNavIcon = ({ name }) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {TP_NAV_ICONS[name]}
    </svg>
);

/* Значки-обводки в карточках приложения: здание парка, машина, телефон,
   кошелёк, обновление, история. Все одной толщины — так они и нарисованы. */
const TP_LINE_ICONS = {
    park: (
        <>
            <path d="M4.6 20.4V4.8h9.2v15.6M13.8 10.2h5.6v10.2M3 20.4h18" />
            <path d="M7.4 8.2h3M7.4 11.6h3M7.4 15h3M16 13.6h1M16 17h1" />
        </>
    ),
    car: (
        <>
            <path d="M3.4 14.6h17.2v3.6a.9.9 0 0 1-.9.9h-1.4a.9.9 0 0 1-.9-.9v-.9H6.6v.9a.9.9 0 0 1-.9.9H4.3a.9.9 0 0 1-.9-.9Z" />
            <path d="M5 14.6 6.8 9a1.8 1.8 0 0 1 1.7-1.2h7a1.8 1.8 0 0 1 1.7 1.2l1.8 5.6" />
            <path d="M6.6 11.6h10.8" />
        </>
    ),
    phone: <path d="M6.2 3.8h2.9l1.5 3.6-2 1.4a11 11 0 0 0 5.2 5.2l1.4-2 3.6 1.5v2.9a2.2 2.2 0 0 1-2.4 2.2A15.6 15.6 0 0 1 4 6.2a2.2 2.2 0 0 1 2.2-2.4Z" />,
    wallet: (
        <>
            <rect x="3.2" y="6.4" width="17.6" height="12.4" rx="2.6" />
            <path d="M3.2 10.4h17.6M16.6 14.4h1.6" />
        </>
    ),
    refresh: (
        <>
            <path d="M20 12a8 8 0 1 1-2.6-5.9" />
            <path d="M20.4 4.4v4.4H16" />
        </>
    ),
    history: (
        <>
            <circle cx="12" cy="12" r="8.4" />
            <path d="M12 7.2V12l3.2 2" />
        </>
    ),
    edit: <path d="M4.4 19.6h3.2L19 8.2a2 2 0 0 0-2.8-2.8L4.8 16.8Zm10.4-13 2.8 2.8" />,
    doc: (
        <>
            <path d="M6.8 3.4h7L18 7.6v13H6.8Z" />
            <path d="M13.4 3.4v4.2H18M9.6 12.2h5.4M9.6 15.6h5.4" />
        </>
    ),
    eye: (
        <>
            <path d="M2.6 12S6 6.4 12 6.4 21.4 12 21.4 12 18 17.6 12 17.6 2.6 12 2.6 12Z" />
            <circle cx="12" cy="12" r="2.9" />
        </>
    ),
    share: (
        <>
            <circle cx="17.6" cy="5.8" r="2.6" />
            <circle cx="6.4" cy="12" r="2.6" />
            <circle cx="17.6" cy="18.2" r="2.6" />
            <path d="m8.7 10.8 6.6-3.6M8.7 13.2l6.6 3.6" />
        </>
    ),
    download: <path d="M12 3.8v11.4M7.8 11.4 12 15.6l4.2-4.2M4.6 19.4h14.8" />,
    /* Росчерк на кнопке «Подписать» — по нему её и находят в списке актов. */
    sign: (
        <>
            <path d="M3.6 16.4c2.4 0 3-2.2 4.2-5.4C9 7.8 9.9 4.6 11.6 4.6c1.4 0 1.8 1.5 1 3.6-1 2.6-3.2 4.4-3.2 6.2 0 1 .7 1.6 1.7 1.6 1.6 0 2.6-1.4 3.8-1.4.8 0 1.2.5 1.2 1.1 0 .5-.3.8-.3 1.1" />
            <path d="M4 20h16" />
        </>
    ),
    check2: <path d="m2.6 12.6 3.8 3.8L14 8.8M10.4 16.4l1.2 1.2L21 8" />,
};

/** Линейный значок Такси.Про (карточки, кнопки, строки). */
export const TpIcon = ({ name, className = '' }) => (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {TP_LINE_ICONS[name]}
    </svg>
);

/* ── Яндекс Про ───────────────────────────────────────────────────────────
   Значки нижней панели и строк профиля нарисованы по кадрам приложения:
   тонкая обводка, скругления, ничего залитого. Отдельно — марки провайдеров
   ЭДО: они ЦВЕТНЫЕ и залитые, потому что в списке водитель находит Sapar
   именно по чёрному ромбовому орнаменту, а не по подписи. */

const YP_NAV_ICONS = {
    orders: <path d="M20.4 4.2 4.2 10.8l6.2 2.6 2.6 6.2Z" />,
    intercity: (
        <>
            <path d="M4 17.4h16" />
            <path d="M6.4 17.4V9.6l5.6-3.8 5.6 3.8v7.8" />
            <path d="M10.2 17.4v-4.2h3.6v4.2" />
        </>
    ),
    money: (
        <>
            <rect x="3.4" y="6.6" width="17.2" height="11.2" rx="2.6" />
            <path d="M16.2 12.2h2.2" />
        </>
    ),
    chats: <path d="M5 4.6h14a1.8 1.8 0 0 1 1.8 1.8v8.4a1.8 1.8 0 0 1-1.8 1.8H9.6L5.4 20v-3.4H5a1.8 1.8 0 0 1-1.8-1.8V6.4A1.8 1.8 0 0 1 5 4.6Z" />,
    profile: (
        <>
            <circle cx="12" cy="8.4" r="3.6" />
            <path d="M5.2 20a6.8 6.8 0 0 1 13.6 0" />
        </>
    ),
};

/** Значок нижней панели Яндекс Про. */
export const YpNavIcon = ({ name }) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {YP_NAV_ICONS[name]}
    </svg>
);

/* Значки строк профиля и разделов. Приложение рисует их в серых кружках —
   кружок делает CSS, здесь только сам знак. */
const YP_ROW_ICONS = {
    legal: (
        <>
            <rect x="3.4" y="5.6" width="17.2" height="13" rx="2.6" />
            <path d="M3.4 11.4h5.2a1 1 0 0 1 1 1v.4a2.4 2.4 0 0 0 4.8 0v-.4a1 1 0 0 1 1-1h5.2" />
        </>
    ),
    /* Шестерёнка, а не «солнце»: восемь отдельных лучиков вокруг кружка
       читались именно солнцем — зубцы обязаны примыкать к ободу. */
    settings: (
        <>
            <circle cx="12" cy="12" r="2.9" />
            <path d="M19.1 14.6a1.5 1.5 0 0 0 .3 1.65l.05.05a1.8 1.8 0 0 1-2.55 2.55l-.05-.05a1.5 1.5 0 0 0-1.65-.3 1.5 1.5 0 0 0-.9 1.37v.13a1.8 1.8 0 0 1-3.6 0v-.07a1.5 1.5 0 0 0-.98-1.37 1.5 1.5 0 0 0-1.65.3l-.05.05A1.8 1.8 0 0 1 4.9 16.4l.05-.05a1.5 1.5 0 0 0 .3-1.65 1.5 1.5 0 0 0-1.37-.9H3.7a1.8 1.8 0 0 1 0-3.6h.13a1.5 1.5 0 0 0 1.37-.98 1.5 1.5 0 0 0-.3-1.65L4.85 7.5A1.8 1.8 0 0 1 7.4 4.95l.05.05a1.5 1.5 0 0 0 1.65.3h.07a1.5 1.5 0 0 0 .9-1.37V3.8a1.8 1.8 0 0 1 3.6 0v.13a1.5 1.5 0 0 0 .9 1.37 1.5 1.5 0 0 0 1.65-.3l.05-.05A1.8 1.8 0 0 1 18.82 7.5l-.05.05a1.5 1.5 0 0 0-.3 1.65v.07a1.5 1.5 0 0 0 1.37.9h.13a1.8 1.8 0 0 1 0 3.6h-.13a1.5 1.5 0 0 0-1.37.9Z" />
        </>
    ),
    diagnostics: (
        <>
            <path d="M4.2 8.4V5.6a1.4 1.4 0 0 1 1.4-1.4h2.8M15.6 4.2h2.8a1.4 1.4 0 0 1 1.4 1.4v2.8M19.8 15.6v2.8a1.4 1.4 0 0 1-1.4 1.4h-2.8M8.4 19.8H5.6a1.4 1.4 0 0 1-1.4-1.4v-2.8" />
            <circle cx="12" cy="12" r="3" />
        </>
    ),
    camera: (
        <>
            <path d="M4 8.6h3l1.4-2h7.2l1.4 2h3v9.8H4Z" />
            <circle cx="12" cy="13.4" r="3.2" />
        </>
    ),
    fuel: (
        <>
            <path d="M6 20.4V5.6A1.4 1.4 0 0 1 7.4 4.2h5.2A1.4 1.4 0 0 1 14 5.6v14.8" />
            <path d="M4.4 20.4h11.2M7.6 8.2h4.8" />
            <path d="M14 10.6h2.6a1.6 1.6 0 0 1 1.6 1.6v4a1.4 1.4 0 0 0 2.8 0V9.4l-2.2-2.6" />
        </>
    ),
    gift: (
        <>
            <rect x="3.6" y="8.4" width="16.8" height="4" rx="1.2" />
            <path d="M5.2 12.4v6.4a1.4 1.4 0 0 0 1.4 1.4h10.8a1.4 1.4 0 0 0 1.4-1.4v-6.4M12 8.4v11.8" />
            <path d="M12 8.4S10.6 4.2 8.2 4.2a2.1 2.1 0 0 0 0 4.2M12 8.4s1.4-4.2 3.8-4.2a2.1 2.1 0 0 1 0 4.2" />
        </>
    ),
    star: <path d="m12 4.4 2.5 5.1 5.6.8-4 4 .9 5.6-5-2.7-5 2.7.9-5.6-4-4 5.6-.8Z" />,
    doc: (
        <>
            <path d="M7 3.8h6.6l4.4 4.4v12H7Z" />
            <path d="M13.4 3.8v4.6H18M9.8 12.6h6M9.8 16h4.2" />
        </>
    ),
    info: (
        <>
            <circle cx="12" cy="12" r="8.4" />
            <path d="M12 11v5.4M12 8.1v.9" />
        </>
    ),
};

/** Значок строки в Яндекс Про. */
export const YpIcon = ({ name, className = '' }) => (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {YP_ROW_ICONS[name]}
    </svg>
);

/* Орнамент Sapar — девять ромбов: большой в центре, четыре по сторонам и
   четыре маленьких по диагоналям. Рисуем генератором, иначе в файле лежала бы
   стена одинаковых path. */
const diamond = (cx, cy, r) => `M${cx} ${cy - r}L${cx + r} ${cy}L${cx} ${cy + r}L${cx - r} ${cy}Z`;
const SAPAR_ORNAMENT = [
    [16, 16, 4.6],
    [16, 7.4, 3.4], [16, 24.6, 3.4], [7.4, 16, 3.4], [24.6, 16, 3.4],
    [9.6, 9.6, 2.2], [22.4, 9.6, 2.2], [9.6, 22.4, 2.2], [22.4, 22.4, 2.2],
].map(([x, y, r]) => diamond(x, y, r)).join('');

/** Марка провайдера ЭДО в списке. Цвета сняты пипеткой с настоящих кадров:
 *  ЦНТ #0bbd5f, Payda #2f7ddf, Sapar чёрный, Partners Pay #8c58e8,
 *  Vezunchik.Pro #f8d000. По ним провайдера и узнают. */
export const ProviderMark = ({ mark }) => {
    if (mark === 'cnt') {
        return (
            <svg viewBox="0 0 32 32" aria-hidden="true">
                <path d="M5 7.4h15.6v13.2H5Z" fill="#0bbd5f" />
                <path d="M7.8 11h9.4M7.8 14h9.4M7.8 17h5.6" stroke="#fff" strokeWidth="1.5"
                    strokeLinecap="round" />
                <circle cx="21.6" cy="20.4" r="6.2" fill="#0bbd5f" stroke="#fff" strokeWidth="1.6" />
                <circle cx="21.6" cy="20.4" r="1.9" fill="#fff" />
                <path d="M16.2 20.4h1.6M25.4 20.4H27" stroke="#fff" strokeWidth="1.5"
                    strokeLinecap="round" />
            </svg>
        );
    }
    if (mark === 'payda') {
        return (
            <svg viewBox="0 0 32 32" aria-hidden="true">
                <path d="M11 5.4h9.2l4.4 5.2-4.4 5.2H11Z" fill="#2f7ddf" />
                <path d="M11 5.4v21.2l5.6-5.6V5.4Z" fill="#1d55c6" />
                <path d="M11 15.8h6.4l-6.4 6.4Z" fill="#4a97f0" />
            </svg>
        );
    }
    if (mark === 'sapar') {
        return (
            <svg viewBox="0 0 32 32" aria-hidden="true">
                <path d={SAPAR_ORNAMENT} fill="#000000" />
            </svg>
        );
    }
    if (mark === 'partners') {
        return (
            <svg viewBox="0 0 32 32" aria-hidden="true">
                <path d="M10.6 26.4V8.2a2.8 2.8 0 0 1 2.8-2.8h4.2a7.2 7.2 0 0 1 0 14.4h-7"
                    fill="none" stroke="#8c58e8" strokeWidth="4.6" strokeLinecap="round" />
                <circle cx="17.4" cy="12.6" r="1.5" fill="#c6f24a" />
            </svg>
        );
    }
    if (mark === 'vezunchik') {
        return (
            <svg viewBox="0 0 32 32" aria-hidden="true">
                <rect x="4" y="4" width="24" height="24" rx="5" fill="#f8d000" />
                <path d="M20.4 26V17c0-4.4-2.4-7-6.4-7.6l-1.2-2.2 2.6.5 1.6-1.3.9 2.3c3.9 1.2 6.3 4.4 6.3 8.6V26Z"
                    fill="#151515" />
                <path d="M7.6 24.8h3v-2.6h-3ZM10.6 22.2h3v-2.6h-3ZM7.6 19.6h3V17h-3ZM13.6 19.6h2.8V17h-2.8Z"
                    fill="#151515" />
            </svg>
        );
    }
    /* Бумажный документооборот — не бренд, а отказ от ЭДО: серый лист. */
    return (
        <svg viewBox="0 0 32 32" fill="none" stroke="#6f6f6f" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M9 5h9l5 5v17H9Z" />
            <path d="M18 5v5h5" />
        </svg>
    );
};

/* ── Браузер ─────────────────────────────────────────────────────────────── */

const BROWSER_ICONS = {
    home: <path d="M3.6 11.4 12 4.2l8.4 7.2v8.2a1 1 0 0 1-1 1h-4.6v-5.4H9.2v5.4H4.6a1 1 0 0 1-1-1Z" />,
    /* Значок «сведения о сайте» — в Chrome он теперь такой, а не замок. */
    tune: (
        <>
            <path d="M3.6 8.4h16.8M3.6 15.6h16.8" />
            <circle cx="9" cy="8.4" r="2.1" />
            <circle cx="15" cy="15.6" r="2.1" />
        </>
    ),
    plus: <path d="M12 5.2v13.6M5.2 12h13.6" />,
    refresh: (
        <>
            <path d="M20 12a8 8 0 1 1-2.6-5.9" />
            <path d="M20.4 4.4v4.4H16" />
        </>
    ),
    dots: (
        <>
            <circle cx="12" cy="5" r="1.5" fill="currentColor" stroke="none" />
            <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
            <circle cx="12" cy="19" r="1.5" fill="currentColor" stroke="none" />
        </>
    ),
    mic: (
        <>
            <rect x="9.2" y="3.2" width="5.6" height="10.4" rx="2.8" />
            <path d="M5.6 11.6a6.4 6.4 0 0 0 12.8 0M12 18v2.8" />
        </>
    ),
    search: (
        <>
            <circle cx="11" cy="11" r="6.4" />
            <path d="m15.8 15.8 4.4 4.4" />
        </>
    ),
    link: (
        <>
            <path d="M10.4 13.6a3.6 3.6 0 0 0 5.4.4l2.6-2.6a3.6 3.6 0 0 0-5.1-5.1l-1.5 1.5" />
            <path d="M13.6 10.4a3.6 3.6 0 0 0-5.4-.4l-2.6 2.6a3.6 3.6 0 0 0 5.1 5.1l1.5-1.5" />
        </>
    ),
};

/** Значок в рамке браузера. */
export const BrIcon = ({ name }) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {BROWSER_ICONS[name]}
    </svg>
);

/* ── Домашний экран телефона ─────────────────────────────────────────────── */

/** Значок приложения на домашнем экране. Плитки нарисованы, а не набраны
 *  эмодзи: по ним человек и находит нужное приложение среди чужих. */
export const AppIcon = ({ app }) => {
    if (app === 'taxipro') {
        return (
            <svg viewBox="0 0 48 48" aria-hidden="true">
                <rect width="48" height="48" rx="12" fill="#fce000" />
                <circle cx="24" cy="24" r="12.5" fill="#111" />
                <path d="M24 16.5 31 30H17Z" fill="#fce000" />
            </svg>
        );
    }
    if (app === 'egov') {
        return (
            <svg viewBox="0 0 48 48" aria-hidden="true">
                <rect width="48" height="48" rx="12" fill="#2f5bd8" />
                <text x="24" y="27" textAnchor="middle" fontSize="15" fontWeight="700" fill="#fff"
                    fontFamily="Helvetica, Arial, sans-serif">egov</text>
                <text x="24" y="37" textAnchor="middle" fontSize="7.5" fontWeight="600"
                    fill="#c7d6ff" fontFamily="Helvetica, Arial, sans-serif">mobile</text>
            </svg>
        );
    }
    if (app === 'chrome') {
        return (
            <svg viewBox="0 0 48 48" aria-hidden="true">
                <circle cx="24" cy="24" r="20" fill="#e8eaed" />
                <path d="M24 4a20 20 0 0 1 17.3 10H24a10 10 0 0 0-8.7 5L7 9.3A20 20 0 0 1 24 4Z" fill="#ea4335" />
                <path d="M7 9.3 15.3 19A10 10 0 0 0 20 32.6l-7.3 12.1A20 20 0 0 1 7 9.3Z" fill="#34a853" />
                <path d="M41.3 14a20 20 0 0 1-16.6 30l7.7-13.3A10 10 0 0 0 34 14Z" fill="#fbbc05" />
                <circle cx="24" cy="24" r="8.2" fill="#fff" />
                <circle cx="24" cy="24" r="6" fill="#4285f4" />
            </svg>
        );
    }
    if (app === 'chat') {
        return (
            <svg viewBox="0 0 48 48" aria-hidden="true">
                <rect width="48" height="48" rx="12" fill="#25d366" />
                <path d="M24 11a13 13 0 0 0-11.2 19.6L11 37l6.6-1.7A13 13 0 1 0 24 11Z" fill="#fff" />
                <path d="M19.4 18.6c.4-.9.8-.9 1.2-.9h1c.3 0 .8 0 1.1.8l1.2 2.9c.2.4 0 .8-.2 1l-.8.9c-.2.2-.3.5-.1.8a10 10 0 0 0 4.6 4c.4.2.7.1.9-.1l1-1.1c.3-.3.6-.3.9-.2l2.7 1.3c.4.2.6.4.6.8v1.2c0 .5-.4 1.1-1 1.4a4 4 0 0 1-2.4.5c-1.4-.2-4.4-1-7.4-4a17 17 0 0 1-3.6-5.6 4 4 0 0 1 .8-3.7Z" fill="#25d366" />
            </svg>
        );
    }
    return (
        <svg viewBox="0 0 48 48" aria-hidden="true">
            <rect width="48" height="48" rx="12" fill="#8e8e93" />
            <g fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round">
                <circle cx="24" cy="24" r="5.4" />
                <path d="M24 12.6v3.2M24 32.2v3.2M35.4 24h-3.2M15.8 24h-3.2M32.1 15.9l-2.3 2.3M18.2 29.8l-2.3 2.3M32.1 32.1l-2.3-2.3M18.2 18.2l-2.3-2.3" />
            </g>
        </svg>
    );
};
