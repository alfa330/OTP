import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PHOTO_PREVIEW_TTL_MS,
  createPhotoPreviewCache,
  isPreviewablePhoto,
  splitTaskFiles,
  taskPhotoIds,
} from '../src/components/tasks/taskPhotos.js';

const PHOTO = { id: 11, content_type: 'image/webp', file_name: 'photo.webp' };
const OLD_JPEG = { id: 12, content_type: 'image/jpeg; charset=binary', file_name: 'jpg' };
const DOC = { id: 13, content_type: 'application/pdf', file_name: 'tz.pdf' };
const HEIC = { id: 14, content_type: 'image/heic', file_name: 'IMG.heic' };

test('плиткой — только то, что браузер нарисует и сервер подпишет', () => {
  assert.equal(isPreviewablePhoto(PHOTO), true);
  assert.equal(isPreviewablePhoto(OLD_JPEG), true);
  assert.equal(isPreviewablePhoto(DOC), false);
  assert.equal(isPreviewablePhoto(HEIC), false);
  assert.equal(isPreviewablePhoto({ content_type: 'image/png' }), false, 'без номера плитку не открыть');
});

test('номера картинок карточки — из всех трёх мест и без повторов', () => {
  const clarificationPhoto = { ...PHOTO, id: 21, message_id: 5 };
  const task = {
    attachments: [PHOTO, DOC, clarificationPhoto],
    completion_attachments: [{ id: 31, content_type: 'image/png' }],
    messages: [{ id: 5, attachments: [clarificationPhoto] }],
  };
  assert.deepEqual(taskPhotoIds(task), [11, 21, 31]);
  assert.deepEqual(taskPhotoIds({ attachments: [DOC] }), [], 'без картинок запроса нет');
  assert.deepEqual(taskPhotoIds(null), []);
});

test('пока ждём ответа — пустая плитка, после ответа без адреса — кнопка файла', () => {
  const list = [PHOTO, OLD_JPEG, DOC];
  const waiting = splitTaskFiles(list, {}, false, new Set());
  assert.deepEqual(waiting.photos.map((item) => item.attachment.id), [11, 12]);
  assert.deepEqual(waiting.files.map((item) => item.id), [13]);

  const previews = { 11: { url: 'u11', thumbUrl: 't11' } };
  const settled = splitTaskFiles(list, previews, true, new Set());
  assert.deepEqual(settled.photos.map((item) => item.attachment.id), [11]);
  assert.equal(settled.photos[0].preview.thumbUrl, 't11');
  assert.deepEqual(settled.files.map((item) => item.id), [12, 13], 'старый файл не подписан — скачивается');
});

test('картинка, которую <img> не смог нарисовать, становится кнопкой', () => {
  const previews = { 11: { url: 'u11', thumbUrl: 't11' } };
  const { photos, files } = splitTaskFiles([PHOTO], previews, true, new Set([11]));
  assert.equal(photos.length, 0);
  assert.deepEqual(files.map((item) => item.id), [11]);
});

test('кэш: повторное открытие карточки без запроса, дослали файл — запрос', () => {
  const cache = createPhotoPreviewCache();
  const byId = cache.write(7, [11, 12], [{ id: 11, url: 'u11', thumb_url: 't11' }], 1000);
  assert.deepEqual(byId, { 11: { url: 'u11', thumbUrl: 't11' } });
  // 12 сервер не подписал (старый файл в базе) — всё равно «известен»,
  // иначе такая карточка ходила бы на сервер при каждом открытии.
  assert.deepEqual(cache.read(7, [11, 12], 2000), byId);
  assert.equal(cache.read(7, [11, 12, 15], 2000), null, 'новая картинка уточнением — нужен запрос');
  assert.equal(cache.read(8, [11], 2000), null);
});

test('кэш: ответ старше срока не отдаётся, drop сбрасывает сразу', () => {
  const cache = createPhotoPreviewCache();
  cache.write(7, [11], [{ id: 11, url: 'u', thumb_url: '' }], 0);
  assert.equal(cache.read(7, [11], PHOTO_PREVIEW_TTL_MS)?.[11]?.thumbUrl, 'u', 'без миниатюры — полный кадр');
  assert.equal(cache.read(7, [11], PHOTO_PREVIEW_TTL_MS + 1), null);
  cache.write(7, [11], [{ id: 11, url: 'u' }], 0);
  cache.drop(7);
  assert.equal(cache.read(7, [11], 1), null);
});

test('кэш не растёт без предела', () => {
  const cache = createPhotoPreviewCache(PHOTO_PREVIEW_TTL_MS, 3);
  [1, 2, 3, 4].forEach((taskId) => cache.write(taskId, [taskId], [{ id: taskId, url: 'u' }], 0));
  assert.equal(cache.read(1, [1], 1), null, 'самая старая задача вытеснена');
  assert.ok(cache.read(4, [4], 1));
});
