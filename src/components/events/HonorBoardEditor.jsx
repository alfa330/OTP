import React, { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Check, ChevronDown, ChevronRight, ChevronUp, EyeOff, ImagePlus, Loader2, Plus, RotateCcw, Search, Trash2, UserRound,
} from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import {
    IosMenu, IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, prefersReducedMotion,
} from '../ui/ios';
import InfoHint from '../common/InfoHint';
import useIsMobileShell from '../common/useIsMobileShell';
import { HonorMainPhoto, HonorPhoto, HonorPortrait, HonorTile, IconPicker, NominationIcon } from './honorUi';
import {
    BOARD_STATE_LABELS, CUSTOM_NOMINATION, DESCRIPTION_MAX, HONOR_DEPARTMENTS, NAME_MAX, ROLE_LABELS,
    TITLE_MAX, blankEntry, boardPlace, entryHasContent, entryPhoto, filterPeople, formatMoment, honorDepartment,
    honorDisplayName, nominationMeta, nominationSlotsLeft, periodLabel, publishIssues, serializeEntries, sortPeople, toneOf,
} from './honorBoardMeta';

/*
 * Окно тренера «Доски почёта» (ТЗ #318, п. 4): месяц, направление, карточки
 * номинаций и «Опубликовать».
 *
 * ЧЕРНОВИК СОХРАНЯЕТСЯ САМ — через мгновение после последней правки, при уходе
 * из поля, при смене месяца или направления и при закрытии окна. Кнопки
 * «Сохранить» нет: длинный стих не пропадёт от случайно закрытого окна, а
 * сотрудники всё равно видят только опубликованное. Сервер сверяет revision:
 * два окна (или два тренера) не затрут друг друга молча — опоздавшее получит
 * свежую версию.
 *
 * НОМИНАЦИИ — у всех трёх направлений одинаково (с 06.11.2026): ключевая
 * «Лучший сотрудник отдела» закреплена, остальные тренер называет сам, меняет
 * им значок, убирает и добавляет — вместе с ключевой до восьми.
 *
 * ФОТО НА ДОСКЕ — своё у карточки: тренер фотографирует сотрудника и загружает
 * снимок, фон убирает нейросеть прямо в браузере (cutout/). Без своего фото
 * карточка берёт фото из профиля.
 *
 * ВЫБОР СОТРУДНИКА И ФОТО — второй уровень этого же окна, как в «Настройках»
 * iOS: окно поверх окна клало бы второе затемнение на первое.
 */

const AUTOSAVE_MS = 800;
// Список людей — постоянной высоты: окно стоит посреди экрана, и меняйся его
// рост с каждой буквой поиска, поле ввода уезжало бы из-под курсора.
const PEOPLE_LIST_CLASS = 'thin-scroll h-[400px] overflow-y-auto overscroll-contain';
const SEARCH_INPUT = 'w-full rounded-xl border-0 bg-slate-100 py-2.5 pl-9 pr-3.5 text-[14px] text-slate-900 placeholder-slate-400 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70';
const INVALID = 'ring-2 ring-rose-300';

const DEPARTMENT_OPTIONS = HONOR_DEPARTMENTS.map((item) => ({ value: item.key, label: item.label }));
const DRAFT_HINT = 'Черновик сохраняется сам, по ходу ввода. Сотрудники видят доску только после «Опубликовать»: '
    + 'правки опубликованной доски тоже выходят к ним следующей публикацией.';
const PHOTO_HINT = 'Фото на карточке доски. Сфотографируйте сотрудника и загрузите снимок — фон уберётся сам, '
    + 'прямо в браузере. Без своего фото карточка берёт фото из профиля. Загруженное фото окно подставит '
    + 'этому сотруднику и в следующие месяцы.';
const CUTOUT_HINT = 'Фон убирает нейросеть прямо в браузере, фото уходит на сервер только по «Сохранить фото». '
    + 'Если края вышли неаккуратно (тёмный фон, человек в полный рост) — сохраните фото с фоном.';
const PHOTO_MODES = [
    { value: 'cutout', label: 'Без фона' },
    { value: 'photo', label: 'С фоном' },
];
const ISSUE_FIELD_OF = { user_id: 'user', name: 'name', description: 'description', title: 'title' };
const ISSUE_ORDER = ['title', 'user', 'name', 'description'];

let keySeed = 0;
const withKeys = (entries) => (entries || []).map((entry) => {
    keySeed += 1;
    return { ...entry, _key: `honor-${keySeed}` };
});

const sameTarget = (view, target) => Boolean(view && target
    && view.period === target.period && view.department === target.department);

const personLine = (person) => [person?.direction, ROLE_LABELS[person?.role]].filter(Boolean).join(' · ');

/* Порядок «не заполнено» — как на экране: общая строка доски, затем по
   номинациям сверху вниз, внутри — по полям карточки. */
const sortIssues = (list) => [...list].sort((a, b) => {
    if (a.index === null) return -1;
    if (b.index === null) return 1;
    return a.index - b.index || ISSUE_ORDER.indexOf(a.field) - ISSUE_ORDER.indexOf(b.field);
});

/* Подпись поля. <label> — только у текста и связан с полем по id: внутри одной
   метки с «i» щелчок по подписи нажимал бы кнопку подсказки, а не ставил курсор
   в поле, и у самого поля не было бы имени для читалки. */
const FieldLabel = ({ children, hint = '', htmlFor }) => (
    <span className="flex min-h-5 items-center px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        {htmlFor ? <label htmlFor={htmlFor}>{children}</label> : <span>{children}</span>}
        {hint && <InfoHint text={hint} side="left" className="ml-1.5 align-middle" />}
    </span>
);

const IssueText = ({ text }) => (text
    ? <span className="block px-1 text-[12.5px] leading-snug text-rose-600 motion-safe:animate-reveal">{text}</span>
    : null);

/* Описание растёт вместе с текстом: стих в четыре строки виден целиком, а
   не за полосой прокрутки внутри крошечного поля. */
const AutoTextarea = ({ value, className, ...rest }) => {
    const ref = useRef(null);
    useLayoutEffect(() => {
        const el = ref.current;
        if (!el) return;
        el.style.height = 'auto';
        el.style.height = `${Math.min(el.scrollHeight + 2, 360)}px`;
    }, [value]);
    return <textarea ref={ref} rows={4} value={value} className={className} {...rest} />;
};

const PHOTO_CAPTIONS = {
    cutout: 'Своё фото без фона',
    photo: 'Своё фото с фоном',
    avatar: 'Фото из профиля',
    initials: 'Фото нет — на карточке инициалы',
};

/* «Фото на доске»: как карточка выглядит сейчас (маленькая плитка), загрузить
   своё и вернуть фото профиля. Кнопки — под подписью, а не справа от неё: на
   телефоне ряд «плитка · подпись · две кнопки» сжимал подпись в столбик слов. */
const PhotoField = ({ entry, person, tone, photo, onUpload, onReset }) => {
    const own = Boolean(entry.photo_id);
    return (
        <div className="space-y-1.5">
            <FieldLabel hint={PHOTO_HINT}>Фото на доске</FieldLabel>
            <div className="flex items-center gap-3 rounded-xl bg-slate-50 p-2.5 ring-1 ring-slate-200/70">
                <span
                    data-honor-photo-preview={photo.kind}
                    className={`relative h-[84px] w-[68px] shrink-0 overflow-hidden rounded-[14px] ${tone.tile}`}
                >
                    <HonorPortrait photo={photo} name={entry.name || person?.name} ink={tone.ink} figure={0.94} circle={50} inset={4} />
                </span>
                <span className="min-w-0 grow basis-0">
                    <span className="block text-[14px] font-medium text-slate-900">{PHOTO_CAPTIONS[photo.kind]}</span>
                    <span className="mt-2 flex flex-wrap items-center gap-x-1 gap-y-1.5">
                        <button type="button" onClick={onUpload} className={iosBtnSecondary}>
                            <ImagePlus size={15} aria-hidden="true" />
                            {own ? 'Заменить' : 'Загрузить фото'}
                        </button>
                        {own && (
                            <button
                                type="button"
                                onClick={onReset}
                                className="rounded-lg px-2.5 py-1.5 text-[13px] font-medium text-slate-500 transition hover:bg-slate-200/70 hover:text-slate-700 active:scale-95"
                                title="Карточка возьмёт фото из профиля"
                            >
                                Убрать
                            </button>
                        )}
                    </span>
                </span>
            </div>
        </div>
    );
};

const SlotCard = ({
    entry, index, place, departmentKey, person, photo, issueFor, onEdit, onBlur, onPick, onRemove, onUpload, onResetPhoto,
    titleRef,
}) => {
    const ids = useId();
    const meta = nominationMeta(departmentKey, entry.nomination, entry.title, entry.icon);
    const tone = toneOf(entry, place);
    const custom = entry.nomination === CUSTOM_NOMINATION;
    const issue = (field) => issueFor(index, field);
    const hint = [
        meta.reason ? `За что: ${meta.reason}.` : '',
        meta.main ? 'Главная номинация доски — её карточка крупнее и в золотой рамке.' : '',
    ].filter(Boolean).join(' ');
    const length = entry.description.length;
    return (
        <section className="space-y-2">
            <header className="flex min-h-8 items-center gap-2.5 px-1">
                <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-[9px] ${tone.tile} ${tone.ink}`}>
                    <NominationIcon name={meta.icon} size={15} strokeWidth={2.2} />
                </span>
                <span className="min-w-0 flex-1 text-[14.5px] font-semibold text-slate-800">
                    <span className="break-words">{custom ? (entry.title.trim() || 'Новая номинация') : meta.title}</span>
                    {hint && <InfoHint text={hint} side="left" className="ml-1.5 align-middle" />}
                </span>
                {custom && (
                    <button
                        type="button"
                        onClick={onRemove}
                        className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 active:scale-95"
                        aria-label="Убрать номинацию"
                        title="Убрать номинацию"
                    >
                        <Trash2 size={15} />
                    </button>
                )}
            </header>
            <div className={`${iosCard} space-y-3.5 p-3.5 sm:p-4`}>
                {custom && (
                    <div className="space-y-1.5">
                        <FieldLabel htmlFor={`${ids}-title`} hint="Название и значок номинации на доске. Значок — по кнопке слева.">
                            Номинация
                        </FieldLabel>
                        <div className="flex items-center gap-2">
                            <IconPicker value={meta.icon} tone={tone} onChange={(icon) => onEdit({ icon })} />
                            <input
                                id={`${ids}-title`}
                                ref={titleRef}
                                value={entry.title}
                                maxLength={TITLE_MAX}
                                onChange={(event) => onEdit({ title: event.target.value })}
                                onBlur={onBlur}
                                placeholder="Например, «Лучший администратор»"
                                data-honor-issue={`${index}:title`}
                                className={`${iosInput} min-w-0 flex-1${issue('title') ? ` ${INVALID}` : ''}`}
                            />
                        </div>
                        <IssueText text={issue('title')} />
                    </div>
                )}
                <div className="space-y-1.5">
                    <FieldLabel htmlFor={`${ids}-user`}>Сотрудник</FieldLabel>
                    <button
                        id={`${ids}-user`}
                        type="button"
                        onClick={onPick}
                        data-honor-issue={`${index}:user`}
                        className={`flex w-full items-center gap-3 rounded-xl bg-slate-50 px-3 py-2.5 text-left ring-1 ring-slate-200/70 transition hover:bg-slate-100 active:scale-[0.99]${
                            issue('user') ? ` ${INVALID}` : ''
                        }`}
                    >
                        {entry.user_id ? (
                            <HonorPhoto name={person?.name || entry.name} url={person?.photo_url} size={44} />
                        ) : (
                            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-white text-slate-400 ring-1 ring-slate-200">
                                <UserRound size={20} aria-hidden="true" />
                            </span>
                        )}
                        <span className="min-w-0 flex-1">
                            {entry.user_id ? (
                                <>
                                    <span className="block truncate text-[14.5px] font-medium text-slate-900">
                                        {person?.name || entry.name || 'Сотрудник'}
                                    </span>
                                    <span className="block truncate text-[12.5px] text-slate-500">
                                        {personLine(person) || 'Сменить сотрудника'}
                                    </span>
                                </>
                            ) : (
                                <span className="block text-[14.5px] font-medium text-blue-600">Выбрать сотрудника</span>
                            )}
                        </span>
                        <ChevronRight size={17} className="shrink-0 text-slate-300" aria-hidden="true" />
                    </button>
                    <IssueText text={issue('user')} />
                </div>
                {entry.user_id && (
                    <PhotoField
                        entry={entry}
                        person={person}
                        tone={tone}
                        photo={photo}
                        onUpload={onUpload}
                        onReset={onResetPhoto}
                    />
                )}
                <div className="space-y-1.5">
                    <FieldLabel
                        htmlFor={`${ids}-name`}
                        hint="Так имя будет написано на доске. Подставляется из профиля без отчества — поправьте, если нужно."
                    >
                        Имя и фамилия
                    </FieldLabel>
                    <input
                        id={`${ids}-name`}
                        value={entry.name}
                        maxLength={NAME_MAX}
                        onChange={(event) => onEdit({ name: event.target.value })}
                        onBlur={onBlur}
                        placeholder="Имя и фамилия"
                        data-honor-issue={`${index}:name`}
                        className={`${iosInput}${issue('name') ? ` ${INVALID}` : ''}`}
                    />
                    <IssueText text={issue('name')} />
                </div>
                <div className="space-y-1.5">
                    <span className="flex items-end justify-between gap-2">
                        <FieldLabel
                            htmlFor={`${ids}-description`}
                            hint="Краткое описание заслуг — можно и стихами: переносы строк сохранятся."
                        >
                            Описание заслуг
                        </FieldLabel>
                        {length > DESCRIPTION_MAX * 0.8 && (
                            <span className="px-1 text-[11.5px] tabular-nums text-slate-400">{length} / {DESCRIPTION_MAX}</span>
                        )}
                    </span>
                    <AutoTextarea
                        id={`${ids}-description`}
                        value={entry.description}
                        maxLength={DESCRIPTION_MAX}
                        onChange={(event) => onEdit({ description: event.target.value })}
                        onBlur={onBlur}
                        placeholder="За что награждаем"
                        data-honor-issue={`${index}:description`}
                        className={`${iosInput} resize-none leading-relaxed${issue('description') ? ` ${INVALID}` : ''}`}
                    />
                    <IssueText text={issue('description')} />
                </div>
            </div>
        </section>
    );
};

const Bone = ({ className = '', style }) => (
    <span className={`block animate-pulse rounded-full bg-slate-200/70 ${className}`} style={style} />
);

/* Заготовка окна — те же карточки номинаций: окно открывается сразу в свой
   рост, а не коротким «Загружаем…», которое потом резко вырастает. */
const SlotsSkeleton = ({ count }) => (
    <div className="space-y-5" aria-busy="true" aria-label="Загрузка">
        {Array.from({ length: count }).map((_, index) => (
            <section key={index} className="space-y-2">
                <div className="flex min-h-8 items-center gap-2.5 px-1">
                    <span className="block h-7 w-7 animate-pulse rounded-[9px] bg-slate-200/70" />
                    <Bone className="h-4 w-40" />
                </div>
                <div className={`${iosCard} space-y-3.5 p-3.5 sm:p-4`}>
                    <div className="space-y-1.5">
                        <Bone className="h-3 w-20" />
                        <span className="block h-[66px] animate-pulse rounded-xl bg-slate-200/70" />
                    </div>
                    <div className="space-y-1.5">
                        <Bone className="h-3 w-28" />
                        <span className="block h-[42px] animate-pulse rounded-xl bg-slate-200/70" />
                    </div>
                    <div className="space-y-1.5">
                        <Bone className="h-3 w-32" />
                        <span className="block h-[106px] animate-pulse rounded-xl bg-slate-200/70" />
                    </div>
                </div>
            </section>
        ))}
    </div>
);

const PeopleBones = () => (
    <ul className="divide-y divide-slate-100" aria-busy="true" aria-label="Загрузка">
        {[46, 38, 52, 41, 49, 44, 36, 50].map((width) => (
            <li key={width} className="flex items-center gap-3 px-3.5 py-2.5">
                <span className="block h-10 w-10 shrink-0 animate-pulse rounded-full bg-slate-200/70" />
                <span className="flex-1 space-y-1.5">
                    <Bone className="h-3.5" style={{ width: `${width}%` }} />
                    <Bone className="h-3 w-1/3" />
                </span>
            </li>
        ))}
    </ul>
);

const PeoplePicker = ({ state, query, onQuery, selectedId, onPick, onClear, onRetry, autoFocus }) => {
    const items = useMemo(() => filterPeople(state?.items || [], query), [state, query]);
    let list;
    if (!state || state.status === 'loading') {
        list = <PeopleBones />;
    } else if (state.status === 'error') {
        list = (
            <div className="grid h-full place-items-center px-4 text-center text-[13px] text-slate-500">
                <div>
                    Список сотрудников не загрузился
                    <button type="button" onClick={onRetry} className="mt-2 block w-full font-medium text-blue-600">Повторить</button>
                </div>
            </div>
        );
    } else if (!items.length) {
        list = (
            <div className="grid h-full place-items-center px-4 text-center text-[13px] text-slate-500">
                {query ? 'Никого не нашли — проверьте написание' : 'В отделе пока нет сотрудников'}
            </div>
        );
    } else {
        list = (
            <ul className="divide-y divide-slate-100">
                {items.map((person) => {
                    const selected = person.id === selectedId;
                    return (
                        <li key={person.id}>
                            <button
                                type="button"
                                onClick={() => onPick(person)}
                                aria-pressed={selected}
                                className={`flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition active:bg-slate-100 ${
                                    selected ? 'bg-blue-50 hover:bg-blue-50' : 'hover:bg-slate-50'
                                }`}
                            >
                                <HonorPhoto name={person.name} url={person.photo_url || person.honor_photo?.url} size={40} />
                                <span className="min-w-0 flex-1">
                                    <span className={`block truncate text-[14.5px] font-medium ${selected ? 'text-blue-700' : 'text-slate-900'}`}>
                                        {person.name}
                                    </span>
                                    {personLine(person) && (
                                        <span className="block truncate text-[12.5px] text-slate-500">{personLine(person)}</span>
                                    )}
                                </span>
                                {selected && <Check size={17} strokeWidth={2.4} className="shrink-0 text-blue-600" aria-hidden="true" />}
                            </button>
                        </li>
                    );
                })}
            </ul>
        );
    }
    return (
        <div className="space-y-3">
            <label className="relative block">
                <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                <input
                    autoFocus={autoFocus}
                    value={query}
                    onChange={(event) => onQuery(event.target.value)}
                    placeholder="Поиск по ФИО или направлению"
                    aria-label="Поиск сотрудника"
                    className={SEARCH_INPUT}
                />
            </label>
            <div className={`${iosCard} overflow-hidden`}>
                <div className={PEOPLE_LIST_CLASS}>{list}</div>
            </div>
            {selectedId && (
                <button
                    type="button"
                    onClick={onClear}
                    className="w-full rounded-xl px-3 py-2.5 text-[14px] font-medium text-rose-600 transition hover:bg-rose-50 active:scale-[0.99]"
                >
                    Убрать сотрудника из номинации
                </button>
            )}
        </div>
    );
};

/* Строка состояния обработки фото: загрузка модели (один раз), вырезание, ошибка. */
const cutoutStatus = (task) => {
    if (!task) return '';
    if (task.cut.state === 'model') {
        const { loaded = 0, total = 0 } = task.cut;
        const percent = total ? Math.min(99, Math.round((loaded / total) * 100)) : 0;
        return percent ? `Загружаю нейросеть для фона… ${percent}%` : 'Загружаю нейросеть для фона…';
    }
    if (task.cut.state === 'run' || task.cut.state === 'decode') return 'Убираю фон…';
    if (task.cut.state === 'error') return task.cut.error;
    return '';
};

/* Второй уровень окна — «Фото для доски»: снимок на плитке так, как его
   увидят, и выбор «без фона / с фоном». */
const PhotoScreen = ({ task, entry, departmentKey, place, onMode }) => {
    const meta = nominationMeta(departmentKey, entry.nomination, entry.title, entry.icon);
    const tone = toneOf(entry, place);
    const variant = task.mode === 'cutout' ? task.cut : task.asIs;
    const busy = variant.state !== 'ready' && variant.state !== 'error';
    const status = task.mode === 'cutout' ? cutoutStatus(task) : (task.asIs.state === 'error' ? task.asIs.error : '');
    const photo = variant.state === 'ready'
        ? { kind: task.mode === 'cutout' ? 'cutout' : 'photo', url: variant.url, ratio: variant.ratio || 0 }
        : { kind: 'initials', url: '' };
    return (
        <div className="space-y-4">
            <div className="mx-auto w-full max-w-[340px]">
                <div className="relative">
                    {/* Превью — та карточка, на которой фото выйдет: у ключевой
                        номинации золотая колонка главной карточки, у остальных — плитка. */}
                    {meta.main ? (
                        <div className="overflow-hidden rounded-[28px] ring-2 ring-amber-300">
                            <HonorMainPhoto entry={entry} tone={tone} photo={photo} className="h-[400px]" />
                        </div>
                    ) : (
                        <HonorTile
                            entry={{ ...entry, description: '' }}
                            meta={meta}
                            tone={tone}
                            photo={photo}
                            height={380}
                        />
                    )}
                    {busy && (
                        <span className="absolute inset-0 grid place-items-center rounded-[28px] bg-slate-50/60">
                            <Loader2 size={30} className="animate-spin text-slate-500" aria-hidden="true" />
                        </span>
                    )}
                </div>
            </div>
            <div className="flex flex-col items-center gap-2">
                <span className="inline-flex items-center gap-1.5">
                    <IosSegmented
                        value={task.mode}
                        options={PHOTO_MODES}
                        onChange={onMode}
                        ariaLabel="Фон на фото"
                    />
                    <InfoHint text={CUTOUT_HINT} side="left" />
                </span>
                <p
                    aria-live="polite"
                    className={`min-h-5 text-center text-[13px] ${
                        variant.state === 'error' ? 'text-rose-600' : 'text-slate-500'
                    }`}
                >
                    {status}
                </p>
            </div>
        </div>
    );
};

/* «Не заполнено: N ⌃ ⌄» — когда полей больше одного, стрелки ведут от поля к
   полю; одно поле — к нему уже подвели. */
const IssueNav = ({ count, onStep }) => {
    if (!(count > 1)) return null;
    const button = 'grid h-7 w-7 place-items-center rounded-lg text-rose-600 transition hover:bg-rose-100 active:scale-95';
    return (
        <div
            role="group"
            aria-label="Поля, где не хватает данных"
            className="inline-flex items-center gap-0.5 rounded-xl bg-rose-50 py-0.5 pl-3 pr-0.5 text-[12.5px] font-medium text-rose-700 ring-1 ring-rose-200 motion-safe:animate-reveal"
        >
            <span className="mr-1 tabular-nums" aria-live="polite">Не заполнено: {count}</span>
            <button type="button" className={button} onClick={(event) => onStep(-1, event.detail === 0)} aria-label="К предыдущему полю" title="Предыдущее">
                <ChevronUp size={16} />
            </button>
            <button type="button" className={button} onClick={(event) => onStep(1, event.detail === 0)} aria-label="К следующему полю" title="Следующее">
                <ChevronDown size={16} />
            </button>
        </div>
    );
};

const blankVariant = () => ({ state: 'idle', blob: null, url: '', error: '', loaded: 0, total: 0 });

const fileNameFor = (blob) => {
    const type = String(blob?.type || '');
    if (type === 'image/webp') return 'honor.webp';
    if (type === 'image/png') return 'honor.png';
    return 'honor.jpg';
};

/* departments — направления, которые человек ведёт (с сервера, manage_departments). */
export default function HonorBoardEditor({ open, target, departments, apiRoot, authHeaders, notify, onClose, onBoardChanged }) {
    const isMobileShell = useIsMobileShell();
    const [period, setPeriod] = useState(target.period);
    const [departmentKey, setDepartmentKey] = useState(target.department);
    const [board, setBoard] = useState(null);
    const [entries, setEntries] = useState([]);
    const [people, setPeople] = useState({});
    const [photos, setPhotos] = useState({});
    const [months, setMonths] = useState([]);
    const [loadState, setLoadState] = useState('loading');
    const [loadError, setLoadError] = useState('');
    const [saveState, setSaveState] = useState('idle');
    const [busy, setBusy] = useState('');
    const [issues, setIssues] = useState([]);
    const [picker, setPicker] = useState(null);
    const [query, setQuery] = useState('');
    const [candidates, setCandidates] = useState({});
    const [pendingTarget, setPendingTarget] = useState(null);
    const [session, setSession] = useState(null);
    // Второй уровень «Фото для доски»: {key (строка доски), file, mode, asIs, cut, saving}.
    const [photoTask, setPhotoTask] = useState(null);

    const entriesRef = useRef(entries);
    entriesRef.current = entries;
    const photosRef = useRef(photos);
    photosRef.current = photos;
    const targetRef = useRef({ period: target.period, department: target.department });
    const revisionRef = useRef(0);
    const dirtyRef = useRef(false);
    const savingRef = useRef(null);
    // Сколько раз сервер подменил доску свежей версией (ответ 409): по нему
    // flush узнаёт подмену, даже если её поймал чужой вызов, пока этот ждал.
    const replacedRef = useRef(0);
    // Месяц и направление, выбранные, пока доводится сохранение прежней доски.
    const desiredRef = useRef(null);
    const timerRef = useRef(null);
    const ticketRef = useRef(0);
    const candidatesRef = useRef(candidates);
    candidatesRef.current = candidates;
    const bodyRef = useRef(null);
    const issueCursor = useRef(-1);
    const focusTitleOf = useRef(null);
    const titleRefs = useRef({});
    const fileInputRef = useRef(null);
    // Для какой строки доски выбирают файл (ключ строки, а не номер: номера
    // сдвигаются, когда номинацию убирают).
    const uploadKeyRef = useRef(null);
    const photoTicketRef = useRef(0);
    const photoTaskRef = useRef(photoTask);
    photoTaskRef.current = photoTask;

    /* Новое открытие окна — состояние строится при отрисовке, а не эффектом:
       иначе первый кадр стоял бы с доской прошлого открытия. */
    if (open && session !== target) {
        setSession(target);
        setPeriod(target.period);
        setDepartmentKey(target.department);
        setBoard(null);
        setEntries([]);
        setLoadState('loading');
        setLoadError('');
        setSaveState('idle');
        setIssues([]);
        setPicker(null);
        setPhotoTask(null);
        setQuery('');
        setCandidates({});
        setPendingTarget(null);
        targetRef.current = { period: target.period, department: target.department };
        desiredRef.current = null;
        revisionRef.current = 0;
        dirtyRef.current = false;
    }

    const updateMonth = useCallback((view) => {
        if (!view) return;
        setMonths((prev) => prev.map((month) => (month.period === view.period
            ? { ...month, states: { ...month.states, [view.department]: view.state } }
            : month)));
    }, []);

    const mergeResponse = useCallback((data) => {
        if (data?.people) setPeople((prev) => ({ ...prev, ...data.people }));
        if (data?.photos) setPhotos((prev) => ({ ...prev, ...data.photos }));
    }, []);

    /* Доска с сервера целиком заменяет местную: открытие, смена месяца,
       ответ «уже изменили в другом окне», отмена правок. */
    const applyBoard = useCallback((data) => {
        const view = data?.board;
        revisionRef.current = Number(view?.revision) || 0;
        dirtyRef.current = false;
        setBoard(view);
        setEntries(withKeys(view?.entries));
        mergeResponse(data);
        setIssues([]);
        setSaveState('idle');
        updateMonth(view);
    }, [mergeResponse, updateMonth]);

    const loadCandidates = useCallback(async (department, { retry = false } = {}) => {
        const known = candidatesRef.current[department];
        if (known && (known.status === 'ready' || known.status === 'loading') && !retry) return;
        setCandidates((prev) => ({ ...prev, [department]: { status: 'loading', items: [] } }));
        try {
            const response = await axios.get(`${apiRoot}/api/events/honor_board/candidates`, {
                headers: authHeaders(),
                params: { department },
            });
            const items = sortPeople(Array.isArray(response?.data?.people) ? response.data.people : []);
            setCandidates((prev) => ({ ...prev, [department]: { status: 'ready', items } }));
        } catch {
            setCandidates((prev) => ({ ...prev, [department]: { status: 'error', items: [] } }));
        }
    }, [apiRoot, authHeaders]);

    const loadBoard = useCallback(async (nextPeriod, nextDepartment) => {
        ticketRef.current += 1;
        const mine = ticketRef.current;
        setLoadState('loading');
        setLoadError('');
        try {
            const response = await axios.get(`${apiRoot}/api/events/honor_board/edit`, {
                headers: authHeaders(),
                params: { period: nextPeriod, department: nextDepartment },
            });
            if (mine !== ticketRef.current) return;
            const data = response?.data || {};
            if (Array.isArray(data.months)) setMonths(data.months);
            applyBoard(data);
            setLoadState('ready');
            // Список людей нужен почти всегда — берём его сразу, пока тренер читает доску.
            loadCandidates(nextDepartment);
        } catch (requestError) {
            if (mine !== ticketRef.current) return;
            setLoadError(requestError?.response?.data?.error || 'Не удалось открыть итоги месяца');
            setLoadState('error');
        }
    }, [apiRoot, applyBoard, authHeaders, loadCandidates]);

    const loadBoardRef = useRef(loadBoard);
    loadBoardRef.current = loadBoard;
    useEffect(() => {
        if (open && session) loadBoardRef.current(session.period, session.department);
    }, [open, session]);

    useEffect(() => () => clearTimeout(timerRef.current), []);

    /* Сохранить черновик сейчас. Исход:
         'saved'    — на сервере то же, что на экране (в том числе нечего было сохранять);
         'replaced' — пока сохраняли, доску изменили в другом окне: на экране уже
                      свежая версия, и действие, ради которого сохраняли (публикация,
                      откат, смена месяца, закрытие), выполнять нельзя — человек её
                      ещё не видел;
         'failed'   — не сохранилось (связь), правки остались на экране.
       Правки, сделанные, пока шёл запрос, уходят следующим кругом. */
    const flush = useCallback(async () => {
        clearTimeout(timerRef.current);
        const replacedBefore = replacedRef.current;
        for (;;) {
            while (savingRef.current) {
                // eslint-disable-next-line no-await-in-loop
                await savingRef.current;
            }
            if (!dirtyRef.current) break;
            const destination = targetRef.current;
            const sent = entriesRef.current;
            dirtyRef.current = false;
            setSaveState('saving');
            let failed = false;
            const request = (async () => {
                try {
                    const response = await axios.put(`${apiRoot}/api/events/honor_board/edit`, {
                        period: destination.period,
                        department: destination.department,
                        entries: serializeEntries(sent),
                        revision: revisionRef.current,
                    }, { headers: authHeaders() });
                    const view = response?.data?.board;
                    if (sameTarget(view, targetRef.current)) {
                        revisionRef.current = Number(view.revision) || 0;
                        setBoard(view);
                        updateMonth(view);
                        mergeResponse(response?.data);
                    }
                } catch (requestError) {
                    const data = requestError?.response?.data;
                    if (requestError?.response?.status === 409 && data?.board) {
                        replacedRef.current += 1;
                        if (sameTarget(data.board, targetRef.current)) applyBoard(data);
                        notify?.(data.error || 'Итоги изменили в другом окне — показываю свежую версию', 'error');
                    } else if (data?.code === 'HONOR_BAD_PHOTO') {
                        // Фото строки сервер не нашёл у выбранного в ней сотрудника.
                        // Снимаем своё фото только там, где оно не сходится и у нас
                        // (нет такого фото или оно другого человека); не нашли таких —
                        // со всех строк: иначе черновик не сохранится никогда.
                        const known = photosRef.current;
                        const wrong = (entry) => entry.photo_id
                            && (!known[String(entry.photo_id)] || known[String(entry.photo_id)].user_id !== entry.user_id);
                        const strip = sent.some(wrong) ? wrong : (entry) => Boolean(entry.photo_id);
                        const fix = (list) => list.map((entry) => (strip(entry) ? { ...entry, photo_id: null } : entry));
                        entriesRef.current = fix(entriesRef.current);
                        setEntries((prev) => fix(prev));
                        dirtyRef.current = true;
                        notify?.(data.error, 'error');
                    } else {
                        dirtyRef.current = true;
                        failed = true;
                        setSaveState('error');
                    }
                }
            })();
            savingRef.current = request;
            // eslint-disable-next-line no-await-in-loop
            await request;
            savingRef.current = null;
            if (failed) return 'failed';
        }
        if (replacedRef.current !== replacedBefore) return 'replaced';
        if (!dirtyRef.current) setSaveState((prev) => (prev === 'saving' ? 'saved' : prev));
        return 'saved';
    }, [apiRoot, applyBoard, authHeaders, mergeResponse, notify, updateMonth]);

    const flushRef = useRef(flush);
    flushRef.current = flush;

    const markDirty = useCallback(() => {
        dirtyRef.current = true;
        setSaveState('pending');
        clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => { flushRef.current(); }, AUTOSAVE_MS);
    }, []);

    const edit = useCallback((index, patch) => {
        setEntries((prev) => prev.map((entry, position) => (position === index ? { ...entry, ...patch } : entry)));
        const fields = Object.keys(patch).map((key) => ISSUE_FIELD_OF[key]).filter(Boolean);
        setIssues((prev) => prev.filter((item) => !(item.index === index && fields.includes(item.field))
            && !(item.index === null && (patch.user_id || patch.name || patch.description))));
        markDirty();
    }, [markDirty]);

    const saveOnBlur = useCallback(() => {
        if (dirtyRef.current) flushRef.current();
    }, []);

    const savingFailed = useCallback(() => {
        notify?.('Черновик не сохранился — проверьте связь и повторите', 'error');
    }, [notify]);

    /* Сменить месяц или направление. Выбор виден сразу (pendingTarget), а
       прежняя доска сперва досохраняется; выбрали ещё что-то за это время —
       решает последний выбор, а не устаревшее состояние. */
    const switchTo = useCallback(async (patch) => {
        const base = desiredRef.current || targetRef.current;
        const desired = { period: patch.period ?? base.period, department: patch.department ?? base.department };
        if (desired.period === targetRef.current.period && desired.department === targetRef.current.department) {
            desiredRef.current = null;
            setPendingTarget(null);
            return;
        }
        desiredRef.current = desired;
        setPendingTarget(desired);
        const outcome = await flushRef.current();
        if (desiredRef.current !== desired) return;
        desiredRef.current = null;
        setPendingTarget(null);
        if (outcome === 'failed') savingFailed();
        if (outcome !== 'saved') return;
        targetRef.current = desired;
        setPeriod(desired.period);
        setDepartmentKey(desired.department);
        setPicker(null);
        setQuery('');
        setIssues([]);
        loadBoardRef.current(desired.period, desired.department);
    }, [savingFailed]);

    const closePicker = useCallback(() => {
        setPicker(null);
        setQuery('');
    }, []);

    /* Фото: снимок разобран один раз, варианты «с фоном» и «без фона» готовятся
       параллельно; выбор между ними — мгновенный. Ссылки на превью освобождаем,
       когда второй уровень закрыт или выбран другой файл. */
    const releaseTask = useCallback((task) => {
        if (!task) return;
        for (const variant of [task.asIs, task.cut]) if (variant?.url) URL.revokeObjectURL(variant.url);
    }, []);

    const closePhoto = useCallback(() => {
        photoTicketRef.current += 1;
        releaseTask(photoTaskRef.current);
        setPhotoTask(null);
    }, [releaseTask]);

    useEffect(() => () => {
        photoTicketRef.current += 1;
        releaseTask(photoTaskRef.current);
    }, [releaseTask]);

    /* Доску подменили (ответ «изменили в другом окне», отмена правок): строки
       получили новые ключи, и фото больше не к чему приложить — закрываем
       второй уровень и говорим почему, а не оставляем пустой экран. */
    const photoLost = Boolean(photoTask && !photoTask.saving
        && !entries.some((entry) => entry._key === photoTask.key));
    useEffect(() => {
        if (!photoLost) return;
        closePhoto();
        notify?.('Доску изменили в другом окне — выберите фото заново', 'error');
    }, [closePhoto, notify, photoLost]);

    /* Окно закрыли — нейросеть вырезания фона выгружаем: модель и её память
       WebAssembly не должны жить в вкладке до перезагрузки портала (телефон).
       Следующий раз модель придёт из кэша браузера. */
    const pipelineRef = useRef(null);
    useEffect(() => {
        if (open) return undefined;
        pipelineRef.current?.releaseCutout?.();
        return undefined;
    }, [open]);
    useEffect(() => () => pipelineRef.current?.releaseCutout?.(), []);

    const startPhoto = useCallback(async (key, file) => {
        photoTicketRef.current += 1;
        const mine = photoTicketRef.current;
        releaseTask(photoTaskRef.current);
        setPhotoTask({
            key, mode: 'cutout', saving: false,
            asIs: { ...blankVariant(), state: 'decode' }, cut: { ...blankVariant(), state: 'decode' },
        });
        const patch = (variant, value) => {
            if (mine !== photoTicketRef.current) {
                if (value?.url) URL.revokeObjectURL(value.url);
                return;
            }
            setPhotoTask((prev) => (prev ? { ...prev, [variant]: { ...prev[variant], ...value } } : prev));
        };
        let pipeline;
        let canvas;
        try {
            // Модуль с нейросетью — отдельным чанком: окну он нужен только здесь.
            pipeline = await import('./cutout/honorPhotoPipeline');
            pipelineRef.current = pipeline;
            canvas = await pipeline.decodePhoto(file);
        } catch (error) {
            const message = error?.message || 'Не удалось открыть фото';
            patch('asIs', { state: 'error', error: message });
            patch('cut', { state: 'error', error: message });
            return;
        }
        pipeline.photoAsIs(canvas)
            .then((result) => patch('asIs', {
                state: 'ready', blob: result.blob, url: URL.createObjectURL(result.blob), ratio: result.width / result.height,
            }))
            .catch((error) => patch('asIs', { state: 'error', error: error?.message || 'Не удалось подготовить фото' }));
        try {
            const result = await pipeline.photoWithoutBackground(canvas, {
                onProgress: (progress) => patch('cut', progress.stage === 'model'
                    ? { state: 'model', loaded: progress.loaded, total: progress.total }
                    : { state: 'run' }),
            });
            patch('cut', { state: 'ready', blob: result.blob, url: URL.createObjectURL(result.blob) });
        } catch (error) {
            patch('cut', { state: 'error', error: error?.message || 'Не удалось убрать фон' });
            // Без фона не вышло — сразу показываем вариант с фоном.
            if (mine === photoTicketRef.current) setPhotoTask((prev) => (prev ? { ...prev, mode: 'photo' } : prev));
        }
    }, [releaseTask]);

    const openUpload = useCallback((key) => {
        uploadKeyRef.current = key;
        fileInputRef.current?.click();
    }, []);

    const onFileChosen = useCallback((event) => {
        const file = event.target.files?.[0];
        // Сбрасываем выбор: тот же файл второй раз тоже должен срабатывать.
        event.target.value = '';
        const key = uploadKeyRef.current;
        if (!file || !key) return;
        setPicker(null);
        startPhoto(key, file);
    }, [startPhoto]);

    const savePhoto = useCallback(async () => {
        const task = photoTaskRef.current;
        if (!task || task.saving) return;
        const variant = task.mode === 'cutout' ? task.cut : task.asIs;
        const index = entriesRef.current.findIndex((entry) => entry._key === task.key);
        const entry = index >= 0 ? entriesRef.current[index] : null;
        if (!variant?.blob || !entry?.user_id) return;
        setPhotoTask((prev) => (prev ? { ...prev, saving: true } : prev));
        try {
            const form = new FormData();
            form.append('user_id', String(entry.user_id));
            form.append('cutout', task.mode === 'cutout' ? '1' : '0');
            form.append('photo', variant.blob, fileNameFor(variant.blob));
            const response = await axios.post(`${apiRoot}/api/events/honor_board/photos`, form, { headers: authHeaders() });
            const photo = response?.data?.photo;
            if (!photo?.id) throw new Error('no photo');
            setPhotos((prev) => ({ ...prev, [String(photo.id)]: photo }));
            setPeople((prev) => {
                const person = prev[String(entry.user_id)];
                return person ? { ...prev, [String(entry.user_id)]: { ...person, honor_photo: photo } } : prev;
            });
            // Новое фото — и в списке людей: выбранный заново в другой номинации
            // этот человек придёт уже с ним.
            setCandidates((prev) => Object.fromEntries(Object.entries(prev).map(([key, value]) => [key, {
                ...value,
                items: (value.items || []).map((person) => (person.id === entry.user_id ? { ...person, honor_photo: photo } : person)),
            }])));
            const current = entriesRef.current.findIndex((item) => item._key === task.key);
            if (current >= 0 && entriesRef.current[current].user_id === entry.user_id) edit(current, { photo_id: photo.id });
            closePhoto();
            notify?.('Фото сохранено — так сотрудник будет на доске', 'success');
        } catch (requestError) {
            setPhotoTask((prev) => (prev ? { ...prev, saving: false } : prev));
            notify?.(requestError?.response?.data?.error || 'Не удалось загрузить фото — попробуйте ещё раз', 'error');
        }
    }, [apiRoot, authHeaders, closePhoto, edit, notify]);

    const closeAfterSave = useCallback(async () => {
        const outcome = await flushRef.current();
        if (outcome === 'replaced') return;
        if (outcome === 'failed' && !window.confirm('Черновик не сохранился. Закрыть окно без сохранения?')) return;
        onClose?.();
    }, [onClose]);

    /* Закрытие — синхронно: на телефоне сюда приходит системный «назад», и стек
       жестов понимает только ответ false — «остаюсь» (обещание он принял бы за
       «закрылся» и потерял бы запись жеста). Со второго уровня («Сотрудник»,
       «Фото для доски») жест ведёт на уровень назад, как шеврон в шапке.
       Несохранённое — сперва досохраняем, окно закроется само. */
    const handleClose = useCallback(() => {
        if (isMobileShell && photoTask !== null) {
            if (!photoTask.saving) closePhoto();
            return false;
        }
        if (picker !== null && isMobileShell) {
            closePicker();
            return false;
        }
        if (busy || photoTask?.saving) return false;
        if (photoTask) closePhoto();
        if (dirtyRef.current || savingRef.current) {
            closeAfterSave();
            return false;
        }
        onClose?.();
        return undefined;
    }, [busy, closeAfterSave, closePhoto, closePicker, isMobileShell, onClose, photoTask, picker]);

    /* Подвести к полю: прокрутка тела окна к нему. Фокус — только если нажали
       клавиатурой: после щелчка мышью синее кольцо фокуса перекрыло бы красную
       рамку поля, а на телефоне фокус ещё и поднял бы клавиатуру. */
    const revealIssue = useCallback((list, position, focus = false) => {
        const item = list[position];
        const root = bodyRef.current?.closest('.otp-modal-panel') || document;
        const anchor = item && item.index !== null
            ? root.querySelector(`[data-honor-issue="${item.index}:${item.field}"]`)
            : root.querySelector('[data-honor-board-issue]');
        if (!anchor) return;
        anchor.scrollIntoView({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
        if (focus && !isMobileShell && typeof anchor.focus === 'function') anchor.focus({ preventScroll: true });
    }, [isMobileShell]);

    const sortedIssues = useMemo(() => sortIssues(issues), [issues]);

    const showIssues = useCallback((list, focus = false) => {
        setIssues(list);
        issueCursor.current = 0;
        // Поля краснеют в этом же кадре, подводим — когда разметка уже на месте.
        requestAnimationFrame(() => revealIssue(sortIssues(list), 0, focus));
    }, [revealIssue]);

    const stepIssue = useCallback((step, focus = false) => {
        if (!sortedIssues.length) return;
        const next = (issueCursor.current + step + sortedIssues.length) % sortedIssues.length;
        issueCursor.current = next;
        revealIssue(sortedIssues, next, focus);
    }, [revealIssue, sortedIssues]);

    const issueFor = useCallback((index, field) => (
        issues.find((item) => item.index === index && item.field === field)?.message || ''
    ), [issues]);

    const publish = useCallback(async (event) => {
        if (busy) return;
        // detail === 0 — кнопку нажали клавиатурой (Enter, пробел), а не мышью.
        const byKeyboard = event?.detail === 0;
        setBusy('publish');
        try {
            const outcome = await flushRef.current();
            if (outcome === 'failed') savingFailed();
            if (outcome !== 'saved') return;
            const local = publishIssues(entriesRef.current);
            if (local.length) {
                showIssues(local, byKeyboard);
                return;
            }
            const destination = targetRef.current;
            const response = await axios.post(`${apiRoot}/api/events/honor_board/publish`, {
                period: destination.period,
                department: destination.department,
                revision: revisionRef.current,
            }, { headers: authHeaders() });
            const view = response?.data?.board;
            if (sameTarget(view, targetRef.current)) {
                revisionRef.current = Number(view.revision) || 0;
                setBoard(view);
                updateMonth(view);
            }
            notify?.('Итоги опубликованы — их видят все сотрудники', 'success');
            onBoardChanged?.(destination);
        } catch (requestError) {
            const data = requestError?.response?.data;
            if (data?.code === 'HONOR_INCOMPLETE' && Array.isArray(data.issues)) {
                showIssues(data.issues, byKeyboard);
            } else if (requestError?.response?.status === 409 && data?.board) {
                replacedRef.current += 1;
                if (sameTarget(data.board, targetRef.current)) applyBoard(data);
                notify?.(data.error, 'error');
            } else {
                notify?.(data?.error || 'Не удалось опубликовать итоги', 'error');
            }
        } finally {
            setBusy('');
        }
    }, [apiRoot, applyBoard, authHeaders, busy, notify, onBoardChanged, savingFailed, showIssues, updateMonth]);

    const boardAction = useCallback(async (action) => {
        if (busy) return;
        const question = action === 'unpublish'
            ? 'Снять итоги с доски? Сотрудники перестанут их видеть, черновик останется.'
            : 'Вернуть опубликованную версию? Неопубликованные правки пропадут.';
        if (!window.confirm(question)) return;
        setBusy(action);
        try {
            const outcome = await flushRef.current();
            if (outcome === 'failed') savingFailed();
            if (outcome !== 'saved') return;
            const destination = targetRef.current;
            const response = await axios.post(`${apiRoot}/api/events/honor_board/${action}`, {
                period: destination.period,
                department: destination.department,
                revision: revisionRef.current,
            }, { headers: authHeaders() });
            if (sameTarget(response?.data?.board, targetRef.current)) applyBoard(response.data);
            if (action === 'unpublish') {
                notify?.('Итоги сняты с доски', 'success');
                onBoardChanged?.(destination);
            } else {
                notify?.('Черновик снова совпадает с опубликованным', 'success');
            }
        } catch (requestError) {
            const data = requestError?.response?.data;
            if (requestError?.response?.status === 409 && data?.board) {
                replacedRef.current += 1;
                if (sameTarget(data.board, targetRef.current)) applyBoard(data);
            }
            notify?.(data?.error || 'Не удалось выполнить действие', 'error');
        } finally {
            setBusy('');
        }
    }, [apiRoot, applyBoard, authHeaders, busy, notify, onBoardChanged, savingFailed]);

    const addNomination = useCallback(() => {
        const [entry] = withKeys([blankEntry(CUSTOM_NOMINATION)]);
        focusTitleOf.current = entry._key;
        setEntries((prev) => [...prev, entry]);
        markDirty();
    }, [markDirty]);

    useEffect(() => {
        const key = focusTitleOf.current;
        if (!key) return;
        const input = titleRefs.current[key];
        if (input) {
            focusTitleOf.current = null;
            input.scrollIntoView({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
            input.focus({ preventScroll: true });
        }
    }, [entries]);

    const removeNomination = useCallback((index) => {
        const entry = entriesRef.current[index];
        if (entryHasContent(entry) && !window.confirm('Убрать номинацию вместе с победителем и описанием?')) return;
        setEntries((prev) => prev.filter((_, position) => position !== index));
        // Номера строк сдвинулись — прежние отметки «не заполнено» указывали бы мимо.
        setIssues([]);
        markDirty();
    }, [markDirty]);

    const openPicker = useCallback((index) => {
        setPicker(index);
        setQuery('');
        loadCandidates(targetRef.current.department);
    }, [loadCandidates]);

    const pickPerson = useCallback((person) => {
        const index = picker;
        if (index === null) return;
        const current = entriesRef.current[index];
        setPeople((prev) => ({ ...prev, [String(person.id)]: person }));
        if (person.honor_photo?.id) setPhotos((prev) => ({ ...prev, [String(person.honor_photo.id)]: person.honor_photo }));
        if (current?.user_id !== person.id) {
            // Фото для доски, которое этому человеку уже загружали, — сразу на карточку.
            edit(index, { user_id: person.id, name: honorDisplayName(person.name), photo_id: person.honor_photo?.id || null });
        } else if (!current?.photo_id && person.honor_photo?.id) {
            // Тот же человек ещё раз — так возвращают его фото после «Убрать».
            edit(index, { photo_id: person.honor_photo.id });
        }
        setPicker(null);
        setQuery('');
    }, [edit, picker]);

    const clearPerson = useCallback(() => {
        const index = picker;
        if (index === null) return;
        const current = entriesRef.current[index];
        const person = people[String(current?.user_id)];
        // Имя, подставленное из профиля, уходит вместе с человеком; набранное тренером — остаётся.
        const autoName = person ? honorDisplayName(person.name) : '';
        edit(index, { user_id: null, photo_id: null, ...(current?.name === autoName ? { name: '' } : {}) });
        setPicker(null);
        setQuery('');
    }, [edit, people, picker]);

    const department = honorDepartment(departmentKey) || HONOR_DEPARTMENTS[0];
    // Переключатели показывают выбор сразу, даже пока прежняя доска досохраняется.
    const shownPeriod = pendingTarget?.period ?? period;
    const shownDepartment = pendingTarget?.department ?? departmentKey;
    const departmentOptions = useMemo(() => DEPARTMENT_OPTIONS.filter((option) => (
        !Array.isArray(departments) || departments.includes(option.value))), [departments]);
    const ready = loadState === 'ready' && sameTarget(board, { period, department: departmentKey });
    const state = board?.state || null;
    const pending = saveState === 'pending' || saveState === 'saving';
    const alreadyPublished = state === 'published' && !pending;
    // Правка опубликованной доски — уже «правки», ещё до ответа сохранения:
    // иначе подпись мигнула бы «Опубликовать» на время запроса.
    const editingPublished = state === 'changed' || (state === 'published' && pending);
    const monthOptions = useMemo(() => {
        // Месяц старше окна в 24 месяца (открыли с доски архив) — тоже в списке, первым.
        const list = months.some((month) => month.period === shownPeriod)
            ? months : [{ period: shownPeriod, states: {} }, ...months];
        return list.map((month) => ({
            value: month.period,
            label: periodLabel(month.period),
            meta: BOARD_STATE_LABELS[month.states?.[shownDepartment]] || '',
        }));
    }, [months, shownDepartment, shownPeriod]);
    const boardIssue = issues.find((item) => item.index === null)?.message || '';
    const slotsLeft = nominationSlotsLeft(entries);
    const avatars = useMemo(() => Object.fromEntries(Object.entries(people)
        .filter(([, person]) => person?.photo_url).map(([id, person]) => [id, person.photo_url])), [people]);

    // В подвале — только состояние доски; как работает черновик — под «i» рядом.
    let status = '';
    if (ready) {
        if (saveState === 'saving' || saveState === 'pending') status = 'Сохраняю черновик…';
        else if (saveState === 'error') status = 'Черновик не сохранился';
        else if (state === 'published') status = `Опубликовано ${formatMoment(board.published_at)}`;
        else if (state === 'changed') status = 'Есть неопубликованные правки';
        else if (state === 'draft') status = 'Черновик сохранён';
    }

    const menuItems = ready ? [
        state === 'changed' && {
            key: 'revert', label: 'Отменить правки', icon: RotateCcw, onSelect: () => boardAction('revert'),
        },
        (state === 'published' || state === 'changed') && {
            key: 'unpublish', label: 'Снять с публикации', icon: EyeOff, danger: true,
            onSelect: () => boardAction('unpublish'),
        },
    ] : [];

    const photoIndex = photoTask ? entries.findIndex((entry) => entry._key === photoTask.key) : -1;
    const photoEntry = photoIndex >= 0 ? entries[photoIndex] : null;
    // Второй уровень «Фото» открыт, только пока жива его строка доски.
    const photoOpen = Boolean(photoTask && photoEntry);
    const photoVariant = photoOpen ? (photoTask.mode === 'cutout' ? photoTask.cut : photoTask.asIs) : null;

    let footer = null;
    if (photoOpen) {
        footer = (
            <>
                <button
                    type="button"
                    className={`${iosBtnSecondary} mr-auto`}
                    onClick={() => openUpload(photoTask.key)}
                    disabled={photoTask.saving}
                >
                    <ImagePlus size={15} aria-hidden="true" />
                    Другое фото
                </button>
                <button
                    type="button"
                    className={iosBtnPrimary}
                    onClick={savePhoto}
                    disabled={photoTask.saving || photoVariant?.state !== 'ready' || !photoEntry?.user_id}
                >
                    {photoTask.saving && <Loader2 size={15} className="animate-spin" aria-hidden="true" />}
                    Сохранить фото
                </button>
            </>
        );
    } else if (picker === null) {
        footer = (
            <>
                <span className="mr-auto flex min-w-0 items-center gap-1.5 text-[12.5px] text-slate-500 max-sm:w-full">
                    {pending && <Loader2 size={13} className="shrink-0 animate-spin" aria-hidden="true" />}
                    {status && <span className="truncate" aria-live="polite">{status}</span>}
                    {saveState === 'error' && (
                        <button type="button" onClick={() => flushRef.current()} className="shrink-0 font-medium text-blue-600">
                            Повторить
                        </button>
                    )}
                    {ready && <InfoHint text={DRAFT_HINT} side="left" />}
                </span>
                <IssueNav count={sortedIssues.length} onStep={stepIssue} />
                <IosMenu items={menuItems} label="Ещё действия" />
                <button
                    type="button"
                    className={iosBtnPrimary}
                    onClick={publish}
                    disabled={!ready || Boolean(busy) || alreadyPublished}
                >
                    {busy === 'publish' ? <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                        : alreadyPublished ? <Check size={15} aria-hidden="true" /> : null}
                    {alreadyPublished ? 'Опубликовано' : editingPublished ? 'Опубликовать правки' : 'Опубликовать'}
                </button>
            </>
        );
    }

    const pickerEntry = picker !== null ? entries[picker] : null;
    const pickerMeta = pickerEntry ? nominationMeta(departmentKey, pickerEntry.nomination, pickerEntry.title, pickerEntry.icon) : null;
    const photoMeta = photoEntry ? nominationMeta(departmentKey, photoEntry.nomination, photoEntry.title, photoEntry.icon) : null;

    let title = 'Доска почёта';
    let subtitle = `${periodLabel(period)} · ${department.title}`;
    let onBack = null;
    let screen = 'board';
    if (photoOpen) {
        title = 'Фото для доски';
        subtitle = [photoEntry?.name || people[String(photoEntry?.user_id)]?.name, photoMeta?.title].filter(Boolean).join(' · ');
        onBack = photoTask.saving ? null : closePhoto;
        screen = 'photo';
    } else if (picker !== null) {
        title = 'Сотрудник';
        subtitle = [pickerMeta?.title || 'Новая номинация', department.label].join(' · ');
        onBack = closePicker;
        screen = 'picker';
    }

    return (
        <IosModal
            open={open}
            onClose={handleClose}
            onBack={onBack}
            title={title}
            subtitle={subtitle}
            maxWidth="max-w-2xl"
            footer={footer}
            sizeKey={`${screen}-${ready ? 'ready' : 'loading'}-${departmentKey}-${entries.length}`}
        >
            <div ref={bodyRef}>
                <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={onFileChosen}
                    tabIndex={-1}
                    aria-hidden="true"
                />
                {photoOpen ? (
                    <PhotoScreen
                        task={photoTask}
                        entry={photoEntry}
                        departmentKey={departmentKey}
                        place={boardPlace(entries, photoIndex)}
                        onMode={(mode) => setPhotoTask((prev) => (prev ? { ...prev, mode } : prev))}
                    />
                ) : picker !== null ? (
                    <PeoplePicker
                        state={candidates[departmentKey]}
                        query={query}
                        onQuery={setQuery}
                        selectedId={pickerEntry?.user_id || null}
                        onPick={pickPerson}
                        onClear={clearPerson}
                        onRetry={() => loadCandidates(departmentKey, { retry: true })}
                        autoFocus={!isMobileShell}
                    />
                ) : (
                    <div className="space-y-5">
                        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                            {/* Ведёт одно направление (глава Фронт-офиса) — переключать
                                нечего: оно уже названо в шапке окна. */}
                            {departmentOptions.length > 1 && (
                                <IosSegmented
                                    value={shownDepartment}
                                    options={departmentOptions}
                                    onChange={(key) => switchTo({ department: key })}
                                    ariaLabel="Направление"
                                    className="w-full sm:w-auto"
                                    stretch
                                    slide
                                />
                            )}
                            <CustomSelect
                                className="sm:ml-auto sm:w-[230px]"
                                variant="ios"
                                value={shownPeriod}
                                onChange={(value) => switchTo({ period: value })}
                                options={monthOptions}
                                ariaLabel="Отчётный месяц"
                            />
                        </div>
                        {loadState === 'error' ? (
                            <div className={`${iosCard} px-4 py-10 text-center text-[13.5px] text-slate-600`}>
                                {loadError}
                                <button
                                    type="button"
                                    onClick={() => loadBoardRef.current(period, departmentKey)}
                                    className="mt-3 block w-full font-medium text-blue-600"
                                >
                                    Повторить
                                </button>
                            </div>
                        ) : !ready ? (
                            <SlotsSkeleton count={1 + Math.max(1, department.presets.length)} />
                        ) : (
                            <div className="space-y-5 motion-safe:animate-fade-in-soft">
                                {boardIssue && (
                                    <p
                                        data-honor-board-issue
                                        className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-[13px] font-medium text-rose-700 ring-1 ring-rose-200"
                                    >
                                        {boardIssue}
                                    </p>
                                )}
                                {entries.map((entry, index) => (
                                    <SlotCard
                                        key={entry._key}
                                        entry={entry}
                                        index={index}
                                        place={boardPlace(entries, index)}
                                        departmentKey={departmentKey}
                                        person={people[String(entry.user_id)]}
                                        photo={entryPhoto(entry, photos, avatars)}
                                        issueFor={issueFor}
                                        onEdit={(patch) => edit(index, patch)}
                                        onBlur={saveOnBlur}
                                        onPick={() => openPicker(index)}
                                        onRemove={() => removeNomination(index)}
                                        onUpload={() => openUpload(entry._key)}
                                        onResetPhoto={() => edit(index, { photo_id: null })}
                                        titleRef={(node) => { titleRefs.current[entry._key] = node; }}
                                    />
                                ))}
                                {slotsLeft > 0 && (
                                    <button
                                        type="button"
                                        onClick={addNomination}
                                        className="flex w-full items-center justify-center gap-2 rounded-2xl border border-dashed border-slate-300 px-4 py-3 text-[14px] font-medium text-blue-600 transition hover:bg-slate-50 active:scale-[0.99]"
                                    >
                                        <Plus size={16} aria-hidden="true" />
                                        Добавить номинацию
                                        <span className="text-slate-400">· ещё {slotsLeft}</span>
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </IosModal>
    );
}
