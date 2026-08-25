/*
 * История версий — рендер настоящих компонентов через react-dom/server.
 *
 * Зачем именно так. Браузерного окружения в проекте нет, а серверный рендер
 * закрывает главное: экран вообще открывается. Обращение к значению,
 * объявленному НИЖЕ по телу компонента, даёт ReferenceError на первом же
 * рендере — сборка такое пропускает, а у человека вместо модалки пустой экран
 * (тот же приём, что в wiki_section_access_render).
 *
 * Строки списка и строки сравнения проверяются отдельно: эффекты при серверном
 * рендере не выполняются, то есть данные в саму модалку не попадают никогда.
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

async function loadModule() {
  /* Собранный модуль кладём ВНУТРЬ проекта: из системной временной папки
     `import 'react'` не разрешается — node ищет node_modules вверх от файла. */
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, 'WikiHistory.mjs');
  buildSync({
    entryPoints: [fileURLToPath(new URL('../src/components/wiki/WikiHistory.jsx',
                                        import.meta.url))],
    bundle: true,
    format: 'esm',
    target: 'node18',
    outfile,
    external: ['react', 'react-dom', 'axios', 'lucide-react'],
    loader: { '.jsx': 'jsx' },
    logLevel: 'silent',
  });
  return import(`file://${outfile.replace(/\\/g, '/')}`);
}

const mod = await loadModule();
const WikiHistory = mod.default;
const { DiffLine, HistoryRow } = mod;

const ARTICLE = { id: 618, title: 'Приветствие' };

const render = (element) => renderToStaticMarkup(element);

test('модалка истории открывается', () => {
  const html = render(React.createElement(WikiHistory, {
    base: '/api/wiki', headers: {}, article: ARTICLE, open: true,
    onClose: () => {}, onRestored: () => {}, showToast: () => {},
  }));
  assert.match(html, /История версий/);
  assert.match(html, /Приветствие/);
  // До ответа сервера — состояние загрузки, а не пустой экран.
  assert.match(html, /Собираем историю/);
});

test('закрытая модалка ничего не рисует', () => {
  const html = render(React.createElement(WikiHistory, {
    base: '/api/wiki', headers: {}, article: ARTICLE, open: false, onClose: () => {},
  }));
  assert.equal(html, '');
});

test('строка списка называет автора, время и что изменилось', () => {
  const html = render(React.createElement(HistoryRow, {
    item: {
      key: 'v692', version_id: 692, created_at: '2026-08-24T16:54:10',
      editor_name: 'Алчинбаева Анель', changed: ['content', 'status'],
      comment: 'Поправлен скрипт', is_current: false, is_first: false,
      extra_saves: [{ editor_name: 'Ядигаров Руслан', created_at: '2026-08-24T16:55:00' }],
      saves: 2,
    },
    active: false,
    onSelect: () => {},
  }));
  assert.match(html, /24\.08\.2026, 16:54:10/);
  assert.match(html, /Алчинбаева Анель/);
  assert.match(html, /Текст/);
  assert.match(html, /Статус/);
  assert.match(html, /Поправлен скрипт/);
  assert.match(html, /ещё 1 сохранение без/);
});

test('текущая редакция подписана, созданная — тоже', () => {
  const current = render(React.createElement(HistoryRow, {
    item: { key: 'current', version_id: null, created_at: '2026-08-24T16:58:00',
            editor_name: 'Анель', changed: ['content'], is_current: true,
            is_first: false, extra_saves: [], saves: 1 },
    active: true, onSelect: () => {},
  }));
  assert.match(current, /Текущая/);

  const first = render(React.createElement(HistoryRow, {
    item: { key: 'v1', version_id: 1, created_at: '2026-08-09T00:26:53',
            editor_name: 'Руслан', changed: [], is_current: false,
            is_first: true, extra_saves: [], saves: 1,
            comment: 'Создание статьи' },
    active: false, onSelect: () => {},
  }));
  assert.match(first, /Создание/);
});

test('откат подписан датой редакции, к которой вернулись', () => {
  // Номера версии в подписи нет намеренно: в списке номеров нет вовсе, и
  // «версия №5» отсылала бы к счётчику сохранений, которого человек не видит.
  const html = render(React.createElement(HistoryRow, {
    item: { key: 'current', version_id: null, created_at: '2026-08-25T16:48:30',
            editor_name: 'Руслан', changed: ['content'], is_current: true,
            is_first: false, extra_saves: [], saves: 1,
            restored_from_version_id: 5, comment: 'Восстановление прежней редакции' },
    active: false, onSelect: () => {}, restoredFrom: '25.08.2026, 16:47:07',
  }));
  assert.match(html, /Откат/);
  assert.match(html, /Вернули редакцию от 25\.08\.2026, 16:47:07/);
});

test('строка без автора не показывает пустоту', () => {
  // editor_id в базе — ON DELETE SET NULL: уволенный автор оставляет NULL.
  const html = render(React.createElement(HistoryRow, {
    item: { key: 'v5', version_id: 5, created_at: '2026-08-09T00:26:53',
            editor_name: null, changed: ['content'], is_current: false,
            is_first: false, extra_saves: [], saves: 1 },
    active: false, onSelect: () => {},
  }));
  assert.match(html, /Неизвестно/);
});

test('строки сравнения рисуются каждая по-своему', () => {
  const gap = render(React.createElement(DiffLine, { row: { op: 'gap', skipped: 12 } }));
  assert.match(gap, /12 строк без изменений/);

  const ins = render(React.createElement(DiffLine, { row: { op: 'ins', text: 'новая строка' } }));
  assert.match(ins, /emerald/);
  assert.match(ins, /новая строка/);

  const del = render(React.createElement(DiffLine, { row: { op: 'del', text: 'убранная строка' } }));
  assert.match(del, /rose/);

  const change = render(React.createElement(DiffLine, {
    row: {
      op: 'change', before: 'Один два три', after: 'Один ДВА три',
      before_parts: [{ op: 'same', text: 'Один ' }, { op: 'cut', text: 'два ' },
                     { op: 'same', text: 'три' }],
      after_parts: [{ op: 'same', text: 'Один ' }, { op: 'add', text: 'ДВА ' },
                    { op: 'same', text: 'три' }],
    },
  }));
  // Обе половины на месте, и изменённое слово подсвечено, а не зачёркнуто.
  assert.match(change, /два /);
  assert.match(change, /ДВА /);
  assert.match(change, /bg-rose-200/);
  assert.match(change, /bg-emerald-200/);
  assert.doesNotMatch(change, /line-through/);
});
