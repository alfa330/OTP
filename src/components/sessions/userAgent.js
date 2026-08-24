/**
 * Разбор user-agent для раздела «Сессии».
 *
 * Те же правила лежат в SQL (`Database._active_session_device_sql`): по ним
 * считаются плашки устройств и фильтр. Здесь — только показ строки в таблице и
 * в карточке. Порядок проверок обязан совпадать с серверным, иначе плашка
 * «Планшет 12» приведёт к списку из телефонов.
 */

const BOT_RE = /bot|crawl|spider|slurp|bingpreview|facebookexternalhit|linkedinbot|twitterbot/;
const TABLET_RE = /(ipad|tablet|kindle|playbook|silk)|(android(?!.*mobile))|(windows(?!.*phone)(.*touch))|(puffin(?!.*(ip|ap|wp)))/;
const MOBILE_RE = /mobi|android|iphone|ipod|blackberry|iemobile|opera mini|windows phone/;

export const DEVICE_LABELS = {
    desktop: 'ПК',
    mobile: 'Телефон',
    tablet: 'Планшет',
    bot: 'Бот',
    unknown: 'Неизвестно'
};

export function parseUserAgent(ua) {
    if (!ua) return { type: 'unknown', os: '—', browser: '—', label: 'Неизвестно' };
    const u = String(ua).toLowerCase();

    const isBot = BOT_RE.test(u);
    const isTablet = !isBot && TABLET_RE.test(u);
    const isMobile = !isBot && !isTablet && MOBILE_RE.test(u);
    const type = isBot ? 'bot' : isTablet ? 'tablet' : isMobile ? 'mobile' : 'desktop';

    let os = '—';
    if (/windows nt 10/.test(u)) os = 'Windows 10/11';
    else if (/windows nt 6\.3/.test(u)) os = 'Windows 8.1';
    else if (/windows nt 6\.1/.test(u)) os = 'Windows 7';
    else if (/windows/.test(u)) os = 'Windows';
    else if (/ipad/.test(u)) os = 'iPadOS';
    else if (/iphone|ipod/.test(u)) os = 'iOS';
    else if (/mac os x/.test(u)) {
        const v = u.match(/mac os x (\d+[._]\d+)/);
        os = v ? `macOS ${v[1].replace('_', '.')}` : 'macOS';
    } else if (/android/.test(u)) {
        const v = u.match(/android (\d+(\.\d+)?)/);
        os = v ? `Android ${v[1]}` : 'Android';
    } else if (/linux/.test(u)) os = 'Linux';
    else if (/chromeos|cros/.test(u)) os = 'ChromeOS';

    let browser = '—';
    if (/edg\/|edge\//.test(u)) browser = 'Edge';
    else if (/opr\/|opera/.test(u)) browser = 'Opera';
    else if (/yabrowser/.test(u)) browser = 'Яндекс';
    else if (/firefox\//.test(u)) browser = 'Firefox';
    else if (/chrome\//.test(u) && !/chromium/.test(u)) browser = 'Chrome';
    else if (/chromium/.test(u)) browser = 'Chromium';
    else if (/safari\//.test(u) && !/chrome/.test(u)) browser = 'Safari';
    else if (/msie|trident/.test(u)) browser = 'IE';
    else if (isBot) browser = 'Bot';

    const label = [DEVICE_LABELS[type], os !== '—' ? os : null, browser !== '—' ? browser : null]
        .filter(Boolean)
        .join(' · ');

    return { type, os, browser, label };
}

export const ROLE_META = {
    admin: { label: 'Админ', cls: 'bg-violet-100 text-violet-700 ring-violet-200' },
    sv: { label: 'Супервайзер', cls: 'bg-blue-100 text-blue-700 ring-blue-200' },
    operator: { label: 'Оператор', cls: 'bg-emerald-100 text-emerald-700 ring-emerald-200' }
};

export function roleLabel(role) {
    return ROLE_META[role === 'super_admin' ? 'admin' : role]?.label || role || '—';
}

/* Русская форма числительного. Без неё раздел писал бы «42 сессий» и «2 адрес». */
export function plural(count, one, few, many) {
    const abs = Math.abs(Number(count) || 0) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last > 1 && last < 5) return few;
    if (last === 1) return one;
    return many;
}

export const sessionWord = (n) => plural(n, 'сессия', 'сессии', 'сессий');
export const personWord = (n) => plural(n, 'сотрудник', 'сотрудника', 'сотрудников');
export const addressWord = (n) => plural(n, 'адрес', 'адреса', 'адресов');
