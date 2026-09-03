/* Геометрия плавающего помощника: где висит шарик и куда раскрывается панель.
 *
 * Вынесено из компонента отдельным модулем не ради красоты, а потому что это
 * единственная часть виджета, которую можно проверить без браузера: тут чистые
 * функции над числами. Тесты — tests/assistant_orb_position.test.mjs.
 *
 * Система координат одна на весь модуль: X и Y — левый верхний угол ШАРИКА в
 * координатах окна (как у position: fixed). Не центр: с углом совпадает то, что
 * потом уходит в style.left/style.top, и лишнего пересчёта в компоненте нет.
 */

/** Диаметр шарика. Совпадает с --aorb-size в assistant-orb.css. */
export const ORB_SIZE = 56;

/** Отступ от краёв окна, на который шарик не наезжает. */
export const EDGE_MARGIN = 12;

/* Полоса у левого и правого края, в которой отпущенный шарик прилипает.
   14 пикселей — половина расстояния, на которое палец промахивается мимо края
   на тачпаде; шире полоса начинает ловить намеренные позиции у края. */
export const SNAP_ZONE = 40;

/** Насколько шарик уходит за край в прижатом состоянии (ровно половина). */
export const DOCK_HIDDEN = ORB_SIZE / 2;

/* Низ правого края занят: тосты сидят на bottom:16 right:16 (z 9999), виджет
   закреплённой задачи — на right:18 bottom:18 (z 130). Шарик по умолчанию
   встаёт ВЫШЕ них, иначе первый же тост накроет его собой. */
export const DEFAULT_BOTTOM_OFFSET = 96;
export const DEFAULT_RIGHT_OFFSET = 18;

/* Гамбургер мобильного меню — fixed top:16 left:16, 44×44, z 60. Шарик, севший
   на него, отнимает у человека единственный вход в навигацию, поэтому левый
   верхний угол для него закрыт. Порог 768px — тот же, что у медиазапроса
   .hamburger-btn в src/styles.css. */
const HAMBURGER_BREAKPOINT = 768;
const HAMBURGER_BOX = { x: 0, y: 0, width: 76, height: 76 };

const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

const isFiniteNumber = (value) => typeof value === 'number' && Number.isFinite(value);

/** Прямоугольник, в котором шарику разрешено стоять целиком. */
const freeBounds = (viewport) => ({
    minX: EDGE_MARGIN,
    minY: EDGE_MARGIN,
    maxX: Math.max(EDGE_MARGIN, viewport.width - ORB_SIZE - EDGE_MARGIN),
    maxY: Math.max(EDGE_MARGIN, viewport.height - ORB_SIZE - EDGE_MARGIN),
});

/**
 * Позиция по умолчанию — правый нижний угол, выше тостов.
 * Считается от размеров окна, а не хранится константой: на узком экране
 * фиксированные координаты увели бы шарик за край.
 */
export const defaultPosition = (viewport) => {
    const bounds = freeBounds(viewport);
    return {
        x: clamp(viewport.width - ORB_SIZE - DEFAULT_RIGHT_OFFSET, bounds.minX, bounds.maxX),
        y: clamp(viewport.height - ORB_SIZE - DEFAULT_BOTTOM_OFFSET, bounds.minY, bounds.maxY),
        dock: null,
    };
};

/**
 * Загнать позицию внутрь окна.
 *
 * Вызывается не только при перетаскивании, но и при КАЖДОМ монтировании: позиция
 * сохранена с того монитора, где человек её поставил, и на ноутбуке x=2400
 * означал бы шарик, которого не видно и который поэтому нельзя вернуть.
 *
 * У прижатого к краю шарика по горизонтали свои границы — он обязан торчать
 * ровно наполовину, и обычный clamp вернул бы его целиком на экран.
 */
export const clampPosition = (position, viewport) => {
    const bounds = freeBounds(viewport);
    const dock = position?.dock === 'left' || position?.dock === 'right' ? position.dock : null;
    const y = clamp(isFiniteNumber(position?.y) ? position.y : bounds.minY, bounds.minY, bounds.maxY);

    if (dock === 'left') return { x: -DOCK_HIDDEN, y, dock };
    if (dock === 'right') return { x: viewport.width - DOCK_HIDDEN, y, dock };

    return {
        x: clamp(isFiniteNumber(position?.x) ? position.x : bounds.maxX, bounds.minX, bounds.maxX),
        y,
        dock: null,
    };
};

/**
 * Куда встал отпущенный шарик: прилип к краю или остался, где брошен.
 *
 * Прилипание считается по ЦЕНТРУ шарика, а не по его левому краю: человек
 * тащит за середину, и на край он смотрит тоже серединой.
 */
export const resolveDock = (position, viewport) => {
    const centerX = position.x + ORB_SIZE / 2;
    if (centerX <= SNAP_ZONE) return clampPosition({ ...position, dock: 'left' }, viewport);
    if (centerX >= viewport.width - SNAP_ZONE) {
        return clampPosition({ ...position, dock: 'right' }, viewport);
    }
    return clampPosition({ ...position, dock: null }, viewport);
};

/**
 * Позиция после нажатия на прижатый шарик: он выезжает обратно на экран.
 * Панель, раскрытая от наполовину спрятанного шарика, выглядела бы приклеенной
 * к пустому месту, поэтому «показаться» и «открыться» — одно движение.
 */
export const undock = (position, viewport) => {
    if (!position?.dock) return position;
    const bounds = freeBounds(viewport);
    return {
        x: position.dock === 'left' ? bounds.minX : bounds.maxX,
        y: clamp(position.y, bounds.minY, bounds.maxY),
        dock: null,
    };
};

/**
 * Мешает ли шарик мобильному гамбургеру. Позицию не правим молча — компонент
 * решает сам; функция нужна тестам и подсказке при перетаскивании.
 */
export const overlapsHamburger = (position, viewport) => {
    if (viewport.width > HAMBURGER_BREAKPOINT) return false;
    return position.x < HAMBURGER_BOX.x + HAMBURGER_BOX.width
        && position.x + ORB_SIZE > HAMBURGER_BOX.x
        && position.y < HAMBURGER_BOX.y + HAMBURGER_BOX.height
        && position.y + ORB_SIZE > HAMBURGER_BOX.y;
};

/**
 * Куда поставить панель мини-чата относительно шарика.
 *
 * Правило простое: панель раскрывается В СТОРОНУ СВОБОДНОГО МЕСТА и вверх, а
 * если места нет ни там ни там — прижимается к окну. Возвращает координаты
 * левого верхнего угла панели и сторону, с которой она выросла: сторона нужна
 * анимации (панель должна раскрываться ОТ шарика, а не из своего центра).
 *
 * На узком экране панель разворачивается на всё окно с отступами — 384 пикселя
 * на телефоне шириной 360 не помещаются, а ужимать чат до 320 значит ломать
 * таблицы, ради которых у помощника вообще есть markdown.
 */
export const panelAnchor = (position, viewport, panel) => {
    const gap = 12;
    const fullscreen = viewport.width < panel.width + 2 * EDGE_MARGIN + gap;

    if (fullscreen) {
        const width = Math.max(240, viewport.width - 2 * EDGE_MARGIN);
        const height = Math.max(240, Math.min(panel.height, viewport.height - 2 * EDGE_MARGIN));
        return {
            left: EDGE_MARGIN,
            top: Math.max(EDGE_MARGIN, viewport.height - height - EDGE_MARGIN),
            width,
            height,
            origin: 'bottom',
            fullscreen: true,
        };
    }

    const orbCenterX = position.x + ORB_SIZE / 2;
    const toLeft = orbCenterX > viewport.width / 2;
    const rawLeft = toLeft
        ? position.x + ORB_SIZE - panel.width
        : position.x;
    const rawTop = position.y - panel.height - gap;

    const maxLeft = viewport.width - panel.width - EDGE_MARGIN;
    const maxTop = viewport.height - panel.height - EDGE_MARGIN;
    /* Панель выше окна не бывает: если она не влезает над шариком, её опускают
       вниз, а не обрезают. Обрезанный композер — это чат, в который нельзя
       написать. */
    const top = rawTop < EDGE_MARGIN
        ? clamp(position.y + ORB_SIZE + gap, EDGE_MARGIN, Math.max(EDGE_MARGIN, maxTop))
        : clamp(rawTop, EDGE_MARGIN, Math.max(EDGE_MARGIN, maxTop));

    return {
        left: clamp(rawLeft, EDGE_MARGIN, Math.max(EDGE_MARGIN, maxLeft)),
        top,
        width: panel.width,
        height: Math.min(panel.height, viewport.height - 2 * EDGE_MARGIN),
        origin: `${rawTop < EDGE_MARGIN ? 'top' : 'bottom'}-${toLeft ? 'right' : 'left'}`,
        fullscreen: false,
    };
};

/* Клик или перетаскивание — решается пройденным расстоянием, а не таймером.
   Порог в 4 пикселя пропускает дрожание руки на нажатии (особенно на тачскрине)
   и при этом не съедает намеренный короткий сдвиг. */
export const DRAG_THRESHOLD = 4;

export const movedEnough = (from, to) => (
    Math.abs(to.x - from.x) >= DRAG_THRESHOLD || Math.abs(to.y - from.y) >= DRAG_THRESHOLD
);
