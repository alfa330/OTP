/*
 * Правила раздела «Посылки», вынесенные из JSX.
 *
 * Здесь всё, что можно проверить без браузера: подписи статусов, разбор ссылки
 * на аккаунт водителя, правило «спрашивать офис или подставить сам», сборка
 * строки истории. Тесты — tests/parcel_meta.test.mjs.
 *
 * Про цвет. Красим ТОЛЬКО то, что требует действия. «В офисе» — рабочее
 * состояние, оно нейтральное; «Передали» — закрытое, оно приглушённое. Цвет
 * появляется в одном месте: посылка лежит слишком долго, а раздел ровно про
 * это — «невостребованные».
 */

export const PARCEL_STATUSES = ['in_office', 'given_to_recipient', 'given_to_sender'];

export const STATUS_META = {
    in_office: { label: 'В офисе', short: 'В офисе', tone: null },
    given_to_recipient: { label: 'Передали получателю', short: 'Получателю', tone: 'muted' },
    given_to_sender: { label: 'Передали отправителю', short: 'Отправителю', tone: 'muted' },
};

export const PARCEL_KINDS = ['parcel', 'document', 'other'];

export const KIND_META = {
    parcel: { label: 'Посылка' },
    document: { label: 'Документ' },
    other: { label: 'Другое' },
};

// Сегменты фильтра — «Все/В офисе/Передали получателю/Передали отправителю»
// дословно из ТЗ, в том же порядке.
export const STATE_FILTERS = [
    { key: 'all', label: 'Все', status: '' },
    { key: 'in_office', label: 'В офисе', status: 'in_office' },
    { key: 'given_to_recipient', label: 'Передали получателю', status: 'given_to_recipient' },
    { key: 'given_to_sender', label: 'Передали отправителю', status: 'given_to_sender' },
];

// Сколько дней посылка считается «просто лежит», а не «залежалась». Месяц —
// срок, после которого водитель почти наверняка уже не придёт сам, и офису
// пора звонить. Значение одно на весь раздел: подсветка строки и подпись «лежит
// N дней» обязаны срабатывать одновременно.
export const STALE_AFTER_DAYS = 30;

export const statusMeta = (code) => STATUS_META[code] || { label: code || '—', short: code || '—', tone: null };
export const kindMeta = (code) => KIND_META[code] || { label: code || '—' };

export const isClosed = (status) => status === 'given_to_recipient' || status === 'given_to_sender';

/* ── Ссылка на аккаунт водителя ───────────────────────────────────────────────
 *
 * Близнец серверного `parcels/drivers.py::extract_account_id` — набор случаев у
 * обоих одинаковый. Здесь он нужен, чтобы форма гасила кнопку «Найти» до того,
 * как человек отправит заведомо непонятную строку.
 *
 * Два живых вида ссылки:
 *   https://fleet.yandex.kz/contractors?park_id=<парк>&contractor_id=<водитель>&candidate_id=<…>
 *   https://fleet.yandex.kz/contractors/<водитель>/details?park_id=<парк>
 *
 * «Первое 32-значное значение в строке» брать НЕЛЬЗЯ: в первой ссылке таких
 * значений два, и первым идёт ПАРК. Поэтому только именованный параметр или
 * сегмент пути после известного слова.
 */
const ACCOUNT_ID_RE = /^[0-9a-f]{32}$/i;

const DRIVER_QUERY_KEYS = [
    'contractor_id', 'driver_id', 'driver_profile_id', 'account_id',
    'courier_id', 'profile_id',
];

const DRIVER_PATH_KEYS = [
    'contractors', 'contractor', 'drivers', 'driver',
    'driver-accounts', 'couriers', 'courier',
];

export const extractAccountId = (value) => {
    const raw = String(value ?? '').trim();
    if (!raw) return null;
    if (ACCOUNT_ID_RE.test(raw)) return raw.toLowerCase();

    let parsed;
    try {
        parsed = new URL(raw.includes('://') ? raw : `https://${raw.replace(/^\/+/, '')}`);
    } catch {
        return null;
    }

    for (const key of DRIVER_QUERY_KEYS) {
        const found = parsed.searchParams.get(key);
        if (found && ACCOUNT_ID_RE.test(found.trim())) return found.trim().toLowerCase();
    }

    const segments = parsed.pathname.split('/').filter(Boolean);
    for (let index = 0; index < segments.length - 1; index += 1) {
        if (!DRIVER_PATH_KEYS.includes(segments[index].toLowerCase())) continue;
        const token = segments[index + 1].trim();
        if (ACCOUNT_ID_RE.test(token)) return token.toLowerCase();
    }
    return null;
};

/* ── Город → офис ────────────────────────────────────────────────────────────
 *
 * «Офис выбирается, только если в городе несколько офисов, в ином случае
 * заполнено авто» — просьба владельца. Правило считается по справочнику, а не
 * по списку городов из ТЗ: справочник живой, и завтра второй офис появится там,
 * где сегодня один.
 */
export const officesOfCity = (offices, city) => {
    const needle = String(city || '').trim().toLowerCase();
    if (!needle) return [];
    return (offices || []).filter((office) => String(office.city || '').trim().toLowerCase() === needle);
};

export const officeChoiceFor = (offices, city) => {
    const list = officesOfCity(offices, city);
    return {
        options: list,
        // Спрашиваем офис только когда есть из чего выбирать.
        asks: list.length > 1,
        autoOfficeId: list.length === 1 ? list[0].id : null,
    };
};

/* ── Даты ────────────────────────────────────────────────────────────────── */

// Сегодня по Алматы. У сотрудника в браузере может стоять любая зона, а дата
// приёма — рабочий день офиса в Казахстане; без приведения человек в другом
// поясе получал бы «дата в будущем» на сегодняшнем дне.
export const todayISO = () => {
    const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Almaty', year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(new Date());
    const get = (type) => parts.find((part) => part.type === type)?.value || '';
    return `${get('year')}-${get('month')}-${get('day')}`;
};

// Разбор ISO-даты с проверкой, а не только по маске. `Date.UTC(2026, 12, 40)`
// молча превращается в февраль следующего года — и «13-й месяц» дал бы не
// прочерк, а какое-то число дней. Сверяем компоненты с тем, что получилось.
const dateParts = (iso) => {
    const text = String(iso || '').slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
    const year = Number(text.slice(0, 4));
    const month = Number(text.slice(5, 7));
    const day = Number(text.slice(8, 10));
    const stamp = Date.UTC(year, month - 1, day);
    const back = new Date(stamp);
    if (back.getUTCFullYear() !== year || back.getUTCMonth() !== month - 1
        || back.getUTCDate() !== day) return null;
    return { year, month, day, stamp };
};

const dayNumber = (iso) => dateParts(iso)?.stamp ?? null;

export const daysInOffice = (parcel, today = todayISO()) => {
    if (!parcel || isClosed(parcel.status)) return null;
    const from = dayNumber(parcel.received_on);
    const to = dayNumber(today);
    if (from === null || to === null) return null;
    return Math.max(0, Math.round((to - from) / 86400000));
};

export const isStale = (parcel, today = todayISO()) => {
    const days = daysInOffice(parcel, today);
    return days !== null && days >= STALE_AFTER_DAYS;
};

const RU_MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн',
    'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

export const fmtDate = (iso) => {
    const parts = dateParts(iso);
    if (!parts) return '—';
    return `${parts.day} ${RU_MONTHS_SHORT[parts.month - 1]} ${parts.year}`;
};

export const fmtDateTime = (iso) => {
    if (!iso) return '—';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '—';
    return parsed.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
};

// Дни склоняются: «1 день», «2 дня», «5 дней». Готовой функции в проекте нет,
// а «5 дня» в реестре читается как опечатка.
export const pluralDays = (count) => {
    const value = Math.abs(Number(count) || 0);
    const tail = value % 100;
    if (tail >= 11 && tail <= 14) return `${value} дней`;
    const last = value % 10;
    if (last === 1) return `${value} день`;
    if (last >= 2 && last <= 4) return `${value} дня`;
    return `${value} дней`;
};

/* ── Телефон ─────────────────────────────────────────────────────────────── */

// CRM отдаёт «+77719736925». Показываем группами, чтобы номер можно было
// прочитать вслух водителю, не сбиваясь.
export const fmtPhone = (value) => {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length !== 11) return String(value || '').trim() || null;
    return `+${digits[0]} ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7, 9)} ${digits.slice(9)}`;
};

/* ── История ─────────────────────────────────────────────────────────────── */

export const describeEvent = (event) => {
    const payload = event?.payload || {};
    if (event?.kind === 'created') return 'Посылка добавлена в реестр';
    if (event?.kind === 'comment') return 'Добавлен комментарий';
    if (event?.kind === 'status') {
        // Одинаковые «было» и «стало» сервер больше не пишет, но такие строки
        // могли остаться в истории — печатать «В офисе → В офисе» нельзя.
        if (payload.from === payload.to) return 'Статус подтверждён';
        return `Статус изменён: ${statusMeta(payload.from).label} → ${statusMeta(payload.to).label}`;
    }
    if (event?.kind === 'edited') {
        const changes = Array.isArray(payload.changes) ? payload.changes : [];
        if (!changes.length) return 'Карточка изменена';
        return `Изменено: ${changes.map((change) => change.label).join(', ')}`;
    }
    if (event?.kind === 'driver_synced') return 'Данные водителя обновлены из CRM';
    return 'Изменение карточки';
};
