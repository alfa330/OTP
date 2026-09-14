/*
 * Подписи и форматтеры раздела «Объявления OLX» (задача #299).
 *
 * Вынесено из экрана по той же причине, что и olxMeta.js у «Лидов»: экран
 * занимается раскладкой и состоянием, а словарь подписей меняется отдельно и
 * читается глазами целиком.
 *
 * Про цвет здесь и в экране. Красится ТОЛЬКО отклонение: отказ площадки,
 * просроченное объявление, текст, не проходящий правила. Успешная правка,
 * активное объявление и обычная строка остаются нейтральными — если раскрасить
 * всё, «плохо» перестаёт бросаться в глаза (требование владельца: лишний
 * визуальный шум — брак).
 */

/* Статусы объявления. Живьём по девяти кабинетам встречаются только active и
   outdated, остальные описаны в спецификации Partner API и появятся, если
   объявление уйдёт на модерацию или его снимут. Подписи человеческие: в
   кабинете OLX это вкладки, и называть их надо так же. */
export const STATUS_LABEL = {
    active: 'Активное',
    new: 'На модерации',
    moderated: 'Отклонено',
    blocked: 'Заблокировано',
    disabled: 'Отключено',
    limited: 'Сверх лимита',
    unpaid: 'Не оплачено',
    unconfirmed: 'Не подтверждено',
    outdated: 'Истекло',
    removed_by_user: 'Снято',
    removed_by_moderator: 'Снято модератором',
};

export const STATUS_TONE = {
    active: 'slate',
    new: 'amber',
    moderated: 'red',
    blocked: 'red',
    disabled: 'red',
    limited: 'amber',
    unpaid: 'amber',
    unconfirmed: 'amber',
    outdated: 'amber',
    removed_by_user: 'slate',
    removed_by_moderator: 'red',
};

export const statusLabel = (code) => STATUS_LABEL[code] || code || '—';
export const statusTone = (code) => STATUS_TONE[code] || 'slate';

/* Результат правки в истории. */
export const RESULT_LABEL = {
    applied: 'Применено',
    failed: 'Не удалось',
    rolled_back: 'Откат',
};

export const RESULT_TONE = {
    applied: 'slate',
    failed: 'red',
    rolled_back: 'amber',
};

export const ORIGIN_LABEL = { ai: 'ИИ', human: 'Человек' };

export const SOURCE_LABEL = {
    single: 'по одному',
    bulk: 'пачкой',
    rollback: 'откат',
};

/* Направление выводится из категории OLX — тот же словарь, что в olx_ads/ai.py.
   Держать его в двух местах приходится (питон не читается фронтом), поэтому
   числа продублированы явно и подписаны, чтобы расхождение было заметно. */
export const DIRECTION_BY_CATEGORY = {
    1812: 'Водители такси',
    1802: 'Курьеры',
    1801: 'Водители',
    1942: 'Другое',
    3031: 'Аренда авто',
};

export const directionLabel = (categoryId, fallback) => (
    DIRECTION_BY_CATEGORY[categoryId] || fallback || '—'
);

export const plural = (n, one, few, many) => {
    const abs = Math.abs(Number(n) || 0);
    const mod10 = abs % 10;
    const mod100 = abs % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
    return many;
};

export const fmtDateTime = (value) => {
    if (!value) return '—';
    const date = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', year: '2-digit',
        hour: '2-digit', minute: '2-digit',
    });
};

export const fmtDate = (value) => {
    if (!value) return '—';
    const date = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString('ru-RU', {
        day: '2-digit', month: '2-digit', year: '2-digit',
    });
};

/* «Обновлено 3 минуты назад» — снимок объявлений живёт своей жизнью, и человек
   должен видеть, насколько свежее то, что он правит. */
export const fmtAgo = (value) => {
    if (!value) return 'ещё не обновлялось';
    const date = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return String(value);
    const minutes = Math.round((Date.now() - date.getTime()) / 60000);
    if (minutes < 1) return 'только что';
    if (minutes < 60) return `${minutes} ${plural(minutes, 'минуту', 'минуты', 'минут')} назад`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours} ${plural(hours, 'час', 'часа', 'часов')} назад`;
    return fmtDateTime(value);
};

/* Описание в OLX — HTML из пяти разрешённых тегов. В списке нужен предпросмотр
   одной строкой, поэтому разметка снимается. Тот же разбор, что в
   olx_ads/validate.py::plain_text, только для показа. */
export const toPlain = (html) => String(html || '')
    .replace(/<\s*(br|\/p|\/li|\/ul)\s*\/?\s*>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/[ \t]+/g, ' ')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .join('\n')
    .trim();

export const excerpt = (html, limit = 160) => {
    const text = toPlain(html).replace(/\n+/g, ' · ');
    return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
};

/* Пределы площадки дублируются с бэкенда через /ping, но нужны и до ответа —
   счётчик под полем не должен мигать при открытии карточки. */
export const LIMITS = {
    title_min: 16,
    title_max: 70,
    description_min: 80,
    description_max: 9000,
};
