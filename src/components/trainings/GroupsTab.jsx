import React, { useEffect, useMemo, useState } from 'react';
import { Users2, Plus, ChevronRight, UserRound } from 'lucide-react';
import { iosCard, iosBtnPrimary, IosBadge, IosHint } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { SearchField, ViewSwitcher, EmptyBlock, LoadingBlock, StatPair } from './pieces';
import SessionList from './SessionList';
import TrainingsCalendar from './TrainingsCalendar';
import {
    VIEW_CARDS, VIEW_ROWS, VIEW_CALENDAR, GROUP_VIEWS, NO_GROUP_KEY,
    buildGroupBuckets, durationMinutes, formatDuration, formatDayShort,
    pluralPeople, pluralSessions, tileTone, initials,
} from './constants';

/* Вкладка «По группам».
 *
 * Каскад: отдел → группа → сотрудник. Отдел спрашивается только у того, кто
 * видит больше одного: у СВ и главы отдела он один, и селектор с единственным
 * вариантом — это шум, а не выбор.
 *
 * Группа у занятия берётся из самой записи и посчитана сервером НА ДАТУ
 * ТРЕНИНГА, а не по текущему членству. Разница не теоретическая: 120 занятий
 * из 1648 принадлежат людям без открытого членства (в основном уволенным), и
 * по текущей группе они все свалились бы в «Без группы» — то есть за прошлые
 * месяцы раздел показывал бы не то, что было.
 *
 * «Без группы» — отдельная корзина, а не скрытые строки. На проде 87 занятий с
 * июня не накрыты членством ни на одну дату (задним числом оформленное
 * зачисление в Отделе продаж); прятать их значило бы показывать неполный месяц
 * и молчать об этом.
 */

export default function GroupsTab({
    month,
    trainings,
    loading,
    departments,
    showDepartmentPicker,
    view,
    onViewChange,
    canManage,
    onAddSession,
    onEditSession,
    onDeleteSession,
}) {
    const [departmentId, setDepartmentId] = useState('');
    const [groupKey, setGroupKey] = useState('');
    const [personId, setPersonId] = useState('');
    const [search, setSearch] = useState('');

    // Занятия отдела. Отдел у записи — отдел получателя, он приходит с сервера
    // вместе с занятием, поэтому фильтр не требует ни второго запроса, ни
    // склейки со списком пользователей.
    const departmentScoped = useMemo(() => {
        if (!departmentId) return trainings;
        return trainings.filter((item) => String(item?.operator_department_id ?? '') === departmentId);
    }, [trainings, departmentId]);

    const buckets = useMemo(() => buildGroupBuckets(departmentScoped), [departmentScoped]);

    // Выбранная группа исчезла из выборки (сменили месяц или отдел) — сбрасываем
    // выбор, иначе экран показывал бы «ничего» без объяснения причины.
    useEffect(() => {
        if (groupKey && !buckets.some((bucket) => bucket.key === groupKey)) {
            setGroupKey('');
            setPersonId('');
        }
    }, [buckets, groupKey]);

    const activeBucket = useMemo(
        () => buckets.find((bucket) => bucket.key === groupKey) || null,
        [buckets, groupKey],
    );

    useEffect(() => {
        if (personId && activeBucket && !activeBucket.people.some((p) => String(p.id) === personId)) {
            setPersonId('');
        }
    }, [activeBucket, personId]);

    const activePerson = useMemo(() => {
        if (!activeBucket || !personId) return null;
        return activeBucket.people.find((person) => String(person.id) === personId) || null;
    }, [activeBucket, personId]);

    const departmentOptions = useMemo(() => ([
        { value: '', label: 'Все отделы' },
        ...(departments || []).map((department) => ({
            value: String(department.id),
            label: department.name,
        })),
    ]), [departments]);

    const groupOptions = useMemo(() => ([
        { value: '', label: 'Все группы' },
        ...buckets.map((bucket) => ({
            value: bucket.key,
            label: `${bucket.name} · ${bucket.trainings.length}`,
        })),
    ]), [buckets]);

    const personOptions = useMemo(() => ([
        { value: '', label: 'Все сотрудники' },
        ...(activeBucket?.people || []).map((person) => ({
            value: String(person.id),
            label: `${person.name} · ${person.trainings.length}`,
        })),
    ]), [activeBucket]);

    /* Что в итоге показываем. Чем глубже спустились по каскаду, тем уже выборка.
     *
     * Поиск смотрит и на НАЗВАНИЕ ГРУППЫ, а не только на имя и тему. Названия
     * групп в портале строятся из имени супервайзера («Ешан Алмас группа
     * Основа»), и без этого поиск «Алмас» отвечал «Ничего не найдено», хотя
     * группа с таким названием на экране была. */
    const shown = useMemo(() => {
        const base = activePerson
            ? activePerson.trainings
            : activeBucket
                ? activeBucket.trainings
                : departmentScoped;
        const needle = search.trim().toLowerCase();
        if (!needle) return base;
        return base.filter((item) => (
            String(item?.operator_name || '').toLowerCase().includes(needle)
            || String(item?.reason || '').toLowerCase().includes(needle)
            || String(item?.group_name || '').toLowerCase().includes(needle)
        ));
    }, [activePerson, activeBucket, departmentScoped, search]);

    /* Совпало НАЗВАНИЕ группы — показываем группу целиком; совпало имя
     * человека — только его. Иначе выходила бессмыслица: названия групп в
     * портале строятся из имени супервайзера («Ешан Алмас группа Основа»),
     * поиск «Алмас» попадал в название, карточка оставалась, а список людей в
     * ней уже был отфильтрован по тому же слову и оказывался пустым — карточка
     * заявляла 28 занятий и не показывала ни одной строки. */
    const visibleBuckets = useMemo(() => {
        const needle = search.trim().toLowerCase();
        if (!needle) return buckets;
        return buckets
            .map((bucket) => {
                if (String(bucket.name).toLowerCase().includes(needle)) return bucket;
                return {
                    ...bucket,
                    people: bucket.people.filter(
                        (person) => String(person.name).toLowerCase().includes(needle),
                    ),
                };
            })
            .filter((bucket) => bucket.people.length > 0);
    }, [buckets, search]);

    const hasFilters = Boolean(departmentId || groupKey || personId || search.trim());

    // Карточки строятся из visibleBuckets, поэтому и «пусто ли» для них надо
    // спрашивать у них же. Раньше пустое состояние решалось по `shown`, и в
    // виде карточек экран мог сказать «ничего не найдено», имея непустой список
    // групп под руками.
    const cardsMode = view === VIEW_CARDS && !activePerson;
    const shownBuckets = useMemo(
        () => visibleBuckets.filter((bucket) => !groupKey || bucket.key === groupKey),
        [visibleBuckets, groupKey],
    );
    const nothingToShow = cardsMode ? shownBuckets.length === 0 : shown.length === 0;

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 px-1">
                {showDepartmentPicker && (
                    <CustomSelect
                        className="w-52"
                        variant="ios"
                        searchable
                        value={departmentId}
                        onChange={(value) => { setDepartmentId(value); setGroupKey(''); setPersonId(''); }}
                        options={departmentOptions}
                        placeholder="Все отделы"
                        searchPlaceholder="Название отдела"
                        ariaLabel="Отдел"
                    />
                )}

                <CustomSelect
                    className="w-56"
                    variant="ios"
                    searchable
                    value={groupKey}
                    onChange={(value) => { setGroupKey(value); setPersonId(''); }}
                    options={groupOptions}
                    placeholder="Все группы"
                    searchPlaceholder="Название группы"
                    ariaLabel="Группа"
                />

                {/* Сотрудник выбирается только после группы: список «все люди
                    портала» в этом месте ничего не отвечает. */}
                {activeBucket && activeBucket.people.length > 1 && (
                    <CustomSelect
                        className="w-56"
                        variant="ios"
                        searchable
                        value={personId}
                        onChange={setPersonId}
                        options={personOptions}
                        placeholder="Все сотрудники"
                        searchPlaceholder="Имя сотрудника"
                        ariaLabel="Сотрудник"
                    />
                )}

                <SearchField value={search} onChange={setSearch} placeholder="Имя или тема" />
                <ViewSwitcher value={view} onChange={onViewChange} views={GROUP_VIEWS} />

                {canManage && (
                    <button type="button" onClick={onAddSession} className={iosBtnPrimary}>
                        <Plus size={14} /> Занятие
                    </button>
                )}
            </div>

            {loading && <LoadingBlock />}

            {!loading && nothingToShow && (
                <EmptyBlock
                    icon={Users2}
                    title={hasFilters ? 'Ничего не найдено' : 'В этом месяце тренингов не было'}
                    text={hasFilters
                        ? 'Попробуйте выбрать другую группу или снять поиск.'
                        : 'Проведите занятие — оно появится здесь в группе сотрудника.'}
                />
            )}

            {/* Календарь показывает выбранную выборку целиком: он отвечает на
                «что было в этот день», и сужение отделом или группой для него
                так же осмысленно, как для списка. */}
            {!loading && !nothingToShow && view === VIEW_CALENDAR && (
                <TrainingsCalendar
                    month={month}
                    sessions={shown}
                    canManage={canManage}
                    onEdit={onEditSession}
                    onDelete={onDeleteSession}
                />
            )}

            {/* Раскрыт конкретный сотрудник — показываем его занятия списком,
                вид карточек/строк здесь уже ничего не различает. */}
            {!loading && !nothingToShow && view !== VIEW_CALENDAR && activePerson && (
                <div className="space-y-3">
                    <PersonHeader person={activePerson} groupName={activeBucket?.name} />
                    <SessionList
                        sessions={shown}
                        showPerson={false}
                        showTopic
                        canManage={canManage}
                        onEdit={onEditSession}
                        onDelete={onDeleteSession}
                    />
                </div>
            )}

            {!loading && !nothingToShow && cardsMode && (
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                    {shownBuckets
                        .map((bucket) => (
                            <GroupCard
                                key={bucket.key}
                                bucket={bucket}
                                onPickPerson={(id) => { setGroupKey(bucket.key); setPersonId(String(id)); }}
                            />
                        ))}
                </div>
            )}

            {!loading && !nothingToShow && view === VIEW_ROWS && !activePerson && (
                <SessionList
                    sessions={shown}
                    showPerson
                    showTopic
                    canManage={canManage}
                    onEdit={onEditSession}
                    onDelete={onDeleteSession}
                />
            )}
        </div>
    );
}

function PersonHeader({ person, groupName }) {
    const minutes = person.trainings.reduce((acc, item) => acc + durationMinutes(item), 0);
    const counted = person.trainings.filter((item) => item.count_in_hours !== false).length;

    return (
        <div className={`${iosCard} flex flex-wrap items-center gap-x-6 gap-y-3 p-4`}>
            <div className="flex min-w-0 items-center gap-3">
                <span className={`grid h-[38px] w-[38px] shrink-0 place-items-center rounded-[12px] text-[13px] font-semibold ring-1 ${tileTone(person.name)}`}>
                    {initials(person.name)}
                </span>
                <div className="min-w-0">
                    <div className="truncate text-[14px] font-semibold text-slate-900">{person.name}</div>
                    <div className="truncate text-[11.5px] text-slate-400">{groupName || 'Без группы'}</div>
                </div>
                {person.status === 'fired' && <IosBadge tone="amber" className="!py-0 !text-[10px]">уволен</IosBadge>}
                {person.status === 'bs' && <IosBadge className="!py-0 !text-[10px]">БС</IosBadge>}
            </div>

            <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                <StatPair label="Занятий" value={person.trainings.length} />
                <StatPair label="Время" value={formatDuration(minutes)} />
                <StatPair
                    label="В часах"
                    value={`${counted} из ${person.trainings.length}`}
                    hint="Сколько занятий учтено в оплачиваемых часах"
                />
            </div>
        </div>
    );
}

function GroupCard({ bucket, onPickPerson }) {
    const [expanded, setExpanded] = useState(false);
    const people = expanded ? bucket.people : bucket.people.slice(0, 5);
    const isNoGroup = bucket.key === NO_GROUP_KEY;

    return (
        <div className={`${iosCard} flex flex-col overflow-hidden`}>
            <div className="flex items-start gap-3 p-4 pb-3">
                <span
                    className={`grid h-[38px] w-[38px] shrink-0 place-items-center rounded-[12px] text-[13px] font-semibold ring-1 ${
                        isNoGroup ? 'bg-slate-100 text-slate-400 ring-slate-200' : tileTone(bucket.name)
                    }`}
                    aria-hidden="true"
                >
                    {isNoGroup ? '—' : initials(bucket.name)}
                </span>

                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                        <span className="truncate text-[14px] font-semibold text-slate-900">{bucket.name}</span>
                        {isNoGroup && (
                            <IosHint
                                text="Сотрудник не был зачислен ни в одну группу на дату занятия. Чаще всего это зачисление, оформленное задним числом: занятие уже было, а членство начинается позже."
                            />
                        )}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11.5px] text-slate-500">
                        <span className="tabular-nums">
                            {bucket.trainings.length} {pluralSessions(bucket.trainings.length)}
                        </span>
                        <span className="text-slate-300">·</span>
                        <span className="tabular-nums">
                            {bucket.people.length} {pluralPeople(bucket.people.length)}
                        </span>
                        <span className="text-slate-300">·</span>
                        <span className="tabular-nums">{formatDuration(bucket.minutes)}</span>
                    </div>
                </div>
            </div>

            <div className="divide-y divide-slate-100 border-t border-slate-100">
                {people.map((person) => {
                    const last = person.trainings.reduce(
                        (acc, item) => (!acc || String(item.date) > acc ? item.date : acc), null);
                    return (
                        <button
                            key={person.id}
                            type="button"
                            onClick={() => onPickPerson(person.id)}
                            className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50"
                        >
                            <UserRound size={14} className="shrink-0 text-slate-300" />
                            <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-slate-800">
                                {person.name}
                            </span>
                            {person.status === 'fired' && (
                                <IosBadge tone="amber" className="!py-0 shrink-0 !text-[10px]">уволен</IosBadge>
                            )}
                            <span className="shrink-0 text-[11.5px] tabular-nums text-slate-400">
                                {person.trainings.length} · {last ? formatDayShort(last) : '—'}
                            </span>
                            <ChevronRight size={14} className="shrink-0 text-slate-300" />
                        </button>
                    );
                })}
            </div>

            {bucket.people.length > 5 && (
                <button
                    type="button"
                    onClick={() => setExpanded((prev) => !prev)}
                    className="border-t border-slate-100 py-2.5 text-[12.5px] font-semibold text-slate-500 transition hover:bg-slate-50"
                >
                    {expanded ? 'Свернуть' : `Ещё ${bucket.people.length - 5}`}
                </button>
            )}
        </div>
    );
}
