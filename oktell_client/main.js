/*
 * iCORE Oktell — клиент АТС с входом по учётке iCORE и обязательными новостями.
 *
 * Зачем он вообще. Оператор сегодня держит на экране веб-клиент Oktell в
 * браузере, а обязательные объявления живут во вкладке портала iCORE. Значит
 * объявление доходит до того, у кого эта вкладка открыта, — то есть не до всех
 * и не тогда, когда нужно. Здесь и клиент АТС, и окно объявления — одно
 * приложение, поэтому «показать и не пустить к работе» перестаёт зависеть от
 * того, куда человек смотрит.
 *
 * Что здесь устроено не очевидно и почему.
 *
 * 1. СТРАНИЦА OKTELL ЖИВЁТ ДОЧЕРНИМ ВИДОМ (WebContentsView), а не в том же
 *    документе, что наш интерфейс. Так окно объявления может убрать её с экрана
 *    целиком одним вызовом — это и есть блокировка работы. Наложить свой div
 *    поверх чужой страницы нельзя: она в другом процессе и про наш DOM не знает.
 *
 * 2. СОСТОЯНИЕ ОПЕРАТОРА БЕРЁМ У САМОЙ СТРАНИЦЫ, а не с нашего сервера. Сервер
 *    вычитывает статусы из Oktell SQL-прокси раз в несколько секунд, прокси
 *    бывает недоступен минутами — за это время разговор успевает начаться и
 *    кончиться. Клиент Oktell получает статус по своему веб-сокету мгновенно,
 *    поэтому правило «не показывать во время разговора» считается в странице
 *    (тот же приём, что в «Ограничителе Перезвона»).
 *
 * 3. ВЕЗДЕ FAIL-OPEN, кроме самой обязательности. Не отдалась учётка кабинета —
 *    пускаем оператора вводить руками, а не запираем его перед пустым экраном.
 *    Не удалось понять состояние — через минуту показываем объявление всё
 *    равно: молчаливо проглоченное объявление хуже показанного не вовремя.
 *    А вот подтверждение обязательного объявления обойти нельзя — этим и
 *    занимается сервер, клиент его решения не дублирует.
 */
const { app, BrowserWindow, WebContentsView, ipcMain, shell } = require('electron');
const fs = require('fs');
const path = require('path');

const SELFTEST = !!process.env.ICORE_OKTELL_SELFTEST;
// Показать окно объявления, не дожидаясь публикации и не заводя звонок:
// нужно, чтобы показать заказчику, как это выглядит у оператора.
const SHOWCASE = !!process.env.ICORE_OKTELL_SHOWCASE;

// ── Конфиг ──────────────────────────────────────────────────────────────────
// Рядом с exe, а не в коде: адрес АТС и адрес портала у стенда и у боя разные,
// и пересобирать программу ради строки никто не станет.
const CONFIG_PATH = process.env.ICORE_OKTELL_CONFIG
    || path.join(__dirname, 'config.json');

const DEFAULT_CONFIG = {
    api_base_url: '',
    oktell: {
        url: '',
        autologin: true,
        selectors: {
            login: "input[name='login'], input#login, input[type='text']",
            password: "input[name='password'], input#password, input[type='password']",
            submit: "button[type='submit'], input[type='submit'], .login-button",
        },
        // Состояния, при которых оператор занят клиентом и окно ждёт. Список
        // догадочный — точные имена состояний Oktell снимаются одним живым
        // звонком (см. README «Ограничителя Перезвона»), поэтому он в конфиге.
        busy_state_strings: ['talk', 'dial', 'call', 'ring'],
        session_keys: ['___oktellsessionid'],
    },
    news: {
        poll_seconds: 60,
        unknown_state_grace_seconds: 60,
    },
};

function deepMerge(base, extra) {
    const out = Array.isArray(base) ? [...base] : { ...base };
    Object.entries(extra || {}).forEach(([key, value]) => {
        if (value && typeof value === 'object' && !Array.isArray(value)
            && base && typeof base[key] === 'object' && !Array.isArray(base[key])) {
            out[key] = deepMerge(base[key], value);
        } else if (value !== undefined) {
            out[key] = value;
        }
    });
    return out;
}

function loadConfig() {
    try {
        const raw = fs.readFileSync(CONFIG_PATH, 'utf8');
        return deepMerge(DEFAULT_CONFIG, JSON.parse(raw));
    } catch (error) {
        // Отсутствующий конфиг — норма первого запуска, а не отказ: экран входа
        // спросит адрес портала сам.
        log(`конфиг не прочитан (${error.message}) — работаем на умолчаниях`);
        return deepMerge(DEFAULT_CONFIG, {});
    }
}

let config = loadConfig();

function log(message) {
    console.log(`[icore-oktell] ${message}`);
}

// ── Состояние процесса ──────────────────────────────────────────────────────
let win = null;
let oktellView = null;
let oktellVisible = false;   // своего getVisible у WebContentsView нет
let auth = null;              // {token, refreshToken, user}
let newsTimer = null;
let newsShown = false;        // окно объявления сейчас на экране
// Состояние оператора глазами страницы Oktell. everSeen отличает «точно
// свободен» от «мы ещё ничего не слышали»: это разные причины не показывать.
let operatorState = { busy: false, everSeen: false, raw: '', since: Date.now() };
let waitingSince = 0;         // когда объявление впервые захотело показаться

const api = (path_) => `${String(config.api_base_url || '').replace(/\/+$/, '')}${path_}`;

// ── Запросы к порталу ───────────────────────────────────────────────────────
async function request(method, path_, { body, retryOn401 = true } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (auth?.token) {
        headers.Authorization = `Bearer ${auth.token}`;
        // Тот же заголовок шлёт CLI задач и телефон: часть ручек читает его
        // вместо разбора токена.
        headers['X-User-Id'] = String(auth.user?.id ?? '');
    }
    const response = await fetch(api(path_), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.status === 401 && retryOn401 && auth?.refreshToken) {
        // Смена длится смену, а токен доступа живёт меньше. Молчаливое
        // обновление здесь важнее красоты: иначе оператор к обеду увидит экран
        // входа посреди разговора.
        const refreshed = await refresh();
        if (refreshed) return request(method, path_, { body, retryOn401: false });
    }
    let payload = null;
    try { payload = await response.json(); } catch (error) { payload = null; }
    return { ok: response.ok, status: response.status, payload };
}

async function login(loginValue, password) {
    const response = await fetch(api('/api/login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            login: loginValue,
            password,
            // Куки нам не подходят: приложение ходит с другого origin, и
            // SameSite их не приложит. Токен держим сами.
            auth_transport: 'bearer',
        }),
    });
    let payload = null;
    try { payload = await response.json(); } catch (error) { payload = null; }
    if (!response.ok || !payload?.access_token) {
        return { ok: false, status: response.status, error: payload?.error || 'Не удалось войти' };
    }
    auth = {
        token: payload.access_token,
        refreshToken: payload.refresh_token || null,
        user: payload.user || null,
    };
    return { ok: true, user: auth.user };
}

async function refresh() {
    if (!auth?.refreshToken) return false;
    try {
        const response = await fetch(api('/api/auth/refresh'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: auth.refreshToken, auth_transport: 'bearer' }),
        });
        if (!response.ok) return false;
        const payload = await response.json();
        if (!payload?.access_token) return false;
        auth = {
            token: payload.access_token,
            refreshToken: payload.refresh_token || auth.refreshToken,
            user: payload.user || auth.user,
        };
        return true;
    } catch (error) {
        log(`обновление токена не удалось: ${error.message}`);
        return false;
    }
}

// ── Окно и страница Oktell ──────────────────────────────────────────────────
function createWindow() {
    win = new BrowserWindow({
        width: 1280,
        height: 860,
        minWidth: 900,
        minHeight: 600,
        show: false,
        backgroundColor: '#f2f2f7',
        title: 'iCORE Oktell',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });
    win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
    win.once('ready-to-show', () => win.show());
    win.on('resize', layoutOktell);
    win.on('closed', () => { win = null; oktellView = null; stopNews(); });
}

// Дочерний вид занимает всё окно. Отдельные координаты не нужны: свой
// интерфейс мы показываем, только когда вид убран, — иначе они дрались бы за
// одни и те же пиксели.
function layoutOktell() {
    if (!win || !oktellView) return;
    // Спрятанный вид не растягиваем: размер схлопнут нарочно (см. showOktell),
    // и обычный resize окна вернул бы его под объявление.
    if (!oktellVisible) return;
    const [width, height] = win.getContentSize();
    oktellView.setBounds({ x: 0, y: 0, width, height });
}

function openOktell(account) {
    // Запоминаем ДО создания вида: preload страницы забирает учётку синхронно
    // на своём старте, то есть раньше, чем сюда вернётся управление.
    if (account) pendingAccount = account;
    const url = String(config.oktell?.url || '').trim();
    if (!url) {
        log('адрес Oktell в конфиге не задан — страницу не открываем');
        return false;
    }
    if (!oktellView) {
        oktellView = new WebContentsView({
            webPreferences: {
                preload: path.join(__dirname, 'preload-oktell.js'),
                // Обёртка window.WebSocket обязана встать ДО скриптов страницы,
                // а из изолированного мира чужой глобальный объект не подменить
                // (см. шапку preload-oktell.js — это поймано прогоном).
                // Node странице при этом не достаётся: nodeIntegration выключен.
                contextIsolation: false,
                sandbox: false,
                nodeIntegration: false,
            },
        });
        win.contentView.addChildView(oktellView);
        // Чужая страница не должна уводить оператора в отдельные окна: всё,
        // что она попробует открыть, отправляем в системный браузер.
        oktellView.webContents.setWindowOpenHandler(({ url: target }) => {
            shell.openExternal(target);
            return { action: 'deny' };
        });
        oktellView.webContents.on('did-fail-load', (_e, code, description) => {
            log(`страница Oktell не загрузилась: ${code} ${description}`);
            send('oktell:failed', { code, description });
        });
    }
    showOktell(true);
    oktellView.webContents.loadURL(url);
    return true;
}

// Учётка и настройки, которые preload страницы забирает синхронно при старте.
// Синхронно — потому что обёртка сокета должна встать раньше скриптов страницы,
// а ответ по обычному ipc приехал бы уже после них.
let pendingAccount = null;

ipcMain.on('oktell:options', (event) => {
    event.returnValue = {
        account: config.oktell?.autologin === false ? null : (pendingAccount || null),
        selectors: config.oktell?.selectors || DEFAULT_CONFIG.oktell.selectors,
        busy: config.oktell?.busy_state_strings || DEFAULT_CONFIG.oktell.busy_state_strings,
    };
});

function showOktell(visible) {
    if (!oktellView) return;
    // setVisible прячет вид целиком: пока обязательное объявление на экране,
    // до страницы АТС не добраться ни мышью, ни клавиатурой. Это и есть
    // «не пустить к работе», и держится оно не на нашем z-index.
    //
    // Размер схлопываем вторым рубежом: если однажды setVisible окажется
    // пустышкой на чужой платформе, вид нулевого размера всё равно не примет
    // ни клика, ни клавиши. Своего getVisible у вида нет (проверено на
    // Electron 33), поэтому состояние держим сами.
    oktellVisible = !!visible;
    oktellView.setVisible(oktellVisible);
    if (oktellVisible) layoutOktell();
    else oktellView.setBounds({ x: 0, y: 0, width: 0, height: 0 });
}

function send(channel, payload) {
    if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
}

// ── Обязательные новости ────────────────────────────────────────────────────
// Правило показа. Ждём, пока оператор занят разговором: окно закрывает собой
// клиент АТС, и посреди консультации это отняло бы у него интерфейс. Но ждём не
// вечно — если состояние так и не пришло (страница другая, вендор переименовал
// поле, сокет не поднялся), через grace-период показываем всё равно.
function canShowNews() {
    if (operatorState.busy) return false;
    if (operatorState.everSeen) return true;
    const grace = Math.max(0, Number(config.news?.unknown_state_grace_seconds ?? 60)) * 1000;
    return waitingSince > 0 && (Date.now() - waitingSince) >= grace;
}

async function pollNews() {
    if (!auth?.token || newsShown) return;
    const { ok, status, payload } = await request('GET', '/api/news/pending');
    if (!ok) {
        if (status === 401) send('auth:expired', {});
        return;
    }
    const items = payload?.items || [];
    if (!items.length) {
        waitingSince = 0;
        return;
    }
    if (!waitingSince) waitingSince = Date.now();
    if (!canShowNews()) {
        log(`объявление ждёт: оператор ${operatorState.busy ? 'в разговоре' : 'в неизвестном состоянии'}`);
        return;
    }
    newsShown = true;
    showOktell(false);
    send('news:show', items[0]);
}

function startNews() {
    stopNews();
    const seconds = Math.max(10, Number(config.news?.poll_seconds ?? 60));
    // Первый заход сразу: объявление могло выйти, пока человек вводил пароль.
    pollNews();
    newsTimer = setInterval(pollNews, seconds * 1000);
}

function stopNews() {
    if (newsTimer) { clearInterval(newsTimer); newsTimer = null; }
}

function closeNews() {
    newsShown = false;
    waitingSince = 0;
    showOktell(true);
    send('news:hide', {});
}

// ── Мост с интерфейсом ──────────────────────────────────────────────────────
ipcMain.handle('app:config', () => ({
    apiBaseUrl: config.api_base_url || '',
    oktellUrl: config.oktell?.url || '',
    configPath: CONFIG_PATH,
}));

ipcMain.handle('auth:login', async (_event, { login: loginValue, password, apiBaseUrl }) => {
    // Адрес портала можно задать прямо на экране входа: у первого запуска
    // конфига может не быть вовсе, а гонять человека в файл — не вариант.
    if (apiBaseUrl) config = deepMerge(config, { api_base_url: apiBaseUrl });
    if (!config.api_base_url) return { ok: false, error: 'Не задан адрес iCORE' };
    const result = await login(String(loginValue || '').trim(), String(password || ''));
    if (!result.ok) return result;

    const account = await request('GET', '/api/operator/oktell_account');
    const cabinet = account.ok ? account.payload?.account : null;
    if (!cabinet) {
        // 409 — учётку ещё не завели; текст пришёл с сервера, его и показываем.
        log(`учётка Oktell не выдана (${account.status})`);
    }
    const opened = openOktell(cabinet);
    startNews();
    return {
        ok: true,
        user: result.user,
        cabinet_login: cabinet?.cabinet_login || '',
        cabinet_error: cabinet ? '' : (account.payload?.error || 'Учётная запись Oktell не выдана'),
        oktell_opened: opened,
    };
});

ipcMain.handle('news:confirm', async (_event, { id, answers }) => {
    const { ok, status, payload } = await request('POST', `/api/news/${id}/read`, {
        body: { answers: answers || undefined },
    });
    if (ok) {
        // Очередь может быть длиннее одной: закрываем окно и сразу спрашиваем
        // следующую, как это делает портал.
        newsShown = false;
        await pollNews();
        if (!newsShown) closeNews();
        return { ok: true };
    }
    return { ok: false, status, ...(payload || {}) };
});

ipcMain.handle('news:quiz', async (_event, { id, answers }) => {
    const { ok, status, payload } = await request('POST', `/api/news/${id}/quiz`, {
        body: { answers: answers || [] },
    });
    return { ok, status, ...(payload || {}) };
});

// Состояние оператора приезжает из страницы Oktell через её preload.
ipcMain.on('oktell:state', (_event, { raw, busy }) => {
    const wasBusy = operatorState.busy;
    operatorState = { busy: !!busy, everSeen: true, raw: String(raw || ''), since: Date.now() };
    if (wasBusy && !busy) {
        // Разговор кончился — это единственный момент, когда ожидающее
        // объявление обязано появиться само, не дожидаясь следующего опроса.
        pollNews();
    }
});

ipcMain.on('oktell:autologin', (_event, { filled, reason }) => {
    autologinSeen = !!filled;
    autologinReason = reason || '';
    log(`подстановка учётки: ${filled ? 'поля заполнены' : `не вышло (${reason || 'причина неизвестна'})`}`);
    send('oktell:autologin', { filled, reason });
});

// ── Самопроверка ────────────────────────────────────────────────────────────
/* Сквозной прогон против заглушки (dev_harness/stub_server.py), без живой руки.
 *
 * Проверяет ровно то, что ломается молча: вход, выдачу учётки, очередь
 * объявлений и ТРИ отказа сервера, о которые клиент обязан спотыкаться, —
 * рано, неверный тест, и только потом успех. Плюс статус оператора: кадры
 * приходят из настоящего веб-сокета поддельной страницы, то есть проверяется
 * сама обёртка, а не наша вера в неё.
 *
 * Верные ответы теста здесь захардкожены нарочно: их знает заглушка, а клиент
 * не знает и знать не должен — в этом и смысл проверки на сервере.
 */
const STUB_ANSWERS = { 9001: 1, 9002: 1 };
const STUB_WRONG = { 9001: 0, 9002: 0 };

function selftest() {
    const results = [];
    const say = (name, passed, extra) => {
        results.push(!!passed);
        console.log(`[selftest] ${name}: ${passed ? 'ок' : 'ПРОВАЛ'}${extra ? ' — ' + extra : ''}`);
    };
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    setTimeout(async () => {
        try {
            say('окно поднято', !!win && win.isVisible());

            const auth_ = await login('stand', 'stand');
            say('вход по учётке iCORE', auth_.ok, auth_.error || '');

            const account = await request('GET', '/api/operator/oktell_account');
            const cabinet = account.payload?.account;
            say('учётка АТС выдана', account.ok && !!cabinet?.cabinet_login,
                cabinet?.cabinet_login || String(account.status));

            const pending = await request('GET', '/api/news/pending');
            const item = pending.payload?.items?.[0];
            say('объявление в очереди', !!item, item?.title || '');
            say('тест приехал без верных ответов',
                Array.isArray(item?.quiz) && item.quiz.length > 0
                && item.quiz.every((q) => q.correct === undefined));

            const early = await request('POST', `/api/news/${item.id}/read`,
                { body: { answers: STUB_ANSWERS } });
            say('раннее подтверждение отвергнуто',
                early.status === 409 && early.payload?.code === 'NEWS_TOO_EARLY',
                `осталось ${early.payload?.remaining_seconds ?? '?'} с`);

            await wait(((early.payload?.remaining_seconds ?? 5) + 1) * 1000);

            const wrong = await request('POST', `/api/news/${item.id}/read`,
                { body: { answers: STUB_WRONG } });
            say('неверный тест отвергнут',
                wrong.status === 409 && wrong.payload?.code === 'NEWS_QUIZ_WRONG');

            const done = await request('POST', `/api/news/${item.id}/read`,
                { body: { answers: STUB_ANSWERS } });
            say('верный тест принят', done.ok);

            const after = await request('GET', '/api/news/pending');
            say('очередь опустела', (after.payload?.items || []).length === 0);

            // Страница АТС и статусы. Загружаем поддельный Oktell и ждём кадры.
            const seen = [];
            const collect = (_event, payload) => seen.push(payload);
            ipcMain.on('oktell:state', collect);
            const opened = openOktell(cabinet);
            say('вид АТС создан', opened === true && !!oktellView);
            await wait(4000);
            say('статус оператора доехал из сокета страницы', seen.length > 0,
                seen.map((s) => `${s.raw}${s.busy ? ' (занят)' : ''}`).join(', '));
            say('подстановка учётки отработала', autologinSeen !== null,
                autologinSeen === true ? 'поля заполнены' : String(autologinReason));

            showOktell(false);
            const hidden = oktellView.getBounds();
            say('вид прячется под объявлением',
                oktellVisible === false && hidden.width === 0 && hidden.height === 0);
            showOktell(true);
            const back = oktellView.getBounds();
            say('вид возвращается', oktellVisible === true && back.width > 0);

            // Правило показа считаем на живом объекте состояния.
            operatorState = { busy: true, everSeen: true, raw: 'talk', since: Date.now() };
            waitingSince = Date.now();
            say('в разговоре объявление ждёт', canShowNews() === false);
            operatorState = { busy: false, everSeen: true, raw: 'usReady', since: Date.now() };
            say('освободился — показываем', canShowNews() === true);
            operatorState = { busy: false, everSeen: false, raw: '', since: Date.now() };
            waitingSince = Date.now();
            say('состояние неизвестно — ждём', canShowNews() === false);
            waitingSince = Date.now() - (Number(config.news.unknown_state_grace_seconds) + 1) * 1000;
            say('неизвестно дольше выдержки — показываем всё равно', canShowNews() === true);
        } catch (error) {
            say('прогон без исключений', false, error.message);
        }
        const failed = results.filter((value) => !value).length;
        console.log(`[selftest] итог: ${results.length - failed} из ${results.length}`);
        setTimeout(() => app.exit(failed ? 1 : 0), 200);
    }, 800);
}

// Итог подстановки нужен самопроверке, а обычной работе — только в журнал.
let autologinSeen = null;
let autologinReason = '';

async function showcase() {
    const ok = await login(process.env.ICORE_OKTELL_LOGIN || 'stand',
                           process.env.ICORE_OKTELL_PASSWORD || 'stand');
    if (!ok.ok) return log(`витрина: войти не удалось (${ok.error})`);
    const pending = await request('GET', '/api/news/pending');
    const item = pending.payload?.items?.[0];
    if (!item) return log('витрина: показывать нечего — очередь пуста');
    newsShown = true;
    send('news:show', item);
    log('витрина: окно объявления показано');
}

app.whenReady().then(() => {
    createWindow();
    if (SELFTEST) selftest();
    // Ждём, пока страница интерфейса подпишется на события, иначе показывать
    // объявление некому.
    else if (SHOWCASE) win.webContents.once('did-finish-load', () => setTimeout(showcase, 400));
});

app.on('window-all-closed', () => app.quit());
