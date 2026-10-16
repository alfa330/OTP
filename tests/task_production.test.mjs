import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PRODUCTION_ITEM_OPTIONS,
  PRODUCTION_MAX_COLORS,
  PRODUCTION_MAX_SIDE,
  addPaletteColor,
  briefFromTask,
  briefToPayload,
  emptyProductionBrief,
  frameSize,
  isProductionPerson,
  newCustomSize,
  productionAllowedFor,
  productionBriefIssues,
  productionBriefRows,
  productionPersonIds,
  productionTeamFor,
  replacePaletteColor,
  sizeDimensions,
} from '../src/components/tasks/productionBrief.js';

const filled = (patch = {}) => ({
  ...emptyProductionBrief(),
  item: 'static_banner',
  channels: ['social'],
  sizes: ['9x16'],
  color: 'brandbook',
  ...patch,
});

const keys = (issues) => issues.map((issue) => issue.key);

test('у продакшна все исполнители — из маркетинга (уточнение владельца 16.10.2026)', () => {
  const recipients = [
    { id: 7, name: 'Дизайнер', department_code: 'marketing' },
    { id: 6, name: 'Видеограф', department_code: 'marketing' },
    { id: 8, name: 'Оператор', department_code: 'szov' },
    { id: 9, name: 'Без отдела', department_code: null },
  ];
  assert.deepEqual([...productionPersonIds(recipients)].sort(), ['6', '7']);
  assert.equal(productionTeamFor(['7'], recipients), true);
  assert.equal(productionTeamFor([6, 7, '7'], recipients), true);
  assert.equal(productionTeamFor([8, 7], recipients), false, 'один посторонний — уже нельзя');
  assert.equal(productionTeamFor(['9'], recipients), false);
  assert.equal(productionTeamFor([], recipients), false);
  assert.equal(isProductionPerson({ department_code: 'marketing' }), true);
  assert.equal(isProductionPerson({}), false);
});

test('пустой бриф: все поля по порядку формы, описание последним', () => {
  assert.deepEqual(keys(productionBriefIssues(emptyProductionBrief(), '')), [
    'item', 'channels', 'sizes', 'color', 'description',
  ]);
});

test('заполненный бриф с описанием — пунктов нет', () => {
  assert.deepEqual(productionBriefIssues(filled(), 'Баннер к акции'), []);
});

test('без маркетолога в составе — первый пункт про тип', () => {
  const issues = productionBriefIssues(filled(), 'текст', { productionAllowed: false });
  assert.deepEqual(keys(issues), ['tag']);
  assert.match(issues[0].text, /маркетинга/);
});

test('правка: старый продакшн с прежним составом не требует маркетолога — как на сервере', () => {
  // Маркетолог перешёл в отдел HR после постановки.
  const recipients = [{ id: 7, department_code: 'hr' }, { id: 8, department_code: 'szov' }];
  const initial = { tag: 'production', assigneeIds: ['7'] };
  assert.equal(productionAllowedFor(['7'], recipients, initial), true, 'тот же состав — правится');
  assert.equal(productionAllowedFor([7], [], initial), true, 'и когда человека нет в списке получателей');
  assert.equal(productionAllowedFor(['7', '8'], recipients, initial), false, 'состав сменили — нужен маркетолог');
  assert.equal(productionAllowedFor(['7'], recipients, { tag: 'task', assigneeIds: ['7'] }), false, 'тип только появляется');
  assert.equal(productionAllowedFor(['7'], recipients), false, 'новая задача');
  assert.equal(productionAllowedFor(['7'], [{ id: 7, department_code: 'marketing' }]), true);
  assert.equal(productionAllowedFor(['7', '8'], [{ id: 7, department_code: 'marketing' }, { id: 8 }]), false);
});

test('«Свои тона» требуют хотя бы один цвет палитры', () => {
  assert.deepEqual(keys(productionBriefIssues(filled({ color: 'custom', colors: [] }), 'т')), ['colors']);
  assert.deepEqual(productionBriefIssues(filled({ color: 'custom', colors: ['#FFD400'] }), 'т'), []);
});

test('палитра: без повторов, по порядку добавления, не больше двадцати', () => {
  let colors = [];
  colors = addPaletteColor(colors, '#ffd400');
  colors = addPaletteColor(colors, '1A1916');
  colors = addPaletteColor(colors, '#FFD400');
  assert.deepEqual(colors, ['#FFD400', '#1A1916']);
  assert.deepEqual(addPaletteColor(colors, 'не цвет'), colors);
  assert.deepEqual(replacePaletteColor(colors, 0, '#00aaff'), ['#00AAFF', '#1A1916']);
  // Заменили на уже выбранный — повтор схлопывается.
  assert.deepEqual(replacePaletteColor(colors, 0, '#1A1916'), ['#1A1916']);
  let full = [];
  for (let index = 1; index <= PRODUCTION_MAX_COLORS + 3; index += 1) {
    full = addPaletteColor(full, `#${(index * 0x0a0a0a).toString(16).padStart(6, '0')}`);
  }
  assert.equal(full.length, PRODUCTION_MAX_COLORS);
});

test('свой размер: пустая строка не мешает, половина или мусор — пункт', () => {
  const empty = newCustomSize();
  assert.deepEqual(productionBriefIssues(filled({ customSizes: [empty] }), 'т'), []);
  // Без готового формата пустая строка размера не считается размером.
  assert.deepEqual(keys(productionBriefIssues(filled({ sizes: [], customSizes: [empty] }), 'т')), ['sizes']);
  const half = { ...newCustomSize(), width: '728' };
  assert.match(productionBriefIssues(filled({ customSizes: [half] }), 'т')[0].text, /и ширину, и высоту/);
  const huge = { ...newCustomSize(), width: String(PRODUCTION_MAX_SIDE + 1), height: '90' };
  assert.match(productionBriefIssues(filled({ customSizes: [huge] }), 'т')[0].text, /целые числа/);
  const ok = { ...newCustomSize('mm'), width: '850', height: '2000' };
  assert.deepEqual(productionBriefIssues(filled({ sizes: [], customSizes: [ok] }), 'т'), []);
});

test('тело запроса: порядок справочника, свои размеры числами, палитра только у «Своих тонов»', () => {
  const payload = briefToPayload(filled({
    channels: ['outdoor', 'social'],
    sizes: ['9x16', '1x1'],
    customSizes: [
      { ...newCustomSize(), width: '728', height: '90' },
      newCustomSize(),
    ],
    color: 'designer',
    colors: ['#FFD400'],
  }));
  assert.deepEqual(payload, {
    item: 'static_banner',
    channels: ['social', 'outdoor'],
    sizes: ['1x1', '9x16'],
    custom_sizes: [{ width: 728, height: 90, unit: 'px' }],
    color: 'designer',
    colors: [],
  });
  assert.deepEqual(briefToPayload(filled({ color: 'custom', colors: ['#ffd400', '#FFD400', '#1a1916'] })).colors,
    ['#FFD400', '#1A1916']);
});

test('бриф из ответа сервера возвращается в форму без потерь', () => {
  const server = {
    item: 'promo_video',
    channels: ['social'],
    sizes: ['16x9'],
    custom_sizes: [{ width: 1200, height: 628, unit: 'px' }],
    color: 'custom',
    colors: ['#FFD400', '#1A1916'],
  };
  const form = briefFromTask(server);
  assert.equal(form.item, 'promo_video');
  assert.deepEqual(form.customSizes.map(({ width, height, unit }) => ({ width, height, unit })), [
    { width: '1200', height: '628', unit: 'px' },
  ]);
  assert.deepEqual(briefToPayload(form), server);
  // Чужие коды форма не показывает.
  assert.equal(briefFromTask({ item: 'gone', color: 'rainbow' }).item, '');
  assert.equal(briefFromTask({ item: 'gone', color: 'rainbow' }).color, null);
  assert.deepEqual(briefFromTask(null), emptyProductionBrief());
});

test('пункты списка: у каждого своя группа — заголовки верны и в результатах поиска', () => {
  assert.ok(PRODUCTION_ITEM_OPTIONS.every((option) => option.groupLabel));
  // Так CustomSelect решает, где нарисовать заголовок группы (startsGroup).
  const headers = (options) => options
    .filter((option, index) => option.groupLabel !== options[index - 1]?.groupLabel)
    .map((option) => option.groupLabel);
  assert.deepEqual(headers(PRODUCTION_ITEM_OPTIONS), ['Полиграфия', 'Digital', 'Видео и анимация', 'Бренд и упаковка']);
  const found = PRODUCTION_ITEM_OPTIONS.filter((option) => option.label.toLowerCase().includes('ан'));
  const animated = found.find((option) => option.value === 'animation_2d');
  assert.equal(animated.groupLabel, 'Видео и анимация');
  assert.deepEqual(headers(found), ['Digital', 'Видео и анимация']);
  assert.equal(PRODUCTION_ITEM_OPTIONS[0].value, 'leaflet');
});

test('рамка плитки сохраняет пропорцию и не превращается в черту', () => {
  assert.deepEqual(frameSize(1080, 1080), { width: 28, height: 28 });
  assert.deepEqual(frameSize(1920, 1080), { width: 28, height: 16 });
  assert.deepEqual(frameSize(1080, 1920), { width: 16, height: 28 });
  // Ролл-ап 850×2000: короткая сторона не уже трети рамки.
  assert.deepEqual(frameSize(850, 2000), { width: 12, height: 28 });
});

test('размер для показа', () => {
  assert.equal(sizeDimensions(1080, 1920, 'px'), '1080×1920 px');
  assert.equal(sizeDimensions(210, 297, 'mm'), '210×297 мм');
});

test('строки карточки: подписи вместо кодов, свои размеры после готовых', () => {
  const rows = productionBriefRows({
    item: 'static_banner',
    channels: ['social', 'gone'],
    sizes: ['a4'],
    custom_sizes: [{ width: 728, height: 90, unit: 'px' }],
    color: 'custom',
    colors: ['#ffd400', 'не цвет'],
  });
  assert.deepEqual(rows.map((row) => row.key), ['item', 'channels', 'sizes', 'color']);
  assert.equal(rows[0].text, 'Статичный баннер');
  assert.deepEqual(rows[1].items, ['Социальные сети и мессенджеры', 'gone']);
  assert.deepEqual(rows[2].sizes.map((size) => size.label), ['A4', '']);
  assert.deepEqual(rows[3].colors, ['#FFD400']);
  assert.deepEqual(productionBriefRows(null), []);
});
