import React, { useMemo } from 'react';
import {
    Archive, ArchiveRestore, CalendarClock, Clock, Coffee, Copy, ExternalLink,
    MapPin, Pencil, Phone,
} from 'lucide-react';
import {
    iosBtnGhost, iosBtnSecondary, iosGroupLabel, IosBadge, IosModal,
} from '../ui/ios';
import OfficeMap from './OfficeMap';
import { OfficeStatusBadge } from './officeBadges';
import {
    DAY_SOURCE_LABELS, formatDay, formatStampTime, officeDayStatus,
} from './officeDayStatus';
import { breakLines, dayHoursOn, scheduleLines } from './officeSchedule';

/* Всё про один офис — по нажатию на его адрес.
 *
 * Карточка раньше показывала сразу всё: карту, статус, адрес, ориентиры, часы
 * дня, неделю с обедами, парки и их телефоны. Двадцать таких карточек — это
 * простыня, в которой ответ «работает ли офис в Караганде» ищется прокруткой,
 * хотя нужен один взгляд. Решение владельца: в карточке остаётся адрес, всё
 * остальное открывается по нажатию.
 *
 * Поэтому здесь именно ВСЁ, что было на карточке, — иначе данные просто
 * исчезли бы из раздела. Порядок — по частоте вопроса оператору: работает ли,
 * куда ехать, куда звонить, до скольки.
 */

const Panel = ({ title, children }) => (
    <section className="space-y-1.5">
        {title && <div className={iosGroupLabel}>{title}</div>}
        <div className="rounded-2xl bg-white p-3.5 ring-1 ring-slate-200/70">{children}</div>
    </section>
);

export default function OfficeInfoModal({
    office, base, dayISO, isToday, tick, canManage,
    onClose, onCopyAddress, onEdit, onArchive, onRestore, onMarkDay,
}) {
    const status = officeDayStatus(office, dayISO);
    const absent = status.state === 'absent';

    const week = useMemo(() => scheduleLines(office.schedule), [office.schedule]);
    const lunch = useMemo(() => breakLines(office.schedule), [office.schedule]);
    const hours = useMemo(() => dayHoursOn(office.schedule, dayISO), [office.schedule, dayISO]);

    const notes = (office.address_note || '').split('\n').map((s) => s.trim()).filter(Boolean);
    const parkPhones = (office.parks || []).filter((link) => link.phones?.length);

    return (
        <IosModal
            open
            onClose={onClose}
            title={office.name}
            subtitle={[office.city, absent ? 'офиса в городе нет' : office.address]
                .filter(Boolean).join(' · ')}
            maxWidth="max-w-lg"
            footer={(
                <>
                    {canManage && (
                        <div className="mr-auto flex items-center gap-1">
                            {!absent && (
                                <button
                                    type="button"
                                    className={iosBtnGhost}
                                    onClick={() => onMarkDay(office)}
                                >
                                    <CalendarClock size={14} /> Статус на дату
                                </button>
                            )}
                            <button type="button" className={iosBtnGhost} onClick={() => onEdit(office)}>
                                <Pencil size={14} /> Изменить
                            </button>
                            {office.status === 'active' ? (
                                <button type="button" className={iosBtnGhost} onClick={() => onArchive(office)}>
                                    <Archive size={14} /> В архив
                                </button>
                            ) : (
                                <button type="button" className={iosBtnGhost} onClick={() => onRestore(office)}>
                                    <ArchiveRestore size={14} /> Вернуть
                                </button>
                            )}
                        </div>
                    )}
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>
                        Закрыть
                    </button>
                </>
            )}
        >
            <div className="space-y-3.5">
                {/* Статус первым: за ним и открывают карточку. Выбор бейджа —
                    то же правило, что на карточке (officeBadges.jsx): сегодня
                    без ручной отметки статус живой, с минутами. Держать здесь
                    оба сразу нельзя: «Открыт» над «сейчас закрыто» читается как
                    спор двух надписей, хотя это один и тот же факт с разной
                    точностью. */}
                <Panel>
                    <div className="flex flex-wrap items-center gap-2">
                        <OfficeStatusBadge
                            schedule={office.schedule}
                            status={status}
                            isToday={isToday}
                            dayISO={dayISO}
                            tick={tick}
                        />
                        {office.status === 'archived' && <IosBadge tone="amber">В архиве</IosBadge>}
                        {office.kind === 'partner' && (
                            <IosBadge tone="blue">{office.partner_label || 'Партнёрский'}</IosBadge>
                        )}
                        {!isToday && (
                            <span className="text-[12px] tabular-nums text-slate-400">
                                на {formatDay(dayISO)}
                            </span>
                        )}
                    </div>

                    {/* Причина закрытия — это готовый ответ водителю. */}
                    {status.note && (
                        <p className="mt-2 text-[13px] leading-relaxed text-slate-600">
                            {status.note}
                            {status.recordedOn && (
                                <span className="text-slate-400"> · отметка на {formatDay(status.recordedOn)}</span>
                            )}
                        </p>
                    )}

                    {/* Свежесть данных — здесь с минутами и источником: в
                        таблице на это есть только колонка с датой. */}
                    {status.updatedAt && (
                        <p className="mt-2 text-[12.5px] tabular-nums text-slate-400">
                            обновлено {formatStampTime(status.updatedAt)}
                            {DAY_SOURCE_LABELS[status.source]
                                && ` · ${DAY_SOURCE_LABELS[status.source]}`}
                        </p>
                    )}
                </Panel>

                {!absent && office.lat != null && office.lon != null && (
                    <OfficeMap
                        base={base}
                        lat={office.lat}
                        lon={office.lon}
                        url={office.map_url}
                        height={190}
                        className="w-full rounded-2xl ring-1 ring-slate-200/70"
                    />
                )}

                <Panel title="Адрес">
                    {absent ? (
                        <p className="text-[13.5px] italic text-slate-500">Офиса в городе нет</p>
                    ) : (
                        <>
                            <p className="flex items-start gap-2 text-[13.5px] leading-relaxed text-slate-800">
                                <MapPin size={14} className="mt-0.5 shrink-0 text-slate-400" />
                                <span>{office.address || '—'}</span>
                            </p>
                            {notes.length > 0 && (
                                <ul className="mt-1.5 space-y-0.5 pl-[22px] text-[12.5px] leading-relaxed text-slate-500">
                                    {notes.map((note, index) => (
                                        // eslint-disable-next-line react/no-array-index-key
                                        <li key={index} className="list-inside list-disc">{note}</li>
                                    ))}
                                </ul>
                            )}
                            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                                {/* Адрес оператор диктует и пересылает — копия
                                    нужна десятки раз за смену. */}
                                {office.address && (
                                    <button
                                        type="button"
                                        className={iosBtnSecondary}
                                        onClick={() => onCopyAddress(office)}
                                    >
                                        <Copy size={13} /> Скопировать
                                    </button>
                                )}
                                {office.map_url && (
                                    <a
                                        href={office.map_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className={iosBtnSecondary}
                                    >
                                        <ExternalLink size={13} /> Открыть в 2ГИС
                                    </a>
                                )}
                            </div>
                        </>
                    )}
                </Panel>

                {!absent && (office.phone || parkPhones.length > 0) && (
                    <Panel title="Телефоны">
                        {office.phone && (
                            <a
                                href={`tel:${office.phone.replace(/[^\d+]/g, '')}`}
                                className="flex items-center gap-2 text-[13.5px] font-medium tabular-nums text-slate-800 hover:text-blue-600"
                            >
                                <Phone size={14} className="text-slate-400" /> {office.phone}
                            </a>
                        )}
                        {/* Номер у парка в этом офисе свой: у одного адреса их
                            столько же, сколько парков за ним сидит. */}
                        {parkPhones.length > 0 && (
                            <div className={`${office.phone ? 'mt-2 border-t border-slate-100 pt-2' : ''} space-y-1`}>
                                {parkPhones.map((link) => (
                                    <div key={link.park_id} className="text-[12.5px] text-slate-600">
                                        <span className="text-slate-400">{link.name}:</span>{' '}
                                        <span className="tabular-nums">
                                            {link.phones
                                                .map((item) => (typeof item === 'string'
                                                    ? item
                                                    : [item.phone, item.note].filter(Boolean).join(' · ')))
                                                .join(', ')}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </Panel>
                )}

                {!absent && (hours || week.length > 0) && (
                    <Panel title="Часы работы">
                        {hours ? (
                            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-slate-700">
                                <span className="flex items-center gap-1.5 tabular-nums">
                                    <Clock size={13} className="text-slate-400" />
                                    {formatDay(dayISO)} {hours.from}–{hours.to}
                                </span>
                                {hours.breakFrom && (
                                    <span className="flex items-center gap-1.5 tabular-nums text-slate-500">
                                        <Coffee size={13} className="text-slate-400" />
                                        обед {hours.breakFrom}–{hours.breakTo}
                                    </span>
                                )}
                            </div>
                        ) : week.length > 0 && (
                            <div className="flex items-center gap-1.5 text-[13px] text-slate-400">
                                <Clock size={13} /> {formatDay(dayISO)} выходной
                            </div>
                        )}

                        {week.length > 0 && (
                            <div className="mt-2 border-t border-slate-100 pt-2">
                                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12.5px] tabular-nums text-slate-600">
                                    {week.map((line) => (
                                        <span key={line.days} className={line.isDayOff ? 'text-slate-400' : ''}>
                                            {line.days}&nbsp;{line.time}
                                        </span>
                                    ))}
                                </div>
                                {lunch.length > 0 && (
                                    <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-[12.5px] text-slate-500">
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
                    </Panel>
                )}

                {/* Список только показывает привязку; меняют её в карточке парка. */}
                {office.parks?.length > 0 && (
                    <Panel title="Таксопарки">
                        <div className="flex flex-wrap items-center gap-1.5">
                            {office.parks.map((link) => (
                                <IosBadge key={link.park_id} tone="slate">{link.name}</IosBadge>
                            ))}
                        </div>
                    </Panel>
                )}
            </div>
        </IosModal>
    );
}
