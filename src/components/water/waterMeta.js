/*
 * Справочники и чистые функции раздела «Учёт воды». Без React — их гоняет
 * node --test (tests/water_meta.test.mjs).
 *
 * Двойники серверных словарей (water/rules.py, water/report.py) — их сверяет
 * pytest (tests/test_water_frontend.py): разойдись подписи, человек увидел бы в
 * файле не то слово, что на экране.
 */

// Коды тарифов — в наименованиях Флита, как их отдаёт CRM. Порядок — порядок
// показа в настройках. Неизвестный код показывается как есть.
export const TARIFF_LABELS = {
    econom: 'Эконом',
    comfort: 'Комфорт',
    comfort_plus: 'Комфорт+',
    business: 'Business',
    ultimate: 'Premier (Ultima)',
    maybach: 'Élite',
    minivan: 'Минивэн',
    express: 'Доставка',
    courier: 'Курьер',
    cargo: 'Грузовой',
    intercity: 'Межгород',
    personal_driver: 'Личный водитель',
};

export const KNOWN_TARIFFS = Object.keys(TARIFF_LABELS);

export const tariffLabel = (code) => TARIFF_LABELS[code] || code || '';

export const KIND_LABELS = {
    welcome: 'Приветственная',
    activity: 'За активность',
};

export const kindLabel = (code) => KIND_LABELS[code] || code || '';

/* Статус остатка. Достаточный не красится — цвет только там, где есть что
   делать: янтарь «скоро кончится», красный «пора закупать». */
export const STOCK_STATUS = {
    enough: { label: 'Достаточно', pill: 'bg-slate-100 text-slate-600', text: 'text-slate-900' },
    low: { label: 'Низкий остаток', pill: 'bg-amber-50 text-amber-700 ring-1 ring-amber-100', text: 'text-amber-700' },
    buy: { label: 'Требуется закупка', pill: 'bg-rose-50 text-rose-600 ring-1 ring-rose-100', text: 'text-rose-600' },
};

export const stockStatus = (code) => STOCK_STATUS[code] || STOCK_STATUS.enough;

export const plural = (count, one, few, many) => {
    const value = Math.abs(Math.trunc(Number(count) || 0)) % 100;
    if (value >= 11 && value <= 14) return many;
    const last = value % 10;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

export const blocksWord = (count) => `${count} ${plural(count, 'блок', 'блока', 'блоков')}`;
export const tripsWord = (count) => `${count} ${plural(count, 'поездка', 'поездки', 'поездок')}`;
export const daysWord = (count) => `${count} ${plural(count, 'день', 'дня', 'дней')}`;
export const issuesWord = (count) => `${count} ${plural(count, 'выдача', 'выдачи', 'выдач')}`;

// Потолок периода выгрузки — тот же EXPORT_MAX_DAYS, что в water/report.py.
export const EXPORT_MAX_DAYS = 366;

const ruDate = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return match ? `${match[3]}.${match[2]}.${match[1]}` : '';
};

export const fmtDay = ruDate;

/* Имя файла — двойник report_filename: заголовок Content-Disposition до фронта
   не доходит (его нет в Access-Control-Expose-Headers). */
export const exportFileName = (from, to) => {
    const a = ruDate(from);
    const b = ruDate(to);
    if (!a || !b) return 'Выдачи воды.xlsx';
    return a === b ? `Выдачи воды ${a}.xlsx` : `Выдачи воды ${a} — ${b}.xlsx`;
};

/* Заголовок вердикта. */
export const verdictHeadline = (verdict) => {
    if (!verdict) return '';
    if (!verdict.allowed) return 'Воду выдать нельзя';
    return verdict.kind === 'welcome' ? 'Можно выдать приветственный блок' : 'Можно выдать воду';
};

/* Одна строка «почему можно» — чтобы человек за стойкой видел основание, а не
   только зелёную плашку. Причины отказа приходят с сервера готовыми фразами. */
export const verdictBasis = (verdict) => {
    if (!verdict?.allowed) return '';
    if (verdict.kind === 'welcome') {
        const base = 'Новый водитель — ещё ни одного выполненного заказа';
        return verdict.fk === 'passed' ? `${base} · ФК пройден` : base;
    }
    const trips = Number(verdict.trips) || 0;
    const where = verdict.trips_basis === 'since_last' ? 'с прошлой выдачи' : 'за последние 7 дней';
    return `${tripsWord(trips)} ${where}`;
};

/* Когда следующая выдача — ТЗ, раздел 4: «доступна ли следующая выдача». */
export const nextIssueLine = (verdict) => {
    if (!verdict?.next_date || !verdict.last_issue) return '';
    return `Следующая выдача за активность — с ${ruDate(verdict.next_date)}`;
};

/* Прогноз закупки строкой: days_to_buy приходит с сервера. У офиса, где
   закупка уже нужна, прогноза нет — это и так сказано плашкой в той же строке,
   второй раз то же самое было бы шумом. */
export const forecastLabel = (row) => {
    if (!row) return '—';
    if (row.status === 'buy') return '—';
    if (row.days_to_buy === null || row.days_to_buy === undefined) return '—';
    return `через ~${daysWord(row.days_to_buy)}`;
};

export const fmtAvg = (value) => {
    const number = Number(value) || 0;
    if (!number) return '0';
    return number >= 10 ? String(Math.round(number)) : number.toFixed(1).replace('.', ',');
};

/* Целое неотрицательное число из поля ввода: '' — «не задано». */
export const parseCount = (value) => {
    const text = String(value ?? '').trim();
    if (!text) return null;
    if (!isCountDraft(text)) return NaN;
    return Number(text);
};

/* Черновик поля количества (CountInput): пусто или только цифры, не длиннее
   COUNT_MAX_DIGITS. Буква, пробел, минус, запятая — не черновик: такой ввод
   поле отбрасывает. Тот же потолок, что у parseCount. */
export const COUNT_MAX_DIGITS = 6;

export const isCountDraft = (value) => {
    const text = String(value ?? '');
    return text.length <= COUNT_MAX_DIGITS && /^\d*$/.test(text);
};

/* Условия получения воды словами — вкладка «Условия» у тех, кто программу
   не настраивает (колл-центр, офисники). Числа и тарифы — из настроек, а не из
   кода: поменял руководитель порог — тут же поменялся и текст. */
export const conditionGroups = (settings) => {
    const s = settings || {};
    const tariffs = (s.tariffs || []).map(tariffLabel).join(', ') || 'не заданы';
    const trips = Number(s.min_trips) || 0;
    const days = Number(s.cooldown_days) || 0;
    return [
        {
            title: 'Приветственный блок',
            rows: [
                ['Кому', 'Новому водителю — ещё ни одного выполненного заказа'],
                ['Тариф машины', tariffs],
                ['ФК', 'Должен быть пройден'],
                ['Сколько', `${blocksWord(Number(s.welcome_blocks) || 1)}, один раз`],
            ],
        },
        {
            title: 'За активность',
            rows: [
                ['Поездки', trips
                    ? `${tripsWord(trips)} с прошлой выдачи, первый раз — за последние 7 дней`
                    : 'Не требуются'],
                ['Как часто', days ? `Не чаще раза в ${daysWord(days)}` : 'Без ограничения по дням'],
                ['Тариф машины', tariffs],
                ['Сколько', `${blocksWord(Number(s.activity_blocks) || 1)} за выдачу`],
            ],
        },
    ];
};

/* Где офис: «Алматы · ул. Байзакова 78А». Название офиса в справочнике вики
   («Офис для подключения тарифа «Бизнес»») есть и в Алматы, и в Астане, а
   за стойкой офис узнают по адресу (01.10.2026: «отображать адрес самого
   офиса, не только название»). */
export const officePlace = (office) => [office?.city, office?.address]
    .map((part) => String(part || '').trim())
    .filter(Boolean)
    .join(' · ');

/* Ключ «последний офис» в localStorage — удобство одного сотрудника. */
export const LAST_OFFICE_KEY = 'otp_water_last_office';

export const readLastOffice = () => {
    try {
        const value = Number(window.localStorage.getItem(LAST_OFFICE_KEY));
        return Number.isFinite(value) && value > 0 ? value : null;
    } catch {
        return null;
    }
};

export const rememberOffice = (id) => {
    try {
        if (id) window.localStorage.setItem(LAST_OFFICE_KEY, String(id));
    } catch {
        /* приватное окно — просто не запоминаем */
    }
};

/* Офис по умолчанию: запомненный (если он ещё в учёте) → единственный офис
   своего города. Офис ЧУЖОГО города сам не подставляется никогда: пока в учёте
   один алматинский офис, астанинский сотрудник иначе получил бы его выбранным и
   списывал бы воду с чужой полки. Единственный офис вообще подставляется только
   тому, у кого город не указан. Иначе человек выбирает сам. */
export const defaultOfficeId = (offices, city, remembered) => {
    const active = (offices || []).filter((office) => office.is_active !== false);
    if (remembered && active.some((office) => office.id === remembered)) return remembered;
    if (city) {
        const own = active.filter((office) => office.city === city);
        return own.length === 1 ? own[0].id : null;
    }
    return active.length === 1 ? active[0].id : null;
};
