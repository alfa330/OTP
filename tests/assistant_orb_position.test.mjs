// Геометрия плавающего помощника: где стоит шарик и куда раскрывается панель.
//
// Единственная часть виджета, которую можно проверить без браузера, — и та, в
// которой ошибка обходится дороже всего: шарик, уехавший за край экрана, нельзя
// вернуть мышью, потому что его не видно. Поэтому clampPosition проверяется не
// только на перетаскивании, но и на монтировании с чужого монитора.
//
// Запуск: node --test tests/assistant_orb_position.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';

import {
    DOCK_HIDDEN,
    EDGE_MARGIN,
    ORB_SIZE,
    PANEL_SIZE,
    clampPosition,
    defaultPosition,
    movedEnough,
    normalizePanelSize,
    overlapsNavigation,
    panelAnchor,
    resizePanelRect,
    resolveDock,
    settledPanelSize,
    undock,
} from '../src/components/assistant/orbPosition.js';

const DESKTOP = { width: 1440, height: 900 };
const LAPTOP = { width: 1280, height: 720 };
const PHONE = { width: 375, height: 667 };
const PANEL = { width: 384, height: 520 };
/* Мобильная оболочка: бар разделов у одной из граней экрана. Сторона меняется
   при повороте телефона — бар остаётся у нижней грани КОРПУСА. */
const NAV_BOTTOM = { shell: true, side: 'bottom' };
const NAV_RIGHT = { shell: true, side: 'right' };
const NAV_OFF = { shell: false, side: 'bottom' };
const PHONE_LANDSCAPE = { width: 667, height: 375 };

test('по умолчанию шарик стоит в правом нижнем углу, но выше тостов', () => {
    const position = defaultPosition(DESKTOP);
    assert.equal(position.x, DESKTOP.width - ORB_SIZE - 18);
    // Тосты живут на bottom:16, виджет закреплённой задачи — на bottom:18.
    // Шарик обязан оказаться заметно выше обоих, иначе первый же тост его накроет.
    const bottomGap = DESKTOP.height - (position.y + ORB_SIZE);
    assert.ok(bottomGap > 60, `шарик слишком низко: ${bottomGap}px до низа`);
    assert.equal(position.dock, null);
});

test('позиция с широкого монитора не уводит шарик за край ноутбука', () => {
    // Ровно этот случай нельзя исправить мышью: шарика не видно.
    const stored = { x: 2500, y: 1300, dock: null };
    const fixed = clampPosition(stored, LAPTOP);
    assert.ok(fixed.x + ORB_SIZE <= LAPTOP.width, 'уехал за правый край');
    assert.ok(fixed.y + ORB_SIZE <= LAPTOP.height, 'уехал за нижний край');
    assert.ok(fixed.x >= EDGE_MARGIN && fixed.y >= EDGE_MARGIN);
});

test('битое сохранённое значение не роняет виджет', () => {
    for (const broken of [null, {}, { x: 'нет', y: undefined }, { x: NaN, y: NaN }]) {
        const fixed = clampPosition(broken, DESKTOP);
        assert.ok(Number.isFinite(fixed.x) && Number.isFinite(fixed.y),
                  `битое значение дало ${JSON.stringify(fixed)}`);
    }
});

test('отпущенный у края шарик прилипает и прячется ровно наполовину', () => {
    const left = resolveDock({ x: 4, y: 300, dock: null }, DESKTOP);
    assert.equal(left.dock, 'left');
    assert.equal(left.x, -DOCK_HIDDEN);

    const right = resolveDock({ x: DESKTOP.width - ORB_SIZE - 4, y: 300, dock: null }, DESKTOP);
    assert.equal(right.dock, 'right');
    assert.equal(right.x, DESKTOP.width - DOCK_HIDDEN);

    // Ровно половина, а не «почти»: торчащая треть читается как поломка вёрстки.
    assert.equal(DOCK_HIDDEN, ORB_SIZE / 2);
});

test('брошенный посреди экрана шарик остаётся там, где брошен', () => {
    const middle = resolveDock({ x: 600, y: 400, dock: null }, DESKTOP);
    assert.equal(middle.dock, null);
    assert.equal(middle.x, 600);
    assert.equal(middle.y, 400);
});

test('прижатый шарик выезжает обратно целиком', () => {
    const docked = resolveDock({ x: 2, y: 300, dock: null }, DESKTOP);
    const back = undock(docked, DESKTOP);
    assert.equal(back.dock, null);
    assert.ok(back.x >= EDGE_MARGIN, 'после возврата всё ещё за краем');
    assert.equal(undock({ x: 100, y: 100, dock: null }, DESKTOP).x, 100,
                 'неприжатый шарик трогать не за чем');
});

test('прижатое состояние переживает смену размера окна', () => {
    const docked = { x: DESKTOP.width - DOCK_HIDDEN, y: 300, dock: 'right' };
    const resized = clampPosition(docked, LAPTOP);
    assert.equal(resized.dock, 'right');
    // Пересчитан по НОВОМУ краю, а не оставлен в координатах прежнего окна.
    assert.equal(resized.x, LAPTOP.width - DOCK_HIDDEN);
});

test('клик и перетаскивание различаются по расстоянию', () => {
    assert.equal(movedEnough({ x: 100, y: 100 }, { x: 101, y: 101 }), false,
                 'дрожание руки не должно считаться перетаскиванием');
    assert.equal(movedEnough({ x: 100, y: 100 }, { x: 100, y: 108 }), true);
});

test('панель раскрывается в сторону свободного места и не вылезает за окно', () => {
    const rightSide = panelAnchor({ x: 1360, y: 800, dock: null }, DESKTOP, PANEL);
    assert.ok(rightSide.left + rightSide.width <= DESKTOP.width - EDGE_MARGIN + 1,
              'панель уехала за правый край');
    assert.ok(rightSide.top >= EDGE_MARGIN);

    const leftSide = panelAnchor({ x: 20, y: 800, dock: null }, DESKTOP, PANEL);
    assert.ok(leftSide.left >= EDGE_MARGIN, 'панель уехала за левый край');
});

test('шарик у верхней кромки: панель падает ВНИЗ, а не обрезается', () => {
    // Обрезанная сверху панель — это чат без композера, то есть чат, в который
    // нельзя написать.
    const anchor = panelAnchor({ x: 1200, y: 20, dock: null }, DESKTOP, PANEL);
    assert.ok(anchor.top >= EDGE_MARGIN);
    assert.ok(anchor.top + anchor.height <= DESKTOP.height - EDGE_MARGIN + 1);
});

test('на телефоне панель занимает окно, а не 384 пикселя', () => {
    const anchor = panelAnchor({ x: 300, y: 560, dock: null }, PHONE, PANEL);
    assert.equal(anchor.fullscreen, true);
    assert.ok(anchor.width <= PHONE.width - 2 * EDGE_MARGIN + 1);
    assert.ok(anchor.width > 300, 'панель ужалась так, что таблица не поместится');
    assert.ok(anchor.top + anchor.height <= PHONE.height - EDGE_MARGIN + 1);
});

test('на телефоне навигация защищена, на десктопе такой зоны нет', () => {
    // Шарик, севший на бар разделов или на колокол, отнимает вход в навигацию
    // и в уведомления — ровно то, чем раньше грозил гамбургер.
    const onBar = { x: 150, y: PHONE.height - 40, dock: null };
    const onBell = { x: PHONE.width - 50, y: 6, dock: null };
    assert.equal(overlapsNavigation(onBar, PHONE, NAV_BOTTOM), true);
    assert.equal(overlapsNavigation(onBell, PHONE, NAV_BOTTOM), true);
    assert.equal(overlapsNavigation({ x: 150, y: 300, dock: null }, PHONE, NAV_BOTTOM), false);
    assert.equal(overlapsNavigation(onBar, PHONE, NAV_OFF), false);
    assert.equal(overlapsNavigation({ x: 12, y: 12, dock: null }, DESKTOP, NAV_OFF), false);
});

test('шарик не встаёт под баром разделов ни в одном повороте', () => {
    /* Полоса бара — единственное место экрана, где шарика быть не должно: под
       ним он и сам недоступен, и кнопки перекрывает. Проверяем оба поворота:
       боком бар уезжает к боковой грани, и «просто не ставить внизу» мало. */
    const low = clampPosition({ x: 100, y: 10000, dock: null }, PHONE, NAV_BOTTOM);
    assert.ok(low.y + ORB_SIZE <= PHONE.height - 64, `шарик залез на бар: y=${low.y}`);

    const right = clampPosition({ x: 10000, y: 100, dock: null }, PHONE_LANDSCAPE, NAV_RIGHT);
    assert.ok(
        right.x + ORB_SIZE <= PHONE_LANDSCAPE.width - 64,
        `шарик залез на боковой бар: x=${right.x}`,
    );
});

test('прижатый шарик липнет к краю свободного места, а не к краю экрана', () => {
    // Иначе «прижать вправо» на повёрнутом телефоне означало бы «спрятать под бар».
    const docked = resolveDock({ x: PHONE_LANDSCAPE.width - 60, y: 100, dock: null }, PHONE_LANDSCAPE, NAV_RIGHT);
    assert.equal(docked.dock, 'right');
    assert.equal(docked.x, PHONE_LANDSCAPE.width - 64 - DOCK_HIDDEN);
});

// ── Панель тянут за край ────────────────────────────────────────────────────
//
// Ответ помощника — текст с таблицей и источниками, и в колонке 384 на 520 его
// читают прокруткой. Панель растягивается за любой край или угол, как окно.
// Запоминается только РАЗМЕР; место остаётся производным от шарика. Отсюда
// вещи, которые ломаются молча: край, уехавший от указателя; панель, которую
// сжали так, что шапка и таблицы не помещаются; размер с большого монитора,
// который на ноутбуке увёл панель за край окна, — и обратное: ноутбук, который
// стёр размер, выбранный на мониторе.

const HANDLES = [
    { y: 'top' }, { y: 'bottom' }, { x: 'left' }, { x: 'right' },
    { x: 'left', y: 'top' }, { x: 'right', y: 'top' },
    { x: 'left', y: 'bottom' }, { x: 'right', y: 'bottom' },
];

/* Панель у шарика в правом нижнем углу: стоит над ним, правым краем по шарику. */
const START = { left: 1038, top: 216, width: 384, height: 520 };
const right = (rect) => rect.left + rect.width;
const bottom = (rect) => rect.top + rect.height;

test('тянутый край идёт за указателем, противоположный стоит на месте', () => {
    /* Сторону тянут по одной оси, а рука при этом гуляет и поперёк. Поперечное
       смещение взято в сторону РОСТА: в сторону сжатия его съел бы минимум
       размера, и лишняя ось в расчёте осталась бы незамеченной. */
    const wider = resizePanelRect(START, { x: 'left' }, { x: -200, y: -37 }, DESKTOP);
    assert.equal(wider.left, START.left - 200);
    assert.equal(right(wider), right(START), 'правый край уехал вместе с левым');
    assert.equal(wider.top, START.top, 'левый край потянул за собой верх');
    assert.equal(wider.height, START.height);

    const taller = resizePanelRect(START, { y: 'top' }, { x: -55, y: -100 }, DESKTOP);
    assert.equal(taller.top, START.top - 100);
    assert.equal(bottom(taller), bottom(START), 'низ уехал вместе с верхом');
    assert.equal(taller.left, START.left, 'верх потянул за собой левый край');
    assert.equal(taller.width, START.width);

    // Правый и нижний края — те же правила в зеркале (шарик стоит слева сверху).
    const leftSide = { left: 200, top: 88, width: 384, height: 520 };
    const east = resizePanelRect(leftSide, { x: 'right' }, { x: 300, y: 44 }, DESKTOP);
    assert.equal(east.left, leftSide.left);
    assert.equal(east.width, 684);
    assert.equal(east.top, leftSide.top);
    assert.equal(east.height, leftSide.height, 'правый край потянул за собой низ');
    const south = resizePanelRect(leftSide, { y: 'bottom' }, { x: 66, y: 200 }, DESKTOP);
    assert.equal(south.top, leftSide.top);
    assert.equal(south.height, 720);
    assert.equal(south.width, leftSide.width, 'низ потянул за собой правый край');
});

test('угол тянет обе стороны разом', () => {
    const corner = resizePanelRect(START, { x: 'left', y: 'top' }, { x: -50, y: -40 }, DESKTOP);
    assert.deepEqual(corner, { left: 988, top: 176, width: 434, height: 560 });
    assert.equal(right(corner), right(START));
    assert.equal(bottom(corner), bottom(START));
});

test('меньше стандартной панель не сжимается', () => {
    // Колонку у́же 384 не переживают шапка с пятью кнопками и таблицы в ответах.
    // И у человека всегда есть дорога назад: сжал до упора — панель стандартная.
    const grown = { left: 500, top: 100, width: 900, height: 700 };
    const squeezed = resizePanelRect(grown, { x: 'left', y: 'top' }, { x: 5000, y: 5000 }, DESKTOP);
    assert.equal(squeezed.width, PANEL_SIZE.width);
    assert.equal(squeezed.height, PANEL_SIZE.height);
    assert.equal(right(squeezed), right(grown));
    assert.equal(bottom(squeezed), bottom(grown));

    const fromRight = resizePanelRect(grown, { x: 'right', y: 'bottom' }, { x: -5000, y: -5000 }, DESKTOP);
    assert.equal(fromRight.width, PANEL_SIZE.width);
    assert.equal(fromRight.height, PANEL_SIZE.height);
    assert.equal(fromRight.left, grown.left);
    assert.equal(fromRight.top, grown.top);

    assert.deepEqual(PANEL_SIZE, PANEL, 'стандартный размер панели изменился');
});

test('за край окна панель не вытянуть', () => {
    const out = resizePanelRect(START, { x: 'left', y: 'top' }, { x: -9000, y: -9000 }, DESKTOP);
    assert.equal(out.left, EDGE_MARGIN);
    assert.equal(out.top, EDGE_MARGIN);

    const leftSide = { left: 200, top: 88, width: 384, height: 520 };
    const far = resizePanelRect(leftSide, { x: 'right', y: 'bottom' }, { x: 9000, y: 9000 }, DESKTOP);
    assert.equal(right(far), DESKTOP.width - EDGE_MARGIN);
    assert.equal(bottom(far), DESKTOP.height - EDGE_MARGIN);
});

test('на низком окне панель, уже ужатая окном, от нажатия на край не дёргается', () => {
    // Окно 541 px высотой: стандартные 520 не помещаются, panelAnchor дал 517.
    // Нижней границей жеста обязан служить нынешний размер, иначе первое же
    // движение рвануло бы край к недостижимым 520.
    const low = { width: 1024, height: 541 };
    const anchor = panelAnchor({ x: 900, y: 400, dock: null }, low, PANEL);
    assert.equal(anchor.height, 517);
    const rect = { left: anchor.left, top: anchor.top, width: anchor.width, height: anchor.height };
    assert.deepEqual(resizePanelRect(rect, { y: 'top' }, { x: 0, y: 0 }, low), rect);
    assert.deepEqual(resizePanelRect(rect, { y: 'top' }, { x: 0, y: 60 }, low), rect);
    assert.deepEqual(resizePanelRect(rect, { y: 'bottom' }, { x: 0, y: -60 }, low), rect);
    // То же по ширине: рамка у́же стандартной не обязана вырасти от одного нажатия.
    const slim = { left: 400, top: 12, width: 300, height: 517 };
    assert.deepEqual(resizePanelRect(slim, { x: 'left' }, { x: 0, y: 0 }, low), slim);
    assert.deepEqual(resizePanelRect(slim, { x: 'right' }, { x: -40, y: 0 }, low), slim);
});

test('дробный указатель не даёт дробного размера', () => {
    // При масштабе страницы 110 % координаты указателя дробные. Размер остаётся
    // целым за любой край: половинки пикселя размывают рамку и копятся в записи.
    const rect = resizePanelRect(START, { x: 'left', y: 'top' }, { x: -100.4, y: -30.6 }, DESKTOP);
    assert.deepEqual(rect, { left: 938, top: 185, width: 484, height: 551 });

    const leftSide = { left: 200, top: 88, width: 384, height: 520 };
    const mirrored = resizePanelRect(leftSide, { x: 'right', y: 'bottom' }, { x: 100.4, y: 30.6 }, DESKTOP);
    assert.deepEqual(mirrored, { left: 200, top: 88, width: 484, height: 551 });
});

test('панель на дробном месте: нажатие на край её не двигает, размер остаётся целым', () => {
    /* При том же масштабе 110 % дробным бывает и место шарика, а с ним — левый
       верхний угол панели. Округляй жест КРАЙ, а не размер, нажатие без
       движения сдвигало бы его на долю пикселя и записывало ширину 384.4. */
    const orb = { x: 1132.364, y: 646.182, dock: null };
    const anchor = panelAnchor(orb, DESKTOP, PANEL);
    const start = { left: anchor.left, top: anchor.top, width: anchor.width, height: anchor.height };
    assert.ok(!Number.isInteger(start.left) && !Number.isInteger(start.top), 'пример обязан быть дробным');
    for (const handle of HANDLES) {
        assert.deepEqual(resizePanelRect(start, handle, { x: 0, y: 0 }, DESKTOP), start,
                         `нажатие на ${JSON.stringify(handle)} сдвинуло рамку`);
        assert.deepEqual(resizePanelRect(start, handle, { x: 0.3, y: -0.4 }, DESKTOP), start,
                         `дрожание руки на ${JSON.stringify(handle)} сдвинуло рамку`);
    }
    /* Место, на котором видна ошибка сложения: (700.4 + 384) - 384 — это уже
       700.4000000000001. Край, пересчитанный без нужды, уехал бы на неё. */
    const shaky = { left: 700.4, top: 100.3, width: 384, height: 520 };
    for (const handle of HANDLES) {
        assert.deepEqual(resizePanelRect(shaky, handle, { x: 0, y: 0 }, DESKTOP), shaky,
                         `нажатие на ${JSON.stringify(handle)} сдвинуло рамку на ошибку сложения`);
    }

    const wider = resizePanelRect(start, { x: 'left', y: 'top' }, { x: -200.3, y: -50.2 }, DESKTOP);
    assert.equal(wider.width, 584);
    assert.equal(wider.height, 570);
    // Стоящий край стоит: правый и нижний те же, с точностью до ошибки сложения.
    assert.ok(Math.abs(right(wider) - right(start)) < 1e-9);
    assert.ok(Math.abs(bottom(wider) - bottom(start)) < 1e-9);
    // И при упоре в край окна размер всё равно целый, а панель — в окне.
    const far = resizePanelRect(start, { x: 'left', y: 'top' }, { x: -9000, y: -9000 }, DESKTOP);
    assert.ok(Number.isInteger(far.width) && Number.isInteger(far.height));
    assert.ok(far.left >= EDGE_MARGIN && far.left < EDGE_MARGIN + 1);
    assert.ok(far.top >= EDGE_MARGIN && far.top < EDGE_MARGIN + 1);
});

test('рамка, уже стоящая за отступом, от нажатия не дёргается и за край не растёт', () => {
    // Такой рамки компонент не даёт, но функция не должна её «чинить» рывком:
    // потолком служит сама рамка.
    const outLeft = { left: 4, top: 4, width: 600, height: 700 };
    const outRight = { left: 900, top: 300, width: 536, height: 596 };   // правый и нижний край за отступом
    for (const start of [outLeft, outRight]) {
        for (const handle of HANDLES) {
            assert.deepEqual(resizePanelRect(start, handle, { x: 0, y: 0 }, DESKTOP), start);
        }
    }
    const grown = resizePanelRect(outLeft, { x: 'left', y: 'top' }, { x: -50, y: -50 }, DESKTOP);
    assert.deepEqual(grown, outLeft, 'рамка выросла дальше за край окна');
    const grownRight = resizePanelRect(outRight, { x: 'right', y: 'bottom' }, { x: 50, y: 50 }, DESKTOP);
    assert.deepEqual(grownRight, outRight, 'рамка выросла дальше за край окна');
});

test('жест за свободный край и место от шарика дают одну и ту же рамку', () => {
    /* Место панели — производное от шарика и размера. Если бы после жеста оно
       считалось иначе, чем двигался край, панель прыгала бы при следующем же
       открытии: потянул левый край — открыл снова — она стоит не там. */
    const orb = defaultPosition(DESKTOP);
    const start = panelAnchor(orb, DESKTOP, PANEL);
    const dragged = resizePanelRect(
        { left: start.left, top: start.top, width: start.width, height: start.height },
        { x: 'left', y: 'top' }, { x: -260, y: -120 }, DESKTOP,
    );
    const reopened = panelAnchor(orb, DESKTOP, { width: dragged.width, height: dragged.height });
    assert.deepEqual(
        { left: reopened.left, top: reopened.top, width: reopened.width, height: reopened.height },
        dragged,
    );
    // И осталась над шариком: низ панели — в 12 пикселях над ним.
    assert.equal(bottom(reopened), orb.y - 12);
});

test('за край у шарика панель растёт от шарика, а не поверх него', () => {
    /* Нижний край стоит у шарика. Потянутая вниз, панель ложится на шарик
       только на время жеста: её место по-прежнему считается от шарика, и по
       отпусканию она встаёт над ним — выросшей вверх на то, что вытянули. */
    const orb = defaultPosition(DESKTOP);
    const start = panelAnchor(orb, DESKTOP, PANEL);
    const dragged = resizePanelRect(
        { left: start.left, top: start.top, width: start.width, height: start.height },
        { y: 'bottom' }, { x: 0, y: 100 }, DESKTOP,
    );
    assert.equal(bottom(dragged), bottom(start) + 100, 'во время жеста край идёт за указателем');
    const settled = panelAnchor(orb, DESKTOP, { width: dragged.width, height: dragged.height });
    assert.equal(settled.height, 620);
    assert.equal(bottom(settled), orb.y - 12, 'панель осталась лежать на шарике');
    assert.equal(settled.top, start.top - 100);
});

test('запоминается только та сторона, которую жест изменил', () => {
    /* На мониторе панель растянули до 900×900. На ноутбуке она показана как
       900×676 — высоту ужало окно. Жест, не тронувший высоту, не имеет права
       записать её «как на экране»: на мониторе панель вернулась бы низкой. */
    const desired = { width: 900, height: 900 };
    const shown = { left: 454, top: 12, width: 900, height: 676 };

    const cornerLeftOnly = { left: 354, top: 12, width: 1000, height: 676 };
    assert.deepEqual(settledPanelSize(desired, shown, cornerLeftOnly), { width: 1000, height: 900 });

    // Тянули вверх, где места нет: рамка не изменилась — и запись тоже.
    assert.equal(settledPanelSize(desired, shown, { ...shown }), desired,
                 'жест, ничего не изменивший, обязан вернуть прежний объект');

    // Высоту тронули намеренно — она и запоминается, ширина остаётся желаемой.
    const lower = { left: 454, top: 112, width: 900, height: 576 };
    assert.deepEqual(settledPanelSize({ width: 1700, height: 900 }, shown, lower),
                     { width: 1700, height: 576 });

    // Обе стороны разом.
    assert.deepEqual(settledPanelSize(PANEL, START, { left: 938, top: 116, width: 484, height: 620 }),
                     { width: 484, height: 620 });
});

test('низкое окно не записывает высоту меньше стандартной', () => {
    // Окно 541 px: панель показана как 517. Сколько ни тяни её края, в запись
    // 517 попасть не должно — на обычном окне она осталась бы ниже стандартной.
    const low = { width: 1024, height: 541 };
    const anchor = panelAnchor({ x: 900, y: 400, dock: null }, low, PANEL);
    const start = { left: anchor.left, top: anchor.top, width: anchor.width, height: anchor.height };
    for (const handle of HANDLES) {
        for (const delta of [{ x: 0, y: -40 }, { x: 0, y: 40 }, { x: -3, y: -3 }]) {
            const rect = resizePanelRect(start, handle, delta, low);
            const size = settledPanelSize(PANEL, start, rect);
            assert.ok(size.height >= PANEL.height, `в запись ушла высота ${size.height}`);
            assert.ok(size.width >= PANEL.width);
        }
    }
    // И даже если бы рамка изменилась на дробь или ниже стандарта — запись чистая.
    assert.deepEqual(settledPanelSize(PANEL, START, { ...START, width: 500.6, height: 300 }),
                     { width: 501, height: 520 });
});

test('размер с большого монитора не уводит панель за край ноутбука', () => {
    // Тот же случай, что с позицией шарика: желаемое хранится как есть, а в
    // окно загоняется на каждом открытии.
    const huge = { width: 1700, height: 1300 };
    const anchor = panelAnchor(defaultPosition(LAPTOP), LAPTOP, huge);
    assert.equal(anchor.fullscreen, false, 'растянутая панель принята за телефон — ручки краёв пропадут');
    assert.equal(anchor.left, EDGE_MARGIN);
    assert.equal(anchor.top, EDGE_MARGIN);
    assert.equal(anchor.width, LAPTOP.width - 2 * EDGE_MARGIN);
    assert.equal(anchor.height, LAPTOP.height - 2 * EDGE_MARGIN);

    // Ширина чуть меньше желаемой — это всё ещё компьютер, а не телефон.
    const tight = panelAnchor({ x: 800, y: 600, dock: null }, { width: 920, height: 800 }, { width: 900, height: 520 });
    assert.equal(tight.fullscreen, false);
    assert.equal(tight.width, 896);
    assert.ok(tight.left >= EDGE_MARGIN && tight.left + tight.width <= 920 - EDGE_MARGIN);
});

test('размер из хранилища: мусор и мелочь дают стандартный', () => {
    for (const broken of [null, undefined, {}, 'нет', { width: 'широко' }, { width: NaN, height: Infinity }]) {
        assert.deepEqual(normalizePanelSize(broken), PANEL, `битое значение ${JSON.stringify(broken)}`);
    }
    // Запись от руки или от другой версии: меньше стандартного не бывает.
    assert.deepEqual(normalizePanelSize({ width: 100, height: -5 }), PANEL);
    assert.deepEqual(normalizePanelSize({ width: 640.4, height: 700.6 }), { width: 640, height: 701 });
    // Сверху не режем: потолок ставит окно, а не запись.
    assert.deepEqual(normalizePanelSize({ width: 5000, height: 4000 }), { width: 5000, height: 4000 });
});
