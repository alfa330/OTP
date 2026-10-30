import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import {
    IosBadge, IosHint, IosModal, IosPager, IosToggle, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput,
} from '../ui/ios';

/**
 * Вкладка «Ключи» раздела «Сессии»: ключи партнёрских сервисов, к чему у них доступ
 * и журнал вызовов.
 *
 * Ключ открывает стороннему сервису ручки портала — ровно те области, что отмечены
 * при выпуске (их можно поменять позже): ручку знаний для ИИ Syntony, внешний вход
 * «Ссылок на подписание», окно «Чатов ОП» для CRM. Показывается ключ один раз при
 * выпуске; дальше по нему видно только вызовы: когда, какая область, что спросили
 * и чем кончилось. Отозванный ключ остаётся в списке с пометкой — его журнал ещё
 * нужен.
 *
 * Только супер-админу: вкладку показывает SessionsPanel (App.jsx), ручки
 * /api/admin/partner-keys отвечают остальным 403.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const OUTCOMES = {
    ok: { label: 'Выполнен', tone: 'emerald' },
    no_data: { label: 'Нет данных', tone: 'amber' },
    no_link: { label: 'Без учётки', tone: 'amber' },
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

/* Подписи подробностей вызова по областям. Чего нет в словаре — показывается как есть. */
const DETAIL_LABELS = {
    park_name: 'Парк', question_id: 'Вопрос операторам №', fragments_count: 'Фрагментов',
    degraded: 'Поиск без векторов', error_text: 'Ошибка',
    iins: 'ИИН', links: 'Ссылок', not_found: 'Без документов', invalid: 'Не ИИН', errors: 'Без ответа',
    stopped: 'Остановлена',
    crm_user_id: 'Пользователь CRM', crm_user_name: 'Имя в CRM', matched_by: 'Найдено по',
    linked: 'Учётка найдена', icore_user_id: 'Учётка iCORE',
};
const DETAIL_HIDDEN = new Set(['park_id', 'fragments']);

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

const hostOf = (url) => {
    try {
        return new URL(url).host;
    } catch {
        return String(url || '');
    }
};

const detailValue = (value) => {
    if (value === true) return 'да';
    if (value === false) return 'нет';
    if (value == null || value === '') return '—';
    return String(value);
};

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

/* Области доступа списком: название, «i» с тем, что область открывает, и тумблер. */
function ScopePicker({ catalog, value, onChange, disabled }) {
    const chosen = new Set(value);
    const toggle = (code, on) => {
        const next = new Set(chosen);
        if (on) next.add(code); else next.delete(code);
        onChange(catalog.map((scope) => scope.code).filter((code) => next.has(code)));
    };
    return (
        <ul className="divide-y divide-slate-100 rounded-xl bg-slate-50">
            {catalog.map((scope) => (
                <li key={scope.code} className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                    <span className="flex min-w-0 items-center gap-1.5 text-[13.5px] text-slate-800">
                        <span className="truncate">{scope.title}</span>
                        <IosHint text={`${scope.hint} ${scope.endpoints.join(', ')}. Заголовок: ${scope.header}.`} />
                    </span>
                    <IosToggle checked={chosen.has(scope.code)} disabled={disabled} onChange={(on) => toggle(scope.code, on)} />
                </li>
            ))}
        </ul>
    );
}

function KeyCard({ item, formatDate, onCalls, onScopes, onRevoke }) {
    const calls = Number(item.calls_count || 0);
    const titles = Array.isArray(item.scope_titles) ? item.scope_titles : [];
    return (
        <div className={`${iosCard} px-4 py-3.5`}>
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-[15px] font-semibold text-slate-900">{item.name}</span>
                        {!item.active && <IosBadge tone="slate">Отозван</IosBadge>}
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        {titles.length > 0
                            ? titles.map((title) => <IosBadge key={title} tone={item.active ? 'blue' : 'slate'}>{title}</IosBadge>)
                            : <span className="text-[12.5px] text-rose-600">Доступ не выдан</span>}
                    </div>
                    <p className="mt-1.5 text-[12.5px] text-slate-500">
                        Отпечаток <span className="font-mono">{item.mark}</span>
                    </p>
                    {/* Подписки на события: куда партнёр получает вебхуки. Красным — только
                        сбои подряд: исправная доставка внимания не просит. */}
                    {Array.isArray(item.webhooks) && item.webhooks.map((hook) => (
                        <p key={hook.scope} className="mt-0.5 text-[12.5px] text-slate-500">
                            Вебхук · {hook.scope_title || hook.scope}: <span className="font-mono">{hostOf(hook.url)}</span>
                            {!hook.enabled && ' · выключен'}
                            {hook.failures_in_row > 0 && (
                                <span className="text-rose-600"> · сбоев подряд {hook.failures_in_row}</span>
                            )}
                        </p>
                    ))}
                </div>
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                    {item.active && (
                        <button type="button" onClick={() => onScopes(item)} className={iosBtnGhost}>
                            Доступ
                        </button>
                    )}
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

function CallDetails({ call }) {
    const details = call.details && typeof call.details === 'object' ? call.details : {};
    const fragments = Array.isArray(details.fragments) ? details.fragments : [];
    const lines = Object.entries(details).filter(([key, value]) => !DETAIL_HIDDEN.has(key) && value != null && value !== '');
    return (
        <div className="px-4 pb-3 text-[12.5px] text-slate-500">
            <p className="mb-1">
                {call.endpoint}{call.ip_address ? ` · IP ${call.ip_address}` : ''}
            </p>
            {lines.length > 0 && (
                <dl className="mb-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                    {lines.map(([key, value]) => (
                        <React.Fragment key={key}>
                            <dt className="text-slate-400">{DETAIL_LABELS[key] || key}</dt>
                            <dd className="text-slate-700">{detailValue(value)}</dd>
                        </React.Fragment>
                    ))}
                </dl>
            )}
            {fragments.length > 0 && (
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
            )}
            {call.outcome === 'no_data' && fragments.length === 0 && (
                <p>Фрагментов не нашлось — вопрос ушёл в «Вопросы операторов».</p>
            )}
        </div>
    );
}

function CallRow({ call, scopeTitle, formatDate }) {
    const [open, setOpen] = useState(false);
    const outcome = OUTCOMES[call.outcome] || { label: call.outcome, tone: 'slate' };
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
                    {scopeTitle && <span className="text-[12.5px] font-medium text-slate-700">{scopeTitle}</span>}
                    <IosBadge tone={outcome.tone}>{outcome.label}</IosBadge>
                    <span className="ml-auto text-[12px] tabular-nums text-slate-400">
                        {call.latency_ms != null ? `${call.latency_ms} мс` : ''}
                    </span>
                </div>
                <p className={`mt-1 break-words text-[13.5px] leading-snug text-slate-800 ${open ? '' : 'line-clamp-2'}`}>
                    {call.summary || <span className="text-slate-400">без сводки</span>}
                </p>
            </button>
            {open && <CallDetails call={call} />}
        </li>
    );
}

function CallsModal({ item, base, headers, scopeTitles, formatDate, onClose }) {
    const [page, setPage] = useState(1);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    useEffect(() => { setPage(1); setData(null); }, [item?.id]);

    useEffect(() => {
        if (!item) return undefined;
        let alive = true;
        setLoading(true);
        setError('');
        axios.get(`${base}/api/admin/partner-keys/${item.id}/calls`, {
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
            bodyClassName="thin-scroll flex-1 overflow-y-auto overflow-x-hidden overscroll-contain p-0"
        >
            {error && <p className="px-4 py-3 text-[13px] text-rose-600">{error}</p>}
            {calls.length > 0 && (
                <ul>
                    {calls.map((call) => (
                        <CallRow key={call.id} call={call} scopeTitle={scopeTitles[call.scope]} formatDate={formatDate} />
                    ))}
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

export default function PartnerKeysPanel({ base, headers, formatDate }) {
    const [keys, setKeys] = useState([]);
    const [catalog, setCatalog] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [creating, setCreating] = useState(false);
    const [name, setName] = useState('');
    const [newScopes, setNewScopes] = useState([]);
    const [editing, setEditing] = useState(null);      // { key, scopes }
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
            const response = await axios.get(`${base}/api/admin/partner-keys`, { headers });
            setKeys(Array.isArray(response.data?.keys) ? response.data.keys : []);
            setCatalog(Array.isArray(response.data?.scopes) ? response.data.scopes : []);
        } catch (e) {
            setError(errText(e, 'Список ключей не загрузился'));
        } finally {
            setLoading(false);
        }
    }, [base, headers]);

    useEffect(() => { load(); }, [load]);

    const scopeTitles = Object.fromEntries(catalog.map((scope) => [scope.code, scope.title]));

    const openCreate = () => {
        setName('');
        setNewScopes([]);
        setCreating(true);
    };

    const create = async (event) => {
        event?.preventDefault?.();
        const value = name.trim();
        if (!value || newScopes.length === 0 || busy) return;
        setBusy(true);
        try {
            const response = await axios.post(`${base}/api/admin/partner-keys`, { name: value, scopes: newScopes }, { headers });
            setIssued({ secret: response.data?.secret, header: response.data?.header, key: response.data?.key });
            setCreating(false);
            load();
        } catch (e) {
            toast(errText(e, 'Ключ не выпущен'), 'error');
        } finally {
            setBusy(false);
        }
    };

    const saveScopes = async () => {
        if (!editing || editing.scopes.length === 0 || busy) return;
        setBusy(true);
        try {
            await axios.patch(`${base}/api/admin/partner-keys/${editing.key.id}`, { scopes: editing.scopes }, { headers });
            toast(`Доступ ключа «${editing.key.name}» обновлён`);
            setEditing(null);
            load();
        } catch (e) {
            toast(errText(e, 'Доступ не сохранён'), 'error');
        } finally {
            setBusy(false);
        }
    };

    const revoke = async () => {
        const item = revoking;
        if (!item || busy) return;
        setBusy(true);
        try {
            await axios.post(`${base}/api/admin/partner-keys/${item.id}/revoke`, {}, { headers });
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
                        <IosHint text="Ключ открывает стороннему сервису ручки портала — только те области, что отмечены. Каждый вызов записывается. Сам ключ показывается один раз при выпуске." />
                    </h2>
                    <p className="mt-0.5 text-sm text-gray-400 tabular-nums">
                        {active.length} {plural(active.length, 'действующий', 'действующих', 'действующих')}
                        {revoked.length > 0 ? ` · ${revoked.length} ${plural(revoked.length, 'отозван', 'отозвано', 'отозвано')}` : ''}
                    </p>
                </div>
                <button type="button" onClick={openCreate} className={`${iosBtnPrimary} shrink-0`}>
                    Создать ключ
                </button>
            </div>

            {error && <p className="text-[13px] text-rose-600">{error}</p>}
            {!loading && !error && keys.length === 0 && (
                <div className={`${iosCard} px-4 py-6 text-center text-[13.5px] text-slate-500`}>
                    Ключей пока нет. Создайте ключ, отметьте, к чему у него доступ, и передайте партнёру —
                    он присылает его в заголовке X-Api-Key.
                </div>
            )}

            {keys.length > 0 && (
                <div className="space-y-2.5">
                    {active.concat(revoked).map((item) => (
                        <KeyCard
                            key={item.id}
                            item={item}
                            formatDate={formatDate}
                            onCalls={setJournalKey}
                            onScopes={(key) => setEditing({ key, scopes: Array.isArray(key.scopes) ? key.scopes : [] })}
                            onRevoke={setRevoking}
                        />
                    ))}
                </div>
            )}

            <IosModal
                open={creating}
                onClose={() => { if (!busy) setCreating(false); }}
                title="Новый ключ"
                subtitle="Кому выдан и к чему открыт доступ"
                footer={(
                    <div className="flex justify-end gap-2">
                        <button type="button" onClick={() => setCreating(false)} className={iosBtnSecondary} disabled={busy}>Отмена</button>
                        <button type="button" onClick={create} className={iosBtnPrimary} disabled={busy || !name.trim() || newScopes.length === 0}>Выпустить</button>
                    </div>
                )}
            >
                <form onSubmit={create} className="space-y-4">
                    <div>
                        <label className="block text-[12px] font-medium text-slate-500" htmlFor="partner-key-name">
                            Кому выдан
                        </label>
                        <input
                            id="partner-key-name"
                            className={`${iosInput} mt-1`}
                            value={name}
                            onChange={(event) => setName(event.target.value)}
                            placeholder="Например, Syntony или CRM «Потока»"
                            maxLength={120}
                            autoFocus
                        />
                    </div>
                    <div>
                        <p className="mb-1 text-[12px] font-medium text-slate-500">Доступ</p>
                        <ScopePicker catalog={catalog} value={newScopes} onChange={setNewScopes} disabled={busy} />
                    </div>
                </form>
            </IosModal>

            <IosModal
                open={!!editing}
                onClose={() => { if (!busy) setEditing(null); }}
                title="Доступ ключа"
                subtitle={editing?.key?.name || ''}
                footer={(
                    <div className="flex justify-end gap-2">
                        <button type="button" onClick={() => setEditing(null)} className={iosBtnSecondary} disabled={busy}>Отмена</button>
                        <button type="button" onClick={saveScopes} className={iosBtnPrimary} disabled={busy || !editing || editing.scopes.length === 0}>Сохранить</button>
                    </div>
                )}
            >
                {editing && (
                    <ScopePicker
                        catalog={catalog}
                        value={editing.scopes}
                        onChange={(scopes) => setEditing({ ...editing, scopes })}
                        disabled={busy}
                    />
                )}
                <p className="mt-3 text-[12.5px] text-slate-500">
                    Снятая область закрывается сразу: партнёр получит 403 на эту ручку. Чтобы закрыть всё — отзовите ключ.
                </p>
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
                    Доступ: {(issued?.key?.scope_titles || []).join(', ') || '—'}. Партнёр передаёт ключ в заголовке{' '}
                    <span className="font-mono">{issued?.header || 'X-Api-Key'}</span>.
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
                    Партнёр перестанет получать ответы сразу, по всем областям. Журнал вызовов останется.
                </p>
            </IosModal>

            <CallsModal item={journalKey} base={base} headers={headers} scopeTitles={scopeTitles} formatDate={formatDate} onClose={() => setJournalKey(null)} />
        </div>
    );
}
