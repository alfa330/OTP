/* Вкладки карточки водителя в учебной CRM — данные так, как их показывает CRM.
 *
 * Чистый модуль без React: его читают тесты обычным node.
 *
 * Разметка вкладок — в crmDrivers.jsx (снята с рабочей CRM 20.11.2026), здесь
 * только то, ЧТО в них показывается: строки ведомости и заказов за период,
 * карточка заказа с этапами и комиссиями, GPS, заработок, бонусы. Числа — в
 * виде formatMoney карточки CRM: два знака, разряды через неразрывный пробел.
 *
 * Период — как в фильтрах CRM: дата и время начала, дата и время окончания;
 * по умолчанию «вчера 00:00 — сегодня 23:59».
 */

const DAY = 24 * 3600 * 1000;
const two = (value) => String(value).padStart(2, '0');

/** Число как formatNumber карточки CRM: ru-RU, заданное число знаков. */
export const fmtNumber = (value, digits) => Number(value || 0)
    .toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** Деньги вкладок карточки: «-526,67», «3 190,00». */
export const fmtMoney = (value) => fmtNumber(value, 2);

/** «08:41» или «00:08:41» — как formatDuration CRM. */
export const fmtDuration = (seconds, withSeconds = true) => {
    const total = Math.max(0, Math.round(seconds || 0));
    const head = `${two(Math.floor(total / 3600))}:${two(Math.floor((total % 3600) / 60))}`;
    return withSeconds ? `${head}:${two(total % 60)}` : head;
};

/** «5 мин», «2 ч 10 мин» — длительность события GPS. */
export const fmtShiftDuration = (seconds) => {
    if (seconds === null || seconds === undefined) return '';
    const minutes = Math.round(seconds / 60);
    return minutes < 60 ? `${minutes} мин` : `${Math.floor(minutes / 60)} ч ${minutes % 60} мин`;
};

/** Задержка между этапами: «+06:12». */
const after = (seconds) => `${two(Math.floor(seconds / 60))}:${two(seconds % 60)}`;

/** Дата ISO из миллисекунд местного времени тренажёра. */
export const isoDay = (ms) => {
    const at = new Date(ms);
    return `${at.getFullYear()}-${two(at.getMonth() + 1)}-${two(at.getDate())}`;
};

/** Период по умолчанию — вчера и сегодня, весь день, как в CRM. */
export const defaultPeriod = (nowMs) => ({
    from: isoDay(nowMs - DAY), to: isoDay(nowMs), timeFrom: '00:00', timeTo: '23:59',
});

/** Попадает ли строка (day + clock) в период. */
export const inPeriod = (row, period) => {
    if (!period || !row || !row.day) return true;
    const point = `${row.day}T${row.clock || '00:00'}`;
    return point >= `${period.from}T${period.timeFrom || '00:00'}` && point <= `${period.to}T${period.timeTo || '23:59'}`;
};

/* Операции старше двух суток Диспетчерская не отдаёт — CRM предупреждает. */
export const beyondWindow = (period, nowMs) => Boolean(period) && period.from < isoDay(nowMs - 2 * DAY);

/* ── Ведомость ─────────────────────────────────────────────────────────── */

const CASH = /наличн/i;

/** Строки ведомости за период — от свежих к старым. */
export const statementRows = (caseData, period, { withoutCash = false, category = '' } = {}) => (caseData.transactions || [])
    .filter((row) => inPeriod(row, period))
    .filter((row) => !withoutCash || !CASH.test(row.category))
    .filter((row) => !category || row.category === category)
    .map((row) => ({
        id: row.id,
        when: row.when,
        event: row.event,
        order: row.order,
        category: row.category,
        balance: row.balance,
        amount: row.amount,
        comment: row.comment,
        by: row.by,
    }));

/** Итог под ведомостью: сколько загружено, сумма, баланс на начало и конец. */
export const statementSummary = (rows) => {
    const sum = rows.reduce((total, row) => total + row.amount, 0);
    if (!rows.length) return { count: 0, sum: 0, before: null, after: null, change: null };
    const newest = rows[0];
    const oldest = rows[rows.length - 1];
    const before = Math.round((oldest.balance - oldest.amount) * 100) / 100;
    return { count: rows.length, sum, before, after: newest.balance, change: Math.round((newest.balance - before) * 100) / 100 };
};

/** Категории ведомости для фильтра «Категории». */
export const statementCategories = (caseData) => [...new Set((caseData.transactions || []).map((row) => row.category))];

/* ── Заказы ────────────────────────────────────────────────────────────── */

const ORDER_STATUS = {
    complete: ['Выполнен', 'badge-success'],
    cancelled: ['Отменён', 'badge-secondary'],
    failed: ['Не состоялся', 'badge-danger'],
    expired: ['Просрочен', 'badge-warning'],
};

/** Плашка статуса заказа — классы те же, что у orderStatusClass CRM. */
export const orderStatus = (status) => {
    const [label, tone] = ORDER_STATUS[status] || [status || '—', 'badge-info'];
    return { label, tone };
};

const carTitle = (caseData) => {
    const car = caseData.car;
    return car ? `${[car.brand, car.model].filter(Boolean).join(' ')} ${car.plate}`.trim() : '';
};

/** Заказы за период — от свежих к старым. */
export const orderRows = (caseData, period) => (caseData.orders || [])
    .filter((order) => inPeriod(order, period))
    .map((order) => ({
        id: order.id,
        number: order.number,
        status: order.status,
        car: carTitle(caseData),
        booked_at: order.when,
        ended_at: order.finished_when,
        cancel_reason: order.cancel_reason,
        from: order.from,
        to: order.to,
        category: order.category,
        km: order.km,
        price: order.price,
    }));

/** Итог таблицы заказов: пробег и стоимость. */
export const orderTotals = (rows) => ({
    count: rows.length,
    km: rows.reduce((total, row) => total + (row.km || 0), 0),
    price: rows.reduce((total, row) => total + (row.price || 0), 0),
});

/** Операции ведомости по заказу. */
const orderOps = (caseData, number) => (caseData.transactions || []).filter((row) => row.order === number);

const sum = (rows) => Math.round(rows.reduce((total, row) => total + row.amount, 0) * 100) / 100;

/**
 * Карточка заказа — окно «Заказ N» CRM: кто, на чём, этапы с задержками,
 * маршрут, оплата и комиссии, операции по заказу. Нет заказа — null.
 */
export const orderCard = (caseData, number) => {
    const order = (caseData.orders || []).find((item) => item.number === number);
    if (!order) return null;
    const person = caseData.contractor || {};
    const ops = orderOps(caseData, number);
    const change = sum(ops);
    const cash = CASH.test(order.payment || '');
    const service = ops.filter((row) => /комиссия сервиса/i.test(row.category));
    const park = ops.filter((row) => row.park);
    const stages = (order.stages || []).map((stage, index, list) => ({
        label: stage.name,
        at: stage.when,
        after: index > 0 && stage.at && list[index - 1].at
            ? after(Math.round((Date.parse(`${stage.at}Z`) - Date.parse(`${list[index - 1].at}Z`)) / 1000))
            : '',
    }));
    const tripStart = (order.stages || []).find((stage) => stage.name === 'В пути');
    const tripEnd = (order.stages || []).find((stage) => stage.name === 'Выполнено');
    const tripSeconds = tripStart && tripEnd
        ? Math.round((Date.parse(`${tripEnd.at}Z`) - Date.parse(`${tripStart.at}Z`)) / 1000) : null;
    const money = (value) => `${fmtMoney(value)} ₸`;
    return {
        order: {
            id: order.id,
            number: order.number,
            status: order.status,
            driver: [person.last, person.first].filter(Boolean).join(' '),
            car: carTitle(caseData),
            work_rule: person.terms || '',
            booked_at: order.when,
            cancelled_by: order.cancelled_by || '',
            cancel_reason: order.cancel_reason || '',
            price: order.price,
            category: order.category,
            mileage_km: order.km,
            trip_duration: tripSeconds === null ? '' : fmtDuration(tripSeconds),
            stages,
            segments: [{ from: { address: order.from }, to: [{ address: order.to }] }],
        },
        money: {
            driver_income: order.status === 'complete' ? money((cash ? order.price : 0) + change) : '',
            payment_method: order.payment || '',
            tariff: order.category,
            blocks: order.status === 'complete' ? [
                {
                    name: 'Стоимость заказа',
                    value: money(order.price),
                    groups: [{ name: cash ? 'Наличные — у водителя' : 'Безналичная оплата', value: money(order.price), items: [] }],
                },
                {
                    name: 'Удержания',
                    value: money(sum([...service, ...park])),
                    groups: [
                        {
                            name: 'Комиссии сервиса',
                            value: money(sum(service)),
                            items: service.map((row) => ({ name: row.category, description: row.comment, value: money(row.amount) })),
                        },
                        {
                            name: 'Комиссия парка',
                            value: money(sum(park)),
                            items: park.map((row) => ({ name: row.category, description: row.comment, value: money(row.amount) })),
                        },
                    ].filter((group) => group.items.length),
                },
            ] : [],
            transactions: ops.length ? [{
                name: ops[0].when.slice(0, 10),
                items: ops.map((row) => ({ description: row.category, at: row.when.slice(11), amount: row.amount })),
            }] : [],
            balance_change: ops.length ? change : null,
        },
    };
};

/* ── GPS ───────────────────────────────────────────────────────────────── */

const GPS_BADGE = {
    login: 'badge-primary',
    logout: 'badge-dark',
    in_order: 'badge-success',
    free: 'badge-info',
    busy: 'badge-warning',
    offline: 'badge-secondary',
};

/** Плашка события GPS — классы shiftBadgeClass CRM. */
export const gpsBadge = (type) => GPS_BADGE[type] || 'badge-light';

/** События GPS за период (не больше 48 часов в CRM) — от свежих к старым. */
export const gpsRows = (caseData, period, onlyLogins = false) => (caseData.gps || [])
    .filter((row) => inPeriod(row, period))
    .filter((row) => !onlyLogins || row.type === 'login' || row.type === 'logout')
    .map((row) => {
        const ref = row.order ? (caseData.orders || []).find((item) => item.number === row.order) : null;
        return {
            type: row.type,
            label: row.label,
            when: row.when,
            sec: row.sec,
            km: row.km,
            order: ref ? { number: ref.number, address: ref.from } : null,
            details: row.details,
        };
    });

/** Плитки над GPS: общий пробег, на заказе, холостой, офлайн. */
export const gpsSummary = (rows) => {
    const km = (types) => Math.round(rows.filter((row) => types.includes(row.type))
        .reduce((total, row) => total + (row.km || 0), 0) * 10) / 10;
    const inOrder = km(['in_order']);
    const idle = km(['free', 'busy']);
    const offline = km(['offline']);
    return [
        ['Общий пробег', Math.round((inOrder + idle + offline) * 10) / 10],
        ['На заказе', inOrder],
        ['Холостой пробег', idle],
        ['Офлайн', offline],
    ];
};

/* ── Заработок ─────────────────────────────────────────────────────────── */

/* Строки отчёта — группы операций, как их сводит Диспетчерская. */
const INCOME_GROUPS = [
    ['cash', 'Наличные'],
    ['card', 'Безналичная оплата'],
    ['service', 'Комиссии сервиса'],
    ['partner', 'Комиссии партнёра'],
    ['payout', 'Выплаты'],
    ['topup', 'Пополнения'],
];

const incomeGroup = (row) => {
    if (/оплата картой/i.test(row.category)) return 'card';
    if (/комиссия сервиса/i.test(row.category)) return 'service';
    if (row.park) return 'partner';
    if (/перевод/i.test(row.category)) return 'payout';
    if (/пополнение/i.test(row.category)) return 'topup';
    return 'service';
};

/** Отчёт «Заработок» за период. */
export const incomeSummary = (caseData, period) => {
    const orders = (caseData.orders || []).filter((order) => inPeriod(order, period));
    const ops = (caseData.transactions || []).filter((row) => inPeriod(row, period));
    const done = orders.filter((order) => order.status === 'complete');
    const values = Object.fromEntries(INCOME_GROUPS.map(([key]) => [key, 0]));
    done.filter((order) => CASH.test(order.payment || '')).forEach((order) => { values.cash += order.price; });
    ops.forEach((row) => { values[incomeGroup(row)] += row.amount; });
    const balances = INCOME_GROUPS.map(([key, label]) => ({ key, label, value: Math.round(values[key] * 100) / 100 }))
        .filter((row) => row.value !== 0);
    const total = Math.round(balances.filter((row) => !['payout', 'topup'].includes(row.key))
        .reduce((all, row) => all + row.value, 0) * 100) / 100;
    const work = (caseData.gps || []).filter((row) => inPeriod(row, period) && ['free', 'busy', 'in_order'].includes(row.type))
        .reduce((all, row) => all + (row.sec || 0), 0);
    return {
        completed: done.length,
        price: done.reduce((all, order) => all + order.price, 0),
        mileage_km: done.reduce((all, order) => all + (order.km || 0), 0),
        balances,
        total,
        work_seconds: work,
        per_hour: work ? Math.round((total / (work / 3600)) * 100) / 100 : 0,
        days: [...new Set(orders.map((order) => order.day))].sort().map((day) => ({
            day: `${day.slice(8, 10)}.${day.slice(5, 7)}`,
            completed: orders.filter((order) => order.day === day && order.status === 'complete').length,
            cancelled: orders.filter((order) => order.day === day && order.status === 'cancelled').length,
            failed: orders.filter((order) => order.day === day && order.status === 'failed').length,
        })),
    };
};

/* ── Изменения статусов и история ──────────────────────────────────────── */

/** Смены статуса онлайн/офлайн, которые CRM заметила по GPS. */
export const statusChanges = (caseData) => (caseData.gps || [])
    .filter((row) => row.type === 'login' || row.type === 'logout')
    .map((row) => ({
        when: row.when,
        what: 'Статус (онлайн/офлайн)',
        from: row.type === 'login' ? 'offline' : 'online',
        to: row.type === 'login' ? 'online' : 'offline',
    }));

/** Журнал условий работы — смены условий из истории изменений. */
export const workRuleLog = (caseData) => (caseData.changes || [])
    .filter((row) => row.field === 'Условия работы')
    .map((row) => ({ when: row.when, from: row.from, to: row.to, by: row.author }));

/** Прочие изменения карточки (фотоконтроль, вывод, автомобиль). */
export const otherChanges = (caseData) => (caseData.changes || [])
    .filter((row) => row.field !== 'Условия работы');
