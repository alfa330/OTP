/*
 * Каталог вики: правая колонка НЕ пустует.
 *
 * Зачем этот файл. До 26.08.2026 вкладка «Статьи» открывалась пустой правой
 * колонкой с надписью «Выберите раздел слева»: экран встречал вопросом, хотя
 * ответ у него был — все доступные статьи корзины. Теперь список стоит там с
 * первого кадра, а строка над деревом возвращает его после выбора раздела.
 *
 * Ломается такое молча и одинаково: обращение к значению, объявленному НИЖЕ по
 * телу компонента, даёт ReferenceError на первом же рендере — сборка это
 * пропускает, а человек видит пустой экран. Поэтому компонент здесь настоящий и
 * отрисовывается через react-dom/server; браузерного окружения в проекте нет.
 *
 * Чего этот файл НЕ проверяет: сам запрос списка. Эффекты при серверном рендере
 * не выполняются, поэтому загрузку страницами и границу пространства сторожит
 * tests/test_wiki_catalog.py (читает исходник текстом), а поведение вживую —
 * прогон в браузере.
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
  const outfile = join(dir, 'WikiCatalog.mjs');
  buildSync({
    entryPoints: [fileURLToPath(new URL('../src/components/wiki/WikiCatalog.jsx',
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
const WikiCatalog = mod.default;
const { articleWhere } = mod;

const SPACE = { id: 1, name: 'Таксопарки' };

const counts = (published, draft, archived) => ({ published, draft, archived });

const CATALOG = {
  spaces: [SPACE],
  sections: [
    { id: 10, space_id: 1, parent_section_id: null, name: 'Коммерческий отдел',
      counts: counts(12, 30, 4) },
    { id: 11, space_id: 1, parent_section_id: 10, name: 'Супервайзер',
      counts: counts(5, 9, 0) },
    { id: 20, space_id: 1, parent_section_id: null, name: 'IT-отдел',
      counts: counts(20, 196, 19) },
  ],
  orphans: counts(0, 0, 0),
  totals: counts(37, 235, 23),
};

const render = (props = {}) => renderToStaticMarkup(React.createElement(WikiCatalog, {
  base: '/api/wiki',
  headers: {},
  showToast: () => {},
  catalog: CATALOG,
  loading: false,
  bucket: 'published',
  onBucketChange: () => {},
  onOpenArticle: () => {},
  onEditArticle: () => {},
  reloadCatalog: () => {},
  space: SPACE,
  ...props,
}));

test('экран открывается списком, а не просьбой что-нибудь выбрать', () => {
  const html = render();
  assert.doesNotMatch(html, /Выберите раздел слева/);
  // Пока список едет, колонка честно говорит, что ждёт, — а не показывает «Пусто».
  assert.match(html, /Загружаем/);
});

test('шапка правой колонки называет выборку и её границу', () => {
  const html = render();
  assert.match(html, /Все статьи/);
  assert.match(html, /Все разделы, к которым у вас есть доступ/);
});

test('строка возврата стоит над деревом и знает число статей корзины', () => {
  // 37 — totals.published, то же число, что на кнопке переключателя корзин.
  const html = render();
  const rail = html.slice(0, html.indexOf('Коммерческий отдел'));
  assert.match(rail, /Все статьи/);
  assert.match(rail, /37/);
});

test('название выборки меняется вместе с корзиной', () => {
  // Иначе в «Архиве» строка возврата обещала бы «Все статьи» и врала.
  assert.match(render({ bucket: 'draft' }), /Все черновики/);
  assert.match(render({ bucket: 'draft' }), /235/);
  assert.match(render({ bucket: 'archived' }), /Весь архив/);
  assert.match(render({ bucket: 'archived' }), /23/);
});

test('дерево разделов на месте — полный список его не заменил', () => {
  const html = render();
  assert.match(html, /Таксопарки/);
  assert.match(html, /Коммерческий отдел/);
  assert.match(html, /IT-отдел/);
});

test('пустая корзина: сообщение про всю вику, а не про раздел', () => {
  // Раньше эту фразу показывал отдельный экран «Архив: пусто» до выбора
  // раздела. Экран ушёл, фраза обязана была остаться.
  const empty = { ...CATALOG, totals: counts(0, 0, 0) };
  const html = render({ catalog: empty, bucket: 'archived' });
  assert.match(html, /Весь архив/);
});

test('каталог не ответил — колонка говорит об этом, а не крутит спиннер', () => {
  // busy поднят с первого кадра, а загрузчик ждёт каталога: без отдельной ветки
  // «Загружаем…» осталось бы на экране навсегда.
  const html = render({ catalog: null, loading: false });
  assert.match(html, /Список не загрузился/);
  assert.doesNotMatch(html, /Загружаем/);
});

test('каталог ещё едет — это по-прежнему ожидание, а не ошибка', () => {
  const html = render({ catalog: null, loading: true });
  assert.doesNotMatch(html, /Список не загрузился/);
});

// ── Подпись «где лежит статья» ──────────────────────────────────────────────

const NAMES = new Map([[10, 'Коммерческий отдел'], [11, 'Супервайзер']]);

test('одна ветка — просто её название', () => {
  assert.equal(articleWhere({ section_ids: [11] }, NAMES), 'Супервайзер');
});

test('несколько веток — первая и счётчик, а не простыня', () => {
  assert.equal(articleWhere({ section_ids: [10, 11] }, NAMES), 'Коммерческий отдел +1');
});

test('раздел, которого человеку не показывают, в подпись не попадает', () => {
  // 99 закрыт правами или принадлежит соседней вике: назвать его — рассказать
  // о содержимом чужого раздела.
  assert.equal(articleWhere({ section_ids: [99, 11] }, NAMES), 'Супервайзер');
  assert.equal(articleWhere({ section_ids: [99] }, NAMES), 'Без раздела');
});

test('статья без разделов вовсе — наследие импорта, а не пустая строка', () => {
  assert.equal(articleWhere({ section_ids: [] }, NAMES), 'Без раздела');
  assert.equal(articleWhere({}, NAMES), 'Без раздела');
});
