import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeftRight, ArrowRight, Loader2, Plus, Trash2 } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosCard, iosGroupLabel, iosInput } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import InfoHint from '../common/InfoHint';
import { Empty, Loading } from '../hr_office/hrUi';
import { collapseRoutePairs, errorText, serverIssues } from './mailMeta';

/*
 * «Справочники и настройки» (ТЗ п. 2, 7) — только администратору раздела.
 *
 * Зоны договора: сколько рабочих дней везут (стандарт и эконом, «от–до»); контроль — по верхней
 * границе. Стартовые значения — из ТЗ: внутригородская (зона 2) — 1 день, зона 1 — 2–3, зона 3 —
 * 7–15. Матрица маршрутов — «город отправления × город получения» из приложения №1 к договору:
 * пара городов → зона. Город один — внутригородская зона сама; пары нет в матрице — при передаче
 * курьеру зону выбирают вручную («специальные маршруты — ручное подтверждение»).
 */
let seed = 0;
const uid = () => { seed += 1; return `row-${seed}`; };
const SMALL_INPUT = `${iosInput} !px-2.5 !py-2 text-center tabular-nums`;
const collapseRoutes = (routes) => collapseRoutePairs(routes).map((route) => ({ ...route, key: uid() }));

const numberField = (value) => (value === null || value === undefined ? '' : String(value));
const toNumber = (value) => (String(value).trim() === '' ? null : Number(value));

export default function MailSettings({ api, toast, onSaved }) {
    const [zones, setZones] = useState(null);
    const [routes, setRoutes] = useState([]);
    const [cities, setCities] = useState([]);
    const [error, setError] = useState('');
    const [problems, setProblems] = useState({});
    const [busy, setBusy] = useState(false);
    const [dirty, setDirty] = useState(false);

    const apply = useCallback((payload) => {
        setZones((payload.zones || []).map((zone) => ({ ...zone, key: uid() })));
        setRoutes(collapseRoutes(payload.routes || []));
        setCities(payload.cities || []);
        setDirty(false);
        setProblems({});
    }, []);
    const load = useCallback(() => api.get('/settings')
        .then(({ data }) => { apply(data); setError(''); })
        .catch((requestError) => setError(errorText(requestError, 'Настройки не загрузились'))), [api, apply]);
    useEffect(() => { load(); }, [load]);

    const cityOptions = useMemo(() => {
        const all = new Set(cities);
        routes.forEach((route) => { if (route.from_city) all.add(route.from_city); if (route.to_city) all.add(route.to_city); });
        return [...all].sort((a, b) => a.localeCompare(b, 'ru')).map((city) => ({ value: city, label: city }));
    }, [cities, routes]);
    const zoneOptions = useMemo(() => (zones || []).filter((zone) => String(zone.code).trim())
        .map((zone) => ({ value: zone.code, label: `${zone.label || `Зона ${zone.code}`}` })), [zones]);

    const editZone = (key, changes) => { setZones((prev) => prev.map((zone) => (zone.key === key ? { ...zone, ...changes } : zone))); setDirty(true); };
    const editRoute = (key, changes) => { setRoutes((prev) => prev.map((route) => (route.key === key ? { ...route, ...changes } : route))); setDirty(true); };

    const save = async () => {
        setBusy(true);
        setProblems({});
        try {
            const { data } = await api.put('/settings', {
                zones: zones.map((zone) => ({
                    code: String(zone.code).trim(), label: zone.label, min_days: toNumber(zone.min_days), max_days: toNumber(zone.max_days),
                    eco_min_days: toNumber(zone.eco_min_days), eco_max_days: toNumber(zone.eco_max_days),
                })),
                routes: routes.map((route) => ({
                    from_city: route.from_city, to_city: route.to_city, zone_code: route.zone_code, both_ways: route.both_ways,
                })),
            });
            apply(data);
            toast?.('Справочники сохранены', 'success');
            onSaved?.();
        } catch (requestError) {
            const map = {};
            serverIssues(requestError).forEach((item) => { map[item.key] = item.text; });
            setProblems(map);
            toast?.(errorText(requestError, 'Не сохранилось'), 'error');
        } finally {
            setBusy(false);
        }
    };

    if (error && !zones) return <div className={`${iosCard} p-4 text-[13.5px] text-rose-600`}>{error}</div>;
    if (!zones) return <Loading text="Загружаем справочники…" />;

    const bad = (key) => (problems[key] ? ' ring-2 ring-rose-300' : '');
    return (
        <div className="flex flex-col gap-5">
            <section className="space-y-2">
                <div className="flex items-center gap-1.5">
                    <span className={iosGroupLabel}>Зоны договора</span>
                    <InfoHint side="left" text="Рабочие дни от фактической передачи курьеру. Контроль просрочки — по верхней границе, праздники РК не считаются. Эконом без своих границ не отправить — сначала задайте их здесь." />
                </div>
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="hidden grid-cols-[5rem_minmax(0,1fr)_10rem_10rem_2.5rem] gap-3 border-b border-slate-100 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 sm:grid">
                        <span>Код</span><span>Название</span><span>Стандарт, раб. дн.</span><span>Эконом, раб. дн.</span><span />
                    </div>
                    <div className="divide-y divide-slate-100">
                        {zones.map((zone, index) => (
                            <div key={zone.key} className="grid grid-cols-2 gap-2 px-4 py-2.5 sm:grid-cols-[5rem_minmax(0,1fr)_10rem_10rem_2.5rem] sm:items-center sm:gap-3">
                                <input value={zone.code} maxLength={8} aria-label="Код зоны" onChange={(event) => editZone(zone.key, { code: event.target.value })}
                                       className={`${SMALL_INPUT}${bad(`zones.${index}.code`)}`} />
                                <input value={zone.label} maxLength={60} aria-label="Название зоны" onChange={(event) => editZone(zone.key, { label: event.target.value })}
                                       className={`${iosInput} !py-2${bad(`zones.${index}.label`)}`} />
                                {[['min_days', 'max_days'], ['eco_min_days', 'eco_max_days']].map(([low, high]) => (
                                    <div key={low} className="col-span-2 flex items-center gap-1.5 sm:col-span-1">
                                        {/* На телефоне шапки таблицы нет — пара подписана сама. */}
                                        <span className="w-[4.5rem] shrink-0 text-[11.5px] text-slate-500 sm:hidden">
                                            {low === 'min_days' ? 'Стандарт' : 'Эконом'}
                                        </span>
                                        <input value={numberField(zone[low])} inputMode="numeric" aria-label="от" placeholder="от"
                                               onChange={(event) => editZone(zone.key, { [low]: event.target.value.replace(/[^\d]/g, '') })}
                                               className={`${SMALL_INPUT}${bad(`zones.${index}.${low}`)}`} />
                                        <span className="text-slate-400">–</span>
                                        <input value={numberField(zone[high])} inputMode="numeric" aria-label="до" placeholder="до"
                                               onChange={(event) => editZone(zone.key, { [high]: event.target.value.replace(/[^\d]/g, '') })}
                                               className={`${SMALL_INPUT}${bad(`zones.${index}.${high}`)}`} />
                                    </div>
                                ))}
                                <button type="button" aria-label="Убрать зону" disabled={zones.length <= 1}
                                        onClick={() => { setZones((prev) => prev.filter((item) => item.key !== zone.key)); setDirty(true); }}
                                        className="grid h-9 w-9 place-items-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-rose-600 disabled:opacity-30">
                                    <Trash2 size={15} />
                                </button>
                            </div>
                        ))}
                    </div>
                </div>
                <button type="button" className={`${iosBtnGhost} -ml-2`}
                        onClick={() => { setZones((prev) => [...prev, { key: uid(), code: '', label: '', min_days: '', max_days: '' }]); setDirty(true); }}>
                    <Plus size={14} /> Зона
                </button>
            </section>

            <section className="space-y-2">
                <div className="flex items-center gap-1.5">
                    <span className={iosGroupLabel}>Маршруты — город отправления × город получения</span>
                    <InfoHint side="left" text="Матрица из приложения №1 к договору. Город один — внутригородская зона сама. Пары нет — при передаче курьеру зону выбирают вручную." />
                </div>
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="divide-y divide-slate-100">
                        {routes.length === 0 && <Empty>Маршрутов пока нет — пока их нет, зону при передаче курьеру выбирают вручную</Empty>}
                        {routes.map((route, index) => (
                            <div key={route.key} className="grid grid-cols-1 gap-2 px-4 py-2.5 sm:grid-cols-[minmax(0,1fr)_2.5rem_minmax(0,1fr)_minmax(0,0.9fr)_2.5rem] sm:items-center sm:gap-3">
                                <CustomSelect variant="ios" searchable value={route.from_city || null} options={cityOptions} placeholder="Откуда"
                                              ariaLabel="Город отправления" invalid={Boolean(problems[`routes.${index}.from_city`])}
                                              onChange={(value) => editRoute(route.key, { from_city: value })} />
                                <button type="button" title={route.both_ways ? 'В обе стороны' : 'Только туда'} aria-pressed={route.both_ways}
                                        onClick={() => editRoute(route.key, { both_ways: !route.both_ways })}
                                        className={`grid h-9 w-9 place-items-center rounded-xl ring-1 transition ${route.both_ways
                                            ? 'bg-[#eef4ff] text-[#1d4ed8] ring-[#bfd4fb]' : 'bg-white text-slate-500 ring-slate-200/70 hover:bg-[#f6f6f4]'}`}>
                                    {route.both_ways ? <ArrowLeftRight size={15} /> : <ArrowRight size={15} />}
                                </button>
                                <CustomSelect variant="ios" searchable value={route.to_city || null} options={cityOptions} placeholder="Куда"
                                              ariaLabel="Город получения" invalid={Boolean(problems[`routes.${index}.to_city`])}
                                              onChange={(value) => editRoute(route.key, { to_city: value })} />
                                <CustomSelect variant="ios" value={route.zone_code || null} options={zoneOptions} placeholder="Зона"
                                              ariaLabel="Зона" invalid={Boolean(problems[`routes.${index}.zone_code`])}
                                              onChange={(value) => editRoute(route.key, { zone_code: value })} />
                                <button type="button" aria-label="Убрать маршрут"
                                        onClick={() => { setRoutes((prev) => prev.filter((item) => item.key !== route.key)); setDirty(true); }}
                                        className="grid h-9 w-9 place-items-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-rose-600">
                                    <Trash2 size={15} />
                                </button>
                            </div>
                        ))}
                    </div>
                </div>
                <button type="button" className={`${iosBtnGhost} -ml-2`}
                        onClick={() => { setRoutes((prev) => [...prev, { key: uid(), from_city: '', to_city: '', zone_code: '', both_ways: true }]); setDirty(true); }}>
                    <Plus size={14} /> Маршрут
                </button>
            </section>

            <div className="flex items-center justify-end gap-2">
                {dirty && <span className="text-[12.5px] text-slate-500">Есть несохранённые изменения</span>}
                {dirty && <button type="button" className={iosBtnGhost} onClick={load}>Отменить</button>}
                <button type="button" className={iosBtnPrimary} onClick={save} disabled={busy || !dirty}>
                    {busy && <Loader2 size={15} className="animate-spin" />} Сохранить
                </button>
            </div>
        </div>
    );
}
