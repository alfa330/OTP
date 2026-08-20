/* Правила отображения переписки обращения. Без React — чтобы их можно было
 * проверить: в этом разделе логика, жившая в разметке, уже однажды стоила
 * четырёх нерабочих тематик (см. wizardRules.js).
 *
 * Первые три вещи — следствие одного факта: в нить падает ВСЯ ветка обсуждения
 * из группы, а не только ответы боту.
 *   кто кому отвечал  — цитата над сообщением;
 *   кто говорит       — цвет имени и кружок с инициалами;
 *   что в сообщении   — вид вложения.
 * Четвёртая — разбивка по дням: у каждой реплики есть время, но без плашки дня
 * переписка за неделю читается как один бесконечный день.
 */

/* Палитра имён. Семь цветов, как в Telegram: больше — и они перестают
 * различаться, меньше — в переписке на пятерых двое окажутся одного цвета.
 * Цвет нужен только на светлом пузыре входящего: у исходящих имя не
 * показывается вовсе, там и так понятно, кто написал. */
export const AUTHOR_TONES = [
    'text-rose-600', 'text-amber-600', 'text-emerald-600', 'text-sky-600',
    'text-violet-600', 'text-fuchsia-600', 'text-teal-600',
];

/* Ключ автора: id из Telegram устойчивее имени — сотрудник может сменить имя,
 * и цвет не должен переехать посреди переписки. */
export const authorKey = (message) => {
    if (!message) return '';
    if (message.telegram_user_id) return 'tg:' + message.telegram_user_id;
    if (message.author_user_id) return 'u:' + message.author_user_id;
    return 'n:' + (message.author_name || '');
};

export const authorTone = (message) => {
    const key = authorKey(message);
    if (!key) return AUTHOR_TONES[0];
    let hash = 0;
    for (let i = 0; i < key.length; i += 1) {
        hash = (hash * 31 + key.charCodeAt(i)) % 100000007;
    }
    return AUTHOR_TONES[hash % AUTHOR_TONES.length];
};

/* Короткая строка сообщения для цитаты. Вложение без текста — не пустая
 * цитата, а «Фото» или имя файла: иначе над ответом висела бы пустая полоска. */
export const messageSnippet = (message, limit = 90) => {
    if (!message) return '';
    const text = String(message.body || '').replace(/\s+/g, ' ').trim();
    if (text) return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
    const attachment = message.attachment;
    if (!attachment) return 'Без текста';
    if (attachment.kind === 'photo') return 'Фото';
    return attachment.name || 'Файл';
};

export const indexByTgId = (messages) => {
    const index = new Map();
    for (const message of messages || []) {
        if (message && message.tg_message_id) {
            index.set(Number(message.tg_message_id), message);
        }
    }
    return index;
};

/* Цитата над сообщением: на что именно отвечали.
 *
 * null — отвечать было не на что. Найти цель мы можем не всегда: нить обрезана
 * потолком, а в группе могли ответить на сообщение, которого у нас нет вовсе.
 * Тогда возвращаем цитату без текста — «ответ на сообщение» честнее, чем
 * промолчать и показать реплику как самостоятельную. */
export const quoteOf = (message, index) => {
    const target = Number((message && message.reply_to_tg_message_id) || 0);
    if (!target) return null;
    const found = index && index.get(target);
    if (!found) return { id: null, author: null, text: 'Сообщение недоступно', missing: true };
    if (found.id === message.id) return null;
    return {
        id: found.id,
        author: found.author_name || (found.direction === 'out' ? 'Оператор' : null),
        text: messageSnippet(found),
        missing: false,
    };
};

/* Что делать с вложением: показать картинкой, проиграть или отдать файлом.
 * Смотрим и на тип, и на имя: Telegram отдаёт mime не всегда. */
export const attachmentKind = (attachment) => {
    if (!attachment) return null;
    const mime = String(attachment.mime || '').toLowerCase();
    const name = String(attachment.name || '').toLowerCase();
    const looks = (exts) => exts.some((ext) => name.endsWith(ext));
    if (attachment.kind === 'photo' || mime.startsWith('image/')
        || looks(['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'])) return 'image';
    if (mime.startsWith('video/') || looks(['.mp4', '.mov', '.webm', '.mkv'])) return 'video';
    if (mime.startsWith('audio/') || looks(['.mp3', '.ogg', '.oga', '.m4a', '.wav'])) return 'audio';
    return 'file';
};

/* Фон кружка с инициалами — в том же порядке, что AUTHOR_TONES: имя и кружок
 * одного сотрудника обязаны быть одного цвета, иначе цвет перестаёт что-либо
 * значить и становится просто рябью. */
export const AUTHOR_BG_TONES = [
    'bg-rose-100 text-rose-700', 'bg-amber-100 text-amber-700',
    'bg-emerald-100 text-emerald-700', 'bg-sky-100 text-sky-700',
    'bg-violet-100 text-violet-700', 'bg-fuchsia-100 text-fuchsia-700',
    'bg-teal-100 text-teal-700',
];

/* Тот же хеш, что у authorTone: индекс считается один раз и одинаково. */
const toneIndex = (message) => {
    const key = authorKey(message);
    if (!key) return 0;
    let hash = 0;
    for (let i = 0; i < key.length; i += 1) {
        hash = (hash * 31 + key.charCodeAt(i)) % 100000007;
    }
    return hash % AUTHOR_TONES.length;
};

/* Инициалы: «Асхат Нурланов» → «АН», «@nick» → «N». Две буквы, потому что в
 * рабочей группе половина имён начинается на одну и ту же. */
export const authorInitials = (name) => {
    const words = String(name || '').replace(/^@/, '').trim().split(/[\s._-]+/).filter(Boolean);
    if (!words.length) return '?';
    const letters = words.slice(0, 2).map((word) => word[0]);
    return letters.join('').toUpperCase();
};

export const authorBadge = (message) => {
    const index = toneIndex(message);
    return {
        initials: authorInitials(message && message.author_name),
        tone: AUTHOR_TONES[index],
        bg: AUTHOR_BG_TONES[index],
    };
};

/* ─── Разбивка нити по дням ───────────────────────────────────────────────
 *
 * «Сегодня» / «Вчера» / «12 августа» — плашка между сообщениями, как в любом
 * мессенджере. Без неё у каждой реплики есть время, но нет дня, и переписка
 * на неделю читается как один бесконечный день.
 *
 * Ключ дня считается по ЛОКАЛЬНОЙ дате, а не по срезу ISO-строки: сервер
 * отдаёт время в Asia/Almaty без зоны, но полагаться на формат строки здесь
 * нельзя — «2026-08-20T23:30» и «2026-08-21T01:00» это разные дни, и делить
 * их должен календарь, а не подстрока.
 */
export const dayKey = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    return [date.getFullYear(), date.getMonth() + 1, date.getDate()]
        .map((part) => String(part).padStart(2, '0')).join('-');
};

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

/* Подпись дня. Год добавляется только когда он не текущий: «12 августа 2025»
 * нужно, «12 августа 2026» в 2026 году — шум. */
export const dayLabel = (iso, now = new Date()) => {
    const date = new Date(iso);
    if (!iso || Number.isNaN(date.getTime())) return '';
    const key = dayKey(iso);
    if (key === dayKey(now)) return 'Сегодня';
    const yesterday = new Date(now.getTime());
    yesterday.setDate(yesterday.getDate() - 1);
    if (key === dayKey(yesterday)) return 'Вчера';
    const label = `${date.getDate()} ${MONTHS[date.getMonth()]}`;
    return date.getFullYear() === new Date(now).getFullYear()
        ? label
        : `${label} ${date.getFullYear()}`;
};

/* Нить → [{ key, label, items }]. Сообщения приходят уже по возрастанию
 * времени (queries.list_messages), поэтому дни собираются одним проходом. */
export const groupByDay = (messages, now = new Date()) => {
    const groups = [];
    for (const message of messages || []) {
        if (!message) continue;
        const key = dayKey(message.created_at);
        const last = groups[groups.length - 1];
        if (last && last.key === key) {
            last.items.push(message);
            continue;
        }
        groups.push({ key, label: dayLabel(message.created_at, now), items: [message] });
    }
    return groups;
};

/* Продолжение серии: подряд идущие реплики одного автора в одну сторону и в
 * один день. У продолжения не повторяются ни кружок с инициалами, ни имя — как
 * в Telegram: четыре подряд «Асхат» это подпись к абзацу, а не к сообщению. */
export const continuesRun = (previous, message) => Boolean(
    previous && message
    && previous.direction === message.direction
    && authorKey(previous) === authorKey(message)
    && dayKey(previous.created_at) === dayKey(message.created_at),
);
