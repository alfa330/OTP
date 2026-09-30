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

// Выбора «отправлять ли в группу» у оператора нет ни у одной цели (владелец,
// 29.09.2026). Жалоба на Яндекс сначала идёт на проверку супервайзеру и в
// группу уходит только по его решению (владелец, 30.09.2026); остальные —
// в группу сразу.
export const PROCESS_ALWAYS = 'always';
export const PROCESS_REVIEW = 'review';

// Проверка супервайзером — те же значения, что catalog.REVIEW_*.
export const REVIEW_PENDING = 'pending';
export const REVIEW_SENT = 'sent';
export const REVIEW_RESOLVED = 'resolved';

export const WORK_UNASSIGNED = 'unassigned';
export const WORK_PENDING = 'pending';
export const WORK_DONE = 'done';

/* «Зафиксирована»: в группу не уходила, на проверку не ставилась, итога и
 * сотрудника нет. Так до 30.09.2026 сохранялись жалобы на Яндекс. Та же формула,
 * что queries.RECORDED_SQL и report.is_recorded на сервере. */
export const isRecorded = (complaint) => Boolean(complaint)
    && complaint.requires_processing === false && !complaint.review_state
    && !complaint.result_code && !complaint.employee_id;

/* Статус жалобы для человека. «Отработана» про зафиксированную было бы
 * неправдой — её никто не разбирал; жалоба на проверке — «На проверке»: она
 * ещё не в группе, и «В работе» обещало бы, что группа уже занята ею. */
export const statusView = (complaint) => {
    if (!complaint) return { label: '—', tone: 'slate' };
    if (complaint.status === 'closed') {
        return isRecorded(complaint)
            ? { label: 'Зафиксирована', tone: 'slate' }
            : { label: 'Отработана', tone: 'green' };
    }
    if (complaint.review_state === REVIEW_PENDING) return { label: 'На проверке', tone: 'slate' };
    // «В работе» — штатное состояние, и красить его нечем: цвет у того, что
    // ждёт действия, а это решают бейджи (rowBadges), а не статус.
    return { label: 'В работе', tone: 'slate' };
};

/* Где жалоба сейчас — одной фразой для автора (карточка в «Обращениях»):
 * в группе, на проверке у супервайзера, решена им без группы или только
 * зафиксирована. Пустая строка — сказать нечего (в группу ещё не ушла:
 * об этом говорит красная плашка с повтором). */
export const whereabouts = (complaint) => {
    if (!complaint) return '';
    if (complaint.review_state === REVIEW_PENDING) return 'на проверке у супервайзера';
    if (complaint.review_state === REVIEW_RESOLVED) {
        return 'решена супервайзером, в группу не отправлялась';
    }
    if (isRecorded(complaint)) return 'зафиксирована для аналитики, в группу не отправлялась';
    return complaint.tg_chat_title && complaint.delivery_status === 'sent'
        ? `в группе «${complaint.tg_chat_title}»` : '';
};

/* «2026-10-02T14:00:00» → «02.10 в 14:00». Время наивное (Алматы), поэтому
 * разбираем строку, а не Date: браузер в другом поясе сдвинул бы часы. */
export const planText = (iso) => {
    const found = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(iso || ''));
    return found ? `${found[3]}.${found[2]} в ${found[4]}:${found[5]}` : '';
};

/* Назначенный тренинг, который ещё в силе: требование не снято. */
export const activePlan = (complaint) => (
    complaint && complaint.training_required && complaint.training_planned_at
        ? complaint.training_planned_at : null);

/* Бейджи строки ленты — только исключения, как в «Обращениях»: штатное «в
 * работе» у сорока строк подряд превращает ленту в светофор. Горит то, что
 * ждёт действия ЗРИТЕЛЯ. Жалоба на Яндекс, которая ждёт ЕГО проверки, —
 * по флагу сервера review_mine: тем же правилом считаются счётчик «К
 * разбору» и колокол, и бейдж не горит у главы или админа, которым жалоба
 * просто видна. */
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
    if (complaint.review_mine && complaint.review_state === REVIEW_PENDING) {
        badges.push({ key: 'review', label: 'Ждёт проверки', tone: 'amber' });
    }
    if (Number(complaint.responsible_id) === Number(viewerId)
        && complaint.work_state === WORK_PENDING) {
        const plan = activePlan(complaint);
        badges.push({
            key: 'work',
            label: plan ? `Тренинг ${planText(plan).split(' в ')[0]}`
                : complaint.training_required ? 'Нужен тренинг' : 'Ждёт работы',
            tone: 'amber',
        });
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

/* Уйдёт ли жалоба в группу СРАЗУ. Решает цель, а не оператор. Отсюда и
 * подпись кнопки: «Отправить в группу» или «Отправить на проверку» — оператор
 * должен знать, кого побеспокоит его нажатие, ДО нажатия. */
export const willProcess = (target) => Boolean(target) && target.processing === PROCESS_ALWAYS;

export const needsReview = (target) => Boolean(target) && target.processing === PROCESS_REVIEW;

export const submitLabel = (target) => {
    if (willProcess(target)) return 'Отправить в группу';
    return needsReview(target) ? 'Отправить на проверку' : 'Зафиксировать';
};

/* Типы жалобы в мастере «Обращений» (решение владельца 29.09.2026). Как у
 * тематик: то, что без группы отправить нельзя, стоит неактивным с пометкой
 * «Нет группы». Без неё работает Яндекс: он сначала идёт на проверку
 * супервайзеру, а группа понадобится, только если тот решит отправить. */
export const wizardTargets = (meta) => {
    const groupReady = Boolean(meta && meta.group && meta.group.ready);
    return ((meta && meta.targets) || []).map((item) => ({
        code: item.code,
        title: item.title,
        hint: item.hint,
        is_ready: groupReady || !willProcess(item),
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
    };
    if (target.unit && clean('unit_id')) payload.unit_id = Number(clean('unit_id'));
    if (target.employee && clean('employee_id')) payload.employee_id = Number(clean('employee_id'));
    return payload;
};

/* ─── Работа с сотрудником ─────────────────────────────────────────────────── */

/* Цепочка одной лесенкой: «сотрудник определён → тренинг → работа завершена».
 * Шаг «тренинг» показывается, только если он вообще понадобился — иначе у
 * каждой жалобы висел бы серый «тренинг не проведён», которого никто не
 * требовал. Обратная связь — только у жалоб, где её записали до 30.09.2026:
 * отдельной кнопки у неё больше нет, и серый шаг «ОС не проведена» висел бы
 * вечно. */
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
    if (complaint.feedback_done) {
        steps.push({ key: 'feedback', label: 'Обратная связь проведена', done: true });
    }
    const plan = activePlan(complaint);
    if (plan) {
        steps.push({ key: 'training', label: `Тренинг назначен на ${planText(plan)}`, done: false });
    } else if (complaint.training_required) {
        steps.push({ key: 'training', label: 'Требуется тренинг', done: false });
    } else if (complaint.training_done) {
        steps.push({ key: 'training', label: 'Тренинг проведён', done: true });
    }
    steps.push({
        key: 'done',
        label: 'Работа с сотрудником завершена',
        done: complaint.work_state === WORK_DONE,
    });
    return steps;
};

/* ─── Три кнопки работы с сотрудником ─────────────────────────────────────────
 *
 * «Назначить тренинг», «Проведён тренинг», «Приняты другие меры» (владелец,
 * 30.09.2026). Проверка — та же, что у сервера (service.record_work,
 * _parse_plan, _parse_training): расходиться им нельзя, иначе кнопка активна,
 * а сервер отказывает. */

export const ACTION_PLAN = 'training_assigned';
export const ACTION_HELD = 'training';
export const ACTION_OTHER = 'other';

export const TIME_PATTERN = /^([01]?\d|2[0-3]):([0-5]\d)$/;
const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

const minutes = (value) => {
    const found = TIME_PATTERN.exec(String(value || ''));
    return found ? Number(found[1]) * 60 + Number(found[2]) : null;
};

const pad = (value) => String(value).padStart(2, '0');

/* Сегодня и «сейчас» в виде, в каком их сравнивает форма: день ISO и минуты
 * от полуночи. Отдельной функцией — чтобы тест подставлял своё время. */
export const clockOf = (date = new Date()) => ({
    today: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    minute: date.getHours() * 60 + date.getMinutes(),
});

/* Кнопки блока — в порядке сервера (meta.work_buttons). Без сотрудника
 * тренинг не назначить и не провести: кнопки стоят, но неактивны и говорят
 * почему — «другие меры» остаются, ими объясняют, почему работать не с кем. */
export const workButtons = (meta, complaint) => (
    ((meta && meta.work_buttons) || []).map((action) => {
        const needsEmployee = action.code !== ACTION_OTHER;
        const blocked = needsEmployee && !(complaint && complaint.employee_id);
        return {
            ...action,
            disabled: blocked,
            hint: blocked ? 'Сначала определите сотрудника' : null,
        };
    }));

/* Смена в окне «Назначить тренинг» подставляет свой день, а если ещё не
 * началась — и время начала: тренинг обычно ставят на начало смены. Идущая
 * смена — это «сегодня», даже ночная, начавшаяся вчера вечером: день её
 * начала уже прошёл, и назначить на него тренинг нельзя. Галочка — у той
 * смены, которую выбрали, пока день не поменяли руками. */
export const shiftKey = (shift) => `${shift?.date}-${shift?.start}`;

export const shiftDay = (shift, clock = clockOf()) => (shift?.ongoing ? clock.today : shift?.date);

export const pickShift = (draft, shift, clock = clockOf()) => ({
    ...draft,
    shift: shiftKey(shift),
    date: shiftDay(shift, clock),
    time: shift?.ongoing ? draft?.time : shift?.start,
});

export const isPickedShift = (draft, shift, clock = clockOf()) => (
    draft?.shift === shiftKey(shift) && draft?.date === shiftDay(shift, clock));

/* Черновик окна «Проведён тренинг»: если тренинг назначали и его день
 * наступил — подставляем день и время начала, остаётся проставить конец. */
export const heldDraft = (complaint, clock = clockOf()) => {
    const found = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(String(activePlan(complaint) || ''));
    if (found && found[1] <= clock.today) return { date: found[1], start: found[2] };
    return { date: clock.today };
};

export const workProblems = (code, draft, clock = clockOf()) => {
    const problems = {};
    const day = String(draft?.date || '');
    if (code === ACTION_PLAN) {
        if (!DAY_PATTERN.test(day)) problems.date = 'Укажите день тренинга';
        else if (day < clock.today) problems.date = 'Этот день уже прошёл';
        const at = minutes(draft?.time);
        if (at === null) problems.time = 'Укажите время тренинга';
        else if (day === clock.today && at <= clock.minute) {
            problems.time = 'Это время уже прошло';
        }
    } else if (code === ACTION_HELD) {
        if (!DAY_PATTERN.test(day)) problems.date = 'Укажите дату занятия';
        else if (day > clock.today) problems.date = 'Занятие ещё не прошло';
        const start = minutes(draft?.start);
        const end = minutes(draft?.end);
        if (start === null || end === null) problems.time = 'Укажите время начала и окончания';
        else if (end <= start) problems.time = 'Окончание должно быть позже начала';
    } else if (code === ACTION_OTHER) {
        if (!String(draft?.comment || '').trim()) problems.comment = 'Опишите, что сделано';
    } else {
        problems.action = 'Выберите, что сделано';
    }
    return problems;
};

export const workPayload = (code, draft) => {
    if (code === ACTION_PLAN) return { action: code, plan: { date: draft.date, time: draft.time } };
    if (code === ACTION_HELD) {
        return { action: code, training: { date: draft.date, start: draft.start, end: draft.end } };
    }
    return { action: code, comment: String(draft?.comment || '').trim() };
};

/* Смена в окне «Назначить тренинг»: «Чт, 02.10 · 09:00–18:00». Дата — строкой
 * ISO без часового пояса, поэтому день недели считаем от полудня: так браузер
 * в любом поясе не уедет на соседний день. */
const WEEKDAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
const SHIFT_TYPES = { office_practice: 'практика в офисе', phone_shift: 'телефонная смена' };

export const shiftLabel = (shift) => {
    const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(shift?.date || ''));
    if (!found) return '';
    const weekday = WEEKDAYS[new Date(`${shift.date}T12:00:00`).getDay()];
    const parts = [`${weekday}, ${found[3]}.${found[2]}`, `${shift.start}–${shift.end}`];
    if (SHIFT_TYPES[shift.type]) parts.push(SHIFT_TYPES[shift.type]);
    return parts.join(' · ');
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
    review_sent: 'Проверена супервайзером и отправлена в группу',
    review_resolved: 'Проверена супервайзером — решено',
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
        const plan = payload.planned_at ? ` на ${planText(payload.planned_at)}` : '';
        return `${title || 'Работа с сотрудником'}${plan}${payload.closed ? ' · работа завершена' : ''}`;
    }
    if (kind === 'created' && payload.employee) {
        return `Жалоба принята · сотрудник: ${payload.employee}`;
    }
    return EVENT_TITLES[kind] || kind || '';
};
