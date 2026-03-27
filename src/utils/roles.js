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
