import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Loader2, RefreshCw, ScrollText } from 'lucide-react';
import { iosCard, iosGroupLabel, iosBtnSecondary, IosBadge } from '../ui/ios';
import useStableCallback from './useStableCallback';

/* Журнал раздела.
 *
 * В исходной вике таблиц аудита было две, почти одинаковых, и обе только
 * писались — ни API, ни интерфейс их не читали. То есть аудита фактически не
 * существовало: узнать, кто и когда выдал доступ, было неоткуда.
 */

const ACTION_LABELS = {
    'space.create': 'Создано пространство',
    'space.update': 'Изменено пространство',
    'space.archive': 'Пространство в архив',
    'section.create': 'Создан раздел',
    'section.update': 'Изменён раздел',
    'section.archive': 'Раздел в архив',
    'rule.upsert': 'Выдано право',
    'rule.delete': 'Право отозвано',
};

const ACTION_TONES = {
    'rule.upsert': 'blue',
    'rule.delete': 'amber',
    'space.archive': 'amber',
    'section.archive': 'amber',
};

const fmt = (iso) => (iso
    ? new Date(iso).toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    })
    : '—');

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/** Правила пишутся в details целиком — показываем только включённые права. */
const grantedFrom = (details) => Object.entries(details || {})
    .filter(([key, value]) => key.startsWith('can_') && value === true)
    .map(([key]) => key.replace('can_', ''));

export default function WikiAudit({ base, headers, showToast }) {
    const toast = useStableCallback(showToast);

    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);

    const load = useCallback(() => {
        setLoading(true);
        axios.get(`${base}/audit`, { headers, params: { limit: 200 } })
            .then((r) => setItems(r.data?.items || []))
            .catch((e) => toast(errText(e, 'Не удалось загрузить журнал'), 'error'))
            .finally(() => setLoading(false));
    }, [base, headers, toast]);

    useEffect(() => { load(); }, [load]);

    return (
        <section className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
                <div className={iosGroupLabel}>Журнал изменений</div>
                <button type="button" className={iosBtnSecondary} onClick={load}>
                    <RefreshCw size={14} /> Обновить
                </button>
            </div>

            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                {loading && (
                    <div className="flex items-center justify-center gap-2 py-12 text-slate-400">
                        <Loader2 size={16} className="animate-spin" />
                        <span className="text-[13px]">Загружаем…</span>
                    </div>
                )}

                {!loading && items.length === 0 && (
                    <div className="px-4 py-14 text-center">
                        <ScrollText size={20} className="mx-auto mb-2 text-slate-300" />
                        <div className="text-[13.5px] font-medium text-slate-700">Записей нет</div>
                        <p className="mx-auto mt-1 max-w-sm text-[12px] leading-relaxed text-slate-400">
                            Здесь появятся все изменения структуры и выдачи прав — кто, что и когда.
                        </p>
                    </div>
                )}

                {!loading && items.map((item) => {
                    const granted = grantedFrom(item.details);
                    return (
                        <div key={item.id} className="flex flex-wrap items-start gap-2 px-4 py-3">
                            <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-1.5">
                                    <IosBadge tone={ACTION_TONES[item.action] || 'slate'}>
                                        {ACTION_LABELS[item.action] || item.action}
                                    </IosBadge>
                                    {item.details?.name && (
                                        <span className="truncate text-[13.5px] font-medium text-slate-900">
                                            {item.details.name}
                                        </span>
                                    )}
                                </div>
                                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] text-slate-400">
                                    <span>{item.actor_name || `#${item.actor_id}`}</span>
                                    {item.target_user_name && <span>кому: {item.target_user_name}</span>}
                                    {item.details?.subject_role && (
                                        <span>роль: {item.details.subject_role}</span>
                                    )}
                                    {granted.length > 0 && <span>права: {granted.join(', ')}</span>}
                                </div>
                            </div>
                            <span className="shrink-0 text-[11.5px] tabular-nums text-slate-400">
                                {fmt(item.created_at)}
                            </span>
                        </div>
                    );
                })}
            </div>
        </section>
    );
}
