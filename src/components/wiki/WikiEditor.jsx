import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { EditorContent, useEditor, useEditorState } from '@tiptap/react';
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
    Gamepad2, Highlighter, Italic, Link2, List, ListOrdered, Loader2, Quote, Redo2,
    Image as ImageIcon, Save, Strikethrough, Table as TableIcon,
    Underline as UnderlineIcon, Undo2, Upload,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary, IosBadge,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { absolutizeFileUrls, relativizeFileUrls } from './fileUrls';
import { ARTICLE_TYPES, JOB_DESCRIPTION_TEMPLATE, TRAINER_TYPE } from './articleTypes';
import WikiTrainerNode from './trainers/TrainerNode';
import { TRAINER_CARDS, defaultButtonLabel, findTrainer } from './trainers/registry';
import SectionTreeSelect from './SectionTreeSelect';
import WikiAiDraft from './WikiAiDraft';
import WikiTableMenu from './WikiTableMenu';

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
    base, headers, showToast, article, sections, spaces = [], onClose, onSaved,
    pendingUpdateFile = null, onPendingUsed = null, onUpdateExisting = null,
}) {
    const isNew = !article?.id;
    // Статус берём из статьи, а не из «новизны»: существующий черновик тоже
    // должен уметь опубликоваться.
    const isPublished = article?.status === 'published';
    const [title, setTitle] = useState(article?.title || '');
    const [summary, setSummary] = useState(article?.summary || '');
    const [articleType, setArticleType] = useState(article?.article_type || 'general');
    const [sectionIds, setSectionIds] = useState(article?.section_ids || []);
    const [saving, setSaving] = useState(false);
    const [dirty, setDirty] = useState(false);
    const [importing, setImporting] = useState(false);
    /* Выбор в селекторе тренажёров держим пустым: селектор здесь — не поле со
       значением, а команда «вставить». Останься в нём выбранный тренажёр, второе
       нажатие по тому же пункту не считалось бы изменением, и вставить одну и ту
       же кнопку дважды стало бы нельзя. */
    const [trainerPick, setTrainerPick] = useState('');
    // Поддержка ИИ по умолчанию ВКЛЮЧЕНА: в базе рубильник называется ai_opt_out
    // и по умолчанию false, то есть новая статья и так участвует в ответах
    // помощника. Показать её выключенной значило бы соврать про текущее
    // состояние, а сохранить в этом виде — молча выключить то, что включено.
    const [aiSupport, setAiSupport] = useState(!article?.ai_opt_out);

    /* Название общего раздела — для подсказки под выбором раздела. Слаг тот же,
       что знает сервер (wiki/edit.py: _FALLBACK_SECTION_SLUG); совпадение
       намеренное, и расходиться им нельзя. */
    const fallbackSection = useMemo(() => {
        const section = (sections || []).find((s) => s.slug === 'obschiy-sotrudnik');
        if (!section) return null;
        const space = (spaces || []).find((sp) => sp.id === section.space_id);
        return space ? `${space.name} › ${section.name}` : section.name;
    }, [sections, spaces]);

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
            /* Кнопка тренажёра. Расширение подключено ВСЕГДА, а не только у
               статей-тренажёров: тип статьи можно сменить обратно, и без узла в
               схеме уже вставленная кнопка при открытии редактора превратилась
               бы в пустой абзац — то есть молча пропала бы из текста. */
            WikiTrainerNode,
        ],
        // Тот же разворот адресов, что и при чтении: иначе в редакторе картинки
        // уже загруженной статьи стоят битыми (см. fileUrls.js). Обратно они
        // сворачиваются при сохранении.
        content: absolutizeFileUrls(article?.content || '', base),
        onUpdate: () => setDirty(true),
        editorProps: {
            attributes: {
                class: 'wiki-prose min-h-[320px] focus:outline-none',
            },
        },
    }, [article?.id]);

    /* Подсветка активных кнопок тулбара.
     *
     * Читается через useEditorState, а не прямыми editor.isActive(...) в
     * разметке: useEditor в TipTap 3 по умолчанию НЕ перерисовывает компонент
     * на транзакциях (shouldRerenderOnTransaction: false), поэтому isActive в
     * теле рендера возвращал значение на момент ОТКРЫТИЯ редактора и больше
     * никогда не менялся — кнопки «жирный», «заголовок», «список» стояли
     * подсвеченными или погашенными наугад, независимо от того, где курсор.
     * Селектор пересчитывается на каждой транзакции, но перерисовывает только
     * при фактической смене набора — deepEqual внутри хука. */
    const active = useEditorState({
        editor,
        selector: ({ editor: ed }) => (ed ? {
            bold: ed.isActive('bold'),
            italic: ed.isActive('italic'),
            underline: ed.isActive('underline'),
            strike: ed.isActive('strike'),
            heading: [1, 2, 3].find((level) => ed.isActive('heading', { level })) || null,
            bulletList: ed.isActive('bulletList'),
            orderedList: ed.isActive('orderedList'),
            blockquote: ed.isActive('blockquote'),
            codeBlock: ed.isActive('codeBlock'),
            align: ['left', 'center', 'right'].find((a) => ed.isActive({ textAlign: a })) || null,
            link: ed.isActive('link'),
        } : null),
    });

    /* Смена типа на «Должностная инструкция» раскладывает скелет документа.
     *
     * Только в ПУСТУЮ статью: перебор типов на уже написанном тексте не должен
     * его затирать, а подтверждения здесь ставить не за что — человек выбирал
     * тип, а не соглашался потерять написанное. Пустоту спрашиваем у самого
     * редактора (editor.isEmpty), а не у длины HTML: пустой документ TipTap —
     * это '<p></p>', и проверка на непустую строку считала бы его текстом.
     */
    const applyTypeTemplate = useCallback((value) => {
        if (value !== 'job_description' || !editor?.isEmpty) return;
        editor.commands.setContent(JOB_DESCRIPTION_TEMPLATE);
    }, [editor]);

    // Предупреждаем о несохранённом при уходе со страницы. Внутри портала
    // навигация без перезагрузки, поэтому это только про закрытие вкладки.
    useEffect(() => {
        if (!dirty) return undefined;
        const handler = (event) => { event.preventDefault(); event.returnValue = ''; };
        window.addEventListener('beforeunload', handler);
        return () => window.removeEventListener('beforeunload', handler);
    }, [dirty]);

    const save = useCallback((status) => {
        if (!editor) return;
        if (!title.trim()) {
            showToast?.('Укажите название статьи', 'error');
            return;
        }
        const payload = {
            title: title.trim(),
            summary: summary.trim() || null,
            // Сворачиваем адреса файлов обратно в относительные: в базе тело
            // статьи не должно зависеть от домена API.
            content: relativizeFileUrls(editor.getHTML()),
            article_type: articleType,
            section_ids: sectionIds.map(Number).filter(Boolean),
            ai_support: aiSupport,
        };
        if (status) payload.status = status;

        setSaving(true);
        const request = isNew
            ? axios.post(`${base}/articles`, payload, { headers })
            : axios.patch(`${base}/articles/${article.id}`, payload, { headers });

        request
            .then((r) => {
                setDirty(false);
                // Говорим о том, ЧТО получилось, а не о том, что просили: у
                // создания статус может не примениться, если нет права
                // публикации в выбранном разделе.
                const applied = r.data?.status || status;
                showToast?.(
                    applied === 'published' ? 'Статья опубликована'
                        : status === 'published'
                            ? 'Сохранено черновиком: нет права публиковать в этом разделе'
                            : 'Сохранено',
                    applied !== 'published' && status === 'published' ? 'info' : 'success');
                onSaved?.(r.data?.slug || article?.slug);
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setSaving(false));
    }, [editor, title, summary, articleType, sectionIds, aiSupport, isNew, base,
        headers, article, showToast, onSaved]);

    const importDocument = (file) => {
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        setImporting(true);
        axios.post(`${base}/import`, form, { headers })
            .then((r) => {
                const data = r.data || {};
                if (!title.trim() && data.title) setTitle(data.title);
                if (!summary.trim() && data.summary) setSummary(data.summary);
                editor.commands.setContent(absolutizeFileUrls(data.content || '', base));
                setDirty(true);
                const extra = data.images?.length
                    ? `, картинок: ${data.images.length}` : '';
                showToast?.(`Документ разобран (${data.kind}${extra})`, 'success');
                if (data.warnings?.length) {
                    showToast?.(`Замечания при разборе: ${data.warnings.length}`, 'info');
                }
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось разобрать документ'), 'error'))
            .finally(() => setImporting(false));
    };

    const uploadImage = (file) => {
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        axios.post(`${base}/upload`, form, { headers })
            .then((r) => {
                // Адрес постоянный (/api/wiki/file/<id>), подпись выдаётся при
                // каждом запросе — картинки в статье не протухают.
                editor.chain().focus().setImage({ src: r.data.url }).run();
                setDirty(true);
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось загрузить картинку'), 'error'));
    };

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

    /* Вставка кнопки тренажёра. Подпись по умолчанию содержит название
       тренажёра: кнопка «Открыть тренажёр» без уточнения в статье с двумя
       разными тренажёрами не отвечает на вопрос, какой из них откроется. */
    const insertTrainer = (key) => {
        const scenario = findTrainer(key);
        if (!scenario || !editor) return;
        editor.commands.insertWikiTrainer({
            trainer: scenario.key,
            label: defaultButtonLabel(scenario),
            width: 60,
            align: 'left',
        });
        setDirty(true);
        setTrainerPick('');
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
                    <label
                        className={`${iosBtnSecondary} cursor-pointer`}
                        title="Разобрать формат документа без ИИ: ничего не уходит наружу"
                    >
                        {importing
                            ? <Loader2 size={14} className="animate-spin" />
                            : <Upload size={14} />}
                        Импорт как есть
                        <input
                            type="file"
                            className="hidden"
                            accept=".docx,.doc,.pdf,.xlsx,.xlsm,.csv,.txt,.md,.html,.htm"
                            onChange={(e) => { importDocument(e.target.files?.[0]); e.target.value = ''; }}
                        />
                    </label>
                    {/* «Опубликовать» показывается, пока статья НЕ опубликована.
                        У опубликованной эта кнопка бессмысленна и только пугает:
                        правка и так уходит читателям, статус менять не нужно.
                        А вот у существующего ЧЕРНОВИКА она обязана быть —
                        иначе черновик останется черновиком навсегда, и это
                        ровно та ловушка, из которой недавно не выбиралась
                        статья «Реестр акций». Поэтому условие про статус, а не
                        про «новая или нет». */}
                    <button
                        type="button"
                        className={isPublished ? iosBtnPrimary : iosBtnSecondary}
                        disabled={saving}
                        onClick={() => save(null)}
                    >
                        {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                        Сохранить
                    </button>
                    {!isPublished && (
                        <button
                            type="button"
                            className={iosBtnPrimary}
                            disabled={saving}
                            onClick={() => save('published')}
                        >
                            Опубликовать
                        </button>
                    )}
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
                                onChange={(v) => {
                                    setArticleType(v);
                                    setDirty(true);
                                    applyTypeTemplate(v);
                                }}
                                options={ARTICLE_TYPES}
                                ariaLabel="Тип статьи"
                            />
                        </div>
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                Раздел
                            </label>
                            {/* Дерево, а не плоский список: ветки СЗоВ и ОП
                                называются одинаково, и в общей выпадашке статья
                                уезжала не туда. */}
                            <SectionTreeSelect
                                sections={sections}
                                spaces={spaces}
                                value={sectionIds[0] || null}
                                onChange={(id) => { setSectionIds(id ? [id] : []); setDirty(true); }}
                            />
                            {/* Куда уедет статья, если раздел не выбрать.
                                Раньше она оставалась вообще без раздела и
                                пропадала из оглавления и поиска у всех, включая
                                автора; теперь сервер кладёт её в общий отдел, и
                                человек должен узнать об этом ДО сохранения, а
                                не обнаружить статью в чужой ветке потом. */}
                            {!sectionIds[0] && fallbackSection && (
                                <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-slate-400">
                                    Не выбран — статья попадёт в «{fallbackSection}».
                                </p>
                            )}
                        </div>
                    </div>
                </div>
            </section>

            <WikiAiDraft
                base={base}
                headers={headers}
                showToast={showToast}
                enabled={aiSupport}
                onEnabledChange={(value) => { setAiSupport(value); setDirty(true); }}
                excludeId={article?.id || null}
                getSnapshot={() => ({ title, content: editor?.getHTML() || '' })}
                pendingUpdateFile={pendingUpdateFile}
                onPendingUsed={onPendingUsed}
                onUpdateExisting={onUpdateExisting}
                onContent={(content) => {
                    // Обновление и правка меняют ТОЛЬКО текст: название и
                    // описание человек уже выверил, и перезаписывать их
                    // машинным вариантом было бы потерей его работы.
                    if (typeof content === 'string') {
                        editor?.commands.setContent(absolutizeFileUrls(content, base));
                        setDirty(true);
                    }
                }}
                onDraft={(data) => {
                    // Черновик ИИ ЗАМЕЩАЕТ поля, а не дописывает: он собран из
                    // документа целиком, и смешивать его с прежним текстом
                    // значило бы получить статью с двумя вступлениями.
                    if (data.title) setTitle(data.title);
                    if (data.summary) setSummary(data.summary);
                    editor?.commands.setContent(absolutizeFileUrls(data.content || '', base));
                    setDirty(true);
                }}
            />

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Текст</div>
                {/* overflow-hidden здесь БЫЛО и ломало закрепление панели: любой
                    предок с overflow, отличным от visible, становится для sticky
                    скролл-контейнером, а этот контейнер сам не прокручивается —
                    панель переставала липнуть и уезжала вверх вместе с карточкой.
                    Скругление верхних углов панели заменяет обрезку. */}
                <div className={iosCard}>
                    <div className="sticky top-0 z-20 flex flex-wrap items-center gap-0.5 rounded-t-2xl border-b border-slate-100 bg-white/95 px-2 py-1.5 backdrop-blur-xl">
                        <ToolButton title="Отменить" onClick={() => editor.chain().focus().undo().run()}>
                            <Undo2 size={15} />
                        </ToolButton>
                        <ToolButton title="Повторить" onClick={() => editor.chain().focus().redo().run()}>
                            <Redo2 size={15} />
                        </ToolButton>
                        <Divider />

                        <ToolButton title="Жирный" active={active?.bold}
                            onClick={() => editor.chain().focus().toggleBold().run()}>
                            <Bold size={15} />
                        </ToolButton>
                        <ToolButton title="Курсив" active={active?.italic}
                            onClick={() => editor.chain().focus().toggleItalic().run()}>
                            <Italic size={15} />
                        </ToolButton>
                        <ToolButton title="Подчёркнутый" active={active?.underline}
                            onClick={() => editor.chain().focus().toggleUnderline().run()}>
                            <UnderlineIcon size={15} />
                        </ToolButton>
                        <ToolButton title="Зачёркнутый" active={active?.strike}
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
                                    active={active?.heading === level}
                                    onClick={() => editor.chain().focus().toggleHeading({ level }).run()}
                                >
                                    <Icon size={15} />
                                </ToolButton>
                            );
                        })}
                        <Divider />

                        <ToolButton title="Маркированный список" active={active?.bulletList}
                            onClick={() => editor.chain().focus().toggleBulletList().run()}>
                            <List size={15} />
                        </ToolButton>
                        <ToolButton title="Нумерованный список" active={active?.orderedList}
                            onClick={() => editor.chain().focus().toggleOrderedList().run()}>
                            <ListOrdered size={15} />
                        </ToolButton>
                        <ToolButton title="Цитата" active={active?.blockquote}
                            onClick={() => editor.chain().focus().toggleBlockquote().run()}>
                            <Quote size={15} />
                        </ToolButton>
                        <ToolButton title="Код" active={active?.codeBlock}
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
                                    active={active?.align === align}
                                    onClick={() => editor.chain().focus().setTextAlign(align).run()}
                                >
                                    <Icon size={15} />
                                </ToolButton>
                            );
                        })}
                        <Divider />

                        <ToolButton title="Ссылка" active={active?.link} onClick={setLink}>
                            <Link2 size={15} />
                        </ToolButton>
                        <ToolButton
                            title="Таблица 3×3 — строки и столбцы добавляются панелью у таблицы"
                            onClick={() => editor.chain().focus()
                                .insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
                        >
                            <TableIcon size={15} />
                        </ToolButton>
                        <label
                            title="Картинка"
                            className="grid h-8 w-8 shrink-0 cursor-pointer place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100"
                        >
                            <ImageIcon size={15} />
                            <input
                                type="file"
                                className="hidden"
                                accept="image/*"
                                onChange={(e) => { uploadImage(e.target.files?.[0]); e.target.value = ''; }}
                            />
                        </label>
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

                        {/* Выбор тренажёра — в конце того же ряда, где жирный и
                            курсив: вставка кнопки для автора статьи-тренажёра
                            такое же обычное действие, как вставка картинки.
                            Появляется ТОЛЬКО у типа «Тренажёр»: у остальных
                            статей это лишний элемент, который приходится читать
                            и понимать, зачем он тут. */}
                        {articleType === TRAINER_TYPE && (
                            <>
                                <Divider />
                                <span className="flex items-center gap-1.5 px-1">
                                    <Gamepad2 size={14} className="text-slate-400" />
                                    <CustomSelect
                                        variant="ios"
                                        className="w-[210px]"
                                        value={trainerPick}
                                        onChange={insertTrainer}
                                        options={TRAINER_CARDS.map((card) => ({
                                            value: card.key,
                                            label: `${card.title} · ${card.stages} шагов`,
                                        }))}
                                        placeholder="Вставить тренажёр…"
                                        ariaLabel="Вставить кнопку тренажёра"
                                    />
                                </span>
                            </>
                        )}
                    </div>

                    <div className="px-4 py-4 sm:px-6">
                        <EditorContent editor={editor} />
                        {/* Панель команд таблицы: всплывает у той таблицы, в
                            которой стоит курсор. Живёт рядом с EditorContent,
                            потому что позиционируется от родителя редактора. */}
                        <WikiTableMenu editor={editor} />
                    </div>
                </div>
            </section>
        </div>
    );
}
