import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    Bold, Italic, Link2, List, Loader2, Send,
} from 'lucide-react';

import CustomSelect from '../ui/CustomSelect';
import {
    IosSection, IosToggle, iosBtnGhost, iosBtnPrimary, iosCard, iosInput,
} from '../ui/ios';
import MailingPreview from './MailingPreview';
import {
    applyFormatting, composeBilingual, formatCount, plural, splitBilingual,
} from './mailingText';

/* Переключение «Два языка» не имеет права терять набранное.
 *
 * Раньше включение безусловно писало `{message_kk: '', message_ru: message}`, и
 * путь «набрал оба языка → выключил → включил» стирал казахскую часть: при
 * выключении части склеивались в один текст, а при включении весь этот текст
 * целиком уезжал в русское поле. Теперь включение сначала пробует разобрать
 * склейку обратно по разделителю и лишь потом кладёт всё в русское поле. */
const toBilingual = (draft) => {
    if (draft.message_kk || draft.message_ru) {
        return { bilingual: true, message: '' };
    }
    const parts = splitBilingual(draft.message || '');
    return {
        bilingual: true,
        message: '',
        message_kk: parts.bilingual ? parts.kk : '',
        message_ru: parts.bilingual ? parts.ru : (draft.message || ''),
    };
};

/* Выключение склеивает части в один текст, но НЕ стирает сами части: иначе
   обратное включение опять пришлось бы разбирать по разделителю, а человек мог
   успеть поправить склейку руками. */
const toSingle = (draft) => ({
    bilingual: false,
    message: composeBilingual(draft.message_kk, draft.message_ru),
});

/*
 * Экран составления рассылки.
 *
 * Раскладка: слева форма, справа липкая колонка с предпросмотром и охватом.
 * Обязательного на виду ровно три вещи — заголовок, текст и диспетчерские;
 * отбор получателей необязателен и убран за чипы «+ Сегмент», «+ Группа»,
 * «+ Фильтры», как в форме задачи и в карточке посылки. Выложить восемь
 * выпадающих списков сразу означало бы превратить экран в анкету, хотя чаще
 * всего рассылка уходит вообще без отбора.
 *
 * Число получателей пересчитывает сервер по тем же правилам, по которым потом
 * отправит: считать охват на клиенте нечем, а показать «примерно» здесь нельзя —
 * именно это число стоит в кнопке отправки.
 */

/* Кнопка панели форматирования. onMouseDown с preventDefault обязателен: без
   него нажатие снимает выделение в поле, и обернуть выделенный кусок нечем. */
const ToolButton = ({ title, onClick, children }) => (
    <button
        type="button"
        title={title}
        aria-label={title}
        onMouseDown={(event) => event.preventDefault()}
        onClick={onClick}
        className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100 active:scale-95"
    >
        {children}
    </button>
);

const Toolbar = ({ onFormat }) => (
    <div className="flex items-center gap-0.5">
        <ToolButton title="Жирный" onClick={() => onFormat('bold')}><Bold size={15} /></ToolButton>
        <ToolButton title="Курсив" onClick={() => onFormat('italic')}><Italic size={15} /></ToolButton>
        <ToolButton title="Ссылка" onClick={() => onFormat('link')}><Link2 size={15} /></ToolButton>
        <ToolButton title="Список" onClick={() => onFormat('list')}><List size={15} /></ToolButton>
    </div>
);

/* Поле текста с панелью и счётчиком. Выделение после вставки разметки ставится
   здесь, а не в чистой функции: ссылка на textarea есть только у компонента. */
let messageFieldSeq = 0;

const MessageField = ({ label, value, onChange, hint }) => {
    const ref = useRef(null);
    // Свой id, чтобы подпись открывала поле по клику: у двуязычного составителя
    // полей два, и один общий id связал бы обе подписи с первым.
    const idRef = useRef(null);
    if (idRef.current === null) { messageFieldSeq += 1; idRef.current = `dm-msg-${messageFieldSeq}`; }
    const format = useCallback((kind) => {
        const node = ref.current;
        if (!node) return;
        const next = applyFormatting(value, node.selectionStart, node.selectionEnd, kind);
        onChange(next.value);
        requestAnimationFrame(() => {
            node.focus();
            node.setSelectionRange(next.selectionStart, next.selectionEnd);
        });
    }, [value, onChange]);

    return (
        <div>
            <div className="mb-1 flex items-center justify-between gap-2">
                <label className="text-[12.5px] font-medium text-slate-600" htmlFor={idRef.current}>
                    {label}
                </label>
                <Toolbar onFormat={format} />
            </div>
            <textarea
                id={idRef.current}
                ref={ref}
                rows={7}
                value={value}
                onChange={(event) => onChange(event.target.value)}
                placeholder="Введите текст рассылки"
                className={`${iosInput} resize-none bg-white leading-relaxed ring-1 ring-slate-200/70`}
            />
            {hint && <div className="mt-1 text-[11px] text-slate-500">{hint}</div>}
        </div>
    );
};

/* Ряд чекбоксов-таблеток. Для подсегментов и коротких наборов он честнее
   выпадающего списка: вариантов три-четыре, и прятать их под кнопку значит
   заставлять открывать её всегда. */
const PillChecks = ({ options, value = [], onChange }) => (
    <div className="flex flex-wrap gap-1.5">
        {options.map((option) => {
            const active = value.includes(option.id);
            return (
                <button
                    key={option.id}
                    type="button"
                    onClick={() => onChange(active
                        ? value.filter((item) => item !== option.id)
                        : [...value, option.id])}
                    className={`rounded-full px-3 py-1.5 text-[12.5px] font-medium transition active:scale-[0.98] ${
                        active
                            ? 'bg-blue-600 text-white shadow-sm'
                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                >
                    {option.name}
                </button>
            );
        })}
    </div>
);

const MultiFilter = ({ label, options, value = [], onChange, placeholder }) => {
    /* Пустой справочник прячет контрол — но ТОЛЬКО если в нём ничего не выбрано.
       Иначе выходило так: справочники не догрузились, поле исчезло с экрана, а
       значение осталось в фильтре и продолжало уходить на сервер. Человек видит
       охват меньше ожидаемого и не понимает почему. */
    if ((!options || options.length === 0) && !value.length) return null;
    const known = options || [];
    const orphans = value.filter((id) => !known.some((item) => item.id === id));
    return (
        <div>
            <div className="mb-1 text-[12.5px] font-medium text-slate-600">{label}</div>
            <CustomSelect
                multiple
                variant="ios"
                searchable={options.length > 8}
                ariaLabel={label}
                placeholder={placeholder || 'Не важно'}
                value={value}
                onChange={onChange}
                options={[
                    ...known.map((item) => ({ value: item.id, label: item.name })),
                    // Выбранное, чего нет в справочнике, оставляем в списке —
                    // иначе снять его было бы нечем.
                    ...orphans.map((id) => ({ value: id, label: id })),
                ]}
                renderValue={(vals) => (vals.length === 1
                    ? (known.find((item) => item.id === vals[0]) || {}).name || vals[0]
                    : `Выбрано: ${vals.length}`)}
            />
            {orphans.length > 0 && (
                <div className="mt-1 text-[11px] text-amber-700">
                    Справочник диспетчерской не загрузился — значения показаны кодами.
                </div>
            )}
        </div>
    );
};

export default function MailingComposer({
    parks, limits, refs, refsLoading, filters, onFilters, draft, onDraft,
    counting, count, countError, onSend, sending, onOpenTemplates, onSaveTemplate,
}) {
    const [openSections, setOpenSections] = useState(() => new Set());

    /* Чипы раскрываются под уже заполненный отбор — как секции в форме задачи.
       Эффектом, а не начальным состоянием: «Повторить» и «Взять шаблон»
       выставляют фильтры уже после того, как форма смонтирована, и без этого
       человек видел бы свёрнутые чипы со счётчиками и не понимал, откуда
       взялись цифры в охвате.
       Раскрываем только те чипы, у которых есть значение, и никогда не
       закрываем сами: закрытие чипа стирает фильтр, и делать это без человека
       нельзя. */
    useEffect(() => {
        const filled = [];
        if (filters.segment) filled.push('segment');
        if (filters.group) filled.push('group');
        if (['city_ids', 'profession_ids', 'contractor_statuses', 'car_categories', 'car_amenities']
            .some((key) => (filters[key] || []).length)) filled.push('filters');
        if (!filled.length) return;
        setOpenSections((prev) => {
            if (filled.every((id) => prev.has(id))) return prev;
            const next = new Set(prev);
            filled.forEach((id) => next.add(id));
            return next;
        });
    }, [filters]);

    const selectedParks = draft.park_ids || [];
    const bilingual = Boolean(draft.bilingual);
    const message = bilingual
        ? composeBilingual(draft.message_kk, draft.message_ru)
        : String(draft.message || '');

    const maxTitle = limits?.max_title || 120;
    const maxMessage = limits?.max_message || 1500;

    const titleLeft = maxTitle - String(draft.title || '').length;
    const messageLength = message.length;

    const patch = useCallback((next) => onDraft({ ...draft, ...next }), [draft, onDraft]);
    const patchFilters = useCallback((next) => onFilters({ ...filters, ...next }), [filters, onFilters]);

    /* Ограничение длины — обрезкой в обработчике, а не только maxLength на
       элементе: заголовок приходит ещё и из шаблона, и оттуда он может быть
       длиннее, чем разрешает кабинет. */
    const setTitle = (value) => patch({ title: value.slice(0, maxTitle) });

    /* Правку фильтров делаем ДО setState, а не внутри функции-апдейтера: в
       строгом режиме React вызывает апдейтер дважды, и побочное действие внутри
       него отработало бы два раза. Чип и так знает, открыт он или нет. */
    const toggleSection = (id) => {
        const open = openSections.has(id);
        if (open) {
            // Закрытый чип не должен оставлять фильтр включённым втихую.
            if (id === 'segment') patchFilters({ segment: undefined, subsegments: [], city_ids: [] });
            if (id === 'group') patchFilters({ group: undefined });
            if (id === 'filters') {
                patchFilters({
                    profession_ids: [], contractor_statuses: [],
                    car_categories: [], car_amenities: [], city_ids: [],
                });
            }
        }
        setOpenSections((prev) => {
            const next = new Set(prev);
            if (open) next.delete(id); else next.add(id);
            return next;
        });
    };

    const segments = refs?.segments || [];
    const currentSegment = segments.find((item) => item.id === filters.segment);
    const cityAllowed = ['active', 'churn'].includes(filters.segment || '');

    const groupCategories = useMemo(() => {
        const seen = new Map();
        (refs?.groups || []).forEach((group) => {
            if (!seen.has(group.category)) seen.set(group.category, group.category_label);
        });
        return Array.from(seen, ([value, label]) => ({ value, label }));
    }, [refs]);
    const [groupCategory, setGroupCategory] = useState('');
    /* Категорию группы восстанавливаем, когда доехали справочники.
       Начальное состояние её знать не может: «Повторить» из журнала кладёт в
       фильтр только машинный ключ группы, а к какой категории он относится,
       известно лишь из справочника — а тот грузится после выбора диспетчерских.
       Без этого повтор рассылки открывал форму с пустой «Категорией» и
       заблокированным «Значением», хотя фильтр был выставлен. */
    useEffect(() => {
        if (!filters.group) { setGroupCategory(''); return; }
        const found = (refs?.groups || []).find((item) => item.key === filters.group);
        if (found) setGroupCategory(found.category);
    }, [filters.group, refs]);
    const groupOptions = (refs?.groups || []).filter((item) => item.category === groupCategory);

    /* Города зависят от сегмента: кабинет отдаёт свой список на каждый. Поэтому
       чистим выбор при ЛЮБОЙ смене сегмента, а не только при уходе на сегмент
       без городов. Иначе «Активные + Алматы» → «Отток» оставляло город
       включённым, а из списка он исчезал — фильтр работал невидимо, и охват
       сходился с ожиданием только случайно. */
    const segmentRef = useRef(filters.segment);
    useEffect(() => {
        if (segmentRef.current === filters.segment) return;
        segmentRef.current = filters.segment;
        if ((filters.city_ids || []).length) patchFilters({ city_ids: [] });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [filters.segment]);

    const summaries = {
        segment: filters.segment
            ? `${(currentSegment && currentSegment.name) || filters.segment}${
                (filters.subsegments || []).length ? ` · ${filters.subsegments.length}` : ''}`
            : '',
        group: filters.group
            ? ((refs?.groups || []).find((item) => item.key === filters.group) || {}).label || filters.group
            : '',
        filters: (() => {
            const total = ['city_ids', 'profession_ids', 'contractor_statuses', 'car_categories', 'car_amenities']
                .filter((key) => (filters[key] || []).length).length;
            return total ? `${total}` : '';
        })(),
    };

    const CHIPS = [
        { id: 'segment', label: 'Сегмент' },
        { id: 'group', label: 'Группа' },
        { id: 'filters', label: 'Фильтры' },
    ];

    /* Кнопка гаснет и когда охват ещё не посчитан. Без этого она обещала «(0)»
       или число от ПРЕЖНЕГО набора фильтров, а рассылка уходила по нынешнему —
       то есть подпись на кнопке не отвечала за то, что произойдёт по нажатию.
       Длина заголовка гасит наравне с длиной текста: кабинет откажет по любой. */
    const ready = Boolean(
        String(draft.title || '').trim()
        && message.trim()
        && selectedParks.length
        && messageLength <= maxMessage
        && titleLeft >= 0
        && !counting
        && !countError
        && Number.isFinite(count?.total)
        && count.total > 0
        && !sending,
    );

    const parkOptions = parks.map((park) => ({
        value: park.id,
        label: park.city ? `${park.name} · ${park.city}` : park.name,
    }));

    return (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
            <div className="min-w-0 space-y-4">
                <IosSection
                    title="Сообщение"
                    right={(
                        <div className="flex items-center gap-2">
                            <span className="text-[11px] text-slate-500">Два языка</span>
                            <IosToggle
                                checked={bilingual}
                                onChange={(next) => patch(next ? toBilingual(draft) : toSingle(draft))}
                            />
                        </div>
                    )}
                >
                    <div>
                        <div className="mb-1 flex items-center justify-between gap-2">
                            <label className="text-[12.5px] font-medium text-slate-600" htmlFor="dm-title">
                                Заголовок
                            </label>
                            <span className={`text-[11.5px] tabular-nums ${titleLeft < 0 ? 'text-rose-600' : 'text-slate-400'}`}>
                                {String(draft.title || '').length}/{maxTitle}
                            </span>
                        </div>
                        <input
                            id="dm-title"
                            value={draft.title || ''}
                            onChange={(event) => setTitle(event.target.value)}
                            placeholder="Введите заголовок"
                            className={`${iosInput} bg-white ring-1 ring-slate-200/70`}
                        />
                    </div>

                    {bilingual ? (
                        <>
                            <MessageField
                                label="Қазақша"
                                value={draft.message_kk || ''}
                                onChange={(value) => patch({ message_kk: value })}
                            />
                            <MessageField
                                label="Русский"
                                value={draft.message_ru || ''}
                                onChange={(value) => patch({ message_ru: value })}
                                hint="Части уйдут одним сообщением, между ними встанет разделительная черта."
                            />
                        </>
                    ) : (
                        <MessageField
                            label="Текст рассылки"
                            value={draft.message || ''}
                            onChange={(value) => patch({ message: value })}
                        />
                    )}

                    <div className="flex items-center justify-between gap-2 pt-0.5">
                        <span className="text-[11px] text-slate-500">
                            **жирный** · _курсив_ · [подпись](ссылка) · • список
                        </span>
                        <span className={`text-[11.5px] tabular-nums ${messageLength > maxMessage ? 'text-rose-600' : 'text-slate-400'}`}>
                            {formatCount(messageLength)}/{formatCount(maxMessage)}
                        </span>
                    </div>
                </IosSection>

                <IosSection
                    title="Диспетчерские"
                    hint={selectedParks.length > 1
                        ? 'Рассылка уйдёт водителям всех выбранных диспетчерских, охват считается суммарно.'
                        : null}
                >
                    <CustomSelect
                        multiple
                        variant="ios"
                        ariaLabel="Диспетчерские"
                        placeholder="Выберите диспетчерские"
                        value={selectedParks}
                        onChange={(value) => patch({ park_ids: value })}
                        options={parkOptions}
                        renderValue={(vals) => (vals.length === 1
                            ? (parkOptions.find((item) => item.value === vals[0]) || {}).label
                            : `${vals.length} ${plural(vals.length, 'диспетчерская', 'диспетчерские', 'диспетчерских')}`)}
                    />
                    {parks.length === 0 && (
                        <div className="text-[12.5px] text-slate-500">
                            Ни в одной диспетчерской аккаунта рассылка не разрешена. Проверьте связь с кабинетом внизу страницы.
                        </div>
                    )}
                </IosSection>

                <IosSection title="Кому отправить">
                    <div className="flex flex-wrap gap-1.5">
                        {CHIPS.map((chip) => {
                            const active = openSections.has(chip.id);
                            const summary = summaries[chip.id];
                            return (
                                <button
                                    key={chip.id}
                                    type="button"
                                    aria-expanded={active}
                                    onClick={() => toggleSection(chip.id)}
                                    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium transition active:scale-[0.98] ${
                                        active ? 'bg-slate-200/80 text-slate-700' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                    }`}
                                >
                                    <span className="text-slate-400">{active ? '×' : '+'}</span>
                                    {chip.label}
                                    {summary && (
                                        <span className="border-l border-slate-300 pl-1.5 text-slate-500 tabular-nums">
                                            {summary}
                                        </span>
                                    )}
                                </button>
                            );
                        })}
                    </div>

                    {refsLoading && (
                        <div className="flex items-center gap-2 text-[12.5px] text-slate-500">
                            <Loader2 size={14} className="animate-spin" /> Загружаем справочники диспетчерских…
                        </div>
                    )}

                    {openSections.has('segment') && (
                        <div className="space-y-2.5 rounded-xl bg-slate-50 p-3">
                            <div className="flex flex-wrap gap-1.5">
                                {[{ id: '', name: 'Все' }, ...segments].map((option) => {
                                    const active = (filters.segment || '') === option.id;
                                    return (
                                        <button
                                            key={option.id || 'all'}
                                            type="button"
                                            onClick={() => patchFilters({
                                                segment: option.id || undefined,
                                                subsegments: [],
                                            })}
                                            className={`rounded-full px-3 py-1.5 text-[12.5px] font-medium transition active:scale-[0.98] ${
                                                active ? 'bg-blue-600 text-white shadow-sm' : 'bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-100'
                                            }`}
                                        >
                                            {option.name}
                                        </button>
                                    );
                                })}
                            </div>
                            {currentSegment && (currentSegment.subsegments || []).length > 0 && (
                                <div className="space-y-1.5">
                                    <div className="text-[11px] text-slate-500">Подсегменты — можно несколько</div>
                                    <PillChecks
                                        options={currentSegment.subsegments}
                                        value={filters.subsegments || []}
                                        onChange={(value) => patchFilters({ subsegments: value })}
                                    />
                                </div>
                            )}
                        </div>
                    )}

                    {openSections.has('group') && (
                        <div className="grid gap-2.5 rounded-xl bg-slate-50 p-3 sm:grid-cols-2">
                            <div>
                                {/* «Категория группы», а не просто «Категория»: рядом
                                    в фильтрах есть «Категории» — тарифы, и два поля с
                                    почти одинаковым названием на одном экране путают. */}
                                <div className="mb-1 text-[12.5px] font-medium text-slate-600">Категория группы</div>
                                <CustomSelect
                                    variant="ios"
                                    ariaLabel="Категория группы"
                                    placeholder="Не важно"
                                    value={groupCategory}
                                    onChange={(value) => { setGroupCategory(value); patchFilters({ group: undefined }); }}
                                    options={groupCategories}
                                />
                            </div>
                            <div>
                                <div className="mb-1 text-[12.5px] font-medium text-slate-600">Значение</div>
                                <CustomSelect
                                    variant="ios"
                                    ariaLabel="Группа"
                                    placeholder={groupCategory ? 'Выберите значение' : 'Сначала категория'}
                                    disabled={!groupCategory}
                                    value={filters.group || ''}
                                    onChange={(value) => patchFilters({ group: value })}
                                    options={groupOptions.map((item) => ({ value: item.key, label: item.label }))}
                                />
                            </div>
                        </div>
                    )}

                    {openSections.has('filters') && (
                        <div className="grid gap-2.5 rounded-xl bg-slate-50 p-3 sm:grid-cols-2">
                            {cityAllowed && (
                                <MultiFilter
                                    label="Город"
                                    options={refs?.cities || []}
                                    value={filters.city_ids || []}
                                    onChange={(value) => patchFilters({ city_ids: value })}
                                />
                            )}
                            <MultiFilter
                                label="Профессия"
                                options={refs?.professions || []}
                                value={filters.profession_ids || []}
                                onChange={(value) => patchFilters({ profession_ids: value })}
                            />
                            <MultiFilter
                                label="Статус на линии"
                                options={refs?.statuses || []}
                                value={filters.contractor_statuses || []}
                                onChange={(value) => patchFilters({ contractor_statuses: value })}
                            />
                            <MultiFilter
                                label="Категории"
                                options={refs?.categories || []}
                                value={filters.car_categories || []}
                                onChange={(value) => patchFilters({ car_categories: value })}
                            />
                            <MultiFilter
                                label="Услуги"
                                options={refs?.amenities || []}
                                value={filters.car_amenities || []}
                                onChange={(value) => patchFilters({ car_amenities: value })}
                            />
                            {!cityAllowed && (
                                <div className="self-end text-[11px] text-slate-500 sm:col-span-2">
                                    Город выбирается только для сегментов «Активные» и «Отток» — в остальных случаях
                                    у водителей ещё нет города работы.
                                </div>
                            )}
                        </div>
                    )}
                </IosSection>
            </div>

            <div className="space-y-3 lg:sticky lg:top-4">
                <MailingPreview title={draft.title} message={message} />

                <div className={`${iosCard} p-4`}>
                    <div className="flex items-baseline justify-between gap-2">
                        <span className="text-[12.5px] text-slate-500">Получатели</span>
                        {counting ? (
                            <Loader2 size={15} className="animate-spin text-slate-400" />
                        ) : (
                            <span className="text-[22px] font-semibold tabular-nums text-slate-900">
                                {count?.total == null ? '—' : formatCount(count.total)}
                            </span>
                        )}
                    </div>

                    {countError && (
                        <div className="mt-2 text-[11.5px] text-rose-600">{countError}</div>
                    )}

                    {/* Разбивку прячем на время пересчёта: цифры от ПРЕЖНЕГО
                        набора фильтров рядом с крутящимся итогом читаются как
                        свежие, и человек сверяет охват не с тем, что уйдёт. */}
                    {!countError && !counting && (count?.by_park || []).length > 1 && (
                        <ul className="mt-2.5 space-y-1 border-t border-slate-100 pt-2.5">
                            {count.by_park.map((row) => (
                                <li key={row.park_id} className="flex items-baseline justify-between gap-2 text-[12px]">
                                    <span className="min-w-0 truncate text-slate-500">{row.park_name}</span>
                                    {row.error
                                        ? <span className="shrink-0 text-rose-500">ошибка</span>
                                        : <span className="shrink-0 tabular-nums text-slate-700">{formatCount(row.count)}</span>}
                                </li>
                            ))}
                        </ul>
                    )}

                    <button
                        type="button"
                        disabled={!ready}
                        onClick={onSend}
                        className={`${iosBtnPrimary} mt-3 w-full`}
                    >
                        {sending || counting
                            ? <Loader2 size={15} className="animate-spin" />
                            : <Send size={15} />}
                        {sending ? 'Отправляем…'
                            : counting ? 'Считаем охват…'
                                : `Отправить${Number.isFinite(count?.total) ? ` (${formatCount(count.total)})` : ''}`}
                    </button>

                    <div className="mt-2 flex items-center justify-between gap-1">
                        <button type="button" className={iosBtnGhost} onClick={onOpenTemplates}>Шаблоны</button>
                        <button
                            type="button"
                            className={iosBtnGhost}
                            disabled={!String(draft.title || '').trim() && !message.trim()}
                            onClick={onSaveTemplate}
                        >
                            Сохранить как шаблон
                        </button>
                    </div>
                </div>

            </div>
        </div>
    );
}
