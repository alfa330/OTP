/* Правила отображения СТРОКИ обращения в ленте. Без React — по той же
 * причине, что и threadView.js: логика, живущая в разметке, в этом разделе уже
 * стоила четырёх нерабочих тематик (см. wizardRules.js).
 *
 * Строка ленты отвечает на четыре вопроса и ни на один больше:
 *   куда ушло          — плитка с монограммой очереди;
 *   о чём              — тема;
 *   что там последнее  — превью реплики («Вы: …», «Асхат: Фото»);
 *   ждёт ли меня       — пузырёк с числом непрочитанного.
 *
 * Всё остальное (просрочено, не доставлено, массовый сбой, приоритет) —
 * исключения, и они показываются только когда случились.
 */

/* Плитка очереди. Цвет выводится из id, а не из названия: очередь можно
 * переименовать, и плитка не должна менять цвет — по нему её узнают быстрее,
 * чем по буквам.
 *
 * Шесть спокойных пар вместо радуги: плиток на экране сорок, и каждая новая
 * краска в ленте — это минус к тому, как читаются настоящие сигналы (пузырёк
 * непрочитанного, «не доставлено»). */
export const QUEUE_TILES = [
    'bg-blue-50 text-blue-600 ring-blue-100',
    'bg-violet-50 text-violet-600 ring-violet-100',
    'bg-teal-50 text-teal-600 ring-teal-100',
    'bg-orange-50 text-orange-600 ring-orange-100',
    'bg-cyan-50 text-cyan-600 ring-cyan-100',
    'bg-indigo-50 text-indigo-600 ring-indigo-100',
];

export const queueTile = (queueId) => {
    const id = Number(queueId);
    if (!Number.isFinite(id)) return QUEUE_TILES[0];
    return QUEUE_TILES[Math.abs(Math.trunc(id)) % QUEUE_TILES.length];
};

/* Монограмма очереди: «iTaxi Sapar» → «iS», «Посылки» → «По».
 *
 * Две буквы, и вторая берётся из второго слова, если оно есть: у «Яндекс
 * Доставка» и «Яндекс Еда» первая буква одна и та же, и монограмма из одного
 * слова их не различала бы. Регистр НЕ поднимаем: «iTaxi» с большой I — это уже
 * другое название. */
export const queueMonogram = (title) => {
    const words = String(title || '').trim().split(/\s+/).filter(Boolean);
    if (!words.length) return '—';
    if (words.length === 1) return words[0].slice(0, 2);
    return words[0][0] + words[1][0];
};

/* Кто написал последнюю реплику — так, как это подписывают в мессенджере.
 * «Вы» у своих: имя автора в своей же переписке ничего не добавляет.
 * Заметка (direction 'note') автора не имеет вовсе. */
export const previewAuthor = (message) => {
    if (!message) return null;
    if (message.direction === 'out') return 'Вы';
    if (message.direction === 'note') return null;
    const name = String(message.author_name || '').trim();
    if (!name) return null;
    // В ленте у строки одна строка на превью, поэтому от «Асхат Нурланов»
    // берём имя: фамилия съела бы сам текст реплики.
    return name.replace(/^@/, '').split(/\s+/)[0];
};

const ATTACHMENT_WORDS = {
    photo: 'Фото',
    image: 'Фото',
    video: 'Видео',
    voice: 'Голосовое',
    audio: 'Аудио',
    document: 'Файл',
};

/* Текст превью. Вложение без подписи — не пустая строка, а «Фото»: пустое
 * превью выглядит как сломанная строка, а не как сообщение без текста. */
export const previewText = (message, limit = 80) => {
    if (!message) return '';
    const text = String(message.body || '').replace(/\s+/g, ' ').trim();
    if (text) return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
    const attachment = message.attachment;
    if (!attachment) return '';
    return attachment.name || ATTACHMENT_WORDS[attachment.kind] || 'Файл';
};

/* Пузырёк непрочитанного. Больше 99 не показываем числом: четыре цифры ломают
 * ширину строки, а разница между 100 и 137 непрочитанными ни на что не влияет. */
export const unreadLabel = (count) => {
    const value = Math.max(0, Math.trunc(Number(count) || 0));
    if (!value) return '';
    return value > 99 ? '99+' : String(value);
};

/* «13 обращений» / «2 обращения» / «1 обращение».
 *
 * Нужно ровно в одном месте — в вопросе перед удалением отобранных, — и там
 * важно каждое слово: «Удалить 13 обращение?» на экране, после которого ничего
 * не вернуть, читается как поломка, а не как вопрос. Своя функция в разделе, а
 * не общая на портал: в каждом разделе она уже своя (задачи, опросы, табло), и
 * заводить общую сейчас значило бы переписывать пять чужих.
 */
export const pluralTickets = (count) => {
    const value = Math.abs(Math.trunc(Number(count) || 0));
    const tail = value % 100;
    if (tail >= 11 && tail <= 14) return 'обращений';
    switch (value % 10) {
        case 1: return 'обращение';
        case 2:
        case 3:
        case 4: return 'обращения';
        default: return 'обращений';
    }
};

/* Что в строке требует действия. Одно значение, а не набор флагов: у строки
 * ровно одна левая полоска и один самый важный бейдж, и порядок здесь — это
 * порядок срочности.
 *
 * 'failed'  — обращение вообще не ушло в Telegram: пока не переотправишь, его
 *             никто не увидит, это хуже любого срока.
 * 'overdue' — срок ответа вышел.
 * null      — ничего чинить не нужно.
 */
const OPEN_STATUSES = ['open', 'in_progress', 'answered'];

/* Просрочен ли срок ответа. Только для незакрытых: у решённого обращения срок
 * уже ничего не значит, и красить его — врать про состояние дел. */
export const isOverdue = (ticket, now = Date.now()) => Boolean(
    ticket
    && ticket.due_at
    && OPEN_STATUSES.includes(ticket.status)
    && new Date(ticket.due_at).getTime() < now,
);

export const rowAlert = (ticket, now = Date.now()) => {
    if (!ticket) return null;
    if (ticket.delivery_status === 'failed') return 'failed';
    if (isOverdue(ticket, now)) return 'overdue';
    return null;
};

/* ─── Проверка супервайзером до группы ────────────────────────────────────── */

/* Состояния проверки — те же слова, что на сервере (crm/schema.py: REVIEW_*) и
 * у жалоб на Яндекс. Проходят её не все обращения, а те, которым это объявила
 * тематика: сейчас «Сотрудничество с Яндексом» (возврат задачи #297). */
export const REVIEW_PENDING = 'pending';
export const REVIEW_SENT = 'sent';
export const REVIEW_RESOLVED = 'resolved';

export const isUnderReview = (ticket) => Boolean(
    ticket && ticket.review_state === REVIEW_PENDING);

/* Как назвать состояние обращения в шапке карточки.
 *
 * Обращение на проверке в группу не уходило, и штатное «Отправлено» про него —
 * неправда. Тон нейтральный: автору здесь делать нечего, а проверяющему о его
 * задаче говорят бейдж строки и панель решения в карточке. */
export const statusView = (ticket, status) => (
    isUnderReview(ticket) ? { label: 'На проверке', tone: null } : status
);

/* Подпись внизу карточки, когда писать в обращение нельзя.
 *
 * Три разных «нельзя», и объяснять их одной фразой значило бы не объяснить:
 * на проверке — в группу ещё не ушло, и писать некуда (слова те же, что у
 * жалобы на проверке); решено супервайзером — в группу не уходило вовсе;
 * остальное закрытое — обычное «закрыто».
 *
 * У решённого супервайзером фраза говорит только то, чего на экране ещё нет:
 * что оно решено и кем — уже стоит плашкой под итогом, строкой выше. */
export const lockedReplyText = (ticket) => {
    if (isUnderReview(ticket)) {
        return 'Ждёт проверки супервайзером — в группу уйдёт, только если он решит';
    }
    if (ticket && ticket.review_state === REVIEW_RESOLVED) {
        return 'В группу это обращение не уходило';
    }
    if (ticket && (ticket.status === 'resolved' || ticket.status === 'cancelled')) {
        return 'Обращение закрыто';
    }
    return 'Писать в это обращение нельзя';
};

/* Сегмент «На проверку» в фильтре состояния: обращения, которые ждут решения
 * ИМЕННО этого человека (на сервере — review=1, queries.review_task_sql).
 *
 * Сегмента нет, пока проверять нечего: у оператора и у супервайзера без задач
 * фильтр остаётся прежним, четыре сегмента. Появляется он вместе с первой
 * задачей и стоит в КОНЦЕ — остальные сегменты от этого не сдвигаются. И не
 * исчезает из-под руки: пока он выбран, он на месте, даже когда последнее
 * обращение решено, — иначе человек остался бы в фильтре, которого на экране
 * уже нет. */
export const REVIEW_FILTER = 'review';

export const stateFilters = (base, { reviewCount = 0, selected = '' } = {}) => {
    const count = Math.max(0, Math.trunc(Number(reviewCount) || 0));
    if (!count && selected !== REVIEW_FILTER) return base;
    return [...base, { key: REVIEW_FILTER, label: 'На проверку', statuses: '', count }];
};

/* Что сказать проверяющему после решения. { text, tone }.
 *
 * «В группу» — это решение плюс доставка, и второе может не случиться (бота
 * выгнали из группы): решение при этом принято, а повтор отправки — обычной
 * кнопкой в шапке карточки. Поэтому ошибка говорит ровно про доставку. */
export const reviewToast = (decision, result) => {
    if (decision === 'resolve') return { tone: 'success', text: 'Решено — обращение закрыто' };
    if (result && result.delivered) return { tone: 'success', text: 'Обращение отправлено в группу' };
    return {
        tone: 'error',
        text: `В группу не ушло: ${(result && result.delivery_error) || 'ошибка Telegram'}`,
    };
};

/* Склейка страниц ленты по id.
 *
 * Простая конкатенация здесь стала неверной, и виноват как раз новый порядок
 * «непрочитанное сверху». Ведущий член сортировки теперь меняется от действия
 * самого человека: открыл обращение — сервер погасил «непрочитано», строка
 * переехала из верхнего яруса в ярус свежести, и всё, что было выше, сдвинулось
 * на одну позицию. Следующее «Показать ещё» с OFFSET 40 в этот момент молча
 * пропускает одну строку, а пришедший ответ в обратную сторону — так же молча
 * даёт дубль (React ещё и предупредит про повторяющийся key).
 *
 * Переходить на keyset-пагинацию ради этого нельзя: границу яруса в один
 * курсорный кортеж не упаковать. Зато склейка по id делает догрузку
 * идемпотентной — а «пропущенную» строку всё равно принесёт следующее
 * обновление списка.
 *
 * Новая версия строки предпочитается старой: в догруженной странице у неё
 * свежее время и счётчик.
 */
export const mergeTicketsById = (previous, next) => {
    const fresh = new Map((next || []).filter(Boolean).map((item) => [item.id, item]));
    const merged = (previous || []).filter(Boolean).map(
        (item) => (fresh.has(item.id) ? fresh.get(item.id) : item),
    );
    const seen = new Set(merged.map((item) => item.id));
    for (const item of next || []) {
        if (item && !seen.has(item.id)) {
            seen.add(item.id);
            merged.push(item);
        }
    }
    return merged;
};

/* Погасить «непрочитано» у одной строки, не перезапрашивая ленту.
 *
 * Перезапрос был бы проще, но вреден: список отсортирован «непрочитанное
 * сверху», и обращение, которое человек прямо сейчас читает, уехало бы из-под
 * курсора вниз. Ссылку на массив меняем только если что-то действительно
 * изменилось — иначе лента перерисовывается на каждое открытие карточки. */
export const markTicketSeen = (tickets, ticketId) => {
    const id = Number(ticketId);
    let changed = false;
    const next = (tickets || []).map((item) => {
        if (item.id !== id || (!item.unread && !item.unread_count)) return item;
        changed = true;
        return { ...item, unread: false, unread_kind: null, unread_count: 0 };
    });
    return changed ? next : tickets;
};

/* Сколько бейджей помещается в строку ленты, не перенося её. Два.
 *
 * Число не из головы: на ленте 360 px третий бейдж уезжает на новую строку, и
 * ряды становятся рваными — 79 px, 98, 79, 98. Сорок строк одной высоты
 * читаются заметно быстрее сорока разных, даже если в каждой стало на одно
 * слово меньше. Всё, что не поместилось, видно в самой карточке. */
export const BADGE_LIMIT = 2;

/* Какие бейджи показать в строке. Порядок — порядок важности:
 *   что сломано (не ушло / просрочено) → насколько срочно → в каком контексте.
 *
 * Подписи статуса и приоритета приходят снаружи: их словарь — дело интерфейса,
 * а здесь решается ЧТО показать и сколько.
 *
 * Статус не дублируется, когда рядом уже висит пузырёк непрочитанного: «Есть
 * ответ» и число ответов — одно и то же сообщение, сказанное дважды.
 */
export const rowBadges = (ticket, meta = {}, now = Date.now()) => {
    if (!ticket) return [];
    const badges = [];
    const alert = rowAlert(ticket, now);
    if (alert === 'failed') {
        badges.push({ key: 'failed', tone: 'red', label: 'Не доставлено' });
    } else if (alert === 'overdue') {
        badges.push({ key: 'overdue', tone: 'amber', label: 'Просрочено' });
    } else if (isUnderReview(ticket)) {
        // «Ждёт проверки» горит только у того, чья это задача (review_mine
        // считает сервер тем же правилом, что счётчик и колокол). Автору и
        // всем остальным — спокойное «На проверке»: им тут делать нечего.
        badges.push(ticket.review_mine
            ? { key: 'review', tone: 'amber', label: 'Ждёт проверки' }
            : { key: 'review', tone: 'slate', label: 'На проверке' });
    } else if (!ticket.unread && meta.status && meta.status.tone) {
        badges.push({ key: 'status', tone: meta.status.tone, label: meta.status.label });
    }
    if (meta.priority && meta.priority.tone) {
        badges.push({ key: 'priority', tone: meta.priority.tone, label: meta.priority.label });
    }
    if ((ticket.flags || []).includes('mass_outage')) {
        badges.push({ key: 'mass_outage', tone: 'red', label: 'Массовый сбой' });
    }
    return badges.slice(0, BADGE_LIMIT);
};
