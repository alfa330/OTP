import test from 'node:test';
import assert from 'node:assert/strict';

import {
  attachmentKind, attachmentMeta, fileExtension, formatBytes,
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
