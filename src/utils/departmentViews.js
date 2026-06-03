import { isAdminLikeRole } from './roles';

/*
 * Хардкод-карта «отдел → разрешённые разделы» (view-ключи из App.jsx).
 *
 * Правила:
 *  - Отдел отсутствует в карте  => ограничений НЕТ (поведение как раньше, напр. СЗоВ
 *    и любой отдел без конфига видят свои разделы по роли как обычно).
 *  - Для отделов из карты НЕ-админские роли видят ТОЛЬКО перечисленные разделы.
 *  - Админы / супер-админы отделом не ограничиваются (управляют всем).
 *
 * Ключ — это departments.code (нижний регистр). Значение — массив view-ключей.
 */
export const DEPARTMENT_VIEW_ALLOWLIST = {
    op: ['salary', 'profile'], // Отдел продаж: пока только Зарплата + Профиль
};

export const departmentCodeOf = (user) => {
    const code = user?.department_code ?? user?.departmentCode;
    return code ? String(code).toLowerCase() : null;
};

export const departmentRestrictsViews = (user) => {
    const code = departmentCodeOf(user);
    return !!(code && DEPARTMENT_VIEW_ALLOWLIST[code]);
};

// Разрешён ли раздел viewKey пользователю с учётом его отдела.
export const departmentAllowsView = (user, viewKey) => {
    const code = departmentCodeOf(user);
    const allow = code ? DEPARTMENT_VIEW_ALLOWLIST[code] : null;
    if (!allow) return true;                        // отдел без ограничений
    if (isAdminLikeRole(user?.role)) return true;   // админы не ограничиваются отделом
    return allow.includes(viewKey);
};

// Первый разрешённый раздел из списка кандидатов (для дефолта/редиректа).
export const firstAllowedView = (user, candidates = []) => {
    for (const v of candidates) {
        if (departmentAllowsView(user, v)) return v;
    }
    return null;
};
