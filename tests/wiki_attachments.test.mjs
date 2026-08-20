import test from 'node:test';
import assert from 'node:assert/strict';

import {
  attachmentKind, attachmentMeta, fileExtension, fileLinkHtml, formatBytes,
} from '../src/components/wiki/attachments.js';
import { absoluteFileUrl } from '../src/components/wiki/fileUrls.js';

/* Фронт и API живут на разных доменах (страница — GitHub Pages, API — Render),
   поэтому относительный адрес файла обязан раскрываться перед показом. Ровно
   на этом когда-то ломались картинки во всех статьях с загруженными файлами. */
const BASE = 'https://otp-api.onrender.com/api/wiki';

test('адрес приложения раскрывается до абсолютного вместе с ?download', () => {
  assert.equal(
    absoluteFileUrl('/api/wiki/file/abc?download=1', BASE),
    'https://otp-api.onrender.com/api/wiki/file/abc?download=1'
  );
});

test('уже абсолютный адрес не склеивается сам с собой', () => {
  const url = 'https://otp-api.onrender.com/api/wiki/file/abc';
  assert.equal(absoluteFileUrl(url, BASE), url);
});

test('тип определяется по расширению, а не по MIME от браузера', () => {
  // docx часто приезжает как application/octet-stream — имя честнее.
  assert.equal(attachmentKind('Заявление на отпуск.docx').label, 'DOCX');
  assert.equal(attachmentKind('Реестр акций.XLSX').label, 'XLSX');
  assert.equal(attachmentKind('без расширения').label, 'Файл');
  assert.equal(fileExtension('scan.0012.pdf'), 'pdf');
});

test('таблица и документ различаются значком', () => {
  assert.notEqual(
    attachmentKind('план.xlsx').icon,
    attachmentKind('регламент.pdf').icon
  );
});

test('размер читается по-русски и не показывает дробь в килобайтах', () => {
  assert.equal(formatBytes(0), '');
  assert.equal(formatBytes(512), '512 Б');
  assert.equal(formatBytes(238 * 1024), '238 КБ');
  assert.equal(formatBytes(1.25 * 1024 * 1024), '1,3 МБ');
});

test('подпись строки не оставляет точку-сироту у файла без размера', () => {
  assert.equal(attachmentMeta({ name: 'бланк.pdf', size: 0 }), 'PDF');
  assert.equal(attachmentMeta({ name: 'бланк.pdf', size: 2048 }), 'PDF · 2 КБ');
});

/* Карточка файла внутри текста статьи. Она живёт как обычная ссылка с классом:
   у <a> серверный санитайзер разрешает только href, target и class, поэтому
   всё, что несёт смысл, обязано лежать в них и в самом тексте ссылки. */

test('карточка файла собирается ссылкой с классом типа', () => {
  const html = fileLinkHtml({
    name: 'Заявление на отпуск.docx',
    size: 238 * 1024,
    url: '/api/wiki/file/abc',
    download_url: '/api/wiki/file/abc?download=1',
  });
  assert.match(html, /class="wiki-file wiki-file--doc"/);
  assert.match(html, /href="\/api\/wiki\/file\/abc\?download=1"/);
  assert.match(html, /Заявление на отпуск\.docx · 238 КБ/);
});

test('имя файла не может внести разметку в статью', () => {
  const html = fileLinkHtml({ name: '"><img src=x onerror=alert(1)>.pdf', size: 10 });
  assert.ok(!html.includes('<img'), html);
  assert.match(html, /&quot;&gt;&lt;img/);
});

test('без размера подпись остаётся именем файла', () => {
  const html = fileLinkHtml({ name: 'бланк.xlsx', size: 0, url: '/api/wiki/file/x' });
  assert.match(html, /wiki-file--sheet/);
  assert.match(html, />бланк\.xlsx</);
});
