/*
 * Окно Syntony в iCORE — чистая логика экрана (без React): какое окно просить,
 * что показать оператору по коду ошибки, годится ли ссылка в src фрейма.
 *
 * Портированная механика помощника Syntony (syntony-chat.js из ТЗ от 09.11.2026):
 * ссылку каждый раз просим заново у НАШЕГО сервера, в теле — только scope, phones и
 * theme; оператора (id, ФИО, почту) сервер берёт из сессии. Сам помощник взять как
 * есть нельзя: он ходит на свой же origin куками, а API портала живёт на другом
 * адресе и пускает по заголовку доступа.
 *
 * Ошибку разбираем по error.code, а не по HTTP-статусу и не по error.message
 * (ТЗ, задача 5): текст Syntony может меняться, тексты оператору — здесь.
 */

export const SCOPE_INBOX = 'inbox';
export const SCOPE_LEADS = 'leads';
export const SCOPE_PHONE = 'phone';

/* Доска заявок сети помещается от 768 px (ТЗ, раздел 1): уже — Syntony покажет
   заглушку «не помещается в узкое окно». Контейнер «Заявок» держит эту ширину сам. */
export const LEADS_MIN_WIDTH = 768;

export const SECTIONS = Object.freeze({
    syntony_chats: { scope: SCOPE_INBOX, title: 'Чаты Syntony' },
    syntony_leads: { scope: SCOPE_LEADS, title: 'Заявки Syntony', minWidth: LEADS_MIN_WIDTH },
});

const GENERAL_TEXT = 'Не удалось открыть окно Syntony. Попробуйте ещё раз.';
const ADMIN_TEXT = 'Окно Syntony временно недоступно, обратитесь к администратору.';

/* Что показать оператору (ТЗ, раздел 7). admin — причина на стороне ключа или
   подключения: оператору её не исправить, поэтому под текстом остаётся код для того, кто
   будет писать в Syntony. «Открыть заново» экран предлагает при ЛЮБОЙ ошибке: открытое окно
   живёт, пока человек в портале, и после того как причину устранили (выдали доступ,
   связали оператора, починили ключ), другого способа запросить ссылку у него нет. */
export const ERROR_VIEWS = Object.freeze({
    // Syntony
    invalid_request: { text: GENERAL_TEXT },
    invalid_key: { text: ADMIN_TEXT, admin: true },
    key_revoked: { text: ADMIN_TEXT, admin: true },
    key_expired: { text: ADMIN_TEXT, admin: true },
    insufficient_scope: { text: ADMIN_TEXT, admin: true },
    connection_inactive: { text: ADMIN_TEXT, admin: true },
    embed_disabled: { text: ADMIN_TEXT, admin: true },
    operator_not_linked: {
        text: 'Ваша учётная запись ещё не подключена к Syntony. Обратитесь к администратору.',
    },
    transport_not_connected: {
        text: 'Этот канал Syntony не ведёт — переписка осталась в прежнем инструменте.',
    },
    rate_limited: { text: 'Слишком много запросов к Syntony. Повторите немного позже.' },
    operator_lookup_unavailable: { text: 'Syntony сейчас не отвечает. Попробуйте через минуту.' },
    // iCORE (syntony_window/rules.py: ICORE_ERROR_CODES)
    icore_no_access: { text: 'Окно Syntony вам не открыто.' },
    icore_qr_required: { text: 'Окно Syntony открывается после подтверждения QR-кода старшим.' },
    icore_not_configured: { text: 'Окно Syntony ещё не подключено. Обратитесь к администратору.', admin: true },
    icore_unreachable: { text: 'Syntony сейчас не отвечает. Попробуйте через минуту.' },
});

export const KNOWN_ERROR_CODES = Object.freeze(Object.keys(ERROR_VIEWS));

/* Ошибка из ответа сервера → { code, message, retryAfter }.
   Ответ окна — { error: { code, message, details } }; общий отказ портала (сессия,
   500) — { error: 'строка' } или вовсе без тела: тогда кода нет. */
export const readError = (data) => {
    const error = data && typeof data === 'object' ? data.error : null;
    if (error && typeof error === 'object') {
        const retryAfter = Number(error.details?.retryAfter);
        return {
            code: typeof error.code === 'string' ? error.code.trim() : '',
            message: typeof error.message === 'string' ? error.message : '',
            retryAfter: Number.isFinite(retryAfter) && retryAfter > 0 ? Math.ceil(retryAfter) : null,
        };
    }
    return { code: '', message: '', retryAfter: null };
};

/* То же — тому, кто ведёт список доступа (супер-админ): не «обратитесь к
   администратору», а что именно случилось и кому писать (ТЗ, раздел 7, «что сделать»). */
const MANAGER_TEXTS = Object.freeze({
    invalid_key: 'Syntony не приняла ключ: он неверный. Проверьте ключ на сервере iCORE и напишите в Syntony.',
    key_revoked: 'Ключ Syntony отозван. Нужен новый ключ — напишите в Syntony.',
    key_expired: 'Срок ключа Syntony истёк. Нужен новый ключ — напишите в Syntony.',
    insufficient_scope: 'У ключа Syntony нет права открывать окно — напишите в Syntony.',
    connection_inactive: 'Подключение приостановлено на стороне Syntony — напишите в Syntony.',
    embed_disabled: 'Окно выключено на стороне Syntony — напишите в Syntony.',
    icore_not_configured: 'Окно ещё не подключено: на сервере iCORE не задан ключ Syntony.',
    icore_no_access: 'Вас нет в списке доступа. Добавьте себя кнопкой «Доступ» — и окно откроется.',
    operator_not_linked: 'Ваша учётная запись ещё не подключена к Syntony: её администратор связывает операторов по почте из списка «Доступ».',
});

/* Строка над списком доступа: что Syntony ответила на запись или проверку оператора,
   когда ответ не про самого оператора (ключ, пауза, сбой). '' — сказать нечего. */
export const managerNote = (code, message = '') => {
    if (!code) return '';
    if (MANAGER_TEXTS[code]) return MANAGER_TEXTS[code];
    if (code === 'invalid_request') {
        return `Syntony не приняла запрос${message ? `: ${message}` : ''}. Проверьте ФИО и почту сотрудника.`;
    }
    if (ERROR_VIEWS[code]) return ERROR_VIEWS[code].text;
    return `Syntony ответила незнакомой ошибкой (${code})${message ? `: ${message}` : ''}.`;
};

const secondsWord = (count) => {
    const tail = count % 100;
    const last = count % 10;
    if (tail >= 11 && tail <= 14) return 'секунд';
    if (last === 1) return 'секунду';
    if (last >= 2 && last <= 4) return 'секунды';
    return 'секунд';
};

/* Экран ошибки: { code, text, note }.
   manager — человек ведёт список доступа (супер-админ): ему вместо «обратитесь к
   администратору» говорим, что сделать самому. Незнакомый код — общий текст (ТЗ). */
export const describeError = (error, { manager = false } = {}) => {
    const code = error?.code || '';
    const view = ERROR_VIEWS[code];
    if (!view) return { code, text: GENERAL_TEXT, note: code ? `Код: ${code}` : '' };
    let text = view.text;
    if (code === 'rate_limited' && error.retryAfter) {
        text = `Слишком много запросов к Syntony. Повторите через ${error.retryAfter} ${secondsWord(error.retryAfter)}.`;
    }
    if (manager && MANAGER_TEXTS[code]) text = MANAGER_TEXTS[code];
    return { code, text, note: view.admin ? `Код: ${code}` : '' };
};

/* Тело запроса «дай окно». Оператора и canWrite сюда не кладём — их нет в договоре. */
export const windowRequestBody = ({ scope, phones, theme }) => {
    const body = { scope: scope || SCOPE_INBOX, theme: theme === 'dark' ? 'dark' : 'light' };
    if (Array.isArray(phones) && phones.length) body.phones = phones;
    return body;
};

/* Ссылка окна годится в src фрейма: только https и только домен Syntony. Сервер
   проверяет то же (syntony_window/client.py); здесь — вторая линия: src фрейма
   не должен получить ни javascript:, ни посторонний адрес. */
export const isWindowUrl = (value) => {
    if (typeof value !== 'string') return false;
    let url;
    try {
        url = new URL(value);
    } catch (error) {
        return false;
    }
    if (url.protocol !== 'https:' || url.username || url.password) return false;
    const host = url.hostname.toLowerCase();
    return host === 'syntony.kz' || host.endsWith('.syntony.kz');
};

/* Тема окна — по теме портала в момент открытия (ТЗ, задача 7). */
export const portalTheme = (root = globalThis.document?.documentElement) => (
    root?.getAttribute?.('data-otp-theme') === 'dark' ? 'dark' : 'light'
);

/* Номера водителя для окна переписки: чат, CRM — без повторов (тот же номер в
   другой записи сверяется по последним десяти цифрам), не больше десяти. Номер
   уходит как записан («+7 701…», «8701…»): Syntony принимает любой вид, а
   дописанный здесь «+» превратил бы «8701…» в несуществующий код страны. */
export const driverPhones = (...candidates) => {
    const phones = [];
    const seen = new Set();
    candidates.flat().forEach((candidate) => {
        const text = String(candidate ?? '').trim().replace(/\s+/g, ' ');
        if (!/^[0-9+()\- ]+$/.test(text)) return;
        const digits = text.replace(/\D/g, '');
        if (digits.length < 10 || digits.length > 15) return;
        const key = digits.slice(-10);
        if (seen.has(key) || phones.length >= 10) return;
        seen.add(key);
        phones.push(text);
    });
    return phones;
};

// ── Список доступа ───────────────────────────────────────────────────────────

export const LINK_VIEWS = Object.freeze({
    linked: { label: 'Подключён', tone: 'green' },
    not_linked: { label: 'Ждёт связи', tone: 'amber' },
    unknown: { label: 'Не проверено', tone: 'slate' },
});

/* Состояние строки списка: что с человеком со стороны Syntony и что мешает. */
export const accessRowState = (item) => {
    if (!item?.working) return { label: 'Уволен', tone: 'red', hint: 'Окно ему уже не открыть. Снимите доступ и попросите Syntony снять связь.' };
    if (!item.email) return { label: 'Нет почты', tone: 'red', hint: 'По почте Syntony находит сотрудника. Укажите адрес его учётной записи Syntony.' };
    const view = LINK_VIEWS[item.link_state] || LINK_VIEWS.unknown;
    const hint = item.link_state === 'not_linked'
        ? 'Оператор записан у Syntony. Дальше его связывает администратор Syntony — по этой почте.'
        : '';
    return { ...view, hint };
};

const EMAIL_RE = /^[^\s@<>()[\],;:"\\]+@[^\s@<>()[\],;:"\\]+\.[^\s@<>()[\],;:"\\.]+$/;

/* Почта в поле списка: '' — можно сохранить, иначе текст под полем. Пустое
   значение допустимо: человек остаётся в списке без почты. */
export const emailProblem = (value) => {
    const text = String(value ?? '').trim();
    if (!text) return '';
    return EMAIL_RE.test(text) && text.length <= 255 ? '' : 'Проверьте адрес почты';
};

export const sameEmail = (left, right) => (
    String(left ?? '').trim().toLowerCase() === String(right ?? '').trim().toLowerCase()
);

/* «11.11.2026 14:05» из ISO без пояса (время Алматы, как отдаёт сервер). */
export const formatWhen = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso ?? ''));
    return match ? `${match[3]}.${match[2]}.${match[1]} ${match[4]}:${match[5]}` : '';
};

export const peopleCount = (count) => {
    const tail = count % 100;
    const last = count % 10;
    let word = 'человек';
    if (!(tail >= 11 && tail <= 14) && last >= 2 && last <= 4) word = 'человека';
    return `${count} ${word}`;
};

export const exportFileName = (date = new Date()) => {
    const two = (value) => String(value).padStart(2, '0');
    return `Операторы iCORE для Syntony ${two(date.getDate())}.${two(date.getMonth() + 1)}.${date.getFullYear()}.xlsx`;
};

/* Файл — в этом же окне, временной ссылкой (правило портала: без новых вкладок).
   Ссылка на blob, а не startFileDownload: выгрузку собирает наш API за
   авторизацией, скрытый фрейм без заголовка получил бы отказ. */
export const saveBlob = (blob, name, doc = globalThis.document) => {
    if (!doc?.body || typeof URL === 'undefined' || !URL.createObjectURL) return false;
    const url = URL.createObjectURL(blob);
    const link = doc.createElement('a');
    link.href = url;
    link.download = name;
    doc.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return true;
};
