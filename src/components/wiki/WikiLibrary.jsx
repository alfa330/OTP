import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { FileText, Loader2, Search, Sparkles, X } from 'lucide-react';
import { iosCard, iosGroupLabel, IosBadge } from '../ui/ios';
import WikiArticle from './WikiArticle';
import WikiHome from './WikiHome';
import WikiIndexPanel from './WikiIndexPanel';
import WikiParkRail from './WikiParkRail';
import WikiPark from './WikiPark';
import { markedWord } from './WikiSearch';
import useStableCallback from './useStableCallback';
import { syncArticleDeepLink } from './articleLink';
import { selectableSections } from './sectionPicker';
import { typeBadge } from './articleTypes';

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

/* Карточка статьи в выдаче поиска: заголовок, подпись типа и отрывок с
   найденным словом. */
const ArticleCard = ({ article, onOpen }) => {
    const meta = typeBadge(article.article_type);
    return (
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
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="text-[14px] font-semibold leading-snug text-slate-900">
                            {article.title}
                        </span>
                        {meta && <IosBadge tone={meta.tone}>{meta.label}</IosBadge>}
                    </div>
                    {article.snippet
                        ? <Snippet html={article.snippet} />
                        : article.summary && (
                            <p className="mt-1 line-clamp-2 text-[12.5px] leading-relaxed text-slate-500">
                                {article.summary}
                            </p>
                        )}
                </div>
            </div>
        </button>
    );
};

export default function WikiLibrary({ base, headers, showToast, structure, catalog,
                                      canCreate, canEdit = false,
                                      createRequest = null, onCreateConsumed,
                                      editTarget = null, onEditTargetConsumed,
                                      homeTick = 0,
                                      onOpenParks, onOpenCatalog, reloadCatalog,
                                      initialSlug, onInitialSlugConsumed,
                                      searchTarget, onSearchTargetConsumed,
                                      features = null, spaceId = null }) {
    /* Колбэки родителя стабилизируем: showToast — обычная функция в теле App,
       onSearchTargetConsumed — инлайновая стрелка в WikiView. Без этого список
       статей перезапрашивался на каждый чужой рендер (см. useStableCallback). */
    const toast = useStableCallback(showToast);
    const consumeInitialSlug = useStableCallback(onInitialSlugConsumed);
    const consumeSearchTarget = useStableCallback(onSearchTargetConsumed);
    const consumeCreate = useStableCallback(onCreateConsumed);
    const consumeEditTarget = useStableCallback(onEditTargetConsumed);
    const refreshCatalog = useStableCallback(reloadCatalog);

    const [openSlug, setOpenSlug] = useState(initialSlug || null);
    // Открытый парк — такая же страница витрины, как статья (см. WikiPark).
    const [openParkSlug, setOpenParkSlug] = useState(null);
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
    const [parks, setParks] = useState([]);
    const [parksCanManage, setParksCanManage] = useState(false);
    const [found, setFound] = useState(null);   // null = поиска не было
    const [loading, setLoading] = useState(false);
    /* Периметр витрины — ВСЕГДА личный: человек видит то, к чему имеет
     * отношение. Переключателя «Моё / Всё содержимое» здесь больше нет.
     *
     * Он держался на одном сценарии: статья БЕЗ РАЗДЕЛА в режиме «наследовать»
     * не видна никому, кроме автора (наследовать не от чего), и без широкого
     * периметра её нельзя было ни найти, ни починить. Теперь такой статьи не
     * бывает — сервер кладёт её в общий отдел при сохранении, — и переключатель
     * стал кнопкой, которая ничего не чинит, зато выкладывает администратору
     * содержимое чужих отделов вместе с черновиками.
     *
     * ?scope=all на сервере остаётся: это точечный путь для разбора инцидента,
     * а не режим витрины. */

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

    /* Открытая статья живёт в адресной строке: ?view=wiki&article=<slug>. Это и
       есть «ссылка на статью» — её копирует кнопка «Ссылка» на самой статье, и
       по ней же перезагрузка страницы возвращает человека в статью, а не в
       список. Метку снимаем при закрытии статьи и при уходе с вкладки: иначе
       она осталась бы в адресе показывать то, чего на экране нет. */
    useEffect(() => {
        syncArticleDeepLink(openSlug);
        return () => syncArticleDeepLink(null);
    }, [openSlug]);

    /* «Новая статья» и заголовок раздела живут в шапке, а состояние, которым они
       управляют, — здесь.
       Заголовок приходит счётчиком нажатий: он меняется на каждое нажатие, и
       повторно сработать можно без гашения флага в родителе. Сравниваем со
       ЗНАЧЕНИЕМ НА МОМЕНТ МОНТИРОВАНИЯ, а не с нулём: вкладка размонтируется
       при уходе на «Парки», и по возвращении ненулевой счётчик иначе сбросил бы
       витрину сам собой. */
    const seenTicks = useRef({ home: homeTick });

    /* «Новая статья» — не счётчик, а одноразовая просьба: кнопка работает и из
       каталога, откуда витрина монтируется ЗАНОВО, уже с новым значением. Гасим
       её сразу, как выполнили, — иначе следующий заход в раздел открывал бы
       редактор сам собой. */
    useEffect(() => {
        if (!createRequest) return;
        consumeCreate();
        setEditing({});
    }, [createRequest, consumeCreate]);

    /* Правка статьи из каталога. Статью тянем целиком: редактору нужен её
       текст, а список отдаёт только карточку. Тот же путь, что у
       onUpdateExisting ниже. */
    useEffect(() => {
        const slug = editTarget?.slug;
        if (!slug) return;
        consumeEditTarget();
        axios.get(`${base}/articles/${encodeURIComponent(slug)}`, { headers })
            .then((r) => setEditing(r.data))
            .catch((e) => toast(errText(e, 'Не удалось открыть статью на правку'), 'error'));
    }, [editTarget, consumeEditTarget, base, headers, toast]);

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
        setOpenParkSlug(null);
        setEditing(null);
        setQuery('');
        setFound(null);
    }, [homeTick]);

    /* Обычное открытие из списка — без подсветки: она осмысленна только когда
       известно, по какому слову статью нашли. */
    const openArticle = useCallback((slug) => {
        setOpenHighlight(null);
        setOpenPrefill(null);
        setOpenParkSlug(null);
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
        axios.get(`${base}/search`, { headers, params: { q: term, space_id: spaceId } })
            .then((r) => setFound(r.data?.items || []))
            .catch((e) => {
                setFound([]);
                toast(errText(e, 'Поиск не сработал'), 'error');
            })
            .finally(() => setLoading(false));
    }, [base, headers, query, toast, spaceId]);

    useEffect(() => {
        const timer = setTimeout(load, query ? 250 : 0);   // дебаунс только на поиск
        return () => clearTimeout(timer);
    }, [load, query]);

    /* Оглавление — весь периметр разом. Потолок сервера 200 статей; на большем
       содержимом список станет длинным, но не обрежется молча: разделы сверху
       остаются рабочим способом сузить выборку. */
    const loadIndex = useCallback(() => {
        setIndexLoading(true);
        return axios.get(`${base}/articles`,
                         { headers, params: { limit: 200, space_id: spaceId } })
            .then((r) => setIndex(r.data?.items || []))
            .catch(() => setIndex([]))
            .finally(() => setIndexLoading(false));
    }, [base, headers, spaceId]);

    const loadHome = useCallback(() => {
        setHomeLoading(true);
        return axios.get(`${base}/home`, { headers, params: { space_id: spaceId } })
            .then((r) => setHome(r.data))
            .catch(() => setHome(null))
            .finally(() => setHomeLoading(false));
    }, [base, headers, spaceId]);

    useEffect(() => { loadIndex(); }, [loadIndex]);
    useEffect(() => { loadHome(); }, [loadHome]);

    /* Парки нужны двум местам главной — рельсу и плитке-счётчику. Если в
       пространстве выключено и то, и другое, запрос не делаем вовсе: справочник
       общий на портал, и вика без парков тянула бы чужие пятнадцать штук ради
       того, чтобы их не показать. */
    const parksWanted = features?.parks !== false || features?.library_park_rail !== false;
    useEffect(() => {
        if (!parksWanted) { setParks([]); setParksCanManage(false); return; }
        axios.get(`${base}/parks`, { headers })
            .then((r) => {
                // Архивные парки сервер отдаёт управляющему справочником —
                // в рельсе им не место, это витрина «куда звонить сейчас».
                setParks((r.data?.items || []).filter((p) => p.status === 'active'));
                setParksCanManage(!!r.data?.can_manage);
            })
            .catch(() => setParks([]));
    }, [base, headers, parksWanted]);

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
                    spaces={spaces}
                    features={features}
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
                        loadHome();
                        // Числа каталога меняются той же правкой: опубликовали
                        // черновик — «Черновиков» обязано уменьшиться сразу.
                        refreshCatalog();
                        if (slug) setOpenSlug(slug);
                    }}
                />
            </Suspense>
        );
    }

    /* Парк — страница витрины, а не её часть: рельс с плитками на ней не нужен,
       человек уже выбрал парк. */
    if (openParkSlug) {
        return (
            <WikiPark
                base={base}
                headers={headers}
                slug={openParkSlug}
                onBack={() => setOpenParkSlug(null)}
                onOpenParks={onOpenParks}
            />
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
                /* Ссылка на другую статью внутри текста открывает её здесь же
                   (см. articleLink.js), без перезагрузки портала. */
                onOpenArticle={openArticle}
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
                    loadIndex();
                    loadHome();
                    refreshCatalog();
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
            {/* Рельс парков — тумблер пространства: у вики без парков он занимал
                бы колонку под пустой список. Сравнение с false, а не проверка
                истинности: пока ping не ответил, features нет вовсе, и рельс
                обязан остаться на месте, а не мигнуть. */}
            {features?.library_park_rail !== false && (
                <WikiParkRail
                    parks={parks}
                    canManage={parksCanManage}
                    onOpenPark={(slug) => setOpenParkSlug(slug)}
                    onOpenParks={onOpenParks}
                />
            )}

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
                                <ArticleCard key={article.id} article={article}
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
                            totals={catalog?.totals}
                            sectionsTotal={catalog?.sections_total}
                            /* null = справочника в этом пространстве нет, и
                               плитки быть не должно; ноль — это «есть, но пуст». */
                            parksCount={features?.parks === false ? null : parks.length}
                            home={home}
                            onOpen={openArticle}
                            onOpenCatalog={onOpenCatalog}
                            onOpenParks={onOpenParks}
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
