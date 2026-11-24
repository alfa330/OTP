/*
 * Подписи и форматирование раздела «Воронка найма» (#404).
 *
 * Время с сервера — наивное время Алматы («2026-10-30T14:00:00»), поэтому разбирается по
 * цифрам строки, а не через new Date(): в браузере с другим поясом Date сдвинул бы часы.
 * Коды этапов, подстатусов и причин — те же, что в hiring/rules.py; подписи приходят в
 * meta первого ответа, здесь — только то, что нужно до загрузки и для форматирования.
 */

export const STAGES = [
    'contacts', 'responses', 'in_work', 'invited_office', 'invited_training', 'refused', 'on_training',
    'on_line', 'dropped', 'hired', 'no_response',
];
export const STAGE_LABELS = {
    contacts: 'Контакты',
    responses: 'Отклики',
    in_work: 'В работе',
    invited_office: 'Приглашен в офис',
    invited_training: 'Приглашен на обучение',
    refused: 'Отказ',
    on_training: 'Вышел на обучение',
    on_line: 'Вышел на линию',
    dropped: 'Отсев',
    hired: 'Отработал 7 дней',
    // Категория тренера (SZOV-10), не этап: карточка остаётся в «Отработал 7 дней», с десятой смены — ещё и здесь
    // (колонка доски за «7 днями», строка воронки, история). Ставит система сама, как и «7 дней».
    hired_10: 'Отработал 10 дней',
    no_response: 'Не отвечает звонок/чат',
};
/* «Не отвечает звонок/чат» — архив молчащих (владелец 20.11.2026: «отдельный список как архив», «по умолчанию
   завёрнутый и серый»): последняя колонка доски свёрнута, пока её не раскрыли (BoardTab, ARCHIVE_OPEN_KEY). */
export const ARCHIVE_STAGE = 'no_response';
export const POOL_STAGES = ['contacts', 'responses'];
export const FINAL_STAGES = ['refused', 'dropped', 'hired'];
export const FUNNEL_STAGES = ['in_work', 'dialed', 'invited_office', 'invited_training', 'on_training', 'on_line', 'hired', 'hired_10'];
export const FUNNEL_LABELS = { ...STAGE_LABELS, dialed: 'Дозвон' };

/* «Недозвон / не ответил» — кандидат не ответил ни на звонок, ни на сообщение в WhatsApp (владелец
   09.11.2026). Исход самого звонка в ленте — TOUCH_RESULT_LABELS: там «Недозвон» без WhatsApp. */
export const SUBSTAGE_LABELS = {
    dial: 'Дозвон', no_answer: 'Недозвон / не ответил', whatsapp: 'Переписка в WhatsApp',
    expected: 'Ожидается', came: 'Пришел', no_show: 'Не пришел',
    ours: 'Наш отказ', candidate: 'Отказ кандидата', self: 'Сам', we: 'Мы',
};
export const TOUCH_RESULT_LABELS = { dial: 'Дозвон', no_answer: 'Недозвон' };

/* Цвет источника (владелец 20.11.2026: «понятие Канал надо убрать и всё запихать в источник… выбрать цвет прямо в
   источниках»; стартовые цвета — его же слова о каналах 09.11.2026: Таргет — синий, hh — красный, Каспи — розовый).
   Палитра — как у списков «Напоминаний»: коды те же, что rules.SOURCE_COLORS на сервере; без цвета — серый. Источник
   на карточке — светлая плашка с цветной точкой: оттенок различим и у близких цветов (красный и розовый), а плашка не
   кричит рядом с подстатусом. Классы — целыми строками, иначе сборка Tailwind их не увидит. */
export const SOURCE_COLORS = {
    red: { label: 'Красный', pill: 'bg-red-50 text-red-700 ring-1 ring-red-200/80', dot: 'bg-red-500' },
    orange: { label: 'Оранжевый', pill: 'bg-orange-50 text-orange-700 ring-1 ring-orange-200/80', dot: 'bg-orange-500' },
    yellow: { label: 'Жёлтый', pill: 'bg-yellow-50 text-yellow-800 ring-1 ring-yellow-200/80', dot: 'bg-yellow-400' },
    green: { label: 'Зелёный', pill: 'bg-green-50 text-green-700 ring-1 ring-green-200/80', dot: 'bg-green-500' },
    teal: { label: 'Бирюзовый', pill: 'bg-teal-50 text-teal-700 ring-1 ring-teal-200/80', dot: 'bg-teal-500' },
    blue: { label: 'Синий', pill: 'bg-blue-50 text-blue-700 ring-1 ring-blue-200/80', dot: 'bg-blue-500' },
    violet: { label: 'Фиолетовый', pill: 'bg-violet-50 text-violet-700 ring-1 ring-violet-200/80', dot: 'bg-violet-500' },
    pink: { label: 'Розовый', pill: 'bg-pink-50 text-pink-700 ring-1 ring-pink-200/80', dot: 'bg-pink-500' },
    gray: { label: 'Серый', pill: 'bg-slate-100 text-slate-700 ring-1 ring-slate-200/80', dot: 'bg-slate-400' },
};
export const SOURCE_COLOR_CODES = Object.keys(SOURCE_COLORS);
export const sourceTone = (color) => SOURCE_COLORS[color] || SOURCE_COLORS.gray;

/** Источники, которые можно поставить карточке: включённые и того же вида — отклику источники откликов, контакту
 *  базы (сервер проверяет то же, HiringService._card_source). Порядок — справочника. */
export const sourceChoices = (card, sources) => {
    const kind = card?.kind === 'contact' ? 'contacts' : 'responses';
    return (sources || []).filter((source) => source.kind === kind && source.is_active !== false);
};

/** Добавить или убрать значение из списка (свёрнутые колонки и группы доски); исходный массив не меняется. */
export const toggleInList = (list, value) => {
    const items = Array.isArray(list) ? list : [];
    return items.includes(value) ? items.filter((item) => item !== value) : [...items, value];
};

/** Свёрнута ли колонка доски: обычная — если её свернули (collapsed), архив «Не отвечает звонок/чат» — пока его не
 *  раскрыли (opened; владелец 20.11.2026: «по умолчанию завёрнутый и серый»). */
export const columnCollapsed = (stage, collapsed, opened) => (stage === ARCHIVE_STAGE
    ? !(Array.isArray(opened) && opened.includes(stage))
    : Array.isArray(collapsed) && collapsed.includes(stage));

/** Ключ группы «Откликов» в списке раскрытых: id источника, у карточек без источника — 'none'. */
export const groupKey = (group) => (group?.source_id === null || group?.source_id === undefined ? 'none' : String(group.source_id));

const GROUP_KEY_RE = /^(\d+|none)$/;

/* «Приглашен в офис» и «Приглашен на обучение» на доске — по дням (владелец 24.11.2026: «разбивку по датам приглашения
   начиная от сегодня… как в откликах раскрыть и посмотреть людей, которые приглашены к этому дню»; «прошлые дни должны
   уходить вниз»). День в офисе — дата визита, на обучении — старт группы; порядок дней задаёт сервер (rules.day_order).
   Дни свёрнуты, пока их не раскрыли, — как группы «Откликов». */
export const DATED_STAGES = ['invited_office', 'invited_training'];
const DAY_ARG = { invited_office: 'office', invited_training: 'training' };
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const DAY_KEY_RE = /^(\d{4}-\d{2}-\d{2}|none)$/;
// Сколько раскрытых дней колонки помнит браузер: прошедшие дни иначе копились бы в нём без конца.
const OPEN_DAYS_KEPT = 60;

/** Ключ дня в списке раскрытых: «2026-11-24», у карточек без даты — 'none'. */
export const dayKey = (group) => (group?.day ? String(group.day).slice(0, 10) : 'none');

/** Раскрытые дни колонок, как их запомнил браузер: {этап: [ключи]}; чужое и мусор отбрасываются. */
export const openDaysFrom = (raw) => Object.fromEntries(DATED_STAGES.map((stage) => [stage,
    (raw && typeof raw === 'object' && Array.isArray(raw[stage]) ? raw[stage] : [])
        .map(String).filter((key) => DAY_KEY_RE.test(key)).slice(-OPEN_DAYS_KEPT)]));

/** «Показать ещё» у дня: дата добавляется в список один раз; у группы без даты раскрывать нечем. */
export const withExpandedDay = (list, group) => {
    const items = Array.isArray(list) ? list : [];
    const key = dayKey(group);
    return key === 'none' || items.includes(key) ? items : [...items, key];
};

/*
 * Запрос доски. Период — общий для всех колонок (владелец 24.11.2026: «сделать пикер периода общим на все столбцы»; до
 * этого он был только у «Откликов»): from / to по дате отклика, «по» — включительно. open — раскрытые группы «Откликов»
 * (группы свёрнуты по умолчанию — решение владельца 20.11.2026): карточки сервер отдаёт только им, '-' — ни одной; без
 * open сервер отдал бы карточки всем. expand — источники, где нажали «Показать ещё». Так же дни колонок «Приглашен в
 * офис / на обучение»: openDays и expandedDays — {этап: [дни]} → office_open, training_open (с openDays уходят оба,
 * '-' — ни одного дня) и office_expand, training_expand. Пустые отборы не отправляются.
 */
export const boardQuery = ({
    mine = false, query = '', filters = {}, period = {}, expanded = [], open, openDays, expandedDays,
} = {}) => {
    const params = {
        mine: mine ? '1' : undefined,
        q: String(query || '').trim() || undefined,
        vacancy_id: filters.vacancy_id || undefined,
        city: filters.city || undefined,
        source_id: filters.source_id || undefined,
        recruiter_id: filters.recruiter_id || undefined,
        from: period?.from || undefined,
        to: period?.to || undefined,
        open: Array.isArray(open) ? (open.filter((key) => GROUP_KEY_RE.test(String(key))).join(',') || '-') : undefined,
        expand: (expanded || []).filter((key) => /^\d+$/.test(String(key))).join(',') || undefined,
    };
    DATED_STAGES.forEach((stage) => {
        if (openDays && typeof openDays === 'object') {
            const keys = (Array.isArray(openDays[stage]) ? openDays[stage] : []).filter((key) => DAY_KEY_RE.test(String(key)));
            params[`${DAY_ARG[stage]}_open`] = keys.join(',') || '-';
        }
        const more = Array.isArray(expandedDays?.[stage]) ? expandedDays[stage] : [];
        params[`${DAY_ARG[stage]}_expand`] = more.filter((key) => DAY_RE.test(String(key))).join(',') || undefined;
    });
    return Object.fromEntries(Object.entries(params).filter(([, value]) => value !== undefined));
};

/** Карточки группы «Откликов»: сервер присылает их номера (card_ids), сами карточки лежат в колонке. */
export const groupCards = (group, cardsById) => (group?.card_ids || []).map((id) => cardsById.get(id)).filter(Boolean);

/** «Показать ещё» у группы: её источник добавляется в expand один раз; у группы без источника раскрывать нечем. */
export const withExpanded = (list, group) => {
    const items = Array.isArray(list) ? list : [];
    if (group?.source_id === null || group?.source_id === undefined) return items;
    const key = String(group.source_id);
    return items.includes(key) ? items : [...items, key];
};

/** Тело запроса окна источника: новому — название, вид и цвет, существующему — название и цвет (вид не меняется). */
export const sourceRequestBody = (form, existing = null) => {
    const body = { name: String(form?.name || '').trim(), color: form?.color || null };
    return existing?.id ? body : { ...body, kind: form?.kind === 'contacts' ? 'contacts' : 'responses' };
};

/* Цвет только со смыслом: завершения (отказ, отсев — красный; найм закрыт — зелёный),
   просрочка — янтарный. Рабочие этапы не красятся. */
export const STAGE_TONES = { refused: 'red', dropped: 'red', hired: 'green', hired_10: 'green', no_response: 'gray' };
export const SUBSTAGE_TONES = { no_answer: 'amber', no_show: 'red', came: 'green', dial: 'green' };

export const TOUCH_LABELS = {
    call: 'Звонок', whatsapp: 'WhatsApp', comment: 'Комментарий', repeat_response: 'Повторный отклик', system: 'Система',
};
/* Звонки из телефонии Binotel (hiring/calls.py): исход — как в журнале «Удаленного КЦ». */
export const CALL_RESULT_LABELS = { answered: 'Дозвонились', busy: 'Занято', no_answer: 'Не поднял трубку', other: 'Не состоялся' };
export const CALL_RESULT_TONES = { answered: 'green', busy: 'amber', no_answer: 'amber', other: 'slate' };
export const CALL_DIRECTION_LABELS = { out: 'Исходящий звонок', in: 'Входящий звонок' };

/** «3:07» — длительность разговора в секундах. */
export const fmtDuration = (seconds) => {
    const total = Math.max(0, Number(seconds) || 0);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
};
export const MOTIVATION_LABELS = {
    pending: 'Ждёт 7 смен', counted: 'Засчитано', paid: 'Выплачено', cancelled: 'Отменено',
};
export const MOTIVATION_TONES = { pending: 'amber', counted: 'green', paid: 'green', cancelled: 'red' };
export const GRANT_LABELS = { lead: 'Руководитель рекрутинга', staff: 'Штатный рекрутер', remote: 'Удалённый рекрутер' };
export const REASON_KIND_LABELS = {
    refusal_ours: 'Наш отказ', refusal_candidate: 'Отказ кандидата', drop_self: 'Отсев «сам»', drop_we: 'Отсев «мы»',
};
/* Инициатор перехода → вид причины (как rules.REASON_KIND_FOR). */
export const reasonKindFor = (stage, initiator) => ({
    'refused:ours': 'refusal_ours', 'refused:candidate': 'refusal_candidate',
    'dropped:self': 'drop_self', 'dropped:we': 'drop_we',
}[`${stage}:${initiator}`] || null);

export const ASSIGNMENT_LABELS = {
    take: 'Взял в работу', reassign: 'Переназначена', release: 'Возвращена в пул', auto_timeout: 'Вернулась в пул по сроку',
    reactivate: 'Повторная активация', recruiter_off: 'Рекрутер отключён',
};
export const INTEGRATION_LABELS = { hh: 'HeadHunter', olx: 'OLX', webhook: 'Вебхук площадок' };
export const INTEGRATION_STATE_LABELS = { ok: 'Работает', not_configured: 'Не настроена', error: 'Ошибка' };

/* Вакансии hh.kz в «Интеграциях» (владелец 23.11.2026: новые вакансии hh переносятся сами, руководитель выключает их или
   ставит вакансию iCORE). Выбор в списке — одним значением: 'off' — не переносить, 'on' — без вакансии, 'v<id>' — с
   вакансией iCORE. Правило с сервера: {on, vacancy_id}. */
/* Вакансии hh.kz в «Настройках → Интеграции» (владелец 23.11.2026: «Добавь в настройки возможность выбрать какие
 * вакансии отслеживать»): тумблер «отслеживать» у каждой, у отслеживаемой — с какой вакансией iCORE заводить карточки. */
export const HH_NO_ICORE = 'Без вакансии iCORE';
export const HH_LINK_ICORE = 'Связать с вакансией iCORE';

/* Варианты выбора вакансии iCORE: действующие вакансии воронки; у связанной — первым «Без вакансии iCORE», чтобы
 * связь можно было снять. Несвязанная показывает серую подсказку HH_LINK_ICORE, а не одинаковую надпись в каждой строке. */
export const hhIcoreOptions = (vacancies, linked = false) => [
    ...(linked ? [{ value: '', label: HH_NO_ICORE }] : []),
    ...(vacancies || []).filter((v) => v.is_active !== false).map((v) => ({ value: String(v.id), label: v.name })),
];

/* Значение выбора вакансии iCORE: связанная вакансия, если её ещё можно выбрать, иначе null (подсказка) — так же
 * поступает сервер (выключенную вакансию iCORE отклику не ставит). */
export const hhIcoreValue = (vacancyId, options) => {
    const value = vacancyId ? String(vacancyId) : '';
    return value && (options || []).some((option) => option.value === value) ? value : null;
};

/* Подпись под вакансией hh: город, отклики в кабинете и — у включённой позже — с какого момента её отклики идут. */
export const hhVacancyMeta = (vacancy) => [
    vacancy.area,
    vacancy.responses != null ? `откликов ${vacancy.responses}` : null,
    vacancy.on && vacancy.tracked_since ? `отслеживается с ${shortTime(vacancy.tracked_since)}` : null,
].filter(Boolean).join(' · ');

/* Тело PUT /integrations/hh/settings: прежние правила (и вакансий, которых мост сейчас не видит) + правка одной.
 * Выключение связь с вакансией iCORE не стирает: включили снова — она на месте. */
export const hhSettingsBody = (savedRules, id, change) => {
    const vacancies = {};
    Object.entries(savedRules || {}).forEach(([key, rule]) => {
        if (rule && typeof rule === 'object') vacancies[key] = { on: rule.on !== false, vacancy_id: rule.vacancy_id || null };
    });
    if (id != null && change) {
        vacancies[String(id)] = { on: change.on !== false, vacancy_id: change.vacancy_id || null };
    }
    return { vacancies };
};

/* Резюме отклика hh во вкладке «Резюме» карточки (владелец 23.11.2026: «все резюме и письмо человека который обратился
 * отобразились сразу в карточке»). Подписи кодам hh даёт сервер; здесь — деньги, сроки и сводка одной строкой. */
const HH_MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

/** «250 000 ₸» по {amount, sign}. */
export const hhMoney = (salary) => {
    if (!salary || !Number.isFinite(Number(salary.amount)) || Number(salary.amount) <= 0) return '';
    const amount = String(Math.round(Number(salary.amount))).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    return `${amount} ${salary.sign || salary.currency || ''}`.trim();
};

/** «5 лет 3 мес.», «1 год», «8 мес.» по {years, months}. */
export const hhSpan = (span) => {
    const years = Number(span?.years) || 0;
    const months = Number(span?.months) || 0;
    return [years ? `${years} ${plural(years, 'год', 'года', 'лет')}` : '', months ? `${months} мес.` : '']
        .filter(Boolean).join(' ');
};

/** «янв 2020 — сейчас» по датам места работы «2020-01-01» / null. */
export const hhPeriod = (start, end) => {
    const label = (iso) => {
        const match = /^(\d{4})-(\d{2})/.exec(String(iso || ''));
        return match ? `${HH_MONTHS_SHORT[Number(match[2]) - 1] || ''} ${match[1]}`.trim() : '';
    };
    const from = label(start);
    if (!from) return '';
    return `${from} — ${label(end) || 'сейчас'}`;
};

/** Сводка резюме для главной вкладки карточки: «Оператор call-центра · 250 000 ₸ · опыт 5 лет 3 мес.». */
export const hhResumeSummary = (resume) => {
    if (!resume) return '';
    const span = hhSpan(resume.experience_total);
    return [resume.title, hhMoney(resume.salary), span ? `опыт ${span}` : (resume.experience?.length ? null : 'без опыта')]
        .filter(Boolean).join(' · ');
};

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
    'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_NOM = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август',
    'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

const parts = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(String(iso || ''));
    if (!match) return null;
    const [, year, month, day, hour, minute] = match;
    return { year: +year, month: +month, day: +day, hour: hour || '00', minute: minute || '00', hasTime: Boolean(hour) };
};

/** «30.10 14:00» (без времени — «30.10»). */
export const shortTime = (iso) => {
    const p = parts(iso);
    if (!p) return '';
    const date = `${String(p.day).padStart(2, '0')}.${String(p.month).padStart(2, '0')}`;
    return p.hasTime ? `${date} ${p.hour}:${p.minute}` : date;
};

/** «30 октября, 14:00». */
export const longTime = (iso) => {
    const p = parts(iso);
    if (!p) return '';
    return `${p.day} ${MONTHS[p.month - 1]}${p.hasTime ? `, ${p.hour}:${p.minute}` : ''}`;
};

/** «30.10.2026». */
export const fullDate = (iso) => {
    const p = parts(iso);
    return p ? `${String(p.day).padStart(2, '0')}.${String(p.month).padStart(2, '0')}.${p.year}` : '';
};

/** «Октябрь 2026» по «2026-10». */
export const monthLabel = (month) => {
    const match = /^(\d{4})-(\d{2})/.exec(String(month || ''));
    return match ? `${MONTHS_NOM[+match[2] - 1]} ${match[1]}` : '';
};

/** «2026-10» текущего месяца браузера (месяц отчёта по умолчанию). */
export const currentMonth = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
};

/** Сдвиг месяца: «2026-10», −1 → «2026-09». */
export const shiftMonth = (month, delta) => {
    const match = /^(\d{4})-(\d{2})/.exec(String(month || ''));
    if (!match) return currentMonth();
    const total = +match[1] * 12 + (+match[2] - 1) + delta;
    return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}`;
};

/** Сегодня «2026-10-30» (локальная дата браузера). */
export const todayIso = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

/** Дата ISO + дни → ISO (без поясов браузера). */
export const addDays = (iso, days) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
    if (!match) return '';
    const moment = new Date(Date.UTC(+match[1], +match[2] - 1, +match[3] + days));
    return moment.toISOString().slice(0, 10);
};

/** Дата и время из полей формы → «2026-10-30T14:00». */
export const joinDateTime = (date, time) => (date ? `${date}T${time || '00:00'}` : '');

/** Разобрать «2026-10-30T14:00:00» на поля формы. */
export const splitDateTime = (iso) => {
    const p = parts(iso);
    if (!p) return { date: '', time: '' };
    return { date: `${p.year}-${String(p.month).padStart(2, '0')}-${String(p.day).padStart(2, '0')}`, time: `${p.hour}:${p.minute}` };
};

/** Сколько дней прошло от момента до сегодня (по датам, без часов): «3 дн». */
export const daysSince = (iso, today = todayIso()) => {
    const a = parts(iso);
    const b = parts(today);
    if (!a || !b) return null;
    return Math.round((Date.UTC(b.year, b.month - 1, b.day) - Date.UTC(a.year, a.month - 1, a.day)) / 86400000);
};

const WEEKDAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

/** Шапка дня в колонке доски: «Сегодня, 24 ноября», «Завтра, 25 ноября», «Вчера, 23 ноября», «Чт, 26 ноября»; другой
 *  год — с годом. today — день сервера (Алматы): по нему же сервер расставил дни. */
export const dayLabel = (day, today = todayIso()) => {
    const p = parts(day);
    if (!p) return 'Без даты';
    const date = `${p.day} ${MONTHS[p.month - 1]}`;
    const since = daysSince(day, today);
    const ahead = since === null ? null : -since;
    if (ahead === 0) return `Сегодня, ${date}`;
    if (ahead === 1) return `Завтра, ${date}`;
    if (ahead === -1) return `Вчера, ${date}`;
    const year = parts(today)?.year === p.year ? '' : ` ${p.year}`;
    return `${WEEKDAYS[new Date(Date.UTC(p.year, p.month - 1, p.day)).getUTCDay()]}, ${date}${year}`;
};

/** День уже прошёл — сервер ставит такие дни вниз колонки. */
export const isPastDay = (day, today = todayIso()) => DAY_RE.test(String(day || '').slice(0, 10))
    && String(day).slice(0, 10) < String(today).slice(0, 10);

/** Время визита в офис «14:00» — на карточке внутри дня (сам день — в шапке группы). */
export const visitTime = (card) => {
    const p = parts(card?.office_visit_at);
    return p?.hasTime ? `${p.hour}:${p.minute}` : '';
};

/** Дата обучения кандидата: своя, если обучение переносили (training_date), иначе старт группы. */
export const trainingDay = (card) => String(card?.training_date || card?.group_start_date || '').slice(0, 10);

/*
 * Перенос даты приглашения (владелец 24.11.2026: «в колонках Приглашен в офис и Приглашен в обучение возможность менять
 * дату приглашения с указанием причины в категориях — форс-мажор, поменялись планы, учёба, другое с комментом»).
 * Причины — из меты раздела (rules.RESCHEDULE_REASONS), у «Другое» нужен комментарий. Переносить можно закреплённому
 * рекрутеру и руководителю, пока карточка в одной из двух колонок и не закрыта.
 */
export const canReschedule = (card, ownerOrLead) => Boolean(card && ownerOrLead && !card.is_closed && DATED_STAGES.includes(card.stage));

/** Что не так с формой переноса — строкой для тоста; всё в порядке — ''. current — нынешняя дата (визиту — со временем). */
export const rescheduleProblem = (form, office, reasons = [], current = '') => {
    if (!form?.date) return office ? 'Укажите новую дату и время визита' : 'Укажите новую дату обучения';
    if (!form.reason) return 'Укажите причину переноса';
    if ((reasons || []).find((reason) => reason.code === form.reason)?.needs_comment && !String(form.comment || '').trim()) {
        return 'Для «Другое» нужен комментарий';
    }
    const chosen = office ? joinDateTime(form.date, form.time) : form.date;
    if (current && chosen === String(current).slice(0, chosen.length)) return 'Это та же дата — выберите новую';
    return '';
};

/** Тело POST /cards/<id>/reschedule: визиту — дата со временем, обучению — дата. */
export const rescheduleBody = (form, office) => ({
    when: office ? joinDateTime(form.date, form.time) : form.date,
    reason: form.reason,
    comment: String(form.comment || '').trim() || null,
});

/* Системные строки ленты со своим названием (остальные подписаны «Система»). */
export const SYSTEM_TOUCH_LABELS = { rescheduled: 'Перенос даты' };

/*
 * Звонок из карточки — в iCore Phone на этом же компьютере (владелец 30.10.2026: «заменить tel:
 * на наш icore-phone:call?number=»). Формат — docs/CLICK-TO-CALL.md телефона:
 * icore-phone:call?number=<2–15 цифр>, префикс не нужен — рекрутеры сидят на линиях Binotel.
 * Открывается скрытым iframe (как помощник icore-phone.js), а не переходом страницы: переход
 * поднял бы «Покинуть страницу?» у несохранённых форм. Вызывать прямо в обработчике клика.
 */
export const ICORE_PHONE_FRAME_ID = 'icore-phone-call-frame';

export const buildICorePhoneUrl = (phone) => {
    const digits = String(phone || '').replace(/\D/g, '');
    if (digits.length < 2 || digits.length > 15) return null;
    // «+» впереди обязателен: без него линия Binotel номер не набирала (владелец 30.10.2026); в ссылке — %2B.
    return `icore-phone:call?number=%2B${digits}`;
};

export const openICorePhoneCall = (phone, doc = typeof document === 'undefined' ? null : document) => {
    const url = buildICorePhoneUrl(phone);
    if (!url) throw new Error('Номер для звонка не разобран');
    if (!doc) return url;
    let frame = doc.getElementById(ICORE_PHONE_FRAME_ID);
    if (!frame) {
        frame = doc.createElement('iframe');
        frame.id = ICORE_PHONE_FRAME_ID;
        frame.title = 'Звонок в iCore Phone';
        frame.tabIndex = -1;
        frame.setAttribute('aria-hidden', 'true');
        frame.style.display = 'none';
        doc.body.appendChild(frame);
    }
    frame.src = url;
    return url;
};

export const errorOf = (error, fallback) => error?.response?.data?.error || fallback;
export const codeOf = (error) => error?.response?.data?.code || '';

/** «+7 705 123 45 67» из «+77051234567». */
export const prettyPhone = (phone) => {
    const digits = String(phone || '').replace(/\D/g, '');
    if (digits.length !== 11) return String(phone || '');
    return `+7 ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7, 9)} ${digits.slice(9)}`;
};

/** Инициалы для круглого бейджа рекрутера. */
export const initials = (name) => String(name || '').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();

/** Конверсия для таблицы: «42,1 %» / «—». */
export const percent = (value) => (value === null || value === undefined ? '—' : `${String(value).replace('.', ',')} %`);

/** Склонение: 1 карточка, 2 карточки, 5 карточек. */
export const plural = (n, one, few, many) => {
    const abs = Math.abs(n) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last > 1 && last < 5) return few;
    if (last === 1) return one;
    return many;
};

/*
 * Места в группе на обучение (владелец 18.11.2026): вместимость задаёт тренер, рекрутер видит, сколько
 * ещё кандидатов войдёт. Место занимает приглашённый и не отпавший (hiring/rules.py SEAT_STAGES); сервер
 * присылает capacity и seats_taken. У групп до 18.11.2026 вместимости нет — предела нет, места не считаются.
 */
export const GROUP_CAPACITY_MAX = 500;

export const groupSeats = (group) => {
    const taken = Math.max(0, Math.trunc(Number(group?.seats_taken) || 0));
    const raw = group?.capacity;
    const capacity = raw === null || raw === undefined || raw === '' || !Number.isFinite(Number(raw)) ? null : Number(raw);
    if (capacity === null) return { capacity: null, taken, free: null, full: false, share: 0, label: 'вместимость не указана' };
    const free = Math.max(0, capacity - taken);
    return {
        capacity, taken, free, full: free === 0, share: capacity > 0 ? Math.min(1, taken / capacity) : 1,
        label: free === 0 ? 'мест нет' : `свободно ${free} из ${capacity}`,
    };
};

/** Название группы по дате старта, пока тренер не написал своё: «Группа 24.11». */
export const groupNameFor = (startIso) => (shortTime(startIso) ? `Группа ${shortTime(startIso)}` : '');

/** Название при смене даты старта: пока его не трогали руками, оно идёт за датой; своё не затирается. */
export const groupNameAfterStart = (name, startIso, touched) => (touched ? name : groupNameFor(startIso));

/** Шаг степпера вместимости: из пустого поля «+» ставит число занятых мест (не меньше 1) — меньше всё равно
 *  нельзя; «−» не опускает ниже занятых и ниже 1; сверху — GROUP_CAPACITY_MAX. */
export const stepCapacity = (value, delta, taken = 0) => {
    const floor = Math.min(GROUP_CAPACITY_MAX, Math.max(1, Math.trunc(Number(taken) || 0)));
    const number = Math.trunc(Number(value) || 0);
    if (!number) return delta > 0 ? String(floor) : '';
    return String(Math.min(GROUP_CAPACITY_MAX, Math.max(floor, number + delta)));
};

/** Тело запроса окна группы. Тренера и отдел группе назначает руководитель: тренер их не шлёт, иначе
 *  сервер ответит 403 «Тренера и отдел группы назначает руководитель рекрутинга». */
export const groupRequestBody = (form, { lead = false } = {}) => {
    const body = {
        name: String(form?.name || '').trim() || groupNameFor(form?.start_date),
        start_date: form?.start_date || '',
        capacity: Number(form?.capacity),
        vacancy_id: form?.vacancy_id ? Number(form.vacancy_id) : null,
    };
    if (lead) {
        body.trainer_id = form?.trainer_id ? Number(form.trainer_id) : null;
        body.department_id = form?.department_id ? Number(form.department_id) : null;
    }
    return body;
};

/** Когда группа: «старт 24 ноября», а уже начавшаяся — «идёт с 5 ноября» (рекрутер видит, что обучение идёт). */
export const groupWhen = (group, today = todayIso()) => {
    const start = String(group?.start_date || '').slice(0, 10);
    if (!start) return '';
    return start < String(today).slice(0, 10) ? `идёт с ${longTime(start)}` : `старт ${longTime(start)}`;
};

/** Сервер ответил «мест нет»: группа помечается полной у себя, даже если перечитать места не удалось. */
export const markGroupFull = (groups, groupId) => (groups || []).map((group) => (
    String(group.id) === String(groupId) && group.capacity !== null && group.capacity !== undefined
        ? { ...group, seats_taken: Math.max(Number(group.seats_taken) || 0, Number(group.capacity)), seats_free: 0 }
        : group));

/** Вкладки раздела по правам (hiring/access.py, capabilities). Тренеру — не рекрутеру и не руководителю —
 *  «Группы» первой: группы он ведёт сам, а доска — его кандидаты по этапам. Отчётов у тренера нет: там цифры
 *  рекрутеров, по нему пустые. «Чаты» (переписка WhatsApp найма, владелец 19.11.2026) — тем, кто отвечает
 *  кандидатам: рекрутерам и руководителю рекрутинга. */
export const hiringTabs = (caps = {}) => {
    const trainerOnly = Boolean(caps.groups && !caps.recruiter && !caps.lead);
    return [
        trainerOnly && 'groups',
        caps.recruiter && 'desk',
        'board',
        (caps.recruiter || caps.lead) && 'chats',
        caps.groups && !trainerOnly && 'groups',
        (caps.recruiter || caps.lead || caps.dept_head) && 'reports',
        caps.upload && 'upload',
        caps.manage && 'settings',
    ].filter(Boolean);
};

/** Отбор карточек по строке поиска на клиенте: имя, телефон, город, вакансия, номер. */
export const matchesQuery = (card, query) => {
    const text = String(query || '').trim().toLowerCase();
    if (!text) return true;
    const digits = text.replace(/\D/g, '');
    const hay = [card.full_name, card.city, card.vacancy_name, card.vacancy_text, card.source_name, card.recruiter_name]
        .map((v) => String(v || '').toLowerCase());
    if (hay.some((v) => v.includes(text))) return true;
    // Номер кандидата — точным совпадением цифр, телефон — вхождением от трёх цифр.
    if (digits && digits === text && String(card.candidate_no || '') === digits) return true;
    if (digits.length >= 3 && String(card.phone || '').replace(/\D/g, '').includes(digits)) return true;
    return false;
};
