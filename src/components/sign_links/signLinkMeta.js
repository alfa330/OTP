/* Подписи и цвета раздела «Ссылка на подписание».
 *
 * Коды исходов — те же, что пишет в журнал бэкенд (sign_links/schema.OUTCOMES).
 * Второй словарь неизбежен (питон не читает js), поэтому набор кодов сторожит
 * тест: разойдись они, админ увидел бы в журнале код вместо слова.
 *
 * ЦВЕТ ТОЛЬКО СО СМЫСЛОМ. Зелёным помечена выданная ссылка — единственный
 * исход, ради которого раздел существует. «Документов нет» — нейтральный
 * ответ водителю, его не красим. Отказы и сбои — розовым: это то, что админу
 * стоит заметить в журнале.
 */

export const OUTCOME_LABELS = {
    link: 'Ссылка выдана',
    no_documents: 'Документов нет',
    rejected: 'Отказ сервиса',
    unavailable: 'Сервис не ответил',
    invalid: 'ИИН не прошёл проверку',
    limit: 'Дневной предел',
};

export const OUTCOME_TONE = {
    link: 'green',
    no_documents: 'slate',
    rejected: 'red',
    unavailable: 'red',
    invalid: 'amber',
    limit: 'amber',
};

/* Порядок — от «всё хорошо» к «ничего не вышло»; таким же он стоит в фильтре. */
export const OUTCOME_ORDER = ['link', 'no_documents', 'rejected', 'unavailable', 'invalid', 'limit'];

export const DEPARTMENT_LABELS = {
    szov: 'СЗоВ',
    front_office: 'Фронт-офисы',
    op: 'Отдел продаж',
};

/* Запросы внешнего сервиса (вход по ключу, sign_links/external.py) лежат в том
   же журнале. Сотрудника у такой строки нет, вместо роли стоит API_ROLE —
   двойник sign_links/queries.API_ROLE, набор сторожит тест. */
export const API_ROLE = 'api';
export const isApiRow = (item) => String(item?.user_role || '') === API_ROLE;

/* Кто спрашивал, если это внешний сервис. В журнале он записан одной строкой
   «Внешний сервис (ключ 6f899aa2)» (queries.API_CLIENT_NAME) — здесь она
   расходится на имя и отпечаток ключа, чтобы встать в две строки, как имя и
   роль у сотрудника. Строка другого вида показывается как есть. */
const API_CLIENT_RE = /^(.+?)\s*\((ключ [0-9a-f]+)\)$/;
export const apiClient = (item) => {
    const raw = String(item?.user_name || '').trim();
    const match = API_CLIENT_RE.exec(raw);
    return match
        ? { name: match[1], note: match[2] }
        : { name: raw || SOURCE_LABELS.api, note: 'вход по ключу' };
};

/* Отбор «Источник»: кто спрашивал. Ключи — значения параметра source у журнала
   (queries.SOURCES); «все» параметром не уходит. */
export const SOURCE_LABELS = {
    all: 'Все источники',
    staff: 'Сотрудники',
    api: 'Внешний сервис',
};

export const SOURCE_ORDER = ['all', 'staff', 'api'];

export const outcomeLabel = (code) => OUTCOME_LABELS[code] || code || '—';
export const outcomeTone = (code) => OUTCOME_TONE[code] || 'slate';
export const departmentLabel = (code) => DEPARTMENT_LABELS[String(code || '').toLowerCase()] || code || '—';

/* Заметно ли уже, что дневной предел близко. Порог — десяток: обычный день
   фронт-офиса до него не доходит, а тот, кто дошёл, узнаёт заранее, а не из
   отказа. */
export const LIMIT_WARNING_LEFT = 10;

export const limitWarning = (limits) => {
    if (!limits || typeof limits.left_today !== 'number') return null;
    if (limits.left_today > LIMIT_WARNING_LEFT) return null;
    if (limits.left_today <= 0) return 'Дневной предел запросов исчерпан — новые ссылки можно получить завтра';
    return `Сегодня осталось ${limits.left_today} ${pluralRequests(limits.left_today)}`;
};

export const pluralRequests = (n) => {
    const abs = Math.abs(Number(n) || 0) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return 'запросов';
    if (last === 1) return 'запрос';
    if (last >= 2 && last <= 4) return 'запроса';
    return 'запросов';
};

/* Домен ссылки без «www.» — для подписи «ссылка на …». Самой ссылки в журнале
   нет, домен — единственное, что о ней известно. */
export const hostLabel = (host) => String(host || '').replace(/^www\./i, '');
