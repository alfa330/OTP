import {
    STATUS_STYLE,
    createSnapshotFeed,
    formatDuration,
    formatInt,
    formatPercent,
    slTone,
} from './szovWallboardShared';

/*
 * Общая начинка «Табло Тез КЦ»: опрос, пороги и каталоги показателей двух направлений —
 * ТП (техподдержка, у неё есть очередь) и ОП (отдел продаж, очереди нет).
 *
 * Почему отдельный файл, а не строки в szovWallboardShared:
 *   - механику опроса берём оттуда импортом (`createSnapshotFeed`), поэтому таймер и запрос
 *     на весь фронт остаются в одном месте — две копии разойдутся ровно там, где чинили одну;
 *   - а вот пороги и каталоги у Тез свои, и правка «на месте» перекрасила бы табло СЗоВ.
 *
 * Источник у обоих направлений один — кабинет my.binotel.kz, поэтому и снимок сервер собирает
 * одним обходом; здесь это видно только в том, что оба фида ходят с одинаковым шагом.
 */

/*
 * Шаг опроса под серверным TTL (20 с). Сам кабинет свою страницу очереди обновляет раз в 15 с,
 * а виджет «Прямо сейчас» дёргает каждые 7,5 с — то есть табло грузит Binotel меньше, чем одна
 * открытая вкладка кабинета, сколько бы зрителей ни смотрело: снимок общий на всех.
 */
export const TEZ_POLL_INTERVAL_MS = 20000;

/*
 * AR у Тез — ПОТОЛОК, а не коридор: норма «не выше 5 %», и один процент здесь так же хорош,
 * как ноль. Переиспользовать arTone СЗоВ нельзя — там норма 3…5 %, и честный 1 % на табло Тез
 * горел бы красным. Жёлтая полоса между 5 и 7 % — «уже не норма, но ещё не авария»: без неё
 * табло прыгает из зелёного в красный на десятых долях процента.
 */
export const TEZ_AR_TARGET_PERCENT = 5;
export const TEZ_AR_BAD_PERCENT = 7;

export const arCeilingTone = (ratio) => {
    if (ratio === null || ratio === undefined || !Number.isFinite(Number(ratio))) return 'neutral';
    // Сравниваем доли, а не проценты: `0.07 * 100` в двоичной арифметике даёт 7.000000000000001,
    // и ровно семь процентов молча уехали бы из жёлтого в красный.
    const value = Number(ratio);
    if (value <= TEZ_AR_TARGET_PERCENT / 100) return 'good';
    if (value <= TEZ_AR_BAD_PERCENT / 100) return 'warn';
    return 'bad';
};

/*
 * Порог SL приходит в снимке: кабинет пишет его прямо в подписи колонки («SLA: 20 секунд»),
 * и сервер забирает число оттуда, чтобы табло не разъехалось с кабинетом, если порог поменяют.
 * Здесь — только запасное значение для подписи, пока снимка ещё нет.
 */
export const TEZ_SL_THRESHOLD_SECONDS = 20;

/*
 * Ноль вместо «нет данных» — самая опасная ложь на стене: пустое ожидание читается как «всё
 * хорошо, звонков нет». А `Number(null)` — это ноль, поэтому formatInt на null отвечает «0», а
 * formatDuration — «0:00». Показателю, который не из чего посчитать, положен прочерк, и эти две
 * обёртки — единственная дверь к числам снимка на обоих табло Тез.
 */
const isBlank = (value) => value === null || value === undefined;

export const formatCount = (value) => (isBlank(value) ? '—' : formatInt(value));

export const formatSeconds = (value) => (isBlank(value) ? '—' : formatDuration(value));

/*
 * Чип у строки списка. Обычный перерыв не помечаем — весь список и так о перерывах, а вот всё
 * остальное обязано отличаться на вид: без чипа занятый работой в CRM выглядел бы на стене
 * ровно как ушедший на перерыв. Незнакомую причину показываем нейтральным серым: смысла её
 * цвета мы не знаем, но прятать её нельзя.
 */
export const tezBreakChip = (item) => {
    if (!item || item.reason_key === 'break' || !item.reason) return null;
    const style = STATUS_STYLE[item.reason_key];
    return { label: item.reason, className: style ? style.chip : 'bg-slate-100 text-slate-500' };
};

/*
 * Статусы в списке операторов. Источник у них НЕ тот же, что у плиток: плитки считаются из
 * кабинета Binotel, а строки людей — из событий iCORE Phone, которые телефон шлёт нам сам.
 * У кабинета градаций всего четыре, «Тренинг» и «Техническая пауза» схлопнуты в «Перерыв», а
 * отдел продаж статусы там не переключает вовсе — поэтому на вопрос «кто чем занят» отвечает
 * телефон, а не кабинет.
 *
 * Подписи — словами самой пилюли телефона («Активный», «Исход»), чтобы оператор узнавал на
 * стене своё состояние; они приходят в снимке, здесь — запасные для ключа без подписи.
 *
 * Цвета: зелёный/синий/янтарный — работа в трёх её видах, оранжевый — перерыв (та же
 * оранжевая, что у перерывов на табло СЗоВ: один смысл на всех экранах горит одинаково),
 * фиолетовый и фуксия — учёба и техпауза, серый — отсутствие. «Нет событий» отличается от
 * «Не в сети» намеренно: первое — мы не знаем, второе — знаем, что человек вышел.
 */
export const TEZ_STATUS_STYLE = {
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

/** Чип статуса строки: подпись из снимка, оформление — по ключу. */
export const tezStatusChip = (row) => {
    const style = TEZ_STATUS_STYLE[row?.status_key] || TEZ_STATUS_STYLE.other;
    return {
        label: row?.status_label || style.label || '—',
        className: style.chip,
        // «Нет событий» — не состояние человека, а отсутствие данных о нём: такую строку
        // приглушаем целиком, иначе она читается как полноценный статус.
        muted: row?.status_key === 'unknown',
    };
};

/*
 * Пара «главное / приглушённое» в ячейке: набрано → дозвонились. Прочерк ставится по правилу
 * нуля — счётчиков у человека нет вовсе, когда его номер не нашёлся в ответе кабинета.
 */
export const formatPair = (main, secondary) => ({
    value: formatCount(main),
    secondary: isBlank(secondary) ? null : formatInt(secondary),
});

export const useTezTpWallboardSnapshot = createSnapshotFeed({
    path: '/api/tez_wallboard/tp_snapshot',
    pollIntervalMs: TEZ_POLL_INTERVAL_MS,
});

export const useTezOpWallboardSnapshot = createSnapshotFeed({
    path: '/api/tez_wallboard/op_snapshot',
    pollIntervalMs: TEZ_POLL_INTERVAL_MS,
});

/*
 * Каталоги показателей: единственное место, где сказано «как называется, как считается, каким
 * цветом». Экран берёт отсюда подпись, цифру и тон — поэтому одна и та же величина не может
 * разойтись между двумя режимами табло, а в каталоге есть и то, чего на стене нет (место на
 * ней ограничено, а показатель уже посчитан и приедет в виджет, когда он появится).
 *
 * Ключи с префиксом направления (tp_… и op_…) — не украшение: «Онлайн» есть и у ТП, и у ОП,
 * и в общем пространстве ключей второй такой ключ молча затёр бы первый.
 *
 * kind: 'tile' — плитка с числом, 'pair' — «главное / приглушённое», 'list' — перечень людей.
 */
export const TEZ_TP_METRIC_GROUPS = [
    { key: 'tp_now', title: 'Линия сейчас' },
    { key: 'tp_people', title: 'Операторы сейчас' },
    { key: 'tp_today', title: 'За день' },
    { key: 'tp_lists', title: 'Списки' },
];

export const TEZ_TP_METRICS = [
    {
        key: 'tp_queue',
        group: 'tp_now',
        label: 'В очереди',
        hint: 'Ждут ответа',
        read: (now) => ({
            value: formatCount(now.queue),
            // Очередь оцениваем: пусто — хорошо; есть очередь и никто не свободен — тревога.
            // Пустое значение не красим вовсе: зелёная плитка с прочерком обещала бы порядок,
            // которого мы не знаем.
            tone: isBlank(now.queue)
                ? 'neutral'
                : ((Number(now.queue) || 0) === 0
                    ? 'good'
                    : (Number(now.operators_free) === 0 ? 'bad' : 'warn')),
        }),
    },
    {
        key: 'tp_queue_max_wait',
        group: 'tp_now',
        label: 'Ждут дольше всех',
        hint: 'Самый долгий звонок в очереди',
        // На стене плитки нет: разметку строки ждущего клиента подтвердить не на чем — за всю
        // разведку очередь ни разу не была занята. Пока сервер честно отдаёт null, и вечный
        // прочерк на стене был бы шумом; появятся данные — плитку останется поставить.
        read: (now) => ({ value: formatSeconds(now.queue_max_wait_seconds) }),
    },
    {
        key: 'tp_online',
        group: 'tp_people',
        label: 'Онлайн',
        hint: 'Свободны и в разговоре',
        tone: 'info',
        read: (now) => ({ value: formatCount(now.operators_online) }),
    },
    {
        key: 'tp_free',
        group: 'tp_people',
        label: 'Свободны',
        hint: 'Готовы принять звонок',
        read: (now) => ({ value: formatCount(now.operators_free) }),
    },
    {
        key: 'tp_talking',
        group: 'tp_people',
        label: 'В разговоре',
        hint: 'Сотрудников',
        read: (now) => ({ value: formatCount(now.operators_talking) }),
    },
    {
        key: 'tp_break',
        group: 'tp_people',
        label: 'Перерыв',
        hint: 'Сотрудников',
        tone: 'warn',
        read: (now) => ({ value: formatCount(now.operators_on_break) }),
    },
    {
        key: 'tp_other',
        group: 'tp_people',
        label: 'Прочие статусы',
        hint: 'Работа в CRM, не на линии',
        read: (now) => ({ value: formatCount(now.operators_other) }),
    },
    {
        key: 'tp_total',
        group: 'tp_people',
        label: 'Всего сотрудников',
        hint: 'Обслуживают очередь',
        read: (now) => ({ value: formatCount(now.operators_total) }),
    },

    /*
     * «Принято / входящих» одной плиткой: принятые — главное число, входящие приглушены.
     * Разрыв между числами равен ровно потерянным, а плитка AR говорит, допустим ли он.
     */
    {
        key: 'tp_served_pair',
        group: 'tp_today',
        kind: 'pair',
        label: 'Принято / входящих',
        hint: 'Дошедших до очереди',
        read: (now, today) => ({
            value: formatCount(today.served),
            secondary: formatCount(today.arrived),
        }),
    },
    {
        key: 'tp_served',
        group: 'tp_today',
        label: 'Принято',
        hint: 'Отвеченных звонков',
        read: (now, today) => ({ value: formatCount(today.served) }),
    },
    {
        key: 'tp_arrived',
        group: 'tp_today',
        label: 'Входящих',
        hint: 'Дошедших до очереди',
        read: (now, today) => ({ value: formatCount(today.arrived) }),
    },
    {
        key: 'tp_lost',
        group: 'tp_today',
        label: 'Потеряно',
        hint: 'Ушли, не дождавшись',
        // Красим, только когда есть что терять: красный ноль — тревога на пустом месте,
        // а нейтральное состояние цвета не заслуживает.
        read: (now, today) => ({
            value: formatCount(today.lost),
            tone: (Number(today.lost) || 0) > 0 ? 'bad' : 'neutral',
        }),
    },
    {
        key: 'tp_ar',
        group: 'tp_today',
        label: 'AR',
        hint: `Норма до ${TEZ_AR_TARGET_PERCENT}%`,
        read: (now, today) => ({ value: formatPercent(today.ar_ratio), tone: arCeilingTone(today.ar_ratio) }),
    },
    {
        key: 'tp_sl',
        group: 'tp_today',
        label: 'SL',
        // У кабинета SL считается ОТ ПРИНЯТЫХ, а не от всех попавших в очередь, как у СЗоВ:
        // число берём готовым со страницы очереди и подписываем честно, иначе одинаково
        // названные плитки двух табло значили бы разное.
        hint: `Принятых за ${TEZ_SL_THRESHOLD_SECONDS} с`,
        read: (now, today) => ({ value: formatPercent(today.sl_ratio), tone: slTone(today.sl_ratio) }),
    },
    {
        key: 'tp_avg_wait',
        group: 'tp_today',
        label: 'Ср. ожидание',
        hint: 'До соединения с оператором',
        read: (now, today) => ({ value: formatSeconds(today.avg_wait_seconds) }),
    },
    {
        key: 'tp_avg_talk',
        group: 'tp_today',
        label: 'Ср. разговор',
        hint: 'На принятый звонок',
        read: (now, today) => ({ value: formatSeconds(today.avg_talk_seconds) }),
    },
    {
        key: 'tp_outgoing_pair',
        group: 'tp_today',
        kind: 'pair',
        label: 'Поднято / совершено',
        hint: 'Исходящие звонки',
        read: (now, today) => ({
            value: formatCount(today.outgoing_success),
            secondary: formatCount(today.outgoing_total),
        }),
    },

    {
        key: 'tp_break_list',
        group: 'tp_lists',
        kind: 'list',
        label: 'Кто на перерыве',
        hint: 'Имя и время в статусе',
        icon: 'fa-list-ul',
        read: (now) => ({ items: now.break_list }),
        chip: tezBreakChip,
    },
];

export const TEZ_TP_METRIC_MAP = TEZ_TP_METRICS.reduce((acc, metric) => {
    acc[metric.key] = metric;
    return acc;
}, {});

/*
 * У ОП нет ни очереди, ни, стало быть, SL и ожидания: показывать их прочерком — обещать
 * показатель, которого у направления не существует. Поэтому в каталоге ОП их нет вовсе.
 */
export const TEZ_OP_METRIC_GROUPS = [
    { key: 'op_now', title: 'Продавцы сейчас' },
    { key: 'op_today', title: 'За день' },
    { key: 'op_lists', title: 'Списки' },
];

export const TEZ_OP_METRICS = [
    {
        key: 'op_online',
        group: 'op_now',
        label: 'Онлайн',
        hint: 'Свободны и в разговоре',
        tone: 'info',
        read: (now) => ({ value: formatCount(now.operators_online) }),
    },
    {
        key: 'op_free',
        group: 'op_now',
        label: 'Свободны',
        hint: 'Готовы принять звонок',
        read: (now) => ({ value: formatCount(now.operators_free) }),
    },
    {
        key: 'op_talking',
        group: 'op_now',
        label: 'В разговоре',
        hint: 'Сотрудников',
        read: (now) => ({ value: formatCount(now.operators_talking) }),
    },
    {
        key: 'op_break',
        group: 'op_now',
        label: 'Перерыв',
        hint: 'Сотрудников',
        tone: 'warn',
        read: (now) => ({ value: formatCount(now.operators_on_break) }),
    },
    {
        key: 'op_other',
        group: 'op_now',
        label: 'Прочие статусы',
        hint: 'Работа в CRM, не на линии',
        read: (now) => ({ value: formatCount(now.operators_other) }),
    },
    {
        key: 'op_total',
        group: 'op_now',
        label: 'Всего сотрудников',
        hint: 'В направлении',
        read: (now) => ({ value: formatCount(now.operators_total) }),
    },

    /*
     * Главная пара направления: поднято — главное число, совершено приглушено. Именно так на
     * неё смотрит руководитель продаж — «дозвонились столько-то из стольких».
     */
    {
        key: 'op_outgoing_pair',
        group: 'op_today',
        kind: 'pair',
        label: 'Поднято / совершено',
        hint: 'Исходящие звонки',
        read: (now, today) => ({
            value: formatCount(today.outgoing_success),
            secondary: formatCount(today.outgoing_total),
        }),
    },
    {
        /*
         * Дозвон — качество обзвона, ровно то же по смыслу, что SL у линии: сколько трубок
         * подняли из набранного. Нормы владелец для него не задавал, поэтому не красим:
         * цвет без порога — это украшение, а не смысл.
         */
        key: 'op_dial_rate',
        group: 'op_today',
        label: 'Дозвон',
        hint: 'Подняли трубку из набранных',
        read: (now, today) => ({ value: formatPercent(today.outgoing_success_ratio) }),
    },
    {
        key: 'op_avg_talk',
        group: 'op_today',
        label: 'Ср. разговор',
        // У отдела продаж входящих нет вовсе, и «средняя длительность разговора» из
        // постановки может значить только исходящие. Подпись обязана это называть,
        // иначе плитку сравнят со средним разговором техподдержки.
        hint: 'На исходящий разговор',
        read: (now, today) => ({ value: formatSeconds(today.avg_talk_seconds) }),
    },

    {
        key: 'op_break_list',
        group: 'op_lists',
        kind: 'list',
        label: 'Кто на перерыве',
        hint: 'Имя и время в статусе',
        icon: 'fa-list-ul',
        read: (now) => ({ items: now.break_list }),
        chip: tezBreakChip,
    },
];

export const TEZ_OP_METRIC_MAP = TEZ_OP_METRICS.reduce((acc, metric) => {
    acc[metric.key] = metric;
    return acc;
}, {});

/*
 * Наборы по умолчанию — то же, что крупными плитками на стене: этого хватает, чтобы понять,
 * всё ли в порядке, не открывая раздел.
 */
export const DEFAULT_TEZ_TP_METRICS = [
    'tp_queue',
    'tp_ar',
    'tp_sl',
    'tp_online',
    'tp_free',
    'tp_break',
];

export const DEFAULT_TEZ_OP_METRICS = [
    'op_online',
    'op_talking',
    'op_break',
    'op_outgoing_pair',
    'op_dial_rate',
    'op_avg_talk',
];

/*
 * Реестр направлений Тез — СВОЙ, отдельный от WALLBOARD_DIRECTIONS СЗоВ. Тот словарь напрямую
 * питает сегментный переключатель чужого раздела: допиши мы туда ТП и ОП, супервайзер СЗоВ
 * увидел бы в своей шапке два лишних направления, а в его localStorage мог бы залечь `tez_op`.
 * Форма записи та же, чтобы направления Тез подхватились виджетом, когда он научится жить
 * больше чем в одном экземпляре.
 */
export const TEZ_WALLBOARD_DIRECTIONS = {
    tez_tp: {
        key: 'tez_tp',
        label: 'ТП',
        hint: 'Техподдержка: очередь Binotel',
        title: 'Табло Тез КЦ · ТП',
        source: 'Binotel',
        clockField: 'binotel_now',
        icon: 'fa-headset',
        useSnapshot: useTezTpWallboardSnapshot,
        metrics: TEZ_TP_METRICS,
        metricMap: TEZ_TP_METRIC_MAP,
        metricGroups: TEZ_TP_METRIC_GROUPS,
        defaultMetrics: DEFAULT_TEZ_TP_METRICS,
    },
    tez_op: {
        key: 'tez_op',
        label: 'ОП',
        hint: 'Отдел продаж: очереди нет',
        title: 'Табло Тез КЦ · ОП',
        source: 'Binotel',
        clockField: 'binotel_now',
        icon: 'fa-phone-alt',
        useSnapshot: useTezOpWallboardSnapshot,
        metrics: TEZ_OP_METRICS,
        metricMap: TEZ_OP_METRIC_MAP,
        metricGroups: TEZ_OP_METRIC_GROUPS,
        defaultMetrics: DEFAULT_TEZ_OP_METRICS,
    },
};

export const TEZ_WALLBOARD_DIRECTION_LIST = Object.values(TEZ_WALLBOARD_DIRECTIONS);

export const tezWallboardDirection = (key) => TEZ_WALLBOARD_DIRECTIONS[key] || TEZ_WALLBOARD_DIRECTIONS.tez_tp;
