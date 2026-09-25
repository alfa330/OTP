import React from 'react';
import MobileActionSheet from '../common/MobileActionSheet';
import { IosModal } from '../ui/ios';
import {
    buildCardSections,
    countByStatus,
    filterEmployees,
    groupOperators,
    plural,
    rowStatusNote,
    sortOptionsFromColumns,
    visibleStatusChips,
} from './employeesPhoneList';
import './employees-mobile.css';

/**
 * «Учет сотрудников» на телефоне: список людей вместо таблицы.
 *
 * На экране в 390 px настольный раздел превращался в столбик: заголовок,
 * выбор отдела, две крупные кнопки одна под другой, пустая карточка дней
 * рождения на полэкрана, шесть меток статусов в три ряда, таблица, которая
 * ездит вбок, и плавающий переключатель колонок поверх строк. Здесь всё это
 * приведено к виду списка в приложении:
 *   • строка — человек: фото, имя, направление или должность, а статус —
 *     только когда он не «Работает»;
 *   • нажатие — карточка экраном: все поля четырёх наборов таблицы разом,
 *     действия строками внизу (переключатель колонок не нужен вовсе);
 *   • подтверждения и порядок — листами снизу, а не окнами посреди экрана;
 *   • массовая правка — режимом «Выбрать», как в «Сессиях».
 *
 * Состояние (поиск, вкладка, отдел, порядок, выбор) живёт в App.jsx и общее с
 * компьютером; правила списка — employeesPhoneList.js. Здесь только раскладка.
 * Один компонент на все пять списков раздела: «Супервайзеры», «Сотрудники»,
 * «Тренеры», «Админы» и «Операторы» у СВ и тренера.
 */

const EMPTY_SET = new Set();

const RATE_OPTIONS = [
    { value: '1', label: '1.00' },
    { value: '0.75', label: '0.75' },
    { value: '0.5', label: '0.50' },
];

const CheckGlyph = ({ className }) => (
    <svg className={className} viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="M3.5 8.4l2.9 2.9L12.6 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

const ChevronDown = ({ className }) => (
    <svg className={className} viewBox="0 0 12 12" fill="none" aria-hidden="true">
        <path d="M2.5 4.5L6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

const Photo = ({ person, Avatar, className }) => {
    const name = String(person?.name || '').trim();
    return (
        <span className={className} aria-hidden="true">
            {person?.avatar_url
                ? <Avatar src={person.avatar_url} alt="" className="emp-m-avatar-img" />
                : (name.charAt(0).toUpperCase() || '?')}
        </span>
    );
};

/* Строка выбора в массовой правке: подпись, значение и системное колесо iOS
   под прозрачным select — само колесо для одного значения лучше любого
   своего списка, а закрытое поле выглядит строкой настроек. */
const PickRow = ({ label, value, options, onChange, disabled }) => {
    const current = options.find((option) => String(option.value) === String(value));
    return (
        <li className="emp-card-item">
            <label className="emp-card-pick">
                <span className="emp-card-label">{label}</span>
                <span className={`emp-card-pick-value${current ? ' is-set' : ''}`}>
                    {current ? current.label : 'Не менять'}
                </span>
                <ChevronDown className="emp-card-pick-chev" />
                <select
                    className="emp-card-select"
                    value={value}
                    onChange={(event) => onChange(event.target.value)}
                    disabled={disabled}
                    aria-label={label}
                >
                    <option value="">Не менять</option>
                    {options.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                </select>
            </label>
        </li>
    );
};

export default function EmployeesMobileView({
    title,
    rows,
    loading = false,
    emptyText = 'Сотрудники не найдены.',
    emptySearchText = 'Никого не нашли.',
    countWords = ['сотрудник', 'сотрудника', 'сотрудников'],
    statusTabs,
    statusTab,
    onStatusTab,
    isVisibleByStatus,
    statusCodeOf,
    statusLabelOf,
    isBlacklist,
    search,
    onSearch,
    matchesSearch,
    columnsFor,
    cardSectionList,
    renderValue,
    sortField,
    sortDir,
    onSort,
    compare,
    operatorGroups = null,
    subtitleOf = null,
    department = null,
    birthdays = null,
    formatDaysAway,
    onAdd = null,
    addLabel = 'Добавить',
    onReport = null,
    reportBusy = false,
    selection = null,
    actionsFor = null,
    Avatar,
}) {
    const [selecting, setSelecting] = React.useState(false);
    const [sortOpen, setSortOpen] = React.useState(false);
    const [cardId, setCardId] = React.useState(null);
    const [pending, setPending] = React.useState(null);
    const [bulkOpen, setBulkOpen] = React.useState(false);
    const [birthdaysOpen, setBirthdaysOpen] = React.useState(false);
    /* Экран и лист уезжают дольше, чем живёт их состояние: пока идёт уход,
       им нужно что показывать, иначе содержимое пропадает на полпути. */
    const lastCardRef = React.useRef(null);
    const lastPendingRef = React.useRef(null);
    const applyAskedRef = React.useRef(false);

    const allRows = Array.isArray(rows) ? rows : [];
    const rowsById = new Map(allRows.map((row) => [Number(row?.id), row]));
    const counts = countByStatus(allRows, statusTabs, isVisibleByStatus);
    const chips = visibleStatusChips(statusTabs, counts, statusTab);
    const query = String(search || '').trim();
    const matched = filterEmployees(allRows, { statusTab, isVisible: isVisibleByStatus, query, matches: matchesSearch });
    const groups = operatorGroups
        ? groupOperators(matched, { ...operatorGroups, compare, sortField, sortDir })
        : [{ key: 'all', title: '', rows: [...matched].sort(compare) }];
    const visibleIds = groups
        .flatMap((group) => group.rows)
        .map((row) => Number(row?.id))
        .filter((id) => Number.isFinite(id));

    const sortOptions = sortOptionsFromColumns(columnsFor('general'));
    const currentSort = sortOptions.find((option) => option.field === sortField) || sortOptions[0];

    const selectedIds = selection?.ids || EMPTY_SET;
    const bulk = selection?.bulk || null;
    const bulkSaving = Boolean(bulk?.saving);
    const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));

    const cardEmployee = cardId == null ? null : rowsById.get(cardId) || null;
    if (cardEmployee) lastCardRef.current = cardEmployee;
    if (pending) lastPendingRef.current = pending;

    /* Человек ушёл из списка — удалили, перевели в операторы, уволили с
       переносом в другой отдел: экран закрывается сам. Пока список
       перечитывается, ждём — строки на это время бывают пустыми. */
    React.useEffect(() => {
        if (cardId != null && !cardEmployee && !loading) setCardId(null);
    }, [cardId, cardEmployee, loading]);

    /* Массовая правка применилась — выходим из режима выбора, как «Фото»
       после удаления. На ошибке App.jsx выбор сохраняет, и человек остаётся
       со своими отметками. */
    React.useEffect(() => {
        if (bulkSaving || !applyAskedRef.current) return;
        applyAskedRef.current = false;
        if (selectedIds.size === 0) {
            setBulkOpen(false);
            setSelecting(false);
        }
    }, [bulkSaving, selectedIds]);

    const toggleSelecting = () => {
        if (selecting) {
            selection.onClear();
            setBulkOpen(false);
        }
        setSelecting((prev) => !prev);
    };

    const openRow = (employee) => {
        const id = Number(employee?.id);
        if (!Number.isFinite(id)) return;
        if (selecting) selection.onToggle(id);
        else setCardId(id);
    };

    const runAction = (action) => {
        if (action.confirm) setPending(action);
        else action.onClick();
    };

    const renderRow = (employee, groupKey) => {
        const id = Number(employee?.id);
        const isSelected = selectedIds.has(id);
        const note = rowStatusNote({
            code: statusCodeOf(employee?.status),
            statusTab,
            label: statusLabelOf(employee?.status),
            blacklist: isBlacklist(employee),
        });
        const subtitle = subtitleOf ? subtitleOf(employee, groupKey) : '';
        return (
            <li className="emp-m-item" key={`${groupKey}-${employee?.id}`}>
                <button
                    type="button"
                    className="emp-m-row"
                    onClick={() => openRow(employee)}
                    disabled={selecting && bulkSaving}
                    aria-pressed={selecting ? isSelected : undefined}
                >
                    {selecting && (
                        <span className={`emp-m-check${isSelected ? ' is-on' : ''}`} aria-hidden="true">
                            <CheckGlyph />
                        </span>
                    )}
                    <Photo person={employee} Avatar={Avatar} className="emp-m-avatar" />
                    <span className="emp-m-main">
                        <span className="emp-m-line">
                            <span className="emp-m-name">{employee?.name || '—'}</span>
                            {note && <span className={`emp-m-note is-${note.tone}`}>{note.text}</span>}
                        </span>
                        {subtitle && <span className="emp-m-meta">{subtitle}</span>}
                    </span>
                    {!selecting && <span className="emp-m-chevron" aria-hidden="true" />}
                </button>
            </li>
        );
    };

    const renderAction = (action) => (
        <li key={action.key} className="emp-card-item">
            <button
                type="button"
                className={`emp-card-act${action.danger ? ' is-danger' : ''}`}
                onClick={() => runAction(action)}
                disabled={action.disabled}
            >
                {action.label}
            </button>
        </li>
    );

    const renderCard = (employee) => {
        const code = statusCodeOf(employee?.status);
        const danger = code === 'fired' || code === 'dismissal';
        const sections = buildCardSections(cardSectionList, columnsFor, (column) => renderValue(column, employee));
        /* Подпись шапки — то, чего нет в полях ниже: направление у оператора
           стоит строкой в «Общем», и повтор в шапке был бы тем же словом дважды
           на одном экране. Отдел у супервайзера полем не бывает — он остаётся. */
        const fieldTexts = new Set(sections.flatMap((section) => section.fields).map((field) => field.value));
        const subtitle = (subtitleOf ? subtitleOf(employee, 'card') : '')
            .split(' · ')
            .filter((part) => part && !fieldTexts.has(part))
            .join(' · ');
        const actions = actionsFor ? actionsFor(employee) : [];
        const regular = actions.filter((action) => !action.danger);
        const destructive = actions.filter((action) => action.danger);
        return (
            <div className="emp-card">
                <div className="emp-card-hero">
                    <Photo person={employee} Avatar={Avatar} className="emp-card-photo" />
                    <div className="emp-card-name" role="heading" aria-level={2}>{employee?.name || '—'}</div>
                    <div className="emp-card-sub">
                        {subtitle && <span>{subtitle}</span>}
                        <span className={code === 'working' ? undefined : (danger ? 'is-danger' : 'is-warn')}>
                            {statusLabelOf(employee?.status)}{isBlacklist(employee) ? ' · ЧС' : ''}
                        </span>
                    </div>
                </div>

                {sections.map((section) => (
                    <section key={section.key} className="emp-card-group">
                        <div className="emp-card-caption">{section.title}</div>
                        <ul className="emp-card-list">
                            {section.fields.map((field) => (
                                <li key={field.key} className="emp-card-item">
                                    <div className="emp-card-field">
                                        <span className="emp-card-label">{field.label}</span>
                                        <span className="emp-card-value">{field.value}</span>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    </section>
                ))}

                {/* Сначала сведения, потом то, что с ними делают; опасное —
                    отдельной группой, чтобы его не задеть вместо «Изменить». */}
                {regular.length > 0 && <ul className="emp-card-list emp-card-acts">{regular.map(renderAction)}</ul>}
                {destructive.length > 0 && <ul className="emp-card-list emp-card-acts">{destructive.map(renderAction)}</ul>}
            </div>
        );
    };

    const departmentName = department
        ? ((department.options || []).find((dep) => String(dep.id) === String(department.value))?.name || 'Все отделы')
        : '';

    const birthdayList = Array.isArray(birthdays) ? birthdays : [];
    const shownBirthdays = birthdaysOpen ? birthdayList : birthdayList.slice(0, 3);

    const draft = bulk?.draft || { group_id: '', direction_id: '', rate: '' };
    const canApply = selectedIds.size > 0 && !bulkSaving
        && (draft.group_id !== '' || draft.direction_id !== '' || draft.rate !== '');
    const setDraftField = (field) => (value) => bulk.onDraft((prev) => ({ ...prev, [field]: value }));

    const shownPending = pending || lastPendingRef.current;
    const shownCard = cardEmployee || lastCardRef.current;

    return (
        <div className={`emp-m${selecting ? ' is-selecting' : ''}`}>
            <div className="emp-m-head">
                <div className="emp-m-title" role="heading" aria-level={1}>{title}</div>
                {selection && (allRows.length > 0 || selecting) && (
                    <button type="button" className="emp-m-link" onClick={toggleSelecting} disabled={bulkSaving}>
                        {selecting ? 'Готово' : 'Выбрать'}
                    </button>
                )}
                {!selecting && onReport && (
                    <button type="button" className="emp-m-round" onClick={onReport} disabled={reportBusy} aria-label="Сформировать отчёт">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M4.5 16.5v1.75A1.75 1.75 0 006.25 20h11.5a1.75 1.75 0 001.75-1.75V16.5" />
                        </svg>
                    </button>
                )}
                {!selecting && onAdd && (
                    <button type="button" className="emp-m-round" onClick={onAdd} aria-label={addLabel}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} aria-hidden="true">
                            <path strokeLinecap="round" d="M12 5.5v13M5.5 12h13" />
                        </svg>
                    </button>
                )}
            </div>

            {department && (
                /* Отдел — строкой под заголовком, а не полем во всю ширину:
                   это уточнение того, чей список, как «Входящие ⌄» в «Почте».
                   Сам выбор — системное колесо под прозрачным select. */
                <label className="emp-m-dept">
                    <span className="emp-m-dept-text">{departmentName}</span>
                    <ChevronDown className="emp-m-dept-chev" />
                    <select
                        className="emp-m-dept-select"
                        value={department.value || ''}
                        onChange={(event) => department.onChange(event.target.value)}
                        aria-label="Отдел"
                    >
                        <option value="">Все отделы</option>
                        {(department.options || []).map((dep) => (
                            <option key={dep.id} value={dep.id}>{dep.name}</option>
                        ))}
                    </select>
                </label>
            )}

            {/* Дни рождения — только когда они есть. Пустая карточка «в
                ближайшие 2 недели дней рождений нет» занимала на телефоне
                полэкрана над списком, ради которого раздел открывают. */}
            {!selecting && birthdayList.length > 0 && (
                <section className="emp-m-block" aria-label="Ближайшие дни рождения">
                    <div className="emp-m-caption">Дни рождения</div>
                    <ul className="emp-m-list">
                        {shownBirthdays.map((item, index) => {
                            const person = rowsById.get(Number(item.id)) || null;
                            const meta = [item.dateLabel, item.directionLabel === 'Без направления' ? '' : item.directionLabel]
                                .filter(Boolean).join(' · ');
                            return (
                                <li className="emp-m-item" key={`${item.id || item.name}-${index}`}>
                                    <button
                                        type="button"
                                        className="emp-m-row"
                                        onClick={() => person && setCardId(Number(person.id))}
                                        disabled={!person}
                                    >
                                        <Photo person={person || item} Avatar={Avatar} className="emp-m-avatar" />
                                        <span className="emp-m-main">
                                            <span className="emp-m-line">
                                                <span className="emp-m-name">{item.name}</span>
                                                <span className={`emp-m-when${item.daysAway === 0 ? ' is-today' : ''}`}>
                                                    {formatDaysAway(item.daysAway)}
                                                </span>
                                            </span>
                                            {meta && <span className="emp-m-meta">{meta}</span>}
                                        </span>
                                    </button>
                                </li>
                            );
                        })}
                        {birthdayList.length > 3 && (
                            <li className="emp-m-item">
                                <button type="button" className="emp-m-more" onClick={() => setBirthdaysOpen((prev) => !prev)}>
                                    {birthdaysOpen ? 'Свернуть' : `Показать все · ${birthdayList.length}`}
                                </button>
                            </li>
                        )}
                    </ul>
                </section>
            )}

            <div className="emp-m-search">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
                </svg>
                <input
                    type="search"
                    value={search}
                    onChange={(event) => onSearch(event.target.value)}
                    onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
                    placeholder="Поиск"
                    enterKeyHint="search"
                    autoComplete="off"
                    autoCorrect="off"
                    spellCheck={false}
                    aria-label="Поиск сотрудников"
                />
                {search && (
                    <button type="button" className="emp-m-search-clear" onClick={() => onSearch('')} aria-label="Очистить поиск">
                        <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM7.47 7.47a.75.75 0 011.06 0L10 8.94l1.47-1.47a.75.75 0 111.06 1.06L11.06 10l1.47 1.47a.75.75 0 11-1.06 1.06L10 11.06l-1.47 1.47a.75.75 0 01-1.06-1.06L8.94 10 7.47 8.53a.75.75 0 010-1.06z" clipRule="evenodd" />
                        </svg>
                    </button>
                )}
            </div>

            {chips.length > 0 && (
                <div className="emp-m-chips" role="tablist" aria-label="Статус">
                    {chips.map((tab) => {
                        const active = tab.key === statusTab;
                        return (
                            <button
                                key={tab.key}
                                type="button"
                                role="tab"
                                aria-selected={active}
                                className={`emp-m-chip${active ? ' is-on' : ''}`}
                                onClick={() => { if (!active) onStatusTab(tab.key); }}
                            >
                                {tab.label}
                                <span className="emp-m-chip-count">{counts[tab.key] || 0}</span>
                            </button>
                        );
                    })}
                </div>
            )}

            {allRows.length === 0 ? (
                <div className="emp-m-empty">{loading ? 'Загрузка…' : emptyText}</div>
            ) : (
                <>
                    <div className="emp-m-summary">
                        <span className="emp-m-caption">
                            {query ? `Найдено: ${matched.length}` : `${matched.length} ${plural(matched.length, countWords)}`}
                        </span>
                        {sortOptions.length > 1 && matched.length > 1 && (
                            <button type="button" className="emp-m-sort" onClick={() => setSortOpen(true)} aria-haspopup="dialog">
                                {currentSort.short}
                                <ChevronDown />
                            </button>
                        )}
                    </div>

                    {matched.length === 0 ? (
                        <div className="emp-m-empty">{query ? emptySearchText : emptyText}</div>
                    ) : groups.map((group) => (
                        <section key={group.key} className="emp-m-block">
                            {group.title && (
                                <div className="emp-m-caption">{group.title} · {group.rows.length}</div>
                            )}
                            <ul className="emp-m-list" aria-busy={loading ? 'true' : undefined}>
                                {group.rows.map((employee) => renderRow(employee, group.key))}
                            </ul>
                        </section>
                    ))}
                </>
            )}

            {selecting && selection && (
                <div className="emp-m-bulk" role="region" aria-label="Выбранные сотрудники">
                    <button
                        type="button"
                        className="emp-m-link"
                        onClick={() => (allSelected ? selection.onClear() : selection.onSelectAll(visibleIds))}
                        disabled={bulkSaving || visibleIds.length === 0}
                    >
                        {allSelected ? 'Снять выбор' : 'Выбрать всех'}
                    </button>
                    <span className="emp-m-bulk-count" aria-live="polite">
                        {bulkSaving
                            ? <strong>Сохраняем…</strong>
                            : selectedIds.size > 0
                                ? <strong>Выбрано: {selectedIds.size}</strong>
                                : 'Отметьте сотрудников'}
                    </span>
                    <button
                        type="button"
                        className="emp-m-link is-strong"
                        onClick={() => setBulkOpen(true)}
                        disabled={bulkSaving || selectedIds.size === 0 || !bulk}
                    >
                        Изменить
                    </button>
                </div>
            )}

            <IosModal open={cardEmployee != null} onClose={() => setCardId(null)} title="">
                {shownCard && renderCard(shownCard)}
            </IosModal>

            {bulk && (
                <IosModal
                    open={bulkOpen}
                    onClose={() => setBulkOpen(false)}
                    title="Изменить выбранных"
                    subtitle={`${selectedIds.size} ${plural(selectedIds.size, countWords)}`}
                    footer={(
                        <button
                            type="button"
                            className="emp-m-apply"
                            onClick={() => { applyAskedRef.current = true; bulk.onApply(); }}
                            disabled={!canApply}
                        >
                            {bulkSaving ? 'Сохраняем…' : 'Применить'}
                        </button>
                    )}
                >
                    <div className="emp-card">
                        <ul className="emp-card-list">
                            {bulk.showGroupAndDirection && (
                                <PickRow label="Группа" value={draft.group_id} options={bulk.groups} onChange={setDraftField('group_id')} disabled={bulkSaving} />
                            )}
                            {bulk.showGroupAndDirection && bulk.showDirection !== false && (
                                <PickRow label="Направление" value={draft.direction_id} options={bulk.directions} onChange={setDraftField('direction_id')} disabled={bulkSaving} />
                            )}
                            <PickRow label="Ставка" value={draft.rate} options={RATE_OPTIONS} onChange={setDraftField('rate')} disabled={bulkSaving} />
                        </ul>
                    </div>
                </IosModal>
            )}

            <MobileActionSheet
                open={Boolean(pending)}
                onClose={() => setPending(null)}
                actions={shownPending ? [
                    {
                        key: 'note',
                        render: <p className="emp-m-sheet-note">{shownPending.confirm.note}</p>,
                    },
                    {
                        key: 'run',
                        label: shownPending.confirm.label,
                        danger: Boolean(shownPending.danger),
                        onClick: () => {
                            const run = shownPending.onClick;
                            setPending(null);
                            run();
                        },
                    },
                ] : []}
            />

            <MobileActionSheet
                open={sortOpen}
                onClose={() => setSortOpen(false)}
                actions={sortOptions.map((option) => {
                    const current = option.field === currentSort?.field;
                    return {
                        key: option.field,
                        render: (
                            <button
                                type="button"
                                className={`mobile-actions__row emp-m-sheet-choice${current ? ' is-current' : ''}`}
                                onClick={() => {
                                    setSortOpen(false);
                                    if (option.field !== sortField || option.dir !== sortDir) onSort(option.field, option.dir);
                                }}
                            >
                                {option.label}
                                {current && <CheckGlyph className="emp-m-sheet-check" />}
                            </button>
                        ),
                    };
                })}
            />
        </div>
    );
}
