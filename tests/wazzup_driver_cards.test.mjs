/*
 * Карточка водителя «Чатов ОП»: разбор ID (как на сервере), версии, подписи
 * записи в amoCRM, отрисовка карточки и «Карточек», поведение хука.
 *
 * Компоненты собираются esbuild'ом и рендерятся через react-dom/server (как
 * cdr_deals_view_render.test.mjs): эффекты не запускаются, но сломанный
 * импорт, JSX или обращение к несуществующему полю падают здесь. Хук гоняется
 * на детерминированной подмене React и axios (как wazzup_internal_notes).
 */
import test, { mock } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import {
    DRIVER_ID_HINT, amoStatus, changedFields, fmtCardTime, mergeCardVersions, normalizeDriverId, sameCardValues,
} from '../src/components/wazzup/driverCards.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const DRIVER = 'a1b2c3d4e5f60718293a4b5c6d7e8f90';
const PARK = '0f1e2d3c4b5a69788796a5b4c3d2e1f0';
const OUT_DIR = join(process.cwd(), 'node_modules', '.cache', 'otp-driver-cards-test');
mkdirSync(OUT_DIR, { recursive: true });

const card = (id, overrides = {}) => ({
    id, channelId: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', chatId: '77000000101',
    createdAt: '2026-10-16T06:30:00+00:00', authorId: 7, authorName: 'Оператор Тестовый',
    contactName: 'Асқар', contactPhone: '+77000000101', driverId: DRIVER,
    park: { id: 1667515, name: 'Jana Taxi' }, city: { id: 1610837, name: 'Алматы' },
    amo: { state: 'synced', leadId: 10000001, leadUrl: 'https://igroupkz.amocrm.ru/leads/detail/10000001',
           error: null, syncedAt: '2026-10-16T06:30:02+00:00' },
    ...overrides,
});

// ── Разбор ID ───────────────────────────────────────────────────────────────

test('driver ID: the same rule as the server, without lookbehind for old Safari', async () => {
    for (const [raw, expected] of [
        [DRIVER, DRIVER], [`  ${DRIVER.toUpperCase()}\n`, DRIVER],
        [`https://fleet.yandex.kz/contractors/${DRIVER}/details?park_id=${PARK}&theme=night`, DRIVER],
        [`fleet.yandex.kz/drivers/${DRIVER}`, DRIVER], [`ID: ${DRIVER}`, DRIVER], [`g${DRIVER}`, DRIVER],
        ['', null], ['   ', null], [null, null], [undefined, null],
    ]) {
        const parsed = normalizeDriverId(raw);
        assert.equal(parsed.value, expected, String(raw));
        assert.equal(parsed.error, '', String(raw));
    }
    for (const raw of ['77000000099', 'ractors/a1b2c3d4e5f60718293a4b5c', DRIVER.slice(1), `${DRIVER}0`,
        `a${DRIVER}`, `${DRIVER} ${PARK}`, `contractors/${DRIVER}/x/contractors/${PARK}`, 'x'.repeat(2001), 12345,
        `https://fleet.yandex.kz/contractors/${DRIVER.slice(0, -1)}/details?park_id=${PARK}`,
        `https://fleet.yandex.kz/drivers?park_id=${PARK}`, `park_id=${PARK}`]) {
        assert.deepEqual(normalizeDriverId(raw), { value: null, error: DRIVER_ID_HINT }, String(raw).slice(0, 40));
    }
    const { readFileSync } = await import('node:fs');
    const source = readFileSync(new URL('../src/components/wazzup/driverCards.js', import.meta.url), 'utf8')
        .replace(/\/\/.*$/gm, '');
    assert.doesNotMatch(source, /\(\?<[!=]/, 'просмотр назад ломает разбор модуля в Safari до 16.4');
});

// ── Версии и подписи ────────────────────────────────────────────────────────

test('versions: newest first, one per id, live and snapshot merged, capped', () => {
    const older = card('a', { createdAt: '2026-10-16T05:00:00Z' });
    const newer = card('b', { createdAt: '2026-10-16T06:00:00Z', amo: { ...card('b').amo, state: 'pending' } });
    const merged = mergeCardVersions([older], [newer, { ...newer, amo: { ...newer.amo, state: 'synced' } }]);
    assert.deepEqual(merged.map((item) => [item.id, item.amo.state]), [['b', 'synced'], ['a', 'synced']]);
    assert.deepEqual(mergeCardVersions([], [{ id: 'x' }, null, { createdAt: 'z' }]), []);
    const many = Array.from({ length: 60 }, (_, index) => card(`c${index}`, {
        createdAt: new Date(Date.UTC(2026, 9, 1, 0, index)).toISOString() }));
    const capped = mergeCardVersions([], many);
    assert.equal(capped.length, 50);
    assert.equal(capped[0].id, 'c59');
});

test('a late GET snapshot does not turn a written card back into «sending»; a retry does', () => {
    const queued = '2026-10-16T06:30:00+00:00';
    const synced = card('a', { amo: { ...card('a').amo, state: 'synced', queuedAt: queued } });
    const staleGet = card('a', { amo: { state: 'pending', leadId: null, error: null, queuedAt: queued } });
    assert.equal(mergeCardVersions([synced], [staleGet])[0].amo.state, 'synced', 'поздний снимок той же постановки');
    const olderQueue = card('a', { amo: { state: 'pending', queuedAt: '2026-10-16T06:00:00+00:00' } });
    assert.equal(mergeCardVersions([synced], [olderQueue])[0].amo.state, 'synced');
    const retried = card('a', { amo: { state: 'pending', error: null, queuedAt: '2026-10-16T07:00:00+00:00' } });
    assert.equal(mergeCardVersions([synced], [retried])[0].amo.state, 'pending', '«Повторить» — новая постановка');
    const failed = card('a', { amo: { state: 'failed', error: 'amoCRM ответила 400', queuedAt: queued } });
    assert.equal(mergeCardVersions([staleGet], [failed])[0].amo.state, 'failed', 'итог поверх «отправляется» — всегда');
});

test('amoCRM status: green only when written, red only when a person must act', () => {
    assert.equal(amoStatus(card('a')).tone, 'ok');
    assert.match(amoStatus(card('a')).title, /10000001/);
    const pending = amoStatus(card('a', { amo: { state: 'pending' } }));
    assert.deepEqual([pending.tone, pending.label], ['wait', 'Отправляем в amoCRM']);
    const retrying = amoStatus(card('a', { amo: { state: 'pending', error: 'amoCRM ответила 503' } }));
    assert.deepEqual([retrying.tone, retrying.title], ['wait', 'amoCRM ответила 503']);
    for (const [state, label] of [['failed', 'Не записано в amoCRM'], ['no_deal', 'Сделка в amoCRM не найдена']]) {
        const status = amoStatus(card('a', { amo: { state, error: 'причина' } }));
        assert.deepEqual([status.tone, status.label, status.title, status.retry], ['bad', label, 'причина', true]);
    }
    assert.equal(amoStatus(card('a', { amo: { state: 'superseded' } })).tone, 'muted');
});

test('unchanged drafts and changed fields are told apart', () => {
    const current = card('a');
    assert.equal(sameCardValues(current, { driverId: DRIVER, parkId: 1667515, cityId: 1610837 }), true);
    assert.equal(sameCardValues(current, { driverId: DRIVER, parkId: 2386199, cityId: 1610837 }), false);
    assert.equal(sameCardValues(null, { driverId: DRIVER }), false);
    assert.deepEqual([...changedFields(card('b', { park: { id: 2386199, name: 'Tenge Taxi' } }), current)], ['park']);
    assert.deepEqual([...changedFields(card('c', { city: null }), null)], [], 'первая версия не выделяется');
    assert.deepEqual([...changedFields(card('d', { driverId: null, city: null }), current)].sort(), ['city', 'driverId']);
    assert.match(fmtCardTime(new Date().toISOString()), /^сегодня, \d\d:\d\d$/);
    assert.equal(fmtCardTime('not a date'), '');
});

// ── Отрисовка ───────────────────────────────────────────────────────────────

async function bundle(entry, name) {
    const stub = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stub, 'const never = () => new Promise(() => {}); export default { get: never, post: never, isCancel: () => false };');
    const result = await esbuild.build({
        entryPoints: [join(process.cwd(), entry)], bundle: true, write: false, format: 'esm', platform: 'node',
        target: 'node18', jsx: 'transform', loader: { '.jsx': 'jsx', '.js': 'jsx' },
        external: ['react', 'react-dom', 'lucide-react', 'framer-motion'], alias: { axios: stub }, logLevel: 'silent',
    });
    const out = join(OUT_DIR, `${name}.mjs`);
    writeFileSync(out, result.outputFiles[0].text);
    return import(pathToFileURL(out).href + `?t=${Date.now()}`);
}

const { default: ChatDriverCard, cancelsForm, selectOptions, missingChoice } = await bundle('src/components/wazzup/ChatDriverCard.jsx', 'card');

test('Escape closes the form, but not the one that closes an open list', () => {
    const inside = { getAttribute: () => null };
    const form = { contains: (node) => node === inside || node === openButton };
    const openButton = { getAttribute: (name) => (name === 'aria-expanded' ? 'true' : null) };
    const portalSearch = { getAttribute: () => null };
    assert.equal(cancelsForm({ key: 'Escape', currentTarget: form, target: inside }), true);
    assert.equal(cancelsForm({ key: 'Escape', currentTarget: form, target: portalSearch }), false, 'поиск списка — в портале');
    assert.equal(cancelsForm({ key: 'Escape', currentTarget: form, target: openButton }), false, 'кнопка раскрытого списка');
    assert.equal(cancelsForm({ key: 'Enter', currentTarget: form, target: inside }), false);
});

test('a card value no longer in the amoCRM list stays visible, marked, and must be replaced', () => {
    const list = [{ id: 1, name: 'Jana Taxi' }];
    assert.deepEqual(selectOptions(list, { id: 1, name: 'Jana Taxi' }), [{ value: 1, label: 'Jana Taxi' }]);
    assert.deepEqual(selectOptions(list, { id: 9, name: 'Старый парк' }),
        [{ value: 9, label: 'Старый парк — нет в amoCRM' }, { value: 1, label: 'Jana Taxi' }]);
    assert.deepEqual(selectOptions(null, { id: 9, name: 'Старый парк' }), [{ value: 9, label: 'Старый парк' }],
        'пока справочник не загружен, пометки нет');
    assert.deepEqual(selectOptions(null, null), []);
    assert.equal(missingChoice(list, 9, 'Этого таксопарка'), 'Этого таксопарка больше нет в списке amoCRM — выберите другой');
    assert.equal(missingChoice(list, 1, 'Этого таксопарка'), '');
    assert.equal(missingChoice(list, null, 'Этого таксопарка'), '');
    assert.equal(missingChoice(null, 9, 'Этого таксопарка'), '');
});
const { default: DriverCardsTab, cardsCount, appendCards } = await bundle('src/components/wazzup/DriverCardsTab.jsx', 'tab');

test('«Show more» never repeats a chat that the shifted page brings again', () => {
    const row = (id, chatId) => ({ id, channelId: 'c', chatId });
    assert.deepEqual(appendCards([row('a', '1'), row('b', '2')], [row('b', '2'), row('c', '3')]).map((card) => card.id),
        ['a', 'b', 'c']);
    assert.deepEqual(appendCards(null, [row('x', '9')]).map((card) => card.id), ['x']);
});

test('the cards counter is declined like Russian, not «2 карточек»', () => {
    assert.deepEqual([0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 111, 1002].map(cardsCount), [
        '0 карточек', '1 карточка', '2 карточки', '4 карточки', '5 карточек', '11 карточек', '12 карточек',
        '14 карточек', '21 карточка', '22 карточки', '25 карточек', '101 карточка', '111 карточек', '1002 карточки']);
});
const hookState = (overrides = {}) => ({ items: [], current: null, canEdit: false, loading: false, error: '',
    refresh() {}, apply() {}, save: async () => ({}), retry: async () => ({}), ...overrides });
const render = (state) => renderToStaticMarkup(React.createElement(ChatDriverCard, {
    chat: { channelId: 'c', chatId: '77000000101' }, card: state, apiBaseUrl: 'http://api.test', headers: () => ({}) }));

test('until the chat card arrives there is nothing to fill: an empty form must not cover a filled card', () => {
    const html = render(hookState({ canEdit: true, loading: true, current: null }));
    assert.match(html, /Загрузка карточки/);
    assert.doesNotMatch(html, /Заполнить|Изменить/);
});

test('the registry shows «sending» without a spinner: it is not live', async () => {
    const { AmoStatus } = await bundle('src/components/wazzup/ChatDriverCard.jsx', 'card-status');
    const pending = card('a', { amo: { state: 'pending' } });
    const still = renderToStaticMarkup(React.createElement(AmoStatus, { card: pending, live: false }));
    const live = renderToStaticMarkup(React.createElement(AmoStatus, { card: pending }));
    assert.doesNotMatch(still, /animate-spin/);
    assert.match(live, /animate-spin/);
});

test('empty card: processors are offered to fill it, viewers only see that it is empty', () => {
    const editor = render(hookState({ canEdit: true }));
    assert.match(editor, /Карточка водителя не заполнена/);
    assert.match(editor, />Заполнить</);
    assert.doesNotMatch(editor, /История/);
    const viewer = render(hookState());
    assert.match(viewer, /Карточка водителя не заполнена/);
    assert.doesNotMatch(viewer, /Заполнить|Изменить/);
    assert.match(render(hookState({ loading: true })), /Загрузка карточки/);
});

test('filled card shows the three values, the deal link and history; edit only for processors', () => {
    const current = card('a');
    const html = render(hookState({ items: [current], current, canEdit: true }));
    for (const text of [DRIVER, 'Jana Taxi', 'Алматы', 'В amoCRM', ' История</button>', '>Изменить</button>']) {
        assert.ok(html.includes(text), text);
    }
    assert.match(html, /href="https:\/\/igroupkz\.amocrm\.ru\/leads\/detail\/10000001"/);
    assert.match(html, /target="_blank" rel="noopener noreferrer"/);
    const viewer = render(hookState({ items: [current], current }));
    assert.doesNotMatch(viewer, /Изменить|Повторить/);
    assert.match(viewer, /История/);
});

test('a card that did not reach amoCRM says so and offers a retry only to processors', () => {
    const failed = card('a', { park: null, amo: { state: 'no_deal', error: 'В amoCRM нет контакта с этим номером' } });
    const html = render(hookState({ items: [failed], current: failed, canEdit: true }));
    assert.match(html, /Сделка в amoCRM не найдена/);
    assert.match(html, /title="В amoCRM нет контакта с этим номером"/);
    assert.match(html, />Повторить</);
    assert.match(html, /—/, 'пустое поле — прочерк, а не пропуск');
    assert.doesNotMatch(render(hookState({ items: [failed], current: failed })), /Повторить/);
});

test('cards tab renders its toolbar and the loading state before any request', () => {
    const html = renderToStaticMarkup(React.createElement(DriverCardsTab, {
        apiBaseUrl: 'http://api.test', headers: () => ({}), onOpenChat() {}, active: true }));
    assert.match(html, /placeholder="ID, телефон, имя, парк…"/);
    assert.match(html, /Загрузка…/);
    assert.match(html, / Обновить<\/button>/);
});

// ── Хук ─────────────────────────────────────────────────────────────────────

const harnessPlugin = { name: 'driver-card-harness', setup(builder) {
    builder.onResolve({ filter: /^(react|axios)$/ }, ({ path }) => ({ path, namespace: 'card-fixture' }));
    builder.onLoad({ filter: /.*/, namespace: 'card-fixture' }, ({ path }) => ({ loader: 'js', contents:
        path === 'react' ? `const h=()=>globalThis.__cardHarness;
            export const useState=(...v)=>h().useState(...v);
            export const useRef=(...v)=>h().useRef(...v);
            export const useEffect=(...v)=>h().useEffect(...v);
            export const useCallback=(...v)=>h().useCallback(...v);
            export default {};`
            : 'export default {get:(...v)=>globalThis.__cardHarness.get(...v),post:(...v)=>globalThis.__cardHarness.post(...v)};',
    }));
} };
const hookFile = join(OUT_DIR, 'hook.mjs');
await esbuild.build({ entryPoints: [join(process.cwd(), 'src/components/wazzup/useDriverCard.js')], outfile: hookFile,
    bundle: true, platform: 'node', format: 'esm', target: 'node22', plugins: [harnessPlugin], logLevel: 'silent' });
const { default: useDriverCard, loadDriverCardOptions, resetDriverCardOptions } = await import(pathToFileURL(hookFile).href);
const tick = () => new Promise((resolve) => setImmediate(resolve));

function fixture({ get, post } = {}) {
    const slots = [], gets = [], posts = [];
    let index = 0, effects = [];
    const props = { enabled: true, apiBaseUrl: '/fixture', headers: () => ({ Authorization: 'fixture' }),
        chat: { channelId: 'c', chatId: '77000000101' } };
    const h = {
        get(...args) { gets.push(args); return get?.(...args) || Promise.resolve({ data: { items: [], canEdit: true } }); },
        post(...args) { posts.push(args); return post?.(...args) || Promise.resolve({ data: { item: card('saved') } }); },
        useState(initial) { const i = index++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
            return [slots[i].value, (value) => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }]; },
        useRef(initial) { const i = index++; slots[i] ??= { current: initial }; return slots[i]; },
        useCallback(fn, deps) { const i = index++; if (!slots[i] || deps.some((v, k) => !Object.is(v, slots[i].deps[k]))) slots[i] = { fn, deps }; return slots[i].fn; },
        useEffect(fn, deps) { const i = index++, old = slots[i];
            if (old && deps?.every((v, k) => Object.is(v, old.deps[k]))) return;
            slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); },
        render(next = {}) { Object.assign(props, next); index = 0; const result = useDriverCard(props);
            const pending = effects; effects = []; pending.forEach((fn) => fn()); return result; },
        unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
        gets, posts,
    };
    globalThis.__cardHarness = h;
    return h;
}

test('hook: loads the chat card, applies live events of this chat only, re-reads on an empty frame', async () => {
    const h = fixture({ get: () => Promise.resolve({ data: { items: [card('a')], canEdit: false } }) });
    h.render(); await tick();
    let state = h.render();
    assert.equal(state.current.id, 'a');
    assert.equal(state.canEdit, false);
    assert.deepEqual(h.gets[0][1].params, { account: 'op', channelId: 'c', chatId: '77000000101' });
    assert.equal(state.apply({ account: 'op', channelId: 'c', chatId: 'other', card: card('x') }), false);
    state.apply({ account: 'op', channelId: 'c', chatId: '77000000101',
        card: card('b', { createdAt: '2026-10-16T07:00:00Z', amo: { state: 'pending' } }) });
    state = h.render();
    assert.deepEqual(state.items.map((item) => item.id), ['b', 'a']);
    state.apply({ account: 'op', channelId: 'c', chatId: '77000000101', cardId: 'b' });
    await tick();
    assert.equal(h.gets.length, 2, 'кадр без содержимого — повод перечитать');
    h.unmount();
});

test('hook: an unanswered save keeps its id for the retry; a refusal or a new draft gets a new one', async () => {
    let answer = () => Promise.reject(Object.assign(new Error('timeout'), { code: 'ECONNABORTED' }));
    const h = fixture({ post: () => answer() });
    h.render(); await tick();
    const state = h.render();
    const values = { driverId: DRIVER, parkId: 1667515, cityId: null };
    await assert.rejects(state.save(values), (error) => error.uncertain === true && /вторая версия не появится/.test(error.message));
    await assert.rejects(state.save(values));
    assert.equal(h.posts[0][1].clientCardId, h.posts[1][1].clientCardId);
    answer = () => Promise.reject({ response: { status: 400, data: { error: 'Такого таксопарка нет', field: '' } } });
    await assert.rejects(state.save(values), (error) => error.uncertain === false && error.message === 'Такого таксопарка нет');
    answer = () => Promise.resolve({ data: { item: card('saved') } });
    await state.save(values);
    assert.notEqual(h.posts[3][1].clientCardId, h.posts[1][1].clientCardId);
    assert.deepEqual(Object.keys(h.posts[3][1]).sort(), ['account', 'channelId', 'chatId', 'cityId', 'clientCardId', 'driverId', 'parkId']);
    assert.equal(h.render().current.id, 'saved');
    h.unmount();
});

test('hook: a late answer for the previous chat and events of another account are ignored', async () => {
    let answerA;
    const h = fixture({ get: (_url, config) => (config.params.chatId === 'A'
        ? new Promise((resolve) => { answerA = resolve; })
        : Promise.resolve({ data: { items: [card('b')], canEdit: true } })) });
    h.render({ chat: { channelId: 'c', chatId: 'A' } });
    h.render({ chat: { channelId: 'c', chatId: 'B' } }); await tick();
    answerA({ data: { items: [card('a')], canEdit: false } }); await tick();
    const state = h.render();
    assert.deepEqual([state.current?.id, state.canEdit], ['b', true], 'ответ чата A не попадает в чат B');
    assert.equal(state.apply({ account: 'potok', channelId: 'c', chatId: 'B', card: card('x') }), false);
    assert.equal(h.render().current.id, 'b');
    h.unmount();
});

test('hook: 503 and 408 are «no answer» — the same save id goes again', async () => {
    for (const status of [503, 408]) {
        let attempt = 0;
        const h = fixture({ post: () => (attempt++ === 0
            ? Promise.reject({ response: { status, data: {} } }) : Promise.resolve({ data: { item: card('saved') } })) });
        h.render(); await tick();
        const values = { driverId: DRIVER, parkId: 1, cityId: null };
        await assert.rejects(h.render().save(values), (error) => error.uncertain === true);
        await h.render().save(values);
        assert.equal(h.posts[0][1].clientCardId, h.posts[1][1].clientCardId, String(status));
        h.unmount();
    }
});

test('hook: another chat does not inherit the edit right while its card is loading', async () => {
    const h = fixture({ get: (_url, config) => (config.params.chatId === 'A'
        ? Promise.resolve({ data: { items: [card('a')], canEdit: true } }) : new Promise(() => {})) });
    h.render({ chat: { channelId: 'c', chatId: 'A' } }); await tick();
    assert.equal(h.render().canEdit, true);
    h.render({ chat: { channelId: 'c', chatId: 'B' } });
    const state = h.render();
    assert.deepEqual([state.canEdit, state.loading, state.current], [false, true, null]);
    h.unmount();
});

test('hook: without a live stream a «sending» card is asked again until it is written', async () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    try {
        let calls = 0;
        let amo = { state: 'pending', queuedAt: '2026-10-16T06:30:00+00:00' };
        const h = fixture({ get: () => { calls += 1; return Promise.resolve({ data: { items: [card('a', { amo })], canEdit: false } }); } });
        h.render({ live: false }); await tick(); h.render();
        assert.equal(calls, 1);
        mock.timers.tick(6000); await tick(); h.render();
        assert.equal(calls, 2, 'итог записи спрашиваем сами');
        amo = { ...amo, state: 'synced', leadId: 7 };
        mock.timers.tick(6000); await tick(); h.render();
        assert.equal(calls, 3);
        mock.timers.tick(60000); await tick(); h.render();
        assert.equal(calls, 3, 'записано — опрос окончен');
        h.unmount();
        const live = fixture({ get: () => { calls += 1; return Promise.resolve({ data: { items: [card('b', { amo: { state: 'pending' } })] } }); } });
        live.render({ live: true }); await tick(); live.render();
        mock.timers.tick(60000); await tick(); live.render();
        assert.equal(calls, 4, 'с живым потоком итог придёт кадром — не опрашиваем');
        live.unmount();
    } finally {
        mock.timers.reset();
    }
});

test('hook: a server without the card route hides the card; our own 404 is shown as an error', async () => {
    // Pages выкатывается раньше Render: старый сервер отвечает HTML-страницей 404.
    const old = fixture({ get: () => Promise.reject({ response: { status: 404, data: '<!doctype html><title>404 Not Found</title>' } }) });
    old.render(); await tick();
    let state = old.render();
    assert.deepEqual([state.unavailable, state.error], [true, '']);
    assert.equal(render(hookState({ unavailable: true, canEdit: true })), '', 'полосы нет вовсе');
    old.unmount();
    const ours = fixture({ get: () => Promise.reject({ response: { status: 404, data: { error: 'Чат не найден' } } }) });
    ours.render(); await tick();
    state = ours.render();
    assert.deepEqual([state.unavailable, state.error], [false, 'Чат не найден']);
    ours.unmount();
});

test('hook: a stale retry re-reads the card instead of guessing', async () => {
    const h = fixture({ post: () => Promise.reject({ response: { status: 409, data: { code: 'STALE_CARD', error: 'Карточку уже изменили' } } }) });
    h.render(); await tick();
    await assert.rejects(h.render().retry('a'), /Карточку уже изменили/);
    await tick();
    assert.equal(h.gets.length, 2);
    h.unmount();
});

test('options: one request per address, a failure is not cached', async () => {
    resetDriverCardOptions();
    let fail = true;
    const h = fixture({ get: () => (fail ? Promise.reject({ response: { status: 503, data: { error: 'нет списков' } } })
        : Promise.resolve({ data: { parks: [{ id: 1, name: 'Jana Taxi' }], cities: [{ id: 2, name: 'Алматы' }] } })) });
    await assert.rejects(loadDriverCardOptions({ apiBaseUrl: '/fixture', headers: () => ({}) }), /нет списков/);
    fail = false;
    const [first, second] = await Promise.all([
        loadDriverCardOptions({ apiBaseUrl: '/fixture', headers: () => ({}) }),
        loadDriverCardOptions({ apiBaseUrl: '/fixture', headers: () => ({}) })]);
    assert.equal(first, second);
    assert.equal(h.gets.length, 2);
    await loadDriverCardOptions({ apiBaseUrl: '/fixture', headers: () => ({}) });
    assert.equal(h.gets.length, 2);
    resetDriverCardOptions();
});
