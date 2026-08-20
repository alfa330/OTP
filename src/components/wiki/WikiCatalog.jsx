import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Archive, ChevronRight, Eye, FileText, Folder, FolderOpen, Layers, Loader2,
    MousePointerClick, PenLine, Search, User, X,
} from 'lucide-react';
import { iosCard, IosBadge } from '../ui/ios';
import { typeBadge } from './articleTypes';

/* Вкладка «Статьи» — каталог: дерево разделов слева, статьи выбранного справа.
 *
 * Отличие от главной. Главная отвечает на вопрос «что почитать МНЕ»: избранное,
 * недавнее, популярное. Каталог отвечает на другой — «что вообще лежит в
 * разделе N». Это разные вопросы, и мешать их в одном экране нельзя.
 *
 * ── Почему экран устроен именно так ────────────────────────────────────────
 *
 * ДЕРЕВО, А НЕ СЕТКА ПЛИТОК. Плитки не умеют показать иерархию: «Регламенты»
 * лежат ВНУТРИ «Оператора», а в сетке стояли рядом как равные. Дерево — та же
 * раскладка, что в оглавлении на главной (WikiIndexPanel), и намеренно: человек
 * уже научился читать её там, второму способу показывать одну и ту же структуру
 * взяться неоткуда.
 *
 * РЕЗУЛЬТАТ СПРАВА, А НЕ В МОДАЛКЕ. Модальное окно закрывает собой дерево, и
 * чтобы сравнить два раздела, его приходилось открывать и закрывать. Справа
 * место всё равно пустует: список живёт рядом с деревом, выбор виден, переход
 * между разделами — одно нажатие.
 *
 * ПОЯСНЕНИЕ ЖИВЁТ В ПУСТОЙ ПРАВОЙ КОЛОНКЕ. Пока раздел не выбран, там стоит
 * объяснение, что это за экран. Так подсказка появляется ровно там, куда
 * смотрит человек, впервые сюда попавший, и исчезает, как только перестаёт быть
 * нужной, — вместо вечной шапки, которую перестают читать на второй день.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Ключи совпадают с ARTICLE_BUCKETS на сервере: ключ уходит в ?bucket= и по нему
   же приходят counts. Подписи, иконки и пояснения — только здесь. */
const BUCKETS = [
    { key: 'published', label: 'Статьи', icon: FileText,
      nothing: 'В доступных вам разделах пока нет опубликованных статей.',
      emptyHere: 'В этом разделе нет опубликованных статей.' },
    { key: 'draft', label: 'Черновики', icon: PenLine,
      nothing: 'Незаконченных статей нет — всё, что начато, уже опубликовано.',
      emptyHere: 'В этом разделе нет черновиков и статей на согласовании.' },
    { key: 'archived', label: 'Архив', icon: Archive,
      nothing: 'В архиве пусто — ни одну статью ещё не убирали.',
      emptyHere: 'В этом разделе нет архивных статей.' },
];

const BUCKET_BY_KEY = new Map(BUCKETS.map((b) => [b.key, b]));

// Синтетический раздел для статей, не привязанных ни к одной ветке. Та же
// подпись, что в оглавлении на главной (WikiIndexPanel) — это одно и то же.
const ORPHANS_ID = 'none';

const plural = (n, one, few, many) => {
    const mod100 = Math.abs(n) % 100;
    const mod10 = mod100 % 10;
    if (mod100 >= 11 && mod100 <= 14) return many;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return few;
    return many;
};

const articleWord = (n) => plural(n, 'статья', 'статьи', 'статей');
const sectionWord = (n) => plural(n, 'раздел', 'раздела', 'разделов');

const DAY = 24 * 60 * 60 * 1000;

/* «вчера» вместо «19.08.26»: в каталоге важна свежесть документа, а не дата.
   Та же шкала, что на главной (WikiHome). */
const fmtAgo = (iso) => {
    if (!iso) return '';
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) return '';
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const days = Math.floor((midnight.getTime() - then.getTime()) / DAY) + 1;
    if (days <= 0) return 'обновлена сегодня';
    if (days === 1) return 'обновлена вчера';
    if (days < 7) return `обновлена ${days} дн. назад`;
    return `обновлена ${then.toLocaleDateString('ru-RU',
        { day: '2-digit', month: '2-digit', year: '2-digit' })}`;
};

/* Подписи статусов совпадают с шапкой статьи (WikiArticle): один и тот же
   статус обязан называться одинаково в списке и на самой статье. */
const STATUS_LABELS = {
    draft: 'Черновик',
    on_approval: 'На согласовании',
    published: 'Опубликована',
    requires_verification: 'Требует проверки',
    archived: 'В архиве',
    expired: 'Устарела',
};

const STATUS_TONES = {
    draft: 'slate', on_approval: 'amber', published: 'green',
    requires_verification: 'amber', archived: 'slate', expired: 'red',
};

const toggled = (set, key) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
};

/* ── Переключатель корзин ──────────────────────────────────────────────────
   Счётчик стоит прямо на кнопке: он и есть ответ на вопрос «а есть ли там
   вообще что-нибудь», ради которого иначе пришлось бы переключиться и
   посмотреть. Пояснительной строки под кнопками нет по решению владельца:
   экран объясняет себя пустой правой колонкой, а вечная подпись сверху
   перестаёт читаться на второй день. */
export const BucketSwitch = ({ value, onChange, totals }) => (
    <div className="inline-flex max-w-full gap-1 overflow-x-auto rounded-2xl bg-slate-100 p-1">
        {BUCKETS.map(({ key, label, icon: Icon }) => {
            const on = value === key;
            return (
                <button
                    key={key}
                    type="button"
                    aria-pressed={on}
                    onClick={() => onChange(key)}
                    className={`flex shrink-0 items-center gap-2 rounded-xl px-3.5 py-2 text-[13px] font-medium transition ${
                        on ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                    }`}
                >
                    <Icon size={14} className={on ? 'text-indigo-600' : ''} /> {label}
                    <span className={`rounded-full px-1.5 py-0.5 text-[11px] font-bold tabular-nums ${
                        on ? 'bg-indigo-50 text-indigo-600' : 'bg-slate-200/70 text-slate-500'
                    }`}>
                        {totals?.[key] ?? 0}
                    </span>
                </button>
            );
        })}
    </div>
);


/* Шапка пространства в дереве — карточка, а не мелкая надпись: это самая
   заметная строка списка, по ней человек и находит свою ветку. Тот же приём,
   что в оглавлении на главной. */
const SpaceGroup = ({ title, closed, onToggle, children }) => (
    <div className="mb-1.5">
        <button
            type="button"
            aria-expanded={!closed}
            onClick={onToggle}
            className="flex w-full items-center gap-2 rounded-xl bg-white px-2.5 py-2 text-left shadow-sm ring-1 ring-slate-200/70 transition hover:ring-slate-300"
        >
            <Layers size={14} className="shrink-0 text-indigo-500" />
            <span className="min-w-0 flex-1 truncate text-[12.5px] font-bold tracking-[-0.01em] text-slate-900">
                {title}
            </span>
            <ChevronRight
                size={13}
                className={`shrink-0 text-slate-400 transition-transform ${closed ? '' : 'rotate-90'}`}
            />
        </button>
        {!closed && <div className="mt-0.5 pl-1.5">{children}</div>}
    </div>
);

/* ── Строка раздела ────────────────────────────────────────────────────────
 * Две мишени в одной строке, и это намеренно: раскрыть ветку и посмотреть её
 * статьи — разные желания. Стрелка складывает потомков, само название выбирает
 * раздел. Вложенные <button> невалидны, поэтому строка — это <div> с двумя
 * кнопками внутри, а не кнопка с кнопкой.
 */
const SectionRow = ({ section, depth, count, selected, open, hasChildren, onSelect, onToggle }) => (
    <div
        className={`flex items-center rounded-lg transition ${
            selected ? 'bg-indigo-50 ring-1 ring-indigo-200' : 'hover:bg-white hover:shadow-sm hover:ring-1 hover:ring-slate-200/70'
        }`}
        style={{ paddingLeft: `${4 + depth * 12}px` }}
    >
        {hasChildren ? (
            <button
                type="button"
                aria-expanded={open}
                aria-label={open ? 'Свернуть подразделы' : 'Развернуть подразделы'}
                onClick={onToggle}
                className="grid h-6 w-6 shrink-0 place-items-center rounded text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
            >
                <ChevronRight size={12} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
            </button>
        ) : (
            <span className="h-6 w-6 shrink-0" />
        )}

        <button
            type="button"
            onClick={onSelect}
            aria-current={selected ? 'true' : undefined}
            className="flex min-w-0 flex-1 items-center gap-1.5 py-1.5 pr-2 text-left"
        >
            {open && hasChildren
                ? <FolderOpen size={14} className="shrink-0 text-amber-500" />
                : <Folder size={14} className="shrink-0 text-amber-500" />}
            <span className={`min-w-0 flex-1 truncate text-[12.5px] ${
                selected ? 'font-bold text-indigo-900' : 'font-semibold text-slate-800'
            }`}>
                {section.name}
            </span>
            {/* Ноль показываем прочерком: «(0)» на каждой второй строке —
                это шум, а прочерк читается как «здесь пусто» с одного взгляда. */}
            <span className={`shrink-0 text-[11px] font-medium tabular-nums ${
                count ? (selected ? 'text-indigo-500' : 'text-slate-400') : 'text-slate-300'
            }`}>
                {count || '—'}
            </span>
        </button>
    </div>
);

/* ── Строка статьи в правой колонке ────────────────────────────────────────
   Автор и свежесть здесь не украшение: по ним понимают, можно ли документу
   верить, — а это первый вопрос при виде незнакомой статьи. */
const ArticleRow = ({ article, showStatus, onOpen }) => {
    const type = typeBadge(article.article_type);
    const ago = fmtAgo(article.updated_at);
    return (
        <button
            type="button"
            onClick={onOpen}
            className="flex w-full items-start gap-3 px-3 py-3 text-left transition hover:bg-slate-50"
        >
            <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-indigo-50 text-indigo-500">
                <FileText size={14} />
            </span>
            <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-[13.5px] font-semibold leading-snug text-slate-900">
                        {article.title}
                    </span>
                    {type && <IosBadge tone={type.tone}>{type.label}</IosBadge>}
                    {showStatus && article.status && (
                        <IosBadge tone={STATUS_TONES[article.status] || 'slate'}>
                            {STATUS_LABELS[article.status] || article.status}
                        </IosBadge>
                    )}
                </span>
                {article.summary && (
                    <span className="mt-0.5 block line-clamp-2 text-[12px] leading-relaxed text-slate-500">
                        {article.summary}
                    </span>
                )}
                <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10.5px] text-slate-400">
                    {article.author_name && (
                        <span className="inline-flex items-center gap-1">
                            <User size={10} /> {article.author_name}
                        </span>
                    )}
                    {ago && <span>{ago}</span>}
                    {article.views > 0 && (
                        <span className="inline-flex items-center gap-1 tabular-nums">
                            <Eye size={10} /> {article.views}
                        </span>
                    )}
                </span>
            </span>
            <ChevronRight size={14} className="mt-2 shrink-0 text-slate-300" />
        </button>
    );
};

/* Пустой экран с одной внятной фразой. */
const Blank = ({ icon: Icon, title, text, children }) => (
    <div className="flex flex-col items-center gap-2 px-6 py-16 text-center">
        <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
            <Icon size={22} />
        </div>
        <div className="text-[15px] font-semibold text-slate-900">{title}</div>
        <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">{text}</p>
        {children}
    </div>
);

export default function WikiCatalog({ base, headers, showToast, catalog, loading,
                                      bucket, onBucketChange, onOpenArticle }) {
    const [selected, setSelected] = useState(null);   // {id, name, path} либо null
    const [items, setItems] = useState(null);         // null = ещё не ответили
    const [busy, setBusy] = useState(false);
    const [filter, setFilter] = useState('');         // поиск внутри выбранного раздела
    const [query, setQuery] = useState('');           // поиск по дереву
    const [openSections, setOpenSections] = useState(() => new Set());
    const [closedSpaces, setClosedSpaces] = useState(() => new Set());
    const resultRef = useRef(null);

    const spaces = catalog?.spaces || [];
    const sections = catalog?.sections || [];
    const totals = catalog?.totals;
    const orphans = catalog?.orphans;
    const active = BUCKET_BY_KEY.get(bucket) || BUCKETS[0];
    const orphanCount = orphans?.[bucket] ?? 0;

    const countOf = useCallback((id) => {
        if (id === ORPHANS_ID) return orphanCount;
        const section = sections.find((s) => s.id === id);
        return section?.counts?.[bucket] ?? 0;
    }, [sections, bucket, orphanCount]);

    /* Путь до раздела — для шапки правой колонки: «СЗоВ › Супервайзер». В самом
       дереве он не нужен, там положение видно отступом. */
    const pathOf = useCallback((section) => {
        const byId = new Map(sections.map((s) => [s.id, s]));
        const space = spaces.find((sp) => sp.id === section.space_id);
        const chain = [];
        let parent = section.parent_section_id ? byId.get(section.parent_section_id) : null;
        let guard = 0;   // петель сервер не допускает, но зациклиться — подвесить вкладку
        while (parent && guard < 50) {
            chain.unshift(parent.name);
            parent = parent.parent_section_id ? byId.get(parent.parent_section_id) : null;
            guard += 1;
        }
        return [space?.name, ...chain].filter(Boolean).join(' › ');
    }, [sections, spaces]);

    /* Дерево: пространство → строки {section, depth} в порядке обхода.
       Плоский список со ступенью глубины, а не вложенные массивы: свёрнутую
       ветку так отсекают курсором глубины одним проходом — тот же приём, что в
       оглавлении на главной.

       Раздел, чей родитель не виден (закрыт правами), поднимается в корень:
       иначе доступная ветка потерялась бы под недоступной. */
    const tree = useMemo(() => {
        const shown = new Set(sections.map((s) => s.id));
        const childrenOf = new Map();
        sections.forEach((section) => {
            const parent = section.parent_section_id && shown.has(section.parent_section_id)
                ? section.parent_section_id
                : `root:${section.space_id}`;
            if (!childrenOf.has(parent)) childrenOf.set(parent, []);
            childrenOf.get(parent).push(section);
        });
        const walk = (key, depth, guard = 0) => (guard > 20 ? [] : (childrenOf.get(key) || [])
            .flatMap((section) => [
                { section, depth, hasChildren: (childrenOf.get(section.id) || []).length > 0 },
                ...walk(section.id, depth + 1, guard + 1),
            ]));
        return spaces
            .map((space) => ({ space, rows: walk(`root:${space.id}`, 0) }))
            .filter(({ rows }) => rows.length > 0);
    }, [spaces, sections]);

    const needle = query.trim().toLowerCase();

    /* Кого показывать при поиске. Раздел виден, если совпал сам ИЛИ совпал
       кто-то из его потомков — иначе найденный подраздел пропадал бы вместе
       с несовпавшим родителем. Строки идут в порядке обхода, поэтому считаем
       с конца: к моменту встречи родителя все потомки уже посчитаны. */
    const visibleSections = useMemo(() => {
        if (!needle) return null;
        const visible = new Set();
        tree.forEach(({ rows }) => {
            const childHit = [];
            for (let i = rows.length - 1; i >= 0; i -= 1) {
                const { section, depth } = rows[i];
                if (section.name.toLowerCase().includes(needle) || childHit[depth + 1]) {
                    visible.add(section.id);
                    childHit[depth] = true;
                }
                childHit[depth + 1] = false;
            }
        });
        return visible;
    }, [tree, needle]);

    // Во время поиска раскрываем всё, где есть совпадения: искать в свёрнутом
    // дереве бессмысленно.
    const isOpen = (id) => (needle ? visibleSections.has(id) : openSections.has(id));

    const loadArticles = useCallback((id) => {
        setBusy(true);
        setItems(null);
        axios.get(`${base}/articles`, { headers, params: { section_id: id, bucket, limit: 200 } })
            .then((r) => setItems(r.data?.items || []))
            .catch((e) => {
                setItems([]);
                showToast?.(errText(e, 'Не удалось загрузить статьи раздела'), 'error');
            })
            .finally(() => setBusy(false));
    }, [base, headers, bucket, showToast]);

    const selectSection = (section, path) => {
        setFilter('');
        setSelected({ id: section.id, name: section.name, path: path ?? pathOf(section) });
        // Раскрываем ветку выбранного: он мог быть выбран из свёрнутого родителя.
        if (section.id !== ORPHANS_ID) setOpenSections((prev) => new Set(prev).add(section.id));
        loadArticles(section.id);
        /* На узком экране колонки встают друг под другом, и результат оказывается
           ниже сгиба — нажатие выглядит как «ничего не произошло». На широком
           колонки рядом, и прокрутка была бы дёрганьем на ровном месте. */
        if (window.matchMedia('(max-width: 1023px)').matches) {
            window.setTimeout(
                () => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
        }
    };

    /* Переключили корзину — перезапрашиваем выбранный раздел. Иначе в шапке
       стояло бы «Черновики», а в списке лежали опубликованные. */
    useEffect(() => {
        if (selected) loadArticles(selected.id);
        // loadArticles уже зависит от bucket; selected в зависимостях дал бы
        // второй запрос поверх того, что делает selectSection.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [bucket]);

    /* Фильтр внутри раздела — по названию и описанию, на клиенте: список уже
       целиком здесь, ходить за подстрокой на сервер незачем. */
    const shown = useMemo(() => {
        const term = filter.trim().toLowerCase();
        if (!term || !items) return items;
        return items.filter(
            (a) => `${a.title} ${a.summary || ''}`.toLowerCase().includes(term));
    }, [items, filter]);

    if (loading && !catalog) {
        return (
            <div className="flex flex-col gap-3 lg:flex-row">
                <div className={`${iosCard} h-[420px] overflow-hidden lg:w-[320px] lg:shrink-0`}>
                    <div className="sk-shimmer h-full w-full" />
                </div>
                <div className={`${iosCard} h-[420px] flex-1 overflow-hidden`}>
                    <div className="sk-shimmer h-full w-full" />
                </div>
            </div>
        );
    }

    const nothingAtAll = sections.length === 0 && orphanCount === 0;
    const bucketEmpty = !nothingAtAll && (totals?.[bucket] ?? 0) === 0;

    /* Дерево рисуем по пространствам. Пустое после поиска пространство
       выбрасываем целиком — заголовок над пустотой ничего не сообщает. */
    const renderSpace = ({ space, rows }) => {
        const spaceClosed = !needle && closedSpaces.has(space.id);
        let hideDeeperThan = null;
        const body = [];

        rows.forEach(({ section, depth, hasChildren }) => {
            if (hideDeeperThan !== null && depth > hideDeeperThan) return;
            hideDeeperThan = null;
            if (needle && !visibleSections.has(section.id)) { hideDeeperThan = depth; return; }

            const open = isOpen(section.id);
            body.push(
                <SectionRow
                    key={section.id}
                    section={section}
                    depth={depth}
                    count={countOf(section.id)}
                    selected={selected?.id === section.id}
                    open={open}
                    hasChildren={hasChildren}
                    onSelect={() => selectSection(section)}
                    onToggle={() => setOpenSections((prev) => toggled(prev, section.id))}
                />,
            );
            if (!open) hideDeeperThan = depth;
        });

        if (body.length === 0) return null;
        return (
            <SpaceGroup
                key={space.id}
                title={space.name}
                closed={spaceClosed}
                onToggle={() => setClosedSpaces((prev) => toggled(prev, space.id))}
            >
                {body}
            </SpaceGroup>
        );
    };

    const renderedSpaces = tree.map(renderSpace).filter(Boolean);
    // «Без раздела» ищется наравне с настоящими разделами, иначе при активном
    // поиске он висел бы внизу как единственная не отфильтрованная строка.
    const showOrphans = orphanCount > 0 && (!needle || 'без раздела'.includes(needle));
    const treeEmpty = renderedSpaces.length === 0 && !showOrphans;

    return (
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
                {/* ── Левая колонка: дерево ──────────────────────────────── */}
                <aside className="lg:w-[320px] lg:shrink-0 2xl:w-[360px]">
                    <div className={`${iosCard} flex flex-col overflow-hidden lg:sticky lg:top-4 lg:max-h-[calc(100vh-2.5rem)]`}>
                        <div className="flex items-center justify-between gap-2 px-3 pb-1 pt-2.5">
                            <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                                <Layers size={12} /> Разделы
                            </span>
                            <span className="text-[10.5px] tabular-nums text-slate-400">
                                {sections.length} {sectionWord(sections.length)}
                            </span>
                        </div>

                        <div className="px-2.5 pb-2 pt-1">
                            <div className="flex items-center gap-2 rounded-lg bg-slate-100 px-2.5 py-1.5 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70">
                                <Search size={13} className="shrink-0 text-slate-400" />
                                <input
                                    value={query}
                                    onChange={(e) => setQuery(e.target.value)}
                                    placeholder="Найти раздел"
                                    className="wiki-focus-outside w-full min-w-0 bg-transparent text-[12px] text-slate-900 placeholder-slate-400 focus:outline-none"
                                />
                                {query && (
                                    <button
                                        type="button"
                                        onClick={() => setQuery('')}
                                        aria-label="Очистить"
                                        className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-slate-200 text-slate-500 transition hover:bg-slate-300"
                                    >
                                        <X size={9} />
                                    </button>
                                )}
                            </div>
                        </div>

                        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-slate-50/70 px-1.5 pb-2 pt-1.5">
                            {nothingAtAll && (
                                <p className="px-2 py-6 text-center text-[12px] leading-relaxed text-slate-400">
                                    Вам не открыт ни один раздел вики. Доступ выдаёт руководитель
                                    или супервайзер на вкладке «Структура».
                                </p>
                            )}

                            {!nothingAtAll && treeEmpty && (
                                <p className="px-2 py-6 text-center text-[12px] leading-relaxed text-slate-400">
                                    Ни одного раздела с «{query.trim()}» в названии.
                                </p>
                            )}

                            {renderedSpaces}

                            {/* Появляется, только когда бесхозные статьи есть:
                                пустая строка «Без раздела» — вопрос без ответа. */}
                            {showOrphans && (
                                <SpaceGroup title="Вне дерева" closed={false} onToggle={() => {}}>
                                    <SectionRow
                                        section={{ id: ORPHANS_ID, name: 'Без раздела' }}
                                        depth={0}
                                        count={orphanCount}
                                        selected={selected?.id === ORPHANS_ID}
                                        open={false}
                                        hasChildren={false}
                                        onSelect={() => selectSection(
                                            { id: ORPHANS_ID, name: 'Без раздела' },
                                            'Не привязаны ни к одному разделу')}
                                        onToggle={() => {}}
                                    />
                                </SpaceGroup>
                            )}
                        </div>
                    </div>
                </aside>

                {/* ── Правая колонка: статьи выбранного раздела ──────────── */}
                <section ref={resultRef} className={`${iosCard} min-w-0 flex-1 overflow-hidden`}>
                    {/* Пока раздел не выбран — здесь объяснение экрана: подсказка
                        стоит ровно там, куда смотрит человек, впервые сюда
                        попавший, и уходит, как только становится не нужна. */}
                    {!selected && !bucketEmpty && (
                        <Blank
                            icon={MousePointerClick}
                            title="Выберите раздел слева"
                            text="Здесь появятся его статьи. Слева — все разделы, к которым у вас есть доступ; цифра рядом с названием показывает, сколько в нём статей."
                        />
                    )}

                    {/* Пустая корзина — сообщение вместо списка нулей: «Архив 0»
                        и под ним дерево прочерков читается как поломка. */}
                    {!selected && bucketEmpty && (
                        <Blank icon={active.icon} title={`${active.label}: пусто`} text={active.nothing} />
                    )}

                    {selected && (
                        <>
                            <div className="border-b border-slate-200/70 bg-white/80 px-4 py-3 backdrop-blur-xl">
                                <div className="flex flex-wrap items-start justify-between gap-2">
                                    <div className="min-w-0">
                                        {selected.path && (
                                            <div className="truncate text-[11px] text-slate-400">
                                                {selected.path}
                                            </div>
                                        )}
                                        <h3 className="truncate text-[15px] font-bold tracking-[-0.01em] text-slate-900">
                                            {selected.name}
                                        </h3>
                                    </div>
                                    {!busy && items && (
                                        <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-[11.5px] font-medium text-slate-600">
                                            <active.icon size={12} className="text-indigo-500" />
                                            {active.label}: {items.length} {articleWord(items.length)}
                                        </span>
                                    )}
                                </div>

                                {/* Поле фильтра — только когда список длинный: над
                                    пятью строками оно занимает место, ничего не решая. */}
                                {!busy && items && items.length > 5 && (
                                    <div className="mt-2 flex items-center gap-2 rounded-lg bg-slate-100 px-2.5 py-1.5 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70">
                                        <Search size={13} className="shrink-0 text-slate-400" />
                                        <input
                                            value={filter}
                                            onChange={(e) => setFilter(e.target.value)}
                                            placeholder="Фильтр по названию статьи"
                                            className="wiki-focus-outside w-full min-w-0 bg-transparent text-[12px] text-slate-900 placeholder-slate-400 focus:outline-none"
                                        />
                                    </div>
                                )}
                            </div>

                            {busy && (
                                <div className="flex items-center justify-center gap-2 py-16 text-slate-400">
                                    <Loader2 size={16} className="animate-spin" />
                                    <span className="text-[13px]">Загружаем…</span>
                                </div>
                            )}

                            {!busy && items && items.length === 0 && (
                                <Blank icon={active.icon} title="Пусто" text={active.emptyHere} />
                            )}

                            {!busy && shown && shown.length === 0 && items.length > 0 && (
                                <div className="px-4 py-12 text-center text-[13px] text-slate-500">
                                    Ничего не найдено по запросу «{filter.trim()}».
                                </div>
                            )}

                            {!busy && shown && shown.length > 0 && (
                                <div className="divide-y divide-slate-100">
                                    {shown.map((article) => (
                                        <ArticleRow
                                            key={article.id}
                                            article={article}
                                            showStatus={bucket !== 'published'}
                                            onOpen={() => onOpenArticle(article.slug)}
                                        />
                                    ))}
                                </div>
                            )}
                        </>
                    )}
                </section>
        </div>
    );
}
