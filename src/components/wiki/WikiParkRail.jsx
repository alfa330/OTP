import React from 'react';
import { Plus } from 'lucide-react';
import { iosCard } from '../ui/ios';

/* Рельс таксопарков — левая колонка витрины статей.
 *
 * Парки к статьям не привязаны (см. шапку WikiParks), поэтому рельс НЕ фильтр:
 * он справочник под рукой. Плитка — две буквы, и сама по себе не говорит
 * ничего: наведение отвечает «что это» — название и комиссия. Двух строк
 * подсказке хватает; всё остальное о парке (контакты, адрес, акции) открывается
 * нажатием — отдельной страницей WikiPark, а не поповером у плитки.
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

/* Подсказка на CSS, а не на состоянии: перерисовывать витрину со всеми её
   списками на каждое движение мыши по плиткам незачем. group-focus-within —
   та же подсказка с клавиатуры: наведение мышью не единственный способ дойти
   до плитки.

   hidden lg:block намеренно: до lg рельс — горизонтальная лента с
   overflow-x-auto, и подсказка обрезалась бы его краем. Наведения там всё
   равно нет, название парка человек получает нажатием. */
const ParkTooltip = ({ park }) => (
    <div
        role="tooltip"
        className="pointer-events-none absolute left-full top-1/2 z-30 ml-2 hidden w-max max-w-[220px] -translate-y-1/2 scale-95 opacity-0 transition duration-150 group-hover:scale-100 group-hover:opacity-100 group-focus-within:scale-100 group-focus-within:opacity-100 lg:block"
    >
        <div className="rounded-xl bg-slate-900/90 px-2.5 py-1.5 text-left shadow-[0_8px_20px_rgba(15,23,42,0.22)] backdrop-blur-sm">
            <div className="text-[12.5px] font-semibold leading-tight text-white">{park.name}</div>
            <div className="mt-0.5 text-[10.5px] leading-tight text-white/65 tabular-nums">
                {park.commission != null
                    ? `комиссия ${park.commission}%`
                    : 'комиссия не указана'}
            </div>
        </div>
    </div>
);

export default function WikiParkRail({ parks, canManage, onOpenPark, onOpenParks }) {
    if (!parks.length && !canManage) return null;

    return (
        /* self-stretch — чтобы sticky-рельс ехал вдоль всей витрины, а не
           «отлипал», когда закончится его собственная высота. */
        <aside className="lg:w-[64px] lg:shrink-0 lg:self-stretch">
            <div className={`${iosCard} flex items-center gap-2 overflow-x-auto p-2 lg:sticky lg:top-4 lg:flex-col lg:items-center lg:overflow-visible lg:py-2.5`}>
                <span className="shrink-0 px-1 text-[9px] font-bold uppercase tracking-[0.08em] text-slate-400 lg:mb-0.5">
                    Парки
                </span>

                {parks.map((park) => (
                    <div key={park.id} className="group relative shrink-0">
                        <button
                            type="button"
                            onClick={() => onOpenPark?.(park.slug)}
                            aria-label={park.name}
                            className="relative grid h-[38px] w-[38px] place-items-center overflow-hidden rounded-xl bg-slate-100 text-[10.5px] font-bold text-slate-500 transition hover:bg-slate-200 active:scale-[0.96]"
                        >
                            {park.logo_url
                                ? <img src={park.logo_url} alt="" className="h-full w-full object-cover" />
                                : parkInitials(park.name)}
                            {park.promotions_count > 0 && (
                                <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-white bg-amber-400" />
                            )}
                        </button>

                        <ParkTooltip park={park} />
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
