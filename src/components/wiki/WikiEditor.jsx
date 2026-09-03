import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
// mergeAttributes берём из @tiptap/react, а не из @tiptap/core: core стоит
// транзитивно и в package.json не заявлен, а на нетронутый транзитивный пакет
// опираться нельзя — он переедет при первой же переустановке зависимостей.
import { EditorContent, mergeAttributes, useEditor, useEditorState } from '@tiptap/react';
/* NodeSelection — из @tiptap/pm, а не из prosemirror-state напрямую: тот стоит
   транзитивно и в package.json не заявлен, а `npm ci` на Pages сверяет
   манифест с локом. */
import { NodeSelection } from '@tiptap/pm/state';
import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import Underline from '@tiptap/extension-underline';
import TextAlign from '@tiptap/extension-text-align';
import Highlight from '@tiptap/extension-highlight';
// В TipTap 3 у этих пакетов нет default-экспорта, а таблицы приезжают одним
// набором — отдельные extension-table-row/cell/header больше не нужны.
import { Color, TextStyle } from '@tiptap/extension-text-style';
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table';
import {
    AlignCenter, AlignLeft, AlignRight, Blocks, Bold, Code, FileSymlink, Heading1, Heading2,
    Heading3, Gamepad2, Highlighter, Italic, Link2, List, ListOrdered, Loader2, Quote, Redo2,
    Image as ImageIcon, Save, Strikethrough, Table as TableIcon,
    Underline as UnderlineIcon, Undo2, Upload,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary, IosBadge, IosHint,
    IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { absoluteFileUrl, absolutizeFileUrls, relativizeFileUrls } from './fileUrls';
import { ARTICLE_TYPES, JOB_DESCRIPTION_TEMPLATE, TRAINER_TYPE } from './articleTypes';
import WikiImage from './WikiImageNode';
import WikiTrainerNode from './trainers/TrainerNode';
import { TRAINER_CARDS, defaultButtonLabel, findTrainer } from './trainers/registry';
import SectionTreeSelect from './SectionTreeSelect';
import ArticlePicker from './ArticlePicker';
import { buildRelativeArticleLink, linkAttrsForSaving } from './articleLink';
import useStableCallback from './useStableCallback';
import WikiAiDraft from './WikiAiDraft';
import WikiTableMenu from './WikiTableMenu';
import WikiBlockMenu from './WikiBlockMenu';
import { BLOCK_MENU, WikiBlock, WikiListVariant } from './WikiBlockNode';
// Стили оформительских блоков. Импорт нужен и здесь, и в WikiArticle.jsx:
// редактор — отдельный ленивый чанк, и без своего импорта он получил бы блоки
// без вида, то есть автор правил бы не то, что увидит читатель.
import './wiki-blocks.css';

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

/* Подсказка у меню блоков. Отвечает не на вопрос «какие бывают» — это и так
   видно в списке, — а на вопрос «когда какой», потому что статью портят не
   отсутствующие блоки, а блоки не к месту. */
const BLOCK_HINT = 'Вводка — первый абзац: о чём статья и кому нужна. '
    + 'Плашка — то, что нельзя пропустить (тон выбирается в панели у самого блока). '
    + 'Шаги — действия строго по порядку. Карточки — равнозначные куски рядом. '
    + 'Чипы — перечень коротких значений. Галочки — что входит или уже сделано. '
    + 'Правило одно: блок ставится там, где экономит читателю время. '
    + 'Три плашки подряд не выделяют ничего.';

/* Картинки из буфера обмена или из перетаскивания.
 *
 * Пустой список означает «это не про картинки, разбирайся сам» — и обычная
 * вставка текста остаётся нетронутой.
 *
 * Отдельная оговорка про кусок документа. Копируя абзац с иллюстрацией из Word
 * или из браузера, человек кладёт в буфер И текст, И файл картинки. Забрать
 * оттуда только картинку значило бы потерять абзац, поэтому такой буфер целиком
 * уходит редактору: признак — непустой text/plain. У снимка экрана его нет,
 * поэтому «вставил скриншот» через это условие проходит.
 */
const imageFiles = (event) => {
    const transfer = event.clipboardData || event.dataTransfer;
    if (!transfer) return [];
    if (event.clipboardData && String(transfer.getData('text/plain') || '').trim()) return [];
    return Array.from(transfer.files || []).filter((file) => file.type?.startsWith('image/'));
};

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

/* Ссылка, которая знает, своя она или чужая.
 *
 * Расширение Link по умолчанию ставит КАЖДОЙ ссылке target="_blank"
 * (@tiptap/extension-link, options.HTMLAttributes.target), а витрина статьи
 * ровно на этот признак отказывается открывать статью внутри портала
 * (WikiArticle: `if (!anchor || anchor.target === '_blank') return`). Беда не
 * только в новых ссылках: TipTap разбирает тело статьи и собирает его обратно,
 * поэтому при ПЕРВОМ ЖЕ сохранении target="_blank" дописался бы всем уже
 * лежащим в базе внутренним ссылкам — а их в проде 253. Фича сломала бы то, что
 * работало, раньше, чем добавила новое.
 *
 * Поэтому: своим ссылкам target не ставим вовсе (портал откроет статью сам, без
 * перезагрузки приложения и потери места), чужим оставляем как было — внешний
 * сайт поверх портала открываться не должен.
 *
 * Решение «своя или чужая» берём у readArticleSlugFromHref — той самой функции,
 * которой это же решает витрина. Двух правил тут быть не может.
 */
const WikiLink = Link.extend({
    renderHTML({ HTMLAttributes }) {
        // Само правило живёт в articleLink.js чистой функцией: без браузера
        // расширение TipTap не проверить, а это самое дорогое место фичи.
        return ['a', linkAttrsForSaving(
            mergeAttributes(this.options.HTMLAttributes, HTMLAttributes)), 0];
    },
});

export default function WikiEditor({
    base, headers, showToast, article, sections, spaces = [], onClose, onSaved,
    pendingUpdateFile = null, onPendingUsed = null, onUpdateExisting = null,
    features = null,
    /* Пространство, в котором человек сейчас работает. Нужно ЖУРНАЛУ: пока
       статьи нет, её пространство вывести не из чего, и запись о черновике
       из документа оказывалась «ничьей» — а «ничья» запись показывается в
       журнале И «Таксопарков», И «Теза» (wiki/structure.py: _audit_filters).
       Отсюда и жалоба «журналы перемешались». */
    spaceId = null,
    /* Оглавление витрины — источник для пикера внутренних ссылок. Приходит уже
       загруженным и уже суженным по пространству; своего запроса пикер не
       делает (см. ArticlePicker.jsx). */
    articles = [],
}) {
    const isNew = !article?.id;
    // Статус берём из статьи, а не из «новизны»: существующий черновик тоже
    // должен уметь опубликоваться.
    const isPublished = article?.status === 'published';
    const [title, setTitle] = useState(article?.title || '');
    const [summary, setSummary] = useState(article?.summary || '');
    const [articleType, setArticleType] = useState(article?.article_type || 'general');
    /* Типы, доступные в ЭТОМ пространстве. «Тренажёр» — единственный тип, за
       которым стоит целая вкладка: выключили «Тренажёры» — предлагать тип,
       который потом негде собрать и нечем наполнить, нельзя.
       Уже проставленный тип из списка НЕ выкидываем: статья, написанная до
       выключения вкладки, иначе открылась бы с пустым селектом и сохранилась
       бы «обычной» — молча сменив тип у чужого документа. */
    const articleTypes = useMemo(() => ARTICLE_TYPES.filter(
        (type) => type.value !== TRAINER_TYPE
            || features?.catalog_trainers !== false
            || article?.article_type === TRAINER_TYPE,
    ), [features, article]);
    const [sectionIds, setSectionIds] = useState(article?.section_ids || []);
    const [saving, setSaving] = useState(false);
    const [dirty, setDirty] = useState(false);
    const [importing, setImporting] = useState(false);
    const [pickerOpen, setPickerOpen] = useState(false);
    /* Выбор в селекторе тренажёров держим пустым: селектор здесь — не поле со
       значением, а команда «вставить». Останься в нём выбранный тренажёр, второе
       нажатие по тому же пункту не считалось бы изменением, и вставить одну и ту
       же кнопку дважды стало бы нельзя. */
    const [trainerPick, setTrainerPick] = useState('');
    // Выбор блока держим пустым по той же причине, что и выбор тренажёра:
    // селектор здесь — команда «вставить», а не поле со значением.
    const [blockPick, setBlockPick] = useState('');
    // Поддержка ИИ по умолчанию ВКЛЮЧЕНА: в базе рубильник называется ai_opt_out
    // и по умолчанию false, то есть новая статья и так участвует в ответах
    // помощника. Показать её выключенной значило бы соврать про текущее
    // состояние, а сохранить в этом виде — молча выключить то, что включено.
    const [aiSupport, setAiSupport] = useState(!article?.ai_opt_out);
    /* Защита от копирования. Формулировка ПОЛОЖИТЕЛЬНАЯ и в базе, и здесь:
       включён тумблер — защита стоит. Инверсии, как у поддержки ИИ, тут не
       нужно, и заводить её было бы вредно — два имени у одного признака это
       второе место, где можно ошибиться знаком. */
    const [copyProtected, setCopyProtected] = useState(!!article?.copy_protected);
    /* «Сведения не действуют»: статья остаётся в вике и в ответах помощника, но
       каждый её фрагмент едет к модели и к оператору с пометкой «архив». Тумблер
       положительный, как и защита от копирования: включён — пометка стоит.

       Отдельно от «Убрать в архив» намеренно. Архив ПРЯЧЕТ статью от рядового
       читателя, а справку о прошлых акциях операторы читать обязаны — именно
       поэтому «Архивные акции TEZ» и опубликовали как обычную статью, из-за
       чего помощник 27.08.2026 выдал закончившуюся акцию как действующую. */
    const [historical, setHistorical] = useState(!!article?.historical);

    /* Название общего раздела — для подсказки под выбором раздела. Слаг тот же,
       что знает сервер (wiki/edit.py: _FALLBACK_SECTION_SLUG); совпадение
       намеренное, и расходиться им нельзя. */
    const fallbackSection = useMemo(() => {
        const section = (sections || []).find((s) => s.slug === 'obschiy-sotrudnik');
        if (!section) return null;
        const space = (spaces || []).find((sp) => sp.id === section.space_id);
        return space ? `${space.name} › ${section.name}` : section.name;
    }, [sections, spaces]);

    /* В выпадашке — только разделы, куда этот человек ВПРАВЕ положить статью.
       Сервер проверяет ровно это (can_create в правиле раздела, routes_edit),
       и предлагать ветку, на которой он ответит 403, значит выдавать отказ за
       поломку. Раздел самой статьи остаётся в списке всегда: иначе поле
       опустело бы у того, кто правит чужую статью, и сохранение молча увезло
       бы её в другое место. Ответ без прав (структура ещё не пришла) не
       фильтруем вовсе — пустая выпадашка хуже лишней строки. */
    const creatableSections = useMemo(() => {
        const list = sections || [];
        if (!list.some((s) => s.permissions)) return list;
        const current = String(sectionIds[0] ?? '');
        return list.filter(
            (s) => s.permissions?.can_create || String(s.id) === current);
    }, [sections, sectionIds]);

    /* «Опубликовать» — это ПРАВО, а не просто кнопка. У существующей статьи его
       уже посчитал сервер (article.permissions), у новой оно берётся из правила
       раздела, куда её кладут, — так же, как список разделов выше.

       Раньше кнопка стояла на одном лишь статусе, и человек с правом только
       править жал её, чтобы получить отказ тостом (routes_edit: «Нет права
       публиковать эту статью»). Пока правки в разделе доставались только
       супервайзеру и выше, у которых право выпуска есть всегда, это не
       выстреливало; с 21.08.2026 правку выдают правилом поимённо, и выдать
       можно одну лишь правку.

       Неизвестность толкуем в пользу кнопки: права ещё не приехали — показываем.
       Спрятать кнопку у того, кто вправе публиковать, хуже, чем показать её
       лишний раз: во втором случае человек увидит внятный отказ, в первом —
       ничего. */
    const mayPublish = useMemo(() => {
        if (article?.permissions) return !!article.permissions.can_publish;
        const list = sections || [];
        if (!list.some((s) => s.permissions)) return true;
        const chosen = sectionIds.map(String);
        if (!chosen.length) return true;
        return list.some((s) => chosen.includes(String(s.id))
            && s.permissions?.can_publish);
    }, [article, sections, sectionIds]);

    const editor = useEditor({
        extensions: [
            StarterKit.configure({ heading: { levels: [1, 2, 3, 4] } }),
            Underline,
            WikiLink.configure({ openOnClick: false, autolink: true }),
            /* Картинка со своим видом узла: размер и выравнивание. Стоковое
               @tiptap/extension-image умеет только вставить <img> — см. шапку
               WikiImageNode.jsx о том, почему не подошёл и встроенный resize.

               allowBase64 остаётся: в статьях, перенесённых из старой вики,
               картинки лежат строкой data:image/*, и без разрешения они
               пропали бы из текста при первом же открытии редактора. Новые
               так уже не появляются — вставленный скриншот уходит в бакет
               (handlePaste ниже). */
            WikiImage.configure({ inline: false, allowBase64: true }),
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
            /* Оформительские блоки: вводка, плашка, сетка карточек. Как и
               кнопка тренажёра, подключены ВСЕГДА и без условий — узел в схеме
               нужен не для того, чтобы блок вставить, а для того, чтобы уже
               стоящий в статье блок пережил открытие редактора. Нет узла —
               TipTap разбирает <div> в обычные абзацы, getHTML() отдаёт их же,
               и сохранение стирает оформление молча, безо всякого сообщения.
               Ровно это уже случилось в разделе с раскрывающимися блоками
               <details>: они разрешены обоими санитайзерами, но узла у них
               нет, и редактор их ломает до сих пор. */
            WikiBlock,
            /* Вид списка («шаги», «чипы», «галочки») — атрибут на самом
               <ol>/<ul>, а не отдельный узел: список обязан остаться списком
               для поиска, для выгрузки и для кнопки «нумерованный список» в
               этой же панели. */
            WikiListVariant,
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
            /* Скриншот из буфера и картинка, перетащенная в окно, уходят В
               БАКЕТ, а не в текст статьи.

               Без этих двух обработчиков TipTap кладёт вставленное
               изображение прямо в разметку строкой data:image/* — она
               разрешена (allowBase64) и её пропускает санитайзер. Статья от
               одного скриншота толстеет на мегабайты, в поиск попадает
               мусором, а главное — мимо хранилища проходит самый частый
               способ добавить картинку: в старой вике на такие вставки
               пришлось 81 % всего объёма контента. Заодно это единственный
               путь, на котором картинка иначе не превратилась бы в WebP:
               пережимает их сервер, при укладке в бакет (wiki/images.py). */
            handlePaste: (_view, event) => insertImageFiles(imageFiles(event)),
            handleDrop: (view, event) => insertImageFiles(
                imageFiles(event),
                // Картинка должна лечь ТУДА, КУДА её бросили, а не туда, где
                // стоял курсор до перетаскивания.
                view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos),
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
            copy_protected: copyProtected,
            historical,
            // Для журнала: сервер запишет действие в это пространство, даже
            // если раздел статье достался запасной.
            space_id: spaceId || undefined,
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
    }, [editor, title, summary, articleType, sectionIds, aiSupport, copyProtected,
        historical, isNew, base, headers, article, showToast, onSaved]);

    const importDocument = (file) => {
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        setImporting(true);
        axios.post(`${base}/import`, form, { headers, params: { space_id: spaceId || undefined } })
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

    /* Загрузка картинки. Обёрнута в useStableCallback, потому что её зовут из
       editorProps: тот объект собирается ОДИН раз, при создании редактора, и
       обычная стрелка осталась бы там навсегда со ссылками на первый рендер —
       на ещё не созданный editor в том числе. */
    const uploadImage = useStableCallback((file, at) => {
        if (!file) return undefined;
        const form = new FormData();
        form.append('file', file);
        return axios.post(`${base}/upload`, form, { headers })
            .then((r) => {
                /* Адрес постоянный (/api/wiki/file/<id>), подпись выдаётся при
                   каждом запросе — картинки в статье не протухают.

                   В УЗЕЛ адрес идёт РАСКРЫТЫМ до домена API — тем же разворотом,
                   что и тело уже сохранённой статьи (fileUrls.js). Сервер отдаёт
                   его относительным, и без разворота свежая картинка вставала
                   битой: относительный адрес браузер разрешает относительно
                   СТРАНИЦЫ, а страница отдаётся с Pages, где никакого /api нет.
                   То есть только что загруженный кадр немедленно получал 404 —
                   до сохранения статьи его было не видно вообще. Обратно адрес
                   свернётся при сохранении (relativizeFileUrls). */
                const src = absoluteFileUrl(r.data.url, base);
                const chain = editor.chain().focus();
                if (typeof at === 'number') {
                    chain.setTextSelection(at);
                } else if (editor.state.selection instanceof NodeSelection) {
                    /* ВЫДЕЛЕН УЗЕЛ — ВСТАВЛЯЕМ ЗА НИМ, А НЕ ВМЕСТО НЕГО.
                       Щелчок по картинке даёт выделение всего узла, а обычная
                       вставка заменяет выделенное: скриншот, вставленный при
                       выбранном кадре галереи, ЗАТИРАЛ этот кадр. Пропажу и не
                       заметить — кадров было три, стало три же. Текстовое
                       выделение по-прежнему заменяется, как от вставки и ждут. */
                    chain.insertContentAt(editor.state.selection.to,
                        { type: 'image', attrs: { src } }).run();
                    setDirty(true);
                    return;
                }
                chain.setImage({ src }).run();
                setDirty(true);
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось загрузить картинку'), 'error'));
    });

    /* «+ кадр» из панели галереи. Отдельно от uploadImage потому, что место
       вставки здесь определяет не курсор, а сама галерея: команда addWikiFrame
       кладёт кадр её ПОСЛЕДНИМ ребёнком. Файлы уходят по очереди — по тем же
       двум причинам, что и при перетаскивании (см. insertImageFiles). */
    const addGalleryFrames = useStableCallback((files) => {
        if (!files?.length) return undefined;
        return files.reduce((queue, file) => queue.then(() => {
            const form = new FormData();
            form.append('file', file);
            return axios.post(`${base}/upload`, form, { headers })
                // Тот же разворот адреса, что и в uploadImage: без него кадр
                // галереи вставал битым до сохранения статьи.
                .then((r) => {
                    editor.chain().addWikiFrame(absoluteFileUrl(r.data.url, base)).run();
                    setDirty(true);
                })
                .catch((e) => showToast?.(errText(e, 'Не удалось загрузить кадр'), 'error'));
        }), Promise.resolve());
    });

    /* true — событие разобрано нами, ProseMirror пусть не вставляет ничего сам.
       Пустой список — false, и обычная вставка текста работает как работала.

       Файлы уходят ПО ОЧЕРЕДИ, а не пачкой, и причин две. Первая — сервер: на
       каждую загрузку /upload занимает соединение из пула вики и пережимает
       картинку в WebP, то есть десяток брошенных разом файлов это десяток
       занятых соединений и десяток одновременных кодирований. Вторая — порядок:
       позиция вставки считается один раз, по месту, куда бросили, и параллельные
       вставки уложили бы картинки в обратном порядке. Поэтому место указывается
       только первой, а каждая следующая встаёт за предыдущей. */
    const insertImageFiles = useStableCallback((files, at) => {
        if (!files.length) return false;
        files.reduce(
            (queue, file, index) => queue.then(() => uploadImage(file, index ? undefined : at)),
            Promise.resolve());
        return true;
    });

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

    /* Вставка ссылки на статью вики.
     *
     * Два случая, и путать их нельзя. Если человек выделил текст — превращаем
     * выделенное в ссылку. Если не выделил (а это типовой случай: «поставь тут
     * ссылку на Тарифы»), setLink не сделал бы НИЧЕГО видимого: он ставит метку
     * на пустой диапазон, а текста в документе не появляется. Поэтому вставляем
     * название статьи текстом и уже его помечаем ссылкой.
     *
     * unsetMark в конце — из-за autolink: с ним метка ссылки «включающая»
     * (inclusive), и текст, набранный сразу после вставки, продолжал бы быть
     * частью ссылки. Выглядит это как «полстроки уехало в чужую статью».
     */
    const insertArticleLink = (row) => {
        setPickerOpen(false);
        const href = buildRelativeArticleLink(row?.slug);
        if (!href) {
            showToast?.('У этой статьи неподходящий адрес', 'error');
            return;
        }
        const { from, to } = editor.state.selection;
        if (from !== to) {
            editor.chain().focus().extendMarkRange('link').setLink({ href }).run();
            return;
        }
        editor.chain().focus()
            .insertContent({
                type: 'text',
                text: row.title,
                marks: [{ type: 'link', attrs: { href } }],
            })
            .unsetMark('link')
            .run();
        setDirty(true);
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

    /* Вставка оформительского блока.
     *
     * Две разные операции под одним пунктом меню — и это не небрежность.
     * Вводка, плашка и сетка карточек ВСТАВЛЯЮТСЯ шаблоном: их не из чего
     * сделать, они появляются пустыми и заполняются автором. Шаги, чипы и
     * галочки, наоборот, ПРЕВРАЩАЮТ уже написанное: выделил три абзаца,
     * выбрал «Шаги» — получил три шага с номерами. Вставлять их шаблоном
     * значило бы заставить человека набирать текст заново рядом с тем,
     * который у него уже есть.
     *
     * Повторный выбор того же пункта для списков снимает вид обратно — иначе
     * превратить шаги назад в обычный список можно было бы только удалением. */
    const insertBlock = (key) => {
        const item = BLOCK_MENU.find((entry) => entry.key === key);
        if (!item || !editor) return;
        if (item.action === 'variant') editor.commands.toggleListVariant(item.value);
        else editor.commands.insertWikiBlock(item.key);
        setDirty(true);
        setBlockPick('');
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
                    {!isPublished && mayPublish && (
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
                                options={articleTypes}
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
                                sections={creatableSections}
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

                    {/* Защита от копирования — свойство ДОКУМЕНТА, поэтому она
                        здесь, рядом с типом и разделом, а не в правах доступа:
                        кому статья видна, тумблер не меняет вовсе.
                        Оговорка под тумблером обязательна. Запрет держится на
                        браузере читателя, и человек, включивший его в расчёте на
                        «текст отсюда не унесут», должен узнать про снимок экрана
                        здесь, а не после утечки. */}
                    <div className="flex items-start justify-between gap-3 border-t border-slate-100 pt-3">
                        <div className="min-w-0">
                            <div className="text-[14px] font-medium text-slate-900">
                                Защита от копирования
                            </div>
                            <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-400">
                                Текст нельзя будет выделить, скопировать и распечатать.
                                Не спасает от снимка экрана; отрывки статьи по-прежнему
                                показывают поиск и ИИ-помощник, а весь текст видит тот,
                                кто откроет её на правку.
                            </p>
                        </div>
                        <IosToggle
                            checked={copyProtected}
                            onChange={(value) => { setCopyProtected(value); setDirty(true); }}
                        />
                    </div>

                    {/* Оговорка под тумблером обязательна, как и у защиты от
                        копирования, но говорит о другом: человек должен понять,
                        что статья НЕ прячется. Иначе он потянется к «Убрать в
                        архив» — и справку, которую операторы читают, никто
                        больше не увидит. */}
                    <div className="flex items-start justify-between gap-3 border-t border-slate-100 pt-3">
                        <div className="min-w-0">
                            <div className="text-[14px] font-medium text-slate-900">
                                Сведения уже не действуют
                            </div>
                            <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-400">
                                Для справок о прошлом: архивные акции, отменённые
                                правила. Статья остаётся в вике и в ответах
                                ИИ-помощника, но он больше не выдаст её за
                                действующее — пометит «архив» и предупредит
                                оператора. Спрятать статью — это «Убрать в архив».
                            </p>
                        </div>
                        <IosToggle
                            checked={historical}
                            onChange={(value) => { setHistorical(value); setDirty(true); }}
                        />
                    </div>
                </div>
            </section>

            <WikiAiDraft
                base={base}
                headers={headers}
                showToast={showToast}
                spaceId={spaceId}
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
                        {/* Ссылка на статью вики — отдельной кнопкой от обычной
                            ссылки. Складывать их в одну («сначала спросим адрес,
                            потом предложим статью») значит прятать главный
                            случай за диалогом про адрес. */}
                        <ToolButton title="Ссылка на статью вики"
                                    onClick={() => setPickerOpen(true)}>
                            <FileSymlink size={15} />
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
                                multiple
                                onChange={(e) => {
                                    /* multiple, а не одна картинка: кадры
                                       галереи выбирают пачкой, и без этого
                                       автору приходилось открывать окно выбора
                                       по разу на кадр. Очередь insertImageFiles
                                       пачку уже умеет. */
                                    insertImageFiles(Array.from(e.target.files || []));
                                    e.target.value = '';
                                }}
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

                        {/* Оформительские блоки — одним селектором-командой, а
                            не шестью кнопками: панель здесь один ряд с
                            переносом (flex-wrap), и шесть значков утащили бы
                            её на вторую строку у всех, включая тех, кто блоки
                            не ставит никогда.

                            Подсказка «i» рядом объясняет, ЧТО КОГДА ставить.
                            Без неё меню отвечает только на вопрос «какие
                            бывают», а статью портит не отсутствие блоков, а
                            блоки не к месту: плашка на каждый абзац перестаёт
                            что-либо выделять. */}
                        <Divider />
                        <span className="flex items-center gap-1.5 px-1">
                            <Blocks size={14} className="text-slate-400" />
                            <CustomSelect
                                variant="ios"
                                className="w-[185px]"
                                value={blockPick}
                                onChange={insertBlock}
                                options={BLOCK_MENU.map((item) => ({
                                    value: item.key,
                                    label: item.label,
                                    groupLabel: item.action === 'insert'
                                        ? 'Вставить' : 'Оформить выделенное',
                                }))}
                                placeholder="Блок…"
                                ariaLabel="Вставить оформительский блок"
                            />
                            <IosHint align="right" label="Какой блок когда ставить"
                                     text={BLOCK_HINT} />
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
                        {/* Панель оформительского блока — там же и по той же
                            причине: команды («тон», «столбцы», «нумеровать»)
                            имеют смысл только внутри блока, и в постоянном
                            тулбаре стояли бы погашенными. Двух панелей разом
                            не бывает: у блока с таблицей внутри всплывает
                            панель таблицы — так решено в её shouldShow. */}
                        <WikiBlockMenu editor={editor} onAddFrame={addGalleryFrames} />
                    </div>
                </div>
            </section>

            <ArticlePicker
                open={pickerOpen}
                articles={articles}
                currentId={article?.id || null}
                onPick={insertArticleLink}
                onClose={() => setPickerOpen(false)}
            />
        </div>
    );
}
