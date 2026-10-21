/*
 * Адрес раздела «Оплата счетов».
 *
 * Вкладки, справочники и окна раздела жили состоянием React: обновление страницы
 * возвращало на «Мои задачи», а адрес из строки браузера вёл туда же — прислать
 * коллеге «Справочники → Договоры» или заявку №5 было нечем. Номер заявки в адресе
 * был только у ссылки из Telegram, открытая щелчком заявка адрес не меняла. Теперь
 * адрес повторяет то, что открыто:
 *
 *   ?view=payments                                   — «Мои задачи»;
 *   ?view=payments&section=requests                  — вкладка; доски — approval, accounting, finance;
 *   ?view=payments&section=dictionaries&dict=routes  — справочник («Поставщики» — без метки);
 *   ?view=payments&section=assets&asset=2            — карточка имущества;
 *   …&request=5                                      — окно заявки поверх любой вкладки.
 *
 * `request` — та же метка, что у ссылок из Telegram (payments/notify.py::request_link):
 * уже отправленные ссылки по-прежнему открывают заявку.
 *
 * Метки свои, а не `tab` вики: её `tab` портал снимает при уходе из вики, и общая
 * метка связала бы два раздела. Свои метки при уходе отсюда снимает App.jsx
 * (stripPaymentsParams).
 *
 * Метки принадлежат разделу, названному в адресе (`view`). Входя сюда из меню, раздел
 * видит в строке ещё прежний `view` — портал переписывает адрес после отрисовки, — и
 * такие метки не читает.
 *
 * replaceState, а не pushState: вкладки и окна — не история браузера, как у вики и
 * Wazzup; на телефоне окна закрывает жест «назад» своим стеком (utils/mobileBackStack.js).
 */
import { stripTechnicalQueryParams } from '../../utils/urlHygiene.js';

export const PAYMENTS_VIEW = 'payments';
export const APP_VIEW_QUERY_PARAM = 'view';
export const PAYMENTS_SECTION_QUERY_PARAM = 'section';
export const PAYMENTS_DICTIONARY_QUERY_PARAM = 'dict';
export const PAYMENTS_ASSET_QUERY_PARAM = 'asset';
export const PAYMENTS_REQUEST_QUERY_PARAM = 'request';
// В этом порядке метки и стоят в адресе.
export const PAYMENTS_QUERY_PARAMS = [
    PAYMENTS_SECTION_QUERY_PARAM, PAYMENTS_DICTIONARY_QUERY_PARAM, PAYMENTS_ASSET_QUERY_PARAM, PAYMENTS_REQUEST_QUERY_PARAM,
];

/* Вкладки раздела. Набор ЗАКРЫТЫЙ: из адреса приезжает что угодно, и до состояния раздела
   доходит только своё. Доски — коды payments/workflow.py::BOARDS; что списки совпадают с
   вкладками PaymentsView.jsx и досками сервера, сторожит tests/payments_link.test.mjs. */
export const PAYMENTS_DEFAULT_TAB = 'desk';
export const PAYMENTS_BOARD_CODES = ['approval', 'accounting', 'finance'];
export const PAYMENTS_SECTION_KEYS = [PAYMENTS_DEFAULT_TAB, ...PAYMENTS_BOARD_CODES, 'requests', 'assets', 'dictionaries', 'roles'];
const BOARD_TAB_PREFIX = 'board:';

/* Справочники — значения SECTIONS в PaymentsDictionaries.jsx (сверяет тот же тест).
   Первый, «Поставщики», открывается и без метки. */
export const PAYMENTS_DEFAULT_DICTIONARY = 'counterparties';
export const PAYMENTS_DICTIONARY_KEYS = [
    'counterparties', 'contracts', 'legal_entities', 'counterparty_accounts', 'cards', 'templates',
    'limits', 'routes', 'counterparty_emails', 'mailings', 'categories', 'asset_categories', 'projects', 'settings',
];

/** Вкладка раздела по метке адреса: 'approval' → 'board:approval'. '' — такой вкладки нет. */
export const tabFromSection = (value) => {
    const key = String(value ?? '').trim();
    if (!PAYMENTS_SECTION_KEYS.includes(key)) return '';
    return PAYMENTS_BOARD_CODES.includes(key) ? `${BOARD_TAB_PREFIX}${key}` : key;
};

/** Метка адреса по вкладке: 'board:approval' → 'approval'. '' — у вкладки метки нет («Мои задачи»). */
export const sectionFromTab = (tab) => {
    const value = String(tab ?? '');
    // Доска — только с приставкой: голое «approval» вкладкой раздела не бывает, «board:requests» — тоже.
    if (value.startsWith(BOARD_TAB_PREFIX)) {
        const code = value.slice(BOARD_TAB_PREFIX.length);
        return PAYMENTS_BOARD_CODES.includes(code) ? code : '';
    }
    if (value === PAYMENTS_DEFAULT_TAB || PAYMENTS_BOARD_CODES.includes(value)) return '';
    return PAYMENTS_SECTION_KEYS.includes(value) ? value : '';
};

/** Ключ справочника или '' — если такого справочника нет. */
export const normalizeDictionary = (value) => {
    const key = String(value ?? '').trim();
    return PAYMENTS_DICTIONARY_KEYS.includes(key) ? key : '';
};

/** Номер заявки или имущества: только цифры (как пишет бот), 0 и мусор — null. */
export const normalizeRecordId = (value) => {
    const text = String(value ?? '').trim();
    if (!/^\d{1,9}$/.test(text)) return null;
    const id = Number(text);
    return id > 0 ? id : null;
};

const EMPTY_LINK = Object.freeze({ tab: PAYMENTS_DEFAULT_TAB, dict: '', asset: null, request: null });

/** Что открыть по адресу: { tab, dict, asset, request }. Адрес чужого раздела — пусто.
 *
 * Справочник и имущество читаются только при своей вкладке: метка без вкладки — не наша
 * сборка, и угадывать за неё нельзя.
 */
export const readPaymentsLink = (search) => {
    try {
        const params = new URLSearchParams(String(search ?? ''));
        if (params.get(APP_VIEW_QUERY_PARAM) !== PAYMENTS_VIEW) return { ...EMPTY_LINK };
        const tab = tabFromSection(params.get(PAYMENTS_SECTION_QUERY_PARAM)) || PAYMENTS_DEFAULT_TAB;
        return {
            tab,
            dict: tab === 'dictionaries' ? normalizeDictionary(params.get(PAYMENTS_DICTIONARY_QUERY_PARAM)) : '',
            asset: tab === 'assets' ? normalizeRecordId(params.get(PAYMENTS_ASSET_QUERY_PARAM)) : null,
            request: normalizeRecordId(params.get(PAYMENTS_REQUEST_QUERY_PARAM)),
        };
    } catch (error) {
        return { ...EMPTY_LINK };
    }
};

/** Что показать и что писать в адрес — по состоянию раздела. Чистая функция: таблицу случаев
 * проверяет tests/payments_link.test.mjs.
 *
 *   tab, dict, asset, request — что открыто (или что просит ссылка);
 *   tabs, dictionaries        — какие вкладки и справочники человеку видны по известным правам;
 *   settled                   — сервер в этот вход ответил на /ping — успешно или ошибкой;
 *   held                      — метки ссылки, которую раздел не открыл из-за сбоя /ping.
 *
 * Вкладка или справочник из ссылки, которых нет в известных правах, ЖДУТ ответа: набор
 * приходит с сервера, а запомненный с прошлого входа мог их не знать. Пока ждут, на их месте
 * заготовка, а в адресе — метки ссылки как есть: первый же кадр не должен стереть то, ради чего
 * человек пришёл. Номер заявки при этом идёт за окном — закрытая заявка из адреса уходит сразу.
 * `missing` — ответ пришёл, а открыть просимое нельзя: раздел возвращает «Мои задачи».
 */
export const resolvePaymentsLink = ({
    tab, dict = '', asset = null, request = null, tabs = [], dictionaries = [], settled = false, held = null,
}) => {
    const tabOpen = tabs.includes(tab);
    const activeTab = tabOpen ? tab : PAYMENTS_DEFAULT_TAB;
    const tabPending = !tabOpen && tab !== PAYMENTS_DEFAULT_TAB && !settled;
    const dictionaryOpen = !dict || dictionaries.includes(dict);
    const activeDictionary = dict && dictionaryOpen ? dict : (dictionaries[0] || '');
    const dictionaryPending = activeTab === 'dictionaries' && !dictionaryOpen && !settled;
    let marks;
    if (held) marks = { ...held, request };
    else if (tabPending || dictionaryPending) marks = { tab, dict, asset, request };
    else marks = { tab: activeTab, dict: activeDictionary, asset, request };
    return {
        activeTab,
        tabPending,
        activeDictionary,
        dictionaryPending,
        marks,
        missing: settled && ((!tabOpen && tab !== PAYMENTS_DEFAULT_TAB) || !dictionaryOpen),
    };
};

/** Снять метки раздела. Возвращает тот же объект URL. */
export const stripPaymentsParams = (url) => {
    PAYMENTS_QUERY_PARAMS.forEach((name) => url.searchParams.delete(name));
    return url;
};

/** Метки открытого на готовом URL: { tab, dict, asset, request }. Возвращает тот же объект. */
export const applyPaymentsMarks = (url, { tab, dict, asset, request } = {}) => {
    url.searchParams.set(APP_VIEW_QUERY_PARAM, PAYMENTS_VIEW);
    // Сначала снимаем все, потом ставим по порядку: адрес у одного и того же всегда один.
    stripPaymentsParams(url);
    const section = sectionFromTab(tab);
    if (section) url.searchParams.set(PAYMENTS_SECTION_QUERY_PARAM, section);
    const dictionary = section === 'dictionaries' ? normalizeDictionary(dict) : '';
    if (dictionary && dictionary !== PAYMENTS_DEFAULT_DICTIONARY) url.searchParams.set(PAYMENTS_DICTIONARY_QUERY_PARAM, dictionary);
    const assetId = section === 'assets' ? normalizeRecordId(asset) : null;
    if (assetId) url.searchParams.set(PAYMENTS_ASSET_QUERY_PARAM, String(assetId));
    const requestId = normalizeRecordId(request);
    if (requestId) url.searchParams.set(PAYMENTS_REQUEST_QUERY_PARAM, String(requestId));
    return url;
};

/** Открытое в разделе — в адресную строку.
 *
 * `onlyHere` — только если строка всё ещё адрес раздела. Так зовётся после «назад» на
 * телефоне: стек жестов возвращает в строку адрес из истории, а там бывает номер заявки,
 * которую уже закрыли, — его надо убрать, но жест, уводящий в другой раздел, не трогать.
 *
 * Тот же адрес второй раз не пишется: Safari разрешает не больше сотни replaceState за
 * 30 секунд и дальше бросает ошибку.
 */
export const syncPaymentsLink = (marks, { onlyHere = false } = {}) => {
    if (typeof window === 'undefined') return;
    try {
        const url = new URL(window.location.href);
        if (onlyHere && url.searchParams.get(APP_VIEW_QUERY_PARAM) !== PAYMENTS_VIEW) return;
        stripTechnicalQueryParams(url);
        applyPaymentsMarks(url, marks);
        const next = `${url.pathname}${url.search}${url.hash}`;
        const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        if (next !== current) window.history.replaceState(window.history.state, '', next);
    } catch (error) {
        // В урезанных браузерных контекстах адресная строка недоступна — не беда.
    }
};
