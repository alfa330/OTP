/*
 * Тумблер «Уведомления о доступе» в разделе «Сессии» (владелец, 24.11.2026).
 *
 * Сообщение «Открыт доступ к чувствительным данным» приходит в Telegram только
 * тем админам, кто сам включил тумблер; по умолчанию он выключен. Здесь:
 *   • телефонная раскладка рисуется НАСТОЯЩЕЙ (esbuild + react-dom/server) —
 *     строка тумблера, его состояние и предупреждение «Telegram не привязан»;
 *   • проводка в App.jsx: тумблер читается своей ручкой один раз при входе, а не
 *     с каждым ответом списка, и стоит в обеих раскладках.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const outfile = join(cache, 'SessionsMobileViewNotify.cjs');
buildSync({
    entryPoints: ['src/components/sessions/SessionsMobileView.jsx'], outfile,
    bundle: true, platform: 'node', format: 'cjs', packages: 'external',
    loader: { '.css': 'empty' }, logLevel: 'silent',
});
const require = createRequire(import.meta.url);
const SessionsMobileView = require(outfile).default;

const LABEL = 'Уведомления о доступе';
const NO_TELEGRAM = 'Telegram не привязан — уведомления не придут.';

const render = (props = {}) => renderToStaticMarkup(React.createElement(SessionsMobileView, {
    people: [], totalPeople: 3, totalSessions: 5, matchedPeople: 3, sensitivePeople: 0,
    deviceCounts: {}, deviceOrder: [], deviceLabels: {},
    DeviceIcon: () => null, Avatar: () => null,
    search: '', onSearch: () => {}, roleFilter: 'all', onRole: () => {},
    deviceFilter: 'all', onDevice: () => {}, departmentFilter: 'all', departmentOptions: [],
    canPickDepartment: false, onDepartment: () => {}, sortKey: 'last_seen_at', sortDir: 'desc',
    onSort: () => {}, hasFilters: false, onResetFilters: () => {}, isLoading: false,
    isLoadingMore: false, hasMore: false, onLoadMore: () => {}, onRefresh: () => {},
    onOpen: () => {}, selected: new Map(), onToggle: () => {}, onToggleAll: () => {},
    onClearSelection: () => {}, allSelected: false, selectedSessionsCount: 0,
    bulkRevoking: false, onBulkRevoke: () => {},
    notifyLabel: LABEL, notifyHint: 'Пояснение', notifyNoTelegram: NO_TELEGRAM,
    onToggleNotify: () => {},
    ...props,
}));

const switchOf = (html) => (html.match(/<button[^>]*role="switch"[^>]*>/) || [null])[0];

test('пока состояние не прочитано, строки тумблера нет', () => {
    const html = render({ notify: null });
    assert.doesNotMatch(html, /ses-m-notify/);
    assert.equal(switchOf(html), null);
});

test('выключенный тумблер: подпись, «i» и переключатель в положении «выкл»', () => {
    const html = render({ notify: { enabled: false, telegram_linked: true } });
    assert.match(html, /class="ses-m-notify"/);
    assert.ok(html.includes(LABEL));
    assert.match(html, /aria-label="Что за уведомления"/, 'пояснение — под «i», а не строкой');
    const toggle = switchOf(html);
    assert.ok(toggle, 'переключателя нет');
    assert.match(toggle, /aria-checked="false"/);
    assert.match(toggle, new RegExp(`aria-label="${LABEL}"`));
    assert.doesNotMatch(toggle, /disabled/);
    assert.ok(!html.includes(NO_TELEGRAM));
});

test('включённый без Telegram — предупреждение, что сообщения не придут', () => {
    const html = render({ notify: { enabled: true, telegram_linked: false } });
    assert.match(switchOf(html), /aria-checked="true"/);
    assert.ok(html.includes(NO_TELEGRAM));
});

test('без Telegram, но выключенный — предупреждать не о чем', () => {
    assert.ok(!render({ notify: { enabled: false, telegram_linked: false } }).includes(NO_TELEGRAM));
    assert.ok(!render({ notify: { enabled: true, telegram_linked: true } }).includes(NO_TELEGRAM));
});

test('пока запрос в пути, переключатель не нажать второй раз', () => {
    const html = render({ notify: { enabled: true, telegram_linked: true }, notifyBusy: true });
    assert.match(switchOf(html), /disabled=""/);
});

test('строка стоит над поиском', () => {
    const html = render({ notify: { enabled: false, telegram_linked: true } });
    assert.ok(html.indexOf('ses-m-notify') < html.indexOf('ses-m-search'));
});

/* ── Проводка в App.jsx ─────────────────────────────────────────────────── */

const app = readFileSync(join(process.cwd(), 'src', 'App.jsx'), 'utf8');
const panel = app.slice(app.indexOf('const SessionsPanel = ({'), app.indexOf('const _SkPulse'));

/* Настоящие обработчики тумблера: вырезаются из App.jsx и исполняются с
   подменой axios, setState и тостов — проверяется поведение, а не строки. */
const callback = (name) => {
    const head = `const ${name} = React.useCallback(`;
    const start = panel.indexOf(head);
    assert.ok(start > 0, `${name} не найден — проверь тест`);
    const from = start + head.length;
    const end = panel.indexOf('\n        }, [', from);
    assert.ok(end > from, `конец ${name} не найден — проверь тест`);
    const deps = panel.slice(end + '\n        }, ['.length, panel.indexOf(']);', end)).trim();
    return { source: panel.slice(from, end + '\n        }'.length), deps };
};

const evaluate = (source, scope) => {
    const names = Object.keys(scope);
    return new Function(...names, `return (${source});`)(...names.map((name) => scope[name]));
};

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

const deferred = () => {
    let resolve;
    const promise = new Promise((ok) => { resolve = ok; });
    return { promise, resolve };
};

const toggleHarness = ({ initial = { enabled: false, telegram_linked: true }, put, get } = {}) => {
    const h = { state: initial, sets: 0, busy: [], toasts: [], puts: [], gets: [], ref: { current: 0 } };
    const scope = {
        axios: {
            put: (url, body, config) => { h.puts.push({ url, body, config }); return put(); },
            get: (url, config) => { h.gets.push({ url, config }); return get(); },
        },
        API_BASE_URL: 'https://api.test',
        notifyUserId: 7,
        notifyRequestRef: h.ref,
        setNotify: (value) => { h.sets += 1; h.state = typeof value === 'function' ? value(h.state) : value; },
        setNotifyBusy: (value) => { h.busy.push(value); },
        emitAppToast: (message, type) => { h.toasts.push({ message, type }); },
    };
    h.toggle = evaluate(callback('toggleNotify').source, scope);
    h.load = evaluate(callback('loadNotify').source, scope);
    return h;
};

test('тумблер читается своей ручкой при входе, а не с ответом списка', () => {
    assert.ok(panel.length > 1000, 'SessionsPanel не найден — проверь тест');
    // Чтение зависит только от того, кто смотрит: от поиска и фильтров — нет,
    // иначе ответ списка, ушедший до щелчка, возвращал бы тумблер назад.
    assert.equal(callback('loadNotify').deps, 'notifyUserId');
    assert.equal(callback('toggleNotify').deps, 'notifyUserId');
    assert.match(panel, /React\.useEffect\(\(\) => \{\s*loadNotify\(\);\s*return \(\) => \{ notifyRequestRef\.current \+= 1; \};\s*\}, \[loadNotify\]\);/);

    const listFetch = app.slice(app.indexOf('const fetchAdminSessions = useCallback'),
        app.indexOf('const refreshAdminSessions = useCallback'));
    assert.ok(listFetch.length > 200, 'загрузка списка не найдена — проверь тест');
    assert.ok(!listFetch.includes('notify'), 'список сессий не должен нести тумблер');
});

test('чтение: состояние — из ответа сервера, своей ручкой', async () => {
    const h = toggleHarness({ initial: null,
        get: () => Promise.resolve({ data: { status: 'success', enabled: true, telegram_linked: false } }) });
    h.load();
    await tick();
    assert.equal(h.gets[0].url, 'https://api.test/api/admin/sessions/notify');
    assert.equal(h.gets[0].config.headers['X-User-Id'], 7);
    assert.deepEqual(h.state, { enabled: true, telegram_linked: false });
});

test('чтение не удалось — тумблер не выдумывается «выключенным» и тостом не шумит', async () => {
    for (const get of [() => Promise.reject(new Error('сеть')),
        () => Promise.resolve({ data: { status: 'error', error: 'Forbidden' } })]) {
        const h = toggleHarness({ initial: null, get });
        h.load();
        await tick();
        assert.equal(h.state, null);
        assert.equal(h.sets, 0);
        assert.deepEqual(h.toasts, []);
    }
});

test('щелчок: на сервер — настоящий bool, на экране — ответ сервера, тумблер занят на время запроса', async () => {
    const h = toggleHarness({
        put: () => Promise.resolve({ data: { status: 'success', enabled: true, telegram_linked: false } }) });
    await h.toggle(true);
    assert.equal(h.puts.length, 1);
    assert.equal(h.puts[0].url, 'https://api.test/api/admin/sessions/notify');
    assert.deepEqual(h.puts[0].body, { enabled: true });
    assert.equal(h.puts[0].config.headers['X-User-Id'], 7);
    assert.deepEqual(h.state, { enabled: true, telegram_linked: false }, 'правда — в ответе сервера');
    assert.deepEqual(h.busy, [true, false]);
    assert.deepEqual(h.toasts, []);
});

test('ошибка: тумблер возвращается назад, тост — свой, по-русски, без английского ответа сервера', async () => {
    const failures = [
        () => Promise.reject({ response: { status: 500, data: { error: 'Internal server error' } } }),
        () => Promise.reject({ response: { status: 403, data: { error: 'Forbidden: only admins can access' } } }),
        () => Promise.reject(new Error('Network Error')),
        () => Promise.resolve({ data: { status: 'error', error: 'Unauthorized' } }),
    ];
    for (const put of failures) {
        const h = toggleHarness({ put });
        await h.toggle(true);
        assert.deepEqual(h.state, { enabled: false, telegram_linked: true });
        assert.deepEqual(h.toasts, [{ message: 'Не удалось переключить уведомления', type: 'error' }]);
        assert.deepEqual(h.busy, [true, false]);
    }
});

test('опоздавшее чтение не перебивает щелчок', async () => {
    const slowGet = deferred();
    const h = toggleHarness({
        get: () => slowGet.promise,
        put: () => Promise.resolve({ data: { status: 'success', enabled: true, telegram_linked: true } }) });
    h.load();
    await h.toggle(true);
    slowGet.resolve({ data: { status: 'success', enabled: false, telegram_linked: true } });
    await tick();
    assert.deepEqual(h.state, { enabled: true, telegram_linked: true });
});

test('«Обновить»: список — всегда, тумблер — только если прошлое чтение не удалось', () => {
    const { source, deps } = callback('refreshPanel');
    assert.equal(deps, 'fetchAdminSessions, loadNotify, notify');
    for (const [notify, reread] of [[null, 1], [{ enabled: false, telegram_linked: true }, 0]]) {
        const calls = { list: 0, notify: 0 };
        evaluate(source, {
            fetchAdminSessions: () => { calls.list += 1; },
            loadNotify: () => { calls.notify += 1; },
            notify,
        })();
        assert.deepEqual(calls, { list: 1, notify: reread });
    }
    // И кнопка на компьютере, и круглая на телефоне идут через неё.
    assert.match(panel, /<button onClick=\{refreshPanel\} disabled=\{isAdminSessionsLoading\}/);
    assert.ok(!panel.includes('onClick={fetchAdminSessions}'));
    const mobile = panel.slice(panel.indexOf('<SessionsMobileView'));
    assert.ok(mobile.slice(0, mobile.indexOf('/>')).includes('onRefresh={refreshPanel}'));
});

test('тумблер есть в обеих раскладках', () => {
    // Компьютер: в шапке раздела, рядом с «Обновить», пока состояние прочитано.
    assert.match(panel, /\{notify && \(\s*<div className="inline-flex items-center gap-2 rounded-lg/);
    assert.match(panel, /<IosToggle checked=\{notify\.enabled\} disabled=\{notifyBusy\}\s+onChange=\{toggleNotify\}/);
    assert.match(panel, /\{notify\?\.enabled && !notify\.telegram_linked && \(/);
    // Телефон: те же состояние и обработчик уходят в свою раскладку…
    const mobile = panel.slice(panel.indexOf('<SessionsMobileView'));
    for (const prop of ['notify={notify}', 'notifyBusy={notifyBusy}', 'onToggleNotify={toggleNotify}',
        'notifyLabel={SESSION_NOTIFY_LABEL}', 'notifyHint={SESSION_NOTIFY_HINT}',
        'notifyNoTelegram={SESSION_NOTIFY_NO_TELEGRAM}']) {
        assert.ok(mobile.slice(0, mobile.indexOf('/>')).includes(prop), `телефону не передан ${prop}`);
    }
    // …и раскладка подключает к ним сам переключатель.
    const view = readFileSync(join(process.cwd(), 'src', 'components', 'sessions', 'SessionsMobileView.jsx'), 'utf8');
    assert.match(view, /<IosToggle checked=\{notify\.enabled\} disabled=\{notifyBusy\}\s+onChange=\{onToggleNotify\} ariaLabel=\{notifyLabel\} \/>/);
});
