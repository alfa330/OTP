/*
 * Подсказка «i» на телефоне. Одно касание даёт цепочку «наведение (его
 * достраивает браузер) → фокус → клик»; пока подсказка открывалась от наведения
 * и от фокуса, клик следом её закрывал — «i» срабатывал только со второго раза.
 * На компьютере этого не видно при любой ошибке, поэтому правило закреплено
 * здесь: и сама логика, и то, что компонент ею пользуется.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { clickClosesHint, hoverOpensHint } from '../src/components/common/infoHintPress.js';

const component = readFileSync(new URL('../src/components/common/InfoHint.jsx', import.meta.url), 'utf8');

/* Модель кнопки: те же обработчики, что в компоненте, в порядке, в каком
   события приходят от браузера. */
const makeHint = () => {
  const hint = { open: false, openOnPress: null };
  return {
    hint,
    pointerEnter: (type) => { if (hoverOpensHint(type)) hint.open = true; },
    pointerLeave: (type) => { if (hoverOpensHint(type)) hint.open = false; },
    pointerDown: () => { hint.openOnPress = hint.open; },
    focus: () => { hint.open = true; },
    blur: () => { hint.openOnPress = null; hint.open = false; },
    click: () => {
      const closes = clickClosesHint({ openOnPress: hint.openOnPress, open: hint.open });
      hint.openOnPress = null;
      hint.open = !closes;
    },
  };
};

test('телефон (Android): первое касание открывает подсказку, второе закрывает', () => {
  const button = makeHint();
  // касание 1: наведение от касания, нажатие, фокус, клик
  button.pointerEnter('touch');
  button.pointerDown();
  button.focus();
  button.click();
  assert.equal(button.hint.open, true, 'с первого касания подсказка должна остаться открытой');
  // касание 2: кнопка уже в фокусе, фокус не приходит
  button.pointerEnter('touch');
  button.pointerDown();
  button.click();
  assert.equal(button.hint.open, false);
  // касание 3 — снова открывает
  button.pointerDown();
  button.click();
  assert.equal(button.hint.open, true);
});

test('iPhone: кнопка по касанию фокус не получает — открывает сам клик', () => {
  const button = makeHint();
  button.pointerEnter('touch');
  button.pointerDown();
  button.click();
  assert.equal(button.hint.open, true);
  button.pointerDown();
  button.click();
  assert.equal(button.hint.open, false);
});

test('мышь: наведение открывает, уход закрывает, клик по открытой — закрывает', () => {
  const button = makeHint();
  button.pointerEnter('mouse');
  assert.equal(button.hint.open, true);
  button.pointerLeave('mouse');
  assert.equal(button.hint.open, false);
  button.pointerEnter('mouse');
  button.pointerDown();
  button.focus();
  button.click();
  assert.equal(button.hint.open, false);
});

test('клавиатура: фокус открывает, Enter переключает', () => {
  const button = makeHint();
  button.focus();
  assert.equal(button.hint.open, true);
  button.click();
  assert.equal(button.hint.open, false, 'нажатия указателем не было — переключаем по тому, что на экране');
  button.click();
  assert.equal(button.hint.open, true);
  button.blur();
  assert.equal(button.hint.open, false);
});

test('наведение слушаем только у мыши', () => {
  assert.equal(hoverOpensHint('mouse'), true);
  assert.equal(hoverOpensHint('touch'), false);
  assert.equal(hoverOpensHint('pen'), false, 'перо чаще касается, чем парит над экраном');
  assert.equal(hoverOpensHint(undefined), false);
});

test('компонент пользуется этими правилами, а не событиями мыши', () => {
  const button = component.split('<button')[1].split('</button>')[0];
  assert.ok(button.includes('onPointerEnter={(event) => { if (hoverOpensHint(event.pointerType)) place(); }}'));
  assert.ok(button.includes('onPointerLeave={(event) => { if (hoverOpensHint(event.pointerType)) closeSoon(); }}'));
  assert.ok(button.includes('onPointerDown={() => { openOnPress.current = open; }}'));
  assert.ok(button.includes('clickClosesHint({ openOnPress: openOnPress.current, open })'));
  assert.ok(!button.includes('onMouseEnter'), 'наведение мыши браузер достраивает и для касания');
  assert.ok(!button.includes('if (open) close(); else place();'), 'клик не должен смотреть на состояние «сейчас»');
  // касание мимо закрывает подсказку — blur на iPhone не приходит
  assert.ok(component.includes("document.addEventListener('pointerdown', onPressOutside, true)"));
  assert.ok(component.includes("document.removeEventListener('pointerdown', onPressOutside, true)"));
});
