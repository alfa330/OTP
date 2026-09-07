import React, { useEffect, useMemo, useRef } from 'react';
import { RotateCcw, SlidersHorizontal, X } from 'lucide-react';
import { IosSegmented, iosGroupLabel } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import {
    EMPTY_FILTERS, MATCH_OPTIONS, TYPE_OPTIONS, activeCount, filterChips,
    isDefaultFilters, normalizeFilters, toggleValue, withoutChip,
} from './searchFilters';

/* Фильтры поиска: где искать, какой тип документа, чей.
 *
 * Кнопка стоит У ПРАВОГО КРАЯ САМОГО ПОЛЯ поиска, а панель раскрывается ДО
 * запроса. Раньше и кнопка, и панель появлялись только с двух символов — то
 * есть «сначала найди, потом сужай», — и это переворачивало обычный порядок:
 * человек знает, что ищет регламент, ещё до того, как набрал слово, а получал
 * полную выдачу и шёл сужать её вторым заходом. Кнопка внутри поля заодно
 * снимает старое возражение против ранней кнопки на витрине «Все статьи вики»:
 * стоя в поле, она читается как часть поиска, а не как настройки раздела.
 *
 * Кнопку держит РОДИТЕЛЬ (SearchFilterButton ниже), потому что поле поиска —
 * его разметка: у шапки раздела это растущая строка с ⌘K, у витрины — широкая
 * пилюля по центру, и одну кнопку в двух чужих строках компонент бы не собрал.
 *
 * Панель ВСТРОЕНА в выдачу, а не всплывает поповером над ней, и это главное
 * решение здесь. Работа с фильтром всегда итеративная — отметил тип, посмотрел,
 * снял, добавил создателя, — а поповер (как у фильтра офисов, OfficeFilters.jsx)
 * ложится поверх результатов и прячет ровно то, ради чего его крутят. Заодно
 * встроенная панель снимает три ловушки портала разом: щелчок мимо, внешнюю
 * прокрутку и перехват Escape.
 *
 * Единственное, что остаётся в портале, — список создателей внутри CustomSelect.
 * Из-за него проверка isInsideSearchFilters нужна по-прежнему: поиск в шапке
 * гасит выдачу щелчком мимо своего контейнера, а список лежит в body.
 *
 * Кнопка и чипы в клавиатурный список строк выдачи НЕ входят: стрелки и Enter
 * принадлежат статьям (searchRows в WikiSearch.jsx), и попади фильтр в тот же
 * список — Enter «открывал» бы его вместо статьи.
 *
 * Раскрытие панели держит РОДИТЕЛЬ: в выпадашке поиска от него зависит высота
 * списка результатов, и состояние, которое нужно двоим, не может жить у одного.
 */

/* Щелчок пришёлся по фильтрам? Сама панель теперь лежит в общем потоке, но
   список создателей уходит в портал body — то есть ВНЕ контейнера поиска. Без
   этой проверки первый mousedown по имени размонтировал бы выпадашку до
   mouseup: click не случался бы вовсе, и выбор терялся молча. Тот же приём, что
   в OfficeFilters.jsx. */
export const isInsideSearchFilters = (target) => {
    if (!target) return false;
    const panels = Array.from(document.querySelectorAll('[data-wiki-search-filters]'));
    if (panels.some((panel) => panel.contains(target))) return true;
    return Array.from(document.querySelectorAll('[role="listbox"]'))
        .some((list) => list.parentElement?.contains(target));
};

/** Открыт ли слой поверх поиска — для Escape.
 *  Escape обслуживает верхний слой: раскрытый список гасит себя сам, и поиск
 *  при этом обязан остаться. */
export const hasOpenFilterLayer = () => !!document.querySelector('[role="listbox"]');

/* ── Чипы выбранного ────────────────────────────────────────────────────── */

/** Строка выбранных фильтров. Пусто — не рисуем ничего, даже отступа.
 *
 * Чипы обязательны везде, где виден результат: без них «Ничего не найдено» при
 * забытом фильтре превращается в молчаливый отказ — поиск честно ничего не
 * нашёл, а виноватым остаётся поиск.
 */
export function SearchFilterChips({ value, authors = [], onChange, className = '' }) {
    const chips = useMemo(() => filterChips(value, authors), [value, authors]);
    if (!chips.length) return null;
    return (
        <div className={`flex flex-wrap items-center gap-1.5 ${className}`}>
            {chips.map((chip) => (
                <button
                    key={chip.key}
                    type="button"
                    onClick={() => onChange(withoutChip(value, chip))}
                    /* Контрастом, а не индиго: в выпадашке поиска индиго уже
                       занят выделенной строкой выдачи и клавишей Enter, и третий
                       индиговый смысл на одном экране читался бы как один. */
                    className="inline-flex max-w-full items-center gap-1 rounded-full bg-slate-900 py-1 pl-2.5 pr-1.5 text-[11.5px] font-medium text-white transition hover:bg-slate-700"
                    aria-label={`Снять фильтр «${chip.label}»`}
                >
                    <span className="min-w-0 truncate">{chip.label}</span>
                    <X size={11} className="shrink-0 opacity-70" />
                </button>
            ))}
            {/* «Сбросить» — только когда чипов больше одного: при единственном
                его собственный крестик и есть сброс, а второй орган для того же
                действия — шум. */}
            {chips.length > 1 && (
                <button
                    type="button"
                    onClick={() => onChange({ ...EMPTY_FILTERS })}
                    className="rounded-full px-2 py-1 text-[11.5px] font-medium text-slate-400 transition hover:text-slate-600"
                >
                    Сбросить
                </button>
            )}
        </div>
    );
}

/* ── Кнопка в поле поиска ───────────────────────────────────────────────── */

/** Кнопка фильтров — значком, у правого края поля.
 *
 * Без подписи: место в поле считанное (в шапке раздела там же живут крестик
 * «очистить» и подсказка ⌘K), а ползунки читаются как фильтр и без слова.
 * Цифра рядом — сколько условий стоит: свёрнутая панель без неё была бы скрытым
 * состоянием, из-за которого «ничего не найдено» выглядит поломкой поиска.
 */
export function SearchFilterButton({ value, open = false, onOpenChange, className = '' }) {
    const count = activeCount(value);
    return (
        <button
            type="button"
            /* Метка та же, что у панели: щелчок по кнопке не должен считаться
               щелчком мимо поиска — иначе выпадашка гасла бы в тот же миг, в
               который кнопка её открывает. */
            data-wiki-search-filters=""
            aria-label="Фильтры поиска"
            aria-expanded={open}
            title="Фильтры поиска"
            onClick={() => onOpenChange?.(!open)}
            className={`inline-flex h-6 shrink-0 items-center gap-1 rounded-lg px-1.5 text-[11px] font-bold tabular-nums transition active:scale-[0.95] ${
                open || count
                    ? 'bg-slate-900 text-white'
                    : 'text-slate-400 hover:bg-slate-100 hover:text-slate-600'
            } ${className}`}
        >
            <SlidersHorizontal size={13} />
            {count > 0 && count}
        </button>
    );
}

/* ── Панель ─────────────────────────────────────────────────────────────── */

const TypeChip = ({ checked, label, onClick }) => (
    <button
        type="button"
        onClick={onClick}
        aria-pressed={checked}
        className={`rounded-full px-2.5 py-1 text-[12px] font-medium transition active:scale-[0.98] ${
            checked
                ? 'bg-slate-900 text-white'
                : 'bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50'
        }`}
    >
        {label}
    </button>
);

export default function WikiSearchFilters({
    value, onChange, authors = [], authorsLoading = false,
    open = false, onNeedAuthors = null, className = '',
}) {
    const filters = useMemo(() => normalizeFilters(value), [value]);

    /* Создатели грузятся при ПЕРВОМ раскрытии панели, а не при заходе в поиск:
       список стоит обхода периметра, а открывает панель меньшинство. Ref, а не
       состояние: повторный запрос не нужен, а лишний рендер — тем более. */
    const asked = useRef(false);
    useEffect(() => {
        if (!open || asked.current || !onNeedAuthors) return;
        asked.current = true;
        onNeedAuthors();
    }, [open, onNeedAuthors]);

    const patch = (part) => onChange({ ...filters, ...part });

    const authorOptions = useMemo(() => authors.map((author) => ({
        value: String(author.id),
        // Число статей рядом с именем объясняет порядок списка и сразу говорит,
        // есть ли смысл выбирать этого человека.
        label: author.articles ? `${author.name} · ${author.articles}` : author.name,
    })), [authors]);

    const selectedAuthors = useMemo(() => filters.authors.map(String), [filters.authors]);

    /* Ни панели, ни чипов — не рисуем и оболочки. Компонент теперь стоит в
       разметке БЕЗУСЛОВНО (кнопка, которая его открывает, живёт отдельно), и
       пустой блок с рамкой и отступом висел бы над выдачей всё время, ничего
       не сообщая. */
    if (!open && isDefaultFilters(filters)) return null;

    return (
        <div className={className} data-wiki-search-filters="">
            {/* Пока панель раскрыта, чипы — второй экземпляр того же состояния:
                отмеченные типы и выбранный человек видны прямо в панели, а
                «Сбросить» там свой. Показываем их только когда панель свёрнута —
                тогда чипы единственное, что говорит, чем сужена выдача. */}
            {!open && (
                <SearchFilterChips value={filters} authors={authors} onChange={onChange} />
            )}

            {open && (
                <div className="rounded-xl bg-slate-50 p-2.5 ring-1 ring-slate-200/70">
                    <div className={iosGroupLabel}>Где искать</div>
                    <IosSegmented
                        className="mt-1.5"
                        stretch
                        ariaLabel="Где искать"
                        value={filters.match}
                        options={MATCH_OPTIONS}
                        onChange={(match) => patch({ match })}
                    />

                    <div className={`${iosGroupLabel} mt-3`}>Тип документа</div>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {TYPE_OPTIONS.map((type) => (
                            <TypeChip
                                key={type.value}
                                label={type.label}
                                checked={filters.types.includes(type.value)}
                                onClick={() => patch({ types: toggleValue(filters.types, type.value) })}
                            />
                        ))}
                    </div>

                    {/* «Создатель», а не «Автор»: в «Аналитике» авторами названы
                        те, кто ПРАВИЛ статьи, и это сознательно другая величина
                        (wiki/analytics.py). Одно слово на две разные цифры в
                        одном разделе читалось бы как ошибка в одной из них. */}
                    <div className={`${iosGroupLabel} mt-3`}>Создатель</div>
                    {authorsLoading ? (
                        <div className="mt-1.5 rounded-xl bg-white px-3 py-2 text-[12.5px] text-slate-400 ring-1 ring-slate-200/70">
                            Загружаем…
                        </div>
                    ) : authorOptions.length ? (
                        <>
                            <CustomSelect
                                variant="ios"
                                className="mt-1.5"
                                multiple
                                value={selectedAuthors}
                                onChange={(next) => patch({ authors: next.map(Number) })}
                                options={authorOptions}
                                searchable
                                searchPlaceholder="Имя…"
                                placeholder="Любой"
                                ariaLabel="Создатель статьи"
                                /* Своя подпись вместо «Выбрано: 1»: пока выбран
                                   один человек, его имя на кнопке и есть ответ
                                   на вопрос «чьи статьи я сейчас вижу». */
                                renderValue={(picked) => (picked.length === 1
                                    ? (authors.find((a) => String(a.id) === picked[0])?.name
                                       || 'Выбран 1')
                                    : `Выбрано: ${picked.length}`)}
                            />
                            {/* Оговорка не косметическая: перенос из старой вики
                                записывает создателем того, кто переносил, и на
                                нём висит почти вся база. Без этой строки человек
                                выберет его, получит почти всю вику и решит, что
                                фильтр сломан. */}
                            <p className="mt-1.5 px-0.5 text-[11px] leading-snug text-slate-400">
                                У статей, перенесённых из старой вики, создатель —
                                тот, кто их перенёс.
                            </p>
                        </>
                    ) : (
                        <div className="mt-1.5 rounded-xl bg-white px-3 py-2 text-[12.5px] text-slate-400 ring-1 ring-slate-200/70">
                            Пока некого выбрать
                        </div>
                    )}

                    {!isDefaultFilters(filters) && (
                        <button
                            type="button"
                            onClick={() => onChange({ ...EMPTY_FILTERS })}
                            className="mt-2.5 flex w-full items-center justify-center gap-1.5 rounded-xl bg-white py-2 text-[12.5px] font-semibold text-slate-600 ring-1 ring-slate-200 transition hover:bg-slate-100 active:scale-[0.98]"
                        >
                            <RotateCcw size={13} /> Сбросить фильтры
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}
