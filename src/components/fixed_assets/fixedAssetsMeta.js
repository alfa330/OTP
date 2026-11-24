import axios from 'axios';

/*
 * «Основные средства» (ACC-1): подписи, тона, даты и разбор форм — всё, что экран знает о
 * разделе помимо разметки. Правила (кто что может, чего не хватает запросу) считает сервер
 * (fixed_assets/access.py, rules.py) и отдаёт готовым списком действий; здесь — только то,
 * как это назвать и показать.
 */

export const KIND_LABELS = {
    intake: 'Приём в учёт',
    transfer: 'Перемещение',
    writeoff: 'Списание',
    dismissal: 'Передача при увольнении',
    need: 'Новое ОС',
};

/* Пункты кнопки «Новый запрос». */
export const KIND_MENU = {
    transfer: { label: 'Перемещение' },
    writeoff: { label: 'Списание' },
    dismissal: { label: 'Передача при увольнении' },
    need: { label: 'Новое ОС' },
};

export const STATUS_LABELS = {
    draft: 'Черновик',
    review: 'На проверке',
    signing: 'На подписании',
    original: 'Ждёт оригинал в бухгалтерии',
    closed: 'Закрыт',
    rejected: 'Отклонён',
};
export const STATUS_SHORT = { ...STATUS_LABELS, original: 'Ждёт оригинал' };
export const STATUS_ORDER = ['draft', 'review', 'signing', 'original', 'closed', 'rejected'];

/* Цвет — только у состояний со смыслом: проверка — янтарь, подписание — синий, оригинал —
   фиолетовый, закрыт — зелёный, отклонён — красный. Черновик нейтрален. */
export const STATUS_TONE = {
    draft: 'slate',
    review: 'amber',
    signing: 'blue',
    original: 'violet',
    closed: 'green',
    rejected: 'rose',
};

export const TABS = {
    my: { title: 'Мои ОС' },
    registry: { title: 'Реестр' },
    requests: { title: 'Запросы' },
    dismissal: { title: 'Увольнение' },
    history: { title: 'История' },
    settings: { title: 'Настройки' },
};

/* Срезы реестра — плитки над списком. */
export const ASSET_SLICES = [
    { key: '', label: 'Все ОС', counter: 'all', tone: 'blue' },
    { key: 'in_use', label: 'У сотрудников', counter: 'in_use', tone: 'green' },
    { key: 'stock', label: 'На складе', counter: 'stock', tone: 'violet' },
    { key: 'busy', label: 'В запросах', counter: 'busy', tone: 'amber' },
    { key: 'written_off', label: 'Списано', counter: 'written_off', tone: 'slate' },
];

/* ── Доска запросов (владелец 17.11.2026: «запросы надо бы оформить в виде канбан-доски») ──
   Колонка — статус запроса, в порядке пути запроса. «Черновики» видит только их автор — колонка
   стоит на доске, пока они есть. В отборе «Ждут меня» закрытых и отклонённых нет. */
export const BOARD_COLUMNS = [
    { status: 'draft', title: 'Черновики', tone: 'slate', optional: true },
    { status: 'review', title: 'На проверке', tone: 'amber' },
    { status: 'signing', title: 'На подписании', tone: 'blue' },
    { status: 'original', title: 'Ждут оригинал', tone: 'violet' },
    { status: 'closed', title: 'Закрыты', tone: 'green', done: true },
    { status: 'rejected', title: 'Отклонены', tone: 'rose', done: true },
];
/* Карточек в колонке сразу; остальное — «Посмотреть ещё» (сервер: BOARD_PAGE в routes.py). */
export const BOARD_PAGE = 30;

export const boardColumns = (totals = {}, { waiting = false, extra = [] } = {}) => BOARD_COLUMNS.filter((column) => {
    // extra — колонки, куда сейчас можно бросить несомую карточку: они видны всегда.
    if (extra.includes(column.status)) return true;
    if (waiting && column.done) return false;
    if (column.optional) return Number(totals[column.status] || 0) > 0;
    return true;
});

/* Перетащить карточку в колонку — значит сделать шаг запроса. Сам шаг (скан, оригинал, причина
   отказа) спрашивает карточка запроса, которая открывается после броска; кнопки в ней — те же. */
export const MOVE_STEPS = ['submit', 'check', 'accept', 'scan', 'receive', 'reject'];
const STEP_TARGET = { check: 'signing', accept: 'closed', scan: 'original', receive: 'closed', reject: 'rejected' };
export const stepTarget = (step, kind) => {
    // Отправленное списание и «Новое ОС» сначала проверяют; перемещение и передача — сразу на подписи.
    if (step === 'submit') return (kind === 'writeoff' || kind === 'need') ? 'review' : 'signing';
    return STEP_TARGET[step] || null;
};
/* Колонка → шаг, которым туда попадает эта карточка. Шаги — из списка действий с сервера
   (access.allowed_actions): без права шага колонка не принимает карточку. */
export const dropTargets = (item) => {
    const map = {};
    (item?.actions || []).forEach((step) => {
        if (!MOVE_STEPS.includes(step)) return;
        const target = stepTarget(step, item.kind);
        if (target && target !== item.status && !map[target]) map[target] = step;
    });
    return map;
};

/* Два обязательных снимка ОС (ТЗ: «первое показывает ОС целиком, второе даёт чёткий
   инвентарный номер») и необязательные дополнительные. */
export const PHOTO_WHOLE = 'photo_whole';
export const PHOTO_NUMBER = 'photo_number';
export const PHOTO_EXTRA = 'photo_extra';
export const PHOTO_SLOTS = [
    { kind: PHOTO_WHOLE, label: 'ОС целиком' },
    { kind: PHOTO_NUMBER, label: 'Инвентарный номер' },
];
export const PHOTO_LABELS = { [PHOTO_WHOLE]: 'ОС целиком', [PHOTO_NUMBER]: 'инвентарный номер', [PHOTO_EXTRA]: 'ещё фото' };

export const CHECK_BY_LABELS = { it: 'IT-специалист', head: 'Руководитель отдела' };

export const EVENT_OPTIONS = [
    { value: 'registered', label: 'Принято в учёт' },
    { value: 'submitted', label: 'Запрос отправлен' },
    { value: 'checked', label: 'Проверено' },
    { value: 'scan_attached', label: 'Приложен скан акта' },
    { value: 'original_received', label: 'Оригинал получен' },
    { value: 'moved', label: 'Перемещено' },
    { value: 'written_off', label: 'Списано' },
    { value: 'rejected', label: 'Запрос отклонён' },
    { value: 'corrected', label: 'Карточка исправлена' },
    { value: 'barcode_set', label: 'Штрихкод наклейки' },
];

/* Отбор истории по времени — готовыми отрезками: точные даты нужны редко, а два поля дат
   занимают полпанели. */
export const PERIOD_OPTIONS = [
    { value: 'today', label: 'Сегодня' },
    { value: 'week', label: '7 дней' },
    { value: 'month', label: '30 дней' },
    { value: 'this_month', label: 'Этот месяц' },
    { value: 'last_month', label: 'Прошлый месяц' },
];

/* В сетке карточек 2, 3 или 4 колонки — страница делится на все три без неполной строки. */
export const LIST_PAGE_SIZE = 24;
export const HISTORY_PAGE_SIZE = 40;

/* ── Даты и числа ────────────────────────────────────────────────────────────
   Сервер отдаёт настенное время Алматы без пояса («2026-11-17T12:34:56»). Режем строку,
   а не строим Date: тот сдвинул бы время на пояс браузера. */
export const fmtDate = (iso) => {
    const text = String(iso || '');
    if (text.length < 10) return '';
    return `${text.slice(8, 10)}.${text.slice(5, 7)}.${text.slice(0, 4)}`;
};

export const fmtDateTime = (iso) => {
    const text = String(iso || '');
    if (text.length < 16) return fmtDate(text);
    return `${fmtDate(text)} ${text.slice(11, 16)}`;
};

export const fmtTime = (iso) => {
    const text = String(iso || '');
    return text.length >= 16 ? text.slice(11, 16) : '';
};

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const MONTHS_LONG = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября',
    'ноября', 'декабря'];

/* «17 ноя» — короткая дата для списков; год дописывается, когда он не текущий. */
export const fmtDay = (iso, today = todayIso()) => {
    const text = String(iso || '');
    if (text.length < 10) return '';
    const month = MONTHS_SHORT[Number(text.slice(5, 7)) - 1] || '';
    const base = `${Number(text.slice(8, 10))} ${month}`;
    return text.slice(0, 4) === String(today).slice(0, 4) ? base : `${base} ${text.slice(0, 4)}`;
};

/* «17 ноября» — заголовок дня в истории: «Сегодня», «Вчера», дальше дата; год — когда не текущий. */
export const fmtDayLong = (iso, today = todayIso()) => {
    const text = String(iso || '').slice(0, 10);
    if (text.length < 10) return '';
    if (text === today) return 'Сегодня';
    if (text === shiftDay(today, -1)) return 'Вчера';
    const base = `${Number(text.slice(8, 10))} ${MONTHS_LONG[Number(text.slice(5, 7)) - 1] || ''}`;
    return text.slice(0, 4) === String(today).slice(0, 4) ? base : `${base} ${text.slice(0, 4)}`;
};

/* Сегодня по Алматы (UTC+5, без перевода часов) — для полей дат «по умолчанию сегодня». */
export function todayIso(now = new Date()) {
    const shifted = new Date(now.getTime() + 5 * 60 * 60 * 1000);
    return shifted.toISOString().slice(0, 10);
}

/* День плюс-минус N суток — арифметикой календаря в UTC, пояс браузера не участвует. */
export function shiftDay(iso, days) {
    const [year, month, day] = String(iso).slice(0, 10).split('-').map(Number);
    return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

/* Отрезок истории → { date_from, date_to } для сервера. */
export function periodRange(period, today = todayIso()) {
    if (period === 'today') return { date_from: today, date_to: today };
    if (period === 'week') return { date_from: shiftDay(today, -6), date_to: today };
    if (period === 'month') return { date_from: shiftDay(today, -29), date_to: today };
    const first = `${today.slice(0, 8)}01`;
    if (period === 'this_month') return { date_from: first, date_to: today };
    if (period === 'last_month') {
        const last = shiftDay(first, -1);
        return { date_from: `${last.slice(0, 8)}01`, date_to: last };
    }
    return {};
}

/* Записи истории, собранные по дням в том порядке, в каком пришли: [{ day, events }]. */
export function groupByDay(events) {
    const groups = [];
    (events || []).forEach((event) => {
        const day = String(event.created_at || '').slice(0, 10);
        const last = groups[groups.length - 1];
        if (last && last.day === day) last.events.push(event);
        else groups.push({ day, events: [event] });
    });
    return groups;
}

export const fmtMoney = (value) => {
    if (value === null || value === undefined || value === '') return '';
    const number = Number(value);
    if (!Number.isFinite(number)) return '';
    const [whole, fraction] = number.toFixed(2).split('.');
    // Неразрывные пробелы: «389 900 ₸» не рвётся на две строки посреди числа.
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    return `${grouped}${fraction === '00' ? '' : `,${fraction}`} ₸`;
};

export function plural(count, forms) {
    const number = Math.abs(Number(count) || 0);
    const tens = number % 100;
    const ones = number % 10;
    if (tens >= 11 && tens <= 14) return forms[2];
    if (ones === 1) return forms[0];
    if (ones >= 2 && ones <= 4) return forms[1];
    return forms[2];
}

export const countText = (count, forms) => `${count} ${plural(count, forms)}`;
export const ASSET_FORMS = ['ОС', 'ОС', 'ОС'];
export const REQUEST_FORMS = ['запрос', 'запроса', 'запросов'];
export const EVENT_FORMS = ['запись', 'записи', 'записей'];

export const fileSizeLabel = (bytes) => {
    const size = Number(bytes) || 0;
    if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`;
    if (size >= 1024) return `${Math.round(size / 1024)} КБ`;
    return `${size} Б`;
};

/* ── Подписи ───────────────────────────────────────────────────────────────── */

/* «Иванов Иван Иванович» → «Иванов И. И.» — в строках списков полное ФИО не помещается. */
export const shortName = (fullName) => {
    const parts = String(fullName || '').trim().split(/\s+/).filter(Boolean);
    if (parts.length < 2) return parts[0] || '';
    return `${parts[0]} ${parts.slice(1, 3).map((part) => `${part[0]}.`).join(' ')}`;
};

/* Две буквы для кружка с лицом: фамилия и имя. */
export const initials = (fullName) => {
    const parts = String(fullName || '').trim().split(/\s+/).filter(Boolean);
    return parts.slice(0, 2).map((part) => part[0].toUpperCase()).join('');
};

export const requestTitle = (request) => (request ? `${KIND_LABELS[request.kind] || 'Запрос'} № ${request.id}` : '');

/* Где ОС сейчас: «Склад» важнее названия отдела — так его ищет IT. Если склад уже назван в
   самом месте («Алматы, склад IT»), второй раз слово не пишется. */
const namesStock = (location) => /склад/i.test(String(location || ''));
export const placeText = (asset) => [asset?.on_stock && !namesStock(asset?.location) ? 'Склад' : '', asset?.location]
    .filter(Boolean).join(' · ');

/* Состояние ОС одной меткой: списано → в запросе → на складе → на учёте. */
export const assetState = (asset) => {
    if (!asset) return { label: '', tone: 'slate' };
    if (asset.status === 'written_off') return { label: 'Списано', tone: 'muted' };
    if (asset.open_request) {
        return { label: KIND_LABELS[asset.open_request.kind] || 'В запросе', tone: 'amber', request: asset.open_request };
    }
    if (asset.on_stock) return { label: 'На складе', tone: 'blue' };
    return { label: 'На учёте', tone: 'slate' };
};

/* Метка в карточке списка — только у необычного: «На учёте» у каждого ОС читалось бы шумом.
   Вид запроса — коротким словом: «Передача при увольнении» в карточку не помещается. */
const KIND_SHORT = { transfer: 'Перемещение', writeoff: 'Списание', dismissal: 'Передача' };
export const assetFlag = (asset) => {
    const state = assetState(asset);
    if (!state.label || state.label === 'На учёте') return null;
    return state.request ? { ...state, label: KIND_SHORT[state.request.kind] || state.label } : state;
};

export const statusLabel = (request) => request?.status_label || STATUS_LABELS[request?.status] || '';

/* Кому что передаётся — строкой «Иванов И. И. → Петров П. П.». */
export const routeText = (request) => {
    if (!request) return '';
    if (request.kind === 'need') return request.need_for_name ? `кому: ${shortName(request.need_for_name)}` : '';
    if (request.kind === 'intake') return request.to_name ? `МОЛ: ${shortName(request.to_name)}` : '';
    if (request.kind === 'writeoff') return request.from_name ? `МОЛ: ${shortName(request.from_name)}` : '';
    const from = request.from_name ? shortName(request.from_name) : '—';
    const to = request.to_name ? shortName(request.to_name) : '—';
    return `${from} → ${to}${request.to_stock ? ' (склад)' : ''}`;
};

export const requestSubject = (request) => {
    if (!request) return '';
    if (request.kind === 'need') return request.need_title || 'Новое ОС';
    return request.items_title || '';
};

/* Строки одного события истории: что изменилось — коротко, словами. */
export const eventLines = (event) => {
    const payload = event?.payload || {};
    const lines = [];
    const where = (side) => [side?.on_stock && !namesStock(side?.location) ? 'склад' : '', side?.location].filter(Boolean).join(', ');
    if (event.kind === 'registered') {
        const to = payload.to || {};
        const line = [shortName(to.mol_name), where(to)].filter(Boolean).join(' · ');
        if (line) lines.push(line);
    } else if (event.kind === 'moved') {
        const from = payload.from || {};
        const to = payload.to || {};
        lines.push(`${shortName(from.mol_name) || '—'} → ${shortName(to.mol_name) || '—'}`);
        if (where(from) !== where(to) && where(to)) lines.push(`${where(from) || '—'} → ${where(to)}`);
    } else if (event.kind === 'submitted') {
        if (payload.to?.name) lines.push(`${shortName(payload.from?.name) || '—'} → ${shortName(payload.to.name)}${payload.to.on_stock ? ' (склад)' : ''}`);
        if (payload.need?.title) lines.push(`${payload.need.title}${payload.need.for_name ? ` — кому: ${shortName(payload.need.for_name)}` : ''}`);
    } else if (event.kind === 'corrected') {
        (payload.changes || []).forEach((change) => {
            lines.push(`${change.label}: ${change.from ?? '—'} → ${change.to ?? '—'}`);
        });
    } else if (event.kind === 'original_received') {
        lines.push(`Оригинал передал ${shortName(payload.handed_by_name) || '—'}, ${fmtDate(payload.handed_on)}`);
    } else if (event.kind === 'scan_attached' || event.kind === 'scan_replaced') {
        if (payload.signed_on) lines.push(`Акт подписан ${fmtDate(payload.signed_on)}`);
        if (payload.replaced?.name) lines.push(`Прежний файл: ${payload.replaced.name}`);
    } else if (event.kind === 'barcode_set') {
        if (payload.from && payload.to) lines.push(`${payload.from} → ${payload.to}`);
        else if (payload.to || payload.from) lines.push(payload.to || payload.from);
    } else if (event.kind === 'need_accepted') {
        lines.push(`${payload.name || 'ОС'} · ${payload.inventory_number || '—'}`);
    }
    const note = payload.reason || payload.comment;
    if (note) lines.push(note);
    return lines;
};

export const eventFiles = (event) => (event?.payload?.files || []).filter((file) => file?.name);

/* ── Формы ─────────────────────────────────────────────────────────────────── */

export const blankRequest = (kind, preset = {}) => ({
    kind,
    asset_ids: [],
    to_user_id: null,
    to_location: '',
    to_stock: false,
    comment: '',
    from_user_id: null,
    need_title: '',
    need_category_id: null,
    need_for_user_id: null,
    ...preset,
});

export const requestToForm = (card) => {
    const request = card?.request || {};
    return blankRequest(request.kind, {
        asset_ids: (card?.items || []).map((item) => item.asset_id),
        to_user_id: request.to_user_id || null,
        to_location: request.to_location || '',
        to_stock: Boolean(request.to_stock),
        comment: request.comment || '',
        from_user_id: request.from_user_id || null,
        need_title: request.need_title || '',
        need_category_id: request.need_category_id || null,
        need_for_user_id: request.need_for_user_id || null,
    });
};

export const hasAssets = (kind) => kind === 'transfer' || kind === 'writeoff' || kind === 'dismissal';
export const isMoving = (kind) => kind === 'transfer' || kind === 'dismissal';

/* Каких обязательных фото не хватает у ОС: есть ли снимок на сервере или выбран новый файл.
   photos — {assetId: {photo_whole: {…}, photo_number: {…}, extra: […]}}. */
export const photoGaps = (assets, photos) => (assets || [])
    .map((asset) => ({
        asset,
        missing: PHOTO_SLOTS.map((slot) => slot.kind).filter((kind) => !photos?.[asset.id]?.[kind]),
    }))
    .filter((gap) => gap.missing.length > 0);

/* Причина, по которой запрос не уходит, — словами (ТЗ: «система показывает причину»). */
export const photoGapText = (gaps) => {
    if (!gaps?.length) return '';
    const parts = gaps.slice(0, 3).map((gap) => `${gap.asset.name} — ${gap.missing.map((kind) => PHOTO_LABELS[kind]).join(' и ')}`);
    const more = gaps.length - parts.length;
    return `Не хватает фото: ${parts.join('; ')}${more > 0 ? ` и ещё у ${more} ОС` : ''}. К каждому ОС нужны два снимка: ОС целиком и инвентарный номер.`;
};

/* Чего не хватает форме запроса, чтобы его отправить: {поле: текст}. Двойник
   fixed_assets/rules.py: missing_for_submit — сервер проверяет то же самое ещё раз. */
export const requestIssues = (form, assets) => {
    const issues = {};
    if (hasAssets(form.kind) && !(assets || []).length) issues.asset_ids = 'Выберите хотя бы одно ОС';
    if (isMoving(form.kind) && !form.to_user_id) issues.to_user_id = 'Укажите, кому передаётся ОС';
    if (form.kind === 'dismissal' && !form.from_user_id) issues.from_user_id = 'Выберите сотрудника';
    if (form.kind === 'writeoff' && !String(form.comment || '').trim()) issues.comment = 'Напишите причину списания';
    if (form.kind === 'need') {
        if (!String(form.need_title || '').trim()) issues.need_title = 'Напишите, что нужно';
        if (!form.need_for_user_id) issues.need_for_user_id = 'Укажите, кому нужно ОС';
        if (!String(form.comment || '').trim()) issues.comment = 'Напишите, зачем нужно ОС';
    }
    return issues;
};

export const requestPayload = (form) => {
    const body = { kind: form.kind, comment: String(form.comment || '').trim() };
    if (hasAssets(form.kind)) body.asset_ids = form.asset_ids;
    if (isMoving(form.kind)) {
        body.to_user_id = form.to_user_id || null;
        body.to_location = String(form.to_location || '').trim();
        body.to_stock = Boolean(form.to_stock);
    }
    if (form.kind === 'dismissal') body.from_user_id = form.from_user_id;
    if (form.kind === 'need') {
        body.need_title = String(form.need_title || '').trim();
        body.need_category_id = form.need_category_id || null;
        body.need_for_user_id = form.need_for_user_id || null;
    }
    return body;
};

export const intakeIssues = (form, photos) => {
    const issues = {};
    if (!String(form.name || '').trim()) issues.name = 'Назовите ОС';
    if (!form.category_id) issues.category_id = 'Выберите категорию';
    if (!String(form.inventory_number || '').trim()) issues.inventory_number = 'Присвойте инвентарный номер';
    if (!form.to_user_id) issues.to_user_id = 'Укажите, за кем числится ОС';
    const missing = PHOTO_SLOTS.map((slot) => slot.kind).filter((kind) => !photos?.[kind]);
    if (missing.length) issues.photos = `Не хватает фото: ${missing.map((kind) => PHOTO_LABELS[kind]).join(' и ')}`;
    return issues;
};

/* Реквизиты для актов: пустая форма и то, что уходит на сервер. */
export const blankRequisites = (saved = {}) => ({
    org_name: saved.org_name || '',
    org_bin: saved.org_bin || '',
    head_name: saved.head_name || '',
    commission_order_number: saved.commission_order_number || '',
    commission_order_date: String(saved.commission_order_date || '').slice(0, 10),
    chairman_title: saved.chairman_title || '',
    chairman_name: saved.chairman_name || '',
    members: (saved.members || []).map((member) => ({ title: member.title || '', name: member.name || '' })),
    chief_accountant_name: saved.chief_accountant_name || '',
});

export const requisitesPayload = (form) => ({
    ...form,
    org_bin: String(form.org_bin || '').replace(/\s+/g, ''),
    commission_order_date: form.commission_order_date || null,
    members: (form.members || []).filter((member) => String(member.title || '').trim() || String(member.name || '').trim()),
});

/* ── Запросы к серверу ─────────────────────────────────────────────────────── */

export function errorText(error, fallback = 'Не получилось — попробуйте ещё раз') {
    const message = error?.response?.data?.error;
    if (message) return message;
    if (error?.message === 'Network Error') return 'Нет связи с сервером — проверьте интернет';
    return fallback;
}

export const errorFields = (error) => error?.response?.data?.fields || {};

export function createApi(apiBaseUrl, headersRef) {
    const url = (path) => `${apiBaseUrl}/api/fixed_assets${path}`;
    const config = (extra = {}) => {
        const { headers: own = {}, ...rest } = extra || {};
        return { headers: { ...(headersRef.current ? headersRef.current() : {}), ...own }, ...rest };
    };
    /* Шаг с файлами — multipart: поля формы едут JSON-строкой в `payload`. */
    const multipart = (body, files) => {
        const form = new FormData();
        form.append('payload', JSON.stringify(body || {}));
        (files || []).forEach((file) => form.append('files', file, file.name || 'file'));
        return form;
    };
    return {
        get: (path, params) => axios.get(url(path), config({ params })),
        post: (path, body, files) => axios.post(url(path), files?.length ? multipart(body, files) : (body ?? {}), config()),
        put: (path, body) => axios.put(url(path), body ?? {}, config()),
        patch: (path, body) => axios.patch(url(path), body ?? {}, config()),
        del: (path, params) => axios.delete(url(path), config({ params })),
        blob: (path, params) => axios.get(url(path), config({ params, responseType: 'blob' })),
    };
}

/* Сохранить файл из ответа сервера (акт Word, выгрузка Excel) — в этом же окне. */
export const saveBlob = (response, fallbackName) => {
    const header = response?.headers?.['content-disposition'] || '';
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
    const plain = /filename="?([^";]+)"?/i.exec(header);
    let name = fallbackName;
    try {
        if (encoded) name = decodeURIComponent(encoded[1]);
        else if (plain) name = plain[1];
    } catch {
        name = fallbackName;
    }
    const link = document.createElement('a');
    const href = URL.createObjectURL(response.data);
    link.href = href;
    link.download = name || 'file';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(href), 30000);
};

/* Открыть скан акта (PDF) в соседней вкладке: файл отдаётся с обычной авторизацией, поэтому
   сначала забираем его, потом показываем по временному адресу. */
export const openBlob = (response) => {
    const href = URL.createObjectURL(response.data);
    const opened = window.open(href, '_blank', 'noopener');
    if (!opened) {
        const link = document.createElement('a');
        link.href = href;
        link.target = '_blank';
        link.rel = 'noopener';
        document.body.appendChild(link);
        link.click();
        link.remove();
    }
    setTimeout(() => URL.revokeObjectURL(href), 120000);
};
