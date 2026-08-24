import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, BookOpen, EyeOff, Loader2, RefreshCw, Search, ShieldAlert,
    ShieldCheck, TrendingUp, Users,
} from 'lucide-react';
import {
    Bar as RBar, CartesianGrid, ComposedChart, Line, ResponsiveContainer,
    Tooltip, XAxis, YAxis,
} from 'recharts';

import { iosCard, iosGroupLabel, iosBtnGhost, IosBadge } from '../ui/ios';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import { Bar, Metric, Table, Td, Th } from './reportKit';
import useStableCallback from './useStableCallback';

/* Вкладка «Аналитика» — отчёт о том, работает ли база знаний.
 *
 * Три блока и жёсткий порядок, потому что вопросов ровно три: пользуются ли
 * викой (ЧТЕНИЕ), выполнено ли обязательное (ОЗНАКОМЛЕНИЯ) и чего в ней не
 * хватает (СПРОС БЕЗ ОТВЕТА). Первые два отвечают на «работает ли», третий —
 * на «что делать дальше», и третий читают, только поверив первым двум.
 *
 * ЧЕГО ЗДЕСЬ НЕТ СОЗНАТЕЛЬНО. Ни «среднего числа находок», ни «медианы
 * подтверждения», ни «токенов помощника», ни топа цитируемых статей. Всё это
 * считается, всё это красиво и ни одно не меняет ничьих действий. Отчёт, где
 * половина таблиц — «просто цифры», перестают открывать целиком, и вместе с
 * цифрами теряются те пять разрезов, ради которых он и сделан.
 *
 * ДВЕ ВЕЩИ, КОТОРЫЕ ОБЯЗАНЫ БЫТЬ НАПИСАНЫ НА ЭКРАНЕ.
 *
 * 1. Период действует не на всё: ознакомления показаны на сейчас, потому что
 *    просрочка не бывает «за прошлый месяц». Молча игнорировать выбранный
 *    фильтр нельзя — это читается как поломка.
 * 2. Цифры здесь и под статьёй разные: под статьёй пожизненный счётчик
 *    открытий, здесь — прочтения за период. Оговорки приходят с сервера и
 *    лежат в подвале экрана.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Почему спрос остался без ответа. Четыре причины, и лечатся они РАЗНЫМ:
   первая — написать статью, вторая — выдать доступ, остальные две про
   качество ответа помощника. Ради этого различия блок и существует. */
const REASON = {
    missing: { label: 'Нет статьи', tone: 'amber' },
    rights: { label: 'У других находится', tone: 'blue' },
    empty_perimeter: { label: 'Пустой периметр', tone: 'slate' },
    unverified: { label: 'Числа не подтвердились', tone: 'slate' },
    refused: { label: 'Модель отказала', tone: 'slate' },
};

const CHANNEL = { search: 'Поиск', assistant: 'Помощник' };

/* Пресеты периода. «Весь период» стоит последним и назван честно: на нём график
   переключается на недели, потому что история тянется назад настолько,
   насколько тянется самый старый просмотр. */
const back = (days) => isoDate(new Date(Date.now() - days * 86400000));

const PRESETS = [
    { label: '7 дней', range: () => ({ from: back(6), to: isoDate(new Date()) }) },
    { label: '30 дней', range: () => ({ from: back(29), to: isoDate(new Date()) }) },
    { label: '90 дней', range: () => ({ from: back(89), to: isoDate(new Date()) }) },
    { label: 'Весь период', range: () => ({ from: '', to: '' }) },
];

const ACK_STATUS = {
    not_open: 'не открывал',
    in_progress: 'открыл',
    read_completed: 'дочитал',
    overdue: 'просрочено',
    requires_reacknowledgement: 'нужна переподпись',
};

const num = (value) => (value === null || value === undefined
    ? '—' : Number(value).toLocaleString('ru-RU'));

const day = (iso) => {
    if (!iso) return '—';
    const value = new Date(iso);
    if (Number.isNaN(value.getTime())) return '—';
    return value.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' });
};

const dayShort = (iso) => {
    if (!iso) return '';
    const value = new Date(iso);
    if (Number.isNaN(value.getTime())) return '';
    return value.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
};

/** Сколько дней назад. «Не открывали 214 дней» читается быстрее даты. */
const ago = (iso) => {
    if (!iso) return null;
    const value = new Date(iso);
    if (Number.isNaN(value.getTime())) return null;
    return Math.max(0, Math.round((Date.now() - value.getTime()) / 86400000));
};

const Group = ({ title, subtitle, icon: Icon, children }) => (
    <section className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-1">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                {Icon && <Icon size={15} className="text-indigo-600" />}{title}
            </h2>
            {subtitle && <span className="text-[12px] text-slate-400">{subtitle}</span>}
        </div>
        {children}
    </section>
);

const Tiles = ({ children }) => (
    <div className={`${iosCard} grid grid-cols-2 gap-2 p-3 sm:grid-cols-4`}>
        {children}
    </div>
);

/** Ссылка на статью. Отдельным компонентом, потому что повторяется в трёх
 *  таблицах, а без onOpenArticle обязана оставаться просто текстом. */
const ArticleLink = ({ row, onOpen }) => (onOpen ? (
    <button
        type="button"
        className="text-left text-indigo-600 hover:underline"
        onClick={() => onOpen(row.slug)}
    >
        {row.title}
    </button>
) : <span>{row.title}</span>);

/* Ряд для графика.
 *
 * Две задачи, и обе видны только на настоящих данных.
 *
 * 1. РЯД ОБЯЗАН БЫТЬ ПЛОТНЫМ. Сервер отдаёт только дни, в которые читали, и на
 *    оси они встали бы вплотную друг к другу: неделя молчания выглядела бы как
 *    ровный ряд, а возврат после неё — как рост, которого не было.
 *
 * 2. КРУПНЫЙ ПЕРИОД СЧИТАЕТСЯ ПО НЕДЕЛЯМ. На «всём периоде» между первым и
 *    последним чтением может лежать год: триста точек по одному-двум прочтениям
 *    сжимают всю осмысленную часть в правый сантиметр графика, а столбики
 *    становятся тоньше пикселя и исчезают вовсе. Ровно это и вышло на первом
 *    прогоне — из-за одной статьи, прочитанной триста дней назад.
 *
 * Порог в два месяца выбран по ширине: около шестидесяти столбиков — предел,
 * при котором на 1440 px ещё видно каждый.
 */
const DAILY_LIMIT = 62;

const addDays = (date, count) => {
    const next = new Date(date);
    next.setDate(next.getDate() + count);
    return next;
};

/** Понедельник недели, в которую попадает дата. */
const weekStart = (date) => addDays(date, -((date.getDay() + 6) % 7));

export const chartSeries = (days, since = null, until = null) => {
    const rows = (days || []).filter((d) => d && d.day);
    if (rows.length < 2) return { points: rows, grain: 'day' };

    /* Границы оси — ВЫБРАННЫЙ период, а не первый и последний день с чтениями.
       Иначе выбранные тридцать дней, из которых читали в последние четырнадцать,
       рисуются как четырнадцать — и «две недели тишины в начале месяца»
       превращается в «данных до этого не было». */
    const first = new Date(`${since || rows[0].day}T00:00:00`);
    const last = new Date(`${until || rows[rows.length - 1].day}T00:00:00`);
    const span = Math.round((last - first) / 86400000) + 1;
    const weekly = span > DAILY_LIMIT;

    const buckets = new Map();
    /* Ключ собираем isoDate'ом, а НЕ toISOString(): у нас часовой пояс +5, и
       toISOString от локальной полуночи уводит дату на сутки назад — ось
       начиналась с 25 июля при выбранном 26-м. Данные при этом ложились в
       правильные корзины (сдвиг одинаков с обеих сторон), поэтому ошибка
       видна только на подписях и ловится глазами, а не тестом на суммы. */
    const key = (date) => isoDate(weekly ? weekStart(date) : date);

    // Пустые корзины заводим заранее — иначе дырка в данных станет дыркой в оси.
    for (let cursor = weekly ? weekStart(first) : first, guard = 0;
        cursor <= last && guard < 400; cursor = addDays(cursor, weekly ? 7 : 1), guard += 1) {
        buckets.set(key(cursor), { day: key(cursor), reads: 0, readers: 0 });
    }
    rows.forEach((row) => {
        const bucket = buckets.get(key(new Date(`${row.day}T00:00:00`)));
        if (!bucket) return;
        bucket.reads += row.reads || 0;
        // Читатели за неделю НЕ складываются честно: один человек, заходивший
        // трижды, дал бы троих. Берём максимум за день недели — заниженную, но
        // не выдуманную оценку. Точное число за период стоит в плитке выше.
        bucket.readers = Math.max(bucket.readers, row.readers || 0);
    });
    return { points: [...buckets.values()], grain: weekly ? 'week' : 'day' };
};

const DaysChart = ({ days, since = null, until = null }) => {
    const { points, grain } = useMemo(
        () => chartSeries(days, since, until), [days, since, until]);
    const data = useMemo(() => points.map((d) => ({
        ...d, label: dayShort(d.day),
    })), [points]);

    if (data.length < 2) {
        return (
            <div className={`${iosCard} flex flex-col items-center justify-center gap-1 px-6 py-10 text-center`}>
                <div className="text-[13px] text-slate-500">Недостаточно данных для графика</div>
                <div className="text-[12px] text-slate-400">
                    Появится, когда в периоде наберётся хотя бы два дня с чтениями.
                </div>
            </div>
        );
    }

    return (
        <div className={`${iosCard} p-3`}>
            <div
                className="h-64"
                role="img"
                aria-label={grain === 'week'
                    ? `Прочтения по неделям: ${data.length} недель`
                    : `Прочтения по дням: ${data.length} дней`}
            >
                <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                        <XAxis
                            dataKey="label"
                            tick={{ fontSize: 10, fill: '#94a3b8' }}
                            interval="preserveStartEnd"
                            tickLine={false}
                            axisLine={{ stroke: '#e2e8f0' }}
                        />
                        <YAxis
                            tick={{ fontSize: 10, fill: '#94a3b8' }}
                            tickLine={false}
                            axisLine={false}
                            allowDecimals={false}
                        />
                        <Tooltip
                            contentStyle={{
                                borderRadius: 12, border: '1px solid #e2e8f0',
                                fontSize: 12, boxShadow: '0 4px 16px rgba(15,23,42,0.08)',
                            }}
                            labelStyle={{ color: '#64748b', fontSize: 11 }}
                            formatter={(value, name) => [num(value), name]}
                        />
                        <RBar
                            dataKey="reads"
                            name={grain === 'week' ? 'Прочтений за неделю' : 'Прочтений'}
                            fill="#c7d2fe" radius={[4, 4, 0, 0]}
                        />
                        <Line
                            type="monotone" dataKey="readers"
                            name={grain === 'week' ? 'Читателей в день недели, максимум' : 'Читателей'}
                            stroke="#4f46e5" strokeWidth={2} dot={false}
                        />
                    </ComposedChart>
                </ResponsiveContainer>
            </div>
            {grain === 'week' && (
                <div className="px-1 pt-2 text-[11.5px] text-slate-400">
                    Период длиннее двух месяцев, поэтому график собран по неделям —
                    по дням он превратился бы в частокол из трёхсот столбиков.
                </div>
            )}
            {/* Те же числа построчно — для скринридера: график для него картинка. */}
            <ul className="sr-only">
                {data.map((d) => (
                    <li key={d.day}>{day(d.day)}: прочтений {d.reads}, читателей {d.readers}</li>
                ))}
            </ul>
        </div>
    );
};

export default function WikiAnalytics({ base, headers, showToast, spaceId = null,
                                        onOpenArticle = null }) {
    const toast = useStableCallback(showToast);

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    /* Период одним значением: «с» и «по» — две границы одного отрезка, а не два
       независимых фильтра. Пустые границы = «за всё время».

       По умолчанию — последний месяц, а НЕ вся история. Причина видна только на
       настоящих данных: одна статья, прочитанная год назад, растягивает ось
       графика на триста дней, и весь живой трафик сжимается в правый край.
       «Весь период» остаётся в календаре — просто он не то, с чего начинают. */
    const [range, setRange] = useState(() => ({
        from: isoDate(new Date(Date.now() - 29 * 86400000)),
        to: isoDate(new Date()),
    }));

    /* Зависимости — примитивы, а не сам `range`: календарь отдаёт новый объект
       на каждый выбор, и по ссылке запрос уходил бы даже за тем же периодом. */
    const params = useMemo(() => ({
        since: range.from || undefined,
        until: range.to || undefined,
        space_id: spaceId || undefined,
        limit: 20,
    }), [range.from, range.to, spaceId]);

    const load = useCallback(() => {
        setLoading(true);
        return axios.get(`${base}/analytics`, { headers, params })
            .then((r) => { setData(r.data); setError(''); })
            .catch((e) => {
                const message = errText(e, 'Не удалось загрузить аналитику');
                /* Плашку вместо содержимого ставим, только когда содержимого
                   ещё нет: иначе человек теряет то, что уже разбирал, — там
                   достаточно тоста. Правило то же, что в «Журнале». */
                setData((prev) => {
                    if (prev) toast(message, 'error'); else setError(message);
                    return prev;
                });
            })
            .finally(() => setLoading(false));
    }, [base, headers, params, toast]);

    useEffect(() => { load(); }, [load]);

    const reading = data?.reading;
    const ack = data?.acknowledgements;
    const demand = data?.demand;

    if (loading && !data) {
        return (
            <div className={`${iosCard} flex items-center gap-2.5 px-4 py-10`} aria-busy="true">
                <Loader2 size={18} className="animate-spin text-slate-400" />
                <span className="text-[13px] text-slate-500">Считаем аналитику…</span>
            </div>
        );
    }

    if (error && !data) {
        return (
            <div className={`${iosCard} flex items-start gap-3 p-4`}>
                <AlertCircle size={18} className="mt-0.5 shrink-0 text-rose-500" />
                <div className="min-w-0">
                    <div className="text-[13.5px] font-semibold text-slate-900">
                        Аналитика не загрузилась
                    </div>
                    <div className="mt-0.5 text-[12px] text-slate-500">{error}</div>
                    <button type="button" onClick={load} className={`${iosBtnGhost} mt-3`}>
                        <RefreshCw size={14} /> Попробовать снова
                    </button>
                </div>
            </div>
        );
    }

    const t = reading?.totals || {};
    const at = ack?.totals || {};
    const ds = demand?.search || {};
    const da = demand?.assistant || {};

    return (
        <div className="space-y-6">

            {/* Панель периода. Подписей «с»/«по» нет: чип сам называет период. */}
            <section className={`${iosCard} flex flex-wrap items-center gap-3 px-4 py-3`}>
                <IosDateRangePicker
                    from={range.from} to={range.to} max={isoDate(new Date())}
                    onChange={setRange}
                    presets={PRESETS}
                />
                {(range.from || range.to) && (
                    <button
                        type="button"
                        className={iosBtnGhost}
                        onClick={() => setRange({ from: '', to: '' })}
                    >
                        Весь период
                    </button>
                )}
                {loading && (
                    <span className="flex items-center gap-1.5 text-[12px] text-slate-400">
                        <Loader2 size={13} className="animate-spin" /> считаем…
                    </span>
                )}
            </section>

            {/* ── Блок 1. Чтение и охват ─────────────────────────────────── */}
            <Group title="Чтение и охват" icon={TrendingUp} subtitle="за выбранный период">
                <Tiles>
                    <Metric
                        label="Прочтений" value={num(t.reads)}
                        hint={`${num(t.opens)} открытий с повторами`}
                    />
                    <Metric label="Читателей" value={num(t.readers)} />
                    <Metric
                        label="Охват статей"
                        value={t.coverage === null || t.coverage === undefined
                            ? '—' : `${t.coverage}%`}
                        hint={`${num(t.articles_read)} из ${num(t.published)} опубликованных`}
                        tone={t.coverage === null || t.coverage === undefined ? null
                            : t.coverage >= 60 ? 'good' : t.coverage >= 30 ? 'warn' : 'bad'}
                    />
                    <Metric
                        label="Не открывали"
                        value={num(reading?.unread?.length)}
                        hint="опубликованных статей за период"
                        tone={reading?.unread?.length ? 'warn' : 'good'}
                    />
                </Tiles>

                <DaysChart
                    days={reading?.days}
                    since={data?.period?.since}
                    until={data?.period?.until}
                />

                <Table
                    title="Кто читает: по отделам" icon={Users}
                    count={reading?.departments?.length}
                    empty="За период никто не читал."
                    hint="«Из штата» — сколько человек отдела заходили хотя бы раз. Уволенные в знаменатель не входят, отпуск и больничный — входят."
                    head={(
                        <tr>
                            <Th>Отдел</Th>
                            <Th right>Читателей</Th>
                            <Th right>Из штата</Th>
                            <Th right>Прочтений</Th>
                            <Th right>Статей</Th>
                        </tr>
                    )}
                >
                    {(reading?.departments || []).map((row) => (
                        <tr key={row.department_id ?? 'none'}>
                            <Td>{row.name}</Td>
                            <Td right>{num(row.readers)}</Td>
                            <Td right>
                                {row.headcount
                                    ? <Bar done={row.readers} total={row.headcount} />
                                    : <span className="text-slate-400">—</span>}
                            </Td>
                            <Td right>{num(row.reads)}</Td>
                            <Td right>{num(row.articles_read)}</Td>
                        </tr>
                    ))}
                </Table>

                <Table
                    title="Что читают" icon={BookOpen}
                    count={reading?.top?.length}
                    empty="За период не открыли ни одной статьи."
                    head={(
                        <tr>
                            <Th>Статья</Th>
                            <Th right>Прочтений</Th>
                            <Th right>Читателей</Th>
                            <Th right>Обновлена</Th>
                        </tr>
                    )}
                >
                    {(reading?.top || []).map((row) => (
                        <tr key={row.id}>
                            <Td>
                                <ArticleLink row={row} onOpen={onOpenArticle} />
                                {row.status !== 'published' && (
                                    <IosBadge tone="slate" className="ml-2">черновик</IosBadge>
                                )}
                            </Td>
                            <Td right>{num(row.reads)}</Td>
                            <Td right>{num(row.readers)}</Td>
                            <Td right muted>{day(row.updated_at)}</Td>
                        </tr>
                    ))}
                </Table>

                <Table
                    title="Контент-долг: не открывали за период" icon={EyeOff}
                    count={reading?.unread?.length}
                    empty="За период открывали каждую опубликованную статью."
                    hint="Только опубликованные: черновик без просмотров — это норма, а не находка."
                    head={(
                        <tr>
                            <Th>Статья</Th>
                            <Th right>Последний раз читали</Th>
                            <Th right>Обновлена</Th>
                        </tr>
                    )}
                >
                    {(reading?.unread || []).map((row) => {
                        const days = ago(row.last_at);
                        return (
                            <tr key={row.id}>
                                <Td><ArticleLink row={row} onOpen={onOpenArticle} /></Td>
                                <Td right muted={!row.last_at}>
                                    {row.last_at ? `${day(row.last_at)} · ${days} дн. назад` : 'ни разу'}
                                </Td>
                                <Td right muted>{day(row.updated_at)}</Td>
                            </tr>
                        );
                    })}
                </Table>
            </Group>

            {/* ── Блок 2. Ознакомления ───────────────────────────────────── */}
            <Group
                title="Ознакомления" icon={ShieldAlert}
                subtitle="на сейчас — период на этот блок не действует"
            >
                {/* Назначений нет вовсе — блок складывается в одну строку.
                    Пять пустых плиток и две пустые таблицы рядом с живыми
                    блоками читаются как поломка, а не как «нечего показывать». */}
                {!at.total ? (
                    <div className={`${iosCard} flex items-center gap-3 px-4 py-4`}>
                        <ShieldCheck size={18} className="shrink-0 text-slate-300" />
                        <div className="text-[13px] text-slate-500">
                            Обязательных ознакомлений в этом пространстве пока не назначали.
                        </div>
                    </div>
                ) : (
                    <>
                        <Tiles>
                            <Metric
                                label="Просрочено" value={num(at.overdue)}
                                tone={at.overdue > 0 ? 'bad' : 'good'}
                                hint="срок вышел, не подтвердили"
                            />
                            <Metric
                                label="Не открывали" value={num(at.not_open)}
                                tone={at.not_open > 0 ? 'warn' : null}
                            />
                            <Metric
                                label="Подтверждено" value={num(at.done)}
                                hint={`из ${num(at.total)} назначений`}
                            />
                            <Metric
                                label="Людей" value={num(at.people)}
                                hint={`по ${num(at.articles)} статьям`}
                            />
                        </Tiles>

                        <Table
                            title="По отделам" icon={Users}
                            count={ack?.departments?.length}
                            empty="Назначений нет."
                            hint="Отдел — из снимка на момент назначения: перешедший человек не уносит просрочку в новый отдел."
                            head={(
                                <tr>
                                    <Th>Отдел</Th>
                                    <Th right>Назначено</Th>
                                    <Th right>Подтверждено</Th>
                                    <Th right>Просрочено</Th>
                                </tr>
                            )}
                        >
                            {(ack?.departments || []).map((row) => (
                                <tr key={row.department_id ?? 'none'}>
                                    <Td>{row.name}</Td>
                                    <Td right>{num(row.total)}</Td>
                                    <Td right><Bar done={row.done} total={row.total} /></Td>
                                    <Td right>
                                        {row.overdue > 0
                                            ? <span className="font-medium text-rose-600">{num(row.overdue)}</span>
                                            : <span className="text-slate-400">0</span>}
                                    </Td>
                                </tr>
                            ))}
                        </Table>

                        <Table
                            title="Просрочено поимённо" icon={ShieldAlert}
                            count={ack?.overdue?.length}
                            empty="Просроченных ознакомлений нет."
                            head={(
                                <tr>
                                    <Th>Человек</Th>
                                    <Th>Отдел и группа</Th>
                                    <Th>Статья</Th>
                                    <Th right>Срок был</Th>
                                    <Th right>Дней</Th>
                                </tr>
                            )}
                        >
                            {(ack?.overdue || []).map((row) => (
                                <tr key={`${row.user_id}-${row.article_id}`}>
                                    <Td>
                                        {row.name || '—'}
                                        <div className="text-[11px] text-slate-400">
                                            {ACK_STATUS[row.status] || row.status}
                                        </div>
                                    </Td>
                                    <Td muted>
                                        {row.department}
                                        {row.team && row.team !== '—' ? ` · ${row.team}` : ''}
                                    </Td>
                                    <Td>{row.title}</Td>
                                    <Td right muted>{day(row.due_at)}</Td>
                                    <Td right>
                                        <span className="font-medium text-rose-600">{num(row.days)}</span>
                                    </Td>
                                </tr>
                            ))}
                        </Table>
                    </>
                )}
            </Group>

            {/* ── Блок 3. Спрос, на который вика не отвечает ─────────────── */}
            <Group
                title="Спрос без ответа" icon={Search}
                subtitle="что искали и о чём спрашивали впустую"
            >
                <Tiles>
                    <Metric
                        label="Запросов в поиске" value={num(ds.total)}
                        hint={ds.steps ? `${num(ds.steps)} обращений до склейки` : null}
                    />
                    <Metric
                        label="Ничего не нашли" value={num(ds.empty)}
                        hint={ds.empty_share === null || ds.empty_share === undefined
                            ? null : `${ds.empty_share}% запросов`}
                        tone={ds.empty_share === null || ds.empty_share === undefined ? null
                            : ds.empty_share >= 20 ? 'bad' : ds.empty_share >= 10 ? 'warn' : 'good'}
                    />
                    <Metric
                        label="Вопросов помощнику" value={num(da.total)}
                        hint={da.people ? `${num(da.people)} человек` : null}
                    />
                    <Metric
                        label="Помощник не нашёл" value={num(da.no_answer)}
                        tone={da.total && da.no_answer / da.total >= 0.25 ? 'warn' : null}
                        hint={da.clarify ? `${num(da.clarify)} раз переспросил` : null}
                    />
                </Tiles>

                <Table
                    title="Чего не хватает" count={demand?.items?.length}
                    empty={ds.logging_since
                        ? 'За период всё, что искали и спрашивали, находилось.'
                        : 'Журнал поиска пуст — запросы начали записываться только что.'}
                    hint={[
                        '«Нет статьи» — это и есть дыра в базе знаний: текста по теме не нашлось ни поиском, ни помощником.',
                        '«У других находится» — статья есть, но спрашивавшему её не выдали: лечится доступом, а не текстом.',
                        ds.logging_since ? `Журнал поиска ведётся с ${day(ds.logging_since)}; запросы, набранные подряд одним человеком, склеиваются в один.` : null,
                        'Кто именно спрашивал, здесь не показано намеренно: список отвечает на «какой статьи не хватает», а не на «кто не знал».',
                    ].filter(Boolean).join(' ')}
                    head={(
                        <tr>
                            <Th>Запрос или вопрос</Th>
                            <Th>Откуда</Th>
                            <Th>Почему без ответа</Th>
                            <Th right>Раз</Th>
                            <Th right>Людей</Th>
                            <Th right>Последний раз</Th>
                        </tr>
                    )}
                >
                    {(demand?.items || []).map((row) => {
                        const reason = REASON[row.reason] || { label: row.reason, tone: 'slate' };
                        return (
                            <tr key={`${row.channel}-${row.key}`}>
                                <Td>{row.text}</Td>
                                <Td muted>{CHANNEL[row.channel] || row.channel}</Td>
                                <Td><IosBadge tone={reason.tone}>{reason.label}</IosBadge></Td>
                                <Td right>{num(row.times)}</Td>
                                <Td right>{num(row.people)}</Td>
                                <Td right muted>{day(row.last_at)}</Td>
                            </tr>
                        );
                    })}
                </Table>
            </Group>

            {/* Оговорки приходят с сервера: они про то, как устроены данные, и
                меняются вместе с запросами, а не с вёрсткой. */}
            {(data?.notes || []).length > 0 && (
                <section className="space-y-1 px-1">
                    <div className={iosGroupLabel}>Как это посчитано</div>
                    <ul className="space-y-1">
                        {data.notes.map((note) => (
                            <li key={note} className="flex gap-2 text-[11.5px] leading-relaxed text-slate-400">
                                <span className="select-none">·</span><span>{note}</span>
                            </li>
                        ))}
                    </ul>
                </section>
            )}
        </div>
    );
}
