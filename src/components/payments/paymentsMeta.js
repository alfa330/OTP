/*
 * Правила раздела «Оплата счетов», вынесенные из JSX.
 *
 * Здесь всё, что проверяется без браузера: подписи состояний, источников и
 * типов оплаты, цвет строки реестра, формат денег и дат, сборка строки истории.
 * Шаги маршрута (12 штук) приходят с сервера (`/api/payments/ping` → steps) —
 * их подписи живут в payments/workflow.py, и второй копии на фронте нет
 * намеренно: шаг — это правило процесса, а не оформление.
 *
 * Про цвет. Строка красится ТОЛЬКО там, где цвет несёт смысл: просрочка,
 * ожидание договора, закрытая и отклонённая заявка. Обычная заявка в работе не
 * красится вообще — двадцать залитых строк читались бы как тревога, а не как
 * состояние. Приём и веса — те же, что у «Посылок» и офисов в вики.
 *
 * Тесты — tests/payments_meta.test.mjs.
 */

export const STATE_META = {
    active: { label: 'В работе', tone: null },
    blocked: { label: 'Ожидает договор', tone: 'blocked' },
    overdue: { label: 'Просрочена', tone: 'overdue' },
    done: { label: 'Закрыта', tone: 'done' },
    rejected: { label: 'Отклонена', tone: 'rejected' },
    cancelled: { label: 'Отменена', tone: 'rejected' },
};

/* Полоса-легенда, она же фильтр. «Ждут меня» — не состояние заявки, а срез по
   ответственному, поэтому стоит сразу за «Все» и не имеет кружка. */
export const STATE_FILTERS = [
    { key: 'all', label: 'Все', counter: 'all', tone: null },
    { key: 'mine', label: 'Ждут меня', counter: 'mine', tone: null },
    { key: 'open', label: 'В работе', counter: 'open', tone: null },
    { key: 'blocked', label: 'Ожидают договор', counter: 'blocked', tone: 'blocked' },
    { key: 'overdue', label: 'Просрочены', counter: 'overdue', tone: 'overdue' },
    { key: 'done', label: 'Закрыты', counter: 'done', tone: 'done' },
    { key: 'rejected', label: 'Отклонены', counter: 'rejected', tone: 'rejected' },
];

export const SOURCE_META = {
    too: { label: 'ТОО' },
    wallet: { label: 'Кошелёк' },
    cash: { label: 'Наличные' },
};
export const SOURCE_OPTIONS = Object.entries(SOURCE_META).map(([value, meta]) => ({ value, label: meta.label }));

export const TYPE_META = {
    one_time: { label: 'Разовый' },
    monthly: { label: 'Ежемесячный' },
    fixed: { label: 'Фиксированный' },
};
export const TYPE_OPTIONS = Object.entries(TYPE_META).map(([value, meta]) => ({ value, label: meta.label }));

export const ROLE_LABELS = {
    initiator: 'Инициатор',
    manager: 'Руководитель инициатора',
    founder: 'Учредитель',
    accounting: 'Бухгалтерия',
};

/* Роли, у которых есть список участников (вкладка «Участники»). Инициатор и
   его руководитель — люди конкретной заявки, а не роли со списком. */
export const MEMBER_ROLES = [
    { code: 'founder', label: 'Учредитель', hint: 'Итоговое подтверждение закупа (шаг 3) и счёта (шаг 9)' },
    { code: 'accounting', label: 'Бухгалтерия', hint: 'Реквизиты, проверка счёта, оплата, закрытие (шаги 5, 8, 10, 12)' },
    { code: 'development_director', label: 'Директор по развитию', hint: 'Согласует счёт вместо Учредителя только по Приказу — форма Приказа подставляет его «кому передаётся право»', optional: true },
];

export const ATTACHMENT_LABELS = {
    supplier_registry: 'Реестр поставщиков',
    offer: 'Коммерческое предложение',
    invoice: 'Счёт на оплату',
    payment_order: 'Платёжное поручение',
    power_of_attorney: 'Доверенность',
    act: 'АВР / накладная',
    contract: 'Договор',
    other: 'Другое',
};

export const CONTRACT_STATUS_META = {
    active: { label: 'Действующий', tone: 'done' },
    terminated: { label: 'Расторгнут', tone: 'rejected' },
    cancelled: { label: 'Отменён', tone: 'rejected' },
    archived: { label: 'Архивный', tone: 'rejected' },
    inactive: { label: 'Недействующий', tone: 'rejected' },
};
export const CONTRACT_STATUS_OPTIONS = Object.entries(CONTRACT_STATUS_META)
    .map(([value, meta]) => ({ value, label: meta.label }));

export const ORDER_STATUS_META = {
    active: { label: 'Действующий', tone: 'done' },
    cancelled: { label: 'Отменён', tone: 'rejected' },
};

export const PERIOD_META = {
    monthly: { label: 'Ежемесячно' },
    quarterly: { label: 'Ежеквартально' },
    yearly: { label: 'Ежегодно' },
    custom: { label: 'Свой интервал' },
};
export const PERIOD_OPTIONS = Object.entries(PERIOD_META).map(([value, meta]) => ({ value, label: meta.label }));

export const PARTY_KIND_OPTIONS = [
    { value: 'too', label: 'ТОО' },
    { value: 'ip', label: 'ИП' },
    { value: 'other', label: 'Другое' },
];

export const PHASE_LABELS = {
    purchase: 'Согласование закупки',
    requisites: 'Реквизиты',
    invoice: 'Счёт',
    payment: 'Оплата',
    closing: 'Закрытие',
};

export const TOTAL_STEPS = 12;

// Порог договора — тот же, что CONTRACT_REQUIRED_OVER на сервере; сервер отдаёт
// его в /ping, здесь значение по умолчанию до ответа.
export const CONTRACT_THRESHOLD = 300000;

/* ── Оттенок строки ────────────────────────────────────────────────────────── */

export const ROW_TONES = ['overdue', 'blocked', 'done', 'rejected'];

export const TONE_ROW = {
    overdue: 'bg-rose-50/80',
    blocked: 'bg-amber-50/80',
    done: 'bg-emerald-50/60',
    rejected: 'bg-slate-50',
};
export const TONE_EDGE = {
    overdue: 'before:bg-rose-400',
    blocked: 'before:bg-amber-400',
    done: 'before:bg-emerald-400',
    rejected: 'before:bg-slate-300',
};
export const TONE_PILL = {
    overdue: { fill: 'bg-rose-100 text-rose-800', dot: 'bg-rose-500' },
    blocked: { fill: 'bg-amber-100 text-amber-800', dot: 'bg-amber-500' },
    done: { fill: 'bg-emerald-100 text-emerald-800', dot: 'bg-emerald-500' },
    rejected: { fill: 'bg-slate-200 text-slate-600', dot: 'bg-slate-400' },
    neutral: { fill: 'bg-slate-100 text-slate-700', dot: 'bg-slate-400' },
    current: { fill: 'bg-blue-50 text-blue-700 ring-1 ring-blue-100', dot: 'bg-blue-500' },
};
export const TONE_TEXT = {
    overdue: { main: 'text-rose-950', body: 'text-rose-900/80', meta: 'text-rose-700/70' },
    blocked: { main: 'text-amber-950', body: 'text-amber-900/80', meta: 'text-amber-700/70' },
    done: { main: 'text-emerald-950', body: 'text-emerald-900/75', meta: 'text-emerald-700/70' },
    rejected: { main: 'text-slate-500', body: 'text-slate-400', meta: 'text-slate-400' },
    neutral: { main: 'text-slate-900', body: 'text-slate-600', meta: 'text-slate-500' },
};

export const requestState = (request) => {
    if (!request) return 'active';
    if (STATE_META[request.state]) return request.state;
    if (request.status && request.status !== 'active') return request.status;
    return 'active';
};
export const stateMeta = (state) => STATE_META[state] || STATE_META.active;
export const rowTone = (request) => stateMeta(requestState(request)).tone;
export const toneRow = (tone) => (tone && TONE_ROW[tone]) || '';
export const toneEdge = (tone) => (tone && TONE_EDGE[tone]) || 'before:bg-transparent';
export const tonePill = (tone) => TONE_PILL[tone] || TONE_PILL.neutral;
export const toneText = (tone) => TONE_TEXT[tone] || TONE_TEXT.neutral;

export const isClosed = (request) => ['done', 'rejected', 'cancelled'].includes(request?.status);

/* ── Деньги и даты ─────────────────────────────────────────────────────────── */

const NBSP = ' ';

export const parseAmount = (value) => {
    if (value === null || value === undefined || value === '') return 0;
    if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
    const text = String(value).replace(/[\s  ₸]/g, '').replace(/тг/gi, '').replace(',', '.');
    const number = Number(text);
    return Number.isFinite(number) ? number : 0;
};

export const fmtMoney = (value, { currency = true, cents = 'auto' } = {}) => {
    const number = parseAmount(value);
    const negative = number < 0;
    const abs = Math.abs(number);
    const whole = Math.floor(abs + 1e-9);
    const rest = Math.round((abs - whole) * 100);
    let text = String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
    if (cents === 'always' || (cents === 'auto' && rest > 0)) text += `,${String(rest).padStart(2, '0')}`;
    return `${negative ? '−' : ''}${text}${currency ? `${NBSP}₸` : ''}`;
};

export const itemTotal = (item) => parseAmount(item?.quantity || 1) * parseAmount(item?.unit_price);
export const itemsTotal = (items) => Math.round((items || []).reduce((sum, item) => sum + itemTotal(item), 0) * 100) / 100;

export const fmtQty = (value) => {
    const number = parseAmount(value);
    if (!number) return '0';
    return Number.isInteger(number) ? String(number) : String(number).replace('.', ',');
};

export const dateParts = (value) => {
    if (!value) return null;
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
    if (!match) return null;
    return { y: match[1], m: match[2], d: match[3], hh: match[4], mm: match[5] };
};

export const fmtDate = (value) => {
    const parts = dateParts(value);
    return parts ? `${parts.d}.${parts.m}.${parts.y}` : '—';
};

export const fmtDateShort = (value) => {
    const parts = dateParts(value);
    return parts ? `${parts.d}.${parts.m}` : '—';
};

export const fmtDateTime = (value) => {
    const parts = dateParts(value);
    if (!parts) return '—';
    return parts.hh ? `${parts.d}.${parts.m}.${parts.y} ${parts.hh}:${parts.mm}` : `${parts.d}.${parts.m}.${parts.y}`;
};

/* «Сегодня» по Алматы, а не по часам браузера: срок оплаты — рабочий день в Казахстане. */
export const todayISO = () => {
    const now = new Date(Date.now() + 5 * 60 * 60 * 1000);
    return now.toISOString().slice(0, 10);
};

export const daysUntil = (iso, today = todayISO()) => {
    const a = dateParts(iso);
    const b = dateParts(today);
    if (!a || !b) return null;
    const ms = Date.UTC(+a.y, +a.m - 1, +a.d) - Date.UTC(+b.y, +b.m - 1, +b.d);
    return Math.round(ms / 86400000);
};

export const pluralDays = (count) => {
    const abs = Math.abs(count);
    const mod10 = abs % 10;
    const mod100 = abs % 100;
    if (mod10 === 1 && mod100 !== 11) return `${abs} день`;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return `${abs} дня`;
    return `${abs} дней`;
};

export const dueLabel = (request, today = todayISO()) => {
    if (!request?.due_on) return '';
    const days = daysUntil(request.due_on, today);
    if (days === null) return fmtDate(request.due_on);
    if (isClosed(request) || Number(request.current_step) > 10) return `к ${fmtDate(request.due_on)}`;
    if (days < 0) return `просрочен на ${pluralDays(days)}`;
    if (days === 0) return 'сегодня';
    return `через ${pluralDays(days)}`;
};

/* ── Маршрут ───────────────────────────────────────────────────────────────── */

export const stepProgress = (request) => {
    const step = Number(request?.current_step) || 0;
    if (request?.status === 'done') return { done: TOTAL_STEPS, total: TOTAL_STEPS };
    return { done: Math.max(0, step - 1), total: TOTAL_STEPS };
};

export const stepLabel = (request, steps = []) => {
    if (!request) return '';
    if (request.status === 'done') return 'Все 12 шагов пройдены';
    if (request.status === 'rejected') return `Отклонена на шаге ${request.current_step}`;
    if (request.status === 'cancelled') return `Отменена на шаге ${request.current_step}`;
    const step = steps.find((item) => Number(item.no ?? item.step_no) === Number(request.current_step));
    return step ? step.title : `Шаг ${request.current_step}`;
};

export const responsibleLabel = (request) => request?.current_assignee_name
    || ROLE_LABELS[request?.current_role] || '—';

export const routeSummary = (basis) => {
    if (!basis) return null;
    if (basis.approver_name) {
        return {
            approver: basis.approver_name,
            basisText: `По Приказу №${basis.order_number || '—'} вместо Учредителя`,
            byOrder: true,
        };
    }
    const reasons = (basis.evaluations || []).map((item) => item.reason).filter(Boolean);
    return { approver: basis.standard_label || 'Учредитель', basisText: reasons.join(' ') || 'Стандартный маршрут', byOrder: false };
};

/* ── История ───────────────────────────────────────────────────────────────── */

const FIELD_LABELS = {
    expense_name: 'Наименование расхода',
    project_id: 'Проект',
    branch: 'Филиал / регион',
    category_id: 'Категория',
    subcategory_id: 'Подкатегория',
    counterparty_id: 'Контрагент',
    legal_entity_id: 'Юр. лицо',
    contract_id: 'Договор',
    amount: 'Сумма',
    items: 'Позиции',
    payment_period: 'Период оплаты',
    payment_source: 'Источник оплаты',
    payment_type: 'Тип оплаты',
    card_number: 'Номер карты',
    notes: 'Примечания',
    due_on: 'Срок оплаты',
    invoice_requisites: 'Реквизиты',
    invoice_number: 'Номер счёта',
    invoice_date: 'Дата счёта',
    invoice_description: 'Описание счёта',
    needs_power_of_attorney: 'Доверенность',
    needs_payment_order: 'Платёжное поручение',
    previous_payment_note: 'Проверка бухгалтерии',
    paid_on: 'Дата оплаты',
    paid_amount: 'Оплачено',
    refund_on: 'Дата возврата',
    refund_amount: 'Сумма возврата',
    manager_id: 'Руководитель',
    department_id: 'Отдел',
};

export const describeEvent = (event, steps = []) => {
    if (!event) return '';
    const stepTitle = (no) => {
        const step = steps.find((item) => Number(item.no ?? item.step_no) === Number(no));
        return step ? `«${step.title}»` : `${no}`;
    };
    const payload = event.payload || {};
    switch (event.kind) {
        case 'created': return 'Заявка создана';
        case 'generated': return 'Создана календарём фиксированных платежей';
        case 'step_done': return `Шаг ${event.step_no} ${stepTitle(event.step_no)} — отписка`;
        case 'step_skipped': return `Шаг ${event.step_no} пропущен`;
        case 'returned': return `Возвращена на шаг ${event.step_no} ${stepTitle(event.step_no)}`;
        case 'rejected': return 'Заявка отклонена';
        case 'cancelled': return 'Заявка отменена';
        case 'blocked': return 'Счёт остановлен: нужен действующий договор';
        case 'unblocked': return 'Ограничение по договору снято';
        case 'route': return 'Определён согласующий счёта';
        case 'reassigned': return payload.assignee_name
            ? `Шаг ${event.step_no}: ответственный — ${payload.assignee_name}`
            : `Шаг ${event.step_no}: ответственный возвращён роли`;
        case 'attachment_added': return `Файл добавлен: ${payload.file_name || ''}`.trim();
        case 'attachment_removed': return `Файл снят: ${payload.file_name || ''}`.trim();
        case 'refund': return payload.refund_amount
            ? `Отмечен возврат ${fmtMoney(payload.refund_amount)}`
            : 'Возврат снят';
        case 'edited': {
            const keys = Object.keys(payload.changes || {});
            const labels = keys.map((key) => FIELD_LABELS[key] || key);
            return labels.length ? `Изменено: ${labels.join(', ')}` : 'Заявка изменена';
        }
        case 'comment': return 'Комментарий';
        default: return event.kind || 'Событие';
    }
};

/* Виды файлов, уместные на шаге; вне известного шага — полный список. */
export const attachmentKindsForStep = (step) => {
    const kinds = step?.files && step.files.length ? step.files : Object.keys(ATTACHMENT_LABELS);
    const list = kinds.includes('other') ? kinds : [...kinds, 'other'];
    return list.map((value) => ({ value, label: ATTACHMENT_LABELS[value] || value }));
};

export const fileSizeLabel = (bytes) => {
    const size = Number(bytes) || 0;
    if (size < 1024) return `${size} Б`;
    if (size < 1024 * 1024) return `${Math.round(size / 1024)} КБ`;
    return `${(size / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`;
};

export const cardNumberLabel = (digits) => {
    const text = String(digits || '').replace(/\D/g, '');
    if (!text) return '';
    return text.replace(/(\d{4})(?=\d)/g, '$1 ');
};

export const exportFileName = (today = todayISO()) => `Заявки на оплату ${today}.xlsx`;
