import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PRODUCTION_ITEM_OPTIONS,
  PRODUCTION_MAX_SIDE,
  briefFromTask,
  briefToPayload,
  emptyProductionBrief,
  frameSize,
  hexColorsIn,
  isProductionPerson,
  newCustomSize,
  productionAvailableFor,
  productionBriefIssues,
  productionBriefRows,
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

test('продакшн доступен, только когда среди исполнителей есть маркетинг', () => {
  const recipients = [
    { id: 7, name: 'Дизайнер', department_code: 'marketing' },
    { id: 8, name: 'Оператор', department_code: 'szov' },
    { id: 9, name: 'Без отдела', department_code: null },
  ];
  assert.equal(productionAvailableFor(['7'], recipients), true);
  assert.equal(productionAvailableFor([8, 7], recipients), true);
  assert.equal(productionAvailableFor(['8', '9'], recipients), false);
  assert.equal(productionAvailableFor([], recipients), false);
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

test('«Свои тона» требуют написать, какие', () => {
  assert.deepEqual(keys(productionBriefIssues(filled({ color: 'custom', colorNotes: '  ' }), 'т')), ['colorNotes']);
  assert.deepEqual(productionBriefIssues(filled({ color: 'custom', colorNotes: 'жёлтый' }), 'т'), []);
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

test('тело запроса: порядок справочника, свои размеры числами, тона только у «Своих тонов»', () => {
  const payload = briefToPayload(filled({
    channels: ['outdoor', 'social'],
    sizes: ['9x16', '1x1'],
    customSizes: [
      { ...newCustomSize(), width: '728', height: '90' },
      newCustomSize(),
    ],
    color: 'designer',
    colorNotes: 'останется в форме, но не уйдёт',
  }));
  assert.deepEqual(payload, {
    item: 'static_banner',
    channels: ['social', 'outdoor'],
    sizes: ['1x1', '9x16'],
    custom_sizes: [{ width: 728, height: 90, unit: 'px' }],
    color: 'designer',
    color_notes: '',
  });
});

test('бриф из ответа сервера возвращается в форму без потерь', () => {
  const server = {
    item: 'promo_video',
    channels: ['social'],
    sizes: ['16x9'],
    custom_sizes: [{ width: 1200, height: 628, unit: 'px' }],
    color: 'custom',
    color_notes: 'жёлтый #FFD400',
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

test('пункты списка: заголовок группы у первого пункта каждой группы', () => {
  const headed = PRODUCTION_ITEM_OPTIONS.filter((option) => option.groupLabel);
  assert.deepEqual(headed.map((option) => option.groupLabel), ['Полиграфия', 'Digital', 'Видео и анимация', 'Бренд и упаковка']);
  assert.equal(PRODUCTION_ITEM_OPTIONS[0].value, 'leaflet');
});

test('рамка плитки сохраняет пропорцию и не превращается в черту', () => {
  assert.deepEqual(frameSize(1080, 1080), { width: 28, height: 28 });
  assert.deepEqual(frameSize(1920, 1080), { width: 28, height: 16 });
  assert.deepEqual(frameSize(1080, 1920), { width: 16, height: 28 });
  // Ролл-ап 850×2000: короткая сторона не уже трети рамки.
  assert.deepEqual(frameSize(850, 2000), { width: 12, height: 28 });
});

test('размер и цвета для показа', () => {
  assert.equal(sizeDimensions(1080, 1920, 'px'), '1080×1920 px');
  assert.equal(sizeDimensions(210, 297, 'mm'), '210×297 мм');
  assert.deepEqual(hexColorsIn('жёлтый #FFD400, чёрный #000 и снова #ffd400'), ['#ffd400', '#000']);
  assert.deepEqual(hexColorsIn(''), []);
});

test('строки карточки: подписи вместо кодов, свои размеры после готовых', () => {
  const rows = productionBriefRows({
    item: 'static_banner',
    channels: ['social', 'gone'],
    sizes: ['a4'],
    custom_sizes: [{ width: 728, height: 90, unit: 'px' }],
    color: 'custom',
    color_notes: 'жёлтый',
  });
  assert.deepEqual(rows.map((row) => row.key), ['item', 'channels', 'sizes', 'color']);
  assert.equal(rows[0].text, 'Статичный баннер');
  assert.deepEqual(rows[1].items, ['Социальные сети и мессенджеры', 'gone']);
  assert.deepEqual(rows[2].sizes.map((size) => size.label), ['A4', '']);
  assert.equal(rows[3].notes, 'жёлтый');
  assert.deepEqual(productionBriefRows(null), []);
});
