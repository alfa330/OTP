/*
 * Первый кадр раздела «Вики» — что стоит на экране, пока сервер не ответил.
 *
 * Жалоба владельца 13.10.2026: «при входе в раздел вики имеется небольшой
 * пролаг, сначала выходят некоторые разделы, пролаг и разделы, которые
 * имеются». Замер на стенде показал, что именно менялось: до ответа /ping ряд
 * вкладок собирался «по умолчанию» (семь вкладок — без «Статей», «Вопросов»,
 * «Аналитики» и «Журнала», зато с выключенными в пространстве), обложка
 * подписывалась как у читателя, а витрина уходила за оглавлением и подборками
 * БЕЗ пространства. На ответе вкладок становилось одиннадцать, экран прыгал на
 * 140 px, в шапку приезжали кнопки, а оглавление и подборки запрашивались
 * второй раз — три лишних запроса на каждый вход.
 *
 * Теперь первый вход ждёт ответа под заготовкой, а второй и следующие сразу
 * рисуют то, что сервер ответил в прошлый раз (entryMemory.js).
 *
 * Компонент здесь НАСТОЯЩИЙ и рисуется настоящим React (react-dom/server).
 * Эффектов серверный рендер не запускает — раздел остаётся ровно в том
 * состоянии, в каком человек видит его в первый кадр после входа: запросы ещё
 * не ушли. Что происходит после ответов — в tests/wiki_entry_after_answer.test.mjs.
 *
 * Запуск: node --test tests/wiki_entry_first_frame.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    isRefusal, keepUnchanged, memoryKey, recall, remember,
} from '../src/components/wiki/entryMemory.js';
import {
    BASE, HEADER_HIDDEN, HR_SPACE, PARK, READER, SKELETON, TABS_SKELETON,
    firstFrame, libraryProps, loadSection, parksAnswer, pingAnswer, read, sectionProps,
    storage, tabLabels, withAddress,
} from './wiki_entry_lab.mjs';

const { WikiView, WikiLibrary, memory } = await loadSection();

const renderSection = (user, extra) => firstFrame(WikiView, sectionProps(user, extra));
const renderLibrary = (over) => firstFrame(WikiLibrary, libraryProps(over));

const rememberPing = (user, over) => memory.remember(memory.memoryKey(BASE, user.id, 'ping'), pingAnswer(over));
const rememberParks = (userId, space, items, over) => memory.remember(
    memory.memoryKey(BASE, userId, 'parks', space), parksAnswer(space, items, over));

/* ── Первый вход: ответа ещё нет ─────────────────────────────────────────── */

test('до ответа сервера в ряду вкладок нет ни одной вкладки «по умолчанию»', () => {
    const html = renderSection({ id: 901 });

    assert.deepEqual(tabLabels(html), [],
        'вкладки нарисованы раньше, чем сервер назвал права и пространство');
    for (const label of ['Помощник', 'Новости', 'Обзор', 'Парки', 'Офисы', 'Города']) {
        assert.ok(!html.includes(`>${label}<`) && !html.includes(` ${label}</button>`),
            `«${label}» показана до ответа: у человека её может не быть вовсе`);
    }
});

test('первый кадр — заготовка в свой рост, а не витрина без пространства', () => {
    const html = renderSection({ id: 902 });

    // Заготовка названа читалке один раз.
    assert.equal((html.match(/aria-busy="true"/g) || []).length, 1);
    assert.match(html, /role="status"[^>]*aria-label="Загружаем вики"|aria-label="Загружаем вики"[^>]*role="status"/);
    // Обложка зависит от прав («Режим редактора») — раньше ответа её нет.
    assert.ok(!html.includes('Все статьи вики'), 'обложка витрины нарисована до ответа');
    assert.ok(!html.includes('База знаний компании</span>') && !html.includes('Режим редактора'),
        'подпись обложки выбрана раньше, чем известны права');
    // Оглавление не утверждает «0 статей» и «Статей здесь пока нет» до ответа.
    assert.ok(!html.includes('Разделы и статьи'), 'оглавление нарисовано до ответа');
    // Место ряда вкладок занято — вкладки встанут без сдвига содержимого.
    assert.match(html, TABS_SKELETON);
});

test('место ряда вкладок держит НАСТОЯЩАЯ разметка ряда — её рост считают те же правила', () => {
    /* Телефонный слой отбирает ряд вкладок по классам подложки и задаёт высоту
       её кнопкам. Заготовка с другой разметкой была на телефоне выше ряда на
       6,5 px — и содержимое прыгало, когда вкладки вставали. */
    const html = renderSection({ id: 903 });
    assert.match(html, /<div aria-hidden="true" class="flex gap-1 overflow-x-auto rounded-2xl bg-slate-100 p-1"><button type="button" disabled="" tabindex="-1" class="invisible flex shrink-0 items-center gap-1\.5 rounded-xl px-3\.5 py-2 text-\[13px\] font-medium">/);
    const layer = read('src/components/wiki/wiki-mobile.css');
    assert.match(layer, /\.wiki-scope > div > div\[class~="overflow-x-auto"\]\[class~="bg-slate-100"\]/);
});

test('кнопки шапки до ответа держат место, но не видны', () => {
    const html = renderSection({ id: 904 });
    // Редактору в группу приедут ещё две кнопки — показанные раньше них поиск
    // и «Обновить» на глазах уехали бы влево.
    assert.match(html, HEADER_HIDDEN);
    assert.ok(!html.includes('Новая статья'));
});

/* ── Следующие входы: стоит прошлый ответ ────────────────────────────────── */

test('второй вход рисует настоящие вкладки с первого кадра', () => {
    const user = { id: 905 };
    rememberPing(user);
    const html = renderSection(user);

    assert.deepEqual(tabLabels(html), [
        'Главная', 'Помощник', 'Вопросы', 'Статьи', 'Новости', 'Обзор',
        'Парки', 'Офисы', 'Города', 'Аналитика', 'Журнал',
    ]);
    // Шапка — сразу целиком: переключатель вики и кнопки редактора.
    assert.ok(html.includes('Таксопарки'));
    assert.ok(html.includes('Новая статья'));
    assert.doesNotMatch(html, HEADER_HIDDEN);
    assert.doesNotMatch(html, TABS_SKELETON, 'пустая подложка стоит рядом с настоящими вкладками');
});

test('вкладки второго входа — по тумблерам и правам из ответа, а не «всё включено»', () => {
    const user = { id: 906 };
    rememberPing(user, { ...READER, spaces: [HR_SPACE] });
    storage.set('wiki:space', '12');
    const html = renderSection(user);
    storage.delete('wiki:space');

    assert.deepEqual(tabLabels(html), ['Главная', 'Новости']);
    // Рельса в этом пространстве нет — ждать ответа о парках незачем, и витрина
    // стоит с первого кадра: обложка читателя и оглавление.
    assert.ok(html.includes('Все статьи вики'));
    assert.ok(html.includes('База знаний компании</span>'));
    assert.ok(!html.includes('Режим редактора'));
});

test('ответ запомнен за учёткой: под другим логином — снова заготовка', () => {
    rememberPing({ id: 907 });
    const html = renderSection({ id: 908 });
    assert.deepEqual(tabLabels(html), []);
    assert.match(html, SKELETON);
});

test('второй вход: рельс парков стоит в первом кадре РАЗДЕЛА, а не только витрины', () => {
    /* Память парков ключуется учёткой, а её витрине передаёт раздел: без
       пропа userId рельс не запоминается вовсе и въезжает на каждом входе. */
    const user = { id: 909 };
    rememberPing(user);
    rememberParks(user.id, 1, [PARK]);
    const html = renderSection(user);

    assert.ok(html.includes('aria-label="Тест Такси"'), 'рельс не нарисован из памяти входа');
    assert.ok(html.includes('Все статьи вики'));
    assert.doesNotMatch(html, SKELETON);
});

/* ── Переходы в вики извне: колокол и шарик помощника ────────────────────── */

/* Серверный рендер показывает кадр ДО эффектов — тот самый, с которого раздел
   уходит на нужную вкладку. Раньше на нём стояла главная со всеми её запросами
   (при переходе из шарика — полсекунды, до ответа /ping). Теперь на этом кадре
   витрина не рисует ничего и за данными не ходит. */
const HOME_MARKS = ['Все статьи вики', 'Разделы и статьи', 'Загружаем вики'];

test('переход из колокола не рисует главную, с которой сейчас уйдут', () => {
    const user = { id: 920 };
    rememberPing(user);
    rememberParks(user.id, 1, []);
    const html = renderSection(user, { bellFocus: { kind: 'assistant', id: 5, nonce: 1 } });

    assert.ok(tabLabels(html).includes('Помощник'));
    for (const mark of HOME_MARKS) assert.ok(!html.includes(mark), `на кадре перед переходом стоит «${mark}»`);
});

test('просьба колокола на вкладку, которой у человека нет, витрину не держит', () => {
    /* Такая просьба может не исполниться никогда — оставь она витрину пустой,
       человек без «Вопросов» не увидел бы главную вовсе. */
    const user = { id: 921 };
    rememberPing(user, { grant_ceiling: null });   // «Вопросы» — тому, кто вправе публиковать
    rememberParks(user.id, 1, []);
    const html = renderSection(user, { bellFocus: { kind: 'questions', id: 3, nonce: 1 } });

    assert.ok(!tabLabels(html).includes('Вопросы'));
    assert.ok(html.includes('Все статьи вики'), 'витрина осталась пустой из-за неисполнимой просьбы');
});

test('переход из шарика помощника не рисует главную, с которой сейчас уйдут', () => {
    const user = { id: 922 };
    rememberPing(user);
    rememberParks(user.id, 1, []);
    for (const tab of ['offices', 'cities', 'classifier']) {
        const html = renderSection(user, { directoryFocus: { tab, space_id: 1, nonce: 1 } });
        for (const mark of HOME_MARKS) assert.ok(!html.includes(mark), `${tab}: на кадре перед переходом стоит «${mark}»`);
    }
});

/* Нейтральная заглушка раздела: один блок без формы главной. */
const NEUTRAL = /rounded-2xl[^"]*h-\[120px\]"><div class="sk-shimmer h-full w-full rounded-2xl">/;

test('без запомненного ответа переход извне ждёт под НЕЙТРАЛЬНОЙ заглушкой', async () => {
    /* Три серых блока в форме главной, на месте которых через полсекунды
       встают «Офисы» или чат помощника, — то же «сначала одно, потом другое».
       Пока неизвестно, где человек окажется, место держит блок без формы. */
    const arrivals = [
        ['колокол', {}, { bellFocus: { kind: 'assistant', id: 5, nonce: 1 } }],
        ['шарик помощника', {}, { directoryFocus: { tab: 'offices', space_id: 1, nonce: 1 } }],
    ];
    for (const [name, , extra] of arrivals) {
        const html = renderSection({ id: 923 }, extra);
        assert.deepEqual(tabLabels(html), [], name);
        assert.doesNotMatch(html, SKELETON, `${name}: стоит заготовка главной, а откроется не она`);
        assert.match(html, NEUTRAL, name);
    }
    // Перезагрузка страницы на вкладке, отличной от главной.
    await withAddress('?view=wiki&tab=offices', () => {
        const html = renderSection({ id: 924 });
        assert.doesNotMatch(html, SKELETON, 'ссылка на вкладку: стоит заготовка главной');
        assert.match(html, NEUTRAL);
    });
    // Обычный вход: заготовка главной, и только она.
    const plain = renderSection({ id: 925 });
    assert.match(plain, SKELETON);
    assert.doesNotMatch(plain, NEUTRAL);
});

/* ── Витрина: рельс парков и первый кадр ─────────────────────────────────── */

test('первый кадр витрины ждёт ответа о парках: от рельса зависит, где стоит центр', () => {
    const html = renderLibrary({ userId: 930 });
    assert.match(html, SKELETON);
    assert.ok(!html.includes('Все статьи вики'),
        'обложка встала раньше рельса и уедет вправо на его ширину');
});

test('на втором входе рельс парков стоит с первого кадра', () => {
    rememberParks(931, 1, [PARK]);
    const html = renderLibrary({ userId: 931 });

    assert.ok(html.includes('aria-label="Тест Такси"'), 'рельс не нарисован из запомненного ответа');
    assert.ok(html.includes('Все статьи вики'));
    assert.doesNotMatch(html, SKELETON);
});

test('парки запомнены за пространством: в каждом встаёт СВОЙ рельс', () => {
    rememberParks(932, 1, [PARK]);
    rememberParks(932, 9, [{ ...PARK, id: 6, slug: 'tz', name: 'Тез Парк' }]);
    const html = renderLibrary({ userId: 932, spaceId: 9 });
    assert.ok(html.includes('aria-label="Тез Парк"'), 'рельс своего пространства не встал из памяти');
    assert.ok(!html.includes('Тест Такси'), 'встал рельс соседнего пространства');
});

test('запомненный «рельса нет» открывает витрину с первого кадра', () => {
    /* Гостю и тому, кому справочник не выдан, сервер на /parks отвечает
       отказом — исход запроса известен заранее. Без запомненного «нет» такой
       человек на каждом входе ждал бы под заготовкой. */
    rememberParks(933, 1, []);
    const html = renderLibrary({ userId: 933 });
    assert.doesNotMatch(html, SKELETON);
    assert.ok(html.includes('Все статьи вики'));
    assert.ok(!html.includes('wiki-m-parks'), 'рельс нарисован, хотя парков нет');
});

test('логотипы из давнего ответа не рисуются: их подписанные адреса истекли', () => {
    /* Сервер отдаёт подпись, которой осталось жить не меньше получаса; портал
       держат открытым всю смену. Рельс из памяти встаёт, но плитка рисует
       буквы — битая картинка до свежего ответа была бы хуже. */
    const logo = 'https://storage.test/logo.png';
    rememberParks(934, 1, [{ ...PARK, logo_url: logo }]);
    const fresh = renderLibrary({ userId: 934 });
    assert.ok(fresh.includes(`src="${logo}"`), 'свежий логотип не нарисован');

    rememberParks(935, 1, [{ ...PARK, logo_url: logo }], { at: Date.now() - 26 * 60 * 1000 });
    const old = renderLibrary({ userId: 935 });
    assert.ok(old.includes('aria-label="Тест Такси"'), 'рельс из давнего ответа не встал вовсе');
    assert.ok(!old.includes('storage.test'), 'логотип с истёкшей подписью нарисован из памяти');
    // «Раздетый» ответ лежит в памяти одним объектом — каждый рендер получает его же.
    const key = memory.memoryKey(BASE, 935, 'parks', 1);
    const plain = memory.recall(key);
    assert.equal(plain.plain, true);
    renderLibrary({ userId: 935 });
    assert.equal(memory.recall(key), plain);
});

test('пока ответа /ping нет, витрина — заготовка; при отказе — ничего', () => {
    assert.match(renderLibrary({ ready: false, spaceId: null }), SKELETON);
    // Отказ показывает карточка раздела выше: заготовка под ней обещала бы то,
    // чего не будет.
    assert.equal(renderLibrary({ ready: false, unavailable: true, spaceId: null }), '');
});

test('витрина, с которой уходят, не успев её показать, не рисует ничего', () => {
    const features = { parks: false, library_park_rail: false };
    assert.equal(renderLibrary({ features, leaving: true }), '');
    // И до ответа тоже: её заготовка обещала бы главную, а откроется не она.
    assert.equal(renderLibrary({ features, ready: false, leaving: true, spaceId: null }), '');
});

test('на узких экранах оглавление не встаёт, пока грузятся подборки', () => {
    /* На телефоне и планшете оно стоит ПОД подборками: показанное сразу, с
       ответом улетало вниз на 600–700 px. На компьютере оно в своей колонке. */
    const html = renderLibrary({ features: { parks: false, library_park_rail: false } });
    assert.match(html, /<div class="hidden lg:contents"><aside class="wiki-m-index/);
});

test('заготовка обложки носит телефонную метку обложки', () => {
    /* На телефоне обложки нет (wiki-mobile.css прячет .wiki-m-hero): заготовка
       без метки стояла бы там блоком в 257 px и исчезала на ответе. */
    const html = renderLibrary({ ready: false, spaceId: null });
    assert.match(html, /class="[^"]*\bwiki-m-hero\b[^"]*h-\[257px\]/);
    assert.match(read('src/components/wiki/wiki-mobile.css'), /\.wiki-scope \.wiki-m-hero \{\s*display: none;/);
});

test('оглавление не называет число статей, пока грузится', () => {
    const features = { parks: false, library_park_rail: false };
    const loading = renderLibrary({ features });
    assert.ok(loading.includes('Разделы и статьи'));
    assert.doesNotMatch(loading, /bg-indigo-50 px-1\.5 py-0\.5 text-\[9\.5px\]/,
        '«0» в шапке оглавления до ответа — утверждение, которое через полсекунды сменится');
});

/* ── Память входа ────────────────────────────────────────────────────────── */

test('память входа: ключ без учётки пуст, и запоминать под ним нечего', () => {
    assert.equal(memoryKey(BASE, null, 'ping'), '');
    assert.equal(memoryKey(BASE, undefined, 'ping'), '');
    assert.equal(memoryKey(BASE, 22, 'ping'), `${BASE}|22|ping`);
    assert.equal(memoryKey(BASE, 22, 'parks', 9), `${BASE}|22|parks|9`);

    remember('', { leaked: true });
    assert.equal(recall(''), null);
});

test('память входа: ответ хранится по ключу и забывается по отказу', () => {
    const key = memoryKey(BASE, 913, 'ping');
    assert.equal(recall(key), null);
    const answer = { ok: true };
    remember(key, answer);
    assert.equal(recall(key), answer);
    assert.equal(recall(memoryKey(BASE, 914, 'ping')), null, 'ответ виден под чужой учёткой');
    remember(key, null);
    assert.equal(recall(key), null);
});

test('отказ — это ответ сервера с кодом 4xx; обрыв сети и 5xx — сбой', () => {
    for (const status of [401, 403, 404]) assert.equal(isRefusal({ response: { status } }), true, String(status));
    for (const status of [500, 502, 503]) assert.equal(isRefusal({ response: { status } }), false, String(status));
    assert.equal(isRefusal(new Error('Network Error')), false);
    assert.equal(isRefusal(null), false);
});

test('равный ответ возвращает ПРЕЖНИЕ объекты — перерисовывать нечего', () => {
    const prev = pingAnswer();
    const same = keepUnchanged(prev, JSON.parse(JSON.stringify(prev)));
    assert.equal(same, prev);
});

test('изменилась одна часть ответа — остальные остались прежними объектами', () => {
    const prev = pingAnswer();
    const fresh = JSON.parse(JSON.stringify(prev));
    fresh.counters.articles_published = 22;
    const next = keepUnchanged(prev, fresh);

    assert.notEqual(next, prev);
    assert.equal(next.counters.articles_published, 22);
    // Пространства не менялись — активное пространство, тумблеры и вкладки
    // считаются от этого объекта и обязаны остаться на месте.
    assert.equal(next.spaces, prev.spaces);
    assert.equal(next.capabilities, prev.capabilities);
    assert.notEqual(next.counters, prev.counters);
});

test('keepUnchanged видит добавленное, убранное и переставленное', () => {
    const prev = { list: [{ id: 1 }, { id: 2 }], flags: { a: true } };

    const grown = keepUnchanged(prev, { list: [{ id: 1 }, { id: 2 }, { id: 3 }], flags: { a: true } });
    assert.equal(grown.list.length, 3);
    assert.equal(grown.list[0], prev.list[0]);
    assert.equal(grown.flags, prev.flags);

    const shrunk = keepUnchanged(prev, { list: [{ id: 1 }], flags: { a: true } });
    assert.equal(shrunk.list.length, 1);
    assert.notEqual(shrunk.list, prev.list);

    const dropped = keepUnchanged(prev, { list: prev.list });
    assert.deepEqual(Object.keys(dropped), ['list']);
    assert.notEqual(dropped, prev);

    const renamed = keepUnchanged({ a: 1 }, { b: 1 });
    assert.deepEqual(renamed, { b: 1 });
    // Ключ, которого у прежнего ответа не было, — изменение, даже без значения.
    const added = keepUnchanged({ a: 1 }, { b: undefined });
    assert.notEqual(added, undefined);
    assert.deepEqual(Object.keys(added), ['b']);

    assert.equal(keepUnchanged(null, prev), prev);
    assert.equal(keepUnchanged(prev, null), null);
    assert.deepEqual(keepUnchanged([1, 2], { 0: 1, 1: 2 }), { 0: 1, 1: 2 });
});

/* ── То, у чего поведения нет, — только текст ────────────────────────────── */

test('память входа живёт только в памяти страницы', () => {
    /* В ответе — способности человека, его отделы и срок гостевого доступа:
       на общем компьютере смены они не должны переживать закрытие вкладки. */
    const code = (name) => read(`src/components/wiki/${name}`).replace(/\/\*[\s\S]*?\*\//g, '');
    assert.doesNotMatch(code('entryMemory.js'), /localStorage|sessionStorage|indexedDB|document\.cookie/);
    /* У самого раздела в localStorage лежит только выбранное пространство — id, который
       человек и так видит в адресе. Ответа /ping и парков там быть не должно. */
    const stored = (source) => [...source.matchAll(/(?:localStorage|sessionStorage)\.setItem\(([^,]+),/g)]
        .map((match) => match[1].trim());
    assert.deepEqual(stored(code('WikiView.jsx')), ["'wiki:space'"]);
    assert.deepEqual(stored(code('WikiLibrary.jsx')), []);
});

test('заглушка над вкладкой не встаёт на каждый запрос /ping', () => {
    /* Раньше она стояла под условием `loading` и на «Обновить» сдвигала всё
       содержимое на 140 px вниз и обратно. Теперь во время обновления крутится
       значок кнопки, а «Обновлено» говорится, только когда обновилось. */
    const view = read('src/components/wiki/WikiView.jsx');
    assert.doesNotMatch(view, /\{loading && \(\s*<div className=\{`\$\{iosCard\} h-\[120px\]`\}>/);
    assert.match(view, /\{!ready && loading && \(tab !== 'library' \|\| leavingLibrary\) && \(/);
    assert.match(view, /className=\{refreshing \? 'animate-spin motion-reduce:animate-none' : ''\}/);
    assert.match(view, /\.then\(\(\[pinged\]\) => \{ if \(pinged\) showToast\?\.\('Обновлено', 'success'\); \}\)/);
});

test('связки, которые видны только в браузере', () => {
    const view = read('src/components/wiki/WikiView.jsx');
    /* ⌘K до первого ответа: поле невидимо, фокус в него не встаёт — поиск
       оставался «раскрытым» без курсора и растягивал шапку на две строки. */
    assert.match(view, /asleep=\{!ready && !settled\}/);
    const search = read('src/components/wiki/WikiSearch.jsx');
    assert.match(search, /if \(asleep\) return;\s*e\.preventDefault\(\);/);
    assert.match(search, /\}, \[escapeStep, asleep\]\);/);
    /* До ответа /ping витрина получает пространство, выбранное в прошлый раз, и уходит
       за данными одновременно с ним (что она с ней делает — в соседнем файле:
       «пространство известно заранее…»). */
    assert.match(view, /spaceId=\{activeSpace\?\.id \|\| \(ready \? null : spaceId\) \|\| null\}/);
    /* «Каталог не грузится» — только по ответу /ping. До ответа редактором не
       выглядит никто; снятый тогда флаг давал редактору кадр полок без
       плиток-счётчиков, затем заглушку и снова полки (замер: 570 → 578 → 849 мс). */
    assert.match(view, /if \(!isEditor\) \{\s*setCatalog\(null\);[\s\S]{0,700}?if \(ready\) setCatalogLoading\(false\);\s*return Promise\.resolve\(\);/);
    assert.match(view, /\}, \[base, headers, isEditor, activeSpace, ready\]\);/);
    /* «Структура»: дерево, которое уже на экране, не гаснет на «Обновить» и
       после каждой правки. */
    assert.match(view, /loading=\{structureLoading && !structure\}\s*canManageAccess/);
    /* Половина «Статей» не переключается по пустым правам отказа: человек,
       вернувшись после «Попробовать снова», оказывался бы в «Структуре». */
    assert.match(view, /if \(ready && catalogModes\.length && !catalogModes\.includes\(catalogMode\)\) \{/);
    /* «С витрины уходят»: исполненное помнит сам раздел, и неснятая снаружи
       просьба не оставляет витрину пустой навсегда. */
    const leaving = view.slice(view.indexOf('const leavingLibrary ='), view.indexOf('const bodyTab'));
    assert.match(leaving, /bellDone !== bellFocus && \(!ready \|\| tabs\.some\(\(t\) => t\.key === bellTab\)\)/);
    assert.match(leaving, /directoryDone !== directoryFocus/);
    // Кнопка «Обновить»: знак работы есть и у тех, кому движение уменьшено.
    assert.match(view, /disabled=\{refreshing\}\s*aria-busy=\{refreshing\}/);
    assert.match(read('src/components/wiki/wiki-theme.css'),
        /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\.wiki-scope \.sk-shimmer \{\s*animation: none;/);
    /* Надпись «Загрузка Вики…» (пока едет код раздела) проявляется через треть
       секунды: обычно код в кеше, и надпись мелькала на кадр перед заготовкой. */
    const app = read('src/App.jsx');
    const fallback = app.slice(app.indexOf('Загрузка Вики…') - 220, app.indexOf('Загрузка Вики…'));
    assert.ok(fallback.includes('<Suspense fallback={<div className="flex min-h-[240px] items-center justify-center text-sm text-slate-500 motion-safe:animate-fade-in-soft motion-safe:![animation-delay:300ms]">'), fallback);
    // Телефон: строка заголовка заранее в рост переключателя вики.
    assert.match(read('src/components/wiki/wiki-mobile.css'),
        /header > \[role="button"\] \{[^}]*min-height: 36px;/);
});

test('переходы по адресу и из колокола исполняются до отрисовки', () => {
    /* useLayoutEffect: кадр «открыта главная», с которого тут же уходят, на
       экран не попадает. Шарик помощника — обычным эффектом: «Офисы», открытые
       синхронным рендером с просьбой показать запись, уходят за списком дважды
       (замер на стенде); кадр до перехода витрина при этом не рисует вовсе. */
    const view = read('src/components/wiki/WikiView.jsx');
    assert.match(view, /useLayoutEffect\(\(\) => \{\s*if \(!requestedTab\) return;/);
    assert.match(view, /useLayoutEffect\(\(\) => \{\s*const target = bellTabOf\(bellFocus\);/);
    assert.match(view, /useEffect\(\(\) => \{\s*if \(!directoryFocus\?\.tab \|\| directoryDone === directoryFocus\) return;/);
});
