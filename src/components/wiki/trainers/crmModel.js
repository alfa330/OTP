/* Учебная CRM без React: меню, форма обращения, поиск учётки водителя и итог
 * для статистики.
 *
 * Отдельным модулем, потому что всё это — правила, а не разметка: их гоняют
 * тесты обычным node, и они не должны тянуть за собой экраны.
 *
 * Снято с рабочей CRM 20.11.2026. Три вещи, на которых держится сходство и
 * которые нельзя «упрощать»:
 *   меню — те же восемь групп в том же порядке, даже если учебных разделов
 *   в нём три: по меню оператор и ищет, куда идти;
 *   форма «Создать обращение» — поля появляются по ходу, как в CRM: «Город»
 *   после парка, следующая категория после выбора предыдущей, поля
 *   категории, файлы, тикет и акция — после конечной категории;
 *   поиск учётки — только по ТОЧНОМУ совпадению, обрывок номера или ФИО
 *   не находят ничего. Так CRM ограничивает операторов, и учиться надо так же.
 */

import { DEFAULT_SOURCE, pathLabel, resolveChain } from './crmCatalog.js';

/* Меню CRM целиком — как в рабочей CRM 20.11.2026. Пункт — [ключ, название,
   учебный экран или null, путь страницы, ключевые слова, значок]. Путь и
   ключевые слова — как у ссылки в рабочем меню: по ним CRM ищет раздел, и по
   пути же строится адрес в строке браузера. Значок — классы рабочей CRM
   (Simple Line Icons и Font Awesome 4.7). Пункты без экрана открывают
   заглушку «раздел не входит в учебную среду». */
export const CRM_MENU = [
    {
        key: 'drivers', title: 'Водители', icon: 'icon-people', items: [
            ['drivers', 'Водители', null, 'drivers', '', 'icon-user'],
            ['accounts', 'Учетные записи водителей', 'accounts', 'driver-accounts', '', 'fa fa-user-circle-o'],
            ['team', 'Моя команда', null, 'driver-accounts/team-members', '', 'icon-people'],
            ['registered', 'Регистрация водителей', null, 'registered-drivers', '', 'icon-user-follow'],
            ['unblock_ip', 'Разблокировка водителей ИП', null, 'withdraw-blocked-drivers', '', 'icon-lock-open'],
        ],
    },
    {
        key: 'promotions', title: 'Акции', icon: 'icon-present', items: [
            ['promotions_desk', 'Подключение к акции', null, 'promotions-desk', '', 'fa fa-plug'],
            ['promotion_participants', 'Реестр участников', null, 'promotion-participants', '', 'icon-people'],
            ['promotion_backdated', 'Заявки задним числом', null, 'promotion-backdated', '', 'fa fa-history'],
            ['work_rule_actions', 'Смена условий работы', null, 'work-rule-actions', '', 'icon-refresh'],
        ],
    },
    {
        key: 'support', title: 'Поддержка', icon: 'icon-bubble', items: [
            ['support_chats', 'Чат поддержки', null, 'support-chats', '', 'icon-bubble'],
            ['ya_list', 'Обращения в Яндекс', 'ya_list', 'yandex-appeals',
                'мои обращения поддержка яндекса диспетчерская тикеты', 'icon-earphones-alt'],
        ],
    },
    {
        key: 'coupons', title: 'Купоны', icon: 'fa fa-ticket', items: [
            ['coupon_desk', 'Купоны — активация', null, 'coupon-desk', '', 'fa fa-toggle-on'],
        ],
    },
    {
        key: 'mailings', title: 'Рассылки', icon: 'icon-envelope', items: [
            ['proposed_offers', 'Офферы по номеру', null, 'proposed-offers/lookup',
                'кц колл-центр телефон номер оффер предложение история', 'fa fa-search'],
        ],
    },
    {
        key: 'content', title: 'Контент', icon: 'icon-screen-desktop', items: [
            ['news_feeds', 'Новостная лента', null, 'news-feeds', '', 'icon-feed'],
        ],
    },
    {
        key: 'tickets', title: 'Тикетная система', icon: 'icon-list', items: [
            ['requests', 'Обращения', 'requests', 'list-requests', '', 'fa fa-inbox'],
            ['list_tickets', 'Тикеты', null, 'list-tickets', '', 'fa fa-ticket'],
            ['notifications', 'Уведомления', null, 'notifications', '', 'icon-bell'],
        ],
    },
    {
        key: 'edo', title: 'ЭДО', icon: 'icon-doc', items: [
            ['edo_signings', 'Подписание в приложении', null, 'edo-signings', '', 'icon-pencil'],
            ['driver_edo', 'ЭДО водителей', null, 'driver-edo', '', 'icon-doc'],
            ['provider_switch', 'Смена провайдера', null, 'driver-edo/provider-switch', '', 'icon-refresh'],
            ['provider_dashboard', 'Дашборд смены провайдера', null, 'driver-edo/provider-switch/dashboard', '', 'icon-chart'],
            ['edo_dashboard', 'Дашборд ЭДО', null, 'driver-edo/dashboard', '', 'icon-chart'],
            ['sapar_iins', 'Проверка ИИН (Сапар)', null, 'sapar-iins/check',
                'иин сапар документы поступили проверка кц', 'fa fa-search'],
        ],
    },
];

/* Счётчики у пунктов: у «Тикетов» — плашка badge-info «Мои в работе + очередь
   отдела», у «Уведомлений» — красный кружок непрочитанных. Числа учебные. */
export const CRM_BADGES = { list_tickets: ['count', 12], notifications: ['alert', 3] };

/* Учебные экраны и пункт меню, который при них подсвечен. */
export const CRM_VIEWS = {
    requests: 'requests',
    request_new: 'requests',
    request_show: 'requests',
    accounts: 'accounts',
    account: 'accounts',
    ya_list: 'ya_list',
    ya_new: 'ya_list',
    ya_show: 'ya_list',
};

/** Пункт меню, который подсвечен на экране; на заглушке — сам пункт. */
export const activeMenuItem = (world) => (world.crmView === 'stub'
    ? world.crmStub
    : CRM_VIEWS[world.crmView] || 'requests');

const menuItem = (itemKey) => {
    for (const group of CRM_MENU) {
        const found = group.items.find(([key]) => key === itemKey);
        if (found) return found;
    }
    return null;
};

/** Группа меню, в которой лежит пункт. */
export const groupOfItem = (itemKey) => (CRM_MENU.find(
    (group) => group.items.some(([key]) => key === itemKey)) || CRM_MENU[0]).key;

/*
 * Страницы, на которые ведут кнопки CRM, но которых нет в учебной среде:
 * [заголовок, путь в CRM, пояснение]. Почти все они МЕНЯЮТ данные водителя —
 * поэтому и не открываются: учебная среда ничего не меняет.
 */
export const CRM_STUBS = {
    home: ['Главная', '', 'Главная страница CRM в учебную среду не входит: разделы открываются из меню слева.'],
    smz: ['Перевод в СМЗ', 'self-employed-transfer',
        'В рабочей CRM здесь водителя переводят в самозанятые — это меняет его тип сотрудничества в Диспетчерской.'],
    individual: ['Перерегистрация в физическое лицо', 'individual-transfer',
        'В рабочей CRM здесь водителя-ИП или самозанятого перерегистрируют в физическое лицо.'],
    limit: ['Управление лимитом', 'limit', 'В рабочей CRM здесь меняют лимит по счёту водителя.'],
    car_editor: ['Редактор автомобиля', 'car-editor', 'В рабочей CRM здесь правят данные автомобиля в Диспетчерской.'],
    car_binding: ['Сменить или отвязать автомобиль', 'car-binding', 'В рабочей CRM здесь меняют автомобиль водителя или отвязывают его.'],
    check_driver: ['Проверить водителя', 'check', 'В рабочей CRM здесь запускают проверку водителя по базам.'],
    request_edit: ['Редактирование обращения', 'edit', 'В рабочей CRM здесь правят сохранённое обращение.'],
    crm_profile: ['Profile', 'profile', 'Профиль учётки CRM в учебную среду не входит.'],
    crm_password: ['Password', 'password', 'Смена пароля учётки CRM в учебную среду не входит.'],
    crm_logout: ['Выйти', 'logout', 'Из учебной CRM не выходят: закройте тренажёр, когда закончите.'],
};

/** Название пункта меню или страницы-заглушки по ключу. */
export const menuTitle = (itemKey) => menuItem(itemKey)?.[1] || CRM_STUBS[itemKey]?.[0] || '';

/* ── «Поиск раздела» — тем же способом, что в рабочей CRM ──────────────────
   Регистр не важен, «ё» = «е»; запрос делится на слова, у русских слов от
   четырёх букв отрезается окончание («обращение» найдёт «Обращения»);
   набранное не в той раскладке тоже ищется. Пункт ищется по названию,
   названию группы, ключевым словам и пути страницы. Подходит пункт, в
   котором есть ВСЕ слова; группа без подходящих пунктов прячется. */
const LAYOUT_EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./";
const LAYOUT_RU = 'ёйцукенгшщзхъфывапролджэячсмитьбю.';
const ENDING = /(ами|ями|ов|ев|ей|ий|ый|ой|ая|яя|ое|ее|ые|ие|ам|ям|ах|ях|ом|ем|[аяоеуюыиьй])$/;

const norm = (text) => String(text || '').toLowerCase().replace(/ё/g, 'е');
const swapLayout = (text, from, to) => Array.from(text)
    .map((char) => { const at = from.indexOf(char); return at === -1 ? char : to[at]; }).join('');
const stem = (word) => {
    if (!/^[а-я]{4,}$/.test(word)) return word;
    const cut = word.replace(ENDING, '');
    return cut.length >= 3 ? cut : word;
};
const searchWords = (text) => text.split(/\s+/).filter(Boolean).map(stem);
const itemText = (group, [, title, , path = '', keywords = '']) => norm(
    [title, group.title, keywords, path, path.replace(/[-/_]+/g, ' ')].join(' '));

/* Поиск раздела целиком: найденные группы и слова, которыми нашли (их же
   CRM подсвечивает в названиях). Пустой запрос — всё меню без подсветки. */
export const menuSearch = (query) => {
    const base = norm(String(query || '').trim());
    if (!base) return { groups: CRM_MENU, words: [] };
    const variants = [base];
    for (const alt of [norm(swapLayout(base, LAYOUT_EN, LAYOUT_RU)), swapLayout(base, LAYOUT_RU, LAYOUT_EN)]) {
        if (!variants.includes(alt)) variants.push(alt);
    }
    for (const variant of variants) {
        const words = searchWords(variant);
        if (!words.length) continue;
        const groups = CRM_MENU
            .map((group) => ({
                ...group,
                items: group.items.filter((item) => words.every((word) => itemText(group, item).includes(word))),
            }))
            .filter((group) => group.items.length);
        if (groups.length) return { groups, words };
    }
    return { groups: [], words: [] };
};

export const filterMenu = (query) => menuSearch(query).groups;

/** Адрес в строке браузера для текущего экрана CRM. */
export const crmUrl = (world) => {
    const base = 'backend.yataxi.kz/admin';
    switch (world.crmView) {
    case 'requests': return `${base}/list-requests`;
    case 'accounts': return `${base}/driver-accounts`;
    case 'account': return `${base}/driver-accounts/${world.drvOpenNumber || CRM_ACCOUNT_ID}`;
    case 'request_show': return `${base}/list-requests/${world.crmShown?.id || ''}`;
    case 'ya_list': return `${base}/yandex-appeals`;
    case 'ya_new': return `${base}/yandex-appeals/create`;
    case 'ya_show': return `${base}/yandex-appeals/${world.yaOpen || ''}`;
    case 'stub': {
        const own = menuItem(world.crmStub)?.[3];
        if (own !== undefined) return `${base}/${own}`;
        const path = CRM_STUBS[world.crmStub]?.[1] || '';
        /* Кнопки карточки водителя ведут на страницы ЕГО учётки. */
        const driver = ['smz', 'individual', 'limit', 'car_editor', 'car_binding', 'check_driver'].includes(world.crmStub);
        return driver ? `${base}/driver-accounts/${world.drvOpenNumber || CRM_ACCOUNT_ID}/${path}` : `${base}${path ? `/${path}` : ''}`;
    }
    default: return `${base}/list-requests/create`;
    }
};

/* ── Форма «Создать обращение» ─────────────────────────────────────────── */

/**
 * Пустая форма. «Звонок/Чат» сразу стоит на «Звонок» — так CRM открывает
 * форму у оператора линии. cats — НОМЕРА выбранных категорий от корня (см.
 * crmCatalog.resolveChain). Поля категории (info) — по названию поля; при
 * смене категории они очищаются, как в CRM.
 */
export const emptyForm = (prefill = {}) => ({
    source: DEFAULT_SOURCE,
    phone: '',
    license: '',
    account: '',
    park: '',
    city: '',
    cats: [],
    promotion: '',
    info: {},
    files: [],
    ticket: true,
    linked: '',
    duplicate: false,
    ...prefill,
});

/** Конечная категория формы или null, пока цепочка не дошла до листа. */
export const formLeaf = (form) => {
    const cats = form?.cats || [];
    const chain = resolveChain(cats);
    const last = chain[chain.length - 1];
    return last && last.leaf && chain.length === cats.length ? last : null;
};

/** Путь выбранных категорий — через « › », как пишет сама CRM. */
export const categoryLabel = (cats) => resolveChain(cats || []).map((node) => node.title).join(' › ');

/** Только цифры номера. */
export const digits = (value) => String(value || '').replace(/\D/g, '');

/* Номер к виду 7XXXXXXXXXX: 8 в начале и номер без кода страны — тот же
   номер. Иначе «87015550142» и «+7 701 555 01 42» были бы разными людьми. */
export const normalizePhone = (value) => {
    const raw = digits(value);
    if (raw.length === 10) return `7${raw}`;
    if (raw.length === 11 && raw[0] === '8') return `7${raw.slice(1)}`;
    return raw;
};

/* История номера под полем телефона. CRM показывает её с десяти цифр: открытые
   тикеты и последние обращения — свежие сверху. Берётся из двух мест: слепка
   дела (case.crm.history, только для номера дела) и обращений, сохранённых в
   этой же попытке: в рабочей CRM они попадают в историю сразу, и второе
   обращение того же звонка видит тикет первого. Новый тикет — «Новый». */
export const phoneHistory = (world, phone) => {
    const empty = { requests: [], open_tickets: [] };
    if (digits(phone).length < 10) return empty;
    const number = normalizePhone(phone);
    const saved = [...(world?.crmRequests || [])]
        .filter((request) => normalizePhone(request.phone) === number)
        .reverse();
    const own = saved.map((request) => ({
        id: request.id,
        category: categoryLabel(request.cats),
        when: String(request.at || '').slice(0, 16),
    }));
    const ownTickets = saved
        .filter((request) => request.ticketNo && !request.linked)
        .map((request) => ({
            id: request.ticketNo,
            category: categoryLabel(request.cats),
            status: 'Новый',
            when: String(request.at || '').slice(0, 16),
            type_id: formLeaf(request)?.id || null,
        }));
    /* История номера — из дела того водителя, чей это номер: в учебной CRM их
       несколько, и номер однофамильца не должен показывать чужие тикеты. */
    const history = caseByPhone(world, phone)?.crm?.history;
    return {
        requests: [...own, ...(history ? history.requests || [] : [])],
        open_tickets: [...ownTickets, ...(history ? history.open_tickets || [] : [])],
    };
};

/* Открытые тикеты того же номера в выбранной категории — повод присоединиться.
   Сверяем по номеру категории (type_id), как CRM: путь у двух одноимённых
   листьев одинаковый. Без type_id — по пути. */
export const sameCategoryTickets = (world, form) => {
    const leaf = formLeaf(form);
    if (!leaf || !leaf.ticket) return [];
    const label = pathLabel(leaf);
    return phoneHistory(world, form.phone).open_tickets.filter((ticket) => (ticket.type_id
        ? ticket.type_id === leaf.id
        : ticket.category === label));
};

/* Выбор категории. Сбрасывается то, что от неё зависело: поля категории и
   «присоединить к тикету» — как applyLeaf в CRM. Акция живёт, пока поле
   «Акция» остаётся на экране: из листа с акцией в другой лист с акцией CRM
   выбранную акцию не сбрасывает. cats — номера узлов от корня. */
export const withCategory = (form, cats) => {
    const next = formLeaf({ cats });
    return {
        ...form,
        cats,
        info: {},
        linked: '',
        promotion: next && next.promotion ? form.promotion : '',
    };
};

/** Можно ли сохранять: как в CRM — выбрана конечная категория. */
export const canSave = (form) => Boolean(formLeaf(form));

/**
 * Итог обращения для статистики и экрана результата: то, что человек
 * заполнил, в порядке формы. Пустое не показываем.
 */
export const crmFormFields = (form = {}) => {
    const leaf = formLeaf(form);
    const info = form.info || {};
    const fields = [
        ['Звонок/Чат', form.source],
        ['Номер телефона', form.phone],
        ['Номер В/У', form.license],
        ['ID водителя', form.account],
        ['Таксопарк', form.park],
        ['Город', form.city],
        ['Категория', categoryLabel(form.cats)],
        ['Акция', form.promotion],
    ];
    for (const field of (leaf ? leaf.fields : [])) fields.push([field.title, info[field.title]]);
    if (leaf && leaf.ticket) {
        fields.push(form.linked
            ? ['Тикет', `присоединено к №${form.linked}`]
            : ['Формировать тикет', form.ticket === false ? 'нет' : 'да']);
    }
    if ((form.files || []).length) fields.push(['Файлы', form.files.join(', ')]);
    if (form.duplicate) fields.push(['Дублировать обращение', 'да']);
    return fields.filter(([, value]) => value);
};

/* Обращение ОДНОЙ строкой — для итога аттестации. Итог попытки сервер
   хранит до ~4000 знаков (wiki/trainers.py), а по восемь строк на каждое из
   пяти обращений съели бы место разбора. Самоочевидное (канал, номер, парк,
   город, категория) — без подписи, остальное — «подпись: значение». */
const PLAIN_FIELDS = new Set(['Звонок/Чат', 'Номер телефона', 'Таксопарк', 'Город', 'Категория']);
const clip = (text, max) => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

export const crmFormLine = (form = {}) => crmFormFields(form)
    .map(([label, value]) => clip(PLAIN_FIELDS.has(label) ? String(value) : `${label}: ${value}`, 160))
    .join(' · ');

/* Последнее СОХРАНЁННОЕ обращение, а не форма на экране: после сохранения
   форма очищается под следующее обращение. Ничего не сохраняли — форма. */
export const lastSavedForm = (world) => {
    const saved = world?.crmRequests || [];
    return saved.length ? saved[saved.length - 1] : (world?.form || {});
};

/* Итог попытки. Одно обращение — его поля. Несколько («Дублировать
   обращение» — несколько обращений одного звонка) — все, с номером
   обращения в подписи: итог смены не должен терять первое. */
export const crmResultFields = (world) => {
    const saved = world?.crmRequests || [];
    if (saved.length <= 1) return crmFormFields(lastSavedForm(world));
    return saved.flatMap((form, index) => crmFormFields(form)
        .map(([label, value]) => [`${label} · №${index + 1}`, value]));
};

/* ── Учётные записи водителей ──────────────────────────────────────────── */

/* Номера учёток и водителей в CRM — учебные. У героя курса учётка 50417, у
   остальных — следом, по порядку в учебной CRM. */
const ACCOUNT_BASE = 50417;
const DRIVER_BASE = 31872;

/** Номер учётки героя — им же подписаны адреса страниц его карточки. */
export const CRM_ACCOUNT_ID = ACCOUNT_BASE;

const upper = (value) => String(value || '').toUpperCase().replace(/\s+/g, '');

/** Все дела учебной CRM: герой и остальные водители. */
export const crmCases = (world) => (world?.crmCases && world.crmCases.length
    ? world.crmCases
    : (world?.case ? [world.case] : []));

/** Дело водителя по ID в Яндексе (по умолчанию — герой). */
export const caseByAccount = (world, accountId) => crmCases(world)
    .find((item) => item.contractor?.id === accountId) || world?.case || null;

/** Дело водителя по телефону: CRM узнаёт номер с десяти цифр. */
export const caseByPhone = (world, phone) => {
    if (digits(phone).length < 10) return null;
    const number = normalizePhone(phone);
    return crmCases(world).find((item) => normalizePhone(item.contractor?.phone) === number) || null;
};

/*
 * Поиск учётки, как у оператора в CRM: ID в Яндексе, телефон, ИИН, ВУ или
 * госномер — и только ТОЧНОЕ совпадение. Обрывок номера или фамилия не
 * находят ничего: общий список оператору недоступен, и тренажёр не должен
 * приучать к поиску, которого на смене нет.
 */
export const findCrmAccounts = (world, query) => {
    const raw = String(query || '').trim();
    if (!raw) return [];
    return crmCases(world).map((item, index) => [item, index]).filter(([item]) => {
        const person = item.contractor || {};
        const car = item.car || {};
        return upper(raw) === upper(person.id)
            || (normalizePhone(raw).length === 11 && normalizePhone(raw) === normalizePhone(person.phone))
            || (digits(raw).length === 12 && Boolean(person.iin) && digits(raw) === digits(person.iin))
            || (Boolean(person.license) && upper(raw) === upper(person.license))
            || (Boolean(car.plate) && upper(raw) === upper(car.plate));
    }).map(([item, index]) => crmAccount(item, index));
};

/* Деньги так, как их пишет общая часть карточки CRM (formatMoney): целые
   тенге с разрядами, «-619 ₸». Принимает и число, и строку «−618,95 ₸». */
export const crmMoney = (value) => {
    const raw = String(value == null ? '' : value).replace(/−/g, '-').replace(/[^\d,.-]/g, '').replace(',', '.');
    if (!raw || raw === '-' || Number.isNaN(Number(raw))) return 'Нет данных';
    return `${Math.round(Number(raw)).toLocaleString('ru-RU')} ₸`;
};

/* Тип сотрудничества: «Физлицо» в списке, «Физическое лицо» в карточке. От
   него зависит ветка категорий обращения: физлицо — «Обычный водитель»,
   самозанятый — «Самозанятый водитель». */
const EMPLOYMENT = {
    'Парковый сотрудник': ['park_employee', 'Физлицо', 'Физическое лицо', 'Обычный водитель'],
    'Самозанятый': ['selfemployed', 'СМЗ', 'Самозанятый', 'Самозанятый водитель'],
    'Индивидуальный предприниматель': ['individual_entrepreneur', 'ИП', 'Индивидуальный предприниматель', 'Обычный водитель'],
};

/** Ветка категорий обращения для водителя: «Обычный водитель» или «Самозанятый водитель». */
export const categoryBranch = (caseData) => (EMPLOYMENT[caseData?.contractor?.employment] || [])[3] || 'Обычный водитель';

/**
 * Учётка водителя глазами CRM — из его дела. index — место дела в учебной
 * CRM: от него номера учётки и водителя в CRM.
 */
export const crmAccount = (caseData, index = 0) => {
    const person = caseData.contractor || {};
    const car = caseData.car || null;
    const park = caseData.park || {};
    const [kind, shortLabel, longLabel] = EMPLOYMENT[person.employment] || ['', 'Нет данных', person.employment || '—'];
    const terms = person.terms || '—';
    const commission = person.park_commission ?? 4;
    const balance = Number(person.balance);
    const limit = Number(person.limit);
    return {
        id: ACCOUNT_BASE + index * 37,
        driverId: DRIVER_BASE + index * 23,
        accountId: person.id,
        name: [person.last, person.first].filter(Boolean).join(' '),
        fullName: [person.last, person.first, person.middle].filter(Boolean).join(' '),
        initials: [person.last, person.first].filter(Boolean).map((part) => part[0]).join('').toUpperCase(),
        phone: person.phone_pretty || person.phone || '—',
        park: [park.name, park.city].filter(Boolean).join(' '),
        parkName: park.name || '',
        parkCity: park.city || '',
        /* CRM пишет телефон слитно, с плюсом: «+77015550142». */
        phoneCrm: digits(person.phone) ? `+${digits(person.phone)}` : '—',
        /* Профессию CRM не уточняет: «Водитель», без «такси». */
        profession: 'Водитель',
        employmentKind: kind,
        employmentShort: shortLabel,
        employment: longLabel,
        terms,
        /* Комиссия парка по умолчанию — в скобках после названия условий,
           как пишет CRM: «Свободный4 (парк 6)». */
        termsCrm: terms === '—' ? terms : `${terms} (парк ${commission})`,
        parkCommission: `${commission} %`,
        created: person.created_text || '—',
        hired: person.hired_text || '—',
        fired: person.fired_text || '—',
        lastTransaction: (caseData.transactions || [])[0]?.when || '—',
        working: person.work_status === 'Работает',
        workStatus: person.work_status || '—',
        workBadge: person.work_status === 'Работает' ? 'badge-success'
            : person.work_status === 'Уволен' ? 'badge-danger' : 'badge-warning',
        online: person.online || 'Офлайн',
        balanceValue: Number.isFinite(balance) ? balance : null,
        limitValue: Number.isFinite(limit) ? limit : null,
        balanceCrm: crmMoney(person.balance),
        /* «К выводу в приложении»: баланс минус неснижаемый остаток 100 ₸. */
        withdrawable: Number.isFinite(balance) ? crmMoney(Math.max(0, balance - 100)) : 'Нет данных',
        /* В CRM минус — обычный дефис: «-50 ₸». */
        limit: Number.isFinite(limit) ? crmMoney(limit) : '—',
        rating: person.rating || 'Нет данных',
        diagnostics: person.diagnostics || 'Нет данных',
        priority: person.priority || 'Нет данных',
        thermobox: person.thermobox || 'Нет данных',
        edo: person.edo || '—',
        license: person.license || '—',
        iin: person.iin || '—',
        callsign: person.callsign || '—',
        hasCar: Boolean(car),
        car: {
            title: car ? [car.brand, car.model].filter(Boolean).join(' ') : '—',
            brand: car?.brand || '',
            model: car?.model || '',
            plate: car?.plate || '—',
            year: car?.year || '—',
            color: car?.color || '—',
            tariffs: car?.categories ? car.categories.split('·').map((part) => part.trim()).join(', ') : '—',
            status: car?.status || '—',
            owner: car?.owner || '—',
            vin: car?.vin || '—',
            body: car?.body || '',
            sts: car?.sts || '',
            transmission: car?.transmission || '',
            fuel: car?.fuel || '',
        },
    };
};
