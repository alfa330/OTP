/*
 * Базы обзвона делятся по месяцам (решение владельца 23.09.2026): один и тот же
 * номер может повторяться в базе другого месяца. Здесь — общие помощники для
 * переключателей месяца во вкладках «База водителей», «Журнал» и «Настройки».
 *
 * Месяц везде передаётся как ISO первого дня ('2026-09-01'); сервер принимает и
 * 'YYYY-MM'. 'all' — все месяцы (только журнал).
 */

const MONTHS_RU = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

export const monthLabel = (iso) => {
    if (!iso || iso === 'all') return iso === 'all' ? 'Все месяцы' : '';
    const [y, m] = String(iso).split('-').map(Number);
    if (!y || !m) return String(iso);
    return `${MONTHS_RU[m - 1]} ${y}`;
};

export const addMonths = (iso, n) => {
    const [y, m] = String(iso).split('-').map(Number);
    if (!y || !m) return iso;
    const d = new Date(y, m - 1 + n, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
};

export const currentMonthIso = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
};

/**
 * Варианты для CustomSelect: месяцы с базами (с числом водителей), обзваниваемый
 * помечен, при желании — следующий месяц (чтобы загрузить базу заранее) и «Все».
 *
 * grouped — обзваниваемый месяц не хвостом «· обзванивается» в подписи, а
 * отдельной группой списка («Обзванивается» / «Другие месяцы»). Хвост не
 * помещался в кнопку выбора: в ней оставалось «Сентябрь 2026 · 128 · о…».
 */
export const buildPeriodOptions = (periods = [], { includeAll = false, includeNext = false, activePeriod = '', grouped = false } = {}) => {
    if (grouped) {
        const sorted = [...periods].sort((a, b) => String(b.period).localeCompare(String(a.period)));
        const active = activePeriod || sorted.find((p) => p.active)?.period || '';
        const label = (p) => `${monthLabel(p.period)}${Number(p.total) ? ` · ${p.total}` : ''}`;
        const current = sorted.find((p) => p.period === active) || (active ? { period: active, total: 0 } : null);
        return [
            ...(includeAll ? [{ value: 'all', label: 'Все месяцы' }] : []),
            ...(current ? [{ value: current.period, label: label(current), groupLabel: 'Обзванивается' }] : []),
            ...sorted.filter((p) => p.period !== active)
                .map((p) => ({ value: p.period, label: label(p), groupLabel: 'Другие месяцы' })),
        ];
    }
    const seen = new Set();
    const options = [];
    const push = (value, label) => {
        if (!value || seen.has(value)) return;
        seen.add(value);
        options.push({ value, label });
    };
    if (includeAll) push('all', 'Все месяцы');
    const sorted = [...periods].sort((a, b) => String(b.period).localeCompare(String(a.period)));
    sorted.forEach((p) => {
        const tag = p.active || p.period === activePeriod ? ' · обзванивается' : '';
        const count = Number(p.total) ? ` · ${p.total}` : '';
        push(p.period, `${monthLabel(p.period)}${count}${tag}`);
    });
    if (activePeriod) push(activePeriod, `${monthLabel(activePeriod)} · обзванивается`);
    if (includeNext) {
        const base = sorted[0]?.period || activePeriod || currentMonthIso();
        const next = addMonths(base, 1);
        push(next, `${monthLabel(next)} · новая база`);
    }
    return options;
};
