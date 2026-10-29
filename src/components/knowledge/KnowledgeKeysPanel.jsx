import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import {
    IosBadge, IosHint, IosModal, IosPager, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput,
} from '../ui/ios';

/**
 * Вкладка «Ключи» раздела «Сессии»: ключи партнёрских сервисов и журнал их вызовов.
 *
 * Ключ открывает стороннему ИИ (Syntony) фрагменты базы знаний в правах оператора
 * СЗоВ — решение владельца 29.10.2026: «их ИИ — тот же оператор техподдержки».
 * Показывается ключ один раз при выпуске; дальше по нему видно только вызовы:
 * когда, про какой парк, что спросили и что отдали. Отозванный ключ остаётся в
 * списке с пометкой — его журнал ещё нужен.
 *
 * Только супер-админу: вкладку показывает SessionsPanel (App.jsx), ручки
 * /api/admin/knowledge-keys отвечают остальным 403.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const OUTCOMES = {
    ok: { label: 'Ответ', tone: 'emerald' },
    no_data: { label: 'Нет данных', tone: 'amber' },
    bad_request: { label: 'Плохой запрос', tone: 'slate' },
    park_unavailable: { label: 'Парк закрыт', tone: 'slate' },
    limit: { label: 'Предел', tone: 'rose' },
    error: { label: 'Ошибка', tone: 'rose' },
};

const KINDS = {
    article: 'статья', park: 'парк', office: 'офис', offices: 'офисы', city: 'город',
    cities: 'города', classifier: 'классификатор',
};

const MATCH = { named: 'парк назван', general: 'общее', all_parks: 'все парки' };

const PAGE = 25;

const plural = (n, one, few, many) => {
    const abs = Math.abs(n) % 100;
    const last = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (last > 1 && last < 5) return few;
    if (last === 1) return one;
    return many;
};

const callWord = (n) => plural(n, 'вызов', 'вызова', 'вызовов');

function roleLabel(acts) {
    const role = acts?.role === 'trainee' ? 'стажёр' : 'оператор';
    const department = acts?.department_code ? acts.department_code.toUpperCase() : (acts?.department || '');
    return department ? `${role} · ${department === 'SZOV' ? 'СЗоВ' : department}` : role;
}

function CopyButton({ value }) {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(String(value || ''));
            setCopied(true);
            setTimeout(() => setCopied(false), 1600);
        } catch {
            /* без буфера обмена остаётся выделить руками */
        }
    };
    return (
        <button type="button" onClick={copy} className={iosBtnSecondary}>
            {copied ? 'Скопировано' : 'Скопировать'}
        </button>
    );
}

function KeyCard({ item, formatDate, onCalls, onRevoke }) {
    const calls = Number(item.calls_count || 0);
    return (
        <div className={`${iosCard} px-4 py-3.5`}>
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-[15px] font-semibold text-slate-900">{item.name}</span>
                        {!item.active && <IosBadge tone="slate">Отозван</IosBadge>}
                    </div>
                    <p className="mt-0.5 text-[12.5px] text-slate-500">
                        Права: {roleLabel(item.acts_as)} · отпечаток <span className="font-mono">{item.mark}</span>
                    </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    <button type="button" onClick={() => onCalls(item)} className={iosBtnGhost}>
                        Вызовы
                    </button>
                    {item.active && (
                        <button type="button" onClick={() => onRevoke(item)} className={`${iosBtnGhost} !text-rose-600 hover:!bg-rose-50`}>
                            Отозвать
                        </button>
                    )}
                </div>
            </div>
            <p className="mt-2 text-[12.5px] tabular-nums text-slate-500">
                {calls} {callWord(calls)}
                {item.calls_last_day > 0 ? ` · ${item.calls_last_day} за сутки` : ''}
                {item.last_used_at ? ` · последний ${formatDate(item.last_used_at)}` : ' · ещё не использовался'}
            </p>
            <p className="mt-0.5 text-[12px] text-slate-400">
                Выпущен {formatDate(item.created_at)}{item.created_by_name ? ` · ${item.created_by_name}` : ''}
                {item.revoked_at ? ` · отозван ${formatDate(item.revoked_at)}${item.revoked_by_name ? ` · ${item.revoked_by_name}` : ''}` : ''}
            </p>
        </div>
    );
}

function CallRow({ call, formatDate }) {
    const [open, setOpen] = useState(false);
    const outcome = OUTCOMES[call.outcome] || { label: call.outcome, tone: 'slate' };
    const fragments = Array.isArray(call.fragments) ? call.fragments : [];
    return (
        <li className="border-b border-slate-100 last:border-b-0">
            <button
                type="button"
                onClick={() => setOpen((value) => !value)}
                aria-expanded={open}
                className="block w-full px-4 py-3 text-left transition hover:bg-slate-50"
            >
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[12px] tabular-nums text-slate-400">{formatDate(call.created_at)}</span>
                    {call.park_name && <span className="text-[12.5px] font-medium text-slate-700">{call.park_name}</span>}
                    <IosBadge tone={outcome.tone}>{outcome.label}</IosBadge>
                    <span className="ml-auto text-[12px] tabular-nums text-slate-400">
                        {call.fragments_count > 0 ? `${call.fragments_count} фр.` : ''}
                        {call.latency_ms != null ? ` · ${call.latency_ms} мс` : ''}
                    </span>
                </div>
                <p className={`mt-1 break-words text-[13.5px] leading-snug text-slate-800 ${open ? '' : 'line-clamp-2'}`}>
                    {call.question || <span className="text-slate-400">без текста</span>}
                </p>
            </button>
            {open && (
                <div className="px-4 pb-3 text-[12.5px] text-slate-500">
                    {call.error_text && <p className="mb-1 text-rose-600">{call.error_text}</p>}
                    {call.ip_address && <p className="mb-1">IP {call.ip_address}{call.degraded ? ' · поиск без векторов' : ''}</p>}
                    {fragments.length > 0 ? (
                        <ol className="space-y-1">
                            {fragments.map((fragment) => (
                                <li key={`${call.id}-${fragment.rank}`} className="flex flex-wrap items-baseline gap-x-2">
                                    <span className="tabular-nums text-slate-400">{fragment.rank}.</span>
                                    <span className="text-slate-700">{fragment.title || '—'}</span>
                                    {fragment.heading && <span className="text-slate-400">› {fragment.heading}</span>}
                                    <span className="text-slate-400">
                                        {KINDS[fragment.kind] || fragment.kind}
                                        {fragment.park_match && fragment.park_match !== 'general' ? ` · ${MATCH[fragment.park_match] || fragment.park_match}` : ''}
                                        {fragment.similarity != null ? ` · ${Math.round(fragment.similarity * 100)}%` : ''}
                                    </span>
                                </li>
                            ))}
                        </ol>
                    ) : (
                        call.outcome === 'no_data' && <p>Фрагментов не нашлось — вопрос ушёл в «Вопросы операторов».</p>
                    )}
                </div>
            )}
        </li>
    );
}

function CallsModal({ item, base, headers, formatDate, onClose }) {
    const [page, setPage] = useState(1);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!item) return undefined;
        let alive = true;
        setLoading(true);
        setError('');
        axios.get(`${base}/api/admin/knowledge-keys/${item.id}/calls`, {
            headers, params: { limit: PAGE, offset: (page - 1) * PAGE },
        }).then((response) => {
            if (alive) setData(response.data || {});
        }).catch((e) => {
            if (alive) setError(errText(e, 'Журнал не загрузился'));
        }).finally(() => {
            if (alive) setLoading(false);
        });
        return () => { alive = false; };
    }, [item, page, base, headers]);

    const total = Number(data?.pagination?.total || 0);
    const calls = Array.isArray(data?.calls) ? data.calls : [];
    const pageCount = Math.max(1, Math.ceil(total / PAGE));
    const from = total === 0 ? 0 : (page - 1) * PAGE + 1;
    const to = Math.min(total, (page - 1) * PAGE + calls.length);

    return (
        <IosModal
            open={!!item}
            onClose={onClose}
            title={item ? `Вызовы · ${item.name}` : ''}
            subtitle={total > 0 ? `${total} ${callWord(total)}` : (loading ? 'Загрузка…' : 'Пока ни одного вызова')}
            maxWidth="max-w-3xl"
            bodyClassName="p-0"
        >
            {error && <p className="px-4 py-3 text-[13px] text-rose-600">{error}</p>}
            {calls.length > 0 && (
                <ul>
                    {calls.map((call) => <CallRow key={call.id} call={call} formatDate={formatDate} />)}
                </ul>
            )}
            {pageCount > 1 && (
                <div className="border-t border-slate-100 px-4 py-2">
                    <IosPager page={page} pageCount={pageCount} total={total} from={from} to={to} onPage={setPage} unit="вызовы" />
                </div>
            )}
        </IosModal>
    );
}

export default function KnowledgeKeysPanel({ base, headers, formatDate }) {
    const [keys, setKeys] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [creating, setCreating] = useState(false);
    const [name, setName] = useState('');
    const [busy, setBusy] = useState(false);
    const [issued, setIssued] = useState(null);
    const [journalKey, setJournalKey] = useState(null);
    const [revoking, setRevoking] = useState(null);

    const toast = (text, type = 'success') => {
        if (typeof window !== 'undefined' && typeof window.showToast === 'function') window.showToast(text, type);
    };

    const load = useCallback(async () => {
        setError('');
        try {
            const response = await axios.get(`${base}/api/admin/knowledge-keys`, { headers });
            setKeys(Array.isArray(response.data?.keys) ? response.data.keys : []);
        } catch (e) {
            setError(errText(e, 'Список ключей не загрузился'));
        } finally {
            setLoading(false);
        }
    }, [base, headers]);

    useEffect(() => { load(); }, [load]);

    const create = async (event) => {
        event?.preventDefault?.();
        const value = name.trim();
        if (!value || busy) return;
        setBusy(true);
        try {
            const response = await axios.post(`${base}/api/admin/knowledge-keys`, { name: value }, { headers });
            setIssued({ secret: response.data?.secret, header: response.data?.header, key: response.data?.key });
            setCreating(false);
            setName('');
            load();
        } catch (e) {
            toast(errText(e, 'Ключ не выпущен'), 'error');
        } finally {
            setBusy(false);
        }
    };

    const revoke = async () => {
        const item = revoking;
        if (!item || busy) return;
        setBusy(true);
        try {
            await axios.post(`${base}/api/admin/knowledge-keys/${item.id}/revoke`, {}, { headers });
            toast(`Ключ «${item.name}» отозван`);
            setRevoking(null);
            load();
        } catch (e) {
            toast(errText(e, 'Ключ не отозван'), 'error');
        } finally {
            setBusy(false);
        }
    };

    const active = keys.filter((item) => item.active);
    const revoked = keys.filter((item) => !item.active);

    return (
        <div className="space-y-4">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h2 className="flex items-center gap-1.5 text-xl font-semibold tracking-tight text-gray-900">
                        Ключи партнёров
                        <IosHint text="Ключ даёт стороннему сервису фрагменты базы знаний в правах оператора СЗоВ: парк + вопрос → фрагменты статей и справочников. Каждый вызов записывается. Сам ключ показывается один раз при выпуске." />
                    </h2>
                    <p className="mt-0.5 text-sm text-gray-400 tabular-nums">
                        {active.length} {plural(active.length, 'действующий', 'действующих', 'действующих')}
                        {revoked.length > 0 ? ` · ${revoked.length} ${plural(revoked.length, 'отозван', 'отозвано', 'отозвано')}` : ''}
                    </p>
                </div>
                <button type="button" onClick={() => setCreating(true)} className={`${iosBtnPrimary} shrink-0`}>
                    Создать ключ
                </button>
            </div>

            {error && <p className="text-[13px] text-rose-600">{error}</p>}
            {!loading && !error && keys.length === 0 && (
                <div className={`${iosCard} px-4 py-6 text-center text-[13.5px] text-slate-500`}>
                    Ключей пока нет. Создайте ключ и передайте его партнёру — он присылает парк и вопрос
                    в POST /api/knowledge/search с заголовком X-Api-Key.
                </div>
            )}

            {keys.length > 0 && (
                <div className="space-y-2.5">
                    {active.map((item) => (
                        <KeyCard key={item.id} item={item} formatDate={formatDate} onCalls={setJournalKey} onRevoke={setRevoking} />
                    ))}
                    {revoked.map((item) => (
                        <KeyCard key={item.id} item={item} formatDate={formatDate} onCalls={setJournalKey} onRevoke={setRevoking} />
                    ))}
                </div>
            )}

            <IosModal
                open={creating}
                onClose={() => { if (!busy) setCreating(false); }}
                title="Новый ключ"
                subtitle="Права — как у оператора СЗоВ"
                footer={(
                    <div className="flex justify-end gap-2">
                        <button type="button" onClick={() => setCreating(false)} className={iosBtnSecondary} disabled={busy}>Отмена</button>
                        <button type="button" onClick={create} className={iosBtnPrimary} disabled={busy || !name.trim()}>Выпустить</button>
                    </div>
                )}
            >
                <form onSubmit={create}>
                    <label className="block text-[12px] font-medium text-slate-500" htmlFor="knowledge-key-name">
                        Кому выдан
                    </label>
                    <input
                        id="knowledge-key-name"
                        className={`${iosInput} mt-1`}
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                        placeholder="Например, Syntony"
                        maxLength={120}
                        autoFocus
                    />
                </form>
            </IosModal>

            <IosModal
                open={!!issued}
                onClose={() => setIssued(null)}
                title="Ключ выпущен"
                subtitle={issued?.key?.name || ''}
                footer={(
                    <div className="flex justify-end gap-2">
                        <CopyButton value={issued?.secret} />
                        <button type="button" onClick={() => setIssued(null)} className={iosBtnPrimary}>Готово</button>
                    </div>
                )}
            >
                <p className="text-[13.5px] text-slate-600">
                    Показывается один раз — скопируйте и передайте партнёру. Потом его не восстановить, только выпустить новый.
                </p>
                <pre className="mt-3 select-all overflow-x-auto rounded-xl bg-slate-100 px-3.5 py-3 font-mono text-[13px] text-slate-900">
                    {issued?.secret}
                </pre>
                <p className="mt-3 text-[12.5px] text-slate-500">
                    Партнёр передаёт его в заголовке <span className="font-mono">{issued?.header || 'X-Api-Key'}</span>; инструкция — docs/SYNTONY_KNOWLEDGE_API.md.
                </p>
            </IosModal>

            <IosModal
                open={!!revoking}
                onClose={() => { if (!busy) setRevoking(null); }}
                title="Отозвать ключ?"
                subtitle={revoking?.name || ''}
                footer={(
                    <div className="flex justify-end gap-2">
                        <button type="button" onClick={() => setRevoking(null)} className={iosBtnSecondary} disabled={busy}>Отмена</button>
                        <button type="button" onClick={revoke} className={`${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700`} disabled={busy}>Отозвать</button>
                    </div>
                )}
            >
                <p className="text-[13.5px] text-slate-600">
                    Партнёр перестанет получать ответы сразу. Журнал вызовов останется.
                </p>
            </IosModal>

            <CallsModal item={journalKey} base={base} headers={headers} formatDate={formatDate} onClose={() => setJournalKey(null)} />
        </div>
    );
}
