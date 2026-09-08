// Окно поверх других окон: перенос стилей и темы в чужой документ.
//
// Проверять тут стоит ровно одно место, и оно же — причина, по которой модуль
// вообще появился. Две прежние копии этого кода разъехались на одной строке:
// табло СЗоВ подставляло клонированной <link> РАЗРЕШЁННЫЙ href, а закреплённая
// задача копировала атрибут как есть. У PiP-окна свой базовый адрес, поэтому
// относительный путь в нём ищется от другого корня, и окно открывается голым
// HTML — без единой ошибки в консоли и без падения сборки.
//
// Запуск: node --test tests/pip_window.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';

import { cloneDocumentStyles, mirrorDocumentChrome } from '../src/utils/pipWindow.js';

/** Узел <link> так, как его отдаёт браузер: атрибут относительный, свойство — нет. */
const linkNode = (attrHref, absoluteHref) => {
    const node = {
        tagName: 'LINK',
        href: absoluteHref,
        attrHref,
        cloneNode() { return { tagName: 'LINK', href: this.attrHref }; },
    };
    return node;
};

const styleNode = (text) => ({
    tagName: 'STYLE',
    textContent: text,
    cloneNode() { return { tagName: 'STYLE', textContent: text }; },
});

const fakeSource = (nodes, { theme = null, bodyClass = '' } = {}) => ({
    querySelectorAll: () => nodes,
    documentElement: { getAttribute: (name) => (name === 'data-otp-theme' ? theme : null) },
    body: { className: bodyClass },
});

const fakeTarget = () => {
    const head = [];
    const attrs = {};
    return {
        head,
        attrs,
        window: {
            document: {
                head: { appendChild: (node) => head.push(node) },
                documentElement: { setAttribute: (name, value) => { attrs[name] = value; } },
                body: { className: '' },
            },
        },
    };
};

const withDocument = (source, run) => {
    const previous = globalThis.document;
    globalThis.document = source;
    try {
        return run();
    } finally {
        if (previous === undefined) delete globalThis.document;
        else globalThis.document = previous;
    }
};

test('ссылка на стили едет в окно с РАЗРЕШЁННЫМ адресом, а не с относительным', () => {
    const source = fakeSource([linkNode('/assets/main-abc.css', 'https://portal.example/assets/main-abc.css')]);
    const target = fakeTarget();
    withDocument(source, () => cloneDocumentStyles(target.window));

    assert.equal(target.head.length, 1);
    // Это и есть строка, на которой копии разъехались: без неё в окне остался бы
    // «/assets/main-abc.css», который PiP-документ ищет от своего корня.
    assert.equal(target.head[0].href, 'https://portal.example/assets/main-abc.css');
});

test('встроенные стили переносятся вместе со ссылками', () => {
    const source = fakeSource([
        styleNode('.a{color:red}'),
        linkNode('/x.css', 'https://portal.example/x.css'),
    ]);
    const target = fakeTarget();
    withDocument(source, () => cloneDocumentStyles(target.window));

    assert.deepEqual(target.head.map((n) => n.tagName), ['STYLE', 'LINK']);
    assert.equal(target.head[0].textContent, '.a{color:red}');
});

test('тёмный режим уезжает в окно: он включается атрибутом, а не темой системы', () => {
    const source = fakeSource([], { theme: 'dark', bodyClass: 'bg-gray-50 font-sans' });
    const target = fakeTarget();
    withDocument(source, () => mirrorDocumentChrome(target.window));

    assert.equal(target.attrs['data-otp-theme'], 'dark');
    assert.equal(target.window.document.body.className, 'bg-gray-50 font-sans');
});

test('светлая тема не проставляет атрибут — иначе слой темы включится наполовину', () => {
    const source = fakeSource([], { theme: null, bodyClass: 'bg-gray-50' });
    const target = fakeTarget();
    withDocument(source, () => mirrorDocumentChrome(target.window));

    assert.equal(target.attrs['data-otp-theme'], undefined);
});
