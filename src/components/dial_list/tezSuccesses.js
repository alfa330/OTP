/*
 * Вкладка «Успешки» раздела «Воронка ОП Tez» (03.11.2026): чистая логика без React.
 * На неё написан node-тест (tests/dial_list_tez_successes.test.mjs).
 *
 * Вкладка — бывшее окно «База лидов и успешки» из «Учёта часов» (владелец, 03.11.2026:
 * «перенести весь функционал по успешкам в раздел, загрузка базы и т.д. по сути уже там
 * есть»). Успешки здесь те, по которым считается зарплата ОП: база лидов месяца (её
 * кладёт сюда же загрузка во вкладке «База водителей»), звонки операторов ОП в Binotel
 * и первый заказ в TEZ APP. Правила — на сервере (tez/op_leads.py); здесь подписи,
 * форматы и то, что удобно проверить исполнением: плитки воронки и плана, календарь
 * месяца, проверка формы плана, тексты итогов.
 *
 * Колонки «План успешек», «Выполнение» и «Успешки в час» остались в таблице «Учёта
 * часов» (выбор владельца 03.11.2026): они считаются от часов той же таблицы.
 */

export const STATUS_LABELS = {
    new: 'Новый',
    in_progress: 'В работе',
    already_working: 'Уже работающий',
    success: 'Успешка',
    not_counted: 'Не засчитана',
};

/* Цвет — только со смыслом: зелёный — цель, красный — «не засчитана» (именно её
   операторы приходят оспаривать), янтарный — выехал сам, без нашего звонка.
   «Новый» и «В работе» — нейтральные. */
export const STATUS_TONES = {
    new: 'slate',
    in_progress: 'slate',
    already_working: 'amber',
    success: 'green',
    not_counted: 'red',
};

export const RULE_LABELS = {
    same_month: 'Звонок в месяце поездки',
    prev_month_last7: 'Звонок в последние 7 дней прошлого месяца',
    reactivated_30d: 'Не работал 30+ дней и вернулся после звонка',
    no_call_before_trip: 'Нет звонка до поездки',
    call_before_last7: 'Звонок раньше последних 7 дней прошлого месяца',
    gap_under_30d: 'Заказ был меньше 30 дней назад — водитель не уходил',
    no_call_after_last_order: 'Звонка между последним и новым заказом не было',
    active_prev_month: 'Были заказы в прошлом месяце — уже работал (старое правило)',
    // Коды прежнего правила (окно было на стороне поездки): новый расчёт их не
    // выдаёт, но на закрытых месяцах они остались в базе.
    prev_month_week1: 'Звонок в прошлом месяце, поездка до 7-го (старое правило)',
    trip_after_day7: 'Поездка позже 7-го числа (старое правило)',
};

export const ruleLabel = (code) => RULE_LABELS[code] || '';

/** Варианты отбора по статусу для CustomSelect: '' — все. */
export const STATUS_FILTER_OPTIONS = [
    { value: '', label: 'Все статусы' },
    ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label })),
];

/* ─── числа и даты ───────────────────────────────────────────────────────── */

const NF = new Intl.NumberFormat('ru-RU');

/** Целое с разрядами: «7 195». Пусто — 0. */
export const num = (value) => NF.format(Number(value) || 0);

/** Число с запятой и не больше digits знаков: 5,5; '—' — значения нет. */
export const fmtNumber = (value, digits = 1) => (value == null || value === '' || !Number.isFinite(Number(value))
    ? '—'
    : Number(value).toLocaleString('ru-RU', { maximumFractionDigits: digits }));

/** Процент по-русски: «3,5%». */
export const pctText = (value) => `${fmtNumber(value ?? 0, 1)}%`;

/** Склонение по числу: 1 лид, 2 лида, 5 лидов, 21 лид. */
export const plural = (n, one, few, many) => {
    const abs = Math.abs(Number(n) || 0) % 100;
    const last = abs % 10;
    if (abs >= 11 && abs <= 14) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

const pad2 = (n) => String(n).padStart(2, '0');

/** '2026-06-24' → '24.06': год в месяце отчёта лишний. */
export const fmtDay = (value) => {
    const parts = String(value || '').slice(0, 10).split('-');
    return parts.length === 3 && parts[2] ? `${parts[2]}.${parts[1]}` : '';
};

const ALMATY_PARTS = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Almaty',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
});

/**
 * Момент → «24.06, 14:05» по Алматы. Время со смещением (aware) переводим в Алматы;
 * время без смещения сервер уже отдал местным (AT TIME ZONE 'Asia/Almaty') или оно
 * старое и записано «как есть» — его показываем как записано, не трактуя часовым
 * поясом браузера. Пусто — «—».
 */
export const fmtWhen = (value) => {
    if (!value) return '—';
    const raw = String(value);
    const plain = raw.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
    if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw)) {
        return plain ? `${plain[3]}.${plain[2]}, ${plain[4]}:${plain[5]}` : raw;
    }
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return plain ? `${plain[3]}.${plain[2]}, ${plain[4]}:${plain[5]}` : raw;
    const p = Object.fromEntries(ALMATY_PARTS.formatToParts(parsed)
        .filter((part) => part.type !== 'literal').map((part) => [part.type, part.value]));
    return `${p.day}.${p.month}, ${p.hour}:${p.minute}`;
};

/** Только время «14:05»: в карточке дня дата поездки и так в заголовке. */
export const fmtClock = (value) => {
    const text = fmtWhen(value);
    return text === '—' ? text : text.split(', ')[1] || text;
};

/** Длительность разговора — голым числом секунд, как в выгрузке и в правиле (порог 10). */
export const fmtSeconds = (value) => (value == null ? '—' : String(Math.max(0, Math.round(Number(value) || 0))));

const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
export const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/** '2026-11-03' → '3 ноября, Вт' — заголовок карточки дня. */
export const dayTitle = (iso) => {
    const [y, m, d] = String(iso || '').split('-').map((v) => parseInt(v, 10));
    if (!y || !m || !d) return '';
    const weekday = WEEKDAYS[(new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7];
    return `${d} ${MONTHS_GEN[m - 1]}, ${weekday}`;
};

/* ─── месяц ──────────────────────────────────────────────────────────────── */

/** Текущий месяц 'YYYY-MM' по часам браузера (как MonthPicker «Учёта часов»). */
export const currentMonthKey = (now = new Date()) => `${now.getFullYear()}-${pad2(now.getMonth() + 1)}`;

/** 'YYYY-MM' → { year, month } или null. */
export const monthParts = (key) => {
    const [year, month] = String(key || '').split('-').map((v) => parseInt(v, 10));
    return year >= 2000 && month >= 1 && month <= 12 ? { year, month } : null;
};

/** Имя файла выгрузки — то же, что ставит сервер (tez/routes.py, download_name). */
export const exportFileName = (year, month) => `tez_leads_${year}_${pad2(month)}.xlsx`;

/* ─── календарь «По дням» ────────────────────────────────────────────────── */

/**
 * Клетки месяца: пустые места до первого числа (неделя с понедельника, как во всех
 * графиках портала) и по клетке на день с числом успешек.
 */
export const calendarCells = (year, month, byDay = []) => {
    const counts = new Map((Array.isArray(byDay) ? byDay : []).map((row) => [row.date, Number(row.successes) || 0]));
    const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
    const lead = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7;
    const cells = new Array(lead).fill(null);
    for (let day = 1; day <= days; day += 1) {
        const date = `${year}-${pad2(month)}-${pad2(day)}`;
        cells.push({ day, date, successes: counts.get(date) || 0 });
    }
    return cells;
};

/**
 * Заливка клетки — не украшение, а загрузка дня относительно самого результативного:
 * пустой день не красится вовсе, дальше три ступени зелёного.
 */
export const calendarTone = (count, peak) => {
    if (!count) return 'empty';
    const share = peak ? count / peak : 0;
    if (share <= 0.34) return 'low';
    if (share <= 0.67) return 'mid';
    return 'high';
};

/* ─── воронка базы месяца ────────────────────────────────────────────────── */

/**
 * Плитки воронки (GET /api/tez_leads/stats → funnel). На экране — числа и названия,
 * короткая оговорка с числом под плиткой; как считается — под «i».
 */
export const funnelTiles = (funnel) => {
    const f = funnel || {};
    const window = f.call_window_start && f.call_window_end
        ? `${fmtDay(f.call_window_start)} — ${fmtDay(f.call_window_end)}` : '';
    const successes = Number(f.successes) || 0;
    const carriedIn = Number(f.carried_in) || 0;
    const carriedOut = Number(f.carried_out) || 0;
    return [
        {
            key: 'leads',
            label: 'Загружено лидов',
            value: num(f.leads_total),
            hint: `дублей при загрузке: ${num(f.duplicates)}`,
            help: 'Водители базы месяца: каждый номер — один раз, сколько бы раз его ни загружали. Номера «Реестра тестовых номеров» не считаются.',
        },
        {
            key: 'dialed',
            label: 'Обзвонено',
            value: num(f.dialed),
            hint: `попыток: ${num(f.attempts)}`,
            help: 'Лиды, которым оператор отдела продаж хотя бы раз позвонил — неважно, взяли трубку или нет: сброс, занято и недозвон тоже считаются попыткой.'
                + (window ? ` Учитываются исходящие звонки за ${window} — то же окно, в котором звонок может дать успешку.` : '')
                + ' Звонки техподдержки и линии на тот же номер сюда не входят.',
        },
        {
            key: 'reached',
            label: 'Дозвонились',
            value: num(f.reached),
            hint: 'разговор от 10 с',
            help: 'Из обзвоненных — те, с кем разговор состоялся: от 10 секунд в том же окне и тоже только с оператором отдела продаж. Именно такой звонок правило считает доказательством привлечения.',
        },
        {
            key: 'went_online',
            label: 'Заказ в этом месяце',
            value: num(f.went_online),
            hint: `работали и в прошлом: ${num(f.active_prev_month)}`,
            help: `Водители, у которых есть заказ в отчётном месяце. Из них ${num(f.active_prev_month)} выполняли заказы и в прошлом месяце — значит, уже работали, привлечения не было. Всего «уже работающих» (вместе с теми, кто выехал без нашего звонка): ${num(f.already_working)}; в знаменатель конверсии они не входят.`,
        },
        {
            key: 'successes',
            label: 'Успешки',
            value: num(successes),
            tone: successes ? 'good' : null,
            // Перенос — в подписи, только когда он что-то дал: нулевое «из них переносом» — шум.
            hint: carriedIn
                ? `конверсия ${pctText(f.conversion)} · из них переносом ${num(carriedIn)}`
                : `конверсия ${pctText(f.conversion)}`,
            help: `Конверсия ${pctText(f.conversion)} считается от рабочей части базы (${num(f.workable)} лидов, без «уже работающих»). От всей базы было бы ${pctText(f.conversion_all)}. Не засчитано по правилу дат: ${num(f.not_counted)}.`
                + (carriedIn ? ` Из них ${num(carriedIn)} дали лиды прошлого месяца: водитель выехал в этом месяце, а звонок ему был в прошлом. В «Загружено лидов» они не входят — там только то, что загрузили в этом месяце.` : '')
                + (carriedOut ? ` Лидов этого месяца, которые ещё дорабатываются в следующем: ${num(carriedOut)}.` : ''),
        },
    ];
};

/* ─── план отдела ────────────────────────────────────────────────────────── */

/** Тон выполнения плана: от 100% — цель, от 60% — внимание, ниже — плохо. */
export const closureTone = (pct) => {
    if (pct == null || !Number.isFinite(Number(pct))) return null;
    if (Number(pct) >= 100) return 'good';
    if (Number(pct) >= 60) return 'warn';
    return 'bad';
};

export const PLAN_HINT = 'Индивидуальный план = план продаж на 1 FTE ÷ норма часов на 1 FTE × фактически '
    + 'отработанные часы; весь месяц приёма у новичка ×0,8. План и выполнение каждого оператора — в «Учёте '
    + 'часов»: колонка «План успешек» и вкладка «Успешки».';

/**
 * Плитки плана (GET /api/department_plan → plan и summary): что задано на месяц и
 * как отдел его закрывает. summary нет (у отдела нет групп ОП Tez) — только заданное.
 */
export const planTiles = (plan, summary) => {
    const p = plan || {};
    const tiles = [
        {
            key: 'plan_per_fte',
            label: 'План на 1 FTE',
            value: Number(p.plan_per_fte) > 0 ? fmtNumber(p.plan_per_fte, 2) : '—',
        },
        {
            key: 'norm_hours_fte',
            label: 'Норма часов на 1 FTE',
            value: fmtNumber(p.norm_hours_fte, 2),
        },
    ];
    if (!summary) return tiles;
    const coefficient = fmtNumber(summary.coefficient ?? 0.8, 2);
    return [
        ...tiles,
        {
            key: 'plan_total',
            label: 'Групповой план',
            value: fmtNumber(summary.plan_total),
            help: `План на 1 FTE (${fmtNumber(summary.plan_per_fte)}) × FTE на 1 число (${fmtNumber(summary.fte_total, 2)}) × ${coefficient}. В FTE входят только операторы со статусом «Работает» на 1 число: БС, больничный, отпуск и другие статусы исключаются. Состав и ставки зафиксированы: приём, увольнение и отсутствие сотрудника в течение месяца этот FTE не меняют.`,
        },
        {
            key: 'actual_plan',
            label: 'Фактический план',
            value: fmtNumber(summary.actual_plan),
            help: `Групповой план ÷ FTE на 1 число × FTE по отработанным часам. Учтено ${fmtNumber(summary.actual_hours, 2)} ч ÷ ${fmtNumber(summary.norm_hours_fte, 2)} ч на 1 FTE = ${fmtNumber(summary.actual_fte, 4)} FTE. Учитываются часы всех операторов отдела, включая принятых и уволенных в течение месяца.`,
        },
        {
            key: 'fact',
            label: 'Факт продаж',
            value: fmtNumber(summary.successes_total, 0),
            help: 'Успешки отдела за месяц — по месяцу поездки, без номеров «Реестра тестовых номеров».',
        },
        {
            key: 'closure',
            label: 'Выполнение',
            value: summary.closure_pct == null ? '—' : `${fmtNumber(summary.closure_pct)}%`,
            tone: closureTone(summary.closure_pct),
            help: 'Факт продаж ÷ фактический план × 100%.',
        },
    ];
};

/** Оговорка под плитками плана: почему фактического плана нет. '' — всё посчитано. */
export const planNote = (summary) => {
    if (!summary) return '';
    if (summary.fte_total == null) return 'FTE будет зафиксирован при наступлении месяца.';
    if (Number(summary.fte_total) === 0) return 'На 1 число в отделе нет FTE — фактический план не рассчитывается.';
    if (!(Number(summary.plan_per_fte) > 0)) return 'План на 1 FTE за этот месяц не задан.';
    return '';
};

/**
 * Проверка формы плана до запроса — те же пределы, что у сервера
 * (POST /api/department_plan): план ≥ 0, норма часов больше 0 и не больше 744.
 * '' — можно сохранять.
 */
export const planFormError = (planPerFte, normHoursFte) => {
    const blank = (v) => v === '' || v == null;
    const plan = Number(String(planPerFte ?? '').replace(',', '.'));
    const norm = Number(String(normHoursFte ?? '').replace(',', '.'));
    if (blank(planPerFte) || !Number.isFinite(plan) || plan < 0) return 'План на 1 FTE — число не меньше 0';
    if (blank(normHoursFte) || !Number.isFinite(norm) || norm <= 0 || norm > 744) return 'Норма часов на 1 FTE — больше 0 и не больше 744';
    return '';
};

/** Тело POST /api/department_plan из полей формы (запятая — как точка). */
export const planPayload = (departmentId, year, month, planPerFte, normHoursFte) => ({
    department_id: Number(departmentId),
    year,
    month,
    plan_per_fte: Number(String(planPerFte).replace(',', '.')),
    norm_hours_fte: Number(String(normHoursFte).replace(',', '.')),
});

/* ─── итоги действий ─────────────────────────────────────────────────────── */

/**
 * Итог «Сверить сейчас» одной фразой (POST /api/tez_leads/recompute). Успешки переноса
 * лежат на лидах прошлого месяца и в outcomes.success не входят — без суммы тост показал
 * бы меньше, чем плитка «Успешки». Звонки окна добираются порциями: остаток называем.
 */
export const recomputeSummary = (data) => {
    const o = data?.outcomes || {};
    const carried = Number(o.carried_success) || 0;
    const left = Number(data?.calls_mirror?.days_left) || 0;
    return `Сверка выполнена: успешек ${num((Number(o.success) || 0) + carried)}`
        + (carried ? ` (из них ${num(carried)} переносом с прошлого месяца)` : '')
        + `, уже работающих ${num(o.already_working)}`
        + (left ? `. Звонки за ${left} дн. ещё не выгружены — докачаются ночью или следующей сверкой` : '');
};

/** Итог удаления / восстановления загрузки (POST …/batches/<id>/delete|restore). */
export const batchActionSummary = (mode, batch, data) => {
    const d = data || {};
    const name = batch?.file_name || 'файл';
    if (mode === 'delete') {
        return `База «${name}» удалена: лидов убрано ${num(d.leads_removed)}, успешек снято ${num(d.successes_removed)}, `
            + `оставлено (есть в других загрузках) ${num(d.leads_kept)}. Вернуть можно кнопкой «Восстановить».`;
    }
    return `База «${name}» восстановлена: лидов ${num(d.leads_restored)}, успешек ${num(d.successes_restored)}`
        + (d.leads_merged ? `, слито с уже существующими ${num(d.leads_merged)}` : '')
        + '. Нажмите «Сверить сейчас», чтобы пересчитать успешки.';
};

/** Строка истории загрузки (GET …/batches/<id>/history → events): что сделало действие. */
export const batchEventText = (event) => (event?.action === 'delete'
    ? `лидов убрано ${num(event.leads_removed)}, оставлено ${num(event.leads_kept)}, успешек ${num(event.successes_removed)}`
    : `лидов возвращено ${num(event?.leads_restored)}, успешек ${num(event?.successes_restored)}`
        + (event?.leads_merged ? `, слито ${num(event.leads_merged)}` : ''));

/** Причина удаления: обязательна, до 2000 знаков — как у сервера. '' — годится. */
export const BATCH_REASON_MAX = 2000;
export const batchReasonError = (mode, reason) => {
    const text = String(reason ?? '').trim();
    if (mode === 'delete' && !text) return 'Укажите причину удаления';
    if (text.length > BATCH_REASON_MAX) return `Причина — не длиннее ${BATCH_REASON_MAX} символов`;
    return '';
};

/** Проверка «уже работающих» по загрузке ещё идёт. */
export const batchCheckPending = (batch) => Boolean(batch) && !batch.is_deleted
    && (batch.check_status === 'pending' || batch.check_status === 'running');

/** Плашка проверки загрузки: сколько водителей уже работали до звонка, или что проверка идёт / сорвалась. */
export const batchCheckText = (batch) => {
    if (batch?.check_status === 'done') return `уже работают ${num(batch.already_working)}`;
    if (batch?.check_status === 'error') return 'ошибка проверки';
    return 'проверка…';
};
