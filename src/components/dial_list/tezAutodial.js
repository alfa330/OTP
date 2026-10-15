/*
 * «Автообзвон Тез» (15.10.2026): чистая логика раздела, без React. На неё написан
 * node-тест (tests/dial_list_tez_autodial.test.mjs).
 *
 * Раздел — тот же движок обзвона (dial_list), что у «Удаленного КЦ», но отдел в нём
 * с ПРОФИЛЕМ 'tez' (контракт сервер ↔ сайт ↔ телефон от 15.10.2026, решения владельца):
 *   • цель лида — водитель ВЫШЕЛ НА ЛИНИЮ: у TEZ APP есть его завершённый заказ в
 *     месяце базы. ИИН, Sapar и документы Тез не нужны вовсе;
 *   • статус телефона задаёт режим: «Активный» — телефон звонит сам, подряд;
 *     «Исход» — автообзвон стоит, оператор сам звонит водителям из своих списков
 *     (строки пачки, итоги, «Позвонить ещё раз»); прочие статусы — звонков нет;
 *   • недозвон возвращается по расписанию в минутах, у итога бывает дата —
 *     перезвона или выхода на линию, — и водитель вернётся к своему оператору к сроку;
 *   • распределение — пачками, как в удалённом КЦ; «операторы под распределение» —
 *     те, кому руководитель включил режим лично. Режим отдела целиком остаётся
 *     выключенным: иначе «Настройки SIP» заперли бы карточки всех операторов Тез.
 *
 * Здесь то, в чём сайту нельзя разойтись с сервером и что удобно проверить
 * исполнением: пределы настроек, расписание недозвонов словами, этапы журнала,
 * сроки, метка поездок, проверки офферов и WhatsApp-шаблонов.
 */

import { isNewId } from './outcomeFormat.js';

export const PROFILE_REMOTE_CC = 'remote_cc';
export const PROFILE_TEZ = 'tez';

export const TEZ_SECTION_TITLE = 'Автообзвон Тез';

/** Профиль отдела: 'tez' или 'remote_cc'. Пустое и незнакомое — удалённый КЦ:
    сервер до профилей считал так все отделы раздела. */
export const normalizeProfile = (value) => (
    String(value ?? '').trim().toLowerCase() === PROFILE_TEZ ? PROFILE_TEZ : PROFILE_REMOTE_CC
);

/**
 * Отделы своего профиля из GET /api/dial_list/departments. Сервер режет список сам
 * (?profile=…), здесь — вторая страховка на время раскатки: сервер, который профилей
 * ещё не знает, отдал бы «Автообзвону Тез» отделы удалённого КЦ, и настройки Тез
 * легли бы на чужой отдел.
 */
export const departmentsOfProfile = (list, profile) => (Array.isArray(list) ? list : [])
    .filter((d) => d && normalizeProfile(d.profile) === normalizeProfile(profile));

/* Отдел Тез КЦ — тот же id, что SIP_SETTINGS_TEZ_DEPARTMENT_ID в App.jsx; код 'tez'
   в карточке отдела проставлен не у всех записей, поэтому сверяем и то и другое. */
export const TEZ_DEPARTMENT_ID = 560;
export const isTezDepartment = (d) => Number(d?.department_id) === TEZ_DEPARTMENT_ID
    || String(d?.code ?? '').trim().toLowerCase() === 'tez';

/* ─── время ──────────────────────────────────────────────────────────────── */

const pad2 = (n) => String(n).padStart(2, '0');

const toDate = (value) => {
    if (!value) return null;
    const d = value instanceof Date ? value : new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
};

const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

/** «сегодня, 14:02», «вчера, 09:10», «завтра, 11:00», «22.09, 18:00», «22.09.2025». */
export const whenText = (value, now = new Date()) => {
    const d = toDate(value);
    if (!d) return '';
    const clock = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    const ago = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
    if (ago === 0) return `сегодня, ${clock}`;
    if (ago === 1) return `вчера, ${clock}`;
    if (ago === -1) return `завтра, ${clock}`;
    if (d.getFullYear() !== now.getFullYear()) return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()}`;
    return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}, ${clock}`;
};

const dayMonth = (d) => `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}`;
const dayMonthYear = (d) => `${dayMonth(d)}.${d.getFullYear()}`;

/** Минуты словами: 45 → «45 мин», 60 → «1 ч», 90 → «1 ч 30 мин», 1440 → «24 ч»,
    2880 → «2 сут». Сутки — только от двух: «24 ч» владелец пишет сам (ТЗ: «1 ч, 3 ч, 24 ч»). */
export const formatMinutes = (value) => {
    const n = Math.round(Number(value));
    if (!Number.isFinite(n) || n <= 0) return '';
    if (n >= 2880 && n % 1440 === 0) return `${n / 1440} сут`;
    if (n % 60 === 0) return `${n / 60} ч`;
    if (n > 60) return `${Math.floor(n / 60)} ч ${n % 60} мин`;
    return `${n} мин`;
};

/** Секунды словами: 45 → «45 с», 60 → «1 мин», 90 → «1 мин 30 с». */
export const formatSeconds = (value) => {
    const n = Math.max(0, Math.round(Number(value) || 0));
    if (n < 60) return `${n} с`;
    const m = Math.floor(n / 60);
    const s = n % 60;
    return s ? `${m} мин ${s} с` : `${m} мин`;
};

/** Постобработка: 0 — без ограничения. */
export const wrapupText = (value) => (Number(value) === 0 ? 'без ограничения' : formatSeconds(value));

/* ─── настройки автообзвона ─────────────────────────────────────────────── */

/* Пределы — те же, что проверяет сервер (контракт §2): сайт показывает ошибку сразу,
   а не после запроса. */
export const LIMITS = {
    portion_size: [1, 100],
    max_attempts: [1, 20],
    retry_after_hours: [0, 720],
    call_interval_sec: [0, 600],
    wrapup_sec: [10, 1800], // и 0 — «без ограничения»
    callback_own_minutes: [0, 1440],
    followup_after_min: [0, 1440],
};
export const RETRY_SCHEDULE_MAX = 10;
export const RETRY_MINUTES_MAX = 43200; // 30 суток
// С чего начинает новый отдел Тез (контракт §5, enroll): через 1 ч, 3 ч и 24 ч.
export const DEFAULT_RETRY_SCHEDULE = [60, 180, 1440];

export const formatSchedule = (list) => (Array.isArray(list) ? list : [])
    .map(formatMinutes).filter(Boolean).join(', ');

/** Расписание из полей редактора: пустые поля пропускаются, остальное — числом. */
export const scheduleFromInputs = (values) => (Array.isArray(values) ? values : [])
    .map((v) => String(v ?? '').trim())
    .filter(Boolean)
    .map(Number);

/** '' — расписание годится; иначе текст ошибки. Пустое допустимо: тогда повтор — по часам. */
export const validateRetrySchedule = (list) => {
    if (!Array.isArray(list)) return 'Интервалы недозвона — список минут';
    if (list.length > RETRY_SCHEDULE_MAX) return `Интервалов не больше ${RETRY_SCHEDULE_MAX}`;
    for (const n of list) {
        if (!Number.isInteger(n) || n < 1 || n > RETRY_MINUTES_MAX) {
            return `Интервал недозвона — целое число минут от 1 до ${RETRY_MINUTES_MAX}`;
        }
    }
    return '';
};

/**
 * Какая попытка через сколько после недозвона. Элемент i — пауза перед попыткой i+2
 * (после i+1-го недозвона), последний повторяется; попыток не больше maxAttempts —
 * лишние элементы расписания не используются.
 */
export const retryPlan = (list, maxAttempts) => {
    const schedule = (Array.isArray(list) ? list : []).map(Number).filter((n) => Number.isFinite(n) && n > 0);
    const max = Math.max(1, Math.floor(Number(maxAttempts) || 1));
    if (!schedule.length) return [];
    const plan = [];
    for (let attempt = 2; attempt <= max; attempt += 1) {
        plan.push({ attempt, minutes: schedule[Math.min(attempt - 2, schedule.length - 1)] });
    }
    return plan;
};

/** «2-я попытка — через 1 ч, 3-я — через 3 ч». */
export const retryPlanText = (list, maxAttempts) => retryPlan(list, maxAttempts)
    .map((p, i) => `${p.attempt}-я${i === 0 ? ' попытка' : ''} — через ${formatMinutes(p.minutes)}`)
    .join(', ');

/** Форма редактора из настроек отдела (GET …/settings): числа — строками полей ввода. */
export const tezSettingsForm = (settings = {}) => ({
    auto_dial: !!settings.auto_dial,
    call_interval_sec: String(settings.call_interval_sec ?? 10),
    wrapup_sec: String(settings.wrapup_sec ?? 60),
    retry_schedule: (Array.isArray(settings.retry_schedule_min) ? settings.retry_schedule_min : []).map(String),
    callback_own_minutes: String(settings.callback_own_minutes ?? 120),
    followup_after_min: String(settings.followup_after_min ?? 60),
});

const toInt = (value) => {
    const text = String(value ?? '').trim();
    return /^-?\d+$/.test(text) ? Number(text) : NaN;
};

/**
 * Тело PUT …/settings для отдела Тез. Ключа `enabled` (режим отдела целиком) нет
 * намеренно: сервер не трогает поле, которого нет в запросе, а режим отдела Тез
 * обязан оставаться выключенным (решение владельца, контракт §0.2). Профиль здесь
 * тоже не меняется — для этого есть отдельное действие админа.
 */
export const tezSettingsPayload = (form) => ({
    auto_dial: !!form.auto_dial,
    portion_size: toInt(form.portion_size),
    max_attempts: toInt(form.max_attempts),
    retry_after_hours: toInt(form.retry_after_hours),
    call_interval_sec: toInt(form.call_interval_sec),
    wrapup_sec: toInt(form.wrapup_sec),
    retry_schedule_min: (form.retry_schedule || []).map((v) => String(v ?? '').trim()).filter(Boolean).map(toInt),
    callback_own_minutes: toInt(form.callback_own_minutes),
    followup_after_min: toInt(form.followup_after_min),
    caller_id_for_employee: String(form.caller_id_for_employee ?? '').trim(),
    active_period: form.active_period || '',
});

const FIELD_LABELS = {
    portion_size: 'Строк за раз',
    max_attempts: 'Попыток на номер',
    retry_after_hours: 'Повтор через, часов',
    call_interval_sec: 'Пауза между звонками',
    wrapup_sec: 'Постобработка',
    callback_own_minutes: 'Перезвон ждёт своего оператора',
    followup_after_min: 'Дожать через',
};

/** Проверка тела PUT перед запросом. '' — всё верно. */
export const validateTezSettings = (payload) => {
    for (const key of Object.keys(LIMITS)) {
        const value = payload[key];
        const [lo, hi] = LIMITS[key];
        if (key === 'wrapup_sec' && value === 0) continue;
        if (!Number.isInteger(value) || value < lo || value > hi) {
            if (key === 'wrapup_sec') return `${FIELD_LABELS[key]}: 0 (без ограничения) или от ${lo} до ${hi} секунд`;
            return `${FIELD_LABELS[key]}: целое число от ${lo} до ${hi}`;
        }
    }
    return validateRetrySchedule(payload.retry_schedule_min);
};

/* ─── журнал ────────────────────────────────────────────────────────────── */

/* Этапы журнала Тез в порядке полосы (контракт §5). dot — цвет точки: общие с
   удалённым КЦ этапы — тех же цветов. «Обещали выйти» и «Вышли на линию» — в конце,
   как «На проверке у Яндекса» и «Подписали» у удалённого КЦ: это шаг перед целью и
   сама цель. Голубая точка «Обещали выйти» — в тон тому шагу перед целью. */
export const TEZ_STAGES = [
    { value: 'queue', label: 'В очереди', dot: 'bg-slate-400' },
    { value: 'waiting', label: 'Ждут повтора', dot: 'bg-amber-400' },
    { value: 'callback', label: 'Перезвон назначен', dot: 'bg-orange-400' },
    { value: 'issued', label: 'У операторов', dot: 'bg-blue-500' },
    { value: 'answered', label: 'Дозвонились', dot: 'bg-emerald-500' },
    { value: 'exhausted', label: 'Не дозвонились', dot: 'bg-rose-500' },
    { value: 'excluded', label: 'Исключены', dot: 'bg-slate-300' },
    { value: 'followup', label: 'Обещали выйти', dot: 'bg-sky-500' },
    { value: 'signed', label: 'Вышли на линию', dot: 'bg-emerald-600', check: true },
];

/* Этап одной строки — в единственном числе. Сервер называет этап своим словарём,
   где «signed» — «Подписал документы»: у Тез это неправда, поэтому подпись берём отсюда. */
export const TEZ_STAGE_ROW_LABEL = {
    queue: 'В очереди',
    waiting: 'Ждёт повтора',
    callback: 'Перезвон назначен',
    issued: 'У оператора',
    answered: 'Дозвонились',
    exhausted: 'Не дозвонились',
    excluded: 'Исключён',
    followup: 'Обещал выйти на линию',
    signed: 'Вышел на линию',
};

export const tezStageLabel = (lead) => TEZ_STAGE_ROW_LABEL[lead?.stage] || lead?.stage_label || '';

/** Кому засчитан выход на линию: оператор / «вышел сам» / ждём конца звонка. */
export const tezSuccessText = (sign) => {
    if (!sign) return '';
    if (sign.success) return `успешка · ${sign.success.operator?.name || 'оператор'}`;
    if (!sign.resolved) return 'ждём конца звонка';
    return 'вышел сам, без разговора';
};

/**
 * Срок строки журнала: свой перезвон, обещанный выход на линию, повтор недозвона.
 * null — срока нет. overdue — срок наступил: пора звонить (цвет в журнале — со смыслом).
 */
export const leadDue = (lead, now = new Date()) => {
    const nowMs = now.getTime();
    switch (lead?.stage) {
        case 'callback': {
            const due = toDate(lead.owner_call_at);
            if (!due) return null;
            return { text: `перезвон ${whenText(due, now)}`, overdue: due.getTime() <= nowMs };
        }
        case 'followup': {
            const promised = toDate(lead.promised_online_at);
            const due = toDate(lead.owner_call_at);
            if (due && due.getTime() <= nowMs) {
                return { text: promised ? `дожать · обещал ${whenText(promised, now)}` : 'дожать', overdue: true };
            }
            if (promised) return { text: `обещал выйти ${whenText(promised, now)}`, overdue: false };
            return due ? { text: `дожать ${whenText(due, now)}`, overdue: false } : null;
        }
        case 'waiting': {
            const due = toDate(lead.next_retry_at);
            return due ? { text: `повтор ${whenText(due, now)}`, overdue: false } : null;
        }
        default:
            return null;
    }
};

const DAY_MS = 86400000;
// «Отток»: последний заказ до месяца базы старше 30 дней (контракт §3.6).
export const CHURN_DAYS = 30;

/**
 * Метка водителя по ответу TEZ APP. Сервер присылает её готовой (trip.label /
 * driver.label); вычисляем сами, только если ключа нет — по тем же правилам:
 * «Вышел на линию DD.MM» / «Отток · последний заказ DD.MM.YYYY» / «Работал DD.MM» /
 * «Заказов не было»; не проверяли — ''. «Нет аккаунта» от «не ездил» TEZ APP не
 * отличает, поэтому такой метки нет.
 */
export const tripLabel = (trip, now = new Date()) => {
    if (!trip) return '';
    if (typeof trip.label === 'string' && trip.label.trim()) return trip.label.trim();
    if (!trip.checked_at) return '';
    const first = toDate(trip.month_first_order_at);
    if (first) return `Вышел на линию ${dayMonth(first)}`;
    const last = toDate(trip.last_order_before_at);
    if (last) {
        return now.getTime() - last.getTime() > CHURN_DAYS * DAY_MS
            ? `Отток · последний заказ ${dayMonthYear(last)}`
            : `Работал ${dayMonth(last)}`;
    }
    return 'Заказов не было';
};

/** Срок, выбранный с итогом попытки: «выйдет на линию …» / «перезвонить …». */
export const attemptDueText = (attempt, now = new Date()) => {
    const due = toDate(attempt?.due_at);
    if (!due) return '';
    const kind = attempt?.outcome?.due_kind || attempt?.due_kind || '';
    const when = whenText(due, now);
    if (kind === 'online') return `выйдет на линию ${when}`;
    if (kind === 'callback') return `перезвонить ${when}`;
    return `срок ${when}`;
};

/** Кто начал звонок: автообзвон («Активный») или оператор сам («Исход»). '' — телефон не сообщил. */
export const attemptModeText = (attempt) => {
    if (!attempt) return '';
    if (attempt.auto) return 'автообзвон';
    if (attempt.operator_status === 'outbound') return 'вручную, «Исход»';
    if (attempt.operator_status === 'active') return 'вручную, «Активный»';
    return '';
};

export const WA_STATUS_LABEL = { sending: 'отправляется', sent: 'отправлено', failed: 'не отправлено' };

/* ─── операторы ─────────────────────────────────────────────────────────── */

/* Живой статус оператора (GET /overview, live_status — по последнему событию
   телефона за 16 ч). wallboardKey — ключ TEZ_STATUS_STYLE табло Тез: один смысл
   горит на обоих экранах одним цветом. Подписи — словами пилюли телефона. */
export const LIVE_STATUS = {
    active: { label: 'Активный', wallboardKey: 'free', hint: 'автообзвон: телефон звонит сам' },
    outbound: { label: 'Исход', wallboardKey: 'outgoing', hint: 'звонит сам из своих списков' },
    talking: { label: 'В разговоре', wallboardKey: 'talking', hint: '' },
    offline: { label: 'Офлайн', wallboardKey: 'idle_offline', hint: 'звонков нет; после просрочки постобработки — так' },
    break: { label: 'Перерыв', wallboardKey: 'break', hint: 'перерыв, тренинг или тех. причина — звонков нет' },
    off: { label: 'Не в сети', wallboardKey: 'offline', hint: 'телефон выключен или событий нет' },
};

/** Описание статуса или null (ключа нет — сервер его ещё не присылает). */
export const liveStatusMeta = (key) => (key && Object.prototype.hasOwnProperty.call(LIVE_STATUS, key) ? LIVE_STATUS[key] : null);

/** «ставка 0,75»; ставки нет — ''. */
export const rateLabel = (rate) => {
    if (rate === null || rate === undefined || rate === '') return '';
    const n = Number(rate);
    if (!Number.isFinite(n) || n <= 0) return '';
    return `ставка ${String(n).replace('.', ',')}`;
};

/* ─── база ──────────────────────────────────────────────────────────────── */

const rowsText = (rows = [], total = 0) => {
    if (!Array.isArray(rows) || !rows.length) return '';
    const more = total > rows.length ? '…' : '';
    return `${rows.length === 1 && total <= 1 ? 'строка' : 'строки'} ${rows.join(', ')}${more}`;
};

/** Итог загрузки базы Тез одной фразой: ИИН не нужен, поэтому и строк «без ИИН» нет. */
export const tezUploadSummary = (data, periodText = '') => {
    const parts = [`новых ${data?.rows_new ?? 0}`];
    if (data?.rows_duplicate) parts.push(`повторов ${data.rows_duplicate}`);
    if (data?.rows_invalid) {
        const where = rowsText(data.invalid_rows, data.rows_invalid);
        parts.push(`без номера ${data.rows_invalid}${where ? ` (${where})` : ''}`);
    }
    return `${periodText ? `${periodText}: ` : ''}${parts.join(', ')}`;
};

/** Что стало с копией файла в «Базе лидов ОП TEZ» (контракт §0.3): {ok, text} или null. */
export const mirrorSummary = (mirror) => {
    if (!mirror || typeof mirror !== 'object') return null;
    if (mirror.error) return { ok: false, text: `В «Базу лидов ОП TEZ» не попало: ${mirror.error}` };
    const parts = [`новых ${mirror.rows_new ?? 0}`];
    if (mirror.rows_duplicate) parts.push(`повторов ${mirror.rows_duplicate}`);
    if (mirror.rows_invalid) parts.push(`без номера ${mirror.rows_invalid}`);
    return { ok: true, text: `В «Базе лидов ОП TEZ»: ${parts.join(', ')}` };
};

/* ─── акции и офферы ───────────────────────────────────────────────────── */

export const OFFERS_MAX = 50;
export const OFFER_TITLE_MAX = 120;
export const OFFER_DESCRIPTION_MAX = 2000;

/** Список офферов для PUT …/offers: полный, порядок — позиция; новые — без id. */
export const serializeOffers = (items) => (Array.isArray(items) ? items : []).map((o, i) => ({
    id: isNewId(o.id) ? undefined : o.id,
    title: String(o.title ?? '').trim().slice(0, OFFER_TITLE_MAX),
    description: String(o.description ?? '').trim().slice(0, OFFER_DESCRIPTION_MAX),
    is_active: o.is_active !== false,
    position: i + 1,
}));

export const offersSnapshot = (items) => JSON.stringify(serializeOffers(items));

/** '' — список годится; иначе текст ошибки (правила те же, что у сервера). */
export const validateOffers = (payload) => {
    if (payload.length > OFFERS_MAX) return `Офферов не больше ${OFFERS_MAX}`;
    const seen = new Set();
    for (const o of payload) {
        if (!o.title) return 'У каждого оффера должно быть название';
        const key = o.title.toLowerCase();
        if (seen.has(key)) return `Оффер «${o.title}» повторяется`;
        seen.add(key);
    }
    return '';
};

/* ─── WhatsApp ──────────────────────────────────────────────────────────── */

export const WA_TEMPLATES_MAX = 10;
export const WA_PARAMS_MAX = 10;
export const WA_PARAM_MAX = 200;

/* Подстановки в параметрах шаблона (контракт §2). Номер водителя не подставляется
   никогда — его нет и в телефоне. */
export const WA_SUBSTITUTIONS = [
    { key: '{name}', label: 'ФИО водителя' },
    { key: '{first_name}', label: 'имя водителя (второе слово ФИО, нет — первое)' },
    { key: '{operator}', label: 'имя оператора' },
];

/** Имя водителя по правилу {first_name}: второе слово ФИО, нет — первое. */
export const firstNameOf = (fullName) => {
    const parts = String(fullName ?? '').trim().split(/\s+/).filter(Boolean);
    return parts[1] || parts[0] || '';
};

export const WA_SAMPLE = { name: 'Иванов Айбек Серикович', operator: 'Алия' };

/** Сколько переменных {{1}}, {{2}}… в тексте шаблона ChatApp (по наибольшему номеру). */
export const templateVariableCount = (text) => {
    let max = 0;
    for (const m of String(text ?? '').matchAll(/\{\{\s*(\d+)\s*\}\}/g)) max = Math.max(max, Number(m[1]));
    return max;
};

/** Подстановки в параметре, которых сервер не знает: '{phone}' и т. п. */
export const unknownSubstitutions = (value) => [...String(value ?? '').matchAll(/\{([^{}]*)\}/g)]
    .map((m) => `{${m[1]}}`)
    .filter((key) => !WA_SUBSTITUTIONS.some((s) => s.key === key));

/** Почему шаблон нельзя разрешить; '' — можно. Отправлять можно только утверждённые текстовые. */
export const templateBlockReason = (template) => {
    if (!template) return 'нет в ChatApp';
    if (template.supported === true) return '';
    if (String(template.status ?? '').toUpperCase() !== 'APPROVED') return 'не утверждён в WhatsApp';
    if (String(template.type ?? '').toUpperCase() !== 'TEXT') return 'не текстовый';
    return template.supported === false ? 'не поддерживается' : '';
};

/** Подстановки образцом — для предпросмотра на сайте. */
export const fillSubstitutions = (value, sample = WA_SAMPLE) => String(value ?? '')
    .replace(/\{first_name\}/g, firstNameOf(sample.name))
    .replace(/\{name\}/g, sample.name)
    .replace(/\{operator\}/g, sample.operator);

/** Текст шаблона, каким его получит водитель: {{n}} — параметр n с подстановками. */
export const previewTemplate = (text, params = [], sample = WA_SAMPLE) => String(text ?? '')
    .replace(/\{\{\s*(\d+)\s*\}\}/g, (all, n) => {
        const value = String(params?.[Number(n) - 1] ?? '').trim();
        return value ? fillSubstitutions(value, sample) : all;
    });

/** Разрешённые шаблоны для PUT …/whatsapp. */
export const serializeWaAllowed = (allowed) => (Array.isArray(allowed) ? allowed : []).map((t) => ({
    id: String(t.id ?? ''),
    title: String(t.title ?? '').trim().slice(0, 120),
    text: String(t.text ?? ''),
    params: (Array.isArray(t.params) ? t.params : []).map((p) => String(p ?? '').trim()),
}));

export const waSnapshot = (licenseId, allowed) => JSON.stringify({
    license_id: licenseId === '' || licenseId === undefined ? null : licenseId,
    allowed: serializeWaAllowed(allowed),
});

/**
 * Проверка перед PUT …/whatsapp. paramsCountById — сколько переменных у шаблона
 * (params_count из списка ChatApp); шаблона в списке нет — считаем по тексту.
 */
export const validateWaAllowed = (allowed, paramsCountById = {}) => {
    if (allowed.length > WA_TEMPLATES_MAX) return `Разрешённых шаблонов не больше ${WA_TEMPLATES_MAX}`;
    for (const t of allowed) {
        const title = t.title || t.id;
        const params = Array.isArray(t.params) ? t.params : [];
        if (params.length > WA_PARAMS_MAX) return `У шаблона «${title}» не больше ${WA_PARAMS_MAX} параметров`;
        const need = Number.isInteger(paramsCountById[t.id]) ? paramsCountById[t.id] : templateVariableCount(t.text);
        for (let i = 0; i < need; i += 1) {
            if (!String(params[i] ?? '').trim()) return `Шаблон «${title}»: заполните параметр {{${i + 1}}}`;
        }
        for (const p of params) {
            if (p.length > WA_PARAM_MAX) return `Шаблон «${title}»: параметр длиннее ${WA_PARAM_MAX} символов`;
            const unknown = unknownSubstitutions(p);
            if (unknown.length) {
                return `Шаблон «${title}»: подстановки ${unknown.join(', ')} нет — можно ${WA_SUBSTITUTIONS.map((s) => s.key).join(', ')}`;
            }
        }
    }
    return '';
};
