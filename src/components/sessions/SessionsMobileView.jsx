import React from 'react';
import MobileActionSheet from '../common/MobileActionSheet';
import { roleTitle } from '../news/newsShared';
import { lastSeenLabel, plural, personWord, roleLabel, sessionWord, sessionWordAcc } from './userAgent';
import './sessions-mobile.css';

/**
 * Раздел «Сессии» на телефоне: список людей вместо таблицы.
 *
 * Настольная таблица на семь колонок на экране в 390 px ездила вбок внутри
 * страницы, три плитки ролей повторяли полосу вкладок тех же ролей, а
 * «Прервать все», проявленная без наведения, стояла в каждой строке. Здесь
 * всё это приведено к виду списка в приложении:
 *   • строка — человек: имя, когда был в сети, роль и число сессий, а если у
 *     него открыты чувствительные данные — строкой ниже, янтарным;
 *   • прервать сессии одного человека — в его карточке (она на телефоне и так
 *     экран), а не красной кнопкой в каждой строке, где её легко задеть при
 *     прокрутке;
 *   • прервать сразу у нескольких — режимом «Выбрать», как в «Фото»;
 *   • подтверждение и сортировка — листами снизу, а не окнами посреди экрана.
 *
 * Состояние (поиск, фильтры, выбор, загрузка) живёт в SessionsPanel в App.jsx
 * и общее с компьютером — здесь только раскладка. Поэтому переход телефона в
 * альбомную ориентацию и обратно ничего не теряет.
 */

const ROLE_OPTIONS = [
    { value: 'all', label: 'Все' },
    { value: 'admin', label: 'Админы' },
    { value: 'sv', label: 'СВ' },
    { value: 'operator', label: 'Операторы' },
];

/* Порядок выбирается целиком — ключ вместе с направлением. У таблицы щелчок
   по заголовку перещёлкивает направление, но в листе «По имени» от Я к А
   никто не ищет. */
const SORT_OPTIONS = [
    { sort: 'last_seen_at', dir: 'desc', label: 'Недавняя активность', short: 'Активность' },
    { sort: 'user_name', dir: 'asc', label: 'По имени', short: 'Имя' },
    { sort: 'sessions_count', dir: 'desc', label: 'Больше всего сессий', short: 'Сессии' },
    { sort: 'sensitive', dir: 'desc', label: 'Сначала открытые данные', short: 'Доступ' },
];

/* Подпись роли в строке. Словарь раздела знает только три роли фильтра, и
   тренер показывался бы как «trainer» — остальные берём из общего словаря
   должностей. */
const rowRoleLabel = (role) => {
    if (role === 'admin' || role === 'super_admin' || role === 'sv' || role === 'operator') return roleLabel(role);
    const title = roleTitle(role);
    return title ? title.charAt(0).toUpperCase() + title.slice(1) : (role || '—');
};

const CheckGlyph = ({ className }) => (
    <svg className={className} viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="M3.5 8.4l2.9 2.9L12.6 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

const PersonRow = React.memo(function PersonRow({ person, now, selecting, isSelected, disabled, onOpen, onToggle, Avatar }) {
    const name = person.user_name || `#${person.user_id}`;
    const sessions = Number(person.sessions_count || 0);
    const opened = Number(person.sensitive_open_count || 0);
    return (
        <li className="ses-m-item">
            <button
                type="button"
                className="ses-m-row"
                onClick={() => (selecting ? onToggle(person) : onOpen(person))}
                disabled={selecting && disabled}
                aria-pressed={selecting ? isSelected : undefined}
            >
                {selecting && (
                    <span className={`ses-m-check${isSelected ? ' is-on' : ''}`} aria-hidden="true">
                        <CheckGlyph />
                    </span>
                )}
                <span className="ses-m-avatar" aria-hidden="true">
                    {person.avatar_url
                        ? <Avatar src={person.avatar_url} alt="" className="ses-m-avatar-img" />
                        : name.charAt(0).toUpperCase()}
                </span>
                <span className="ses-m-main">
                    <span className="ses-m-line">
                        <span className="ses-m-name">{name}</span>
                        <span className="ses-m-time">{lastSeenLabel(person.last_seen_at, now)}</span>
                    </span>
                    <span className="ses-m-meta">
                        {rowRoleLabel(person.user_role)} · {sessions} {sessionWord(sessions)}
                    </span>
                    {/* Открытые данные — единственное, что в строке окрашено:
                        ради этого в раздел и заходят. Закрытые — норма, про
                        них строка молчит. */}
                    {opened > 0 && (
                        <span className="ses-m-warn text-amber-700">
                            Данные открыты{opened > 1 ? ` · ${opened}` : ''}
                            {person.sensitive_last_granted_by_name ? ` · выдал ${person.sensitive_last_granted_by_name}` : ''}
                        </span>
                    )}
                </span>
                {!selecting && <span className="ses-m-chevron" aria-hidden="true" />}
            </button>
        </li>
    );
});

export default function SessionsMobileView({
    people,
    totalPeople,
    totalSessions,
    matchedPeople,
    sensitivePeople,
    deviceCounts,
    deviceOrder,
    deviceLabels,
    DeviceIcon,
    Avatar,
    search,
    onSearch,
    roleFilter,
    onRole,
    deviceFilter,
    onDevice,
    sortKey,
    sortDir,
    onSort,
    hasFilters,
    onResetFilters,
    isLoading,
    isLoadingMore,
    hasMore,
    onLoadMore,
    onRefresh,
    onOpen,
    selected,
    onToggle,
    onToggleAll,
    onClearSelection,
    allSelected,
    selectedSessionsCount,
    bulkRevoking,
    onBulkRevoke,
}) {
    const [selecting, setSelecting] = React.useState(false);
    const [sortOpen, setSortOpen] = React.useState(false);
    const [confirmOpen, setConfirmOpen] = React.useState(false);
    const sentinelRef = React.useRef(null);
    const revokeAskedRef = React.useRef(false);

    /* Одно «сейчас» на страницу списка: строки не пересчитывают его каждая
       сама, и memo строки не срывается на каждой букве поиска. */
    const now = React.useMemo(() => new Date(), [people]);

    const currentSort = SORT_OPTIONS.find((option) => option.sort === sortKey) || SORT_OPTIONS[0];

    /* Метки устройств нужны, только когда есть из чего выбирать. Если все
       сидят с компьютера, метка «ПК 11» отбирает ровно тех же, кто и так в
       списке, — это шум. Выбранную метку оставляем всегда: иначе снять фильтр
       было бы нечем. */
    const shownDevices = deviceOrder.filter((key) => (deviceCounts[key] || 0) > 0 || key === deviceFilter);
    const showDevices = deviceFilter !== 'all'
        || deviceOrder.filter((key) => (deviceCounts[key] || 0) > 0).length > 1;

    const toggleSelecting = () => {
        if (selecting) onClearSelection();
        setSelecting((prev) => !prev);
    };

    /* Прервали — выходим из режима выбора, как «Фото» после удаления. Но
       только если выбор действительно снят: на ошибке SessionsPanel его
       сохраняет, и человек должен остаться со своими отметками. */
    React.useEffect(() => {
        if (bulkRevoking || !revokeAskedRef.current) return;
        revokeAskedRef.current = false;
        if (selected.size === 0) setSelecting(false);
    }, [bulkRevoking, selected]);

    const confirmRevoke = () => {
        setConfirmOpen(false);
        revokeAskedRef.current = true;
        onBulkRevoke();
    };

    /* Подгрузка следующей страницы. Своя, а не из SessionsPanel: этот
       компонент ленивый, и к моменту, когда появляется его «хвост», эффект
       панели уже отработал с пустой ссылкой. Прокрутка на телефоне — у
       страницы, поэтому root не задаём. */
    React.useEffect(() => {
        if (typeof IntersectionObserver === 'undefined') return undefined;
        if (!hasMore || isLoading || isLoadingMore) return undefined;
        const target = sentinelRef.current;
        if (!target) return undefined;
        const observer = new IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting)) onLoadMore();
        }, { rootMargin: '400px 0px', threshold: 0.01 });
        observer.observe(target);
        return () => observer.disconnect();
    }, [hasMore, isLoading, isLoadingMore, onLoadMore]);

    const query = search.trim();
    const nobodyAtAll = totalPeople === 0 && !hasFilters && !isLoading;

    return (
        <div className={`ses-m${selecting ? ' is-selecting' : ''}`}>
            <div className="ses-m-head">
                <div className="ses-m-title" role="heading" aria-level={1}>Сессии</div>
                {(people.length > 0 || selecting) && (
                    <button type="button" className="ses-m-link" onClick={toggleSelecting} disabled={bulkRevoking}>
                        {selecting ? 'Готово' : 'Выбрать'}
                    </button>
                )}
                <button type="button" className="ses-m-round" onClick={onRefresh} disabled={isLoading} aria-label="Обновить">
                    <svg className={isLoading ? 'animate-spin' : undefined} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                </button>
            </div>

            {totalPeople > 0 && (
                /* Части сводки не рвутся посередине: «1 с открытыми / данными»
                   на второй строке читалось как обрывок. Переносится только
                   целая часть. */
                <p className="ses-m-sub">
                    <span>{totalPeople} {personWord(totalPeople)}</span>
                    {' · '}
                    <span>{totalSessions} {sessionWord(totalSessions)}</span>
                    {sensitivePeople > 0 && (
                        <>
                            {' · '}
                            <span className="text-amber-700">{sensitivePeople} с доступом</span>
                        </>
                    )}
                </p>
            )}

            <div className="ses-m-search">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
                </svg>
                <input
                    type="search"
                    value={search}
                    onChange={(event) => onSearch(event.target.value)}
                    /* «Найти» на клавиатуре прячет её: запрос и так уходит сам
                       через полсекунды после последней буквы. */
                    onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
                    placeholder="Имя, логин или IP"
                    enterKeyHint="search"
                    autoComplete="off"
                    autoCorrect="off"
                    autoCapitalize="off"
                    spellCheck={false}
                    aria-label="Поиск по сессиям"
                />
                {search && (
                    <button type="button" className="ses-m-search-clear" onClick={() => onSearch('')} aria-label="Очистить поиск">
                        <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM7.47 7.47a.75.75 0 011.06 0L10 8.94l1.47-1.47a.75.75 0 111.06 1.06L11.06 10l1.47 1.47a.75.75 0 11-1.06 1.06L10 11.06l-1.47 1.47a.75.75 0 01-1.06-1.06L8.94 10 7.47 8.53a.75.75 0 010-1.06z" clipRule="evenodd" />
                        </svg>
                    </button>
                )}
            </div>

            {/* Роли — одним переключателем. Плиток с теми же ролями, как на
                компьютере, здесь нет: два ряда кнопок про одно и то же. */}
            <div className="ses-m-seg" role="tablist" aria-label="Роль">
                {ROLE_OPTIONS.map((option) => {
                    const active = roleFilter === option.value;
                    return (
                        <button
                            key={option.value}
                            type="button"
                            role="tab"
                            aria-selected={active}
                            className={`ses-m-seg-btn${active ? ' is-on' : ''}`}
                            onClick={() => { if (!active) onRole(option.value); }}
                        >
                            {option.label}
                        </button>
                    );
                })}
            </div>

            {showDevices && (
                <div className="ses-m-chips" role="group" aria-label="Устройства">
                    {shownDevices.map((key) => {
                        const active = deviceFilter === key;
                        return (
                            <button
                                key={key}
                                type="button"
                                className={`ses-m-chip${active ? ' is-on' : ''}`}
                                aria-pressed={active}
                                onClick={() => onDevice(active ? 'all' : key)}
                            >
                                <DeviceIcon type={key} className="ses-m-ico" />
                                {deviceLabels[key]}
                                <span className="ses-m-chip-count">{deviceCounts[key] || 0}</span>
                            </button>
                        );
                    })}
                </div>
            )}

            {nobodyAtAll ? (
                <div className="ses-m-empty">Активных сессий нет</div>
            ) : (
                <>
                    <div className="ses-m-group-head">
                        <span className="ses-m-group-title">
                            {hasFilters ? 'Найдено' : 'Сотрудники'} · {matchedPeople}
                        </span>
                        <button type="button" className="ses-m-sort" onClick={() => setSortOpen(true)} aria-haspopup="dialog">
                            {currentSort.short}
                            <svg viewBox="0 0 12 12" fill="none" aria-hidden="true">
                                <path d="M2.5 4.5L6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                        </button>
                    </div>

                    {people.length === 0 ? (
                        <div className="ses-m-empty">
                            {isLoading
                                ? 'Ищем…'
                                : <span>Ничего не найдено{query ? <> по запросу «{query}»</> : null}</span>}
                            {!isLoading && hasFilters && (
                                <button type="button" className="ses-m-link" onClick={onResetFilters}>Сбросить фильтры</button>
                            )}
                        </div>
                    ) : (
                        <ul className="ses-m-list" aria-busy={isLoading ? 'true' : undefined}>
                            {people.map((person) => (
                                <PersonRow
                                    key={person.user_id}
                                    person={person}
                                    now={now}
                                    selecting={selecting}
                                    isSelected={selected.has(person.user_id)}
                                    disabled={bulkRevoking}
                                    onOpen={onOpen}
                                    onToggle={onToggle}
                                    Avatar={Avatar}
                                />
                            ))}
                        </ul>
                    )}

                    {hasMore && <div ref={sentinelRef} className="ses-m-sentinel" aria-hidden="true" />}
                    {isLoadingMore && <div className="ses-m-foot">Загружаем ещё…</div>}
                </>
            )}

            {selecting && (
                <div className="ses-m-bulk" role="region" aria-label="Выбранные сотрудники">
                    <button type="button" className="ses-m-link" onClick={onToggleAll} disabled={bulkRevoking || people.length === 0}>
                        {allSelected ? 'Снять выбор' : 'Выбрать всех'}
                    </button>
                    <span className="ses-m-bulk-count" aria-live="polite">
                        {bulkRevoking ? (
                            <strong>Прерываем…</strong>
                        ) : selected.size > 0 ? (
                            <>
                                <strong>Выбрано: {selected.size}</strong>
                                {selectedSessionsCount} {sessionWord(selectedSessionsCount)}
                            </>
                        ) : 'Отметьте сотрудников'}
                    </span>
                    <button
                        type="button"
                        className="ses-m-link is-danger"
                        onClick={() => setConfirmOpen(true)}
                        disabled={bulkRevoking || selected.size === 0}
                    >
                        Прервать
                    </button>
                </div>
            )}

            <MobileActionSheet
                open={confirmOpen}
                onClose={() => setConfirmOpen(false)}
                actions={[
                    {
                        key: 'note',
                        render: (
                            <p className="ses-m-sheet-note">
                                У {selected.size} {plural(selected.size, 'сотрудника', 'сотрудников', 'сотрудников')} будет
                                прервано {selectedSessionsCount} {sessionWord(selectedSessionsCount)}. Они будут
                                принудительно разлогинены.
                            </p>
                        ),
                    },
                    {
                        key: 'revoke',
                        label: `Прервать ${selectedSessionsCount} ${sessionWordAcc(selectedSessionsCount)}`,
                        danger: true,
                        onClick: confirmRevoke,
                    },
                ]}
            />

            <MobileActionSheet
                open={sortOpen}
                onClose={() => setSortOpen(false)}
                actions={SORT_OPTIONS.map((option) => {
                    const current = option.sort === currentSort.sort;
                    return {
                        key: option.sort,
                        render: (
                            <button
                                type="button"
                                className={`mobile-actions__row ses-m-sheet-choice${current ? ' is-current' : ''}`}
                                onClick={() => {
                                    setSortOpen(false);
                                    if (option.sort !== sortKey || option.dir !== sortDir) onSort(option.sort, option.dir);
                                }}
                            >
                                {option.label}
                                {current && <CheckGlyph className="ses-m-sheet-check" />}
                            </button>
                        ),
                    };
                })}
            />
        </div>
    );
}
