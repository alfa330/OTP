/* Подготовка слепка дела к показу и поиск по исполнителям.
 *
 * Чистый модуль без React: его гоняют тесты обычным node.
 *
 * ЗАЧЕМ СДВИГ ДАТ. Слепок пишет владелец один раз и датами в ISO относительно
 * поля anchor. Без сдвига дело, написанное в августе, в декабре покажет
 * ведомость за июль — и стажёр, который учится читать «когда это было»,
 * научится читать неправду. Поэтому все даты уезжают на (сегодня − anchor), а
 * относительный порядок событий сохраняется.
 *
 * ЗАЧЕМ НАСТОЯЩИЙ ПОИСК. На смене оператор находит водителя по номеру из
 * звонка, а не глазами по списку из шести строк. Поиск обязан работать так же,
 * как в кабинете: с трёх знаков, по ФИО, телефону, номеру ВУ и позывному.
 */

const MONTHS_SHORT = ['янв.', 'февр.', 'мар.', 'апр.', 'мая', 'июн.',
    'июл.', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'];

const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

const two = (value) => String(value).padStart(2, '0');

/** Разбор ISO без часового пояса: «2026-08-18T06:17» или «2026-08-18». */
const parseIso = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(String(iso || ''));
    if (!match) return null;
    const [, y, m, d, hh, mm] = match;
    return {
        date: Date.UTC(Number(y), Number(m) - 1, Number(d)),
        time: hh === undefined ? null : { hh, mm },
    };
};

const DAY = 24 * 3600 * 1000;

/** Сколько дней между anchor и сегодня. Считаем по календарю, не по часам. */
export const shiftDays = (anchor, today) => {
    const from = parseIso(anchor);
    if (!from) return 0;
    const to = Date.UTC(today.year, today.month - 1, today.day);
    return Math.round((to - from.date) / DAY);
};

/** Сдвинутая дата как объект. */
const moved = (iso, days) => {
    const parsed = parseIso(iso);
    if (!parsed) return null;
    const at = new Date(parsed.date + days * DAY);
    return {
        year: at.getUTCFullYear(),
        month: at.getUTCMonth() + 1,
        day: at.getUTCDate(),
        time: parsed.time,
    };
};

/** «18 авг., 06:17» — так время подписано в кабинете. */
export const fmtДатаВремя = (iso, days = 0) => {
    const at = moved(iso, days);
    if (!at) return '';
    const head = `${at.day} ${MONTHS_SHORT[at.month - 1]}`;
    return at.time ? `${head}, ${at.time.hh}:${at.time.mm}` : head;
};

/** «19 июля» — так подписаны дни в фотоконтроле. */
export const fmtДень = (iso, days = 0) => {
    const at = moved(iso, days);
    return at ? `${at.day} ${MONTHS_GEN[at.month - 1]}` : '';
};

/** «22.08.2026» — так даты стоят в таблице обращений. */
export const fmtКоротко = (iso, days = 0) => {
    const at = moved(iso, days);
    return at ? `${two(at.day)}.${two(at.month)}.${at.year}` : '';
};

/* ── Подготовка слепка ───────────────────────────────────────────────────── */

/**
 * Привести слепок к виду, готовому для экранов: сдвинуть даты и добавить к
 * каждой записи человеческую подпись. ISO остаётся на месте — по нему считает
 * логика, а показывается подпись.
 */
export const prepareCase = (raw, today) => {
    const source = raw && typeof raw === 'object' ? raw : {};
    const days = shiftDays(source.anchor, today);
    const when = (iso) => fmtДатаВремя(iso, days);

    const list = (value) => (Array.isArray(value) ? value : []);

    return {
        ...source,
        shiftedBy: days,
        park: source.park || {},
        call: source.call || {},
        contractor: {
            ...(source.contractor || {}),
            license_from_text: fmtКоротко(source.contractor?.license_from, days),
            license_to_text: fmtКоротко(source.contractor?.license_to, days),
        },
        car: source.car || {},
        detail_blocks: list(source.detail_blocks),
        transactions: list(source.transactions).map((t) => ({ ...t, when: when(t.at) })),
        tx_totals: list(source.tx_totals),
        income: list(source.income),
        balance_history: list(source.balance_history).map((b) => ({ ...b, when: when(b.at) })),
        orders: list(source.orders).map((o) => ({ ...o, when: when(o.at) })),
        gps_tiles: list(source.gps_tiles),
        gps_log: list(source.gps_log).map((g) => ({ ...g, when: when(g.at) })),
        photo_days: list(source.photo_days).map((p) => ({ ...p, title: fmtДень(p.date, days) })),
        changes: list(source.changes).map((c) => ({ ...c, when: when(c.at) })),
        documents: list(source.documents),
        support: list(source.support).map(([q, status, updated, created]) => ([
            q, status, fmtКоротко(updated, days), fmtКоротко(created, days),
        ])),
        /* Строку героя дополняем тем, что есть только в его карточке: номером
           ВУ и позывным. По контракту строка списка их не несёт, а искать по
           ним оператор обязан — иначе поиск по ВУ не находит даже звонящего. */
        contractors: list(source.contractors).map((person) => (
            person.id && person.id === source.contractor?.id
                ? {
                    phone: source.contractor.phone,
                    license: source.contractor.license,
                    callsign: source.contractor.callsign,
                    ...person,
                }
                : person
        )),
        cars: list(source.cars),
        crm: source.crm || { prefill: {} },
    };
};

/* ── Поиск по исполнителям ───────────────────────────────────────────────── */

/** Порог поиска в кабинете — три знака. Меньше просто не срабатывает. */
export const SEARCH_MIN = 3;

const onlyDigits = (value) => String(value || '').replace(/\D/g, '');
const lower = (value) => String(value || '').toLowerCase().replace(/ё/g, 'е');

/**
 * Найти исполнителей. Возвращает { ready, items }: ready=false означает, что
 * запрос короче порога и список показывается целиком, а не «ничего не найдено».
 *
 * Телефон ищем по ЦИФРАМ: оператор набирает то +7 701…, то 7701…, то последние
 * четыре, и придираться к формату там, где кабинет его сам вычищает, значит
 * учить не тому.
 */
export const findContractors = (people, query) => {
    const raw = String(query || '').trim();
    if (raw.length < SEARCH_MIN) return { ready: false, items: people || [] };

    const text = lower(raw);
    /* По цифрам ищем ТОЛЬКО когда в запросе нет букв: иначе «AN000000» своими
       нулями попадал в номера телефонов и находил посторонних людей. */
    const digits = /[a-zа-яё]/i.test(raw) ? '' : onlyDigits(raw);

    const items = (people || []).filter((person) => {
        /* Телефон берём из ОБОИХ полей: по контракту строка списка несёт
           phone_pretty, а phone есть не всегда. Полагаться на одно из них —
           значит не находить звонящего в чужом слепке. */
        const phones = onlyDigits(person.phone) + ' ' + onlyDigits(person.phone_pretty);
        if (digits.length >= SEARCH_MIN && phones.includes(digits)) return true;
        if (lower(person.name).includes(text)) return true;
        if (lower(person.license).includes(text)) return true;
        if (lower(person.callsign).includes(text)) return true;
        return false;
    });
    return { ready: true, items };
};

/** Чем именно искали — для события ui.search. */
export const searchKind = (query) => {
    const raw = String(query || '').trim();
    if (!raw) return 'name';
    if (onlyDigits(raw).length >= SEARCH_MIN && onlyDigits(raw).length >= raw.length - 4) return 'phone';
    if (/^[A-Za-z]{2}\d+$/.test(raw)) return 'license';
    return 'name';
};

/* ── Правки над копией слепка ────────────────────────────────────────────────
 *
 * Половина ошибок новичка — не «не нашёл», а «полез менять то, что менять
 * нельзя». Значит такие кнопки в среде быть должны, иначе разбор о них не
 * скажет. Три правила без исключений:
 *
 *   правят ТОЛЬКО копию слепка внутри попытки;
 *   всегда оставляют след в ленте (это делает вызывающий, ui.action);
 *   наружу как данные не уходят никогда.
 */

/** Шаг начисления и списания. Фиксированный: диалог ввода суммы здесь лишний. */
export const MONEY_STEP = 1000;

const MINUS = '−';

/** «−618,95 ₸» → -618.95. Понимает и минус-знак, и дефис, и пробелы-разделители. */
export const parseMoney = (text) => {
    const raw = String(text || '')
        .replace(new RegExp(MINUS, 'g'), '-')
        .replace(/[\s  ]/g, '')
        .replace('₸', '')
        .replace(',', '.');
    const value = Number.parseFloat(raw);
    return Number.isFinite(value) ? value : 0;
};

/* -618.95 → «−618,95 ₸».
 *
 * Пробелы при ПЕЧАТИ обычные, а не неразрывные, и это не мелочь: строки слепка
 * написаны обычными, и с неразрывным «списать 1 000» не возвращало баланс к
 * исходной строке — два визуально одинаковых значения не совпадали.
 * При РАЗБОРЕ, наоборот, принимаем оба: кабинет разделяет разряды неразрывным. */
export const formatMoney = (value) => {
    const negative = value < 0;
    const fixed = Math.abs(value).toFixed(2).replace('.', ',');
    const [whole, cents] = fixed.split(',');
    const spaced = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    const body = cents === '00' ? spaced : `${spaced},${cents}`;
    return `${negative ? MINUS : ''}${body} ₸`;
};

/**
 * Применить правку к слепку. Возвращает НОВЫЙ слепок — исходный не трогаем:
 * на нём стоит React, и мутация не перерисовала бы экран.
 *
 * Неизвестное действие возвращает слепок как есть: кнопка «Открыть в WhatsApp»
 * данные не меняет, но след в ленте оставить обязана.
 */
export const applyEdit = (source, what, args = {}) => {
    const id = args.id;
    const bump = (person, delta) => ({
        ...person,
        balance: formatMoney(parseMoney(person.balance) + delta),
    });

    switch (what) {
    case 'balance_add':
    case 'balance_sub': {
        const delta = what === 'balance_add' ? MONEY_STEP : -MONEY_STEP;
        return {
            ...source,
            contractor: source.contractor.id === id
                ? bump(source.contractor, delta) : source.contractor,
            contractors: source.contractors.map(
                (p) => (p.id === id ? bump(p, delta) : p),
            ),
        };
    }
    case 'limit': {
        const next = (person) => ({
            ...person,
            limit: formatMoney(parseMoney(person.limit) - MONEY_STEP),
        });
        return {
            ...source,
            contractor: source.contractor.id === id ? next(source.contractor) : source.contractor,
            contractors: source.contractors.map((p) => (p.id === id ? next(p) : p)),
        };
    }
    case 'switch_park':
        return { ...source, park: { ...source.park, name: args.to || source.park.name } };
    default:
        return source;
    }
};
