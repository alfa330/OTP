import test from 'node:test';
import assert from 'node:assert/strict';

import {
    ARTICLE_PAGE, fetchArticleIndex,
} from '../src/components/wiki/articleIndex.js';

/**
 * Сборка оглавления витрины вики из страниц /articles.
 *
 * Дефект, ради которого это появилось: оглавление запрашивало ОДНУ страницу с
 * limit=200, а в пространстве «Таксопарки» статей стало 292. Девяносто две
 * статьи не доезжали до правой колонки, и раздел «Общий сотрудник» показывал
 * «(29)», раскрываясь пустым. Сторожим здесь три вещи: хвост забирается,
 * запросов на это уходит два, а не N, и молча обрезать список нельзя даже
 * тогда, когда сервер соврал про размер периметра.
 */

// Ленты статей и сервер над ней: отдаёт страницу и общий размер периметра,
// как настоящий /articles (items + total_visible).
const feed = (count) => Array.from({ length: count }, (_, i) => ({ id: i + 1, title: `Статья ${i + 1}` }));

const server = (rows, { total = rows.length, pageSize = ARTICLE_PAGE } = {}) => {
    const calls = [];
    const fetchPage = async (offset, limit) => {
        calls.push({ offset, limit });
        return { items: rows.slice(offset, offset + (limit || pageSize)), total };
    };
    return { fetchPage, calls };
};

test('одна короткая страница — один запрос', async () => {
    const { fetchPage, calls } = server(feed(36));
    const rows = await fetchArticleIndex(fetchPage);
    assert.equal(rows.length, 36);
    assert.equal(calls.length, 1);
});

test('292 статьи с боя доезжают целиком и за два обращения', async () => {
    const { fetchPage, calls } = server(feed(292));
    const rows = await fetchArticleIndex(fetchPage);
    assert.equal(rows.length, 292, 'ровно столько статей в пространстве «Таксопарки»');
    assert.deepEqual(rows.map((a) => a.id).slice(-3), [290, 291, 292], 'хвост списка на месте');
    assert.equal(calls.length, 2, 'вторая страница берётся сразу, а не цепочкой');
    assert.deepEqual(calls.map((c) => c.offset), [0, 200]);
});

test('страницы за первой идут параллельно, а не по очереди', async () => {
    const rows = feed(1000);
    let live = 0;
    let peak = 0;
    const fetchPage = async (offset, limit) => {
        live += 1;
        peak = Math.max(peak, live);
        await new Promise((resolve) => { setTimeout(resolve, 5); });
        live -= 1;
        return { items: rows.slice(offset, offset + limit), total: rows.length };
    };
    const got = await fetchArticleIndex(fetchPage);
    assert.equal(got.length, 1000);
    assert.ok(peak > 1, `страницы 2–5 обязаны идти разом, а шли по одной (пик ${peak})`);
});

test('сервер занизил размер периметра — оглавление всё равно полное', async () => {
    // total из ответа — подсказка, а не приговор: пока страница приходит
    // полной, есть продолжение. Иначе вернулся бы ровно тот же молчаливый
    // обрез, из-за которого всё и затевалось.
    const { fetchPage } = server(feed(450), { total: 200 });
    const rows = await fetchArticleIndex(fetchPage);
    assert.equal(rows.length, 450);
});

test('статья, переехавшая между страницами, не двоится', async () => {
    // Между запросами страниц статью поправили: список отсортирован по
    // updated_at, правленая уехала в начало, и соседняя пришла дважды.
    const pages = [
        { items: feed(200), total: 260 },
        { items: [{ id: 200, title: 'Статья 200' }, ...feed(260).slice(200)], total: 260 },
    ];
    let call = 0;
    const rows = await fetchArticleIndex(async () => pages[call++] || { items: [], total: 260 });
    assert.equal(rows.length, 260);
    assert.equal(new Set(rows.map((a) => a.id)).size, 260, 'дублей по id быть не должно');
});

test('потолок страниц не даёт зациклиться на бесконечном сервере', async () => {
    let call = 0;
    const rows = await fetchArticleIndex(async (offset, limit) => {
        call += 1;
        return { items: feed(limit).map((a) => ({ ...a, id: offset + a.id })), total: 0 };
    }, { pageSize: 10, maxPages: 4 });
    assert.equal(rows.length, 40);
    assert.equal(call, 4);
});

test('пустой периметр — пустое оглавление', async () => {
    const { fetchPage, calls } = server([]);
    assert.deepEqual(await fetchArticleIndex(fetchPage), []);
    assert.equal(calls.length, 1);
});
