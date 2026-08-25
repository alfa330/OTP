/* История версий: расчёты, которые можно проверить тестом.
 *
 * Отдельным модулем от WikiHistory.jsx по тем же соображениям, что и
 * guestAccess.js рядом: сам экран — модалка, которая наполняется по сети, и
 * серверный рендер до этих ветвлений не доходит. А ветвлений тут ровно столько,
 * сколько крайних случаев: у самой старой редакции нет предыдущей, у текущей
 * нет «текущей», а у только что созданной статьи нет ни того, ни другого.
 */

/** Ключ редакции для запроса сравнения: у текущей своего снимка нет. */
export const stateKey = (entry) => (
    entry && entry.version_id != null ? String(entry.version_id) : 'current');

/* Отметка времени — строкой, БЕЗ new Date().
 *
 * Сервер отдаёт наивное алматинское время («2026-08-24T16:58:00»), а
 * `new Date('2026-08-24T16:58:00')` браузер разбирает как МЕСТНОЕ: у человека в
 * другом часовом поясе вся история уезжает на несколько часов, и соседние по
 * времени правки меняются местами. То же правило и по той же причине действует
 * в guestAccess.js. */
export const fmtStamp = (iso) => {
    const parts = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/.exec(String(iso || ''));
    if (!parts) return '—';
    const [, year, month, day, hour, minute, second] = parts;
    // Секунды не для точности, а чтобы строки не двоились: правки идут
    // очередями по нескольку в минуту (у статьи «Приветствие» в проде две
    // подряд в 16:39), и без секунд две редакции читались бы как одна запись,
    // случайно продублированная.
    return `${day}.${month}.${year}, ${hour}:${minute}${second ? ':' + second : ''}`;
};

export const plural = (n, one, few, many) => {
    const mod100 = Math.abs(n) % 100;
    const mod10 = mod100 % 10;
    if (mod100 >= 11 && mod100 <= 14) return many;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return few;
    return many;
};

/* Что изменилось — словами читателя, а не именами полей ответа. */
export const CHANGE_LABELS = {
    content: 'Текст',
    title: 'Заголовок',
    summary: 'Аннотация',
    status: 'Статус',
};

/**
 * Что с чем сравнивать. Список приходит от новых к старым, поэтому
 * «предыдущая» — СЛЕДУЮЩАЯ в массиве, а текущая всегда первая.
 *
 * Режим вычисляется, а не хранится: выбранное человеком «сравнить с текущей»
 * не должно оставлять пустой экран, когда он затем ткнул в саму текущую
 * редакцию, — и наоборот.
 */
export const comparePair = (items, selectedKey, against = 'prev') => {
    const list = items || [];
    const index = list.findIndex((item) => item.key === selectedKey);
    if (index < 0) {
        return { entry: null, mode: null, from: null, to: null,
                 canPrev: false, canCurrent: false };
    }
    const entry = list[index];
    const older = list[index + 1] || null;
    const canPrev = !!older;
    // Текущая — верхняя строка списка; сравнивать её с самой собой нечего.
    const canCurrent = index > 0;
    const mode = (against === 'current' && canCurrent)
        ? 'current'
        : (canPrev ? 'prev' : (canCurrent ? 'current' : null));
    if (mode === 'current') {
        return { entry, mode, from: entry, to: list[0], canPrev, canCurrent };
    }
    if (mode === 'prev') {
        return { entry, mode, from: older, to: entry, canPrev, canCurrent };
    }
    return { entry, mode: null, from: null, to: null, canPrev, canCurrent };
};
