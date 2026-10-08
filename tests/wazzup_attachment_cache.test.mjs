import test from 'node:test';
import assert from 'node:assert/strict';

import {
    ATTACHMENT_CACHE_LIMITS, attachmentCache, createAttachmentCache,
} from '../src/components/wazzup/attachmentCache.js';

/* Временный кэш просмотра вложений: файлы и распознанный текст живут в памяти
   вкладки, пока ими пользуются, и не достаются следующему человеку. */

const MINUTE = 60 * 1000;
const file = (size, type = 'image/jpeg') => ({ size, type });

function harness(limits) {
    let time = 1_000_000;
    const cache = createAttachmentCache(limits, () => time);
    return { cache, advance: (ms) => { time += ms; } };
}

test('файл и текст возвращаются по своему ключу, чужой ключ пуст', () => {
    const { cache } = harness();
    const photo = file(1000);
    cache.putMedia('channel:chat:one', photo);
    cache.putText('channel:chat:one', 2, { text: 'Вторая страница', source: 'ocr', page: 2 });
    assert.equal(cache.getMedia('channel:chat:one'), photo);
    assert.equal(cache.getMedia('channel:chat:two'), null);
    assert.equal(cache.getText('channel:chat:one', 2).text, 'Вторая страница');
    assert.equal(cache.getText('channel:chat:one', 1), null, 'у каждой страницы свой текст');
    assert.equal(cache.getText('channel:chat:two', 2), null);
});

test('страница 1 сообщения «a:1» и страница 11 сообщения «a» не сталкиваются', () => {
    const { cache } = harness();
    cache.putText('a', 11, { text: 'одиннадцатая' });
    cache.putText('a:1', 1, { text: 'первая' });
    assert.equal(cache.getText('a', 11).text, 'одиннадцатая');
    assert.equal(cache.getText('a:1', 1).text, 'первая');
    assert.equal(cache.getText('a:11', undefined), null);
});

test('пустой результат распознавания — тоже результат, а не промах', () => {
    const { cache } = harness();
    cache.putText('key', 1, { text: '', source: 'ocr', page: 1 });
    assert.deepEqual(cache.getText('key', 1), { text: '', source: 'ocr', page: 1 });
});

test('файл живёт час с последнего обращения, текст — смену', () => {
    const { cache, advance } = harness();
    cache.putMedia('key', file(1000));
    cache.putText('key', 1, { text: 'Текст' });
    advance(50 * MINUTE);
    assert.ok(cache.getMedia('key'), 'обращение продлевает жизнь файла');
    advance(50 * MINUTE);
    assert.ok(cache.getMedia('key'));
    advance(61 * MINUTE);
    assert.equal(cache.getMedia('key'), null, 'час без обращений — файла больше нет');
    assert.equal(cache.stats().mediaItems, 0, 'и память он не держит');
    assert.ok(cache.getText('key', 1), 'текст переживает файл: распознавание платное');
    advance(ATTACHMENT_CACHE_LIMITS.textTtlMs + MINUTE);
    assert.equal(cache.getText('key', 1), null);
});

test('подсмотреть запись можно без продления её жизни', () => {
    const { cache, advance } = harness();
    cache.putMedia('key', file(1000));
    advance(40 * MINUTE);
    assert.ok(cache.peekMedia('key'));
    advance(30 * MINUTE);
    assert.equal(cache.peekMedia('key'), null, 'подсмотр жизнь не продлил');
    assert.equal(cache.getMedia('key'), null);
});

test('кэш ограничен объёмом: вытесняется то, к чему давно не возвращались', () => {
    const { cache } = harness({ mediaMaxBytes: 3000, mediaMaxItems: 10 });
    cache.putMedia('a', file(1000));
    cache.putMedia('b', file(1000));
    cache.putMedia('c', file(1000));
    cache.getMedia('a');                       // «a» снова нужен — он остаётся
    cache.putMedia('d', file(1000));
    assert.deepEqual(['a', 'b', 'c', 'd'].map((key) => Boolean(cache.getMedia(key))),
        [true, false, true, true]);
    assert.equal(cache.stats().mediaBytes, 3000);
});

test('кэш ограничен числом записей', () => {
    const { cache } = harness({ mediaMaxItems: 2, textMaxItems: 2 });
    for (const key of ['a', 'b', 'c']) {
        cache.putMedia(key, file(10));
        cache.putText(key, 1, { text: key });
    }
    assert.deepEqual(cache.stats(), { mediaItems: 2, mediaBytes: 20, textItems: 2 });
    assert.equal(cache.getMedia('a'), null);
    assert.equal(cache.getText('a', 1), null);
});

test('файл больше всего кэша не кладётся и ничего не вытесняет', () => {
    const { cache } = harness({ mediaMaxBytes: 1500 });
    cache.putMedia('small', file(1000));
    cache.putMedia('huge', file(2000));
    assert.ok(cache.getMedia('small'));
    assert.equal(cache.getMedia('huge'), null);
});

test('пустой файл и пустое значение не кладутся', () => {
    const { cache } = harness();
    cache.putMedia('empty', file(0));
    cache.putMedia('nothing', null);
    assert.equal(cache.stats().mediaItems, 0);
});

test('повторная запись заменяет прежнюю и не задваивает объём', () => {
    const { cache } = harness();
    cache.putMedia('key', file(1000));
    cache.putMedia('key', file(400));
    assert.deepEqual(cache.stats(), { mediaItems: 1, mediaBytes: 400, textItems: 0 });
    cache.putText('key', 1, { text: 'первая попытка' });
    cache.putText('key', 1, { text: 'вторая попытка' });
    assert.equal(cache.getText('key', 1).text, 'вторая попытка');
    cache.dropText('key', 1);
    assert.equal(cache.getText('key', 1), null);
});

test('другой человек в той же вкладке чужих документов не наследует', () => {
    const { cache } = harness();
    cache.setOwner(241);
    cache.putMedia('key', file(1000));
    cache.putText('key', 1, { text: 'Паспорт' });
    cache.setOwner('241');                     // тот же человек, id пришёл строкой
    assert.ok(cache.getMedia('key'));
    cache.setOwner(292);
    assert.equal(cache.getMedia('key'), null);
    assert.equal(cache.getText('key', 1), null);
    // Выход из портала (человека нет) — тоже смена владельца.
    cache.putMedia('key', file(1000));
    cache.setOwner(null);
    assert.equal(cache.getMedia('key'), null);
});

test('первое назначение владельца ничего не сбрасывает', () => {
    const { cache } = harness();
    cache.putMedia('key', file(1000));
    cache.setOwner(241);
    assert.ok(cache.getMedia('key'));
});

test('на вкладку один общий кэш с рабочими пределами', () => {
    assert.equal(typeof attachmentCache.getMedia, 'function');
    assert.ok(ATTACHMENT_CACHE_LIMITS.mediaMaxBytes >= 20 * 1024 * 1024 * 4,
        'в кэш обязано помещаться несколько файлов предельного размера');
    assert.ok(ATTACHMENT_CACHE_LIMITS.textTtlMs > ATTACHMENT_CACHE_LIMITS.mediaTtlMs);
});
