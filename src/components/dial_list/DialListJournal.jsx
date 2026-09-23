import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosModal, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker } from '../ui/DateRangePicker';

/*
 * Журнал водителей раздела «Обзвон из телефона» (запрос владельца 23.09.2026).
 *
 * Список всех, кого загрузили для обзвона: этап, ответственный оператор, сколько
 * было попыток и чем кончился последний звонок. Карточка — вся история: каждая
 * попытка с исходом и длительностью, запись разговора (если дозвонились), ручные
 * действия руководителя. Руководитель может вернуть человека в список (попытки
 * обнуляются) или исключить его из обзвона.
 *
 * Номер телефона здесь — только маска (последние 4 цифры), как и везде в
 * разделе: файл с номерами есть у того, кто его загрузил, а сервер номер наружу
 * не отдаёт вовсе.
 */

const PAGE = 50;

const STAGE_TONE = {
    queue: 'slate', waiting: 'amber', issued: 'blue', answered: 'green', exhausted: 'red', excluded: 'slate',
};

const RESULT_LABEL = {
    answered: 'Дозвонились', busy: 'Занято', no_answer: 'Не ответил', other: 'Не состоялся', failed: 'Ошибка АТС',
};

const STATE_LABEL = {
    requested: 'Звонок запрошен у АТС',
    leg_ringing: 'АТС звонит оператору',
    leg_answered: 'Оператор на линии, набираем водителя',
    ended: 'Разговор завершён, ждём исход от АТС',
};

const EVENT_LABEL = {
    requeue: 'Вернул(а) в список',
    restore: 'Вернул(а) из исключённых',
    exclude: 'Исключил(а) из обзвона',
    note: 'Заметка',
};

const readError = async (resp) => {
    const data = await resp.json().catch(() => ({}));
    return data?.error || `HTTP ${resp.status}`;
};

const pad2 = (n) => String(n).padStart(2, '0');

const fmtDateTime = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const now = new Date();
    const sameYear = d.getFullYear() === now.getFullYear();
    const day = `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}${sameYear ? '' : `.${d.getFullYear()}`}`;
    return `${day}, ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
};

const fmtDate = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()}`;
};

const fmtDuration = (sec) => {
    const s = Math.max(0, Number(sec) || 0);
    if (s === 0) return '';
    if (s < 60) return `${s} сек`;
    return `${Math.floor(s / 60)}:${pad2(s % 60)}`;
};

const initials = (name) => {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return '•';
    return parts.slice(0, 2).map((p) => p[0].toUpperCase()).join('');
};

const resultOf = (call) => {
    if (!call) return '';
    if (call.result) return RESULT_LABEL[call.result] || call.result;
    return STATE_LABEL[call.state] || '';
};

const Avatar = ({ name, tone = 'slate' }) => (
    <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-full text-[12px] font-semibold ${
        tone === 'green' ? 'bg-emerald-50 text-emerald-700'
            : tone === 'blue' ? 'bg-blue-50 text-blue-700'
                : tone === 'amber' ? 'bg-amber-50 text-amber-700'
                    : tone === 'red' ? 'bg-rose-50 text-rose-600'
                        : 'bg-slate-100 text-slate-500'}`}>
        {initials(name)}
    </div>
);

const StageBadge = ({ lead }) => (
    <IosBadge tone={STAGE_TONE[lead.stage] || 'slate'}>{lead.stage_label || lead.stage}</IosBadge>
);

/* ─── строка списка ─────────────────────────────────────────────────────── */

const LeadRow = ({ lead, onOpen }) => {
    const last = lead.last_call;
    const resultText = resultOf(last);
    return (
        <button
            type="button"
            onClick={() => onOpen(lead)}
            className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50 active:bg-slate-100"
        >
            <Avatar name={lead.full_name} tone={STAGE_TONE[lead.stage]} />
            <div className="min-w-0 flex-1">
                <div className={`truncate text-[14px] font-semibold ${lead.stage === 'excluded' ? 'text-slate-400 line-through' : 'text-slate-900'}`}>
                    {lead.full_name || 'Без имени'}
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] text-slate-500">
                    <span className="font-mono tabular-nums">{lead.phone_masked}</span>
                    <span className="text-slate-300">·</span>
                    <span>попыток {lead.attempts_total}/{lead.max_attempts}</span>
                    {lead.note && (
                        <>
                            <span className="text-slate-300">·</span>
                            <span className="truncate text-slate-400"><FaIcon className="fas fa-pen" style={{ width: 10, height: 10 }} /> {lead.note}</span>
                        </>
                    )}
                </div>
            </div>
            <div className="hidden w-44 shrink-0 md:block">
                <div className="truncate text-[12.5px] text-slate-700">{lead.responsible?.name || '—'}</div>
                <div className="text-[11px] text-slate-400">
                    {lead.responsible ? (lead.responsible_is_current ? 'сейчас в его списке' : 'звонил последним') : 'ещё никому не выдавался'}
                </div>
            </div>
            <div className="w-36 shrink-0 text-right sm:w-44">
                <StageBadge lead={lead} />
                <div className="mt-1 truncate text-[11px] text-slate-400">
                    {last ? `${fmtDateTime(last.at)}${resultText ? ` · ${resultText}` : ''}` : 'звонков не было'}
                </div>
            </div>
            <FaIcon className="fas fa-chevron-right shrink-0 text-slate-300" style={{ width: 12, height: 12 }} />
        </button>
    );
};

/* ─── карточка ──────────────────────────────────────────────────────────── */

const MetaCell = ({ label, children }) => (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
        <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
        <div className="mt-0.5 text-[13.5px] text-slate-800">{children || '—'}</div>
    </div>
);

const AttemptItem = ({ attempt, onRecording, recording }) => {
    const result = attempt.result;
    const tone = result === 'answered' ? 'green' : result === 'failed' ? 'red' : result ? 'amber' : 'blue';
    const icon = result === 'answered' ? 'fa-phone-volume' : result === 'failed' ? 'fa-triangle-exclamation' : result ? 'fa-phone-slash' : 'fa-phone';
    const title = result ? (RESULT_LABEL[result] || result) : (STATE_LABEL[attempt.state] || 'Попытка');
    const rec = recording || {};
    return (
        <li className="flex gap-3 px-4 py-3">
            <div className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${
                tone === 'green' ? 'bg-emerald-50 text-emerald-600' : tone === 'red' ? 'bg-rose-50 text-rose-500'
                    : tone === 'amber' ? 'bg-amber-50 text-amber-600' : 'bg-blue-50 text-blue-600'}`}>
                <FaIcon className={`fas ${icon}`} style={{ width: 13, height: 13 }} />
            </div>
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-[13.5px] font-semibold text-slate-900">{title}</span>
                    {attempt.billsec > 0 && <span className="text-[12.5px] text-slate-500">разговор {fmtDuration(attempt.billsec)}</span>}
                    <span className="ml-auto text-[12px] tabular-nums text-slate-400">{fmtDateTime(attempt.requested_at)}</span>
                </div>
                <div className="mt-0.5 text-[12.5px] text-slate-500">
                    {attempt.operator?.name || 'Оператор'}
                    {attempt.internal_number && <> · линия {attempt.internal_number}</>}
                    {attempt.final_source && <> · исход: {attempt.final_source === 'webhook' ? 'вебхук Binotel' : attempt.final_source === 'poll' ? 'опрос Binotel' : attempt.final_source}</>}
                </div>
                {attempt.api_error && (
                    <div className="mt-1 rounded-lg bg-rose-50 px-2.5 py-1.5 text-[12px] text-rose-600">{attempt.api_error}</div>
                )}
                {attempt.recording_available && (
                    <div className="mt-2">
                        {rec.url ? (
                            <audio controls preload="none" src={rec.url} className="h-9 w-full max-w-md" />
                        ) : (
                            <button
                                type="button"
                                onClick={() => onRecording(attempt.id)}
                                disabled={rec.loading}
                                className={`${iosBtnSecondary} py-1.5 text-[12.5px]`}
                            >
                                <FaIcon className={rec.loading ? 'fas fa-spinner fa-spin' : 'fas fa-play'} style={{ width: 11, height: 11 }} />
                                {rec.loading ? 'Запрашиваем у АТС…' : 'Запись разговора'}
                            </button>
                        )}
                        {rec.error && <div className="mt-1 text-[12px] text-rose-600">{rec.error}</div>}
                    </div>
                )}
            </div>
        </li>
    );
};

const EventItem = ({ event }) => (
    <li className="flex gap-3 px-4 py-3">
        <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500">
            <FaIcon className={`fas ${event.kind === 'exclude' ? 'fa-ban' : event.kind === 'note' ? 'fa-pen' : 'fa-rotate-left'}`} style={{ width: 12, height: 12 }} />
        </div>
        <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-[13.5px] font-semibold text-slate-900">{EVENT_LABEL[event.kind] || event.kind}</span>
                <span className="text-[12.5px] text-slate-500">{event.actor?.name || 'руководитель'}</span>
                <span className="ml-auto text-[12px] tabular-nums text-slate-400">{fmtDateTime(event.at)}</span>
            </div>
            {event.note && <div className="mt-0.5 text-[12.5px] text-slate-600">{event.note}</div>}
        </div>
    </li>
);

const LeadCard = ({ lead, loading, error, canEdit, busy, onRequeue, onExclude, onSaveNote, onRecording, recordings }) => {
    const [confirm, setConfirm] = useState(null); // 'requeue' | 'exclude' | null
    const [reason, setReason] = useState('');
    const [note, setNote] = useState(lead?.note || '');
    useEffect(() => { setNote(lead?.note || ''); setConfirm(null); setReason(''); }, [lead?.id, lead?.note]);

    if (loading && !lead) {
        return <div className="px-4 py-10 text-center text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>;
    }
    if (error && !lead) {
        return <div className="px-4 py-6 text-[13px] text-rose-600">{error}</div>;
    }
    if (!lead) return null;

    const timeline = [
        ...(lead.attempts || []).map((a) => ({ kind: 'attempt', at: a.requested_at, item: a })),
        ...(lead.events || []).map((e) => ({ kind: 'event', at: e.at, item: e })),
    ].sort((x, y) => String(y.at || '').localeCompare(String(x.at || '')));

    const canRequeue = canEdit && ['answered', 'exhausted', 'waiting', 'excluded'].includes(lead.stage);
    const canExclude = canEdit && lead.stage !== 'excluded';
    const firstBatch = lead.batches?.[0];
    const lastBatch = lead.batches?.[lead.batches.length - 1];

    return (
        <div className="space-y-4">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                <MetaCell label="Ответственный">
                    {lead.responsible?.name}
                    {lead.responsible && (
                        <div className="text-[11.5px] text-slate-400">{lead.responsible_is_current ? 'сейчас в его списке' : 'звонил последним'}</div>
                    )}
                </MetaCell>
                <MetaCell label="Попыток">
                    {lead.attempts_total} из {lead.max_attempts}
                    {lead.next_retry_at && <div className="text-[11.5px] text-slate-400">повтор не раньше {fmtDateTime(lead.next_retry_at)}</div>}
                </MetaCell>
                <MetaCell label="Последний звонок">
                    {lead.last_call ? (
                        <>
                            {fmtDateTime(lead.last_call.at)}
                            <div className="text-[11.5px] text-slate-400">{resultOf(lead.last_call)}{lead.last_call.billsec > 0 ? ` · ${fmtDuration(lead.last_call.billsec)}` : ''}</div>
                        </>
                    ) : 'не было'}
                </MetaCell>
                <MetaCell label="Дозвонились">
                    {lead.answered_at ? fmtDateTime(lead.answered_at) : 'нет'}
                </MetaCell>
                <MetaCell label="Загружен">
                    {fmtDate(firstBatch?.uploaded_at || lead.created_at)}
                    <div className="truncate text-[11.5px] text-slate-400" title={firstBatch?.file_name}>
                        {firstBatch?.file_name || lead.batch?.file_name || ''}{firstBatch?.uploaded_by ? ` · ${firstBatch.uploaded_by}` : ''}
                    </div>
                </MetaCell>
                <MetaCell label="В файлах">
                    {lead.upload_count} {lead.upload_count === 1 ? 'раз' : 'раза'}
                    {lead.upload_count > 1 && lastBatch && (
                        <div className="truncate text-[11.5px] text-slate-400">последний: {fmtDate(lastBatch.uploaded_at)}</div>
                    )}
                </MetaCell>
            </div>

            {canEdit && (
                <div className="flex items-start gap-2">
                    <textarea
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        rows={2}
                        maxLength={500}
                        placeholder="Заметка руководителя (видна только здесь)"
                        className={`${iosInput} resize-none text-[13px]`}
                    />
                    <button
                        type="button"
                        onClick={() => onSaveNote(note)}
                        disabled={busy || note.trim() === (lead.note || '').trim()}
                        className={`${iosBtnSecondary} shrink-0 py-2`}
                    >
                        <FaIcon className="fas fa-floppy-disk" style={{ width: 12, height: 12 }} />
                    </button>
                </div>
            )}
            {!canEdit && lead.note && (
                <div className="rounded-xl bg-amber-50 px-3 py-2 text-[13px] text-amber-800">{lead.note}</div>
            )}

            <section>
                <div className="mb-1.5 flex items-center justify-between px-1">
                    <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">История</div>
                    <div className="text-[11.5px] text-slate-400">{timeline.length ? `${timeline.length} записей` : ''}</div>
                </div>
                <div className={`${iosCard} overflow-hidden`}>
                    {timeline.length === 0 ? (
                        <div className="px-4 py-6 text-center text-[13px] text-slate-500">Звонков по этому водителю ещё не было.</div>
                    ) : (
                        <ul className="divide-y divide-slate-100">
                            {timeline.map((t) => (t.kind === 'attempt'
                                ? <AttemptItem key={`a-${t.item.id}`} attempt={t.item} onRecording={onRecording} recording={recordings[t.item.id]} />
                                : <EventItem key={`e-${t.item.id}`} event={t.item} />))}
                        </ul>
                    )}
                </div>
            </section>

            {(canRequeue || canExclude) && (
                <div className={`${iosCard} p-3`}>
                    {confirm ? (
                        <div className="space-y-2">
                            <div className="text-[13px] text-slate-700">
                                {confirm === 'exclude'
                                    ? 'Исключить водителя из обзвона? Операторы его больше не получат.'
                                    : 'Вернуть в список? Счётчик попыток обнулится, водитель попадёт в ближайшую порцию.'}
                            </div>
                            <input
                                value={reason}
                                onChange={(e) => setReason(e.target.value)}
                                maxLength={500}
                                placeholder="Причина (необязательно)"
                                className={`${iosInput} text-[13px]`}
                            />
                            <div className="flex justify-end gap-2">
                                <button type="button" onClick={() => { setConfirm(null); setReason(''); }} className={iosBtnGhost}>Отмена</button>
                                <button
                                    type="button"
                                    disabled={busy}
                                    onClick={() => (confirm === 'exclude' ? onExclude(reason) : onRequeue(reason))}
                                    className={`${iosBtnPrimary} ${confirm === 'exclude' ? 'bg-rose-600 hover:bg-rose-700' : ''}`}
                                >
                                    {busy && <FaIcon className="fas fa-spinner fa-spin" style={{ width: 12, height: 12 }} />}
                                    {confirm === 'exclude' ? 'Исключить' : 'Вернуть в список'}
                                </button>
                            </div>
                        </div>
                    ) : (
                        <div className="flex flex-wrap justify-end gap-2">
                            {canExclude && (
                                <button type="button" onClick={() => setConfirm('exclude')} className={`${iosBtnSecondary} text-rose-600`}>
                                    <FaIcon className="fas fa-ban" style={{ width: 12, height: 12 }} /> Исключить из обзвона
                                </button>
                            )}
                            {canRequeue && (
                                <button type="button" onClick={() => setConfirm('requeue')} className={iosBtnPrimary}>
                                    <FaIcon className="fas fa-rotate-left" style={{ width: 12, height: 12 }} /> Вернуть в список
                                </button>
                            )}
                        </div>
                    )}
                    {lead.stage === 'issued' && (
                        <div className="mt-2 text-[12px] text-slate-400">Строка сейчас у оператора — вернуть или исключить можно после её обработки.</div>
                    )}
                </div>
            )}
        </div>
    );
};

/* ─── сам журнал ────────────────────────────────────────────────────────── */

const DialListJournal = ({ apiBaseUrl, authHeaders, departmentId, batches = [], canEdit = true, showToast, onChanged }) => {
    const [q, setQ] = useState('');
    const [qDebounced, setQDebounced] = useState('');
    const [stage, setStage] = useState('');
    const [operatorId, setOperatorId] = useState('');
    const [batchId, setBatchId] = useState('');
    const [range, setRange] = useState({ from: '', to: '' });
    const [sort, setSort] = useState('activity');

    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [byStage, setByStage] = useState({});
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [error, setError] = useState('');
    const [users, setUsers] = useState([]);

    const [openId, setOpenId] = useState(null);
    const [card, setCard] = useState(null);
    const [cardLoading, setCardLoading] = useState(false);
    const [cardError, setCardError] = useState('');
    const [busy, setBusy] = useState(false);
    const [recordings, setRecordings] = useState({});
    const requestSeq = useRef(0);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    useEffect(() => {
        const t = setTimeout(() => setQDebounced(q.trim()), 300);
        return () => clearTimeout(t);
    }, [q]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/users`, { credentials: 'include', headers: authHeaders() });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
                if (!cancelled) setUsers(Array.isArray(data.users) ? data.users : Array.isArray(data.items) ? data.items : []);
            } catch {
                if (!cancelled) setUsers([]);
            }
        })();
        return () => { cancelled = true; };
    }, [apiBaseUrl, authHeaders, departmentId]);

    const buildQuery = useCallback((offset) => {
        const qs = new URLSearchParams({ limit: String(PAGE), offset: String(offset), sort });
        if (qDebounced) qs.set('q', qDebounced);
        if (stage) qs.set('stage', stage);
        if (operatorId) qs.set('operator_id', operatorId);
        if (batchId) qs.set('batch_id', batchId);
        if (range.from) qs.set('date_from', range.from);
        if (range.to) qs.set('date_to', range.to);
        return qs;
    }, [qDebounced, stage, operatorId, batchId, range.from, range.to, sort]);

    const load = useCallback(async (offset = 0) => {
        const seq = ++requestSeq.current;
        if (offset === 0) setLoading(true); else setLoadingMore(true);
        setError('');
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/leads?${buildQuery(offset).toString()}`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            if (seq !== requestSeq.current) return;
            const list = Array.isArray(data.items) ? data.items : [];
            setItems((cur) => (offset === 0 ? list : [...cur, ...list]));
            setTotal(Number(data.total) || 0);
            setByStage(data.by_stage || {});
        } catch (e) {
            if (seq !== requestSeq.current) return;
            setError(e.message || 'Не удалось загрузить журнал');
        } finally {
            if (seq === requestSeq.current) { setLoading(false); setLoadingMore(false); }
        }
    }, [apiBaseUrl, authHeaders, departmentId, buildQuery]);

    useEffect(() => { load(0); }, [load]);

    const loadCard = useCallback(async (leadId) => {
        setCardLoading(true);
        setCardError('');
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/leads/${leadId}`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            setCard(data.lead || null);
        } catch (e) {
            setCardError(e.message || 'Не удалось открыть карточку');
        } finally {
            setCardLoading(false);
        }
    }, [apiBaseUrl, authHeaders]);

    const openLead = (lead) => {
        setOpenId(lead.id);
        setCard(null);
        setRecordings({});
        loadCard(lead.id);
    };

    const closeCard = () => { setOpenId(null); setCard(null); };

    const applyLead = (lead) => {
        setCard(lead);
        setItems((cur) => cur.map((it) => (it.id === lead.id ? { ...it, ...lead, attempts: undefined, events: undefined } : it)));
        load(0);
        onChanged?.();
    };

    const act = async (path, method, body, okMessage) => {
        if (!openId) return;
        setBusy(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/leads/${openId}/${path}`, {
                method, credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(body || {}),
            });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            if (data.lead) applyLead(data.lead);
            if (okMessage) toast(okMessage, 'success');
        } catch (e) {
            toast(e.message || 'Не получилось', 'error');
        } finally {
            setBusy(false);
        }
    };

    const fetchRecording = async (attemptId) => {
        setRecordings((cur) => ({ ...cur, [attemptId]: { loading: true } }));
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/attempts/${attemptId}/recording`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            setRecordings((cur) => ({ ...cur, [attemptId]: { url: data.url } }));
        } catch (e) {
            setRecordings((cur) => ({ ...cur, [attemptId]: { error: e.message || 'Запись недоступна' } }));
        }
    };

    const stageOptions = useMemo(() => {
        const all = Object.values(byStage).reduce((s, n) => s + (Number(n) || 0), 0);
        const mk = (value, label) => ({ value, label, count: value ? Number(byStage[value]) || 0 : all });
        return [
            mk('', 'Все'), mk('queue', 'В очереди'), mk('waiting', 'Ждут повтора'), mk('issued', 'У операторов'),
            mk('answered', 'Дозвонились'), mk('exhausted', 'Не дозвонились'), mk('excluded', 'Исключены'),
        ];
    }, [byStage]);

    const operatorOptions = useMemo(() => [
        { value: '', label: 'Все операторы' },
        ...users.map((u) => ({ value: String(u.id), label: u.name || u.login || `#${u.id}` })),
    ], [users]);

    const batchOptions = useMemo(() => [
        { value: '', label: 'Все загрузки' },
        ...batches.map((b) => ({ value: String(b.id), label: `${b.file_name || 'файл'} · ${fmtDate(b.created_at)}` })),
    ], [batches]);

    const sortOptions = [
        { value: 'activity', label: 'Сначала свежие' },
        { value: 'name', label: 'По ФИО' },
        { value: 'created', label: 'По дате загрузки' },
        { value: 'attempts', label: 'Больше попыток' },
    ];

    const hasFilters = Boolean(qDebounced || stage || operatorId || batchId || range.from || range.to);
    const resetFilters = () => { setQ(''); setStage(''); setOperatorId(''); setBatchId(''); setRange({ from: '', to: '' }); };

    return (
        <section className="space-y-3">
            <div className={`${iosCard} space-y-3 p-3`}>
                <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
                    <div className="relative flex-1">
                        <FaIcon className="fas fa-magnifying-glass pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" style={{ width: 13, height: 13 }} />
                        <input
                            value={q}
                            onChange={(e) => setQ(e.target.value)}
                            placeholder="ФИО или последние цифры номера"
                            className={`${iosInput} pl-9`}
                            aria-label="Поиск по журналу"
                        />
                    </div>
                    <CustomSelect value={operatorId} onChange={setOperatorId} options={operatorOptions} className="lg:w-52" ariaLabel="Оператор" searchable={users.length > 8} />
                    <CustomSelect value={batchId} onChange={setBatchId} options={batchOptions} className="lg:w-56" ariaLabel="Файл загрузки" />
                    <IosDateRangePicker
                        from={range.from}
                        to={range.to}
                        onChange={(next) => setRange({ from: next?.from || '', to: next?.to || '' })}
                    />
                    <CustomSelect value={sort} onChange={setSort} options={sortOptions} className="lg:w-44" ariaLabel="Сортировка" />
                    <button type="button" onClick={() => load(0)} disabled={loading} className={`${iosBtnSecondary} py-2.5`} aria-label="Обновить">
                        <FaIcon className={loading ? 'fas fa-spinner fa-spin' : 'fas fa-rotate'} />
                    </button>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <div className="min-w-0 flex-1 overflow-x-auto">
                        <IosSegmented value={stage} onChange={setStage} options={stageOptions} size="sm" ariaLabel="Этап" />
                    </div>
                    {hasFilters && (
                        <button type="button" onClick={resetFilters} className={`${iosBtnGhost} shrink-0`}>
                            <FaIcon className="fas fa-filter-circle-xmark" style={{ width: 12, height: 12 }} /> Сбросить
                        </button>
                    )}
                </div>
            </div>

            <div className={`${iosCard} overflow-hidden`}>
                <div className="hidden items-center gap-3 border-b border-slate-100 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 md:flex">
                    <div className="w-9" />
                    <div className="flex-1">Водитель</div>
                    <div className="w-44">Ответственный</div>
                    <div className="w-44 text-right">Этап · последний звонок</div>
                    <div className="w-3" />
                </div>
                {error ? (
                    <div className="px-4 py-6 text-[13px] text-rose-600">{error}</div>
                ) : loading && items.length === 0 ? (
                    <div className="px-4 py-8 text-center text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                ) : items.length === 0 ? (
                    <div className="px-4 py-10 text-center">
                        <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                            <FaIcon className="fas fa-address-book" style={{ width: 18, height: 18 }} />
                        </div>
                        <div className="mt-3 text-[14px] font-semibold text-slate-800">{hasFilters ? 'Никого не нашли' : 'Журнал пуст'}</div>
                        <div className="mt-1 text-[12.5px] text-slate-500">
                            {hasFilters ? 'Попробуйте снять часть фильтров.' : 'Загрузите список водителей во вкладке «База водителей» — они появятся здесь.'}
                        </div>
                    </div>
                ) : (
                    <>
                        <div className="divide-y divide-slate-100">
                            {items.map((lead) => <LeadRow key={lead.id} lead={lead} onOpen={openLead} />)}
                        </div>
                        <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-2.5 text-[12px] text-slate-500">
                            <span>Показано {items.length} из {total}</span>
                            {items.length < total && (
                                <button type="button" onClick={() => load(items.length)} disabled={loadingMore} className={`${iosBtnSecondary} py-1.5 text-[12.5px]`}>
                                    {loadingMore ? <FaIcon className="fas fa-spinner fa-spin" /> : <FaIcon className="fas fa-arrow-down" style={{ width: 11, height: 11 }} />}
                                    Показать ещё
                                </button>
                            )}
                        </div>
                    </>
                )}
            </div>

            <IosModal
                open={Boolean(openId)}
                onClose={closeCard}
                title={card?.full_name || (cardLoading ? 'Загрузка…' : 'Водитель')}
                subtitle={card ? (
                    <span className="inline-flex flex-wrap items-center gap-2">
                        <span className="font-mono tabular-nums">{card.phone_masked}</span>
                        <StageBadge lead={card} />
                    </span>
                ) : undefined}
                maxWidth="max-w-2xl"
            >
                <LeadCard
                    lead={card}
                    loading={cardLoading}
                    error={cardError}
                    canEdit={canEdit}
                    busy={busy}
                    onRequeue={(note) => act('requeue', 'POST', { note }, 'Водитель возвращён в список')}
                    onExclude={(note) => act('exclude', 'POST', { note }, 'Водитель исключён из обзвона')}
                    onSaveNote={(note) => act('note', 'PUT', { note }, 'Заметка сохранена')}
                    onRecording={fetchRecording}
                    recordings={recordings}
                />
            </IosModal>
        </section>
    );
};

export default DialListJournal;
