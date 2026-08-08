import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import Image from '@tiptap/extension-image';
import Underline from '@tiptap/extension-underline';
import TextAlign from '@tiptap/extension-text-align';
import Highlight from '@tiptap/extension-highlight';
// В TipTap 3 у этих пакетов нет default-экспорта, а таблицы приезжают одним
// набором — отдельные extension-table-row/cell/header больше не нужны.
import { Color, TextStyle } from '@tiptap/extension-text-style';
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table';
import {
    AlignCenter, AlignLeft, AlignRight, Bold, Code, Heading1, Heading2, Heading3,
    Highlighter, Italic, Link2, List, ListOrdered, Loader2, Quote, Redo2,
    Save, Strikethrough, Table as TableIcon, Underline as UnderlineIcon, Undo2,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary, IosBadge,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';

/* Редактор статьи на TipTap.
 *
 * Загружается лениво вместе со всем разделом: 18 пакетов @tiptap плюс
 * ProseMirror весят ~128 КБ gzip, и платить за них должны только те, кто
 * реально открыл редактор, а не каждый вошедший в портал.
 *
 * react-quill из LMS сознательно не переиспользован: раскрывающиеся блоки,
 * цветные выделения и таблицы — узлы схемы, и на Quill их пришлось бы
 * воспроизводить кастомными blot'ами, потеряв при этом ровно то, на чём стоит
 * существующий контент (35 блоков и 251 выделение по дампу прода).
 *
 * Содержимое, которое уходит на сервер, там же и санитизируется (wiki/sanitize.py).
 * Клиентская чистка — второй рубеж, а не единственный: она защищает того, кто
 * отправляет, а не того, кто потом читает.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const ARTICLE_TYPES = [
    { value: 'general', label: 'Обычная статья' },
    { value: 'regulation', label: 'Регламент' },
    { value: 'instruction', label: 'Инструкция' },
    { value: 'job_description', label: 'Должностная инструкция' },
    { value: 'tool_description', label: 'Описание инструмента' },
];

const HIGHLIGHT_COLORS = ['#fef3c7', '#dcfce7', '#dbeafe', '#fce7f3', '#e0e7ff'];

const ToolButton = ({ active, disabled, title, onClick, children }) => (
    <button
        type="button"
        title={title}
        aria-label={title}
        aria-pressed={!!active}
        disabled={disabled}
        onMouseDown={(e) => e.preventDefault()}   /* не терять выделение в редакторе */
        onClick={onClick}
        className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg transition ${
            active ? 'bg-indigo-50 text-indigo-600' : 'text-slate-500 hover:bg-slate-100'
        } disabled:opacity-40`}
    >
        {children}
    </button>
);

const Divider = () => <span className="mx-0.5 h-5 w-px shrink-0 bg-slate-200" />;

export default function WikiEditor({
    base, headers, showToast, article, sections, onClose, onSaved,
}) {
    const isNew = !article?.id;
    const [title, setTitle] = useState(article?.title || '');
    const [summary, setSummary] = useState(article?.summary || '');
    const [articleType, setArticleType] = useState(article?.article_type || 'general');
    const [sectionIds, setSectionIds] = useState(article?.section_ids || []);
    const [saving, setSaving] = useState(false);
    const [dirty, setDirty] = useState(false);

    const editor = useEditor({
        extensions: [
            StarterKit.configure({ heading: { levels: [1, 2, 3, 4] } }),
            Underline,
            Link.configure({ openOnClick: false, autolink: true }),
            Image.configure({ inline: false, allowBase64: true }),
            TextAlign.configure({ types: ['heading', 'paragraph'] }),
            TextStyle,
            Color,
            Highlight.configure({ multicolor: true }),
            Table.configure({ resizable: true }),
            TableRow,
            TableHeader,
            TableCell,
        ],
        content: article?.content || '',
        onUpdate: () => setDirty(true),
        editorProps: {
            attributes: {
                class: 'wiki-prose min-h-[320px] focus:outline-none',
            },
        },
    }, [article?.id]);

    // Предупреждаем о несохранённом при уходе со страницы. Внутри портала
    // навигация без перезагрузки, поэтому это только про закрытие вкладки.
    useEffect(() => {
        if (!dirty) return undefined;
        const handler = (event) => { event.preventDefault(); event.returnValue = ''; };
        window.addEventListener('beforeunload', handler);
        return () => window.removeEventListener('beforeunload', handler);
    }, [dirty]);

    const sectionOptions = useMemo(
        () => (sections || []).map((s) => ({ value: String(s.id), label: s.name })),
        [sections],
    );

    const save = useCallback((status) => {
        if (!editor) return;
        if (!title.trim()) {
            showToast?.('Укажите название статьи', 'error');
            return;
        }
        const payload = {
            title: title.trim(),
            summary: summary.trim() || null,
            content: editor.getHTML(),
            article_type: articleType,
            section_ids: sectionIds.map(Number).filter(Boolean),
        };
        if (status) payload.status = status;

        setSaving(true);
        const request = isNew
            ? axios.post(`${base}/articles`, payload, { headers })
            : axios.patch(`${base}/articles/${article.id}`, payload, { headers });

        request
            .then((r) => {
                setDirty(false);
                showToast?.(status === 'published' ? 'Статья опубликована' : 'Сохранено', 'success');
                onSaved?.(r.data?.slug || article?.slug);
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setSaving(false));
    }, [editor, title, summary, articleType, sectionIds, isNew, base, headers,
        article, showToast, onSaved]);

    const setLink = () => {
        const previous = editor.getAttributes('link').href || '';
        const url = window.prompt('Адрес ссылки', previous);
        if (url === null) return;
        if (!url.trim()) {
            editor.chain().focus().unsetLink().run();
            return;
        }
        editor.chain().focus().extendMarkRange('link').setLink({ href: url.trim() }).run();
    };

    if (!editor) {
        return (
            <div className={`${iosCard} flex items-center justify-center gap-2 py-16 text-slate-400`}>
                <Loader2 size={18} className="animate-spin" />
                <span className="text-[13px]">Готовим редактор…</span>
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>
                        Закрыть
                    </button>
                    {dirty && <IosBadge tone="amber">Есть несохранённые правки</IosBadge>}
                </div>
                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        className={iosBtnSecondary}
                        disabled={saving}
                        onClick={() => save(null)}
                    >
                        {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                        Сохранить
                    </button>
                    <button
                        type="button"
                        className={iosBtnPrimary}
                        disabled={saving}
                        onClick={() => save('published')}
                    >
                        Опубликовать
                    </button>
                </div>
            </div>

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>О статье</div>
                <div className={`${iosCard} space-y-3 p-4`}>
                    <input
                        className={`${iosInput} text-[16px] font-semibold`}
                        value={title}
                        onChange={(e) => { setTitle(e.target.value); setDirty(true); }}
                        placeholder="Название статьи"
                    />
                    <textarea
                        className={`${iosInput} min-h-[60px] resize-y`}
                        value={summary}
                        onChange={(e) => { setSummary(e.target.value); setDirty(true); }}
                        placeholder="Короткое описание — оно видно в списке и в поиске"
                    />
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                Тип
                            </label>
                            <CustomSelect
                                variant="ios"
                                value={articleType}
                                onChange={(v) => { setArticleType(v); setDirty(true); }}
                                options={ARTICLE_TYPES}
                                ariaLabel="Тип статьи"
                            />
                        </div>
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                Раздел
                            </label>
                            <CustomSelect
                                variant="ios"
                                value={sectionIds[0] ? String(sectionIds[0]) : ''}
                                onChange={(v) => { setSectionIds(v ? [Number(v)] : []); setDirty(true); }}
                                options={sectionOptions}
                                searchable
                                ariaLabel="Раздел статьи"
                            />
                        </div>
                    </div>
                </div>
            </section>

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Текст</div>
                <div className={`${iosCard} overflow-hidden`}>
                    {/* Панель липкая внутри своего контейнера: скроллится
                        .main-content, и sticky работает относительно неё. */}
                    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-0.5 border-b border-slate-100 bg-white/90 px-2 py-1.5 backdrop-blur-xl">
                        <ToolButton title="Отменить" onClick={() => editor.chain().focus().undo().run()}>
                            <Undo2 size={15} />
                        </ToolButton>
                        <ToolButton title="Повторить" onClick={() => editor.chain().focus().redo().run()}>
                            <Redo2 size={15} />
                        </ToolButton>
                        <Divider />

                        <ToolButton title="Жирный" active={editor.isActive('bold')}
                            onClick={() => editor.chain().focus().toggleBold().run()}>
                            <Bold size={15} />
                        </ToolButton>
                        <ToolButton title="Курсив" active={editor.isActive('italic')}
                            onClick={() => editor.chain().focus().toggleItalic().run()}>
                            <Italic size={15} />
                        </ToolButton>
                        <ToolButton title="Подчёркнутый" active={editor.isActive('underline')}
                            onClick={() => editor.chain().focus().toggleUnderline().run()}>
                            <UnderlineIcon size={15} />
                        </ToolButton>
                        <ToolButton title="Зачёркнутый" active={editor.isActive('strike')}
                            onClick={() => editor.chain().focus().toggleStrike().run()}>
                            <Strikethrough size={15} />
                        </ToolButton>
                        <Divider />

                        {[1, 2, 3].map((level) => {
                            const Icon = { 1: Heading1, 2: Heading2, 3: Heading3 }[level];
                            return (
                                <ToolButton
                                    key={level}
                                    title={`Заголовок ${level}`}
                                    active={editor.isActive('heading', { level })}
                                    onClick={() => editor.chain().focus().toggleHeading({ level }).run()}
                                >
                                    <Icon size={15} />
                                </ToolButton>
                            );
                        })}
                        <Divider />

                        <ToolButton title="Маркированный список" active={editor.isActive('bulletList')}
                            onClick={() => editor.chain().focus().toggleBulletList().run()}>
                            <List size={15} />
                        </ToolButton>
                        <ToolButton title="Нумерованный список" active={editor.isActive('orderedList')}
                            onClick={() => editor.chain().focus().toggleOrderedList().run()}>
                            <ListOrdered size={15} />
                        </ToolButton>
                        <ToolButton title="Цитата" active={editor.isActive('blockquote')}
                            onClick={() => editor.chain().focus().toggleBlockquote().run()}>
                            <Quote size={15} />
                        </ToolButton>
                        <ToolButton title="Код" active={editor.isActive('codeBlock')}
                            onClick={() => editor.chain().focus().toggleCodeBlock().run()}>
                            <Code size={15} />
                        </ToolButton>
                        <Divider />

                        {['left', 'center', 'right'].map((align) => {
                            const Icon = { left: AlignLeft, center: AlignCenter, right: AlignRight }[align];
                            return (
                                <ToolButton
                                    key={align}
                                    title={`Выравнивание: ${align}`}
                                    active={editor.isActive({ textAlign: align })}
                                    onClick={() => editor.chain().focus().setTextAlign(align).run()}
                                >
                                    <Icon size={15} />
                                </ToolButton>
                            );
                        })}
                        <Divider />

                        <ToolButton title="Ссылка" active={editor.isActive('link')} onClick={setLink}>
                            <Link2 size={15} />
                        </ToolButton>
                        <ToolButton
                            title="Таблица"
                            onClick={() => editor.chain().focus()
                                .insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
                        >
                            <TableIcon size={15} />
                        </ToolButton>
                        <Divider />

                        <span className="flex items-center gap-1 px-1">
                            <Highlighter size={14} className="text-slate-400" />
                            {HIGHLIGHT_COLORS.map((color) => (
                                <button
                                    key={color}
                                    type="button"
                                    title="Выделить цветом"
                                    aria-label={`Выделить цветом ${color}`}
                                    onMouseDown={(e) => e.preventDefault()}
                                    onClick={() => editor.chain().focus().toggleHighlight({ color }).run()}
                                    className="h-5 w-5 rounded-md ring-1 ring-slate-200 transition hover:scale-110"
                                    style={{ backgroundColor: color }}
                                />
                            ))}
                        </span>
                    </div>

                    <div className="px-4 py-4 sm:px-6">
                        <EditorContent editor={editor} />
                    </div>
                </div>
            </section>
        </div>
    );
}
