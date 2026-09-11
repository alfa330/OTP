import React, { useMemo, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { APPLE_FONT, iosCard } from '../ui/ios';
import { formatNumber, formatPercent, formatSeconds, toneForRate } from './funnelFormat';

/**
 * Таблица операторов направления: строка на человека, итог по команде снизу.
 *
 * Два набора колонок, потому что направления считаются по-разному. У «Потока»,
 * «Основы» и «Яндекс Регистрации» это воронка обзвона (kind='funnel'), у
 * Верификаторов обзвона нет вовсе — там нагрузка и скорость ответа
 * (kind='load'), и рисовать им пустые колонки дозвона значило бы показывать
 * прочерки там, где показателя не существует.
 *
 * ЦВЕТ ТОЛЬКО У ВЫПОЛНЕНИЯ ПЛАНА. Требование владельца: раскрашенная таблица
 * перестаёт читаться, потому что глаз цепляется за цвет, а не за число.
 * Порогов ровно два и приходят они из норм направления (`green_from`,
 * `amber_from`), а не зашиты здесь: руководитель меняет их в «Настройках», и
 * таблица обязана краситься по новым, а не по прошлогодним.
 *
 * Props:
 *  - operators: строки `/api/op_funnel/operators` (поле `operators`)
 *  - total: итог команды оттуда же (`total`), со вложенным `rates`
 *  - targets: нормы направления ({metric: число}) — пороги цвета и таргеты
 *  - kind: 'funnel' | 'load'
 *  - onOpenOperator: (row) => void, клик по имени; без него имя не кнопка
 */

const SHIFT_LABELS = { day: 'День', night: 'Ночь' };

/* Дробное число в ячейке. Не `formatNumber` и не `formatHours`: первый
   округляет до целого (а 7,5 часа смены — это не 8), второй дописывает «ч»,
   что в колонке, уже названной «Часы», было бы повтором в каждой строке. */
const decimal = (value, digits = 1) => {
    const number = Number(value);
    if (value === null || value === undefined || !Number.isFinite(number)) return '—';
    return number.toLocaleString('ru-RU', {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
    });
};

/* «Разница от таргета» = таргет / факт − 1, знак — как в файле супервайзера:
   ПЛЮС значит «ответили быстрее таргета». Это относительная разница, а не
   процентные пункты, поэтому подписи «п.п.» здесь быть не должно — она бы
   врала про единицу измерения. Тот же знак считает `metrics.verificator_rates`
   на сервере, и расходиться таблице с выгрузкой нельзя. */
const gapText = (gap) => {
    const number = Number(gap);
    if (gap === null || gap === undefined || !Number.isFinite(number)) return '—';
    const sign = number > 0 ? '+' : number < 0 ? '−' : '';
    return `${sign}${formatPercent(Math.abs(number))}`;
};

/* Стаж считаем от даты найма помесячно, а не делением дней на 30: «1 г. 0 мес.»
   у человека, вышедшего год назад, — это то, что скажет и кадровик. */
const monthsSince = (iso, now) => {
    if (!iso) return null;
    const start = new Date(`${iso}T00:00:00`);
    if (Number.isNaN(start.getTime())) return null;
    let months = (now.getFullYear() - start.getFullYear()) * 12 + (now.getMonth() - start.getMonth());
    if (now.getDate() < start.getDate()) months -= 1;
    return months < 0 ? null : months;
};

const tenureText = (months) => {
    if (months === null) return '—';
    if (months < 1) return 'меньше мес.';
    const years = Math.floor(months / 12);
    const rest = months % 12;
    if (!years) return `${months} мес.`;
    return rest ? `${years} г. ${rest} мес.` : `${years} г.`;
};

const rateText = (rate) => {
    const number = Number(rate);
    /* Нулевая ставка в реестре означает «не заполнена», а не «ноль ставки»:
       ноль и «нет данных» на экране обязаны выглядеть по-разному. */
    if (!Number.isFinite(number) || number <= 0) return '—';
    return decimal(number, 1);
};

const TONE_TEXT = {
    green: 'text-emerald-600',
    amber: 'text-amber-600',
    red: 'text-rose-600',
};

/* Нормы приходят в ПРОЦЕНТАХ (100 и 80), а выполнение — долей (0,692).
   Приводим пороги к той же единице у самого вызова: сравнение 0,692 с 100
   молча красило бы всю колонку красным, и никто бы не заметил, что таблица
   врёт цветом. */
const planTone = (rate, targets) => {
    if (rate === null || rate === undefined) return '';
    const green = Number(targets.green_from);
    const amber = Number(targets.amber_from);
    if (!Number.isFinite(green) || !Number.isFinite(amber)) return '';
    return toneForRate(rate, green / 100, amber / 100);
};

/* У Верификатора «обработано» — это чаты плюс тикеты: колонки разные, а
   нагрузка одна, и ровно так же складывает `metrics.verificator_rates`. */
const loadHandled = (row) => Number(row.chats || 0) + Number(row.tickets || 0);

const loadPlanRate = (row, targets) => {
    const fact = row.rates?.chats_per_hour;
    const target = Number(targets.chats_per_hour);
    if (fact === null || fact === undefined || !Number.isFinite(target) || target <= 0) return null;
    return Number(fact) / target;
};

/* Сервер сравнивает и время ответа, и время тикета с одним таргетом
   `reply_seconds` (отдельной нормы на тикет в справочнике норм нет). Повторяем
   это здесь буквально: своя формула на фронте дала бы вторую правду. */
const secondsGap = (seconds, targets) => {
    const fact = Number(seconds);
    const target = Number(targets.reply_seconds);
    if (!Number.isFinite(fact) || fact <= 0 || !Number.isFinite(target) || target <= 0) return null;
    return target / fact - 1;
};

const num = (row, field) => Number(row[field] || 0);
const rate = (row, field) => {
    const value = row.rates?.[field];
    return value === null || value === undefined ? null : Number(value);
};

/* Колонка: как тянуть значение для сортировки (`sort`) и как его показать
   (`cell`). `foot: false` — колонка, которой у команды не существует: общего
   стажа или общей смены нет, и прочерк там честнее выдуманного среднего. */
const COMMON_COLUMNS = [
    {
        key: 'name',
        label: 'Оператор',
        align: 'left',
        sticky: true,
        text: true,
        sort: (row) => (row.name || '').toLowerCase(),
        cell: (row) => row.name || '—',
        foot: false,
    },
    {
        key: 'tenure',
        label: 'Стаж',
        align: 'right',
        hint: 'От даты найма на сегодня',
        sort: (row, ctx) => monthsSince(row.hire_date, ctx.now),
        cell: (row, ctx) => tenureText(monthsSince(row.hire_date, ctx.now)),
        foot: false,
    },
    {
        key: 'shift_kind',
        label: 'Смена',
        align: 'left',
        text: true,
        sort: (row) => SHIFT_LABELS[row.shift_kind] || row.shift_kind || '',
        cell: (row) => SHIFT_LABELS[row.shift_kind] || row.shift_kind || '—',
        foot: false,
    },
    {
        key: 'rate',
        label: 'Ставка',
        align: 'right',
        sort: (row) => (Number(row.rate) > 0 ? Number(row.rate) : null),
        cell: (row) => rateText(row.rate),
        foot: false,
    },
    {
        key: 'work_hours',
        label: 'Часы',
        align: 'right',
        sort: (row) => num(row, 'work_hours'),
        cell: (row, ctx) => <HoursCell row={row} ctx={ctx} />,
    },
];

const FUNNEL_COLUMNS = [
    ...COMMON_COLUMNS,
    {
        key: 'handled', label: 'Обработано', align: 'right', strong: true,
        hint: 'Лидов взято в работу за период',
        sort: (row) => num(row, 'handled'),
        cell: (row) => formatNumber(row.handled),
    },
    {
        key: 'leads_per_hour', label: 'Лидов/час', align: 'right',
        sort: (row) => rate(row, 'leads_per_hour'),
        cell: (row) => decimal(rate(row, 'leads_per_hour'), 1),
    },
    {
        key: 'reached', label: 'Дозвон', align: 'right',
        sort: (row) => num(row, 'reached'),
        cell: (row) => formatNumber(row.reached),
    },
    {
        key: 'reach_rate', label: '% дозвона', align: 'right',
        hint: 'Дозвон от всей взятой в работу базы',
        sort: (row) => rate(row, 'reach_rate'),
        cell: (row) => formatPercent(rate(row, 'reach_rate')),
    },
    {
        key: 'agreed', label: 'Согласия', align: 'right',
        sort: (row) => num(row, 'agreed'),
        cell: (row) => formatNumber(row.agreed),
    },
    {
        key: 'agree_rate', label: '% согласий', align: 'right',
        hint: 'Согласия от дозвонившихся',
        sort: (row) => rate(row, 'agree_rate'),
        cell: (row) => formatPercent(rate(row, 'agree_rate')),
    },
    {
        key: 'succeeded', label: 'Успешно', align: 'right',
        sort: (row) => num(row, 'succeeded'),
        cell: (row) => formatNumber(row.succeeded),
    },
    {
        key: 'success_rate', label: '% успешно', align: 'right',
        hint: 'Успешно от согласившихся',
        sort: (row) => rate(row, 'success_rate'),
        cell: (row) => formatPercent(rate(row, 'success_rate')),
    },
    {
        key: 'rejected', label: 'Отказы', align: 'right',
        sort: (row) => num(row, 'rejected'),
        cell: (row) => formatNumber(row.rejected),
    },
    {
        key: 'untargeted', label: 'Нецелевые', align: 'right',
        sort: (row) => num(row, 'untargeted'),
        cell: (row) => formatNumber(row.untargeted),
    },
    {
        key: 'plan_reached', label: 'План', align: 'right',
        hint: 'Часы × норму дозвонов в час',
        sort: (row) => num(row, 'plan_reached'),
        cell: (row) => decimal(row.plan_reached, 0),
    },
    {
        key: 'plan_reached_rate', label: '% плана', align: 'right', strong: true,
        hint: 'Дозвон к плану на отработанные часы',
        sort: (row) => rate(row, 'plan_reached_rate'),
        tone: (row, ctx) => planTone(rate(row, 'plan_reached_rate'), ctx.targets),
        cell: (row) => formatPercent(rate(row, 'plan_reached_rate')),
    },
];

const LOAD_COLUMNS = [
    ...COMMON_COLUMNS,
    {
        key: 'chats', label: 'Чаты', align: 'right',
        sort: (row) => num(row, 'chats'),
        cell: (row) => formatNumber(row.chats),
    },
    {
        key: 'tickets', label: 'Тикеты', align: 'right',
        hint: 'Из ручной выгрузки СРМ: партнёрской ручки обращений нет',
        sort: (row) => num(row, 'tickets'),
        cell: (row) => formatNumber(row.tickets),
    },
    {
        key: 'load_handled', label: 'Обработано', align: 'right', strong: true,
        hint: 'Чаты плюс тикеты',
        sort: (row) => loadHandled(row),
        cell: (row) => formatNumber(loadHandled(row)),
    },
    {
        key: 'chats_per_hour', label: 'Чаты/час', align: 'right',
        sort: (row) => rate(row, 'chats_per_hour'),
        cell: (row) => decimal(rate(row, 'chats_per_hour'), 1),
    },
    {
        key: 'chats_plan_rate', label: '% факт', align: 'right', strong: true,
        hint: 'Нагрузка в час к таргету смены',
        sort: (row, ctx) => loadPlanRate(row, ctx.targets),
        tone: (row, ctx) => planTone(loadPlanRate(row, ctx.targets), ctx.targets),
        cell: (row, ctx) => formatPercent(loadPlanRate(row, ctx.targets)),
    },
    {
        key: 'chat_reply_seconds', label: 'Ср. время ответа', align: 'right',
        hint: 'От входящего до первого ответа человека',
        sort: (row) => (row.chat_reply_seconds == null ? null : Number(row.chat_reply_seconds)),
        cell: (row) => formatSeconds(row.chat_reply_seconds),
    },
    {
        key: 'reply_gap', label: 'Разница от таргета', align: 'right',
        hint: 'Таргет / факт − 1. Плюс — ответили быстрее таргета',
        sort: (row, ctx) => secondsGap(row.chat_reply_seconds, ctx.targets),
        cell: (row, ctx) => gapText(secondsGap(row.chat_reply_seconds, ctx.targets)),
    },
    {
        key: 'ticket_handle_seconds', label: 'Ср. время тикета', align: 'right',
        sort: (row) => (row.ticket_handle_seconds == null ? null : Number(row.ticket_handle_seconds)),
        cell: (row) => formatSeconds(row.ticket_handle_seconds),
    },
    {
        key: 'ticket_gap', label: 'Разница от таргета', align: 'right',
        hint: 'Сравнение с тем же таргетом времени ответа: отдельной нормы на тикет нет',
        sort: (row, ctx) => secondsGap(row.ticket_handle_seconds, ctx.targets),
        cell: (row, ctx) => gapText(secondsGap(row.ticket_handle_seconds, ctx.targets)),
    },
];

/** Часы с отметкой источника: план по графику или факт по телефону. */
const HoursCell = ({ row, ctx }) => {
    const bySchedule = ctx.scheduleHours(row);
    return (
        <span className="inline-flex items-center justify-end gap-1">
            {decimal(row.work_hours, 1)}
            {bySchedule && (
                /* Часы по графику — это ПЛАН, а не факт. Верификаторы не работают
                   на телефоне, и у них `daily_hours.work_time` нулевой; без этой
                   отметки человек читал бы плановые часы как отработанные и
                   считал по ним выполнение. */
                <FaIcon
                    className="fas fa-calendar-days text-slate-400"
                    title={bySchedule === 'partial'
                        ? 'Часть часов взята по графику смен, а не по статусам телефона'
                        : 'По графику смен, а не по статусам телефона'}
                    aria-label="Часы по графику смен"
                    style={{ fontSize: '0.85em' }}
                />
            )}
        </span>
    );
};

/* Строка «Не сопоставлен» — это не человек, а пробел в сопоставлении. Она
   попадает в таблицу, только если за ней стоят числа: пустая строка-предупреждение
   была бы шумом, а строка с 300 лидами без владельца — поводом идти в настройки. */
const hasNumbers = (row) => (
    num(row, 'handled') > 0 || num(row, 'reached') > 0 || num(row, 'agreed') > 0
    || num(row, 'chats') > 0 || num(row, 'tickets') > 0 || num(row, 'work_hours') > 0
);

const compareValues = (left, right, dir) => {
    /* Пусто всегда внизу, в обе стороны сортировки: «нет данных» не должно
       выигрывать первое место только потому, что сортируют по возрастанию. */
    const leftEmpty = left === null || left === undefined || left === '';
    const rightEmpty = right === null || right === undefined || right === '';
    if (leftEmpty && rightEmpty) return 0;
    if (leftEmpty) return 1;
    if (rightEmpty) return -1;
    const sign = dir === 'asc' ? 1 : -1;
    if (typeof left === 'string' || typeof right === 'string') {
        return String(left).localeCompare(String(right), 'ru') * sign;
    }
    return (left - right) * sign;
};

const OperatorsTable = ({
    operators = [],
    total = null,
    targets = {},
    kind = 'funnel',
    onOpenOperator = null,
}) => {
    const columns = kind === 'load' ? LOAD_COLUMNS : FUNNEL_COLUMNS;
    const [sort, setSort] = useState({ key: 'name', dir: 'asc' });

    /* «Сегодня» фиксируем на состав таблицы, а не берём новое в каждой ячейке:
       иначе стаж пересчитывался бы по разу на строку и на каждый рендер. */
    const now = useMemo(() => new Date(), [operators]);

    const anySchedule = useMemo(
        () => operators.some((row) => row.hours_source === 'schedule'),
        [operators],
    );

    /* Нормы приходят новым объектом на каждый рендер родителя. По самому объекту
       пересобирался бы контекст, а за ним — пересортировка всей таблицы на любой
       чужой рендер; ключом по содержимому это стоит одного JSON.stringify. */
    const targetsKey = JSON.stringify(targets || {});

    const ctx = useMemo(() => ({
        targets: targets || {},
        now,
        /* Для строки оператора источник свой, для итога — «частично», если хотя бы
           у кого-то часы плановые: общая сумма в этом случае тоже не чистый факт. */
        scheduleHours: (row) => (
            row === total ? (anySchedule ? 'partial' : false) : (row.hours_source === 'schedule')
        ),
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }), [targetsKey, now, total, anySchedule]);

    const { people, unmapped } = useMemo(() => {
        const column = columns.find((item) => item.key === sort.key) || columns[0];
        const rows = [];
        let orphan = null;
        operators.forEach((row) => {
            if ((row.user_id || 0) === 0) orphan = row;
            else rows.push(row);
        });
        rows.sort((a, b) => compareValues(column.sort(a, ctx), column.sort(b, ctx), sort.dir));
        return { people: rows, unmapped: orphan && hasNumbers(orphan) ? orphan : null };
    }, [operators, columns, sort, ctx]);

    const toggleSort = (column) => {
        setSort((prev) => (
            prev.key === column.key
                ? { key: column.key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
                /* Текст начинают читать с «А», числа — с наибольшего: по
                   «Обработано» руководителю нужен верх списка, а не нули. */
                : { key: column.key, dir: column.text ? 'asc' : 'desc' }
        ));
    };

    if (!operators.length) {
        return (
            <div className={`${iosCard} px-4 py-10 text-center text-[13px] text-slate-500`}
                 style={{ fontFamily: APPLE_FONT }}>
                За этот период по направлению строк нет.
            </div>
        );
    }

    return (
        <div className={`${iosCard} overflow-hidden`} style={{ fontFamily: APPLE_FONT }}>
            {/* Прокрутка живёт ЗДЕСЬ, а не на странице: иначе семнадцать колонок
                утаскивали бы вбок весь экран вместе с сайдбаром. */}
            <div className="max-h-[70vh] overflow-auto">
                <table className="min-w-full border-collapse text-[13px]">
                    <thead>
                        <tr>
                            {columns.map((column) => {
                                const active = sort.key === column.key;
                                return (
                                    <th
                                        key={column.key}
                                        scope="col"
                                        aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                                        title={column.hint}
                                        className={`sticky top-0 whitespace-nowrap border-b border-slate-200/70
                                            bg-white/95 px-3 py-2 text-[11px] font-semibold uppercase
                                            tracking-wider text-slate-500 backdrop-blur ${
                                            column.sticky ? 'left-0 z-30 min-w-[200px] text-left' : 'z-20'
                                        } ${column.align === 'right' && !column.sticky ? 'text-right' : 'text-left'}`}
                                    >
                                        <button
                                            type="button"
                                            onClick={() => toggleSort(column)}
                                            className={`inline-flex items-center gap-1 uppercase tracking-wider
                                                transition hover:text-slate-900 active:scale-[0.98] ${
                                                active ? 'text-slate-900' : ''
                                            } ${column.align === 'right' && !column.sticky ? 'flex-row-reverse' : ''}`}
                                        >
                                            {column.label}
                                            {/* Стрелка только у той колонки, по которой сортируют, и только
                                                иконкой: символы ↕ ↑ ↓ браузер рисует цветным эмодзи-глифом. */}
                                            {active && (
                                                <FaIcon
                                                    className={`fas fa-chevron-${sort.dir === 'asc' ? 'up' : 'down'} text-blue-500`}
                                                    aria-hidden="true"
                                                    style={{ fontSize: '0.8em' }}
                                                />
                                            )}
                                        </button>
                                    </th>
                                );
                            })}
                        </tr>
                    </thead>

                    <tbody className="divide-y divide-slate-100">
                        {people.map((row) => (
                            <tr key={row.user_id} className="group transition-colors hover:bg-slate-50/80">
                                {columns.map((column) => {
                                    const tone = column.tone ? column.tone(row, ctx) : '';
                                    if (column.sticky) {
                                        return (
                                            <td key={column.key}
                                                className="sticky left-0 z-10 whitespace-nowrap bg-white px-3 py-2
                                                           text-slate-900 transition-colors group-hover:bg-slate-50">
                                                {onOpenOperator ? (
                                                    <button type="button"
                                                            onClick={() => onOpenOperator(row)}
                                                            className="font-medium text-slate-900 transition hover:text-blue-600 active:scale-[0.98]">
                                                        {row.name || '—'}
                                                    </button>
                                                ) : (
                                                    <span className="font-medium">{row.name || '—'}</span>
                                                )}
                                            </td>
                                        );
                                    }
                                    return (
                                        <td key={column.key}
                                            className={`whitespace-nowrap px-3 py-2 tabular-nums ${
                                                column.align === 'right' ? 'text-right' : 'text-left'
                                            } ${column.strong ? 'font-semibold' : ''} ${
                                                TONE_TEXT[tone] || (column.strong ? 'text-slate-900' : 'text-slate-600')
                                            }`}>
                                            {column.cell(row, ctx)}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}

                        {unmapped && (
                            /* Проблема, а не оператор: янтарная строка со знаком и подписью,
                               по имени кликать некуда — открывать нечего. */
                            <tr className="group bg-amber-50/70">
                                {columns.map((column) => {
                                    if (column.sticky) {
                                        return (
                                            <td key={column.key}
                                                className="sticky left-0 z-10 whitespace-nowrap bg-amber-50 px-3 py-2">
                                                <span className="inline-flex items-center gap-1.5 font-medium text-amber-800">
                                                    <FaIcon className="fas fa-triangle-exclamation" aria-hidden="true"
                                                            style={{ fontSize: '0.9em' }} />
                                                    Не сопоставлен
                                                </span>
                                                <div className="text-[11px] font-normal text-amber-700">
                                                    данные без владельца — сопоставьте в «Настройках»
                                                </div>
                                            </td>
                                        );
                                    }
                                    /* Стаж, смена и ставка у несопоставленных данных отсутствуют
                                       в принципе — рисовать там прочерк честнее, чем нули. */
                                    const value = column.foot === false ? '—' : column.cell(unmapped, ctx);
                                    return (
                                        <td key={column.key}
                                            className={`whitespace-nowrap px-3 py-2 tabular-nums text-amber-800 ${
                                                column.align === 'right' ? 'text-right' : 'text-left'
                                            }`}>
                                            {value}
                                        </td>
                                    );
                                })}
                            </tr>
                        )}
                    </tbody>

                    {total && (
                        <tfoot>
                            <tr className="text-[12.5px] font-semibold text-slate-700">
                                {columns.map((column) => {
                                    if (column.sticky) {
                                        return (
                                            <td key={column.key}
                                                title="Считается по всем строкам периода, включая «Не сопоставлен»"
                                                className="sticky bottom-0 left-0 z-30 whitespace-nowrap border-t
                                                           border-slate-200/70 bg-slate-50/95 px-3 py-2.5 backdrop-blur">
                                                Итого
                                                <span className="ml-1.5 text-[11px] font-normal tabular-nums text-slate-500">
                                                    {formatNumber(total.operators)} чел.
                                                </span>
                                            </td>
                                        );
                                    }
                                    const tone = column.tone ? column.tone(total, ctx) : '';
                                    return (
                                        <td key={column.key}
                                            className={`sticky bottom-0 z-20 whitespace-nowrap border-t border-slate-200/70
                                                        bg-slate-50/95 px-3 py-2.5 tabular-nums backdrop-blur ${
                                                column.align === 'right' ? 'text-right' : 'text-left'
                                            } ${TONE_TEXT[tone] || ''}`}>
                                            {column.foot === false ? '' : column.cell(total, ctx)}
                                        </td>
                                    );
                                })}
                            </tr>
                        </tfoot>
                    )}
                </table>
            </div>
        </div>
    );
};

export default OperatorsTable;
