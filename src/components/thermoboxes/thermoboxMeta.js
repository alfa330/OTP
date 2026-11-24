/*
 * «Термокороба» (#363) — подписи, проверка чисел и отбор строк.
 *
 * Чистые функции без React: их гоняет node --test (tests/thermobox_meta.test.mjs),
 * а подписи сверяются с сервером буквально (tests/test_thermoboxes.py) —
 * экран, выгрузка и история обязаны называть одно и то же одинаково.
 */

export const COUNT_FIELDS = ['free_boxes', 'thermo_bags', 'used_boxes'];
export const CONDITION_FIELDS = ['tariff', 'min_orders', 'period_days', 'deposit_tenge', 'special_condition'];
export const EDITABLE_FIELDS = [...COUNT_FIELDS, ...CONDITION_FIELDS];
const NUMBER_FIELDS = ['free_boxes', 'thermo_bags', 'used_boxes', 'min_orders', 'period_days', 'deposit_tenge'];

export const FIELD_LABELS = {
    free_boxes: 'Бесплатные термокороба',
    thermo_bags: 'Термопакеты',
    used_boxes: 'Б/У короба',
    tariff: 'Тариф',
    min_orders: 'Заказы',
    period_days: 'Срок',
    deposit_tenge: 'Депозит',
    special_condition: 'Отдельное условие',
};

export const TARIFF_LABELS = {
    auto_couriers: 'Выдаются авто-курьерам',
    all: 'Все тарифы',
};

/* В узкой колонке таблицы — коротко; полная подпись в карточке и в файле. */
export const TARIFF_SHORT = {
    auto_couriers: 'Авто-курьеры',
    all: 'Все тарифы',
};

export const MEMO_OWNER_LABELS = {
    kc: 'КЦ',
    regions: 'Регионы',
};

// Пределы — те же, что у сервера (thermoboxes/rules.py: _LIMITS).
const LIMITS = {
    free_boxes: [0, 100000],
    thermo_bags: [0, 100000],
    used_boxes: [0, 100000],
    min_orders: [0, 10000],
    period_days: [1, 365],
    deposit_tenge: [0, 10000000],
};

export const SPECIAL_CONDITION_MAX = 200;
export const MEMO_TEXT_MAX = 500;
export const MEMO_ITEMS_MAX = 20;

export const plural = (count, one, few, many) => {
    const value = Math.abs(Math.trunc(Number(count) || 0)) % 100;
    if (value >= 11 && value <= 14) return many;
    const last = value % 10;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

const thousands = (value) => String(Math.trunc(Number(value) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');

export const tariffLabel = (code) => TARIFF_LABELS[code] || code || '—';
export const tariffShort = (code) => TARIFF_SHORT[code] || code || '—';

export const ordersLabel = (value) => (value ? `${value}+ заказов` : 'Без нормы');

export const periodLabel = (value) => (Number(value) === 7
    ? 'Неделя (7 дней)'
    : `${value} ${plural(value, 'день', 'дня', 'дней')}`);

/* «за неделю» / «за 14 дней» — вторая строка ячейки «Заказы» в таблице. */
export const periodShort = (value) => (Number(value) === 7
    ? 'за неделю'
    : `за ${value} ${plural(value, 'день', 'дня', 'дней')}`);

export const depositLabel = (value) => (value ? `${thousands(value)} тенге` : 'Нет депозита');
// Неразрывный пробел перед «₸»: знак валюты не должен уезжать на новую строку.
export const depositShort = (value) => (value ? `${thousands(value)}\u00a0₸` : 'Нет');

/* Значение поля так, как его пишут в листе: история строки. Пара —
   display_value в thermoboxes/rules.py. */
export const displayValue = (field, value) => {
    if (field === 'tariff') return tariffLabel(value);
    if (field === 'min_orders') return ordersLabel(value || 0);
    if (field === 'period_days') return value ? periodLabel(value) : '—';
    if (field === 'deposit_tenge') return depositLabel(value || 0);
    if (field === 'special_condition') return value || '—';
    return value === null || value === undefined ? '—' : String(value);
};

/* ── Числа ───────────────────────────────────────────────────────────── */

/* Текст поля → число. null — поле пустое, NaN — не целое неотрицательное.
   «Только целые числа, без отрицательных значений» — требование постановки:
   «-1», «1.5», «1,5» и «пять» не проходят, а не округляются молча. */
export const parseWhole = (text) => {
    const value = String(text ?? '').replace(/\s/g, '');
    if (!value) return null;
    if (!/^\d{1,8}$/.test(value)) return NaN;
    return Number(value);
};

/* Ошибка поля для человека или '' — та же граница, что у сервера. */
export const fieldError = (field, text) => {
    if (field === 'tariff') return TARIFF_LABELS[text] ? '' : 'Выберите тариф';
    if (field === 'special_condition') {
        return String(text || '').trim().length > SPECIAL_CONDITION_MAX
            ? `Не длиннее ${SPECIAL_CONDITION_MAX} знаков` : '';
    }
    const value = parseWhole(text);
    if (value === null) return 'Укажите число';
    if (Number.isNaN(value)) {
        return /^\s*-/.test(String(text)) ? 'Не может быть меньше нуля' : 'Только целое число';
    }
    const [low, high] = LIMITS[field];
    if (value < low) return `Не меньше ${low}`;
    if (value > high) return 'Слишком большое число';
    return '';
};

/* Черновик строки — то, что лежит в полях формы: числа строками. */
export const draftOf = (row) => Object.fromEntries(EDITABLE_FIELDS.map((field) => {
    const value = row?.[field];
    if (NUMBER_FIELDS.includes(field)) return [field, value === null || value === undefined ? '' : String(value)];
    return [field, value ?? ''];
}));

/* Значение черновика в том виде, в каком его ждёт сервер. */
export const valueOf = (field, text) => {
    if (NUMBER_FIELDS.includes(field)) return parseWhole(text);
    if (field === 'special_condition') {
        const value = String(text || '').split(/\s+/).filter(Boolean).join(' ');
        return value || null;
    }
    return text;
};

/* Поля, которые в черновике отличаются от строки, — {поле: значение}.
   Пустая правка (те же числа) изменением не считается, как и на сервере. */
export const changedFields = (row, draft, fields = EDITABLE_FIELDS) => {
    const result = {};
    fields.forEach((field) => {
        if (!draft || !(field in draft)) return;
        const value = valueOf(field, draft[field]);
        const current = row?.[field] ?? null;
        if (value !== current) result[field] = value;
    });
    return result;
};

export const draftErrors = (draft, fields = EDITABLE_FIELDS) => {
    const result = {};
    fields.forEach((field) => {
        if (!draft || !(field in draft)) return;
        const message = fieldError(field, draft[field]);
        if (message) result[field] = message;
    });
    return result;
};

/* ── Отбор ───────────────────────────────────────────────────────────── */

const fold = (text) => String(text ?? '').toLowerCase().replace(/ё/g, 'е').split(/\s+/).filter(Boolean).join(' ');

/* Пара — matches в thermoboxes/rules.py: что на экране, то и в выгрузке. */
export const matchesRow = (row, city = '', query = '') => {
    if (fold(city) && fold(row?.city) !== fold(city)) return false;
    const words = fold(query).split(' ').filter(Boolean);
    if (!words.length) return true;
    const haystack = fold(['city', 'name', 'address', 'special_condition'].map((key) => row?.[key] || '').join(' '));
    return words.every((word) => haystack.includes(word));
};

export const citiesOf = (rows) => [...new Set((rows || []).map((row) => row.city).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, 'ru'));

/* «Обновлено: дата, кто» — самая свежая правка среди строк. */
export const latestUpdate = (rows) => (rows || []).reduce((latest, row) => {
    if (!row?.updated_at) return latest;
    if (!latest || String(row.updated_at) > String(latest.at)) {
        return { at: row.updated_at, by: row.updated_by_name || '' };
    }
    return latest;
}, null);

/* ── Даты ────────────────────────────────────────────────────────────── */

const MONTHS = ['янв.', 'февр.', 'марта', 'апр.', 'мая', 'июня', 'июля', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'];

/* Сервер отдаёт часы Алматы без зоны («2026-10-01T14:20:05»). Читаем цифры как
   есть: new Date() сдвинул бы их на пояс браузера. */
const partsOf = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(iso || ''));
    if (!match) return null;
    const [, year, month, day, hour, minute] = match;
    return { year: Number(year), month: Number(month), day: Number(day), time: `${hour}:${minute}` };
};

export const almatyToday = (now = new Date()) => {
    const text = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Almaty', year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(now);
    const [year, month, day] = text.split('-').map(Number);
    return { year, month, day };
};

const dayNumber = ({ year, month, day }) => Math.round(Date.UTC(year, month - 1, day) / 86400000);

/* «сегодня в 14:20», «вчера в 09:05», «28 сент. в 18:40», «3 дек. 2025 в 10:00». */
export const fmtStamp = (iso, now = new Date()) => {
    const parts = partsOf(iso);
    if (!parts) return '';
    const today = almatyToday(now);
    const diff = dayNumber(today) - dayNumber(parts);
    if (diff === 0) return `сегодня в ${parts.time}`;
    if (diff === 1) return `вчера в ${parts.time}`;
    const year = parts.year === today.year ? '' : ` ${parts.year}`;
    return `${parts.day} ${MONTHS[parts.month - 1]}${year} в ${parts.time}`;
};

/* Откуда цифры (с 24.11.2026 раздел — зеркало Google-таблицы, thermoboxes/sync.py).

   Метка в шапке — когда лист последний раз прочитали, а не когда цифры
   менялись: лист, в котором неделю ничего не правили, свеж, если его сверили
   пять минут назад. Когда цифры менялись — в подсказке. Лист не прочитался —
   предупреждение: оператор КЦ должен знать, на какой момент цифры. */
export const sourceState = (source, now = new Date()) => {
    if (!source) return null;
    const readAt = fmtStamp(source.read_at, now);
    const changedAt = fmtStamp(source.changed_at, now);
    const state = {
        label: readAt ? `Сверено с Google-таблицей ${readAt}` : 'Google-таблица ещё не прочитана',
        hint: changedAt ? `Цифры последний раз менялись ${changedAt}` : '',
        warning: '',
    };
    // Время последней сверки уже в метке рядом — плашка его не повторяет.
    if (source.status === 'error') {
        state.warning = readAt
            ? 'Google-таблица сейчас не читается — показаны цифры последней сверки'
            : 'Google-таблица пока не читается — цифры могут быть устаревшими';
    }
    return state;
};

/* Правка в Google-таблице — на открытом экране (с 24.11.2026).

   Сервер читает лист каждую минуту (thermoboxes/sync.py) и после каждого чтения
   будит открытый экран по живому каналу (thermoboxes/live.py, sheetPulse.js);
   нет канала — экран сам раз в SOURCE_POLL_MS спрашивает /api/thermoboxes/source.
   Строки перечитываются, только когда сдвинулся changed_at. Пришли новые цифры — изменённые ячейки
   подсвечиваются на FRESH_MS («было → стало» в подсказке), а тост говорит,
   какие офисы обновились: цифры не должны молча меняться под глазами. */
export const SOURCE_POLL_MS = 30000;
export const FRESH_MS = 120000;
// Сверка — фон: зависший запрос не должен держать её — тычки копились бы за ним.
export const CHECK_TIMEOUT_MS = 15000;

/* Ответ сервера не старше того, что уже на экране: сначала по changed_at
   (цифры), потом по checked_at (метка «Сверено …»). Ответы сверки и кнопки
   «Обновить из таблицы» не упорядочены — опоздавший старый не должен лечь
   поверх нового. Время — строки одного вида «2026-11-24T17:51:00», их порядок
   — порядок строк; нет источника (раздел не зеркало) — не с чем сравнивать. */
export const sourceNotOlder = (current, next) => {
    if (!current || !next) return true;
    const changedNow = current.changed_at || '';
    const changedNext = next.changed_at || '';
    if (changedNext !== changedNow) return changedNext > changedNow;
    return (next.checked_at || '') >= (current.checked_at || '');
};
export const TRACKED_FIELDS = [...COUNT_FIELDS, ...CONDITION_FIELDS];

/* Что поменялось между двумя ответами экрана: {changed: {id: {поле: прежнее}},
   added: [id], removed: [id]}. Считаются только строки на экране (is_active). */
export const rowChanges = (before, after, fields = TRACKED_FIELDS) => {
    const shown = (rows) => (rows || []).filter((row) => row.is_active !== false);
    const previous = new Map(shown(before).map((row) => [row.id, row]));
    const changed = {};
    const added = [];
    for (const row of shown(after)) {
        const old = previous.get(row.id);
        if (!old) {
            added.push(row.id);
            continue;
        }
        const fieldsChanged = {};
        for (const field of fields) {
            if ((old[field] ?? null) !== (row[field] ?? null)) fieldsChanged[field] = old[field] ?? null;
        }
        if (Object.keys(fieldsChanged).length) changed[row.id] = fieldsChanged;
    }
    const now = new Set(shown(after).map((row) => row.id));
    const removed = [...previous.keys()].filter((id) => !now.has(id));
    return { changed, added, removed };
};

const memoKey = (memo) => JSON.stringify([memo?.title || null,
    (memo?.items || []).map((item) => [item.text, item.owner || null, item.kind || null])]);

export const memoChanged = (before, after) => memoKey(before) !== memoKey(after);

/* Тост о правке из таблицы: какие офисы обновились; пусто — ничего видимого. */
export const changesToast = (diff, rows, memoUpdated = false) => {
    const byId = new Map((rows || []).map((row) => [row.id, row]));
    const ids = [...Object.keys(diff.changed).map(Number), ...diff.added];
    const cities = [...new Set(ids.map((id) => byId.get(id)?.city).filter(Boolean))];
    const parts = [];
    if (cities.length) {
        parts.push(cities.length > 3 ? `${cities.slice(0, 3).join(', ')} и ещё ${cities.length - 3}` : cities.join(', '));
    }
    if (diff.removed.length) {
        parts.push(`${diff.removed.length} ${plural(diff.removed.length, 'офис убран', 'офиса убраны', 'офисов убраны')}`);
    }
    if (memoUpdated) parts.push('памятка');
    return parts.length ? `Обновлено из Google-таблицы: ${parts.join('; ')}` : '';
};

/* Подсказка у подсвеченной ячейки: «Было 28 → стало 18». */
export const freshHint = (field, was, now) => {
    const show = (value) => {
        if (COUNT_FIELDS.includes(field)) return String(value || 0);
        if (field === 'tariff') return tariffShort(value);
        if (field === 'min_orders') return ordersLabel(value);
        if (field === 'period_days') return periodShort(value);
        if (field === 'deposit_tenge') return value ? depositShort(value) : 'без депозита';
        return value || '—';
    };
    return `Было ${show(was)} → стало ${show(now)} — правка в Google-таблице`;
};

/* «Термокороба 01.10.2026.xlsx». Пара — file_name в thermoboxes/report.py. */
export const exportFileName = (now = new Date()) => {
    const { year, month, day } = almatyToday(now);
    const pad = (value) => String(value).padStart(2, '0');
    return `Термокороба ${pad(day)}.${pad(month)}.${year}.xlsx`;
};

/* ── История ─────────────────────────────────────────────────────────── */

export const EVENT_LABELS = {
    created: 'Офис добавлен в таблицу',
    hidden: 'Офис убран из таблицы',
    shown: 'Офис возвращён в таблицу',
    edited: 'Изменение',
};

export const eventLabel = (kind) => EVENT_LABELS[kind] || kind || '';

/* ── Наличие ─────────────────────────────────────────────────────────── */

/* Три состояния — из самих данных, без придуманных порогов: есть бесплатные
   (можно отправлять курьера), бесплатных нет, но есть Б/У или пакеты, пусто. */
export const AVAILABILITY_LEGEND = [
    { key: 'free', tone: 'green', label: 'Есть бесплатные' },
    { key: 'partial', tone: 'amber', label: 'Только Б/У или пакеты' },
    { key: 'none', tone: 'red', label: 'Нет в наличии' },
];

export const availabilityOf = (row) => {
    const free = Number(row?.free_boxes) || 0;
    const bags = Number(row?.thermo_bags) || 0;
    const used = Number(row?.used_boxes) || 0;
    if (free > 0) {
        return { key: 'free', tone: 'green', label: 'Есть коробы', counted: `${free} ${plural(free, 'короб', 'короба', 'коробов')}` };
    }
    if (bags > 0 || used > 0) {
        return { key: 'partial', tone: 'amber', label: 'Нет бесплатных', counted: 'Нет бесплатных' };
    }
    return { key: 'none', tone: 'red', label: 'Нет в наличии', counted: 'Нет в наличии' };
};

/* ── Памятка: значок правила ─────────────────────────────────────────── */

/* Значок у правила — как в листе (⛔ 🚫 💰 📊 ⚠️ ✅ ❓). Хранится в пункте
   памятки (`kind`), выбирает его супервайзер в редакторе. Пара —
   MEMO_KIND_LABELS в thermoboxes/rules.py. */
export const MEMO_KIND_LABELS = {
    forbidden: 'Запрет',
    money: 'Деньги',
    data: 'Данные',
    warning: 'Важно',
    check: 'Проверка',
    question: 'Вопрос',
};

export const MEMO_KINDS = Object.keys(MEMO_KIND_LABELS);

/* Пункт без значка (сохранён до того, как значок появился) — угадываем по
   тексту, чтобы памятка не осталась без значков до первой правки. */
export const guessMemoKind = (text) => {
    const value = String(text || '').toLowerCase();
    if (/не обслуживается|нельзя|не отправлять/.test(value)) return 'forbidden';
    if (/депозит|баланс/.test(value)) return 'money';
    if (/критическ/.test(value)) return 'warning';
    if (/проверьте|согласовани/.test(value)) return 'check';
    if (/не гадайте|не поняли|обратитесь/.test(value)) return 'question';
    return 'data';
};

export const memoKindOf = (item) => (MEMO_KIND_LABELS[item?.kind] ? item.kind : guessMemoKind(item?.text));

/* Первая строка пункта — суть, остальное — пояснение. */
export const splitMemo = (text) => {
    const lines = String(text || '').split('\n').map((line) => line.trim()).filter(Boolean);
    return { head: lines[0] || '', rest: lines.slice(1).join(' ') };
};
