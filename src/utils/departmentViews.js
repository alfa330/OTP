import { isAdminLikeRole, isDepartmentHead, normalizeRole } from './roles';

const TEZ_OPERATOR_VIEWS = ['profile', 'evaluation', 'hours', 'work_schedules', 'surveys', 'salary'];
const TEZ_MANAGER_VIEWS = [
    'manage_operators',
    'call_evaluation',
    'call_division',
    'monitoring_scale',
    'work_schedules',
    'sv_hours',
    'tasks',
    'salary',
    'surveys',
];

/*
 * Хардкод-карта «отдел → роль → разрешённые разделы» (view-ключи из App.jsx).
 *
 * Правила:
 *  - Отдел отсутствует в карте  => ограничений НЕТ (напр. СЗоВ — все видят свои
 *    разделы по роли как обычно).
 *  - Роль отсутствует в конфиге отдела => для этой роли ограничений НЕТ.
 *  - Админы / супер-админы и главы отдела (управленцы) НЕ ограничиваются.
 *  - Для остальных ролей спец-отдела показываем ТОЛЬКО перечисленные разделы.
 *
 * Ключ верхнего уровня — departments.code (lowercase). Внутри — роль → [view-ключи].
 */
export const DEPARTMENT_VIEW_ALLOWLIST = {
    tez: {
        operator: TEZ_OPERATOR_VIEWS,
        trainee: TEZ_OPERATOR_VIEWS,
        sv: TEZ_MANAGER_VIEWS,
    },
    op: {
        // Операторы ОП: Профиль, Зарплата + Мои смены, Мои оценки, Опросы
        operator: ['salary', 'profile', 'work_schedules', 'evaluation', 'surveys'],
        trainee: ['salary', 'profile', 'work_schedules', 'evaluation', 'surveys'],
        // Супервайзеры продаж: их рабочий набор разделов
        sv: [
            'manage_operators',   // Учет сотрудников (их группа)
            'qr_access',          // QR доступ
            'call_evaluation',    // Журнал оценок
            'call_division',      // Деление звонков
            'work_schedules',     // Графики работы
            'trainings',          // Учет тренингов
            'technical_issues',   // Тех причины
            'surveys',            // Опросы
            'tasks',              // Задачи
            'salary',             // Калькулятор зарплаты
        ],
    },
};

export const departmentCodeOf = (user) => {
    const code = user?.department_code ?? user?.departmentCode;
    return code ? String(code).toLowerCase() : null;
};

// Возвращает массив разрешённых разделов для пользователя, либо null (без ограничений).
const allowlistFor = (user) => {
    // Управленцы (админы/главы) — без ограничений по отделу.
    if (isAdminLikeRole(user?.role)) return null;
    const code = departmentCodeOf(user);
    const deptCfg = code ? DEPARTMENT_VIEW_ALLOWLIST[code] : null;
    if (!deptCfg) return null;
    const role = isDepartmentHead(user) ? 'sv' : normalizeRole(user?.role);
    const allow = deptCfg[role];
    return Array.isArray(allow) ? allow : null;
};

export const departmentRestrictsViews = (user) => Array.isArray(allowlistFor(user));

// Разрешён ли раздел viewKey пользователю с учётом его отдела и роли.
export const departmentAllowsView = (user, viewKey) => {
    const allow = allowlistFor(user);
    if (!allow) return true; // нет ограничений
    return allow.includes(viewKey);
};

// Первый разрешённый раздел: сначала из переданных кандидатов, иначе — первый из allowlist.
export const firstAllowedView = (user, candidates = []) => {
    const allow = allowlistFor(user);
    for (const v of candidates) {
        if (!allow || allow.includes(v)) return v;
    }
    return allow && allow.length ? allow[0] : null;
};
