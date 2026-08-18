import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, BookOpen, FileText, FolderTree, KeyRound, Layers, MapPin,
    Building2, Loader2, Plus, RefreshCw, ScrollText, ShieldCheck, Sparkles, Users,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, IosBadge,
} from '../ui/ios';
import WikiLibrary from './WikiLibrary';
import WikiParks from './WikiParks';
import WikiOffices from './WikiOffices';
import WikiStructure from './WikiStructure';
import WikiAudit from './WikiAudit';
import WikiSearch from './WikiSearch';
const WikiAssistant = lazy(() => import('./WikiAssistant'));
import { CLASSIFIER_SLUG } from './WikiArticle';
import { getScrollContainer } from './scrollContainer';
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

const CAPABILITY_LABELS = {
    can_read: 'Читать',
    can_create: 'Создавать',
    can_edit: 'Редактировать',
    can_delete: 'Удалять',
    can_publish: 'Публиковать',
    can_approve: 'Согласовывать',
    can_manage_users: 'Управлять людьми',
    can_manage_structure: 'Управлять структурой',
    can_manage_access: 'Управлять доступами',
};

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
    /* Счётчик нажатий «Новая статья»: кнопка живёт в шапке раздела, редактор —
       во вкладке со статьями. Счётчик, а не флаг: после закрытия редактора его
       не нужно гасить, чтобы кнопка сработала во второй раз. */
    const [createTick, setCreateTick] = useState(0);
    // Тем же способом заголовок раздела возвращает витрину на главную.
    const [homeTick, setHomeTick] = useState(0);
    const rootRef = useRef(null);

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

    useEffect(() => { loadPing(); loadStructure(); }, [loadPing, loadStructure]);

    const capabilities = state?.capabilities || {};
    const granted = Object.keys(CAPABILITY_LABELS).filter((key) => capabilities[key]);
    const counters = state?.counters;
    const subjects = state?.subjects || {};
    const canManageStructure = !!(capabilities.can_manage_structure || capabilities.can_manage_access);
    const canManageAccess = !!capabilities.can_manage_access;
    const canEdit = !!(capabilities.can_edit || capabilities.can_publish);

    const tabs = useMemo(() => ([
        { key: 'library', label: 'Статьи', icon: BookOpen, show: true },
        // Показан всем: периметр считает сервер, и гейт по способности здесь
        // был бы вреден — он мигал бы false во время загрузки ping, а эффект
        // ниже выкидывал бы человека из открытого чата.
        { key: 'assistant', label: 'Помощник', icon: Sparkles, show: true },
        { key: 'overview', label: 'Обзор', icon: ShieldCheck, show: true },
        { key: 'parks', label: 'Парки', icon: Building2, show: true },
        { key: 'offices', label: 'Офисы', icon: MapPin, show: true },
        // Отдельной вкладки «Доступы» нет: права выдаются из строки раздела на
        // вкладке «Структура». Раздел там выбран тем, что человек на него нажал,
        // а не селектом из плоского списка, где ветки СЗоВ и ОП одноимённые.
        { key: 'structure', label: 'Структура', icon: Layers, show: canManageStructure },
        { key: 'audit', label: 'Журнал', icon: ScrollText, show: canManageAccess },
    ].filter((t) => t.show)), [canManageStructure, canManageAccess]);

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
        Promise.all([loadPing(), loadStructure()])
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

                    <div className="flex w-full flex-wrap items-center gap-2 sm:ml-auto sm:w-auto">
                        {/* Поиск живёт прямо в шапке: поле растёт при фокусе, выдача
                            выпадает под ним. Так он доступен с любой вкладки раздела. */}
                        <WikiSearch
                            base={base}
                            headers={headers}
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
                        />

                        <button type="button" onClick={refresh} className={iosBtnSecondary}>
                            <RefreshCw size={15} /> Обновить
                        </button>

                        {/* Действие вкладки со статьями — в шапке рядом с «Обновить»,
                            как в макете. На других вкладках его быть не должно:
                            редактор принадлежит витрине статей. */}
                        {tab === 'library' && capabilities.can_create && (
                            <button
                                type="button"
                                onClick={() => setCreateTick((n) => n + 1)}
                                className={iosBtnPrimary}
                            >
                                <Plus size={15} /> Новая статья
                            </button>
                        )}
                    </div>
                </header>


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
                        structure={structure}
                        counters={counters}
                        canCreate={!!capabilities.can_create}
                        canEdit={canEdit}
                        createTick={createTick}
                        homeTick={homeTick}
                        onOpenParks={() => setTab('parks')}
                        initialSlug={initialArticleSlug}
                        onInitialSlugConsumed={onInitialArticleConsumed}
                        searchTarget={searchTarget}
                        onSearchTargetConsumed={() => setSearchTarget(null)}
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
                            onOpenArticle={(slug, highlight) => {
                                setTab('library');
                                setSearchTarget({ slug, highlight });
                            }}
                        />
                    </Suspense>
                )}

                {tab === 'parks' && (
                    <WikiParks base={base} headers={headers} showToast={showToast} />
                )}

                {tab === 'offices' && (
                    <WikiOffices base={base} headers={headers} showToast={showToast} />
                )}

                {tab === 'structure' && (
                    <WikiStructure
                        base={base}
                        headers={headers}
                        showToast={showToast}
                        structure={structure}
                        loading={structureLoading}
                        canManageAccess={canManageAccess}
                        reload={() => { loadStructure(); loadPing(); }}
                    />
                )}

                {tab === 'audit' && (
                    <WikiAudit base={base} headers={headers} showToast={showToast} />
                )}
            </div>
        </div>
    );
}
