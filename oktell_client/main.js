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
const updater = require('./update');

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
    // Автообновление. Раз в четыре часа — этого хватает: релизы выходят реже, а
    // обязательный доедет ещё и на ближайшем запуске.
    update: {
        check_hours: 4,
    },
    // Снятие оператора с линии на время объявления. Кадр протокола вендор
    // нигде не описывает, поэтому он в конфиге: уточнили — поправили строку, а
    // не пересобрали программу. lunchreasonid = 3 — это «Тренинг» в справочнике
    // подпричин перерыва (1 Тех.причина, 2 Перезвон, 3 Тренинг, 4 Перерыв); у
    // него на табло СЗоВ свой счётчик и свой цвет, поэтому время обучения видно
    // сразу и не путается с обычным перерывом.
    status: {
        enabled: true,
        socket_frames: {
            training: ['setuserstate', { onlunch: true, lunchreasonid: 3 }],
            restore: ['setuserstate', { onlunch: false }],
        },
        // Кнопки статуса в самой странице — запасной путь, если кадр не подошёл.
        selectors: { training: '', restore: '' },
        // Серверная команда АТС. Наш сервер до неё не дотянется — он снаружи
        // офисной сети, — а вот клиент стоит на машине оператора и дотянется.
        // Проверено вживую 17.08.2026: снимает с линии (IsCC=false), но НЕ
        // держится — оператор возвращается сам за минуту-полторы. Поэтому
        // повторяем, пока объявление на экране.
        http_fallback: true,
        reapply_seconds: 30,
        // Сертификат АТС внутри сети бывает самоподписанным. Включать осознанно
        // и только ради адреса самой АТС.
        allow_insecure_tls: false,
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
    win.on('blur', keepFocus);
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

// ── Снятие с линии и запирание экрана ───────────────────────────────────────
/*
 * Пока оператор читает обязательное объявление, он не должен ни получать новые
 * звонки, ни добираться до чего-либо ещё на машине. Это два разных запрета, и
 * держатся они по-разному.
 *
 * ЗВОНКИ снимает сама АТС — мы переводим оператора в перерыв «Тренинг». Двумя
 * путями, потому что ни один не надёжен в одиночку: кадром через живой сокет
 * страницы (быстро, но протокол вендор не описывает) и серверной командой
 * wp_setuserstate (документирована и проверена вживую, но не держится — за
 * минуту-полторы оператор возвращается на линию сам). Поэтому команду
 * ПОВТОРЯЕМ, пока объявление на экране.
 *
 * ЭКРАН запирает окно: киоск, поверх всех, без кнопки в панели задач, и любой
 * увод фокуса возвращается обратно. Честная граница: Ctrl+Alt+Del и
 * переключение пользователя Windows этим не перехватываются — их не
 * перехватывает ни одно приложение без драйвера, и обещать обратное нельзя.
 */
let statusRequestId = 0;
const statusWaiters = new Map();
let reapplyTimer = null;
let lineReleased = false;

function pageStatusCommand(kind) {
    // Ответ страницы ждём не дольше секунды: она может быть занята, не
    // загружена или вообще другой версии, и вешать на этом показ объявления
    // нельзя.
    if (!oktellView) return Promise.resolve({ ok: false, reason: 'страница АТС не открыта' });
    const id = ++statusRequestId;
    const frames = config.status?.socket_frames || {};
    const selectors = config.status?.selectors || {};
    oktellView.webContents.send('oktell:status', {
        id,
        frame: frames[kind] || null,
        selector: selectors[kind] || '',
    });
    return new Promise((resolve) => {
        const timer = setTimeout(() => {
            statusWaiters.delete(id);
            resolve({ ok: false, reason: 'страница не ответила' });
        }, 1000);
        statusWaiters.set(id, (answer) => { clearTimeout(timer); resolve(answer); });
    });
}

ipcMain.on('oktell:status-result', (_event, answer) => {
    const waiter = statusWaiters.get(answer?.id);
    if (!waiter) return;
    statusWaiters.delete(answer.id);
    waiter(answer);
});

async function httpStatusCommand(onLine) {
    if (config.status?.http_fallback === false) return { ok: false, reason: 'запасной путь выключен' };
    const login = pendingAccount?.cabinet_login;
    const base = String(config.oktell?.url || '').trim();
    if (!login || !base) return { ok: false, reason: 'нет логина или адреса АТС' };
    try {
        const url = new URL('wp_setuserstate', base.endsWith('/') ? base : base + '/');
        url.searchParams.set('user', login);
        url.searchParams.set('oncallcenter', onLine ? '1' : '0');
        const response = await fetch(url.toString(), { method: 'GET' });
        return { ok: response.ok, reason: response.ok ? '' : `АТС ответила ${response.status}` };
    } catch (error) {
        return { ok: false, reason: error.message };
    }
}

async function setTraining(on) {
    if (config.status?.enabled === false) return { ok: false, reason: 'смена статуса выключена' };
    const viaPage = await pageStatusCommand(on ? 'training' : 'restore');
    if (viaPage.ok) {
        log(`статус: ${on ? 'тренинг' : 'возврат'} — ${viaPage.how}`);
        lineReleased = on;
        return viaPage;
    }
    const viaHttp = await httpStatusCommand(!on);
    log(`статус: ${on ? 'тренинг' : 'возврат'} — страница не смогла (${viaPage.reason}), `
        + `команда АТС ${viaHttp.ok ? 'прошла' : 'не прошла: ' + viaHttp.reason}`);
    lineReleased = on && viaHttp.ok;
    return viaHttp;
}

function startReapply() {
    stopReapply();
    const seconds = Math.max(5, Number(config.status?.reapply_seconds ?? 30));
    // Повтор ровно потому, что wp_setuserstate не залипает: без него оператор
    // вернётся на линию посреди чтения и получит звонок в закрытое окно.
    reapplyTimer = setInterval(() => { httpStatusCommand(false); }, seconds * 1000);
}

function stopReapply() {
    if (reapplyTimer) { clearInterval(reapplyTimer); reapplyTimer = null; }
}

let locked = false;

function lockScreen(on) {
    if (!win || locked === on) return;
    locked = on;
    if (on) {
        win.setAlwaysOnTop(true, 'screen-saver');
        win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
        win.setSkipTaskbar(true);
        win.setMinimizable(false);
        win.setClosable(false);
        win.setKiosk(true);
        win.show();
        win.focus();
    } else {
        win.setKiosk(false);
        win.setAlwaysOnTop(false);
        win.setSkipTaskbar(false);
        win.setMinimizable(true);
        win.setClosable(true);
    }
}

// Увели фокус — забираем обратно. Без этого Alt+Tab уводит оператора на рабочий
// стол, и объявление превращается в окно, которое просто висит сзади.
function keepFocus() {
    if (!locked || !win || win.isDestroyed()) return;
    win.show();
    win.focus();
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
    const item = items[0];
    // Снимаем с линии и запираем экран ТОЛЬКО под обязательное объявление.
    // Необязательное — это «к сведению»: закрывается крестиком, и уводить ради
    // него человека с линии значило бы терять звонки на ровном месте.
    if (item.is_mandatory) {
        // Порядок важен и он такой: СНАЧАЛА снять с линии, потом показывать.
        // Иначе между появлением окна и сменой статуса есть щель, в которую АТС
        // успевает направить звонок — а окно АТС уже закрыто, и звонок пропадёт.
        await setTraining(true);
        startReapply();
        lockScreen(true);
    }
    // Спрятать страницу АТС приходится в обоих случаях: свой интерфейс рисуется
    // под ней, и другого места показать объявление у нас нет.
    showOktell(false);
    send('news:show', item);
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

async function closeNews() {
    newsShown = false;
    waitingSince = 0;
    stopReapply();
    // Возвращаем прежнее состояние линии только если сами его и забрали: у
    // оператора, который к моменту объявления уже был на перерыве, статус не
    // наш, и «вернуть на линию» означало бы поднять его с обеда.
    if (lineReleased) await setTraining(false);
    lockScreen(false);
    showOktell(true);
    send('news:hide', {});
    updater.onIdle();
}

/* Пауза, в которую можно переставить программу. Оба условия обязательны:
   перезапуск посреди разговора оборвёт звонок, а посреди объявления — собьёт
   чтение и оставит человека снятым с линии. */
function updateIsSafeNow() {
    return !operatorState.busy && !newsShown;
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
    // Ссылку на дистрибутив дают только вошедшему, поэтому первую настоящую
    // проверку делаем здесь, а не на старте.
    updater.check();
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
        if (!newsShown) await closeNews();
        return { ok: true };
    }
    return { ok: false, status, ...(payload || {}) };
});

// Крестик у НЕОБЯЗАТЕЛЬНОГО объявления: ничего не подтверждаем, просто
// возвращаем человека к работе. Отметка «показали» уже стоит — её ставит
// /pending, — поэтому повторно оно не всплывёт.
ipcMain.handle('news:dismiss', async () => {
    await closeNews();
    return { ok: true };
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
        // И единственная пауза, которую стоит ловить для отложенного
        // обновления: следующей может не быть до конца смены.
        updater.onIdle();
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

            // Страница АТС нужна дальше всему: и статусу, и команде снятия с линии.
            const seen = [];
            ipcMain.on('oktell:state', (_event, payload) => seen.push(payload));
            say('вид АТС создан', openOktell(cabinet) === true && !!oktellView);
            await wait(4000);
            say('статус оператора доехал из сокета страницы', seen.length > 0,
                seen.map((item) => `${item.raw}${item.busy ? ' (занят)' : ''}`).join(', '));
            say('подстановка учётки отработала', autologinSeen === true, String(autologinReason));

            // Разговор: объявление обязано ждать, линию не трогаем.
            operatorState = { busy: true, everSeen: true, raw: 'talk', since: Date.now() };
            await pollNews();
            say('во время разговора объявление не показано', newsShown === false);
            say('во время разговора линию не трогали', lineReleased === false);

            // Разговор кончился — снимаем с линии и показываем.
            operatorState = { busy: false, everSeen: true, raw: 'usReady', since: Date.now() };
            await pollNews();
            say('после разговора объявление показано', newsShown === true);

            const commands = await (await fetch(api('/stub/commands'))).json();
            const training = (commands.commands || []).find(
                (item) => String(item.raw).includes('lunchreasonid'))
                || (commands.commands || []).find(
                    (item) => String(item.raw).includes('oncallcenter=0'));
            say('команда «Тренинг» ушла в АТС', !!training, training ? training.raw : 'ни одной');
            say('снятие с линии засчитано', lineReleased === true);

            const hidden = oktellView.getBounds();
            say('страница АТС убрана с экрана',
                oktellVisible === false && hidden.width === 0 && hidden.height === 0);
            say('экран заперт', locked === true && win.isKiosk());
            say('окно нельзя закрыть мимо подтверждения', win.isClosable() === false);

            // Порядок отказов сервера: рано → неверный тест → успех.
            const item = (await request('GET', '/api/news/pending')).payload?.items?.[0]
                || { id: 501, quiz: [] };
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

            await closeNews();
            const after = await (await fetch(api('/stub/commands'))).json();
            const restored = (after.commands || []).find(
                (cmd) => String(cmd.raw).includes('"onlunch":false')
                      || String(cmd.raw).includes('oncallcenter=1'));
            say('статус вернули после подтверждения', !!restored,
                restored ? restored.raw : 'команды возврата нет');
            say('экран отперт', locked === false && win.isClosable() === true);
            say('страница АТС вернулась', oktellVisible === true
                && oktellView.getBounds().width > 0);

            // Правило показа во всех ветках.
            operatorState = { busy: true, everSeen: true, raw: 'talk', since: Date.now() };
            waitingSince = Date.now();
            say('правило: в разговоре ждём', canShowNews() === false);
            operatorState = { busy: false, everSeen: true, raw: 'usReady', since: Date.now() };
            say('правило: освободился — показываем', canShowNews() === true);
            operatorState = { busy: false, everSeen: false, raw: '', since: Date.now() };
            waitingSince = Date.now();
            say('правило: состояние неизвестно — ждём', canShowNews() === false);
            waitingSince = Date.now() - (Number(config.news.unknown_state_grace_seconds) + 1) * 1000;
            say('правило: неизвестно дольше выдержки — показываем', canShowNews() === true);
            // ── Автообновление ──
            say('версии сравниваются числами, а не строкой',
                updater.isNewer('0.10.0', '0.9.0') === true
                && updater.isNewer('0.9.0', '0.10.0') === false);

            updater.init({
                apiBase: config.api_base_url,
                getToken: () => auth?.token || null,
                isIdle: updateIsSafeNow,
                checkHours: 4,
                log,
            });

            // Скачивание и проверка хэша.
            await updater.check();
            const stored = require('fs').existsSync(
                require('path').join(app.getPath('userData'), 'updates', 'pending-update.json'));
            say('новая версия скачана и проверена по sha256', stored === true);

            // Пауза не наступила — ставить нельзя ни при какой обязательности.
            operatorState = { busy: true, everSeen: true, raw: 'talk', since: Date.now() };
            say('во время разговора обновление не ставится',
                updateIsSafeNow() === false && updater.install() === false);
            operatorState = { busy: false, everSeen: true, raw: 'usReady', since: Date.now() };
            newsShown = true;
            say('под объявлением обновление не ставится',
                updateIsSafeNow() === false && updater.install() === false);
            newsShown = false;
            say('в паузе установка разрешена', updateIsSafeNow() === true);

            // Подменённый файл ставиться не должен: хэш — единственная проверка.
            require('fs').rmSync(
                require('path').join(app.getPath('userData'), 'updates'),
                { recursive: true, force: true });
            await (await fetch(api('/stub/break_hash'))).json();
            updater.init({
                apiBase: config.api_base_url,
                getToken: () => auth?.token || null,
                isIdle: () => false,          // ставить всё равно запрещаем
                checkHours: 4,
                log,
            });
            await updater.check();
            const afterBroken = require('fs').existsSync(
                require('path').join(app.getPath('userData'), 'updates', 'pending-update.json'));
            say('файл с чужим хэшем отброшен', afterBroken === false);
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
    // ICORE_OKTELL_SHOWCASE=quiz — сразу второй шаг: показать заказчику, как
    // выглядит тест, не нажимая ничего руками.
    if (String(process.env.ICORE_OKTELL_SHOWCASE).toLowerCase() === 'quiz') {
        setTimeout(() => win.webContents.executeJavaScript(
            "document.getElementById('news-confirm').click()"), 600);
    }
}

app.whenReady().then(async () => {
    createWindow();
    updater.init({
        apiBase: config.api_base_url,
        getToken: () => auth?.token || null,
        isIdle: updateIsSafeNow,
        checkHours: config.update?.check_hours,
        log,
    });
    // На старте оператор ещё не работает — обрывать нечего, и отложенное
    // обновление ставится сразу, включая обязательное.
    if (!SELFTEST) await updater.onStartup();
    if (SELFTEST) selftest();
    // Ждём, пока страница интерфейса подпишется на события, иначе показывать
    // объявление некому.
    else if (SHOWCASE) win.webContents.once('did-finish-load', () => setTimeout(showcase, 400));
});

app.on('window-all-closed', () => app.quit());
