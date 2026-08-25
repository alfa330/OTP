/* Подписи и форматы раздела «Касания».
 *
 * Вынесено из TouchesView отдельным модулем не ради красоты: часть этих правил
 * ОБЯЗАНА совпадать с сервером, и совпадение проверяется тестом
 * (tests/cdr_touch_meta.test.mjs + tests/test_cdr_touches.py). Расхождение между
 * экраном и выгрузкой — самый дорогой сорт расхождения: его замечают уже в
 * переписке с заказчиком, и объяснить его нечем.
 *
 * Конкретно `hms` — двойник `cdr/report.py:hms`. Если один покажет «0:42», а
 * второй «42 с», человек решит, что перед ним разные цифры.
 */

export const TYPE_OUT = 'Исходящий';
export const TYPE_IN = 'Входящий';
export const TYPE_IN_MISSED = 'Входящий (не приняли)';

export const RESULT_TALK = 'Разговор';
export const RESULT_DROPPED = 'Сброс без разговора';
export const RESULT_NO_ANSWER = 'Не ответил';
export const RESULT_BUSY = 'Занято';
export const RESULT_FAILED = 'Не соединился';

/* Тон плашки результата. «Сброс без разговора» жёлтый, а не серый: это не
   «не дозвонились», а «дозвонились и бросили», и разница видна руководителю. */
export const RESULT_TONE = {
    [RESULT_TALK]: 'bg-emerald-50 text-emerald-700 ring-emerald-100',
    [RESULT_DROPPED]: 'bg-amber-50 text-amber-700 ring-amber-100',
    [RESULT_NO_ANSWER]: 'bg-slate-100 text-slate-600 ring-slate-200/70',
    [RESULT_BUSY]: 'bg-slate-100 text-slate-600 ring-slate-200/70',
    [RESULT_FAILED]: 'bg-rose-50 text-rose-600 ring-rose-100',
};

export const resultTone = (result) => RESULT_TONE[result] || RESULT_TONE[RESULT_NO_ANSWER];

/** Секунды → «7:12» или «1:04:30». Двойник cdr/report.py:hms.
 *
 *  Ноль — прочерк, а не «0:00»: «0:00» читается как «разговор был и длился
 *  ноль секунд», а его не было вовсе. */
export const hms = (seconds) => {
    const total = Math.trunc(Number(seconds) || 0);
    if (total <= 0) return '—';
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
};

/** Секунды → часы с одним знаком. Тем же способом, что на листе «Операторы». */
export const hours = (seconds) => Math.round(((Number(seconds) || 0) / 3600) * 10) / 10;

/** Доля в процентах с одним знаком. Ноль знаменателя — ноль, а не NaN. */
export const percent = (part, whole) => (
    whole ? Math.round((1000 * (Number(part) || 0)) / whole) / 10 : 0);

/** Телефон показываем как его набирают, а не как он лежит в базе (десять цифр).
 *  Не десять цифр — отдаём как есть: выдумывать формат для непонятного значения
 *  хуже, чем показать его сырым. */
export const prettyPhone = (value) => {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length !== 10) return value || '—';
    return `+7 ${digits.slice(0, 3)} ${digits.slice(3, 6)} ${digits.slice(6, 8)} ${digits.slice(8)}`;
};

/** «2026-08-24 09:00:00» → «09:00:00». */
export const shortTime = (value) => (value ? String(value).slice(11, 19) : '—');

/** «2026-08-24» → «24.08». Год в таблице не нужен: он есть в шапке периода. */
export const shortDay = (value) => {
    const parts = String(value || '').slice(0, 10).split('-');
    return parts.length === 3 && parts[0] ? `${parts[2]}.${parts[1]}` : '—';
};

/** Сколько молчит мост: «12 мин» / «3 ч» / «2 сут».
 *
 *  Точное число минут на третьи сутки молчания ничего не сообщает — важен
 *  порядок величины, а не цифра. */
export const silence = (minutes) => {
    const value = Math.max(0, Math.trunc(Number(minutes) || 0));
    if (value < 60) return `${value} мин`;
    if (value < 60 * 24) return `${Math.floor(value / 60)} ч`;
    return `${Math.floor(value / 1440)} сут`;
};

/** «2026-08-24» → «24.08.2026». С годом — для имени файла. */
export const fullDay = (value) => {
    const parts = String(value || '').slice(0, 10).split('-');
    return parts.length === 3 && parts[0] ? `${parts[2]}.${parts[1]}.${parts[0]}` : '';
};

/** Имя файла выгрузки. Собирает ФРОНТ, а не сервер: Content-Disposition через
 *  CORS до нас не доходит (фронт живёт на другом origin).
 *
 *  С ГОДОМ, в отличие от подписей в таблице: файл живёт в «Загрузках» месяцами и
 *  сравнивается с прошлогодним, а «Касания 01.08—31.08.xlsx» от такого же файла
 *  за прошлый год не отличить. Совпадает с серверным report_filename. */
export const exportFileName = (from, to) => (
    from === to ? `Касания ${fullDay(from)}.xlsx`
        : `Касания ${fullDay(from)} — ${fullDay(to)}.xlsx`);
