import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Search, Database, Check, X, Minus, ArrowRight, Quote, Repeat, Loader2,
    Pencil, Trash2, Save, RefreshCw, AlertTriangle, Activity, Layers3,
    ChevronLeft, ChevronRight, Box, CircleDot, ServerCrash,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, IosBadge,
} from '../ui/ios';

const VERDICTS = {
    Correct: { tone: 'green', label: 'Верно', Icon: Check },
    Incorrect: { tone: 'red', label: 'Неверно', Icon: X },
    'N/A': { tone: 'slate', label: 'N/A', Icon: Minus },
};

const RULE_STATUS = {
    draft: { tone: 'amber', label: 'Черновик' },
    active: { tone: 'green', label: 'Активно' },
    deprecated: { tone: 'slate', label: 'Устарело' },
    quarantined: { tone: 'red', label: 'Карантин' },
};

const INDEX_STATUS = {
    indexed: { tone: 'green', label: 'Индекс готов', participates: true },
    ready: { tone: 'green', label: 'Индекс готов', participates: true },
    pending: { tone: 'amber', label: 'Ожидает индексации', participates: false },
    indexing: { tone: 'amber', label: 'Индексируется', participates: false },
    stale: { tone: 'amber', label: 'Индекс устарел', participates: false },
    failed: { tone: 'red', label: 'Ошибка индекса', participates: false },
    error: { tone: 'red', label: 'Ошибка индекса', participates: false },
    disabled: { tone: 'slate', label: 'Без индекса', participates: false },
    unindexed: { tone: 'slate', label: 'Без индекса', participates: false },
};

const PAGE_SIZES = [12, 24, 48];
const fieldCls = `${iosInput} px-3 py-2 text-[12.5px]`;
const controlCls = `${iosInput} min-w-0 px-3 py-2 text-[12.5px]`;

const cleanString = (value) => (value == null ? '' : String(value));
const normStatus = (value, fallback) => cleanString(value || fallback).toLowerCase();
const ruleStatusOf = (item) => normStatus(item.rule_status || item.status, 'active');
const indexStatusOf = (item) => normStatus(item.index_status || item.embedding_status, 'unindexed');

function Verdict({ value }) {
    const meta = VERDICTS[value] || VERDICTS['N/A'];
    return <IosBadge tone={meta.tone}><meta.Icon size={11} />{meta.label}</IosBadge>;
}

function RuleStatusBadge({ value }) {
    const meta = RULE_STATUS[normStatus(value, 'active')] || { tone: 'slate', label: value || 'Неизвестно' };
    return <IosBadge tone={meta.tone}><CircleDot size={10} />{meta.label}</IosBadge>;
}

function IndexStatusBadge({ value }) {
    const meta = INDEX_STATUS[normStatus(value, 'unindexed')] || { tone: 'slate', label: value || 'Неизвестно' };
    return <IosBadge tone={meta.tone}><Box size={10} />{meta.label}</IosBadge>;
}

function toFacetOptions(rawFacet, fallback = []) {
    const options = [];
    if (Array.isArray(rawFacet)) {
        rawFacet.forEach((entry) => {
            if (entry && typeof entry === 'object') {
                const value = entry.value ?? entry.id ?? entry.key ?? entry.name;
                if (value != null) options.push({
                    value: String(value),
                    label: String(entry.label ?? entry.name ?? value),
                    count: entry.count,
                });
            } else if (entry != null) {
                options.push({ value: String(entry), label: String(entry) });
            }
        });
    } else if (rawFacet && typeof rawFacet === 'object') {
        Object.entries(rawFacet).forEach(([value, count]) => options.push({ value, label: value, count }));
    }
    fallback.forEach((value) => {
        if (value != null && !options.some((option) => option.value === String(value))) {
            options.push({ value: String(value), label: String(value) });
        }
    });
    return options;
}

function matchesLegacyFilters(item, { q, direction, status, indexStatus }) {
    if (direction !== 'all' && cleanString(item.direction_id ?? item.direction) !== direction && cleanString(item.direction) !== direction) return false;
    if (status !== 'all' && ruleStatusOf(item) !== status) return false;
    if (indexStatus !== 'all' && indexStatusOf(item) !== indexStatus) return false;
    if (!q) return true;
    const haystack = [
        item.criterion, item.criterion_name, item.excerpt, item.reason, item.situation,
        item.not_covered, item.direction, item.by,
    ].map(cleanString).join(' ').toLowerCase();
    return haystack.includes(q.toLowerCase());
}

function normalizeResponse(body, requestState) {
    const payload = body && typeof body === 'object' ? body : {};
    const rawItems = Array.isArray(body) ? body : (Array.isArray(payload.items) ? payload.items : []);
    const modern = !Array.isArray(body) && (
        Object.prototype.hasOwnProperty.call(payload, 'total') || payload.facets || payload.knowledge || payload.health
    );
    let items = rawItems;
    let total = Number(payload.total);
    let page = Number(payload.page) || requestState.page;
    let pageSize = Number(payload.page_size) || requestState.pageSize;

    if (!modern) {
        const filtered = rawItems.filter((item) => matchesLegacyFilters(item, requestState));
        total = filtered.length;
        page = requestState.page;
        pageSize = requestState.pageSize;
        items = filtered.slice((page - 1) * pageSize, page * pageSize);
    } else if (!Number.isFinite(total)) {
        total = rawItems.length;
    }

    return {
        items,
        total: Math.max(0, total || 0),
        page: Math.max(1, page),
        pageSize: Math.max(1, pageSize),
        facets: payload.facets || {},
        knowledge: payload.knowledge || {},
        health: payload.health || {},
        knowledgeRevision: payload.knowledge_revision,
        legacy: !modern,
    };
}

function healthMeta(health) {
    const raw = health || {};
    let status = normStatus(raw.status || raw.state, 'unknown');
    if (raw.ok === true) status = 'healthy';
    if (raw.ok === false || raw.degraded === true) status = 'degraded';
    if (['ok', 'ready', 'healthy'].includes(status)) {
        return { status: 'healthy', tone: 'green', label: 'Система готова' };
    }
    if (['degraded', 'warning', 'partial'].includes(status)) {
        return { status: 'degraded', tone: 'amber', label: 'Работа ограничена' };
    }
    if (['down', 'error', 'failed', 'unavailable'].includes(status)) {
        return { status: 'error', tone: 'red', label: 'Система недоступна' };
    }
    return { status: 'unknown', tone: 'slate', label: 'Статус не передан' };
}

function SummaryCard({ icon: Icon, label, value, hint, tone = 'blue' }) {
    const tones = {
        blue: 'bg-blue-50 text-blue-600', green: 'bg-emerald-50 text-emerald-600',
        amber: 'bg-amber-50 text-amber-600', red: 'bg-rose-50 text-rose-600', slate: 'bg-slate-100 text-slate-500',
    };
    return (
        <div className={`${iosCard} flex min-w-0 items-center gap-3 p-3.5`}>
            <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ${tones[tone] || tones.blue}`}><Icon size={17} /></div>
            <div className="min-w-0">
                <p className="text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">{label}</p>
                <p className="truncate text-[14px] font-semibold text-slate-800">{value}</p>
                {hint && <p className="truncate text-[10.5px] text-slate-400">{hint}</p>}
            </div>
        </div>
    );
}

function EditForm({ item, onCancel, onSaved, onBusyChange, apiBaseUrl, headers, showToast }) {
    const isLegacy = item.source_type === 'legacy' || String(item.id || '').startsWith('legacy:');
    const [draft, setDraft] = useState({
        correct_verdict: item.correct ?? item.correct_verdict ?? 'N/A',
        reason: item.reason || '',
        situation: item.situation || '',
        not_covered: item.not_covered || '',
        rule_status: isLegacy ? 'draft' : ruleStatusOf(item),
    });
    const [saving, setSaving] = useState(false);
    const set = (key) => (event) => setDraft((current) => ({ ...current, [key]: event.target.value }));

    const save = async (event) => {
        event.preventDefault();
        if (saving) return;
        const payload = {
            ...draft,
            reason: draft.reason.trim(),
            situation: draft.situation.trim() || null,
            not_covered: draft.not_covered.trim() || null,
            ...(isLegacy ? {} : {
                expected_rule_version_id: item.rule_version_id,
                expected_content_hash: item.content_hash,
            }),
        };
        if (!payload.reason) {
            showToast?.('Правило не может быть пустым', 'error');
            return;
        }
        setSaving(true);
        onBusyChange?.(true);
        try {
            const response = await axios.put(`${apiBaseUrl}/api/ai-qa/adjudications/${item.id}`, payload, { headers: headers() });
            showToast?.(isLegacy && payload.rule_status === 'draft'
                ? 'Legacy-разбор мигрирован в проверяемый черновик'
                : 'Правило обновлено', 'success');
            onSaved(response.data?.item || payload);
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось сохранить правило', 'error');
        } finally {
            setSaving(false);
            onBusyChange?.(false);
        }
    };

    return (
        <form onSubmit={save} className="mt-4 space-y-3 rounded-2xl bg-slate-50 p-3.5 ring-1 ring-slate-200/80" aria-label={`Редактирование правила ${item.criterion || item.criterion_name || item.id}`}>
            {isLegacy && (
                <div className="rounded-xl bg-amber-50 px-3 py-2.5 text-[11.5px] leading-relaxed text-amber-800 ring-1 ring-amber-200">
                    Старый разбор не участвует в поиске правил. При сохранении он станет проверяемым черновиком:
                    исторический фрагмент останется условием применения, а отсутствие подтверждённой цитаты будет отмечено явно.
                    После подготовки индекса правило можно активировать отдельным действием.
                </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
                <fieldset>
                    <legend className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Правильный вердикт</legend>
                    <div className="inline-flex rounded-xl bg-slate-200/60 p-0.5">
                        {Object.keys(VERDICTS).map((value) => (
                            <button key={value} type="button" disabled={saving}
                                onClick={() => setDraft((current) => ({ ...current, correct_verdict: value }))}
                                aria-pressed={draft.correct_verdict === value}
                                className={`rounded-lg px-2.5 py-1.5 text-[12px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                                    draft.correct_verdict === value ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                                {VERDICTS[value].label}
                            </button>
                        ))}
                    </div>
                </fieldset>
                <label>
                    <span className="mb-1 block text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Жизненный цикл</span>
                    <select value={draft.rule_status} onChange={set('rule_status')} disabled={saving} className={`${fieldCls} appearance-none`}>
                        {Object.entries(RULE_STATUS)
                            .filter(([value]) => !isLegacy || value !== 'active')
                            .map(([value, meta]) => <option key={value} value={value}>
                                {isLegacy && value === 'draft' ? 'Мигрировать в черновик (без цитаты)' : meta.label}
                            </option>)}
                    </select>
                </label>
            </div>
            <label className="block">
                <span className="mb-1 block text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Правило <span className="normal-case text-rose-500">· обязательно</span></span>
                <textarea autoFocus rows={3} value={draft.reason} onChange={set('reason')} disabled={saving}
                    className={`${fieldCls} resize-y`} placeholder="Обобщённое правило, которое можно применить к похожим ситуациям" />
            </label>
            <div className="grid gap-3 md:grid-cols-2">
                <label>
                    <span className="mb-1 block text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Когда применять</span>
                    <textarea rows={2} value={draft.situation} onChange={set('situation')} disabled={saving}
                        className={`${fieldCls} resize-y`} placeholder="Условия применимости правила" />
                </label>
                <label>
                    <span className="mb-1 block text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Чего правило не оправдывает</span>
                    <textarea rows={2} value={draft.not_covered} onChange={set('not_covered')} disabled={saving}
                        className={`${fieldCls} resize-y`} placeholder="Границы и исключения" />
                </label>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
                <button type="button" onClick={onCancel} disabled={saving} className={iosBtnGhost}>Отмена</button>
                <button type="submit" disabled={saving} className={iosBtnPrimary}>
                    {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                    {saving ? 'Сохраняю…' : 'Сохранить'}
                </button>
            </div>
        </form>
    );
}

function RolloutPanel({ apiBaseUrl, headers, showToast, onInteractionChange }) {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [savingId, setSavingId] = useState(null);
    const [dirtyIds, setDirtyIds] = useState(() => new Set());
    const loadRequest = useRef(0);
    const load = () => {
        const requestId = ++loadRequest.current;
        setLoading(true); setError(null);
        if (!apiBaseUrl) {
            setItems([]); setError('Сервис базы знаний не настроен'); setLoading(false);
            return;
        }
        axios.get(`${apiBaseUrl}/api/ai-qa/rag-rollout`, { headers: headers() })
            .then((response) => {
                if (requestId !== loadRequest.current) return;
                setItems(response.data?.items || []); setDirtyIds(new Set());
            })
            .catch((requestError) => {
                if (requestId !== loadRequest.current) return;
                setItems([]);
                setError(requestError?.response?.data?.error || 'Не удалось загрузить режимы базы знаний');
            })
            .finally(() => {
                if (requestId === loadRequest.current) setLoading(false);
            });
    };
    useEffect(() => {
        load();
        return () => { loadRequest.current += 1; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl]);
    useEffect(() => {
        onInteractionChange?.({ editing: dirtyIds.size > 0, busy: savingId !== null });
    }, [dirtyIds, savingId, onInteractionChange]);
    useEffect(() => () => onInteractionChange?.({ editing: false, busy: false }), [onInteractionChange]);
    const edit = (directionId, patch) => {
        if (savingId !== null) return;
        setItems((current) => current.map((item) => (
            item.direction_id === directionId ? { ...item, ...patch } : item
        )));
        setDirtyIds((current) => new Set(current).add(directionId));
    };
    const save = async (item) => {
        if (savingId !== null || !dirtyIds.has(item.direction_id)) return;
        setSavingId(item.direction_id);
        try {
            const response = await axios.put(`${apiBaseUrl}/api/ai-qa/rag-rollout`, {
                direction_id: item.direction_id, mode: item.mode,
                canary_percent: item.canary_percent,
                approved_experiment_id: item.approved_experiment_id || null,
                manual_override: Boolean(item.manual_override),
                override_reason: item.manual_reason || null,
            }, { headers: headers() });
            const saved = response.data?.item || item;
            setItems((current) => current.map((currentItem) => (
                currentItem.direction_id === item.direction_id ? { ...currentItem, ...saved } : currentItem
            )));
            setDirtyIds((current) => {
                const next = new Set(current); next.delete(item.direction_id); return next;
            });
            showToast?.('Режим базы знаний обновлён', 'success');
        } catch (requestError) {
            showToast?.(requestError?.response?.data?.error || 'Не удалось изменить режим', 'error');
        } finally {
            setSavingId(null);
        }
    };
    return (
        <section className={`${iosCard} p-3.5`} aria-label="Влияние базы знаний на оценки">
            <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
                <div>
                    <p className="flex items-center gap-1.5 text-[12.5px] font-semibold text-slate-700"><Activity size={14} />Влияние базы знаний на оценки</p>
                    <p className="mt-0.5 text-[12px] text-slate-500">Частичное или полное включение — после контрольной проверки либо вручную (осознанно и обратимо).</p>
                </div>
                <div className="flex items-center gap-2">
                    {dirtyIds.size > 0 && <IosBadge tone="amber">Не сохранено: {dirtyIds.size}</IosBadge>}
                    {loading && <Loader2 size={15} className="animate-spin text-slate-500" />}
                </div>
            </div>
            {!loading && error ? (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-rose-50 px-3 py-2.5 text-rose-700" role="alert">
                    <span className="flex items-center gap-1.5 text-[12px]"><AlertTriangle size={14} />{error}</span>
                    <button type="button" onClick={load} className={`${iosBtnGhost} !py-1 !text-rose-700`}><RefreshCw size={13} />Повторить</button>
                </div>
            ) : !loading && items.length === 0 ? (
                <p className="py-2 text-[12px] text-slate-500">Для направлений пока нет настроек включения.</p>
            ) : (
                <div className="grid gap-2 lg:grid-cols-3">
                    {items.map((item) => {
                        const gated = !item.approved_experiment_id && !item.manual_override;
                        const saving = savingId === item.direction_id;
                        const dirty = dirtyIds.has(item.direction_id);
                        return (
                            <div key={item.direction_id} className="rounded-xl bg-slate-50 p-2.5 ring-1 ring-slate-200/70">
                                <p className="mb-2 truncate text-[12px] font-semibold text-slate-600">{item.direction}</p>
                                <div className="flex items-center gap-1.5">
                                    <label className="min-w-0 flex-1">
                                        <span className="sr-only">Режим базы знаний для направления {item.direction}</span>
                                        <select value={item.mode} disabled={savingId !== null}
                                            onChange={(event) => edit(item.direction_id, { mode: event.target.value })}
                                            className={`${fieldCls} min-w-0 appearance-none`}>
                                            <option value="off">Выключено</option>
                                            <option value="shadow">Проверка без влияния</option>
                                            <option value="canary" disabled={gated}>Часть звонков</option>
                                            <option value="active" disabled={gated}>Включено полностью</option>
                                        </select>
                                    </label>
                                    {item.mode === 'canary' && (
                                        <input type="number" min="1" max="99" value={item.canary_percent ?? 10}
                                            disabled={savingId !== null}
                                            onChange={(event) => edit(item.direction_id, { canary_percent: Number(event.target.value) })}
                                            className={`${fieldCls} !w-16`} aria-label={`Процент звонков для направления ${item.direction}`} />
                                    )}
                                    <button type="button" onClick={() => save(item)} disabled={savingId !== null || !dirty}
                                        aria-label={`Сохранить режим для направления ${item.direction}`} title="Сохранить режим"
                                        className={`${iosBtnSecondary} !h-9 !px-2.5`}>
                                        {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                                    </button>
                                </div>
                                <label className="mt-2 block">
                                    <span className="sr-only">ID контрольной проверки для направления {item.direction}</span>
                                    <input type="text" value={item.approved_experiment_id || ''}
                                        disabled={savingId !== null}
                                        onChange={(event) => edit(item.direction_id, {
                                            approved_experiment_id: event.target.value.trim(),
                                            approval_valid: false,
                                            approval_reason: event.target.value.trim()
                                                ? 'Сохраните режим, чтобы проверить новый ID'
                                                : 'Контрольная проверка не выбрана',
                                        })}
                                        placeholder="ID контрольной проверки (UUID)"
                                        className={`${fieldCls} font-mono text-[11px]`} />
                                </label>
                                <label className="mt-2 flex items-start gap-2 text-[11px] text-slate-600">
                                    <input type="checkbox" checked={Boolean(item.manual_override)}
                                        disabled={savingId !== null}
                                        onChange={(event) => {
                                            const checked = event.target.checked;
                                            const patch = { manual_override: checked };
                                            if (!checked && (item.mode === 'canary' || item.mode === 'active')) patch.mode = 'shadow';
                                            edit(item.direction_id, patch);
                                        }}
                                        className="mt-0.5 shrink-0" />
                                    <span>Включить вручную, без контрольной проверки</span>
                                </label>
                                {item.manual_override ? (
                                    <p className="mt-1.5 flex items-start gap-1.5 rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] leading-snug text-amber-800 ring-1 ring-amber-200">
                                        <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                                        Влияет на оценки без эксперимента. Предохранители сохранены: участвуют только активные проиндексированные правила, порог схожести и отказоустойчивость. Обратимо — снимите галочку или выберите «Проверка без влияния».
                                    </p>
                                ) : (
                                    <p className="mt-1.5 text-[11px] text-slate-500">
                                        {item.approval_valid
                                            ? `Контрольная проверка ${String(item.approved_experiment_id).slice(0, 8)} · актуальна`
                                            : item.approval_reason
                                                ? `Безопасный режим: ${item.approval_reason}`
                                            : 'Нет одобренной контрольной проверки'}
                                    </p>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </section>
    );
}

function Pagination({ page, pageSize, total, onPageChange, disabled }) {
    const pages = Math.max(1, Math.ceil(total / pageSize));
    if (total <= pageSize && page <= 1) return null;
    const start = total === 0 ? 0 : ((page - 1) * pageSize) + 1;
    const end = Math.min(total, page * pageSize);
    return (
        <nav className={`${iosCard} flex flex-wrap items-center justify-between gap-3 px-3.5 py-2.5`} aria-label="Навигация по правилам">
            <span className="text-[12px] tabular-nums text-slate-500">{start}–{end} из {total}</span>
            <div className="flex items-center gap-1.5">
                <button type="button" onClick={() => onPageChange(page - 1)} disabled={disabled || page <= 1}
                    className={`${iosBtnGhost} !h-8 !w-8 !p-0`} aria-label="Предыдущая страница"><ChevronLeft size={16} /></button>
                <span className="min-w-[86px] text-center text-[12.5px] font-medium tabular-nums text-slate-600">{page} / {pages}</span>
                <button type="button" onClick={() => onPageChange(page + 1)} disabled={disabled || page >= pages}
                    className={`${iosBtnGhost} !h-8 !w-8 !p-0`} aria-label="Следующая страница"><ChevronRight size={16} /></button>
            </div>
        </nav>
    );
}

export default function AdjudicationsRag(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast, canManage, onInteractionChange } = props;
    const [queryInput, setQueryInput] = useState('');
    const [query, setQuery] = useState('');
    const [direction, setDirection] = useState('all');
    const [status, setStatus] = useState('all');
    const [indexStatus, setIndexStatus] = useState('all');
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(12);
    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [editId, setEditId] = useState(null);
    const [busy, setBusy] = useState(null);
    const [rolloutInteraction, setRolloutInteraction] = useState({ editing: false, busy: false });
    const [reloadKey, setReloadKey] = useState(0);
    const requestId = useRef(0);

    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const locked = editId !== null || Boolean(busy) || rolloutInteraction.busy;

    useEffect(() => {
        const timer = window.setTimeout(() => {
            setQuery(queryInput.trim());
            setPage(1);
        }, 350);
        return () => window.clearTimeout(timer);
    }, [queryInput]);

    useEffect(() => {
        onInteractionChange?.({
            editing: editId !== null || rolloutInteraction.editing,
            busy: Boolean(busy) || rolloutInteraction.busy,
        });
    }, [editId, busy, rolloutInteraction, onInteractionChange]);

    useEffect(() => () => onInteractionChange?.({ editing: false, busy: false }), [onInteractionChange]);

    useEffect(() => {
        const currentRequest = ++requestId.current;
        const controller = new AbortController();
        if (!apiBaseUrl) {
            setLoading(false);
            setResult(null);
            setError('Сервис базы знаний не настроен');
            return () => controller.abort();
        }
        setLoading(true);
        setError(null);
        const requestState = { q: query, direction, status, indexStatus, page, pageSize };
        axios.get(`${apiBaseUrl}/api/ai-qa/adjudications`, {
            params: {
                page, page_size: pageSize,
                ...(query ? { q: query } : {}),
                ...(direction !== 'all' ? { direction } : {}),
                ...(status !== 'all' ? { status } : {}),
                ...(indexStatus !== 'all' ? { index_status: indexStatus } : {}),
            },
            headers: headers(),
            signal: controller.signal,
        }).then((response) => {
            if (currentRequest !== requestId.current) return;
            const normalized = normalizeResponse(response.data, requestState);
            setResult(normalized);
            if (normalized.page !== page) setPage(normalized.page);
        }).catch((requestError) => {
            if (axios.isCancel(requestError) || currentRequest !== requestId.current) return;
            setError(requestError?.response?.data?.error || 'Не удалось загрузить базу знаний');
        }).finally(() => {
            if (currentRequest === requestId.current) setLoading(false);
        });
        return () => controller.abort();
        // Access-token headers are intentionally read at request time.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl, query, direction, status, indexStatus, page, pageSize, reloadKey]);

    const refresh = () => setReloadKey((value) => value + 1);
    const items = result?.items || [];
    const facets = result?.facets || {};
    const directions = useMemo(() => toFacetOptions(
        facets.directions || facets.direction,
        items.map((item) => item.direction_id ?? item.direction).filter(Boolean),
    ), [facets, items]);
    const statuses = useMemo(() => toFacetOptions(
        facets.statuses || facets.status || facets.rule_status,
        [...Object.keys(RULE_STATUS), ...items.map(ruleStatusOf)],
    ), [facets, items]);
    const indexStatuses = useMemo(() => toFacetOptions(
        facets.index_statuses || facets.index_status,
        [...Object.keys(INDEX_STATUS), ...items.map(indexStatusOf)],
    ), [facets, items]);

    const knowledge = result?.knowledge || {};
    const health = healthMeta(result?.health);
    const revision = knowledge.revision ?? knowledge.knowledge_revision ?? knowledge.snapshot_revision ?? result?.knowledgeRevision;
    const activeCount = knowledge.active_count ?? knowledge.active_rules ?? knowledge.active
        ?? facets?.statuses?.active ?? facets?.status?.active ?? statuses.find((option) => option.value === 'active')?.count;
    const indexedCount = knowledge.indexed_count ?? knowledge.indexed_rules ?? knowledge.indexed
        ?? facets?.index_statuses?.indexed ?? facets?.index_status?.indexed ?? indexStatuses.find((option) => option.value === 'indexed')?.count;
    const healthHint = result?.health?.message || result?.health?.detail || result?.health?.hint;
    const revisionLabel = revision == null ? 'Не передана' : String(revision).toLowerCase().startsWith('r') ? String(revision) : `r${revision}`;

    const applyEdit = (id, saved) => {
        setResult((current) => current ? {
            ...current,
            items: current.items.map((item) => item.id === id ? {
                ...item,
                ...saved,
                correct: saved.correct ?? saved.correct_verdict ?? item.correct,
            } : item),
        } : current);
        setEditId(null);
        refresh();
    };

    const remove = async (item) => {
        if (locked) return;
        const title = item.criterion || item.criterion_name || `#${item.id}`;
        if (!window.confirm(`Удалить правило «${title}»? Оно перестанет участвовать в будущих оценках.`)) return;
        setBusy({ id: item.id, action: 'delete' });
        try {
            await axios.delete(`${apiBaseUrl}/api/ai-qa/adjudications/${item.id}`, { headers: headers() });
            showToast?.('Правило удалено', 'success');
            if (items.length === 1 && page > 1) setPage((value) => value - 1);
            else refresh();
        } catch (requestError) {
            showToast?.(requestError?.response?.data?.error || 'Не удалось удалить правило', 'error');
        } finally {
            setBusy(null);
        }
    };

    const reindex = async (item) => {
        if (locked) return;
        setBusy({ id: item.id, action: 'reindex' });
        try {
            await axios.post(`${apiBaseUrl}/api/ai-qa/adjudications/${item.id}/reindex`, {}, { headers: headers() });
            showToast?.('Повторная индексация поставлена в очередь', 'success');
            refresh();
        } catch (requestError) {
            showToast?.(requestError?.response?.data?.error || 'Не удалось запустить индексацию', 'error');
        } finally {
            setBusy(null);
        }
    };

    const resetFilters = () => {
        setQueryInput(''); setQuery(''); setDirection('all'); setStatus('all'); setIndexStatus('all'); setPage(1);
    };
    const hasFilters = Boolean(query || direction !== 'all' || status !== 'all' || indexStatus !== 'all');
    const totalPages = Math.max(1, Math.ceil((result?.total || 0) / (result?.pageSize || pageSize)));

    useEffect(() => {
        if (result && page > totalPages) setPage(totalPages);
    }, [result, page, totalPages]);

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-3.5" aria-busy={loading}>
            <section className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-4" aria-label="Состояние базы знаний">
                <SummaryCard icon={Database} label="Правил найдено" value={result ? result.total.toLocaleString('ru-RU') : '—'}
                    hint={activeCount != null ? `активных: ${activeCount}` : 'с учётом фильтров'} />
                <SummaryCard icon={Layers3} label="Ревизия знаний" value={revisionLabel}
                    hint={knowledge.updated_at || knowledge.created_at || 'версия production-снимка'} tone={revision != null ? 'blue' : 'slate'} />
                <SummaryCard icon={Activity} label="Состояние" value={health.label} hint={healthHint || 'retrieval и индекс'} tone={health.tone} />
                <SummaryCard icon={Box} label="Проиндексировано" value={indexedCount != null ? indexedCount.toLocaleString('ru-RU') : '—'}
                    hint={knowledge.embedding_model || knowledge.index_model || 'готовы к retrieval'} tone={indexedCount != null ? 'green' : 'slate'} />
            </section>

            {canManage && <RolloutPanel apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast}
                                        onInteractionChange={setRolloutInteraction} />}

            {health.status === 'degraded' || health.status === 'error' ? (
                <div className={`flex flex-col gap-3 rounded-2xl px-4 py-3 ring-1 sm:flex-row sm:items-center sm:justify-between ${
                    health.status === 'error' ? 'bg-rose-50 text-rose-700 ring-rose-200' : 'bg-amber-50 text-amber-800 ring-amber-200'}`}
                    role={health.status === 'error' ? 'alert' : 'status'}>
                    <div className="flex min-w-0 items-start gap-2.5">
                        <AlertTriangle size={17} className="mt-0.5 shrink-0" />
                        <div>
                            <p className="text-[13px] font-semibold">{health.label}</p>
                            <p className="text-[11.5px] opacity-80">{healthHint || 'Часть правил может временно не участвовать в оценках. Проверьте состояние индекса.'}</p>
                        </div>
                    </div>
                    <button type="button" onClick={refresh} disabled={loading || locked} className={`${iosBtnSecondary} shrink-0 !bg-white/70`}>
                        <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />Обновить
                    </button>
                </div>
            ) : null}

            <section className={`${iosCard} space-y-3 p-3.5`} aria-label="Фильтры базы знаний">
                <div className="flex flex-col gap-2.5 lg:flex-row lg:items-center">
                    <label className="relative min-w-[240px] flex-1">
                        <span className="sr-only">Поиск правил</span>
                        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={queryInput} onChange={(event) => setQueryInput(event.target.value)} disabled={locked}
                            placeholder="Поиск по критерию, ситуации или правилу…"
                            className={`${iosInput} py-2 pl-9 pr-9 text-[13px]`} />
                        {queryInput && (
                            <button type="button" onClick={() => setQueryInput('')} disabled={locked}
                                className="absolute right-2.5 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-full text-slate-400 hover:bg-slate-200 hover:text-slate-600"
                                aria-label="Очистить поиск"><X size={13} /></button>
                        )}
                    </label>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:flex">
                        <label>
                            <span className="sr-only">Направление</span>
                            <select value={direction} disabled={locked} onChange={(event) => { setDirection(event.target.value); setPage(1); }} className={`${controlCls} w-full xl:min-w-[150px] xl:w-auto`}>
                                <option value="all">Все направления</option>
                                {directions.map((option) => <option key={option.value} value={option.value}>{option.label}{option.count != null ? ` · ${option.count}` : ''}</option>)}
                            </select>
                        </label>
                        <label>
                            <span className="sr-only">Статус правила</span>
                            <select value={status} disabled={locked} onChange={(event) => { setStatus(event.target.value); setPage(1); }} className={`${controlCls} w-full xl:min-w-[150px] xl:w-auto`}>
                                <option value="all">Любой статус</option>
                                {statuses.map((option) => <option key={option.value} value={option.value}>{RULE_STATUS[option.value]?.label || option.label}{option.count != null ? ` · ${option.count}` : ''}</option>)}
                            </select>
                        </label>
                        <label>
                            <span className="sr-only">Статус индекса</span>
                            <select value={indexStatus} disabled={locked} onChange={(event) => { setIndexStatus(event.target.value); setPage(1); }} className={`${controlCls} w-full xl:min-w-[150px] xl:w-auto`}>
                                <option value="all">Любой индекс</option>
                                {indexStatuses.map((option) => <option key={option.value} value={option.value}>{INDEX_STATUS[option.value]?.label || option.label}{option.count != null ? ` · ${option.count}` : ''}</option>)}
                            </select>
                        </label>
                        <label>
                            <span className="sr-only">Правил на странице</span>
                            <select value={pageSize} disabled={locked} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }} className={`${controlCls} w-full xl:min-w-[105px] xl:w-auto`}>
                                {PAGE_SIZES.map((size) => <option key={size} value={size}>{size} / стр.</option>)}
                            </select>
                        </label>
                    </div>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-2.5">
                    <div className="flex items-center gap-2 text-[11.5px] text-slate-400">
                        {loading ? <><Loader2 size={13} className="animate-spin text-blue-500" />Обновляю данные…</>
                            : error ? <><AlertTriangle size={13} className="text-rose-500" />Показаны последние загруженные данные</>
                                : health.status === 'error' ? <><AlertTriangle size={13} className="text-rose-500" />Данные недоступны</>
                                    : health.status === 'degraded' ? <><AlertTriangle size={13} className="text-amber-500" />Данные загружены с ограничениями</>
                                        : <><Check size={13} className="text-emerald-500" />Данные актуальны</>}
                        {result?.legacy && <IosBadge tone="amber">совместимый режим API</IosBadge>}
                    </div>
                    {hasFilters && <button type="button" onClick={resetFilters} disabled={locked} className={`${iosBtnGhost} !py-1`}>Сбросить фильтры</button>}
                </div>
            </section>

            {error && result && (
                <div className="flex items-center justify-between gap-3 rounded-2xl bg-rose-50 px-4 py-3 text-rose-700 ring-1 ring-rose-200" role="alert">
                    <div className="flex min-w-0 items-center gap-2 text-[12.5px]"><ServerCrash size={16} className="shrink-0" /><span>{error}. Показаны последние загруженные данные.</span></div>
                    <button type="button" onClick={refresh} className={`${iosBtnGhost} shrink-0 !text-rose-700`}><RefreshCw size={14} />Повторить</button>
                </div>
            )}

            {!result && loading ? (
                <div className={`${iosCard} grid gap-3 p-4 sm:grid-cols-2`} aria-label="Загрузка правил">
                    {[0, 1, 2, 3].map((key) => <div key={key} className="h-32 animate-pulse rounded-xl bg-slate-100" />)}
                </div>
            ) : !result && error ? (
                <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-14 text-center`} role="alert">
                    <ServerCrash size={28} className="text-rose-500" />
                    <div><p className="text-[14px] font-semibold text-slate-800">База знаний недоступна</p><p className="mt-1 text-[12.5px] text-slate-500">{error}</p></div>
                    <button type="button" onClick={refresh} disabled={loading} className={iosBtnSecondary}><RefreshCw size={14} />Повторить</button>
                </div>
            ) : result && health.status === 'error' && items.length === 0 ? (
                <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-14 text-center`} role="alert">
                    <ServerCrash size={28} className="text-rose-500" />
                    <div>
                        <p className="text-[14px] font-semibold text-slate-800">Правила сейчас недоступны</p>
                        <p className="mt-1 text-[12.5px] text-slate-500">{healthHint || 'Не удалось получить каталог базы знаний.'}</p>
                    </div>
                    <button type="button" onClick={refresh} disabled={loading} className={iosBtnSecondary}><RefreshCw size={14} />Повторить</button>
                </div>
            ) : result && items.length === 0 ? (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <Database size={28} className="text-slate-300" />
                    <p className="text-[14px] font-semibold text-slate-700">{hasFilters ? 'Ничего не найдено' : 'Правил пока нет'}</p>
                    <p className="max-w-lg text-[12.5px] text-slate-400">
                        {hasFilters ? 'Измените запрос или сбросьте фильтры.' : 'После проверки исправление станет черновиком и появится здесь для дальнейшей модерации.'}
                    </p>
                    {hasFilters && <button type="button" onClick={resetFilters} className={iosBtnSecondary}>Сбросить фильтры</button>}
                </div>
            ) : result ? (
                <>
                    <div className="space-y-2.5" aria-live="polite">
                        {items.map((item) => {
                            const itemRuleStatus = ruleStatusOf(item);
                            const itemIndexStatus = indexStatusOf(item);
                            const isLegacy = item.source_type === 'legacy' || String(item.id || '').startsWith('legacy:');
                            const indexMeta = INDEX_STATUS[itemIndexStatus] || { participates: false };
                            const participates = !isLegacy && itemRuleStatus === 'active' && indexMeta.participates;
                            const indexModel = item.embedding_model || item.index_model || item.model;
                            const indexVersion = item.embedding_version || item.index_version || item.model_version;
                            const canReindex = canManage && ['pending', 'failed', 'error', 'stale'].includes(itemIndexStatus);
                            const criterion = item.criterion || item.criterion_name || 'Без названия';
                            return (
                                <article key={item.id} className={`${iosCard} p-4 transition ${editId === item.id ? 'ring-2 ring-blue-400/60' : 'hover:ring-slate-300'}`}>
                                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                        <div className="min-w-0 flex-1">
                                            <div className="flex flex-wrap items-center gap-1.5">
                                                <h3 className="text-[13.5px] font-semibold text-slate-900">{criterion}</h3>
                                                {item.direction && <IosBadge tone="slate">{item.direction}</IosBadge>}
                                                {isLegacy && <IosBadge tone="amber">Исторический разбор</IosBadge>}
                                                <RuleStatusBadge value={itemRuleStatus} />
                                                <IndexStatusBadge value={itemIndexStatus} />
                                            </div>
                                            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                                <Verdict value={item.ai ?? item.ai_verdict} />
                                                <ArrowRight size={13} className="text-slate-300" aria-hidden="true" />
                                                <Verdict value={item.correct ?? item.correct_verdict} />
                                                <span className="text-[11px] text-slate-400">вердикт ИИ → решение ревьюера</span>
                                            </div>
                                        </div>
                                        <div className="flex flex-wrap items-center justify-between gap-1.5 sm:shrink-0 sm:justify-end">
                                            <div className="flex flex-wrap gap-1.5 sm:mr-1">
                                                <IosBadge tone={participates ? 'green' : 'slate'}>{participates ? 'Участвует в оценках' : 'Не участвует в оценках'}</IosBadge>
                                                <IosBadge tone="blue"><Repeat size={11} />Использований: {item.use_count ?? item.exposure_count ?? 0}</IosBadge>
                                            </div>
                                            {canReindex && (
                                                <button type="button" title="Повторить индексацию" aria-label={`Повторить индексацию правила ${criterion}`}
                                                    disabled={locked} onClick={() => reindex(item)}
                                                    className="grid h-8 w-8 place-items-center rounded-lg text-amber-600 transition hover:bg-amber-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60 disabled:opacity-50">
                                                    {busy?.action === 'reindex' && busy.id === item.id ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                                                </button>
                                            )}
                                            {canManage && (
                                                <>
                                                    <button type="button" title="Редактировать правило" aria-label={`Редактировать правило ${criterion}`}
                                                        disabled={Boolean(busy) || (editId !== null && editId !== item.id)}
                                                        onClick={() => setEditId(editId === item.id ? null : item.id)}
                                                        className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 disabled:opacity-50">
                                                        <Pencil size={14} />
                                                    </button>
                                                    <button type="button" title="Удалить правило" aria-label={`Удалить правило ${criterion}`}
                                                        onClick={() => remove(item)} disabled={locked}
                                                        className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500/60 disabled:opacity-50">
                                                        {busy?.action === 'delete' && busy.id === item.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                    </div>

                                    {editId === item.id ? (
                                        <EditForm item={item} apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast}
                                            onCancel={() => setEditId(null)} onSaved={(saved) => applyEdit(item.id, saved)}
                                            onBusyChange={(isBusy) => setBusy(isBusy ? { id: item.id, action: 'save' } : null)} />
                                    ) : (
                                        <div className="mt-3 space-y-2">
                                            {item.situation && <p className="text-[12.5px] leading-relaxed text-slate-600"><b className="font-semibold text-slate-500">Когда применять:</b> {item.situation}</p>}
                                            {item.excerpt && (
                                                <blockquote className="flex gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-[12.5px] italic leading-relaxed text-slate-600 ring-1 ring-slate-100">
                                                    <Quote size={13} className="mt-0.5 shrink-0 text-slate-300" /><span>«{item.excerpt}»</span>
                                                </blockquote>
                                            )}
                                            <p className="text-[13px] leading-relaxed text-slate-700"><b className="font-semibold text-slate-500">Правило:</b> {item.reason || 'Формулировка не заполнена'}</p>
                                            {item.not_covered && <p className="text-[12.5px] leading-relaxed text-rose-600/85"><b className="font-semibold text-rose-500/80">Не оправдывает:</b> {item.not_covered}</p>}
                                            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-100 pt-2 text-[10.5px] text-slate-400">
                                                <span>{item.by || item.created_by || 'Система'}{(item.date || item.updated_at || item.created_at) ? ` · ${item.date || item.updated_at || item.created_at}` : ''}</span>
                                                {item.rule_version != null && <span>версия правила: {item.rule_version}</span>}
                                                {(indexModel || indexVersion) && <span>индекс: {[indexModel, indexVersion && `v${indexVersion}`].filter(Boolean).join(' · ')}</span>}
                                                {item.content_hash && <span title={item.content_hash}>hash: {String(item.content_hash).slice(0, 10)}</span>}
                                                {!participates && <span className="font-medium text-amber-700">
                                                    {isLegacy ? 'Исторический разбор не участвует в поиске правил' : 'Не участвует в поиске до активации и подготовки индекса'}
                                                </span>}
                                            </div>
                                        </div>
                                    )}
                                </article>
                            );
                        })}
                    </div>
                    <Pagination page={result.page} pageSize={result.pageSize} total={result.total}
                        onPageChange={setPage} disabled={locked || loading} />
                </>
            ) : null}
        </div>
    );
}
