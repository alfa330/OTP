/*
 * Вкладка «Офисы» вики: архив и подсказки к кнопкам (задача 09.11.2026).
 *
 * Было: архив — тумблер «Показывать архивные» в панели «Фильтр»; включённый, он
 * мешал архивные офисы с живыми, и те красились по графику («Открыт до 19:00»).
 * Офис уезжал в архив от одного щелчка по значку без подписи, а что делают
 * значки в строке таблицы, не говорило ничего.
 *
 * Стало: «Офисы | Архив N» у того, кто правит справочник; в архиве — свой список
 * с «Вернуть» и тем, кто и когда убрал; перед архивацией — вопрос; у кнопок —
 * подсказка по наведению (data-tip, common/HoverTips).
 *
 * Запуск: node --test tests/wiki_office_archive.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
    archiveConfirmText, archiveOrder, archivedLine,
} from '../src/components/wiki/officeArchiveRules.js';
import {
    BASE, HEADERS, calls, loadSection, mount, path, pending,
} from './wiki_entry_lab.mjs';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');

/* Компоненты — настоящие, через esbuild и серверный рендер (как в
   offices_embed_render.test.mjs): браузерного окружения в проекте нет. */
async function load(name, file) {
    const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
    mkdirSync(dir, { recursive: true });
    const outfile = join(dir, `${name}-${process.pid}.mjs`);
    buildSync({
        entryPoints: [fileURLToPath(new URL(file, import.meta.url))],
        bundle: true,
        format: 'esm',
        target: 'node18',
        outfile,
        external: ['react', 'react-dom', 'axios', 'lucide-react', 'framer-motion'],
        loader: { '.jsx': 'jsx', '.css': 'empty' },
        logLevel: 'silent',
    });
    /* Сборку убираем сразу после import: имя с номером процесса, и без уборки
       каждый прогон оставлял бы в кэше пять новых файлов (тот же урок, что у
       tests/wiki_entry_lab.mjs, — там за день набралось 1,5 ГБ). */
    try {
        return await import(`file://${outfile.replace(/\\/g, '/')}`);
    } finally {
        rmSync(outfile, { force: true });
    }
}

const { default: OfficeArchive } = await load('OfficeArchive', '../src/components/wiki/OfficeArchive.jsx');
const { default: OfficeTable } = await load('OfficeTable', '../src/components/wiki/OfficeTable.jsx');
const { default: OfficeInfoModal } = await load('OfficeInfoModalArchive', '../src/components/wiki/OfficeInfoModal.jsx');
const {
    DEFAULT_FILTERS, activeFilterCount,
} = await load('OfficeFiltersArchive', '../src/components/wiki/OfficeFilters.jsx');
const {
    tipPlacement, TIP_SHOW_DELAY_MS, TAB_FOCUS_WINDOW_MS, focusCameFromTab,
} = await load('HoverTips', '../src/components/common/HoverTips.jsx');
const { default: ParkEditor } = await load('ParkEditorArchive', '../src/components/wiki/ParkEditor.jsx');

const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));
const noop = () => {};

const WEEK = {
    mon: { from: '09:00', to: '19:00' }, tue: { from: '09:00', to: '19:00' },
    wed: { from: '09:00', to: '19:00' }, thu: { from: '09:00', to: '19:00' },
    fri: { from: '09:00', to: '19:00' }, sat: null, sun: null,
};

const office = (over = {}) => ({
    id: 5, name: 'Центральный', city: 'Алматы', address: 'ул. Абая, 1', phone: '+7 707 000 00 00',
    status: 'active', schedule: WEEK, no_office: false, kind: 'park', parks: [],
    day: null, updated_at: '2026-11-01T10:00:00', ...over,
});

const ARCHIVED = office({
    id: 7, name: 'Сайран', status: 'archived',
    archived_at: '2026-10-12T14:05:33.123456', archived_by: 'Иванов Иван',
});

/* ── Правила архива ──────────────────────────────────────────────────────── */

test('архив: недавно убранные сверху, без даты — в конце по названию', () => {
    const items = [
        { id: 1, name: 'Б', archived_at: null },
        { id: 2, name: 'В', archived_at: '2026-10-01T09:00:00' },
        { id: 3, name: 'А', archived_at: null },
        { id: 4, name: 'Г', archived_at: '2026-11-05T09:00:00' },
        { id: 5, name: 'Д', archived_at: '2026-11-05T09:00:00' },
    ];
    assert.deepEqual(archiveOrder(items).map((item) => item.id), [4, 5, 2, 3, 1]);
    // Порядок — новым массивом: ответ сервера в состоянии раздела не трогаем.
    assert.deepEqual(items.map((item) => item.id), [1, 2, 3, 4, 5]);
});

test('строка «убран в архив»: дата и кто, без кого — только дата, без даты — ничего', () => {
    assert.equal(archivedLine(ARCHIVED), 'Убран в архив 12.10.2026 · Иванов Иван');
    assert.equal(archivedLine({ ...ARCHIVED, archived_by: null }), 'Убран в архив 12.10.2026');
    assert.equal(archivedLine({ ...ARCHIVED, archived_by: '  ' }), 'Убран в архив 12.10.2026');
    assert.equal(archivedLine({ ...ARCHIVED, archived_at: null }), '');
    assert.equal(archivedLine(null), '');
});

test('вопрос перед архивацией называет офис и говорит, где его вернуть', () => {
    const text = archiveConfirmText(office());
    assert.match(text, /^Убрать офис «Центральный» в архив\?/);
    assert.match(text, /из списка, из поиска и из выбора офиса в других разделах/);
    assert.match(text, /на вкладке «Архив»/);
    // Головным офисом парка архивный остаётся — обещать «пропадёт из ответов
    // помощника» значило бы обещать неправду.
    assert.doesNotMatch(text, /помощник/);
});

test('фильтр: тумблера «Показывать архивные» больше нет — и в счёт он не идёт', () => {
    assert.ok(!('showArchived' in DEFAULT_FILTERS));
    assert.equal(activeFilterCount({ ...DEFAULT_FILTERS, showArchived: true }), 0);
    assert.equal(activeFilterCount({ ...DEFAULT_FILTERS, city: 'Алматы', parkId: '3' }), 2);
});

/* ── Список архива ───────────────────────────────────────────────────────── */

test('архив: строка — офис, город, адрес, кто и когда убрал, «Вернуть» словами', () => {
    const html = render(OfficeArchive, {
        offices: [ARCHIVED, office({ id: 8, name: 'Балхаш', city: 'Балхаш', status: 'archived', no_office: true })],
        query: '', busy: false, onOpen: noop, onRestore: noop,
    });
    assert.match(html, /Сайран/);
    assert.match(html, /Алматы/);
    assert.match(html, /ул\. Абая, 1/);
    assert.match(html, /Убран в архив 12\.10\.2026 · Иванов Иван/);
    // Город, совпавший с названием, не повторяется; «офиса нет» — словами.
    assert.equal((html.match(/>Балхаш</g) || []).length, 1);
    assert.match(html, /Офиса в городе нет/);
    assert.equal((html.match(/> Вернуть</g) || []).length, 2);
    assert.equal((html.match(/data-tip="Вернуть офис в справочник — он снова появится в списке и поиске"/g) || []).length, 2);
    // Вся строка — кнопка во всю ширину: пузырь над её серединой вставал бы
    // далеко от курсора, поверх поиска. Подсказка — только у «Вернуть».
    assert.equal((html.match(/data-tip=/g) || []).length, 2);
    // Статусов у архивных нет: ни бейджа «Открыт», ни заливки строки цветом.
    assert.doesNotMatch(html, />(Открыт|Закрыт|Открыто|Закрыто)[^<]*</);
    assert.doesNotMatch(html, /bg-emerald|bg-rose/);
});

test('архив: пустой — объясняет, что сюда попадает; пустой поиск — «Ничего не найдено»', () => {
    const empty = render(OfficeArchive, { offices: [], query: '', busy: false, onOpen: noop, onRestore: noop });
    assert.match(empty, /Архив пуст/);
    assert.match(empty, /любой офис можно вернуть/);
    const notFound = render(OfficeArchive, { offices: [], query: 'Атырау', busy: false, onOpen: noop, onRestore: noop });
    assert.match(notFound, /Ничего не найдено/);
    assert.doesNotMatch(notFound, /Архив пуст/);
});

test('архив: пока идёт возврат, «Вернуть» не нажимается второй раз', () => {
    const html = render(OfficeArchive, { offices: [ARCHIVED], query: '', busy: true, onOpen: noop, onRestore: noop });
    assert.match(html, /<button type="button" disabled=""[^>]*>[^]*?Вернуть/);
});

/* ── Таблица статусов ────────────────────────────────────────────────────── */

test('таблица: у значков в строке — подсказки, что они делают; возврата из архива тут нет', () => {
    const html = render(OfficeTable, {
        offices: [office()], groups: null, dayISO: '2026-11-09', isToday: true, tick: 0,
        canManage: true, onOpen: noop, onEdit: noop, onArchive: noop, onMarkDay: noop,
    });
    assert.match(html, /data-tip="Статус на дату: отметить, что офис открыт или закрыт, или закрыть на срок"/);
    assert.match(html, /data-tip="Изменить: адрес, телефон, график работы, карта"/);
    assert.match(html, /data-tip="Убрать в архив — офис пропадёт из списка, вернуть можно на вкладке «Архив»"/);
    assert.match(html, /data-tip="Открыть карточку офиса: карта, часы работы, телефоны парков"/);
    assert.match(html, /data-tip="Позвонить по этому номеру"/);
    assert.doesNotMatch(html, /Вернуть из архива|В архиве/);
    // Системная подсказка title легла бы второй поверх нашей.
    assert.doesNotMatch(html, / title="/);
});

test('таблица: у читателя справочника кнопок действий и их подсказок нет', () => {
    const html = render(OfficeTable, {
        offices: [office()], groups: null, dayISO: '2026-11-09', isToday: true, tick: 0,
        canManage: false, onOpen: noop, onEdit: noop, onArchive: noop, onMarkDay: noop,
    });
    assert.doesNotMatch(html, /Убрать в архив|Изменить офис|Отметить статус/);
});

/* ── Карточка офиса ──────────────────────────────────────────────────────── */

const modal = (target, over = {}) => render(OfficeInfoModal, {
    office: target, base: BASE, dayISO: '2026-11-09', isToday: true, tick: 0, canManage: true,
    onClose: noop, onCopyAddress: noop, onEdit: noop, onArchive: noop, onRestore: noop, onMarkDay: noop,
    ...over,
});

test('карточка архивного офиса: «В архиве», кто и когда — вместо статуса; «Вернуть», без отметки дня', () => {
    const html = modal(ARCHIVED);
    assert.match(html, /В архиве/);
    assert.match(html, /Убран в архив 12\.10\.2026 · Иванов Иван/);
    assert.match(html, /Вернуть/);
    assert.doesNotMatch(html, /Статус на дату/);
    assert.doesNotMatch(html, />\s*В архив\s*</);
    // Живой статус по графику («Открыто до 19:00») архивному не положен.
    assert.doesNotMatch(html, /Открыто|Закрыто|Обед/);
});

test('карточка архивного офиса: в часах только неделя — без строки «на дату» вкладки «Офисы»', () => {
    const html = modal(ARCHIVED, { dayISO: '2026-11-05', isToday: false });
    assert.doesNotMatch(html, /05\.11\.2026/);
    assert.match(html, /Пн–Пт/);
    // У действующего строка дня на месте — правка только про архивный.
    assert.match(modal(office(), { dayISO: '2026-11-05', isToday: false }), /05\.11\.2026 09:00–19:00/);
});

test('карточка действующего офиса: подсказки у кнопок подвала и у «Скопировать»', () => {
    const html = modal(office({ map_url: 'https://go.2gis.com/abc' }));
    assert.match(html, /data-tip="Отметить, что офис открыт или закрыт в этот день, или закрыть на срок"/);
    assert.match(html, /data-tip="Изменить адрес, телефон, график работы и карту"/);
    assert.match(html, /data-tip="Убрать офис в архив — вернуть его можно на вкладке «Архив»"/);
    assert.match(html, /data-tip="Скопировать адрес вместе с городом"/);
    assert.match(html, /data-tip="Открыть офис на карте 2ГИС в новой вкладке"/);
    assert.doesNotMatch(html, /Вернуть/);
});

/* ── Головной офис парка в архиве ────────────────────────────────────────── */

test('форма парка: головной офис в архиве — строка об этом, а не молчаливое «Офис не выбран»', () => {
    // Список офисов формы — только действующие (WikiParks грузит их вместе с парками).
    const offices = [office({ id: 5, address: 'ул. Абая, 1' })];
    const draft = (headOfficeId) => ({
        name: 'iTaxi', city: '', address: '', website: '', commission: '', description: '',
        head_office_id: headOfficeId, logo_file_id: null, logo_url: null, logo_frame: null,
        numbers: [{ office_id: null, phone: '7070000000', note: '' }],
    });
    const props = (headOfficeId) => ({
        draft: draft(headOfficeId), setDraft: noop, offices, onUploadLogo: noop,
    });

    const archived = render(ParkEditor, props(7));
    assert.match(archived, /Головной офис парка убран в архив/);
    assert.match(archived, /Верните офис на вкладке «Офисы» или выберите другой/);
    // Вторая строка про ту же вкладку под тем же полем — шум.
    assert.doesNotMatch(archived, /Нужного нет — заведите его/);

    const active = render(ParkEditor, props(5));
    assert.doesNotMatch(active, /убран в архив/);
    assert.match(active, /ул\. Абая, 1/);

    // Новый парк и парк без головного офиса — без строки, с прежней подсказкой.
    for (const empty of [undefined, null]) {
        const html = render(ParkEditor, props(empty));
        assert.doesNotMatch(html, /убран в архив/);
        assert.match(html, /Нужного нет — заведите его на вкладке «Офисы»/);
    }
});

/* ── Подсказка: где встать пузырю ────────────────────────────────────────── */

const VIEW = { width: 1280, height: 800 };

test('подсказка: над кнопкой по центру, стрелка — над кнопкой', () => {
    const pos = tipPlacement({
        anchor: { left: 600, top: 400, width: 40, bottom: 432 },
        tip: { width: 200, height: 40 },
        view: VIEW,
    });
    assert.equal(pos.placement, 'top');
    assert.equal(pos.left, 520);          // центр кнопки 620 − половина пузыря
    assert.equal(pos.top, 400 - 8 - 40);
    assert.equal(pos.arrowLeft, 100);
});

test('подсказка: у верхнего края — под кнопкой; у правого — прижата к окну, стрелка на кнопке', () => {
    const pos = tipPlacement({
        anchor: { left: 1240, top: 10, width: 32, bottom: 42 },
        tip: { width: 240, height: 48 },
        view: VIEW,
    });
    assert.equal(pos.placement, 'bottom');
    assert.equal(pos.top, 42 + 8);
    assert.equal(pos.left, 1280 - 8 - 240);
    // Стрелка смотрит на центр кнопки (1256), а не на центр пузыря.
    assert.equal(pos.arrowLeft, 1256 - pos.left);
    assert.ok(pos.arrowLeft <= 240 - 12);
});

test('подсказка: пузырь не выходит за левый край окна', () => {
    const pos = tipPlacement({
        anchor: { left: 4, top: 300, width: 24, bottom: 324 },
        tip: { width: 260, height: 40 },
        view: VIEW,
    });
    assert.equal(pos.left, 8);
    assert.equal(pos.arrowLeft, 12);     // не уезжает в скругление угла
});

test('подсказка ждёт мышь, а не вспыхивает от проезжающего курсора', () => {
    assert.ok(TIP_SHOW_DELAY_MS >= 300 && TIP_SHOW_DELAY_MS <= 700);
});

test('фокус показывает подсказку, только если его перевёл Tab', () => {
    // Tab только что — фокус пришёл от него.
    assert.equal(focusCameFromTab(1000, 1010), true);
    assert.equal(focusCameFromTab(1000, 1000 + TAB_FOCUS_WINDOW_MS), true);
    // Escape в панели «Фильтр» вернул фокус на кнопку — Tab не нажимали.
    assert.equal(focusCameFromTab(null, 5000), false);
    // Tab был давно: фокус вернул скрипт, а не переход.
    assert.equal(focusCameFromTab(1000, 1000 + TAB_FOCUS_WINDOW_MS + 1), false);
    assert.equal(focusCameFromTab(2000, 1000), false);
});

/* ── Раздел целиком: несколько кадров, на запросы отвечает тест ──────────── */

const { WikiOffices } = await loadSection({
    lab: true,
    name: 'wiki-offices',
    entry: `export { default as WikiOffices } from ${JSON.stringify(path('src/components/wiki/WikiOffices.jsx'))};`,
});

const officesProps = { base: BASE, headers: HEADERS, showToast: noop };
const officeCalls = (from) => calls.slice(from).filter((call) => call.url === `${BASE}/offices`);

/* Раздел грузит список через setTimeout (250 мс дребезга у поиска, 0 без него):
   дать таймеру сработать, потом прожить кадры ответа. */
const flush = async (section) => {
    await new Promise((resolve) => { setTimeout(resolve, 5); });
    await section.settle();
};

/* Первый кадр и ответ на первую загрузку раздела. */
const opened = async (props, answer) => {
    const mark = calls.length;
    const section = mount(WikiOffices, props);
    await flush(section);
    const first = officeCalls(mark);
    assert.equal(first.length, 1, 'раздел не запросил офисы ровно один раз');
    first[0].answer(answer);
    pending('/parks', mark).answer({ items: [] });
    await section.settle();
    return { section, first: first[0] };
};

test('управляющий: «Офисы | Архив 2» в ряду инструментов; первый запрос — без архивных', async () => {
    const mark = calls.length;
    const section = mount(WikiOffices, officesProps);
    try {
        await flush(section);
        const first = officeCalls(mark);
        assert.equal(first.length, 1);
        assert.equal(first[0].params.archived, undefined, 'архивные снова примешаны к действующим');
        first[0].answer({ items: [office()], cities: [], can_manage: true, archived_count: 2, date: '2026-11-09' });
        pending('/parks', mark).answer({ items: [] });
        await section.settle();

        const html = section.html;
        assert.match(html, /role="tablist" aria-label="Действующие офисы или архив"/);
        assert.match(html, /role="tab" aria-selected="true"[^>]*>Офисы</);
        assert.match(html, /role="tab" aria-selected="false"[^>]*>Архив<span[^>]*>2<\/span>/);
        assert.match(html, /data-tip="Офисы, убранные в архив\. Отсюда их можно вернуть"/);
        // Подсказки в шапке и ряду инструментов.
        assert.match(html, /data-tip="Статус офисов на предыдущий день"/);
        assert.match(html, /data-tip="Выбрать день, на который показать статус офисов"/);
        assert.match(html, /data-tip="Сортировка и отбор по городу и таксопарку"/);
        assert.match(html, /data-tip="Таблица: статус всех офисов одним списком"/);
        assert.match(html, /data-tip="Добавить новый офис в справочник"/);
        assert.match(html, /data-tip="Показать только открытые офисы"/);
        assert.doesNotMatch(html, / title="/, 'системная подсказка title легла бы поверх нашей');
    } finally {
        section.unmount();
    }
});

test('пустой архив — без числа на вкладке; «Архив 0» был бы шумом', async () => {
    const { section } = await opened(officesProps,
        { items: [office()], cities: [], can_manage: true, archived_count: 0 });
    try {
        assert.match(section.html, /role="tab" aria-selected="false"[^>]*>Архив<\/button>/);
    } finally {
        section.unmount();
    }
});

test('читатель справочника: ни вкладки «Архив», ни кнопок правки', async () => {
    const { section } = await opened(officesProps,
        { items: [office()], cities: [], can_manage: false });
    try {
        assert.doesNotMatch(section.html, /role="tablist"/);
        assert.doesNotMatch(section.html, /Убрать в архив|Добавить новый офис/);
    } finally {
        section.unmount();
    }
});

test('вкладка iCORE Phone (readOnly): архива нет даже у того, кто вправе править', async () => {
    const { section } = await opened({ ...officesProps, readOnly: true },
        { items: [office()], cities: [], can_manage: true, archived_count: 3 });
    try {
        assert.doesNotMatch(section.html, /role="tablist"/);
        assert.doesNotMatch(section.html, />Архив</);
    } finally {
        section.unmount();
    }
});
