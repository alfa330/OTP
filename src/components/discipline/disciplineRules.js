/*
 * Правила раздела «Дисциплина» на клиенте. Чистый модуль: ни React, ни запросов.
 *
 * Сервер — источник правды (discipline/catalog.py): номер, ступень и статус
 * записи приходят готовыми. Здесь только то, что нужно, чтобы НАРИСОВАТЬ их
 * одинаково везде: подписи ступеней и статусов, тон (цвет со смыслом), слова
 * «нарушение/нарушения/нарушений», даты по-русски. Тест (discipline_rules.test.mjs
 * и tests/test_discipline_frontend_wiring.py) сверяет подписи с сервером.
 */

export const STEP_LABELS = {
    training: 'Тренинг',
    explanation: 'Объяснительная',
    act: 'Объяснительная + акт',
};

export const STATE_LABELS = {
    explanation_requested: 'Объяснительная запрошена',
    explanation_received: 'Объяснительная получена',
    explanation_refused: 'Отказ от объяснения',
    training_required: 'Провести тренинг',
    training_done: 'Тренинг проведён',
    act_required: 'Требуется акт',
    act_done: 'Акт составлен',
    measure_required: 'Нужна мера',
    measure_pending: 'Мера на утверждении',
    measure_applied: 'Мера применена',
    recorded: 'Зафиксировано',
    closed: 'Закрыто',
    cancelled: 'Отменено',
};

/* Тон — смысл, а не украшение (требование владельца: цвет только там, где он
   что-то значит). red — нужен акт или мера, amber — ждём действия, rose —
   тренинг не проведён, green — сделано, blue — мера применена, slate — нейтрально. */
export const STATE_TONES = {
    explanation_requested: 'amber',
    explanation_received: 'slate',
    explanation_refused: 'amber',
    training_required: 'rose',
    training_done: 'green',
    act_required: 'red',
    act_done: 'red',
    measure_required: 'red',
    measure_pending: 'amber',
    measure_applied: 'blue',
    recorded: 'slate',
    closed: 'slate',
    cancelled: 'slate',
};

export const TONE_CHIP = {
    red: 'bg-rose-50 text-rose-700 ring-1 ring-rose-200/70',
    rose: 'bg-rose-50 text-rose-600 ring-1 ring-rose-100',
    amber: 'bg-amber-50 text-amber-800 ring-1 ring-amber-200/70',
    green: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
    blue: 'bg-blue-50 text-blue-700 ring-1 ring-blue-100',
    slate: 'bg-slate-100 text-slate-600',
};

/* Плашка у поля «Оператор» (ТЗ п. 6.2): серая — нарушений нет, жёлтая —
   уже есть, красная — следующее потребует акта или человек на особом контроле. */
export const PLATE_TONES = {
    gray: 'bg-slate-50 text-slate-700 ring-1 ring-slate-200/80',
    yellow: 'bg-amber-50 text-amber-900 ring-1 ring-amber-200/80',
    red: 'bg-rose-50 text-rose-900 ring-1 ring-rose-200/80',
};

/* Подпись ступени в строке — как на скриншоте заказчика. */
export const stepTitle = (action, explanationNo) => {
    if (action === 'training') return 'Тренинг';
    if (action === 'explanation') return explanationNo ? `Объясн. №${explanationNo}` : 'Объяснительная';
    if (action === 'act') return explanationNo ? `Объясн. №${explanationNo} + акт` : 'Объяснительная + акт';
    return '';
};

/* Тон чипа ступени в списке: акт — красный всегда; тренинг — зелёный, когда
   проведён, и розовый «Провести тренинг», пока нет; объяснительная — янтарная,
   пока её ждём, нейтральная, когда получена. Отменённая — серая. */
export const stepChip = (item) => {
    if (!item) return null;
    if (item.state === 'cancelled') return { label: 'Отменено', tone: 'slate' };
    if (item.action === 'act') return { label: item.step_title || stepTitle('act', item.explanation_no), tone: 'red' };
    if (item.action === 'training') {
        return item.state === 'training_required'
            ? { label: 'Провести тренинг', tone: 'rose' }
            : { label: 'Тренинг', tone: 'green' };
    }
    if (item.action === 'explanation') {
        if (item.state === 'explanation_refused') return { label: 'Отказ от объяснения', tone: 'amber' };
        // Ждём — янтарная, получена — зелёная, как у тренинга: одна плашка вместо
        // «Объясн. №1» рядом с «Объяснительная получена».
        const waiting = item.state === 'explanation_requested';
        return { label: item.step_title || stepTitle('explanation', item.explanation_no), tone: waiting ? 'amber' : 'green' };
    }
    return { label: STATE_LABELS[item.state] || '', tone: STATE_TONES[item.state] || 'slate' };
};

export const pluralViolations = (count) => {
    const n = Math.abs(Number(count) || 0);
    if (n % 10 === 1 && n % 100 !== 11) return 'нарушение';
    if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return 'нарушения';
    return 'нарушений';
};

const ORDINALS = ['первое', 'второе', 'третье', 'четвёртое', 'пятое', 'шестое', 'седьмое', 'восьмое', 'девятое', 'десятое'];
export const ordinalWord = (n) => (n >= 1 && n <= ORDINALS.length ? ORDINALS[n - 1] : `${n}-е`);

/* Цвет шапки группы «по сотрудникам» — стадия лестницы, как на скриншоте
   заказчика: зелёная — пока только тренинг, жёлтая — дошли до объяснительных,
   красная — акт. */
const actStepOf = (items, actStep) => actStep || (items || []).map((item) => Number(item.act_step) || 0).find(Boolean) || null;

export const groupTone = (items) => {
    const live = (items || []).filter((item) => item.state !== 'cancelled');
    if (live.some((item) => item.action === 'act')) return 'red';
    if (live.some((item) => item.action === 'explanation')) return 'yellow';
    return 'normal';
};

export const groupSummary = (items, actStep = null) => {
    const live = (items || []).filter((item) => item.state !== 'cancelled');
    actStep = actStepOf(live, actStep);
    const count = live.length;
    const head = `${count} ${pluralViolations(count)}`;
    const act = live.find((item) => item.action === 'act');
    if (act) return `${head} · ${act.act?.has_file ? 'акт составлен' : 'нужен акт'}`;
    const training = live.find((item) => item.state === 'training_required');
    if (training) return `${head} · тренинг не проведён`;
    const maxWeighted = Math.max(0, ...live.map((item) => Number(item.weighted) || 0));
    if (actStep && maxWeighted >= actStep - 1) return `${head} · следующее — акт`;
    const last = live[0];
    if (last?.explanation_no) return `${head} · следующее — объясн. №${last.explanation_no + 1}`;
    return head;
};

const pad = (n) => String(n).padStart(2, '0');

export const parseIso = (value) => {
    if (!value) return null;
    const text = String(value);
    const m = text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
    if (!m) return null;
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] || 0), Number(m[5] || 0));
};

export const fmtDate = (value) => {
    const d = parseIso(value);
    return d ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}` : '';
};

export const fmtShort = (value) => {
    const d = parseIso(value);
    return d ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}` : '';
};

export const fmtDateTime = (value) => {
    const d = parseIso(value);
    return d ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
};

export const fmtTime = (value) => {
    const d = parseIso(value);
    return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
};

export const localInputValue = (date = new Date()) => (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
);

export const todayIso = (date = new Date()) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

export const shiftIsoDays = (iso, days) => {
    const d = parseIso(iso) || new Date();
    d.setDate(d.getDate() + days);
    return todayIso(d);
};

/* ── Время в форме нарушения ───────────────────────────────────────────────
   Дата, смена и тайм-код набираются пикерами портала (TimeFields.jsx), а в
   форме и черновике лежат строками — теми же, что уходят на сервер. Половина
   набранного (одна граница смены, дата без времени) тоже строка: черновик не
   теряет набранное, а проверка перед отправкой говорит, чего не хватает. */

// Сервер принимает момент до 5 минут вперёд (часы устройства и сервера).
const FUTURE_SLACK_MS = 5 * 60 * 1000;

const normTime = (value) => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(String(value || '').trim());
    if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) return '';
    return `${pad(Number(m[1]))}:${m[2]}`;
};

// «ГГГГ-ММ-ДДTЧЧ:ММ» ↔ дата и время по отдельности.
export const splitMoment = (value) => {
    const m = /^(\d{4}-\d{2}-\d{2})?T?(\d{2}:\d{2})?$/.exec(String(value || '').trim());
    return m ? { date: m[1] || '', time: m[2] || '' } : { date: '', time: '' };
};

export const joinMoment = (date, time) => (date || time ? `${date || ''}T${time || ''}` : '');

export const isFullMoment = (value) => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(String(value || ''));

export const momentProblem = (value, now = new Date()) => {
    if (!isFullMoment(value)) return 'Укажите дату и время';
    if (value > localInputValue(new Date(now.getTime() + FUTURE_SLACK_MS))) return 'Это время ещё не наступило';
    return null;
};

// Смена — «ЧЧ:ММ–ЧЧ:ММ», как её отдаёт график. Набрана одна граница — «09:00–».
export const SHIFT_DASH = '–';

export const shiftParts = (label) => {
    const text = String(label || '').trim();
    if (!text) return { start: '', end: '', other: '' };
    const m = /^(\d{1,2}:\d{2})?\s*[–—-]\s*(\d{1,2}:\d{2})?$/.exec(text);
    const start = normTime(m?.[1]);
    const end = normTime(m?.[2]);
    if (!m || (m[1] && !start) || (m[2] && !end) || (!start && !end)) {
        return { start: '', end: '', other: text };   // свободный текст прежних записей
    }
    return { start, end, other: '' };
};

export const joinShift = (start, end) => (start || end ? `${start || ''}${SHIFT_DASH}${end || ''}` : '');

// Ночная смена: конец раньше начала — смена уходит в следующие сутки.
export const isOvernight = (start, end) => Boolean(start && end && end < start);

export const shiftProblem = (label) => {
    const { start, end, other } = shiftParts(label);
    if (other) return 'Смена — время начала и конца, например 09:00–18:00';
    if (!start && !end) return 'Укажите смену';
    if (!start) return 'Укажите начало смены';
    if (!end) return 'Укажите конец смены';
    if (start === end) return 'Начало и конец смены совпадают';
    return null;
};

/* Тайм-код записи экрана — время на записи в день нарушения: фрагменты
   «ЧЧ:ММ» или «ЧЧ:ММ–ЧЧ:ММ» через запятую. Конец фрагмента необязателен. */
export const TIMECODES_MAX = 5;

export const timecodeRows = (note) => {
    const text = String(note || '').trim();
    if (!text) return { rows: [], other: '' };
    const rows = [];
    for (const part of text.split(/\s*[,;\n]\s*/).filter(Boolean)) {
        const m = /^(\d{1,2}:\d{2})?\s*(?:[–—-]\s*(\d{1,2}:\d{2})?)?$/.exec(part);
        const start = normTime(m?.[1]);
        const end = normTime(m?.[2]);
        if (!m || (m[1] && !start) || (m[2] && !end) || (!start && !end)) return { rows: [], other: text };
        rows.push({ start, end });
    }
    return { rows, other: '' };
};

export const joinTimecodes = (rows) => (rows || [])
    .filter((row) => row && (row.start || row.end))
    .map((row) => (row.end && row.end !== row.start ? `${row.start || ''}${SHIFT_DASH}${row.end}` : row.start))
    .join(', ');

export const hasTimecode = (note) => timecodeRows(note).rows.some((row) => row.start);

export const timecodesProblem = (note) => {
    const { rows, other } = timecodeRows(note);
    if (other) return 'Тайм-код — время на записи экрана, например 14:32–14:41';
    if (rows.some((row) => !row.start)) return 'Укажите, с какого времени смотреть запись';
    if (rows.length > TIMECODES_MAX) return `Не больше ${TIMECODES_MAX} фрагментов записи`;
    return null;
};

/* Сумма с неразрывными пробелами: разряды и «₸» не разъезжаются по строкам.
   Не toLocaleString — тот ставит разный пробел в разных браузерах. */
export const fmtMoney = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '';
    const digits = String(Math.round(Math.abs(n))).replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0');
    return `${n < 0 ? '−' : ''}${digits}\u00a0₸`;
};

export const daysLeftText = (days) => {
    const n = Number(days);
    if (!Number.isFinite(n)) return '';
    if (n < 0) return `просрочено на ${Math.abs(n)} дн.`;
    if (n === 0) return 'заканчивается сегодня';
    return `осталось ${n} дн.`;
};

export const fileSizeText = (bytes) => {
    const n = Number(bytes) || 0;
    if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`;
    if (n >= 1024) return `${Math.round(n / 1024)} КБ`;
    return `${n} Б`;
};

export const isPreviewable = (file) => /^(image\/(jpeg|png)|application\/pdf)$/.test(file?.content_type || '');

/* Черновик формы фиксации — в браузере (ТЗ п. 16: «черновик сохраняется каждые
   30 секунд, чтобы СВ не терял описание при обрыве связи»). При обрыве связи
   сервер недоступен по определению, поэтому место черновика — устройство.
   Ключ — свой у каждого пользователя: на общем компьютере СВ не увидит
   черновик коллеги (там ФИО и описание — персональные данные), а при выходе
   из портала черновики стираются. */
export const DRAFT_PREFIX = 'otp.discipline.draft.v1.';
export const draftKey = (userId) => `${DRAFT_PREFIX}${userId || 'anon'}`;
export const DRAFT_INTERVAL_MS = 30000;

/* Пределы файлов — как на сервере (catalog.FILE_KINDS, сверяет тест). Размер
   проверяется до отправки: иначе по мобильной сети человек ждал бы загрузку
   60 МБ, чтобы узнать, что предел — 50. Тип проверяет сервер: он узнаёт файл
   и по содержимому, когда у фото с телефона нет расширения. */
const MB = 1024 * 1024;
export const FILE_LIMITS = {
    evidence: { maxBytes: 10 * MB, videoMaxBytes: 50 * MB, maxFiles: 5 },
    explanation: { maxBytes: 10 * MB, maxFiles: 1 },
    act: { maxBytes: 20 * MB, maxFiles: 1 },
    refusal_act: { maxBytes: 20 * MB, maxFiles: 1 },
    act_template: { maxBytes: 5 * MB, maxFiles: 1 },
};

const isVideo = (file) => /^video\//.test(file?.type || '') || /\.mp4$/i.test(file?.name || '');

/* Что не так с выбранными файлами (или null): число и размер. already — сколько
   действующих файлов этого вида уже у записи. */
export const filesProblem = (kind, files, already = 0) => {
    const spec = FILE_LIMITS[kind];
    if (!spec) return null;
    const list = Array.from(files || []);
    if (already + list.length > spec.maxFiles) {
        const left = Math.max(0, spec.maxFiles - already);
        return left ? `Можно добавить ещё ${left} из ${spec.maxFiles}` : `Уже ${spec.maxFiles} из ${spec.maxFiles} — больше не добавить`;
    }
    for (const file of list) {
        const limit = isVideo(file) && spec.videoMaxBytes ? spec.videoMaxBytes : spec.maxBytes;
        if ((file?.size || 0) > limit) {
            return `«${file.name}» — ${fileSizeText(file.size)}, предел ${Math.round(limit / MB)} МБ`;
        }
    }
    return null;
};

/* Статус рядом со ступенью — только тот, что ступень сама не говорит. В
   карточке это лишь «Закрыто»: акт и мера показаны чипом у своей секции, а
   объяснительная, тренинг и отмена — подписью и цветом ступени. Вторая плашка
   про то же была бы повтором (правило владельца: шум = брак). В строке журнала
   секций нет — там рядом со ступенью и состояние меры. */
export const CARD_STATES_BESIDE_STEP = ['closed'];
export const ROW_STATES_BESIDE_STEP = ['measure_required', 'measure_pending', 'measure_applied', 'closed'];

export const DESCRIPTION_MIN = 30;

/* Лестницы отдела: у каждой название, действует одна (catalog.normalize_ladders
   на сервере). Здесь — та же проверка названий до отправки, чтобы подсветить
   поле: пустое, длиннее предела или уже занятое (без учёта регистра).
   Ответ — { [id лестницы]: что не так }. */
export const ladderName = (value) => String(value || '').split(/\s+/).filter(Boolean).join(' ');

export const ladderProblems = (ladders, nameMax = 60) => {
    const problems = {};
    const taken = new Set();
    (ladders || []).forEach((ladder) => {
        const name = ladderName(ladder?.name);
        if (!name) { problems[ladder.id] = 'Назовите лестницу'; return; }
        if (name.length > nameMax) { problems[ladder.id] = `Название — не длиннее ${nameMax} символов`; return; }
        const key = name.toLocaleLowerCase('ru');
        if (taken.has(key)) problems[ladder.id] = `Лестница «${name}» уже есть — назовите эту иначе`;
        taken.add(key);
    });
    return problems;
};

/* Основание и причина контроля одной строкой. Причина испытательного срока по
   умолчанию повторяет основание — тогда показываем его один раз. */
export const controlReason = (row) => {
    const source = String(row?.source_label || '').trim();
    const reason = String(row?.reason || '').trim();
    if (!reason) return source;
    if (!source || reason.toLowerCase().startsWith(source.toLowerCase())) return reason;
    return `${source}: ${reason}`;
};
