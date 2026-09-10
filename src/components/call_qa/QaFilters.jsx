import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { SlidersHorizontal, X, Search } from 'lucide-react';
import { iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDateRangePicker, { isoDate } from '../ui/DateRangePicker';
import {
    EMPTY_FILTERS, NO_GROUP, countActiveFilters, activeFilterChips, operatorsMatching,
    clampScore,
} from './filters';

/* Панель фильтров раздела «ИИ-оценка»: период, направление, группа, сотрудник,
 * балл ИИ, наличие оценки человека и поиск.
 *
 * Форма повторяет «Касания» (src/components/cdr/TouchesView.jsx:356) — это канон
 * фильтров в проекте, и он же стоит в «Чатах Wazzup» и в базе разборов соседней
 * вкладки:
 *
 *   1) ВИДИМАЯ ПОЛОСА — период и поиск. С них начинается работа с любым списком,
 *      и прятать их за кнопкой значит требовать лишний клик на каждый заход.
 *   2) КНОПКА «Фильтры · N» — за ней остальное, что нужно реже. Синяя, когда
 *      что-то отобрано, серая — когда нет.
 *   3) КАРТОЧКА-СЕТКА под полосой: подписи заглавными (iosGroupLabel) уместны
 *      именно здесь — это второй уровень, где без подписи не догадаться, что
 *      значит «Все группы».
 *   4) ЧИПЫ отобранного — сразу под полосой, ДО панели: так они не прыгают вниз
 *      при её раскрытии и видны, даже когда панель свёрнута (порядок «Посылок»,
 *      ParcelsView.jsx:696).
 *
 * Первая версия была карточкой-анкетой на четыре колонки с заглавными подписями
 * над КАЖДЫМ полем, включая период и поиск. Такой формы в проекте нет нигде: она
 * читается как форма ввода данных, а не как отбор.
 *
 * Фильтрует СЕРВЕР (см. call_qa.api._list_filters_predicate). Клиентская
 * фильтрация разошлась бы со счётчиком «Показано N из M»: сервер считает total
 * по всей выборке, а не по загруженной странице.
 */

const shiftDays = (days) => {
    const value = new Date();
    value.setDate(value.getDate() - days);
    return isoDate(value);
};

/* Три пресета, как в «Посылках» и «Чатах». Четвёртым просился «Весь период», но
   он не влезает в ряд и переносится на две строки, ломая ровную полосу кнопок, —
   а снимается период тем же крестиком на чипе, что и любой другой фильтр. */
const DATE_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: isoDate(new Date()), to: isoDate(new Date()) }) },
    { label: 'Неделя', range: () => ({ from: shiftDays(6), to: isoDate(new Date()) }) },
    { label: 'Месяц', range: () => ({ from: shiftDays(29), to: isoDate(new Date()) }) },
];

const REVIEWED_OPTIONS = [
    { value: '', label: 'Все' },
    { value: 'no', label: 'Только ИИ' },
    { value: 'yes', label: 'С оценкой' },
];

export default function QaFilters(props) {
    const {
        filters, onChange, apiBaseUrl, withAccessTokenHeader, department, subject,
        // Балл ИИ и «есть ли оценка человека» показываем только там, где оценка
        // уже существует. В очереди ревью каждая карточка по определению не
        // проверена человеком, и фильтр «с оценкой» вернул бы там пусто всегда.
        showScoreFilters = true,
        showReviewedFilter = true,
    } = props;

    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [open, setOpen] = useState(false);
    const [options, setOptions] = useState(null);   // null = ещё не загружены
    /* Поля, которые НАБИРАЮТ, а не выбирают: поиск и границы балла. Запрос на
       каждое нажатие — это и лишняя нагрузка, и мигающий список: «90» уходило
       двумя запросами, а «100» ушло бы тремя, причём промежуточный «10» дал бы
       на секунду совсем другую выборку. Держим их в черновике и переносим в
       фильтры с задержкой; в зависимостях эффекта — ПРИМИТИВ, а не колбэк:
       нестабильная функция в deps сбрасывала бы таймер на каждом рендере, и
       задержка не срабатывала бы никогда. */
    const [draft, setDraft] = useState({
        q: filters?.q || '', score_min: filters?.score_min || '', score_max: filters?.score_max || '',
    });
    const draftSignature = JSON.stringify([draft.q, draft.score_min, draft.score_max]);
    const optionsRequest = useRef({ id: 0, controller: null });

    /* Сброс отбора при смене отдела делает НЕ панель, а владелец состояния
       (CallQaView, changeDepartment). Пока это жило здесь, эффект срабатывал и
       на монтировании: панель показывается только на трёх вкладках, и уход на
       «Обзор» с возвратом обратно стирал выставленные фильтры. */
    useEffect(() => {
        if (!apiBaseUrl || !department) return undefined;
        // Справочник принадлежит отделу и вкладке: пока летит новый запрос,
        // прежние фамилии — это фамилии чужого отдела.
        setOptions(null);
        optionsRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = optionsRequest.current.id + 1;
        optionsRequest.current = { id: requestId, controller };
        axios.get(`${apiBaseUrl}/api/ai-qa/filter-options`, {
            params: { department, ...(subject ? { subject } : {}) },
            headers: headers(), signal: controller.signal,
        })
            .then((r) => {
                if (requestId !== optionsRequest.current.id) return;
                setOptions({ directions: r.data?.directions || [],
                             groups: r.data?.groups || [],
                             operators: r.data?.operators || [] });
            })
            .catch((error) => {
                if (axios.isCancel(error) || requestId !== optionsRequest.current.id) return;
                // Справочник не пришёл — панель остаётся с периодом, поиском и
                // баллом: они работают без него. Пустые селекторы честнее
                // отказа во всей панели.
                setOptions({ directions: [], groups: [], operators: [] });
            });
        return () => controller.abort();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, department, subject]);

    useEffect(() => () => optionsRequest.current.controller?.abort(), []);

    // Набираемые поля живут своей жизнью; в фильтры значения уезжают с задержкой.
    useEffect(() => {
        const current = filters || EMPTY_FILTERS;
        const next = { q: draft.q.trim(), score_min: draft.score_min, score_max: draft.score_max };
        if (next.q === (current.q || '') && next.score_min === (current.score_min || '')
            && next.score_max === (current.score_max || '')) return undefined;
        const timer = window.setTimeout(() => onChange?.({ ...current, ...next }), 350);
        return () => window.clearTimeout(timer);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [draftSignature]);

    /* Внешний сброс («сбросить», крестик на чипе, смена отдела) обязан очистить
       и черновик — иначе поле осталось бы заполненным и через 350 мс вернуло бы
       только что снятый фильтр обратно. */
    const externalSignature = `${filters?.q || ''} ${filters?.score_min || ''} ${filters?.score_max || ''}`;
    useEffect(() => {
        setDraft((state) => {
            const next = { q: filters?.q || '', score_min: filters?.score_min || '',
                           score_max: filters?.score_max || '' };
            const changed = ['q', 'score_min', 'score_max']
                .some((key) => String(state[key]).trim() !== String(next[key]));
            return changed ? next : state;
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [externalSignature]);

    const value = filters || EMPTY_FILTERS;
    const directions = options?.directions || [];
    const groups = options?.groups || [];
    const operators = options?.operators || [];
    const activeCount = countActiveFilters(value);
    const chips = activeFilterChips(value, { directions, groups, operators });
    const set = (fields) => onChange?.({ ...value, ...fields });

    /* Сотрудники сужаются уже выбранными направлением и группой: список из
       полусотни фамилий, половина которых не относится к отобранному, — это не
       фильтр, а поиск иголки. Рядом с именем стоит число его оценок: по нему
       видно, кого вообще есть смысл выбирать. */
    const operatorOptions = [
        { value: null, label: 'Все сотрудники' },
        ...operatorsMatching(operators, value).map((person) => ({
            value: person.id,
            label: `${person.name}${person.evaluations ? ` · ${person.evaluations}` : ''}`
                + (person.fired ? ' · уволен' : ''),
        })),
    ];

    const groupOptions = [
        { value: null, label: 'Все группы' },
        ...groups.map((group) => ({ value: group.id, label: `${group.name} · ${group.operators}` })),
        // Корзина внизу и подписана словами: у части звонков из АТС учётной
        // записи нет вовсе, и без неё сумма по группам не сходилась бы с общим
        // числом оценок, а строки молча исчезали бы из любого разреза.
        { value: NO_GROUP, label: 'Без группы' },
    ];

    const directionOptions = [
        { value: null, label: 'Все направления' },
        ...directions.map((item) => ({ value: item.id, label: item.name })),
    ];

    return (
        <div>
            {/* Полоса, с которой начинают: период и поиск. Остальное — за кнопкой. */}
            <section className="flex flex-col gap-2.5 sm:flex-row sm:items-center">
                <IosDateRangePicker
                    from={value.date_from || ''}
                    to={value.date_to || ''}
                    max={isoDate(new Date())}
                    presets={DATE_PRESETS}
                    onChange={({ from, to }) => set({ date_from: from || '', date_to: to || '' })}
                />
                <div className="relative sm:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="search"
                        className={`${iosInput} pl-9`}
                        value={draft.q}
                        onChange={(event) => setDraft((state) => ({ ...state, q: event.target.value }))}
                        placeholder="Сотрудник или номер — хватит фамилии"
                    />
                </div>
                <button
                    type="button"
                    onClick={() => setOpen((state) => !state)}
                    aria-expanded={open}
                    className={`${activeCount ? iosBtnPrimary : iosBtnSecondary} shrink-0`}
                >
                    <SlidersHorizontal size={14} />
                    Фильтры{activeCount ? ` · ${activeCount}` : ''}
                </button>
            </section>

            {/* Чипы СРАЗУ под полосой, до панели: так они не прыгают вниз при её
                раскрытии и видны, даже когда панель свёрнута — иначе снаружи
                торчит только число, и человек не помнит, что именно отобрано. */}
            {chips.length > 0 && (
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                    {chips.map((chip) => (
                        <button
                            key={chip.key}
                            type="button"
                            onClick={() => onChange?.(chip.next)}
                            title={`Убрать: ${chip.name || chip.label}`}
                            className="inline-flex max-w-full items-center gap-1 rounded-full bg-white px-2.5 py-1 text-[12.5px] text-slate-700 ring-1 ring-slate-200/80 transition hover:bg-slate-50"
                        >
                            {/* Имени может не быть: у групп с названием «Группа
                                Тестбаевой» оно дублировало бы значение (см.
                                chipName в filters.js), и тогда чип — одно значение. */}
                            {chip.name && <span className="text-slate-400">{chip.name}</span>}
                            <span className="truncate font-medium">{chip.label}</span>
                            <X size={12} className="shrink-0 text-slate-400" />
                        </button>
                    ))}
                    <button
                        type="button"
                        onClick={() => onChange?.(EMPTY_FILTERS)}
                        className="px-1.5 text-[12.5px] font-medium text-slate-500 hover:text-slate-800"
                    >
                        сбросить
                    </button>
                </div>
            )}
            {/* Три колонки, а не четыре: фильтров пять, и в ряду по четыре
                «Балл ИИ» повисал один во втором ряду, а панель выглядела
                оборванной. По три ряды складываются осмысленно — «кого смотрим»
                и «какие оценки», — а на вкладке «Очередь ревью», где последних
                двух нет, выходит ровно один полный ряд. */}
            {open && (
                <section className={`${iosCard} mt-3 grid gap-3 p-3.5 sm:grid-cols-2 lg:grid-cols-3`}>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Направление</span>
                        <CustomSelect
                            value={value.direction_id}
                            /* Сотрудник принадлежит направлению и группе: оставить
                               его выбранным после смены верхнего уровня — значит
                               показать пустой список при заполненной панели. */
                            onChange={(next) => set({ direction_id: next || null, operator_id: null })}
                            options={directionOptions}
                            placeholder="Все направления"
                            variant="ios"
                            ariaLabel="Направление"
                        />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Группа</span>
                        <CustomSelect
                            value={value.group_id}
                            onChange={(next) => set({ group_id: next || null, operator_id: null })}
                            options={groupOptions}
                            placeholder="Все группы"
                            variant="ios"
                            searchable
                            ariaLabel="Группа"
                        />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Сотрудник</span>
                        <CustomSelect
                            value={value.operator_id}
                            onChange={(next) => set({ operator_id: next || null })}
                            options={operatorOptions}
                            placeholder="Все сотрудники"
                            variant="ios"
                            searchable
                            ariaLabel="Сотрудник"
                        />
                    </label>
                    {showReviewedFilter && (
                        /* НЕ <label>: IosSegmented — это tablist из кнопок, а
                           <label> без htmlFor делает своим управляемым элементом
                           первую кнопку внутри. Клик по подписи «Оценка человека»
                           тогда молча выбирал «Все», то есть снимал фильтр.
                           У соседей обёртка <label> уместна: там первый потомок —
                           триггер CustomSelect, и клик по подписи просто
                           раскрывает список. */
                        <div className="block space-y-1.5">
                            <span className={iosGroupLabel}>Оценка человека</span>
                            <IosSegmented
                                value={value.reviewed || ''}
                                options={REVIEWED_OPTIONS}
                                onChange={(next) => set({ reviewed: next })}
                                stretch
                                ariaLabel="Оценка человека"
                            />
                        </div>
                    )}
                    {showScoreFilters && (
                        <div className="block space-y-1.5">
                            <span className={iosGroupLabel}>Балл ИИ</span>
                            {/* ОДИН орган, а не два поля рядом: балл и считается
                                одним фильтром, и снимается одним чипом («70—100»).
                                Плитка в габаритах iosInput, поля внутри прозрачные,
                                кольцо фокуса — на всей плитке (тот же приём, что у
                                поиска вики и телефона в справочнике парков).
                                Плейсхолдеры «0» и «100» — это действующие границы:
                                пустой фильтр так и говорит, что берёт весь диапазон,
                                а «от»/«до» были служебной подписью ни о чём. */}
                            <div className="flex items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70">
                                {/* text + inputMode, а не type="number": числовое
                                    поле рисует стрелки-спиннеры (чужая деталь в ряду
                                    iOS-полей) и меняет значение колесом мыши при
                                    прокрутке страницы. Значение зажимает clampScore,
                                    а телефон покажет цифровую клавиатуру. */}
                                <input
                                    type="text" inputMode="numeric" maxLength={3}
                                    className="min-w-0 flex-1 border-0 bg-transparent p-0 text-center text-[14px] tabular-nums text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-0"
                                    placeholder="0" aria-label="Балл ИИ от"
                                    value={draft.score_min}
                                    onChange={(event) => setDraft((state) => ({
                                        ...state, score_min: clampScore(event.target.value) }))}
                                />
                                <span className="shrink-0 text-[14px] text-slate-400">—</span>
                                <input
                                    type="text" inputMode="numeric" maxLength={3}
                                    className="min-w-0 flex-1 border-0 bg-transparent p-0 text-center text-[14px] tabular-nums text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-0"
                                    placeholder="100" aria-label="Балл ИИ до"
                                    value={draft.score_max}
                                    onChange={(event) => setDraft((state) => ({
                                        ...state, score_max: clampScore(event.target.value) }))}
                                />
                            </div>
                        </div>
                    )}
                </section>
            )}

        </div>
    );
}
