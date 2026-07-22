import { isAdminLikeRole, isDepartmentHead, normalizeRole } from './roles';

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

const SALES_SUPERVISOR_VIEWS = [
    'manage_operators',
    'qr_access',
    'call_evaluation',
    'call_division',
    'ai_qa',
    'work_schedules',
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
        // Операторы ОП: Профиль, Зарплата + Мои смены, Мои оценки, Опросы
        operator: ['salary', 'profile', 'work_schedules', 'evaluation', 'surveys'],
        trainee: ['salary', 'profile', 'work_schedules', 'evaluation', 'surveys'],
        // Супервайзеры продаж: их рабочий набор разделов
        head: SALES_HEAD_VIEWS,
        sv: SALES_SUPERVISOR_VIEWS,
    },
    front_office: {
        operator: FRONT_OFFICE_OPERATOR_VIEWS,
        trainee: FRONT_OFFICE_OPERATOR_VIEWS,
        head: FRONT_OFFICE_MANAGER_VIEWS,
        sv: FRONT_OFFICE_MANAGER_VIEWS,
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
// называются «Сотрудники», а не «Операторы».
const SIMPLE_EMPLOYEE_ACCOUNTING_DEPARTMENTS = new Set(['front_office']);

export const departmentUsesSimpleEmployeeAccounting = (user) => {
    const code = departmentCodeOf(user);
    return Boolean(code && SIMPLE_EMPLOYEE_ACCOUNTING_DEPARTMENTS.has(code));
};

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
