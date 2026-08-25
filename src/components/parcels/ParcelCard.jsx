import React, { useCallback, useMemo, useState } from 'react';
import axios from 'axios';
import { Check, ExternalLink, Loader2, Pencil, Trash2 } from 'lucide-react';
import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosGroupLabel, IosModal,
} from '../ui/ios';
import {
    PARCEL_STATUSES, daysInOffice, describeEvent, driverAccountUrl, fmtDate, fmtDateTime,
    fmtPhone, kindMeta, linkLabel, pluralDays, rowTone, safeLink, statusMeta, tonePill,
    toneRow, toneText,
} from './parcelMeta';

/*
 * Карточка посылки: всё о записи + история изменений + смена статуса.
 *
 * История показана лентой, а не последней правкой: ТЗ просит её прямо («так же
 * необходимо отобразить историю изменений»), и на вопрос «кому отдали коробку»
 * через месяц отвечает именно она.
 *
 * Смена статуса — прямо в карточке, а не отдельной модалкой: это единственное
 * частое действие над посылкой, и прятать его за вторым нажатием незачем. У
 * читателя (СЗоВ) выбор не рендерится вовсе — кнопка, которая всегда отвечает
 * отказом, хуже её отсутствия.
 *
 * Карточка читается сверху вниз как ответ на четыре вопроса: в каком она
 * состоянии (сводка), что с ней делать (выбор), что это за посылка и где она,
 * кто её оставил, что с ней было. До правки 25.08.2026 поля лежали двумя
 * безымянными панелями, а состояние приходилось собирать из сегментного
 * контрола и строчки под ним.
 */

/* Внешняя ссылка в карточке: подпись + иконка «откроется в новой вкладке».
   Иконка не украшение — без неё человек не отличает ссылку наружу от перехода
   внутри портала и теряет карточку, из которой уходил. `noopener` обязателен:
   без него открытая страница получает доступ к нашему окну. */
const OutLink = ({ href, children, title }) => (
    <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        title={title}
        className="inline-flex max-w-full items-center gap-1 text-blue-600 underline decoration-blue-200 underline-offset-2 transition hover:decoration-blue-500"
    >
        <span className="truncate">{children}</span>
        <ExternalLink size={12} className="shrink-0 text-blue-400" />
    </a>
);

const Row = ({ label, children }) => {
    /* `false` в этом списке не для красоты: `{value && <span>…</span>}` при
       пустом значении отдаёт именно false, и без него строка рисовалась пустой.
       Обёртка в элемент (`<span>{value}</span>`) не спасает вовсе — элемент
       никогда не null, поэтому такие значения передаём сюда СЫРЫМИ, а
       оформление вешаем внутри. Так и пряталась пустая строка «Номер заказа». */
    if (children === null || children === undefined || children === '' || children === false) {
        return null;
    }
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
    /* Оттенок берём тем же правилом, что реестр: нажал янтарную строку —
       открылась янтарная карточка, и сверять, ту ли открыл, не нужно. */
    const tone = useMemo(() => rowTone(parcel), [parcel]);
    const text = toneText(tone);
    const pill = tonePill(tone);
    const account = useMemo(() => driverAccountUrl(parcel), [parcel]);
    const orderHref = useMemo(() => safeLink(parcel?.order_url), [parcel]);

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
            /* Подзаголовком — дата приёма, а не описание: описание целиком стоит
               в секции «Посылка», и в шапке оно было бы тем же текстом второй
               раз на одном экране (да ещё обрезанным). Дата коротка, не
               обрезается и отвечает на вопрос «давно ли». */
            title={`${kindMeta(parcel.kind).label} · №${parcel.id}`}
            subtitle={`Принята ${fmtDate(parcel.received_on)}`}
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
                {/* Сводка: в каком состоянии запись — одним взглядом, до всех полей.
                    Панель тонируется тем же оттенком, что строка в реестре, поэтому
                    человек, нажавший янтарную строку, видит янтарную карточку и не
                    сверяет, ту ли он открыл. */}
                <section className={`rounded-2xl px-3.5 py-3 ${toneRow(tone)} ring-1 ring-slate-900/[0.06]`}>
                    <div className="flex flex-wrap items-center gap-2">
                        <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[13px] font-semibold ${pill.fill}`}>
                            <span className={`h-2 w-2 rounded-full ${pill.dot}`} />
                            {statusMeta(parcel.status).label}
                        </span>
                        {lying !== null && (
                            <span className={`text-[12.5px] ${text.meta} ${tone === 'stale' ? 'font-medium' : ''}`}>
                                лежит {pluralDays(lying)}
                            </span>
                        )}
                    </div>
                    <p className={`mt-1.5 text-[12px] ${text.meta}`}>
                        {/* Расшифровку статуса словами показываем только читателю:
                            у того, кто может статус менять, она стоит строкой ниже,
                            у выбранного варианта — и повторять её здесь значило бы
                            написать одно и то же дважды на одном экране. */}
                        {!canEdit && statusMeta(parcel.status).hint}
                        {!canEdit && parcel.status_changed_at && ' · '}
                        {parcel.status_changed_at && (
                            <>
                                Изменён{' '}
                                <span className="tabular-nums">{fmtDateTime(parcel.status_changed_at)}</span>
                                {parcel.status_changed_by_name && `, ${parcel.status_changed_by_name}`}
                            </>
                        )}
                        {!parcel.status_changed_at && canEdit && 'Статус ещё не менялся'}
                    </p>
                    {/* Где лежит — здесь же, а не отдельной секцией: «в каком
                        состоянии» и «где искать» оператор читает одним движением,
                        а тремя строками в своей панели место повторяло бы то, что
                        и так стоит в реестре. */}
                    <p className={`mt-1 border-t border-slate-900/[0.06] pt-1.5 text-[12.5px] ${text.body}`}>
                        {parcel.city}
                        {parcel.office_name && ` · ${parcel.office_name}`}
                        {parcel.office_address && (
                            <span className={text.meta}>, {parcel.office_address}</span>
                        )}
                    </p>
                </section>

                {/* Смена статуса — СПИСОК с подписями, а не сегментный контрол.
                    В сегментах помещались только «Получателю | Отправителю»: два
                    дательных падежа, различающиеся корнем, читались как выбор
                    адресата, а не как итог. Здесь у каждого варианта глагол и
                    строка «кто именно забрал», так что выбор не требует догадки.
                    Читателю (СЗоВ) список не рисуется вовсе — состояние он уже
                    прочитал в сводке выше. */}
                {canEdit && (
                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Что с посылкой</div>
                        <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                            {PARCEL_STATUSES.map((code, index) => {
                                const meta = statusMeta(code);
                                const chosen = parcel.status === code;
                                return (
                                    <button
                                        key={code}
                                        type="button"
                                        disabled={busy}
                                        onClick={() => changeStatus(code)}
                                        aria-pressed={chosen}
                                        className={`flex w-full items-start gap-3 px-3.5 py-2.5 text-left transition disabled:opacity-60 ${
                                            index > 0 ? 'border-t border-slate-100' : ''
                                        } ${chosen ? 'bg-slate-50' : 'hover:bg-slate-50'}`}
                                    >
                                        <span className={`mt-[3px] grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full transition ${
                                            chosen ? 'bg-blue-600' : 'ring-1 ring-slate-300'
                                        }`}>
                                            {chosen && <Check size={12} className="text-white" strokeWidth={3} />}
                                        </span>
                                        <span className="min-w-0">
                                            <span className={`block text-[13.5px] ${chosen ? 'font-semibold text-slate-900' : 'text-slate-800'}`}>
                                                {meta.action}
                                            </span>
                                            <span className="block text-[12px] text-slate-500">{meta.hint}</span>
                                        </span>
                                        {busy && chosen && (
                                            <Loader2 size={14} className="ml-auto mt-[3px] shrink-0 animate-spin text-slate-400" />
                                        )}
                                    </button>
                                );
                            })}
                        </div>
                    </section>
                )}

                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Посылка</div>
                    <div className="rounded-2xl bg-white px-3.5 py-2 ring-1 ring-slate-200/70">
                        <Row label="Тип">{kindMeta(parcel.kind).label}</Row>
                        <Row label="Описание">{parcel.description}</Row>
                        <Row label="Отправитель">{parcel.sender}</Row>
                        <Row label="Получатель">{parcel.recipient}</Row>
                        {/* Заказ прикреплён ссылкой — её и открываем. Битую или
                            не-http ссылку (могла попасть до проверки на сервере)
                            показываем текстом: ссылка, ведущая непонятно куда,
                            хуже, чем её отсутствие. */}
                        <Row label="Заказ">
                            {orderHref
                                ? <OutLink href={orderHref} title={parcel.order_url}>
                                    {linkLabel(parcel.order_url)}
                                </OutLink>
                                : (parcel.order_url
                                    ? <span className="break-all text-slate-500">{parcel.order_url}</span>
                                    : null)}
                        </Row>
                        {/* Свободный номер заказа больше не спрашивается; строка
                            остаётся ради записей, где он был заполнен. */}
                        <Row label="Номер заказа">
                            {parcel.order_number
                                ? <span className="tabular-nums">{parcel.order_number}</span>
                                : null}
                        </Row>
                        <Row label="Комментарий">{parcel.comment}</Row>
                    </div>
                </section>

                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Водитель</div>
                    <div className="rounded-2xl bg-white px-3.5 py-2 ring-1 ring-slate-200/70">
                        <Row label="ФИО">
                            {account
                                ? <OutLink href={account} title="Открыть аккаунт водителя во Флите">
                                    {parcel.driver_name || 'Аккаунт водителя'}
                                </OutLink>
                                : (parcel.driver_name || '—')}
                        </Row>
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
                        {/* id показываем только там, где ссылки нет: при живой
                            ссылке это тот же адрес второй раз, а по 32 символам
                            всё равно ничего не находят руками. */}
                        {!account && (
                            <Row label="ID во Флите">
                                <span className="break-all font-mono text-[12px] text-slate-500">{parcel.driver_account_id}</span>
                            </Row>
                        )}
                    </div>
                </section>

                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>История</div>
                    <ol className="space-y-2.5 rounded-2xl bg-white px-3.5 py-3 ring-1 ring-slate-200/70">
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
                        <li className="flex gap-3 border-t border-slate-100 pt-2.5 text-[12px] text-slate-500">
                            <span className="min-w-0">
                                Добавил {parcel.created_by_name || '—'}
                                {' · '}
                                <span className="tabular-nums">{fmtDateTime(parcel.created_at)}</span>
                            </span>
                        </li>
                    </ol>
                </section>
            </div>

        </IosModal>
    );
};

export default ParcelCard;
