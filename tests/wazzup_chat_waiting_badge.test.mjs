/*
 * Счётчик «Ожидают ответа» «Чатов ОП» в боковой панели портала (до «9+»):
 * хранилище chatWaitingBadge.js, сторож WazzupWaitingKeeper.jsx и проводка в
 * разделе и App.jsx. Поток only=unread — tests/wazzup_pilot_realtime.test.mjs
 * и tests/test_wazzup_pilot.py.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import {
    claimSectionWaiting, currentWaiting, publishWaiting, resetWaiting, sectionCountsWaiting, waitingBadgeLabel,
} from '../src/components/wazzup/chatWaitingBadge.js';

test('подпись бейджа: одна цифра, дальше «9+»', () => {
    assert.deepEqual([1, 9, 10, 57, 1000].map(waitingBadgeLabel), ['1', '9', '9+', '9+', '9+']);
});

test('хранилище: раздел считает сам — сторож его не перебивает; после ухода раздела число не мигает нулём', () => {
    resetWaiting();
    assert.equal(currentWaiting(), 0);
    publishWaiting(4, 'keeper');
    assert.equal(currentWaiting(), 4);
    const release = claimSectionWaiting();
    assert.equal(sectionCountsWaiting(), true);
    publishWaiting(2);                         // раздел
    publishWaiting(7, 'keeper');               // сторож молчит, пока раздел считает
    assert.equal(currentWaiting(), 2);
    release(); release();                      // повторный отказ — без последствий
    assert.equal(sectionCountsWaiting(), false);
    assert.equal(currentWaiting(), 2, 'сторож подхватит с этого числа');
    publishWaiting(3, 'keeper');
    assert.equal(currentWaiting(), 3);
    publishWaiting(null, 'keeper');            // обработка не открыта — числа нет
    assert.equal(currentWaiting(), 0);
    for (const broken of [-1, Number.NaN, Infinity, '5', undefined]) {
        publishWaiting(broken);
        assert.equal(currentWaiting(), 0, String(broken));
    }
    publishWaiting(2.7);
    assert.equal(currentWaiting(), 2);
    publishWaiting(0);
    // Повторный отказ одного держателя не снимает заявку другого (смена ключа раздела).
    const first = claimSectionWaiting(), second = claimSectionWaiting();
    first(); first();
    assert.equal(sectionCountsWaiting(), true);
    second();
    assert.equal(sectionCountsWaiting(), false);
    resetWaiting();
});

// Сторож — настоящий компонент; хуки раздела, хранилище и React подменены.
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzup-waiting-keeper.mjs');
await build({
    entryPoints: ['src/components/wazzup/WazzupWaitingKeeper.jsx'], outfile: output,
    bundle: true, format: 'esm', platform: 'node', jsx: 'automatic',
    plugins: [{ name: 'waiting-keeper', setup(builder) {
        builder.onResolve({ filter: /^(react|\.\/useChatPilot|\.\/useSharedChatUnread|\.\/sendQueue|\.\/chatWaitingBadge)$/ },
            ({ path }) => ({ path, namespace: 'keeper' }));
        builder.onLoad({ filter: /.*/, namespace: 'keeper' }, ({ path }) => ({ loader: 'js', contents: {
            react: 'const h=()=>globalThis.__keeper; export const useEffect=(...v)=>h().useEffect(...v), useRef=(...v)=>h().useRef(...v);',
            './useChatPilot': 'export default (props)=>globalThis.__keeper.pilot(props);',
            './useSharedChatUnread': 'export default (props)=>globalThis.__keeper.unread(props);',
            './sendQueue': 'export const useOutbox=()=>globalThis.__keeper.outbox;',
            './chatWaitingBadge': `export const publishWaiting=(...v)=>globalThis.__keeper.published.push(v);
                export const useSectionCountsWaiting=()=>globalThis.__keeper.sectionCounts;`,
        }[path] }));
    } }],
});
const { default: Keeper } = await import(pathToFileURL(output));

function keeper(props = {}) {
    const slots = [];
    let cursor = 0;
    const h = {
        published: [], outbox: [{ clientMessageId: 'x' }], sectionCounts: false, pilotProps: null, unreadProps: null,
        pilotState: { enabled: false, capability: null, connection: 'connecting' },
        unreadState: { ready: false, total: 0, items: {}, apply(changes) { h.applied.push(...changes); }, refresh: async () => {} },
        applied: [],
        useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
        useEffect(effect, deps) {
            const i = cursor++, old = slots[i];
            if (old && deps.every((value, k) => Object.is(value, old.deps[k]))) return;
            old?.cleanup?.();
            slots[i] = { deps, cleanup: effect() };
        },
        pilot(options) { h.pilotProps = options; return h.pilotState; },
        unread(options) { h.unreadProps = options; return h.unreadState; },
        render(next = {}) {
            Object.assign(props, next);
            globalThis.__keeper = h; cursor = 0;
            Keeper({ apiBaseUrl: '/api-base', withAccessTokenHeader: () => ({ Authorization: 'Bearer t' }), mayProcess: true, ...props });
        },
        unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
    };
    return h;
}

test('сторож: поток только счётчика у тех, кто обрабатывает чаты, и тишина, пока раздел считает сам', () => {
    const h = keeper();
    h.render();
    assert.equal(h.pilotProps.only, 'unread');
    assert.deepEqual([h.pilotProps.account, h.pilotProps.active, h.pilotProps.mayProcess, h.pilotProps.selected],
        ['op', true, true, null]);
    assert.deepEqual(h.pilotProps.headers(), { Authorization: 'Bearer t' });
    assert.equal(h.unreadProps.enabled, true);
    assert.equal(h.unreadProps.outbox, h.outbox, 'свой ответ из раздела гасит ожидание и здесь');
    // Изменения потока — в счётчик, и ни одна панель не перечитывается.
    const change = { kind: 'unread', channelId: 'c', chatId: '1', unreadCount: 1, unreadVersion: 2 };
    assert.deepEqual(h.pilotProps.onChanges([change]), { thread: false, list: false, notes: false });
    assert.deepEqual(h.applied, [change]);
    assert.equal(h.pilotProps.refreshUnread, h.unreadState.refresh);
    for (const name of ['refreshThread', 'refreshList', 'refreshNotes', 'refreshCards']) {
        assert.equal(h.pilotProps[name](), undefined, name);
    }
    assert.deepEqual(h.published, [], 'до снимка числа нет');
    // Поток есть, а снимка счётчика ещё нет — ноль не публикуется: после ухода из
    // раздела бейдж держит его последнее число, а не мигает пустотой.
    h.pilotState = { enabled: true, capability: { enabled: true }, connection: 'live' };
    h.render();
    assert.deepEqual(h.published, []);
    h.unreadState = { ...h.unreadState, ready: true, total: 12 };
    h.render();
    assert.deepEqual(h.published, [[12, 'keeper']]);
    h.unreadState = { ...h.unreadState, total: 3 };
    h.render();
    assert.deepEqual(h.published.at(-1), [3, 'keeper']);
    // Раздел открыт на «Чатах» — сторож без потока и без записей.
    h.sectionCounts = true;
    h.render();
    assert.deepEqual([h.pilotProps.active, h.unreadProps.enabled], [false, false]);
    h.unreadState = { ...h.unreadState, total: 9 };
    h.render();
    assert.deepEqual(h.published.at(-1), [3, 'keeper']);
    h.unmount();
    assert.deepEqual(h.published.at(-1), [null, 'keeper'], 'ушёл человек — числа сторожа нет');
});

test('сторож: обработка не открыта или закрыта посреди работы — числа нет; без права — ни потока, ни снимка', () => {
    const denied = keeper();
    denied.pilotState = { enabled: false, capability: { enabled: false }, connection: 'paused' };
    denied.render();
    assert.deepEqual(denied.published, [[null, 'keeper']]);
    const revoked = keeper();
    revoked.pilotState = { enabled: true, capability: { enabled: true }, connection: 'live' };
    revoked.unreadState = { ...revoked.unreadState, ready: true, total: 5 };
    revoked.render();
    revoked.pilotState = { enabled: false, capability: null, connection: 'unavailable' };
    revoked.render();
    assert.deepEqual(revoked.published, [[5, 'keeper'], [null, 'keeper']]);
    const viewer = keeper({ mayProcess: false });
    viewer.render();
    assert.deepEqual([viewer.pilotProps.active, viewer.unreadProps.enabled], [false, false]);
});

test('раздел: на «Чатах» аккаунта «Верификаторы» держит счётчик сам и отдаёт его боковой панели', () => {
    const view = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8').replace(/\r\n/g, '\n');
    assert.match(view, /import \{ claimSectionWaiting, publishWaiting \} from '\.\/chatWaitingBadge';/);
    assert.match(view, /const countsWaiting = account === 'op' && pilot\.enabled && mainTab === 'chats';\n\s*useEffect\(\(\) => \(countsWaiting \? claimSectionWaiting\(\) : undefined\), \[countsWaiting\]\);/);
    assert.match(view, /if \(countsWaiting && unread\.ready\) publishWaiting\(unread\.total\);\n\s*\}, \[countsWaiting, unread\.ready, unread\.total\]\);/);
});

test('боковая панель: бейдж во всех трёх ветках меню, перерисовка по числу, сторож — только у обрабатывающих', () => {
    const app = readFileSync('src/App.jsx', 'utf8').replace(/\r\n/g, '\n');
    // Пункт «Чаты ОП» есть в трёх ветках меню — бейдж обязан быть в каждой.
    const calls = app.split('{renderWazzupChatsSidebarInner()}').length - 1;
    assert.equal(calls, 3);
    assert.equal(app.split('<span className="sidebar-text">Чаты ОП</span>').length - 1, 0, 'ни одной ветки без бейджа');
    assert.equal(app.split("handleSidebarViewNavigation(e, 'wazzup_chats')").length - 1, 3);
    for (const piece of app.split("handleSidebarViewNavigation(e, 'wazzup_chats')").slice(1)) {
        const button = piece.slice(0, piece.indexOf('</button>'));
        assert.match(button, /title=\{wazzupChatsSidebarTitle\}/);
        // Уголок свёрнутой панели — absolute от кнопки.
        assert.match(button, /className=\{`relative w-full/);
    }
    const helper = app.slice(app.indexOf('const renderWazzupChatsSidebarInner = '), app.indexOf('const renderEventsSidebarItemInner = '));
    assert.equal(helper.split('waitingBadgeLabel(wazzupWaitingCount)').length - 1, 2, 'и в подписи, и в свёрнутой панели — «9+»');
    assert.match(helper, /sidebar-surveys-collapsed-badge/);
    assert.match(helper, /wazzupWaitingCount > 0\n\s*\? `Ждут ответа: \$\{wazzupWaitingCount\}` : undefined;/);
    assert.ok(app.includes('crmUnreadCount,\n                wazzupWaitingCount,\n                canAccessCrmSection,'),
        'число — в зависимостях меню');
    assert.match(app, /const wazzupWaitingCount = useChatWaitingCount\(\);/);
    assert.match(app, /\{user && \(isWazzupChatOperator\(user\) \|\| normalizeRole\(user\.role\) === 'super_admin'\)\n\s*&& canAccessVerifierChatsForUser\(user\) && \(\n\s*<Suspense fallback=\{null\}>\n\s*<WazzupWaitingKeeper\n\s*key=\{user\.id\}/);
    assert.match(app, /const WazzupWaitingKeeper = lazyWithRetry\(\(\) => import\('\.\/components\/wazzup\/WazzupWaitingKeeper'\)\);/);
});
