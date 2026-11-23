import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { revealInFrame } from './crmReveal';

/* Учебная CRM в своём документе — iframe, в который React рисует порталом.
 *
 * ЗАЧЕМ. Учебная CRM обязана выглядеть ОДИН В ОДИН с рабочей, поэтому она
 * рисуется настоящими стилями CRM (public/trainers/crm/admin.css: Bootstrap,
 * CoreUI, vue-multiselect и правки CRM) и настоящей разметкой. Пустить эти
 * стили в документ портала нельзя: они глобальные (body, h1…h6, .card,
 * .btn…) и переписали бы весь портал, а стили портала (Tailwind, тёмная тема)
 * — учебную CRM. В отдельном документе они не встречаются вовсе.
 *
 * ПОЧЕМУ ПОРТАЛ, а не отдельное приложение во фрейме: состояние CRM — часть
 * попытки (мир движка), подсветка цели и нажатия идут через тот же движок,
 * что во всех тренажёрах. React 18 слушает события на контейнере портала,
 * поэтому клики внутри фрейма доходят до тех же обработчиков.
 *
 * ШИРИНА. CRM рисуется в ширину экрана оператора (1024–1240 px, см.
 * frameWidth): уже этого карточка водителя и таблицы CRM сжимаются так, как
 * на смене их не видят, а меню слева CRM прячет и вовсе от 992 px. Окно
 * тренажёра уже — фрейм уменьшается масштабом, а не перестраивается.
 *
 * Без DOM (тесты экранов, renderToStaticMarkup) фрейма нет — содержимое
 * рисуется прямо в разметку, чтобы тест видел кнопки и подсветку.
 */

/* Стили CRM лежат в public/ — рядом с сайтом, как модель машины фотоконтроля
   (CarStage): адрес от корня сборки, иначе под VITE_BASE_PATH они пропадут. */
const BASE = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.BASE_URL) || '/';
const CSS_URL = `${BASE}trainers/crm/admin.css`;
const WIDE = 1240;
const NARROW = 1024;
const SCALE = 0.82;

/* Ширина, в которой рисуется CRM, для места шириной width. Шире 1240 —
   как есть. Уже — CRM рисуется шире места и уменьшается, но не мельче
   ~0,82: на ноутбуке 1366 px это 1024–1240 px CRM, а не телефонная вёрстка.
   Переход плавный — окно тренажёра тянут мышью, и скачков быть не должно. */
export const frameWidth = (width) => (width >= WIDE ? width
    : Math.min(WIDE, Math.max(NARROW, width / SCALE)));

/* Свои правила внутри фрейма: подсветка цели движка (та же, что на рабочем
   месте, см. .wt-desk .is-target в trainer.css) и плашка учебной среды. */
const FRAME_CSS = `
.is-target {
    outline: 3px solid #e11d48 !important;
    outline-offset: 2px;
    animation: wt-target 1.6s ease-in-out infinite;
}
@keyframes wt-target {
    0%, 100% { box-shadow: 0 0 0 0 rgba(225, 29, 72, .45); }
    60% { box-shadow: 0 0 0 9px rgba(225, 29, 72, 0); }
}
.wt-crm-mark {
    position: fixed;
    z-index: 1031;
    top: 15px;
    left: 50%;
    transform: translateX(-50%);
    padding: 3px 9px;
    border: 1px solid rgba(180, 120, 20, .28);
    border-radius: 999px;
    background: rgba(254, 243, 199, .94);
    font: 700 9px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    letter-spacing: .05em;
    text-transform: uppercase;
    color: #7c4a03;
    pointer-events: none;
}
/* CRM во фрейме уже экрана оператора, и в шапке карточки водителя (три
   колонки) подпись и значение не влезают в строку: «Физическое лицо»
   наезжало на «Дату создания» соседней колонки. Тогда значение уходит
   строкой ниже, вправо. Где место есть, строка как в рабочей CRM. */
.detail-row:not(.detail-row--text):not(.detail-row--action) { flex-wrap: wrap; }
.detail-row:not(.detail-row--text):not(.detail-row--action) > .detail-row__value { margin-left: auto; }
`;

const SRC_DOC = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<link rel="stylesheet" href="${CSS_URL}"><style>${FRAME_CSS}</style></head>
<body class="app header-fixed sidebar-fixed sidebar-lg-show"></body></html>`;

/** Размер места под фрейм — от него считается масштаб. */
const useBox = (ref) => {
    const [box, setBox] = useState({ width: 0, height: 0 });
    useEffect(() => {
        const node = ref.current;
        if (!node || typeof ResizeObserver === 'undefined') return undefined;
        const observer = new ResizeObserver(([entry]) => {
            const { width, height } = entry.contentRect;
            setBox((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
        });
        observer.observe(node);
        return () => observer.disconnect();
    }, [ref]);
    return box;
};

export default function CrmFrame({
    children, hidden = false, title = 'Учебная CRM', onDocument = null, focusKey = '',
}) {
    const wrapRef = useRef(null);
    const [doc, setDoc] = useState(null);
    const box = useBox(wrapRef);

    /* Новая цель урока — CRM сама прокручивает к ней. Подсветка за краем
       окна — всё равно что её нет: таблица учёток шире экрана (в рабочей CRM
       она прокручивается вбок), «Сохранить» — ниже формы. Только при СМЕНЕ
       цели: прокрутку, которую человек сделал сам, не перебиваем. Крутим
       только внутри CRM (crmReveal.js), не страницу вокруг. */
    useEffect(() => {
        if (!doc || hidden || !focusKey) return undefined;
        const frame = requestAnimationFrame(() => revealInFrame(doc, doc.querySelector('.is-target')));
        return () => cancelAnimationFrame(frame);
    }, [doc, hidden, focusKey]);

    /* Esc внутри фрейма до документа портала не доходит, а тренажёр
       закрывается именно по Esc — пересылаем. */
    useEffect(() => {
        if (!doc) return undefined;
        const onKey = (event) => {
            if (event.key === 'Escape') document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        };
        doc.addEventListener('keydown', onKey);
        if (onDocument) onDocument(doc);
        return () => doc.removeEventListener('keydown', onKey);
    }, [doc, onDocument]);

    if (typeof document === 'undefined') {
        return <div className="wt-crmframe wt-crmframe--inline">{children}</div>;
    }

    const scale = box.width ? box.width / frameWidth(box.width) : 1;
    const style = {
        width: box.width ? box.width / scale : '100%',
        height: box.height ? box.height / scale : '100%',
        transform: scale === 1 ? 'none' : `scale(${scale})`,
    };

    return (
        <div className="wt-crmframe" ref={wrapRef} style={hidden ? { display: 'none' } : undefined}>
            <iframe
                title={title}
                srcDoc={SRC_DOC}
                style={style}
                /* Документ готов, когда загрузились стили CRM: событие load
                   ждёт таблиц стилей, и до него CRM без стилей не мелькает. */
                onLoad={(event) => setDoc(event.currentTarget.contentDocument)}
            />
            {doc ? createPortal(children, doc.body) : null}
        </div>
    );
}
