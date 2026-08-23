import test from 'node:test';
import assert from 'node:assert/strict';

import {
  MISSING_ATTACHMENT,
  MISSING_CHECKS,
  answerValue,
  carryOver,
  checksAreComplete,
  checksPayload,
  groupCatalog,
  isAnswered,
  localVerdict,
  referenceOptions,
  rowsOfGroup,
  missingTarget,
  stepIsComplete,
  stepIsVisible,
  toggleCheck,
  visibleSteps,
  afterCategory, afterChecks, describeSnapshot, entryCategories, entryIsComplete,
  needsSaparCheck, nextStop, openStop, pairRows, periodLabel, periodOptions,
  blockedLabel, previousStop, routeNote, saparGroup, saparKey,
} from '../src/components/crm/wizardRules.js';

/* Эти тесты написаны по следам реальной поломки: шаг с вложением никогда не
 * считался пройденным, и четыре тематики из шести нельзя было отправить вовсе.
 * Серверные тесты её поймать не могли — регламент был верный, ломался переход
 * по шагам мастера. */

const SCENARIO = {
  key: 'demo',
  attachment: 'image',
  steps: [
    { key: 'iin', kind: 'iin' },
    { key: 'relogin_done', kind: 'yesno' },
    { key: 'docs_after_relogin', kind: 'yesno', depends_on: ['relogin_done', 'yes'] },
    { key: 'provider_changed', kind: 'yesno_date' },
    { key: 'other_device', kind: 'yesno', optional: true },
    { key: 'screenshot', kind: 'attachment' },
  ],
  rules: [
    { when: ['error_persists', 'no'], outcome: 'close', message: 'Сервис заработал' },
    { when: ['docs_visible', 'no'], outcome: 'switch', message: 'Другая тематика', switch_to: 'other' },
    { when: ['cache_cleared', 'no'], outcome: 'blocked', message: 'Очистите кэш' },
  ],
};

const attachmentStep = SCENARIO.steps.find((s) => s.kind === 'attachment');

test('шаг вложения проходится выбранным файлом, а не записью в ответах', () => {
  // Ровно та ошибка, что доехала до прода: файл лежит в своём состоянии,
  // а готовность считалась только по answers.
  assert.equal(
    stepIsComplete(attachmentStep, { answers: {}, attachment: null, scenario: SCENARIO }),
    false,
  );
  assert.equal(
    stepIsComplete(attachmentStep, { answers: {}, attachment: { name: 'shot.png' }, scenario: SCENARIO }),
    true,
  );
});

test('каждая тематика с обязательным вложением имеет путь дальше', () => {
  // Инвариант, а не частный случай: если шаг вложения последний и он
  // непроходим, отправить обращение нельзя в принципе.
  const last = SCENARIO.steps[SCENARIO.steps.length - 1];
  assert.equal(last.kind, 'attachment');
  assert.equal(stepIsComplete(last, { attachment: { name: 'x' }, scenario: SCENARIO }), true);
});

test('без требования к вложению шаг проходится пустым', () => {
  const free = { ...SCENARIO, attachment: 'none' };
  assert.equal(stepIsComplete(attachmentStep, { attachment: null, scenario: free }), true);
});

test('«да, и уточните» без уточнения — ещё не ответ', () => {
  const step = SCENARIO.steps.find((s) => s.kind === 'yesno_date');
  assert.equal(stepIsComplete(step, { answers: { provider_changed: { value: 'yes' } } }), false);
  assert.equal(
    stepIsComplete(step, { answers: { provider_changed: { value: 'yes', detail: '2026-07-10' } } }),
    true,
  );
  // «Нет» уточнения не требует.
  assert.equal(stepIsComplete(step, { answers: { provider_changed: { value: 'no' } } }), true);
});

test('необязательный шаг не держит оператора', () => {
  const step = SCENARIO.steps.find((s) => s.optional);
  assert.equal(stepIsComplete(step, { answers: {} }), true);
});

test('пустая строка и пробелы ответом не считаются', () => {
  assert.equal(isAnswered(''), false);
  assert.equal(isAnswered('   '), false);
  assert.equal(isAnswered('123'), true);
  assert.equal(isAnswered({ value: '' }), false);
  assert.equal(isAnswered(undefined), false);
});

test('зависимый шаг не показывается, пока условие не выполнено', () => {
  const dependent = SCENARIO.steps.find((s) => s.depends_on);
  assert.equal(stepIsVisible(dependent, { relogin_done: 'no' }), false);
  assert.equal(stepIsVisible(dependent, { relogin_done: 'yes' }), true);
  assert.equal(visibleSteps(SCENARIO, { relogin_done: 'no' }).length, SCENARIO.steps.length - 1);
});

test('порядок правил — это приоритет: «сервис заработал» побеждает блокировку', () => {
  // В ТЗ «сервис заработал» стоит первым пунктом среди причин не отправлять,
  // и он обязан выигрывать у «не выполнил проверку».
  const hit = localVerdict(SCENARIO, { error_persists: 'no', cache_cleared: 'no' });
  assert.equal(hit.outcome, 'close');
});

test('перевод в другую тематику срабатывает по своему ответу', () => {
  const hit = localVerdict(SCENARIO, { docs_visible: 'no' });
  assert.equal(hit.outcome, 'switch');
  assert.equal(hit.switch_to, 'other');
});

test('без совпадений правило не выдумывается', () => {
  assert.equal(localVerdict(SCENARIO, { docs_visible: 'yes' }), null);
  assert.equal(localVerdict({}, {}), null);
});

test('«не приложено вложение» ведёт на шаг вложения, а не в пустоту', () => {
  // Раньше поиск шёл только по ключам шагов и давал -1: мастер молчал,
  // а кнопка оставалась серой без единого объяснения.
  const target = missingTarget(SCENARIO.steps, { [MISSING_ATTACHMENT]: 'Приложите скриншот' });
  assert.equal(target.phase, 'steps');
  assert.equal(SCENARIO.steps[target.stepIndex].kind, 'attachment');
  assert.equal(target.message, 'Приложите скриншот');
});

test('«не подтверждены проверки» возвращает на экран проверок', () => {
  const target = missingTarget(SCENARIO.steps, { [MISSING_CHECKS]: 'Подтвердите проверки' });
  assert.equal(target.phase, 'checks');
  assert.equal(target.message, 'Подтвердите проверки');
});

test('обычная незаполненность ведёт на свой вопрос', () => {
  const target = missingTarget(SCENARIO.steps, { relogin_done: 'Не заполнено' });
  assert.equal(SCENARIO.steps[target.stepIndex].key, 'relogin_done');
});

test('при переходе в другую тематику общие ответы переносятся, частные — нет', () => {
  const carried = carryOver({ iin: '123456789012', period: '2026-07', park: 'iTaxi',
                              city: 'Алматы', docs_visible: 'no', error_text: 'ошибка' });
  assert.deepEqual(Object.keys(carried).sort(), ['city', 'iin', 'park', 'period']);
  assert.equal(carried.iin, '123456789012');
  // «Где» от смены тематики не меняется — переспрашивать парк и город незачем.
  assert.equal(carried.park, 'iTaxi');
  assert.equal(carried.city, 'Алматы');
});

test('значение достаётся одинаково из строки и из объекта', () => {
  assert.equal(answerValue({ a: 'да' }, 'a'), 'да');
  assert.equal(answerValue({ a: { value: 'yes', detail: 'x' } }, 'a'), 'yes');
  assert.equal(answerValue({}, 'нет такого'), undefined);
});

/* ─── Картотека тематик по группам (задачи #183/#184) ─────────────────────── */

const CATALOG = [
  { key: 'sapar_docs_missing', queue_code: 'itaxi_sapar', queue_title: 'iTaxi Sapar', is_ready: true },
  { key: 'sapar_sign_error', queue_code: 'itaxi_sapar', queue_title: 'iTaxi Sapar', is_ready: true },
  { key: 'parcel_location', queue_code: 'parcels', queue_title: 'Посылки', is_ready: false },
];

test('тематики разложены по рабочим группам, а не одним списком', () => {
  const groups = groupCatalog(CATALOG);
  assert.deepEqual(groups.map((g) => g.code), ['itaxi_sapar', 'parcels']);
  assert.deepEqual(groups.map((g) => g.title), ['iTaxi Sapar', 'Посылки']);
  assert.deepEqual(groups[0].items.map((i) => i.key),
                   ['sapar_docs_missing', 'sapar_sign_error']);
});

test('порядок групп и тематик берётся с сервера, а не сортируется заново', () => {
  const reversed = [...CATALOG].reverse();
  const groups = groupCatalog(reversed);
  assert.deepEqual(groups.map((g) => g.code), ['parcels', 'itaxi_sapar']);
  assert.deepEqual(groups[1].items.map((i) => i.key),
                   ['sapar_sign_error', 'sapar_docs_missing']);
});

test('недоступная тематика остаётся в своей группе, а не пропадает', () => {
  const groups = groupCatalog(CATALOG);
  const parcels = groups.find((g) => g.code === 'parcels');
  assert.equal(parcels.items.length, 1);
  assert.equal(parcels.items[0].is_ready, false);
});

test('пустая картотека не роняет раскладку', () => {
  assert.deepEqual(groupCatalog(null), []);
  assert.deepEqual(groupCatalog([]), []);
});

/* ─── Адрес темы: тему из тематики можно увести в чужую группу ────────────── */

/* Тема остаётся в своей тематике (там её ищет оператор), а уходит в другую
 * группу. Раскладка обязана показывать обе вещи сразу — иначе оператор либо не
 * найдёт тему на привычном месте, либо не узнает, кого побеспокоил. */

const ROUTED_CATALOG = [
  { key: 'sapar_service_error', queue_code: 'itaxi_sapar', home_queue_title: 'iTaxi Sapar',
    queue_title: 'Техподдержка', routed: true, is_ready: true },
  { key: 'sapar_sign_error', queue_code: 'itaxi_sapar', home_queue_title: 'iTaxi Sapar',
    queue_title: 'iTaxi Sapar', routed: false, is_ready: true },
  { key: 'parcel_location', queue_code: 'parcels', home_queue_title: 'Посылки',
    queue_title: 'Посылки', routed: false, is_ready: true },
];

test('уведённая тема остаётся в своей тематике, а не переезжает к адресату', () => {
  const groups = groupCatalog(ROUTED_CATALOG);
  assert.deepEqual(groups.map((g) => g.code), ['itaxi_sapar', 'parcels']);
  assert.deepEqual(groups[0].items.map((i) => i.key),
                   ['sapar_service_error', 'sapar_sign_error']);
});

test('заголовок раздела — группа тематики, даже если первая тема уведена', () => {
  // Возьми раскладка queue_title, и раздел «iTaxi Sapar» назывался бы
  // «Техподдержка» — по адресу одной-единственной темы в нём.
  assert.equal(groupCatalog(ROUTED_CATALOG)[0].title, 'iTaxi Sapar');
});

test('без адресов раскладка работает как раньше', () => {
  // Каталог старого образца (без home_queue_title) приходит с сервера, пока
  // страница не перезагружена после выката.
  assert.equal(groupCatalog(CATALOG)[0].title, 'iTaxi Sapar');
});

test('у темы с выключенным адресом бейдж не спорит с подписью', () => {
  // «Нет группы» рядом со строкой «Уйдёт в группу «Техподдержка»» читалось бы
  // как противоречие: то ли группы нет, то ли она есть и названа.
  assert.equal(blockedLabel({ routed: true, queue_title: 'Техподдержка' }),
               'Группа недоступна');
  assert.equal(blockedLabel({ routed: false }), 'Нет группы');
  assert.equal(blockedLabel(null), 'Нет группы');
});

test('адрес подписывается только у уведённой темы', () => {
  assert.equal(routeNote(ROUTED_CATALOG[0]), 'Техподдержка');
  assert.equal(routeNote(ROUTED_CATALOG[1]), null);
  assert.equal(routeNote(null), null);
  // Маршрут есть, а очередь исчезла — подписывать нечего.
  assert.equal(routeNote({ routed: true, queue_title: null }), null);
});

/* ─── Вход в тематику: проверка по ИИН раньше категории (инструкция #230) ─── */

const ENTRY = {
  queue_code: 'itaxi_sapar',
  title: 'iTaxi Sapar',
  is_ready: true,
  steps: [
    { key: 'iin', kind: 'iin' },
    { key: 'period', kind: 'period' },
    { key: 'park', kind: 'taxi_park', half: true },
    { key: 'city', kind: 'city', half: true },
  ],
  categories: ['sapar_sign_error'],
  no_documents: 'sapar_docs_missing',
};

const ENTRY_CATALOG = [
  { key: 'sapar_docs_missing', queue_code: 'itaxi_sapar', queue_title: 'iTaxi Sapar',
    is_ready: true, entry_only: true },
  { key: 'sapar_sign_error', queue_code: 'itaxi_sapar', queue_title: 'iTaxi Sapar',
    is_ready: true, title: 'Ошибка подписания' },
  { key: 'parcel_location', queue_code: 'parcels', queue_title: 'Посылки', is_ready: true },
];

test('очередь со входом занимает в списке одну строку — саму тематику', () => {
  const groups = groupCatalog(ENTRY_CATALOG, [ENTRY]);
  const sapar = groups.find((g) => g.code === 'itaxi_sapar');
  assert.equal(sapar.items.length, 1);
  assert.equal(sapar.items[0].title, 'iTaxi Sapar');
  assert.equal(sapar.items[0].entry, ENTRY);
  // Очередь без входа показывается как раньше.
  assert.deepEqual(groups.find((g) => g.code === 'parcels').items.map((i) => i.key),
                   ['parcel_location']);
});

test('тематика, в которую ведёт только проверка, в списке выбора не стоит', () => {
  const groups = groupCatalog(ENTRY_CATALOG);   // без входов
  const sapar = groups.find((g) => g.code === 'itaxi_sapar');
  assert.deepEqual(sapar.items.map((i) => i.key), ['sapar_sign_error']);
});

test('категории входа берутся в порядке сервера', () => {
  assert.deepEqual(entryCategories(ENTRY, ENTRY_CATALOG).map((i) => i.key),
                   ['sapar_sign_error']);
});

test('пока Sapar молчит, «Документы не поступили» остаётся в списке', () => {
  assert.deepEqual(
    entryCategories(ENTRY, ENTRY_CATALOG, { withNoDocuments: true }).map((i) => i.key),
    ['sapar_sign_error', 'sapar_docs_missing'],
  );
});

test('Sapar не спрашиваем, пока ИИН и период не заполнены по-настоящему', () => {
  const filled = { iin: '123456789012', period: '2026-07', park: 'iTaxi', city: 'Алматы' };
  assert.equal(entryIsComplete(ENTRY, filled), true);
  assert.equal(entryIsComplete(ENTRY, { ...filled, iin: '12345' }), false);
  assert.equal(entryIsComplete(ENTRY, { ...filled, period: '2026-13' }), false);
  assert.equal(entryIsComplete(ENTRY, { ...filled, city: '' }), false);
  assert.equal(entryIsComplete(null, filled), false);
});

test('парк и город на экране входа встают в одну строку', () => {
  assert.deepEqual(pairRows(ENTRY.steps).map((row) => row.map((s) => s.key)),
                   [['iin'], ['period'], ['park', 'city']]);
});

const ENTRY_SCENARIO = {
  key: 'sapar_sign_error',
  attachment: 'none',
  checks: ['раз', 'два'],
  steps: [
    { key: 'iin', kind: 'iin', group: 'Водитель и период' },
    { key: 'docs_visible', kind: 'yesno', group: 'Что происходит' },
  ],
  rules: [],
};
const ENTRY_GROUPS = ['Водитель и период', 'Что происходит'];

test('после категории идёт чек-лист, а не вопросы (§2 инструкции)', () => {
  const state = { answers: { iin: '123456789012' }, checksReady: false };
  assert.deepEqual(afterCategory(ENTRY_SCENARIO, ENTRY_GROUPS, state), { phase: 'checks' });
});

test('заполненный на входе экран второй раз не показывается', () => {
  const state = { answers: { iin: '123456789012' }, checksReady: true };
  assert.deepEqual(afterCategory(ENTRY_SCENARIO, ENTRY_GROUPS, state),
                   { phase: 'form', groupIndex: 1 });
  assert.deepEqual(openStop(ENTRY_SCENARIO, ENTRY_GROUPS, state),
                   { phase: 'form', groupIndex: 1 });
});

test('заполнено всё — сразу к проверке ответов, а не в пустой экран', () => {
  const state = { answers: { iin: '123456789012', docs_visible: 'yes' }, checksReady: true };
  assert.deepEqual(openStop(ENTRY_SCENARIO, ENTRY_GROUPS, state), { phase: 'submit' });
});

test('чек-лист входа возвращает на первый незаполненный экран, а не на второй', () => {
  const resume = { phase: 'form', groupIndex: 1 };
  assert.deepEqual(afterChecks(ENTRY_GROUPS, resume), resume);
  // Без входа порядок прежний.
  assert.deepEqual(afterChecks(ENTRY_GROUPS), { phase: 'form', groupIndex: 1 });
});

/* ─── Третий ответ «Неизвестно» (задача #172) ─────────────────────────────── */

test('«неизвестно» — полноценный ответ, шаг считается пройденным', () => {
  const step = { key: 'provider_changed', kind: 'yesno_date', allow_unknown: true };
  assert.equal(stepIsComplete(step, { answers: { provider_changed: { value: 'unknown' } } }), true);
});

test('«да» по-прежнему требует уточнения, а «неизвестно» — нет', () => {
  const step = { key: 'provider_changed', kind: 'yesno_date', allow_unknown: true };
  assert.equal(stepIsComplete(step, { answers: { provider_changed: { value: 'yes', detail: '' } } }), false);
  assert.equal(stepIsComplete(step, { answers: { provider_changed: { value: 'yes', detail: '2026-07-01' } } }), true);
});

/* ─── «Таксопарк» и «Город» рядом одной строкой ───────────────────────────── */

const WHERE = {
  key: 'where-demo',
  attachment: 'none',
  groups: ['Водитель и период'],
  rules: [],
  steps: [
    { key: 'iin', kind: 'iin', group: 'Водитель и период' },
    { key: 'period', kind: 'period', group: 'Водитель и период' },
    { key: 'park', kind: 'taxi_park', half: true, group: 'Водитель и период' },
    { key: 'city', kind: 'city', half: true, group: 'Водитель и период' },
  ],
};

test('парк и город встают в одну строку, остальное — по одному', () => {
  const rows = rowsOfGroup(WHERE, 'Водитель и период', {});
  assert.deepEqual(rows.map((row) => row.map((s) => s.key)),
                   [['iin'], ['period'], ['park', 'city']]);
});

test('одинокий половинный вопрос занимает всю ширину', () => {
  const parcels = {
    ...WHERE,
    steps: [
      { key: 'iin', kind: 'iin', group: 'Водитель и период' },
      { key: 'city', kind: 'city', half: true, group: 'Водитель и период' },
    ],
  };
  const rows = rowsOfGroup(parcels, 'Водитель и период', {});
  assert.deepEqual(rows.map((row) => row.length), [1, 1]);
});

test('вложение в пару не берётся никогда', () => {
  const withFile = {
    ...WHERE,
    groups: ['Вложение'],
    steps: [
      { key: 'park', kind: 'taxi_park', half: true, group: 'Вложение' },
      { key: 'screenshot', kind: 'attachment', half: true, group: 'Вложение' },
    ],
  };
  const rows = rowsOfGroup(withFile, 'Вложение', {});
  assert.deepEqual(rows.map((row) => row.map((s) => s.key)), [['park'], ['screenshot']]);
});

test('раскладка не меняет состав вопросов экрана', () => {
  const flat = rowsOfGroup(WHERE, 'Водитель и период', {}).flat().map((s) => s.key);
  assert.deepEqual(flat, ['iin', 'period', 'park', 'city']);
});

test('скрытый зависимый вопрос в строки не попадает', () => {
  const conditional = {
    ...WHERE,
    steps: [
      { key: 'park', kind: 'taxi_park', half: true, group: 'Водитель и период' },
      { key: 'city', kind: 'city', half: true, group: 'Водитель и период',
        depends_on: ['park', 'iTaxi'] },
    ],
  };
  assert.deepEqual(rowsOfGroup(conditional, 'Водитель и период', {}).map((r) => r.length), [1]);
  assert.deepEqual(
    rowsOfGroup(conditional, 'Водитель и период', { park: 'iTaxi' }).map((r) => r.length), [2]);
});

/* ─── Варианты из справочников ────────────────────────────────────────────── */

test('таксопарки приходят с сервера, города берутся из справочника рядом', () => {
  const parks = referenceOptions({ kind: 'taxi_park' }, { taxiParks: ['iTaxi', 'Qazaq'] });
  assert.deepEqual(parks, [{ value: 'iTaxi', label: 'iTaxi' },
                           { value: 'Qazaq', label: 'Qazaq' }]);

  const cities = referenceOptions({ kind: 'city' }, {});
  assert.ok(cities.length > 50, `городов подхватилось всего ${cities.length}`);
  // groupLabel рисует заголовок области — без него длинный список не читается.
  assert.deepEqual(cities.find((o) => o.value === 'Алматы'),
                   { value: 'Алматы', label: 'Алматы',
                     groupLabel: 'Города республиканского значения' });
  assert.ok(cities.some((o) => o.groupLabel === 'Мангистауская область'));
});

test('обычный вопрос справочником не подменяется', () => {
  assert.equal(referenceOptions({ kind: 'text' }, { taxiParks: ['iTaxi'] }), null);
  assert.equal(referenceOptions({ kind: 'yesno' }, {}), null);
  assert.equal(referenceOptions(null, {}), null);
});

test('пустой справочник парков не роняет мастер', () => {
  assert.deepEqual(referenceOptions({ kind: 'taxi_park' }, {}), []);
  assert.deepEqual(referenceOptions({ kind: 'taxi_park' }, { taxiParks: [] }), []);
});

/* ─── Чек-лист перед обращением (ТЗ термокоробов, задача #189) ─────────────── */

const SIMPLE = { checks: ['раз', 'два', 'три'] };
const EACH = { checks: ['раз', 'два', 'три'], checks_each: true };

test('обычная тематика подтверждает проверки одной галочкой', () => {
  assert.equal(checksAreComplete(SIMPLE, { confirmedAll: false }), false);
  assert.equal(checksAreComplete(SIMPLE, { confirmedAll: true }), true);
  // Отмеченные пункты в обычном режиме ничего не решают.
  assert.equal(checksAreComplete(SIMPLE, { confirmedItems: [0, 1, 2] }), false);
});

test('тематика с checks_each требует отметить каждый пункт', () => {
  assert.equal(checksAreComplete(EACH, { confirmedItems: [] }), false);
  assert.equal(checksAreComplete(EACH, { confirmedItems: [0, 1] }), false);
  assert.equal(checksAreComplete(EACH, { confirmedItems: [0, 1, 2] }), true);
  // Общая галочка не должна подменять пункты — иначе смысл режима теряется.
  assert.equal(checksAreComplete(EACH, { confirmedAll: true }), false);
});

test('лишние и повторяющиеся номера не проходят чек-лист за пункт', () => {
  assert.equal(checksAreComplete(EACH, { confirmedItems: [0, 0, 1, 1, 9] }), false);
  assert.equal(checksAreComplete(EACH, { confirmedItems: [0, 1, 2, 2, 7] }), true);
});

test('тематика без чек-листа проходит его пустой', () => {
  assert.equal(checksAreComplete({ checks: [] }, {}), true);
  assert.equal(checksAreComplete({}, {}), true);
  assert.equal(checksAreComplete(null, {}), true);
});

test('серверу уходит то, что соответствует режиму тематики', () => {
  assert.deepEqual(checksPayload(SIMPLE, { confirmedAll: true, confirmedItems: [0] }),
                   { checks_confirmed: true, checks_done: [] });
  assert.deepEqual(checksPayload(EACH, { confirmedAll: true, confirmedItems: [2, 0, 0, 1] }),
                   { checks_confirmed: false, checks_done: [0, 1, 2] });
});

test('пункт отмечается и снимается, массив не меняется на месте', () => {
  const before = [0, 2];
  const after = toggleCheck(before, 1);
  assert.deepEqual(after, [0, 1, 2]);
  assert.deepEqual(before, [0, 2], 'исходный массив тронут');
  assert.deepEqual(toggleCheck(after, 2), [0, 1]);
  assert.deepEqual(toggleCheck(undefined, 3), [3]);
});

/* ─── Предпроверка по Sapar ───────────────────────────────────────────────── */

const SAPAR_SCENARIO = {
  key: 'sapar_docs_missing',
  sapar: true,
  steps: [
    { key: 'iin', kind: 'iin', group: 'Водитель и период' },
    { key: 'period', kind: 'period', group: 'Водитель и период' },
    { key: 'trips_in_park', kind: 'yesno', group: 'Что происходит' },
  ],
};

const FILLED = { iin: '060606060606', period: '2026-02' };

test('Sapar спрашивают на экране, где стоит ИИН', () => {
  assert.equal(saparGroup(SAPAR_SCENARIO), 'Водитель и период');
  assert.equal(saparGroup({ ...SAPAR_SCENARIO, sapar: false }), null);
});

test('спрашиваем только с заполненными ИИН и периодом', () => {
  assert.equal(needsSaparCheck(SAPAR_SCENARIO, 'Водитель и период', FILLED, ''), true);
  assert.equal(needsSaparCheck(SAPAR_SCENARIO, 'Водитель и период',
                               { iin: '123', period: '2026-02' }, ''), false);
  assert.equal(needsSaparCheck(SAPAR_SCENARIO, 'Водитель и период',
                               { iin: '060606060606' }, ''), false);
  // Кириллические цифры и прочая экзотика ИИН не образуют.
  assert.equal(needsSaparCheck(SAPAR_SCENARIO, 'Водитель и период',
                               { ...FILLED, iin: '٠٦٠٦٠٦٠٦٠٦٠٦' }, ''), false);
});

test('на другом экране и у тематики без предпроверки не спрашиваем', () => {
  assert.equal(needsSaparCheck(SAPAR_SCENARIO, 'Что происходит', FILLED, ''), false);
  assert.equal(needsSaparCheck({ ...SAPAR_SCENARIO, sapar: false },
                               'Водитель и период', FILLED, ''), false);
});

test('ту же пару «ИИН + период» второй раз не спрашиваем', () => {
  const key = saparKey(FILLED);
  assert.equal(key, '060606060606|2026-02');
  assert.equal(needsSaparCheck(SAPAR_SCENARIO, 'Водитель и период', FILLED, key), false);
  // Сменили период — это уже другой вопрос.
  assert.equal(needsSaparCheck(SAPAR_SCENARIO, 'Водитель и период',
                               { ...FILLED, period: '2026-03' }, key), true);
});

test('снимок описывается по смыслу, а не одним серым текстом', () => {
  const found = describeSnapshot({
    available: true, month_ready: true, driver_name: 'Кенжебаев Б.',
    documents: [{ status_label: 'подписан', signed: true },
                { status_label: 'подписан', signed: true }],
  });
  assert.equal(found.tone, 'green');
  // Заголовок — словами инструкции #230, теми же и на экране проверки.
  assert.equal(found.title, 'Есть документы за отчётный период');
  // Повторяющийся статус не дублируется, количество — рядом со статусом.
  assert.deepEqual(found.lines, ['2 шт. · подписан', 'Кенжебаев Б.']);

  const one = describeSnapshot({
    available: true, month_ready: true,
    documents: [{ status_label: 'ждёт подписи водителя', signed: false }],
  });
  // Один документ — без «1 шт.»: количество там ничего не добавляет.
  assert.deepEqual(one.lines, ['ждёт подписи водителя']);

  const none = describeSnapshot({ available: true, month_ready: false, documents: [] });
  assert.equal(none.tone, 'amber');
  assert.equal(none.title, 'Нет документов. Документы не поступили');
  assert.match(none.lines[0], /по парку/);

  const silent = describeSnapshot({ available: false });
  assert.equal(silent.tone, 'muted');
  // Молчание сервиса НЕ выдаётся за «документов нет».
  assert.doesNotMatch(silent.title, /нет/);
});

/* ─── Порядок экранов: чек-лист после первого экрана ──────────────────────── */

const WITH_CHECKS = { key: 'sapar_docs_missing', checks: ['раз', 'два'] };
const NO_CHECKS = { key: 'free', checks: [] };
const THREE = ['Водитель и период', 'Что происходит', 'Вложение'];

test('чек-лист показывается ПОСЛЕ первого экрана, а не до него', () => {
  // Просьба владельца: проверка по ИИН должна идти раньше чек-листа, иначе
  // оператор проходит его для обращения, которое Sapar закрывает сам.
  assert.deepEqual(nextStop(WITH_CHECKS, THREE, 0, { checksReady: false }),
                   { phase: 'checks' });
  assert.deepEqual(afterChecks(THREE), { phase: 'form', groupIndex: 1 });
});

test('пройденный чек-лист второй раз не показывается', () => {
  assert.deepEqual(nextStop(WITH_CHECKS, THREE, 0, { checksReady: true }),
                   { phase: 'form', groupIndex: 1 });
});

test('тематика без чек-листа идёт по экранам подряд', () => {
  assert.deepEqual(nextStop(NO_CHECKS, THREE, 0, {}), { phase: 'form', groupIndex: 1 });
  assert.deepEqual(nextStop(NO_CHECKS, THREE, 2, {}), { phase: 'submit' });
});

test('после последнего экрана спрашиваем сервер', () => {
  assert.deepEqual(nextStop(WITH_CHECKS, THREE, 2, { checksReady: true }),
                   { phase: 'submit' });
  // Экранов после чек-листа может и не остаться — тогда сразу к проверке,
  // а не в пустой шаг.
  assert.deepEqual(afterChecks(['Водитель и период']), { phase: 'submit' });
});

test('«Назад» возвращает тем же путём, каким пришли', () => {
  assert.deepEqual(previousStop(WITH_CHECKS, THREE, 2), { phase: 'form', groupIndex: 1 });
  // Со второго экрана назад — на чек-лист, он теперь между первым и вторым.
  assert.deepEqual(previousStop(WITH_CHECKS, THREE, 1), { phase: 'checks' });
  assert.deepEqual(previousStop(NO_CHECKS, THREE, 1), { phase: 'form', groupIndex: 0 });
  // С первого экрана — к выбору тематики.
  assert.deepEqual(previousStop(WITH_CHECKS, THREE, 0), { phase: 'pick' });
});

/* ─── Отчётный период одним списком ───────────────────────────────────────── */

test('список периодов начинается с ПРОШЛОГО месяца и идёт вглубь', () => {
  const options = periodOptions(new Date(2026, 7, 21));   // 21 августа 2026
  assert.deepEqual(options.slice(0, 3).map((o) => o.label),
                   ['Июль 2026', 'Июнь 2026', 'Май 2026']);
  assert.equal(options[0].value, '2026-07');
});

test('текущего месяца в списке нет', () => {
  // Месяц не закончился — отчётным периодом он быть не может, и именно на нём
  // операторы путали «за какой месяц» с «когда жду».
  const options = periodOptions(new Date(2026, 7, 21));
  assert.equal(options.some((o) => o.value === '2026-08'), false);
});

test('через новый год список переходит сам', () => {
  const options = periodOptions(new Date(2027, 0, 5));    // 5 января 2027
  assert.deepEqual(options.slice(0, 2).map((o) => o.label),
                   ['Декабрь 2026', 'Ноябрь 2026']);
  assert.equal(options[0].value, '2026-12');
});

test('значения совпадают с форматом, который ждёт сервер', () => {
  for (const option of periodOptions(new Date(2026, 0, 15), 14)) {
    assert.match(option.value, /^\d{4}-(0[1-9]|1[0-2])$/, option.value);
  }
});

test('глубина списка — два года', () => {
  assert.equal(periodOptions(new Date(2026, 7, 21)).length, 24);
  assert.equal(periodOptions(new Date(2026, 7, 21), 3).length, 3);
});

test('период читается по-человечески, даже если он не из списка', () => {
  assert.equal(periodLabel('2025-03'), 'Март 2025');
  assert.equal(periodLabel('2026-12'), 'Декабрь 2026');
  assert.equal(periodLabel('чепуха'), '');
  assert.equal(periodLabel('2026-13'), '');
});
