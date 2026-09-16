/*
 * Адрес вкладки вики.
 *
 * У статьи адрес есть с 27.08.2026 (articleLink.js), а у самих вкладок раздела
 * не было: «Офисы», «Парки», «Помощник» и «Журнал» жили состоянием React, и
 * человек, который хотел показать коллеге статус офисов, мог прислать только
 * «зайди в вики и нажми Офисы». Перезагрузка страницы по той же причине
 * возвращала на главную витрину.
 *
 * Строим поверх ТЕКУЩЕГО адреса портала (?view=wiki&tab=<ключ>), как ссылка на
 * статью: фронт живёт на GitHub Pages с базовым путём, и собранный руками
 * '/?view=wiki' увёл бы на корень домена.
 *
 * ПРОСТРАНСТВО в ссылке — не украшение. Вкладки показываются по тумблерам
 * пространства (spaceFeatures.js), а выбранное пространство хранится у каждого
 * своё, в localStorage. Ссылка на «Офисы» Таксопарков, открытая тем, у кого
 * выбрана вика Тез КЦ, привела бы в раздел, где этой вкладки нет вовсе.
 * Поэтому метку пишем, когда пространств больше одного, — там, где оно одно,
 * это был бы шум в каждой ссылке.
 */
import { stripTechnicalQueryParams } from '../../utils/urlHygiene.js';

export const WIKI_VIEW = 'wiki';
export const APP_VIEW_QUERY_PARAM = 'view';
export const WIKI_TAB_QUERY_PARAM = 'tab';
export const WIKI_SPACE_QUERY_PARAM = 'space';

/* Ключи вкладок раздела. Набор ЗАКРЫТЫЙ: из адреса приезжает что угодно, и
 * «tab=<script>» не должен доходить до состояния раздела. Что список совпадает
 * с вкладками WikiView.jsx, сторожит tests/test_wiki_tab_link.py — разъехавшись,
 * они дали бы ссылку, которая молча открывает главную.
 *
 * Главная («library») здесь есть намеренно, хотя в адрес не пишется (см.
 * buildWikiTabLink): её присылают явной ссылкой из чужих ссылок-обрезков, и
 * принять такой адрес раздел обязан.
 */
export const WIKI_TAB_KEYS = [
    'library', 'assistant', 'questions', 'catalog', 'news',
    'overview', 'parks', 'offices', 'analytics', 'audit',
];

/** Вкладка по умолчанию — витрина статей. Метки в адресе у неё нет. */
export const WIKI_DEFAULT_TAB = 'library';

/** Ключ вкладки или '' — если это не вкладка вики. */
export const normalizeWikiTab = (value) => {
    const key = String(value || '').trim();
    return WIKI_TAB_KEYS.includes(key) ? key : '';
};

/** Идентификатор пространства из адреса. 0 — метки нет или она битая. */
export const normalizeWikiSpaceId = (value) => {
    const id = Number(String(value ?? '').trim());
    return Number.isInteger(id) && id > 0 ? id : 0;
};

/** Метки вкладки и пространства на готовом URL. Возвращает тот же объект. */
const applyWikiMarks = (url, tab, spaceId) => {
    url.searchParams.set(APP_VIEW_QUERY_PARAM, WIKI_VIEW);
    const key = normalizeWikiTab(tab);
    /* Главная метки не получает: '?view=wiki' и так открывает витрину, а
       '&tab=library' в каждой скопированной ссылке — лишние буквы, за которыми
       нет ни одного решения. */
    if (key && key !== WIKI_DEFAULT_TAB) url.searchParams.set(WIKI_TAB_QUERY_PARAM, key);
    else url.searchParams.delete(WIKI_TAB_QUERY_PARAM);
    const space = normalizeWikiSpaceId(spaceId);
    if (space) url.searchParams.set(WIKI_SPACE_QUERY_PARAM, String(space));
    else url.searchParams.delete(WIKI_SPACE_QUERY_PARAM);
    return url;
};

/** Ссылка на вкладку, которую можно скопировать и отправить. '' — не собралась.
 *
 * Метку СТАТЬИ снимаем: ссылку на вкладку копируют из шапки раздела, и открытая
 * в этот момент статья к обещанию «вот раздел Офисы» отношения не имеет — с ней
 * получатель попал бы в чужой текст. Ссылка на саму статью собирается отдельно
 * (articleLink.js) и своей кнопкой.
 */
export const buildWikiTabLink = (tab, spaceId = null) => {
    if (typeof window === 'undefined') return '';
    try {
        const url = new URL(window.location.href);
        // Метки перезагрузки в ссылку не переносим — см. utils/urlHygiene.js.
        stripTechnicalQueryParams(url);
        url.searchParams.delete('article');
        return applyWikiMarks(url, tab, spaceId).toString();
    } catch (error) {
        return '';
    }
};

/** Открытая вкладка в адресной строке.
 *
 * replaceState, а не pushState: вкладки — не история браузера, и «назад» должно
 * уводить туда, откуда человек пришёл в портал, а не отматывать вкладки по
 * одной. Тот же выбор, что у ссылки на статью (syncArticleDeepLink).
 *
 * Метку статьи здесь НЕ трогаем ни в какую сторону: её ставит и снимает витрина
 * (articleLink.js), и снятие отсюда гасило бы адрес открытой статьи на каждом
 * рендере раздела.
 */
export const syncWikiTabLink = (tab, spaceId = null) => {
    if (typeof window === 'undefined') return;
    try {
        const url = new URL(window.location.href);
        stripTechnicalQueryParams(url);
        applyWikiMarks(url, tab, spaceId);
        window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    } catch (error) {
        // В урезанных браузерных контекстах адресная строка недоступна — не беда.
    }
};

/** Вкладка из строки запроса. Нужна разделу при входе по присланной ссылке. */
export const readWikiTabFromSearch = (search) => {
    try {
        const params = new URLSearchParams(String(search || ''));
        return normalizeWikiTab(params.get(WIKI_TAB_QUERY_PARAM));
    } catch (error) {
        return '';
    }
};

/** Пространство из строки запроса. 0 — метки нет. */
export const readWikiSpaceFromSearch = (search) => {
    try {
        const params = new URLSearchParams(String(search || ''));
        return normalizeWikiSpaceId(params.get(WIKI_SPACE_QUERY_PARAM));
    } catch (error) {
        return 0;
    }
};
