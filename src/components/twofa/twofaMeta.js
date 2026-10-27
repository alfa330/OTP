/*
 * Раздел «Коды 2FA» (#373): время, статусы, вкладки — без React, чтобы проверять тестом.
 *
 * Время приходит с сервера строкой времени Алматы с явным «+05:00» (twofa/rules.py: iso).
 * Часы и минуты берём из самой строки, а не через Date: у сотрудника в командировке
 * браузер показал бы время своего пояса, а в Telegram и в журнале — алматинское.
 */

export const clockOf = (iso, seconds = false) => {
    const match = /T(\d{2}):(\d{2}):(\d{2})/.exec(String(iso || ''));
    if (!match) return '';
    return seconds ? `${match[1]}:${match[2]}:${match[3]}` : `${match[1]}:${match[2]}`;
};

export const dateOf = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return match ? `${match[3]}.${match[2]}` : '';
};

/* «14:31» сегодня, «26.10 14:31» — в другой день (сравниваем с временем сервера). */
export const whenOf = (iso, serverIso) => {
    const day = String(iso || '').slice(0, 10);
    const today = String(serverIso || '').slice(0, 10);
    const clock = clockOf(iso);
    if (!clock) return '';
    return day && today && day !== today ? `${dateOf(iso)} ${clock}` : clock;
};

/* Тон статуса: ждёт — янтарный (горит), подтверждён — зелёный, остальное — серое.
   Цвет только у того, что требует внимания или говорит «готово». */
export const STATUS_TONES = {
    pending: 'amber',
    approved: 'green',
    rejected: 'slate',
    expired: 'slate',
    cancelled: 'slate',
};

export const toneOfStatus = (status) => STATUS_TONES[status] || 'slate';

/* Последний запрос сотрудника по каждому аккаунту: по нему карточка аккаунта говорит,
   что происходит сейчас («ждёт подтверждения», «код отправлен»). Список приходит от
   новых к старым — первый встреченный и есть последний. */
export const latestByAccount = (requests) => {
    const result = {};
    (requests || []).forEach((request) => {
        if (request && result[request.account_id] === undefined) result[request.account_id] = request;
    });
    return result;
};

/* Строка состояния под аккаунтом у сотрудника. null — показывать нечего. */
export const accountStateLine = (request, serverIso) => {
    if (!request) return null;
    const at = (iso) => whenOf(iso, serverIso);
    if (request.status === 'pending') {
        return { tone: 'amber', text: `Ждёт подтверждения главы · сгорит в ${clockOf(request.expires_at)}` };
    }
    if (request.status === 'approved') {
        if (request.delivery_error) return { tone: 'red', text: `Код не дошёл до Telegram: ${request.delivery_error}` };
        return { tone: 'green', text: `Код отправлен в Telegram · ${at(request.decided_at)}` };
    }
    if (request.status === 'rejected') return { tone: 'slate', text: `Запрос отклонён · ${at(request.decided_at)}` };
    if (request.status === 'expired') return { tone: 'slate', text: 'Запрос сгорел: за 10 минут его не подтвердили' };
    if (request.status === 'cancelled') return { tone: 'slate', text: 'Запрос отменён' };
    return null;
};

/* Вкладки по правам: сотруднику — его коды; главе и супер-админу — работа раздела.
   Если глава сам получил доступ к аккаунту, у него есть и «Мои коды». */
export const tabsFor = (capabilities) => {
    const caps = capabilities || {};
    const tabs = [];
    if (caps.can_manage) tabs.push({ value: 'requests', label: 'Запросы' });
    if (caps.can_request) tabs.push({ value: 'codes', label: caps.can_manage ? 'Мои коды' : 'Коды' });
    if (caps.can_manage) {
        tabs.push({ value: 'accounts', label: 'Аккаунты' });
        tabs.push({ value: 'people', label: 'Доступы' });
        tabs.push({ value: 'journal', label: 'Журнал' });
    }
    return tabs;
};

export const plural = (count, one, few, many) => {
    const n = Math.abs(Number(count) || 0) % 100;
    const last = n % 10;
    if (n > 10 && n < 20) return many;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

export const peopleCount = (count) => `${count} ${plural(count, 'сотрудник', 'сотрудника', 'сотрудников')}`;

export const accountsCount = (count) => `${count} ${plural(count, 'аккаунт', 'аккаунта', 'аккаунтов')}`;

/* Порядок проверки формы добавления: первое, чего не хватает, — туда и прокрутка. */
export const manualErrors = ({ title, secret }) => {
    const errors = {};
    if (!String(title || '').trim()) errors.title = 'Укажите название';
    if (!String(secret || '').trim()) errors.secret = 'Вставьте ключ из настроек 2FA сервиса';
    return errors;
};

export const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;
