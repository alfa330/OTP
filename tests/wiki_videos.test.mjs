import assert from 'node:assert/strict';
import test from 'node:test';

import {
    MAX_VIDEO_BYTES, VIDEO_ACCEPT, checkVideoFile, extensionOf, looksLikeVideo, titleFromName,
    videoAddress, videoBox, videoBoxCss, videoFileId, wikiVideoEndpoints,
} from '../src/components/wiki/videoRules.js';
import { absolutizeFileUrls, relativizeFileUrls } from '../src/components/wiki/fileUrls.js';
import { groupByType, typeBadge, typePlural } from '../src/components/wiki/articleTypes.js';

const file = (name, size = 1024, type = '') => ({ name, size, type });
const VIDEO = '0b8f6c2e-1111-4222-8333-944455556666';
const POSTER = '0b8f6c2e-1111-4222-8333-944455556667';

test('ролики, которые браузер сыграет, проходят — по расширению, а не по типу', () => {
    for (const name of ['a.mp4', 'b.M4V', 'IMG_0042.MOV', 'c.webm']) {
        assert.equal(checkVideoFile(file(name)).ok, true, name);
    }
    // macOS отдаёт для .mov пустой тип — это всё равно ролик.
    assert.equal(looksLikeVideo(file('IMG_0042.MOV', 10, '')), true);
    assert.equal(looksLikeVideo(file('скрин.png', 10, 'image/png')), false);
});

test('отказы — те же слова, что у сервера', () => {
    assert.match(checkVideoFile(file('camera.mkv')).error, /MP4/);
    assert.equal(checkVideoFile(file('Регламент.pdf')).error, 'Это не ролик: подходят MP4, MOV и WebM');
    assert.equal(checkVideoFile(file('a.mp4', 0)).error, 'Файл пустой');
    assert.equal(checkVideoFile(file('a.mp4', MAX_VIDEO_BYTES + 1)).error, 'Ролик больше 2 ГБ');
    assert.equal(checkVideoFile(file('a.mp4', MAX_VIDEO_BYTES)).ok, true);
    // MKV — ролик, просто не тот: перетаскивание обязано его узнать и отказать словами.
    assert.equal(looksLikeVideo(file('camera.mkv')), true);
});

test('окно выбора видит и расширения, и типы', () => {
    for (const piece of ['.mp4', '.mov', '.webm', 'video/quicktime']) {
        assert.ok(VIDEO_ACCEPT.split(',').includes(piece), piece);
    }
    assert.equal(extensionOf('папка.v2/Ролик.Final.MP4'), 'mp4');
    assert.equal(extensionOf('без_расширения'), '');
});

test('название статьи из имени файла — как на сервере', () => {
    assert.equal(titleFromName('Как_принять  заказ 18.11.mp4'), 'Как принять заказ 18.11');
    assert.equal(titleFromName('ролик'), 'ролик');
});

test('рамка ролика держит пропорцию и не выше 72 % экрана — и вертикальная, и горизонтальная', () => {
    assert.deepEqual(videoBox(1920, 1080),
        { aspectRatio: '1920 / 1080', width: 'min(100%, calc(72vh * 1920 / 1080))' });
    assert.deepEqual(videoBox(),
        { aspectRatio: '16 / 9', width: 'min(100%, calc(72vh * 16 / 9))' });
    const vertical = videoBox(1080, 1920);
    assert.equal(vertical.aspectRatio, '1080 / 1920');
    assert.equal(vertical.width, 'min(100%, calc(72vh * 1080 / 1920))');
    assert.equal(videoBoxCss(1080, 1920),
        'aspect-ratio: 1080 / 1920; width: min(100%, calc(72vh * 1080 / 1920))');
    assert.equal(videoBoxCss('мусор', null),
        'aspect-ratio: 16 / 9; width: min(100%, calc(72vh * 16 / 9))');
});

test('ручки вики для общей очереди', () => {
    const urls = wikiVideoEndpoints('https://api.example/api/wiki');
    assert.equal(urls.start, 'https://api.example/api/wiki/videos');
    assert.equal(urls.state('x'), 'https://api.example/api/wiki/videos/x');
    assert.equal(urls.poster('x'), 'https://api.example/api/wiki/videos/x/poster');
    assert.equal(urls.complete('x'), 'https://api.example/api/wiki/videos/x/complete');
    assert.equal(urls.cancel('x'), 'https://api.example/api/wiki/videos/x');
});

test('обложка ролика разворачивается и сворачивается так же, как его адрес', () => {
    const stored = `<video src="/api/wiki/file/${VIDEO}" poster="/api/wiki/file/${POSTER}" controls=""></video>`;
    const base = 'https://api.example/api/wiki';
    const shown = absolutizeFileUrls(stored, base);
    assert.ok(shown.includes(`poster="https://api.example/api/wiki/file/${POSTER}"`));
    assert.ok(shown.includes(`src="https://api.example/api/wiki/file/${VIDEO}"`));
    // Круг «показали → сохранили» возвращает ровно то, что лежало в базе.
    assert.equal(relativizeFileUrls(shown), stored);
    // На витрине — подписи хранилища, и для обложки тоже.
    const signed = absolutizeFileUrls(stored, base, {
        [VIDEO]: 'https://storage.example/v?a=1&b=2',
        [POSTER]: 'https://storage.example/p?a=1',
    });
    assert.ok(signed.includes('src="https://storage.example/v?a=1&amp;b=2"'));
    assert.ok(signed.includes('poster="https://storage.example/p?a=1"'));
});

test('тип «Ролик»: подпись в списках и своя подборка в оглавлении', () => {
    assert.equal(typeBadge('video')?.label, 'Ролик');
    assert.equal(typePlural('video'), 'Ролики');
    const groups = groupByType([
        { id: 1, article_type: 'general' },
        { id: 2, article_type: 'video' },
        { id: 3, article_type: 'regulation' },
    ]);
    assert.deepEqual(groups.map((group) => group.key), ['regulation', 'video', 'general']);
    // Ни одна статья не потерялась при раскладке.
    assert.equal(groups.reduce((sum, group) => sum + group.items.length, 0), 3);
});

test('адрес ролика в узле: свой — как есть, со страницы читателя — по номеру, чужой — нет', () => {
    const own = `/api/wiki/file/${VIDEO}`;
    assert.equal(videoAddress(own), own);
    assert.equal(videoAddress(`https://api.example/api/wiki/file/${VIDEO}`),
        `https://api.example/api/wiki/file/${VIDEO}`);
    // Скопировано со страницы: подпись хранилища, номер — в data-file-id.
    assert.equal(videoAddress('https://storage.googleapis.com/b/wiki/videos/x.mp4?X-Goog-Signature=1',
        VIDEO.toUpperCase()), own);
    assert.equal(videoAddress('https://evil.example/a.mp4', null), null);
    assert.equal(videoAddress('', 'не-номер'), null);
    assert.equal(videoFileId(`https://api.example/api/wiki/file/${VIDEO}`), VIDEO);
    assert.equal(videoFileId('https://evil.example/a.mp4'), null);
});
