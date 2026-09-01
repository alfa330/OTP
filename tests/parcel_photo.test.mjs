import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test, { mock } from 'node:test';

import {
    PHOTO_ACCEPT, PHOTO_MAX_BYTES, PHOTO_MAX_COUNT, PHOTO_SIDE, PHOTO_TYPES,
    TOBLOB_TIMEOUT_MS, countIssue, fitPhoto, photoIssue, preparePhoto,
    pluralPhotos, shrinkPhoto, sortPhotos, webpName,
} from '../src/components/parcels/parcelPhoto.js';

/* Здесь закрыты решения, которые ломаются МОЛЧА: файл уходит в бакет, но
   показать его потом нечем, или форма отказывает без причины. Браузера в node
   нет — значит `createImageBitmap` недоступен, и каждый путь «обработка не
   удалась» проверяется здесь по-настоящему, а не подменой. */

test('обычная фотография проходит', () => {
    assert.equal(photoIssue({ name: 'box.jpg', type: 'image/jpeg', size: 2 * 1024 * 1024 }), null);
    assert.equal(photoIssue({ name: 'scan.png', type: 'image/png', size: 400 * 1024 }), null);
    assert.equal(photoIssue({ name: 'p.webp', type: 'image/webp', size: 90 * 1024 }), null);
});

test('документ и SVG отклоняются формой, а не сервером', () => {
    // Расхождение с сервером читалось бы как «форма приняла, а сервер отказал»,
    // то есть как поломка.
    assert.ok(photoIssue({ name: 'akt.pdf', type: 'application/pdf', size: 10 }));
    assert.match(photoIssue({ name: 'i.svg', type: 'image/svg+xml', size: 10 }), /не фотография/);
});

test('файл тяжелее предела отклоняется с понятной причиной', () => {
    const issue = photoIssue({ name: 'raw.jpg', type: 'image/jpeg', size: PHOTO_MAX_BYTES + 1 });
    assert.match(issue, /больше 20 МБ/);
});

test('пустой выбор и пустой файл — это причина, а не молчание', () => {
    assert.ok(photoIssue(null));
    assert.match(photoIssue({ name: 'z.jpg', type: 'image/jpeg', size: 0 }), /пустой/);
});

test('перетащенный файл без типа судится по расширению', () => {
    // Некоторые программы отдают файл с пустым type. Отказать ему значило бы
    // сломать ровно то, ради чего перетаскивание и делалось.
    assert.equal(photoIssue({ name: 'IMG_0042.JPEG', type: '', size: 1024 }), null);
    assert.ok(photoIssue({ name: 'notes.txt', type: '', size: 1024 }));
});

test('счётчик места считает остаток, а не факт превышения', () => {
    assert.equal(countIssue(0, 3), null);
    assert.equal(countIssue(7, 3), null);
    assert.match(countIssue(PHOTO_MAX_COUNT, 1), /Больше 10 фотографий/);
    assert.match(countIssue(8, 5), /Поместится ещё 2 фотографии/);
    assert.match(countIssue(9, 4), /Поместится ещё 1 фотография/);
});

test('большой кадр ужимается по длинной стороне, пропорции сохраняются', () => {
    assert.deepEqual(fitPhoto(4032, 3024), { width: PHOTO_SIDE, height: 1536 });
    assert.deepEqual(fitPhoto(1000, 4000), { width: 512, height: PHOTO_SIDE });
});

test('маленький кадр не растягивается', () => {
    assert.deepEqual(fitPhoto(800, 600), { width: 800, height: 600 });
});

test('расширение меняется вместе с содержимым', () => {
    assert.equal(webpName('коробка.jpg'), 'коробка.webp');
    assert.equal(webpName('C:\\photos\\IMG_1.HEIC'), 'IMG_1.webp');
    assert.equal(webpName(''), 'photo.webp');
    // Имя из одних точек оставило бы файл «.webp» — скрытым и безымянным.
    assert.equal(webpName('...'), 'photo.webp');
});

test('без canvas загрузка не срывается: JPEG уходит исходником', async () => {
    // Обработка в браузере — экономия трафика, а не условие загрузки: сервер
    // всё равно переведёт кадр в WebP сам.
    const file = { name: 'box.jpg', type: 'image/jpeg', size: 4096 };
    assert.equal(await shrinkPhoto(file), null);
    const ready = await preparePhoto(file);
    assert.equal(ready.ok, true);
    assert.equal(ready.converted, false);
    assert.equal(ready.blob, file);
});

test('HEIC без обработки НЕ уходит на сервер', async () => {
    // Ни один браузер, кроме Safari, его не покажет, а Pillow на сервере без
    // плагина HEIF не откроет: такой файл лёг бы в бакет мёртвым грузом.
    const ready = await preparePhoto({ name: 'IMG_0042.HEIC', type: 'image/heic', size: 3 * 1024 * 1024 });
    assert.equal(ready.ok, false);
    assert.match(ready.issue, /сохраните фото как JPEG/);
});

test('accept перечисляет форматы, а не image/* — иначе iPhone отдаёт HEIC', () => {
    assert.ok(!PHOTO_ACCEPT.includes('image/*'));
    for (const type of PHOTO_TYPES) assert.ok(PHOTO_ACCEPT.includes(type), type);
});

test('порядок показа один и тот же, откуда бы список ни приехал', () => {
    const photos = [
        { id: 'c', sort_order: 1, created_at: '2026-09-01T10:00:00' },
        { id: 'a', sort_order: 0, created_at: '2026-09-01T10:05:00' },
        { id: 'b', sort_order: 0, created_at: '2026-09-01T10:01:00' },
    ];
    assert.deepEqual(sortPhotos(photos).map((p) => p.id), ['b', 'a', 'c']);
    // Исходный список не трогаем: он приходит из состояния React.
    assert.equal(photos[0].id, 'c');
});

test('счётчик фотографий склоняется', () => {
    assert.equal(pluralPhotos(1), 'фотография');
    assert.equal(pluralPhotos(3), 'фотографии');
    assert.equal(pluralPhotos(5), 'фотографий');
    assert.equal(pluralPhotos(11), 'фотографий');
    assert.equal(pluralPhotos(21), 'фотография');
    assert.equal(pluralPhotos(0), 'фотографий');
});

test('зависший toBlob не оставляет форму в обработке навсегда', async () => {
    // На части Android WebView колбэк toBlob не приходит НИКОГДА. Без сторожа
    // промис не разрешился бы, и человек у стойки не смог бы сохранить посылку
    // вообще — форма так и стояла бы в «обработке».
    const bitmap = globalThis.createImageBitmap;
    const doc = globalThis.document;
    globalThis.createImageBitmap = async () => ({ width: 4000, height: 3000, close() {} });
    globalThis.document = {
        createElement: () => ({
            width: 0,
            height: 0,
            getContext: () => ({ drawImage() {} }),
            toBlob() { /* молчит, как та самая WebView */ },
        }),
    };
    mock.timers.enable({ apis: ['setTimeout'] });
    try {
        const pending = shrinkPhoto({ name: 'box.jpg', type: 'image/jpeg', size: 4096 });
        // Даём отработать микрозадачам до setTimeout: внутри есть await на
        // createImageBitmap, и тик раньше него сторож бы не застал.
        for (let step = 0; step < 10; step += 1) await Promise.resolve();
        mock.timers.tick(TOBLOB_TIMEOUT_MS + 1);
        assert.equal(await pending, null);
    } finally {
        mock.timers.reset();
        globalThis.createImageBitmap = bitmap;
        globalThis.document = doc;
    }
});

test('пределы формы совпадают с серверными', () => {
    // Расхождение читалось бы как «форма приняла, а сервер отказал», то есть
    // как поломка. Регулярки с \r?\n: на диске CRLF, и `\)\n` молча не нашёлся
    // бы — именно так падает wiki_space_features.test.mjs.
    const server = readFileSync(new URL('../parcels/photos.py', import.meta.url), 'utf8');

    const types = server.match(/PHOTO_TYPES = \(([\s\S]*?)\)\r?\n/);
    assert.ok(types, 'PHOTO_TYPES не найден в parcels/photos.py');
    const serverTypes = [...types[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
    assert.deepEqual(serverTypes.sort(), [...PHOTO_TYPES].sort());

    // String.raw обязателен: в обычной шаблонной строке `\b` — это символ
    // забоя, а `\*` теряет экранирование и становится квантификатором.
    assert.match(server, new RegExp(String.raw`MAX_PER_PARCEL = ${PHOTO_MAX_COUNT}\b`));
    assert.match(server, new RegExp(
        String.raw`MAX_BYTES = ${PHOTO_MAX_BYTES / (1024 * 1024)} \* 1024 \* 1024\b`));
});
