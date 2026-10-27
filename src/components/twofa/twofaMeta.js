/*
 * Раздел «Коды 2FA» (#373): время, статусы, вкладки — без React, чтобы проверять тестом.
 *
 * Время приходит с сервера строкой времени Алматы с явным «+05:00» (twofa/rules.py: iso).
 * Часы и минуты берём из самой строки, а не через Date: у сотрудника в командировке
 * браузер показал бы время своего пояса, а в Telegram и в журнале — алматинское.
 */

export const clockOf = (iso, seconds = false) => {
    const match = /T(\d{2}):(\d{2}):(\d{2})/.exec(String(iso || ''));
    if (!match) return '';
    return seconds ? `${match[1]}:${match[2]}:${match[3]}` : `${match[1]}:${match[2]}`;
};

export const dateOf = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return match ? `${match[3]}.${match[2]}` : '';
};

/* «14:31» сегодня, «26.10 14:31» — в другой день (сравниваем с временем сервера). */
export const whenOf = (iso, serverIso) => {
    const day = String(iso || '').slice(0, 10);
    const today = String(serverIso || '').slice(0, 10);
    const clock = clockOf(iso);
    if (!clock) return '';
    return day && today && day !== today ? `${dateOf(iso)} ${clock}` : clock;
};

/* Тон статуса: ждёт — янтарный (горит), подтверждён — зелёный, остальное — серое.
   Цвет только у того, что требует внимания или говорит «готово». */
export const STATUS_TONES = {
    pending: 'amber',
    approved: 'green',
    rejected: 'slate',
    expired: 'slate',
    cancelled: 'slate',
};

export const toneOfStatus = (status) => STATUS_TONES[status] || 'slate';

/* Последний запрос сотрудника по каждому аккаунту: по нему карточка аккаунта говорит,
   что происходит сейчас («ждёт подтверждения», «код отправлен»). Список приходит от
   новых к старым — первый встреченный и есть последний. */
export const latestByAccount = (requests) => {
    const result = {};
    (requests || []).forEach((request) => {
        if (request && result[request.account_id] === undefined) result[request.account_id] = request;
    });
    return result;
};

/* Строка состояния под аккаунтом у сотрудника. null — показывать нечего. */
export const accountStateLine = (request, serverIso) => {
    if (!request) return null;
    const at = (iso) => whenOf(iso, serverIso);
    if (request.status === 'pending') {
        return { tone: 'amber', text: `Ждёт подтверждения · сгорит в ${clockOf(request.expires_at)}` };
    }
    if (request.status === 'approved') {
        if (request.delivery_error) return { tone: 'red', text: `Код не дошёл до Telegram: ${request.delivery_error}` };
        return { tone: 'green', text: `Код отправлен в Telegram · ${at(request.decided_at)}` };
    }
    if (request.status === 'rejected') return { tone: 'slate', text: `Запрос отклонён · ${at(request.decided_at)}` };
    if (request.status === 'expired') return { tone: 'slate', text: 'Запрос сгорел: за 10 минут его не подтвердили' };
    if (request.status === 'cancelled') return { tone: 'slate', text: 'Запрос отменён' };
    return null;
};

/* Вкладки по правам: сотруднику — его коды; главе и супер-админу — работа раздела.
   Если глава сам получил доступ к аккаунту, у него есть и «Мои коды». */
export const tabsFor = (capabilities) => {
    const caps = capabilities || {};
    const tabs = [];
    if (caps.can_manage) tabs.push({ value: 'requests', label: 'Запросы' });
    if (caps.can_request) tabs.push({ value: 'codes', label: caps.can_manage ? 'Мои коды' : 'Коды' });
    if (caps.can_manage) {
        tabs.push({ value: 'accounts', label: 'Аккаунты' });
        tabs.push({ value: 'people', label: 'Доступы' });
        tabs.push({ value: 'journal', label: 'Журнал' });
    }
    return tabs;
};

export const plural = (count, one, few, many) => {
    const n = Math.abs(Number(count) || 0) % 100;
    const last = n % 10;
    if (n > 10 && n < 20) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

export const peopleCount = (count) => `${count} ${plural(count, 'сотрудник', 'сотрудника', 'сотрудников')}`;

export const accountsCount = (count) => `${count} ${plural(count, 'аккаунт', 'аккаунта', 'аккаунтов')}`;

/* ── Разделы аккаунтов (владелец 27.10.2026: «эти 16 сейчас один раздел — Общий маркетинг и
   рекламные аккаунты», новые — при добавлении кодов, ведут глава маркетинга и супер-админы). */

export const UNGROUPED_TITLE = 'Без раздела';
export const NEW_GROUP = '__new__';

/* Аккаунты по разделам — в порядке разделов с сервера (порядок создания); «Без раздела» — в
   конце и только когда там что-то есть. withEmpty — и пустые разделы: у главы их наполняют
   кнопкой «Изменить». Аккаунт с разделом, которого нет в списке, — тоже «Без раздела». */
export const groupAccounts = (accounts, groups, { withEmpty = false } = {}) => {
    const buckets = new Map((groups || []).map((group) => [group.id, []]));
    const loose = [];
    (accounts || []).forEach((account) => {
        const bucket = account.group_id != null ? buckets.get(account.group_id) : null;
        if (bucket) bucket.push(account); else loose.push(account);
    });
    const sections = (groups || [])
        .map((group) => ({ id: group.id, title: group.title, accounts: buckets.get(group.id) }))
        .filter((section) => withEmpty || section.accounts.length);
    if (loose.length) sections.push({ id: null, title: UNGROUPED_TITLE, accounts: loose });
    return sections;
};

/* Варианты поля «Раздел»: существующие и последним — новый. Значения — строки (CustomSelect). */
export const groupOptions = (groups) => [
    ...(groups || []).map((group) => ({ value: String(group.id), label: group.title })),
    { value: NEW_GROUP, label: 'Новый раздел…' },
];

/* Раздел по умолчанию в окне добавления: единственный — он, ни одного — новый, несколько —
   не выбран: «не выбрано» не должно молча стать первым разделом. */
export const defaultGroupChoice = (groups) => {
    const list = groups || [];
    if (list.length === 1) return String(list[0].id);
    return list.length ? '' : NEW_GROUP;
};

/* Чего не хватает в поле «Раздел» — текст у поля; null — всё есть. */
export const groupChoiceError = (choice, newTitle) => {
    if (!choice) return 'Выберите раздел';
    if (choice === NEW_GROUP && !String(newTitle || '').trim()) return 'Укажите название раздела';
    return null;
};

/* Выбор в поле «Раздел» → поля запроса: новый — названием, существующий — id. */
export const groupPayload = (choice, newTitle) => {
    if (choice === NEW_GROUP) return { group_title: String(newTitle || '').trim() };
    return choice ? { group_id: Number(choice) } : {};
};

/* Хвост строки журнала о разделах: куда добавлен, откуда и куда перенесён, как переименован. */
export const journalGroupNote = (item) => {
    const details = item?.details || {};
    const quoted = (title) => `«${title}»`;
    switch (item?.kind) {
    case 'account_added':
        return details.group ? `в ${quoted(details.group)}` : '';
    case 'account_moved':
        if (details.from_group && details.to_group) return `из ${quoted(details.from_group)} в ${quoted(details.to_group)}`;
        if (details.to_group) return `в ${quoted(details.to_group)}`;
        return details.from_group ? `из ${quoted(details.from_group)} — без раздела` : '';
    case 'group_created':
        return details.group ? quoted(details.group) : '';
    case 'group_renamed':
        return details.from && details.to ? `${quoted(details.from)} → ${quoted(details.to)}` : '';
    case 'group_deleted':
        return [details.group && quoted(details.group),
            details.accounts ? `${accountsCount(details.accounts)} — без раздела` : ''].filter(Boolean).join(' · ');
    case 'groups_introduced':
        return [details.group && quoted(details.group),
            details.accounts != null ? accountsCount(details.accounts) : ''].filter(Boolean).join(' · ');
    default:
        return '';
    }
};

/* ── Страницы и поиск (владелец 27.10.2026: «не забудь использовать пагинацию») ── */

// Карточек аккаунтов на страницу раздела: делится и на две колонки, и на три.
export const ACCOUNTS_PAGE_SIZE = 12;
// Строк сотрудников во «Доступах» и плиток аккаунтов в окнах (доступы, состав раздела).
export const PEOPLE_PAGE_SIZE = 10;
export const TILES_PAGE_SIZE = 8;

/* Страница списка: номер вне диапазона прижимается к краю — после удаления последней
   строки страницы человек не остаётся на пустой странице. */
export const pageOf = (list, page, size) => {
    const items = list || [];
    const total = items.length;
    const pageCount = Math.max(1, Math.ceil(total / size));
    const current = Math.min(Math.max(1, Number(page) || 1), pageCount);
    const start = (current - 1) * size;
    return {
        items: items.slice(start, start + size),
        page: current,
        pageCount,
        total,
        from: total ? start + 1 : 0,
        to: Math.min(start + size, total),
    };
};

/* Поиск без учёта регистра и «ё»: «Аккаунты Ёлки» находятся по «елки». */
const fold = (text) => String(text || '').toLowerCase().replace(/ё/g, 'е');

export const matchesQuery = (query, ...fields) => {
    const needle = fold(query).trim();
    if (!needle) return true;
    return fields.some((field) => fold(field).includes(needle));
};

export const filterAccounts = (accounts, query) => (accounts || [])
    .filter((account) => matchesQuery(query, account.title, account.service));

export const filterPeople = (people, query) => (people || [])
    .filter((person) => matchesQuery(query, person.name, person.department));

/* Плитки-разделы над аккаунтами: «Все», разделы по порядку и «Без раздела», если там есть
   что-то. Счётчики — по найденному поиском: видно, в каком разделе совпадения. */
export const ALL_SECTIONS = 'all';
export const UNGROUPED = 'none';

export const sectionTiles = (accounts, groups, matched = accounts) => {
    const counts = new Map();
    let loose = 0;
    const known = new Set((groups || []).map((group) => group.id));
    (matched || []).forEach((account) => {
        if (account.group_id != null && known.has(account.group_id)) {
            counts.set(account.group_id, (counts.get(account.group_id) || 0) + 1);
        } else {
            loose += 1;
        }
    });
    const hasLoose = (accounts || []).some((account) => account.group_id == null || !known.has(account.group_id));
    return [
        { key: ALL_SECTIONS, title: 'Все', count: (matched || []).length },
        ...(groups || []).map((group) => ({ key: String(group.id), id: group.id, title: group.title, count: counts.get(group.id) || 0 })),
        ...(hasLoose ? [{ key: UNGROUPED, id: null, title: UNGROUPED_TITLE, count: loose }] : []),
    ];
};

/* Плитка аккаунта — буква сервиса на мягком цвете, как у сайтов в «Паролях» iOS: одинаковый
   сервис — одинаковый цвет, на любом экране и у любого человека. */
// Без розового: в разделе он значит «мешает получить код» («Код не дошёл», «Telegram не привязан»).
export const MONOGRAM_TONES = [
    'bg-blue-50 text-blue-700', 'bg-emerald-50 text-emerald-700', 'bg-amber-50 text-amber-700',
    'bg-violet-50 text-violet-700', 'bg-sky-50 text-sky-700', 'bg-teal-50 text-teal-700',
    'bg-indigo-50 text-indigo-700',
];

/* Край ленты, которая листается вбок (плитки разделов на телефоне), гаснет маской — и только
   с той стороны, где за ним что-то есть: обрезанная посреди плитка выглядит браком. Тот же
   приём, что edgeFade у доски «Оплаты счетов». */
export const EDGE_MASK = 20;

export const edgeMask = (left, right, size = EDGE_MASK) => {
    if (!left && !right) return undefined;
    const image = `linear-gradient(to right, ${left ? 'transparent' : '#000'} 0, #000 ${left ? size : 0}px, `
        + `#000 calc(100% - ${right ? size : 0}px), ${right ? 'transparent' : '#000'} 100%)`;
    return { maskImage: image, WebkitMaskImage: image };
};

export const monogramOf = (account) => {
    const source = String(account?.service || account?.title || '').trim();
    const letter = (source.match(/[\p{L}\p{N}]/u) || ['•'])[0].toUpperCase();
    let hash = 0;
    for (const char of fold(source)) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
    return { letter, tone: MONOGRAM_TONES[hash % MONOGRAM_TONES.length] };
};

/* Строка журнала: значок и цвет по виду события — глаз находит выдачи, отказы и правки
   доступа, не читая подписи. */
const JOURNAL_KINDS = {
    requested: { icon: 'request', tone: 'blue' },
    approved: { icon: 'approve', tone: 'green' },
    code_sent: { icon: 'code', tone: 'green' },
    code_resent: { icon: 'code', tone: 'green' },
    rejected: { icon: 'reject', tone: 'slate' },
    expired: { icon: 'clock', tone: 'slate' },
    cancelled: { icon: 'reject', tone: 'slate' },
    code_failed: { icon: 'alert', tone: 'red' },
    account_added: { icon: 'account', tone: 'blue' },
    account_renamed: { icon: 'edit', tone: 'slate' },
    account_removed: { icon: 'remove', tone: 'red' },
    account_moved: { icon: 'folder', tone: 'slate' },
    access_granted: { icon: 'access', tone: 'blue' },
    access_revoked: { icon: 'access', tone: 'slate' },
    group_created: { icon: 'folder', tone: 'blue' },
    group_renamed: { icon: 'edit', tone: 'slate' },
    group_deleted: { icon: 'remove', tone: 'red' },
    groups_introduced: { icon: 'folder', tone: 'slate' },
};

export const journalKind = (kind) => JOURNAL_KINDS[kind] || { icon: 'dot', tone: 'slate' };

/* Порядок проверки формы добавления: первое, чего не хватает, — туда и прокрутка. */
export const manualErrors = ({ title, secret }) => {
    const errors = {};
    if (!String(title || '').trim()) errors.title = 'Укажите название';
    if (!String(secret || '').trim()) errors.secret = 'Вставьте ключ из настроек 2FA сервиса';
    return errors;
};

export const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;
