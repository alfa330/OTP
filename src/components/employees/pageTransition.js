/*
 * Переход между списком «Учета сотрудников» и страницей сотрудника — как смена
 * экрана в навигации iOS/macOS: прежняя страница уезжает, новая въезжает, ОБЕ
 * видны одновременно и только в пределах поля раздела (сайдбар стоит).
 *
 * Держится на View Transitions: браузер снимает кадр поля ДО смены, React
 * меняет разметку синхронно (flushSync), и браузер анимирует два кадра —
 * «было» и «стало». Одной CSS-анимацией на входящем экране так не сделать:
 * уходящая страница к этому моменту уже размонтирована, и вместо сдвига
 * получалось «исчезло — проявилось».
 *
 * Где View Transitions нет (Firefox) или человек просил меньше движения —
 * смена без переходов браузера, а страница проявляется своей CSS-анимацией
 * (см. usesViewTransitions в EmployeeCardPage и App.jsx).
 *
 * Стили переходов — в employee-card-page.css: html[data-ecp-nav] даёт полю
 * раздела имя view-transition-name только на время перехода.
 */
import { flushSync } from 'react-dom';

export const usesViewTransitions = () => {
    if (typeof document === 'undefined' || typeof document.startViewTransition !== 'function') return false;
    try {
        return !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (error) {
        return true;
    }
};

/** direction: 'forward' — вглубь (список → человек → правка), 'back' — назад. */
export const runPageTransition = (direction, update) => {
    if (!usesViewTransitions()) {
        update();
        return;
    }
    const root = document.documentElement;
    root.dataset.ecpNav = direction;
    let transition;
    try {
        transition = document.startViewTransition(() => {
            flushSync(update);
        });
    } catch (error) {
        delete root.dataset.ecpNav;
        update();
        return;
    }
    const done = () => {
        if (root.dataset.ecpNav === direction) delete root.dataset.ecpNav;
    };
    transition.finished.then(done, done);
    // Пропущенный переход (второй щелчок поверх первого) — не ошибка.
    transition.ready?.catch?.(() => {});
};
