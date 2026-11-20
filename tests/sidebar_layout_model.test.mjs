/* Личная раскладка сайдбара (свой порядок и папки): данные, слияние с
 * меню, перенос и геометрия перетаскивания.
 *
 * Что сторожим главным образом:
 *  - раскладка накладывается на меню, ничего в нём не теряя: раздел, которого
 *    раскладка не знает, встаёт рядом со стандартным соседом, а не пропадает;
 *  - раздела, которого нет в меню сейчас, раскладка не рисует, но и не
 *    забывает — вернулся, и он на своём месте;
 *  - перенос не ломает форму (папка в папку не кладётся, пустые карточки
 *    исчезают), раздел на раздел — папка из двух, опустевшая папка исчезает,
 *    бросок ниже последнего — новая карточка;
 *  - щель при перетаскивании идёт за курсором и не дрожит, а над серединой
 *    строки замирает — иначе до папки и до раздела было бы не дотянуться. */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    arrangeRows,
    cleanText,
    createFolder,
    deleteFolder,
    folderRef,
    layoutSignature,
    locateEntry,
    mergeLayout,
    moveEntry,
    newFolderId,
    nextFolderName,
    normalizeLayout,
    renameFolder,
    sameLayout,
    MAX_ENTRIES,
    completeLayout,
    layoutItemKeys,
    placeEntry,
    pruneEmptyFolders,
    removeEntry,
    stackEntries,
} from '../src/components/sidebar/sidebarLayoutModel.js';
import {
    compactBottom,
    compactRows,
    dropTarget,
    gapTop,
    pointerZone,
    rowShifts,
    snapGapForFolder,
    unitHeight,
    keyboardGap,
    NEST_SHIFT_PX,
    NEW_BLOCK_HYSTERESIS_PX,
    NEW_BLOCK_REACH_PX,
} from '../src/components/sidebar/sidebarDragMath.js';

const DEFAULT = [
    ['Отметки', 'Учет сотрудников', 'QR доступ'],
    ['Вики', 'Курсы', 'Задачи'],
    ['Графики работы', 'Учет часов'],
];

const layoutOf = (blocks, folders = {}) => ({ v: 1, blocks, folders });
const keysOf = (rows) => rows.map((row) => (row.type === 'hr' ? '—' : (row.type === 'folder' ? `[${row.name}]` : row.key)));

test('без личной раскладки меню остаётся стандартным', () => {
    const merged = mergeLayout(null, DEFAULT);
    assert.deepEqual(merged.blocks, DEFAULT);
    assert.deepEqual(keysOf(arrangeRows(merged, new Set(DEFAULT.flat()))), [
        'Отметки', 'Учет сотрудников', 'QR доступ', '—',
        'Вики', 'Курсы', 'Задачи', '—',
        'Графики работы', 'Учет часов',
    ]);
});

test('раздел, которого раскладка не знает, встаёт за своим стандартным соседом', () => {
    const saved = layoutOf([['Задачи', 'Вики', 'Курсы'], ['QR доступ', 'Отметки'], ['Графики работы']]);
    const withNew = [
        ['Отметки', 'Учет сотрудников', 'QR доступ'],
        ['Вики', 'Курсы', 'Задачи'],
        ['Графики работы', 'Учет часов'],
    ];
    const merged = mergeLayout(saved, withNew);
    // «Учет сотрудников» — после «Отметок» (стандартный предшественник), где бы
    // «Отметки» у человека ни стояли; «Учет часов» — после «Графиков работы».
    assert.deepEqual(merged.blocks, [
        ['Задачи', 'Вики', 'Курсы'],
        ['QR доступ', 'Отметки', 'Учет сотрудников'],
        ['Графики работы', 'Учет часов'],
    ]);
});

test('новый раздел рядом с соседом из папки встаёт за папкой, а не в неё', () => {
    const saved = layoutOf([['folder:fa1', 'Задачи']], { fa1: { name: 'Своё', items: ['Вики'] } });
    const merged = mergeLayout(saved, [['Вики', 'Курсы', 'Задачи']]);
    assert.deepEqual(merged.blocks, [['folder:fa1', 'Курсы', 'Задачи']]);
    assert.deepEqual(merged.folders.fa1.items, ['Вики']);
});

test('целиком новая карточка встаёт после карточки своего стандартного предшественника', () => {
    const saved = layoutOf([['Графики работы'], ['Отметки']]);
    const merged = mergeLayout(saved, [['Отметки'], ['Вики', 'Курсы'], ['Графики работы']]);
    assert.deepEqual(merged.blocks, [['Графики работы'], ['Отметки'], ['Вики', 'Курсы']]);
});

test('смена роли: раскладка не знает ни одного раздела — меню стандартное, по карточкам', () => {
    const saved = layoutOf([['Касания', 'Лиды OLX'], ['Посылки']]);
    const merged = mergeLayout(saved, DEFAULT);
    const rows = arrangeRows(merged, new Set(DEFAULT.flat()));
    assert.deepEqual(keysOf(rows), keysOf(arrangeRows(mergeLayout(null, DEFAULT), new Set(DEFAULT.flat()))));
    // А чужие ключи не потеряны: вернётся роль — вернётся и порядок.
    assert.ok(merged.blocks.flat().includes('Касания'));
});

test('раздел, которого нет в меню сейчас, не рисуется, но остаётся на своём месте', () => {
    const saved = layoutOf([['Посылки', 'Задачи', 'Вики']]);
    const present = new Set(['Задачи', 'Вики']);
    const merged = mergeLayout(saved, [['Задачи', 'Вики']]);
    assert.deepEqual(keysOf(arrangeRows(merged, present)), ['Задачи', 'Вики']);
    const moved = moveEntry(merged, 'Вики', { block: 0, folder: null, before: 'Задачи' });
    assert.deepEqual(moved.blocks, [['Посылки', 'Вики', 'Задачи']]);
});

test('папка без единого видимого раздела не рисуется — кроме режима правки', () => {
    const saved = layoutOf([['folder:fa1', 'Задачи']], { fa1: { name: 'Пустая', items: ['Посылки'] } });
    const merged = mergeLayout(saved, [['Задачи']]);
    assert.deepEqual(keysOf(arrangeRows(merged, new Set(['Задачи']))), ['Задачи']);
    assert.deepEqual(keysOf(arrangeRows(merged, new Set(['Задачи']), { editing: true })), ['[Пустая]', 'Задачи']);
});

test('черта рисуется только между непустыми карточками', () => {
    const merged = layoutOf([['Посылки'], ['Задачи'], ['Вики']]);
    assert.deepEqual(keysOf(arrangeRows(merged, new Set(['Задачи', 'Вики']))), ['Задачи', '—', 'Вики']);
});

test('перенос: внутри карточки, в другую, в папку и из неё; пустая карточка исчезает', () => {
    let layout = layoutOf([['А', 'Б', 'В'], ['Г']], {});
    layout = moveEntry(layout, 'В', { block: 0, folder: null, before: 'А' });
    assert.deepEqual(layout.blocks, [['В', 'А', 'Б'], ['Г']]);
    layout = moveEntry(layout, 'Г', { block: 0, folder: null, before: null });
    assert.deepEqual(layout.blocks, [['В', 'А', 'Б', 'Г']], 'опустевшая карточка не остаётся');
    layout = createFolder(layout, { id: 'fz1', name: 'Папка', block: 0, before: 'В' });
    layout = moveEntry(layout, 'А', { block: 0, folder: 'fz1', before: null });
    layout = moveEntry(layout, 'Б', { block: 0, folder: 'fz1', before: 'А' });
    assert.deepEqual(layout.folders.fz1.items, ['Б', 'А']);
    assert.deepEqual(locateEntry(layout, 'А'), { block: 0, folder: 'fz1', index: 1 });
    layout = moveEntry(layout, 'Б', { block: 0, folder: null, before: null });
    assert.deepEqual(layout.blocks, [['folder:fz1', 'В', 'Г', 'Б']]);
});

test('папку в папку не положить', () => {
    let layout = layoutOf([['folder:fa1', 'folder:fb2']], { fa1: { name: 'A', items: [] }, fb2: { name: 'B', items: [] } });
    const same = moveEntry(layout, 'folder:fb2', { block: 0, folder: 'fa1', before: null });
    assert.equal(same, layout);
    layout = moveEntry(layout, 'folder:fb2', { block: 0, folder: null, before: 'folder:fa1' });
    assert.deepEqual(layout.blocks, [['folder:fb2', 'folder:fa1']]);
});

test('удалили папку — разделы остаются на её месте и в том же порядке', () => {
    const layout = layoutOf([['Х', 'folder:fa1', 'Я']], { fa1: { name: 'A', items: ['П', 'Р'] } });
    const next = deleteFolder(layout, 'fa1');
    assert.deepEqual(next.blocks, [['Х', 'П', 'Р', 'Я']]);
    assert.deepEqual(next.folders, {});
});

test('имя папки чистится, пустое не принимается, новое — по образцу Finder', () => {
    let layout = createFolder(layoutOf([['А']]), { id: 'fa1', name: '  Мои\n разделы  ', block: 0, before: 'А' });
    assert.equal(layout.folders.fa1.name, 'Мои разделы');
    assert.equal(renameFolder(layout, 'fa1', '   '), layout, 'пустое имя — без изменений');
    layout = renameFolder(layout, 'fa1', 'Чаты');
    assert.equal(layout.folders.fa1.name, 'Чаты');
    assert.equal(nextFolderName(layout), 'Новая папка');
    const two = createFolder(layout, { id: 'fb2', name: nextFolderName(layout), block: 0, before: null });
    assert.equal(nextFolderName(two), 'Новая папка 2');
    assert.equal(cleanText('ab'.repeat(30), 40).length, 40);
    // Обрезка по символам, а не по половинкам UTF-16: эмодзи не рвётся.
    assert.equal(Array.from(cleanText('😀'.repeat(50), 40)).length, 40);
});

test('id папки — по форме, которую примет сервер', () => {
    for (let i = 0; i < 50; i += 1) {
        assert.match(newFolderId(Date.now() + i * 997, i / 50), /^f[a-z0-9]{1,15}$/);
    }
});

test('раскладка из хранилища — чужие данные: всё не по форме отбрасывается', () => {
    assert.equal(normalizeLayout(null), null);
    assert.equal(normalizeLayout({ v: 2, blocks: [] }), null);
    const dirty = normalizeLayout({
        v: 1,
        blocks: [
            ['А', 'А', 'folder:fa1', 'folder:fa1', 'folder:bad id', 'folder:nope', 7, '  Б', 'В'],
            [],
            'не список',
        ],
        folders: { fa1: { name: '', items: ['В', 'Г', 'Г'] }, nope: 'строка' },
    });
    // Первое вхождение ключа выигрывает: «В» встретилось в папке раньше, чем в
    // хвосте карточки, — второе отброшено.
    assert.deepEqual(dirty.blocks, [['А', 'folder:fa1']]);
    assert.deepEqual(dirty.folders, { fa1: { name: 'Новая папка', items: ['В', 'Г'] } });
    const huge = { v: 1, blocks: [Array.from({ length: MAX_ENTRIES + 50 }, (_, i) => `Раздел ${i}`)], folders: {} };
    assert.equal(normalizeLayout(huge).blocks[0].length, MAX_ENTRIES);
});

test('сравнение раскладок не зависит от порядка ключей папок', () => {
    const a = layoutOf([['folder:fa1', 'folder:fb2']], { fa1: { name: 'A', items: [] }, fb2: { name: 'B', items: [] } });
    const b = layoutOf([['folder:fa1', 'folder:fb2']], { fb2: { name: 'B', items: [] }, fa1: { name: 'A', items: [] } });
    assert.ok(sameLayout(a, b));
    assert.notEqual(layoutSignature(a), layoutSignature(layoutOf([['folder:fb2', 'folder:fa1']], a.folders)));
    assert.equal(folderRef('fa1'), 'folder:fa1');
});

test('раздел на раздел — папка из двух на месте того, на который положили', () => {
    const layout = layoutOf([['А', 'Б', 'В'], ['Г']], {});
    // «В» (из меню) положили на «А»: папка на месте «А», внутри «А» и «В».
    const stacked = stackEntries(layout, 'А', 'В', { id: 'fs1', name: 'Новая папка' });
    assert.deepEqual(stacked.blocks, [['folder:fs1', 'Б'], ['Г']]);
    assert.deepEqual(stacked.folders.fs1, { name: 'Новая папка', items: ['А', 'В'] });
    // Принесённый стоял ПЕРЕД целью в той же карточке — место цели ищется заново.
    const before = stackEntries(layout, 'В', 'А', { id: 'fs2', name: 'П' });
    assert.deepEqual(before.blocks, [['Б', 'folder:fs2'], ['Г']]);
    assert.deepEqual(before.folders.fs2.items, ['В', 'А']);
    // С панели (его нет в раскладке) — просто вторым в папку.
    const fromPalette = stackEntries(layoutOf([['Г']]), 'Г', 'Д', { id: 'fs3', name: 'П' });
    assert.deepEqual(fromPalette.blocks, [['folder:fs3']]);
    assert.deepEqual(fromPalette.folders.fs3.items, ['Г', 'Д']);
    // Унесли последний раздел другой папки — та исчезает тем же шагом.
    const withFolder = layoutOf([['folder:fa1', 'Б']], { fa1: { name: 'Своё', items: ['А'] } });
    const moved = stackEntries(withFolder, 'Б', 'А', { id: 'fs4', name: 'П' });
    assert.deepEqual(moved.blocks, [['folder:fs4']]);
    assert.deepEqual(Object.keys(moved.folders), ['fs4']);
    // Нельзя: на раздел в папке (вложенных папок нет), на самого себя, чужой id, папку.
    const nested = layoutOf([['folder:fa1', 'Б']], { fa1: { name: 'Своё', items: ['А'] } });
    assert.equal(stackEntries(nested, 'А', 'Б', { id: 'fs5', name: 'П' }), nested);
    assert.equal(stackEntries(layout, 'А', 'А', { id: 'fs6', name: 'П' }), layout);
    assert.equal(stackEntries(layout, 'А', 'Б', { id: 'bad id', name: 'П' }), layout);
    assert.equal(stackEntries(nested, 'Б', 'folder:fa1', { id: 'fs7', name: 'П' }), nested);
});

test('опустевшая папка исчезает, а папку со скрытым сейчас разделом держит он', () => {
    const layout = layoutOf([['folder:fa1', 'А'], ['folder:fb2']], {
        fa1: { name: 'Пустая', items: [] },
        fb2: { name: 'Со скрытым', items: ['Посылки'] },
    });
    const pruned = pruneEmptyFolders(layout);
    assert.deepEqual(pruned.blocks, [['А'], ['folder:fb2']]);
    assert.deepEqual(Object.keys(pruned.folders), ['fb2']);
    const full = layoutOf([['А']]);
    assert.equal(pruneEmptyFolders(full), full, 'нечего убирать — та же раскладка');
});

test('бросок ниже последнего раздела — новая карточка в самом низу', () => {
    const layout = layoutOf([['А', 'Б'], ['В']], { fa1: { name: 'П', items: ['Г'] } });
    layout.blocks[1].push('folder:fa1');
    assert.deepEqual(moveEntry(layout, 'А', { newBlock: true }).blocks, [['Б'], ['В', 'folder:fa1'], ['А']]);
    // Из папки — на верхний уровень своей карточкой; сама папка тоже может уехать.
    const out = moveEntry(layout, 'Г', { newBlock: true });
    assert.deepEqual(out.blocks, [['А', 'Б'], ['В', 'folder:fa1'], ['Г']]);
    assert.deepEqual(out.folders.fa1.items, []);
    assert.deepEqual(moveEntry(layout, 'folder:fa1', { newBlock: true }).blocks, [['А', 'Б'], ['В'], ['folder:fa1']]);
    // Последний в своей карточке и так стоит внизу отдельно — ничего не меняется.
    const alone = layoutOf([['А'], ['Б']]);
    assert.ok(sameLayout(moveEntry(alone, 'Б', { newBlock: true }), alone));
    // С панели.
    assert.deepEqual(placeEntry(layoutOf([['А']]), 'Б', { newBlock: true }).blocks, [['А'], ['Б']]);
    assert.deepEqual([...layoutItemKeys(layout)].sort(), ['А', 'Б', 'В', 'Г']);
});

test('бросок между двумя карточками — новая карточка ровно между ними', () => {
    const layout = layoutOf([['А', 'Б'], ['В'], ['Г', 'Д']]);
    assert.deepEqual(moveEntry(layout, 'Д', { newBlock: true, at: 1 }).blocks, [['А', 'Б'], ['Д'], ['В'], ['Г']]);
    assert.deepEqual(moveEntry(layout, 'А', { newBlock: true, at: 2 }).blocks, [['Б'], ['В'], ['А'], ['Г', 'Д']]);
    // Опустевшая карточка, откуда унесли, уходит уже после вставки — новая встаёт, куда бросили.
    assert.deepEqual(moveEntry(layout, 'В', { newBlock: true, at: 2 }).blocks, [['А', 'Б'], ['В'], ['Г', 'Д']]);
    assert.ok(sameLayout(moveEntry(layout, 'В', { newBlock: true, at: 1 }), layout), 'одиночку на своё же место — без изменений');
    // С панели и за краями.
    assert.deepEqual(placeEntry(layout, 'Е', { newBlock: true, at: 1 }).blocks, [['А', 'Б'], ['Е'], ['В'], ['Г', 'Д']]);
    assert.deepEqual(placeEntry(layout, 'Е', { newBlock: true, at: 99 }).blocks.at(-1), ['Е']);
    assert.deepEqual(placeEntry(layout, 'Е', { newBlock: true, at: -3 }).blocks[0], ['Е']);
});

test('раздел с панели встаёт куда бросили, не дублируется; уносится обратно', () => {
    let layout = layoutOf([]);
    layout = placeEntry(layout, 'Б');
    layout = placeEntry(layout, 'А', { block: 0, folder: null, before: 'Б' });
    layout = placeEntry(layout, 'В', { block: 0, folder: null, after: 'А' });
    assert.deepEqual(layout.blocks, [['А', 'В', 'Б']]);
    assert.equal(placeEntry(layout, 'А'), layout, 'раздел уже в меню — без изменений');
    layout = { ...layout, blocks: [['folder:fa1', ...layout.blocks[0]]], folders: { fa1: { name: 'П', items: [] } } };
    layout = placeEntry(layout, 'Г', { block: 0, folder: 'fa1', before: null });
    assert.deepEqual(layout.folders.fa1.items, ['Г']);
    layout = removeEntry(layout, 'Г');
    assert.deepEqual(layout.folders.fa1.items, []);
    layout = removeEntry(layout, 'В');
    assert.deepEqual(layout.blocks, [['folder:fa1', 'А', 'Б']]);
    assert.equal(removeEntry(layout, 'folder:fa1'), layout, 'папку на панель не унести');
});

test('«Готово»: собранное — сверху, остальное — следом в обычном порядке по карточкам', () => {
    const working = layoutOf([['В', 'folder:fa1']], { fa1: { name: 'П', items: ['Б'] } });
    const result = completeLayout(working, [['А', 'Б'], ['В', 'Г', 'Д'], ['Е']]);
    assert.deepEqual(result.blocks, [['В', 'folder:fa1'], ['А'], ['Г', 'Д'], ['Е']]);
    assert.deepEqual(completeLayout(layoutOf([]), [['А', 'Б'], ['В']]).blocks, [['А', 'Б'], ['В']], 'ничего не перенесли — стандартное меню');
});

/* ── Геометрия перетаскивания ──────────────────────────────────────────── */

const H = 40;
// Строки списка: rows[i] = { kind, entry, folder, id, open, block, top, height }.
const buildRows = (spec) => {
    let top = 0;
    return spec.map((row) => {
        const height = row.kind === 'hr' ? 10 : H;
        const out = { folder: null, id: null, open: false, block: 0, ...row, top, height };
        top += height;
        return out;
    });
};

test('расстановка без поднятой строки: дыра закрыта, расступаются только строки за щелью', () => {
    const rows = buildRows([
        { kind: 'item', entry: 'А' }, { kind: 'item', entry: 'Б' }, { kind: 'item', entry: 'В' },
    ]);
    const unit = { start: 0, end: 0 };
    const compact = compactRows(rows, unit);
    assert.deepEqual(compact.map((r) => r.ctop), [0, 40]);
    assert.equal(unitHeight(rows, unit), 40);
    assert.equal(gapTop(compact, 2, rows), 80);
    assert.equal(compactBottom(compact, rows), 80);
    assert.equal(compactBottom([], rows), 0, 'пустое меню — от его верха');
    const shifts = rowShifts(rows, compact, 1, 40);
    assert.equal(shifts.get(1), -40);
    assert.equal(shifts.get(2), 0);
});

/* Меню для зон курсора: А, Б, В, папка П (раскрыта: Р, С), черта, Д.
   Тащим «Б» (строка 1) — щель стоит на его месте. */
const zoneRows = () => buildRows([
    { kind: 'item', entry: 'А' },
    { kind: 'item', entry: 'Б' },
    { kind: 'item', entry: 'В' },
    { kind: 'folder', entry: 'folder:fp', id: 'fp', open: true },
    { kind: 'item', entry: 'Р', folder: 'fp' },
    { kind: 'item', entry: 'С', folder: 'fp' },
    { kind: 'hr', block: 1 },
    { kind: 'item', entry: 'Д', block: 1 },
]);
const ITEM = { kind: 'item' };

test('щель идёт за курсором: четверти строки — перед ней и за ней, сама щель ничего не меняет', () => {
    const compact = compactRows(zoneRows(), { start: 1, end: 1 });
    // На экране при щели 1: А 0–40, щель 40–80, В 80–120, П 120–160, Р 160–200, С 200–240, черта 240–250, Д 250–290.
    const at = (pointerY, shownGap = 1) => pointerZone(compact, { pointerY, shownGap, height: H, dragged: ITEM });
    assert.equal(at(60).gap, 1, 'курсор в щели — на месте');
    assert.equal(at(85).gap, 1, 'верхняя четверть «В» — перед ним (там и стоим)');
    assert.equal(at(115).gap, 2, 'нижняя четверть «В» — за ним');
    assert.equal(at(5).gap, 0, 'верхняя четверть «А» — перед ним');
    assert.equal(at(-10).gap, 0, 'выше меню — в начало');
    // Щель уже за «В» (2): «В» поднялся на 40–80, щель — 80–120; тот же курсор в щели — стоим.
    assert.equal(at(115, 2).gap, 2);
    assert.equal(at(100, 2).gap, 2);
    // Конец верхней карточки и начало нижней — четвертями крайних строк.
    assert.equal(at(238).gap, 5, 'нижняя четверть «С» — в конец карточки');
    assert.equal(at(253).gap, 6, 'верхняя четверть «Д» — в начало следующей');
});

test('черта между карточками — новая карточка на этом месте; раздвинутая черта держит курсор', () => {
    /* Просьба владельца 20.11: «нельзя между двумя существующими блоками
       создать другой — нужно спускаться вниз; перенести в центр к двум блокам —
       должен создаваться новый блок». */
    const rows = zoneRows();
    const compact = compactRows(rows, { start: 1, end: 1 });
    // Щель 1: черта (№5) на экране 240–250.
    const at = (pointerY, shownGap, shownSplit = null) => pointerZone(compact, { pointerY, shownGap, shownSplit, height: H, dragged: ITEM });
    assert.deepEqual(at(243, 1), { gap: null, into: null, center: null, newBlock: false, split: 5 });
    assert.equal(at(248, 1).split, 5, 'вся черта — одно место, без половин');
    // Черта раздвинута: под ней пустая карточка и воздух (200–200+10+40+10), «Д» отъехал на 260–300.
    for (const y of [201, 215, 245, 259]) {
        assert.equal(at(y, null, 5).split, 5, `курсор на ${y} не соскальзывает с раздвинутой черты`);
    }
    assert.deepEqual(at(265, null, 5), { gap: 6, into: null, center: null, newBlock: false, split: null }, 'ниже — в начало нижней карточки');
    assert.equal(at(195, null, 5).gap, 5, 'выше — в конец верхней карточки');
    // Строки под чертой отъезжают на её место — карточку строки и воздух под ней.
    const shifts = rowShifts(rows, compact, null, H, 5);
    assert.equal(shifts.get(7), 210 + H + 10 - 250, '«Д» — на высоту строки и воздуха ниже');
    assert.equal(shifts.get(6), 200 - 240, 'черта — где стоит без поднятой строки, раздвижка её не трогает');
    // Папку тоже можно бросить между карточками.
    assert.equal(pointerZone(compact, { pointerY: 243, shownGap: 1, height: H, dragged: { kind: 'folder' } }).split, 5);
});

test('над серединой строки щель замирает: раздел — папка из двух, папка — «в неё», раздел папки — ничего', () => {
    const compact = compactRows(zoneRows(), { start: 1, end: 1 });
    const at = (pointerY, shownGap, dragged = ITEM) => pointerZone(compact, { pointerY, shownGap, height: H, dragged });
    // Середина «В» (щель вплотную над ним): щель на месте, «В» — кандидат в папку.
    assert.deepEqual(at(100, 1), { gap: 1, into: null, center: 1, newBlock: false, split: null });
    // Середина «А» (щель вплотную под ним) — тоже.
    assert.deepEqual(at(20, 1), { gap: 1, into: null, center: 0, newBlock: false, split: null });
    // Середина шапки папки (щель за «В», вплотную над папкой: шапка на 120–160) — в папку.
    assert.deepEqual(at(140, 2), { gap: 2, into: 'fp', center: null, newBlock: false, split: null });
    // Середина раздела в папке (щель вплотную под «Р»: он на 120–160) — щель
    // замирает, но папки из него не делают: вложенных папок нет.
    assert.deepEqual(at(140, 4), { gap: 4, into: null, center: null, newBlock: false, split: null });
    // Папку несут — ни «в папку», ни «на раздел».
    assert.deepEqual(at(100, 1, { kind: 'folder' }), { gap: 1, into: null, center: null, newBlock: false, split: null });
    // Курсор перескочил далеко — щель сперва подходит к строке по половинам.
    assert.deepEqual(at(265, 1), { gap: 6, into: null, center: null, newBlock: false, split: null });
    assert.deepEqual(at(278, 1), { gap: 7, into: null, center: null, newBlock: false, split: null });
});

test('с панели (щели ещё нет): над разделом щель открывается по половинам, над папкой — сразу в неё', () => {
    const rows = zoneRows();
    const compact = rows.map((row, index) => ({ ...row, index, ctop: row.top }));
    const at = (pointerY) => pointerZone(compact, { pointerY, shownGap: null, height: H, dragged: ITEM });
    assert.deepEqual(at(55), { gap: 1, into: null, center: null, newBlock: false, split: null }, 'середина «Б», верхняя половина — перед ним');
    assert.deepEqual(at(65), { gap: 2, into: null, center: null, newBlock: false, split: null }, 'нижняя половина — за ним');
    assert.deepEqual(at(140), { gap: null, into: 'fp', center: null, newBlock: false, split: null });
    assert.deepEqual(pointerZone([], { pointerY: 30, shownGap: null, height: H, dragged: ITEM }).gap, 0, 'пустое меню — в первую карточку');
});

test('ниже последнего раздела: у самого низа — в конец, дальше — новая карточка, назад — с запасом', () => {
    const compact = compactRows(zoneRows(), { start: 1, end: 1 });
    // Низ меню без «Б» — 250 (Д: 210–250). Щель в конце: 250–290.
    const at = (pointerY, wasNewBlock = false) => pointerZone(compact, { pointerY, shownGap: 7, height: H, dragged: ITEM, wasNewBlock });
    const reach = 250 + H + NEW_BLOCK_REACH_PX;
    assert.equal(at(270).gap, 7, 'в щели в конце — в конец последней карточки');
    assert.deepEqual(at(reach - 1), { gap: 7, into: null, center: null, newBlock: false, split: null });
    assert.deepEqual(at(reach), { gap: null, into: null, center: null, newBlock: true, split: null });
    assert.equal(at(reach - 1, true).newBlock, true, 'вернулись чуть выше порога — всё ещё новая карточка');
    assert.equal(at(reach - NEW_BLOCK_HYSTERESIS_PX - 1, true).newBlock, false);
});

test('щель между разделами папки — в папку; под последним — по сдвигу вбок и по тому, откуда взяли', () => {
    const rows = buildRows([
        { kind: 'folder', entry: 'folder:fa1', id: 'fa1', open: true },
        { kind: 'item', entry: 'П', folder: 'fa1' },
        { kind: 'item', entry: 'Р', folder: 'fa1' },
        { kind: 'item', entry: 'Х' },
        { kind: 'item', entry: 'Я' },
    ]);
    // Тащим «Я» (снаружи).
    const outside = { kind: 'item', entry: 'Я', folder: null, block: 0 };
    const compactY = compactRows(rows, { start: 4, end: 4 });
    assert.deepEqual(dropTarget(compactY, 2, outside), { block: 0, folder: 'fa1', before: 'Р' });
    assert.deepEqual(dropTarget(compactY, 3, outside), { block: 0, folder: null, before: 'Х' }, 'под папкой без сдвига — за папкой');
    assert.deepEqual(dropTarget(compactY, 3, outside, NEST_SHIFT_PX), { block: 0, folder: 'fa1', before: null, after: 'Р' }, 'сдвиг вправо — в конец папки');
    // Тащим «Р» (последний раздел папки): без сдвига остаётся в папке.
    const inside = { kind: 'item', entry: 'Р', folder: 'fa1', block: 0 };
    const compactR = compactRows(rows, { start: 2, end: 2 });
    assert.deepEqual(dropTarget(compactR, 2, inside), { block: 0, folder: 'fa1', before: null, after: 'П' });
    assert.deepEqual(dropTarget(compactR, 2, inside, -NEST_SHIFT_PX), { block: 0, folder: null, before: 'Х' }, 'сдвиг влево — из папки');
});

test('щель на границе карточек решает, в какую карточку встать', () => {
    const rows = buildRows([
        { kind: 'item', entry: 'А', block: 0 },
        { kind: 'hr', block: 1 },
        { kind: 'item', entry: 'Б', block: 1 },
        { kind: 'item', entry: 'В', block: 1 },
    ]);
    const dragged = { kind: 'item', entry: 'В', folder: null, block: 1 };
    const compact = compactRows(rows, { start: 3, end: 3 });
    assert.deepEqual(dropTarget(compact, 1, dragged), { block: 0, folder: null, before: null, after: 'А' }, 'над чертой — в конец верхней');
    assert.deepEqual(dropTarget(compact, 2, dragged), { block: 1, folder: null, before: 'Б' }, 'под чертой — в начало нижней');
});

test('папку тащат мимо раскрытой папки — щель уходит к ближнему краю', () => {
    const rows = buildRows([
        { kind: 'folder', entry: 'folder:fa1', id: 'fa1', open: true },
        { kind: 'item', entry: 'П', folder: 'fa1' },
        { kind: 'item', entry: 'Р', folder: 'fa1' },
        { kind: 'item', entry: 'С', folder: 'fa1' },
        { kind: 'folder', entry: 'folder:fb2', id: 'fb2', open: false },
    ]);
    const compact = compactRows(rows, { start: 4, end: 4 });
    assert.equal(snapGapForFolder(compact, 1), 0, 'у верха — перед шапкой');
    assert.equal(snapGapForFolder(compact, 3), 4, 'у низа — за последним разделом');
    const dragged = { kind: 'folder', entry: 'folder:fb2', folder: null, block: 0 };
    assert.deepEqual(dropTarget(compact, 4, dragged), { block: 0, folder: null, before: null, after: 'folder:fa1' });
    assert.deepEqual(dropTarget(compact, 0, dragged), { block: 0, folder: null, before: 'folder:fa1' });
});

test('«в конец» — сразу за последним видимым: бросок на то же место ничего не меняет', () => {
    /* Разбор 20.11: за последним видимым разделом в раскладке стоят спрятанные
       сейчас (другой отдел в селекторе), и «в конец массива» уводило раздел за
       них — равный на глаз бросок менял раскладку. */
    const layout = layoutOf([['А', 'Б', 'Скрытый'], ['В']], { fa1: { name: 'Папка', items: ['П', 'Р', 'Тайный'] } });
    layout.blocks[1].push('folder:fa1');
    const rows = buildRows([
        { kind: 'item', entry: 'А', block: 0 },
        { kind: 'item', entry: 'Б', block: 0 },
        { kind: 'hr', block: 1 },
        { kind: 'item', entry: 'В', block: 1 },
        { kind: 'folder', entry: 'folder:fa1', id: 'fa1', open: true, block: 1 },
        { kind: 'item', entry: 'П', folder: 'fa1', block: 1 },
        { kind: 'item', entry: 'Р', folder: 'fa1', block: 1 },
    ]);
    const dragB = { kind: 'item', entry: 'Б', folder: null, block: 0 };
    const targetB = dropTarget(compactRows(rows, { start: 1, end: 1 }), 1, dragB);
    assert.ok(sameLayout(moveEntry(layout, 'Б', targetB), layout), 'карточка: «Б» осталась перед «Скрытым»');
    const dragR = { kind: 'item', entry: 'Р', folder: 'fa1', block: 1 };
    const targetR = dropTarget(compactRows(rows, { start: 6, end: 6 }), 6, dragR);
    assert.ok(sameLayout(moveEntry(layout, 'Р', targetR), layout), 'папка: «Р» осталась перед «Тайным»');
    // А перенос «В» в конец первой карточки встаёт за «Б», не за «Скрытый».
    const dragV = { kind: 'item', entry: 'В', folder: null, block: 1 };
    const moved = moveEntry(layout, 'В', dropTarget(compactRows(rows, { start: 3, end: 3 }), 2, dragV));
    assert.deepEqual(moved.blocks[0], ['А', 'Б', 'В', 'Скрытый']);
});

test('шаг клавиатурой учитывает величину и не выходит за края', () => {
    const compact = [{}, {}, {}, {}];
    assert.equal(keyboardGap(compact, { start: 2, end: 2 }, 1), 3);
    assert.equal(keyboardGap(compact, { start: 2, end: 2 }, 2), 4);
    assert.equal(keyboardGap(compact, { start: 2, end: 2 }, 3), 4, 'не дальше конца');
    assert.equal(keyboardGap(compact, { start: 2, end: 2 }, -3), 0, 'не выше начала');
});

test('чистка текста: NEL, BOM и одиночная половинка суррогата — в пробел, как на сервере', () => {
    for (const code of [0x85, 0xfeff, 0xd800, 0x9f, 0x07]) {
        assert.equal(cleanText(`Мои${String.fromCharCode(code)}разделы`, 40), 'Мои разделы', `код ${code.toString(16)}`);
    }
    // Настоящая пара суррогатов (эмодзи) остаётся символом.
    assert.equal(cleanText('Чаты 😀', 40), 'Чаты 😀');
    assert.equal(cleanText(`А${String.fromCharCode(0xa0, 0xa0)}Б`, 40), 'А Б');
});
