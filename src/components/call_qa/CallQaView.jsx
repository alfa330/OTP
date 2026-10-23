import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { motion } from 'framer-motion';
import {
    Sparkles, ListChecks, SlidersHorizontal, Database, ChevronLeft, Gauge,
    Loader2, AlertCircle, RotateCcw, CheckCircle2, MessageSquare, PhoneCall, ScrollText, Ban, Link2,
} from 'lucide-react';
import { APPLE_FONT, iosCard, iosBtnGhost, iosBtnSecondary, IosBadge, IosSegmented } from '../ui/ios';
import { isDepartmentHead, normalizeRole } from '../../utils/roles';
/* Высота открытой карточки — счётом до низа видимой области, а не константой
   `calc(100vh-N)`: арифметика общая с «Обращениями» и «Лидами OLX». */
import { fitHeight, measureShell } from '../crm/layout';
import { canPullCalls, chatSubjectOf, SUBJECT_FAMILY_CALLS, SUBJECT_FAMILY_CHATS } from './subjects';
import CallReviewCard from './CallReviewCard';
import { adjudicationSummary } from './SimilarRules';
import QaDashboard from './QaDashboard';
import EvaluationsList from './EvaluationsList';
import CriteriaClassification from './CriteriaClassification';
import AdjudicationsRag from './AdjudicationsRag';
import ChatQueue from './ChatQueue';
import { isChat } from './QueueList';
import QueueDays from './QueueDays';
import DigestView from './DigestView';
import useDayList from './useDayList';
import useDigest from './useDigest';
import usePeriod from './usePeriod';
import { applyReviewed, dayPageRequest, itemKey, mergeDayPage, NO_DAY } from './queueDayRules';
import QaFilters from './QaFilters';
import FindSubjectModal from './FindSubjectModal';
import AudioPendingCard from './AudioPendingCard';
import ExcludeModal from './ExcludeModal';
import { exclusionAuthor, exclusionLabel } from './exclusionRules';
import { buildEvalLink, copyText, readEvalLink, syncEvalLink } from './evalLink';
import { EMPTY_FILTERS, filtersToParams, filtersKey, hasActiveFilters } from './filters';

/* Контейнер раздела «ИИ-оценка» (App.jsx: view === "ai_qa").
 *
 * Раздел оценивает ТРИ отдела — ОП, СЗоВ и Тез КЦ, — и у каждого своя телефония
 * и свой источник переписки. Выбранный отдел уходит параметром `department` во
 * все запросы; что открыто конкретному человеку, решает бэкенд
 * (/api/ai-qa/departments): супер-админ и глобальный админ видят все три, глава
 * и СВ — свой отдел, наблюдатель «Маркетинга» — разборы ОП. У СВ данные вдобавок
 * режутся до его направлений, а вкладки «Критерии»/«База разборов» скрыты.
 *
 * Все данные — реальные с /api/ai-qa/*. Мок-данных нет; при недоступности
 * бэкенда — состояния загрузки / ошибки / пусто. */

const TABS = [
    { key: 'overview',  label: 'Обзор',          Icon: Gauge },
    // Сводка ИИ по дням (call_qa.digest): что случилось, что повторяется, кому
    // нужна помощь — по направлениям, и чат о том же дне. Рядом с «Обзором»:
    // её читают первой, до очереди.
    { key: 'digest',    label: 'Сводка',         Icon: ScrollText },
    { key: 'queue',     label: 'Очередь ревью',  Icon: ListChecks },
    { key: 'chats',     label: 'Чаты',           Icon: MessageSquare },
    // Два блока оценённого — по субъекту: переписки Верификаторов и звонки
    // остальных направлений ОП. Раньше здесь были «Оценки» вперемешку.
    { key: 'evals',     label: 'Звонки',         Icon: PhoneCall },
    { key: 'criteria',  label: 'Критерии',       Icon: SlidersHorizontal },
    { key: 'rag',       label: 'База разборов',  Icon: Database },
];

/* Где под панелью фильтров есть список, который она сужает. У обзора,
 * классификации критериев и базы разборов своей выборки по сотрудникам нет —
 * панель там была бы кнопкой, которая ни на что не влияет. */
/* Панель фильтров стоит там, где под ней выборка разговоров: очередь, чаты и
   звонки. Модулю маркетинга (ТЗ #317) — ещё «Обзор» (метрики по тому же
   отбору) и «База разборов» (только оси сделки: правило отбирается по сделке
   разговора, из которого его вывели). Кому открыт модуль, говорит сервер. */
const FILTERABLE_TABS = ['queue', 'chats', 'evals'];
const EMPTY_QUEUE = { days: [], total: 0, truncated: false };
const MARKETING_FILTERABLE_TABS = ['overview', 'rag'];
const EXPORT_TABS = ['chats', 'evals'];

/* «Исключить оценку» в строке над карточкой — красным словом, как «Удалить» в
   панели инструментов iOS: заметно, но без заливки (залитая красная кнопка —
   это уже подтверждение в окне). Своя строка классов: у iosBtnGhost серый цвет
   текста перебивает утилиту поверх. */
const EXCLUDE_BTN = 'inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-medium '
    + 'text-rose-600 transition-all hover:bg-rose-50 active:scale-[0.98] focus-visible:outline-none '
    + 'focus-visible:ring-2 focus-visible:ring-rose-500/60 disabled:cursor-not-allowed disabled:opacity-50';

function Segmented({ tabs = TABS, tab, setTab }) {
    const refs = useRef([]);
    const move = (event, index) => {
        let next = null;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next === null) return;
        event.preventDefault();
        setTab(tabs[next].key);
        refs.current[next]?.focus();
    };
    return (
        <div className="flex max-w-full overflow-x-auto rounded-2xl bg-slate-100 p-1" role="tablist" aria-label="Разделы ИИ-оценки">
            {tabs.map((t, index) => {
                const active = tab === t.key;
                return (
                    <button key={t.key} ref={(node) => { refs.current[index] = node; }} type="button" role="tab"
                        id={`qa-tab-${t.key}`} aria-controls={`qa-panel-${t.key}`}
                        aria-selected={active} tabIndex={active ? 0 : -1} onKeyDown={(event) => move(event, index)}
                        onClick={() => setTab(t.key)}
                        className={`relative flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-[13px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                            active ? 'text-slate-900' : 'text-slate-500 hover:text-slate-700'}`}>
                        {active && (
                            <motion.span layoutId="qa-tab" className="absolute inset-0 rounded-xl bg-white shadow-sm"
                                         transition={{ type: 'spring', stiffness: 400, damping: 32 }} />
                        )}
                        <t.Icon size={15} className="relative z-10" />
                        <span className="relative z-10">{t.label}</span>
                    </button>
                );
            })}
        </div>
    );
}

const Spinner = ({ text }) => (
    <div className={`${iosCard} flex flex-col items-center justify-center gap-3 px-6 py-16 text-center`} role="status" aria-live="polite">
        <Loader2 size={26} className="animate-spin text-blue-500" aria-hidden="true" />
        <p className="text-[13px] text-slate-500">{text}</p>
    </div>
);

const ErrorCard = ({ text, onRetry }) => (
    <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-14 text-center`} role="alert">
        <AlertCircle size={26} className="text-rose-500" aria-hidden="true" />
        <p className="text-[13.5px] font-medium text-slate-700">{text}</p>
        {onRetry && <button type="button" onClick={onRetry} className={iosBtnSecondary}>Повторить</button>}
    </div>
);

export default function CallQaView(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast, user } = props;
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    // Правка/удаление разборов — только супер-админ (бэкенд проверяет то же в _ai_qa_admin_guard).
    const canManageRag = normalizeRole(user?.role) === 'super_admin';
    // Переоценить из карточки строку, которая уже есть в журнале, может админ или
    // глава отдела — та же граница, что у переоценки в самом журнале (бэкенд
    // проверяет то же в _ai_qa_can_correct_journal). Здесь флаг нужен только для
    // подсказки и блокировки переключателя до отправки.
    const canCorrectJournal = ['admin', 'super_admin'].includes(normalizeRole(user?.role))
        || isDepartmentHead(user);
    // СВ ОП оценивает только свои направления: конфигурация критериев и база разборов
    // ему не показываются (бэкенд их тоже ограничивает/запрещает).
    const isScopedSupervisor = normalizeRole(user?.role) === 'sv' && !isDepartmentHead(user);
    const visibleTabs = isScopedSupervisor
        ? TABS.filter((t) => t.key !== 'criteria' && t.key !== 'rag')
        : TABS;

    /* Селектор отдела. Раздел оценивает три отдела (ОП, СЗоВ, Тез КЦ), и у
     * каждого свои направления, своя телефония и свой источник переписки.
     * Что открыто именно этому человеку — решает бэкенд (/api/ai-qa/departments):
     * супер-админ и глобальный админ видят все три, глава и СВ — свой отдел.
     * До ответа сервера department = null, и запросы уходят БЕЗ параметра —
     * тогда бэкенд сам подставит единственный доступный отдел. */
    const [departments, setDepartments] = useState(null);   // null = загрузка
    /* Модуль маркетинга (ТЗ #317, раздел 3): маркетинг и глобальные админы.
       Остальным раздел ровно такой, каким был до модуля. */
    const [marketingAccess, setMarketingAccess] = useState(false);
    const [departmentsErr, setDepartmentsErr] = useState(false);
    const [department, setDepartment] = useState(null);
    const departmentRef = useRef(department);
    departmentRef.current = department;
    /* Ссылка на оценку (evalLink.js): цель читается один раз, при входе в раздел, и
       открывается, когда известен отдел. Пока она не открыта, метку в адресе не
       трогаем: первый же кадр без карточки стёр бы то, ради чего человек пришёл.
       adoptFor — разговор, открытый ссылкой: когда придёт его карточка, раздел
       перейдёт на его отдел (у супер-админа по умолчанию выбран другой). */
    const linkTarget = useRef(typeof window === 'undefined' ? null : readEvalLink(window.location.search));
    const linkPending = useRef(Boolean(linkTarget.current));
    const adoptFor = useRef(null);

    const [tab, setTab] = useState('queue');
    const [sectionInteraction, setSectionInteraction] = useState({ editing: false, busy: false });
    const [reviewInteraction, setReviewInteraction] = useState({ dirty: false, busy: false });
    const [queue, setQueue] = useState(null);      // сводка дней { days, total, truncated }; null = загрузка
    const [queueErr, setQueueErr] = useState(false);
    const [queueDayItems, setQueueDayItems] = useState({});   // день → { items, total, end, loading, error }
    const [queueDay, setQueueDay] = useState(null);   // открытый день очереди; null — список дней
    // Проверенные с последней загрузки: из дня пропадают сразу, сводка дня меняется на месте.
    const [queueReviewed, setQueueReviewed] = useState([]);
    const queueGeneration = useRef(0);
    const queueReviewedKeys = useMemo(() => new Set(queueReviewed.map((entry) => entry.key)), [queueReviewed]);
    const queueDays = useMemo(() => applyReviewed(queue?.days || [], queueReviewed), [queue, queueReviewed]);
    const [departmentsReload, setDepartmentsReload] = useState(0);

    /* Отбор живёт в контейнере, а не в каждом списке: панель стоит под
     * вкладками и не должна прыгать по экрану, а переключение вкладки не должно
     * сбрасывать то, что человек уже выставил. Фильтрует СЕРВЕР — клиентская
     * фильтрация разошлась бы со счётчиком «Показано N из M». */
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const filtersActive = hasActiveFilters(filters);
    /* Ключ-строка, а не сам объект: объект пересоздаётся на каждом рендере, и в
     * зависимостях эффекта это бесконечный перезапрос. */
    const filtersSignature = filtersKey(filters);

    /* «Звонки», «Чаты» и «Сводка» — по дням, как очередь. Их состояние здесь, а
     * не во вкладках: пока открыта карточка разговора, вкладка размонтирована, а
     * вернуться человек должен на тот же экран дня (и в ту же переписку с ИИ). */
    const callsList = useDayList({ apiBaseUrl, headers, department, filters,
                                   subject: SUBJECT_FAMILY_CALLS, enabled: tab === 'evals' });
    const chatsList = useDayList({ apiBaseUrl, headers, department, filters,
                                   subject: chatSubjectOf(department), enabled: tab === 'chats' });
    const digest = useDigest({ apiBaseUrl, headers, department, enabled: tab === 'digest', showToast });
    // Экран «Период» вкладки «Сводка» (цифры за несколько дней, сводка за месяц,
    // чат о периоде) — здесь же: из него открывают карточку разговора.
    const period = usePeriod({ apiBaseUrl, headers, department, enabled: tab === 'digest', showToast });

    const [selected, setSelected] = useState(null);
    const [callData, setCallData] = useState(null);
    const [callLoading, setCallLoading] = useState(false);
    const [callErr, setCallErr] = useState(null);
    /* Запись звонка из АТС ещё едет (сервер ответил 202 audio_pending): держим
       этапы на экране и опрашиваем карточку, пока файл не появится. Таймеры в
       ref — их надо гасить при закрытии карточки и смене субъекта. */
    const [callPending, setCallPending] = useState(null);
    const pendingTimer = useRef(null);
    const evaluatingTimer = useRef(null);
    // Диалог точечного подбора «Найти звонок / переписку» — один на обе вкладки,
    // семейство берёт у открытой вкладки.
    const [findOpen, setFindOpen] = useState(false);
    // Окно «Исключить оценку» и возврат исключённой (плашка в карточке).
    const [excludeOpen, setExcludeOpen] = useState(false);
    const [restoreBusy, setRestoreBusy] = useState(false);
    const callRequest = useRef({ id: 0, controller: null });
    const queueRequest = useRef({ id: 0, controller: null });
    const returnFocus = useRef(null);
    // Вкладка, с которой открыли карточку: строка разговора годится в правку
    // сводки очереди, только если она и есть строка очереди (у строк «Звонков» и
    // «Чатов» другие метки — по ним сводка дня очереди посчиталась бы неверно).
    const openedFrom = useRef(null);

    /* Открытая карточка занимает экран до низа: две её колонки (запись с
       транскриптом и оценка) прокручиваются независимо, а страница — нет.
       Высота считается от прокрутчика портала (.main-content) и пересчитывается
       при изменении размеров; на телефоне (уже lg) высоту не навязываем —
       там одна колонка, и страница едет как прежде. */
    const reviewShellRef = useRef(null);
    const [shellHeight, setShellHeight] = useState(null);
    useEffect(() => {
        const node = reviewShellRef.current;
        if (!selected || !node || typeof window === 'undefined') { setShellHeight(null); return undefined; }
        const wide = window.matchMedia('(min-width: 1024px)');
        const recompute = () => {
            if (!wide.matches) { setShellHeight(null); return; }
            setShellHeight(fitHeight(measureShell(node)));
        };
        recompute();
        const scroller = node.closest('.main-content');
        const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(recompute) : null;
        if (observer && scroller) observer.observe(scroller);
        window.addEventListener('resize', recompute);
        wide.addEventListener?.('change', recompute);
        return () => {
            observer?.disconnect();
            window.removeEventListener('resize', recompute);
            wide.removeEventListener?.('change', recompute);
        };
    }, [selected]);

    const changeTab = (nextTab) => {
        if (nextTab === tab) return;
        if (sectionInteraction.busy) {
            showToast?.('Дождитесь завершения сохранения', 'error');
            return;
        }
        if (sectionInteraction.editing &&
            !window.confirm('Перейти в другой раздел? Несохранённые изменения будут потеряны.')) return;
        setSectionInteraction({ editing: false, busy: false });
        setTab(nextTab);
    };

    /* Пока список отделов не пришёл, НИ ОДНОГО запроса раздела не отправляем:
     * без параметра `department` бэкенд отдаёт неограниченному зрителю данные
     * ВСЕХ отделов, а очередь грузится один раз (`queue === null`) и потом сама
     * не перерисовалась бы — на экране осталась бы смесь отделов. Ошибку тоже
     * показываем явно: молчаливый откат к запросам без отдела дал бы ту же смесь. */
    useEffect(() => {
        if (!apiBaseUrl) { setDepartments([]); setDepartmentsErr(true); return; }
        let alive = true;
        setDepartmentsErr(false);
        axios.get(`${apiBaseUrl}/api/ai-qa/departments`, { headers: headers() })
            .then((r) => {
                if (!alive) return;
                const items = r.data?.items || [];
                setDepartments(items);
                setMarketingAccess(!!r.data?.marketing);
                setDepartment(r.data?.current || items[0]?.code || null);
                setDepartmentsErr(!items.length);
            })
            .catch(() => { if (alive) { setDepartments([]); setDepartmentsErr(true); } });
        return () => { alive = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, departmentsReload]);

    const changeDepartment = (nextCode) => {
        if (!nextCode || nextCode === department) return;
        if (sectionInteraction.busy) {
            showToast?.('Дождитесь завершения сохранения', 'error');
            return;
        }
        if (sectionInteraction.editing &&
            !window.confirm('Сменить отдел? Несохранённые изменения будут потеряны.')) return;
        setSectionInteraction({ editing: false, busy: false });
        // Открытая карточка и очередь принадлежат ПРЕЖНЕМУ отделу — закрываем и
        // сбрасываем, иначе после смены отдела на экране осталась бы чужая оценка.
        // Запрос карточки при этом отменяем: его поздний ответ дорисовал бы её
        // обратно уже под новым отделом.
        callRequest.current.controller?.abort();
        callRequest.current = { id: callRequest.current.id + 1, controller: null };
        setSelected(null); setCallData(null); setCallErr(null); setCallLoading(false);
        setReviewInteraction({ dirty: false, busy: false });
        setExcludeOpen(false);
        resetQueue();
        callsList.reset(); chatsList.reset(); digest.reset(); period.reset();
        // Отбор принадлежит ОТДЕЛУ: сотрудники, группы и направления у отделов
        // разные, и сохранённый выбор после переключения ссылался бы на чужого
        // человека — список молча оказался бы пустым при живых оценках.
        setFilters(EMPTY_FILTERS);
        setDepartment(nextCode);
    };

    const departmentOptions = (departments || []).map((item) => ({
        value: item.code, label: item.name,
    }));
    const canSwitchDepartment = departmentOptions.length > 1;
    const departmentName = (departments || []).find((item) => item.code === department)?.name || '';

    /* Очередь — по дням разговора: список дней по месяцам из сводки (/review-queue/days),
     * а по нажатию на строку дня — экран дня со списком его разговоров (/review-queue?day=…).
     * Состояние (открытый день, строки, только что проверенные) живёт здесь, а не в
     * QueueDays: пока открыта карточка разговора, очередь не смонтирована, и человек
     * вернулся бы к списку дней, а не на экран дня, где остановился. */
    /* Сброс очереди: запрос сводки в полёте отменяется, а поздние ответы по строкам
     * дней отсекает новое поколение. Нужен и там, где очередь не на экране (смена
     * отдела или отбора на другой вкладке): иначе запоздавший ответ прежнего отдела
     * записал бы свои дни, и к ним подгрузились бы строки уже нового отдела. */
    const resetQueue = () => {
        queueRequest.current.controller?.abort();
        queueRequest.current = { id: queueRequest.current.id + 1, controller: null };
        queueGeneration.current += 1;
        setQueue(null); setQueueErr(false);
        setQueueDayItems({}); setQueueDay(null); setQueueReviewed([]);
    };
    const loadQueue = () => {
        resetQueue();
        if (!apiBaseUrl) { setQueueErr(true); setQueue(EMPTY_QUEUE); return; }
        // Отмена + сверка номера запроса: при смене отдела в полёте остаётся
        // запрос прежнего, и его поздний ответ дорисовал бы чужие дни в уже
        // переключённую очередь. Тот же приём, что у запроса карточки.
        const controller = new AbortController();
        const requestId = queueRequest.current.id + 1;
        queueRequest.current = { id: requestId, controller };
        axios.get(`${apiBaseUrl}/api/ai-qa/review-queue/days`,
            { params: { ...(department ? { department } : {}), ...filtersToParams(filters) },
              headers: headers(), signal: controller.signal })
            .then((r) => {
                if (requestId !== queueRequest.current.id) return;
                setQueue({ days: r.data.days || [], total: Number(r.data.total) || 0,
                           truncated: Boolean(r.data.truncated) });
            })
            .catch((error) => {
                if (axios.isCancel(error) || requestId !== queueRequest.current.id) return;
                setQueue(EMPTY_QUEUE); setQueueErr(true);
            });
    };

    /* Строки дня. `fresh` — день открыли (с плиток или стрелкой): список читается
     * заново — пока человек смотрел список дней, его могли проверить другие. Дальше
     * «Показать ещё» и повтор после сбоя — с перекрытием и отсевом повторов
     * (dayPageRequest / mergeDayPage): список дня мог сдвинуться. Сколько ещё не
     * показано, считает ответ самого дня (`total` и докуда дочитали — `end`), а не
     * сводка: она могла устареть. Поздний ответ прежнего открытия того же дня
     * отсекает номер запроса, ответ прежнего отдела или отбора — поколение. */
    const queueDayRequest = useRef({});
    const loadQueueDay = (day, { fresh = false } = {}) => {
        if (!apiBaseUrl || !day) return;
        const generation = queueGeneration.current;
        const requestId = (queueDayRequest.current[day] || 0) + 1;
        queueDayRequest.current[day] = requestId;
        const current = (map) => (fresh ? [] : map[day]?.items || []);
        const loaded = current(queueDayItems).filter((item) => !queueReviewedKeys.has(itemKey(item))).length;
        const { offset, limit } = dayPageRequest(loaded);
        const outdated = () => generation !== queueGeneration.current || requestId !== queueDayRequest.current[day];
        setQueueDayItems((map) => ({ ...map, [day]: { ...(fresh ? {} : map[day] || {}), items: current(map),
                                                     loading: true, error: false } }));
        axios.get(`${apiBaseUrl}/api/ai-qa/review-queue`,
            { params: { day, limit, offset, ...(department ? { department } : {}),
                        ...filtersToParams(filters) },
              headers: headers() })
            .then((r) => {
                if (outdated()) return;
                setQueueDayItems((map) => ({ ...map, [day]: {
                    ...mergeDayPage(map[day]?.items, r.data.items, offset, r.data.total),
                    loading: false, error: false } }));
            })
            .catch(() => {
                if (outdated()) return;
                setQueueDayItems((map) => ({ ...map, [day]: { ...(map[day] || {}), items: map[day]?.items || [],
                                                             loading: false, error: true } }));
            });
    };
    // Из списка дней — на экран дня; стрелками — к соседнему дню, не возвращаясь к списку.
    const openQueueDay = (day, { fromTile = true } = {}) => {
        if (!day) return;
        queueReturn.current = { dayOpened: true, focusTitle: fromTile };
        setQueueDay(day);
        loadQueueDay(day, { fresh: true });
    };
    const closeQueueDay = () => {
        queueReturn.current = { tile: queueDay };
        setQueueDay(null);
    };

    useEffect(() => {
        // department === null означает «отдел ещё не известен»: запрос без него
        // вернул бы смесь отделов, и второй раз очередь не грузится.
        if (department === null) return;
        if (tab === 'queue' && queue === null) loadQueue();
        // eslint-disable-next-line
    }, [tab, apiBaseUrl, department]);

    /* Смена отбора — это НОВАЯ очередь, а не догрузка прежней. Очередь грузится
     * один раз по условию `queue === null`, поэтому без явного сброса панель
     * фильтров молча ничего бы не делала: запрос ушёл бы, а на экране остались
     * бы старые строки. Первый прогон пропускаем — очередь уже грузит эффект
     * выше, и второй запрос был бы гонкой двух ответов. */
    const queueFiltersRef = useRef(filtersSignature);
    useEffect(() => {
        if (queueFiltersRef.current === filtersSignature) return;
        queueFiltersRef.current = filtersSignature;
        if (department === null || tab !== 'queue') { resetQueue(); return; }
        loadQueue();
        // eslint-disable-next-line
    }, [filtersSignature, tab, department]);

    const clearPendingTimers = () => {
        if (pendingTimer.current) { window.clearTimeout(pendingTimer.current); pendingTimer.current = null; }
        if (evaluatingTimer.current) { window.clearTimeout(evaluatingTimer.current); evaluatingTimer.current = null; }
    };

    /* `poll` — повторный запрос той же карточки, пока сервер отвечает 202
       «запись скачивается»: этапы на экране не сбрасываем. Если такой запрос
       не вернулся за несколько секунд, значит запись уже нашлась и сервер
       распознаёт и оценивает разговор (это и есть долгая часть) — переводим
       этапы на «оценка идёт», не дожидаясь ответа. */
    const openCall = (c, refresh = false, { poll = false } = {}) => {
        if (!selected && typeof document !== 'undefined') returnFocus.current = document.activeElement;
        if (!selected) openedFrom.current = tab;
        callRequest.current.controller?.abort();
        clearPendingTimers();
        const controller = new AbortController();
        const requestId = callRequest.current.id + 1;
        callRequest.current = { id: requestId, controller };
        setSelected(c);
        if (!refresh) {
            setCallData(null);
            setReviewInteraction({ dirty: false, busy: false });
        }
        if (!poll) setCallPending(null);
        setCallErr(null);
        if (!apiBaseUrl) { setCallErr('Бэкенд недоступен'); setCallLoading(false); return; }
        setCallLoading(true);
        if (poll) {
            evaluatingTimer.current = window.setTimeout(() => {
                if (requestId !== callRequest.current.id) return;
                setCallPending((current) => (current ? { ...current, stage: 'evaluating' } : current));
            }, 3000);
        }
        const subject = c.subject || 'call';
        axios.get(`${apiBaseUrl}/api/ai-qa/call/${c.id}`, {
            params: { ...(refresh ? { refresh: 1 } : {}), subject },
            headers: headers(), signal: controller.signal,
        })
            .then((r) => {
                if (requestId !== callRequest.current.id) return;
                if (r.status === 202 || r.data?.status === 'audio_pending') {
                    const info = r.data || {};
                    setCallPending((current) => ({ ...info, polls: (current?.polls || 0) + 1 }));
                    // «Файла нет» — финал: опрашивать дальше бессмысленно.
                    if (info.stage !== 'missing') {
                        const delay = Math.max(5, Number(info.retry_after) || 10) * 1000;
                        pendingTimer.current = window.setTimeout(() => {
                            if (requestId === callRequest.current.id) openCall(c, false, { poll: true });
                        }, delay);
                    }
                    return;
                }
                setCallPending(null);
                setCallData(r.data.call);
                if (adoptFor.current === itemKey(c)) {
                    adoptFor.current = null;
                    adoptCardDepartment(r.data.call?.department);
                }
            })
            .catch((e) => {
                if (!axios.isCancel(e) && requestId === callRequest.current.id) {
                    setCallPending(null);
                    // 409 — оценить нельзя по существу (в эпизоде отвечали несколько
                    // операторов и т.п.): показываем причину, а не «ошибку загрузки».
                    setCallErr(e?.response?.data?.error
                        || `Не удалось загрузить оценку ${isChat(subject) ? 'чата' : 'звонка'}`);
                }
            })
            .finally(() => {
                if (requestId === callRequest.current.id) {
                    if (evaluatingTimer.current) { window.clearTimeout(evaluatingTimer.current); evaluatingTimer.current = null; }
                    setCallLoading(false);
                }
            });
    };

    useEffect(() => () => clearPendingTimers(), []);

    /* Карточка, открытая ссылкой, бывает из другого отдела, чем выбран по умолчанию:
       раздел переходит на отдел разговора, чтобы «Назад» вёл в его списки. Карточку
       при этом не закрываем — в отличие от смены отдела селектором; списки прежнего
       отдела сбрасываются по тем же причинам, что и там. */
    const adoptCardDepartment = (code) => {
        if (!code || code === departmentRef.current) return;
        if (!(departments || []).some((item) => item.code === code)) return;
        resetQueue();
        callsList.reset(); chatsList.reset(); digest.reset(); period.reset();
        setFilters(EMPTY_FILTERS);
        setDepartment(code);
    };

    // Оценка из ссылки — как только известен отдел (без него запросы раздела не уходят).
    useEffect(() => {
        const target = linkTarget.current;
        if (!linkPending.current || !target || departments === null || departmentsErr || department === null) return;
        linkPending.current = false;
        adoptFor.current = `${target.subject}-${target.id}`;
        openCall({ id: target.id, subject: target.subject });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [departments, departmentsErr, department]);

    /* Открытая карточка — в адресе: перезагрузка вернёт её же, а адрес из строки —
       готовая ссылка. «Назад» метку убирает. */
    useEffect(() => {
        if (linkPending.current) return;
        syncEvalLink(selected ? { subject: selected.subject || 'call', id: selected.id } : null);
    }, [selected]);

    /* «Ссылка» в строке над карточкой: в буфер — ссылку только на эту оценку, без
       меток входа и других разделов. Отправляют её в переписке, адресную строку
       при этом никто не открывает. */
    const copyEvalLink = async () => {
        const link = buildEvalLink({ subject: callData?.subject_kind || selected?.subject || 'call',
                                     id: callData?.id ?? selected?.id });
        if (!link) { showToast?.('Не удалось собрать ссылку на оценку', 'error'); return; }
        const copied = await copyText(link);
        showToast?.(copied ? 'Ссылка на оценку скопирована' : 'Не удалось скопировать — ссылка есть в адресной строке',
                    copied ? 'success' : 'error');
    };

    /* Куда поставить фокус и прокрутку после смены экрана очереди — эффектом, после
       отрисовки нового экрана, а не таймером:
       — из карточки разговора — на экран того же дня: к той же строке, если разговор
         закрыли без проверки, иначе к соседней (следующей, у последней — предыдущей),
         а если в дне никого не осталось — к заголовку дня. Прежний фокус (кнопка
         строки) к этому времени отцеплен от документа: без этого он уходил на вкладку,
         и страница прыгала в начало;
       — открыли день — начало экрана дня в поле зрения (строка могла быть внизу
         длинной ленты), фокус на заголовок дня, если открыли из списка;
       — «Все дни» — обратно к строке этого дня. */
    const queueReturn = useRef(null);
    // Куда вернуться в сводке после карточки, открытой по ссылке из неё; лента
    // чата восстанавливает свою прокрутку сама, при монтировании (chatRestore).
    const digestReturn = useRef(null);
    const [digestChatRestore, setDigestChatRestore] = useState(null);
    useEffect(() => {
        const back = queueReturn.current;
        if (selected || !back) return;
        queueReturn.current = null;
        const esc = (value) => (window.CSS?.escape ? window.CSS.escape(value) : String(value));
        const title = document.getElementById(back.titleId || 'qa-queue-day-title');
        if (back.digest) {
            // Из карточки — обратно в сводку: туда же по прокрутке (прокручивается
            // .main-content, а в оболочке телефона — сам документ) и на ТУ ЖЕ
            // кнопку разговора: в той же колонке (сводка или чат), в том же ответе
            // ИИ и той же по счёту — одинаковых ссылок в тексте бывает несколько.
            setDigestChatRestore(null);
            const scroller = document.querySelector('.main-content');
            if (scroller && Number.isFinite(back.scrollTop)) scroller.scrollTop = back.scrollTop;
            if (Number.isFinite(back.windowY)) window.scrollTo(0, back.windowY);
            const pane = back.pane && document.querySelector(`[data-qa-pane="${esc(back.pane)}"]`);
            const holder = (back.message && pane?.querySelector(`[data-qa-message="${esc(back.message)}"]`))
                || pane || document;
            const refs = back.ref ? [...holder.querySelectorAll(`[data-qa-ref="${esc(back.ref)}"]`)] : [];
            const ref = refs[back.index] || refs[0];
            if (ref && ref.offsetParent !== null) {
                // Ленту чата прокрутку восстанавливает сама (chatRestore) — когда у
                // неё появится высота; двигать её отсюда значило бы сбить это место.
                const box = ref.getBoundingClientRect();
                if (back.pane !== 'chat' && (box.top < 0 || box.bottom > window.innerHeight)) {
                    ref.scrollIntoView({ block: 'center' });
                }
                ref.focus({ preventScroll: true });
            } else {
                title?.focus({ preventScroll: true });
            }
            return;
        }
        if (back.tile) {
            const tile = document.querySelector(`[data-qa-day-tile="${esc(back.tile)}"]`);
            tile?.scrollIntoView({ block: 'center' });
            tile?.focus({ preventScroll: true });
            return;
        }
        if (back.dayOpened) {
            const panel = document.getElementById(`qa-panel-${tab}`);
            if (panel && panel.getBoundingClientRect().top < 0) panel.scrollIntoView({ block: 'start' });
            if (back.focusTitle) title?.focus({ preventScroll: true });
            return;
        }
        const row = document.querySelector(`[data-qa-row="${esc(back.key)}"]`)
            || (back.next && document.querySelector(`[data-qa-row="${esc(back.next)}"]`));
        if (row) {
            row.scrollIntoView({ block: 'center' });
            row.focus({ preventScroll: true });
        } else if (title) {
            title.scrollIntoView({ block: 'center' });
            title.focus({ preventScroll: true });
        } else {
            document.getElementById(`qa-tab-${tab}`)?.focus();
        }
    });

    const resetCall = () => {
        const focusTarget = returnFocus.current;
        callRequest.current.controller?.abort();
        clearPendingTimers();
        callRequest.current = { id: callRequest.current.id + 1, controller: null };
        if (tab === 'queue' && selected) {
            const key = itemKey(selected);
            const day = selected.day || NO_DAY;
            const rows = (queueDayItems[day]?.items || []).filter((item) => !queueReviewedKeys.has(itemKey(item)));
            const at = rows.findIndex((item) => itemKey(item) === key);
            const next = at >= 0 ? rows[at + 1] || rows[at - 1] : null;
            queueReturn.current = { key, day, next: next ? itemKey(next) : null };
        }
        if ((tab === 'evals' || tab === 'chats') && selected) {
            const list = tab === 'evals' ? callsList : chatsList;
            // Оценку могли поправить, а подбор — добавить новую: день и его строки
            // перечитываются, не убирая строки с экрана до ответа.
            list.refresh();
            if (list.openDay) {
                queueReturn.current = { key: itemKey(selected),
                                        titleId: tab === 'evals' ? 'qa-evals-day-title' : 'qa-chats-day-title' };
            } else if (list.excludedOpen) {
                // Из карточки исключённой — обратно в «Исключённые», к той же строке.
                queueReturn.current = { key: itemKey(selected), titleId: 'qa-excluded-title' };
            }
        }
        if (tab === 'digest' && selected && digestReturn.current) {
            queueReturn.current = { digest: true, titleId: 'qa-digest-day-title', ...digestReturn.current };
            digestReturn.current = null;
        }
        setSelected(null); setCallData(null); setCallErr(null); setCallLoading(false);
        setCallPending(null);
        setReviewInteraction({ dirty: false, busy: false });
        setExcludeOpen(false);
        adoptFor.current = null;
        returnFocus.current = null;
        if (queueReturn.current) return;
        window.setTimeout(() => {
            const target = focusTarget?.isConnected ? focusTarget : document.getElementById(`qa-tab-${tab}`);
            target?.focus?.();
        }, 0);
    };

    const requestCloseCall = () => {
        if (reviewInteraction.busy) {
            showToast?.('Дождитесь завершения сохранения', 'error');
            return;
        }
        if (reviewInteraction.dirty &&
            !window.confirm('Закрыть карточку? Несохранённые исправления будут потеряны.')) return;
        resetCall();
    };

    const requestReevaluation = () => {
        if (!selected || callLoading || reviewInteraction.busy) return;
        if (reviewInteraction.dirty &&
            !window.confirm(`Переоценить ${isChat(selected.subject) ? 'чат' : 'звонок'}? `
                + 'Несохранённые исправления будут потеряны.')) return;
        setReviewInteraction({ dirty: false, busy: false });
        openCall(selected, true);
    };

    useEffect(() => () => callRequest.current.controller?.abort(), []);

    /* Переходы между экранами «по дням» с фокусом — тем же эффектом queueReturn,
       что у очереди: открыли день из списка — фокус на его заголовок, вернулись к
       списку — на строку этого дня. Стрелки соседнего дня фокус не уводят: он
       остаётся на стрелке, и листать дальше можно тем же Enter (как в очереди). */
    const withDayFocus = (list, titleId) => ({
        ...list,
        open: (day, { fromList = false } = {}) => {
            queueReturn.current = { dayOpened: true, focusTitle: fromList, titleId };
            list.open(day);
        },
        close: () => { queueReturn.current = { tile: list.openDay }; list.close(); },
    });
    const callsDays = withDayFocus(callsList, 'qa-evals-day-title');
    const chatsDays = withDayFocus(chatsList, 'qa-chats-day-title');
    const digestDays = withDayFocus(digest, 'qa-digest-day-title');

    /* «Сводка дня» с экрана дня очереди, «Звонков» или «Чатов». */
    const openDigestDay = (day) => {
        if (!day || day === NO_DAY) return;
        if (sectionInteraction.busy) {
            showToast?.('Дождитесь завершения сохранения', 'error');
            return;
        }
        setSectionInteraction({ editing: false, busy: false });
        setTab('digest');
        queueReturn.current = { dayOpened: true, focusTitle: true, titleId: 'qa-digest-day-title' };
        digest.open(day);
    };

    /* Ссылка на разговор в сводке или в ответе ИИ → карточка; «Назад» вернёт
       в сводку на то же место (digestReturn — выше, рядом с queueReturn): та же
       прокрутка страницы и ленты чата, та же кнопка. */
    const openFromDigest = (ref, button = null) => {
        if (!ref?.id || !ref?.subject) return;
        const key = `${ref.subject}:${ref.id}`;
        const scroller = typeof document !== 'undefined' ? document.querySelector('.main-content') : null;
        const pane = button?.closest?.('[data-qa-pane]');
        const message = button?.closest?.('[data-qa-message]');
        const holder = message || pane;
        const log = typeof document !== 'undefined'
            ? document.querySelector('[data-qa-pane="chat"] [role="log"]') : null;
        digestReturn.current = {
            ref: key,
            scrollTop: scroller ? scroller.scrollTop : null,
            windowY: typeof window !== 'undefined' ? window.scrollY : null,
            pane: pane?.getAttribute('data-qa-pane') || null,
            message: message?.getAttribute('data-qa-message') || null,
            index: holder && button ? [...holder.querySelectorAll(`[data-qa-ref="${key}"]`)].indexOf(button) : 0,
        };
        // Ключ ленты: день — у чата дня, границы периода — у чата периода.
        setDigestChatRestore(log ? { key: period.range ? period.key : digest.openDay, scrollTop: log.scrollTop } : null);
        openCall({ id: ref.id, subject: ref.subject });
    };

    // ИИ-подсказка формулировки разбора (правило + границы) — человек редактирует и сохраняет сам.
    const refineAdjud = async (c, d) => {
        if (!apiBaseUrl || !callData) return null;
        try {
            const r = await axios.post(`${apiBaseUrl}/api/ai-qa/adjudicate/refine`, {
                // Разговор и прогон: сервер берёт из прогона сам текст разговора,
                // и правило описывает то, что в нём было.
                call_id: callData.id, subject_kind: callData.subject_kind || 'call',
                evaluation_run_id: callData._evaluation_run_id || null,
                direction_id: callData.direction_id, criterion_idx: c.idx, criterion_name: c.name,
                ai_verdict: c.ai, ai_comment: c.comment || '', correct_verdict: d.verdict,
                reason: d.reason || '',
                excerpt: d.excerpt || '',
                excerpt_verified: d.excerpt_verified === true,
                evidence_status: d.evidence_status || null,
            }, { headers: headers() });
            return r.data?.proposal || null;
        } catch {
            showToast?.('Не удалось получить подсказку ИИ', 'error');
            return null;
        }
    };

    /* «Такой разбор уже был?» и «что ИИ получил по критерию в этой оценке» —
       под исправленным критерием (SimilarRules). Сбой молчит: подсказка не
       должна мешать сохранить разбор. */
    const similarAdjud = async (c, { text, verdict }) => {
        const call = callData;
        if (!apiBaseUrl || !call?._evaluation_run_id || !c?.criterion_id) return null;
        try {
            const r = await axios.post(`${apiBaseUrl}/api/ai-qa/adjudicate/similar`, {
                call_id: call.id, subject_kind: call.subject_kind || 'call',
                evaluation_run_id: call._evaluation_run_id,
                criterion_id: c.criterion_id, text: text || '', correct_verdict: verdict,
            }, { headers: headers() });
            return r.data || null;
        } catch {
            return null;
        }
    };

    /* Сохранение карточки — одно на оценку человека и ревью ИИ.
     *
     * `human` — оценка проверяющего по шкале сотрудника (ai_human_reviews, а с
     * «Учитывать в качестве» — ещё и строка журнала). Ответ сервера — то же
     * состояние, что карточка получает при открытии (балл человека, кто оценил,
     * моя оценка, вердикты человека по критериям), и оно вливается в открытую
     * карточку без повторного прогона. Имена критериев уходят вместе с оценкой:
     * сервер сверяет их со шкалой, чтобы устаревшая карточка не положила баллы в
     * журнал со смещением.
     *
     * `ai` — итог ревью прогона, пока он не разобран: без исправлений это
     * подтверждение (тоже результат ревью: звонок уходит из очереди и остаётся
     * сигналом качества модели), с исправлениями — правила базы знаний, которые
     * ИИ применит уже в следующих оценках (дубль действующего — к нему).
     *
     * Порядок — сначала своя оценка: она важнее и её не отвергают из-за того, что
     * прогон успели переоценить. Карточка закрывается только после ответа на ревью
     * ИИ — при сбое введённое не теряется, а уже сохранённая оценка повторно не
     * отправляется (панель сверяет её с сохранённой). */
    const saveReview = async ({ human, ai }) => {
        const call = callData;
        if (!call) return { ok: false };
        if (!apiBaseUrl) {
            showToast?.('Бэкенд недоступен — оценка не сохранена', 'error');
            return { ok: false };
        }
        const kind = call.subject_kind || 'call';
        let state = null;
        if (human) {
            try {
                const r = await axios.post(`${apiBaseUrl}/api/ai-qa/human-review`, {
                    call_id: call.id, subject_kind: kind,
                    direction_id: call.direction_id, evaluation_run_id: call._evaluation_run_id,
                    criteria_names: (call.criteria || []).map((c) => c.name),
                    ...human,
                }, { headers: headers() });
                state = r.data || {};
            } catch (error) {
                showToast?.(error?.response?.data?.error || 'Не удалось сохранить оценку', 'error');
                return { ok: false };
            }
            setCallData((current) => {
                if (!current || current.id !== call.id || (current.subject_kind || 'call') !== kind) return current;
                const humanByIdx = new Map((state.criteria || []).map((h) => [h.idx, h]));
                return {
                    ...current,
                    human_score: state.human_score ?? null,
                    has_human_review: Boolean(state.has_human_review),
                    human_review: state.human_review || null,
                    my_review: state.my_review || null,
                    criteria: (current.criteria || []).map((c) => {
                        const h = humanByIdx.get(c.idx);
                        return h ? { ...c, human: h.human ?? null, human_comment: h.human_comment ?? null } : c;
                    }),
                };
            });
        }
        const inJournal = human?.count_in_quality
            ? ` · в журнале${state?.journal_call_id ? ` №${state.journal_call_id}` : ''}` : '';
        if (!ai) {
            showToast?.(human?.count_in_quality
                ? `Оценка учтена в качестве${state?.journal_call_id ? ` (№${state.journal_call_id})` : ''}`
                : 'Оценка сохранена', 'success');
            return { ok: true, state };
        }
        const items = ai.items || [];
        let adjudicated = null;
        try {
            const r = await axios.post(`${apiBaseUrl}/api/ai-qa/adjudicate`,
                { call_id: call.id, direction_id: call.direction_id, subject_kind: kind,
                  evaluation_run_id: call._evaluation_run_id,
                  scale_revision_id: call._scale_revision_id,
                  evaluation_fingerprint: call._evaluation_fingerprint,
                  items }, { headers: headers() });
            adjudicated = r.data || null;
        } catch (error) {
            const message = error?.response?.data?.error || 'не удалось сохранить разбор';
            // Прогон успел разобрать кто-то другой: дальше карточка сохраняет только
            // свою оценку, а не упирается в тот же отказ на каждом нажатии.
            if (/уже проверена/.test(message)) {
                setCallData((current) => (current && current.id === call.id && (current.subject_kind || 'call') === kind
                    ? { ...current, ai_review: { outcome: 'reviewed', reviewer: null, reviewed_at: null } } : current));
            }
            showToast?.(human ? `Оценка сохранена, а разбор ИИ — нет: ${message}` : message, 'error');
            return { ok: false, state };
        }
        showToast?.(items.length
            ? `${adjudicationSummary(items.length, adjudicated)}${inJournal}`
            : `Оценка ИИ подтверждена${inJournal}`, 'success');
        // Разговор уходит из своего дня очереди сразу, а сводка дня меняется на месте.
        // День и причины берём у строки очереди (открыт из неё) или у загруженного дня.
        // Открыт мимо очереди («Звонки», «Найти») и день не загружен — сводку не
        // угадываем: очередь перечитается целиком, когда её снова покажут.
        const key = `${kind}-${call.id}`;
        const row = Object.values(queueDayItems).flatMap((entry) => entry.items || [])
            .find((item) => itemKey(item) === key)
            || (openedFrom.current === 'queue' && selected && itemKey(selected) === key && selected.day
                ? selected : null);
        if (row) {
            setQueueReviewed((list) => (list.some((entry) => entry.key === key) ? list
                : [...list, { key, day: row.day || NO_DAY, reasons: row.reasons || [],
                              corrected: items.length > 0,
                              // Балл человека дня прибавляется, только если у разговора его не было.
                              human: row.human_score == null ? state?.my_review?.score ?? null : null }]));
        } else if (queue !== null) {
            // Очередь сейчас на экране — перечитываем сразу: эффект загрузки сам не
            // перезапустится, и сброс оставил бы вечный «Загружаю очередь…».
            if (tab === 'queue') loadQueue(); else resetQueue();
        }
        resetCall();
        return { ok: true, closed: true, state };
    };

    /* «Исключить оценку» — разговор оценивать незачем (нецелевой, ошибся номером,
       тест). Несохранённые исправления пропадут вместе с карточкой — спрашиваем,
       как при «Назад». */
    const requestExclude = () => {
        if (!callData) return;
        if (reviewInteraction.busy) {
            showToast?.('Дождитесь завершения сохранения', 'error');
            return;
        }
        if (reviewInteraction.dirty &&
            !window.confirm('Исключить оценку? Несохранённые исправления будут потеряны.')) return;
        setExcludeOpen(true);
    };

    /* Исключили — разговор уходит из раздела. Из своего дня очереди — сразу, а
       сводка дня меняется на месте, как после проверки (ждут и оценено на один
       меньше, его баллы выходят из средних); «Звонки» и «Чаты» перечитают день,
       когда карточка закроется. Карточка закрывается, как после «Подтвердить»: в
       очереди следующим встаёт соседний разговор. Кто-то успел исключить раньше —
       карточка остаётся и показывает ЕГО причину. */
    const handleExcluded = (exclusion, { already = false, category = null } = {}) => {
        const call = callData;
        setExcludeOpen(false);
        if (!call) return;
        const kind = call.subject_kind || 'call';
        if (already) {
            setCallData((current) => (current && current.id === call.id && (current.subject_kind || 'call') === kind
                ? { ...current, exclusion } : current));
            const author = exclusionAuthor(exclusion);
            showToast?.(`Эту оценку уже исключили${author ? ` — ${author}` : ''}`, 'error');
            return;
        }
        showToast?.(`Оценка исключена${category ? ` · ${category}` : ''}`, 'success');
        const key = `${kind}-${call.id}`;
        const row = Object.values(queueDayItems).flatMap((entry) => entry.items || [])
            .find((item) => itemKey(item) === key)
            || (openedFrom.current === 'queue' && selected && itemKey(selected) === key && selected.day
                ? selected : null);
        if (row) {
            setQueueReviewed((list) => (list.some((entry) => entry.key === key) ? list
                : [...list, { key, day: row.day || NO_DAY, reasons: row.reasons || [], excluded: true,
                              ai: row.ai_score ?? null, humanRemoved: row.human_score ?? null }]));
        } else if (queue !== null) {
            if (tab === 'queue') loadQueue(); else resetQueue();
        }
        resetCall();
    };

    /* «Вернуть» на плашке исключённой оценки: карточка остаётся открытой и снова
       обычная. Непроверенная возвращается и в очередь — та перечитается. */
    const restoreExclusion = async () => {
        const call = callData;
        if (!call || !apiBaseUrl || restoreBusy) return;
        const kind = call.subject_kind || 'call';
        setRestoreBusy(true);
        try {
            await axios.delete(`${apiBaseUrl}/api/ai-qa/exclusion`,
                { params: { subject_kind: kind, call_id: call.id }, headers: headers() });
            setCallData((current) => (current && current.id === call.id && (current.subject_kind || 'call') === kind
                ? { ...current, exclusion: null } : current));
            showToast?.('Оценка возвращена в раздел', 'success');
            if (queue !== null) {
                if (tab === 'queue') loadQueue(); else resetQueue();
            }
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось вернуть оценку', 'error');
        } finally {
            setRestoreBusy(false);
        }
    };

    return (
        /* relative — опора для всего absolute внутри раздела. Без неё подписи для
           экранного диктора (`sr-only` — это position:absolute) в строках длинного
           списка привязывались к <body>, вылезали из прокрутки .main-content и
           растягивали ДОКУМЕНТ: на экране дня появлялся второй, внешний скролл на
           тысячи пикселей вниз (01.10.2026: 3968 px при окне 900). */
        /* Фон корня — для телефона: оболочка делает .main-content прозрачным, и
           сквозь зазоры между карточками просвечивал фоновый логотип портала
           (тот же приём, что у «Термокоробов»). На компьютере цвет совпадает с
           .main-content, ничего не меняется. */
        <div style={{ fontFamily: APPLE_FONT }} className="relative min-h-screen space-y-4 bg-gray-50 md:min-h-0">
            {/* Шапка раздела — только над списками. В открытой карточке разговора
                она отнимала высоту у транскрипта и оценки, а вкладок и выбора отдела
                там всё равно нет; заголовок для экранного диктора — в строке «Назад». */}
            {!selected && (
            <div className="flex flex-wrap items-center gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-500 text-white shadow-sm">
                    <Sparkles size={20} />
                </div>
                <div className="min-w-0">
                    <h1 className="text-[19px] font-semibold text-slate-900">ИИ-оценка</h1>
                    <p className="text-[12.5px] text-slate-400">
                        {isScopedSupervisor
                            ? `Звонки и чаты · ваши направления${departmentName ? ` · ${departmentName}` : ''}`
                            : `Звонки и чаты${departmentName ? ` · ${departmentName}` : ''}`}
                    </p>
                </div>
                {/* Селектор показываем только тем, кому открыт больше одного отдела:
                    у главы и СВ он был бы одной неактивной кнопкой. */}
                {canSwitchDepartment && (
                    <div className="ml-auto">
                        <IosSegmented value={department} options={departmentOptions}
                                      onChange={changeDepartment} ariaLabel="Отдел" />
                    </div>
                )}
            </div>
            )}

            {departments === null ? (
                <Spinner text="Загружаю отделы…" />
            ) : departmentsErr ? (
                <ErrorCard text="Не удалось получить список отделов — раздел не может определить, чьи данные показывать"
                           onRetry={() => { setDepartments(null); setDepartmentsReload((n) => n + 1); }} />
            ) : (
            <>
            {!selected && <Segmented tabs={visibleTabs} tab={tab} setTab={changeTab} />}

            {/* Панель — под вкладками и только там, где под ней список: у обзора,
                классификации критериев и базы разборов своя выборка, и фильтр по
                сотруднику к ним отношения не имеет. В очереди ревью каждая
                карточка по определению не проверена человеком, поэтому балл и
                «есть оценка человека» там не показываем — они дали бы пусто. */}
            {!selected && (FILTERABLE_TABS.includes(tab)
                || (marketingAccess && MARKETING_FILTERABLE_TABS.includes(tab))) && (
                <QaFilters
                    filters={filters}
                    onChange={setFilters}
                    apiBaseUrl={apiBaseUrl}
                    withAccessTokenHeader={withAccessTokenHeader}
                    department={department}
                    /* «Обзор» и «База разборов» охватывают и звонки, и чаты,
                       поэтому справочники панели там — без вида субъекта. */
                    subject={tab === 'chats' ? SUBJECT_FAMILY_CHATS
                        : (tab === 'overview' || tab === 'rag') ? undefined : SUBJECT_FAMILY_CALLS}
                    showScoreFilters={tab !== 'queue'}
                    showReviewedFilter={tab !== 'queue'}
                    /* Выгрузка — по списку оценённых (та же выборка, что у
                       «Звонков» и «Чатов»); у очереди, «Обзора» и базы разборов
                       своя выборка, и кнопка там обещала бы не то, что на экране. */
                    canExport={marketingAccess && EXPORT_TABS.includes(tab)}
                    marketingAccess={marketingAccess}
                    marketingOnly={tab === 'rag'}
                    showToast={showToast}
                />
            )}

            {selected ? (
                <div ref={reviewShellRef} className="flex flex-col gap-3"
                     style={shellHeight ? { height: shellHeight } : undefined}>
                    <div className="flex shrink-0 items-center justify-between gap-2">
                        <h1 className="sr-only">ИИ-оценка</h1>
                        <button type="button" onClick={requestCloseCall} disabled={reviewInteraction.busy}
                            className={`${iosBtnGhost} disabled:cursor-not-allowed disabled:opacity-50`}>
                            <ChevronLeft size={16} />Назад
                        </button>
                        {callData && apiBaseUrl && (
                            <div className="flex flex-wrap items-center justify-end gap-2">
                                {!callData._cached && callData._previous_evaluation_stale && (
                                    <IosBadge tone="amber" title="Прежняя оценка сделана в устаревшей конфигурации ИИ (промпт, критерии или база знаний изменились), поэтому звонок переоценён заново.">
                                        прежняя оценка устарела — переоценено
                                    </IosBadge>
                                )}
                                {callData._stale && (
                                    <IosBadge tone="amber" title="Оценка сделана в устаревшей конфигурации ИИ (промпт, критерии или база знаний изменились с тех пор). Показана прежняя оценка без пересчёта — нажмите «Переоценить», чтобы оценить по актуальной конфигурации.">
                                        <RotateCcw size={11} />оценка устарела
                                    </IosBadge>
                                )}
                                {/* Технический бейдж на телефоне не показываем: строке над
                                    карточкой там не хватает места на действия. */}
                                <IosBadge tone={callData._cached ? 'slate' : 'green'} className="hidden sm:inline-flex">
                                    {callData._cached ? 'из кэша' : 'оценено сейчас'}
                                </IosBadge>
                                <button type="button" onClick={copyEvalLink} aria-label="Скопировать ссылку на оценку"
                                        title="Скопировать ссылку на эту оценку — её можно отправить коллеге"
                                        className={iosBtnGhost}>
                                    <Link2 size={14} aria-hidden="true" />
                                    <span className="hidden sm:inline">Ссылка</span>
                                </button>
                                <button type="button" onClick={requestReevaluation} disabled={callLoading || reviewInteraction.busy}
                                    className={`${iosBtnGhost} disabled:cursor-not-allowed disabled:opacity-50`}>
                                    {callLoading ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                                    {callLoading ? 'Переоцениваю…' : 'Переоценить'}
                                </button>
                                {callData.can_exclude !== false && !callData.exclusion && (
                                    <button type="button" onClick={requestExclude} aria-label="Исключить оценку"
                                            disabled={callLoading || reviewInteraction.busy} className={EXCLUDE_BTN}
                                            title="Оценка не нужна — нецелевой звонок или чат: убрать её из очереди, списков и сводок">
                                        {/* На телефоне — красная иконка, как кнопки панели
                                            iOS: три подписанные кнопки там в строку не входят. */}
                                        <Ban size={15} aria-hidden="true" />
                                        <span className="hidden sm:inline">Исключить оценку</span>
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                    {/* Исключённая оценка: почему и кто — и «Вернуть». Плашка над
                        карточкой, а не внутри неё: это состояние всей оценки, а не
                        одной из колонок. */}
                    {callData?.exclusion && !callPending && !callLoading && (
                        <div className="flex shrink-0 items-center gap-3 rounded-2xl bg-slate-50 px-4 py-2.5 ring-1 ring-slate-200/70"
                             role="status">
                            <Ban size={16} className="shrink-0 text-rose-500" aria-hidden="true" />
                            <div className="min-w-0 flex-1">
                                <p className="text-[13px] leading-snug text-slate-700">
                                    <span className="font-semibold text-slate-900">Оценка исключена</span>
                                    {exclusionLabel(callData.exclusion) && <> · {exclusionLabel(callData.exclusion)}</>}
                                </p>
                                {exclusionAuthor(callData.exclusion) && (
                                    <p className="mt-0.5 text-[12px] text-slate-500">{exclusionAuthor(callData.exclusion)}</p>
                                )}
                            </div>
                            {callData.can_exclude !== false && (
                                <button type="button" onClick={restoreExclusion} disabled={restoreBusy}
                                        className="inline-flex shrink-0 items-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-medium text-blue-600 transition-all hover:bg-blue-50 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 disabled:opacity-50">
                                    {restoreBusy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                                        : <RotateCcw size={14} aria-hidden="true" />}
                                    Вернуть
                                </button>
                            )}
                        </div>
                    )}
                    <div className="min-h-0 lg:flex-1">
                    {callPending ? (
                        <AudioPendingCard pending={callPending} subject={selected} polling={callLoading}
                                          onRetry={() => openCall(selected, false, { poll: true })}
                                          onClose={requestCloseCall} />
                    ) : callLoading ? (
                        <Spinner text={isChat(selected.subject)
                            ? `Оцениваю чат #${selected.id} — читаю вложения и анализирую…`
                            : `Оцениваю звонок #${selected.id} — распознавание и анализ…`} />
                    ) : callErr ? (
                        <ErrorCard text={callErr} onRetry={() => openCall(selected)} />
                    ) : (
                        <CallReviewCard key={callData?._evaluation_run_id || callData?.id} call={callData || undefined} onSkip={requestCloseCall}
                                        onSave={saveReview} onRefine={refineAdjud}
                                        onSimilar={similarAdjud}
                                        canCorrectJournal={canCorrectJournal}
                                        onInteractionChange={setReviewInteraction} />
                    )}
                    </div>
                </div>
            ) : (
                <div role="tabpanel" id={`qa-panel-${tab}`} aria-labelledby={`qa-tab-${tab}`}>
                {tab === 'queue' ? (
                queue === null ? <Spinner text="Загружаю очередь…" />
                    : queueErr ? <ErrorCard text="Не удалось загрузить очередь" onRetry={loadQueue} />
                    : queue.days.length === 0 ? (
                        /* «Всё проверено» при активном отборе — неправда: очередь
                           не пуста, пуст её срез. Сказать это прямо важнее, чем
                           похвалить: иначе человек уходит, не сняв фильтр. */
                        <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-14 text-center`}>
                            <CheckCircle2 size={26} className={filtersActive ? 'text-slate-300' : 'text-emerald-500'} />
                            <div>
                                <p className="text-[14px] font-semibold text-slate-700">
                                    {filtersActive ? 'Под фильтры ничего не подошло' : 'Всё проверено'}
                                </p>
                                <p className="mt-1 text-[12.5px] text-slate-500">
                                    {filtersActive
                                        ? 'Снимите часть фильтров или расширьте период — в очереди могут быть другие карточки.'
                                        : 'В очереди сейчас нет новых карточек для ревью.'}
                                </p>
                            </div>
                            {filtersActive ? (
                                <button type="button" onClick={() => setFilters(EMPTY_FILTERS)} className={iosBtnSecondary}>
                                    Сбросить фильтры
                                </button>
                            ) : (
                                <button type="button" onClick={loadQueue} className={iosBtnSecondary}>Обновить очередь</button>
                            )}
                        </div>
                    ) : (
                        <QueueDays days={queueDays} total={queue.total} truncated={queue.truncated}
                                   dayItems={queueDayItems} reviewedKeys={queueReviewedKeys}
                                   onOpen={openCall} onRefresh={loadQueue}
                                   onLoadMore={loadQueueDay} onRetryDay={loadQueueDay}
                                   openDay={queueDay} onCloseDay={closeQueueDay}
                                   onOpenDigest={openDigestDay}
                                   onOpenDay={(day, opts) => openQueueDay(day, { fromTile: !queueDay || Boolean(opts?.focusTitle) })} />
                    )
            ) : tab === 'chats' ? (
                <ChatQueue apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                           showToast={showToast} onOpen={openCall} department={department}
                           filters={filters} onResetFilters={() => setFilters(EMPTY_FILTERS)}
                           onFind={() => setFindOpen(true)} list={chatsDays}
                           onOpenDigest={openDigestDay} />
            ) : tab === 'digest' ? (
                <DigestView digest={digestDays} period={period} scoped={isScopedSupervisor}
                            chatRestore={digestChatRestore} onOpenRef={openFromDigest} />
            ) : tab === 'overview' ? (
                <QaDashboard apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                             department={department}
                             filters={marketingAccess ? filters : undefined} />
            ) : tab === 'evals' ? (
                /* Семейство, а не один вид: у СЗоВ и Тез КЦ звонок раздела приходит
                   из АТС и лежит в imported_calls, поэтому запрос одним `call`
                   показывал им пустую вкладку при живых оценках. */
                <EvaluationsList apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                                 onOpen={openCall} showToast={showToast}
                                 subject={SUBJECT_FAMILY_CALLS} list={callsDays}
                                 department={department} canPull={canPullCalls(department)}
                                 filters={filters}
                                 onResetFilters={() => setFilters(EMPTY_FILTERS)}
                                 onFind={() => setFindOpen(true)}
                                 onOpenDigest={openDigestDay} />
            ) : tab === 'criteria' ? (
                <CriteriaClassification showToast={showToast} apiBaseUrl={apiBaseUrl}
                                        withAccessTokenHeader={withAccessTokenHeader} directions={props.directions}
                                        department={department}
                                        onInteractionChange={setSectionInteraction} />
            ) : (
                <AdjudicationsRag key={department} apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                                   showToast={showToast} canManage={canManageRag}
                                   department={department} departmentName={departmentName}
                                   filters={marketingAccess ? filters : undefined}
                                   onInteractionChange={setSectionInteraction} />
            )}
                </div>
            )}
            {/* Точечный подбор: номер, сотрудник, период → конкретный звонок или
                переписка → карточка. Сотрудник и период предзаполняются из панели. */}
            <FindSubjectModal open={findOpen} onClose={() => setFindOpen(false)}
                              apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                              department={department}
                              family={tab === 'chats' ? SUBJECT_FAMILY_CHATS : SUBJECT_FAMILY_CALLS}
                              initialFilters={filters} showToast={showToast}
                              onOpen={(item) => { setFindOpen(false); openCall(item); }} />
            {/* Отдел — разговора, а не селектора: категории причин у отделов свои,
                а карточку могли открыть ссылкой или поиском из другого отдела. */}
            <ExcludeModal open={excludeOpen && Boolean(callData)} call={callData}
                          department={callData?.department || department}
                          apiBaseUrl={apiBaseUrl} headers={headers}
                          onClose={() => setExcludeOpen(false)} onExcluded={handleExcluded} />
            </>
            )}
        </div>
    );
}
