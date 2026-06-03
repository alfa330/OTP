export const ROLE_HIERARCHY = Object.freeze({
    operator: 10,
    trainee: 10,
    trainer: 20,
    sv: 30,
    admin: 40,
    super_admin: 50
});

export const normalizeRole = (role) => {
    const normalized = String(role || '').trim().toLowerCase();
    if (normalized === 'supervisor') return 'sv';
    if (normalized === 'superadmin' || normalized === 'super-admin' || normalized === 'super admin') return 'super_admin';
    return normalized;
};

export const roleLevel = (role) => Number(ROLE_HIERARCHY[normalizeRole(role)] || 0);

export const roleHasMin = (role, requiredRole) => {
    const requiredLevel = roleLevel(requiredRole);
    if (!requiredLevel) return false;
    return roleLevel(role) >= requiredLevel;
};

export const roleIsAny = (role, allowedRoles = []) => {
    const normalized = normalizeRole(role);
    if (!normalized) return false;
    return (allowedRoles || []).some((item) => normalizeRole(item) === normalized);
};

export const isSupervisorRole = (role) => normalizeRole(role) === 'sv';
export const isAdminLikeRole = (role) => roleHasMin(role, 'admin');

// Глава отдела определяется не ролью, а фактом назначения: departments.head_user_id.
// Бэкенд отдаёт это в payload пользователя как headed_department_id.
export const isDepartmentHead = (user) => {
    if (!user || typeof user !== 'object') return false;
    const dept = user.headed_department_id ?? user.headedDepartmentId;
    return dept != null && dept !== '';
};

export const headedDepartmentId = (user) => {
    if (!user || typeof user !== 'object') return null;
    const dept = user.headed_department_id ?? user.headedDepartmentId;
    return dept != null && dept !== '' ? Number(dept) : null;
};
