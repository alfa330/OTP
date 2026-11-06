/*
 * «Доска почёта» во вкладке «Ивентов» (задача #318): справочник и правила.
 *
 * Тот же справочник — на сервере, honor_board/catalog.py; tests/test_honor_board.py
 * сверяет их буквально: доска не должна назвать номинацию иначе, чем её принял
 * сервер. Здесь нет React — только данные и чистые функции (tests/honor_board_meta.test.mjs).
 */

export const MAIN_NOMINATION = 'best_employee';
export const CUSTOM_NOMINATION = 'custom';

// Номинаций на доске вместе с ключевой — у всех трёх направлений.
export const MAX_NOMINATIONS = 8;
export const NAME_MAX = 80;
export const TITLE_MAX = 60;
export const DESCRIPTION_MAX = 600;

export const MAIN_TITLE = 'Лучший сотрудник отдела';
export const MAIN_REASON = 'по итогам месяца, за лучшие показатели по всем ключевым метрикам';
// Корона — только у ключевой номинации (ТЗ: «специальная плашка/корона»).
export const MAIN_ICON = 'Crown';
export const DEFAULT_ICON = 'Award';

/* Значки своих номинаций — имена значков lucide (компоненты подставляет
   honorUi.jsx: модуль остаётся без React и читается тестом). Список закрытый,
   тот же, что у сервера. */
export const HONOR_ICONS = [
    'Award', 'Star', 'Trophy', 'Medal', 'Rocket', 'Target', 'Zap', 'TrendingUp',
    'Flame', 'Gem', 'Sparkles', 'HeartHandshake', 'MessageCircleHeart', 'Headphones', 'BadgeCheck', 'Car',
];

/* presets — номинации ТЗ, которыми открывается первая доска направления
   (дальше каждый месяц берёт названия своего прошлого): code — прежний ключ,
   которым хранились доски до 06.11.2026. genitive — для строки «победителей
   отдела продаж»; серверу не нужен. */
export const HONOR_DEPARTMENTS = [
    {
        key: 'op',
        label: 'ОП',
        title: 'Отдел продаж',
        genitive: 'отдела продаж',
        presets: [
            { code: 'guru_sales', title: 'Гуру продаж', icon: 'TrendingUp', reason: 'за высокие результаты и профессионализм в продажах' },
            { code: 'master_yr', title: 'Мастер ЯР', icon: 'Car', reason: 'за лучшие показатели в группе Яндекс Регистрации' },
            { code: 'best_verifier', title: 'Лучший верификатор', icon: 'BadgeCheck', reason: 'за наибольшее количество регистраций за месяц' },
        ],
    },
    {
        key: 'szov',
        label: 'СЗоВ',
        title: 'Служба заботы о водителях',
        genitive: 'службы заботы о водителях',
        presets: [
            { code: 'productivity_champion', title: 'Чемпион по продуктивности', icon: 'Zap', reason: 'за высокие результаты и стабильную работу' },
            { code: 'first_class_manager', title: 'Первоклассный менеджер', icon: 'HeartHandshake', reason: 'за качество обслуживания и вовлечённость' },
            { code: 'chat_star', title: 'Звезда чат-направления', icon: 'MessageCircleHeart', reason: 'за эффективность и внимательность в переписке с водителями' },
        ],
    },
    {
        key: 'front_office',
        label: 'Фронт-офис',
        title: 'Фронт-офис',
        genitive: 'фронт-офиса',
        presets: [],
    },
];

export const honorDepartment = (key) => HONOR_DEPARTMENTS.find((item) => item.key === key) || null;

export const iconOrDefault = (icon, fallback = DEFAULT_ICON) => (HONOR_ICONS.includes(icon) ? icon : fallback);

/* Справка о номинации карточки: название из записи (так её объявили), значок —
   из записи; «за что» — у ключевой из ТЗ, у своей — только если её название
   совпадает с номинацией ТЗ этого направления (переименованную тренер
   описывает сам, в описании заслуг). */
export const nominationMeta = (departmentKey, nomination, storedTitle = '', storedIcon = '') => {
    const main = nomination === MAIN_NOMINATION;
    if (main) {
        return { title: storedTitle || MAIN_TITLE, reason: MAIN_REASON, icon: MAIN_ICON, main: true };
    }
    const preset = honorDepartment(departmentKey)?.presets.find((item) => item.title === storedTitle);
    return {
        title: storedTitle || '',
        reason: preset?.reason || '',
        icon: iconOrDefault(storedIcon),
        main: false,
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

/* Какое направление показать после захода или зова колокола.
   wanted — направления, о которых звал колокол (вышли и не просмотрены):
   выбранное (своё) — если оно среди них, иначе первое из них с доской.
   Без зова — выбранное, если у него есть доска за месяц, иначе первое с доской. */
export const pickDepartment = (current, boards, wanted = []) => {
    const has = (key) => Boolean(boards?.[key]);
    const called = (wanted || []).filter(has);
    if (called.length) return called.includes(current) ? current : called[0];
    if (has(current)) return current;
    return HONOR_DEPARTMENTS.find((item) => has(item.key))?.key || current;
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

export const isPeriodKey = (key) => parsePeriod(key) !== null;

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

export const blankEntry = (nomination, title = '', icon = null) => ({
    nomination,
    title,
    icon: icon || (nomination === MAIN_NOMINATION ? MAIN_ICON : DEFAULT_ICON),
    user_id: null,
    name: '',
    description: '',
    photo_id: null,
});

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

/** Сколько своих номинаций ещё можно добавить. */
export const nominationSlotsLeft = (entries) => Math.max(0, MAX_NOMINATIONS - (entries || []).length);

/* Что уходит на сервер: только поля записи. Пустые строки — тоже: пустая своя
   номинация — место, которое тренер только что добавил, или название с
   прошлого месяца, которое ждёт победителя. */
export const serializeEntries = (entries) => (entries || []).map((entry) => {
    const main = entry.nomination === MAIN_NOMINATION;
    return {
        nomination: entry.nomination,
        title: main ? '' : String(entry.title || ''),
        icon: main ? MAIN_ICON : iconOrDefault(entry.icon),
        user_id: entry.user_id || null,
        name: String(entry.name || ''),
        description: String(entry.description || ''),
        photo_id: entry.user_id ? (entry.photo_id || null) : null,
    };
});

// ── Доска для сотрудников ──────────────────────────────────────────────

/* Карточки направления: ключевая отдельно (её доска выделяет), остальные — в
   порядке публикации. */
export const splitBoard = (board) => {
    const entries = Array.isArray(board?.entries) ? board.entries : [];
    const main = entries.find((entry) => entry.nomination === MAIN_NOMINATION) || null;
    return { main, others: entries.filter((entry) => entry !== main) };
};

/* Пастельные плитки доски — «немного цветов», как на образце: у каждой
   карточки свой оттенок по её месту на доске, у ключевой — золотой. Утилиты
   Tailwind, а не свои цвета: тёмный слой портала (theme-dark.css) перекрашивает
   их сам. */
export const HONOR_TONES = [
    { key: 'green', tile: 'bg-emerald-100', ink: 'text-emerald-700' },
    { key: 'blue', tile: 'bg-sky-100', ink: 'text-sky-700' },
    { key: 'peach', tile: 'bg-orange-100', ink: 'text-orange-700' },
    { key: 'lilac', tile: 'bg-fuchsia-100', ink: 'text-fuchsia-700' },
    { key: 'teal', tile: 'bg-teal-100', ink: 'text-teal-700' },
    { key: 'rose', tile: 'bg-rose-100', ink: 'text-rose-700' },
    { key: 'violet', tile: 'bg-violet-100', ink: 'text-violet-700' },
];
export const MAIN_TONE = { key: 'gold', tile: 'bg-amber-100', ink: 'text-amber-700' };

/** Оттенок карточки: ключевая — золото, остальные — по кругу по месту на доске. */
export const toneOf = (entry, index) => (entry?.nomination === MAIN_NOMINATION
    ? MAIN_TONE : HONOR_TONES[Math.max(0, Number(index) || 0) % HONOR_TONES.length]);

/* Место строки черновика среди плиток доски: на доску выходят только
   заполненные номинации, поэтому окно тренера красит строку тем же цветом,
   что получит её плитка, — считая только заполненные строки перед ней. */
export const boardPlace = (entries, index) => (entries || []).slice(0, Math.max(0, index))
    .filter((entry) => entry?.nomination !== MAIN_NOMINATION && entryHasContent(entry)).length;

/* Раскладка плиток по фактической ширине доски (а не по ширине экрана: сайдбар
   бывает раскрыт и свёрнут). Просьбы владельца 06.11.2026: «чтобы всё
   отображалось симметрично» и «пусть не будет таких длинных плит, минимум две
   плиты на одной строке»:
     * колонок — сколько влезает плиток не уже MIN_TILE, но не меньше двух
       (и на телефоне) и не больше четырёх; одна плитка — в половину ряда;
     * ряды выравниваются: 5 номинаций — 3 + 2, а не 4 + 1; 3 номинации — три
       колонки на всю ширину, а не три из четырёх по центру;
     * рост плитки — от её ширины: узкая — выше своей ширины (портрет, как на
       образце), широкая — ниже; в пределах 240–380;
       золотая карточка — того же роста, её фото — шириной в одну плитку.
   width — ширина доски в px, count — сколько плиток (без ключевой). */
export const TILE_GAP = 20;
// На узкой доске (телефон) промежуток меньше: две плитки в ряд — по ~180 px.
const NARROW_GAP = 12;
const NARROW_BOARD = 600;
const MIN_TILE = 260;
const MIN_COLUMNS = 2;
const MAX_COLUMNS = 4;
// Плитка уже этого — компактная подпись (меньше шрифт и поля).
export const DENSE_TILE = 240;

const gapFor = (room) => (room < NARROW_BOARD ? NARROW_GAP : TILE_GAP);
const fitColumns = (room, gap) => Math.max(MIN_COLUMNS, Math.min(MAX_COLUMNS, Math.floor((room + gap) / (MIN_TILE + gap))));
const tileHeight = (tileWidth) => Math.round(Math.min(380, Math.max(240, 170 + tileWidth * 0.45)));

export const tileLayout = (width, count) => {
    const total = Math.max(1, Number(count) || 0);
    const room = Number(width) || 0;
    if (room <= 0) return { columns: Math.min(Math.max(total, MIN_COLUMNS), 3), tileWidth: 0, height: 320, gap: TILE_GAP };
    const gap = gapFor(room);
    const fit = fitColumns(room, gap);
    const rows = Math.ceil(total / fit);
    const columns = Math.max(MIN_COLUMNS, Math.min(fit, Math.ceil(total / rows)));
    const tileWidth = (room - gap * (columns - 1)) / columns;
    return { columns, tileWidth, height: tileHeight(tileWidth), gap };
};

/* Раскладка всей доски: золотая карточка и плитки.
     inline  — одна-две свои номинации, если влезают: золотая карточка (две
               колонки: фото и текст) и плитки встают ОДНИМ рядом равных колонок.
     иначе   — золотая карточка во всю ширину, плитки рядами под ней (tileLayout);
               фото карточки — шириной в одну плитку ряда, на узкой доске — над текстом.
   mainPhotoWidth — ширина фото в золотой карточке (0 — фото над текстом). */
export const boardLayout = (width, count) => {
    const total = Math.max(0, Number(count) || 0);
    const room = Number(width) || 0;
    if (room <= 0) {
        return { inline: false, columns: Math.min(Math.max(total, MIN_COLUMNS), 3), tileWidth: 0, mainWidth: 0,
            mainPhotoWidth: 0, height: 320, gap: TILE_GAP };
    }
    const gap = gapFor(room);
    const fit = fitColumns(room, gap);
    if (total >= 1 && total + 2 <= fit) {
        const columns = total + 2;
        const cell = (room - gap * (columns - 1)) / columns;
        return { inline: true, columns, tileWidth: cell, mainWidth: cell * 2 + gap, mainPhotoWidth: cell,
            height: tileHeight(cell), gap };
    }
    const tiles = tileLayout(room, total || 3);
    // Рядом с фото тексту нужно место: на узкой доске фото — над текстом.
    const mainPhotoWidth = room >= 640 ? tiles.tileWidth : 0;
    return { inline: false, columns: tiles.columns, tileWidth: tiles.tileWidth, mainWidth: room, mainPhotoWidth,
        height: tiles.height, gap };
};

/* Какое фото у карточки:
     'cutout'   — фото для доски без фона: фигура стоит на цветной плитке;
     'photo'    — фото для доски с фоном: портретная рамка в пропорциях снимка;
     'avatar'   — фото из профиля: кружок;
     'initials' — ничего нет.
   honorPhotos — {id: {url, cutout}} из ответа, avatars — {user_id: url}. */
export const entryPhoto = (entry, honorPhotos = {}, avatars = {}) => {
    const honor = entry?.photo_id ? honorPhotos?.[String(entry.photo_id)] : null;
    if (honor?.url) {
        // Пропорции снимка — фото с фоном встаёт рамкой своей формы, а не
        // полосой во всю плитку, где от лица остаётся макушка.
        const ratio = Number(honor.width) > 0 && Number(honor.height) > 0 ? Number(honor.width) / Number(honor.height) : 0;
        return { kind: honor.cutout ? 'cutout' : 'photo', url: honor.url, ratio };
    }
    const avatar = entry?.user_id ? avatars?.[String(entry.user_id)] : null;
    if (avatar) return { kind: 'avatar', url: avatar };
    return { kind: 'initials', url: '' };
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
