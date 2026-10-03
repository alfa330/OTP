/*
 * «Сводка» и «Звонки»/«Чаты» по дням — настоящие компоненты через
 * react-dom/server, как ai_qa_panels_render.test.mjs.
 *
 * Зачем: ошибка в теле компонента (переменная из соседнего, опечатка в имени
 * поля ответа) проходит сборку и падает только при отрисовке — у человека,
 * открывшего вкладку. Эффекты при серверной отрисовке не выполняются, тело —
 * выполняется.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');

async function load(relative, exportName = 'default') {
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, `${relative.replace(/[\\/]/g, '_')}.mjs`);
  buildSync({
    entryPoints: [fileURLToPath(new URL(`../src/components/${relative}`, import.meta.url))],
    bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
    packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
    logLevel: 'silent',
  });
  const mod = await import(`file://${outfile.replace(/\\/g, '/')}`);
  return exportName === '*' ? mod : mod[exportName];
}

const DigestView = await load('call_qa/DigestView.jsx');
const EvaluationsList = await load('call_qa/EvaluationsList.jsx');
const ChatQueue = await load('call_qa/ChatQueue.jsx');
const markup = await load('call_qa/AiMarkup.jsx', '*');
const digestRules = await load('call_qa/useDigest.js', '*');

const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));
const noop = () => {};

const baseDigest = (extra = {}) => ({
  days: [
    { day: '2026-10-01', evaluated: 124, critical: 2, operators: 13, reviewed: 6, ai_avg: 88.6,
      human_avg: 93.8, headline: 'Две критические ошибки и провал возражений в «Потоке»', running: false },
    { day: '2026-09-30', evaluated: 120, critical: 0, operators: 15, reviewed: 0, ai_avg: 82,
      human_avg: null, headline: '', running: false },
    { day: '2026-09-29', evaluated: 118, critical: 1, operators: 12, reviewed: 0, ai_avg: 80,
      human_avg: null, headline: '', running: true },
  ],
  daysError: false, openDay: null, view: null, section: null, setSection: noop, pane: 'summary',
  setPane: noop, chat: { day: null, messages: [], loading: false, error: '' }, asking: false,
  draft: '', setDraft: noop, draftFocus: 0, generating: false, open: noop, close: noop,
  reset: noop, reloadDays: noop, reloadView: noop, generate: noop, ask: noop, clearChat: noop,
  suggest: noop, ...extra,
});

test('список дней сводки: заголовок ИИ, «не составлена» и «пишет»', () => {
  const html = render(DigestView, { digest: baseDigest(), onOpenRef: noop });
  assert.ok(html.includes('Две критические ошибки и провал возражений'));
  assert.ok(html.includes('Сводка не составлена'));
  assert.ok(html.includes('ИИ пишет сводку'));
  assert.ok(html.includes('data-qa-day-tile="2026-10-01"'));
  assert.ok(html.includes('2 критических'));
});

const section = (key, direction, htmlText, extra = {}) => ({
  key, direction, family: 'calls', headline: direction, html: htmlText, error: null,
  stats: { evaluated: 30, ai_avg: 86, critical: 0, reviewed: 2, operators: 3, below_60: 0, human_avg: null },
  usual: { ai_avg: 75 }, ...extra,
});

test('экран дня: разделы, цифры и подсказки чата; HTML модели без санитайзера не рисуется', () => {
  const ref = '<span data-qa-ref="imported_call:6444" data-qa-kind="call" title="Звонок · Касымов Ержан · 13:41">13:41</span>';
  const digest = baseDigest({
    openDay: '2026-10-01',
    view: { day: '2026-10-01', loading: false, error: '', data: {
      day: '2026-10-01', status: 'ready', running: false, evaluated: 124, stale: true, new_evaluations: 4,
      generated_at: '2026-10-02T03:41:00+00:00', can_force: true, can_generate: true,
      headline: 'Главное дня', overview_html: `<div data-wiki-block="lead"><p>Обзор ${ref}</p></div>`,
      stats: { evaluated: 124, ai_avg: 89, critical: 2, reviewed: 6, operators: 13, below_60: 2 },
      usual: { ai_avg: 84 },
      sections: [section('74', 'Поток', `<p>Персонализация ${ref}</p>`),
                 section('71', 'Верификатор', '', { family: 'chats', missing: true })],
    } },
  });
  const html = render(DigestView, { digest, onOpenRef: noop });
  for (const piece of ['Главное', 'Поток', 'Верификатор', 'Средний балл ИИ', 'обычно 84',
                       'Новых оценок: 4', 'Спросить ИИ', 'Кому из сотрудников нужна помощь',
                       'qa-digest-day-title']) {
    assert.ok(html.includes(piece), `нет «${piece}»`);
  }
  // Без DOM у DOMPurify нет sanitize, и разметка ИИ не рисуется вовсе — чужой
  // HTML без санитайзера на экран не попадает (кнопки проверяет браузер).
  assert.ok(!html.includes('data-qa-ref'), 'HTML модели ушёл на экран мимо санитайзера');
});

test('экран дня без сводки предлагает её составить, с ответами — рисует ленту', () => {
  const empty = baseDigest({
    openDay: '2026-09-30',
    view: { day: '2026-09-30', loading: false, error: '', data: {
      day: '2026-09-30', status: 'none', running: false, evaluated: 120, sections: [],
      stats: { evaluated: 120, ai_avg: 82, critical: 0, reviewed: 0 }, overview_html: null } },
    chat: { day: '2026-09-30', loading: false, error: '', messages: [
      { id: 1, role: 'user', body: 'Кто ошибся?', created_at: '2026-10-02T04:00:00+00:00' },
      { id: 2, role: 'assistant', body: '<p>Ответ</p>', created_at: '2026-10-02T04:00:10+00:00' },
    ] },
  });
  const html = render(DigestView, { digest: empty, onOpenRef: noop });
  assert.ok(html.includes('Сводки за этот день ещё нет'));
  assert.ok(html.includes('Составить сводку'));
  assert.ok(html.includes('Кто ошибся?'));
  // Обе реплики в ленте (текст ответа без DOM не рисуется — см. тест выше).
  assert.ok(html.includes('data-qa-message="1"') && html.includes('data-qa-message="2"'));
  assert.ok(!html.includes('Кому из сотрудников нужна помощь'), 'подсказки при живой переписке — шум');
});

const listState = (extra = {}) => ({
  days: [
    { day: '2026-10-01', evaluated: 94, reviewed: 2, corrected: 1, critical: 2, ai_avg: 89, human_avg: null,
      human_n: 0, operators: 13, directions: [{ name: 'Основа ОП', n: 34 }, { name: 'Поток', n: 30 }] },
  ],
  error: false, dayItems: {}, openDay: null, open: noop, close: noop, loadDay: noop, refresh: noop,
  reset: noop, reload: noop, ...extra,
});

test('«Звонки» по дням: строка дня и экран дня со строками разговоров', () => {
  const overview = render(EvaluationsList, { list: listState(), apiBaseUrl: 'http://stub', subject: 'calls',
                                             department: 'op', canPull: true, onFind: noop });
  for (const piece of ['94', 'звонка', '2 критических', 'Основа ОП, Поток', 'Из АТС', 'Найти звонок']) {
    assert.ok(overview.includes(piece), `нет «${piece}»`);
  }
  const day = render(EvaluationsList, {
    list: listState({ openDay: '2026-10-01', dayItems: { '2026-10-01': { items: [
      { id: 6384, subject: 'imported_call', operator: 'Тимурова Айгуль', datetime: '01.10 10:38',
        ai_score: 0, human_score: null, reasons: ['critical'], direction: 'Яндекс Регистрация' },
    ], total: 1, end: 1, loading: false, error: false } } }),
    apiBaseUrl: 'http://stub', subject: 'calls', department: 'op', onOpenDigest: noop,
  });
  for (const piece of ['Все дни', 'Оценено ИИ', 'Звонки дня', 'Тимурова Айгуль', 'Критическое', 'Сводка дня']) {
    assert.ok(day.includes(piece), `нет «${piece}»`);
  }
  // Каждое число дня — один раз: подписи под средними не повторяют «оценено» и
  // «проверено».
  assert.ok(!day.includes('94 звонка'), 'под средним баллом ИИ повтор «оценено»');
  assert.ok(!day.includes('проверенных'), 'под «Поправили ИИ» повтор «проверено»');
});

test('«Без даты» — без кнопки «Сводка дня»: сводки без даты не бывает', () => {
  const html = render(EvaluationsList, {
    list: listState({ days: [{ day: 'none', evaluated: 3, reviewed: 0, corrected: 0, critical: 0, ai_avg: 70,
                               human_avg: null, human_n: 0, operators: 1, directions: [] }],
                      openDay: 'none', dayItems: { none: { items: [], total: 0, end: 0, loading: false, error: false } } }),
    apiBaseUrl: 'http://stub', subject: 'calls', department: 'op', onOpenDigest: noop,
  });
  assert.ok(html.includes('Все дни'));
  assert.ok(!html.includes('Сводка дня'));
});

test('«Чаты»: подбор заявки в общей шапке и единица «заявка»', () => {
  const html = render(ChatQueue, { list: listState(), apiBaseUrl: 'http://stub', department: 'szov', onFind: noop });
  assert.ok(html.includes('Оценить случайную заявку'));
  assert.ok(html.includes('Найти заявку'));
  assert.ok(html.includes('заявки') || html.includes('заявок'));
});

test('ключ ссылки разбирается строго', () => {
  assert.deepEqual(markup.parseRef('imported_call:6371'), { subject: 'imported_call', id: 6371 });
  // Вид заявки Chat2Desk — с цифрой: без неё ссылки СЗоВ были голым текстом.
  assert.deepEqual(markup.parseRef('c2d_snapshot:3741'), { subject: 'c2d_snapshot', id: 3741 });
  assert.deepEqual(markup.parseRef('ca_episode:1'), { subject: 'ca_episode', id: 1 });
  assert.deepEqual(markup.parseRef('wz_episode:49318'), { subject: 'wz_episode', id: 49318 });
  for (const bad of ['', 'call', 'call:', 'call:12a', 'CALL:1', '<b>:1', 'call:1:2', '2c:1', '_x:1']) {
    assert.equal(markup.parseRef(bad), null, bad);
  }
});

const dayView = (data, extra = {}) => baseDigest({
  openDay: '2026-10-01',
  view: { day: '2026-10-01', loading: false, error: '', stalled: false, ...extra.view, data: {
    day: '2026-10-01', status: 'ready', running: false, evaluated: 124, stale: false, new_evaluations: 0,
    generated_at: '2026-10-02T03:41:00+00:00', can_force: false, can_generate: true,
    headline: 'Главное', overview_html: null,
    stats: { evaluated: 124, ai_avg: 89, critical: 2, reviewed: 6, operators: 13, below_60: 2 },
    usual: { ai_avg: 84 }, sections: [], ...data } },
  ...extra.digest,
});

test('упавший раздел дописывает обычное «Обновить», наблюдателю — без кнопок', () => {
  const failed = [section('74', 'Поток', '', {
    error: 'ИИ был перегружен — раздел допишется при следующем обновлении сводки.' }),
                  section('73', 'Основа ОП', '<p>Текст</p>')];
  const html = render(DigestView, { digest: dayView({ sections: failed }), onOpenRef: noop });
  assert.ok(html.includes('ИИ не смог написать этот раздел'));
  assert.ok(html.includes('ИИ был перегружен'));
  assert.ok(html.includes('Дописать раздел'));
  // Число разговоров — в шапке карточки; в подписи под текстом его больше нет.
  assert.ok(!html.includes('124 разговор'), 'подпись повторяет «Оценено»');

  const observer = render(DigestView, {
    digest: dayView({ sections: failed, can_generate: false, stale: true, new_evaluations: 3 }),
    onOpenRef: noop });
  for (const button of ['Дописать раздел', 'Новых оценок', 'Обновить сводку', 'Составить сводку']) {
    assert.ok(!observer.includes(button), `наблюдателю показана кнопка «${button}»`);
  }
  // Разделы целы, а «Главное» не написалось — в шапке дня «дописать».
  const noOverview = render(DigestView, {
    digest: dayView({ incomplete: true, sections: [section('73', 'Основа ОП', '<p>Текст</p>'),
                                                   section('74', 'Поток', '<p>Текст</p>')] }),
    onOpenRef: noop });
  assert.ok(noOverview.includes('Сводка дописана не полностью — дописать'));
  const none = render(DigestView, {
    digest: dayView({ status: 'none', sections: [], can_generate: false }), onOpenRef: noop });
  assert.ok(none.includes('Сводки за этот день ещё нет'));
  assert.ok(!none.includes('Составить сводку'));
});

test('опрос генерации потерял связь — «проверить», а не вечное «обновится сама»', () => {
  const html = render(DigestView, {
    digest: dayView({ running: true, status: 'none', sections: [] }, { view: { stalled: true } }),
    onOpenRef: noop });
  assert.ok(html.includes('Связь с сервером прервалась'));
  assert.ok(html.includes('Проверить'));
  assert.ok(!html.includes('страница обновится сама'));
  const written = render(DigestView, {
    digest: dayView({ running: true, sections: [section('73', 'Основа ОП', '<p>Т</p>')] },
                    { view: { stalled: true } }),
    onOpenRef: noop });
  assert.ok(written.includes('Нет связи — проверить'));
  assert.ok(!written.includes('ИИ обновляет сводку'));
});

test('вопрос к ИИ привязан к своему дню: в чате соседнего дня ожидания нет', () => {
  // Правило хука: спросили о 01.10 и перешли к 30.09 — там не «думает».
  assert.equal(digestRules.askingFor(['2026-10-01'], '2026-10-01'), true);
  assert.equal(digestRules.askingFor(['2026-10-01'], '2026-09-30'), false);
  assert.equal(digestRules.askingFor([], '2026-10-01'), false);
  assert.equal(digestRules.askingFor(['2026-10-01'], null), false);
  const chat = { day: '2026-10-01', messages: [], loading: false, error: '' };
  const idle = render(DigestView, {
    digest: dayView({ sections: [section('73', 'Основа ОП', '<p>Т</p>')] }, { digest: { asking: false, chat } }),
    onOpenRef: noop });
  assert.ok(!idle.includes('Читаю разговоры дня'));
  assert.ok(idle.includes('у длинных'), 'пустой чат не предупреждает, что середина длинных разговоров пропущена');
  const busy = render(DigestView, {
    digest: dayView({ sections: [section('73', 'Основа ОП', '<p>Т</p>')] }, { digest: { asking: true, chat } }),
    onOpenRef: noop });
  assert.ok(busy.includes('Читаю разговоры дня'));
  assert.ok(busy.includes('Думаю…'));
});

test('опрос генерации: паузы растут, после шести сбоев подряд — «связь прервалась»', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5].map(digestRules.pollDelay), [5000, 10000, 20000, 30000, 30000, 30000]);
  assert.equal(digestRules.pollGivesUp(4), false);
  assert.equal(digestRules.pollGivesUp(5), true);
});

test('строка дня в списке сверяется с прочитанной сводкой', () => {
  const days = [{ day: '2026-10-01', running: true, headline: '' }, { day: '2026-09-30', running: true, headline: '' }];
  const synced = digestRules.syncDayRow(days, '2026-10-01', { running: false, headline: 'Главное дня' });
  assert.deepEqual(synced[0], { day: '2026-10-01', running: false, headline: 'Главное дня' });
  assert.equal(synced[1].running, true, 'чужая строка не трогается');
  assert.equal(digestRules.syncDayRow(null, '2026-10-01', {}), null, 'незагруженный список не превращается в пустой');
});

test('сводка есть, но в общих разделах: честная надпись без «Составить»', () => {
  const html = render(DigestView, {
    digest: dayView({ sections: [], hidden_own: 3, status: 'ready' }), onOpenRef: noop });
  assert.ok(html.includes('Сводка этого дня — в общих разделах'));
  assert.ok(!html.includes('Составить сводку'));
  assert.ok(!html.includes('Сводки за этот день ещё нет'));
  const list = render(DigestView, { digest: baseDigest({ days: [
    { day: '2026-10-01', evaluated: 12, critical: 0, operators: 3, reviewed: 0, ai_avg: 80, human_avg: null,
      headline: '', running: false, digest_hidden: true }] }), onOpenRef: noop });
  assert.ok(list.includes('Сводка — в общих разделах, их видит глава отдела'));
  assert.ok(!list.includes('Сводка не составлена'));
});

test('«Начать заново» недоступно, пока ИИ отвечает', () => {
  const chat = { day: '2026-10-01', loading: false, error: '', messages: [
    { id: 'local-1', role: 'user', body: 'Кто ошибся?', created_at: '2026-10-02T04:00:00+00:00', pending: true }] };
  const busy = render(DigestView, {
    digest: dayView({ sections: [section('73', 'Основа ОП', '<p>Т</p>')] }, { digest: { asking: true, chat } }),
    onOpenRef: noop });
  assert.match(busy, /aria-label="Действия с перепиской"[^>]*disabled=""|disabled=""[^>]*aria-label="Действия с перепиской"/);
});
