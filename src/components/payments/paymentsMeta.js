/*
 * Правила раздела «Оплата счетов», вынесенные из JSX.
 *
 * Здесь всё, что проверяется без браузера: подписи состояний, цвет строки
 * реестра, формат денег и дат, сборка строки истории, подсказки под «i»,
 * проверка формы заявки до отправки.
 *
 * С ТЗ «Закуп и оплата» (#381) заявка идёт не по двенадцати шагам, а по этапам:
 * инициация → согласование → оплата → получение → учёт имущества → закрывающие
 * документы → закрытие. Названия этапов, подзадач, колонок досок и причин
 * уточнения приходят с сервера (`/api/payments/ping` → meta) — они живут в
 * payments/workflow.py, и второй копии на фронте нет намеренно: это правила
 * процесса, а не оформление. Здесь — только то, чем форма помогает человеку
 * выбрать (варианты с пояснениями) и как экран раскрашивает и подписывает.
 *
 * Про цвет. Строка красится ТОЛЬКО там, где цвет несёт смысл: просрочка,
 * «требуется уточнение», закрытая и отклонённая заявка. Обычная заявка в работе
 * не красится вообще — двадцать залитых строк читались бы как тревога, а не как
 * состояние. Приём и веса — те же, что у «Посылок» и офисов в вики.
 *
 * Тесты — tests/payments_meta.test.mjs.
 */

export const STATE_META = {
    active: { label: 'В работе', tone: null },
    clarification: { label: 'Требуется уточнение', tone: 'blocked' },
    overdue: { label: 'Просрочена', tone: 'overdue' },
    done: { label: 'Закрыта', tone: 'done' },
    rejected: { label: 'Отклонена', tone: 'rejected' },
    cancelled: { label: 'Отменена', tone: 'rejected' },
};

/* Полоса-легенда, она же фильтр. «Ждут меня» — не состояние заявки, а срез по
   исполнителю, поэтому стоит сразу за «Все» и не имеет кружка. */
export const STATE_FILTERS = [
    { key: 'all', label: 'Все', counter: 'all', tone: null },
    { key: 'mine', label: 'Ждут меня', counter: 'mine', tone: null },
    { key: 'open', label: 'В работе', counter: 'open', tone: null },
    { key: 'clarification', label: 'Требуют уточнения', counter: 'clarification', tone: 'blocked' },
    { key: 'overdue', label: 'Просрочены', counter: 'overdue', tone: 'overdue' },
    { key: 'done', label: 'Закрыты', counter: 'done', tone: 'done' },
    { key: 'rejected', label: 'Отклонены', counter: 'rejected', tone: 'rejected' },
    { key: 'cancelled', label: 'Отменены', counter: 'cancelled', tone: 'rejected' },
];

/* Фильтр «Статус» на досках (ТЗ, п. 19). У реестра его роль играет
   полоса-легенда, поэтому там этого поля в панели фильтров нет. */
export const STATE_OPTIONS = [
    { value: 'open', label: 'В работе' },
    { value: 'clarification', label: 'Требуется уточнение' },
    { value: 'overdue', label: 'Просрочена' },
    { value: 'done', label: 'Закрыта' },
    { value: 'rejected', label: 'Отклонена' },
    { value: 'cancelled', label: 'Отменена' },
];

/* ── Варианты выбора в заявке (ТЗ, пп. 4, 5, 10) ─────────────────────────────
   Коды — те же, что на сервере (payments/schema.py); подписи совпадают с
   workflow.py — это сверяет тест. */

export const REQUEST_KIND_OPTIONS = [
    { value: 'purchase', label: 'Новый закуп' },
    { value: 'regular', label: 'Регулярный платёж' },
];
export const REQUEST_KIND_LABELS = {
    purchase: 'Новый закуп',
    regular: 'Оплата по действующему/регулярному обязательству',
};

export const PAYMENT_METHOD_OPTIONS = [
    { value: 'invoice', label: 'Оплата счёта' },
    { value: 'card', label: 'Пополнение карты' },
];
/* Короткие подписи тех же вариантов — для сегментов в фильтрах и в условиях
   лимита и маршрута, где рядом стоит третий вариант «все»; полные — под «i». */
export const METHOD_SHORT_OPTIONS = [{ value: 'invoice', label: 'Счёт' }, { value: 'card', label: 'Карта' }];
export const KIND_SHORT_OPTIONS = [{ value: 'purchase', label: 'Закуп' }, { value: 'regular', label: 'Регулярный' }];
export const PAYMENT_METHOD_LABELS = { invoice: 'Оплата счёта', card: 'Пополнение банковской карты' };
export const PAYMENT_METHOD_SHORT = { invoice: 'Счёт', card: 'Карта' };

export const CARD_RECIPIENT_OPTIONS = [
    { value: 'employee', label: 'Карта сотрудника' },
    { value: 'supplier', label: 'Карта поставщика' },
];
export const CARD_RECIPIENT_LABELS = { employee: 'Карта сотрудника', supplier: 'Карта поставщика' };
/* Тип карты одним словом — для карточки доски финансового отдела (п. 9: «тип карты: сотрудник/поставщик»). */
export const CARD_RECIPIENT_SHORT = { employee: 'сотрудник', supplier: 'поставщик' };

export const OBJECT_TYPE_OPTIONS = [
    { value: 'goods', label: 'Товар' },
    { value: 'service', label: 'Услуга' },
];
export const OBJECT_TYPE_LABELS = { goods: 'Товар', service: 'Услуга' };

export const ACCOUNTING_CATEGORY_OPTIONS = [
    { value: 'consumable', label: 'Расходный материал' },
    { value: 'asset', label: 'Имущество на учёт' },
];
export const ACCOUNTING_CATEGORY_LABELS = {
    consumable: 'Расходный материал',
    asset: 'Имущество, подлежащее учёту',
};

export const ALTERNATIVES_OPTIONS = [
    { value: false, label: 'Есть' },
    { value: true, label: 'Отсутствуют' },
];

export const MANAGER_STEP_OPTIONS = [
    { value: 'auto', label: 'По умолчанию' },
    { value: 'required', label: 'Обязателен' },
    { value: 'skip', label: 'Не нужен' },
];

export const AUTO_CREATE_OPTIONS = [
    { value: true, label: 'Создавать самому' },
    { value: false, label: 'Только вручную' },
];

export const ROLE_LABELS = {
    initiator: 'Инициатор',
    manager: 'Непосредственный руководитель',
    approver: 'Утвердитель',
    accounting: 'Бухгалтерия',
    finance: 'Финансовый отдел',
    asset_keeper: 'Ответственный за учёт имущества',
};

/* Роли, у которых есть список участников (вкладка «Участники»). Инициатор и
   его руководитель — люди конкретной заявки, а не роли со списком. `required`
   — без участников этой роли заявку не завести вовсе; остальные нужны только
   заявкам своего вида, и раздел скажет об этом в момент создания такой заявки. */
export const MEMBER_ROLES = [
    { code: 'approver', label: 'Утвердитель', required: true,
        hint: 'Финально согласует закуп, оплату или пополнение карты. Кому из утверждающих уйдёт заявка, решают лимиты и маршруты согласования; не решили — её берёт любой из списка.' },
    { code: 'accounting', label: 'Бухгалтерия', required: true,
        hint: 'Проверяет и оплачивает счета, принимает закрывающие документы. Задача назначается отделу — её видит каждый из списка.' },
    { code: 'finance', label: 'Финансовый отдел',
        hint: 'Выполняет согласованные пополнения карт и видит полные номера карт. В согласовании закупа не участвует.' },
    { code: 'asset_keeper', label: 'Ответственный за учёт имущества',
        hint: 'Ставит купленное имущество на учёт: инвентарный номер, город, подразделение, ответственный. Ведёт реестр имущества.' },
];

export const ATTACHMENT_LABELS = {
    supplier_registry: 'Реестр поставщиков',
    offer: 'Коммерческое предложение',
    invoice: 'Счёт на оплату',
    payment_order: 'Платёжное поручение',
    transfer_proof: 'Подтверждение перевода',
    receipt: 'Чек',
    act: 'Акт (АВР)',
    waybill: 'Накладная',
    handover_act: 'Акт приёма-передачи',
    power_of_attorney: 'Доверенность',
    contract: 'Договор',
    other: 'Другое',
};

/* Виды файлов, уместные в каждом месте заявки: первый в списке — тот, что
   ставится выбранному файлу по умолчанию. */
export const FILE_KINDS = {
    request: ['offer', 'invoice', 'contract', 'other'],
    invoice_payment: ['payment_order', 'power_of_attorney', 'other'],
    card_topup: ['transfer_proof', 'other'],
    receipt_confirm: ['receipt', 'other'],
    receiving: ['waybill', 'act', 'receipt', 'other'],
    asset_registration: ['handover_act', 'other'],
    closing_docs: ['act', 'waybill', 'receipt', 'other'],
};
export const fileKindOptions = (place) => (FILE_KINDS[place] || Object.keys(ATTACHMENT_LABELS))
    .map((value) => ({ value, label: ATTACHMENT_LABELS[value] || value }));

export const CONTRACT_STATUS_META = {
    active: { label: 'Действующий', tone: 'done' },
    terminated: { label: 'Расторгнут', tone: 'rejected' },
    cancelled: { label: 'Отменён', tone: 'rejected' },
    archived: { label: 'Архивный', tone: 'rejected' },
    inactive: { label: 'Недействующий', tone: 'rejected' },
};
export const CONTRACT_STATUS_OPTIONS = Object.entries(CONTRACT_STATUS_META)
    .map(([value, meta]) => ({ value, label: meta.label }));

export const LIMIT_STATUS_META = {
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

export const DOCS_STATUS_META = {
    none: { label: 'Документы не получены', tone: 'blocked' },
    scan: { label: 'Скан получен', tone: 'neutral' },
    original: { label: 'Оригинал получен', tone: 'neutral' },
    closed: { label: 'Документы закрыты', tone: 'done' },
};

export const ASSET_STATUS_META = {
    in_use: { label: 'В эксплуатации', tone: 'done' },
    in_stock: { label: 'На складе', tone: 'neutral' },
    repair: { label: 'В ремонте', tone: 'blocked' },
    written_off: { label: 'Списано', tone: 'rejected' },
};
export const ASSET_STATUS_OPTIONS = Object.entries(ASSET_STATUS_META)
    .map(([value, meta]) => ({ value, label: meta.label }));

/* ── Пояснения к выбору ───────────────────────────────────────────────────────
   Под «i» у каждого селектора: одна фраза о том, зачем поле, и по строке на
   вариант. Человек, который пришёл в раздел первый раз, не обязан знать, чем
   «расходный материал» отличается от «имущества на учёт» (решение владельца
   06.10.2026: «чтобы человеку, который только пришёл, всё было понятно»).
   Тексты — данными, а не в разметке: один и тот же селектор стоит в форме
   заявки, в справочниках и в фильтрах. */
export const HINTS = {
    // Сегменты с короткими подписями (METHOD_SHORT_OPTIONS, KIND_SHORT_OPTIONS):
    // в фильтрах третий вариант — «Все», в условиях лимита и маршрута — «Любой».
    filterKind: {
        intro: 'Заявки какого типа показывать.',
        options: [
            ['Закуп', 'новый закуп: с вариантами поставщиков и обоснованием'],
            ['Регулярный', 'оплата по действующему или регулярному обязательству — аренда, связь, лицензии'],
        ],
    },
    filterMethod: {
        intro: 'Заявки с каким способом оплаты показывать.',
        options: [
            ['Счёт', 'оплата счёта — её проводит бухгалтерия'],
            ['Карта', 'пополнение банковской карты — его выполняет финансовый отдел'],
        ],
    },
    limitKind: {
        intro: 'К каким заявкам относится условие. «Любая» — ко всем.',
        options: [
            ['Закуп', 'только новые закупы'],
            ['Регулярный', 'только оплаты по действующим и регулярным обязательствам'],
        ],
    },
    limitMethod: {
        intro: 'К какому способу оплаты относится условие. «Любой» — к обоим.',
        options: [
            ['Счёт', 'только оплата счёта'],
            ['Карта', 'только пополнение банковской карты'],
        ],
    },
    legalEntity: {
        intro: 'Наша компания, с которой уйдёт оплата и на которую поставщик выставит счёт.',
        outro: 'Реквизиты компании появятся под полем — их можно скопировать и переслать поставщику.',
    },
    department: { intro: 'Подразделение, для которого делается закуп. По умолчанию — ваше.' },
    category: {
        intro: 'Категория закупа. По ней бухгалтерия группирует затраты, а матрица согласования выбирает, кто утверждает.',
        outro: 'Нет подходящей — попросите администратора раздела добавить её в «Справочниках».',
    },
    subcategory: { intro: 'Уточнение внутри категории: например, «Аренда → Аренда офиса». Можно не выбирать.' },
    project: {
        intro: 'К какому проекту относится расход.',
        outro: 'От проекта может зависеть, кто утверждает: лимит согласования бывает задан на отдельные проекты.',
    },
    justification: { intro: 'Что покупаем и зачем: руководитель и утверждающий решают по этому тексту, нужен ли закуп.' },
    dueOn: { intro: 'К какому числу нужна оплата. Заявка с прошедшим сроком и без оплаты подсвечивается как просроченная, а исполнитель получает напоминание.' },
    items: {
        intro: 'Каждая строка — один товар или услуга, как в счёте поставщика.',
        options: [
            ['Количество', 'сколько покупаем'],
            ['Ед. изм.', 'в чём считаем: штуки, килограммы, месяцы'],
            ['Цена за ед.', 'цена одной единицы'],
            ['Сумма', 'количество × цена, считается сама'],
        ],
        outro: 'Ориентировочная сумма заявки складывается из строк. Обобщения вроде «хоз. товары» не подойдут — перечислите, что именно покупаете.',
    },
    objectType: {
        intro: 'Что получаем. От этого зависит, что понадобится после оплаты.',
        options: [
            ['Товар', 'вещь: после получения нужно указать количество и категорию учёта'],
            ['Услуга', 'работа или доступ: подтверждается актом'],
        ],
    },
    accountingCategory: {
        intro: 'Как купленный товар будет учитываться.',
        options: [
            ['Расходный материал', 'бумага, картриджи, вода — получили и израсходовали'],
            ['Имущество на учёт', 'техника, мебель — получит инвентарный номер и ответственного; без постановки на учёт заявка не закроется'],
        ],
    },
    paymentMethod: {
        intro: 'Как платим.',
        options: [
            ['Оплата счёта', 'поставщик выставил счёт — его оплатит бухгалтерия'],
            ['Пополнение карты', 'деньги переводит финансовый отдел на карту сотрудника или поставщика'],
        ],
    },
    cardRecipient: {
        intro: 'Чью карту пополняем.',
        options: [
            ['Карта сотрудника', 'сотрудник купит сам и после покупки приложит чек'],
            ['Карта поставщика', 'перевод напрямую поставщику товара или услуги'],
        ],
    },
    cardNumber: {
        intro: 'Полный номер карты — он нужен финансовому отделу для перевода.',
        outro: 'Номер хранится зашифрованным. Целиком его видят только финансовый отдел и вы; остальным показываются последние четыре цифры.',
    },
    paymentPurpose: { intro: 'На что пойдут деньги — эту фразу увидят утверждающий и финансовый отдел.' },
    offers: {
        intro: 'Варианты, из которых вы выбирали: у кого смотрели, сколько стоит и на каких условиях.',
        options: [
            ['Стоимость', 'цена всего закупа у этого поставщика'],
            ['Рекомендуемый', 'у кого предлагаете купить — он станет поставщиком заявки'],
        ],
        outro: 'К каждому варианту можно приложить коммерческое предложение и ссылку на товар.',
    },
    alternatives: {
        intro: 'Обычный закуп сравнивает нескольких поставщиков.',
        options: [
            ['Есть', 'перечислите варианты поставщиков в таблице ниже'],
            ['Отсутствуют', 'поставщик единственный — укажите причину, и хватит одного'],
        ],
    },
    choiceReason: { intro: 'Почему рекомендуете именно этого поставщика: цена, сроки, гарантия.' },
    template: {
        intro: 'Регулярный платёж из справочника.',
        outro: 'Поставщик, договор, компания, реквизиты, назначение платежа и согласующий подставятся из справочника сами — вам остаётся период, сумма и счёт.',
    },
    paymentPeriod: { intro: 'За какой период платим: «октябрь 2026», «4 квартал 2026».' },
    invoice: {
        intro: 'Счёт поставщика: файл, номер и дата.',
        outro: 'По номеру и дате раздел предупреждает о возможном дубле: такой же счёт этому поставщику уже мог быть оплачен.',
    },
    contract: {
        intro: 'Договор с этим поставщиком.',
        outro: 'Для счёта свыше 300 000 ₸ действующий договор обязателен: без него счёт не уйдёт в оплату.',
    },
    received: { intro: 'Сколько получили — как в накладной: «10 уп», «1 шт».' },
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
    supplierApprover: {
        intro: 'Кому из утверждающих уходят заявки на этого поставщика.',
        outro: 'Действует в пределах лимита согласования; заявка на бо́льшую сумму идёт по общим лимитам и маршрутам.',
    },
    limitScope: {
        intro: 'На что распространяется лимит.',
        options: [
            ['Выбранные', 'только перечисленные ниже'],
            ['Все', 'любые — отдельная проверка не нужна'],
        ],
    },
    limitStatus: {
        intro: 'Применяется только действующий лимит.',
        options: [
            ['Действующий', 'заявка в его пределах уйдёт указанному согласующему'],
            ['Отменён', 'не применяется'],
        ],
    },
    limitAny: { intro: 'Пусто — лимит действует для любого значения.' },
    managerStep: {
        intro: 'Нужно ли заявкам этого маршрута согласование непосредственного руководителя.',
        options: [
            ['По умолчанию', 'у нового закупа этап есть, у регулярного платежа — нет'],
            ['Обязателен', 'руководитель согласует всегда'],
            ['Не нужен', 'заявка сразу идёт утверждающему'],
        ],
    },
    routeApprover: {
        intro: 'Кто утверждает заявки этого маршрута, когда ни один лимит согласования не подошёл.',
        outro: 'Пусто — заявку берёт любой из утверждающих.',
    },
    periodicity: {
        intro: 'Как часто повторяется платёж. От этого зависит, когда раздел сам создаст заявку.',
        options: [
            ['Ежемесячно, ежеквартально, ежегодно', 'заявка создаётся первого числа месяца, на который приходится срок'],
            ['Свой интервал', 'каждые N дней; заявка создаётся за несколько дней до срока'],
        ],
    },
    templateActive: {
        intro: 'Действует ли платёж.',
        options: [
            ['Активен', 'его можно выбрать в заявке, и раздел создаёт по нему заявки сам'],
            ['Приостановлен', 'остаётся в списке, но в заявках не предлагается'],
        ],
    },
    autoCreate: {
        intro: 'Кто заводит заявку по этому платежу.',
        options: [
            ['Создавать самому', 'раздел заводит заявку в начале периода и сразу отправляет на согласование; счёт ответственный приложит перед оплатой'],
            ['Только вручную', 'заявку заводит человек: «Создать заявку» → «Регулярный платёж»'],
        ],
    },
    templateLimit: { intro: 'До какой суммы заявка по этому платежу уходит его согласующему. Больше — идёт по общим лимитам и маршрутам.' },
    minSuppliers: { intro: 'Сколько вариантов поставщиков нужно в новом закупе, чтобы заявка ушла на согласование. Не действует, когда альтернативные предложения отсутствуют.' },
    dueSoonDays: { intro: 'За сколько дней до срока оплаты исполнителю и инициатору приходит напоминание.' },
    cardOwner: {
        intro: 'Чья это карта.',
        options: [
            ['Сотрудника', 'на неё переводят деньги под закуп; сотрудник потом приложит чек'],
            ['Поставщика', 'перевод напрямую поставщику'],
        ],
    },
    assetStatus: {
        intro: 'Что сейчас с имуществом.',
        options: [
            ['В эксплуатации', 'им пользуются'],
            ['На складе', 'лежит и ждёт выдачи'],
            ['В ремонте', 'временно не работает'],
            ['Списано', 'с учёта снято — из профиля сотрудника пропадает'],
        ],
    },
};

/* Выбор из двух вариантов — подписи. Значение у «не выбрано» — null. */
export const VAT_OPTIONS = [{ value: false, label: 'Без НДС' }, { value: true, label: 'С НДС' }];

// Порог договора — тот же, что CONTRACT_REQUIRED_OVER на сервере; сервер отдаёт
// его в /ping, здесь значение по умолчанию до ответа.
export const CONTRACT_THRESHOLD = 300000;

/* ── Оттенок строки ────────────────────────────────────────────────────────── */


export const TONE_ROW = {
    overdue: 'bg-rose-50/80',
    blocked: 'bg-amber-50/80',
    done: 'bg-emerald-50/60',
    rejected: 'bg-slate-50',
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
export const tonePill = (tone) => TONE_PILL[tone] || TONE_PILL.neutral;
export const toneText = (tone) => TONE_TEXT[tone] || TONE_TEXT.neutral;

export const isClosed = (request) => ['done', 'rejected', 'cancelled'].includes(request?.status);
export const isPaid = (request) => Boolean(request?.paid_on);

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
        : String(value).replace(/[\s  ₸]/g, '').replace(/тг/gi, '').replace(',', '.');
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

/* «10 уп; 2 шт» — заготовка поля «Количество получено» из позиций заявки. */
export const quantityText = (items) => (items || [])
    .map((item) => `${fmtQty(item.quantity)} ${item.unit || ''}`.trim())
    .filter(Boolean)
    .join('; ');

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

export const plural = (count, forms) => {
    const abs = Math.abs(count);
    const mod10 = abs % 10;
    const mod100 = abs % 100;
    if (mod10 === 1 && mod100 !== 11) return `${count} ${forms[0]}`;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return `${count} ${forms[1]}`;
    return `${count} ${forms[2]}`;
};

/* Срок словами. Срок важен, пока заявка не оплачена: у оплаченной и закрытой
   «просрочен на 3 дня» было бы неправдой — деньги уже ушли. */
export const dueLabel = (request, today = todayISO()) => {
    if (!request?.due_on) return '';
    const days = daysUntil(request.due_on, today);
    if (days === null) return fmtDate(request.due_on);
    if (isClosed(request) || isPaid(request)) return `к ${fmtDate(request.due_on)}`;
    if (days < 0) return `просрочен на ${pluralDays(days)}`;
    if (days === 0) return 'сегодня';
    return `через ${pluralDays(days)}`;
};

export const isOverdue = (request, today = todayISO()) => {
    if (!request?.due_on || isClosed(request) || isPaid(request)) return false;
    const days = daysUntil(request.due_on, today);
    return days !== null && days < 0;
};

/* Метка срока — как у карточек «Задач»: флажок и дата, смысл несёт тон. Красный —
   срок прошёл, жёлтый — подходит (`soonDays` — та же настройка, по которой раздел
   шлёт напоминание о сроке), серый — всё спокойно. Подробности — в подсказке.
   У оплаченной вместо срока — галочка и день оплаты. null — показывать нечего:
   срока нет либо заявка закрыта без оплаты. */
export const dueChip = (request, { soonDays = 3, today = todayISO() } = {}) => {
    if (!request) return null;
    if (request.paid_on) {
        const word = request.payment_method === 'card' ? 'Пополнено' : 'Оплачено';
        return { tone: 'done', icon: 'check', label: fmtDateShort(request.paid_on), title: `${word} ${fmtDate(request.paid_on)}` };
    }
    if (!request.due_on || (request.status && request.status !== 'active')) return null;
    const days = daysUntil(request.due_on, today);
    const label = fmtDateShort(request.due_on);
    const date = fmtDate(request.due_on);
    if (days === null) return { tone: 'normal', icon: 'flag', label, title: `Срок оплаты: ${date}` };
    if (days < 0) return { tone: 'overdue', icon: 'flag', label, title: `Срок оплаты ${date} — просрочен на ${pluralDays(days)}` };
    if (days === 0) return { tone: 'soon', icon: 'flag', label, title: `Срок оплаты — сегодня, ${date}` };
    return { tone: days <= soonDays ? 'soon' : 'normal', icon: 'flag', label, title: `Срок оплаты ${date} — через ${pluralDays(days)}` };
};

/* «оплачено 09.10» / «пополнено 09.10» — для строки срока в карточке заявки. */
export const paidLabel = (request) => (request?.paid_on
    ? `${request.payment_method === 'card' ? 'пополнено' : 'оплачено'} ${fmtDateShort(request.paid_on)}`
    : '');

/* ── Порции и страницы — как в разделе «Задачи» ────────────────────────────────
   Колонка доски показывает одну порцию (её размер человек выбирает: по 20, 40
   или 60), остальное — в окне колонки, которое догружается при прокрутке.
   Списки — страницами со стрелками. */
export const BOARD_CHUNK_SIZES = [20, 40, 60];
export const DEFAULT_BOARD_CHUNK = 20;
export const BOARD_CHUNK_STORAGE_KEY = 'otp.payments.board.chunkSize';
export const normalizeBoardChunk = (value) => {
    const parsed = Number(value);
    return BOARD_CHUNK_SIZES.includes(parsed) ? parsed : DEFAULT_BOARD_CHUNK;
};
export const COLUMN_SHEET_PAGE = 40;
export const DESK_PAGE_SIZE = 20;
export const LIST_PAGE_SIZE = 50;
export const DICTIONARY_PAGE_SIZE = 20;

export const REQUEST_FORMS = ['заявка', 'заявки', 'заявок'];
export const TASK_FORMS = ['задача', 'задачи', 'задач'];
export const RECORD_FORMS = ['запись', 'записи', 'записей'];

/* Страница списка: номер (в пределах), с какой по какую строку, сколько страниц. */
export const pageRange = (page, pageSize, total) => {
    const count = Math.max(0, Number(total) || 0);
    const size = Math.max(1, Number(pageSize) || 1);
    const totalPages = Math.max(1, Math.ceil(count / size));
    const current = Math.min(Math.max(1, Math.trunc(Number(page)) || 1), totalPages);
    return {
        page: current,
        totalPages,
        offset: (current - 1) * size,
        from: count ? (current - 1) * size + 1 : 0,
        to: Math.min(count, current * size),
    };
};

/* ── Тип заявки на карточке ───────────────────────────────────────────────────
   Карточки разводятся цветом по типу: новый закуп по счёту, закуп на карту,
   регулярный платёж (пп. 4–5: тип заявки и способ оплаты; регулярный платёж
   всегда идёт по счёту). Цвета — не из тех, что в разделе уже значат состояние
   (красный — срок прошёл, жёлтый — уточнение, зелёный — сделано): тип не должен
   читаться как «что-то не так». Классы — целыми строками, иначе сборка Tailwind
   их не найдёт. */
export const REQUEST_TYPES = {
    purchase: { label: 'Закуп по счёту', bar: 'before:bg-blue-500', pill: 'bg-blue-50 text-blue-700' },
    card: { label: 'Закуп на карту', bar: 'before:bg-teal-500', pill: 'bg-teal-50 text-teal-700' },
    regular: { label: 'Регулярный платёж', bar: 'before:bg-violet-500', pill: 'bg-violet-50 text-violet-700' },
};

export const requestType = (request) => {
    const key = request?.request_kind === 'regular' ? 'regular' : (request?.payment_method === 'card' ? 'card' : 'purchase');
    // Черновик закупа без выбранного способа оплаты — просто «Новый закуп».
    const label = key === 'purchase' && !request?.payment_method ? 'Новый закуп' : REQUEST_TYPES[key].label;
    return { key, ...REQUEST_TYPES[key], label };
};

/* ── Лица на карточке — как у «Задач»: «кто поручил → кто исполняет» ──────────
   У заявки это «инициатор → у кого этап сейчас»; в итоговых колонках — кто этап
   закрыл. Подразделение («Бухгалтерия», «Утвердитель» без имени) лицом не
   рисуется: у него значок, а не инициалы — иначе «Б» читалась бы как человек. */
export const initialsOf = (name) => String(name || '?').trim().split(/\s+/).slice(0, 2)
    .map((part) => part[0]).join('').toUpperCase() || '?';

const DONE_VERB = {
    manager_approval: 'Согласовал',
    approval: 'Согласовал',
    invoice_payment: 'Оплатил',
    card_topup: 'Пополнил',
    closing_docs: 'Закрыл',
};

export const cardFaces = (card) => {
    const task = card?.subtask || {};
    const from = card?.initiator_name ? { name: card.initiator_name, title: `Инициатор: ${card.initiator_name}`, role: false } : null;
    let to = null;
    if (task.state === 'open') {
        const name = task.assignee_name || card?.current_assignee_name || '';
        const verb = ['manager_approval', 'approval'].includes(task.kind) ? 'Согласует' : 'Исполнитель';
        if (name) to = { name, title: `${verb}: ${name}`, role: Boolean(task.role_label) && name === task.role_label };
    } else if (task.state === 'done' && task.done_by_name) {
        const name = task.done_by_name;
        const verb = task.outcome === 'rejected' || card?.column === 'rejected' ? 'Отклонил' : (DONE_VERB[task.kind] || 'Выполнил');
        to = { name, title: `${verb}: ${name}`, role: Boolean(task.role_label) && name === task.role_label };
    }
    // Заявка у самого инициатора (вернули с вопросом) — второе лицо повторяло бы первое.
    if (task.state === 'waiting' && from) return { from: { ...from, title: `Ждём ответа: ${from.name}` }, to: null };
    if (to && from && to.name === from.name) return { from, to: null };
    return { from, to };
};

/* Задача рабочего стола «вернули мне»: заявку отправили инициатору на доработку
   или с вопросом. Только такие строки стола отмечены цветом — по ним ждут ответа
   человека, а не его очередного шага. */
export const deskTaskReturned = (task) => Boolean(task) && (
    Boolean(task.clarify_label || task.clarify_comment)
    || (task.task === 'initiation' && ['rework', 'clarification'].includes(task.subtask?.status))
);

/* Нижняя строка задачи стола: номер заявки, способ оплаты, инициатор, подразделение.
   Своё имя человеку не печатаем (`own` ставит сервер) — в его заявках оно стояло
   бы в каждой строке. */
export const deskTaskAbout = (task) => [
    `№${task.id}`, PAYMENT_METHOD_SHORT[task.payment_method], task.own ? null : task.initiator_name, task.department_name,
].filter(Boolean).join(' · ');

/* ── Этапы и исполнитель ───────────────────────────────────────────────────── */

/* Исполнитель строкой реестра: кто и в какой роли. Роль второй строкой
   печатается, только если она что-то добавляет: у задачи подразделения имя и
   есть роль («Бухгалтерия» дважды — шум). */
export const responsibleLines = (request) => {
    if (!request || request.status !== 'active') return { name: '—', sub: '' };
    const role = ROLE_LABELS[request.current_role] || '';
    const name = request.current_assignee_name || role || '—';
    return { name, sub: name === role ? '' : role };
};

/* Где заявка сейчас — одной строкой: этап, а у заявки на уточнении — причина. */
export const stageLine = (request) => {
    if (!request) return '';
    if (request.status === 'done') return 'Закрыта';
    if (request.status === 'rejected') return 'Отклонена';
    if (request.status === 'cancelled') return 'Отменена';
    if (requestState(request) === 'clarification') {
        return request.clarify_from ? 'Требуется уточнение' : 'У инициатора';
    }
    return request.stage_label || '';
};

/* Где заявка сейчас — для карточки реестра на телефоне: этап и у кого он. У закрытой
   заявки исполнителя нет, а у задачи подразделения этап и исполнитель могут
   называться одинаково — дважды одно слово не печатаем. */
export const registryWhere = (request) => {
    const stage = stageLine(request);
    if (!request || request.status !== 'active') return stage;
    const who = responsibleLines(request).name;
    return [stage, who].filter((part, index, list) => part && part !== '—' && list.indexOf(part) === index).join(' · ');
};

/* Итоговая сумма расхода: сумма − возврат (п. 5 дополнения Дмитриевой). */
export const netAmount = (request) => {
    const amount = parseAmount(request?.amount);
    const refund = parseAmount(request?.refund_amount);
    return refund > 0 ? Math.round((amount - refund) * 100) / 100 : amount;
};

/* ── Колонки реестра ───────────────────────────────────────────────────────────
   П. 2 дополнения Дмитриевой: часть колонок в интерфейсе можно скрыть, а
   выгрузка в Excel всегда полная. «№» и «Закуп» не скрываются — без них
   строку не узнать. Ширина — в пикселях, у «Закупа» её нет: он забирает
   оставшееся место. Порядок списка — порядок колонок в таблице. */
export const REGISTRY_COLUMNS = [
    { key: 'number', label: '№ · создана', width: 152, fixed: true, shown: true },
    { key: 'expense', label: 'Закуп', fixed: true, shown: true, minWidth: 220 },
    { key: 'kind', label: 'Тип заявки', width: 150 },
    { key: 'entity', label: 'Компания', width: 160 },
    { key: 'project', label: 'Проект', width: 140 },
    { key: 'category', label: 'Категория', width: 170 },
    { key: 'counterparty', label: 'Поставщик', width: 188, shown: true },
    { key: 'amount', label: 'Сумма', width: 128, shown: true, align: 'right' },
    { key: 'method', label: 'Оплата', width: 104, shown: true },
    { key: 'period', label: 'Период оплаты', width: 136 },
    { key: 'stage', label: 'Этап', width: 176, shown: true },
    { key: 'responsible', label: 'Исполнитель', width: 192, shown: true },
    { key: 'due', label: 'Срок', width: 132, shown: true },
    { key: 'paid', label: 'Дата оплаты', width: 116 },
    { key: 'invoice', label: 'Счёт', width: 132 },
    { key: 'docs', label: 'Закрывающие документы', width: 190 },
    { key: 'notes', label: 'Комментарий', width: 220 },
];
export const REGISTRY_COLUMNS_STORAGE_KEY = 'payments_registry_columns_v2';

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

/* Вторая строка «Закупа»: категория, проект, подразделение — кроме тех, что уже
   стоят своей колонкой, чтобы одно и то же не печаталось дважды. */
export const expenseSubline = (request, keys = []) => [
    keys.includes('category') ? null : request?.category_name,
    keys.includes('project') ? null : request?.project_name,
    request?.department_name,
].filter(Boolean).join(' · ');

/* ── Согласование (п. 6) ───────────────────────────────────────────────────────
   Маршрут определяет система; человеку показываем, кто согласует и почему. */
export const approvalSummary = (basis) => {
    if (!basis) return null;
    return {
        approver: basis.approver_name || 'Любой из утверждающих',
        basisText: basis.approver_name ? (basis.basis || '') : '',
        personal: Boolean(basis.approver_name),
        managerStep: Boolean(basis.manager_step),
        managerReason: basis.manager_reason || '',
        trace: basis.trace || [],
        conflict: Boolean(basis.conflict),
    };
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

/* ── Реквизиты (п. 13.3) ───────────────────────────────────────────────────────
   Реквизиты компании одним текстом — то, что копируют и пересылают поставщику.
   Та же раскладка на сервере — directory.requisites_text. */
export const requisitesText = (entity) => {
    if (!entity) return '';
    const lines = [entity.name || ''];
    if (entity.bin) lines.push(`БИН ${entity.bin}`);
    if (entity.legal_address) lines.push(`Юридический адрес: ${entity.legal_address}`);
    if (entity.iik) lines.push(`ИИК ${entity.iik}`);
    if (entity.bank_name) lines.push(`Банк: ${entity.bank_name}`);
    if (entity.bik) lines.push(`БИК ${entity.bik}`);
    if (entity.kbe) lines.push(`КБЕ ${entity.kbe}`);
    lines.push(entity.vat_payer ? 'Плательщик НДС' : 'Без НДС');
    const extra = String(entity.requisites || '').trim();
    if (extra) lines.push(extra);
    return lines.filter(Boolean).join('\n');
};

/* ── Карта (п. 5.2) ────────────────────────────────────────────────────────── */

export const cardDigits = (value) => String(value || '').replace(/\D/g, '');

/* Номер карты группами по четыре: «4400 4301 2345 6789». */
export const cardNumberLabel = (value) => cardDigits(value).replace(/(\d{4})(?=\d)/g, '$1 ');

/* Сходится ли контрольная цифра номера (алгоритм Луна). Подсказка, не запрет:
   опечатка в одной цифре почти всегда её ломает. */
export const cardLuhnOk = (value) => {
    const digits = cardDigits(value);
    if (!digits) return false;
    let total = 0;
    for (let index = 0; index < digits.length; index += 1) {
        let digit = Number(digits[digits.length - 1 - index]);
        if (index % 2 === 1) {
            digit *= 2;
            if (digit > 9) digit -= 9;
        }
        total += digit;
    }
    return total % 10 === 0;
};

/* Что не так с номером карты — словами; '' — годится. Длина — как на сервере
   (cards.MIN_DIGITS…MAX_DIGITS). */
export const cardProblem = (value, [min, max] = [13, 19]) => {
    const digits = cardDigits(value);
    if (!digits) return 'Укажите номер карты';
    if (digits.length < min || digits.length > max) return `Номер карты: от ${min} до ${max} цифр`;
    return '';
};

/* ── Форма заявки: чего не хватает ─────────────────────────────────────────────
   Те же правила, что workflow.missing_for_submit на сервере, — чтобы человек
   узнал о пропуске до нажатия «Отправить», а не после. Сервер проверяет сам;
   здесь только подсказка. `fileKinds` — виды уже приложенных и выбранных файлов. */
/* Чего не хватает, чтобы отправить заявку. `staffed` — у каких ролей есть участники
   ({finance, asset_keeper}): способ оплаты или категорию учёта, которые некому
   выполнить, сервер не примет — человек должен узнать об этом до отправки. */
export const purchaseProblems = (draft, { minSuppliers = 3, fileKinds = [], hasCard = false, staffed = {} } = {}) => {
    const problems = [];
    if (!draft.legal_entity_id) problems.push('выберите компанию');
    if (!draft.department_id) problems.push('укажите подразделение');
    if (!String(draft.expense_name || '').trim()) problems.push('назовите закуп');
    if (!draft.category_id) problems.push('выберите категорию закупа');
    if (!String(draft.justification || '').trim()) problems.push('опишите и обоснуйте закуп');
    const filled = (draft.items || []).filter((item) => !isBlankItem(item));
    if (!filled.length) problems.push('добавьте хотя бы одну позицию');
    else {
        const problem = filled.map(itemProblem).find(Boolean);
        if (problem) problems.push(problem);
        else if (!(itemsTotal(filled) > 0)) problems.push('укажите цену за единицу');
    }
    if (!draft.due_on) problems.push('укажите желаемый срок');
    if (!draft.object_type) problems.push('выберите: товар или услуга');
    else if (draft.object_type === 'goods' && !draft.accounting_category) problems.push('выберите категорию учёта');
    else if (draft.object_type === 'goods' && draft.accounting_category === 'asset' && staffed.asset_keeper === false) {
        problems.push(UNSTAFFED.asset_keeper);
    }
    if (!draft.payment_method) problems.push('выберите способ оплаты');

    const offers = (draft.offers || []).filter((offer) => !isBlankOffer(offer));
    const incomplete = offers.some((offer) => !offerName(offer) || !(parseAmount(offer.amount) > 0));
    if (draft.no_alternatives) {
        if (!draft.no_alternatives_reason) problems.push('выберите причину отсутствия альтернатив');
        else if (draft.no_alternatives_reason === 'other' && !String(draft.no_alternatives_comment || '').trim()) {
            problems.push('опишите причину отсутствия альтернатив');
        }
        if (!offers.length) problems.push('укажите поставщика и стоимость');
    } else if (offers.length < minSuppliers) {
        problems.push(`нужно не меньше ${plural(minSuppliers, ['варианта поставщика', 'вариантов поставщиков', 'вариантов поставщиков'])}`);
    }
    if (incomplete) problems.push('у каждого поставщика укажите название и стоимость');
    // Единственный поставщик («альтернатив нет») рекомендуем по определению —
    // отмечать его и обосновывать выбор не из чего.
    if (offers.length && !draft.no_alternatives) {
        if (offers.filter((offer) => offer.is_recommended).length !== 1) problems.push('отметьте рекомендуемого поставщика');
        if (!String(draft.supplier_choice_reason || '').trim()) problems.push('обоснуйте выбор поставщика');
    }

    if (draft.payment_method === 'card') {
        if (staffed.finance === false) problems.push(UNSTAFFED.finance);
        if (!draft.card_recipient) problems.push('выберите, чью карту пополнить');
        // У каждого получателя своё поле: сотрудник выбирается из списка, владельца
        // карты поставщика вписывают — заполненное «чужое» поле не считается.
        else if (draft.card_recipient === 'employee' && !draft.card_holder_user_id) problems.push('укажите сотрудника');
        else if (draft.card_recipient === 'supplier' && !String(draft.card_holder_name || '').trim()) {
            problems.push('укажите ФИО владельца карты');
        }
        if (!hasCard && !draft.card_id && cardProblem(draft.card_number)) problems.push('укажите полный номер карты');
        if (!String(draft.payment_purpose || '').trim()) problems.push('укажите назначение перевода');
    } else if (draft.payment_method === 'invoice') {
        // Счёт у нового закупа обычно появляется после согласования; но если его
        // уже прикладывают — номер и дата нужны сразу.
        const started = fileKinds.includes('invoice') || String(draft.invoice_number || '').trim() || draft.invoice_date;
        if (started) problems.push(...invoiceProblems(draft, fileKinds));
    }
    return problems;
};

/* Способ оплаты или категория учёта, для которых в «Участниках» нет исполнителя. */
export const UNSTAFFED = {
    finance: 'пополнение карты пока некому выполнить: финансовый отдел не назначен — сообщите администратору раздела',
    asset_keeper: 'имущество пока некому поставить на учёт: ответственный не назначен — сообщите администратору раздела',
};

/* Регулярный платёж: `template` — строка справочника. Поставщика, компанию и
   категорию учёта товара задаёт она — исправить их в заявке человек не может. */
export const regularProblems = (draft, { fileKinds = [], template = null, staffed = {} } = {}) => {
    const problems = [];
    if (!draft.fixed_template_id) problems.push('выберите регулярный платёж');
    if (template) {
        const gaps = [
            !template.counterparty_id && 'поставщик',
            !template.legal_entity_id && 'компания',
            template.object_type === 'goods' && !template.accounting_category && 'категория учёта товара',
        ].filter(Boolean);
        if (gaps.length) problems.push(`в справочнике у платежа не указаны: ${gaps.join(', ')} — попросите администратора дополнить`);
        else if (template.accounting_category === 'asset' && staffed.asset_keeper === false) problems.push(UNSTAFFED.asset_keeper);
    }
    if (!draft.department_id) problems.push('укажите подразделение');
    if (!String(draft.payment_period || '').trim()) problems.push('укажите период оплаты');
    if (!(parseAmount(draft.amount) > 0)) problems.push('укажите сумму');
    problems.push(...invoiceProblems(draft, fileKinds));
    return problems;
};

/* Заявка, принятая ещё первой версией процесса (у неё есть `legacy_step`): трёх
   поставщиков и обоснования от неё не требовали. Нужно только то, без чего её
   не оплатить, — как и на сервере (workflow.missing_for_submit). */
export const legacyProblems = (draft, { fileKinds = [] } = {}) => {
    const problems = [];
    if (!draft.legal_entity_id) problems.push('выберите компанию');
    if (!(draftTotal(draft) > 0)) problems.push('укажите сумму');
    if (draft.payment_method !== 'card') problems.push(...invoiceProblems(draft, fileKinds));
    return problems;
};

const draftTotal = (draft) => (draft.request_kind === 'regular'
    ? parseAmount(draft.amount)
    : itemsTotal((draft.items || []).filter((item) => !isBlankItem(item))));

export const invoiceProblems = (draft, fileKinds = []) => {
    const problems = [];
    if (!fileKinds.includes('invoice')) problems.push('приложите счёт');
    if (!String(draft.invoice_number || '').trim()) problems.push('укажите номер счёта');
    if (!draft.invoice_date) problems.push('укажите дату счёта');
    return problems;
};

export const offerName = (offer) => String(offer?.supplier_name || '').trim() || (offer?.counterparty_id ? '·' : '');
export const isBlankOffer = (offer) => !offer?.counterparty_id && !String(offer?.supplier_name || '').trim()
    && !parseAmount(offer?.amount) && !String(offer?.terms || '').trim() && !String(offer?.link || '').trim()
    && !String(offer?.comment || '').trim();

/* ── История ───────────────────────────────────────────────────────────────── */

const FIELD_LABELS = {
    expense_name: 'Наименование закупа',
    justification: 'Описание и обоснование',
    project_id: 'Проект',
    branch: 'Филиал / регион',
    category_id: 'Категория закупа',
    subcategory_id: 'Подкатегория',
    counterparty_id: 'Поставщик',
    counterparty_account_id: 'Реквизиты поставщика',
    legal_entity_id: 'Компания',
    contract_id: 'Договор',
    department_id: 'Подразделение',
    amount: 'Сумма',
    items: 'Позиции',
    offers: 'Варианты поставщиков',
    payment_period: 'Период оплаты',
    payment_method: 'Способ оплаты',
    payment_purpose: 'Назначение платежа',
    card_recipient: 'Получатель по карте',
    card_holder_name: 'Владелец карты',
    card_holder_user_id: 'Владелец карты',
    card_id: 'Карта',
    card_number: 'Номер карты',
    object_type: 'Тип объекта',
    accounting_category: 'Категория учёта',
    no_alternatives: 'Альтернативы',
    no_alternatives_reason: 'Причина отсутствия альтернатив',
    no_alternatives_comment: 'Причина отсутствия альтернатив',
    supplier_choice_reason: 'Обоснование выбора поставщика',
    notes: 'Комментарий',
    due_on: 'Желаемый срок',
    invoice_number: 'Номер счёта',
    invoice_date: 'Дата счёта',
    invoice_description: 'Описание счёта',
    fixed_template_id: 'Регулярный платёж',
    paid_on: 'Дата оплаты',
    paid_amount: 'Оплачено',
    refund_on: 'Дата возврата',
    refund_amount: 'Сумма возврата',
};

const unique = (list) => list.filter((value, index) => value && list.indexOf(value) === index);

/* Событие истории словами. `titleOf(kind)` — название подзадачи с сервера
   (meta.subtasks); без него событие подписывается обобщённо. */
export const describeEvent = (event, titleOf = () => '') => {
    if (!event) return '';
    const payload = event.payload || {};
    const stage = payload.kind ? titleOf(payload.kind) : '';
    const of = stage ? ` — «${stage}»` : '';
    switch (event.kind) {
        case 'created': return 'Заявка создана';
        case 'generated': return 'Создана календарём регулярных платежей';
        case 'migrated': return 'Переведена на этапы';
        case 'submitted': {
            if (payload.first) return 'Отправлена на согласование';
            const changed = (payload.changed || []).join(', ');
            return changed
                ? `Отправлена на согласование заново: изменено — ${changed}`
                : 'Отправлена на согласование заново';
        }
        case 'clarified': return `Инициатор ответил${of}`;
        case 'approved': return payload.kind === 'manager_approval' ? 'Согласовано руководителем' : 'Утверждено';
        case 'returned': return `Возвращена на доработку${of}`;
        case 'clarification': return payload.system
            ? `Требуется уточнение${of}`
            : `Запрошена информация${of}`;
        case 'rejected': return 'Заявка отклонена';
        case 'cancelled': return 'Заявка отменена';
        case 'paid': return `Счёт оплачен${payload.paid_amount ? `: ${fmtMoney(payload.paid_amount)}` : ''}`;
        case 'topped_up': return `Карта пополнена${payload.paid_amount ? `: ${fmtMoney(payload.paid_amount)}` : ''}`;
        case 'receipt_provided': return 'Приложен чек';
        case 'received': return `Получение подтверждено${payload.received_quantity ? `: ${payload.received_quantity}` : ''}`;
        case 'asset_registered': {
            const numbers = (payload.inventory_numbers || []).filter(Boolean).join(', ');
            return numbers ? `Имущество поставлено на учёт: ${numbers}` : 'Имущество поставлено на учёт';
        }
        case 'docs_status': return `Закрывающие документы: ${(DOCS_STATUS_META[payload.to]?.label || '').toLowerCase() || payload.to}`;
        case 'closed': return 'Заявка закрыта';
        case 'subtask_moved': return `Статус изменён${of}`;
        case 'reassigned': return payload.assignee_name
            ? `Исполнитель${stage ? ` «${stage}»` : ''}: ${payload.assignee_name}`
            : `Исполнитель${stage ? ` «${stage}»` : ''} возвращён подразделению`;
        case 'attachment_added': return `Файл добавлен: ${payload.file_name || ''}`.trim();
        case 'attachment_removed': return `Файл снят: ${payload.file_name || ''}`.trim();
        case 'card_revealed': return 'Посмотрел полный номер карты';
        case 'refund': return payload.refund_amount
            ? `Отмечен возврат ${fmtMoney(payload.refund_amount)}`
            : 'Возврат снят';
        case 'edited': {
            const labels = unique(Object.keys(payload.changes || {}).map((key) => FIELD_LABELS[key] || key));
            return labels.length ? `Изменено: ${labels.join(', ')}` : 'Заявка изменена';
        }
        /* События первой версии раздела (12 шагов) остаются в истории старых заявок. */
        case 'step_done': return `Шаг ${event.step_no} — отписка`;
        case 'step_skipped': return `Шаг ${event.step_no} пропущен`;
        case 'blocked': return 'Счёт остановлен: нужен действующий договор';
        case 'unblocked': return 'Ограничение по договору снято';
        case 'route': return 'Определён согласующий счёта';
        case 'comment': return 'Комментарий';
        default: return event.kind || 'Событие';
    }
};

export const fileSizeLabel = (bytes) => {
    const size = Number(bytes) || 0;
    if (size < 1024) return `${size} Б`;
    if (size < 1024 * 1024) return `${Math.round(size / 1024)} КБ`;
    return `${(size / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`;
};

export const exportFileName = (today = todayISO()) => `Заявки на закуп и оплату ${today}.xlsx`;

/* ── Доски (пп. 7–9) ───────────────────────────────────────────────────────────
   Колонки и их названия приходят с сервера. Здесь — только то, куда карточку
   можно перетащить: между рабочими колонками (`work`) её двигают свободно, в
   остальные ведёт действие со своими обязательными данными. */
export const canDropTo = (board, card, columnKey) => Boolean(
    board && card && card.can_act && card.subtask?.state === 'open'
    && (board.work || []).includes(columnKey) && (board.work || []).includes(card.column)
    && card.column !== columnKey);

/* Фильтры досок и реестра (п. 19) → параметры запроса. Пустое не уходит. */
export const EMPTY_FILTERS = {
    legal_entity_id: null, department_id: null, initiator_id: null, assignee_id: null, approver_id: null,
    counterparty_id: null, request_kind: '', payment_method: '', state: '', amount_from: '', amount_to: '',
    date_from: '', date_to: '', overdue: false, mine: false,
};

export const filtersToParams = (filters, params = new URLSearchParams()) => {
    Object.entries(filters || {}).forEach(([key, value]) => {
        if (value === '' || value === null || value === undefined || value === false) return;
        params.set(key, String(value === true ? 1 : value));
    });
    return params;
};

export const activeFilterCount = (filters) => {
    let count = 0;
    Object.entries(filters || {}).forEach(([key, value]) => {
        if (value === '' || value === null || value === undefined || value === false) return;
        // «Сумма от/до» и «Создана с/по» — по одному фильтру, а не по два.
        if (key === 'amount_to' && filters.amount_from !== '') return;
        if (key === 'date_to' && filters.date_from) return;
        count += 1;
    });
    return count;
};
