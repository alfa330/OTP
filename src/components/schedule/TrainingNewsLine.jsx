import React from 'react';
import { Newspaper } from 'lucide-react';
import { formatNewsWindow } from './trainingNews';

/* Какое объявление Oktell оператор читал в интервале «Тренинг» (задача #382).
 *
 * Одна строка на объявление: название и время его окна — с секундами, как у
 * самого интервала, чтобы супервайзер сверил их глазами. Строка занимает всю
 * ширину родителя: в списке интервалов она встаёт под временем и кнопками, а
 * не между ними.
 *
 * Название и время идут одним текстом: длинное название на телефоне
 * переносится, и время просто уходит следом на новую строку.
 *
 * РАЗМЕТКА ВЫБРАНА ПОД ТЕЛЕФОННУЮ ОБОЛОЧКУ (mobile-shell.css), и трогать её
 * стоит, только проверив на телефоне. Оболочка переписывает разделам общие
 * раскладки: ряду на flex с классом gap-* разрешает перенос с отступом в 8 px
 * (время отрывалось от названия), а сетку с grid-cols-[…] сворачивает в одну
 * колонку (значок вставал отдельной строкой). Поэтому здесь flex БЕЗ gap —
 * отступ значку даёт его собственное поле. */
export default function TrainingNewsLine({ matches, className = '' }) {
    const items = Array.isArray(matches) ? matches : [];
    if (items.length === 0) return null;
    return (
        <div className={`w-full space-y-0.5 ${className}`}>
            {items.map((match, index) => (
                <div
                    key={`${match.newsId ?? match.title}-${index}`}
                    className="flex items-start text-[11px] leading-snug"
                    title="Новость в Oktell, которую оператор читал в это время"
                >
                    <Newspaper className="mr-1.5 mt-[2px] h-3 w-3 shrink-0 text-slate-400" aria-hidden="true" />
                    <span className="min-w-0 break-words">
                        <span className="font-medium text-slate-700">{match.title}</span>
                        {' '}
                        <span className="whitespace-nowrap tabular-nums text-slate-400">{formatNewsWindow(match)}</span>
                    </span>
                </div>
            ))}
        </div>
    );
}
