import React, { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    BookOpen, ChevronRight, Clock, Eye, FileText, FolderTree, Loader2,
    Plus, Search, Star, TrendingUp,
} from 'lucide-react';
import { iosCard, iosGroupLabel, iosInput, iosBtnPrimary, IosBadge, IosToggle } from '../ui/ios';
import WikiArticle from './WikiArticle';
import { markedWord } from './WikiSearch';
import { CLASSIFIER_SLUG } from './WikiArticle';
import useStableCallback from './useStableCallback';

// TipTap с ProseMirror весит ~128 КБ gzip — грузим только при открытии
// редактора, а не при входе в раздел.
const WikiEditor = lazy(() => import('./WikiEditor'));

/* Библиотека: дерево разделов слева, статьи справа, статья по клику.
 *
 * Дерево — внутренняя колонка раздела, а не второй сайдбар приложения. В
 * исходной вике «книжный» сайдбар был фиксированной панелью с z-50 и менял
 * padding КОРНЯ приложения; у нас слева уже стоит сайдбар портала с тем же
 * z-index, и две панели наложились бы друг на друга.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const fmtDate = (iso) => (iso
    ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
    : '');

const ArticleCard = ({ article, onOpen }) => (
    <button
        type="button"
        onClick={() => onOpen(article.slug)}
        className={`${iosCard} group flex w-full flex-col items-start gap-1.5 p-4 text-left transition hover:ring-2 hover:ring-indigo-500/20`}
    >
        <div className="flex w-full items-start gap-3">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-indigo-50 text-indigo-600">
                <FileText size={16} />
            </div>
            <div className="min-w-0 flex-1">
                <div className="line-clamp-2 text-[14px] font-semibold leading-snug text-slate-900">
                    {article.title}
                </div>
                {article.summary && (
                    <p className="mt-1 line-clamp-2 text-[12.5px] leading-relaxed text-slate-500">
                        {article.summary}
                    </p>
                )}
            </div>
        </div>
        <div className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 pl-12 text-[11.5px] text-slate-400">
            {article.status !== 'published' && (
                <IosBadge tone={article.status === 'draft' ? 'slate' : 'amber'}>
                    {article.status === 'draft' ? 'Черновик' : article.status}
                </IosBadge>
            )}
            {/* У классификатора собственный периметр (restricted), но правило в
                нём одно: читать могут все роли. Бейдж на статье, открытой всем,
                вводил бы в заблуждение — см. тот же гард в WikiArticle. */}
            {article.visibility_mode === 'restricted' && article.slug !== CLASSIFIER_SLUG && (
                <IosBadge tone="amber">Только по списку</IosBadge>
            )}
            <span className="flex items-center gap-1 tabular-nums"><Eye size={11} /> {article.views}</span>
            <span className="tabular-nums">{fmtDate(article.updated_at)}</span>
        </div>
    </button>
);

/* Сниппет приходит с сервера уже с <mark>. Санитизация здесь не нужна и была
   бы вредна: ts_headline вставляет ровно те теги, что мы задали, а любой текст
   статьи внутри экранирован Postgres. Тем не менее собираем разметку вручную,
   а не через dangerouslySetInnerHTML, чтобы не открывать этот путь в принципе. */
const Snippet = ({ html }) => {
    if (!html) return null;
    const parts = String(html).split(/(<mark>.*?<\/mark>)/g);
    return (
        <p className="mt-1 text-[12.5px] leading-relaxed text-slate-500">
            {parts.map((part, index) => (
                part.startsWith('<mark>')
                    ? (
                        <mark key={index} className="rounded bg-amber-200/70 px-0.5 font-medium text-slate-900">
                            {part.slice(6, -7)}
                        </mark>
                    )
                    : <React.Fragment key={index}>{part}</React.Fragment>
            ))}
        </p>
    );
};

const SearchHit = ({ article, onOpen }) => (
    <button
        type="button"
        onClick={() => onOpen(article.slug)}
        className={`${iosCard} w-full p-4 text-left transition hover:ring-2 hover:ring-indigo-500/20`}
    >
        <div className="flex items-start gap-3">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-indigo-50 text-indigo-600">
                <FileText size={16} />
            </div>
            <div className="min-w-0 flex-1">
                <div className="text-[14px] font-semibold leading-snug text-slate-900">
                    {article.title}
                </div>
                <Snippet html={article.snippet} />
            </div>
        </div>
    </button>
);

const MiniList = ({ title, icon: Icon, items, onOpen, empty }) => (
    <section className="space-y-1.5">
        <div className={`${iosGroupLabel} flex items-center gap-1.5`}>
            <Icon size={12} /> {title}
        </div>
        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
            {items.length === 0 && (
                <div className="px-4 py-6 text-center text-[12.5px] text-slate-400">{empty}</div>
            )}
            {items.map((item) => (
                <button
                    key={item.id}
                    type="button"
                    onClick={() => onOpen(item.slug)}
                    className="flex w-full items-center gap-2 px-4 py-2.5 text-left transition hover:bg-slate-50"
                >
                    <span className="min-w-0 flex-1 truncate text-[13px] text-slate-800">{item.title}</span>
                    <ChevronRight size={14} className="shrink-0 text-slate-300" />
                </button>
            ))}
        </div>
    </section>
);

export default function WikiLibrary({ base, headers, showToast, structure, canCreate,
                                      canSeeEverything = false,
                                      initialSlug, onInitialSlugConsumed,
                                      searchTarget, onSearchTargetConsumed }) {
    /* Колбэки родителя стабилизируем: showToast — обычная функция в теле App,
       onSearchTargetConsumed — инлайновая стрелка в WikiView. Без этого список
       статей перезапрашивался на каждый чужой рендер (см. useStableCallback). */
    const toast = useStableCallback(showToast);
    const consumeInitialSlug = useStableCallback(onInitialSlugConsumed);
    const consumeSearchTarget = useStableCallback(onSearchTargetConsumed);

    const [openSlug, setOpenSlug] = useState(initialSlug || null);
    const [openHighlight, setOpenHighlight] = useState(null);
    // Префилл для статьи-классификатора: пришли из поиска с готовой машиной.
    const [openPrefill, setOpenPrefill] = useState(null);
    const [editing, setEditing] = useState(null);   // null | {} | статья
    const [sectionId, setSectionId] = useState(null);
    const [query, setQuery] = useState('');
    const [items, setItems] = useState([]);
    const [home, setHome] = useState(null);
    const [found, setFound] = useState(null);   // null = поиска не было
    const [loading, setLoading] = useState(true);
    /* «Всё содержимое портала» — только для администратора доступов и только по
       его явному нажатию. По умолчанию раздел показывает личный периметр: то,
       к чему у человека есть отношение по правилам. */
    const [scopeAll, setScopeAll] = useState(false);

    /* Статья из уведомления открывается один раз: значение сразу гасится в
       App, иначе следующий заход в раздел снова открывал бы её поверх того,
       что пользователь смотрел. Отдельный ref не нужен — гашение и есть
       признак того, что переход уже отработал. */
    useEffect(() => {
        if (!initialSlug) return;
        setOpenHighlight(null);
        setOpenSlug(initialSlug);
        consumeInitialSlug();
    }, [initialSlug, consumeInitialSlug]);

    /* Переход из поисковой модалки WikiView: та же одноразовая механика, но
       вдобавок несёт слово для подсветки в тексте статьи. */
    useEffect(() => {
        if (!searchTarget?.slug) return;
        setOpenHighlight(searchTarget.highlight || null);
        setOpenPrefill(searchTarget.prefill || null);
        setOpenSlug(searchTarget.slug);
        consumeSearchTarget();
    }, [searchTarget, consumeSearchTarget]);

    /* Обычное открытие из списка — без подсветки: она осмысленна только когда
       известно, по какому слову статью нашли. */
    const openArticle = useCallback((slug) => {
        setOpenHighlight(null);
        setOpenPrefill(null);
        setOpenSlug(slug);
    }, []);

    /* Открытие ИЗ ВЫДАЧИ — с подсветкой, как из поисковой модалки. Раньше это
       же действие в двух местах интерфейса работало по-разному: человеку
       показывали сниппет с найденным словом, а статья открывалась на первом
       экране, где этого слова нет. */
    const openHit = useCallback((article) => {
        setOpenHighlight(markedWord(article.snippet, query.trim()));
        setOpenPrefill(null);
        setOpenSlug(article.slug);
    }, [query]);

    const spaces = structure?.spaces || [];
    const sections = structure?.sections || [];

    const load = useCallback(() => {
        setLoading(true);
        const term = query.trim();

        // Короткий ввод — обычный список; от двух символов включается
        // полнотекстовый поиск со сниппетами, как в оригинале.
        if (term.length >= 2) {
            const params = { q: term, ...(scopeAll ? { scope: 'all' } : {}) };
            if (sectionId) params.section_id = sectionId;
            axios.get(`${base}/search`, { headers, params })
                .then((r) => setFound(r.data?.items || []))
                .catch((e) => {
                    setFound([]);
                    toast(errText(e, 'Поиск не сработал'), 'error');
                })
                .finally(() => setLoading(false));
            return;
        }

        setFound(null);
        const params = scopeAll ? { scope: 'all' } : {};
        if (sectionId) params.section_id = sectionId;
        axios.get(`${base}/articles`, { headers, params })
            .then((r) => setItems(r.data?.items || []))
            .catch((e) => toast(errText(e, 'Не удалось загрузить статьи'), 'error'))
            .finally(() => setLoading(false));
    }, [base, headers, sectionId, query, scopeAll, toast]);

    useEffect(() => {
        const timer = setTimeout(load, query ? 250 : 0);   // дебаунс только на поиск
        return () => clearTimeout(timer);
    }, [load, query]);

    useEffect(() => {
        axios.get(`${base}/home`, { headers, params: scopeAll ? { scope: 'all' } : {} })
            .then((r) => setHome(r.data))
            .catch(() => setHome(null));
    }, [base, headers, scopeAll]);

    /* В дереве — только разделы своего периметра. Сервер отдаёт и чужие
       (вкладке «Структура» они нужны), помечая их accessible=false; здесь,
       на витрине чтения, они были бы ветками, которые открываются пустыми. */
    const treeSections = useMemo(() => (
        scopeAll ? sections : sections.filter((s) => s.accessible !== false)
    ), [sections, scopeAll]);

    const tree = useMemo(() => {
        const shown = new Set(treeSections.map((s) => s.id));
        const children = new Map();
        treeSections.forEach((s) => {
            // Родитель скрыт — раздел поднимается в корень пространства, иначе
            // доступная ветка потерялась бы под недоступной.
            const key = (s.parent_section_id && shown.has(s.parent_section_id))
                ? s.parent_section_id
                : `root:${s.space_id}`;
            if (!children.has(key)) children.set(key, []);
            children.get(key).push(s);
        });
        const walk = (key, depth) => (children.get(key) || [])
            .flatMap((s) => [{ section: s, depth }, ...walk(s.id, depth + 1)]);
        return spaces
            .map((space) => ({ space, rows: walk(`root:${space.id}`, 0) }))
            .filter(({ rows }) => rows.length > 0);
    }, [spaces, treeSections]);

    /* Выключили «всё содержимое» — выбранный раздел мог оказаться за периметром.
       Сбрасываем фильтр, иначе список молча остался бы пустым. */
    useEffect(() => {
        if (sectionId && !treeSections.some((s) => s.id === sectionId)) setSectionId(null);
    }, [treeSections, sectionId]);

    if (editing) {
        return (
            <Suspense fallback={(
                <div className={`${iosCard} flex items-center justify-center gap-2 py-16 text-slate-400`}>
                    <Loader2 size={18} className="animate-spin" />
                    <span className="text-[13px]">Загружаем редактор…</span>
                </div>
            )}>
                <WikiEditor
                    base={base}
                    headers={headers}
                    showToast={showToast}
                    article={editing.id ? editing : null}
                    sections={sections}
                    onClose={() => setEditing(null)}
                    onSaved={(slug) => { setEditing(null); load(); if (slug) setOpenSlug(slug); }}
                />
            </Suspense>
        );
    }

    if (openSlug) {
        return (
            <WikiArticle
                base={base}
                headers={headers}
                slug={openSlug}
                highlightTerm={openHighlight}
                classifierPrefill={openPrefill}
                showToast={showToast}
                onBack={() => { setOpenSlug(null); setOpenHighlight(null); setOpenPrefill(null); }}
            />
        );
    }

    return (
        <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[200px] flex-1">
                    <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        className={`${iosInput} pl-10`}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Поиск по статьям — понимает опечатки и раскладку"
                    />
                </div>
                {canCreate && (
                    <button type="button" className={iosBtnPrimary} onClick={() => setEditing({})}>
                        <Plus size={15} /> Новая статья
                    </button>
                )}
            </div>

            {/* Администратор доступов по умолчанию видит свой периметр, как все.
                Всё содержимое портала — по явному нажатию: без него не найти
                статью, которую попросили починить, а молча показывать чужие
                отделы и черновики раздел не должен. */}
            {canSeeEverything && (
                <div className={`${iosCard} flex items-center gap-3 px-4 py-3`}>
                    <IosToggle checked={scopeAll} onChange={setScopeAll} />
                    {/* Подпись — отдельная кнопка, а не <label>: внутри IosToggle
                        лежит <button>, а его метка не связывается с label. */}
                    <button
                        type="button"
                        onClick={() => setScopeAll(!scopeAll)}
                        className="min-w-0 flex-1 text-left"
                    >
                        <span className="block text-[13.5px] font-medium text-slate-900">
                            Всё содержимое портала
                        </span>
                        <span className="block text-[12px] leading-snug text-slate-500">
                            Статьи всех отделов, включая черновики и архив. Обычно
                            здесь только то, к чему у вас есть доступ.
                        </span>
                    </button>
                </div>
            )}

            <div className="flex flex-col gap-5 lg:flex-row">
                {/* Дерево разделов — внутренняя колонка, а не второй сайдбар портала. */}
                {tree.length > 0 && (
                    <nav className="lg:w-60 lg:shrink-0">
                        <div className={`${iosGroupLabel} mb-1.5 flex items-center gap-1.5`}>
                            <FolderTree size={12} /> Разделы
                        </div>
                        <div className={`${iosCard} overflow-hidden`}>
                            <button
                                type="button"
                                onClick={() => setSectionId(null)}
                                className={`w-full px-3.5 py-2 text-left text-[13px] transition hover:bg-slate-50 ${
                                    !sectionId ? 'bg-blue-50 font-medium text-blue-700' : 'text-slate-700'
                                }`}
                            >
                                Все статьи
                            </button>
                            {tree.map(({ space, rows }) => (
                                <div key={space.id} className="border-t border-slate-100">
                                    <div className="px-3.5 pb-1 pt-2.5 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">
                                        {space.name}
                                    </div>
                                    {rows.map(({ section, depth }) => (
                                        <button
                                            key={section.id}
                                            type="button"
                                            onClick={() => setSectionId(section.id)}
                                            className={`flex w-full items-center gap-1.5 py-2 pr-3 text-left text-[13px] transition hover:bg-slate-50 ${
                                                sectionId === section.id
                                                    ? 'bg-blue-50 font-medium text-blue-700'
                                                    : 'text-slate-700'
                                            }`}
                                            style={{ paddingLeft: `${14 + depth * 14}px` }}
                                        >
                                            <span className="min-w-0 flex-1 truncate">{section.name}</span>
                                            {/* Счётчик — по видимым статьям, а не по всем:
                                                цифра рядом с названием обязана совпадать
                                                с тем, что откроется по клику. */}
                                            {(scopeAll ? section.articles_count : section.readable_count) > 0 && (
                                                <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
                                                    {scopeAll ? section.articles_count : section.readable_count}
                                                </span>
                                            )}
                                        </button>
                                    ))}
                                </div>
                            ))}
                        </div>
                    </nav>
                )}

                <div className="min-w-0 flex-1 space-y-5">
                    {loading && (
                        <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-slate-400`}>
                            <Loader2 size={16} className="animate-spin" />
                            <span className="text-[13px]">Загружаем…</span>
                        </div>
                    )}

                    {!loading && found !== null && (
                        found.length > 0 ? (
                            <div className="space-y-3">
                                <div className={iosGroupLabel}>
                                    Найдено: {found.length}
                                </div>
                                {found.map((article) => (
                                    <SearchHit key={article.id} article={article}
                                               onOpen={() => openHit(article)} />
                                ))}
                            </div>
                        ) : (
                            <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                                <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                                    <Search size={22} />
                                </div>
                                <div className="text-[15px] font-semibold text-slate-900">Ничего не найдено</div>
                                <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                                    Поиск понимает опечатки, латиницу и забытую раскладку —
                                    попробуйте другое слово.
                                </p>
                            </div>
                        )
                    )}

                    {!loading && found === null && items.length === 0 && (
                        <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                            <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                                <BookOpen size={22} />
                            </div>
                            <div className="text-[15px] font-semibold text-slate-900">
                                {query ? 'Ничего не найдено' : 'Здесь пока пусто'}
                            </div>
                            <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                                {query
                                    ? 'Попробуйте изменить запрос. Полнотекстовый поиск по содержимому появится на следующем этапе.'
                                    : 'Статьи появятся после переноса содержимого.'}
                            </p>
                        </div>
                    )}

                    {!loading && found === null && items.length > 0 && (
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                            {items.map((article) => (
                                <ArticleCard key={article.id} article={article} onOpen={openArticle} />
                            ))}
                        </div>
                    )}

                    {!query && home && (
                        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                            <MiniList
                                title="Избранное" icon={Star}
                                items={home.favorites || []} onOpen={openArticle}
                                empty="Пусто"
                            />
                            <MiniList
                                title="Недавнее" icon={Clock}
                                items={home.recent || []} onOpen={openArticle}
                                empty="Пока не читали"
                            />
                            <MiniList
                                title="Популярное" icon={TrendingUp}
                                items={home.popular || []} onOpen={openArticle}
                                empty="Пусто"
                            />
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
