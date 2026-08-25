import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { motion, useReducedMotion } from 'framer-motion';
import {
    AlertCircle, ArrowDownToLine, BookOpen, FileText, FolderTree, Gamepad2, Home, KeyRound,
    Layers, MapPin,
    Network,
    Building2, ChevronDown, LineChart, Loader2, Pencil, Plus, RefreshCw, ScrollText,
    ShieldCheck, Sparkles, Users,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, IosBadge,
} from '../ui/ios';
import WikiLibrary from './WikiLibrary';
import WikiCatalog, { BucketSwitch } from './WikiCatalog';
import WikiParks from './WikiParks';
import WikiOffices from './WikiOffices';
import WikiStructure from './WikiStructure';
import WikiTrainers from './WikiTrainers';
import WikiGuests from './WikiGuests';
import WikiGuestBanner from './WikiGuestBanner';
import WikiMigration from './WikiMigration';
import WikiAudit from './WikiAudit';
import WikiAnalytics from './WikiAnalytics';
import WikiSearch from './WikiSearch';
import WikiSpaceModal from './WikiSpaceModal';
import { effectiveFeatures } from './spaceFeatures';
const WikiAssistant = lazy(() => import('./WikiAssistant'));
import { CLASSIFIER_SLUG } from './WikiArticle';
import { getScrollContainer } from './scrollContainer';
import { CAPABILITY_LABELS } from './sectionGrants';
import './wiki-theme.css';

/* Раздел «Вики» — корпоративная база знаний.
 *
 * Этап 2: структура (пространства → разделы) и выдача доступов. Статьи и поиск
 * приходят на этапах 3 и 5.
 *
 * Периметр доступа показан пользователю прямо в интерфейсе. Модель прав здесь
 * глубже, чем в остальных разделах: способности роли, правила разделов, правила
 * отдельных статей и запреты поверх них. Когда уровней четыре, вопрос «почему я
 * этого не вижу» возникает неизбежно, и ответ должен быть в интерфейсе, а не
 * в логах.
 *
 * Тёмной темы нет намеренно: портал светлый, а классы dark:* из исходной вики
 * сработали бы от темы системы — darkMode в tailwind.config.cjs не задан,
 * значит Tailwind работает в режиме media.
 */

const ACCESS_MODE_LABELS = { auto: 'Автоматический', manual: 'Ручная выдача' };

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const StatTile = ({ icon: Icon, value, label }) => (
    <div className="flex items-center gap-3 px-4 py-3.5">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-indigo-50 text-indigo-600">
            <Icon size={17} />
        </div>
        <div className="min-w-0">
            <div className="text-[19px] font-semibold leading-none text-slate-900 tabular-nums">{value}</div>
            <div className="mt-1 truncate text-[12px] text-slate-500">{label}</div>
        </div>
    </div>
);

/* Переключатель половин вкладки «Статьи»: смотреть содержимое или править
   разложение. Сегмент-контрол, а не две кнопки: это выбор ОДНОГО из двух
   состояний одного экрана, и он обязан выглядеть так же, как переключатель
   корзин ниже, — иначе два ряда пилюль читаются как разные механизмы. */
const MODES = [
    { key: 'catalog', label: 'Статьи', icon: BookOpen },
    { key: 'structure', label: 'Структура', icon: Network },
    /* Тренажёры — третья половина той же работы: «что лежит», «как разложено»
       и «чем это отрабатывают». Отдельным пунктом меню они стали бы четвёртой
       вкладкой с двумя карточками внутри. */
    { key: 'trainers', label: 'Тренажёры', icon: Gamepad2 },
    /* Гостевой доступ — четвёртая половина той же работы. «Что лежит», «как
       разложено», «чем это отрабатывают» и «кому ещё это показать». Отдельным
       пунктом меню он стал бы пятой вкладкой с одной таблицей внутри, и
       открывали бы его так же редко, как любой пункт, который надо вспомнить. */
    { key: 'guests', label: 'Гостевой доступ', icon: KeyRound },
    /* Перенос — половина ВРЕМЕННАЯ: она есть, только пока в очереди есть
       неразобранные статьи из старой вики (см. catalogModes ниже). Разберут
       очередь — половина исчезнет сама, и переключатель вернётся к трём
       кнопкам. Постоянная кнопка, которая одиннадцать месяцев в году открывает
       «ничего нет», — это ровно тот шум, которого в разделе быть не должно. */
    { key: 'migration', label: 'Перенос', icon: ArrowDownToLine },
];

/* Порядок половин — он же направление, с которого приезжает содержимое. */
const MODE_ORDER = MODES.map((mode) => mode.key);

const ModeSwitch = ({ value, onChange, allowed }) => (
    <div className="inline-flex max-w-full gap-1 overflow-x-auto rounded-2xl bg-slate-100 p-1">
        {MODES.filter((m) => allowed.includes(m.key)).map(({ key, label, icon: Icon }) => {
            const on = value === key;
            return (
                <button
                    key={key}
                    type="button"
                    aria-pressed={on}
                    onClick={() => onChange(key)}
                    className={`flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-[13px] font-medium transition ${
                        on ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                    }`}
                >
                    <Icon size={14} className={on ? 'text-indigo-600' : ''} /> {label}
                </button>
            );
        })}
    </div>
);

/* Переключатель пространств — правее заголовка раздела.
 *
 * Выпадающий список, а не ряд пилюль: пространств у клиента бывает и одно, и
 * десять, а ряд пилюль на десяти именах отжимает поиск и «Обновить» на вторую
 * строку. Одно пространство — списка нет вовсе: выбор из одного варианта это
 * подпись, притворяющаяся управлением.
 */
const SpaceSwitch = ({ spaces, value, onChange, onCreate, onEdit }) => {
    const [open, setOpen] = useState(false);
    const boxRef = useRef(null);
    const current = spaces.find((sp) => sp.id === value) || spaces[0];

    /* Закрытие по клику мимо вешаем ТОЛЬКО пока список раскрыт: постоянный
       слушатель на документе висел бы на каждом экране раздела ради окна,
       которое открывают раз в месяц. */
    useEffect(() => {
        if (!open) return undefined;
        const away = (event) => {
            if (!boxRef.current?.contains(event.target)) setOpen(false);
        };
        document.addEventListener('mousedown', away);
        return () => document.removeEventListener('mousedown', away);
    }, [open]);

    if (!current) return null;

    return (
        <div className="flex items-center gap-1.5">
            <div ref={boxRef} className="relative">
                <button
                    type="button"
                    onClick={() => spaces.length > 1 && setOpen((x) => !x)}
                    aria-haspopup={spaces.length > 1 ? 'listbox' : undefined}
                    aria-expanded={spaces.length > 1 ? open : undefined}
                    className={`flex max-w-[220px] items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 text-[13px] font-semibold text-slate-800 transition ${
                        spaces.length > 1 ? 'hover:bg-slate-200 active:scale-[0.98]' : 'cursor-default'
                    }`}
                >
                    <span className="min-w-0 truncate">{current.name}</span>
                    {spaces.length > 1 && (
                        <ChevronDown size={13}
                            className={`shrink-0 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
                    )}
                </button>

                {open && (
                    <div
                        role="listbox"
                        className="absolute left-0 top-full z-30 mt-1.5 w-[240px] overflow-hidden rounded-2xl bg-white/95 p-1 shadow-xl ring-1 ring-slate-900/10 backdrop-blur-xl"
                    >
                        {spaces.map((sp) => (
                            <button
                                key={sp.id}
                                type="button"
                                role="option"
                                aria-selected={sp.id === current.id}
                                onClick={() => { onChange(sp.id); setOpen(false); }}
                                className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[13px] transition ${
                                    sp.id === current.id
                                        ? 'bg-slate-100 font-semibold text-slate-900'
                                        : 'text-slate-700 hover:bg-slate-50'
                                }`}
                            >
                                <span className="min-w-0 flex-1 truncate">{sp.name}</span>
                            </button>
                        ))}
                    </div>
                )}
            </div>

            {/* «+» и «править» показываем только тому, кто вправе: сервер
                отвечает 403, и кнопка, ведущая в отказ, — это брак. */}
            {onCreate && (
                <button
                    type="button"
                    onClick={onCreate}
                    aria-label="Новое пространство"
                    title="Новое пространство"
                    className="grid h-7 w-7 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-95"
                >
                    <Plus size={14} />
                </button>
            )}
            {onEdit && (
                <button
                    type="button"
                    onClick={onEdit}
                    aria-label="Настроить пространство"
                    title="Настроить пространство"
                    className="grid h-7 w-7 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-95"
                >
                    <Pencil size={13} />
                </button>
            )}
        </div>
    );
};

export default function WikiView({ apiBaseUrl, withAccessTokenHeader, showToast, user,
                                   initialArticleSlug, onInitialArticleConsumed }) {
    const headers = useMemo(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    const base = `${apiBaseUrl}/api/wiki`;

    const [state, setState] = useState(null);
    const [structure, setStructure] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [structureLoading, setStructureLoading] = useState(true);
    const [tab, setTab] = useState('library');
    const [searchTarget, setSearchTarget] = useState(null);   // {slug, highlight}
    /* Вопрос, уехавший из поиска к помощнику. Просьба одноразовая, как
       createRequest: помощник её выполняет и гасит. Своим id, а не текстом —
       один и тот же запрос, заданный дважды подряд, обязан уйти дважды. */
    const [assistantAsk, setAssistantAsk] = useState(null);   // {id, text}
    /* Каталог разделов — данные вкладки «Статьи». Живут ЗДЕСЬ, а не в ней, по
       двум причинам: счётчики на главной берут из них свои числа (иначе «29
       статей» и список за плиткой считались бы разными запросами и разошлись),
       и «Обновить» в шапке обязана обновлять и их тоже. */
    const [catalog, setCatalog] = useState(null);
    const [catalogLoading, setCatalogLoading] = useState(true);
    /* Корзина каталога тоже здесь: на неё нажимают со СЧЁТЧИКОВ главной
       («9 черновиков» открывает каталог сразу в черновиках), а вкладка при
       уходе размонтируется и локальное состояние потеряла бы. */
    const [catalogBucket, setCatalogBucket] = useState('published');
    /* Что показывает вкладка «Статьи»: каталог или правку структуры. Отдельной
       вкладки «Структура» больше нет — по решению владельца обе половины одной
       работы («что лежит» и «как разложено») собраны под одним пунктом меню и
       переключаются здесь. */
    const [catalogMode, setCatalogMode] = useState('catalog');
    /* Просьба открыть редактор новой статьи. Кнопка живёт в шапке раздела и
       работает с ДВУХ вкладок — с главной и из каталога, — а сам редактор
       принадлежит витрине статей.
       Просьба одноразовая, как initialSlug, а не счётчик нажатий: при переходе
       из каталога витрина монтируется заново и уже с новым значением счётчика,
       то есть сравнивать его было бы не с чем — кнопка молча не срабатывала бы.
       Гасит просьбу тот, кто её выполнил. */
    const [createRequest, setCreateRequest] = useState(null);
    /* Правка статьи из каталога. Редактор там не живёт (в каталоге нет ни
       читалки, ни оглавления), поэтому просьба уезжает на витрину — тем же
       путём, каким из каталога открывается сама статья. */
    const [editTarget, setEditTarget] = useState(null);   // {slug}
    // Тем же способом заголовок раздела возвращает витрину на главную.
    const [homeTick, setHomeTick] = useState(0);
    /* Выбранное пространство. Переживает перезаход в раздел: человек работает
       в одной вике неделями, и заново выбирать её при каждом входе — работа
       вместо результата. Храним ИДЕНТИФИКАТОР, а не объект: список приходит с
       сервера, и сохранённая копия устарела бы при первом переименовании. */
    const [spaceId, setSpaceId] = useState(() => {
        const saved = Number(localStorage.getItem('wiki:space'));
        return Number.isFinite(saved) && saved > 0 ? saved : null;
    });
    const [spaceModal, setSpaceModal] = useState(null);   // {mode:'create'|'edit'}
    /* Отделы для конструктора. Тянем их ОДИН раз на раздел, а не при каждом
       открытии окна: список отделов компании меняется раз в квартал. */
    const [departments, setDepartments] = useState([]);
    const rootRef = useRef(null);
    /* prefers-reduced-motion: CSS-переходы глушит правило в теме, но framer
       пишет инлайновые стили и под него не подпадает — нужен свой флаг.
       Тот же приём, что в поиске раздела (WikiSearch). */
    const reduceMotion = useReducedMotion();

    /* Способности считаем ДО загрузчиков: loadCatalog держит isEditor в
       списке зависимостей, а const в теле компонента до своей строки лежит во
       временной мёртвой зоне — объявление ниже уронило бы раздел. */
    const capabilities = state?.capabilities || {};
    const granted = Object.keys(CAPABILITY_LABELS).filter((key) => capabilities[key]);
    const counters = state?.counters;
    const subjects = state?.subjects || {};
    const canManageStructure = !!(capabilities.can_manage_structure || capabilities.can_manage_access);
    const canManageAccess = !!capabilities.can_manage_access;
    /* Право раздавать доступ живёт отдельно от способностей: у супервайзера нет
       ни can_manage_structure, ни can_manage_access, но операторов он раздаёт —
       значит вкладку «Структура» ему показать надо, пусть и без правки дерева. */
    const canGrantAccess = state?.grant_ceiling != null;
    /* Право выдавать ГОСТЕВОЙ доступ. Считает сервер и присылает готовым
       признаком: право адресное — оно выписано на конкретной ветке правилом
       (wiki_section_access_rules.can_grant_guest), — и в словаре способностей
       его нет вовсе. Вывести его здесь было бы вторым источником истины. */
    const canGrantGuest = !!state?.can_grant_guest;
    /* Что открыто МНЕ и до какого срока. Едет тем же ответом /ping: срок обязан
       быть виден на любой вкладке, а второй запрос ради подписи в шапке дал бы
       вкладку, на которой подпись почему-то не появляется. */
    const guestGrants = useMemo(() => state?.guest_access || [], [state]);
    const canEdit = !!(capabilities.can_edit || capabilities.can_publish);
    /* Каталог — инструмент того, кто ведёт базу знаний: он показывает разом
       черновики, архив и объём каждого раздела. Читателю всё это не нужно, и
       по решению владельца вкладку ему не показываем. Формула та же, что у
       счётчиков на главной (WikiLibrary: isEditor), — «редактор» обязан
       означать одно и то же во всём разделе. */
    const isEditor = !!(capabilities.can_create || canEdit);

    /* Пространства приходят из ping вместе со способностями: набор вкладок
       нужен раньше дерева разделов, иначе «Помощник» мигнул бы у того, кому
       его выключили. */
    const spaces = useMemo(() => state?.spaces || [], [state]);
    /* Выбранное пространство может исчезнуть между заходами — его убрали в
       архив или закрыли от отдела. Тогда берём первое доступное, а не пустоту:
       раздел обязан открыться. */
    const activeSpace = useMemo(
        () => spaces.find((sp) => sp.id === spaceId) || spaces[0] || null,
        [spaces, spaceId],
    );
    const features = useMemo(() => effectiveFeatures(activeSpace), [activeSpace]);

    /* Пространство ДЛЯ КОНСТРУКТОРА берём из /structure, а не из ping: в ping
       лежит только то, что нужно шапке (имя и тумблеры), без списка отделов —
       знать, кому ещё выдана вика, читателю незачем. Окно правки открывает
       супер-админ, и полную карточку ему отдаёт /structure. */
    const editableSpace = useMemo(
        () => (structure?.spaces || []).find((sp) => sp.id === activeSpace?.id) || activeSpace,
        [structure, activeSpace],
    );

    /* Дерево, суженное до активного пространства. Витрина, каталог, оглавление
       и журнал получают ИМЕННО его: сервер отдаёт всё, к чему у человека есть
       доступ (в том числе соседнее пространство у супер-админа), а на экране
       одновременно живёт ровно одно — то, что выбрано переключателем.
       Сужаем здесь, в одном месте, а не в каждой витрине: пять независимых
       фильтров по space_id разъедутся на первой же новой витрине. */
    const scopedStructure = useMemo(() => {
        if (!structure) return structure;
        if (!activeSpace) return structure;
        return {
            ...structure,
            spaces: (structure.spaces || []).filter((sp) => sp.id === activeSpace.id),
            sections: (structure.sections || [])
                .filter((x) => x.space_id === activeSpace.id),
        };
    }, [structure, activeSpace]);
    const canManageSpaces = !!structure?.can_manage_spaces;

    /* Запоминаем ФАКТИЧЕСКИ показанное пространство, а не то, что попросили:
       после архивации выбранного иначе сохранился бы идентификатор, которого
       больше нет, и следующий заход снова начинался бы с подстановки.
       Выбор при этом НЕ переписываем: подстановка живёт в activeSpace и на
       каждом рендере считается заново. Запись сюда ломала создание — только
       что заведённое пространство ещё не приехало в ping, подстановка честно
       давала первое из старого списка, и она же затирала выбор новым. Человек
       нажимал «Создать» и оставался в прежней вике. */
    useEffect(() => {
        if (activeSpace) localStorage.setItem('wiki:space', String(activeSpace.id));
    }, [activeSpace]);

    const loadPing = useCallback(() => {
        setLoading(true);
        return axios.get(`${base}/ping`, { headers })
            .then((r) => { setState(r.data); setError(''); })
            .catch((e) => { setState(null); setError(errText(e, 'Не удалось связаться с разделом')); })
            .finally(() => setLoading(false));
    }, [base, headers]);

    const loadStructure = useCallback(() => {
        setStructureLoading(true);
        return axios.get(`${base}/structure`, { headers })
            .then((r) => setStructure(r.data))
            .catch(() => setStructure(null))
            .finally(() => setStructureLoading(false));
    }, [base, headers]);

    /* Каталог грузим только редактору: и вкладка, и счётчики на главной
       принадлежат ему одному, а сервер читателю всё равно ответит 403. */
    const loadCatalog = useCallback(() => {
        if (!isEditor) { setCatalog(null); setCatalogLoading(false); return Promise.resolve(); }
        setCatalogLoading(true);
        /* Просим ФАКТИЧЕСКИ показанное пространство, а не сохранённое: до
           первого ответа ping сохранённого может не быть вовсе, и каталог
           пришёл бы за всю вику, а потом перезапросился. */
        return axios.get(`${base}/catalog`,
                         { headers, params: { space_id: activeSpace?.id || null } })
            .then((r) => setCatalog(r.data))
            .catch(() => setCatalog(null))
            .finally(() => setCatalogLoading(false));
    }, [base, headers, isEditor, activeSpace]);

    useEffect(() => { loadPing(); loadStructure(); }, [loadPing, loadStructure]);

    /* Отделы — только тому, кто настраивает пространства: остальным этот
       запрос вернёт 403 и добавит красную строку в консоль на каждом заходе. */
    useEffect(() => {
        if (!canManageSpaces) return;
        axios.get(`${base}/access/subjects`, { headers })
            .then((r) => setDepartments(r.data?.department || []))
            .catch(() => setDepartments([]));
    }, [base, headers, canManageSpaces]);
    // Отдельным эффектом: способности приходят из ping, то есть ПОЗЖЕ первого
    // рендера, и общий эффект пропустил бы загрузку каталога у редактора.
    useEffect(() => { loadCatalog(); }, [loadCatalog]);

    /* Вкладку показывают ДВА независимых условия: способности человека и
       тумблеры пространства. Их нельзя складывать в одно — они отвечают на
       разные вопросы («вправе ли он» и «есть ли это здесь вообще»), и,
       слитые, однажды прочитаются как «нет права» там, где просто выключено. */
    const tabs = useMemo(() => ([
        { key: 'library', label: 'Главная', icon: Home, show: true },
        // Показан всем: периметр считает сервер, и гейт по способности здесь
        // был бы вреден — он мигал бы false во время загрузки ping, а эффект
        // ниже выкидывал бы человека из открытого чата.
        { key: 'assistant', label: 'Помощник', icon: Sparkles, show: features.assistant },
        /* Каталог и правка структуры — один пункт меню: «что лежит в разделе»
           и «как разделы устроены» это две половины одной работы, и раньше
           между ними приходилось прыгать по вкладкам. Внутри — переключатель.
           Показываем тому, у кого есть хоть одна из половин. */
        { key: 'catalog', label: 'Статьи', icon: BookOpen,
          /* canGrantGuest — четвёртый вход, и он не лишний: право выдавать
             гостевой доступ выписывают правилом раздела кому угодно, в том
             числе тренеру, у которого нет ни одной из трёх остальных дверей.
             Без него вкладка не появилась бы, а половина внутри неё — тем
             более. */
          show: features.catalog && (isEditor || canManageStructure
                                     || canGrantAccess || canGrantGuest) },
        { key: 'overview', label: 'Обзор', icon: ShieldCheck, show: features.overview },
        { key: 'parks', label: 'Парки', icon: Building2, show: features.parks },
        { key: 'offices', label: 'Офисы', icon: MapPin, show: features.offices },
        // Отдельных вкладок «Структура» и «Доступы» нет: структура переехала
        // внутрь «Статей» переключателем, а права выдаются из строки раздела
        // там же. Раздел выбран тем, что человек на него нажал, а не селектом
        // из плоского списка, где ветки СЗоВ и ОП одноимённые.
        /* Аналитика — редактору: отчёт про базу знаний нужен тому, кто её
           ведёт, а не только администратору доступов (решение владельца).
           Формула редактора тут та же, что у каталога и у сервера
           (routes_analytics: _is_editor), — иначе вкладка появлялась бы в меню
           у того, кому сервер отвечает 403. Администратора доступов без прав
           правки пропускаем отдельно: вкладка у него уже была.
           Стоит перед журналом: отчёт открывают регулярно, аудит — по поводу. */
        { key: 'analytics', label: 'Аналитика', icon: LineChart,
          show: features.analytics && (isEditor || canManageAccess) },
        { key: 'audit', label: 'Журнал', icon: ScrollText,
          show: features.audit && canManageAccess },
    ].filter((t) => t.show)),
    [canManageStructure, canManageAccess, canGrantAccess, canGrantGuest,
     isEditor, features]);

    /* Поиск предлагает спросить помощника ровно тогда, когда вкладка помощника
       вообще есть: у пространства без неё это была бы кнопка в никуда.
       Периметр и готовность индекса проверяет уже сам помощник — знать о них
       поиску не нужно, а лишний запрос /ai/status на каждый заход в раздел
       стоил бы дороже редкого перехода на пустой чат. */
    const canAskAssistant = useMemo(
        () => tabs.some((t) => t.key === 'assistant'), [tabs]);

    const askAssistant = useCallback((question) => {
        const text = String(question || '').trim();
        if (!text) return;
        setTab('assistant');
        setAssistantAsk({ id: `${Date.now()}-${text}`, text });
    }, []);

    /* Половины вкладки «Статьи» гейтятся по отдельности: каталог — редактору,
       структура — тому, кто правит дерево или раздаёт доступы. Обычно человек
       имеет обе (см. матрицу в wiki/access.py), но роль вики можно собрать
       руками, и на такой сборке половина обязана просто не появиться. */
    const catalogModes = useMemo(() => [
        ...(isEditor && features.catalog_articles ? ['catalog'] : []),
        ...((canManageStructure || canGrantAccess) && features.catalog_structure
            ? ['structure'] : []),
        // Тренажёры — редактору: это инструмент того, кто СТАВИТ тренажёр в
        // статью. Читателю он не нужен, тренажёр к нему приходит кнопкой в тексте.
        ...(isEditor && features.catalog_trainers ? ['trainers'] : []),
        /* Гостевой доступ — тому, кто РАЗДАЁТ, а не тому, кто пишет: это выдача
           доступа, и редактор без права выдавать ничего бы здесь не сделал.
           Право адресное и живёт в правиле раздела, поэтому его считает сервер
           и присылает признаком can_grant_guest — вывести его из способностей
           нельзя. Администратора доступов пропускаем отдельно: у него мастер-ключ,
           и половина ему нужна независимо от правил.
           Половина видна и тому, у кого право уже сняли: свои прошлые выдачи
           надо иметь возможность отозвать. */
        ...((canGrantGuest || canManageAccess) && features.catalog_guests
            ? ['guests'] : []),
        /* Перенос — по ОСТАТКУ работы, а не по тумблеру пространства: это разовая
           процедура, а не часть раздела, и настраивать её видимость незачем.
           Число берём из каталога — он уже посчитал периметр, и половина
           появляется ровно тогда, когда за ней есть что показать. */
        ...(isEditor && (catalog?.migration?.pending || 0) > 0 ? ['migration'] : []),
    ], [isEditor, canManageStructure, canGrantAccess, canGrantGuest, canManageAccess,
        features, catalog]);

    /* Сторона, с которой въезжает выбранная половина. Предыдущую держим в ref,
       а не в состоянии: она нужна только для стартового смещения анимации, и
       лишний рендер на её запись был бы чистой платой ни за что. */
    const previousMode = useRef(catalogMode);
    const modeSlide = MODE_ORDER.indexOf(catalogMode) >= MODE_ORDER.indexOf(previousMode.current)
        ? 16 : -16;
    useEffect(() => { previousMode.current = catalogMode; }, [catalogMode]);

    // Доступная человеку половина может не совпасть с выбранной — например,
    // права сузились между заходами.
    useEffect(() => {
        if (catalogModes.length && !catalogModes.includes(catalogMode)) {
            setCatalogMode(catalogModes[0]);
        }
    }, [catalogModes, catalogMode]);

    // Если права сузились между заходами, активная вкладка может исчезнуть.
    useEffect(() => {
        if (tabs.length && !tabs.some((t) => t.key === tab)) setTab('library');
    }, [tabs, tab]);

    /* Пришли по уведомлению об ознакомлении — открываем вкладку со статьями,
       даже если в прошлый раз ушли, например, в «Структуру». */
    useEffect(() => {
        if (initialArticleSlug) setTab('library');
    }, [initialArticleSlug]);

    const refresh = () => {
        Promise.all([loadPing(), loadStructure(), loadCatalog()])
            .then(() => showToast?.('Обновлено', 'success'));
    };

    /* Заголовок раздела работает как логотип сайта: возвращает на главную вики
       из статьи, из выбранного раздела и с любой вкладки. Прокрутку сбрасываем
       сами — скроллится .main-content портала, а не окно (scrollContainer.js). */
    const goHome = () => {
        setTab('library');
        setSearchTarget(null);
        setHomeTick((n) => n + 1);
        getScrollContainer(rootRef.current)?.scrollTo({ top: 0, behavior: 'smooth' });
    };

    return (
        <div
            ref={rootRef}
            className="wiki-scope min-h-full bg-slate-50 px-4 pb-10 pt-[68px] sm:px-6 min-[769px]:pt-8"
            style={{ fontFamily: APPLE_FONT }}
        >
            {/* Ширина растёт со экраном: на 5xl (1024px) справочная таблица из
                шести колонок уже не помещалась и схлопывалась. Ступени, а не
                «во всю ширину»: строка текста длиной в монитор нечитаема.
                Витрине статей эти же ступени дают три колонки: рельс парков,
                центр и оглавление. */}
            <div className="mx-auto w-full max-w-5xl space-y-5 xl:max-w-6xl 2xl:max-w-[88rem]">

                {/* Отступ сверху на мобильном — под фиксированный гамбургер портала
                    (44×44, z-index 60), иначе он накроет заголовок. */}
                <header className="flex flex-wrap items-center gap-3">
                    {/* Кликабельный заголовок — не <button>: внутри лежит <h1>, а
                        заголовок внутри кнопки невалиден и теряется для screen
                        reader'ов. Поэтому role/tabIndex и клавиши вручную. */}
                    <div
                        role="button"
                        tabIndex={0}
                        aria-label="Вики: на главную раздела"
                        onClick={goHome}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goHome(); }
                        }}
                        className="-m-1 flex cursor-pointer items-center gap-3 rounded-2xl p-1 transition hover:opacity-75 active:scale-[0.99]"
                    >
                        <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-indigo-600 text-white shadow-sm">
                            <BookOpen size={21} />
                        </div>
                        <div>
                            <h1 className="text-[22px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
                                Вики
                            </h1>
                            <p className="text-[13px] text-slate-500">База знаний компании</p>
                        </div>
                    </div>

                    {/* Переключатель пространств — сразу за названием раздела:
                        он отвечает на вопрос «чья это вика», и ответ обязан
                        стоять рядом с самим заголовком, а не в настройках. */}
                    {spaces.length > 0 && (
                        <SpaceSwitch
                            spaces={spaces}
                            value={activeSpace?.id}
                            onChange={setSpaceId}
                            onCreate={canManageSpaces
                                ? () => setSpaceModal({ mode: 'create' }) : null}
                            onEdit={canManageSpaces
                                ? () => setSpaceModal({ mode: 'edit' }) : null}
                        />
                    )}

                    <div className="flex w-full flex-wrap items-center gap-2 sm:ml-auto sm:w-auto">
                        {/* Поиск живёт прямо в шапке: поле растёт при фокусе, выдача
                            выпадает под ним. Так он доступен с любой вкладки раздела. */}
                        <WikiSearch
                            base={base}
                            headers={headers}
                            spaceId={activeSpace?.id || null}
                            onOpenArticle={(slug, highlight) => {
                                setTab('library');
                                setSearchTarget({ slug, highlight });
                            }}
                            onOpenClassifier={(prefill) => {
                                // Классификатор — теперь статья этой же вики,
                                // поэтому никуда из раздела не уходим.
                                setTab('library');
                                setSearchTarget({ slug: CLASSIFIER_SLUG, prefill });
                            }}
                            onAskAssistant={canAskAssistant ? askAssistant : null}
                        />

                        <button type="button" onClick={refresh} className={iosBtnSecondary}>
                            <RefreshCw size={15} /> Обновить
                        </button>

                        {/* Действие вкладки со статьями — в шапке рядом с «Обновить»,
                            как в макете.
                            Показываем на ДВУХ вкладках: на главной и в каталоге.
                            Каталог — рабочее место того, кто ведёт базу знаний:
                            он разбирает там разделы и черновики, и «создать
                            статью» ему нужно ровно оттуда, а не после
                            возвращения на главную. Место у кнопки при этом одно
                            и то же — на другие вкладки она не выходит, потому
                            что редактор принадлежит витрине статей. */}
                        {/* В ГОСТЕВОМ пространстве кнопки нет. Способность
                            can_create приходит от должности (тренер, СВ и выше)
                            и отдела не знает, а гостя позвали ПРОЧИТАТЬ один
                            раздел: правил на запись у него там нет, и сервер
                            ответит «нет права создавать статьи в …». Кнопка,
                            которая всегда отказывает, — это и есть мёртвая
                            кнопка. */}
                        {capabilities.can_create && !activeSpace?.guest_only
                            && (tab === 'library'
                                || (tab === 'catalog' && catalogMode === 'catalog')) && (
                            <button
                                type="button"
                                onClick={() => { setTab('library'); setCreateRequest({}); }}
                                className={iosBtnPrimary}
                            >
                                <Plus size={15} /> Новая статья
                            </button>
                        )}
                    </div>
                </header>

                {/* Свой срок человек видит РАНЬШЕ вкладок и независимо от них:
                    гость приходит по ссылке в одну статью, и вопрос «до какого
                    числа это у меня открыто» у него возникает там же, а не на
                    той вкладке, куда он, может быть, и не зайдёт. */}
                <WikiGuestBanner grants={guestGrants} />

                {tabs.length > 1 && (
                    <div className="flex gap-1 overflow-x-auto rounded-2xl bg-slate-100 p-1">
                        {tabs.map(({ key, label, icon: Icon }) => (
                            <button
                                key={key}
                                type="button"
                                onClick={() => setTab(key)}
                                className={`flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-[13px] font-medium transition ${
                                    tab === key
                                        ? 'bg-white text-slate-900 shadow-sm'
                                        : 'text-slate-500 hover:text-slate-700'
                                }`}
                            >
                                <Icon size={14} /> {label}
                            </button>
                        ))}
                    </div>
                )}

                {loading && (
                    <div className={`${iosCard} h-[120px]`}>
                        <div className="sk-shimmer h-full w-full rounded-2xl" />
                    </div>
                )}

                {!loading && error && (
                    <div className={`${iosCard} flex items-start gap-3 p-4`}>
                        <AlertCircle size={18} className="mt-0.5 shrink-0 text-rose-500" />
                        <div className="min-w-0">
                            <div className="text-[14px] font-semibold text-slate-900">Раздел недоступен</div>
                            <div className="mt-0.5 text-[13px] text-slate-500">{error}</div>
                            <button type="button" onClick={loadPing} className={`${iosBtnSecondary} mt-3`}>
                                Попробовать снова
                            </button>
                        </div>
                    </div>
                )}

                {!loading && !error && state && tab === 'overview' && (
                    <>
                        {!state.schema_ready && (
                            <div className={`${iosCard} flex items-start gap-3 p-4`}>
                                <Loader2 size={18} className="mt-0.5 shrink-0 animate-spin text-amber-500" />
                                <div>
                                    <div className="text-[14px] font-semibold text-slate-900">
                                        Раздел разворачивается
                                    </div>
                                    <div className="mt-0.5 text-[13px] text-slate-500">
                                        Таблицы ещё не созданы. Они появятся при ближайшем перезапуске сервера.
                                    </div>
                                </div>
                            </div>
                        )}

                        {state.schema_ready && counters && (
                            <section className="space-y-1.5">
                                <div className={iosGroupLabel}>Содержимое</div>
                                <div className={`${iosCard} grid grid-cols-1 divide-y divide-slate-100 sm:grid-cols-3 sm:divide-x sm:divide-y-0`}>
                                    <StatTile icon={Layers} value={counters.spaces} label="Пространств" />
                                    <StatTile icon={FolderTree} value={counters.sections} label="Разделов" />
                                    <StatTile
                                        icon={FileText}
                                        value={counters.articles_published}
                                        label={`Статей${counters.articles_total !== counters.articles_published
                                            ? ` (всего ${counters.articles_total})` : ''}`}
                                    />
                                </div>
                            </section>
                        )}

                        {state.schema_ready && counters?.articles_total === 0 && (
                            <div className={`${iosCard} flex flex-col items-center justify-center gap-2 px-6 py-14 text-center`}>
                                <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                                    <BookOpen size={22} />
                                </div>
                                <div className="text-[15px] font-semibold text-slate-900">Статей пока нет</div>
                                <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                                    {canManageStructure
                                        ? 'Создайте пространство и раздел на вкладке «Структура» — статьи появятся на следующем этапе.'
                                        : 'Наполнение раздела ещё идёт.'}
                                </p>
                            </div>
                        )}

                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Ваш доступ</div>
                            <div className={`${iosCard} divide-y divide-slate-100`}>
                                <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3.5">
                                    <div className="flex items-center gap-2.5 text-[13.5px] text-slate-600">
                                        <ShieldCheck size={16} className="text-slate-400" /> Режим выдачи
                                    </div>
                                    <IosBadge tone={state.access_mode === 'manual' ? 'amber' : 'slate'}>
                                        {ACCESS_MODE_LABELS[state.access_mode] || state.access_mode}
                                    </IosBadge>
                                </div>

                                <div className="px-4 py-3.5">
                                    <div className="mb-2 flex items-center gap-2.5 text-[13.5px] text-slate-600">
                                        <KeyRound size={16} className="text-slate-400" /> Что вы можете
                                    </div>
                                    <div className="flex flex-wrap gap-1.5">
                                        {granted.length === 0 && (
                                            <span className="text-[13px] text-slate-400">Прав пока нет</span>
                                        )}
                                        {granted.map((key) => (
                                            <IosBadge
                                                key={key}
                                                tone={key === 'can_delete' || key === 'can_manage_access' ? 'amber' : 'blue'}
                                            >
                                                {CAPABILITY_LABELS[key]}
                                            </IosBadge>
                                        ))}
                                    </div>
                                    {state.wiki_roles?.length > 0 && (
                                        <div className="mt-2 text-[12px] text-slate-500">
                                            Роли в вики: {state.wiki_roles.join(', ')}
                                        </div>
                                    )}
                                </div>

                                <div className="px-4 py-3.5">
                                    <div className="mb-2 flex items-center gap-2.5 text-[13.5px] text-slate-600">
                                        <Users size={16} className="text-slate-400" />
                                        Правила применяются к вам как к
                                    </div>
                                    <div className="flex flex-wrap gap-1.5">
                                        {(subjects.otp_role || []).map((role) => (
                                            <IosBadge key={`r-${role}`} tone="slate">роль: {role}</IosBadge>
                                        ))}
                                        {(subjects.department || []).map((id) => (
                                            <IosBadge key={`d-${id}`} tone="slate">отдел #{id}</IosBadge>
                                        ))}
                                        {(subjects.group || []).map((id) => (
                                            <IosBadge key={`g-${id}`} tone="slate">группа #{id}</IosBadge>
                                        ))}
                                        {(subjects.direction || []).map((id) => (
                                            <IosBadge key={`n-${id}`} tone="slate">направление #{id}</IosBadge>
                                        ))}
                                    </div>
                                    <p className="mt-2 text-[12px] leading-relaxed text-slate-500">
                                        Правило, выданное на роль ниже вашей, действует и на вас —
                                        руководитель видит всё, что видит подчинённый.
                                    </p>
                                </div>
                            </div>
                        </section>
                    </>
                )}

                {tab === 'library' && (
                    <WikiLibrary
                        base={base}
                        headers={headers}
                        showToast={showToast}
                        structure={scopedStructure}
                        catalog={catalog}
                        features={features}
                        spaceId={activeSpace?.id || null}
                        /* В гостевом пространстве человек — читатель, какая
                           бы должность у него ни была: правил на запись ему
                           там не выписывали, и «Режим редактора» в шапке
                           витрины обещал бы то, чего сервер не даст. */
                        canCreate={!!capabilities.can_create && !activeSpace?.guest_only}
                        canEdit={canEdit && !activeSpace?.guest_only}
                        createRequest={createRequest}
                        onCreateConsumed={() => setCreateRequest(null)}
                        editTarget={editTarget}
                        onEditTargetConsumed={() => setEditTarget(null)}
                        homeTick={homeTick}
                        onOpenParks={() => setTab('parks')}
                        /* Счётчики на главной — не подписи, а кнопки: число
                           ведёт туда, где лежит то, что оно посчитало. */
                        onOpenCatalog={(bucket) => {
                            setCatalogBucket(bucket);
                            setTab('catalog');
                        }}
                        /* Правка статьи меняет числа каталога: опубликовали
                           черновик — «Черновиков» обязано уменьшиться сразу, а
                           не при следующем заходе в раздел. */
                        reloadCatalog={loadCatalog}
                        initialSlug={initialArticleSlug}
                        onInitialSlugConsumed={onInitialArticleConsumed}
                        searchTarget={searchTarget}
                        onSearchTargetConsumed={() => setSearchTarget(null)}
                        /* Поиск на витрине — тот же поиск, что в шапке, и выход
                           к помощнику у них обязан быть один и тот же. */
                        onAskAssistant={canAskAssistant ? askAssistant : null}
                    />
                )}

                {/* Помощник грузится лениво: у него свой чат-каркас и композер,
                    а открывают вкладку далеко не всегда. Тот же приём, что у
                    редактора статей и классификатора. */}
                {tab === 'assistant' && (
                    <Suspense fallback={(
                        <div className={`${iosCard} flex items-center justify-center gap-2 py-16 text-slate-400`}>
                            <Loader2 size={18} className="animate-spin" />
                            <span className="text-[13px]">Загружаем помощника…</span>
                        </div>
                    )}>
                        <WikiAssistant
                            base={base}
                            headers={headers}
                            showToast={showToast}
                            /* Помощник знает ровно ту вику, что открыта: у того,
                               кому выдано два пространства, ответ иначе собрался
                               бы из обоих вперемешку. */
                            spaceId={activeSpace?.id || null}
                            askRequest={assistantAsk}
                            onAskRequestConsumed={() => setAssistantAsk(null)}
                            onOpenArticle={(slug, highlight) => {
                                setTab('library');
                                setSearchTarget({ slug, highlight });
                            }}
                        />
                    </Suspense>
                )}

                {tab === 'catalog' && (
                    <div className="space-y-3">
                        {/* Оба переключателя в одной строке: половина вкладки
                            слева, корзина справа. Друг под другом два одинаковых
                            ряда пилюль читались как один механизм — разные места
                            в строке и делают их различимыми.
                            Переключатель половин прячем, когда выбирать не из
                            чего: сегмент-контрол с одной кнопкой — это подпись,
                            притворяющаяся управлением. */}
                        {(catalogModes.length > 1 || catalogMode === 'catalog') && (
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                                {catalogModes.length > 1 && (
                                    <ModeSwitch
                                        value={catalogMode}
                                        onChange={setCatalogMode}
                                        allowed={catalogModes}
                                    />
                                )}
                                {catalogMode === 'catalog' && (
                                    <BucketSwitch
                                        value={catalogBucket}
                                        onChange={setCatalogBucket}
                                        totals={catalog?.totals}
                                    />
                                )}
                            </div>
                        )}

                        {/* Половина появляется со сдвигом в ту сторону, откуда
                            пришла: правее в переключателе — значит въезжает
                            справа. Без направления переход читается как
                            перерисовка, а не как переход. Анимируем ТОЛЬКО въезд:
                            выезд старой половины схлопывал бы высоту и дёргал
                            прокрутку — структура много выше каталога.
                            Сторону считаем по порядку половин, а не по имени
                            одной из них: с появлением третьей («Тренажёры»)
                            проверка на 'structure' начала бы врать. */}
                        <motion.div
                            key={catalogMode}
                            initial={reduceMotion
                                ? { opacity: 0 }
                                : { opacity: 0, x: modeSlide }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={reduceMotion
                                ? { duration: 0 }
                                : { duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                        >
                            {catalogMode === 'migration' ? (
                                <WikiMigration
                                    base={base}
                                    headers={headers}
                                    showToast={showToast}
                                    space={activeSpace}
                                    /* Статья открывается там же, где все
                                       статьи, — на главной: второго экрана
                                       статьи в разделе быть не должно. */
                                    onOpenArticle={(slug) => {
                                        setTab('library');
                                        setSearchTarget({ slug });
                                    }}
                                    /* Решение меняет корзину статьи и остаток
                                       очереди — и то и другое живёт в каталоге. */
                                    onReviewed={loadCatalog}
                                />
                            ) : catalogMode === 'trainers' ? (
                                <WikiTrainers
                                    base={base}
                                    headers={headers}
                                    /* Статью со вставленным тренажёром открываем
                                       там же, где все статьи, — на главной. */
                                    onOpenArticle={(slug) => {
                                        setTab('library');
                                        setSearchTarget({ slug });
                                    }}
                                    showToast={showToast}
                                />
                            ) : catalogMode === 'guests' ? (
                                <WikiGuests
                                    base={base}
                                    headers={headers}
                                    space={activeSpace}
                                    showToast={showToast}
                                />
                            ) : catalogMode === 'catalog' ? (
                                <WikiCatalog
                                    base={base}
                                    headers={headers}
                                    showToast={showToast}
                                    catalog={catalog}
                                    space={activeSpace}
                                    loading={catalogLoading}
                                    bucket={catalogBucket}
                                    onBucketChange={setCatalogBucket}
                                    /* Статья открывается на главной — там живут
                                       читалка, редактор и оглавление. Второго
                                       экрана статьи в каталоге быть не должно. */
                                    onOpenArticle={(slug) => {
                                        setTab('library');
                                        setSearchTarget({ slug });
                                    }}
                                    /* Правка — туда же, где редактор: на
                                       витрину. Второго редактора в каталоге
                                       быть не должно ровно по той же причине,
                                       что и второго экрана статьи. */
                                    onEditArticle={(article) => {
                                        setTab('library');
                                        setEditTarget({ slug: article.slug });
                                    }}
                                    /* Смена статуса из списка меняет числа на
                                       переключателе корзин — они живут здесь. */
                                    reloadCatalog={loadCatalog}
                                />
                            ) : (
                                <WikiStructure
                                    base={base}
                                    headers={headers}
                                    showToast={showToast}
                                    structure={scopedStructure}
                                    loading={structureLoading}
                                    canManageAccess={canManageAccess}
                                    canManageStructure={canManageStructure}
                                    /* Правка структуры меняет и каталог: раздел
                                       переименовали или убрали в архив — дерево
                                       рядом обязано это показать сразу, а не при
                                       следующем заходе в раздел. */
                                    reload={() => { loadStructure(); loadPing(); loadCatalog(); }}
                                />
                            )}
                        </motion.div>
                    </div>
                )}

                {/* Справочники парков и офисов принадлежат пространству: адрес
                    и телефон офиса Таксопарков не должны доезжать до Тез. Поэтому
                    spaceId — не уточнение выборки, а сам доступ, и сервер без
                    него отвечает 400 (wiki/routes_structure.request_space). */}
                {tab === 'parks' && (
                    <WikiParks base={base} headers={headers} showToast={showToast}
                               spaceId={activeSpace?.id || null} />
                )}

                {tab === 'offices' && (
                    <WikiOffices base={base} headers={headers} showToast={showToast}
                                 spaceId={activeSpace?.id || null} />
                )}

                {tab === 'analytics' && (
                    <WikiAnalytics
                        base={base}
                        headers={headers}
                        showToast={showToast}
                        /* Пространство сужает отчёт: в отличие от журнала, где
                           запись о чужой вике всё равно нужна, аналитика
                           отвечает на вопрос про КОНКРЕТНУЮ базу знаний. */
                        spaceId={activeSpace?.id || null}
                        onOpenArticle={(slug) => {
                            setTab('library');
                            setSearchTarget({ slug });
                        }}
                    />
                )}

                {tab === 'audit' && (
                    <WikiAudit
                        base={base}
                        headers={headers}
                        showToast={showToast}
                        /* Дерево нужно журналу, чтобы называть пространства и
                           разделы словами: в записи лежат их идентификаторы.
                           Здесь — ПОЛНОЕ, а не суженное: у записи о разделе,
                           уехавшем в архив или в другую ветку, имя берётся
                           отсюда, и без него строка стала бы «раздел №7». */
                        structure={structure}
                        /* Журнал у каждого пространства свой (решение владельца
                           25.08.2026): у «Таксопарков» и «Теза» он был общим, и
                           каждый видел, кто что правил в чужой вике. */
                        spaceId={activeSpace?.id}
                        onOpenArticle={(slug) => {
                            setTab('library');
                            setSearchTarget({ slug });
                        }}
                    />
                )}
            </div>

            {/* Конструктор пространства. Живёт на уровне раздела, а не вкладки:
                его открывают из шапки, и он обязан работать с любой вкладки —
                в том числе с той, которую сам сейчас выключит. */}
            <WikiSpaceModal
                open={!!spaceModal}
                space={spaceModal?.mode === 'edit' ? editableSpace : null}
                base={base}
                headers={headers}
                departments={departments}
                showToast={showToast}
                onClose={() => setSpaceModal(null)}
                onSaved={(id) => {
                    /* Новое пространство сразу становится текущим: его завели,
                       чтобы в нём работать, а не чтобы найти в списке. */
                    if (id) setSpaceId(id);
                    refresh();
                }}
            />
        </div>
    );
}
