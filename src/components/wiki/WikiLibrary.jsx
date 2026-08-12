import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { FileText, Loader2, Search, Sparkles, X } from 'lucide-react';
import { iosCard, iosGroupLabel } from '../ui/ios';
import WikiArticle from './WikiArticle';
import WikiHome from './WikiHome';
import WikiIndexPanel from './WikiIndexPanel';
import WikiParkRail from './WikiParkRail';
import { markedWord } from './WikiSearch';
import useStableCallback from './useStableCallback';
import { selectableSections } from './sectionPicker';

// TipTap с ProseMirror весит ~128 КБ gzip — грузим только при открытии
// редактора, а не при входе в раздел.
const WikiEditor = lazy(() => import('./WikiEditor'));

/* Витрина статей — три колонки, как в макете десктопа.
 *
 *   рельс парков │ центр: описание, поиск, «про меня» │ оглавление раздела
 *
 * Дерево — внутренняя колонка раздела, а не второй сайдбар приложения. В
 * исходной вике «книжный» сайдбар был фиксированной панелью с z-50 и менял
 * padding КОРНЯ приложения; у нас слева уже стоит сайдбар портала с тем же
 * z-index, и две панели наложились бы друг на друга.
 *
 * Разделение обязанностей между колонками: оглавление справа — навигатор,
 * нажатие на раздел там только раскрывает его статьи, а центр остаётся
 * главным экраном (витрина «про меня» или выдача поиска). Раньше выбор раздела
 * ещё и фильтровал центр — тогда одни и те же статьи показывались дважды,
 * узким списком справа и карточками в центре.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

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

export default function WikiLibrary({ base, headers, showToast, structure, counters,
                                      canCreate, canEdit = false,
                                      createTick = 0, homeTick = 0,
                                      onOpenParks,
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
    // Документ, который надо применить к статье СРАЗУ после её открытия. Живёт
    // здесь, а не в редакторе: путь начинается в проверке дублей на другой
    // статье, и пережить смену открытого документа он обязан.
    const [pendingUpdateFile, setPendingUpdateFile] = useState(null);
    const [query, setQuery] = useState('');
    const [index, setIndex] = useState([]);         // весь периметр — для оглавления
    const [indexLoading, setIndexLoading] = useState(true);
    const [home, setHome] = useState(null);
    const [homeLoading, setHomeLoading] = useState(true);
    const [drafts, setDrafts] = useState([]);
    const [parks, setParks] = useState([]);
    const [parksCanManage, setParksCanManage] = useState(false);
    const [found, setFound] = useState(null);   // null = поиска не было
    const [loading, setLoading] = useState(false);

    const isEditor = !!(canCreate || canEdit);

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

    /* «Новая статья» и заголовок раздела живут в шапке, а состояние, которым они
       управляют, — здесь. Нажатие приходит счётчиком: он меняется на каждое
       нажатие, и повторно сработать можно без гашения флага в родителе.
       Сравниваем со ЗНАЧЕНИЕМ НА МОМЕНТ МОНТИРОВАНИЯ, а не с нулём: вкладка
       размонтируется при уходе на «Парки», и по возвращении ненулевой счётчик
       иначе открыл бы редактор сам собой. */
    const seenTicks = useRef({ create: createTick, home: homeTick });

    useEffect(() => {
        if (createTick === seenTicks.current.create) return;
        seenTicks.current.create = createTick;
        setEditing({});
    }, [createTick]);

    /* Возврат на главную витрины: закрываем статью и сбрасываем поиск. editing намеренно не в зависимостях — он читается тем значением,
       какое было в момент нажатия, а в deps заставил бы эффект сбрасывать
       витрину каждый раз, когда открывают редактор. */
    useEffect(() => {
        if (homeTick === seenTicks.current.home) return;
        seenTicks.current.home = homeTick;
        // Редактор — единственное состояние, где есть что терять. Портал и так
        // теряет правки при уходе в другой раздел, но заголовок стоит вплотную
        // к полям ввода, и промахнуться по нему слишком легко.
        if (editing && !window.confirm(
            'Уйти на главную вики? Несохранённые правки статьи пропадут.')) return;
        setOpenSlug(null);
        setOpenHighlight(null);
        setOpenPrefill(null);
        setEditing(null);
        setQuery('');
        setFound(null);
    }, [homeTick]);

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

    /* Центр показывает выдачу поиска — или ничего, когда на экране витрина
       «про меня». Короткий ввод поиска не запускает: от двух символов, как в
       оригинале. */
    const load = useCallback(() => {
        const term = query.trim();
        if (term.length < 2) { setFound(null); return; }

        setLoading(true);
        axios.get(`${base}/search`, { headers, params: { q: term } })
            .then((r) => setFound(r.data?.items || []))
            .catch((e) => {
                setFound([]);
                toast(errText(e, 'Поиск не сработал'), 'error');
            })
            .finally(() => setLoading(false));
    }, [base, headers, query, toast]);

    useEffect(() => {
        const timer = setTimeout(load, query ? 250 : 0);   // дебаунс только на поиск
        return () => clearTimeout(timer);
    }, [load, query]);

    /* Оглавление — весь периметр разом. Потолок сервера 200 статей; на большем
       содержимом список станет длинным, но не обрежется молча: разделы сверху
       остаются рабочим способом сузить выборку. */
    const loadIndex = useCallback(() => {
        setIndexLoading(true);
        return axios.get(`${base}/articles`, { headers, params: { limit: 200 } })
            .then((r) => setIndex(r.data?.items || []))
            .catch(() => setIndex([]))
            .finally(() => setIndexLoading(false));
    }, [base, headers]);

    const loadHome = useCallback(() => {
        setHomeLoading(true);
        return axios.get(`${base}/home`, { headers })
            .then((r) => setHome(r.data))
            .catch(() => setHome(null))
            .finally(() => setHomeLoading(false));
    }, [base, headers]);

    const loadDrafts = useCallback(() => {
        if (!isEditor) { setDrafts([]); return Promise.resolve(); }
        return axios.get(`${base}/articles`, { headers, params: { status: 'draft', limit: 8 } })
            .then((r) => setDrafts(r.data?.items || []))
            .catch(() => setDrafts([]));
    }, [base, headers, isEditor]);

    useEffect(() => { loadIndex(); }, [loadIndex]);
    useEffect(() => { loadHome(); }, [loadHome]);
    useEffect(() => { loadDrafts(); }, [loadDrafts]);

    useEffect(() => {
        axios.get(`${base}/parks`, { headers })
            .then((r) => {
                // Архивные парки сервер отдаёт управляющему справочником —
                // в рельсе им не место, это витрина «куда звонить сейчас».
                setParks((r.data?.items || []).filter((p) => p.status === 'active'));
                setParksCanManage(!!r.data?.can_manage);
            })
            .catch(() => setParks([]));
    }, [base, headers]);

    /* В дереве — только разделы своего периметра и только живые. Сервер отдаёт
       и чужие (вкладке «Структура» они нужны), помечая их accessible=false, и
       архивные — здесь, на витрине чтения, первые были бы ветками, которые
       открываются пустыми, а вторые — вторым экземпляром раздела рядом с живым
       двойником: архивируют обычно дубль с тем же именем. */
    const treeSections = useMemo(
        () => selectableSections(sections).filter((s) => s.accessible !== false),
        [sections],
    );

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
                    pendingUpdateFile={pendingUpdateFile}
                    onPendingUsed={() => setPendingUpdateFile(null)}
                    /* Документ оказался новой версией другой статьи: открываем
                       ТУ статью и несём файл с собой, чтобы человеку не искать
                       его заново. Статью тянем целиком — редактору нужен её
                       текст, а список отдаёт только карточку. */
                    onUpdateExisting={(row, file) => {
                        axios.get(`${base}/articles/${encodeURIComponent(row.slug)}`, { headers })
                            .then((r) => {
                                setPendingUpdateFile(file);
                                setEditing(r.data);
                            })
                            .catch((e) => showToast?.(errText(e, 'Не удалось открыть статью'), 'error'));
                    }}
                    onClose={() => { setEditing(null); setPendingUpdateFile(null); }}
                    /* Сохранение меняет и центр, и правую колонку: новая статья
                       обязана появиться в оглавлении и в черновиках сразу, иначе
                       после сохранения кажется, что её нет. */
                    onSaved={(slug) => {
                        setEditing(null);
                        load();
                        loadIndex();
                        loadDrafts();
                        loadHome();
                        if (slug) setOpenSlug(slug);
                    }}
                />
            </Suspense>
        );
    }

    /* Статья и редактор занимают всю ширину раздела намеренно: справочные
       таблицы вики — это шесть колонок и больше (см. ступени ширины в
       WikiView), и отобранные у них 400px возвращают перенос по буквам. */
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
                /* Статья приходит с сервера целиком (content, разделы, флаги),
                   поэтому редактору не нужен второй запрос — открываем прямо
                   на том объекте, который человек сейчас читает. */
                onEdit={(article) => setEditing(article)}
                onArchived={() => {
                    // Возвращаемся к списку: открытая статья только что ушла из
                    // него, и оставлять её на экране значит показывать то, чего
                    // в витрине уже нет.
                    setOpenSlug(null);
                    load();
                    loadHome();
                }}
            />
        );
    }

    const searching = query.trim().length >= 2;
    /* Между вводом и запросом лежит дебаунс в 250 мс. Без этого условия в
       окне дебаунса found ещё null, а «идёт загрузка» уже false — и на первые
       буквы запроса человек успевал увидеть «Ничего не найдено». */
    const busy = loading || (searching && found === null);

    return (
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
            <WikiParkRail parks={parks} canManage={parksCanManage} onOpenParks={onOpenParks} />

            <div className="flex min-w-0 flex-1 flex-col gap-3">
                {/* Обложка витрины: где человек находится и одно поле, с которого
                    начинается почти любой заход в базу знаний. */}
                <section className={`${iosCard} px-5 py-6 text-center sm:px-8`}>
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-50 px-2.5 py-1 text-[10.5px] font-bold text-indigo-600">
                        <Sparkles size={11} />
                        {isEditor ? 'Режим редактора' : 'База знаний компании'}
                    </span>

                    <h2 className="mx-auto mt-2.5 max-w-[520px] text-[26px] font-bold leading-[1.1] tracking-[-0.03em] text-slate-900 sm:text-[30px]">
                        Все статьи вики<br className="hidden sm:block" /> в одном месте.
                    </h2>

                    <p className="mx-auto mt-2 max-w-[500px] text-[12.5px] leading-relaxed text-slate-500">
                        {isEditor
                            ? 'Статьи, разделы и парки: черновики, доступы и порядок публикаций — на одном экране.'
                            : 'Поиск по содержимому статей, разделы по отделам и материалы таксопарков — без переходов между сервисами.'}
                    </p>

                    <div className="mx-auto mt-4 flex max-w-[560px] items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70">
                        <Search size={16} className="shrink-0 text-slate-400" />
                        {/* wiki-focus-outside: правило доступности в wiki-theme.css
                            рисует контур вокруг САМОГО input, а фокус здесь
                            показывает кольцо всей строки — выходила двойная
                            рамка. Индикация фокуса не теряется, она снаружи.
                            Тот же приём, что у поиска в шапке раздела. */}
                        <input
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Поиск по статьям — понимает опечатки и раскладку"
                            className="wiki-focus-outside w-full min-w-0 bg-transparent text-[13px] text-slate-900 placeholder-slate-400 focus:outline-none"
                        />
                        {query && (
                            <button
                                type="button"
                                onClick={() => setQuery('')}
                                aria-label="Очистить поиск"
                                className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-slate-200 text-slate-500 transition hover:bg-slate-300"
                            >
                                <X size={11} />
                            </button>
                        )}
                    </div>

                </section>


                {busy && (
                    <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-slate-400`}>
                        <Loader2 size={16} className="animate-spin" />
                        <span className="text-[13px]">Загружаем…</span>
                    </div>
                )}

                {!busy && searching && (
                    found.length > 0 ? (
                        <div className="space-y-2.5">
                            <div className={iosGroupLabel}>Найдено: {found.length}</div>
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

                {/* Пока /home не ответил, витрину «про меня» не рисуем пустыми
                    полками: «Пусто» и «Пока ничего не читали» — это утверждения,
                    и до ответа сервера они неправда. */}
                {!busy && !searching && (
                    homeLoading ? (
                        <div className={`${iosCard} h-[220px] overflow-hidden`}>
                            <div className="sk-shimmer h-full w-full" />
                        </div>
                    ) : (
                        <WikiHome
                            isEditor={isEditor}
                            counters={counters}
                            parksCount={parks.length}
                            drafts={drafts}
                            home={home}
                            onOpen={openArticle}
                        />
                    )
                )}
            </div>

            <WikiIndexPanel
                tree={tree}
                articles={index}
                onOpen={openArticle}
                loading={indexLoading}
            />
        </div>
    );
}
