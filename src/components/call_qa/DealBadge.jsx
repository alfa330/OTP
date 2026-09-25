import React from 'react';
import { Tag } from 'lucide-react';
import { IosBadge } from '../ui/ios';

/* Сделка amoCRM рядом с разговором (ТЗ #317, п. 2.1 «обогащение атрибутами сделки»).
 *
 * Один компонент на оба списка и карточку — иначе канал в очереди и канал во
 * «Звонках» разошлись бы на первой же правке подписи.
 *
 * В СТРОКЕ списка — одна метка «Канал · Парк»: по ней маркетолог выбирает, что
 * смотреть, а этап и причина — уже внутри карточки. Пустые оси не рисуются:
 * «— · —» на каждой второй строке — это шум, а не информация. Если сделка
 * есть, но ни канала, ни парка у неё нет, показываем номер: связь всё равно
 * найдена, и это надо видеть.
 *
 * В КАРТОЧКЕ — строка атрибутов целиком, тем же компонентом в режиме `full`.
 */
export const dealLabel = (deal) => {
    if (!deal) return '';
    const parts = [deal.channel_title, deal.park_title].filter(Boolean);
    return parts.length ? parts.join(' · ') : `сделка № ${deal.id}`;
};

export default function DealBadge({ deal, full = false, className = '' }) {
    if (!deal) return null;
    if (!full) {
        return (
            <IosBadge tone="slate" className={className}
                      title={`Сделка amoCRM № ${deal.id}${deal.stage ? ` · ${deal.stage}` : ''}`}>
                <Tag size={11} aria-hidden="true" />{dealLabel(deal)}
            </IosBadge>
        );
    }
    const rows = [
        ['Канал', [deal.channel_title, deal.campaign].filter(Boolean).join(' / ')],
        ['Тип лида', deal.lead_type],
        ['Таксопарк', deal.park_title],
        ['Этап сейчас', deal.stage],
        /* Пустой «на момент разговора» — не поломка: журнал этапов ведётся с
           момента запуска модуля, и до первой записи по сделке ответа нет.
           Подписываем словами, а не оставляем прочерк. */
        ['Этап на момент разговора', deal.stage_at_call || 'журнала на тот момент ещё не было'],
        ['Причина отказа', deal.reason],
        ['Ответственный в CRM', deal.responsible],
        ['Город', deal.city],
    ].filter(([, value]) => value);
    return (
        <div className={`rounded-xl bg-slate-50 px-3 py-2.5 ring-1 ring-slate-200/70 ${className}`}>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px]">
                <span className="inline-flex items-center gap-1 font-semibold text-slate-700">
                    <Tag size={12} aria-hidden="true" />Сделка № {deal.id}
                </span>
                {/* Связь по телефону у повторной заявки всегда спорна: человек
                    должен видеть, что номер делят несколько сделок, а не верить
                    привязке вслепую. */}
                {deal.shared_phone > 1 && (
                    <IosBadge tone="amber" title={`По этому номеру ${deal.shared_phone} сделок; разговор отнесён к ${deal.shared_index}-й по времени`}>
                        номер у {deal.shared_phone} сделок
                    </IosBadge>
                )}
            </div>
            <dl className="mt-1.5 grid gap-x-4 gap-y-1 text-[12.5px] sm:grid-cols-2">
                {rows.map(([name, value]) => (
                    <div key={name} className="flex min-w-0 gap-1.5">
                        <dt className="shrink-0 text-slate-400">{name}</dt>
                        <dd className="min-w-0 truncate text-slate-800" title={value}>{value}</dd>
                    </div>
                ))}
            </dl>
        </div>
    );
}
