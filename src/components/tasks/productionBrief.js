/*
 * Бриф задачи «Продакшн» (задача #354): справочник вариантов и проверка формы.
 *
 * Тип «Продакшн» в форме задачи виден всегда, но исполнители у него — только
 * сотрудники отдела маркетинга (уточнение владельца 16.10.2026: «при выборе
 * данного типа в исполнителях будет только маркетинг»). Бриф — обязательные поля
 * ТЗ: наименование, адаптация, размеры, цветность (при «Своих тонах» — палитра
 * до 20 цветов); описание задания — обычное описание задачи, при этом типе тоже
 * обязательное.
 *
 * ТОТ ЖЕ справочник — в task_production/__init__.py: форма показывает варианты
 * отсюда, сервер принимает только свои. tests/test_task_production.py сверяет
 * блок между отметками catalog:start / catalog:end с серверным буквально — коды,
 * подписи, размеры. Правишь вариант — правь оба файла.
 *
 * Модуль без React: его целиком проверяет node --test (tests/task_production.test.mjs).
 */

export const PRODUCTION_TAG = 'production';
export const PRODUCTION_DEPARTMENT_CODE = 'marketing';

/* catalog:start */
export const PRODUCTION_ITEM_GROUPS = [
  {
    code: 'print', label: 'Полиграфия', items: [
      { code: 'leaflet', label: 'Лифлет' },
      { code: 'flyer', label: 'Флаер' },
      { code: 'brochure', label: 'Брошюра' },
      { code: 'catalog', label: 'Каталог' },
      { code: 'poster', label: 'Плакат' },
      { code: 'wobbler', label: 'Воблер' },
      { code: 'shelf_talker', label: 'Шелфтокер' },
      { code: 'roll_up', label: 'Выставочный ролл-ап' },
    ],
  },
  {
    code: 'digital', label: 'Digital', items: [
      { code: 'static_banner', label: 'Статичный баннер' },
      { code: 'animated_banner', label: 'Анимированный баннер' },
      { code: 'target_feed', label: 'Креатив для таргета — лента' },
      { code: 'target_stories', label: 'Креатив для таргета — Stories' },
      { code: 'target_reels', label: 'Креатив для таргета — Reels' },
      { code: 'profile_cover', label: 'Обложка профиля' },
      { code: 'teaser', label: 'Тизер' },
      { code: 'popup', label: 'Поп-ап' },
    ],
  },
  {
    code: 'video', label: 'Видео и анимация', items: [
      { code: 'promo_video', label: 'Промо-ролик' },
      { code: 'preroll', label: 'Короткий преролл' },
      { code: 'explainer', label: 'Эксплейнер' },
      { code: 'animation_2d', label: '2D-анимированный креатив' },
      { code: 'animation_3d', label: '3D-анимированный креатив' },
    ],
  },
  {
    code: 'brand', label: 'Бренд и упаковка', items: [
      { code: 'packaging', label: 'Дизайн упаковки' },
      { code: 'label', label: 'Этикетка' },
      { code: 'merch', label: 'Фирменный мерч' },
      { code: 'brand_guidelines', label: 'Гайдлайн по фирменному стилю' },
    ],
  },
];

export const PRODUCTION_CHANNELS = [
  { code: 'digital', label: 'Рекламные digital-сети и веб-сайты' },
  { code: 'social', label: 'Социальные сети и мессенджеры' },
  { code: 'outdoor', label: 'Наружная реклама' },
  { code: 'retail', label: 'Точки продаж и ритейл' },
  { code: 'print', label: 'Полиграфия и пресса' },
  { code: 'merch', label: 'Брендированная продукция' },
];

export const PRODUCTION_SIZE_PRESETS = [
  { code: '1x1', group: 'screen', label: '1:1', width: 1080, height: 1080, unit: 'px' },
  { code: '4x5', group: 'screen', label: '4:5', width: 1080, height: 1350, unit: 'px' },
  { code: '9x16', group: 'screen', label: '9:16', width: 1080, height: 1920, unit: 'px' },
  { code: '16x9', group: 'screen', label: '16:9', width: 1920, height: 1080, unit: 'px' },
  { code: 'a5', group: 'print', label: 'A5', width: 148, height: 210, unit: 'mm' },
  { code: 'a4', group: 'print', label: 'A4', width: 210, height: 297, unit: 'mm' },
  { code: 'a4_landscape', group: 'print', label: 'A4 альбомный', width: 297, height: 210, unit: 'mm' },
  { code: 'a3', group: 'print', label: 'A3', width: 297, height: 420, unit: 'mm' },
  { code: 'rollup_85x200', group: 'print', label: 'Ролл-ап', width: 850, height: 2000, unit: 'mm' },
];

export const PRODUCTION_SIZE_UNITS = [
  { code: 'px', label: 'px' },
  { code: 'mm', label: 'мм' },
];

export const PRODUCTION_COLOR_MODES = [
  { code: 'custom', label: 'Свои тона' },
  { code: 'brandbook', label: 'Брендбук' },
  { code: 'designer', label: 'На усмотрение дизайнера' },
];
/* catalog:end */

/* Готовые форматы делятся так же, как в Canva: экранные — пропорцией, печатные — листом. */
export const PRODUCTION_SIZE_GROUPS = [
  { code: 'screen', label: 'Экран' },
  { code: 'print', label: 'Печать' },
];

/* Потолки — те же, что у сервера (MAX_CUSTOM_SIZES, MAX_SIDE, MAX_COLORS). */
export const PRODUCTION_MAX_CUSTOM_SIZES = 6;
export const PRODUCTION_MAX_SIDE = 100000;
export const PRODUCTION_MAX_COLORS = 20;

/* Пояснения под «i» у полей. Текст нужен один раз — когда человек не понял
   поле, — поэтому он не стоит строкой под полем (решение владельца 27.08.2026).
   Напоминания у описания задания — дословно из ТЗ #354. */
export const PRODUCTION_HINTS = {
  item: 'Что именно нужно сделать. Список разбит на группы: полиграфия, digital, видео и анимация, бренд и упаковка.',
  channels: 'Где будет размещаться результат. От этого зависят форматы и требования к файлам. Можно отметить несколько.',
  sizes: 'В каких форматах нужен результат. Можно выбрать несколько. Нужного нет — «Свой размер»: для экрана в пикселях, для печати в миллиметрах.',
  color: {
    intro: 'В каких цветах делать.',
    options: [
      ['Свои тона', 'подберите цвета в палитре — до 20 оттенков'],
      ['Брендбук', 'цвета из фирменного стиля'],
      ['На усмотрение дизайнера', 'цвета подберёт дизайнер'],
    ],
  },
  colors: 'Цвета, которые нужно использовать. В «Сетке» — готовые оттенки, в «Спектре» тот же цвет доводят до своего, код из брендбука можно вписать. Первый цвет — главный; до 20 цветов.',
  description: {
    intro: 'Основное поле ТЗ. Чтобы не было правок по кругу, закройте эти пункты:',
    points: [
      'Цель и место размещения: канал, площадка, оффлайн-точка.',
      'Целевая аудитория: водители, курьеры, арендаторы, пассажиры.',
      'Текст на макете дословно (финальный и проверенный): заголовок, подзаголовок, CTA.',
      'Требования к дизайну: стиль, акценты, расположение элементов, обязательная информация.',
      'Обязательные элементы: QR, номер телефона, промокод, дисклеймер.',
      'Референсы и ссылки: брендбук, исходники, прошлые макеты.',
      'Ограничения: что менять нельзя (шрифтовая пара, логотип партнёра, фирменные цвета).',
    ],
  },
};

const ITEM_BY_CODE = new Map(
  PRODUCTION_ITEM_GROUPS.flatMap((group) => group.items.map((item) => [item.code, item])),
);
const CHANNEL_BY_CODE = new Map(PRODUCTION_CHANNELS.map((item) => [item.code, item]));
const SIZE_BY_CODE = new Map(PRODUCTION_SIZE_PRESETS.map((item) => [item.code, item]));
const UNIT_BY_CODE = new Map(PRODUCTION_SIZE_UNITS.map((item) => [item.code, item]));
const COLOR_BY_CODE = new Map(PRODUCTION_COLOR_MODES.map((item) => [item.code, item]));

/* Пункты для CustomSelect. Группа — у КАЖДОГО пункта: список рисует заголовок
   при смене группы, и только так он верен и в найденном поиском («ан» —
   баннеры под «Digital», 2D/3D-креативы под «Видео и анимацией»). */
export const PRODUCTION_ITEM_OPTIONS = PRODUCTION_ITEM_GROUPS.flatMap((group) => (
  group.items.map((item) => ({ value: item.code, label: item.label, groupLabel: group.label }))
));

let customSizeSeq = 0;
/* Ключ строки своего размера — только для React: строки удаляют из середины. */
export const newCustomSize = (unit = 'px') => {
  customSizeSeq += 1;
  return { key: `custom-${customSizeSeq}`, width: '', height: '', unit };
};

/* `color: null` — «ещё не выбрано», а не «по умолчанию брендбук»: молчаливого
   выбора у обязательного поля не бывает (решение владельца 06.10.2026). */
export const emptyProductionBrief = () => ({
  item: '',
  channels: [],
  sizes: [],
  customSizes: [],
  color: null,
  colors: [],
});

/* '#ffd400', 'FFD400', '#fd4' → '#FFD400'; не цвет → ''. Тот же разбор, что у
   сервера (task_production._clean_colors). */
const normalizeColor = (value) => {
  let text = String(value || '').trim().replace(/^#/, '').toUpperCase();
  if (/^[0-9A-F]{3}$/.test(text)) text = text.split('').map((char) => char + char).join('');
  return /^[0-9A-F]{6}$/.test(text) ? `#${text}` : '';
};

const cleanColors = (values) => {
  const result = [];
  (Array.isArray(values) ? values : []).forEach((value) => {
    const color = normalizeColor(value);
    if (color && !result.includes(color)) result.push(color);
  });
  return result.slice(0, PRODUCTION_MAX_COLORS);
};

/* Добавить цвет в палитру: без повторов и не больше двадцати. */
export const addPaletteColor = (colors, color) => cleanColors([...(colors || []), color]);

/* Заменить цвет на месте (правка образца); совпал с другим — повтор схлопнется. */
export const replacePaletteColor = (colors, index, color) => cleanColors(
  (colors || []).map((item, position) => (position === index ? color : item)),
);

const codesIn = (values, byCode) => (Array.isArray(values) ? values : [])
  .map((value) => String(value || '').trim())
  .filter((value) => byCode.has(value));

/* Бриф из ответа API → состояние формы. Неизвестный код (пункт убрали из
   справочника) форма не покажет — но и не потеряет молча: сервер на правке
   без нового брифа оставляет прежний. */
export const briefFromTask = (raw) => {
  const brief = emptyProductionBrief();
  if (!raw || typeof raw !== 'object') return brief;
  brief.item = ITEM_BY_CODE.has(raw.item) ? raw.item : '';
  brief.channels = codesIn(raw.channels, CHANNEL_BY_CODE);
  brief.sizes = codesIn(raw.sizes, SIZE_BY_CODE);
  brief.customSizes = (Array.isArray(raw.custom_sizes) ? raw.custom_sizes : [])
    .filter((size) => size && UNIT_BY_CODE.has(size.unit))
    .map((size) => ({ ...newCustomSize(size.unit), width: String(size.width ?? ''), height: String(size.height ?? '') }));
  brief.color = COLOR_BY_CODE.has(raw.color) ? raw.color : null;
  brief.colors = cleanColors(raw.colors);
  return brief;
};

const sideValue = (value) => {
  const text = String(value ?? '').trim().replace(',', '.');
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : NaN;
};

const sideIsValid = (number) => Number.isInteger(number) && number >= 1 && number <= PRODUCTION_MAX_SIDE;

/* Строка своего размера: пустая целиком — не размер вовсе (её можно не
   заполнять, если выбран готовый формат), заполненная наполовину — ошибка. */
const customSizeState = (size) => {
  const width = sideValue(size?.width);
  const height = sideValue(size?.height);
  if (width === null && height === null) return 'empty';
  if (width === null || height === null) return 'partial';
  return sideIsValid(width) && sideIsValid(height) ? 'ok' : 'invalid';
};

/* Состояние формы → тело запроса. Порядок — справочника, а не щелчков: один и
   тот же выбор обязан давать один и тот же бриф (так считает и сервер). */
export const briefToPayload = (brief) => {
  const value = brief || emptyProductionBrief();
  const pick = (codes, list) => list.map((item) => item.code).filter((code) => (codes || []).includes(code));
  const color = COLOR_BY_CODE.has(value.color) ? value.color : null;
  return {
    item: ITEM_BY_CODE.has(value.item) ? value.item : '',
    channels: pick(value.channels, PRODUCTION_CHANNELS),
    sizes: pick(value.sizes, PRODUCTION_SIZE_PRESETS),
    custom_sizes: (value.customSizes || [])
      .filter((size) => customSizeState(size) === 'ok')
      .map((size) => ({ width: sideValue(size.width), height: sideValue(size.height), unit: size.unit })),
    color,
    colors: color === 'custom' ? cleanColors(value.colors) : [],
  };
};

export const isProductionPerson = (person) => (
  String(person?.department_code || '') === PRODUCTION_DEPARTMENT_CODE
);

/* id сотрудников маркетинга среди тех, кому можно поручить задачу. */
export const productionPersonIds = (recipients) => new Set(
  (recipients || []).filter(isProductionPerson).map((person) => String(person.id)),
);

/* Все ли выбранные исполнители — из маркетинга (и выбран хоть кто-то). */
export const productionTeamFor = (assigneeIds, recipients) => {
  const ids = [...new Set((assigneeIds || []).map((id) => String(id)))];
  if (!ids.length) return false;
  const marketing = productionPersonIds(recipients);
  return ids.every((id) => marketing.has(id));
};

/* Один и тот же состав без учёта порядка: смена порядка показа — не смена состава. */
const sameIdSet = (left, right) => {
  const a = new Set((left || []).map(String));
  const b = new Set((right || []).map(String));
  return a.size === b.size && [...a].every((id) => b.has(id));
};

/* Можно ли оставить тип «Продакшн» при таком составе — то же правило, что у
   сервера: маркетолог нужен, когда продакшн появляется или меняется состав.
   Старый продакшн с прежним составом правится и тогда, когда маркетолог успел
   перейти в другой отдел. `initial` — { tag, assigneeIds } задачи до правки. */
export const productionAllowedFor = (assigneeIds, recipients, initial = null) => {
  const sameTeam = Boolean(initial)
    && initial.tag === PRODUCTION_TAG
    && sameIdSet(initial.assigneeIds, assigneeIds);
  return sameTeam || productionTeamFor(assigneeIds, recipients);
};

/* Чего не хватает, по порядку полей на экране. Ключ — якорь поля
   (data-issue-anchor): по нему форма подводит к полю и красит его. */
export const productionBriefIssues = (brief, description, { productionAllowed = true } = {}) => {
  const value = brief || emptyProductionBrief();
  const issues = [];
  if (!productionAllowed) {
    issues.push({
      key: 'tag',
      text: 'Продакшн ставится только сотрудникам отдела маркетинга — оставьте в исполнителях только их',
    });
  }
  if (!ITEM_BY_CODE.has(value.item)) issues.push({ key: 'item', text: 'Выберите, что нужно сделать' });
  if (!codesIn(value.channels, CHANNEL_BY_CODE).length) {
    issues.push({ key: 'channels', text: 'Отметьте, где будет размещаться' });
  }
  const states = (value.customSizes || []).map(customSizeState);
  if (states.includes('partial')) {
    issues.push({ key: 'sizes', text: 'У своего размера укажите и ширину, и высоту' });
  } else if (states.includes('invalid')) {
    issues.push({ key: 'sizes', text: `Ширина и высота — целые числа от 1 до ${PRODUCTION_MAX_SIDE.toLocaleString('ru-RU')}` });
  } else if (!codesIn(value.sizes, SIZE_BY_CODE).length && !states.includes('ok')) {
    issues.push({ key: 'sizes', text: 'Выберите формат или укажите свой размер' });
  }
  if (!COLOR_BY_CODE.has(value.color)) {
    issues.push({ key: 'color', text: 'Выберите цветность' });
  } else if (value.color === 'custom' && !cleanColors(value.colors).length) {
    issues.push({ key: 'colors', text: 'Добавьте хотя бы один цвет' });
  }
  if (!String(description || '').trim()) issues.push({ key: 'description', text: 'Опишите задание' });
  return issues;
};

/* «1080×1920 px» — без разделителя разрядов, как пишут дизайнеры. */
export const sizeDimensions = (width, height, unit) => (
  `${width}×${height} ${UNIT_BY_CODE.get(unit)?.label || unit || ''}`.trim()
);

/* Рамка плитки: длинная сторона — `box`, короткая — по пропорции, но не уже
   трети, иначе ролл-ап превратился бы в черту. */
export const frameSize = (width, height, box = 28) => {
  const w = Number(width) || 1;
  const h = Number(height) || 1;
  const scale = box / Math.max(w, h);
  const min = Math.round(box / 3);
  return {
    width: Math.max(min, Math.round(w * scale)),
    height: Math.max(min, Math.round(h * scale)),
  };
};

export const productionItemLabel = (brief) => ITEM_BY_CODE.get(brief?.item)?.label || brief?.item || '';

/* Бриф из ответа API → строки для карточки задачи. Неизвестный код показываем
   как есть: лучше код, чем молча потерянное «что заказывали». */
export const productionBriefRows = (raw) => {
  if (!raw || typeof raw !== 'object') return [];
  const rows = [];
  const item = productionItemLabel(raw);
  if (item) rows.push({ key: 'item', label: 'Наименование', text: item });
  const channels = (Array.isArray(raw.channels) ? raw.channels : [])
    .map((code) => CHANNEL_BY_CODE.get(code)?.label || code)
    .filter(Boolean);
  if (channels.length) rows.push({ key: 'channels', label: 'Адаптация', items: channels });
  const sizes = [
    ...(Array.isArray(raw.sizes) ? raw.sizes : []).map((code) => {
      const preset = SIZE_BY_CODE.get(code);
      return preset
        ? { key: code, label: preset.label, width: preset.width, height: preset.height, unit: preset.unit }
        : { key: code, label: code };
    }),
    ...(Array.isArray(raw.custom_sizes) ? raw.custom_sizes : [])
      .filter((size) => size && size.width && size.height)
      .map((size, index) => ({ key: `custom-${index}`, label: '', width: size.width, height: size.height, unit: size.unit })),
  ];
  if (sizes.length) rows.push({ key: 'sizes', label: 'Размеры', sizes });
  const color = COLOR_BY_CODE.get(raw.color);
  if (color || raw.color) {
    rows.push({
      key: 'color',
      label: 'Цветность',
      text: color?.label || raw.color,
      colors: raw.color === 'custom' ? cleanColors(raw.colors) : [],
    });
  }
  return rows;
};
