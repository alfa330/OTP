import {
    arTone,
    createSnapshotFeed,
    formatClock,
    formatDuration,
    formatInt,
    formatPercent,
    slTone,
} from './szovWallboardShared';

/*
 * Общая начинка «Табло ОП» — отдел продаж на телефонии FreePBX.
 *
 * Аналог табло СЗоВ по «Линии», но источник другой, и это видно в составе экрана:
 *   - цифры дня (входящие, принято, потеряно, AR, SL, разговор) приезжают из касаний,
 *     которые мост внутри корпоративной сети досылает каждые двадцать секунд;
 *   - люди и их статусы — из событий iCORE Phone, как на табло Тез КЦ;
 *   - «в очереди сейчас» здесь НЕТ: CDR узнаёт о звонке после его завершения, а живые
 *     ручки станции роняли её в августе 2026. Плитка появится вместе с доступом к AMI.
 *
 * Механика опроса — из szovWallboardShared импортом (`createSnapshotFeed`): таймер и
 * запрос на весь фронт живут в одном месте. Пороги AR/SL те же, что у СЗоВ (arTone/slTone),
 * и приезжают в снимке: если владелец поменяет коридор на сервере, экран перекрасится сам.
 */

// Серверный TTL снимка — 10 с, мост досылает раз в 20 с: чаще опрашивать бессмысленно.
export const OP_POLL_INTERVAL_MS = 10000;

export const OP_WALLBOARD_SNAPSHOT_PATH = '/api/op_wallboard/snapshot';

export const useOpWallboardSnapshot = createSnapshotFeed({
    path: OP_WALLBOARD_SNAPSHOT_PATH,
    pollIntervalMs: OP_POLL_INTERVAL_MS,
});

/*
 * Ноль вместо «нет данных» на стене читается как «всё хорошо, звонков нет». Показателю,
 * который не из чего посчитать (AR при нуле входящих), положен прочерк.
 */
const isBlank = (value) => value === null || value === undefined;
export const formatCount = (value) => (isBlank(value) ? '—' : formatInt(value));
export const formatSeconds = (value) => (isBlank(value) ? '—' : formatDuration(value));
export const formatRatio = (value) => (isBlank(value) ? '—' : formatPercent(value));

/*
 * Коридор AR приходит в снимке (ar_min_percent / ar_max_percent). arTone СЗоВ зашит на 3–5 %;
 * здесь то же правило, но с порогами из данных — чтобы серверная настройка была законом.
 */
export const opArTone = (ratio, snapshot) => {
    if (isBlank(ratio) || !Number.isFinite(Number(ratio))) return 'neutral';
    const min = Number(snapshot?.ar_min_percent);
    const max = Number(snapshot?.ar_max_percent);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return arTone(ratio);
    const percent = Number(ratio) * 100;
    // Сравнение с допуском: 0.05 * 100 в двоичной арифметике — это 5.000000000000001.
    if (percent < min - 1e-9 || percent > max + 1e-9) return 'bad';
    return 'good';
};

/*
 * Статусы людей — те же ключи и цвета, что на табло Тез КЦ (там они же приходят из iCORE
 * Phone). Один смысл на всех экранах горит одинаково. «Нет событий» — не состояние
 * человека, а отсутствие данных о нём; «Нет в составе» — звонил, но в отделе не числится.
 */
export const OP_STATUS_STYLE = {
    talking: { label: 'В разговоре', chip: 'bg-blue-100 text-blue-700' },
    free: { label: 'Активный', chip: 'bg-green-100 text-green-700' },
    outgoing: { label: 'Исход', chip: 'bg-amber-100 text-amber-700' },
    training: { label: 'Тренинг', chip: 'bg-purple-100 text-purple-700' },
    tech: { label: 'Техническая пауза', chip: 'bg-fuchsia-100 text-fuchsia-700' },
    break: { label: 'Перерыв', chip: 'bg-orange-100 text-orange-700' },
    offline: { label: 'Не в сети', chip: 'bg-slate-100 text-slate-500' },
    unknown: { label: 'Нет событий', chip: 'bg-slate-50 text-slate-400 ring-1 ring-slate-200' },
    other: { label: null, chip: 'bg-slate-100 text-slate-600' },
};

export const opStatusChip = (row) => {
    const style = OP_STATUS_STYLE[row?.status_key] || OP_STATUS_STYLE.other;
    return {
        label: row?.status_label || style.label || '—',
        className: style.chip,
        muted: row?.status_key === 'unknown' || row?.in_roster === false,
    };
};

/*
 * SL, среднее ожидание и разговор считаются по моменту, когда трубку снял сотрудник. В CDR
 * станции этого момента с 09.09.2026 нет (очередь «отвечает» звонок сама в секунду входа),
 * поэтому сервер берёт его из событий iCORE Phone: «занят» в секунду ответа, «готов» —
 * в секунду отбоя. `wait_measured` в итогах — у скольких принятых пара нашлась; если ни у
 * одного (телефоны молчат), снимок отдаёт SL и ожидание как null, и экран объясняет прочерк
 * одной строкой, а не молчит и не рисует 0 %.
 */
export const ANSWER_MOMENT_MISSING = 'телефоны iCORE Phone не прислали ни одного момента ответа';

export const opAnswerMomentMissing = (snapshot) => (
    (snapshot?.totals?.answered || 0) > 0 && !(snapshot?.totals?.wait_measured || 0)
);

export const opDataGapNotice = (snapshot) => (
    opAnswerMomentMissing(snapshot) ? `SL и среднее ожидание не считаются: ${ANSWER_MOMENT_MISSING}` : null
);

/** «по 120 из 130 принятых» — когда момент ответа известен не у всех; иначе пусто. */
export const opMeasuredNote = (snapshot) => {
    const answered = Number(snapshot?.totals?.answered || 0);
    const measured = Number(snapshot?.totals?.wait_measured || 0);
    if (!measured || measured >= answered) return '';
    return ` · момент ответа известен у ${formatInt(measured)} из ${formatInt(answered)} принятых`;
};

/*
 * Каталог показателей: единственное место, где сказано «как называется, откуда берётся,
 * каким цветом». Экран берёт отсюда подпись, значение и тон; виджет «поверх окон» — тот же
 * каталог через реестр направления ниже (OP_WALLBOARD_DIRECTIONS).
 *
 * `read` получает снимок целиком, а не пару (now, today), как у СЗоВ: у табло ОП итоги дня
 * лежат в `totals`, и виджету об этом знать незачем — он читает через `readMetric` из реестра.
 */
export const OP_METRICS = [
    {
        key: 'op_arrived', group: 'day', label: 'Входящих',
        hint: 'Дошли до линии за сегодня',
        read: (s) => ({ value: formatCount(s.totals?.arrived) }),
    },
    {
        key: 'op_answered', group: 'day', label: 'Принято',
        hint: 'Входящих с разговором',
        read: (s) => ({ value: formatCount(s.totals?.answered) }),
    },
    {
        // Потерян — дошёл до линии, а до разговора с сотрудником не дошло: сброс в очереди,
        // занято, нет ответа. Очередь станции сама «отвечает» звонок (в CDR он ANSWERED),
        // поэтому в отчёте FreePBX такие звонки пропущенными не считаются — а здесь считаются.
        key: 'op_missed', group: 'day', label: 'Потеряно',
        hint: 'Дошли до линии, разговора с сотрудником не было',
        read: (s) => ({
            value: formatCount(s.totals?.missed),
            tone: (s.totals?.missed || 0) > 0 ? 'warn' : 'neutral',
        }),
    },
    {
        key: 'op_ar', group: 'day', label: 'AR',
        hint: 'Потеряно / входящих',
        read: (s) => ({ value: formatRatio(s.totals?.ar), tone: opArTone(s.totals?.ar, s) }),
    },
    {
        key: 'op_sl', group: 'day', label: 'SL',
        hint: (s) => (opAnswerMomentMissing(s)
            ? ANSWER_MOMENT_MISSING
            : `Отвечено не позже ${s?.sl_threshold_seconds ?? 20} с после входа в очередь${opMeasuredNote(s)}`),
        read: (s) => ({ value: formatRatio(s.totals?.sl), tone: slTone(s.totals?.sl) }),
    },
    {
        key: 'op_avg_talk', group: 'day', label: 'Средний разговор',
        hint: 'От ответа до отбоя по телефону, усечённое',
        read: (s) => ({ value: formatSeconds(s.totals?.avg_talk_seconds) }),
    },
    {
        key: 'op_avg_wait', group: 'day', label: 'Среднее ожидание',
        hint: (s) => (opAnswerMomentMissing(s)
            ? ANSWER_MOMENT_MISSING
            : `От входа в очередь (после автоинформатора) до ответа сотрудника${opMeasuredNote(s)}`),
        read: (s) => ({ value: formatSeconds(s.totals?.avg_wait_seconds) }),
    },
    {
        key: 'op_outgoing', group: 'day', label: 'Исходящих',
        hint: 'Набрали → дозвонились',
        read: (s) => ({
            value: formatCount(s.totals?.outgoing),
            secondary: isBlank(s.totals?.outgoing_answered) ? null : formatInt(s.totals.outgoing_answered),
        }),
    },
    {
        key: 'op_online', group: 'now', label: 'Онлайн',
        hint: 'Свободны и в разговоре',
        read: (s) => ({ value: formatCount(s.now?.operators_online), tone: 'info' }),
    },
    {
        key: 'op_free', group: 'now', label: 'Свободны',
        hint: 'Готовы принять звонок',
        read: (s) => ({ value: formatCount(s.now?.operators_free) }),
    },
    {
        key: 'op_talking', group: 'now', label: 'В разговоре',
        hint: 'Сотрудников',
        read: (s) => ({ value: formatCount(s.now?.operators_talking) }),
    },
    {
        key: 'op_break', group: 'now', label: 'На перерыве',
        hint: 'Перерыв, тренинг, тех.причина',
        read: (s) => ({
            value: formatCount(s.now?.operators_on_break),
            tone: (s.now?.operators_on_break || 0) > 0 ? 'warn' : 'neutral',
        }),
    },
];

export const OP_METRIC_MAP = OP_METRICS.reduce((acc, metric) => {
    acc[metric.key] = metric;
    return acc;
}, {});

/** Значение плитки по каталогу: подпись, число, тон. */
export const readOpMetric = (metric, snapshot) => {
    if (!metric || !snapshot) return { value: '—', tone: 'neutral', secondary: null };
    const out = metric.read(snapshot) || {};
    return { value: out.value ?? '—', tone: out.tone || metric.tone || 'neutral', secondary: out.secondary ?? null };
};

export const opMetricHint = (metric, snapshot) => (
    typeof metric.hint === 'function' ? metric.hint(snapshot) : metric.hint
);

/*
 * Свежесть. Снимок считается порталом из своей базы, а базу кормит мост: если мост замолчал,
 * цифры на стене замирают, хотя сам снимок «свежий». Поэтому возраст показываем по последнему
 * живому приращению моста (bridge.live_age_seconds), а не по generated_at снимка.
 */
export const OP_LIVE_WARN_SECONDS = 120;

export const opFreshnessNotice = (snapshot) => {
    const age = snapshot?.bridge?.live_age_seconds;
    if (isBlank(age)) {
        return snapshot?.bridge?.connected
            ? 'Мост на связи, но живых данных за сегодня ещё не присылал'
            : 'Мост не выходил на связь — цифры могут быть неполными';
    }
    if (age > OP_LIVE_WARN_SECONDS) {
        return `Данные моста устарели: последнее обновление ${formatDuration(age)} назад`;
    }
    return null;
};

/** Отметка времени для шапки: по последнему живому приращению моста, а не по снимку. */
export const opClockLabel = (snapshot) => {
    const at = snapshot?.bridge?.live_at || snapshot?.captured_at;
    return at ? `данные на ${formatClock(at)}` : null;
};

/*
 * Реестр направления для виджета «поверх окон» — той же формы, что WALLBOARD_DIRECTIONS СЗоВ и
 * TEZ_WALLBOARD_DIRECTIONS, и по той же причине свой, а не запись в чужом словаре: словарь СЗоВ
 * напрямую питает переключатель направлений в его разделе, и «ОП» там был бы лишним пунктом.
 *
 * Три поля сверх общей формы — потому что источник другой:
 *   - readMetric: показатели ОП читают снимок целиком (см. OP_METRICS), общий
 *     readWallboardMetric отдал бы им `now` вместо снимка и прочерки во всех плитках;
 *   - clockLabel: часы берутся из bridge.live_at, а не из поля вида `oktell_now`;
 *   - freshnessNotice: «мост замолчал» — предупреждение этого табло, у снимка Oktell его нет.
 */
export const OP_METRIC_GROUPS = [
    { key: 'now', title: 'Сейчас' },
    { key: 'day', title: 'За день' },
];

// По умолчанию — то же, что крупными плитками на стене: кто на линии и сколько потеряли.
export const DEFAULT_OP_WIDGET_METRICS = [
    'op_online',
    'op_talking',
    'op_free',
    'op_arrived',
    'op_missed',
    'op_ar',
];

export const OP_WALLBOARD_DIRECTIONS = {
    op: {
        key: 'op',
        label: 'ОП',
        hint: 'Отдел продаж: FreePBX',
        title: 'Табло ОП',
        source: 'мост «Касаний»',
        clockField: 'captured_at',
        icon: 'fa-tachometer-alt',
        useSnapshot: useOpWallboardSnapshot,
        metrics: OP_METRICS,
        metricMap: OP_METRIC_MAP,
        metricGroups: OP_METRIC_GROUPS,
        defaultMetrics: DEFAULT_OP_WIDGET_METRICS,
        readMetric: readOpMetric,
        clockLabel: (snapshot) => {
            const clock = opClockLabel(snapshot);
            return clock ? `Мост «Касаний», ${clock}` : null;
        },
        freshnessNotice: opFreshnessNotice,
    },
};

export const opWallboardDirection = (key) => OP_WALLBOARD_DIRECTIONS[key] || OP_WALLBOARD_DIRECTIONS.op;

// Журнал статусов сотрудника (ТЗ #339): боковая панель, OpStatusJournal.jsx.
export const OP_JOURNAL_PATH = '/api/op_wallboard/journal';
