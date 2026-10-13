import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import lazyWithRetry from '../../utils/lazyWithRetry';
import axios from 'axios';
import { FileText, Loader2, Search, Sparkles, User, X } from 'lucide-react';
import { iosCard, iosGroupLabel, IosBadge } from '../ui/ios';
import WikiArticle from './WikiArticle';
import WikiHome from './WikiHome';
import WikiIndexPanel from './WikiIndexPanel';
import WikiParkRail from './WikiParkRail';
import WikiPark from './WikiPark';
import { ArticleStamp, markedWord } from './WikiSearch';
import WikiSearchFilters, { SearchFilterButton } from './WikiSearchFilters';
import { EMPTY_FILTERS, filtersKey, searchParams } from './searchFilters';
import { AskAssistantEmpty, AskAssistantRow } from './WikiAskAssistant';
import { DirectoryRow, directoryKey, directoryTab, openableDirectory } from './searchDirectory';
import useStableCallback from './useStableCallback';
import { syncArticleDeepLink } from './articleLink';
import { openTrail, popTrail, pushTrail, trailBack, trailTop } from './articleTrail';
import { portalScrollTop, scrollPortalTo } from './scrollContainer';
import { fetchArticleIndex } from './articleIndex';
import { absoluteFileUrl } from './fileUrls';
import { selectableSections } from './sectionPicker';
import { typeBadge } from './articleTypes';
import {
    isRefusal, keepUnchanged, memoryKey, recall, remember,
} from './entryMemory';

// TipTap с ProseMirror весит ~128 КБ gzip — грузим только при открытии
// редактора, а не при входе в раздел.
const WikiEditor = lazyWithRetry(() => import('./WikiEditor'));

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

/* Сколько статей забираем за раз. Совпадает со значением по умолчанию на
   сервере, но объявлено здесь: строка «Найдено: N» упирается ровно в это число,
   и без суффикса «+» на упоре она врала бы. */
const SEARCH_LIMIT = 20;

// Один пустой список на все рендеры: новый [] каждый раз перерисовывал бы рельс.
const NO_PARKS = [];

/* Сколько первый кадр витрины ждёт ответа о парках после ответа /ping (см.
   railSettled). Запрос о парках уходит одновременно с /ping, так что обычно
   ждать нечего; полсекунды — на случай, когда он отстал. Дольше держать под
   заготовкой поиск и оглавление ради справочника под рукой нельзя. */
const RAIL_WAIT_MS = 500;

/* Логотип парка в ответе — подписанный адрес со сроком: сервер отдаёт подпись,
   которой осталось жить не меньше получаса (wiki/file_urls.py:
   _RESIGN_BEFORE_SECONDS). Запомненным логотипам верим чуть меньше. */
const LOGO_FRESH_MS = 25 * 60 * 1000;

/* Запомненный ответ о парках — в том виде, в каком его можно показать.
 *
 * Состав рельса не портится, а адреса логотипов портятся: портал держат
 * открытым всю смену, и рельс, вставший из памяти через час, показал бы вместо
 * логотипов битые картинки — до свежего ответа. У постаревшего ответа адреса
 * убираем: плитка рисует буквы, как у парка без логотипа, а логотипы приедут
 * со свежим ответом на своё место, ничего не сдвинув. «Раздетый» ответ кладём
 * обратно в память — чтобы каждый рендер получал один и тот же объект.
 */
const recallParks = (key) => {
    const answer = recall(key);
    if (!answer || answer.plain || Date.now() - answer.at <= LOGO_FRESH_MS) return answer;
    const plain = {
        ...answer,
        plain: true,
        items: answer.items.map((park) => (park.logo_url ? { ...park, logo_url: null } : park)),
    };
    remember(key, plain);
    return plain;
};

/* Заготовка витрины — первый кадр, пока неизвестны права и пространство.
 *
 * Те же три места, что займёт витрина: обложка, подборки под ней и оглавление
 * справа, — тем же видом, что заглушка подборок ниже (она при появлении
 * витрины остаётся на месте, пока едет /home). Рельса парков в заготовке нет:
 * будет ли он, решает ответ, которого ещё нет. Настоящего текста здесь тоже
 * нет намеренно — подпись обложки зависит от прав («Режим редактора»), а
 * показанное раньше ответа пришлось бы на глазах менять.
 */
const LibrarySkeleton = () => (
    <div
        role="status"
        aria-busy="true"
        aria-label="Загружаем вики"
        className="flex flex-col gap-3 lg:flex-row lg:items-start"
    >
        {/* Высоты — по настоящим блокам на компьютере: обложка 257 px,
            заглушка подборок 220, оглавление с «Загружаем…» 146. Блок обложки
            носит её метку: на телефоне обложки нет (wiki-mobile.css), и её
            заготовка обязана пропасть там же и тем же правилом. */}
        <div className="flex min-w-0 flex-1 flex-col gap-3">
            <div className={`${iosCard} wiki-m-hero h-[257px] overflow-hidden`}>
                <div className="sk-shimmer h-full w-full" />
            </div>
            <div className={`${iosCard} h-[220px] overflow-hidden`}>
                <div className="sk-shimmer h-full w-full" />
            </div>
        </div>
        <div className="hidden lg:block lg:w-[272px] lg:shrink-0 2xl:w-[306px]">
            <div className={`${iosCard} h-[146px] overflow-hidden`}>
                <div className="sk-shimmer h-full w-full" />
            </div>
        </div>
    </div>
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

/* Карточка статьи в выдаче поиска: заголовок, подпись типа, актуальность с
   датой добавления и отрывок с найденным словом.

   Штамп актуальности тот же, что в выпадашке поиска (WikiSearch.ArticleStamp), и
   это одно из двух мест, где он обязан стоять: витрина и выпадашка — две двери в
   одну и ту же выдачу, и ответ на «черновик это или живая статья» не может
   зависеть от того, в какую человек вошёл. */
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
                        <ArticleStamp status={article.status} createdAt={article.created_at} />
                        {/* Создатель — рядом с типом, а не отдельной строкой:
                            выбрав фильтр по человеку, его же и хочется увидеть
                            в выдаче, иначе фильтр нечем проверить глазами. */}
                        {article.author_name && (
                            <span className="inline-flex items-center gap-1 text-[11.5px] text-slate-400">
                                <User size={11} /> {article.author_name}
                            </span>
                        )}
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
                                      features = null, spaceId = null,
                                      onAskAssistant = null,
                                      /* Вернуть человека на вкладку, с которой он
                                         открыл статью (каталог, журнал, отчёт). */
                                      onReturnTo = null,
                                      /* «Опубликовать статью и как новость»
                                         (23.09.2026): из статьи и из редактора.
                                         null — нельзя. */
                                      onPublishAsNews = null,
                                      /* Справочник поиска (офисы, комиссии
                                         Яндекса): та же дверь, что у поиска в
                                         шапке. null — вкладки нет. */
                                      onOpenOffice = null, onOpenCity = null,
                                      /* Известны ли права и пространство
                                         (ответ /ping). Пока нет — витрина
                                         стоит заготовкой и ничего не
                                         запрашивает; статья по ссылке при этом
                                         открывается сразу, ей пространство не
                                         нужно. leaving — с витрины уходят этим
                                         же коммитом; unavailable — /ping
                                         отказал, отказ уже показан выше. */
                                      ready = true, leaving = false, unavailable = false,
                                      /* Первая загрузка дерева и каталога ещё
                                         идёт — см. WikiView. */
                                      structurePending = false, catalogPending = false,
                                      userId = null }) {
    /* Колбэки родителя стабилизируем: showToast — обычная функция в теле App,
       onSearchTargetConsumed — инлайновая стрелка в WikiView. Без этого список
       статей перезапрашивался на каждый чужой рендер (см. useStableCallback). */
    /* Витрина «живёт» — рисует первый кадр, — когда известны права и
       пространство и с неё не уходят этим же коммитом. */
    const live = ready && !leaving;
    /* ЗА ДАННЫМИ она уходит раньше — как только известно пространство. До
       ответа /ping раздел передаёт сюда пространство, выбранное в прошлый раз
       (или названное ссылкой): запросы витрины уходят ОДНОВРЕМЕННО с /ping, а не
       после него, и первый вход не ждёт два круга подряд. Проверять догадку не
       нужно — периметр считает сервер, пространство лишь сужает его; не
       угадали (пространство убрали в архив) — ответ /ping назовёт другое, и
       запросы уйдут заново, а ответы за прежнее отбросят метки ниже. Рисовать при этом
       по-прежнему нечего, пока нет прав: подпись обложки и плитки зависят от
       них. Без пространства и без ответа — ждём: запрос «по всем пространствам разом»
       и был лишним кругом каждого входа. */
    const canLoad = !leaving && (ready || !!spaceId);

    const toast = useStableCallback(showToast);
    const consumeInitialSlug = useStableCallback(onInitialSlugConsumed);
    const consumeSearchTarget = useStableCallback(onSearchTargetConsumed);
    const consumeCreate = useStableCallback(onCreateConsumed);
    const consumeEditTarget = useStableCallback(onEditTargetConsumed);
    const refreshCatalog = useStableCallback(reloadCatalog);
    const askAssistant = useStableCallback(onAskAssistant);
    const returnTo = useStableCallback(onReturnTo);
    const canAskAssistant = !!onAskAssistant;
    const openOffice = useStableCallback(onOpenOffice);
    const openCity = useStableCallback(onOpenCity);

    /* Открытая статья — ВЕРШИНА цепочки переходов, а не отдельное значение:
       из статьи уходят по ссылке в текст соседней, и возврат обязан вести туда,
       откуда пришли (см. articleTrail.js). Подсветка найденного слова и
       заготовка классификатора живут в записи цепочки, а не рядом с ней: они
       принадлежат КОНКРЕТНОЙ статье, и при возврате должны вернуться вместе
       с ней. */
    const [trail, setTrail] = useState(() => openTrail(initialSlug || null));
    const openEntry = trailTop(trail);
    const openSlug = openEntry?.slug || null;
    const openHighlight = openEntry?.highlight || null;
    const openPrefill = openEntry?.prefill || null;
    const backEntry = trailBack(trail);
    /* ДВЕРЬ, через которую вошли в статью: вкладка раздела, где человек был до
       того (каталог, журнал, аналитика, помощник). Хранится РЯДОМ с цепочкой, а
       не внутри неё, и это не мелочь: цепочка обрезается по потолку глубины с
       самого старого конца, то есть корневая запись рано или поздно уходит — и
       вместе с ней ушла бы дверь. Цепочка про статьи, дверь — про выход. */
    const [door, setDoor] = useState(null);   // {tab, label} либо null
    // Открытый парк — такая же страница витрины, как статья (см. WikiPark).
    const [openParkSlug, setOpenParkSlug] = useState(null);
    const [editing, setEditing] = useState(null);   // null | {} | статья
    // Документ, который надо применить к статье СРАЗУ после её открытия. Живёт
    // здесь, а не в редакторе: путь начинается в проверке дублей на другой
    // статье, и пережить смену открытого документа он обязан.
    const [pendingUpdateFile, setPendingUpdateFile] = useState(null);
    const [query, setQuery] = useState('');
    /* Фильтры выдачи: тип документа, создатель и область поиска. Живут рядом с
       запросом и никуда не деваются, пока человек на витрине — их выбор виден
       на кнопке и чипами, скрытым состоянием он не становится. */
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [authors, setAuthors] = useState([]);
    const [authorsLoading, setAuthorsLoading] = useState(false);
    const [index, setIndex] = useState([]);         // весь периметр: оглавление и пикер ссылок
    const [indexLoading, setIndexLoading] = useState(true);
    /* Номер захода за оглавлением: оно собирается из НЕСКОЛЬКИХ ответов, и за
       это время человек успевает переключить пространство в шапке. Без метки
       медленная старая сборка дописала бы в правую колонку чужие статьи. */
    const indexRun = useRef(0);
    const [home, setHome] = useState(null);
    const [homeLoading, setHomeLoading] = useState(true);
    // Та же метка, что у оглавления: поздний ответ за прежнее пространство
    // не должен лечь поверх подборок нынешнего.
    const homeRun = useRef(0);
    /* За какое пространство лежат (или уже едут) подборки и оглавление. Загрузчики —
       эффекты, они отрабатывают ПОСЛЕ рендера, в котором сменилось
       пространство: на этот рендер в состоянии ещё лежит прежнее (или то, что
       было догадкой и ответом /ping не подтвердилось), а «идёт загрузка»
       ещё не поднято. Пока метка не догнала пространство, данные считаем
       недогруженными — иначе на кадр вставали бы чужие полки и оглавление. */
    const loadedFor = useRef(undefined);
    const [found, setFound] = useState(null);   // null = поиска не было
    // Справочник того же ответа — офисы и комиссии (wiki/directory.py).
    const [foundDirectory, setFoundDirectory] = useState([]);
    const canOpenOffices = !!onOpenOffice;
    const canOpenCities = !!onOpenCity;
    const shownDirectory = useMemo(
        () => openableDirectory(foundDirectory, { offices: canOpenOffices, cities: canOpenCities }),
        [foundDirectory, canOpenOffices, canOpenCities],
    );
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

    /* «Опубликовать статью и как новость»: вместе со статьёй уходит и ДВЕРЬ, с
       которой в неё вошли. Форма новости живёт на другой вкладке, и, вернувшись
       из неё, человек обязан получить статью с тем же «назад», что был до
       формы, — а не выход в список, которого он не просил. */
    const publishAsNews = useMemo(
        () => (onPublishAsNews ? (article) => onPublishAsNews(article, door) : null),
        [onPublishAsNews, door]);

    /* Статья из уведомления открывается один раз: значение сразу гасится в
       App, иначе следующий заход в раздел снова открывал бы её поверх того,
       что пользователь смотрел. Отдельный ref не нужен — гашение и есть
       признак того, что переход уже отработал. */
    useEffect(() => {
        if (!initialSlug) return;
        /* Парк гасим ЯВНО: его страница стоит в рендере раньше статьи, и без
           этого ссылка из чата или клик в колоколе, пришедшие поверх открытого
           парка, меняли бы адресную строку, не меняя экрана. */
        setOpenParkSlug(null);
        setDoor(null);            // ссылка из чата — вход снаружи, вкладки-источника нет
        setTrail(openTrail(initialSlug));
        consumeInitialSlug();
    }, [initialSlug, consumeInitialSlug]);

    /* Переход из поисковой модалки WikiView: та же одноразовая механика, но
       вдобавок несёт слово для подсветки в тексте статьи. */
    useEffect(() => {
        if (!searchTarget?.slug) return;
        setOpenParkSlug(null);
        // Единственный путь, у которого дверь есть: статью открыли с другой вкладки.
        setDoor(searchTarget.from || null);
        setTrail(openTrail(searchTarget.slug, {
            highlight: searchTarget.highlight || null,
            prefill: searchTarget.prefill || null,
        }));
        consumeSearchTarget();
    }, [searchTarget, consumeSearchTarget]);

    /* Открытая статья живёт в адресной строке: ?view=wiki&article=<slug>. Это и
       есть «ссылка на статью» — её копируют из адресной строки, и по ней же
       перезагрузка страницы возвращает человека в статью, а не в список.
       Метку снимаем при закрытии статьи и при уходе с вкладки: иначе она
       осталась бы в адресе показывать то, чего на экране нет. */
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
        setTrail([]);
        // Заголовок раздела — явная просьба «на главную», дверь ей не перечит.
        setDoor(null);
        setOpenParkSlug(null);
        setEditing(null);
        setQuery('');
        setFound(null);
    }, [homeTick]);

    /* Обычное открытие из списка — без подсветки: она осмысленна только когда
       известно, по какому слову статью нашли. Цепочка при этом начинается
       заново: список — не шаг по сети статей, и возврат из него ведёт в список. */
    const openArticle = useCallback((slug) => {
        setOpenParkSlug(null);
        setDoor(null);            // открыли из витрины — в витрину и возвращаемся
        setTrail(openTrail(slug));
    }, []);

    /* Открытие ИЗ ВЫДАЧИ — с подсветкой, как из поисковой модалки. Раньше это
       же действие в двух местах интерфейса работало по-разному: человеку
       показывали сниппет с найденным словом, а статья открывалась на первом
       экране, где этого слова нет. */
    const openHit = useCallback((article) => {
        setOpenParkSlug(null);
        setDoor(null);
        setTrail(openTrail(article.slug, {
            highlight: markedWord(article.snippet, query.trim()),
        }));
    }, [query]);

    /* Шаг ВПЕРЁД по сети статей: ссылка в тексте, «Связанные материалы»,
       «Сюда ссылаются». Отличается от openArticle ровно тем, ради чего всё и
       затевалось, — предыдущая статья остаётся в цепочке, и возврат ведёт в
       неё. Заголовок и позицию прокрутки забираем в момент ухода: витрина
       заголовка открытой статьи не знает (его знает только сама статья), а
       прокрутка через мгновение схлопнется под карточку «Открываем статью…». */
    const openLinkedArticle = useCallback((slug, from = null) => {
        const leaving = { title: from?.title || null, scrollTop: portalScrollTop() };
        setTrail((prev) => pushTrail(prev, slug, leaving));
    }, []);

    /* Возврат на шаг назад: в предыдущую статью цепочки, а когда её нет — в
       дверь, через которую вошли (вкладка-источник), иначе в список витрины.
     *
     * Решение принимается ЗДЕСЬ, в теле обработчика, а не внутри апдейтера
     * setTrail: апдейтер React зовёт в фазе рендера и повторяет его в StrictMode,
     * так что setTab родителя оттуда — это «Cannot update a component while
     * rendering a different component» и двойное переключение вкладки.
     *
     * Прокрутку в двери сбрасывает РОДИТЕЛЬ. Свой эффект (ниже) здесь не
     * сработает: витрина размонтируется тем же коммитом, а эффекты
     * размонтированного не выполняются — каталог открылся бы на той прокрутке,
     * докуда дочитали статью. */
    const closeArticle = useCallback(() => {
        const exit = trail.length <= 1 ? door : null;
        setTrail(popTrail);
        if (!exit) return;
        setDoor(null);
        returnTo(exit);
    }, [trail.length, door, returnTo]);

    /* Список после статьи показываем СВЕРХУ. Открытие статьи прокрутку сбивает
       само (высота страницы схлопывается под карточку загрузки, и браузер
       обрезает scrollTop), а возврат рисуется одним коммитом из готового
       состояния — и человек попадал в витрину на той прокрутке, докуда дочитал
       статью, то есть в её самый низ. Прокрутку ВНУТРИ статьи восстанавливает
       сама статья: списку для этого нечего ждать, а ей — есть (тело приходит
       с сервера). */
    useEffect(() => {
        if (!openSlug) scrollPortalTo(0);
    }, [openSlug]);

    const spaces = structure?.spaces || [];
    const sections = structure?.sections || [];

    /* Центр показывает выдачу поиска — или ничего, когда на экране витрина
       «про меня». Короткий ввод поиска не запускает: от двух символов, как в
       оригинале. */
    const load = useCallback(() => {
        const term = query.trim();
        if (term.length < 2) { setFound(null); setFoundDirectory([]); return; }

        setLoading(true);
        /* URLSearchParams, а не объект: axios разложил бы массив как
           `article_type[]=a`, а сервер читает его через getlist — и фильтр
           молча перестал бы работать (searchFilters.js). */
        axios.get(`${base}/search`, {
            headers,
            /* limit шлём ЯВНО, хотя он совпадает с потолком сервера: цифру
               «Найдено: N» рисует витрина, и потолок обязан знать тот, кто её
               рисует, — иначе «Найдено: 20» при сорока подходящих статьях было
               бы ложью на экране (см. SEARCH_LIMIT). */
            params: searchParams(term, filters, {
                space_id: spaceId, limit: SEARCH_LIMIT,
            }),
        })
            .then((r) => {
                setFound(r.data?.items || []);
                setFoundDirectory(r.data?.directory || []);
            })
            .catch((e) => {
                setFound([]);
                setFoundDirectory([]);
                toast(errText(e, 'Поиск не сработал'), 'error');
            })
            .finally(() => setLoading(false));
        // filtersKey, а не сам объект: он пересобирается на каждый рендер и в
        // зависимостях гонял бы поиск по кругу.
    }, [base, headers, query, toast, spaceId, filtersKey(filters)]);

    useEffect(() => {
        const timer = setTimeout(load, query ? 250 : 0);   // дебаунс только на поиск
        return () => clearTimeout(timer);
    }, [load, query]);

    /* Оглавление — весь периметр, СТРАНИЦАМИ по потолку сервера.
       Раньше здесь стоял один запрос с limit: 200, и это молча обрезало правую
       колонку, как только статей стало больше: на бою из 292 статей витрины в
       оглавление доезжали 200, а разделы «Общий сотрудник» и «Оператор»
       показывали свою цифру и раскрывались пустыми. Сколько страниц забрать,
       считает fetchArticleIndex по total_visible из первого же ответа. */
    const loadIndex = useCallback(() => {
        /* Без пространства запрос вернул бы статьи ВСЕХ вики человека, а через
           полсекунды тот же список запросился бы заново, уже для одной. Ждём —
           флаг стоит в зависимостях, и первый же рендер с пространством
           запустит загрузку (см. canLoad). */
        if (!canLoad) return Promise.resolve();
        const run = indexRun.current + 1;
        indexRun.current = run;
        const mine = () => indexRun.current === run;
        setIndexLoading(true);
        return fetchArticleIndex((offset, limit) => axios
            .get(`${base}/articles`, { headers, params: { limit, offset, space_id: spaceId } })
            .then((r) => ({ items: r.data?.items || [], total: r.data?.total_visible })))
            .then((items) => { if (mine()) setIndex(items); })
            .catch(() => { if (mine()) setIndex([]); })
            .finally(() => { if (mine()) setIndexLoading(false); });
    }, [base, headers, spaceId, canLoad]);

    const loadHome = useCallback(() => {
        if (!canLoad) return Promise.resolve();   // см. loadIndex
        const run = homeRun.current + 1;
        homeRun.current = run;
        const mine = () => homeRun.current === run;
        setHomeLoading(true);
        return axios.get(`${base}/home`, { headers, params: { space_id: spaceId } })
            .then((r) => { if (mine()) setHome(r.data); })
            .catch(() => { if (mine()) setHome(null); })
            .finally(() => { if (mine()) setHomeLoading(false); });
    }, [base, headers, spaceId, canLoad]);

    // Метка — вместе с загрузчиками: они уходят за ЭТИМ пространством тем же проходом эффектов.
    useEffect(() => { loadedFor.current = spaceId; }, [spaceId]);
    useEffect(() => { loadIndex(); }, [loadIndex]);
    useEffect(() => { loadHome(); }, [loadHome]);

    /* Создатели для фильтра — по требованию, при первом раскрытии панели: список
       стоит обхода периметра, а панель раскрывает меньшинство. Отказ гасим
       молча — без списка фильтр по человеку просто не предлагается, а поиск
       работает. */
    const loadAuthors = useCallback(() => {
        setAuthorsLoading(true);
        axios.get(`${base}/search/authors`, { headers, params: { space_id: spaceId } })
            .then((r) => setAuthors(r.data?.items || []))
            .catch(() => setAuthors([]))
            .finally(() => setAuthorsLoading(false));
    }, [base, headers, spaceId]);

    // Сменилось пространство — прежние создатели не при чём: у соседней вики
    // свой периметр и свои люди, и поиск по ним всегда возвращал бы пусто.
    useEffect(() => { setAuthors([]); }, [spaceId]);

    /* Парки нужны двум местам главной — рельсу и плитке-счётчику. Если в
       пространстве выключено и то, и другое, запрос не делаем вовсе: лишний
       запрос ради того, чтобы ничего не показать.
       space_id обязателен: справочник принадлежит пространству, и без него
       рельс собрался бы из парков соседней вики. */
    const parksWanted = features?.parks !== false || features?.library_park_rail !== false;
    /* Ответ о парках принадлежит ПРОСТРАНСТВУ, за которое его спросили, и
       запоминается на время жизни страницы (entryMemory.js): на втором входе
       рельс стоит с первого кадра, а не въезжает после ответа, сдвигая центр
       витрины на свою ширину. */
    const parksKey = spaceId ? memoryKey(base, userId, 'parks', spaceId) : '';
    const [parksAnswer, setParksAnswer] = useState(() => recallParks(parksKey));   // {space, items, canManage, at}
    // Витрина уже показана (первый кадр позади) — см. ниже, у railSettled.
    const [framed, setFramed] = useState(false);
    /* Что показать: ответ для открытого пространства, а до него — запомненный.
       Пространство только что сменили в шапке и ответа ещё нет — остаётся
       прежний рельс, как и раньше: пустая колонка на полсекунды сдвинула бы
       центр влево и обратно. Только у витрины, которая уже стоит: до первого
       кадра «прежний» ответ — это ответ за пространство из догадки, и поставить
       его рельс в другое значило бы показать чужой. */
    const parksFresh = parksAnswer?.space === spaceId ? parksAnswer : recallParks(parksKey);
    const parksShown = parksWanted ? (parksFresh || (framed ? parksAnswer : null)) : null;
    const parks = parksShown?.items || NO_PARKS;
    const parksCanManage = !!parksShown?.canManage;
    /* Есть ли рельс, решает ответ о парках, а от рельса зависит, где стоит
       центр витрины. Поэтому ПЕРВЫЙ кадр ждёт этого ответа под заготовкой:
       иначе обложка с поиском вставала бы левее и через долю секунды уезжала
       вправо. Без пространства спрашивать некого — и ждать нечего. */
    const railSettled = !parksWanted || !spaceId || !!parksShown;
    /* Ждёт именно ПЕРВЫЙ кадр, и не дольше RAIL_WAIT_MS после ответа /ping.
       Рельс — справочник под рукой, а не условие работы витрины: застрявший
       запрос о парках не должен держать под заготовкой поиск и оглавление, а
       смена вики в шапке не должна на время запроса убирать с экрана уже
       стоящую витрину. Опоздавший рельс въедет, как въезжал. */
    useEffect(() => {
        if (!live || framed) return undefined;
        if (railSettled) { setFramed(true); return undefined; }
        const timer = setTimeout(() => setFramed(true), RAIL_WAIT_MS);
        return () => clearTimeout(timer);
    }, [live, railSettled, framed]);
    useEffect(() => {
        /* В пространстве без парков прежний ответ забываем: иначе, перейдя
           отсюда в третье пространство, человек увидел бы в нём рельс первого. */
        if (!parksWanted) { setParksAnswer(null); return undefined; }
        // Без пространства не спрашиваем: у того, кому выдано два, сервер
        // честно ответит «укажите пространство», и на главной мигнула бы
        // ошибка вместо рельса. С пространством — можно и до ответа /ping
        // (см. canLoad): ответ лежит за своим пространством и чужому не достанется.
        if (!canLoad || !spaceId) return undefined;
        let stale = false;
        const settle = (items, canManage) => {
            // Ответ за пространство, с которого уже ушли, рельсу не нужен.
            if (stale) return;
            const answer = { space: spaceId, items, canManage, at: Date.now() };
            remember(parksKey, answer);
            setParksAnswer((prev) => keepUnchanged(prev, answer));
        };
        axios.get(`${base}/parks`, { headers, params: { space_id: spaceId } })
            // Архивные парки сервер отдаёт управляющему справочником —
            // в рельсе им не место, это витрина «куда звонить сейчас».
            // Логотип раскрываем до абсолютного адреса: страница и API на
            // разных доменах, и относительный /api/wiki/file/<id> браузер
            // искал бы на Pages (fileUrls.js).
            .then((r) => settle(
                (r.data?.items || [])
                    .filter((p) => p.status === 'active')
                    .map((p) => ({ ...p, logo_url: absoluteFileUrl(p.logo_url, base) })),
                !!r.data?.can_manage,
            ))
            .catch((e) => {
                /* Отказ (гостю и тому, кому справочник не выдан, сервер
                   отвечает 404) — это ответ «рельса нет»: его запоминаем, иначе
                   такой человек на КАЖДОМ входе ждал бы под заготовкой запроса,
                   исход которого известен. */
                if (isRefusal(e)) { settle(NO_PARKS, false); return; }
                /* Сбой — не ответ. Что знали об этом пространстве, тому и верим дальше
                   (и памяти не трогаем); не знали ничего — рельса пока нет, а
                   первый кадр ждать больше нечего. */
                if (stale) return;
                setParksAnswer((prev) => (prev?.space === spaceId ? prev
                    : recallParks(parksKey) || { space: spaceId, items: NO_PARKS, canManage: false, at: 0 }));
            });
        return () => { stale = true; };
    }, [base, headers, parksWanted, spaceId, canLoad, parksKey]);

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
                    /* key — не украшение. Редактор держит поля в useState с
                       начальным значением из article, а ниже есть путь, где
                       article подменяется у УЖЕ смонтированного редактора:
                       «Обновить эту статью» документом делает GET и setEditing
                       (onUpdateExisting). Без key React оставляет тот же
                       инстанс, и форма показывает состояние ПРЕЖНЕЙ статьи —
                       чужое название, чужой раздел и, что хуже всего, снятую
                       защиту от копирования: сохранение молча унесло бы
                       copy_protected=false на боевой регламент. Ключ меняется
                       ровно в этом переходе — onSaved закрывает редактор
                       (setEditing(null)), так что перемонтирования на ровном
                       месте не будет. */
                    key={editing.id || 'new'}
                    base={base}
                    headers={headers}
                    showToast={showToast}
                    article={editing.id ? editing : null}
                    sections={sections}
                    spaces={spaces}
                    features={features}
                    /* Пространство работы — для журнала: пока статьи нет, её
                       пространство серверу вывести не из чего, и запись о
                       черновике попадала в журнал обоих пространств сразу. */
                    spaceId={spaceId}
                    /* Оглавление для пикера внутренних ссылок. Оно уже здесь и
                       уже сужено по пространству — своего запроса пикер не
                       делает. */
                    articles={index}
                    onPublishAsNews={publishAsNews}
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
                    /* Сохранение меняет и центр, и правую колонку: статья
                       обязана появиться на своём месте сразу, иначе после
                       сохранения кажется, что её нет. Черновик при этом
                       появится только в каталоге — в оглавлении витрины живёт
                       одно опубликованное (WikiIndexPanel). */
                    onSaved={(slug) => {
                        setEditing(null);
                        load();
                        loadIndex();
                        loadHome();
                        // Числа каталога меняются той же правкой: опубликовали
                        // черновик — «Черновиков» обязано уменьшиться сразу.
                        refreshCatalog();
                        // Дверь переживает сохранение: человек пришёл из
                        // каталога, ушёл в правку и вернулся в статью — выход
                        // у него всё тот же.
                        if (slug) setTrail(openTrail(slug));
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
                spaceId={spaceId}
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
                /* Куда уведёт возврат, знает цепочка: в предыдущую статью, а
                   когда её нет — в список. Подпись кнопки берётся отсюда же,
                   чтобы обещание на кнопке и её действие не могли разойтись. */
                backTo={backEntry || door}
                restoreScroll={openEntry?.scrollTop || 0}
                onBack={closeArticle}
                /* Ссылка на другую статью внутри текста открывает её здесь же
                   (см. articleLink.js), без перезагрузки портала, и ШАГОМ
                   ВПЕРЁД по цепочке — статья, из которой ушли, остаётся на
                   расстоянии одной кнопки. */
                onOpenArticle={openLinkedArticle}
                /* Статья приходит с сервера целиком (content, разделы, флаги),
                   поэтому редактору не нужен второй запрос — открываем прямо
                   на том объекте, который человек сейчас читает. */
                onEdit={(article) => setEditing(article)}
                onPublishAsNews={publishAsNews}
                onArchived={() => {
                    // Уходим с закрытой статьи: она только что ушла из витрины,
                    // и оставлять её на экране значит показывать то, чего в
                    // вике уже нет. Выход ТОТ ЖЕ, что у кнопки возврата: два
                    // выхода с одного экрана не должны вести в разные места —
                    // архив разбирают из каталога, туда и возвращаемся.
                    closeArticle();
                    load();
                    loadIndex();
                    loadHome();
                    refreshCatalog();
                }}
            />
        );
    }

    /* Статья, парк и редактор — выше этих строк: им пространство не нужно, и
       статья по ссылке открывается, не дожидаясь /ping.
       Отказ /ping показывает карточка раздела — под ней витрине сказать нечего. */
    if (unavailable) return null;
    /* Всё ниже — только ДО первого кадра. Витрину, которая уже стоит, ничем не
       подменяем: «с неё уходят» длится один коммит, и мигнуть ради него
       заготовкой или пустым местом хуже, чем дать ей достоять этот кадр. */
    if (!framed) {
        /* Уходят, не успев её показать, — рисовать нечего: ни её, ни её
           заготовку. Три серых блока в форме главной, на месте которых встают
           «Офисы», — то же «сначала одно, потом другое»; пока ответа нет,
           место держит нейтральная заглушка раздела. */
        if (leaving) return null;
        // Нет прав и пространства — либо первый кадр ждёт ответа о парках.
        if (!(ready && railSettled)) return <LibrarySkeleton />;
    }

    // Подборки и оглавление в состоянии — ещё за прежнее пространство (см. loadedFor).
    const otherSpace = loadedFor.current !== spaceId;
    const homePending = homeLoading || catalogPending || otherSpace;

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
                <section className={`${iosCard} wiki-m-hero px-5 py-6 text-center sm:px-8`}>
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
                        {/* Кнопка фильтров — у правого края САМОГО поля, как и в
                            поиске шапки раздела. Раньше и она, и панель ждали
                            запроса: «пока искать не начали, фильтровать нечего».
                            Возражение было про кнопку НА ВИТРИНЕ — отдельно
                            стоящая, она читалась как настройки раздела. Внутри
                            поля она часть поиска, и порядок наконец обычный:
                            сузил, потом набрал. */}
                        <SearchFilterButton
                            className="-mr-1"
                            value={filters}
                            open={filtersOpen}
                            onOpenChange={setFiltersOpen}
                        />
                    </div>

                    {/* Панель и чипы — без оглядки на запрос: своя пустота
                        забота самой панели (свёрнутая и без выбранного она не
                        рисует ничего). */}
                    <WikiSearchFilters
                        className="mx-auto mt-2.5 max-w-[560px] text-left"
                        value={filters}
                        onChange={setFilters}
                        open={filtersOpen}
                        onNeedAuthors={loadAuthors}
                        authors={authors}
                        authorsLoading={authorsLoading}
                    />

                </section>


                {busy && (
                    <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-slate-400`}>
                        <Loader2 size={16} className="animate-spin" />
                        <span className="text-[13px]">Загружаем…</span>
                    </div>
                )}

                {!busy && searching && (
                    found.length > 0 || shownDirectory.length > 0 ? (
                        <div className="space-y-2.5">
                            {/* Справочник — над статьями, как в поиске шапки: на
                                «офис алматы» адрес и есть ответ. */}
                            {shownDirectory.length > 0 && (
                                <div className={`${iosCard} p-1.5`}>
                                    <ul className="space-y-0.5">
                                        {shownDirectory.map((entry) => (
                                            <DirectoryRow
                                                key={directoryKey(entry)}
                                                entry={entry}
                                                onPick={() => (directoryTab(entry) === 'offices'
                                                    ? openOffice(entry) : openCity(entry))}
                                            />
                                        ))}
                                    </ul>
                                </div>
                            )}
                            {found.length > 0 && (
                                <div className={iosGroupLabel}>
                                    Найдено: {found.length}{found.length >= SEARCH_LIMIT ? '+' : ''}
                                </div>
                            )}
                            {found.map((article) => (
                                <ArticleCard key={article.id} article={article}
                                             onOpen={() => openHit(article)} />
                            ))}
                            {/* Статьи нашлись, но ответа в них может и не быть:
                                выход к помощнику остаётся под выдачей — тихой
                                строкой, тем же видом, что и в поиске шапки. */}
                            {canAskAssistant && (
                                <div className={`${iosCard} p-1.5`}>
                                    <AskAssistantRow
                                        term={query.trim()}
                                        onAsk={() => askAssistant(query.trim())}
                                    />
                                </div>
                            )}
                        </div>
                    ) : canAskAssistant ? (
                        <div className={iosCard}>
                            <AskAssistantEmpty
                                term={query.trim()}
                                onAsk={() => askAssistant(query.trim())}
                                note="Или попробуйте другое слово: поиск понимает опечатки, латиницу и забытую раскладку."
                            />
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
                    и до ответа сервера они неправда.
                    Редактор ждёт ещё и каталога: плитки-счётчики берут числа
                    оттуда, и, приехав вторыми, вставали над уже показанными
                    полками и сдвигали их вниз. */}
                {!busy && !searching && (
                    homePending ? (
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

            {/* На телефоне и планшете оглавление стоит ПОД подборками. Пока они
                грузятся, на их месте заглушка в 220 px — и оглавление вставало
                на первом экране, чтобы с ответом улететь вниз на 600–700 px.
                До ответа его там не показываем; на компьютере оно в своей
                колонке и никуда не едет. `contents` — обёртка без своей рамки:
                колонки витрины раскладываются так, будто её нет. */}
            <div className={homePending ? 'hidden lg:contents' : 'contents'}>
                <WikiIndexPanel
                    tree={tree}
                    articles={index}
                    onOpen={openArticle}
                    /* Оглавление — это дерево И список статей. Пришёл один
                       список — панель показала бы все статьи одной группой «Без
                       раздела», а с деревом тут же разложила бы их по разделам. */
                    loading={indexLoading || structurePending || otherSpace}
                />
            </div>
        </div>
    );
}
