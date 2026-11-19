// «Дисциплина» (#254): фото HEIC с iPhone — что считается HEIC, как зовётся
// JPEG-копия и до какого размера она ужимается. Сам перевод (canvas) в node не
// запустить — без DOM heicToJpeg честно отвечает null, и файл уходит как есть.
// Запуск: node --test tests/discipline_heic.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    fitSide, HEIC_MAX_SIDE, heicToJpeg, isHeic, jpegName, prepareUploads,
} from '../src/components/discipline/heicImage.js';

test('HEIC узнаётся по типу, а без типа — по расширению', () => {
    assert.equal(isHeic({ type: 'image/heic', name: 'IMG_0001.HEIC' }), true);
    assert.equal(isHeic({ type: 'image/heif', name: 'x' }), true);
    // Файл из Windows без типа и из карточки сервера (content_type).
    assert.equal(isHeic({ type: '', name: 'Объяснительная.heic' }), true);
    assert.equal(isHeic({ type: 'application/octet-stream', name: 'scan.HEIF' }), true);
    assert.equal(isHeic({ content_type: 'image/heic', name: 'photo' }), true);
    // Тип говорит другое — расширению не верим.
    assert.equal(isHeic({ type: 'image/jpeg', name: 'IMG_0001.heic' }), false);
    assert.equal(isHeic({ type: 'application/pdf', name: 'акт.pdf' }), false);
    assert.equal(isHeic(null), false);
});

test('JPEG-копия зовётся как исходник, но .jpg', () => {
    assert.equal(jpegName('IMG_0001.HEIC'), 'IMG_0001.jpg');
    assert.equal(jpegName('Объяснительная Иванова.heif'), 'Объяснительная Иванова.jpg');
    assert.equal(jpegName('C:\\Фото\\скан.heic'), 'скан.jpg');
    assert.equal(jpegName(''), 'photo.jpg');
});

test('12 Мп проходят как есть, 48 Мп ужимаются до предела стороны', () => {
    assert.deepEqual(fitSide(4032, 3024), { width: 4032, height: 3024 });
    const big = fitSide(8064, 6048);
    assert.equal(big.width, HEIC_MAX_SIDE);
    assert.equal(big.height, Math.round(6048 * (HEIC_MAX_SIDE / 8064)));
    assert.deepEqual(fitSide(3024, 8064).height, HEIC_MAX_SIDE);
    assert.deepEqual(fitSide(0, 0), { width: 0, height: 0 });
});

test('без браузера перевода нет — файлы уходят как были и в том же порядке', async () => {
    assert.equal(await heicToJpeg(new Blob(['x'], { type: 'image/heic' })), null);
    const files = [{ type: 'image/heic', name: 'a.heic' }, { type: 'application/pdf', name: 'b.pdf' }];
    assert.deepEqual(await prepareUploads(files), files);
});
