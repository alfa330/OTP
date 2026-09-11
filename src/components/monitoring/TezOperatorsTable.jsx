import React from 'react';
import { Section } from './SzovWallboardTiles';
import { formatCount, formatSeconds, tezStatusChip } from './tezWallboardShared';

/*
 * Список операторов направления: кто в каком статусе и что у него за день.
 *
 * Зачем он рядом с плитками. Плитки отвечают на вопрос «что с направлением», а руководителю в
 * зале нужен и второй — «что с человеком»: сколько людей на перерыве, видно и по плитке, а вот
 * кто именно и сколько звонков принял сегодня — только здесь.
 *
 * Два источника на одном экране — осознанная плата. Статус строки приходит из событий
 * iCORE Phone (наша база), счётчики звонков — из кабинета Binotel, и в редкую минуту они могут
 * разойтись: телефон и кабинет узнают о начале разговора не в одну и ту же секунду, а телефон,
 * который ещё не обновился, статусов не шлёт вовсе. Поэтому источник статусов назван подписью
 * секции, а строка без событий помечена «Нет событий» и приглушена — это отсутствие данных, а
 * не состояние человека, и выдавать одно за другое на стене нельзя.
 *
 * Колонки разные у ТП и ОП, и это не украшение: у отдела продаж входящих нет вовсе (проверено
 * на живом дне — incomingSuccess = 0 у всех семи номеров), поэтому «Принято» и «Пропущено»
 * стояли бы там вечными нулями, а главная величина дня — набранные и дозвонившиеся.
 */

/** Каталог колонок: подпись, как достать число из строки, и подсказка для узкого экрана. */
const COLUMNS = {
    tp: [
        { key: 'served', label: 'Принято', read: (s) => formatCount(s.served) },
        { key: 'missed', label: 'Пропущено', read: (s) => formatCount(s.missed) },
        { key: 'avg_talk', label: 'Ср. разговор', read: (s) => formatSeconds(s.avg_talk_seconds) },
        {
            key: 'outgoing',
            label: 'Исходящие',
            hint: 'набрано / дозвон',
            read: (s) => formatCount(s.outgoing_total),
            readSecondary: (s) => formatCount(s.outgoing_success),
        },
    ],
    op: [
        { key: 'dialed', label: 'Набрано', read: (s) => formatCount(s.outgoing_total) },
        { key: 'reached', label: 'Дозвонились', read: (s) => formatCount(s.outgoing_success) },
        {
            key: 'avg_out_talk',
            label: 'Ср. разговор',
            hint: 'на исходящий',
            read: (s) => formatSeconds(s.avg_outgoing_talk_seconds),
        },
        {
            key: 'talk_total',
            label: 'В разговоре',
            hint: 'за день',
            read: (s) => formatSeconds(s.outgoing_talk_seconds),
        },
    ],
};

/** Строка человека. Счётчиков нет вовсе, когда номер не нашёлся в кабинете — тогда прочерки. */
const OperatorRow = ({ row, columns, nameSize }) => {
    const chip = tezStatusChip(row);
    const stats = row.stats || null;
    return (
        <tr className={`border-t border-slate-100 ${chip.muted ? 'opacity-60' : ''}`}>
            <td className="py-2.5 pr-3">
                <span className="leading-snug text-slate-800" style={{ fontSize: nameSize }}>
                    {row.name}
                </span>
            </td>
            <td className="py-2.5 pr-3">
                <span className={`inline-block rounded-md px-2 py-0.5 text-[12.5px] font-medium ${chip.className}`}>
                    {chip.label}
                </span>
            </td>
            {/* Время в статусе: события у телефона бывают и без него (первое после входа),
                поэтому прочерк здесь — обычное дело, а не поломка. */}
            <td className="py-2.5 pr-3 text-[14px] font-medium tabular-nums text-slate-500">
                {formatSeconds(row.status_seconds)}
            </td>
            {columns.map((column) => (
                <td key={column.key} className="py-2.5 pr-3 text-right text-[15px] font-medium tabular-nums text-slate-700">
                    {stats ? column.read(stats) : '—'}
                    {/* Второе число пары отделяем косой чертой, как в плитках (StatTile):
                        без неё «8 3» со стены читается как восемьдесят три. */}
                    {stats && column.readSecondary ? (
                        <span className="text-[13px] font-normal text-slate-400">
                            /{column.readSecondary(stats)}
                        </span>
                    ) : null}
                </td>
            ))}
        </tr>
    );
};

export default function TezOperatorsTable({ rows, direction, scale = 1 }) {
    const columns = COLUMNS[direction] || COLUMNS.tp;
    const items = Array.isArray(rows) ? rows : [];
    const nameSize = `clamp(0.9375rem, ${(1 * scale).toFixed(2)}vw, ${(1.125 * scale).toFixed(3)}rem)`;
    // Сколько человек не прислали ни одного события: пока флот телефонов обновляется, это
    // ровно те строки, где статус неизвестен. Молча показывать их серыми мало — руководитель
    // должен видеть, что дело в телефоне, а не в человеке.
    const silent = items.filter((row) => row.status_key === 'unknown').length;

    return (
        <Section
            icon="fa-users"
            /* Не просто «Операторы»: у ТП так уже названа секция с плитками направления, и два
               одинаковых заголовка на одном экране заставляют искать между ними разницу. */
            title="Операторы поимённо"
            right={(
                <span className="hidden text-right text-[12.5px] leading-tight text-slate-400 sm:block">
                    Статусы — по событиям iCORE Phone
                    {silent > 0 ? ` · без событий: ${silent}` : ''}
                </span>
            )}
        >
            {items.length === 0 ? (
                <div className="py-1.5 text-[15px] text-slate-400">Состав направления пуст</div>
            ) : (
                /* Таблица — единственное место табло, где строк может не хватить по ширине:
                   на телефоне её горизонтальная прокрутка своя, страница при этом не едет. */
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[34rem] border-collapse text-left">
                        <thead>
                            <tr className="text-[12.5px] font-medium uppercase tracking-wide text-slate-400">
                                <th className="pb-2 pr-3 font-medium">Сотрудник</th>
                                <th className="pb-2 pr-3 font-medium">Статус</th>
                                <th className="pb-2 pr-3 font-medium">В статусе</th>
                                {columns.map((column) => (
                                    <th key={column.key} className="pb-2 pr-3 text-right font-medium">
                                        {column.label}
                                        {column.hint ? (
                                            <span className="normal-case text-slate-300">{` ${column.hint}`}</span>
                                        ) : null}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((row) => (
                                <OperatorRow
                                    key={row.operator_id ?? row.name}
                                    row={row}
                                    columns={columns}
                                    nameSize={nameSize}
                                />
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </Section>
    );
}
