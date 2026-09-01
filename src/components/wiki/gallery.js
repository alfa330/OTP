/* Галерея статьи: листание кадров одного действия. Счётная часть.
 *
 * Вынесено из WikiArticle.jsx отдельным модулем не ради красоты — тем же
 * приёмом, что imageSize.js, articleLink.js и parkLogo.js: витрина тянет за
 * собой React, DOMPurify и половину раздела, то есть проверить листание
 * `node --test` без сборки нельзя, а ошибаться здесь есть где. Ровно здесь
 * ошибки и были: геометрия считалась ДО загрузки картинок, а мышью на
 * десктопе тянуть было нечего.
 *
 * ЧТО ДЕЛАЕТ CSS И ЧТО ДЕЛАЕТ ЭТОТ МОДУЛЬ. Лента с overflow-x и scroll-snap
 * (wiki-blocks.css) даёт настоящий свайп пальцем: инерцию, остановку ровно на
 * кадре, работу без единой строки JS. Модуль добавляет то, чего у CSS нет:
 * стрелки, точки, подпись, перетаскивание мышью и клавиши. Отними модуль —
 * лента останется листаемой пальцем; отними CSS — стрелки будут двигать то,
 * что и так лежит столбиком.
 *
 * ТРИ ОШИБКИ, ИЗ КОТОРЫХ ЭТОТ ФАЙЛ И ПОЯВИЛСЯ:
 *
 * 1. РАЗМЕРЫ ДО ЗАГРУЗКИ. У <img> без атрибутов width/height до загрузки нет
 *    собственных размеров: offsetWidth равен нулю, и вся арифметика «какой
 *    кадр открыт» и «куда прокрутить» считается по нулям. Внешне это выглядит
 *    как «стрелки не работают» или «прыгает не туда». Лечится не заглушкой, а
 *    пересчётом по событию load каждой картинки.
 *
 * 2. МЫШЬ. Полоса прокрутки у ленты спрятана (под галереей есть точки, они
 *    понятнее), и на десктопе схватить ленту было нечем: пальцем листается, а
 *    мышью — нет. Отсюда перетаскивание на pointer-событиях.
 *
 * 3. НАТИВНОЕ ПЕРЕТАСКИВАНИЕ КАРТИНКИ. Браузер по умолчанию тащит <img> как
 *    файл, и это перебивает наше перетаскивание ленты: курсор уезжает с
 *    призраком картинки, лента стоит. Поэтому кадрам внутри галереи
 *    draggable=false.
 */

/* Пикселей, после которых движение мыши считается перетаскиванием, а не
   щелчком. Без порога любой клик по кадру превращался бы в микро-прокрутку, и
   ссылка внутри подписи перестала бы нажиматься. */
export const DRAG_THRESHOLD = 4;

/* Длительность доводки до кадра. 260 мс — столько же, сколько у остальных
   переходов раздела; заметно, но не заставляет ждать. */
export const SCROLL_MS = 260;

/* Замедление к концу. Линейное движение читается как рывок: лента трогается и
   встаёт мгновенно, и глазу не за что зацепиться. */
export const ease = (t) => 1 - (1 - t) * (1 - t) * (1 - t);

/** Индекс открытого кадра: тот, чья середина ближе к середине ленты.
 *
 * По scrollLeft считать нельзя: кадры бывают разной ширины (после правки через
 * ИИ кадр приезжает внутри абзаца), и «сколько прокрутили» не отвечает на
 * вопрос «что сейчас видно».
 *
 * Геометрия берётся из offsetLeft/clientWidth, а НЕ из getBoundingClientRect:
 * у раздела свой масштаб (zoom на .wiki-scope), и rect возвращает размеры,
 * уже умноженные на него, — на больших мониторах это промах на 6-35 %.
 */
export const activeIndex = (strip, slides) => {
    if (!slides.length) return 0;
    const middle = strip.scrollLeft + strip.clientWidth / 2;
    let best = 0;
    let bestGap = Infinity;
    slides.forEach((slide, index) => {
        const gap = Math.abs(slide.offsetLeft + slide.offsetWidth / 2 - middle);
        if (gap < bestGap) { bestGap = gap; best = index; }
    });
    return best;
};

/** Куда прокрутить, чтобы кадр встал по центру ленты. */
export const scrollTargetFor = (strip, slide) => (
    slide.offsetLeft - (strip.clientWidth - slide.offsetWidth) / 2
);

/** Значение в границах массива. Нужно и стрелкам, и точкам, и клавишам. */
export const clampIndex = (index, count) => Math.max(0, Math.min(count - 1, index));

const PREV_ICON = '<svg width="9" height="15" viewBox="0 0 9 15" fill="none" aria-hidden="true">'
    + '<path d="M7.5 1.5 2 7.5l5.5 6" stroke="currentColor" stroke-width="1.8"'
    + ' stroke-linecap="round" stroke-linejoin="round"/></svg>';
const NEXT_ICON = '<svg width="9" height="15" viewBox="0 0 9 15" fill="none" aria-hidden="true">'
    + '<path d="M1.5 1.5 7 7.5l-5.5 6" stroke="currentColor" stroke-width="1.8"'
    + ' stroke-linecap="round" stroke-linejoin="round"/></svg>';

/** Оживить одну галерею. Возвращает функцию отмены (или null, если нечего оживлять).
 *
 * strip — сам <div data-wiki-block="gallery"> из тела статьи. Обвязка строится
 * ВОКРУГ него и в базу не попадает: тело статьи обязано остаться разметкой,
 * которую можно править руками.
 */
export function mountGallery(strip, doc = document) {
    if (!strip || strip.parentElement?.classList.contains('wiki-gallery')) return null;

    /* Кадр, обёрнутый в абзац, разворачиваем. Так галерея приходит после
       правки через ИИ: маркер картинки защищается вместе с абзацем
       (wiki/ai/revise.py), и обратно картинка возвращается внутрь <p>. Абзац с
       текстом не трогаем — это подсказка из шаблона вставки, у неё свой вид. */
    Array.from(strip.querySelectorAll('p')).forEach((para) => {
        const inner = para.querySelector('img');
        if (inner && !para.textContent.trim()) para.replaceWith(inner);
    });

    const frames = Array.from(strip.querySelectorAll('img'));
    /* Одному кадру карусель не нужна: стрелки, которым некуда листать, и одна
       точка — это шум, а не управление. */
    if (frames.length < 2) return null;

    /* Каждый кадр — в обёртку на всю ширину ленты. Без неё кадры стоят своим
       размером, и два вертикальных скриншота телефона просто влезают в колонку
       РЯДОМ: листать нечего, а стрелки двигают то, что и так целиком видно.
       Растянуть сам <img> нельзя — flex-basis у картинки растягивает КАРТИНКУ,
       и скриншот расплывается во всю колонку. */
    const slides = frames.map((frame) => {
        const slide = doc.createElement('div');
        slide.className = 'wiki-gallery__slide';
        frame.replaceWith(slide);
        slide.appendChild(frame);
        /* Браузер тащит картинку как файл и перебивает наше перетаскивание
           ленты: курсор уезжает с призраком кадра, лента стоит. */
        frame.setAttribute('draggable', 'false');
        return slide;
    });

    /* Список отмен заводится ДО обвязки: в него пишет и анимация прокрутки. */
    const undo = [];
    const undoLater = undo;

    const box = doc.createElement('div');
    box.className = 'wiki-gallery';
    strip.replaceWith(box);
    box.appendChild(strip);

    /* Лента получает фокус и роль: без tabindex до неё не добраться с
       клавиатуры, а без роли читалка с экрана назовёт её просто группой. */
    strip.setAttribute('tabindex', '0');
    strip.setAttribute('role', 'group');
    strip.setAttribute('aria-roledescription', 'галерея');

    const caption = doc.createElement('p');
    caption.className = 'wiki-gallery__caption';
    box.appendChild(caption);

    const dots = doc.createElement('div');
    dots.className = 'wiki-gallery__dots';
    box.appendChild(dots);

    const arrow = (side, label) => {
        const button = doc.createElement('button');
        button.type = 'button';
        button.className = `wiki-gallery__arrow wiki-gallery__arrow--${side}`;
        button.setAttribute('aria-label', label);
        /* Шеврон рисуем SVG, а не знаком: у стрелок из юникода часть систем
           подставляет цветной эмодзи-глиф — раздел на этом уже обжигался на
           стрелках сортировки в таблицах. */
        button.innerHTML = side === 'prev' ? PREV_ICON : NEXT_ICON;
        box.appendChild(button);
        return button;
    };
    const prev = arrow('prev', 'Предыдущий кадр');
    const next = arrow('next', 'Следующий кадр');

    /* ПРОКРУТКА СВОИМИ РУКАМИ, а не scrollTo({behavior:'smooth'}).
       Замер на живой галерее: щелчок по стрелке не двигал ленту ВООБЩЕ —
       scrollLeft оставался нулём при максимуме 868. Причина в том, что
       scroll-snap-type: x mandatory возвращает ленту к текущему кадру в самом
       начале плавной анимации: снап и анимация тянут её в разные стороны, и
       побеждает снап. Присваивание scrollLeft снап не отменяет — оно
       мгновенное, и снапу нечего отматывать. Поэтому анимируем сами, а на
       время анимации снап выключаем, как и при перетаскивании. */
    let frame = 0;
    const stopAnimation = () => {
        if (!frame) return;
        cancelAnimationFrame(frame);
        frame = 0;
        strip.style.scrollSnapType = '';
    };
    undoLater.push(stopAnimation);

    const goTo = (index) => {
        const slide = slides[clampIndex(index, slides.length)];
        if (!slide) return;
        const to = scrollTargetFor(strip, slide);
        stopAnimation();
        const from = strip.scrollLeft;
        if (Math.abs(to - from) < 1) { sync(); return; }
        /* Уважаем системную настройку «меньше движения»: там анимация не
           украшение, а помеха. */
        const reduced = typeof window !== 'undefined' && window.matchMedia
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduced) {
            strip.scrollLeft = to;
            sync();
            return;
        }
        strip.style.scrollSnapType = 'none';
        const started = (typeof performance !== 'undefined' ? performance : Date).now();
        const step = () => {
            const passed = ((typeof performance !== 'undefined' ? performance : Date).now()
                            - started) / SCROLL_MS;
            const done = passed >= 1;
            strip.scrollLeft = from + (to - from) * ease(done ? 1 : passed);
            if (!done) { frame = requestAnimationFrame(step); return; }
            frame = 0;
            strip.style.scrollSnapType = '';
            sync();
        };
        frame = requestAnimationFrame(step);
    };

    const dotNodes = slides.map((_slide, index) => {
        const dot = doc.createElement('button');
        dot.type = 'button';
        dot.className = 'wiki-gallery__dot';
        dot.setAttribute('aria-label', `Кадр ${index + 1} из ${slides.length}`);
        dot.addEventListener('click', () => goTo(index));
        dots.appendChild(dot);
        return dot;
    });

    const sync = () => {
        const index = activeIndex(strip, slides);
        dotNodes.forEach((dot, at) => dot.setAttribute(
            'aria-current', at === index ? 'true' : 'false'));
        /* Подпись берём у КАДРА, а не у обёртки: alt живёт на картинке,
           обёртку поставила витрина и она пустая. */
        caption.textContent = frames[index]?.getAttribute('alt') || '';
        prev.disabled = index === 0;
        next.disabled = index === slides.length - 1;
        strip.setAttribute('aria-label', `Кадр ${index + 1} из ${slides.length}`);
    };

    const on = (node, event, handler, options) => {
        node.addEventListener(event, handler, options);
        undo.push(() => node.removeEventListener(event, handler, options));
    };

    on(prev, 'click', () => goTo(activeIndex(strip, slides) - 1));
    on(next, 'click', () => goTo(activeIndex(strip, slides) + 1));
    on(strip, 'scroll', sync, { passive: true });

    /* ПЕРЕСЧЁТ ПОСЛЕ ЗАГРУЗКИ КАДРА. До неё у картинки без width/height нет
       собственных размеров, offsetWidth равен нулю, и «какой кадр открыт»
       считается по нулям — снаружи это выглядит как «стрелки не работают». */
    frames.forEach((frame) => {
        if (frame.complete) return;
        on(frame, 'load', sync);
        on(frame, 'error', sync);
    });

    /* КЛАВИАТУРА: лента с фокусом листается стрелками. Браузер и сам прокрутит
       её на шаг, но шаг у него свой (примерно 40 пикселей), то есть кадр
       уезжает наполовину — а scroll-snap возвращает его назад, и получается
       дёрганье вместо листания. */
    on(strip, 'keydown', (event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        goTo(activeIndex(strip, slides) + (event.key === 'ArrowRight' ? 1 : -1));
    });

    /* ПЕРЕТАСКИВАНИЕ МЫШЬЮ. Пальцем лента листается сама, а на десктопе
       схватить её нечем: полоса прокрутки спрятана. Тянем на pointer-событиях,
       и только настоящее движение (порог DRAG_THRESHOLD) считаем
       перетаскиванием — иначе обычный щелчок превращался бы в микро-прокрутку.
       Касания НЕ перехватываем: там работает родная прокрутка с инерцией,
       и подменять её значило бы сделать хуже. */
    let drag = null;
    on(strip, 'pointerdown', (event) => {
        if (event.pointerType === 'touch' || event.button !== 0) return;
        drag = { x: event.clientX, left: strip.scrollLeft, moved: false };
    });
    on(strip, 'pointermove', (event) => {
        if (!drag) return;
        const shift = event.clientX - drag.x;
        if (!drag.moved && Math.abs(shift) < DRAG_THRESHOLD) return;
        drag.moved = true;
        /* Пока тащим, snap выключаем: с ним лента дёргается к ближайшему кадру
           на каждом движении мыши и за курсором не идёт. */
        strip.style.scrollSnapType = 'none';
        strip.classList.add('wiki-gallery--dragging');
        strip.scrollLeft = drag.left - shift;
    });
    const endDrag = () => {
        if (!drag) return;
        const moved = drag.moved;
        drag = null;
        strip.classList.remove('wiki-gallery--dragging');
        strip.style.scrollSnapType = '';
        /* Доводим до кадра сами: snap включается обратно уже после отпускания,
           и без доводки лента осталась бы стоять между кадрами. */
        if (moved) goTo(activeIndex(strip, slides));
    };
    on(strip, 'pointerup', endDrag);
    on(strip, 'pointercancel', endDrag);
    on(strip, 'pointerleave', endDrag);

    sync();
    /* Ещё раз на следующем кадре: к этому моменту браузер уже применил
       обязательный снап и восстановление позиции прокрутки, а они умеют
       сдвинуть ленту БЕЗ события scroll. Без этого на экране один кадр, а
       подпись и точка — от другого; ровно это и было видно на живой статье. */
    if (typeof requestAnimationFrame === 'function') {
        const settle = requestAnimationFrame(sync);
        undo.push(() => cancelAnimationFrame(settle));
    }
    /* Смена ширины ленты (свернули сайдбар, повернули телефон, сменили масштаб
       раздела) меняет и ширину кадра, и цели прокрутки. */
    if (typeof ResizeObserver === 'function') {
        const observer = new ResizeObserver(sync);
        observer.observe(strip);
        undo.push(() => observer.disconnect());
    }
    return () => undo.forEach((fn) => fn());
}

/** Оживить все галереи внутри узла. Возвращает функцию отмены для всех сразу. */
export function mountGalleries(root, doc = document) {
    if (!root) return () => {};
    const undo = Array.from(root.querySelectorAll('[data-wiki-block="gallery"]'))
        .map((strip) => mountGallery(strip, doc))
        .filter(Boolean);
    return () => undo.forEach((fn) => fn());
}

export default mountGalleries;
