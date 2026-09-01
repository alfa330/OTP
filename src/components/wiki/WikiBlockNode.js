/* Оформительские блоки статьи — узел схемы редактора и его команды.
 *
 * ЗАЧЕМ УЗЕЛ, А НЕ ПРОСТО CSS. Стили из wiki-blocks.css раскрасили бы блок и
 * без всякого расширения — но только ОДИН раз. TipTap разбирает тело статьи по
 * своей схеме и собирает обратно из неё же: всё, чего в схеме нет, при первом
 * же открытии редактора разворачивается в обычные абзацы. То есть красиво
 * оформленная статья теряла бы оформление ровно тогда, когда её пришли
 * поправить, — и молча, без единого сообщения. Так уже случилось в этом
 * разделе с раскрывающимися блоками <details>, и второй раз наступать на это
 * незачем.
 *
 * ПОЧЕМУ ОДИН УЗЕЛ НА ВСЕ БЛОКИ. Вводка, плашка, сетка и карточка отличаются
 * не поведением, а видом: у всех внутри обычное содержимое статьи, все живут
 * на верхнем уровне, все сериализуются в <div> с data-атрибутами. Пять
 * отдельных узлов дали бы пять почти одинаковых файлов и пять мест, где можно
 * забыть про санитайзер. Разницу несёт атрибут kind.
 *
 * ПОЧЕМУ СПИСКИ — НЕ УЗЕЛ. «Шаги», «чипы» и «галочки» остаются НАСТОЯЩИМИ
 * <ol>/<ul>: это по-прежнему список, просто нарисованный иначе. Своим узлом
 * они перестали бы быть списком для всего остального — для поиска по тексту,
 * для выгрузки, для читалки с экрана, для кнопки «нумерованный список» в
 * тулбаре. Поэтому вид списка живёт атрибутом data-variant на самом списке.
 *
 * СЕРИАЛИЗАЦИЯ. В базу уезжает голый <div data-wiki-block="…"> без единого
 * класса — так же, как у кнопки тренажёра. Имена атрибутов продублированы в
 * ЧЕТЫРЁХ местах: здесь, в wiki/sanitize.py, в SANITIZE_OPTIONS витрины
 * (WikiArticle.jsx) и в наставлении для ИИ (wiki/ai/markup.py). Разойдись
 * любые два — блок сохранится, а покажется безымянным div'ом: без фона, без
 * колонок, без номеров, и ни одной ошибки нигде. Паритет сторожит
 * tests/test_wiki_blocks.py.
 */

import { Extension, Node, mergeAttributes } from '@tiptap/react';

/* Тона плашек и карточек. Порядок — тот, в котором они стоят в панели: от
   нейтрального к тревожному, тёмный отдельно в конце. */
export const BLOCK_TONES = [
    { value: 'info', label: 'Обычная', hint: 'нейтральное уточнение' },
    { value: 'ok', label: 'Хорошо', hint: 'так правильно, так можно' },
    { value: 'warn', label: 'Внимание', hint: 'легко ошибиться' },
    { value: 'danger', label: 'Нельзя', hint: 'запрет, потеря денег, отказ' },
    { value: 'tip', label: 'Совет', hint: 'как быстрее или удобнее' },
    { value: 'neutral', label: 'Справка', hint: 'сведения без окраски' },
    { value: 'dark', label: 'Пример', hint: 'разбор случая с числами' },
];

export const TONE_VALUES = BLOCK_TONES.map((t) => t.value);

/* Виды блоков, которые вставляет автор. label и hint видны в редакторе,
   template — то, что кладётся в текст.

   В шаблонах стоит осмысленный текст-рыба, а не пустые абзацы. Пустой блок
   выглядит поломкой вёрстки, и первое, что делает с ним человек, — удаляет,
   решив, что кнопка не сработала. Текст-рыбу же видно, что надо заменить. */
export const INSERT_BLOCKS = [
    {
        key: 'lead',
        label: 'Вводка',
        hint: 'первый абзац: о чём статья и кому нужна',
        template: '<div data-wiki-block="lead"><p>Коротко о том, что внутри и кому пригодится.</p></div><p></p>',
    },
    {
        key: 'note',
        label: 'Плашка',
        hint: 'условие, запрет, итог — то, что нельзя пропустить',
        template: '<div data-wiki-block="note" data-tone="warn"><h4>Заголовок плашки</h4>'
            + '<p>Одна мысль, которую читатель не должен пропустить.</p></div><p></p>',
    },
    {
        key: 'cards',
        label: 'Карточки',
        hint: 'равнозначные куски рядом: способы, условия, роли',
        template: '<div data-wiki-block="cards" data-cols="2">'
            + '<div data-wiki-block="card"><h4>Первый</h4><p>Пояснение.</p></div>'
            + '<div data-wiki-block="card"><h4>Второй</h4><p>Пояснение.</p></div>'
            + '</div><p></p>',
    },
    {
        key: 'gallery',
        label: 'Галерея',
        hint: 'несколько кадров одного действия: читатель листает их на месте',
        /* Текст-рыба, а не пустой блок: галерея наполняется картинками, а
           пустая полоса выглядит поломкой вёрстки — первое, что с ней сделает
           автор, это удалит, решив, что кнопка не сработала. Подсказку он
           заменит кадрами, перетащив их внутрь. */
        template: '<div data-wiki-block="gallery">'
            + '<p>Перетащите сюда 2–3 кадра одного действия.</p>'
            + '</div><p></p>',
    },
    {
        key: 'stats',
        label: 'Показатели',
        hint: 'крупные числа рядом: сроки, суммы, доли, пороги',
        template: '<div data-wiki-block="stats" data-cols="3">'
            + '<div data-wiki-block="stat"><h4>10 минут</h4><p>подпись</p></div>'
            + '<div data-wiki-block="stat"><h4>4,75</h4><p>подпись</p></div>'
            + '<div data-wiki-block="stat"><h4>0 ₸</h4><p>подпись</p></div>'
            + '</div><p></p>',
    },
];

/* Одна карточка — её же добавляет кнопка «+ карточка» у сетки. */
const CARD_TEMPLATE = '<div data-wiki-block="card"><h4>Заголовок</h4><p>Пояснение.</p></div>';
const STAT_TEMPLATE = '<div data-wiki-block="stat"><h4>0</h4><p>подпись</p></div>';

/* Какую ячейку добавляет кнопка «+» у сетки. Пары те же, что в GRIDS на
   сервере (wiki/ai/markup.py): разойдись они — кнопка положила бы в сетку
   чужую ячейку, а ремонт разметки при первой же правке через ИИ выкинул бы
   её наружу. Паритет сторожит tests/test_wiki_blocks.py. */
export const GRID_ITEMS = {
    cards: { item: 'card', template: CARD_TEMPLATE, label: 'карточка' },
    stats: { item: 'stat', template: STAT_TEMPLATE, label: 'показатель' },
};

/* Разрешённые значения атрибутов. Чужое значение НЕ сохраняется: тон
   «data-tone=purple» не нарисовал бы ничего (в CSS такого набора нет), но
   пережил бы санитайзер и остался в теле статьи навсегда — мусором, который
   потом никто не решится вычистить, не зная, чей он. */
export const BLOCK_KINDS = ['lead', 'note', 'cards', 'card', 'stats', 'stat',
    /* Галерея — единственный блок, который заводит не человек, а импортёр
       (wiki/yandex_pro.py): у источника несколько кадров одного действия
       лежат каруселью. В меню вставки её нет намеренно — пустая галерея
       без картинок выглядит поломкой вёрстки. Листание живёт на витрине
       (WikiArticle.jsx), в редакторе это обычная полоса картинок, из
       которой любую можно убрать. */
    'gallery'];
export const BLOCK_COLS = ['1', '2', '3'];

/* Значение из закрытого перечня — или запасное.
 *
 * Вынесено и названо отдельно, потому что это и есть правило: чужое значение
 * НЕ сохраняется. data-tone="фиолетовый" пережил бы санитайзер, не нарисовал
 * бы ничего и остался в теле статьи навсегда — мусором, чьё происхождение
 * потом уже не установить. Проверяется в tests/wiki_blocks.test.mjs. */
export const pickAllowed = (list, value, fallback = null) => (
    list.includes(String(value || '')) ? String(value) : fallback);

/* Ближайший блок нужного вида вверх по дереву.

   Ищем от курсора НАРУЖУ, а не от корня внутрь: карточка лежит в сетке, и
   команде «сменить тон» нужна карточка, а команде «три колонки» — сетка.
   Различает их только вид, поэтому он и есть условие поиска. */
const findBlock = (state, kinds) => {
    const { $from } = state.selection;
    const wanted = Array.isArray(kinds) ? kinds : [kinds];
    for (let depth = $from.depth; depth > 0; depth -= 1) {
        const node = $from.node(depth);
        if (node.type.name === 'wikiBlock' && (!kinds || wanted.includes(node.attrs.kind))) {
            return { node, depth, from: $from.before(depth), to: $from.after(depth) };
        }
    }
    return null;
};

export const WikiBlock = Node.create({
    name: 'wikiBlock',
    group: 'block',
    /* block+, а не paragraph+: в плашке бывает заголовок и список, в карточке —
       таблица, а в сетке лежат сами карточки (они тоже wikiBlock и потому тоже
       block). Одно выражение закрывает все четыре вида. */
    content: 'block+',
    /* defining: вставка поверх выделения внутри блока не должна съедать сам
       блок — то же решение, что у цитаты в TipTap. */
    defining: true,

    addAttributes() {
        return {
            kind: {
                default: 'note',
                parseHTML: (element) => pickAllowed(BLOCK_KINDS, element.getAttribute('data-wiki-block'), 'note'),
                renderHTML: (attrs) => ({ 'data-wiki-block': pickAllowed(BLOCK_KINDS, attrs.kind, 'note') }),
            },
            tone: {
                default: null,
                parseHTML: (element) => pickAllowed(TONE_VALUES, element.getAttribute('data-tone')),
                renderHTML: (attrs) => {
                    const tone = pickAllowed(TONE_VALUES, attrs.tone);
                    return tone ? { 'data-tone': tone } : {};
                },
            },
            cols: {
                default: null,
                parseHTML: (element) => pickAllowed(BLOCK_COLS, element.getAttribute('data-cols')),
                renderHTML: (attrs) => {
                    const cols = pickAllowed(BLOCK_COLS, attrs.cols);
                    return cols ? { 'data-cols': cols } : {};
                },
            },
            numbered: {
                default: false,
                parseHTML: (element) => element.getAttribute('data-numbered') === 'true',
                renderHTML: (attrs) => (attrs.numbered ? { 'data-numbered': 'true' } : {}),
            },
        };
    },

    /* Правило разбора одно, но приоритет поднят: без него <div data-wiki-block>
       достаётся общему правилу «неизвестный тег — развернуть», и блок теряется
       ровно в том сценарии, ради которого узел и написан. */
    parseHTML() {
        return [{ tag: 'div[data-wiki-block]', priority: 60 }];
    },

    renderHTML({ HTMLAttributes }) {
        return ['div', mergeAttributes(HTMLAttributes), 0];
    },

    addCommands() {
        return {
            /* Вставка блока по шаблону. Шаблон — строка HTML, а не описание
               узлов: ровно ту же строку понимает и санитайзер, и ИИ, и глаз
               человека, который читает этот файл. */
            insertWikiBlock: (kindKey) => ({ chain }) => {
                const kind = INSERT_BLOCKS.find((item) => item.key === kindKey);
                if (!kind) return false;
                return chain().focus().insertContent(kind.template).run();
            },

            /* Сменить свойство ближайшего блока нужного вида. */
            setWikiBlockAttrs: (kinds, attrs) => ({ state, tr, dispatch }) => {
                const found = findBlock(state, kinds);
                if (!found) return false;
                if (dispatch) {
                    tr.setNodeMarkup(found.from, undefined, { ...found.node.attrs, ...attrs });
                }
                return true;
            },

            /* Ещё одна ячейка — в конец той сетки, в которой стоит курсор.
               Какая именно ячейка, решает сама сетка (GRID_ITEMS): в сетке
               карточек это карточка, в сетке показателей — показатель. */
            addWikiCard: () => ({ state, chain }) => {
                const found = findBlock(state, Object.keys(GRID_ITEMS));
                if (!found) return false;
                const spec = GRID_ITEMS[found.node.attrs.kind];
                if (!spec) return false;
                // to указывает ЗА закрывающий тег сетки; минус единица ставит
                // вставку внутрь, последним ребёнком.
                return chain().insertContentAt(found.to - 1, spec.template).focus().run();
            },

            /* Разобрать блок: содержимое остаётся в статье, обёртка уходит.
               Отдельно от удаления намеренно — «убрать оформление» и «стереть
               текст» это разные намерения, а кнопка была бы одна. */
            unwrapWikiBlock: (kinds) => ({ state, tr, dispatch }) => {
                const found = findBlock(state, kinds);
                if (!found) return false;
                if (dispatch) tr.replaceWith(found.from, found.to, found.node.content);
                return true;
            },

            removeWikiBlock: (kinds) => ({ state, tr, dispatch }) => {
                const found = findBlock(state, kinds);
                if (!found) return false;
                if (dispatch) tr.delete(found.from, found.to);
                return true;
            },

            /* Выйти из блока вниз. Без этой команды блок в конце статьи —
               ловушка: писать дальше некуда, потому что Enter добавляет абзац
               ВНУТРИ блока, а мышью ниже блока щёлкать некуда. */
            paragraphAfterWikiBlock: () => ({ state, chain }) => {
                // Берём САМЫЙ ВНЕШНИЙ блок: из карточки выходим за всю сетку,
                // иначе новый абзац оказался бы третьей карточкой в ряду.
                const { $from } = state.selection;
                let outer = null;
                for (let depth = $from.depth; depth > 0; depth -= 1) {
                    if ($from.node(depth).type.name === 'wikiBlock') {
                        outer = $from.after(depth);
                    }
                }
                if (outer === null) return false;
                return chain().insertContentAt(outer, '<p></p>').focus(outer + 1).run();
            },
        };
    },

    addKeyboardShortcuts() {
        return {
            'Mod-Enter': () => this.editor.commands.paragraphAfterWikiBlock(),
        };
    },
});

/* Вид списка: «шаги», «чипы», «галочки».
 *
 * Глобальный атрибут, а не свои узлы, — см. шапку файла. Значение чужого вида
 * не сохраняется по той же причине, что и чужой тон: невидимый мусор в теле
 * статьи хуже, чем его отсутствие.
 */
export const LIST_VARIANTS = [
    {
        value: 'steps',
        label: 'Шаги',
        hint: 'действия по порядку: номера с пунктиром между ними',
        type: 'orderedList',
    },
    {
        value: 'chips',
        label: 'Чипы',
        hint: 'перечень коротких значений: города, тарифы, статусы',
        type: 'bulletList',
    },
    {
        value: 'checks',
        label: 'Галочки',
        hint: 'что входит, что уже сделано',
        type: 'bulletList',
    },
    {
        value: 'crosses',
        label: 'Крестики',
        hint: 'чего делать нельзя — пара к галочкам',
        type: 'bulletList',
    },
];

export const VARIANT_VALUES = LIST_VARIANTS.map((v) => v.value);

export const WikiListVariant = Extension.create({
    name: 'wikiListVariant',

    addGlobalAttributes() {
        return [{
            types: ['bulletList', 'orderedList'],
            attributes: {
                variant: {
                    default: null,
                    parseHTML: (element) => pickAllowed(VARIANT_VALUES, element.getAttribute('data-variant')),
                    renderHTML: (attrs) => {
                        const variant = pickAllowed(VARIANT_VALUES, attrs.variant);
                        return variant ? { 'data-variant': variant } : {};
                    },
                },
            },
        }];
    },

    addCommands() {
        return {
            /* Переключатель, а не установщик: повторное нажатие на «Чипы»
               возвращает обычный список. Иначе снять вид можно было бы только
               удалением списка целиком. */
            toggleListVariant: (variant) => ({ state, chain }) => {
                const spec = LIST_VARIANTS.find((item) => item.value === variant);
                if (!spec) return false;
                const active = state.selection.$from;
                let listDepth = null;
                for (let depth = active.depth; depth > 0; depth -= 1) {
                    if (active.node(depth).type.name === spec.type) listDepth = depth;
                }
                const current = listDepth === null ? null : active.node(listDepth).attrs.variant;
                const next = current === variant ? null : variant;
                // Списка ещё нет — сначала делаем его, потом красим.
                const start = listDepth === null
                    ? chain().focus()[spec.type === 'orderedList' ? 'toggleOrderedList' : 'toggleBulletList']()
                    : chain().focus();
                return start.updateAttributes(spec.type, { variant: next }).run();
            },
        };
    },
});

/* Единый список пунктов меню «Вставить блок».
 *
 * Собран из двух разных механизмов нарочно: автору всё равно, что вводка —
 * это узел, а шаги — атрибут на списке; ему нужен один список пунктов.
 * Разницу несёт поле action.
 *
 * Порядок — по частоте: вводка стоит первой, потому что нужна почти каждой
 * статье, а «крестики» последними, потому что нужны реже всех и всегда
 * рядом с галочками.
 *
 * ЗАБЫТЬ ЗДЕСЬ ПУНКТ — САМЫЙ ТИХИЙ СПОСОБ СЛОМАТЬ ФИЧУ: блок останется в
 * схеме, в санитайзере, в стилях и в наставлении для ИИ, статья с ним будет
 * открываться правильно — но поставить его руками автор не сможет, и понять
 * почему, не читая исходник, нельзя. Список сторожит tests/wiki_blocks.test.mjs.
 */
const MENU_ORDER = ['lead', 'note', 'steps', 'cards', 'stats', 'chips',
                    'checks', 'crosses', 'gallery'];

export const BLOCK_MENU = MENU_ORDER.map((key) => {
    const kind = INSERT_BLOCKS.find((item) => item.key === key);
    if (kind) return { ...kind, action: 'insert' };
    const variant = LIST_VARIANTS.find((item) => item.value === key);
    return { ...variant, key, action: 'variant' };
});

export default WikiBlock;
