import React from 'react';
import { useEditorState } from '@tiptap/react';
import { BubbleMenu } from '@tiptap/react/menus';
import {
    ArrowDownToLine, ArrowLeftToLine, ArrowRightToLine, ArrowUpToLine,
    Combine, Heading, Split, Trash2, X,
} from 'lucide-react';

/* Панель управления таблицей — всплывает, когда курсор внутри таблицы.
 *
 * До неё вставка таблицы была дорогой в один конец: кнопка в тулбаре давала
 * 3×3 с шапкой, и на этом всё. Ни строки добавить, ни столбец убрать, ни саму
 * таблицу удалить — человек, промахнувшийся с размером, вычищал ячейки руками
 * или начинал статью заново. Тянуть можно было только ширину колонок.
 *
 * Почему всплывающая панель, а не ещё одна группа кнопок в общем тулбаре:
 * команд тринадцать, и в постоянной панели они занимали бы целую строку,
 * которая девяносто процентов времени неактивна. Здесь они появляются ровно
 * тогда, когда есть над чем работать, и рядом с той таблицей, к которой
 * относятся.
 *
 * placement='bottom': сверху таблицы обычно шапка со смыслом, а снизу —
 * свободное место; вдобавок собственный липкий тулбар редактора стоит именно
 * сверху и перекрывал бы панель на прокрутке.
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

const Sep = () => <span className="mx-0.5 h-4 w-px shrink-0 bg-slate-200" />;

export default function WikiTableMenu({ editor }) {
    /* Доступность команд читаем через useEditorState, а не прямым
       editor.can(): useEditor в TipTap 3 по умолчанию НЕ перерисовывает
       компонент на транзакциях, и обычное editor.can().mergeCells() навсегда
       осталось бы значением на момент открытия редактора — кнопки «объединить»
       и «разбить» не оживали бы никогда. */
    const state = useEditorState({
        editor,
        selector: ({ editor: ed }) => (ed ? {
            canMerge: ed.can().mergeCells(),
            canSplit: ed.can().splitCell(),
        } : { canMerge: false, canSplit: false }),
    });

    if (!editor) return null;

    const run = (command) => () => command(editor.chain().focus()).run();

    return (
        <BubbleMenu
            editor={editor}
            pluginKey="wikiTableMenu"
            // Показываем по факту нахождения в таблице, а не по выделению:
            // у обычного BubbleMenu условие — непустое выделение, а в таблице
            // человек чаще просто стоит курсором в ячейке.
            shouldShow={({ editor: ed }) => ed.isEditable && ed.isActive('table')}
            options={{ placement: 'bottom', offset: 8 }}
            className="flex items-center gap-0.5 rounded-xl border border-slate-200 bg-white/95 px-1.5 py-1 shadow-lg shadow-slate-900/10 backdrop-blur-xl"
        >
            <Btn title="Строка выше" onClick={run((c) => c.addRowBefore())}>
                <ArrowUpToLine size={14} />
            </Btn>
            <Btn title="Строка ниже" onClick={run((c) => c.addRowAfter())}>
                <ArrowDownToLine size={14} />
            </Btn>
            <Btn title="Удалить строку" tone="danger" onClick={run((c) => c.deleteRow())}>
                <X size={14} />
            </Btn>
            <Sep />

            <Btn title="Столбец слева" onClick={run((c) => c.addColumnBefore())}>
                <ArrowLeftToLine size={14} />
            </Btn>
            <Btn title="Столбец справа" onClick={run((c) => c.addColumnAfter())}>
                <ArrowRightToLine size={14} />
            </Btn>
            <Btn title="Удалить столбец" tone="danger" onClick={run((c) => c.deleteColumn())}>
                <X size={14} className="rotate-45" />
            </Btn>
            <Sep />

            <Btn title="Строка-шапка" onClick={run((c) => c.toggleHeaderRow())}>
                <Heading size={14} />
            </Btn>
            {/* Объединение и разбиение — двумя кнопками, а не одной
                mergeOrSplit: та молча делает то одно, то другое в зависимости от
                выделения, и человек не знает заранее, что нажимает. Здесь
                неприменимая команда просто гаснет. */}
            <Btn
                title="Объединить ячейки"
                disabled={!state?.canMerge}
                onClick={run((c) => c.mergeCells())}
            >
                <Combine size={14} />
            </Btn>
            <Btn
                title="Разбить ячейку"
                disabled={!state?.canSplit}
                onClick={run((c) => c.splitCell())}
            >
                <Split size={14} />
            </Btn>
            <Sep />

            {/* Подтверждение: остальные команды правят таблицу по одной строке,
                а эта сносит всю работу целиком, и отменять её пришлось бы
                через Ctrl+Z, о котором вспоминают не все. */}
            <Btn
                title="Удалить таблицу"
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
