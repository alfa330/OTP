/*
 * Режим «Сделки» раздела «Касания» (DealsView) и сам раздел (TouchesView) собираются
 * и отрисовываются без ошибок. Браузерного окружения в проекте нет, поэтому
 * компоненты собираются esbuild'ом в один модуль и рендерятся через
 * react-dom/server: эффекты не запускаются, экран остаётся в начальном
 * состоянии — но любой сломанный импорт, опечатка в JSX или обращение к
 * несуществующему полю падают именно здесь, а не у супервайзера в браузере.
 *
 * Сеть подменяется: axios резолвится на заглушку, у которой get/post никогда не
 * отвечают — в серверном рендере они и не зовутся.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешние react и
// lucide-react не резолвятся. Каталог .cache не отслеживается git.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-cdr-render-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

const AXIOS_STUB = `
  const never = () => new Promise(() => {});
  export default { get: never, post: never };
`;

async function bundle(entry, name) {
    const stubPath = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stubPath, AXIOS_STUB);
    const result = await esbuild.build({
        entryPoints: [new URL(entry, ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
        bundle: true,
        write: false,
        format: 'esm',
        platform: 'node',
        target: 'node18',
        jsx: 'transform',
        loader: { '.jsx': 'jsx', '.js': 'jsx' },
        external: ['react', 'react-dom', 'lucide-react'],
        alias: { axios: stubPath },
        logLevel: 'silent',
    });
    const outPath = join(OUT_DIR, `${name}.mjs`);
    writeFileSync(outPath, result.outputFiles[0].text);
    return import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
}

const props = {
    apiBaseUrl: 'http://api.test',
    withAccessTokenHeader: () => ({}),
    showToast: () => {},
};

test('DealsView отрисовывается в начальном состоянии', async () => {
    const { default: DealsView } = await bundle('src/components/cdr/DealsView.jsx', 'deals');
    const html = renderToStaticMarkup(React.createElement(DealsView, {
        ...props, presets: [], today: '2026-09-16',
    }));
    assert.match(html, /Основа/);
    assert.match(html, /Платный найм/);
    assert.match(html, /Поток/);
    assert.match(html, /Выгрузить в Excel/);
    assert.match(html, /считаем/);
    // Пока данных нет — ни таблицы, ни плашек об ошибках.
    assert.doesNotMatch(html, /ничего не попало/);
});

test('TouchesView собирается с новым режимом', async () => {
    const { default: TouchesView } = await bundle('src/components/cdr/TouchesView.jsx', 'touches');
    const html = renderToStaticMarkup(React.createElement(TouchesView, props));
    assert.match(html, /Касания/);
    assert.match(html, /Звонки/);
    assert.match(html, /Сделки/);
});
