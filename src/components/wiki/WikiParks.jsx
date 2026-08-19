import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Archive, ArchiveRestore, Building2, Globe, Loader2, MapPin, Percent,
    Phone, Plus, Search, Tag, Pencil,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosBadge, IosModal,
} from '../ui/ios';
import useStableCallback from './useStableCallback';
import ParkEditor from './ParkEditor';
import { parkDraftIssue, pointsFromPark, pointsPayload } from './parkPoints';

/* Таксопарки и акции.
 *
 * Автономный справочник: к статьям не привязан, живёт своей вкладкой. Данные
 * из вики не переносились — там было 16 парков ровно захардкоженного сида и
 * ноль акций, то есть содержимым никто не пользовался. Переехала механика,
 * заполнять начинают заново.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const fmtDate = (iso) => (iso
    ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
    : null);

const emptyPark = () => ({
    name: '', city: '', address: '', website: '', commission: '', description: '',
    head_office_id: undefined,
    // Одна пустая строка сразу: номер у парка обязателен, и форма должна
    // показывать это полем, а не пустым местом с кнопкой.
    points: pointsFromPark({}),
});

/* Номера парка одной строкой: сперва те, что без офиса, потом офисные.
   Больше двух в карточку не влезает — остальные считаем числом, за ними всё
   равно идут в саму карточку. */
const parkPhones = (park) => [
    ...(park.phones || []),
    ...(park.offices || []).flatMap((link) => link.phones || []),
];

const ParkCard = ({ park, canManage, onEdit, onArchive, onRestore }) => {
    const phones = parkPhones(park);
    return (
        <div className={`${iosCard} p-4`}>
            <div className="flex items-start gap-3">
                <div className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-xl bg-indigo-50 text-indigo-600">
                    {park.logo_url
                        ? <img src={park.logo_url} alt="" className="h-full w-full object-cover" />
                        : <Building2 size={18} />}
                </div>
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[14.5px] font-semibold text-slate-900">{park.name}</span>
                        {park.status === 'archived' && <IosBadge tone="amber">В архиве</IosBadge>}
                        {park.offices?.length > 0 && (
                            <IosBadge tone="slate">
                                <MapPin size={11} /> офисов: {park.offices.length}
                            </IosBadge>
                        )}
                        {park.promotions_count > 0 && (
                            <IosBadge tone="blue">
                                <Tag size={11} /> акций: {park.promotions_count}
                            </IosBadge>
                        )}
                    </div>
                    {park.description && (
                        <p className="mt-1 line-clamp-2 text-[12.5px] leading-relaxed text-slate-500">
                            {park.description}
                        </p>
                    )}
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-slate-500">
                        {park.city && <span className="flex items-center gap-1"><MapPin size={11} /> {park.city}</span>}
                        {phones.slice(0, 2).map((phone) => (
                            <span key={phone} className="flex items-center gap-1 tabular-nums">
                                <Phone size={11} /> {phone}
                            </span>
                        ))}
                        {phones.length > 2 && (
                            <span className="text-slate-400">ещё номеров: {phones.length - 2}</span>
                        )}
                        {park.commission != null && (
                            <span className="flex items-center gap-1 tabular-nums">
                                <Percent size={11} /> комиссия {park.commission}%
                            </span>
                        )}
                        {park.website && (
                            <a
                                href={park.website}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-1 text-indigo-600 hover:underline"
                            >
                                <Globe size={11} /> сайт
                            </a>
                        )}
                    </div>
                </div>
                {canManage && (
                    <div className="flex shrink-0 items-center gap-1">
                        <button
                            type="button"
                            onClick={() => onEdit(park)}
                            className="grid h-8 w-8 place-items-center rounded-full text-slate-400 transition hover:bg-blue-50 hover:text-blue-600"
                            aria-label="Изменить парк"
                        >
                            <Pencil size={14} />
                        </button>
                        {park.status === 'active' ? (
                            <button
                                type="button"
                                onClick={() => onArchive(park)}
                                className="grid h-8 w-8 place-items-center rounded-full text-slate-400 transition hover:bg-amber-50 hover:text-amber-600"
                                aria-label="Убрать в архив"
                            >
                                <Archive size={14} />
                            </button>
                        ) : (
                            // Обратный ход обязателен рядом с архивированием: убрать
                            // парк можно было одним нажатием, а вернуть — ничем.
                            <button
                                type="button"
                                onClick={() => onRestore(park)}
                                className="grid h-8 w-8 place-items-center rounded-full text-slate-400 transition hover:bg-emerald-50 hover:text-emerald-600"
                                aria-label="Вернуть из архива"
                            >
                                <ArchiveRestore size={14} />
                            </button>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

export default function WikiParks({ base, headers, showToast }) {
    const toast = useStableCallback(showToast);

    const [parks, setParks] = useState([]);
    const [promotions, setPromotions] = useState([]);
    const [offices, setOffices] = useState([]);
    const [canManage, setCanManage] = useState(false);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [query, setQuery] = useState('');
    const [draft, setDraft] = useState(null);

    const load = useCallback(() => {
        setLoading(true);
        Promise.all([
            axios.get(`${base}/parks`, { headers, params: query.trim() ? { q: query.trim() } : {} }),
            axios.get(`${base}/promotions`, { headers }),
            axios.get(`${base}/offices`, { headers }),
        ])
            .then(([parksResponse, promoResponse, officeResponse]) => {
                setParks(parksResponse.data?.items || []);
                setCanManage(!!parksResponse.data?.can_manage);
                setPromotions(promoResponse.data?.items || []);
                setOffices((officeResponse.data?.items || [])
                    .filter((office) => office.status === 'active'));
            })
            .catch((e) => toast(errText(e, 'Не удалось загрузить справочник'), 'error'))
            .finally(() => setLoading(false));
    }, [base, headers, query, toast]);

    useEffect(() => {
        const timer = setTimeout(load, query ? 250 : 0);
        return () => clearTimeout(timer);
    }, [load, query]);

    const save = () => {
        const payload = {
            name: draft.name,
            city: draft.city || null,
            // Адрес парка — ссылка на офис; null снимает её.
            head_office_id: draft.head_office_id ?? null,
            website: draft.website || null,
            description: draft.description || null,
            commission: draft.commission === '' ? null : Number(draft.commission),
            ...pointsPayload(draft.points),
        };
        setBusy(true);
        const request = draft.id
            ? axios.patch(`${base}/parks/${draft.id}`, payload, { headers })
            : axios.post(`${base}/parks`, payload, { headers });
        request
            .then(() => { toast(draft.id ? 'Парк обновлён' : 'Парк добавлен', 'success'); setDraft(null); load(); })
            .catch((e) => toast(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setBusy(false));
    };

    const archive = (park) => {
        setBusy(true);
        axios.delete(`${base}/parks/${park.id}`, { headers })
            .then(() => { toast('Парк убран в архив', 'success'); load(); })
            .catch((e) => toast(errText(e, 'Не удалось'), 'error'))
            .finally(() => setBusy(false));
    };

    const restore = (park) => {
        setBusy(true);
        axios.patch(`${base}/parks/${park.id}`, { status: 'active' }, { headers })
            .then(() => { toast('Парк возвращён из архива', 'success'); load(); })
            .catch((e) => toast(errText(e, 'Не удалось вернуть'), 'error'))
            .finally(() => setBusy(false));
    };

    const running = useMemo(() => promotions.filter((p) => p.is_running), [promotions]);
    const draftIssue = parkDraftIssue(draft);

    return (
        <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[200px] flex-1">
                    <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        className={`${iosInput} pl-10`}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Поиск по названию или городу"
                    />
                </div>
                {canManage && (
                    <button type="button" className={iosBtnPrimary} onClick={() => setDraft(emptyPark())}>
                        <Plus size={15} /> Парк
                    </button>
                )}
            </div>

            {running.length > 0 && (
                <section className="space-y-1.5">
                    <div className={`${iosGroupLabel} flex items-center gap-1.5`}>
                        <Tag size={12} /> Действующие акции
                    </div>
                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {running.map((promo) => (
                            <div key={promo.id} className="px-4 py-3">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-[14px] font-medium text-slate-900">{promo.title}</span>
                                    {promo.ends_at && (
                                        <IosBadge tone="amber">до {fmtDate(promo.ends_at)}</IosBadge>
                                    )}
                                    {promo.park_ids?.length > 0 && (
                                        <IosBadge tone="slate">парков: {promo.park_ids.length}</IosBadge>
                                    )}
                                </div>
                                {promo.description && (
                                    <p className="mt-0.5 text-[12.5px] leading-relaxed text-slate-500">
                                        {promo.description}
                                    </p>
                                )}
                            </div>
                        ))}
                    </div>
                </section>
            )}

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Таксопарки</div>

                {loading && (
                    <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-slate-400`}>
                        <Loader2 size={16} className="animate-spin" />
                        <span className="text-[13px]">Загружаем…</span>
                    </div>
                )}

                {!loading && parks.length === 0 && (
                    <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                        <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                            <Building2 size={22} />
                        </div>
                        <div className="text-[15px] font-semibold text-slate-900">
                            {query ? 'Ничего не найдено' : 'Справочник пуст'}
                        </div>
                        <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                            {query
                                ? 'Попробуйте изменить запрос.'
                                : canManage
                                    ? 'Добавьте первый таксопарк — данные из старой вики не переносились, там был только демонстрационный набор.'
                                    : 'Справочник ещё не заполнен.'}
                        </p>
                    </div>
                )}

                {!loading && parks.length > 0 && (
                    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                        {parks.map((park) => (
                            <ParkCard
                                key={park.id}
                                park={park}
                                canManage={canManage}
                                onEdit={(p) => setDraft({
                                    id: p.id, name: p.name, city: p.city || '',
                                    address: p.address || '',
                                    head_office_id: p.head_office?.id ?? undefined,
                                    website: p.website || '', description: p.description || '',
                                    commission: p.commission ?? '',
                                    points: pointsFromPark(p),
                                })}
                                onArchive={archive}
                                onRestore={restore}
                            />
                        ))}
                    </div>
                )}
            </section>

            <IosModal
                open={!!draft}
                onClose={() => setDraft(null)}
                title={draft?.id ? 'Изменить парк' : 'Новый таксопарк'}
                maxWidth="max-w-xl"
                footer={(
                    <>
                        {draftIssue && (
                            <span className="mr-auto max-w-[55%] text-left text-[11.5px] leading-snug text-amber-600">
                                {draftIssue}
                            </span>
                        )}
                        <button type="button" className={iosBtnSecondary} onClick={() => setDraft(null)}>
                            Отмена
                        </button>
                        <button
                            type="button"
                            className={iosBtnPrimary}
                            disabled={busy || !!draftIssue}
                            onClick={save}
                        >
                            {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                        </button>
                    </>
                )}
            >
                {draft && (
                    <ParkEditor draft={draft} setDraft={setDraft} offices={offices} />
                )}
            </IosModal>
        </div>
    );
}
