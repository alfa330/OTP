import React, { useCallback, useEffect, useState } from 'react';
import { ChevronRight, Lock, LockOpen, UserMinus } from 'lucide-react';
import { ASSET_FORMS, STATUS_SHORT, STATUS_TONE, countText, errorText } from './fixedAssetsMeta';
import {
    Avatar, CAPSULE_PRIMARY, EmptyState, ErrorNote, PageTitle, PersonSelect, SURFACE, StatusWord,
} from './fixedAssetsUi';

/*
 * «Увольнение» — вкладка HR (ТЗ, процесс 4 и правило «Увольнение»). HR выбирает сотрудника,
 * видит, числятся ли за ним ОС, и запускает передачу. Приказ закрыт, пока за сотрудником есть
 * ОС без подписанного акта; как только к запросу приложен скан акта — открыт.
 */
function PersonCard({ state, onStart, onOpenRequest }) {
    const { user, assets = [], blockers = [], request } = state;
    const locked = Boolean(state.order_locked);
    const running = request && ['draft', 'review', 'signing', 'original'].includes(request.status);
    const free = assets.filter((asset) => !asset.open_request).length;
    return (
        <div className={`${SURFACE} flex flex-col gap-3 p-4`}>
            <div className="flex items-center gap-3">
                <Avatar name={user.name} size={42} />
                <div className="min-w-0 flex-1">
                    <div className="truncate text-[15.5px] font-semibold text-slate-900">{user.name}</div>
                    <div className="truncate text-[12.5px] text-slate-500">{[user.job_title, user.department_name].filter(Boolean).join(' · ')}</div>
                </div>
                <span className={`inline-flex shrink-0 items-center gap-1.5 text-[13px] font-semibold ${locked ? 'text-rose-700' : 'text-emerald-700'}`}>
                    {locked ? <Lock size={15} /> : <LockOpen size={15} />}
                    {locked ? 'Приказ закрыт' : 'Приказ открыт'}
                </span>
            </div>

            <div className="text-[13.5px] text-slate-600">
                {assets.length === 0 && 'ОС за сотрудником нет'}
                {assets.length > 0 && !locked && `${countText(assets.length, ASSET_FORMS)} · акты подписаны`}
                {assets.length > 0 && locked && (
                    <>
                        {countText(assets.length, ASSET_FORMS)} · без подписанного акта {blockers.length}
                        <span className="mt-0.5 block truncate text-[12.5px] text-slate-400">
                            {blockers.slice(0, 3).map((item) => item.name).join(', ')}{blockers.length > 3 ? ` и ещё ${blockers.length - 3}` : ''}
                        </span>
                    </>
                )}
            </div>

            {(request || (!running && free > 0)) && (
                <div className="mt-auto flex flex-wrap items-center gap-2">
                    {request && (
                        <button type="button" onClick={() => onOpenRequest(request.id)}
                                className="inline-flex h-10 min-w-0 items-center gap-2 rounded-xl bg-slate-100 pl-3.5 pr-2.5 text-[14px] font-semibold text-slate-700 transition hover:bg-slate-200">
                            <span className="truncate">Передача № {request.id}</span>
                            <StatusWord tone={STATUS_TONE[request.status]} className="shrink-0">{STATUS_SHORT[request.status]}</StatusWord>
                            <ChevronRight size={15} className="shrink-0 text-slate-400" />
                        </button>
                    )}
                    {!running && free > 0 && (
                        <button type="button" className={CAPSULE_PRIMARY} onClick={() => onStart(user.id)}>
                            <UserMinus size={16} /> Запустить передачу
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

const GRID = { display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))' };

export default function FaDismissals({ api, version, people, needPeople, onStart, onOpenRequest }) {
    const [rows, setRows] = useState(null);
    const [error, setError] = useState('');
    const [checkId, setCheckId] = useState(null);
    const [checked, setChecked] = useState(null);

    useEffect(() => { needPeople(); }, [needPeople]);
    const load = useCallback(() => api.get('/dismissals')
        .then(({ data }) => { setRows(data.items || []); setError(''); })
        .catch((requestError) => setError(errorText(requestError, 'Список не загрузился'))), [api]);
    useEffect(() => { load(); }, [load, version]);

    /* Проверка любого сотрудника: можно ли оформлять приказ и есть ли что передавать. */
    useEffect(() => {
        if (!checkId) { setChecked(null); return undefined; }
        let alive = true;
        api.get('/dismissals/check', { user_id: checkId })
            .then(({ data }) => alive && setChecked(data))
            .catch((requestError) => alive && setError(errorText(requestError, 'Не удалось проверить сотрудника')));
        return () => { alive = false; };
    }, [api, checkId, version]);

    const listed = new Set((rows || []).map((row) => row.user.id));
    const fresh = checked && !listed.has(checked.user.id) ? checked : null;
    return (
        <div className="space-y-6">
            <div className="max-w-md">
                <PersonSelect people={people || []} value={checkId} onChange={setCheckId} placeholder="Найти сотрудника" ariaLabel="Найти сотрудника" />
            </div>
            <ErrorNote text={error} />

            {fresh && (
                <div className="max-w-xl"><PersonCard state={fresh} onStart={onStart} onOpenRequest={onOpenRequest} /></div>
            )}

            {rows && rows.length > 0 && (
                <section>
                    <PageTitle className="mb-3">Передачи</PageTitle>
                    <div style={GRID}>
                        {rows.map((row) => <PersonCard key={row.user.id} state={row} onStart={onStart} onOpenRequest={onOpenRequest} />)}
                    </div>
                </section>
            )}
            {rows && rows.length === 0 && !fresh && (
                <EmptyState icon={UserMinus} title="Передач при увольнении нет">Найдите сотрудника — и запустите передачу его ОС.</EmptyState>
            )}
        </div>
    );
}
