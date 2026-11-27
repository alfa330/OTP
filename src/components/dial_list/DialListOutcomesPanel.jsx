import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard, iosInput, iosGroupLabel, iosBtnSecondary, iosBtnGhost, IosBadge, IosHint, IosSegmented, IosToggle } from '../ui/ios';
import {
    DUE_KIND_OPTIONS, OUTCOME_GROUPS_MAX, OUTCOME_GROUP_NAME_MAX, SUBTYPES_MAX, SUBTYPE_NAME_MAX, applyIdMap, isNewId,
    mainGroupKey, newGroupKey, normalizeDueKind, outcomeGroupOf, outcomeLabel, outcomeSubtypeName, outcomesSnapshot,
    rowKey, savedIdMap, serializeGroups, serializeOutcomes, serverHasSubtypes, subtypesOpen, subtypesToggleLabel,
    toEditable, toEditableGroups, validateOutcomes,
} from './outcomeFormat';

/*
 * Итоги звонка (запрос владельца 23.09.2026): после каждого разговора оператор в
 * iCORE Phone обязан выбрать итог из этого списка и может оставить комментарий.
 * Руководитель здесь задаёт названия, цвета и порядок кнопок в телефоне.
 *
 * Итоги не удаляются, а выключаются: на них ссылается история звонков. «Перезвонить»
 * — особый признак: водитель с таким итогом снова попадёт в порции через
 * «Повтор через, часов», счётчик попыток у него обнуляется.
 *
 * Типы итога (запрос владельца 29.09.2026): у итога может быть список вложенных
 * типов — у «Отказа», например, «Дорого», «Уже работает в другом парке». Если у
 * итога есть хоть один включённый тип, оператор в телефоне обязан выбрать и его.
 * Цвет и «перезвонить» у типа свои не заводим — они у итога. Типы, как и итоги,
 * не удаляются, а выключаются: на них ссылаются попытки в истории.
 *
 * «Нужна дата» (автообзвон Тез, контракт 15.10.2026; только при dueKinds): у
 * «Перезвонить» оператор обязан указать дату и время перезвона, у «Согласен выйти
 * на линию» — когда водитель выйдет. Водитель вернётся к этому же оператору к
 * сроку, поэтому итог с датой всегда «перезвонить» — переключатель тогда включён и
 * заперт (так же решает сервер). У удалённого КЦ выбора нет вовсе.
 *
 * Сохранение — само, без кнопки (20.11.2026: «причины в разделе Воронка Тез иногда
 * сбрасываются сами по себе»). Раньше у карточки была своя «Сохранить» под списком,
 * отдельная от «Сохранить» настроек выше: причины, добавленные без неё, жили только на
 * экране и пропадали при переходе на другую вкладку — у Тез КЦ за всё время на сервер не
 * дошло ни одной. Теперь каждая правка уходит на сервер через AUTOSAVE_MS после последней;
 * уход со вкладки и закрытие страницы досохраняют сразу. Новая строка без названия —
 * черновик (outcomeFormat.isDraftSubtype): не уходит и не держит остальное. Сервер
 * получает версию справочника, с которой начинали: список успел измениться (другая
 * вкладка) — отказ 409 и свежий список, а не тихое выключение чужих причин.
 *
 * Вечером того же дня «типы всё ещё исчезают» — на экране, в базе все 20 причин были на месте:
 *   • ключ строки был её id, а первое автосохранение меняет временный id на настоящий — React
 *     пересоздавал строку, курсор выпадал из поля, допечатанное после паузы терялось. Ключ теперь
 *     постоянный (outcomeFormat.rowKey);
 *   • список типов был свёрнут по умолчанию: вернулся на вкладку — видна одна серая строчка
 *     «Типы · 20». Теперь у итога с типами он раскрыт, пока его не свернут (outcomeFormat.subtypesOpen),
 *     а свёрнутое и раскрытое руками помнится до перезагрузки страницы (expandedByDepartment);
 *   • вернулся на вкладку раньше, чем дошло досохранение при уходе, — загрузка показала бы список без
 *     последней причины. Загрузка теперь ждёт сохранение своего отдела (pendingSaves).
 *
 * Вкладки итогов (владелец 27.11.2026, «Активация водителей»: у звонка три цели — «табы, и переключаясь по табам
 * он выбирал итоги звонка по каждому табу», у итогов любой вкладки — типы). Когда у отдела вкладки есть, список
 * показывается по вкладкам; у вкладки — название и «обязательна» (без итога на ней звонок не сохранится). Первая
 * вкладка — главная: её итог ведёт обзвон, поэтому «перезвонить» и «нужна дата» — только у её итогов. Итог
 * переносят на другую вкладку выбором в строке; вкладку убирают, пока на ней нет сохранённых итогов (на них ссылается
 * история). Вкладки сохраняются вместе со списком и входят в его версию.
 */
const AUTOSAVE_MS = 600;

// Сохранение, которое ещё идёт, — по отделу: панель, рождённая заново (вернулись на вкладку), читает
// список только после него. Иначе досохранение при уходе со вкладки и загрузка при возврате расходились.
const pendingSaves = new Map();
// Что руководитель раскрыл или свернул руками — по отделу, до перезагрузки страницы.
const expandedByDepartment = new Map();
// Сколько видна отметка «Сохранено».
const SAVED_SHOW_MS = 2500;

// Системная палитра iOS — те же цвета, что у бейджей сайта.
export const OUTCOME_PALETTE = [
    '#FF3B30', '#FF9F0A', '#FFD60A', '#34C759', '#00C7BE', '#30B0C7', '#32ADE6',
    '#007AFF', '#5856D6', '#AF52DE', '#FF2D55', '#A2845E', '#8E8E93',
];

const HEX_RE = /^#[0-9A-Fa-f]{6}$/;

const readError = async (resp) => {
    const data = await resp.json().catch(() => ({}));
    return data?.error || `HTTP ${resp.status}`;
};

/** Тонированный бейдж итога: цвет из справочника, читаемый на белом.
    С типом — «Отказ · Дорого»: тип чуть бледнее, полная подпись в title. */
export const OutcomeBadge = ({ outcome, className = '', small = false }) => {
    if (!outcome?.name) return null;
    const color = HEX_RE.test(outcome.color || '') ? outcome.color : '#8E8E93';
    const sub = outcomeSubtypeName(outcome);
    return (
        <span
            className={`inline-flex max-w-full items-center gap-1.5 rounded-full font-medium ${small ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-[11.5px]'} ${className}`}
            style={{ backgroundColor: `${color}1F`, color, boxShadow: `inset 0 0 0 1px ${color}33` }}
            title={outcomeLabel(outcome)}
        >
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
            <span className="truncate">
                {outcome.name}
                {sub && <span className="opacity-75">{` · ${sub}`}</span>}
            </span>
        </span>
    );
};

const ColorDot = ({ color, size = 22, ring = false, onClick, title }) => (
    <button
        type="button"
        onClick={onClick}
        title={title}
        aria-label={title}
        className={`grid shrink-0 place-items-center rounded-full transition active:scale-95 ${ring ? 'ring-2 ring-offset-2 ring-slate-900' : 'hover:scale-105'}`}
        style={{ width: size, height: size, backgroundColor: color }}
    >
        {ring && <FaIcon className="fas fa-check text-white" style={{ width: 10, height: 10 }} />}
    </button>
);

/** Поповер палитры: 13 системных цветов + произвольный hex. */
const ColorPicker = ({ value, onChange, disabled }) => {
    const [open, setOpen] = useState(false);
    const [hex, setHex] = useState(value);
    const ref = useRef(null);
    useEffect(() => { setHex(value); }, [value]);
    useEffect(() => {
        if (!open) return undefined;
        const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener('mousedown', onDoc);
        return () => document.removeEventListener('mousedown', onDoc);
    }, [open]);
    return (
        <div ref={ref} className="relative">
            <ColorDot color={value} size={26} onClick={() => !disabled && setOpen((v) => !v)} title="Цвет итога" />
            {open && (
                <div className="absolute left-0 top-full z-20 mt-2 w-56 rounded-2xl bg-white p-3 shadow-xl ring-1 ring-slate-200">
                    <div className="grid grid-cols-7 gap-2">
                        {OUTCOME_PALETTE.map((c) => (
                            <ColorDot key={c} color={c} size={22} ring={c.toUpperCase() === String(value).toUpperCase()}
                                onClick={() => { onChange(c); setOpen(false); }} title={c} />
                        ))}
                    </div>
                    <div className="mt-3 flex items-center gap-2">
                        <span className="h-5 w-5 rounded-full ring-1 ring-slate-200" style={{ backgroundColor: HEX_RE.test(hex) ? hex : '#fff' }} />
                        <input
                            value={hex}
                            onChange={(e) => setHex(e.target.value)}
                            onBlur={() => { if (HEX_RE.test(hex)) onChange(hex.toUpperCase()); else setHex(value); }}
                            onKeyDown={(e) => { if (e.key === 'Enter' && HEX_RE.test(hex)) { onChange(hex.toUpperCase()); setOpen(false); } }}
                            placeholder="#RRGGBB"
                            className={`${iosInput} py-1.5 font-mono text-[12.5px] uppercase`}
                            maxLength={7}
                        />
                    </div>
                </div>
            )}
        </div>
    );
};

let tempSeq = 0;
const tempId = () => `new-${Date.now()}-${tempSeq++}`;

/* Вложенный список типов одного итога. Точка — цвет итога: своего цвета у
   типа нет, в телефоне и в журнале он красится цветом итога. */
const SubtypeList = ({ outcome, canEdit, onPatch, onMove, onAdd, onRemove }) => {
    const subs = outcome.subtypes || [];
    const color = HEX_RE.test(outcome.color || '') ? outcome.color : '#8E8E93';
    return (
        <div className="ml-[38px] mt-1.5 border-l-2 border-slate-100 pl-3">
            {subs.length > 0 && (
                <ul className="space-y-1">
                    {subs.map((s, sIdx) => (
                        <li key={rowKey(s)} className="flex flex-wrap items-center gap-2 sm:flex-nowrap">
                            <span className={`h-2 w-2 shrink-0 rounded-full ${s.is_active !== false ? '' : 'opacity-40'}`} style={{ backgroundColor: color }} />
                            <input
                                value={s.name}
                                onChange={(e) => onPatch(sIdx, { name: e.target.value })}
                                disabled={!canEdit}
                                maxLength={SUBTYPE_NAME_MAX}
                                placeholder="Например: Дорого"
                                className={`${iosInput} min-w-[120px] flex-1 py-1.5 text-[13px] ${s.is_active !== false ? '' : 'text-slate-400 line-through'}`}
                            />
                            <label className="flex shrink-0 items-center gap-2 text-[12px] text-slate-600">
                                <IosToggle checked={s.is_active !== false} disabled={!canEdit} onChange={(v) => onPatch(sIdx, { is_active: v })} />
                                {s.is_active !== false ? 'включён' : 'выключен'}
                            </label>
                            {s.used > 0 && <IosBadge tone="slate" title="Сколько раз тип уже выбирали">{s.used}</IosBadge>}
                            {canEdit && (
                                <div className="flex shrink-0 items-center gap-0.5">
                                    <button type="button" onClick={() => onMove(sIdx, -1)} disabled={sIdx === 0} className={`${iosBtnGhost} px-2 py-1`} aria-label="Выше">
                                        <FaIcon className="fas fa-chevron-up" style={{ width: 10, height: 10 }} />
                                    </button>
                                    <button type="button" onClick={() => onMove(sIdx, 1)} disabled={sIdx === subs.length - 1} className={`${iosBtnGhost} px-2 py-1`} aria-label="Ниже">
                                        <FaIcon className="fas fa-chevron-down" style={{ width: 10, height: 10 }} />
                                    </button>
                                    {isNewId(s.id) && (
                                        <button type="button" onClick={() => onRemove(sIdx)} className={`${iosBtnGhost} px-2 py-1 text-rose-500`} aria-label="Убрать">
                                            <FaIcon className="fas fa-xmark" style={{ width: 10, height: 10 }} />
                                        </button>
                                    )}
                                </div>
                            )}
                        </li>
                    ))}
                </ul>
            )}
            {canEdit && (subs.length >= SUBTYPES_MAX ? (
                // Предел виден словами, а не только подсказкой на погасшей кнопке.
                <div className="mt-1 px-2 py-1 text-[12px] text-slate-500">
                    Типов {SUBTYPES_MAX} из {SUBTYPES_MAX} — больше у итога не добавить; ненужный можно переименовать
                </div>
            ) : (
                <button
                    type="button"
                    onClick={onAdd}
                    className={`${iosBtnGhost} mt-1 px-2 py-1 text-[12.5px]`}
                >
                    <FaIcon className="fas fa-plus" style={{ width: 10, height: 10 }} /> Добавить тип
                </button>
            ))}
        </div>
    );
};

/* Справа в подвале — что с сохранением: исправить (повтор названия и т. п. — до запроса,
   те же правила, что у сервера), идёт, сохранено (ненадолго), не сохранилось — с повтором.
   Пока всё тихо — ничего. */
const SaveStatus = ({ state, error, problem, onRetry }) => {
    if (problem) {
        return <span className="text-right text-[12px] text-amber-600">{problem} — пока не исправите, изменения не сохранятся</span>;
    }
    if (state === 'failed') {
        return (
            <span className="flex items-center gap-2 text-[12px] text-rose-600">
                <span className="text-right">Не сохранено: {error}</span>
                <button type="button" onClick={onRetry} className={`${iosBtnSecondary} py-1.5`}>Повторить</button>
            </span>
        );
    }
    if (state === 'saving') {
        return <span className="text-[12px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Сохраняем…</span>;
    }
    if (state === 'saved') {
        return <span className="text-[12px] text-slate-500"><FaIcon className="fas fa-check" /> Сохранено</span>;
    }
    return null;
};

/* Раскрытые списки типов — по id итога: после сохранения временный id стал настоящим. */
const remapExpandedIds = (expanded, idMap) => {
    const out = {};
    for (const [id, open] of Object.entries(expanded || {})) out[idMap[id] || id] = open;
    return out;
};

/* Число использований — у сервера свежее (его двигают телефоны): переносим его в рабочую
   копию, ничего больше в ней не трогая. */
const withServerUsage = (items, serverOutcomes) => {
    const used = new Map();
    for (const o of Array.isArray(serverOutcomes) ? serverOutcomes : []) {
        used.set(o.id, o.used);
        for (const s of Array.isArray(o.subtypes) ? o.subtypes : []) used.set(s.id, s.used);
    }
    return items.map((o) => ({
        ...o,
        used: used.has(o.id) ? used.get(o.id) : o.used,
        subtypes: (o.subtypes || []).map((s) => (used.has(s.id) ? { ...s, used: used.get(s.id) } : s)),
    }));
};

const DialListOutcomesPanel = ({ apiBaseUrl, authHeaders, departmentId, canEdit = true, showToast, dueKinds = false }) => {
    const [items, setItems] = useState(null);     // рабочая копия
    const [saved, setSaved] = useState(null);     // что на сервере (для «есть изменения»)
    const [error, setError] = useState('');
    // Сохранение: state '' | 'saving' | 'saved' | 'failed', error — текст отказа.
    const [save, setSave] = useState({ state: '', error: '' });
    // Раскрытые и свёрнутые руками списки типов: {id итога: true | false}; не нажимали — раскрыт, если
    // типы есть (subtypesOpen). Только вид, на сервер не уходит; переживает уход со вкладки.
    const [expanded, setExpanded] = useState(() => expandedByDepartment.get(String(departmentId)) || {});
    useEffect(() => { expandedByDepartment.set(String(departmentId), expanded); }, [departmentId, expanded]);
    // Старый сервер (раскатка/откат по частям) типы не хранит — не предлагаем их.
    const [typesSupported, setTypesSupported] = useState(true);
    // Вкладки итогов отдела (рабочая копия): null — сервер их не знает, [] — у отдела их нет.
    const [groups, setGroups] = useState(null);
    // Открытая вкладка — ключ.
    const [tab, setTab] = useState('');
    const tabsOn = Array.isArray(groups) && groups.length > 0;
    // Версия справочника, с которой работаем (сервер отклонит сохранение, если список уже другой).
    const versionRef = useRef(null);
    // Сохранения идут по одному: правка во время запроса уйдёт следующим.
    const inFlightRef = useRef(false);
    const mountedRef = useRef(true);
    // Ушли со вкладки, пока шёл запрос: досохранить, когда он вернётся.
    const flushAfterRef = useRef(false);
    // Последнее, что на экране, — для сохранения из таймера, при уходе со вкладки и закрытии страницы.
    const latestRef = useRef({ items: null, saved: null, dueKinds, canEdit, groups: null });
    const sendRef = useRef(null);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    const load = useCallback(async () => {
        try {
            // Досохранение этого отдела ещё идёт (ушли со вкладки и сразу вернулись) — сначала оно,
            // иначе список пришёл бы без последней правки. Пока ждали, могло начаться следующее.
            for (let pending = pendingSaves.get(String(departmentId)); pending; pending = pendingSaves.get(String(departmentId))) {
                await pending;
            }
            if (!mountedRef.current) return;
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/outcomes`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            const list = toEditable(data.outcomes);
            const tabs = toEditableGroups(data.groups);
            versionRef.current = data.version ?? null;
            setTypesSupported(serverHasSubtypes(data.outcomes));
            setItems(list);
            setGroups(tabs);
            setTab((cur) => ((tabs || []).some((g) => g.key === cur) ? cur : mainGroupKey(tabs)));
            setSaved(outcomesSnapshot(list, { dueKinds, groups: tabs }));
            setError('');
        } catch (e) {
            setError(e.message || 'Не удалось загрузить итоги');
        }
    }, [apiBaseUrl, authHeaders, departmentId, dueKinds]);

    useEffect(() => { load(); }, [load]);

    // Один и тот же снимок для сохранённого и для рабочей копии (outcomeFormat.js); вкладки — его часть.
    const snapshot = useMemo(() => (items ? outcomesSnapshot(items, { dueKinds, groups }) : null), [items, dueKinds, groups]);
    const dirty = !!items && snapshot !== saved;
    // Что не даст сохранить (те же правила, что у сервера) — показываем сразу, запрос не шлём.
    const problem = useMemo(() => (items
        ? validateOutcomes(serializeOutcomes(items, { dueKinds, groups }), tabsOn ? groups : null) : ''),
    [items, dueKinds, groups, tabsOn]);
    latestRef.current = { items, saved, dueKinds, canEdit, groups };

    // Отправить рабочую копию. keepalive — запрос переживёт закрытие страницы.
    const send = useCallback(async ({ keepalive = false } = {}) => {
        const { items: current, saved: savedNow, dueKinds: dk, canEdit: editable, groups: gs } = latestRef.current;
        if (!current || !editable || inFlightRef.current) return;
        const tabsNow = Array.isArray(gs) && gs.length > 0;
        const payload = serializeOutcomes(current, { dueKinds: dk, groups: gs });
        if (validateOutcomes(payload, tabsNow ? gs : null)) return;
        if (outcomesSnapshot(current, { dueKinds: dk, groups: gs }) === savedNow) return;
        inFlightRef.current = true;
        // Видно панели, рождённой заново для этого отдела (load ждёт его).
        const deptKey = String(departmentId);
        let release = () => {};
        const pending = new Promise((resolve) => { release = resolve; });
        pendingSaves.set(deptKey, pending);
        if (mountedRef.current) setSave({ state: 'saving', error: '' });
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/outcomes`, {
                method: 'PUT', credentials: 'include', keepalive,
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                // Вкладки — только у отдела с вкладками: без них запрос прежний.
                body: JSON.stringify({
                    items: payload, version: versionRef.current, ...(tabsNow ? { groups: serializeGroups(gs) } : {}),
                }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                const failure = new Error(data?.error || `HTTP ${resp.status}`);
                failure.kind = data?.error_kind || '';
                throw failure;
            }
            versionRef.current = data.version ?? null;
            // Рабочую копию ответом не заменяем: пока он шёл, могли печатать дальше.
            const idMap = savedIdMap(current, data.outcomes, tabsNow ? gs : null);
            const nowSaved = outcomesSnapshot(applyIdMap(current, idMap), { dueKinds: dk, groups: gs });
            if (!mountedRef.current) {
                latestRef.current = { ...latestRef.current, items: applyIdMap(latestRef.current.items || [], idMap), saved: nowSaved };
                return;
            }
            setItems((list) => withServerUsage(applyIdMap(list || [], idMap), data.outcomes));
            setExpanded((cur) => remapExpandedIds(cur, idMap));
            setTypesSupported(serverHasSubtypes(data.outcomes));
            setSaved(nowSaved);
            setSave({ state: 'saved', error: '' });
        } catch (e) {
            if (!mountedRef.current) return;
            if (e.kind === 'stale') {
                // Список изменили в другой вкладке: показываем свежий, правку повторят на нём.
                setSave({ state: '', error: '' });
                toast(e.message, 'error');
                load();
                return;
            }
            setSave({ state: 'failed', error: e.message || 'не удалось сохранить итоги' });
        } finally {
            inFlightRef.current = false;
            if (pendingSaves.get(deptKey) === pending) pendingSaves.delete(deptKey);
            // Досохранение — до release: ждущая загрузка увидит уже его, а не пустое место.
            if (flushAfterRef.current) {
                flushAfterRef.current = false;
                sendRef.current?.({ keepalive: true });
            }
            release();
        }
    }, [apiBaseUrl, authHeaders, departmentId, toast, load]);
    sendRef.current = send;

    // Сохранить само через AUTOSAVE_MS после последней правки. saved — тоже повод: правка, сделанная, пока
    // шёл запрос, снимок могла уже не менять, и без него она ждала бы следующей.
    useEffect(() => {
        if (!canEdit || !dirty || problem) return undefined;
        const timer = setTimeout(() => sendRef.current?.(), AUTOSAVE_MS);
        return () => clearTimeout(timer);
    }, [canEdit, dirty, problem, snapshot, saved]);

    // «Сохранено» — ненадолго.
    useEffect(() => {
        if (save.state !== 'saved') return undefined;
        const timer = setTimeout(() => setSave((s) => (s.state === 'saved' ? { state: '', error: '' } : s)), SAVED_SHOW_MS);
        return () => clearTimeout(timer);
    }, [save.state]);

    // Ушли со вкладки (панель размонтирована) или закрыли страницу — досохранить сразу, не дожидаясь паузы.
    useEffect(() => {
        mountedRef.current = true;
        const flush = () => {
            const { items: current, saved: savedNow, dueKinds: dk, canEdit: editable, groups: gs } = latestRef.current;
            if (!current || !editable || outcomesSnapshot(current, { dueKinds: dk, groups: gs }) === savedNow) return;
            if (inFlightRef.current) { flushAfterRef.current = true; return; }
            sendRef.current?.({ keepalive: true });
        };
        window.addEventListener('pagehide', flush);
        return () => {
            window.removeEventListener('pagehide', flush);
            mountedRef.current = false;
            flush();
        };
    }, []);

    const patch = (idx, changes) => setItems((list) => list.map((o, i) => (i === idx ? { ...o, ...changes } : o)));
    // Выше/ниже — внутри открытой вкладки: соседом считается ближайший итог той же вкладки.
    const move = (idx, dir) => setItems((list) => {
        const next = [...list];
        const own = tabsOn ? outcomeGroupOf(list[idx], groups) : '';
        let j = idx + dir;
        while (tabsOn && j >= 0 && j < next.length && outcomeGroupOf(next[j], groups) !== own) j += dir;
        if (j < 0 || j >= next.length) return list;
        [next[idx], next[j]] = [next[j], next[idx]];
        return next;
    });
    // _key — постоянный ключ строки (outcomeFormat.rowKey): id после сохранения станет настоящим, ключ — нет.
    const add = () => setItems((list) => {
        const id = tempId();
        return [...(list || []), {
            id, _key: id, name: '', color: OUTCOME_PALETTE[(list?.length || 0) % OUTCOME_PALETTE.length],
            requeue: false, requeue_counts: false, is_active: true, used: 0, subtypes: [], due_kind: '',
            ...(tabsOn ? { group: openTab.key } : {}),
        }];
    });
    const remove = (idx) => setItems((list) => list.filter((_, i) => i !== idx));

    // Вкладки: открытая, главная, правка, новая и убрать (пока на ней нет сохранённых итогов).
    const mainKey = mainGroupKey(groups);
    const openTab = tabsOn ? (groups.find((g) => g.key === tab) || groups[0]) : null;
    const patchGroup = (key, changes) => setGroups((list) => list.map((g) => (g.key === key ? { ...g, ...changes } : g)));
    const addGroup = () => {
        const key = newGroupKey(groups);
        setGroups((list) => [...list, { key, name: 'Новая вкладка', required: false }]);
        setTab(key);
    };
    const groupRemovable = (key) => key !== mainKey
        && !(items || []).some((o) => outcomeGroupOf(o, groups) === key && !isNewId(o.id));
    const removeGroup = (key) => {
        if (!groupRemovable(key)) return;
        setItems((list) => list.filter((o) => outcomeGroupOf(o, groups) !== key));
        setGroups((list) => list.filter((g) => g.key !== key));
        setTab(mainKey);
    };
    const activeIn = (key) => (items || []).filter((o) => o.is_active !== false && outcomeGroupOf(o, groups) === key).length;
    // Строки открытой вкладки — с их местом в общем списке (правка и порядок идут по нему).
    const visible = (items || []).map((o, idx) => ({ o, idx }))
        .filter(({ o }) => !tabsOn || outcomeGroupOf(o, groups) === openTab.key);

    // Типы итога idx: те же операции, что у итогов, но внутри его subtypes.
    const patchSubs = (idx, fn) => setItems((list) => list.map((o, i) => (i === idx ? { ...o, subtypes: fn(o.subtypes || []) } : o)));
    const patchSub = (idx, sIdx, changes) => patchSubs(idx, (subs) => subs.map((s, j) => (j === sIdx ? { ...s, ...changes } : s)));
    const moveSub = (idx, sIdx, dir) => patchSubs(idx, (subs) => {
        const next = [...subs];
        const j = sIdx + dir;
        if (j < 0 || j >= next.length) return subs;
        [next[sIdx], next[j]] = [next[j], next[sIdx]];
        return next;
    });
    const addSub = (idx) => patchSubs(idx, (subs) => {
        if (subs.length >= SUBTYPES_MAX) return subs;
        const id = tempId();
        return [...subs, { id, _key: id, name: '', is_active: true, used: 0 }];
    });
    const removeSub = (idx, sIdx) => patchSubs(idx, (subs) => subs.filter((_, j) => j !== sIdx));
    const toggleExpanded = (outcome) => setExpanded((cur) => ({ ...cur, [outcome.id]: !subtypesOpen(outcome, cur) }));

    return (
        <section className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 px-1">
                {dueKinds ? (
                    <div className="flex items-center gap-1.5">
                        <span className={iosGroupLabel}>Итоги звонка</span>
                        <IosHint text="«Нужна дата»: у итога «Перезвон» оператор обязан указать дату и время перезвона, у «Выход на линию» — когда водитель выйдет. К сроку водитель вернётся к этому же оператору; не выйдет на линию к обещанному времени — его вернут «дожать». Итог с датой всегда возвращает водителя в обзвон." />
                    </div>
                ) : (
                    <div className={iosGroupLabel}>Итоги звонка</div>
                )}
                <span className="text-[11.5px] text-slate-500">
                    {tabsOn ? 'Оператор выбирает итог на каждой вкладке' : 'Оператор выбирает один после каждого разговора'}
                </span>
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                {error ? (
                    <div className="px-4 py-4 text-[13px] text-rose-600">{error}</div>
                ) : items === null ? (
                    <div className="px-4 py-4 text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                ) : (
                    <>
                        {tabsOn && (
                            <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2.5">
                                <div className="min-w-0 overflow-x-auto">
                                    <IosSegmented
                                        value={openTab.key}
                                        options={groups.map((g) => ({
                                            value: g.key, label: String(g.name).trim() || 'Без названия', count: activeIn(g.key),
                                        }))}
                                        onChange={setTab}
                                        ariaLabel="Вкладки итогов"
                                        slide
                                    />
                                </div>
                                {canEdit && groups.length < OUTCOME_GROUPS_MAX && (
                                    <button type="button" onClick={addGroup} className={`${iosBtnGhost} shrink-0 px-2 py-1.5 text-[12.5px]`}>
                                        <FaIcon className="fas fa-plus" style={{ width: 10, height: 10 }} /> Вкладка
                                    </button>
                                )}
                            </div>
                        )}
                        {tabsOn && (
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-100 bg-slate-50/60 px-3 py-2">
                                <input
                                    value={openTab.name}
                                    onChange={(e) => patchGroup(openTab.key, { name: e.target.value })}
                                    disabled={!canEdit}
                                    maxLength={OUTCOME_GROUP_NAME_MAX}
                                    placeholder="Название вкладки"
                                    aria-label="Название вкладки"
                                    // Не iosInput: его w-full и text-[14px] перебивают ширину и размер.
                                    className="w-52 rounded-xl border-0 bg-white px-3 py-1.5 text-[13px] text-slate-900 placeholder-slate-400 ring-1 ring-slate-200 transition focus:outline-none focus:ring-2 focus:ring-blue-500/70 disabled:opacity-60"
                                />
                                {openTab.key === mainKey ? (
                                    <span className="text-[12px] text-slate-500">Главная: её итог ведёт обзвон — дата и «перезвонить» только здесь</span>
                                ) : (
                                    <label
                                        className="flex items-center gap-2 text-[12px] text-slate-600"
                                        title="Без итога на этой вкладке оператор не сохранит звонок"
                                    >
                                        <IosToggle
                                            checked={openTab.required !== false}
                                            disabled={!canEdit}
                                            onChange={(v) => patchGroup(openTab.key, { required: v })}
                                        />
                                        обязательна
                                    </label>
                                )}
                                {canEdit && groupRemovable(openTab.key) && (
                                    <button
                                        type="button"
                                        onClick={() => removeGroup(openTab.key)}
                                        className={`${iosBtnGhost} ml-auto px-2 py-1 text-[12.5px] text-rose-500`}
                                    >
                                        Убрать вкладку
                                    </button>
                                )}
                            </div>
                        )}
                        <ul className="divide-y divide-slate-100">
                            {visible.map(({ o, idx }, pos) => {
                                const typesLabel = typesSupported ? subtypesToggleLabel(o, canEdit) : '';
                                const open = subtypesOpen(o, expanded);
                                // Обзвон ведёт итог главной вкладки: «перезвонить» и дата — только у него.
                                const rowGroup = tabsOn ? outcomeGroupOf(o, groups) : '';
                                const drivesDial = !tabsOn || rowGroup === mainKey;
                                // Итог с датой всегда «перезвонить» (outcomeFormat.js, DUE_KINDS).
                                const dueKind = dueKinds ? normalizeDueKind(o.due_kind) : '';
                                const requeueForced = dueKind !== '';
                                const requeueTitle = requeueForced
                                    ? 'Итог с датой всегда возвращает водителя его оператору к сроку'
                                    : dueKinds
                                        ? 'Водитель снова попадёт в пачки через первый интервал недозвона'
                                        : 'Водитель снова попадёт в порции через «Повтор через, часов»';
                                return (
                                    <li key={rowKey(o)} className={`px-3 py-2.5 ${o.is_active ? '' : 'bg-slate-50/60'}`}>
                                        <div className="flex flex-wrap items-center gap-3 sm:flex-nowrap">
                                            <ColorPicker value={o.color} onChange={(c) => patch(idx, { color: c })} disabled={!canEdit} />
                                            <input
                                                value={o.name}
                                                onChange={(e) => patch(idx, { name: e.target.value })}
                                                disabled={!canEdit}
                                                maxLength={64}
                                                placeholder="Название итога"
                                                className={`${iosInput} min-w-[140px] flex-1 py-2 text-[13.5px] ${o.is_active ? '' : 'text-slate-400 line-through'}`}
                                            />
                                            {drivesDial && (
                                                <label className="flex shrink-0 items-center gap-2 text-[12px] text-slate-600" title={requeueTitle}>
                                                    <IosToggle
                                                        checked={!!o.requeue || requeueForced}
                                                        disabled={!canEdit || requeueForced}
                                                        onChange={(v) => patch(idx, { requeue: v })}
                                                    />
                                                    перезвонить
                                                </label>
                                            )}
                                            {drivesDial && !dueKinds && 'requeue_counts' in o && !!o.requeue && (
                                                <label
                                                    className="flex shrink-0 items-center gap-2 text-[12px] text-slate-600"
                                                    title="Водитель возвращается в список, но каждый такой итог — попытка: когда попытки на номер кончились, ему больше не звонят. Выключено — попытки после итога с нуля."
                                                >
                                                    <IosToggle
                                                        checked={!!o.requeue_counts}
                                                        disabled={!canEdit}
                                                        onChange={(v) => patch(idx, { requeue_counts: v })}
                                                    />
                                                    в счёт попыток
                                                </label>
                                            )}
                                            <label className="flex shrink-0 items-center gap-2 text-[12px] text-slate-600">
                                                <IosToggle checked={o.is_active !== false} disabled={!canEdit} onChange={(v) => patch(idx, { is_active: v })} />
                                                {o.is_active !== false ? 'включён' : 'выключен'}
                                            </label>
                                            {o.used > 0 && <IosBadge tone="slate">{o.used}</IosBadge>}
                                            {tabsOn && canEdit && groups.length > 1 && (
                                                <select
                                                    value={rowGroup}
                                                    onChange={(e) => patch(idx, { group: e.target.value })}
                                                    title="Перенести итог на другую вкладку"
                                                    aria-label={`Вкладка итога «${o.name || 'новый'}»`}
                                                    className="w-[150px] shrink-0 rounded-lg border-0 bg-slate-100 px-2.5 py-1.5 text-[12px] text-slate-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                                                >
                                                    {groups.map((g) => (
                                                        <option key={g.key} value={g.key}>{String(g.name).trim() || 'Без названия'}</option>
                                                    ))}
                                                </select>
                                            )}
                                            {canEdit && (
                                                <div className="flex shrink-0 items-center gap-0.5">
                                                    <button type="button" onClick={() => move(idx, -1)} disabled={pos === 0} className={`${iosBtnGhost} px-2 py-1.5`} aria-label="Выше">
                                                        <FaIcon className="fas fa-chevron-up" style={{ width: 11, height: 11 }} />
                                                    </button>
                                                    <button type="button" onClick={() => move(idx, 1)} disabled={pos === visible.length - 1} className={`${iosBtnGhost} px-2 py-1.5`} aria-label="Ниже">
                                                        <FaIcon className="fas fa-chevron-down" style={{ width: 11, height: 11 }} />
                                                    </button>
                                                    {String(o.id).startsWith('new-') && (
                                                        <button type="button" onClick={() => remove(idx)} className={`${iosBtnGhost} px-2 py-1.5 text-rose-500`} aria-label="Убрать">
                                                            <FaIcon className="fas fa-xmark" style={{ width: 11, height: 11 }} />
                                                        </button>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                        {dueKinds && drivesDial && (
                                            <div className="ml-[38px] mt-1.5 flex flex-wrap items-center gap-2">
                                                <span className="text-[12px] text-slate-500">Нужна дата</span>
                                                <div className={canEdit ? '' : 'pointer-events-none opacity-60'}>
                                                    <IosSegmented
                                                        size="xs"
                                                        value={dueKind}
                                                        options={DUE_KIND_OPTIONS}
                                                        onChange={(v) => patch(idx, { due_kind: v })}
                                                        ariaLabel={`Нужна дата: ${o.name || 'итог'}`}
                                                    />
                                                </div>
                                            </div>
                                        )}
                                        {typesLabel && (
                                            <button
                                                type="button"
                                                onClick={() => toggleExpanded(o)}
                                                aria-expanded={open}
                                                className="ml-[38px] mt-1 inline-flex items-center gap-1.5 rounded-lg px-1.5 py-0.5 text-[12px] font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
                                            >
                                                <FaIcon className={open ? 'fas fa-chevron-down' : 'fas fa-chevron-right'} style={{ width: 9, height: 9 }} />
                                                {typesLabel}
                                            </button>
                                        )}
                                        {typesLabel && open && (
                                            <SubtypeList
                                                outcome={o}
                                                canEdit={canEdit}
                                                onPatch={(sIdx, changes) => patchSub(idx, sIdx, changes)}
                                                onMove={(sIdx, dir) => moveSub(idx, sIdx, dir)}
                                                onAdd={() => addSub(idx)}
                                                onRemove={(sIdx) => removeSub(idx, sIdx)}
                                            />
                                        )}
                                    </li>
                                );
                            })}
                            {visible.length === 0 && (
                                <li className="px-4 py-5 text-center text-[13px] text-slate-500">
                                    {tabsOn ? 'На этой вкладке итогов пока нет — добавьте хотя бы один.' : 'Итогов пока нет — добавьте хотя бы один.'}
                                </li>
                            )}
                        </ul>
                        {canEdit && (
                            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-3 py-2.5">
                                <button type="button" onClick={add} disabled={items.length >= 30} className={iosBtnSecondary}>
                                    <FaIcon className="fas fa-plus" style={{ width: 11, height: 11 }} /> Добавить итог
                                </button>
                                <SaveStatus state={save.state} error={save.error} problem={problem}
                                    onRetry={() => sendRef.current?.()} />
                            </div>
                        )}
                    </>
                )}
            </div>
            <div className="px-1 text-[11.5px] text-slate-500">
                {canEdit ? 'Правки сохраняются сами, сразу. ' : ''}
                Выключенный итог пропадает из телефона, но остаётся в истории. Число справа — сколько раз итог уже выбирали.
                {' '}Если у итога есть включённые типы, оператор после итога обязан выбрать и тип. Выключенный тип тоже
                пропадает из телефона и остаётся в истории.
                {tabsOn && (
                    <>
                        {' '}Вкладки — цели звонка: в iCORE Phone оператор переключает их и выбирает итог на каждой; без итога
                        на обязательной вкладке звонок не сохранится. Телефон старше 3.22.67 вкладок не показывает — в нём
                        только итоги главной.
                    </>
                )}
            </div>
        </section>
    );
};

export default DialListOutcomesPanel;
