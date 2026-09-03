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
    clampPosition,
    defaultPosition,
    movedEnough,
    overlapsHamburger,
    panelAnchor,
    resolveDock,
    undock,
} from '../src/components/assistant/orbPosition.js';

const DESKTOP = { width: 1440, height: 900 };
const LAPTOP = { width: 1280, height: 720 };
const PHONE = { width: 375, height: 667 };
const PANEL = { width: 384, height: 520 };

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

test('на телефоне гамбургер защищён, на десктопе такой зоны нет', () => {
    // Шарик, севший на гамбургер, отнимает единственный вход в навигацию.
    assert.equal(overlapsHamburger({ x: 12, y: 12, dock: null }, PHONE), true);
    assert.equal(overlapsHamburger({ x: 200, y: 400, dock: null }, PHONE), false);
    assert.equal(overlapsHamburger({ x: 12, y: 12, dock: null }, DESKTOP), false);
});
