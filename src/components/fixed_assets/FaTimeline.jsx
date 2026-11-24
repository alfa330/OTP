import React, { useMemo } from 'react';
import {
    Archive, ArrowRightLeft, Check, FileCheck2, FileUp, PackageCheck, PackagePlus, Pencil, RefreshCw, ScanBarcode, Send,
    ShieldCheck, X,
} from 'lucide-react';
import { KIND_LABELS, eventFiles, eventLines, fmtDayLong, fmtTime, groupByDay, shortName } from './fixedAssetsMeta';
import { Group, GroupTitle, IconCircle } from './fixedAssetsUi';

/*
 * Лента истории: кто, когда, что сделал, какие файлы приложил (ТЗ, правило «История»).
 * Записи собраны по дням; у каждой — значок события, время и кто это сделал. Строки только
 * читаются — на сервере историю нельзя ни править, ни удалять.
 */
const EVENT_LOOK = {
    registered: [PackagePlus, 'green'],
    corrected: [Pencil, 'amber'],
    submitted: [Send, 'blue'],
    checked: [ShieldCheck, 'amber'],
    scan_attached: [FileUp, 'blue'],
    scan_replaced: [RefreshCw, 'blue'],
    original_received: [FileCheck2, 'violet'],
    closed: [Check, 'green'],
    rejected: [X, 'rose'],
    moved: [ArrowRightLeft, 'blue'],
    written_off: [Archive, 'slate'],
    need_accepted: [PackageCheck, 'green'],
    barcode_set: [ScanBarcode, 'blue'],
};

/* subject — в журнале раздела у записи подписано, о каком ОС речь; requestRef — ссылка на
   запрос (в карточке самого запроса она лишняя). */
export default function FaTimeline({ events = [], onOpenRequest, onOpenAsset, subject = false, requestRef = true }) {
    const groups = useMemo(() => groupByDay(events), [events]);
    if (!events.length) return <div className="py-10 text-center text-[13.5px] text-slate-500">Записей пока нет</div>;
    return (
        <div className="space-y-4">
            {groups.map((group) => (
                <section key={group.day}>
                    <GroupTitle>{fmtDayLong(group.day)}</GroupTitle>
                    <Group>
                        {group.events.map((event) => {
                            const [Icon, tone] = EVENT_LOOK[event.kind] || [Check, 'slate'];
                            const lines = eventLines(event);
                            const files = eventFiles(event);
                            const photos = files.filter((file) => file.kind !== 'act_scan').length;
                            const scans = files.length - photos;
                            const title = subject ? (event.asset_name || event.items_title) : '';
                            const meta = [
                                shortName(event.actor_name),
                                photos ? `фото: ${photos}` : '',
                                scans ? 'скан акта' : '',
                            ].filter(Boolean).join(' · ');
                            return (
                                <div key={event.id} className="flex gap-3 px-4 py-3">
                                    <IconCircle icon={Icon} tone={tone} size={30} soft className="mt-0.5" />
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-baseline justify-between gap-3">
                                            <span className="min-w-0 text-[14px] font-medium text-slate-900">{event.label}</span>
                                            <span className="shrink-0 text-[12.5px] tabular-nums text-slate-400">{fmtTime(event.created_at)}</span>
                                        </div>
                                        {title && (event.asset_id && event.asset_open ? (
                                            <button type="button" onClick={() => onOpenAsset?.(event.asset_id)}
                                                    className="block max-w-full truncate text-left text-[13px] text-slate-700 hover:text-blue-600">
                                                {title}{event.inventory_number ? ` · ${event.inventory_number}` : ''}
                                            </button>
                                        ) : (
                                            <div className="truncate text-[13px] text-slate-700">
                                                {title}{event.asset_id && event.inventory_number ? ` · ${event.inventory_number}` : ''}
                                            </div>
                                        ))}
                                        {lines.map((line, index) => (
                                            // eslint-disable-next-line react/no-array-index-key
                                            <div key={index} className="break-words text-[13px] text-slate-500">{line}</div>
                                        ))}
                                        <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[12.5px] text-slate-400">
                                            {meta && <span>{meta}</span>}
                                            {requestRef && event.request_id && (
                                                <>
                                                    {meta && <span aria-hidden="true">·</span>}
                                                    <button type="button" className="text-blue-600 hover:underline" onClick={() => onOpenRequest?.(event.request_id)}>
                                                        {(event.request_kind && KIND_LABELS[event.request_kind]) || 'Запрос'} № {event.request_id}
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </Group>
                </section>
            ))}
        </div>
    );
}
