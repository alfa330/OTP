/*
 * «Доска почёта» во вкладке «Ивентов» (задача #318): справочник и правила.
 *
 * Тот же справочник — на сервере, honor_board/catalog.py; tests/test_honor_board.py
 * сверяет их буквально: доска не должна назвать номинацию иначе, чем её принял
 * сервер. Здесь нет React — только данные и чистые функции (tests/honor_board.test.mjs).
 */

export const MAIN_NOMINATION = 'best_employee';
export const CUSTOM_NOMINATION = 'custom';

// «2–4 профильные номинации, включая ключевую» — потолок Фронт-офиса вместе с ключевой.
export const CUSTOM_MAX_NOMINATIONS = 4;
export const NAME_MAX = 80;
export const TITLE_MAX = 60;
export const DESCRIPTION_MAX = 600;

const MAIN = {
    key: MAIN_NOMINATION,
    title: 'Лучший сотрудник отдела',
    reason: 'по итогам месяца, за лучшие показатели по всем ключевым метрикам',
};

/* icon — имя значка lucide для бейджа номинации (компоненты подставляет доска:
   модуль остаётся без React и читается тестом). genitive — для строки
   «победителей отдела продаж»; серверу не нужен. */
export const HONOR_DEPARTMENTS = [
    {
        key: 'op',
        label: 'ОП',
        title: 'Отдел продаж',
        genitive: 'отдела продаж',
        custom: false,
        nominations: [
            { ...MAIN, icon: 'Crown' },
            { key: 'guru_sales', title: 'Гуру продаж', reason: 'за высокие результаты и профессионализм в продажах', icon: 'TrendingUp' },
            { key: 'master_yr', title: 'Мастер ЯР', reason: 'за лучшие показатели в группе Яндекс Регистрации', icon: 'Car' },
            { key: 'best_verifier', title: 'Лучший верификатор', reason: 'за наибольшее количество регистраций за месяц', icon: 'BadgeCheck' },
        ],
    },
    {
        key: 'szov',
        label: 'СЗоВ',
        title: 'Служба заботы о водителях',
        genitive: 'службы заботы о водителях',
        custom: false,
        nominations: [
            { ...MAIN, icon: 'Crown' },
            { key: 'productivity_champion', title: 'Чемпион по продуктивности', reason: 'за высокие результаты и стабильную работу', icon: 'Zap' },
            { key: 'first_class_manager', title: 'Первоклассный менеджер', reason: 'за качество обслуживания и вовлечённость', icon: 'HeartHandshake' },
            { key: 'chat_star', title: 'Звезда чат-направления', reason: 'за эффективность и внимательность в переписке с водителями', icon: 'MessageCircleHeart' },
        ],
    },
    {
        key: 'front_office',
        label: 'Фронт-офис',
        title: 'Фронт-офис',
        genitive: 'фронт-офиса',
        custom: true,
        nominations: [
            { ...MAIN, icon: 'Crown' },
        ],
    },
];

// Своя номинация Фронт-офиса: названия справочник не знает, значок общий.
export const CUSTOM_ICON = 'Award';

export const honorDepartment = (key) => HONOR_DEPARTMENTS.find((item) => item.key === key) || null;

/* Справка о номинации карточки: название из записи (так её объявили), за что
   и значок — из справочника. Незнакомая номинация (её убрали из списка после
   публикации) показывается своим названием и общим значком. */
export const nominationMeta = (departmentKey, nomination, storedTitle = '') => {
    const item = honorDepartment(departmentKey);
    const known = item?.nominations.find((entry) => entry.key === nomination);
    return {
        title: storedTitle || known?.title || '',
        reason: known?.reason || '',
        icon: known?.icon || CUSTOM_ICON,
        main: nomination === MAIN_NOMINATION,
    };
};

/* Кто ведёт доску — то же правило, что у сервера (honor_board/routes.py:
   MANAGER_ROLES и HEAD_MANAGED_DEPARTMENTS; тест сверяет). Здесь это только
   догадка для первого кадра — ставить ли место под «Заполнить итоги», пока не
   пришёл ответ; решает всё равно сервер (manage_departments). */
export const HONOR_MANAGER_ROLES = ['trainer', 'super_admin'];
export const HONOR_HEAD_MANAGED_CODES = ['front_office'];

export const managesHonorBoard = (user) => {
    const role = String(user?.role || '').trim().toLowerCase().replace(/[\s-]/g, '_');
    if (HONOR_MANAGER_ROLES.includes(role === 'superadmin' ? 'super_admin' : role)) return true;
    const codes = [
        ...(Array.isArray(user?.headed_department_codes) ? user.headed_department_codes : []),
        ...(Array.isArray(user?.headedDepartmentCodes) ? user.headedDepartmentCodes : []),
        user?.headed_department_code, user?.headedDepartmentCode,
    ].map((code) => String(code || '').trim().toLowerCase());
    return codes.some((code) => HONOR_HEAD_MANAGED_CODES.includes(code));
};

/* Направление, которое доска открывает первым: своё, если человек из ОП, СЗоВ
   или Фронт-офиса, иначе — первое по списку. */
export const defaultDepartmentKey = (user) => {
    const code = String(user?.department_code ?? user?.departmentCode ?? '').trim().toLowerCase();
    return honorDepartment(code) ? code : HONOR_DEPARTMENTS[0].key;
};

// ── Месяц ──────────────────────────────────────────────────────────────

const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

const parsePeriod = (key) => {
    const match = /^(\d{4})-(\d{2})$/.exec(String(key || ''));
    if (!match) return null;
    const month = Number(match[2]);
    return month >= 1 && month <= 12 ? { year: Number(match[1]), month } : null;
};

/** '2026-10' → «Октябрь 2026». */
export const periodLabel = (key) => {
    const parsed = parsePeriod(key);
    return parsed ? `${MONTHS[parsed.month - 1]} ${parsed.year}` : '';
};

/** «за октябрь 2026» — для строк вида «Итоги за … ещё не опубликованы». */
export const periodPhrase = (key) => {
    const label = periodLabel(key);
    return label ? `за ${label.charAt(0).toLowerCase()}${label.slice(1)}` : '';
};

/* Время с сервера — Алматы без пояса (как у ленты «Ивентов»): читаем как местное. */
export const formatMoment = (iso, now = new Date()) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const hhmm = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
    const sameDay = date.getFullYear() === now.getFullYear()
        && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
    if (sameDay) return `сегодня в ${hhmm}`;
    const year = date.getFullYear() === now.getFullYear() ? '' : ` ${date.getFullYear()}`;
    return `${date.getDate()} ${MONTHS_SHORT[date.getMonth()]}${year} в ${hhmm}`;
};

// ── Имя на карточке ────────────────────────────────────────────────────

/* Отчество — третье слово с окончанием отчества: русские -вич/-вна/-чна/-ична,
   казахские -улы/-ұлы/-кызы/-қызы, тюркские -оглы/-кызы. Двойную фамилию или
   имя из трёх слов без такого окончания не трогаем. */
const PATRONYMIC = /(вич|вна|ична|чна|улы|ұлы|уулу|кызы|қызы|оглы|оглу|гызы)$/i;
// Отчество в два слова: «Рашид оглы», «Нурлан улы» — частица пишется отдельно.
const PATRONYMIC_PARTICLES = new Set(['улы', 'ұлы', 'уулу', 'кызы', 'қызы', 'оглы', 'оглу', 'гызы']);

/** «Рамазан Алдияр Кажымулы» → «Рамазан Алдияр»: ТЗ просит на карточке имя и
    фамилию. Это только подстановка в поле — тренер может её поправить. */
export const honorDisplayName = (fullName) => {
    const words = String(fullName || '').trim().split(/\s+/).filter(Boolean);
    const last = String(words[words.length - 1] || '').toLowerCase();
    if (words.length >= 4 && PATRONYMIC_PARTICLES.has(last)) words.splice(-2);
    else if (words.length >= 3 && PATRONYMIC.test(last)) words.pop();
    return words.join(' ').slice(0, NAME_MAX);
};

/** Инициалы для кружка без фото: «Анна Иванова» → «АИ». */
export const initialsOf = (name) => {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
};

// ── Строки доски в окне тренера ────────────────────────────────────────

export const blankEntry = (nomination, title = '') => ({ nomination, title, user_id: null, name: '', description: '' });

/* Строка, в которой тренер что-то начал; название своей номинации само по
   себе не в счёт (оно остаётся с прошлого месяца) — как на сервере. */
export const entryHasContent = (entry) => Boolean(entry?.user_id || String(entry?.name || '').trim()
    || String(entry?.description || '').trim());

/* Чего не хватает для публикации — те же правила и тексты, что у сервера
   (honor_board/catalog.py: publish_issues): окно краснеет сразу, не дожидаясь
   ответа. [{index, field, message}] */
export const publishIssues = (entries) => {
    const issues = [];
    let filled = 0;
    (entries || []).forEach((entry, index) => {
        if (!entryHasContent(entry)) return;
        filled += 1;
        if (entry.nomination === CUSTOM_NOMINATION && !String(entry.title || '').trim()) {
            issues.push({ index, field: 'title', message: 'Назовите номинацию' });
        }
        if (!entry.user_id) issues.push({ index, field: 'user', message: 'Выберите сотрудника' });
        if (!String(entry.name || '').trim()) issues.push({ index, field: 'name', message: 'Впишите имя и фамилию' });
        if (!String(entry.description || '').trim()) issues.push({ index, field: 'description', message: 'Опишите заслуги' });
    });
    if (!filled) issues.push({ index: null, field: null, message: 'Выберите хотя бы одного победителя' });
    return issues;
};

/** Сколько своих номинаций ещё можно добавить Фронт-офису. */
export const customSlotsLeft = (departmentKey, entries) => {
    const item = honorDepartment(departmentKey);
    if (!item?.custom) return 0;
    return Math.max(0, CUSTOM_MAX_NOMINATIONS - (entries || []).length);
};

/* Что уходит на сервер: только поля записи. Пустые строки — тоже: у ОП и СЗоВ
   строка номинации есть всегда, а у Фронт-офиса пустая своя номинация — место,
   которое тренер только что добавил. */
export const serializeEntries = (entries) => (entries || []).map((entry) => ({
    nomination: entry.nomination,
    title: entry.nomination === CUSTOM_NOMINATION ? String(entry.title || '') : '',
    user_id: entry.user_id || null,
    name: String(entry.name || ''),
    description: String(entry.description || ''),
}));

// ── Доска для сотрудников ──────────────────────────────────────────────

/* Карточки направления: ключевая отдельно (её доска выделяет), остальные — в
   порядке публикации. */
export const splitBoard = (board) => {
    const entries = Array.isArray(board?.entries) ? board.entries : [];
    const main = entries.find((entry) => entry.nomination === MAIN_NOMINATION) || null;
    return { main, others: entries.filter((entry) => entry !== main) };
};

/* Отметка месяца в окне тренера (состояние доски направления). */
export const BOARD_STATE_LABELS = {
    draft: 'черновик',
    published: 'опубликовано',
    changed: 'есть правки',
};

/* Список людей в окне — по алфавиту по-русски. Порядок базы зависит от её
   правил сравнения строк (на локальном кластере под macOS кириллица идёт
   вразнобой), поэтому окно упорядочивает список само. */
const RU = typeof Intl !== 'undefined' ? new Intl.Collator('ru', { sensitivity: 'base' }) : null;

export const sortPeople = (people) => [...(people || [])].sort((a, b) => (
    RU ? RU.compare(String(a.name || ''), String(b.name || '')) : String(a.name || '').localeCompare(String(b.name || ''))
) || (Number(a.id) || 0) - (Number(b.id) || 0));

/* Поиск сотрудника: регистр и «ё» не важны, слова — в любом порядке. */
const fold = (value) => String(value || '').toLowerCase().replace(/ё/g, 'е');

export const filterPeople = (people, query) => {
    const words = fold(query).split(/\s+/).filter(Boolean);
    if (!words.length) return people || [];
    return (people || []).filter((person) => {
        const haystack = fold(`${person.name} ${person.direction || ''}`);
        return words.every((word) => haystack.includes(word));
    });
};

export const ROLE_LABELS = {
    sv: 'СВ',
    trainee: 'стажёр',
    trainer: 'тренер',
    admin: 'руководитель',
};
