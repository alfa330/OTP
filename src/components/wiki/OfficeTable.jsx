import React from 'react';
import { Archive, ArchiveRestore, CalendarClock, Pencil } from 'lucide-react';
import { iosCard, IosBadge } from '../ui/ios';
import { OfficeDayPill } from './officeBadges';
import {
    DAY_STATE_ROW, DAY_STATE_TEXT, formatDay, officeDayStatus,
} from './officeDayStatus';

/* Таблица ТЗ «Статус офисов по городам»: город, адрес, статус на дату и дата
 * актуальности — двадцать городов одним экраном.
 *
 * Таблица отвечает на вопрос «где сегодня закрыто»: закрытый офис находится, не
 * листая. Карточки отвечают на «как доехать», поэтому это переключатель вида, а
 * не замена карточкам.
 *
 * Строка тонируется целиком — и фон, и текст (требование ТЗ и буква макета):
 * цвет должен считываться сразу, а не точкой в бейдже.
 *
 * Всё остальное про офис — карта, часы, телефоны парков — за нажатием на адрес
 * (OfficeInfoModal). В таблицу это не влезает и влезать не должно.
 */

const CELL = 'px-4 py-2.5 text-[13.5px] align-middle';

/* Шапка липкая: в таблице на двадцать городов заголовки уезжают вверх, и
   «19.08.2026» в последней колонке перестаёт быть понятно чем. Фон обязан быть
   непрозрачным — под шапкой проезжают залитые строки. */
const HEAD = 'sticky top-0 z-10 bg-slate-100 px-4 py-3 text-[11px] font-semibold '
    + 'uppercase tracking-wider text-slate-500 align-middle';

/* Кнопки действий сидят в залитой строке, поэтому подсветка — белая плашка, а
   не цветная: bg-blue-50 на зелёной строке читался бы третьим состоянием. */
const ACTION = 'grid h-8 w-8 place-items-center rounded-full text-slate-500 transition hover:bg-white/80';

/* Пунктир под адресом — единственный намёк, что за строкой есть продолжение.
   Сплошного подчёркивания тут нельзя: адрес больше не ссылка в 2ГИС (она уехала
   в модалку), и синей ссылкой он обещал бы уход со страницы. */
const ADDRESS_LINK = 'text-left underline decoration-dotted decoration-1 '
    + 'underline-offset-[3px] transition hover:decoration-solid';

export default function OfficeTable({
    offices, dayISO, isToday, canManage, onOpen, onEdit, onArchive, onRestore, onMarkDay,
}) {
    return (
        <div className={`${iosCard} overflow-hidden`}>
            {/* Прокрутка внутри рамки: сжимать текст в узком окне нельзя (п. 6 ТЗ),
                а горизонтальная прокрутка всей страницы ломала бы раздел. */}
            <div className="overflow-x-auto">
                <table className="w-full min-w-[860px] border-collapse text-left">
                    <thead>
                        <tr>
                            <th className={`${HEAD} w-[19%]`}>Город</th>
                            <th className={`${HEAD} w-[36%]`}>Адрес офиса</th>
                            <th className={`${HEAD} w-[15%]`}>Телефон</th>
                            {/* За сегодня колонка так и называется — как в макете.
                                На прошлом дне «Статус сегодня» было бы прямой
                                неправдой: показан день, который уже прошёл. */}
                            <th className={`${HEAD} w-[18%]`}>
                                {isToday ? 'Статус сегодня' : 'Статус на дату'}
                            </th>
                            <th className={`${HEAD} w-[12%]`}>Обновлено</th>
                            {canManage && <th className={`${HEAD} w-px`} aria-label="Действия" />}
                        </tr>
                    </thead>
                    <tbody>
                        {offices.map((office, index) => {
                            const status = officeDayStatus(office, dayISO);
                            const absent = status.state === 'absent';
                            const text = DAY_STATE_TEXT[status.state] || DAY_STATE_TEXT.none;
                            return (
                                <tr
                                    key={office.id}
                                    /* Волосок затемнением, а не серой линией: он
                                       обязан читаться и на зелёной строке, и на
                                       тёмно-серой. Первой строке он не нужен —
                                       её сверху уже держит граница шапки.
                                       Высота на строке, а не в отступах ячейки:
                                       иначе управляющему её задавали бы кнопки
                                       действий (32 px), и таблица у него была бы
                                       выше, чем у оператора, — без причины. */
                                    className={`h-[52px] ${index > 0 ? 'border-t border-slate-900/[0.06]' : ''} ${
                                        DAY_STATE_ROW[status.state] || ''}`}
                                >
                                    {/* Колонка ТЗ — город (п. 4.3), поэтому он и
                                        стоит в ней жирным. Название офиса нужно,
                                        когда в городе их несколько: иначе две
                                        строки «Алматы» неразличимы. Когда запись
                                        и есть город, второй строки нет. */}
                                    <td className={`${CELL} font-semibold ${text.city}`}>
                                        <div className="flex flex-wrap items-center gap-1.5">
                                            {office.city || office.name || 'Без города'}
                                            {office.status === 'archived' && (
                                                <IosBadge tone="amber">В архиве</IosBadge>
                                            )}
                                        </div>
                                        {office.city && office.name !== office.city && (
                                            <div className={`text-[11.5px] font-normal ${text.meta}`}>
                                                {office.name}
                                            </div>
                                        )}
                                    </td>

                                    {/* Адрес — вход в подробности: карта, часы,
                                        телефоны парков и ссылка в 2ГИС. У офиса
                                        без адреса вход всё равно нужен — за ним
                                        график и телефон, — поэтому нажимается и
                                        прочерк. Кроме случая «офиса нет»: там за
                                        адресом нет ничего. */}
                                    <td className={`${CELL} ${text.body}`}>
                                        {absent ? (
                                            <span className="italic">Офиса в городе нет</span>
                                        ) : (
                                            <button
                                                type="button"
                                                onClick={() => onOpen(office)}
                                                title="Подробнее об офисе"
                                                className={ADDRESS_LINK}
                                            >
                                                {office.address || '—'}
                                            </button>
                                        )}
                                        {status.note && (
                                            <div className={`text-[11.5px] ${text.meta}`}>{status.note}</div>
                                        )}
                                    </td>

                                    <td className={`${CELL} whitespace-nowrap tabular-nums ${text.body}`}>
                                        {office.phone ? (
                                            <a
                                                href={`tel:${office.phone.replace(/[^\d+]/g, '')}`}
                                                className="font-medium underline-offset-2 hover:underline"
                                            >
                                                {office.phone}
                                            </a>
                                        ) : (
                                            <span className={text.meta}>—</span>
                                        )}
                                    </td>

                                    <td className={CELL}>
                                        <OfficeDayPill state={status.state} label={status.label} />
                                    </td>

                                    {/* «Обновлено» в тон строке: в макете дата у
                                        закрытого офиса красная, и колонка работает
                                        вторым сигналом, а не серой сноской. */}
                                    <td className={`${CELL} whitespace-nowrap font-medium tabular-nums ${text.meta}`}>
                                        {formatDay(status.recordedOn)}
                                    </td>

                                    {canManage && (
                                        <td className={CELL}>
                                            <div className="flex items-center justify-end gap-0.5">
                                                {!absent && (
                                                    <button
                                                        type="button"
                                                        onClick={() => onMarkDay(office)}
                                                        className={`${ACTION} hover:text-blue-600`}
                                                        aria-label="Отметить статус на дату"
                                                    >
                                                        <CalendarClock size={14} />
                                                    </button>
                                                )}
                                                <button
                                                    type="button"
                                                    onClick={() => onEdit(office)}
                                                    className={`${ACTION} hover:text-blue-600`}
                                                    aria-label="Изменить офис"
                                                >
                                                    <Pencil size={14} />
                                                </button>
                                                {office.status === 'active' ? (
                                                    <button
                                                        type="button"
                                                        onClick={() => onArchive(office)}
                                                        className={`${ACTION} hover:text-amber-600`}
                                                        aria-label="Убрать в архив"
                                                    >
                                                        <Archive size={14} />
                                                    </button>
                                                ) : (
                                                    <button
                                                        type="button"
                                                        onClick={() => onRestore(office)}
                                                        className={`${ACTION} hover:text-emerald-700`}
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
