import React from 'react';
import { useEditorState } from '@tiptap/react';
import { BubbleMenu } from '@tiptap/react/menus';
import { Columns2, Columns3, CornerDownLeft, Hash, Plus, Square, Trash2, Unlink } from 'lucide-react';

import { BLOCK_TONES } from './WikiBlockNode';

/* Панель управления оформительским блоком — всплывает под блоком, в котором
 * стоит курсор.
 *
 * Почему панель, а не кнопки в общем тулбаре. Команд восемь, и каждая имеет
 * смысл только внутри блока: «три колонки» бессмысленно в обычном абзаце, а
 * «сменить тон» — тем более. В постоянной панели они занимали бы место и
 * девяносто процентов времени стояли бы погашенными. Решение и вид взяты у
 * панели таблицы (WikiTableMenu.jsx) — двух разных всплывающих панелей в
 * одном редакторе быть не должно.
 *
 * Почему тона подписаны словами, а не только кружком. Первая версия была из
 * шести цветных кружков, и разницу между «Внимание» и «Нельзя» приходилось
 * угадывать по оттенку — жёлтый против красного читается, а вот индиговый
 * «Обычная» против фиолетового «Совет» уже нет. Подпись снимает вопрос
 * целиком, а место есть: панель всё равно шире тоновой группы.
 */

const Btn = ({ title, onClick, active, tone = 'plain', children }) => (
    <button
        type="button"
        title={title}
        aria-label={title}
        aria-pressed={active === undefined ? undefined : !!active}
        // Не терять выделение в редакторе: без этого клик по кнопке сначала
        // снимает курсор с блока, и команде применяться уже не к чему.
        onMouseDown={(e) => e.preventDefault()}
        onClick={onClick}
        className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg transition ${
            tone === 'danger'
                ? 'text-rose-500 hover:bg-rose-50'
                : active
                    ? 'bg-indigo-50 text-indigo-600'
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

/* Кружок тона. Цвета продублированы из wiki-blocks.css: панель живёт вне
   .wiki-prose, и переменные --tone-* до неё не доходят. Разойдись они —
   ошибётся только подсказка в панели, а не сама статья, поэтому дублирование
   тут дешевле, чем ещё один слой переменных. */
const TONE_SWATCH = {
    info: { bg: '#eef2ff', line: '#c7d2fe' },
    ok: { bg: '#ecfdf5', line: '#6ee7b7' },
    warn: { bg: '#fffbeb', line: '#fcd34d' },
    danger: { bg: '#fef2f2', line: '#fca5a5' },
    tip: { bg: '#f5f3ff', line: '#c4b5fd' },
    dark: { bg: '#0f172a', line: '#0f172a' },
};

const ToneButton = ({ tone, active, onClick }) => {
    const swatch = TONE_SWATCH[tone.value] || TONE_SWATCH.info;
    return (
        <button
            type="button"
            title={`${tone.label} — ${tone.hint}`}
            aria-label={`Тон «${tone.label}»`}
            aria-pressed={!!active}
            onMouseDown={(e) => e.preventDefault()}
            onClick={onClick}
            className={`flex shrink-0 items-center gap-1.5 rounded-lg px-1.5 py-1 text-[11px] transition ${
                active ? 'bg-slate-100 font-semibold text-slate-700' : 'text-slate-500 hover:bg-slate-50'
            }`}
        >
            <span
                className="h-3 w-3 shrink-0 rounded-full"
                style={{ backgroundColor: swatch.bg, boxShadow: `inset 0 0 0 1px ${swatch.line}` }}
            />
            {tone.label}
        </button>
    );
};

/* Что вокруг курсора. Возвращает атрибуты ближайших блоков каждого вида —
   именно ближайших, потому что карточка лежит внутри сетки, и «сменить тон»
   относится к карточке, а «три колонки» к сетке. */
const EMPTY = {
    card: null, cards: null, note: null, lead: null,
    stat: null, stats: null, gallery: null, innermost: null,
};

const readBlocks = (ed) => {
    const { $from } = ed.state.selection;
    const found = { ...EMPTY };
    for (let depth = $from.depth; depth > 0; depth -= 1) {
        const node = $from.node(depth);
        if (node.type.name !== 'wikiBlock') continue;
        const kind = node.attrs.kind;
        // Идём ОТ КУРСОРА НАРУЖУ, поэтому первый встреченный блок и есть
        // ближайший — тот, к которому относятся «разобрать» и «удалить».
        if (!found.innermost) found.innermost = kind;
        if (kind in found && !found[kind]) {
            found[kind] = { tone: node.attrs.tone, cols: node.attrs.cols, numbered: !!node.attrs.numbered };
        }
    }
    return found;
};

/* Как назвать ближайший блок в подписи кнопки. */
const TARGET_LABELS = {
    gallery: 'галерею',
    card: 'карточку',
    cards: 'сетку карточек',
    stat: 'показатель',
    stats: 'сетку показателей',
    note: 'плашку',
    lead: 'вводку',
};

export default function WikiBlockMenu({ editor, onAddFrame }) {
    /* Состояние читаем через useEditorState: useEditor в TipTap 3 по умолчанию
       НЕ перерисовывает компонент на транзакциях, и прочитанное прямо в теле
       рендера навсегда осталось бы значением на момент открытия редактора —
       панель показывала бы тон первой попавшейся плашки для всех.

       Проверка `ed && !ed.isDestroyed` обязательна: подписка успевает дёрнуть
       селектор на редакторе, у которого ещё нет view (или уже нет), и падение
       СЕЛЕКТОРА роняет весь редактор — человек видит витрину вместо статьи.
       Та же оговорка и по той же причине стоит в WikiTableMenu.jsx. */
    const state = useEditorState({
        editor,
        selector: ({ editor: ed }) => (ed && !ed.isDestroyed ? readBlocks(ed) : EMPTY),
    });

    if (!editor) return null;

    const card = state?.card || null;
    const note = state?.note || null;
    const toned = note || card;
    const tonedKind = note ? 'note' : 'card';
    const target = TARGET_LABELS[state?.innermost] || 'блок';

    /* Столбцы и «добавить ячейку» одинаковы у обеих сеток, поэтому панель
       работает с той, внутри которой стоит курсор, а не с сеткой карточек
       поимённо. Нумерация — только у карточек: пронумерованные показатели
       читались бы как список шагов, хотя это величины, а не порядок. */
    const gridKind = state?.cards ? 'cards' : (state?.stats ? 'stats' : null);
    const grid = gridKind ? state[gridKind] : null;

    /* Панель привязана к САМОМУ ВНЕШНЕМУ блоку, а не к выделению: у сетки
       карточек это её нижний край, а не край той карточки, в которой стоит
       курсор, — иначе панель ложится поверх соседней карточки, то есть
       закрывает ровно то, с чем человек работает. */
    const blockRect = () => {
        const { state: pmState, view } = editor;
        const at = view.domAtPos(pmState.selection.from)?.node;
        const start = at instanceof HTMLElement ? at : at?.parentElement;
        let block = start?.closest('[data-wiki-block]');
        if (!block) return null;
        let outer = block.parentElement?.closest('[data-wiki-block]');
        while (outer) {
            block = outer;
            outer = block.parentElement?.closest('[data-wiki-block]');
        }
        /* Делим прямоугольник на масштаб раздела (wiki-scale.css): панель
           лежит ВНУТРИ масштабированного поддерева, а getBoundingClientRect
           отдаёт координаты уже в масштабе — без деления браузер умножит их
           второй раз, и на широком мониторе панель уедет за нижний край окна.
           Подробности — в комментарии WikiTableMenu.jsx. */
        const anchor = block;
        return {
            getBoundingClientRect: () => {
                const r = anchor.getBoundingClientRect();
                const z = anchor.currentCSSZoom || 1;
                return z === 1 ? r : new DOMRect(r.x / z, r.y / z, r.width / z, r.height / z);
            },
            contextElement: anchor,
        };
    };

    const run = (command) => () => command(editor.chain().focus()).run();

    return (
        <BubbleMenu
            editor={editor}
            pluginKey="wikiBlockMenu"
            // Показываем по факту нахождения в блоке, а не по выделению: у
            // обычного BubbleMenu условие — непустое выделение, а человек
            // чаще просто стоит курсором внутри плашки.
            /* ...и НЕ показываем, когда курсор в таблице: таблица внутри
               карточки — обычное дело, а две всплывающие панели у одного
               места накладываются друг на друга. Панель таблицы в этом случае
               и нужнее: человек правит клетки, а не тон карточки. */
            shouldShow={({ editor: ed }) => ed.isEditable
                && ed.isActive('wikiBlock') && !ed.isActive('table')}
            getReferencedVirtualElement={blockRect}
            options={{ placement: 'bottom-start', offset: 8 }}
            className="flex flex-wrap items-center gap-0.5 rounded-xl border border-slate-200 bg-white/95 px-1.5 py-1 shadow-lg shadow-slate-900/10 backdrop-blur-xl"
        >
            {toned && (
                <>
                    <Label>Тон</Label>
                    {BLOCK_TONES.map((tone) => (
                        <ToneButton
                            key={tone.value}
                            tone={tone}
                            active={(toned.tone || 'info') === tone.value}
                            onClick={run((c) => c.setWikiBlockAttrs(tonedKind, { tone: tone.value }))}
                        />
                    ))}
                    <Sep />
                </>
            )}

            {grid && (
                <>
                    <Label>Сетка</Label>
                    <Btn
                        title="Один столбец"
                        active={grid.cols === '1'}
                        onClick={run((c) => c.setWikiBlockAttrs(gridKind, { cols: '1' }))}
                    >
                        <Square size={14} />
                    </Btn>
                    <Btn
                        title="Два столбца"
                        active={grid.cols === '2' || !grid.cols}
                        onClick={run((c) => c.setWikiBlockAttrs(gridKind, { cols: '2' }))}
                    >
                        <Columns2 size={14} />
                    </Btn>
                    <Btn
                        title="Три столбца"
                        active={grid.cols === '3'}
                        onClick={run((c) => c.setWikiBlockAttrs(gridKind, { cols: '3' }))}
                    >
                        <Columns3 size={14} />
                    </Btn>
                    {gridKind === 'cards' && (
                        <Btn
                            title="Нумеровать карточки"
                            active={grid.numbered}
                            onClick={run((c) => c.setWikiBlockAttrs('cards', { numbered: !grid.numbered }))}
                        >
                            <Hash size={14} />
                        </Btn>
                    )}
                    <Btn
                        title={gridKind === 'cards' ? 'Добавить карточку' : 'Добавить показатель'}
                        onClick={run((c) => c.addWikiCard())}
                    >
                        <Plus size={14} />
                    </Btn>
                    <Sep />
                </>
            )}

            {/* ГАЛЕРЕЯ: «+ кадр». У сеток такая кнопка есть с самого начала, а
                у галереи её не было — и добавить кадр в уже собранную галерею
                было нечем вовсе. Кнопка картинки в панели самого кадра собирает
                галерею из соседних картинок, но ВНУТРИ готовой она намеренно
                отказывается работать, а вставка скриншота кладёт его туда, где
                стоит курсор, то есть чаще всего под галерею. */}
            {state?.gallery && onAddFrame && (
                <>
                    <Label>Галерея</Label>
                    <label
                        title="Добавить кадр"
                        className="grid h-7 w-7 shrink-0 cursor-pointer place-items-center rounded-lg
                            text-slate-500 transition hover:bg-slate-100"
                        onMouseDown={(e) => e.preventDefault()}
                    >
                        <Plus size={14} />
                        <input
                            type="file"
                            className="hidden"
                            accept="image/*"
                            multiple
                            onChange={(e) => { onAddFrame(Array.from(e.target.files || [])); e.target.value = ''; }}
                        />
                    </label>
                    <Sep />
                </>
            )}

            <Btn title="Абзац после блока (Cmd/Ctrl + Enter)"
                 onClick={run((c) => c.paragraphAfterWikiBlock())}>
                <CornerDownLeft size={14} />
            </Btn>
            {/* «Разобрать» и «удалить» — две разные кнопки. Одной их сделать
                нельзя: «убрать оформление» и «стереть текст» — это разные
                намерения, и человек, нажавший не то, теряет написанное.

                Обе работают по БЛИЖАЙШЕМУ блоку, поэтому внутри сетки они
                относятся к карточке, а не ко всей сетке. Подпись об этом
                говорит прямо: «удалить блок», стоящее в карточке и сносящее
                весь ряд, — это ловушка, а не команда. */}
            <Btn title={`Разобрать ${target}: текст останется, оформление уйдёт`}
                 onClick={run((c) => c.unwrapWikiBlock(null))}>
                <Unlink size={14} />
            </Btn>
            <Btn
                title={`Удалить ${target} вместе с содержимым`}
                tone="danger"
                onClick={() => {
                    if (!window.confirm(`Удалить ${target} вместе с содержимым?`)) return;
                    editor.chain().focus().removeWikiBlock(null).run();
                }}
            >
                <Trash2 size={14} />
            </Btn>
        </BubbleMenu>
    );
}
