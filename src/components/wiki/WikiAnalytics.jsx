import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, BookOpen, Download, EyeOff, FolderTree, History, Library,
    Loader2, RefreshCw, Search, ShieldAlert, ShieldCheck, TrendingUp, Users,
} from 'lucide-react';
import {
    Bar as RBar, CartesianGrid, ComposedChart, Line, ResponsiveContainer,
    Tooltip, XAxis, YAxis,
} from 'recharts';

import { iosCard, iosBtnGhost, iosBtnSecondary, IosBadge, IosHint, IosSegmented } from '../ui/ios';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import { Bar, Metric, PagedTable, Td, Th } from './reportKit';
import useStableCallback from './useStableCallback';

/* Вкладка «Аналитика» — отчёт о том, работает ли база знаний.
 *
 * Четыре блока и жёсткий порядок, потому что вопросов четыре: пользуются ли
 * викой (ЧТЕНИЕ), в каком состоянии её содержимое (СОДЕРЖИМОЕ), выполнено ли
 * обязательное (ОЗНАКОМЛЕНИЯ) и чего в ней не хватает (ЧЕГО НЕ ХВАТАЕТ).
 * Первые три отвечают на «работает ли», последний — на «что делать дальше»,
 * и его читают, только поверив предыдущим. Вопрос блока написан подзаголовком:
 * заголовок «Чтение и охват» называет тему, но не говорит, зачем сюда смотреть.
 *
 * ЧЕГО ЗДЕСЬ НЕТ СОЗНАТЕЛЬНО. Ни «среднего числа находок», ни «медианы
 * подтверждения», ни «токенов помощника», ни топа цитируемых статей. Всё это
 * считается, всё это красиво и ни одно не меняет ничьих действий. Отчёт, где
 * половина таблиц — «просто цифры», перестают открывать целиком, и вместе с
 * цифрами теряются те разрезы, ради которых он и сделан.
 *
 * ТАБЛИЦЫ ЛИСТАЮТСЯ ПО ПЯТЬ СТРОК (PagedTable). Отчёт из девяти таблиц по два
 * десятка строк — это лента, в которой блоки ниже не находят вовсе; с пятёркой
 * каждая таблица занимает один взгляд, а глубина уходит в пейджер. Поэтому же
 * с сервера берётся не двадцать строк, а сотня: длина списка больше не равна
 * длине страницы, и подробность ничего не стоит.
 *
 * ДВА СПИСКА ЗДЕСЬ ПОИМЁННЫЕ — перепись читателей (постановка 4.6: «какие
 * сотрудники пользовались Wiki за период») и просрочка ознакомлений. Оба
 * сужаются границей отдела на сервере и оба помечены бейджем: у супервайзера
 * и у директора числа в них РАЗНЫЕ, и без пометки это читается как расхождение
 * данных. Остальные разрезы сводные и не сужаются.
 *
 * ГДЕ ЖИВУТ ОБЪЯСНЕНИЯ. Правило владельца от 25.08.2026: на экране остаются
 * ЧИСЛА, НАЗВАНИЯ И ЧИПЫ, любая объясняющая фраза уходит под «i» (IosHint) —
 * туда же, где она и нужна: к своей плитке, к своей таблице, к своему блоку.
 * Прежде эти фразы стояли открытым текстом под каждым числом и заголовком:
 * шестнадцать серых строк на экран, из-за которых отчёт перестают читать
 * целиком. Подвала «как это посчитано» тоже нет — он был набором верных
 * предложений внизу длинной страницы, до которых не доскроллили.
 *
 * На виду остаётся только то, что короче фразы и меняет чтение числа: чип
 * периода у блока («за выбранный период» против «на сейчас») и бейдж сужения
 * у поимённых списков. Текст самих оговорок по-прежнему приходит с сервера —
 * он про то, как устроены данные, и меняется вместе с запросами.
 *
 * ЧТО ЗДЕСЬ НАЗЫВАЕТСЯ СВОИМИ ИМЕНАМИ. Внутренних слов на экране нет:
 * «периметр», «контент-долг» и «свёртка префиксов» понятны тому, кто писал
 * запросы, и никому больше. Причины «без ответа» подписаны действием, которое
 * из них следует, — легенда под таблицей показывает только те, что реально
 * встретились в выборке.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/** Одна пустота на все таблицы — см. комментарий у списков ниже. */
const EMPTY = [];

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

/* Блок отчёта: заголовок, чип периода и подсказка «i» с вопросом, на который
 * блок отвечает.
 *
 * Чип нужен именно здесь, а не только наверху: блоки живут в РАЗНОМ времени —
 * чтение за выбранный отрезок, ознакомления на сейчас. Пока это стояло серой
 * припиской у заголовка, разница читалась как «фильтр не сработал». Чип —
 * два слова, и он остаётся на виду; предложение про смысл блока уходит под «i».
 */
const Group = ({ title, help, scope, icon: Icon, children }) => (
    <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 px-1">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                {Icon && <Icon size={15} className="text-indigo-600" />}{title}
            </h2>
            {help && <IosHint text={help} label={`О чём блок «${title}»`} />}
            {scope && <IosBadge tone="slate">{scope}</IosBadge>}
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

/* Расшифровка причин — текстом внутрь подсказки «i» у таблицы.
 *
 * Легенда нужна: колонка «Почему без ответа» состоит из бейджей, и «Ответ
 * придержан» сам по себе не подсказывает никакого действия. А действия у
 * причин разные — написать статью, выдать доступ, уточнить числа в статье.
 *
 * Но расшифровка нужна ОДИН РАЗ, а место под таблицей занимала всегда: пять
 * строк пояснений под таблицей из пяти строк — это ровно тот шум, из-за
 * которого перестают читать и сам отчёт. В подсказку попадают только те
 * причины, что встретились в выборке. */
const reasonHelp = (items) => {
    const seen = new Set((items || []).map((row) => row.reason));
    return Object.keys(REASON)
        .filter((key) => seen.has(key))
        .map((key) => `«${REASON[key].label}» — ${REASON[key].help}.`)
        .join(' ');
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
       узнать их можно было только наведением на конкретный столбик. Два слова
       у образца цвета — это подпись, а не пояснение; всё, что длиннее, уходит
       под «i» рядом с названием графика. */
    return (
        <div className={`${iosCard} p-3`}>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-1">
                <div className="flex items-center gap-1.5">
                    <div className="text-[12.5px] font-semibold text-slate-700">
                        {grain === 'week' ? 'Прочтения по неделям' : 'Прочтения по дням'}
                    </div>
                    {grain === 'week' && (
                        <IosHint
                            label="Почему по неделям"
                            text="Период длиннее двух месяцев, поэтому график собран по неделям — по дням он превратился бы в частокол из трёхсот столбиков. Линия при этом показывает не сумму читателей за неделю, а самый людный день в ней: один и тот же человек, заходивший трижды, иначе посчитался бы за троих."
                        />
                    )}
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
                                        spaceName = '', onOpenArticle = null }) {
    const toast = useStableCallback(showToast);

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [downloading, setDownloading] = useState(false);
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
       на каждый выбор, и по ссылке запрос уходил бы даже за тем же периодом.

       Потолок строк ВЫСОКИЙ, потому что на экране всё равно видно пять: до
       пейджера длина списка была длиной страницы, и двадцать строк были
       компромиссом между «видно достаточно» и «не отодвигает всё остальное».
       Теперь глубина не стоит места, а долистать до конца сотни — это и есть
       та подробность, за которой сюда приходят. Сотня — потолок сервера
       (MAX_ROWS), больше он всё равно не отдаст. */
    const params = useMemo(() => ({
        since: range.from || undefined,
        until: range.to || undefined,
        space_id: spaceId || undefined,
        limit: 100,
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

    /* Выгрузка: тот же отчёт, но целиком и в Excel.
     *
     * Зачем она нужна при живом экране. Экран отвечает на вопрос «работает ли
     * вики» и ради этого режет каждую таблицу пятью строками; из него нельзя
     * ни отфильтровать просрочку по своей группе, ни свести прочтения по
     * отделам за квартал, ни отправить список тому, у кого вкладки нет вовсе.
     * Файл ровно за этим и берут — поэтому в нём нет ни потолка в пять строк,
     * ни экранной сотни: сервер собирает всё, что нашлось за период.
     *
     * Файл забираем axios'ом, а не ссылкой: раздел авторизуется заголовком, а
     * обычная ссылка заголовков не несёт — вместо книги пришла бы страница
     * входа. Отдаём его браузеру временной ссылкой на blob.
     *
     * Период и пространство уходят те же, что на экране: выгрузка обязана
     * повторять то, на что человек сейчас смотрит, — иначе её открывают и
     * видят другие числа. А вот `limit` снимаем: это потолок ЭКРАНА, и с ним
     * файл обрезался бы ровно там, где начинается то, ради чего его просили. */
    const download = useCallback(() => {
        setDownloading(true);
        return axios.get(`${base}/analytics/export`, {
            headers, responseType: 'blob', params: { ...params, limit: undefined },
        })
            .then((r) => {
                const url = URL.createObjectURL(new Blob([r.data]));
                const link = document.createElement('a');
                link.href = url;
                /* Имя с пространством: у Тез и Таксопарков отчёты разные, и
                   две «Аналитика вики.xlsx» в загрузках различаются только
                   припиской «(1)». */
                link.download = spaceName
                    ? `Аналитика вики — ${spaceName}.xlsx` : 'Аналитика вики.xlsx';
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            })
            .catch((e) => toast(errText(e, 'Не удалось собрать выгрузку'), 'error'))
            .finally(() => setDownloading(false));
    }, [base, headers, params, spaceName, toast]);

    const reading = data?.reading;
    const ack = data?.acknowledgements;
    const demand = data?.demand;
    const content = data?.content;

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

    /* Бейдж сужения повторяется у ОБОИХ поимённых списков — переписи читателей
       и просрочки: числа в них у супервайзера и у директора разные, и увидеть
       пометку надо там, где смотришь, а не там, где вспомнил. */
    const scopedBadge = data?.scoped ? (
        <span className="flex items-center gap-1.5">
            <IosBadge tone="blue">только ваши отделы</IosBadge>
            <IosHint text={notes.scoped} align="right" label="Почему список сужен" />
        </span>
    ) : null;

    /* Массивы для таблиц — ОДНОЙ ссылкой на пустоту (EMPTY), а не `|| []`:
       PagedTable возвращается к первой странице, когда меняется ссылка на
       список, и новый литерал на каждом рендере сбрасывал бы страницу
       постоянно — например, от тика загрузки соседнего блока. */
    const departments = reading?.departments || EMPTY;
    const top = reading?.top || EMPTY;
    const unread = reading?.unread || EMPTY;
    const people = reading?.people || EMPTY;
    const sections = content?.sections || EMPTY;
    const stale = content?.stale || EMPTY;
    const ackDepartments = ack?.departments || EMPTY;
    const ackOverdue = ack?.overdue || EMPTY;
    const demandItems = demand?.items || EMPTY;

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
                {/* Кнопка стоит здесь, а не над каждой таблицей: выгружается
                    весь отчёт за выбранный период, и место ей — рядом с тем,
                    что этот период задаёт. Девять кнопок «выгрузить» у девяти
                    таблиц дали бы девять файлов и ту же работу по их сборке
                    вручную. */}
                <button
                    type="button"
                    className={`${iosBtnSecondary} ml-auto`}
                    onClick={download}
                    disabled={downloading}
                >
                    {downloading
                        ? <Loader2 size={15} className="animate-spin" />
                        : <Download size={15} />}
                    Выгрузить в Excel
                </button>
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
                help="Пользуются ли вики: сколько читают, кто читает и что лежит нетронутым."
            >
                <Tiles>
                    <Metric
                        label="Прочтений" value={num(t.reads)}
                        help={`Открытий за период — ${num(t.opens)}, прочтений из них ${num(t.reads)}. ${notes.read || ''}`}
                    />
                    <Metric
                        label="Читателей" value={num(t.readers)}
                        help="Сколько человек открыли за период хотя бы одну статью."
                    />
                    <Metric
                        label="Охват статей"
                        value={t.coverage === null || t.coverage === undefined
                            ? '—' : `${t.coverage}%`}
                        help={`За период открыли ${num(t.articles_read)} из ${num(t.published)} опубликованных статей. Черновики в знаменатель не входят: показатель не должен падать от того, что кто-то начал писать новую статью.`}
                        tone={t.coverage === null || t.coverage === undefined ? null
                            : t.coverage >= 60 ? 'good' : t.coverage >= 30 ? 'warn' : 'bad'}
                    />
                    {/* Число берётся из итогов, а НЕ из длины списка ниже:
                        список режется потолком строк, и плитка показывала бы
                        «не открывали 20» при пятидесяти семи нетронутых. */}
                    <Metric
                        label="Статей без чтений"
                        value={num(t.unread)}
                        helpAlign="right"
                        help="Опубликованные статьи, которые за период не открыли ни разу. Поимённый список — в таблице «Не открывали ни разу за период» ниже."
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
                <PagedTable
                    title="Кто читает: по отделам" icon={Users}
                    rows={departments}
                    empty="За период никто не читал."
                    help="«Доля отдела» — сколько человек из штата отдела заходили в вики хотя бы раз за период. Уволенные и уволившиеся в знаменатель не входят, отпуск, больничный и Б/С — входят: человек в отпуске остаётся сотрудником, и вики адресована ему тоже. Отдел берётся тот, в котором человек был на момент чтения."
                    head={(
                        <tr>
                            <Th>Отдел</Th>
                            <Th right>Читателей</Th>
                            <Th right>Доля отдела</Th>
                            <Th right>Прочтений</Th>
                            <Th right>Статей</Th>
                        </tr>
                    )}
                    renderRow={(row) => (
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
                    )}
                />

                <PagedTable
                    title="Что читают чаще всего" icon={BookOpen}
                    rows={top}
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
                    renderRow={(row) => (
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
                    )}
                />

                <PagedTable
                    title="Не открывали ни разу за период" icon={EyeOff}
                    rows={unread}
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
                    renderRow={(row) => {
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
                    }}
                />

                {/* Перепись читателей — требование 4.6 «какие сотрудники
                    пользовались Wiki за период». Поимённо, поэтому сужается
                    границей отдела так же, как просрочка ознакомлений, и по
                    той же причине потолок строк подписывается только там, где
                    сужения нет. */}
                <PagedTable
                    title="Кто пользовался вики" icon={Users}
                    rows={people}
                    total={data?.scoped ? null : t.readers}
                    badge={scopedBadge}
                    empty="За период вики никто не пользовался."
                    help="Поимённо: кто открывал вики за выбранный период. Прочтение — человек, статья и минута, поэтому обновление страницы не удваивает счёт. Отдел показан тот, в котором человек был на момент последнего чтения: перешедший не уносит прошлые чтения в новый отдел."
                    head={(
                        <tr>
                            <Th>Человек</Th>
                            <Th>Отдел</Th>
                            <Th right>Прочтений</Th>
                            <Th right>Статей</Th>
                            <Th right>Последний заход</Th>
                        </tr>
                    )}
                    renderRow={(row) => (
                        <tr key={row.user_id}>
                            <Td>{row.name}</Td>
                            <Td muted>{row.department}</Td>
                            <Td right>{num(row.reads)}</Td>
                            <Td right>{num(row.articles)}</Td>
                            <Td right muted>{day(row.last_at)}</Td>
                        </tr>
                    )}
                />
            </Group>

            {/* ── Блок 2. Содержимое базы ────────────────────────────────── */}
            <Group
                title="Содержимое базы" icon={Library} scope="на сейчас"
                help="В каком состоянии сама база: что где лежит, кто её ведёт и что давно не трогали."
            >
                <PagedTable
                    title="Разделы" icon={FolderTree}
                    rows={sections}
                    empty="Разделов в этом пространстве вам не видно."
                    help="Считаются статьи раздела, которые видны вам, — вместе с черновиками; «Опубликовано» из них выделено отдельно. «Правили за период» — кто сохранял версии статей раздела за выбранный период, тройка самых частых. Пустой раздел показан намеренно: «завели и не наполнили» — это находка."
                    head={(
                        <tr>
                            <Th>Раздел</Th>
                            <Th right>Статей</Th>
                            <Th right>Опубликовано</Th>
                            <Th right>Последняя правка</Th>
                            <Th>Правили за период</Th>
                        </tr>
                    )}
                    renderRow={(row) => (
                        <tr key={row.id}>
                            <Td>
                                {row.parent && (
                                    <span className="text-slate-400">{row.parent} / </span>
                                )}
                                {row.name}
                            </Td>
                            <Td right>{num(row.articles)}</Td>
                            <Td right muted={!row.published}>{num(row.published)}</Td>
                            <Td right muted={!row.last_update}>
                                {row.last_update ? day(row.last_update) : 'не правили'}
                            </Td>
                            <Td muted={!(row.editors || []).length}>
                                {(row.editors || []).length
                                    ? row.editors.map((e) => `${e.name} · ${e.edits}`).join(', ')
                                    : '—'}
                            </Td>
                        </tr>
                    )}
                />

                {/* Устаревшее ≠ непрочитанное. Там статью не ЧИТАЛИ, здесь её не
                    ПИСАЛИ: первую надо показать людям, вторую перечитать автору. */}
                <PagedTable
                    title="Давно не обновляли" icon={History}
                    rows={stale}
                    total={content?.stale_total}
                    empty={`Статей старше ${num(content?.stale_days)} дней нет — базу обновляют.`}
                    help={`Устаревшей считается опубликованная статья, которую не правили дольше ${num(content?.stale_days)} дней; первыми идут самые давние. Признак «просрочен пересмотр» появляется у статей, которым проставили срок пересмотра и он прошёл: срок заполняют не у всех, поэтому отбор идёт по дате последней правки, а не по нему.`}
                    head={(
                        <tr>
                            <Th>Статья</Th>
                            <Th>Раздел</Th>
                            <Th>Правил последним</Th>
                            <Th right>Не обновляли</Th>
                        </tr>
                    )}
                    renderRow={(row) => (
                        <tr key={row.id}>
                            <Td>
                                <ArticleLink row={row} onOpen={onOpenArticle} />
                                {row.review_overdue && (
                                    <IosBadge tone="amber" className="ml-2">
                                        просрочен пересмотр
                                    </IosBadge>
                                )}
                            </Td>
                            <Td muted>{row.section || '—'}</Td>
                            <Td muted>{row.editor}</Td>
                            <Td right>
                                {num(row.days)} дн.
                                <div className="text-[11px] text-slate-500">
                                    {day(row.updated_at)}
                                </div>
                            </Td>
                        </tr>
                    )}
                />
            </Group>

            {/* ── Блок 3. Ознакомления ───────────────────────────────────── */}
            <Group
                title="Ознакомления" icon={ShieldAlert} scope="на сейчас"
                help={['Выполнено ли обязательное: кто не подтвердил назначенные статьи.',
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
                                help="Срок вышел, а подтверждения нет. Отменённые и перевыпущенные назначения не считаются."
                            />
                            <Metric
                                label="Не открывали" value={num(at.not_open)}
                                help="Статью назначили, но человек не открывал её ни разу."
                                tone={at.not_open > 0 ? 'warn' : null}
                            />
                            <Metric
                                label="Подтверждено" value={num(at.done)}
                                help={`Подтверждено ${num(at.done)} из ${num(at.total)} живых назначений.`}
                            />
                            <Metric
                                label="Людей" value={num(at.people)}
                                helpAlign="right"
                                help={`Скольким людям что-то назначено; статей в назначениях — ${num(at.articles)}.`}
                            />
                        </Tiles>

                        <PagedTable
                            title="Ознакомления по отделам" icon={Users}
                            rows={ackDepartments}
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
                            renderRow={(row) => (
                                <tr key={row.department_id ?? 'none'}>
                                    <Td>{row.name}</Td>
                                    <Td right>{num(row.total)}</Td>
                                    <Td right><Bar done={row.done} total={row.total} /></Td>
                                    <Td right>
                                        {row.overdue > 0
                                            ? <span className="font-medium text-rose-600">{num(row.overdue)}</span>
                                            : <span className="text-slate-500">0</span>}
                                    </Td>
                                </tr>
                            )}
                        />

                        {/* Список сужен по отделу — и это видно рядом с ним,
                            а не только оговоркой внизу страницы: у супервайзера
                            и у директора числа здесь РАЗНЫЕ, и без пометки
                            расхождение читается как поломка данных. Потолок
                            строк подписан там же («· 20 из 57»), но только
                            когда сужения нет: иначе обрез по правам и обрез по
                            потолку слились бы в одно число. */}
                        <PagedTable
                            title="Просрочено поимённо" icon={ShieldAlert}
                            rows={ackOverdue}
                            total={data?.scoped ? null : at.overdue}
                            badge={scopedBadge}
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
                            renderRow={(row) => (
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
                            )}
                        />
                    </>
                )}
            </Group>

            {/* ── Блок 4. Спрос, на который вика не отвечает ─────────────── */}
            <Group
                title="Чего не хватает в базе" icon={Search} scope="за выбранный период"
                help="Что искали и о чём спрашивали помощника, но ответа не нашли, — темы для новых статей."
            >
                <Tiles>
                    <Metric
                        label="Запросов в поиске" value={num(ds.total)}
                        help={[
                            'Запросы к поиску по вики за период. Поле ищет по мере набора, поэтому одна фраза приезжает пятью запросами-огрызками: «дог», «догов», «договор» — это один запрос, а не три, и здесь они склеены.',
                            ds.steps && ds.steps !== ds.total
                                ? `До склейки обращений было ${num(ds.steps)}.` : null,
                            ds.logging_since
                                ? `Журнал поиска ведётся с ${day(ds.logging_since)} — за более ранние дни запросов не сохранилось.`
                                : 'Журнал поиска пуст: запросы начали записываться только что.',
                        ].filter(Boolean).join(' ')}
                    />
                    <Metric
                        label="Ничего не нашли" value={num(ds.empty)}
                        help={ds.empty_share === null || ds.empty_share === undefined
                            ? 'Запросы, на которые поиск не отдал ни одной статьи.'
                            : `Запросы, на которые поиск не отдал ни одной статьи, — ${ds.empty_share}% всех запросов за период.`}
                        tone={ds.empty_share === null || ds.empty_share === undefined ? null
                            : ds.empty_share >= 20 ? 'bad' : ds.empty_share >= 10 ? 'warn' : 'good'}
                    />
                    <Metric
                        label="Вопросов помощнику" value={num(da.total)}
                        help={`Ответов помощника за период${da.people ? `; спрашивали ${num(da.people)} человек` : ''}.`}
                    />
                    <Metric
                        label="Помощник не нашёл" value={num(da.no_answer)}
                        tone={da.total && da.no_answer / da.total >= 0.25 ? 'warn' : null}
                        helpAlign="right"
                        help={`Ответы, в которых помощник прямо сказал, что ответа в вики нет.${da.clarify ? ` Ещё ${num(da.clarify)} раз он попросил уточнить вопрос` : ' Когда он просит уточнить вопрос'} — такие обращения в дыры базы знаний не записываются.`}
                    />
                </Tiles>

                <PagedTable
                    title="Темы без ответа" rows={demandItems}
                    empty={ds.logging_since
                        ? 'За период всё, что искали и спрашивали, находилось.'
                        : 'Журнал поиска пуст — запросы начали записываться только что.'}
                    help={['Кто именно спрашивал, здесь не показано намеренно: список отвечает на вопрос «какой статьи не хватает», а не «кто не знал». Одинаковые запросы разных людей склеены в одну строку, поэтому «Спрашивали» больше, чем «Людей».',
                           reasonHelp(demandItems)].filter(Boolean).join(' ')}
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
                    renderRow={(row) => {
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
                    }}
                />
            </Group>
        </div>
    );
}
