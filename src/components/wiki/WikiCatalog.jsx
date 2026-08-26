import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Archive, ChevronRight, Eye, FileText, Folder, FolderOpen, Layers, Loader2,
    PenLine, Pencil, Search, User, X,
} from 'lucide-react';
import { iosCard, IosBadge, IosMenu } from '../ui/ios';
import { fetchArticleIndex } from './articleIndex';
import { STATUS_LABELS, STATUS_TONES, typeBadge } from './articleTypes';

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
 * СПРАВА НИКОГДА НЕ ПУСТО. Раньше, пока раздел не выбран, там стояло пояснение
 * «выберите раздел слева»: экран открывался вопросом, хотя ответ у него был.
 * Теперь по умолчанию справа лежат ВСЕ доступные статьи текущей корзины,
 * свежие сверху. С этого списка видно объём базы, находится статья, раздел
 * которой не помнят, и работают те же действия строки. Выбор раздела в дереве
 * СУЖАЕТ этот список, строка «Все статьи» над деревом возвращает его целиком.
 *
 * СПИСОК БЕРЁТСЯ СТРАНИЦАМИ. Потолок одного ответа /articles — 200 записей, а
 * черновиков на бою 235: одним запросом список молча обрезался бы, и «Черновики
 * 235» открывались бы двумя сотнями. Страницы добирает fetchArticleIndex — тот
 * же, что собирает оглавление на главной, и по той же причине.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Ключи совпадают с ARTICLE_BUCKETS на сервере: ключ уходит в ?bucket= и по нему
   же приходят counts. Подписи, иконки и пояснения — только здесь. */
const BUCKETS = [
    { key: 'published', label: 'Статьи', icon: FileText, all: 'Все статьи',
      nothing: 'В доступных вам разделах пока нет опубликованных статей.',
      emptyHere: 'В этом разделе нет опубликованных статей.' },
    { key: 'draft', label: 'Черновики', icon: PenLine, all: 'Все черновики',
      nothing: 'Незаконченных статей нет — всё, что начато, уже опубликовано.',
      emptyHere: 'В этом разделе нет черновиков и статей на согласовании.' },
    { key: 'archived', label: 'Архив', icon: Archive, all: 'Весь архив',
      nothing: 'В архиве пусто — ни одну статью ещё не убирали.',
      emptyHere: 'В этом разделе нет архивных статей.' },
];

const BUCKET_BY_KEY = new Map(BUCKETS.map((b) => [b.key, b]));

// Синтетический раздел для статей, не привязанных ни к одной ветке. Та же
// подпись, что в оглавлении на главной (WikiIndexPanel) — это одно и то же.
const ORPHANS_ID = 'none';

/* Выборка «без раздела вовсе» — весь периметр корзины. Не null и не пустая
   строка: выборка ходит тем же загрузчиком, что и раздел, и её имя попадает в
   ключ гонки — по пустому значению два запроса было бы не различить. */
const ALL_ID = 'all';

/* Подпись «где лежит статья» для списка «все статьи»: строки там пришли из
   разных веток, и без неё неоткуда узнать, куда идти за соседними.

   names — карта «id раздела → название», собранная из каталога. Разделы,
   которых в ней нет, отбрасываются молча, и это не небрежность: статья лежит и
   в закрытых правами ветках, и в соседней вике, а подписать её тем, чего
   человеку не показывают, значит рассказать о содержимом чужого раздела.

   Перечислять все ветки не пробуем: статья в трёх разделах — обычное дело, и
   строка распухла бы ровно там, где список читают глазами. Первая плюс счётчик
   отвечают на вопрос «где искать» и не мешают читать соседние строки. */
export const articleWhere = (article, names) => {
    const found = (article?.section_ids || [])
        .map((id) => names?.get(id))
        .filter(Boolean);
    if (found.length === 0) return 'Без раздела';
    return found.length > 1 ? `${found[0]} +${found.length - 1}` : found[0];
};

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

const toggled = (set, key) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
};

/* ── Переключатель корзин ──────────────────────────────────────────────────
   Счётчик стоит прямо на кнопке: он и есть ответ на вопрос «а есть ли там
   вообще что-нибудь», ради которого иначе пришлось бы переключиться и
   посмотреть. Пояснительной строки под кнопками нет по решению владельца:
   переключить корзину и увидеть её содержимое — одно движение, а вечная
   подпись сверху перестаёт читаться на второй день. */
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
 * Автор и свежесть здесь не украшение: по ним понимают, можно ли документу
 * верить, — а это первый вопрос при виде незнакомой статьи.
 *
 * Две мишени в одной строке, как у строки раздела слева: нажатие на название
 * открывает статью, «три точки» — распоряжаются ею. Раньше мишень была одна, и
 * каталог умел ровно одно действие: уйти в статью. Вложенные <button>
 * невалидны, поэтому строка — <div> с кнопкой и меню внутри, а не кнопка
 * с кнопкой.
 *
 * where — раздел статьи. Показываем ТОЛЬКО в списке «все статьи»: там строки
 * пришли из разных веток, и без этой подписи неоткуда узнать, где статья лежит.
 * В списке выбранного раздела ответ уже стоит в шапке колонки, и повторить его
 * на каждой строке значило бы засыпать список одним и тем же словом.
 */
const ArticleRow = ({ article, showStatus, onOpen, menu, busy, locked, where }) => {
    const type = typeBadge(article.article_type);
    const ago = fmtAgo(article.updated_at);
    return (
        <div className="flex items-start transition hover:bg-slate-50">
            <button
                type="button"
                onClick={onOpen}
                className="flex min-w-0 flex-1 items-start gap-3 px-3 py-3 text-left"
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
                        {where && (
                            <span className="inline-flex min-w-0 items-center gap-1">
                                <Folder size={10} className="shrink-0 text-amber-500" />
                                <span className="truncate">{where}</span>
                            </span>
                        )}
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
            {/* Меню держим на строке заголовка — на одной линии со стрелкой
                «открыть»: по центру высокой строки оно вставало ниже стрелки, и
                два действия на правом краю читались как разнобой.
                Пока идёт запрос — на месте меню спиннер: строка меняется здесь
                же, и общего индикатора у списка нет (перезапрос идёт тихо). */}
            <span className="mt-2.5 mr-1 grid h-8 w-8 shrink-0 place-items-center">
                {busy
                    ? <Loader2 size={14} className="animate-spin text-slate-400" />
                    : <IosMenu
                        items={menu}
                        /* Пока идёт действие над ДРУГОЙ строкой, меню погашено:
                           одновременно мы обрабатываем одно, и пункт, который
                           молча ничего не делает, — тот же отказ, только
                           неотличимый от поломки. Так же гасится меню разделов
                           на «Структуре». */
                        disabled={locked}
                        label={`Действия со статьёй «${article.title}»`}
                      />}
            </span>
        </div>
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
                                      bucket, onBucketChange, onOpenArticle,
                                      onEditArticle, reloadCatalog, space = null }) {
    const [selected, setSelected] = useState(null);   // {id, name, path} либо null
    const [items, setItems] = useState(null);         // null = ещё не ответили
    /* Ждём с самого начала: список «все статьи» экран грузит сам, не дожидаясь
       выбора. Начни busy с false — между первым кадром и первым эффектом
       колонка успела бы показать «Пусто» и тут же его убрать. */
    const [busy, setBusy] = useState(true);
    const [acting, setActing] = useState(null);       // id статьи, над которой работаем
    const [filter, setFilter] = useState('');         // поиск внутри показанного списка
    const [query, setQuery] = useState('');           // поиск по дереву
    const [openSections, setOpenSections] = useState(() => new Set());
    const [closedSpaces, setClosedSpaces] = useState(() => new Set());
    const resultRef = useRef(null);
    /* Выборка, ответ по которой ещё ждём: «пространство + раздел + корзина».
       Ответов бывает несколько в полёте — выбор раздела, тихое обновление после
       действия над строкой, смена корзины, — и без этой отметки поздний ответ по
       ПРЕЖНЕЙ выборке лёг бы под шапку новой. Корзина в ключе не для красоты:
       переключение корзины сразу после выбора раздела оставляет в полёте два
       запроса по ОДНОМУ разделу, и по одному id их не различить — в «Черновиках»
       оказались бы опубликованные. Пространство — по той же причине: у выборки
       «все статьи» имя одно на любую вику.

       Забирает ключ себе только ОТКРЫТИЕ выборки; тихое обновление после
       действия над строкой лишь сверяется с ним — см. loadArticles. */
    const wantedRef = useRef(null);

    const spaceId = space?.id || null;

    /* Каталог держим ещё и в ref. Загрузчику нужно ЧИСЛО статей выборки — по
       нему он считает, сколько страниц забирать, — а сам объект приходит новым
       на каждый ответ /catalog. Попади он в зависимости загрузчика, список
       перезапрашивался бы после каждого действия над строкой: действие обновляет
       счётчики корзин, счётчики меняют catalog, catalog менял бы загрузчик.
       Эффект объявлен раньше остальных и потому выполняется первым — к моменту
       загрузки в ref уже лежит свежий каталог. */
    const catalogRef = useRef(catalog);
    useEffect(() => { catalogRef.current = catalog; });

    /* Каталог отдаёт ВСЁ, к чему у человека есть доступ, — в том числе соседнее
       пространство у супер-админа. На экране одновременно живёт одно, выбранное
       переключателем в шапке: дерево каталога и оглавление на главной обязаны
       показывать одну и ту же вику, иначе счётчики разойдутся с содержимым. */
    const spaces = useMemo(() => {
        const all = catalog?.spaces || [];
        return space ? all.filter((sp) => sp.id === space.id) : all;
    }, [catalog, space]);
    const sections = useMemo(() => {
        const all = catalog?.sections || [];
        return space ? all.filter((x) => x.space_id === space.id) : all;
    }, [catalog, space]);
    const totals = catalog?.totals;
    const orphans = catalog?.orphans;
    const active = BUCKET_BY_KEY.get(bucket) || BUCKETS[0];
    const orphanCount = orphans?.[bucket] ?? 0;

    const countOf = useCallback((id) => {
        if (id === ORPHANS_ID) return orphanCount;
        const section = sections.find((s) => s.id === id);
        return section?.counts?.[bucket] ?? 0;
    }, [sections, bucket, orphanCount]);

    /* Имена разделов берём из уже пришедшего каталога, а не отдельным запросом:
       дерево слева нарисовано ими же. */
    const sectionNames = useMemo(
        () => new Map(sections.map((x) => [x.id, x.name])), [sections]);

    const whereOf = useCallback(
        (article) => articleWhere(article, sectionNames), [sectionNames]);

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

    /* ── Загрузка списка ───────────────────────────────────────────────────
     * Один загрузчик на обе выборки — раздел и «все статьи». Список, фильтр,
     * меню строки, тихое обновление после действия и защита от поздних ответов
     * у них общие, и вторая копия этой цепочки разошлась бы с первой на первой
     * же правке.
     *
     * СТРАНИЦАМИ, А НЕ ОДНИМ ЗАПРОСОМ. Потолок ответа /articles — 200 записей,
     * и раньше загрузчик просил ровно его, одним запросом. Это молча обрезало бы
     * и «все черновики» (их на бою 235), и раздел, доросший до трёх сотен:
     * список показывал бы двести, а счётчик рядом — правду. Ровно этот дефект
     * уже чинили на главной, поэтому страницы добирает тот же fetchArticleIndex.
     *
     * Сколько страниц забирать, знает КАТАЛОГ, а не список: total_visible из
     * ответа — размер всего периметра, он считается ДО фильтра по корзине и
     * завысил бы число страниц (см. articleIndex.js). Каталог же назвал точное
     * число статей этой выборки — то самое, что стоит на кнопке корзины и в
     * дереве. Не пришёл каталог — 0, и fetchArticleIndex доберёт страницы
     * цепочкой: медленнее, но без молчаливого обрыва.
     *
     * quiet — обновление списка ПОСЛЕ действия над строкой: список остаётся на
     * экране, пока сервер не ответит. Гасить всю правую колонку спиннером ради
     * смены статуса одной строки значило бы мигать экраном на ровном месте.
     */
    const knownCount = useCallback((id) => {
        const data = catalogRef.current;
        if (!data) return 0;
        if (id === ALL_ID) return data.totals?.[bucket] ?? 0;
        if (id === ORPHANS_ID) return data.orphans?.[bucket] ?? 0;
        return (data.sections || []).find((x) => x.id === id)?.counts?.[bucket] ?? 0;
    }, [bucket]);

    const loadArticles = useCallback((id, { quiet = false } = {}) => {
        const wanted = `${spaceId}:${id}:${bucket}`;
        /* Ключ гонки забирает себе только ОТКРЫТИЕ выборки. Тихое обновление
           догоняет уже показанный список и на роль текущей выборки не
           претендует: заберёт ключ — и ответ по разделу, который человек
           выбрал секунду назад, окажется «чужим». Список остался бы прежним,
           а спиннер над ним не погас бы вовсе. Себя же тихое обновление
           проверяет тем же ключом: сменилась выборка, пока летел ответ, —
           значит он опоздал и в список не идёт. */
        if (!quiet) { wantedRef.current = wanted; setBusy(true); setItems(null); }
        const mine = () => wantedRef.current === wanted;

        const total = knownCount(id);
        fetchArticleIndex((offset, limit) => axios
            .get(`${base}/articles`, {
                headers,
                params: {
                    // «Все статьи» — просто отсутствие фильтра по разделу.
                    ...(id === ALL_ID ? {} : { section_id: id }),
                    // Пространство обязательно: без него выборка без раздела
                    // собралась бы по всем викам сразу, а на экране живёт одна —
                    // та же, по которой каталог посчитал числа рядом.
                    bucket, limit, offset, space_id: spaceId,
                },
            })
            .then((r) => ({ items: r.data?.items || [], total })))
            .then((list) => { if (mine()) setItems(list); })
            .catch((e) => {
                if (!quiet && mine()) setItems([]);
                showToast?.(errText(e, 'Не удалось загрузить статьи'), 'error');
            })
            .finally(() => { if (!quiet && mine()) setBusy(false); });
    }, [base, headers, bucket, spaceId, knownCount, showToast]);

    /* ── Действия над статьёй прямо из списка ──────────────────────────────
     * Раньше каталог умел одно: уйти в статью. Чтобы снять статью с публикации
     * или убрать в архив, приходилось открыть её, найти кнопку в шапке и
     * вернуться назад — при разборе раздела на десяток статей это десяток
     * кругов.
     *
     * Статус после действия НЕ подставляем на клиенте: корзину статьи считает
     * сервер (ARTICLE_BUCKETS), и вторая копия этих правил здесь разошлась бы с
     * первой. Вместо этого тихо перезапрашиваем список и счётчики корзин —
     * статья, ушедшая из открытой корзины, исчезает из него сама.
     */
    const act = (article, send, done, fail) => {
        if (acting) return;
        setActing(article.id);
        send()
            .then(() => {
                showToast?.(done, 'success');
                /* Перезапрашиваем ТУ выборку, что сейчас на экране, — в том
                   числе «все статьи». Раньше здесь стояло `if (selected)`, и
                   это было верно, пока строки статей жили только у выбранного
                   раздела. Теперь основная выборка экрана — вся корзина, а в
                   ней selected === null: условие молча отключало обновление
                   ровно там, где список лежит чаще всего, и заархивированная
                   статья оставалась в списке, споря со счётчиком рядом. */
                loadArticles(selected ? selected.id : ALL_ID, { quiet: true });
                // Числа на переключателе корзин меняются тем же действием:
                // сняли статью с публикации — «Черновиков» обязано стать
                // больше сразу, а не при следующем заходе в раздел.
                reloadCatalog?.();
            })
            .catch((e) => showToast?.(errText(e, fail), 'error'))
            .finally(() => setActing(null));
    };

    const toDraft = (article) => act(
        article,
        () => axios.patch(`${base}/articles/${article.id}`,
                          // Комментарий уезжает в историю версий: снятие с
                          // публикации — то самое событие, о котором потом
                          // спрашивают «кто и зачем».
                          { status: 'draft', comment: 'Возврат в черновики из каталога' },
                          { headers }),
        'Статья вернулась в черновики', 'Не удалось отправить в черновик');

    const archive = (article) => {
        /* Подтверждение обязательно, и тот же вопрос задаёт кнопка «В архив» на
           самой статье (WikiArticle): одно действие — один разговор с
           человеком. */
        if (!window.confirm(`Убрать статью «${article.title}» в архив?

`
            + 'Она пропадёт из списков и из ответов помощника. '
            + 'Восстановить сможет администратор.')) return;
        act(article, () => axios.delete(`${base}/articles/${article.id}`, { headers }),
            'Статья убрана в архив', 'Не удалось убрать в архив');
    };

    /* Пункты меню строки. Право берём из ответа сервера (permissions), а не из
       роли: у статьи есть свои правила доступа, и роль их не описывает —
       предложить «Редактировать» на статье, которую сервер откажется править,
       значит соврать. Не пришли права вовсе (старый бандл против нового
       сервера) — меню просто нет: IosMenu без пунктов не рисует и кнопку. */
    const menuFor = (article) => {
        const rights = article.permissions || {};
        const canDraft = !!rights.can_edit && article.status !== 'draft';
        return [
            onEditArticle && rights.can_edit && {
                key: 'edit', label: 'Редактировать', icon: Pencil,
                onSelect: () => onEditArticle(article) },
            /* Черновику этот пункт не нужен — он уже черновик. Зато нужен
               АРХИВНОЙ статье: из архива иначе нет пути назад. */
            canDraft && {
                key: 'draft', label: 'Отправить в черновик', icon: PenLine,
                separatorBefore: true, onSelect: () => toDraft(article) },
            rights.can_delete && article.status !== 'archived' && {
                key: 'archive', label: 'Убрать в архив', icon: Archive,
                danger: true, separatorBefore: !canDraft,
                onSelect: () => archive(article) },
        ];
    };

    /* На узком экране колонки встают друг под другом, и результат оказывается
       ниже сгиба — нажатие выглядит как «ничего не произошло». На широком
       колонки рядом, и прокрутка была бы дёрганьем на ровном месте. */
    const revealResult = () => {
        if (!window.matchMedia('(max-width: 1023px)').matches) return;
        window.setTimeout(
            () => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
    };

    const selectSection = (section, path) => {
        setFilter('');
        setSelected({ id: section.id, name: section.name, path: path ?? pathOf(section) });
        // Раскрываем ветку выбранного: он мог быть выбран из свёрнутого родителя.
        if (section.id !== ORPHANS_ID) setOpenSections((prev) => new Set(prev).add(section.id));
        loadArticles(section.id);
        revealResult();
    };

    /* Назад ко всему содержимому корзины. Без этой дороги выбор раздела был бы
       билетом в один конец: список, с которого экран открылся, вернуть было бы
       нечем — снять выделение в дереве нажатием на ту же строку нельзя, это
       ровно то поведение, которое люди принимают за поломку. */
    const selectAll = () => {
        setFilter('');
        setSelected(null);
        loadArticles(ALL_ID);
        revealResult();
    };

    /* Первая загрузка, смена корзины и смена пространства.
     *
     * КОРЗИНА. Переключили — перезапрашиваем показанную выборку. Иначе в шапке
     * стояло бы «Черновики», а в списке лежали опубликованные.
     *
     * ПРОСТРАНСТВО. Смена вики в шапке — смена всего экрана: раздел, выбранный
     * в прежней, в дереве больше не существует, а список за ним так и висел бы
     * справа. Возвращаемся ко «всем статьям» уже новой вики.
     *
     * ФИЛЬТР. Сбрасываем вместе с выборкой: слово, набранное в «Черновиках»,
     * молча спрятало бы половину «Архива», а поле стоит выше списка и замечают
     * его не сразу.
     *
     * ЖДЁМ КАТАЛОГ. Пока он не пришёл, спрашивать нечего: число страниц
     * загрузчик берёт именно из него, а busy и без того true с первого кадра.
     */
    const hasCatalog = !!catalog;
    const spaceSeenRef = useRef(spaceId);
    useEffect(() => {
        if (!hasCatalog) return;
        const sameSpace = spaceSeenRef.current === spaceId;
        spaceSeenRef.current = spaceId;
        const next = sameSpace && selected ? selected.id : ALL_ID;
        if (!sameSpace) setSelected(null);
        setFilter('');
        loadArticles(next);
        // selected читаем, но в зависимости не берём: его смену обрабатывает
        // selectSection, и здесь она дала бы второй запрос поверх первого.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [bucket, spaceId, hasCatalog]);

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
    const bucketTotal = totals?.[bucket] ?? 0;

    /* Каталог не ответил, и ждать его больше нечего. Без него список не
       спросить — загрузчик берёт из каталога число страниц, — поэтому спиннер
       крутился бы вечно: он ждёт того, чего уже не будет. Говорим об этом
       прямо. «Пусто» здесь сказать нельзя: мы не знаем, что там. */
    const catalogLost = !catalog && !loading;

    // Раздел не выбран — справа лежит вся корзина целиком.
    const viewingAll = !selected;
    const filtering = filter.trim().length > 0;

    /* Число в шапке правой колонки. С включённым фильтром — «7 из 235», а не
       «235»: показано семь строк, и цифра, называющая другое, читается как
       потерянные статьи. Название корзины в списке «все статьи» не повторяем —
       оно уже стоит заголовком колонки. */
    let countLabel = '';
    if (items) {
        if (filtering) countLabel = `${shown.length} из ${items.length}`;
        else {
            const n = `${items.length} ${articleWord(items.length)}`;
            countLabel = viewingAll ? n : `${active.label}: ${n}`;
        }
    }

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

                        {/* Возврат ко всей корзине. Стоит НАД прокруткой, а не
                            первой строкой дерева: это не раздел, а выход из
                            выбора, и уехать под сгиб длинного дерева он не
                            должен — искать дорогу назад прокруткой не станут.
                            Название меняется вместе с корзиной: «Все статьи»,
                            «Все черновики», «Весь архив» — так строка сама
                            говорит, что именно вернётся. */}
                        <div className="px-2.5 pb-2">
                            <button
                                type="button"
                                onClick={selectAll}
                                aria-current={viewingAll ? 'true' : undefined}
                                className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition ${
                                    viewingAll
                                        ? 'bg-indigo-50 ring-1 ring-indigo-200'
                                        : 'hover:bg-slate-100'
                                }`}
                            >
                                <active.icon
                                    size={14}
                                    className={`shrink-0 ${viewingAll ? 'text-indigo-600' : 'text-slate-400'}`}
                                />
                                <span className={`min-w-0 flex-1 truncate text-[12.5px] ${
                                    viewingAll ? 'font-bold text-indigo-900' : 'font-semibold text-slate-700'
                                }`}>
                                    {active.all}
                                </span>
                                {/* Ноль — прочерком, как и в строках разделов. */}
                                <span className={`shrink-0 text-[11px] font-medium tabular-nums ${
                                    bucketTotal
                                        ? (viewingAll ? 'text-indigo-500' : 'text-slate-400')
                                        : 'text-slate-300'
                                }`}>
                                    {bucketTotal || '—'}
                                </span>
                            </button>
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

                {/* ── Правая колонка: статьи выборки ─────────────────────── */}
                <section ref={resultRef} className={`${iosCard} min-w-0 flex-1 overflow-hidden`}>
                    {/* Шапка стоит всегда, а не только у выбранного раздела: с
                        ней колонка отвечает, ЧТО именно сейчас в списке, — и
                        когда это раздел, и когда вся корзина. */}
                    <div className="border-b border-slate-200/70 bg-white/80 px-4 py-3 backdrop-blur-xl">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                            <div className="min-w-0">
                                {/* Надстрочник у раздела — его путь, у полного
                                    списка — граница выборки. Пояснение, ради
                                    которого раньше пустовала целая колонка,
                                    умещается в одну эту строку. */}
                                <div className="truncate text-[11px] text-slate-400">
                                    {viewingAll
                                        ? 'Все разделы, к которым у вас есть доступ'
                                        : selected.path}
                                </div>
                                <h3 className="truncate text-[15px] font-bold tracking-[-0.01em] text-slate-900">
                                    {viewingAll ? active.all : selected.name}
                                </h3>
                            </div>
                            {!busy && items && (
                                <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-[11.5px] font-medium text-slate-600">
                                    <active.icon size={12} className="text-indigo-500" />
                                    {countLabel}
                                </span>
                            )}
                        </div>

                        {/* Поле фильтра — только когда список длинный: над
                            пятью строками оно занимает место, ничего не решая.
                            В полном списке это порог, который проходят всегда, —
                            там фильтр и есть главный способ найти статью, когда
                            раздел её не помнят. */}
                        {!busy && items && items.length > 5 && (
                            <div className="mt-2 flex items-center gap-2 rounded-lg bg-slate-100 px-2.5 py-1.5 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70">
                                <Search size={13} className="shrink-0 text-slate-400" />
                                <input
                                    value={filter}
                                    onChange={(e) => setFilter(e.target.value)}
                                    placeholder="Фильтр по названию статьи"
                                    className="wiki-focus-outside w-full min-w-0 bg-transparent text-[12px] text-slate-900 placeholder-slate-400 focus:outline-none"
                                />
                                {filtering && (
                                    <button
                                        type="button"
                                        onClick={() => setFilter('')}
                                        aria-label="Очистить фильтр"
                                        className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-slate-200 text-slate-500 transition hover:bg-slate-300"
                                    >
                                        <X size={9} />
                                    </button>
                                )}
                            </div>
                        )}
                    </div>

                    {catalogLost && (
                        <Blank
                            icon={active.icon}
                            title="Список не загрузился"
                            text="Каталог вики не ответил, и без него не собрать список статей. Обновите страницу — если не поможет, напишите в IT."
                        />
                    )}

                    {!catalogLost && busy && (
                        <div className="flex items-center justify-center gap-2 py-16 text-slate-400">
                            <Loader2 size={16} className="animate-spin" />
                            <span className="text-[13px]">Загружаем…</span>
                        </div>
                    )}

                    {/* Пустая корзина и пустой раздел — разные новости, и
                        фраза у каждой своя: «в архиве пусто» про всю вику
                        нечего говорить, стоя в разделе, и наоборот. */}
                    {!busy && items && items.length === 0 && (
                        <Blank
                            icon={active.icon}
                            title={viewingAll ? `${active.label}: пусто` : 'Пусто'}
                            text={viewingAll ? active.nothing : active.emptyHere}
                        />
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
                                    /* Раздел подписываем только в полном
                                       списке: в списке раздела он уже в шапке. */
                                    where={viewingAll ? whereOf(article) : null}
                                    onOpen={() => onOpenArticle(article.slug)}
                                    menu={menuFor(article)}
                                    busy={acting === article.id}
                                    locked={acting !== null && acting !== article.id}
                                />
                            ))}
                        </div>
                    )}
                </section>
        </div>
    );
}
