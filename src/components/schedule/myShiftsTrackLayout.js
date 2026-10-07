/*
 * «Мои смены»: раскладка двух полос факта под лентой смены.
 *
 * Лент у дня три (решение владельца, 07.10.2026):
 *   1) смена — план, как и раньше;
 *   2) статусы ВНУТРИ смены. За края смены полоса не выходит: сутки серого
 *      «Выключен» вокруг неё ничего не говорили и прятали саму смену. Работа
 *      до начала и после конца отмечается отдельной зелёной меткой рядом,
 *      опоздание — красным в начале смены;
 *   3) несоответствия — минуты смены, где статус не совпал с графиком.
 *
 * Здесь нет ни одного своего правила: что считать работой, перерывом,
 * опозданием и совпадением, решает plannerComputeShiftStatusMatchMetrics в
 * App.jsx — тот же расчёт, что у руководителя в «Графиках работы». Модуль
 * только режет его ответ по сменам и переводит минуты в проценты ширины.
 * Поэтому сумма красного в третьей полосе всегда равна «100% минус
 * совпадение», а не похожа на неё.
 *
 * Чистые функции без React и без часов: раскладку гоняет node-тест
 * (tests/my_shifts_track_layout.test.mjs) на настоящем расчёте из App.jsx.
 */

const DAY_MIN = 24 * 60;

/* Метка работы вне смены и красное опоздание короче этого не рисуются и не
   подписываются: статус, поставленный за двадцать секунд до начала смены, — не
   «начал раньше», а вход через двадцать секунд после — не опоздание. Полминуты
   — ровно то, что при округлении даёт «1 мин»: нарисованное всегда подписано. */
const MARK_MIN_MINUTES = 0.5;

const clampDay = (value) => Math.max(0, Math.min(DAY_MIN, Number(value) || 0));
const percentOfDay = (minutes) => (minutes / DAY_MIN) * 100;

const toDayInterval = (start, end) => {
    const from = clampDay(start);
    const to = clampDay(end);
    return to > from ? { start: from, end: to } : null;
};

const mergeIntervals = (list) => {
    const sorted = (list || []).filter(Boolean).slice().sort((a, b) => a.start - b.start);
    const merged = [];
    sorted.forEach((item) => {
        const last = merged[merged.length - 1];
        if (last && item.start <= last.end) last.end = Math.max(last.end, item.end);
        else merged.push({ start: item.start, end: item.end });
    });
    return merged;
};

const subtractIntervals = (base, cuts) => {
    let fragments = (base || []).filter(Boolean).map((item) => ({ start: item.start, end: item.end }));
    mergeIntervals(cuts).forEach((cut) => {
        fragments = fragments.flatMap((fragment) => {
            if (cut.end <= fragment.start || cut.start >= fragment.end) return [fragment];
            const next = [];
            if (cut.start > fragment.start) next.push({ start: fragment.start, end: cut.start });
            if (cut.end < fragment.end) next.push({ start: cut.end, end: fragment.end });
            return next;
        });
    });
    return fragments.filter((fragment) => fragment.end > fragment.start);
};

const intersect = (interval, bounds) => {
    const start = Math.max(interval.start, bounds.start);
    const end = Math.min(interval.end, bounds.end);
    return end > start ? { start, end } : null;
};

/* Время и длительность — теми же словами, что печатает раздел: «09:17»,
   «17 мин», «1ч 05м». Свои, а не из App.jsx: раскладку собирает useMemo выше
   по телу компонента, чем там объявлены его форматтеры. */
export const formatTrackTime = (minutes) => {
    const total = ((Math.round(Number(minutes) || 0) % DAY_MIN) + DAY_MIN) % DAY_MIN;
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
};

export const formatTrackDuration = (minutes) => {
    const total = Math.max(0, Math.round(Number(minutes) || 0));
    if (total < 60) return `${total} мин`;
    return `${Math.floor(total / 60)}ч ${String(total % 60).padStart(2, '0')}м`;
};

const rangeText = (start, end) => `${formatTrackTime(start)} — ${formatTrackTime(end)}`;

/* Один отрезок — два порядка слов. Подсказка на компьютере начинается с
   названия, как все подсказки раздела («Перерыв • 12:20 — 13:05 • 45 мин»).
   Подпись выбранного касанием отрезка — со времени и набрана плотнее
   («12:20–13:05 · 45 мин · Перерыв»): строка телефона узкая, и под многоточие
   должно уходить название, а не «когда» и «сколько». */
const describeSegment = (title, start, end, minutes) => {
    const duration = formatTrackDuration(minutes);
    return {
        tooltip: `${title} • ${rangeText(start, end)} • ${duration}`,
        caption: `${formatTrackTime(start)}–${formatTrackTime(end)} · ${duration} · ${title}`,
    };
};

const NO_STATUS_LABEL = 'Нет статуса';
/* Соседние статусы стыкуются секунда в секунду; зазор меньше секунды — тот же
   сплошной участок, а не разрыв. */
const RUN_JOIN_GAP_MIN = 1 / 60;
/* В подписи участка — не больше трёх названий, от самого долгого: четвёртое в
   строку телефона уже не помещается, а смысла не добавляет. */
const LABELS_IN_CAPTION = 3;

const joinLabels = (minutesByLabel) => {
    const labels = Array.from(minutesByLabel.entries())
        .sort((a, b) => b[1] - a[1])
        .map(([label]) => label);
    if (!labels.length) return NO_STATUS_LABEL;
    return labels.length > LABELS_IN_CAPTION
        ? `${labels.slice(0, LABELS_IN_CAPTION).join(', ')} …`
        : labels.join(', ');
};

const OUTSIDE_TITLES = {
    before: 'Работа до смены',
    after: 'Работа после смены',
    off: 'Работа вне смены',
};

const PLANNED_GENITIVE = { work: 'работы', break: 'перерыва' };

export const buildMyShiftsTrackLayout = ({
    shiftParts = [],
    statusBars = [],
    dayMetrics = null,
    boundaryMetrics = null,
} = {}) => {
    const shifts = mergeIntervals((shiftParts || []).map((part) => toDayInterval(part?.start, part?.end)));
    const bars = (statusBars || [])
        .map((bar) => {
            const interval = toDayInterval(bar?.startMin, bar?.endMin);
            if (!interval) return null;
            return {
                start: interval.start,
                end: interval.end,
                label: String(bar?.label || 'Статус'),
                background: bar?.background || null,
            };
        })
        .filter(Boolean)
        .sort((a, b) => (a.start - b.start) || (a.end - b.end));

    /* Опоздание — по смене, которая в этот день НАЧАЛАСЬ: у ночной 21:00–09:00
       оно принадлежит вечеру, а не утру следующих суток. Окно то же, что красит
       сетка «Графиков работы»: от начала смены (или конца обоснованного
       отсутствия) до первого рабочего статуса. */
    const lateWindows = (boundaryMetrics?.perShift || [])
        .map((shift) => {
            const lateMin = Number(shift?.lateMin || 0);
            if (!(lateMin >= MARK_MIN_MINUTES)) return null;
            const startRaw = Number(shift?.lateStartMin ?? shift?.start ?? 0);
            const interval = toDayInterval(startRaw, Math.min(Number(shift?.end || 0), startRaw + lateMin));
            if (!interval) return null;
            // В подсказке — окно целиком, а не его кусок до полуночи: у ночной
            // смены «23:00 — 00:00 • 1ч 15м» читалось бы как ошибка счёта.
            return { ...interval, lateMin, ...describeSegment('Опоздание', startRaw, startRaw + lateMin, lateMin) };
        })
        .filter(Boolean);

    const mismatchSource = (Array.isArray(dayMetrics?.mismatchIntervals) ? dayMetrics.mismatchIntervals : [])
        .map((item) => {
            const interval = toDayInterval(item?.start, item?.end);
            return interval ? { ...interval, planned: item?.planned === 'break' ? 'break' : 'work' } : null;
        })
        .filter(Boolean);

    /* Какие статусы стояли на несовпавшем отрезке: «Перерыв вместо работы»
       говорит больше, чем просто красное. Отрезок не дробится по сменам
       статусов — «Готов» и «Занят» чередуются каждые несколько минут, и вместо
       одного «обед не по графику» вышла бы дюжина обрывков; статусы просто
       перечислены, от самого долгого. */
    const factsWithin = (interval) => {
        const minutesByFact = new Map();
        const add = (fact, minutes) => {
            if (minutes > 0) minutesByFact.set(fact, (minutesByFact.get(fact) || 0) + minutes);
        };
        let cursor = interval.start;
        bars.forEach((bar) => {
            const start = Math.max(bar.start, cursor);
            const end = Math.min(bar.end, interval.end);
            if (end <= start) return;
            add(NO_STATUS_LABEL, start - cursor);
            add(bar.label, end - start);
            cursor = end;
        });
        add(NO_STATUS_LABEL, interval.end - cursor);
        return joinLabels(minutesByFact);
    };

    const totalsByLabel = new Map();
    let mismatchMin = 0;

    const spans = shifts.map((shift, shiftIndex) => {
        const length = shift.end - shift.start;
        const place = (start, end) => ({
            startMin: start,
            endMin: end,
            left: ((start - shift.start) / length) * 100,
            width: ((end - start) / length) * 100,
        });

        const late = lateWindows
            .map((window) => {
                const piece = intersect(window, shift);
                if (!piece) return null;
                return { ...place(piece.start, piece.end), lateMin: window.lateMin, tooltip: window.tooltip, caption: window.caption };
            })
            .filter(Boolean)
            .map((item, index) => ({ ...item, key: `late-${shiftIndex}-${index}` }));

        /* Под красным опозданием статус не рисуем и в подписи не считаем: иначе в
           легенде стояло бы «Выключен 17 мин», которого на полосе не видно. */
        const lateCuts = late.map((item) => ({ start: item.startMin, end: item.endMin }));
        const statusPieces = [];
        /* Сплошной участок одного цвета глаз видит одним куском, хотя под ним
           может лежать десяток статусов: «Готов» и «Занят» в справочнике одного
           цвета. Касание на телефоне выбирает его целиком (runKey) — отдельные
           четыре минуты «Занят» внутри синего пальцем не выбрать и незачем. */
        const runs = [];
        /* Статусы внахлёст (свежий срез дня лёг поверх отрезка выгрузки) рисуются
           и считаются один раз: кто начался раньше, тот и занимает минуты. Иначе
           в подписи под лентой вышло бы «Готов 10ч» при девятичасовой смене. */
        let covered = shift.start;
        bars.forEach((bar, barIndex) => {
            const inside = intersect({ start: Math.max(bar.start, covered), end: bar.end }, shift);
            if (!inside) return;
            covered = inside.end;
            subtractIntervals([inside], lateCuts).forEach((piece, pieceIndex) => {
                const minutes = piece.end - piece.start;
                let run = runs[runs.length - 1];
                if (!run || run.background !== bar.background || (piece.start - run.endMin) >= RUN_JOIN_GAP_MIN) {
                    run = {
                        key: `run-${shiftIndex}-${runs.length}`,
                        startMin: piece.start,
                        endMin: piece.end,
                        background: bar.background,
                        minutesByLabel: new Map(),
                    };
                    runs.push(run);
                }
                run.endMin = Math.max(run.endMin, piece.end);
                run.minutesByLabel.set(bar.label, (run.minutesByLabel.get(bar.label) || 0) + minutes);
                statusPieces.push({
                    key: `status-${shiftIndex}-${barIndex}-${pieceIndex}`,
                    runKey: run.key,
                    ...place(piece.start, piece.end),
                    label: bar.label,
                    background: bar.background,
                    tooltip: `${bar.label} • ${rangeText(bar.start, bar.end)}`,
                });
                const totals = totalsByLabel.get(bar.label)
                    || { key: bar.label, label: bar.label, background: bar.background, minutes: 0 };
                totals.minutes += minutes;
                totalsByLabel.set(bar.label, totals);
            });
        });

        const mismatches = [];
        mismatchSource.forEach((item) => {
            const inside = intersect(item, shift);
            if (!inside) return;
            const duration = inside.end - inside.start;
            mismatchMin += duration;
            mismatches.push({
                key: `mismatch-${shiftIndex}-${mismatches.length}`,
                ...place(inside.start, inside.end),
                planned: item.planned,
                ...describeSegment(`${factsWithin(inside)} вместо ${PLANNED_GENITIVE[item.planned]}`, inside.start, inside.end, duration),
            });
        });

        return {
            key: `shift-${shiftIndex}`,
            startMin: shift.start,
            endMin: shift.end,
            left: percentOfDay(shift.start),
            width: percentOfDay(length),
            bars: statusPieces,
            runs: runs.map((run) => ({
                key: run.key,
                startMin: run.startMin,
                endMin: run.endMin,
                background: run.background,
                ...describeSegment(joinLabels(run.minutesByLabel), run.startMin, run.endMin, run.endMin - run.startMin),
            })),
            late,
            mismatches,
        };
    });

    /* Работа вне смены. Кусок лежит целиком между сменами, поэтому «раньше» или
       «после» решает ближайшая граница: десять минут после утренней смены — это
       «после», а двадцать перед вечерней — «раньше». */
    const outsideKind = (interval) => {
        if (!shifts.length) return 'off';
        const previous = shifts.filter((shift) => shift.end <= interval.start + 1e-6).pop() || null;
        const next = shifts.find((shift) => shift.start >= interval.end - 1e-6) || null;
        if (previous && next) return (interval.start - previous.end) <= (next.start - interval.end) ? 'after' : 'before';
        return next ? 'before' : 'after';
    };
    const outsideTotals = { before: 0, after: 0, off: 0 };
    const outside = subtractIntervals(
        mergeIntervals((Array.isArray(dayMetrics?.workOutsideShiftIntervals) ? dayMetrics.workOutsideShiftIntervals : [])
            .map((item) => toDayInterval(item?.start, item?.end))),
        shifts
    )
        .filter((interval) => (interval.end - interval.start) >= MARK_MIN_MINUTES)
        .map((interval, index) => {
            const kind = outsideKind(interval);
            const duration = interval.end - interval.start;
            outsideTotals[kind] += duration;
            return {
                key: `outside-${index}`,
                kind,
                startMin: interval.start,
                endMin: interval.end,
                left: percentOfDay(interval.start),
                width: percentOfDay(duration),
                ...describeSegment(OUTSIDE_TITLES[kind], interval.start, interval.end, duration),
            };
        });

    const hasInside = spans.some((span) => span.bars.length > 0 || span.late.length > 0 || span.mismatches.length > 0);

    return {
        spans,
        outside,
        totals: Array.from(totalsByLabel.values()).sort((a, b) => b.minutes - a.minutes),
        beforeMin: outsideTotals.before,
        afterMin: outsideTotals.after,
        offMin: outsideTotals.off,
        mismatchMin,
        // Нечего показать — раздел полосы не рисует вовсе: пустая дорожка с
        // подписью «Статусы» оператору ничего не сообщает.
        isEmpty: !hasInside && outside.length === 0,
    };
};

/*
 * Слова под лентами: совпадение, отклонения, работа вне смены и итог третьей
 * ленты. Печатается только то, что есть: «опоздание 0 мин» каждый день — ровно
 * тот шум, из-за которого строку перестают читать. Пороги те же, что у меток
 * на полосе, поэтому нарисованное всегда подписано, а подписанное нарисовано.
 * Порога «переработки» в десять минут, как в сетке руководителя, здесь нет:
 * владелец просил отмечать и ранний вход, и задержку после смены.
 */
export const describeMyShiftsTrack = ({ layout = null, metrics = null } = {}) => {
    const minutesText = (label, minutes) => (
        Math.round(Number(minutes) || 0) >= 1 ? `${label} ${formatTrackDuration(minutes)}` : null
    );
    const listOf = (items) => items
        .map(([key, label, minutes]) => ({ key, text: minutesText(label, minutes) }))
        .filter((item) => item.text);
    const mismatchMin = Number(layout?.mismatchMin) || 0;
    return {
        compliance: metrics
            ? (metrics.compliancePct != null ? `${Math.round(metrics.compliancePct)}%` : '—')
            : null,
        problems: metrics
            ? listOf([['late', 'опоздание', metrics.lateTotalMin], ['early', 'ранний уход', metrics.earlyLeaveTotalMin]])
            : [],
        outside: listOf([
            ['before', 'до смены', layout?.beforeMin],
            ['after', 'после смены', layout?.afterMin],
            ['off', 'вне смены', layout?.offMin],
        ]),
        /* Третья лента есть только там, где есть с чем сравнивать: у дня без
           смены и у смены, до которой статусы ещё не дошли, её нет (null). */
        mismatchLabel: metrics
            ? (mismatchMin >= 1 ? formatTrackDuration(mismatchMin) : (mismatchMin > 0 ? 'меньше минуты' : 'нет'))
            : null,
    };
};

/*
 * Что под пальцем. На телефоне наведения нет, а отрезок в минуту шириной в
 * пиксель пальцем не поймать. Поэтому у каждого отрезка есть зона касания не
 * уже minTargetMin (в минутах оси — столько, сколько занимает на экране палец):
 * широкий отрезок берётся там, где он нарисован, узкому зона достраивается
 * поровну в обе стороны. Если касание попало в зоны нескольких, выигрывает
 * узкий — широкий сосед доступен в любой другой своей точке, узкий только
 * здесь; из двух узких — тот, чья середина ближе.
 */
export const pickTrackSegment = (segments, minute, minTargetMin = 0) => {
    const point = Number(minute);
    if (!Number.isFinite(point)) return null;
    const minTarget = Math.max(0, Number(minTargetMin) || 0);
    let best = null;
    (segments || []).forEach((segment) => {
        const start = Number(segment?.startMin);
        const end = Number(segment?.endMin);
        if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return;
        const zone = Math.max(end - start, minTarget);
        const distance = Math.abs(point - (start + end) / 2);
        if (distance > zone / 2) return;
        if (!best || zone < best.zone || (zone === best.zone && distance < best.distance)) {
            best = { segment, zone, distance };
        }
    });
    return best ? best.segment : null;
};

/*
 * Касание по ряду ленты → какой отрезок теперь выбран. offsetX — расстояние от
 * левого края ряда, width — его ширина (ось суток растянута на неё целиком),
 * targetPx — ширина пальца. Второе касание того же отрезка и касание мимо всех
 * снимают выбор.
 */
export const resolveTrackTap = ({ row, segments = [], offsetX = 0, width = 0, targetPx = 0, current = null } = {}) => {
    if (!(width > 0)) return current;
    const hit = pickTrackSegment(segments, (offsetX / width) * DAY_MIN, (targetPx / width) * DAY_MIN);
    if (!hit) return null;
    return current && current.row === row && current.key === hit.key ? null : { row, key: hit.key };
};
