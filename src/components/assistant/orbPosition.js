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

import { mobileShellReservedBoxes, tabBarInsets } from '../../utils/mobileShell.js';

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

/* Размер мини-чата — и стандартный, и НАИМЕНЬШИЙ. Панель можно растянуть за
   край (resizePanelRect), но не сжать: колонку у́же 384 пикселей не переживают
   ни шапка с пятью кнопками, ни таблицы в ответах (см. panelAnchor о телефоне),
   а просили окно увеличивать. Заодно у человека всегда есть дорога назад:
   сжал до упора — и панель снова стандартная, отдельной кнопки сброса не нужно. */
export const PANEL_SIZE = { width: 384, height: 520 };

/* Навигация телефона: бар разделов у одной из граней экрана и колокол в правом
   верхнем углу. Шарик, севший на них, отнимает у человека вход в разделы и в
   уведомления, поэтому занятые полосы для него закрыты — раньше ровно так же
   был закрыт левый верхний угол под гамбургером.

   Какая грань занята, знает оболочка (src/utils/mobileShell.js): при повороте
   телефона бар остаётся у нижней грани КОРПУСА, то есть уезжает вбок. */
const navInsets = tabBarInsets;

const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

const isFiniteNumber = (value) => typeof value === 'number' && Number.isFinite(value);

/** Прямоугольник, в котором шарику разрешено стоять целиком. */
const freeBounds = (viewport, nav) => {
    const insets = navInsets(nav);
    const minX = EDGE_MARGIN + insets.left;
    const minY = EDGE_MARGIN + insets.top;
    return {
        minX,
        minY,
        maxX: Math.max(minX, viewport.width - ORB_SIZE - EDGE_MARGIN - insets.right),
        maxY: Math.max(minY, viewport.height - ORB_SIZE - EDGE_MARGIN - insets.bottom),
    };
};

/**
 * Позиция по умолчанию — правый нижний угол, выше тостов.
 * Считается от размеров окна, а не хранится константой: на узком экране
 * фиксированные координаты увели бы шарик за край.
 */
export const defaultPosition = (viewport, nav) => {
    const bounds = freeBounds(viewport, nav);
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
export const clampPosition = (position, viewport, nav) => {
    const bounds = freeBounds(viewport, nav);
    const insets = navInsets(nav);
    const dock = position?.dock === 'left' || position?.dock === 'right' ? position.dock : null;
    const y = clamp(isFiniteNumber(position?.y) ? position.y : bounds.minY, bounds.minY, bounds.maxY);

    /* Прижатый шарик торчит из-за края наполовину — но из-за края СВОБОДНОГО
       места, а не экрана: там, где вдоль края стоит бар, он прилипает к бару. */
    if (dock === 'left') return { x: insets.left - DOCK_HIDDEN, y, dock };
    if (dock === 'right') return { x: viewport.width - insets.right - DOCK_HIDDEN, y, dock };

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
export const resolveDock = (position, viewport, nav) => {
    const insets = navInsets(nav);
    const centerX = position.x + ORB_SIZE / 2;
    if (centerX <= insets.left + SNAP_ZONE) return clampPosition({ ...position, dock: 'left' }, viewport, nav);
    if (centerX >= viewport.width - insets.right - SNAP_ZONE) {
        return clampPosition({ ...position, dock: 'right' }, viewport, nav);
    }
    return clampPosition({ ...position, dock: null }, viewport, nav);
};

/**
 * Позиция после нажатия на прижатый шарик: он выезжает обратно на экран.
 * Панель, раскрытая от наполовину спрятанного шарика, выглядела бы приклеенной
 * к пустому месту, поэтому «показаться» и «открыться» — одно движение.
 */
export const undock = (position, viewport, nav) => {
    if (!position?.dock) return position;
    const bounds = freeBounds(viewport, nav);
    return {
        x: position.dock === 'left' ? bounds.minX : bounds.maxX,
        y: clamp(position.y, bounds.minY, bounds.maxY),
        dock: null,
    };
};

/**
 * Мешает ли шарик мобильной навигации — бару разделов или колоколу в углу.
 * Позицию не правим молча (её уже сузил freeBounds), функция отвечает на
 * вопрос «наехал ли» — для тестов и подсказки при перетаскивании.
 */
export const overlapsNavigation = (position, viewport, nav) => (
    mobileShellReservedBoxes(nav || { shell: false }, viewport).some((box) => (
        position.x < box.x + box.width
        && position.x + ORB_SIZE > box.x
        && position.y < box.y + box.height
        && position.y + ORB_SIZE > box.y
    ))
);

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
    /* Телефон узнаём по СТАНДАРТНОЙ ширине, а не по желаемой: панель, которую
       растянули до 900 пикселей, на окне в 920 — всё ещё компьютер, ей просто
       тесно. Сравнение с желаемой шириной развернуло бы её на всё окно и
       отняло ручки краёв — то есть человек не смог бы сжать её обратно. */
    const fullscreen = viewport.width < PANEL_SIZE.width + 2 * EDGE_MARGIN + gap;

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

    /* Желаемый размер человек выставил на том окне, где тянул панель, и
       хранится он как есть. На окне поменьше панель занимает его целиком за
       вычетом отступов, не больше — а на прежнем мониторе вернётся к своему. */
    const width = Math.min(panel.width, viewport.width - 2 * EDGE_MARGIN);
    const height = Math.min(panel.height, viewport.height - 2 * EDGE_MARGIN);

    const orbCenterX = position.x + ORB_SIZE / 2;
    const toLeft = orbCenterX > viewport.width / 2;
    const rawLeft = toLeft
        ? position.x + ORB_SIZE - width
        : position.x;
    const rawTop = position.y - height - gap;

    const maxLeft = viewport.width - width - EDGE_MARGIN;
    const maxTop = viewport.height - height - EDGE_MARGIN;
    /* Панель выше окна не бывает: если она не влезает над шариком, её опускают
       вниз, а не обрезают. Обрезанный композер — это чат, в который нельзя
       написать. */
    const top = rawTop < EDGE_MARGIN
        ? clamp(position.y + ORB_SIZE + gap, EDGE_MARGIN, Math.max(EDGE_MARGIN, maxTop))
        : clamp(rawTop, EDGE_MARGIN, Math.max(EDGE_MARGIN, maxTop));

    return {
        left: clamp(rawLeft, EDGE_MARGIN, Math.max(EDGE_MARGIN, maxLeft)),
        top,
        width,
        height,
        origin: `${rawTop < EDGE_MARGIN ? 'top' : 'bottom'}-${toLeft ? 'right' : 'left'}`,
        fullscreen: false,
    };
};

/**
 * Желаемый размер панели из хранилища.
 *
 * Запись могла остаться битой или от версии, где размера ещё не было: мусор и
 * всё, что меньше стандартного, дают стандартный. Сверху не режем — потолок
 * зависит от окна, и ставит его panelAnchor на каждом открытии.
 */
export const normalizePanelSize = (size) => ({
    width: isFiniteNumber(size?.width)
        ? Math.max(PANEL_SIZE.width, Math.round(size.width))
        : PANEL_SIZE.width,
    height: isFiniteNumber(size?.height)
        ? Math.max(PANEL_SIZE.height, Math.round(size.height))
        : PANEL_SIZE.height,
});

/**
 * Рамка панели, пока её тянут за край или угол.
 *
 * Правило то же, что у окна в macOS: тянутый край идёт за указателем,
 * противоположный стоит на месте. Поэтому считаем от рамки, с которой жест
 * НАЧАЛСЯ, и полного смещения указателя, а не от прошлого кадра: накопленная
 * по кадрам ошибка округления уводила бы «стоящий» край по пикселю.
 *
 * edge — какие края взяты: { x: 'left' | 'right', y: 'top' | 'bottom' }, у
 * стороны по одной оси второй оси нет вовсе.
 *
 * Считается РАЗМЕР, а край выводится из него. Смещение указателя округляется
 * до целого, и размер остаётся целым, даже когда сама рамка стоит на дробном
 * месте: при масштабе страницы 110 % шарик, а с ним и панель, встают на
 * половинки пикселя. Округляй мы край, нажатие без движения уже сдвигало бы
 * его на долю пикселя и записывало размер вида 384.4.
 *
 * Границы две. Наружу — отступ от края окна, как у самой панели. Внутрь —
 * стандартный размер; на окне, где панель УЖЕ ниже стандартной (её ужал
 * panelAnchor), нижней границей служит её нынешний размер — иначе первое же
 * движение дёрнуло бы край к недостижимым 520 пикселям. По той же причине
 * рамке, которая уже стоит за отступом, потолком служит она сама.
 */
export const resizePanelRect = (start, edge, delta, viewport) => {
    const right = start.left + start.width;
    const bottom = start.top + start.height;
    /* Сторона, которую тянут: size — её нынешний размер, grow — на сколько
       просит вырасти указатель, room — сколько места до отступа от края. */
    const side = (size, standard, grow, room) => clamp(
        size + Math.round(grow), Math.min(standard, size), Math.max(size, Math.floor(room)));

    let { left, top, width, height } = start;
    if (edge?.x === 'left') {
        width = side(start.width, PANEL_SIZE.width, -delta.x, right - EDGE_MARGIN);
    } else if (edge?.x === 'right') {
        width = side(start.width, PANEL_SIZE.width, delta.x,
                     viewport.width - EDGE_MARGIN - start.left);
    }
    if (edge?.y === 'top') {
        height = side(start.height, PANEL_SIZE.height, -delta.y, bottom - EDGE_MARGIN);
    } else if (edge?.y === 'bottom') {
        height = side(start.height, PANEL_SIZE.height, delta.y,
                      viewport.height - EDGE_MARGIN - start.top);
    }
    // Левый и верхний край пересчитываем, только если размер изменился: иначе
    // (left + width) - width возвращал бы то же место с ошибкой в 13-м знаке.
    if (edge?.x === 'left' && width !== start.width) left = right - width;
    if (edge?.y === 'top' && height !== start.height) top = bottom - height;

    return { left, top, width, height };
};

/**
 * Желаемый размер панели после жеста.
 *
 * Запоминается только та сторона, которую жест действительно ИЗМЕНИЛ. Вторая
 * могла быть ужата окном: желаемые 900 пикселей высоты на ноутбуке показаны
 * как 696, и запись «как на экране» молча стёрла бы выбор, сделанный на
 * мониторе, — от того, что человек потянул другой край или упёрся в отступ.
 *
 * prev возвращается тем же объектом, если не изменилось ничего: по нему
 * компонент понимает, что запоминать нечего.
 */
export const settledPanelSize = (prev, start, rect) => {
    if (rect.width === start.width && rect.height === start.height) return prev;
    return normalizePanelSize({
        width: rect.width !== start.width ? rect.width : prev.width,
        height: rect.height !== start.height ? rect.height : prev.height,
    });
};

/* Порог «нажали или потащили» — общий для всех плавающих элементов портала
   (шарик здесь, колокол в углу телефона), поэтому живёт в utils/dragGesture.js.
   Реэкспорт оставлен ради тех, кто уже импортирует его отсюда. */
export { DRAG_THRESHOLD, movedEnough } from '../../utils/dragGesture.js';
