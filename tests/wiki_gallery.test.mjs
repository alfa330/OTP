import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DRAG_THRESHOLD, FLICK_SPEED, PAGE_RATIO, SCROLL_MS, WHEEL_SETTLE_MS,
  activeIndex, clampIndex, ease, pageTarget, scrollTargetFor, slideStepOf,
} from '../src/components/wiki/gallery.js';

/* Счётная часть галереи. Проверяется node --test, потому что витрина тянет за
   собой React и половину раздела, а ошибаться здесь есть где: ровно в этой
   арифметике и жили «стрелки не работают» и «кадр встал наполовину». */

const strip = (scrollLeft, clientWidth) => ({ scrollLeft, clientWidth });
const slide = (offsetLeft, offsetWidth) => ({ offsetLeft, offsetWidth });

test('открытый кадр — тот, чья середина ближе к середине ленты', () => {
  // Замер с живой статьи: лента 760, кадр 736, отступы по 12.
  const slides = [slide(12, 736), slide(760, 736)];
  assert.equal(activeIndex(strip(0, 760), slides), 0);
  assert.equal(activeIndex(strip(748, 760), slides), 1);
  // На середине пути открытым считается тот, к чьей середине ближе.
  assert.equal(activeIndex(strip(300, 760), slides), 0);
  assert.equal(activeIndex(strip(500, 760), slides), 1);
});

test('кадры разной ширины: считаем по серединам, а не по scrollLeft', () => {
  /* После правки через ИИ кадр приезжает внутри абзаца и бывает другой ширины.
     Открытым считается тот, чья середина ближе к середине ленты, — то есть
     самый заметный, а не первый попавшийся: при узком первом кадре (200) и
     широком втором (736) в начале прокрутки на экране в основном второй, и
     подпись обязана быть его. По scrollLeft этого не вычислить вовсе. */
  const slides = [slide(12, 200), slide(224, 736), slide(972, 200)];
  assert.equal(activeIndex(strip(0, 760), slides), 1);
  assert.equal(activeIndex(strip(224, 760), slides), 1);
  assert.equal(activeIndex(strip(700, 760), slides), 2);
});

test('пустая галерея не роняет расчёт', () => {
  assert.equal(activeIndex(strip(0, 760), []), 0);
});

test('цель прокрутки ставит кадр по центру ленты', () => {
  const box = strip(0, 760);
  assert.equal(scrollTargetFor(box, slide(12, 736)), 0);
  // Второй кадр обязан попадать РОВНО в максимум прокрутки: иначе он встаёт
  // наполовину, а scroll-snap возвращает его назад — это и есть дёрганье.
  assert.equal(scrollTargetFor(box, slide(760, 736)), 748);
});

test('до загрузки картинок размеры нулевые — расчёт обязан не врать', () => {
  // У <img> без width/height до загрузки нет собственных размеров. Модуль
  // пересчитывает всё по событию load; здесь важно, что до него арифметика
  // остаётся определённой и не даёт отрицательных целей.
  const slides = [slide(12, 0), slide(12, 0)];
  assert.equal(activeIndex(strip(0, 760), slides), 0);
  assert.equal(scrollTargetFor(strip(0, 760), slides[0]), 12 - 380);
});

test('индекс не выходит за границы', () => {
  assert.equal(clampIndex(-5, 3), 0);
  assert.equal(clampIndex(9, 3), 2);
  assert.equal(clampIndex(1, 3), 1);
});

test('порог перетаскивания больше нуля и меньше щелчка', () => {
  // Без порога любой щелчок по кадру превращался бы в микро-прокрутку.
  assert.ok(DRAG_THRESHOLD >= 2 && DRAG_THRESHOLD <= 10);
});

test('замедление к концу, а не рывок', () => {
  // Линейное движение читается как рывок: лента трогается и встаёт мгновенно.
  assert.equal(ease(0), 0);
  assert.equal(ease(1), 1);
  assert.ok(ease(0.5) > 0.5, 'к середине пути пройдено меньше половины');
  let prev = -1;
  for (let t = 0; t <= 1.0001; t += 0.1) {
    const value = ease(t);
    assert.ok(value >= prev, 'движение обязано быть монотонным');
    prev = value;
  }
});

test('доводка заметна, но не заставляет ждать', () => {
  assert.ok(SCROLL_MS >= 150 && SCROLL_MS <= 400);
});

test('прокрутка идёт присваиванием, а не scrollTo со smooth', async () => {
  /* ГЛАВНЫЙ сторож этого файла. scrollTo({behavior:'smooth'}) на ленте со
     scroll-snap-type: x mandatory НЕ РАБОТАЕТ: снап возвращает ленту к
     текущему кадру в начале анимации, и кнопка молча не делает ничего. Замер
     на живой галерее: scrollLeft оставался нулём при максимуме 868. */
  const source = await import('node:fs').then(
    (fs) => fs.readFileSync('src/components/wiki/gallery.js', 'utf8'));
  // Ищем сам ВЫЗОВ, а не упоминание: в комментарии выше эта конструкция
  // названа по имени как раз затем, чтобы никто не вернул её обратно.
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '');
  assert.doesNotMatch(code, /scrollTo\(/,
    'вернулась плавная прокрутка браузером — она проигрывает снапу');
  assert.doesNotMatch(code, /behavior:/,
    'вернулась плавная прокрутка браузером — она проигрывает снапу');
  assert.match(source, /scrollSnapType = 'none'/,
    'на время своей анимации снап обязан отключаться');
  assert.match(source, /requestAnimationFrame/);
});

/* ─────────────────────────────────────────────────────────────────────────
   ПРАВИЛО ЛИСТАНИЯ. Главная поломка галереи была не в арифметике, а в том,
   ЧЬЁ правило решало, где встать. Решал scroll-snap, а у него условие одно —
   «проехал больше половины кадра». Замер на живой галерее: двупальцевый свайп
   по трекпаду в 400 px при шаге кадра 808 оставлял ленту РОВНО НА МЕСТЕ, тот
   же жест без снапа давал честные 400. Снаружи это и есть «не свайпается».
   Ниже — правило, которым снап заменён.
   ───────────────────────────────────────────────────────────────────────── */

const STEP = 808;

test('короткий, но быстрый бросок листает — как в iOS', () => {
  // Ровно тот случай, который снап откатывал: проехали шестую часть кадра.
  const target = pageTarget({
    index: 0, landed: 0, count: 3, moved: 130, speed: 0.9, step: STEP,
  });
  assert.equal(target, 1);
});

test('медленное подталкивание на пятую часть кадра тоже листает', () => {
  const target = pageTarget({
    index: 1, landed: 1, count: 3, moved: STEP * PAGE_RATIO, speed: 0.02, step: STEP,
  });
  assert.equal(target, 2);
});

test('дрожание руки на щелчке кадр НЕ листает', () => {
  /* Без этого условия клик по кадру превращался бы в перелистывание, а
     ссылка внутри подписи перестала бы нажиматься. */
  const target = pageTarget({
    index: 1, landed: 1, count: 3, moved: 3, speed: 0.01, step: STEP,
  });
  assert.equal(target, 1);
});

test('назад листаем по знаку жеста, а не по его длине', () => {
  assert.equal(pageTarget({
    index: 2, landed: 2, count: 3, moved: -140, speed: -0.8, step: STEP,
  }), 1);
});

test('жест, уехавший дальше соседнего кадра, спорить с глазами не даёт', () => {
  /* Человек видит третий кадр и ждёт третий, а не «шаг вперёд от первого».
     Поэтому увиденное сильнее правила броска. */
  assert.equal(pageTarget({
    index: 0, landed: 2, count: 3, moved: STEP * 2.1, speed: 1.4, step: STEP,
  }), 2);
});

test('на краю ленты жест никуда не уводит', () => {
  assert.equal(pageTarget({
    index: 0, landed: 0, count: 3, moved: -400, speed: -1.2, step: STEP,
  }), 0);
  assert.equal(pageTarget({
    index: 2, landed: 2, count: 3, moved: 400, speed: 1.2, step: STEP,
  }), 2);
});

test('пустая галерея не роняет правило листания', () => {
  assert.equal(pageTarget({
    index: 0, landed: 0, count: 0, moved: 500, speed: 2, step: 0,
  }), 0);
});

test('порог броска и доля кадра — в разумных пределах', () => {
  // 0.35 px/мс ≈ 350 px/с: медленное подталкивание сюда не попадает, а
  // короткий резкий свайп попадает уверенно.
  assert.ok(FLICK_SPEED > 0.1 && FLICK_SPEED < 1);
  // Половину требует снап — и именно из-за неё казалось, что лента не едет.
  assert.ok(PAGE_RATIO > 0 && PAGE_RATIO < 0.5);
  // Пауза между событиями внутри одного свайпа — 8–16 мс.
  assert.ok(WHEEL_SETTLE_MS > 30 && WHEEL_SETTLE_MS < 300);
});

test('шаг кадра меряется по соседям, а не по ширине ленты', () => {
  /* Кадры бывают разной ширины (после правки через ИИ кадр приезжает внутри
     абзаца), и «сколько проехать до смены кадра» — это расстояние между их
     началами. Замер с живой галереи: лента 820, кадр 796, шаг 808. */
  const box = { scrollLeft: 0, clientWidth: 820 };
  const slides = [slide(12, 796), slide(820, 796), slide(1628, 796)];
  assert.equal(slideStepOf(box, slides, 0), 808);
  assert.equal(slideStepOf(box, slides, 2), 808);
});

test('единственному кадру шагом служит он сам', () => {
  const box = { scrollLeft: 0, clientWidth: 820 };
  assert.equal(slideStepOf(box, [slide(12, 194)], 0), 194);
});

test('шаг у пустой ленты — её собственная ширина, а не ноль', () => {
  // Ноль обнулил бы условие «далеко» и превратил бы любое дрожание в листание.
  assert.equal(slideStepOf({ scrollLeft: 0, clientWidth: 820 }, [], 0), 820);
});

test('перетаскивание и трекпад приводятся к масштабу раздела', async () => {
  /* У раздела свой zoom (wiki-scale.css, до 1.35). Курсор и колесо приходят в
     координатах экрана, то есть уже умноженными на масштаб, а scrollLeft живёт
     в единицах узла, где масштаба нет. Смешаешь — и лента обгоняет курсор на
     треть. Сторожим сам факт перевода: считать его тут нечем, DOM нет. */
  const source = await import('node:fs').then(
    (fs) => fs.readFileSync('src/components/wiki/gallery.js', 'utf8'));
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.match(code, /zoomOf\(strip\)/, 'движение руки обязано делиться на масштаб');
  assert.match(code, /currentCSSZoom/);
  assert.match(code, /event\.deltaX \/ zoomOf/, 'трекпад считает в тех же единицах, что и мышь');
});

test('тяга переживает уход курсора с ленты', async () => {
  /* Без захвата указателя длинная тяга обрывалась ровно тогда, когда курсор
     уходил с ленты, — а уходит он почти всегда: тянут на пол-экрана, а лента
     высотой в кадр. pointerleave по этой же причине НЕ подписан. */
  const source = await import('node:fs').then(
    (fs) => fs.readFileSync('src/components/wiki/gallery.js', 'utf8'));
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.match(code, /setPointerCapture/);
  assert.doesNotMatch(code, /'pointerleave'/,
    'отмена тяги по уходу курсора и была причиной обрыва на полпути');
});

test('повторный монтаж не убивает галерею', async () => {
  /* Эффект витрины отрабатывает ДВАЖДЫ на каждом открытии статьи (тело
     попадает в DOM раньше, чем поднимается bodyReady). Прежний сторож искал
     следы прошлой сборки и на втором заходе отказывался работать, а отмена эти
     следы не убирала — обвязка оставалась нарисованной, но без единого
     обработчика: стрелки не нажимались, мышью не тянулось, подпись врала.
     Признак обязан жить НА УЗЛЕ, а отмена — быть обратной монтажу. */
  const source = await import('node:fs').then(
    (fs) => fs.readFileSync('src/components/wiki/gallery.js', 'utf8'));
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.match(code, /strip\.__wikiGallery/, 'признак сборки обязан лежать на самой ленте');
  assert.match(code, /box\.replaceWith\(strip\)/, 'отмена обязана возвращать ленту на место');
  assert.doesNotMatch(code, /parentElement\?\.classList\.contains\('wiki-gallery'\)/,
    'сторож по следам в DOM и был причиной мёртвой галереи');
});

test('редактор оживляет галерею тем же кодом, что и витрина', async () => {
  /* Автор правит статью в том виде, в каком её увидит читатель. Пока листание
     жило только на витрине, галерея в редакторе не листалась вовсе: три
     вертикальных скриншота помещались в колонку целиком, scrollWidth равнялся
     clientWidth. */
  const view = await import('node:fs').then(
    (fs) => fs.readFileSync('src/components/wiki/galleryNodeView.js', 'utf8'));
  const node = await import('node:fs').then(
    (fs) => fs.readFileSync('src/components/wiki/WikiBlockNode.js', 'utf8'));
  assert.match(view, /attachGallery/, 'редактор обязан звать ту же обвязку, что и витрина');
  assert.match(view, /contentDOM/, 'содержимое ленты обязано остаться за ProseMirror');
  assert.match(view, /ignoreMutation/, 'обвязка не должна читаться редактором как правка');
  assert.match(node, /addNodeView\(\)/, 'вид узла обязан быть подключён к схеме');
});
