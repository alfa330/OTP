/* Гостевой доступ: как читается срок.
 *
 * Отдельным модулем от WikiGuests.jsx ради теста: сам экран — модалка с
 * загрузкой по сети, и серверный рендер до этих подписей не доходит. Тот же
 * приём, что у sectionGrants.js.
 *
 * ГЛАВНОЕ ПРАВИЛО ФАЙЛА: НИ ОДНОЙ ДАТЫ ЧЕРЕЗ new Date().
 *
 * Сервер живёт в Алматы и отдаёт наивное время без зоны — «2026-09-05T23:59:59»
 * (wiki/guests.py). `new Date('2026-09-05T23:59:59')` браузер разберёт как
 * ЛОКАЛЬНОЕ время, и у человека западнее Алматы дата в баннере уедет на сутки:
 * доступ «до 5 сентября» покажется истёкшим четвёртого. Поэтому дата режется
 * строкой, а «осталось дней» приходит уже посчитанным с сервера (days_left) —
 * там же, где считается сам срок.
 *
 * По той же причине рамки календаря (today, max_until) приезжают из ответа
 * сервера, а не считаются здесь: сегодня у браузера и сегодня у Алматы — разные
 * дни, и форма предлагала бы дату, которую сервер тут же отвергнет.
 */

/** Предустановки срока. Дни, а не даты: потолок на сервере тоже в днях. */
export const GUEST_PRESETS = [1, 3, 7, 14];

/** Предустановки, которые проходят под потолок сервера (max_days из ответа). */
export const presetsWithin = (maxDays) => GUEST_PRESETS.filter(
    (days) => !maxDays || days <= maxDays);

export const plural = (n, one, few, many) => {
    const mod100 = Math.abs(n) % 100;
    const mod10 = mod100 % 10;
    if (mod100 >= 11 && mod100 <= 14) return many;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return few;
    return many;
};

export const presetLabel = (days) => `${days} ${plural(days, 'день', 'дня', 'дней')}`;

/** «2026-09-05T23:59:59» → «05.09.2026». Строкой, без разбора в Date — см. шапку. */
export const fmtDate = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    if (!match) return '';
    const [, year, month, day] = match;
    return `${day}.${month}.${year}`;
};

/** Дата без времени — для сравнения и для пикера. */
export const dateOnly = (iso) => String(iso || '').slice(0, 10);

/* Сколько осталось — словами.
 *
 * Ноль дней — это «сегодня последний», а не «истёк»: срок живёт до конца дня
 * (wiki/guests.py: resolve_expiry). Отрицательное — уже прошёл. Числа при этом
 * НЕ считаются здесь: их присылает сервер, у которого календарь алматинский. */
export const daysLeftLabel = (daysLeft) => {
    if (daysLeft === null || daysLeft === undefined) return '';
    if (daysLeft < 0) {
        const gone = Math.abs(daysLeft);
        return `истёк ${gone} ${plural(gone, 'день', 'дня', 'дней')} назад`;
    }
    if (daysLeft === 0) return 'сегодня последний день';
    if (daysLeft === 1) return 'остался 1 день';
    return `осталось ${daysLeft} ${plural(daysLeft, 'день', 'дня', 'дней')}`;
};

/* Насколько срочно. Тон, а не украшение: за два дня до конца выдачу либо
 * продлевают, либо она исчезнет, и это единственный момент, когда строку надо
 * заметить в списке. */
export const urgency = (daysLeft) => {
    if (daysLeft === null || daysLeft === undefined) return 'calm';
    if (daysLeft < 0) return 'gone';
    if (daysLeft <= 2) return 'soon';
    return 'calm';
};

/* Состояние выдачи — зеркало guests.grant_status на сервере.
 *
 * Тона — из BADGE_TONES набора (ui/ios.jsx): slate, green, red, blue, amber.
 * Незнакомый тон там молча падает в серый, то есть «Действует» и «Истёк»
 * выглядели бы одинаково, и разницу заметил бы только тот, кто её искал. */
export const STATUS_META = {
    active: { label: 'Действует', tone: 'green' },
    expired: { label: 'Истёк', tone: 'slate' },
    revoked: { label: 'Отозван', tone: 'amber' },
};

export const STATUS_FILTERS = [
    { key: 'active', label: 'Действующие' },
    { key: 'expired', label: 'Истёкшие' },
    { key: 'revoked', label: 'Отозванные' },
];

/* Подтянуть выбранную дату к границе.
 *
 * Пресет «Сегодня» внутри панели IosDatePicker про min/max не знает и умеет
 * отдать запрещённый день (ловушка из OfficeDayModal.jsx). Поэтому результат
 * подтягиваем прямо в onChange, а не надеемся на неактивные клетки. */
export const clampDate = (iso, min, max) => {
    const value = dateOnly(iso);
    if (!value) return '';
    if (min && value < min) return min;
    if (max && value > max) return max;
    return value;
};

/** Что именно выдано — одной строкой для списка и баннера. */
export const targetLabel = (item) => {
    if (!item) return '';
    const title = item.title || item.article_title || item.section_name || 'Без названия';
    if (item.kind === 'article') return title;
    return item.include_subsections ? `${title} и подразделы` : title;
};

/* Фраза баннера для самого гостя.
 *
 * Одна выдача — говорим, что именно открыто: человек не обязан помнить, зачем
 * ему дали доступ. Несколько — говорим «сколько» и ближайший срок: перечислять
 * пять названий в шапке значит закрыть ими сам раздел.
 */
export const bannerText = (grants) => {
    const items = Array.isArray(grants) ? grants : [];
    if (!items.length) return null;
    // Сервер отдаёт отсортированным по сроку, но полагаться на порядок ответа
    // при выборе БЛИЖАЙШЕГО срока нельзя: сортировку однажды поменяют ради
    // списка, а баннер молча начнёт показывать самый дальний.
    const soonest = items.reduce((best, item) => (
        !best || String(item.expires_at) < String(best.expires_at) ? item : best), null);
    const until = `до ${fmtDate(soonest.expires_at)}`;
    const left = daysLeftLabel(soonest.days_left);
    if (items.length === 1) {
        return {
            title: `Гостевой доступ: ${targetLabel(soonest)}`,
            detail: [until, left].filter(Boolean).join(' · '),
            urgency: urgency(soonest.days_left),
        };
    }
    return {
        title: `Гостевой доступ: ${items.length} ${plural(
            items.length, 'выдача', 'выдачи', 'выдач')}`,
        detail: `ближайшая — ${until}${left ? ` · ${left}` : ''}`,
        urgency: urgency(soonest.days_left),
    };
};
