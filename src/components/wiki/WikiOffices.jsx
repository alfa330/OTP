import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    ChevronLeft, ChevronRight, Columns2, Loader2, MapPin, Plus, Rows3, Search,
    Table2,
} from 'lucide-react';
import {
    iosCard, iosInput, iosBtnPrimary, iosBtnSecondary,
    IosBadge, IosModal,
} from '../ui/ios';
import useStableCallback from './useStableCallback';
import OfficeEditor from './OfficeEditor';
import OfficeFilters, { DEFAULT_FILTERS, SORT_OPTIONS } from './OfficeFilters';
import OfficeTable from './OfficeTable';
import OfficeDayModal from './OfficeDayModal';
import OfficeInfoModal from './OfficeInfoModal';
import { OfficeStatusBadge } from './officeBadges';
import { officeTodayISO } from './officeSchedule';
import {
    DAY_LEGEND, DAY_STATE_EDGE, closureCovers, formatDay, officeDayStatus,
} from './officeDayStatus';

/* Офисы: адреса, карта, график и привязка к таксопаркам.
 *
 * Раздел заменяет статью «Адреса офисов», где один и тот же адрес был переписан
 * в таблице каждого парка. Здесь запись одна на физический офис, а парки к ней
 * привязываются; отличия телефона у конкретного парка живут в связи.
 *
 * Второй вопрос раздела — «работает ли офис в этот день». На него отвечает дата
 * в панели: сегодня статус живой (до минуты), за прошлый день — отметка
 * дежурного или ночной снимок, а если за день ничего не записано, расчёт по
 * графику. Правило одно на карточку и таблицу: officeDayStatus.js.
 *
 * Карточка и строка таблицы показывают только адрес и статус; карта, часы,
 * телефоны и парки открываются нажатием — OfficeInfoModal. Решение владельца:
 * двадцать карточек «со всем сразу» превращали раздел в простыню.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* «3 офиса» рядом с городом: заголовок отвечает не только «где», но и «сколько
   искать». Число словами не заменяется — оператор ищет глазами цифру. */
const officeCount = (count) => {
    const tail = count % 100 >= 11 && count % 100 <= 14 ? 0 : count % 10;
    return `${count} ${tail === 1 ? 'офис' : tail >= 2 && tail <= 4 ? 'офиса' : 'офисов'}`;
};

const emptyDraft = () => ({
    name: '', city: '', address: '', address_note: '', phone: '',
    map_url: '', map_resolved_url: null, lat: null, lon: null,
    schedule: {}, no_office: false,
    kind: 'park', partner_label: '',
});

const draftFrom = (office) => ({
    id: office.id,
    name: office.name || '',
    city: office.city || '',
    address: office.address || '',
    address_note: office.address_note || '',
    phone: office.phone || '',
    map_url: office.map_url || '',
    map_resolved_url: office.map_resolved_url || null,
    lat: office.lat ?? null,
    lon: office.lon ?? null,
    schedule: office.schedule || {},
    no_office: !!office.no_office,
    kind: office.kind || 'park',
    partner_label: office.partner_label || '',
});

const OfficeCard = ({ office, onOpen, dayISO, isToday, showCity, tick }) => {
    const status = officeDayStatus(office, dayISO);
    const absent = status.state === 'absent';

    return (
        <div className={`${iosCard} relative overflow-hidden before:absolute before:inset-y-0 before:left-0 before:z-10 before:w-[3px] ${DAY_STATE_EDGE[status.state] || ''}`}>
            {/* Вся карточка — одна кнопка: цель нажатия «адрес» на ощупь должна
                быть строкой, а не подчёркнутыми буквами в ней. Кнопки правки
                здесь больше нет намеренно — вложенная кнопка в кнопке
                недопустима, а действия и так стоят в подвале модалки. */}
            <button
                type="button"
                onClick={() => onOpen(office)}
                aria-label={`${office.name}: подробнее`}
                className="flex w-full items-center gap-3 py-3 pl-4 pr-3 text-left transition hover:bg-slate-50"
            >
                <div className="min-w-0 flex-1">
                    {/* Без заголовков городов город обязан быть на самой
                        карточке: «Навигатор» без него не адрес, а слово. */}
                    {showCity && office.city && office.city !== office.name && (
                        <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                            {office.city}
                        </div>
                    )}
                    <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[14.5px] font-semibold text-slate-900">
                            {office.name}
                        </span>
                        {office.status === 'archived' && <IosBadge tone="amber">В архиве</IosBadge>}
                        {office.kind === 'partner' && (
                            <IosBadge tone="blue">{office.partner_label || 'Партнёрский'}</IosBadge>
                        )}
                    </div>

                    <div className="mt-0.5 flex items-start gap-1.5 text-[13px] leading-relaxed text-slate-600">
                        <MapPin size={13} className="mt-[3px] shrink-0 text-slate-400" />
                        {absent ? (
                            <span className="italic text-slate-500">Офиса в городе нет</span>
                        ) : (
                            <span className="min-w-0">{office.address || '—'}</span>
                        )}
                    </div>
                </div>

                <OfficeStatusBadge
                    schedule={office.schedule}
                    status={status}
                    isToday={isToday}
                    dayISO={dayISO}
                    tick={tick}
                />
                {/* Шеврон — единственное, что отличает карточку от плашки:
                    без него «нажми, чтобы увидеть остальное» приходится
                    угадывать. */}
                <ChevronRight size={16} className="shrink-0 text-slate-400" />
            </button>
        </div>
    );
};

const VIEWS = [
    { key: 'cards1', label: 'Одна в ряд', icon: Rows3 },
    { key: 'cards2', label: 'Две в ряд', icon: Columns2 },
    { key: 'table', label: 'Таблица', icon: Table2 },
];

const PREFS_KEY = 'wiki.offices.view';

/** Сдвиг календарной даты на дни. UTC-полдень, чтобы переход через месяц и
 *  летнее время не уводили день на соседний. */
const shiftDay = (dayISO, days) => {
    const [y, m, d] = String(dayISO).split('-').map(Number);
    const date = new Date(Date.UTC(y, m - 1, d));
    date.setUTCDate(date.getUTCDate() + days);
    return date.toISOString().slice(0, 10);
};

/* Поле даты набрано классами iosInput без w-full: рядом с w-full класс w-auto
   ничего не перебивает (побеждает порядок в CSS, а не в атрибуте), и поле
   растягивалось на всю строку панели. */
const dateInput = 'shrink-0 rounded-xl bg-slate-100 px-3.5 py-2.5 text-[14px] tabular-nums '
    + 'font-medium text-slate-900 border-0 ring-1 ring-slate-200 transition '
    + 'focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70';

/* Вид и сортировку помним между заходами: раздел открывают каждый день, и
 * выбирать «таблицу» заново каждое утро — работа, которую делать не надо.
 * В приватном режиме Safari доступ к localStorage бросает, поэтому обе стороны
 * в try/catch: настройка второстепенна, падать из-за неё нельзя.
 *
 * По умолчанию — таблица: раздел заводили под вопрос «где сегодня закрыто»
 * (критерий приёмки ТЗ №2 — при открытии виден статус всех офисов на сегодня),
 * а карточками двадцать городов одним экраном не показать. Кто выбрал карточки,
 * получает их и завтра: выбор человека сильнее умолчания. */
const readPrefs = () => {
    try {
        const raw = JSON.parse(window.localStorage.getItem(PREFS_KEY) || '{}');
        return {
            view: VIEWS.some((item) => item.key === raw.view) ? raw.view : 'table',
            sort: SORT_OPTIONS.some((item) => item.key === raw.sort) ? raw.sort : DEFAULT_FILTERS.sort,
        };
    } catch {
        return { view: 'table', sort: DEFAULT_FILTERS.sort };
    }
};

export default function WikiOffices({ base, headers, showToast }) {
    const toast = useStableCallback(showToast);
    const prefs = useMemo(readPrefs, []);

    const [offices, setOffices] = useState([]);
    const [cities, setCities] = useState([]);
    const [parks, setParks] = useState([]);
    const [canManage, setCanManage] = useState(false);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [query, setQuery] = useState('');
    const [filters, setFilters] = useState({ ...DEFAULT_FILTERS, sort: prefs.sort });
    const [view, setView] = useState(prefs.view);
    const [dayISO, setDayISO] = useState(officeTodayISO);
    const [dayTarget, setDayTarget] = useState(null);
    const [infoTarget, setInfoTarget] = useState(null);
    const [stateFilter, setStateFilter] = useState('');
    const [draft, setDraft] = useState(null);
    const [tick, setTick] = useState(0);

    const today = officeTodayISO();
    const isToday = dayISO === today;

    useEffect(() => {
        try {
            window.localStorage.setItem(PREFS_KEY, JSON.stringify({ view, sort: filters.sort }));
        } catch {
            // Приватный режим — молча живём без запомненного вида.
        }
    }, [view, filters.sort]);

    // Минутный тик оживляет бейдж «Открыто до 19:00»: карточку держат открытой
    // весь день, и статус, посчитанный при загрузке, к обеду уже врал бы.
    useEffect(() => {
        const timer = setInterval(() => setTick((n) => n + 1), 60_000);
        return () => clearInterval(timer);
    }, []);

    const load = useCallback(() => {
        setLoading(true);
        const params = { date: dayISO };
        if (query.trim()) params.q = query.trim();
        if (filters.city) params.city = filters.city;
        if (filters.parkId) params.park_id = filters.parkId;
        if (filters.showArchived) params.archived = 1;

        Promise.all([
            axios.get(`${base}/offices`, { headers, params }),
            axios.get(`${base}/parks`, { headers }),
        ])
            .then(([officeResponse, parkResponse]) => {
                setOffices(officeResponse.data?.items || []);
                setCities(officeResponse.data?.cities || []);
                setCanManage(!!officeResponse.data?.can_manage);
                setParks((parkResponse.data?.items || []).filter((p) => p.status === 'active'));
            })
            .catch((e) => toast(errText(e, 'Не удалось загрузить офисы'), 'error'))
            .finally(() => setLoading(false));
    }, [base, headers, query, dayISO, filters.city, filters.parkId, filters.showArchived, toast]);

    useEffect(() => {
        const timer = setTimeout(load, query ? 250 : 0);
        return () => clearTimeout(timer);
    }, [load, query]);

    const save = () => {
        const payload = {
            name: draft.name.trim(),
            city: draft.city || null,
            address: draft.address || null,
            address_note: draft.address_note || null,
            phone: draft.phone || null,
            map_url: draft.map_url || null,
            map_resolved_url: draft.map_resolved_url,
            lat: draft.lat,
            lon: draft.lon,
            schedule: draft.schedule,
            no_office: !!draft.no_office,
            kind: draft.kind,
            partner_label: draft.kind === 'partner' ? (draft.partner_label || null) : null,
        };
        setBusy(true);
        const request = draft.id
            ? axios.patch(`${base}/offices/${draft.id}`, payload, { headers })
            : axios.post(`${base}/offices`, payload, { headers });
        request
            .then(() => { toast(draft.id ? 'Офис обновлён' : 'Офис добавлен', 'success'); setDraft(null); load(); })
            .catch((e) => toast(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setBusy(false));
    };

    const archive = (office) => {
        setBusy(true);
        axios.delete(`${base}/offices/${office.id}`, { headers })
            .then(() => { toast('Офис убран в архив', 'success'); load(); })
            .catch((e) => toast(errText(e, 'Не удалось'), 'error'))
            .finally(() => setBusy(false));
    };

    const restore = (office) => {
        setBusy(true);
        axios.patch(`${base}/offices/${office.id}`, { status: 'active' }, { headers })
            .then(() => { toast('Офис возвращён из архива', 'success'); load(); })
            .catch((e) => toast(errText(e, 'Не удалось вернуть'), 'error'))
            .finally(() => setBusy(false));
    };

    const copyAddress = (office) => {
        const text = [office.city, office.address].filter(Boolean).join(', ');
        if (!text) return;
        // clipboard.writeText есть не всюду (http-контекст, старые вебвью), и
        // молчаливый отказ выглядел бы как «кнопка не работает».
        const done = () => toast('Адрес скопирован', 'success');
        if (navigator.clipboard?.writeText) {
            navigator.clipboard.writeText(text).then(done)
                .catch(() => toast('Не удалось скопировать — выделите адрес вручную', 'error'));
            return;
        }
        toast('Копирование недоступно в этом браузере', 'error');
    };

    /* Отметка на день и закрытие на срок — два разных ответа на вопрос «офис
       работает?», и одновременно им висеть нельзя: отметка сильнее срока, и
       оставленная рядом со сроком она молча перебивала бы его первый день.
       Поэтому что записываем, то и оставляем, а второе снимаем. */
    const markDay = (state, note, term) => {
        const office = dayTarget;
        const day = `${base}/offices/${office.id}/day/${dayISO}`;
        const closure = `${base}/offices/${office.id}/closure`;
        const period = state === 'closed' && term?.kind !== 'day';

        /* Закрытие уже идёт — правим его, а не заводим новое: срок у закрытия
           «по техническим причинам» сплошь и рядом появляется потом, когда его
           наконец назвали. Начало при этом обязано остаться прежним, иначе
           добавление даты сдвигало бы его на сегодня, и уже прошедшие дни
           ремонта возвращались к графику — в истории офис «работал» во время
           ремонта. Закончившееся закрытие таким не считается: там начинается
           новое, с сегодняшнего дня. */
        const from = closureCovers(office, dayISO) ? office.closed_from : dayISO;

        const requests = period
            ? [axios.put(closure, { from, until: term.until || null, note: note || null },
                         { headers }),
               axios.delete(day, { headers })]
            : [axios.put(day, { state, note: note || null }, { headers })];
        // Снимаем срок, только если он был: лишний DELETE трогал бы updated_at
        // и колонка «Обновлено» врала бы о правке, которой не было.
        if (!period && office.closed_from) requests.push(axios.delete(closure, { headers }));

        setBusy(true);
        Promise.all(requests)
            .then(() => {
                toast(period ? 'Срок закрытия сохранён' : 'Статус на дату сохранён', 'success');
                setDayTarget(null);
                load();
            })
            .catch((e) => toast(errText(e, 'Не удалось сохранить статус'), 'error'))
            .finally(() => setBusy(false));
    };

    const clearDay = () => {
        const office = dayTarget;
        setBusy(true);
        // Для дежурного «считать по графику» — одно действие, а не выбор между
        // отметкой и сроком: снимаем оба.
        Promise.all([
            axios.delete(`${base}/offices/${office.id}/day/${dayISO}`, { headers }),
            ...(office.closed_from
                ? [axios.delete(`${base}/offices/${office.id}/closure`, { headers })] : []),
        ])
            .then(() => { toast('Офис снова считается по графику', 'success'); setDayTarget(null); load(); })
            .catch((e) => toast(errText(e, 'Не удалось снять отметку'), 'error'))
            .finally(() => setBusy(false));
    };

    /* Сортировка на клиенте: офисы уже в состоянии, и ходить за тем же списком
       в другом порядке было бы лишним запросом. */
    const collator = useMemo(() => new Intl.Collator('ru'), []);

    const sorted = useMemo(() => {
        const items = [...offices];
        const byCity = (a, b) => collator.compare(a.city || '', b.city || '');
        const byName = (a, b) => collator.compare(a.name || '', b.name || '');
        // «Сначала открытые» — порядок по срочности вопроса: сначала то, что
        // работает, в конце то, чего нет вообще.
        const order = ['open', 'online', 'closed', 'none', 'absent'];
        const rank = (office) => order.indexOf(officeDayStatus(office, dayISO).state);

        switch (filters.sort) {
            case 'city_desc': items.sort((a, b) => byCity(b, a) || byName(a, b)); break;
            case 'name_asc': items.sort(byName); break;
            case 'name_desc': items.sort((a, b) => byName(b, a)); break;
            case 'status': items.sort((a, b) => rank(a) - rank(b) || byCity(a, b) || byName(a, b)); break;
            // «Как в справочнике» — порядок сервера (position), его не трогаем.
            case 'manual': break;
            default: items.sort((a, b) => byCity(a, b) || byName(a, b));
        }
        return items;
    }, [offices, filters.sort, dayISO, collator]);

    /* Сколько офисов в каком состоянии на выбранную дату. Считается по всей
       загруженной выборке, а не по отфильтрованной — иначе счётчик менялся бы
       от собственного нажатия. */
    const counts = useMemo(() => {
        const result = { open: 0, closed: 0, absent: 0, none: 0 };
        offices.forEach((office) => {
            const state = officeDayStatus(office, dayISO).state;
            result[state] = (result[state] || 0) + 1;
        });
        return result;
    }, [offices, dayISO]);

    const visible = useMemo(() => (
        stateFilter
            ? sorted.filter((office) => officeDayStatus(office, dayISO).state === stateFilter)
            : sorted
    ), [sorted, stateFilter, dayISO]);

    /* Заголовки городов остаются только там, где город и задаёт порядок:
       внутри групп сортировка по названию или статусу была бы не видна, и
       выбранный пункт фильтра выглядел бы сломанным.

       В два ряда заголовки тоже нужны. Без них город оставался подписью на
       самой карточке, а карточки в два столбца читаются сверху вниз: Алматы,
       Алматы, Астана слева и Алматы, Астана, Караганда справа — деления по
       городам в этой раскладке не было видно вовсе. */
    const grouped = !!SORT_OPTIONS.find((item) => item.key === filters.sort)?.grouped;

    const groups = useMemo(() => {
        if (!grouped) return null;
        const order = [];
        const byCity = new Map();
        visible.forEach((office) => {
            const key = office.city || 'Без города';
            if (!byCity.has(key)) { byCity.set(key, []); order.push(key); }
            byCity.get(key).push(office);
        });
        return order.map((key) => ({ city: key, items: byCity.get(key) }));
    }, [visible, grouped]);

    // Одна карточка в ряд или две — выбор человека, а не порог экрана: прежняя
    // сетка включала вторую колонку сама от 2xl и никого не спрашивала. На узком
    // экране колонка всё равно одна: две по 300 px нечитаемы.
    const grid = view === 'cards2'
        ? 'grid grid-cols-1 gap-3 lg:grid-cols-2'
        : 'grid grid-cols-1 gap-3';

    const renderCard = (office, showCity) => (
        <OfficeCard
            key={office.id}
            office={office}
            onOpen={setInfoTarget}
            dayISO={dayISO}
            isToday={isToday}
            showCity={showCity}
            tick={tick}
        />
    );

    return (
        <div className="space-y-4">
            {/* Заголовок раздела из ТЗ. Он тут не украшение: вкладка называется
                «Офисы» и держит ещё справочник адресов с картой, а таблица ниже
                отвечает на другой вопрос — «работает ли офис в этот день». Пара
                «название + подпись» и говорит, что таблицей управляет дата
                справа, без чего календарь читается как фильтр по одному дню. */}
            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
                <div className="min-w-0">
                    <h2 className="text-[19px] font-semibold leading-tight tracking-tight text-slate-900">
                        Статус офисов по городам
                    </h2>
                    <p className="mt-0.5 text-[12.5px] text-slate-500">
                        Данные показаны на выбранную дату
                    </p>
                </div>

                {/* Дата — «на какой день смотрим». Вперёд смотреть нечего:
                    статус будущего дня — это график, а не факт. Стрелки нужны
                    затем, что типовой вопрос — «а позавчера?», и открывать ради
                    одного дня календарь незачем. */}
                <div className="flex shrink-0 items-center gap-1">
                    <button
                        type="button"
                        onClick={() => setDayISO(shiftDay(dayISO, -1))}
                        className="grid h-9 w-8 place-items-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                        aria-label="Предыдущий день"
                    >
                        <ChevronLeft size={16} />
                    </button>
                    <input
                        type="date"
                        className={dateInput}
                        value={dayISO}
                        max={today}
                        onChange={(e) => setDayISO(e.target.value || today)}
                        aria-label="Дата, на которую показан статус"
                    />
                    <button
                        type="button"
                        onClick={() => setDayISO(shiftDay(dayISO, 1))}
                        disabled={isToday}
                        className="grid h-9 w-8 place-items-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30 disabled:hover:bg-transparent"
                        aria-label="Следующий день"
                    >
                        <ChevronRight size={16} />
                    </button>
                    {!isToday && (
                        <button
                            type="button"
                            onClick={() => setDayISO(today)}
                            className="rounded-xl bg-slate-100 px-2.5 py-2 text-[12.5px] font-semibold text-slate-600 transition hover:bg-slate-200"
                        >
                            Сегодня
                        </button>
                    )}
                </div>
            </div>

            {/* Поиск, условия и вид — второй строкой. В одной с заголовком и
                датой они складывались в семь элементов подряд, и дата, которой
                таблица и управляется, стояла в этом ряду наравне с кнопкой
                «Офис». */}
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[200px] flex-1">
                    <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        className={`${iosInput} pl-10`}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Город, адрес или телефон"
                    />
                </div>

                <OfficeFilters
                    value={filters}
                    onChange={setFilters}
                    cities={cities}
                    parks={parks}
                    canManage={canManage}
                />

                <div className="flex shrink-0 rounded-xl bg-slate-100 p-1">
                    {VIEWS.map(({ key, label, icon: Icon }) => (
                        <button
                            key={key}
                            type="button"
                            onClick={() => setView(key)}
                            title={label}
                            aria-label={label}
                            aria-pressed={view === key}
                            className={`grid h-[34px] w-[34px] place-items-center rounded-[9px] transition-all ${
                                view === key
                                    ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                    : 'text-slate-500 hover:text-slate-700'
                            }`}
                        >
                            <Icon size={15} />
                        </button>
                    ))}
                </div>

                {canManage && (
                    <button type="button" className={iosBtnPrimary} onClick={() => setDraft(emptyDraft())}>
                        <Plus size={15} /> Офис
                    </button>
                )}
            </div>

            {/* Легенда и счётчики — одной полосой, и она же фильтр. Цветовую
                кодировку новый оператор читает без обучения (п. 4.4 ТЗ), а
                «где закрыто» находится одним нажатием вместо перебора списка.
                Отдельная легенда и отдельные счётчики были бы двумя строками
                про одно и то же.
                Залитая полоса, как в макете: три подписи вразброс по белому
                полю читались как случайные слова над таблицей, а не как ключ
                к её цветам. */}
            {!loading && offices.length > 0 && (
                <div className="flex flex-wrap items-center gap-1 rounded-xl bg-slate-100 px-2 py-1.5">
                    {DAY_LEGEND.map((item) => {
                        const count = counts[item.state] || 0;
                        const active = stateFilter === item.state;
                        return (
                            <button
                                key={item.state}
                                type="button"
                                disabled={!count && !active}
                                onClick={() => setStateFilter(active ? '' : item.state)}
                                className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12.5px] transition disabled:opacity-40 ${
                                    active
                                        ? 'bg-slate-900 font-medium text-white'
                                        : 'text-slate-700 enabled:hover:bg-white'
                                }`}
                            >
                                <span className={`h-2 w-2 shrink-0 rounded-full ${item.dot}`} />
                                {item.label}
                                <span className="font-semibold tabular-nums">{count}</span>
                            </button>
                        );
                    })}
                    {!isToday && (
                        <span className="ml-auto px-1.5 text-[12px] font-medium tabular-nums text-slate-500">
                            на {formatDay(dayISO)}
                        </span>
                    )}
                </div>
            )}

            {loading && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-slate-400`}>
                    <Loader2 size={16} className="animate-spin" />
                    <span className="text-[13px]">Загружаем…</span>
                </div>
            )}

            {!loading && visible.length === 0 && (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <MapPin size={22} />
                    </div>
                    <div className="text-[15px] font-semibold text-slate-900">
                        {query || filters.city || filters.parkId || stateFilter
                            ? 'Ничего не найдено' : 'Офисов пока нет'}
                    </div>
                    <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                        {query || filters.city || filters.parkId || stateFilter
                            ? 'Попробуйте изменить условия поиска.'
                            : canManage
                                ? 'Добавьте офис: адрес, ссылку 2ГИС, телефон и график. Парки привяжете в той же форме.'
                                : 'Справочник ещё не заполнен.'}
                    </p>
                </div>
            )}

            {!loading && visible.length > 0 && view === 'table' && (
                <OfficeTable
                    offices={visible}
                    groups={groups}
                    officeCount={officeCount}
                    dayISO={dayISO}
                    isToday={isToday}
                    tick={tick}
                    canManage={canManage}
                    onOpen={setInfoTarget}
                    onEdit={(item) => setDraft(draftFrom(item))}
                    onArchive={archive}
                    onRestore={restore}
                    onMarkDay={setDayTarget}
                />
            )}

            {!loading && visible.length > 0 && view !== 'table' && (
                groups
                    /* Между городами воздуха больше, чем между карточками внутри
                       города: одинаковый отступ и делал список сплошным, а
                       заголовок города читался как подпись к первой карточке.
                       Заголовок стоит у КАЖДОГО города, даже если офис в нём
                       один: сорок пять карточек без ровного деления и есть та
                       самая «непонятно, где какой город». Города-одиночки
                       пробовали пускать одной сеткой с городом подписью на
                       карточке — подпись в 11 пикселей рядом с заголовком в 20
                       читалась как другой уровень, а не как тот же. */
                    ? <div className="space-y-5">
                        {groups.map((group) => (
                            <section key={group.city} className="space-y-2.5">
                                <div className="flex items-baseline gap-3">
                                    <h3 className="text-[20px] font-semibold leading-none tracking-tight text-slate-900">
                                        {group.city}
                                    </h3>
                                    {/* Записи «офиса в городе нет» в счёт не идут:
                                        «1 офис» над карточкой «Офиса в городе
                                        нет» — прямое противоречие. */}
                                    {group.items.some((office) => !office.no_office) && (
                                        <span className="shrink-0 text-[12.5px] font-medium tabular-nums text-slate-400">
                                            {officeCount(group.items.filter((office) => !office.no_office).length)}
                                        </span>
                                    )}
                                    {/* Линия до правого края: карточки заполнены
                                        только слева, и без неё граница города
                                        читалась лишь в первой трети ширины. */}
                                    <div className="h-px min-w-6 flex-1 bg-slate-200" />
                                </div>
                                <div className={grid}>
                                    {group.items.map((office) => renderCard(office, false))}
                                </div>
                            </section>
                        ))}
                      </div>
                    : <div className={grid}>{visible.map((office) => renderCard(office, true))}</div>
            )}

            <IosModal
                open={!!draft}
                onClose={() => setDraft(null)}
                title={draft?.id ? 'Изменить офис' : 'Новый офис'}
                maxWidth="max-w-2xl"
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary} onClick={() => setDraft(null)}>
                            Отмена
                        </button>
                        <button
                            type="button"
                            className={iosBtnPrimary}
                            disabled={busy || !draft?.name?.trim()}
                            onClick={save}
                        >
                            {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                        </button>
                    </>
                )}
            >
                {draft && (
                    <OfficeEditor
                        draft={draft}
                        setDraft={setDraft}
                        base={base}
                        headers={headers}
                        showToast={toast}
                    />
                )}
            </IosModal>

            {/* Модалок две, и вложенными они не бывают: любое действие из
                подробностей сначала закрывает их. Иначе после «в архив» на
                экране осталась бы карточка офиса, которого в выборке уже нет —
                список-то перезагружается. */}
            {infoTarget && (
                <OfficeInfoModal
                    office={infoTarget}
                    base={base}
                    dayISO={dayISO}
                    isToday={isToday}
                    tick={tick}
                    canManage={canManage}
                    onClose={() => setInfoTarget(null)}
                    onCopyAddress={copyAddress}
                    onEdit={(item) => { setInfoTarget(null); setDraft(draftFrom(item)); }}
                    onMarkDay={(item) => { setInfoTarget(null); setDayTarget(item); }}
                    onArchive={(item) => { setInfoTarget(null); archive(item); }}
                    onRestore={(item) => { setInfoTarget(null); restore(item); }}
                />
            )}

            {dayTarget && (
                <OfficeDayModal
                    office={dayTarget}
                    dayISO={dayISO}
                    busy={busy}
                    onSubmit={markDay}
                    onClear={clearDay}
                    onClose={() => setDayTarget(null)}
                />
            )}
        </div>
    );
}
