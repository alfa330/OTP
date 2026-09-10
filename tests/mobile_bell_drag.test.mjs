// Колокол на телефоне можно перетащить: геометрия места и сам жест.
//
// Зачем это вообще: колокол висит поверх содержимого в правом верхнем углу и
// закрывает собой то, что там рисует раздел. У инструкции аукциона смен под ним
// оказался крестик — инструкцию нельзя было закрыть вовсе, тап приходил
// колоколу. Углом дело не ограничивается, поэтому колокол сделан подвижным.
//
// Две вещи ломаются молча и обе — только на чужом телефоне:
//
//   * ГРАНИЦЫ МЕСТА. Позиция переживает поворот и переезд на другой аппарат.
//     Не загнав её в экран заново, получаем колокол за нижней гранью или на
//     кнопках разделов — то есть уведомления, до которых уже не дотянуться и
//     которые поэтому нельзя вернуть на место;
//   * ПОРЯДОК СОБЫТИЙ ЖЕСТА. Захват указателя, поставленный на нажатии,
//     перенаправляет и мышиные события, из которых браузер собирает click, —
//     обычный тап по колоколу переставал открывать уведомления. Проверено
//     живым прогоном, а не рассуждением.
//
// Запуск: node --test tests/mobile_bell_drag.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    BELL_EDGE_MARGIN,
    BELL_SIZE,
    TAB_BAR_SIDE,
    TAB_BAR_THICKNESS,
    clampBellPosition,
    tabBarInsets,
} from '../src/utils/mobileShell.js';
import { DRAG_THRESHOLD, movedEnough } from '../src/utils/dragGesture.js';
import { DRAG_THRESHOLD as ORB_THRESHOLD } from '../src/components/assistant/orbPosition.js';

const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const slotSource = readLf('src/components/common/MobileBellSlot.jsx');
const shellCss = readLf('src/components/common/mobile-shell.css');
const appSource = readLf('src/App.jsx');

const PHONE = { width: 390, height: 844 };

test('бар съедает свою грань, и только её', () => {
    assert.deepEqual(tabBarInsets({ shell: true, side: TAB_BAR_SIDE.BOTTOM }),
        { top: 0, right: 0, bottom: TAB_BAR_THICKNESS, left: 0 });
    assert.deepEqual(tabBarInsets({ shell: true, side: TAB_BAR_SIDE.RIGHT }),
        { top: 0, right: TAB_BAR_THICKNESS, bottom: 0, left: 0 });
    assert.deepEqual(tabBarInsets({ shell: true, side: TAB_BAR_SIDE.LEFT }),
        { top: 0, right: 0, bottom: 0, left: TAB_BAR_THICKNESS });
    // Вне оболочки бара нет вовсе — настольная вёрстка от него не зависит.
    assert.deepEqual(tabBarInsets({ shell: false, side: TAB_BAR_SIDE.LEFT }),
        { top: 0, right: 0, bottom: 0, left: 0 });
    assert.deepEqual(tabBarInsets(undefined), { top: 0, right: 0, bottom: 0, left: 0 });
});

test('колокол не выходит за экран и не садится на бар', () => {
    const bottom = clampBellPosition({ x: 9999, y: 9999 }, PHONE, TAB_BAR_SIDE.BOTTOM);
    assert.equal(bottom.x, PHONE.width - BELL_SIZE - BELL_EDGE_MARGIN);
    assert.equal(bottom.y, PHONE.height - BELL_SIZE - BELL_EDGE_MARGIN - TAB_BAR_THICKNESS);

    const negative = clampBellPosition({ x: -500, y: -500 }, PHONE, TAB_BAR_SIDE.BOTTOM);
    assert.deepEqual(negative, { x: BELL_EDGE_MARGIN, y: BELL_EDGE_MARGIN });

    // Боком бар стоит вертикальной полосой — занята уже другая грань.
    const landscape = { width: 844, height: 390 };
    const right = clampBellPosition({ x: 9999, y: 9999 }, landscape, TAB_BAR_SIDE.RIGHT);
    assert.equal(right.x, landscape.width - BELL_SIZE - BELL_EDGE_MARGIN - TAB_BAR_THICKNESS);
    assert.equal(right.y, landscape.height - BELL_SIZE - BELL_EDGE_MARGIN);

    const left = clampBellPosition({ x: -9999, y: 0 }, landscape, TAB_BAR_SIDE.LEFT);
    assert.equal(left.x, BELL_EDGE_MARGIN + TAB_BAR_THICKNESS);
});

test('вырезы устройства тоже закрыты для колокола', () => {
    /* На iPhone в режиме приложения сверху чёлка, снизу — полоса жеста «домой».
       Без них колокол паркуется под чёлкой: он там наполовину не виден и всё
       так же перекрывает содержимое. */
    const safe = { top: 59, right: 0, bottom: 34, left: 0 };
    const top = clampBellPosition({ x: 100, y: 0 }, PHONE, TAB_BAR_SIDE.BOTTOM, safe);
    assert.equal(top.y, BELL_EDGE_MARGIN + safe.top);
    // Снизу уже стоит бар — он толще полосы жеста, поэтому побеждает он.
    const bottom = clampBellPosition({ x: 100, y: 9999 }, PHONE, TAB_BAR_SIDE.BOTTOM, safe);
    assert.equal(bottom.y, PHONE.height - BELL_SIZE - BELL_EDGE_MARGIN - TAB_BAR_THICKNESS);
    // А боком бар уходит вбок, и низ достаётся полосе жеста.
    const side = clampBellPosition({ x: 100, y: 9999 }, PHONE, TAB_BAR_SIDE.RIGHT, safe);
    assert.equal(side.y, PHONE.height - BELL_SIZE - BELL_EDGE_MARGIN - safe.bottom);
});

test('мусор вместо координат не уносит колокол в никуда', () => {
    for (const broken of [null, undefined, {}, { x: 'левее', y: NaN }, { x: Infinity, y: 10 }]) {
        const at = clampBellPosition(broken, PHONE, TAB_BAR_SIDE.BOTTOM);
        assert.ok(Number.isFinite(at.x) && Number.isFinite(at.y), `сломалось на ${JSON.stringify(broken)}`);
        assert.ok(at.x >= BELL_EDGE_MARGIN && at.y >= BELL_EDGE_MARGIN);
    }
});

test('порог «нажал или потащил» один на колокол и на шарика помощника', () => {
    // Две похожие кнопки на одном экране, ведущие себя по-разному, учат не
    // жесту, а конкретной кнопке.
    assert.equal(DRAG_THRESHOLD, ORB_THRESHOLD);
    assert.equal(movedEnough({ x: 0, y: 0 }, { x: DRAG_THRESHOLD - 1, y: 0 }), false);
    assert.equal(movedEnough({ x: 0, y: 0 }, { x: 0, y: DRAG_THRESHOLD }), true);
});

test('жест ведут слушатели окна, а не сам слот', () => {
    /* ПРОВЕРЕНО ЖИВЫМ ПРОГОНОМ. setPointerCapture на нажатии перенаправляет на
       слот и мышиные события, из которых собирается click, — тап по колоколу
       переставал открывать уведомления. А без окна pointermove уходит тому, что
       под курсором: палец обгоняет колокол и жест срывается на первом рывке. */
    assert.match(slotSource, /window\.addEventListener\('pointermove', onMove\)/);
    assert.match(slotSource, /window\.addEventListener\('pointerup', onUp\)/);
    assert.match(slotSource, /window\.addEventListener\('pointercancel', onUp\)/);
    assert.match(slotSource, /window\.removeEventListener\('pointermove', onMove\)/);
    // Ищем именно ВЫЗОВ: слово встречается ещё и в объяснении рядом.
    assert.doesNotMatch(slotSource, /\.setPointerCapture\(/);
    // Слушатели окна переживают размонтирование — снимать их обязательно.
    assert.match(slotSource, /useEffect\(\(\) => \(\) => \{ detachRef\.current\?\.\(\); \}, \[\]\);/);
});

test('клик после перетаскивания гасится, обычный тап — нет', () => {
    // Кнопку рисует NotificationsBell, её onClick нам не принадлежит: иначе
    // каждое перетаскивание заканчивалось бы раскрытым листом уведомлений.
    assert.match(slotSource, /onClickCapture=\{onClickCapture\}/);
    assert.match(slotSource, /suppressClickRef\.current = true;/);
    assert.match(slotSource, /if \(!drag\.moved\) return;/);
    // Жест без клика не должен оставить заслонку взведённой — она съела бы
    // следующий, уже настоящий тап.
    assert.match(slotSource, /suppressClickRef\.current = false; \}, 400\);/);
});

test('колокол двигается координатами, а не сдвигом', () => {
    /* Внутри слота лежит лист уведомлений с position: fixed. transform на слоте
       сделал бы его точкой отсчёта для этого fixed, и лист уехал бы за
       колоколом в угол — та же ловушка, что была со шторкой разделов. */
    assert.match(slotSource, /style=\{position \? \{ top: position\.y, left: position\.x, right: 'auto' \} : undefined\}/);
    assert.doesNotMatch(slotSource, /transform:/);
});

test('прокрутку страницы жест отключает только под кнопкой', () => {
    /* touch-action: none на всём слоте убил бы прокрутку списка уведомлений —
       он лежит внутри. Потомки кнопки перечислены отдельно: свойство не
       наследуется, а на кнопке сидит бейдж со счётчиком. */
    assert.match(shellCss, /\.mobile-bell-slot > div > button,\s*\n\.mobile-bell-slot > div > button \* \{\s*\n\s*touch-action: none;/);
    const slotBlock = shellCss.slice(shellCss.indexOf('.mobile-bell-slot {'), shellCss.indexOf('.mobile-bell-slot > div > button'));
    assert.doesNotMatch(slotBlock, /touch-action/, 'запрет на слоте убьёт прокрутку листа уведомлений');
});

test('в углу экрана стоит перетаскиваемый слот, а не голый div', () => {
    assert.match(appSource, /<MobileBellSlot userId=\{user\?\.id\} side=\{mobileTabSide\}>\{bell\}<\/MobileBellSlot>/);
    assert.doesNotMatch(appSource, /<div className="mobile-bell-slot">/);
    // Колокол по-прежнему один на портал: второй занял бы второй слот SSE.
    assert.equal(appSource.split('<MobileBellSlot').length - 1, 1);
});

test('место колокола переживает поворот и перезагрузку', () => {
    assert.match(slotSource, /const STORAGE_PREFIX = 'otp_mobile_bell:';/);
    // Хранилище закрыто в приватном режиме — падать из-за него нельзя.
    assert.equal(slotSource.split('try {').length - 1 >= 2, true);
    assert.match(slotSource, /window\.addEventListener\('orientationchange', onResize\)/);
    assert.match(slotSource, /window\.addEventListener\('resize', onResize\)/);
    // Сохранённое место загоняется в экран ПРИ ЧТЕНИИ, а не только при жесте.
    assert.match(slotSource, /setPosition\(clampBellPosition\(stored, viewportSize\(\), sideRef\.current, safeArea\(\)\)\)/);
});
