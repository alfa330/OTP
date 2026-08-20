import React, { useCallback, useRef } from 'react';
import { Node, mergeAttributes } from '@tiptap/core';
import { NodeViewWrapper, ReactNodeViewRenderer } from '@tiptap/react';
import {
    AlignCenter, AlignLeft, AlignRight, GripVertical, Minus, Pencil, Play, Plus, Trash2,
} from 'lucide-react';

import { findTrainer, defaultButtonLabel } from './registry';

/* Кнопка тренажёра внутри текста статьи.
 *
 * ЧТО ЭТО. Узел схемы редактора (atom): в тексте он ведёт себя как одна
 * неделимая картинка — его можно перетащить, выделить, удалить, но нельзя
 * «войти внутрь» и испортить разметку. В базу уезжает пустой div с четырьмя
 * data-атрибутами, а не готовая кнопка с обработчиком: обработчик из статьи не
 * пережил бы санитизацию, да и не должен — интерактивность навешивает читалка.
 *
 * ПОЧЕМУ НЕ ССЫЛКА. Ссылка обязана куда-то вести, а тренажёр открывается ЗДЕСЬ
 * же, поверх статьи; ссылка «в никуда» ломает и Ctrl+клик, и копирование адреса.
 *
 * ПЕРЕМЕЩЕНИЕ И РАЗМЕР. Перетаскивание даёт draggable-узел (ручка слева),
 * ширину меняет ручка справа и кнопки «−/+» рядом — мышью и с клавиатуры
 * соответственно. Ширина в ПРОЦЕНТАХ, а не в пикселях: статью читают и на
 * телефоне, и кнопка в 620 пикселей уехала бы за край.
 */

const MIN_WIDTH = 25;
const MAX_WIDTH = 100;
const STEP = 5;

const clampWidth = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return 60;
    return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(number)));
};

const ALIGNS = ['left', 'center', 'right'];

const normalizeAlign = (value) => (ALIGNS.includes(value) ? value : 'left');

/* Отступы вместо text-align: сама кнопка — блок с заданной шириной, и
   выравнивать её надо полями, а не выравниванием текста внутри. Оба свойства
   есть в белом списке серверного санитайзера (wiki/sanitize.py: ALLOWED_CSS) —
   расходиться этим двум местам нельзя, иначе выравнивание молча пропадёт при
   сохранении. */
const styleFor = (attrs) => {
    const width = clampWidth(attrs.width);
    const align = normalizeAlign(attrs.align);
    const sides = {
        left: 'margin-right: auto',
        center: 'margin-left: auto; margin-right: auto',
        right: 'margin-left: auto',
    }[align];
    return `width: ${width}%; ${sides}`;
};

/* ── Вид узла в редакторе ─────────────────────────────────────────────────── */

const TrainerNodeView = ({ node, updateAttributes, deleteNode, selected, editor }) => {
    const { trainer: key, label, width, align } = node.attrs;
    const scenario = findTrainer(key);
    const wrapRef = useRef(null);
    const editable = editor?.isEditable !== false;

    const setWidth = useCallback((value) => updateAttributes({ width: clampWidth(value) }),
        [updateAttributes]);

    /* Тяга за правый край. Считаем от ширины КОЛОНКИ редактора, а не от кнопки:
       иначе шаг «на пиксель» у узкой кнопки означал бы совсем другой процент,
       чем у широкой, и тянуть её было бы неудобно у краёв диапазона. */
    const startResize = (event) => {
        event.preventDefault();
        event.stopPropagation();
        const host = wrapRef.current?.parentElement;
        const total = host?.getBoundingClientRect().width || 640;
        const startX = event.clientX;
        const startWidth = clampWidth(width);

        const onMove = (moveEvent) => {
            const delta = ((moveEvent.clientX - startX) / total) * 100;
            setWidth(startWidth + delta);
        };
        const onUp = () => {
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
        };
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
    };

    const rename = () => {
        const next = window.prompt('Подпись кнопки',
            label || (scenario ? defaultButtonLabel(scenario) : ''));
        if (next === null) return;
        updateAttributes({ label: next.trim() || (scenario ? defaultButtonLabel(scenario) : 'Тренажёр') });
    };

    return (
        <NodeViewWrapper
            ref={wrapRef}
            className={`wiki-trainer-node${selected ? ' is-selected' : ''}`}
            data-align={normalizeAlign(align)}
        >
            <div className="wiki-trainer-node__inner" style={{ width: `${clampWidth(width)}%` }}>
                {editable && (
                    /* Панель появляется у выделенного узла. Постоянная панель у
                       каждой кнопки превратила бы текст статьи в череду пультов. */
                    <div className="wiki-trainer-node__tools" contentEditable={false}>
                        <span className="wiki-trainer-node__grip" data-drag-handle
                            title="Перетащить кнопку по тексту">
                            <GripVertical size={13} />
                        </span>
                        {ALIGNS.map((value) => {
                            const Icon = { left: AlignLeft, center: AlignCenter, right: AlignRight }[value];
                            return (
                                <button
                                    key={value}
                                    type="button"
                                    className={normalizeAlign(align) === value ? 'is-on' : ''}
                                    title={`По ${{ left: 'левому краю', center: 'центру', right: 'правому краю' }[value]}`}
                                    onClick={() => updateAttributes({ align: value })}
                                >
                                    <Icon size={13} />
                                </button>
                            );
                        })}
                        <button type="button" title="Уменьшить" onClick={() => setWidth(clampWidth(width) - STEP)}>
                            <Minus size={13} />
                        </button>
                        <span className="wiki-trainer-node__size">{clampWidth(width)}%</span>
                        <button type="button" title="Увеличить" onClick={() => setWidth(clampWidth(width) + STEP)}>
                            <Plus size={13} />
                        </button>
                        <button type="button" title="Изменить подпись" onClick={rename}>
                            <Pencil size={13} />
                        </button>
                        <button type="button" title="Удалить кнопку" onClick={() => deleteNode()}>
                            <Trash2 size={13} />
                        </button>
                    </div>
                )}

                <div className="wiki-trainer-embed" data-preview="1">
                    <span className="wiki-trainer-embed__icon" aria-hidden="true"><Play size={15} /></span>
                    <span className="wiki-trainer-embed__label">
                        {label || (scenario ? defaultButtonLabel(scenario) : 'Тренажёр')}
                    </span>
                </div>

                {/* Тренажёр мог уехать из кода, а кнопка в статье осталась.
                    Молчать нельзя: автор должен увидеть это в редакторе, а не
                    читатель — на опубликованной статье. */}
                {!scenario && (
                    <p className="wiki-trainer-node__warn">
                        Тренажёр «{key}» не найден. Удалите кнопку или вставьте другой тренажёр.
                    </p>
                )}

                {editable && (
                    <span
                        className="wiki-trainer-node__resize"
                        onPointerDown={startResize}
                        role="presentation"
                        title="Потяните, чтобы изменить ширину"
                    />
                )}
            </div>
        </NodeViewWrapper>
    );
};

/* ── Сам узел схемы ──────────────────────────────────────────────────────── */

export const WikiTrainerNode = Node.create({
    name: 'wikiTrainer',
    group: 'block',
    atom: true,
    draggable: true,
    selectable: true,

    addAttributes() {
        return {
            trainer: {
                default: null,
                parseHTML: (element) => element.getAttribute('data-wiki-trainer'),
                renderHTML: (attrs) => (attrs.trainer ? { 'data-wiki-trainer': attrs.trainer } : {}),
            },
            label: {
                default: '',
                parseHTML: (element) => element.getAttribute('data-label') || '',
                renderHTML: (attrs) => (attrs.label ? { 'data-label': attrs.label } : {}),
            },
            width: {
                default: 60,
                parseHTML: (element) => clampWidth(element.getAttribute('data-width')),
                renderHTML: (attrs) => ({ 'data-width': String(clampWidth(attrs.width)) }),
            },
            align: {
                default: 'left',
                parseHTML: (element) => normalizeAlign(element.getAttribute('data-align')),
                renderHTML: (attrs) => ({ 'data-align': normalizeAlign(attrs.align) }),
            },
        };
    },

    parseHTML() {
        return [{ tag: 'div[data-wiki-trainer]' }];
    },

    /* Разметка, которая уезжает в базу. Подпись лежит и в data-label, и внутри
       тега: атрибут читает читалка, текст видит тот, у кого JavaScript не
       выполнился, — и он же попадает в поиск по тексту статьи. */
    renderHTML({ node, HTMLAttributes }) {
        const scenario = findTrainer(node.attrs.trainer);
        const label = node.attrs.label || (scenario ? defaultButtonLabel(scenario) : 'Тренажёр');
        return ['div', mergeAttributes(HTMLAttributes, {
            class: 'wiki-trainer-embed',
            style: styleFor(node.attrs),
        }), ['span', { class: 'wiki-trainer-embed__label' }, label]];
    },

    addNodeView() {
        return ReactNodeViewRenderer(TrainerNodeView);
    },

    addCommands() {
        return {
            /* Вставка идёт ОДНОЙ командой: редактору важно, чтобы после вставки
               курсор оказался ПОСЛЕ кнопки, иначе следующий набранный текст
               уходит в никуда (узел atom его не принимает). */
            insertWikiTrainer: (attrs) => ({ chain }) => chain()
                .focus()
                .insertContent([
                    { type: this.name, attrs },
                    { type: 'paragraph' },
                ])
                .run(),
        };
    },
});

export default WikiTrainerNode;
