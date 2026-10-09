/*
 * «Реестр тестовых номеров» — подписи и подсчёты экрана. Чистые функции: без
 * React и без сети, чтобы их можно было проверить node-тестом.
 *
 * Сервер отдаёт тесты периода списком (test_numbers/activity.py): одна строка —
 * один звонок или одна переписка за сутки. Отбор по номеру делается здесь, на
 * уже загруженном списке: за месяц тестов десятки, второй запрос ради фильтра
 * был бы дороже (звонки СЗоВ читаются живым запросом к Oktell).
 */

// Тот же потолок, что у сервера (activity.MAX_DAYS).
export const MAX_PERIOD_DAYS = 31;

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const WEEKDAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

export const plural = (n, one, few, many) => {
    const abs = Math.abs(Number(n) || 0);
    const mod10 = abs % 10;
    const mod100 = abs % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
};

export const testsLabel = (n) => `${n} ${plural(n, 'тест', 'теста', 'тестов')}`;

/* Из чего день состоит: «3 звонка · 2 чата». Пустая часть не печатается. */
export const splitLabel = ({ calls = 0, chats = 0 } = {}) => [
    calls ? `${calls} ${plural(calls, 'звонок', 'звонка', 'звонков')}` : '',
    chats ? `${chats} ${plural(chats, 'чат', 'чата', 'чатов')}` : '',
].filter(Boolean).join(' · ');

const parseIsoDay = (iso) => {
    const [y, m, d] = String(iso || '').split('-').map(Number);
    return new Date(y, (m || 1) - 1, d || 1);
};

export const isoDay = (date) => {
    const p = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}`;
};

export const shiftDay = (iso, days) => {
    const date = parseIsoDay(iso);
    date.setDate(date.getDate() + days);
    return isoDay(date);
};

export const daySpan = (from, to) => Math.round((parseIsoDay(to) - parseIsoDay(from)) / 86400000) + 1;

export const dayLabel = (iso, todayIso) => {
    if (iso === todayIso) return 'Сегодня';
    if (iso === shiftDay(todayIso, -1)) return 'Вчера';
    const date = parseIsoDay(iso);
    const sameYear = iso.slice(0, 4) === String(todayIso).slice(0, 4);
    return `${WEEKDAYS[date.getDay()]}, ${date.getDate()} ${MONTHS_SHORT[date.getMonth()]}${sameYear ? '' : ` ${date.getFullYear()}`}`;
};

export const timeLabel = (atIso) => String(atIso || '').slice(11, 16);

export const durationLabel = (seconds) => {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
};

export const directionLabel = (item) => {
    if (item?.kind !== 'call') return '';
    if (item.direction === 'out') return 'исходящий';
    if (item.direction === 'in') return 'входящий';
    return '';
};

/* Что было в тесте — одной строкой: у звонка итог и разговор, у чата число
   сообщений. Пустые части не печатаются: «— · 0:00» был бы шумом. */
export const itemSummary = (item) => {
    const parts = [];
    if (item?.kind === 'call') {
        const direction = directionLabel(item);
        if (direction) parts.push(direction);
        if (item.result) parts.push(item.result);
        if (item.duration_seconds) parts.push(durationLabel(item.duration_seconds));
    } else if (item?.messages) {
        parts.push(`${item.messages} ${plural(item.messages, 'сообщение', 'сообщения', 'сообщений')}`);
    }
    return parts.join(' · ');
};

/* На какой НАШ номер и в какой таксопарк был тест: «на +7 747 577 77 78 · Jana Taxi».
   Исходящий — «с номера»; номер неизвестен (исходящий Oktell, справочник молчит) —
   только парк; нет ни того, ни другого — пусто. */
export const lineCaption = (item) => {
    const line = String(item?.line || '').trim();
    const park = String(item?.park || '').trim();
    if (!line) return park;
    const lead = item?.direction === 'out' ? 'с' : 'на';
    return park ? `${lead} ${line} · ${park}` : `${lead} ${line}`;
};

export const filterItems = (items, phoneKey) => (
    phoneKey ? (items || []).filter((item) => item.phone_key === phoneKey) : (items || [])
);

/* Тесты по дням — то же правило, что у сервера (activity.per_day), но по уже
   отобранному списку: при выборе номера сервер не спрашиваем заново. Дни без
   тестов не показываются — четырнадцать строк нулей были бы шумом. */
export const daysOf = (items) => {
    const byDay = new Map();
    for (const item of items || []) {
        if (!item?.day) continue;
        const bucket = byDay.get(item.day) || { day: item.day, calls: 0, chats: 0, items: [] };
        if (item.kind === 'call') bucket.calls += 1; else bucket.chats += 1;
        bucket.items.push(item);
        byDay.set(item.day, bucket);
    }
    return [...byDay.values()]
        .map((bucket) => ({ ...bucket, total: bucket.calls + bucket.chats }))
        .sort((a, b) => (a.day < b.day ? 1 : -1));
};

export const countsByNumber = (items) => {
    const counts = {};
    for (const item of items || []) {
        if (!item?.phone_key) continue;
        counts[item.phone_key] = (counts[item.phone_key] || 0) + 1;
    }
    return counts;
};

export const periodPresets = (todayIso) => [
    { label: '7 дней', range: () => ({ from: shiftDay(todayIso, -6), to: todayIso }) },
    { label: '14 дней', range: () => ({ from: shiftDay(todayIso, -13), to: todayIso }) },
    { label: '30 дней', range: () => ({ from: shiftDay(todayIso, -29), to: todayIso }) },
];

export const periodError = (from, to) => {
    if (!from || !to) return 'Выберите начало и конец периода';
    if (daySpan(from, to) > MAX_PERIOD_DAYS) return `Период — не больше ${MAX_PERIOD_DAYS} дней`;
    return '';
};

/* Номер в списке выбора и в строке теста: «+7 700 000 01 01 · Иванова Айгерим». */
export const numberCaption = (number) => {
    if (!number) return '';
    const owner = number.owner?.name;
    return owner ? `${number.phone_display} · ${owner}` : number.phone_display;
};

export const SOURCE_HINT = [
    'Звонки ОП — FreePBX, СЗоВ — Oktell, Тез КЦ — Binotel.',
    'Чаты: WhatsApp ОП (Wazzup), СЗоВ (Chat2Desk), Тез КЦ (ChatApp) — за последние 45 дней.',
    'Один тест — один звонок или одна переписка за день.',
    'У каждого теста — наш номер, на который позвонили или написали, и таксопарк этой линии.',
].join(' ');

export const REGISTRY_HINT = [
    'Номера, с которых сотрудники проверяют линии и чаты.',
    'Их звонки и чаты не входят в табло, отбивки, отчёты, ИИ-оценку, планы, успешки и расчёт ресурсов.',
].join(' ');
