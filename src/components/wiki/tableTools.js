/* Таблицы в редакторе статьи — вся логика без React и без браузера.
 *
 * Отдельным модулем ровно для того, чтобы её можно было проверить: node --test
 * собирает ту же схему, что редактор (StarterKit + узлы таблицы), и гоняет
 * команды на настоящем ProseMirror. Всё, что трогает DOM (замер ширины колонок,
 * буфер обмена), лежит в конце файла и в тестах не участвует.
 *
 * Что здесь решается и почему так.
 *
 * 1. РУЧКИ СТРОК И СТОЛБЦОВ (tableGrips). У каждой строки слева и у каждого
 *    столбца сверху — маленькая ручка: нажал — строка выделена и открыто её
 *    меню. Ручка ставится в ячейку, а не рядом с таблицей: так она едет вместе
 *    с таблицей при прокрутке и при любой перекладке строк, и не нужно ни
 *    одного замера координат. Объединённые ячейки: ручка одна на всю
 *    объединённую ячейку и выделяет все строки (столбцы), которые та
 *    накрывает, — выделить «половину» такой ячейки ProseMirror не умеет.
 *
 * 2. ШИРИНА СТОЛБЦОВ. Пока хоть у одного столбца ширина не задана, таблица
 *    раскладывается по содержимому, и заданная ширина работает только как
 *    НИЖНЯЯ граница — сузить столбец перетаскиванием было нельзя: отпустил
 *    мышь, и он возвращался обратно. Поэтому, как только автор потянул границу
 *    столбца (первый сдвиг мыши, не щелчок), ширины ВСЕХ столбцов
 *    замораживаются по факту (freezeColumnWidths), а таблица получает признак
 *    fixedLayout — и дальше держит ровно те ширины, что ей задали, и в
 *    редакторе, и у читателя (класс wiki-table-fixed, wiki-theme.css).
 *    guardFixedWidths следит, чтобы такая таблица не «оттаяла» от нового
 *    столбца, и не вмешивается в ⌘Z. Высота строки — атрибут строки (её
 *    нижнюю границу тоже тянут мышью, см. WikiTableTools.js).
 *
 * 3. ВСТАВКА ТАБЛИЦЫ ТЕКСТОМ (parseTabular). Excel, Google Таблицы и Word
 *    кладут в буфер HTML, и его ProseMirror разбирает сам. Текст с табуляциями
 *    без HTML приходит из «Блокнота», из выгрузок, из мессенджеров — его
 *    раньше вставляло одной строкой с пробелами. Теперь — таблицей.
 *
 * 4. КОПИЯ ВСЕЙ ТАБЛИЦЫ (tableClipboard). В буфер кладутся два вида сразу:
 *    HTML в формате самого ProseMirror (вставка в любую статью вики даёт ту же
 *    таблицу со всеми объединениями и ширинами, а Excel и Word понимают его
 *    как обычную таблицу) и текст с табуляциями — для мессенджера и блокнота.
 */
import { Fragment, Slice } from '@tiptap/pm/model';
import { TextSelection } from '@tiptap/pm/state';
import { isHistoryTransaction } from '@tiptap/pm/history';
import {
    CellSelection, TableMap, addColumn, addRow, columnIsHeader, rowIsHeader, tableNodeTypes,
} from '@tiptap/pm/tables';

/* ── Размер новой таблицы ─────────────────────────────────────────────── */

// Сетка выбора размера: 10 столбцов на 8 строк. Больше в справочной статье не
// бывает, а если понадобится — строки добавляются «плюсом» под таблицей и
// клавишей Tab в последней ячейке.
export const GRID_COLS = 10;
export const GRID_ROWS = 8;

export const plural = (n, one, few, many) => {
    const n10 = Math.abs(n) % 10;
    const n100 = Math.abs(n) % 100;
    if (n10 === 1 && n100 !== 11) return one;
    if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few;
    return many;
};

export const tableSizeLabel = (cols, rows) => (
    `${cols} ${plural(cols, 'столбец', 'столбца', 'столбцов')} × `
    + `${rows} ${plural(rows, 'строка', 'строки', 'строк')}`
);

export const clampSize = (value, max) => Math.min(max, Math.max(1, Math.round(Number(value) || 1)));

/* ── Где таблица ──────────────────────────────────────────────────────── */

const isTable = (node) => node?.type?.spec?.tableRole === 'table';

/* Ближайшая таблица вокруг позиции: { node, pos, start }. Ближайшая, а не
   внешняя: таблица внутри ячейки другой таблицы схемой разрешена, и ручки
   должны принадлежать той, в которой стоит курсор. */
export const tableAround = ($pos) => {
    for (let depth = $pos.depth; depth > 0; depth -= 1) {
        const node = $pos.node(depth);
        if (isTable(node)) return { node, pos: $pos.before(depth), start: $pos.start(depth) };
    }
    return null;
};

/* Таблица, с которой сейчас работает автор: та, где стоит курсор или лежит
   выделение ячеек. У выделения ячеек $from — внутри первой ячейки, но берём
   $anchorCell: это та ячейка, с которой начали выделять. */
export const activeTable = (state) => {
    const sel = state.selection;
    const $pos = sel instanceof CellSelection ? sel.$anchorCell : sel.$from;
    return tableAround($pos);
};

export const tableAt = (doc, pos) => {
    const node = doc.nodeAt(pos);
    return isTable(node) ? { node, pos, start: pos + 1 } : null;
};

/* Ячейка (строка, столбец) — с учётом объединений: { pos, top, left,
   bottom, right }. pos — абсолютная позиция ПЕРЕД ячейкой. Сам узел не
   берём: nodeAt на каждую строку большой таблицы — это обход от её корня
   на каждое нажатие клавиши. */
const cellAt = (found, map, row, col) => {
    const rel = map.map[row * map.width + col];
    return { pos: found.start + rel, ...map.findCell(rel) };
};

/* Все ячейки таблицы по их позиции внутри неё (как в TableMap) — одним
   проходом. table.nodeAt(pos) на каждую ячейку идёт от корня таблицы, и на
   таблице в тысячу строк обход «по ячейке» превращался в миллионы шагов на
   каждое нажатие клавиши. */
export const cellNodes = (table) => {
    const cells = new Map();
    table.forEach((row, rowOffset) => {
        row.forEach((cell, cellOffset) => cells.set(rowOffset + 1 + cellOffset, cell));
    });
    return cells;
};

/* ── Выделения ────────────────────────────────────────────────────────── */

/* Выделение ровно строк fromRow…toRow (столбцов fromCol…toCol) — для ручки.
 *
 * Не CellSelection.rowSelection: та тянет выделение до ячейки в последнем
 * столбце, и если эта ячейка объединена со строкой выше, в выделение (а за ним
 * в «Удалить строку») уезжала и чужая строка. Здесь дальний угол — последняя
 * ячейка полосы, которая целиком лежит в её строках; объединённая ячейка,
 * залезающая в полосу снаружи, в выделение не попадает (половину ячейки
 * ProseMirror выделить не умеет). */
const bandSelection = (doc, found, rows, cols) => {
    const map = TableMap.get(found.node);
    const inside = (rect) => rect.top >= rows[0] && rect.bottom <= rows[1] + 1
        && rect.left >= cols[0] && rect.right <= cols[1] + 1;
    const anchorRel = map.map[rows[0] * map.width + cols[0]];
    let headRel = anchorRel;
    // Дальний угол ищем с конца полосы: последняя строка, последний столбец.
    for (let row = rows[1]; row >= rows[0] && headRel === anchorRel; row -= 1) {
        for (let col = cols[1]; col >= cols[0]; col -= 1) {
            const rel = map.map[row * map.width + col];
            if (inside(map.findCell(rel))) {
                headRel = rel;
                break;
            }
        }
    }
    return new CellSelection(doc.resolve(found.start + anchorRel), doc.resolve(found.start + headRel));
};

export const rowSelection = (doc, found, fromRow, toRow = fromRow) => {
    const map = TableMap.get(found.node);
    return bandSelection(doc, found, [fromRow, toRow], [0, map.width - 1]);
};

export const columnSelection = (doc, found, fromCol, toCol = fromCol) => {
    const map = TableMap.get(found.node);
    return bandSelection(doc, found, [0, map.height - 1], [fromCol, toCol]);
};

export const wholeTableSelection = (doc, found) => {
    const map = TableMap.get(found.node);
    return CellSelection.create(doc, found.start + map.map[0],
        found.start + map.map[map.map.length - 1]);
};

/* Что сейчас выделено в таблице: строки, столбцы, вся таблица, несколько
   ячеек или курсор в одной. По этому решается, какую ручку подсветить и
   показывать ли панель «Объединить». */
export const selectionShape = (state) => {
    const sel = state.selection;
    const found = activeTable(state);
    if (!found) return null;
    const map = TableMap.get(found.node);
    if (!(sel instanceof CellSelection)) {
        const $cell = cellAroundPos(sel.$from);
        if (!$cell) return { kind: 'cursor', found, map };
        const rect = map.findCell($cell.pos - found.start);
        return { kind: 'cursor', found, map, ...rect, cell: $cell.nodeAfter };
    }
    const rect = map.rectBetween(sel.$anchorCell.pos - found.start, sel.$headCell.pos - found.start);
    const rows = sel.isRowSelection();
    const cols = sel.isColSelection();
    let kind = 'cells';
    if (rows && cols) kind = 'table';
    else if (rows) kind = 'rows';
    else if (cols) kind = 'columns';
    let count = 0;
    sel.forEachCell(() => { count += 1; });
    return { kind, found, map, count, ...rect };
};

/* $pos ячейки, в которой стоит позиция (как cellAround в prosemirror-tables,
   но без его требований к роли строки). */
const cellAroundPos = ($pos) => {
    for (let depth = $pos.depth - 1; depth > 0; depth -= 1) {
        if ($pos.node(depth).type.spec.tableRole === 'row') {
            return $pos.node(0).resolve($pos.before(depth + 1));
        }
    }
    return null;
};

/* ── Ручки строк и столбцов ───────────────────────────────────────────── */

/* Список ручек для таблицы: куда их поставить и что каждая выделяет.
 *
 * pos — позиция ВНУТРИ ячейки (начало её содержимого): виджет ложится первым
 * ребёнком <td>, а сама ячейка в редакторе — position: relative, поэтому
 * ручку можно вынести за её край стилем, без замеров.
 *
 *  * строка: ячейка нулевого столбца. Если её накрывает объединение сверху,
 *    своей ручки у строки нет — её выделяет ручка той объединённой ячейки;
 *  * столбец: ячейка верхней строки, по одной на ячейку.
 *
 * Ручка всей таблицы (угловая: меню и перенос таблицы) — не здесь, а в виде
 * узла (WikiTableView.js): она нужна у КАЖДОЙ таблицы, а не только у той, где
 * курсор, — чтобы таблицу можно было взять и перенести, не щёлкая в неё.
 *
 * Подсветка выбранной ручки — не здесь, а стилем: ячейка с ручкой получает
 * от prosemirror-tables класс selectedCell, а таблица — класс вида выделения
 * (строки, столбцы, вся). Будь «выбрана» частью ручки, ProseMirror
 * пересоздавал бы её на каждом выделении, и меню, открытое от неё, повисло бы
 * на уже снятом с экрана элементе. */
export const tableGrips = (found) => {
    const map = TableMap.get(found.node);
    const grips = [];
    for (let row = 0; row < map.height; row += 1) {
        const cell = cellAt(found, map, row, 0);
        if (cell.top !== row) continue;
        grips.push({ kind: 'row', from: cell.top, to: cell.bottom - 1, pos: cell.pos + 1 });
    }
    for (let col = 0; col < map.width; col += 1) {
        const cell = cellAt(found, map, 0, col);
        if (cell.left !== col) continue;
        grips.push({ kind: 'column', from: cell.left, to: cell.right - 1, pos: cell.pos + 1 });
    }
    return grips;
};

/* Выделение, которое даёт ручка. */
export const gripSelection = (doc, found, grip) => {
    if (grip.kind === 'row') return rowSelection(doc, found, grip.from, grip.to);
    if (grip.kind === 'column') return columnSelection(doc, found, grip.from, grip.to);
    return wholeTableSelection(doc, found);
};

/* ── Шапка ────────────────────────────────────────────────────────────── */

export const hasHeaderRow = (table) => rowIsHeader(TableMap.get(table), table, 0);
export const hasHeaderColumn = (table) => columnIsHeader(TableMap.get(table), table, 0);

/* ── Ширины столбцов ──────────────────────────────────────────────────── */

/* Ширины столбцов по атрибутам ячеек: массив длиной в число столбцов, null —
   ширина не задана. Берётся первое ненулевое значение по столбцу: ячейки
   одного столбца хранят одну и ту же ширину, а fixTables подравнивает
   расхождения сам. */
export const columnWidths = (table) => {
    const map = TableMap.get(table);
    const cells = cellNodes(table);
    const widths = new Array(map.width).fill(null);
    const seen = new Set();
    map.map.forEach((rel, index) => {
        if (seen.has(rel)) return;
        seen.add(rel);
        const node = cells.get(rel);
        const left = index % map.width;
        const { colwidth, colspan } = node.attrs;
        for (let j = 0; j < colspan; j += 1) {
            const width = colwidth && colwidth[j];
            if (width && widths[left + j] == null) widths[left + j] = width;
        }
    });
    return widths;
};

/* Ширины заданы у всех столбцов. */
export const widthsFixed = (table) => {
    const widths = columnWidths(table);
    return widths.length > 0 && widths.every((width) => width > 0);
};

/* Таблица, которой автор задал ширины, перетащив границу столбца: у неё
   атрибут fixedLayout (класс wiki-table-fixed, см. WikiTable в
   WikiTableTools.js), и только она раскладывается по заданным ширинам точно.
   Отдельный признак, а не «ширины заданы у всех»: ширины у всех бывают и у
   таблицы, вставленной из Google Таблиц (TipTap читает их из <col width>),
   и у старых статей — их вид без ведома автора меняться не должен. */
export const isFixedLayout = (table) => Boolean(table?.attrs?.fixedLayout);

/* Проставить ширины всем ячейкам таблицы. widths — по столбцам; null у
   столбца — ширина снимается. fixed — заодно поставить или снять признак
   «ширины заданы автором». */
export const setColumnWidths = (tr, tablePos, widths, { fixed } = {}) => {
    const table = tr.doc.nodeAt(tablePos);
    if (!isTable(table)) return tr;
    const map = TableMap.get(table);
    const cells = cellNodes(table);
    const seen = new Set();
    map.map.forEach((rel, index) => {
        if (seen.has(rel)) return;
        seen.add(rel);
        const node = cells.get(rel);
        const left = index % map.width;
        const own = [];
        for (let j = 0; j < node.attrs.colspan; j += 1) own.push(widths[left + j] || 0);
        const colwidth = own.every((width) => !width) ? null : own;
        const before = node.attrs.colwidth;
        const same = (before == null && colwidth == null)
            || (before && colwidth && before.length === colwidth.length
                && before.every((width, j) => width === colwidth[j]));
        if (same) return;
        tr.setNodeMarkup(tablePos + 1 + rel, null, { ...node.attrs, colwidth });
    });
    if (fixed !== undefined && 'fixedLayout' in table.attrs && Boolean(table.attrs.fixedLayout) !== fixed) {
        tr.setNodeMarkup(tablePos, null, { ...tr.doc.nodeAt(tablePos).attrs, fixedLayout: fixed });
    }
    return tr;
};

// Ширина нового столбца у таблицы с замороженными ширинами: средняя по
// таблице, но в разумных пределах — новый столбец не должен ни схлопнуться,
// ни растянуть таблицу на экран.
const NEW_COLUMN_MIN = 80;
const NEW_COLUMN_MAX = 240;
export const newColumnWidth = (widths) => {
    const known = widths.filter((width) => width > 0);
    if (!known.length) return 120;
    const average = known.reduce((sum, width) => sum + width, 0) / known.length;
    return Math.round(Math.min(NEW_COLUMN_MAX, Math.max(NEW_COLUMN_MIN, average)));
};

/* Метка транзакции «размеры снимаются намеренно» — для сторожа ширин. */
export const RESET_WIDTHS_META = 'wikiTableResetWidths';

/* Таблица с заданными автором ширинами не должна «оттаять» от правки.
 *
 * Новый столбец (кнопкой, из меню, вставкой из Excel, которая растит таблицу)
 * приходит без ширины, а объединение ячеек на всю высоту столбца теряет его
 * ширину. TableView тогда снимает с таблицы width, раскладка переходит на «по
 * содержимому», и суженные автором столбцы разъезжаются обратно. Поэтому
 * недостающие ширины проставляются сами: число столбцов то же — прежней
 * шириной этого столбца, столбцов стало больше — средней по таблице.
 *
 * Не вмешивается:
 *  * в отмену и повтор (⌘Z, ⌘⇧Z) — они возвращают ровно то, что было, и
 *    «досочинённые» ширины не дали бы отменить ни перетаскивание границы, ни
 *    «Сбросить размеры»;
 *  * в «Сбросить размеры» (RESET_WIDTHS_META);
 *  * в таблицы без признака fixedLayout — старые статьи и вставленные таблицы
 *    открыть на правку не значит молча поменять их вид;
 *  * в таблицы, которых правка не коснулась: узел тот же — проверять нечего
 *    (на большой статье это и есть стоимость каждого нажатия). */
export const guardFixedWidths = (transactions, oldState, newState) => {
    if (!transactions.some((tr) => tr.docChanged)) return null;
    if (transactions.some((tr) => tr.getMeta(RESET_WIDTHS_META) || isHistoryTransaction(tr))) return null;
    const fixed = [];
    oldState.doc.descendants((node, pos) => {
        if (isTable(node)) {
            if (isFixedLayout(node)) fixed.push({ pos, node });
            return true;
        }
        // В абзацы не спускаемся: таблиц там нет, а обход идёт на каждое нажатие.
        return !node.isTextblock;
    });
    if (!fixed.length) return null;
    const tr = newState.tr;
    fixed.forEach(({ pos: oldPos, node: oldTable }) => {
        let pos = oldPos;
        let deleted = false;
        transactions.forEach((step) => {
            if (deleted) return;
            const result = step.mapping.mapResult(pos, 1);
            if (result.deleted) deleted = true;
            pos = result.pos;
        });
        if (deleted) return;
        const table = tr.doc.nodeAt(pos);
        if (table === oldTable || !isTable(table) || !isFixedLayout(table) || widthsFixed(table)) return;
        const widths = columnWidths(table);
        const before = columnWidths(oldTable);
        const fill = newColumnWidth(widths);
        const sameColumns = before.length === widths.length;
        setColumnWidths(tr, pos, widths.map((width, col) => width || (sameColumns && before[col]) || fill));
    });
    return tr.docChanged ? tr : null;
};

/* ── Команды над таблицей ─────────────────────────────────────────────── */

const cursorInto = (tr, cellPos) => tr.setSelection(TextSelection.near(tr.doc.resolve(cellPos + 1)));

/* Строка в конец таблицы — кнопка «+» под таблицей. Курсор встаёт в первую
   ячейку новой строки: её добавляют, чтобы сразу заполнить. */
export const appendRow = (tablePos) => (state, dispatch) => {
    const table = state.doc.nodeAt(tablePos);
    if (!isTable(table)) return false;
    if (!dispatch) return true;
    const map = TableMap.get(table);
    const tr = state.tr;
    addRow(tr, { map, tableStart: tablePos + 1, table }, map.height);
    const next = tr.doc.nodeAt(tablePos);
    const nextMap = TableMap.get(next);
    cursorInto(tr, tablePos + 1 + nextMap.map[(nextMap.height - 1) * nextMap.width]);
    dispatch(tr.scrollIntoView());
    return true;
};

/* Столбец справа — кнопка «+» у правого края. Курсор — в верхнюю ячейку
   нового столбца: первым делом столбцу дают название. */
export const appendColumn = (tablePos) => (state, dispatch) => {
    const table = state.doc.nodeAt(tablePos);
    if (!isTable(table)) return false;
    if (!dispatch) return true;
    const map = TableMap.get(table);
    const tr = state.tr;
    addColumn(tr, { map, tableStart: tablePos + 1, table }, map.width);
    const next = tr.doc.nodeAt(tablePos);
    const nextMap = TableMap.get(next);
    cursorInto(tr, tablePos + 1 + nextMap.map[nextMap.width - 1]);
    dispatch(tr.scrollIntoView());
    return true;
};

/* Копия строки сразу под ней. Только для строки без объединений по
   вертикали: строка, которую накрывает объединение сверху или чья ячейка
   уходит вниз, копией «как есть» не вставляется — таблица разъедется. */
export const canDuplicateRow = (table, row) => {
    const map = TableMap.get(table);
    for (let col = 0; col < map.width; col += 1) {
        const rect = map.findCell(map.map[row * map.width + col]);
        if (rect.top !== row || rect.bottom !== row + 1) return false;
    }
    return true;
};

export const duplicateRow = (tablePos, row) => (state, dispatch) => {
    const table = state.doc.nodeAt(tablePos);
    if (!isTable(table) || row < 0 || row >= table.childCount) return false;
    if (!canDuplicateRow(table, row)) return false;
    if (!dispatch) return true;
    let rowPos = tablePos + 1;
    for (let i = 0; i < row; i += 1) rowPos += table.child(i).nodeSize;
    const source = table.child(row);
    /* Шапку не размножаем: копия строки-шапки — обычная строка с тем же
       текстом, иначе у таблицы оказалось бы две шапки. */
    const types = tableNodeTypes(state.schema);
    const headerRow = row === 0 && hasHeaderRow(table);
    const headerColumn = hasHeaderColumn(table);
    const cells = [];
    source.forEach((cell, _offset, index) => {
        const keep = !headerRow || (index === 0 && headerColumn);
        const type = keep ? cell.type : types.cell;
        cells.push(type.create(cell.attrs, cell.content, cell.marks));
    });
    const copy = source.type.create(source.attrs, cells);
    const tr = state.tr.insert(rowPos + source.nodeSize, copy);
    cursorInto(tr, rowPos + source.nodeSize + 1);
    dispatch(tr.scrollIntoView());
    return true;
};

/* Копия всей таблицы сразу под ней. Курсор — в её первую ячейку. */
export const duplicateTable = (tablePos) => (state, dispatch) => {
    const table = state.doc.nodeAt(tablePos);
    if (!isTable(table)) return false;
    if (!dispatch) return true;
    const at = tablePos + table.nodeSize;
    const tr = state.tr.insert(at, table.copy(table.content));
    cursorInto(tr, at + 1 + TableMap.get(table).map[0]);
    dispatch(tr.scrollIntoView());
    return true;
};

/* ── Высота строк ─────────────────────────────────────────────────────── */

/* Высота строки — атрибут самой строки (<tr style="height: …">, узел
   WikiTableRow в WikiTableTools.js). Для таблицы это НИЖНЯЯ граница: строка
   с длинным текстом всё равно вырастет под него, а короче текста её не
   сделать. Санитайзер (wiki/sanitize.py) height в style пропускает. */
export const ROW_MIN_HEIGHT = 24;

export const rowPosition = (table, tablePos, index) => {
    let pos = tablePos + 1;
    for (let i = 0; i < index; i += 1) pos += table.child(i).nodeSize;
    return pos;
};

export const rowHeights = (table) => {
    const heights = [];
    table.forEach((row) => heights.push(row.attrs.height || null));
    return heights;
};

/* Строка, чья граница под указателем: нижний край ячейки — граница её
   последней строки (у объединённой ячейки это не та строка, где она
   начинается), верхний край — граница строки над ней. -1 — над первой
   строкой тянуть нечего. */
export const rowAtCellEdge = (table, cellRel, edge) => {
    const rect = TableMap.get(table).findCell(cellRel);
    const index = edge === 'bottom' ? rect.bottom - 1 : rect.top - 1;
    return index >= 0 ? index : -1;
};

/* Ячейки, нижний край которых — нижняя граница строки index: в них встаёт
   маркер границы. Ячейка, что тянется ниже этой строки, его не получает:
   её нижний край — не эта граница. */
export const cellsEndingAtRow = (table, index) => {
    const map = TableMap.get(table);
    const cells = [];
    const seen = new Set();
    for (let col = 0; col < map.width; col += 1) {
        const rel = map.map[index * map.width + col];
        if (seen.has(rel)) continue;
        seen.add(rel);
        if (map.findCell(rel).bottom === index + 1) cells.push(rel);
    }
    return cells;
};

/* Задать (или снять — null) высоту строки. */
export const setRowHeight = (tablePos, index, height) => (state, dispatch) => {
    const table = state.doc.nodeAt(tablePos);
    if (!isTable(table) || index < 0 || index >= table.childCount) return false;
    const row = table.child(index);
    if (!('height' in row.attrs)) return false;
    const value = height ? Math.max(ROW_MIN_HEIGHT, Math.round(height)) : null;
    if ((row.attrs.height || null) === value) return false;
    if (dispatch) {
        dispatch(state.tr.setNodeMarkup(rowPosition(table, tablePos, index), null,
            { ...row.attrs, height: value }));
    }
    return true;
};

export const tableHasSizes = (table) => isFixedLayout(table)
    || columnWidths(table).some((width) => width != null)
    || rowHeights(table).some((height) => height != null);

/* «Сбросить размеры»: ширины столбцов и высоты строк — снова по содержимому. */
export const resetTableSizes = (tablePos) => (state, dispatch) => {
    const table = state.doc.nodeAt(tablePos);
    if (!isTable(table) || !tableHasSizes(table)) return false;
    if (!dispatch) return true;
    const tr = setColumnWidths(state.tr, tablePos, columnWidths(table).map(() => null), { fixed: false });
    let pos = tablePos + 1;
    table.forEach((row) => {
        if (row.attrs.height) tr.setNodeMarkup(tr.mapping.map(pos), null, { ...row.attrs, height: null });
        pos += row.nodeSize;
    });
    tr.setMeta(RESET_WIDTHS_META, true);
    dispatch(tr);
    return true;
};

/* ── Перенос таблицы ──────────────────────────────────────────────────── */

/* Перенести таблицу (copy — поставить копию) на границу между блоками
   верхнего уровня статьи: между абзацами, заголовками, другими таблицами.
   Внутрь абзаца, списка или чужой ячейки таблица не встаёт — так и
   задумано: «переставить таблицу» значит переставить её между частями
   статьи, а вложенная таблица в ячейке — совсем другая вещь.
   false — переносить некуда: граница не та или таблица уже стоит здесь. */
export const moveTable = (tablePos, target, { copy = false } = {}) => (state, dispatch) => {
    const table = state.doc.nodeAt(tablePos);
    if (!isTable(table)) return false;
    if (!Number.isInteger(target) || target < 0 || target > state.doc.content.size) return false;
    if (state.doc.resolve(target).depth !== 0) return false;
    const end = tablePos + table.nodeSize;
    if (!copy && target >= tablePos && target <= end) return false;
    if (!dispatch) return true;
    const tr = state.tr;
    let at = target;
    if (copy) {
        tr.insert(at, table.copy(table.content));
    } else {
        tr.delete(tablePos, end);
        at = tr.mapping.map(target);
        tr.insert(at, table);
    }
    cursorInto(tr, at + 1 + TableMap.get(table).map[0]);
    dispatch(tr);
    return true;
};

/* Граница, ближайшая к указателю по вертикали. boundaries — [{ pos, y }]:
   позиция в документе и экранная координата линии. */
export const nearestBoundary = (boundaries, y) => {
    let best = null;
    boundaries.forEach((boundary) => {
        if (!best || Math.abs(boundary.y - y) < Math.abs(best.y - y)) best = boundary;
    });
    return best;
};

/* ── Положение по ширине ──────────────────────────────────────────────── */

/* Таблица уже колонки текста может стоять у левого края, по центру или у
   правого края. Свободного сдвига на пиксели нет намеренно: у читателей
   разная ширина экрана, и сдвинутая на 300 пикселей таблица на телефоне
   уехала бы за край, а три положения выглядят одинаково везде. */
export const TABLE_ALIGNS = ['left', 'center', 'right'];

export const tableAlign = (table) => (table?.attrs?.align === 'center' || table?.attrs?.align === 'right'
    ? table.attrs.align : 'left');

export const setTableAlign = (tablePos, align) => (state, dispatch) => {
    const table = state.doc.nodeAt(tablePos);
    if (!isTable(table) || !('align' in table.attrs) || !TABLE_ALIGNS.includes(align)) return false;
    if (tableAlign(table) === align) return false;
    if (dispatch) {
        dispatch(state.tr.setNodeMarkup(tablePos, null, { ...table.attrs, align: align === 'left' ? null : align }));
    }
    return true;
};

/* Куда встанет таблица, если её тянут вбок: к ближайшему из трёх положений.
   center — где сейчас середина таблицы под указателем; left/width —
   колонка текста статьи. Таблица шире колонки встаёт только к левому краю. */
export const snapAlign = ({ center, left, width, tableWidth }) => {
    if (!(width > 0) || tableWidth >= width - 1) return 'left';
    const spots = {
        left: left + tableWidth / 2,
        center: left + width / 2,
        right: left + width - tableWidth / 2,
    };
    let best = 'left';
    TABLE_ALIGNS.forEach((align) => {
        if (Math.abs(spots[align] - center) < Math.abs(spots[best] - center)) best = align;
    });
    return best;
};

/* Куда таблицу «на шаг» выше или ниже — для пунктов меню «Переместить выше /
   ниже»: перед соседним блоком сверху или за соседним блоком снизу. Только
   для таблицы верхнего уровня (не в ячейке и не внутри блока оформления).
   null — дальше некуда. */
export const neighborTarget = (doc, tablePos, dir) => {
    const $pos = doc.resolve(tablePos);
    if ($pos.depth !== 0) return null;
    const index = $pos.index(0);
    if (dir < 0) {
        if (index === 0) return null;
        return tablePos - doc.child(index - 1).nodeSize;
    }
    if (index >= doc.childCount - 1) return null;
    const table = doc.child(index);
    return tablePos + table.nodeSize + doc.child(index + 1).nodeSize;
};

/* ── Текст с табуляциями ──────────────────────────────────────────────── */

// Предел разбора — чтобы случайно вставленный лог на сто тысяч строк не
// превратился в таблицу, которая повесит вкладку.
const TABULAR_MAX_ROWS = 1000;
const TABULAR_MAX_COLS = 60;

/* Текст с табуляциями → массив строк таблицы, или null, если это не таблица.
 *
 * Таблицей считается только то, что ею точно является: от двух строк и от
 * двух столбцов, и ВО ВСЕХ строках столбцов поровну — так пишут Excel и
 * Google Таблицы (пустая ячейка — это две табуляции подряд). Текст с отступом
 * табуляцией в одной строке из трёх таблицей не станет.
 *
 * Кавычки — по правилам Excel: ячейка с переносом строки или табуляцией
 * приходит в кавычках, а кавычка внутри неё удваивается. Кавычка, после
 * которой идёт не табуляция и не конец строки («"Да" — ответ»), — это просто
 * текст: такую ячейку берём как есть, вместе с кавычками.
 *
 * Первый столбец, пустой во всех строках, — это отступ табуляцией у каждой
 * строки, а не таблица. */
export const parseTabular = (text) => {
    const src = String(text || '').replace(/\r\n?/g, '\n');
    if (src.indexOf('\t') === -1) return null;
    const rows = [];
    let row = [];
    let field = '';
    let quoted = false;
    let fieldStart = true;
    let quoteAt = -1;
    for (let i = 0; i < src.length; i += 1) {
        const ch = src[i];
        if (quoted) {
            if (ch === '"') {
                const next = src[i + 1];
                if (next === '"') {
                    field += '"';
                    i += 1;
                } else if (next === undefined || next === '\t' || next === '\n') {
                    quoted = false;
                } else {
                    // Не закрывающая кавычка: ячейка — обычный текст с кавычками.
                    quoted = false;
                    field = src.slice(quoteAt, i + 1);
                }
            } else {
                field += ch;
            }
        } else if (ch === '"' && fieldStart) {
            quoted = true;
            fieldStart = false;
            quoteAt = i;
        } else if (ch === '\t') {
            row.push(field);
            field = '';
            fieldStart = true;
        } else if (ch === '\n') {
            row.push(field);
            rows.push(row);
            if (rows.length > TABULAR_MAX_ROWS) return null;
            row = [];
            field = '';
            fieldStart = true;
        } else {
            field += ch;
            fieldStart = false;
        }
    }
    if (quoted) return null;
    if (field !== '' || row.length) {
        row.push(field);
        rows.push(row);
    }
    // Excel заканчивает буфер переводом строки — пустая последняя строка не данные.
    while (rows.length && rows[rows.length - 1].every((cell) => cell === '')) rows.pop();
    if (rows.length < 2 || rows.length > TABULAR_MAX_ROWS) return null;
    const width = rows[0].length;
    if (width < 2 || width > TABULAR_MAX_COLS) return null;
    if (rows.some((cells) => cells.length !== width)) return null;
    if (rows.every((cells) => cells[0] === '')) return null;
    return rows;
};

/* Строки текста → узел таблицы. header — первая строка шапкой, как у
   таблицы, вставленной кнопкой. Перенос строки внутри ячейки — отдельный
   абзац ячейки. */
export const tableFromRows = (schema, rows, { header = true } = {}) => {
    const types = tableNodeTypes(schema);
    const { paragraph } = schema.nodes;
    const blocks = (value) => {
        const lines = String(value || '').split('\n');
        return lines.map((line) => paragraph.create(null, line ? schema.text(line) : null));
    };
    const tableRows = rows.map((cells, index) => types.row.create(null, cells.map(
        (value) => (header && index === 0 ? types.header_cell : types.cell).create(null, blocks(value)),
    )));
    return types.table.create(null, tableRows);
};

/* Таблица → текст с табуляциями. Для мессенджера и блокнота: строки — по
   строкам, ячейки через табуляцию. Абзацы внутри ячейки склеиваются
   пробелом — перенос строки разорвал бы строку таблицы. Ячейку, накрытую
   объединением, оставляем пустой, как это делает Excel. */
export const tableToTabular = (table) => {
    const map = TableMap.get(table);
    const nodes = cellNodes(table);
    const lines = [];
    for (let row = 0; row < map.height; row += 1) {
        const cells = [];
        for (let col = 0; col < map.width; col += 1) {
            const rel = map.map[row * map.width + col];
            const rect = map.findCell(rel);
            if (rect.top !== row || rect.left !== col) {
                cells.push('');
                continue;
            }
            const cell = nodes.get(rel);
            const text = cell.textBetween(0, cell.content.size, ' ', ' ');
            cells.push(text.replace(/[\t\n\r]+/g, ' ').replace(/ {2,}/g, ' ').trim());
        }
        lines.push(cells.join('\t'));
    }
    return lines.join('\n');
};

/* Одна ячейка из Excel или Google Таблиц приходит в буфер таблицей 1×1, и
   вставленная в текст она становилась рамкой вокруг одного слова. Такую
   вставку разворачиваем в содержимое ячейки. Только для таблиц из
   табличных программ: одиночная ячейка, скопированная из другой статьи вики,
   бывает намеренной рамкой-плашкой, и её трогать нельзя. */
const SPREADSHEET_HTML = /google-sheets-html-origin|urn:schemas-microsoft-com:office:excel|ProgId"? content="?Excel/i;

export const isSpreadsheetHtml = (html) => SPREADSHEET_HTML.test(String(html || ''));

export const unwrapSpreadsheetCell = (html) => {
    if (!isSpreadsheetHtml(html) || typeof DOMParser === 'undefined') return html;
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const cells = doc.querySelectorAll('td, th');
    if (cells.length !== 1 || doc.querySelectorAll('table').length !== 1) return html;
    return cells[0].innerHTML;
};

/* ── Браузер: замер и буфер обмена ────────────────────────────────────── */

/* Заморозить ширины столбцов по тому, как таблица нарисована СЕЙЧАС.
 *
 * Зовётся, когда автор потянул границу столбца (на первом сдвиге мыши, а не
 * на нажатии: простой щелчок по границе статью не меняет). Замер по факту
 * значит, что ничего не сдвинется — таблица просто начинает держать те
 * ширины, что уже были на экране. Ширина столбца — по ячейке без
 * объединения; столбец, где таких нет, получает долю объединённой.
 *
 * Мимо истории правок: ⌘Z после перетаскивания возвращает столбцу прежнюю
 * ширину одним нажатием, а не двумя (сначала ширину, потом «заморозку»,
 * которой на экране не видно). */
export const freezeColumnWidths = (view, tablePos) => {
    const table = view.state.doc.nodeAt(tablePos);
    if (!isTable(table) || (isFixedLayout(table) && widthsFixed(table))) return false;
    const map = TableMap.get(table);
    const nodes = cellNodes(table);
    const widths = new Array(map.width).fill(null);
    const spans = [];
    const seen = new Set();
    map.map.forEach((rel, index) => {
        if (seen.has(rel)) return;
        seen.add(rel);
        const dom = view.nodeDOM(tablePos + 1 + rel);
        const width = dom && dom.offsetWidth;
        if (!width) return;
        const left = index % map.width;
        const { colspan } = nodes.get(rel).attrs;
        if (colspan === 1) {
            if (widths[left] == null) widths[left] = width;
        } else {
            spans.push({ left, colspan, width });
        }
    });
    spans.forEach(({ left, colspan, width }) => {
        const missing = [];
        let rest = width;
        for (let j = 0; j < colspan; j += 1) {
            if (widths[left + j] == null) missing.push(left + j);
            else rest -= widths[left + j];
        }
        missing.forEach((col) => { widths[col] = Math.max(1, Math.round(rest / missing.length)); });
    });
    if (widths.some((width) => !width)) return false;
    const tr = setColumnWidths(view.state.tr, tablePos, widths.map(Math.round), { fixed: true });
    if (!tr.docChanged) return false;
    view.dispatch(tr.setMeta('addToHistory', false));
    return true;
};

/* То, что ляжет в буфер при «Копировать таблицу»: HTML в формате ProseMirror
   (data-pm-slice — вставка в вики восстанавливает таблицу один в один) и
   текст с табуляциями. */
export const tableClipboard = (view, tablePos) => {
    const table = view.state.doc.nodeAt(tablePos);
    if (!isTable(table)) return null;
    const { dom } = view.serializeForClipboard(new Slice(Fragment.from(table), 0, 0));
    return { html: dom.innerHTML, text: tableToTabular(table) };
};

/* Записать в буфер HTML и текст разом.
 *
 * Первый путь — copy-событие через execCommand: синхронный, в обработчике
 * нажатия работает во всех браузерах без запроса разрешений, и в нём можно
 * положить оба формата. Слушатель стоит на document в фазе ПЕРЕХВАТА и гасит
 * событие: иначе его поймал бы и сам ProseMirror и записал поверх нашего
 * текущее выделение. Второй путь — Clipboard API, если первый не сработал. */
export const writeClipboard = ({ html, text }) => {
    let done = false;
    const onCopy = (event) => {
        if (!event.clipboardData) return;
        event.clipboardData.setData('text/html', html);
        event.clipboardData.setData('text/plain', text);
        event.preventDefault();
        event.stopImmediatePropagation();
        done = true;
    };
    document.addEventListener('copy', onCopy, true);
    try {
        document.execCommand('copy');
    } catch (e) {
        // execCommand снят — идём вторым путём.
    } finally {
        document.removeEventListener('copy', onCopy, true);
    }
    if (done) return Promise.resolve(true);
    const clip = typeof navigator !== 'undefined' ? navigator.clipboard : null;
    if (clip?.write && typeof window.ClipboardItem === 'function') {
        const item = new window.ClipboardItem({
            'text/html': new Blob([html], { type: 'text/html' }),
            'text/plain': new Blob([text], { type: 'text/plain' }),
        });
        return clip.write([item]).then(() => true, () => false);
    }
    return Promise.resolve(false);
};

/* ⌘ на Mac, Ctrl на остальных — для подсказок «нажмите ⌘V». */
export const pasteKeys = () => {
    const nav = typeof navigator !== 'undefined' ? navigator : null;
    const mac = /Mac|iPhone|iPad/.test(nav?.platform || nav?.userAgent || '');
    return mac ? '⌘V' : 'Ctrl+V';
};
