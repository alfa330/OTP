/* Личная раскладка сайдбара: настоящий SidebarMenuArranger на меню того же
 * устройства, что в App.jsx (фрагменты, условия, обёртка отдела, счётчики), —
 * через react-dom/server. Браузера в наборе тестов нет; перетаскивание и
 * анимации проверяются на стенде, а здесь — то, что видно в разметке:
 *   - без раскладки меню рисуется в стандартном порядке и целиком;
 *   - с раскладкой — в порядке человека, с папками и отступом их разделов;
 *   - свёрнутая папка несёт сумму счётчиков своих разделов;
 *   - в режиме правки меню остаётся как было, у строк появляются ручки, у
 *     папки — «минус»; после «Обнулить» — место «перетащите сюда»; раздел на
 *     раздел — папка сразу с полем имени.
 * И проводку в App.jsx: обёртка стоит внутри меню, а ролевые ветки — как были.
 *
 * Компонент собирается esbuild'ом в node_modules/.cache (оттуда разрешается
 * react), стили подменяются пустыми. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SIDEBAR = join(ROOT, 'src', 'components', 'sidebar');
const readLf = (rel) => readFileSync(join(ROOT, rel), 'utf8').split('\r\n').join('\n');

/* localStorage — до загрузки модуля: хранилище раскладки читает копию
   учётки синхронно, при первой же отрисовке. */
const storage = new Map();
globalThis.window = globalThis.window || {};
globalThis.window.localStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
};

const cacheDir = join(ROOT, 'node_modules', '.cache', 'otp-tests');
mkdirSync(cacheDir, { recursive: true });
const entry = join(cacheDir, 'sidebar-layout-entry.js');
writeFileSync(entry, [
    `export { default } from ${JSON.stringify(join(SIDEBAR, 'SidebarMenuArranger.jsx'))};`,
    `export { sidebarLayoutStore } from ${JSON.stringify(join(SIDEBAR, 'sidebarLayoutStore.js'))};`,
    `export * from ${JSON.stringify(join(SIDEBAR, 'sidebarMenuElements.js'))};`,
].join('\n'));
const outfile = join(cacheDir, 'sidebar-layout-bundle.mjs');
await esbuild.build({
    entryPoints: [entry],
    bundle: true,
    format: 'esm',
    platform: 'node',
    outfile,
    external: ['react', 'react-dom', 'lucide-react', 'axios'],
    loader: { '.css': 'empty', '.jsx': 'jsx' },
    logLevel: 'silent',
});
const mod = await import(`file://${outfile}?v=${Date.now()}`);
const Arranger = mod.default;
const store = mod.sidebarLayoutStore;

const h = React.createElement;

/* Пункт меню как в App.jsx: значок, счётчик-уголок рельса (до подписи, до
   «9+»), подпись со своим счётчиком (до «99+»). «Обращения» и «Жалобы» носят
   один белый счётчик после подписи. */
const BADGE = 'inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full';
const item = (label, { active = false, badge = 0, tone = 'rose', white = false } = {}) => h('li', null,
    h('button', { className: `w-full text-left hover:bg-blue-700 ${active ? 'bg-blue-700' : ''}` },
        h('svg', { 'data-icon': label }),
        ' ',
        badge > 0 && !white ? h('span', { className: `sidebar-surveys-collapsed-badge ${BADGE} bg-${tone}-500 text-white` }, badge > 9 ? '9+' : String(badge)) : null,
        ' ',
        h('span', { className: 'sidebar-text inline-flex' },
            h('span', null, label),
            badge > 0 && !white ? h('span', { className: `${BADGE} bg-${tone}-500 text-white` }, badge > 99 ? '99+' : String(badge)) : null),
        badge > 0 && white ? h('span', { className: 'ml-auto inline-flex rounded-full bg-white/90 px-1.5 text-blue-700' }, String(badge)) : null));

const Scope = ({ section, activeCode, children }) => (activeCode && section === 'скрытый' ? null : children);

const menu = (activeCode = null) => [
    item('Отметки'),
    false && item('Не выдано'),
    h(Scope, { key: 's1', section: 'скрытый', activeCode }, item('Учет сотрудников')),
    h('hr', { key: 'hr1', className: 'border-t border-white-700 my-2 opacity-40' }),
    h(React.Fragment, { key: 'f' }, item('Вики'), item('Задачи', { badge: 3 })),
    item('Курсы', { active: true }),
    h('hr', { key: 'hr2', className: 'border-t border-white-700 my-2 opacity-40' }),
    item('Чаты ОП', { badge: 12, tone: 'orange' }),
];

const quiet = (fn) => {
    const original = console.error;
    console.error = () => {};
    try {
        return fn();
    } finally {
        console.error = original;
    }
};

const render = (userId, activeCode = null, children = menu(activeCode)) => quiet(() => renderToStaticMarkup(
    h('ul', null, h(Arranger, { userId, transparent: [Scope], apiBaseUrl: '', getHeaders: null, listRef: { current: null } }, children)),
));

const folderRowOf = (html) => /<li[^>]*data-sb-kind="folder"[\s\S]*?<\/li>/.exec(html)[0];

const rowsOf = (html) => [...html.matchAll(/<(li|hr)\b([^>]*)>/g)].map(([, tag, attrs]) => {
    if (tag === 'hr') return '—';
    const key = /data-sb-key="([^"]*)"/.exec(attrs)?.[1];
    const depth = /data-sb-depth="1"/.test(attrs) ? '  ' : '';
    return depth + key;
});

const saveFor = (userId, layout, closed = null) => {
    storage.set(`otp:sidebarLayout:v1:${userId}`, JSON.stringify({ layout, dirty: false }));
    if (closed) storage.set(`otp:sidebarFolders:v1:${userId}`, JSON.stringify(closed));
};

test('без личной раскладки меню целиком и в стандартном порядке', () => {
    const html = render(101);
    assert.deepEqual(rowsOf(html), ['Отметки', 'Учет сотрудников', '—', 'Вики', 'Задачи', 'Курсы', '—', 'Чаты ОП']);
    // Строка остаётся той же строкой меню: своя кнопка, подпись и счётчик.
    assert.match(html, /<li[^>]*data-sb-key="Задачи"[^>]*><button class="w-full text-left hover:bg-blue-700 ">/);
    assert.ok(!html.includes('sb-edit-overlay'), 'вне правки ручек нет');
});

test('обёртка фильтра отдела раскрывается: спрятанный пункт не рисуется', () => {
    const html = render(102, 'отдел');
    assert.deepEqual(rowsOf(html), ['Отметки', '—', 'Вики', 'Задачи', 'Курсы', '—', 'Чаты ОП']);
});

test('с раскладкой — порядок человека, папка и отступ её разделов', () => {
    saveFor(103, {
        v: 1,
        blocks: [['Чаты ОП', 'folder:fa1'], ['Учет сотрудников', 'Отметки', 'Курсы']],
        folders: { fa1: { name: 'Каждый день', items: ['Вики', 'Задачи'] } },
    });
    const html = render(103);
    assert.deepEqual(rowsOf(html), [
        'Чаты ОП', 'folder:fa1', '  Вики', '  Задачи', '—', 'Учет сотрудников', 'Отметки', 'Курсы',
    ]);
    assert.match(html, /data-sb-kind="folder"[^>]*data-sb-open="1"/);
    assert.match(html, /<span class="sb-folder-name">Каждый день<\/span>/);
    // У раскрытой папки своего счётчика нет — он у самих разделов.
    const folderRow = /<li[^>]*data-sb-kind="folder"[\s\S]*?<\/li>/.exec(html)[0];
    assert.ok(!/sidebar-surveys-collapsed-badge/.test(folderRow));
});

test('свёрнутая папка несёт сумму счётчиков и знает, что открытый раздел в ней', () => {
    saveFor(104, {
        v: 1,
        blocks: [['folder:fb2', 'Отметки', 'Учет сотрудников', 'Вики']],
        folders: { fb2: { name: 'Работа', items: ['Задачи', 'Чаты ОП', 'Курсы'] } },
    }, { fb2: true });
    const html = render(104);
    const folderRow = /<li[^>]*data-sb-kind="folder"[\s\S]*?<\/li>/.exec(html)[0];
    assert.match(folderRow, /data-sb-open="0"/);
    // 3 задачи + 12 чатов: в рельсе «9+», в подписи — число.
    assert.match(folderRow, /sidebar-surveys-collapsed-badge[^"]*bg-rose-500[^>]*>9\+</);
    assert.match(folderRow, />15</);
    assert.match(folderRow, /sb-folder-has-active/, 'открытый «Курсы» внутри свёрнутой папки');
});

test('режим правки: меню на месте и с ручками; «Обнулить» — место «перетащите сюда»; раздел на раздел — папка', () => {
    saveFor(105, {
        v: 1,
        blocks: [['folder:fc3', 'Отметки']],
        folders: { fc3: { name: 'Своё', items: ['Вики'] } },
    });
    const before = rowsOf(render(105));
    store.startEditing();
    const html = render(105);
    assert.deepEqual(rowsOf(html), before, 'по «Изменить меню» ничего не разлетается');
    assert.match(html, /class="sb-edit-overlay sb-edit-overlay--folder"/);
    assert.match(html, /class="sb-folder-delete /);
    assert.equal((html.match(/class="sb-edit-overlay"/g) || []).length, before.filter((row) => row !== '—' && !row.startsWith('folder:')).length, 'ручка у каждого раздела');
    assert.ok(!/sb-drop-hint/.test(html));
    // «Обнулить» — всё на панели, в сайдбаре место, куда бросать.
    store.reset();
    const empty = render(105);
    assert.deepEqual(rowsOf(empty), ['undefined']);
    assert.match(empty, /class="sb-drop-hint"/);
    // Перенесли раздел с панели — он в сайдбаре с ручкой, место-подсказка ушло.
    store.place('Задачи');
    const placed = render(105);
    assert.deepEqual(rowsOf(placed), ['Задачи']);
    assert.ok(!/sb-drop-hint/.test(placed));
    // Раздел с панели положили на «Задачи» — папка на её месте, сразу с полем имени.
    const id = store.stack('Задачи', 'Вики');
    const folder = render(105);
    assert.deepEqual(rowsOf(folder), [`folder:${id}`, '  Задачи', '  Вики']);
    assert.match(folder, /class="sb-rename-input"/);
    assert.match(folder, /value="Новая папка"/);
    store.undo();
    assert.deepEqual(rowsOf(render(105)), ['Задачи'], '⌘Z — папки нет, «Вики» снова на панели');
    store.cancelEditing();
    assert.deepEqual(rowsOf(render(105)), before, '«Отменить» — всё как было до правки');
});

test('разбор меню: подпись — ключ, счётчик в неё не попадает, дубль получает номер', () => {
    const { items, defaultBlocks } = mod.readMenu([
        item('Задачи', { badge: 3 }),
        item('Задачи'),
        h('li', null, h('button', null, h('svg'))),
        h('hr'),
        item('Вики'),
    ], { transparent: [] });
    assert.deepEqual(defaultBlocks, [['Задачи', 'Задачи #2', '#item:2'], ['Вики']]);
    assert.equal(items.get('#item:2').fixed, true, 'строка без подписи остаётся на месте');
    assert.equal(mod.itemIsActive(item('X', { active: true })), true);
    assert.equal(mod.itemIsActive(item('X')), false, 'hover:bg-blue-700 — не открытый раздел');
    // Точное число — из подписи («12»), а не из уголка рельса («9+»).
    assert.deepEqual(mod.itemBadgeOf(item('X', { badge: 12, tone: 'amber' })), { count: 12, plus: false, tone: 'amber' });
    assert.deepEqual(mod.itemBadgeOf(item('X', { badge: 250 })), { count: 99, plus: true, tone: 'rose' });
    assert.deepEqual(mod.itemBadgeOf(item('Обращения', { badge: 4, white: true })), { count: 4, plus: false, tone: 'white' });
    assert.equal(mod.itemBadgeOf(item('X')), null);
});

test('сумма в папке: белая плашка уступает цветной, «99+» у раздела даёт «99+»', () => {
    saveFor(106, {
        v: 1,
        blocks: [['folder:fd4', 'Отметки']],
        folders: { fd4: { name: 'Почта', items: ['Обращения', 'Задачи'] } },
    }, { fd4: true });
    const some = [item('Отметки'), item('Обращения', { badge: 4, white: true }), item('Задачи', { badge: 3 })];
    assert.match(folderRowOf(render(106, null, some)), /rounded-full bg-rose-500 text-white[^>]*>7</);
    const many = [item('Отметки'), item('Обращения', { badge: 4, white: true }), item('Задачи', { badge: 250 })];
    assert.match(folderRowOf(render(106, null, many)), />99\+</);
    const none = [item('Отметки'), item('Обращения'), item('Задачи')];
    assert.ok(!/rounded-full/.test(folderRowOf(render(106, null, none))), 'без счётчиков в папке — без плашки');
});

/* ── Проводка в App.jsx ────────────────────────────────────────────────── */

const app = readLf('src/App.jsx');

test('раскладка оборачивает пункты меню, а ролевые ветки остаются на местах', () => {
    const ul = app.indexOf('<ul ref={sidebarMenuScrollRef}');
    const open = app.indexOf('<SidebarMenuArranger', ul);
    const firstItem = app.indexOf('{canAccessGroupLateBotSection && (', ul);
    const close = app.indexOf('</SidebarMenuArranger>');
    const ulEnd = app.indexOf('</ul>', close);
    assert.ok(ul > 0 && open > ul && open < firstItem, 'обёртка открывается сразу внутри меню');
    assert.ok(close > firstItem && app.slice(close, ulEnd).trim() === '</SidebarMenuArranger>', 'обёртка закрывается последней перед </ul>');
    assert.equal((app.match(/<SidebarMenuArranger\b/g) || []).length, 1);
    assert.match(app.slice(open, firstItem), /transparent=\{SIDEBAR_TRANSPARENT_WRAPPERS\}/);
    assert.match(app, /const SIDEBAR_TRANSPARENT_WRAPPERS = Object\.freeze\(\[SidebarDeptScope\]\);/);
    assert.match(app.slice(open, firstItem), /onArranged=\{paintSidebarMenu\}/, 'после перестановки карточки перекрашиваются');
});

test('проход по меню прячет разделы свёрнутой папки, а поиск их находит', () => {
    const at = app.indexOf('const paintSidebarMenu = useCallback(() => {');
    const body = app.slice(at, app.indexOf('}, []);', at));
    assert.match(body, /if \(!query\) return row\.dataset\.sbFolded !== '1';/);
    assert.match(body, /const hit = rowMatches\(row\);/);
    assert.match(body, /row\.classList\.toggle\('sidebar-menu-hidden', !hit\);/);
});

test('«Изменить меню» — в подвале, перед «Выходом»; правка закрывается при сворачивании', () => {
    const footer = app.indexOf('<ul className="sidebar-footer-menu');
    const row = app.indexOf('<SidebarEditFooterRow', footer);
    const logout = app.indexOf('onClick={stableSidebarHandleLogout}', footer);
    assert.ok(footer > 0 && row > footer && row < logout);
    assert.match(app, /if \(sidebarCollapsed \|\| isMobileShell\) sidebarLayoutStore\.finishEditing\(\);/);
});
