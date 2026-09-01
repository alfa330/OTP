/*
 * Оглавление витрины вики рисует только опубликованное.
 *
 * Решение владельца 01.09.2026: черновики и архив разбирают на вкладке
 * «Статьи», где под них заведены корзины, а на главной они мешают. Пропорция
 * боевая: 51 опубликованная статья против 239 черновиков и 25 архивных — то
 * есть дерево витрины на 84 % состояло из чужой незаконченной работы. Архивные
 * при этом не отличались от живых ничем: плашка была только у черновика.
 *
 * Компонент здесь НАСТОЯЩИЙ и отрисовывается через react-dom/server: отсев
 * стоит на входе в панель и обязан работать на всех её путях сразу — и на
 * дереве разделов, и на группе «Без раздела», и на счётчике в шапке. Проверка
 * по тексту исходника этого не даёт: она подтвердила бы наличие строки, а не
 * то, что список после неё пуст.
 *
 * Статьи внутри раздела раскрываются нажатием, а событий при серверном рендере
 * нет — поэтому дерево здесь пустое, и все статьи попадают в группу «Без
 * раздела», которая открыта сразу. Тот же самый отфильтрованный список.
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

async function loadPanel() {
  /* Собранный модуль кладём ВНУТРЬ проекта: из системной временной папки
     `import 'react'` не разрешается — node ищет node_modules вверх от файла. */
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, 'WikiIndexPanel.mjs');
  buildSync({
    entryPoints: [fileURLToPath(new URL('../src/components/wiki/WikiIndexPanel.jsx',
                                        import.meta.url))],
    bundle: true,
    format: 'esm',
    target: 'node18',
    outfile,
    external: ['react', 'react-dom', 'axios', 'lucide-react'],
    loader: { '.jsx': 'jsx' },
    logLevel: 'silent',
  });
  const mod = await import(`file://${outfile.replace(/\\/g, '/')}`);
  return mod.default;
}

const WikiIndexPanel = await loadPanel();

const article = (id, title, status) => ({
  id, title, status, slug: `a-${id}`, summary: '', section_ids: [1],
});

const PERIMETER = [
  article(1, 'Регламент выдачи авто', 'published'),
  article(2, 'Черновик про страхование', 'draft'),
  article(3, 'Статья на согласовании', 'on_approval'),
  article(4, 'Требует проверки', 'requires_verification'),
  article(5, 'Старый регламент', 'archived'),
  article(6, 'Просроченная памятка', 'expired'),
  article(7, 'Как заправить авто', 'published'),
];

const render = (articles, tree = []) => renderToStaticMarkup(
  React.createElement(WikiIndexPanel, {
    tree, articles, onOpen: () => {}, loading: false,
  }),
);

test('в оглавлении только опубликованные статьи', () => {
  const html = render(PERIMETER);
  assert.ok(html.includes('Регламент выдачи авто'), 'опубликованная пропала из оглавления');
  assert.ok(html.includes('Как заправить авто'), 'опубликованная пропала из оглавления');
  for (const row of PERIMETER.filter((a) => a.status !== 'published')) {
    assert.ok(!html.includes(row.title), `статья со статусом ${row.status} осталась в оглавлении`);
  }
});

test('все шесть статусов разложены верно: пять корзин мимо витрины', () => {
  // Статусов в CHECK'е шесть, опубликованный один. Правило «не published» —
  // единственное, иначе новый статус тихо просочился бы на главную.
  const shown = PERIMETER.filter((a) => render([a]).includes(a.title));
  assert.deepEqual(shown.map((a) => a.status), ['published', 'published']);
});

test('счётчик в шапке считает то же, что показывает', () => {
  const html = render(PERIMETER);
  const badge = html.match(/tabular-nums">(\d+)</);
  assert.ok(badge, 'счётчик статей исчез из шапки панели');
  assert.equal(badge[1], '2', 'счётчик считает весь периметр, а список — опубликованное');
});

test('плашки «Черновик» в строке больше нет', () => {
  assert.ok(!render(PERIMETER).includes('Черновик'),
    'плашка вернулась — а помечать в оглавлении больше нечего');
});

test('раздел с одними черновиками остаётся в дереве, но пустым', () => {
  /* Ветка «Старая вика» — 239 перенесённых черновиков. Раздел обязан остаться
     виден: он существует, в него будут публиковать. А вот числа рядом с ним
     быть не должно — счётчик показывает только то, что раскроется. */
  const tree = [{
    space: { id: 1, name: 'Таксопарки' },
    rows: [{ section: { id: 1, name: 'Старая вика' }, depth: 0 }],
  }];
  const html = render(PERIMETER.filter((a) => a.status !== 'published'), tree);
  assert.ok(html.includes('Старая вика'), 'раздел пропал из дерева вместе со своими черновиками');
  assert.ok(!/\(\d+\)/.test(html), 'счётчик раздела считает черновики, которые не раскроются');
});
