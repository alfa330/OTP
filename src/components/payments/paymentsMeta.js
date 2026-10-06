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

/* ── Пояснения к выбору ───────────────────────────────────────────────────────
   Под «i» у каждого селектора: одна фраза о том, зачем поле, и по строке на
   вариант. Человек, который пришёл в раздел первый раз, не обязан знать, чем
   «фиксированный» платёж отличается от «ежемесячного» (решение владельца
   06.10.2026: «чтобы человеку, который только пришёл, всё было понятно»).
   Тексты — данными, а не в разметке: один и тот же селектор стоит в форме
   заявки, в календаре и в фильтрах. */
export const HINTS = {
    source: {
        intro: 'Откуда уйдут деньги.',
        options: [
            ['ТОО', 'безналичный платёж с расчётного счёта компании'],
            ['Кошелёк', 'оплата с корпоративного кошелька'],
            ['Наличные', 'оплата наличными'],
        ],
    },
    type: {
        intro: 'Как часто бывает такой расход.',
        options: [
            ['Разовый', 'покупка, которая не повторяется'],
            ['Ежемесячный', 'расход повторяется каждый месяц, но сумма меняется'],
            ['Фиксированный', 'регулярный платёж с известной суммой и сроком: аренда, абонплата'],
        ],
    },
    category: {
        intro: 'Статья расходов. По ней бухгалтерия группирует затраты в реестре и в выгрузке.',
        outro: 'Нет подходящей — попросите администратора раздела добавить её в «Справочниках».',
    },
    subcategory: { intro: 'Уточнение внутри категории: например, «Аренда → Аренда офиса». Можно не выбирать.' },
    project: {
        intro: 'К какому проекту относится расход.',
        outro: 'От проекта зависит, кто подтвердит счёт: по Приказу часть счетов согласует Директор по развитию вместо Учредителя.',
    },
    department: { intro: 'Отдел, для которого делается закуп. По умолчанию — ваш.' },
    manager: {
        intro: 'Ваш непосредственный руководитель: на шаге 2 он подтверждает, что закуп нужен и поставщик выбран верно.',
        outro: 'Подставляется сам. Если указан не тот человек — измените.',
    },
    items: {
        intro: 'Каждая строка — один товар или услуга, как в счёте поставщика.',
        options: [
            ['Количество', 'сколько покупаем'],
            ['Ед. изм.', 'в чём считаем: штуки, килограммы, месяцы'],
            ['Цена за ед.', 'цена одной единицы'],
            ['Сумма', 'количество × цена, считается сама'],
        ],
        outro: 'Итог заявки складывается из строк. Обобщения вроде «хоз. товары» не подойдут — перечислите, что именно покупаете.',
    },
    documents: {
        intro: 'Без документа заявка на согласование не уйдёт.',
        options: [
            ['Реестр поставщиков', 'таблица: у кого смотрели, цены и ссылки на товар'],
            ['Коммерческое предложение', 'если поставщик один и сравнивать не с кем'],
        ],
    },
    supplierKind: {
        intro: 'Кто поставщик по документам. От этого зависит, какие реквизиты даст бухгалтерия.',
        options: [
            ['ТОО', 'товарищество с ограниченной ответственностью'],
            ['ИП', 'индивидуальный предприниматель'],
            ['Другое', 'физлицо, иностранная компания и прочее — уточните в комментарии'],
        ],
    },
    supplierVat: {
        intro: 'Плательщик ли поставщик НДС — он сам пишет это в счёте или КП.',
        options: [
            ['Без НДС', 'в счёте стоит «Без НДС»'],
            ['С НДС', 'в счёте выделена сумма НДС'],
        ],
    },
    legalEntity: { intro: 'Наше юр. лицо, на которое поставщик выставит счёт и с которого уйдёт оплата.' },
    contract: {
        intro: 'Договор с этим поставщиком.',
        outro: 'Для счёта свыше 300 000 ₸ действующий договор обязателен: без него счёт останется у вас.',
    },
    powerOfAttorney: {
        intro: 'Доверенность — документ, по которому сотрудник забирает товар у поставщика.',
        options: [
            ['Не нужна', 'поставщик привезёт сам или это услуга'],
            ['Нужна', 'бухгалтерия приложит её при оплате, на шаге 10'],
        ],
    },
    paymentOrder: {
        intro: 'Платёжное поручение — отметка банка о том, что оплата ушла.',
        options: [
            ['Не нужно', 'поставщику хватит самой оплаты'],
            ['Нужно', 'поставщик просит подтверждение перед отгрузкой — бухгалтерия приложит его на шаге 10'],
        ],
    },
    previouslyPaid: {
        intro: 'Проверка от двойной оплаты: не платили ли этому поставщику по такому же счёту.',
        options: [
            ['Не платили', 'оплат этому поставщику раньше не было'],
            ['Платили', 'укажите дату и сумму последней оплаты'],
        ],
        outro: 'Прошлые оплаты из реестра раздел подсказывает сам.',
    },
    received: {
        intro: 'От этого зависит закрывающий документ.',
        options: [
            ['Товар', 'нужна накладная'],
            ['Услугу', 'нужен акт выполненных работ (АВР)'],
        ],
    },
    originals: {
        intro: 'Оригинал накладной или АВР с подписью и печатью нужен бухгалтерии: без него она не закроет заявку на шаге 12.',
        options: [
            ['Передал в бухгалтерию', 'оригинал уже у бухгалтера'],
            ['Передам позже', 'бухгалтерия будет ждать оригинал'],
        ],
    },
    vatPayer: {
        intro: 'Плательщик ли НДС.',
        options: [
            ['Без НДС', 'работает без НДС'],
            ['С НДС', 'плательщик НДС'],
        ],
    },
    partyKind: {
        intro: 'Организационная форма.',
        options: [
            ['ТОО', 'товарищество с ограниченной ответственностью'],
            ['ИП', 'индивидуальный предприниматель'],
            ['Другое', 'всё остальное'],
        ],
    },
    active: {
        intro: 'Видна ли запись при заведении новых заявок.',
        options: [
            ['Активна', 'предлагается в списках'],
            ['Скрыта', 'в новых заявках не предлагается, в старых остаётся как была'],
        ],
    },
    contractStatus: {
        intro: 'Счёт свыше 300 000 ₸ проходит только по действующему договору.',
        options: [
            ['Действующий', 'договор работает — счёт пройдёт'],
            ['Расторгнут, Отменён, Архивный, Недействующий', 'для проверки счёта договора как будто нет'],
        ],
    },
    orderScope: {
        intro: 'На что распространяется Приказ.',
        options: [
            ['Выбранные', 'только перечисленные ниже'],
            ['Все', 'любые — отдельная проверка не нужна'],
        ],
    },
    orderStatus: {
        intro: 'Применяется только действующий Приказ.',
        options: [
            ['Действующий', 'по нему счёт уйдёт указанному сотруднику'],
            ['Отменён', 'не применяется, счёт согласует Учредитель'],
        ],
    },
    periodicity: {
        intro: 'Как часто повторяется платёж. От этого зависит, когда раздел сам создаст заявку.',
        options: [
            ['Ежемесячно, ежеквартально, ежегодно', 'заявка создаётся первого числа месяца, на который приходится срок'],
            ['Свой интервал', 'каждые N дней; заявка создаётся за несколько дней до срока'],
        ],
    },
    templateActive: {
        intro: 'Создаёт ли раздел заявки по этому платежу.',
        options: [
            ['Активен', 'заявки создаются сами'],
            ['Приостановлен', 'платёж остаётся в списке, заявки не создаются'],
        ],
    },
    fixedOnly: {
        intro: 'Какие заявки показывать.',
        options: [
            ['Все', 'любые заявки'],
            ['Фиксированные', 'только регулярные платежи: с типом «Фиксированный» и созданные календарём'],
        ],
    },
};

/* Выбор из двух вариантов — подписи. Значение у «не выбрано» — null. */
export const VAT_OPTIONS = [{ value: false, label: 'Без НДС' }, { value: true, label: 'С НДС' }];
export const SUPPLIER_KIND_OPTIONS = [
    { value: 'too', label: 'ТОО' },
    { value: 'ip', label: 'ИП' },
    { value: 'other', label: 'Другое' },
];
export const supplierLabel = (request) => {
    const kind = SUPPLIER_KIND_OPTIONS.find((option) => option.value === request?.supplier_kind)?.label;
    const vat = request?.supplier_vat;
    const parts = kind ? [kind] : [];
    if (vat === true || vat === false) parts.push(vat ? 'с НДС' : 'без НДС');
    return parts.join(', ');
};

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

/* Число из поля или из ответа сервера → целое в долях 10^-digits («2,5» при
   digits = 3 → 2500), округление — половина вверх. Считаем по ЗАПИСИ числа, а не
   умножением дроби: в двоичной арифметике 2,5 × 10,01 = 25,02499…, и тиын на
   экране расходился бы с тем, что сохранит сервер (у него Decimal). */
export const toScaled = (value, digits) => {
    if (value === null || value === undefined || value === '') return 0;
    if (typeof value === 'number' && !Number.isFinite(value)) return 0;
    const text = typeof value === 'number'
        ? String(value)
        : String(value).replace(/[\s₸]/g, '').replace(/тг/gi, '').replace(',', '.');
    const match = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(text);
    if (!match || !(match[2] || match[3])) {
        // «1e21», «1e-7» — запись с порядком: руками такие числа не вводят.
        const number = Number(text);
        return Number.isFinite(number) ? Math.round(number * 10 ** digits) : 0;
    }
    const fraction = match[3] || '';
    const kept = `${fraction}${'0'.repeat(digits)}`.slice(0, digits);
    const scaled = Number(`${match[2] || '0'}${kept}`) + (Number(fraction[digits] || 0) >= 5 ? 1 : 0);
    return match[1] === '-' && scaled ? -scaled : scaled;
};

export const fmtMoney = (value, { currency = true, cents = 'auto' } = {}) => {
    // Тиыны — целым числом. Пока дробная часть считалась вычитанием, «0,999»
    // превращалось в «0,100», а «1,005» — в «1» вместо «1,01».
    const total = toScaled(value, 2);
    const negative = total < 0;
    const abs = Math.abs(total);
    const whole = Math.floor(abs / 100);
    const rest = abs % 100;
    let text = String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
    if (cents === 'always' || (cents === 'auto' && rest > 0)) text += `,${String(rest).padStart(2, '0')}`;
    return `${negative ? '−' : ''}${text}${currency ? `${NBSP}₸` : ''}`;
};

/* ── Позиции заявки ──────────────────────────────────────────────────────────
   Строка — как в счёте поставщика: что, сколько, в чём считаем, цена за одну
   единицу; сумма строки = количество × цена, итог заявки = сумма строк.

   Единица измерения — ВЫБОРОМ из списка, а не полем ввода. В первой версии это
   было текстовое поле «Ед.» рядом с «Кол-во», и в него вписывали второе число
   («5» и «100»), ожидая, что оно попадёт в итог (заявка №1 на проде). Список
   такой ошибки не допускает: в нём только слова. */
export const DEFAULT_UNIT = 'шт';
export const UNITS = ['шт', 'уп', 'пачка', 'компл', 'пара', 'рулон', 'кг', 'г', 'л', 'м', 'м²', 'м³',
    'час', 'день', 'мес', 'год', 'усл'];
/* У старой заявки единица могла быть вписана руками («бут.», «лиц.») — она
   остаётся в списке этой строки, иначе правка заявки молча её стёрла бы. */
export const unitOptions = (current) => {
    const value = String(current || '').trim();
    const list = value && !UNITS.includes(value) ? [...UNITS, value] : UNITS;
    return list.map((unit) => ({ value: unit, label: unit }));
};

/* Количество — до тысячных, цена — до тиына: столько хранит база, и считать
   надо из тех же чисел, что потом покажет карточка. */
const QUANTITY_DIGITS = 3;
const QUANTITY_SCALE = 10 ** QUANTITY_DIGITS;

/* Пустое количество — это ноль, а не «одна штука»: иначе строка с ценой, но без
   количества молча попала бы в итог как 1 × цена. */
const quantityScaled = (item) => {
    const raw = item?.quantity;
    if (raw === '' || raw === null || raw === undefined) return 0;
    return toScaled(raw, QUANTITY_DIGITS);
};
export const itemQuantity = (item) => quantityScaled(item) / QUANTITY_SCALE;

/* Сумма строки в тиынах: количество × цена, половина — вверх. Всё в целых:
   количество делим на целую часть и тысячные, цену — на тысячи тиынов и остаток,
   и ни одно промежуточное произведение не теряет точность. Та же формула на
   сервере — workflow.item_total; сумма заявки там и тут — сумма строк. */
const itemCents = (item) => {
    const quantity = quantityScaled(item);
    const price = toScaled(item?.unit_price, 2);
    const q = Math.abs(quantity);
    const p = Math.abs(price);
    const units = Math.floor(q / QUANTITY_SCALE);
    const part = q % QUANTITY_SCALE;
    const cents = units * p
        + part * Math.floor(p / QUANTITY_SCALE)
        + Math.floor((part * (p % QUANTITY_SCALE) + QUANTITY_SCALE / 2) / QUANTITY_SCALE);
    return (quantity < 0) !== (price < 0) ? -cents : cents;
};
export const itemTotal = (item) => itemCents(item) / 100;
export const itemsTotal = (items) => (items || []).reduce((sum, item) => sum + itemCents(item), 0) / 100;

/* Строка, в которой ничего не начато: её не считаем и не сохраняем. */
export const isBlankItem = (item) => !String(item?.name || '').trim() && !parseAmount(item?.unit_price);

/* Что не так со строкой позиции — словами для человека; '' — строка в порядке
   или пустая. Проверка та же, что на сервере, плюс «цена есть, названия нет»:
   такая строка видна в итоге на экране, а в заявку не попала бы — итог на
   экране и в сохранённой заявке расходились бы. */
export const itemProblem = (item) => {
    if (isBlankItem(item)) return '';
    const name = String(item?.name || '').trim();
    if (!name) return 'У позиции с ценой нет названия — впишите его или удалите строку';
    if (!(itemQuantity(item) > 0)) return `«${name}»: количество — число больше нуля`;
    if (parseAmount(item?.unit_price) < 0) return `«${name}»: цена не может быть отрицательной`;
    return '';
};

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

/* Короткие подписи ролей для узкой колонки реестра. */
const ROLE_SHORT = { ...ROLE_LABELS, manager: 'Руководитель' };

/* Ответственный строкой реестра: кто и в какой роли. Роль второй строкой
   печатается, только если она что-то добавляет: у шага роли имя и есть роль
   («Бухгалтерия» дважды — шум). Согласующий счёта по Приказу подписан
   основанием, а не ролью Учредителя, которую он замещает. */
export const responsibleLines = (request) => {
    if (!request || request.status !== 'active') return { name: '—', sub: '' };
    const role = ROLE_SHORT[request.current_role] || '';
    const name = request.current_assignee_name || role || '—';
    if (Number(request.current_step) === 9 && request.current_assignee_id && request.approval_order_number) {
        return { name, sub: `по Приказу №${request.approval_order_number}` };
    }
    return { name, sub: name === role || name === ROLE_LABELS[request.current_role] ? '' : role };
};

/* Итоговая сумма расхода: сумма − возврат (п. 5 дополнения Дмитриевой). */
export const netAmount = (request) => {
    const amount = parseAmount(request?.amount);
    const refund = parseAmount(request?.refund_amount);
    return refund > 0 ? Math.round((amount - refund) * 100) / 100 : amount;
};

/* ── Колонки реестра ───────────────────────────────────────────────────────────
   П. 2 дополнения Дмитриевой: часть колонок в интерфейсе можно скрыть, а
   выгрузка в Excel всегда полная. «№» и «Расход» не скрываются — без них
   строку не узнать. Ширина — в пикселях, у «Расхода» её нет: он забирает
   оставшееся место. Порядок списка — порядок колонок в таблице. */
export const REGISTRY_COLUMNS = [
    { key: 'number', label: '№ · создана', width: 168, fixed: true, shown: true },
    { key: 'expense', label: 'Расход', fixed: true, shown: true, minWidth: 220 },
    { key: 'project', label: 'Проект', width: 140 },
    { key: 'branch', label: 'Филиал / регион', width: 140 },
    { key: 'category', label: 'Категория', width: 170 },
    { key: 'counterparty', label: 'Контрагент', width: 196, shown: true },
    { key: 'amount', label: 'Сумма', width: 136, shown: true, align: 'right' },
    { key: 'source', label: 'Источник', width: 104 },
    { key: 'type', label: 'Тип оплаты', width: 128 },
    { key: 'period', label: 'Период оплаты', width: 136 },
    { key: 'stage', label: 'Этап', width: 212, shown: true },
    { key: 'responsible', label: 'Ответственный', width: 176, shown: true },
    { key: 'due', label: 'Срок', width: 136, shown: true },
    { key: 'paid', label: 'Дата платежа', width: 116 },
    { key: 'invoice', label: 'Счёт', width: 132 },
    { key: 'card', label: 'Карта', width: 168 },
    { key: 'notes', label: 'Примечания', width: 220 },
];
export const REGISTRY_COLUMNS_STORAGE_KEY = 'payments_registry_columns_v1';

export const defaultRegistryColumns = () => REGISTRY_COLUMNS.filter((column) => column.shown).map((column) => column.key);

/* Сохранённый набор → набор по порядку таблицы: неизвестные ключи (колонку
   убрали из раздела) выпадают, обязательные возвращаются. */
export const normalizeRegistryColumns = (saved) => {
    if (!Array.isArray(saved) || !saved.length) return defaultRegistryColumns();
    const wanted = new Set(saved);
    REGISTRY_COLUMNS.filter((column) => column.fixed).forEach((column) => wanted.add(column.key));
    return REGISTRY_COLUMNS.map((column) => column.key).filter((key) => wanted.has(key));
};

export const registryTableMinWidth = (keys) => REGISTRY_COLUMNS
    .filter((column) => keys.includes(column.key))
    .reduce((sum, column) => sum + (column.width || column.minWidth || 0), 0);

/* Вторая строка «Расхода»: категория, проект, отдел — кроме тех, что уже
   стоят своей колонкой, чтобы одно и то же не печаталось дважды. */
export const expenseSubline = (request, keys = []) => [
    keys.includes('category') ? null : request?.category_name,
    keys.includes('project') ? null : request?.project_name,
    request?.department_name,
].filter(Boolean).join(' · ');

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

/* Основание согласующего счёта одной строкой — пп. 11–12 ТЗ о Приказах: в
   карточке видно не только КТО согласует, но и ПОЧЕМУ; если Приказ не
   применён — причина. Подробная разбивка по условиям остаётся под «i». */
export const approverBasisLine = (basis) => {
    if (!basis) return '';
    if (basis.approver_name) {
        const chosen = (basis.evaluations || []).find((item) => item.order_id === basis.order_id);
        const issued = chosen?.issued_on ? ` от ${chosen.issued_on}` : '';
        return `по Приказу №${basis.order_number || '—'}${issued} вместо Учредителя`;
    }
    const failed = (basis.evaluations || []).find((item) => item.reason);
    return failed ? failed.reason : '';
};

/* Маршрут в карточке: пройденная голова сворачивается, чтобы текущий шаг —
   единственное, с чем человек пришёл, — был виден без прокрутки. Последний
   пройденный шаг остаётся на виду: «что было только что» нужно почти всегда. */
export const ROUTE_FOLD_MIN = 3;
export const splitRoute = (steps = []) => {
    const firstOpen = steps.findIndex((step) => step.state === 'current' || step.state === 'pending');
    const passed = firstOpen === -1 ? steps : steps.slice(0, firstOpen);
    const rest = firstOpen === -1 ? [] : steps.slice(firstOpen);
    if (passed.length < ROUTE_FOLD_MIN) return { folded: [], visible: steps };
    return { folded: passed.slice(0, -1), visible: [passed[passed.length - 1], ...rest] };
};

/* Динамика цены в «Истории оплат» (п. 9 дополнения, «опционально»): цена
   за единицу против прошлой оплаты ТОГО ЖЕ товара. Разные позиции между собой
   не сравниваются — «бумага подорожала на 300 %» по сравнению с ручкой была
   бы враньём. Строки приходят от новых к старым. Возвращает {request_id: %}. */
export const priceTrend = (rows = []) => {
    const result = {};
    rows.forEach((row, index) => {
        const price = parseAmount(row.unit_price);
        const item = String(row.first_item || '').trim().toLowerCase();
        if (!price || !item) return;
        const older = rows.slice(index + 1).find((other) => (
            parseAmount(other.unit_price) > 0 && String(other.first_item || '').trim().toLowerCase() === item));
        if (!older) return;
        const pct = Math.round(((price - parseAmount(older.unit_price)) / parseAmount(older.unit_price)) * 100);
        if (pct !== 0) result[row.request_id] = pct;
    });
    return result;
};

export const trendLabel = (pct) => (pct > 0 ? `+${pct} %` : `−${Math.abs(pct)} %`);

/* «7 пройденных шагов», «2 пройденных шага», «1 пройденный шаг». */
export const passedStepsLabel = (count) => {
    const mod10 = count % 10;
    const mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return `${count} пройденный шаг`;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return `${count} пройденных шага`;
    return `${count} пройденных шагов`;
};

/* ── Шаги: выбор вместо текста ───────────────────────────────────────────────
   На шаге человек выбирает вариант, а отписку из выбранного собирает раздел:
   так её не нужно придумывать, и у всех она читается одинаково. */

export const POWER_OF_ATTORNEY_OPTIONS = [{ value: false, label: 'Не нужна' }, { value: true, label: 'Нужна' }];
export const PAYMENT_ORDER_OPTIONS = [{ value: false, label: 'Не нужно' }, { value: true, label: 'Нужно' }];
export const PREVIOUSLY_PAID_OPTIONS = [{ value: false, label: 'Не платили' }, { value: true, label: 'Платили' }];
export const RECEIVED_OPTIONS = [{ value: 'goods', label: 'Товар' }, { value: 'service', label: 'Услугу' }];
export const ORIGINALS_OPTIONS = [
    { value: 'handed', label: 'Передал в бухгалтерию' },
    { value: 'later', label: 'Передам позже' },
];

/* Шаг вернули на доработку? Последнее событие шага — «возвращена», а не
   «отписка»: тогда причина возврата нужна на виду, прямо над формой шага. */
export const stepReturnNote = (events, stepNo) => {
    const mine = (events || []).filter((event) => Number(event.step_no) === Number(stepNo)
        && (event.kind === 'returned' || event.kind === 'step_done'));
    const last = mine[mine.length - 1];
    return last && last.kind === 'returned' ? last : null;
};

/* Шаг 5: реквизиты нашего юр. лица из справочника — заготовка для поля. */
export const requisitesDraft = (entity) => {
    if (!entity) return '';
    const head = [entity.name, entity.bin ? `БИН ${entity.bin}` : ''].filter(Boolean).join(', ');
    return [head, String(entity.requisites || '').trim()].filter(Boolean).join('\n');
};

/* Шаг 7: описание счёта «одним текстом» из того, что уже есть в заявке —
   что закупается, за какой период, с какого на какое юр. лицо, сумма, отдел
   (перечень из постановки). Человек правит заготовку, а не пишет с нуля. */
export const invoiceDescriptionDraft = (request, legalEntityName) => {
    if (!request) return '';
    const what = [request.expense_name, request.payment_period].filter(Boolean).join(', ');
    const parts = [what ? `${what}.` : ''];
    if (legalEntityName || request.counterparty_name) {
        parts.push(`Оплата с ${legalEntityName || '…'} на ${request.counterparty_name || '…'}.`);
    }
    parts.push(`Сумма ${fmtMoney(request.amount)}.`);
    if (request.department_name) parts.push(`Отдел: ${request.department_name}.`);
    return parts.filter(Boolean).join(' ');
};

/* Шаг 8: последняя оплата этому поставщику из справки «История оплат». */
export const lastPaymentToCounterparty = (history) => (history || [])
    .find((row) => (row.matched || []).includes('counterparty') && row.paid_on) || null;

/* Шаг 8: отписка бухгалтерии из выбора «платили / не платили». */
export const previousPaymentNote = ({ paid, paidOn, paidAmount }) => {
    if (paid === false) return 'Ранее этому поставщику не платили';
    if (paid === true && paidOn && parseAmount(paidAmount) > 0) {
        return `Последняя оплата: ${fmtDate(paidOn)}, ${fmtMoney(paidAmount)}`;
    }
    return '';
};

/* Шаг 11: отписка о получении из выбора «товар / услуга» и «оригинал». */
export const receiptComment = ({ received, originals }) => {
    if (!received || !originals) return '';
    const goods = received === 'goods';
    const head = goods ? 'Получен товар.' : 'Получена услуга.';
    const paper = goods ? 'накладной' : 'АВР';
    const tail = originals === 'handed'
        ? `Оригинал ${paper} передан в бухгалтерию.`
        : `Оригинал ${paper} передам в бухгалтерию позже.`;
    return `${head} ${tail}`;
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
    supplier_kind: 'Форма поставщика',
    supplier_vat: 'НДС поставщика',
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
