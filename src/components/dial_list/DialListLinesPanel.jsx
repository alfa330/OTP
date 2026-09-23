import React, { useCallback, useEffect, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';

/*
 * Линии Binotel отдела: какие внутренние номера есть у компании, кто из них
 * онлайн, кто из сотрудников iCORE на какой линии сидит, и кнопка «Назначить».
 *
 * Назначение — одна кнопка: сервер сам связывает линию с сотрудником, дальше тот
 * входит в iCORE Phone логином iCORE и регистрируется на этой линии.
 */

const readError = async (resp) => {
    const data = await resp.json().catch(() => ({}));
    return data?.error || `HTTP ${resp.status}`;
};

const fmtAgo = (unix) => {
    if (!unix) return '';
    const sec = Math.max(0, Math.floor(Date.now() / 1000) - Number(unix));
    if (sec < 90) return 'только что';
    if (sec < 3600) return `${Math.floor(sec / 60)} мин назад`;
    if (sec < 86400) return `${Math.floor(sec / 3600)} ч назад`;
    return `${Math.floor(sec / 86400)} дн назад`;
};

const DialListLinesPanel = ({ apiBaseUrl, authHeaders, departmentId, canEdit = true, showToast }) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [picking, setPicking] = useState(null);   // номер линии, для которой открыт выбор
    const [pickedUser, setPickedUser] = useState('');
    const [busyLine, setBusyLine] = useState(null);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/lines`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            setData(await resp.json());
        } catch (e) {
            setError(e.message || 'Не удалось загрузить линии');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, departmentId]);

    useEffect(() => { load(); }, [load]);

    const post = async (path, body) => {
        const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/lines/${path}`, {
            method: 'POST',
            credentials: 'include',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error(await readError(resp));
        return resp.json();
    };

    const assign = async (line) => {
        if (!pickedUser) return;
        setBusyLine(line.internal_number);
        try {
            const user = (data?.users || []).find((u) => String(u.id) === String(pickedUser));
            await post('assign', { user_id: Number(pickedUser), internal_number: line.internal_number });
            toast(`Линия ${line.internal_number} назначена: ${user?.name || ''}. Сотруднику нужно войти в iCORE Phone заново`, 'success');
            setPicking(null);
            setPickedUser('');
            await load();
        } catch (e) {
            toast(e.message || 'Не удалось назначить линию', 'error');
        } finally {
            setBusyLine(null);
        }
    };

    const release = async (line) => {
        if (!line.icore_user) return;
        setBusyLine(line.internal_number);
        try {
            await post('release', { user_id: line.icore_user.id });
            toast(`Линия ${line.internal_number} освобождена`, 'success');
            await load();
        } catch (e) {
            toast(e.message || 'Не удалось снять линию', 'error');
        } finally {
            setBusyLine(null);
        }
    };

    const users = data?.users || [];
    const freeUsers = users.filter((u) => !u.sip_number);
    const lines = data?.lines || [];
    const online = lines.filter((l) => l.online).length;

    return (
        <div className="space-y-4">
            {data && !data.sip_server && (
                <div className="flex items-start gap-3 rounded-2xl bg-amber-50 px-4 py-3 text-[13px] text-amber-800 ring-1 ring-amber-100">
                    <FaIcon className="fas fa-triangle-exclamation mt-0.5" />
                    <div>
                        <div className="font-semibold">У отдела не задан SIP-сервер Binotel</div>
                        <div className="text-[12.5px]">
                            Откройте «Настройки SIP» → отдел и впишите сервер (например <span className="font-mono">sip52.binotel.com</span>),
                            иначе телефон не сможет зарегистрироваться на линии.
                        </div>
                    </div>
                </div>
            )}

            <section className="space-y-1.5">
                <div className="flex items-center justify-between gap-3 px-1">
                    <div className={iosGroupLabel}>Линии компании Binotel</div>
                    <div className="flex items-center gap-2 text-[12px] text-slate-500">
                        {data?.sip_server && <span className="font-mono">{data.sip_server}</span>}
                        {lines.length > 0 && <IosBadge tone="slate">онлайн {online} из {lines.length}</IosBadge>}
                        <button type="button" onClick={load} disabled={loading} className={`${iosBtnGhost} py-1`} title="Обновить">
                            <FaIcon className={loading ? 'fas fa-spinner fa-spin' : 'fas fa-rotate'} />
                        </button>
                    </div>
                </div>
                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {error ? (
                        <div className="px-4 py-5 text-[13px] text-rose-600">{error}</div>
                    ) : loading && !data ? (
                        <div className="px-4 py-6 text-center text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                    ) : lines.length === 0 ? (
                        <div className="px-4 py-6 text-center text-[13px] text-slate-500">Binotel не вернул ни одной линии</div>
                    ) : lines.map((line) => {
                        const isPicking = picking === line.internal_number;
                        const busy = busyLine === line.internal_number;
                        return (
                            <div key={line.internal_number} className="px-4 py-3">
                                <div className="flex items-center gap-3">
                                    <div className={`grid h-9 w-14 shrink-0 place-items-center rounded-xl font-mono text-[14px] font-semibold ${
                                        line.online ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-600'
                                    }`}>
                                        {line.internal_number}
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <div className="flex flex-wrap items-center gap-1.5">
                                            <IosBadge tone={line.online ? 'green' : 'slate'}>{line.online ? 'онлайн' : 'офлайн'}</IosBadge>
                                            {line.tls && <IosBadge tone="blue">TLS</IosBadge>}
                                            {line.binotel_employee?.name && (
                                                <span className="text-[12px] text-slate-500">в Binotel: {line.binotel_employee.name}</span>
                                            )}
                                            {!line.online && line.was_online_at && (
                                                <span className="text-[11.5px] text-slate-400">был онлайн {fmtAgo(line.was_online_at)}</span>
                                            )}
                                        </div>
                                        <div className="mt-0.5 text-[13.5px]">
                                            {line.icore_user ? (
                                                <span className="font-medium text-slate-800">
                                                    {line.icore_user.name}
                                                    {line.icore_user.login && <span className="ml-1 text-[12px] font-normal text-slate-400">@{line.icore_user.login}</span>}
                                                </span>
                                            ) : (
                                                <span className="text-slate-400">никому в iCORE не назначена</span>
                                            )}
                                        </div>
                                    </div>
                                    {canEdit && !isPicking && (
                                        line.icore_user ? (
                                            <button type="button" onClick={() => release(line)} disabled={busy} className={iosBtnGhost}>
                                                <FaIcon className={busy ? 'fas fa-spinner fa-spin' : 'fas fa-link-slash'} />
                                                Снять
                                            </button>
                                        ) : (
                                            <button
                                                type="button"
                                                onClick={() => { setPicking(line.internal_number); setPickedUser(freeUsers[0] ? String(freeUsers[0].id) : ''); }}
                                                disabled={busy}
                                                className={iosBtnSecondary}
                                            >
                                                <FaIcon className="fas fa-user-plus" />
                                                Назначить
                                            </button>
                                        )
                                    )}
                                </div>
                                {isPicking && (
                                    <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl bg-slate-50 px-3 py-2.5">
                                        <span className="text-[12.5px] text-slate-600">Кому:</span>
                                        <CustomSelect
                                            className="w-72"
                                            variant="ios"
                                            value={pickedUser}
                                            onChange={(v) => setPickedUser(String(v))}
                                            ariaLabel="Сотрудник"
                                            placeholder={users.length === 0 ? 'В отделе нет сотрудников' : 'Выберите сотрудника'}
                                            disabled={users.length === 0}
                                            searchable={users.length > 8}
                                            options={users.map((u) => ({
                                                value: String(u.id),
                                                label: `${u.name}${u.login ? ` (@${u.login})` : ''}${u.sip_number ? ` — сейчас линия ${u.sip_number}` : ''}`,
                                            }))}
                                        />
                                        <button type="button" onClick={() => assign(line)} disabled={busy || !pickedUser} className={iosBtnPrimary}>
                                            <FaIcon className={busy ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                                            Назначить
                                        </button>
                                        <button type="button" onClick={() => { setPicking(null); setPickedUser(''); }} className={iosBtnGhost}>Отмена</button>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
                <p className="px-1 text-[11.5px] leading-relaxed text-slate-500">
                    Сотрудник входит в iCORE Phone своим логином iCORE и оказывается на назначенной линии.
                    Один сотрудник — одна линия.
                </p>
            </section>
        </div>
    );
};

export default DialListLinesPanel;
