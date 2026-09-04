/* Общее для окна новости и для витрины редактора.
 *
 * Отдельный модуль, а не копия в каждом: подпись должности и формат даты
 * человек видит в обоих местах, и разойдись они — одна и та же новость
 * подписывалась бы в списке иначе, чем в окне.
 */

/** Названия должностей по-русски. Ключ — значение users.role. */
export const ROLE_TITLES = {
    super_admin: 'коммерческий директор',
    admin: 'руководитель',
    sv: 'супервайзер',
    supervisor: 'супервайзер',
    trainer: 'тренер',
    operator: 'оператор',
    trainee: 'стажёр',
    hr_manager: 'HR',
    accounting_manager: 'бухгалтерия',
    marketing_manager: 'маркетинг',
};

export const roleTitle = (role) => ROLE_TITLES[String(role || '').toLowerCase()] || '';

/** Инициалы для кружка автора — те же две буквы, что и в макете постановки. */
export const initialsOf = (name) => String(name || '')
    .split(/\s+/).filter(Boolean).slice(0, 2)
    .map((part) => part[0].toUpperCase()).join('') || '—';

/* «сегодня, 09:14». Абсолютную дату показываем только когда новость не
   сегодняшняя: у вчерашней «09:14» без даты вводит в заблуждение. */
export const publishedLabel = (iso) => {
    if (!iso) return '';
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return '';
    const time = at.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    const today = new Date();
    const sameDay = at.getFullYear() === today.getFullYear()
        && at.getMonth() === today.getMonth()
        && at.getDate() === today.getDate();
    if (sameDay) return `сегодня, ${time}`;
    return `${at.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long' })}, ${time}`;
};

/* delayLabel («кнопка загорится через N с») здесь больше нет: подпись под
   полем задержки пересказывала нажатый чип и число в соседнем поле — то же
   значение третий раз, — и ушла вместе с остальными пояснениями формы под «i»
   (решение владельца 02.09.2026). Функция осталась бы мёртвым кодом. */

/* ── КАНАЛ ТЫЧКА ────────────────────────────────────────────────────────────
 *
 * Модуль-подписка, а НЕ состояние в App и не проп. Причина ровно одна и она
 * дорого стоила: окно смонтировано внутри `sidebarTree = useMemo(...)`
 * в App.jsx, и проп `pokeNonce`, положенный в состояние App, замерзал бы на
 * нуле — список зависимостей того useMemo насчитывает под сорок значений, и
 * новое в нём забыть проще, чем вспомнить. Тычок не доезжал бы до окна вовсе,
 * причём молча: ни ошибки, ни падения, просто «у открытой вкладки новость не
 * всплывает».
 *
 * Второе, что это чинит: состояние в корне App перерисовывало бы ВЕСЬ портал
 * на каждое чужое уведомление колокола — App.jsx это 50 тысяч строк разметки.
 *
 * Подписчиков может быть сколько угодно, отписка обязательна (окно живёт
 * столько же, сколько сессия, но в тестах и при смене пользователя монтируется
 * заново).
 */
const newsPokeListeners = new Set();

/** Позвать всех подписчиков. Стабильная функция модуля — её можно отдавать
 *  пропом куда угодно, не боясь за списки зависимостей. */
export const emitNewsPoke = () => {
    newsPokeListeners.forEach((listener) => {
        try {
            listener();
        } catch {
            /* один сломавшийся подписчик не должен глушить остальных */
        }
    });
};

/** Подписаться на тычок. Возвращает функцию отписки. */
export const subscribeNewsPoke = (listener) => {
    newsPokeListeners.add(listener);
    return () => newsPokeListeners.delete(listener);
};
