/* Правила раздела «Жалобы» без React — чтобы их можно было проверить.
 *
 * Урок «Обращений»: логика, живущая в разметке, не проверяется ничем, и там
 * это стоило четырёх тематик из шести. Поэтому здесь всё, что решает «что
 * показать, что обязательно и чем закончится»: подписи статусов, бейджи ленты,
 * проверка формы, шаги работы с сотрудником. Право сказать «можно» остаётся за
 * сервером (complaints/catalog.py, complaints/access.py) — здесь подсказка.
 *
 * Тесты: tests/complaints_rules.test.mjs (node --test).
 */

export const PROCESS_ALWAYS = 'always';
export const PROCESS_OPTIONAL = 'optional';
export const PROCESS_NEVER = 'never';

export const WORK_UNASSIGNED = 'unassigned';
export const WORK_PENDING = 'pending';
export const WORK_DONE = 'done';

/* Статус жалобы для человека. Три вида, а не два: жалоба, которую только
 * зафиксировали (Яндекс, часть жалоб на парк), закрыта с момента создания, и
 * «Отработана» про неё было бы неправдой — её никто не разбирал. */
export const statusView = (complaint) => {
    if (!complaint) return { label: '—', tone: 'slate' };
    if (complaint.status === 'closed') {
        return complaint.requires_processing === false && !complaint.result_code
            && !complaint.employee_id
            ? { label: 'Зафиксирована', tone: 'slate' }
            : { label: 'Отработана', tone: 'green' };
    }
    // «В работе» — штатное состояние, и красить его нечем: цвет у того, что
    // ждёт действия, а это решают бейджи (rowBadges), а не статус.
    return { label: 'В работе', tone: 'slate' };
};

/* Бейджи строки ленты — только исключения, как в «Обращениях»: штатное «в
 * работе» у сорока строк подряд превращает ленту в светофор. Горит то, что
 * ждёт действия ЗРИТЕЛЯ. */
export const rowBadges = (complaint, viewerId) => {
    const badges = [];
    if (!complaint) return badges;
    const mine = Number(complaint.created_by) === Number(viewerId);
    if (complaint.delivery_status === 'failed') {
        badges.push({ key: 'failed', label: 'Не доставлено', tone: 'red' });
    }
    if (mine && complaint.question_open) {
        badges.push({ key: 'question', label: 'Вопрос вам', tone: 'amber' });
    } else if (complaint.unread && complaint.unread_kind === 'answer') {
        badges.push({ key: 'answer', label: 'Есть ответ', tone: 'blue' });
    }
    if (Number(complaint.responsible_id) === Number(viewerId)
        && complaint.work_state === WORK_PENDING) {
        badges.push({ key: 'work', label: complaint.training_required ? 'Нужен тренинг' : 'Нужна ОС',
                      tone: 'amber' });
    }
    return badges;
};

/* Вторая строка ленты. Оператору важен водитель, разбирающему — ещё и
 * сотрудник: по нему он ищет «своё». */
export const rowSubtitle = (complaint, { handler = false } = {}) => {
    if (!complaint) return '';
    const parts = [];
    if (handler && complaint.employee_name) parts.push(complaint.employee_name);
    else if (handler && complaint.unit_name) parts.push(complaint.unit_name);
    if (complaint.driver_name) parts.push(complaint.driver_name);
    if (complaint.city) parts.push(complaint.city);
    return parts.join(' · ');
};

/* ─── Форма «Новая жалоба» ─────────────────────────────────────────────────── */

export const targetByCode = (meta, code) => (
    ((meta && meta.targets) || []).find((item) => item.code === code) || null);

/* Уйдёт ли жалоба в группу при текущем выборе. Отсюда и подпись кнопки:
 * «Отправить в группу» или «Зафиксировать» — оператор должен знать, кого
 * побеспокоит его нажатие, ДО нажатия. */
export const willProcess = (target, wanted = true) => {
    if (!target) return false;
    if (target.processing === PROCESS_NEVER) return false;
    if (target.processing === PROCESS_ALWAYS) return true;
    return Boolean(wanted);
};

export const submitLabel = (target, wanted = true) => (
    willProcess(target, wanted) ? 'Отправить в группу' : 'Зафиксировать');

/* Направления жалобы на первом экране мастера «Обращений» — там их выбирают
 * среди тематик (решение владельца 29.09.2026). Как у тематик: то, что без
 * группы отправить нельзя, стоит неактивным с пометкой «Нет группы».
 * «Только зафиксировать» (Яндекс) и «по выбору» (парк) работают и без неё. */
export const wizardTargets = (meta) => {
    const groupReady = Boolean(meta && meta.group && meta.group.ready);
    return ((meta && meta.targets) || []).map((item) => ({
        code: item.code,
        title: item.title,
        hint: item.hint,
        is_ready: groupReady || item.processing !== PROCESS_ALWAYS,
    }));
};

/* Подразделения, из которых выбирают у цели. У КЦ — подразделения колл-центра,
 * у фронт-офиса список не нужен: отдел один. */
export const unitDepartments = (meta) => (
    ((meta && meta.departments) || []).filter((item) => item.call_center));

export const frontOfficeDepartment = (meta) => (
    ((meta && meta.departments) || []).find((item) => item.code === 'front_office') || null);

/* Отдел, чьих сотрудников предлагать в поле «Сотрудник». null — поля нет. */
export const employeeDepartmentId = (target, form, meta) => {
    if (!target || !target.employee) return null;
    if (target.unit === 'department') return form.unit_id ? Number(form.unit_id) : null;
    return frontOfficeDepartment(meta)?.id || null;
};

/* Офисы для выбора: сначала выбранного города — туда водитель и ездил, —
 * остальные ниже. Не прячем чужие города совсем: водитель мог назвать город
 * проживания, а жаловаться на офис, куда приезжал. */
export const officeOptions = (meta, city) => {
    const offices = (meta && meta.offices) || [];
    const wanted = String(city || '').trim().toLowerCase();
    const option = (office) => ({
        value: String(office.id),
        label: office.name,
        groupLabel: office.city || 'Без города',
    });
    const own = offices.filter((office) => String(office.city || '').trim().toLowerCase() === wanted);
    const rest = offices.filter((office) => String(office.city || '').trim().toLowerCase() !== wanted);
    return [...own, ...rest].map(option);
};

export const REQUIRED_FIELDS = ['driver_name', 'driver_phone', 'city', 'description'];

/* Предел описания — тот же, что у сервера (catalog.LIMITS). Сервер длинное не
 * режет молча, а отказывает; форма говорит об этом раньше. */
export const DESCRIPTION_LIMIT = 4000;

const FIELD_MESSAGES = {
    target: 'Выберите, на кого или на что жалоба',
    reason: 'Выберите причину жалобы',
    unit_id: 'Выберите подразделение колл-центра',
    driver_name: 'Укажите ФИО водителя',
    driver_phone: 'Укажите номер телефона',
    city: 'Выберите город',
    description: 'Опишите ситуацию',
};

/* Чего не хватает форме. Та же обязательность, что у сервера
 * (catalog.clean_complaint): расходиться им нельзя — иначе кнопка активна, а
 * сервер отвечает отказом, или наоборот. */
export const formProblems = (target, form) => {
    const problems = {};
    const value = (key) => String((form || {})[key] ?? '').trim();
    if (!target) return { target: FIELD_MESSAGES.target };
    if (!value('reason') || !(target.reasons || []).some((item) => item.code === value('reason'))) {
        problems.reason = FIELD_MESSAGES.reason;
    }
    if (target.unit_required && !value('unit_id')) problems.unit_id = FIELD_MESSAGES.unit_id;
    for (const key of REQUIRED_FIELDS) {
        if (!value(key)) problems[key] = FIELD_MESSAGES[key];
    }
    if (value('description').length > DESCRIPTION_LIMIT) {
        problems.description = `Описание длиннее ${DESCRIPTION_LIMIT} символов — сократите`;
    }
    return problems;
};

/* Тело запроса. Поля, которых у цели нет, не отправляем вовсе: сотрудник,
 * выбранный до смены цели на «Аренду авто», не должен уехать в жалобу. */
export const formPayload = (target, form) => {
    const clean = (key) => String((form || {})[key] ?? '').trim();
    const payload = {
        target: target.code,
        reason: clean('reason'),
        driver_name: clean('driver_name'),
        driver_phone: clean('driver_phone'),
        driver_ref: clean('driver_ref') || null,
        city: clean('city'),
        description: clean('description'),
        event_at: clean('event_at') || null,
        requires_processing: willProcess(target, form?.requires_processing !== false),
    };
    if (target.unit && clean('unit_id')) payload.unit_id = Number(clean('unit_id'));
    if (target.employee && clean('employee_id')) payload.employee_id = Number(clean('employee_id'));
    return payload;
};

/* ─── Работа с сотрудником ─────────────────────────────────────────────────── */

/* Цепочка ТЗ одной лесенкой: «сотрудник определён → обратная связь →
 * тренинг → работа завершена». Шаг «тренинг» показывается, только если он
 * вообще понадобился — иначе у каждой жалобы висел бы серый «тренинг не
 * проведён», которого никто не требовал. */
export const workSteps = (complaint) => {
    if (!complaint) return [];
    const steps = [{
        key: 'employee',
        label: complaint.employee_name ? 'Сотрудник определён' : 'Сотрудник не определён',
        done: Boolean(complaint.employee_id),
        note: complaint.employee_name || null,
    }];
    if (!complaint.employee_id) {
        // Сотрудника так и не нашли, но СВ записал, почему работать не с кем, —
        // это и есть «указал, почему дополнительная работа не требуется».
        if (complaint.work_state === WORK_DONE) {
            steps.push({ key: 'done', label: 'Работа с сотрудником завершена', done: true });
        }
        return steps;
    }
    steps.push({
        key: 'feedback',
        label: complaint.feedback_done ? 'Обратная связь проведена' : 'Обратная связь не проведена',
        done: Boolean(complaint.feedback_done),
    });
    if (complaint.training_required || complaint.training_done) {
        steps.push({
            key: 'training',
            label: complaint.training_done ? 'Тренинг проведён' : 'Требуется тренинг',
            done: Boolean(complaint.training_done),
        });
    }
    steps.push({
        key: 'done',
        label: 'Работа с сотрудником завершена',
        done: complaint.work_state === WORK_DONE,
    });
    return steps;
};

/* Проверка записи о работе — та же, что у сервера (service.record_work). */
export const TIME_PATTERN = /^([01]?\d|2[0-3]):([0-5]\d)$/;

const minutes = (value) => {
    const found = TIME_PATTERN.exec(String(value || ''));
    return found ? Number(found[1]) * 60 + Number(found[2]) : null;
};

/* Что можно записать, когда сотрудник не определён: только объяснение, почему
 * работать не с кем. Тот же список, что catalog.UNASSIGNED_ACTIONS. */
export const UNASSIGNED_ACTIONS = ['review', 'no_training', 'other'];

export const availableWorkActions = (actions, complaint) => (
    complaint?.employee_id
        ? (actions || [])
        : (actions || []).filter((item) => UNASSIGNED_ACTIONS.includes(item.code)));

export const workProblems = (action, draft, { today = null } = {}) => {
    const problems = {};
    if (!action) return { action: 'Выберите, что сделано' };
    if (!String(draft?.comment || '').trim()) problems.comment = 'Опишите, что сделано';
    // «Результат» ТЗ велит видеть в тренинге — у ОС и тренинга он обязателен.
    if (action.training && !String(draft?.outcome || '').trim()) {
        problems.outcome = 'Укажите результат — он попадёт в «Тренинги»';
    }
    if (action.training) {
        const day = String(draft?.date || '');
        if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) problems.date = 'Укажите дату занятия';
        else if (today && day > today) problems.date = 'Занятие ещё не прошло';
        const start = minutes(draft?.start);
        const end = minutes(draft?.end);
        if (start === null || end === null) problems.time = 'Укажите время начала и окончания';
        else if (end <= start) problems.time = 'Окончание должно быть позже начала';
    }
    return problems;
};

export const workPayload = (action, draft) => {
    const payload = {
        action: action.code,
        comment: String(draft?.comment || '').trim(),
        outcome: String(draft?.outcome || '').trim() || null,
        need_training: Boolean(action.ask_training && draft?.need_training),
    };
    if (action.training) {
        payload.training = {
            date: draft.date, start: draft.start, end: draft.end,
            reason: draft.reason || action.default_reason,
        };
    }
    return payload;
};

/* ─── Переписка ────────────────────────────────────────────────────────────── */

export const MESSAGE_KIND_LABELS = {
    answer: 'Ответ для водителя',
    question: 'Вопрос оператору',
    operator_reply: 'Ответ оператора',
    internal: 'Внутреннее обсуждение',
    notice: 'Сообщение бота',
};

/* Ответы для водителя — отдельно и сверху карточки: это то, ради чего оператор
 * открывает жалобу, и искать их в переписке он не должен. Свежий — первым. */
export const driverAnswers = (messages) => (
    (messages || []).filter((item) => item.kind === 'answer').slice().reverse());

/* Последний вопрос группы — на него оператор и отвечает. */
export const openQuestion = (complaint, messages) => {
    if (!complaint?.question_open) return null;
    const questions = (messages || []).filter((item) => item.kind === 'question');
    return questions.length ? questions[questions.length - 1] : null;
};

/* ─── Аналитика ────────────────────────────────────────────────────────────── */

export const percent = (part, total) => (
    total ? Math.round((Number(part) || 0) * 100 / Number(total)) : 0);

/* Подпись столбика динамики по шагу, который выбрал сервер (день/неделя/месяц). */
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const MONTHS_FULL = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август',
    'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

export const bucketLabel = (iso, bucket) => {
    const found = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    if (!found) return String(iso || '');
    const [, year, month, day] = found;
    if (bucket === 'month') return `${MONTHS_FULL[Number(month) - 1]} ${year}`;
    if (bucket === 'week') return `с ${Number(day)} ${MONTHS_SHORT[Number(month) - 1]}`;
    return `${Number(day)} ${MONTHS_SHORT[Number(month) - 1]}`;
};

/* Сколько фильтров включено, кроме периода и цели — они стоят на виду, а
 * остальные спрятаны за кнопкой «Фильтры», и число на ней говорит, что
 * отбор сейчас уже, чем кажется. */
export const HIDDEN_FILTER_KEYS = ['city', 'reason', 'unit', 'employee_id', 'status', 'result',
    'confirmed', 'feedback', 'training'];

export const activeFilterCount = (filters) => (
    HIDDEN_FILTER_KEYS.filter((key) => String((filters || {})[key] ?? '').trim()).length);

export const analyticsQuery = (filters) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters || {})) {
        const text = String(value ?? '').trim();
        if (text) params.set(key, text);
    }
    return params.toString();
};

/* Повторные жалобы на сотрудника — «повторные жалобы на одного сотрудника»
 * из ТЗ: больше одной за выбранный период. */
export const isRepeated = (row) => Number(row?.total || 0) > 1;

/* ─── История жалобы ───────────────────────────────────────────────────────── */

const EVENT_TITLES = {
    created: 'Жалоба принята',
    sent: 'Отправлена в группу',
    send_failed: 'Не ушла в группу',
    answer: 'Ответ для водителя из группы',
    question: 'Вопрос оператору из группы',
    operator_reply: 'Ответ оператора в группу',
    employee: 'Сотрудник',
    result: 'Итог проверки',
    work: 'Работа с сотрудником',
};

/* Строка истории со СМЫСЛОМ события, а не только его видом. «Полная история»
 * по ТЗ — это кто был определён и кто фактически, какой итог и какая работа;
 * одна подпись «Сотрудник определён» на смену А→Б и на снятие сотрудника
 * говорила бы неправду. */
export const eventText = (event, meta = null) => {
    const payload = (event && event.payload) || {};
    const kind = event && event.kind;
    if (kind === 'employee') {
        if (payload.from && payload.to) return `Сотрудник изменён: ${payload.from} → ${payload.to}`;
        if (payload.to) return `Сотрудник определён: ${payload.to}`;
        if (payload.from) return `Сотрудник снят: ${payload.from}`;
        return 'Сотрудник не определён';
    }
    if (kind === 'result') {
        const title = ((meta && meta.results) || []).find((item) => item.code === payload.result)?.title;
        return `Итог проверки: ${title || payload.result || '—'}${payload.via === 'telegram' ? ' · в группе' : ''}`;
    }
    if (kind === 'work') {
        const title = ((meta && meta.work_actions) || []).find((item) => item.code === payload.action)?.title;
        return `${title || 'Работа с сотрудником'}${payload.closed ? ' · работа завершена' : ''}`;
    }
    if (kind === 'created' && payload.employee) {
        return `Жалоба принята · сотрудник: ${payload.employee}`;
    }
    return EVENT_TITLES[kind] || kind || '';
};
