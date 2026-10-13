/* Чистые помощники страницы «Адреса офисов» для iCORE Phone (offices.html):
 * какое пространство вики открыть, что делать со ссылкой, какой номер копировать по
 * нажатию на телефон, когда обновлять список в фоне, что сказать человеку в каждом
 * состоянии страницы.
 *
 * Без React и без DOM — их читают тесты через node (tests/offices_embed.test.mjs).
 * Общее с тренажёрами (разбор ответа о доступе, сроки QR, паузы повтора) — в
 * src/embed/embedAccess.js.
 */
import { effectiveFeatures } from '../components/wiki/spaceFeatures.js';
import { officePhones } from '../components/wiki/officePhones.js';

// Пауза повтора живёт в общем модуле (её считают и тренажёры); отсюда она видна
// по-прежнему — страница и её тесты берут её здесь.
export { retryDelayMs } from '../embed/embedAccess.js';

/** Пространства, где справочник офисов человеку ВЫДАН: свои (не гостевые — гостю
 *  справочники сервер не отдаёт, wiki/routes_structure.request_space) и с вкладкой
 *  «Офисы» (тумблер пространства; чего нет в features — то включено). */
export const officeSpaces = (spaces) => (Array.isArray(spaces) ? spaces : [])
    .filter((space) => space && space.id != null && !space.guest_only && effectiveFeatures(space).offices);

/** Какое пространство открыть: запомненное, если оно всё ещё в списке, иначе первое
 *  (порядок пространств задаёт сервер). null — справочника офисов у человека нет. */
export const pickOfficesSpace = (spaces, preferredId = null) => {
    const list = officeSpaces(spaces);
    if (!list.length) return null;
    const preferred = Number(preferredId);
    return (preferred && list.find((space) => Number(space.id) === preferred)) || list[0];
};

/* Что делать со ссылкой, на которую нажали:
 *   'copy-tel' — tel: не открываем, а копируем номер. Звонить с телефона оператора
 *                на номер офиса незачем (iCORE Phone такой переход и не пропустит:
 *                WebView2 ушёл бы на неизвестную схему и показал бы ошибку
 *                загрузки вместо страницы), а продиктовать или переслать номер
 *                водителю — ровно то, за чем его открывают;
 *   'external' — внутри телефона: http(s)-ссылку «в новое окно» или на чужой сайт
 *                (2ГИС) открывает сам телефон в системном браузере
 *                ({type:'open:external'}), иначе WebView2 ушёл бы со страницы;
 *   null       — ничего не трогаем (в обычном браузере ссылки работают как есть). */
export const linkAction = (href, target, inPhone, pageOrigin = '') => {
    const text = String(href || '').trim();
    if (/^tel:/i.test(text)) return 'copy-tel';
    if (!inPhone || !/^https?:\/\//i.test(text)) return null;
    if (String(target || '').trim().toLowerCase() === '_blank') return 'external';
    try {
        return pageOrigin && new URL(text).origin !== pageOrigin ? 'external' : null;
    } catch {
        return null;
    }
};

/* tel:-ссылка по частям: body — сам номер как записан (%-последовательности
   раскрыты, параметры ;ext=… ?… # отброшены), ext — добавочный из ;ext=. */
const telParts = (href) => {
    let text = String(href || '').trim().replace(/^tel:/i, '');
    try {
        text = decodeURIComponent(text);
    } catch {
        // Битая %-последовательность: раскрываем плюс, прочие коды выбрасываем —
        // иначе их шестнадцатеричные цифры (%E0 → «0») попали бы в номер.
        text = text.replace(/%2B/gi, '+').replace(/%[0-9A-F]{2}/gi, '');
    }
    text = text.replace(/^\/+/, '');
    return {
        body: text.split(/[;?#]/)[0].trim(),
        ext: /;ext=(\d{1,6})/i.exec(text)?.[1] || '',
    };
};

/** Номер из tel:-ссылки: %-последовательности раскрыты (%2B → +), лишнее после
 *  номера (;ext=…, ?…) отброшено, остаются цифры и плюс в начале. */
export const telNumber = (href) => {
    const { body } = telParts(href);
    const digits = body.replace(/\D/g, '');
    if (!digits) return '';
    return body.startsWith('+') ? `+${digits}` : digits;
};

// Длиннее в ссылке — уже не один номер: казахстанский — 11 цифр, с запасом на
// соседние страны — 12; два склеенных дают 22.
const TEL_MAX_DIGITS = 12;

/* Что скопировать по нажатию на телефон: { value, ext, more } или null — нечего.
 *
 * Источник — ТЕКСТ ссылки, то есть то, что оператор видит, а не её href. Портал
 * собирает href как tel:${phone.replace(/[^\d+]/g, '')}, а поле телефона — свободный
 * текст: из «+7 707 705 08 80, +7 727 333 22 11» получается
 * tel:+77077050880+77273332211, из «8 (7212) 50-60-70 доб. 12» — tel:8721250607012.
 * Прежде страница копировала этот склеенный номер и показывала «Номер скопирован» —
 * водитель получал номер, которого нет.
 *
 *   value — что кладём в буфер: набираемый номер (цифры и плюс в начале);
 *   ext   — добавочный: в буфер не идёт (его не набирают, а называют), но в тосте
 *           показан — оператор продиктует его отдельно;
 *   more  — сколько ещё номеров было в тексте ссылки: скопирован первый;
 *   raw   — номер разобрать не удалось, скопирован текст ссылки как есть.
 *
 * href нужен только ссылке с подписью без цифр («Позвонить») — и только если в нём
 * ровно один номер: не больше одного плюса, и тот в начале, не больше 12 цифр. */
export const phoneToCopy = (href, text = '') => {
    const seen = officePhones(text);
    if (seen.length) return { value: seen[0].number, ext: seen[0].ext, more: seen.length - 1 };

    const { body, ext } = telParts(href);
    const number = telNumber(href);
    const pluses = (body.match(/\+/g) || []).length;
    const single = pluses === (body.startsWith('+') ? 1 : 0)
        && number.replace(/\D/g, '').length <= TEL_MAX_DIGITS;
    if (number && single) return { value: number, ext, more: 0 };

    // Ни текст, ни ссылка одним номером не читаются — отдаём то, что на экране:
    // так человек получает ровно виденное, а не склейку из href.
    const shown = String(text || '').replace(/\s+/g, ' ').trim();
    return /\d/.test(shown) ? { value: shown, ext: '', more: 0, raw: true } : null;
};

/** Текст тоста после копирования телефона. */
export const phoneCopiedText = (phone, ok) => {
    if (!phone?.value) return '';
    if (phone.raw) return ok ? `Скопировано: ${phone.value}` : `Не удалось скопировать: ${phone.value}`;
    const notes = [
        phone.ext ? `доб. ${phone.ext}` : '',
        phone.more > 0 ? `первый из ${phone.more + 1}` : '',
    ].filter(Boolean).join(', ');
    const tail = notes ? ` (${notes})` : '';
    return ok
        ? `Номер скопирован: ${phone.value}${tail}`
        : `Не удалось скопировать номер ${phone.value}${tail}`;
};

/* Текст ошибки для тоста — по-русски. Когда сервер своего текста не дал, раздел
 * показывает e.message, а у axios это «Network Error» и «Request failed with
 * status code 502»: английские и технические, оператору они ничего не говорят.
 * Пустая строка — тост не нужен вовсе (запрос отменён, это не событие для человека).
 * Чужие тексты (сервер отвечает по-русски) проходят как есть. */
export const toastText = (message) => {
    const text = String(message ?? '').trim();
    if (/^Network Error$/i.test(text)) return 'Нет связи с порталом — проверьте подключение к интернету.';
    const failed = /^Request failed with status code (\d{3})$/i.exec(text);
    if (failed) {
        return Number(failed[1]) >= 500
            ? `Портал не ответил (ошибка ${failed[1]}) — попробуйте чуть позже.`
            : `Портал отклонил запрос (код ${failed[1]}).`;
    }
    if (/^timeout of \d+ms exceeded$/i.test(text)) return 'Портал не ответил вовремя — попробуйте ещё раз.';
    if (/^(canceled|Request aborted)$/i.test(text)) return '';
    return text;
};

/* Фоновое обновление списка офисов.
 *
 * Вкладка телефона живёт от первого показа до выхода из аккаунта: при переключении
 * вкладок телефон её только прячет (DialWebTab::SetWebVisible), страница не
 * перезагружается. Список, загруженный в 09:00, в 15:00 всё ещё показывал офис
 * открытым, хотя в 13:00 дежурный отметил его закрытым, — и водителя отправляли к
 * закрытой двери. Ночная смена после полуночи видела вчерашний день.
 *
 * Поэтому список обновляется сам: пока вкладка на экране — раз в пять минут, при
 * возврате во вкладку — если данным больше двух минут, а с наступлением новых суток
 * — сразу (раздел заодно переходит на новый «сегодня»). */
export const LIST_REFRESH_MS = 5 * 60 * 1000;
export const LIST_STALE_ON_RETURN_MS = 2 * 60 * 1000;
// Сколько ждать ответа на прежнюю просьбу, прежде чем просить снова: холодный
// старт API (Render) идёт до минуты, и новая просьба поверх прежней отменяла бы её
// раз за разом.
export const LIST_REFRESH_WAIT_MS = 90 * 1000;

/** Пора ли обновить список. okAt — когда он загрузился в последний раз, askedAt —
 *  когда ушла просьба, на которую ещё нет ответа (0 — такой нет), day — сутки
 *  (officeTodayISO), которым принадлежит загруженное, returning — оператор только
 *  что вернулся во вкладку. */
export const refreshDue = ({
    now, okAt, askedAt = 0, day = '', today = '', returning = false,
}) => {
    if (askedAt && now - askedAt < LIST_REFRESH_WAIT_MS) return false;
    if (day && today && day !== today) return true;
    return now - okAt >= (returning ? LIST_STALE_ON_RETURN_MS : LIST_REFRESH_MS);
};

/* Что показать в каждом состоянии, кроме «готово» и «нужен QR» (у тех свои экраны).
 * kind — какой значок: wait | session | denied | error. action — подпись кнопки
 * повтора (нет — кнопки нет). Тексты короткие: вкладка телефона бывает 243 px. */
export const noticeFor = (phase, {
    inPhone = false, reason = '', message = '', tokenError = '',
} = {}) => {
    switch (phase) {
        case 'connecting':
            return { kind: 'wait', title: inPhone ? 'Подключаемся к iCORE Phone…' : 'Проверяем вход…' };
        case 'checking':
            return { kind: 'wait', title: 'Проверяем доступ…' };
        case 'no-session':
            // reason 'expired' — сервер ответил 401 на живой с виду токен: сессию
            // отозвали (выход в другом месте, смена пароля). Английский текст
            // ответа («JWT authentication failed») человеку не показываем.
            if (!inPhone) {
                return {
                    kind: 'session',
                    title: 'Войдите в портал',
                    text: reason === 'expired'
                        ? 'Сессия портала закончилась — войдите заново в этом браузере.'
                        : 'Откройте портал iCORE в этом браузере и войдите — адреса офисов откроются здесь же.',
                    action: 'Проверить снова',
                };
            }
            return {
                kind: 'session',
                title: 'Войдите в iCORE Phone',
                text: reason === 'expired'
                    ? 'Сессия закончилась — войдите в iCORE Phone заново.'
                    : (tokenError || 'Адреса офисов открываются после входа в телефон.'),
                action: 'Проверить снова',
            };
        case 'denied': {
            const texts = {
                department: 'Раздел «Вики» не выдан вашему отделу.',
                'no-offices': 'В вашей вики нет справочника офисов. Если он нужен — скажите супервайзеру.',
                'no-space': message || 'Вам не выдано ни одного пространства вики.',
            };
            return {
                kind: 'denied',
                title: 'Адреса офисов недоступны',
                text: texts[reason] || message || 'Доступ к вики закрыт.',
                action: 'Проверить снова',
            };
        }
        case 'error':
            return {
                kind: 'error',
                title: reason === 'server' ? 'Портал не ответил' : 'Нет связи с порталом',
                // Что повторим сами и когда — говорит подсказка под текстом.
                text: (reason === 'server' && message) || 'Проверьте подключение к интернету.',
                action: 'Повторить',
            };
        default:
            return null;
    }
};
