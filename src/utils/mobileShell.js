/* Мобильная оболочка портала: нижний бар вместо бокового меню.
 *
 * На телефоне сайдбар шириной 256 px съедал экран, поэтому его прятали за
 * гамбургер — навигация жила в углу, в которую надо было целиться. Взамен
 * четыре верхних раздела вынесены в бар у нижнего края (как в Telegram), а
 * пятая кнопка — аватар — открывает шторку со ВСЕМИ разделами, то есть тот же
 * сайдбар, только выехавший снизу.
 *
 * ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ, А НЕ МЕДИАЗАПРОС В CSS. Признак «мы на телефоне»
 * нужен в трёх местах сразу: в CSS (вёрстка бара), в разметке (колокол уходит
 * порталом в угол экрана, а не рисуется пунктом меню) и в геометрии плавающего
 * помощника (шарику нельзя садиться на бар). Разъехавшись, эти три ответа дают
 * бар без места под него или колокол в двух экземплярах, поэтому источник
 * ответа один — здесь, а CSS читает его классом на <body>.
 *
 * ПОЧЕМУ ЗАПРОС НЕ ПРОСТО max-width. Телефон, повёрнутый набок, — это 844×390:
 * по ширине «настольный» экран, по высоте — нет. На один max-width: 768px
 * оболочка при повороте выключалась бы целиком, возвращая боковой сайдбар на
 * экран высотой в 390 px. Вторая половина запроса ловит именно этот случай:
 * альбомная ориентация с низким экраном. У ноутбука и планшета высота больше
 * 540 px даже боком, так что настольная вёрстка запросом не задета.
 */

export const MOBILE_SHELL_QUERY = '(max-width: 768px), (orientation: landscape) and (max-height: 540px)';

/** Класс, которым оболочка помечает <body> для CSS. */
export const MOBILE_SHELL_CLASS = 'mobile-shell';

/* Где стоит бар. Значения — «сторона ЭКРАНА», на которой он нарисован. */
export const TAB_BAR_SIDE = {
    BOTTOM: 'bottom',
    LEFT: 'left',
    RIGHT: 'right',
};

/**
 * Сторона бара по углу поворота экрана.
 *
 * Бар прибит к НИЖНЕЙ ГРАНИ КОРПУСА — к той, у которой лежит большой палец, — и
 * при повороте остаётся у неё же, а не переезжает вниз экрана: снизу в альбомной
 * ориентации он съел бы шестую часть и без того низкого окна. Поэтому боком он
 * становится вертикальной полосой у края, а иконки с подписями поворачиваются
 * вместе с ним — ровно как если бы бар был наклейкой на корпусе.
 *
 * angle — это угол, на который повёрнуто СОДЕРЖИМОЕ относительно естественной
 * ориентации устройства:
 *   90  — верх корпуса смотрит влево, значит низ корпуса (и бар) — справа;
 *   270 — зеркальный случай, бар слева;
 *   0/180 — портрет, бар внизу.
 * Всё, кроме 90 и 270, трактуем как портрет: у планшетов естественная
 * ориентация альбомная, и там 0 — это как раз «бар снизу», чего мы и хотим.
 */
export const tabBarSideForAngle = (angle) => {
    const normalized = ((Number(angle) || 0) % 360 + 360) % 360;
    if (normalized === 90) return TAB_BAR_SIDE.RIGHT;
    if (normalized === 270) return TAB_BAR_SIDE.LEFT;
    return TAB_BAR_SIDE.BOTTOM;
};

/**
 * Угол поворота экрана. Screen Orientation API — основной источник; window.orientation
 * оставлен фолбэком ради Safari старше 16.4, где первого нет вовсе, а поворот есть.
 * Не сумели прочитать — считаем портретом: бар внизу, то есть привычно.
 */
export const readOrientationAngle = (win) => {
    if (!win) return 0;
    const fromApi = win.screen?.orientation?.angle;
    if (typeof fromApi === 'number' && Number.isFinite(fromApi)) return fromApi;
    const legacy = win.orientation;
    if (typeof legacy === 'number' && Number.isFinite(legacy)) return legacy;
    return 0;
};

/**
 * Полное состояние оболочки: включена ли она и куда встал бар.
 *
 * В обычном (не мобильном) режиме сторона всегда «снизу»: бар не рисуется
 * вовсе, и хранить для него поворот незачем — иначе настольная вёрстка
 * зависела бы от угла, под которым стоит монитор.
 */
export const readMobileShell = (win) => {
    if (!win || typeof win.matchMedia !== 'function') {
        return { shell: false, side: TAB_BAR_SIDE.BOTTOM };
    }
    const shell = win.matchMedia(MOBILE_SHELL_QUERY).matches;
    return {
        shell,
        side: shell ? tabBarSideForAngle(readOrientationAngle(win)) : TAB_BAR_SIDE.BOTTOM,
    };
};

/**
 * Подписка на изменения. Слушаем и медиазапрос, и поворот: смена ориентации
 * меняет сторону бара, не трогая сам факт «мы на телефоне», и на один
 * change медиазапроса бар остался бы висеть у прежнего края.
 *
 * orientationchange в паре с событием Screen Orientation API: первое есть
 * везде, второе приходит на некоторых Android раньше, чем окно успевает
 * пересчитать размеры. Обработчик идемпотентен, лишний вызов ничего не стоит.
 */
export const subscribeMobileShell = (win, onChange) => {
    if (!win || typeof win.matchMedia !== 'function') return () => {};
    const media = win.matchMedia(MOBILE_SHELL_QUERY);
    const handle = () => onChange(readMobileShell(win));

    /* addListener — для Safari старше 14: там addEventListener у MediaQueryList
       ещё нет, а портал открывают и с таких устройств. */
    if (typeof media.addEventListener === 'function') media.addEventListener('change', handle);
    else if (typeof media.addListener === 'function') media.addListener(handle);
    win.addEventListener('orientationchange', handle);
    win.screen?.orientation?.addEventListener?.('change', handle);
    /* resize — единственный сигнал в настольном браузере, где окно тянут мышью:
       ни медиазапрос при дробных ширинах, ни поворот его не покрывают. */
    win.addEventListener('resize', handle);

    return () => {
        if (typeof media.removeEventListener === 'function') media.removeEventListener('change', handle);
        else if (typeof media.removeListener === 'function') media.removeListener(handle);
        win.removeEventListener('orientationchange', handle);
        win.screen?.orientation?.removeEventListener?.('change', handle);
        win.removeEventListener('resize', handle);
    };
};

/**
 * Прямоугольники, которые оболочка занимает под навигацию, в координатах окна.
 * Нужны плавающему помощнику: шарик, севший на бар или на колокол, отнимает
 * у человека вход в навигацию — тот же запрет, что раньше стоял на гамбургере.
 *
 * Возвращаем ПУСТОЙ список в настольном режиме: там ни бара, ни углового
 * колокола нет, и запретных зон быть не должно.
 */
export const TAB_BAR_THICKNESS = 64;
export const CORNER_BELL_BOX = 64;

export const mobileShellReservedBoxes = ({ shell, side }, viewport) => {
    if (!shell || !viewport) return [];
    const boxes = [];
    if (side === TAB_BAR_SIDE.RIGHT) {
        boxes.push({ x: viewport.width - TAB_BAR_THICKNESS, y: 0, width: TAB_BAR_THICKNESS, height: viewport.height });
    } else if (side === TAB_BAR_SIDE.LEFT) {
        boxes.push({ x: 0, y: 0, width: TAB_BAR_THICKNESS, height: viewport.height });
    } else {
        boxes.push({ x: 0, y: viewport.height - TAB_BAR_THICKNESS, width: viewport.width, height: TAB_BAR_THICKNESS });
    }
    /* Колокол в правом верхнем углу экрана — он там при любом повороте:
       угол корпуса при повороте уезжает в неудобное место (низ экрана), а
       уведомления должны быть под большим пальцем сверху, где их и ищут. */
    boxes.push({
        x: Math.max(0, viewport.width - CORNER_BELL_BOX),
        y: 0,
        width: CORNER_BELL_BOX,
        height: CORNER_BELL_BOX,
    });
    return boxes;
};
