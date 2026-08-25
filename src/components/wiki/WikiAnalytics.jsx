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

import { iosCard, iosBtnGhost, IosBadge, IosHint, IosSegmented } from '../ui/ios';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import { Bar, Metric, Table, Td, Th } from './reportKit';
import useStableCallback from './useStableCallback';

/* Вкладка «Аналитика» — отчёт о том, работает ли база знаний.
 *
 * Три блока и жёсткий порядок, потому что вопросов ровно три: пользуются ли
 * викой (ЧТЕНИЕ), выполнено ли обязательное (ОЗНАКОМЛЕНИЯ) и чего в ней не
 * хватает (ЧЕГО НЕ ХВАТАЕТ). Первые два отвечают на «работает ли», третий —
 * на «что делать дальше», и третий читают, только поверив первым двум.
 * Вопрос блока написан подзаголовком: заголовок «Чтение и охват» называет
 * тему, но не говорит, зачем сюда смотреть.
 *
 * ЧЕГО ЗДЕСЬ НЕТ СОЗНАТЕЛЬНО. Ни «среднего числа находок», ни «медианы
 * подтверждения», ни «токенов помощника», ни топа цитируемых статей. Всё это
 * считается, всё это красиво и ни одно не меняет ничьих действий. Отчёт, где
 * половина таблиц — «просто цифры», перестают открывать целиком, и вместе с
 * цифрами теряются те пять разрезов, ради которых он и сделан.
 *
 * ГДЕ ЖИВУТ ОБЪЯСНЕНИЯ. Оговорка стоит у того числа, которое объясняет, а не
 * подвалом «как это посчитано» внизу страницы: подвал был набором верных
 * предложений, до которых не доскроллили. Правило простое — что нужно всем и
 * всегда (период на ознакомления не действует; список сужен по отделу),
 * написано открытым текстом; что нужно один раз (определение прочтения,
 * почему числа расходятся со счётчиком под статьёй), спрятано под «i» рядом
 * с показателем. Текст оговорок по-прежнему приходит с сервера — он про то,
 * как устроены данные, и меняется вместе с запросами.
 *
 * ЧТО ЗДЕСЬ НАЗЫВАЕТСЯ СВОИМИ ИМЕНАМИ. Внутренних слов на экране нет:
 * «периметр», «контент-долг» и «свёртка префиксов» понятны тому, кто писал
 * запросы, и никому больше. Причины «без ответа» подписаны действием, которое
 * из них следует, — легенда под таблицей показывает только те, что реально
 * встретились в выборке.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Почему спрос остался без ответа. Пять причин, и лечатся они РАЗНЫМ: первая —
   написать статью, вторая и третья — выдать доступ, остальные про качество
   ответа помощника. Ради этого различия блок и существует, поэтому подпись
   называет не механику («пустой периметр», «числа не подтвердились»), а то,
   что видно снаружи, а расшифровка идёт легендой под таблицей. */
const REASON = {
    missing: {
        label: 'Нет статьи', tone: 'amber',
        help: 'по теме не нашлось текста ни поиском, ни помощником — это и есть дыра в базе знаний',
    },
    rights: {
        label: 'Не выдан доступ', tone: 'blue',
        help: 'статья есть — тому же запросу она нашлась у других; спрашивавшему её не выдали',
    },
    empty_perimeter: {
        label: 'Доступа нет ни к чему', tone: 'blue',
        help: 'человеку не выдан ни один раздел, поэтому поиск пуст на любой запрос',
    },
    unverified: {
        label: 'Ответ придержан', tone: 'slate',
        help: 'помощник нашёл текст, но не смог подтвердить числа фрагментами и промолчал — статью стоит уточнить',
    },
    refused: {
        label: 'Помощник отказал', tone: 'slate',
        help: 'модель не стала отвечать своими словами по найденному тексту',
    },
};

const CHANNEL = { search: 'Поиск', assistant: 'Помощник' };

/* Пресеты периода стоят ОТДЕЛЬНЫМ сегментным контролом, а не только в подвале
   календаря. Причина не в удобстве: на отчётном экране первый вопрос к любому
   числу — «за какой это период», и ответ обязан читаться, не открывая
   поповер. Календарь рядом остаётся для произвольного отрезка и показывает
   выбранные даты; когда он не совпадает ни с одним пресетом, в сегментах не
   подсвечено ничего — и это честно.

   «Всё время» названо честно: на нём график переключается на недели, потому
   что история тянется назад настолько, насколько тянется самый старый
   просмотр. */
const back = (days) => isoDate(new Date(Date.now() - days * 86400000));

const PRESETS = [
    { value: 'd7', label: '7 дней', days: 7 },
    { value: 'd30', label: '30 дней', days: 30 },
    { value: 'd90', label: '90 дней', days: 90 },
    { value: 'all', label: 'Всё время', days: null },
];

const presetRange = (value) => {
    const preset = PRESETS.find((p) => p.value === value);
    if (!preset || !preset.days) return { from: '', to: '' };
    return { from: back(preset.days - 1), to: isoDate(new Date()) };
};

/** Какой пресет отвечает выбранному отрезку. Пустая строка — произвольный. */
const presetOf = ({ from, to }) => {
    if (!from && !to) return 'all';
    if (to !== isoDate(new Date())) return '';
    const hit = PRESETS.find((p) => p.days && back(p.days - 1) === from);
    return hit ? hit.value : '';
};

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

/* Блок отчёта: заголовок, вопрос, на который он отвечает, и чип периода.
 *
 * Чип нужен именно здесь, а не только наверху: блоки живут в РАЗНОМ времени —
 * чтение за выбранный отрезок, ознакомления на сейчас. Пока это стояло серой
 * припиской у заголовка, разница читалась как «фильтр не сработал». */
const Group = ({ title, subtitle, scope, icon: Icon, children }) => (
    <section className="space-y-3">
        <div className="px-1">
            <div className="flex flex-wrap items-center gap-2">
                <h2 className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                    {Icon && <Icon size={15} className="text-indigo-600" />}{title}
                </h2>
                {scope && <IosBadge tone="slate">{scope}</IosBadge>}
            </div>
            {subtitle && (
                <p className="mt-1 text-[12.5px] leading-snug text-slate-500">{subtitle}</p>
            )}
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

/* Расшифровка причин под таблицей.
 *
 * Показываются ТОЛЬКО те причины, что встретились в выборке: полная легенда из
 * пяти строк под таблицей из двух — это пять строк текста, которые никто не
 * читает, и ровно тот шум, из-за которого перестают читать и остальное.
 *
 * Легенда нужна вообще: колонка «Почему без ответа» состоит из бейджей, и без
 * расшифровки «Ответ придержан» не подсказывает никакого действия. А действия
 * у причин разные — написать статью, выдать доступ, уточнить числа в статье.
 */
const ReasonLegend = ({ items }) => {
    const shown = useMemo(() => {
        const seen = new Set((items || []).map((row) => row.reason));
        return Object.keys(REASON).filter((key) => seen.has(key));
    }, [items]);

    if (!shown.length) return null;
    return (
        <div className="space-y-1.5">
            {shown.map((key) => (
                <div key={key} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                    {/* Серому бейджу нужна обводка ИМЕННО здесь: в таблице он
                        лежит на белой строке и виден, а под таблицей — на сером
                        фоне раздела, где сливается с ним и перестаёт читаться
                        как тот же бейдж, что в колонке. */}
                    <IosBadge
                        tone={REASON[key].tone}
                        className={REASON[key].tone === 'slate' ? 'ring-1 ring-slate-200' : ''}
                    >
                        {REASON[key].label}
                    </IosBadge>
                    <span>— {REASON[key].help}</span>
                </div>
            ))}
        </div>
    );
};

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
                <div className="text-[12px] text-slate-500">
                    Появится, когда в периоде наберётся хотя бы два дня с чтениями.
                </div>
            </div>
        );
    }

    /* Легенда обязательна: рядов два и они разной природы — столбики считают
       события, линия людей. Без подписи цвета читаются как «что-то и что-то», а
       узнать их можно было только наведением на конкретный столбик. */
    return (
        <div className={`${iosCard} p-3`}>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-1">
                <div className="text-[12.5px] font-semibold text-slate-700">
                    {grain === 'week' ? 'Прочтения по неделям' : 'Прочтения по дням'}
                </div>
                <div className="flex items-center gap-3 text-[11.5px] text-slate-500">
                    <span className="flex items-center gap-1.5">
                        <span className="h-2.5 w-2.5 rounded-[3px] bg-[#c7d2fe]" />
                        прочтений
                    </span>
                    <span className="flex items-center gap-1.5">
                        <span className="h-[3px] w-3.5 rounded-full bg-[#4f46e5]" />
                        {grain === 'week' ? 'читателей в день, максимум' : 'читателей'}
                    </span>
                </div>
            </div>
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
                <div className="px-1 pt-2 text-[11.5px] leading-relaxed text-slate-500">
                    Период длиннее двух месяцев, поэтому график собран по неделям —
                    по дням он превратился бы в частокол из трёхсот столбиков.
                    Линия при этом показывает не сумму читателей за неделю, а
                    самый людный день в ней: один и тот же человек, заходивший
                    трижды, иначе посчитался бы за троих.
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
    const notes = data?.notes || {};

    return (
        <div className="space-y-6">

            {/* Панель периода: пресеты сегментами, календарь — для произвольного
                отрезка. Отдельной кнопки «Весь период» рядом нет: она делала
                ровно то же, что сегмент «Всё время», а два элемента с одним
                действием читаются как два разных. */}
            <section className={`${iosCard} flex flex-wrap items-center gap-3 px-4 py-3`}>
                <IosSegmented
                    value={presetOf(range)}
                    options={PRESETS}
                    onChange={(value) => setRange(presetRange(value))}
                    ariaLabel="Период отчёта"
                />
                <IosDateRangePicker
                    from={range.from} to={range.to} max={isoDate(new Date())}
                    onChange={setRange}
                    presets={[{ label: 'Сегодня',
                                range: () => ({ from: isoDate(new Date()),
                                                to: isoDate(new Date()) }) }]}
                />
                {loading && (
                    <span className="flex items-center gap-1.5 text-[12px] text-slate-500">
                        <Loader2 size={13} className="animate-spin" /> считаем…
                    </span>
                )}
            </section>

            {/* Ни одной видимой статьи — говорим об этом ДО нулей, а не после.
                Экран из нулей без объяснения читается как «викой не пользуются»,
                хотя причина в том, что смотрящему не выдан ни один раздел. */}
            {notes.empty && (
                <div className={`${iosCard} flex items-start gap-3 px-4 py-3`}>
                    <EyeOff size={16} className="mt-0.5 shrink-0 text-amber-500" />
                    <div className="text-[12.5px] leading-relaxed text-slate-600">
                        {notes.empty}
                    </div>
                </div>
            )}

            {/* ── Блок 1. Чтение и охват ─────────────────────────────────── */}
            <Group
                title="Чтение и охват" icon={TrendingUp} scope="за выбранный период"
                subtitle="Пользуются ли викой: сколько читают, кто читает и что лежит нетронутым."
            >
                <Tiles>
                    <Metric
                        label="Прочтений" value={num(t.reads)}
                        hint={`из ${num(t.opens)} открытий: повторы за минуту свёрнуты`}
                        help={notes.read}
                    />
                    <Metric
                        label="Читателей" value={num(t.readers)}
                        hint="человек открыли хотя бы одну статью"
                    />
                    <Metric
                        label="Охват статей"
                        value={t.coverage === null || t.coverage === undefined
                            ? '—' : `${t.coverage}%`}
                        hint={`${num(t.articles_read)} из ${num(t.published)} опубликованных`}
                        help="Доля опубликованных статей, которые за период открыл хотя бы один человек. Черновики в знаменатель не входят: показатель не должен падать от того, что кто-то начал писать новую статью."
                        tone={t.coverage === null || t.coverage === undefined ? null
                            : t.coverage >= 60 ? 'good' : t.coverage >= 30 ? 'warn' : 'bad'}
                    />
                    {/* Число берётся из итогов, а НЕ из длины списка ниже:
                        список режется потолком строк, и плитка показывала бы
                        «не открывали 20» при пятидесяти семи нетронутых. */}
                    <Metric
                        label="Статей без чтений"
                        value={num(t.unread)}
                        hint="опубликованных — за период не открыли ни разу"
                        tone={t.unread ? 'warn' : 'good'}
                    />
                </Tiles>

                <DaysChart
                    days={reading?.days}
                    since={data?.period?.since}
                    until={data?.period?.until}
                />

                {/* Колонка называется «Доля отдела», а знаменатель написан
                    рядом с процентом: «Из штата» с одной лишь полосой не
                    говорило, от какого числа доля считается, и 30 % у отдела
                    из шести человек читались как 30 % у отдела из ста. */}
                <Table
                    title="Кто читает: по отделам" icon={Users}
                    count={reading?.departments?.length}
                    empty="За период никто не читал."
                    help="«Доля отдела» — сколько человек из штата отдела заходили в вику хотя бы раз за период. Уволенные и уволившиеся в знаменатель не входят, отпуск, больничный и Б/С — входят: человек в отпуске остаётся сотрудником, которому вика адресована. Отдел берётся тот, в котором человек был на момент чтения."
                    head={(
                        <tr>
                            <Th>Отдел</Th>
                            <Th right>Читателей</Th>
                            <Th right>Доля отдела</Th>
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
                                    ? (
                                        <Bar
                                            done={row.readers} total={row.headcount}
                                            caption={`из ${num(row.headcount)}`}
                                        />
                                    )
                                    : <span className="text-slate-500">—</span>}
                            </Td>
                            <Td right>{num(row.reads)}</Td>
                            <Td right>{num(row.articles_read)}</Td>
                        </tr>
                    ))}
                </Table>

                <Table
                    title="Что читают чаще всего" icon={BookOpen}
                    count={reading?.top?.length}
                    empty="За период не открыли ни одной статьи."
                    help="Самые читаемые за период статьи из тех, что видны вам. Черновики сюда тоже попадают — их открывают редакторы, и такая строка помечена."
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
                    title="Не открывали ни разу за период" icon={EyeOff}
                    count={reading?.unread?.length}
                    total={t.unread}
                    empty="За период открывали каждую опубликованную статью."
                    help="Только опубликованные статьи: черновик без просмотров — это норма, а не находка. Первыми идут те, которых не открывали никогда, дальше — по давности последнего чтения. Дата последнего чтения берётся за всё время, а не за период: «читали в марте» и «не читали никогда» — разные диагнозы."
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
                title="Ознакомления" icon={ShieldAlert} scope="на сейчас"
                subtitle={['Выполнено ли обязательное: кто не подтвердил назначенные статьи.',
                           notes.ack_now].filter(Boolean).join(' ')}
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
                                hint="срок вышел, подписи нет"
                            />
                            <Metric
                                label="Не открывали" value={num(at.not_open)}
                                hint="назначено, но статью не открывали"
                                tone={at.not_open > 0 ? 'warn' : null}
                            />
                            <Metric
                                label="Подтверждено" value={num(at.done)}
                                hint={`из ${num(at.total)} назначений`}
                            />
                            <Metric
                                label="Людей" value={num(at.people)}
                                hint={`кому назначено — по ${num(at.articles)} статьям`}
                            />
                        </Tiles>

                        <Table
                            title="Ознакомления по отделам" icon={Users}
                            count={ack?.departments?.length}
                            empty="Назначений нет."
                            help="Отдел берётся из снимка на момент назначения, а не из нынешней карточки: перешедший человек не уносит просрочку в новый отдел — назначали ему тогда, когда он был здесь. Отменённые и перевыпущенные назначения не считаются."
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

                        {/* Список сужен по отделу — и это видно рядом с ним,
                            а не только оговоркой внизу страницы: у супервайзера
                            и у директора числа здесь РАЗНЫЕ, и без пометки
                            расхождение читается как поломка данных. Потолок
                            строк подписан там же («· 20 из 57»), но только
                            когда сужения нет: иначе обрез по правам и обрез по
                            потолку слились бы в одно число. */}
                        <Table
                            title="Просрочено поимённо" icon={ShieldAlert}
                            count={ack?.overdue?.length}
                            total={data?.scoped ? null : at.overdue}
                            badge={data?.scoped ? (
                                <span className="flex items-center gap-1.5">
                                    <IosBadge tone="blue">только ваши отделы</IosBadge>
                                    <IosHint text={notes.scoped} align="right"
                                             label="Почему список сужен" />
                                </span>
                            ) : null}
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
                title="Чего не хватает в базе" icon={Search} scope="за выбранный период"
                subtitle="Что искали и о чём спрашивали помощника, но ответа не нашли, — темы для новых статей."
            >
                <Tiles>
                    <Metric
                        label="Запросов в поиске" value={num(ds.total)}
                        hint={ds.steps && ds.steps !== ds.total
                            ? `набрано ${num(ds.steps)} раз` : null}
                        help={[
                            'Запросы к поиску по вике за период. Поле ищет по мере набора, поэтому одна фраза приезжает пятью запросами-огрызками: «дог», «догов», «договор» — это один запрос, а не три, и здесь они склеены.',
                            ds.logging_since
                                ? `Журнал поиска ведётся с ${day(ds.logging_since)} — за более ранние дни запросов не сохранилось.`
                                : 'Журнал поиска пуст: запросы начали записываться только что.',
                        ].join(' ')}
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
                        hint={da.people ? `спрашивали ${num(da.people)} человек` : null}
                    />
                    <Metric
                        label="Помощник не нашёл" value={num(da.no_answer)}
                        tone={da.total && da.no_answer / da.total >= 0.25 ? 'warn' : null}
                        hint={da.clarify ? `и ${num(da.clarify)} раз переспросил` : null}
                        helpAlign="right"
                        help="Ответы, в которых помощник прямо сказал, что ответа в вике нет. «Переспросил» — это когда он попросил уточнить вопрос: такие обращения в дыры базы знаний не записываются."
                    />
                </Tiles>

                <Table
                    title="Темы без ответа" count={demand?.items?.length}
                    empty={ds.logging_since
                        ? 'За период всё, что искали и спрашивали, находилось.'
                        : 'Журнал поиска пуст — запросы начали записываться только что.'}
                    help="Кто именно спрашивал, здесь не показано намеренно: список отвечает на вопрос «какой статьи не хватает», а не «кто не знал». Одинаковые запросы разных людей склеены в одну строку, поэтому «Спрашивали» больше, чем «Людей»."
                    hint={<ReasonLegend items={demand?.items} />}
                    head={(
                        <tr>
                            <Th>Запрос или вопрос</Th>
                            <Th>Откуда</Th>
                            <Th>Почему без ответа</Th>
                            <Th right>Спрашивали</Th>
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
        </div>
    );
}
