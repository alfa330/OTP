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

/**
 * Прокрутить контейнер так, чтобы элемент оказался под шапкой.
 * offset — сколько пикселей оставить сверху (высота липкой шапки раздела).
 */
export function scrollToElement(target, offset = 88) {
    if (!target) return;
    const container = getScrollContainer(target);
    if (!container) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
    }
    const top = target.getBoundingClientRect().top
        - container.getBoundingClientRect().top
        + container.scrollTop
        - offset;
    container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
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
