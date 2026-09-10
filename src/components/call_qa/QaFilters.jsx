import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { SlidersHorizontal, X, Search, Loader2 } from 'lucide-react';
import { iosCard, iosGroupLabel, iosInput, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDateRangePicker, { isoDate } from '../ui/DateRangePicker';
import {
    EMPTY_FILTERS, NO_GROUP, countActiveFilters, activeFilterChips, operatorsMatching,
    clampScore,
} from './filters';

/* Панель фильтров раздела «ИИ-оценка»: период, направление, группа, сотрудник,
 * балл ИИ, наличие оценки человека и поиск.
 *
 * Одна панель на все списки раздела — она стоит в CallQaView под вкладками, а
 * не внутри списка: пока фильтры сидели бы в каждом списке отдельно, при
 * переключении вкладки отбор сбрасывался бы, а панель прыгала бы по экрану.
 *
 * Фильтрует СЕРВЕР (см. call_qa.api._list_filters_predicate). Клиентская
 * фильтрация разошлась бы со счётчиком «Показано N из M»: сервер считает total
 * по всей выборке, а не по загруженной странице.
 *
 * Вид собран из тех же примитивов, что «Посылки» и «Чаты»: свёрнутая строка с
 * чипами отобранного, раскрытая карточка iosCard с сеткой полей. Никаких новых
 * цветовых утилит — тёмная тема в проекте собирается генератором по уже
 * встречающимся классам (scripts/build_dark_theme.py), и свежая утилита осталась
 * бы светлой.
 */

/* Вид чипа диапазона — один в один с ios-вариантом CustomSelect, иначе поле
 * периода выбивается по высоте из ряда селекторов. `[&>span]:flex-1` нужен,
 * потому что triggerClassName заменяет класс кнопки целиком. */
const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 '
    + 'text-left text-[12.5px] font-medium text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all hover:bg-slate-50 '
    + 'active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:text-left [&>span]:truncate';

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

const scoreField = 'w-full rounded-xl bg-white px-3 py-2 text-[12.5px] font-medium '
    + 'text-slate-700 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] '
    + 'transition placeholder:font-normal placeholder:text-slate-400 focus:outline-none '
    + 'focus:ring-2 focus:ring-blue-500/60 tabular-nums';

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
    const [optionsBusy, setOptionsBusy] = useState(false);
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
        setOptionsBusy(true);
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
            })
            .finally(() => {
                if (requestId === optionsRequest.current.id) setOptionsBusy(false);
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

    /* Внешний сброс («сбросить всё», крестик на чипе, смена отдела) обязан
       очистить и черновик — иначе поле осталось бы заполненным и через 350 мс
       вернуло бы только что снятый фильтр обратно. */
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
        <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
                <button
                    type="button"
                    onClick={() => setOpen((state) => !state)}
                    aria-expanded={open}
                    className={`inline-flex items-center gap-2 rounded-xl px-3 py-2 text-[13px] font-semibold transition active:scale-[0.98] ${
                        open || activeCount
                            ? 'bg-white text-slate-900 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)]'
                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200/80'}`}
                >
                    <SlidersHorizontal size={14} className="text-slate-400" />
                    Фильтры
                    {activeCount > 0 && (
                        <span className="grid h-[18px] min-w-[18px] place-items-center rounded-full bg-blue-600 px-1 text-[11px] font-semibold tabular-nums text-white">
                            {activeCount}
                        </span>
                    )}
                </button>
                {optionsBusy && options === null && (
                    <span className="inline-flex items-center gap-1.5 text-[12px] text-slate-400">
                        <Loader2 size={12} className="animate-spin" />справочник…
                    </span>
                )}
            </div>

            {/* Что отобрано — видно, не раскрывая панель: иначе снаружи торчит
                только число, и человек не помнит, какие именно фильтры стоят. */}
            {chips.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                    {chips.map((chip) => (
                        <button
                            key={chip.key}
                            type="button"
                            onClick={() => onChange?.(chip.next)}
                            title={`Убрать: ${chip.name} — ${chip.label}`}
                            className="group inline-flex max-w-full items-center gap-1.5 rounded-full bg-white px-2.5 py-1 text-[12.5px] text-slate-700 ring-1 ring-slate-200/80 transition hover:ring-slate-300 active:scale-[0.98]"
                        >
                            <span className="text-slate-400">{chip.name}</span>
                            <span className="truncate font-medium">{chip.label}</span>
                            <X size={12} className="shrink-0 text-slate-400 group-hover:text-slate-600" />
                        </button>
                    ))}
                    <button
                        type="button"
                        onClick={() => onChange?.(EMPTY_FILTERS)}
                        className="px-1.5 text-[12.5px] text-slate-500 underline decoration-slate-300 underline-offset-2 transition hover:text-slate-700"
                    >
                        сбросить всё
                    </button>
                </div>
            )}

            {open && (
                <div className={`${iosCard} p-3.5`}>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        <label className="block space-y-1.5">
                            <span className={iosGroupLabel}>Период</span>
                            <IosDateRangePicker
                                from={value.date_from || ''}
                                to={value.date_to || ''}
                                max={isoDate(new Date())}
                                presets={DATE_PRESETS}
                                triggerClassName={DATE_TRIGGER}
                                onChange={({ from, to }) => set({ date_from: from || '', date_to: to || '' })}
                            />
                        </label>
                        <label className="block space-y-1.5">
                            <span className={iosGroupLabel}>Направление</span>
                            <CustomSelect
                                value={value.direction_id}
                                /* Сотрудник принадлежит направлению и группе:
                                   оставить его выбранным после смены верхнего
                                   уровня — значит показать пустой список при
                                   заполненной панели. */
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
                            <label className="block space-y-1.5">
                                <span className={iosGroupLabel}>Оценка человека</span>
                                <IosSegmented
                                    value={value.reviewed || ''}
                                    options={REVIEWED_OPTIONS}
                                    onChange={(next) => set({ reviewed: next })}
                                    stretch
                                    ariaLabel="Оценка человека"
                                />
                            </label>
                        )}
                        {showScoreFilters && (
                            <label className="block space-y-1.5">
                                <span className={iosGroupLabel}>Балл ИИ</span>
                                <div className="flex items-center gap-2">
                                    <input
                                        /* text + inputMode, а не type="number": числовое поле
                                           рисует стрелки-спиннеры (чужая деталь в ряду
                                           iOS-полей) и меняет значение колесом мыши при
                                           прокрутке страницы. Значение всё равно зажимает
                                           clampScore, а телефон покажет цифровую клавиатуру. */
                                        type="text" inputMode="numeric"
                                        className={scoreField} placeholder="от"
                                        aria-label="Балл ИИ от"
                                        value={draft.score_min}
                                        onChange={(event) => setDraft((state) => ({
                                            ...state, score_min: clampScore(event.target.value) }))}
                                    />
                                    <span className="text-[12.5px] text-slate-400">—</span>
                                    <input
                                        /* text + inputMode, а не type="number": числовое поле
                                           рисует стрелки-спиннеры (чужая деталь в ряду
                                           iOS-полей) и меняет значение колесом мыши при
                                           прокрутке страницы. Значение всё равно зажимает
                                           clampScore, а телефон покажет цифровую клавиатуру. */
                                        type="text" inputMode="numeric"
                                        className={scoreField} placeholder="до"
                                        aria-label="Балл ИИ до"
                                        value={draft.score_max}
                                        onChange={(event) => setDraft((state) => ({
                                            ...state, score_max: clampScore(event.target.value) }))}
                                    />
                                </div>
                            </label>
                        )}
                        <label className="block space-y-1.5 sm:col-span-2">
                            <span className={iosGroupLabel}>Поиск</span>
                            <div className="relative">
                                <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input
                                    type="text"
                                    className={`${iosInput} pl-9 pr-8`}
                                    placeholder="Имя сотрудника или номер"
                                    value={draft.q}
                                    onChange={(event) => setDraft((state) => ({ ...state, q: event.target.value }))}
                                />
                                {draft.q && (
                                    <button
                                        type="button"
                                        onClick={() => setDraft((state) => ({ ...state, q: '' }))}
                                        aria-label="Очистить поиск"
                                        className="absolute right-2.5 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                                    >
                                        <X size={12} />
                                    </button>
                                )}
                            </div>
                        </label>
                    </div>
                </div>
            )}
        </div>
    );
}
