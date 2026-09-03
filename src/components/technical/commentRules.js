// Причины техсбоя, для которых одного пункта из списка мало: без подробностей
// инцидент потом не разобрать. Правило приходит из API
// (`/api/technical_issues/reasons` → `comment_required_reasons`), здесь только
// резерв на случай, когда справочник не доехал, и общие помощники — чтобы
// формула «обязателен или нет» не размножилась по трём формам.

export const FALLBACK_COMMENT_REQUIRED_REASONS = [
    {
        reason: 'Не работал рабочий сайт',
        hint: 'Укажите название сайта, что именно не работало и какая ошибка отображалась.',
        example: 'Не открывался сайт CRM, при входе отображалась ошибка 502',
    },
];

export const normalizeCommentRules = (raw) => {
    const list = Array.isArray(raw) ? raw : [];
    const out = [];
    const seen = new Set();
    for (const item of list) {
        const reason = String(item?.reason || '').trim();
        if (!reason || seen.has(reason)) continue;
        seen.add(reason);
        out.push({
            reason,
            hint: String(item?.hint || '').trim(),
            example: String(item?.example || '').trim(),
        });
    }
    return out;
};

export const areCommentRuleListsEqual = (left, right) => {
    const a = Array.isArray(left) ? left : [];
    const b = Array.isArray(right) ? right : [];
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
        if (String(a[i]?.reason || '') !== String(b[i]?.reason || '')) return false;
        if (String(a[i]?.hint || '') !== String(b[i]?.hint || '')) return false;
        if (String(a[i]?.example || '') !== String(b[i]?.example || '')) return false;
    }
    return true;
};

export const buildCommentRuleMap = (rules) => {
    const map = new Map();
    for (const rule of Array.isArray(rules) ? rules : []) {
        const reason = String(rule?.reason || '').trim();
        if (reason) map.set(reason, rule);
    }
    return map;
};

export const findCommentRule = (rules, reason) => {
    const needle = String(reason || '').trim();
    if (!needle) return null;
    const list = Array.isArray(rules) ? rules : [];
    return list.find((rule) => String(rule?.reason || '').trim() === needle) || null;
};

// Текст один и тот же на сервере и во всех формах, чтобы человек не читал
// в тосте одно, а в подсказке под полем другое.
export const commentRequiredMessage = (rule) => {
    const reason = String(rule?.reason || '').trim();
    const hint = String(rule?.hint || '').trim();
    const head = reason
        ? `Комментарий обязателен для причины «${reason}»`
        : 'Комментарий обязателен';
    return hint ? `${head}. ${hint}` : `${head}.`;
};
