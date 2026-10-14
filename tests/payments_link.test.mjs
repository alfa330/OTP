/*
 * «Оплата счетов»: адрес раздела повторяет открытое — вкладку, справочник, карточку имущества
 * и заявку (src/components/payments/paymentsLink.js).
 *
 * Просьба 14.10.2026: «сделай так чтобы ссылки были корректные по каждому разделу и заявке».
 * До этого вкладки и окна жили только состоянием React: обновление страницы и любая ссылка из
 * адресной строки открывали «Мои задачи», а номер заявки в адресе был лишь у ссылки из Telegram.
 *
 * Здесь — разбор и сборка адреса, сверка закрытых списков с кодом раздела и проводка в App.jsx.
 * Первый кадр раздела по ссылке проверяет tests/payments_section_entry.test.mjs, живое
 * поведение (щелчки, перезагрузка, «назад») — проход стенда.
 *
 * Запуск: node --test tests/payments_link.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    PAYMENTS_BOARD_CODES, PAYMENTS_DICTIONARY_KEYS, PAYMENTS_QUERY_PARAMS, PAYMENTS_SECTION_KEYS,
    applyPaymentsMarks, normalizeRecordId, readPaymentsLink, resolvePaymentsLink, sectionFromTab, stripPaymentsParams,
    syncPaymentsLink, tabFromSection,
} from '../src/components/payments/paymentsLink.js';

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
const BASE = 'https://alfa330.github.io/OTP/';

const linkOf = (search) => readPaymentsLink(search);
const urlFor = (marks, start = `${BASE}?view=payments`) => applyPaymentsMarks(new URL(start), marks);

/* ── разбор адреса ───────────────────────────────────────────────────────── */

test('ссылка из Telegram открывает заявку на «Моих задачах»', () => {
    // Тот же вид, что собирает payments/notify.py::request_link: уже отправленные ссылки живут.
    assert.deepEqual(linkOf('?view=payments&request=12'), { tab: 'desk', dict: '', asset: null, request: 12 });
    const notify = source('payments/notify.py');
    assert.ok(notify.includes("return '%s/?view=payments&request=%s' % (base, int(request_id))"), 'бот собирает ссылку иначе — сверить с readPaymentsLink');
});

test('каждая вкладка, справочник и карточка имущества читаются из адреса', () => {
    assert.deepEqual(linkOf('?view=payments&section=requests&request=5'), { tab: 'requests', dict: '', asset: null, request: 5 });
    for (const code of PAYMENTS_BOARD_CODES) {
        assert.equal(linkOf(`?view=payments&section=${code}`).tab, `board:${code}`);
    }
    assert.equal(linkOf('?view=payments&section=roles').tab, 'roles');
    assert.deepEqual(linkOf('?view=payments&section=dictionaries&dict=routes'), { tab: 'dictionaries', dict: 'routes', asset: null, request: null });
    assert.deepEqual(linkOf('?view=payments&section=assets&asset=2&request=3'), { tab: 'assets', dict: '', asset: 2, request: 3 });
    // Порядок меток в адресе не важен.
    assert.deepEqual(linkOf('?request=7&dict=contracts&section=dictionaries&view=payments'), { tab: 'dictionaries', dict: 'contracts', asset: null, request: 7 });
});

test('метки чужого раздела не читаются: в адресе ещё прежний view', () => {
    // Вход из меню: портал переписывает адрес уже после отрисовки раздела.
    for (const search of ['?view=tasks&section=requests&request=5', '?view=wiki&tab=offices', '?section=assets&asset=2', '', '?']) {
        assert.deepEqual(linkOf(search), { tab: 'desk', dict: '', asset: null, request: null }, search);
    }
});

test('мусор в адресе до раздела не доходит', () => {
    assert.equal(linkOf('?view=payments&section=<script>').tab, 'desk');
    assert.equal(linkOf('?view=payments&section=board:approval').tab, 'desk');
    assert.equal(linkOf('?view=payments&section=offices').tab, 'desk');
    assert.equal(linkOf('?view=payments&section=dictionaries&dict=users').dict, '');
    // Справочник и имущество — только при своей вкладке.
    assert.equal(linkOf('?view=payments&dict=routes').dict, '');
    assert.equal(linkOf('?view=payments&section=requests&asset=2').asset, null);
    for (const junk of ['abc', '0', '-1', '1e3', '5.5', '12abc', '1234567890', ' ', '']) {
        assert.equal(normalizeRecordId(junk), null, `«${junk}»`);
        assert.equal(linkOf(`?view=payments&request=${encodeURIComponent(junk)}`).request, null, `request=${junk}`);
    }
    assert.equal(normalizeRecordId(' 42 '), 42);
    assert.equal(normalizeRecordId(42), 42);
});

/* ── сборка адреса ───────────────────────────────────────────────────────── */

test('у вкладки по умолчанию и у первого справочника меток нет', () => {
    assert.equal(urlFor({ tab: 'desk' }).search, '?view=payments');
    assert.equal(urlFor({ tab: 'dictionaries', dict: 'counterparties' }).search, '?view=payments&section=dictionaries');
    assert.equal(urlFor({ tab: 'dictionaries', dict: '' }).search, '?view=payments&section=dictionaries');
});

test('адрес одного и того же всегда один: метки по порядку, лишние сняты', () => {
    const url = urlFor({ tab: 'board:accounting', request: 4 }, `${BASE}?request=9&view=payments&asset=3&dict=routes&section=assets&foo=1`);
    // Чужая метка (foo) остаётся на месте — раздел трогает только свои.
    assert.equal(url.search, '?view=payments&foo=1&section=accounting&request=4');
    assert.equal(urlFor({ tab: 'assets', asset: 2, request: 3 }).search, '?view=payments&section=assets&asset=2&request=3');
    assert.equal(urlFor({ tab: 'dictionaries', dict: 'templates', request: 8 }).search, '?view=payments&section=dictionaries&dict=templates&request=8');
    // Справочник и имущество не пишутся на чужой вкладке, мусор — никуда.
    assert.equal(urlFor({ tab: 'requests', dict: 'routes', asset: 2 }).search, '?view=payments&section=requests');
    assert.equal(urlFor({ tab: 'nope', request: 'abc' }).search, '?view=payments');
});

test('вкладка → адрес → вкладка: по кругу без потерь', () => {
    for (const key of PAYMENTS_SECTION_KEYS) {
        const tab = tabFromSection(key);
        assert.ok(tab, key);
        assert.equal(readPaymentsLink(urlFor({ tab, request: 15 }).search).tab, tab, key);
        assert.equal(readPaymentsLink(urlFor({ tab, request: 15 }).search).request, 15, key);
    }
    for (const dict of PAYMENTS_DICTIONARY_KEYS) {
        assert.equal(readPaymentsLink(urlFor({ tab: 'dictionaries', dict }).search).dict, dict === 'counterparties' ? '' : dict);
    }
    assert.equal(sectionFromTab('desk'), '');
    assert.equal(sectionFromTab('approval'), '', 'голый код доски — не вкладка');
    assert.equal(sectionFromTab('board:requests'), '');
    assert.equal(sectionFromTab('board:finance'), 'finance');
});

test('уход из раздела снимает только его метки', () => {
    const url = stripPaymentsParams(new URL(`${BASE}?view=tasks&section=requests&dict=routes&asset=2&request=5&task_id=9&tab=offices`));
    assert.equal(url.search, '?view=tasks&task_id=9&tab=offices');
    assert.deepEqual(PAYMENTS_QUERY_PARAMS, ['section', 'dict', 'asset', 'request']);
});

/* ── запись в адресную строку ───────────────────────────────────────────── */

const fakeWindow = (href) => {
    const calls = [];
    const win = {
        location: new URL(href),
        history: {
            state: { idx: 3 },
            replaceState(state, title, next) {
                calls.push(next);
                win.location = new URL(next, win.location.href);
            },
        },
    };
    return { win, calls };
};

const withWindow = (href, run) => {
    const { win, calls } = fakeWindow(href);
    const hadWindow = 'window' in globalThis;
    const before = globalThis.window;
    globalThis.window = win;
    try {
        run(win);
    } finally {
        if (hadWindow) globalThis.window = before; else delete globalThis.window;
    }
    return { win, calls };
};

test('открытое пишется в строку браузера; тот же адрес второй раз не пишется', () => {
    const { win, calls } = withWindow(`${BASE}?view=payments&auth_reload=1&v=2`, () => {
        syncPaymentsLink({ tab: 'requests', request: 5 });
        syncPaymentsLink({ tab: 'requests', request: 5 });
        syncPaymentsLink({ tab: 'requests', request: null });
    });
    // Служебные метки перезагрузки в ссылку не попадают (utils/urlHygiene.js).
    assert.deepEqual(calls, ['/OTP/?view=payments&section=requests&request=5', '/OTP/?view=payments&section=requests']);
    assert.equal(win.location.search, '?view=payments&section=requests');
});

test('после «назад» поправляется только адрес раздела, жест в другой раздел не трогается', () => {
    // Стек жестов вернул адрес с номером закрытой заявки — раздел убирает его.
    const here = withWindow(`${BASE}?view=payments&request=5`, () => {
        syncPaymentsLink({ tab: 'desk', request: null }, { onlyHere: true });
    });
    assert.deepEqual(here.calls, ['/OTP/?view=payments']);
    // Жест увёл в «Задачи» — адрес уже не наш.
    const away = withWindow(`${BASE}?view=tasks&task_id=3`, () => {
        syncPaymentsLink({ tab: 'requests', request: 5 }, { onlyHere: true });
    });
    assert.deepEqual(away.calls, []);
    assert.equal(away.win.location.search, '?view=tasks&task_id=3');
});

/* ── что показать и что писать в адрес ───────────────────────────────────── */

const ADMIN_TABS = ['desk', 'board:approval', 'board:accounting', 'board:finance', 'requests', 'assets', 'dictionaries', 'roles'];
const PLAIN_TABS = ['desk', 'requests'];
const resolve = (state) => resolvePaymentsLink({ dictionaries: PAYMENTS_DICTIONARY_KEYS, ...state });

test('права известны: показано то, что просит ссылка, адрес — то же', () => {
    const view = resolve({ tab: 'assets', asset: 2, request: 9, tabs: ADMIN_TABS, settled: true });
    assert.equal(view.activeTab, 'assets');
    assert.equal(view.tabPending, false);
    assert.equal(view.missing, false);
    assert.deepEqual(view.marks, { tab: 'assets', dict: 'counterparties', asset: 2, request: 9 });
    // Справочник без метки — первый из видимых.
    assert.equal(resolve({ tab: 'dictionaries', tabs: ADMIN_TABS }).activeDictionary, 'counterparties');
    assert.equal(resolve({ tab: 'dictionaries', dict: 'routes', tabs: ADMIN_TABS }).activeDictionary, 'routes');
});

test('вкладка из ссылки ждёт прав: заготовка, метки ссылки в адресе, заявка идёт за окном', () => {
    const waiting = resolve({ tab: 'board:finance', request: 5, tabs: PLAIN_TABS, settled: false });
    assert.equal(waiting.tabPending, true);
    assert.equal(waiting.activeTab, 'desk', 'под заготовкой стол не рисуется — его гасит tabPending');
    assert.equal(waiting.missing, false);
    assert.deepEqual(waiting.marks, { tab: 'board:finance', dict: '', asset: null, request: 5 });
    // Заявку закрыли, пока вкладка ждёт: номер из адреса уходит сразу, вкладка из ссылки — остаётся.
    assert.deepEqual(resolve({ tab: 'board:finance', request: null, tabs: PLAIN_TABS }).marks,
        { tab: 'board:finance', dict: '', asset: null, request: null });
    // Справочник из ссылки ждёт прав так же.
    const dict = resolve({ tab: 'dictionaries', dict: 'cards', tabs: ADMIN_TABS, dictionaries: ['counterparties', 'contracts'] });
    assert.equal(dict.dictionaryPending, true);
    assert.equal(dict.marks.dict, 'cards');
});

test('ответ пришёл, а просимого нет — «Мои задачи»; сбой /ping ссылку в адресе держит', () => {
    const answered = resolve({ tab: 'assets', asset: 2, tabs: PLAIN_TABS, settled: true });
    assert.equal(answered.missing, true);
    assert.equal(answered.tabPending, false);
    assert.equal(answered.activeTab, 'desk');
    // Удержанная ссылка (сбой /ping): экран — «Мои задачи», адрес — ссылка, заявка — за окном.
    const held = resolve({ tab: 'desk', request: 7, tabs: PLAIN_TABS, settled: true, held: { tab: 'assets', dict: '', asset: 2 } });
    assert.equal(held.activeTab, 'desk');
    assert.deepEqual(held.marks, { tab: 'assets', dict: '', asset: 2, request: 7 });
    const dictMissing = resolve({ tab: 'dictionaries', dict: 'settings', tabs: ADMIN_TABS, dictionaries: ['counterparties'], settled: true });
    assert.equal(dictMissing.missing, true);
    assert.equal(dictMissing.activeDictionary, 'counterparties');
    assert.equal(dictMissing.dictionaryPending, false);
});

/* ── сверка с кодом раздела ─────────────────────────────────────────────── */

test('справочники в адресе — ровно справочники раздела', () => {
    const dictionaries = source('src/components/payments/PaymentsDictionaries.jsx');
    const block = dictionaries.split('const SECTIONS = [')[1].split('];')[0];
    const values = [...block.matchAll(/\{ value: '([a-z_]+)'/g)].map((match) => match[1]);
    assert.deepEqual(values, PAYMENTS_DICTIONARY_KEYS);
    assert.equal(values[0], 'counterparties', 'первый справочник открывается без метки — он должен быть первым и в списке');
});

test('вкладки в адресе — ровно вкладки раздела, доски — доски сервера', () => {
    const workflow = source('payments/workflow.py');
    const boards = [...workflow.matchAll(/^BOARD_[A-Z]+ = '([a-z]+)'$/gm)].map((match) => match[1]);
    assert.deepEqual(boards, PAYMENTS_BOARD_CODES);
    const view = source('src/components/payments/PaymentsView.jsx');
    const block = view.split('const tabs = useMemo(() => {')[1].split('}, [')[0];
    const values = [...block.matchAll(/value: '([a-z]+)'/g)].map((match) => match[1]);
    assert.deepEqual(values.sort(), PAYMENTS_SECTION_KEYS.filter((key) => !PAYMENTS_BOARD_CODES.includes(key)).sort());
    assert.ok(block.includes('value: `board:${code}`'), 'доски перестали называться board:<код>');
});

test('раздел пишет открытое в адрес и поправляет его после «назад»', () => {
    const view = source('src/components/payments/PaymentsView.jsx');
    assert.ok(view.includes('const [link] = useState(linkAtEntry);'));
    assert.ok(view.includes('const [openedId, setOpenedId] = useState(link.request);'));
    // Что писать в адрес, решает resolvePaymentsLink (таблица случаев выше) — по состоянию раздела.
    assert.match(view, /resolvePaymentsLink\(\{\s*tab, dict: dictionary, asset: assetId, request: openedId,\s*tabs: tabValues, dictionaries: dictionaryKeys, settled: pingSettled, held: heldLink,\s*\}\)/);
    assert.ok(view.includes('syncPaymentsLink(current);'));
    assert.ok(view.includes("window.addEventListener('popstate', restore);"));
    assert.ok(view.includes("return () => window.removeEventListener('popstate', restore);"));
    assert.ok(view.includes('syncPaymentsLink(current, { onlyHere: true })'));
    assert.ok(view.includes('}, [markTab, markDict, markAsset, markRequest]);'));
    // Сбой /ping — не ответ «вкладки нет»: права подтверждает только успешный ответ.
    assert.ok(view.includes('setPingOk(true);') && view.includes('if (!pingOk) setHeldLink('));
    // Щелчок по «Мои задачи», пока вкладка из ссылки ждёт прав, срабатывает.
    assert.ok(view.includes('if (value === tab) return;'));
    // Справочник и карточку имущества держит раздел, а не сами вкладки.
    assert.ok(view.includes('section={activeDictionary}') && view.includes('onSectionChange={changeDictionary}'));
    assert.ok(view.includes('openedAsset={assetId}') && view.includes('onOpenAsset={setAssetId}'));
    // Прежнее чтение и снятие номера заявки ушли в модуль адреса.
    assert.ok(!view.includes('requestIdFromLocation') && !view.includes('dropRequestFromLocation'));
});

test('портал снимает метки раздела при уходе из него — и в адресе, и в ссылке пункта меню', () => {
    const app = source('src/App.jsx');
    assert.ok(app.includes("import { stripPaymentsParams } from './components/payments/paymentsLink';"));
    for (const name of ['const buildAppViewUrl = ', 'const syncAppViewWithUrl = ']) {
        const body = app.split(name)[1].split('\n};')[0];
        assert.ok(body.includes("if (nextView !== 'payments') stripPaymentsParams(url);"), name);
    }
});
