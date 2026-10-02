import {
    curlGeometry, easeInOut, easeOut, matrixCss, polygonCss, tapPath,
} from './pageCurl.js';
import { currentTocIndex, pageAtFraction } from './libraryMeta.js';

/*
 * Движок страниц ридера «Библиотеки»: книга, которую листают.
 *
 * ДВА ВИДА. На телефоне и в узком окне — одна страница (как Apple Books на
 * iPhone). На широком экране — открытая книга, разворот из двух страниц: при
 * перелистывании правая страница переворачивается через корешок, и на её
 * обороте уже видна следующая левая — как у настоящего листа.
 *
 * СТРАНИЦА — ВИД НА ГЛАВУ. Каждая страница (PageView) — лист бумаги с
 * колонтитулом сверху, номером снизу и теневым DOM с главой, разложенной в
 * колонки ширины страницы; номер экрана — сдвиг колонок. Страниц четыре: на
 * развороте две видны, две заранее держат следующий разворот; ход вперёд
 * загибает правую, её оборот показывает следующую левую (зеркало внутри
 * отражения — текст на обороте читается как надо), под ней — следующая
 * правая. На одной странице оборот — сама бумага, сквозь которую чуть
 * просвечивает текст (копия того же листа).
 *
 * ОБОРОТ НЕ ПЕРЕКЛАДЫВАЕТСЯ В DOM. Страница оборота остаётся на своём месте, а
 * загиб — это её transform и clip-path. Перенос элемента с целой главой в
 * другой контейнер заставлял браузер заново верстать главу дважды за ход:
 * на книге одним файлом это секунды заморозки на каждое перелистывание.
 *
 * ГЛАВА НАЧИНАЕТСЯ С НОВОЙ СТРАНИЦЫ, на развороте — с левой: если глава
 * кончилась на левой странице, правая остаётся чистой, как в печатной книге.
 *
 * НОМЕРА СТРАНИЦ — НАСТОЯЩИЕ, как в Apple Books: вёрстка всей книги под этот
 * экран считается в фоне, и номер внизу страницы, «из M» и номера в
 * оглавлении — одни и те же. Процент прочтения и мониторинг от этого не
 * зависят: они по-прежнему от 1800-знаковых страниц сервера (libraryMeta.js).
 * «Глава» — по пунктам оглавления, а не по файлам книги: в книге одним файлом
 * файл — это вся книга.
 *
 * ОБЁРТКИ — СВОИ ЭЛЕМЕНТЫ (lr-clip, lr-flow, lr-body), а не div: правило
 * книги вида «div { margin: 0 5% }» иначе сдвигало бы колонки движка.
 */

const FONT = 'ui-serif, "New York", "PT Serif", Georgia, "Times New Roman", serif';

/* Бумага и краски — днём и ночью. Бумага тёплая: белый экран утомляет глаза
   сильнее книжной страницы, и текст на нём выглядит документом, а не книгой. */
export const PALETTES = {
    day: {
        paper: '#fcfaf5', back: '#f2eee5', desk: '#eceae5', ink: '#24211d', soft: '#9a9285',
        link: '#2f5d9b', rule: '#cfc6b6', selection: 'rgba(47, 93, 155, 0.20)', edge: '#e4ddcf', gloss: 0.55,
        night: false,
    },
    night: {
        paper: '#1d1c1a', back: '#282622', desk: '#0e0e0d', ink: '#ddd8cf', soft: '#7f786d',
        link: '#8fb4e6', rule: '#46423b', selection: 'rgba(143, 180, 230, 0.28)', edge: '#2c2a26', gloss: 0.10,
        night: true,
    },
};

/*
 * Стиль страницы. Он стоит в теневом корне ПОСЛЕ стиля книги: при равной
 * специфичности побеждает он (книжное «p { margin: 1em 0 }» уступает
 * книжному абзацу ридера), а правила книги с классами (.center, .epigraph)
 * — сильнее и остаются за книгой. Поэтому типографика здесь почти вся с
 * нулевой специфичностью (:where), а !important — только там, где книга не
 * вправе спорить (шрифт, служебные обёртки, размеры).
 */
const BASE_CSS = `
:host {
  all: initial;
  /* all: initial сбросил бы и то, что лист наследует от ридера: скрытый лист
     проступал бы для поиска и читалки, а на телефоне долгое нажатие начинало
     выделение вместо перелистывания. */
  visibility: inherit; -webkit-user-select: inherit; user-select: inherit;
  -webkit-touch-callout: inherit;
}
lr-clip { display: block; position: absolute; inset: 0; overflow: hidden; overflow: clip; }
lr-flow {
  display: block; position: absolute; left: 0; top: 0; margin: 0; padding: 0; border: 0;
  width: var(--w); height: var(--h);
  column-width: var(--w); column-gap: var(--gap); column-fill: auto;
  font-family: ${FONT}; font-size: var(--fs); line-height: 1.56; color: var(--ink);
  text-align: justify; -webkit-hyphens: auto; hyphens: auto; overflow-wrap: break-word;
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
  font-kerning: normal; font-feature-settings: "kern", "liga";
  widows: 2; orphans: 2; will-change: transform;
  /* Последняя строка абзаца — не из одного слова (где браузер это умеет). */
  text-wrap: pretty;
}
lr-flow > .lr-body {
  display: block; margin: 0 !important; padding: 0 !important; border: 0 !important;
  width: auto !important; max-width: none !important; min-height: 0 !important;
  background: none !important; color: inherit !important; font-size: inherit !important;
  line-height: inherit !important; columns: auto !important; column-count: auto !important;
  position: static !important; float: none !important;
}
/* Начало главы — без разрыва перед ним (загрузчик помечает, см. markLead):
   иначе WebKit начинает главу с пустой страницы. */
.lr-body .lr-lead { break-before: auto !important; page-break-before: auto !important; -webkit-column-break-before: auto !important; }
.lr-body .lr-lead-empty { break-after: auto !important; page-break-after: auto !important; -webkit-column-break-after: auto !important; }
/* Один шрифт на всю книгу: книги задают свои, а их на устройстве обычно нет,
   и страница собиралась бы из трёх гарнитур. Моноширинный код не трогаем. */
.lr-body :where(*):not(pre, code, kbd, samp, tt, pre *, code *) { font-family: inherit !important; }
/* Ничто не шире колонки: широкая таблица или длинный адрес иначе ложились бы
   поверх текста следующей страницы. */
.lr-body :where(*) { max-width: 100% !important; box-sizing: border-box; }
.lr-body :where(td, th, pre, code) { overflow-wrap: anywhere; word-break: break-word; }
.lr-body table { table-layout: fixed; width: 100% !important; border-collapse: collapse; }

/* Книжный абзац: красная строка и никаких пустых промежутков между абзацами. */
:where(.lr-body) p { margin-top: 0; margin-bottom: 0; text-indent: 1.5em; }
:where(.lr-body) :is(h1, h2, h3, h4, h5, h6, hr, img, figure, table, blockquote, ul, ol) + p,
:where(.lr-body) p:first-child { text-indent: 0; }
/* Выровненные по центру и вправо строки (заглавия, подписи, эпиграфы) — без
   красной строки: отступ сдвинул бы их с оси. */
:where(.lr-body) :is(p[class*="center" i], p[class*="right" i], p[align], p[style*="text-align" i]) { text-indent: 0; }
/* Строки, разорванные переносом <br>, по ширине не растягиваем. */
:where(.lr-body) :is(p, div):has(> br) { text-align: start; }
/* Стихи — без красной строки: там каждая строка абзацем. */
:where(.lr-body) :is([class*="poem" i], [class*="verse" i], [class*="stanza" i], [class*="stih" i]) p { text-indent: 0; }

:where(.lr-body) :where(h1, h2, h3, h4, h5, h6) {
  text-align: center; -webkit-hyphens: manual; hyphens: manual; line-height: 1.25;
  break-after: avoid; font-weight: 600; margin: 1.3em 0 1.05em; letter-spacing: 0.01em;
}
:where(.lr-body) :where(h1) { font-size: 1.5em; }
:where(.lr-body) :where(h2) { font-size: 1.3em; }
:where(.lr-body) :where(h3) { font-size: 1.12em; }
/* Глава начинается ниже верхнего края, как в печатной книге. */
.lr-body > :is(h1, h2, h3):first-child,
.lr-body > :first-child > :is(h1, h2, h3):first-child { margin-top: calc(var(--h) * 0.13) !important; }
/* Буквица — у первого абзаца главы, если он начинается с буквы (загрузчик
   помечает такой абзац: тире диалога или подпись под заголовком буквицей
   становиться не должны). */
.lr-body p.lr-dropcap { text-indent: 0 !important; }
.lr-body p.lr-dropcap::first-letter {
  float: left; font-size: 3.15em; line-height: 0.86; padding: 0.07em 0.08em 0 0; font-weight: 500;
}
.lr-body img, .lr-body svg, .lr-body video {
  max-height: calc(var(--h) - 8px) !important; height: auto; object-fit: contain; break-inside: avoid;
}
:where(.lr-body) img { vertical-align: middle; }
/* Отдельная картинка — по центру своей строкой; картинка внутри фразы
   остаётся в строке (загрузчик отличает одну от другой). */
.lr-body img.lr-img-block { display: block; margin: 0.9em auto; }
/* Потянуть лист за край поверх картинки — это перелистывание, а не
   перетаскивание картинки в другое окно. */
.lr-body img, .lr-body svg, .lr-body a { -webkit-user-drag: none; }
.lr-body a, .lr-body [data-lr-href], .lr-body [data-lr-ext] { color: var(--link); text-decoration: none; cursor: pointer; }
:where(.lr-body) blockquote { margin: 0.8em 1.4em; font-size: 0.96em; }
.lr-body pre { white-space: pre-wrap; font-size: 0.85em; }
/* Разделитель сцен — звёздочки, а не линия. */
.lr-body hr { border: 0; height: auto; margin: 1.1em 0; text-align: center; overflow: visible; color: var(--soft); }
.lr-body hr::after { content: "⁂"; font-size: 1.05em; letter-spacing: 0.3em; }
.lr-body sup, .lr-body sub { line-height: 0; }
.lr-body ::selection { background: var(--selection); }
/* Ночью цвета книги не спорят с бумагой: чёрный текст из Word/Calibre на
   тёмной бумаге был бы невидим. */
lr-flow[data-night] .lr-body :where(*:not(a, [data-lr-href], [data-lr-ext], svg, svg *)) {
  color: inherit !important; background-color: transparent !important; border-color: var(--rule) !important;
}
lr-flow[data-night] .lr-body :where(a, [data-lr-href], [data-lr-ext]) { color: var(--link) !important; }
`;

const IMAGE_WAIT_MS = 2500;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

const waitImages = (root) => Promise.all(Array.from(root.querySelectorAll('img')).map((img) => (
    img.complete ? null : new Promise((resolve) => {
        const done = () => resolve();
        img.addEventListener('load', done, { once: true });
        img.addEventListener('error', done, { once: true });
        setTimeout(done, IMAGE_WAIT_MS);
    })
)));

const idle = () => new Promise((resolve) => {
    if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
        window.requestIdleCallback(() => resolve(), { timeout: 120 });
    } else {
        setTimeout(resolve, 16);
    }
});
const tick = () => new Promise((resolve) => { setTimeout(resolve, 0); });

/*
 * Размер книги под сцену.
 *   phone  — одна страница во весь экран между панелями;
 *   single — одна страница посередине стола (узкое окно компьютера);
 *   spread — разворот, если на экран влезают две страницы по 400+ точек.
 * Поля — доля ширины страницы: у маленькой страницы поля в 60 точек съедали
 * бы треть строки. Ширина набора у всех страниц книги одна и та же — иначе
 * колонки разных страниц разбивали бы главу по-разному.
 */
/* Кегль — от ширины страницы: в строке книги 45–65 знаков. На развороте
   ноутбука 1366×768 страница ~450 точек, и при 18 px строка сжималась до
   сорока знаков: короткие строки по ширине шли «дырами». */
const fontFor = (pageWidth) => (pageWidth >= 520 ? 19 : pageWidth >= 470 ? 18 : 17);

export const bookMetrics = (stageWidth, stageHeight, narrow) => {
    if (narrow) {
        return {
            mode: 'single', narrow: true,
            left: 0, top: 0, width: stageWidth, height: stageHeight, pageWidth: stageWidth,
            pad: { top: 40, bottom: 40, inner: 24, outer: 24 }, fontSize: 18,
        };
    }
    const height = Math.max(360, stageHeight - 44);
    const vertical = clamp(Math.round(height * 0.07), 40, 58);
    const spreadPage = Math.min(Math.max(Math.round(height * 0.7), 400), Math.floor((stageWidth - 120) / 2), 620);
    if (spreadPage >= 400) {
        return {
            mode: 'spread', narrow: false,
            left: Math.round((stageWidth - spreadPage * 2) / 2),
            top: Math.round((stageHeight - height) / 2),
            width: spreadPage * 2, height, pageWidth: spreadPage,
            pad: {
                top: vertical, bottom: vertical,
                inner: clamp(Math.round(spreadPage * 0.085), 28, 50),
                outer: clamp(Math.round(spreadPage * 0.1), 32, 60),
            },
            fontSize: fontFor(spreadPage),
        };
    }
    // Одна страница: не уже 460 точек, даже если экран низкий, — иначе в
    // строке 26–29 знаков, и выключка по ширине рвёт её «реками».
    const pageWidth = Math.max(300, Math.min(stageWidth - 140, 680, Math.max(Math.round(height * 0.72), 460)));
    const side = clamp(Math.round(pageWidth * 0.1), 32, 56);
    return {
        mode: 'single', narrow: false,
        left: Math.round((stageWidth - pageWidth) / 2),
        top: Math.round((stageHeight - height) / 2),
        width: pageWidth, height, pageWidth,
        pad: { top: vertical, bottom: vertical, inner: side, outer: side },
        fontSize: fontFor(pageWidth),
    };
};

const css = (element, styles) => { Object.assign(element.style, styles); return element; };
const make = (tag, styles = {}) => css(document.createElement(tag), styles);
const setClip = (element, value) => { element.style.clipPath = value; element.style.webkitClipPath = value; };

/* Одна страница книги: бумага, колонтитул, текст главы, номер. */
class PageView {
    constructor(palette) {
        this.el = make('div', {
            position: 'absolute', top: '0', left: '0', overflow: 'hidden', contain: 'layout paint',
            visibility: 'hidden', willChange: 'transform, clip-path', transformOrigin: '0 0',
        });
        this.head = make('div', {
            position: 'absolute', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            textAlign: 'center', fontFamily: FONT, fontSize: '10.5px', letterSpacing: '0.14em',
            textTransform: 'uppercase', pointerEvents: 'none', lineHeight: '1',
        });
        this.host = make('div', { position: 'absolute' });
        this.folio = make('div', {
            position: 'absolute', textAlign: 'center', fontFamily: FONT, fontSize: '12.5px',
            fontVariantNumeric: 'tabular-nums', pointerEvents: 'none', lineHeight: '1',
        });
        this.el.append(this.head, this.host, this.folio);
        this.shadow = this.host.attachShadow({ mode: 'open' });
        // Стиль книги — ПЕРВЫМ, стиль страницы — после него (см. BASE_CSS).
        this.shadow.innerHTML = `<style data-book></style><style>${BASE_CSS}</style>`
            + '<lr-clip><lr-flow><lr-body class="lr-body"></lr-body></lr-flow></lr-clip>';
        this.bookStyle = this.shadow.querySelector('style[data-book]');
        this.clip = this.shadow.querySelector('lr-clip');
        this.flow = this.shadow.querySelector('lr-flow');
        this.body = this.shadow.querySelector('lr-body');
        // Поиск по странице (Cmd+F) и фокус умеют прокрутить обрезку — и все
        // следующие страницы съехали бы на полколонки. Где overflow: clip не
        // понимают (старый Safari), прокрутку возвращаем.
        this.clip.addEventListener('scroll', () => { this.clip.scrollLeft = 0; this.clip.scrollTop = 0; });
        this.section = -1;
        this.want = -1;
        this.pages = 1;
        this.page = 0;
        this.stride = 1;
        this.blank = false;
        this.side = 'single';
        this.ready = Promise.resolve();
        this.applyPalette(palette);
    }

    applyPalette(palette) {
        this.palette = palette;
        Object.entries({ '--ink': palette.ink, '--link': palette.link, '--rule': palette.rule, '--selection': palette.selection, '--soft': palette.soft })
            .forEach(([name, value]) => this.flow.style.setProperty(name, value));
        this.flow.toggleAttribute('data-night', Boolean(palette.night));
        this.head.style.color = palette.soft;
        this.folio.style.color = palette.soft;
        this.paintPaper();
    }

    /* Бумага с тенью корешка: у левой страницы разворота — справа, у правой —
       слева, у одиночной страницы на столе — едва заметно слева. */
    paintPaper() {
        const { paper } = this.palette;
        const gutter = {
            left: `linear-gradient(270deg, rgba(0,0,0,0.085), rgba(0,0,0,0.02) 18px, rgba(0,0,0,0) 44px), ${paper}`,
            right: `linear-gradient(90deg, rgba(0,0,0,0.085), rgba(0,0,0,0.02) 18px, rgba(0,0,0,0) 44px), ${paper}`,
            single: this.narrow ? paper : `linear-gradient(90deg, rgba(0,0,0,0.045), rgba(0,0,0,0) 30px), ${paper}`,
        };
        this.el.style.background = gutter[this.side] || paper;
    }

    applyMetrics(metrics) {
        this.metrics = metrics;
        this.narrow = metrics.narrow;
        const { pad, pageWidth, height } = metrics;
        const w = pageWidth - pad.inner - pad.outer;
        const h = height - pad.top - pad.bottom;
        css(this.el, { width: `${pageWidth}px`, height: `${height}px` });
        css(this.host, { top: `${pad.top}px`, width: `${w}px`, height: `${h}px` });
        css(this.head, { top: `${Math.round(pad.top * 0.42)}px`, width: `${w}px` });
        css(this.folio, { bottom: `${Math.round(pad.bottom * 0.36)}px`, width: `${w}px` });
        this.flow.style.setProperty('--w', `${w}px`);
        this.flow.style.setProperty('--h', `${h}px`);
        this.flow.style.setProperty('--gap', `${pad.inner + pad.outer}px`);
        this.flow.style.setProperty('--fs', `${metrics.fontSize}px`);
        this.gap = pad.inner + pad.outer;
        this.stride = w + this.gap;
        this.setSide(this.side);
        if (this.section >= 0) this.measure();
    }

    /* Где лежит страница: left/right — на развороте, single — одна. От стороны
       зависят поля (у корешка уже) и тень корешка. */
    setSide(side, x = null) {
        this.side = side;
        const m = this.metrics;
        if (m) {
            const offset = side === 'right' ? m.pad.inner : m.pad.outer;
            css(this.host, { left: `${offset}px` });
            css(this.head, { left: `${offset}px` });
            css(this.folio, { left: `${offset}px` });
            this.el.style.left = `${x === null ? (side === 'right' ? m.pageWidth : 0) : x}px`;
        }
        this.paintPaper();
    }

    measure() {
        // Колонки уходят вправо за ширину набора: сколько их — столько страниц.
        const total = this.flow.scrollWidth + (this.gap || 0);
        this.pages = Math.max(1, Math.round(total / this.stride));
        this.setPage(Math.min(this.page, this.pages - 1));
    }

    /* Положить главу. Страница «готова» (ready) только после картинок и
       разметки: до того число экранов у неё от прошлой главы. */
    setSection(index, content, lang) {
        this.blank = false;
        this.host.style.visibility = '';
        this.bookStyle.textContent = content.css || '';
        this.body.replaceChildren(content.fragment ? content.fragment.cloneNode(true) : document.createTextNode(''));
        this.body.setAttribute('lang', content.lang || lang || 'ru');
        this.section = index;
        this.page = 0;
        this.ready = waitImages(this.body).then(() => this.measure());
        return this.ready;
    }

    /* Освободить память: глава в скрытой странице-замерщике больше не нужна. */
    clear() {
        this.bookStyle.textContent = '';
        this.body.replaceChildren();
        this.section = -1;
        this.want = -1;
    }

    setPage(page) {
        this.page = Math.max(0, Math.min(this.pages - 1, page));
        this.flow.style.transform = `translate3d(${-this.page * this.stride}px, 0, 0)`;
    }

    /* Чистая страница — конец главы на левой странице разворота. */
    setBlank() {
        this.blank = true;
        this.host.style.visibility = 'hidden';
        this.head.textContent = '';
        this.folio.textContent = '';
    }

    setFurniture(head, folio) {
        this.head.textContent = this.blank ? '' : (head || '');
        this.folio.textContent = this.blank || !folio ? '' : String(folio);
    }

    pageOfAnchor(anchor) {
        if (!anchor) return 0;
        let target = null;
        try {
            target = this.shadow.getElementById(anchor)
                || this.shadow.querySelector(`[name="${CSS.escape(anchor)}"]`);
        } catch { target = null; }
        if (!target) return 0;
        const x = target.getBoundingClientRect().left - this.flow.getBoundingClientRect().left;
        return Math.max(0, Math.min(this.pages - 1, Math.floor((x + 2) / this.stride)));
    }
}

export class ReaderEngine {
    /*
     * container — пустой элемент сцены, всё внутри рисует движок.
     * book — описание книги с сервера (spine со знаками, оглавление).
     * onPosition(place) — новое место; onPagination(state) — посчитаны номера.
     */
    constructor({
        loader, book, container, lang, onPosition, onPagination, cacheKey = '',
        reducedMotion = false, palette = PALETTES.day,
    }) {
        this.loader = loader;
        this.book = book;
        this.container = container;
        this.lang = lang;
        this.onPosition = onPosition;
        this.onPagination = onPagination;
        this.cacheKey = cacheKey;
        this.reducedMotion = reducedMotion;
        this.palette = palette;
        this.destroyed = false;
        this.turn = null;
        this.preparing = false;
        this.held = false;           // книга раскрывается или закрывается
        this.closing = false;        // книгу закрывают: ни ходов, ни переходов
        // Книга легла на место (первый emit): раскрывать можно только её —
        // открытие, прерванное сменой размера, повторяется и сдержит обещание.
        this.placed = new Promise((resolve) => { this.markPlaced = resolve; });
        this.pendingLayout = null;
        this.pendingJump = null;
        this.layoutGen = 0;
        this.raf = 0;
        this.token = 0;
        this.metrics = null;
        this.pos = null;            // {section, page}: одна страница — она; разворот — левая
        this.sectionPages = new Map();
        this.pagination = null;
        this.tocBySpine = new Map();
        (Array.isArray(book?.toc) ? book.toc : []).forEach((item, index) => {
            if (!this.tocBySpine.has(item.spine)) this.tocBySpine.set(item.spine, []);
            this.tocBySpine.get(item.spine).push(index);
        });

        this.buildDom();
        this.views = [0, 1, 2, 3].map(() => new PageView(palette));
        this.views.forEach((view) => this.clipper.append(view.el));
        this.roles = { left: null, right: null, single: null };
        this.measurer = new PageView(palette);
        this.offscreen.append(this.measurer.el);
    }

    buildDom() {
        const p = this.palette;
        this.bookEl = make('div', { position: 'absolute' });
        // Толщина книги: стопки страниц у внешних краёв разворота — слева
        // прочитанное, справа оставшееся.
        this.edgeLeft = make('div', { position: 'absolute', top: '2px', bottom: '2px', right: '100%', pointerEvents: 'none' });
        this.edgeRight = make('div', { position: 'absolute', top: '2px', bottom: '2px', left: '100%', pointerEvents: 'none' });
        this.clipper = make('div', { position: 'absolute', inset: '0', overflow: 'hidden' });
        this.shade = make('div', { position: 'absolute', top: '0', pointerEvents: 'none', zIndex: '2', visibility: 'hidden' });
        // Оборот листа — три слоя одной формы: бумага (с тенью от всего
        // листа), страница оборота (сама PageView, см. paintCurl) и свет.
        this.flapShadow = make('div', { position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '4', visibility: 'hidden' });
        this.flapPaper = make('div', { position: 'absolute', top: '0', transformOrigin: '0 0', background: p.back, willChange: 'transform, clip-path' });
        this.flapShadow.append(this.flapPaper);
        this.flapGloss = make('div', {
            position: 'absolute', top: '0', transformOrigin: '0 0', pointerEvents: 'none', zIndex: '6',
            visibility: 'hidden', willChange: 'transform, clip-path',
        });
        // Свет на листе, который при раскрытии книги ложится налево (см.
        // openingParts): поворачивается вместе с левой страницей.
        this.leafShade = make('div', {
            position: 'absolute', top: '0', left: '0', pointerEvents: 'none', zIndex: '4',
            visibility: 'hidden', transformOrigin: '100% 50%',
            background: 'linear-gradient(90deg, rgba(0,0,0,0.16), rgba(0,0,0,0.34))',
        });
        this.clipper.append(this.shade, this.flapShadow, this.flapGloss, this.leafShade);
        // Тень книги на столе — своим слоем, а не box-shadow самой книги: при
        // раскрытии она растёт вслед за ложащимся листом (transform, без
        // перерисовки тени на каждом кадре).
        this.shadowEl = make('div', { position: 'absolute', inset: '0', pointerEvents: 'none', transformOrigin: '100% 50%' });
        this.bookEl.append(this.shadowEl, this.edgeLeft, this.edgeRight, this.clipper);
        this.offscreen = make('div', { position: 'absolute', left: '-20000px', top: '0', visibility: 'hidden', pointerEvents: 'none' });
        this.container.append(this.bookEl, this.offscreen);
    }

    get spread() { return this.metrics?.mode === 'spread'; }

    /* ---------- главы и порядок ---------- */

    isLinear(index) { return Boolean(this.loader.spine[index]?.linear); }

    linearAfter(index) {
        const { spine } = this.loader;
        for (let i = index + 1; i < spine.length; i += 1) if (spine[i].linear) return i;
        return -1;
    }

    linearBefore(index) {
        const { spine } = this.loader;
        for (let i = index - 1; i >= 0; i -= 1) if (spine[i].linear) return i;
        return -1;
    }

    /* Страница-обложка (epubLoader.js: findCoverPage) — не страница книги:
       обложку показывает закрытая книга (LibraryReader). */
    isCover(index) { return Boolean(this.loader.spine[index]?.cover); }

    /* С чего открывать новую книгу: первая линейная глава с ТЕКСТОМ. Страница-
       обложка в счёт не идёт вовсе (она нелинейная); текст первым — на случай
       обложки, которую распознать не удалось: картинка без единого знака. */
    firstLinear() {
        const chars = (index) => Number(this.book?.spine?.[index]?.chars) || 0;
        const withText = this.loader.spine.findIndex((item, index) => item.linear && chars(index) > 0);
        if (withText >= 0) return withText;
        const index = this.loader.spine.findIndex((item) => item.linear);
        return index >= 0 ? index : 0;
    }

    /* Положить главу в страницу. Последний запрос побеждает: заранее начатая
       подготовка следующей страницы не ляжет поверх главы, которую страница
       уже готовит для хода назад. -> true, когда глава на месте и размечена. */
    async fill(view, section) {
        view.want = section;
        if (view.section !== section || view.blank) {
            const content = await this.loader.loadSection(section);
            if (this.destroyed || view.want !== section) return false;
            if (view.section !== section || view.blank) view.setSection(section, content, this.lang);
        }
        await view.ready;
        if (!this.destroyed && view.section === section && view !== this.measurer) this.sectionPages.set(section, view.pages);
        return !this.destroyed && view.want === section && view.section === section;
    }

    /* Показать в странице экран {section, page} или чистый лист (null). */
    async show(view, place) {
        if (!place) {
            view.want = -1;
            view.setBlank();
            return true;
        }
        if (!await this.fill(view, place.section)) return false;
        view.setPage(place.page);
        this.furnish(view);
        return true;
    }

    /* ---------- номера, колонтитулы, главы ---------- */

    /* Номер экрана в книге (с единицы) или null, пока вёрстка не досчитана. */
    folioOf(section, page) {
        const start = this.pagination?.start?.get(section);
        return Number.isFinite(start) ? start + page + 1 : null;
    }

    /* Пункты оглавления внутри главы-файла: [[экран, номер пункта]], по
       порядку экранов. Есть только там, где вёрстка уже прошла. */
    anchorsOf(section) {
        return this.pagination?.anchors?.get(section) || null;
    }

    /* Глава (пункт оглавления), на экране {section, page}, и экран, где
       начнётся следующая. null — вёрстка этого файла ещё не посчитана. */
    chapterAt(section, page) {
        const anchors = this.anchorsOf(section);
        if (!anchors) return null;
        let index = -1;
        let next = null;
        for (const [at, tocIndex] of anchors) {
            if (at <= page) index = tocIndex;
            else if (next === null) next = at;
        }
        return { index, next, opening: anchors.some(([at]) => at === page && at > 0) || page === 0 };
    }

    /* Колонтитул: на развороте слева — автор (или название книги), справа —
       глава; на одной странице — глава. Страница, где начинается глава, — без
       колонтитула, как в печатной книге. */
    headOf(view) {
        if (view.blank) return '';
        const chapter = this.chapterAt(view.section, view.page);
        if (chapter ? chapter.opening : view.page === 0) return '';
        if (this.spread && view.side === 'left') return this.book?.author || this.book?.title || '';
        const toc = Array.isArray(this.book?.toc) ? this.book.toc : [];
        let index = chapter ? chapter.index : -1;
        if (index < 0) {
            const part = this.book?.spine?.[view.section];
            if (part) {
                const offset = (Number(part.start) || 0) + (view.page / Math.max(1, view.pages)) * (Number(part.chars) || 0);
                index = currentTocIndex(toc, 1, offset);
            }
        }
        return (index >= 0 ? toc[index]?.title : '') || this.book?.title || '';
    }

    furnish(view) {
        if (view.section < 0 || view.blank) { view.setFurniture('', null); return; }
        view.setFurniture(this.headOf(view), this.isLinear(view.section) ? this.folioOf(view.section, view.page) : null);
    }

    refreshFurniture() {
        this.views.forEach((view) => { if (view.section >= 0 && !view.blank) this.furnish(view); });
    }

    /* Сколько экранов до следующей главы (после показанных). null — не знаем:
       файл без своих пунктов оглавления (продолжение главы из прошлого файла)
       или вёрстка ещё не дошла до него, а в файле несколько глав. */
    chapterLeft(section, page, shown) {
        const pages = this.sectionPages.get(section) || 1;
        const entries = this.tocBySpine.get(section) || [];
        const chapter = this.chapterAt(section, page);
        if (chapter) {
            if (!entries.length) return null;
            const end = chapter.next ?? pages;
            return Math.max(0, end - page - shown);
        }
        return entries.length === 1 ? Math.max(0, pages - page - shown) : null;
    }

    /* Толщина стопок по краям разворота — по доле прочитанного. */
    paintBook(fraction = this.lastShare ?? 0) {
        this.lastShare = fraction;
        const m = this.metrics;
        if (!m) return;
        const shadow = m.narrow ? 'none'
            : '0 1px 2px rgba(0,0,0,0.06), 0 14px 38px -10px rgba(0,0,0,0.28), 0 40px 70px -40px rgba(0,0,0,0.30)';
        css(this.bookEl, {
            left: `${m.left}px`, top: `${m.top}px`, width: `${m.width}px`, height: `${m.height}px`,
            borderRadius: m.narrow ? '0' : '2px',
        });
        css(this.shadowEl, { boxShadow: shadow, borderRadius: m.narrow ? '0' : '2px' });
        const stack = (width) => ({
            width: `${width}px`,
            background: `repeating-linear-gradient(90deg, ${this.palette.edge} 0 1px, ${this.palette.paper} 1px 2px)`,
            boxShadow: '0 2px 6px rgba(0,0,0,0.10)',
        });
        const show = m.mode === 'spread';
        css(this.edgeLeft, show ? { ...stack(Math.round(1 + 5 * fraction)), display: '', borderRadius: '2px 0 0 2px' } : { display: 'none' });
        css(this.edgeRight, show ? { ...stack(Math.round(1 + 5 * (1 - fraction))), display: '', borderRadius: '0 2px 2px 0' } : { display: 'none' });
    }

    /* ---------- раскладка ---------- */

    /*
     * Новый размер сцены. Место читателя — середина экрана, на котором он
     * последний раз ЧТО-ТО СДЕЛАЛ (открыл, перелистнул, перешёл): перекладки
     * этого места не двигают. Иначе каждое изменение размера заново округляло
     * бы место до начала разворота, и окно, растянутое туда и обратно,
     * возвращало бы читателя на страницу назад. Ход, идущий в этот момент,
     * досчитывается в своей, старой геометрии: план хода построен под неё.
     */
    layout(metrics) {
        if (this.turn) this.abortTurn();
        const keep = this.pos
            ? (this.anchorPlace?.section === this.pos.section ? this.anchorPlace : this.centerOf(this.pos))
            : null;
        const previous = this.metrics;
        this.metrics = metrics;
        const geometryChanged = !previous || previous.mode !== metrics.mode || previous.pageWidth !== metrics.pageWidth
            || previous.height !== metrics.height || previous.fontSize !== metrics.fontSize;
        // Поколение вёрстки — только когда страницы стали другими: сдвиг книги
        // на столе не должен обрывать открытие или переход, идущие сейчас.
        if (geometryChanged) this.layoutGen += 1;
        [...this.views, this.measurer].forEach((view) => view.applyMetrics(metrics));
        css(this.shade, { width: `${metrics.pageWidth}px`, height: `${metrics.height}px` });
        css(this.flapPaper, { width: `${metrics.pageWidth}px`, height: `${metrics.height}px` });
        css(this.flapGloss, { width: `${metrics.pageWidth}px`, height: `${metrics.height}px` });
        css(this.leafShade, { width: `${metrics.pageWidth}px`, height: `${metrics.height}px` });
        this.paintBook();
        if (geometryChanged) {
            this.sectionPages.clear();
            // Перевёрстку книги — не на каждый кадр перетаскивания окна.
            clearTimeout(this.paginationTimer);
            this.paginationRun = (this.paginationRun || 0) + 1;
            this.paginationTimer = setTimeout(() => this.startPagination(), previous ? 400 : 0);
        }
        if (keep && geometryChanged) {
            if (this.preparing) this.pendingLayout = keep;
            else this.openAt(keep.section, keep.fraction, '', 'layout');
        }
    }

    /* Время подготовки (переход, открытие, план хода): движок занят, а смена
       размера, пришедшая в это время, выполняется сразу после. */
    async busy(task) {
        this.preparing = true;
        try {
            return await task();
        } finally {
            this.preparing = false;
            const jump = this.pendingJump;
            const layout = this.pendingLayout;
            this.pendingJump = null;
            this.pendingLayout = null;
            if (this.closing) return;   // книгу закрывают — ничего не перекладываем
            if (jump) this.jumpTo(jump.section, jump.anchor, jump.fraction, jump.kind);
            else if (layout) this.openAt(layout.section, layout.fraction, layout.anchor || '', layout.kind || 'layout');
        }
    }

    /* ---------- позиция ---------- */

    align(page) { return this.spread ? page - (page % 2) : page; }

    rightOf(left) {
        const pages = this.sectionPages.get(left.section) || 1;
        return left.page + 1 < pages ? { section: left.section, page: left.page + 1 } : null;
    }

    currentViews() {
        return this.spread ? [this.roles.left, this.roles.right].filter(Boolean) : [this.roles.single].filter(Boolean);
    }

    freeViews() {
        const busySet = new Set(this.currentViews());
        return this.views.filter((view) => !busySet.has(view));
    }

    /* Расставить страницы по местам. Лишние прячутся: несколько полных глав
       на экране — лишняя отрисовка на каждом кадре. */
    arrange() {
        const visible = new Set(this.currentViews());
        this.views.forEach((view) => {
            css(view.el, { opacity: '', transition: '', transform: '' });
            setClip(view.el, '');
            view.el.style.zIndex = visible.has(view) ? '3' : '1';
            view.el.style.visibility = visible.has(view) ? 'visible' : 'hidden';
        });
        if (this.spread) {
            this.roles.left?.setSide('left');
            this.roles.right?.setSide('right');
        } else {
            this.roles.single?.setSide('single');
        }
        this.flapShadow.style.visibility = 'hidden';
        this.flapGloss.style.visibility = 'hidden';
        this.shade.style.visibility = 'hidden';
    }

    atEnd() {
        if (!this.pos) return false;
        const places = this.spread
            ? [this.pos, this.rightOf(this.pos)].filter(Boolean)
            : [this.pos];
        return places.some((place) => this.isLinear(place.section) && this.linearAfter(place.section) < 0
            && place.page >= (this.sectionPages.get(place.section) || 1) - 1);
    }

    /* Середина экрана как доля главы: на развороте — корешок (если правая
       страница есть), на одной странице — середина страницы. */
    centerOf(place) {
        const pages = Math.max(1, this.sectionPages.get(place.section) || 1);
        const shown = this.spread ? Math.min(2, pages - place.page) : 1;
        return { section: place.section, fraction: (place.page + shown / 2) / pages };
    }

    /* kind: open | layout | refresh | forward | back | jump | link. Прочитанной
       книгу делает только forward — ход вперёд на последний экран (ТЗ 4.3).
       refresh — только новые номера страниц, место не менялось. */
    emit(kind) {
        if (!this.pos) return;
        this.markPlaced();
        if (kind !== 'layout' && kind !== 'refresh') this.anchorPlace = this.centerOf(this.pos);
        const pages = this.sectionPages.get(this.pos.section) || 1;
        const shown = this.spread ? 2 : 1;
        const chapter = this.chapterAt(this.pos.section, this.pos.page);
        const place = {
            section: this.pos.section,
            page: this.pos.page,
            pages,
            fraction: this.pos.page / pages,
            atEnd: this.atEnd(),
            paged: kind === 'forward',
            kind,
            folio: this.isLinear(this.pos.section) ? this.folioOf(this.pos.section, this.pos.page) : null,
            total: this.pagination?.total ?? null,
            leftInChapter: this.isLinear(this.pos.section) ? this.chapterLeft(this.pos.section, this.pos.page, shown) : null,
            chapterIndex: chapter ? chapter.index : -1,
        };
        const part = this.book?.spine?.[this.pos.section];
        const totalChars = Math.max(1, Number(this.book?.total_chars) || 1);
        if (part) this.paintBook(Math.min(1, ((Number(part.start) || 0) + place.fraction * (Number(part.chars) || 0)) / totalChars));
        this.onPosition?.(place);
    }

    /* Открыть книгу (или переложить после смены размера) на месте. */
    async openAt(section, fraction = 0, anchor = '', kind = 'open') {
        const request = { section, fraction, anchor, kind };
        if (this.preparing) {
            this.pendingLayout = request;
            return false;
        }
        const gen = this.layoutGen;
        // Размер сменился посреди открытия — открыть заново на новой вёрстке
        // (busy повторит). Иначе первое открытие терялось: места ещё нет, и
        // перекладке нечего было запомнить — книга оставалась пустой.
        const retry = () => {
            if (!this.pendingLayout && !this.closing) this.pendingLayout = request;
            return false;
        };
        return this.busy(async () => {
            // Место на странице-обложке (сохранено, когда она была страницей)
            // — это начало книги.
            const target = this.loader.spine[section] && !this.isCover(section) ? section : this.firstLinear();
            const [main, second] = this.views;
            main.setSide(this.spread ? 'left' : 'single');
            if (this.spread) second.setSide('right');
            if (!await this.fill(main, target)) return false;
            if (gen !== this.layoutGen) return retry();
            const page = this.align(anchor ? main.pageOfAnchor(anchor)
                : pageAtFraction(fraction, main.pages));
            this.pos = { section: target, page };
            if (this.spread) {
                this.roles = { left: main, right: second, single: null };
                await this.show(main, this.pos);
                await this.show(second, this.rightOf(this.pos));
            } else {
                this.roles = { left: null, right: null, single: main };
                await this.show(main, this.pos);
            }
            if (this.destroyed) return false;
            if (gen !== this.layoutGen) return retry();
            this.arrange();
            this.emit(kind);
            this.prefetch();
            return true;
        });
    }

    /* Первый экран книги: назад листать некуда — дальше только закрыть её. */
    isFirstPage() {
        return Boolean(this.pos) && this.pos.page === 0 && this.linearBefore(this.pos.section) < 0;
    }

    /* Читатель в самом начале: на первом экране книги или там, откуда
       открывается новая (firstLinear: до неё — разве что листы без текста). */
    atBeginning() {
        return Boolean(this.pos) && this.pos.page === 0
            && (this.pos.section === this.firstLinear() || this.linearBefore(this.pos.section) < 0);
    }

    /* Книга лежит закрытой в начале: смена размера окна кладёт её снова на
       первый экран. Без этого перекладка держала бы середину прошлого
       разворота — стр. 1–2 на одной странице стали бы стр. 2. */
    pinStart() {
        if (this.pos) this.anchorPlace = { section: this.pos.section, fraction: 0 };
    }

    /* ---------- соседние экраны ---------- */

    async pagesOf(section) {
        if (this.sectionPages.has(section)) return this.sectionPages.get(section);
        const probe = this.freeViews()[0];
        if (!probe) return 1;
        await this.fill(probe, section);
        return this.sectionPages.get(section) || 1;
    }

    targetForward() {
        if (!this.pos) return null;
        const step = this.spread ? 2 : 1;
        const pages = this.sectionPages.get(this.pos.section) || 1;
        if (this.pos.page + step < pages) return { section: this.pos.section, page: this.pos.page + step };
        const next = this.linearAfter(this.pos.section);
        return next >= 0 ? { section: next, page: 0 } : null;
    }

    async targetBackward() {
        const step = this.spread ? 2 : 1;
        if (this.pos.page - step >= 0) return { section: this.pos.section, page: this.pos.page - step };
        if (this.pos.page > 0) return { section: this.pos.section, page: 0 };
        const previous = this.linearBefore(this.pos.section);
        if (previous < 0) return null;
        const pages = await this.pagesOf(previous);
        return { section: previous, page: this.align(pages - 1) };
    }

    /* Заранее разложить следующий ход вперёд в свободные страницы. */
    prefetch() {
        const target = this.targetForward();
        if (!target) return;
        const token = ++this.token;
        const [first, second] = this.freeViews();
        const stale = () => token !== this.token || this.turn || this.preparing;
        if (first) {
            first.setSide(this.spread ? 'left' : 'single');
            this.fill(first, target.section).then((ok) => {
                if (!ok || stale()) return;
                first.setPage(target.page);
                this.furnish(first);
            }).catch(() => {});
        }
        if (second) {
            second.setSide(this.spread ? 'right' : 'single');
            // Разворот: вторая — следующая правая; одна страница: копия
            // текущей для просвета на обороте.
            const place = this.spread ? target : this.pos;
            this.fill(second, place.section).then((ok) => {
                if (!ok || stale()) return;
                if (this.spread) {
                    const right = this.rightOf(target);
                    if (right) second.setPage(right.page); else second.setBlank();
                } else {
                    second.setPage(place.page);
                }
                this.furnish(second);
            }).catch(() => {});
        }
    }

    /* ---------- загиб ---------- */

    corner(fromTop) {
        const { pageWidth, height } = this.metrics;
        return { x: pageWidth, y: fromTop ? 0 : height };
    }

    paintCurl(geometry) {
        const turn = this.turn;
        if (!geometry || !turn) {
            this.flapShadow.style.visibility = 'hidden';
            this.flapGloss.style.visibility = 'hidden';
            this.shade.style.visibility = 'hidden';
            if (turn) turn.flap.el.style.visibility = 'hidden';
            return;
        }
        const W = this.metrics.pageWidth;
        setClip(turn.leaf.el, polygonCss(geometry.kept));

        const removed = polygonCss(geometry.removed);
        const { angle, start } = geometry.gradient;
        const depth = geometry.depth;
        const lift = Math.sin(Math.PI * geometry.progress);   // 0 у краёв хода, 1 посередине
        // Свет гаснет к краям хода: первый и последний кадры совпадают с
        // лежащими страницами, и в конце хода ничего не «щёлкает».
        const k = Math.min(1, 6.4 * geometry.progress * (1 - geometry.progress));
        const [a, b, c, d, e, f] = geometry.matrix;
        const reflect = matrixCss([a, b, c, d, e, f]);
        const left = `${turn.leafX}px`;

        // Бумага оборота — с тенью от всего листа.
        css(this.flapPaper, { left, transform: reflect });
        setClip(this.flapPaper, removed);
        this.flapShadow.style.filter = `drop-shadow(0 2px 10px rgba(0,0,0,${(0.24 * k).toFixed(3)}))`;
        this.flapShadow.style.visibility = 'visible';

        // Сама страница оборота. На развороте — следующая левая, зеркалом
        // внутри отражения (x → W − x), чтобы текст читался; её обрезка — та
        // же форма, отражённая в её собственных координатах. На одной
        // странице — копия листа, едва просвечивающая сквозь бумагу.
        const flap = turn.flap;
        if (turn.flapIsPage) {
            css(flap.el, { left, transform: matrixCss([-a, -b, c, d, a * W + e, b * W + f]), opacity: '' });
            setClip(flap.el, polygonCss(geometry.removed.map((point) => ({ x: W - point.x, y: point.y }))));
        } else {
            css(flap.el, { left, transform: reflect, opacity: '0.12' });
            setClip(flap.el, removed);
        }
        css(flap.el, { zIndex: '5', visibility: 'visible' });

        // Свет на обороте: тень у самого сгиба, блик по изгибу, тень к краю.
        css(this.flapGloss, { left, transform: reflect, visibility: 'visible' });
        setClip(this.flapGloss, removed);
        const sheen = turn.flapIsPage ? 0.12 : this.palette.gloss;
        this.flapGloss.style.backgroundImage = `linear-gradient(${angle}deg,`
            + ` rgba(0,0,0,${((0.12 + 0.10 * lift) * k).toFixed(3)}) ${start}px,`
            + ` rgba(255,255,255,0) ${start + Math.min(24, depth * 0.1)}px,`
            + ` rgba(255,255,255,${(sheen * k).toFixed(3)}) ${start + depth * 0.42}px,`
            + ` rgba(0,0,0,${(0.06 * k).toFixed(3)}) ${start + depth}px)`;

        // Тень поднятого листа на открывшейся странице — у самого сгиба.
        const reach = Math.min(W * 0.3, 20 + depth * 0.24);
        this.shade.style.left = left;
        setClip(this.shade, removed);
        this.shade.style.backgroundImage = `linear-gradient(${angle}deg,`
            + ` rgba(0,0,0,${((0.14 + 0.2 * lift) * k).toFixed(3)}) ${start}px,`
            + ` rgba(0,0,0,0) ${start + reach}px)`;
        this.shade.style.visibility = 'visible';
    }

    setPoint(point) {
        if (!this.turn) return;
        this.turn.point = point;
        this.paintCurl(curlGeometry(point, this.turn.corner, this.metrics.pageWidth, this.metrics.height));
    }

    /* Начать ход. -> false, если листать некуда или идёт другая подготовка. */
    async beginTurn(forward, fromTop = false) {
        if (!this.metrics || !this.pos || this.turn || this.preparing || this.closing) return false;
        if (forward && !this.targetForward()) {
            // Дальше некуда. Если это последний экран книги, ход вперёд и есть
            // «пролистал последнюю страницу» — книга из одного экрана иначе не
            // заканчивалась бы никогда.
            if (this.atEnd()) this.emit('forward');
            return false;
        }
        this.token += 1;   // отменяет незаконченную подготовку свободных страниц
        const gen = this.layoutGen;
        const plan = await this.busy(async () => {
            const built = forward ? await this.planForward() : await this.planBackward();
            // Размер сменился, пока готовились: план построен под старую вёрстку.
            return built && gen === this.layoutGen ? built : null;
        });
        // Пока план готовился, книгу начали закрывать — хода не будет.
        if (!plan || this.destroyed || this.turn || this.closing || gen !== this.layoutGen) return false;
        const views = [plan.leaf, plan.under, plan.flap, ...(plan.spread ? [plan.still] : [])];
        if (views.some((view) => !view)) return false;
        const corner = this.corner(fromTop);
        const { pageWidth } = this.metrics;
        const flat = { x: corner.x - 0.6, y: corner.y };
        const turned = { x: -pageWidth - 2, y: corner.y };
        this.stage(plan);
        this.turn = {
            ...plan, forward, corner, flat, turned, completed: undefined,
            point: forward ? flat : turned,
        };
        this.setPoint(this.turn.point);
        return true;
    }

    async planForward() {
        const target = this.targetForward();
        const [first, second] = this.freeViews();
        if (!target || !first || !second) return null;
        if (this.spread) {
            // Лист — текущая правая; под ней — следующая правая; на обороте —
            // следующая левая. Текущая левая лежит на месте.
            first.setSide('left');
            second.setSide('right');
            if (!await this.show(first, target)) return null;
            if (!await this.show(second, this.rightOf(target))) return null;
            return {
                spread: true, target, leaf: this.roles.right, under: second, still: this.roles.left, flap: first,
                flapIsPage: true, leafX: this.metrics.pageWidth, after: { left: first, right: second },
            };
        }
        first.setSide('single');
        second.setSide('single');
        if (!await this.show(first, target)) return null;
        if (!await this.show(second, this.pos)) return null;   // просвет на обороте
        return {
            spread: false, target, leaf: this.roles.single, under: first, still: null, flap: second,
            flapIsPage: false, leafX: 0, after: { single: first },
        };
    }

    async planBackward() {
        const target = await this.targetBackward();
        const [first, second] = this.freeViews();
        if (!target || !first || !second) return null;
        if (this.spread) {
            // Назад — тот же ход от прошлого разворота к текущему, но от конца к
            // началу: прошлая правая ложится на текущую правую, её оборот —
            // текущая левая, под ней открывается прошлая левая.
            first.setSide('left');
            second.setSide('right');
            if (!await this.show(first, target)) return null;
            if (!await this.show(second, this.rightOf(target))) return null;
            return {
                spread: true, target, leaf: second, under: this.roles.right, still: first, flap: this.roles.left,
                flapIsPage: true, leafX: this.metrics.pageWidth, after: { left: first, right: second },
            };
        }
        first.setSide('single');
        second.setSide('single');
        if (!await this.show(first, target)) return null;
        if (!await this.show(second, target)) return null;
        return {
            spread: false, target, leaf: first, under: this.roles.single, still: null, flap: second,
            flapIsPage: false, leafX: 0, after: { single: first },
        };
    }

    /* Разложить страницы на время хода. Страницы не переносятся в DOM —
       только места, слои и видимость. */
    stage(plan) {
        const place = (view, side, z) => {
            view.setSide(side);
            css(view.el, { zIndex: String(z), visibility: 'visible', opacity: '', transform: '' });
            setClip(view.el, '');
        };
        if (plan.spread) {
            place(plan.still, 'left', 2);
            place(plan.under, 'right', 1);
            place(plan.leaf, 'right', 3);
            plan.flap.setSide('left', 0);
        } else {
            place(plan.under, 'single', 1);
            place(plan.leaf, 'single', 3);
            plan.flap.setSide('single', 0);
        }
        this.flapPaper.style.background = this.palette.back;
        const used = new Set([plan.still, plan.under, plan.leaf, plan.flap].filter(Boolean));
        this.views.forEach((view) => { if (!used.has(view)) view.el.style.visibility = 'hidden'; });
    }

    /* Довести ход: completed — перевернуть до конца, иначе вернуть лист.
       Задуманный исход запоминается в ходе: новое действие во время доворота
       досчитает ход именно так, а не «как-нибудь до конца». */
    finishTurn(completed, duration) {
        const turn = this.turn;
        if (!turn) return Promise.resolve();
        turn.completed = completed;
        const goal = turn.forward === completed ? turn.turned : turn.flat;
        return this.animate(turn.point, goal, duration, easeOut).then((finished) => {
            if (finished && this.turn === turn) this.settle(completed);
        });
    }

    /* Ход закончен: страницы встают на новые (или прежние) места. Роли — из
       режима, в котором ход начинался. */
    settle(completed) {
        const turn = this.turn;
        if (!turn) return;
        cancelAnimationFrame(this.raf);
        this.turn = null;
        if (completed) {
            this.pos = turn.target;
            this.roles = turn.spread
                ? { left: turn.after.left, right: turn.after.right, single: null }
                : { left: null, right: null, single: turn.after.single };
        }
        this.arrange();
        this.currentViews().forEach((view) => this.furnish(view));
        if (completed) this.emit(turn.forward ? 'forward' : 'back');
        this.prefetch();
    }

    /* Прервать ход сейчас же: доворачивающийся — тем исходом, к которому шёл;
       ход под пальцем — вернуть лист. */
    abortTurn() {
        if (!this.turn) return;
        this.animationRun = (this.animationRun || 0) + 1;
        cancelAnimationFrame(this.raf);
        this.settle(this.turn.completed === true);
    }

    /* -> Promise<boolean>: true — дошла до конца, false — её прервали. */
    animate(from, to, duration, easing, path = null) {
        cancelAnimationFrame(this.raf);
        const run = (this.animationRun || 0) + 1;
        this.animationRun = run;
        if (this.reducedMotion || duration <= 0) {
            this.setPoint(to);
            return Promise.resolve(true);
        }
        return new Promise((resolve) => {
            const started = performance.now();
            const step = (now) => {
                if (this.animationRun !== run || this.destroyed) { resolve(false); return; }
                const t = Math.min(1, (now - started) / duration);
                const k = easing(t);
                const point = path ? path(k) : { x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k };
                this.setPoint(point);
                if (t < 1) this.raf = requestAnimationFrame(step);
                else resolve(true);
            };
            this.raf = requestAnimationFrame(step);
        });
    }

    /* Ход, который доворачивается сам (палец уже отпущен), досчитать
       мгновенно — тем исходом, к которому он шёл. Ход под пальцем не трогаем. */
    completeNow() {
        const turn = this.turn;
        if (!turn || turn.completed === undefined) return false;
        this.animationRun = (this.animationRun || 0) + 1;
        cancelAnimationFrame(this.raf);
        this.settle(turn.completed);
        return true;
    }

    /* Нажатие или клавиша: полный ход по дуге. Быстрые нажатия листают
       быстро: доворачивающийся ход досчитывается сразу. */
    async flip(forward) {
        if (this.turn && !this.completeNow()) return false;
        const started = await this.beginTurn(forward, false);
        if (!started) return false;
        const turn = this.turn;
        turn.completed = true;
        const { corner } = turn;
        const { pageWidth, height } = this.metrics;
        const duration = this.reducedMotion ? 0 : (turn.spread ? 640 : 520);
        const path = forward
            ? (k) => tapPath(k, corner, pageWidth, height)
            : (k) => tapPath(1 - k, corner, pageWidth, height);
        const finished = await this.animate(null, forward ? turn.turned : turn.flat, duration, easeInOut, path);
        if (finished && this.turn === turn) this.settle(true);
        return true;
    }

    /* ---------- перетаскивание ---------- */

    /* Новый свайп, пока прошлый лист ещё доворачивается: тот досчитываем,
       этот начинаем — иначе на обычном темпе чтения терялся бы каждый второй. */
    async dragStart(forward, fromTop) {
        if (this.turn) this.completeNow();
        return this.beginTurn(forward, fromTop);
    }

    /* dx, dy — сдвиг пальца от точки касания. Вперёд палец держит угол;
       назад лист приходит из перевёрнутого положения вдвое быстрее пальца —
       так он оказывается под пальцем к середине страницы. */
    dragMove(dx, dy) {
        const turn = this.turn;
        if (!turn || turn.completed !== undefined) return;
        if (turn.forward) {
            this.setPoint({ x: turn.corner.x + dx, y: turn.corner.y + dy });
        } else {
            this.setPoint({ x: turn.turned.x + 2 * Math.max(0, dx), y: turn.corner.y + dy * 0.5 });
        }
    }

    dragEnd(velocityX) {
        const turn = this.turn;
        if (!turn || turn.completed !== undefined) return Promise.resolve();
        const geometry = curlGeometry(turn.point, turn.corner, this.metrics.pageWidth, this.metrics.height);
        const progress = geometry ? geometry.progress : (turn.forward ? 0 : 1);
        const completed = turn.forward
            ? (progress > 0.22 || velocityX < -0.35) && velocityX < 0.35
            : (progress < 0.78 || velocityX > 0.35) && velocityX > -0.35;
        const remaining = completed === turn.forward ? 1 - progress : progress;
        const duration = Math.max(170, Math.round(480 * remaining));
        return this.finishTurn(completed, duration);
    }

    /* Точка на книге: где нажали — на левой или правой странице, в поле или в
       наборе. */
    locate(clientX, clientY) {
        const rect = this.bookEl.getBoundingClientRect();
        const x = clientX - rect.left;
        const y = clientY - rect.top;
        const side = this.spread ? (x < rect.width / 2 ? 'left' : 'right') : 'single';
        const pageX = side === 'right' ? x - rect.width / 2 : x;
        const pageWidth = this.spread ? rect.width / 2 : rect.width;
        const pad = this.metrics?.pad || { inner: 0, outer: 0 };
        // Поле страницы (вне набора): у разворота внешнее поле — слева у левой
        // страницы и справа у правой.
        const margin = side === 'left' ? (pageX < pad.outer ? 'outer' : pageX > pageWidth - pad.inner ? 'inner' : null)
            : side === 'right' ? (pageX > pageWidth - pad.outer ? 'outer' : pageX < pad.inner ? 'inner' : null)
                : (pageX < pad.outer ? 'left' : pageX > pageWidth - pad.inner ? 'right' : null);
        return {
            x, y, side, pageX, pageWidth, margin, height: rect.height,
            inside: x >= 0 && y >= 0 && x <= rect.width && y <= rect.height,
        };
    }

    /* ---------- переходы ---------- */

    /* Переход по оглавлению или ссылке — мягкой сменой страниц, без загиба.
       Всё время смены движок занят: ход, начатый посреди неё, перезаписал бы
       страницы и увёл бы читателя мимо цели. Сменился размер посреди
       перехода — переход повторяется на новой вёрстке. */
    async jumpTo(section, anchor = '', fraction = 0, kind = 'jump') {
        if (this.closing) return false;
        // Ссылка на страницу-обложку ведёт в начало книги: страницей она не
        // показывается (закрыть книгу на обложке решает LibraryReader).
        if (this.isCover(section)) {
            const start = this.firstLinear();
            return this.isCover(start) ? false : this.jumpTo(start, '', 0, kind);
        }
        if (this.turn && !this.completeNow()) return false;
        if (this.turn || !this.metrics) return false;
        if (this.preparing) {
            this.pendingJump = { section, anchor, fraction, kind };
            return false;
        }
        this.token += 1;
        const gen = this.layoutGen;
        const moved = await this.busy(async () => {
            const [first, second] = this.freeViews();
            if (!first || !second) return false;
            first.setSide(this.spread ? 'left' : 'single');
            if (this.spread) second.setSide('right');
            if (!await this.fill(first, section) || this.destroyed) return false;
            const page = this.align(anchor ? first.pageOfAnchor(anchor)
                : pageAtFraction(fraction, first.pages));
            const target = { section, page };
            if (!await this.show(first, target)) return false;
            if (this.spread && !await this.show(second, this.rightOf(target))) return false;
            if (gen !== this.layoutGen) {
                this.pendingJump = { section, anchor, fraction, kind };
                return false;
            }
            const next = this.spread
                ? { left: first, right: second, single: null }
                : { left: null, right: null, single: first };
            // Новые страницы — под старыми, старые тают.
            const old = this.currentViews();
            (this.spread ? [first, second] : [first]).forEach((view) => css(view.el, { zIndex: '2', visibility: 'visible' }));
            old.forEach((view) => css(view.el, { transition: 'opacity 220ms ease', opacity: '0', zIndex: '3' }));
            await new Promise((resolve) => setTimeout(resolve, this.reducedMotion ? 0 : 230));
            if (this.destroyed) return false;
            this.pos = target;
            this.roles = next;
            this.arrange();
            return true;
        });
        if (!moved) return false;
        this.emit(kind);
        this.prefetch();
        return true;
    }

    /* ---------- раскрытие и закрытие книги (bookOpening.js) ----------
       Лист, который при раскрытии ложится налево, — сама левая страница
       разворота: её поворот вокруг корешка задают снаружи (Web Animations),
       здесь — только подготовка и уборка. Пока книга раскрывается, подсчёт
       страниц ждёт: вёрстка главы посреди раскрытия отняла бы у него кадры. */

    /* Подсчёт страниц подождёт раскрытия книги: вёрстка главы в момент его
       старта задержала бы первый кадр. Отпускает endOpening. */
    hold() {
        this.held = true;
    }

    /* Книгу закрывают: новые ходы, переходы и перекладки не начинаются, начатый
       ход досчитывается, а подготовка, которая уже идёт (переход по оглавлению,
       перекладка), доделывается — лист для закрытия берётся после неё, иначе
       она подменила бы страницу посреди движения. */
    beginClosing() {
        this.closing = true;
        this.held = true;
        this.token += 1;
        this.pendingJump = null;
        this.pendingLayout = null;
        if (this.turn) this.abortTurn();
        const started = performance.now();
        return new Promise((resolve) => {
            const wait = () => {
                if (!this.preparing || this.destroyed || performance.now() - started > 900) resolve();
                else requestAnimationFrame(wait);
            };
            wait();
        });
    }

    openingParts() {
        this.held = true;
        const spread = this.spread;
        const leaf = spread ? this.roles.left : null;
        // Лист в полёте выходит за обрез книги — перспектива делает его крупнее.
        this.clipper.style.overflow = 'visible';
        if (leaf) {
            leaf.el.style.transformOrigin = '100% 50%';
            leaf.el.style.visibility = 'visible';
            leaf.el.style.zIndex = '3';
        }
        this.leafShade.style.visibility = leaf ? 'visible' : 'hidden';
        return {
            spread,
            pageWidth: this.metrics?.pageWidth || 0,
            leaf: leaf ? leaf.el : null,
            leafShade: leaf ? this.leafShade : null,
            shadow: spread ? this.shadowEl : null,
            edgeLeft: spread ? this.edgeLeft : null,
        };
    }

    endOpening() {
        this.held = false;
        this.clipper.style.overflow = 'hidden';
        this.leafShade.style.visibility = 'hidden';
        // Загиб считает transform страниц от левого верхнего угла.
        this.views.forEach((view) => { view.el.style.transformOrigin = '0 0'; });
        this.arrange();
    }

    /* ---------- вёрстка всей книги: настоящие номера страниц ---------- */

    paginationKey() {
        const m = this.metrics;
        // v5: страница-обложка больше не страница — номера сдвинулись.
        return `lr-pages:v5:${this.cacheKey}:${m.mode}:${m.pageWidth - m.pad.inner - m.pad.outer}x${m.height - m.pad.top - m.pad.bottom}:${m.fontSize}`;
    }

    applyPagination(pages, anchorPages, done) {
        const start = new Map();
        let running = 0;
        let complete = true;
        this.loader.spine.forEach((item, index) => {
            if (!item.linear) return;
            if (!pages.has(index)) { complete = false; return; }
            if (!complete) return;
            start.set(index, running);
            const count = pages.get(index);
            // На развороте глава начинается с левой страницы: нечётная глава
            // оставляет чистую правую.
            running += this.spread ? count + (count % 2) : count;
        });
        const bookToc = Array.isArray(this.book?.toc) ? this.book.toc : [];
        const anchors = new Map();
        const toc = bookToc.map((item, tocIndex) => {
            const at = anchorPages.get(`${item.spine}#${item.anchor || ''}`);
            if (Number.isFinite(at)) {
                if (!anchors.has(item.spine)) anchors.set(item.spine, []);
                anchors.get(item.spine).push([at, tocIndex]);
            }
            const s = start.get(item.spine);
            return Number.isFinite(s) && Number.isFinite(at) ? s + at + 1 : null;
        });
        anchors.forEach((list) => list.sort((x, y) => x[0] - y[0]));
        this.pagination = { start, total: done ? running : null, toc, anchors, done };
        this.refreshFurniture();
        this.onPagination?.(this.pagination);
        this.emit('refresh');
    }

    async startPagination() {
        const run = (this.paginationRun || 0) + 1;
        this.paginationRun = run;
        const stale = () => this.destroyed || this.paginationRun !== run;
        const key = this.paginationKey();
        const pages = new Map();
        const anchorPages = new Map();
        try {
            const cached = JSON.parse(window.localStorage.getItem(key) || 'null');
            if (cached && Array.isArray(cached.pages)) {
                cached.pages.forEach(([index, count]) => pages.set(index, count));
                (cached.anchors || []).forEach(([name, page]) => anchorPages.set(name, page));
                this.applyPagination(pages, anchorPages, true);
                return;
            }
        } catch { /* нет кэша — посчитаем */ }
        this.pagination = null;
        this.onPagination?.(null);
        const tocBySpine = new Map();
        (Array.isArray(this.book?.toc) ? this.book.toc : []).forEach((item) => {
            if (!tocBySpine.has(item.spine)) tocBySpine.set(item.spine, []);
            tocBySpine.get(item.spine).push(item.anchor || '');
        });
        const { spine } = this.loader;
        let lastReport = 0;
        for (let index = 0; index < spine.length; index += 1) {
            // Не мешать: ждать, пока книга открыта и лист не в движении.
            // eslint-disable-next-line no-await-in-loop
            while (!stale() && (!this.pos || this.turn || this.preparing || this.held)) await idle();
            if (stale()) return;
            if (!spine[index].linear) continue;
            // eslint-disable-next-line no-await-in-loop
            if (!await this.fill(this.measurer, index)) return;
            if (stale()) return;
            pages.set(index, this.measurer.pages);
            (tocBySpine.get(index) || []).forEach((anchor) => {
                anchorPages.set(`${index}#${anchor}`, this.measurer.pageOfAnchor(anchor));
            });
            // Номер текущей страницы — как можно раньше: до неё — без пауз,
            // дальше — в свободное время. Отчёт — не на каждую главу: у книги в
            // 368 глав оглавление перерисовывалось бы 368 раз.
            const reachedReader = this.pos && index >= this.pos.section;
            const now = Date.now();
            if (reachedReader && (index === this.pos.section || now - lastReport > 600)) {
                lastReport = now;
                this.applyPagination(pages, anchorPages, false);
            }
            // eslint-disable-next-line no-await-in-loop
            await (reachedReader ? idle() : tick());
        }
        if (stale()) return;
        this.measurer.clear();
        this.applyPagination(pages, anchorPages, true);
        try {
            window.localStorage.setItem(key, JSON.stringify({ pages: [...pages], anchors: [...anchorPages] }));
        } catch { /* нет места — посчитаем в следующий раз заново */ }
    }

    destroy() {
        this.destroyed = true;
        clearTimeout(this.paginationTimer);
        this.paginationRun = (this.paginationRun || 0) + 1;
        this.animationRun = (this.animationRun || 0) + 1;
        cancelAnimationFrame(this.raf);
        this.bookEl.remove();
        this.offscreen.remove();
    }
}
