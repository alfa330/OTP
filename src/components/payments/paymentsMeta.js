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
   исполнителю, поэтому стоит сразу за «Все» и не имеет кружка. «Наблюдаю» — такой
   же срез: заявки в работе, где смотрящий в наблюдателях (плитка есть, только когда они есть). */
export const STATE_FILTERS = [
    { key: 'all', label: 'Все', counter: 'all', tone: null },
    { key: 'mine', label: 'Ждут меня', counter: 'mine', tone: null },
    { key: 'watching', label: 'Наблюдаю', counter: 'watching', tone: null },
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
        options: [
            ['Знаете компанию', 'выберите её — реквизиты появятся под полем, их можно скопировать и переслать поставщику'],
            ['Не знаете', 'оставьте «Не знаю»: после отправки компанию укажет бухгалтерия, и потом заявка уйдёт на согласование'],
        ],
    },
    // Заявка первой версии: она уже согласована, компания нужна для оплаты — «Не знаю» у неё нет.
    legalEntityRequired: {
        intro: 'Наша компания, с которой уйдёт оплата и на которую поставщик выставит счёт.',
        outro: 'Реквизиты компании появятся под полем — их можно скопировать и переслать поставщику.',
    },
    // Бухгалтерия указывает компанию, которой не знал инициатор (просьба владельца 15.10.2026).
    entityClarify: {
        intro: 'Для какой компании закуп: с её счёта уйдёт оплата, на неё поставщик выставит счёт.',
        outro: 'Инициатор увидит выбранную компанию в заявке, а согласующего раздел выберет уже с её учётом.',
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
    observers: {
        intro: 'Кто будет следить за ходом заявки — например, пока вы в отпуске.',
        outro: 'Наблюдатель видит заявку и её ход, а в колокол ему приходят те же сообщения, что и инициатору: согласовано, оплачено, вернули на доработку. Действовать по заявке он не может. Добавить и снять наблюдателей можно и потом — в окне заявки.',
    },
    supplierEmails: {
        intro: 'Почта поставщика: бухгалтерия, менеджер, общий ящик.',
        outro: 'Когда меняются реквизиты наших компаний, сюда само уходит письмо с новыми реквизитами. Несколько адресов — через запятую, до пяти. Те же адреса с контактными лицами — в справочнике «Почта поставщиков».',
    },
    requisitesMail: {
        intro: 'Изменились реквизиты компании — поставщикам с почтой само уходит письмо: что было, что стало и новые реквизиты целиком.',
        outro: 'Письмо уходит через 10 минут после сохранения с почты компании (блок «Почта для писем»). Поправка в эти минуты попадёт в то же письмо, «Не отправлять» его отменит. Получают все поставщики с почтой; кому письмо не нужно — снимите галочку в «Кому». «Отправлено» — письмо принял почтовый сервер; если адреса не существует, отказ придёт позже в сам ящик.',
    },
    companyMailbox: {
        intro: 'С этой почты уходят письма поставщикам о смене реквизитов компании. Письмо подписано названием компании, ответы поставщиков придут сюда же.',
        outro: 'Нужен пароль приложения — его создают в настройках безопасности почты, обычный пароль от почты не подойдёт. «Подключить» отправит пробное письмо на этот же адрес. Пароль хранится зашифрованным и больше нигде не показывается.',
    },
    recipientsPick: {
        intro: 'Кому уйдёт это письмо: по умолчанию — всем поставщикам с почтой.',
        outro: 'Снимите галочку — этому поставщику письмо не уйдёт. Выбрать можно, пока письмо ждёт отправки. Адрес, общий у двух поставщиков, получит письмо, если отмечен хотя бы один из них.',
    },
};

/* Наблюдателей у заявки — не больше этого (workflow.MAX_OBSERVERS; сервер отдаёт его в
   /ping → meta.max_observers, здесь значение по умолчанию до ответа). */
export const MAX_OBSERVERS = 10;

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
/* Рамка строки-карточки того же состояния (фон — TONE_ROW). Закрытая заявка — самой тихой
   зелёной рамкой: таких строк в реестре десятки подряд. */
export const TONE_EDGE = {
    overdue: 'border-rose-200',
    blocked: 'border-amber-200',
    done: 'border-emerald-100',
    rejected: 'border-slate-200',
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
export const toneEdge = (tone) => (tone && TONE_EDGE[tone]) || '';

/** С этой ли строки реестра начинаются завершённые заявки. Сервер отдаёт сначала заявки в
 *  работе, потом закрытые, отклонённые и отменённые — на стыке список получает подпись.
 *  Страница, которая целиком из завершённых (или целиком из рабочих), подписи не имеет. */
export const closedGroupStart = (items, index) => Boolean(items?.[index])
    && items[index].status !== 'active'
    && items[index - 1]?.status === 'active';
export const tonePill = (tone) => TONE_PILL[tone] || TONE_PILL.neutral;
export const toneText = (tone) => TONE_TEXT[tone] || TONE_TEXT.neutral;

export const isClosed = (request) => ['done', 'rejected', 'cancelled'].includes(request?.status);
export const isPaid = (request) => Boolean(request?.paid_on);
/* Каким словом назвать день, когда ушли деньги. Перевод на карту — «пополнено», а
   не «оплачено»: у карты сотрудника оплату подтверждает только чек (п. 5.3). */
export const paidWord = (request) => (request?.payment_method === 'card' ? 'Пополнено' : 'Оплачено');

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
export const itemProblem = (item) => itemIssue(item)?.text || '';

/* То же со ссылкой на поле строки (`field` — name, quantity или unit_price): после
   неудачной попытки отправить форма обводит красным именно его. */
export const itemIssue = (item) => {
    if (isBlankItem(item)) return null;
    const name = String(item?.name || '').trim();
    if (!name) return { field: 'name', text: 'У позиции с ценой нет названия — впишите его или удалите строку' };
    if (!(itemQuantity(item) > 0)) return { field: 'quantity', text: `«${name}»: количество — число больше нуля` };
    if (parseAmount(item?.unit_price) < 0) return { field: 'unit_price', text: `«${name}»: цена не может быть отрицательной` };
    return null;
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
        return { tone: 'done', icon: 'check', label: fmtDateShort(request.paid_on), title: `${paidWord(request)} ${fmtDate(request.paid_on)}` };
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

/* «оплачено 09.10» / «пополнено 09.10» — для строки срока в карточке заявки и в реестре. */
export const paidLabel = (request) => (request?.paid_on
    ? `${paidWord(request).toLowerCase()} ${fmtDateShort(request.paid_on)}`
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

/* Заготовка «Моих задач», пока список не пришёл и помнить нечего (вход в раздел не должен
   перестраиваться на глазах — см. paymentsEntry.js). Строк — сколько задач обещает счётчик
   вкладки, но не длиннее экрана. Ноль — заготовки нет: сразу пустой стол. Счётчик неизвестен
   (первый вход на этом устройстве) — три строки. */
export const DESK_SKELETON_MAX = 6;
export const deskSkeletonRows = (expected) => {
    if (expected === null || expected === undefined || expected === '') return 3;
    const count = Math.trunc(Number(expected));
    if (!Number.isFinite(count) || count < 0) return 3;
    return Math.min(DESK_SKELETON_MAX, count);
};
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

/* ── Окно записи, пока она грузится ───────────────────────────────────────────
   Что показывает окно заявки или имущества: 'loading' — заготовку, 'error' —
   отказ сервера, 'ready' — саму запись. Первый кадр после открытия — уже
   'loading': запрос ещё не ушёл, но пустое окно на этот кадр и было тем коротким
   окном, которое потом разом вырастало. Отказ виден, только когда запрос
   закончился: прошлая ошибка не должна мелькать, пока идёт новая попытка.
   Значение же служит окну ключом смены высоты (sizeKey в ui/ios.jsx). */
export const recordView = ({ data, error, loading = false } = {}) => (
    data ? 'ready' : (error && !loading ? 'error' : 'loading'));

/* Сколько строк в разделах заготовки. У заявки — по её обычным разделам (задача,
   «Заявка», «Поставщики», «Оплата и согласование»), ниже — карточка свёрнутых
   разделов (CARD_FOLDS): вместе со сводкой заготовка выше окна на любом ноутбуке
   и обычном мониторе, как и настоящая заявка, — поэтому рамка окна при загрузке
   не меняется вовсе. У имущества — карточка описания без заголовка и «История
   владельцев и мест». */
export const REQUEST_SKELETON = [2, 7, 3, 7];
export const ASSET_SKELETON = [{ rows: 12, title: false }, 2];

/* ── Разделы окна заявки «по запросу» ─────────────────────────────────────────
   Ход по этапам, документы и история нужны не каждому и не каждый раз, а окно
   заявки с ними было в два с половиной экрана (просьба владельца 13.10.2026:
   «история, ход заявки и т.д. уходили под таб, то есть раскрытые»). Теперь это
   три строки в одной карточке внизу окна: название, суть одной строкой, шеврон;
   раздел раскрывается на месте. Суть свёрнутого раздела — здесь, чтобы её
   формулировку держал тест. */
export const CARD_FOLDS = [
    { key: 'route', title: 'Ход заявки' },
    { key: 'observers', title: 'Наблюдатели' },
    { key: 'files', title: 'Документы' },
    { key: 'history', title: 'История' },
];
export const FILE_FORMS = ['файл', 'файла', 'файлов'];
export const EVENT_FORMS = ['событие', 'события', 'событий'];

/* Статус задачи на этапе — тот же, что колонка на доске: «Новые / На рассмотрении» у
   согласования, «Новые счета / Проверка / Готово к оплате / На оплате» у бухгалтерии.
   Исполнитель двигает свою задачу сам — перетаскиванием на доске или кнопками на
   странице задачи в окне заявки (STAGE_STEPS ниже). Здесь — у каких этапов рабочие
   статусы есть и как они называются: так их подписывает окно заявки.

   Этапы инициатора (черновик, чек, получение) сюда не входят: их статус меняет действие,
   а не перестановка. У учёта имущества доски нет — подписи свои. */
export const STAGE_STATUS_KINDS = ['manager_approval', 'approval', 'invoice_payment', 'card_topup', 'asset_registration'];
const STAGE_STATUS_LABELS = { new: 'Новая', in_progress: 'В работе' };

/** Рабочие статусы задачи этапа: [{ value, label }].
 *  Пусто — у этапа нет рабочих статусов или статус один. */
export const stageStatusOptions = (meta, kind) => {
    if (!STAGE_STATUS_KINDS.includes(kind)) return [];
    const subtask = (meta?.subtasks || []).find((item) => item.kind === kind);
    const board = (meta?.boards || []).find((item) => item.code === subtask?.board);
    const options = (subtask?.statuses || []).map((value) => ({
        value,
        label: (board?.columns || []).find((column) => column.key === value)?.label || STAGE_STATUS_LABELS[value] || value,
    }));
    return options.length > 1 ? options : [];
};

/* ── Задача — кнопками, ступень за ступенью ────────────────────────────────────
   Просьба владельца 14.10.2026 (со снимком окна заявки): «не нужен такой
   переключатель, посмотри раздел задача сделай кнопками, и чтобы было постепенно».
   Как в «Задачах» («Принять в работу» → «Отметить выполненной»): на странице задачи
   внизу — одна кнопка следующей ступени; ступени — рабочие статусы этапа в порядке
   колонок доски (workflow.SUBTASKS.statuses). Итоговое действие этапа («Согласовать»,
   «Оплачено», «Пополнено», «Поставить на учёт») с полями формы появляется на последней
   ступени, когда шагать кнопками больше некуда. Назад кнопок нет: вернуть карточку на
   прежнюю ступень можно на доске. */
export const STAGE_STEPS = {
    manager_approval: { new: { to: 'review', label: 'Взять на рассмотрение' } },
    approval: { new: { to: 'review', label: 'Взять на рассмотрение' } },
    invoice_payment: {
        new: { to: 'checking', label: 'Взять в проверку' },
        checking: { to: 'ready', label: 'Счёт проверен' },
        ready: { to: 'paying', label: 'Начать оплату' },
    },
    card_topup: {
        new: { to: 'in_progress', label: 'Взять в работу' },
        in_progress: { to: 'ready', label: 'Готово к пополнению' },
    },
    asset_registration: { new: { to: 'in_progress', label: 'Взять в работу' } },
};

/** Следующая ступень задачи: { to, label } или null — ступеней дальше нет. */
export const stageStep = (kind, status) => STAGE_STEPS[kind]?.[status] || null;

/** Задача дошла до итогового действия этапа. Этап без ступеней и незнакомый статус
 *  (старая заявка) — тоже итог: форма этапа от человека не прячется. */
export const stageAtFinal = (kind, status) => !stageStep(kind, status);

/** Задачу уже взяли (она не на первой ступени): с этого места можно запросить информацию
 *  у инициатора. Этап без ступеней — взят всегда. */
export const stageTaken = (kind, status) => !STAGE_STEPS[kind] || status !== 'new';

/** Подпись текущей ступени — как колонка доски («Проверка», «На рассмотрении»). */
export const stageStatusLabel = (meta, kind, status) => (
    stageStatusOptions(meta, kind).find((option) => option.value === status)?.label || '');

/** У кого заявка сейчас — одной строкой для свёрнутого «Хода заявки». */
export const routeSummary = (request, subtasks = []) => {
    const status = request?.status || 'active';
    if (status === 'done') return 'Все этапы пройдены';
    if (status === 'rejected') return 'Отклонена';
    if (status === 'cancelled') return 'Отменена';
    const stages = (subtasks || []).filter((item) => item.kind !== 'initiation' && item.state !== 'skipped');
    const current = (subtasks || []).find((item) => item.state === 'open');
    if (!current) return stages.length ? plural(stages.length, ['этап', 'этапа', 'этапов']) : '';
    const who = current.assignee_name || current.role_label || '';
    return [current.title, who].filter(Boolean).join(' · ');
};

export const filesSummary = (attachments = []) => (
    (attachments || []).length ? plural(attachments.length, FILE_FORMS) : 'нет файлов');

/** Наблюдатели одной строкой для свёрнутого раздела: «Иванова Айгерим и ещё 2». Сам
 *  смотрящий — «вы», и первым: ему важнее всего, что он в списке. */
export const observersSummary = (observers = [], meId = null) => {
    const list = observers || [];
    if (!list.length) return 'не добавлены';
    const mine = meId !== null && meId !== undefined && list.some((person) => person.user_id === meId);
    const first = mine ? 'вы' : (list[0].name || '');
    return list.length > 1 ? `${first} и ещё ${list.length - 1}` : first;
};

/** Что форма заявки шлёт о наблюдателях: разницу с тем, что было при её открытии, —
 *  { observers_add, observers_remove } (пустые не шлются). Не список целиком: пока форма
 *  открыта, в окне заявки могли добавить ещё кого-то, и сохранение формы сняло бы его
 *  (разбор 15.10.2026). У новой заявки `atOpen` пуст — уходят только выбранные. */
export const observersPatch = (draftIds = [], atOpen = []) => {
    const now = new Set((draftIds || []).map(String));
    const before = new Set((atOpen || []).map(String));
    const add = [...now].filter((id) => !before.has(id)).map(Number).filter(Boolean);
    const remove = [...before].filter((id) => !now.has(id)).map(Number).filter(Boolean);
    return {
        ...(add.length ? { observers_add: add } : {}),
        ...(remove.length ? { observers_remove: remove } : {}),
    };
};

/** Кому из наблюдателей раздел не открыт — одной короткой строкой: до двух имён, дальше «и ещё N»,
 *  а если не открыт никому из трёх и больше — так и сказано. `names` — без доступа, `total` —
 *  сколько наблюдателей всего. Десять длинных ФИО подряд на телефоне занимали восемь строк. */
export const observersClosedLine = (names = [], total = 0) => {
    const list = (names || []).filter(Boolean);
    if (!list.length) return '';
    const verb = list.length > 1 ? 'не увидят' : 'не увидит';
    if (list.length === total && total > 2) return 'Никому из наблюдателей раздел пока не открыт — заявку они не увидят.';
    const shown = list.length > 2 ? `${list.slice(0, 2).join(', ')} и ещё ${list.length - 2}` : list.join(', ');
    return `Раздел не открыт: ${shown} — заявку пока ${verb}.`;
};

/** Выбранные люди для кнопки списка выбора нескольких сотрудников: первое имя и сколько
 *  ещё — { first, rest, total }. `ids` — как их отдаёт список (строками). Одно имя, а не
 *  два через запятую: на телефоне строка «А, Б» обрезалась на первом имени, и второй
 *  человек пропадал из виду без счётчика (разбор 15.10.2026). */
export const peopleParts = (ids = [], users = []) => {
    const byId = new Map((users || []).map((user) => [String(user.id), user.name]));
    const all = [...new Set((ids || []).map(String))];
    const first = all.map((id) => byId.get(id)).find(Boolean) || '';
    return { first, rest: first ? all.length - 1 : all.length, total: all.length };
};

/** `events` — в порядке записи, последнее — в конце (как отдаёт сервер). */
export const historySummary = (events = []) => {
    const list = events || [];
    if (!list.length) return 'пока пусто';
    const last = list[list.length - 1];
    const when = last?.created_at ? fmtDateTime(last.created_at) : '';
    return [plural(list.length, EVENT_FORMS), when && `последнее ${when}`].filter(Boolean).join(' · ');
};

/* Гаснущий край ленты, которая листается вбок (доска, этапы в сводке заявки), — маской, а не
   градиентом поверх: маске всё равно, какого цвета страница, светлая тема или тёмная. Гаснет
   только тот край, за которым что-то есть: обрезанное само говорит, что лента продолжается. */
export const EDGE_FADE = 44;

/** Стиль маски для ленты: left/right — есть ли скрытое за этим краем; size — сколько пикселей гаснет.
 *  undefined — гасить нечего. */
export const edgeFade = (left, right, size = EDGE_FADE) => {
    if (!left && !right) return undefined;
    const image = `linear-gradient(to right, ${left ? 'transparent' : '#000'} 0, #000 ${left ? size : 0}px, `
        + `#000 calc(100% - ${right ? size : 0}px), ${right ? 'transparent' : '#000'} 100%)`;
    return { maskImage: image, WebkitMaskImage: image };
};

/* ── Доска на экранах разной ширины ───────────────────────────────────────────
   Просьба владельца 13.10.2026: на досках с большим числом колонок остальные
   «не заметны при первом взгляде»; на большом мониторе колонки должны стоять
   все сразу, если помещаются, а если нет — должно быть видно, что есть ещё.

   Колонки делят ширину поля поровну, пока каждой достаётся не меньше
   BOARD_COLUMN_MIN: тогда доска видна целиком и не листается. Верхнего предела
   у колонки нет: поле доски — колонка раздела, и доска занимает её целиком,
   от края до края, как шапка и поиск над ней (с пределом доска на широком
   экране стояла бы уже шапки — та самая несимметричность, на которую владелец
   и указал). Когда все не помещаются, ширина подбирается так, чтобы следующая
   колонка выглядывала из-за края на треть: обрезанная колонка сама говорит,
   что доска продолжается (а не стоит впритык к краю, будто последняя). */
export const BOARD_COLUMN_GAP = 10;
export const BOARD_COLUMN_MIN = 232;
export const BOARD_COLUMN_PEEK = 0.35;

/** Раскладка доски по ширине поля: { fits, width, visible }.
 *  fits — все колонки на экране; width — ширина одной; visible — сколько колонок
 *  видно целиком. */
export const boardLayout = (areaWidth, count) => {
    const area = Math.floor(Number(areaWidth) || 0);
    const total = Math.max(0, Math.trunc(Number(count) || 0));
    if (!total || area <= 0) return { fits: true, width: BOARD_COLUMN_MIN, visible: total };
    const equal = Math.floor((area - (total - 1) * BOARD_COLUMN_GAP) / total);
    if (equal >= BOARD_COLUMN_MIN) return { fits: true, width: equal, visible: total };
    // Сколько колонок встаёт целиком, если оставить место выглядывающей следующей.
    for (let full = Math.floor((area + BOARD_COLUMN_GAP) / (BOARD_COLUMN_MIN + BOARD_COLUMN_GAP)); full >= 1; full -= 1) {
        const width = Math.floor((area - full * BOARD_COLUMN_GAP) / (full + BOARD_COLUMN_PEEK));
        if (width >= BOARD_COLUMN_MIN) return { fits: false, width, visible: full };
    }
    return { fits: false, width: BOARD_COLUMN_MIN, visible: 1 };
};

/** Какие колонки не видны целиком при прокрутке на `scrollLeft`: { left: [...], right: [...] } —
 *  сами колонки, по порядку. Колонка, выглядывающая из-за края, тоже «ещё не видна». */
export const boardHidden = (columns, layout, scrollLeft, areaWidth) => {
    const step = layout.width + BOARD_COLUMN_GAP;
    const from = Math.max(0, Number(scrollLeft) || 0);
    const to = from + Math.max(0, Number(areaWidth) || 0);
    const left = [];
    const right = [];
    (columns || []).forEach((column, index) => {
        const start = index * step;
        // Пара пикселей — дробная прокрутка и округление ширины, а не скрытая колонка.
        if (start < from - 2) left.push(column);
        else if (start + layout.width > to + 2) right.push(column);
    });
    return { left, right };
};

/** На сколько прокрутить доску кнопкой «ещё»: на видимые колонки без одной — последняя
 *  виденная остаётся на экране, взгляд не теряет место. */
export const boardPageStep = (layout) => (
    Math.max(1, (layout.visible || 1) - 1) * (layout.width + BOARD_COLUMN_GAP));

/** Ползунок тонкой полосы под доской: { left, width } в пикселях дорожки либо null,
 *  когда листать нечего. */
export const BOARD_THUMB_MIN = 48;
export const boardThumb = (scrollLeft, contentWidth, viewWidth, trackWidth) => {
    const content = Number(contentWidth) || 0;
    const view = Number(viewWidth) || 0;
    const track = Number(trackWidth) || 0;
    if (content <= view + 1 || track <= 0) return null;
    const width = Math.min(track, Math.max(BOARD_THUMB_MIN, Math.round((view / content) * track)));
    const progress = Math.min(1, Math.max(0, (Number(scrollLeft) || 0) / (content - view)));
    return { left: Math.round(progress * (track - width)), width };
};

/** Обратно: куда прокрутить доску, если ползунок поставили на `thumbLeft`. */
export const boardScrollFor = (thumbLeft, thumbWidth, contentWidth, viewWidth, trackWidth) => {
    const room = (Number(trackWidth) || 0) - (Number(thumbWidth) || 0);
    if (room <= 0) return 0;
    const progress = Math.min(1, Math.max(0, (Number(thumbLeft) || 0) / room));
    return Math.round(progress * Math.max(0, (Number(contentWidth) || 0) - (Number(viewWidth) || 0)));
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
   рисуется: у него значок, а не инициалы — иначе «Б» читалась бы как человек.

   Фото — из профиля человека, как на карточках «Задач»; у кого фото нет —
   инициалы. Ссылки в карточку раскладывает boardColumnsWithPhotos (ниже). Фото
   привязано к тому же полю, что и имя: взяли имя из запасного поля — фото к нему
   не приставляем. */
export const initialsOf = (name) => String(name || '?').trim().split(/\s+/).slice(0, 2)
    .map((part) => part[0]).join('').toUpperCase() || '?';

const DONE_VERB = {
    manager_approval: 'Согласовал',
    approval: 'Согласовал',
    invoice_payment: 'Оплатил',
    card_topup: 'Пополнил',
    closing_docs: 'Закрыл',
};

/** Ответ доски → колонки, в карточках которых фото людей стоят готовыми ссылками.
 *  Сервер отдаёт ссылки один раз на ответ (`photos`: номер человека → ссылка), а в карточке —
 *  только номера: одно лицо бывает на десятках карточек, и подписанная ссылка длиной под
 *  килобайт в каждой из них утроила бы ответ. Дальше раздел работает с карточкой как раньше. */
export const boardColumnsWithPhotos = (payload) => {
    const photos = payload?.photos || {};
    const link = (key) => (key === null || key === undefined ? '' : String(photos[key] || ''));
    return (payload?.columns || []).map((column) => ({
        ...column,
        cards: (column.cards || []).map((card) => ({
            ...card,
            initiator_avatar_url: link(card.initiator_photo),
            subtask: card.subtask
                ? { ...card.subtask, assignee_avatar_url: link(card.subtask.assignee_photo), done_by_avatar_url: link(card.subtask.done_by_photo) }
                : card.subtask,
        })),
    }));
};

const withPhoto = (face, url) => {
    const photo = face.role ? '' : String(url || '').trim();
    return photo ? { ...face, avatar_url: photo } : face;
};

export const cardFaces = (card) => {
    const task = card?.subtask || {};
    const from = card?.initiator_name
        ? withPhoto({ name: card.initiator_name, title: `Инициатор: ${card.initiator_name}`, role: false }, card.initiator_avatar_url)
        : null;
    let to = null;
    if (task.state === 'open') {
        const name = task.assignee_name || card?.current_assignee_name || '';
        const verb = ['manager_approval', 'approval'].includes(task.kind) ? 'Согласует' : 'Исполнитель';
        if (name) {
            to = withPhoto({ name, title: `${verb}: ${name}`, role: Boolean(task.role_label) && name === task.role_label },
                name === task.assignee_name ? task.assignee_avatar_url : '');
        }
    } else if (task.state === 'done' && task.done_by_name) {
        const name = task.done_by_name;
        const verb = task.outcome === 'rejected' || card?.column === 'rejected' ? 'Отклонил' : (DONE_VERB[task.kind] || 'Выполнил');
        to = withPhoto({ name, title: `${verb}: ${name}`, role: Boolean(task.role_label) && name === task.role_label }, task.done_by_avatar_url);
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
    // Бухгалтерия на «Согласовании» — это уточнение компании, а не согласование.
    const stage = request?.status === 'active' && request.current_kind === 'entity_clarify' ? 'Уточнение компании' : stageLine(request);
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
   здесь только подсказка. `fileKinds` — виды уже приложенных и выбранных файлов.

   Пункт — { key, text }: `text` — что сделать, словами; `key` — поле формы, к
   которому пункт относится (`anchor` у Field). Нажали «Отправить», а чего-то не
   хватает, — форма обводит эти поля красным, пишет под каждым, чего в нём нет, и
   подводит прокрутку к первому (просьба владельца 14.10.2026). У позиций и
   поставщиков есть ещё `part` и `rows` — что именно подсветить внутри списка.
   Строки (`…Problems`) — те же пункты одним текстом. */
const issueOf = (key, text, extra = null) => (extra ? { key, text, ...extra } : { key, text });
const issueTexts = (issues) => issues.map((item) => item.text);

/* Чего не хватает, чтобы отправить заявку. `staffed` — у каких ролей есть участники
   ({finance, asset_keeper}): способ оплаты или категорию учёта, которые некому
   выполнить, сервер не примет — человек должен узнать об этом до отправки. */
export const purchaseIssues = (draft, { minSuppliers = 3, fileKinds = [], hasCard = false, staffed = {} } = {}) => {
    const issues = [];
    const add = (key, text, extra) => issues.push(issueOf(key, text, extra));
    // Компании здесь нет намеренно (просьба владельца 15.10.2026): не знает её инициатор —
    // её укажет бухгалтерия после отправки (workflow.needs_entity_step).
    if (!draft.department_id) add('department_id', 'укажите подразделение');
    if (!String(draft.expense_name || '').trim()) add('expense_name', 'назовите закуп');
    if (!draft.category_id) add('category_id', 'выберите категорию закупа');
    if (!String(draft.justification || '').trim()) add('justification', 'опишите и обоснуйте закуп');
    const filled = (draft.items || []).filter((item) => !isBlankItem(item));
    if (!filled.length) add('items', 'добавьте хотя бы одну позицию', { part: 'empty' });
    else {
        const broken = filled.find((item) => itemIssue(item));
        if (broken) {
            const { field, text } = itemIssue(broken);
            add('items', text, { part: 'row', rows: [broken.key], field });
        } else if (!(itemsTotal(filled) > 0)) {
            add('items', 'укажите цену за единицу', { part: 'price', rows: filled.map((item) => item.key) });
        }
    }
    if (!draft.due_on) add('due_on', 'укажите желаемый срок');
    if (!draft.object_type) add('object_type', 'выберите: товар или услуга');
    else if (draft.object_type === 'goods' && !draft.accounting_category) add('accounting_category', 'выберите категорию учёта');
    else if (draft.object_type === 'goods' && draft.accounting_category === 'asset' && staffed.asset_keeper === false) {
        add('accounting_category', UNSTAFFED.asset_keeper);
    }
    if (!draft.payment_method) add('payment_method', 'выберите способ оплаты');

    const offers = (draft.offers || []).filter((offer) => !isBlankOffer(offer));
    const incomplete = offers.filter((offer) => !offerName(offer) || !(parseAmount(offer.amount) > 0));
    if (draft.no_alternatives) {
        if (!draft.no_alternatives_reason) add('no_alternatives_reason', 'выберите причину отсутствия альтернатив');
        else if (draft.no_alternatives_reason === 'other' && !String(draft.no_alternatives_comment || '').trim()) {
            add('no_alternatives_comment', 'опишите причину отсутствия альтернатив');
        }
        if (!offers.length) add('offers', 'укажите поставщика и стоимость', { part: 'empty' });
    } else if (offers.length < minSuppliers) {
        add('offers', `нужно не меньше ${plural(minSuppliers, ['варианта поставщика', 'вариантов поставщиков', 'вариантов поставщиков'])}`,
            { part: 'count' });
    }
    if (incomplete.length) {
        add('offers', 'у каждого поставщика укажите название и стоимость', { part: 'incomplete', rows: incomplete.map((offer) => offer.key) });
    }
    // Единственный поставщик («альтернатив нет») рекомендуем по определению —
    // отмечать его и обосновывать выбор не из чего.
    if (offers.length && !draft.no_alternatives) {
        if (offers.filter((offer) => offer.is_recommended).length !== 1) add('offers', 'отметьте рекомендуемого поставщика', { part: 'recommend' });
        if (!String(draft.supplier_choice_reason || '').trim()) add('supplier_choice_reason', 'обоснуйте выбор поставщика');
    }

    if (draft.payment_method === 'card') {
        if (staffed.finance === false) add('payment_method', UNSTAFFED.finance);
        if (!draft.card_recipient) add('card_recipient', 'выберите, чью карту пополнить');
        // У каждого получателя своё поле: сотрудник выбирается из списка, владельца
        // карты поставщика вписывают — заполненное «чужое» поле не считается.
        else if (draft.card_recipient === 'employee' && !draft.card_holder_user_id) add('card_holder_user_id', 'укажите сотрудника');
        else if (draft.card_recipient === 'supplier' && !String(draft.card_holder_name || '').trim()) {
            add('card_holder_name', 'укажите ФИО владельца карты');
        }
        if (!hasCard && !draft.card_id && cardProblem(draft.card_number)) add('card_number', 'укажите полный номер карты');
        if (!String(draft.payment_purpose || '').trim()) add('payment_purpose', 'укажите назначение перевода');
    } else if (draft.payment_method === 'invoice') {
        // Счёт у нового закупа обычно появляется после согласования; но если его
        // уже прикладывают — номер и дата нужны сразу.
        const started = fileKinds.includes('invoice') || String(draft.invoice_number || '').trim() || draft.invoice_date;
        if (started) issues.push(...invoiceIssues(draft, fileKinds));
    }
    return issues;
};

export const purchaseProblems = (draft, options) => issueTexts(purchaseIssues(draft, options));

/* Способ оплаты или категория учёта, для которых в «Участниках» нет исполнителя. */
export const UNSTAFFED = {
    finance: 'пополнение карты пока некому выполнить: финансовый отдел не назначен — сообщите администратору раздела',
    asset_keeper: 'имущество пока некому поставить на учёт: ответственный не назначен — сообщите администратору раздела',
};

/* Регулярный платёж: `template` — строка справочника. Поставщика, компанию и
   категорию учёта товара задаёт она — исправить их в заявке человек не может. */
export const regularIssues = (draft, { fileKinds = [], template = null, staffed = {} } = {}) => {
    const issues = [];
    // Поставщика, компанию и категорию учёта задаёт строка справочника — пункты о
    // них стоят у выбора платежа: в самой заявке этих полей нет.
    if (!draft.fixed_template_id) issues.push(issueOf('fixed_template_id', 'выберите регулярный платёж'));
    if (template) {
        const gaps = [
            !template.counterparty_id && 'поставщик',
            !template.legal_entity_id && 'компания',
            template.object_type === 'goods' && !template.accounting_category && 'категория учёта товара',
        ].filter(Boolean);
        if (gaps.length) {
            issues.push(issueOf('fixed_template_id', `в справочнике у платежа не указаны: ${gaps.join(', ')} — попросите администратора дополнить`));
        } else if (template.accounting_category === 'asset' && staffed.asset_keeper === false) {
            issues.push(issueOf('fixed_template_id', UNSTAFFED.asset_keeper));
        }
    }
    if (!draft.department_id) issues.push(issueOf('department_id', 'укажите подразделение'));
    if (!String(draft.payment_period || '').trim()) issues.push(issueOf('payment_period', 'укажите период оплаты'));
    if (!(parseAmount(draft.amount) > 0)) issues.push(issueOf('amount', 'укажите сумму'));
    issues.push(...invoiceIssues(draft, fileKinds));
    return issues;
};

export const regularProblems = (draft, options) => issueTexts(regularIssues(draft, options));

/* Заявка, принятая ещё первой версией процесса (у неё есть `legacy_step`): трёх
   поставщиков и обоснования от неё не требовали. Нужно только то, без чего её
   не оплатить, — как и на сервере (workflow.missing_for_submit). Сумма у закупа —
   итог позиций, у регулярного платежа — своё поле. */
export const legacyIssues = (draft, { fileKinds = [] } = {}) => {
    const issues = [];
    if (!draft.legal_entity_id) issues.push(issueOf('legal_entity_id', 'выберите компанию'));
    if (!(draftTotal(draft) > 0)) {
        issues.push(draft.request_kind === 'regular' ? issueOf('amount', 'укажите сумму') : issueOf('items', 'укажите сумму', { part: 'price' }));
    }
    if (draft.payment_method !== 'card') issues.push(...invoiceIssues(draft, fileKinds));
    return issues;
};

export const legacyProblems = (draft, options) => issueTexts(legacyIssues(draft, options));

const draftTotal = (draft) => (draft.request_kind === 'regular'
    ? parseAmount(draft.amount)
    : itemsTotal((draft.items || []).filter((item) => !isBlankItem(item))));

export const invoiceIssues = (draft, fileKinds = []) => {
    const issues = [];
    if (!fileKinds.includes('invoice')) issues.push(issueOf('invoice_file', 'приложите счёт'));
    if (!String(draft.invoice_number || '').trim()) issues.push(issueOf('invoice_number', 'укажите номер счёта'));
    if (!draft.invoice_date) issues.push(issueOf('invoice_date', 'укажите дату счёта'));
    return issues;
};

export const invoiceProblems = (draft, fileKinds = []) => issueTexts(invoiceIssues(draft, fileKinds));

/* ── Пункты «чего не хватает» на экране ────────────────────────────────────────
   Что писать под полем: пункты одного поля одной фразой, с заглавной буквы. */
export const issueSentence = (texts) => {
    const text = (Array.isArray(texts) ? texts : [texts]).filter(Boolean).join('; ');
    return text ? text[0].toUpperCase() + text.slice(1) : '';
};

/* Пункты по полям: { ключ поля → [пункты] } — по нему поля узнают свои пункты. */
export const issuesByKey = (issues) => {
    const map = {};
    (issues || []).forEach((item) => { (map[item.key] ||= []).push(item); });
    return map;
};

/* Поля, к которым стоят пункты, — в том порядке, в каком они идут на экране
   (`order` — ключи полей сверху вниз; чего в нём нет — в конце, по порядку пунктов).
   Стрелки переходят по ним, а не по пунктам: у одного поля пунктов бывает несколько. */
export const issueKeys = (issues, order = []) => {
    const keys = [];
    (issues || []).forEach((item) => { if (!keys.includes(item.key)) keys.push(item.key); });
    const place = (key) => { const index = order.indexOf(key); return index < 0 ? order.length : index; };
    return keys.map((key, index) => [key, index]).sort((a, b) => place(a[0]) - place(b[0]) || a[1] - b[1]).map(([key]) => key);
};

/* Куда перейти стрелкой из поля `current`: `step` = 1 — к следующему, −1 — к
   предыдущему, по кругу. `current` мог уже исправиться и выйти из списка — тогда
   соседи ищутся по его месту на экране (`order`). Нет `current` — с первого. */
export const issueStep = (keys, current, step, order = []) => {
    if (!keys.length) return null;
    if (current === null || current === undefined) return step < 0 ? keys[keys.length - 1] : keys[0];
    const index = keys.indexOf(current);
    if (index >= 0) return keys[(index + step + keys.length) % keys.length];
    const place = order.indexOf(current);
    if (place < 0) return step < 0 ? keys[keys.length - 1] : keys[0];
    const at = (key) => { const value = order.indexOf(key); return value < 0 ? Infinity : value; };
    if (step > 0) return keys.find((key) => at(key) > place) ?? keys[0];
    return [...keys].reverse().find((key) => at(key) < place) ?? keys[keys.length - 1];
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
            const changed = (payload.changed || []).join(', ');
            // Компанию не указали — сначала заявка у бухгалтерии (просьба владельца 15.10.2026).
            if (payload.entity_step) {
                if (payload.first) return 'Отправлена: компанию уточнит бухгалтерия';
                return changed
                    ? `Отправлена заново: изменено — ${changed}; компанию уточнит бухгалтерия`
                    : 'Отправлена заново: компанию уточнит бухгалтерия';
            }
            if (payload.first) return 'Отправлена на согласование';
            return changed
                ? `Отправлена на согласование заново: изменено — ${changed}`
                : 'Отправлена на согласование заново';
        }
        case 'clarified': return `Инициатор ответил${of}`;
        case 'entity_set': return payload.legal_entity_name ? `Указана компания: ${payload.legal_entity_name}` : 'Указана компания';
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
        case 'observers': {
            const names = (list) => (list || []).map((person) => person?.name).filter(Boolean);
            const added = names(payload.added);
            const removed = names(payload.removed);
            if (added.length && removed.length) {
                return `Наблюдатели: ${added.length > 1 ? 'добавлены' : 'добавлен'} — ${added.join(', ')}; `
                    + `${removed.length > 1 ? 'сняты' : 'снят'} — ${removed.join(', ')}`;
            }
            if (added.length) return `${added.length > 1 ? 'Добавлены наблюдатели' : 'Добавлен наблюдатель'}: ${added.join(', ')}`;
            if (removed.length) return `${removed.length > 1 ? 'Сняты наблюдатели' : 'Снят наблюдатель'}: ${removed.join(', ')}`;
            return 'Изменены наблюдатели';
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

/* ── Почта поставщиков и письма о смене реквизитов (просьба владельца 20.10.2026) ──
   Те же правила, что у сервера (payments/mail.py, payments/requisites_mail.py):
   форма подсвечивает ошибку и предупреждает о письме до того, как сервер ответит.
   Таблицы случаев — общие у tests/payments_requisites_mail.test.mjs и
   tests/test_payments_requisites_mail.py: правило меняется в обоих местах. */
export const MAX_SUPPLIER_EMAILS = 5;
const EMAIL_RE = /^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;
/* Невидимое из Telegram, сайтов и Word убирается до разбора; разделители — явным набором,
   тем же, что у сервера (у Python и JavaScript «пробел» понимается по-разному). */
const EMAIL_INVISIBLE_RE = /[\u200b\u200c\u200d\u2060\ufeff\u00ad]/g;
const EMAIL_SPLIT_RE = /[ \t\n\r\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000,;]+/;

const cleanEmail = (part) => {
    let token = part.replace(/^[<>]+|[<>]+$/g, '');
    if (/^mailto:/i.test(token)) token = token.slice(7);
    token = token.replace(/\.+$/, '').toLowerCase();
    if (!token) return '';
    const local = token.split('@')[0];
    if (token.length > 254 || !EMAIL_RE.test(token) || token.includes('..') || local.startsWith('.') || local.endsWith('.')) return null;
    return token;
};

/* Адреса из поля «Почта»: через запятую, точку с запятой, пробел или перенос строки.
   { emails, bad, tooMany }: bad — первое, что не похоже на адрес (пусто — всё верно). */
export const parseEmails = (value) => {
    const emails = [];
    const parts = String(value ?? '').replace(EMAIL_INVISIBLE_RE, '').split(EMAIL_SPLIT_RE).filter(Boolean);
    for (const part of parts) {
        const token = cleanEmail(part);
        if (token === null) return { emails, bad: part, tooMany: false };
        if (token && !emails.includes(token)) emails.push(token);
    }
    return { emails, bad: '', tooMany: emails.length > MAX_SUPPLIER_EMAILS };
};

/* Что не так с полем «Почта» — строкой под полем; пусто — всё верно. */
export const emailsIssue = (value) => {
    const { bad, tooMany } = parseEmails(value);
    if (bad) return `«${bad.slice(0, 80)}» — не похоже на адрес почты`;
    return tooMany ? `Не больше ${MAX_SUPPLIER_EMAILS} адресов у одного поставщика` : '';
};

/* Почта поставщика в строке списка: первый адрес и «+N». */
export const emailsShort = (emails) => {
    const list = emails || [];
    if (!list.length) return '';
    return list.length > 1 ? `${list[0]} +${list.length - 1}` : list[0];
};

/* Поля реквизитов, правка которых уводит письмо поставщикам, — те, что идут в
   «Скопировать реквизиты». Форма (ТОО/ИП) и примечание писем не вызывают.
   ИИК и БИН сравниваются так, как их пишет сервер: ИИК — верхним регистром без
   пробелов, БИН — цифрами. */
export const REQUISITE_FIELDS = ['name', 'bin', 'legal_address', 'iik', 'bank_name', 'bik', 'kbe', 'vat_payer', 'requisites'];
const MULTILINE_REQUISITES = ['legal_address', 'requisites'];
const requisiteValue = (field, value) => {
    if (field === 'vat_payer') return Boolean(value);
    const text = String(value ?? '').replace(/\r/g, '');
    if (field === 'iik') return text.split(/\s+/).join('').toUpperCase();
    if (field === 'bin') return text.replace(/[^\p{Nd}]/gu, '');
    return MULTILINE_REQUISITES.includes(field) ? text.split('\n').map((line) => line.trim()).join('\n').trim() : text.trim();
};
export const requisitesChanged = (before, after) => REQUISITE_FIELDS
    .filter((field) => requisiteValue(field, before?.[field]) !== requisiteValue(field, after?.[field]));

/* «Сейчас» по часам Алматы строкой «YYYY-MM-DDTHH:MM:SS» — как время писем от сервера. */
export const almatyNow = () => new Date(Date.now() + 5 * 60 * 60 * 1000).toISOString().slice(0, 19);
const dueBy = (value, now) => Boolean(value) && String(value).slice(0, 19) <= now;

/* Когда уйдёт письмо: «в 14:35» сегодня, «21.10 в 14:35» — в другой день (часы Алматы). */
export const mailingWhen = (value, today = todayISO()) => {
    const parts = dateParts(value);
    if (!parts || !parts.hh) return '';
    const time = `${parts.hh}:${parts.mm}`;
    return `${parts.y}-${parts.m}-${parts.d}` === today ? `в ${time}` : `${parts.d}.${parts.m} в ${time}`;
};

/* Метка письма в строке компании — только то, что ждёт внимания: письмо скоро уйдёт,
   уходит, не уходит (почта не подключена или не пускает) или ушло не на все адреса.
   Ушедшее на все — без метки. Срок прошёл — «уходит», а не время в прошлом. */
export const mailingPill = (state, { ready = true, today, now = almatyNow() } = {}) => {
    if (!state) return null;
    if (state.status === 'pending') {
        if (!ready) return { tone: 'blocked', label: 'письмо ждёт почту' };
        if (state.last_error) return { tone: 'blocked', label: 'письмо не уходит' };
        return { tone: 'current', label: dueBy(state.send_after, now) ? 'письмо уходит' : `письмо ${mailingWhen(state.send_after, today)}` };
    }
    if (state.status === 'sending') return state.last_error || !ready ? { tone: 'blocked', label: 'письмо не уходит' } : { tone: 'current', label: 'письмо уходит' };
    if (state.status === 'done' && state.failed > 0) return { tone: 'overdue', label: `письмо не ушло: ${state.failed}` };
    return null;
};

const ADDRESS_FORMS = ['адрес', 'адреса', 'адресов'];
// «из 1 адреса», «из 2 адресов», «из 21 адреса» — после «из» родительный падеж.
const ADDRESS_FROM_FORMS = ['адреса', 'адресов', 'адресов'];
const NOTICE_NAMES = 3;

/* «через 1 минуту», «через 2 минуты», «через 10 минут». */
export const minutesWord = (count) => plural(count, ['минуту', 'минуты', 'минут']);

/* «Изменилось: ИИК, банк» — сокращения остаются заглавными. */
export const changedLine = (labels) => (labels || [])
    .map((label) => (label === label.toUpperCase() ? label : label.toLowerCase()))
    .join(', ');

/* Письмо из истории одной строкой: когда и чем кончилось. «Отправлено» — письмо
   принял почтовый сервер; отказ «такого адреса нет» может прийти позже в сам ящик. */
export const mailingSummary = (item, { ready = true, today, now = almatyNow() } = {}) => {
    const counts = item?.counts || {};
    const sent = counts.sent || 0;
    switch (item?.status) {
        case 'pending':
            if (!ready) return 'Ждёт подключения почты';
            if (item.last_error) return 'Не уходит';
            return dueBy(item.send_after, now) ? 'Уходит' : `Уйдёт ${mailingWhen(item.send_after, today)}`;
        case 'sending':
            return `${item.last_error || !ready ? 'Не уходит' : 'Отправляется'}: ${sent} из ${plural(counts.total || 0, ADDRESS_FROM_FORMS)}`;
        case 'done': {
            const removed = counts.removed || 0;
            const superseded = counts.superseded || 0;
            if (!counts.total && !removed && !superseded) return 'Некому было отправить: ни у одного поставщика нет почты';
            const missed = (counts.failed || 0) + removed;
            const parts = [missed ? `Отправлено ${sent} из ${plural(sent + missed, ADDRESS_FROM_FORMS)}` : `Отправлено на ${plural(sent, ADDRESS_FORMS)}`];
            if (counts.failed) parts.push(`не ушло: ${counts.failed}`);
            if (removed) parts.push(`адрес убран: ${removed}`);
            if (superseded) parts.push(`ещё ${plural(superseded, ADDRESS_FORMS)} — новым письмом`);
            return parts.join(' · ');
        }
        case 'cancelled':
            return `Отменено${item.cancelled_by_name ? ` · ${item.cancelled_by_name}` : ''}`;
        default:
            return '';
    }
};

/* Строка перед сохранением изменённых реквизитов: уйдёт ли письмо и когда, сколько
   адресов его получат и у кого почты нет. `waiting` — письмо уже ждёт отправки;
   `reverting` — реквизиты вернутся к тем, что знают поставщики, и письма не будет. */
export const mailNotice = ({ recipients, delayMinutes = 10, ready = true, waiting = false, reverting = false } = {}) => {
    if (reverting) {
        return waiting
            ? 'Реквизиты вернутся к тем, что знают поставщики, — ждущее письмо не уйдёт.'
            : 'Реквизиты вернутся к тем, что знают поставщики, — письма не будет.';
    }
    const addresses = recipients?.addresses || 0;
    const without = recipients?.without_email || 0;
    const names = recipients?.without_names || [];
    let first;
    if (!ready) {
        first = waiting
            ? 'Письмо, которое ждёт отправки, уйдёт уже с этими реквизитами, когда у компании заработает почта для писем.'
            : 'После сохранения письмо о новых реквизитах уйдёт поставщикам, когда у компании заработает почта для писем: сейчас с неё отправлять нельзя.';
    } else {
        first = waiting
            ? `Письмо, которое ждёт отправки, уйдёт уже с этими реквизитами — через ${minutesWord(delayMinutes)} после сохранения.`
            : `После сохранения поставщикам уйдёт письмо о новых реквизитах — через ${minutesWord(delayMinutes)}.`;
    }
    const parts = [first];
    if (!addresses && recipients?.excluded) {
        parts.push('Но все поставщики с почтой сняты в «Кому» — письмо некому отправить.');
    } else if (!addresses) {
        parts.push('Но сейчас ни у одного поставщика не указана почта — письмо некому отправить.');
    } else {
        parts.push(`Получат ${plural(addresses, ADDRESS_FORMS)}.`);
        if (without) {
            // Названий — не больше трёх: двадцать имён подряд прячут главное.
            const shown = names.slice(0, NOTICE_NAMES);
            const rest = without - shown.length;
            parts.push(`Без почты — ${plural(without, ['поставщик', 'поставщика', 'поставщиков'])}: ${shown.join(', ')}${rest > 0 ? ` и ещё ${rest}` : ''}.`);
        }
    }
    return parts.join(' ');
};

/* ── «Письма поставщикам» — журнал писем раздела (просьба владельца 20.10.2026:
   «всё же должно быть внутри раздела Оплата счетов») ─────────────────────────── */
export const LETTER_FORMS = ['письмо', 'письма', 'писем'];
const MONTHS_GENITIVE = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

const shiftDay = (iso, days) => {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
};

/* Момент письма для списка: ждёт — когда уйдёт, отменено — когда отменили, иначе — когда ушло. */
export const letterMoment = (item) => {
    if (item?.status === 'pending') return item.send_after;
    if (item?.status === 'cancelled') return item.cancelled_at || item.updated_at;
    return item?.started_at || item?.updated_at || item?.created_at;
};

/* Подпись дня над письмами, как в «Почте»: «Сегодня», «Вчера», «20 октября», «3 марта 2025». */
export const letterDay = (value, today = todayISO()) => {
    const parts = dateParts(value);
    if (!parts) return '';
    const iso = `${parts.y}-${parts.m}-${parts.d}`;
    if (iso === today) return 'Сегодня';
    if (iso === shiftDay(today, -1)) return 'Вчера';
    const label = `${Number(parts.d)} ${MONTHS_GENITIVE[Number(parts.m) - 1]}`;
    return parts.y === today.slice(0, 4) ? label : `${label} ${parts.y}`;
};

/* Время в строке письма: сегодня — «16:43», в другой день — «20.10». */
export const letterTime = (value, today = todayISO()) => {
    const parts = dateParts(value);
    if (!parts) return '';
    return `${parts.y}-${parts.m}-${parts.d}` === today && parts.hh ? `${parts.hh}:${parts.mm}` : `${parts.d}.${parts.m}`;
};

/* Кружок отправителя — буквы названия компании без «ТОО», «ИП» и кавычек: «ИП Иванов» → «И». */
export const letterInitials = (name) => {
    const words = String(name || '').replace(/[«»"'“”]/g, ' ').split(/\s+/)
        .filter((word) => word && !/^(ТОО|ИП|АО|ООО|ОАО|ЗАО|ТДО|КХ|LLP|LLC)$/i.test(word));
    return words.slice(0, 2).map((word) => word[0]).join('').toUpperCase() || '·';
};

/* Как дела у адреса: ушло, ждёт, повтор, не ушло, убран, ушло новым письмом. */
export const recipientState = (recipient, { today } = {}) => {
    switch (recipient?.status) {
        case 'sent': {
            const parts = dateParts(recipient.sent_at);
            return { tone: 'done', label: parts?.hh ? `ушло в ${parts.hh}:${parts.mm}` : 'ушло' };
        }
        case 'failed':
            return recipient.next_attempt_at
                ? { tone: 'blocked', label: `повтор ${mailingWhen(recipient.next_attempt_at, today)}` }
                : { tone: 'overdue', label: 'не ушло' };
        case 'removed':
            return { tone: 'neutral', label: 'адрес убран' };
        case 'superseded':
            return { tone: 'neutral', label: 'новым письмом' };
        default:
            return { tone: 'current', label: 'ждёт' };
    }
};

/* ── 21.10.2026: почта у каждой компании, галочки «Кому», «Почта поставщиков» ── */

const SUPPLIER_FORMS = ['поставщик', 'поставщика', 'поставщиков'];
const SUPPLIER_FROM_FORMS = ['поставщика', 'поставщиков', 'поставщиков'];

/* Строка под адресом подключённой почты: «Gmail · проверено в 11:40». */
export const mailboxLine = (box, today = todayISO()) => {
    if (!box) return '';
    const server = box.label || (box.host ? `${box.host}:${box.port}` : '');
    const checked = box.checked_at ? `проверено ${mailingWhen(box.checked_at, today)}` : '';
    return [server, checked].filter(Boolean).join(' · ');
};

/* Какая это почта — подпись под адресом, пока его вводят: «Gmail · smtp.gmail.com». */
export const serverLine = (server) => (server ? `${server.label} · ${server.host}` : '');

/* Галочки «Кому» у ждущего письма: сколько поставщиков отмечено и сколько адресов получат. */
export const recipientsChosen = (candidates) => {
    const list = candidates || [];
    const chosen = list.filter((item) => item.send);
    const addresses = new Set(chosen.flatMap((item) => item.emails || []));
    return { chosen: chosen.length, total: list.length, addresses: addresses.size };
};

/* «все 14 поставщиков · 15 адресов», «12 из 14 поставщиков · 13 адресов», «никому». */
export const recipientsLine = (candidates) => {
    const { chosen, total, addresses } = recipientsChosen(candidates);
    if (!total) return 'ни у одного поставщика нет почты';
    if (!chosen) return 'никому — письмо не уйдёт';
    let who;
    if (chosen === total) who = total === 1 ? plural(1, SUPPLIER_FORMS) : `все ${plural(total, SUPPLIER_FORMS)}`;
    else who = `${chosen} из ${plural(total, SUPPLIER_FROM_FORMS)}`;
    return `${who} · ${plural(addresses, ADDRESS_FORMS)}`;
};

/* Кому письмо не отправляли — у ушедшего письма. */
export const excludedLine = (names) => (names?.length ? `Не отправляли: ${names.join(', ')}` : '');

/* «Почта поставщиков»: чем кончилось последнее письмо на этот адрес. */
export const addressLetterState = (last, today = todayISO()) => {
    if (!last) return { tone: 'neutral', label: 'писем не было' };
    switch (last.status) {
        case 'sent':
            return { tone: 'done', label: last.sent_at ? `ушло ${mailingWhen(last.sent_at, today)}` : 'ушло' };
        case 'failed':
            return last.next_attempt_at
                ? { tone: 'blocked', label: `повтор ${mailingWhen(last.next_attempt_at, today)}` }
                : { tone: 'overdue', label: 'не дошло' };
        default:
            return { tone: 'current', label: 'письмо уходит' };
    }
};

/* Письма ждут почту этих компаний — строкой над журналом писем. */
export const waitingMailboxLine = (companies) => {
    const list = companies || [];
    if (!list.length) return '';
    const missing = list.filter((item) => !item.connected).map((item) => item.name);
    const broken = list.filter((item) => item.connected).map((item) => item.name);
    const parts = [];
    if (missing.length) parts.push(`Письма ждут почту: у ${missing.join(', ')} она не подключена.`);
    if (broken.length) parts.push(`Почта ${broken.join(', ')} не пускает — введите пароль заново.`);
    parts.push('Почту подключают в «Реквизитах компаний», в окне компании — блок «Почта для писем».');
    return parts.join(' ');
};

/* Нажали галочку «Кому»: кого теперь не отправлять (так это хранит сервер). */
export const toggleRecipient = (candidates, id) => {
    const list = candidates || [];
    const excluded = list.filter((item) => !item.send).map((item) => item.id);
    const target = list.find((item) => item.id === id);
    if (!target) return excluded;
    return target.send ? [...excluded, id] : excluded.filter((value) => value !== id);
};

/* «Отметить всех» — никого не исключать; «Снять всех» — исключить каждого. */
export const allRecipients = (candidates, on) => (on ? [] : (candidates || []).map((item) => item.id));
