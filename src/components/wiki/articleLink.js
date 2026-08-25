/*
 * Адрес статьи вики.
 *
 * До сих пор у статьи адреса не было: витрина держала открытую статью в
 * состоянии React, и человек, который хотел «скинуть коллеге тарифы», мог
 * прислать только адрес раздела со словами «дальше найди сама». Перезагрузка
 * страницы теряла статью по той же причине.
 *
 * Адрес строим поверх ТЕКУЩЕГО адреса портала (?view=wiki&article=<slug>), а не
 * из константы: фронт живёт на GitHub Pages с базовым путём, и собранный руками
 * '/?view=wiki' увёл бы человека на корень домена. По этой же схеме сделаны
 * ссылки на задачу и на обращение — см. TASK_ID_QUERY_PARAM в src/App.jsx.
 */
import { stripTechnicalQueryParams } from '../../utils/urlHygiene.js';

export const WIKI_VIEW = 'wiki';
export const APP_VIEW_QUERY_PARAM = 'view';
export const WIKI_ARTICLE_QUERY_PARAM = 'article';

/* Слаг статьи задаёт сервер, и он НЕ ОБЯЗАН быть латинским: сегодняшний
   _slugify (wiki/routes_structure.py) транслитерирует, а 25 статей из 41 на
   проде пришли миграцией из старой вики с кириллицей в слаге
   («структура-отделов», «забытые-вещи»). Проверка на латиницу оставила бы эти
   статьи без ссылки — то есть почти всю вики.
   Пускаем буквы любого алфавита, цифры, дефис и подчёркивание, не длиннее 200
   символов. Запрет важен не по алфавиту, а по СЛУЖЕБНЫМ символам: слаг уходит
   в путь запроса /api/wiki/articles/<slug>, и '/', '.', '%' там недопустимы.
   В адресе кириллица уезжает в проценты (article=%D1%81%D1%82...) — браузер
   разворачивает её обратно сам.

   Потолок — 255, ровно как VARCHAR(255) у wiki_articles.slug. Раньше стояло
   200, и это расходилось со схемой молча: у статьи со слагом длиннее 200
   символов ссылка не строилась и не разбиралась, без единой ошибки. Слаг режется
   до 200 при создании (routes_structure._slugify), но суффикс от совпадения
   ('-2', '-3') дописывается УЖЕ ПОСЛЕ обрезки. */
export const normalizeArticleSlug = (value) => {
    const slug = String(value || '').trim();
    return /^[\p{L}\p{N}_-]{1,255}$/u.test(slug) ? slug : '';
};

/** Ссылка на статью ДЛЯ ТЕКСТА ДРУГОЙ СТАТЬИ — относительная и без процентов.
 *
 * Отдельная функция рядом с buildArticleLink, а не её повторное использование,
 * и разница принципиальная. buildArticleLink строит АБСОЛЮТНЫЙ адрес поверх
 * window.location.href — он нужен, чтобы ссылку скопировали и отправили в чат.
 * Но то, что уходит в ТЕЛО статьи, остаётся в базе навсегда, и абсолютный адрес
 * забетонировал бы там сегодняшний домен фронта (github.io — чужое пространство
 * имён). После переезда портала все внутренние ссылки разом повели бы на старое
 * место, а readArticleSlugFromHref перестала бы узнавать их по origin.
 *
 * Слаг кладём СЫРЫМ, без encodeURIComponent: в значении атрибута href кириллица
 * допустима, браузер закодирует её сам при переходе, а в базе останется
 * читаемое '?view=wiki&article=тарифы' вместо цепочки процентов. Это же снимает
 * вопрос декодирования на сервере (wiki/links.py). Служебных символов в слаге
 * нет по построению — normalizeArticleSlug пропускает только буквы, цифры,
 * дефис и подчёркивание.
 */
export const buildRelativeArticleLink = (slug) => {
    const normalized = normalizeArticleSlug(slug);
    if (!normalized) return '';
    return `?${APP_VIEW_QUERY_PARAM}=${WIKI_VIEW}&${WIKI_ARTICLE_QUERY_PARAM}=${normalized}`;
};

/** Ссылка на статью, которую можно скопировать и отправить. '' — если слаг битый. */
export const buildArticleLink = (slug) => {
    if (typeof window === 'undefined') return '';
    const normalized = normalizeArticleSlug(slug);
    if (!normalized) return '';
    try {
        const url = new URL(window.location.href);
        // Метки перезагрузки в ссылку не переносим — см. utils/urlHygiene.js.
        stripTechnicalQueryParams(url);
        url.searchParams.set(APP_VIEW_QUERY_PARAM, WIKI_VIEW);
        url.searchParams.set(WIKI_ARTICLE_QUERY_PARAM, normalized);
        return url.toString();
    } catch (error) {
        return '';
    }
};

/** Открытая статья в адресной строке. Пустой слаг — статью закрыли, метку убираем.
 *
 * replaceState, а не pushState: витрина статей — не роутер, и «назад» в браузере
 * должно уводить туда, откуда человек пришёл в портал, а не отматывать открытые
 * статьи по одной.
 */
export const syncArticleDeepLink = (slug) => {
    if (typeof window === 'undefined') return;
    try {
        const url = new URL(window.location.href);
        stripTechnicalQueryParams(url);
        const normalized = normalizeArticleSlug(slug);
        if (normalized) {
            url.searchParams.set(APP_VIEW_QUERY_PARAM, WIKI_VIEW);
            url.searchParams.set(WIKI_ARTICLE_QUERY_PARAM, normalized);
        } else {
            url.searchParams.delete(WIKI_ARTICLE_QUERY_PARAM);
        }
        window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    } catch (error) {
        // В урезанных браузерных контекстах адресная строка недоступна — не беда.
    }
};

/** Слаг из строки запроса адреса. Нужен порталу при загрузке: ссылку на статью
 * открывают из чата, и раздел обязан открыть её сразу после входа. */
export const readArticleSlugFromSearch = (search) => {
    try {
        const params = new URLSearchParams(String(search || ''));
        return normalizeArticleSlug(params.get(WIKI_ARTICLE_QUERY_PARAM));
    } catch (error) {
        return '';
    }
};

/** Слаг из ссылки на статью ЭТОГО портала. '' — ссылка внешняя или не на статью.
 *
 * Нужно ссылкам ВНУТРИ текста статьи: редактор вставляет обычный <a href>, и без
 * разбора такая ссылка перезагружала бы приложение целиком — с повторной
 * авторизацией и потерей места, откуда человек пришёл.
 */
export const readArticleSlugFromHref = (href) => {
    if (typeof window === 'undefined' || !href) return '';
    /* Якорь внутри страницы ('#glava-2') разрешился бы в ТЕКУЩИЙ адрес вместе с
       его параметрами, то есть в «ссылку на открытую статью». Такие ссылки —
       переходы по оглавлению, и трогать их нельзя. */
    if (String(href).trim().startsWith('#')) return '';
    try {
        const url = new URL(String(href), window.location.href);
        if (url.origin !== window.location.origin) return '';
        // Путь сравниваем без хвостового слеша: '/OTP/' и '/OTP' — одна страница.
        const trim = (path) => String(path || '').replace(/\/+$/, '');
        if (trim(url.pathname) !== trim(window.location.pathname)) return '';
        const view = String(url.searchParams.get(APP_VIEW_QUERY_PARAM) || '').trim();
        if (view && view !== WIKI_VIEW) return '';
        return normalizeArticleSlug(url.searchParams.get(WIKI_ARTICLE_QUERY_PARAM));
    } catch (error) {
        return '';
    }
};

/** Атрибуты <a> для сохранения: у СВОЕЙ ссылки target снимается.
 *
 * Редактор (TipTap) ставит каждой ссылке target="_blank" — так устроено
 * расширение Link по умолчанию. Витрина статьи ровно на этот признак
 * отказывается открывать статью внутри портала: `anchor.target === '_blank'` —
 * значит «отдать браузеру». Пока обе стороны жили порознь, это было незаметно;
 * с внутренними ссылками получается прямая поломка, причём РЕТРОАКТИВНАЯ.
 * TipTap разбирает тело статьи и собирает его обратно, поэтому при первом же
 * сохранении target="_blank" дописался бы всем уже лежащим в базе внутренним
 * ссылкам — а их в проде 253.
 *
 * Функция вынесена отдельно и намеренно чистая: это правило дороже всего
 * остального в фиче, а проверить его внутри расширения TipTap без браузера
 * нельзя. Внешние ссылки не трогаем — им новая вкладка полагается.
 */
export const linkAttrsForSaving = (attrs) => {
    const next = { ...(attrs || {}) };
    if (!readArticleSlugFromHref(next.href)) return next;
    delete next.target;
    // rel="noopener noreferrer" осмысленно только у внешней ссылки в новой
    // вкладке; своей он не нужен, а серверный санитайзер проставит его сам.
    delete next.rel;
    return next;
};
