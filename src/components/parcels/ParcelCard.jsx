import React, { useCallback, useMemo, useState } from 'react';
import axios from 'axios';
import { Loader2, Pencil, Trash2 } from 'lucide-react';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, IosModal, IosSegmented,
} from '../ui/ios';
import {
    PARCEL_STATUSES, daysInOffice, describeEvent, fmtDate, fmtDateTime, fmtPhone,
    isStale, kindMeta, pluralDays, statusMeta,
} from './parcelMeta';

/*
 * Карточка посылки: всё о записи + история изменений + смена статуса.
 *
 * История показана лентой, а не последней правкой: ТЗ просит её прямо («так же
 * необходимо отобразить историю изменений»), и на вопрос «кому отдали коробку»
 * через месяц отвечает именно она.
 *
 * Смена статуса — сегментный контрол прямо в карточке, а не отдельная модалка:
 * это единственное частое действие над посылкой, и прятать его за вторым
 * нажатием незачем. У читателя (СЗоВ) контрол не рендерится вовсе — кнопка,
 * которая всегда отвечает отказом, хуже её отсутствия.
 */

const Row = ({ label, children }) => {
    if (children === null || children === undefined || children === '') return null;
    return (
        <div className="flex gap-3 py-1.5">
            <div className="w-[132px] shrink-0 text-[12.5px] text-slate-500">{label}</div>
            <div className="min-w-0 flex-1 text-[13.5px] text-slate-900">{children}</div>
        </div>
    );
};

const ParcelCard = ({
    open, onClose, apiBaseUrl, headers, parcel, events, canEdit, canDelete,
    onEdit, onChanged, onDeleted, showToast,
}) => {
    const [busy, setBusy] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);

    const lying = useMemo(() => daysInOffice(parcel), [parcel]);
    const stale = useMemo(() => isStale(parcel), [parcel]);

    const changeStatus = useCallback(async (status) => {
        if (!parcel || busy || status === parcel.status) return;
        setBusy(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/parcels/${parcel.id}/status`,
                { status },
                { headers: headers() },
            );
            showToast?.(`Статус: ${statusMeta(status).label}`, 'success');
            onChanged?.(response.data?.item || null, response.data?.events || []);
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось изменить статус', 'error');
        } finally {
            setBusy(false);
        }
    }, [apiBaseUrl, busy, headers, onChanged, parcel, showToast]);

    const remove = useCallback(async () => {
        if (!parcel || busy) return;
        setBusy(true);
        try {
            await axios.delete(`${apiBaseUrl}/api/parcels/${parcel.id}`, { headers: headers() });
            showToast?.('Запись удалена', 'success');
            onDeleted?.(parcel.id);
            onClose?.();
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось удалить запись', 'error');
        } finally {
            setBusy(false);
            setConfirmDelete(false);
        }
    }, [apiBaseUrl, busy, headers, onClose, onDeleted, parcel, showToast]);

    if (!parcel) return null;

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={`${kindMeta(parcel.kind).label} · №${parcel.id}`}
            subtitle={`${parcel.city}${parcel.office_name ? ` · ${parcel.office_name}` : ''}`}
            maxWidth="max-w-xl"
            footer={canEdit ? (
                <>
                    {canDelete && (
                        confirmDelete ? (
                            <>
                                <span className="mr-auto text-[12.5px] text-slate-600">Удалить запись вместе с историей?</span>
                                <button type="button" className={iosBtnGhost} onClick={() => setConfirmDelete(false)}>
                                    Отмена
                                </button>
                                <button
                                    type="button"
                                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 text-[13.5px] font-semibold text-white transition hover:bg-red-700 active:scale-[0.98] disabled:opacity-50"
                                    disabled={busy}
                                    onClick={remove}
                                >
                                    {busy ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
                                    Удалить
                                </button>
                            </>
                        ) : (
                            <button
                                type="button"
                                className={`${iosBtnGhost} mr-auto text-red-600 hover:bg-red-50`}
                                onClick={() => setConfirmDelete(true)}
                            >
                                <Trash2 size={14} />
                                Удалить
                            </button>
                        )
                    )}
                    {!confirmDelete && (
                        <button type="button" className={iosBtnPrimary} onClick={() => onEdit?.(parcel)}>
                            <Pencil size={15} />
                            Изменить
                        </button>
                    )}
                </>
            ) : (
                <button type="button" className={iosBtnSecondary} onClick={onClose}>Закрыть</button>
            )}
        >
            <div className="space-y-5">
                {/* Статус. У того, кто вправе его менять, это контрол; у читателя —
                    строка. Одна и та же информация, разная роль. */}
                <section className="space-y-2">
                    <div className="px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Статус</div>
                    {canEdit ? (
                        <IosSegmented
                            value={parcel.status}
                            onChange={changeStatus}
                            options={PARCEL_STATUSES.map((code) => ({
                                value: code, label: statusMeta(code).short,
                            }))}
                            stretch
                            ariaLabel="Статус посылки"
                        />
                    ) : (
                        <div className="rounded-xl bg-slate-100 px-3.5 py-2.5 text-[13.5px] text-slate-900">
                            {statusMeta(parcel.status).label}
                        </div>
                    )}
                    <p className="px-1 text-[12px] text-slate-500">
                        {parcel.status_changed_at
                            ? <>Изменён {fmtDateTime(parcel.status_changed_at)}
                                {parcel.status_changed_by_name && <> · {parcel.status_changed_by_name}</>}</>
                            : 'Статус ещё не менялся'}
                    </p>
                </section>

                <section className="rounded-2xl bg-white px-3.5 py-2 ring-1 ring-slate-200/70">
                    <Row label="Дата приёма">
                        <span className="tabular-nums">{fmtDate(parcel.received_on)}</span>
                        {lying !== null && (
                            <span className={stale ? 'ml-2 text-[12.5px] text-amber-600' : 'ml-2 text-[12.5px] text-slate-500'}>
                                лежит {pluralDays(lying)}
                            </span>
                        )}
                    </Row>
                    <Row label="Офис">
                        {parcel.office_name}
                        {parcel.office_address && <span className="text-slate-500">, {parcel.office_address}</span>}
                    </Row>
                    <Row label="Тип">{kindMeta(parcel.kind).label}</Row>
                    <Row label="Описание">{parcel.description}</Row>
                    <Row label="Отправитель">{parcel.sender}</Row>
                    <Row label="Получатель">{parcel.recipient}</Row>
                    <Row label="Номер заказа"><span className="tabular-nums">{parcel.order_number}</span></Row>
                    <Row label="Комментарий">{parcel.comment}</Row>
                </section>

                <section className="rounded-2xl bg-white px-3.5 py-2 ring-1 ring-slate-200/70">
                    <Row label="Водитель">{parcel.driver_name || '—'}</Row>
                    <Row label="Телефон">
                        {parcel.driver_phone && (
                            <a href={`tel:${parcel.driver_phone}`} className="tabular-nums text-blue-600 hover:underline">
                                {fmtPhone(parcel.driver_phone)}
                            </a>
                        )}
                    </Row>
                    <Row label="Таксопарк">{parcel.driver_park}</Row>
                    <Row label="Машина">{parcel.driver_car}</Row>
                    <Row label="Позывной">{parcel.driver_callsign}</Row>
                    <Row label="ID во Флите">
                        <span className="break-all font-mono text-[12px] text-slate-500">{parcel.driver_account_id}</span>
                    </Row>
                </section>

                <section className="space-y-2">
                    <div className="px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">История</div>
                    <ol className="space-y-2.5">
                        {(events || []).map((event) => (
                            <li key={event.id} className="flex gap-3">
                                <span className="mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />
                                <div className="min-w-0">
                                    <div className="text-[13px] text-slate-900">{describeEvent(event)}</div>
                                    <div className="text-[12px] text-slate-500">
                                        <span className="tabular-nums">{fmtDateTime(event.created_at)}</span>
                                        {event.actor_name && <> · {event.actor_name}</>}
                                    </div>
                                    {event.payload?.comment && (
                                        <div className="mt-0.5 text-[12.5px] text-slate-600">
                                            «{event.payload.comment}»
                                        </div>
                                    )}
                                    {event.kind === 'edited' && Array.isArray(event.payload?.changes) && (
                                        <ul className="mt-1 space-y-0.5">
                                            {event.payload.changes.map((change) => (
                                                <li key={change.field} className="text-[12px] text-slate-500">
                                                    {change.label}: <span className="line-through">{change.from || '—'}</span>
                                                    {' → '}
                                                    <span className="text-slate-700">{change.to || '—'}</span>
                                                </li>
                                            ))}
                                        </ul>
                                    )}
                                </div>
                            </li>
                        ))}
                        {!(events || []).length && (
                            <li className="text-[13px] text-slate-500">История пуста</li>
                        )}
                    </ol>
                    <p className="px-1 text-[12px] text-slate-500">
                        Добавил {parcel.created_by_name || '—'} · <span className="tabular-nums">{fmtDateTime(parcel.created_at)}</span>
                    </p>
                </section>
            </div>
        </IosModal>
    );
};

export default ParcelCard;
