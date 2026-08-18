import { useCallback, useSyncExternalStore } from 'react';

/*
 * Общая начинка «Табло СЗоВ»: пороги, форматирование, каталог показателей и единый снимок.
 *
 * Табло живёт в двух местах: сам раздел (экран на стене) и виджет «поверх окон», где каждый
 * сам выбирает, что мониторить. Пороги и опрос вынесены сюда, потому что две копии рано или
 * поздно разойдутся: на стене AR горит зелёным, а в виджете красным — и верить нельзя ни тому,
 * ни другому. Значение и цвет каждого показателя описаны РОВНО один раз — в каталоге ниже.
 */

// Опрос под TTL серверного кэша (13 с). Прокси Oktell низкоконкурентный и иногда подвисает
// на установке соединения, поэтому лишний раз его не дёргаем: 15 с для стены достаточно.
const POLL_INTERVAL_MS = 15000;

// Направление «Чат» опрашиваем реже: квота Chat2Desk общая на всю компанию и уже расходуется
// ночным синком и почасовым отчётом. Сервер всё равно держит снимок 2 минуты, поэтому чаще
// спрашивать нечего — придёт тот же ответ, только счётчик квоты потратится.
const CHAT_POLL_INTERVAL_MS = 60000;

// Пробуждение вкладки (focus/visibilitychange) прилетает пачками — при переключении окон
// событие приходит на каждый чих. Снимок свежее этого порога перезапрашивать незачем.
const WAKE_REFRESH_MIN_AGE_MS = 5000;

/*
 * AR — не «чем меньше, тем лучше», а коридор (правило владельца): норма 3…5 %.
 * Ниже 3 % — тоже отклонение, а не успех: значит операторов на линии больше, чем нужно.
 * Коридор сплошной, промежуточного цвета нет: либо в норме, либо нет.
 */
export const AR_MIN_PERCENT = 3;
export const AR_MAX_PERCENT = 5;

export const arTone = (ratio) => {
    if (ratio === null || ratio === undefined || !Number.isFinite(Number(ratio))) return 'neutral';
    const percent = Number(ratio) * 100;
    return percent >= AR_MIN_PERCENT && percent <= AR_MAX_PERCENT ? 'good' : 'bad';
};

/*
 * SL — доля звонков, отвеченных в пределах порога ожидания, ко ВСЕМ попавшим в очередь.
 * Пороги те же, что в отчёте «Расчёт ресурсов -> Биллинг» (ResourceFteView), иначе одна и та
 * же цифра горела бы на табло и в отчёте разными цветами.
 */
export const SL_GOOD_RATIO = 0.8;
export const SL_WARN_RATIO = 0.6;

export const slTone = (ratio) => {
    if (ratio === null || ratio === undefined || !Number.isFinite(Number(ratio))) return 'neutral';
    const value = Number(ratio);
    if (value >= SL_GOOD_RATIO) return 'good';
    if (value >= SL_WARN_RATIO) return 'warn';
    return 'bad';
};

export const formatInt = (value) => (Number.isFinite(Number(value)) ? Number(value).toLocaleString('ru-RU') : '—');

export const formatPercent = (ratio, digits = 1) => (
    ratio === null || ratio === undefined || !Number.isFinite(Number(ratio))
        ? '—'
        : `${(Number(ratio) * 100).toFixed(digits).replace('.', ',')}%`
);

/** Секунды -> «М:СС» (или «Ч:ММ:СС» для долгих ожиданий). Компактно, читаемо с расстояния. */
export const formatDuration = (seconds) => {
    const total = Number(seconds);
    if (!Number.isFinite(total) || total < 0) return '—';
    const whole = Math.round(total);
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    const secs = whole % 60;
    if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    return `${minutes}:${String(secs).padStart(2, '0')}`;
};

/** «12:07:18» из «2026-08-03 12:07:18» — на табло дата не нужна, только время источника. */
export const formatClock = (value) => {
    const text = String(value || '').trim();
    const match = text.match(/(\d{2}):(\d{2}):(\d{2})/);
    return match ? match[0] : '—';
};

/*
 * Цвета статусов заданы владельцем: перерыв — оранжевый, тренинг — зелёный,
 * тех.причина — фиолетовый. Перезвон владелец не называл; взяли синий (акцент приложения),
 * чтобы он не сливался с тремя остальными.
 */
export const STATUS_STYLE = {
    break: { label: 'Перерыв', chip: 'bg-orange-100 text-orange-700' },
    training: { label: 'Тренинг', chip: 'bg-emerald-100 text-emerald-700' },
    tech: { label: 'Тех.причина', chip: 'bg-violet-100 text-violet-700' },
    recall: { label: 'Перезвон', chip: 'bg-blue-100 text-blue-700' },
};

/*
 * Статусы чатников (Chat2Desk). Цвета взяты у статусов линии, чтобы один и тот же смысл на
 * обоих направлениях табло горел одинаково: перерыв — оранжевый, тренинг — зелёный, онлайн —
 * синий. «Занят» на линии нет, поэтому ему достался фиолетовый — свободный цвет из той же
 * палитры. Отпуск и «не в системе» серые: это отсутствие, а не состояние работы.
 */
export const CHAT_STATUS_STYLE = {
    online: { label: 'Онлайн', chip: 'bg-blue-100 text-blue-700' },
    busy: { label: 'Занят', chip: 'bg-violet-100 text-violet-700' },
    training: { label: 'Тренинг', chip: 'bg-emerald-100 text-emerald-700' },
    break: { label: 'Перерыв', chip: 'bg-orange-100 text-orange-700' },
    tech: { label: 'Тех. перерыв', chip: 'bg-orange-100 text-orange-700' },
    holiday: { label: 'Отпуск', chip: 'bg-slate-100 text-slate-600' },
    offline: { label: 'Не в системе', chip: 'bg-slate-100 text-slate-500' },
};

/*
 * Секунды -> «12,7 мин». Ось графика и плитки чатов живут в минутах: цель тоже задана в них.
 * withUnit=false отдаёт голое число — плитка рисует «мин» отдельным приглушённым суффиксом.
 */
export const formatMinutes = (seconds, digits = 1, withUnit = true) => (
    // null проверяем отдельно: Number(null) === 0, и «нет данных» превратилось бы в «0,0 мин» —
    // то есть в идеальный показатель там, где считать вообще нечего.
    seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))
        ? '—'
        : `${(Number(seconds) / 60).toFixed(digits).replace('.', ',')}${withUnit ? ' мин' : ''}`
);

/*
 * Тон показателя. Два вида, и путать их нельзя:
 *   - оценочный (good/warn/bad) — «в норме или нет»: очередь, AR, SL, потери;
 *   - опознавательный (info/violet и зелёный у тренинга) — идентичность статуса, а не оценка:
 *     онлайн синий, перерыв оранжевый, тренинг зелёный, тех.причина фиолетовая — те же цвета,
 *     что у чипов статусов выше, чтобы счётчик и список читались как одно и то же.
 * Всё остальное — neutral: цвет достаётся только тому, что несёт смысл.
 */
export const WALLBOARD_TONE_TEXT = {
    neutral: 'text-slate-900',
    good: 'text-emerald-600',
    warn: 'text-amber-600',
    bad: 'text-rose-600',
    info: 'text-blue-600',
    violet: 'text-violet-600',
};

/*
 * Виджет табло существует только как окно поверх других окон (Document Picture-in-Picture).
 * Проверка здесь, а не в самом виджете: раздел спрашивает то же самое, чтобы не предлагать
 * кнопку там, где окна не будет.
 */
export const canOpenWallboardWidget = () => (
    typeof window !== 'undefined' && Boolean(window.documentPictureInPicture?.requestWindow)
);

export const WALLBOARD_METRIC_GROUPS = [
    { key: 'line', title: 'Линия сейчас' },
    { key: 'people', title: 'Операторы сейчас' },
    { key: 'today', title: 'За день' },
    { key: 'lists', title: 'Списки' },
];

/*
 * Каталог показателей: единственное место, где сказано «как называется, как считается, каким
 * цветом». Раздел берёт отсюда цифры для своих плиток, виджет — ещё и список того, что вообще
 * можно вывести. Появился новый показатель в снапшоте — добавляется одна запись, и он сразу
 * доступен обоим.
 *
 * kind: 'tile' — плитка с числом, 'pair' — «главное / приглушённое», 'list' — перечень людей.
 */
export const WALLBOARD_METRICS = [
    {
        key: 'queue',
        group: 'line',
        label: 'В очереди',
        hint: 'Ждут ответа',
        read: (now) => ({
            value: formatInt(now.queue),
            // Очередь оцениваем: пусто — хорошо; есть очередь и никто не свободен — тревога.
            tone: (Number(now.queue) || 0) === 0
                ? 'good'
                : (Number(now.operators_free) === 0 ? 'bad' : 'warn'),
        }),
    },
    {
        key: 'queue_max_wait',
        group: 'line',
        label: 'Ждут дольше всех',
        hint: 'Самый долгий звонок в очереди',
        read: (now) => ({ value: formatDuration(now.queue_max_wait_seconds) }),
    },
    {
        key: 'talking_calls',
        group: 'line',
        label: 'Разговоров',
        hint: 'Идут прямо сейчас',
        read: (now) => ({ value: formatInt(now.talking_calls) }),
    },

    {
        key: 'operators_online',
        group: 'people',
        label: 'Онлайн',
        hint: 'Сотрудников',
        tone: 'info',
        read: (now) => ({ value: formatInt(now.operators_online) }),
    },
    {
        key: 'operators_free',
        group: 'people',
        label: 'Свободны',
        hint: 'Готовы принять звонок',
        read: (now) => ({ value: formatInt(now.operators_free) }),
    },
    {
        key: 'operators_talking',
        group: 'people',
        label: 'В разговоре',
        hint: 'Сотрудников',
        read: (now) => ({ value: formatInt(now.operators_talking) }),
    },
    {
        key: 'operators_on_break',
        group: 'people',
        label: 'Перерыв',
        hint: 'Сотрудников',
        tone: 'warn',
        read: (now) => ({ value: formatInt(now.operators_on_break) }),
    },
    {
        key: 'operators_on_recall',
        group: 'people',
        label: 'Перезвон',
        hint: 'Сотрудников',
        read: (now) => ({ value: formatInt(now.operators_on_recall) }),
    },
    {
        key: 'operators_on_training',
        group: 'people',
        label: 'Тренинг',
        hint: 'Сотрудников',
        tone: 'good',
        read: (now) => ({ value: formatInt(now.operators_on_training) }),
    },
    {
        key: 'operators_on_tech',
        group: 'people',
        label: 'Тех.причина',
        hint: 'Сотрудников',
        tone: 'violet',
        read: (now) => ({ value: formatInt(now.operators_on_tech) }),
    },
    {
        key: 'operators_other',
        group: 'people',
        label: 'Прочие статусы',
        hint: 'Резерв, нет на месте',
        read: (now) => ({ value: formatInt(now.operators_other) }),
    },

    /*
     * «Принято / входящих» одной плиткой: принятые — главное число, входящие приглушены.
     * Входящими считаем только дошедших до очереди — сбросившие трубку на приветствии до
     * оператора не доходили, и записывать их во входящие некорректно. За счёт этого разрыв
     * между числами равен ровно потерянным, а плитка AR показывает, допустим ли он.
     */
    {
        key: 'served_pair',
        group: 'today',
        kind: 'pair',
        label: 'Принято / входящих',
        hint: 'Дошедших до очереди',
        read: (now, today) => ({
            value: formatInt(today.served),
            secondary: formatInt(today.arrived),
        }),
    },
    {
        key: 'served',
        group: 'today',
        label: 'Принято',
        hint: 'Отвеченных звонков',
        read: (now, today) => ({ value: formatInt(today.served) }),
    },
    {
        key: 'arrived',
        group: 'today',
        label: 'Входящих',
        hint: 'Дошедших до очереди',
        read: (now, today) => ({ value: formatInt(today.arrived) }),
    },
    {
        key: 'lost',
        group: 'today',
        label: 'Потеряно',
        hint: 'Ушли, не дождавшись',
        tone: 'bad',
        read: (now, today) => ({ value: formatInt(today.lost) }),
    },
    {
        key: 'greet_drop',
        group: 'today',
        label: 'Сброс на приветствии',
        hint: 'До очереди не дошли',
        read: (now, today) => ({ value: formatInt(today.greet_drop) }),
    },
    {
        key: 'total',
        group: 'today',
        label: 'Всего звонков',
        hint: 'Включая сброшенных на приветствии',
        read: (now, today) => ({ value: formatInt(today.total) }),
    },
    {
        key: 'ar_ratio',
        group: 'today',
        label: 'AR',
        hint: `Норма ${AR_MIN_PERCENT}–${AR_MAX_PERCENT}%`,
        read: (now, today) => ({ value: formatPercent(today.ar_ratio), tone: arTone(today.ar_ratio) }),
    },
    {
        key: 'sl_ratio',
        group: 'today',
        label: 'SL',
        hint: `Норма от ${Math.round(SL_GOOD_RATIO * 100)}%`,
        read: (now, today) => ({ value: formatPercent(today.sl_ratio), tone: slTone(today.sl_ratio) }),
    },
    {
        key: 'avg_wait_seconds',
        group: 'today',
        label: 'Ср. ожидание',
        hint: 'На всех попавших в очередь',
        read: (now, today) => ({ value: formatDuration(today.avg_wait_seconds) }),
    },
    {
        key: 'max_wait_seconds',
        group: 'today',
        label: 'Макс. ожидание',
        hint: 'Самое долгое за день',
        read: (now, today) => ({ value: formatDuration(today.max_wait_seconds) }),
    },
    {
        key: 'avg_talk_seconds',
        group: 'today',
        label: 'Ср. разговор',
        hint: 'На принятый звонок',
        read: (now, today) => ({ value: formatDuration(today.avg_talk_seconds) }),
    },

    {
        key: 'break_list',
        group: 'lists',
        kind: 'list',
        label: 'Кто на перерыве',
        hint: 'Имя и время в статусе',
        icon: 'fa-list-ul',
        read: (now) => ({ items: now.break_list }),
    },
    {
        key: 'recall_list',
        group: 'lists',
        kind: 'list',
        label: 'Кто на перезвоне',
        hint: 'Имя и время в статусе',
        icon: 'fa-phone-volume',
        read: (now) => ({ items: now.recall_list }),
    },
];

export const WALLBOARD_METRIC_MAP = WALLBOARD_METRICS.reduce((acc, metric) => {
    acc[metric.key] = metric;
    return acc;
}, {});

/*
 * По умолчанию в виджете — то же, что крупными плитками на стене, плюс SL и «свободны»:
 * этого набора хватает, чтобы понять, всё ли в порядке, не открывая раздел. Остальное
 * пользователь добавляет сам — виджет для того и нужен, чтобы каждый смотрел своё.
 */
export const DEFAULT_WIDGET_METRICS = [
    'queue',
    'ar_ratio',
    'sl_ratio',
    'operators_online',
    'operators_free',
    'operators_on_break',
];

/** Значение показателя из снимка: {value, secondary, tone, items}. */
export const readWallboardMetric = (metric, snapshot) => {
    const raw = metric?.read?.(snapshot?.now || {}, snapshot?.today || {}, snapshot) || {};
    return {
        value: raw.value === undefined || raw.value === null ? '—' : raw.value,
        secondary: raw.secondary === undefined ? null : raw.secondary,
        tone: raw.tone || metric?.tone || 'neutral',
        items: Array.isArray(raw.items) ? raw.items : null,
    };
};

/** Отфильтровать сохранённый набор: ключи, которых больше нет в каталоге, молча выбрасываем. */
export const sanitizeWidgetMetrics = (keys) => {
    const list = Array.isArray(keys) ? keys : [];
    const seen = new Set();
    return list.filter((key) => {
        if (!WALLBOARD_METRIC_MAP[key] || seen.has(key)) return false;
        seen.add(key);
        return true;
    });
};

/* ─────────────────────────── Единый снимок табло ───────────────────────────
 *
 * Раздел и виджет читают ОДИН снимок и делят ОДИН опрос: иначе, когда открыто и то и другое,
 * прокси Oktell дёргается вдвое чаще, а цифры в разделе и в виджете в один момент времени
 * могут расходиться. Опрос запускается с появлением первого подписчика и останавливается,
 * когда закрыли и раздел, и виджет; последний снимок остаётся в памяти, чтобы при возврате
 * экран не мигал пустотой.
 *
 * Направлений табло два («Основа» и «Чат»), у каждого свой источник и свой темп опроса,
 * поэтому опрос собран фабрикой: одна и та же механика (общий снимок на всех подписчиков,
 * пауза на скрытой вкладке, догрузка при возврате в окно) заводится дважды с разными
 * адресами. Копировать её второй раз нельзя — разойдётся ровно там, где чинили один раз.
 */
const isTabVisible = () => {
    if (typeof document === 'undefined') return true;
    return typeof document.visibilityState === 'string'
        ? document.visibilityState === 'visible'
        : !document.hidden;
};

function createSnapshotFeed({ path, pollIntervalMs }) {
    const store = {
        state: { snapshot: null, error: null, loading: true },
        listeners: new Set(),
        source: { apiBaseUrl: '', buildHeaders: null },
        timer: null,
        controller: null,
        inFlight: false,
        lastFetchAt: 0,
        subscribers: 0,
    };

    const getStoreState = () => store.state;

    const patchState = (next) => {
        store.state = { ...store.state, ...next };
        store.listeners.forEach((listener) => listener());
    };

    async function fetchSnapshot({ silent = false } = {}) {
        // Защита от наложения запросов: снимок просят и таймер, и фокус окна, и кнопка «Обновить».
        if (store.inFlight) return;
        const { apiBaseUrl, buildHeaders } = store.source;
        if (!apiBaseUrl) return;
        store.inFlight = true;
        const controller = typeof AbortController === 'function' ? new AbortController() : null;
        store.controller = controller;
        if (!silent) patchState({ loading: true });
        try {
            const response = await fetch(`${apiBaseUrl}${path}`, {
                headers: buildHeaders ? buildHeaders({ Accept: 'application/json' }) : { Accept: 'application/json' },
                credentials: 'include',
                signal: controller?.signal,
            });
            if (!response.ok) {
                let detail = '';
                try {
                    const body = await response.json();
                    detail = body?.error || body?.detail || '';
                } catch (parseError) {
                    detail = '';
                }
                throw new Error(detail || `Сервер ответил ${response.status}`);
            }
            const data = await response.json();
            store.lastFetchAt = Date.now();
            patchState({ snapshot: data, error: null });
        } catch (requestError) {
            if (requestError?.name === 'AbortError') return;
            // Пока есть последний снимок — экран на стене не гасим, просто помечаем расхождение.
            patchState({ error: requestError?.message || 'Не удалось получить данные' });
        } finally {
            store.inFlight = false;
            store.controller = null;
            if (store.state.loading) patchState({ loading: false });
        }
    }

    const handleWake = () => {
        if (!isTabVisible()) return;
        if (Date.now() - store.lastFetchAt < WAKE_REFRESH_MIN_AGE_MS) return;
        fetchSnapshot({ silent: Boolean(store.state.snapshot) });
    };

    function startPolling() {
        if (typeof window === 'undefined') return;
        fetchSnapshot({ silent: Boolean(store.state.snapshot) });
        store.timer = window.setInterval(() => {
            // Скрытую вкладку не опрашиваем: незачем дёргать источник впустую.
            if (isTabVisible()) fetchSnapshot({ silent: true });
        }, pollIntervalMs);
        document.addEventListener('visibilitychange', handleWake);
        window.addEventListener('focus', handleWake);
    }

    function stopPolling() {
        if (typeof window === 'undefined') return;
        if (store.timer) window.clearInterval(store.timer);
        store.timer = null;
        document.removeEventListener('visibilitychange', handleWake);
        window.removeEventListener('focus', handleWake);
        store.controller?.abort?.();
    }

    const subscribeSnapshot = (listener) => {
        store.listeners.add(listener);
        store.subscribers += 1;
        if (store.subscribers === 1) startPolling();
        return () => {
            store.listeners.delete(listener);
            store.subscribers = Math.max(0, store.subscribers - 1);
            if (store.subscribers === 0) stopPolling();
        };
    };

    /**
     * Снимок: {snapshot, error, loading, refresh}.
     *
     * Источник (адрес API и сборщик заголовков) обновляем в рендере, а не в эффекте: первый
     * запрос уходит из subscribe, то есть ДО эффектов, и без адреса он бы просто не состоялся.
     */
    return function useSnapshot({ apiBaseUrl, withAccessTokenHeader }) {
        store.source = { apiBaseUrl, buildHeaders: withAccessTokenHeader };
        const state = useSyncExternalStore(subscribeSnapshot, getStoreState, getStoreState);
        const refresh = useCallback(() => { fetchSnapshot({ silent: true }); }, []);
        return { snapshot: state.snapshot, error: state.error, loading: state.loading, refresh };
    };
}

export const useSzovWallboardSnapshot = createSnapshotFeed({
    path: '/api/szov_wallboard/snapshot',
    pollIntervalMs: POLL_INTERVAL_MS,
});

export const useSzovChatWallboardSnapshot = createSnapshotFeed({
    path: '/api/szov_wallboard/chat_snapshot',
    pollIntervalMs: CHAT_POLL_INTERVAL_MS,
});

/** Текст «данные замерли»: ошибка запроса или устаревший снимок из кэша сервера. */
export const wallboardStaleNotice = (snapshot, error, source = 'Oktell') => {
    if (error) return error;
    if (snapshot?.stale) {
        const age = Number(snapshot.age_seconds);
        return `${source} не отвечает, данные ${Number.isFinite(age) ? `${formatDuration(age)} назад` : 'устарели'}`;
    }
    return null;
};
