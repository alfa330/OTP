import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DRAG_THRESHOLD, SCROLL_MS, activeIndex, clampIndex, ease, scrollTargetFor,
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
