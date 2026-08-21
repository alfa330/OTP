/*
 * «Кому открыт раздел» — модалка выдачи доступа. Настоящий компонент
 * отрисовывается через react-dom/server: браузерного окружения в проекте нет,
 * а серверный рендер закрывает главное — что экран вообще открывается.
 *
 * Зачем именно этот файл. 21.08.2026 в модалку добавили третье измерение
 * выдачи (какие права раздающий вправе поставить), правку существующего
 * точечного правила и авто-раскрытие блока правил. Любая из этих правок ломается
 * молча одинаково: обращение к значению, объявленному НИЖЕ по телу компонента,
 * даёт ReferenceError уже на первом рендере — сборка такое пропускает, а у
 * человека вместо модалки пустой экран.
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

async function loadComponent() {
  /* Собранный модуль кладём ВНУТРЬ проекта: из системной временной папки
     `import 'react'` не разрешается — node ищет node_modules вверх от файла. */
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, 'WikiSectionAccess.mjs');
  buildSync({
    entryPoints: [fileURLToPath(new URL('../src/components/wiki/WikiSectionAccess.jsx',
                                        import.meta.url))],
    bundle: true,
    format: 'esm',
    target: 'node18',
    outfile,
    // Пакеты оставляем внешними: их резолвит сам node из node_modules проекта.
    external: ['react', 'react-dom', 'axios', 'lucide-react'],
    loader: { '.jsx': 'jsx' },
    logLevel: 'silent',
  });
  const mod = await import(`file://${outfile.replace(/\\/g, '/')}`);
  return mod.default;
}

const WikiSectionAccess = await loadComponent();

const SECTION = { id: 3, name: 'Супервайзер', parent_section_id: 2, space_id: 1 };
const SECTIONS = [
  { id: 1, name: 'Коммерческий директор', parent_section_id: null, space_id: 1 },
  { id: 2, name: 'Руководитель группы', parent_section_id: 1, space_id: 1,
    department_id: 1, department_name: 'СЗоВ' },
  SECTION,
];

const render = (props = {}) => renderToStaticMarkup(React.createElement(WikiSectionAccess, {
  base: '/api/wiki',
  headers: {},
  showToast: () => {},
  section: SECTION,
  sections: SECTIONS,
  onClose: () => {},
  reload: () => {},
  ...props,
}));

test('экран доступа открывается и называет раздел', () => {
  const html = render();
  assert.match(html, /Супервайзер/);
});

test('путь раздела показан целиком — правило не уедет в чужую ветку', () => {
  // Ровно ради этого экран переехал в строку раздела: у СЗоВ и у ОП свои
  // одноимённые «Супервайзер», и по плоскому списку их было не различить.
  assert.match(render(), /Коммерческий директор › Руководитель группы › Супервайзер/);
});

test('отдел ветки подписан и унаследован от родителя', () => {
  const html = render();
  assert.match(html, /Отдел ветки: СЗоВ/);
  assert.match(html, /Унаследован от «Руководитель группы»/);
});

test('блок точечных правил есть — именно его владелец искал 21.08.2026', () => {
  assert.match(render(), /Точечные правила/);
});
