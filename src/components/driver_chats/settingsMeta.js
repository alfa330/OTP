/* Вкладка «Настройки» раздела «Чаты водителей»: поиски сверх суточного лимита.
 *
 * Владелец 27.10.2026: «св и выше добавь в настройках возможность добавлять
 * дополнительные попытки операторам до 5 попыток» — и на уточнение: только на
 * сегодня. Оператор упёрся в лимит, пишет супервайзеру, тот добавляет +1…+5;
 * в полночь по Алматы лимит снова обычный.
 *
 * Здесь — чистые правила экрана, без React: их проверяет node --test
 * (tests/driver_chats_settings.test.mjs). Потолки — лимит и «до пяти» —
 * приходят с сервера (`daily_limit`, `max_extra`), своих копий экран не держит.
 */

/* Шаг степпера «− N +». Итог зажат в [0, max]: кнопки у края гаснут, но
   шаг всё равно не обязан верить, что его позвали только из живой кнопки. */
export const stepExtra = (value, delta, max) => {
    const current = Number.isInteger(value) ? value : 0;
    const ceiling = Number.isInteger(max) && max > 0 ? max : 0;
    return Math.min(ceiling, Math.max(0, current + delta));
};

/* Потолок человека СЕГОДНЯ — лимит плюс добавленное. */
export const todayLimit = (person, dailyLimit) => (dailyLimit || 0) + (person?.extra_today || 0);

/* Искать сегодня ему уже нечем. */
export const isExhausted = (person, dailyLimit) => (
    (person?.used_today || 0) >= todayLimit(person, dailyLimit)
);

/* Кто упёрся в ОСНОВНОЙ лимит — их группа стоит сверху: ради них вкладку и
   открывают. Считается один раз, при загрузке списка, и по основному лимиту,
   а не по сегодняшнему потолку: иначе добавка или свежий поиск оператора
   переносили бы строку в другую группу прямо из-под пальца. */
export const reachedIds = (people, dailyLimit) => new Set(
    (people || [])
        .filter((person) => (person.used_today || 0) >= (dailyLimit || 0))
        .map((person) => person.user_id));

/* Порядок — по имени и по-русски. Сортировка базы зависит от её локали: в
   «C» буква «Ё» встаёт после «Я», а латиница — перед кириллицей. */
export const byName = (people) => [...(people || [])].sort(
    (a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'ru'));

/* Поиск по имени: регистр и «ё» не важны, слова — в любом порядке и с любого
   места («айгерим абен» найдёт «Абенова Айгерим»). */
const fold = (text) => String(text || '').toLowerCase().replace(/ё/g, 'е');

export const matchesName = (person, query) => {
    const words = fold(query).split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const name = fold(person?.name);
    return words.every((word) => name.includes(word));
};

/* Группы экрана: «Дошли до лимита сегодня» сверху, остальные под ними.
   Заголовок верен и после добавки: дошёл до сорока — дошёл, даже если сейчас
   у человека «40 из 43». Пустая группа не рисуется вовсе; вторая называется
   «Остальные» только рядом с первой — одна она просто «Сотрудники». */
export const groupPeople = (people, reached, query) => {
    const shown = (people || []).filter((person) => matchesName(person, query));
    const top = shown.filter((person) => reached.has(person.user_id));
    const rest = shown.filter((person) => !reached.has(person.user_id));
    return [
        { key: 'reached', title: 'Дошли до лимита сегодня', people: top },
        { key: 'rest', title: top.length ? 'Остальные' : 'Сотрудники', people: rest },
    ].filter((group) => group.people.length);
};

/* Фоновое обновление открытого списка (вернулись в окно браузера): числа
   свежие, а порядок строк — прежний. Новые люди встают в конец по имени,
   ушедшие пропадают. Пересортировка на глазах выдёргивала бы строку из-под
   пальца; группы и порядок пересчитываются при следующем открытии вкладки. */
export const mergeRefresh = (previous, fresh) => {
    const byId = new Map((fresh || []).map((person) => [person.user_id, person]));
    const kept = (previous || [])
        .filter((person) => byId.has(person.user_id))
        .map((person) => byId.get(person.user_id));
    const known = new Set(kept.map((person) => person.user_id));
    return [...kept, ...byName((fresh || []).filter((person) => !known.has(person.user_id)))];
};

/* ── Полоса вкладок раздела ─────────────────────────────────────────────────
 *
 * Какие вкладки у человека, решает сервер (права из /context), и до его
 * ответа полоса не рисовалась вовсе — у супервайзера она появлялась через
 * полсекунды и сдвигала экран вниз. Поэтому права прошлого входа этой учётки
 * помним в браузере и рисуем полосу по ним с первого кадра; ответ сервера
 * поправит, если права поменялись. Это только вид: ручки журнала и «Настроек»
 * сервер закрывает своими гейтами. Хранилище подставляется — для тестов и на
 * случай, когда браузер его не даёт (приватное окно). */
export const TABS_MEMORY_KEY = 'otp.driverChats.tabs';

const browserStorage = () => {
    try {
        return globalThis.localStorage || null;
    } catch {
        return null;
    }
};

export const recallTabs = (userId, storage = browserStorage()) => {
    if (userId === null || userId === undefined || !storage) return null;
    try {
        const raw = storage.getItem(`${TABS_MEMORY_KEY}.${userId}`);
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
};

export const rememberTabs = (userId, capabilities, storage = browserStorage()) => {
    if (userId === null || userId === undefined || !capabilities || !storage) return;
    try {
        storage.setItem(`${TABS_MEMORY_KEY}.${userId}`, JSON.stringify({
            journal: Boolean(capabilities.can_view_journal),
            settings: Boolean(capabilities.can_grant_extra_searches),
        }));
    } catch {
        /* хранилище запрещено — полоса просто появится с ответом сервера */
    }
};

/* Вкладки по правам: из ответа сервера, а до него — из памяти прошлого входа. */
export const sectionTabs = (capabilities, recalled) => {
    const journal = capabilities ? Boolean(capabilities.can_view_journal) : Boolean(recalled?.journal);
    const settings = capabilities
        ? Boolean(capabilities.can_grant_extra_searches) : Boolean(recalled?.settings);
    return [
        { value: 'search', label: 'Поиск чатов' },
        ...(journal ? [{ value: 'journal', label: 'Журнал' }] : []),
        ...(settings ? [{ value: 'settings', label: 'Настройки' }] : []),
    ];
};

/* Поиск по имени нужен, когда список длиннее экрана; на шести строках поле
   над ними — шум. Порог тот же, что у поиска в выпадающих списках журнала. */
export const SEARCH_FROM = 9;

/* «Добавлено в 14:05 · Иванова Анна» — кто и когда менял добавку последним.
   Только пока добавка есть: при нуле строка ничего не сообщает человеку,
   который смотрит на список. Время — стенные часы Алматы, как в журнале. */
export const grantNote = (person, formatTime) => {
    if (!person?.extra_today || !person.granted_by_name) return '';
    const time = person.granted_at ? formatTime(person.granted_at) : '';
    return time
        ? `Добавлено в ${time} · ${person.granted_by_name}`
        : `Добавлено · ${person.granted_by_name}`;
};
