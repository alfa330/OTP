/* Подготовка слепка дела к показу в учебной CRM.
 *
 * Чистый модуль без React: его гоняют тесты обычным node.
 *
 * ЗАЧЕМ СДВИГ ДАТ. Дело пишется один раз, датами в ISO относительно поля
 * anchor. Без сдвига дело, написанное в августе, в декабре покажет ведомость
 * за июль — и стажёр, который учится читать «когда это было», научится читать
 * неправду. Поэтому все даты уезжают на (сегодня − anchor), а порядок событий
 * сохраняется.
 *
 * ПОДПИСИ — как в CRM: «20.11.2026 12:14», этапы заказа — с секундами. Рядом
 * с подписью остаётся день в ISO (day): по нему фильтры периода в карточке
 * водителя отбирают строки так же, как CRM.
 */

const two = (value) => String(value).padStart(2, '0');
const DAY = 24 * 3600 * 1000;

/** Разбор ISO без часового пояса: «2026-08-18T06:17[:44]» или «2026-08-18». */
const parseIso = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(String(iso || ''));
    if (!match) return null;
    const [, y, m, d, hh, mm, ss] = match;
    return {
        date: Date.UTC(Number(y), Number(m) - 1, Number(d)),
        time: hh === undefined ? null : { hh, mm, ss: ss === undefined ? null : ss },
    };
};

/** Сколько дней между anchor и сегодня. Считаем по календарю, не по часам. */
export const shiftDays = (anchor, today) => {
    const from = parseIso(anchor);
    if (!from || !today) return 0;
    const to = Date.UTC(today.year, today.month - 1, today.day);
    return Math.round((to - from.date) / DAY);
};

/** Сдвинутая дата: день ISO, время и обе подписи. */
const moved = (iso, days) => {
    const parsed = parseIso(iso);
    if (!parsed) return null;
    const at = new Date(parsed.date + days * DAY);
    const day = `${at.getUTCFullYear()}-${two(at.getUTCMonth() + 1)}-${two(at.getUTCDate())}`;
    const date = `${two(at.getUTCDate())}.${two(at.getUTCMonth() + 1)}.${at.getUTCFullYear()}`;
    const { time } = parsed;
    return {
        day,
        date,
        time: time ? `${time.hh}:${time.mm}${time.ss ? `:${time.ss}` : ''}` : null,
        iso: time ? `${day}T${time.hh}:${time.mm}:${time.ss || '00'}` : day,
        minutes: time ? `${time.hh}:${time.mm}` : null,
    };
};

/** «19.08.2026 10:05» — так CRM подписывает время обращений, операций и заказов. */
export const fmtCrm = (iso, days = 0) => {
    const at = moved(iso, days);
    if (!at) return '';
    return at.minutes ? `${at.date} ${at.minutes}` : at.date;
};

/** «18.08.2026 06:17:44» — этапы заказа, где спор идёт о секундах. */
export const fmtCrmSec = (iso, days = 0) => {
    const at = moved(iso, days);
    if (!at) return '';
    return at.time ? `${at.date} ${at.time.length === 5 ? `${at.time}:00` : at.time}` : at.date;
};

/** «18.08.2026». */
export const fmtDate = (iso, days = 0) => {
    const at = moved(iso, days);
    return at ? at.date : '';
};

const list = (value) => (Array.isArray(value) ? value : []);

/* ── Слепки в форме кабинета Диспетчерской ─────────────────────────────────
   Дела голосового тренажёра (voice_trainer/cases) пишутся по контракту, где
   суммы — строки кабинета («−618,95»), заказ — номер с карточкой, а GPS —
   статусы с текстовой длительностью. Рабочее место теперь одна CRM, но дела
   соседней дорожки обязаны открываться в ней так же: приводим к форме CRM. */

/** «−618,95 ₸» → -618.95; понимает минус-знак, дефис и пробелы-разделители. */
export const parseMoney = (text) => {
    if (typeof text === 'number') return text;
    const raw = String(text || '').replace(/−/g, '-').replace(/[\s  ₸]/g, '').replace(',', '.');
    const value = Number.parseFloat(raw);
    return Number.isFinite(value) ? value : 0;
};

const ORDER_STATUS = { Завершён: 'complete', Выполнен: 'complete', Отменён: 'cancelled' };
const GPS_TYPE = {
    Свободен: 'free', Занят: 'busy', 'На заказе': 'in_order', Офлайн: 'offline',
    'Выход на линию': 'login', 'Уход с линии': 'logout',
};
const STAGE_NAMES = new Set(['Создан', 'Выехал', 'На месте', 'В пути', 'Выполнено']);

/* «4 мин», «2 ч», «3 дня» → секунды; «—» → null. */
const parseSpan = (text) => {
    const raw = String(text || '');
    const minutes = /(\d+)\s*мин/.exec(raw);
    const hours = /(\d+)\s*ч/.exec(raw);
    const days = /(\d+)\s*д/.exec(raw);
    if (!minutes && !hours && !days) return null;
    return (Number(days?.[1] || 0) * 24 * 60 + Number(hours?.[1] || 0) * 60 + Number(minutes?.[1] || 0)) * 60;
};

const fromFleetContract = (source) => {
    const person = source.contractor || {};
    const crm = source.crm || {};
    const tickets = list(crm.tickets);
    return {
        ...source,
        contractor: { ...person, balance: parseMoney(person.balance), limit: parseMoney(person.limit) },
        transactions: list(source.transactions).map((t, index) => ({
            id: `t${index + 1}`,
            at: t.at,
            event: t.event,
            order: /Заказ\s+(\d+)/.exec(t.event || '')?.[1] || null,
            category: t.category,
            amount: parseMoney(t.sum),
            balance: parseMoney(t.balance),
            comment: t.comment || '',
            by: t.by || '',
            park: Boolean(t.park),
        })),
        orders: list(source.orders).map((o) => ({
            id: o.order_uuid || `o${o.id}`,
            number: String(o.id),
            at: o.at,
            finished_at: o.finished_at || null,
            status: ORDER_STATUS[o.status] || 'failed',
            cancel_reason: o.cancel_reason || '',
            cancelled_by: '',
            from: o.address || '',
            to: o.address_to || '',
            category: o.category || '',
            km: o.distance_km ? parseMoney(o.distance_km) : null,
            price: o.price ? parseMoney(o.price) : null,
            payment: o.card?.payment || '',
            stages: list(o.card?.stages).filter((stage) => STAGE_NAMES.has(stage.name))
                .map((stage) => ({ name: stage.name, at: stage.at })),
        })),
        gps: list(source.gps_log).map((g) => ({
            at: g.at,
            type: GPS_TYPE[g.status] || 'mode',
            label: g.status,
            sec: parseSpan(g.time),
            km: /\d/.test(String(g.distance || '')) ? parseMoney(String(g.distance).replace(/км/, '')) : null,
            order: null,
            details: g.details || '',
        })),
        crm: {
            prefill: crm.prefill || {},
            history: crm.history || (tickets.length ? {
                requests: tickets.map((t, index) => ({
                    id: 1780000 + index, at: t.at, type_id: null, category: t.category, operator: t.author, source: t.source,
                })),
                open_tickets: tickets.filter((t) => t.status !== 'Закрыт').map((t, index) => ({
                    id: 41000 + index, at: t.at, status: t.status, type_id: null, category: t.category,
                })),
            } : null),
        },
    };
};

/* Форма кабинета узнаётся по строковым суммам ведомости или журналу GPS. */
const isFleetContract = (source) => Array.isArray(source.gps_log)
    || list(source.transactions).some((t) => typeof t.sum === 'string');

/* Точка во времени для строки: ISO со сдвигом, день для фильтра, подпись. */
const stamp = (iso, days) => {
    const at = moved(iso, days);
    return at
        ? { at: at.iso, day: at.day, clock: at.minutes || '00:00', when: fmtCrm(iso, days) }
        : { at: '', day: '', clock: '', when: '' };
};

/**
 * Подготовить слепок для экранов: сдвинуть даты и добавить подписи CRM. ISO
 * остаётся рядом — по нему считают логика и фильтры периода.
 */
export const prepareCase = (raw, today) => {
    const given = raw && typeof raw === 'object' ? raw : {};
    const source = isFleetContract(given) ? fromFleetContract(given) : given;
    const days = shiftDays(source.anchor, today);
    const contractor = source.contractor || {};
    const crm = source.crm || {};

    return {
        ...source,
        shiftedBy: days,
        park: source.park || {},
        call: source.call || {},
        contractor: {
            ...contractor,
            hired_text: fmtDate(contractor.hired, days),
            fired_text: contractor.fired ? fmtDate(contractor.fired, days) : '',
            created_text: fmtCrm(contractor.created, days),
        },
        car: source.car || null,
        transactions: list(source.transactions).map((t) => ({ ...t, ...stamp(t.at, days) })),
        orders: list(source.orders).map((o) => {
            const start = stamp(o.at, days);
            return {
                ...o,
                ...start,
                finished_at: o.finished_at ? moved(o.finished_at, days).iso : null,
                finished_when: o.finished_at ? fmtCrm(o.finished_at, days) : '',
                stages: list(o.stages).map((stage) => ({
                    ...stage,
                    at: moved(stage.at, days)?.iso || '',
                    when: fmtCrmSec(stage.at, days),
                })),
            };
        }),
        gps: list(source.gps).map((g) => ({ ...g, ...stamp(g.at, days) })),
        photo_days: list(source.photo_days).map((p) => ({ ...p, ...stamp(p.date, days), when: fmtDate(p.date, days) })),
        changes: list(source.changes).map((c) => ({ ...c, ...stamp(c.at, days) })),
        bonuses: {
            active: list(source.bonuses?.active),
            previous: list(source.bonuses?.previous),
        },
        crm: {
            prefill: crm.prefill || {},
            history: crm.history ? {
                requests: list(crm.history.requests).map((r) => ({ ...r, ...stamp(r.at, days) })),
                open_tickets: list(crm.history.open_tickets).map((t) => ({ ...t, ...stamp(t.at, days) })),
            } : null,
        },
    };
};
