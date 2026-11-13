/*
 * Раздел «Почта» (#417): подписи, тона, правила формы и обращение к API — без React.
 *
 * Коды и подписи — те же, что в mail/rules.py; тест tests/test_mail_rules.py сверяет их
 * буквально: разойдись подпись, человек увидел бы на экране одно слово, а в Excel другое.
 */
import axios from 'axios';

export const STATUS_LABELS = {
    draft: 'Черновик',
    ready: 'Готово к отправке',
    courier_requested: 'Заявка курьеру',
    handed_over: 'Передано курьеру',
    in_transit: 'В пути',
    delivered: 'Доставлено курьером',
    accepted: 'Принято и сверено',
    cancelled: 'Отменено',
    not_delivered: 'Не удалось вручить',
    problem: 'Проблема при получении',
    return_initiated: 'Возврат инициирован',
    returned: 'Возвращено',
};

/* Цвет — только со смыслом (TonePill «Оплаты счетов»): синий — ждёт шага отправителя,
   янтарный — ждёт получателя или координатора, красный — беда, зелёный — закрыто сверкой.
   Едущее в срок и черновик не красим. */
const STATUS_TONES = {
    draft: 'neutral',
    ready: 'current',
    courier_requested: 'current',
    handed_over: 'neutral',
    in_transit: 'neutral',
    delivered: 'blocked',
    accepted: 'done',
    cancelled: 'rejected',
    not_delivered: 'blocked',
    problem: 'overdue',
    return_initiated: 'blocked',
    returned: 'rejected',
};

export const statusTone = (shipment) => (shipment?.overdue ? 'overdue' : (STATUS_TONES[shipment?.status] || 'neutral'));
export const statusLabel = (code) => STATUS_LABELS[code] || code || '';

export const DOC_TYPE_LABELS = {
    avr: 'АВР',
    avr_return: 'АВР возвратный',
    contract: 'Договор',
    power_of_attorney: 'Доверенность',
    other: 'Иное',
};
export const DOC_TYPE_OPTIONS = Object.entries(DOC_TYPE_LABELS).map(([value, label]) => ({ value, label }));
export const COPY_KIND_LABELS = { original: 'Оригинал', copy: 'Копия' };
export const DELIVERY_TYPE_LABELS = { standard: 'Стандарт', economy: 'Эконом' };
export const URGENCY_LABELS = { normal: 'Обычная', urgent: 'Срочная' };
export const LINE_STATUS_LABELS = {
    ok: 'Совпадает',
    shortage: 'Недостача',
    excess: 'Лишнее',
    damaged: 'Повреждение',
    wrong: 'Не тот документ',
};
export const DISCREPANCY_OPTIONS = ['shortage', 'excess', 'damaged', 'wrong']
    .map((value) => ({ value, label: LINE_STATUS_LABELS[value] }));
export const FILE_KIND_LABELS = {
    waybill: 'Накладная',
    handover: 'Подтверждение передачи',
    delivery: 'Подтверждение доставки',
    problem: 'Фото или акт расхождения',
    other: 'Другое',
};
export const EVENT_LABELS = {
    created: 'Создано',
    edited: 'Изменено',
    status: 'Смена статуса',
    pickup_failed: 'Забор не состоялся',
    file: 'Добавлен файл',
};
export const FIELD_LABELS = {
    sender_office_id: 'Офис отправки', recipient_office_id: 'Офис получения',
    sender_name: 'Отправитель', sender_phone: 'Телефон отправителя',
    recipient_name: 'Получатель', recipient_phone: 'Телефон получателя',
    recipient_address: 'Адрес получателя', recipient_postcode: 'Индекс',
    payer_entity_id: 'Юрлицо-плательщик', delivery_type: 'Тип доставки', urgency: 'Срочность',
    places: 'Мест', weight_kg: 'Вес, кг', volume_weight_kg: 'Объёмный вес, кг',
    declared_value: 'Объявленная ценность', comment: 'Комментарий',
    track_number: 'Трек-номер', handed_at: 'Передано курьеру', due_on: 'Крайний срок',
};

/* Роли ТЗ п. 11. «Участники» выдают только сотрудника офиса (решение владельца 13.11.2026);
   подписи остальных нужны, лишь если такая строка заведена в базе напрямую. */
export const ROLE_LABELS = {
    office: 'Сотрудник офиса',
    coordinator: 'Координатор почты',
    regional: 'Региональный руководитель',
    central: 'Центральный координатор',
    accounting: 'Бухгалтерия',
    admin: 'Администратор',
};

export const ACTION_LABELS = {
    ready: 'Готово к отправке',
    courier_request: 'Заявка курьеру',
    pickup_failed: 'Забор не состоялся',
    hand_over: 'Передано курьеру',
    in_transit: 'В пути',
    deliver: 'Доставлено курьером',
    not_delivered: 'Не удалось вручить',
    reconcile: 'Сверить',
    resolve: 'Проблема решена',
    start_return: 'Начать возврат',
    returned: 'Возвращено',
    cancel: 'Отменить',
};
/* Где шаг на карточке: главный из доступных — синей кнопкой; частые — рядом, спокойнее; редкие и
   необратимые — в меню «⋯». Каждый шаг сервера обязан попасть хотя бы в один список — иначе его
   нечем нажать (так пропала «Заявка курьеру»); сторожит tests/mail_meta.test.mjs. */
export const PRIMARY_ORDER = ['reconcile', 'hand_over', 'ready', 'deliver', 'resolve', 'returned'];
export const SECONDARY_ACTIONS = ['courier_request', 'deliver', 'in_transit', 'pickup_failed'];
export const MENU_ACTIONS = ['not_delivered', 'start_return', 'cancel'];
export const primaryAction = (actions = []) => PRIMARY_ORDER.find((code) => actions.includes(code)) || null;
export const actionLayout = (actions = []) => {
    const primary = primaryAction(actions);
    return {
        primary,
        secondary: SECONDARY_ACTIONS.filter((code) => actions.includes(code) && code !== primary),
        menu: MENU_ACTIONS.filter((code) => actions.includes(code) && code !== primary),
    };
};
/* Шаги без формы: подтверждаются сразу. */
export const INSTANT_ACTIONS = ['ready', 'in_transit'];
export const NOTE_REQUIRED = {
    pickup_failed: 'Укажите причину',
    not_delivered: 'Укажите причину',
    resolve: 'Опишите, как решили',
    start_return: 'Укажите причину возврата',
    cancel: 'Укажите причину отмены',
};

export const QUICK_FILTERS = [
    { value: 'to_send', label: 'Требуют отправки' },
    { value: 'in_transit', label: 'В пути' },
    { value: 'to_reconcile', label: 'Ожидают сверки' },
    { value: 'overdue', label: 'Просрочено' },
    { value: 'problems', label: 'Проблемы' },
];
/* Отбор реестра по дате события (ТЗ п. 9: «по периоду документа и событиям отдельно»). */
export const EVENT_FILTERS = [
    { value: 'created', label: 'Создано' },
    { value: 'handed', label: 'Передано курьеру' },
    { value: 'delivered', label: 'Вручено' },
    { value: 'reconciled', label: 'Сверено' },
];
export const STATUS_OPTIONS = [{ value: '', label: 'Все статусы' }]
    .concat(Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label })));

/* Задан ли срок эконом-доставки хоть у одной зоны (сервер: rules.economy_ready). */
export const economyReady = (zones = []) => (zones || []).some((zone) => zone.eco_max_days !== null && zone.eco_max_days !== undefined);
export const ECONOMY_MISSING = 'Сроки эконома не заданы в «Справочниках»';

/* Плитка «Обзора» → тон числа: красное — просрочено, янтарное — проблемы (как в макете). */
export const QUICK_TONE = { overdue: 'text-rose-600', problems: 'text-amber-600' };

export const TABS = {
    overview: { title: 'Обзор', subtitle: 'Сроки, просрочки и что ждёт вас' },
    outgoing: { title: 'Исходящие', subtitle: 'Что ушло и уходит из вашего офиса', scope: 'outgoing' },
    incoming: { title: 'Входящие', subtitle: 'Что едет в ваш офис и ждёт сверки', scope: 'incoming' },
    registry: { title: 'Общий реестр', subtitle: 'Все отправления в вашем охвате', scope: 'registry' },
    problems: { title: 'Проблемы и возвраты', subtitle: 'Расхождения, невручения, возвраты и просрочки', scope: 'problems' },
    participants: { title: 'Участники', subtitle: 'Сотрудники офисов, которые отправляют и принимают почту' },
    settings: { title: 'Справочники и настройки', subtitle: 'Зоны договора и сроки доставки по маршрутам' },
};

export const ATTENTION_LABELS = {
    to_reconcile: 'Сверить входящие',
    to_send: 'Передать курьеру',
    drafts: 'Ваши черновики',
    overdue: 'Просрочено',
    problems: 'Проблемы',
};

/* ── Числа и даты ──────────────────────────────────────────────────────────── */

const pad = (value) => String(value).padStart(2, '0');

export const dateText = (iso) => {
    if (!iso) return '';
    const [year, month, day] = String(iso).slice(0, 10).split('-');
    return day && month && year ? `${day}.${month}.${year}` : String(iso);
};

/* Время раздела — по Алматы без пояса: дату и время берём из строки, а не через Date. */
export const dateTimeText = (iso) => {
    if (!iso) return '';
    const [day, time] = String(iso).split('T');
    return `${dateText(day)}${time ? ` ${time.slice(0, 5)}` : ''}`;
};

export function plural(count, forms) {
    const number = Math.abs(Math.trunc(Number(count) || 0));
    if (number % 10 === 1 && number % 100 !== 11) return forms[0];
    if (number % 10 >= 2 && number % 10 <= 4 && !(number % 100 >= 12 && number % 100 <= 14)) return forms[1];
    return forms[2];
}

const WORK_DAYS = ['рабочий день', 'рабочих дня', 'рабочих дней'];

/* Состояние срока (сервер: rules.deadline_state) словами. */
export const deadlineText = (deadline, dueOn) => {
    if (!deadline) return dueOn ? `до ${dateText(dueOn)}` : '';
    const days = Math.abs(Number(deadline.days) || 0);
    if (deadline.kind === 'overdue') {
        return days ? `просрочено на ${days} ${plural(days, WORK_DAYS)}` : `просрочено, срок был ${dateText(dueOn)}`;
    }
    if (deadline.kind === 'today') return 'срок сегодня';
    if (deadline.kind === 'soon') return `срок ${dateText(dueOn)} — следующий рабочий день`;
    return `до ${dateText(dueOn)} · ещё ${days} ${plural(days, WORK_DAYS)}`;
};

export const deadlineTone = (deadline) => {
    if (!deadline) return 'normal';
    if (deadline.kind === 'overdue') return 'overdue';
    if (deadline.kind === 'today' || deadline.kind === 'soon') return 'soon';
    return 'normal';
};

/* Колонка «Срок»: едет — состояние срока; уже вручено — в срок или позже (срок дальше не идёт,
   и «до 11.11» у закрытого отправления был бы шумом); до передачи курьеру срока нет вовсе. */
export const deadlineCell = (shipment) => {
    if (!shipment) return null;
    if (shipment.deadline) {
        return { text: deadlineText(shipment.deadline, shipment.due_on), tone: deadlineTone(shipment.deadline) };
    }
    if (shipment.delivered_on && shipment.due_on) {
        const late = String(shipment.delivered_on) > String(shipment.due_on);
        return { text: `вручено ${dateText(shipment.delivered_on)} · ${late ? 'позже срока' : 'в срок'}`,
            tone: late ? 'soon' : 'normal' };
    }
    return null;
};

export const routeText = (shipment) => `${shipment?.sender_city || '—'} → ${shipment?.recipient_city || '—'}`;

export const slaText = (shipment) => {
    const high = shipment?.sla_max_days;
    if (high === null || high === undefined) return '';
    const low = shipment?.sla_min_days;
    const range = low === null || low === undefined || low === high ? `${high}` : `${low}–${high}`;
    return `${range} ${plural(high, WORK_DAYS)}`;
};

/* «Алматы · Центральный»; офис, названный по городу, — без повтора: «Павлодар», а не «Павлодар · Павлодар». */
export const officeLabel = (office) => {
    if (!office) return '';
    if (!office.city || String(office.name || '').toLowerCase().includes(String(office.city).toLowerCase())) {
        return office.name || office.city || '';
    }
    return `${office.city} · ${office.name}`;
};

/* «Центральный, Алматы»; офис, названный по городу, — без повтора: «Павлодар», а не «Павлодар, Павлодар». */
export const officeLine = (name, city) => {
    if (!name) return city || '';
    if (!city || String(name).toLowerCase().includes(String(city).toLowerCase())) return name;
    return `${name}, ${city}`;
};

/* Офисы для выбора: по городу, город — подписью группы в строке. */
export const officeOptions = (offices = [], allowedIds = null) => (offices || [])
    .filter((office) => !allowedIds || allowedIds.includes(office.id))
    .map((office) => ({ value: office.id, label: officeLabel(office) }));

export const fileSizeLabel = (bytes) => {
    const value = Number(bytes) || 0;
    if (value < 1024) return `${value} Б`;
    if (value < 1024 * 1024) return `${Math.round(value / 1024)} КБ`;
    return `${(value / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`;
};

/* ── Форма отправления ─────────────────────────────────────────────────────── */

let itemSeed = 0;
export const blankItem = (docType = 'avr') => {
    itemSeed += 1;
    return {
        key: `item-${itemSeed}`,
        doc_type: docType, other_description: '', period_from: '', period_to: '', qty: '1',
        copy_kind: 'original', doc_number: '', is_confidential: false, showNumber: false,
    };
};

export const blankForm = (context) => {
    const me = context?.me || {};
    return {
        sender_office_id: context?.default_sender_office_id ?? null,
        recipient_office_id: null,
        sender_name: me.name || '',
        sender_phone: me.phone || '',
        recipient_name: '',
        recipient_phone: '',
        recipient_address: '',
        recipient_postcode: '',
        payer_entity_id: (context?.legal_entities || []).length === 1 ? context.legal_entities[0].id : null,
        delivery_type: 'standard',
        urgency: 'normal',
        places: '1',
        weight_kg: '',
        volume_weight_kg: '',
        declared_value: '',
        comment: '',
        items: [blankItem()],
    };
};

const str = (value) => (value === null || value === undefined ? '' : String(value));

export const shipmentToForm = (card) => {
    const s = card?.shipment || {};
    return {
        sender_office_id: s.sender_office_id ?? null,
        recipient_office_id: s.recipient_office_id ?? null,
        sender_name: str(s.sender_name),
        sender_phone: str(s.sender_phone),
        recipient_name: str(s.recipient_name),
        recipient_phone: str(s.recipient_phone),
        recipient_address: str(s.recipient_address),
        recipient_postcode: str(s.recipient_postcode),
        payer_entity_id: s.payer_entity_id ?? null,
        delivery_type: s.delivery_type || 'standard',
        urgency: s.urgency || 'normal',
        places: str(s.places || 1),
        weight_kg: str(s.weight_kg).replace('.', ','),
        volume_weight_kg: str(s.volume_weight_kg).replace('.', ','),
        declared_value: str(s.declared_value).replace('.', ','),
        comment: str(s.comment),
        items: (card?.items || []).map((item) => ({
            ...blankItem(item.doc_type),
            other_description: str(item.other_description),
            period_from: str(item.period_from),
            period_to: str(item.period_to),
            qty: str(item.qty),
            copy_kind: item.copy_kind || 'original',
            doc_number: str(item.doc_number),
            is_confidential: Boolean(item.is_confidential),
            showNumber: Boolean(item.doc_number),
        })),
    };
};

const numberOrNull = (value) => {
    const text = String(value ?? '').replace(/\s/g, '').replace(',', '.');
    return text === '' ? null : text;
};

export const formToPayload = (form) => ({
    sender_office_id: form.sender_office_id,
    recipient_office_id: form.recipient_office_id,
    sender_name: form.sender_name,
    sender_phone: form.sender_phone,
    recipient_name: form.recipient_name,
    recipient_phone: form.recipient_phone,
    recipient_address: form.recipient_address,
    recipient_postcode: form.recipient_postcode,
    payer_entity_id: form.payer_entity_id,
    delivery_type: form.delivery_type,
    urgency: form.urgency,
    places: numberOrNull(form.places),
    weight_kg: numberOrNull(form.weight_kg),
    volume_weight_kg: numberOrNull(form.volume_weight_kg),
    declared_value: numberOrNull(form.declared_value),
    comment: form.comment,
    items: (form.items || []).map((item) => ({
        doc_type: item.doc_type,
        other_description: item.doc_type === 'other' ? item.other_description : null,
        period_from: item.period_from || null,
        period_to: item.period_to || null,
        qty: numberOrNull(item.qty),
        copy_kind: item.copy_kind,
        doc_number: item.doc_number,
        is_confidential: Boolean(item.is_confidential),
    })),
});

const filled = (value) => String(value ?? '').trim() !== '';
const positive = (value) => Number(String(value ?? '').replace(/\s/g, '').replace(',', '.')) > 0;

/*
 * Чего не хватает — пункты с ключом поля (как в «Оплате счетов»): поле краснеет, стрелки
 * «Не хватает: N ⌃ ⌄» водят по ним. `ready` — проверка «Готово к отправке» (ТЗ п. 3);
 * без неё — проверка черновика: «Иное» без описания не сохраняется даже черновиком.
 * Тексты — те же, что у сервера (rules.missing_for_ready, rules.clean_item).
 */
export const formIssues = (form, { ready = false, economyAllowed = true } = {}) => {
    const issues = [];
    // «Эконом» без заданных сроков застрял бы на «Передано курьеру», когда пакет уже у курьера.
    if (ready && form?.delivery_type === 'economy' && !economyAllowed) {
        issues.push({ key: 'delivery_type', text: ECONOMY_MISSING });
    }
    (form?.items || []).forEach((item, index) => {
        if (!item.doc_type) issues.push({ key: `items.${index}.doc_type`, text: 'Выберите тип документа' });
        if (item.doc_type === 'other' && !filled(item.other_description)) {
            issues.push({ key: `items.${index}.other_description`, text: 'Укажите, что именно отправляется' });
        }
        if (!positive(item.qty)) issues.push({ key: `items.${index}.qty`, text: 'Укажите количество' });
        if (item.period_from && item.period_to && item.period_from > item.period_to) {
            issues.push({ key: `items.${index}.period_to`, text: 'Конец периода раньше начала' });
        }
    });
    if (!form?.sender_office_id) issues.push({ key: 'sender_office_id', text: 'Выберите офис отправки' });
    if (form?.sender_office_id && form.sender_office_id === form.recipient_office_id) {
        issues.push({ key: 'recipient_office_id', text: 'Офис получения совпадает с офисом отправки' });
    }
    if (!ready) return issues;
    if (!form.recipient_office_id) issues.push({ key: 'recipient_office_id', text: 'Выберите офис получения' });
    if (!filled(form.recipient_name)) issues.push({ key: 'recipient_name', text: 'Укажите ФИО получателя' });
    if (!filled(form.recipient_phone)) issues.push({ key: 'recipient_phone', text: 'Укажите телефон получателя' });
    if (!filled(form.recipient_address)) issues.push({ key: 'recipient_address', text: 'Укажите адрес получателя' });
    if (!filled(form.sender_name)) issues.push({ key: 'sender_name', text: 'Укажите ФИО отправителя' });
    if (!filled(form.sender_phone)) issues.push({ key: 'sender_phone', text: 'Укажите телефон отправителя' });
    if (!form.payer_entity_id) issues.push({ key: 'payer_entity_id', text: 'Выберите юрлицо-плательщика' });
    if (!positive(form.weight_kg)) issues.push({ key: 'weight_kg', text: 'Укажите вес' });
    if (!(form.items || []).length) issues.push({ key: 'items', text: 'Добавьте хотя бы одну строку содержимого' });
    return issues;
};

/* Поля формы сверху вниз — порядок стрелок «Не хватает». */
export const formOrder = (form) => [
    'sender_office_id', 'sender_name', 'sender_phone', 'recipient_office_id', 'recipient_name', 'recipient_phone',
    'recipient_address', 'items',
    ...(form?.items || []).flatMap((_item, index) => ['doc_type', 'other_description', 'period_to', 'qty']
        .map((name) => `items.${index}.${name}`)),
    'delivery_type', 'weight_kg', 'payer_entity_id', 'track_number', 'handed_at',
];

/* Почему строки содержимого в правке закрыты — словами, по тому, что с отправлением на деле. */
export const itemsLockText = (shipment) => {
    if (shipment?.reconciled_at) return 'после сверки не меняется';
    if (shipment?.status === 'delivered') return 'доставлено — ждёт сверки, не меняется';
    if (['accepted', 'cancelled', 'returned', 'problem', 'return_initiated'].includes(shipment?.status)) {
        return 'отправление закрыто — не меняется';
    }
    return 'меняет сторона отправки';
};

/* Ответ сервера с полями ошибок → те же пункты (сервер проверяет и то, чего форма не видит:
   офис не из справочника, юрлицо закрыто). */
export const serverIssues = (error) => {
    const fields = error?.response?.data?.fields;
    if (!fields || typeof fields !== 'object') return [];
    return Object.entries(fields).map(([key, text]) => ({ key, text: String(text) }));
};

/* Заполнили адрес офиса получения сами, если человек его ещё не трогал. */
export const recipientDefaults = (form, office) => {
    if (!office) return form;
    const next = { ...form };
    if (!filled(form.recipient_address) || form.recipient_address === form.autoAddress) {
        next.recipient_address = office.address || '';
        next.autoAddress = office.address || '';
    }
    return next;
};

/* ── Сверка ────────────────────────────────────────────────────────────────── */

export const blankLines = (items = []) => items.map((item) => ({
    id: item.id, verdict: null, line_status: 'shortage', received_qty: '', line_note: '',
}));

export const reconcileIssues = (lines, items, { needDate, deliveredOn, note }) => {
    const issues = [];
    if (needDate && !deliveredOn) issues.push({ key: 'delivered_on', text: 'Укажите дату вручения' });
    let discrepancies = 0;
    lines.forEach((line, index) => {
        const item = items[index];
        if (line.verdict === null) {
            issues.push({ key: `lines.${index}.line_status`, text: 'Отметьте: совпадает или расхождение' });
            return;
        }
        if (line.verdict !== 'bad') return;
        discrepancies += 1;
        const received = Number(String(line.received_qty).replace(',', '.'));
        if (String(line.received_qty).trim() === '' || !Number.isInteger(received) || received < 0) {
            issues.push({ key: `lines.${index}.received_qty`, text: 'Укажите, сколько пришло фактически' });
        } else if (line.line_status === 'shortage' && received >= Number(item?.qty)) {
            issues.push({ key: `lines.${index}.received_qty`, text: 'При недостаче пришло меньше заявленного' });
        } else if (line.line_status === 'excess' && received <= Number(item?.qty)) {
            issues.push({ key: `lines.${index}.received_qty`, text: 'При «лишнем» пришло больше заявленного' });
        }
    });
    if (discrepancies && !filled(note)) issues.push({ key: 'note', text: 'Опишите расхождение' });
    return issues;
};

export const linesPayload = (lines) => lines.map((line) => (line.verdict === 'ok'
    ? { id: line.id, line_status: 'ok' }
    : { id: line.id, line_status: line.line_status, received_qty: line.received_qty, line_note: line.line_note }));

/* ── Справочники: маршруты ─────────────────────────────────────────────────── */

const normCity = (value) => String(value || '').trim().toLowerCase().replace(/ё/g, 'е').replace(/\s+/g, ' ');

/* Сервер хранит маршруты по направлениям. Пары «туда и обратно» с одной зоной в настройках
   показываем одной строкой «↔» (при сохранении сервер развернёт её обратно в две). */
export const collapseRoutePairs = (routes = []) => {
    const used = new Set();
    const rows = [];
    routes.forEach((route) => {
        const key = `${normCity(route.from_city)}|${normCity(route.to_city)}`;
        if (used.has(key)) return;
        const back = routes.find((other) => normCity(other.from_city) === normCity(route.to_city)
            && normCity(other.to_city) === normCity(route.from_city));
        const both = Boolean(back && back.zone_code === route.zone_code && normCity(route.from_city) !== normCity(route.to_city));
        used.add(key);
        if (both) used.add(`${normCity(route.to_city)}|${normCity(route.from_city)}`);
        rows.push({ from_city: route.from_city, to_city: route.to_city, zone_code: route.zone_code, both_ways: both });
    });
    return rows;
};

/* ── Дата и время по Алматы ────────────────────────────────────────────────── */

/* «Сейчас» по Алматы для формы передачи курьеру: у сотрудника в браузере может стоять
   любая зона, а время передачи — время в Казахстане (UTC+5, без перевода часов). */
export const almatyNow = (now = new Date()) => {
    const shifted = new Date(now.getTime() + (5 * 60 + now.getTimezoneOffset()) * 60000);
    return {
        date: `${shifted.getFullYear()}-${pad(shifted.getMonth() + 1)}-${pad(shifted.getDate())}`,
        time: `${pad(shifted.getHours())}:${pad(shifted.getMinutes())}`,
    };
};

/* ── API ───────────────────────────────────────────────────────────────────── */

export function errorText(error, fallback = 'Не получилось — попробуйте ещё раз') {
    const data = error?.response?.data;
    if (data && typeof data === 'object' && data.error) return data.error;
    return error?.message && !/status code/i.test(error.message) ? error.message : fallback;
}

export function createMailApi(apiBaseUrl, headersRef) {
    const url = (path) => `${apiBaseUrl}/api/mail${path}`;
    const config = (extra = {}) => {
        const { headers: own = {}, ...rest } = extra || {};
        return { headers: { ...(headersRef.current ? headersRef.current() : {}), ...own }, ...rest };
    };
    /* Шаг с файлами — multipart: поля формы едут JSON-строкой в `payload`. */
    const multipart = (body, files) => {
        const form = new FormData();
        form.append('payload', JSON.stringify(body || {}));
        (files || []).forEach((file) => form.append('files', file));
        return form;
    };
    return {
        get: (path, params) => axios.get(url(path), config({ params })),
        post: (path, body, files) => axios.post(url(path), files?.length ? multipart(body, files) : (body ?? {}), config()),
        put: (path, body) => axios.put(url(path), body ?? {}, config()),
        patch: (path, body) => axios.patch(url(path), body ?? {}, config()),
        del: (path) => axios.delete(url(path), config()),
        blob: (path, params) => axios.get(url(path), config({ params, responseType: 'blob' })),
    };
}
