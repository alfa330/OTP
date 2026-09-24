/* Правила карточки города — отдельно от JSX, чтобы их можно было проверить
 * без браузера (tests/wiki_city_rules.test.mjs).
 *
 * Карточка складывается из двух источников: слепка тарифов Яндекс Go
 * (city.yandex_data — сервер кладёт его сверкой, wiki/yandex_tariffs.py) и
 * ручных полей редактора. Склейка живёт здесь одной функцией, а не в каждом
 * месте показа: карточка, список, карта и редактор обязаны видеть одни и те
 * же тарифы, иначе скрытый в редакторе тариф всплыл бы в диапазоне комиссии.
 */

/** Тарифы города для показа: Яндекс + ручные поля + свои тарифы.
 *
 *  Ручные поля Яндекс-тарифа привязаны к его КОДУ («econom»), а не к названию:
 *  у Яндекса «Комфорт» внутри называется business, а «Бизнес» — vip. */
export const cityTariffs = (city, { includeHidden = false } = {}) => {
    const meta = city?.tariff_meta || {};
    const yandex = (city?.yandex_data?.tariffs || []).map((tariff) => {
        const own = meta[tariff.class] || {};
        return {
            key: `y:${tariff.class}`,
            code: tariff.class,
            source: 'yandex',
            name: tariff.name,
            from: tariff.from || null,
            commission: own.commission ?? null,
            requirement: own.requirement || '',
            hidden: !!own.hidden,
            detail: tariff,
        };
    });
    const extra = (city?.extra_tariffs || []).map((tariff, index) => ({
        key: `m:${index}`,
        code: null,
        source: 'manual',
        name: tariff.name,
        from: tariff.price || null,
        commission: tariff.commission ?? null,
        requirement: tariff.requirement || '',
        hidden: false,
        detail: null,
    }));
    const all = [...yandex, ...extra];
    return includeHidden ? all : all.filter((tariff) => !tariff.hidden);
};

const PERCENT = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

/** 12 → «12%», 4.5 → «4,5%». */
export const formatPercent = (value) => (
    value === null || value === undefined || value === '' || Number.isNaN(Number(value))
        ? '' : `${PERCENT.format(Number(value))}%`
);

/** Диапазон комиссии по тарифам: «12–19%», «12%» или '' — если не заполнено. */
export const commissionRange = (tariffs) => {
    const values = (tariffs || [])
        .map((tariff) => tariff.commission)
        .filter((value) => value !== null && value !== undefined && !Number.isNaN(Number(value)))
        .map(Number);
    if (!values.length) return '';
    const min = Math.min(...values);
    const max = Math.max(...values);
    return min === max
        ? formatPercent(min)
        : `${PERCENT.format(min)}–${formatPercent(max)}`;
};

/** Первый период тарифа — тот, что показан в карточке. */
export const mainInterval = (tariff) => tariff?.detail?.intervals?.[0] || null;

/** Платные опции заказа по городу для чипов «Дополнительные услуги Яндекса».
 *
 *  Только платные и только у показанных тарифов: бесплатные опции
 *  («Общаюсь текстом», «Молчу, но слышу вас») есть у каждого тарифа, и четыре
 *  одинаковых чипа в каждом городе — это шум, а не ответ. Они видны в
 *  раскрытом тарифе. Сами тарифы («Доставка», «Грузовой») в чипы не идут —
 *  они и так стоят строками выше, дубль на одном экране — тоже шум. */
export const yandexExtras = (tariffs) => {
    const seen = new Set();
    const result = [];
    (tariffs || []).forEach((tariff) => {
        (mainInterval(tariff)?.options || []).forEach((option) => {
            if (option.free || seen.has(option.label)) return;
            seen.add(option.label);
            result.push(option.label);
        });
    });
    return result;
};

/** Есть ли у города хоть один тариф с заказом по телефону. */
export const hasPhoneOrders = (tariffs) => (tariffs || []).some((tariff) => tariff.detail?.by_phone);

/* ── Зоны ───────────────────────────────────────────────────────────────────
 * Зона города — город офиса, который его обслуживает («Экибастуз — зона
 * Астаны»). Зону задаёт ТОЛЬКО выбор обслуживающего офиса: город, в который
 * отправляют водителей других городов, — центр зоны и сам в неё входит.
 *
 * «Город со своим офисом — сам себе зона» пробовали: офисы есть в двадцати
 * городах из двадцати четырёх, и схема превращалась в радугу из двадцати
 * цветов, где цвет уже ничего не различал. Город с офисом без зоны — крупная
 * серая точка: офис у него есть, а зону ещё не назначили. */
export const zoneHubs = (cities) => new Set(
    (cities || []).map((city) => city.serving_office_city).filter(Boolean),
);

export const cityZone = (city, hubs = null) => (
    city?.serving_office_city
    || (hubs && hubs.has(city?.name) ? city.name : '')
    || ''
);

/* Цвета зон. Четыре первых — как на макете постановки (Алматы зелёная, Астана
   фиолетовая, Шымкент красная, Актобе оранжевая); остальные зоны получают
   цвета дальше по списку в алфавитном порядке, чтобы цвет не прыгал между
   заходами. Цвет — только у точки и маркера легенды, текст остаётся
   нейтральным. */
export const ZONE_COLORS = ['#1f9d6b', '#7475d8', '#e5553a', '#f2a33a',
                            '#2f9bd6', '#d4589a', '#14a3a3', '#9b6bd6'];
const PREFERRED_ZONE = { 'Алматы': 0, 'Астана': 1, 'Шымкент': 2, 'Актобе': 3 };
export const NO_ZONE_COLOR = '#cbd5e1';

/** Родительный падеж названия города для подписи «Зона Астаны».
 *
 *  Только правила, которые верны для городов страны: несклоняемые на гласную
 *  (Алматы, Актобе, Атырау, Кокшетау) остаются как есть; «-а» → «-ы», после
 *  к/г/х/ж/ш/ч/щ → «-и»; «-ый» → «-ого» (Рудный); «-й» → «-я» (Семей,
 *  Костанай); на согласную — «+а» (Шымкент, Павлодар). Составное название
 *  склоняется по последнему слову («Усть-Каменогорска»). */
export const cityGenitive = (name) => {
    const text = String(name || '').trim();
    if (!text) return '';
    const lower = text.toLowerCase();
    if (/ый$/.test(lower)) return `${text.slice(0, -2)}ого`;
    if (/[кгхжшчщ]а$/.test(lower)) return `${text.slice(0, -1)}и`;
    if (/а$/.test(lower)) return `${text.slice(0, -1)}ы`;
    if (/й$/.test(lower)) return `${text.slice(0, -1)}я`;
    if (/[бвгджзклмнпрстфхцчшщ]$/.test(lower)) return `${text}а`;
    return text;
};

/** { зона: цвет } для набора городов. */
export const zonePalette = (cities) => {
    const zones = [...zoneHubs(cities)];
    const collator = new Intl.Collator('ru');
    const palette = {};
    const taken = new Set();
    zones.forEach((zone) => {
        if (zone in PREFERRED_ZONE) {
            palette[zone] = ZONE_COLORS[PREFERRED_ZONE[zone]];
            taken.add(PREFERRED_ZONE[zone]);
        }
    });
    const free = ZONE_COLORS.map((_, index) => index).filter((index) => !taken.has(index));
    zones.filter((zone) => !(zone in palette)).sort(collator.compare).forEach((zone, index) => {
        palette[zone] = ZONE_COLORS[free[index % free.length] ?? index % ZONE_COLORS.length];
    });
    return palette;
};

/* ── Услуги ─────────────────────────────────────────────────────────────────
 * Иконка услуги — по словам названия. Услуги пишут руками, поэтому правило
 * словесное; не угадали — нейтральная иконка, а не пустое место. */
const SERVICE_RULES = [
    ['bike', /велосип|самокат|электровел/i],
    // Границу слова пишем явно: \b в JS знает только латиницу, и «авто\b»
    // не сработал бы ни на одном русском названии.
    ['car', /аренд|автомоб|машин|авто(?![а-яё])/i],
    ['wash', /мойк|химчист/i],
    ['repair', /(^|[^а-яё])сто([^а-яё]|$)|ремонт|шином|сервис|техобслуж/i],
    ['insurance', /страх|осаго|каско/i],
    ['brand', /брендир|наклейк|оклейк/i],
    ['fuel', /топлив|заправ|азс|бензин|газ(?![а-яё])/i],
    ['money', /выплат|вывод|кешбэк|бонус|рассрочк/i],
    ['docs', /документ|лицензи|путев|медосмотр|осмотр/i],
];

export const serviceIconKey = (title) => {
    const text = String(title || '');
    const hit = SERVICE_RULES.find(([, rule]) => rule.test(text));
    return hit ? hit[0] : 'other';
};

/* Иконка опции Яндекса — по названию опции. */
const OPTION_RULES = [
    ['child', /детск/i],
    ['pet', /животн/i],
    ['airport', /аэропорт/i],
    ['door', /двер/i],
    ['bag', /термосумк|сумк|багаж|груз/i],
    ['ski', /лыж|сноуборд/i],
    ['stop', /промежуточн|точк/i],
    ['wheelchair', /инвалид|кресл/i],
];

export const optionIconKey = (label) => {
    const text = String(label || '');
    const hit = OPTION_RULES.find(([, rule]) => rule.test(text));
    return hit ? hit[0] : 'other';
};

/* ── Даты ───────────────────────────────────────────────────────────────────
 * «Обновлено» — когда карточка последний раз МЕНЯЛАСЬ: правка руками или
 * изменение тарифов у Яндекса. Ночная сверка без изменений дату не двигает
 * (сервер пишет yandex_changed_at только при новом отпечатке). */
export const cityUpdatedAt = (city) => {
    const stamps = [city?.updated_at, city?.yandex_changed_at].filter(Boolean).sort();
    return stamps.length ? stamps[stamps.length - 1] : null;
};

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
                'августа', 'сентября', 'октября', 'ноября', 'декабря'];

/** «2026-09-04T…» → «4 сентября» (год — только если не текущий). */
export const formatDate = (iso, now = new Date()) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    if (!match) return '';
    const [, year, month, day] = match;
    const base = `${Number(day)} ${MONTHS[Number(month) - 1]}`;
    return Number(year) === now.getFullYear() ? base : `${base} ${year}`;
};

/** «…T05:20:14» → «05:20». */
export const formatTime = (iso) => {
    const match = /T(\d{2}):(\d{2})/.exec(String(iso || ''));
    return match ? `${match[1]}:${match[2]}` : '';
};

/** Хост источника для подписи ссылки: «taxi.yandex.ru». */
export const sourceHost = (url) => {
    try {
        return new URL(url).hostname;
    } catch {
        return '';
    }
};

/* ── Поиск ──────────────────────────────────────────────────────────────── */
const fold = (value) => String(value || '').toLowerCase().replace(/ё/g, 'е').trim();

export const cityMatches = (city, query) => {
    const needle = fold(query);
    return !needle || fold(city?.name).includes(needle);
};

/* ── Офисы города ───────────────────────────────────────────────────────────
 * «Куда направлять водителя»: обслуживающий офис, если его выбрали; иначе
 * собственные офисы города (парковые, живые, не «офиса нет»). Список
 * офисов приходит той же выборкой, что во вкладке «Офисы», — второго
 * источника адресов у раздела нет. */
export const cityOffices = (city, offices) => {
    const list = offices || [];
    if (city?.serving_office_id) {
        const office = list.find((item) => item.id === city.serving_office_id);
        return office ? [office] : [];
    }
    const name = fold(city?.name);
    return list.filter((office) => (
        fold(office.city) === name && !office.no_office
        && office.kind !== 'partner' && office.status === 'active'
    ));
};
