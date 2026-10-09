import test from 'node:test';
import assert from 'node:assert/strict';

import {
  docxFileName,
  downloadErrorText,
  portalAddress,
  saveBlob,
} from '../src/components/wiki/articleDownload.js';

/* Имя файла — та же формула, что на сервере (wiki/docx_export.py: file_name);
   набор случаев повторён в tests/test_wiki_docx_export.py::NamingTests. */
test('имя файла: кириллица остаётся, запрещённые знаки уходят', () => {
  assert.equal(docxFileName('Регламент выплат'), 'Регламент выплат.docx');
  assert.equal(docxFileName('Тарифы: Алматы / Астана?'), 'Тарифы Алматы Астана.docx');
  assert.equal(docxFileName('  Много   пробелов  '), 'Много пробелов.docx');
  assert.equal(docxFileName('Точки в конце...'), 'Точки в конце.docx');
  assert.equal(docxFileName('a\tb\nc'), 'a b c.docx');
});

test('имя файла: пустое название и одни служебные знаки — «Статья»', () => {
  assert.equal(docxFileName(''), 'Статья.docx');
  assert.equal(docxFileName(null), 'Статья.docx');
  assert.equal(docxFileName(undefined), 'Статья.docx');
  assert.equal(docxFileName('<>:"|?*\\/'), 'Статья.docx');
});

test('имя файла режется до ста знаков', () => {
  assert.equal(docxFileName('ы'.repeat(300)), 'ы'.repeat(100) + '.docx');
});

test('адрес портала — origin и путь, без строки запроса и якоря', () => {
  assert.equal(
    portalAddress({ origin: 'https://alfa330.github.io', pathname: '/OTP/', search: '?view=wiki', hash: '#x' }),
    'https://alfa330.github.io/OTP/',
  );
  assert.equal(portalAddress(undefined), '');
  assert.equal(portalAddress({}), '');
});

test('saveBlob отдаёт файл ссылкой в этом же окне и прибирает за собой', () => {
  const events = [];
  const link = {
    click: () => events.push('click'),
    remove: () => events.push('remove'),
  };
  const doc = {
    body: { appendChild: (node) => events.push(['append', node === link]) },
    createElement: (tag) => { events.push(['create', tag]); return link; },
  };
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = (blob) => { events.push(['url', blob instanceof Blob]); return 'blob:x'; };
  URL.revokeObjectURL = (url) => events.push(['revoke', url]);
  try {
    assert.equal(saveBlob(new Blob(['a']), 'Статья.docx', doc), true);
    assert.equal(saveBlob(new Uint8Array([1, 2]), 'Статья.docx', doc), true, 'байты тоже заворачиваются в Blob');
  } finally {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
  assert.equal(link.href, 'blob:x');
  assert.equal(link.download, 'Статья.docx');
  assert.deepEqual(events.slice(0, 6), [
    ['url', true], ['create', 'a'], ['append', true], 'click', 'remove', ['revoke', 'blob:x'],
  ]);
  assert.deepEqual(events[6], ['url', true]);
});

test('saveBlob вне браузера ничего не делает', () => {
  assert.equal(saveBlob(new Blob(['a']), 'x.docx', undefined), false);
  assert.equal(saveBlob(new Blob(['a']), 'x.docx', {}), false);
});

test('текст ошибки читается из blob-ответа сервера', async () => {
  const error = {
    message: 'Request failed with status code 403',
    response: { data: new Blob([JSON.stringify({ error: 'Скачивать статьи файлом могут администраторы и выше' })]) },
  };
  assert.equal(await downloadErrorText(error, 'Не удалось'),
    'Скачивать статьи файлом могут администраторы и выше');
});

test('текст ошибки: обычный JSON, не-JSON и сеть', async () => {
  assert.equal(await downloadErrorText({ response: { data: { error: 'Нет статьи' } } }, 'x'), 'Нет статьи');
  assert.equal(await downloadErrorText({ message: 'Network Error', response: { data: new Blob(['<html>']) } }, 'x'),
    'Network Error');
  assert.equal(await downloadErrorText({}, 'Не удалось собрать файл'), 'Не удалось собрать файл');
  assert.equal(await downloadErrorText(undefined, 'Не удалось собрать файл'), 'Не удалось собрать файл');
});
