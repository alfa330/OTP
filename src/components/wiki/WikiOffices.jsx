import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Archive, Building2, Clock, Coffee, Loader2, MapPin, Pencil, Phone, Plus,
    Search, Wifi,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary,
    IosBadge, IosModal,
} from '../ui/ios';
import useStableCallback from './useStableCallback';
import OfficeMap from './OfficeMap';
import OfficeEditor from './OfficeEditor';
import { breakLines, officeStatus, scheduleLines } from './officeSchedule';

/* Офисы: адреса, карта, график и привязка к таксопаркам.
 *
 * Раздел заменяет статью «Адреса офисов», где один и тот же адрес был переписан
 * в таблице каждого парка. Здесь запись одна на физический офис, а парки к ней
 * привязываются; отличия телефона у конкретного парка живут в связи.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const emptyDraft = () => ({
    name: '', city: '', address: '', address_note: '', phone: '',
    map_url: '', map_resolved_url: null, lat: null, lon: null,
    schedule: {}, is_online: false, all_parks: false,
    kind: 'park', partner_label: '', parks: [],
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
    is_online: !!office.is_online,
    all_parks: !!office.all_parks,
    kind: office.kind || 'park',
    partner_label: office.partner_label || '',
    parks: (office.parks || []).map((link) => ({
        park_id: link.park_id, phone: link.phone || '',
    })),
});

const STATUS_TONE = { open: 'green', break: 'amber', closed: 'slate' };

const StatusBadge = ({ schedule, isOnline, tick }) => {
    // tick только заставляет пересчитать: время берётся из часов, а не из него.
    const status = useMemo(() => officeStatus(schedule), [schedule, tick]);

    if (isOnline) return <IosBadge tone="blue"><Wifi size={11} /> Только по телефону</IosBadge>;
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

const OfficeCard = ({ base, office, canManage, onEdit, onArchive, tick }) => {
    const week = useMemo(() => scheduleLines(office.schedule), [office.schedule]);
    const lunch = useMemo(() => breakLines(office.schedule), [office.schedule]);
    const overrides = (office.parks || []).filter((link) => link.phone);
    const notes = (office.address_note || '').split('\n').map((s) => s.trim()).filter(Boolean);

    return (
        <div className={`${iosCard} overflow-hidden`}>
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
                            <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-[14.5px] font-semibold text-slate-900">
                                    {office.name}
                                </span>
                                {office.status === 'archived' && <IosBadge tone="amber">В архиве</IosBadge>}
                                {office.kind === 'partner' && (
                                    <IosBadge tone="blue">{office.partner_label || 'Партнёрский'}</IosBadge>
                                )}
                                <StatusBadge
                                    schedule={office.schedule}
                                    isOnline={office.is_online}
                                    tick={tick}
                                />
                            </div>

                            {office.address && (
                                <p className="mt-1 flex items-start gap-1.5 text-[13px] leading-relaxed text-slate-600">
                                    <MapPin size={13} className="mt-0.5 shrink-0 text-slate-400" />
                                    <span>{office.address}</span>
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
                                <button
                                    type="button"
                                    onClick={() => onEdit(office)}
                                    className="grid h-8 w-8 place-items-center rounded-full text-slate-400 transition hover:bg-blue-50 hover:text-blue-600"
                                    aria-label="Изменить офис"
                                >
                                    <Pencil size={14} />
                                </button>
                                {office.status === 'active' && (
                                    <button
                                        type="button"
                                        onClick={() => onArchive(office)}
                                        className="grid h-8 w-8 place-items-center rounded-full text-slate-400 transition hover:bg-amber-50 hover:text-amber-600"
                                        aria-label="Убрать в архив"
                                    >
                                        <Archive size={14} />
                                    </button>
                                )}
                            </div>
                        )}
                    </div>

                    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-slate-600">
                        {office.phone && (
                            <a
                                href={`tel:${office.phone.replace(/[^\d+]/g, '')}`}
                                className="flex items-center gap-1.5 font-medium tabular-nums hover:text-blue-600"
                            >
                                <Phone size={12} className="text-slate-400" /> {office.phone}
                            </a>
                        )}
                        {week.length > 0 && (
                            <span className="flex flex-wrap items-center gap-1.5">
                                <Clock size={12} className="text-slate-400" />
                                {week.map((line, index) => (
                                    <React.Fragment key={line.days}>
                                        {index > 0 && <span className="text-slate-300">·</span>}
                                        <span className={line.isDayOff ? 'text-slate-400' : ''}>
                                            {line.days}&nbsp;{line.time}
                                        </span>
                                    </React.Fragment>
                                ))}
                            </span>
                        )}
                        {lunch.map((line) => (
                            <span key={line.days || 'all'} className="flex items-center gap-1.5 text-slate-500">
                                <Coffee size={12} className="text-slate-400" />
                                обед {line.days ? `${line.days} ` : ''}{line.time}
                            </span>
                        ))}
                    </div>

                    {(office.all_parks || office.parks?.length > 0) && (
                        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                            <Building2 size={12} className="text-slate-400" />
                            {office.all_parks
                                ? <IosBadge tone="blue">Все таксопарки</IosBadge>
                                : office.parks.map((link) => (
                                    <IosBadge key={link.park_id} tone="slate">{link.name}</IosBadge>
                                ))}
                        </div>
                    )}

                    {overrides.length > 0 && (
                        <div className="mt-2 rounded-xl bg-slate-50 px-3 py-2">
                            <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                Свой телефон у парков
                            </div>
                            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[12.5px] text-slate-600">
                                {overrides.map((link) => (
                                    <span key={link.park_id} className="tabular-nums">
                                        <span className="text-slate-400">{link.name}:</span> {link.phone}
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

export default function WikiOffices({ base, headers, showToast }) {
    const toast = useStableCallback(showToast);

    const [offices, setOffices] = useState([]);
    const [cities, setCities] = useState([]);
    const [parks, setParks] = useState([]);
    const [canManage, setCanManage] = useState(false);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [query, setQuery] = useState('');
    const [city, setCity] = useState('');
    const [parkId, setParkId] = useState('');
    const [draft, setDraft] = useState(null);
    const [tick, setTick] = useState(0);

    // Минутный тик оживляет бейдж «Открыто до 19:00»: карточку держат открытой
    // весь день, и статус, посчитанный при загрузке, к обеду уже врал бы.
    useEffect(() => {
        const timer = setInterval(() => setTick((n) => n + 1), 60_000);
        return () => clearInterval(timer);
    }, []);

    const load = useCallback(() => {
        setLoading(true);
        const params = {};
        if (query.trim()) params.q = query.trim();
        if (city) params.city = city;
        if (parkId) params.park_id = parkId;

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
    }, [base, headers, query, city, parkId, toast]);

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
            is_online: !!draft.is_online,
            all_parks: !!draft.all_parks,
            kind: draft.kind,
            partner_label: draft.kind === 'partner' ? (draft.partner_label || null) : null,
            parks: draft.parks.map((link) => ({
                park_id: link.park_id, phone: link.phone || null,
            })),
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

    /* Группировка по городам: справочник читают запросом «а где офис в
       Караганде», а не листая всё подряд. Порядок групп — как пришёл с
       сервера, там он задан позицией. */
    const groups = useMemo(() => {
        const order = [];
        const byCity = new Map();
        offices.forEach((office) => {
            const key = office.city || 'Без города';
            if (!byCity.has(key)) { byCity.set(key, []); order.push(key); }
            byCity.get(key).push(office);
        });
        return order.map((key) => ({ city: key, items: byCity.get(key) }));
    }, [offices]);

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

                <select
                    className={`${iosInput} w-auto min-w-[150px]`}
                    value={city}
                    onChange={(e) => setCity(e.target.value)}
                >
                    <option value="">Все города</option>
                    {cities.map((item) => (
                        <option key={item.city} value={item.city}>{item.city} ({item.count})</option>
                    ))}
                </select>

                <select
                    className={`${iosInput} w-auto min-w-[150px]`}
                    value={parkId}
                    onChange={(e) => setParkId(e.target.value)}
                >
                    <option value="">Все таксопарки</option>
                    {parks.map((park) => (
                        <option key={park.id} value={park.id}>{park.name}</option>
                    ))}
                </select>

                {canManage && (
                    <button type="button" className={iosBtnPrimary} onClick={() => setDraft(emptyDraft())}>
                        <Plus size={15} /> Офис
                    </button>
                )}
            </div>

            {loading && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-slate-400`}>
                    <Loader2 size={16} className="animate-spin" />
                    <span className="text-[13px]">Загружаем…</span>
                </div>
            )}

            {!loading && offices.length === 0 && (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <MapPin size={22} />
                    </div>
                    <div className="text-[15px] font-semibold text-slate-900">
                        {query || city || parkId ? 'Ничего не найдено' : 'Офисов пока нет'}
                    </div>
                    <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                        {query || city || parkId
                            ? 'Попробуйте изменить условия поиска.'
                            : canManage
                                ? 'Добавьте офис: адрес, ссылку 2ГИС, телефон и график. Парки привяжете в той же форме.'
                                : 'Справочник ещё не заполнен.'}
                    </p>
                </div>
            )}

            {!loading && groups.map((group) => (
                <section key={group.city} className="space-y-1.5">
                    <div className={iosGroupLabel}>{group.city}</div>
                    <div className="grid grid-cols-1 gap-3 2xl:grid-cols-2">
                        {group.items.map((office) => (
                            <OfficeCard
                                key={office.id}
                                base={base}
                                office={office}
                                canManage={canManage}
                                onEdit={(item) => setDraft(draftFrom(item))}
                                onArchive={archive}
                                tick={tick}
                            />
                        ))}
                    </div>
                </section>
            ))}

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
                        parks={parks}
                        base={base}
                        headers={headers}
                        showToast={toast}
                    />
                )}
            </IosModal>
        </div>
    );
}
