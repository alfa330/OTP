import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { marketingParams } from './filters';
import CustomSelect from '../ui/CustomSelect';
import {
    Search, Check, X, Minus, ArrowRight, Quote, Loader2,
    Pencil, Trash2, Save, RefreshCw, AlertTriangle, Activity,
    ChevronLeft, ChevronRight, ChevronDown, Box, CircleDot,
    SlidersHorizontal, BookOpen, Clock3, ShieldCheck, Settings2, Info,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, IosBadge,
} from '../ui/ios';

const VERDICTS = {
    Correct: { tone: 'green', label: 'Зачёт', Icon: Check },
    Incorrect: { tone: 'red', label: 'Нарушение', Icon: X },
    'N/A': { tone: 'slate', label: 'Не применимо', Icon: Minus },
    Deficiency: { tone: 'amber', label: 'Недочёт', Icon: Minus },
};

const RULE_STATUS = {
    draft: { tone: 'amber', label: 'Черновик' },
    active: { tone: 'green', label: 'Одобрено' },
    deprecated: { tone: 'slate', label: 'Удалено' },
    quarantined: { tone: 'red', label: 'Требует проверки' },
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

const cleanString = (value) => (value == null ? '' : String(value));
const normStatus = (value, fallback) => cleanString(value || fallback).toLowerCase();
const ruleStatusOf = (item) => normStatus(item.rule_status || item.status, 'active');
const indexStatusOf = (item) => normStatus(item.index_status || item.embedding_status, 'unindexed');

function Verdict({ value }) {
    const meta = VERDICTS[value] || { tone: 'slate', label: 'Не указано', Icon: Minus };
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
                    <span className="mb-1 block text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">Статус правила</span>
                    <select value={draft.rule_status} onChange={set('rule_status')} disabled={saving} className={fieldCls}>
                        {Object.entries(RULE_STATUS)
                            .filter(([value]) => !isLegacy || value !== 'active')
                            .map(([value, meta]) => <option key={value} value={value}>
                                {isLegacy && value === 'draft' ? 'Мигрировать в черновик (без цитаты)' : meta.label}
                            </option>)}
                    </select>
                </label>
            </div>
            <p className="text-[12px] leading-relaxed text-slate-500">
                Черновик не применяется ИИ. Выберите «Одобрено» после проверки правила.
                Применение также зависит от режима направления и готовности поиска.
            </p>
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

function RolloutPanel({ apiBaseUrl, headers, showToast, onInteractionChange, department }) {
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
        axios.get(`${apiBaseUrl}/api/ai-qa/rag-rollout`, { params: { department }, headers: headers() })
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
    }, [apiBaseUrl, department]);
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
                department, direction_id: item.direction_id, mode: item.mode,
                canary_percent: item.canary_percent,
                approved_experiment_id: item.approved_experiment_id || null,
                manual_override: Boolean(item.manual_override),
                override_reason: item.manual_reason || null,
            }, { params: { department }, headers: headers() });
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
                                            className={`${fieldCls} min-w-0`}>
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


export function AdjudicationCard({ item, canManage, locked, editing, busy, onEdit, onDelete, onReindex, children }) {
    const [expanded, setExpanded] = useState(false);
    const itemRuleStatus = ruleStatusOf(item);
    const indexState = indexStatusOf(item);
    const legacy = item.source_type === 'legacy' || String(item.id).startsWith('legacy:');
    const ready = !legacy && itemRuleStatus === 'active' && INDEX_STATUS[indexState]?.participates;
    const criterion = item.criterion || item.criterion_name || 'Разбор без названия';
    const usage = item.exposure_count ?? item.use_count ?? 0;
    const needsIndex = ['pending', 'failed', 'error', 'stale', 'unindexed'].includes(indexState);
    const explanation = legacy ? 'Исторический разбор · нужна проверка'
        : ready ? 'Готово к применению в похожих ситуациях'
        : itemRuleStatus === 'draft' ? 'ИИ ещё не применяет это правило'
        : itemRuleStatus === 'deprecated' ? 'Сохранено в истории · ИИ не применяет'
        : itemRuleStatus === 'active' ? 'Одобрено · подготовка к поиску не завершена'
        : 'ИИ не применяет до проверки';
    const detailId = `adjudication-details-${item.id}`;
    return (
        <article className={`${iosCard} min-w-0 overflow-hidden transition-shadow ${editing ? 'ring-2 ring-blue-400' : ''}`} aria-label={`Разбор: ${criterion}`}>
            <div className="p-5 sm:p-6">
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <p className="mb-1 text-[12px] font-medium text-slate-500">{item.direction || 'Направление не указано'}</p>
                        <h3 className="break-words text-[16px] font-semibold leading-snug tracking-[-0.015em] text-slate-900">{criterion}</h3>
                    </div>
                    <span className="shrink-0"><RuleStatusBadge value={itemRuleStatus} /></span>
                </div>
                <div className="mt-4 grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-2xl bg-slate-50 p-3.5">
                    <div className="min-w-0"><p className="mb-1.5 text-[11px] font-medium text-slate-500">Оценка ИИ</p><Verdict value={item.ai ?? item.ai_verdict} /></div>
                    <ArrowRight size={16} className="text-slate-300" aria-hidden="true" />
                    <div className="min-w-0"><p className="mb-1.5 text-[11px] font-medium text-slate-500">Решение проверяющего</p><Verdict value={item.correct ?? item.correct_verdict} /></div>
                </div>
                {editing ? children : <>
                    <div className="mt-5">
                        <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Правило для ИИ</p>
                        <p className={`whitespace-pre-line break-words text-[14px] leading-[1.65] text-slate-800 ${expanded ? '' : 'line-clamp-4'}`}>{item.reason || 'Описание правила пока не заполнено.'}</p>
                    </div>
                    {item.situation && <div className="mt-4">
                        <p className="mb-1 text-[12px] font-semibold text-slate-600">Когда применять</p>
                        <p className={`whitespace-pre-line break-words text-[13px] leading-relaxed text-slate-500 ${expanded ? '' : 'line-clamp-2'}`}>{item.situation}</p>
                    </div>}
                    <button type="button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded} aria-controls={detailId}
                        className="mt-3 inline-flex min-h-10 items-center gap-1 text-[13px] font-medium text-blue-600 outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded-lg">
                        {expanded ? 'Свернуть подробности' : 'Подробнее о разборе'}<ChevronDown size={15} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
                    </button>
                    {expanded && <div id={detailId} className="mt-2 space-y-4 border-t border-slate-100 pt-4">
                        {item.not_covered && <div className="rounded-xl bg-amber-50 px-3.5 py-3 text-amber-900">
                            <p className="mb-1 text-[12px] font-semibold">Когда правило не действует</p>
                            <p className="whitespace-pre-line break-words text-[13px] leading-relaxed">{item.not_covered}</p>
                        </div>}
                        <div>
                            <p className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold text-slate-600"><Quote size={14} />Фрагмент разговора</p>
                            {item.excerpt ? <blockquote className="whitespace-pre-line break-words border-l-2 border-blue-200 pl-3 text-[13px] leading-relaxed text-slate-600">{item.excerpt}</blockquote>
                                : <p className="text-[13px] text-slate-400">Цитата не приложена.</p>}
                            <p className="mt-2 text-[11px] text-slate-400">{item.evidence_status === 'verified' ? 'Цитата проверена по исходному разговору' : 'Без подтверждённой цитаты'}</p>
                        </div>
                        <div className="flex flex-wrap gap-x-4 gap-y-2 text-[11px] text-slate-400">
                            <span>Автор: {item.by || 'Не указан'}</span><span>{item.date || 'Дата не указана'}</span>
                            {item.rule_version && <span>Версия {item.rule_version}</span>}
                            <IndexStatusBadge value={indexState} />
                        </div>
                        {needsIndex && <p className="text-[12px] leading-relaxed text-amber-700">Подготовка к поиску не завершена. {canManage ? 'Можно повторить подготовку кнопкой ниже.' : 'Администратор может повторить подготовку.'}</p>}
                        {canManage && needsIndex && !legacy && <button type="button" onClick={onReindex} disabled={locked} className={iosBtnSecondary}>
                            {busy?.action === 'reindex' ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}Подготовить к поиску
                        </button>}
                    </div>}
                </>}
            </div>
            <div className="flex flex-col items-stretch justify-between gap-3 border-t border-slate-100 bg-slate-50/50 px-5 py-3 sm:flex-row sm:items-center sm:px-6">
                <div className="min-w-0 flex-1">
                    <p className={`flex items-start gap-1.5 text-[11.5px] leading-relaxed ${ready ? 'text-emerald-700' : 'text-slate-500'}`}>
                        {ready ? <ShieldCheck size={14} className="mt-0.5 shrink-0" /> : <Clock3 size={14} className="mt-0.5 shrink-0" />}{explanation}
                    </p>
                    <p className="mt-0.5 pl-5 text-[11px] text-slate-400">Передавалось ИИ: {Number(usage).toLocaleString('ru-RU')}</p>
                </div>
                {canManage && !editing && <div className="flex shrink-0 items-center justify-end gap-1">
                    <button type="button" onClick={onEdit} disabled={locked} className={`${iosBtnSecondary} !min-h-10 !px-3 !text-[12px]`}>
                        <Pencil size={13} />{itemRuleStatus === 'draft' ? 'Проверить' : 'Изменить'}
                    </button>
                    {itemRuleStatus !== 'deprecated' && (
                        <button type="button" onClick={onDelete} disabled={locked} aria-label={`Удалить правило ${criterion}`}
                            className={`${iosBtnGhost} !h-10 !w-10 !p-0 hover:!bg-rose-50 hover:!text-rose-600`}>
                            {busy?.action === 'delete' ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
                        </button>
                    )}
                </div>}
            </div>
        </article>
    );
}

export default function AdjudicationsRag(props) {
    const { apiBaseUrl, withAccessTokenHeader, showToast, canManage, department, departmentName,
            onInteractionChange, filters } = props;
    /* Отбор по сделке из панели раздела (ТЗ #317): правило попадает в выдачу,
       если разговор, из которого его вывели, связан с подходящей сделкой.
       Остальной отбор панели к каталогу не относится и сюда не уходит. */
    const dealParams = marketingParams(filters);
    const dealSignature = JSON.stringify(dealParams);
    const [showFilters, setShowFilters] = useState(false);
    const [showSettings, setShowSettings] = useState(false);
    const [queryInput, setQueryInput] = useState('');
    const [query, setQuery] = useState('');
    const [view, setView] = useState('catalog');
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
    const locked = editId !== null || Boolean(busy) || rolloutInteraction.busy || rolloutInteraction.editing;

    useEffect(() => {
        const timer = window.setTimeout(() => {
            setQuery(queryInput.trim());
            setPage(1);
        }, 350);
        return () => window.clearTimeout(timer);
    }, [queryInput]);

    // Новый отбор по сделке — с первой страницы: на пятой странице узкой
    // выборки обычно пусто, и это читалось бы как «ничего не нашлось».
    const dealSignatureRef = useRef(dealSignature);
    useEffect(() => {
        if (dealSignatureRef.current === dealSignature) return;
        dealSignatureRef.current = dealSignature;
        setPage(1);
    }, [dealSignature]);

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
        // Вкладка «Удалённые» — это явный запрос deprecated-строк; каталог бэкенд
        // по умолчанию отдаёт без них.
        const effectiveStatus = view === 'deleted' ? 'deprecated' : status;
        const requestState = { q: query, direction, status: effectiveStatus, indexStatus, page, pageSize };
        axios.get(`${apiBaseUrl}/api/ai-qa/adjudications`, {
            params: {
                page, page_size: pageSize,
                // Каталог правил — политика оценки НАПРАВЛЕНИЙ отдела: без отдела
                // глава СЗоВ видел бы правила Тез КЦ (бэкенд режет тем же параметром).
                ...(department ? { department } : {}),
                ...(query ? { q: query } : {}),
                ...(direction !== 'all' ? { direction } : {}),
                ...(effectiveStatus !== 'all' ? { status: effectiveStatus } : {}),
                ...(indexStatus !== 'all' ? { index_status: indexStatus } : {}),
                ...dealParams,
            },
            headers: headers(),
            signal: controller.signal,
        }).then((response) => {
            if (controller.signal.aborted || currentRequest !== requestId.current) return;
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
    }, [apiBaseUrl, department, query, view, direction, status, indexStatus, page, pageSize, reloadKey,
        dealSignature]);

    const refresh = () => setReloadKey((value) => value + 1);
    const items = result?.items || [];
    const facets = result?.facets || {};
    const directions = useMemo(() => toFacetOptions(
        facets.directions || facets.direction,
        facets.directions?.length ? [] : items.map((item) => item.direction).filter(Boolean),
    ), [facets, items]);
    const statuses = useMemo(() => toFacetOptions(
        facets.statuses || facets.status || facets.rule_status,
        [...Object.keys(RULE_STATUS), ...items.map(ruleStatusOf)],
    ), [facets, items]);
    // Deprecated живут во вкладке «Удалённые», из фильтра каталога они исключены.
    const statusOptions = useMemo(() => statuses.filter((option) => option.value !== 'deprecated'), [statuses]);
    const deletedCount = statuses.find((option) => option.value === 'deprecated')?.count;
    const knowledge = result?.knowledge || {};
    const health = healthMeta(result?.health);
    const activeCount = knowledge.active_count ?? knowledge.active_rules ?? knowledge.active
        ?? facets?.statuses?.active ?? facets?.status?.active ?? statuses.find((option) => option.value === 'active')?.count;

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
        if (!window.confirm(`Удалить правило «${title}»? Оно перестанет участвовать в оценках и переместится во вкладку «Удалённые».`)) return;
        setBusy({ id: item.id, action: 'delete' });
        try {
            await axios.delete(`${apiBaseUrl}/api/ai-qa/adjudications/${item.id}`, { headers: headers() });
            showToast?.('Разбор перемещён во вкладку «Удалённые»', 'success');
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
    /* Отбор по сделке снимается в панели над вкладкой, а не здесь, но пустой
       каталог под ним — это «ничего не нашлось», а не «правил пока нет». */
    const hasDealFilters = Object.keys(dealParams).length > 0;
    const totalPages = Math.max(1, Math.ceil((result?.total || 0) / (result?.pageSize || pageSize)));

    useEffect(() => {
        if (result && page > totalPages) setPage(totalPages);
    }, [result, page, totalPages]);

    const draftCount = statuses.find((option) => option.value === 'draft')?.count ?? 0;
    const catalogCount = statuses.reduce((sum, option) => sum + (option.value !== 'deprecated' ? Number(option.count || 0) : 0), 0);
    const quickFilters = [
        { key: 'all', label: 'Все разборы', count: catalogCount },
        { key: 'draft', label: 'Ждут проверки', count: draftCount },
        { key: 'active', label: 'Одобренные', count: activeCount ?? 0 },
        { key: 'deleted', label: 'Удалённые', count: deletedCount ?? 0 },
    ];
    const selectedQuickFilter = view === 'deleted' ? 'deleted' : status;
    const chooseQuickFilter = (key) => {
        if (locked) return;
        setView(key === 'deleted' ? 'deleted' : 'catalog');
        setStatus(key === 'deleted' ? 'all' : key);
        setPage(1);
    };

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4" aria-busy={loading}>
            <section className={`${iosCard} overflow-hidden`}>
                <div className="flex items-start justify-between gap-2 px-4 pt-5 sm:px-6 sm:pt-6">
                    <div className="flex min-w-0 items-start gap-3.5">
                        <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-blue-50 text-blue-600"><BookOpen size={22} strokeWidth={1.7} /></div>
                        <div className="min-w-0">
                            <h2 className="text-[19px] font-semibold tracking-[-0.025em] text-slate-900">База разборов</h2>
                            <p className="mt-0.5 text-[13px] font-medium text-slate-500">{departmentName || 'Выбранный отдел'}</p>
                        </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                        {canManage && <button type="button" onClick={() => setShowSettings(!showSettings)} disabled={locked} aria-expanded={showSettings} aria-label="Настройки ИИ" title="Настройки ИИ"
                            className={`${iosBtnGhost} !min-h-10 !px-2 sm:!px-3`}><Settings2 size={16} /><span className="hidden sm:inline">Настройки ИИ</span></button>}
                        <button type="button" onClick={refresh} disabled={locked || loading} aria-label="Обновить базу разборов" className={`${iosBtnGhost} !h-10 !w-10 !p-0`}>
                            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
                        </button>
                    </div>
                </div>
                <p className="px-4 pb-5 pt-3 text-[13px] leading-relaxed text-slate-500 sm:px-6">Проверенные правила помогают ИИ оценивать похожие разговоры.</p>
                <div className="grid grid-cols-3 divide-x divide-slate-100 border-t border-slate-100 bg-slate-50/50">
                    {[{ value: catalogCount, label: 'Разборов в отделе', key: 'all' },
                      { value: draftCount, label: 'Ждут проверки', key: 'draft' },
                      { value: activeCount ?? 0, label: 'Одобрено', key: 'active' }].map((stat) => (
                        <button key={stat.key} type="button" disabled={locked || !result} onClick={() => chooseQuickFilter(stat.key)}
                            className="min-w-0 px-2 py-4 text-center transition hover:bg-slate-100/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 sm:px-5 sm:text-left">
                            <span className="block text-[23px] font-semibold leading-none tracking-tight tabular-nums text-slate-900">{result && health.status !== 'error' ? Number(stat.value).toLocaleString('ru-RU') : '—'}</span>
                            <span className="mt-2 block text-[11px] leading-snug text-slate-500 sm:text-[12px]">{stat.label}</span>
                        </button>
                    ))}
                </div>
            </section>

            {showSettings && canManage && <RolloutPanel key={department} department={department} apiBaseUrl={apiBaseUrl} headers={headers}
                showToast={showToast} onInteractionChange={setRolloutInteraction} />}

            {result && !loading && health.status === 'healthy' && activeCount === 0 && draftCount > 0 && (
                <div className="flex items-start gap-2.5 rounded-2xl bg-amber-50 px-4 py-3 text-amber-900">
                    <Info size={17} className="mt-0.5 shrink-0" />
                    <p className="text-[12.5px] leading-relaxed"><span className="font-semibold">Разборы пока не влияют на оценки.</span> {canManage ? 'Откройте «Проверить» в карточке и одобрите подходящее правило. Режим применения задаётся в настройках ИИ.' : 'Правила ожидают проверки и одобрения администратором.'}</p>
                </div>
            )}

            <section className="space-y-3" aria-label="Фильтры базы разборов">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="grid w-full grid-cols-2 gap-1 rounded-2xl bg-slate-200/60 p-1 sm:flex sm:w-auto sm:flex-wrap" aria-label="Статус разборов">
                        {quickFilters.map((filter) => <button type="button" key={filter.key} disabled={locked}
                            aria-pressed={selectedQuickFilter === filter.key} onClick={() => chooseQuickFilter(filter.key)}
                            className={`inline-flex min-h-10 items-center gap-2 rounded-xl px-3 text-[12px] font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${selectedQuickFilter === filter.key ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>
                            {filter.label}{result && <span className={`rounded-md px-1.5 py-0.5 text-[10px] tabular-nums ${selectedQuickFilter === filter.key ? 'bg-slate-100 text-slate-600' : 'text-slate-400'}`}>{filter.count}</span>}
                        </button>)}
                    </div>
                    <span className="text-[12px] text-slate-400" role="status">{loading ? 'Загружаем…' : result && !error && health.status !== 'error' ? `Найдено: ${result.total}` : ''}</span>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                    <div className="relative min-w-0 flex-1">
                        <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input aria-label="Поиск по разборам" placeholder="Найти правило, ситуацию или цитату" value={queryInput} disabled={locked}
                            onChange={(event) => setQueryInput(event.target.value)} className={`${iosInput} !min-h-11 !bg-white !pl-10 !pr-10 ring-1 ring-slate-200/70`} />
                        {queryInput && <button type="button" aria-label="Очистить поиск" disabled={locked} onClick={() => setQueryInput('')}
                            className="absolute right-1 top-1/2 grid h-10 w-9 -translate-y-1/2 place-items-center rounded-lg text-slate-400 hover:text-slate-700"><X size={15} /></button>}
                    </div>
                    <div className="w-full sm:w-56">
                        <CustomSelect ariaLabel="Направление разборов" value={direction} disabled={locked} placeholder="Все направления"
                            onChange={(value) => { setDirection(value); setPage(1); }}
                            options={[{ value: 'all', label: 'Все направления' }, ...directions.map((option) => ({ ...option, label: option.label }))]} />
                    </div>
                    <button type="button" onClick={() => setShowFilters(!showFilters)} aria-expanded={showFilters} disabled={locked}
                        className={`${iosBtnSecondary} !min-h-11 !bg-white ring-1 ring-slate-200/70 ${showFilters ? '!text-blue-600' : ''}`}>
                        <SlidersHorizontal size={15} />Фильтры{indexStatus !== 'all' && <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />}
                    </button>
                </div>
                {showFilters && <div className={`${iosCard} grid items-end gap-3 p-4 sm:grid-cols-3`}>
                    <label className="space-y-1.5 text-[12px] text-slate-500"><span>Статус правила</span>
                        <CustomSelect value={status} disabled={locked || view === 'deleted'} onChange={(value) => { setStatus(value); setPage(1); }}
                            options={[{ value: 'all', label: 'Любой статус' }, ...statusOptions.map((option) => ({ value: option.value, label: RULE_STATUS[option.value]?.label || option.label }))]} />
                    </label>
                    <label className="space-y-1.5 text-[12px] text-slate-500"><span>Готовность к поиску</span>
                        <CustomSelect value={indexStatus} disabled={locked} onChange={(value) => { setIndexStatus(value); setPage(1); }}
                            options={[{ value: 'all', label: 'Любая' }, { value: 'ready', label: 'Подготовлено' }, { value: 'pending', label: 'Ожидает подготовки' }, { value: 'stale', label: 'Нужно обновить' }, { value: 'error', label: 'Ошибка подготовки' }]} />
                    </label>
                    <label className="space-y-1.5 text-[12px] text-slate-500"><span>Разборов на странице</span>
                        <CustomSelect value={pageSize} disabled={locked} onChange={(value) => { setPageSize(Number(value)); setPage(1); }} options={PAGE_SIZES.map((value) => ({ value, label: String(value) }))} />
                    </label>
                </div>}
                {hasFilters && <div className="flex flex-wrap items-center gap-2 text-[12px] text-slate-500">
                    {query && <span className="max-w-full break-words rounded-lg bg-slate-100 px-2.5 py-1">Поиск: {query}</span>}
                    {direction !== 'all' && <span className="rounded-lg bg-slate-100 px-2.5 py-1">{directions.find((option) => option.value === direction)?.label || direction}</span>}
                    {indexStatus !== 'all' && <span className="rounded-lg bg-slate-100 px-2.5 py-1">{INDEX_STATUS[indexStatus]?.label || indexStatus}</span>}
                    <button type="button" disabled={locked} onClick={resetFilters} className="min-h-9 rounded-lg px-2 text-blue-600 hover:bg-blue-50">Сбросить фильтры</button>
                </div>}
            </section>

            {(error || (result && health.status !== 'healthy')) && <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-amber-50 p-4 text-amber-900" role="alert">
                <p className="flex items-center gap-2 text-[13px]"><AlertTriangle size={17} className="shrink-0" />{error || 'Часть данных базы недоступна. Попробуйте обновить страницу.'}</p>
                <button type="button" disabled={loading || locked} onClick={refresh} className={iosBtnGhost}>Повторить загрузку</button>
            </div>}

            {loading ? <div className="grid gap-4 xl:grid-cols-2" aria-label="Загрузка разборов">
                {[0, 1, 2, 3].map((key) => <div key={key} className={`${iosCard} space-y-4 p-6 motion-safe:animate-pulse`}><div className="h-4 w-1/3 rounded bg-slate-100" /><div className="h-6 w-2/3 rounded bg-slate-100" /><div className="h-16 rounded-xl bg-slate-100" /><div className="h-24 rounded-xl bg-slate-50" /></div>)}
            </div> : result && !error && health.status !== 'error' && items.length === 0 ? <div className={`${iosCard} flex flex-col items-center px-6 py-14 text-center`}>
                <div className="mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-slate-100 text-slate-400"><BookOpen size={26} strokeWidth={1.5} /></div>
                <h3 className="text-[16px] font-semibold text-slate-800">{hasFilters || hasDealFilters ? 'Подходящих разборов нет' : view === 'deleted' ? 'Удалённых разборов нет' : 'В этом отделе пока нет разборов'}</h3>
                <p className="mt-2 max-w-md text-[13px] leading-relaxed text-slate-500">{hasFilters ? 'Попробуйте другое направление, статус или поисковый запрос.' : hasDealFilters ? 'Измените отбор по сделке в панели раздела.' : view === 'deleted' ? 'Здесь сохраняется история удалённых правил.' : 'Исправьте оценку ИИ при проверке разговора — разбор появится здесь как черновик.'}</p>
                {hasFilters && <button type="button" onClick={resetFilters} className={`${iosBtnSecondary} mt-5`}>Сбросить фильтры</button>}
            </div> : result && !error && health.status !== 'error' ? <>
                <div className="grid items-start gap-4 xl:grid-cols-2">
                    {items.map((item) => <AdjudicationCard key={item.id} item={item} canManage={canManage} locked={locked}
                        editing={editId === item.id} busy={busy?.id === item.id ? busy : null}
                        onEdit={() => setEditId(item.id)} onDelete={() => remove(item)} onReindex={() => reindex(item)}>
                        <EditForm item={item} apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast}
                            onCancel={() => setEditId(null)} onSaved={(saved) => applyEdit(item.id, saved)}
                            onBusyChange={(isBusy) => setBusy(isBusy ? { id: item.id, action: 'save' } : null)} />
                    </AdjudicationCard>)}
                </div>
                <Pagination page={result.page} pageSize={result.pageSize} total={result.total} onPageChange={setPage} disabled={locked || loading} />
            </> : null}
        </div>
    );
}
