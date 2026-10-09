import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Slash, Smile, X, Pencil, Trash2 } from 'lucide-react';
import { prepareTemplate } from './chatTemplates';
import { loadChatEmojiPicker, scheduleEmojiWarmup, warmChatEmojiPicker } from './chatEmojiLoading';

function WhatsAppMark() {
    return <svg role="img" aria-label="Шаблон WhatsApp Business" viewBox="0 0 24 24"
        className="h-3.5 w-3.5 shrink-0 text-emerald-600" fill="currentColor">
        <path d="M20.52 3.48A11.88 11.88 0 0 0 12.04 0C5.43 0 .05 5.38.05 12c0 2.12.55 4.19 1.6 6.02L0 24l6.12-1.6a11.96 11.96 0 0 0 5.92 1.51h.01c6.61 0 11.99-5.38 11.99-12a11.92 11.92 0 0 0-3.52-8.43ZM12.04 21.9a9.94 9.94 0 0 1-5.06-1.38l-.36-.21-3.63.95.97-3.54-.24-.37A9.91 9.91 0 0 1 2.07 12c0-5.5 4.47-9.97 9.97-9.97a9.9 9.9 0 0 1 7.05 2.92A9.9 9.9 0 0 1 22 12c0 5.5-4.47 9.97-9.96 9.97Zm5.47-7.46c-.3-.15-1.77-.87-2.04-.97-.28-.1-.47-.15-.67.15-.2.3-.77.97-.95 1.17-.17.2-.35.22-.65.07-.3-.15-1.26-.46-2.4-1.48-.89-.79-1.49-1.77-1.66-2.07-.18-.3-.02-.46.13-.61.14-.14.3-.35.45-.52.15-.18.2-.3.3-.5.1-.2.05-.37-.03-.52-.07-.15-.67-1.62-.92-2.22-.24-.58-.49-.5-.67-.5h-.57c-.2 0-.52.07-.8.37-.27.3-1.04 1.02-1.04 2.49s1.07 2.89 1.22 3.09c.15.2 2.11 3.22 5.12 4.52.72.31 1.27.5 1.7.64.72.23 1.37.2 1.88.12.58-.09 1.77-.72 2.02-1.42.25-.7.25-1.3.17-1.42-.07-.12-.27-.2-.57-.35Z" />
    </svg>;
}

export default function ChatComposerTools({ apiBaseUrl, headers, channelId, locked, emojiDisabled, slash, onChoose, onEmoji, composerRef, composerKeyDownRef }) {
    const [panel, setPanel] = useState(null);
    const [items, setItems] = useState([]);
    const [warnings, setWarnings] = useState([]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [activeKey, setActiveKey] = useState(null);
    const [editing, setEditing] = useState(null);
    const [selected, setSelected] = useState(null);
    const [values, setValues] = useState({});
    const [deleting, setDeleting] = useState(null);
    const [EmojiPicker, setEmojiPicker] = useState(null);
    const [emojiOpened, setEmojiOpened] = useState(false);
    const [emojiError, setEmojiError] = useState(false);
    const [emojiAttempt, setEmojiAttempt] = useState(0);
    const rootRef = useRef(null);
    const templateButtonRef = useRef(null);
    const templateListRef = useRef(null);
    const hadSlash = useRef(false);
    const mutationRef = useRef(false);
    const dismissedSlash = useRef(null);
    const closePanel = (restoreFocus = false) => {
        if (mutationRef.current) return;
        dismissedSlash.current = slash;
        // Скрытие окна сохраняет набранный шаблон и значения переменных.
        setPanel(null); setDeleting(null);
        if (restoreFocus) (composerRef?.current || templateButtonRef.current)?.focus();
    };
    const requestHeaders = () => typeof headers === 'function' ? headers() : headers;
    const warmEmoji = () => { warmChatEmojiPicker().catch(() => {}); };
    useEffect(() => scheduleEmojiWarmup(warmChatEmojiPicker), []);
    useEffect(() => {
        if (panel !== 'emoji' || EmojiPicker) return undefined;
        let active = true;
        setEmojiError(false);
        loadChatEmojiPicker().then((module) => {
            if (active) setEmojiPicker(() => module.default);
        }).catch(() => { if (active) setEmojiError(true); });
        return () => { active = false; };
    }, [panel, EmojiPicker, emojiAttempt]);
    useEffect(() => {
        if (slash === null) {
            dismissedSlash.current = null;
            if (hadSlash.current && panel === 'templates') closePanel();
            hadSlash.current = false;
        } else {
            hadSlash.current = true;
            if (!locked && slash !== dismissedSlash.current) {
                setPanel('templates'); setSearch(slash); setActiveKey(null);
            }
        }
    }, [slash, locked]);
    useEffect(() => {
        if (!panel || typeof document === 'undefined') return undefined;
        const outside = (event) => {
            if (panel === 'templates' && event.target === composerRef?.current) return;
            if (!rootRef.current?.contains(event.target)) closePanel();
        };
        const escape = (event) => {
            if (event.key !== 'Escape') return;
            event.preventDefault(); event.stopPropagation(); closePanel(true);
        };
        document.addEventListener('pointerdown', outside);
        document.addEventListener('keydown', escape);
        return () => {
            document.removeEventListener('pointerdown', outside);
            document.removeEventListener('keydown', escape);
        };
    }, [panel, slash]);
    useEffect(() => {
        if (panel !== 'templates') return undefined;
        const controller = new AbortController();
        setBusy(true); setError(''); setWarnings([]);
        axios.get(`${apiBaseUrl}/api/wazzup/pilot/templates`, {
            headers: requestHeaders(), params: { account: 'op', channelId }, signal: controller.signal, timeout: 20000,
        }).then(({ data }) => { if (!controller.signal.aborted) { setItems(data.items || []); setWarnings(data.sourceWarnings || []); } })
            .catch((e) => { if (!controller.signal.aborted) setError(e.response?.data?.error || 'Не удалось загрузить шаблоны'); })
            .finally(() => { if (!controller.signal.aborted) setBusy(false); });
        return () => controller.abort();
    }, [panel, channelId, apiBaseUrl]);
    const choose = (item) => {
        if (busy || mutationRef.current) return;
        try {
            onChoose(prepareTemplate(item, values));
            setSelected(null); setEditing(null); setValues({}); closePanel(); setError('');
        }
        catch (e) { setError(e.message); }
    };
    const save = async () => {
        if (busy || mutationRef.current || !editing?.title.trim() || !editing?.text.trim()) return;
        mutationRef.current = true;
        setBusy(true); setError('');
        try {
            const payload = { account: 'op', title: editing.title, text: editing.text };
            const config = { headers: requestHeaders(), timeout: 15000 };
            const { data } = editing.id
                ? await axios.patch(`${apiBaseUrl}/api/wazzup/pilot/templates/${editing.id}`, payload, config)
                : await axios.post(`${apiBaseUrl}/api/wazzup/pilot/templates`, payload, config);
            setItems((prev) => [data.item, ...prev.filter((item) => item.id !== data.item.id)]);
            setEditing(null); setSearch('');
        } catch (e) { setError(e.response?.data?.error || 'Не удалось сохранить шаблон'); }
        finally { mutationRef.current = false; setBusy(false); }
    };
    const remove = async () => {
        if (busy || mutationRef.current || !deleting) return;
        mutationRef.current = true;
        setBusy(true); setError('');
        try {
            await axios.delete(`${apiBaseUrl}/api/wazzup/pilot/templates/${deleting.id}`, { headers: requestHeaders(), timeout: 15000 });
            setItems((prev) => prev.filter((item) => item.id !== deleting.id)); setDeleting(null);
        } catch (e) { setError(e.response?.data?.error || 'Не удалось удалить шаблон'); }
        finally { mutationRef.current = false; setBusy(false); }
    };
    const matchingTemplates = items.filter((item) => String(item.title || '').toLowerCase().includes(search.trim().toLowerCase()));
    const availableTemplates = matchingTemplates.filter((item) => item.supported);
    const templateKey = (item) => `${item.source}:${item.id}`;
    const activeTemplate = availableTemplates.find((item) => templateKey(item) === activeKey) || availableTemplates[0];
    const currentKey = activeTemplate ? templateKey(activeTemplate) : null;
    const selectTemplate = (item) => {
        if (busy || mutationRef.current || !item.supported) return;
        setValues({});
        if (item.variables.length) setSelected(item);
        else choose(item);
    };
    const navigateTemplates = (event) => {
        if (panel !== 'templates' || locked || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey
            || event.isComposing || event.nativeEvent?.isComposing || event.keyCode === 229 || event.nativeEvent?.keyCode === 229) return false;
        if (!['ArrowUp', 'ArrowDown', 'Enter', 'Escape'].includes(event.key)) return false;
        if ((editing || selected || deleting) && event.key.startsWith('Arrow')) return false;
        event.preventDefault(); event.stopPropagation();
        if (event.key === 'Escape') closePanel(true);
        else if (!busy && !editing && !selected && !deleting) {
            if (event.key === 'Enter') {
                if (activeTemplate) selectTemplate(activeTemplate);
            } else if (availableTemplates.length) {
                const index = availableTemplates.indexOf(activeTemplate);
                const next = (index + (event.key === 'ArrowDown' ? 1 : -1) + availableTemplates.length) % availableTemplates.length;
                setActiveKey(templateKey(availableTemplates[next]));
            }
        }
        return true;
    };
    useEffect(() => {
        if (!composerKeyDownRef) return undefined;
        composerKeyDownRef.current = navigateTemplates;
        return () => { composerKeyDownRef.current = null; };
    }, [composerKeyDownRef, navigateTemplates]);
    useEffect(() => {
        const list = templateListRef.current;
        const active = list?.querySelector('[data-template-active="true"]');
        if (!active) return;
        const row = active.parentElement;
        // Scroll just this list; scrollIntoView can also move the chat/page.
        const top = row.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop;
        if (top < list.scrollTop) list.scrollTop = top;
        else if (top + row.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = top + row.offsetHeight - list.clientHeight;
    }, [currentKey, panel, search, editing, selected, deleting]);
    return <div ref={rootRef} className="shrink-0">
        <div className="flex gap-0.5">
            <button ref={templateButtonRef} type="button" disabled={locked || mutationRef.current} aria-label="Шаблоны сообщений" title="Шаблоны сообщений · /" aria-expanded={panel === 'templates'}
                onClick={() => {
                    if (mutationRef.current) return;
                    if (panel === 'templates') closePanel(true);
                    else { setPanel('templates'); setSearch(slash || ''); setActiveKey(null); composerRef?.current?.focus(); }
                }}
                className="inline-flex h-10 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 disabled:opacity-40">
                <Slash size={18} /></button>
            <button type="button" disabled={locked || emojiDisabled || mutationRef.current} aria-label="Выбрать эмодзи" title="Выбрать эмодзи" aria-expanded={panel === 'emoji'}
                onPointerEnter={warmEmoji} onFocus={warmEmoji} onPointerDown={warmEmoji}
                onClick={() => { if (mutationRef.current) return; dismissedSlash.current = slash; setEmojiOpened(true); setPanel(panel === 'emoji' ? null : 'emoji'); }}
                className="inline-flex h-10 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 disabled:opacity-40"><Smile size={19} /></button>
        </div>
        {emojiOpened && <div role="dialog" aria-label="Эмодзи" hidden={panel !== 'emoji' || locked || emojiDisabled}
            onKeyDown={(e) => {
                if (e.key === 'Escape') { e.stopPropagation(); closePanel(true); }
                if (e.key === 'Enter') { e.stopPropagation(); if (e.target.tagName === 'INPUT') e.preventDefault(); }
            }}
            className="absolute bottom-full left-0 z-20 mb-2 w-full max-w-md rounded-xl border border-slate-200 bg-white p-3 shadow-xl">
            <div className="mb-2 flex items-center justify-between text-sm font-semibold">
                <span>Эмодзи</span>
                <button type="button" aria-label="Закрыть эмодзи" onClick={() => closePanel(true)}><X size={17} /></button>
            </div>
            {EmojiPicker ? <EmojiPicker onSelect={onEmoji} /> : emojiError
                ? <button type="button" onClick={() => setEmojiAttempt((value) => value + 1)} className="text-xs text-blue-600">Повторить загрузку эмодзи</button>
                : <p className="h-[340px] text-xs text-slate-500" role="status">Загрузка эмодзи…</p>}
        </div>}
        {panel === 'templates' && !locked && <div role="dialog" aria-label="Шаблоны сообщений"
            onKeyDown={(e) => {
                if (e.key === 'Escape') { e.stopPropagation(); closePanel(true); }
                // Template editing/variable fields must not submit the message form.
                if (e.key === 'Enter') { e.stopPropagation(); if (e.target.tagName === 'INPUT') e.preventDefault(); }
            }}
            className="absolute bottom-full left-0 z-20 mb-2 flex max-h-[min(420px,60dvh)] w-full max-w-[460px] flex-col overflow-hidden rounded-lg bg-white shadow-[0_3px_18px_rgba(15,23,42,0.18)]">
            <div className="flex shrink-0 items-center px-4 py-3">
                <button type="button" disabled={busy || Boolean(editing) || Boolean(selected)}
                    onClick={() => { setEditing({ title: '', text: '' }); setDeleting(null); setError(''); }}
                    className="rounded px-1 py-0.5 text-sm font-medium text-blue-600 hover:bg-blue-50 disabled:opacity-40">+ iCORE</button>
            </div>
            <div className="flex min-h-0 flex-col">
                {error && <p role="alert" className="px-4 pb-2 text-xs text-rose-600">{error}</p>}
                {warnings.map((warning) => <p key={warning} role="status" className="px-4 pb-2 text-xs text-amber-700">{warning}</p>)}
                {editing ? <div className="space-y-2 overflow-y-auto px-4 pb-4">
                    <input autoFocus aria-label="Название шаблона" disabled={busy} value={editing.title} maxLength={100} placeholder="Название"
                        onChange={(e) => setEditing({ ...editing, title: e.target.value })} className="w-full rounded border p-2 text-sm" />
                    <textarea aria-label="Текст шаблона" disabled={busy} value={editing.text} maxLength={4096} rows={6} placeholder="Текст ответа"
                        onChange={(e) => setEditing({ ...editing, text: e.target.value })} className="w-full rounded border p-2 text-sm" />
                    <p className="text-xs text-slate-500">Общий шаблон iCORE для команды. В Wazzup он не добавляется.</p>
                    <button type="button" disabled={busy || !editing.title.trim() || !editing.text.trim()} onClick={save}
                        className="mr-3 text-sm font-semibold text-blue-600 disabled:opacity-40">Сохранить</button>
                    <button type="button" disabled={busy} onClick={() => setEditing(null)} className="text-sm">Отмена</button>
                </div> : selected ? <div className="space-y-2 overflow-y-auto px-4 pb-4">
                    <p className="text-sm font-medium">{selected.title}</p>
                    <p className="max-h-36 overflow-auto whitespace-pre-wrap text-xs text-slate-600">{selected.text}</p>
                    {selected.variables.map((key, i) => <input key={key} autoFocus={i === 0} aria-label={`Переменная ${i+1}`}
                        placeholder={`Переменная ${i+1}`} value={values[key] || ''} maxLength={500}
                        onChange={(e) => setValues({ ...values, [key]: e.target.value })} className="w-full rounded border p-2 text-sm" />)}
                    <button type="button" onClick={() => choose(selected)} className="mr-3 text-sm font-semibold text-blue-600">Вставить</button>
                    <button type="button" onClick={() => setSelected(null)} className="text-sm">Назад</button>
                </div> : <>
                    {busy && <p role="status" className="px-4 py-2 text-sm text-slate-500">Загрузка…</p>}
                    <div ref={templateListRef} className="wazzup-thin-scrollbar relative min-h-0 overflow-y-auto overscroll-contain pb-1">
                        {matchingTemplates.map((item) =>
                            <div key={templateKey(item)} className={`group flex items-stretch ${currentKey === templateKey(item) ? 'bg-slate-100' : 'hover:bg-slate-50'}`}>
                                <button type="button" disabled={busy || !item.supported} title={item.unsupportedReason || item.text}
                                    data-template-key={templateKey(item)} data-template-active={currentKey === templateKey(item)}
                                    aria-current={currentKey === templateKey(item) ? 'true' : undefined}
                                    onPointerMove={() => { if (item.supported) setActiveKey(templateKey(item)); }}
                                    onClick={() => selectTemplate(item)}
                                    className="min-w-0 flex-1 px-4 py-2.5 text-left outline-none focus-visible:bg-blue-50 disabled:opacity-50">
                                    <span className="flex items-center gap-2 text-[15px] leading-6 text-slate-900">
                                        <span className="truncate">{item.title}</span>
                                        {item.kind === 'waba' && <WhatsAppMark />}
                                    </span>
                                    <span className="mt-1 block truncate text-sm leading-5 text-slate-500">{item.text}</span>
                                </button>
                                {item.source === 'icore' && <>
                                    <button type="button" disabled={busy} aria-label={`Изменить ${item.title}`} onClick={() => setEditing(item)} className="px-2 text-slate-400 hover:text-blue-600"><Pencil size={15} /></button>
                                    <button type="button" disabled={busy} aria-label={`Удалить ${item.title}`} onClick={() => setDeleting(item)} className="pr-3 pl-1 text-slate-400 hover:text-rose-600"><Trash2 size={15} /></button>
                                </>}
                            </div>)}
                        {!busy && !items.length && <p className="px-4 py-3 text-sm text-slate-500">Для этого канала шаблонов пока нет.</p>}
                        {!busy && items.length > 0 && !matchingTemplates.length && <p className="px-4 py-3 text-sm text-slate-500">Шаблоны с таким названием не найдены.</p>}
                    </div>
                    {deleting && <div className="mt-2 rounded bg-rose-50 p-2 text-xs">
                        Удалить «{deleting.title}» для всей команды?
                        <button type="button" disabled={busy} onClick={remove} className="ml-2 font-semibold text-rose-700">Удалить</button>
                        <button type="button" disabled={busy} onClick={() => setDeleting(null)} className="ml-2">Отмена</button>
                    </div>}
                </>}
            </div>
        </div>}
    </div>;
}
