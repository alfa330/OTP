import React, { useCallback, useEffect, useMemo, useRef } from 'react';
/* mergeAttributes и обвязку узла берём из @tiptap/react, а не из @tiptap/core:
   core стоит транзитивно и в package.json не заявлен, а сборка на Pages идёт
   через `npm ci` — она сверяет манифест с локом. Та же причина расписана в
   trainers/TrainerNode.jsx. */
import { NodeViewWrapper, ReactNodeViewRenderer, mergeAttributes } from '@tiptap/react';
import Image from '@tiptap/extension-image';
import {
    AlignCenter, AlignLeft, AlignRight, GripVertical, Images, Minus, Plus, RotateCcw,
    Trash2,
} from 'lucide-react';

import {
    ALIGNS, STEP, clampSize, normalizeAlign, sizeFromElement, styleFor,
} from './imageSize';
import { adjacentImageRun, insideGallery } from './WikiBlockNode';

/* Картинка в статье, у которой можно менять размер.
 *
 * ЗАЧЕМ СВОЙ УЗЕЛ. Штатный @tiptap/extension-image вставляет <img> и на этом
 * заканчивается: атрибуты width/height в схеме есть, но проставить их нечем —
 * ни панели, ни ручек, ни команды. После вставки картинка стоит своим исходным
 * размером, и сделать с ней нельзя ничего. Скриншот с «retina» приезжает в
 * статью во всю колонку, значок статуса — крохотным пятном, и автору
 * оставалось только удалить картинку и пересохранить её из внешнего редактора
 * нужного размера.
 *
 * ПОЧЕМУ НЕ ВСТРОЕННЫЙ resize. С версии 3.29 у расширения есть опция
 * resize: { enabled: true }. Она пишет ширину и высоту В ПИКСЕЛЯХ. Для вики это
 * не годится по той же причине, по которой в процентах сделана кнопка
 * тренажёра: статью читают и с телефона, где колонка втрое уже. Пиксельная
 * ширина там упирается в max-width, и заданные автором пропорции между
 * соседними картинками рассыпаются; заданная вместе с ней высота вдобавок
 * растягивает кадр. Проценты этим не болеют вовсе — и ими же, кстати,
 * снимается вторая беда: у раздела есть свой масштаб (zoom на .wiki-scope), от
 * которого любые пиксельные замеры «уезжают».
 *
 * ЧТО УЕЗЖАЕТ В БАЗУ. Ширина — в ПРОЦЕНТАХ от колонки: data-width и то же
 * значение инлайновым style. Выравнивание — data-align и поля margin-left /
 * margin-right. Дублирование не лишнее: data-* читает редактор при следующем
 * открытии, а style работает всюду, где HTML статьи показывают как есть, — в
 * истории версий, в сравнении редакций, в ответе ИИ-помощника.
 *
 * ТРИ БЕЛЫХ СПИСКА, КОТОРЫЕ ОБЯЗАНЫ СОВПАДАТЬ. Тело статьи чистится дважды:
 * на сервере (wiki/sanitize.py) и при чтении (SANITIZE_OPTIONS в
 * WikiArticle.jsx). Выпади data-width или data-align хоть из одного — размер
 * молча сбросится, и автор увидит это уже на опубликованной статье. Свойства
 * CSS сюда же: width, margin-left и margin-right в ALLOWED_CSS есть, а вот
 * display и float — нет и не будет, поэтому «блочность» картинки задана
 * правилом в wiki-theme.css, а обтекание текстом не делается вовсе.
 *
 * ПУСТАЯ ШИРИНА — ЭТО ЗНАЧЕНИЕ, А НЕ «ЕЩЁ НЕ ЗАПОЛНЕНО». Пока автор ничего не
 * трогал, атрибута нет вовсе, и картинка стоит своим размером (её ограничивает
 * max-width: 100%). Подставить всем 100 % значило бы растянуть каждый мелкий
 * значок на всю колонку и превратить его в мыло.
 *
 * ПОЧЕМУ КНОПКИ, А НЕ ТОЛЬКО РУЧКА. Ровно та же причина, что у кнопки
 * тренажёра: тянуть ручку на тачскрине внутри редактора получается через силу,
 * а с клавиатуры — никак.
 */

const ALIGN_ICON = { left: AlignLeft, center: AlignCenter, right: AlignRight };
const ALIGN_TITLE = {
    left: 'По левому краю',
    center: 'По центру',
    right: 'По правому краю',
};

/* ── Вид узла в редакторе ─────────────────────────────────────────────────── */

const WikiImageView = ({ node, updateAttributes, deleteNode, selected, editor, getPos }) => {
    const { src, alt, title, size, align, width } = node.attrs;
    /* Пиксельная ширина штатного расширения. Своей она у нас не появляется —
       её приносит картинка из импортированного документа, — но показать её
       редактор обязан: статья эту ширину применяет, и без неё автор видел бы
       не то, что увидит читатель. */
    const legacyWidth = Number(width) > 0 ? Number(width) : null;
    const wrapRef = useRef(null);
    const imgRef = useRef(null);
    const dragRef = useRef(null);
    const editable = editor?.isEditable !== false;

    // Узел удалили посреди тяги — подписки на window должны уйти вместе с ним.
    useEffect(() => () => dragRef.current?.(), []);

    /* Текущая ширина в процентах — даже когда её никто не задавал.
     *
     * Без этого «−/+» и ручке не с чего было бы начать: у нетронутой картинки
     * атрибута нет, и «уменьшить» пришлось бы отсчитывать от 100 %. Для
     * значка, который занимает восьмую часть колонки, первое нажатие тогда не
     * уменьшало бы его, а РАЗДУВАЛО до 95 %. Поэтому меряется то, что человек
     * видит на экране: доля показанной картинки от ширины колонки. Оба замера
     * экранные, поэтому масштаб раздела в отношении сокращается. */
    const currentSize = useCallback(() => {
        if (size) return clampSize(size);
        const total = wrapRef.current?.getBoundingClientRect().width || 0;
        const shown = imgRef.current?.getBoundingClientRect().width || 0;
        // Ещё не загрузилась (или битая) — мерить нечего. Раньше здесь стояло
        // «считаем, что 100 %», и первое же нажатие «−» превращало значок в
        // картинку на 95 % колонки.
        if (!total || !shown) return null;
        // Замер НЕ зажимается в диапазон, и это принципиально: clampSize
        // подтянул бы долю мелкого значка (4 %) к нижней границе в 10, и
        // «уменьшить» от неё дало бы снова 10 — то есть кнопка «−» удваивала
        // бы картинку. Зажимает только запись.
        return Math.round((shown / total) * 100);
    }, [size]);

    /* Вместе с процентами гасим width/height штатного расширения. Они приезжают
       ПИКСЕЛЯМИ из импортированного документа, и оставить их рядом с процентной
       шириной значило бы хранить в одной картинке два разных ответа на вопрос
       «какая она». Победил бы стиль, но разбираться в этом пришлось бы каждому,
       кто откроет HTML статьи. */
    const setSize = useCallback((value) => updateAttributes(
        { size: clampSize(value), width: null, height: null }), [updateAttributes]);

    /* Шаг кнопками. Пока картинка не загрузилась, «текущий размер» неизвестен,
       и кнопка молчит: подставить сюда догадку значит один раз промахнуться на
       весь экран. */
    const step = (delta) => {
        const current = currentSize();
        if (current === null) return;
        setSize(current + delta);
    };

    /* Тяга за край. Считаем от ширины КОЛОНКИ, а не от самой картинки: иначе
       один и тот же сдвиг мыши означал бы у мелкого значка и у широкого
       скриншота совершенно разный шаг в процентах. */
    const startResize = (event) => {
        event.preventDefault();
        event.stopPropagation();
        const total = wrapRef.current?.getBoundingClientRect().width || 640;
        const startX = event.clientX;
        const startSize = currentSize();
        if (startSize === null) return;

        const onMove = (moveEvent) => {
            const delta = ((moveEvent.clientX - startX) / total) * 100;
            setSize(startSize + delta);
        };
        /* Отпускание — это ТРИ разных события, и подписки надо снимать на
           каждое. pointerup не приходит, если жест отменила система (звонок,
           переключение окна, свайп «назад» на тачскрине): тогда слушатель
           остаётся на window навсегда и продолжает менять документ при
           обычном движении мыши, без нажатой кнопки. Третье — размонтирование
           узла: картинку могли удалить прямо посреди тяги. */
        const stop = () => {
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', stop);
            window.removeEventListener('pointercancel', stop);
            dragRef.current = null;
        };
        dragRef.current = stop;
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', stop);
        window.addEventListener('pointercancel', stop);
    };

    /* Выравнивание — переключатель, а не радиокнопка: повторным нажатием на
       уже выбранный край картинка возвращается в поток текста. */
    const toggleAlign = (value) => updateAttributes({ align: align === value ? null : value });

    /* Кнопки панели не должны уводить выделение с узла: без preventDefault на
       нажатии редактор теряет выделенную картинку, и updateAttributes применять
       становится не к чему. Тот же приём — у кнопок тулбара (ToolButton). */
    const hold = (event) => event.preventDefault();

    /* ГАЛЕРЕЯ — из панели самой картинки, а не из меню вставки.
       «Сделать эти кадры листающимися» — мысль про КАРТИНКИ, и рождается она
       там, где на них смотрят. Пункт меню вставки кладёт пустую галерею и
       годится, когда кадров ещё нет; здесь кадры уже стоят в тексте.

       Кнопка показывается, только если она что-то сделает: подряд идущих
       картинок хотя бы две (одному кадру карусель бессмысленна) либо кадр уже
       внутри галереи — тогда её можно разобрать. Кнопка, которая всегда
       отказывает, — это и есть мёртвая кнопка. */
    const galleryState = useMemo(() => {
        if (!editable || typeof getPos !== 'function' || !editor?.state) return null;
        let pos;
        try { pos = getPos(); } catch (error) { return null; }
        if (typeof pos !== 'number') return null;
        if (insideGallery(editor.state, pos)) return { inside: true };
        const run = adjacentImageRun(editor.state, pos);
        return run && run.count >= 2 ? { inside: false, count: run.count } : null;
    }, [editable, editor, getPos, node, selected]);

    const toggleGallery = () => {
        const pos = getPos?.();
        if (typeof pos !== 'number') return;
        if (galleryState?.inside) {
            /* Выделение СНАЧАЛА ставится на этот самый кадр, и только потом
               идёт разбор. unwrapWikiBlock ищет блок от курсора наружу, а
               панель показывается и по наведению, причём нажатие на её кнопках
               намеренно погашено (hold), чтобы выделение не переезжало, — то
               есть к моменту щелчка курсор мог стоять где угодно, и кнопка
               молча не срабатывала. focus() последним: поставь его раньше, и
               он вернёт прежнее выделение. */
            editor.chain().setNodeSelection(pos).unwrapWikiBlock(['gallery']).focus().run();
            return;
        }
        editor.chain().focus().wrapImagesInGallery(pos).run();
    };

    return (
        <NodeViewWrapper
            ref={wrapRef}
            className={`wiki-image-node${selected ? ' is-selected' : ''}`}
            data-align={align || 'left'}
        >
            <span className="wiki-image-node__inner"
                style={size ? { width: `${clampSize(size)}%` }
                    : (legacyWidth ? { width: `${legacyWidth}px` } : undefined)}>
                {editable && (
                    /* Панель у выделенной (или наведённой) картинки. Постоянная
                       панель у каждой превратила бы статью в череду пультов. */
                    <span className="wiki-image-node__tools" contentEditable={false}>
                        <span className="wiki-image-node__grip" data-drag-handle
                            title="Перетащить картинку по тексту">
                            <GripVertical size={13} />
                        </span>
                        {/* РАЗМЕР И ВЫРАВНИВАНИЕ — ТОЛЬКО ВНЕ ГАЛЕРЕИ.
                            Внутри неё ширину и положение кадра задаёт сама
                            галерея: кадры одного действия обязаны быть одного
                            размера, иначе листание выглядит как дёрганье, и
                            правило в wiki-blocks.css стоит с !important. То
                            есть кнопки продолжали бы показывать проценты и
                            писать data-width в тело статьи, а на экране не
                            менялось бы ничего — ни у автора, ни у читателя.
                            Хуже того, оставленный процент всплывал бы позже,
                            когда кадр вынут из галереи обратно: команда сборки
                            эти атрибуты как раз намеренно снимает. */}
                        {!galleryState?.inside && (
                            <>
                                {ALIGNS.map((value) => {
                                    const Icon = ALIGN_ICON[value];
                                    return (
                                        <button key={value} type="button"
                                            className={align === value ? 'is-on' : ''}
                                            title={ALIGN_TITLE[value]}
                                            onMouseDown={hold}
                                            onClick={() => toggleAlign(value)}>
                                            <Icon size={13} />
                                        </button>
                                    );
                                })}
                                <button type="button" title="Уменьшить" onMouseDown={hold}
                                    onClick={() => step(-STEP)}>
                                    <Minus size={13} />
                                </button>
                                <span className="wiki-image-node__size">
                                    {size ? `${clampSize(size)}%` : 'авто'}
                                </span>
                                <button type="button" title="Увеличить" onMouseDown={hold}
                                    onClick={() => step(STEP)}>
                                    <Plus size={13} />
                                </button>
                                <button type="button" title="Вернуть исходный размер" onMouseDown={hold}
                                    onClick={() => updateAttributes({ size: null })}>
                                    <RotateCcw size={13} />
                                </button>
                            </>
                        )}
                        {galleryState && (
                            <button type="button"
                                className={galleryState.inside ? 'is-on' : ''}
                                title={galleryState.inside
                                    ? 'Разобрать галерею: кадры встанут столбиком'
                                    : `Сделать листающейся галереей (${galleryState.count} кадра)`}
                                onMouseDown={hold}
                                onClick={toggleGallery}>
                                <Images size={13} />
                            </button>
                        )}
                        <button type="button" title="Удалить картинку" onMouseDown={hold}
                            onClick={() => deleteNode()}>
                            <Trash2 size={13} />
                        </button>
                    </span>
                )}

                {/* draggable={false} у самой картинки: перетаскивание узла даёт
                    ручка слева, а встроенное перетаскивание изображения браузером
                    перебивало бы его и роняло картинку мимо документа. */}
                <img ref={imgRef} src={src || ''} alt={alt || ''} title={title || undefined}
                    draggable={false} />

                {/* Ручка тяги — тоже только вне галереи: внутри неё она тянула
                    бы процент, который на экране не действует. */}
                {editable && !galleryState?.inside && (
                    <span className="wiki-image-node__resize" onPointerDown={startResize}
                        role="presentation" title="Потяните, чтобы изменить размер" />
                )}
            </span>
        </NodeViewWrapper>
    );
};

/* ── Сам узел схемы ──────────────────────────────────────────────────────── */

export const WikiImage = Image.extend({
    addAttributes() {
        return {
            ...this.parent?.(),
            /* Своё поле, а не переопределённое width расширения: у того ширина
               уезжает HTML-атрибутом в пикселях, и им же приходит картинка из
               импортированного документа. Два разных смысла в одном атрибуте
               рано или поздно подрались бы. */
            size: {
                default: null,
                parseHTML: sizeFromElement,
                renderHTML: (attrs) => (clampSize(attrs.size)
                    ? { 'data-width': String(clampSize(attrs.size)) } : {}),
            },
            align: {
                default: null,
                parseHTML: (element) => normalizeAlign(element.getAttribute('data-align')),
                renderHTML: (attrs) => (normalizeAlign(attrs.align)
                    ? { 'data-align': normalizeAlign(attrs.align) } : {}),
            },
        };
    },

    renderHTML({ node, HTMLAttributes }) {
        const style = styleFor(node.attrs);
        return ['img', mergeAttributes(this.options.HTMLAttributes, HTMLAttributes,
            style ? { style } : {})];
    },

    addNodeView() {
        return ReactNodeViewRenderer(WikiImageView);
    },
});

export default WikiImage;
