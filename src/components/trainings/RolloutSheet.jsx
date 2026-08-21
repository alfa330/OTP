import React, { useEffect, useMemo, useState } from 'react';
import { Check, Loader2, Users, CalendarCheck2, Clock } from 'lucide-react';
import FullscreenSheet from '../common/FullscreenSheet';
import { iosCard, iosInput, iosBtnPrimary, iosBtnSecondary, iosGroupLabel, IosBadge, IosHint } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { SearchField, CoverageBar, EmptyBlock, LoadingBlock, ErrorBlock } from './pieces';
import {
    formatDayLong, formatDuration, durationMinutes, pluralPeople, plural, errText,
} from './constants';

/* Раскатка корпоративной темы пачками.
 *
 * Смысл экрана — чек-лист: слева список всех, кому тему надо провести, справо
 * отмеченные уходят в одно занятие. «Всем сразу» провести нельзя физически,
 * поэтому раскатка идёт волнами, и главный вопрос экрана — «кто ещё остался».
 * Поэтому список по умолчанию показывает именно НЕПРОЙДЕННЫХ, а не всех.
 *
 * Отдельной таблицы назначений под это нет и не нужно: «провели» — это обычная
 * запись в trainings с этой темой. Так охват считается по факту проведённого, а
 * не по галочке в отдельном реестре, и не возникает второй правды о том, кому
 * тренинг провели.
 */

const SCOPE_REMAINING = 'remaining';
const SCOPE_DONE = 'done';
const SCOPE_ALL = 'all';

/* Своя галочка: примитива чекбокса в ui/ нет вовсе, а нативный `<input
 * type=checkbox>` в этой типографике выглядит деталью из другой программы. */
const Tick = ({ checked }) => (
    <span
        aria-hidden="true"
        className={`grid h-[20px] w-[20px] shrink-0 place-items-center rounded-[6px] transition ${
            checked ? 'bg-blue-600 text-white' : 'bg-white ring-1 ring-slate-300'
        }`}
    >
        {checked && <Check size={13} strokeWidth={3} />}
    </span>
);

export default function RolloutSheet({
    open,
    onClose,
    topic,
    audience = null,
    loading = false,
    loadError = '',
    onReload,
    onSubmit,
}) {
    const [scope, setScope] = useState(SCOPE_REMAINING);
    const [search, setSearch] = useState('');
    const [groupFilter, setGroupFilter] = useState('');
    const [picked, setPicked] = useState(() => new Set());
    const [date, setDate] = useState('');
    const [startTime, setStartTime] = useState('');
    const [endTime, setEndTime] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!open) return;
        setScope(SCOPE_REMAINING);
        setSearch('');
        setGroupFilter('');
        setPicked(new Set());
        setDate(new Date().toISOString().slice(0, 10));
        setStartTime('');
        setEndTime('');
        setError('');
    }, [open, topic?.id]);

    const people = useMemo(() => audience?.audience || [], [audience]);

    const groupOptions = useMemo(() => {
        const names = new Map();
        people.forEach((person) => {
            const name = person.group_name || 'Без группы';
            names.set(name, (names.get(name) || 0) + 1);
        });
        return [
            { value: '', label: 'Все группы' },
            ...Array.from(names.entries())
                .sort((a, b) => String(a[0]).localeCompare(String(b[0]), 'ru', { sensitivity: 'base' }))
                .map(([name, count]) => ({ value: name, label: `${name} · ${count}` })),
        ];
    }, [people]);

    const visible = useMemo(() => {
        const needle = search.trim().toLowerCase();
        return people.filter((person) => {
            if (scope === SCOPE_REMAINING && person.covered) return false;
            if (scope === SCOPE_DONE && !person.covered) return false;
            if (groupFilter && (person.group_name || 'Без группы') !== groupFilter) return false;
            if (needle && !String(person.name || '').toLowerCase().includes(needle)) return false;
            return true;
        });
    }, [people, scope, groupFilter, search]);

    const selectable = useMemo(() => visible.filter((person) => !person.covered), [visible]);
    const pickedCount = picked.size;
    const minutes = durationMinutes({ start_time: startTime, end_time: endTime });

    const covered = audience?.covered_count ?? 0;
    const total = audience?.audience_count ?? 0;
    const remaining = audience?.remaining_count ?? 0;

    const toggle = (id) => {
        setPicked((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    };

    const allVisiblePicked = selectable.length > 0 && selectable.every((person) => picked.has(person.id));

    const toggleAllVisible = () => {
        setPicked((prev) => {
            const next = new Set(prev);
            if (allVisiblePicked) selectable.forEach((person) => next.delete(person.id));
            else selectable.forEach((person) => next.add(person.id));
            return next;
        });
    };

    const submit = async () => {
        if (pickedCount === 0) { setError('Отметьте, кому проводите.'); return; }
        if (!date) { setError('Укажите дату занятия.'); return; }
        if (!startTime || !endTime) { setError('Укажите время начала и окончания.'); return; }
        if (minutes <= 0) { setError('Время окончания должно быть позже начала.'); return; }
        setError('');
        setSaving(true);
        try {
            await onSubmit({
                topic_id: topic.id,
                operator_ids: Array.from(picked),
                date,
                start_time: startTime,
                end_time: endTime,
            });
            setSaving(false);
            onClose();
        } catch (submitError) {
            setError(errText(submitError, 'Не удалось записать занятие.'));
            setSaving(false);
        }
    };

    if (!open || !topic) return null;

    return (
        <FullscreenSheet
            open
            wide
            icon="fa-chalkboard-teacher"
            title={topic.title}
            subtitle={total > 0
                ? `Проведён ${covered} из ${total} ${pluralPeople(total)} · осталось ${remaining}`
                : 'Аудитория пуста'}
            onClose={onClose}
            actions={(
                <div className="flex items-center gap-2">
                    <button type="button" onClick={onClose} className={iosBtnSecondary}>Закрыть</button>
                    <button type="button" onClick={submit} disabled={saving || pickedCount === 0} className={iosBtnPrimary}>
                        {saving ? <Loader2 size={14} className="animate-spin" /> : <CalendarCheck2 size={14} />}
                        {saving
                            ? 'Записываем…'
                            : `Провести ${pickedCount > 0 ? `${pickedCount} ${pluralPeople(pickedCount)}` : 'пачке'}`}
                    </button>
                </div>
            )}
        >
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
                {/* ── Список аудитории ─────────────────────────────────────── */}
                <div className="min-w-0 space-y-3">
                    <div className="flex flex-wrap items-center gap-2 px-1">
                        <div className="flex rounded-xl bg-slate-100 p-1">
                            {[
                                { key: SCOPE_REMAINING, label: 'Осталось', count: remaining },
                                { key: SCOPE_DONE, label: 'Проведено', count: covered },
                                { key: SCOPE_ALL, label: 'Все', count: total },
                            ].map((item) => (
                                <button
                                    key={item.key}
                                    type="button"
                                    onClick={() => setScope(item.key)}
                                    className={`flex items-center gap-1.5 whitespace-nowrap rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                                        scope === item.key
                                            ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                            : 'text-slate-500 hover:text-slate-700'
                                    }`}
                                >
                                    {item.label}
                                    <span className="tabular-nums text-slate-400">{item.count}</span>
                                </button>
                            ))}
                        </div>
                        {groupOptions.length > 2 && (
                            <CustomSelect
                                className="w-44"
                                variant="ios"
                                searchable
                                value={groupFilter}
                                onChange={setGroupFilter}
                                options={groupOptions}
                                placeholder="Все группы"
                                searchPlaceholder="Название группы"
                                ariaLabel="Группа"
                            />
                        )}
                        <SearchField value={search} onChange={setSearch} placeholder="Имя сотрудника" />
                    </div>

                    {loading && <LoadingBlock label="Считаем охват…" />}
                    {!loading && loadError && <ErrorBlock text={loadError} onRetry={onReload} />}

                    {!loading && !loadError && visible.length === 0 && (
                        <EmptyBlock
                            icon={Users}
                            title={scope === SCOPE_REMAINING && !search && !groupFilter
                                ? 'Тему прошли все'
                                : 'Никого не нашлось'}
                            text={scope === SCOPE_REMAINING && !search && !groupFilter
                                ? 'В этом отделе не осталось сотрудников, которым тему ещё не проводили.'
                                : 'Попробуйте снять фильтр или изменить поиск.'}
                        />
                    )}

                    {!loading && !loadError && visible.length > 0 && (
                        <div className={`${iosCard} overflow-hidden`}>
                            <button
                                type="button"
                                onClick={toggleAllVisible}
                                disabled={selectable.length === 0}
                                className="flex w-full items-center gap-3 border-b border-slate-100 px-3.5 py-2.5 text-left transition hover:bg-slate-50 disabled:opacity-40 disabled:hover:bg-white"
                            >
                                <Tick checked={allVisiblePicked} />
                                <span className="text-[12.5px] font-semibold text-slate-600">
                                    {allVisiblePicked ? 'Снять отметки' : 'Отметить всех в списке'}
                                </span>
                                <span className="ml-auto text-[11.5px] tabular-nums text-slate-400">
                                    {selectable.length} доступно
                                </span>
                            </button>

                            <div className="max-h-[52vh] divide-y divide-slate-100 overflow-y-auto overscroll-contain">
                                {visible.map((person) => {
                                    const checked = picked.has(person.id);
                                    return (
                                        <button
                                            key={person.id}
                                            type="button"
                                            disabled={person.covered}
                                            onClick={() => toggle(person.id)}
                                            className={`flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition ${
                                                person.covered
                                                    ? 'cursor-default'
                                                    : checked ? 'bg-blue-50/60 hover:bg-blue-50' : 'hover:bg-slate-50'
                                            }`}
                                        >
                                            {person.covered
                                                ? (
                                                    <span className="grid h-[20px] w-[20px] shrink-0 place-items-center rounded-[6px] bg-emerald-50 text-emerald-600 ring-1 ring-emerald-100">
                                                        <Check size={13} strokeWidth={3} />
                                                    </span>
                                                )
                                                : <Tick checked={checked} />}

                                            <span className="min-w-0 flex-1">
                                                <span className="block truncate text-[13.5px] font-medium text-slate-800">
                                                    {person.name}
                                                </span>
                                                {/* Только группа. СВ рядом не пишем: названия
                                                    групп в портале и так строятся из имени
                                                    супервайзера («Ешан Алмас группа Основа»), и
                                                    вторая копия того же имени — чистый шум. */}
                                                <span className="block truncate text-[11.5px] text-slate-400">
                                                    {person.group_name || 'Без группы'}
                                                </span>
                                            </span>

                                            {person.covered && person.last_date && (
                                                <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
                                                    {formatDayLong(person.last_date)}
                                                </span>
                                            )}
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </div>

                {/* ── Когда проводим ───────────────────────────────────────── */}
                <div className="space-y-3">
                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Охват темы</div>
                        <div className={`${iosCard} space-y-3 p-4`}>
                            <div className="flex items-baseline gap-2">
                                <span className="text-[26px] font-semibold leading-none tabular-nums text-slate-900">
                                    {covered}
                                </span>
                                <span className="text-[13px] text-slate-400">
                                    из {total} {pluralPeople(total)}
                                </span>
                            </div>
                            <CoverageBar covered={covered} audience={total} />
                            <div className="flex items-center justify-between text-[12px]">
                                <span className="text-slate-500">Осталось</span>
                                <span className="font-semibold tabular-nums text-slate-900">{remaining}</span>
                            </div>
                            <div className="flex items-center gap-2 pt-1">
                                <IosBadge tone="blue">Корпоративная</IosBadge>
                                <span className="text-[11px] text-slate-400">в часы не идёт</span>
                                <IosHint
                                    align="right"
                                    text="Информационная тема записывается как факт прохождения: в оплачиваемые часы и в расчёт зарплаты она не попадает."
                                />
                            </div>
                        </div>
                    </section>

                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Когда проводим</div>
                        <div className={`${iosCard} space-y-3 p-4`}>
                            <div>
                                <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Дата</label>
                                <input
                                    type="date"
                                    value={date}
                                    max={new Date().toISOString().slice(0, 10)}
                                    onChange={(event) => setDate(event.target.value)}
                                    className={`${iosInput} bg-white ring-1 ring-slate-200/70`}
                                />
                            </div>
                            <div className="grid grid-cols-2 gap-2">
                                <div>
                                    <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Начало</label>
                                    <input
                                        type="time"
                                        value={startTime}
                                        onChange={(event) => setStartTime(event.target.value)}
                                        className={`${iosInput} bg-white ring-1 ring-slate-200/70`}
                                    />
                                </div>
                                <div>
                                    <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Окончание</label>
                                    <input
                                        type="time"
                                        value={endTime}
                                        onChange={(event) => setEndTime(event.target.value)}
                                        className={`${iosInput} bg-white ring-1 ring-slate-200/70`}
                                    />
                                </div>
                            </div>
                            <div className="flex items-center gap-2 rounded-xl bg-slate-100 px-3 py-2.5">
                                <Clock size={14} className="shrink-0 text-slate-400" />
                                <span className="text-[13px] font-semibold tabular-nums text-slate-700">
                                    {minutes > 0 ? formatDuration(minutes) : 'Время не указано'}
                                </span>
                            </div>
                            <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                                Время у всей пачки общее — так занятие и проходит. Кому нужно другое
                                время, проведите отдельной пачкой.
                            </p>
                        </div>
                    </section>

                    {error && (
                        <div className="rounded-xl bg-rose-50 px-3 py-2.5 text-[12px] leading-snug text-rose-700 ring-1 ring-rose-100">
                            {error}
                        </div>
                    )}

                    {pickedCount > 0 && (
                        <div className="px-1 text-[12px] text-slate-500">
                            Отмечено {pickedCount} {pluralPeople(pickedCount)} — будет записано{' '}
                            {pickedCount} {plural(pickedCount, 'занятие', 'занятия', 'занятий')}.
                        </div>
                    )}
                </div>
            </div>
        </FullscreenSheet>
    );
}
