/* Работа с прокруткой внутри портала.
 *
 * В OTP окно НЕ скроллится никогда: корневой каркас — `flex h-screen
 * overflow-hidden` (App.jsx), а прокручивается его второй ребёнок `.main-content`
 * с `overflow-y-auto`. Обращений к window.scrollY во всём src/ ноль — и это не
 * случайность, а следствие каркаса.
 *
 * Исходная вика написана в расчёте на прокрутку окна: гейт «дочитал статью до
 * конца» там считает
 *     window.innerHeight + window.scrollY >= documentElement.scrollHeight - 80
 * и вызывает проверку сразу после подписки. В нашем каркасе window.scrollY
 * всегда 0, а documentElement.scrollHeight равен высоте вьюпорта — условие
 * истинно с первого кадра. То есть при прямом переносе отметка «ознакомлен»
 * ставилась бы в момент ОТКРЫТИЯ статьи, без чтения.
 *
 * Готовой утилиты в проекте не было (closest('.main-content') не встречается
 * ни разу), поэтому она здесь.
 */

/** Ближайший прокручиваемый предок. Возвращает null, если такого нет. */
export function getScrollContainer(node) {
    if (!node || typeof node.closest !== 'function') return null;
    const main = node.closest('.main-content');
    if (main) return main;

    // Запасной путь: любой предок, который реально может прокручиваться.
    let current = node.parentElement;
    while (current && current !== document.body) {
        const { overflowY } = window.getComputedStyle(current);
        if ((overflowY === 'auto' || overflowY === 'scroll')
            && current.scrollHeight > current.clientHeight + 1) {
            return current;
        }
        current = current.parentElement;
    }
    return null;
}

/** Ближайший предок, который прокручивается ПО ГОРИЗОНТАЛИ. */
export function getHorizontalScroller(node) {
    let current = node && node.parentElement;
    while (current && current !== document.body) {
        const { overflowX } = window.getComputedStyle(current);
        if ((overflowX === 'auto' || overflowX === 'scroll')
            && current.scrollWidth > current.clientWidth + 1) {
            return current;
        }
        current = current.parentElement;
    }
    return null;
}

/**
 * Прокрутить контейнер так, чтобы элемент оказался под шапкой.
 * offset — сколько пикселей оставить сверху (высота липкой шапки раздела).
 *
 * Прокрутка ДВУМЕРНАЯ, и вторая половина появилась не для полноты: справочные
 * таблицы вики прокручиваются внутри своей обёртки, и совпадение в пятой колонке
 * из одиннадцати остаётся за правым краем. Вертикально страница доезжала до
 * нужной строки, а подсвеченное слово человек не видел — и это читается как «нет
 * перехода», хотя переход состоялся.
 */
export function scrollToElement(target, offset = 88) {
    if (!target) return;
    const container = getScrollContainer(target);
    if (!container) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
        const top = target.getBoundingClientRect().top
            - container.getBoundingClientRect().top
            + container.scrollTop
            - offset;
        container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }

    const box = getHorizontalScroller(target);
    if (!box) return;
    const targetBox = target.getBoundingClientRect();
    const boxBox = box.getBoundingClientRect();
    const hidden = targetBox.left < boxBox.left + 8 || targetBox.right > boxBox.right - 8;
    if (!hidden) return;
    // Оставляем треть ширины слева: слово у самого края читается хуже, а у
    // таблицы слева обычно стоит колонка-название, по которой человек и
    // ориентируется.
    const left = targetBox.left - boxBox.left + box.scrollLeft - box.clientWidth / 3;
    box.scrollTo({ left: Math.max(0, left), behavior: 'smooth' });
}

/* ── Прокрутка портала без узла-подсказки ────────────────────────────────────
 *
 * Функции выше отталкиваются от элемента: «прокрути так, чтобы БЫЛО ВИДНО вот
 * это». Смене экрана такой элемент взять неоткуда — она как раз и происходит в
 * тот момент, когда старого узла уже нет, а нового ещё нет. Поэтому здесь
 * скроллер берётся по тому же селектору напрямую.
 *
 * Понадобилось возврату из статьи в список. Открытие статьи прокрутку сбрасывает
 * само собой — карточка «Открываем статью…» схлопывает высоту страницы, и
 * браузер обрезает scrollTop до нуля; у возврата такого промежуточного
 * состояния нет (витрина уже в состоянии и рисуется тем же коммитом), и человек
 * попадал в список на той прокрутке, докуда дочитал статью, — то есть в самый
 * низ витрины.
 */

/** Прокручиваемый контейнер портала. null — каркас ещё не отрисован. */
export function getPortalScroller() {
    if (typeof document === 'undefined') return null;
    return document.querySelector('.main-content');
}

/** Где сейчас стоит прокрутка портала. 0 — если каркаса нет. */
export function portalScrollTop() {
    return getPortalScroller()?.scrollTop || 0;
}

/** Поставить прокрутку портала. Без анимации: это не переход по странице, а
 *  смена экрана — плавный проезд через всю ленту читался бы рывком. */
export function scrollPortalTo(top = 0) {
    const container = getPortalScroller();
    if (!container) return;
    container.scrollTo({ top: Math.max(0, Number(top) || 0), behavior: 'auto' });
}

/**
 * Дочитан ли контент до конца.
 *
 * threshold — запас снизу в пикселях. Возвращает false, если контейнера нет
 * или он ещё не измерен: неопределённость обязана трактоваться как «не дочитал»,
 * иначе гейт снова превратится в фикцию.
 */
export function isScrolledToEnd(container, threshold = 80) {
    if (!container) return false;
    const { scrollTop, clientHeight, scrollHeight } = container;
    if (!scrollHeight || !clientHeight) return false;
    // Контент короче экрана — прокручивать нечего, но и «дочитал» тут не
    // информативно: решение принимает вызывающий, мы честно говорим «да».
    if (scrollHeight <= clientHeight + 1) return true;
    return scrollTop + clientHeight >= scrollHeight - threshold;
}

/**
 * Подписка на прокрутку контейнера.
 * Возвращает функцию отписки. Слушатель пассивный — прокрутку не тормозит.
 */
export function observeScroll(container, handler) {
    if (!container || typeof handler !== 'function') return () => {};
    container.addEventListener('scroll', handler, { passive: true });
    return () => container.removeEventListener('scroll', handler);
}
