import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, Map as MapIcon, Plus, Search } from 'lucide-react';
import {
    iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosModal,
} from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import useStableCallback from './useStableCallback';
import CityMap from './CityMap';
import CityCard from './CityCard';
import CityEditor, { draftFromCity, payloadFromDraft } from './CityEditor';
import OfficeInfoModal from './OfficeInfoModal';
import { officeTodayISO } from './officeSchedule';
import { cityMatches, cityZone, zoneHubs, zonePalette } from './cityRules';

/* Вкладка «Города» (задача #322): тарифы Яндекс Go по городу и то, чего на
 * странице Яндекса нет, — комиссии, требования к авто, услуги парка и куда
 * направлять водителя.
 *
 * Раскладка — по двум макетам постановки: сверху схема страны с зонами
 * офисов, под ней список городов с поиском и карточка выбранного. Список
 * приходит сводкой (без тарифов — на 24 города это ~140 КБ), карточка
 * догружается по выбору и запоминается, чтобы переход между городами туда и
 * обратно не ходил на сервер дважды.
 *
 * Справочник принадлежит пространству: у Таксопарков и Тез свои города, и
 * space_id едет в КАЖДОМ запросе вкладки (см. wiki/routes_cities.py).
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const selectedKey = (spaceId) => `wiki.cities.selected.${spaceId || 'default'}`;

const readSelected = (spaceId) => {
    try {
        return Number(window.localStorage.getItem(selectedKey(spaceId))) || null;
    } catch {
        return null;
    }
};

export default function WikiCities({ base, headers, showToast, spaceId = null }) {
    const toast = useStableCallback(showToast);
    const isPhone = useIsMobileShell();

    /* Одним объектом, чтобы очередной запрос вкладки не забыл пространство —
       забытое, оно молча ушло бы не в ту вику (тот же приём, что в «Офисах»). */
    const req = useMemo(
        () => ({ headers, params: { space_id: spaceId || undefined } }),
        [headers, spaceId],
    );

    const [cities, setCities] = useState([]);
    const [offices, setOffices] = useState([]);
    const [canManage, setCanManage] = useState(false);
    const [loading, setLoading] = useState(true);
    const [query, setQuery] = useState('');
    const [selectedId, setSelectedId] = useState(() => readSelected(spaceId));
    const [details, setDetails] = useState({});
    const [detailLoading, setDetailLoading] = useState(false);
    const [phoneOpen, setPhoneOpen] = useState(false);
    const [draft, setDraft] = useState(null);
    const [busy, setBusy] = useState(false);
    const [officeTarget, setOfficeTarget] = useState(null);
    const [tick, setTick] = useState(0);
    const inflight = useRef(new Set());

    // Минутный тик — ради живого статуса офиса в «Куда направлять водителя».
    useEffect(() => {
        const timer = setInterval(() => setTick((n) => n + 1), 60_000);
        return () => clearInterval(timer);
    }, []);

    const load = useCallback(() => {
        setLoading(true);
        Promise.all([
            axios.get(`${base}/cities`, req),
            axios.get(`${base}/offices`, req),
        ])
            .then(([cityResponse, officeResponse]) => {
                setCities(cityResponse.data?.items || []);
                setCanManage(!!cityResponse.data?.can_manage);
                setOffices(officeResponse.data?.items || []);
            })
            .catch((e) => toast(errText(e, 'Не удалось загрузить города'), 'error'))
            .finally(() => setLoading(false));
    }, [base, req, toast]);

    useEffect(() => { load(); }, [load]);

    const collator = useMemo(() => new Intl.Collator('ru'), []);
    const sorted = useMemo(
        () => [...cities].sort((a, b) => collator.compare(a.name, b.name)),
        [cities, collator],
    );
    const visible = useMemo(() => sorted.filter((city) => cityMatches(city, query)), [sorted, query]);
    const palette = useMemo(() => zonePalette(cities), [cities]);
    const hubs = useMemo(() => zoneHubs(cities), [cities]);

    /* На компьютере карточка открыта всегда: без выбора правая половина
       пустовала бы. Запомненный город ушёл в архив — берём первый. */
    const effectiveId = useMemo(() => {
        if (selectedId && cities.some((city) => city.id === selectedId)) return selectedId;
        return isPhone ? null : sorted[0]?.id || null;
    }, [selectedId, cities, sorted, isPhone]);

    const fetchDetail = useCallback((cityId, { force = false } = {}) => {
        if (!cityId || inflight.current.has(cityId)) return;
        if (!force && details[cityId]) return;
        inflight.current.add(cityId);
        setDetailLoading(true);
        axios.get(`${base}/cities/${cityId}`, req)
            .then((r) => setDetails((current) => ({ ...current, [cityId]: r.data?.city })))
            .catch((e) => toast(errText(e, 'Не удалось открыть город'), 'error'))
            .finally(() => {
                inflight.current.delete(cityId);
                setDetailLoading(false);
            });
    }, [base, req, details, toast]);

    useEffect(() => { fetchDetail(effectiveId); }, [effectiveId, fetchDetail]);

    const select = (city) => {
        setSelectedId(city.id);
        try {
            window.localStorage.setItem(selectedKey(spaceId), String(city.id));
        } catch {
            // Приватный режим — выбор просто не запомнится.
        }
        if (isPhone) setPhoneOpen(true);
    };

    const summary = cities.find((city) => city.id === effectiveId) || null;
    // Карточка — сводка, поверх которой лежит догруженная подробность: пока
    // подробность едет, шапка и комиссии уже на месте.
    const current = summary ? { ...summary, ...(details[summary.id] || {}) } : null;

    /* Ответ сервера после записи несёт карточку целиком — кладём её и в
       сводку, и в подробности, без второго запроса за тем же. */
    const absorb = (city) => {
        if (!city) return;
        setDetails((currentDetails) => ({ ...currentDetails, [city.id]: city }));
        setCities((list) => {
            const exists = list.some((item) => item.id === city.id);
            const merged = exists
                ? list.map((item) => (item.id === city.id ? { ...item, ...city } : item))
                : [...list, city];
            return merged.filter((item) => item.status === 'active');
        });
    };

    const reportSync = (sync) => {
        if (!sync) return;
        if (sync.status === 'error') toast(`Яндекс: ${sync.error}`, 'error');
        else if (sync.status === 'changed') toast('Тарифы обновлены с Яндекса', 'success');
        else toast('У Яндекса без изменений', 'success');
    };

    const openEditor = (city) => {
        if (!city) {
            setDraft(draftFromCity(null));
            return;
        }
        // Редактору нужны тарифы Яндекса — берём из подробности, а не из сводки.
        setDraft(draftFromCity(details[city.id] || city));
    };

    const save = () => {
        const payload = payloadFromDraft(draft);
        setBusy(true);
        const request = draft.id
            ? axios.patch(`${base}/cities/${draft.id}`, payload, req)
            : axios.post(`${base}/cities`, payload, req);
        request
            .then((r) => {
                const city = r.data?.city;
                absorb(city);
                if (city && !draft.id) select(city);
                setDraft(null);
                /* Город из архива возвращается той же карточкой — со всем, что в
                   неё вписывали (routes_cities: POST на архивное название). */
                toast(r.data?.restored ? 'Город возвращён из архива — с прежней карточкой'
                    : draft.id ? 'Город обновлён' : 'Город добавлен', 'success');
                if (r.data?.sync) reportSync(r.data.sync);
            })
            .catch((e) => toast(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setBusy(false));
    };

    const sync = (city) => {
        setBusy(true);
        axios.post(`${base}/cities/${city.id}/sync`, {}, req)
            .then((r) => { absorb(r.data?.city); reportSync(r.data?.sync); })
            .catch((e) => toast(errText(e, 'Не удалось обновить'), 'error'))
            .finally(() => setBusy(false));
    };

    const archive = (city) => {
        setBusy(true);
        axios.delete(`${base}/cities/${city.id}`, req)
            .then(() => {
                toast('Город убран в архив', 'success');
                setPhoneOpen(false);
                load();
            })
            .catch((e) => toast(errText(e, 'Не удалось'), 'error'))
            .finally(() => setBusy(false));
    };

    const copyAddress = (office) => {
        const text = [office.city, office.address].filter(Boolean).join(', ');
        if (!text) return;
        if (navigator.clipboard?.writeText) {
            navigator.clipboard.writeText(text)
                .then(() => toast('Адрес скопирован', 'success'))
                .catch(() => toast('Не удалось скопировать — выделите адрес вручную', 'error'));
            return;
        }
        toast('Копирование недоступно в этом браузере', 'error');
    };

    const card = current && (
        <CityCard
            city={current}
            embedded={isPhone}
            loading={detailLoading && !details[current.id]}
            offices={offices}
            canManage={canManage}
            busy={busy}
            tick={tick}
            zoneColor={palette[cityZone(current, hubs)] || null}
            onEdit={(city) => { setPhoneOpen(false); openEditor(city); }}
            onSync={sync}
            onArchive={archive}
            onOpenOffice={(office) => { setPhoneOpen(false); setOfficeTarget(office); }}
        />
    );

    const list = (
        <div className={`${iosCard} overflow-hidden`}>
            {visible.length === 0 ? (
                <div className="px-4 py-6 text-center text-[13px] text-slate-500">
                    {query ? 'Город не найден' : 'Городов пока нет'}
                </div>
            ) : (
                <ul className="max-h-[70vh] divide-y divide-slate-100 overflow-y-auto thin-scroll">
                    {visible.map((city) => {
                        const active = city.id === effectiveId && !isPhone;
                        const color = palette[cityZone(city, hubs)];
                        return (
                            <li key={city.id}>
                                <button
                                    type="button"
                                    onClick={() => select(city)}
                                    aria-current={active ? 'true' : undefined}
                                    className={`flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-[14px] transition ${
                                        active
                                            ? 'bg-blue-50 font-semibold text-blue-700'
                                            : 'text-slate-800 hover:bg-slate-50'
                                    }`}
                                >
                                    <span
                                        className="h-2 w-2 shrink-0 rounded-full"
                                        style={{ background: color || '#e2e8f0' }}
                                    />
                                    <span className="min-w-0 flex-1 truncate">{city.name}</span>
                                </button>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
                <div className="min-w-0">
                    <h2 className="text-[19px] font-semibold leading-tight tracking-tight text-slate-900">
                        Города
                    </h2>
                    <p className="mt-0.5 text-[12.5px] text-slate-500">
                        Тарифы Яндекс Go, комиссии и куда направлять водителя
                    </p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                    {canManage && (
                        <button type="button" className={iosBtnPrimary} onClick={() => openEditor(null)}>
                            <Plus size={15} /> Город
                        </button>
                    )}
                </div>
            </div>

            {loading && cities.length === 0 && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-slate-400`}>
                    <Loader2 size={16} className="animate-spin" />
                    <span className="text-[13px]">Загружаем…</span>
                </div>
            )}

            {!loading && cities.length === 0 && (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <MapIcon size={22} />
                    </div>
                    <div className="text-[15px] font-semibold text-slate-900">Городов пока нет</div>
                    <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                        {canManage
                            ? 'Добавьте город и ссылку на его тарифы Яндекс Go — тарифы и цены подтянутся сами, комиссии и услуги парка впишете в той же форме.'
                            : 'Справочник городов ещё не заполнен.'}
                    </p>
                </div>
            )}

            {cities.length > 0 && (
                <>
                    <div className={`${iosCard} px-3 pb-3 pt-2 sm:px-5 sm:pb-4`}>
                        <CityMap
                            cities={cities}
                            selectedId={isPhone ? null : effectiveId}
                            onSelect={select}
                            compact={isPhone}
                        />
                    </div>

                    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
                        <div className="space-y-2.5 lg:sticky lg:top-2">
                            <div className="relative">
                                <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input
                                    className={`${iosInput} pl-10`}
                                    value={query}
                                    onChange={(e) => setQuery(e.target.value)}
                                    placeholder="Поиск города"
                                    aria-label="Поиск города"
                                />
                            </div>
                            {list}
                        </div>
                        {!isPhone && card}
                    </div>
                </>
            )}

            {/* На телефоне карточка — отдельный экран, как карточка офиса:
                под списком из двадцати четырёх городов её пришлось бы искать
                прокруткой. */}
            {isPhone && (
                <IosModal
                    open={phoneOpen && !!current}
                    onClose={() => setPhoneOpen(false)}
                    title={current?.name || ''}
                    maxWidth="max-w-2xl"
                >
                    {card}
                </IosModal>
            )}

            <IosModal
                open={!!draft}
                onClose={() => setDraft(null)}
                title={draft?.id ? `Изменить: ${draft.name}` : 'Новый город'}
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
                    <CityEditor
                        draft={draft}
                        setDraft={setDraft}
                        offices={offices}
                        takenNames={cities.filter((city) => city.id !== draft.id).map((city) => city.name)}
                    />
                )}
            </IosModal>

            {officeTarget && (
                <OfficeInfoModal
                    office={officeTarget}
                    base={base}
                    dayISO={officeTodayISO()}
                    isToday
                    tick={tick}
                    canManage={false}
                    onClose={() => setOfficeTarget(null)}
                    onCopyAddress={copyAddress}
                    onEdit={() => {}}
                    onMarkDay={() => {}}
                    onArchive={() => {}}
                    onRestore={() => {}}
                />
            )}
        </div>
    );
}
