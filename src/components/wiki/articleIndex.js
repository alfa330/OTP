/* Оглавление витрины — ВЕСЬ периметр статей, а не первая страница списка.
 *
 * Сервер отдаёт /articles страницами: потолок — 200 записей на ответ
 * (wiki/routes_articles.py), и на 292 статьях пространства «Таксопарки» одной
 * страницы перестало хватать. 92 статьи молча не доезжали до правой колонки, и
 * выглядело это не как «список короткий», а как ложь: раздел «Общий сотрудник»
 * показывал «(29)» и раскрывался пустым.
 *
 * Страницы добираем не цепочкой, а разом: сколько их всего, сервер сказал уже
 * первым ответом (total_visible — размер периметра), поэтому оглавление стоит
 * двух обращений при любом объёме базы, а не N. Цепочка из N ответов по 200
 * растянула бы правую колонку на секунды ровно тогда, когда база вырастет.
 */

// Потолок одного ответа /articles. Держим его здесь числом, а не «сколько
// придёт»: по нему считается, полна ли страница, то есть есть ли продолжение.
export const ARTICLE_PAGE = 200;

// 5000 статей. Не бизнес-ограничение, а страховка от бесконечного цикла, если
// сервер однажды начнёт отдавать полную страницу на любой offset.
export const ARTICLE_MAX_PAGES = 25;

/* fetchPage(offset, limit) → { items, total }
 *
 * total — размер периметра из ответа сервера (total_visible). Для оглавления он
 * точен: оглавление спрашивает список БЕЗ фильтров, а total_visible считается
 * до них. Спрашивающему с фильтром (раздел, корзина) верить ему нельзя — он
 * завысит число страниц.
 */
export async function fetchArticleIndex(fetchPage, {
    pageSize = ARTICLE_PAGE, maxPages = ARTICLE_MAX_PAGES,
} = {}) {
    const seen = new Set();
    const rows = [];

    /* Страницы приходят разными запросами, и между ними статью могли поправить:
       список отсортирован по updated_at, поэтому правленая уезжает в начало, а
       соседняя переползает через границу страницы и приходит дважды. Ключ
       строки оглавления — id статьи, так что дубль стоил бы и предупреждения
       React, и второй одинаковой строки в дереве. */
    const take = (page) => {
        const items = (page && page.items) || [];
        items.forEach((article) => {
            if (!article || seen.has(article.id)) return;
            seen.add(article.id);
            rows.push(article);
        });
        return items.length;
    };

    const first = await fetchPage(0, pageSize);
    let last = take(first);
    if (last < pageSize) return rows;

    // Сколько страниц обещал сервер. Берём их параллельно — они независимы.
    const total = Number(first && first.total) || 0;
    const planned = Math.min(Math.ceil(total / pageSize) || 1, maxPages);
    let offset = pageSize;

    if (planned > 1) {
        const offsets = [];
        for (let page = 1; page < planned; page += 1) offsets.push(page * pageSize);
        const pages = await Promise.all(offsets.map((at) => fetchPage(at, pageSize)));
        pages.forEach((page) => { last = take(page); });
        offset = planned * pageSize;
    }

    /* Хвост. В норме не нужен: сервер назвал размер периметра, и он сошёлся.
       Нужен, если total занижен или его нет вовсе — без него оглавление снова
       обрезалось бы МОЛЧА, а это ровно тот дефект, ради которого файл и
       появился. Цена страховки — один пустой ответ, когда статей ровно кратно
       странице. */
    while (last === pageSize && offset < maxPages * pageSize) {
        // eslint-disable-next-line no-await-in-loop
        last = take(await fetchPage(offset, pageSize));
        offset += pageSize;
    }
    return rows;
}
