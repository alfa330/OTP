/* Словарь и мелкая логика раздела «Лиды OLX».
 *
 * Вынесено из разметки намеренно: подписи исходов нужны и таблице журнала, и
 * фильтру, и ленте диалогов, а логика времени — трём местам сразу. Пока это
 * жило внутри вью, любая правка подписи означала правку в двух местах, и они
 * успевали разъехаться.
 */

/* Исходы обработки обращения. Коды приходят с сервера
 * (olx_amo/schema.py::JOURNAL_RESULTS), человеку они не показываются.
 * Порядок = порядок в фильтре. Тест сторожит, что подписи есть у всех исходов,
 * кроме служебного `skipped`. */
export const RESULTS = [
    { value: '', label: 'Все исходы' },
    { value: 'lead_created', label: 'Сделка создана' },
    { value: 'canned_reply', label: 'Отправлен ответ' },
    { value: 'human_reply', label: 'Ответил сотрудник' },
    { value: 'needs_human', label: 'Ждёт ответа человека' },
    { value: 'duplicate', label: 'Повтор за день' },
    { value: 'manual_review', label: 'Нужна проверка' },
    { value: 'error', label: 'Ошибка' },
];

export const RESULT_LABEL = RESULTS.reduce((acc, item) => {
    if (item.value) acc[item.value] = item.label;
    return acc;
}, {});

/* Тон исхода. Нейтральное состояние НЕ красим вовсе: если раскрасить всё,
 * «плохо» перестаёт бросаться в глаза. Тона — только из палитры ios.jsx
 * (slate/green/red/blue/amber), иначе бейдж молча станет серым. */
export const RESULT_TONE = {
    error: 'red',
    manual_review: 'amber',
    needs_human: 'blue',
    lead_created: 'green',
};

export const STATE_LABEL = {
    ok: 'Работает',
    needs_auth: 'Нужен вход владельца',
    not_configured: 'Нет доступов',
    disabled: 'Выключен',
    error: 'Ошибка',
};

export const STATE_TONE = {
    ok: 'slate',
    needs_auth: 'amber',
    not_configured: 'slate',
    disabled: 'slate',
    error: 'red',
};

/* Порог SLA из пункта 6.2 ТЗ — минута от отклика до сделки. */
export const SLA_MS = 60 * 1000;

/* Время с сервера приходит БЕЗ часового пояса: это стенные часы Алматы
 * (olx_amo/queries.py::now_almaty). `new Date(строка)` трактовал бы такую
 * строку как местное время браузера — на машине не в Алматы лента чата
 * поехала бы на часы. Поэтому разбираем строку сами и собираем дату из частей:
 * что сервер назвал 21:57, то и покажем как 21:57. */
export const wallTime = (value) => {
    const match = String(value || '').match(
        /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!match) return null;
    const [, y, mo, d, h, mi, se] = match;
    return new Date(+y, +mo - 1, +d, +h, +mi, +(se || 0));
};

export const fmtTime = (value) => {
    const date = wallTime(value);
    if (!date) return '—';
    return date.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
};

export const fmtClock = (value) => {
    const date = wallTime(value);
    if (!date) return '';
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

/* Ключ дня для разделителя в ленте. Считаем по частям даты, а не срезом
 * ISO-строки: срез сломался бы на строке с зоной. */
export const dayKey = (value) => {
    const date = wallTime(value);
    if (!date) return '';
    return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
};

export const dayLabel = (value) => {
    const date = wallTime(value);
    if (!date) return '';
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);
    if (dayKey(value) === `${today.getFullYear()}-${today.getMonth()}-${today.getDate()}`) {
        return 'Сегодня';
    }
    if (dayKey(value) === `${yesterday.getFullYear()}-${yesterday.getMonth()}-${yesterday.getDate()}`) {
        return 'Вчера';
    }
    const sameYear = date.getFullYear() === today.getFullYear();
    return date.toLocaleDateString('ru-RU', {
        day: 'numeric', month: 'long', year: sameYear ? undefined : 'numeric',
    });
};

export const fmtAgo = (value) => {
    const date = wallTime(value);
    if (!date) return 'ни разу';
    const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return `${seconds} с назад`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} мин назад`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} ч назад`;
    return `${Math.round(seconds / 86400)} дн назад`;
};

export const fmtWaiting = (minutes) => {
    if (minutes === null || minutes === undefined) return '—';
    if (minutes < 60) return `${minutes} мин`;
    if (minutes < 1440) return `${Math.round(minutes / 60)} ч`;
    return `${Math.round(minutes / 1440)} дн`;
};

export const fmtLatency = (ms) => {
    if (ms === null || ms === undefined) return '—';
    if (ms < 1000) return `${ms} мс`;
    return `${(ms / 1000).toFixed(1)} с`;
};

export const fmtPhone = (value) => {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length !== 11) return value || '—';
    return `+${digits[0]} ${digits.slice(1, 4)} ${digits.slice(4, 7)} `
        + `${digits.slice(7, 9)} ${digits.slice(9)}`;
};

export const isoToday = () => {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

/* Русское склонение для счётчиков. Одно на раздел, чтобы «1 группа» и
 * «5 групп» не расходились между блоками. */
export const plural = (count, one, few, many) => {
    const tail = count % 100;
    if (tail >= 11 && tail <= 14) return many;
    switch (count % 10) {
        case 1: return one;
        case 2:
        case 3:
        case 4: return few;
        default: return many;
    }
};

/* Разбивка ленты сообщений по дням — чистая функция, чтобы её можно было
 * проверить без React. */
export const groupByDay = (messages) => {
    const groups = [];
    let current = null;
    for (const message of messages || []) {
        const key = dayKey(message.at);
        if (!current || current.key !== key) {
            current = { key, label: dayLabel(message.at), messages: [] };
            groups.push(current);
        }
        current.messages.push(message);
    }
    return groups;
};
