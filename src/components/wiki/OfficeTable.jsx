import React from 'react';
import { Archive, ArchiveRestore, CalendarClock, Pencil } from 'lucide-react';
import { iosCard, IosBadge } from '../ui/ios';
import {
    DAY_STATE_ROW, DAY_STATE_TONE, formatDay, officeDayStatus,
} from './officeDayStatus';

/* Плотный вид справочника: город, адрес, статус на дату и дата актуальности.
 *
 * Карточки отвечают на вопрос «как доехать» (за этим и мини-карта), таблица —
 * на вопрос «где сегодня закрыто»: двадцать городов видно одним экраном, и
 * закрытый офис находится, не листая. Поэтому это переключатель вида, а не
 * замена карточкам.
 *
 * Строка залита целиком (требование ТЗ): цвет должен считываться сразу, а не
 * точкой в бейдже.
 */

const CELL = 'px-3 py-2.5 text-[13px] align-middle';

/* Шапка липкая: в таблице на двадцать городов заголовки уезжают вверх, и
   «19.08.2026» в четвёртой колонке перестаёт быть понятно чем. */
const HEAD = 'sticky top-0 z-10 bg-slate-50 px-3 py-2.5 text-[11px] align-middle';

/* Полоса города. В таблице деления по городам не было вовсе: «Алматы» просто
   повторялось в трёх строках подряд, и граница между городами читалась только
   вчитыванием. Полоса заменяет повтор — в строках остаётся название офиса. */
function CityBand({ city, count, span }) {
    return (
        <tr>
            <td colSpan={span} className="border-t border-slate-200 bg-slate-100/70 px-3 py-2">
                <div className="flex items-baseline gap-2.5">
                    <span className="text-[15px] font-semibold leading-none tracking-tight text-slate-900">
                        {city}
                    </span>
                    <span className="text-[11.5px] font-medium tabular-nums text-slate-500">
                        {count}
                    </span>
                </div>
            </td>
        </tr>
    );
}

export default function OfficeTable({
    offices, dayISO, canManage, onEdit, onArchive, onRestore, onMarkDay,
    groups = null, officeCount = (n) => n,
}) {
    const span = 5 + (canManage ? 1 : 0);
    // Плоский список остаётся плоским: при сортировке по названию или статусу
    // полосы городов встали бы поперёк выбранного порядка.
    const rows = groups
        ? groups.flatMap((group) => [
            { band: group.city, count: group.items.length },
            ...group.items.map((office) => ({ office })),
        ])
        : offices.map((office) => ({ office }));

    return (
        <div className={`${iosCard} overflow-hidden`}>
            {/* Прокрутка внутри рамки: сжимать текст в узком окне нельзя (п. 6 ТЗ),
                а горизонтальная прокрутка всей страницы ломала бы раздел. */}
            <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] border-collapse">
                    <thead>
                        <tr className="text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                            {/* Под полосами городов в первой колонке стоит офис, и
                                заголовок «Город» врал бы о её содержимом. */}
                            <th className={`${HEAD} font-semibold`}>{groups ? 'Офис' : 'Город'}</th>
                            <th className={`${HEAD} font-semibold`}>Адрес офиса</th>
                            <th className={`${HEAD} font-semibold`}>Телефон</th>
                            <th className={`${HEAD} font-semibold`}>Статус на дату</th>
                            <th className={`${HEAD} font-semibold`}>Обновлено</th>
                            {canManage && <th className={`${HEAD} w-1`} aria-label="Действия" />}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => {
                            if (row.band) {
                                return (
                                    <CityBand
                                        key={`city:${row.band}`}
                                        city={row.band}
                                        count={officeCount(row.count)}
                                        span={span}
                                    />
                                );
                            }
                            const office = row.office;
                            const status = officeDayStatus(office, dayISO);
                            const absent = status.state === 'absent';
                            return (
                                <tr
                                    key={office.id}
                                    className={`border-t border-slate-200/70 ${DAY_STATE_ROW[status.state] || ''}`}
                                >
                                    <td className={`${CELL} font-semibold text-slate-900`}>
                                        <div className="flex flex-wrap items-center gap-1.5">
                                            {/* Под полосой города повторять город незачем —
                                                строку называет офис. Без полосы город
                                                обязателен: «Навигатор» сам по себе не адрес. */}
                                            {groups ? office.name : (office.city || 'Без города')}
                                            {office.status === 'archived' && (
                                                <IosBadge tone="amber">В архиве</IosBadge>
                                            )}
                                        </div>
                                        {/* Название нужно, когда в городе несколько офисов:
                                            иначе две строки «Алматы» неразличимы. Но у
                                            записи о городе оно и есть город — тогда молчим. */}
                                        {!groups && office.name !== office.city && (
                                            <div className="text-[11.5px] font-normal text-slate-500">
                                                {office.name}
                                            </div>
                                        )}
                                    </td>

                                    <td className={`${CELL} text-slate-700`}>
                                        {absent ? (
                                            <span className="italic text-slate-500">Офиса в городе нет</span>
                                        ) : office.map_url && office.address ? (
                                            <a
                                                href={office.map_url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="hover:text-blue-600 hover:underline"
                                            >
                                                {office.address}
                                            </a>
                                        ) : (
                                            office.address || <span className="text-slate-400">—</span>
                                        )}
                                        {status.note && (
                                            <div className="text-[11.5px] text-slate-500">{status.note}</div>
                                        )}
                                    </td>

                                    <td className={`${CELL} tabular-nums`}>
                                        {office.phone ? (
                                            <a
                                                href={`tel:${office.phone.replace(/[^\d+]/g, '')}`}
                                                className="font-medium text-slate-700 hover:text-blue-600"
                                            >
                                                {office.phone}
                                            </a>
                                        ) : (
                                            <span className="text-slate-400">—</span>
                                        )}
                                    </td>

                                    <td className={CELL}>
                                        <IosBadge tone={DAY_STATE_TONE[status.state]}>{status.label}</IosBadge>
                                    </td>

                                    <td className={`${CELL} tabular-nums text-slate-500`}>
                                        {formatDay(status.recordedOn)}
                                    </td>

                                    {canManage && (
                                        <td className={CELL}>
                                            <div className="flex items-center justify-end gap-0.5">
                                                {!absent && (
                                                    <button
                                                        type="button"
                                                        onClick={() => onMarkDay(office)}
                                                        className="grid h-8 w-8 place-items-center rounded-full text-slate-400 transition hover:bg-blue-50 hover:text-blue-600"
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
                                        </td>
                                    )}
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
