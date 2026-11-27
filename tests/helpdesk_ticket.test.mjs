/*
 * Заявка в IT из «Задач» (владелец 27.11.2026): правила формы без React —
 * src/components/tasks/helpdeskTicket.js. Что защищаем: доску узнаём по тому же
 * ключу, что сервер; ответ ИИ не ломает выбранную тему и заполненное; в задачу
 * уходит то, что прочтёт сисадмин, — без разметки и без потерь.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  HELPDESK_PROJECT_CODE,
  TICKET_TASK_PRIORITY,
  applyTicketAiResult,
  cabinetsForProfile,
  checksAnswer,
  clipboardFiles,
  emptyHelpdeskTicket,
  isHelpdeskProject,
  isTicketTextFresh,
  massContext,
  missingTicketFields,
  sanitizeTicketFields,
  seedTicketDescription,
  selectionFromValue,
  ticketAiRequest,
  ticketExtraLines,
  ticketIssues,
  ticketLogPayload,
  ticketNeedsCompose,
  ticketSignature,
  ticketTaskDescription,
  ticketTaskSubject,
  toggleTicketCheck,
  triedFieldKey,
  workplaceValue,
} from '../src/components/tasks/helpdeskTicket.js';
import { WORKPLACE_CABINETS } from '../src/components/technical/workplaceCabinets.js';

const CATALOG = {
  op: { label: 'Отдел продаж (ОП)', categories: [{ name: 'Телефония', items: ['Нет звука', 'Не идут звонки'] }] },
  szov: {
    label: 'СЗоВ',
    categories: [
      { name: 'Телефония и Oktell', items: ['Не работают наушники/гарнитура', 'Нет исходящих'] },
      { name: 'Сетевые и интернет-проблемы', items: ['Отсутствует доступ в интернет'] },
    ],
  },
};
const SUPPORT = WORKPLACE_CABINETS.filter((cabinet) => cabinet.group === 'support');
const SALES = WORKPLACE_CABINETS.filter((cabinet) => cabinet.group === 'sales');
const ticket = (patch = {}) => ({ ...emptyHelpdeskTicket(), profile: 'szov', ...patch });

test('доска заявок — по ключу, тому же, что у сервера', () => {
  assert.equal(isHelpdeskProject({ code: 'ITH', name: 'IT Helpdesk' }), true);
  assert.equal(isHelpdeskProject({ code: ' ith ' }), true);
  assert.equal(isHelpdeskProject({ code: 'BE', name: 'IT Helpdesk' }), false, 'по названию не узнаём — его правят');
  assert.equal(isHelpdeskProject(null), false);
  const python = readFileSync(new URL('../task_projects.py', import.meta.url), 'utf8');
  const match = python.match(/^HELPDESK_PROJECT_CODE\s*=\s*'([A-Z0-9]+)'/m);
  assert.ok(match, 'в task_projects.py есть HELPDESK_PROJECT_CODE');
  assert.equal(match[1], HELPDESK_PROJECT_CODE);
});

test('написанное до выбора доски становится описанием проблемы', () => {
  assert.equal(seedTicketDescription({ subject: ' Нет звука ', description: 'РМ 12, со вчера' }), 'Нет звука\nРМ 12, со вчера');
  assert.equal(seedTicketDescription({ subject: '', description: '' }), '');
});

test('поля ИИ: без ключа и повторов, тип из допустимых', () => {
  const fields = sanitizeTicketFields([
    { key: 'workplace', label: 'Где (№ РМ)', type: 'WORKPLACE', required: true, value: 'РМ 16' },
    { key: 'workplace', label: 'дубль' },
    { key: '', label: 'без ключа' },
    { key: 'bad key!', label: 'кривой ключ' },
    { key: 'when', label: 'Когда', type: 'calendar' },
  ]);
  assert.deepEqual(fields.map((field) => [field.key, field.type, field.required]), [
    ['workplace', 'workplace', true],
    ['when', 'text', false],
  ]);
  assert.equal(fields[0].value, 'РМ 16');
  assert.deepEqual(missingTicketFields(fields, { workplace: '  ' }).map((field) => field.key), ['workplace']);
  assert.deepEqual(missingTicketFields(fields, { workplace: 'РМ 3' }), []);
});

test('отмеченные советы — ответ на «что уже пробовали»', () => {
  const fields = [{ key: 'what', label: 'Что случилось' }, { key: 'tried_before', label: 'Что уже пробовали' }];
  assert.equal(triedFieldKey(fields), 'tried_before');
  assert.equal(triedFieldKey([{ key: 'what', label: 'Что' }]), 'tried', 'нет такого поля — свой ключ');
  const base = ticket({ fields, checks: ['Переподключить гарнитуру', 'Выбрать устройство вывода'], done: [] });
  const first = toggleTicketCheck(base, 1);
  assert.deepEqual(first.done, [1]);
  assert.equal(first.values.tried_before, 'Пробовали, не помогло: Выбрать устройство вывода');
  const both = toggleTicketCheck({ ...base, ...first }, 0);
  assert.equal(both.values.tried_before, 'Пробовали, не помогло: Переподключить гарнитуру; Выбрать устройство вывода');
  const none = toggleTicketCheck({ ...base, ...toggleTicketCheck({ ...base, ...first }, 1) }, 99);
  assert.equal(checksAnswer(base.checks, []), '');
  assert.ok(Array.isArray(none.done));
});

test('свой текст в «Уже пробовали» отметки советов не затирают', () => {
  const fields = [{ key: 'tried', label: 'Уже пробовали' }];
  const base = ticket({ fields, checks: ['Переподключить гарнитуру', 'Проверить mute'], values: { tried: 'гарнитуру меняли.' } });
  const ticked = toggleTicketCheck(base, 0);
  assert.equal(ticked.values.tried, 'гарнитуру меняли; Пробовали, не помогло: Переподключить гарнитуру');
  const both = toggleTicketCheck({ ...base, ...ticked }, 1);
  assert.equal(both.values.tried, 'гарнитуру меняли; Пробовали, не помогло: Переподключить гарнитуру; Проверить mute');
  const back = toggleTicketCheck({ ...base, ...toggleTicketCheck({ ...base, ...both }, 0) }, 1);
  assert.equal(back.values.tried, 'гарнитуру меняли', 'сняли все отметки — остался свой текст');
});

test('подпись поля от ИИ — с заглавной', () => {
  const [field] = sanitizeTicketFields([{ key: 'workplace', label: 'рабочее место / № РМ', type: 'workplace' }]);
  assert.equal(field.label, 'Рабочее место / № РМ');
});

test('РМ на схеме: строка ↔ выбор, по кабинетам', () => {
  const picked = new Set(['support:5', 'support:3']);
  assert.equal(workplaceValue(picked, SUPPORT), 'РМ 3, 5 (Тех поддержка)');
  assert.deepEqual([...selectionFromValue('РМ 3, 5 (Тех поддержка)', SUPPORT)].sort(), ['support:3', 'support:5']);
  // Номер, которого нет на схеме, не выдумывается.
  assert.deepEqual([...selectionFromValue('РМ 99', SUPPORT)], []);
  const sales = workplaceValue(new Set(['op17:70', 'op2:48', 'op2:50']), SALES);
  assert.equal(sales, 'РМ 70 (ОП · 17 этаж); РМ 48, 50 (ОП 2 · 18 этаж)');
  assert.deepEqual([...selectionFromValue(sales, SALES)].sort(), ['op17:70', 'op2:48', 'op2:50']);
  assert.equal(workplaceValue(new Set(), SUPPORT), '');
});

test('РМ 28 в «ОП 3» не загорается в тех-поддержке (номера 28–30 есть в обоих)', () => {
  const all = WORKPLACE_CABINETS;
  const value = workplaceValue(new Set(['op3:28', 'support:12']), all);
  assert.equal(value, 'РМ 12 (Тех поддержка); РМ 28 (ОП 3 (Честный) · 18 этаж)');
  assert.deepEqual([...selectionFromValue(value, all)].sort(), ['op3:28', 'support:12'],
    'цифры названия «ОП 3 … 18 этаж» номерами не считаются');
  // Без названия кабинета (так пишет ИИ: «РМ 28») — первый кабинет с таким номером.
  assert.deepEqual([...selectionFromValue('РМ 28', all)], ['support:28']);
});

test('схема — кабинеты отдела заявки', () => {
  assert.deepEqual(cabinetsForProfile('op', WORKPLACE_CABINETS).map((cabinet) => cabinet.id), ['op17', 'op2', 'op3']);
  assert.deepEqual(cabinetsForProfile('szov', WORKPLACE_CABINETS).map((cabinet) => cabinet.id), ['support']);
  assert.equal(cabinetsForProfile('', WORKPLACE_CABINETS).length, WORKPLACE_CABINETS.length);
});

test('дозаполненное после сборки не теряется', () => {
  const fields = [
    { key: 'workplace', label: 'Где (№ РМ)', type: 'workplace' },
    { key: 'when', label: 'Когда началось' },
  ];
  const composed = ticket({
    description: 'нет звука',
    fields,
    values: { workplace: '', when: 'сегодня' },
    title: 'Нет звука',
    text: '🔧 <b>Что:</b> нет звука\n🕒 <b>Когда:</b> сегодня',
  });
  const fresh = { ...composed, composed: true };
  fresh.composedSig = ticketSignature(fresh);
  assert.deepEqual(ticketExtraLines(fresh), [], 'свежий текст ИИ — ничего не дописываем');
  const stale = { ...fresh, values: { ...fresh.values, workplace: 'РМ 27 (Тех поддержка)' } };
  assert.deepEqual(ticketExtraLines(stale), ['Где (№ РМ): РМ 27 (Тех поддержка)'], '«сегодня» уже в тексте — не дублируем');
  assert.equal(
    ticketTaskDescription(stale),
    '🔧 Что: нет звука\n🕒 Когда: сегодня\n\nДетали:\nГде (№ РМ): РМ 27 (Тех поддержка)',
  );
  const tried = ticket({ description: 'нет звука', fields, values: { tried: 'Пробовали, не помогло: Переподключить' } });
  assert.deepEqual(ticketExtraLines(tried), ['Пробовали, не помогло: Переподключить']);
});

test('масштаб для срочности: доля затронутых РМ', () => {
  const fields = [{ key: 'workplace', type: 'workplace' }];
  assert.match(massContext(SUPPORT, fields, {}), /~30 РМ\. Конкретные РМ на схеме не выбраны/);
  const half = Array.from({ length: 15 }, (_, i) => i + 1).join(', ');
  const text = massContext(SUPPORT, fields, { workplace: `РМ ${half} (Тех поддержка)` });
  assert.match(text, /Затронуто 15 РМ \(~50% отдела\)/);
  assert.match(text, /Тех поддержка — 15\/30 РМ \(50%\)/);
  assert.equal(massContext([], fields, { workplace: 'РМ 1' }), '');
});

test('ИИ: тема из каталога, выбранную человеком не трогает без пометки', () => {
  const fromEmpty = applyTicketAiResult(ticket(), {
    status: 'draft', category: 'телефония и oktell ', subcategory: 'Не работают наушники/гарнитура', priority: 'high',
  }, CATALOG).patch;
  assert.equal(fromEmpty.category, 'Телефония и Oktell', 'строка — из каталога, а не из ответа');
  assert.equal(fromEmpty.subcategory, 'Не работают наушники/гарнитура');
  assert.equal(fromEmpty.priority, 'urgent');
  assert.equal(fromEmpty.aiPriority, 'high');

  const kept = applyTicketAiResult(ticket({ category: 'Сетевые и интернет-проблемы' }), {
    status: 'draft', category: 'Телефония и Oktell', subcategory: 'Нет исходящих',
  }, CATALOG).patch;
  assert.equal(kept.category, undefined, 'без category_adjusted тема человека остаётся');
  assert.equal(kept.subcategory, undefined, 'тип из чужой темы не подставляется');

  const adjusted = applyTicketAiResult(
    ticket({ category: 'Сетевые и интернет-проблемы', subcategory: 'Отсутствует доступ в интернет' }),
    { status: 'draft', category: 'Телефония и Oktell', subcategory: 'нет такого типа', category_adjusted: true, category_adjustment_note: 'Это про гарнитуру' },
    CATALOG,
  ).patch;
  assert.equal(adjusted.category, 'Телефония и Oktell');
  assert.equal(adjusted.subcategory, '', 'тип прежней темы новой не остаётся');
  assert.equal(adjusted.note, 'Это про гарнитуру');

  const unknown = applyTicketAiResult(ticket(), { status: 'draft', category: 'Выдуманная', priority: 'urgent!' }, CATALOG).patch;
  assert.equal(unknown.category, undefined);
  assert.equal(unknown.priority, undefined);
});

test('ИИ: заполненное не пропадает, отметки советов — по тексту', () => {
  const before = ticket({
    fields: [{ key: 'error_text', label: 'Текст ошибки', type: 'text' }, { key: 'workplace', label: 'Где', type: 'workplace' }],
    values: { error_text: 'DNS_PROBE', workplace: '' },
    checks: ['А', 'Б'],
    done: [1],
  });
  const { patch } = applyTicketAiResult(before, {
    status: 'draft',
    form: { fields: [{ key: 'workplace', label: 'Где (№ РМ)', type: 'workplace', required: true, value: 'РМ 16' }, { key: 'when', label: 'Когда' }] },
    checks: ['Б', 'В'],
  }, CATALOG);
  assert.deepEqual(patch.fields.map((field) => field.key), ['workplace', 'when', 'error_text']);
  assert.equal(patch.values.error_text, 'DNS_PROBE');
  assert.equal(patch.values.workplace, 'РМ 16', 'пустое поле берёт значение, распознанное ИИ');
  assert.equal(patch.values.when, '');
  assert.deepEqual(patch.checks, ['Б', 'В']);
  assert.deepEqual(patch.done, [0], 'отмеченный «Б» остался отмеченным на новом месте');
});

test('ИИ: собранный текст, устаревание и заголовок, поправленный руками', () => {
  const base = ticket({ description: 'нет звука', fields: [{ key: 'workplace', type: 'workplace', label: 'Где' }], values: { workplace: '' } });
  const ready = applyTicketAiResult(base, {
    status: 'ready', ticket: { title: 'Нет звука, РМ 27', markdown: '🔧 <b>Что:</b> нет звука' },
  }, CATALOG).patch;
  const composed = { ...base, ...ready };
  assert.equal(composed.title, 'Нет звука, РМ 27');
  assert.equal(isTicketTextFresh(composed), true);
  assert.equal(ticketNeedsCompose(composed), false);

  const edited = { ...composed, values: { workplace: 'РМ 27 (Тех поддержка)' } };
  assert.equal(isTicketTextFresh(edited), false, 'дозаполнили РМ — текст устарел');
  assert.equal(ticketNeedsCompose(edited), true);
  assert.equal(isTicketTextFresh({ ...composed, priority: 'critical', title: 'другой' }), true, 'срочность и заголовок текст не меняют');

  const manual = { ...composed, title: 'Мой заголовок', titleEdited: true };
  const again = applyTicketAiResult(manual, { status: 'ready', ticket: { title: 'ИИ-заголовок', markdown: 'x' } }, CATALOG).patch;
  assert.equal(again.title, undefined);

  const more = applyTicketAiResult(composed, { status: 'need_more_info' }, CATALOG).patch;
  assert.equal(more.composed, false);
  const draft = applyTicketAiResult(composed, { status: 'draft', ticket: { markdown: 'черновик' } }, CATALOG).patch;
  assert.equal(draft.text, undefined, 'черновик не затирает собранный текст');
  assert.equal(ticketNeedsCompose(ticket({ description: 'без ИИ' })), false, 'без ИИ собирать нечего');
});

test('задача из заявки: тема и текст без разметки', () => {
  const composed = ticket({
    category: 'Телефония и Oktell',
    subcategory: 'Не работают наушники/гарнитура',
    title: '  Не работают наушники,   РМ 27 ',
    text: '🔧 <b>Что:</b> не слышно &lt;оператора&gt;\n📍 <b>Где:</b> РМ 27 <code>Тех поддержка</code>',
    description: 'исходный текст человека',
  });
  assert.equal(ticketTaskSubject(composed), 'Не работают наушники, РМ 27');
  assert.equal(
    ticketTaskDescription(composed),
    'Тема: Телефония и Oktell → Не работают наушники/гарнитура\n\n🔧 Что: не слышно <оператора>\n📍 Где: РМ 27 Тех поддержка',
  );
  const plain = ticket({ description: 'у оператора на РМ 12 нет звука в Oktell\nперезагрузка не помогла' });
  assert.equal(ticketTaskDescription(plain), 'у оператора на РМ 12 нет звука в Oktell\nперезагрузка не помогла');
  assert.equal(ticketTaskSubject(plain), 'У оператора на РМ 12 нет звука в Oktell');
  const long = ticket({ description: 'очень '.repeat(40) + 'длинно' });
  const subject = ticketTaskSubject(long);
  assert.ok(subject.length <= 121, subject);
  assert.ok(subject.endsWith('…'));
  assert.ok(!/\s…$/.test(subject), 'по границе слова, без пробела перед многоточием');
});

test('чего не хватает перед отправкой', () => {
  assert.deepEqual(ticketIssues(ticket()).map((issue) => issue.key), ['ticket-description']);
  const fields = [{ key: 'workplace', label: 'Где', required: true }, { key: 'when', label: 'Когда' }];
  assert.deepEqual(
    ticketIssues(ticket({ description: 'нет звука', fields, values: {} })).map((issue) => issue.key),
    ['ticket-field-workplace'],
  );
  assert.deepEqual(ticketIssues(ticket({ description: 'нет звука', fields, values: { workplace: 'РМ 3' } })), []);
});

test('журнал заявок: срочность в шкале ИИ, только заполненные поля', () => {
  const sameAsAi = ticketLogPayload(ticket({ priority: 'normal', aiPriority: 'low', values: { a: ' x ', b: '' } }));
  assert.equal(sameAsAi.priority, 'low', 'не меняли — уровень ИИ');
  assert.deepEqual(sameAsAi.fields, { a: 'x' });
  assert.equal(ticketLogPayload(ticket({ priority: 'critical', aiPriority: 'medium' })).priority, 'critical');
  assert.equal(ticketLogPayload(ticket({ priority: 'urgent' })).priority, 'high');
  assert.equal(ticketLogPayload(ticket({ profile: 'hack' })).profile, '');
  assert.equal(ticketLogPayload(ticket({ values: { big: 'я'.repeat(5000) } })).fields.big.length, 2000);
  assert.deepEqual(Object.keys(TICKET_TASK_PRIORITY).sort(), ['critical', 'high', 'low', 'medium']);
});

test('запрос к ИИ несёт поля и масштаб', () => {
  const body = ticketAiRequest(ticket({ description: 'нет интернета', values: { workplace: 'РМ 1, 2 (Тех поддержка)' }, fields: [{ key: 'workplace', type: 'workplace' }] }), 'finalize', SUPPORT);
  assert.equal(body.mode, 'finalize');
  assert.equal(body.profile, 'szov');
  assert.deepEqual(body.fields, { workplace: 'РМ 1, 2 (Тех поддержка)' });
  assert.match(body.mass_context, /Затронуто 2 РМ/);
});

test('скриншот из буфера получает своё имя', () => {
  const png = new File(['x'], 'image.png', { type: 'image/png' });
  const doc = new File(['y'], 'отчёт.pdf', { type: 'application/pdf' });
  const clipboard = {
    items: [
      { kind: 'file', getAsFile: () => png },
      { kind: 'string', getAsFile: () => null },
      { kind: 'file', getAsFile: () => doc },
    ],
  };
  const files = clipboardFiles(clipboard, new Date(2026, 10, 27, 9, 5, 7));
  assert.deepEqual(files.map((file) => file.name), ['Снимок 09-05-07.png', 'отчёт.pdf']);
  assert.deepEqual(clipboardFiles({ items: [] }), []);
  assert.deepEqual(clipboardFiles(null), []);
});
