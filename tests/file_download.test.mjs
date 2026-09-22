import test from 'node:test';
import assert from 'node:assert/strict';

import { isDownloadableUrl, startFileDownload } from '../src/utils/fileDownload.js';

/*
 * Скачивание в этом же окне: ссылка уходит в скрытый iframe, а не в новую
 * вкладку (почему — в шапке src/utils/fileDownload.js). Документ — заглушка
 * ровно на то, чем пользуется механика: getElementById, createElement и
 * body.appendChild.
 */
const stubDocument = () => {
    const byId = new Map();
    const appended = [];
    const doc = {
        getElementById: (id) => byId.get(id) ?? null,
        createElement: (tag) => ({
            tagName: tag.toUpperCase(),
            style: {},
            attrs: {},
            setAttribute(name, value) { this.attrs[name] = String(value); },
        }),
        body: {
            appendChild(el) { appended.push(el); byId.set(el.id, el); return el; },
        },
    };
    return { doc, appended };
};

const withGlobal = (name, value, fn) => {
    const had = Object.prototype.hasOwnProperty.call(globalThis, name);
    const previous = globalThis[name];
    globalThis[name] = value;
    try {
        return fn();
    } finally {
        if (had) globalThis[name] = previous;
        else delete globalThis[name];
    }
};

const SIGNED = 'https://storage.googleapis.com/otp/iCOREPhone-Setup.exe?X-Goog-Signature=abc';

test('ссылка уходит в скрытый iframe, а не в новое окно', () => {
    const { doc, appended } = stubDocument();
    const opened = [];
    const started = withGlobal('window', { open: (...args) => { opened.push(args); return null; } },
        () => withGlobal('document', doc, () => startFileDownload(SIGNED)));
    assert.equal(started, true);
    assert.equal(opened.length, 0, 'window.open — это и есть новая вкладка');
    assert.equal(appended.length, 1);
    const [frame] = appended;
    assert.equal(frame.tagName, 'IFRAME');
    assert.equal(frame.src, SIGNED);
    assert.equal(frame.style.display, 'none');
    assert.equal(frame.attrs['aria-hidden'], 'true');
});

test('повторное нажатие переиспользует тот же фрейм', () => {
    // Убрать фрейм сразу после src нельзя — загрузка, не дошедшая до заголовков
    // ответа, отменится вместе с ним; второй фрейм на каждое нажатие — мусор.
    const { doc, appended } = stubDocument();
    withGlobal('document', doc, () => {
        startFileDownload(SIGNED);
        startFileDownload(`${SIGNED}&second=1`);
    });
    assert.equal(appended.length, 1);
    assert.equal(appended[0].src, `${SIGNED}&second=1`);
});

test('без ссылки — ошибка словами для тоста, фрейма нет', () => {
    const { doc, appended } = stubDocument();
    for (const bad of ['', null, undefined, '   ', 'javascript:alert(1)', 'data:text/plain,hi', 'ftp://x/y']) {
        assert.throws(
            () => withGlobal('document', doc, () => startFileDownload(bad)),
            /ссылку/,
            `должно отказать: ${String(bad)}`,
        );
    }
    assert.equal(appended.length, 0);
});

test('isDownloadableUrl — только http(s)', () => {
    assert.equal(isDownloadableUrl(SIGNED), true);
    assert.equal(isDownloadableUrl('http://example.com/file.exe'), true);
    assert.equal(isDownloadableUrl(' https://example.com/a '), true);
    assert.equal(isDownloadableUrl('HTTPS://EXAMPLE.COM/A'), true);
    assert.equal(isDownloadableUrl('javascript:alert(1)'), false);
    assert.equal(isDownloadableUrl('//example.com/file.exe'), false);
    assert.equal(isDownloadableUrl(''), false);
    assert.equal(isDownloadableUrl(undefined), false);
});

test('вне браузера — тихий false, без падения', () => {
    // node-тесты, читающие App.jsx, и прогоны без document.
    const had = Object.prototype.hasOwnProperty.call(globalThis, 'document');
    const previous = globalThis.document;
    delete globalThis.document;
    try {
        assert.equal(startFileDownload(SIGNED), false);
    } finally {
        if (had) globalThis.document = previous;
    }
});
