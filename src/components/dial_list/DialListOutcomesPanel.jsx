import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard, iosInput, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosToggle } from '../ui/ios';

/*
 * Итоги звонка (запрос владельца 23.09.2026): после каждого разговора оператор в
 * iCORE Phone обязан выбрать итог из этого списка и может оставить комментарий.
 * Руководитель здесь задаёт названия, цвета и порядок кнопок в телефоне.
 *
 * Итоги не удаляются, а выключаются: на них ссылается история звонков. «Перезвонить»
 * — особый признак: водитель с таким итогом снова попадёт в порции через
 * «Повтор через, часов», счётчик попыток у него обнуляется.
 */

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

/** Тонированный бейдж итога: цвет из справочника, читаемый на белом. */
export const OutcomeBadge = ({ outcome, className = '', small = false }) => {
    if (!outcome?.name) return null;
    const color = HEX_RE.test(outcome.color || '') ? outcome.color : '#8E8E93';
    return (
        <span
            className={`inline-flex max-w-full items-center gap-1.5 rounded-full font-medium ${small ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-[11.5px]'} ${className}`}
            style={{ backgroundColor: `${color}1F`, color, boxShadow: `inset 0 0 0 1px ${color}33` }}
            title={outcome.name}
        >
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
            <span className="truncate">{outcome.name}</span>
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

const DialListOutcomesPanel = ({ apiBaseUrl, authHeaders, departmentId, canEdit = true, showToast }) => {
    const [items, setItems] = useState(null);     // рабочая копия
    const [saved, setSaved] = useState(null);     // что на сервере (для «есть изменения»)
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    const load = useCallback(async () => {
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/outcomes`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            const list = Array.isArray(data.outcomes) ? data.outcomes : [];
            setItems(list.map((o) => ({ ...o })));
            setSaved(JSON.stringify(list.map(({ id, name, color, requeue, is_active }) => ({ id, name, color, requeue, is_active }))));
            setError('');
        } catch (e) {
            setError(e.message || 'Не удалось загрузить итоги');
        }
    }, [apiBaseUrl, authHeaders, departmentId]);

    useEffect(() => { load(); }, [load]);

    const snapshot = useMemo(() => (items
        ? JSON.stringify(items.map(({ id, name, color, requeue, is_active }) => ({ id: String(id).startsWith('new-') ? undefined : id, name, color, requeue, is_active })))
        : null), [items]);
    const dirty = items && snapshot !== saved;

    const patch = (idx, changes) => setItems((list) => list.map((o, i) => (i === idx ? { ...o, ...changes } : o)));
    const move = (idx, dir) => setItems((list) => {
        const next = [...list];
        const j = idx + dir;
        if (j < 0 || j >= next.length) return list;
        [next[idx], next[j]] = [next[j], next[idx]];
        return next;
    });
    const add = () => setItems((list) => [...(list || []), {
        id: tempId(), name: '', color: OUTCOME_PALETTE[(list?.length || 0) % OUTCOME_PALETTE.length],
        requeue: false, is_active: true, used: 0,
    }]);
    const remove = (idx) => setItems((list) => list.filter((_, i) => i !== idx));

    const save = async () => {
        if (!items) return;
        const payload = items.map(({ id, name, color, requeue, is_active }) => ({
            id: String(id).startsWith('new-') ? undefined : id, name: name.trim(), color, requeue, is_active,
        }));
        if (payload.some((o) => !o.name)) { toast('У каждого итога должно быть название', 'error'); return; }
        setSaving(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/outcomes`, {
                method: 'PUT', credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ items: payload }),
            });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            const list = Array.isArray(data.outcomes) ? data.outcomes : [];
            setItems(list.map((o) => ({ ...o })));
            setSaved(JSON.stringify(list.map(({ id, name, color, requeue, is_active }) => ({ id, name, color, requeue, is_active }))));
            toast('Итоги звонка сохранены. Телефоны подхватят список при следующем обновлении', 'success');
        } catch (e) {
            toast(e.message || 'Не удалось сохранить итоги', 'error');
        } finally {
            setSaving(false);
        }
    };

    return (
        <section className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 px-1">
                <div className={iosGroupLabel}>Итоги звонка</div>
                <span className="text-[11.5px] text-slate-500">Оператор выбирает один после каждого разговора</span>
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                {error ? (
                    <div className="px-4 py-4 text-[13px] text-rose-600">{error}</div>
                ) : items === null ? (
                    <div className="px-4 py-4 text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                ) : (
                    <>
                        <ul className="divide-y divide-slate-100">
                            {items.map((o, idx) => (
                                <li key={o.id} className={`flex flex-wrap items-center gap-3 px-3 py-2.5 sm:flex-nowrap ${o.is_active ? '' : 'bg-slate-50/60'}`}>
                                    <ColorPicker value={o.color} onChange={(c) => patch(idx, { color: c })} disabled={!canEdit} />
                                    <input
                                        value={o.name}
                                        onChange={(e) => patch(idx, { name: e.target.value })}
                                        disabled={!canEdit}
                                        maxLength={64}
                                        placeholder="Название итога"
                                        className={`${iosInput} min-w-[140px] flex-1 py-2 text-[13.5px] ${o.is_active ? '' : 'text-slate-400 line-through'}`}
                                    />
                                    <label className="flex shrink-0 items-center gap-2 text-[12px] text-slate-600" title="Водитель снова попадёт в порции через «Повтор через, часов»">
                                        <IosToggle checked={!!o.requeue} disabled={!canEdit} onChange={(v) => patch(idx, { requeue: v })} />
                                        перезвонить
                                    </label>
                                    <label className="flex shrink-0 items-center gap-2 text-[12px] text-slate-600">
                                        <IosToggle checked={o.is_active !== false} disabled={!canEdit} onChange={(v) => patch(idx, { is_active: v })} />
                                        {o.is_active !== false ? 'включён' : 'выключен'}
                                    </label>
                                    {o.used > 0 && <IosBadge tone="slate">{o.used}</IosBadge>}
                                    {canEdit && (
                                        <div className="flex shrink-0 items-center gap-0.5">
                                            <button type="button" onClick={() => move(idx, -1)} disabled={idx === 0} className={`${iosBtnGhost} px-2 py-1.5`} aria-label="Выше">
                                                <FaIcon className="fas fa-chevron-up" style={{ width: 11, height: 11 }} />
                                            </button>
                                            <button type="button" onClick={() => move(idx, 1)} disabled={idx === items.length - 1} className={`${iosBtnGhost} px-2 py-1.5`} aria-label="Ниже">
                                                <FaIcon className="fas fa-chevron-down" style={{ width: 11, height: 11 }} />
                                            </button>
                                            {String(o.id).startsWith('new-') && (
                                                <button type="button" onClick={() => remove(idx)} className={`${iosBtnGhost} px-2 py-1.5 text-rose-500`} aria-label="Убрать">
                                                    <FaIcon className="fas fa-xmark" style={{ width: 11, height: 11 }} />
                                                </button>
                                            )}
                                        </div>
                                    )}
                                </li>
                            ))}
                            {items.length === 0 && (
                                <li className="px-4 py-5 text-center text-[13px] text-slate-500">Итогов пока нет — добавьте хотя бы один.</li>
                            )}
                        </ul>
                        {canEdit && (
                            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-3 py-2.5">
                                <button type="button" onClick={add} disabled={items.length >= 30} className={iosBtnSecondary}>
                                    <FaIcon className="fas fa-plus" style={{ width: 11, height: 11 }} /> Добавить итог
                                </button>
                                <div className="flex items-center gap-2">
                                    {dirty && <span className="text-[12px] text-amber-600">Есть несохранённые изменения</span>}
                                    <button type="button" onClick={save} disabled={!dirty || saving} className={iosBtnPrimary}>
                                        <FaIcon className={saving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                                        Сохранить
                                    </button>
                                </div>
                            </div>
                        )}
                    </>
                )}
            </div>
            <div className="px-1 text-[11.5px] text-slate-500">
                Выключенный итог пропадает из телефона, но остаётся в истории. Число справа — сколько раз итог уже выбирали.
            </div>
        </section>
    );
};

export default DialListOutcomesPanel;
