/*
 * Правила раздела «Посылки», вынесенные из JSX.
 *
 * Здесь всё, что можно проверить без браузера: подписи и цвет статусов, разбор
 * ссылки на аккаунт водителя, правило «спрашивать офис или подставить сам»,
 * сборка строки истории. Тесты — tests/parcel_meta.test.mjs.
 *
 * Про цвет. Строка реестра окрашивается ПО СТАТУСУ целиком — тем же приёмом и
 * теми же весами, что строки офисов в вики (officeDayStatus.js): состояние
 * записи должно читаться, не доходя глазами до колонки «Статус». Приём взят
 * оттуда намеренно, а не изобретён заново: два раздела портала, красящих строку
 * по-разному, читались бы как две разные программы.
 *
 * Оттенков ЧЕТЫРЕ на три статуса: «В офисе» разделено на «лежит» и
 * «залежалась» (от STALE_AFTER_DAYS дней). Раздел называется
 * «невостребованные», и вопрос «что уже пора разбирать» — главный в нём.
 */

export const PARCEL_STATUSES = ['in_office', 'given_to_recipient', 'given_to_sender'];

/* Подписи статуса. Их ТРИ вида, и это не дубли:
 *   label  — в реестре и в легенде: полная фраза, читается сама по себе;
 *   action — на кнопке смены статуса: ГЛАГОЛ, потому что человек нажимает её,
 *            чтобы что-то сделать, а не чтобы назвать состояние;
 *   hint   — под кнопкой: кто именно забрал посылку.
 *
 * Почему «Вернули отправителю», а не «Передали отправителю» из ТЗ: два дательных
 * падежа рядом («получателю» / «отправителю») различаются одним корнем, и в
 * сегментном контроле «Получателю | Отправителю» человек читал их как выбор
 * адресата, а не как итог. Отправитель — тот, кто посылку и оставил, поэтому
 * верный глагол здесь «вернули»; он же снимает вопрос «а зачем отправителю
 * передавать то, что он сам принёс».
 */
export const STATUS_META = {
    in_office: {
        label: 'В офисе',
        action: 'В офисе',
        hint: 'Лежит в офисе, за ней ещё не пришли',
    },
    given_to_recipient: {
        label: 'Передали получателю',
        action: 'Отдали получателю',
        hint: 'Забрал тот, кому посылка предназначалась',
    },
    given_to_sender: {
        label: 'Вернули отправителю',
        action: 'Вернули отправителю',
        hint: 'Забрал тот, кто её оставил',
    },
};

export const PARCEL_KINDS = ['parcel', 'document', 'other'];

export const KIND_META = {
    parcel: { label: 'Посылка' },
    document: { label: 'Документ' },
    other: { label: 'Другое' },
};

/* Полоса-легенда, она же фильтр — «Все / В офисе / …» в порядке ТЗ.
 *
 * Кружок берётся из той же палитры, что заливка строки (TONE_PILL), а не
 * назначается рядом второй раз: легенда учит читать цвет в таблице, и разойдись
 * эти два места на полтона — она бы этому и мешала. Ровно так же собрана
 * легенда офисов в вики. */
export const STATE_FILTERS = [
    { key: 'all', label: 'Все', status: '', tone: null },
    { key: 'in_office', label: 'В офисе', status: 'in_office', tone: 'waiting' },
    { key: 'given_to_recipient', label: 'Передали получателю', status: 'given_to_recipient', tone: 'recipient' },
    { key: 'given_to_sender', label: 'Вернули отправителю', status: 'given_to_sender', tone: 'sender' },
];

// Сколько дней посылка считается «просто лежит», а не «залежалась». Месяц —
// срок, после которого водитель почти наверняка уже не придёт сам, и офису
// пора звонить. Значение одно на весь раздел: подсветка строки и подпись «лежит
// N дней» обязаны срабатывать одновременно.
export const STALE_AFTER_DAYS = 30;

/* ── Выгрузка ────────────────────────────────────────────────────────────────
 *
 * Потолок периода — тот же, что на сервере (`EXPORT_MAX_DAYS` в
 * parcels/report.py), их сверяет тест. Здесь он нужен, чтобы «Подтвердить»
 * гасло ДО запроса, а не после ожидания: гасить кнопку — удобство, а границей
 * служит сервер.
 *
 * Совпадение с STALE_AFTER_DAYS случайно: один про «сколько лежит», второй про
 * «сколько дней за раз выгружаем», и меняться они будут порознь. */
export const EXPORT_MAX_DAYS = 30;
// Сами функции периода живут ниже, рядом с `dateParts` — разбор даты у раздела
// один, и второй копии ему не нужно.

export const statusMeta = (code) => STATUS_META[code]
    || { label: code || '—', action: code || '—', hint: '' };
export const kindMeta = (code) => KIND_META[code] || { label: code || '—' };

export const isClosed = (status) => status === 'given_to_recipient' || status === 'given_to_sender';

/* ── Оттенок строки ──────────────────────────────────────────────────────────
 *
 * Один вход на всё: строку таблицы, карточку на телефоне, бейдж и легенду.
 * Правило считается ЗДЕСЬ, а не в разметке, потому что оно живёт в четырёх
 * местах — ровно та ошибка, из-за которой в вики у офиса без графика бейджа не
 * было вовсе: условие переписывали в каждом месте отдельно.
 */
export const ROW_TONES = ['waiting', 'stale', 'recipient', 'sender'];

export const rowTone = (parcel, today = todayISO()) => {
    if (parcel?.status === 'given_to_recipient') return 'recipient';
    if (parcel?.status === 'given_to_sender') return 'sender';
    // Остаётся «в офисе» — и незнакомый статус тоже: непрочитанное состояние
    // честнее показать ожидающим, чем закрытым.
    return isStale(parcel, today) ? 'stale' : 'waiting';
};

/* Заливка строки. Веса — как у офисов в вики: заметно, что строка не белая, но
 * таблица из тридцати «в офисе» не превращается в жёлтое поле. Янтарь у
 * ожидающих, а не у закрытых: ждёт — это то, с чем надо что-то делать. */
export const TONE_ROW = {
    waiting: 'bg-amber-50',
    stale: 'bg-amber-100/70',
    recipient: 'bg-emerald-50/70',
    sender: 'bg-slate-100/80',
};

/* Цвет ТЕКСТА в залитой строке, а не только фона: серый slate-500 на янтаре и
 * на зелени читается как выцветший. Тонируем вслед за заливкой. */
export const TONE_TEXT = {
    waiting: { main: 'text-slate-900', body: 'text-slate-700', meta: 'text-amber-700' },
    stale: { main: 'text-slate-900', body: 'text-amber-900', meta: 'text-amber-700' },
    recipient: { main: 'text-emerald-900', body: 'text-emerald-800', meta: 'text-emerald-700' },
    sender: { main: 'text-slate-800', body: 'text-slate-600', meta: 'text-slate-500' },
};

/* Бейдж в залитой строке. IosBadge здесь не годится по той же причине, что в
 * вики: его тона светлее самой строки, и бейдж в заливке пропадает. */
export const TONE_PILL = {
    waiting: { fill: 'bg-amber-200/80 text-amber-900', dot: 'bg-amber-500' },
    stale: { fill: 'bg-amber-300/80 text-amber-950', dot: 'bg-amber-600' },
    recipient: { fill: 'bg-emerald-200 text-emerald-900', dot: 'bg-emerald-600' },
    sender: { fill: 'bg-slate-300/70 text-slate-800', dot: 'bg-slate-500' },
};

/* Кант слева у карточки на телефоне: там заливка всей карточки читается как
 * тревога, а не как состояние (то же решение, что у карточек офисов). */
export const TONE_EDGE = {
    waiting: 'before:bg-amber-400',
    stale: 'before:bg-amber-500',
    recipient: 'before:bg-emerald-400',
    sender: 'before:bg-slate-400',
};

export const toneRow = (tone) => TONE_ROW[tone] || TONE_ROW.waiting;
export const toneText = (tone) => TONE_TEXT[tone] || TONE_TEXT.waiting;
export const tonePill = (tone) => TONE_PILL[tone] || TONE_PILL.waiting;
export const toneEdge = (tone) => TONE_EDGE[tone] || TONE_EDGE.waiting;


/* ── Ссылка на аккаунт водителя ───────────────────────────────────────────────
 *
 * Близнец серверного `parcels/drivers.py::extract_account_id` — набор случаев у
 * обоих одинаковый. Здесь он нужен, чтобы форма гасила кнопку «Найти» до того,
 * как человек отправит заведомо непонятную строку.
 *
 * Два живых вида ссылки:
 *   https://fleet.yandex.kz/contractors?park_id=<парк>&contractor_id=<водитель>&candidate_id=<…>
 *   https://fleet.yandex.kz/contractors/<водитель>/details?park_id=<парк>
 *
 * «Первое 32-значное значение в строке» брать НЕЛЬЗЯ: в первой ссылке таких
 * значений два, и первым идёт ПАРК. Поэтому только именованный параметр или
 * сегмент пути после известного слова.
 */
const ACCOUNT_ID_RE = /^[0-9a-f]{32}$/i;

const DRIVER_QUERY_KEYS = [
    'contractor_id', 'driver_id', 'driver_profile_id', 'account_id',
    'courier_id', 'profile_id',
];

const DRIVER_PATH_KEYS = [
    'contractors', 'contractor', 'drivers', 'driver',
    'driver-accounts', 'couriers', 'courier',
];

export const extractAccountId = (value) => {
    const raw = String(value ?? '').trim();
    if (!raw) return null;
    if (ACCOUNT_ID_RE.test(raw)) return raw.toLowerCase();

    let parsed;
    try {
        parsed = new URL(raw.includes('://') ? raw : `https://${raw.replace(/^\/+/, '')}`);
    } catch {
        return null;
    }

    for (const key of DRIVER_QUERY_KEYS) {
        const found = parsed.searchParams.get(key);
        if (found && ACCOUNT_ID_RE.test(found.trim())) return found.trim().toLowerCase();
    }

    const segments = parsed.pathname.split('/').filter(Boolean);
    for (let index = 0; index < segments.length - 1; index += 1) {
        if (!DRIVER_PATH_KEYS.includes(segments[index].toLowerCase())) continue;
        const token = segments[index + 1].trim();
        if (ACCOUNT_ID_RE.test(token)) return token.toLowerCase();
    }
    return null;
};

/* ── Ссылки ──────────────────────────────────────────────────────────────────
 *
 * Две ссылки уходят наружу: на аккаунт водителя во Флите и на заказ.
 *
 * Аккаунт мы СОБИРАЕМ сами из id водителя и id парка — это тот самый вид
 * ссылки, которым владелец пользуется сам:
 *   https://fleet.yandex.kz/contractors/<водитель>/details?park_id=<парк>
 * Без парка Флит карточку не открывает, поэтому у записи без `driver_park_id`
 * ссылки нет вовсе (у заведённых до 25.08.2026 парк подтягивается миграцией из
 * снимка CRM, но у тех, где снимок не приехал, его нет и взять негде).
 *
 * Ссылку на заказ мы НЕ собираем: её вставляет сотрудник, и показывать её можно
 * только проверив схему. `javascript:` в href — это выполнение кода у того, кто
 * по ссылке щёлкнет; сервер такую не примет (parcels/routes.py::_clean_link), но
 * в базе могут лежать записи, заведённые до этой проверки, и рисует их всё
 * равно фронт.
 */
const FLEET_BASE = 'https://fleet.yandex.kz';

const HEX32 = /^[0-9a-f]{32}$/i;

export const driverAccountUrl = (parcel) => {
    const driver = String(parcel?.driver_account_id || '').trim();
    const park = String(parcel?.driver_park_id || '').trim();
    if (!HEX32.test(driver) || !HEX32.test(park)) return null;
    return `${FLEET_BASE}/contractors/${driver.toLowerCase()}/details`
        + `?park_id=${park.toLowerCase()}`;
};

// Близнец серверного `_clean_link`: наружу отдаём ссылку, только если она
// http(s) и с хостом. Всё остальное показываем текстом, а не ссылкой.
export const safeLink = (value) => {
    const raw = String(value ?? '').trim();
    if (!raw) return null;
    const scheme = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(raw);
    if (scheme && !['http', 'https'].includes(scheme[1].toLowerCase())) return null;
    try {
        const parsed = new URL(raw.includes('://') ? raw : `https://${raw.replace(/^\/+/, '')}`);
        if (!['http:', 'https:'].includes(parsed.protocol)) return null;
        if (!parsed.hostname || !parsed.hostname.includes('.')) return null;
        return parsed.href;
    } catch {
        return null;
    }
};

/* Как показать ссылку человеку. Полный адрес заказа — это 80 символов служебного
   текста, поэтому в подписи оставляем хост и последний осмысленный кусок пути
   («fleet.yandex.kz · 401220d7…»): по нему видно, куда ведёт, и он не разрывает
   строку карточки. */
export const linkLabel = (value) => {
    const href = safeLink(value);
    if (!href) return String(value ?? '').trim() || null;
    try {
        const parsed = new URL(href);
        const tail = parsed.pathname.split('/').filter(Boolean).pop() || '';
        const short = tail.length > 12 ? `${tail.slice(0, 8)}…` : tail;
        return short ? `${parsed.hostname} · ${short}` : parsed.hostname;
    } catch {
        return href;
    }
};

/* ── Город → офис ────────────────────────────────────────────────────────────
 *
 * «Офис выбирается, только если в городе несколько офисов, в ином случае
 * заполнено авто» — просьба владельца. Правило считается по справочнику, а не
 * по списку городов из ТЗ: справочник живой, и завтра второй офис появится там,
 * где сегодня один.
 */
export const officesOfCity = (offices, city) => {
    const needle = String(city || '').trim().toLowerCase();
    if (!needle) return [];
    return (offices || []).filter((office) => String(office.city || '').trim().toLowerCase() === needle);
};

export const officeChoiceFor = (offices, city) => {
    const list = officesOfCity(offices, city);
    return {
        options: list,
        // Спрашиваем офис только когда есть из чего выбирать.
        asks: list.length > 1,
        autoOfficeId: list.length === 1 ? list[0].id : null,
    };
};

/* ── Даты ────────────────────────────────────────────────────────────────── */

// Сегодня по Алматы. У сотрудника в браузере может стоять любая зона, а дата
// приёма — рабочий день офиса в Казахстане; без приведения человек в другом
// поясе получал бы «дата в будущем» на сегодняшнем дне.
export const todayISO = () => {
    const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Almaty', year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(new Date());
    const get = (type) => parts.find((part) => part.type === type)?.value || '';
    return `${get('year')}-${get('month')}-${get('day')}`;
};

// Разбор ISO-даты с проверкой, а не только по маске. `Date.UTC(2026, 12, 40)`
// молча превращается в февраль следующего года — и «13-й месяц» дал бы не
// прочерк, а какое-то число дней. Сверяем компоненты с тем, что получилось.
const dateParts = (iso) => {
    const text = String(iso || '').slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
    const year = Number(text.slice(0, 4));
    const month = Number(text.slice(5, 7));
    const day = Number(text.slice(8, 10));
    const stamp = Date.UTC(year, month - 1, day);
    const back = new Date(stamp);
    if (back.getUTCFullYear() !== year || back.getUTCMonth() !== month - 1
        || back.getUTCDate() !== day) return null;
    return { year, month, day, stamp };
};

const dayNumber = (iso) => dateParts(iso)?.stamp ?? null;

export const daysInOffice = (parcel, today = todayISO()) => {
    if (!parcel || isClosed(parcel.status)) return null;
    const from = dayNumber(parcel.received_on);
    const to = dayNumber(today);
    if (from === null || to === null) return null;
    return Math.max(0, Math.round((to - from) / 86400000));
};

export const isStale = (parcel, today = todayISO()) => {
    const days = daysInOffice(parcel, today);
    return days !== null && days >= STALE_AFTER_DAYS;
};

/* ── Период выгрузки ─────────────────────────────────────────────────────── */

/* Сутки периода, обе границы включительно: «с 1 по 1» — это одни сутки, а не
   ноль. Сервер считает так же (`_export_period` в parcels/routes.py). Ноль
   означает «период не выбран» — на нём «Подтвердить» гаснет. */
export const rangeDays = (from, to) => {
    const a = dateParts(from);
    const b = dateParts(to);
    if (!a || !b) return 0;
    return Math.round(Math.abs(b.stamp - a.stamp) / 86400000) + 1;
};

/* Начало окна максимальной длины, заканчивающегося сегодня. Считаем в UTC от
   разобранной даты, а не `new Date()` минус дни: перевод часов и часовой пояс
   браузера иначе дают сдвиг на сутки. */
export const shiftDaysBack = (iso, days) => {
    const parts = dateParts(iso);
    if (!parts) return iso;
    const moved = new Date(parts.stamp - days * 86400000);
    const p = (n) => String(n).padStart(2, '0');
    return `${moved.getUTCFullYear()}-${p(moved.getUTCMonth() + 1)}-${p(moved.getUTCDate())}`;
};

/* Имя файла выгрузки. Собирается ЗДЕСЬ, а не читается из Content-Disposition:
   заголовок до фронта не доходит — в Access-Control-Expose-Headers его нет.
   Двойник — `report_filename` в parcels/report.py, их сверяет тест. */
export const exportFileName = (from, to) => {
    const ru = (iso) => {
        const parts = dateParts(iso);
        if (!parts) return '';
        const p = (n) => String(n).padStart(2, '0');
        return `${p(parts.day)}.${p(parts.month)}.${parts.year}`;
    };
    const left = ru(from);
    const right = ru(to);
    // Три ветки — дословно как в `report_filename`: расхождение здесь никто бы
    // не заметил, файл просто лёг бы в загрузки под другим именем.
    if (!left && !right) return 'Посылки.xlsx';
    if (left === right) return `Посылки ${left}.xlsx`;
    return `Посылки ${left} — ${right}.xlsx`;
};

const RU_MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн',
    'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

export const fmtDate = (iso) => {
    const parts = dateParts(iso);
    if (!parts) return '—';
    return `${parts.day} ${RU_MONTHS_SHORT[parts.month - 1]} ${parts.year}`;
};

export const fmtDateTime = (iso) => {
    if (!iso) return '—';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '—';
    return parsed.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
};

// Дни склоняются: «1 день», «2 дня», «5 дней». Готовой функции в проекте нет,
// а «5 дня» в реестре читается как опечатка.
export const pluralDays = (count) => {
    const value = Math.abs(Number(count) || 0);
    const tail = value % 100;
    if (tail >= 11 && tail <= 14) return `${value} дней`;
    const last = value % 10;
    if (last === 1) return `${value} день`;
    if (last >= 2 && last <= 4) return `${value} дня`;
    return `${value} дней`;
};

/* ── Телефон ─────────────────────────────────────────────────────────────── */

// CRM отдаёт «+77719736925». Показываем группами, чтобы номер можно было
// прочитать вслух водителю, не сбиваясь.
export const fmtPhone = (value) => {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length !== 11) return String(value || '').trim() || null;
    return `+${digits[0]} ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7, 9)} ${digits.slice(9)}`;
};

/* ── История ─────────────────────────────────────────────────────────────── */

export const describeEvent = (event) => {
    const payload = event?.payload || {};
    if (event?.kind === 'created') return 'Посылка добавлена в реестр';
    if (event?.kind === 'comment') return 'Добавлен комментарий';
    if (event?.kind === 'status') {
        // Одинаковые «было» и «стало» сервер больше не пишет, но такие строки
        // могли остаться в истории — печатать «В офисе → В офисе» нельзя.
        if (payload.from === payload.to) return 'Статус подтверждён';
        return `Статус изменён: ${statusMeta(payload.from).label} → ${statusMeta(payload.to).label}`;
    }
    if (event?.kind === 'edited') {
        const changes = Array.isArray(payload.changes) ? payload.changes : [];
        if (!changes.length) return 'Карточка изменена';
        return `Изменено: ${changes.map((change) => change.label).join(', ')}`;
    }
    if (event?.kind === 'driver_synced') return 'Данные водителя обновлены из CRM';
    // Фотографии — тоже событие карточки: снимок удаляется вместе с файлом, и
    // «кто его снял» через месяц спрашивают так же, как «кто передал посылку».
    if (event?.kind === 'photo_added') return 'Добавлена фотография';
    if (event?.kind === 'photo_removed') return 'Фотография удалена';
    return 'Изменение карточки';
};
