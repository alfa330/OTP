import React, { useEffect, useRef, useState } from 'react';
import { Building2, Globe, MapPin, Percent, Phone, Plus, Tag } from 'lucide-react';
import { iosCard, IosBadge } from '../ui/ios';

/* Рельс таксопарков — левая колонка витрины статей.
 *
 * Парки к статьям не привязаны (см. шапку WikiParks), поэтому рельс НЕ фильтр:
 * он справочник под рукой. Оператор читает статью и тут же смотрит комиссию или
 * телефон парка, не уходя со страницы — карточка раскрывается поповером рядом
 * с плиткой, а не переносит человека на другую вкладку.
 */

/* Аббревиатура из названия: «Бизнес Партнёр» → «БП», «Global» → «GL».
   Слова, начинающиеся не с буквы («24», «(NurTaxi)»), в пару не берём —
   иначе у «Такси 24» получилось бы «Т2». */
const parkInitials = (name) => {
    const words = String(name || '').trim().split(/\s+/).filter((w) => /^\p{L}/u.test(w));
    if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
    const single = words[0] || String(name || '').trim();
    return single.slice(0, 2).toUpperCase() || '—';
};

const ParkPopover = ({ park, onOpenDirectory }) => (
    <div
        className={`${iosCard} absolute left-0 top-full z-30 mt-2 w-[248px] max-w-[calc(100vw-48px)] p-3 text-left shadow-[0_12px_32px_rgba(15,23,42,0.16)] lg:left-full lg:top-0 lg:ml-2 lg:mt-0`}
        role="dialog"
        aria-label={park.name}
    >
        <div className="flex items-start gap-2.5">
            <div className="grid h-9 w-9 shrink-0 place-items-center overflow-hidden rounded-xl bg-indigo-50 text-indigo-600">
                {park.logo_url
                    ? <img src={park.logo_url} alt="" className="h-full w-full object-cover" />
                    : <Building2 size={16} />}
            </div>
            <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-semibold leading-snug text-slate-900">{park.name}</div>
                {park.description && (
                    <p className="mt-0.5 line-clamp-2 text-[11.5px] leading-relaxed text-slate-500">
                        {park.description}
                    </p>
                )}
            </div>
        </div>

        <div className="mt-2.5 space-y-1.5 text-[12px] text-slate-600">
            {park.city && (
                <div className="flex items-center gap-1.5"><MapPin size={12} className="text-slate-400" /> {park.city}</div>
            )}
            {park.phone && (
                <div className="flex items-center gap-1.5"><Phone size={12} className="text-slate-400" /> {park.phone}</div>
            )}
            {park.commission != null && (
                <div className="flex items-center gap-1.5 tabular-nums">
                    <Percent size={12} className="text-slate-400" /> комиссия {park.commission}%
                </div>
            )}
            {park.website && (
                <a
                    href={park.website}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 text-indigo-600 hover:underline"
                >
                    <Globe size={12} /> сайт парка
                </a>
            )}
            {!park.city && !park.phone && park.commission == null && !park.website && (
                <div className="text-[11.5px] text-slate-400">Контакты ещё не заполнены</div>
            )}
        </div>

        {park.promotions_count > 0 && (
            <div className="mt-2.5">
                <IosBadge tone="amber"><Tag size={11} /> акций: {park.promotions_count}</IosBadge>
            </div>
        )}

        <button
            type="button"
            onClick={onOpenDirectory}
            className="mt-3 w-full rounded-lg bg-slate-100 py-1.5 text-[12px] font-semibold text-slate-600 transition hover:bg-slate-200 active:scale-[0.98]"
        >
            Открыть справочник
        </button>
    </div>
);

export default function WikiParkRail({ parks, canManage, onOpenParks }) {
    const [openId, setOpenId] = useState(null);
    const railRef = useRef(null);

    /* Поповер закрывается по Esc и по клику мимо — обычная моторика macOS.
       Слушаем на документе, а не рисуем невидимую подложку: подложка перехватила
       бы первый клик по соседней плитке, и парки пришлось бы открывать дважды. */
    useEffect(() => {
        if (openId == null) return undefined;
        const onKey = (e) => { if (e.key === 'Escape') setOpenId(null); };
        const onDown = (e) => { if (!railRef.current?.contains(e.target)) setOpenId(null); };
        document.addEventListener('keydown', onKey);
        document.addEventListener('mousedown', onDown);
        return () => {
            document.removeEventListener('keydown', onKey);
            document.removeEventListener('mousedown', onDown);
        };
    }, [openId]);

    if (!parks.length && !canManage) return null;

    return (
        <aside ref={railRef} className="lg:w-[64px] lg:shrink-0">
            <div className={`${iosCard} flex items-center gap-2 overflow-x-auto p-2 lg:sticky lg:top-4 lg:flex-col lg:items-center lg:overflow-visible lg:py-2.5`}>
                <span className="shrink-0 px-1 text-[9px] font-bold uppercase tracking-[0.08em] text-slate-400 lg:mb-0.5">
                    Парки
                </span>

                {parks.map((park) => (
                    <div key={park.id} className="relative shrink-0">
                        <button
                            type="button"
                            onClick={() => setOpenId(openId === park.id ? null : park.id)}
                            aria-expanded={openId === park.id}
                            title={park.name}
                            className={`relative grid h-[38px] w-[38px] place-items-center overflow-hidden rounded-xl text-[10.5px] font-bold transition active:scale-[0.96] ${
                                openId === park.id
                                    ? 'bg-indigo-600 text-white shadow-[0_3px_8px_rgba(79,70,229,0.35)]'
                                    : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                            }`}
                        >
                            {park.logo_url
                                ? <img src={park.logo_url} alt="" className="h-full w-full object-cover" />
                                : parkInitials(park.name)}
                            {park.promotions_count > 0 && (
                                <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-white bg-amber-400" />
                            )}
                        </button>

                        {openId === park.id && (
                            <ParkPopover
                                park={park}
                                onOpenDirectory={() => { setOpenId(null); onOpenParks?.(); }}
                            />
                        )}
                    </div>
                ))}

                {canManage && (
                    <button
                        type="button"
                        onClick={() => onOpenParks?.()}
                        title="Добавить парк"
                        className="grid h-[38px] w-[38px] shrink-0 place-items-center rounded-xl border border-dashed border-indigo-300 bg-indigo-50 text-indigo-600 transition hover:bg-indigo-100 active:scale-[0.96]"
                    >
                        <Plus size={15} />
                    </button>
                )}
            </div>
        </aside>
    );
}
