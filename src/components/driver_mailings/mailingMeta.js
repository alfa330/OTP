/*
 * Чистые помощники раздела «Рассылки»: подписи статусов, человеческое описание
 * отбора и окно отзыва. Вынесены отдельным модулем, чтобы их можно было
 * прогнать node-тестом без React (так же сделано в parcels/parcelMeta.js).
 */

import { formatCount, plural } from './mailingText.js';

/* Статус рассылки целиком. Цвет только там, где он что-то значит: успешная
   отправка — обычное состояние и не красится, а вот частичная и провал требуют
   внимания. */
export const MAILING_STATUS = {
    sending: { label: 'Отправляется', tone: 'blue' },
    sent: { label: 'Отправлена', tone: 'slate' },
    partial: { label: 'Ушла не везде', tone: 'amber' },
    failed: { label: 'Не ушла', tone: 'red' },
    revoked: { label: 'Отозвана', tone: 'slate' },
};

/* Статус по одной диспетчерской. */
export const TARGET_STATUS = {
    pending: { label: 'В очереди', tone: 'slate' },
    sent: { label: 'Ушла', tone: 'slate' },
    failed: { label: 'Ошибка', tone: 'red' },
    revoked: { label: 'Отозвана', tone: 'amber' },
};

export const statusMeta = (status) => MAILING_STATUS[status] || { label: status || '—', tone: 'slate' };
export const targetStatusMeta = (status) => TARGET_STATUS[status] || { label: status || '—', tone: 'slate' };

/**
 * Сколько секунд ещё можно отозвать рассылку.
 *
 * Кабинет разрешает отзыв в течение delete_limit.seconds (сейчас 300) от
 * момента отправки. Считаем от sent_at конкретной диспетчерской, а не от
 * времени создания записи: рассылка в пять парков уходит последовательно, и у
 * последнего окно закрывается позже.
 */
export const revokeSecondsLeft = (sentAt, windowSeconds, now = Date.now()) => {
    if (!sentAt) return 0;
    const started = new Date(sentAt).getTime();
    if (Number.isNaN(started)) return 0;
    const left = Math.floor((started + (windowSeconds || 0) * 1000 - now) / 1000);
    return left > 0 ? left : 0;
};

/** «4:37» — обратный отсчёт окна отзыва. */
export const formatCountdown = (seconds) => {
    const total = Math.max(0, Math.floor(seconds || 0));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
};

/**
 * Человеческое описание отбора получателей — для окна подтверждения и журнала.
 *
 * refs — справочники из ручки /filters, чтобы подписи совпадали с тем, что
 * человек видел в форме. Если справочника нет (журнал открыли отдельно),
 * показываем сами значения: лучше «econom», чем пустая строка.
 */
export const describeFilters = (filters, refsLike) => {
    const out = [];
    const source = filters || {};
    // Именно `|| {}`, а не значение по умолчанию в сигнатуре: справочники живут
    // в состоянии как null, пока не выбраны диспетчерские, а умолчание в
    // параметре срабатывает только на undefined. На этом раздел падал целиком
    // при первом открытии — окно подтверждения строит описание отбора всегда.
    const refs = refsLike || {};
    const nameOf = (list, id) => {
        const found = (list || []).find((item) => item.id === id || item.value === id);
        return (found && (found.name || found.label)) || id;
    };
    const joinNames = (list, ids) => (ids || []).map((id) => nameOf(list, id)).join(', ');

    const segment = (refs.segments || []).find((item) => item.id === source.segment);
    if (source.segment) {
        const subs = (source.subsegments || []).length
            ? `: ${joinNames(segment?.subsegments, source.subsegments)}`
            : '';
        out.push(`Сегмент — ${(segment && segment.name) || source.segment}${subs}`);
    }
    if (source.group) {
        const group = (refs.groups || []).find((item) => item.key === source.group);
        out.push(`Группа — ${(group && group.label) || source.group}`);
    }
    if ((source.city_ids || []).length) out.push(`Города — ${source.city_ids.join(', ')}`);
    if ((source.profession_ids || []).length) {
        out.push(`Профессия — ${joinNames(refs.professions, source.profession_ids)}`);
    }
    if ((source.contractor_statuses || []).length) {
        out.push(`Статус на линии — ${joinNames(refs.statuses, source.contractor_statuses)}`);
    }
    if ((source.car_categories || []).length) {
        out.push(`Категории — ${joinNames(refs.categories, source.car_categories)}`);
    }
    if ((source.car_amenities || []).length) {
        out.push(`Услуги — ${joinNames(refs.amenities, source.car_amenities)}`);
    }
    return out;
};

/** «Все водители выбранных диспетчерских» — когда отбора нет вовсе. */
export const describeAudience = (filters, refs) => {
    const parts = describeFilters(filters, refs);
    return parts.length ? parts : ['Все водители выбранных диспетчерских'];
};

/** Сколько фильтров реально задано — для счётчика на чипе. */
export const countActiveFilters = (filters) => {
    const source = filters || {};
    let count = 0;
    if (source.segment) count += 1;
    if (source.group) count += 1;
    ['city_ids', 'profession_ids', 'contractor_statuses', 'car_categories', 'car_amenities']
        .forEach((key) => { if ((source[key] || []).length) count += 1; });
    return count;
};

/** «1 144 водителя» — число и правильное слово рядом. */
export const recipientsLabel = (count) => (
    `${formatCount(count)} ${plural(count, 'водитель', 'водителя', 'водителей')}`
);

/** «3 диспетчерские» — для окна подтверждения. */
export const parksLabel = (count) => (
    `${count} ${plural(count, 'диспетчерская', 'диспетчерские', 'диспетчерских')}`
);
