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

// ─── Main modal ─────────────────────────────────────────────────────────────
const ITTicketModal = ({ isOpen, onClose, apiBaseUrl, buildHeaders, notify, canManageChannels }) => {
    const [catalog, setCatalog] = useState(null);
    const [defaultProfile, setDefaultProfile] = useState('op');
    const [profile, setProfile] = useState('op');
    const [categoryName, setCategoryName] = useState('');
    const [subcategory, setSubcategory] = useState('');
    const [description, setDescription] = useState('');

    const [aiFields, setAiFields] = useState([]);
    const [fieldValues, setFieldValues] = useState({});
    const [questions, setQuestions] = useState([]);
    const [answers, setAnswers] = useState({});
    const [priority, setPriority] = useState('medium');
    const [previewText, setPreviewText] = useState('');
    const [ticketTitle, setTicketTitle] = useState('');
    const [composed, setComposed] = useState(false);

    const [channels, setChannels] = useState([]);
    const [channelId, setChannelId] = useState('');
    const [canManage, setCanManage] = useState(Boolean(canManageChannels));

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
            } catch (err) {
                if (alive) toast(err?.response?.data?.error || 'Не удалось загрузить каталог', 'error');
            } finally {
                if (alive) setLoadingCatalog(false);
            }
        })();
        loadChannels();
        return () => { alive = false; };
    }, [isOpen, apiBaseUrl, buildHeaders, toast, loadChannels]);

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
        setAiFields([]); setFieldValues({}); setQuestions([]); setAnswers({});
        setComposed(false); setPreviewText(''); setTicketTitle('');
    }, []);

    const activeChannels = useMemo(() => channels.filter((c) => c.is_active), [channels]);

    const setFieldValue = useCallback((key, value) => {
        setFieldValues((prev) => ({ ...prev, [key]: value }));
    }, []);
    const setAnswer = useCallback((key, value) => {
        setAnswers((prev) => ({ ...prev, [key]: value }));
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
                answers,
            }, { headers: buildHeaders() });

            if (res?.data?.status !== 'success') {
                toast('ИИ не смог обработать запрос', 'error');
                return;
            }
            const result = res.data.result || {};
            if (result.category && !categoryName) setCategoryName(result.category);
            if (result.subcategory && !subcategory) setSubcategory(result.subcategory);
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
            setQuestions(Array.isArray(result?.questions) ? result.questions : []);

            const ticketMd = result?.ticket?.markdown || result?.ticket?.summary || '';
            if (result?.ticket?.title) setTicketTitle(result.ticket.title);

            if (result.status === 'ready') {
                if (ticketMd) setPreviewText(ticketMd);
                setComposed(true);
                toast('Заявка готова — проверьте текст и отправьте', 'success');
            } else if (result.status === 'need_more_info') {
                setComposed(false);
                toast('ИИ задал уточняющие вопросы — заполните их', 'info');
            } else {
                // draft
                if (ticketMd && !previewText.trim()) setPreviewText(ticketMd);
                toast('Черновик сформирован — заполните поля и оформите заявку', 'success');
            }
        } catch (err) {
            toast(err?.response?.data?.error || 'Ошибка обращения к ИИ', 'error');
        } finally {
            setAiMode(null);
        }
    }, [apiBaseUrl, buildHeaders, profile, categoryName, subcategory, description, fieldValues, answers, previewText, toast]);

    // ── send ──
    const handleSend = useCallback(async () => {
        const body = (previewText || '').trim() || description.trim();
        if (!body) { toast('Пустой текст заявки. Опишите проблему или оформите её с ИИ', 'error'); return; }
        if (!channelId) { toast('Выберите канал для отправки', 'error'); return; }
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
                channel_id: Number(channelId),
            }, { headers: buildHeaders() });
            toast(`Заявка отправлена${res?.data?.channel_title ? ` в «${res.data.channel_title}»` : ''}`, 'success');
            onClose();
        } catch (err) {
            toast(err?.response?.data?.error || 'Не удалось отправить заявку', 'error');
        } finally {
            setSending(false);
        }
    }, [apiBaseUrl, buildHeaders, previewText, description, channelId, profile, categoryName, subcategory, priority, ticketTitle, fieldValues, toast, onClose]);

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
                className="flex w-full max-w-3xl flex-col overflow-hidden rounded-3xl border border-white/60 bg-slate-50 shadow-2xl"
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
                <div className="flex-1 space-y-4 overflow-y-auto p-5">
                    {loadingCatalog ? (
                        <div className="flex items-center gap-2 p-6 text-sm text-slate-500">
                            <FaIcon className="fas fa-spinner fa-spin text-indigo-500" style={{ width: '1em', height: '1em' }} />
                            Загрузка каталога…
                        </div>
                    ) : (
                        <>
                            {/* Profile + category */}
                            <div className={CARD_CLASS}>
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <span className={LABEL_CLASS}>Профиль (определён по отделу)</span>
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
                                    <span className="text-[12px] text-slate-400">ИИ подберёт поля и задаст вопросы, если чего-то не хватает</span>
                                </div>
                            </div>

                            {/* AI clarifying questions */}
                            {questions.length > 0 && (
                                <div className="rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
                                    <div className="flex items-center gap-2 text-sm font-bold text-amber-800">
                                        <FaIcon className="fas fa-circle-question" style={{ width: '1em', height: '1em' }} />
                                        Уточняющие вопросы от ИИ
                                    </div>
                                    <div className="mt-3 space-y-3">
                                        {questions.map((q, i) => (
                                            <label key={q.id || `q-${i}`} className="block">
                                                <span className="text-[13px] font-medium text-amber-900">{q.question}</span>
                                                {q.why && <span className="block text-[11px] text-amber-700/70">{q.why}</span>}
                                                <input
                                                    type="text"
                                                    value={answers[q.id || `q-${i}`] ?? ''}
                                                    onChange={(e) => setAnswer(q.id || `q-${i}`, e.target.value)}
                                                    className={FIELD_CLASS}
                                                    placeholder="Ваш ответ"
                                                />
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* AI-generated form fields */}
                            {aiFields.length > 0 && (
                                <div className={CARD_CLASS}>
                                    <div className="flex items-center gap-2 text-sm font-bold text-slate-700">
                                        <FaIcon className="fas fa-list text-indigo-500" style={{ width: '1em', height: '1em' }} />
                                        Детали заявки
                                    </div>
                                    <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                                        {aiFields.map((f) => (
                                            <DynamicField key={f.key} field={f} value={fieldValues[f.key]} onChange={setFieldValue} />
                                        ))}
                                    </div>
                                </div>
                            )}

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
                                    <span className={LABEL_CLASS}>Текст заявки {composed && <span className="text-emerald-600">· готово</span>}</span>
                                    <button
                                        type="button"
                                        onClick={() => callAi('finalize')}
                                        disabled={aiBusy}
                                        className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${aiBusy ? 'border-slate-200 text-slate-400' : 'border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100'}`}
                                    >
                                        <FaIcon className={`fas ${aiMode === 'finalize' ? 'fa-spinner fa-spin' : 'fa-circle-check'}`} style={{ width: '0.85em', height: '0.85em' }} />
                                        {aiMode === 'finalize' ? 'Оформляю…' : 'Проверить и оформить с ИИ'}
                                    </button>
                                </div>
                                <textarea
                                    value={previewText}
                                    onChange={(e) => setPreviewText(e.target.value)}
                                    rows={8}
                                    placeholder="Здесь появится готовый текст заявки. Можно отредактировать вручную перед отправкой."
                                    className={FIELD_CLASS + ' font-mono text-[12.5px] leading-relaxed'}
                                />
                                <p className="mt-1 text-[11px] text-slate-400">Допустима простая разметка Telegram: &lt;b&gt;жирный&lt;/b&gt;, &lt;i&gt;курсив&lt;/i&gt;, &lt;code&gt;код&lt;/code&gt;.</p>
                            </div>

                            {/* Channel */}
                            <div className={CARD_CLASS}>
                                <div className="flex items-center justify-between gap-2">
                                    <span className={LABEL_CLASS}>Канал для отправки</span>
                                    <button type="button" onClick={loadChannels} className="text-[12px] font-medium text-indigo-600 hover:text-indigo-700">
                                        <FaIcon className="fas fa-rotate" style={{ width: '0.8em', height: '0.8em' }} /> Обновить
                                    </button>
                                </div>
                                <select
                                    value={channelId}
                                    onChange={(e) => setChannelId(e.target.value)}
                                    className={FIELD_CLASS}
                                >
                                    <option value="">— выберите канал —</option>
                                    {activeChannels.map((c) => (
                                        <option key={c.id} value={c.id}>{c.title || `Чат ${c.chat_id || c.id}`}</option>
                                    ))}
                                </select>
                                {activeChannels.length === 0 && (
                                    <p className="mt-1 text-[12px] text-rose-500">
                                        Нет доступных каналов. {canManage ? 'Добавьте канал ниже или добавьте бота в группу.' : 'Обратитесь к администратору.'}
                                    </p>
                                )}

                                {canManage && (
                                    <div className="mt-3">
                                        <ChannelManager
                                            apiBaseUrl={apiBaseUrl}
                                            buildHeaders={buildHeaders}
                                            notify={toast}
                                            channels={channels}
                                            onChannelsChanged={loadChannels}
                                        />
                                    </div>
                                )}
                            </div>
                        </>
                    )}
                </div>

                {/* Footer */}
                <div className="shrink-0 flex items-center justify-end gap-3 border-t border-slate-200 bg-white/80 px-6 py-4">
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
                        disabled={sending || aiBusy}
                        className={`inline-flex items-center gap-2 rounded-xl px-5 py-2 text-sm font-semibold text-white shadow-sm transition ${sending ? 'bg-emerald-400' : 'bg-emerald-600 hover:bg-emerald-700'}`}
                    >
                        <FaIcon className={`fas ${sending ? 'fa-spinner fa-spin' : 'fa-paper-plane'}`} style={{ width: '0.9em', height: '0.9em' }} />
                        {sending ? 'Отправка…' : 'Отправить тикет'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default memo(ITTicketModal);
