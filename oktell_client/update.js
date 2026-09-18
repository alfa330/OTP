/*
 * Автообновление клиента.
 *
 * Устройство скопировано с «Ограничителя Перезвона» и iCORE Phone: манифест
 * версии публично, файл по подписанной ссылке, sha256 обязателен. Подписи у нас
 * нет, поэтому хэш — единственная проверка того, что приехал наш файл, а не
 * что-то подменённое по дороге; не совпал — файл удаляется и не ставится.
 *
 * ЧЕГО У СОСЕДЕЙ НЕТ, А ЗДЕСЬ ОБЯЗАТЕЛЬНО. Телефону и агенту нечего обрывать,
 * а этот клиент держит на экране разговор и обязательное объявление. Установка
 * перезапускает программу, поэтому она разрешена только в паузе:
 *
 *   · не идёт разговор (состояние оператора со страницы АТС);
 *   · на экране нет объявления;
 *   · либо мы вообще ещё до входа — на старте, пока человек не начал работать.
 *
 * Обязательный релиз этих правил НЕ отменяет: он лишь не ждёт следующей
 * проверки, а ставится в ближайшую паузу и обязательно на старте. Оборвать
 * звонок ради обновления нельзя ни при какой обязательности.
 */
const { app } = require('electron');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const PENDING_NAME = 'pending-update.json';

let options = null;
let timer = null;
let pending = null;      // {version, file, sha256, mandatory}
let busyInstalling = false;

function log(message) {
    if (options?.log) options.log(`обновление: ${message}`);
}

function updatesDir() {
    const dir = path.join(app.getPath('userData'), 'updates');
    fs.mkdirSync(dir, { recursive: true });
    return dir;
}

function pendingPath() {
    return path.join(updatesDir(), PENDING_NAME);
}

/* Сравнение версий по числам, а не строкой: '0.10.0' строкой меньше '0.9.0',
   и парк застрял бы на девятой сборке навсегда. */
function isNewer(candidate, current) {
    const parse = (value) => String(value || '')
        .split('.').map((part) => parseInt(part, 10) || 0);
    const left = parse(candidate);
    const right = parse(current);
    for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
        const a = left[i] || 0;
        const b = right[i] || 0;
        if (a !== b) return a > b;
    }
    return false;
}

function sha256OfFile(file) {
    return new Promise((resolve, reject) => {
        const hash = crypto.createHash('sha256');
        const stream = fs.createReadStream(file);
        stream.on('data', (chunk) => hash.update(chunk));
        stream.on('error', reject);
        stream.on('end', () => resolve(hash.digest('hex')));
    });
}

function loadPending() {
    try {
        const saved = JSON.parse(fs.readFileSync(pendingPath(), 'utf8'));
        if (saved?.file && fs.existsSync(saved.file)) return saved;
    } catch (error) { /* нет отложенного — это норма */ }
    return null;
}

function savePending(value) {
    pending = value;
    try {
        if (value) fs.writeFileSync(pendingPath(), JSON.stringify(value), 'utf8');
        else if (fs.existsSync(pendingPath())) fs.unlinkSync(pendingPath());
    } catch (error) {
        log(`не удалось записать отметку: ${error.message}`);
    }
}

async function fetchManifest() {
    const base = String(options.apiBase || '').replace(/\/+$/, '');
    if (!base) return null;
    try {
        const response = await fetch(`${base}/api/oktell_client/version`);
        if (!response.ok) return null;
        const payload = await response.json();
        return payload?.release || null;
    } catch (error) {
        // Манифест недоступен — это не повод шуметь: сеть офиса, прокси, деплой.
        log(`манифест недоступен (${error.message})`);
        return null;
    }
}

async function fetchDownloadUrl() {
    const base = String(options.apiBase || '').replace(/\/+$/, '');
    const token = options.getToken ? options.getToken() : null;
    if (!token) return null;                 // до входа ссылку не выдадут
    try {
        const response = await fetch(`${base}/api/oktell_client/download`, {
            headers: { Authorization: `Bearer ${token}` },
        });
        if (!response.ok) return null;
        const payload = await response.json();
        return payload?.url || null;
    } catch (error) {
        log(`ссылку на файл не дали (${error.message})`);
        return null;
    }
}

async function download(manifest) {
    const url = await fetchDownloadUrl();
    if (!url) return null;
    const file = path.join(updatesDir(), manifest.filename
        || `iCORE-Oktell-Setup-${manifest.version}.exe`);
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const body = Buffer.from(await response.arrayBuffer());
        fs.writeFileSync(file, body);
    } catch (error) {
        log(`скачать не удалось: ${error.message}`);
        return null;
    }
    const actual = await sha256OfFile(file);
    if (actual.toLowerCase() !== String(manifest.sha256 || '').toLowerCase()) {
        // Хэш — единственная проверка подлинности файла: не совпал, значит это
        // не наша сборка, и ставить её нельзя ни при каких обстоятельствах.
        log(`хэш не совпал (ждали ${manifest.sha256}, получили ${actual}) — файл удалён`);
        try { fs.unlinkSync(file); } catch (error) { /* уже нет — и хорошо */ }
        return null;
    }
    log(`версия ${manifest.version} скачана и проверена`);
    return { version: manifest.version, file, sha256: actual, mandatory: !!manifest.mandatory };
}

/* Установка перезапускает программу, поэтому она возможна только в паузе. */
function install() {
    if (!pending || busyInstalling) return false;
    if (!options.isIdle()) {
        log('пауза не наступила — установку откладываем');
        return false;
    }
    busyInstalling = true;
    log(`ставим версию ${pending.version}`);
    try {
        if (process.platform === 'win32') {
            // Тихая установка NSIS. detached + unref: инсталлятору предстоит
            // пережить наш выход, иначе он умрёт вместе с родителем.
            const child = spawn(pending.file, ['/S'], { detached: true, stdio: 'ignore' });
            child.unref();
        } else {
            // На других платформах программу не раздают: там это стенд.
            log(`не Windows — файл лежит в ${pending.file}, установка вручную`);
            busyInstalling = false;
            return false;
        }
    } catch (error) {
        log(`запустить инсталлятор не вышло: ${error.message}`);
        busyInstalling = false;
        return false;
    }
    savePending(null);
    setTimeout(() => app.quit(), 500);
    return true;
}

async function check() {
    const manifest = await fetchManifest();
    if (!manifest?.version) return;
    if (!isNewer(manifest.version, app.getVersion())) return;
    if (pending?.version === manifest.version) {
        install();
        return;
    }
    const ready = await download(manifest);
    if (!ready) return;
    savePending(ready);
    install();
}

/** Поднять автообновление. isIdle() — «сейчас можно перезапускаться». */
function init(opts) {
    options = opts || {};
    pending = loadPending();
    const hours = Math.max(1, Number(options.checkHours || 4));
    if (timer) clearInterval(timer);
    timer = setInterval(() => { check(); }, hours * 3600 * 1000);
    return { pending };
}

/** Проверка на старте: до входа оператор ещё не работает, обрывать нечего. */
async function onStartup() {
    if (pending && install()) return true;
    await check();
    return false;
}

/** Позвать, когда пауза наступила: кончился разговор, закрылось объявление. */
function onIdle() {
    if (pending) install();
}

module.exports = { init, onStartup, onIdle, check, isNewer, install };
