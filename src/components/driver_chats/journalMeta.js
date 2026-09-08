/* Подписи и форматирование журнала раздела «Чаты водителей» (задача #271).
 *
 * Двойник на бэкенде — driver_chats/report.py: те же коды, те же слова. Второй
 * словарь неизбежен (питон не читает js), поэтому его сторожит тест: разойдись
 * подписи, человек увидел бы в выгрузке не то слово, что на экране.
 *
 * ЧЕСТНОСТЬ ФОРМУЛИРОВОК. «Открыл переписку», а не «Сделал скриншот»: система
 * видит факт открытия чата, а не нажатие Cmd+Shift+4. Снимок экрана делается
 * средствами операционной системы и не наблюдаем в принципе — называть одно
 * другим в журнале, по которому потом разбирают утечку, нельзя.
 */

export const KIND_LABELS = {
    search: 'Искал номер',
    open: 'Открыл переписку',
    handoff: 'Передал чат-менеджеру',
};

export const ROLE_LABELS = {
    operator: 'Оператор',
    trainee: 'Стажёр',
    sv: 'Супервайзер',
    supervisor: 'Супервайзер',
    admin: 'Админ',
    super_admin: 'Супер-админ',
    trainer: 'Тренер',
};

/* Цвет — только там, где он несёт смысл. «Передал» — единственное действие,
 * которое меняет чужую систему и которое нельзя отозвать; искал и открыл —
 * нейтральные, их не красим вовсе. */
export const KIND_TONE = {
    search: 'slate',
    open: 'slate',
    handoff: 'blue',
};

export const kindLabel = (kind) => KIND_LABELS[kind] || kind || '—';
export const roleLabel = (role) => ROLE_LABELS[role] || role || '—';

/* Телефон читается группами, как его диктуют вслух: 8 776 003 44 05. */
export const formatPhone = (value) => {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length !== 11) return value || '—';
    return `8 ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7, 9)} ${digits.slice(9)}`;
};

export const formatTime = (iso) => {
    if (!iso) return '';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '';
    return parsed.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

/* «Сегодня, 8 сентября», «Вчера, 7 сентября», «5 сентября, суббота» — заголовок
 * дня в журнале. Разделитель по дням заменил дату в каждой строке: в ленте за
 * неделю дата повторялась 50 раз подряд, а нужна она ровно там, где меняется. */
export const formatDayFull = (iso, today = todayISO()) => {
    if (!iso) return '';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '';
    const day = parsed.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
    const own = dayKeyOf(parsed);
    if (own === today) return `Сегодня, ${day}`;
    if (own === shiftDaysBack(today, 1)) return `Вчера, ${day}`;
    const weekday = parsed.toLocaleDateString('ru-RU', { weekday: 'long' });
    return `${day}, ${weekday}`;
};

/* Сутки события — по МЕСТНОМУ времени браузера, как их и показывает строка:
 * `toISOString().slice(0,10)` дал бы UTC, и всё, что произошло до 05:00 по
 * Алматы, уехало бы во вчерашнюю группу. */
export const dayKeyOf = (value) => {
    const parsed = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(parsed.getTime())) return '';
    const p = (n) => String(n).padStart(2, '0');
    return `${parsed.getFullYear()}-${p(parsed.getMonth() + 1)}-${p(parsed.getDate())}`;
};

/* «1 чат», «2 чата», «5 чатов». Число парков у водителя доходит до девяти, и
 * «5 чата» в шапке списка читалось бы как опечатка портала. */
export const pluralChats = (count) => {
    const value = Math.abs(Number(count) || 0);
    const tail = value % 100;
    if (tail >= 11 && tail <= 14) return `${value} чатов`;
    const last = value % 10;
    if (last === 1) return `${value} чат`;
    if (last >= 2 && last <= 4) return `${value} чата`;
    return `${value} чатов`;
};

export const formatDayShort = (iso) => {
    if (!iso) return '';
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return '';
    return parsed.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
};

/* ── Период выгрузки ─────────────────────────────────────────────────────────
 *
 * Потолок тот же, что на сервере (`EXPORT_MAX_DAYS` в driver_chats/report.py),
 * их сверяет тест. Здесь он нужен, чтобы «Подтвердить» гасло ДО запроса, а не
 * после ожидания: гасить кнопку — удобство, границей служит сервер.
 *
 * Экран журнала потолком НЕ ограничен: смотреть за квартал можно, нельзя лишь
 * собрать его одним файлом. */
export const EXPORT_MAX_DAYS = 30;

// Сегодня по Алматы. У сотрудника в браузере может стоять любая зона, а журнал
// пишется по времени Алматы — без приведения «сегодня» уезжало бы на сутки.
export const todayISO = () => {
    const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Almaty', year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(new Date());
    const get = (type) => parts.find((part) => part.type === type)?.value || '';
    return `${get('year')}-${get('month')}-${get('day')}`;
};

/* Разбор ISO-даты с проверкой, а не только по маске: `Date.UTC(2026, 12, 40)`
   молча превращается в февраль следующего года, и «13-й месяц» дал бы не
   прочерк, а какое-то число дней. Сверяем компоненты с тем, что получилось. */
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

/* Сутки периода, обе границы включительно: «с 1 по 1» — это одни сутки, а не
   ноль. Сервер считает так же (`_export_period` в driver_chats/routes.py).
   Ноль означает «период не выбран» — на нём «Подтвердить» гаснет. */
export const rangeDays = (from, to) => {
    const a = dateParts(from);
    const b = dateParts(to);
    if (!a || !b) return 0;
    return Math.round(Math.abs(b.stamp - a.stamp) / 86400000) + 1;
};

/* Начало окна длиной `days + 1` суток, заканчивающегося указанным днём. Считаем
   в UTC от разобранной даты, а не `new Date()` минус дни: перевод часов и пояс
   браузера иначе дают сдвиг на сутки. */
export const shiftDaysBack = (iso, days) => {
    const parts = dateParts(iso);
    if (!parts) return iso;
    const moved = new Date(parts.stamp - days * 86400000);
    const p = (n) => String(n).padStart(2, '0');
    return `${moved.getUTCFullYear()}-${p(moved.getUTCMonth() + 1)}-${p(moved.getUTCDate())}`;
};

/* «31 день», «32 дня», «45 дней». Двойник `plural_days` из report.py.
   Нужен именно он, а не «N суток»: у «суток» нет формы единственного числа, и
   «31 суток» — не по-русски. */
export const pluralDays = (count) => {
    const value = Math.abs(Number(count) || 0);
    const tail = value % 100;
    if (tail >= 11 && tail <= 14) return `${value} дней`;
    const last = value % 10;
    if (last === 1) return `${value} день`;
    if (last >= 2 && last <= 4) return `${value} дня`;
    return `${value} дней`;
};

const ruDate = (value) => {
    if (!value) return '—';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleDateString('ru-RU', {
        day: '2-digit', month: '2-digit', year: 'numeric',
    });
};

/* Имя файла выгрузки. Двойник на бэкенде — report.export_file_name: заголовок
 * Content-Disposition до фронта не доходит (его нет в
 * Access-Control-Expose-Headers), поэтому имя собирается с двух сторон и обязано
 * совпадать. */
export const exportFileName = (periodFrom, periodTo) => {
    const left = ruDate(periodFrom);
    const right = ruDate(periodTo);
    if (left === '—' && right === '—') return 'Журнал чатов водителей.xlsx';
    if (left === right) return `Журнал чатов водителей ${left}.xlsx`;
    return `Журнал чатов водителей ${left} — ${right}.xlsx`;
};
