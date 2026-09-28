import React from 'react';
import { BookOpen } from 'lucide-react';

/* Обложка книги без картинки: тёмная заливка, значок, название и автор. */
const FILL = 'bg-gradient-to-b from-slate-600 to-slate-800';

export const CoverPlaceholder = ({ title, author }) => (
    <div className={`flex h-full w-full flex-col justify-between ${FILL} p-3 text-white`}>
        <BookOpen size={18} className="text-white/60" />
        <div className="min-w-0">
            <div className="line-clamp-4 font-serif text-[15px] font-semibold leading-snug">{title}</div>
            {author && <div className="mt-1 line-clamp-2 text-[11.5px] text-white/70">{author}</div>}
        </div>
    </div>
);

/* Та же обложка в полёте из карточки в ридер. Нарисована сразу в размер
   книги (scale — во сколько раз книга шире карточки), чтобы в конце полёта
   текст был чётким. Заливка тянется вместе с рамкой, а значок и название —
   отдельные слои у своих углов: им полёт даёт обратный масштаб
   (bookOpening.js: anchor), и буквы не сплющиваются. */
export const FlightPlaceholder = ({ title, author, scale, iconRef, textRef }) => (
    <div className={`absolute inset-0 ${FILL}`}>
        <div ref={iconRef} className="absolute left-0 top-0" style={{ padding: 12 * scale, transformOrigin: '0 0' }}>
            <BookOpen size={18 * scale} className="text-white/60" />
        </div>
        <div
            ref={textRef}
            className="absolute inset-x-0 bottom-0 text-white"
            style={{ padding: 12 * scale, transformOrigin: '0 100%' }}
        >
            <div className="line-clamp-4 font-serif font-semibold leading-snug" style={{ fontSize: 15 * scale }}>{title}</div>
            {author && (
                <div className="line-clamp-2 text-white/70" style={{ fontSize: 11.5 * scale, marginTop: 4 * scale }}>{author}</div>
            )}
        </div>
    </div>
);
