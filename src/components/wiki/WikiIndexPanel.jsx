import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, FileText, Folder, FolderOpen, Layers, Loader2, Search } from 'lucide-react';
import { iosCard } from '../ui/ios';
import { getScrollContainer } from './scrollContainer';

/* Правая колонка витрины — оглавление раздела: дерево «отдел → раздел → статьи».
 *
 * Статьи ЛЕЖАТ ВНУТРИ своих разделов и раскрываются по нажатию, как в исходной
 * вике. Плоский список всех статей под деревом, который был здесь сначала, на
 * реальном содержимом (36 статей) превращал панель в бесконечную ленту и рвал
 * связь статьи с разделом: человек видел «Оператор (14)», а ниже — те же
 * четырнадцать вперемешку с чужими.
 *
 * По умолчанию раскрыты только отделы, разделы свёрнуты: свёрнутое дерево
 * целиком помещается в панель, и прокручивать ничего не нужно.
 *
 * Поиск здесь — фильтр по оглавлению (по названиям, на клиенте), в отличие от
 * полнотекстового поиска в центре: тот ищет ПО ТЕКСТУ статей и отвечает
 * карточками со сниппетами. Пока они делают разное, два поля рядом оправданы.
 */

const DESKTOP = '(min-width: 1024px)';
const GAP = 16;             // тот же отступ, что и sticky top-4
const MIN_HEIGHT = 240;     // ниже этого панель бесполезна, лучше дать ей вылезти

/* Панель обязана помещаться в экран, а не быть высотой 100vh.
 *
 * Липкая панель начинается НИЖЕ шапки раздела и вкладок, поэтому max-height в
 * 100vh уводил её низ за нижний край окна: у человека появлялись две полосы
 * прокрутки сразу — своя у панели и общая у страницы, и чтобы добраться до дна
 * списка, приходилось сперва прокрутить сайт. Считаем высоту от фактического
 * расстояния до низа окна.
 *
 * Стиль пишем прямо в DOM, без useState: пересчёт идёт на каждый кадр прокрутки,
 * а перерисовывать из-за него всё дерево панели незачем.
 */
function useFitToViewport(ref) {
    useEffect(() => {
        const el = ref.current;
        if (!el) return undefined;

        const desktop = window.matchMedia(DESKTOP);
        const apply = () => {
            if (!desktop.matches) { el.style.maxHeight = ''; return; }
            const top = el.getBoundingClientRect().top;
            el.style.maxHeight = `${Math.max(MIN_HEIGHT, window.innerHeight - top - GAP)}px`;
        };

        apply();
        // Прокручивается .main-content портала; window — только запасной путь,
        // если раздел когда-нибудь окажется вне этого каркаса.
        const scroller = getScrollContainer(el) || window;
        scroller.addEventListener('scroll', apply, { passive: true });
        window.addEventListener('resize', apply);
        desktop.addEventListener('change', apply);
        return () => {
            scroller.removeEventListener('scroll', apply);
            window.removeEventListener('resize', apply);
            desktop.removeEventListener('change', apply);
        };
    }, [ref]);
}

const rowBase = 'flex w-full items-center gap-1.5 rounded-lg py-1.5 pr-2.5 text-left text-[12.5px] transition';

const toggled = (set, key) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
};

export default function WikiIndexPanel({
    tree, sectionId, onSection, articles, onOpen, loading,
}) {
    const [filter, setFilter] = useState('');
    const [openSections, setOpenSections] = useState(() => new Set());
    const [closedSpaces, setClosedSpaces] = useState(() => new Set());

    const needle = filter.trim().toLowerCase();
    const match = (text) => !needle || String(text || '').toLowerCase().includes(needle);

    /* Статья может лежать в нескольких разделах — тогда она и в дереве видна в
       каждом из них. Это не дубль: в обоих местах её действительно ищут. */
    const bySection = useMemo(() => {
        const map = new Map();
        articles.forEach((article) => {
            (article.section_ids || []).forEach((id) => {
                if (!map.has(id)) map.set(id, []);
                map.get(id).push(article);
            });
        });
        return map;
    }, [articles]);

    /* Статьи без раздела (или в разделе за периметром) иначе исчезли бы из
       оглавления совсем — а открыть их можно, и они есть в поиске. */
    const orphans = useMemo(() => {
        const known = new Set();
        tree.forEach(({ rows }) => rows.forEach(({ section }) => known.add(section.id)));
        return articles.filter((a) => !(a.section_ids || []).some((id) => known.has(id)));
    }, [tree, articles]);

    const draftCount = articles.filter((a) => a.status !== 'published').length;

    // Выбранный раздел раскрываем сами: иначе выбор в центре не виден в дереве.
    useEffect(() => {
        if (sectionId) setOpenSections((prev) => new Set(prev).add(sectionId));
    }, [sectionId]);

    const boxRef = useRef(null);
    useFitToViewport(boxRef);

    const articlesOf = (id) => (bySection.get(id) || []).filter(
        (a) => match(a.title) || match(a.summary));

    /* Кого показывать при фильтрации. Раздел виден, если совпал сам, совпала
       его статья ИЛИ совпал кто-то из потомков — иначе найденный подраздел
       пропадал бы вместе с несовпавшим родителем.
       Строки идут в порядке обхода дерева, поэтому считаем с конца: к моменту
       встречи родителя все его потомки уже посчитаны. */
    const visibleSections = useMemo(() => {
        if (!needle) return null;
        const visible = new Set();
        tree.forEach(({ rows }) => {
            const childHit = [];
            for (let i = rows.length - 1; i >= 0; i -= 1) {
                const { section, depth } = rows[i];
                const own = (bySection.get(section.id) || []).some(
                    (a) => match(a.title) || match(a.summary));
                if (match(section.name) || own || childHit[depth + 1]) {
                    visible.add(section.id);
                    childHit[depth] = true;
                }
                childHit[depth + 1] = false;
            }
        });
        return visible;
    }, [tree, bySection, needle]);

    /* Во время фильтрации раскрываем всё, где есть совпадения: искать в
       свёрнутом дереве бессмысленно. */
    const isOpen = (section) => (needle
        ? visibleSections.has(section.id)
        : openSections.has(section.id));

    const renderArticle = (article, depth) => (
        <button
            key={`${article.id}-${depth}`}
            type="button"
            onClick={() => onOpen(article.slug)}
            style={{ paddingLeft: `${16 + depth * 12}px` }}
            className={`${rowBase} text-slate-600 hover:bg-slate-50`}
        >
            <FileText size={12} className="shrink-0 text-slate-400" />
            <span className="min-w-0 flex-1 truncate">{article.title}</span>
            {article.status === 'draft' && (
                <span className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[9px] font-bold text-amber-700">
                    Черновик
                </span>
            )}
        </button>
    );

    const renderSpace = ({ space, rows }) => {
        const spaceClosed = !needle && closedSpaces.has(space.id);

        /* Строки приходят в порядке обхода дерева, поэтому свёрнутую ветку
           отсекаем курсором глубины: всё, что глубже свёрнутого раздела, —
           его потомки. Отдельная рекурсивная структура ради этого не нужна. */
        let hideDeeperThan = null;
        const body = [];

        rows.forEach(({ section, depth }) => {
            if (hideDeeperThan !== null && depth > hideDeeperThan) return;
            hideDeeperThan = null;

            const own = articlesOf(section.id);
            const open = isOpen(section);

            // При фильтрации прячем ветки, где ничего не нашлось ни у самого
            // раздела, ни у его потомков.
            if (needle && !visibleSections.has(section.id)) { hideDeeperThan = depth; return; }

            const active = sectionId === section.id;
            const count = own.length || section.readable_count || 0;

            body.push(
                <button
                    key={section.id}
                    type="button"
                    aria-expanded={open}
                    onClick={() => {
                        setOpenSections((prev) => toggled(prev, section.id));
                        onSection(active ? null : section.id);
                    }}
                    style={{ paddingLeft: `${6 + depth * 12}px` }}
                    className={`${rowBase} ${
                        active
                            ? 'bg-indigo-50 font-semibold text-indigo-600'
                            : 'text-slate-700 hover:bg-slate-50'
                    }`}
                >
                    <ChevronRight
                        size={12}
                        className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''} ${
                            active ? 'text-indigo-400' : 'text-slate-300'}`}
                    />
                    {open
                        ? <FolderOpen size={13} className={`shrink-0 ${active ? 'text-indigo-500' : 'text-slate-400'}`} />
                        : <Folder size={13} className={`shrink-0 ${active ? 'text-indigo-500' : 'text-slate-400'}`} />}
                    <span className="min-w-0 flex-1 truncate">{section.name}</span>
                    {/* Счётчик — по видимым статьям: цифра рядом с названием
                        обязана совпасть с тем, что раскроется по нажатию. */}
                    {count > 0 && (
                        <span className="shrink-0 text-[10.5px] tabular-nums text-slate-400">{count}</span>
                    )}
                </button>,
            );

            if (open) own.forEach((article) => body.push(renderArticle(article, depth + 1)));
            else hideDeeperThan = depth;
        });

        if (body.length === 0) return null;

        return (
            <div key={space.id}>
                <button
                    type="button"
                    aria-expanded={!spaceClosed}
                    onClick={() => setClosedSpaces((prev) => toggled(prev, space.id))}
                    className="flex w-full items-center gap-1 px-2.5 pb-1 pt-2.5 text-left text-[10px] font-semibold uppercase tracking-[0.07em] text-slate-400 transition hover:text-slate-600"
                >
                    <ChevronRight
                        size={11}
                        className={`shrink-0 transition-transform ${spaceClosed ? '' : 'rotate-90'}`}
                    />
                    <span className="min-w-0 flex-1 truncate">{space.name}</span>
                </button>
                {!spaceClosed && body}
            </div>
        );
    };

    const spaces = tree.map(renderSpace).filter(Boolean);
    const shownOrphans = orphans.filter((a) => match(a.title) || match(a.summary));
    const nothingFound = needle && spaces.length === 0 && shownOrphans.length === 0;

    return (
        /* self-stretch: без него колонка высотой в саму панель, и sticky
           «отлипает», как только её низ уходит вверх. Растянутая на всю строку,
           она даёт панели ездить до конца центральной колонки. */
        <aside className="lg:w-[272px] lg:shrink-0 lg:self-stretch 2xl:w-[306px]">
            <div
                ref={boxRef}
                className={`${iosCard} flex flex-col overflow-hidden lg:sticky lg:top-4 lg:max-h-[calc(100vh-2.5rem)]`}
            >
                <div className="flex items-center justify-between gap-2 px-3 pb-1 pt-2.5">
                    <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                        <Layers size={11} /> Разделы и статьи
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                        {draftCount > 0 && (
                            <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[9.5px] font-bold text-amber-700 tabular-nums">
                                {draftCount} черн.
                            </span>
                        )}
                        <span className="rounded-full bg-indigo-50 px-1.5 py-0.5 text-[9.5px] font-bold text-indigo-600 tabular-nums">
                            {articles.length}
                        </span>
                    </span>
                </div>

                <div className="mx-2.5 mb-1.5 flex items-center gap-1.5 rounded-lg bg-slate-100 px-2.5 py-1.5">
                    <Search size={12} className="shrink-0 text-slate-400" />
                    <input
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        placeholder="Поиск по оглавлению"
                        className="w-full min-w-0 bg-transparent text-[12px] text-slate-800 placeholder-slate-400 focus:outline-none"
                    />
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
                    <button
                        type="button"
                        onClick={() => onSection(null)}
                        style={{ paddingLeft: '10px' }}
                        className={`${rowBase} ${
                            sectionId
                                ? 'text-slate-700 hover:bg-slate-50'
                                : 'bg-indigo-50 font-semibold text-indigo-600'
                        }`}
                    >
                        <Folder size={13} className={sectionId ? 'text-slate-400' : 'text-indigo-500'} />
                        <span className="truncate">Все статьи</span>
                    </button>

                    {loading && (
                        <div className="flex items-center justify-center gap-2 py-6 text-slate-400">
                            <Loader2 size={14} className="animate-spin" />
                            <span className="text-[12px]">Загружаем…</span>
                        </div>
                    )}

                    {!loading && spaces}

                    {!loading && shownOrphans.length > 0 && (
                        <div>
                            <div className="px-2.5 pb-1 pt-2.5 text-[10px] font-semibold uppercase tracking-[0.07em] text-slate-400">
                                Вне разделов
                            </div>
                            {shownOrphans.map((article) => renderArticle(article, 0))}
                        </div>
                    )}

                    {!loading && nothingFound && (
                        <div className="px-3 py-5 text-center text-[12px] text-slate-400">
                            В оглавлении ничего не нашлось
                        </div>
                    )}

                    {!loading && !needle && spaces.length === 0 && shownOrphans.length === 0 && (
                        <div className="px-3 py-5 text-center text-[12px] text-slate-400">
                            Статей здесь пока нет
                        </div>
                    )}
                </div>
            </div>
        </aside>
    );
}
