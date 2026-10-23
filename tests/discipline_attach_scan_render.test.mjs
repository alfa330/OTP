/*
 * «Дисциплина»: кнопка «Приложить…» — выбор «Загрузить файл / Сделать скан»
 * (владелец, 23.10.2026: «кнопку приложить документ сделай при нажатии
 * селектор, загрузить или сделать скан»).
 *
 * Серверный рендер — первый кадр кнопки: на компьютере она открывает меню, на
 * телефоне — лист снизу, а у шаблона DOCX (скан туда не годится) осталась
 * прежней кнопкой выбора файла.
 *
 * Запуск: node --test tests/discipline_attach_scan_render.test.mjs
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
const OUT_DIR = join(new URL('../node_modules/.cache/otp-discipline-attach-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

const result = await esbuild.build({
    entryPoints: [new URL('src/components/discipline/disciplineUi.jsx', ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
    bundle: true,
    write: false,
    format: 'esm',
    platform: 'node',
    target: 'node18',
    jsx: 'transform',
    loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty' },
    external: ['react', 'react-dom', 'lucide-react'],
    logLevel: 'silent',
});
const outPath = join(OUT_DIR, 'disciplineUi.mjs');
writeFileSync(outPath, result.outputFiles[0].text);
const ui = await import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
const { AttachButton, ACCEPT_ACT, ACCEPT_DOCS, ACCEPT_EVIDENCE } = ui;

const render = (props) => renderToStaticMarkup(React.createElement(AttachButton, {
    label: 'Загрузить объяснительную', onFiles: () => {}, ...props,
}));

/* Телефон для useIsMobileShell: matchMedia, отвечающая «да». */
const asPhone = (work) => {
    const before = globalThis.window;
    globalThis.window = {
        matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
    };
    try {
        return work();
    } finally {
        if (before === undefined) delete globalThis.window;
        else globalThis.window = before;
    }
};

test('документ: на компьютере нажатие открывает меню выбора', () => {
    for (const accept of [ACCEPT_DOCS, ACCEPT_ACT, ACCEPT_EVIDENCE]) {
        const html = render({ accept });
        assert.match(html, /<button[^>]*aria-haspopup="menu"[^>]*>.*Загрузить объяснительную<\/button>/, accept);
        // Поле файла то же, что и было: тот же accept, без захвата камеры.
        assert.ok(html.includes(`accept="${accept}"`), accept);
        assert.ok(!html.includes('capture='), accept);
        // Сканер не открыт заранее — камера включается только по «Сделать скан».
        assert.ok(!html.includes('Скан документа'), accept);
    }
});

test('документ: на телефоне — лист снизу, а не меню', () => {
    const html = asPhone(() => render({ accept: ACCEPT_DOCS }));
    assert.match(html, /<button[^>]*aria-haspopup="dialog"[^>]*>.*Загрузить объяснительную<\/button>/);
    assert.ok(!html.includes('aria-haspopup="menu"'));
    // Лист закрыт, пока не нажали.
    assert.ok(!html.includes('mobile-actions'));
});

test('шаблон DOCX — прежняя кнопка выбора файла, без скана', () => {
    for (const html of [render({ accept: '.docx', label: 'Загрузить DOCX' }),
        asPhone(() => render({ accept: '.docx', label: 'Загрузить DOCX' }))]) {
        assert.ok(html.includes('Загрузить DOCX'));
        assert.ok(!html.includes('aria-haspopup'), 'у DOCX не должно быть выбора — скан туда не годится');
    }
});

test('занятая и выключенная кнопка не открывает выбор', () => {
    for (const props of [{ busy: true }, { disabled: true }]) {
        const html = render({ accept: ACCEPT_DOCS, ...props });
        assert.match(html, /<button[^>]*disabled=""[^>]*aria-haspopup="menu"|<button[^>]*aria-haspopup="menu"[^>]*disabled=""/);
    }
    const busy = render({ accept: ACCEPT_DOCS, busy: true });
    assert.ok(busy.includes('animate-spin'), 'спиннер на месте значка');
});

test('ошибка поля — красная кнопка, как и раньше', () => {
    const html = render({ accept: ACCEPT_DOCS, invalid: true });
    assert.ok(html.includes('!text-rose-600'));
    assert.ok(html.includes('ring-rose-400/80'));
});
