// «Библиотека» (#282): помощники загрузчика книги (epubText.js) — пути внутри
// архива и стиль книги для теневого корня ридера.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    bookCss, cleanInlineStyle, decodeAnchor, dropCapText, isAsideParagraph, normalizeBreaks, resolvePath,
} from '../src/components/library/epubText.js';

test('пути внутри архива — как у сервера', () => {
    assert.equal(resolvePath('OEBPS', 'Text/ch1.xhtml'), 'OEBPS/Text/ch1.xhtml');
    assert.equal(resolvePath('OEBPS/nav', '../Text/ch%202.xhtml#p2'), 'OEBPS/Text/ch 2.xhtml');
    assert.equal(resolvePath('', './a/../b.xhtml'), 'b.xhtml');
    assert.equal(resolvePath('OEBPS', '../../../etc/passwd'), 'etc/passwd');
    assert.equal(resolvePath('OEBPS', 'bad%E0%A4%A.xhtml'), 'OEBPS/bad%E0%A4%A.xhtml');
});

test('разрывы страниц — в разрывы колонок', () => {
    assert.equal(normalizeBreaks('page-break-before: always'), 'break-before: column');
    assert.equal(normalizeBreaks('h1{page-break-after:always}'), 'h1{break-after: column}');
    assert.equal(normalizeBreaks('break-before: page;'), 'break-before: column;');
    assert.equal(normalizeBreaks('page-break-inside: avoid'), 'page-break-inside: avoid');
});

test('стиль книги: ресурсы — blob, внешнее — прочь', () => {
    const css = bookCss(
        '@import url("x.css"); @font-face { font-family: X; src: url(f.ttf); }'
        + ' .a { background: url("img/bg.png") } .b { background: url(https://tracker.example/p.gif) }',
        (ref) => (ref === 'img/bg.png' ? 'blob:http://x/1' : ''),
    );
    assert.doesNotMatch(css, /@import|@font-face|tracker\.example/);
    assert.match(css, /url\("blob:http:\/\/x\/1"\)/);
    assert.match(css, /background: none/);
});

test('стиль книги: body и html — на корень текста ридера', () => {
    const css = bookCss('body { margin: 5em } body p { text-indent: 1em } html,body{x:y} .body { a: b } p.html { c: d }');
    assert.match(css, /\.lr-body \{ margin: 5em \}/);
    assert.match(css, /\.lr-body p \{ text-indent: 1em \}/);
    assert.match(css, /\.lr-body,\.lr-body\{x:y\}/);
    assert.match(css, /\.body \{ a: b \}/, 'класс .body не трогаем');
    assert.match(css, /p\.html \{ c: d \}/, 'класс .html не трогаем');
});

test('стиль книги: обходы фильтра адресов не проходят', () => {
    // image-set и экранирование: \75 rl( — это тот же url(.
    const css = bookCss('p { background: image-set("https://t.example/a.png" 1x) } q { background: \\75 rl(https://t.example/b.png) } .ok { color: red }');
    assert.doesNotMatch(css, /t\.example/);
    assert.match(css, /\.ok \{ color: red \}/);
});

test('стиль книги: :host — не книге', () => {
    assert.doesNotMatch(bookCss(':host { display: none } p { margin: 0 }'), /:host/);
});

test('атрибут style: сеть и fixed — прочь, остальное — как было', () => {
    assert.equal(cleanInlineStyle('color: red; background-image: url(https://t.example/p.gif); margin: 0'), 'color: red; margin: 0');
    assert.equal(cleanInlineStyle('position: fixed; top: 0'), ' top: 0');
    assert.equal(cleanInlineStyle('page-break-before: always'), 'break-before: column');
    assert.equal(cleanInlineStyle('background: u\\72l(//t.example/p.gif)'), '');
});

test('якорь ссылки раскодируется: %D1%81%D0%BD1 — это «сн1»', () => {
    assert.equal(decodeAnchor('%D1%81%D0%BD1'), 'сн1');
    assert.equal(decodeAnchor('bad%E0%A4%A'), 'bad%E0%A4%A');
});

test('стиль книги: fixed не встанет поверх ридера', () => {
    assert.match(bookCss('.n { position: fixed; }'), /position: static/);
});

test('в модулях ридера нет просмотра назад в регулярках (Safari до 16.4)', async () => {
    const { readFile } = await import('node:fs/promises');
    for (const name of ['epubText.js', 'epubLoader.js', 'pageCurl.js', 'readerEngine.js', 'libraryMeta.js', 'bookOpening.js', 'LibraryCover.jsx', 'LibraryReader.jsx']) {
        const source = await readFile(new URL(`../src/components/library/${name}`, import.meta.url), 'utf8');
        const code = source.replace(/\/\/[^\n]*|\/\*[\s\S]*?\*\//g, '');
        assert.doesNotMatch(code, /\(\?<[=!]/, name);
    }
});

test('буквица — только у абзаца текста, который начинается с буквы', () => {
    const long = ' было всё смешано в доме Облонских. Жена узнала, что муж был в связи с бывшею в их доме француженкою-гувернанткой.';
    assert.equal(dropCapText(`Всё${long}`), true);
    assert.equal(dropCapText(`«Всё${long}`), true);
    assert.equal(dropCapText(`— Всё${long}`), false, 'тире диалога');
    assert.equal(dropCapText(`1812${long}`), false, 'число');
    assert.equal(dropCapText('Короткая строка.'), false, 'одна строка');
});

test('строки при главе — подзаголовок, эпиграф, дата — не текст', () => {
    assert.equal(isAsideParagraph({ className: 'epigraph' }), true);
    assert.equal(isAsideParagraph({ parentClass: 'Motto' }), true);
    assert.equal(isAsideParagraph({ align: 'center' }), true);
    assert.equal(isAsideParagraph({ style: 'text-align: right' }), true);
    assert.equal(isAsideParagraph({ className: 'text' }), false);
    assert.equal(isAsideParagraph({}), false);
});
