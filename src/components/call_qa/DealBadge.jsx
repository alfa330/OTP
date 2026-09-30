import React, { useId, useState } from 'react';
import { ChevronDown, Tag } from 'lucide-react';
import { IosBadge } from '../ui/ios';

/* Раскрыта ли сделка в карточке — удобство одного проверяющего, а не данные:
   хранится в браузере. Хранилище бывает недоступно (приватное окно) — тогда
   просто свёрнуто. */
const DEAL_OPEN_KEY = 'aiqa.dealExpanded';
const readDealOpen = () => {
    try { return window.localStorage.getItem(DEAL_OPEN_KEY) === '1'; } catch { return false; }
};
const writeDealOpen = (open) => {
    try { window.localStorage.setItem(DEAL_OPEN_KEY, open ? '1' : '0'); } catch { /* без памяти */ }
};

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
 * В КАРТОЧКЕ — тем же компонентом в режиме `full`: свёрнута в строку «Сделка №
 * … · канал · этап», по нажатию — атрибуты целиком (DealCard).
 */
export const dealLabel = (deal) => {
    if (!deal) return '';
    const parts = [deal.channel_title, deal.park_title].filter(Boolean);
    return parts.length ? parts.join(' · ') : `сделка № ${deal.id}`;
};

export default function DealBadge({ deal, full = false, className = '' }) {
    if (!deal) return null;
    if (full) return <DealCard deal={deal} className={className} />;
    return (
        <IosBadge tone="slate" className={className}
                  title={`Сделка amoCRM № ${deal.id}${deal.stage ? ` · ${deal.stage}` : ''}`}>
            <Tag size={11} aria-hidden="true" />{dealLabel(deal)}
        </IosBadge>
    );
}

/* Сделка в карточке разговора — строкой шапки, без своей рамки (рамка в рамке
   читалась как отдельная карточка). Свёрнута в «Сделка № … · канал · этап»:
   развёрнутая таблица занимала треть левой колонки, и транскрипту оставалась
   пара строк. Раскрывается по нажатию, выбор запоминается. */
function DealCard({ deal, className = '' }) {
    const [open, setOpen] = useState(readDealOpen);
    const bodyId = useId();
    const toggle = () => {
        writeDealOpen(!open);
        setOpen(!open);
    };
    const summary = [deal.channel_title, deal.stage].filter(Boolean).join(' · ');
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
        <dl className={`divide-y divide-slate-100 ${className}`}>
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
        <section className={className}>
            <h3 className="m-0">
                <button type="button" onClick={toggle} aria-expanded={open} aria-controls={bodyId}
                        className="flex w-full min-w-0 items-center gap-2 px-4 py-2.5 text-left text-[12.5px] transition hover:bg-slate-50 focus-visible:bg-blue-50/60 focus-visible:outline-none sm:px-5">
                    <Tag size={13} className="shrink-0 text-slate-400" aria-hidden="true" />
                    <span className="shrink-0 font-medium text-slate-700">Сделка № {deal.id}</span>
                    {/* Свёрнутой — главное одной строкой: откуда лид и чем кончилось. */}
                    {!open && summary && <span className="min-w-0 truncate text-slate-500">· {summary}</span>}
                    {/* Связь по телефону у повторной заявки всегда спорна: человек
                        должен видеть, что номер делят несколько сделок, а не верить
                        привязке вслепую, — поэтому метка видна и у свёрнутой. */}
                    {deal.shared_phone > 1 && (
                        <IosBadge tone="amber" className="shrink-0 !px-2 !py-0.5"
                                  title={`По этому номеру ${deal.shared_phone} сделок; разговор отнесён к ${deal.shared_index}-й по времени`}>
                            номер у {deal.shared_phone} сделок
                        </IosBadge>
                    )}
                    <ChevronDown size={15} aria-hidden="true"
                                 className={`ml-auto shrink-0 text-slate-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
                </button>
            </h3>
            {/* Две колонки — только когда карточке есть где их развернуть:
                ревью занимает половину экрана, и на ноутбуке две колонки по
                ~200 px оставляли значениям несколько пикселей. */}
            {open && (
                <div id={bodyId} className="grid grid-cols-1 gap-x-6 px-4 pb-2.5 sm:px-5 2xl:grid-cols-2">
                    {column(origin)}
                    {/* В одну колонку половины идут подряд — на стыке нужна та же
                        линия, что между строками; в две колонки она не нужна. */}
                    {column(fate, origin.length ? 'border-t border-slate-100 2xl:border-t-0' : '')}
                </div>
            )}
        </section>
    );
}
