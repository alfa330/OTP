/* Правила экранов «Карточки клиента» — чистые функции без React и DOM.
 *
 * Их читают страница карточки в iCORE Phone (src/client_card_embed) и раздел портала
 * (ClientCardsView.jsx), а тесты гоняют через node (tests/client_cards.test.mjs): что
 * решается в разметке, то не проверяется ничем — правило портала после мастера обращений.
 *
 * Время. Сервер отдаёт ISO со смещением +05:00 (время Алматы). Показываем его всегда
 * по Алматы, а не по часам компьютера: оператор в другом поясе должен видеть то же
 * время звонка, что и руководитель.
 */

export const ALMATY_TZ = 'Asia/Almaty';

const pad = (value) => String(value).padStart(2, '0');

const partsOf = (iso) => {
    if (!iso) return null;
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return null;
    const parts = {};
    new Intl.DateTimeFormat('ru-RU', {
        timeZone: ALMATY_TZ, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(date).forEach((part) => { parts[part.type] = part.value; });
    return parts;
};

/** «14:05». */
export const fmtTime = (iso) => {
    const p = partsOf(iso);
    return p ? `${p.hour}:${p.minute}` : '';
};

/** «12.11.2026». */
export const fmtDate = (iso) => {
    const p = partsOf(iso);
    return p ? `${p.day}.${p.month}.${p.year}` : '';
};

/** Сегодняшнее — «14:05», вчерашнее — «вчера 14:05», остальное — «12.11 14:05»
 *  (год — только если он не текущий). `now` — для тестов. */
export const fmtWhen = (iso, now = new Date()) => {
    const p = partsOf(iso);
    if (!p) return '';
    const today = partsOf(now.toISOString());
    const yesterday = partsOf(new Date(now.getTime() - 86400000).toISOString());
    const time = `${p.hour}:${p.minute}`;
    if (p.year === today.year && p.month === today.month && p.day === today.day) return time;
    if (p.year === yesterday.year && p.month === yesterday.month && p.day === yesterday.day) return `вчера ${time}`;
    return p.year === today.year ? `${p.day}.${p.month} ${time}` : `${p.day}.${p.month}.${p.year} ${time}`;
};

/** Секунды → «1:05» (для отсчёта и длительности разговора); больше часа — «1:02:05». */
export const fmtClock = (seconds) => {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const rest = total % 60;
    return hours ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${minutes}:${pad(rest)}`;
};

/** Секунды → «45 с», «1 мин 05 с» — для отчёта; null — прочерк. */
export const fmtSeconds = (seconds) => {
    if (seconds === null || seconds === undefined) return '—';
    const total = Math.round(Number(seconds) || 0);
    return total >= 60 ? `${Math.floor(total / 60)} мин ${pad(total % 60)} с` : `${total} с`;
};

/** Доля «37,5 %»; делить не на что — прочерк. */
export const fmtShare = (part, whole) => {
    if (!whole) return '—';
    return `${((Number(part) / Number(whole)) * 100).toFixed(1).replace('.', ',')} %`;
};

/** Размер файла: «840 КБ», «12,4 МБ». */
export const fmtSize = (bytes) => {
    const value = Number(bytes) || 0;
    if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} КБ`;
    return `${(value / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ`;
};

// ─── Причины обращения ───────────────────────────────────────────────────────

// Причины — дерево любой глубины: основная причина → подтема 1-го уровня → подтема 2-го
// уровня → … (владелец, 13.11.2026). Оператор выбирает самый глубокий уровень своей ветки.
// Ветка здесь — массив узлов от основной причины до выбранной: [основная, …, выбранная].
// В карточке причина хранится парой: category_id — основная, subcategory_id — выбранная
// подтема любого уровня (null — выбрана сама основная причина, у которой подтем нет).

export const PATH_SEPARATOR = ' → ';

/** «Запрос → Проблема с заказом → Не может завершить заказ» или просто «Выплаты». */
export const topicLabel = (topic) => {
    if (Array.isArray(topic?.path) && topic.path.length) return topic.path.join(PATH_SEPARATOR);
    return topic?.sub_title ? `${topic.title}${PATH_SEPARATOR}${topic.sub_title}` : (topic?.title || '');
};

const sameTopic = (a, b) => (
    Number(a.category_id) === Number(b.category_id)
    && (a.subcategory_id ?? null) === (b.subcategory_id ?? null)
);

/** Ветка → причина карточки: идентификаторы для сервера и названия для экрана. */
export const branchTopic = (branch) => {
    const titles = branch.map((node) => node.title);
    return {
        category_id: branch[0].id,
        subcategory_id: branch.length > 1 ? branch[branch.length - 1].id : null,
        title: titles[0],
        sub_title: titles.slice(1).join(PATH_SEPARATOR) || null,
        path: titles,
    };
};

/** Выбрать причину или снять её повторным нажатием. Возвращает НОВЫЙ список; сверх
 *  предела причина не добавляется (кнопки при этом гаснут — см. canAddTopic). */
export const toggleTopic = (topics, branch, limit = 5) => {
    const next = branchTopic(branch);
    const list = topics || [];
    if (list.some((item) => sameTopic(item, next))) return list.filter((item) => !sameTopic(item, next));
    return list.length >= limit ? list : [...list, next];
};

export const isTopicPicked = (topics, branch) => {
    const next = branchTopic(branch);
    return (topics || []).some((item) => sameTopic(item, next));
};

export const canAddTopic = (topics, limit = 5) => (topics || []).length < limit;

/** id причины и всех её подтем любого уровня. */
export const branchIds = (node) => (
    [node.id, ...(node.children || []).flatMap(branchIds)]
);

/** Сколько причин выбрано в ветке — цифра на её строке, пока она свёрнута. */
export const pickedInBranch = (topics, node) => {
    const ids = new Set(branchIds(node));
    return (topics || []).filter((item) => ids.has(item.subcategory_id ?? item.category_id)).length;
};

/** То, что уходит серверу: только идентификаторы. */
export const topicsPayload = (topics) => (
    (topics || []).map((item) => ({ category_id: item.category_id, subcategory_id: item.subcategory_id ?? null }))
);

/** Причины по строке поиска: причина подходит названием (тогда со всей своей веткой)
 *  или чем-то в ветке (тогда только с подходящими подтемами). */
export const filterCategories = (categories, query) => {
    const needle = String(query || '').trim().toLowerCase();
    if (!needle) return categories || [];
    const pick = (nodes) => (nodes || []).flatMap((node) => {
        if (String(node.title || '').toLowerCase().includes(needle)) return [node];
        const children = pick(node.children);
        return children.length ? [{ ...node, children }] : [];
    });
    return pick(categories);
};

/** Сколько всего причин в дереве — всех уровней. */
export const countReasons = (categories) => (
    (categories || []).reduce((sum, node) => sum + 1 + countReasons(node.children), 0)
);

/** Дерево причин плоским списком для фильтра: [{id, label: полный путь, depth}]. */
export const flattenReasons = (categories, parents = []) => (
    (categories || []).flatMap((node) => {
        const titles = [...parents, node.title];
        return [{ id: node.id, label: titles.join(PATH_SEPARATOR), depth: titles.length },
            ...flattenReasons(node.children, titles)];
    })
);

/** Подпись уровня дерева: 1 — основная причина, дальше — подтемы по уровням. */
export const reasonLevelTitle = (level) => (
    level <= 1 ? 'Основная причина' : `Подтема ${level - 1}-го уровня`
);

/** Дерево причин отчёта → строки таблицы: [{key, title, count, depth, note, hasChildren, parentKey}].
 *  У причины с подтемами под ней идут её подтемы и, если выбирали её саму, строка
 *  «без подтемы» (own) — иначе числа подтем не сходились бы с числом причины. */
export const reportReasonRows = (categories, isOpen, depth = 0) => (
    (categories || []).flatMap((node) => {
        const children = node.children || [];
        const row = { key: String(node.id), id: node.id, title: node.title, count: node.count, depth,
            hasChildren: children.length > 0, open: children.length > 0 && isOpen(node.id) };
        if (!row.open) return [row];
        const rows = [row, ...reportReasonRows(children, isOpen, depth + 1)];
        if (node.own > 0) {
            rows.push({ key: `${node.id}:own`, id: null, title: 'без подтемы', count: node.own, depth: depth + 1,
                hasChildren: false, open: false, muted: true });
        }
        return rows;
    })
);

// ─── Аккаунт ─────────────────────────────────────────────────────────────────

/** Подпись аккаунта одной строкой: «Иванов Иван · ID 12345» или «ID 12345», пока имени нет. */
export const accountTitle = (account) => {
    if (!account) return '';
    const name = String(account.full_name || '').trim();
    return name ? `${name} · ID ${account.tez_id}` : `ID ${account.tez_id}`;
};

/** Похоже ли вставленное на ссылку из админки Тез — чтобы подсказать раньше сервера.
 *  Окончательно ссылку разбирает сервер (client_cards/rules.py: parse_account_link). */
export const looksLikeAccountLink = (text) => (
    /^(https?:\/\/)?admin\.tezapp\.org\/admin\/(drivers|clients)\/\d{1,12}([/?#].*)?$/i.test(String(text || '').trim())
);

/** Отметка оператора «у звонившего нет аккаунта в Тез» (client_cards/rules.py: KIND_NONE). */
export const NO_ACCOUNT_LABEL = 'Аккаунта нет';

/** Аккаунт карточки одной строкой для списков: «Водитель · Иванов Иван · ID 12345»,
 *  «Аккаунта нет» по отметке оператора, иначе fallback (карточку ещё не заполнили). */
export const cardAccountText = (card, fallback = '') => {
    if (card?.account) return `${card.account.kind_label} · ${accountTitle(card.account)}`;
    return card?.no_account ? NO_ACCOUNT_LABEL : fallback;
};

/** Какой аккаунт подставить самому: единственный привязанный к номеру. Несколько —
 *  выбирает оператор (задача #335, п. 2), уже выбранный не трогаем. Отметку «Аккаунта нет»
 *  тоже: оператор сказал своё, и запоздавший ответ TEZ APP не должен её перебить. */
export const autoAccountId = (card, accounts) => {
    if (!card || card.account_id || card.no_account || card.status !== 'open') return null;
    return (accounts || []).length === 1 ? accounts[0].id : null;
};

// ─── Состояние карточки ──────────────────────────────────────────────────────

export const STATE_TONES = {
    ringing: 'amber', talking: 'blue', wrapup: 'amber', overdue: 'red', done: 'green', void: 'slate',
};

/** Сколько секунд осталось постобработки сейчас: сервер прислал остаток на момент ответа,
 *  дальше считаем от момента получения — часы компьютера оператора не участвуют. */
export const wrapupLeft = (card, receivedAtMs, nowMs = Date.now()) => {
    if (!card || card.state !== 'wrapup' || card.wrapup_left_sec === null || card.wrapup_left_sec === undefined) {
        return null;
    }
    return Math.max(0, Math.ceil(Number(card.wrapup_left_sec) - (nowMs - receivedAtMs) / 1000));
};

/** Что написать на главной кнопке карточки. */
export const saveLabel = (card) => (card?.status === 'done' ? 'Сохранить изменения' : 'Сохранить карточку');

// ─── Мост с телефоном ────────────────────────────────────────────────────────

/* Сообщения телефона о звонке (microsip-src/ClientCard.cpp):
 *   { type: 'call:start', call_key, phone, direction: 'in' | 'out' }
 *   { type: 'call:answered', call_key }
 *   { type: 'call:ended', call_key }
 * Возвращает событие в виде страницы или null — сообщение не про звонок. */
export const parseCallMessage = (data) => {
    let payload = data;
    if (typeof payload === 'string') {
        try { payload = JSON.parse(payload); } catch { return null; }
    }
    if (!payload || typeof payload !== 'object') return null;
    const callKey = String(payload.call_key || '').trim();
    if (payload.type === 'call:start') {
        const phone = String(payload.phone || '').trim();
        if (!phone) return null;
        return { kind: 'start', callKey, phone, direction: payload.direction === 'out' ? 'out' : 'in' };
    }
    if (payload.type === 'call:answered') return { kind: 'answered', callKey };
    if (payload.type === 'call:ended') return { kind: 'ended', callKey };
    return null;
};

/** Относится ли событие звонка к карточке на экране: по идентификатору звонка, а если
 *  телефон его не прислал — к текущей карточке. */
export const eventIsForCall = (event, currentCallKey) => (
    !event.callKey || !currentCallKey || event.callKey === currentCallKey
);

// ─── Запросы ─────────────────────────────────────────────────────────────────

export const REQUEST_TONES = { open: 'amber', answered: 'blue', resolved: 'green', cancelled: 'slate' };

export const REQUEST_FILTERS = [
    { value: 'active', label: 'В работе' },
    { value: 'resolved', label: 'Решённые' },
    { value: '', label: 'Все' },
];

/** Цвет имени сотрудника группы — по его id в Telegram, а не по имени: имя он может
 *  сменить, и цвет не должен переехать посреди переписки (как в «Обращениях»). */
const NAME_COLORS = ['text-rose-600', 'text-orange-600', 'text-amber-600', 'text-emerald-600',
    'text-cyan-600', 'text-blue-600', 'text-violet-600'];
export const senderColor = (message) => {
    const seed = Number(message?.tg_from_id) || 0;
    return NAME_COLORS[Math.abs(seed) % NAME_COLORS.length];
};

/** Цитата сообщения, на которое ответили: ищем в уже загруженной нити. */
export const quoteOf = (message, messages) => {
    const target = message?.reply_to_tg_message_id;
    if (!target) return null;
    const found = (messages || []).find((item) => item.tg_message_id === target);
    if (!found || found.id === message.id) return null;
    // Реплай на сам запрос (первое сообщение нити) цитатой не показываем: вся нить — о нём.
    if (messages[0] && found.id === messages[0].id) return null;
    return { author: found.author_name || '', text: found.body || (found.file ? found.file.name || 'Файл' : '') };
};

/** Проверка файлов до отправки: (ошибка | null). Пределы приходят с сервера. */
export const uploadsProblem = (fileList, limits) => {
    const list = Array.from(fileList || []);
    const maxFiles = Number(limits?.request_files) || 6;
    const maxBytes = Number(limits?.request_file_bytes) || 50 * 1024 * 1024;
    if (list.length > maxFiles) return `Не больше ${maxFiles} файлов`;
    const big = list.find((file) => file.size > maxBytes);
    if (big) return `Файл «${big.name}» больше ${Math.round(maxBytes / (1024 * 1024))} МБ`;
    return null;
};

// ─── Период отчёта ───────────────────────────────────────────────────────────

const isoDay = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

/** Готовые периоды отчёта от «сегодня» по Алматы: {from, to} днями включительно. */
export const periodPreset = (key, todayIso) => {
    const [year, month, day] = String(todayIso).split('-').map(Number);
    const today = new Date(year, month - 1, day);
    const shift = (days) => new Date(year, month - 1, day + days);
    switch (key) {
        case 'today': return { from: isoDay(today), to: isoDay(today) };
        case 'yesterday': return { from: isoDay(shift(-1)), to: isoDay(shift(-1)) };
        case 'week': return { from: isoDay(shift(-6)), to: isoDay(today) };
        case 'prev_month': {
            const first = new Date(year, month - 2, 1);
            const last = new Date(year, month - 1, 0);
            return { from: isoDay(first), to: isoDay(last) };
        }
        default: return { from: isoDay(new Date(year, month - 1, 1)), to: isoDay(today) };
    }
};

/** «Сегодня» по Алматы в виде ГГГГ-ММ-ДД. */
export const almatyToday = (now = new Date()) => {
    const p = partsOf(now.toISOString());
    return `${p.year}-${p.month}-${p.day}`;
};

/** Текст ошибки сервера для человека; английское «Network Error» axios — по-русски. */
export const errorText = (error, fallback = 'Не получилось — попробуйте ещё раз') => {
    const data = error?.response?.data;
    if (data && typeof data === 'object' && typeof data.error === 'string' && data.error.trim()) return data.error;
    if (!error?.response) return 'Нет связи с порталом';
    return fallback;
};
