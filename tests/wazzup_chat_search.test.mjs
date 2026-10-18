/*
 * Поиск «Чатов ОП» по тексту сообщений — клиент (chatSearch.js,
 * useMessageSearch.js, ChatMessageHits.jsx и проводка в WazzupChatsView.jsx).
 * Сервер — tests/test_wazzup_chat_search.py.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createSearchCache, highlightParts, normalizeSearch, searchableText } from '../src/components/wazzup/chatSearch.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { build, buildSync } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });

test('текст ищется от трёх букв или цифр подряд — как на сервере', () => {
    assert.equal(normalizeSearch('  водительское \n  удостоверение '), 'водительское удостоверение');
    assert.equal(searchableText('сәл'), 'сәл');
    assert.equal(searchableText('777'), '777');
    for (const short of ['ID 77', 'а б в', '7_7', '%%%', '', null, undefined]) assert.equal(searchableText(short), '', String(short));
    assert.equal(searchableText('я'.repeat(500)).length, 200);
});

test('подсветка: без учёта регистра, все вхождения, метасимволы буквально, позиции исходной строки', () => {
    assert.deepEqual(highlightParts('Отправьте ВОДИТЕЛЬСКОЕ и водительское', 'водительское'), [
        { text: 'Отправьте ', match: false }, { text: 'ВОДИТЕЛЬСКОЕ', match: true },
        { text: ' и ', match: false }, { text: 'водительское', match: true }]);
    assert.deepEqual(highlightParts('Комиссия 3% (в день)', '3% (в'), [
        { text: 'Комиссия ', match: false }, { text: '3% (в', match: true }, { text: ' день)', match: false }]);
    assert.deepEqual(highlightParts('ҚҰЖАТ жіберіңіз', 'құжат'), [
        { text: 'ҚҰЖАТ', match: true }, { text: ' жіберіңіз', match: false }]);
    // İ в нижнем регистре длиннее себя: позиции считаются в исходной строке.
    assert.deepEqual(highlightParts('İstanbul ID 7701', '7701'), [
        { text: 'İstanbul ID ', match: false }, { text: '7701', match: true }]);
    assert.deepEqual(highlightParts('Без совпадения', 'чего-нет'), [{ text: 'Без совпадения', match: false }]);
    assert.deepEqual(highlightParts('', 'abc'), []);
    assert.equal(highlightParts('a   b', 'a b').length, 1);   // запрос уже без лишних пробелов, текст — как есть
});

test('кэш недавних запросов: свежее, устаревшее, просроченное и предел', () => {
    const recent = createSearchCache({ limit: 2, freshMs: 1000, maxAgeMs: 5000 });
    recent.set('op\nабв', { items: [1], next: null }, 0);
    assert.deepEqual(recent.get('op\nабв', 500), { items: [1], next: null, stale: false });
    assert.deepEqual(recent.get('op\nабв', 2000), { items: [1], next: null, stale: true });
    assert.equal(recent.get('op\nабв', 6000), null);
    recent.set('a', { items: [] }, 0); recent.set('b', { items: [] }, 0);
    recent.get('a', 0);                                    // «a» стал самым свежим
    recent.set('c', { items: [] }, 0);
    assert.equal(recent.get('b', 0), null);
    assert.ok(recent.get('a', 0) && recent.get('c', 0));
    recent.clear();
    assert.equal(recent.get('a', 0), null);
});

// useMessageSearch — настоящий хук: React и axios подменены, ответы сервера тест отдаёт сам.
const searchOutput = join(cache, 'wazzup-message-search.mjs');
await build({
    entryPoints: ['src/components/wazzup/useMessageSearch.js'], outfile: searchOutput,
    bundle: true, format: 'esm', platform: 'node',
    plugins: [{ name: 'message-search', setup(builder) {
        builder.onResolve({ filter: /^(react|axios)$/ }, ({ path }) => ({ path, namespace: 'message-search' }));
        builder.onLoad({ filter: /.*/, namespace: 'message-search' }, ({ path }) => ({ loader: 'js', contents: path === 'react'
            ? `const h=()=>globalThis.__messageSearch;
               export const useState=(...v)=>h().useState(...v),useRef=(...v)=>h().useRef(...v),
                   useEffect=(...v)=>h().useEffect(...v),useCallback=(fn)=>fn;`
            : `export default { get:(...v)=>globalThis.__messageSearch.get(...v), isCancel:(e)=>e?.name==='CanceledError' };` }));
    } }],
});
const { default: useMessageSearch } = await import(pathToFileURL(searchOutput));

function searchHarness() {
    const slots = [];
    let cursor = 0;
    const requests = [];
    const h = {
        useState(initial) {
            const i = cursor++;
            slots[i] ??= { value: initial };
            const slot = slots[i];
            return [slot.value, (next) => { slot.value = typeof next === 'function' ? next(slot.value) : next; }];
        },
        useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
        useEffect(effect, deps) {
            const i = cursor++, old = slots[i];
            if (old && deps.every((value, k) => Object.is(value, old.deps[k]))) return;
            old?.cleanup?.();
            slots[i] = { deps, cleanup: effect() };
        },
        get(url, options) {
            let resolve, reject;
            const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
            const request = { url, options, resolve: (data) => resolve({ data }),
                reject: (error) => reject(error) };
            options.signal.addEventListener('abort', () => reject(Object.assign(new Error('canceled'), { name: 'CanceledError' })));
            requests.push(request);
            return promise;
        },
    };
    let props = { apiBaseUrl: '/api-base', headers: () => ({ Authorization: 'Bearer t' }), account: 'op' };
    const render = (next = {}) => {
        props = { ...props, ...next };
        globalThis.__messageSearch = h; cursor = 0;
        return useMessageSearch(props);
    };
    const flush = () => new Promise((resolve) => setImmediate(resolve));
    return { render, requests, flush };
}

const hit = (id) => ({ messageId: id, chatId: '7700', channelId: 'ch', dt: '2026-10-18T08:00:00+00:00', text: id, chat: {} });

test('хук поиска: один запрос в полёте, ответ устаревшего ввода отбрасывается', async () => {
    const s = searchHarness();
    s.render().search('водит');
    s.render().search('водитель');
    assert.equal(s.requests.length, 2);
    assert.equal(s.requests[0].options.signal.aborted, true);
    assert.deepEqual(s.requests[1].options.params, { account: 'op', q: 'водитель', before: undefined });
    assert.equal(s.requests[1].url, '/api-base/api/wazzup/chat-search');
    assert.deepEqual(s.requests[1].options.headers, { Authorization: 'Bearer t' });
    assert.equal(s.render().loading, true);
    s.requests[1].resolve({ items: [hit('m2'), hit('m1')], next: 'cursor-1' });
    await s.flush();
    let state = s.render();
    assert.deepEqual([state.query, state.items.map((item) => item.messageId), state.next, state.loading],
        ['водитель', ['m2', 'm1'], 'cursor-1', false]);
    // Следующая страница — с указателем, дописывается в конец.
    state.more();
    assert.deepEqual(s.requests[2].options.params, { account: 'op', q: 'водитель', before: 'cursor-1' });
    s.requests[2].resolve({ items: [hit('m0')], next: null });
    await s.flush();
    state = s.render();
    assert.deepEqual([state.items.map((item) => item.messageId), state.next], [['m2', 'm1', 'm0'], null]);
    state.more();                                              // страниц больше нет — запроса нет
    assert.equal(s.requests.length, 3);
});

test('хук поиска: короткий запрос и сброс гасят найденное и запрос в полёте', async () => {
    const s = searchHarness();
    s.render().search('паспорт');
    s.render().search('па');
    assert.equal(s.requests[0].options.signal.aborted, true);
    assert.deepEqual(s.render().items, []);
    assert.equal(s.render().query, '');
    s.render().search('паспорт');
    s.render().reset();
    s.requests[1].resolve({ items: [hit('late')], next: null });
    await s.flush();
    assert.deepEqual([s.render().query, s.render().items], ['', []]);
    assert.equal(s.requests.length, 2);
});

test('хук поиска: ответ, пришедший перед новым вводом, но разобранный после, не перетирает новый поиск', async () => {
    const s = searchHarness();
    s.render().search('паспорт');
    // Ответ уже получен (отмена опоздала), а обработать его успели только после нового ввода.
    s.requests[0].resolve({ items: [hit('old')], next: null });
    s.render().search('техпаспорт');
    await s.flush();
    let state = s.render();
    assert.deepEqual([state.query, state.items, state.loading], ['техпаспорт', [], true]);
    // Тот же запрос ещё раз (повтор) отменяет прежний — отмена не гасит индикатор нового.
    s.render().search('техпаспорт');
    await s.flush();
    state = s.render();
    assert.deepEqual([state.loading, state.error], [true, '']);
    s.requests[2].resolve({ items: [hit('new')], next: null });
    await s.flush();
    assert.deepEqual(s.render().items.map((item) => item.messageId), ['new']);
});

test('хук поиска: возврат к недавнему запросу — сразу из кэша, без запроса; другой аккаунт — заново', async () => {
    const s = searchHarness();
    s.render().search('техпаспорт');
    s.requests[0].resolve({ items: [hit('t1')], next: null });
    await s.flush();
    s.render().search('техпаспорт2');
    s.requests[1].resolve({ items: [], next: null });
    await s.flush();
    s.render().search('  техпаспорт ');
    assert.equal(s.requests.length, 2);
    assert.deepEqual(s.render().items.map((item) => item.messageId), ['t1']);
    s.render({ account: 'potok' });
    s.render().search('техпаспорт');
    assert.equal(s.requests.length, 3);
    assert.equal(s.requests[2].options.params.account, 'potok');
});

test('хук поиска: ошибка сервера — его текст и повтор; отменённый запрос ошибкой не считается', async () => {
    const s = searchHarness();
    s.render().search('удостоверение');
    s.requests[0].reject({ response: { data: { error: 'Поиск по тексту занял слишком долго — уточните запрос' } } });
    await s.flush();
    assert.equal(s.render().error, 'Поиск по тексту занял слишком долго — уточните запрос');
    s.render().search('удостоверение');
    s.requests[1].reject(new Error('Network Error'));
    await s.flush();
    assert.equal(s.render().error, 'Не удалось найти сообщения. Попробуйте ещё раз.');
    s.render().search('удостоверение');
    s.render().search('водитель');                              // первый отменён
    await s.flush();
    assert.equal(s.render().error, '');
    assert.equal(s.render().loading, true);
});

// Блок «Сообщения» — настоящий компонент через react-dom/server.
const hitsOutput = join(cache, 'wazzup-message-hits.mjs');
buildSync({
    entryPoints: [fileURLToPath(new URL('../src/components/wazzup/ChatMessageHits.jsx', import.meta.url))],
    bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile: hitsOutput,
    packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' }, logLevel: 'silent',
});
const { default: ChatMessageHits } = await import(pathToFileURL(hitsOutput));
const renderHits = (search, extra = {}) => renderToStaticMarkup(React.createElement(ChatMessageHits, {
    search, formatTime: (iso) => `t(${iso.slice(11, 16)})`, onOpen() {}, onMore() {}, onRetry() {}, ...extra }));

test('блок «Сообщения»: строка — чат, время, кусок с подсветкой; «Показать ещё» по указателю', () => {
    const items = [
        { ...hit('m2'), text: '…отправьте Водительское удостоверение', isEcho: true, status: 'read',
            chat: { contactName: 'Айдар', contactPhone: '77011112233' } },
        { ...hit('m1'), text: 'водительское', isEcho: false, chat: { contactPhone: '77019998877' } },
    ];
    const html = renderHits({ query: 'водительское', items, next: 'cursor', loading: false, error: '' }, { currentId: 'm1' });
    assert.match(html, />Сообщения</);
    assert.match(html, /Айдар/);
    assert.match(html, /77019998877/);                           // без имени — номер
    assert.match(html, /t\(08:00\)/);
    assert.equal(html.split('<mark').length - 1, 2);
    assert.match(html, /<mark[^>]*><span[^>]*>Водительское<\/span><\/mark>/);
    assert.match(html, /aria-current="true"/);
    assert.equal(html.split('aria-current').length - 1, 1);
    assert.match(html, /Показать ещё сообщения/);
    assert.doesNotMatch(renderHits({ query: 'водительское', items, next: null, loading: false, error: '' }), /Показать ещё/);
});

test('блок «Сообщения»: пусто — ничего; поиск идёт — индикатор; ошибка — текст и «Повторить»', () => {
    assert.equal(renderHits({ query: '', items: [], next: null, loading: false, error: '' }), '');
    assert.equal(renderHits({ query: 'паспорт', items: [], next: null, loading: false, error: '' }), '');
    assert.match(renderHits({ query: 'паспорт', items: [], next: null, loading: true, error: '' }), /Ищем в сообщениях/);
    const failed = renderHits({ query: 'паспорт', items: [], next: 'c', loading: false, error: 'Не удалось' });
    assert.match(failed, /Не удалось/);
    assert.match(failed, /Повторить/);
    assert.doesNotMatch(failed, /Показать ещё/);
});

test('раздел: поиск по тексту запускается вместе со списком и гаснет при смене аккаунта и переходе по ссылке', () => {
    const view = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8').replace(/\r\n/g, '\n');
    const onInput = view.slice(view.indexOf('    const onSearchInput = '), view.indexOf('    const openChat = '));
    assert.match(onInput, /setTimeout\(\(\) => \{[\s\S]*loadChats\(\{ q: value\.trim\(\) \}\);\n\s*messageSearch\.search\(value\);\n\s*\}, 350\)/);
    const switching = view.slice(view.indexOf('    const switchAccount = '), view.indexOf('    const onSearchInput = '));
    assert.match(switching, /messageSearch\.reset\(\)/);
    const deepLink = view.slice(view.indexOf('        const q = target.channelId ? \'\' : target.phone;'), view.indexOf('        if (target.channelId) openChat('));
    assert.match(deepLink, /messageSearch\.reset\(\)/);
    // Блок — под чатами и не в «Ожидают ответа»; пустой экран не спорит с найденными сообщениями.
    assert.match(view, /const searchHitsShown = !\(unreadOnly && pilot\.enabled\) && Boolean\(appliedSearch\) && Boolean\(messageSearch\.query\);/);
    const list = view.slice(view.indexOf('Показать ещё ({chats.length} из {chatsTotal})'), view.indexOf('{/* Лента переписки */}'));
    assert.match(list, /\{searchHitsShown && \(\n\s*<ChatMessageHits search=\{messageSearch\}/);
    assert.match(view, /&& !\(searchHitsShown && \(messageSearch\.items\.length \|\| messageSearch\.loading \|\| messageSearch\.error\)\) && \(/);
});

test('раздел: строка «Сообщений» открывает чат на найденном; открытый чат не перечитывается', () => {
    const view = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8').replace(/\r\n/g, '\n');
    const opening = view.slice(view.indexOf('    const openSearchHit = '), view.indexOf('    /* Переход по ссылке на чат'));
    assert.match(opening, /messageRow\(threadBox\.current, hit\.messageId\)/);
    assert.match(opening, /centerMessageRow\(threadBox\.current, loadedRow\);\n\s*flashMessage\(loadedRow, 2200\);\n\s*return;/);
    assert.match(opening, /openChat\(hit\.chat, \{ focus: hit\.messageId \}\);/);
    const loadThread = view.slice(view.indexOf('    const loadThread = '), view.indexOf('    const refreshPilotThread = '));
    assert.match(loadThread, /focus: \(!before && focus\) \|\| undefined/);
    assert.match(loadThread, /threadFocus\.current = focus \? \{ key: pilotChatKey\(requestAccount, chat\), messageId: focus,/);
    // Обычное открытие гасит прежнюю цель: иначе чат открылся бы на старом найденном.
    assert.match(loadThread, /\} : null;\n\s*\} else setThreadLoadingMore\(true\);/);
    assert.match(loadThread, /if \(!before\) \{ threadFocus\.current = null; setThread\(\[\]\); \}/);
});
