import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Archive, ArchiveRestore, Building2, CalendarClock, ChevronLeft, ChevronRight,
    Clock, Coffee, Columns2, Copy, Loader2, MapPin, Pencil, Phone, Plus, Rows3,
    Search, Table2,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary,
    IosBadge, IosModal,
} from '../ui/ios';
import useStableCallback from './useStableCallback';
import OfficeMap from './OfficeMap';
import OfficeEditor from './OfficeEditor';
import OfficeFilters, { DEFAULT_FILTERS, SORT_OPTIONS } from './OfficeFilters';
import OfficeTable from './OfficeTable';
import OfficeDayModal from './OfficeDayModal';
import {
    breakLines, dayHoursOn, officeStatus, officeTodayISO, scheduleLines,
} from './officeSchedule';
import {
    DAY_LEGEND, DAY_STATE_EDGE, DAY_STATE_TONE, formatDay, officeDayStatus,
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
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

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

const STATUS_TONE = { open: 'green', break: 'amber', closed: 'slate' };

const StatusBadge = ({ schedule, tick }) => {
    // tick только заставляет пересчитать: время берётся из часов, а не из него.
    const status = useMemo(() => officeStatus(schedule), [schedule, tick]);

    if (status.state === 'none') return null;

    return (
        <IosBadge tone={STATUS_TONE[status.state]}>
            {status.state === 'break' ? <Coffee size={11} /> : <Clock size={11} />}
            {status.state === 'open' && `Открыто до ${status.until}`}
            {status.state === 'break' && `Обед до ${status.until}`}
            {status.state === 'closed' && (status.opensAt
                ? `Закрыто · откроется ${status.opensDay ? `${status.opensDay} ` : ''}${status.opensAt}`
                : 'Закрыто')}
        </IosBadge>
    );
};

/* Статус за выбранный день. В отличие от StatusBadge здесь нет минут: за
   прошедший день «Открыто до 19:00» было бы выдумкой — что офис закрылся именно
   в 19:00, никто не записывал. */
const DayBadge = ({ status }) => (
    <IosBadge tone={DAY_STATE_TONE[status.state]}>
        {status.state === 'open' && <Clock size={11} />}
        {status.label}
        {status.state === 'open' && status.until && ` · ${status.from}–${status.until}`}
    </IosBadge>
);

const OfficeCard = ({
    base, office, canManage, onEdit, onArchive, onRestore, onMarkDay, onCopyAddress,
    dayISO, isToday, showCity, tick,
}) => {
    const week = useMemo(() => scheduleLines(office.schedule), [office.schedule]);
    const lunch = useMemo(() => breakLines(office.schedule), [office.schedule]);
    const parkPhones = (office.parks || []).filter((link) => link.phones?.length);
    const notes = (office.address_note || '').split('\n').map((s) => s.trim()).filter(Boolean);

    const hours = useMemo(() => dayHoursOn(office.schedule, dayISO), [office.schedule, dayISO]);
    const [weekOpen, setWeekOpen] = useState(false);

    const status = officeDayStatus(office, dayISO);
    const absent = status.state === 'absent';
    // Отметка человека перебивает живой расчёт и в сегодняшнем дне: она и
    // ставится ровно для этого — «по графику открыт, а фактически закрыт».
    const marked = office.day?.source === 'manual';

    return (
        <div className={`${iosCard} relative overflow-hidden before:absolute before:inset-y-0 before:left-0 before:z-10 before:w-[3px] ${DAY_STATE_EDGE[status.state] || ''}`}>
            <div className="flex flex-col sm:flex-row">
                {office.lat != null && office.lon != null && (
                    <OfficeMap
                        base={base}
                        lat={office.lat}
                        lon={office.lon}
                        url={office.map_url}
                        height={150}
                        className="shrink-0 rounded-none sm:w-[210px]"
                    />
                )}

                <div className="min-w-0 flex-1 p-4">
                    <div className="flex items-start gap-2">
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
                                {isToday && !marked ? (
                                    <StatusBadge schedule={office.schedule} tick={tick} />
                                ) : (
                                    <DayBadge status={status} />
                                )}
                            </div>

                            {absent ? (
                                <p className="mt-1 pl-[19px] text-[13px] italic leading-relaxed text-slate-500">
                                    Офиса в городе нет
                                </p>
                            ) : office.address && (
                                <p className="mt-1 flex items-start gap-1.5 text-[13px] leading-relaxed text-slate-600">
                                    <MapPin size={13} className="mt-0.5 shrink-0 text-slate-400" />
                                    {/* Адрес копируется щелчком: оператор его диктует и
                                        пересылает, а выделять мышью текст в карточке —
                                        лишняя работа десятки раз за смену. */}
                                    <button
                                        type="button"
                                        onClick={() => onCopyAddress(office)}
                                        title="Скопировать адрес"
                                        className="group flex items-start gap-1.5 text-left hover:text-blue-600"
                                    >
                                        <span>{office.address}</span>
                                        <Copy size={12} className="mt-0.5 shrink-0 text-slate-300 transition group-hover:text-blue-500" />
                                    </button>
                                </p>
                            )}

                            {/* Причина закрытия — это ответ оператора водителю,
                                поэтому она рядом со статусом, а не в истории. */}
                            {status.note && (
                                <p className="mt-1 pl-[19px] text-[12.5px] leading-relaxed text-slate-500">
                                    {status.note}
                                    {status.recordedOn && (
                                        <span className="text-slate-400"> · отметка на {formatDay(status.recordedOn)}</span>
                                    )}
                                </p>
                            )}

                            {notes.length > 0 && (
                                <ul className="mt-1 space-y-0.5 pl-[19px] text-[12.5px] leading-relaxed text-slate-500">
                                    {notes.map((note, index) => (
                                        // eslint-disable-next-line react/no-array-index-key
                                        <li key={index} className="list-disc list-inside">{note}</li>
                                    ))}
                                </ul>
                            )}
                        </div>

                        {canManage && (
                            <div className="flex shrink-0 items-center gap-1">
                                {/* Отметка дня — ежедневное действие дежурного, ему
                                    место в один щелчок, а не внутри формы офиса. */}
                                {!absent && (
                                    <button
                                        type="button"
                                        onClick={() => onMarkDay(office)}
                                        className={`grid h-8 w-8 place-items-center rounded-full transition hover:bg-blue-50 hover:text-blue-600 ${
                                            marked ? 'text-blue-600' : 'text-slate-400'
                                        }`}
                                        aria-label="Отметить статус на дату"
                                    >
                                        <CalendarClock size={14} />
                                    </button>
                                )}
                                <button
                                    type="button"
                                    onClick={() => onEdit(office)}
                                    className="grid h-8 w-8 place-items-center rounded-full text-slate-400 transition hover:bg-blue-50 hover:text-blue-600"
                                    aria-label="Изменить офис"
                                >
                                    <Pencil size={14} />
                                </button>
                                {office.status === 'active' ? (
                                    <button
                                        type="button"
                                        onClick={() => onArchive(office)}
                                        className="grid h-8 w-8 place-items-center rounded-full text-slate-400 transition hover:bg-amber-50 hover:text-amber-600"
                                        aria-label="Убрать в архив"
                                    >
                                        <Archive size={14} />
                                    </button>
                                ) : (
                                    // Без обратного хода архив превращается в
                                    // одностороннюю дверь: убрать одним нажатием,
                                    // вернуть — ничем.
                                    <button
                                        type="button"
                                        onClick={() => onRestore(office)}
                                        className="grid h-8 w-8 place-items-center rounded-full text-slate-400 transition hover:bg-emerald-50 hover:text-emerald-600"
                                        aria-label="Вернуть из архива"
                                    >
                                        <ArchiveRestore size={14} />
                                    </button>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Часы — за выбранный день, а не вся неделя: оператору отвечают
                        «до скольки сегодня». Неделя нужна реже, поэтому она под
                        раскрытием, и карточка перестала быть простыней из семи дней. */}
                    {!absent && (
                        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-slate-600">
                            {office.phone && (
                                <a
                                    href={`tel:${office.phone.replace(/[^\d+]/g, '')}`}
                                    className="flex items-center gap-1.5 font-medium tabular-nums hover:text-blue-600"
                                >
                                    <Phone size={12} className="text-slate-400" /> {office.phone}
                                </a>
                            )}
                            {/* За сегодня часы дня не печатаем: бейдж рядом уже сказал
                                «Открыто до 19:00» — это то же самое, только точнее.
                                Обед показываем всегда: «когда у них перерыв» спрашивают
                                заранее, а в бейдж он попадает только когда уже идёт. */}
                            {hours ? (
                                <>
                                    {!isToday && (
                                        <span className="flex items-center gap-1.5 tabular-nums">
                                            <Clock size={12} className="text-slate-400" />
                                            {formatDay(dayISO)} {hours.from}–{hours.to}
                                        </span>
                                    )}
                                    {hours.breakFrom && (
                                        <span className="flex items-center gap-1.5 tabular-nums text-slate-500">
                                            <Coffee size={12} className="text-slate-400" />
                                            обед {hours.breakFrom}–{hours.breakTo}
                                        </span>
                                    )}
                                </>
                            ) : !isToday && week.length > 0 && (
                                <span className="flex items-center gap-1.5 text-slate-400">
                                    <Clock size={12} /> {formatDay(dayISO)} выходной
                                </span>
                            )}
                            {week.length > 0 && (
                                <button
                                    type="button"
                                    onClick={() => setWeekOpen((v) => !v)}
                                    className="text-[12px] font-medium text-blue-600 hover:underline"
                                >
                                    {weekOpen ? 'скрыть неделю' : 'вся неделя'}
                                </button>
                            )}
                        </div>
                    )}

                    {weekOpen && week.length > 0 && (
                        <div className="mt-1.5 rounded-xl bg-slate-50 px-3 py-2 text-[12.5px] text-slate-600">
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 tabular-nums">
                                {week.map((line) => (
                                    <span key={line.days} className={line.isDayOff ? 'text-slate-400' : ''}>
                                        {line.days}&nbsp;{line.time}
                                    </span>
                                ))}
                            </div>
                            {lunch.length > 0 && (
                                <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-slate-500">
                                    {lunch.map((line) => (
                                        <span key={line.days || 'all'} className="flex items-center gap-1.5">
                                            <Coffee size={12} className="text-slate-400" />
                                            обед {line.days ? `${line.days} ` : ''}{line.time}
                                        </span>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Список только показывает привязку; меняют её в карточке парка. */}
                    {office.parks?.length > 0 && (
                        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                            <Building2 size={12} className="text-slate-400" />
                            {office.parks.map((link) => (
                                <IosBadge key={link.park_id} tone="slate">{link.name}</IosBadge>
                            ))}
                        </div>
                    )}

                    {/* Номер у парка в этом офисе свой: у одного адреса их
                        столько же, сколько парков за ним сидит. */}
                    {parkPhones.length > 0 && (
                        <div className="mt-2 rounded-xl bg-slate-50 px-3 py-2">
                            <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                Телефоны парков
                            </div>
                            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[12.5px] text-slate-600">
                                {parkPhones.map((link) => (
                                    <span key={link.park_id} className="tabular-nums">
                                        <span className="text-slate-400">{link.name}:</span>{' '}
                                        {link.phones
                                            .map((item) => (typeof item === 'string'
                                                ? item
                                                : [item.phone, item.note].filter(Boolean).join(' · ')))
                                            .join(', ')}
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
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
    + 'text-slate-900 border-0 transition focus:bg-white focus:outline-none '
    + 'focus:ring-2 focus:ring-blue-500/70';

/* Вид и сортировку помним между заходами: раздел открывают каждый день, и
 * выбирать «таблицу» заново каждое утро — работа, которую делать не надо.
 * В приватном режиме Safari доступ к localStorage бросает, поэтому обе стороны
 * в try/catch: настройка второстепенна, падать из-за неё нельзя. */
const readPrefs = () => {
    try {
        const raw = JSON.parse(window.localStorage.getItem(PREFS_KEY) || '{}');
        return {
            view: VIEWS.some((item) => item.key === raw.view) ? raw.view : 'cards1',
            sort: SORT_OPTIONS.some((item) => item.key === raw.sort) ? raw.sort : DEFAULT_FILTERS.sort,
        };
    } catch {
        return { view: 'cards1', sort: DEFAULT_FILTERS.sort };
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

    const markDay = (state, note) => {
        setBusy(true);
        axios.put(`${base}/offices/${dayTarget.id}/day/${dayISO}`,
                  { state, note: note || null }, { headers })
            .then(() => { toast('Статус на дату сохранён', 'success'); setDayTarget(null); load(); })
            .catch((e) => toast(errText(e, 'Не удалось сохранить статус'), 'error'))
            .finally(() => setBusy(false));
    };

    const clearDay = () => {
        setBusy(true);
        axios.delete(`${base}/offices/${dayTarget.id}/day/${dayISO}`, { headers })
            .then(() => { toast('День снова считается по графику', 'success'); setDayTarget(null); load(); })
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
       выбранный пункт фильтра выглядел бы сломанным. */
    const grouped = SORT_OPTIONS.find((item) => item.key === filters.sort)?.grouped
        && view !== 'cards2';

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

    const renderCard = (office) => (
        <OfficeCard
            key={office.id}
            base={base}
            office={office}
            canManage={canManage}
            onEdit={(item) => setDraft(draftFrom(item))}
            onArchive={archive}
            onRestore={restore}
            onMarkDay={setDayTarget}
            onCopyAddress={copyAddress}
            dayISO={dayISO}
            isToday={isToday}
            showCity={!grouped}
            tick={tick}
        />
    );

    return (
        <div className="space-y-5">
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

            {/* Легенда и счётчики — одной строкой, и она же фильтр. Цветовую
                кодировку новый оператор читает без обучения (п. 4.4 ТЗ), а
                «где закрыто» находится одним нажатием вместо перебора списка.
                Отдельная легенда и отдельные счётчики были бы двумя строками
                про одно и то же. */}
            {!loading && offices.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 px-1">
                    {DAY_LEGEND.map((item) => {
                        const count = counts[item.state] || 0;
                        const active = stateFilter === item.state;
                        return (
                            <button
                                key={item.state}
                                type="button"
                                disabled={!count && !active}
                                onClick={() => setStateFilter(active ? '' : item.state)}
                                className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] transition disabled:opacity-40 ${
                                    active
                                        ? 'bg-slate-900 text-white'
                                        : 'text-slate-500 hover:bg-slate-100 enabled:hover:text-slate-700'
                                }`}
                            >
                                <span className={`h-2 w-2 rounded-full ${item.dot}`} />
                                {item.label}
                                <span className="tabular-nums font-semibold">{count}</span>
                            </button>
                        );
                    })}
                    {!isToday && (
                        <span className="ml-1 text-[12px] text-slate-400">
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
                    dayISO={dayISO}
                    canManage={canManage}
                    onEdit={(item) => setDraft(draftFrom(item))}
                    onArchive={archive}
                    onRestore={restore}
                    onMarkDay={setDayTarget}
                />
            )}

            {!loading && visible.length > 0 && view !== 'table' && (
                groups
                    ? groups.map((group) => (
                        <section key={group.city} className="space-y-1.5">
                            <div className={iosGroupLabel}>{group.city}</div>
                            <div className={grid}>
                                {group.items.map(renderCard)}
                            </div>
                        </section>
                    ))
                    : <div className={grid}>{visible.map(renderCard)}</div>
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
