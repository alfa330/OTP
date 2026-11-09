/*
 * Вкладка «Офисы» вики — то, что случается ПОСЛЕ первого кадра (дополнение к
 * tests/wiki_office_archive.test.mjs): поведение подсказок HoverTips на событиях
 * документа и вкладка «Архив» после нажатия — запрос, «загружаем», вопрос перед
 * архивацией, просьба открыть офис из поиска.
 *
 * Тесты пришли из разбора правки 09.11.2026: без них в WikiOffices.jsx и
 * HoverTips.jsx выживали 14 мутаций (вспышка живого списка под «Архивом»,
 * archived=1 вместо only, парки на архиве, офис из поиска искался в архиве,
 * DELETE без вопроса, пузырь от касания, у отключённой и раскрытой кнопки,
 * поверх открывшегося окна и т. д.).
 *
 * Браузера нет: корень живёт кадрами на стенде tests/wiki_entry_lab.mjs, а у
 * HoverTips document и window подменены — обработчики складываются в объект и
 * вызываются тестом напрямую.
 *
 * Запуск: node --test tests/wiki_office_archive_flow.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    BASE, HEADERS, calls, loadSection, mount, path, pending, storage, withTimers,
} from './wiki_entry_lab.mjs';

const noop = () => {};

/* ── HoverTips: обработчики документа ────────────────────────────────────── */

/* Элемент без DOM: closest и matches — ровно на те селекторы, что спрашивает
   HoverTips; незнакомый селектор — ошибка, чтобы тест не врал молча. */
class FakeElement {
    constructor(attrs = {}, { disabled = false, focusVisible = false, parent = null, rect = null } = {}) {
        this.attrs = attrs;
        this.disabled = disabled;
        this.focusVisibleFlag = focusVisible;
        this.parent = parent;
        this.rect = rect;
        this.isConnected = true;
    }

    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }

    getBoundingClientRect() { return this.rect || { left: 10, top: 10, width: 20, height: 20, bottom: 30 }; }

    contains(node) {
        for (let current = node; current; current = current.parent) if (current === this) return true;
        return false;
    }

    matches(selector) {
        return selector.split(',').map((part) => part.trim()).some((part) => {
            if (part === ':disabled') return this.disabled;
            if (part === ':focus-visible') return this.focusVisibleFlag;
            const attr = part.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
            if (attr) {
                return attr[2] === undefined
                    ? this.getAttribute(attr[1]) !== null
                    : this.getAttribute(attr[1]) === attr[2];
            }
            throw new Error(`селектор стенда не знает: ${part}`);
        });
    }

    closest(selector) {
        for (let node = this; node; node = node.parent) if (node.matches(selector)) return node;
        return null;
    }
}

const { HoverTips } = await loadSection({
    lab: true,
    name: 'hover-tips',
    entry: `export { default as HoverTips, anchorOnTop } from ${JSON.stringify(path('src/components/common/HoverTips.jsx'))};`,
});
const { anchorOnTop, tipPlacement } = await loadSection({
    name: 'hover-tips-pure',
    entry: `export { anchorOnTop, tipPlacement } from ${JSON.stringify(path('src/components/common/HoverTips.jsx'))};`,
});

/* Хозяин подсказок без браузера: документ ловит обработчики, состояние пузыря —
   первая ячейка корня. Тела у документа нет — пузырь не рисуется, и состояние
   читаем сразу после события, до следующего кадра.
   cover — что «лежит сверху» в точке кнопки (elementFromPoint): по умолчанию
   сама кнопка. */
async function withTips(run) {
    const handlers = {};
    const listen = (type, fn) => { handlers[type] = fn; };
    const doc = {
        addEventListener: listen,
        removeEventListener: noop,
        body: null,
        activeElement: null,
        cover: null,
        elementFromPoint() { return this.cover || this.activeElement; },
    };
    globalThis.Element = FakeElement;
    globalThis.document = doc;
    globalThis.window = { addEventListener: listen, removeEventListener: noop };
    try {
        await withTimers(async (timers) => {
            const section = mount(HoverTips, {});
            const tip = () => globalThis.__wikiEntryLab.slots[0].value;
            const live = () => timers.filter((t) => !t.cleared);
            try {
                await run({ handlers, tip, live, doc });
            } finally {
                section.unmount();
            }
        });
    } finally {
        delete globalThis.Element;
        delete globalThis.document;
        delete globalThis.window;
    }
}

/* Перевести фокус Tab'ом: keydown приходит раньше, чем фокус уходит. */
const tabTo = (handlers, doc, element) => {
    handlers.keydown({ key: 'Tab' });
    doc.activeElement = element;
    handlers.focusin({ target: element });
};

test('подсказка: мышь — через задержку; касание пузыря не даёт; клавиша гасит', async () => {
    await withTips(async ({ handlers, tip, live }) => {
        const button = new FakeElement({ 'data-tip': 'Убрать в архив' });
        handlers.pointerover({ pointerType: 'touch', target: button });
        assert.equal(live().length, 0, 'касание поставило таймер подсказки');
        handlers.pointerover({ pointerType: 'mouse', target: new FakeElement({}, { parent: button }) });
        assert.equal(live().length, 1);
        live()[0].fn();
        assert.equal(tip()?.text, 'Убрать в архив');
        handlers.keydown({ key: 'Escape' });
        assert.equal(tip(), null, 'клавиша не погасила подсказку');
    });
});

test('подсказка: у отключённой и раскрытой кнопки — нет', async () => {
    await withTips(async ({ handlers, live }) => {
        handlers.pointerover({
            pointerType: 'mouse',
            target: new FakeElement({ 'data-tip': 'Следующий день' }, { disabled: true }),
        });
        assert.equal(live().length, 0, 'отключённая кнопка обещает действие');
        handlers.pointerover({
            pointerType: 'mouse',
            target: new FakeElement({ 'data-tip': 'Фильтр', 'aria-expanded': 'true' }),
        });
        assert.equal(live().length, 0, 'раскрытая кнопка кладёт пузырь поверх своей панели');
    });
});

test('подсказка: нажатая кнопка молчит, пока мышь с неё не ушла', async () => {
    await withTips(async ({ handlers, tip, live }) => {
        const button = new FakeElement({ 'data-tip': 'Изменить' });
        handlers.pointerdown({ pointerType: 'mouse', target: button });
        handlers.pointerover({ pointerType: 'mouse', target: button });
        assert.equal(live().length, 0, 'после нажатия пузырь снова встаёт над кнопкой');
        handlers.pointerover({ pointerType: 'mouse', target: new FakeElement({}) });
        handlers.pointerover({ pointerType: 'mouse', target: button });
        assert.equal(live().length, 1, 'после ухода и возврата мыши подсказка не вернулась');
        live()[0].fn();
        assert.equal(tip()?.text, 'Изменить');
    });
});

test('подсказка: Tab — сразу; фокус от щелчка и возвращённый по Escape — нет', async () => {
    await withTips(async ({ handlers, tip, doc }) => {
        // Фокус от щелчка: перед ним было нажатие, а не Tab — даже если Tab
        // нажимали только что.
        const clicked = new FakeElement({ 'data-tip': 'Сегодня' });
        handlers.keydown({ key: 'Tab' });
        handlers.pointerdown({ pointerType: 'mouse', target: clicked });
        doc.activeElement = clicked;
        handlers.focusin({ target: clicked });
        assert.equal(tip(), null, 'фокус от щелчка показал подсказку');
        // Escape в календаре вернул фокус на дату программой — Tab не нажимали.
        const date = new FakeElement({ 'data-tip': 'Выбрать день' }, { focusVisible: true });
        handlers.keydown({ key: 'Escape' });
        doc.activeElement = date;
        handlers.focusin({ target: date });
        assert.equal(tip(), null, 'возвращённый фокус вызвал подсказку');
        // Переход по Tab.
        const today = new FakeElement({ 'data-tip': 'Сегодня' }, { focusVisible: true });
        tabTo(handlers, doc, today);
        assert.equal(tip()?.text, 'Сегодня');
        assert.equal(tip()?.source, 'focus');
        // Взялся за мышь — пузырь от Tab гаснет, даже над местом без подсказки.
        handlers.pointerover({ pointerType: 'mouse', target: new FakeElement({}) });
        assert.equal(tip(), null, 'пузырь от Tab висел, пока мышь водили по соседним кнопкам');
    });
});

test('подсказка: Tab по кнопке под открытым окном пузыря не даёт', async () => {
    await withTips(async ({ handlers, tip, doc }) => {
        const behind = new FakeElement({ 'data-tip': 'Убрать в архив' }, { focusVisible: true });
        doc.cover = new FakeElement({});   // затемнение окна
        tabTo(handlers, doc, behind);
        assert.equal(tip(), null, 'пузырь кнопки под окном встал поверх окна');
        doc.cover = null;
        tabTo(handlers, doc, behind);
        assert.equal(tip()?.text, 'Убрать в архив');
    });
});

test('подсказка: прокрутка гасит пузырь от мыши, а пузырь от Tab переставляет', async () => {
    await withTips(async ({ handlers, tip, live, doc }) => {
        const button = new FakeElement({ 'data-tip': 'Изменить' });
        handlers.pointerover({ pointerType: 'mouse', target: button });
        live()[0].fn();
        assert.equal(tip()?.source, 'hover');
        handlers.scroll({});
        assert.equal(tip(), null, 'пузырь от мыши остался над уехавшей кнопкой');

        const focused = new FakeElement({ 'data-tip': 'Позвонить' }, { focusVisible: true });
        tabTo(handlers, doc, focused);
        assert.equal(tip()?.text, 'Позвонить');
        handlers.scroll({});
        // Браузер сам докрутил до кнопки с фокусом — пузырь остаётся, место
        // считается заново (pos: null).
        assert.equal(tip()?.text, 'Позвонить', 'Tab остался без подсказки на прокрученной строке');
        assert.equal(tip()?.pos, null);
    });
});

test('подсказка: не влезает ни сверху, ни снизу — встаёт туда, где места больше', () => {
    // Окно 300 px, кнопка у нижнего края: сверху 230 px, снизу 22 — пузырь 260 px.
    const pos = tipPlacement({
        anchor: { left: 100, top: 238, width: 32, bottom: 270 },
        tip: { width: 200, height: 260 },
        view: { width: 1280, height: 300 },
    });
    assert.equal(pos.placement, 'top');
});

test('кнопка сверху: сама или её ребёнок — да; затемнение окна — нет; проверить нечем — да', () => {
    const anchor = new FakeElement({});
    const icon = new FakeElement({}, { parent: anchor });
    const doc = (hit) => ({ elementFromPoint: () => hit });
    assert.equal(anchorOnTop(anchor, doc(anchor)), true);
    assert.equal(anchorOnTop(anchor, doc(icon)), true);
    assert.equal(anchorOnTop(anchor, doc(new FakeElement({}))), false);
    assert.equal(anchorOnTop(anchor, doc(null)), true);
    assert.equal(anchorOnTop(anchor, {}), true);
});

/* ── Вкладка «Архив» раздела ─────────────────────────────────────────────── */

const { WikiOffices } = await loadSection({
    lab: true,
    name: 'wiki-offices-flow',
    entry: `export { default as WikiOffices } from ${JSON.stringify(path('src/components/wiki/WikiOffices.jsx'))};`,
    /* Таблица и карточка офиса — пустышки, которые отдают тесту свои пропсы:
       так достаются обработчики «В архив» (разметка статическая, нажать на
       значок нельзя). Карточка оставляет метку в разметке — открыта ли она. */
    stubs: {
        '[\\\\/]wiki[\\\\/]OfficeTable\\.jsx$':
            'export default function OfficeTable(props) { globalThis.__officeTableProps = props; return null; }',
        '[\\\\/]wiki[\\\\/]OfficeInfoModal\\.jsx$':
            "import React from 'react'; export default function OfficeInfoModal(props) { "
            + 'globalThis.__officeInfoProps = props; '
            + "return React.createElement('div', { 'data-info-modal': props.office.id }); }",
    },
});

const office = (over = {}) => ({
    id: 5, name: 'Центральный', city: 'Алматы', address: 'ул. Абая, 1', phone: '',
    status: 'active', schedule: null, no_office: false, kind: 'park', parks: [], day: null, ...over,
});
const officeCalls = (from) => calls.slice(from).filter((call) => call.url === `${BASE}/offices`);
const flush = async (section) => {
    await new Promise((resolve) => { setTimeout(resolve, 5); });
    await section.settle();
};

/* Нажатие на вкладку — сменой состояния корня: обработчиков у статической
   разметки нет. Ячейки состояния идут в порядке объявления в WikiOffices:
   первая со значением 'active' — listScope, вторая — scope. Переставят
   объявления — тест упадёт на «вкладка не переключилась», а не соврёт. */
const pressScope = async (section, key) => {
    const from = key === 'archive' ? 'active' : 'archive';
    const cells = globalThis.__wikiEntryLab.slots.filter((s) => s.kind === 'state' && s.value === from);
    const scopeCell = key === 'archive' ? cells[1] : cells[cells.length - 1];
    scopeCell.set(key);
    await section.settle();        // кадр со сменой вкладки ставит таймер загрузки
    const between = section.html;  // кадр до того, как ушёл запрос
    await flush(section);
    return between;
};
const setFilters = async (section, over) => {
    const cell = globalThis.__wikiEntryLab.slots.find((s) => s.kind === 'state'
        && s.value && typeof s.value === 'object' && 'sort' in s.value && 'city' in s.value);
    cell.set({ ...cell.value, ...over });
    await section.settle();
    await flush(section);
};

async function openedAsManager(props = {}) {
    const mark = calls.length;
    const section = mount(WikiOffices, { base: BASE, headers: HEADERS, showToast: noop, ...props });
    await flush(section);
    officeCalls(mark)[0].answer({ items: [office()], cities: ['Алматы'], can_manage: true, archived_count: 1 });
    pending('/parks', mark).answer({ items: [] });
    await section.settle();
    return section;
}

test('архив: запрос — только архив, без города и /parks; до ответа — не живой список', async () => {
    const section = await openedAsManager();
    try {
        // Город в фильтре: панели на вкладке архива нет, и условие из неё не едет.
        let mark = calls.length;
        await setFilters(section, { city: 'Алматы' });
        officeCalls(mark).at(-1).answer({ items: [office()], cities: ['Алматы'], can_manage: true, archived_count: 1 });
        pending('/parks', mark).answer({ items: [] });
        await section.settle();

        mark = calls.length;
        const between = await pressScope(section, 'archive');
        // Кадр между нажатием и запросом: живой офис строкой архива — ложь.
        assert.match(between, /Архив офисов/, 'вкладка не переключилась');
        assert.doesNotMatch(between, /Центральный/, 'архив на кадр показал живой список');
        const asked = officeCalls(mark);
        assert.equal(asked.length, 1);
        assert.equal(asked[0].params.archived, 'only');
        assert.equal(asked[0].params.city, undefined);
        assert.equal(calls.slice(mark).filter((c) => c.url === `${BASE}/parks`).length, 0, 'архив тянет парки');
        // Ответа ещё нет: в состоянии живой список, показывать его под «Архивом» нельзя.
        assert.doesNotMatch(section.html, /Центральный/);

        asked[0].answer({
            items: [office({ id: 9, name: 'Сайран', status: 'archived', archived_at: '2026-10-12T14:05:33' })],
            cities: [], can_manage: true, archived_count: 1,
        });
        await section.settle();
        assert.match(section.html, /Сайран/);
        assert.match(section.html, /Убран в архив 12\.10\.2026/);
        assert.doesNotMatch(section.html,
            /Добавить новый офис|Статус офисов на предыдущий день|wiki-m-office-legend|Фильтр и сортировка/);
    } finally {
        section.unmount();
    }
});

test('архив → «Офисы»: живой список возвращается, архивного в нём нет', async () => {
    const section = await openedAsManager();
    try {
        let mark = calls.length;
        await pressScope(section, 'archive');
        officeCalls(mark).at(-1).answer({
            items: [office({ id: 9, name: 'Сайран', status: 'archived' })], cities: [], can_manage: true, archived_count: 1,
        });
        await section.settle();
        mark = calls.length;
        const between = await pressScope(section, 'active');
        assert.doesNotMatch(between, /Сайран/, '«Офисы» на кадр показали архивный офис');
        const asked = officeCalls(mark);
        assert.equal(asked.at(-1).params.archived, undefined);
        asked.at(-1).answer({ items: [office()], cities: ['Алматы'], can_manage: true, archived_count: 1 });
        pending('/parks', mark).answer({ items: [] });
        await section.settle();
        assert.match(section.html, /Статус офисов по городам/);
        assert.doesNotMatch(section.html, /Сайран/);
    } finally {
        section.unmount();
    }
});

test('архив: просьба открыть офис из поиска возвращает на «Офисы»', async () => {
    const section = await openedAsManager();
    try {
        await pressScope(section, 'archive');
        const mark = calls.length;
        await section.update({ focusRequest: { nonce: 1, officeId: 5, city: 'Алматы' } });
        await flush(section);
        const asked = officeCalls(mark);
        assert.ok(asked.length >= 1);
        assert.equal(asked[asked.length - 1].params.archived, undefined, 'офис из поиска ищут в архиве');
    } finally {
        section.unmount();
    }
});

test('«В архив» в таблице: без согласия DELETE не уходит, с согласием — уходит', async () => {
    storage.set('wiki.offices.view', JSON.stringify({ view: 'table' }));
    const section = await openedAsManager();
    globalThis.window = { confirm: () => false, localStorage: globalThis.localStorage };
    try {
        const { onArchive } = globalThis.__officeTableProps;
        let mark = calls.length;
        assert.equal(onArchive(office()), false);
        assert.equal(calls.slice(mark).filter((c) => c.method === 'delete').length, 0, 'офис убран без вопроса');

        globalThis.window.confirm = () => true;
        mark = calls.length;
        assert.equal(onArchive(office()), true);
        const sent = calls.slice(mark).filter((c) => c.method === 'delete');
        assert.equal(sent.length, 1);
        assert.equal(sent[0].url, `${BASE}/offices/5`);
    } finally {
        delete globalThis.window;
        storage.delete('wiki.offices.view');
        section.unmount();
    }
});

test('«В архив» в карточке офиса: «Отмена» в вопросе карточку не закрывает', async () => {
    storage.set('wiki.offices.view', JSON.stringify({ view: 'table' }));
    const section = await openedAsManager();
    globalThis.window = { confirm: () => false, localStorage: globalThis.localStorage };
    try {
        globalThis.__officeTableProps.onOpen(office());
        await section.settle();
        assert.match(section.html, /data-info-modal="5"/);

        globalThis.__officeInfoProps.onArchive(office());
        await section.settle();
        assert.match(section.html, /data-info-modal="5"/, '«Отмена» в вопросе унесла и открытую карточку');

        globalThis.window.confirm = () => true;
        globalThis.__officeInfoProps.onArchive(office());
        await section.settle();
        assert.doesNotMatch(section.html, /data-info-modal=/, 'после согласия карточка осталась над списком');
    } finally {
        delete globalThis.window;
        storage.delete('wiki.offices.view');
        section.unmount();
    }
});
