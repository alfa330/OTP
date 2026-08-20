import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Archive, ChevronRight, Eye, FileText, FolderOpen, Layers, Loader2,
    PenLine, Search, User, X,
} from 'lucide-react';
import { iosCard, IosBadge, IosModal } from '../ui/ios';
import { typeBadge } from './articleTypes';

/* Вкладка «Статьи» — каталог: что вообще лежит в базе знаний, по разделам.
 *
 * Отличие от главной. Главная отвечает на вопрос «что почитать МНЕ»: избранное,
 * недавнее, популярное. Каталог отвечает на другой — «что вообще есть в разделе
 * N и сколько там всего». Это разные вопросы, и мешать их в одном экране нельзя.
 *
 * ── Почему экран устроен именно так ────────────────────────────────────────
 *
 * ВЛОЖЕННОСТЬ ПОКАЗАНА ВЛОЖЕННОСТЬЮ. Первая версия раскладывала все разделы
 * плоской сеткой, а родителя подписывала мелким капсом над названием. Читалось
 * это неверно: «Регламенты» и «Оператор» стояли рядом как равные, хотя первый
 * лежит ВНУТРИ второго, а подпись никто не замечал. Теперь подраздел — строка
 * внутри карточки своего родителя, и структура видна без чтения.
 *
 * ПРОСТРАНСТВО — ЗАМЕТНАЯ СТРОКА, а не серый капс над сеткой. Внутри
 * пространства имена разделов повторяются намеренно («Супервайзер» есть и у
 * СЗоВ, и у ОП), и если заголовок теряется, две одинаковые карточки
 * неразличимы.
 *
 * ЭКРАН ОБЪЯСНЯЕТ СЕБЯ. Сетка одинаковых папок без единого слова — это ребус.
 * Сверху стоит строка о том, что здесь лежит и что делает переключатель.
 *
 * ПУСТАЯ КОРЗИНА — ОТДЕЛЬНЫЙ ЭКРАН, а не сетка нулей. «Архив 0» и под ним
 * двадцать карточек с «нет статей» выглядит как поломка, хотя всё в порядке:
 * архива просто нет.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Ключи совпадают с ARTICLE_BUCKETS на сервере: ключ уходит в ?bucket= и по нему
   же приходят counts. Подписи, иконки и пояснения — только здесь. */
const BUCKETS = [
    { key: 'published', label: 'Статьи', icon: FileText,
      hint: 'Опубликованные — их видят все, кому открыт раздел',
      nothing: 'В доступных вам разделах пока нет опубликованных статей.',
      emptyHere: 'В этом разделе нет опубликованных статей.' },
    { key: 'draft', label: 'Черновики', icon: PenLine,
      hint: 'Ещё не выпущены: черновики и статьи на согласовании',
      nothing: 'Незаконченных статей нет — всё, что начато, уже опубликовано.',
      emptyHere: 'В этом разделе нет черновиков и статей на согласовании.' },
    { key: 'archived', label: 'Архив', icon: Archive,
      hint: 'Убраны из базы знаний, но не удалены — их можно вернуть',
      nothing: 'В архиве пусто — ни одну статью ещё не убирали.',
      emptyHere: 'В этом разделе нет архивных статей.' },
];

const BUCKET_BY_KEY = new Map(BUCKETS.map((b) => [b.key, b]));

// Синтетический раздел для статей, не привязанных ни к одной ветке. Та же
// подпись, что в оглавлении на главной (WikiIndexPanel) — это одно и то же.
const ORPHANS_ID = 'none';

/* Цвет пространства. Смысла в конкретном цвете нет — важно, что у СЗоВ и у ОП
   он РАЗНЫЙ: это единственная подсказка «вы смотрите на другую ветку», которую
   видно боковым зрением при прокрутке. */
const SPACE_TONES = [
    { chip: 'bg-indigo-600', soft: 'bg-indigo-50 text-indigo-600', rule: 'bg-indigo-100' },
    { chip: 'bg-emerald-600', soft: 'bg-emerald-50 text-emerald-600', rule: 'bg-emerald-100' },
    { chip: 'bg-orange-500', soft: 'bg-orange-50 text-orange-600', rule: 'bg-orange-100' },
    { chip: 'bg-pink-600', soft: 'bg-pink-50 text-pink-600', rule: 'bg-pink-100' },
    { chip: 'bg-sky-600', soft: 'bg-sky-50 text-sky-600', rule: 'bg-sky-100' },
];

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
const subsectionWord = (n) => plural(n, 'подраздел', 'подраздела', 'подразделов');

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

/* ── Переключатель корзин ──────────────────────────────────────────────────
   Счётчик стоит прямо на кнопке: он и есть ответ на вопрос «а есть ли там
   вообще что-нибудь», ради которого иначе пришлось бы переключиться и
   посмотреть. Под кнопками — строка о том, что означает выбранная. */
const BucketSwitch = ({ value, onChange, totals }) => {
    const active = BUCKET_BY_KEY.get(value) || BUCKETS[0];
    return (
        <div>
            <div className="inline-flex max-w-full gap-1 overflow-x-auto rounded-2xl bg-slate-100 p-1">
                {BUCKETS.map(({ key, label, icon: Icon }) => {
                    const on = value === key;
                    const count = totals?.[key] ?? 0;
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
                                {count}
                            </span>
                        </button>
                    );
                })}
            </div>
            <p className="mt-1.5 px-1 text-[11.5px] text-slate-500">{active.hint}</p>
        </div>
    );
};

/* ── Строка подраздела внутри карточки родителя ────────────────────────────
   Именно СТРОКА, а не вторая плитка: подраздел принадлежит родителю, и это
   должно быть видно раскладкой, а не подписью. */
const ChildRow = ({ name, count, onOpen }) => (
    <button
        type="button"
        onClick={onOpen}
        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition hover:bg-slate-50"
    >
        <span className="h-1 w-1 shrink-0 rounded-full bg-slate-300" />
        <span className={`min-w-0 flex-1 truncate text-[12.5px] ${
            count ? 'text-slate-700' : 'text-slate-400'
        }`}>
            {name}
        </span>
        <span className={`shrink-0 text-[11px] tabular-nums ${
            count ? 'text-slate-500' : 'text-slate-300'
        }`}>
            {count || '—'}
        </span>
        <ChevronRight size={12} className="shrink-0 text-slate-300" />
    </button>
);

/* ── Карточка раздела ──────────────────────────────────────────────────────
   Заголовок карточки открывает статьи САМОГО раздела; строки под ним —
   подразделы. Пустой раздел не прячем: сетка обязана стоять на месте при
   переключении корзины, иначе карточки скачут под курсором и человек открывает
   не то. Вместо этого он честно подписан «нет статей». */
const SectionCard = ({ section, count, subsections, tone, onOpen, onOpenChild }) => {
    const empty = count === 0;
    return (
        <div className={`${iosCard} flex flex-col overflow-hidden`}>
            <button
                type="button"
                onClick={onOpen}
                className="group flex items-start gap-3 p-3.5 text-left transition hover:bg-slate-50/70"
            >
                <span className={`mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl ${
                    empty ? 'bg-slate-100 text-slate-400' : tone.soft
                }`}>
                    <FolderOpen size={16} />
                </span>
                <span className="min-w-0 flex-1">
                    <span className="block text-[13.5px] font-semibold leading-snug tracking-[-0.01em] text-slate-900">
                        {section.name}
                    </span>
                    <span className={`mt-0.5 block text-[11.5px] tabular-nums ${
                        empty ? 'text-slate-400' : 'text-slate-500'
                    }`}>
                        {empty ? 'нет статей' : `${count} ${articleWord(count)}`}
                    </span>
                </span>
                <ChevronRight
                    size={15}
                    className="mt-1.5 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-400"
                />
            </button>

            {subsections.length > 0 && (
                <div className="border-t border-slate-100 px-2 pb-2 pt-1.5">
                    <div className="px-2 pb-1 text-[9.5px] font-bold uppercase tracking-[0.07em] text-slate-400">
                        {subsections.length} {subsectionWord(subsections.length)}
                    </div>
                    {subsections.map((child) => (
                        <ChildRow
                            key={child.section.id}
                            name={child.section.name}
                            count={child.count}
                            onOpen={() => onOpenChild(child.section)}
                        />
                    ))}
                </div>
            )}
        </div>
    );
};

/* ── Строка статьи в окне раздела ──────────────────────────────────────────
   Автор и свежесть здесь не украшение: по ним понимают, можно ли документу
   верить, — а это первый вопрос при виде незнакомой статьи. */
const ArticleRow = ({ article, showStatus, onOpen }) => {
    const type = typeBadge(article.article_type);
    const ago = fmtAgo(article.updated_at);
    return (
        <button
            type="button"
            onClick={onOpen}
            className="flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition hover:bg-white"
        >
            <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white text-indigo-500 ring-1 ring-slate-200/70">
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

/* Пустой экран с одной внятной фразой. Используется и когда пуста вся корзина,
   и когда ничего не нашлось по запросу. */
const Blank = ({ icon: Icon, title, text }) => (
    <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-16 text-center`}>
        <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
            <Icon size={22} />
        </div>
        <div className="text-[15px] font-semibold text-slate-900">{title}</div>
        <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">{text}</p>
    </div>
);

export default function WikiCatalog({ base, headers, showToast, catalog, loading,
                                      bucket, onBucketChange, onOpenArticle }) {
    // Открытый раздел: {id, name, path} либо null. Держим объект, а не id —
    // шапка окна рисуется до того, как придёт список статей.
    const [open, setOpen] = useState(null);
    const [items, setItems] = useState(null);   // null = ещё не ответили
    const [busy, setBusy] = useState(false);
    const [filter, setFilter] = useState('');   // поиск внутри окна
    const [query, setQuery] = useState('');     // поиск по разделам каталога

    const spaces = catalog?.spaces || [];
    const sections = catalog?.sections || [];
    const totals = catalog?.totals;
    const orphans = catalog?.orphans;
    const active = BUCKET_BY_KEY.get(bucket) || BUCKETS[0];
    const orphanCount = orphans?.[bucket] ?? 0;

    const countOf = useCallback(
        (section) => section.counts?.[bucket] ?? 0, [bucket],
    );

    /* Путь до раздела — для шапки окна. В сетке он больше не нужен: там
       вложенность показана вложенностью, а не подписью. */
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

    /* Дерево каталога: пространство → корневые разделы → их потомки.
     *
     * Потомки СХЛОПНУТЫ в один уровень намеренно. В базе вложенность
     * произвольной глубины, но рисовать её лесенкой внутри карточки — значит
     * получить отступы, которые на узкой колонке съедают название. Здесь важно
     * «что лежит внутри», а не «на каком именно этаже».
     *
     * Раздел, чей родитель не виден (закрыт правами), поднимается в корень:
     * иначе доступная ветка потерялась бы под недоступной. Тот же приём, что
     * в дереве на главной (WikiLibrary).
     */
    const groups = useMemo(() => {
        const shown = new Set(sections.map((s) => s.id));
        const childrenOf = new Map();
        const roots = [];
        sections.forEach((section) => {
            const parent = section.parent_section_id;
            if (parent && shown.has(parent)) {
                if (!childrenOf.has(parent)) childrenOf.set(parent, []);
                childrenOf.get(parent).push(section);
            } else {
                roots.push(section);
            }
        });

        const descendants = (id, guard = 0) => (guard > 20 ? [] : (childrenOf.get(id) || [])
            .flatMap((child) => [child, ...descendants(child.id, guard + 1)]));

        const needle = query.trim().toLowerCase();
        const matches = (section) => !needle || section.name.toLowerCase().includes(needle);

        return spaces
            .map((space, index) => ({
                space,
                tone: SPACE_TONES[index % SPACE_TONES.length],
                cards: roots
                    .filter((s) => s.space_id === space.id)
                    .map((root) => ({
                        section: root,
                        count: countOf(root),
                        subsections: descendants(root.id).map((child) => ({
                            section: child, count: countOf(child),
                        })),
                    }))
                    // Поиск оставляет карточку, если совпал сам раздел ИЛИ любой
                    // его потомок: иначе найденный подраздел негде было бы
                    // показать — он живёт внутри карточки родителя.
                    .filter(({ section, subsections }) => matches(section)
                        || subsections.some(({ section: child }) => matches(child))),
            }))
            .filter(({ cards }) => cards.length > 0);
    }, [spaces, sections, countOf, query]);

    const loadArticles = useCallback((section) => {
        setBusy(true);
        setItems(null);
        axios.get(`${base}/articles`, {
            headers,
            params: { section_id: section.id, bucket, limit: 200 },
        })
            .then((r) => setItems(r.data?.items || []))
            .catch((e) => {
                setItems([]);
                showToast?.(errText(e, 'Не удалось загрузить статьи раздела'), 'error');
            })
            .finally(() => setBusy(false));
    }, [base, headers, bucket, showToast]);

    const openSection = (section, path) => {
        setFilter('');
        setOpen({ ...section, path: path ?? pathOf(section) });
        loadArticles(section);
    };

    /* Переключили корзину при открытом окне — перезапрашиваем его же раздел.
       Иначе в шапке стояло бы «Черновики», а в списке лежали опубликованные. */
    useEffect(() => {
        if (open) loadArticles(open);
        // loadArticles уже зависит от bucket; open в зависимостях дал бы второй
        // запрос поверх того, что делает openSection.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [bucket]);

    // Esc закрывает окно: IosModal сам клавиши не слушает, а модалка без Esc
    // ловится мышью только по крестику и фону.
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (e) => { if (e.key === 'Escape') setOpen(null); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open]);

    /* Фильтр внутри окна — по названию и описанию, на клиенте: список раздела
       уже целиком здесь, ходить за подстрокой на сервер незачем. */
    const shown = useMemo(() => {
        const needle = filter.trim().toLowerCase();
        if (!needle || !items) return items;
        return items.filter(
            (a) => `${a.title} ${a.summary || ''}`.toLowerCase().includes(needle));
    }, [items, filter]);

    if (loading && !catalog) {
        return (
            <div className="space-y-3">
                <div className={`${iosCard} h-[92px] overflow-hidden`}>
                    <div className="sk-shimmer h-full w-full" />
                </div>
                <div className={`${iosCard} h-[320px] overflow-hidden`}>
                    <div className="sk-shimmer h-full w-full" />
                </div>
            </div>
        );
    }

    const nothingAtAll = sections.length === 0;
    const bucketEmpty = !nothingAtAll && (totals?.[bucket] ?? 0) === 0;

    return (
        <div className="space-y-4">
            {/* Шапка: что это за экран и поиск по разделам. Без неё человек
                попадает на сетку одинаковых папок и вынужден догадываться. */}
            <section className={`${iosCard} p-4 sm:p-5`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                        <h2 className="text-[17px] font-bold tracking-[-0.02em] text-slate-900">
                            Каталог по разделам
                        </h2>
                        <p className="mt-1 max-w-2xl text-[12.5px] leading-relaxed text-slate-500">
                            Все разделы, к которым у вас есть доступ, и сколько статей лежит
                            в каждом. Нажмите на раздел — откроется список его статей.
                        </p>
                    </div>
                    {!nothingAtAll && (
                        <div className="flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 text-[11.5px] font-medium text-slate-600">
                            <Layers size={13} className="text-slate-400" />
                            {sections.length} {sectionWord(sections.length)}
                        </div>
                    )}
                </div>

                {!nothingAtAll && (
                    <div className="mt-3.5 flex items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70">
                        <Search size={16} className="shrink-0 text-slate-400" />
                        <input
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Найти раздел по названию"
                            className="wiki-focus-outside w-full min-w-0 bg-transparent text-[13px] text-slate-900 placeholder-slate-400 focus:outline-none"
                        />
                        {query && (
                            <button
                                type="button"
                                onClick={() => setQuery('')}
                                aria-label="Очистить"
                                className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-slate-200 text-slate-500 transition hover:bg-slate-300"
                            >
                                <X size={11} />
                            </button>
                        )}
                    </div>
                )}
            </section>

            {!nothingAtAll && (
                <BucketSwitch value={bucket} onChange={onBucketChange} totals={totals} />
            )}

            {nothingAtAll && (
                <Blank
                    icon={Layers}
                    title="Разделов пока нет"
                    text="Вам не открыт ни один раздел вики. Доступ выдаёт руководитель или супервайзер на вкладке «Структура»."
                />
            )}

            {/* Пустая корзина — свой экран, а не сетка нулей: двадцать карточек
                с «нет статей» читаются как поломка, хотя всё в порядке. */}
            {bucketEmpty && (
                <Blank icon={active.icon} title={`${active.label}: пусто`} text={active.nothing} />
            )}

            {!nothingAtAll && !bucketEmpty && groups.length === 0 && orphanCount === 0 && (
                <Blank
                    icon={Search}
                    title="Ничего не нашлось"
                    text={`Среди ваших разделов нет ни одного с «${query.trim()}» в названии.`}
                />
            )}

            {!nothingAtAll && !bucketEmpty && groups.map(({ space, tone, cards }) => (
                <section key={space.id} className="space-y-2">
                    {/* Заметная строка, а не серый капс: внутри пространства
                        имена разделов повторяются, и потерянный заголовок
                        делает две одинаковые карточки неразличимыми. */}
                    <div className="flex items-center gap-2.5">
                        <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-lg text-white ${tone.chip}`}>
                            <Layers size={13} />
                        </span>
                        <h3 className="shrink-0 text-[13.5px] font-bold tracking-[-0.01em] text-slate-900">
                            {space.name}
                        </h3>
                        <span className={`h-px min-w-0 flex-1 ${tone.rule}`} />
                        <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
                            {cards.length} {sectionWord(cards.length)}
                        </span>
                    </div>

                    <div className="grid grid-cols-1 items-start gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
                        {cards.map((card) => (
                            <SectionCard
                                key={card.section.id}
                                section={card.section}
                                count={card.count}
                                subsections={card.subsections}
                                tone={tone}
                                onOpen={() => openSection(card.section)}
                                onOpenChild={(child) => openSection(child)}
                            />
                        ))}
                    </div>
                </section>
            ))}

            {/* Карточка появляется, только когда бесхозные статьи есть: пустая
                строка «Без раздела» — это вопрос без ответа. */}
            {!nothingAtAll && !bucketEmpty && orphanCount > 0 && (
                <section className="space-y-2">
                    <div className="flex items-center gap-2.5">
                        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-slate-400 text-white">
                            <Layers size={13} />
                        </span>
                        <h3 className="shrink-0 text-[13.5px] font-bold tracking-[-0.01em] text-slate-900">
                            Вне дерева
                        </h3>
                        <span className="h-px min-w-0 flex-1 bg-slate-200" />
                    </div>
                    <div className="grid grid-cols-1 items-start gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
                        <SectionCard
                            section={{ id: ORPHANS_ID, name: 'Без раздела' }}
                            count={orphanCount}
                            subsections={[]}
                            tone={SPACE_TONES[0]}
                            onOpen={() => openSection(
                                { id: ORPHANS_ID, name: 'Без раздела' },
                                'Не привязаны ни к одному разделу')}
                            onOpenChild={() => {}}
                        />
                    </div>
                </section>
            )}

            <IosModal
                open={!!open}
                onClose={() => setOpen(null)}
                title={open?.name || ''}
                subtitle={open?.path || ''}
                maxWidth="max-w-2xl"
            >
                {busy && (
                    <div className="flex items-center justify-center gap-2 py-14 text-slate-400">
                        <Loader2 size={16} className="animate-spin" />
                        <span className="text-[13px]">Загружаем…</span>
                    </div>
                )}

                {!busy && items && items.length > 0 && (
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                        <span className="inline-flex items-center gap-1.5 rounded-full bg-white px-2.5 py-1 text-[11.5px] font-medium text-slate-600 ring-1 ring-slate-200/70">
                            <active.icon size={12} className="text-indigo-500" />
                            {active.label}: {items.length} {articleWord(items.length)}
                        </span>
                        {/* Поле фильтра — только когда список длинный: над пятью
                            строками оно занимает место, ничего не решая. */}
                        {items.length > 5 && (
                            <div className="ml-auto flex min-w-[180px] flex-1 items-center gap-2 rounded-xl bg-slate-100 px-3 py-1.5 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70">
                                <Search size={14} className="shrink-0 text-slate-400" />
                                <input
                                    value={filter}
                                    onChange={(e) => setFilter(e.target.value)}
                                    placeholder="Фильтр по названию"
                                    className="wiki-focus-outside w-full min-w-0 bg-transparent text-[12.5px] text-slate-900 placeholder-slate-400 focus:outline-none"
                                />
                            </div>
                        )}
                    </div>
                )}

                {!busy && items && items.length === 0 && (
                    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
                        <div className="grid h-11 w-11 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                            <active.icon size={20} />
                        </div>
                        <div className="text-[14px] font-semibold text-slate-900">Пусто</div>
                        <p className="max-w-sm text-[12.5px] leading-relaxed text-slate-500">
                            {active.emptyHere}
                        </p>
                    </div>
                )}

                {!busy && shown && shown.length === 0 && items.length > 0 && (
                    <div className="px-3 py-10 text-center text-[13px] text-slate-500">
                        Ничего не найдено по запросу «{filter.trim()}».
                    </div>
                )}

                {!busy && shown && shown.length > 0 && (
                    <div className="-mx-1 divide-y divide-slate-200/70">
                        {shown.map((article) => (
                            <ArticleRow
                                key={article.id}
                                article={article}
                                showStatus={bucket !== 'published'}
                                onOpen={() => { setOpen(null); onOpenArticle(article.slug); }}
                            />
                        ))}
                    </div>
                )}
            </IosModal>
        </div>
    );
}
