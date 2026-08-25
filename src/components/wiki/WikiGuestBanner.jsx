import React from 'react';
import { KeyRound } from 'lucide-react';

import { bannerText } from './guestAccess';

/* Свой срок — тому, кому выдали.
 *
 * Требование владельца дословно: «у самого пользователя должен быть виден срок,
 * когда будет всё истекать». Без этой полосы гость узнаёт об окончании доступа
 * ровно одним способом — открыв статью, которая вчера открывалась, а сегодня
 * отвечает «не найдена». Так выглядит поломка, а не срок.
 *
 * Полоса стоит ПОД ШАПКОЙ и над вкладками, то есть видна на любом экране
 * раздела. Гость приходит по ссылке в одну статью, и вопрос «до какого числа
 * это у меня открыто» возникает у него там же, а не на вкладке, куда он,
 * возможно, и не зайдёт вовсе.
 *
 * Без выдач полосы нет вовсе — не «гостевого доступа нет», а пусто:
 * постоянная строка, которая одиннадцать месяцев в году сообщает «ничего»,
 * это ровно тот шум, которого в разделе быть не должно.
 *
 * Считать здесь нечего: и дата, и «осталось дней» приходят с сервера, у
 * которого календарь алматинский (см. шапку guestAccess.js).
 */
const TONE = {
    /* Янтарный — язык предупреждений раздела; за два дня до конца полоса
       перестаёт быть справкой и становится поводом попросить продление. */
    soon: {
        box: 'border-amber-200 bg-amber-50/70',
        icon: 'bg-amber-100 text-amber-700',
        title: 'text-amber-900',
        detail: 'text-amber-800',
    },
    calm: {
        box: 'border-indigo-200 bg-indigo-50/60',
        icon: 'bg-indigo-100 text-indigo-700',
        title: 'text-indigo-900',
        detail: 'text-indigo-800',
    },
};

export default function WikiGuestBanner({ grants = [] }) {
    const banner = bannerText(grants);
    if (!banner) return null;

    const tone = TONE[banner.urgency] || TONE.calm;
    return (
        <div
            /* role="status", а не alert: это положение дел, а не событие, и
               перебивать им чтение вслух незачем. */
            role="status"
            className={`flex items-center gap-3 rounded-2xl border px-4 py-3 ${tone.box}`}
        >
            <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ${tone.icon}`}>
                <KeyRound size={16} />
            </div>
            <div className="min-w-0">
                <div className={`truncate text-[13px] font-semibold ${tone.title}`}>
                    {banner.title}
                </div>
                <div className={`mt-0.5 truncate text-[11.5px] ${tone.detail}`}>
                    {banner.detail}
                </div>
            </div>
        </div>
    );
}
