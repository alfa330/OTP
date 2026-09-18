/*
 * Скрипт, который живёт В СТРАНИЦЕ Oktell и делает две вещи.
 *
 * 1. ОБОРАЧИВАЕТ window.WebSocket, чтобы читать статус оператора. Клиент Oktell
 *    получает свои состояния кадрами вида
 *      ["getuserstateresult", {"userstatestr": "usLunch", ...}]
 *    и это единственный источник, который знает про разговор мгновенно: наш
 *    сервер вычитывает те же статусы из SQL-прокси раз в несколько секунд, а
 *    прокси бывает недоступен минутами.
 *
 * 2. ПОДСТАВЛЯЕТ УЧЁТКУ АТС. Оператор вводит только логин iCORE, пару от Oktell
 *    приносит сервер.
 *
 * ПОЧЕМУ ЭТО PRELOAD, А НЕ executeJavaScript ПОСЛЕ ЗАГРУЗКИ. Первая версия
 * вставляла обёртку на did-finish-load — и не поймала ни одного кадра: страница
 * поднимает свой веб-сокет ещё при разборе разметки, то есть до того, как
 * загрузка «закончилась». Обёртка успевает, только если стоит ДО скриптов
 * страницы, а это умеет один preload. Поймано сквозным прогоном на стенде.
 *
 * Отсюда и contextIsolation: false у этого вида — из изолированного мира чужой
 * window.WebSocket не подменить. nodeIntegration при этом выключен, так что
 * Node страница не получает: ей достаётся ровно то, что мы сами положили.
 *
 * Всякая ошибка здесь проглатывается и уходит в отчёт: упавший наш скрипт не
 * имеет права утащить за собой рабочий клиент АТС.
 */
const { ipcRenderer } = require('electron');

// Синхронно, потому что обёртку надо поставить до первого скрипта страницы, а
// ждать ответа асинхронно значит опоздать ровно так же, как executeJavaScript.
let options = {};
try {
    options = ipcRenderer.sendSync('oktell:options') || {};
} catch (error) {
    options = {};
}

const busyNeedles = (options.busy || []).map((value) => String(value).toLowerCase());

function reportState(raw) {
    try {
        const text = String(raw || '').toLowerCase();
        ipcRenderer.send('oktell:state', {
            raw: String(raw || ''),
            busy: busyNeedles.some((needle) => needle && text.includes(needle)),
        });
    } catch (error) { /* страница важнее отчёта */ }
}

function readFrame(data) {
    if (typeof data !== 'string') return;              // бинарные кадры не наши
    if (data.indexOf('userstatestr') === -1) return;
    try {
        const parsed = JSON.parse(data);
        const body = Array.isArray(parsed) ? parsed[1] : parsed;
        if (body && body.userstatestr) reportState(body.userstatestr);
    } catch (error) { /* чужой формат — молча мимо */ }
}

try {
    const Native = window.WebSocket;
    if (Native && !Native.__icoreWrapped) {
        const Wrapped = function (url, protocols) {
            const socket = protocols === undefined
                ? new Native(url) : new Native(url, protocols);
            socket.addEventListener('message', (event) => readFrame(event.data));
            return socket;
        };
        Wrapped.prototype = Native.prototype;
        ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach((key) => {
            Wrapped[key] = Native[key];
        });
        Wrapped.__icoreWrapped = true;
        window.WebSocket = Wrapped;
    }
} catch (error) {
    ipcRenderer.send('oktell:autologin', { filled: false, reason: 'сокет не обёрнут: ' + error.message });
}

/* ── Подстановка учётки ── */
const account = options.account;
if (account && account.cabinet_login) {
    const selectors = options.selectors || {};
    const pick = (list) => {
        const found = document.querySelector(list || '');
        // Скрытое поле — не то поле: у формы входа бывает второй, невидимый
        // набор инпутов, и заполнение его выглядит как «ничего не произошло».
        return (found && found.offsetParent !== null) ? found : null;
    };
    const setValue = (field, value) => {
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(field, value);
        // Нативный сеттер не будит слушателей фреймворка — события обязательны,
        // иначе форма отправит пустые поля, «заполненные» на экране.
        field.dispatchEvent(new Event('input', { bubbles: true }));
        field.dispatchEvent(new Event('change', { bubbles: true }));
    };

    let attempts = 0;
    const tryFill = () => {
        attempts += 1;
        try {
            const loginField = pick(selectors.login);
            const passwordField = pick(selectors.password);
            if (!loginField || !passwordField) {
                // Форма рисуется не сразу; 20 попыток по 300 мс — это шесть
                // секунд, дольше ждать бессмысленно: оператор уже вводит сам.
                if (attempts < 20) return setTimeout(tryFill, 300);
                return ipcRenderer.send('oktell:autologin',
                    { filled: false, reason: 'поля входа не найдены' });
            }
            setValue(loginField, account.cabinet_login);
            setValue(passwordField, account.cabinet_password || '');
            const submit = pick(selectors.submit);
            if (submit) submit.click();
            ipcRenderer.send('oktell:autologin', { filled: true, reason: '' });
        } catch (error) {
            ipcRenderer.send('oktell:autologin', { filled: false, reason: error.message });
        }
    };
    if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', tryFill);
    } else {
        tryFill();
    }
}
