import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Activity, AlertCircle, AlertTriangle, ArrowDown, ArrowUp, Bell, BellOff, Building2,
    CalendarClock, CheckCircle2, ChevronDown, Clock, Download, FileSpreadsheet, Loader2,
    Link2, LogOut, MapPin, MessageSquare, Moon, Plus, RefreshCw, Search, Send, ShieldAlert,
    Trash2, Users, UserX, X, Zap,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosModal, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';

/* Раздел «Бот опозданий» — контроль отметок Workpace: наш бот следит за
 * нарушениями графика и шлёт их в рабочие чаты Telegram.
 *
 * Всё живёт внутри приложения: опрос — джобой планировщика, настройки, найденные
 * нарушения и отчёты — в таблицах glb_*, поэтому раздел читает их напрямую.
 * Подтверждения «Отбито» под уведомлением нет: отметка ничего не меняла ни в
 * Workpace, ни в отчётах и не хранила причину, поэтому механику убрали. */

const EVENT_TYPES = {
    late: { label: 'Опоздание', tone: 'red', icon: Clock },
    missing: { label: 'Не пришёл', tone: 'red', icon: UserX },
    early_out: { label: 'Ранний уход', tone: 'amber', icon: LogOut },
    missing_out: { label: 'Нет отметки об уходе', tone: 'amber', icon: AlertTriangle },
    late_out: { label: 'Поздний уход', tone: 'blue', icon: Moon },
    suspicious: { label: 'Подозрительная отметка', tone: 'amber', icon: ShieldAlert },
};

const eventMeta = (type) => EVENT_TYPES[type] || { label: type || '—', tone: 'slate', icon: Bell };

/* Статусы дня на вкладке «Отметки». Цветом отмечено только то, где он несёт смысл:
 * «вовремя» и «не отмечается» нейтральные — первое потому, что вопросов нет, второе
 * потому, что это про человека, который вообще не пользуется терминалом, а не про
 * нарушение. Подписи приходят с сервера, здесь только тон. */
const ATTENDANCE_STATUS_TONES = {
    absent: 'red',
    late: 'red',
    early_out: 'amber',
    no_out: 'amber',
    off_schedule: 'blue',
    no_terminal: 'slate',
    ok: 'slate',
};

/* Оси сортировки из постановки: подразделение, локация, приход/уход. Остальные
 * добавлены потому, что таблицу читают ради них же. */
const ATTENDANCE_SORTS = [
    { value: 'status', label: 'Сначала проблемные' },
    { value: 'employee', label: 'По ФИО' },
    { value: 'department', label: 'По подразделению' },
    { value: 'location', label: 'По локации' },
    { value: 'position', label: 'По должности' },
    { value: 'fact_in', label: 'По времени прихода' },
    { value: 'fact_out', label: 'По времени ухода' },
    { value: 'late_minutes', label: 'По опозданию' },
    { value: 'work_seconds', label: 'По времени в работе' },
    { value: 'system', label: 'По системе отметки' },
];

const MUTE_KIND_LABELS = { all: 'Все уведомления', user: 'Сотрудник', dept: 'Отдел' };

/* Почему карточка Workpace осталась без нашего сотрудника. `excluded` — не промах
 * автоматики, а решение человека, поэтому и подпись другая. */
const UNLINKED_REASONS = {
    no_match: 'не нашли по ФИО',
    stale_link: 'привязан к тому, кого нет в отделе',
    excluded: 'отмечен как не наш сотрудник',
};

const TABS = [
    { key: 'attendance', label: 'Отметки', icon: Clock },
    { key: 'overview', label: 'Обзор', icon: Activity },
    { key: 'employees', label: 'Сотрудники', icon: Users },
    { key: 'events', label: 'Отбивки', icon: Bell },
    { key: 'reports', label: 'Отчёты', icon: FileSpreadsheet },
    { key: 'chats', label: 'Чаты', icon: MessageSquare },
    { key: 'departments', label: 'Отделы', icon: Building2 },
    { key: 'mutes', label: 'Тишина', icon: BellOff },
];

const EVENTS_PAGE = 60;

/* Колонки таблицы дисциплины. `numeric` — и выравнивание, и то, что по такой
 * колонке сортируем по убыванию с первого клика: интересны нарушители сверху.
 *
 * «Неявок» в исходный список не входила, но в таблице теперь весь состав отдела:
 * без неё строка из одних прочерков у того, кто вообще не выходил, читалась бы
 * как «вопросов нет». */
const EMPLOYEE_COLUMNS = [
    { key: 'employee_name', label: 'Сотрудник' },
    { key: 'city', label: 'Город', cityOnly: true },
    { key: 'late_count', label: 'Опозданий', numeric: true },
    { key: 'late_minutes', label: 'Минут опоздания', numeric: true },
    { key: 'early_out_minutes', label: 'Минут раннего ухода', numeric: true },
    { key: 'missing_count', label: 'Неявок', numeric: true },
    { key: 'suspicious_count', label: 'Подозрительных отметок', numeric: true },
];

const employeeViolations = (row) => (row.late_count || 0) + (row.early_out_count || 0)
    + (row.missing_count || 0) + (row.suspicious_count || 0);

const fmtInt = (value) => Number(value || 0).toLocaleString('ru-RU');

const fmtDateTime = (iso) => (iso
    ? new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '—');

const fmtTime = (iso) => (iso
    ? new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
    : '—');

/* Время в работе — уже за вычетом обеда, его считает сервер. Ноль показываем
 * прочерком: у человека без пары отметок «00:00» читалось бы как «не работал». */
const fmtWorked = (seconds) => {
    const total = Number(seconds || 0);
    if (total <= 0) return '—';
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
};

const fmtDay = (iso) => (iso
    ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
    : '—');

// «3 мин назад» — состояние опроса важнее точного времени
const fmtAgo = (iso) => {
    if (!iso) return 'никогда';
    const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (secs < 60) return 'только что';
    if (secs < 3600) return `${Math.floor(secs / 60)} мин назад`;
    if (secs < 86400) return `${Math.floor(secs / 3600)} ч назад`;
    return `${Math.floor(secs / 86400)} дн назад`;
};

const fmtSize = (bytes) => {
    if (!bytes) return '—';
    if (bytes < 1024) return `${bytes} Б`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
    return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
};

const fmtPeriod = (from, to) => (from === to ? fmtDay(from) : `${fmtDay(from)} — ${fmtDay(to)}`);

// created_by приходит как 'web:12:Имя' либо 'telegram:<id>'
const actorLabel = (raw) => {
    const value = String(raw || '').trim();
    if (!value) return '—';
    if (value.startsWith('web:')) {
        const parts = value.split(':');
        return parts[2] ? `${parts[2]} (сайт)` : 'сайт';
    }
    if (value.startsWith('telegram:')) return `Telegram ${value.slice(9)}`;
    return value;
};

const daysAgo = (n) => {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return isoDate(d);
};

const monthStart = () => {
    const d = new Date();
    d.setDate(1);
    return isoDate(d);
};

const pluralRu = (n, one, few, many) => {
    const value = Math.abs(Number(n) || 0);
    const tail = value % 10;
    const hundred = value % 100;
    if (tail === 1 && hundred !== 11) return one;
    if (tail >= 2 && tail <= 4 && (hundred < 12 || hundred > 14)) return few;
    return many;
};

// Дисциплину смотрят помесячно, поэтому «Весь период» из пресетов по умолчанию
// тут не нужен: за полгода истории таблица перестаёт читаться.
const EMPLOYEE_DATE_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: isoDate(new Date()), to: isoDate(new Date()) }) },
    { label: 'Этот месяц', range: () => ({ from: monthStart(), to: isoDate(new Date()) }) },
    { label: '30 дней', range: () => ({ from: daysAgo(29), to: isoDate(new Date()) }) },
];

/* Пустой город уходит вниз при любом направлении: прочерки в середине списка
 * ломают чтение. При равенстве сначала те, к кому есть вопросы, потом алфавит:
 * в таблице весь состав, и без этого чистые сотрудники перемешались бы с
 * нарушителями, у которых по выбранной колонке тоже ноль. */
const sortEmployees = (rows, { key, dir }) => {
    const sign = dir === 'asc' ? 1 : -1;
    const numeric = EMPLOYEE_COLUMNS.find((column) => column.key === key)?.numeric;
    return rows.slice().sort((a, b) => {
        if (numeric) {
            const diff = (a[key] || 0) - (b[key] || 0);
            if (diff) return diff * sign;
        } else {
            const left = String(a[key] || '');
            const right = String(b[key] || '');
            if (!left !== !right) return left ? -1 : 1;
            const diff = left.localeCompare(right, 'ru');
            if (diff) return diff * sign;
        }
        const byViolations = employeeViolations(b) - employeeViolations(a);
        if (byViolations) return byViolations;
        return String(a.employee_name || '').localeCompare(String(b.employee_name || ''), 'ru');
    });
};

const errText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;

const SegButton = ({ active, onClick, icon: Icon, children }) => (
    <button onClick={onClick}
            className={`flex items-center gap-1.5 rounded-[9px] px-3.5 py-1.5 text-[12.5px] font-semibold transition-all ${
                active ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                       : 'text-slate-500 hover:text-slate-700'}`}>
        <Icon size={13} /> {children}
    </button>
);

/* Поле фильтра с подписью: подписи держат строку фильтров ровной, а без них
 * непонятно, что означает выбранное значение. */
const FilterField = ({ label, children, className = '' }) => (
    <label className={`flex flex-col gap-1 ${className}`}>
        <span className="px-1 text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">
            {label}
        </span>
        {children}
    </label>
);

/* Плитка показателя. Число — главный элемент, подпись под ним; цвет берём
 * только под статус (норма / внимание / проблема), а не под «серию». */
const StatTile = ({ label, value, hint, tone = 'slate', icon: Icon = null }) => {
    const valueTone = {
        slate: 'text-slate-900', green: 'text-emerald-600',
        amber: 'text-amber-600', red: 'text-rose-600', blue: 'text-blue-600',
    }[tone] || 'text-slate-900';
    return (
        <div className={`${iosCard} px-4 py-3`}>
            <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                {Icon && <Icon size={12} />} {label}
            </div>
            <div className={`mt-1 text-[26px] font-semibold leading-none tabular-nums ${valueTone}`}>{value}</div>
            {hint && <div className="mt-1.5 text-[11.5px] leading-snug text-slate-500">{hint}</div>}
        </div>
    );
};

const EmptyBlock = ({ children, icon: Icon = Bell }) => (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-[13px] text-slate-400">
        <Icon size={20} className="text-slate-300" />
        {children}
    </div>
);

const LoadingBlock = () => (
    <div className="flex items-center justify-center gap-2 py-12 text-[13px] text-slate-400">
        <Loader2 size={15} className="animate-spin" /> Загрузка…
    </div>
);

const ErrorBlock = ({ children }) => (
    <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-rose-500">
        <AlertCircle size={15} /> {children}
    </div>
);

/* Динамика отбивок по дням: одна серия, один тон, значения — в подсказке.
 * Легенда не нужна (серия названа заголовком), подписан только пик. */
const DailyBars = ({ data }) => {
    const [hover, setHover] = useState(null);
    const max = Math.max(1, ...data.map((d) => d.count));
    if (!data.length) return <EmptyBlock icon={Activity}>За период отбивок не было</EmptyBlock>;
    return (
        <div className="relative">
            {hover && (
                <div className="pointer-events-none absolute -top-1 left-1/2 z-10 -translate-x-1/2 rounded-lg bg-slate-900/90 px-2.5 py-1.5 text-[11.5px] font-medium text-white shadow-lg backdrop-blur">
                    {fmtDay(hover.date)} · {fmtInt(hover.count)} нарушений
                </div>
            )}
            <div className="flex h-28 items-end gap-[3px] pt-6">
                {data.map((day) => {
                    const height = Math.max(2, Math.round((day.count / max) * 78));
                    const isPeak = day.count === max && max > 0;
                    return (
                        <div key={day.date}
                             onMouseEnter={() => setHover(day)}
                             onMouseLeave={() => setHover(null)}
                             className="group relative flex flex-1 cursor-default flex-col items-center justify-end"
                             aria-label={`${day.date}: нарушений ${day.count}`}>
                            {isPeak && (
                                <div className="mb-1 text-[10.5px] font-semibold tabular-nums text-slate-500">
                                    {fmtInt(day.count)}
                                </div>
                            )}
                            <div style={{ height }}
                                 className={`w-full rounded-t-[4px] transition-colors ${
                                     hover?.date === day.date ? 'bg-blue-600' : 'bg-blue-500/85'}`} />
                        </div>
                    );
                })}
            </div>
            <div className="mt-1.5 flex items-center justify-between border-t border-slate-200/70 pt-1.5 text-[10.5px] text-slate-400">
                <span>{fmtDay(data[0]?.date)}</span>
                <span>{fmtDay(data[data.length - 1]?.date)}</span>
            </div>
        </div>
    );
};

/* Заголовок сортируемой колонки. Стрелка только у активной: шесть серых стрелок
 * в шапке — шум, по которому непонятно, что сейчас работает. */
const SortHeader = ({ column, sort, onSort, className = '' }) => {
    const active = sort.key === column.key;
    const align = column.numeric ? 'justify-end text-right' : 'justify-start text-left';
    return (
        <th className={`px-3 py-2.5 ${column.numeric ? 'text-right' : 'text-left'} ${className}`}>
            <button type="button" onClick={() => onSort(column)}
                    className={`inline-flex w-full items-center gap-1 ${align} text-[11px] font-semibold uppercase tracking-wider transition ${
                        active ? 'text-slate-900' : 'text-slate-500 hover:text-slate-700'}`}>
                {column.label}
                {active && (sort.dir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
            </button>
        </th>
    );
};

const employeeNumber = (value) => (value
    ? <span className="font-medium text-slate-900">{fmtInt(value)}</span>
    : <span className="text-slate-300">—</span>);

/* Отдел в таблице дисциплины: шапка с итогами и раскрывающаяся таблица по всему
 * составу. Колонка «Город» есть только там, где город заполнен: его ведут
 * отделам, чьи офисы стоят в разных городах (в Workpace это «Регионы»,
 * у нас — «Фронт офисы»). Остальным пустая колонка не нужна. */
const EmployeeDepartmentCard = ({ department, sort, onSort, collapsed, onToggle,
                                 planInfo, onOpenPlanLinks }) => {
    const totals = department.totals || {};
    const columns = EMPLOYEE_COLUMNS.filter((column) => !column.cityOnly || department.has_city);
    const rows = useMemo(
        () => sortEmployees(department.employees || [], sort),
        [department.employees, sort],
    );

    return (
        <section className={`${iosCard} overflow-hidden`}>
            <button type="button" onClick={onToggle} aria-expanded={!collapsed}
                    className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-slate-50/70">
                <span className="flex min-w-0 items-center gap-2">
                    <ChevronDown size={15}
                                 className={`shrink-0 text-slate-400 transition-transform ${collapsed ? '-rotate-90' : ''}`} />
                    <span className="truncate text-[14px] font-semibold text-slate-900">
                        {department.department_name}
                    </span>
                    <span className="shrink-0 text-[12px] text-slate-400">
                        {fmtInt(totals.employees)} чел.
                        {totals.employees_with_violations > 0
                            && ` · с нарушениями ${fmtInt(totals.employees_with_violations)}`}
                    </span>
                </span>
                <span className="flex flex-wrap items-center gap-1.5">
                    {totals.late_count > 0 && (
                        <IosBadge tone="red" title="Опозданий за период">
                            <Clock size={11} /> {fmtInt(totals.late_count)} · {fmtInt(totals.late_minutes)} мин
                        </IosBadge>
                    )}
                    {totals.early_out_minutes > 0 && (
                        <IosBadge tone="amber" title="Ранних уходов за период">
                            <LogOut size={11} /> {fmtInt(totals.early_out_count)} · {fmtInt(totals.early_out_minutes)} мин
                        </IosBadge>
                    )}
                    {totals.missing_count > 0 && (
                        <IosBadge tone="red" title="Неявок за период">
                            <UserX size={11} /> {fmtInt(totals.missing_count)}
                        </IosBadge>
                    )}
                    {totals.suspicious_count > 0 && (
                        <IosBadge tone="amber" title="Подозрительных отметок за период">
                            <ShieldAlert size={11} /> {fmtInt(totals.suspicious_count)}
                        </IosBadge>
                    )}
                    {employeeViolations(totals) === 0 && (
                        <IosBadge tone="green">нарушений нет</IosBadge>
                    )}
                </span>
            </button>

            {!collapsed && (
                <div className="overflow-x-auto border-t border-slate-100">
                    <table className="w-full text-[13px]">
                        <thead className="bg-white/85 backdrop-blur-xl">
                            <tr className="border-b border-slate-200/70">
                                {columns.map((column) => (
                                    <SortHeader key={column.key} column={column} sort={sort} onSort={onSort}
                                                className={column.key === 'employee_name' ? 'pl-4' : ''} />
                                ))}
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                            {rows.map((row) => (
                                <tr key={row.workpace_name || row.employee_name}
                                    className="transition hover:bg-slate-50/80">
                                    <td className="py-2.5 pl-4 pr-3">
                                        <span className="font-medium text-slate-900"
                                              title={row.workpace_name && row.workpace_name !== row.employee_name
                                                  ? `В Workpace: ${row.workpace_name}` : undefined}>
                                            {row.employee_name}
                                        </span>
                                        {/* Не действующий оператор попал в таблицу только
                                            из-за нарушений за период — без пометки строка
                                            выглядела бы как обычная. */}
                                        {department.matched_department && !row.in_roster && (
                                            <IosBadge tone="slate" className="ml-1.5 align-middle"
                                                      title="Сейчас не действующий оператор отдела — в таблице из-за нарушений за период">
                                                вне состава
                                            </IosBadge>
                                        )}
                                    </td>
                                    {department.has_city && (
                                        <td className="px-3 py-2.5 text-slate-600">
                                            {row.city ? (
                                                <span className="inline-flex items-center gap-1">
                                                    <MapPin size={11} className="text-slate-400" /> {row.city}
                                                </span>
                                            ) : <span className="text-slate-300">—</span>}
                                        </td>
                                    )}
                                    <td className="px-3 py-2.5 text-right tabular-nums">{employeeNumber(row.late_count)}</td>
                                    <td className="px-3 py-2.5 text-right tabular-nums">{employeeNumber(row.late_minutes)}</td>
                                    <td className="px-3 py-2.5 text-right tabular-nums">{employeeNumber(row.early_out_minutes)}</td>
                                    <td className="px-3 py-2.5 text-right tabular-nums">{employeeNumber(row.missing_count)}</td>
                                    <td className="px-3 py-2.5 text-right tabular-nums">{employeeNumber(row.suspicious_count)}</td>
                                </tr>
                            ))}
                        </tbody>
                        <tfoot>
                            <tr className="border-t border-slate-200/70 bg-slate-50/60 text-[12.5px] font-semibold text-slate-700">
                                <td className="py-2.5 pl-4 pr-3">Итого по отделу</td>
                                {department.has_city && <td className="px-3 py-2.5" />}
                                <td className="px-3 py-2.5 text-right tabular-nums">{fmtInt(totals.late_count)}</td>
                                <td className="px-3 py-2.5 text-right tabular-nums">{fmtInt(totals.late_minutes)}</td>
                                <td className="px-3 py-2.5 text-right tabular-nums">{fmtInt(totals.early_out_minutes)}</td>
                                <td className="px-3 py-2.5 text-right tabular-nums">{fmtInt(totals.missing_count)}</td>
                                <td className="px-3 py-2.5 text-right tabular-nums">{fmtInt(totals.suspicious_count)}</td>
                            </tr>
                        </tfoot>
                    </table>
                    {department.foreign_employees > 0 && (
                        <div className="px-4 py-2.5 text-[11.5px] text-slate-500">
                            В Workpace в этом отделе есть ещё {fmtInt(department.foreign_employees)}{' '}
                            {pluralRu(department.foreign_employees, 'человек', 'человека', 'человек')} —
                            их нет в iCore, поэтому в таблицу они не попадают.
                        </div>
                    )}
                    {/* Источник плана показываем только там, где он не Workpace: у
                        остальных отделов это была бы строка, не несущая новости. */}
                    {planInfo && (
                        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-2.5">
                            <span className="text-[11.5px] text-slate-500">
                                {/* Цвет здесь несёт смысл: пустой график = контроль
                                    в отделе сейчас не работает, это не фон. */}
                                {planInfo.with_shifts === 0 ? (
                                    <span className="font-medium text-amber-600">
                                        График в iCore не проставлен — опоздания и неявки
                                        по этому отделу сейчас не проверяются.
                                    </span>
                                ) : (
                                    <>Опоздания и неявки считаются по графику iCore, отметки — из Workpace.</>
                                )}
                                {planInfo.without_card > 0 && (
                                    <> Без карточки Workpace {fmtInt(planInfo.without_card)}{' '}
                                        {pluralRu(planInfo.without_card, 'человек', 'человека', 'человек')} —
                                        их контролировать нечем.</>
                                )}
                                {planInfo.unlinked > 0 && (
                                    <> Карточек без сотрудника: {fmtInt(planInfo.unlinked)}.</>
                                )}
                            </span>
                            <button type="button" className={iosBtnSecondary} onClick={onOpenPlanLinks}>
                                <Link2 size={13} /> Сопоставление
                            </button>
                        </div>
                    )}
                </div>
            )}
        </section>
    );
};

/* Чипы отделов чата. Пусто = чат получает уведомления всех отделов. */
const DepartmentChips = ({ names, unknown = [] }) => {
    if (!names.length) {
        return <IosBadge tone="slate">все отделы</IosBadge>;
    }
    const unknownSet = new Set(unknown.map((n) => String(n).toLowerCase()));
    return (
        <div className="flex flex-wrap gap-1">
            {names.map((name) => (
                <IosBadge key={name} tone={unknownSet.has(String(name).toLowerCase()) ? 'amber' : 'blue'}
                          title={unknownSet.has(String(name).toLowerCase())
                              ? 'Такого отдела нет в Workpace — чат ничего не получает по этому фильтру'
                              : undefined}>
                    {name}
                </IosBadge>
            ))}
        </div>
    );
};

/* Выбор отделов: поиск + чекбоксы. Свои значения (отдел уже не в Workpace)
 * остаются в списке, чтобы правка чата их не стирала молча. */
const DepartmentPicker = ({ all, selected, onChange }) => {
    const [query, setQuery] = useState('');
    const options = useMemo(() => {
        const known = all.map((d) => d.name);
        const extra = selected.filter((name) => !known.some((k) => k.toLowerCase() === name.toLowerCase()));
        return [...extra, ...known];
    }, [all, selected]);
    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        return q ? options.filter((name) => name.toLowerCase().includes(q)) : options;
    }, [options, query]);

    const toggle = (name) => {
        const exists = selected.some((s) => s.toLowerCase() === name.toLowerCase());
        onChange(exists ? selected.filter((s) => s.toLowerCase() !== name.toLowerCase()) : [...selected, name]);
    };

    return (
        <div className="space-y-2">
            <div className="relative">
                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={query} onChange={(e) => setQuery(e.target.value)}
                       placeholder="Поиск отдела…" className={`${iosInput} py-2 pl-8 text-[13px]`} />
            </div>
            <div className="max-h-64 overflow-y-auto rounded-xl bg-slate-50 ring-1 ring-slate-200/70">
                {filtered.length === 0 && (
                    <div className="px-3 py-6 text-center text-[12.5px] text-slate-400">Ничего не найдено</div>
                )}
                {filtered.map((name) => {
                    const checked = selected.some((s) => s.toLowerCase() === name.toLowerCase());
                    return (
                        <button key={name} type="button" onClick={() => toggle(name)}
                                className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] transition hover:bg-white">
                            <span className={`grid h-[18px] w-[18px] shrink-0 place-items-center rounded-md transition ${
                                checked ? 'bg-blue-600 text-white' : 'bg-white ring-1 ring-slate-300'}`}>
                                {checked && <CheckCircle2 size={12} />}
                            </span>
                            <span className={checked ? 'font-medium text-slate-900' : 'text-slate-600'}>{name}</span>
                        </button>
                    );
                })}
            </div>
            <div className="px-1 text-[11px] text-slate-500">
                {selected.length ? `Выбрано отделов: ${selected.length}` : 'Ничего не выбрано — чат получает все отделы'}
            </div>
        </div>
    );
};

export default function GroupLateBotView({ apiBaseUrl, withAccessTokenHeader, showToast }) {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    const base = `${apiBaseUrl}/api/group_late_bot`;

    // «Отметки» — то, ради чего в раздел заходят чаще всего, поэтому они и открываются.
    const [tab, setTab] = useState('attendance');

    const [overview, setOverview] = useState(null);
    const [overviewError, setOverviewError] = useState(null);
    const [periodDays, setPeriodDays] = useState(7);
    /* Раздел бывает выдан главе отдела в границах одного отдела Workpace: бэкенд
     * (_group_late_bot_guard) режет и данные, и действия, а здесь мы убираем то,
     * чем в этих границах всё равно нельзя пользоваться, — общий опрос, выбор
     * чужого отдела, глобальная тишина. Название приходит со сводкой. */
    const [departmentScope, setDepartmentScope] = useState(null);

    const [chats, setChats] = useState(null);
    const [chatsError, setChatsError] = useState(null);
    const [availableChats, setAvailableChats] = useState([]);
    const [departments, setDepartments] = useState(null);
    const [mutes, setMutes] = useState(null);
    const [pollRuns, setPollRuns] = useState(null);

    const [events, setEvents] = useState(null);
    const [eventsTotal, setEventsTotal] = useState(0);
    const [eventsError, setEventsError] = useState(null);
    const [eventFilters, setEventFilters] = useState({
        from: daysAgo(6), to: isoDate(new Date()),
        type: '', chatId: '', department: '', q: '',
    });
    const [eventSearch, setEventSearch] = useState('');

    /* Вкладка «Отметки» — главная в разделе: кто когда пришёл и ушёл, сразу по
     * обоим источникам. Данные тянутся из Workpace и Clockster прямо в запросе,
     * поэтому окно короткое (бэкенд отдаёт не больше недели), а за месяц кадровик
     * берёт выгрузку со вкладки «Отчёты». */
    const [attendance, setAttendance] = useState(null);
    const [attendanceTotal, setAttendanceTotal] = useState(0);
    const [attendanceError, setAttendanceError] = useState(null);
    const [attendanceNotice, setAttendanceNotice] = useState(null);
    const [attendanceFilters, setAttendanceFilters] = useState({
        from: isoDate(new Date()), to: isoDate(new Date()),
        department: '', q: '', kind: '', sort: 'status',
    });
    const [attendanceSearch, setAttendanceSearch] = useState('');
    const [expandedMarks, setExpandedMarks] = useState(() => new Set());

    const [employees, setEmployees] = useState(null);
    const [employeesError, setEmployeesError] = useState(null);
    const [employeeFilters, setEmployeeFilters] = useState({
        from: monthStart(), to: isoDate(new Date()), department: '', q: '',
    });
    const [employeeSearch, setEmployeeSearch] = useState('');
    const [employeeSort, setEmployeeSort] = useState({ key: 'late_count', dir: 'desc' });
    const [collapsedDepartments, setCollapsedDepartments] = useState(() => new Set());

    const [planLinks, setPlanLinks] = useState(null);
    const [planLinksError, setPlanLinksError] = useState(null);
    const [planModalOpen, setPlanModalOpen] = useState(false);

    const [reports, setReports] = useState(null);
    const [reportsError, setReportsError] = useState(null);

    const [busy, setBusy] = useState('');           // ключ выполняющегося действия
    const [chatModal, setChatModal] = useState(null);   // {mode:'create'|'departments', chat}
    const [muteModal, setMuteModal] = useState(null);
    const [reportModal, setReportModal] = useState(null);

    const eventsRequest = useRef({ id: 0, controller: null });
    const employeesRequest = useRef({ id: 0, controller: null });
    const attendanceRequest = useRef({ id: 0, controller: null });
    const attendanceSearchDebounce = useRef(null);
    const searchDebounce = useRef(null);
    const employeeSearchDebounce = useRef(null);
    const reportPoll = useRef(null);

    const scoped = Boolean(departmentScope);
    /* {отдел Workpace: сколько людей без карточки и карточек без человека} —
       считаем один раз, а не в каждой карточке отдела. */
    const planInfoByDepartment = useMemo(() => {
        if (!planLinks?.departments?.length) return {};
        const info = {};
        for (const item of planLinks.departments) {
            info[item.workpace_name] = { without_card: 0, unlinked: 0, with_shifts: 0 };
        }
        for (const person of planLinks.people || []) {
            const entry = info[person.department_name];
            if (!entry) continue;
            if (!(person.cards || []).length) entry.without_card += 1;
            if (person.shifts_ahead > 0) entry.with_shifts += 1;
        }
        for (const card of planLinks.unlinked || []) {
            const entry = info[card.department_name];
            if (entry) entry.unlinked += 1;
        }
        return info;
    }, [planLinks]);
    const departmentNames = departments?.items || [];
    const unknownDepartments = useMemo(
        () => (departments?.unknown || []).map((d) => d.name),
        [departments],
    );

    /* ─── загрузка ─────────────────────────────────────────────────────── */

    const loadOverview = useCallback((days = periodDays) => {
        setOverviewError(null);
        axios.get(`${base}/overview`, { headers: headers(), params: { days } })
            .then((r) => {
                setOverview(r.data);
                setDepartmentScope(r.data?.department_scope || null);
            })
            .catch((e) => { setOverview(null); setOverviewError(errText(e, 'Не удалось загрузить сводку')); });
    }, [base, headers, periodDays]);

    const loadChats = useCallback(() => {
        setChatsError(null);
        axios.get(`${base}/chats`, { headers: headers(), params: { days: 7 } })
            .then((r) => setChats(r.data.items || []))
            .catch((e) => { setChats([]); setChatsError(errText(e, 'Не удалось загрузить чаты')); });
        // Группы, где бот уже есть, но рассылка не подключена — их предлагаем
        // в «Подключить чат», чтобы не искать chat_id руками.
        axios.get(`${base}/available_chats`, { headers: headers() })
            .then((r) => setAvailableChats(r.data.items || []))
            .catch(() => setAvailableChats([]));
    }, [base, headers]);

    const loadDepartments = useCallback(() => {
        axios.get(`${base}/departments`, { headers: headers() })
            .then((r) => setDepartments(r.data))
            .catch(() => setDepartments({ items: [], unknown: [] }));
    }, [base, headers]);

    const loadMutes = useCallback(() => {
        axios.get(`${base}/mutes`, { headers: headers() })
            .then((r) => setMutes(r.data.items || []))
            .catch(() => setMutes([]));
    }, [base, headers]);

    const loadPollRuns = useCallback(() => {
        axios.get(`${base}/poll_runs`, { headers: headers(), params: { limit: 12 } })
            .then((r) => setPollRuns(r.data.items || []))
            .catch(() => setPollRuns([]));
    }, [base, headers]);

    const loadEvents = useCallback((filters, { append = false } = {}) => {
        eventsRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = eventsRequest.current.id + 1;
        eventsRequest.current = { id: requestId, controller };
        if (!append) { setEvents(null); setEventsError(null); }
        axios.get(`${base}/events`, {
            headers: headers(), signal: controller.signal,
            params: {
                date_from: filters.from || undefined,
                date_to: filters.to || undefined,
                event_type: filters.type || undefined,
                chat_id: filters.chatId || undefined,
                department: filters.department || undefined,
                q: filters.q || undefined,
                limit: EVENTS_PAGE,
                offset: append ? (events?.length || 0) : 0,
            },
        }).then((r) => {
            if (requestId !== eventsRequest.current.id) return;
            setEventsTotal(r.data.total || 0);
            setEvents((prev) => (append ? [...(prev || []), ...(r.data.items || [])] : (r.data.items || [])));
        }).catch((e) => {
            if (axios.isCancel?.(e) || e.name === 'CanceledError') return;
            if (requestId !== eventsRequest.current.id) return;
            setEvents((prev) => prev || []);
            setEventsError(errText(e, 'Не удалось загрузить отбивки'));
        });
    }, [base, headers, events]);

    const loadAttendance = useCallback((filters) => {
        attendanceRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = attendanceRequest.current.id + 1;
        attendanceRequest.current = { id: requestId, controller };
        setAttendance(null);
        setAttendanceError(null);
        setAttendanceNotice(null);
        axios.get(`${base}/attendance`, {
            headers: headers(), signal: controller.signal,
            params: {
                date_start: filters.from || undefined,
                date_end: filters.to || undefined,
                department: filters.department || undefined,
                q: filters.q || undefined,
                kind: filters.kind || undefined,
                sort: filters.sort || undefined,
                limit: 500,
            },
        }).then((r) => {
            if (requestId !== attendanceRequest.current.id) return;
            setAttendance(r.data.rows || []);
            setAttendanceTotal(r.data.total || 0);
            // Второй источник мог отвалиться — таблица при этом рабочая, но
            // неполная, и молчать об этом нельзя: пропал бы целый офис.
            if (r.data.clockster_error) {
                setAttendanceNotice('Clockster сейчас недоступен — отметок центрального офиса в таблице нет');
            }
        }).catch((e) => {
            if (axios.isCancel?.(e) || e.name === 'CanceledError') return;
            if (requestId !== attendanceRequest.current.id) return;
            setAttendance([]);
            setAttendanceError(errText(e, 'Не удалось загрузить отметки'));
        });
    }, [base, headers]);

    const loadEmployees = useCallback((filters) => {
        employeesRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = employeesRequest.current.id + 1;
        employeesRequest.current = { id: requestId, controller };
        setEmployees(null);
        setEmployeesError(null);
        axios.get(`${base}/employees`, {
            headers: headers(), signal: controller.signal,
            params: {
                date_from: filters.from || undefined,
                date_to: filters.to || undefined,
                department: filters.department || undefined,
                q: filters.q || undefined,
            },
        }).then((r) => {
            if (requestId !== employeesRequest.current.id) return;
            setEmployees(r.data);
        }).catch((e) => {
            if (axios.isCancel?.(e) || e.name === 'CanceledError') return;
            if (requestId !== employeesRequest.current.id) return;
            setEmployees({ departments: [], totals: {} });
            setEmployeesError(errText(e, 'Не удалось загрузить сводку по сотрудникам'));
        });
    }, [base, headers]);

    /* Мост «карточка Workpace → наш сотрудник». Нужен только отделам, которые
       ведут график у себя; у остальных ответ приходит пустым и раздел молчит. */
    const loadPlanLinks = useCallback(() => {
        setPlanLinksError(null);
        axios.get(`${base}/plan_links`, { headers: headers() })
            .then((r) => setPlanLinks(r.data))
            .catch((e) => {
                setPlanLinks({ departments: [], people: [], unlinked: [] });
                setPlanLinksError(errText(e, 'Не удалось загрузить сопоставление'));
            });
    }, [base, headers]);

    const savePlanLink = useCallback((body) => {
        setBusy(`plan:${body.workpace_ext_id}`);
        axios.post(`${base}/plan_links`, body, { headers: headers() })
            .then(() => loadPlanLinks())
            .catch((e) => setPlanLinksError(errText(e, 'Не удалось сохранить привязку')))
            .finally(() => setBusy(''));
    }, [base, headers, loadPlanLinks]);

    const loadReports = useCallback(() => {
        setReportsError(null);
        axios.get(`${base}/reports`, { headers: headers(), params: { limit: 60 } })
            .then((r) => setReports(r.data.items || []))
            .catch((e) => { setReports([]); setReportsError(errText(e, 'Не удалось загрузить отчёты')); });
    }, [base, headers]);

    useEffect(() => {
        loadOverview();
        loadChats();
        loadDepartments();
        /* eslint-disable-next-line react-hooks/exhaustive-deps */
    }, [apiBaseUrl]);

    useEffect(() => {
        if (tab === 'overview') loadPollRuns();
        // id === 0 — лента ещё ни разу не грузилась. Переход на вкладку из
        // «Обзора» уже запускает загрузку со своим фильтром, второй запрос лишний.
        if (tab === 'attendance' && attendanceRequest.current.id === 0) loadAttendance(attendanceFilters);
        if (tab === 'events' && eventsRequest.current.id === 0) loadEvents(eventFilters);
        if (tab === 'employees' && employeesRequest.current.id === 0) loadEmployees(employeeFilters);
        if (tab === 'employees' && planLinks === null) loadPlanLinks();
        if (tab === 'reports' && reports === null) loadReports();
        if (tab === 'mutes' && mutes === null) loadMutes();
        /* eslint-disable-next-line react-hooks/exhaustive-deps */
    }, [tab]);

    // Отчёт считается в фоне на стороне бота: пока есть «формируется» — подтягиваем список
    useEffect(() => {
        const running = (reports || []).some((r) => r.status === 'running');
        clearInterval(reportPoll.current);
        if (running && tab === 'reports') {
            reportPoll.current = setInterval(loadReports, 5000);
        }
        return () => clearInterval(reportPoll.current);
    }, [reports, tab, loadReports]);

    useEffect(() => () => {
        clearTimeout(searchDebounce.current);
        clearTimeout(employeeSearchDebounce.current);
        clearInterval(reportPoll.current);
        eventsRequest.current.controller?.abort();
        employeesRequest.current.controller?.abort();
        clearTimeout(attendanceSearchDebounce.current);
        attendanceRequest.current.controller?.abort();
    }, []);

    /* ─── действия ─────────────────────────────────────────────────────── */

    const run = async (key, fn, successMessage) => {
        setBusy(key);
        try {
            const result = await fn();
            if (successMessage) showToast?.(successMessage, 'success');
            return result;
        } catch (error) {
            showToast?.(errText(error, 'Не удалось выполнить действие'), 'error');
            return null;
        } finally {
            setBusy('');
        }
    };

    const pollNow = () => run('poll', async () => {
        const r = await axios.post(`${base}/poll`, {}, { headers: headers() });
        loadOverview();
        loadPollRuns();
        return r.data;
    }, 'Опрос Workpace выполнен');

    const syncDepartments = () => run('sync', async () => {
        const r = await axios.post(`${base}/departments/sync`, {}, { headers: headers() });
        loadDepartments();
        loadOverview();
        return r.data;
    }, 'Справочник отделов обновлён');

    const testChat = (chatId) => run(`test:${chatId}`, async () => {
        const r = await axios.post(`${base}/chats/${chatId}/test`, {}, { headers: headers() });
        return r.data;
    }, 'Тестовое сообщение отправлено');

    const toggleChat = (chat) => run(`toggle:${chat.chat_id}`, async () => {
        await axios.patch(`${base}/chats/${chat.chat_id}`, { enabled: !chat.enabled }, { headers: headers() });
        loadChats();
    }, chat.enabled ? 'Чат выключен из рассылки' : 'Чат включён в рассылку');

    const deleteChat = (chat) => {
        if (!window.confirm(`Убрать чат ${chat.title || chat.chat_id} из рассылки? История отбивок сохранится.`)) return;
        run(`delete:${chat.chat_id}`, async () => {
            await axios.delete(`${base}/chats/${chat.chat_id}`, { headers: headers() });
            loadChats();
            loadOverview();
        }, 'Чат убран из рассылки');
    };

    const downloadReport = (report) => {
        run(`file:${report.id}`, async () => {
            const response = await axios.get(`${base}/reports/${report.id}/file`, {
                headers: headers(), responseType: 'blob',
            });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = report.file_name || `report_${report.id}.xlsx`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
        });
    };

    const applyAttendanceFilters = (patch) => {
        const next = { ...attendanceFilters, ...patch };
        setAttendanceFilters(next);
        loadAttendance(next);
    };

    const onAttendanceSearch = (value) => {
        setAttendanceSearch(value);
        clearTimeout(attendanceSearchDebounce.current);
        attendanceSearchDebounce.current = setTimeout(
            () => applyAttendanceFilters({ q: value.trim() }), 350);
    };

    const toggleMarks = (key) => setExpandedMarks((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key); else next.add(key);
        return next;
    });

    const applyEventFilters = (patch) => {
        const next = { ...eventFilters, ...patch };
        setEventFilters(next);
        loadEvents(next);
    };

    const onEventSearch = (value) => {
        setEventSearch(value);
        clearTimeout(searchDebounce.current);
        searchDebounce.current = setTimeout(() => applyEventFilters({ q: value.trim() }), 350);
    };

    // Период — не «фильтр», который сбрасывают: он всегда выбран.
    const activeEventFilters = [
        eventFilters.type, eventFilters.department, eventFilters.chatId, eventFilters.q,
    ].filter(Boolean).length;

    const resetEventFilters = () => {
        setEventSearch('');
        clearTimeout(searchDebounce.current);
        applyEventFilters({ type: '', department: '', chatId: '', q: '' });
    };

    const applyEmployeeFilters = (patch) => {
        const next = { ...employeeFilters, ...patch };
        setEmployeeFilters(next);
        loadEmployees(next);
    };

    const onEmployeeSearch = (value) => {
        setEmployeeSearch(value);
        clearTimeout(employeeSearchDebounce.current);
        employeeSearchDebounce.current = setTimeout(
            () => applyEmployeeFilters({ q: value.trim() }), 350,
        );
    };

    // Клик по той же колонке переключает направление, по новой — начинает с
    // «интересного»: у чисел это убывание, у ФИО и города — алфавит.
    const toggleEmployeeSort = (column) => setEmployeeSort((prev) => (
        prev.key === column.key
            ? { key: prev.key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
            : { key: column.key, dir: column.numeric ? 'desc' : 'asc' }
    ));

    const toggleDepartmentCard = (name) => setCollapsedDepartments((prev) => {
        const next = new Set(prev);
        if (next.has(name)) next.delete(name); else next.add(name);
        return next;
    });

    /* ─── вкладки ──────────────────────────────────────────────────────── */

    const totals = overview?.totals || {};
    const lastRun = overview?.last_poll_run || null;
    const pollStale = lastRun?.started_at
        ? (Date.now() - new Date(lastRun.started_at).getTime()) > 15 * 60 * 1000
        : true;

    const renderOverview = () => {
        if (overviewError) return <ErrorBlock>{overviewError}</ErrorBlock>;
        if (!overview) return <LoadingBlock />;
        return (
            <div className="space-y-3">
                {totals.chats_enabled === 0 && (
                    <div className={`${iosCard} flex items-start gap-2.5 border-l-4 border-l-amber-400 px-4 py-3`}>
                        <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-500" />
                        <div className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-slate-600">
                            Опрос смен идёт, но в рассылку не включён ни один чат — поэтому нарушения
                            не фиксируются и никуда не уходят. Так и задумано: иначе при подключении
                            первого чата туда посыпалась бы вся накопленная история.
                            {totals.chats_total > 0
                                ? ' Включите нужный чат тумблером на вкладке «Чаты».'
                                : ' Добавьте бота в рабочую группу — она появится на вкладке «Чаты».'}
                        </div>
                        <button onClick={() => setTab('chats')} className={`${iosBtnSecondary} shrink-0 py-1.5 text-[12.5px]`}>
                            <MessageSquare size={12} /> К чатам
                        </button>
                    </div>
                )}
                <div className={`${iosCard} flex flex-wrap items-center justify-between gap-3 px-4 py-3.5`}>
                    <div className="flex items-center gap-3">
                        <span className={`grid h-10 w-10 place-items-center rounded-full ${
                            !lastRun ? 'bg-slate-100 text-slate-400'
                                : lastRun.ok === false ? 'bg-rose-50 text-rose-500'
                                    : pollStale ? 'bg-amber-50 text-amber-500' : 'bg-emerald-50 text-emerald-500'}`}>
                            <Activity size={18} />
                        </span>
                        <div>
                            <div className="text-[14px] font-semibold text-slate-900">
                                {!lastRun ? 'Опрос ещё не запускался'
                                    : lastRun.ok === false ? 'Последний опрос завершился ошибкой'
                                        : pollStale ? 'Опрос давно не приходил' : 'Опрос Workpace идёт штатно'}
                            </div>
                            <div className="text-[12px] text-slate-500">
                                {lastRun ? (
                                    <>
                                        {fmtAgo(lastRun.started_at)} · смен получено {fmtInt(lastRun.fetched)} ·
                                        найдено {fmtInt(lastRun.events_found)} · отправлено {fmtInt(lastRun.sent)}
                                        {lastRun.error ? ` · ${lastRun.error}` : ''}
                                    </>
                                ) : 'Смены и отметки Workpace проверяются раз в 2 минуты'}
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {totals.poll_failures_24h > 0 && (
                            <IosBadge tone="red">
                                <AlertTriangle size={11} /> сбоев за сутки: {fmtInt(totals.poll_failures_24h)}
                            </IosBadge>
                        )}
                        <IosBadge tone="slate">опросов за сутки: {fmtInt(totals.poll_runs_24h)}</IosBadge>
                        {/* Опрос прогоняет всю компанию и рассылает найденное по всем чатам,
                            поэтому в границах отдела его не запускают вручную. */}
                        {!scoped && (
                            <button onClick={pollNow} disabled={busy === 'poll'} className={iosBtnSecondary}>
                                {busy === 'poll' ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
                                Опросить сейчас
                            </button>
                        )}
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
                    <StatTile label="Чатов в рассылке" value={fmtInt(totals.chats_total)} icon={MessageSquare}
                              hint={`${fmtInt(totals.chats_with_filter)} с фильтром по отделам`} />
                    <StatTile label={`Нарушений за ${overview.days} дн.`} value={fmtInt(totals.events_period)} icon={Bell}
                              hint={`отделов в справочнике: ${fmtInt(totals.departments_total)}`} />
                    <StatTile label="Отчётов" value={fmtInt(totals.reports_period)} icon={FileSpreadsheet}
                              tone={totals.reports_failed > 0 ? 'amber' : 'slate'}
                              hint={totals.reports_failed > 0 ? `${fmtInt(totals.reports_failed)} с ошибкой` : 'все сформированы'} />
                    <StatTile label="Правил тишины" value={fmtInt(totals.mutes_total)} icon={BellOff}
                              hint={`${fmtInt(totals.mutes_global)} глобальных`} />
                </div>

                <div className="grid gap-3 lg:grid-cols-5">
                    <section className={`${iosCard} p-4 lg:col-span-3`}>
                        <div className="mb-1 flex items-center justify-between">
                            <div className={iosGroupLabel}>Нарушения по дням</div>
                            <div className="text-[11px] text-slate-400">за {overview.days} дн.</div>
                        </div>
                        <DailyBars data={overview.by_day || []} />
                    </section>

                    <section className={`${iosCard} p-4 lg:col-span-2`}>
                        <div className={`${iosGroupLabel} mb-2.5`}>По типам нарушений</div>
                        <div className="space-y-1.5">
                            {(overview.by_type || []).length === 0 && (
                                <div className="py-6 text-center text-[12.5px] text-slate-400">Нарушений не было</div>
                            )}
                            {(overview.by_type || [])
                                .slice()
                                .sort((a, b) => b.count - a.count)
                                .map((row) => {
                                    const meta = eventMeta(row.event_type);
                                    const Icon = meta.icon;
                                    return (
                                        <button key={row.event_type}
                                                onClick={() => { setTab('events'); applyEventFilters({ type: row.event_type }); }}
                                                className="flex w-full items-center justify-between gap-2 rounded-xl px-2 py-1.5 text-left transition hover:bg-slate-50">
                                            <span className="flex items-center gap-2 text-[13px] text-slate-700">
                                                <Icon size={13} className="text-slate-400" /> {meta.label}
                                            </span>
                                            <span className="text-[12.5px] font-semibold tabular-nums text-slate-900">
                                                {fmtInt(row.count)}
                                            </span>
                                        </button>
                                    );
                                })}
                        </div>
                    </section>
                </div>

                <div className="grid gap-3 lg:grid-cols-2">
                    <section className={`${iosCard} p-4`}>
                        <div className={`${iosGroupLabel} mb-2.5`}>Отделы с нарушениями</div>
                        {(overview.by_department || []).length === 0 ? (
                            <div className="py-6 text-center text-[12.5px] text-slate-400">Нарушений не было</div>
                        ) : (
                            <div className="space-y-1.5">
                                {overview.by_department.map((row) => (
                                    <button key={row.department_name}
                                            onClick={() => { setTab('events'); applyEventFilters({ department: row.department_name }); }}
                                            className="flex w-full items-center justify-between gap-3 rounded-xl px-2 py-1.5 text-left transition hover:bg-slate-50">
                                        <span className="truncate text-[13px] text-slate-700">{row.department_name}</span>
                                        <span className="shrink-0 text-[12.5px] font-semibold tabular-nums text-slate-900">
                                            {fmtInt(row.count)}
                                        </span>
                                    </button>
                                ))}
                            </div>
                        )}
                    </section>

                    <section className={`${iosCard} p-4`}>
                        <div className={`${iosGroupLabel} mb-2.5`}>Последние опросы Workpace</div>
                        {pollRuns === null ? <LoadingBlock /> : pollRuns.length === 0 ? (
                            <div className="py-6 text-center text-[12.5px] text-slate-400">Запусков пока не было</div>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full text-[12.5px]">
                                    <thead>
                                        <tr className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                            <th className="py-1.5 text-left">Время</th>
                                            <th className="py-1.5 text-right">Смен</th>
                                            <th className="py-1.5 text-right">Найдено</th>
                                            <th className="py-1.5 text-right">Отправлено</th>
                                            <th className="py-1.5 text-right">Длительность</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                        {pollRuns.map((row) => (
                                            <tr key={row.id} className={row.ok === false ? 'text-rose-500' : 'text-slate-600'}>
                                                <td className="py-1.5" title={row.error || ''}>
                                                    {fmtDateTime(row.started_at)}
                                                    {row.ok === false && <AlertCircle size={11} className="ml-1 inline" />}
                                                </td>
                                                <td className="py-1.5 text-right tabular-nums">{fmtInt(row.fetched)}</td>
                                                <td className="py-1.5 text-right tabular-nums">{fmtInt(row.events_found)}</td>
                                                <td className="py-1.5 text-right tabular-nums">{fmtInt(row.sent)}</td>
                                                <td className="py-1.5 text-right tabular-nums text-slate-400">
                                                    {row.duration_ms ? `${(row.duration_ms / 1000).toFixed(1)} с` : '—'}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </section>
                </div>
            </div>
        );
    };

    const renderAttendance = () => (
        <div className="space-y-3">
            <div className={`${iosCard} p-3`}>
                <div className="flex flex-wrap items-end gap-2.5">
                    <FilterField label="Период">
                        <IosDateRangePicker from={attendanceFilters.from} to={attendanceFilters.to}
                                            max={isoDate(new Date())}
                                            onChange={({ from, to }) => applyAttendanceFilters({ from, to })} />
                    </FilterField>
                    <FilterField label="Отметка" className="w-[170px]">
                        <CustomSelect
                            variant="ios"
                            value={attendanceFilters.kind}
                            onChange={(kind) => applyAttendanceFilters({ kind })}
                            options={[
                                { value: '', label: 'Приход и уход' },
                                { value: 'in', label: 'Только приход' },
                                { value: 'out', label: 'Только уход' },
                                { value: 'absent', label: 'Без отметки' },
                            ]}
                            ariaLabel="Отметка"
                        />
                    </FilterField>
                    {!scoped && (
                        <FilterField label="Подразделение" className="w-[210px]">
                            <CustomSelect
                                variant="ios"
                                searchable
                                value={attendanceFilters.department}
                                onChange={(department) => applyAttendanceFilters({ department })}
                                options={[
                                    { value: '', label: 'Все подразделения' },
                                    ...departmentNames.map((dept) => ({ value: dept.name, label: dept.name })),
                                ]}
                                searchPlaceholder="Поиск подразделения…"
                                ariaLabel="Подразделение"
                            />
                        </FilterField>
                    )}
                    <FilterField label="Сортировка" className="w-[190px]">
                        <CustomSelect
                            variant="ios"
                            value={attendanceFilters.sort}
                            onChange={(sort) => applyAttendanceFilters({ sort })}
                            options={ATTENDANCE_SORTS}
                            ariaLabel="Сортировка"
                        />
                    </FilterField>
                    <FilterField label="Поиск" className="flex-1 min-w-[220px]">
                        <div className="relative">
                            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                            <input
                                className={`${iosInput} pl-9`}
                                value={attendanceSearch}
                                onChange={(e) => onAttendanceSearch(e.target.value)}
                                placeholder="ФИО или должность"
                            />
                        </div>
                    </FilterField>
                </div>
            </div>

            {attendanceNotice && (
                <div className={`${iosCard} flex items-center gap-2 p-3 text-sm text-amber-800`}>
                    <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" />
                    {attendanceNotice}
                </div>
            )}
            {attendanceError && (
                <div className={`${iosCard} flex items-center gap-2 p-3 text-sm text-rose-700`}>
                    <AlertCircle className="h-4 w-4 shrink-0" />
                    {attendanceError}
                </div>
            )}

            {attendance === null && (
                <div className={`${iosCard} flex items-center justify-center gap-2 p-8 text-sm text-slate-500`}>
                    <Loader2 className="h-4 w-4 animate-spin" /> Загружаем отметки…
                </div>
            )}

            {attendance !== null && attendance.length === 0 && !attendanceError && (
                <div className={`${iosCard} p-8 text-center text-sm text-slate-500`}>
                    За выбранный период отметок нет
                </div>
            )}

            {attendance !== null && attendance.length > 0 && (
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="flex items-center justify-between px-4 py-2.5 text-xs text-slate-500">
                        <span>Показано {fmtInt(attendance.length)} из {fmtInt(attendanceTotal)}</span>
                        <span className="tabular-nums">{attendanceFilters.from === attendanceFilters.to
                            ? attendanceFilters.from
                            : `${attendanceFilters.from} — ${attendanceFilters.to}`}</span>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[1040px] text-sm">
                            <thead>
                                <tr className="border-y border-slate-200/70 bg-slate-50/70 text-xs text-slate-500">
                                    <th className="px-4 py-2 text-left font-medium">Сотрудник</th>
                                    <th className="px-3 py-2 text-left font-medium">Подразделение</th>
                                    <th className="px-3 py-2 text-left font-medium">График</th>
                                    <th className="px-3 py-2 text-left font-medium">Система</th>
                                    <th className="px-3 py-2 text-center font-medium">Приход</th>
                                    <th className="px-3 py-2 text-center font-medium">Уход</th>
                                    <th className="px-3 py-2 text-right font-medium">Опоздание</th>
                                    <th className="px-3 py-2 text-right font-medium">В работе</th>
                                    <th className="px-3 py-2 text-left font-medium">Статус</th>
                                </tr>
                            </thead>
                            <tbody>
                                {attendance.map((row, index) => {
                                    const key = `${row.date}:${row.employee_id}:${index}`;
                                    const open = expandedMarks.has(key);
                                    const tone = ATTENDANCE_STATUS_TONES[row.status] || 'slate';
                                    return (
                                        <React.Fragment key={key}>
                                            <tr className="border-b border-slate-100 last:border-0 align-top">
                                                <td className="px-4 py-2.5">
                                                    <div className="font-medium text-slate-900">{row.employee || '—'}</div>
                                                    <div className="text-xs text-slate-500">
                                                        {row.position || 'должность не указана'}
                                                        {attendanceFilters.from !== attendanceFilters.to && ` · ${row.date}`}
                                                    </div>
                                                </td>
                                                <td className="px-3 py-2.5 text-slate-700">
                                                    <div>{row.department || '—'}</div>
                                                    {row.location && (
                                                        <div className="flex items-center gap-1 text-xs text-slate-500">
                                                            <MapPin className="h-3 w-3" />{row.location}
                                                        </div>
                                                    )}
                                                </td>
                                                <td className="px-3 py-2.5 text-slate-700">{row.schedule || '—'}</td>
                                                <td className="px-3 py-2.5 text-slate-600">{row.system_label}</td>
                                                <td className="px-3 py-2.5 text-center tabular-nums">
                                                    <div className="text-slate-900">{fmtTime(row.fact_in)}</div>
                                                    <div className="text-xs text-slate-400">{fmtTime(row.plan_in)}</div>
                                                </td>
                                                <td className="px-3 py-2.5 text-center tabular-nums">
                                                    <div className="text-slate-900">{fmtTime(row.fact_out)}</div>
                                                    <div className="text-xs text-slate-400">{fmtTime(row.plan_out)}</div>
                                                </td>
                                                {/* Ноль не красим и не печатаем: цвет только там, где он
                                                    что-то значит, иначе таблица рябит. */}
                                                <td className={`px-3 py-2.5 text-right tabular-nums ${row.late_minutes > 0 ? 'font-medium text-rose-600' : 'text-slate-400'}`}>
                                                    {row.late_minutes > 0 ? `${fmtInt(row.late_minutes)} м` : '—'}
                                                </td>
                                                <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
                                                    {fmtWorked(row.work_seconds)}
                                                </td>
                                                <td className="px-3 py-2.5">
                                                    <div className="flex items-center gap-2">
                                                        <IosBadge tone={tone}>{row.status_label}</IosBadge>
                                                        {(row.marks || []).length > 0 && (
                                                            <button type="button" onClick={() => toggleMarks(key)}
                                                                    className={iosBtnGhost}
                                                                    title="Все отметки за день">
                                                                <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
                                                            </button>
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                            {open && (
                                                <tr className="border-b border-slate-100 bg-slate-50/60">
                                                    <td colSpan={9} className="px-4 py-2.5">
                                                        <div className="flex flex-wrap gap-1.5">
                                                            {(row.marks || []).map((mark, i) => (
                                                                <span key={i}
                                                                      className="inline-flex items-center gap-1 rounded-lg bg-white px-2 py-1 text-xs text-slate-600 ring-1 ring-slate-200/70">
                                                                    {mark.kind === 'in'
                                                                        ? <ArrowDown className="h-3 w-3 text-emerald-600" />
                                                                        : <ArrowUp className="h-3 w-3 text-slate-400" />}
                                                                    <span className="tabular-nums">{fmtTime(mark.at)}</span>
                                                                    {mark.suspicious && (
                                                                        <ShieldAlert className="h-3 w-3 text-amber-500" title="Терминал не подтвердил отметку" />
                                                                    )}
                                                                </span>
                                                            ))}
                                                        </div>
                                                    </td>
                                                </tr>
                                            )}
                                        </React.Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );

    const renderEvents = () => (
        <div className="space-y-3">
            <div className={`${iosCard} p-3`}>
                <div className="flex flex-wrap items-end gap-2.5">
                    <FilterField label="Период">
                        <IosDateRangePicker from={eventFilters.from} to={eventFilters.to} max={isoDate(new Date())}
                                            onChange={({ from, to }) => applyEventFilters({ from, to })} />
                    </FilterField>
                    <FilterField label="Тип нарушения" className="w-[190px]">
                        <CustomSelect
                            variant="ios"
                            value={eventFilters.type}
                            onChange={(type) => applyEventFilters({ type })}
                            options={[
                                { value: '', label: 'Все типы' },
                                ...Object.entries(EVENT_TYPES).map(([key, meta]) => ({
                                    value: key, label: meta.label,
                                })),
                            ]}
                            ariaLabel="Тип нарушения"
                        />
                    </FilterField>
                    {/* В границах отдела фильтровать нечего: лента и так только своя. */}
                    {!scoped && (
                        <FilterField label="Отдел" className="w-[190px]">
                            <CustomSelect
                                variant="ios"
                                searchable
                                value={eventFilters.department}
                                onChange={(department) => applyEventFilters({ department })}
                                options={[
                                    { value: '', label: 'Все отделы' },
                                    ...departmentNames.map((dept) => ({ value: dept.name, label: dept.name })),
                                    // Отдел из истории, которого уже нет в Workpace, иначе исчез бы
                                    // из фильтра вместе с самим справочником.
                                    ...(eventFilters.department
                                        && !departmentNames.some((dept) => dept.name === eventFilters.department)
                                        ? [{ value: eventFilters.department, label: eventFilters.department }]
                                        : []),
                                ]}
                                searchPlaceholder="Поиск отдела…"
                                ariaLabel="Отдел"
                            />
                        </FilterField>
                    )}
                    <FilterField label="Чат" className="w-[190px]">
                        <CustomSelect
                            variant="ios"
                            searchable={(chats || []).length > 7}
                            value={eventFilters.chatId}
                            onChange={(chatId) => applyEventFilters({ chatId })}
                            options={[
                                { value: '', label: 'Любой чат' },
                                ...(chats || []).map((chat) => ({
                                    value: chat.chat_id, label: chat.title || chat.chat_id,
                                })),
                            ]}
                            searchPlaceholder="Поиск чата…"
                            ariaLabel="Чат"
                        />
                    </FilterField>
                    <FilterField label="Поиск" className="min-w-[200px] flex-1">
                        <div className="relative">
                            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input value={eventSearch} onChange={(e) => onEventSearch(e.target.value)}
                                   placeholder="Сотрудник или отдел…"
                                   className={`${iosInput} py-2 pl-8 text-[13px]`} />
                        </div>
                    </FilterField>
                    <div className="flex items-center gap-1 pb-0.5">
                        {activeEventFilters > 0 && (
                            <button onClick={resetEventFilters} className={iosBtnGhost}>
                                <X size={13} /> Сбросить{activeEventFilters > 1 ? ` (${activeEventFilters})` : ''}
                            </button>
                        )}
                        <button onClick={() => loadEvents(eventFilters)} className={iosBtnGhost}>
                            <RefreshCw size={13} /> Обновить
                        </button>
                    </div>
                </div>
            </div>

            <div className={`${iosCard} overflow-hidden`}>
                {eventsError ? <ErrorBlock>{eventsError}</ErrorBlock>
                    : events === null ? <LoadingBlock />
                        : events.length === 0 ? <EmptyBlock>За выбранный период отбивок нет</EmptyBlock>
                            : (
                                <div className="divide-y divide-slate-100">
                                    {events.map((event) => {
                                        const meta = eventMeta(event.event_type);
                                        const Icon = meta.icon;
                                        const deliveries = event.deliveries || [];
                                        return (
                                            <div key={event.id} className="flex flex-wrap items-start gap-3 px-4 py-3 transition hover:bg-slate-50/70">
                                                <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${
                                                    meta.tone === 'red' ? 'bg-rose-50 text-rose-500'
                                                        : meta.tone === 'amber' ? 'bg-amber-50 text-amber-500'
                                                            : 'bg-blue-50 text-blue-500'}`}>
                                                    <Icon size={15} />
                                                </span>
                                                <div className="min-w-[220px] flex-1">
                                                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                                        <span className="text-[13.5px] font-semibold text-slate-900">
                                                            {event.employee_name || '—'}
                                                        </span>
                                                        <IosBadge tone={meta.tone}>{meta.label}</IosBadge>
                                                        {event.minutes ? (
                                                            <span className="text-[12.5px] font-medium tabular-nums text-slate-500">
                                                                {fmtInt(event.minutes)} мин
                                                            </span>
                                                        ) : null}
                                                    </div>
                                                    <div className="mt-0.5 text-[12px] text-slate-500">
                                                        {event.department_name || 'Без отдела'}
                                                        {event.schedule_name ? ` · ${event.schedule_name}` : ''}
                                                        {' · '}план {fmtTime(event.plan_at)}
                                                        {event.fact_at ? ` · факт ${fmtTime(event.fact_at)}` : ' · отметки нет'}
                                                        {event.location ? ` · ${event.location}` : ''}
                                                    </div>
                                                    <div className="mt-1 flex flex-wrap items-center gap-1">
                                                        <span className="text-[11px] text-slate-400">Ушло в:</span>
                                                        {deliveries.length === 0 && (
                                                            <IosBadge tone="amber">никуда не отправлено</IosBadge>
                                                        )}
                                                        {deliveries.map((d) => (
                                                            <IosBadge key={d.chat_id} tone={d.error ? 'red' : 'slate'}
                                                                      title={d.error || `Отправлено ${fmtDateTime(d.sent_at)}`}>
                                                                {d.chat_title || d.chat_id}
                                                            </IosBadge>
                                                        ))}
                                                    </div>
                                                </div>
                                                <div className="shrink-0 text-right text-[11.5px] text-slate-400">
                                                    найдено {fmtDateTime(event.detected_at)}
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
            </div>

            {events && events.length < eventsTotal && (
                <div className="flex justify-center">
                    <button onClick={() => loadEvents(eventFilters, { append: true })} className={iosBtnSecondary}>
                        <ChevronDown size={13} /> Показать ещё ({fmtInt(eventsTotal - events.length)})
                    </button>
                </div>
            )}
            {events && events.length > 0 && (
                <div className="px-1 text-[11px] text-slate-500">
                    Показано {fmtInt(events.length)} из {fmtInt(eventsTotal)}. Каждое нарушение попадает в чаты
                    один раз: повторных уведомлений по тому же сотруднику и типу за день не будет.
                </div>
            )}
        </div>
    );

    /* Дисциплина по всему составу отдела: кто и на сколько опаздывал, уходил
     * раньше, не выходил и сколько раз отметился подозрительно. Состав — из кэша
     * Workpace, нарушения — та же история отбивок, что во вкладке «Отбивки»,
     * поэтому цифры сходятся с «Обзором». */
    const renderEmployees = () => {
        const departments = employees?.departments || [];
        const totals = employees?.totals || {};
        const activeFilters = [employeeFilters.department, employeeFilters.q].filter(Boolean).length;
        const allCollapsed = departments.length > 0
            && departments.every((d) => collapsedDepartments.has(d.department_name));
        // Кэш состава наполняет опрос Workpace. Пока он не прошёл, в таблице
        // окажутся только нарушители — молчать об этом нельзя.
        const rosterMissing = employees && !employeesError && !employees.roster_total;

        return (
            <div className="space-y-3">
                <div className={`${iosCard} p-3`}>
                    <div className="flex flex-wrap items-end gap-2.5">
                        <FilterField label="Период">
                            <IosDateRangePicker from={employeeFilters.from} to={employeeFilters.to}
                                                max={isoDate(new Date())} presets={EMPLOYEE_DATE_PRESETS}
                                                onChange={({ from, to }) => applyEmployeeFilters({ from, to })} />
                        </FilterField>
                        {!scoped && (
                            <FilterField label="Отдел" className="w-[210px]">
                                <CustomSelect
                                    variant="ios"
                                    searchable
                                    value={employeeFilters.department}
                                    onChange={(department) => applyEmployeeFilters({ department })}
                                    options={[
                                        { value: '', label: 'Все отделы' },
                                        ...departmentNames.map((dept) => ({ value: dept.name, label: dept.name })),
                                        ...(employeeFilters.department
                                            && !departmentNames.some((dept) => dept.name === employeeFilters.department)
                                            ? [{ value: employeeFilters.department, label: employeeFilters.department }]
                                            : []),
                                    ]}
                                    searchPlaceholder="Поиск отдела…"
                                    ariaLabel="Отдел"
                                />
                            </FilterField>
                        )}
                        <FilterField label="Поиск" className="min-w-[200px] flex-1">
                            <div className="relative">
                                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input value={employeeSearch} onChange={(e) => onEmployeeSearch(e.target.value)}
                                       placeholder="Сотрудник, отдел или город…"
                                       className={`${iosInput} py-2 pl-8 text-[13px]`} />
                            </div>
                        </FilterField>
                        <div className="flex items-center gap-1 pb-0.5">
                            {activeFilters > 0 && (
                                <button onClick={() => {
                                    setEmployeeSearch('');
                                    clearTimeout(employeeSearchDebounce.current);
                                    applyEmployeeFilters({ department: '', q: '' });
                                }} className={iosBtnGhost}>
                                    <X size={13} /> Сбросить{activeFilters > 1 ? ` (${activeFilters})` : ''}
                                </button>
                            )}
                            {departments.length > 1 && (
                                <button onClick={() => setCollapsedDepartments(allCollapsed
                                    ? new Set()
                                    : new Set(departments.map((d) => d.department_name)))}
                                        className={iosBtnGhost}>
                                    <ChevronDown size={13} className={allCollapsed ? '-rotate-90' : ''} />
                                    {allCollapsed ? 'Развернуть все' : 'Свернуть все'}
                                </button>
                            )}
                            <button onClick={() => loadEmployees(employeeFilters)} className={iosBtnGhost}>
                                <RefreshCw size={13} /> Обновить
                            </button>
                        </div>
                    </div>
                </div>

                {rosterMissing && (
                    <div className={`${iosCard} flex items-start gap-2.5 border-l-4 border-l-amber-400 px-4 py-3`}>
                        <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-500" />
                        <div className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-slate-600">
                            Состав отделов ещё не приезжал из Workpace, поэтому в таблице только те,
                            у кого за период были нарушения. Он подтянется с ближайшим опросом —
                            или нажмите «Обновить из Workpace» на вкладке «Отделы».
                        </div>
                        <button onClick={() => setTab('departments')} className={`${iosBtnSecondary} shrink-0 py-1.5 text-[12.5px]`}>
                            <Building2 size={12} /> К отделам
                        </button>
                    </div>
                )}

                {employees && !employeesError && departments.length > 0 && (
                    <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
                        <StatTile label="Сотрудников" value={fmtInt(totals.employees)} icon={Users}
                                  hint={`с нарушениями: ${fmtInt(totals.employees_with_violations)} · отделов: ${fmtInt(totals.departments)}`} />
                        <StatTile label="Опозданий" value={fmtInt(totals.late_count)} icon={Clock}
                                  tone={totals.late_count > 0 ? 'red' : 'slate'}
                                  hint={`${fmtInt(totals.late_minutes)} мин суммарно`} />
                        <StatTile label="Ранних уходов" value={fmtInt(totals.early_out_count)} icon={LogOut}
                                  tone={totals.early_out_count > 0 ? 'amber' : 'slate'}
                                  hint={`${fmtInt(totals.early_out_minutes)} мин суммарно`} />
                        <StatTile label="Неявок" value={fmtInt(totals.missing_count)} icon={UserX}
                                  tone={totals.missing_count > 0 ? 'red' : 'slate'}
                                  hint={`подозрительных отметок: ${fmtInt(totals.suspicious_count)}`} />
                    </div>
                )}

                {employeesError ? <div className={iosCard}><ErrorBlock>{employeesError}</ErrorBlock></div>
                    : employees === null ? <div className={iosCard}><LoadingBlock /></div>
                        : departments.length === 0 ? (
                            <div className={iosCard}>
                                <EmptyBlock icon={Users}>
                                    {activeFilters > 0
                                        ? 'Под фильтры никто не подходит'
                                        : 'Состав отделов пуст и нарушений за период не было'}
                                </EmptyBlock>
                            </div>
                        ) : (
                            <div className="space-y-2.5">
                                {departments.map((department) => (
                                    <EmployeeDepartmentCard
                                        key={department.department_name}
                                        department={department}
                                        sort={employeeSort}
                                        onSort={toggleEmployeeSort}
                                        collapsed={collapsedDepartments.has(department.department_name)}
                                        onToggle={() => toggleDepartmentCard(department.department_name)}
                                        planInfo={planInfoByDepartment[department.department_name]}
                                        onOpenPlanLinks={() => setPlanModalOpen(true)}
                                    />
                                ))}
                            </div>
                        )}

                <p className="px-1 text-[11px] leading-relaxed text-slate-500">
                    В таблице весь состав отдела, а не только нарушители: пустая строка значит,
                    что вопросов к сотруднику за период нет. Там, где отдел Workpace сопоставлен
                    с нашим («Регионы» — «Фронт офисы»), состав берём из iCore — действующих
                    операторов отдела, с их ФИО и городом; сотрудников Workpace, которых в iCore
                    нет, в таблице не показываем. У остальных отделов пары с iCore нет, поэтому
                    состав и написание там как в Workpace{employees?.roster_synced_at
                        ? ` (обновлён ${fmtAgo(employees.roster_synced_at)})` : ''}. Нарушения —
                    из тех же отбивок, что во вкладке «Отбивки», по дате смены: опоздание и
                    ранний уход — от порога бота, подозрительная отметка — та, что терминал не
                    подтвердил. Поздний уход и отсутствие отметки об уходе в таблицу не выносим —
                    они остались в «Отбивках».
                </p>
            </div>
        );
    };

    const renderReports = () => (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-[12.5px] text-slate-500">
                    Все отчёты, которые формировал бот, — и по команде <code className="rounded bg-slate-100 px-1">/report</code> в чате, и с сайта
                </div>
                <div className="flex items-center gap-2">
                    <button onClick={loadReports} className={iosBtnGhost}>
                        <RefreshCw size={13} /> Обновить
                    </button>
                    <button onClick={() => setReportModal({
                        from: isoDate(new Date()), to: isoDate(new Date()), department: '', chatId: '',
                    })} className={iosBtnPrimary}>
                        <Plus size={13} /> Сформировать отчёт
                    </button>
                </div>
            </div>

            <div className={`${iosCard} overflow-hidden`}>
                {reportsError ? <ErrorBlock>{reportsError}</ErrorBlock>
                    : reports === null ? <LoadingBlock />
                        : reports.length === 0 ? <EmptyBlock icon={FileSpreadsheet}>Отчётов пока не было</EmptyBlock>
                            : (
                                <div className="overflow-x-auto">
                                    <table className="w-full text-[13px]">
                                        <thead className="bg-white/85 backdrop-blur-xl">
                                            <tr className="border-b border-slate-200/70 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                                <th className="px-4 py-2.5 text-left">Сформирован</th>
                                                <th className="px-3 py-2.5 text-left">Период</th>
                                                <th className="px-3 py-2.5 text-left">Отдел</th>
                                                <th className="px-3 py-2.5 text-left">Инициатор</th>
                                                <th className="px-3 py-2.5 text-right">Строк</th>
                                                <th className="px-3 py-2.5 text-right">Опозданий</th>
                                                <th className="px-3 py-2.5 text-right">Неявок</th>
                                                <th className="px-3 py-2.5 text-left">Статус</th>
                                                <th className="px-3 py-2.5" />
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100">
                                            {reports.map((report) => (
                                                <tr key={report.id} className="transition hover:bg-slate-50/80">
                                                    <td className="px-4 py-2.5 whitespace-nowrap text-slate-600">
                                                        {fmtDateTime(report.created_at)}
                                                    </td>
                                                    <td className="px-3 py-2.5 whitespace-nowrap font-medium text-slate-900">
                                                        {fmtPeriod(report.date_from, report.date_to)}
                                                    </td>
                                                    <td className="px-3 py-2.5 text-slate-600">
                                                        {report.department_filter || 'Все отделы'}
                                                        {/* Иначе выгрузка по трём людям читается как выгрузка
                                                            по всему отделу — и по ней делают выводы. */}
                                                        {(report.employee_filter || []).length > 0 && (
                                                            <div className="text-[11px] text-slate-500"
                                                                 title={(report.employee_filter || []).join(', ')}>
                                                                только {fmtInt(report.employee_filter.length)} чел.
                                                            </div>
                                                        )}
                                                    </td>
                                                    <td className="px-3 py-2.5 text-slate-500">
                                                        {report.source === 'web'
                                                            ? actorLabel(report.requested_by)
                                                            : (report.chat_title || report.requested_chat_id || 'Telegram')}
                                                    </td>
                                                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">
                                                        {report.rows_count === null || report.rows_count === undefined ? '—' : fmtInt(report.rows_count)}
                                                    </td>
                                                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">
                                                        {report.late_count === null || report.late_count === undefined ? '—' : fmtInt(report.late_count)}
                                                    </td>
                                                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">
                                                        {report.absent_count === null || report.absent_count === undefined ? '—' : fmtInt(report.absent_count)}
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        {report.status === 'running' ? (
                                                            <IosBadge tone="blue">
                                                                <Loader2 size={11} className="animate-spin" /> формируется
                                                            </IosBadge>
                                                        ) : report.status === 'ok' ? (
                                                            <IosBadge tone="green">готов · {fmtSize(report.file_size)}</IosBadge>
                                                        ) : (
                                                            <IosBadge tone="red" title={report.error || ''}>ошибка</IosBadge>
                                                        )}
                                                    </td>
                                                    <td className="px-3 py-2.5 text-right">
                                                        {report.has_file && (
                                                            <button onClick={() => downloadReport(report)}
                                                                    disabled={busy === `file:${report.id}`}
                                                                    className={iosBtnGhost}>
                                                                {busy === `file:${report.id}`
                                                                    ? <Loader2 size={13} className="animate-spin" />
                                                                    : <Download size={13} />}
                                                                Скачать
                                                            </button>
                                                        )}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
            </div>
            <p className="px-1 text-[11px] leading-relaxed text-slate-500">
                Excel лежит в базе вместе с карточкой отчёта, поэтому его можно скачать здесь, не поднимая переписку
                в Telegram. Отчёт за период тянет из Workpace каждый день диапазона — большой период считается минуты.
            </p>
        </div>
    );

    const renderChats = () => (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-[12.5px] text-slate-500">
                    {scoped
                        ? `Куда бот шлёт отбивки отдела «${departmentScope}». Здесь только чаты этого отдела`
                        : 'Куда бот шлёт отбивки. Пустой список отделов = чат получает нарушения по всей компании'}
                </div>
                <div className="flex items-center gap-2">
                    <button onClick={loadChats} className={iosBtnGhost}>
                        <RefreshCw size={13} /> Обновить
                    </button>
                    <button onClick={() => setChatModal({
                        mode: 'create', chat_id: '', title: '', note: '', departments: [],
                        welcome: true, manual: availableChats.length === 0,
                    })} className={iosBtnPrimary}>
                        <Plus size={13} /> Подключить чат
                        {availableChats.length > 0 && (
                            <span className="rounded-full bg-white/25 px-1.5 text-[11px] font-semibold">
                                {availableChats.length}
                            </span>
                        )}
                    </button>
                </div>
            </div>

            {availableChats.length > 0 && (
                <section className={`${iosCard} border-l-4 border-l-blue-400 p-4`}>
                    <div className="flex items-start gap-2.5">
                        <MessageSquare size={16} className="mt-0.5 shrink-0 text-blue-500" />
                        <div className="min-w-0 flex-1">
                            <div className="text-[13px] font-semibold text-slate-900">
                                Бот уже в этих группах, но рассылка не подключена
                            </div>
                            <div className="mt-0.5 text-[12px] text-slate-500">
                                Нажмите «Подключить», чтобы выбрать отделы и включить чат в рассылку.
                            </div>
                            <div className="mt-2 max-h-52 space-y-1 overflow-y-auto pr-1">
                                {availableChats.map((candidate) => (
                                    <div key={candidate.chat_id}
                                         className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2">
                                        <div className="min-w-0">
                                            <div className="truncate text-[13px] font-medium text-slate-800">
                                                {candidate.title || candidate.chat_id}
                                            </div>
                                            <div className="text-[11px] text-slate-400">
                                                <code>{candidate.chat_id}</code>
                                                {candidate.chat_type === 'supergroup' ? ' · супергруппа' : ' · группа'}
                                            </div>
                                        </div>
                                        <button onClick={() => setChatModal({
                                            mode: 'create', chat_id: candidate.chat_id,
                                            title: candidate.title || '', note: '', departments: [],
                                            welcome: true, manual: false,
                                        })} className={`${iosBtnSecondary} shrink-0 py-1.5 text-[12.5px]`}>
                                            <Plus size={12} /> Подключить
                                        </button>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                </section>
            )}

            <div className={`${iosCard} overflow-hidden`}>
                {chatsError ? <ErrorBlock>{chatsError}</ErrorBlock>
                    : chats === null ? <LoadingBlock />
                        : chats.length === 0 ? <EmptyBlock icon={MessageSquare}>Чатов в рассылке пока нет</EmptyBlock>
                            : (
                                <div className="overflow-x-auto">
                                    <table className="w-full text-[13px]">
                                        <thead className="bg-white/85 backdrop-blur-xl">
                                            <tr className="border-b border-slate-200/70 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                                <th className="px-4 py-2.5 text-left">Чат</th>
                                                <th className="px-3 py-2.5 text-left">Отделы</th>
                                                <th className="px-3 py-2.5 text-left">Тишина</th>
                                                <th className="px-3 py-2.5 text-right">Отбивок за 7 дн.</th>
                                                <th className="px-3 py-2.5 text-left">Последняя</th>
                                                <th className="px-3 py-2.5 text-center">В рассылке</th>
                                                <th className="px-3 py-2.5" />
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100">
                                            {chats.map((chat) => (
                                                <tr key={chat.chat_id} className={`transition hover:bg-slate-50/80 ${chat.enabled ? '' : 'opacity-60'}`}>
                                                    <td className="px-4 py-2.5">
                                                        <div className="flex items-center gap-2">
                                                            <span className="font-semibold text-slate-900">
                                                                {chat.title || chat.chat_id}
                                                            </span>
                                                            {chat.is_admin_chat && <IosBadge tone="blue">админ</IosBadge>}
                                                            {chat.created_by === 'discovered' && !chat.enabled && (
                                                                <IosBadge tone="amber">обнаружен, не включён</IosBadge>
                                                            )}
                                                        </div>
                                                        <div className="text-[11.5px] text-slate-400">
                                                            <code>{chat.chat_id}</code>
                                                            {chat.note ? ` · ${chat.note}` : ''}
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <DepartmentChips names={chat.departments} unknown={unknownDepartments} />
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        {chat.muted_all ? <IosBadge tone="amber"><BellOff size={11} /> всё выключено</IosBadge>
                                                            : (chat.muted_users.length + chat.muted_departments.length) > 0
                                                                ? <IosBadge tone="slate">
                                                                    {fmtInt(chat.muted_users.length + chat.muted_departments.length)} правил
                                                                  </IosBadge>
                                                                : <span className="text-[12.5px] text-slate-400">—</span>}
                                                    </td>
                                                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
                                                        {fmtInt(chat.deliveries_period)}
                                                    </td>
                                                    <td className="px-3 py-2.5 whitespace-nowrap text-slate-500">
                                                        {chat.last_delivery_at ? fmtAgo(chat.last_delivery_at) : '—'}
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex justify-center">
                                                            <IosToggle checked={chat.enabled}
                                                                       disabled={busy === `toggle:${chat.chat_id}`}
                                                                       onChange={() => toggleChat(chat)} />
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex items-center justify-end gap-1">
                                                            <button onClick={() => setChatModal({
                                                                mode: 'departments', chat_id: chat.chat_id,
                                                                title: chat.title || '', note: chat.note || '',
                                                                departments: [...chat.departments],
                                                            })} className={iosBtnGhost}>
                                                                <Building2 size={13} /> Отделы
                                                            </button>
                                                            <button onClick={() => testChat(chat.chat_id)}
                                                                    disabled={busy === `test:${chat.chat_id}`}
                                                                    className={iosBtnGhost} title="Отправить тестовое сообщение">
                                                                {busy === `test:${chat.chat_id}`
                                                                    ? <Loader2 size={13} className="animate-spin" />
                                                                    : <Send size={13} />}
                                                            </button>
                                                            {!chat.is_admin_chat && (
                                                                <button onClick={() => deleteChat(chat)}
                                                                        disabled={busy === `delete:${chat.chat_id}`}
                                                                        className={`${iosBtnGhost} text-rose-500 hover:bg-rose-50`}
                                                                        title="Убрать из рассылки">
                                                                    <Trash2 size={13} />
                                                                </button>
                                                            )}
                                                        </div>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
            </div>
            <p className="px-1 text-[11px] leading-relaxed text-slate-500">
                Достаточно добавить бота в группу — чат появится в этом списке выключенным,
                останется включить его тумблером и выбрать отделы. Вручную чат нужен только тогда,
                когда бота добавили до появления этого раздела: тогда возьмите Chat ID из адреса
                группы. Админские чаты из рассылки не убираются — это контур владельца бота.
            </p>
        </div>
    );

    const renderDepartments = () => (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-[12.5px] text-slate-500">
                    Справочник отделов Workpace и то, в какие чаты уходят их нарушения
                    {departments?.items?.[0]?.synced_at && ` · обновлён ${fmtAgo(departments.items[0].synced_at)}`}
                </div>
                <button onClick={syncDepartments} disabled={busy === 'sync'} className={iosBtnSecondary}>
                    {busy === 'sync' ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                    Обновить из Workpace
                </button>
            </div>

            {(departments?.unknown || []).length > 0 && (
                <div className={`${iosCard} border-l-4 border-l-amber-400 p-4`}>
                    <div className="flex items-start gap-2.5">
                        <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-500" />
                        <div>
                            <div className="text-[13px] font-semibold text-slate-900">
                                Фильтры, которых нет в Workpace
                            </div>
                            <div className="mt-0.5 text-[12px] text-slate-500">
                                Чат закреплён за отделом с таким названием, но в Workpace его нет — значит,
                                по этому фильтру чат не получает ничего. Обычно отдел переименовали.
                            </div>
                            <div className="mt-2 space-y-1">
                                {departments.unknown.map((row) => (
                                    <div key={row.name} className="flex flex-wrap items-center gap-2 text-[12.5px]">
                                        <IosBadge tone="amber">{row.name}</IosBadge>
                                        <span className="text-slate-400">→</span>
                                        {row.chat_ids.map((chatId) => {
                                            const chat = (chats || []).find((c) => c.chat_id === chatId);
                                            return <IosBadge key={chatId} tone="slate">{chat?.title || chatId}</IosBadge>;
                                        })}
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                </div>
            )}

            <div className={`${iosCard} overflow-hidden`}>
                {departments === null ? <LoadingBlock />
                    : departmentNames.length === 0 ? (
                        <EmptyBlock icon={Building2}>
                            Справочник пуст — нажмите «Обновить из Workpace»
                        </EmptyBlock>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-[13px]">
                                <thead className="bg-white/85 backdrop-blur-xl">
                                    <tr className="border-b border-slate-200/70 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                        <th className="px-4 py-2.5 text-left">Отдел</th>
                                        <th className="px-3 py-2.5 text-right">Сотрудников</th>
                                        <th className="px-3 py-2.5 text-left">Уведомления уходят в</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100">
                                    {departmentNames.map((dept) => (
                                        <tr key={dept.name} className="transition hover:bg-slate-50/80">
                                            <td className="px-4 py-2.5 font-medium text-slate-900">{dept.name}</td>
                                            <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">
                                                {fmtInt(dept.employees_count)}
                                            </td>
                                            <td className="px-3 py-2.5">
                                                {dept.chat_ids.length === 0 ? (
                                                    <span className="text-[12.5px] text-slate-400">
                                                        только в чаты без фильтра
                                                    </span>
                                                ) : (
                                                    <div className="flex flex-wrap gap-1">
                                                        {dept.chat_ids.map((chatId) => {
                                                            const chat = (chats || []).find((c) => c.chat_id === chatId);
                                                            return (
                                                                <IosBadge key={chatId} tone={chat?.enabled === false ? 'slate' : 'blue'}>
                                                                    {chat?.title || chatId}
                                                                </IosBadge>
                                                            );
                                                        })}
                                                    </div>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
            </div>
        </div>
    );

    const renderMutes = () => {
        const globalRules = (mutes || []).filter((m) => !m.chat_id);
        const chatRules = (mutes || []).filter((m) => m.chat_id);
        const removeMute = (mute) => run(`mute:${mute.id}`, async () => {
            await axios.delete(`${base}/mutes/${mute.id}`, { headers: headers() });
            loadMutes();
            loadOverview();
        }, 'Правило снято');

        const ruleRow = (mute) => (
            <div key={mute.id} className="flex items-center justify-between gap-3 rounded-xl px-3 py-2 transition hover:bg-slate-50">
                <div className="flex min-w-0 items-center gap-2">
                    <IosBadge tone={mute.mute_kind === 'all' ? 'amber' : 'slate'}>
                        {MUTE_KIND_LABELS[mute.mute_kind] || mute.mute_kind}
                    </IosBadge>
                    <span className="truncate text-[13px] text-slate-700">
                        {mute.mute_kind === 'all' ? 'бот молчит полностью' : mute.mute_value}
                    </span>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                    <span className="text-[11.5px] text-slate-400">{actorLabel(mute.created_by)}</span>
                    <button onClick={() => removeMute(mute)} disabled={busy === `mute:${mute.id}`}
                            className={`${iosBtnGhost} text-rose-500 hover:bg-rose-50`}>
                        {busy === `mute:${mute.id}` ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                    </button>
                </div>
            </div>
        );

        return (
            <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="text-[12.5px] text-slate-500">
                        {scoped
                            ? `Кого бот не трогает в чатах отдела «${departmentScope}». Молчать во всех чатах компании может только администратор бота`
                            : 'Кого бот не трогает. Глобальные правила действуют на все чаты, правила чата — только на него'}
                    </div>
                    <button onClick={() => setMuteModal(newMuteDraft())} className={iosBtnPrimary}>
                        <Plus size={13} /> Добавить правило
                    </button>
                </div>

                {mutes === null ? <div className={iosCard}><LoadingBlock /></div> : (
                    <div className="grid gap-3 lg:grid-cols-2">
                        <section className={`${iosCard} p-4`}>
                            <div className={`${iosGroupLabel} mb-2`}>Глобально</div>
                            {globalRules.length === 0
                                ? <div className="py-6 text-center text-[12.5px] text-slate-400">Глобальных правил нет</div>
                                : <div className="space-y-0.5">{globalRules.map(ruleRow)}</div>}
                        </section>

                        <section className={`${iosCard} p-4`}>
                            <div className={`${iosGroupLabel} mb-2`}>По чатам</div>
                            {chatRules.length === 0
                                ? <div className="py-6 text-center text-[12.5px] text-slate-400">Правил для отдельных чатов нет</div>
                                : (
                                    <div className="space-y-3">
                                        {Object.entries(chatRules.reduce((acc, rule) => {
                                            (acc[rule.chat_id] = acc[rule.chat_id] || []).push(rule);
                                            return acc;
                                        }, {})).map(([chatId, rules]) => (
                                            <div key={chatId}>
                                                <div className="px-3 text-[12px] font-semibold text-slate-600">
                                                    {rules[0].chat_title || chatId}
                                                </div>
                                                <div className="space-y-0.5">{rules.map(ruleRow)}</div>
                                            </div>
                                        ))}
                                    </div>
                                )}
                        </section>
                    </div>
                )}
                <p className="px-1 text-[11px] leading-relaxed text-slate-500">
                    Сотрудник и отдел сопоставляются по вхождению строки, поэтому «Иванов» отключит всех однофамильцев.
                    Те же правила пользователи ставят себе командами <code className="rounded bg-slate-100 px-1">/mute_user</code> и
                    <code className="ml-1 rounded bg-slate-100 px-1">/mute_dept</code> в личном чате с ботом.
                </p>
            </div>
        );
    };

    /* ─── модалки ──────────────────────────────────────────────────────── */

    const saveChatModal = () => {
        if (!chatModal) return;
        if (chatModal.mode === 'create') {
            const chatId = String(chatModal.chat_id || '').trim();
            if (!/^-?\d+$/.test(chatId)) {
                showToast?.('Chat ID — это число, для групп со знаком минус', 'error');
                return;
            }
            run('chat-save', async () => {
                const r = await axios.post(`${base}/chats`, {
                    chat_id: chatId,
                    title: chatModal.title,
                    note: chatModal.note,
                    departments: chatModal.departments,
                    send_welcome: chatModal.welcome,
                }, { headers: headers() });
                setChatModal(null);
                loadChats();
                loadOverview();
                if (r.data?.warning) showToast?.(r.data.warning, 'warning');
                return r.data;
            }, 'Чат добавлен в рассылку');
        } else {
            run('chat-save', async () => {
                await axios.patch(`${base}/chats/${chatModal.chat_id}`, {
                    title: chatModal.title,
                    note: chatModal.note,
                    departments: chatModal.departments,
                }, { headers: headers() });
                setChatModal(null);
                loadChats();
                loadDepartments();
            }, 'Настройки чата сохранены');
        }
    };

    /* В границах отдела правило либо действует в своём чате, либо глушит свой же отдел
     * целиком: правило «во всех чатах» без привязки к отделу бэкенд не примет. Поэтому
     * новое правило открываем сразу на первом своём чате, а «Отдел» подставляем свой. */
    const newMuteDraft = () => (scoped
        ? { kind: 'user', value: '', chatId: (chats || [])[0]?.chat_id || '' }
        : { kind: 'user', value: '', chatId: '' });

    const setMuteKind = (kind) => setMuteModal((prev) => ({
        ...prev,
        kind,
        value: kind === 'dept' && scoped ? departmentScope : (kind === prev.kind ? prev.value : ''),
        // «Глобально» в границах отдела осмысленно только для правила на свой отдел.
        chatId: (scoped && kind !== 'dept' && !prev.chatId)
            ? ((chats || [])[0]?.chat_id || '')
            : prev.chatId,
    }));

    const saveMuteModal = () => {
        if (!muteModal) return;
        if (muteModal.kind !== 'all' && !String(muteModal.value || '').trim()) {
            showToast?.('Укажите ФИО сотрудника или название отдела', 'error');
            return;
        }
        run('mute-save', async () => {
            await axios.post(`${base}/mutes`, {
                mute_kind: muteModal.kind,
                mute_value: muteModal.value,
                chat_id: muteModal.chatId || null,
            }, { headers: headers() });
            setMuteModal(null);
            loadMutes();
            loadOverview();
        }, 'Правило добавлено');
    };

    const submitReport = () => {
        if (!reportModal) return;
        run('report-create', async () => {
            await axios.post(`${base}/reports`, {
                date_from: reportModal.from,
                date_to: reportModal.to,
                department: reportModal.department || null,
                send_to_chat_id: reportModal.chatId || null,
                employees: reportModal.employees || [],
            }, { headers: headers() });
            setReportModal(null);
            loadReports();
        }, 'Отчёт поставлен в очередь — появится в списке через минуту');
    };

    return (
        <div className="w-full" style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
                <div>
                    <h2 className="text-lg font-semibold tracking-tight text-slate-900">
                        Отметки{scoped ? ` · ${departmentScope}` : ''}
                    </h2>
                    <p className="text-xs text-slate-500">
                        {scoped
                            ? `Отметки прихода и ухода по отделу «${departmentScope}»: опоздания, отчёты и чаты отдела`
                            : 'Отметки прихода и ухода: опоздания, отчёты и связки чатов с отделами'}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <div className="flex flex-wrap rounded-xl bg-slate-100 p-1">
                        {TABS.map((item) => (
                            <SegButton key={item.key} active={tab === item.key}
                                       onClick={() => setTab(item.key)} icon={item.icon}>
                                {item.label}
                            </SegButton>
                        ))}
                    </div>
                    {tab === 'overview' && (
                        <div className="flex rounded-xl bg-slate-100 p-1">
                            {[7, 14, 30].map((days) => (
                                <button key={days}
                                        onClick={() => { setPeriodDays(days); loadOverview(days); }}
                                        className={`rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                                            periodDays === days ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                                                : 'text-slate-500 hover:text-slate-700'}`}>
                                    {days} дн.
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {overview && overview.workpace_configured === false && (
                <div className={`${iosCard} mb-3 flex items-start gap-2.5 border-l-4 border-l-amber-400 px-4 py-3`}>
                    <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-500" />
                    <div className="text-[12.5px] leading-relaxed text-slate-600">
                        Не заданы доступы к Workpace (<code className="rounded bg-slate-100 px-1">WORKPACE_LOGIN</code> и
                        <code className="ml-1 rounded bg-slate-100 px-1">WORKPACE_PASSWORD</code>), поэтому опрос смен
                        не запускается и отчёты собрать нечем. Настройки чатов и история при этом доступны.
                    </div>
                </div>
            )}

            {tab === 'attendance' && renderAttendance()}
            {tab === 'overview' && renderOverview()}
            {tab === 'employees' && renderEmployees()}
            {tab === 'events' && renderEvents()}
            {tab === 'reports' && renderReports()}
            {tab === 'chats' && renderChats()}
            {tab === 'departments' && renderDepartments()}
            {tab === 'mutes' && renderMutes()}

            <IosModal
                open={Boolean(chatModal)}
                onClose={() => setChatModal(null)}
                title={chatModal?.mode === 'create' ? 'Новый чат рассылки' : 'Настройки чата'}
                subtitle={chatModal?.mode === 'create' ? 'Бот должен уже состоять в этом чате' : chatModal?.chat_id}
                footer={(
                    <>
                        <button onClick={() => setChatModal(null)} className={iosBtnSecondary}>Отмена</button>
                        <button onClick={saveChatModal} disabled={busy === 'chat-save'} className={iosBtnPrimary}>
                            {busy === 'chat-save' && <Loader2 size={13} className="animate-spin" />}
                            Сохранить
                        </button>
                    </>
                )}
            >
                {chatModal && (
                    <div className="space-y-4">
                        {chatModal.mode === 'create' && !chatModal.manual && (
                            <div>
                                <div className={`${iosGroupLabel} mb-1.5`}>Группы, где бот уже есть</div>
                                {availableChats.length === 0 ? (
                                    <div className="rounded-xl bg-slate-50 px-3.5 py-3 text-[12.5px] text-slate-500">
                                        Свободных групп нет — все, где есть бот, уже в списке рассылки.
                                    </div>
                                ) : (
                                    <div className="max-h-60 overflow-y-auto rounded-xl bg-slate-50 ring-1 ring-slate-200/70">
                                        {availableChats.map((candidate) => {
                                            const picked = chatModal.chat_id === candidate.chat_id;
                                            return (
                                                <button key={candidate.chat_id} type="button"
                                                        onClick={() => setChatModal({
                                                            ...chatModal,
                                                            chat_id: candidate.chat_id,
                                                            title: candidate.title || '',
                                                        })}
                                                        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition hover:bg-white">
                                                    <span className={`grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full transition ${
                                                        picked ? 'bg-blue-600 text-white' : 'bg-white ring-1 ring-slate-300'}`}>
                                                        {picked && <CheckCircle2 size={12} />}
                                                    </span>
                                                    <span className="min-w-0">
                                                        <span className={`block truncate text-[13px] ${
                                                            picked ? 'font-semibold text-slate-900' : 'text-slate-700'}`}>
                                                            {candidate.title || candidate.chat_id}
                                                        </span>
                                                        <span className="block text-[11px] text-slate-400">
                                                            <code>{candidate.chat_id}</code>
                                                            {candidate.chat_type === 'supergroup' ? ' · супергруппа' : ' · группа'}
                                                        </span>
                                                    </span>
                                                </button>
                                            );
                                        })}
                                    </div>
                                )}
                                <button type="button"
                                        onClick={() => setChatModal({ ...chatModal, manual: true, chat_id: '' })}
                                        className="mt-1.5 px-1 text-[11.5px] font-medium text-blue-600 hover:underline">
                                    Ввести Chat ID вручную
                                </button>
                            </div>
                        )}
                        {chatModal.mode === 'create' && chatModal.manual && (
                            <div>
                                <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Chat ID</label>
                                <input value={chatModal.chat_id} autoFocus
                                       onChange={(e) => setChatModal({ ...chatModal, chat_id: e.target.value })}
                                       placeholder="-1001234567890" className={iosInput} />
                                <div className="mt-1 flex items-center justify-between gap-2 px-1">
                                    <span className="text-[11px] text-slate-500">
                                        Нужен, только если бота добавили давно и группа не попала в список
                                    </span>
                                    {availableChats.length > 0 && (
                                        <button type="button"
                                                onClick={() => setChatModal({ ...chatModal, manual: false, chat_id: '' })}
                                                className="shrink-0 text-[11.5px] font-medium text-blue-600 hover:underline">
                                            Выбрать из списка
                                        </button>
                                    )}
                                </div>
                            </div>
                        )}
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Название</label>
                            <input value={chatModal.title}
                                   onChange={(e) => setChatModal({ ...chatModal, title: e.target.value })}
                                   placeholder="Например: Контакт-центр — руководители" className={iosInput} />
                        </div>
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Заметка</label>
                            <input value={chatModal.note}
                                   onChange={(e) => setChatModal({ ...chatModal, note: e.target.value })}
                                   placeholder="Необязательно" className={iosInput} />
                        </div>
                        <div>
                            <div className={`${iosGroupLabel} mb-1.5`}>Отделы</div>
                            {scoped ? (
                                // Отдел у чата один и не выбирается: бэкенд всё равно
                                // перезапишет фильтр своим отделом.
                                <div className="rounded-xl bg-slate-50 px-3.5 py-3 text-[12.5px] text-slate-600">
                                    <IosBadge tone="blue">{departmentScope}</IosBadge>
                                    <span className="ml-2">чат получает нарушения только этого отдела</span>
                                </div>
                            ) : (
                                <DepartmentPicker all={departmentNames} selected={chatModal.departments}
                                                  onChange={(departments) => setChatModal({ ...chatModal, departments })} />
                            )}
                        </div>
                        {chatModal.mode === 'create' && (
                            <div className="flex items-center justify-between rounded-xl bg-slate-50 px-3.5 py-2.5">
                                <div>
                                    <div className="text-[13px] font-medium text-slate-800">Отправить приветствие</div>
                                    <div className="text-[11.5px] text-slate-500">Сразу проверит, что бот может писать в чат</div>
                                </div>
                                <IosToggle checked={chatModal.welcome}
                                           onChange={(welcome) => setChatModal({ ...chatModal, welcome })} />
                            </div>
                        )}
                    </div>
                )}
            </IosModal>

            <IosModal
                open={Boolean(muteModal)}
                onClose={() => setMuteModal(null)}
                title="Новое правило тишины"
                subtitle="Бот перестанет слать выбранные отбивки"
                footer={(
                    <>
                        <button onClick={() => setMuteModal(null)} className={iosBtnSecondary}>Отмена</button>
                        <button onClick={saveMuteModal} disabled={busy === 'mute-save'} className={iosBtnPrimary}>
                            {busy === 'mute-save' && <Loader2 size={13} className="animate-spin" />}
                            Добавить
                        </button>
                    </>
                )}
            >
                {muteModal && (
                    <div className="space-y-4">
                        <div>
                            <div className={`${iosGroupLabel} mb-1.5`}>Что отключаем</div>
                            <div className="flex rounded-xl bg-slate-100 p-1">
                                {Object.entries(MUTE_KIND_LABELS).map(([key, label]) => (
                                    <button key={key} onClick={() => setMuteKind(key)}
                                            className={`flex-1 rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                                                muteModal.kind === key ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                                                       : 'text-slate-500 hover:text-slate-700'}`}>
                                        {label}
                                    </button>
                                ))}
                            </div>
                        </div>
                        {muteModal.kind === 'dept' ? (
                            <div>
                                <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Отдел</label>
                                {scoped ? (
                                    <div className="rounded-xl bg-slate-50 px-3.5 py-3 text-[12.5px] text-slate-600">
                                        <IosBadge tone="blue">{departmentScope}</IosBadge>
                                        <span className="ml-2">чужой отдел заглушить нельзя</span>
                                    </div>
                                ) : (
                                    <CustomSelect
                                        variant="ios"
                                        searchable
                                        value={muteModal.value}
                                        onChange={(value) => setMuteModal({ ...muteModal, value })}
                                        options={[
                                            { value: '', label: 'Выберите отдел…' },
                                            ...departmentNames.map((dept) => ({ value: dept.name, label: dept.name })),
                                        ]}
                                        searchPlaceholder="Поиск отдела…"
                                        ariaLabel="Отдел"
                                    />
                                )}
                            </div>
                        ) : muteModal.kind === 'user' ? (
                            <div>
                                <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">ФИО сотрудника</label>
                                <input value={muteModal.value} autoFocus
                                       onChange={(e) => setMuteModal({ ...muteModal, value: e.target.value })}
                                       placeholder="Как в Workpace" className={iosInput} />
                            </div>
                        ) : (
                            <div className="rounded-xl bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-700">
                                Бот полностью замолчит в выбранной области, пока правило не снимут.
                            </div>
                        )}
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Область</label>
                            <CustomSelect
                                variant="ios"
                                searchable={(chats || []).length > 7}
                                value={muteModal.chatId}
                                onChange={(chatId) => setMuteModal({ ...muteModal, chatId })}
                                options={[
                                    // Правило без чата бьёт по всем чатам компании: в границах
                                    // отдела его оставляем только для «свой отдел целиком».
                                    ...((!scoped || muteModal.kind === 'dept')
                                        ? [{ value: '', label: 'Глобально — во всех чатах' }]
                                        : []),
                                    ...(chats || []).map((chat) => ({
                                        value: chat.chat_id,
                                        label: `Только в «${chat.title || chat.chat_id}»`,
                                    })),
                                ]}
                                searchPlaceholder="Поиск чата…"
                                ariaLabel="Область правила"
                            />
                        </div>
                    </div>
                )}
            </IosModal>

            <IosModal
                open={Boolean(reportModal)}
                onClose={() => setReportModal(null)}
                title="Сформировать отчёт"
                subtitle="Excel по данным Workpace — тот же, что бот присылает по /report"
                footer={(
                    <>
                        <button onClick={() => setReportModal(null)} className={iosBtnSecondary}>Отмена</button>
                        <button onClick={submitReport} disabled={busy === 'report-create'} className={iosBtnPrimary}>
                            {busy === 'report-create' && <Loader2 size={13} className="animate-spin" />}
                            Сформировать
                        </button>
                    </>
                )}
            >
                {reportModal && (
                    <div className="space-y-4">
                        <div>
                            <div className={`${iosGroupLabel} mb-1.5`}>Период</div>
                            <IosDateRangePicker from={reportModal.from} to={reportModal.to} max={isoDate(new Date())}
                                                onChange={({ from, to }) => setReportModal({ ...reportModal, from, to })} />
                            <div className="mt-1 px-1 text-[11px] text-slate-500">
                                Один день — детальная таблица, несколько — сводный лист плюс лист на каждый день
                            </div>
                        </div>
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Отдел</label>
                            {scoped ? (
                                <div className="rounded-xl bg-slate-50 px-3.5 py-3 text-[12.5px] text-slate-600">
                                    <IosBadge tone="blue">{departmentScope}</IosBadge>
                                    <span className="ml-2">отчёт собирается только по своему отделу</span>
                                </div>
                            ) : (
                                <CustomSelect
                                    variant="ios"
                                    searchable
                                    value={reportModal.department}
                                    onChange={(department) => setReportModal({ ...reportModal, department })}
                                    options={[
                                        { value: '', label: 'Все отделы' },
                                        ...departmentNames.map((dept) => ({ value: dept.name, label: dept.name })),
                                    ]}
                                    searchPlaceholder="Поиск отдела…"
                                    ariaLabel="Отдел отчёта"
                                />
                            )}
                        </div>
                        {/* Выбор людей спрятан за кнопкой: по умолчанию отчёт собирается
                            по всему отделу, и постоянно висящий список из сотни ФИО был бы
                            лишним шумом в форме. */}
                        <div>
                            {!reportModal.pickEmployees ? (
                                <button type="button" className={iosBtnSecondary}
                                        onClick={() => {
                                            setReportModal({ ...reportModal, pickEmployees: true });
                                            if (employees === null) {
                                                loadEmployees({
                                                    from: reportModal.from, to: reportModal.to,
                                                    department: reportModal.department, q: '',
                                                });
                                            }
                                        }}>
                                    <Users className="h-4 w-4" /> Выбрать сотрудников
                                </button>
                            ) : (
                                <div>
                                    <div className="mb-1 flex items-center justify-between px-1">
                                        <label className="text-[12px] font-medium text-slate-500">
                                            Сотрудники{(reportModal.employees || []).length > 0
                                                ? ` · выбрано ${(reportModal.employees || []).length}`
                                                : ' · весь отдел'}
                                        </label>
                                        <button type="button" className={iosBtnGhost}
                                                onClick={() => setReportModal({
                                                    ...reportModal, pickEmployees: false, employees: [],
                                                })}>
                                            Сбросить
                                        </button>
                                    </div>
                                    {employees === null ? (
                                        <div className="flex items-center gap-2 rounded-xl bg-slate-50 px-3.5 py-3 text-[12.5px] text-slate-500">
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Загружаем список…
                                        </div>
                                    ) : (
                                        <div className="max-h-52 overflow-y-auto rounded-xl ring-1 ring-slate-200/70">
                                            {(employees || []).length === 0 && (
                                                <div className="px-3.5 py-3 text-[12.5px] text-slate-500">
                                                    За выбранный период сотрудников не нашлось
                                                </div>
                                            )}
                                            {(employees || []).map((person) => {
                                                const name = person.employee_name;
                                                const picked = (reportModal.employees || []).includes(name);
                                                return (
                                                    <label key={name}
                                                           className="flex cursor-pointer items-center gap-2.5 border-b border-slate-100 px-3.5 py-2 text-[13px] last:border-0 hover:bg-slate-50">
                                                        <input type="checkbox" checked={picked}
                                                               onChange={() => setReportModal({
                                                                   ...reportModal,
                                                                   employees: picked
                                                                       ? (reportModal.employees || []).filter((x) => x !== name)
                                                                       : [...(reportModal.employees || []), name],
                                                               })} />
                                                        <span className="text-slate-800">{name}</span>
                                                    </label>
                                                );
                                            })}
                                        </div>
                                    )}
                                    <div className="mt-1 px-1 text-[11px] text-slate-500">
                                        Никого не отметили — отчёт соберётся по всему отделу
                                    </div>
                                </div>
                            )}
                        </div>
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                Отправить в чат <span className="text-slate-400">(необязательно)</span>
                            </label>
                            <CustomSelect
                                variant="ios"
                                searchable={(chats || []).length > 7}
                                value={reportModal.chatId}
                                onChange={(chatId) => setReportModal({ ...reportModal, chatId })}
                                options={[
                                    { value: '', label: 'Не отправлять — только сохранить здесь' },
                                    ...(chats || []).map((chat) => ({
                                        value: chat.chat_id, label: chat.title || chat.chat_id,
                                    })),
                                ]}
                                searchPlaceholder="Поиск чата…"
                                ariaLabel="Чат для отправки"
                            />
                        </div>
                        <div className="flex items-start gap-2 rounded-xl bg-slate-50 px-3.5 py-2.5 text-[12px] text-slate-500">
                            <CalendarClock size={14} className="mt-0.5 shrink-0 text-slate-400" />
                            Отчёт считается в фоне: строка появится в списке со статусом «формируется»
                            и сменится на «готов», когда файл будет собран.
                        </div>
                    </div>
                )}
            </IosModal>

            <IosModal
                open={planModalOpen}
                onClose={() => setPlanModalOpen(false)}
                title="Сопоставление с Workpace"
                subtitle="План берём из графика iCore, отметки — из Workpace: без пары человека не с чем сверить"
                footer={(
                    <button onClick={() => setPlanModalOpen(false)} className={iosBtnSecondary}>Закрыть</button>
                )}
            >
                {planLinksError && <ErrorBlock>{planLinksError}</ErrorBlock>}
                {planLinks === null ? <LoadingBlock /> : (
                    <div className="space-y-4">
                        {/* Сначала то, что требует решения, — иначе список из двух
                            десятков нормально сопоставленных людей его прячет. */}
                        {(planLinks.unlinked || []).length > 0 && (
                            <div>
                                <div className={`${iosGroupLabel} mb-1.5`}>
                                    Карточки Workpace без сотрудника
                                </div>
                                <div className="space-y-1.5">
                                    {planLinks.unlinked.map((card) => (
                                        <div key={card.ext_id}
                                             className="rounded-xl bg-slate-50 px-3.5 py-2.5">
                                            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                                <span className="text-[13px] font-medium text-slate-800">
                                                    {card.full_name || card.ext_id}
                                                </span>
                                                <span className="text-[11.5px] text-slate-500">
                                                    {UNLINKED_REASONS[card.reason] || card.reason}
                                                </span>
                                            </div>
                                            <div className="mt-2 flex flex-wrap items-center gap-2">
                                                <div className="min-w-[220px] flex-1">
                                                    <CustomSelect
                                                        variant="ios"
                                                        searchable
                                                        value=""
                                                        onChange={(userId) => userId && savePlanLink({
                                                            workpace_ext_id: card.ext_id,
                                                            user_id: Number(userId),
                                                        })}
                                                        options={[
                                                            { value: '', label: 'Это сотрудник…' },
                                                            ...(planLinks.people || []).map((person) => ({
                                                                value: String(person.user_id),
                                                                label: person.city
                                                                    ? `${person.name} · ${person.city}`
                                                                    : person.name,
                                                            })),
                                                        ]}
                                                        searchPlaceholder="Поиск сотрудника…"
                                                        ariaLabel="Кому принадлежит карточка"
                                                    />
                                                </div>
                                                {card.reason === 'excluded' ? (
                                                    <button type="button" className={iosBtnGhost}
                                                            disabled={busy === `plan:${card.ext_id}`}
                                                            onClick={() => savePlanLink({
                                                                workpace_ext_id: card.ext_id, reset: true,
                                                            })}>
                                                        Вернуть в поиск
                                                    </button>
                                                ) : (
                                                    <button type="button" className={iosBtnGhost}
                                                            disabled={busy === `plan:${card.ext_id}`}
                                                            onClick={() => savePlanLink({
                                                                workpace_ext_id: card.ext_id, user_id: null,
                                                            })}>
                                                        Не наш сотрудник
                                                    </button>
                                                )}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        <div>
                            <div className={`${iosGroupLabel} mb-1.5`}>Наши сотрудники</div>
                            <div className="divide-y divide-slate-100 overflow-hidden rounded-xl ring-1 ring-slate-200/70">
                                {(planLinks.people || []).map((person) => (
                                    <div key={person.user_id}
                                         className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3.5 py-2.5">
                                        <span className="min-w-0">
                                            <span className="text-[13px] font-medium text-slate-800">{person.name}</span>
                                            {person.city && (
                                                <span className="ml-1.5 text-[11.5px] text-slate-400">{person.city}</span>
                                            )}
                                        </span>
                                        <span className="flex flex-wrap items-center gap-1.5">
                                            {person.shifts_ahead === 0 && (
                                                <IosBadge tone="amber"
                                                          title={`Смен в графике iCore на ближайшие ${planLinks.schedule_days || 14} дней нет`}>
                                                    нет смен в графике
                                                </IosBadge>
                                            )}
                                            {(person.cards || []).length === 0 ? (
                                                <IosBadge tone="red" title="Отметок по человеку взять негде">
                                                    нет карточки Workpace
                                                </IosBadge>
                                            ) : person.cards.map((card) => (
                                                <button key={card.ext_id} type="button"
                                                        disabled={busy === `plan:${card.ext_id}`}
                                                        onClick={() => savePlanLink({
                                                            workpace_ext_id: card.ext_id, reset: true,
                                                        })}
                                                        title={card.source === 'manual'
                                                            ? 'Привязано вручную — нажмите, чтобы снять'
                                                            : 'Сопоставлено по ФИО — нажмите, чтобы отвязать'}
                                                        className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600 transition hover:bg-slate-200 active:scale-[0.98]">
                                                    {card.source === 'manual' && <Link2 size={10} />}
                                                    {card.full_name || card.ext_id}
                                                </button>
                                            ))}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>

                        <div className="flex items-start gap-2 rounded-xl bg-slate-50 px-3.5 py-2.5 text-[12px] text-slate-500">
                            <AlertCircle size={14} className="mt-0.5 shrink-0 text-slate-400" />
                            Человека без карточки Workpace бот не проверяет: отметок по нему нет,
                            и любая смена выглядела бы как неявка. Смены берутся из «Графиков работы»
                            — пока график не проставлен, проверять тоже нечего.
                        </div>
                    </div>
                )}
            </IosModal>
        </div>
    );
}
