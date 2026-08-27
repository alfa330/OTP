/* Раздел «Тренинги»: общие константы, разбор данных и мелкие форматтеры.
 *
 * Всё, что нужно и вкладке «По темам», и вкладке «По группам», живёт здесь —
 * иначе два экрана посчитали бы «сколько занятий по теме» немного по-разному,
 * и это заметили бы как «цифры не сходятся».
 */

// Две семьи тем. Названия — решение владельца: «Базовые / Корпоративные»
// вместо «Дефолтные / Кастомные».
export const FAMILY_BASE = 'base';
export const FAMILY_CORPORATE = 'corporate';

export const FAMILY_LABELS = {
    [FAMILY_BASE]: 'Базовые',
    [FAMILY_CORPORATE]: 'Корпоративные',
};

// Тип корпоративной темы. Пока один — так решил владелец: второй тип без
// реального запроса означал бы поле в форме, которое никто не заполняет
// осмысленно.
export const TOPIC_KIND_LABELS = {
    info: 'Информационный',
};

// Режимы показа. Карточки отвечают на «что за тема и как идёт охват», строки —
// на «сравни темы между собой», календарь — на «что было в этот день».
// Это переключатель вида, а не замена одного другим.
export const VIEW_CARDS = 'cards';
export const VIEW_ROWS = 'rows';
export const VIEW_CALENDAR = 'calendar';

export const TOPIC_VIEWS = [VIEW_CARDS, VIEW_ROWS];
export const GROUP_VIEWS = [VIEW_CARDS, VIEW_ROWS, VIEW_CALENDAR];

export const TAB_TOPICS = 'topics';
export const TAB_GROUPS = 'groups';

// Ключи настроек. Отдельные, потому что вид у двух вкладок разный по смыслу:
// на темах календаря нет, и «последний вид» одной вкладки не должен
// навязываться другой.
const PREFS_KEY = 'trainings.prefs.v1';

// Месяц раздел помнил и раньше — под этим самым ключом. Не переименовываем:
// у людей в браузере уже лежит их последний выбранный месяц.
export const MONTH_KEY = 'trainings_month';

const DEFAULT_PREFS = {
    tab: TAB_TOPICS,
    topicView: VIEW_CARDS,
    groupView: VIEW_CARDS,
};

/* localStorage в приватном окне Safari БРОСАЕТ на чтении и записи, а не
 * возвращает null — поэтому и чтение, и запись в try/catch, и любое
 * прочитанное значение проверяется по белому списку: чужая или устаревшая
 * запись не должна оставить раздел на несуществующем виде. */
export function readPrefs() {
    try {
        const raw = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
        return {
            tab: [TAB_TOPICS, TAB_GROUPS].includes(raw.tab) ? raw.tab : DEFAULT_PREFS.tab,
            topicView: TOPIC_VIEWS.includes(raw.topicView) ? raw.topicView : DEFAULT_PREFS.topicView,
            groupView: GROUP_VIEWS.includes(raw.groupView) ? raw.groupView : DEFAULT_PREFS.groupView,
        };
    } catch (e) {
        return { ...DEFAULT_PREFS };
    }
}

export function writePrefs(prefs) {
    try {
        localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch (e) {
        /* приватное окно — настройка просто не запомнится */
    }
}

export function readMonth() {
    try {
        const raw = localStorage.getItem(MONTH_KEY);
        if (raw && /^\d{4}-\d{2}$/.test(raw)) return raw;
    } catch (e) { /* ignore */ }
    return currentMonthIso();
}

export function writeMonth(month) {
    try {
        localStorage.setItem(MONTH_KEY, month);
    } catch (e) { /* ignore */ }
}

/* ── Время и длительность ───────────────────────────────────────────────── */

/* Сегодняшняя дата ПО ЧАСАМ ПОЛЬЗОВАТЕЛЯ, а не по UTC.
 *
 * `new Date().toISOString().slice(0, 10)` — привычная короткая запись, но она
 * даёт UTC, а портал живёт в Asia/Almaty (UTC+5). С полуночи до пяти утра по
 * местному времени UTC ещё вчерашний: форма занятия открывалась на вчерашней
 * дате, а сегодняшнюю запрещал `max` — ночная смена не могла записать занятие,
 * которое только что провела. */
export function todayIso() {
    const now = new Date();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    return `${now.getFullYear()}-${month}-${day}`;
}

export function currentMonthIso() {
    return todayIso().slice(0, 7);
}

export function timeToMinutes(value) {
    if (!value) return null;
    const [hh, mm] = String(value).split(':').map(Number);
    if (!Number.isFinite(hh) || !Number.isFinite(mm)) return null;
    return hh * 60 + mm;
}

/* Длительность одного занятия в минутах. Тренинг через полночь бэкенд считает
 * переходом на следующие сутки (см. _training_intervals_overlap) — повторяем
 * ту же арифметику, иначе в разделе и в часах будут разные числа. */
export function durationMinutes(training) {
    const start = timeToMinutes(training?.start_time);
    const end = timeToMinutes(training?.end_time);
    if (start == null || end == null) return 0;
    return end > start ? end - start : (end + 24 * 60) - start;
}

export function formatDuration(minutes) {
    const total = Math.max(0, Math.round(Number(minutes) || 0));
    if (total === 0) return '—';
    const hours = Math.floor(total / 60);
    const rest = total % 60;
    if (hours === 0) return `${rest} мин`;
    if (rest === 0) return `${hours} ч`;
    return `${hours} ч ${rest} мин`;
}

const MONTH_NAMES = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MONTH_NAMES_NOM = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

export function formatMonth(month) {
    const [year, m] = String(month || '').split('-').map(Number);
    if (!Number.isFinite(year) || !Number.isFinite(m)) return String(month || '');
    return `${MONTH_NAMES_NOM[m - 1] || m} ${year}`;
}

export function formatDayShort(iso) {
    const [, m, d] = String(iso || '').split('-');
    if (!m || !d) return String(iso || '');
    return `${d}.${m}`;
}

export function formatDayLong(iso) {
    const [year, m, d] = String(iso || '').split('-').map(Number);
    if (!Number.isFinite(year)) return String(iso || '');
    return `${d} ${MONTH_NAMES[m - 1] || ''} ${year}`;
}

/* Русская форма числительного. Без неё раздел писал бы «5 сотрудника». */
export function plural(count, one, few, many) {
    const abs = Math.abs(Number(count) || 0) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last > 1 && last < 5) return few;
    if (last === 1) return one;
    return many;
}

export const pluralPeople = (count) => plural(count, 'сотрудник', 'сотрудника', 'сотрудников');
export const pluralSessions = (count) => plural(count, 'занятие', 'занятия', 'занятий');

/* ── Сводка по темам за месяц ───────────────────────────────────────────── */

/* Собирает карточки тем из плоского списка тренингов за месяц + справочника
 * корпоративных тем.
 *
 * Считается на клиенте СОЗНАТЕЛЬНО: за всё время в базе 1648 тренингов, за
 * месяц — меньше двухсот, они уже загружены одним запросом, и отдельная
 * агрегирующая ручка означала бы второй круг запросов ради арифметики, которую
 * браузер делает мгновенно.
 *
 * Охват (`covered_count` / `audience_count`) приходит с сервера и считается за
 * ВСЁ время, а не за месяц: раскатка информационной темы пачками идёт неделями
 * и месяц не заканчивает. Поэтому у корпоративной темы два разных числа —
 * «сколько занятий в этом месяце» и «сколько людей охвачено всего», и в
 * карточке они подписаны по-разному.
 */
export function buildTopicSummaries({ trainings = [], topics = [], archivedReasons = [] }) {
    const archived = new Set(archivedReasons);
    const byKey = new Map();

    const ensure = (key, seed) => {
        if (!byKey.has(key)) byKey.set(key, { key, sessions: [], operatorIds: new Set(), minutes: 0, ...seed });
        return byKey.get(key);
    };

    // Корпоративные темы попадают в список ВСЕГДА, даже если в этом месяце по
    // ним не проводили: у темы с нулевым охватом главное действие — «провести
    // пачке», и спрятать её означало бы спрятать саму работу.
    topics.forEach((topic) => {
        ensure(`topic:${topic.id}`, {
            family: FAMILY_CORPORATE,
            topic,
            title: topic.title,
            kind: topic.kind,
            isArchivedTopic: !!topic.is_archived,
            departmentId: topic.department_id,
            departmentName: topic.department_name,
            coveredCount: Number(topic.covered_count) || 0,
            audienceCount: Number(topic.audience_count) || 0,
            totalSessions: Number(topic.session_count) || 0,
            lastDate: topic.last_date,
        });
    });

    trainings.forEach((training) => {
        const topicId = training?.topic_id;
        const key = topicId ? `topic:${topicId}` : `reason:${training?.reason || '—'}`;
        // Корпоративный тренинг, чья тема не пришла в справочнике (у чужого
        // отдела или в архиве) — не теряем: показываем под своим названием как
        // базовую строку, иначе занятие исчезло бы из месяца бесследно.
        const bucket = ensure(key, topicId ? {
            family: FAMILY_CORPORATE,
            topic: null,
            title: training?.reason || 'Без названия',
            kind: 'info',
            coveredCount: 0,
            audienceCount: 0,
            totalSessions: 0,
            lastDate: null,
        } : {
            family: FAMILY_BASE,
            title: training?.reason || '—',
            isArchivedReason: archived.has(training?.reason),
        });
        bucket.sessions.push(training);
        if (Number.isFinite(Number(training?.operator_id))) bucket.operatorIds.add(Number(training.operator_id));
        bucket.minutes += durationMinutes(training);
    });

    return Array.from(byKey.values()).map((bucket) => ({
        ...bucket,
        monthSessions: bucket.sessions.length,
        monthOperators: bucket.operatorIds.size,
        monthMinutes: bucket.minutes,
        monthLastDate: bucket.sessions.reduce(
            (acc, item) => (!acc || String(item?.date) > acc ? item?.date : acc), null),
        // «В часы не идёт» — свойство записи, а не темы: базовую тему можно
        // провести и без зачёта в часы. Показываем флаг, только если ВСЕ
        // занятия месяца единодушны, иначе это была бы полуправда.
        allCounted: bucket.sessions.length > 0 && bucket.sessions.every((item) => item?.count_in_hours !== false),
        noneCounted: bucket.sessions.length > 0 && bucket.sessions.every((item) => item?.count_in_hours === false),
    }));
}

/* Сортировка карточек тем: сначала то, где есть незакрытая работа. */
export function sortTopicSummaries(items) {
    return items.slice().sort((left, right) => {
        const leftRemaining = remainingCount(left);
        const rightRemaining = remainingCount(right);
        // Корпоративные с незакрытым охватом — наверх: это единственное место
        // раздела, где карточка требует действия.
        if ((leftRemaining > 0) !== (rightRemaining > 0)) return leftRemaining > 0 ? -1 : 1;
        if (left.monthSessions !== right.monthSessions) return right.monthSessions - left.monthSessions;
        return String(left.title).localeCompare(String(right.title), 'ru', { sensitivity: 'base' });
    });
}

export function remainingCount(summary) {
    if (summary?.family !== FAMILY_CORPORATE) return 0;
    if (summary?.isArchivedTopic) return 0;
    const audience = Number(summary?.audienceCount) || 0;
    const covered = Number(summary?.coveredCount) || 0;
    return Math.max(0, audience - covered);
}

export function coveragePercent(summary) {
    const audience = Number(summary?.audienceCount) || 0;
    if (audience <= 0) return null;
    const covered = Math.min(Number(summary?.coveredCount) || 0, audience);
    return Math.round((covered / audience) * 100);
}

/* ── Группировка для вкладки «По группам» ───────────────────────────────── */

export const NO_GROUP_KEY = 'none';

/* Группа берётся из самой записи тренинга (`group_id`/`group_name`) — сервер
 * считает её НА ДАТУ ТРЕНИНГА. Не из текущего членства: 120 тренингов из 1648
 * принадлежат людям без открытого членства (в основном уволенным), и по
 * текущей группе они все свалились бы в «Без группы». */
export function buildGroupBuckets(trainings = []) {
    const buckets = new Map();
    trainings.forEach((training) => {
        const id = training?.group_id ?? null;
        const key = id == null ? NO_GROUP_KEY : String(id);
        if (!buckets.has(key)) {
            buckets.set(key, {
                key,
                groupId: id,
                name: id == null ? 'Без группы' : (training?.group_name || `Группа ${id}`),
                trainings: [],
                operators: new Map(),
                minutes: 0,
            });
        }
        const bucket = buckets.get(key);
        bucket.trainings.push(training);
        bucket.minutes += durationMinutes(training);
        const operatorId = Number(training?.operator_id);
        if (Number.isFinite(operatorId)) {
            if (!bucket.operators.has(operatorId)) {
                bucket.operators.set(operatorId, {
                    id: operatorId,
                    name: training?.operator_name || `#${operatorId}`,
                    status: training?.operator_status || null,
                    trainings: [],
                    minutes: 0,
                });
            }
            const person = bucket.operators.get(operatorId);
            person.trainings.push(training);
            person.minutes += durationMinutes(training);
        }
    });

    return Array.from(buckets.values())
        .map((bucket) => ({
            ...bucket,
            people: Array.from(bucket.operators.values())
                .sort((a, b) => String(a.name).localeCompare(String(b.name), 'ru', { sensitivity: 'base' })),
        }))
        .sort((left, right) => {
            // «Без группы» всегда последней: это остаток, а не группа.
            if ((left.key === NO_GROUP_KEY) !== (right.key === NO_GROUP_KEY)) {
                return left.key === NO_GROUP_KEY ? 1 : -1;
            }
            return String(left.name).localeCompare(String(right.name), 'ru', { sensitivity: 'base' });
        });
}

/* Стабильная плитка-монограмма для темы или группы.
 *
 * Тот же приём и та же палитра, что у очередей в «Обращениях»: цвет выбирается
 * по строке, а не по порядку в списке, — иначе он менялся бы от фильтра. Цвет
 * здесь не несёт смысла, он только помогает глазу зацепиться за строку, поэтому
 * оттенки намеренно бледные и одинаковой насыщенности. */
const TILE_TONES = [
    'bg-blue-50 text-blue-600 ring-blue-100',
    'bg-violet-50 text-violet-600 ring-violet-100',
    'bg-teal-50 text-teal-600 ring-teal-100',
    'bg-orange-50 text-orange-600 ring-orange-100',
    'bg-cyan-50 text-cyan-600 ring-cyan-100',
    'bg-indigo-50 text-indigo-600 ring-indigo-100',
];

export function tileTone(seed) {
    const text = String(seed || '');
    let hash = 0;
    for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) % 100000;
    return TILE_TONES[hash % TILE_TONES.length];
}

export function initials(title) {
    const words = String(title || '').trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) return '—';
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return (words[0][0] + words[1][0]).toUpperCase();
}

export const errText = (error, fallback) => (
    error?.response?.data?.error || error?.message || fallback
);
