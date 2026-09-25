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
    /* Две колонки по смыслу, а не вперемешку: слева — откуда пришёл лид,
       справа — что стало со сделкой. Подписи — ровной колонкой, строки —
       через тонкий разделитель, как в списках настроек iOS. Пустые поля не
       рисуются: «—» на каждой второй строке — шум. */
    const origin = [
        ['Канал', [deal.channel_title, deal.campaign].filter(Boolean).join(' / ')],
        ['Тип лида', deal.lead_type],
        ['Таксопарк', deal.park_title],
        ['Город', deal.city],
    ].filter(([, value]) => value);
    const fate = [
        ['Этап сейчас', deal.stage],
        /* Пустой «на момент разговора» — не поломка: журнал этапов ведётся с
           момента запуска модуля, и до первой записи по сделке ответа нет.
           Подписываем словами, а не оставляем прочерк. */
        ['На момент разговора', deal.stage_at_call, !deal.stage_at_call && 'журнала ещё не было'],
        ['Причина отказа', deal.reason],
        ['Ответственный', deal.responsible],
    ].filter(([, value, placeholder]) => value || placeholder);
    const column = (rows, className = '') => (
        <dl className={`divide-y divide-slate-200/60 ${className}`}>
            {rows.map(([name, value, placeholder]) => (
                <div key={name} className="grid grid-cols-[minmax(0,9.5rem)_minmax(0,1fr)] gap-x-3 py-1.5 text-[12.5px]">
                    <dt className="text-slate-400">{name}</dt>
                    <dd className={`min-w-0 truncate ${value ? 'text-slate-800' : 'text-slate-400'}`}
                        title={value || placeholder}>{value || placeholder}</dd>
                </div>
            ))}
        </dl>
    );
    return (
        <section className={`rounded-2xl bg-slate-50/80 px-3.5 py-3 ring-1 ring-slate-200/60 ${className}`}>
            <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <Tag size={13} className="text-slate-400" aria-hidden="true" />
                <h3 className="text-[13px] font-semibold text-slate-700">Сделка № {deal.id}</h3>
                {/* Связь по телефону у повторной заявки всегда спорна: человек
                    должен видеть, что номер делят несколько сделок, а не верить
                    привязке вслепую. */}
                {deal.shared_phone > 1 && (
                    <IosBadge tone="amber" title={`По этому номеру ${deal.shared_phone} сделок; разговор отнесён к ${deal.shared_index}-й по времени`}>
                        номер у {deal.shared_phone} сделок
                    </IosBadge>
                )}
            </header>
            {/* Две колонки — только когда карточке есть где их развернуть:
                ревью занимает половину экрана, и на ноутбуке две колонки по
                ~200 px оставляли значениям несколько пикселей. */}
            <div className="mt-1.5 grid grid-cols-1 gap-x-6 2xl:grid-cols-2">
                {column(origin)}
                {/* В одну колонку половины идут подряд — на стыке нужна та же
                    линия, что между строками; в две колонки она не нужна. */}
                {column(fate, origin.length ? 'border-t border-slate-200/60 2xl:border-t-0' : '')}
            </div>
        </section>
    );
}
