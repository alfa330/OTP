/* Общие классы раздела «Жалобы».
 *
 * Дата и время — тем же видом, что обычное поле формы (iosInput): серая
 * плашка на всю ширину ячейки, как в «Оплате счетов». Кнопка календаря по
 * умолчанию узкая и живёт своим видом, а белое поле времени рядом с серыми
 * полями читалось как деталь из другой формы. */
export const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 '
    + 'text-[14px] tabular-nums text-slate-900 border-0 transition hover:bg-slate-200/70 '
    + 'focus:outline-none focus:ring-2 focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left';

// px-6 с обеих сторон: стрелка выбора лежит поверх поля справа (TimePicker),
// и симметричный отступ держит время по центру, не наезжая на неё.
export const TIME_INPUT = 'w-full rounded-xl border-0 bg-slate-100 px-6 py-2.5 text-center text-[14px] '
    + 'tabular-nums text-slate-900 placeholder-slate-400 transition hover:bg-slate-200/70 '
    + 'focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70 '
    + 'disabled:cursor-not-allowed disabled:opacity-50';
