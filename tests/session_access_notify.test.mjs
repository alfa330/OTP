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

test('состояние читается своей ручкой один раз, а не с ответом списка', () => {
    assert.ok(panel.length > 1000, 'SessionsPanel не найден — проверь тест');
    const read = panel.indexOf("axios.get(`${API_BASE_URL}/api/admin/sessions/notify`");
    assert.ok(read > 0, 'тумблер не читает своё состояние');
    // Эффект зависит только от того, кто смотрит: от поиска и фильтров — нет,
    // иначе ответ списка, ушедший до щелчка, возвращал бы тумблер назад.
    const deps = panel.slice(read).match(/\}, \[([^\]]*)\]\);/);
    assert.ok(deps, 'зависимости эффекта не найдены — проверь тест');
    assert.equal(deps[1].trim(), 'notifyUserId');

    const listFetch = app.slice(app.indexOf('const fetchAdminSessions = useCallback'),
        app.indexOf('const refreshAdminSessions = useCallback'));
    assert.ok(listFetch.length > 200, 'загрузка списка не найдена — проверь тест');
    assert.ok(!listFetch.includes('notify'), 'список сессий не должен нести тумблер');
});

test('щелчок шлёт настоящий bool и откатывает тумблер при ошибке', () => {
    const toggle = panel.slice(panel.indexOf('const toggleNotify = React.useCallback'));
    const body = toggle.slice(0, toggle.indexOf('}, [notifyUserId]);'));
    assert.ok(body.length > 100, 'toggleNotify не найден — проверь тест');
    assert.match(body, /axios\.put\(\s*`\$\{API_BASE_URL\}\/api\/admin\/sessions\/notify`,\s*\{ enabled: next \}/);
    assert.match(body, /catch \(err\) \{\s*setNotify\(\(prev\) => \(prev \? \{ \.\.\.prev, enabled: !next \} : prev\)\);/);
    assert.match(body, /emitAppToast\(/, 'ошибка обязана быть видна');
});

test('тумблер есть в обеих раскладках', () => {
    // Компьютер: в шапке раздела, рядом с «Обновить».
    assert.match(panel, /<IosToggle checked=\{notify\.enabled\} disabled=\{notifyBusy\}\s+onChange=\{toggleNotify\}/);
    assert.match(panel, /\{notify\?\.enabled && !notify\.telegram_linked && \(/);
    // Телефон: те же состояние и обработчик уходят в свою раскладку.
    const mobile = panel.slice(panel.indexOf('<SessionsMobileView'));
    for (const prop of ['notify={notify}', 'notifyBusy={notifyBusy}', 'onToggleNotify={toggleNotify}',
        'notifyLabel={SESSION_NOTIFY_LABEL}', 'notifyHint={SESSION_NOTIFY_HINT}',
        'notifyNoTelegram={SESSION_NOTIFY_NO_TELEGRAM}']) {
        assert.ok(mobile.slice(0, mobile.indexOf('/>')).includes(prop), `телефону не передан ${prop}`);
    }
});
