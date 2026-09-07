import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BILINGUAL_SEPARATOR,
  applyFormatting,
  composeBilingual,
  formatCount,
  parseMailingText,
  plural,
  splitBilingual,
  stripMailingMarkup,
} from '../src/components/driver_mailings/mailingText.js';
import {
  countActiveFilters,
  describeAudience,
  describeFilters,
  formatCountdown,
  parksLabel,
  recipientsLabel,
  revokeSecondsLeft,
  statusMeta,
  targetStatusMeta,
} from '../src/components/driver_mailings/mailingMeta.js';

/* Разметка текста рассылки: приложение Pro понимает только жирный, курсив,
   ссылку и список. Всё остальное обязано доехать до водителя как есть. */

test('жирный, курсив и ссылка разбираются в узлы', () => {
  const [block] = parseMailingText('Привет, **Иван**, читайте _правила_ на [сайте](https://a.kz)');
  assert.equal(block.type, 'line');
  const kinds = block.nodes.map((node) => (typeof node === 'string' ? 'text' : node.type));
  assert.deepEqual(kinds, ['text', 'bold', 'text', 'italic', 'text', 'link']);
  const link = block.nodes.find((node) => node.type === 'link');
  assert.equal(link.href, 'https://a.kz');
  assert.equal(link.text, 'сайте');
});

test('подчёркивание внутри адреса ссылки не превращается в курсив', () => {
  const [block] = parseMailingText('[акция](https://x.kz/?utm_source=a_b_c)');
  assert.equal(block.nodes.length, 1);
  assert.equal(block.nodes[0].type, 'link');
  assert.equal(block.nodes[0].href, 'https://x.kz/?utm_source=a_b_c');
});

test('пустая строка сохраняется как отбивка абзаца', () => {
  const blocks = parseMailingText('первая\n\nвторая');
  assert.deepEqual(blocks.map((block) => block.type), ['line', 'gap', 'line']);
});

test('список собирается из подряд идущих строк с маркером', () => {
  const blocks = parseMailingText('Условия:\n• первое\n- второе\n* третье\nконец');
  assert.deepEqual(blocks.map((block) => block.type), ['line', 'list', 'line']);
  assert.equal(blocks[1].items.length, 3);
});

test('несуществующая разметка остаётся обычным текстом', () => {
  const [block] = parseMailingText('# Заголовок и <b>тег</b>');
  assert.deepEqual(block.nodes, ['# Заголовок и <b>тег</b>']);
});

/* Двуязычный составитель */

test('разделитель — три подчёркивания: только их приложение Pro рисует чертой', () => {
  // Десять «─» выглядят чертой в поле ввода, но водителю приезжают палочками.
  assert.match(BILINGUAL_SEPARATOR, /^\n\n___\n\n$/);
});

test('черта между языками — отдельный блок предпросмотра, а не курсив', () => {
  // `___` подпадает под правило курсива `_текст_`; если разбирать его инлайново,
  // предпросмотр покажет подчёркивания вместо линии.
  const blocks = parseMailingText(composeBilingual('Сәлем', 'Привет'));
  assert.deepEqual(blocks.map((b) => b.type), ['line', 'gap', 'rule', 'gap', 'line']);
});

test('склейка двух языков ставит разделитель только между заполненными частями', () => {
  assert.equal(composeBilingual('Сәлем', 'Привет'), `Сәлем${BILINGUAL_SEPARATOR}Привет`);
  assert.equal(composeBilingual('', 'Привет'), 'Привет');
  assert.equal(composeBilingual('Сәлем', '   '), 'Сәлем');
  assert.equal(composeBilingual('', ''), '');
});

test('склейка и обратное разбиение — обратимая пара', () => {
  // Путь «набрал два языка → выключил тумблер → включил обратно» не имеет права
  // терять казахскую часть: выключение склеивает, включение разбирает назад.
  const joined = composeBilingual('Сәлем, жүргізуші!', 'Привет, водитель!');
  const back = splitBilingual(joined);
  assert.equal(back.bilingual, true);
  assert.equal(back.kk, 'Сәлем, жүргізуші!');
  assert.equal(back.ru, 'Привет, водитель!');
});

test('обратное разбиение узнаёт и подчёркивания, и старые «─»', () => {
  // Рассылки, отправленные до смены разделителя, тоже открываются «Повторить».
  assert.deepEqual(splitBilingual('Сәлем\n\n___\n\nПривет'), { kk: 'Сәлем', ru: 'Привет', bilingual: true });
  assert.deepEqual(splitBilingual('Сәлем\n_____\nПривет').bilingual, true);
  assert.deepEqual(splitBilingual('Сәлем\n──────────\nПривет'), { kk: 'Сәлем', ru: 'Привет', bilingual: true });
  assert.deepEqual(splitBilingual('Просто текст'), { kk: '', ru: 'Просто текст', bilingual: false });
});

/* Панель форматирования */

test('жирный оборачивает выделение и выделяет его же', () => {
  const result = applyFormatting('привет мир', 7, 10, 'bold');
  assert.equal(result.value, 'привет **мир**');
  assert.equal(result.value.slice(result.selectionStart, result.selectionEnd), 'мир');
});

test('повторное нажатие снимает уже поставленную разметку', () => {
  const once = applyFormatting('привет мир', 7, 10, 'bold');
  const twice = applyFormatting(once.value, once.selectionStart, once.selectionEnd, 'bold');
  assert.equal(twice.value, 'привет мир');
});

test('без выделения вставляется заготовка и она выделена', () => {
  const result = applyFormatting('', 0, 0, 'italic');
  assert.equal(result.value, '_текст_');
  assert.equal(result.value.slice(result.selectionStart, result.selectionEnd), 'текст');
});

test('ссылка вставляет заготовки словами и выделяет адрес', () => {
  // «https://» выглядело наполовину готовым адресом, и его дописывали прямо к
  // нему, получая «https://https://…». В задании заготовки названы словами.
  const result = applyFormatting('жми сюда', 4, 8, 'link');
  assert.equal(result.value, 'жми [сюда](вставьте ссылку)');
  assert.equal(result.value.slice(result.selectionStart, result.selectionEnd), 'вставьте ссылку');
});

test('ссылка без выделения подставляет и подпись, и адрес', () => {
  const result = applyFormatting('', 0, 0, 'link');
  assert.equal(result.value, '[введите текст](вставьте ссылку)');
  assert.equal(result.value.slice(result.selectionStart, result.selectionEnd), 'вставьте ссылку');
});

test('список ставит маркеры на все затронутые строки и не дублирует их', () => {
  const first = applyFormatting('раз\nдва', 0, 7, 'list');
  assert.equal(first.value, '• раз\n• два');
  const second = applyFormatting(first.value, 0, first.value.length, 'list');
  assert.equal(second.value, '• раз\n• два');
});

test('снятие разметки в тексте без выделения ничего не ломает', () => {
  const result = applyFormatting('текст', 2, 2, 'bold');
  assert.equal(result.value, 'те**текст**кст');
});

/* Служебное */

test('текст без разметки годится для строки журнала', () => {
  assert.equal(
    stripMailingMarkup('**Акция**\n• _пункт_\n[тут](https://a.kz)\n___\nРусский'),
    'Акция пункт тут Русский',
  );
});

test('русские числительные', () => {
  assert.equal(plural(1, 'водитель', 'водителя', 'водителей'), 'водитель');
  assert.equal(plural(3, 'водитель', 'водителя', 'водителей'), 'водителя');
  assert.equal(plural(11, 'водитель', 'водителя', 'водителей'), 'водителей');
  assert.equal(plural(21, 'водитель', 'водителя', 'водителей'), 'водитель');
  assert.equal(plural(0, 'водитель', 'водителя', 'водителей'), 'водителей');
  assert.equal(recipientsLabel(1), '1 водитель');
  assert.equal(parksLabel(2), '2 диспетчерские');
});

test('форматирование чисел не рвёт число по строке', () => {
  assert.equal(formatCount(1144), '1 144');
  assert.equal(formatCount(null), '—');
});

/* Окно отзыва */

test('окно отзыва считается от времени отправки конкретной диспетчерской', () => {
  const sentAt = '2026-09-07T10:00:00.000Z';
  const now = Date.parse('2026-09-07T10:03:20.000Z');
  assert.equal(revokeSecondsLeft(sentAt, 300, now), 100);
  assert.equal(revokeSecondsLeft(sentAt, 300, Date.parse('2026-09-07T10:06:00.000Z')), 0);
  assert.equal(revokeSecondsLeft(null, 300, now), 0);
});

test('обратный отсчёт печатается минутами и секундами', () => {
  assert.equal(formatCountdown(100), '1:40');
  assert.equal(formatCountdown(9), '0:09');
  assert.equal(formatCountdown(-5), '0:00');
});

/* Описание отбора */

const REFS = {
  segments: [{ id: 'active', name: 'Активные', subsegments: [{ id: 'more_100', name: 'Сделали более 100 поездок' }] }],
  groups: [{ category: 'has_warnings', category_label: 'Предупреждения', key: 'has_violation_warning', label: 'Нарушения' }],
  professions: [{ id: 'taxi/driver', name: 'Водитель такси' }],
  statuses: [{ id: 'online', name: 'На линии' }],
  categories: [{ id: 'econom', name: 'Эконом' }],
  amenities: [{ id: 'wifi', name: 'WiFi' }],
};

test('отбор описывается словами, а не ключами', () => {
  const lines = describeFilters({
    segment: 'active',
    subsegments: ['more_100'],
    group: 'has_violation_warning',
    city_ids: ['Алматы'],
    profession_ids: ['taxi/driver'],
    contractor_statuses: ['online'],
    car_categories: ['econom'],
    car_amenities: ['wifi'],
  }, REFS);
  assert.deepEqual(lines, [
    'Сегмент — Активные: Сделали более 100 поездок',
    'Группа — Нарушения',
    'Города — Алматы',
    'Профессия — Водитель такси',
    'Статус на линии — На линии',
    'Категории — Эконом',
    'Услуги — WiFi',
  ]);
});

test('без справочника показываются сами значения, а не пустота', () => {
  assert.deepEqual(describeFilters({ segment: 'churn' }, {}), ['Сегмент — churn']);
});

test('справочники ещё не загружены (null) — описание не падает', () => {
  // Справочники в состоянии живут как null, пока не выбраны диспетчерские, а
  // окно подтверждения строит описание отбора всегда. Раньше на этом падал
  // весь раздел при первом открытии.
  assert.deepEqual(describeFilters({ segment: 'churn' }, null), ['Сегмент — churn']);
  assert.deepEqual(describeFilters(null, null), []);
  assert.deepEqual(describeAudience({}, null), ['Все водители выбранных диспетчерских']);
  assert.deepEqual(describeFilters({ profession_ids: ['taxi/driver'] }, null),
    ['Профессия — taxi/driver']);
});

test('пустой отбор читается как «все водители», а не как пустой список', () => {
  assert.deepEqual(describeAudience({}, REFS), ['Все водители выбранных диспетчерских']);
});

test('счётчик активных фильтров считает и сегмент, и группу, и списки', () => {
  assert.equal(countActiveFilters({}), 0);
  assert.equal(countActiveFilters({ segment: 'active', subsegments: ['more_100'] }), 1);
  assert.equal(countActiveFilters({ segment: 'active', group: 'x', car_categories: ['econom'] }), 3);
});

test('успешная отправка не красится, а частичная и провал — да', () => {
  assert.equal(statusMeta('sent').tone, 'slate');
  assert.equal(statusMeta('partial').tone, 'amber');
  assert.equal(statusMeta('failed').tone, 'red');
  assert.equal(targetStatusMeta('failed').tone, 'red');
  assert.equal(targetStatusMeta('sent').tone, 'slate');
});
