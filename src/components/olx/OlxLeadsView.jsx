import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, Download, ExternalLink, Loader2, MessageSquare, RefreshCw, Settings2,
} from 'lucide-react';
import {
    APPLE_FONT, IosBadge, IosModal, IosPager, IosSegmented, iosBtnGhost, iosBtnPrimary,
    iosBtnSecondary, iosCard, iosGroupLabel,
} from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
/* Арифметику высоты берём у «Обращений», а не переписываем: беда там была ровно
   эта — константа `calc(100vh-N)` под конкретную шапку, а шапка меняется. */
import { fitHeight, measureShell } from '../crm/layout';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import {
    ChatBubble, ChatComposer, ChatDayDivider, ChatEmpty, useThreadAutoScroll,
} from '../ui/chat';
import CustomSelect from '../ui/CustomSelect';
import {
    EVENT_TONE, RESULTS, RESULT_LABEL, RESULT_TONE, SLA_MS, STATE_LABEL, STATE_TONE,
    fmtAgo, fmtClock, fmtLatency, fmtPhone, fmtTime, fmtWaiting,
    groupByDay, isoToday, plural,
} from './olxMeta';

/*
 * Раздел «Лиды OLX» — работа с откликами из чатов девяти кабинетов (задача #223).
 *
 * Раздел открывают двое, и вопросы у них разные:
 *   маркетолог          «кому надо ответить прямо сейчас»
 *   руководитель ОП     «всё ли доезжает в CRM и сколько там заявок»
 *
 * Отсюда порядок вкладок: первая — ДИАЛОГИ, а не журнал. Журнал отвечает на
 * второй вопрос, а на первый отвечает переписка, и ради неё раздел и открывают.
 * Робот на повторное обращение молчит намеренно (решение владельца 02.09.2026:
 * второе автоматическое сообщение раздражает и читается как поломка), поэтому
 * отвечает здесь живой человек — прямо в этом экране, не уходя в кабинет OLX.
 *
 * Про цвет. Красится ТОЛЬКО отклонение: кабинет без доступа, ошибка, простой,
 * промах по SLA, ожидание ответа. Работающий кабинет и обычная строка журнала
 * остаются нейтральными — если раскрасить всё, «плохо» перестаёт бросаться в
 * глаза.
 *
 * Двухпанельная раскладка и лента сообщений повторяют «Обращения»: раздел
 * должен выглядеть частью продукта, а не ещё одним чатом. Пузыри, разделитель
 * дня, поле ввода и автопрокрутка взяты из общих примитивов ui/chat.jsx —
 * своих копий в проекте и так уже было три.
 */

const PAGE_SIZE = 50;

/* Пресеты периода журнала. Модульной константой, а не литералом внутри рендера:
   массив уходит пропсом в пикер. «Весь период» не даём намеренно — журнал
   копится вечно, и выгрузка без границ утянула бы всю таблицу. */
const shiftBack = (days) => {
    const value = new Date();
    value.setDate(value.getDate() - days);
    return isoDate(value);
};

const DATE_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: isoDate(new Date()), to: isoDate(new Date()) }) },
    { label: 'Неделя', range: () => ({ from: shiftBack(6), to: isoDate(new Date()) }) },
    { label: 'Месяц', range: () => ({ from: shiftBack(29), to: isoDate(new Date()) }) },
];

/* Кнопка пикера подменяется целиком, поэтому её класс повторяет ios-вариант
   CustomSelect — иначе чип в ряду фильтров был бы на пару пикселей другой.
   `[&>span]:flex-1` нужен, чтобы подпись растянулась и шеврон ушёл вправо. */
const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 '
    + 'text-left text-[12.5px] font-medium text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all hover:bg-slate-50 '
    + 'active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:text-left [&>span]:truncate';

/* Как часто сама подтягивается открытая переписка и список.
 *
 * Считаем от бюджета: лимит OLX — 4500 запросов с адреса за 5 минут, робот
 * съедает около двухсот. Открытая переписка при таком шаге — 25 запросов за
 * окно, список — 10. Даже десять открытых вкладок остаются в пределах десятой
 * части бюджета, поэтому «пореже» экономило бы то, чего и так вдоволь.
 *
 * Обновлять чаще смысла нет: сообщение всё равно попадает к нам не быстрее, чем
 * его увидит опрос робота, а он ходит дважды в минуту.
 *
 * Таймеры молчат на скрытой вкладке и у невидимых панелей — забытое окно не
 * должно ничего жечь. */
const THREAD_REFRESH_MS = 12000;
const LIST_REFRESH_MS = 30000;

const OlxLeadsView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    /* showToast приходит новой функцией на каждый рендер родителя, поэтому в
       зависимости эффектов он не идёт — иначе данные перезапрашивались бы на
       каждый чужой рендер. Держим его в ref и зовём оттуда. */
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((text, kind) => {
        if (toastRef.current) toastRef.current(text, kind);
    }, []);

    const [schemaReady, setSchemaReady] = useState(true);
    const [capabilities, setCapabilities] = useState(null);
    const [health, setHealth] = useState(null);
    const [tab, setTab] = useState('chats');
    const [settings, setSettings] = useState(false);

    useEffect(() => {
        axios.get(`${apiBaseUrl}/api/olx_amo/ping`, { headers: headers() })
            .then((response) => {
                setSchemaReady(response.data?.schema_ready !== false);
                setCapabilities(response.data?.capabilities || null);
            })
            .catch(() => setSchemaReady(false));
    }, [apiBaseUrl, headers]);

    const loadHealth = useCallback(() => {
        axios.get(`${apiBaseUrl}/api/olx_amo/health`, { headers: headers() })
            .then((response) => setHealth(response.data))
            .catch(() => setHealth(null));
    }, [apiBaseUrl, headers]);

    useEffect(() => {
        loadHealth();
        /* Состояние кабинетов обновляется само, даже когда модалка закрыта:
           знак беды в шапке должен зажечься сам, а не после того, как человек
           вспомнил заглянуть в настройки. */
        const timer = setInterval(() => {
            if (!document.hidden) loadHealth();
        }, LIST_REFRESH_MS);
        return () => clearInterval(timer);
    }, [loadHealth]);

    const cabinetOptions = useMemo(() => ([
        { value: '', label: 'Все кабинеты' },
        ...((health?.cabinets || []).map((c) => ({ value: c.code, label: c.title }))),
    ]), [health]);

    if (!schemaReady) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className="p-6">
                <div className={`${iosCard} p-6 text-[13.5px] text-slate-600`}>
                    Раздел ещё разворачивается. Обновите страницу через минуту.
                </div>
            </div>
        );
    }

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4 p-4 sm:p-6">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-[19px] font-semibold text-slate-900">Лиды OLX</h1>
                    <p className="text-[13px] text-slate-500">
                        Отклики из чатов кабинетов OLX и переписка с кандидатами
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <IosSegmented
                        ariaLabel="Что показывать"
                        value={tab}
                        onChange={setTab}
                        size="lg"
                        options={[
                            { value: 'chats', label: 'Диалоги' },
                            { value: 'journal', label: 'Журнал' },
                            { value: 'summary', label: 'Сводка' },
                        ]}
                    />
                    <SettingsButton health={health} onClick={() => setSettings(true)} />
                </div>
            </header>

            <SettingsModal
                open={settings}
                onClose={() => setSettings(false)}
                health={health}
                onRefresh={loadHealth}
                canManage={!!capabilities?.can_manage_cabinets}
                apiBaseUrl={apiBaseUrl} headers={headers} toast={toast}
            />

            {/* Панели монтируются один раз и прячутся классом, а не
                размонтируются. Иначе каждое переключение вкладки — это заново
                запрос, спиннер и потерянный выбранный диалог; переключаются же
                между «кому ответить» и «что доехало» постоянно.

                Невидимая панель не опрашивает сервер: `visible` глушит её
                таймеры, данные при этом остаются и показываются мгновенно. */}
            <div className={tab === 'chats' ? '' : 'hidden'}>
                <ChatWorkspace
                    apiBaseUrl={apiBaseUrl} headers={headers} toast={toast}
                    canReply={!!capabilities?.can_reply}
                    cabinetOptions={cabinetOptions}
                    visible={tab === 'chats'}
                    onAnswered={loadHealth}
                />
            </div>
            <div className={tab === 'journal' ? '' : 'hidden'}>
                <JournalPanel
                    apiBaseUrl={apiBaseUrl} headers={headers} toast={toast}
                    cabinetOptions={cabinetOptions} visible={tab === 'journal'} />
            </div>
            <div className={tab === 'summary' ? '' : 'hidden'}>
                <SummaryPanel apiBaseUrl={apiBaseUrl} headers={headers}
                    toast={toast} visible={tab === 'summary'} />
            </div>
        </div>
    );
};

/* Сколько кабинетов требуют внимания: без доступа, с ошибкой или молчащие. */
const troubled = (health) => (health?.cabinets || []).filter(
    (c) => c.is_enabled && (c.state !== 'ok' || c.is_stale));

/* ─── Кнопка настроек ────────────────────────────────────────────────────
 *
 * Состояние кабинетов занимало полэкрана каждый день, хотя нужно оно в двух
 * случаях: когда что-то сломалось и когда подключают новый кабинет. Поэтому в
 * шапке осталась одна кнопка, и она молчит, пока всё в порядке.
 *
 * Знак беды — единственное, что здесь красится: если раскрасить и спокойное
 * состояние, тревожное перестанет отличаться. */
const SettingsButton = ({ health, onClick }) => {
    const bad = troubled(health).length;
    return (
        <button
            type="button"
            onClick={onClick}
            aria-label={bad ? `Кабинеты: проблем ${bad}` : 'Кабинеты и уведомления'}
            className={`relative inline-flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-xl ring-1 transition active:scale-95 ${
                bad ? 'bg-amber-50 text-amber-600 ring-amber-200 hover:bg-amber-100'
                    : 'bg-slate-100 text-slate-500 ring-slate-200/70 hover:bg-slate-200/80'}`}
        >
            {bad ? <AlertTriangle size={16} /> : <Settings2 size={16} />}
            {bad > 0 && (
                <span className="absolute -right-1 -top-1 grid h-4 min-w-[16px] place-items-center rounded-full bg-amber-500 px-1 text-[10px] font-semibold tabular-nums text-white">
                    {bad}
                </span>
            )}
        </button>
    );
};

/* ─── Кабинеты и уведомления ─────────────────────────────────────────────
 *
 * Два блока в одной модалке намеренно: и то и другое — настройка робота, а не
 * ежедневная работа, и открывают их одной и той же мыслью «что там с ним».
 *
 * Работающий кабинет показывает только давность опроса: бейдж «Работает» девять
 * раз подряд был бы ровно той плашкой, мимо которой глаз перестаёт смотреть. */
const SettingsModal = ({ open, onClose, health, onRefresh, canManage,
                        apiBaseUrl, headers, toast }) => (
    <IosModal open={open} onClose={onClose} title="Кабинеты и уведомления"
        subtitle="Состояние робота и куда сообщать о сбоях" maxWidth="max-w-2xl">
        <div className="space-y-4">
            <CabinetsPanel health={health} onRefresh={onRefresh} />
            {canManage && (
                <AlertChatsPicker apiBaseUrl={apiBaseUrl} headers={headers} toast={toast}
                    visible={open} />
            )}
        </div>
    </IosModal>
);

const CabinetsPanel = ({ health, onRefresh }) => {
    if (!health) return null;
    const cabinets = health.cabinets || [];
    const working = cabinets.filter((c) => c.state === 'ok').length;
    const broken = cabinets.filter((c) => c.state !== 'ok' && c.is_enabled);
    /* Молчащие называем как на плитках — человеческим именем. Сервер отдаёт
       коды (`tez_olx`), и подставлять их в предупреждение значило бы просить
       читателя держать в голове справочник. */
    const stale = (health.stale || []).map(
        (code) => cabinets.find((c) => c.code === code)?.title || code);

    return (
        <section className="space-y-2">
            {stale.length > 0 && (
                <div className="flex items-center gap-2 rounded-2xl bg-amber-50 px-3.5 py-2.5 text-[13px] text-amber-800 ring-1 ring-amber-200">
                    <AlertTriangle size={15} className="shrink-0" />
                    {/* Порог — 15 минут из ТЗ: дольше робот молчать не должен. */}
                    Не опрашивались дольше {health.idle_minutes} мин: {stale.join(', ')}
                </div>
            )}
            <div className="rounded-2xl bg-white px-3.5 py-3 ring-1 ring-slate-200/70">
                <div className="mb-2 flex items-center justify-between gap-2">
                    <span className={iosGroupLabel}>Кабинеты</span>
                    <div className="flex items-center gap-2 text-[12px] text-slate-500">
                        {/* Пока всё хорошо — счёт работающих. Как только
                            появилась беда, счёт беды: «8 из 9 работают» рядом с
                            жёлтой шапкой отвечало не на тот вопрос, с которым
                            модалку открыли. */}
                        <span className={`tabular-nums ${troubled(health).length ? 'text-amber-700' : ''}`}>
                            {troubled(health).length
                                ? `${troubled(health).length} ${plural(troubled(health).length,
                                    'требует', 'требуют', 'требуют')} внимания`
                                : `${working} из ${cabinets.length} работают`}
                        </span>
                        <button type="button" onClick={onRefresh}
                            className="text-slate-400 transition hover:text-slate-600 active:scale-95"
                            aria-label="Обновить состояние кабинетов">
                            <RefreshCw size={13} />
                        </button>
                    </div>
                </div>
                {/* Колонок три, а не пять: ширину теперь задаёт модалка, и
                    пятая колонка ужала бы название кабинета до многоточия. */}
                <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                    {cabinets.map((cab) => {
                        /* Замолчавший кабинет тоже беда: состояние у него
                           «Работает», но данных из него нет. Без подсветки
                           плитка ТЭЗ выглядела бы обычной при жёлтой шапке. */
                        const bad = cab.is_enabled && (cab.state !== 'ok' || cab.is_stale);
                        return (
                            <div key={cab.code}
                                className={`rounded-xl px-2.5 py-2 ring-1 ${
                                    bad ? 'bg-amber-50/60 ring-amber-200'
                                        : 'bg-slate-50/70 ring-slate-200/70'}`}>
                                <div className="truncate text-[13px] font-medium text-slate-900">
                                    {cab.title}
                                </div>
                                <div className="mt-0.5">
                                    {bad && cab.state !== 'ok' ? (
                                        <IosBadge tone={STATE_TONE[cab.state] || 'slate'}>
                                            {STATE_LABEL[cab.state] || cab.state}
                                        </IosBadge>
                                    ) : (
                                        <span className={`text-[11.5px] tabular-nums ${
                                            bad ? 'text-amber-700' : 'text-slate-500'}`}>
                                            {fmtAgo(cab.last_poll_at)}
                                        </span>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
                {broken.length > 0 && broken[0].last_error && (
                    <p className="mt-2 line-clamp-2 text-[11.5px] text-slate-500">
                        {broken[0].title}: {broken[0].last_error}
                    </p>
                )}
            </div>
        </section>
    );
};

/* ─── Диалоги: список слева, переписка справа ───────────────────────────
 *
 * Раскладка повторяет «Обращения»: две панели в одной карточке, слева лента
 * 340 px, справа переписка. На телефоне видна ровно одна из них — переключение
 * по выбранному чату, иначе на узком экране обе сжались бы до нечитаемого.
 *
 * Высота задана вычислением, а не измерением. В «Обращениях» понадобился
 * ResizeObserver, потому что там над карточкой стоит ряд фильтров, который
 * переносится на вторую строку и меняет высоту; здесь над карточкой только
 * шапка раздела и полоса кабинетов постоянного размера. */
const ChatWorkspace = ({ apiBaseUrl, headers, toast, canReply, cabinetOptions,
                         visible, onAnswered }) => {
    const [threads, setThreads] = useState(null);
    const [cabinet, setCabinet] = useState('');
    const [onlyAwaiting, setOnlyAwaiting] = useState(false);
    const [selected, setSelected] = useState(null);

    const loadThreads = useCallback(() => {
        const params = new URLSearchParams();
        if (cabinet) params.set('cabinet', cabinet);
        if (onlyAwaiting) params.set('awaiting', '1');
        axios.get(`${apiBaseUrl}/api/olx_amo/threads?${params.toString()}`,
            { headers: headers() })
            .then((response) => setThreads(response.data?.items || []))
            .catch(() => setThreads([]));
    }, [apiBaseUrl, headers, cabinet, onlyAwaiting]);

    useEffect(() => {
        if (!visible) return undefined;
        loadThreads();
        const timer = setInterval(() => {
            if (!document.hidden) loadThreads();
        }, LIST_REFRESH_MS);
        return () => clearInterval(timer);
    }, [loadThreads, visible]);

    const awaiting = (threads || []).filter((t) => t.awaiting).length;

    /* Карточка занимает всё, что осталось до низа видимой области. Было
       `calc(100dvh-400px)`, где 400 — «шапка раздела, на глаз». Стоило убрать
       полосу кабинетов в модалку, как под карточкой повисла полоса пустоты в
       триста пикселей: вычитаемое считало то, чего на экране больше нет. */
    const shellRef = useRef(null);
    const [shellHeight, setShellHeight] = useState(null);

    useEffect(() => {
        const node = shellRef.current;
        if (!node || typeof ResizeObserver === 'undefined') return undefined;
        const recompute = () => setShellHeight(fitHeight(measureShell(node)));
        recompute();
        /* Наблюдаем и за прокрутчиком, и за шапкой раздела: на узком экране
           вкладки переносятся на вторую строку уже после первого кадра. */
        const observer = new ResizeObserver(recompute);
        observer.observe(node.closest('.main-content') || document.body);
        const header = document.querySelector('header');
        if (header) observer.observe(header);
        window.addEventListener('resize', recompute);
        return () => {
            observer.disconnect();
            window.removeEventListener('resize', recompute);
        };
    }, [visible]);

    return (
        <section ref={shellRef} className={`${iosCard} overflow-hidden`}>
            {/* Пока не измерено, высоту не навязываем: мигнуть неправильной
                хуже, чем один кадр по содержимому. */}
            <div className="flex min-h-[440px] flex-col lg:flex-row"
                style={shellHeight ? { height: shellHeight } : undefined}>
                {/* ── список диалогов ── */}
                <div className={`flex min-h-0 w-full flex-col border-slate-200/70 lg:w-[340px] lg:shrink-0 lg:border-r ${
                    selected ? 'hidden lg:flex' : 'flex'}`}>
                    <div className="space-y-2 border-b border-slate-200/70 px-3 py-2.5">
                        <CustomSelect value={cabinet} onChange={setCabinet}
                            options={cabinetOptions} variant="ios" />
                        <IosSegmented
                            ariaLabel="Какие диалоги показывать"
                            value={onlyAwaiting ? 'awaiting' : 'all'}
                            onChange={(v) => setOnlyAwaiting(v === 'awaiting')}
                            stretch
                            options={[
                                { value: 'all', label: 'Все' },
                                {
                                    value: 'awaiting',
                                    label: 'Ждут ответа',
                                    count: awaiting || undefined,
                                },
                            ]}
                        />
                    </div>
                    <div className="thin-scroll min-h-0 flex-1 overflow-y-auto">
                        {threads === null && (
                            <div className="p-6 text-center text-slate-400">
                                <Loader2 size={18} className="mx-auto animate-spin" />
                            </div>
                        )}
                        {threads !== null && !threads.length && (
                            <ChatEmpty icon={MessageSquare} title="Диалогов пока нет"
                                hint="Здесь появятся чаты кандидатов из кабинетов OLX" />
                        )}
                        <div className="divide-y divide-slate-100">
                            {(threads || []).map((item) => (
                                <ThreadRow
                                    key={`${item.cabinet}-${item.thread_id}`}
                                    item={item}
                                    active={!!selected
                                        && selected.cabinet === item.cabinet
                                        && selected.thread_id === item.thread_id}
                                    onSelect={() => setSelected(item)}
                                />
                            ))}
                        </div>
                    </div>
                </div>

                {/* ── переписка ── */}
                <div className={`min-h-0 min-w-0 flex-1 ${selected ? 'flex' : 'hidden lg:flex'}`}>
                    {selected ? (
                        /* key по чату: при переходе к другому диалогу компонент
                           пересоздаётся целиком, и не надо руками сбрасывать
                           черновик ответа и позицию прокрутки. */
                        <Conversation
                            key={`${selected.cabinet}-${selected.thread_id}`}
                            apiBaseUrl={apiBaseUrl} headers={headers} toast={toast}
                            canReply={canReply}
                            thread={selected}
                            visible={visible}
                            onBack={() => setSelected(null)}
                            onAnswered={() => { loadThreads(); onAnswered(); }}
                        />
                    ) : (
                        <div className="flex flex-1 items-center justify-center">
                            <ChatEmpty icon={MessageSquare} title="Выберите диалог"
                                hint="Слева — чаты кандидатов, сверху те, кто ждёт ответа" />
                        </div>
                    )}
                </div>
            </div>
        </section>
    );
};

const ThreadRow = ({ item, active, onSelect }) => (
    <button
        type="button"
        onClick={onSelect}
        className={`relative w-full px-3.5 py-2.5 text-left transition ${
            active ? 'bg-blue-50/70' : 'hover:bg-slate-50'}`}
    >
        {/* Полоска-акцент у ждущих ответа: единственный цвет в списке, потому
            что это единственное, что требует действия человека. */}
        {item.awaiting && (
            <span className="absolute inset-y-1 left-0 w-[3px] rounded-r bg-blue-500" />
        )}
        <div className="flex items-baseline gap-2">
            <span className="truncate text-[13.5px] font-medium text-slate-900">
                {item.interlocutor || 'Кандидат'}
            </span>
            <span className={`ml-auto shrink-0 text-[11.5px] tabular-nums ${
                item.awaiting ? 'text-blue-600' : 'text-slate-400'}`}>
                {item.awaiting ? `ждёт ${fmtWaiting(item.waiting_minutes)}`
                    : fmtTime(item.last_message_at)}
            </span>
        </div>
        <div className="mt-0.5 truncate text-[12.5px] text-slate-500">
            {item.cabinet_title}
            {item.advert_title ? ` · ${item.advert_title}` : ''}
        </div>
        {(item.phone || item.amo_lead_id) && (
            <div className="mt-1 flex items-center gap-2 text-[11.5px] tabular-nums text-slate-400">
                {item.phone && <span>{fmtPhone(item.phone)}</span>}
                {item.amo_lead_id && <span>сделка {item.amo_lead_id}</span>}
            </div>
        )}
    </button>
);

/* Переписка с кандидатом.
 *
 * Сообщения читаются из OLX по запросу — своей копии переписки раздел не
 * держит: она немедленно начала бы расходиться с оригиналом. А вот АВТОРСТВО
 * наших сообщений берётся из нашей базы: у OLX сообщение помечено только
 * направлением, и робот, ответ из раздела и написанное руками прямо в кабинете
 * там неразличимы. */
const Conversation = ({ apiBaseUrl, headers, toast, canReply, thread, onBack,
                        onAnswered, visible }) => {
    const [data, setData] = useState(null);
    const [draft, setDraft] = useState('');
    const [busy, setBusy] = useState(false);
    const { boxRef, onScroll } = useThreadAutoScroll(data?.messages?.length);

    const url = `${apiBaseUrl}/api/olx_amo/threads/${thread.cabinet}/${thread.thread_id}`;

    const load = useCallback(() => {
        axios.get(url, { headers: headers() })
            .then((response) => setData(response.data))
            .catch((err) => setData({
                error: err?.response?.data?.error || 'Переписка не открылась',
                messages: [],
            }));
    }, [url, headers]);

    useEffect(() => {
        if (!visible) return undefined;
        load();
        const timer = setInterval(() => {
            if (!document.hidden) load();
        }, THREAD_REFRESH_MS);
        return () => clearInterval(timer);
    }, [load, visible]);

    const send = (text) => {
        setBusy(true);
        axios.post(`${url}/reply`, { text }, { headers: headers() })
            .then(() => {
                /* Поле очищаем только после успеха: не ушедший текст человек
                   второй раз не напишет. */
                setDraft('');
                load();
                onAnswered();
            })
            .catch((err) => toast(
                err?.response?.data?.error || 'Сообщение не ушло', 'error'))
            .finally(() => setBusy(false));
    };

    const groups = useMemo(() => groupByDay(data?.messages || []), [data]);

    return (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <header className="flex items-center gap-2 border-b border-slate-200/70 px-3.5 py-2.5">
                <button type="button" onClick={onBack}
                    className="shrink-0 text-[13px] text-blue-600 lg:hidden">← Назад</button>
                <div className="min-w-0">
                    <div className="truncate text-[14px] font-semibold text-slate-900">
                        {data?.interlocutor || thread.interlocutor || 'Кандидат'}
                    </div>
                    <div className="truncate text-[12px] text-slate-500">
                        {thread.cabinet_title}
                        {(data?.advert_title || thread.advert_title)
                            ? ` · ${data?.advert_title || thread.advert_title}` : ''}
                    </div>
                </div>
                {data?.url && (
                    <a href={data.url} target="_blank" rel="noreferrer"
                        className="ml-auto inline-flex shrink-0 items-center gap-1 text-[12.5px] text-slate-400 transition hover:text-slate-600">
                        В кабинете <ExternalLink size={13} />
                    </a>
                )}
            </header>

            <div ref={boxRef} onScroll={onScroll}
                className="thin-scroll min-h-0 flex-1 space-y-1.5 overflow-y-auto bg-slate-50/60 py-3">
                {data === null && (
                    <div className="p-6 text-center text-slate-400">
                        <Loader2 size={18} className="mx-auto animate-spin" />
                    </div>
                )}
                {data?.error && (
                    <div className="mx-4 rounded-xl bg-rose-50 px-3 py-2 text-[13px] text-rose-700 ring-1 ring-rose-200">
                        {data.error}
                    </div>
                )}
                {data && !data.error && !data.messages.length && (
                    <ChatEmpty icon={MessageSquare} title="Сообщений нет" />
                )}
                {groups.map((group) => (
                    <div key={group.key} className="space-y-1.5">
                        <ChatDayDivider>{group.label}</ChatDayDivider>
                        {group.messages.map((message) => (message.event ? (
                            <TimelineEvent key={message.id} item={message} />
                        ) : (
                            <ChatBubble
                                key={message.id}
                                out={message.outgoing}
                                tone={message.failed ? 'warn' : null}
                                meta={(
                                    <>
                                        {message.author && <span>{message.author}</span>}
                                        <span className="tabular-nums">{fmtClock(message.at)}</span>
                                        {message.failed && <span>· не отправлено</span>}
                                    </>
                                )}
                            >
                                {message.text}
                                {(message.cvs || []).map((cv) => (
                                    <a key={cv.url} href={cv.url} target="_blank" rel="noreferrer"
                                        className="mt-1 block underline underline-offset-2">
                                        {cv.name || 'Резюме'}
                                    </a>
                                ))}
                            </ChatBubble>
                        )))}
                    </div>
                ))}
            </div>

            {canReply ? (
                <ChatComposer
                    value={draft}
                    onChange={setDraft}
                    onSubmit={send}
                    busy={busy}
                    disabled={!!data?.error}
                    placeholder="Ответьте кандидату…"
                    maxLength={2000}
                    submitLabel="Отправить"
                    busyLabel="Отправляем…"
                    hint="Сообщение уйдёт в чат OLX от имени компании. Enter — отправить"
                />
            ) : (
                <div className="border-t border-slate-200/70 px-3.5 py-2.5 text-[12.5px] text-slate-500">
                    Отвечать кандидатам вам нельзя — откройте чат в кабинете OLX.
                </div>
            )}
        </div>
    );
};

/* Системная отметка в ленте: «Создана сделка», «Повтор за сутки» и подобное.
 *
 * По центру и мельче реплик — это не разговор, а пометка на полях. Без них
 * переписка выглядит как беседа без последствий, и маркетолог идёт проверять
 * amoCRM руками; с ними видно прямо здесь, что отклик доехал. */
const TimelineEvent = ({ item }) => {
    const tone = EVENT_TONE[item.event] || 'slate';
    const skin = {
        green: 'bg-emerald-50 text-emerald-700 ring-emerald-100',
        amber: 'bg-amber-50 text-amber-700 ring-amber-100',
        blue: 'bg-blue-50 text-blue-700 ring-blue-100',
        red: 'bg-rose-50 text-rose-600 ring-rose-100',
        slate: 'bg-slate-100 text-slate-500 ring-slate-200/60',
    }[tone];

    return (
        <div className="flex justify-center px-4 py-0.5">
            <div className={`max-w-[86%] rounded-full px-3 py-1 text-center text-[11.5px] ring-1 ${skin}`}>
                <span>{item.text}</span>
                {item.amo_url && (
                    <a href={item.amo_url} target="_blank" rel="noreferrer"
                        className="ml-1.5 underline underline-offset-2">
                        №{item.amo_lead_id}
                    </a>
                )}
                {item.latency_ms != null && (
                    <span className="ml-1.5 tabular-nums opacity-70">
                        за {fmtLatency(item.latency_ms)}
                    </span>
                )}
                <span className="ml-1.5 tabular-nums opacity-70">{fmtClock(item.at)}</span>
                {item.error && (
                    <div className="mt-0.5 opacity-80">{item.error}</div>
                )}
            </div>
        </div>
    );
};

/* ─── Журнал ─────────────────────────────────────────────────────────── */
const JournalPanel = ({ apiBaseUrl, headers, toast, cabinetOptions, visible }) => {
    const [journal, setJournal] = useState({ items: [], total: 0 });
    const [loading, setLoading] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [page, setPage] = useState(0);
    const [cabinet, setCabinet] = useState('');
    const [result, setResult] = useState('');
    const [dateFrom, setDateFrom] = useState(isoToday());
    const [dateTo, setDateTo] = useState(isoToday());

    const params = useCallback(() => {
        const search = new URLSearchParams();
        if (cabinet) search.set('cabinet', cabinet);
        if (result) search.set('result', result);
        if (dateFrom) search.set('date_from', dateFrom);
        if (dateTo) search.set('date_to', dateTo);
        return search;
    }, [cabinet, result, dateFrom, dateTo]);

    const load = useCallback(() => {
        setLoading(true);
        const search = params();
        search.set('limit', String(PAGE_SIZE));
        search.set('offset', String(page * PAGE_SIZE));
        axios.get(`${apiBaseUrl}/api/olx_amo/journal?${search.toString()}`,
            { headers: headers() })
            .then((response) => setJournal(response.data || { items: [], total: 0 }))
            .catch(() => toast('Не удалось загрузить журнал', 'error'))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers, page, params, toast]);

    useEffect(() => { if (visible) load(); }, [load, visible]);
    /* Смена фильтра возвращает на первую страницу: иначе после сужения выборки
       человек оказывается на пустой десятой странице и решает, что данных нет. */
    useEffect(() => { setPage(0); }, [cabinet, result, dateFrom, dateTo]);

    const exportJournal = () => {
        setExporting(true);
        /* Файл забираем запросом, а не ссылкой: ссылка не несёт заголовок
           авторизации, и сервер ответил бы на неё отказом. */
        axios.get(`${apiBaseUrl}/api/olx_amo/journal/export?${params().toString()}`,
            { headers: headers(), responseType: 'blob' })
            .then((response) => {
                const href = window.URL.createObjectURL(new Blob([response.data]));
                const link = document.createElement('a');
                link.href = href;
                link.download = `Лиды OLX ${dateFrom || ''}.xlsx`.trim();
                document.body.appendChild(link);
                link.click();
                link.remove();
                window.URL.revokeObjectURL(href);
            })
            .catch(() => toast('Не удалось выгрузить журнал', 'error'))
            .finally(() => setExporting(false));
    };

    const pageCount = Math.max(1, Math.ceil((journal.total || 0) / PAGE_SIZE));

    return (
        <section className="space-y-3">
            <div className="flex flex-wrap items-end gap-2.5">
                {/* Наш пикер, а не системный input[type=date]: тот выглядит
                    по-своему в каждом браузере и не знает ни пресетов, ни
                    русских подписей. */}
                <div className="w-52">
                    <span className={iosGroupLabel}>Период</span>
                    <IosDateRangePicker
                        from={dateFrom} to={dateTo}
                        max={isoDate(new Date())}
                        presets={DATE_PRESETS}
                        triggerClassName={DATE_TRIGGER}
                        onChange={({ from, to }) => {
                            /* Один клик по дню отдаёт `to` пустым — это ещё не
                               «по сегодня», а выбранный день целиком. */
                            setDateFrom(from || '');
                            setDateTo(to || from || '');
                        }}
                    />
                </div>
                <div className="w-44">
                    <span className={iosGroupLabel}>Кабинет</span>
                    <CustomSelect value={cabinet} onChange={setCabinet}
                        options={cabinetOptions} variant="ios" />
                </div>
                <div className="w-48">
                    <span className={iosGroupLabel}>Исход</span>
                    <CustomSelect value={result} onChange={setResult}
                        options={RESULTS} variant="ios" />
                </div>
                <button type="button" onClick={exportJournal} disabled={exporting}
                    className={`${iosBtnGhost} ml-auto disabled:opacity-40 active:scale-[0.98]`}>
                    <Download size={15} />
                    {exporting ? 'Готовим…' : 'Выгрузить'}
                </button>
            </div>

            <div className={`${iosCard} overflow-hidden`}>
                <div className="overflow-x-auto">
                    <table className="w-full text-[13px]">
                        <thead className="bg-slate-50/80 text-left text-[11.5px] uppercase tracking-wide text-slate-500">
                            <tr>
                                <th className="px-3 py-2 font-medium">Время отклика</th>
                                <th className="px-3 py-2 font-medium">Кабинет</th>
                                <th className="px-3 py-2 font-medium">Телефон</th>
                                <th className="px-3 py-2 font-medium">Исход</th>
                                <th className="px-3 py-2 font-medium">Доставка</th>
                                <th className="px-3 py-2 font-medium">Сделка</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loading && !journal.items.length && (
                                <tr><td colSpan={6} className="px-3 py-10 text-center text-slate-400">
                                    <Loader2 size={18} className="mx-auto animate-spin" />
                                </td></tr>
                            )}
                            {!loading && !journal.items.length && (
                                <tr><td colSpan={6} className="px-3 py-10 text-center text-[13px] text-slate-400">
                                    За выбранный период обращений нет
                                </td></tr>
                            )}
                            {journal.items.map((row) => (
                                <tr key={row.id} className="border-t border-slate-100">
                                    <td className="whitespace-nowrap px-3 py-2 tabular-nums text-slate-600">
                                        {fmtTime(row.message_at || row.created_at)}
                                    </td>
                                    <td className="px-3 py-2 text-slate-700">{row.cabinet_title}</td>
                                    <td className="whitespace-nowrap px-3 py-2 tabular-nums text-slate-900">
                                        {row.phone ? fmtPhone(row.phone) : (row.phone_raw || '—')}
                                    </td>
                                    <td className="px-3 py-2">
                                        <IosBadge tone={RESULT_TONE[row.result] || 'slate'}>
                                            {RESULT_LABEL[row.result] || row.result}
                                        </IosBadge>
                                        {row.error_text && (
                                            <div className="mt-1 max-w-md text-[11.5px] text-rose-600">
                                                {row.error_text}
                                            </div>
                                        )}
                                    </td>
                                    {/* Промах по SLA — единственное, что здесь красится:
                                        ТЗ задаёт минуту как целевой показатель. */}
                                    <td className={`whitespace-nowrap px-3 py-2 tabular-nums ${
                                        row.latency_ms > SLA_MS ? 'text-amber-700' : 'text-slate-600'}`}>
                                        {fmtLatency(row.latency_ms)}
                                    </td>
                                    <td className="px-3 py-2 tabular-nums text-slate-500">
                                        {row.amo_lead_id || '—'}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {/* IosPager нумерует страницы С ЕДИНИЦЫ, а состояние здесь с нуля:
                    из него умножением получается offset. Переводим на границе. */}
                <div className="border-t border-slate-100 px-3 py-2">
                    <IosPager
                        page={page + 1}
                        pageCount={pageCount}
                        total={journal.total}
                        from={page * PAGE_SIZE + 1}
                        to={Math.min((page + 1) * PAGE_SIZE, journal.total)}
                        onPage={(number) => setPage(number - 1)}
                        unit="обращения"
                    />
                </div>
            </div>
        </section>
    );
};

/* ─── Сводка за день ─────────────────────────────────────────────────── */
const SummaryPanel = ({ apiBaseUrl, headers, toast, visible }) => {
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(false);
    const [day, setDay] = useState(isoToday());

    const toastRef = useRef(toast);
    useEffect(() => { toastRef.current = toast; }, [toast]);

    useEffect(() => {
        if (!visible) return;
        setLoading(true);
        axios.get(`${apiBaseUrl}/api/olx_amo/summary?day=${day}`, { headers: headers() })
            .then((response) => setSummary(response.data))
            .catch(() => toastRef.current('Не удалось загрузить сводку', 'error'))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers, day, visible]);

    const rows = summary?.cabinets || [];
    const totals = summary?.totals || {};

    return (
        <section className="space-y-3">
            <div className="w-44">
                <span className={iosGroupLabel}>День</span>
                <IosDatePicker value={day} onChange={setDay}
                    max={isoDate(new Date())} ariaLabel="День сводки"
                    triggerClassName={DATE_TRIGGER} />
            </div>

            <div className={`${iosCard} overflow-hidden`}>
                <div className="overflow-x-auto">
                    <table className="w-full text-[13px]">
                        <thead className="bg-slate-50/80 text-left text-[11.5px] uppercase tracking-wide text-slate-500">
                            <tr>
                                <th className="px-3 py-2 font-medium">Кабинет</th>
                                <th className="px-3 py-2 text-right font-medium">Обращений</th>
                                <th className="px-3 py-2 text-right font-medium">Сделок</th>
                                <th className="px-3 py-2 text-right font-medium">Повторов</th>
                                <th className="px-3 py-2 text-right font-medium">На проверку</th>
                                <th className="px-3 py-2 text-right font-medium">Автоответов</th>
                                <th className="px-3 py-2 text-right font-medium">Ошибок</th>
                                <th className="px-3 py-2 text-right font-medium">Вне минуты</th>
                                <th className="px-3 py-2 text-right font-medium">Среднее</th>
                                <th className="px-3 py-2 text-right font-medium">Максимум</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loading && !rows.length && (
                                <tr><td colSpan={10} className="px-3 py-10 text-center text-slate-400">
                                    <Loader2 size={18} className="mx-auto animate-spin" />
                                </td></tr>
                            )}
                            {!loading && !rows.length && (
                                <tr><td colSpan={10} className="px-3 py-10 text-center text-[13px] text-slate-400">
                                    За этот день обращений не было
                                </td></tr>
                            )}
                            {rows.map((row) => (
                                <tr key={row.code} className="border-t border-slate-100">
                                    <td className="px-3 py-2 text-slate-700">{row.title}</td>
                                    <td className="px-3 py-2 text-right tabular-nums">{row.total}</td>
                                    <td className="px-3 py-2 text-right font-medium tabular-nums">{row.leads}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{row.duplicates}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${row.manual ? 'text-amber-700' : 'text-slate-500'}`}>{row.manual}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{row.replies}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${row.errors ? 'text-rose-600' : 'text-slate-500'}`}>{row.errors}</td>
                                    <td className={`px-3 py-2 text-right tabular-nums ${row.sla_missed ? 'text-amber-700' : 'text-slate-500'}`}>{row.sla_missed}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-600">{fmtLatency(row.avg_latency_ms)}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-600">{fmtLatency(row.max_latency_ms)}</td>
                                </tr>
                            ))}
                        </tbody>
                        {rows.length > 1 && (
                            <tfoot className="border-t border-slate-200 bg-slate-50/60">
                                <tr>
                                    <td className="px-3 py-2 font-medium text-slate-700">Итого</td>
                                    <td className="px-3 py-2 text-right tabular-nums">{totals.total || 0}</td>
                                    <td className="px-3 py-2 text-right font-medium tabular-nums">{totals.leads || 0}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{totals.duplicates || 0}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{totals.manual || 0}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{totals.replies || 0}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{totals.errors || 0}</td>
                                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{totals.sla_missed || 0}</td>
                                    <td className="px-3 py-2" />
                                    <td className="px-3 py-2" />
                                </tr>
                            </tfoot>
                        )}
                    </table>
                </div>
            </div>
        </section>
    );
};

/* ─── Куда слать отбивку о сбоях ────────────────────────────────────────
 *
 * Свой реестр групп раздел не заводит: те, куда добавлен бот, уже копятся в
 * общей таблице портала, и оттуда же берут списки «Обращения» и «Бот
 * опозданий». Здесь только выбор.
 *
 * Живёт в модалке настроек и виден только тому, кто вправе его менять. Своего
 * сворачивания у блока больше нет: модалка и так открывается по нужде, а
 * «Настроить» внутри неё было бы вторым щелчком за ту же мысль. */
const AlertChatsPicker = ({ apiBaseUrl, headers, toast, visible }) => {
    const [chats, setChats] = useState(null);
    const [saving, setSaving] = useState(false);
    const [draft, setDraft] = useState(null);

    const load = useCallback(() => {
        axios.get(`${apiBaseUrl}/api/olx_amo/chats`, { headers: headers() })
            .then((response) => setChats(response.data))
            .catch(() => setChats(null));
    }, [apiBaseUrl, headers]);

    useEffect(() => { if (visible) load(); }, [load, visible]);

    const chosen = useMemo(() => (chats?.chosen_ids || []).map(Number), [chats]);
    const options = useMemo(() => (chats?.available || []).map((chat) => ({
        value: Number(chat.chat_id),
        label: chat.title || String(chat.chat_id),
    })), [chats]);
    const chosenTitles = useCallback((values) => (values || [])
        .map((id) => options.find((o) => String(o.value) === String(id))?.label || id)
        .join(', '), [options]);

    if (!chats) return null;

    const value = draft === null ? chosen : draft;
    const lost = (chats.selected || []).filter((row) => row.is_available === false);

    const save = () => {
        setSaving(true);
        axios.put(`${apiBaseUrl}/api/olx_amo/chats`, { chat_ids: value },
            { headers: headers() })
            .then(() => {
                toast('Адресаты отбивки сохранены', 'success');
                setDraft(null);
                load();
            })
            .catch((err) => toast(
                err?.response?.data?.error || 'Не удалось сохранить', 'error'))
            .finally(() => setSaving(false));
    };

    return (
        <section className="rounded-2xl bg-white px-3.5 py-3 ring-1 ring-slate-200/70">
            <div className="flex items-center gap-2">
                <MessageSquare size={14} className="shrink-0 text-slate-400" />
                <span className="text-[13.5px] font-medium text-slate-900">
                    Уведомления о сбоях
                </span>
                {/* Счётчик показываем только у пустого выбора — это
                    предупреждение. Выбранные группы и так названы в поле ниже,
                    и «1 группа» рядом с их именами было бы тем же дважды. */}
                {!chosen.length && (
                    <span className="ml-auto shrink-0 text-[12.5px] text-amber-700">
                        не настроены
                    </span>
                )}
            </div>

            <div className="mt-2.5 space-y-2.5">
                <p className="text-[12.5px] text-slate-500">
                    Куда сообщать о простое робота, потере доступа к кабинету и об
                    ошибках передачи в amoCRM. В списке — группы, куда уже добавлен
                    бот. Сообщение уходит только при смене состояния.
                </p>
                {options.length ? (
                    /* В кнопке — имена групп, а не «Выбрано: N»: у отбивки
                       адресатов один-два, и важно ровно то, КУДА она уходит. */
                    <CustomSelect multiple value={value} onChange={setDraft}
                        options={options} placeholder="Выберите группы" searchable
                        variant="ios" renderValue={chosenTitles} />
                ) : (
                    <p className="text-[12.5px] text-amber-700">
                        Бот пока не добавлен ни в одну группу. Добавьте его туда, где
                        должна приходить отбивка, — группа появится в списке сама.
                    </p>
                )}
                {lost.length > 0 && (
                    <p className="text-[12.5px] text-amber-700">
                        Бота больше нет в группах: {lost.map((r) => r.title || r.chat_id).join(', ')}.
                        Отбивка туда не дойдёт.
                    </p>
                )}
                {/* Кнопки показываем только при несохранённой правке: пара
                    вечно серых кнопок под списком читалась бы как поломка. */}
                {draft !== null && (
                    <div className="flex justify-end gap-2">
                        <button type="button" onClick={() => setDraft(null)}
                            className={iosBtnSecondary}>Отмена</button>
                        <button type="button" onClick={save} disabled={saving}
                            className={`${iosBtnPrimary} disabled:opacity-40 active:scale-[0.98]`}>
                            {saving ? 'Сохраняем…' : 'Сохранить'}
                        </button>
                    </div>
                )}
            </div>
        </section>
    );
};

export default OlxLeadsView;
