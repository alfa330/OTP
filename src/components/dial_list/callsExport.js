/*
 * Детализация звонков раздела «Удаленный КЦ» (владелец, 15.10.2026): период, пресеты и имя
 * файла — без React, чтобы на правила был node-тест (tests/dial_list_calls_export.test.mjs).
 *
 * Двойник на сервере — dial_list/calls_export.py: потолок периода (MAX_DAYS) и имя файла
 * (report_filename). Разойтись им нельзя: период длиннее потолка сервер отклонит, а кнопка
 * «Подтвердить» гаснет раньше запроса; имя скачанного файла ставит сайт — заголовок
 * Content-Disposition через CORS сюда не доходит.
 */

// Сколько дней за раз: в 31 день помещается любой календарный месяц (отчёт за месяц — самый
// частый). Двойник — calls_export.MAX_DAYS.
export const EXPORT_MAX_DAYS = 31;

const pad = (n) => String(n).padStart(2, '0');

/** Дата → 'YYYY-MM-DD' по местному времени браузера. */
export const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

const daysBefore = (now, days) => {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    d.setDate(d.getDate() - days);
    return isoDay(d);
};

/**
 * Пресеты под календарём — самые частые периоды: день, вчера (разбор вчерашней работы),
 * неделя и текущий месяц с первого числа (итоги месяца). «Весь период» здесь нет: выгрузка
 * за всё время не нужна никому, и сервер дальше 31 дня её не соберёт.
 * now — для теста; в разделе — текущий момент при нажатии.
 */
export const exportPresets = (now = () => new Date()) => [
    { label: 'Сегодня', range: () => { const today = isoDay(now()); return { from: today, to: today }; } },
    { label: 'Вчера', range: () => { const day = daysBefore(now(), 1); return { from: day, to: day }; } },
    { label: '7 дней', range: () => ({ from: daysBefore(now(), 6), to: isoDay(now()) }) },
    {
        label: 'Месяц',
        range: () => {
            const d = now();
            return { from: isoDay(new Date(d.getFullYear(), d.getMonth(), 1)), to: isoDay(d) };
        },
    },
];

/** Сколько дней в периоде, обе границы включительно; 0 — период не выбран. */
export const rangeDays = (from, to) => {
    if (!from || !to) return 0;
    const [fy, fm, fd] = from.split('-').map(Number);
    const [ty, tm, td] = to.split('-').map(Number);
    // В UTC: перевод часов на летнее время не должен съедать или добавлять день.
    return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 864e5) + 1;
};

/** «не больше 31 дня», «не больше 7 дней» — родительный падеж после «больше». */
export const maxDaysLabel = (days = EXPORT_MAX_DAYS) => (
    `${days} ${days % 10 === 1 && days % 100 !== 11 ? 'дня' : 'дней'}`
);

const ruDate = (iso) => {
    const [y, m, d] = String(iso || '').split('-');
    return `${d}.${m}.${y}`;
};

/** «Детализация звонков 01.10.2026 — 15.10.2026.xlsx» — как report_filename на сервере. */
export const exportFileName = (from, to) => {
    const period = !to || from === to ? ruDate(from) : `${ruDate(from)} — ${ruDate(to)}`;
    return `Детализация звонков ${period}.xlsx`;
};
