/*
 * Шапка карточки звонка «ИИ-оценки» рисуется настоящим компонентом через
 * react-dom/server — так же, как ai_qa_panels_render.test.mjs.
 *
 * Стережём пометку о распознавании. Слабую запись повторно распознаёт Gemini, и
 * процент уверенности у неё — от первого прохода Soniox: к показанному тексту он не
 * относится. Вместо него карточка пишет «распознано повторно», а процент уходит в
 * подсказку. У обычной расшифровки всё по-прежнему — «распознавание N%».
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

async function load(relative) {
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
  return mod.default;
}

const CallReviewCard = await load('call_qa/CallReviewCard.jsx');

const card = (extra = {}) => renderToStaticMarkup(React.createElement(CallReviewCard, {
  call: {
    id: 7060, subject_kind: 'imported_call', operator: 'Оператор Тестовый', direction: 'Основа ОП',
    datetime: '04.10.2026, 16:00', languages: { kk: 92, ru: 8 }, asr_mean_conf: 0.88,
    criteria: [], ai_score: 98,
    transcript: [
      { spk: '1', speaker: 'operator', start_ms: 0, seg: [{ t: 'Здравствуйте, чем могу помочь?' }] },
      { spk: '2', speaker: 'client', start_ms: 5300, seg: [{ t: 'Хочу подключиться к таксопарку.' }] },
    ],
    ...extra,
  },
  onSave() {}, onSkip() {},
}));

test('обычная расшифровка показывает уверенность распознавания', () => {
  for (const asr of [undefined, null]) {
    const html = card({ asr });
    assert.match(html, /распознавание 88%/);
    assert.doesNotMatch(html, /распознано повторно/);
  }
});

test('повторно распознанная запись помечена, а процент первого прохода — в подсказке', () => {
  const html = card({ asr: { engine: 'gemini', model: 'gemini-3.8-flash', first_pass_conf: 0.88 } });
  assert.match(html, /распознано повторно/);
  assert.doesNotMatch(html, /распознавание 88%/);
  assert.match(html, /title="Первое распознавание было неуверенным \(88%\), запись повторно распознала Gemini"/);
});

test('без процента первого прохода подсказка обходится без скобок', () => {
  const html = card({ asr: { engine: 'gemini' }, asr_mean_conf: null });
  assert.match(html, /title="Первое распознавание было неуверенным, запись повторно распознала Gemini"/);
});

test('ненадёжные места подсвечены: неуверенное слово Soniox и реплика, услышанная по-разному', () => {
  const html = card({
    transcript: [
      { spk: '1', speaker: 'operator', seg: [{ t: 'Здравствуйте, я' }, { t: ' Тимур', c: 0.18 }] },
      { spk: '2', speaker: 'client', seg: [{ t: 'Как называется приложение?', u: true }] },
      { spk: '1', speaker: 'operator', seg: [{ t: 'Сейчас подскажу.' }] },
    ],
  });
  assert.match(html, /<mark title="распознано неуверенно · 18%"[^>]*> Тимур<\/mark>/);
  assert.match(html, /<mark title="два распознавания услышали это место по-разному"[^>]*>Как называется приложение\?<\/mark>/);
  assert.doesNotMatch(html, /<mark[^>]*>Сейчас подскажу\.<\/mark>/);
  assert.match(html, /<span>Сейчас подскажу\.<\/span>/);
});

test('стороны реплик — по подписи бэкенда', () => {
  const html = card();
  const operator = html.indexOf('Здравствуйте, чем могу помочь?');
  const client = html.indexOf('Хочу подключиться к таксопарку.');
  assert.ok(operator > 0 && client > operator);
  assert.match(html.slice(0, operator), /justify-start[^]*Оператор/);
  assert.match(html.slice(operator, client), /justify-end[^]*Клиент/);
});
