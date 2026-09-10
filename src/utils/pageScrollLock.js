/* Замок страничной прокрутки под открытым листом.
 *
 * ЗАЧЕМ. На телефоне портал прокручивается САМОЙ страницей (корневые
 * контейнеры распущены — см. mobile-shell.css), поэтому шторка разделов и лист
 * уведомлений, лежащие поверх неё, прокрутку не перехватывают: палец, попавший
 * мимо их собственного списка, увозит раздел под ними. Снаружи это выглядит
 * так, что лист «дёргается», а закрыв его, человек оказывается в другом месте
 * раздела. В мобильных приложениях (Telegram — тот самый образец) фон под
 * листом стоит намертво.
 *
 * ПОЧЕМУ position: fixed, А НЕ overflow: hidden. `overflow: hidden` на <body>
 * прокрутку в Safari на iOS не останавливает вовсе — там прокручивается не
 * body, а сам документ. Рабочий способ один: прибить body к экрану и увести
 * его вверх на текущую прокрутку, а при снятии замка вернуть страницу на то же
 * место. `position: fixed` у body, в отличие от transform, точкой отсчёта для
 * `position: fixed` ПОТОМКОВ не становится — бар, колокол и сам лист остаются
 * там же, где были.
 *
 * СЧЁТЧИК, А НЕ ФЛАГ. Листов может быть два сразу: колокол живёт вне шторки и
 * открывается поверх неё. С флагом первый же закрывшийся снял бы замок у обоих.
 */

let depth = 0;
let savedScrollY = 0;
let savedStyle = null;

const bodyStyle = () => (typeof document === 'undefined' ? null : document.body?.style || null);

export const lockPageScroll = () => {
    const style = bodyStyle();
    if (!style) return;
    depth += 1;
    if (depth > 1) return;
    savedScrollY = window.scrollY || document.documentElement.scrollTop || 0;
    savedStyle = {
        position: style.position,
        top: style.top,
        left: style.left,
        right: style.right,
        width: style.width,
    };
    style.position = 'fixed';
    style.top = `${-savedScrollY}px`;
    style.left = '0';
    style.right = '0';
    style.width = '100%';
};

export const unlockPageScroll = () => {
    const style = bodyStyle();
    if (!style || depth === 0) return;
    depth -= 1;
    if (depth > 0) return;
    if (savedStyle) {
        style.position = savedStyle.position;
        style.top = savedStyle.top;
        style.left = savedStyle.left;
        style.right = savedStyle.right;
        style.width = savedStyle.width;
        savedStyle = null;
    }
    /* Возврат на прежнее место обязателен: пока body был прибит, документ
       стоял на нуле, и без этого закрытие листа выбрасывало бы человека в
       начало раздела. */
    window.scrollTo(0, savedScrollY);
};

/**
 * Замок на время жизни эффекта: `useEffect(() => holdPageScroll(active), [active])`.
 * Возвращает функцию снятия — её же React вызовет при размонтировании, поэтому
 * замок не переживает закрытую вкладку или уход со страницы посреди жеста.
 */
export const holdPageScroll = (active) => {
    if (!active) return undefined;
    lockPageScroll();
    return unlockPageScroll;
};
