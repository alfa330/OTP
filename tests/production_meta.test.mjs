import test from 'node:test';
import assert from 'node:assert/strict';

import { crc32c, createCrc32c } from '../src/components/production/crc32c.js';
import {
  ACCEPT_ATTRIBUTE,
  BOARD_COLUMNS,
  DONE_GROUPS,
  MAX_MATERIAL_BYTES,
  VIDEO_ACCEPT_ATTRIBUTE,
  canCapturePoster,
  classifyFile,
  classifyLibraryFile,
  columnOf,
  dueInfo,
  fileExtension,
  filesLabel,
  formatBytes,
  formatDuration,
  formatEta,
  groupBoard,
  itemGroupOf,
  retryDelayMs,
  sessionOutcome,
  shortName,
  speedOf,
} from '../src/components/production/productionMeta.js';

/* Раздел «Продакшн» (задача #316): расчёты без React — контрольная сумма файла,
   отбор файлов до загрузки, раскладка доски, подписи и повторы загрузки. */

test('CRC32C совпадает с контрольными значениями RFC 3720 и с подсчётом кусками', () => {
  const hex = (n) => n.toString(16).padStart(8, '0');
  assert.equal(hex(crc32c(new TextEncoder().encode('123456789'))), 'e3069283');
  assert.equal(hex(crc32c(new Uint8Array(32))), '8a9136aa');
  assert.equal(hex(crc32c(new Uint8Array(32).fill(0xff))), '62a8ab43');
  assert.equal(hex(crc32c(Uint8Array.from({ length: 32 }, (_, i) => i))), '46dd794e');
  assert.equal(hex(crc32c(Uint8Array.from({ length: 32 }, (_, i) => 31 - i))), '113fdb5c');
  assert.equal(crc32c(new Uint8Array(0)), 0);
  const data = Uint8Array.from({ length: 100003 }, (_, i) => (i * 2654435761) >>> 24);
  const parts = createCrc32c();
  // Куски нечётной длины: слайсинг по 8 байт обязан стыковаться через границы.
  for (let at = 0; at < data.length; at += 997) parts.update(data.subarray(at, Math.min(data.length, at + 997)));
  assert.equal(parts.value(), crc32c(data));
});

test('выбор файлов отсекает то же, что сервер', () => {
  assert.equal(fileExtension('Промо «Осень».MOV'), 'mov');
  assert.equal(fileExtension('без_расширения'), '');
  assert.equal(fileExtension('.bashrc'), '');
  const ok = classifyFile({ name: 'ролик.mp4', size: 10 });
  assert.deepEqual([ok.ok, ok.kind, ok.contentType], [true, 'video', 'video/mp4']);
  assert.equal(classifyFile({ name: 'virus.exe', size: 10 }).ok, false);
  assert.equal(classifyFile({ name: 'пусто.mp4', size: 0 }).error, 'Файл пустой');
  assert.equal(classifyFile({ name: 'big.mp4', size: MAX_MATERIAL_BYTES + 1 }).error, 'Файл больше 2 ГБ');
  assert.equal(classifyFile({ name: 'ровно.mp4', size: MAX_MATERIAL_BYTES }).ok, true);
  assert.ok(ACCEPT_ATTRIBUTE.split(',').includes('.psd'));
  assert.ok(canCapturePoster('video', 'video/webm'));
  assert.ok(!canCapturePoster('video', 'video/x-matroska'));
  assert.ok(canCapturePoster('image', 'image/png'));
  assert.ok(!canCapturePoster('image', 'image/heic'));
  assert.ok(!canCapturePoster('file', 'application/pdf'));
});

test('доска раскладывает задачи по статусам, ближайший срок сверху', () => {
  assert.deepEqual(BOARD_COLUMNS.map((c) => c.title), ['К выполнению', 'В работе', 'На проверке']);
  assert.equal(columnOf('returned'), 'progress');
  assert.equal(columnOf('accepted'), null);
  const grouped = groupBoard([
    { id: 1, status: 'in_progress', due_at: null, created_at: '2026-10-10T10:00:00' },
    { id: 2, status: 'returned', due_at: '2026-10-20T18:00:00', created_at: '2026-10-09T10:00:00' },
    { id: 3, status: 'in_progress', due_at: '2026-10-18T18:00:00', created_at: '2026-10-08T10:00:00' },
    { id: 4, status: 'in_progress', due_at: null, created_at: '2026-10-12T10:00:00' },
    { id: 5, status: 'completed', due_at: null, created_at: '2026-10-01T10:00:00' },
    { id: 6, status: 'accepted', due_at: null, created_at: '2026-10-01T10:00:00' },
  ]);
  assert.deepEqual(grouped.progress.map((t) => t.id), [3, 2, 4, 1]);
  assert.deepEqual(grouped.review.map((t) => t.id), [5]);
  assert.deepEqual(grouped.todo, []);
});

test('срок горит только у незакрытой задачи', () => {
  const now = new Date('2026-10-16T12:00:00');
  assert.equal(dueInfo({ status: 'in_progress', due_at: '2026-10-15T18:00:00' }, now).overdue, true);
  assert.equal(dueInfo({ status: 'completed', due_at: '2026-10-15T18:00:00' }, now).overdue, false);
  assert.equal(dueInfo({ status: 'in_progress', due_at: '2026-10-25T18:00:00' }, now).label, '25 окт');
  assert.equal(dueInfo({ status: 'in_progress', due_at: '2027-01-05T18:00:00' }, now).label, '5 янв 2027');
  assert.equal(dueInfo({ status: 'in_progress' }, now), null);
});

test('подписи: размер, длительность, остаток, имена', () => {
  assert.equal(formatBytes(512), '512 Б');
  assert.equal(formatBytes(2785589), '2,7 МБ');
  assert.equal(formatBytes(317358389), '303 МБ');
  assert.equal(formatBytes(2 * 1024 ** 3), '2 ГБ');
  assert.equal(formatDuration(5.988), '0:06');
  assert.equal(formatDuration(3725), '1:02:05');
  assert.equal(formatDuration(0), '');
  assert.equal(formatEta(30), 'меньше минуты');
  assert.equal(formatEta(600), 'ещё 10 мин');
  assert.equal(formatEta(5400), 'ещё 1 ч 30 мин');
  assert.equal(formatEta(Infinity), '');
  assert.equal(filesLabel(1), '1 файл');
  assert.equal(filesLabel(3), '3 файла');
  assert.equal(filesLabel(11), '11 файлов');
  assert.equal(shortName('Иманова Айгерим Сериковна'), 'Иманова А.');
});

test('«Готовое»: виды из справочника брифа', () => {
  assert.deepEqual(DONE_GROUPS.map((g) => g.value), ['', 'print', 'digital', 'video', 'brand']);
  assert.equal(itemGroupOf('promo_video'), 'video');
  assert.equal(itemGroupOf('static_banner'), 'digital');
  assert.equal(itemGroupOf('нет'), null);
});

test('загрузка: что делать с ответом хранилища и когда повторять', () => {
  assert.equal(sessionOutcome(200), 'done');
  assert.equal(sessionOutcome(201), 'done');
  assert.equal(sessionOutcome(410), 'expired');
  assert.equal(sessionOutcome(404), 'expired');
  assert.equal(sessionOutcome(0), 'retry');
  assert.equal(sessionOutcome(308), 'retry');
  assert.equal(sessionOutcome(503), 'retry');
  assert.equal(sessionOutcome(429), 'retry');
  assert.equal(sessionOutcome(403), 'fail');
  assert.deepEqual([1, 2, 3, 6, 10].map(retryDelayMs), [1000, 2000, 4000, 30000, 30000]);
  const now = 100000;
  assert.equal(speedOf([{ t: now - 4000, loaded: 0 }, { t: now, loaded: 40 * 1024 * 1024 }], now), 10 * 1024 * 1024);
  assert.equal(speedOf([{ t: now - 20000, loaded: 0 }, { t: now, loaded: 5 }], now), 0);
});

test('загрузка: 408 от хранилища — повторить, а не бросить файл', () => {
  assert.equal(sessionOutcome(408), 'retry');
  assert.equal(sessionOutcome(400), 'fail');
});

test('«Ролики» принимают только ролики — отказ ещё до загрузки', () => {
  assert.equal(classifyLibraryFile({ name: 'монтаж.mov', size: 10 }).ok, true);
  assert.equal(classifyLibraryFile({ name: 'баннер.png', size: 10 }).ok, false);
  assert.match(classifyLibraryFile({ name: 'баннер.png', size: 10 }).error, /только ролики/);
  assert.equal(classifyLibraryFile({ name: 'virus.exe', size: 10 }).ok, false);
  assert.deepEqual(VIDEO_ACCEPT_ATTRIBUTE.split(',').sort(), ['.avi', '.m4v', '.mkv', '.mov', '.mp4', '.webm']);
});

