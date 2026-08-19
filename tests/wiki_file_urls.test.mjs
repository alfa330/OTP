import assert from 'node:assert/strict';
import test from 'node:test';

import {
    absolutizeFileUrls, originOf, relativizeFileUrls,
} from '../src/components/wiki/fileUrls.js';

const BASE = 'https://otp-2-fos4.onrender.com/api/wiki';
const FILE = '/api/wiki/file/1ebdec21-42f3-4413-827b-6c6180da7317';

/* Дефект, который эти тесты закрывают: фронт отдаётся с GitHub Pages, API живёт
   на Render, а в теле статьи адрес файла лежит относительным. Браузер разрешал
   его относительно СТРАНИЦЫ, то есть картинки во всех статьях с загруженными
   файлами (на 19.08.2026 — девять, включая «О компании») запрашивались с
   alfa330.github.io и получали 404. */

test('относительный адрес файла раскрывается до домена API', () => {
    const html = `<p><img src="${FILE}" alt="кадр"></p>`;
    assert.equal(
        absolutizeFileUrls(html, BASE),
        `<p><img src="https://otp-2-fos4.onrender.com${FILE}" alt="кадр"></p>`);
});

test('уже абсолютный адрес не удваивается', () => {
    const html = `<img src="https://otp-2-fos4.onrender.com${FILE}">`;
    assert.equal(absolutizeFileUrls(html, BASE), html);
});

test('ссылки на файл в href обрабатываются так же, как картинки', () => {
    const html = `<a href="${FILE}">скачать</a>`;
    assert.ok(absolutizeFileUrls(html, BASE).includes(
        `href="https://otp-2-fos4.onrender.com${FILE}"`));
});

test('круг «показали → сохранили» возвращает исходный адрес', () => {
    // Ради этого и нужна обратная свёртка: иначе редактор записал бы в базу
    // тело статьи, привязанное к домену API, и смена домена протухла бы во
    // всех статьях разом.
    const stored = `<p><img src="${FILE}"></p><p><img src="data:image/png;base64,AAA"></p>`;
    assert.equal(relativizeFileUrls(absolutizeFileUrls(stored, BASE)), stored);
});

test('свёртка снимает любой домен, а не только текущий', () => {
    const html = `<img src="http://localhost:5000${FILE}">`;
    assert.equal(relativizeFileUrls(html), `<img src="${FILE}">`);
});

test('чужие адреса и base64 не трогаются', () => {
    const html = '<img src="data:image/png;base64,AAA"><img src="https://example.com/x.png">';
    assert.equal(absolutizeFileUrls(html, BASE), html);
    assert.equal(relativizeFileUrls(html), html);
});

test('пустой ввод и отсутствие базы не роняют подстановку', () => {
    assert.equal(absolutizeFileUrls('', BASE), '');
    assert.equal(absolutizeFileUrls(null, BASE), null);
    const html = `<img src="${FILE}">`;
    assert.equal(absolutizeFileUrls(html, ''), html, 'без базы оставляем как было');
    assert.equal(relativizeFileUrls(''), '');
});

test('origin вычисляется из базового адреса раздела', () => {
    assert.equal(originOf(BASE), 'https://otp-2-fos4.onrender.com');
    assert.equal(originOf('https://host/api/wiki/'), 'https://host');
    assert.equal(originOf(''), '');
});
