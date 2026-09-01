/* Галерея статьи: листание кадров одного действия. Счётная часть и обвязка.
 *
 * Вынесено из WikiArticle.jsx отдельным модулем не ради красоты — тем же
 * приёмом, что imageSize.js, articleLink.js и parkLogo.js: витрина тянет за
 * собой React, DOMPurify и половину раздела, то есть проверить листание
 * `node --test` без сборки нельзя, а ошибаться здесь есть где.
 *
 * ЭТОТ ЖЕ МОДУЛЬ ОЖИВЛЯЕТ ГАЛЕРЕЮ В РЕДАКТОРЕ. Раньше листание жило только на
 * витрине, а автор при создании статьи видел вместо карусели полосу кадров
 * своего размера: три вертикальных скриншота телефона (по 194 px в колонке
 * 820) спокойно вставали в ряд, scrollWidth равнялся clientWidth — листать
 * было НЕЧЕГО, хотя лента и обещала курсором-рукой, что её можно тянуть.
 * Автор собирал галерею вслепую и проверить её мог только сохранив статью и
 * открыв её заново. Поэтому обвязка строится функцией attachGallery, а кто
 * подаёт ей ленту — витрина (mountGalleries) или узел редактора
 * (galleryNodeView.js) — модулю безразлично.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * ЧЕТЫРЕ ЖЕСТА И КТО ЗА КАЖДЫЙ ОТВЕЧАЕТ
 *
 * ПАЛЕЦ — браузер. Лента с overflow-x и scroll-snap даёт настоящий свайп с
 * инерцией и остановкой ровно на кадре. Замер на эмуляции iPhone: свайп даже
 * в 60 px листает на кадр вперёд. Подменять это своей физикой значило бы
 * сделать хуже, поэтому касания мы не трогаем вовсе.
 *
 * ТРЕКПАД — мы. И вот почему это пришлось забрать себе. Двупальцевый свайп по
 * трекпаду (главный способ листать на маке) НЕ РАБОТАЛ СОВСЕМ: замер на живой
 * галерее — deltaX в 400 px при шаге кадра 808 оставлял scrollLeft РОВНО
 * НУЛЁМ. Виноват scroll-snap-type: x mandatory: после жеста браузер обязан
 * встать на ближайшую точку привязки, а 400 < 404 — это меньше половины
 * кадра, и лента возвращается туда, откуда уехала. Тот же замер со снятым
 * снапом даёт честные 400. То есть пролистать трекпадом можно было, только
 * проехав больше половины кадра ОДНИМ движением; всё остальное молча
 * откатывалось. Снаружи это и есть «не свайпается».
 *
 * МЫШЬ — мы, по той же причине и с той же болезнью: перетаскивание на 400 px
 * тоже откатывалось в ноль. Плюс своя беда — указатель не захватывался, и
 * длинная тяга обрывалась в тот момент, когда курсор уходил с ленты
 * (pointerleave), то есть почти всегда.
 *
 * КЛАВИАТУРА — мы: у браузера свой шаг около 40 px, кадр уезжает наполовину,
 * а снап возвращает его назад, и получается дёрганье вместо листания.
 *
 * ПРАВИЛО ЛИСТАНИЯ (pageTarget) взято у iOS, а не у CSS. Снап знает одно
 * условие — «проехал больше половины». Человек ждёт другого: короткий, но
 * быстрый бросок листает, медленное подталкивание на пятую часть кадра тоже
 * листает, а случайное дрожание в паре пикселей — нет. Поэтому решение
 * принимается по СКОРОСТИ в конце жеста и по ПРОЙДЕННОМУ ПУТИ, и только
 * если жест уехал дальше соседнего кадра — по тому, что видно на экране.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * ТРИ ОШИБКИ, ИЗ КОТОРЫХ ЭТОТ ФАЙЛ КОГДА-ТО ПОЯВИЛСЯ (и которые сторожат
 * тесты, поэтому они здесь описаны, а не забыты):
 *
 * 1. РАЗМЕРЫ ДО ЗАГРУЗКИ. У <img> без атрибутов width/height до загрузки нет
 *    собственных размеров: offsetWidth равен нулю, и вся арифметика «какой
 *    кадр открыт» и «куда прокрутить» считается по нулям. Лечится пересчётом
 *    по событию load каждой картинки.
 *
 * 2. НАТИВНОЕ ПЕРЕТАСКИВАНИЕ КАРТИНКИ. Браузер по умолчанию тащит <img> как
 *    файл, и это перебивает нашу тягу ленты: курсор уезжает с призраком
 *    картинки, лента стоит. Поэтому кадрам draggable=false.
 *
 * 3. ПЛАВНАЯ ПРОКРУТКА БРАУЗЕРОМ проигрывает снапу: анимация и снап тянут
 *    ленту в разные стороны, и побеждает снап. Анимируем сами, присваивая
 *    scrollLeft, а на время анимации снап выключаем.
 */

/* Пикселей, после которых движение мыши считается перетаскиванием, а не
   щелчком. Без порога любой клик по кадру превращался бы в микро-прокрутку, и
   ссылка внутри подписи перестала бы нажиматься. */
export const DRAG_THRESHOLD = 4;

/* Длительность доводки до кадра. 260 мс — столько же, сколько у остальных
   переходов раздела; заметно, но не заставляет ждать. */
export const SCROLL_MS = 260;

/* Быстрый бросок листает всегда, как бы коротко он ни был, — px/мс.
   0.35 ≈ 350 px/с: медленное подталкивание пальцем по трекпаду сюда не
   попадает, а короткий резкий свайп попадает уверенно. */
export const FLICK_SPEED = 0.35;

/* Доля кадра, после которой медленный жест всё-таки листает. Пятая часть, а
   не половина: половину требует снап, и именно из-за неё казалось, что лента
   не двигается вовсе. */
export const PAGE_RATIO = 0.2;

/* Тишина, после которой жест трекпада считается законченным. Колесо не
   сообщает об окончании, событий нет — есть только пауза между ними. 90 мс
   больше промежутка внутри одного свайпа (там 8–16 мс) и меньше паузы между
   двумя осознанными свайпами. */
export const WHEEL_SETTLE_MS = 90;

/* Замедление к концу. Линейное движение читается как рывок: лента трогается и
   встаёт мгновенно, и глазу не за что зацепиться. Кривая та же по смыслу, что
   у переходов iOS: почти весь путь проходится в первой трети времени. */
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

/** Шаг между кадрами: ширина кадра вместе с промежутком.
 *
 * Меряется по СОСЕДЯМ, а не по ширине ленты: кадры бывают разной ширины, и
 * «сколько нужно проехать, чтобы сменился кадр» — это расстояние между их
 * началами, а не clientWidth. Один кадр — шагом считаем ленту целиком. */
export const slideStepOf = (strip, slides, index) => {
    const at = clampIndex(index, slides.length);
    const here = slides[at];
    if (!here) return strip.clientWidth || 0;
    /* Сосед берётся БЕЗ зажима в границы: зажатый индекс на последнем кадре
       вернул бы сам этот кадр, расстояние вышло бы нулевым, и шагом стала бы
       ширина кадра вместо расстояния между ними. У последнего кадра сосед —
       предыдущий, и это тот же шаг. */
    const other = slides[at + 1] || slides[at - 1];
    if (other && other !== here) return Math.abs(other.offsetLeft - here.offsetLeft);
    return here.offsetWidth || strip.clientWidth || 0;
};

/** Куда листать по итогам жеста — правило iOS, а не правило снапа.
 *
 * index  — кадр, НА КОТОРОМ жест начался;
 * landed — кадр, к которому лента ближе всего в момент отпускания;
 * moved  — насколько уехала лента за жест (вправо — положительно);
 * speed  — скорость в конце жеста, px/мс (вправо — положительно);
 * step   — ширина кадра вместе с промежутком.
 *
 * Порядок разбора важен. Если жест уехал ДАЛЬШЕ соседнего кадра, спорить с
 * глазами нельзя: человек видит третий кадр и ждёт третий, а не «шаг вперёд
 * от первого». И только когда лента осталась в пределах исходного кадра,
 * включается правило броска: быстро — листаем, далеко — листаем, чуть-чуть и
 * медленно — остаёмся (иначе дрожание руки на щелчке листало бы кадр).
 */
export const pageTarget = ({ index, landed, count, moved, speed, step }) => {
    if (!count) return 0;
    const from = clampIndex(index, count);
    const seen = clampIndex(landed, count);
    if (seen !== from) return seen;
    const fast = Math.abs(speed) >= FLICK_SPEED;
    const far = step > 0 && Math.abs(moved) >= step * PAGE_RATIO;
    if (!fast && !far) return from;
    return clampIndex(from + ((fast ? speed : moved) > 0 ? 1 : -1), count);
};

const PREV_ICON = '<svg width="9" height="15" viewBox="0 0 9 15" fill="none" aria-hidden="true">'
    + '<path d="M7.5 1.5 2 7.5l5.5 6" stroke="currentColor" stroke-width="1.8"'
    + ' stroke-linecap="round" stroke-linejoin="round"/></svg>';
const NEXT_ICON = '<svg width="9" height="15" viewBox="0 0 9 15" fill="none" aria-hidden="true">'
    + '<path d="M1.5 1.5 7 7.5l-5.5 6" stroke="currentColor" stroke-width="1.8"'
    + ' stroke-linecap="round" stroke-linejoin="round"/></svg>';

/* Больше восьми точек читаются как рябь, а не как «сколько осталось». Столько
   же показывает и iOS, дальше переходя на счётчик. */
const MAX_DOTS = 8;

const now = () => (typeof performance !== 'undefined' ? performance : Date).now();

/** Масштаб, в котором нарисован узел (zoom на .wiki-scope, wiki-scale.css).
 *
 * Нужен ровно одному месту — переводу движения РУКИ в движение ЛЕНТЫ. Курсор и
 * колесо приходят в координатах экрана, то есть УЖЕ умноженными на масштаб, а
 * scrollLeft живёт в собственных единицах узла, где масштаба нет. Смешаешь их —
 * и на большом мониторе (а раздел увеличивается именно там, до 1.35) лента
 * обгоняет курсор на треть: тянешь на кадр, уезжает на полтора. Ощущение прямо
 * противоположное iOS, где содержимое приклеено к пальцу.
 *
 * currentCSSZoom знают не все браузеры, поэтому есть запасной путь — отношение
 * экранной ширины к раскладочной. Оно даёт ровно то же число. */
const zoomOf = (node) => {
    const own = node.currentCSSZoom;
    if (typeof own === 'number' && own > 0) return own;
    const shown = node.getBoundingClientRect?.().width || 0;
    const laid = node.offsetWidth || 0;
    return shown && laid ? shown / laid : 1;
};

const reducedMotion = () => (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
);

/** Обвязка вокруг готовой ленты: стрелки, точки, подпись и все четыре жеста.
 *
 * strip — элемент с overflow-x, внутри которого лежат кадры.
 * box   — элемент, ВНУТРИ которого рисуется обвязка. У витрины его строит
 *         mountGallery, у редактора — узел схемы: там обёртку нельзя создать
 *         на лету, её обязан вернуть nodeView.
 * slidesOf — как достать текущие кадры. Функция, а не массив: в редакторе
 *         состав ленты меняет ProseMirror при каждой правке, и запомненный
 *         массив указывал бы на выброшенные узлы.
 * keys, drag — забирать ли себе клавиши и перетаскивание мышью. При чтении
 *         оба нужны, в редакторе оба вредны: стрелками там ходят по тексту, а
 *         тяга мышью — это выделение. Трекпад и кнопки работают везде.
 *
 * Возвращает { undo, sync }.
 */
export function attachGallery(strip, {
    box, doc = document, slidesOf, keys = true, drag: allowDrag = true,
}) {
    const undo = [];
    const on = (node, event, handler, options) => {
        node.addEventListener(event, handler, options);
        undo.push(() => node.removeEventListener(event, handler, options));
    };

    const slides = () => slidesOf();
    const frameOf = (slide) => (slide?.tagName === 'IMG' ? slide : slide?.querySelector('img'));

    /* Роль и словесное имя нужны читалке с экрана: без них она назовёт ленту
       просто группой. Фокус (tabindex) выдаётся ниже и только там, где мы
       забираем клавиши, — фокусируемая лента, которая на стрелки не отвечает,
       была бы ловушкой для клавиатуры. */
    strip.setAttribute('role', 'group');
    strip.setAttribute('aria-roledescription', 'галерея');

    const caption = doc.createElement('p');
    caption.className = 'wiki-gallery__caption';
    /* Подпись меняется от жеста, а не от перехода по странице: читалка обязана
       её проговорить, но не перебивая — отсюда polite. */
    caption.setAttribute('aria-live', 'polite');

    const dots = doc.createElement('div');
    dots.className = 'wiki-gallery__dots';

    const counter = doc.createElement('span');
    counter.className = 'wiki-gallery__counter';

    const arrow = (side, label) => {
        const button = doc.createElement('button');
        button.type = 'button';
        button.className = `wiki-gallery__arrow wiki-gallery__arrow--${side}`;
        button.setAttribute('aria-label', label);
        /* Шеврон рисуем SVG, а не знаком: у стрелок из юникода часть систем
           подставляет цветной эмодзи-глиф — раздел на этом уже обжигался на
           стрелках сортировки в таблицах. */
        button.innerHTML = side === 'prev' ? PREV_ICON : NEXT_ICON;
        /* В редакторе обвязка лежит внутри contenteditable. Без этого браузер
           ставит в кнопку каретку, а ProseMirror считает её текстом статьи. */
        button.setAttribute('contenteditable', 'false');
        return button;
    };
    const prev = arrow('prev', 'Предыдущий кадр');
    const next = arrow('next', 'Следующий кадр');

    /* СЦЕНА — отдельная обёртка вокруг одной только ленты.
     *
     * Без неё стрелки позиционируются от всей галереи, а в неё входят ещё
     * подпись и точки: кнопки уезжают ниже середины кадра тем сильнее, чем
     * длиннее подпись, и на двухстрочной подписи оказываются уже на её тексте.
     * Считать середину скриптом нельзя — она меняется при каждом листании
     * вместе с высотой подписи. Сцена решает это раскладкой и навсегда. */
    const stage = doc.createElement('div');
    stage.className = 'wiki-gallery__stage';
    strip.replaceWith(stage);
    stage.appendChild(strip);
    stage.append(prev, next, counter);
    undo.push(() => { stage.replaceWith(strip); });

    const chrome = doc.createElement('div');
    chrome.className = 'wiki-gallery__chrome';
    chrome.setAttribute('contenteditable', 'false');
    chrome.append(caption, dots);
    box.appendChild(chrome);
    undo.push(() => chrome.remove());

    /* ПРОКРУТКА СВОИМИ РУКАМИ, а не плавная прокрутка браузером: снап
       возвращает ленту к текущему кадру в самом начале анимации, и побеждает
       снап. Присваивание scrollLeft снап не отменяет — оно мгновенное, и
       снапу нечего отматывать. Поэтому анимируем сами, а на время анимации
       снап выключаем, как и при перетаскивании. */
    let raf = 0;
    const stopAnimation = () => {
        if (!raf) return;
        cancelAnimationFrame(raf);
        raf = 0;
        strip.style.scrollSnapType = '';
    };
    undo.push(stopAnimation);

    const goTo = (index) => {
        const list = slides();
        const slide = list[clampIndex(index, list.length)];
        if (!slide) return;
        const to = scrollTargetFor(strip, slide);
        stopAnimation();
        const from = strip.scrollLeft;
        if (Math.abs(to - from) < 1) { sync(); return; }
        /* Уважаем системную настройку «меньше движения»: там анимация не
           украшение, а помеха. */
        if (reducedMotion()) {
            strip.scrollLeft = to;
            sync();
            return;
        }
        strip.style.scrollSnapType = 'none';
        const started = now();
        const step = () => {
            const passed = (now() - started) / SCROLL_MS;
            const done = passed >= 1;
            strip.scrollLeft = from + (to - from) * ease(done ? 1 : passed);
            if (!done) { raf = requestAnimationFrame(step); return; }
            raf = 0;
            strip.style.scrollSnapType = '';
            sync();
        };
        raf = requestAnimationFrame(step);
    };

    /* Точки перестраиваются, а не создаются раз навсегда: в редакторе автор
       добавляет и убирает кадры прямо во время правки. */
    let dotNodes = [];
    const buildDots = (count) => {
        if (dotNodes.length === count) return;
        dots.textContent = '';
        dotNodes = Array.from({ length: count }, (_unused, index) => {
            const dot = doc.createElement('button');
            dot.type = 'button';
            dot.className = 'wiki-gallery__dot';
            dot.setAttribute('contenteditable', 'false');
            dot.setAttribute('aria-label', `Кадр ${index + 1} из ${count}`);
            dots.appendChild(dot);
            return dot;
        });
    };

    /* ЛЕНТА ПОДЖИМАЕТСЯ ПОД КАДР.
     *
     * Кадр обязан занимать всю ширину ленты, иначе два вертикальных скриншота
     * встают в колонку РЯДОМ и листать нечего. Но у скриншота телефона
     * (499x1080) при высоте ленты в 26rem ширина выходит 194 px — в колонке
     * статьи на 820 это 600 px пустого фона вокруг узкой полоски, и галерея
     * читается как поломка вёрстки, а не как карусель.
     *
     * Поэтому ширину ленте задаёт САМЫЙ ШИРОКИЙ кадр: узкие кадры дают
     * узкую аккуратную карусель по центру колонки, широкие — прежнюю во всю
     * ширину. Меряется offsetWidth, а не rect: у раздела свой масштаб.
     */
    const hug = () => {
        /* Мерить ОБЯЗАТЕЛЬНО на распущенной ленте. Кадр во всю ширину ленты
           (flex-basis: 100 %) — значит его размер зависит от ширины ленты, а
           ширину ленты мы этим же замером и собираемся задать. Не сними мы
           прошлое ограничение, широкий скриншот на каждом пересчёте становился
           бы на пару десятков пикселей у́же — и за десяток загрузок сходился бы
           в полоску. Поэтому сначала отпускаем, потом меряем. */
        box.style.removeProperty('max-width');
        const list = slides();
        let widest = 0;
        let slideWidth = 0;
        list.forEach((slide) => {
            const frame = frameOf(slide);
            widest = Math.max(widest, frame ? frame.offsetWidth : 0);
            slideWidth = Math.max(slideWidth, slide.offsetWidth || 0);
        });
        /* Ничего ещё не загрузилось — не поджимаем: подставить сюда догадку
           значит один раз промахнуться на всю ширину колонки. */
        if (!widest || !slideWidth) return;
        /* Разница между лентой и кадром — это её собственные поля. Считаем её,
           а не берём из CSS: значение живёт в wiki-blocks.css и однажды там
           поменяется, а замер верен всегда. */
        const padding = box.offsetWidth - slideWidth;
        const width = Math.round(widest + Math.max(padding, 0));
        /* КУДА СТАВИТЬ СТРЕЛКИ, решает свободное место, а не вкус. Поверх
           кадра они закрывают часть инструкции — на узком скриншоте телефона
           две кнопки по 32 px съедают почти треть его ширины. Но у поджатой
           галереи по бокам остаётся пустая колонка, и там кнопкам самое место:
           ничего не закрывают и стоят как в macOS. Считаем, влезут ли. */
        const room = box.parentElement?.clientWidth || 0;
        box.classList.toggle('wiki-gallery--roomy', room - width >= 112);
        box.style.maxWidth = `${width}px`;
    };

    const sync = () => {
        const list = slides();
        const count = list.length;
        const index = activeIndex(strip, list);
        buildDots(count);
        dotNodes.forEach((dot, at) => dot.setAttribute(
            'aria-current', at === index ? 'true' : 'false'));
        /* Подпись берём у КАДРА, а не у обёртки: alt живёт на картинке,
           обёртку поставила витрина и она пустая. */
        caption.textContent = frameOf(list[index])?.getAttribute('alt') || '';
        counter.textContent = count > MAX_DOTS ? `${index + 1} / ${count}` : '';
        box.classList.toggle('wiki-gallery--many', count > MAX_DOTS);
        /* Гаснущая кнопка не имеет права утащить с собой фокус: у
           недоступной кнопки его отбирает браузер, и человек, листавший
           с клавиатуры, оказывается в начале страницы. Передаём фокус
           соседней — там листание и продолжится. */
        const disable = (button, off, neighbour) => {
            if (off && !button.disabled && doc.activeElement === button) neighbour.focus();
            button.disabled = off;
        };
        disable(prev, index === 0, next);
        disable(next, index === count - 1, prev);
        /* Кнопок нет смысла показывать там, где им некуда листать: один кадр —
           это ещё не карусель. В редакторе такое состояние обычное: автор
           только что вставил галерею и кладёт в неё первый кадр. */
        box.classList.toggle('wiki-gallery--single', count < 2);
        strip.setAttribute('aria-label', `Кадр ${index + 1} из ${count}`);
    };

    const step = (delta) => goTo(activeIndex(strip, slides()) + delta);

    /* Один слушатель на весь ряд точек, а не по одному на точку. Точки
       пересобираются при каждой смене числа кадров (в редакторе — постоянно), и
       слушатель на каждой пришлось бы либо снимать вручную, либо копить в
       списке отмены без края. Заодно это единственное место, где слушатель мог
       бы уехать мимо помощника on() и пережить отмену. */
    on(dots, 'click', (event) => {
        const dot = event.target?.closest?.('.wiki-gallery__dot');
        const at = dot ? dotNodes.indexOf(dot) : -1;
        if (at >= 0) goTo(at);
    });

    on(prev, 'click', () => step(-1));
    on(next, 'click', () => step(1));
    on(strip, 'scroll', sync, { passive: true });

    /* КЛАВИАТУРА: лента с фокусом листается стрелками, Home и End прыгают на
       края. Браузер и сам прокрутит ленту на шаг, но шаг у него свой
       (примерно 40 пикселей), то есть кадр уезжает наполовину — а снап
       возвращает его назад, и получается дёрганье вместо листания.

       В редакторе клавиши не перехватываются вовсе: там стрелка — это
       движение каретки по тексту, и отнять её значило бы запереть курсор
       внутри галереи. */
    if (keys) {
        strip.setAttribute('tabindex', '0');
        undo.push(() => strip.removeAttribute('tabindex'));
        on(strip, 'keydown', (event) => {
            const list = slides();
            if (event.key === 'ArrowLeft') { event.preventDefault(); step(-1); return; }
            if (event.key === 'ArrowRight') { event.preventDefault(); step(1); return; }
            if (event.key === 'Home') { event.preventDefault(); goTo(0); return; }
            if (event.key === 'End') { event.preventDefault(); goTo(list.length - 1); }
        });
    }

    /* ── ЖЕСТ: общая часть тяги и трекпада ──────────────────────────────────
       Оба жеста устроены одинаково: снап на время выключается, лента едет за
       рукой, а в конце мы сами решаем, на каком кадре встать (pageTarget).
       Разница только в том, чем измеряется движение. */
    let gesture = null;
    const beginGesture = () => {
        stopAnimation();
        const list = slides();
        const index = activeIndex(strip, list);
        strip.style.scrollSnapType = 'none';
        gesture = {
            index,
            step: slideStepOf(strip, list, index),
            from: strip.scrollLeft,
            at: strip.scrollLeft,
            time: now(),
            speed: 0,
        };
    };
    const trackGesture = (scrollLeft) => {
        if (!gesture) return;
        const moment = now();
        const passed = moment - gesture.time;
        if (passed > 0) {
            /* Скорость сглаживается, а не берётся с последнего события: у
               трекпада хвост жеста часто состоит из мелких добавок, и по
               последней из них любой свайп выглядел бы остановившимся. */
            const raw = (scrollLeft - gesture.at) / passed;
            gesture.speed = gesture.speed * 0.6 + raw * 0.4;
            gesture.time = moment;
        }
        gesture.at = scrollLeft;
    };
    const endGesture = () => {
        if (!gesture) return;
        const done = gesture;
        gesture = null;
        strip.classList.remove('wiki-gallery--dragging');
        const list = slides();
        goTo(pageTarget({
            index: done.index,
            landed: activeIndex(strip, list),
            count: list.length,
            moved: strip.scrollLeft - done.from,
            speed: done.speed,
            step: done.step,
        }));
    };
    undo.push(() => { gesture = null; });

    /* ТРЕКПАД. Двупальцевый свайп по маку приходит колесом с deltaX, и без
       перехвата снап откатывает его целиком (замер: 400 px жеста → scrollLeft
       остаётся нулём). Вертикальное колесо не трогаем: над горизонтальной
       лентой оно обязано прокручивать СТРАНИЦУ, иначе читатель застревает в
       галерее и не может уйти из неё вниз. */
    let settle = 0;
    const clearSettle = () => { if (settle) { clearTimeout(settle); settle = 0; } };
    undo.push(clearSettle);
    on(strip, 'wheel', (event) => {
        if (Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
        const list = slides();
        if (list.length < 2) return;
        event.preventDefault();
        if (!gesture) beginGesture();
        strip.scrollLeft += event.deltaX / zoomOf(strip);
        trackGesture(strip.scrollLeft);
        clearSettle();
        settle = setTimeout(endGesture, WHEEL_SETTLE_MS);
    }, { passive: false });

    /* ПЕРЕТАСКИВАНИЕ МЫШЬЮ. Пальцем лента листается сама, а на десктопе
       схватить её нечем: полоса прокрутки спрятана. Касания НЕ перехватываем —
       там работает родная прокрутка с инерцией, и подменять её значило бы
       сделать хуже. */
    let drag = null;
    let dragged = false;
    on(strip, 'pointerdown', (event) => {
        if (!allowDrag || event.pointerType === 'touch' || event.button !== 0) return;
        if (slides().length < 2) return;
        drag = { x: event.clientX, left: strip.scrollLeft, id: event.pointerId, moved: false };
    });
    on(strip, 'pointermove', (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        const shift = (event.clientX - drag.x) / zoomOf(strip);
        if (!drag.moved && Math.abs(shift) < DRAG_THRESHOLD) return;
        if (!drag.moved) {
            drag.moved = true;
            /* ЗАХВАТ УКАЗАТЕЛЯ. Без него длинная тяга обрывалась ровно тогда,
               когда курсор уходил с ленты, — а уходит он почти всегда: тянут
               на пол-экрана, а лента высотой в кадр. */
            try { strip.setPointerCapture(drag.id); } catch (error) { /* не судьба */ }
            strip.classList.add('wiki-gallery--dragging');
            beginGesture();
        }
        strip.scrollLeft = drag.left - shift;
        trackGesture(strip.scrollLeft);
    });
    const endDrag = (event) => {
        if (!drag || (event && event.pointerId !== drag.id)) return;
        const id = drag.id;
        const moved = drag.moved;
        drag = null;
        try { strip.releasePointerCapture(id); } catch (error) { /* уже отпущен */ }
        /* Щелчок без движения жестом не считается: иначе клик по кадру
           перелистывал бы галерею, а ссылка в подписи перестала бы нажиматься. */
        if (moved) { dragged = true; endGesture(); } else gesture = null;
    };
    on(strip, 'pointerup', endDrag);
    on(strip, 'pointercancel', endDrag);
    /* Щелчок, которым КОНЧИЛАСЬ тяга, гасим. Браузер шлёт его после
       pointerup, и без этого перетаскивание за кадр со ссылкой внутри
       открывало бы ссылку — то есть тяга уводила бы со страницы. */
    on(strip, 'click', (event) => {
        if (!dragged) return;
        dragged = false;
        event.preventDefault();
        event.stopPropagation();
    }, true);
    /* pointerleave НЕ подписан намеренно: с захватом указателя события
       продолжают приходить и за пределами ленты, а отмена по уходу курсора и
       была той причиной, по которой тяга обрывалась на полпути. */

    /* ПЕРЕСЧЁТ ПОСЛЕ ЗАГРУЗКИ КАДРА. До неё у картинки без width/height нет
       собственных размеров, offsetWidth равен нулю, и «какой кадр открыт»
       считается по нулям — снаружи это выглядит как «стрелки не работают».
       Слушатель вешается на ЛЕНТУ с перехватом: load у картинок не всплывает,
       а состав кадров в редакторе меняется на каждой правке, и подписываться
       на каждую картинку по отдельности значило бы делать это заново. */
    const refresh = () => { hug(); sync(); };
    on(strip, 'load', refresh, true);
    on(strip, 'error', refresh, true);

    /* СОСТАВ ЛЕНТЫ МЕНЯЕТСЯ УЖЕ ПОСЛЕ ТОГО, КАК ОБВЯЗКА ПОСТРОЕНА, и в
       редакторе это норма, а не исключение: кадры туда кладёт ProseMirror, а
       сами картинки дорисовывает React своим узлом — к первому пересчёту ленты
       в ней ещё пусто. Замер на стенде: без этого наблюдателя галерея из трёх
       кадров так и оставалась во всю ширину колонки, потому что картинки были
       в кеше, события load не случилось, а размер самой ленты не менялся.

       Пересчёт откладывается до следующего кадра: правка текста внутри
       галереи даёт десятки изменений подряд, и считать на каждом незачем. */
    let queued = 0;
    const soon = () => {
        if (queued || typeof requestAnimationFrame !== 'function') return;
        queued = requestAnimationFrame(() => { queued = 0; refresh(); });
    };
    undo.push(() => { if (queued) cancelAnimationFrame(queued); });
    if (typeof MutationObserver === 'function') {
        const watcher = new MutationObserver(soon);
        watcher.observe(strip, { childList: true, subtree: true });
        undo.push(() => watcher.disconnect());
    }

    refresh();
    /* Ещё раз на следующем кадре: к этому моменту браузер уже применил
       обязательный снап и восстановление позиции прокрутки, а они умеют
       сдвинуть ленту БЕЗ события scroll. Без этого на экране один кадр, а
       подпись и точка — от другого. */
    if (typeof requestAnimationFrame === 'function') {
        const later = requestAnimationFrame(refresh);
        undo.push(() => cancelAnimationFrame(later));
    }
    /* Смена ширины ленты (свернули сайдбар, повернули телефон, сменили масштаб
       раздела) меняет и ширину кадра, и цели прокрутки.

       Наблюдаем ДВА элемента и по-разному. За лентой следит sync: её ширину
       меняем в том числе мы сами, поджимая галерею, и запускать оттуда новый
       пересчёт ширины значило бы завести круг. За колонкой статьи — refresh:
       её ширину мы не трогаем, значит круга нет, а поджимать под новую колонку
       обязаны. */
    if (typeof ResizeObserver === 'function') {
        const inner = new ResizeObserver(() => sync());
        inner.observe(strip);
        undo.push(() => inner.disconnect());
        const room = box.parentElement;
        if (room) {
            const outer = new ResizeObserver(() => refresh());
            outer.observe(room);
            undo.push(() => outer.disconnect());
        }
    }

    return {
        /* Обвязку отдаём наружу: узлу редактора надо отличать «щёлкнули по
           стрелке» от «поставили курсор в текст», а по DOM это единственный
           надёжный признак. */
        chrome,
        sync: refresh,
        undo: () => {
            undo.forEach((fn) => fn());
            box.style.removeProperty('max-width');
            strip.style.removeProperty('scroll-snap-type');
            strip.removeAttribute('tabindex');
            strip.removeAttribute('role');
            strip.removeAttribute('aria-roledescription');
            strip.removeAttribute('aria-label');
            strip.classList.remove('wiki-gallery--dragging');
        },
    };
}

/** Оживить одну галерею при ЧТЕНИИ. Возвращает функцию отмены (или null, если
 * оживлять нечего).
 *
 * strip — сам <div data-wiki-block="gallery"> из тела статьи. Обвязка строится
 * ВОКРУГ него и в базу не попадает: тело статьи обязано остаться разметкой,
 * которую можно править руками.
 *
 * ПОВТОРНЫЙ МОНТАЖ — НОРМА, А НЕ ОШИБКА, и на этом галерея однажды уже умерла.
 * Эффект витрины висит на [safeHtml, bodyReady]: тело появляется в DOM раньше,
 * чем поднимается bodyReady, поэтому на КАЖДОМ открытии статьи React делает
 * монтаж, отмену и монтаж ещё раз — на том же самом DOM. Прежний сторож искал
 * следы прошлой сборки («лента уже лежит в .wiki-gallery») и на втором заходе
 * отказывался работать, а прежняя отмена эти следы не убирала: обвязка
 * оставалась нарисованной, но БЕЗ ЕДИНОГО ОБРАБОТЧИКА. Снаружи это выглядело
 * как «стрелки не нажимаются, мышью не тянется, подпись врёт» — то есть как
 * поломка ровно того, ради чего галерея и делалась.
 *
 * Поэтому здесь два правила. Первое: признак — не следы в DOM, а метка на
 * самом узле, и при повторном заходе прошлая сборка честно разбирается.
 * Второе: отмена ОБРАТНА монтажу и возвращает ленту туда, где взяла.
 */
export function mountGallery(strip, doc = document) {
    if (!strip) return null;
    if (typeof strip.__wikiGallery === 'function') strip.__wikiGallery();

    /* Кадры считаем ДО всякой правки разметки: от их числа зависит, надо ли
       вообще что-то трогать. Галерея без единой картинки — это ещё не галерея,
       а место под неё: там лежит подсказка из шаблона вставки, и разбирать её
       незачем, автор просто не успел положить кадры.

       А вот ОДИН кадр разбираем как все. Карусели ему не нужно (стрелки,
       которым некуда листать, и одна точка — это шум, а не управление; их
       гасит класс wiki-gallery--single), но поджатие под ширину кадра и подпись
       нужны ему ровно так же. Прежде такая галерея просто выпадала из обработки
       и оставалась серой полосой во всю колонку с узким скриншотом посередине —
       а приезжает она так из импорта, где кадр у шага бывает и один. */
    if (!strip.querySelector('img')) return null;

    /* ЧИСТКА ЛЕНТЫ. Прямые потомки, а не querySelectorAll('p'): вложенный
       абзац (в подписи, в списке) — это содержимое кадра, а не кадр.
     *
     * Абзацы берутся здесь потому, что галерея приезжает в них после правки
     * через ИИ: маркер картинки защищается вместе с абзацем (wiki/ai), и назад
     * картинка возвращается ВНУТРЬ <p>. Открой такую статью в редакторе и
     * сохрани — картинка уедет из абзаца (в схеме она блок), а сам абзац
     * останется в ленте ПУСТЫМ и станет кадром-призраком: пустая рамка, лишняя
     * точка и остановка ленты на ничём.
     *
     * Три случая и три разных ответа:
     *   абзац только с картинками — разворачиваем, причём ВСЕ картинки. Раньше
     *     бралась `querySelector('img')`, то есть первая, и остальные кадры
     *     молча пропадали из статьи (замер: три кадра превращались в два);
     *   абзац с текстом и картинкой — оставляем целым: это кадр с подписью;
     *   абзац без картинок — убираем: это либо призрак, либо текст-рыба из
     *     шаблона вставки («Перетащите сюда 2–3 кадра»), который автор забыл
     *     заменить, а читателю он показывается первым кадром галереи. */
    Array.from(strip.children).forEach((child) => {
        if (child.tagName !== 'P') return;
        const inner = Array.from(child.querySelectorAll('img'));
        if (!inner.length) { child.remove(); return; }
        if (child.textContent.trim()) return;
        child.replaceWith(...inner);
    });

    /* Каждый кадр — в обёртку на всю ширину ленты. Без неё кадры стоят своим
       размером, и два вертикальных скриншота телефона просто влезают в колонку
       РЯДОМ: листать нечего, а стрелки двигают то, что и так целиком видно.
       Растянуть сам <img> нельзя — flex-basis у картинки растягивает КАРТИНКУ,
       и скриншот расплывается во всю колонку. */
    Array.from(strip.children).forEach((child) => {
        const slide = doc.createElement('div');
        slide.className = 'wiki-gallery__slide';
        child.replaceWith(slide);
        slide.appendChild(child);
    });
    /* Браузер тащит картинку как файл и перебивает наше перетаскивание ленты:
       курсор уезжает с призраком кадра, лента стоит. */
    strip.querySelectorAll('img').forEach((frame) => frame.setAttribute('draggable', 'false'));

    const box = doc.createElement('div');
    box.className = 'wiki-gallery';
    strip.replaceWith(box);
    box.appendChild(strip);

    const { undo } = attachGallery(strip, {
        box,
        doc,
        slidesOf: () => Array.from(strip.children),
    });

    const teardown = () => {
        delete strip.__wikiGallery;
        undo();
        Array.from(strip.children).forEach((slide) => {
            if (!slide.classList?.contains('wiki-gallery__slide')) return;
            slide.replaceWith(...Array.from(slide.childNodes));
        });
        box.replaceWith(strip);
    };
    strip.__wikiGallery = teardown;
    return teardown;
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
