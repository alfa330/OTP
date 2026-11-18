import React, { useSyncExternalStore } from 'react';
/* Node и обвязку узла берём из @tiptap/react, а не из @tiptap/core — та же
   причина, что в trainers/TrainerNode.jsx: core стоит транзитивно и в
   package.json не заявлен, а сборка на Pages идёт через `npm ci`. */
import { Node, NodeViewWrapper, ReactNodeViewRenderer } from '@tiptap/react';
/* NodeSelection — из @tiptap/pm, как в WikiEditor.jsx: prosemirror-state стоит
   транзитивно и в package.json не заявлен. */
import { NodeSelection } from '@tiptap/pm/state';
import { AlertCircle, Clapperboard, GripVertical, Loader2, RotateCcw, Trash2, X } from 'lucide-react';

import { uploadStore } from '../production/productionUpload';
import { formatBytes, formatEta } from '../production/productionMeta';
import { absoluteFileUrl } from './fileUrls';
import { videoAddress, videoBox } from './videoRules';
import { isFinishing } from './videoUpload';

/* Ролик в тексте статьи (владелец 18.11.2026: «загружать ролики как статью»).
 *
 * ЧТО УЕЗЖАЕТ В БАЗУ — один тег:
 *   <video src="/api/wiki/file/<id>" poster="/api/wiki/file/<id обложки>"
 *          controls preload="metadata" playsinline
 *          width="1920" height="1080" data-duration="83.4"></video>
 * Адреса постоянные, как у картинки: подпись хранилища подставляет витрина
 * (file_urls.py, fileUrls.js), а к статье файл привязывается при сохранении по
 * адресу в тексте (wiki/edit.py). Размер кадра нужен витрине, чтобы место под
 * ролик стояло сразу и страница не прыгала, когда догрузится обложка.
 *
 * ТРИ БЕЛЫХ СПИСКА, КОТОРЫЕ ОБЯЗАНЫ СОВПАДАТЬ, — как у картинки: серверный
 * санитайзер (wiki/sanitize.py), DOMPurify витрины (WikiArticle.jsx) и этот
 * узел. Выпади атрибут из любого — ролик сохранится, но без обложки или без
 * размера, молча. Паритет сторожит tests/test_wiki_videos.py.
 *
 * ПОКА РОЛИК ЕДЕТ, адреса у узла нет: есть номер строки очереди загрузки
 * (uploadKey). Узел рисует по нему полосу загрузки, а адрес получает, когда
 * сервер принял файл (WikiEditor.jsx: finishVideo). Сохранить статью с таким
 * узлом нельзя — редактор остановит сохранение и скажет почему: ролик без
 * адреса санитайзер выбросил бы, и статья молча ушла бы без ролика.
 */

const STATE_TEXT = {
    queued: 'В очереди',
    starting: 'Начинаем…',
    verifying: 'Проверяем ролик…',
    waiting: 'Нет связи — продолжим, как только появится',
    done: 'Готовим ролик…',
};

const numberOrNull = (value) => {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
};

/* Полоса загрузки — тот же вид, что у строки загрузки «Продакшна»
   (ProductionTaskSheet.jsx: UploadRow), только в рамке будущего ролика. */
const UploadCard = ({ row, name, error, onCancel, onRetry, onRemove }) => {
    const failed = Boolean(error) || row?.state === 'failed';
    const size = Number(row?.size || 0);
    const percent = size > 0 ? Math.min(100, Math.floor((Number(row?.loaded || 0) / size) * 100)) : 0;
    const status = failed ? (error || row?.error || 'Ролик не загрузился')
        : row?.state === 'uploading'
            ? [`${percent}%`, formatEta(row.eta)].filter(Boolean).join(' · ')
            : STATE_TEXT[row?.state] || STATE_TEXT.done;
    const full = !row || ['verifying', 'done'].includes(row.state);
    return (
        <div className="wiki-video-upload" contentEditable={false}>
            <div className="flex w-full max-w-[420px] items-center gap-3 rounded-xl bg-white px-3 py-2.5 ring-1 ring-slate-200/70">
                <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${failed ? 'bg-rose-50 text-rose-500' : 'bg-blue-50 text-blue-600'}`}>
                    {failed ? <AlertCircle size={17} />
                        : full ? <Loader2 size={16} className="animate-spin" /> : <Clapperboard size={16} />}
                </span>
                <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-2">
                        <span className="truncate text-[13px] font-medium text-slate-800" title={name}>{name || 'Ролик'}</span>
                        {size > 0 && (
                            <span className="shrink-0 text-[11.5px] tabular-nums text-slate-400">{formatBytes(size)}</span>
                        )}
                    </span>
                    {!failed && (
                        <span className="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-slate-100">
                            <span
                                className={`block h-full rounded-full transition-[width] duration-300 ${row?.state === 'waiting' ? 'bg-amber-400' : 'bg-blue-500'}`}
                                style={{ width: `${full ? 100 : percent}%` }}
                            />
                        </span>
                    )}
                    <span className={`mt-1 block text-[11.5px] tabular-nums ${failed ? 'text-rose-600' : 'text-slate-500'}`}>
                        {status}
                    </span>
                </span>
                {failed ? (
                    <span className="flex shrink-0 items-center gap-1">
                        {onRetry && (
                            <button type="button" className="wiki-video-upload__btn" onClick={onRetry} title="Повторить">
                                <RotateCcw size={15} />
                            </button>
                        )}
                        <button type="button" className="wiki-video-upload__btn" onClick={onRemove} title="Убрать">
                            <X size={15} />
                        </button>
                    </span>
                ) : row && row.state !== 'done' && (
                    <button type="button" className="wiki-video-upload__btn" onClick={onCancel} title="Отменить загрузку">
                        <X size={15} />
                    </button>
                )}
            </div>
        </div>
    );
};

const WikiVideoView = ({ node, deleteNode, selected, editor, extension }) => {
    const { src, poster, width, height, uploadKey, uploadName, uploadError } = node.attrs;
    const rows = useSyncExternalStore(uploadStore.subscribe, uploadStore.snapshot, uploadStore.snapshot);
    const row = uploadKey ? rows.find((item) => item.key === uploadKey) || null : null;
    const editable = editor?.isEditable;
    /* Адрес, восстановленный по номеру файла, — относительный; показать его
       можно только раскрыв до домена API (см. fileUrls.js). */
    const base = extension?.options?.base || '';

    if (!src) {
        /* Ждать нечего: строки очереди нет и адрес у сервера не спрашивают —
           загрузку отменили (а «Повторить» вернул узел), ролик пришёл чужим
           адресом или потерялся. Тогда — ошибка и «Убрать», а не вечное
           «Готовим ролик…». */
        const lost = !uploadError && (!uploadKey || (!row && !isFinishing(uploadKey)));
        return (
            <NodeViewWrapper className={`wiki-video-node${selected ? ' is-selected' : ''}`}>
                <UploadCard
                    row={row}
                    name={uploadName}
                    error={uploadError || (lost ? 'Ролик не найден — уберите его и загрузите заново' : '')}
                    onCancel={() => { uploadStore.cancel(uploadKey); deleteNode(); }}
                    onRetry={row?.state === 'failed' && row.retryable !== false
                        ? () => uploadStore.retry(uploadKey) : null}
                    onRemove={() => { if (row) uploadStore.dismiss(uploadKey); deleteNode(); }}
                />
            </NodeViewWrapper>
        );
    }

    return (
        <NodeViewWrapper className={`wiki-video-node${selected ? ' is-selected' : ''}`}>
            {/* Рамка по размеру кадра: панель встаёт в угол самого ролика, а не
                в угол строки — у вертикального ролика строка втрое шире кадра. */}
            <div className="wiki-video-node__frame" style={videoBox(width, height)}>
                {editable && (
                    /* Панель — на наведение и у выделенного узла: постоянная
                       панель у каждого ролика превратила бы статью в череду
                       пультов. */
                    <div className="wiki-video-node__tools" contentEditable={false}>
                        <span className="wiki-video-node__grip" data-drag-handle title="Перетащить ролик по тексту">
                            <GripVertical size={13} />
                        </span>
                        <button type="button" title="Удалить ролик" onClick={() => deleteNode()}>
                            <Trash2 size={13} />
                        </button>
                    </div>
                )}
                {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                <video
                    className="wiki-video"
                    src={absoluteFileUrl(src, base) || src}
                    poster={poster ? absoluteFileUrl(poster, base) || poster : undefined}
                    controls
                    preload="metadata"
                    playsInline
                />
            </div>
        </NodeViewWrapper>
    );
};

/* ── Сам узел схемы ──────────────────────────────────────────────────────── */

export const WikiVideo = Node.create({
    name: 'wikiVideo',
    group: 'block',
    atom: true,
    draggable: true,
    selectable: true,

    /* base — адрес раздела API (`https://…/api/wiki`): по нему узел показывает
       ролик, адрес которого восстановлен по номеру файла (videoAddress). */
    addOptions() {
        return { base: '' };
    },

    addAttributes() {
        return {
            /* Адрес — только файла вики: свой как есть, со страницы читателя —
               по data-file-id, чужой — null (узел станет «не найден», и
               сохранение скажет об этом, а не выбросит ролик молча). */
            src: {
                default: null,
                parseHTML: (element) => videoAddress(element.getAttribute('src'),
                    element.getAttribute('data-file-id')),
                renderHTML: (attrs) => (attrs.src ? { src: attrs.src } : {}),
            },
            poster: {
                default: null,
                parseHTML: (element) => videoAddress(element.getAttribute('poster'),
                    element.getAttribute('data-poster-id')),
                renderHTML: (attrs) => (attrs.poster ? { poster: attrs.poster } : {}),
            },
            width: {
                default: null,
                parseHTML: (element) => numberOrNull(element.getAttribute('width')),
                renderHTML: (attrs) => (numberOrNull(attrs.width) ? { width: String(Math.round(attrs.width)) } : {}),
            },
            height: {
                default: null,
                parseHTML: (element) => numberOrNull(element.getAttribute('height')),
                renderHTML: (attrs) => (numberOrNull(attrs.height) ? { height: String(Math.round(attrs.height)) } : {}),
            },
            duration: {
                default: null,
                parseHTML: (element) => numberOrNull(element.getAttribute('data-duration')),
                renderHTML: (attrs) => (numberOrNull(attrs.duration)
                    ? { 'data-duration': String(Math.round(Number(attrs.duration) * 1000) / 1000) } : {}),
            },
            /* Служебное — только пока ролик едет. В разметку не попадает:
               сохранить статью с недогруженным роликом редактор не даст. */
            uploadKey: { default: null, rendered: false },
            uploadName: { default: '', rendered: false },
            uploadError: { default: '', rendered: false },
        };
    },

    parseHTML() {
        return [{ tag: 'video[src]' }];
    },

    renderHTML({ HTMLAttributes }) {
        return ['video', {
            ...HTMLAttributes,
            controls: '',
            preload: 'metadata',
            playsinline: '',
        }];
    },

    addNodeView() {
        return ReactNodeViewRenderer(WikiVideoView, {
            /* Щелчок по пульту плеера — плееру. Без этого ProseMirror забирал
               бы нажатие себе (узел выделяемый), и ролик в редакторе нельзя
               было бы ни запустить, ни перемотать. Перетаскивание и вставка
               по-прежнему идут редактору. */
            stopEvent: ({ event }) => event.target?.tagName === 'VIDEO'
                && !String(event.type || '').startsWith('drag') && event.type !== 'drop',
        });
    },

    addCommands() {
        return {
            /* Вставка с абзацем следом — та же причина, что у кнопки тренажёра:
               узел atom текста не принимает, и набранное после вставки ушло бы
               в никуда. at — позиция, куда бросили файл. */
            insertWikiVideo: (attrs, at) => ({ chain, state }) => {
                const content = [{ type: this.name, attrs }, { type: 'paragraph' }];
                // Позиция (куда бросили) или диапазон (пустая статья целиком).
                if (typeof at === 'number' || (at && Number.isFinite(at.from))) {
                    return chain().insertContentAt(at, content).run();
                }
                /* ВЫДЕЛЕН УЗЕЛ — ВСТАВЛЯЕМ ЗА НИМ, А НЕ ВМЕСТО НЕГО: то же правило,
                   что у картинки (WikiEditor.jsx: uploadImage). Обычная вставка
                   заменила бы выделенную картинку, кадр галереи или другой
                   ролик (поймано разбором 18.11.2026). */
                if (state.selection instanceof NodeSelection) {
                    return chain().insertContentAt(state.selection.to, content).run();
                }
                return chain().focus().insertContent(content).run();
            },
        };
    },
});

/* Узлы роликов документа: [{ pos, node }]. */
export const videoNodes = (doc) => {
    const found = [];
    doc?.descendants((node, pos) => {
        if (node.type.name === 'wikiVideo') found.push({ pos, node });
        return true;
    });
    return found;
};

export default WikiVideo;
