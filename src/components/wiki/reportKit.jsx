import React from 'react';
import { iosCard, iosGroupLabel, IosHint, IosPager } from '../ui/ios';

/* Кирпичи отчётных экранов раздела «Вики»: плитка-показатель и таблица.
 *
 * Жили внутри WikiTrainerStats и оттуда не экспортировались. Со вторым отчётом
 * («Аналитика») выбор был из двух: скопировать их или вынести. Скопировать
 * значило бы завести две таблицы с одинаковым названием и разной вёрсткой —
 * расходиться они начали бы с первой же правки отступа, и это ровно тот сорт
 * расхождения, который замечают не разработчики, а заказчик на созвоне.
 *
 * Поэтому вынесено сюда, и оба экрана берут отсюда. Правило простое: правка
 * этого файла меняет ОБА отчёта раздела, и проверять надо оба.
 *
 * ПОДПИСИ ЧИТАЮТСЯ, А НЕ УГАДЫВАЮТСЯ. Мелкий текст здесь — slate-500, а не
 * slate-400: серый 400-й на белом даёт контраст около 2.8:1 при норме 4.5:1
 * для мелкого шрифта, и подпись под числом превращается в украшение, которое
 * никто не читает. Ровно этими подписями объясняется, что значит число, —
 * значит, они обязаны быть читаемыми.
 *
 * ОБЪЯСНЕНИЕ ЖИВЁТ РЯДОМ С ЧИСЛОМ. Определение показателя («что такое
 * прочтение») даётся через `help` — подсказку «i» (IosHint): нужна она один
 * раз, а место занимала бы всегда.
 */

/** Плитка-показатель: подпись, число, необязательная оговорка под ним.
 *
 *  `hint`  — короткая оговорка, видна всегда (расшифровка знаменателя).
 *  `help`  — определение показателя, спрятано под «i».
 *
 *  «Аналитика» пользуется только `help`: решение владельца 25.08.2026 — на
 *  экране остаются числа и названия, объяснения уходят под «i». `hint` жив
 *  ради статистики тренажёров, где подпись — это единица измерения
 *  («медиана», «в среднем»), а не пояснение.
 */
export const Metric = ({ label, value, hint = null, help = null,
                         helpAlign = 'left', tone = null }) => (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
        <div className="flex items-center gap-1.5">
            <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
                {label}
            </div>
            {help && <IosHint text={help} align={helpAlign} label={`Как считается «${label}»`} />}
        </div>
        <div className={`mt-0.5 text-[19px] font-semibold leading-none ${
            tone === 'bad' ? 'text-rose-600'
                : tone === 'warn' ? 'text-amber-600'
                    : tone === 'good' ? 'text-emerald-600' : 'text-slate-900'}`}>
            {value}
        </div>
        {hint && <div className="mt-1 text-[11.5px] leading-snug text-slate-500">{hint}</div>}
    </div>
);

export const Th = ({ children, right = false }) => (
    <th className={`whitespace-nowrap px-3 py-2 text-[11.5px] font-medium uppercase
                    tracking-wide text-slate-500 ${right ? 'text-right' : 'text-left'}`}>
        {children}
    </th>
);

export const Td = ({ children, right = false, muted = false }) => (
    <td className={`px-3 py-2 text-[12.5px] ${right ? 'text-right tabular-nums' : ''}
                    ${muted ? 'text-slate-500' : 'text-slate-700'}`}>
        {children}
    </td>
);

/** Таблица с подписью и «пусто».
 *
 *  `count` — строк показано, `total` — сколько их всего. Разные числа
 *  подписываются прямо в заголовке («· 20 из 57»): таблица режется потолком
 *  строк, и без этого обрез читается как «просрочек ровно двадцать».
 *  `badge` — состояние выборки (например, сужение по отделу) справа.
 *  `help`  — пояснение под «i». Видимой подписи под таблицей у набора нет
 *  намеренно: пояснение нужно один раз, а место занимало бы всегда.
 *  `footer` — управление под таблицей; туда PagedTable кладёт пейджер.
 */
export const Table = ({ title, icon: Icon, count, total = null, empty, head,
                        children, help = null, badge = null,
                        footer = null }) => (
    <section className="space-y-1.5">
        <div className="flex items-center gap-1.5 pr-1">
            <div className={iosGroupLabel}>
                {Icon && <Icon size={12} className="mr-1 inline align-[-1px]" />}
                {title}
                {count !== undefined ? ` · ${count}` : ''}
                {total !== null && count !== undefined && total > count ? ` из ${total}` : ''}
            </div>
            {help && <IosHint text={help} label={`Как считается «${title}»`} />}
            {badge && <span className="ml-auto">{badge}</span>}
        </div>
        <div className={`${iosCard} overflow-hidden`}>
            {count === 0 ? (
                <p className="px-4 py-3 text-[12.5px] text-slate-500">{empty}</p>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                        <thead className="border-b border-slate-100">{head}</thead>
                        <tbody className="divide-y divide-slate-50">{children}</tbody>
                    </table>
                </div>
            )}
        </div>
        {footer}
    </section>
);

/* Сколько строк таблицы видно за раз.
 *
 * Пять — решение владельца 25.08.2026. Отчёт из девяти таблиц по два десятка
 * строк это лента, по которой листают мышью и в которой блоки ниже не находят
 * вовсе; с пятёркой каждая таблица занимает один взгляд, а глубина уходит в
 * пейджер. Число одно на весь отчёт: таблицы разной высоты рвут ритм страницы
 * сильнее, чем помогает лишняя строка в какой-то одной. */
export const PAGE_SIZE = 5;

/** Таблица со страницами.
 *
 *  Отдельный компонент, а не флаг у Table: страница — СОСТОЯНИЕ, и держать его
 *  надо там же, где режется список. Разложи это по вызывающему коду — и на
 *  экране с девятью таблицами появятся девять почти одинаковых useState.
 *
 *  `rows` — весь список, `renderRow` — как рисовать строку (ключ на ней).
 *  Остальные свойства уходят в Table как есть.
 */
export const PagedTable = ({ rows = [], perPage = PAGE_SIZE, renderRow, ...rest }) => {
    const [page, setPage] = React.useState(1);
    const pageCount = Math.max(1, Math.ceil(rows.length / perPage));
    /* Список стал короче (сменили период) — страница за его концом показала бы
       пустую таблицу с рабочим пейджером. Прижимаем к последней существующей. */
    const safePage = Math.min(page, pageCount);
    React.useEffect(() => { if (page !== safePage) setPage(safePage); }, [page, safePage]);
    /* Сменились сами данные — возвращаемся к первой странице: третья страница
       прошлого месяца к нынешнему отношения не имеет. React гасит повторную
       установку того же значения сам, поэтому лишних перерисовок здесь нет. */
    React.useEffect(() => { setPage(1); }, [rows]);

    const start = (safePage - 1) * perPage;
    const shown = rows.slice(start, start + perPage);
    return (
        <Table
            {...rest}
            count={rows.length}
            footer={rows.length > perPage ? (
                <IosPager
                    page={safePage} pageCount={pageCount} total={rows.length}
                    from={start + 1} to={start + shown.length} onPage={setPage}
                />
            ) : null}
        >
            {shown.map(renderRow)}
        </Table>
    );
};

/** Доля в виде тонкой полосы. Число рядом обязательно: полоса показывает
 *  соотношение, но не величину, и «почти полная» при трёх строках из четырёх
 *  читается как успех.
 *
 *  `caption` — знаменатель словами («из 40»). Нужен там, где его нет в
 *  соседней колонке: доля без знаменателя не говорит, от чего она считается. */
export const Bar = ({ done, total, tone = 'indigo', caption = null }) => {
    const pct = total ? Math.round((100 * done) / total) : 0;
    return (
        <div className="flex items-center justify-end gap-2">
            <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
                <div
                    className={`h-full rounded-full ${
                        tone === 'rose' ? 'bg-rose-400'
                            : tone === 'emerald' ? 'bg-emerald-400' : 'bg-indigo-400'}`}
                    style={{ width: `${pct}%` }}
                />
            </div>
            <span className="tabular-nums text-slate-700">{pct}%</span>
            {caption && (
                <span className="whitespace-nowrap tabular-nums text-slate-500">{caption}</span>
            )}
        </div>
    );
};
