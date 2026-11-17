/*
 * «Отчёт по сменам» · «Чат» (SALES-1, #424) — смены верификаторов в окне «Отчёт по сменам» табло ОП.
 * Чистые функции экрана: колонки ТЗ с подсказками, журнал смены, запросы. Без React — проверяются
 * `node --test`.
 *
 * Расчёт — на сервере (op_wallboard/chat_shifts.py). Фильтры, страницы, форматы ЧЧ:ММ:СС и выгрузка —
 * общие с отчётом линии (opActivityShared.js): у строк смен чата те же поля для фильтра (дата,
 * оператор, группа), и правило отбора у экрана и файла одно.
 */

export const OP_CHAT_SHIFTS_PATH = '/api/op_wallboard/chat_shifts';
export const OP_CHAT_SHIFTS_JOURNAL_PATH = '/api/op_wallboard/chat_shifts/journal';
export const OP_CHAT_SHIFTS_EXPORT_PATH = '/api/op_wallboard/chat_shifts/export';

/*
 * Колонки — строго в порядке ТЗ и его словами. Подсказка «i» — как считается колонка: её ищут, когда
 * сверяют цифры, и постоянной строкой под таблицей она была бы шумом.
 */
export const OP_CHAT_SHIFT_COLUMNS = [
    { key: 'date', label: 'Дата', hint: 'Дата начала смены: ночная смена целиком относится к вечеру, с которого началась.' },
    { key: 'name', label: 'Оператор' },
    { key: 'group', label: 'Группа', hint: 'Группа, в которой оператор числился в день смены.' },
    { key: 'login', label: 'Время входа в систему',
      hint: '«Начать смену» в «Чатах ОП». Серым — прошлую смену не закрыли, и вход — первое сообщение после перерыва в работе.' },
    { key: 'first_reply', label: 'Среднее время первого ответа', numeric: true,
      hint: 'Как «Ср. ответ» в «Чатах ОП»: у чата за день одно значение — от первого сообщения клиента за день до первого ответа; засчитано тому, кто ответил первым. Сумма этих времён, делённая на число чатов с его первым ответом.' },
    { key: 'handling', label: 'Общее время обработки чатов', numeric: true,
      hint: 'Обработка каждого чата — от сообщения клиента, на которое ответил оператор (но не больше 5 минут до ответа), до его последнего сообщения; пауза в переписке дольше 15 минут её прерывает. Только время в «Активном». Чаты, которые шли параллельно, складываются.' },
    { key: 'active', label: 'Активное время работы', numeric: true,
      hint: 'То же время одной лентой на все чаты: минута, когда в работе было три чата, считается один раз.' },
    { key: 'waiting', label: 'Время ожидания', numeric: true,
      hint: 'Время в системе без обработки чатов: «Активный», когда оператор не вёл ни одного чата, и «Тренинг».' },
    { key: 'break', label: 'Время перерывов', numeric: true, hint: 'Сумма периодов статуса «Перерыв».' },
    { key: 'tech', label: 'Время технического перерыва', numeric: true,
      hint: 'Сумма периодов статуса «Тех. пауза». Начало и конец каждого — в журнале смены и в Excel.' },
    { key: 'taken', label: 'Количество взятых в работу чатов', numeric: true,
      hint: 'Разные чаты, где оператор в эту смену писал клиенту или нажал «Ответ не нужен». Повторное открытие того же чата — не новый чат.' },
    { key: 'processed', label: 'Количество обработанных чатов', numeric: true,
      hint: 'Из них те, где разговор закончил он: его сообщение или «Ответ не нужен» — последнее действие сотрудников, пока переписка шла без пауз дольше 15 минут. Если клиент ответил и разговор продолжил коллега — чат обработал коллега.' },
    { key: 'logout', label: 'Время выхода из системы',
      hint: '«Закончить смену», закрытие смены системой (портал закрыт) или выход из iCORE Phone. Серым — выход не зафиксирован: показано последнее действие смены.' },
    { key: 'worked', label: 'Фактически отработанное время', numeric: true,
      hint: 'Выход минус вход минус перерывы. Тех. перерывы и тренинг не вычитаются; время вне системы — портал был закрыт — не входит.' },
];

/* Подсказка у выхода, который поставила система, — словами, без цвета: смена закрыта не человеком. */
export const OP_CHAT_LOGOUT_TITLES = {
    auto: 'Смену закрыла система: портал был закрыт',
    long: 'Смену закрыла система: статус не менялся 16 часов',
    phone: 'Выход из iCORE Phone закрыл и смену чата',
    cut: 'Смену не закрыли — показано последнее действие смены',
};

export const chatLogoutTitle = (row) => OP_CHAT_LOGOUT_TITLES[row?.logout_kind] || undefined;

/* Имя файла собирает фронт: Content-Disposition через CORS сюда не доходит. */
export const chatShiftsExportFileName = (month) => `smeny_chat_${month || 'month'}.xlsx`;

/* Журнал берётся из того же расчёта месяца на сервере: смену находят по человеку и входу. */
export const chatJournalQuery = (shift) => {
    const query = new URLSearchParams();
    query.set('operator_id', String(shift?.operator_id ?? ''));
    query.set('start', shift?.login_at || '');
    return query.toString();
};

/*
 * Журнал смены чата: «Обработка чатов» и «Ожидание» внутри «Активного», паузы, «Вне системы» и выход.
 * Цвета — те же, что у журнала линии с тем же смыслом: работа — синим, как «В разговоре», ожидание —
 * нейтральное, паузы — своими цветами, вне системы — красным, как «Офлайн».
 */
export const OP_CHAT_JOURNAL_STYLE = {
    work: { chip: 'bg-blue-100 text-blue-700', bar: 'bg-blue-500' },
    wait: { chip: 'bg-slate-100 text-slate-600', bar: 'bg-slate-300' },
    break: { chip: 'bg-orange-100 text-orange-700', bar: 'bg-orange-400' },
    tech: { chip: 'bg-fuchsia-100 text-fuchsia-700', bar: 'bg-fuchsia-400' },
    training: { chip: 'bg-purple-100 text-purple-700', bar: 'bg-purple-400' },
    offline: { chip: 'bg-red-100 text-red-700', bar: 'bg-red-400' },
    logout: { chip: 'bg-slate-50 text-slate-500 ring-1 ring-slate-200', bar: 'bg-slate-200' },
    other: { chip: 'bg-slate-100 text-slate-600', bar: 'bg-slate-300' },
};

export const chatJournalStyle = (style) => OP_CHAT_JOURNAL_STYLE[style] || OP_CHAT_JOURNAL_STYLE.other;

/*
 * Фильтр журнала — метки-итоги, как у линии: сколько всего обработки, ожидания, пауз и времени вне
 * системы. «Ожидание» здесь — отрезки без чата в «Активном»; тренинг в колонке «Время ожидания» тоже
 * есть, но в журнале он своим цветом среди пауз — по нему видно, откуда разница. Строка «Выход» —
 * мгновение, а не время вне системы: метку «Вне системы 00:00:00» она не зажигает (шум).
 */
export const OP_CHAT_JOURNAL_FILTERS = [
    { key: 'all', label: 'Все' },
    { key: 'work', label: 'Обработка', kinds: ['work'] },
    { key: 'wait', label: 'Ожидание', kinds: ['wait'] },
    { key: 'pause', label: 'Паузы', kinds: ['break', 'tech', 'training'] },
    { key: 'offline', label: 'Вне системы', kinds: ['offline'] },
];

export const chatJournalFilterChips = (segments) => {
    const sums = {};
    for (const segment of segments || []) {
        sums[segment.kind] = (sums[segment.kind] || 0) + Math.max(0, Number(segment.seconds) || 0);
    }
    return OP_CHAT_JOURNAL_FILTERS
        .map((chip) => ({
            ...chip,
            seconds: chip.kinds ? chip.kinds.reduce((sum, kind) => sum + (sums[kind] || 0), 0) : null,
            present: !chip.kinds || (segments || []).some((segment) => chip.kinds.includes(segment.kind)),
        }))
        .filter((chip) => chip.present);
};

export const filterChatJournal = (segments, key) => {
    const chip = OP_CHAT_JOURNAL_FILTERS.find((item) => item.key === key);
    if (!chip || !chip.kinds) return segments || [];
    return (segments || []).filter((segment) => chip.kinds.includes(segment.kind));
};
