import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import { normalizeRole, isAdminLikeRole, isSupervisorRole } from '../../utils/roles';

// ─── Constants ───────────────────────────────────────────────────────────────

const FALLBACK_TECHNICAL_REASONS = [
    'Не работает интернет',
    'Замена мыши',
    'Не работает микрофон',
    'Не работает Oktell',
    'Проблема с маршрутизацией Oktell (не идут исходящие звонки), переключение в ручной режим',
    'Замена клавиатуры',
    'Не заходит в корпоративный чат',
    'Не включается компьютер',
    'Переполнена память',
    'Кнопка "Войти в колл-центр" в Oktell не реагирует на действия',
    'Виснет компьютер',
    'Не работают программы на ПК (ошибка "Меню "Пуск" не работает")',
    'Проблема с подключением к сайту Oktell',
    'Не может войти в учетную запись ПК',
    'Не поступают звонки',
    'Не может войти в учетную запись Oktell',
    'Отключение света',
    'Массовая проблема с Октелл',
    'Массовая проблема с интернетом',
    'Массовая проблема с телефонией',
];

const MONTHS_RU = [
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];
const DAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const MASS_KEYWORDS = ['массовая', 'массовый', 'массовое'];
const WORKPLACE_MIN = 1;
const WORKPLACE_MAX = 30;
const WORKPLACE_NUMBERS = Array.from({ length: WORKPLACE_MAX }, (_, idx) => WORKPLACE_MIN + idx);

const INPUT_CLASS =
    'mt-1 w-full rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm text-gray-800 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400';
const LABEL_CLASS = 'text-xs font-semibold uppercase tracking-wide text-blue-900/80';

// ─── Utilities ────────────────────────────────────────────────────────────────

const toIsoDate = (value = new Date()) => {
    try {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    } catch {
        return '';
    }
};

const currentMonthStartIso = () => {
    const now = new Date();
    return toIsoDate(new Date(now.getFullYear(), now.getMonth(), 1));
};

const toIntList = (values) => {
    const list = Array.isArray(values) ? values : [values];
    const out = [];
    const seen = new Set();
    for (const item of list) {
        const n = Number(item);
        if (!Number.isFinite(n)) continue;
        const id = Math.trunc(n);
        if (id <= 0 || seen.has(id)) continue;
        seen.add(id);
        out.push(id);
    }
    return out;
};

const areStringListsEqual = (left, right) => {
    if (!Array.isArray(left) || !Array.isArray(right)) return false;
    if (left.length !== right.length) return false;
    for (let i = 0; i < left.length; i++) {
        if (String(left[i]) !== String(right[i])) return false;
    }
    return true;
};

const normalizeFilterPayload = (filters) => ({
    dateFrom: String(filters?.dateFrom || '').trim(),
    dateTo: String(filters?.dateTo || '').trim(),
    operatorId: String(filters?.operatorId || '').trim(),
    reason: String(filters?.reason || '').trim(),
    workplaceNumber: String(filters?.workplaceNumber || '').trim(),
});

const areFiltersEqual = (left, right) => {
    const a = normalizeFilterPayload(left);
    const b = normalizeFilterPayload(right);
    return (
        a.dateFrom === b.dateFrom &&
        a.dateTo === b.dateTo &&
        a.operatorId === b.operatorId &&
        a.reason === b.reason &&
        a.workplaceNumber === b.workplaceNumber
    );
};

const buildFilterQuery = (filters) => {
    const normalized = normalizeFilterPayload(filters);
    const query = new URLSearchParams();
    query.set('limit', '1000');
    if (normalized.dateFrom) query.set('date_from', normalized.dateFrom);
    if (normalized.dateTo) query.set('date_to', normalized.dateTo);
    if (normalized.operatorId) query.set('operator_id', normalized.operatorId);
    if (normalized.reason) query.set('reason', normalized.reason);
    if (normalized.workplaceNumber) query.set('workplace_number', normalized.workplaceNumber);
    return query;
};

const normalizeWorkplaceNumber = (value) => {
    if (value === null || value === undefined) return null;
    if (typeof value === 'string' && value.trim() === '') return null;
    const n = Number(value);
    if (!Number.isFinite(n)) return null;
    const intN = Math.trunc(n);
    if (intN < WORKPLACE_MIN || intN > WORKPLACE_MAX) return null;
    return intN;
};

const formatDateDisplay = (iso) => {
    if (!iso) return '';
    const parts = iso.split('-');
    if (parts.length !== 3) return iso;
    return `${parts[2]}.${parts[1]}.${parts[0]}`;
};

const isMassiveReason = (reason) => {
    if (!reason) return false;
    const lower = String(reason).toLowerCase();
    return MASS_KEYWORDS.some((kw) => lower.includes(kw));
};

// ─── Duration helpers ─────────────────────────────────────────────────────────

const parseTimeMinutes = (timeStr) => {
    if (!timeStr) return null;
    const parts = String(timeStr).split(':');
    if (parts.length < 2) return null;
    const h = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10);
    if (Number.isNaN(h) || Number.isNaN(m)) return null;
    return h * 60 + m;
};

const calcDurationMinutes = (startTime, endTime) => {
    const start = parseTimeMinutes(startTime);
    const end   = parseTimeMinutes(endTime);
    if (start === null || end === null) return null;
    let diff = end - start;
    if (diff < 0) diff += 24 * 60; // spans midnight
    return diff;
};

const formatDuration = (minutes) => {
    if (minutes === null || minutes < 0) return '—';
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    if (h === 0) return `${m} мин`;
    if (m === 0) return `${h} ч`;
    return `${h} ч ${m} мин`;
};

const getDaysInMonth = (year, month) => new Date(year, month + 1, 0).getDate();
const getFirstDayOfWeek = (year, month) => {
    const day = new Date(year, month, 1).getDay();
    return day === 0 ? 6 : day - 1; // Mon = 0
};

// ─── DateRangePicker ──────────────────────────────────────────────────────────

const DateRangePicker = memo(function DateRangePicker({ dateFrom, dateTo, onChange }) {
    const todayIso = toIsoDate(new Date());
    const [open, setOpen] = useState(false);
    const [step, setStep] = useState(0); // 0 = picking start, 1 = picking end
    const [hover, setHover] = useState(null);
    const [viewYear, setViewYear] = useState(() => {
        const d = dateFrom ? new Date(dateFrom.replaceAll('-', '/')) : new Date();
        return isNaN(d.getFullYear()) ? new Date().getFullYear() : d.getFullYear();
    });
    const [viewMonth, setViewMonth] = useState(() => {
        const d = dateFrom ? new Date(dateFrom.replaceAll('-', '/')) : new Date();
        return isNaN(d.getMonth()) ? new Date().getMonth() : d.getMonth();
    });
    const ref = useRef(null);

    useEffect(() => {
        if (!open) return;
        const handler = (e) => {
            if (ref.current && !ref.current.contains(e.target)) {
                setOpen(false);
                setStep(0);
                setHover(null);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [open]);

    const prevMonth = () => {
        if (viewMonth === 0) { setViewYear((y) => y - 1); setViewMonth(11); }
        else setViewMonth((m) => m - 1);
    };
    const nextMonth = () => {
        if (viewMonth === 11) { setViewYear((y) => y + 1); setViewMonth(0); }
        else setViewMonth((m) => m + 1);
    };

    const pickDay = (iso) => {
        if (step === 0) {
            onChange({ dateFrom: iso, dateTo: iso });
            setStep(1);
        } else {
            const start = dateFrom || iso;
            if (iso < start) onChange({ dateFrom: iso, dateTo: start });
            else onChange({ dateFrom: start, dateTo: iso });
            setStep(0);
            setOpen(false);
            setHover(null);
        }
    };

    const quickToday = () => { onChange({ dateFrom: todayIso, dateTo: todayIso }); setStep(0); setOpen(false); };
    const quickMonth = () => { onChange({ dateFrom: currentMonthStartIso(), dateTo: todayIso }); setStep(0); setOpen(false); };
    const quickReset  = () => { onChange({ dateFrom: currentMonthStartIso(), dateTo: todayIso }); setStep(0); };

    const renderDays = () => {
        const total = getDaysInMonth(viewYear, viewMonth);
        const first = getFirstDayOfWeek(viewYear, viewMonth);
        const cells = [];
        for (let i = 0; i < first; i++) cells.push(<div key={`e${i}`} />);

        for (let d = 1; d <= total; d++) {
            const iso = `${viewYear}-${String(viewMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
            const isStart = iso === dateFrom;
            const isEnd   = iso === dateTo;
            const inRange = dateFrom && dateTo && iso > dateFrom && iso < dateTo;
            let inHover = false;
            if (step === 1 && hover && dateFrom) {
                if (hover >= dateFrom) inHover = iso > dateFrom && iso <= hover;
                else inHover = iso >= hover && iso < dateFrom;
            }
            const isToday = iso === todayIso;

            let cls = 'flex h-8 w-8 items-center justify-center text-xs font-medium cursor-pointer select-none transition-all ';
            if (isStart || isEnd) {
                cls += 'rounded-full bg-blue-600 text-white shadow z-10 ';
            } else if (inRange) {
                cls += 'bg-blue-100 text-blue-800 rounded-none ';
            } else if (inHover) {
                cls += 'bg-blue-50 text-blue-700 rounded-none ';
            } else if (isToday) {
                cls += 'rounded-full border border-blue-400 text-blue-700 hover:bg-blue-50 ';
            } else {
                cls += 'rounded-full text-gray-700 hover:bg-blue-50 ';
            }

            cells.push(
                <div
                    key={iso}
                    className={cls}
                    onClick={() => pickDay(iso)}
                    onMouseEnter={() => setHover(iso)}
                    onMouseLeave={() => setHover(null)}
                >
                    {d}
                </div>
            );
        }
        return cells;
    };

    const label = dateFrom && dateTo
        ? dateFrom === dateTo
            ? formatDateDisplay(dateFrom)
            : `${formatDateDisplay(dateFrom)} → ${formatDateDisplay(dateTo)}`
        : dateFrom
            ? `${formatDateDisplay(dateFrom)} → ...`
            : 'Выберите период';

    return (
        <div ref={ref} className="relative">
            <button
                type="button"
                onClick={() => { setOpen((o) => !o); setStep(0); }}
                className="flex w-full items-center gap-2 rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm text-gray-800 shadow-sm hover:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-400"
            >
                <FaIcon className="fas fa-calendar text-blue-400" style={{ width: '0.9em', height: '0.9em' }} />
                <span className="flex-1 text-left">{label}</span>
                <FaIcon
                    className="fas fa-chevron-down text-gray-400"
                    style={{ width: '0.8em', height: '0.8em', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }}
                />
            </button>

            {open && (
                <div
                    className="absolute top-full left-0 z-50 mt-1 w-72 rounded-2xl border border-blue-100 bg-white shadow-2xl p-4"
                    style={{ animation: 'fadeInDown .15s ease' }}
                >
                    {/* Month navigation */}
                    <div className="flex items-center justify-between mb-3">
                        <button type="button" onClick={prevMonth} className="rounded-lg p-1.5 hover:bg-blue-50 text-blue-600">
                            <FaIcon className="fas fa-angle-left" style={{ width: '0.9em', height: '0.9em' }} />
                        </button>
                        <span className="text-sm font-bold text-blue-900">
                            {MONTHS_RU[viewMonth]} {viewYear}
                        </span>
                        <button type="button" onClick={nextMonth} className="rounded-lg p-1.5 hover:bg-blue-50 text-blue-600">
                            <FaIcon className="fas fa-angle-right" style={{ width: '0.9em', height: '0.9em' }} />
                        </button>
                    </div>

                    {/* Day headers */}
                    <div className="grid grid-cols-7 mb-1">
                        {DAYS_SHORT.map((d) => (
                            <div key={d} className="flex h-7 w-8 items-center justify-center text-[10px] font-bold uppercase text-blue-400">
                                {d}
                            </div>
                        ))}
                    </div>

                    {/* Day grid */}
                    <div className="grid grid-cols-7">{renderDays()}</div>

                    {/* Hint */}
                    <p className="mt-2 text-center text-[11px] text-gray-400">
                        {step === 0 ? 'Нажмите начало периода' : 'Нажмите конец периода'}
                    </p>

                    {/* Quick picks */}
                    <div className="mt-3 flex gap-2 border-t border-blue-100 pt-3">
                        <button type="button" onClick={quickToday}
                            className="flex-1 rounded-lg bg-blue-50 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-100">
                            Сегодня
                        </button>
                        <button type="button" onClick={quickMonth}
                            className="flex-1 rounded-lg bg-blue-50 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-100">
                            Этот месяц
                        </button>
                        <button type="button" onClick={quickReset}
                            className="flex-1 rounded-lg bg-gray-100 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-200">
                            Сброс
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
});

// ─── CustomReasonDropdown ─────────────────────────────────────────────────────

const CustomReasonDropdown = memo(function CustomReasonDropdown({ value, onChange, reasons, placeholder = 'Выберите причину', required = false }) {
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState('');
    const ref = useRef(null);
    const searchRef = useRef(null);

    useEffect(() => {
        if (!open) return;
        const handler = (e) => {
            if (ref.current && !ref.current.contains(e.target)) setOpen(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [open]);

    useEffect(() => {
        if (open && searchRef.current) searchRef.current.focus();
    }, [open]);

    const filtered = useMemo(() => {
        if (!search.trim()) return reasons;
        const q = search.toLowerCase();
        return reasons.filter((r) => r.toLowerCase().includes(q));
    }, [reasons, search]);

    const select = (reason) => { onChange(reason); setOpen(false); setSearch(''); };

    return (
        <div ref={ref} className="relative mt-1">
            {/* Hidden native select for required validation */}
            <select
                tabIndex={-1}
                aria-hidden="true"
                value={value}
                onChange={() => {}}
                required={required}
                style={{ position: 'absolute', width: 0, height: 0, opacity: 0, pointerEvents: 'none' }}
            >
                <option value="" />
                {reasons.map((r, i) => <option key={i} value={r}>{r}</option>)}
            </select>

            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-400 transition-colors ${
                    value ? 'border-blue-300 bg-white text-gray-800' : 'border-blue-200 bg-white text-gray-400'
                }`}
            >
                <FaIcon className="fas fa-tools text-blue-400 shrink-0" style={{ width: '0.85em', height: '0.85em' }} />
                <span className="flex-1 text-left truncate">{value || placeholder}</span>
                {value && (
                    <span
                        role="button"
                        tabIndex={0}
                        onClick={(e) => { e.stopPropagation(); onChange(''); }}
                        className="shrink-0 text-gray-300 hover:text-red-400 cursor-pointer"
                        title="Очистить"
                    >
                        <FaIcon className="fas fa-times" style={{ width: '0.75em', height: '0.75em' }} />
                    </span>
                )}
                <FaIcon
                    className="fas fa-chevron-down text-gray-300 shrink-0"
                    style={{ width: '0.8em', height: '0.8em', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }}
                />
            </button>

            {open && (
                <div className="absolute top-full left-0 right-0 z-50 mt-1 rounded-xl border border-blue-100 bg-white shadow-2xl overflow-hidden">
                    <div className="p-2 border-b border-blue-50">
                        <div className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50/60 px-2 py-1.5">
                            <FaIcon className="fas fa-search text-blue-300" style={{ width: '0.8em', height: '0.8em' }} />
                            <input
                                ref={searchRef}
                                type="text"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                placeholder="Поиск причины..."
                                className="flex-1 bg-transparent text-sm text-gray-700 focus:outline-none"
                            />
                        </div>
                    </div>
                    <div className="max-h-56 overflow-y-auto py-1">
                        {filtered.length === 0 ? (
                            <div className="px-3 py-3 text-sm text-gray-400 text-center">Ничего не найдено</div>
                        ) : (
                            filtered.map((reason, i) => (
                                <div
                                    key={i}
                                    onClick={() => select(reason)}
                                    className={`flex items-center gap-2 px-3 py-2 text-sm cursor-pointer transition-colors ${
                                        reason === value ? 'bg-blue-100 text-blue-800 font-semibold' : 'text-gray-700 hover:bg-blue-50'
                                    }`}
                                >
                                    <span className="w-4 shrink-0 text-center">
                                        {reason === value && (
                                            <FaIcon className="fas fa-check text-blue-600" style={{ width: '0.75em', height: '0.75em' }} />
                                        )}
                                    </span>
                                    <span>{reason}</span>
                                </div>
                            ))
                        )}
                    </div>
                </div>
            )}
        </div>
    );
});

// ─── CustomMultiSelect ────────────────────────────────────────────────────────

const CustomMultiSelect = memo(function CustomMultiSelect({ items, selectedIds, onChange, placeholder = 'Выбрать...', emptyText = 'Нет элементов' }) {
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState('');
    const ref = useRef(null);

    useEffect(() => {
        if (!open) return;
        const handler = (e) => {
            if (ref.current && !ref.current.contains(e.target)) setOpen(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [open]);

    const filtered = useMemo(() => {
        if (!search.trim()) return items;
        const q = search.toLowerCase();
        return items.filter((item) => String(item.name || '').toLowerCase().includes(q));
    }, [items, search]);

    const toggle = (id) => {
        const numId = Number(id);
        if (selectedIds.includes(numId)) onChange(selectedIds.filter((i) => i !== numId));
        else onChange([...selectedIds, numId]);
    };

    const selectAll = () => onChange(items.map((item) => Number(item.id)));
    const clearAll  = () => onChange([]);

    const labelText = selectedIds.length === 0
        ? null
        : selectedIds.length === 1
            ? items.find((it) => Number(it.id) === selectedIds[0])?.name || `1 выбран`
            : `Выбрано: ${selectedIds.length}`;

    return (
        <div ref={ref} className="relative">
            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                className="flex w-full items-center gap-2 rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm shadow-sm hover:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-400"
            >
                <span className="flex-1 text-left truncate">
                    {labelText
                        ? <span className="text-gray-800">{labelText}</span>
                        : <span className="text-gray-400">{placeholder}</span>
                    }
                </span>
                {selectedIds.length > 0 && (
                    <span className="shrink-0 rounded-full bg-blue-600 px-1.5 py-0.5 text-[10px] font-bold text-white leading-none">
                        {selectedIds.length}
                    </span>
                )}
                <FaIcon
                    className="fas fa-chevron-down text-gray-300 shrink-0"
                    style={{ width: '0.8em', height: '0.8em', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }}
                />
            </button>

            {open && (
                <div className="absolute top-full left-0 right-0 z-50 mt-1 rounded-xl border border-blue-100 bg-white shadow-2xl overflow-hidden">
                    <div className="p-2 space-y-2 border-b border-blue-50">
                        <div className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50/60 px-2 py-1.5">
                            <FaIcon className="fas fa-search text-blue-300" style={{ width: '0.8em', height: '0.8em' }} />
                            <input
                                type="text"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                placeholder="Поиск..."
                                autoFocus
                                className="flex-1 bg-transparent text-sm text-gray-700 focus:outline-none"
                            />
                        </div>
                        <div className="flex gap-2">
                            <button type="button" onClick={selectAll}
                                className="flex-1 rounded-md bg-blue-50 py-1 text-xs font-semibold text-blue-700 hover:bg-blue-100">
                                Все
                            </button>
                            <button type="button" onClick={clearAll}
                                className="flex-1 rounded-md bg-gray-100 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-200">
                                Очистить
                            </button>
                        </div>
                    </div>

                    <div className="max-h-52 overflow-y-auto py-1">
                        {items.length === 0 ? (
                            <div className="px-3 py-3 text-sm text-gray-400 text-center">{emptyText}</div>
                        ) : filtered.length === 0 ? (
                            <div className="px-3 py-3 text-sm text-gray-400 text-center">Ничего не найдено</div>
                        ) : (
                            filtered.map((item) => {
                                const sel = selectedIds.includes(Number(item.id));
                                return (
                                    <div
                                        key={item.id}
                                        onClick={() => toggle(item.id)}
                                        className={`flex items-center gap-3 px-3 py-2 text-sm cursor-pointer transition-colors ${sel ? 'bg-blue-50/80' : 'hover:bg-gray-50'}`}
                                    >
                                        <div className={`h-4 w-4 shrink-0 rounded border-2 flex items-center justify-center transition-colors ${sel ? 'border-blue-600 bg-blue-600' : 'border-gray-300 bg-white'}`}>
                                            {sel && <FaIcon className="fas fa-check text-white" style={{ width: '0.55em', height: '0.55em' }} />}
                                        </div>
                                        <span className={sel ? 'text-blue-800 font-medium' : 'text-gray-700'}>{item.name}</span>
                                    </div>
                                );
                            })
                        )}
                    </div>
                </div>
            )}
        </div>
    );
});

// ─── AddIssueModal ────────────────────────────────────────────────────────────

const AddIssueModal = memo(function AddIssueModal({
    isOpen, onClose, onSubmit, submitting,
    reasons, visibleOperators, visibleDirections,
    createDate, setCreateDate,
    createStartTime, setCreateStartTime,
    createEndTime, setCreateEndTime,
    createWorkplaceNumber, setCreateWorkplaceNumber,
    createReason, setCreateReason,
    createComment, setCreateComment,
    createOperatorIds, setCreateOperatorIds,
    createDirectionIds, setCreateDirectionIds,
    isMassive, setIsMassive,
}) {
    useEffect(() => {
        if (!isOpen) return;
        const handler = (e) => { if (e.key === 'Escape') onClose(); };
        document.addEventListener('keydown', handler);
        return () => document.removeEventListener('keydown', handler);
    }, [isOpen, onClose]);

    if (!isOpen) return null;

    const toggleMassive = () => {
        setIsMassive((m) => !m);
        setCreateOperatorIds([]);
        setCreateDirectionIds([]);
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            style={{ backgroundColor: 'rgba(15,23,42,0.55)', backdropFilter: 'blur(3px)' }}
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div
                className="w-full max-w-2xl rounded-2xl bg-white shadow-2xl border border-blue-100 flex flex-col"
                style={{ maxHeight: '92vh', animation: 'modalIn .2s ease' }}
            >
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 rounded-t-2xl bg-gradient-to-r from-blue-600 to-blue-700 shrink-0">
                    <h3 className="text-base font-bold text-white flex items-center gap-2">
                        <FaIcon className="fas fa-tools" style={{ width: '1em', height: '1em' }} />
                        Добавить техническую причину
                    </h3>
                    <button
                        type="button"
                        onClick={onClose}
                        className="rounded-lg p-1.5 text-white/60 hover:text-white hover:bg-white/20 transition-colors"
                        aria-label="Закрыть"
                    >
                        <FaIcon className="fas fa-times" style={{ width: '1em', height: '1em' }} />
                    </button>
                </div>

                {/* Scrollable body */}
                <div className="overflow-y-auto flex-1">
                    <form id="add-issue-form" onSubmit={onSubmit} className="p-6 space-y-5">

                        {/* Date + time row */}
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                            <label className="block">
                                <span className={LABEL_CLASS}>Дата проблемы</span>
                                <input
                                    type="date"
                                    value={createDate}
                                    onChange={(e) => setCreateDate(e.target.value)}
                                    className={INPUT_CLASS}
                                    required
                                />
                            </label>
                            <label className="block">
                                <span className={LABEL_CLASS}>Начало</span>
                                <input
                                    type="time"
                                    value={createStartTime}
                                    onChange={(e) => setCreateStartTime(e.target.value)}
                                    className={INPUT_CLASS}
                                    required
                                />
                            </label>
                            <label className="block">
                                <span className={LABEL_CLASS}>Конец</span>
                                <input
                                    type="time"
                                    value={createEndTime}
                                    onChange={(e) => setCreateEndTime(e.target.value)}
                                    className={INPUT_CLASS}
                                    required
                                />
                            </label>
                            <label className="block">
                                <span className={LABEL_CLASS}>Рабочее место</span>
                                <select
                                    value={createWorkplaceNumber}
                                    onChange={(e) => setCreateWorkplaceNumber(e.target.value)}
                                    className={INPUT_CLASS}
                                >
                                    <option value="">Не указано</option>
                                    {WORKPLACE_NUMBERS.map((num) => (
                                        <option key={`create-workplace-${num}`} value={num}>
                                            РМ {num}
                                        </option>
                                    ))}
                                </select>
                            </label>
                        </div>

                        {/* Reason dropdown */}
                        <div>
                            <span className={LABEL_CLASS}>Техническая причина</span>
                            <CustomReasonDropdown
                                value={createReason}
                                onChange={setCreateReason}
                                reasons={reasons}
                                placeholder="Выберите или найдите причину..."
                                required
                            />
                        </div>

                        {/* Massive toggle + selection */}
                        <div className="rounded-xl border border-blue-200 bg-gradient-to-br from-blue-50/80 to-slate-50 p-4">
                            <div className="flex items-center justify-between">
                                <div>
                                    <div className="flex items-center gap-2 text-sm font-bold text-blue-900">
                                        <FaIcon className="fas fa-users text-blue-500" style={{ width: '1em', height: '1em' }} />
                                        Массовая проблема
                                    </div>
                                    <div className="text-xs text-gray-500 mt-0.5">
                                        {isMassive ? 'Проблема затрагивает всё направление' : 'Назначить конкретным операторам'}
                                    </div>
                                </div>

                                {/* Toggle switch */}
                                <button
                                    type="button"
                                    role="switch"
                                    aria-checked={isMassive}
                                    onClick={toggleMassive}
                                    className={`relative inline-flex h-[26px] w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${isMassive ? 'bg-blue-600' : 'bg-gray-200'}`}
                                >
                                    <span
                                        className={`pointer-events-none inline-block h-[22px] w-[22px] transform rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out ${isMassive ? 'translate-x-5' : 'translate-x-0'}`}
                                    />
                                </button>
                            </div>

                            <div className="mt-4">
                                <span className={LABEL_CLASS}>
                                    {isMassive ? 'Направления (мультивыбор)' : 'Операторы (мультивыбор)'}
                                </span>
                                <div className="mt-1">
                                    {isMassive ? (
                                        <CustomMultiSelect
                                            items={visibleDirections}
                                            selectedIds={createDirectionIds}
                                            onChange={setCreateDirectionIds}
                                            placeholder="Выберите направления..."
                                            emptyText="Нет доступных направлений"
                                        />
                                    ) : (
                                        <CustomMultiSelect
                                            items={visibleOperators}
                                            selectedIds={createOperatorIds}
                                            onChange={setCreateOperatorIds}
                                            placeholder="Выберите операторов..."
                                            emptyText="Нет доступных операторов"
                                        />
                                    )}
                                </div>
                            </div>
                        </div>

                        {/* Comment */}
                        <label className="block">
                            <span className={LABEL_CLASS}>Комментарий (необязательно)</span>
                            <textarea
                                value={createComment}
                                onChange={(e) => setCreateComment(e.target.value)}
                                rows={2}
                                className={INPUT_CLASS}
                                placeholder="Дополнительное описание проблемы"
                            />
                        </label>
                    </form>
                </div>

                {/* Footer */}
                <div className="shrink-0 flex justify-end gap-3 px-6 py-4 border-t border-blue-100 bg-gray-50/60 rounded-b-2xl">
                    <button
                        type="button"
                        onClick={onClose}
                        className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
                    >
                        Отмена
                    </button>
                    <button
                        type="submit"
                        form="add-issue-form"
                        disabled={submitting}
                        className={`inline-flex items-center gap-2 rounded-lg px-5 py-2 text-sm font-semibold text-white transition-colors ${submitting ? 'bg-blue-400 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700'}`}
                    >
                        <FaIcon className={`fas ${submitting ? 'fa-spinner fa-spin' : 'fa-save'}`} style={{ width: '0.9em', height: '0.9em' }} />
                        {submitting ? 'Сохранение...' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </div>
    );
});

// ─── AnalyticsPanel ─────────────────────────────────────────────────────────

const REASON_COLORS = [
    '#3b82f6', '#6366f1', '#8b5cf6', '#ec4899',
    '#f59e0b', '#10b981', '#06b6d4', '#ef4444',
    '#84cc16', '#f97316',
];

const getWorkplaceTileStyle = (count, maxCount) => {
    if (!count || count <= 0 || maxCount <= 0) {
        return {
            backgroundColor: '#f8fafc',
            borderColor: '#e2e8f0',
            color: '#64748b',
        };
    }
    const ratio = Math.max(0, Math.min(1, count / maxCount));
    const alpha = 0.2 + ratio * 0.78;
    return {
        backgroundColor: `rgba(220, 38, 38, ${alpha.toFixed(3)})`,
        borderColor: ratio > 0.7 ? 'rgba(127, 29, 29, 0.85)' : 'rgba(220, 38, 38, 0.45)',
        color: ratio > 0.5 ? '#ffffff' : '#7f1d1d',
    };
};

const WorkplaceAnalyticsPanel = memo(function WorkplaceAnalyticsPanel({ rows }) {
    const [selectedWorkplace, setSelectedWorkplace] = useState(null);

    const workplaceStats = useMemo(() => {
        const buckets = new Map();

        for (const row of rows) {
            const workplaceNumber = normalizeWorkplaceNumber(row?.workplace_number);
            if (workplaceNumber === null) continue;

            const durationMinutes = calcDurationMinutes(row?.start_time, row?.end_time);
            if (!buckets.has(workplaceNumber)) {
                buckets.set(workplaceNumber, {
                    workplaceNumber,
                    incidents: 0,
                    totalMinutes: 0,
                    rows: [],
                });
            }
            const entry = buckets.get(workplaceNumber);
            entry.incidents += 1;
            if (durationMinutes !== null && durationMinutes >= 0) entry.totalMinutes += durationMinutes;
            entry.rows.push(row);
        }

        const items = WORKPLACE_NUMBERS.map((num) => {
            const entry = buckets.get(num);
            return entry || {
                workplaceNumber: num,
                incidents: 0,
                totalMinutes: 0,
                rows: [],
            };
        });

        const activeItems = items.filter((item) => item.incidents > 0);
        const maxIncidents = Math.max(...items.map((item) => item.incidents), 0);
        const totalIncidents = items.reduce((sum, item) => sum + item.incidents, 0);

        const topItems = [...activeItems]
            .sort((a, b) => (
                b.incidents - a.incidents
                || b.totalMinutes - a.totalMinutes
                || a.workplaceNumber - b.workplaceNumber
            ))
            .slice(0, 5);

        return {
            items,
            activeItems,
            activeCount: activeItems.length,
            maxIncidents,
            totalIncidents,
            topItems,
        };
    }, [rows]);

    const selectedEntry = useMemo(() => {
        if (selectedWorkplace === null) return null;
        return workplaceStats.items.find((item) => item.workplaceNumber === selectedWorkplace) || null;
    }, [selectedWorkplace, workplaceStats.items]);

    useEffect(() => {
        if (selectedWorkplace === null) return;
        if (!selectedEntry || selectedEntry.incidents <= 0) {
            setSelectedWorkplace(null);
        }
    }, [selectedEntry, selectedWorkplace]);

    if (rows.length === 0) return null;

    return (
        <div className="mb-6 rounded-xl border-2 border-rose-200 bg-white shadow-lg overflow-hidden">
            <div className="px-5 py-3 border-b border-rose-100 bg-gradient-to-r from-rose-50 to-red-50 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm font-bold uppercase tracking-wide text-rose-900">
                    <FaIcon className="fas fa-th" style={{ width: '1em', height: '1em' }} />
                    Аналитика по рабочим местам
                </div>
                <div className="flex flex-wrap gap-2">
                    <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 border border-rose-200 px-3 py-0.5 text-xs font-semibold text-rose-800">
                        <FaIcon className="fas fa-list" style={{ width: '0.8em', height: '0.8em' }} />
                        Инцидентов: {workplaceStats.totalIncidents}
                    </span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-red-100 border border-red-200 px-3 py-0.5 text-xs font-semibold text-red-800">
                        <FaIcon className="fas fa-desktop" style={{ width: '0.8em', height: '0.8em' }} />
                        Активных РМ: {workplaceStats.activeCount}
                    </span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 border border-slate-200 px-3 py-0.5 text-xs font-semibold text-slate-700">
                        Чем больше инцидентов, тем краснее ячейка.
                    </span>
                </div>
            </div>

            <div className="p-5 space-y-4">
                <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 md:grid-cols-6 lg:grid-cols-10">
                    {workplaceStats.items.map((item) => {
                        const isSelected = item.workplaceNumber === selectedWorkplace;
                        const tileStyle = getWorkplaceTileStyle(item.incidents, workplaceStats.maxIncidents);
                        return (
                            <button
                                key={`workplace-tile-${item.workplaceNumber}`}
                                type="button"
                                onClick={() => {
                                    if (item.incidents <= 0) return;
                                    setSelectedWorkplace((prev) => (prev === item.workplaceNumber ? null : item.workplaceNumber));
                                }}
                                title={item.incidents > 0
                                    ? `РМ ${item.workplaceNumber}: ${item.incidents} инцидент(ов)`
                                    : `РМ ${item.workplaceNumber}: инцидентов нет`}
                                className={`rounded-lg border px-2 py-2 text-left transition-all ${
                                    item.incidents > 0 ? 'hover:-translate-y-0.5' : 'cursor-default'
                                } ${isSelected ? 'ring-2 ring-rose-400 shadow-md' : 'shadow-sm'}`}
                                style={tileStyle}
                            >
                                <div className="text-[10px] font-semibold uppercase tracking-wide opacity-80">
                                    РМ {item.workplaceNumber}
                                </div>
                                <div className="text-base font-extrabold leading-tight">
                                    {item.incidents}
                                </div>
                            </button>
                        );
                    })}
                </div>

                {workplaceStats.topItems.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                        {workplaceStats.topItems.map((item, idx) => (
                            <button
                                key={`top-workplace-${item.workplaceNumber}`}
                                type="button"
                                onClick={() => setSelectedWorkplace(item.workplaceNumber)}
                                className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-semibold transition-colors ${
                                    item.workplaceNumber === selectedWorkplace
                                        ? 'border-rose-400 bg-rose-100 text-rose-800'
                                        : 'border-rose-200 bg-white text-rose-700 hover:bg-rose-50'
                                }`}
                            >
                                #{idx + 1} РМ {item.workplaceNumber}: {item.incidents}
                            </button>
                        ))}
                    </div>
                )}

                {selectedEntry && selectedEntry.incidents > 0 && (
                    <div className="rounded-lg border border-rose-200 bg-rose-50/60 p-4">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="text-sm font-bold text-rose-900">
                                РМ {selectedEntry.workplaceNumber}: {selectedEntry.incidents} инцидент(ов)
                            </div>
                            <div className="text-xs font-semibold text-rose-700">
                                Суммарно: {formatDuration(selectedEntry.totalMinutes)}
                            </div>
                        </div>

                        <div className="mt-3 space-y-2 max-h-60 overflow-y-auto pr-1">
                            {selectedEntry.rows.map((row, idx) => {
                                const key = row?.id ? `workplace-row-${row.id}` : `workplace-row-${selectedEntry.workplaceNumber}-${idx}`;
                                const timeText = (row?.start_time && row?.end_time) ? `${row.start_time} - ${row.end_time}` : '—';
                                return (
                                    <div key={key} className="rounded-md border border-rose-100 bg-white p-2.5 text-xs">
                                        <div className="font-semibold text-slate-800">
                                            {row?.date || '—'} • {timeText} • {row?.operator_name || '—'}
                                        </div>
                                        <div className="mt-1 text-slate-700">{row?.reason || '—'}</div>
                                        {!!row?.comment && (
                                            <div className="mt-1 text-slate-500 line-clamp-2">Комментарий: {row.comment}</div>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {workplaceStats.activeCount === 0 && (
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
                        В выбранном периоде нет инцидентов с указанным рабочим местом.
                    </div>
                )}
            </div>
        </div>
    );
});

const AnalyticsPanel = memo(function AnalyticsPanel({ rows }) {
    const stats = useMemo(() => {
        const map = new Map();
        for (const row of rows) {
            const reason = String(row?.reason || '—').trim();
            const dur = calcDurationMinutes(row?.start_time, row?.end_time);
            if (!map.has(reason)) {
                map.set(reason, { reason, totalMinutes: 0, count: 0, operatorSet: new Set() });
            }
            const entry = map.get(reason);
            entry.count += 1;
            if (dur !== null && dur >= 0) entry.totalMinutes += dur;
            if (row?.operator_name) entry.operatorSet.add(row.operator_name);
        }
        const arr = Array.from(map.values())
            .map((e) => ({ ...e, operators: e.operatorSet.size }))
            .sort((a, b) => b.totalMinutes - a.totalMinutes || b.count - a.count);
        const maxMinutes = Math.max(...arr.map((e) => e.totalMinutes), 1);
        return { items: arr, maxMinutes };
    }, [rows]);

    const totalMinutes = useMemo(() => stats.items.reduce((s, e) => s + e.totalMinutes, 0), [stats]);
    const totalCount   = rows.length;

    if (rows.length === 0) return null;

    return (
        <div className="mb-10 rounded-xl border-2 border-blue-200 bg-white shadow-lg overflow-hidden">
            <div className="px-5 py-3 border-b border-blue-100 bg-gradient-to-r from-blue-50 to-indigo-50 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm font-bold uppercase tracking-wide text-blue-900">
                    <FaIcon className="fas fa-chart-bar" style={{ width: '1em', height: '1em' }} />
                    Аналитика по причинам
                </div>
                <div className="flex flex-wrap gap-2">
                    <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 border border-blue-200 px-3 py-0.5 text-xs font-semibold text-blue-800">
                        <FaIcon className="fas fa-clock" style={{ width: '0.8em', height: '0.8em' }} />
                        Всего: {formatDuration(totalMinutes)}
                    </span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 border border-indigo-200 px-3 py-0.5 text-xs font-semibold text-indigo-800">
                        <FaIcon className="fas fa-list" style={{ width: '0.8em', height: '0.8em' }} />
                        Инцидентов: {totalCount}
                    </span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 border border-slate-200 px-3 py-0.5 text-xs font-semibold text-slate-700">
                        <FaIcon className="fas fa-tools" style={{ width: '0.8em', height: '0.8em' }} />
                        Причин: {stats.items.length}
                    </span>
                </div>
            </div>

            <div className="p-5 space-y-4">
                {stats.items.map((entry, i) => {
                    const pct = stats.maxMinutes > 0 ? (entry.totalMinutes / stats.maxMinutes) * 100 : 0;
                    const totalPct = totalMinutes > 0 ? ((entry.totalMinutes / totalMinutes) * 100).toFixed(1) : '0.0';
                    const color = REASON_COLORS[i % REASON_COLORS.length];
                    const massive = isMassiveReason(entry.reason);
                    return (
                        <div key={entry.reason}>
                            <div className="flex items-center justify-between gap-2 mb-1">
                                <div className="flex items-center gap-2 min-w-0">
                                    <span className="shrink-0 h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
                                    {massive && (
                                        <span className="shrink-0 rounded-full bg-amber-100 border border-amber-300 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-amber-700">
                                            Масс.
                                        </span>
                                    )}
                                    <span className="text-xs font-medium text-gray-700 truncate" title={entry.reason}>{entry.reason}</span>
                                </div>
                                <div className="flex items-center gap-3 shrink-0">
                                    <span className="text-xs font-bold text-gray-800 w-16 text-right">{formatDuration(entry.totalMinutes)}</span>
                                    <span className="text-[11px] text-gray-400 w-8 text-right">{entry.count} сл.</span>
                                    <span className="text-[11px] font-bold text-blue-600 w-10 text-right">{totalPct}%</span>
                                </div>
                            </div>
                            <div className="relative h-5 w-full rounded-full bg-gray-100 overflow-hidden">
                                <div
                                    className="absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out"
                                    style={{ width: `${pct}%`, backgroundColor: color, opacity: 0.85 }}
                                />
                                {pct > 8 && (
                                    <div className="absolute inset-0 flex items-center px-2.5">
                                        <span className="text-[11px] font-semibold text-white drop-shadow-sm">{formatDuration(entry.totalMinutes)}</span>
                                    </div>
                                )}
                            </div>
                            <div className="flex gap-4 mt-0.5">
                                <span className="text-[10px] text-gray-400">Операторов: {entry.operators}</span>
                                {entry.count > 0 && (
                                    <span className="text-[10px] text-gray-400">Ср. длит: {formatDuration(Math.round(entry.totalMinutes / entry.count))}</span>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
});

// ─── TechnicalIssueRow ────────────────────────────────────────────────────────

const TechnicalIssueRow = memo(function TechnicalIssueRow({ item, canDelete, isDeleting, onDelete, isEven }) {
    const directionNames = Array.isArray(item?.selected_direction_names)
        ? item.selected_direction_names.filter((n) => String(n || '').trim() !== '')
        : [];
    const massive = isMassiveReason(item?.reason);
    const rowBg = isEven ? 'bg-white' : 'bg-slate-50/70';

    return (
        <tr className={`${rowBg} hover:bg-blue-50/50 transition-colors`}>
            <td className="px-3 py-2.5 text-xs font-semibold text-gray-700 whitespace-nowrap">
                {item?.date || '—'}
            </td>
            <td className="px-3 py-2.5 text-xs text-gray-500 whitespace-nowrap font-mono">
                {item?.time_range || ((item?.start_time && item?.end_time) ? `${item.start_time}–${item.end_time}` : '—')}
            </td>
            <td className="px-3 py-2.5 text-xs text-gray-700">
                <div className="font-semibold text-gray-800 leading-tight">{item?.operator_name || '—'}</div>
                {item?.direction_name && (
                    <div className="text-[11px] text-gray-400 leading-tight mt-0.5">{item.direction_name}</div>
                )}
            </td>
            <td className="px-3 py-2.5 text-xs text-gray-700 whitespace-nowrap">
                {normalizeWorkplaceNumber(item?.workplace_number) !== null ? (
                    <span className="inline-flex items-center rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 font-semibold text-rose-700">
                        РМ {normalizeWorkplaceNumber(item?.workplace_number)}
                    </span>
                ) : (
                    <span className="text-gray-300">—</span>
                )}
            </td>
            <td className="px-3 py-2.5 text-xs text-gray-700" style={{ maxWidth: 220 }}>
                <div className="flex flex-wrap items-start gap-1">
                    {massive && (
                        <span className="shrink-0 rounded-full bg-amber-100 border border-amber-300 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-amber-700">
                            Масс.
                        </span>
                    )}
                    <span className="line-clamp-2 leading-snug">{item?.reason || '—'}</span>
                </div>
            </td>
            <td className="px-3 py-2.5 text-xs text-gray-500" style={{ maxWidth: 160 }}>
                <span className="line-clamp-2 leading-snug">{item?.comment || <span className="text-gray-300">—</span>}</span>
            </td>
            <td className="px-3 py-2.5 text-xs text-gray-500" style={{ maxWidth: 140 }}>
                <span className="line-clamp-2 leading-snug">
                    {directionNames.length > 0 ? directionNames.join(', ') : <span className="text-gray-300">—</span>}
                </span>
            </td>
            <td className="px-3 py-2.5 text-xs text-gray-600 whitespace-nowrap">
                {item?.created_by_name || '—'}
            </td>
            <td className="px-3 py-2.5 text-[11px] text-gray-400 whitespace-nowrap">
                {item?.created_at || '—'}
            </td>
            {canDelete && (
                <td className="px-3 py-2.5 text-right whitespace-nowrap">
                    <button
                        type="button"
                        onClick={() => onDelete(item)}
                        disabled={isDeleting}
                        title="Удалить"
                        aria-label="Удалить техсбой"
                        className={`inline-flex items-center justify-center rounded-lg border p-1.5 transition-colors ${
                            isDeleting
                                ? 'cursor-not-allowed border-red-100 bg-red-50 text-red-300'
                                : 'border-red-200 bg-white text-red-400 hover:bg-red-50 hover:text-red-600'
                        }`}
                    >
                        <FaIcon
                            className={`fas ${isDeleting ? 'fa-spinner fa-spin' : 'fa-trash'}`}
                            style={{ width: '0.85em', height: '0.85em' }}
                        />
                    </button>
                </td>
            )}
        </tr>
    );
});

// ─── Main component ───────────────────────────────────────────────────────────

const TechnicalIssuesView = ({ user, operators = [], directions = [], showToast, apiBaseUrl, withAccessTokenHeader }) => {
    const role = normalizeRole(user?.role);
    const canCreate = isAdminLikeRole(role) || isSupervisorRole(role);
    const canView   = isAdminLikeRole(role) || isSupervisorRole(role);
    const canExport = isAdminLikeRole(role) || isSupervisorRole(role); // admins + super_admins + supervisors
    const canDelete = isAdminLikeRole(role) || isSupervisorRole(role);

    const showToastRef = useRef(showToast);
    useEffect(() => { showToastRef.current = showToast; }, [showToast]);

    const notify = useCallback((message, type = 'info') => {
        if (typeof showToastRef.current === 'function') showToastRef.current(message, type);
    }, []);

    const buildHeaders = useCallback(() => {
        const base = {};
        if (user?.apiKey && String(user.apiKey).trim() !== '') base['X-API-Key'] = user.apiKey;
        if (user?.id !== undefined && user?.id !== null && String(user.id).trim() !== '') base['X-User-Id'] = user.id;
        if (typeof withAccessTokenHeader === 'function') return withAccessTokenHeader(base);
        return base;
    }, [user?.apiKey, user?.id, withAccessTokenHeader]);

    const visibleOperators = useMemo(() => {
        const list = Array.isArray(operators) ? operators : [];
        return list
            .filter((op) => String(op?.role || 'operator').trim().toLowerCase() === 'operator')
            .sort((a, b) => String(a?.name || '').localeCompare(String(b?.name || ''), 'ru', { sensitivity: 'base' }));
    }, [operators]);

    const visibleDirections = useMemo(() => {
        const list = Array.isArray(directions) ? directions : [];
        const allowedIds = new Set(
            visibleOperators.map((op) => Number(op?.direction_id)).filter((id) => Number.isFinite(id) && id > 0)
        );
        return list
            .filter((dir) => {
                const id = Number(dir?.id);
                if (!Number.isFinite(id) || id <= 0) return false;
                if (isAdminLikeRole(role) || isSupervisorRole(role)) return true;
                return allowedIds.has(id);
            })
            .sort((a, b) => String(a?.name || '').localeCompare(String(b?.name || ''), 'ru', { sensitivity: 'base' }));
    }, [directions, role, visibleOperators]);

    const initialFilters = useMemo(() => ({
        dateFrom: currentMonthStartIso(),
        dateTo: toIsoDate(new Date()),
        operatorId: '',
        reason: '',
        workplaceNumber: '',
    }), []);

    // ── state ──
    const [reasons, setReasons]           = useState(FALLBACK_TECHNICAL_REASONS);
    const [rows, setRows]                 = useState([]);
    const [total, setTotal]               = useState(0);
    const [loading, setLoading]           = useState(false);
    const [submitting, setSubmitting]     = useState(false);
    const [exporting, setExporting]       = useState(false);
    const [deletingId, setDeletingId]     = useState(null);
    const [isModalOpen, setIsModalOpen]   = useState(false);

    // form state
    const [createDate, setCreateDate]             = useState(() => toIsoDate(new Date()));
    const [createStartTime, setCreateStartTime]   = useState('00:00');
    const [createEndTime, setCreateEndTime]       = useState('23:59');
    const [createWorkplaceNumber, setCreateWorkplaceNumber] = useState('');
    const [createReason, setCreateReason]         = useState('');
    const [createComment, setCreateComment]       = useState('');
    const [createOperatorIds, setCreateOperatorIds] = useState([]);
    const [createDirectionIds, setCreateDirectionIds] = useState([]);
    const [isMassive, setIsMassive]               = useState(false);

    // filter state
    const [filterDraft, setFilterDraft]       = useState(initialFilters);
    const [appliedFilters, setAppliedFilters] = useState(initialFilters);

    const hasPending = useMemo(() => !areFiltersEqual(filterDraft, appliedFilters), [filterDraft, appliedFilters]);

    const latestReqId    = useRef(0);
    const lastQueryRef   = useRef('');

    useEffect(() => { lastQueryRef.current = ''; }, [apiBaseUrl, user?.id, user?.apiKey, canView]);

    // ── fetch reasons ──
    const fetchReasons = useCallback(async () => {
        if (!canView) return;
        try {
            const res = await axios.get(`${apiBaseUrl}/api/technical_issues/reasons`, { headers: buildHeaders() });
            const next = Array.isArray(res?.data?.reasons) ? res.data.reasons : [];
            if (next.length > 0) setReasons((p) => areStringListsEqual(p, next) ? p : next);
            else setReasons((p) => areStringListsEqual(p, FALLBACK_TECHNICAL_REASONS) ? p : FALLBACK_TECHNICAL_REASONS);
        } catch {
            setReasons((p) => areStringListsEqual(p, FALLBACK_TECHNICAL_REASONS) ? p : FALLBACK_TECHNICAL_REASONS);
        }
    }, [apiBaseUrl, buildHeaders, canView]);

    // ── fetch rows ──
    const fetchRows = useCallback(async (filters, { force = false } = {}) => {
        if (!canView) return;
        const query = buildFilterQuery(filters);
        const key = query.toString();
        if (!force && key === lastQueryRef.current) return;
        lastQueryRef.current = key;
        const reqId = ++latestReqId.current;
        setLoading(true);
        try {
            const res = await axios.get(`${apiBaseUrl}/api/technical_issues?${key}`, { headers: buildHeaders() });
            if (reqId !== latestReqId.current) return;
            const items = Array.isArray(res?.data?.items) ? res.data.items : [];
            setRows(items);
            setTotal(Number(res?.data?.total || items.length || 0));
            const nextReasons = Array.isArray(res?.data?.reasons) ? res.data.reasons : [];
            if (nextReasons.length > 0) setReasons((p) => areStringListsEqual(p, nextReasons) ? p : nextReasons);
        } catch (err) {
            if (reqId !== latestReqId.current) return;
            notify(err?.response?.data?.error || 'Не удалось загрузить список технических причин', 'error');
        } finally {
            if (reqId === latestReqId.current) setLoading(false);
        }
    }, [apiBaseUrl, buildHeaders, canView, notify]);

    useEffect(() => { if (canView) fetchReasons(); }, [canView, fetchReasons]);
    useEffect(() => { if (canView) fetchRows(appliedFilters); }, [appliedFilters, canView, fetchRows]);

    // ── filter handlers ──
    const updateDraft = useCallback((field, value) => {
        setFilterDraft((prev) => prev[field] === value ? prev : { ...prev, [field]: value });
    }, []);

    const handleApplyFilters = useCallback(async () => {
        if (areFiltersEqual(filterDraft, appliedFilters)) { await fetchRows(filterDraft, { force: true }); return; }
        setAppliedFilters(normalizeFilterPayload(filterDraft));
    }, [appliedFilters, fetchRows, filterDraft]);

    const handleResetFilters = useCallback(async () => {
        const reset = { dateFrom: currentMonthStartIso(), dateTo: toIsoDate(new Date()), operatorId: '', reason: '', workplaceNumber: '' };
        setFilterDraft(reset);
        if (areFiltersEqual(reset, appliedFilters)) { await fetchRows(reset, { force: true }); return; }
        setAppliedFilters(reset);
    }, [appliedFilters, fetchRows]);

    // ── modal reset helper ──
    const resetForm = useCallback(() => {
        setCreateDate(toIsoDate(new Date()));
        setCreateStartTime('00:00');
        setCreateEndTime('23:59');
        setCreateWorkplaceNumber('');
        setCreateReason('');
        setCreateComment('');
        setCreateOperatorIds([]);
        setCreateDirectionIds([]);
        setIsMassive(false);
    }, []);

    const openModal = () => { resetForm(); setIsModalOpen(true); };
    const closeModal = () => setIsModalOpen(false);

    // ── create issue ──
    const handleCreateIssue = useCallback(async (event) => {
        event.preventDefault();
        if (!canCreate) return;
        if (!createDate) { notify('Укажите дату технической причины', 'error'); return; }
        if (!createStartTime || !createEndTime) { notify('Укажите время начала и окончания', 'error'); return; }
        if (createStartTime === createEndTime) { notify('Время начала и окончания не должно совпадать', 'error'); return; }
        if (!createReason) { notify('Выберите техническую причину', 'error'); return; }
        const workplaceNumber = normalizeWorkplaceNumber(createWorkplaceNumber);
        if (String(createWorkplaceNumber || '').trim() && workplaceNumber === null) {
            notify('Номер рабочего места должен быть от 1 до 30', 'error');
            return;
        }
        if (createOperatorIds.length === 0 && createDirectionIds.length === 0) {
            notify('Выберите операторов или направления', 'error'); return;
        }
        setSubmitting(true);
        try {
            const payload = {
                date: createDate,
                start_time: createStartTime,
                end_time: createEndTime,
                reason: createReason,
                comment: createComment || null,
                workplace_number: workplaceNumber,
                operator_ids: toIntList(createOperatorIds),
                direction_ids: toIntList(createDirectionIds),
            };
            const res = await axios.post(`${apiBaseUrl}/api/technical_issues`, payload, { headers: buildHeaders() });
            const count = Number(res?.data?.result?.created_count || 0);
            notify(count > 0 ? `Сохранено записей: ${count}` : 'Техническая причина сохранена', 'success');
            closeModal();
            await fetchRows(appliedFilters, { force: true });
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось сохранить техническую причину', 'error');
        } finally {
            setSubmitting(false);
        }
    }, [apiBaseUrl, appliedFilters, buildHeaders, canCreate, createComment, createDate, createDirectionIds, createEndTime, createOperatorIds, createReason, createStartTime, createWorkplaceNumber, fetchRows, notify]);

    // ── export ──
    const handleExport = useCallback(async () => {
        if (!canExport) return;
        setExporting(true);
        try {
            const query = buildFilterQuery(appliedFilters);
            const res = await axios.get(`${apiBaseUrl}/api/technical_issues/export_excel?${query.toString()}`, {
                headers: buildHeaders(), responseType: 'blob',
            });
            const blob = new Blob([res.data], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = `technical_issues_${toIsoDate(new Date())}.xlsx`;
            document.body.appendChild(a); a.click(); a.remove();
            window.URL.revokeObjectURL(url);
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось выгрузить Excel', 'error');
        } finally {
            setExporting(false);
        }
    }, [apiBaseUrl, appliedFilters, buildHeaders, canExport, notify]);

    // ── delete ──
    const handleDeleteIssue = useCallback(async (issue) => {
        if (!canDelete) return;
        const id = Number(issue?.id);
        if (!Number.isFinite(id) || id <= 0) return;
        const name = String(issue?.operator_name || '').trim();
        const date = String(issue?.date || '').trim();
        const msg = name ? `Удалить техсбой оператора "${name}"${date ? ` (${date})` : ''}?` : `Удалить техсбой${date ? ` (${date})` : ''}?`;
        if (typeof window !== 'undefined' && !window.confirm(msg)) return;
        setDeletingId(id);
        try {
            await axios.delete(`${apiBaseUrl}/api/technical_issues/${id}`, { headers: buildHeaders() });
            notify('Техсбой удален', 'success');
            await fetchRows(appliedFilters, { force: true });
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось удалить техсбой', 'error');
        } finally {
            setDeletingId(null);
        }
    }, [apiBaseUrl, appliedFilters, buildHeaders, canDelete, fetchRows, notify]);

    // ── guard ──
    if (!canView) {
        return (
            <div className="mt-6 rounded-xl border-2 border-blue-200 bg-blue-50 p-6 shadow">
                <div className="text-sm text-gray-700">Раздел доступен только администраторам и супервайзерам.</div>
            </div>
        );
    }

    return (
        <>
            <style>{`
                @keyframes modalIn {
                    from { opacity: 0; transform: scale(0.96) translateY(-8px); }
                    to   { opacity: 1; transform: scale(1)    translateY(0);    }
                }
                @keyframes fadeInDown {
                    from { opacity: 0; transform: translateY(-6px); }
                    to   { opacity: 1; transform: translateY(0);    }
                }
            `}</style>

            {/* Add modal */}
            <AddIssueModal
                isOpen={isModalOpen}
                onClose={closeModal}
                onSubmit={handleCreateIssue}
                submitting={submitting}
                reasons={reasons}
                visibleOperators={visibleOperators}
                visibleDirections={visibleDirections}
                createDate={createDate}          setCreateDate={setCreateDate}
                createStartTime={createStartTime} setCreateStartTime={setCreateStartTime}
                createEndTime={createEndTime}     setCreateEndTime={setCreateEndTime}
                createWorkplaceNumber={createWorkplaceNumber} setCreateWorkplaceNumber={setCreateWorkplaceNumber}
                createReason={createReason}       setCreateReason={setCreateReason}
                createComment={createComment}     setCreateComment={setCreateComment}
                createOperatorIds={createOperatorIds} setCreateOperatorIds={setCreateOperatorIds}
                createDirectionIds={createDirectionIds} setCreateDirectionIds={setCreateDirectionIds}
                isMassive={isMassive}             setIsMassive={setIsMassive}
            />

            <div className="mt-6 space-y-4">
                {/* ── Header card ── */}
                <div className="rounded-xl border-2 border-blue-300 bg-blue-50 shadow-lg p-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <h2 className="text-xl font-bold text-blue-800 tracking-wide uppercase flex items-center gap-2">
                            <FaIcon className="fas fa-tools" style={{ width: '1em', height: '1em' }} />
                            Тех причины
                        </h2>
                        <div className="flex items-center gap-2">
                            <span className="inline-flex items-center rounded-full border border-blue-300 bg-white px-3 py-1 text-xs font-semibold text-blue-700">
                                Всего: {total}
                            </span>
                            {canCreate && (
                                <button
                                    type="button"
                                    onClick={openModal}
                                    className="inline-flex items-center gap-2 rounded-lg bg-blue-600 hover:bg-blue-700 px-4 py-2 text-sm font-semibold text-white shadow transition-colors"
                                >
                                    <FaIcon className="fas fa-plus" style={{ width: '0.85em', height: '0.85em' }} />
                                    Добавить
                                </button>
                            )}
                        </div>
                    </div>
                    <p className="mt-2 text-sm text-gray-600">
                        Фиксация технических проблем операторов, массовое добавление по направлениям и экспорт журнала.
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                        <span className="rounded-full bg-white border border-blue-200 px-3 py-0.5 text-xs text-blue-700 font-medium">Операторов: {visibleOperators.length}</span>
                        <span className="rounded-full bg-white border border-blue-200 px-3 py-0.5 text-xs text-blue-700 font-medium">Направлений: {visibleDirections.length}</span>
                        <span className="rounded-full bg-white border border-blue-200 px-3 py-0.5 text-xs text-blue-700 font-medium">Причин: {reasons.length}</span>
                    </div>
                </div>

                {/* ── Filters card ── */}
                <div className="sticky top-0 z-10 rounded-xl border border-blue-200 bg-blue-50/95 shadow px-4 py-3" style={{ backdropFilter: 'blur(6px)' }}>
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                        {/* Date range */}
                        <div className="md:col-span-1">
                            <span className={LABEL_CLASS}>Период</span>
                            <DateRangePicker
                                dateFrom={filterDraft.dateFrom}
                                dateTo={filterDraft.dateTo}
                                onChange={({ dateFrom, dateTo }) => {
                                    setFilterDraft((p) => ({ ...p, dateFrom, dateTo }));
                                }}
                            />
                        </div>

                        {/* Operator */}
                        <label className="block">
                            <span className={LABEL_CLASS}>Оператор</span>
                            <select
                                value={filterDraft.operatorId}
                                onChange={(e) => updateDraft('operatorId', e.target.value)}
                                className={INPUT_CLASS}
                            >
                                <option value="">Все операторы</option>
                                {visibleOperators.map((op) => (
                                    <option key={op.id} value={op.id}>{op.name}</option>
                                ))}
                            </select>
                        </label>

                        {/* Reason */}
                        <label className="block">
                            <span className={LABEL_CLASS}>Причина</span>
                            <select
                                value={filterDraft.reason}
                                onChange={(e) => updateDraft('reason', e.target.value)}
                                className={INPUT_CLASS}
                            >
                                <option value="">Все причины</option>
                                {reasons.map((r, i) => (
                                    <option key={`fr-${i}`} value={r}>{r}</option>
                                ))}
                            </select>
                        </label>

                        <label className="block">
                            <span className={LABEL_CLASS}>Рабочее место</span>
                            <select
                                value={filterDraft.workplaceNumber}
                                onChange={(e) => updateDraft('workplaceNumber', e.target.value)}
                                className={INPUT_CLASS}
                            >
                                <option value="">Все РМ</option>
                                {WORKPLACE_NUMBERS.map((num) => (
                                    <option key={`filter-workplace-${num}`} value={num}>
                                        РМ {num}
                                    </option>
                                ))}
                            </select>
                        </label>
                    </div>

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                        <button
                            type="button"
                            onClick={handleApplyFilters}
                            className="inline-flex items-center gap-2 rounded-lg bg-slate-700 hover:bg-slate-800 px-4 py-2 text-sm font-medium text-white transition-colors"
                        >
                            <FaIcon className="fas fa-filter" style={{ width: '0.85em', height: '0.85em' }} />
                            Применить
                        </button>
                        <button
                            type="button"
                            onClick={handleResetFilters}
                            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white hover:bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors"
                        >
                            <FaIcon className="fas fa-rotate" style={{ width: '0.85em', height: '0.85em' }} />
                            Сбросить
                        </button>
                        {canExport && (
                            <button
                                type="button"
                                onClick={handleExport}
                                disabled={exporting}
                                className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${exporting ? 'bg-emerald-400 cursor-not-allowed' : 'bg-emerald-600 hover:bg-emerald-700'}`}
                            >
                                <FaIcon className={`fas ${exporting ? 'fa-spinner fa-spin' : 'fa-file-excel'}`} style={{ width: '0.85em', height: '0.85em' }} />
                                {exporting ? 'Выгрузка...' : 'Excel'}
                            </button>
                        )}
                        <span className={`ml-auto inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${hasPending ? 'border-amber-200 bg-amber-100 text-amber-800' : 'border-green-200 bg-green-100 text-green-700'}`}>
                            {hasPending ? 'Не применено' : 'Применено'}
                        </span>
                    </div>
                </div>

                {/* ── Table card ── */}
                <div className="mb-10 rounded-xl border-2 border-blue-200 bg-white shadow-lg overflow-hidden">
                    <div className="px-5 py-3 border-b border-blue-100 bg-blue-50 flex items-center justify-between">
                        <div className="text-sm font-bold uppercase tracking-wide text-blue-900 flex items-center gap-2">
                            <FaIcon className="fas fa-table" style={{ width: '0.9em', height: '0.9em' }} />
                            Журнал тех причин
                        </div>
                        <div className="text-xs text-blue-600 font-semibold">Записей: {total}</div>
                    </div>

                    {loading ? (
                        <div className="flex items-center gap-2 p-6 text-sm text-gray-500">
                            <FaIcon className="fas fa-spinner fa-spin text-blue-500" style={{ width: '1em', height: '1em' }} />
                            Загрузка...
                        </div>
                    ) : rows.length === 0 ? (
                        <div className="p-10 text-center text-sm text-gray-400">Записей не найдено</div>
                    ) : (
                        <div className="overflow-x-auto" style={{ maxHeight: '65vh', overflowY: 'auto' }}>
                            <table className="min-w-full border-collapse">
                                <colgroup>
                                    <col style={{ width: 90 }} />
                                    <col style={{ width: 100 }} />
                                    <col style={{ width: 160 }} />
                                    <col style={{ width: 100 }} />
                                    <col style={{ minWidth: 200 }} />
                                    <col style={{ minWidth: 140 }} />
                                    <col style={{ minWidth: 120 }} />
                                    <col style={{ width: 120 }} />
                                    <col style={{ width: 110 }} />
                                    {canDelete && <col style={{ width: 50 }} />}
                                </colgroup>
                                <thead style={{ position: 'sticky', top: 0, zIndex: 5 }}>
                                    <tr className="bg-blue-700 text-white">
                                        {[
                                            'Дата', 'Время', 'Оператор', 'РМ', 'Причина',
                                            'Комментарий', 'Направления', 'Добавил', 'Зафиксировано',
                                        ].map((th) => (
                                            <th key={th} className="px-3 py-3 text-left text-[11px] font-bold uppercase tracking-wider whitespace-nowrap">
                                                {th}
                                            </th>
                                        ))}
                                        {canDelete && (
                                            <th className="px-3 py-3 text-right text-[11px] font-bold uppercase tracking-wider"></th>
                                        )}
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100">
                                    {rows.map((item, idx) => {
                                        const itemId = Number(item?.id);
                                        const key = Number.isFinite(itemId) && itemId > 0
                                            ? `ti-${itemId}`
                                            : `ti-fb-${idx}-${item?.date || ''}-${item?.operator_name || ''}`;
                                        return (
                                            <TechnicalIssueRow
                                                key={key}
                                                item={item}
                                                canDelete={canDelete}
                                                isDeleting={deletingId === item?.id}
                                                onDelete={handleDeleteIssue}
                                                isEven={idx % 2 === 0}
                                            />
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </div>

                {/* ── Analytics panels ── */}
                {!loading && rows.length > 0 && (
                    <>
                        <WorkplaceAnalyticsPanel rows={rows} />
                        <AnalyticsPanel rows={rows} />
                    </>
                )}
        </>
    );
};

export default memo(TechnicalIssuesView);
