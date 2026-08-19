/* Уведомления колокола, продублированные на рабочий стол.
 *
 * Никакого Google API здесь нет и не нужно: показ системной «плашки» из
 * открытой вкладки — это стандартный Notification API браузера, без сервера,
 * без ключей и без внешнего сервиса. Google (FCM) понадобился бы только для
 * доставки в ЗАКРЫТУЮ вкладку — это другая, куда более дорогая механика
 * (Service Worker + Web Push), и она здесь намеренно не делается.
 *
 * Всё, что можно решить без браузера, вынесено сюда чистыми функциями: у
 * Notification нет вменяемого способа проверить себя в node --test, а решение
 * «показывать или молчать» — как раз то место, где ошибка превращается в шум.
 */

// Разрешение выдаётся браузеру на КОНКРЕТНОЙ машине, поэтому и выключатель
// живёт рядом с ним — в localStorage, а не в настройках пользователя на
// сервере. Серверная галочка обещала бы то, чего не может: «включено» на
// рабочем компьютере ничего не значит для ноутбука, где разрешения нет.
// Ключ с id: за одним компьютером сидят посменно разные операторы.
export const desktopPrefKey = (userId) => `otp:desktop-notifications:${userId ?? 'anon'}`;

export const readDesktopPref = (userId, storage) => {
    try {
        return storage?.getItem(desktopPrefKey(userId)) === '1';
    } catch (e) {
        // Приватный режим и заблокированные куки роняют доступ к хранилищу.
        return false;
    }
};

export const writeDesktopPref = (userId, value, storage) => {
    try {
        if (value) storage?.setItem(desktopPrefKey(userId), '1');
        else storage?.removeItem(desktopPrefKey(userId));
    } catch (e) {
        /* см. выше — не смогли запомнить, работаем в рамках сессии */
    }
};

export const desktopSupported = (win) => Boolean(win && typeof win.Notification === 'function');

export const desktopPermission = (win) => (desktopSupported(win) ? win.Notification.permission : 'unsupported');

/* Показывать ли системную плашку.
 *
 * Главное условие — человек НЕ смотрит на страницу. Когда портал перед
 * глазами, о новом уже сказали колокол и выехавшая карточка, и системная
 * плашка поверх них — чистый шум, да ещё и с ковриком поверх той же карточки.
 *
 * Скрытая вкладка и потерянный фокус — разные вещи, нужны обе: свёрнутое окно
 * и вкладка в фоне дают hidden, а вот открытый поверх браузера Excel оставляет
 * вкладку видимой (visibilityState === 'visible'), и ловится он только по
 * hasFocus. Без второй проверки оператор, весь день сидящий в другой
 * программе, не увидел бы ничего.
 */
export const shouldNotifyDesktop = ({ enabled, permission, hidden, focused, panelOpen }) => {
    if (!enabled) return false;
    if (permission !== 'granted') return false;
    if (panelOpen) return false;
    return Boolean(hidden) || !focused;
};

const pluralRu = (count, forms) => {
    const n = Math.abs(count) % 100;
    const n1 = n % 10;
    if (n > 10 && n < 20) return forms[2];
    if (n1 > 1 && n1 < 5) return forms[1];
    if (n1 === 1) return forms[0];
    return forms[2];
};

const NOTICE_FORMS = ['уведомление', 'уведомления', 'уведомлений'];

/* Одна плашка на приход, а не по штуке на каждый элемент: когда ночью
   натекло семь задач, семь всплывающих окон подряд — это не информирование.
   Показываем самое важное (список отсортирован сервером, важное сверху), а про
   остальное говорим числом — ровно как всплывающая карточка в сайдбаре. */
export const buildDesktopNotice = ({ item, extra = 0, added = 0, sourceLabel }) => {
    const rest = Math.max(0, Number(extra) || 0);
    const tail = rest > 0 ? `и ещё ${rest} ${pluralRu(rest, NOTICE_FORMS)}` : '';

    if (!item?.title) {
        /* Состав выяснить не удалось — так бывает у «4 You», где дюжина новых
           фотографий свёрнута сервером в одну строку. Сказать «что-то пришло»
           честнее, чем промолчать: за подробностями человек откроет колокол. */
        const count = Math.max(1, Number(added) || 1);
        return {
            title: 'Новые уведомления',
            body: `${count} ${pluralRu(count, NOTICE_FORMS)}`,
            tag: 'otp-bell-summary',
        };
    }

    const lead = [sourceLabel, item.body].filter(Boolean).join(' · ');
    return {
        title: item.title,
        body: [lead, tail].filter(Boolean).join('\n'),
        // Метка гасит повтор той же карточки (возврат во вкладку, переподключение
        // канала), но не мешает разным уведомлениям лечь стопкой.
        tag: `otp-bell:${item.source}:${item.id}`,
    };
};

/* Запрос разрешения. Вызывать ТОЛЬКО из обработчика клика: браузеры давно
   считают запрос на голом открытии страницы спамом — Chrome его беззвучно
   душит, а Firefox рисует перечёркнутый колокольчик в адресной строке. И
   отказ необратим: второй раз спросить уже не дадут, чинится только руками
   в настройках сайта. */
export const requestDesktopPermission = async (win) => {
    if (!desktopSupported(win)) return 'unsupported';
    try {
        return await win.Notification.requestPermission();
    } catch (e) {
        return desktopPermission(win);
    }
};

/* Показ. onActivate получает управление по клику по плашке — окно к этому
   моменту уже поднято наверх. */
export const showDesktopNotice = (notice, { win, icon, onActivate } = {}) => {
    const target = win || (typeof window !== 'undefined' ? window : null);
    if (!desktopSupported(target) || target.Notification.permission !== 'granted') return null;
    try {
        const notification = new target.Notification(notice.title, {
            body: notice.body || undefined,
            tag: notice.tag,
            icon: icon || undefined,
            badge: icon || undefined,
        });
        notification.onclick = () => {
            try { target.focus?.(); } catch (e) { /* окно могли закрыть */ }
            notification.close();
            onActivate?.();
        };
        return notification;
    } catch (e) {
        // Notification умеет бросать в экзотических сборках (например, когда
        // системные уведомления выключены на уровне ОС). Молчим: колокол и
        // карточка в портале всё это время работают.
        return null;
    }
};
