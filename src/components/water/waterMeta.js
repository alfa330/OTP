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

/* Пределы чисел программы — двойник _SETTINGS_LIMITS и _MAX_BLOCKS в
   water/routes.py (их сверяет pytest). Степпер за них не шагает, а набранное
   вне их подсвечивается у самого поля: сервер такое всё равно не примет. */
export const MAX_BLOCKS = 100000;

export const SETTINGS_LIMITS = {
    min_trips: [0, 1000],
    cooldown_days: [0, 365],
    welcome_blocks: [1, 50],
    activity_blocks: [1, 50],
    low_threshold: [0, MAX_BLOCKS],
    buy_threshold: [0, MAX_BLOCKS],
};

export const THRESHOLD_ORDER_ISSUE = 'Не больше порога «Низкий остаток»';

/* Что исправить в черновике настроек: { поле: 'что сделать' }, пусто — можно
   сохранять. Текст стоит под подписью строки, поэтому короткий. Порядок
   порогов — то же правило, что у сервера: закупка не выше «низкого». */
export const settingsIssues = (draft) => {
    const issues = {};
    const values = {};
    Object.entries(SETTINGS_LIMITS).forEach(([name, [low, high]]) => {
        const value = parseCount(draft?.[name]);
        values[name] = value;
        if (value === null) issues[name] = 'Укажите число';
        else if (!Number.isInteger(value) || value < low || value > high) {
            issues[name] = `От ${low} до ${high.toLocaleString('ru-RU')}`;
        }
    });
    if (!issues.low_threshold && !issues.buy_threshold && values.buy_threshold > values.low_threshold) {
        issues.buy_threshold = THRESHOLD_ORDER_ISSUE;
    }
    if (!(draft?.tariffs || []).length) issues.tariffs = 'Отметьте хотя бы один тариф';
    return issues;
};

/* Какие из ошибок видны, пока человек набирает в поле focused. Стёртое поле
   молчит до ухода из него — человек стёр, чтобы набрать новое. Порядок порогов
   молчит, пока набирают любой из двух: на первой цифре «15» порог «низкий» ещё
   1, и закупка 10 вспыхивала бы красным. Остальное («От 1 до 50») видно сразу. */
export const visibleIssues = (issues, draft, focused) => {
    const shown = { ...issues };
    if (focused && parseCount(draft?.[focused]) === null) delete shown[focused];
    if ((focused === 'low_threshold' || focused === 'buy_threshold')
        && shown.buy_threshold === THRESHOLD_ORDER_ISSUE) {
        delete shown.buy_threshold;
    }
    return shown;
};

/* Шаг степпера: от пустого или негодного значения — нижний предел, дальше —
   ±1 в пределах [min, max]. */
export const stepCount = (value, delta, [min, max]) => {
    const current = parseCount(value);
    if (!Number.isInteger(current)) return min;
    return Math.min(max, Math.max(min, current + delta));
};

const sameSet = (a, b) => {
    const left = [...new Set((a || []).map(String))].sort();
    const right = [...new Set((b || []).map(String))].sort();
    return left.length === right.length && left.every((item, index) => item === right[index]);
};

/* Есть ли в черновике правка: сравниваются значения, а не строки. Набранное
   «10» поверх 10 — не правка, и тариф, снятый и отмеченный снова, — тоже. */
export const settingsDirty = (draft, loaded) => {
    if (!draft || !loaded) return false;
    return Object.keys(SETTINGS_LIMITS).some((name) => parseCount(draft[name]) !== parseCount(loaded[name]))
        || !sameSet(draft.tariffs, loaded.tariffs)
        || !sameSet(draft.notify_user_ids, loaded.notify_user_ids);
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
   списывал бы воду с чужой полки. Запомненный — тоже: раз выбранный не тот офис
   подставлялся сам неделю, и шесть выдач легли на чужую полку (23.10.2026).
   Тогда не подставляется ничего, даже свой: чужой могли выбрать и намеренно,
   подменяя коллегу, — пусть человек решит сам. Своего города в учёте нет —
   запомненный остаётся: сверять не с чем. Единственный офис вообще
   подставляется только тому, у кого город не указан. Иначе человек выбирает сам.

   granted — список уже сужен доступом по офисам (issueOffices): это офисы,
   которые руководитель открыл именно этому человеку, и единственный из них
   подставляется сам, какого бы города он ни был — открыли астанинцу офис
   Алматы на время отпуска коллеги, значит, там он сейчас и выдаёт. */
export const defaultOfficeId = (offices, city, remembered, granted = false) => {
    const active = (offices || []).filter((office) => office.is_active !== false);
    const own = active.filter((office) => office.city === city);
    const last = active.find((office) => office.id === remembered);
    if (last) return !city || !own.length || last.city === city ? last.id : null;
    if (granted && active.length === 1) return active[0].id;
    if (city) return own.length === 1 ? own[0].id : null;
    return active.length === 1 ? active[0].id : null;
};

/* Какой офис выбран в «Выдаче» после того, как список офисов пришёл или
   сменился. Подставить офис экран может один раз — когда список пришёл
   впервые (picked = false). Выбранный офис отняли (доступ сняли, офис вывели
   из учёта) — выбор сбрасывается, и человек выбирает сам: пересадить его
   молча в офис другого города значило бы списывать воду с чужой полки
   (разбор 23.10.2026: после отказа экран сам выбрал Астану и снова предлагал
   «Выдать» офиснику, стоящему в Алматы). */
export const nextOfficeId = (current, offices, { picked = false, city = null, remembered = null, granted = false } = {}) => {
    if (current && (offices || []).some((office) => office.id === current)) return current;
    if (current || picked) return null;
    return defaultOfficeId(offices, city, remembered, granted);
};

/* ── Доступ к выдаче по офисам (владелец 23.10.2026) ─────────────────────────
   «Выбираем офис, далее из фронт-офисников выбираем людей, которым открыт
   данный раздел, и выдаём доступ, чтобы они могли выдавать воду только по
   доступным им офисам». Сервер — water/access.py и PATCH /api/water/access. */

/* Офисы «Выдачи»: офиснику — только те, к которым ему выдан доступ
   (`capabilities.issue_office_ids`), руководителю и супер-админу — все офисы
   учёта (null). Нет поля вовсе (сервер старее фронта) — все, как было. */
export const issueOffices = (offices, allowedIds) => {
    const active = (offices || []).filter((office) => office.is_active !== false);
    if (allowedIds == null) return active;
    const allowed = new Set(allowedIds.map(Number));
    return active.filter((office) => allowed.has(Number(office.id)));
};

/* Офис в окне доступа выбирают сегментами, только когда их два-три и у каждого
   свой город: подпись сегмента — город. Два офиса одного города сегментом не
   различить — подпись с адресом не входит в окно (разбор 23.10.2026: «Астана»
   уезжала за край, на телефоне подписи наезжали друг на друга), — тогда список,
   как и при четырёх офисах и больше. */
const cityKey = (value) => String(value || '').trim().toLowerCase();

export const accessSegments = (offices) => {
    const list = offices || [];
    const cities = list.map((office) => cityKey(office?.city));
    return list.length > 0 && list.length <= 3 && cities.every(Boolean) && new Set(cities).size === list.length;
};

/* Черновик окна: офис → кто в нём выдаёт. */
export const accessDraftOf = (access) => Object.fromEntries(
    (access?.offices || []).map((office) => [office.id, (office.user_ids || []).map(Number)]),
);

/* Что отправить серверу — только переключённые пары «офис, сотрудник»: снятая
   и снова поставленная галочка — не правка. Разницей, а не списком офиса: так
   два руководителя, правящие разных людей, не затирают друг друга. */
export const accessChanges = (draft, access) => {
    const changes = [];
    (access?.offices || []).forEach((office) => {
        const before = new Set((office.user_ids || []).map(Number));
        const after = new Set((draft?.[office.id] || []).map(Number));
        [...after].sort((a, b) => a - b).forEach((userId) => {
            if (!before.has(userId)) changes.push({ water_office_id: office.id, user_id: userId, granted: true });
        });
        [...before].sort((a, b) => a - b).forEach((userId) => {
            if (!after.has(userId)) changes.push({ water_office_id: office.id, user_id: userId, granted: false });
        });
    });
    return changes;
};

/* Отметить или снять сотрудника в офисе черновика. */
export const toggleAccess = (draft, officeId, userId) => {
    const current = (draft?.[officeId] || []).map(Number);
    const id = Number(userId);
    return {
        ...draft,
        [officeId]: current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    };
};

/* Строка в «Настройках»: сколько человек выдаёт в каждом офисе — «Алматы: 6 ·
   Астана: 2». Когда офисы так не назвать (два в одном городе, больше трёх),
   строка говорит итогом — «3 офиса, доступ у 8 сотрудников»: перечень с
   адресами в одну строку не входит и не читается, он — в окне. */
export const accessSummary = (access) => {
    const offices = access?.offices || [];
    if (!offices.length) return 'Офисов в учёте пока нет';
    if (accessSegments(offices)) {
        return offices.map((office) => `${String(office.city).trim()}: ${(office.user_ids || []).length}`).join(' · ');
    }
    const people = new Set(offices.flatMap((office) => (office.user_ids || []).map(Number))).size;
    const count = `${offices.length} ${plural(offices.length, 'офис', 'офиса', 'офисов')}`;
    return people
        ? `${count}, доступ у ${people} ${plural(people, 'сотрудника', 'сотрудников', 'сотрудников')}`
        : `${count}, доступа пока ни у кого`;
};

/* Отказ сервера несёт свежий доступ (офис вывели из учёта, сотрудника
   перевели). Правку человека он не отменяет: переключённое заново ложится на
   свежее состояние, кроме того, что стало невозможным, — офиса больше нет в
   учёте или выдать некому. Иначе одна устаревшая отметка стирала бы все
   остальные (разбор 23.10.2026). */
export const rebaseAccessDraft = (draft, access, fresh) => {
    const next = accessDraftOf(fresh);
    const people = new Set((fresh?.people || []).map((person) => Number(person.id)));
    accessChanges(draft, access).forEach(({ water_office_id: officeId, user_id: userId, granted }) => {
        if (!(officeId in next)) return;
        if (granted && !people.has(userId)) return;
        const current = next[officeId].filter((item) => item !== userId);
        next[officeId] = granted ? [...current, userId] : current;
    });
    return next;
};

/* Окно доступа — черновик одного открытия. Переходы — чистой функцией (её
   гоняет node --test), экран только отправляет действия:
     open      — окно открылось: черновик — сохранённый доступ;
     choose    — другой офис; toggle — отметить или снять человека;
     reset     — «Отменить»: черновик — снова сохранённый доступ;
     ask-close — закрывают с несохранёнными отметками: подвал спрашивает;
     keep      — «Вернуться» к отметкам;
     refused   — отказ сервера со свежим доступом: правка переносится на него.
   Вопрос в подвале снимается любым действием с отметками — человек вернулся
   к работе. */
export const accessSession = (session, action, access) => {
    switch (action?.type) {
        case 'open':
            return { draft: accessDraftOf(access), officeId: action.officeId ?? null, confirmClose: false };
        case 'choose':
            return { ...session, officeId: action.officeId, confirmClose: false };
        case 'toggle':
            return { ...session, draft: toggleAccess(session.draft, action.officeId, action.userId), confirmClose: false };
        case 'reset':
            return { ...session, draft: accessDraftOf(access), confirmClose: false };
        case 'ask-close':
            return { ...session, confirmClose: true };
        case 'keep':
            return { ...session, confirmClose: false };
        case 'refused':
            return { ...session, draft: rebaseAccessDraft(session.draft, access, action.fresh), confirmClose: false };
        default:
            return session;
    }
};

/* Что делать с попыткой закрыть окно (крестик, щелчок мимо, «назад» на
   телефоне): пока идёт сохранение — остаться (ответ некуда было бы показать);
   есть несохранённые отметки — спросить в подвале, как форма заявки «Оплаты
   счетов» (разбор 23.10.2026: щелчок мимо окна молча стирал всю правку);
   спросили уже — закрыть. */
export const accessCloseStep = ({ saving, dirty, confirmClose }) => {
    if (saving) return 'stay';
    if (dirty && !confirmClose) return 'ask';
    return 'close';
};

/* Сотрудники в окне — группами по городу, как списки «Настроек» iOS: подпись
   группы называет город один раз, а не каждая строка (разбор 23.10.2026: шесть
   «Алматы» подряд — шум, и колонка города обрезала ФИО на телефоне). Город
   выбранного офиса — первым, остальные — по алфавиту, без города — последними;
   внутри — по имени. Порядок не зависит от отметок: строка не уезжает из-под
   пальца, когда её отмечают. */
export const accessPeopleGroups = (people, office) => {
    const own = cityKey(office?.city);
    // Группа — по городу без оглядки на регистр и пробелы («  алматы » из
    // карточки — тот же город); подпись — как город записан у первого.
    const groups = new Map();
    (people || []).forEach((person) => {
        const key = cityKey(person?.city);
        if (!groups.has(key)) groups.set(key, { city: String(person?.city || '').trim(), rows: [] });
        groups.get(key).rows.push(person);
    });
    const rank = (key) => (key === own && own ? 0 : key ? 1 : 2);
    return [...groups.entries()]
        .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b, 'ru'))
        .map(([, { city, rows }]) => ({
            city,
            title: city ? `Сотрудники · ${city}` : 'Сотрудники · город не указан',
            rows: [...rows].sort((a, b) => String(a?.name || '').localeCompare(String(b?.name || ''), 'ru')
                || Number(a?.id) - Number(b?.id)),
        }));
};
