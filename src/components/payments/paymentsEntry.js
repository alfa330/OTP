/*
 * Что раздел «Оплата счетов» помнит между входами — чтобы открываться сразу в своём виде.
 *
 * Набор вкладок, кнопка «Создать заявку» и предупреждения приходят с сервера (`/ping`). До
 * ответа раздел рисовал то, что знал без него, — две вкладки, «Мои задачи» и «Заявки», — а
 * через треть секунды перестраивался: вкладок становилось восемь, появлялась кнопка, надпись
 * «Загружаем задачи…» сменялась списком (владелец 13.10.2026: «при входе в раздел имеется микро
 * пролаг, сначала выходит, что сверху два раздела… убери»).
 *
 * Запоминается в два слоя.
 *   Память страницы — последние ответы сервера целиком (`ping`, справочники, сотрудники, первая
 *     страница «Моих задач»): повторный вход рисуется тем же, чем раздел был, и обновляется фоном.
 *   Хранилище браузера — только «оболочка» ответа `/ping`: права, названия досок и статусов,
 *     счётчик и признаки предупреждений. Она переживает перезагрузку страницы, поэтому имён в ней
 *     нет — ни своего (`me`), ни участников процесса (`roles`): на общем компьютере запись
 *     незачем кому-то читать.
 *
 * Всё привязано к пользователю: запись другого человека не читается, а память страницы держит
 * одного — вошёл другой, прежнее стирается. Запомненное — подсказка для первого кадра, а не
 * источник прав: права на каждом запросе проверяет сервер, и свежий ответ тут же её заменяет.
 */

export const ENTRY_STORAGE_KEY = 'otp.payments.entry.v1';

const SHELL_FIELDS = [
    'capabilities', 'meta', 'settings', 'roles_missing', 'roles_staffed', 'counters',
    'schema_ready', 'storage_ready', 'legacy_pending', 'card_key_ready',
];

/** Оболочка ответа `/ping` — то, что можно положить в хранилище браузера: без имён. */
export const entryShell = (ping) => {
    if (!ping || typeof ping !== 'object' || !ping.capabilities || typeof ping.capabilities !== 'object') return null;
    const shell = {};
    SHELL_FIELDS.forEach((field) => { if (ping[field] !== undefined) shell[field] = ping[field]; });
    return shell;
};

const userKey = (userId) => (userId === null || userId === undefined || userId === '' ? null : String(userId));

const browserStorage = () => {
    try { return window.localStorage; } catch { return null; }
};

const memory = { user: null, values: {} };

/** Запомненное в памяти страницы: 'ping', 'dictionaries', 'users', 'desk'. Нет — null. */
export const recall = (userId, name) => {
    const key = userKey(userId);
    if (!key || memory.user !== key) return null;
    return memory.values[name] ?? null;
};

export const remember = (userId, name, value) => {
    const key = userKey(userId);
    if (!key) return;
    if (memory.user !== key) {
        memory.user = key;
        memory.values = {};
    }
    memory.values[name] = value;
};

/** Оболочка раздела из хранилища браузера — для первого входа после перезагрузки страницы. */
export const recallShell = (userId, storage = browserStorage()) => {
    const key = userKey(userId);
    if (!key || !storage) return null;
    try {
        const saved = JSON.parse(storage.getItem(ENTRY_STORAGE_KEY) || 'null');
        if (!saved || saved.user !== key) return null;
        return entryShell(saved.shell);
    } catch {
        return null;
    }
};

/** Свежий ответ `/ping`: целиком — в память страницы, оболочкой — в хранилище браузера. */
export const rememberPing = (userId, ping, storage = browserStorage()) => {
    const key = userKey(userId);
    const shell = entryShell(ping);
    if (!key || !shell) return;
    remember(userId, 'ping', ping);
    if (!storage) return;
    try {
        storage.setItem(ENTRY_STORAGE_KEY, JSON.stringify({ user: key, shell }));
    } catch { /* хранилище закрыто или переполнено — остаётся память страницы */ }
};

/** Только для тестов: память страницы — пустая. */
export const forgetEntryMemory = () => {
    memory.user = null;
    memory.values = {};
};
