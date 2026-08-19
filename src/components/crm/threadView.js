/* Правила отображения переписки обращения. Без React — чтобы их можно было
 * проверить: в этом разделе логика, жившая в разметке, уже однажды стоила
 * четырёх нерабочих тематик (см. wizardRules.js).
 *
 * Здесь три вещи, и каждая — следствие одного факта: в нить падает ВСЯ ветка
 * обсуждения из группы, а не только ответы боту.
 *   кто кому отвечал  — цитата над сообщением;
 *   кто говорит       — цвет имени;
 *   что в сообщении   — вид вложения.
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
