import React, { useMemo } from 'react';
import { Pencil, Trash2 } from 'lucide-react';
import { iosCard, IosBadge, IosMenu } from '../ui/ios';
import { durationMinutes, formatDuration, formatDayLong, pluralSessions } from './constants';

/* Список проведённых занятий. Один компонент на все места, где нужен именно
 * список занятий: раскрытая тема, раскрытый сотрудник, день в календаре.
 *
 * Дата — разделителем ВНУТРИ одной карточки, а не карточкой на каждый день.
 * Так вышло не сразу: сначала день был отдельной карточкой с заголовком, и на
 * реальных данных это развалилось — по большинству тем в день проходит одно
 * занятие, то есть на каждую строку приходилось по заголовку, рамке и тени.
 * Девять занятий читались как девять блоков вместо девяти строк.
 */

export default function SessionList({
    sessions = [],
    showPerson = true,
    showTopic = false,
    canManage = false,
    onEdit,
    onDelete,
    emptyText = 'Занятий нет',
}) {
    const days = useMemo(() => {
        const byDate = new Map();
        sessions.forEach((session) => {
            const date = session?.date || '—';
            if (!byDate.has(date)) byDate.set(date, []);
            byDate.get(date).push(session);
        });
        return Array.from(byDate.entries())
            .sort((left, right) => String(right[0]).localeCompare(String(left[0])))
            .map(([date, items]) => ({
                date,
                items: items.slice().sort(
                    (left, right) => String(left.start_time).localeCompare(String(right.start_time)),
                ),
            }));
    }, [sessions]);

    if (sessions.length === 0) {
        return <div className="px-1 py-8 text-center text-[13px] text-slate-400">{emptyText}</div>;
    }

    return (
        <div className={`${iosCard} overflow-hidden`}>
            {days.map(({ date, items }) => (
                <div key={date}>
                    <div className="flex items-baseline justify-between gap-2 border-b border-slate-100 bg-slate-50/70 px-3.5 py-1.5">
                        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                            {formatDayLong(date)}
                        </span>
                        {items.length > 1 && (
                            <span className="text-[11px] tabular-nums text-slate-400">
                                {items.length} {pluralSessions(items.length)}
                            </span>
                        )}
                    </div>

                    <div className="divide-y divide-slate-100">
                        {items.map((session) => {
                            const minutes = durationMinutes(session);
                            const menu = canManage ? [
                                onEdit && {
                                    key: 'edit', label: 'Изменить', icon: Pencil,
                                    onSelect: () => onEdit(session),
                                },
                                onDelete && {
                                    key: 'delete', label: 'Удалить', icon: Trash2, danger: true,
                                    separatorBefore: true, onSelect: () => onDelete(session),
                                },
                            ].filter(Boolean) : [];

                            return (
                                <div key={session.id} className="flex items-start gap-3 px-3.5 py-2">
                                    <span className="mt-[1px] w-[84px] shrink-0">
                                        <span className="block text-[12.5px] font-semibold tabular-nums leading-tight text-slate-800">
                                            {session.start_time}–{session.end_time}
                                        </span>
                                        <span className="block text-[11px] tabular-nums text-slate-400">
                                            {formatDuration(minutes)}
                                        </span>
                                    </span>

                                    <span className="min-w-0 flex-1">
                                        {showPerson && (
                                            <span className="block truncate text-[13px] font-medium text-slate-800">
                                                {session.operator_name || `#${session.operator_id}`}
                                            </span>
                                        )}
                                        {showTopic && (
                                            <span className={`block truncate ${
                                                showPerson
                                                    ? 'text-[12px] text-slate-500'
                                                    : 'text-[13px] font-medium text-slate-800'
                                            }`}
                                            >
                                                {session.reason}
                                            </span>
                                        )}
                                        {session.comment && (
                                            <span className="mt-0.5 block text-[12px] leading-snug text-slate-500">
                                                {session.comment}
                                            </span>
                                        )}
                                        {/* Кто провёл и в какой группе — одной служебной
                                            строкой мелким кеглем: это нужно, но это не то,
                                            что читают в первую очередь. */}
                                        <span className="block truncate text-[11px] text-slate-400">
                                            {session.group_name || 'Без группы'}
                                            {session.created_by_name ? ` · ${session.created_by_name}` : ''}
                                        </span>
                                    </span>

                                    {/* Метка ставится ТОЛЬКО когда занятие в часы не идёт:
                                        это исключение, а «идёт в часы» — норма, и подписывать
                                        норму значило бы красить весь список. */}
                                    {session.count_in_hours === false && (
                                        <IosBadge
                                            className="mt-0.5 !py-0 shrink-0 !text-[10px]"
                                            title="Не учитывается в оплачиваемых часах"
                                        >
                                            не в часы
                                        </IosBadge>
                                    )}

                                    {menu.length > 0 ? (
                                        <span className="grid h-7 w-7 shrink-0 place-items-center">
                                            <IosMenu items={menu} label="Действия с занятием" />
                                        </span>
                                    ) : <span className="w-1 shrink-0" />}
                                </div>
                            );
                        })}
                    </div>
                </div>
            ))}
        </div>
    );
}
