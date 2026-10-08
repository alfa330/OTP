/* Тренинги и отклонения интервалов в «Графиках работы» лежат в памяти по
 * операторам, а с сервера приходят месяцами.
 *
 * Пришедший месяц ЗАМЕНЯЕТ свои прежние строки, а не дополняет их. Раньше
 * строки только добавлялись, и удалённый на сервере тренинг оставался на экране
 * до перезагрузки страницы — вместе с «подтверждённым» интервалом, который он
 * накрывал.
 *
 * Модуль чистый и без импортов: его целиком исполняет тест
 * (tests/training_month_rows.test.mjs).
 */

const rowMonth = (row) => String(row?.date ?? '').slice(0, 7);
const rowId = (row) => (row?.id === null || row?.id === undefined || row?.id === '' ? '' : String(row.id));

/* byOperator — { [id оператора]: строки }, rows — ответ сервера за monthKey
 * ('YYYY-MM'). Строки остальных месяцев не трогаются; у операторов, которых
 * месяц не коснулся, остаётся прежний массив — их ячейки не перерисовываются. */
export function replaceMonthRows(byOperator, rows, monthKey) {
    const month = String(monthKey ?? '');
    const incoming = new Map();
    const incomingIds = new Set();
    for (const row of (Array.isArray(rows) ? rows : [])) {
        const operatorId = Number(row?.operator_id);
        if (!Number.isFinite(operatorId) || operatorId <= 0) continue;
        const key = String(operatorId);
        if (!incoming.has(key)) incoming.set(key, []);
        incoming.get(key).push(row);
        if (rowId(row)) incomingIds.add(rowId(row));
    }

    const next = {};
    for (const [key, list] of Object.entries(byOperator || {})) {
        const current = Array.isArray(list) ? list : [];
        const kept = current.filter(row => rowMonth(row) !== month && !incomingIds.has(rowId(row)));
        if (kept.length === current.length && !incoming.has(key)) {
            next[key] = list;
        } else if (kept.length > 0 || incoming.has(key)) {
            next[key] = kept.concat(incoming.get(key) || []);
        }
        incoming.delete(key);
    }
    for (const [key, list] of incoming) next[key] = list;
    return next;
}

/* Несколько запросов одного месяца в полёте: ответ применяется, только если он
 * новее уже применённого. Иначе запоздавший ответ, отправленный ДО сохранения,
 * заменил бы месяц своим старым составом — и только что записанный тренинг
 * пропал бы с экрана.
 *
 * Сравнение именно с ПРИМЕНЁННЫМ, а не с последним отправленным: если самый
 * свежий запрос упал, годный ответ предыдущего не должен пропасть — месяц
 * остался бы пустым, а все его интервалы «Ожидает».
 *
 * Возвращает «принять ответ?»: зовётся один раз, когда ответ пришёл, и при
 * согласии запоминает его как применённый. */
export function trackMonthRequest(requestsByMonth, monthKey) {
    const month = String(monthKey ?? '');
    const state = requestsByMonth[month] || (requestsByMonth[month] = { sent: 0, applied: 0 });
    state.sent += 1;
    const ticket = state.sent;
    return () => {
        if (ticket <= state.applied) return false;
        state.applied = ticket;
        return true;
    };
}
