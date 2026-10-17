/*
 * «Период» и сводка за месяц в «Сводке» ИИ-оценки — настоящие компоненты через
 * react-dom/server (как ai_qa_digest_render.test.mjs) и правила без React:
 * границы периода, заголовки, спецификации графиков из цифр, разбор таблицы
 * графика из ответа ИИ.
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
  // Своя папка кеша: node --test гоняет файлы параллельно, и две сборки одного
  // компонента в один файл (с ai_qa_digest_render.test.mjs) ломали импорт.
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests-period');
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
const digestRules = await load('call_qa/DigestView.jsx', '*');
const periodView = await load('call_qa/PeriodView.jsx', '*');
const periodRules = await load('call_qa/usePeriod.js', '*');
const chart = await load('call_qa/QaChart.jsx', '*');
const markup = await load('call_qa/AiMarkup.jsx', '*');

const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));
const noop = () => {};

const baseDigest = (extra = {}) => ({
  days: [
    { day: '2026-10-01', evaluated: 124, critical: 2, operators: 13, reviewed: 6, ai_avg: 88.6,
      human_avg: 93.8, headline: 'Две критические ошибки', running: false },
    { day: '2026-09-30', evaluated: 120, critical: 0, operators: 15, reviewed: 0, ai_avg: 82,
      human_avg: null, headline: '', running: false },
    { day: '2026-09-29', evaluated: 118, critical: 1, operators: 12, reviewed: 0, ai_avg: 80,
      human_avg: null, headline: '', running: false },
  ],
  months: {
    '2026-10': { status: 'none', headline: '', running: false, hidden: false, complete: false },
    '2026-09': { status: 'ready', headline: 'Просели возражения, выросла грамотность', running: false,
                 hidden: false, complete: true },
  },
  daysError: false, openDay: null, view: null, section: null, setSection: noop, pane: 'summary',
  setPane: noop, chat: { day: null, messages: [], loading: false, error: '' }, asking: false,
  draft: '', setDraft: noop, draftFocus: 0, generating: false, open: noop, close: noop,
  reset: noop, reloadDays: noop, reloadView: noop, generate: noop, ask: noop, clearChat: noop,
  suggest: noop, ...extra,
});

const basePeriod = (extra = {}) => ({
  range: null, key: null, view: null, chart: 'score', setChart: noop, section: null, setSection: noop,
  pane: 'summary', setPane: noop, chat: { key: null, messages: [], loading: false, error: '' },
  asking: false, draft: '', setDraft: noop, draftFocus: null, generating: false,
  open: noop, openMonth: noop, close: noop, reset: noop, reloadView: noop, generateMonth: noop,
  ask: noop, clearChat: noop, suggest: noop, ...extra,
});

const summary = {
  totals: { evaluated: 256, calls: 200, chats: 56, ai_avg: 84.2, ai_min: 12, below_60: 7, critical: 5,
            critical_share: 0.02, reviewed: 40, corrected: 3, human_avg: 88.1, operators: 21, days: 22 },
  days: [
    { day: '2026-09-01', evaluated: 10, ai_avg: 80, critical: 1, below_60: 1, reviewed: 2, human_avg: 90, operators: 5 },
    { day: '2026-09-02', evaluated: 0, ai_avg: null, critical: 0, below_60: 0, reviewed: 0, human_avg: null, operators: 0 },
    { day: '2026-09-03', evaluated: 12, ai_avg: 86, critical: 0, below_60: 0, reviewed: 0, human_avg: null, operators: 6 },
  ],
  sections: [{ key: '73', direction: 'Основа ОП', family: 'calls', evaluated: 100, ai_avg: 82, critical: 3, reviewed: 10, human_avg: null, operators: 8, subject_directions: [73] }],
  criteria: [
    { name: 'Приветствие', direction: 'Основа ОП', scale: '73', is_critical: false, applicable: 100, fail: 20, deficiency: 0, bad: 20, bad_share: 0.2 },
    { name: 'Отработка возражений', direction: 'Поток', scale: '74', is_critical: false, applicable: 50, fail: 10, deficiency: 5, bad: 15, bad_share: 0.3 },
    { name: 'Редкий', direction: 'Поток', scale: '74', is_critical: false, applicable: 2, fail: 1, deficiency: 0, bad: 1, bad_share: 0.5 },
  ],
  people: [
    { name: 'Иванова Айгерим', ai_avg: 70.4, talks: 20, critical: 2, fails: 10, deficiencies: 2 },
    { name: 'Петров Арман', ai_avg: 90, talks: 18, critical: 0, fails: 2, deficiencies: 1 },
    { name: 'Без балла', ai_avg: null, talks: 1, critical: 0, fails: 0, deficiencies: 0 },
  ],
  critical_cases: [],
};

const periodData = (extra = {}) => ({
  from: '2026-09-01', to: '2026-09-30', title: 'сентябрь 2026', whole_month: true, days_total: 30,
  pending: [], preparing: false, queued: [], evaluated: 256, summary,
  previous: { from: '2026-08-01', to: '2026-08-31', totals: { ai_avg: 80.0, critical_share: 0.03 } },
  previous_pending: false, digest_days: 12, scope: 'все направления', inputs_hash: 'x',
  month: { month: '2026-09', status: 'none', complete: true, running: false, sections: [], overview_html: null,
           can_generate: true, can_force: false, stale: false, incomplete: false },
  ...extra,
});

test('правила периода: обрезка, целый месяц, соседний месяц, заголовки', () => {
  assert.deepEqual(periodRules.clampRange({ from: '2026-09-01', to: '2026-10-15' }), { from: '2026-09-01', to: '2026-10-01', clamped: true });
  assert.deepEqual(periodRules.clampRange({ from: '2026-10-10', to: '2026-10-01' }), { from: '2026-10-01', to: '2026-10-10', clamped: false });
  assert.equal(periodRules.spanDays({ from: '2026-10-01', to: '2026-10-31' }), 31);
  assert.equal(periodRules.wholeMonthOf({ from: '2026-09-01', to: '2026-09-30' }), '2026-09');
  assert.equal(periodRules.wholeMonthOf({ from: '2026-09-01', to: '2026-09-29' }), null);
  assert.equal(periodRules.shiftMonth('2026-01', -1), '2025-12');
  assert.deepEqual(periodRules.monthRange('2026-09', '2026-10-17'), { from: '2026-09-01', to: '2026-09-30' });
  assert.deepEqual(periodRules.monthRange('2026-10', '2026-10-17'), { from: '2026-10-01', to: '2026-10-17' });
  const presets = periodRules.periodPresets(new Date(2026, 9, 17));
  assert.deepEqual(presets.map((p) => p.label), ['7 дней', '30 дней', 'Прошлая неделя', 'Прошлый месяц']);
  assert.deepEqual(presets[2].range(), { from: '2026-10-05', to: '2026-10-11' });
  assert.deepEqual(presets[3].range(), { from: '2026-09-01', to: '2026-09-30' });
  const now = new Date(2026, 9, 17);
  assert.equal(periodView.periodTitle({ from: '2026-09-01', to: '2026-09-30' }, now), 'Сентябрь 2026');
  assert.equal(periodView.periodTitle({ from: '2026-10-01', to: '2026-10-15' }, now), '1–15 октября');
  assert.equal(periodView.periodTitle({ from: '2026-09-16', to: '2026-10-16' }, now), '16 сентября — 16 октября');
  assert.equal(periodView.monthViewOf({ from: '2026-10-01', to: '2026-10-17' }), periodRules.isoOf(new Date()) === '2026-10-17' ? '2026-10' : null);
  assert.equal(periodView.monthViewOf({ from: '2026-09-02', to: '2026-09-30' }), null);
  assert.deepEqual(digestRules.defaultPeriod(new Date(2026, 9, 17)), { from: '2026-09-18', to: '2026-10-17' });
  assert.equal(digestRules.latestCompleteMonth(baseDigest().months), '2026-09');
  assert.equal(digestRules.monthStatusText({ running: true }), 'ИИ пишет сводку за месяц…');
  assert.equal(digestRules.monthStatusText({ status: 'ready', headline: 'Заголовок' }), 'Заголовок');
  assert.equal(digestRules.monthStatusText({ status: 'ready', hidden: true }), 'Сводка — в общих разделах, их видит глава отдела');
  assert.equal(digestRules.monthStatusText(null), 'Сводка ещё не составлена');
});

test('спецификации графиков из цифр периода', () => {
  const score = periodView.chartSpec('score', summary);
  assert.equal(score.kind, 'line');
  assert.deepEqual(score.categories, ['01.09', '03.09']);
  assert.deepEqual(score.series.map((s) => s.name), ['Балл ИИ', 'Балл человека']);
  assert.deepEqual(score.series[1].values, [90, null]);
  const critical = periodView.chartSpec('critical', summary);
  assert.equal(critical.kind, 'bar');
  assert.deepEqual(critical.series[0].values, [1, 0]);
  const criteria = periodView.chartSpec('criteria', summary);
  assert.equal(criteria.kind, 'hbar');
  // Редкий критерий (меньше трёх применимых) не берётся; направление — в подписи
  // только у одноимённых критериев разных шкал.
  assert.deepEqual(criteria.categories, ['Приветствие', 'Отработка возражений']);
  assert.deepEqual(criteria.series[0].values, [20, 30]);
  const twin = periodView.chartSpec('criteria', { ...summary, criteria: [
    summary.criteria[0], { ...summary.criteria[0], direction: 'Поток', scale: '74' }] });
  assert.deepEqual(twin.categories, ['Приветствие · Основа ОП', 'Приветствие · Поток']);
  const people = periodView.chartSpec('people', summary);
  assert.deepEqual(people.categories, ['Иванова Айгерим', 'Петров Арман']);
  assert.deepEqual(people.series[0].values, [70, 90]);
  assert.equal(periodView.chartSpec('score', { ...summary, days: summary.days.slice(0, 1) }), null);
  assert.equal(periodView.chartSpec('criteria', { ...summary, criteria: [] }), null);
});

test('таблица графика из ответа ИИ разбирается в спецификацию', () => {
  const cell = (text) => ({ textContent: text });
  const row = (...cells) => ({ querySelectorAll: () => cells.map(cell) });
  const table = (rows) => ({ querySelectorAll: () => rows });
  const spec = chart.parseChartTable(table([row('День', 'Балл ИИ', 'Балл человека'), row('01.10', '84'), row('02.10', '81,5', '90 %'), row('03.10', '—', '88')]), 'line', 'Балл');
  assert.equal(spec.kind, 'line');
  assert.deepEqual(spec.categories, ['01.10', '02.10', '03.10']);
  assert.deepEqual(spec.series.map((s) => s.name), ['Балл ИИ', 'Балл человека']);
  assert.deepEqual(spec.series[0].values, [84, 81.5, null]);
  assert.deepEqual(spec.series[1].values, [null, 90, 88]);
  // Чужой вид — столбцы; одна строка данных или ни одного числа — не график.
  assert.equal(chart.parseChartTable(table([row('a', 'b'), row('x', '1'), row('y', '2')]), 'pie').kind, 'bar');
  assert.equal(chart.parseChartTable(table([row('a', 'b'), row('x', '1')]), 'bar'), null);
  assert.equal(chart.parseChartTable(table([row('a', 'b'), row('x', 'много'), row('y', 'мало')]), 'bar'), null);
  assert.deepEqual(markup.MARKUP_ATTRS.filter((a) => a.startsWith('data-qa-')), ['data-qa-ref', 'data-qa-kind', 'data-qa-chart']);
  // Без DOM разметка не режется на куски — один кусок HTML.
  assert.deepEqual(markup.splitMarkup('<p>a</p>'), [{ html: '<p>a</p>' }]);
});

test('список дней: карточка последнего месяца, «За период», ссылка у заголовка месяца', () => {
  const html = render(DigestView, { digest: baseDigest(), period: basePeriod(), onOpenRef: noop });
  assert.ok(html.includes('data-qa-month-card="2026-09"'));
  assert.ok(html.includes('Сводка за сентябрь'));
  assert.ok(html.includes('Просели возражения, выросла грамотность'));
  assert.ok(html.includes('238 разговоров'));
  assert.ok(html.includes('За период'));
  // У карточного месяца ссылка не дублируется; у текущего месяца её нет.
  assert.equal((html.match(/Сводка за месяц</g) || []).length, 0);
  const older = baseDigest({ months: { ...baseDigest().months, '2026-08': { status: 'none', complete: true } },
                              days: [...baseDigest().days, { day: '2026-08-30', evaluated: 5, critical: 0, operators: 2, reviewed: 0, ai_avg: 70, human_avg: null, headline: '' }] });
  const withLink = render(DigestView, { digest: older, period: basePeriod(), onOpenRef: noop });
  assert.ok(withLink.includes('Сводка за месяц<'));
});

test('экран периода: заголовок, цифры, график, чат; месяц без сводки — «Составить»', () => {
  const period = basePeriod({ range: { from: '2026-09-01', to: '2026-09-30' }, key: '2026-09-01:2026-09-30',
                              view: { key: '2026-09-01:2026-09-30', data: periodData(), loading: false, error: '', stalled: false } });
  const html = render(DigestView, { digest: baseDigest(), period, onOpenRef: noop });
  assert.ok(html.includes('Сентябрь 2026'));
  assert.ok(html.includes('Сводка за месяц'));
  assert.ok(html.includes('Средний балл ИИ'));
  assert.ok(html.includes('прошлый период 80'));
  assert.ok(html.includes('2% · было 3%'));
  assert.ok(html.includes('балл человека 88'));
  assert.ok(html.includes('21 сотрудник · 22 дня'));
  assert.ok(html.includes('data-qa-chart-kind="line"'));
  assert.ok(html.includes('Балл по дням'));
  assert.ok(html.includes('Сводки за сентябрь 2026 ещё нет'));
  assert.ok(html.includes('Составить сводку'));
  assert.ok(html.includes('Спросите о периоде'));
  assert.ok(html.includes('Предыдущий месяц'));
  assert.ok(html.includes('data-qa-pane="chat"'));
});

test('экран периода: готовая сводка месяца с разделами, устаревшая — «обновить»', () => {
  const month = {
    month: '2026-09', status: 'ready', complete: true, running: false, can_generate: true, can_force: true,
    stale: true, incomplete: false, headline: 'Главное', overview_html: '<p>Обзор</p>', generated_at: '2026-10-01T05:40:00Z',
    sections: [{ key: '73', direction: 'Основа ОП', family: 'calls', headline: 'О', html: '<p>Текст</p>',
                 stats: { evaluated: 100, ai_avg: 82, critical: 3, reviewed: 10, human_avg: null, operators: 8, below_60: 2, previous: { ai_avg: 88 } } }],
  };
  const period = basePeriod({ range: { from: '2026-09-01', to: '2026-09-30' }, key: '2026-09-01:2026-09-30', section: '73', chart: 'people',
                              view: { key: '2026-09-01:2026-09-30', data: periodData({ month }), loading: false, error: '', stalled: false } });
  const html = render(DigestView, { digest: baseDigest(), period, onOpenRef: noop });
  assert.ok(html.includes('Оценки изменились — обновить'));
  assert.ok(html.includes('прошлый месяц 88'));
  assert.ok(html.includes('Главное'));
  assert.ok(html.includes('Основа ОП'));
  assert.ok(html.includes('Переписать заново') || html.includes('Ещё'));
  assert.ok(html.includes('data-qa-chart-kind="hbar"'));
});

test('экран периода: данные готовятся и пустой период', () => {
  const preparing = basePeriod({ range: { from: '2026-10-01', to: '2026-10-10' }, key: '2026-10-01:2026-10-10',
                                 view: { key: '2026-10-01:2026-10-10', loading: false, error: '', stalled: false,
                                         data: periodData({ from: '2026-10-01', to: '2026-10-10', whole_month: false, month: undefined,
                                                            pending: ['2026-10-03', '2026-10-04'], preparing: true }) } });
  const html = render(DigestView, { digest: baseDigest(), period: preparing, onOpenRef: noop });
  assert.ok(html.includes('осталось 2 дня'));
  assert.ok(html.includes('1–10 октября'));
  assert.ok(html.includes('Период · 10 дней'));
  assert.ok(!html.includes('Сводка за месяц'));
  const empty = basePeriod({ range: { from: '2026-10-01', to: '2026-10-10' }, key: '2026-10-01:2026-10-10',
                             view: { key: '2026-10-01:2026-10-10', loading: false, error: '', stalled: false,
                                     data: periodData({ whole_month: false, month: undefined, evaluated: 0, pending: [],
                                                        summary: { ...summary, totals: { ...summary.totals, evaluated: 0 } } }) } });
  const emptyHtml = render(DigestView, { digest: baseDigest(), period: empty, onOpenRef: noop });
  assert.ok(emptyHtml.includes('За этот период нет оценённых разговоров'));
});
