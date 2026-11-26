/* Вид таблицы в редакторе: та же таблица TipTap плюс кнопки «+» и угловая
 * ручка.
 *
 * «+» под таблицей добавляет строку в конец, «+» справа — столбец. Это самое
 * частое действие при заполнении, и раньше до него было три шага: поставить
 * курсор в последнюю строку, найти панель под таблицей, угадать нужный значок
 * среди десяти. Клавиша Tab в последней ячейке тоже добавляет строку, но о ней
 * знают не все.
 *
 * Угловая ручка (шесть точек над левым верхним углом) — вся таблица: нажатие
 * открывает её меню (копировать, вырезать, удалить…), а если потянуть —
 * таблица переезжает в другое место статьи. Синяя линия показывает, куда она
 * встанет; у края окна страница прокручивается сама; Esc — отмена; с зажатым
 * ⌥ (Alt) встаёт копия, а таблица остаётся на месте, как при перетаскивании
 * файла в Finder. Встаёт таблица только между частями статьи (абзацами,
 * заголовками, другими таблицами) — не внутрь абзаца и не в чужую ячейку.
 *
 * Всё это живёт ВНУТРИ вида таблицы, а не поверх страницы: так оно едет вместе
 * с таблицей при прокрутке и при любой перекладке текста, и считать его
 * координаты не нужно. Обёртка .wiki-table-frame облегает саму таблицу, и
 * кнопки встают ровно по её краю, а не по краю колонки текста.
 *
 * В сохранённую статью ничего из этого не попадает: вид узла живёт только в
 * редакторе, а HTML для базы собирает renderHTML самой таблицы.
 */
import { TableView } from '@tiptap/extension-table';
import {
    appendColumn, appendRow, moveTable, nearestBoundary, setTableAlign, snapAlign, tableAlign,
} from './tableTools.js';
import { openTableMenu } from './WikiTableTools.js';

const PLUS_ICON = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">'
    + '<path d="M6 1.5v9M1.5 6h9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

const dot = (x, y) => `<circle cx="${x}" cy="${y}" r="1.2"/>`;
const MOVE_ICON = '<svg width="9" height="13" viewBox="0 0 8 12" fill="currentColor" aria-hidden="true">'
    + `${dot(2, 2)}${dot(6, 2)}${dot(2, 6)}${dot(6, 6)}${dot(2, 10)}${dot(6, 10)}</svg>`;

// Сдвиг, после которого нажатие на ручку считается перетаскиванием, а не щелчком.
const DRAG_THRESHOLD = 5;
// Полоса у края окна, где страница начинает прокручиваться сама, и предел шага.
const SCROLL_EDGE = 56;
const SCROLL_STEP = 22;

const scrollParent = (element) => {
    const doc = element.ownerDocument;
    for (let node = element.parentElement; node && node !== doc.body; node = node.parentElement) {
        const { overflowY } = getComputedStyle(node);
        if (/(auto|scroll|overlay)/.test(overflowY) && node.scrollHeight > node.clientHeight + 1) return node;
    }
    return doc.scrollingElement || doc.documentElement;
};

export default class WikiTableView extends TableView {
    constructor(node, cellMinWidth, view, HTMLAttributes) {
        super(node, cellMinWidth, view, HTMLAttributes);
        this.view = view;
        this.dragged = false;
        this.frame = document.createElement('div');
        this.frame.className = 'wiki-table-frame';
        this.dom.insertBefore(this.frame, this.table);
        this.frame.appendChild(this.table);
        this.frame.appendChild(this.addButton('row', 'Добавить строку'));
        this.frame.appendChild(this.addButton('column', 'Добавить столбец'));
        this.frame.appendChild(this.moveHandle());
        this.syncLayout(node);
    }

    /* Признак «ширины задал автор» (WikiTable, fixedLayout) — классом на самой
       таблице. Вид узла рисует таблицу сам, мимо renderHTML, поэтому класс из
       сохранённой статьи сюда не доезжает без этой строки. */
    syncLayout(node) {
        this.table.classList.toggle('wiki-table-fixed', Boolean(node.attrs.fixedLayout));
        // Положение по ширине — рамке: она облегает таблицу, и её поля auto
        // ставят таблицу по центру или к правому краю колонки текста.
        const align = tableAlign(node);
        this.frame.classList.toggle('wiki-table-frame--center', align === 'center');
        this.frame.classList.toggle('wiki-table-frame--right', align === 'right');
    }

    update(node) {
        if (!super.update(node)) return false;
        this.syncLayout(node);
        return true;
    }

    addButton(kind, label) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `wiki-table-add wiki-table-add--${kind}`;
        btn.setAttribute('contenteditable', 'false');
        // Tab в таблице — переход по ячейкам; кнопки в этот круг не входят.
        btn.tabIndex = -1;
        btn.title = label;
        btn.setAttribute('aria-label', label);
        btn.innerHTML = PLUS_ICON;
        // Курсор остаётся в тексте: нажатие не должно уводить фокус из редактора.
        btn.addEventListener('mousedown', (event) => event.preventDefault());
        btn.addEventListener('click', (event) => {
            event.preventDefault();
            const pos = this.tablePos();
            if (pos == null) return;
            const command = kind === 'row' ? appendRow(pos) : appendColumn(pos);
            if (command(this.view.state, (tr) => this.view.dispatch(tr))) this.view.focus();
        });
        return btn;
    }

    moveHandle() {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'wiki-table-handle';
        btn.setAttribute('contenteditable', 'false');
        btn.tabIndex = -1;
        btn.title = 'Таблица: нажмите — меню; тяните вверх или вниз — переставить, '
            + 'влево или вправо — к краю или по центру';
        btn.setAttribute('aria-label', 'Таблица: меню и перенос');
        btn.setAttribute('aria-haspopup', 'menu');
        btn.innerHTML = MOVE_ICON;
        btn.addEventListener('mousedown', (event) => event.preventDefault());
        btn.addEventListener('pointerdown', (event) => this.press(event));
        btn.addEventListener('click', (event) => {
            event.preventDefault();
            // Щелчок, которым закончилось перетаскивание, меню не открывает.
            if (this.dragged) return;
            const pos = this.tablePos();
            if (pos != null) openTableMenu(this.view, pos, btn);
        });
        return btn;
    }

    /* Нажали на угловую ручку: пока указатель не сдвинулся — это щелчок (меню),
       сдвинулся дальше порога — перетаскивание таблицы. */
    press(event) {
        if (event.button !== 0 || !this.view.editable) return;
        const win = this.view.dom.ownerDocument.defaultView || window;
        const startX = event.clientX;
        const startY = event.clientY;
        let drag = null;
        const stop = () => {
            win.removeEventListener('pointermove', onMove);
            win.removeEventListener('pointerup', onUp);
            win.removeEventListener('pointercancel', onCancel);
            win.removeEventListener('keydown', onKey, true);
        };
        const onMove = (ev) => {
            if (!drag) {
                if (Math.hypot(ev.clientX - startX, ev.clientY - startY) < DRAG_THRESHOLD) return;
                drag = this.beginDrag(startX);
                if (!drag) {
                    stop();
                    return;
                }
            }
            drag.pointer(ev);
        };
        const onUp = (ev) => {
            stop();
            if (!drag) return;
            this.dragged = true;
            win.setTimeout(() => { this.dragged = false; }, 0);
            drag.drop(ev);
        };
        const onCancel = () => {
            stop();
            if (drag) drag.cancel();
        };
        const onKey = (ev) => {
            if (ev.key !== 'Escape' || !drag) return;
            ev.preventDefault();
            ev.stopPropagation();
            stop();
            drag.cancel();
            // Кнопку ещё держат — щелчок от её отпускания меню не открывает.
            this.dragged = true;
            win.addEventListener('pointerup', () => win.setTimeout(() => { this.dragged = false; }, 0), { once: true });
        };
        win.addEventListener('pointermove', onMove);
        win.addEventListener('pointerup', onUp);
        win.addEventListener('pointercancel', onCancel);
        win.addEventListener('keydown', onKey, true);
    }

    /* Перетаскивание таблицы. Куда встанет — граница между блоками верхнего
       уровня, ближайшая к указателю по вертикали; линия рисуется посередине
       просвета между блоками.
       Пока указатель над самой таблицей, места нет: таблица «на своём месте».
       Как только он ушёл выше или ниже неё — ближайшая граница, но не те две,
       что вплотную к самой таблице (перенос туда ничего не меняет). Раньше
       они и ловили указатель: тянешь таблицу вниз, а линии нет, пока не
       уведёшь мышь за середину следующего абзаца, — и казалось, что таблица не
       двигается вовсе.
       За курсором едет уменьшенная копия таблицы: видно, что её «взяли».
       Вбок — пока указатель над самой таблицей, она встаёт к левому краю, по
       центру или к правому краю колонки текста; где окажется — показывает
       пунктирная рамка. */
    beginDrag(startX) {
        const { view } = this;
        const tablePos = this.tablePos();
        if (tablePos == null) return null;
        const doc = view.dom.ownerDocument;
        const win = doc.defaultView || window;
        const scroller = scrollParent(view.dom);
        const line = doc.createElement('div');
        line.className = 'wiki-table-drop-line';
        doc.body.appendChild(line);
        const preview = doc.createElement('div');
        preview.className = 'wiki-table-align-preview';
        doc.body.appendChild(preview);
        const ghost = this.ghost(doc);
        this.dom.classList.add('is-moving');
        doc.body.classList.add('wiki-table-dragging');
        // Где схватили таблицу относительно её левого края: тянут за ручку, и
        // край таблицы едет вместе с указателем на том же расстоянии.
        const grabDX = startX - this.table.getBoundingClientRect().left;

        let last = null;
        let target = null;
        let align = null;
        let frame = 0;

        /* Колонка текста, в которой стоит таблица: поле обёртки без её полей
           под ручки (у вложенной таблицы — своя ячейка, а не вся статья). */
        const column = () => {
            const rect = this.dom.getBoundingClientRect();
            const style = win.getComputedStyle(this.dom);
            const padLeft = parseFloat(style.paddingLeft) || 0;
            const padRight = parseFloat(style.paddingRight) || 0;
            return { left: rect.left + padLeft, width: rect.width - padLeft - padRight };
        };

        const sideways = (own) => {
            const node = view.state.doc.nodeAt(tablePos);
            const col = column();
            const next = snapAlign({
                center: last.clientX - grabDX + own.width / 2, left: col.left, width: col.width, tableWidth: own.width,
            });
            align = node && next !== tableAlign(node) ? next : null;
            if (!align) {
                preview.style.display = 'none';
                return;
            }
            const offset = { left: 0, center: (col.width - own.width) / 2, right: col.width - own.width }[align];
            preview.style.display = 'block';
            preview.style.left = `${Math.round(col.left + offset)}px`;
            preview.style.top = `${Math.round(own.top)}px`;
            preview.style.width = `${Math.round(own.width)}px`;
            preview.style.height = `${Math.round(own.height)}px`;
        };

        const boundaries = () => {
            const list = [];
            let prevBottom = null;
            view.state.doc.forEach((child, offset) => {
                const dom = view.nodeDOM(offset);
                if (!(dom instanceof Element)) return;
                const rect = dom.getBoundingClientRect();
                list.push({ pos: offset, y: prevBottom == null ? rect.top - 3 : (prevBottom + rect.top) / 2 });
                prevBottom = rect.bottom;
            });
            if (prevBottom != null) list.push({ pos: view.state.doc.content.size, y: prevBottom + 3 });
            return list;
        };

        const place = () => {
            if (!last) return;
            const copy = Boolean(last.altKey);
            ghost.style.transform = `translate(${Math.round(last.clientX + 14)}px, ${Math.round(last.clientY + 14)}px)`;
            const own = this.table.getBoundingClientRect();
            const overItself = last.clientY >= own.top && last.clientY <= own.bottom;
            // Над самой таблицей — тянут вбок: положение по ширине.
            if (overItself && !copy) sideways(own);
            else {
                align = null;
                preview.style.display = 'none';
            }
            const table = view.state.doc.nodeAt(tablePos);
            const end = tablePos + (table ? table.nodeSize : 0);
            const candidates = overItself ? [] : boundaries()
                .filter((boundary) => copy || (boundary.pos !== tablePos && boundary.pos !== end));
            const best = nearestBoundary(candidates, last.clientY);
            const ok = Boolean(best) && moveTable(tablePos, best.pos, { copy })(view.state);
            target = ok ? best.pos : null;
            ghost.classList.toggle('is-idle', !ok && !align);
            if (!ok) {
                line.style.display = 'none';
                return;
            }
            const box = view.dom.getBoundingClientRect();
            line.style.display = 'block';
            line.style.top = `${Math.round(best.y - 1.5)}px`;
            line.style.left = `${Math.round(box.left)}px`;
            line.style.width = `${Math.round(box.width)}px`;
            line.classList.toggle('is-copy', copy);
        };

        /* Прокрутка у края: пока указатель у верхнего или нижнего края окна,
           страница едет сама, и линия пересчитывается на каждом шаге. */
        const tick = () => {
            frame = 0;
            if (!last) return;
            const box = scroller === doc.scrollingElement || scroller === doc.documentElement
                ? { top: 0, bottom: win.innerHeight } : scroller.getBoundingClientRect();
            let step = 0;
            if (last.clientY < box.top + SCROLL_EDGE) step = -(box.top + SCROLL_EDGE - last.clientY) / 3;
            else if (last.clientY > box.bottom - SCROLL_EDGE) step = (last.clientY - (box.bottom - SCROLL_EDGE)) / 3;
            if (!step) return;
            scroller.scrollTop += Math.max(-SCROLL_STEP, Math.min(SCROLL_STEP, Math.round(step)));
            place();
            frame = win.requestAnimationFrame(tick);
        };

        const cleanup = () => {
            if (frame) win.cancelAnimationFrame(frame);
            frame = 0;
            last = null;
            line.remove();
            preview.remove();
            ghost.remove();
            this.dom.classList.remove('is-moving');
            doc.body.classList.remove('wiki-table-dragging');
        };

        return {
            pointer: (ev) => {
                last = ev;
                place();
                if (!frame) frame = win.requestAnimationFrame(tick);
            },
            drop: (ev) => {
                last = ev;
                place();
                const at = target;
                const side = align;
                const copy = Boolean(ev.altKey);
                cleanup();
                const dispatch = (tr) => view.dispatch(tr);
                if (side) {
                    setTableAlign(tablePos, side)(view.state, dispatch);
                    view.focus();
                    return;
                }
                if (at == null) return;
                moveTable(tablePos, at, { copy })(view.state, dispatch);
                view.focus();
            },
            cancel: cleanup,
        };
    }

    /* Уменьшенная копия таблицы под курсором: первые строки, без ручек и
       маркеров редактора, не шире 280 пикселей. Классы wiki-scope и
       wiki-prose — чтобы копия была нарисована теми же правилами, что и
       таблица в статье (и в тёмной теме тоже). */
    ghost(doc) {
        const ghost = doc.createElement('div');
        ghost.className = 'wiki-scope wiki-table-ghost';
        const inner = doc.createElement('div');
        inner.className = 'wiki-prose';
        const clone = this.table.cloneNode(true);
        clone.querySelectorAll('.wiki-table-grip, .column-resize-handle, .wiki-row-resize-handle, .ProseMirror-widget')
            .forEach((element) => element.remove());
        clone.querySelectorAll('[contenteditable]').forEach((element) => element.removeAttribute('contenteditable'));
        clone.querySelectorAll('tr').forEach((row, index) => { if (index >= 5) row.remove(); });
        clone.style.margin = '0';
        const width = Math.max(1, this.table.getBoundingClientRect().width);
        const scale = Math.min(1, 280 / width);
        inner.style.width = `${Math.ceil(width)}px`;
        inner.style.transform = `scale(${scale})`;
        inner.appendChild(clone);
        ghost.appendChild(inner);
        doc.body.appendChild(ghost);
        ghost.style.width = `${Math.ceil(width * scale)}px`;
        ghost.style.height = `${Math.ceil(inner.offsetHeight * scale)}px`;
        return ghost;
    }

    /* Позиция таблицы в документе. Вид узла создаёт плагин ширины столбцов,
       и getPos ему не передаётся — берём её у ProseMirror по началу тела
       таблицы (tbody — это contentDOM узла). */
    tablePos() {
        try {
            return this.view.posAtDOM(this.contentDOM, 0) - 1;
        } catch (e) {
            return null;
        }
    }

    // Нажатия по «+» и по угловой ручке ProseMirror не разбирает: иначе он
    // ставил бы курсор туда, куда пришёлся щелчок, раньше, чем кнопка сработает.
    stopEvent(event) {
        return Boolean(event.target?.closest?.('.wiki-table-add, .wiki-table-handle'));
    }
}
