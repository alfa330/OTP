import React from 'react';
import { Archive, ArchiveRestore, CalendarClock, Pencil } from 'lucide-react';
import { iosCard, IosBadge } from '../ui/ios';
import { OfficeDayPill } from './officeBadges';
import {
    DAY_SOURCE_LABELS, DAY_STATE_ROW, DAY_STATE_TEXT, formatStamp, formatStampTime,
    officeDayStatus, statusUntil,
} from './officeDayStatus';
import { officeStatus } from './officeSchedule';

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

/* Шапка. Высота числом, а не отступами: строки таблицы тоже заданы числом, и
   разнобой в 3 px между шапкой и первой строкой виден. Нижняя граница — как в
   макете (#B4B2A9): под шапкой сразу идёт полоса города такого же семейства
   серого, и без границы они читались одним блоком.

   Липкой шапки здесь быть НЕ МОЖЕТ, и это не недоделка: рамка обязана уметь
   прокручиваться по горизонтали (п. 6 ТЗ), а `overflow-x: auto` по стандарту
   делает контейнер прокручиваемым и по вертикали. Значит, `sticky` внутри него
   считается относительно самой рамки, а она по вертикали не прокручивается —
   заголовки просто уезжают вместе со страницей. Замер 21.08.2026: при
   scrollY = 900 шапка с `sticky top-0` стояла на -679. Прилипание вернётся
   только вместе с ограничением высоты таблицы, а это отдельное решение. */
const HEAD = 'h-[42px] border-b border-slate-300 bg-slate-100 px-4 text-[11px] '
    + 'font-semibold uppercase tracking-wider text-slate-500 align-middle';

/* Полоса города. Без неё в таблице на 45 офисов «Алматы» повторялось восемью
   строками подряд, и граница между городами читалась только вчитыванием: строки
   уже залиты цветом состояния, и обычный разделитель в этой заливке терялся.
   Полоса заменяет повтор — в строках остаётся название офиса.

   Тон на ступень темнее шапки: одинаковый серый склеивал первую полосу с
   шапкой в один блок. Темнее полосы «нет офиса» (slate-300) быть тоже нельзя —
   иначе полоса читалась бы как строка с состоянием. */
const BAND = 'border-t border-slate-300 bg-slate-200 px-4 py-2';

/* Кнопки действий сидят в залитой строке, поэтому подсветка — белая плашка, а
   не цветная: bg-blue-50 на зелёной строке читался бы третьим состоянием. */
const ACTION = 'grid h-8 w-8 place-items-center rounded-full text-slate-500 transition hover:bg-white/80';

/* Пунктир под адресом — единственный намёк, что за строкой есть продолжение.
   Сплошного подчёркивания тут нельзя: адрес больше не ссылка в 2ГИС (она уехала
   в модалку), и синей ссылкой он обещал бы уход со страницы. */
const ADDRESS_LINK = 'text-left underline decoration-dotted decoration-1 '
    + 'underline-offset-[3px] transition hover:decoration-solid';

const CityBand = ({ city, count, span, first }) => (
    <tr>
        {/* Первой полосе граница сверху не нужна: её держит граница шапки. */}
        <td className={`${BAND} ${first ? 'border-t-0' : ''}`} colSpan={span}>
            <div className="flex items-baseline gap-2.5">
                <span className="text-[14px] font-bold leading-none tracking-tight text-slate-900">
                    {city}
                </span>
                {count && (
                    <span className="text-[11.5px] font-medium tabular-nums text-slate-500">
                        {count}
                    </span>
                )}
            </div>
        </td>
    </tr>
);

export default function OfficeTable({
    offices, groups = null, officeCount = (n) => n, dayISO, isToday, tick, canManage,
    onOpen, onEdit, onArchive, onRestore, onMarkDay,
}) {
    const span = 5 + (canManage ? 1 : 0);
    /* Плоский список остаётся плоским: при сортировке по названию или статусу
       полосы городов встали бы поперёк выбранного порядка, и «сначала открытые»
       выглядело бы сломанным. Тогда город возвращается в первую колонку. */
    const rows = groups
        ? groups.flatMap((group) => [
            // Записи «офиса в городе нет» в счёт не идут: «Балхаш · 1 офис» над
            // строкой «Офиса в городе нет» — прямое противоречие. Когда считать
            // нечего, числа рядом с городом просто нет.
            { band: group.city, count: group.items.filter((item) => !item.no_office).length },
            ...group.items.map((office) => ({ office })),
        ])
        : offices.map((office) => ({ office }));

    return (
        <div className={`${iosCard} overflow-hidden`}>
            {/* Прокрутка внутри рамки: сжимать текст в узком окне нельзя (п. 6 ТЗ),
                а горизонтальная прокрутка всей страницы ломала бы раздел. */}
            <div className="overflow-x-auto">
                <table className="w-full min-w-[860px] border-collapse text-left">
                    <thead>
                        <tr>
                            {/* Под полосами городов в первой колонке стоит офис,
                                и заголовок «Город» врал бы о её содержимом. */}
                            <th className={`${HEAD} w-[19%]`}>{groups ? 'Офис' : 'Город'}</th>
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
                        {rows.map((row, index) => {
                            if (row.band) {
                                return (
                                    <CityBand
                                        key={`city:${row.band}`}
                                        city={row.band}
                                        count={row.count ? officeCount(row.count) : null}
                                        span={span}
                                        first={index === 0}
                                    />
                                );
                            }
                            const office = row.office;
                            const status = officeDayStatus(office, dayISO);
                            const absent = status.state === 'absent';
                            const text = DAY_STATE_TEXT[status.state] || DAY_STATE_TEXT.none;
                            // Живой расчёт нужен только сегодняшнему дню: за
                            // прошедший «до завтра 10:00» было бы выдумкой.
                            // tick в зависимостях не нужен — он и так перерисовывает
                            // таблицу раз в минуту, а officeStatus берёт время из часов.
                            const until = statusUntil(
                                status, isToday ? officeStatus(office.schedule) : null, dayISO);
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
                                    className={`h-[52px] ${
                                        rows[index - 1]?.office ? 'border-t border-slate-900/[0.06]' : ''
                                    } ${DAY_STATE_ROW[status.state] || ''}`}
                                >
                                    {/* Под полосой города повторять город незачем —
                                        строку называет офис. Без полосы город
                                        обязателен (колонка ТЗ, п. 4.3), а название
                                        уходит второй строкой: иначе две строки
                                        «Алматы» неразличимы. */}
                                    <td className={`${CELL} font-semibold ${text.city}`}>
                                        <div className="flex flex-wrap items-center gap-1.5">
                                            {groups
                                                ? office.name
                                                : (office.city || office.name || 'Без города')}
                                            {office.status === 'archived' && (
                                                <IosBadge tone="amber">В архиве</IosBadge>
                                            )}
                                        </div>
                                        {!groups && office.city && office.name !== office.city && (
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

                                    {/* Срок — второй строкой, а не внутри бейджа:
                                        бейдж отвечает на вопрос ТЗ «открыт или
                                        закрыт» и должен читаться одним взглядом
                                        по всей колонке, а «до завтра 10:00»
                                        растянуло бы его втрое и сломало колонку. */}
                                    <td className={CELL}>
                                        <OfficeDayPill state={status.state} label={status.label} />
                                        {until && (
                                            <div className={`mt-1 text-[11.5px] tabular-nums ${text.meta}`}>
                                                {until}
                                            </div>
                                        )}
                                    </td>

                                    {/* «Обновлено» в тон строке: в макете дата у
                                        закрытого офиса красная, и колонка работает
                                        вторым сигналом, а не серой сноской.
                                        В подсказке — минуты и источник: сама дата
                                        не отвечает, кто это записал, а разница
                                        между отметкой дежурного и ночным снимком
                                        для дежурного как раз и есть ответ. */}
                                    <td
                                        className={`${CELL} whitespace-nowrap font-medium tabular-nums ${text.meta}`}
                                        title={status.updatedAt
                                            ? `${formatStampTime(status.updatedAt)} · ${
                                                DAY_SOURCE_LABELS[status.source] || ''}`
                                            : undefined}
                                    >
                                        {formatStamp(status.updatedAt)}
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
