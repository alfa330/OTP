/*
 * Таблицы в редакторе статьи вики — логика src/components/wiki/tableTools.js.
 *
 * Схема та же, что у редактора: StarterKit + узлы таблицы TipTap, команды —
 * настоящие prosemirror-tables. Браузера в проекте нет, поэтому здесь всё, что
 * можно проверить без него: разбор текста с табуляциями, обратная выгрузка,
 * ручки строк и столбцов на объединённых ячейках, команды «+», дублирование,
 * перенос строк, сторож замороженных ширин. Сами ручки, меню, буфер обмена и
 * перетаскивание границы проверены стендом в Chrome.
 *
 * Что ломается молча и почему за этим следить.
 *
 * 1. РАЗБОР ВСТАВКИ. Текст из «Блокнота» с одной табуляцией-отступом не должен
 *    становиться таблицей, а выгрузка Excel с пустыми ячейками — терять
 *    столбцы. Ошибка в любую сторону видна только тому, кто вставлял.
 * 2. РУЧКИ НА ОБЪЕДИНЁННЫХ ЯЧЕЙКАХ. Ручка, поставленная в ячейку, которую
 *    накрывает объединение, выделила бы чужую строку.
 * 3. ЗАМОРОЖЕННЫЕ ШИРИНЫ. Новый столбец без ширины «размораживает» таблицу, и
 *    суженные автором столбцы у читателя распахиваются обратно.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { getSchema } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { TableCell, TableHeader } from '@tiptap/extension-table';
import { EditorState, TextSelection } from '@tiptap/pm/state';
import { CellSelection, TableMap, addColumnAfter, deleteRow, mergeCells, moveTableRow, tableEditing } from '@tiptap/pm/tables';
import { history, undo, redo } from '@tiptap/pm/history';
import { Plugin } from '@tiptap/pm/state';

import {
  RESET_WIDTHS_META, ROW_MIN_HEIGHT, activeTable, appendColumn, appendRow, canDuplicateRow,
  cellsEndingAtRow, columnSelection, columnWidths, duplicateRow, duplicateTable, gripSelection,
  guardFixedWidths, hasHeaderColumn, hasHeaderRow, isFixedLayout, isSpreadsheetHtml, moveTable,
  nearestBoundary, neighborTarget, newColumnWidth, rowSelection, setTableAlign, snapAlign, tableAlign,
  parseTabular, plural, resetTableSizes, rowAtCellEdge, rowHeights, selectionShape, setColumnWidths,
  setRowHeight, tableAround, tableFromRows, tableGrips, tableHasSizes, tableSizeLabel, tableToTabular,
  widthsFixed,
} from '../src/components/wiki/tableTools.js';
import { WikiTable, WikiTableRow } from '../src/components/wiki/WikiTableTools.js';

// Таблица и строка — те же, что у редактора: с признаком «ширины задал
// автор» и с высотой строки (WikiTableTools.js).
const schema = getSchema([StarterKit, WikiTable, WikiTableRow, TableHeader, TableCell]);
const { doc, paragraph } = schema.nodes;
const types = {
  table: schema.nodes.table, row: schema.nodes.tableRow, cell: schema.nodes.tableCell, header: schema.nodes.tableHeader,
};

const p = (text) => paragraph.create(null, text ? schema.text(text) : null);
const td = (text, attrs = null) => types.cell.create(attrs, p(text));
const th = (text, attrs = null) => types.header.create(attrs, p(text));
const tr = (...cells) => types.row.create(null, cells);
const table = (...rows) => types.table.create(null, rows);

/* Состояние с документом «абзац, таблица, абзац» и курсором в ячейке. */
const stateWith = (tableNode, { plugins = [] } = {}) => {
  const documentNode = doc.create(null, [p('до'), tableNode, p('после')]);
  const base = EditorState.create({ schema, doc: documentNode, plugins });
  const tablePos = documentNode.child(0).nodeSize;
  const firstCell = tablePos + 1 + TableMap.get(tableNode).map[0];
  return {
    state: base.apply(base.tr.setSelection(TextSelection.near(base.doc.resolve(firstCell + 1)))),
    tablePos,
  };
};

const run = (state, command) => {
  let next = state;
  const ok = command(state, (t) => { next = state.apply(t); });
  return { ok, state: next };
};

const cellTexts = (tableNode) => {
  const rows = [];
  tableNode.forEach((row) => {
    const cells = [];
    row.forEach((cell) => cells.push(cell.textContent));
    rows.push(cells);
  });
  return rows;
};

const sample = () => table(
  tr(th('Город'), th('Комиссия'), th('Тариф')),
  tr(td('Алматы'), td('3 %'), td('Эконом')),
  tr(td('Астана'), td('4 %'), td('Комфорт')),
);

/* ── Размер ──────────────────────────────────────────────────────────── */

test('подпись размера — с правильными окончаниями', () => {
  assert.equal(tableSizeLabel(1, 1), '1 столбец × 1 строка');
  assert.equal(tableSizeLabel(3, 2), '3 столбца × 2 строки');
  assert.equal(tableSizeLabel(5, 11), '5 столбцов × 11 строк');
  assert.equal(tableSizeLabel(10, 21), '10 столбцов × 21 строка');
  assert.equal(plural(14, 'a', 'b', 'c'), 'c');
  assert.equal(plural(22, 'a', 'b', 'c'), 'b');
});

/* ── Текст с табуляциями ─────────────────────────────────────────────── */

test('выгрузка Excel разбирается в строки, пустые ячейки на месте', () => {
  const rows = parseTabular('Город\tКомиссия\tПримечание\r\nАлматы\t3 %\t\r\nАстана\t\tтолько будни\r\n');
  assert.deepEqual(rows, [
    ['Город', 'Комиссия', 'Примечание'],
    ['Алматы', '3 %', ''],
    ['Астана', '', 'только будни'],
  ]);
});

test('ячейка в кавычках: перенос строки и кавычка внутри', () => {
  const rows = parseTabular('a\t"две\nстроки"\nb\t"сказал ""да"""');
  assert.deepEqual(rows, [['a', 'две\nстроки'], ['b', 'сказал "да"']]);
});

test('не таблица — остаётся текстом', () => {
  assert.equal(parseTabular('просто текст'), null);
  assert.equal(parseTabular('одна\tстрока'), null, 'одна строка — не таблица');
  assert.equal(parseTabular('\tотступ\nобычная строка\nещё одна'), null, 'отступ табуляцией — не таблица');
  assert.equal(parseTabular('a\tb\nc\td\te'), null, 'разное число столбцов — не таблица');
  assert.equal(parseTabular('a\t"незакрытая\nb\tc'), null);
  assert.equal(parseTabular(''), null);
});

test('кавычки в обычном тексте остаются, отступ табуляцией — не таблица', () => {
  assert.deepEqual(parseTabular('"Да" — ответ\tx\ny\tz'), [['"Да" — ответ', 'x'], ['y', 'z']]);
  assert.equal(parseTabular('\tfoo\n\tbar\n\tbaz'), null, 'у всех строк пустой первый столбец');
  assert.deepEqual(parseTabular('\tИтог\nАлматы\t3'), [['', 'Итог'], ['Алматы', '3']], 'пустая угловая ячейка — норма');
});

test('строки текста → таблица с шапкой и абзацами в ячейках', () => {
  const node = tableFromRows(schema, [['A', 'B'], ['1', 'две\nстроки']]);
  assert.equal(node.type, types.table);
  assert.deepEqual(cellTexts(node), [['A', 'B'], ['1', 'двестроки']]);
  assert.equal(node.child(0).child(0).type, types.header);
  assert.equal(node.child(1).child(0).type, types.cell);
  assert.equal(node.child(1).child(1).childCount, 2, 'перенос внутри ячейки — второй абзац');
  node.check();
  const plain = tableFromRows(schema, [['A', 'B'], ['1', '']], { header: false });
  assert.equal(plain.child(0).child(0).type, types.cell);
  plain.check();
});

test('таблица → текст с табуляциями; объединённая ячейка оставляет пустые места', () => {
  const merged = table(
    tr(th('Раздел', { colspan: 2 }), th('Итог')),
    tr(td('a'), td('b'), td('c', { rowspan: 2 })),
    tr(td('d'), td('e')),
  );
  assert.equal(tableToTabular(merged), 'Раздел\t\tИтог\na\tb\tc\nd\te\t');
  const withBreaks = table(tr(types.cell.create(null, [p('две'), p('строки\tс табом')]), td('x')));
  assert.equal(tableToTabular(withBreaks), 'две строки с табом\tx');
});

test('ячейка из Excel и Google Таблиц узнаётся по разметке', () => {
  assert.ok(isSpreadsheetHtml('<meta charset="utf-8"><google-sheets-html-origin><table><tr><td>1</td></tr></table>'));
  assert.ok(isSpreadsheetHtml('<html xmlns:x="urn:schemas-microsoft-com:office:excel"><table>'));
  assert.ok(isSpreadsheetHtml('<meta name=ProgId content=Excel.Sheet><table>'));
  assert.ok(!isSpreadsheetHtml('<table data-pm-slice="0 0 []"><tr><td><p>1</p></td></tr></table>'));
});

/* ── Ручки ───────────────────────────────────────────────────────────── */

test('ручки: по строке и по столбцу — каждая в своей ячейке', () => {
  const { state } = stateWith(sample());
  const found = activeTable(state);
  const grips = tableGrips(found);
  assert.deepEqual(grips.filter((g) => g.kind === 'row').map((g) => [g.from, g.to]), [[0, 0], [1, 1], [2, 2]]);
  assert.deepEqual(grips.filter((g) => g.kind === 'column').map((g) => [g.from, g.to]), [[0, 0], [1, 1], [2, 2]]);
  // Угловая ручка (меню и перенос всей таблицы) живёт в виде узла, а не в ячейке.
  assert.equal(grips.filter((g) => g.kind === 'table').length, 0);
  for (const grip of grips) {
    const $pos = state.doc.resolve(grip.pos);
    assert.ok(['tableCell', 'tableHeader'].includes($pos.parent.type.name), `${grip.kind} не в ячейке`);
  }
});

test('объединённая ячейка: одна ручка на всё, что она накрывает', () => {
  const merged = table(
    tr(th('Город', { rowspan: 2 }), th('Тариф', { colspan: 2 })),
    tr(td('Эконом'), td('Комфорт')),
    tr(td('Алматы'), td('3'), td('4')),
  );
  const { state } = stateWith(merged);
  const grips = tableGrips(activeTable(state));
  assert.deepEqual(grips.filter((g) => g.kind === 'row').map((g) => [g.from, g.to]), [[0, 1], [2, 2]]);
  assert.deepEqual(grips.filter((g) => g.kind === 'column').map((g) => [g.from, g.to]), [[0, 0], [1, 2]]);
  const rowGrip = grips.find((g) => g.kind === 'row' && g.from === 0);
  const sel = gripSelection(state.doc, activeTable(state), rowGrip);
  assert.ok(sel instanceof CellSelection);
  assert.ok(sel.isRowSelection());
  const next = state.apply(state.tr.setSelection(sel));
  const shape = selectionShape(next);
  assert.equal(shape.kind, 'rows');
  assert.deepEqual([shape.top, shape.bottom], [0, 2]);
});

test('выделение ручкой: строка, столбец, вся таблица', () => {
  const { state } = stateWith(sample());
  const found = activeTable(state);
  const pick = (kind, from = 1) => {
    const grip = kind === 'table' ? { kind, from: 0, to: 0 }
      : tableGrips(found).find((g) => g.kind === kind && g.from === from);
    return selectionShape(state.apply(state.tr.setSelection(gripSelection(state.doc, found, grip))));
  };
  const row = pick('row');
  assert.equal(row.kind, 'rows');
  assert.deepEqual([row.top, row.bottom, row.left, row.right], [1, 2, 0, 3]);
  const col = pick('column', 2);
  assert.equal(col.kind, 'columns');
  assert.deepEqual([col.left, col.right, col.top, col.bottom], [2, 3, 0, 3]);
  const all = pick('table');
  assert.equal(all.kind, 'table');
  assert.equal(all.count, 9);
});

test('таблица в ячейке таблицы: ручки — у той, где курсор', () => {
  const inner = table(tr(td('x'), td('y')));
  const outer = table(tr(types.cell.create(null, [p('внешняя'), inner]), td('z')));
  const documentNode = doc.create(null, [outer]);
  const state = EditorState.create({ schema, doc: documentNode });
  let innerPos = null;
  documentNode.descendants((node, pos) => {
    if (node === inner) innerPos = pos;
  });
  const inInner = state.apply(state.tr.setSelection(TextSelection.near(state.doc.resolve(innerPos + 3))));
  assert.equal(activeTable(inInner).pos, innerPos);
  assert.equal(tableAround(inInner.selection.$from).node, inner);
});

/* ── Команды ─────────────────────────────────────────────────────────── */

test('«+» снизу: строка в конец, курсор в её первую ячейку', () => {
  const { state, tablePos } = stateWith(sample());
  const { ok, state: next } = run(state, appendRow(tablePos));
  assert.ok(ok);
  const node = next.doc.nodeAt(tablePos);
  assert.equal(node.childCount, 4);
  assert.deepEqual(cellTexts(node)[3], ['', '', '']);
  assert.equal(node.child(3).child(0).type, types.cell, 'новая строка — не шапка');
  const shape = selectionShape(next);
  assert.deepEqual([shape.top, shape.left], [3, 0]);
});

test('«+» справа: столбец в конец, курсор в его верхнюю ячейку, шапка — шапкой', () => {
  const { state, tablePos } = stateWith(sample());
  const { state: next } = run(state, appendColumn(tablePos));
  const node = next.doc.nodeAt(tablePos);
  assert.equal(TableMap.get(node).width, 4);
  assert.equal(node.child(0).child(3).type, types.header, 'в строке-шапке новая ячейка — шапка');
  assert.equal(node.child(1).child(3).type, types.cell);
  const shape = selectionShape(next);
  assert.deepEqual([shape.top, shape.left], [0, 3]);
});

test('дублировать строку: копия под ней, с тем же текстом', () => {
  const { state, tablePos } = stateWith(sample());
  const { ok, state: next } = run(state, duplicateRow(tablePos, 1));
  assert.ok(ok);
  assert.deepEqual(cellTexts(next.doc.nodeAt(tablePos)).map((r) => r[0]), ['Город', 'Алматы', 'Алматы', 'Астана']);
  assert.deepEqual([selectionShape(next).top, selectionShape(next).left], [2, 0]);
});

test('дублировать строку-шапку: копия — обычная строка, шапка одна', () => {
  const { state, tablePos } = stateWith(sample());
  const { state: next } = run(state, duplicateRow(tablePos, 0));
  const node = next.doc.nodeAt(tablePos);
  assert.equal(node.child(1).child(0).type, types.cell);
  assert.ok(hasHeaderRow(node));
});

test('строку под объединением дублировать нельзя', () => {
  const merged = table(tr(td('a', { rowspan: 2 }), td('b')), tr(td('c')), tr(td('d'), td('e')));
  assert.equal(canDuplicateRow(merged, 0), false);
  assert.equal(canDuplicateRow(merged, 1), false);
  assert.equal(canDuplicateRow(merged, 2), true);
  const { state, tablePos } = stateWith(merged);
  assert.equal(run(state, duplicateRow(tablePos, 1)).ok, false);
});

test('дублировать таблицу: копия сразу под ней, курсор в копии', () => {
  const { state, tablePos } = stateWith(sample());
  const { state: next } = run(state, duplicateTable(tablePos));
  const first = next.doc.nodeAt(tablePos);
  const second = next.doc.nodeAt(tablePos + first.nodeSize);
  assert.ok(second && second.type === types.table);
  assert.deepEqual(cellTexts(second), cellTexts(first));
  assert.equal(activeTable(next).pos, tablePos + first.nodeSize);
});

test('перенос строки вниз — тем же prosemirror-tables, что и меню', () => {
  const { state, tablePos } = stateWith(sample());
  const found = activeTable(state);
  const grip = tableGrips(found).find((g) => g.kind === 'row' && g.from === 1);
  const selected = state.apply(state.tr.setSelection(gripSelection(state.doc, found, grip)));
  const { ok, state: next } = run(selected, moveTableRow({ from: 1, to: 2, pos: tablePos + 1 }));
  assert.ok(ok);
  assert.deepEqual(cellTexts(next.doc.nodeAt(tablePos)).map((r) => r[0]), ['Город', 'Астана', 'Алматы']);
});

test('шапка: строка и столбец распознаются', () => {
  assert.equal(hasHeaderRow(sample()), true);
  assert.equal(hasHeaderColumn(sample()), false);
  const side = table(tr(th('a'), td('1')), tr(th('b'), td('2')));
  assert.equal(hasHeaderRow(side), false);
  assert.equal(hasHeaderColumn(side), true);
});

/* ── Ширины столбцов ─────────────────────────────────────────────────── */

const withWidths = (widths, fixed) => {
  const node = sample();
  const { state, tablePos } = stateWith(node);
  const next = state.apply(setColumnWidths(state.tr, tablePos, widths, { fixed }));
  return { state: next, tablePos };
};

// Заморозка, как её делает перетаскивание границы: ширины у всех + признак.
const freeze = (state, tablePos, widths) => state.apply(setColumnWidths(state.tr, tablePos, widths, { fixed: true }));

test('ширины: читаются по столбцам; «заданы у всех» и «задал автор» — разные признаки', () => {
  const partial = withWidths([120, null, 90]);
  assert.deepEqual(columnWidths(partial.state.doc.nodeAt(partial.tablePos)), [120, null, 90]);
  assert.equal(widthsFixed(partial.state.doc.nodeAt(partial.tablePos)), false);
  // Ширины у всех столбцов — как у таблицы из Google Таблиц: точной раскладки нет.
  const pasted = withWidths([120, 150, 90]);
  assert.equal(widthsFixed(pasted.state.doc.nodeAt(pasted.tablePos)), true);
  assert.equal(isFixedLayout(pasted.state.doc.nodeAt(pasted.tablePos)), false);
  const frozen = withWidths([120, 150, 90], true);
  assert.equal(isFixedLayout(frozen.state.doc.nodeAt(frozen.tablePos)), true);
  assert.equal(widthsFixed(sample()), false);
});

test('ширины на объединённой ячейке пишутся массивом по её столбцам', () => {
  const merged = table(tr(th('Раздел', { colspan: 2 }), th('Итог')), tr(td('a'), td('b'), td('c')));
  const { state, tablePos } = stateWith(merged);
  const next = state.apply(setColumnWidths(state.tr, tablePos, [100, 140, 80]));
  const node = next.doc.nodeAt(tablePos);
  assert.deepEqual(node.child(0).child(0).attrs.colwidth, [100, 140]);
  assert.deepEqual(columnWidths(node), [100, 140, 80]);
  assert.ok(widthsFixed(node));
});

test('ширина нового столбца — средняя по таблице в разумных пределах', () => {
  assert.equal(newColumnWidth([100, 200]), 150);
  assert.equal(newColumnWidth([40, 50]), 80);
  assert.equal(newColumnWidth([400, 500]), 240);
  assert.equal(newColumnWidth([null, null]), 120);
});

const guardPlugin = new Plugin({ appendTransaction: guardFixedWidths });

test('замороженная таблица не «оттаивает» от нового столбца', () => {
  const node = sample();
  const { state: plain, tablePos } = stateWith(node, { plugins: [tableEditing(), guardPlugin] });
  const frozen = freeze(plain, tablePos, [120, 160, 100]);
  assert.ok(widthsFixed(frozen.doc.nodeAt(tablePos)));
  const { state: next } = run(frozen, addColumnAfter);
  const after = next.doc.nodeAt(tablePos);
  assert.equal(TableMap.get(after).width, 4);
  assert.ok(widthsFixed(after), 'новый столбец получил ширину сам');
  assert.deepEqual(columnWidths(after), [120, 127, 160, 100]);
});

test('сторож не трогает таблицу, у которой ширины заданы не у всех', () => {
  const node = sample();
  const { state: plain, tablePos } = stateWith(node, { plugins: [tableEditing(), guardPlugin] });
  const partial = plain.apply(setColumnWidths(plain.tr, tablePos, [120, null, null]));
  const { state: next } = run(partial, appendColumn(tablePos));
  assert.deepEqual(columnWidths(next.doc.nodeAt(tablePos)), [120, null, null, null]);
});

test('«Сбросить размеры» снимает ширины и высоты, и сторож ширины не возвращает', () => {
  const node = sample();
  const { state: plain, tablePos } = stateWith(node, { plugins: [tableEditing(), guardPlugin] });
  const frozen = freeze(plain, tablePos, [120, 160, 100]);
  const tall = run(frozen, setRowHeight(tablePos, 1, 70)).state;
  assert.ok(tableHasSizes(tall.doc.nodeAt(tablePos)));
  const { ok, state: next } = run(tall, resetTableSizes(tablePos));
  assert.ok(ok);
  assert.deepEqual(columnWidths(next.doc.nodeAt(tablePos)), [null, null, null]);
  assert.deepEqual(rowHeights(next.doc.nodeAt(tablePos)), [null, null, null]);
  assert.equal(isFixedLayout(next.doc.nodeAt(tablePos)), false, 'признак снят');
  assert.equal(run(next, resetTableSizes(tablePos)).ok, false, 'без размеров пункт неприменим');
  assert.ok(RESET_WIDTHS_META);
});

test('сторож не трогает таблицу без признака, даже с ширинами у всех столбцов', () => {
  const node = sample();
  const { state: plain, tablePos } = stateWith(node, { plugins: [tableEditing(), guardPlugin] });
  const pasted = plain.apply(setColumnWidths(plain.tr, tablePos, [100, 100, 100]));
  const { state: next } = run(pasted, appendColumn(tablePos));
  assert.deepEqual(columnWidths(next.doc.nodeAt(tablePos)), [100, 100, 100, null]);
});

test('⌘Z после заморозки и перетаскивания не «досочиняет» ширины', () => {
  const node = sample();
  const { state: plain, tablePos } = stateWith(node, { plugins: [history(), tableEditing(), guardPlugin] });
  // Заморозка мимо истории — как freezeColumnWidths; затем перетаскивание одного столбца.
  const frozen = plain.apply(setColumnWidths(plain.tr, tablePos, [150, 200, 250], { fixed: true })
    .setMeta('addToHistory', false));
  const dragged = frozen.apply(setColumnWidths(frozen.tr, tablePos, [150, 120, 250]));
  const back = run(dragged, undo).state;
  assert.deepEqual(columnWidths(back.doc.nodeAt(tablePos)), [150, 200, 250], 'одним ⌘Z — прежняя ширина');
  // «Сбросить размеры», ⌘Z, ⌘⇧Z — ширин нет, придуманных средних тоже.
  const reset = run(dragged, resetTableSizes(tablePos)).state;
  const undone = run(reset, undo).state;
  assert.deepEqual(columnWidths(undone.doc.nodeAt(tablePos)), [150, 120, 250]);
  const redone = run(undone, redo).state;
  assert.deepEqual(columnWidths(redone.doc.nodeAt(tablePos)), [null, null, null]);
  assert.equal(isFixedLayout(redone.doc.nodeAt(tablePos)), false);
});

test('ширину, потерянную при объединении ячеек, сторож возвращает прежнюю, а не среднюю', () => {
  const node = table(tr(th('a'), th('b'), th('c')), tr(td('1'), td('2'), td('3')));
  const { state: plain, tablePos } = stateWith(node, { plugins: [tableEditing(), guardPlugin] });
  const frozen = freeze(plain, tablePos, [100, 300, 500]);
  const found = activeTable(frozen);
  const map = TableMap.get(found.node);
  // Столбцы 1–2 целиком, на всю высоту — ширина столбца 2 больше нигде не записана.
  const sel = CellSelection.create(frozen.doc, found.start + map.map[1], found.start + map.map[map.width + 2]);
  const { ok, state: merged } = run(frozen.apply(frozen.tr.setSelection(sel)), mergeCells);
  assert.ok(ok);
  assert.deepEqual(columnWidths(merged.doc.nodeAt(tablePos)), [100, 300, 500]);
});

test('сторож молчит на правке текста — без лишних транзакций', () => {
  const node = sample();
  const { state: plain, tablePos } = stateWith(node, { plugins: [guardPlugin] });
  const frozen = freeze(plain, tablePos, [120, 160, 100]);
  const typed = frozen.tr.insertText('!');
  assert.equal(guardFixedWidths([typed], frozen, frozen.apply(typed)), null);
});

/* ── Высота строк ────────────────────────────────────────────────────── */

test('высота строки: задаётся, снимается, не меньше предела', () => {
  const { state, tablePos } = stateWith(sample());
  const { ok, state: tall } = run(state, setRowHeight(tablePos, 2, 64.4));
  assert.ok(ok);
  assert.deepEqual(rowHeights(tall.doc.nodeAt(tablePos)), [null, null, 64]);
  assert.equal(run(tall, setRowHeight(tablePos, 2, 64)).ok, false, 'та же высота — не правка');
  const low = run(tall, setRowHeight(tablePos, 2, 5)).state;
  assert.equal(rowHeights(low.doc.nodeAt(tablePos))[2], ROW_MIN_HEIGHT);
  const auto = run(low, setRowHeight(tablePos, 2, null)).state;
  assert.deepEqual(rowHeights(auto.doc.nodeAt(tablePos)), [null, null, null]);
  assert.equal(run(state, setRowHeight(tablePos, 9, 50)).ok, false, 'нет такой строки');
});

test('граница строки под указателем: низ ячейки — её последняя строка, верх — строка выше', () => {
  const merged = table(
    tr(th('Город', { rowspan: 2 }), th('Тариф')),
    tr(td('Эконом')),
    tr(td('Алматы'), td('3')),
  );
  const map = TableMap.get(merged);
  const cityRel = map.map[0];
  assert.equal(rowAtCellEdge(merged, cityRel, 'bottom'), 1, 'объединённая на две строки тянет нижнюю');
  assert.equal(rowAtCellEdge(merged, cityRel, 'top'), -1, 'над первой строкой тянуть нечего');
  assert.equal(rowAtCellEdge(merged, map.map[2 * map.width], 'top'), 1);
  // Маркер границы строки 0 — только в ячейке «Тариф»: «Город» тянется ниже.
  assert.deepEqual(cellsEndingAtRow(merged, 0), [map.map[1]]);
  assert.deepEqual(cellsEndingAtRow(merged, 1).length, 2);
});

/* ── Перенос таблицы ─────────────────────────────────────────────────── */

const article = () => {
  const node = sample();
  const documentNode = doc.create(null, [p('один'), p('два'), node, p('три'), p('четыре')]);
  const state = EditorState.create({ schema, doc: documentNode });
  const boundaries = [];
  documentNode.forEach((child, offset) => boundaries.push(offset));
  boundaries.push(documentNode.content.size);
  return { state, tablePos: boundaries[2], boundaries };
};

const blocks = (state) => {
  const out = [];
  state.doc.forEach((child) => out.push(child.type.name === 'table' ? 'ТАБЛИЦА' : child.textContent));
  return out;
};

test('перенос таблицы вниз и вверх — между абзацами', () => {
  const { state, tablePos, boundaries } = article();
  const down = run(state, moveTable(tablePos, boundaries[4]));
  assert.ok(down.ok);
  assert.deepEqual(blocks(down.state), ['один', 'два', 'три', 'ТАБЛИЦА', 'четыре']);
  assert.equal(activeTable(down.state).node.type.name, 'table', 'курсор — в перенесённой таблице');
  const top = run(state, moveTable(tablePos, 0));
  assert.deepEqual(blocks(top.state), ['ТАБЛИЦА', 'один', 'два', 'три', 'четыре']);
  const end = run(state, moveTable(tablePos, state.doc.content.size));
  assert.deepEqual(blocks(end.state), ['один', 'два', 'три', 'четыре', 'ТАБЛИЦА']);
});

test('граница вплотную к таблице — не перенос; внутрь абзаца — нельзя', () => {
  const { state, tablePos, boundaries } = article();
  assert.equal(run(state, moveTable(tablePos, boundaries[2])).ok, false, 'перед самой собой');
  assert.equal(run(state, moveTable(tablePos, boundaries[3])).ok, false, 'сразу после себя');
  assert.equal(run(state, moveTable(tablePos, 2)).ok, false, 'внутрь абзаца');
  assert.equal(run(state, moveTable(tablePos, tablePos + 3)).ok, false, 'внутрь самой таблицы');
  assert.equal(run(state, moveTable(tablePos, -1)).ok, false);
});

test('перенос с ⌥: копия встаёт, таблица остаётся', () => {
  const { state, tablePos } = article();
  const copy = run(state, moveTable(tablePos, 0, { copy: true }));
  assert.ok(copy.ok);
  assert.deepEqual(blocks(copy.state), ['ТАБЛИЦА', 'один', 'два', 'ТАБЛИЦА', 'три', 'четыре']);
});

test('«Переместить выше / ниже»: на шаг через соседний блок, у края — некуда', () => {
  const { state, tablePos } = article();
  const up = run(state, moveTable(tablePos, neighborTarget(state.doc, tablePos, -1)));
  assert.deepEqual(blocks(up.state), ['один', 'ТАБЛИЦА', 'два', 'три', 'четыре']);
  const down = run(state, moveTable(tablePos, neighborTarget(state.doc, tablePos, 1)));
  assert.deepEqual(blocks(down.state), ['один', 'два', 'три', 'ТАБЛИЦА', 'четыре']);
  const top = run(state, moveTable(tablePos, 0)).state;
  assert.equal(neighborTarget(top.doc, 0, -1), null, 'выше первого блока некуда');
  const bottom = run(state, moveTable(tablePos, state.doc.content.size)).state;
  let lastPos = 0;
  bottom.doc.forEach((child, offset) => { lastPos = offset; });
  assert.equal(neighborTarget(bottom.doc, lastPos, 1), null, 'ниже последнего блока некуда');
  // Таблица в ячейке другой таблицы на шаг не двигается.
  const inner = table(tr(td('x')));
  const outer = table(tr(types.cell.create(null, [inner])));
  const nested = EditorState.create({ schema, doc: doc.create(null, [p('a'), outer]) });
  let innerPos = null;
  nested.doc.descendants((node, pos) => { if (node === inner) innerPos = pos; });
  assert.equal(neighborTarget(nested.doc, innerPos, -1), null);
});

test('ближайшая граница — по вертикали к указателю', () => {
  const list = [{ pos: 0, y: 100 }, { pos: 10, y: 140 }, { pos: 20, y: 260 }];
  assert.equal(nearestBoundary(list, 90).pos, 0);
  assert.equal(nearestBoundary(list, 150).pos, 10);
  assert.equal(nearestBoundary(list, 230).pos, 20);
  assert.equal(nearestBoundary([], 10), null);
});

/* ── Ручки при объединённых ячейках с краю ───────────────────────────── */

test('строка: ячейка последнего столбца объединена со строкой выше — выделяется ровно своя строка', () => {
  const merged = table(
    tr(td('a0'), td('a1'), td('a2', { rowspan: 2 })),
    tr(td('b0'), td('b1')),
    tr(td('c0'), td('c1'), td('c2')),
  );
  const { state, tablePos } = stateWith(merged);
  const found = activeTable(state);
  const sel = rowSelection(state.doc, found, 1);
  const withSel = state.apply(state.tr.setSelection(sel));
  const shape = selectionShape(withSel);
  assert.deepEqual([shape.top, shape.bottom], [1, 2], 'строка 0 в выделение не попала');
  const { state: next } = run(withSel, deleteRow);
  assert.deepEqual(cellTexts(next.doc.nodeAt(tablePos)), [['a0', 'a1', 'a2'], ['c0', 'c1', 'c2']]);
});

test('столбец: ячейка последней строки объединена со столбцом правее — выделяется ровно свой столбец', () => {
  const merged = table(
    tr(td('a0'), td('a1'), td('a2')),
    tr(td('b0'), td('b1'), td('b2')),
    tr(td('c0', { colspan: 2 }), td('c2')),
  );
  const { state } = stateWith(merged);
  const found = activeTable(state);
  const shape = selectionShape(state.apply(state.tr.setSelection(columnSelection(state.doc, found, 0))));
  assert.deepEqual([shape.left, shape.right], [0, 1], 'столбец 1 в выделение не попал');
});

test('большая таблица: ширины столбцов считаются быстро', () => {
  const rows = [];
  for (let r = 0; r < 1000; r += 1) rows.push(tr(...Array.from({ length: 10 }, (_, k) => td(`${r}-${k}`))));
  const big = table(...rows);
  const started = performance.now();
  for (let i = 0; i < 20; i += 1) columnWidths(big);
  const perCall = (performance.now() - started) / 20;
  assert.ok(perCall < 15, `ширины 1000×10 за ${perCall.toFixed(1)} мс`);
});

/* ── Положение по ширине ─────────────────────────────────────────────── */

test('вбок: таблица встаёт к ближайшему из трёх мест; широкой двигаться некуда', () => {
  const col = { left: 100, width: 800, tableWidth: 300 };
  // Места середины таблицы: слева 250, по центру 500, справа 750.
  assert.equal(snapAlign({ ...col, center: 260 }), 'left');
  assert.equal(snapAlign({ ...col, center: 470 }), 'center');
  assert.equal(snapAlign({ ...col, center: 700 }), 'right');
  assert.equal(snapAlign({ ...col, center: 2000 }), 'right');
  assert.equal(snapAlign({ left: 100, width: 800, tableWidth: 800, center: 700 }), 'left', 'во всю ширину — только у левого края');
});

test('положение пишется в таблицу и снимается', () => {
  const { state, tablePos } = stateWith(sample());
  assert.equal(tableAlign(state.doc.nodeAt(tablePos)), 'left');
  const centered = run(state, setTableAlign(tablePos, 'center'));
  assert.ok(centered.ok);
  assert.equal(tableAlign(centered.state.doc.nodeAt(tablePos)), 'center');
  assert.equal(run(centered.state, setTableAlign(tablePos, 'center')).ok, false, 'то же положение — не правка');
  const back = run(centered.state, setTableAlign(tablePos, 'left')).state;
  assert.equal(back.doc.nodeAt(tablePos).attrs.align, null, 'у левого края — без атрибута, как у старых таблиц');
  assert.equal(run(state, setTableAlign(tablePos, 'middle')).ok, false);
});

