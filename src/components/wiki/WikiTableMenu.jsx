import React from 'react';
import { useEditorState } from '@tiptap/react';
import { BubbleMenu } from '@tiptap/react/menus';
import {
    ArrowDownToLine, ArrowLeftToLine, ArrowRightToLine, ArrowUpToLine,
    Heading, SquareMinus, TableCellsMerge, TableCellsSplit, Trash2,
} from 'lucide-react';

/* Панель управления таблицей — всплывает под таблицей, когда курсор внутри неё.
 *
 * До неё вставка таблицы была дорогой в один конец: кнопка в тулбаре давала
 * 3×3 с шапкой, и на этом всё. Ни строки добавить, ни столбец убрать, ни саму
 * таблицу удалить — человек, промахнувшийся с размером, вычищал ячейки руками
 * или начинал статью заново. Тянуть можно было только ширину колонок.
 *
 * Почему всплывающая панель, а не ещё одна группа кнопок в общем тулбаре:
 * команд десять, и в постоянной панели они занимали бы целую строку, которая
 * девяносто процентов времени неактивна. Здесь они появляются ровно тогда,
 * когда есть над чем работать, и рядом с той таблицей, к которой относятся.
 *
 * Кнопки подписаны словами «Строка» и «Столбец». Первый вариант был без
 * подписей, и на скриншоте прода стало видно, почему так нельзя: крестик
 * «удалить столбец» (тот же значок, повёрнутый на 45°) читается как «плюс», то
 * есть кнопка удаления выглядит кнопкой добавления. Иконка-подсказка в title
 * не спасает — до неё надо додуматься навести.
 */

const Btn = ({ title, onClick, disabled, tone = 'plain', children }) => (
    <button
        type="button"
        title={title}
        aria-label={title}
        disabled={disabled}
        // Не терять выделение в редакторе: без этого клик по кнопке сначала
        // снимает курсор с ячейки, и команда применяться уже не к чему.
        onMouseDown={(e) => e.preventDefault()}
        onClick={onClick}
        className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg transition disabled:opacity-30 ${
            tone === 'danger'
                ? 'text-rose-500 hover:bg-rose-50'
                : 'text-slate-600 hover:bg-slate-100'
        }`}
    >
        {children}
    </button>
);

const Label = ({ children }) => (
    <span className="shrink-0 pl-1 pr-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-slate-400">
        {children}
    </span>
);

const Sep = () => <span className="mx-1 h-4 w-px shrink-0 bg-slate-200" />;

export default function WikiTableMenu({ editor }) {
    /* Доступность команд читаем через useEditorState, а не прямым
       editor.can(): useEditor в TipTap 3 по умолчанию НЕ перерисовывает
       компонент на транзакциях, и обычное editor.can().mergeCells() навсегда
       осталось бы значением на момент открытия редактора — кнопки «объединить»
       и «разбить» не оживали бы никогда. */
    const state = useEditorState({
        editor,
        /* Проверки НЕ ХВАТАЛО на `ed`: подписка успевает дёрнуть селектор на
           редакторе, у которого ещё нет view (или уже нет — destroy() обнуляет
           commandManager), и `ed.can()` падает внутри TipTap с «Cannot read
           properties of null (reading 'can')». Падение в селекторе роняет весь
           редактор: React разбирает поддерево, и человек видит вместо статьи
           витрину — то есть кнопка «Редактировать» как будто ничего не делает.
           Поймано на открытии редактора из каталога, где статья приезжает
           отдельным запросом: там редактор монтируется под остальной загрузкой
           витрины и промах по этому окну случался в половине заходов.
           isDestroyed у TipTap — это `editorView?.isDestroyed ?? true`, то есть
           одна проверка закрывает оба случая: «ещё не готов» и «уже разобран».
           Тем же условием защищается сам TipTap перед обращением к view. */
        selector: ({ editor: ed }) => (ed && !ed.isDestroyed ? {
            canMerge: ed.can().mergeCells(),
            canSplit: ed.can().splitCell(),
        } : { canMerge: false, canSplit: false }),
    });

    if (!editor) return null;

    const run = (command) => () => command(editor.chain().focus()).run();

    /* Панель привязана к ТАБЛИЦЕ, а не к выделенной ячейке.
     *
     * По умолчанию BubbleMenu считает положение от выделения, и панель ложилась
     * поверх следующей строки таблицы — то есть закрывала собой ровно то, что
     * человек правит. От таблицы целиком она встаёт под её нижним краем и
     * ничего не перекрывает. */
    const tableRect = () => {
        const { state: pmState, view } = editor;
        const at = view.domAtPos(pmState.selection.from)?.node;
        const start = at instanceof HTMLElement ? at : at?.parentElement;
        const table = start?.closest('table');
        if (!table) return null;
        return { getBoundingClientRect: () => table.getBoundingClientRect(),
                 contextElement: table };
    };

    return (
        <BubbleMenu
            editor={editor}
            pluginKey="wikiTableMenu"
            // Показываем по факту нахождения в таблице, а не по выделению:
            // у обычного BubbleMenu условие — непустое выделение, а в таблице
            // человек чаще просто стоит курсором в ячейке.
            shouldShow={({ editor: ed }) => ed.isEditable && ed.isActive('table')}
            getReferencedVirtualElement={tableRect}
            options={{ placement: 'bottom-start', offset: 8 }}
            className="flex items-center gap-0.5 rounded-xl border border-slate-200 bg-white/95 px-1.5 py-1 shadow-lg shadow-slate-900/10 backdrop-blur-xl"
        >
            <Label>Строка</Label>
            <Btn title="Добавить строку выше" onClick={run((c) => c.addRowBefore())}>
                <ArrowUpToLine size={14} />
            </Btn>
            <Btn title="Добавить строку ниже" onClick={run((c) => c.addRowAfter())}>
                <ArrowDownToLine size={14} />
            </Btn>
            <Btn title="Удалить строку" tone="danger" onClick={run((c) => c.deleteRow())}>
                <SquareMinus size={14} />
            </Btn>
            <Sep />

            <Label>Столбец</Label>
            <Btn title="Добавить столбец слева" onClick={run((c) => c.addColumnBefore())}>
                <ArrowLeftToLine size={14} />
            </Btn>
            <Btn title="Добавить столбец справа" onClick={run((c) => c.addColumnAfter())}>
                <ArrowRightToLine size={14} />
            </Btn>
            <Btn title="Удалить столбец" tone="danger" onClick={run((c) => c.deleteColumn())}>
                <SquareMinus size={14} />
            </Btn>
            <Sep />

            <Btn title="Строка-шапка: включить или убрать"
                 onClick={run((c) => c.toggleHeaderRow())}>
                <Heading size={14} />
            </Btn>
            {/* Объединение и разбиение — двумя кнопками, а не одной
                mergeOrSplit: та молча делает то одно, то другое в зависимости от
                выделения, и человек не знает заранее, что нажимает. Здесь
                неприменимая команда просто гаснет. */}
            <Btn
                title="Объединить выделенные ячейки"
                disabled={!state?.canMerge}
                onClick={run((c) => c.mergeCells())}
            >
                <TableCellsMerge size={14} />
            </Btn>
            <Btn
                title="Разбить объединённую ячейку"
                disabled={!state?.canSplit}
                onClick={run((c) => c.splitCell())}
            >
                <TableCellsSplit size={14} />
            </Btn>
            <Sep />

            {/* Подтверждение: остальные команды правят таблицу по одной строке,
                а эта сносит всю работу целиком, и отменять её пришлось бы
                через Ctrl+Z, о котором вспоминают не все. */}
            <Btn
                title="Удалить таблицу целиком"
                tone="danger"
                onClick={() => {
                    if (!window.confirm('Удалить таблицу вместе с содержимым?')) return;
                    editor.chain().focus().deleteTable().run();
                }}
            >
                <Trash2 size={14} />
            </Btn>
        </BubbleMenu>
    );
}
