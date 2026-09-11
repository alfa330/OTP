/* Подписи, форматы и пороги раздела «Воронка ОП».
 *
 * Отдельный модуль без React и axios — на него написан node-тест
 * (tests/op_funnel_format.test.mjs). Правила форматирования здесь потому, что
 * их применяют семь разных экранов раздела, и разойтись им нельзя: одно и то же
 * число в плитке и в таблице обязано выглядеть одинаково, иначе руководитель
 * решает, что перед ним разные цифры (так уже было с «Касаниями»).
 *
 * Два соглашения API, от которых пляшет весь файл (op_funnel/routes.py):
 *   • проценты приходят ДОЛЯМИ (0.508), а не числами 50,8 — умножает фронт;
 *   • `null` означает «данных нет», а не ноль. Ноль и прочерк на экране разные:
 *     «0 %» — это «звонили и не дозвонились», «—» — «ещё не считали».
 */

/* Прочерк — единственный вид «нет данных» на экране. Отдельная константа, чтобы
   нигде не проскочил обычный дефис: он у́же и в колонке чисел заметно дрожит. */
export const DASH = '—';

/* Неразрывный пробел в разрядах и перед «%»: иначе «5 787» переносится по
   середине числа, а «50,8» отрывается от знака процента в узкой колонке. */
const NBSP = ' ';

/* Настоящий минус (U+2212), а не дефис: в колонке с tabular-nums дефис короче
   плюса, и столбец дельт выглядит рваным. */
const MINUS = '−';

// ── Направления ──────────────────────────────────────────────────────────────

/* Коды совпадают с `calculation_model_code` групп и с `DIRECTION_TITLES` в
   op_funnel/routes.py. Список локальный, но он НЕ заменяет `/meta`: показывать
   разрешено только пришедшие оттуда направления (супервайзер видит свои), а
   отсюда берутся короткая подпись и пояснение источника. */
export const DIRECTIONS = [
    {
        code: 'op_osnova',
        title: 'Основа',
        short: 'Основа',
        kind: 'funnel',
        hint: 'amoCRM, воронка «Отдел продаж». Группа 36.',
    },
    {
        code: 'op_potok',
        title: 'Поток',
        short: 'Поток',
        kind: 'funnel',
        hint: 'Партнёрская выгрузка СРМ, потоки 1 и 2. Группы 14 и 38.',
    },
    {
        code: 'op_yandex_reg',
        title: 'Яндекс Регистрация',
        short: 'Яндекс',
        kind: 'funnel',
        hint: 'Платный найм СРМ, период по дате регистрации водителя. Группа 15.',
    },
    {
        code: 'op_verificator',
        title: 'Верификатор',
        short: 'Верификатор',
        /* Не воронка обзвона: у верификаторов нагрузка и качество, дозвонов нет.
           Раздел по этому признаку меняет вкладку «Причины» на «Нагрузку». */
        kind: 'load',
        hint: 'Чаты Wazzup и ручная выгрузка обращений СРМ. Группа 13.',
    },
];

const DIRECTION_BY_CODE = DIRECTIONS.reduce((acc, item) => {
    acc[item.code] = item;
    return acc;
}, {});

/** Направление по коду; для незнакомого кода — заглушка с самим кодом, чтобы
 *  экран не падал и было видно, что пришло с сервера. */
export function directionByCode(code) {
    return DIRECTION_BY_CODE[code] || {
        code: String(code || ''),
        title: String(code || ''),
        short: String(code || ''),
        kind: 'funnel',
        hint: '',
    };
}

/** Направление без обзвона (Верификатор): воронки переходов у него нет. */
export function isLoadDirection(code) {
    return directionByCode(code).kind === 'load';
}

// ── Числа ────────────────────────────────────────────────────────────────────

const isBlank = (value) => value === null || value === undefined || value === '';

/** 5787 → «5 787», 12.5 с digits=1 → «12,5». `null` → «—».
 *
 *  Разряды режем сами, а не через `toLocaleString('ru-RU')`: в разных сборках
 *  ICU разделителем оказывается то U+00A0, то U+202F, и тест на точное
 *  совпадение строки начинал падать на чужой машине. */
export function formatNumber(value, digits = 0) {
    const num = Number(value);
    if (isBlank(value) || !Number.isFinite(num)) return DASH;
    const fixed = Math.abs(num).toFixed(Math.max(0, digits));
    const [whole, fraction] = fixed.split('.');
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
    // «−0» не бывает: после округления знак у нуля только путает.
    const sign = num < 0 && Number(fixed) !== 0 ? MINUS : '';
    return sign + grouped + (fraction ? `,${fraction}` : '');
}

/** Доля 0.5083 → «50,8 %». `null` → «—», 0 → «0,0 %» (это не то же самое). */
export function formatPercent(value, digits = 1) {
    const num = Number(value);
    if (isBlank(value) || !Number.isFinite(num)) return DASH;
    return `${formatNumber(num * 100, digits)}${NBSP}%`;
}

/** 8.5 → «8,5 ч», 212 → «212 ч», `null` → «—».
 *
 *  Целые часы показываем без «,0»: в таблице из двадцати строк эти нули —
 *  ровно тот шум, который владелец считает браком. */
export function formatHours(value) {
    const num = Number(value);
    if (isBlank(value) || !Number.isFinite(num)) return DASH;
    const rounded = Math.round(num * 10) / 10;
    const digits = Number.isInteger(rounded) ? 0 : 1;
    return `${formatNumber(rounded, digits)}${NBSP}ч`;
}

/** 125 → «2:05», 3725 → «1:02:05», `null` → «—».
 *
 *  Ноль здесь остаётся «0:00», а не прочерком: у времени ответа ноль секунд —
 *  это измеренный факт (ответил ботом мгновенно), а не отсутствие данных. */
export function formatSeconds(value) {
    const num = Number(value);
    if (isBlank(value) || !Number.isFinite(num) || num < 0) return DASH;
    const total = Math.round(num);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return hours ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

// ── Дельты к предыдущему периоду ─────────────────────────────────────────────

/* Вид дельты. Проценты обязаны подписываться «п.п.»: «+3,8» без единицы
   читается и как «плюс 3,8 процентных пункта дозвона», и как «плюс 3,8 лида», а
   это разные новости. Порог значимости `min` — ниже него дельта не красится:
   колебание в пол-пункта цветом не отмечают, иначе экран светится всегда. */
const DELTA_KINDS = {
    percent: { scale: 100, digits: 1, suffix: `${NBSP}п.п.`, min: 0.005 },
    count: { scale: 1, digits: 0, suffix: '', min: 1 },
    hours: { scale: 1, digits: 1, suffix: `${NBSP}ч`, min: 0.5 },
    seconds: { scale: 1, digits: 0, suffix: `${NBSP}с`, min: 5 },
    number: { scale: 1, digits: 1, suffix: '', min: 0.1 },
};

/** Дельта к прошлому периоду → `{text, tone}`.
 *
 *  @param delta      разница из `compare[metric].delta` (у процентов — в долях)
 *  @param kind       'percent' | 'count' | 'hours' | 'seconds' | 'number'
 *  @param direction  'up' | 'down' | 'flat' из `compare[metric].direction`
 *
 *  Цвет берём из `direction`, а не из знака: сервер уже учёл, что у отказов и
 *  нецелевых рост — это плохо (`metrics.LOWER_IS_BETTER`). Красить по знаку
 *  значило бы хвалить отдел за выросшие отказы.
 */
export function formatDelta(delta, kind = 'count', direction = '') {
    const spec = DELTA_KINDS[kind] || DELTA_KINDS.count;
    const num = Number(delta);
    if (isBlank(delta) || !Number.isFinite(num)) return { text: DASH, tone: '' };

    const scaled = num * spec.scale;
    const shown = formatNumber(Math.abs(scaled), spec.digits);
    const rounded = Number(Math.abs(scaled).toFixed(spec.digits));
    const sign = rounded === 0 ? '' : (scaled > 0 ? '+' : MINUS);
    const text = `${sign}${shown}${spec.suffix}`;

    if (rounded === 0 || Math.abs(num) < spec.min) return { text, tone: '' };
    if (direction === 'up') return { text, tone: 'green' };
    if (direction === 'down') return { text, tone: 'red' };
    return { text, tone: '' };
}

// ── Тон по порогам ───────────────────────────────────────────────────────────

/* Пороги приходят из `/targets` числами процентов (green_from = 100,
   amber_from = 80), а выполнение плана — долей (1.05). Приводим к одной шкале:
   значение больше 1,5 читаем как проценты. Без этого «1.05 >= 100» никогда не
   срабатывало бы и зелёного на экране не было бы вообще. */
const asShare = (threshold, fallback) => {
    const num = Number(threshold);
    if (!Number.isFinite(num)) return fallback;
    return num > 1.5 ? num / 100 : num;
};

/** Выполнение плана → '' | 'green' | 'amber' | 'red'.
 *
 *  `null` — пустая строка: нейтральное не красим, это правило приёмки. */
export function toneForRate(rate, greenFrom = 100, amberFrom = 80) {
    const num = Number(rate);
    if (isBlank(rate) || !Number.isFinite(num)) return '';
    const green = asShare(greenFrom, 1);
    const amber = asShare(amberFrom, 0.8);
    if (num >= green) return 'green';
    if (num >= amber) return 'amber';
    return 'red';
}

/* Классы тона. Здесь, а не в каждом компоненте: цветов ровно четыре, и
   расползание оттенков по файлам — это тот же шум, только в коде. */
export const TONE_TEXT = {
    green: 'text-emerald-600',
    amber: 'text-amber-600',
    red: 'text-rose-600',
    '': 'text-slate-400',
};

export const TONE_PILL = {
    green: 'bg-emerald-50 text-emerald-700 ring-emerald-100',
    amber: 'bg-amber-50 text-amber-700 ring-amber-100',
    red: 'bg-rose-50 text-rose-600 ring-rose-100',
    '': 'bg-slate-100 text-slate-600 ring-slate-200/70',
};

// ── Тепловая карта ───────────────────────────────────────────────────────────

/* Один тон с альфа-рампой вместо радуги: цветовая шкала из пяти оттенков
   заставляет искать глазами легенду, а насыщенность читается сразу. */
const HEAT_RGB = '79, 70, 229';       // indigo-600, тот же, что у баров отчёта
const HEAT_MIN = 0.08;                // чтобы ячейка с нулём не сливалась с пустой
const HEAT_MAX = 0.9;                 // 100 % насыщенности съедает текст в ячейке
const HEAT_TOP = 1.25;                // 125 % плана и выше — полная заливка

/** Выполнение плана → 0..1 для заливки ячейки. `null` → 0: день без данных не
 *  красится вовсе, иначе прогул выглядит как работа на минимуме. */
export function heatAlpha(rate) {
    const num = Number(rate);
    if (isBlank(rate) || !Number.isFinite(num)) return 0;
    const clamped = Math.min(Math.max(num, 0), HEAT_TOP) / HEAT_TOP;
    return Math.round((HEAT_MIN + clamped * (HEAT_MAX - HEAT_MIN)) * 100) / 100;
}

/** Стиль ячейки тепловой карты. Пустая ячейка остаётся прозрачной. */
export function heatStyle(rate) {
    const alpha = heatAlpha(rate);
    return alpha ? { backgroundColor: `rgba(${HEAT_RGB}, ${alpha})` } : {};
}

// ── Даты и пресеты периода ───────────────────────────────────────────────────

/* Дни складываем из локальных частей даты, а не через `toISOString()`: тот
   переводит в UTC, и в Алматы (+5) «сегодня» превращалось бы во «вчера» для
   всего, что раньше пяти утра. В проекте на смещении уже горели дважды. */
function asLocalDate(value) {
    if (value instanceof Date && !Number.isNaN(value.getTime())) {
        return new Date(value.getFullYear(), value.getMonth(), value.getDate());
    }
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? '').slice(0, 10));
    if (!match) return null;
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

/** Дата → 'ГГГГ-ММ-ДД' в местном поясе (формат периода в API). */
export function isoDay(value) {
    const day = asLocalDate(value) || (value === undefined ? new Date() : null);
    if (!day) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

/** '2026-09-03' → «03.09». Год в ячейке не нужен — он в шапке периода. */
export function shortDay(value) {
    const parts = String(value ?? '').slice(0, 10).split('-');
    return parts.length === 3 && parts[0] ? `${parts[2]}.${parts[1]}` : DASH;
}

/** '2026-09-11 14:22:03' → «11.09.2026 14:22». Для строки свежести данных. */
export function formatMoment(value) {
    const text = String(value ?? '');
    const parts = text.slice(0, 10).split('-');
    if (parts.length !== 3 || !parts[0]) return DASH;
    const time = text.slice(11, 16);
    const day = `${parts[2]}.${parts[1]}.${parts[0]}`;
    return time ? `${day} ${time}` : day;
}

export const DATE_PRESETS = [
    { key: 'today', label: 'Сегодня' },
    { key: 'yesterday', label: 'Вчера' },
    { key: 'week7', label: '7 дней' },
    { key: 'month', label: 'Этот месяц' },
    { key: 'prev_month', label: 'Прошлый месяц' },
];

/** Пресет → `{from, to}` в 'ГГГГ-ММ-ДД'. Незнакомый ключ → `null`.
 *
 *  Именно `null`, а не «последние 7 дней по умолчанию»: молча показать чужой
 *  период хуже, чем не показать ничего — человек примет цифры за выбранные им.
 *
 *  «Этот месяц» заканчивается СЕГОДНЯ, а не последним днём месяца: будущих
 *  суток в данных нет, и они занижали бы средние и план.
 */
export function presetRange(key, today) {
    const base = asLocalDate(today) || new Date();
    const anchor = new Date(base.getFullYear(), base.getMonth(), base.getDate());
    const shifted = (days) => {
        const day = new Date(anchor);
        day.setDate(day.getDate() - days);
        return isoDay(day);
    };
    switch (key) {
        case 'today':
            return { from: shifted(0), to: shifted(0) };
        case 'yesterday':
            return { from: shifted(1), to: shifted(1) };
        case 'week7':
            return { from: shifted(6), to: shifted(0) };
        case 'month':
            return {
                from: isoDay(new Date(anchor.getFullYear(), anchor.getMonth(), 1)),
                to: isoDay(anchor),
            };
        case 'prev_month': {
            const first = new Date(anchor.getFullYear(), anchor.getMonth() - 1, 1);
            const last = new Date(anchor.getFullYear(), anchor.getMonth(), 0);
            return { from: isoDay(first), to: isoDay(last) };
        }
        default:
            return null;
    }
}

// ── Подписи показателей ──────────────────────────────────────────────────────

const FUNNEL_STEPS = {
    handled: 'Обработано',
    reached: 'Дозвон',
    not_reached: 'Недозвон',
    agreed: 'Согласия',
    succeeded: 'Успешно',
    rejected: 'Отказы',
    untargeted: 'Нецелевые',
    callbacks: 'Перезвоны',
    inbound: 'Входящие',
    chats: 'Чаты',
    tickets: 'Обращения',
};

/** Ключ шага воронки → подпись. Незнакомый ключ отдаём как есть: пусть будет
 *  видно, что сервер прислал новое поле, чем оно молча исчезнет с экрана. */
export function funnelStepLabel(key) {
    return FUNNEL_STEPS[key] || String(key || '');
}

/* Корзины причин из op_funnel/metrics.py. Порядок — как читают воронку: сперва
   до кого не дошли, потом кто отказал, потом кто не подходил, и отдельно те,
   кого увели в другой процесс. */
export const BUCKET_ORDER = ['nedozvon', 'otkaz', 'netsel', 'moved'];

const BUCKET_LABELS = {
    nedozvon: 'Недозвон',
    otkaz: 'Отказы',
    netsel: 'Нецелевые',
    /* Полное «Увели в другой процесс», а не короткое «Увели»: лид не отказал,
       его забрал соседний этап, и в отказы он не идёт. Короткая подпись
       заставляла считать эту корзину потерей. */
    moved: 'Увели в другой процесс',
};

export function reasonBucketLabel(bucket) {
    return BUCKET_LABELS[bucket] || String(bucket || '');
}

/* Подписи конверсий из `metrics.derive_rates`. Знаменатель в подписи назван
   явно: «% согласий» без «от дозвона» люди считают от всей базы и спорят
   с цифрой. */
export const RATE_LABELS = {
    reach_rate: '% дозвона от обработанных',
    attempt_rate: '% дозвона от попыток',
    agree_rate: '% согласий от дозвона',
    success_rate: '% успешных от согласий',
    success_of_reached: '% успешных от дозвона',
    reject_rate: '% отказов от дозвона',
    untargeted_rate: '% нецелевых от дозвона',
    closed_rate: '% закрытых от обработанных',
    leads_per_hour: 'Лидов в час',
    plan_reached_rate: '% плана по дозвонам',
    plan_agreed_rate: '% плана по согласиям',
    chats_per_hour: 'Чатов в час',
};

/* Нормы направления (`op_funnel_targets.metric`). Значения хранятся числами:
   `reply_seconds` — в секундах, `green_from`/`amber_from` — в процентах плана,
   веса — долями. Единица обязана быть в подписи, иначе «120» в поле времени
   ответа правят как минуты. */
export const METRIC_LABELS = {
    reached_per_hour: 'Норма дозвонов в час',
    agreed_per_hour: 'Норма согласий в час',
    plan_per_fte: 'Месячный план на ставку 1,0',
    chats_per_hour: 'Норма чатов в час',
    reply_seconds: 'Таргет времени ответа, сек',
    quality: 'Таргет качества, %',
    weight_reply: 'Вес времени ответа',
    weight_chats: 'Вес нагрузки',
    weight_quality: 'Вес качества',
    green_from: 'Зелёный от, % плана',
    amber_from: 'Жёлтый от, % плана',
};

export const METRIC_HINTS = {
    reached_per_hour: 'Из неё считается дневной план дозвонов: часы × норму.',
    agreed_per_hour: 'Из неё считается дневной план согласий: часы × норму.',
    plan_per_fte: 'Новичку план ×0,8, у ночной смены своя строка.',
    chats_per_hour: 'Чаты и обращения вместе, делённые на отработанные часы.',
    reply_seconds: 'Время от входящего сообщения до первого ответа человека.',
    quality: 'Оценка из «Оценок ИИ», если она есть за эти сутки.',
    green_from: 'Ниже этой границы выполнение перестаёт быть зелёным.',
    amber_from: 'Ниже этой границы выполнение красное и попадает в аномалии.',
};

/* Откуда взяты часы. Показывать обязательно: у Верификаторов и части «Потока»
   статусов iCORE Phone нет, часы берутся по графику смен, и это другое число —
   план, а не факт. Молчать об этом нельзя. */
export const HOURS_SOURCE_LABEL = {
    phone: 'по телефону',
    schedule: 'по графику',
};

export function hoursSourceLabel(source) {
    return HOURS_SOURCE_LABEL[source] || '';
}

// ── Аномалии ─────────────────────────────────────────────────────────────────

export const ANOMALY_LABELS = {
    zero_hours_with_leads: 'Ноль часов при обработанных лидах',
    volume_jump: 'Объём вырос кратно',
    below_target_streak: 'Ниже порога два дня подряд',
};

/* Русские числительные: «41 лид», «2 лида», «5 лидов». Без этого фраза про
   аномалию читается как машинный лог, а её должен читать руководитель. */
function plural(value, forms) {
    const num = Math.abs(Math.trunc(Number(value) || 0));
    const tail = num % 100;
    if (tail >= 11 && tail <= 14) return forms[2];
    const last = num % 10;
    if (last === 1) return forms[0];
    if (last >= 2 && last <= 4) return forms[1];
    return forms[2];
}

/** Флаг аномалии из `/overview` → человеческая фраза.
 *
 *  Имя оператора в неё НЕ входит: панель показывает его отдельной строкой, и
 *  дублировать одно и то же в двух местах экрана запрещено правилом приёмки.
 */
export function anomalyText(item) {
    const kind = item?.kind || '';
    const detail = item?.detail || {};
    const day = shortDay(item?.work_day);
    switch (kind) {
        case 'zero_hours_with_leads': {
            const handled = Number(detail.handled) || 0;
            return `${day}: ${formatNumber(handled)} ${plural(handled, ['лид', 'лида', 'лидов'])} `
                + 'обработано при нулевых часах — часы не сняты с телефона';
        }
        case 'volume_jump':
            return `${day}: объём вырос с ${formatNumber(detail.was)} до ${formatNumber(detail.became)} `
                + '— похоже на дубль в выгрузке';
        case 'below_target_streak': {
            const days = Number(detail.days) || 0;
            return `${day}: ${formatNumber(days)} ${plural(days, ['день', 'дня', 'дней'])} подряд `
                + `ниже порога, ${formatPercent(detail.rate)} плана`;
        }
        default:
            return `${day}: необычные цифры за сутки`;
    }
}
