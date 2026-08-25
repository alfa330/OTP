// Расширение в пути обязательно: этот модуль грузит напрямую Node в
// tests/back_office_department_views.test.mjs, а ESM без расширения путь не
// разрешает (Vite разрешает и так, поэтому сборка не замечает разницы).
import { isAdminLikeRole, isDepartmentHead, normalizeRole } from './roles.js';

const TEZ_OPERATOR_VIEWS = ['profile', 'evaluation', 'hours', 'work_schedules', 'surveys', 'salary'];
const TEZ_MANAGER_VIEWS = [
    'manage_operators',
    'qr_access',
    'call_evaluation',
    'call_division',
    'monitoring_scale',
    'work_schedules',
    'sv_hours',
    'tasks',
    'salary',
    'surveys',
];
const TEZ_SUPERVISOR_VIEWS = TEZ_MANAGER_VIEWS.filter((view) => view !== 'monitoring_scale');

// Операторы ОП: Зарплата, Профиль, Мои часы, Мои смены, Мои оценки, Опросы.
// «Зарплата» остаётся первой — это раздел по умолчанию (firstAllowedView).
const SALES_OPERATOR_VIEWS = ['salary', 'profile', 'hours', 'work_schedules', 'evaluation', 'surveys'];

const SALES_SUPERVISOR_VIEWS = [
    'manage_operators',
    'qr_access',
    'call_evaluation',
    'call_division',
    'ai_qa',
    'work_schedules',
    // Учёт часов открыт СВ ОП (модели направлений ОП: часы + штрафы).
    'sv_hours',
    'trainings',
    'technical_issues',
    'surveys',
    'tasks',
    'salary',
];
const SALES_HEAD_VIEWS = [
    ...SALES_SUPERVISOR_VIEWS.slice(0, 4),
    'monitoring_scale',
    ...SALES_SUPERVISOR_VIEWS.slice(4),
];

// Фронт офисы: менеджеры ведут только учёт сотрудников, свои группы и графики
// работы; сотрудники видят только свой профиль и «Мои смены» (без смен коллег).
const FRONT_OFFICE_OPERATOR_VIEWS = ['profile', 'work_schedules'];
const FRONT_OFFICE_MANAGER_VIEWS = ['manage_operators', 'groups', 'work_schedules'];
// «Задачи» выданы только главе отдела: у СВ фронт-офисов набор разделов прежний
// (в tez/op раздел есть у обеих ролей, здесь — по запросу владельца только глава).
// «QR доступ» — там же и по той же причине: сотрудники фронт-офиса открывают
// «Вики» (офисы, парки) только по подтверждению, а супервайзеров в отделе нет
// вовсе — подтверждает глава. Без строки в allowlist пункта меню у него не
// появится, и подтвердить доступ станет физически некому.
const FRONT_OFFICE_HEAD_VIEWS = [...FRONT_OFFICE_MANAGER_VIEWS, 'tasks', 'qr_access'];

// Бэк-офис (Бухгалтерия, HR): отделы без телефонии, направлений, графиков и
// оценок. Им оставлены только «Учёт сотрудников» и «Вики».
//
// «Вики» в этой карте не значится намеренно: раздел выдаётся ОТДЕЛУ тумблером
// departments.wiki_enabled вместе с пространством и гейтится wikiEnabledFor в
// App.jsx, а не allowlist'ом — вписав его сюда, мы бы завели вторую, молчаливо
// расходящуюся проверку.
//
// «QR доступ» у главы — по той же причине, что у фронт-офисов: сотрудник с
// ролью «оператор» открывает «Вики» только после подтверждения QR
// (sensitiveSectionQrRequiredFor), а подтверждает админ, супервайзер или глава
// отдела (_sensitive_access_approval_error). Супервайзеров в бэк-офисе нет —
// без этой строки пункта у главы не будет и подтвердить доступ станет некому.
//
// Роли 'trainer' в конфиге нет намеренно: у такого отдела не осталось бы ни
// одного раздела из TRAINER_ALLOWED_VIEWS, и два гарда в App.jsx — тренерский
// (выкидывает в 'surveys') и отдельский (выкидывает в 'profile') — гоняли бы
// вид друг другу без остановки.
const BACK_OFFICE_EMPLOYEE_VIEWS = ['profile'];
const BACK_OFFICE_MANAGER_VIEWS = ['manage_operators', 'tasks'];
const BACK_OFFICE_HEAD_VIEWS = [...BACK_OFFICE_MANAGER_VIEWS, 'qr_access'];

const VIEW_ALIASES = {
    sv_list: 'manage_operators',
    manage_users: 'manage_operators',
};

const FOUR_YOU_VIEWER_USER_ID = 241;

// Разделы, доступные всем ролям/отделам независимо от allowlist отдела.
// «Ивенты» — общая лента компании (пункт меню тоже рендерится для всех);
// без этого исключения guard видимости выкидывал бы сотрудников отделов с
// ограничениями (op/tez) обратно на первый разрешённый раздел (напр. зарплату).
const UNIVERSAL_VIEWS = new Set(['events']);

/*
 * Хардкод-карта «отдел → роль → разрешённые разделы» (view-ключи из App.jsx).
 *
 * Правила:
 *  - Отдел отсутствует в карте  => ограничений НЕТ (напр. СЗоВ — все видят свои
 *    разделы по роли как обычно).
 *  - Роль отсутствует в конфиге отдела => для этой роли ограничений НЕТ.
 *  - Админы / супер-админы НЕ ограничиваются.
 *  - Главы отделов используют отдельный head-набор.
 *  - Для остальных ролей спец-отдела показываем ТОЛЬКО перечисленные разделы.
 *
 * Ключ верхнего уровня — departments.code (lowercase). Внутри — роль → [view-ключи].
 */
export const DEPARTMENT_VIEW_ALLOWLIST = {
    tez: {
        operator: TEZ_OPERATOR_VIEWS,
        trainee: TEZ_OPERATOR_VIEWS,
        head: TEZ_MANAGER_VIEWS,
        sv: TEZ_SUPERVISOR_VIEWS,
    },
    op: {
        operator: SALES_OPERATOR_VIEWS,
        trainee: SALES_OPERATOR_VIEWS,
        // Супервайзеры продаж: их рабочий набор разделов
        head: SALES_HEAD_VIEWS,
        sv: SALES_SUPERVISOR_VIEWS,
    },
    front_office: {
        operator: FRONT_OFFICE_OPERATOR_VIEWS,
        trainee: FRONT_OFFICE_OPERATOR_VIEWS,
        head: FRONT_OFFICE_HEAD_VIEWS,
        sv: FRONT_OFFICE_MANAGER_VIEWS,
    },
    accounting: {
        operator: BACK_OFFICE_EMPLOYEE_VIEWS,
        trainee: BACK_OFFICE_EMPLOYEE_VIEWS,
        head: BACK_OFFICE_HEAD_VIEWS,
        sv: BACK_OFFICE_MANAGER_VIEWS,
    },
    hr: {
        operator: BACK_OFFICE_EMPLOYEE_VIEWS,
        trainee: BACK_OFFICE_EMPLOYEE_VIEWS,
        head: BACK_OFFICE_HEAD_VIEWS,
        sv: BACK_OFFICE_MANAGER_VIEWS,
    },
};

export const departmentCodeOf = (user) => {
    const code = user?.department_code ?? user?.departmentCode;
    return code ? String(code).toLowerCase() : null;
};

// Отделы, операторам которых нельзя видеть смены коллег по отделу/направлению:
// в «Мои смены» скрываются табы «Замены» и «Смены коллег» вместе с кнопками
// обмена; бэкенд зеркалит это запретом /work_schedules/direction и shift_swap.
const COLLEAGUE_SCHEDULES_HIDDEN_DEPARTMENTS = new Set(['front_office']);

export const departmentHidesColleagueSchedules = (user) => {
    const code = departmentCodeOf(user);
    return Boolean(code && COLLEAGUE_SCHEDULES_HIDDEN_DEPARTMENTS.has(code));
};

// Отделы с упрощённым «Учётом сотрудников» у главы: без пунктов «Супервайзеры»
// и «Тренеры» — сразу список сотрудников (manage_users), в разделе они
// называются «Сотрудники», а не «Операторы». Бэк-офис здесь по той же причине,
// что и фронт-офисы: ни супервайзеров, ни тренеров в этих отделах нет, и оба
// пункта выпадашки открывали бы заведомо пустые списки.
const SIMPLE_EMPLOYEE_ACCOUNTING_DEPARTMENTS = new Set(['front_office', 'accounting', 'hr']);

export const departmentUsesSimpleEmployeeAccounting = (user) => {
    const code = departmentCodeOf(user);
    return Boolean(code && SIMPLE_EMPLOYEE_ACCOUNTING_DEPARTMENTS.has(code));
};

const normalizeDepartmentCodeValue = (code) => String(code ?? '').trim().toLowerCase();

// Отделы, чьи сотрудники сидят по офисам в разных городах: в карточке
// сотрудника у них есть «Город», у остальных отделов поля нет.
const EMPLOYEE_CITY_DEPARTMENTS = new Set(['front_office']);

export const departmentCodeUsesEmployeeCity = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && EMPLOYEE_CITY_DEPARTMENTS.has(normalized));
};

export const departmentUsesEmployeeCity = (user) => departmentCodeUsesEmployeeCity(departmentCodeOf(user));

// Отделы, у сотрудников которых не спрашиваем «Был во фронт офисе на обучении»:
// сотрудники фронт-офисов и есть фронт офис, отметка для них бессмысленна, а
// бухгалтерия и HR на линию не выходят вовсе — обучать их работе в офисе
// продаж незачем. Скрываем только ввод — уже сохранённое значение сохраняется
// как есть.
const FRONT_OFFICE_TRAINING_HIDDEN_DEPARTMENTS = new Set(['front_office', 'accounting', 'hr']);

export const departmentCodeHidesFrontOfficeTraining = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && FRONT_OFFICE_TRAINING_HIDDEN_DEPARTMENTS.has(normalized));
};

export const departmentHidesFrontOfficeTraining = (user) => departmentCodeHidesFrontOfficeTraining(departmentCodeOf(user));

// Отделы без операторских полей в карточке сотрудника: «Группа», «Направление»
// и «SIP номер». У бэк-офиса нет ни групп, ни направлений, ни телефонии —
// сотрудники не сидят на линии и по направлениям не делятся, а пустые
// выпадашки только просят выбрать то, чего нет.
//
// Скрываем не только ввод: с этих отделов снимается и обязательность группы и
// направления при создании сотрудника (UserEditModal.handleSave). Без этого
// глава бэк-офиса не смог бы завести человека вовсе — валидация требовала
// выбрать группу и направление, которых в отделе не существует.
//
// Уже сохранённые значения (сотрудника перевели из отдела с линией) остаются
// как есть: поле не показываем, но и не затираем.
const OPERATOR_FIELDS_HIDDEN_DEPARTMENTS = new Set(['accounting', 'hr']);

export const departmentCodeHidesOperatorFields = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && OPERATOR_FIELDS_HIDDEN_DEPARTMENTS.has(normalized));
};

export const departmentHidesOperatorFields = (user) => departmentCodeHidesOperatorFields(departmentCodeOf(user));

// Возвращает массив разрешённых разделов для пользователя, либо null (без ограничений).
const allowlistFor = (user) => {
    // Глобальные админы — без ограничений по отделу; главы отделов идут по head-набору.
    if (normalizeRole(user?.role) === 'super_admin') return null;
    if (isAdminLikeRole(user?.role) && !isDepartmentHead(user)) return null;
    const code = departmentCodeOf(user);
    const deptCfg = code ? DEPARTMENT_VIEW_ALLOWLIST[code] : null;
    if (!deptCfg) return null;
    const role = isDepartmentHead(user) ? 'head' : normalizeRole(user?.role);
    const allow = deptCfg[role];
    return Array.isArray(allow) ? allow : null;
};

export const departmentRestrictsViews = (user) => Array.isArray(allowlistFor(user));

// Разрешён ли раздел viewKey пользователю с учётом его отдела и роли.
export const departmentAllowsView = (user, viewKey) => {
    if (UNIVERSAL_VIEWS.has(viewKey)) return true;
    if (viewKey === 'four_you' && FOUR_YOU_VIEWER_USER_ID > 0 && Number(user?.id) === FOUR_YOU_VIEWER_USER_ID) return true;
    const allow = allowlistFor(user);
    if (!allow) return true; // нет ограничений
    if (allow.includes(viewKey)) return true;
    const alias = VIEW_ALIASES[viewKey];
    return Boolean(alias && isDepartmentHead(user) && allow.includes(alias));
};

// Первый разрешённый раздел: сначала из переданных кандидатов, иначе — первый из allowlist.
export const firstAllowedView = (user, candidates = []) => {
    const allow = allowlistFor(user);
    for (const v of candidates) {
        if (!allow || allow.includes(v)) return v;
    }
    return allow && allow.length ? allow[0] : null;
};
