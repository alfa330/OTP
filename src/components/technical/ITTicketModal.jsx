import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';

// ─── Styling tokens (clean macOS/iOS look) ─────────────────────────────────────
const FIELD_CLASS =
    'mt-1 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition focus:outline-none focus:ring-2 focus:ring-indigo-400/60 focus:border-indigo-400';
const LABEL_CLASS = 'text-[11px] font-semibold uppercase tracking-wide text-slate-500';
const CARD_CLASS = 'rounded-2xl border border-slate-200/80 bg-white/90 p-4 shadow-sm';

const PRIORITY_META = {
    low: { label: 'Низкий', cls: 'bg-emerald-100 text-emerald-700 border-emerald-200' },
    medium: { label: 'Средний', cls: 'bg-sky-100 text-sky-700 border-sky-200' },
    high: { label: 'Высокий', cls: 'bg-amber-100 text-amber-700 border-amber-200' },
    critical: { label: 'Критический', cls: 'bg-rose-100 text-rose-700 border-rose-200' },
};
const PRIORITY_ORDER = ['low', 'medium', 'high', 'critical'];

// Понятные сообщения для кодов ошибок ИИ от бэкенда
const AI_ERROR_MESSAGES = {
    ai_unavailable: 'Сервис ИИ (Google Gemini) сейчас перегружен. Подождите 10–20 секунд и нажмите ещё раз — либо заполните поля и отправьте заявку вручную.',
    ai_timeout: 'ИИ слишком долго отвечает (перегрузка). Попробуйте ещё раз — либо отправьте заявку вручную.',
    ai_blocked: 'ИИ не смог сформировать ответ. Уточните описание и попробуйте снова.',
    json_parse_error: 'ИИ вернул некорректный ответ. Попробуйте ещё раз.',
    ai_failed: 'Сервис ИИ временно недоступен. Попробуйте позже — либо отправьте заявку вручную.',
};
const aiErrorText = (code) => AI_ERROR_MESSAGES[code] || 'ИИ не смог обработать запрос. Попробуйте ещё раз или заполните заявку вручную.';

// ─── Dynamic field renderer ─────────────────────────────────────────────────
const DynamicField = memo(function DynamicField({ field, value, onChange }) {
    const type = String(field?.type || 'text').toLowerCase();
    const common = {
        value: value ?? '',
        onChange: (e) => onChange(field.key, e.target.value),
        placeholder: field?.placeholder || '',
        className: FIELD_CLASS,
    };
    return (
        <label className="block">
            <span className={LABEL_CLASS}>
                {field?.label || field?.key}
                {field?.required && <span className="text-rose-500"> *</span>}
            </span>
            {type === 'textarea' ? (
                <textarea {...common} rows={3} />
            ) : type === 'select' && Array.isArray(field?.options) && field.options.length > 0 ? (
                <select {...common}>
                    <option value="">— выберите —</option>
                    {field.options.map((opt, i) => (
                        <option key={`${field.key}-opt-${i}`} value={opt}>{opt}</option>
                    ))}
                </select>
            ) : type === 'date' ? (
                <input type="date" {...common} />
            ) : type === 'time' ? (
                <input type="time" {...common} />
            ) : type === 'number' ? (
                <input type="number" {...common} />
            ) : (
                <input type="text" {...common} />
            )}
            {field?.hint && <span className="mt-1 block text-[11px] text-slate-400">{field.hint}</span>}
        </label>
    );
});

// ─── Channel manager (admin) ──────────────────────────────────────────────────
const ChannelManager = memo(function ChannelManager({
    apiBaseUrl, buildHeaders, notify, channels, onChannelsChanged,
}) {
    const [open, setOpen] = useState(false);
    const [newChatId, setNewChatId] = useState('');
    const [busy, setBusy] = useState(false);
    const [actingId, setActingId] = useState(null);

    const addChannel = useCallback(async () => {
        const value = String(newChatId || '').trim();
        if (!value) { notify('Укажите chat_id чата или канала', 'error'); return; }
        setBusy(true);
        try {
            await axios.post(`${apiBaseUrl}/api/it_tickets/channels`, { chat_id: value }, { headers: buildHeaders() });
            setNewChatId('');
            notify('Канал добавлен', 'success');
            await onChannelsChanged();
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось добавить канал', 'error');
        } finally {
            setBusy(false);
        }
    }, [apiBaseUrl, buildHeaders, newChatId, notify, onChannelsChanged]);

    const toggleActive = useCallback(async (channel) => {
        setActingId(channel.id);
        try {
            await axios.patch(`${apiBaseUrl}/api/it_tickets/channels/${channel.id}`,
                { is_active: !channel.is_active }, { headers: buildHeaders() });
            await onChannelsChanged();
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось изменить канал', 'error');
        } finally {
            setActingId(null);
        }
    }, [apiBaseUrl, buildHeaders, notify, onChannelsChanged]);

    const removeChannel = useCallback(async (channel) => {
        if (typeof window !== 'undefined' && !window.confirm(`Удалить канал «${channel.title || channel.chat_id}»?`)) return;
        setActingId(channel.id);
        try {
            await axios.delete(`${apiBaseUrl}/api/it_tickets/channels/${channel.id}`, { headers: buildHeaders() });
            notify('Канал удалён', 'success');
            await onChannelsChanged();
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось удалить канал', 'error');
        } finally {
            setActingId(null);
        }
    }, [apiBaseUrl, buildHeaders, notify, onChannelsChanged]);

    const testChannel = useCallback(async (channel) => {
        setActingId(channel.id);
        try {
            await axios.post(`${apiBaseUrl}/api/it_tickets/channels/${channel.id}/test`, {}, { headers: buildHeaders() });
            notify('Тестовое сообщение отправлено', 'success');
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось отправить тест', 'error');
        } finally {
            setActingId(null);
        }
    }, [apiBaseUrl, buildHeaders, notify]);

    return (
        <div className="rounded-2xl border border-slate-200/80 bg-slate-50/70 p-3">
            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                className="flex w-full items-center justify-between text-sm font-semibold text-slate-700"
            >
                <span className="flex items-center gap-2">
                    <FaIcon className="fas fa-gear text-slate-500" style={{ width: '0.9em', height: '0.9em' }} />
                    Управление каналами
                </span>
                <FaIcon className="fas fa-chevron-down text-slate-400"
                    style={{ width: '0.8em', height: '0.8em', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }} />
            </button>

            {open && (
                <div className="mt-3 space-y-3">
                    <div className="rounded-xl border border-dashed border-slate-300 bg-white p-3 text-[12px] text-slate-500">
                        Добавьте бота в нужную группу/канал — он появится здесь автоматически. Либо добавьте вручную по <code>chat_id</code> (например, <code>-1001234567890</code>).
                    </div>
                    <div className="flex gap-2">
                        <input
                            type="text"
                            value={newChatId}
                            onChange={(e) => setNewChatId(e.target.value)}
                            placeholder="chat_id канала/группы"
                            className={FIELD_CLASS + ' mt-0'}
                        />
                        <button
                            type="button"
                            onClick={addChannel}
                            disabled={busy}
                            className={`shrink-0 inline-flex items-center gap-2 rounded-xl px-4 text-sm font-semibold text-white transition ${busy ? 'bg-indigo-400' : 'bg-indigo-600 hover:bg-indigo-700'}`}
                        >
                            <FaIcon className={`fas ${busy ? 'fa-spinner fa-spin' : 'fa-plus'}`} style={{ width: '0.85em', height: '0.85em' }} />
                            Добавить
                        </button>
                    </div>

                    <div className="space-y-2">
                        {channels.length === 0 ? (
                            <div className="text-[12px] text-slate-400">Пока нет ни одного канала.</div>
                        ) : channels.map((ch) => (
                            <div key={ch.id} className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2">
                                <div className="min-w-0">
                                    <div className="truncate text-sm font-medium text-slate-800">{ch.title || `Чат ${ch.chat_id}`}</div>
                                    <div className="truncate text-[11px] text-slate-400">
                                        {ch.chat_type || '—'} · {ch.chat_id}{ch.source ? ` · ${ch.source === 'auto' ? 'авто' : 'вручную'}` : ''}
                                    </div>
                                </div>
                                <div className="flex shrink-0 items-center gap-1">
                                    <button type="button" title="Тест" onClick={() => testChannel(ch)} disabled={actingId === ch.id}
                                        className="rounded-lg border border-slate-200 bg-white p-1.5 text-slate-500 hover:bg-slate-50">
                                        <FaIcon className="fas fa-paper-plane" style={{ width: '0.8em', height: '0.8em' }} />
                                    </button>
                                    <button type="button" title={ch.is_active ? 'Отключить' : 'Включить'} onClick={() => toggleActive(ch)} disabled={actingId === ch.id}
                                        className="rounded-lg border border-slate-200 bg-white p-1.5 text-slate-500 hover:bg-slate-50">
                                        <FaIcon className={`fas ${ch.is_active ? 'fa-toggle-on text-emerald-500' : 'fa-toggle-off'}`} style={{ width: '0.95em', height: '0.95em' }} />
                                    </button>
                                    <button type="button" title="Удалить" onClick={() => removeChannel(ch)} disabled={actingId === ch.id}
                                        className="rounded-lg border border-rose-200 bg-white p-1.5 text-rose-400 hover:bg-rose-50">
                                        <FaIcon className="fas fa-trash" style={{ width: '0.8em', height: '0.8em' }} />
                                    </button>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
});

// ─── AI instructions editor (admin / department head) ──────────────────────────
const INSTRUCTION_PROFILE_LABELS = {
    common: 'Общие (все профили)',
    op: 'Отдел продаж (ОП)',
    szov: 'СЗоВ',
};

const InstructionsEditor = memo(function InstructionsEditor({ apiBaseUrl, buildHeaders, notify }) {
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [items, setItems] = useState([]);
    const [editable, setEditable] = useState([]);
    const [activeProfile, setActiveProfile] = useState('');
    const [draft, setDraft] = useState('');
    const [saving, setSaving] = useState(false);
    const loadedRef = useRef(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await axios.get(`${apiBaseUrl}/api/it_tickets/instructions`, { headers: buildHeaders() });
            const its = Array.isArray(res?.data?.items) ? res.data.items : [];
            const ed = Array.isArray(res?.data?.editable_profiles) ? res.data.editable_profiles : [];
            setItems(its);
            setEditable(ed);
            setActiveProfile((prev) => (prev && ed.includes(prev)) ? prev : (ed[0] || ''));
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось загрузить инструкции', 'error');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, buildHeaders, notify]);

    const toggleOpen = useCallback(() => {
        setOpen((o) => {
            const next = !o;
            if (next && !loadedRef.current) { loadedRef.current = true; load(); }
            return next;
        });
    }, [load]);

    useEffect(() => {
        if (!activeProfile) { setDraft(''); return; }
        const found = items.find((i) => i.profile === activeProfile);
        setDraft(found?.instructions || '');
    }, [activeProfile, items]);

    const meta = useMemo(() => items.find((i) => i.profile === activeProfile), [items, activeProfile]);

    const save = useCallback(async () => {
        if (!activeProfile) return;
        setSaving(true);
        try {
            const res = await axios.put(`${apiBaseUrl}/api/it_tickets/instructions`,
                { profile: activeProfile, instructions: draft }, { headers: buildHeaders() });
            const item = res?.data?.item;
            if (item) {
                setItems((prev) => [...prev.filter((i) => i.profile !== item.profile), item]);
            }
            notify('Инструкции сохранены', 'success');
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось сохранить инструкции', 'error');
        } finally {
            setSaving(false);
        }
    }, [apiBaseUrl, buildHeaders, draft, activeProfile, notify]);

    const dirty = (meta?.instructions || '') !== draft;

    return (
        <div className={CARD_CLASS}>
            <button
                type="button"
                onClick={toggleOpen}
                className="flex w-full items-center justify-between text-sm font-semibold text-slate-700"
            >
                <span className="flex items-center gap-2">
                    <FaIcon className="fas fa-lightbulb text-amber-500" style={{ width: '0.95em', height: '0.95em' }} />
                    Инструкции для ИИ
                    <span className="text-[11px] font-normal text-slate-400">актуальные изменения для бота</span>
                </span>
                <FaIcon className="fas fa-chevron-down text-slate-400"
                    style={{ width: '0.8em', height: '0.8em', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }} />
            </button>

            {open && (
                <div className="mt-3 space-y-3">
                    <p className="text-[12px] text-slate-500">
                        Эти заметки добавляются к промпту ИИ и имеют приоритет при конфликте с базовыми правилами. Удобно фиксировать недавние изменения (например, новый софт, переименование систем, новые РМ).
                    </p>
                    {loading ? (
                        <div className="flex items-center gap-2 text-sm text-slate-500">
                            <FaIcon className="fas fa-spinner fa-spin text-indigo-500" style={{ width: '1em', height: '1em' }} />
                            Загрузка…
                        </div>
                    ) : editable.length === 0 ? (
                        <div className="text-[12px] text-slate-400">Нет доступных для редактирования профилей.</div>
                    ) : (
                        <>
                            {editable.length > 1 && (
                                <div className="inline-flex flex-wrap gap-1 rounded-xl bg-slate-100 p-0.5">
                                    {editable.map((p) => (
                                        <button
                                            key={p}
                                            type="button"
                                            onClick={() => setActiveProfile(p)}
                                            className={`rounded-lg px-3 py-1 text-xs font-semibold transition ${activeProfile === p ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                                        >
                                            {INSTRUCTION_PROFILE_LABELS[p] || p}
                                        </button>
                                    ))}
                                </div>
                            )}
                            <textarea
                                value={draft}
                                onChange={(e) => setDraft(e.target.value)}
                                rows={6}
                                maxLength={8000}
                                placeholder={`Например: «С 01.06 телефония Oktell заменена на X — все заявки по звонкам направлять как «${INSTRUCTION_PROFILE_LABELS[activeProfile] || ''}». Добавлены РМ 31–35.»`}
                                className={FIELD_CLASS + ' text-[12.5px] leading-relaxed'}
                            />
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className="text-[11px] text-slate-400">
                                    {meta?.updated_at ? `Обновлено: ${meta.updated_by_name || '—'} · ${meta.updated_at}` : 'Ещё не заполнялось'}
                                    {` · ${draft.length}/8000`}
                                </span>
                                <button
                                    type="button"
                                    onClick={save}
                                    disabled={saving || !dirty}
                                    className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-white transition ${(saving || !dirty) ? 'bg-indigo-300' : 'bg-indigo-600 hover:bg-indigo-700'}`}
                                >
                                    <FaIcon className={`fas ${saving ? 'fa-spinner fa-spin' : 'fa-save'}`} style={{ width: '0.85em', height: '0.85em' }} />
                                    {saving ? 'Сохранение…' : 'Сохранить'}
                                </button>
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
});

// ─── CatalogEditor (редактирование категорий и типов проблем — админ) ──────────
const CatalogEditor = memo(function CatalogEditor({ apiBaseUrl, buildHeaders, notify, catalog, editableProfiles = [], onSaved }) {
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState(null);
    const [activeProfile, setActiveProfile] = useState('');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (open && catalog && !draft) {
            try { setDraft(JSON.parse(JSON.stringify(catalog))); } catch { setDraft(null); }
        }
    }, [open, catalog, draft]);

    // Сбрасываем черновик при сворачивании — при повторном открытии берём свежий каталог
    useEffect(() => {
        if (!open) setDraft(null);
    }, [open]);

    // Глава отдела правит только свой профиль; админ — все доступные.
    const profiles = useMemo(() => {
        if (!draft) return [];
        const allowed = Array.isArray(editableProfiles) ? editableProfiles : [];
        return Object.keys(draft).filter((p) => allowed.includes(p));
    }, [draft, editableProfiles]);

    useEffect(() => {
        if (profiles.length && !profiles.includes(activeProfile)) setActiveProfile(profiles[0]);
    }, [profiles, activeProfile]);

    const cats = (draft && draft[activeProfile] && draft[activeProfile].categories) || [];
    const profLabel = (p) => (draft && draft[p] && draft[p].label) || (p === 'szov' ? 'СЗоВ' : 'ОП');

    const update = (mutator) => setDraft((prev) => {
        const next = JSON.parse(JSON.stringify(prev));
        mutator(next);
        return next;
    });
    const setCatName = (ci, v) => update((d) => { d[activeProfile].categories[ci].name = v; });
    const delCat = (ci) => update((d) => { d[activeProfile].categories.splice(ci, 1); });
    const addCat = () => update((d) => { d[activeProfile].categories.push({ name: 'Новая категория', items: [] }); });
    const setItem = (ci, ii, v) => update((d) => { d[activeProfile].categories[ci].items[ii] = v; });
    const delItem = (ci, ii) => update((d) => { d[activeProfile].categories[ci].items.splice(ii, 1); });
    const addItem = (ci) => update((d) => { d[activeProfile].categories[ci].items.push(''); });

    const save = useCallback(async () => {
        if (!draft) return;
        setSaving(true);
        try {
            const res = await axios.put(`${apiBaseUrl}/api/it_tickets/catalog`, { catalog: draft }, { headers: buildHeaders() });
            const saved = res?.data?.catalog;
            if (saved) { setDraft(JSON.parse(JSON.stringify(saved))); if (onSaved) onSaved(saved); }
            notify('Каталог сохранён', 'success');
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось сохранить каталог', 'error');
        } finally {
            setSaving(false);
        }
    }, [apiBaseUrl, buildHeaders, draft, onSaved, notify]);

    return (
        <div className={CARD_CLASS}>
            <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between text-sm font-semibold text-slate-700">
                <span className="flex items-center gap-2">
                    <FaIcon className="fas fa-list text-indigo-500" style={{ width: '0.95em', height: '0.95em' }} />
                    Категории и типы проблем
                    <span className="text-[11px] font-normal text-slate-400">редактирование каталога</span>
                </span>
                <FaIcon className="fas fa-chevron-down text-slate-400" style={{ width: '0.8em', height: '0.8em', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }} />
            </button>

            {open && (
                <div className="mt-3 space-y-3">
                    {!draft ? (
                        <div className="text-[12px] text-slate-400">Загрузка каталога…</div>
                    ) : profiles.length === 0 ? (
                        <div className="text-[12px] text-slate-400">Нет доступных для редактирования профилей.</div>
                    ) : (
                        <>
                            {profiles.length > 1 && (
                            <div className="inline-flex flex-wrap gap-1 rounded-xl bg-slate-100 p-0.5">
                                {profiles.map((p) => (
                                    <button key={p} type="button" onClick={() => setActiveProfile(p)}
                                        className={`rounded-lg px-3 py-1 text-xs font-semibold transition ${activeProfile === p ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                                        {profLabel(p)}
                                    </button>
                                ))}
                            </div>
                            )}

                            <div className="space-y-3">
                                {cats.map((c, ci) => (
                                    <div key={ci} className="rounded-xl border border-slate-200 bg-slate-50/60 p-2.5">
                                        <div className="flex items-center gap-2">
                                            <input
                                                type="text"
                                                value={c.name}
                                                onChange={(e) => setCatName(ci, e.target.value)}
                                                placeholder="Название категории"
                                                className={FIELD_CLASS + ' mt-0 flex-1 font-semibold'}
                                            />
                                            <button type="button" title="Удалить категорию" onClick={() => delCat(ci)}
                                                className="shrink-0 rounded-lg border border-rose-200 bg-white p-2 text-rose-400 hover:bg-rose-50">
                                                <FaIcon className="fas fa-trash" style={{ width: '0.8em', height: '0.8em' }} />
                                            </button>
                                        </div>
                                        <div className="mt-2 space-y-1.5 pl-2">
                                            {(c.items || []).map((it, ii) => (
                                                <div key={ii} className="flex items-center gap-2">
                                                    <span className="text-slate-300">•</span>
                                                    <input
                                                        type="text"
                                                        value={it}
                                                        onChange={(e) => setItem(ci, ii, e.target.value)}
                                                        placeholder="Тип проблемы"
                                                        className={FIELD_CLASS + ' mt-0 flex-1 text-[13px]'}
                                                    />
                                                    <button type="button" title="Удалить тип" onClick={() => delItem(ci, ii)}
                                                        className="shrink-0 rounded-lg border border-slate-200 bg-white p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-500">
                                                        <FaIcon className="fas fa-times" style={{ width: '0.75em', height: '0.75em' }} />
                                                    </button>
                                                </div>
                                            ))}
                                            <button type="button" onClick={() => addItem(ci)}
                                                className="text-[12px] font-medium text-indigo-600 hover:text-indigo-700">
                                                <FaIcon className="fas fa-plus" style={{ width: '0.7em', height: '0.7em' }} /> Добавить тип
                                            </button>
                                        </div>
                                    </div>
                                ))}
                            </div>

                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <button type="button" onClick={addCat}
                                    className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-100">
                                    <FaIcon className="fas fa-plus" style={{ width: '0.8em', height: '0.8em' }} /> Добавить категорию
                                </button>
                                <button type="button" onClick={save} disabled={saving}
                                    className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-white transition ${saving ? 'bg-indigo-300' : 'bg-indigo-600 hover:bg-indigo-700'}`}>
                                    <FaIcon className={`fas ${saving ? 'fa-spinner fa-spin' : 'fa-save'}`} style={{ width: '0.85em', height: '0.85em' }} />
                                    {saving ? 'Сохранение…' : 'Сохранить каталог'}
                                </button>
                            </div>
                            <p className="text-[11px] text-slate-400">Пустые названия не сохраняются. Изменения сразу применяются к форме и подсказкам ИИ.</p>
                        </>
                    )}
                </div>
            )}
        </div>
    );
});

// ─── Main modal ─────────────────────────────────────────────────────────────
const ITTicketModal = ({ isOpen, onClose, apiBaseUrl, buildHeaders, notify, canManageChannels, canEditInstructions, canEditCatalog }) => {
    const [catalog, setCatalog] = useState(null);
    const [defaultProfile, setDefaultProfile] = useState('op');
    const [profile, setProfile] = useState('op');
    const [categoryName, setCategoryName] = useState('');
    const [subcategory, setSubcategory] = useState('');
    const [categoryNote, setCategoryNote] = useState(''); // пояснение ИИ при автокоррекции темы
    const [description, setDescription] = useState('');

    const [aiFields, setAiFields] = useState([]);
    const [fieldValues, setFieldValues] = useState({});
    const [priority, setPriority] = useState('medium');
    const [previewText, setPreviewText] = useState('');
    const [ticketTitle, setTicketTitle] = useState('');
    const [composed, setComposed] = useState(false);

    const [channels, setChannels] = useState([]);
    const [canManage, setCanManage] = useState(Boolean(canManageChannels));
    const [pinned, setPinned] = useState({});           // { op: {...}, szov: {...} }
    const [pinnableProfiles, setPinnableProfiles] = useState([]);
    const [pinDraftId, setPinDraftId] = useState('');
    const [pinning, setPinning] = useState(false);
    const [catalogEditableProfiles, setCatalogEditableProfiles] = useState([]);

    const [loadingCatalog, setLoadingCatalog] = useState(false);
    const [aiMode, setAiMode] = useState(null); // 'draft' | 'finalize' | null
    const [sending, setSending] = useState(false);

    const notifyRef = useRef(notify);
    useEffect(() => { notifyRef.current = notify; }, [notify]);
    const toast = useCallback((m, t) => { if (typeof notifyRef.current === 'function') notifyRef.current(m, t); }, []);

    // ── load catalog + channels on open ──
    const loadChannels = useCallback(async () => {
        try {
            const res = await axios.get(`${apiBaseUrl}/api/it_tickets/channels${canManageChannels ? '?all=1' : ''}`, { headers: buildHeaders() });
            setChannels(Array.isArray(res?.data?.items) ? res.data.items : []);
            setCanManage(Boolean(res?.data?.can_manage));
        } catch (err) {
            toast(err?.response?.data?.error || 'Не удалось загрузить каналы', 'error');
        }
    }, [apiBaseUrl, buildHeaders, canManageChannels, toast]);

    const applyMeta = useCallback((data) => {
        setPinned(data?.pinned || {});
        setPinnableProfiles(Array.isArray(data?.pinnable_profiles) ? data.pinnable_profiles : []);
        setCatalogEditableProfiles(Array.isArray(data?.catalog_editable_profiles) ? data.catalog_editable_profiles : []);
    }, []);

    // Перечитываем мета-данные (закреплённые каналы), не сбрасывая выбор пользователя
    const refreshMeta = useCallback(async () => {
        try {
            const res = await axios.get(`${apiBaseUrl}/api/it_tickets/catalog`, { headers: buildHeaders() });
            applyMeta(res?.data);
        } catch {
            /* тихо игнорируем — основное состояние уже загружено */
        }
    }, [apiBaseUrl, buildHeaders, applyMeta]);

    useEffect(() => {
        if (!isOpen) return;
        let alive = true;
        setLoadingCatalog(true);
        (async () => {
            try {
                const res = await axios.get(`${apiBaseUrl}/api/it_tickets/catalog`, { headers: buildHeaders() });
                if (!alive) return;
                setCatalog(res?.data?.catalog || null);
                const dp = res?.data?.default_profile || 'op';
                setDefaultProfile(dp);
                setProfile(dp);
                applyMeta(res?.data);
            } catch (err) {
                if (alive) toast(err?.response?.data?.error || 'Не удалось загрузить каталог', 'error');
            } finally {
                if (alive) setLoadingCatalog(false);
            }
        })();
        loadChannels();
        return () => { alive = false; };
    }, [isOpen, apiBaseUrl, buildHeaders, toast, loadChannels, applyMeta]);

    // ── Esc to close ──
    useEffect(() => {
        if (!isOpen) return undefined;
        const onKey = (e) => { if (e.key === 'Escape' && !sending) onClose(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [isOpen, onClose, sending]);

    const categories = useMemo(() => {
        const prof = catalog?.[profile];
        return Array.isArray(prof?.categories) ? prof.categories : [];
    }, [catalog, profile]);

    const subItems = useMemo(() => {
        const cat = categories.find((c) => c.name === categoryName);
        return Array.isArray(cat?.items) ? cat.items : [];
    }, [categories, categoryName]);

    const resetAfterCategoryChange = useCallback(() => {
        setAiFields([]); setFieldValues({});
        setComposed(false); setPreviewText(''); setTicketTitle(''); setCategoryNote('');
    }, []);

    const activeChannels = useMemo(() => channels.filter((c) => c.is_active), [channels]);

    const activePin = useMemo(() => pinned?.[profile] || null, [pinned, profile]);
    const channelReady = Boolean(activePin?.channel_id && activePin?.channel_is_active);
    const canPin = useMemo(() => pinnableProfiles.includes(profile), [pinnableProfiles, profile]);

    // выпадающий список закрепления держим в синхроне с активным профилем
    useEffect(() => {
        setPinDraftId(activePin?.channel_id ? String(activePin.channel_id) : '');
    }, [activePin]);

    const pinChannel = useCallback(async () => {
        setPinning(true);
        try {
            const channel_id = pinDraftId ? Number(pinDraftId) : null;
            await axios.put(`${apiBaseUrl}/api/it_tickets/pinned`, { profile, channel_id }, { headers: buildHeaders() });
            toast(channel_id ? 'Канал закреплён' : 'Канал откреплён', 'success');
            await refreshMeta();
        } catch (err) {
            toast(err?.response?.data?.error || 'Не удалось закрепить канал', 'error');
        } finally {
            setPinning(false);
        }
    }, [apiBaseUrl, buildHeaders, profile, pinDraftId, refreshMeta, toast]);

    const setFieldValue = useCallback((key, value) => {
        setFieldValues((prev) => ({ ...prev, [key]: value }));
    }, []);

    // ── AI calls ──
    const callAi = useCallback(async (mode) => {
        if (!description.trim() && !categoryName) {
            toast('Опишите проблему или выберите категорию', 'error');
            return;
        }
        setAiMode(mode);
        try {
            const res = await axios.post(`${apiBaseUrl}/api/it_tickets/ai`, {
                mode,
                profile,
                category: categoryName,
                subcategory,
                description,
                fields: fieldValues,
            }, { headers: buildHeaders() });

            if (res?.data?.status !== 'success') {
                toast(aiErrorText(res?.data?.error), 'error');
                return;
            }
            const result = res.data.result || {};
            // ИИ может скорректировать тему. Применяем ТОЛЬКО значения, которые реально
            // есть в действующем каталоге (с приведением регистра/пробелов к точной строке),
            // иначе <select> покажет пустоту при «исправленном» баннере.
            const adjusted = Boolean(result.category_adjusted);
            const norm = (s) => String(s || '').trim().toLowerCase();
            const catObjs = Array.isArray(catalog?.[profile]?.categories) ? catalog[profile].categories : [];
            const findCat = (name) => (name ? catObjs.find((c) => norm(c.name) === norm(name)) : null);

            let appliedCategory = categoryName;
            const catMatch = findCat(result.category);
            if (catMatch && (adjusted || !categoryName)) { appliedCategory = catMatch.name; setCategoryName(catMatch.name); }

            let subApplied = false;
            if (result.subcategory) {
                const owner = findCat(appliedCategory);
                const items = Array.isArray(owner?.items) ? owner.items : [];
                const subMatch = items.find((it) => norm(it) === norm(result.subcategory));
                if (subMatch && (adjusted || !subcategory)) { setSubcategory(subMatch); subApplied = true; }
            }

            if (adjusted && (catMatch || subApplied) && result.category_adjustment_note) {
                setCategoryNote(String(result.category_adjustment_note));
                toast(`ИИ скорректировал тему: ${result.category_adjustment_note}`, 'info');
            } else {
                setCategoryNote('');
            }
            if (result.priority && PRIORITY_META[result.priority]) setPriority(result.priority);

            const fields = Array.isArray(result?.form?.fields) ? result.form.fields : [];
            if (fields.length > 0) {
                setAiFields(fields);
                setFieldValues((prev) => {
                    const next = { ...prev };
                    fields.forEach((f) => {
                        if (next[f.key] === undefined) next[f.key] = f.value ?? '';
                    });
                    return next;
                });
            }

            const ticketMd = result?.ticket?.markdown || result?.ticket?.summary || '';
            if (result?.ticket?.title) setTicketTitle(result.ticket.title);

            if (result.status === 'ready') {
                if (ticketMd) setPreviewText(ticketMd);
                setComposed(true);
                toast('Заявка готова — проверьте текст и отправьте', 'success');
            } else if (result.status === 'need_more_info') {
                setComposed(false);
                toast('Заполните обязательные поля (отмечены *) и нажмите ещё раз', 'info');
            } else {
                // draft
                if (ticketMd && !previewText.trim()) setPreviewText(ticketMd);
                toast('Черновик сформирован — заполните детали и оформите заявку', 'success');
            }
        } catch (err) {
            toast(aiErrorText(err?.response?.data?.error), 'error');
        } finally {
            setAiMode(null);
        }
    }, [apiBaseUrl, buildHeaders, profile, categoryName, subcategory, description, fieldValues, previewText, toast, catalog]);

    // ── send ──
    const handleSend = useCallback(async () => {
        const body = (previewText || '').trim() || description.trim();
        if (!body) { toast('Пустой текст заявки. Опишите проблему или оформите её с ИИ', 'error'); return; }
        if (!channelReady) { toast('Канал не закреплён. Обратитесь к админу или главе отдела', 'error'); return; }
        setSending(true);
        try {
            const res = await axios.post(`${apiBaseUrl}/api/it_tickets/send`, {
                profile,
                category: categoryName,
                subcategory,
                priority,
                title: ticketTitle,
                body,
                fields: fieldValues,
            }, { headers: buildHeaders() });
            toast(`Заявка отправлена${res?.data?.channel_title ? ` в «${res.data.channel_title}»` : ''}`, 'success');
            onClose();
        } catch (err) {
            toast(err?.response?.data?.error || 'Не удалось отправить заявку', 'error');
        } finally {
            setSending(false);
        }
    }, [apiBaseUrl, buildHeaders, previewText, description, channelReady, profile, categoryName, subcategory, priority, ticketTitle, fieldValues, toast, onClose]);

    if (!isOpen) return null;

    const aiBusy = aiMode !== null;
    const profileLabel = (p) => catalog?.[p]?.label || (p === 'szov' ? 'СЗоВ' : 'ОП');

    return (
        <div
            className="fixed inset-0 z-[120] flex items-center justify-center p-3 sm:p-4"
            style={{ backgroundColor: 'rgba(15,23,42,0.45)', backdropFilter: 'blur(6px)' }}
            onClick={(e) => { if (e.target === e.currentTarget && !sending) onClose(); }}
        >
            <style>{`
                @keyframes itTicketIn { from { opacity:0; transform: scale(0.97) translateY(10px); } to { opacity:1; transform: scale(1) translateY(0); } }
            `}</style>
            <div
                className="flex w-full max-w-5xl flex-col overflow-hidden rounded-3xl border border-white/60 bg-slate-50 shadow-2xl"
                style={{ maxHeight: '94vh', animation: 'itTicketIn .22s ease' }}
            >
                {/* Header */}
                <div className="relative shrink-0 px-6 py-5 text-white" style={{ background: 'linear-gradient(135deg,#4f46e5 0%,#2563eb 55%,#0ea5e9 100%)' }}>
                    <button
                        type="button"
                        onClick={() => { if (!sending) onClose(); }}
                        className="absolute right-4 top-4 rounded-full p-1.5 text-white/70 transition hover:bg-white/20 hover:text-white"
                        aria-label="Закрыть"
                    >
                        <FaIcon className="fas fa-times" style={{ width: '1.1em', height: '1.1em' }} />
                    </button>
                    <div className="flex items-center gap-3">
                        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-white/20 backdrop-blur">
                            <FaIcon className="fas fa-headset" style={{ width: '1.3em', height: '1.3em' }} />
                        </div>
                        <div>
                            <h3 className="text-lg font-bold leading-tight">Тикет в IT-отдел</h3>
                            <p className="text-[13px] text-white/80">ИИ поможет составить понятную заявку и отправит её в нужный канал</p>
                        </div>
                    </div>
                </div>

                {/* Body */}
                <div className="flex-1 overflow-y-auto p-5">
                    {loadingCatalog ? (
                        <div className="flex items-center gap-2 p-6 text-sm text-slate-500">
                            <FaIcon className="fas fa-spinner fa-spin text-indigo-500" style={{ width: '1em', height: '1em' }} />
                            Загрузка каталога…
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                            {/* ── ЛЕВАЯ КОЛОНКА — составление ── */}
                            <div className="space-y-4">
                                {/* Profile + category + description */}
                                <div className={CARD_CLASS}>
                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                        <span className={LABEL_CLASS}>Профиль (по отделу)</span>
                                        <div className="inline-flex rounded-xl bg-slate-100 p-0.5">
                                            {['op', 'szov'].map((p) => (
                                                <button
                                                    key={p}
                                                    type="button"
                                                    onClick={() => { setProfile(p); setCategoryName(''); setSubcategory(''); resetAfterCategoryChange(); }}
                                                    className={`rounded-lg px-3 py-1 text-xs font-semibold transition ${profile === p ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                                                >
                                                    {profileLabel(p)}{p === defaultProfile ? ' •' : ''}
                                                </button>
                                            ))}
                                        </div>
                                    </div>

                                    <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                                        <label className="block">
                                            <span className={LABEL_CLASS}>Категория</span>
                                            <select
                                                value={categoryName}
                                                onChange={(e) => { setCategoryName(e.target.value); setSubcategory(''); resetAfterCategoryChange(); }}
                                                className={FIELD_CLASS}
                                            >
                                                <option value="">— выберите категорию —</option>
                                                {categories.map((c) => (
                                                    <option key={c.name} value={c.name}>{c.name}</option>
                                                ))}
                                            </select>
                                        </label>
                                        <label className="block">
                                            <span className={LABEL_CLASS}>Тип проблемы</span>
                                            <select
                                                value={subcategory}
                                                onChange={(e) => setSubcategory(e.target.value)}
                                                className={FIELD_CLASS}
                                                disabled={subItems.length === 0}
                                            >
                                                <option value="">{subItems.length ? '— выберите тип —' : 'сначала выберите категорию'}</option>
                                                {subItems.map((it, i) => (
                                                    <option key={`${categoryName}-${i}`} value={it}>{it}</option>
                                                ))}
                                            </select>
                                        </label>
                                    </div>

                                    {categoryNote && (
                                        <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-800">
                                            <FaIcon className="fas fa-sparkles text-amber-500 shrink-0" style={{ width: '0.9em', height: '0.9em', marginTop: 1 }} />
                                            <span><b>ИИ скорректировал тему:</b> {categoryNote}</span>
                                        </div>
                                    )}

                                    <label className="mt-3 block">
                                        <span className={LABEL_CLASS}>Опишите проблему своими словами</span>
                                        <textarea
                                            value={description}
                                            onChange={(e) => setDescription(e.target.value)}
                                            rows={3}
                                            placeholder="Например: у оператора на РМ 12 со вчерашнего дня нет звука в Oktell, перезагрузка не помогла"
                                            className={FIELD_CLASS}
                                        />
                                    </label>

                                    <div className="mt-3 flex flex-wrap items-center gap-2">
                                        <button
                                            type="button"
                                            onClick={() => callAi('draft')}
                                            disabled={aiBusy}
                                            className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-white transition ${aiBusy ? 'bg-indigo-400' : 'bg-indigo-600 hover:bg-indigo-700'}`}
                                        >
                                            <FaIcon className={`fas ${aiMode === 'draft' ? 'fa-spinner fa-spin' : 'fa-sparkles'}`} style={{ width: '0.9em', height: '0.9em' }} />
                                            {aiMode === 'draft' ? 'ИИ думает…' : 'Сформировать с ИИ'}
                                        </button>
                                        <span className="text-[12px] text-slate-400">Подберёт поля и задаст вопросы, если чего-то не хватает</span>
                                    </div>
                                </div>

                                {/* AI-generated form fields (единый блок: и детали, и уточнения) */}
                                {aiFields.length > 0 && (
                                    <div className={CARD_CLASS}>
                                        <div className="flex items-center gap-2 text-sm font-bold text-slate-700">
                                            <FaIcon className="fas fa-list text-indigo-500" style={{ width: '1em', height: '1em' }} />
                                            Детали заявки
                                        </div>
                                        <p className="mt-1 text-[12px] text-slate-400">Заполните поля (особенно отмеченные *) — ИИ соберёт из них готовый тикет.</p>
                                        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                                            {aiFields.map((f) => (
                                                <DynamicField key={f.key} field={f} value={fieldValues[f.key]} onChange={setFieldValue} />
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {/* Continue CTA — собрать готовый текст из деталей */}
                                {aiFields.length > 0 && (
                                    <div className="rounded-2xl border border-indigo-200 bg-indigo-50/60 p-3">
                                        <button
                                            type="button"
                                            onClick={() => callAi('finalize')}
                                            disabled={aiBusy}
                                            className={`flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white transition ${aiBusy ? 'bg-indigo-400' : 'bg-indigo-600 hover:bg-indigo-700'}`}
                                        >
                                            <FaIcon className={`fas ${aiMode === 'finalize' ? 'fa-spinner fa-spin' : (composed ? 'fa-rotate' : 'fa-circle-check')}`} style={{ width: '0.95em', height: '0.95em' }} />
                                            {aiMode === 'finalize' ? 'Собираю заявку…' : (composed ? 'Пересобрать заявку с ИИ' : 'Продолжить — собрать заявку')}
                                        </button>
                                        <p className="mt-1.5 text-center text-[11px] text-indigo-500/80">
                                            ИИ учтёт ответы и детали и соберёт готовый текст справа
                                        </p>
                                    </div>
                                )}
                            </div>

                            {/* ── ПРАВАЯ КОЛОНКА — готовый тикет ── */}
                            <div className="space-y-4">
                                {/* Priority */}
                                <div className={CARD_CLASS}>
                                    <span className={LABEL_CLASS}>Приоритет</span>
                                    <div className="mt-2 flex flex-wrap gap-2">
                                        {PRIORITY_ORDER.map((p) => (
                                            <button
                                                key={p}
                                                type="button"
                                                onClick={() => setPriority(p)}
                                                className={`rounded-full border px-3 py-1 text-xs font-semibold transition ${priority === p ? PRIORITY_META[p].cls : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50'}`}
                                            >
                                                {PRIORITY_META[p].label}
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Preview / final text */}
                                <div className={CARD_CLASS}>
                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                        <span className={LABEL_CLASS}>
                                            Текст заявки {composed && <span className="text-emerald-600">· готово</span>}
                                        </span>
                                        <button
                                            type="button"
                                            onClick={() => callAi('finalize')}
                                            disabled={aiBusy}
                                            className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-semibold transition ${aiBusy ? 'border-slate-200 text-slate-400' : 'border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100'}`}
                                        >
                                            <FaIcon className={`fas ${aiMode === 'finalize' ? 'fa-spinner fa-spin' : 'fa-sparkles'}`} style={{ width: '0.8em', height: '0.8em' }} />
                                            {aiMode === 'finalize' ? 'Оформляю…' : 'Оформить с ИИ'}
                                        </button>
                                    </div>
                                    <textarea
                                        value={previewText}
                                        onChange={(e) => setPreviewText(e.target.value)}
                                        rows={14}
                                        placeholder="Здесь появится готовый текст заявки. Можно отредактировать вручную перед отправкой."
                                        className={FIELD_CLASS + ' font-mono text-[12.5px] leading-relaxed'}
                                    />
                                    <p className="mt-1 text-[11px] text-slate-400">Разметка Telegram: &lt;b&gt;жирный&lt;/b&gt;, &lt;i&gt;курсив&lt;/i&gt;, &lt;code&gt;код&lt;/code&gt;.</p>
                                </div>

                                {/* Channel (закрепляет админ / глава отдела) */}
                                <div className={CARD_CLASS}>
                                    <div className="flex items-center justify-between gap-2">
                                        <span className={LABEL_CLASS}>Канал · «{profileLabel(profile)}»</span>
                                        <button type="button" onClick={() => { loadChannels(); refreshMeta(); }} className="text-[12px] font-medium text-indigo-600 hover:text-indigo-700">
                                            <FaIcon className="fas fa-rotate" style={{ width: '0.8em', height: '0.8em' }} /> Обновить
                                        </button>
                                    </div>

                                    {channelReady ? (
                                        <div className="mt-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2">
                                            <div className="flex items-center gap-2 text-sm font-semibold text-emerald-800">
                                                <FaIcon className="fas fa-paper-plane" style={{ width: '0.9em', height: '0.9em' }} />
                                                {activePin.channel_title || 'Канал закреплён'}
                                            </div>
                                            <div className="mt-0.5 text-[11px] text-emerald-700/80">
                                                {activePin.pinned_by_name
                                                    ? `Закрепил: ${activePin.pinned_by_name}${activePin.pinned_at ? ` · ${activePin.pinned_at}` : ''}`
                                                    : 'Закреплено'}
                                            </div>
                                        </div>
                                    ) : activePin?.channel_id ? (
                                        <div className="mt-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-800">
                                            Закреплённый канал отключён. {canPin ? 'Выберите активный канал ниже.' : 'Обратитесь к админу или главе отдела.'}
                                        </div>
                                    ) : (
                                        <div className="mt-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-600">
                                            Канал ещё не закреплён. {canPin ? 'Выберите и закрепите канал ниже.' : 'Отправка недоступна — канал закрепляет админ или глава отдела.'}
                                        </div>
                                    )}

                                    {canPin && (
                                        <div className="mt-3 space-y-2">
                                            <span className={LABEL_CLASS}>Закрепить канал для этого профиля</span>
                                            <div className="flex flex-wrap gap-2">
                                                <select
                                                    value={pinDraftId}
                                                    onChange={(e) => setPinDraftId(e.target.value)}
                                                    className={FIELD_CLASS + ' mt-0 flex-1'}
                                                >
                                                    <option value="">— не закреплён —</option>
                                                    {activeChannels.map((c) => (
                                                        <option key={c.id} value={c.id}>{c.title || `Чат ${c.chat_id || c.id}`}</option>
                                                    ))}
                                                </select>
                                                <button
                                                    type="button"
                                                    onClick={pinChannel}
                                                    disabled={pinning || String(activePin?.channel_id || '') === String(pinDraftId || '')}
                                                    className={`shrink-0 inline-flex items-center gap-2 rounded-xl px-4 text-sm font-semibold text-white transition ${(pinning || String(activePin?.channel_id || '') === String(pinDraftId || '')) ? 'bg-indigo-300' : 'bg-indigo-600 hover:bg-indigo-700'}`}
                                                >
                                                    <FaIcon className={`fas ${pinning ? 'fa-spinner fa-spin' : 'fa-paperclip'}`} style={{ width: '0.85em', height: '0.85em' }} />
                                                    {pinning ? 'Сохранение…' : 'Закрепить'}
                                                </button>
                                            </div>
                                            {activeChannels.length === 0 && (
                                                <p className="text-[12px] text-rose-500">
                                                    Нет активных каналов. {canManage ? 'Добавьте канал ниже или добавьте бота в группу.' : 'Добавьте бота в нужную группу — он появится здесь автоматически.'}
                                                </p>
                                            )}
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* ── Конфигурация (админ / глава отдела) — на всю ширину ── */}
                            {(canManage || canEditInstructions || canEditCatalog) && (
                                <div className="space-y-4 lg:col-span-2">
                                    {canEditCatalog && catalogEditableProfiles.length > 0 && (
                                        <CatalogEditor
                                            apiBaseUrl={apiBaseUrl}
                                            buildHeaders={buildHeaders}
                                            notify={toast}
                                            catalog={catalog}
                                            editableProfiles={catalogEditableProfiles}
                                            onSaved={(c) => setCatalog(c)}
                                        />
                                    )}
                                    {canManage && (
                                        <ChannelManager
                                            apiBaseUrl={apiBaseUrl}
                                            buildHeaders={buildHeaders}
                                            notify={toast}
                                            channels={channels}
                                            onChannelsChanged={() => { loadChannels(); refreshMeta(); }}
                                        />
                                    )}
                                    {canEditInstructions && (
                                        <InstructionsEditor
                                            apiBaseUrl={apiBaseUrl}
                                            buildHeaders={buildHeaders}
                                            notify={toast}
                                        />
                                    )}
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Footer */}
                <div className="shrink-0 flex items-center justify-between gap-3 border-t border-slate-200 bg-white/80 px-6 py-4">
                    <span className="text-[11px] text-slate-400">
                        {channelReady
                            ? <>Заявка уйдёт в «{activePin?.channel_title || 'закреплённый канал'}»</>
                            : 'Канал не закреплён — отправка недоступна'}
                    </span>
                    <div className="flex items-center gap-3">
                        <button
                            type="button"
                            onClick={() => { if (!sending) onClose(); }}
                            className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
                        >
                            Отмена
                        </button>
                        <button
                            type="button"
                            onClick={handleSend}
                            disabled={sending || aiBusy || !channelReady}
                            title={!channelReady ? 'Канал закрепляет админ или глава отдела' : undefined}
                            className={`inline-flex items-center gap-2 rounded-xl px-5 py-2 text-sm font-semibold text-white shadow-sm transition ${(sending || !channelReady) ? 'bg-emerald-400 cursor-not-allowed' : 'bg-emerald-600 hover:bg-emerald-700'}`}
                        >
                            <FaIcon className={`fas ${sending ? 'fa-spinner fa-spin' : 'fa-paper-plane'}`} style={{ width: '0.9em', height: '0.9em' }} />
                            {sending ? 'Отправка…' : 'Отправить тикет'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default memo(ITTicketModal);
