/*
 * «На сколько» в гостевом доступе вики — настоящий TermPicker через
 * react-dom/server (тот же каркас, что у wiki_section_access_render.test.mjs).
 *
 * Кнопка «Бессрочно» (решение владельца 25.11.2026: супер-админ, любой объект,
 * при выдаче и при продлении) видна ТОЛЬКО по признаку сервера
 * can_grant_permanent. Покажи её всем — и руководитель получит кнопку, на
 * которую сервер ответит 403; спрячь у супер-админа — и вернётся ровно та
 * жалоба, ради которой кнопка появилась: «пропала возможность добавлять
 * бессрочный доступ».
 *
 * JSX здесь не используется намеренно: node --test гоняет .mjs без сборки.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');
const { mkdirSync } = require('node:fs');
const { join } = require('node:path');
const { fileURLToPath } = require('node:url');

async function loadTermPicker() {
  /* Собранный модуль кладём ВНУТРЬ проекта: из системной временной папки
     `import 'react'` не разрешается — node ищет node_modules вверх от файла. */
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, 'WikiGuestsTermPicker.mjs');
  buildSync({
    entryPoints: [fileURLToPath(new URL('../src/components/wiki/WikiGuests.jsx',
                                        import.meta.url))],
    bundle: true,
    format: 'esm',
    target: 'node18',
    outfile,
    external: ['react', 'react-dom', 'axios', 'lucide-react'],
    loader: { '.jsx': 'jsx' },
    logLevel: 'silent',
  });
  const mod = await import(`file://${outfile.replace(/\\/g, '/')}`);
  return mod.TermPicker;
}

const TermPicker = await loadTermPicker();

const META = {
  max_days: 14, today: '2026-11-25', max_until: '2026-12-09', now_time: '12:30',
};
const noop = () => {};

const render = (props = {}) => renderToStaticMarkup(React.createElement(TermPicker, {
  meta: META, days: 7, until: '', atTime: '', permanent: false,
  onDays: noop, onUntil: noop, onTime: noop, onPermanent: noop,
  ...props,
}));

// Кнопки срока по подписи: «сегодня», «1 день» … «Бессрочно» — с aria-pressed.
const pressed = (html) => [...html.matchAll(/<button[^>]*aria-pressed="(true|false)"[^>]*>([^<]+)<\/button>/g)]
  .map(([, on, label]) => [label, on === 'true']);

const TIME = /<input[^>]*aria-label="Час, до которого действует гостевой доступ"[^>]*>/;

/* Подписи под «До какого часа»: обе в одной клетке сетки, видна одна. Отдаём
   [видимые, спрятанные] — по классу invisible. */
const HOUR_HINTS = {
  day: 'Не указан — доступ живёт до конца дня.',
  forever: 'У бессрочного доступа часа нет: он действует, пока его не отзовут.',
};
const hourHints = (html) => {
  const shown = [];
  const hidden = [];
  for (const [, cls, text] of html.matchAll(/<p class="col-start-1 row-start-1([^"]*)">([^<]+)<\/p>/g)) {
    const key = Object.keys(HOUR_HINTS).find((k) => text.replace(/\s+/g, ' ').includes(HOUR_HINTS[k]));
    (cls.includes('invisible') ? hidden : shown).push(key);
  }
  return [shown, hidden];
};

test('без права «Бессрочно» кнопки нет, потолок назван словами', () => {
  const html = render({ meta: { ...META, can_grant_permanent: false } });
  const labels = pressed(html).map(([label]) => label);
  assert.deepEqual(labels, ['сегодня', '1 день', '3 дня', '7 дней', '14 дней']);
  assert.match(html, /Дольше 14 дней выдать нельзя\. Бессрочный доступ выдаёт супер-администратор\./);
});

test('сервер старше фронта — без кнопки и без отсылки к супер-админу', () => {
  // Фронт уезжает на сайт сразу, сервер — ручной выкладкой позже. Старый
  // сервер поля can_grant_permanent не присылает, и супер-админ не должен
  // читать «бессрочный выдаёт супер-администратор» без кнопки рядом.
  const html = render();
  assert.equal(pressed(html).some(([label]) => label === 'Бессрочно'), false);
  assert.match(html, /Дольше 14 дней выдать нельзя\.</);
  assert.doesNotMatch(html, /супер-администратор/);
});

test('супер-админу — «Бессрочно» рядом с «14 дней», не нажата', () => {
  const html = render({ meta: { ...META, can_grant_permanent: true } });
  assert.deepEqual(pressed(html), [
    ['сегодня', false], ['1 день', false], ['3 дня', false], ['7 дней', true],
    ['14 дней', false], ['Бессрочно', false],
  ]);
  assert.match(html, /Дольше 14 дней — только «Бессрочно»\./);
  assert.doesNotMatch(TIME.exec(html)[0], /disabled=""/);
  // Подпись «бессрочно» уже лежит в клетке, но спрятана — место под неё занято
  // заранее, и нажатие «Бессрочно» не меняет высоту окна.
  assert.deepEqual(hourHints(html), [['day'], ['forever']]);
});

test('выбрано «Бессрочно» — дни не нажаты, час гаснет, а не исчезает', () => {
  const html = render({ meta: { ...META, can_grant_permanent: true },
                        permanent: true, atTime: '18:00' });
  assert.deepEqual(pressed(html).filter(([, on]) => on), [['Бессрочно', true]]);
  // Нажатая — тем же цветом, что нажатый пресет: aria-pressed без заливки
  // читалка бы видела, а человек нет.
  assert.match(html, /<button[^>]*aria-pressed="true"[^>]*class="[^"]*bg-indigo-600[^"]*"[^>]*>Бессрочно</);
  const time = TIME.exec(html);
  assert.ok(time, 'поле часа пропало — окно прыгнуло бы при переключении');
  assert.match(time[0], /disabled=""/);
  assert.match(time[0], /placeholder="—"/);
  // Названный раньше час не показываем погасшим: он с бессрочной не уедет.
  assert.match(time[0], /value=""/);
  assert.deepEqual(hourHints(html), [['forever'], ['day']]);
});

test('«бессрочно» без права не считается выбранным', () => {
  // Кнопки на экране нет — значит и выбранным оно было бы невидимо.
  const html = render({ permanent: true });
  assert.deepEqual(pressed(html).filter(([, on]) => on), [['7 дней', true]]);
  assert.doesNotMatch(TIME.exec(html)[0], /disabled=""/);
  assert.deepEqual(hourHints(html), [['day'], []]);
});

test('у выбранной даты ни один пресет не нажат — и «Бессрочно» тоже', () => {
  const html = render({ meta: { ...META, can_grant_permanent: true },
                        until: '2026-12-01' });
  assert.deepEqual(pressed(html).filter(([, on]) => on), []);
});

/* Нажатия. TermPicker — функция без хуков, поэтому её можно вызвать напрямую и
 * пройти по возвращённому дереву элементов: кнопки и пикер даты лежат в нём
 * со своими обработчиками. Серверный рендер обработчиков не видит, а именно в
 * них живёт «Бессрочно, пресет и дата исключают друг друга». */
const walk = (node, visit) => {
  if (Array.isArray(node)) { node.forEach((child) => walk(child, visit)); return; }
  if (!node || typeof node !== 'object' || !node.props) return;
  visit(node);
  walk(node.props.children, visit);
};

const textOf = (node) => {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  return textOf(node.props?.children);
};

const harness = (props = {}) => {
  const calls = [];
  const spy = (name) => (value) => calls.push([name, value]);
  const tree = TermPicker({
    meta: { ...META, can_grant_permanent: true }, days: 7, until: '', atTime: '18:00',
    permanent: false,
    onDays: spy('days'), onUntil: spy('until'), onTime: spy('time'),
    onPermanent: spy('permanent'),
    ...props,
  });
  const buttons = new Map();
  let datePicker = null;
  walk(tree, (node) => {
    if (node.type === 'button') buttons.set(textOf(node).trim(), node);
    if (node.props.ariaLabel === 'Дата, до которой действует гостевой доступ') datePicker = node;
  });
  return { calls, buttons, datePicker };
};

test('«Бессрочно» снимает дату, час не стирает', () => {
  const { calls, buttons } = harness({ until: '2026-12-01' });
  buttons.get('Бессрочно').props.onClick();
  assert.deepEqual(calls, [['permanent', true], ['until', '']]);
});

test('пресет снимает «Бессрочно» и дату', () => {
  const { calls, buttons } = harness({ permanent: true });
  buttons.get('3 дня').props.onClick();
  assert.deepEqual(calls, [['days', 3], ['until', ''], ['permanent', false]]);
});

test('выбранная дата снимает «Бессрочно», очищенная — нет', () => {
  const picked = harness({ permanent: true });
  picked.datePicker.props.onChange('2026-12-01');
  assert.deepEqual(picked.calls, [['until', '2026-12-01'], ['permanent', false]]);
  // Очистили дату — «до какого срока» не назван заново, и снимать выбор нечем.
  const cleared = harness({ until: '2026-12-01' });
  cleared.datePicker.props.onChange('');
  assert.deepEqual(cleared.calls, [['until', '']]);
  // Дата мимо рамок сервера подтягивается к границе (ловушка панели пикера).
  const clamped = harness();
  clamped.datePicker.props.onChange('2027-01-15');
  assert.deepEqual(clamped.calls, [['until', '2026-12-09'], ['permanent', false]]);
});
