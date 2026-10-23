import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    CRM_USER_KEY, EMBED_API_BASE_URL, EMBED_SCOPE, LAUNCH_SID_KEY, canKeepSession, isEmbedApiAllowed,
    readEmbedLaunch, reloadPending, runEmbedLaunch, stripLaunchParams,
} from '../src/components/wazzup/embedLaunch.js';

/* Вход в окно «Чатов ОП» в CRM без пароля (владелец, 20.10.2026). */

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const CODE = 'c'.repeat(43);
const SIGNED = `crmExp=1790003600&crmSig=${'a'.repeat(64)}`;
const ADDRESS = `?view=wazzup_chats&embed=1&launch=${CODE}&uid=42&crmUserId=352&${SIGNED}`;
const jwtWith = (payload) => `h.${Buffer.from(JSON.stringify(payload)).toString('base64url')}.s`;
const future = Math.floor(Date.now() / 1000) + 3600;
const launchRefresh = jwtWith({ sub: '42', sid: 's-1', scp: EMBED_SCOPE, exp: future });

const memory = (entries = {}) => {
    const map = new Map(Object.entries(entries));
    return { map, getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, String(v)),
        removeItem: (k) => map.delete(k) };
};

const fakeWindow = ({ search = ADDRESS, stored = {}, reply } = {}) => {
    const calls = [];
    const win = {
        location: { search, href: `https://portal.example.test/OTP/${search}`, reload: () => calls.push(['reload']) },
        history: { state: null, replaceState: (_s, _t, url) => calls.push(['replace', url]) },
        localStorage: memory(stored), sessionStorage: memory(),
        document: { documentElement: { style: {} } },
        fetch: async (url, options) => {
            calls.push(['fetch', url, options]);
            if (url.endsWith('/api/logout')) return { ok: true, json: async () => ({}) };
            return reply();
        },
    };
    const logouts = () => calls.filter(([kind, url]) => kind === 'fetch' && url.endsWith('/api/logout'))
        .map(([, , options]) => options.headers.Authorization);
    return { win, calls, logouts };
};

test('ссылка окна: код, учётка и подписанный пользователь CRM из адреса, мусор отсекается', () => {
    assert.deepEqual(readEmbedLaunch(ADDRESS), { code: CODE, uid: '42', crmUserId: '352' });
    assert.deepEqual(readEmbedLaunch(`?embed=1&launch=${CODE}&uid=x`), { code: CODE, uid: '', crmUserId: '' });
    assert.deepEqual(readEmbedLaunch('?embed=1&crmUserId=352'), { code: '', uid: '', crmUserId: '' },
        'без подписи iCORE пользователь CRM не считается');
    assert.deepEqual(readEmbedLaunch(`?embed=1&uid=42&crmUserId=352&${SIGNED}`), { code: '', uid: '', crmUserId: '352' });
    assert.equal(readEmbedLaunch(`?launch=${CODE}`), null, 'не окно — не вход');
    assert.equal(readEmbedLaunch('?embed=1&launch=short').code, '');
    assert.equal(readEmbedLaunch('?embed=1&launch=bad code with spaces and more symbols!!').code, '');
    assert.equal(stripLaunchParams(`https://p.test/OTP/?view=wazzup_chats&embed=1&launch=${CODE}&uid=42&crmUserId=7`),
        '/OTP/?view=wazzup_chats&embed=1&crmUserId=7');
});

test('остаётся только живая сессия по ссылке того же человека; сессия входа паролем — нет', () => {
    assert.equal(canKeepSession(launchRefresh, '42', 's-1'), true);
    assert.equal(canKeepSession(launchRefresh, '43', 's-1'), false);
    assert.equal(canKeepSession(launchRefresh, '42', ''), false, 'вход паролем в окне — сменить на сессию без QR');
    assert.equal(canKeepSession(launchRefresh, '42', 's-2'), false);
    assert.equal(canKeepSession(jwtWith({ sub: '42', sid: 's-1', exp: future }), '42', 's-1'), false);
    assert.equal(canKeepSession(jwtWith({ sub: '42', sid: 's-1', scp: EMBED_SCOPE, exp: 1 }), '42', 's-1'), false);
    assert.equal(canKeepSession('', '42', 's-1'), false);
});

test('в окне запросы — только чаты, вход и выход; чужие адреса не трогаем', () => {
    const api = EMBED_API_BASE_URL;
    for (const path of ['/api/wazzup/chats?q=1', '/api/auth/me', '/api/auth/refresh', '/api/login', '/api/logout']) {
        assert.equal(isEmbedApiAllowed(`${api}${path}`), true, path);
    }
    for (const path of ['/api/surveys/pending_count', '/api/birthdays/today', '/api/admin/users']) {
        assert.equal(isEmbedApiAllowed(`${api}${path}`), false, path);
    }
    assert.equal(isEmbedApiAllowed('https://cdn.example.test/emoji.png'), true);
    assert.match(read('src/App.jsx'), new RegExp(`const API_BASE_URL = '${EMBED_API_BASE_URL.replace(/\./g, '\\.')}';`),
        'адрес API — тот же, что у портала');
});

test('без кода и без пользователя CRM — ничего не делаем', async () => {
    const { win, calls } = fakeWindow({ search: '?view=wazzup_chats&embed=1',
        stored: { otp_refresh_token: launchRefresh } });
    assert.equal(await runEmbedLaunch({ win }), null);
    assert.deepEqual(calls, []);
    assert.equal(win.localStorage.getItem('otp_refresh_token'), launchRefresh);
});

test('общий компьютер: окно другого пользователя CRM не оставляет сессию прежнего — даже без кода', async () => {
    const stored = { otp_access_token: 'a-access', otp_refresh_token: launchRefresh, user: '{"id":42}',
        [CRM_USER_KEY]: '352', [LAUNCH_SID_KEY]: 's-1' };
    // Пользователь 401 не найден по ФИО: адрес без кода, только его подпись.
    const other = fakeWindow({ search: `?view=wazzup_chats&embed=1&crmUserId=401&${SIGNED}`, stored,
        reply: () => assert.fail('обмена быть не должно') });
    assert.equal(await runEmbedLaunch({ win: other.win }), 'switched');
    for (const key of ['otp_access_token', 'otp_refresh_token', 'user', LAUNCH_SID_KEY]) {
        assert.equal(other.win.localStorage.getItem(key), null, key);
    }
    assert.equal(other.win.localStorage.getItem(CRM_USER_KEY), '401', 'новая сессия окна — уже его');
    assert.deepEqual(other.logouts(), ['Bearer a-access'], 'сессию прежнего отзываем');
    // Тот же пользователь CRM — сессия остаётся.
    const same = fakeWindow({ search: `?view=wazzup_chats&embed=1&crmUserId=352&${SIGNED}`, stored });
    assert.equal(await runEmbedLaunch({ win: same.win }), null);
    assert.equal(same.win.localStorage.getItem('otp_refresh_token'), launchRefresh);
});

test('своя живая сессия по ссылке — оставляем, код только убираем из адреса', async () => {
    const { win, calls } = fakeWindow({
        stored: { otp_refresh_token: launchRefresh, [CRM_USER_KEY]: '352', [LAUNCH_SID_KEY]: 's-1' },
        reply: () => assert.fail('обмена быть не должно') });
    assert.equal(await runEmbedLaunch({ win }), 'kept');
    assert.deepEqual(calls, [['replace', `/OTP/?view=wazzup_chats&embed=1&crmUserId=352&${SIGNED}`]]);
});

test('код меняется на сессию: токены в хранилище портала, прежняя сессия отозвана, окно перезагружено', async () => {
    const user = { id: 42, name: 'Иванова Айгерим' };
    const fresh = jwtWith({ sub: '42', sid: 's-9', scp: EMBED_SCOPE, exp: future });
    // Прежняя — сессия входа паролем того же человека: её меняем на сессию без QR.
    const { win, calls, logouts } = fakeWindow({
        stored: { otp_access_token: 'old-access', otp_refresh_token: jwtWith({ sub: '42', sid: 's-1', scp: EMBED_SCOPE, exp: future }),
            user: '{"id":42}', [CRM_USER_KEY]: '352' },
        reply: () => ({ ok: true, json: async () => ({ access_token: 'new-a', refresh_token: fresh, user }) }),
    });
    const pending = runEmbedLaunch({ win });
    // До ответа сервера прежних токенов в хранилище нет: портал не поднимет старую сессию.
    assert.equal(win.localStorage.getItem('otp_refresh_token'), null);
    assert.equal(win.document.documentElement.style.visibility, 'hidden');
    assert.equal(await pending, 'exchanged');
    assert.equal(reloadPending('exchanged'), true);
    assert.equal(win.localStorage.getItem('otp_access_token'), 'new-a');
    assert.equal(win.sessionStorage.getItem('otp_refresh_token'), fresh);
    assert.equal(win.localStorage.getItem('otp_auth_transport'), 'bearer');
    assert.equal(win.localStorage.getItem(LAUNCH_SID_KEY), 's-9', 'эту сессию в следующий раз можно оставить');
    assert.deepEqual(JSON.parse(win.localStorage.getItem('user')), user);
    const exchange = calls.find(([kind, url]) => kind === 'fetch' && url.endsWith('/api/auth/embed-launch'));
    assert.deepEqual(JSON.parse(exchange[2].body), { code: CODE, auth_transport: 'bearer' });
    assert.equal(exchange[2].credentials, 'omit', 'окно живёт без кук');
    assert.deepEqual(logouts(), ['Bearer old-access']);
    assert.ok(calls.some(([kind]) => kind === 'reload'));
});

test('сессия по ссылке, выданная до пометки «из CRM» (23.10), меняется на новую один раз', async () => {
    // Её отметка лежит под прежним ключом: такую сессию сервер не знает как сессию из CRM,
    // и админ в ней остался бы на просмотре. Код из адреса — обменять, прежнюю — отозвать.
    const fresh = jwtWith({ sub: '42', sid: 's-9', scp: EMBED_SCOPE, exp: future });
    const { win, logouts } = fakeWindow({
        stored: { otp_access_token: 'old-access', otp_refresh_token: launchRefresh, [CRM_USER_KEY]: '352',
            otp_embed_launch_sid: 's-1' },
        reply: () => ({ ok: true, json: async () => ({ access_token: 'new-a', refresh_token: fresh, user: { id: 42 } }) }),
    });
    assert.notEqual(LAUNCH_SID_KEY, 'otp_embed_launch_sid');
    assert.equal(await runEmbedLaunch({ win }), 'exchanged');
    assert.equal(win.localStorage.getItem(LAUNCH_SID_KEY), 's-9', 'новая — уже помеченная');
    assert.equal(win.localStorage.getItem('otp_embed_launch_sid'), null, 'прежний ключ убран');
    assert.deepEqual(logouts(), ['Bearer old-access']);
});

test('в окне CRM портал не уводит с чатов и не грузит данные своих разделов', () => {
    const app = read('src/App.jsx');
    const redirect = app.slice(app.indexOf('const redirectToView = useCallback((nextView) => {'));
    assert.match(redirect.slice(0, 600), /if \(CHATS_EMBED && nextView !== 'wazzup_chats'\) return;/);
    // Справочники при входе, данные СВ, разделы рядового и бейдж «Опросы» — только вне окна.
    for (const guard of [
        "if (!user || !user.id || CHATS_EMBED) return;\n\n                if (isAdminLikeRole) {\n                    fetchSvList();",
        'if (!user || !user.id || CHATS_EMBED || !isSupervisorRole(user?.role)) return;\n                fetchSvData(',
        "if (!user || !user.id || CHATS_EMBED || !isRankAndFileRole(currentUserRole) || isScopedDepartmentHead) return;\n\n                if (view === 'profile') {",
        "if (!user || !user.id || CHATS_EMBED || !isRankAndFileRole(currentUserRole) || isScopedDepartmentHead || view === 'shift_auction') return;\n                fetchSurveysPendingBadgeCount();",
    ]) {
        assert.ok(app.includes(guard), guard.split('\n')[0]);
    }
});

test('не вышло — возвращается прежняя сессия той же учётки; чужая — нет, окно покажет вход', async () => {
    const failing = () => ({ ok: false, json: async () => ({ code: 'LAUNCH_INVALID' }) });
    const before = { otp_access_token: 'old-access', otp_refresh_token: launchRefresh, user: '{"id":42}',
        [CRM_USER_KEY]: '352', [LAUNCH_SID_KEY]: 's-1' };
    const restored = fakeWindow({ stored: { ...before, otp_refresh_token: jwtWith({ sub: '42', sid: 's-1', exp: 1 }) },
        reply: failing });
    assert.equal(await runEmbedLaunch({ win: restored.win }), 'restored');
    assert.equal(restored.win.localStorage.getItem('user'), '{"id":42}');
    assert.equal(restored.win.localStorage.getItem(LAUNCH_SID_KEY), 's-1');
    assert.ok(restored.calls.some(([kind]) => kind === 'reload'));
    assert.deepEqual(restored.logouts(), []);
    // Пользователь CRM тот же, но CRM открывает другую учётку: прежнюю не возвращаем.
    const otherAccount = fakeWindow({ search: ADDRESS.replace('uid=42', 'uid=43'), stored: before, reply: failing });
    assert.equal(await runEmbedLaunch({ win: otherAccount.win }), 'failed');
    assert.equal(otherAccount.win.localStorage.getItem('otp_refresh_token'), null);
    assert.deepEqual(otherAccount.logouts(), ['Bearer old-access']);
    const empty = fakeWindow({ reply: failing });
    assert.equal(await runEmbedLaunch({ win: empty.win }), 'failed');
    assert.equal(reloadPending('failed'), false);
    assert.equal(empty.win.document.documentElement.style.visibility, '');
    assert.ok(!empty.calls.some(([kind]) => kind === 'reload'), 'без кода в адресе перезагрузка не зациклится');
});

test('обмен идёт раньше портала: модуль загрузки стоит перед App, отрисовка ждёт обмена', () => {
    const main = read('src/main.jsx');
    assert.ok(main.indexOf("import { embedLaunchDone, reloadPending } from './embedLaunchBoot';")
        < main.indexOf("import App, { ErrorBoundary } from './App';"));
    assert.match(main, /embedLaunchDone\.then\(\(launch\) => \{\n\s*if \(reloadPending\(launch\)\) return;\n\s*root\.render\(/);
    assert.match(read('src/embedLaunchBoot.js'), /export const embedLaunchDone = runEmbedLaunch\(\)\.catch\(\(\) => null\);/);
    const app = read('src/App.jsx');
    assert.match(app, /\.\.\.\(CHATS_EMBED \? \{ embed: true, crm: currentEmbedIdentity\(\) \|\| undefined \} : \{\}\)/,
        'вход паролем в окне — тоже сессия окна, с подписью CRM из адреса');
    assert.match(app, /if \(!isEmbedApiAllowed\(config\?\.url, API_BASE_URL\)\) \{\n\s*throw new axios\.CanceledError/);
});
