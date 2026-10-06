import React, { lazy, Suspense, useEffect, useState } from 'react';
import axios from 'axios';
import { Plus, Smile, X, Pencil, Trash2 } from 'lucide-react';
import { prepareTemplate } from './chatTemplates';

const EmojiPicker = lazy(() => import('./ChatEmojiPicker'));

export default function ChatComposerTools({ apiBaseUrl, headers, channelId, locked, slash, onChoose, onEmoji }) {
    const [panel, setPanel] = useState(null);
    const [items, setItems] = useState([]);
    const [warnings, setWarnings] = useState([]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [editing, setEditing] = useState(null);
    const [selected, setSelected] = useState(null);
    const [values, setValues] = useState({});
    const [deleting, setDeleting] = useState(null);
    const requestHeaders = () => typeof headers === 'function' ? headers() : headers;
    useEffect(() => { if (slash !== null && !locked) { setPanel('templates'); setSearch(slash); } }, [slash, locked]);
    useEffect(() => {
        if (panel !== 'templates') return undefined;
        const controller = new AbortController();
        setBusy(true); setError('');
        axios.get(`${apiBaseUrl}/api/wazzup/pilot/templates`, {
            headers: requestHeaders(), params: { account: 'op', channelId }, signal: controller.signal, timeout: 20000,
        }).then(({ data }) => { setItems(data.items || []); setWarnings(data.sourceWarnings || []); })
            .catch((e) => { if (!controller.signal.aborted) setError(e.response?.data?.error || 'Не удалось загрузить шаблоны'); })
            .finally(() => { if (!controller.signal.aborted) setBusy(false); });
        return () => controller.abort();
    }, [panel, channelId, apiBaseUrl]);
    const choose = (item) => {
        try { onChoose(prepareTemplate(item, values)); setPanel(null); setSelected(null); setError(''); }
        catch (e) { setError(e.message); }
    };
    const save = async () => {
        if (busy || !editing?.title.trim() || !editing?.text.trim()) return;
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
        finally { setBusy(false); }
    };
    const remove = async () => {
        if (busy || !deleting) return;
        setBusy(true); setError('');
        try {
            await axios.delete(`${apiBaseUrl}/api/wazzup/pilot/templates/${deleting.id}`, { headers: requestHeaders(), timeout: 15000 });
            setItems((prev) => prev.filter((item) => item.id !== deleting.id)); setDeleting(null);
        } catch (e) { setError(e.response?.data?.error || 'Не удалось удалить шаблон'); }
        finally { setBusy(false); }
    };
    return <div className="relative mb-2">
        <div className="flex gap-2">
            <button type="button" disabled={locked} aria-expanded={panel === 'templates'}
                onClick={() => { setPanel(panel === 'templates' ? null : 'templates'); setSearch(''); }}
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-40">
                <Plus size={17} /> Шаблоны · /</button>
            <button type="button" disabled={locked} aria-label="Выбрать эмодзи" aria-expanded={panel === 'emoji'}
                onClick={() => setPanel(panel === 'emoji' ? null : 'emoji')}
                className="rounded-lg px-2 py-1 text-slate-600 hover:bg-slate-100 disabled:opacity-40"><Smile size={17} /></button>
        </div>
        {panel && !locked && <div role="dialog" aria-label={panel === 'emoji' ? 'Эмодзи' : 'Шаблоны сообщений'}
            onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setPanel(null); } }}
            className="absolute bottom-full left-0 z-20 mb-2 w-full max-w-md rounded-xl border border-slate-200 bg-white p-3 shadow-xl">
            <div className="mb-2 flex items-center justify-between text-sm font-semibold">
                <span>{panel === 'emoji' ? 'Эмодзи' : 'Шаблоны сообщений'}</span>
                <button type="button" aria-label="Закрыть" onClick={() => setPanel(null)}><X size={17} /></button>
            </div>
            {panel === 'emoji' ? <Suspense fallback={<p className="text-xs text-slate-500">Загрузка эмодзи…</p>}>
                <EmojiPicker onSelect={onEmoji} /></Suspense> : <>
                {error && <p role="alert" className="mb-2 text-xs text-rose-600">{error}</p>}
                {editing ? <div className="space-y-2">
                    <input aria-label="Название шаблона" value={editing.title} maxLength={100} placeholder="Название"
                        onChange={(e) => setEditing({ ...editing, title: e.target.value })} className="w-full rounded border p-2 text-sm" />
                    <textarea aria-label="Текст шаблона" value={editing.text} maxLength={4096} rows={6} placeholder="Текст ответа"
                        onChange={(e) => setEditing({ ...editing, text: e.target.value })} className="w-full rounded border p-2 text-sm" />
                    <p className="text-xs text-slate-500">Общий шаблон iCORE для команды. В Wazzup он не добавляется.</p>
                    <button type="button" disabled={busy || !editing.title.trim() || !editing.text.trim()} onClick={save}
                        className="mr-3 text-sm font-semibold text-blue-600 disabled:opacity-40">Сохранить</button>
                    <button type="button" disabled={busy} onClick={() => setEditing(null)} className="text-sm">Отмена</button>
                </div> : selected ? <div className="space-y-2">
                    <p className="text-sm font-medium">{selected.title}</p>
                    <p className="max-h-36 overflow-auto whitespace-pre-wrap text-xs text-slate-600">{selected.text}</p>
                    {selected.variables.map((key, i) => <input key={key} aria-label={`Переменная ${i+1}`}
                        placeholder={`Переменная ${i+1}`} value={values[key] || ''} maxLength={500}
                        onChange={(e) => setValues({ ...values, [key]: e.target.value })} className="w-full rounded border p-2 text-sm" />)}
                    <button type="button" onClick={() => choose(selected)} className="mr-3 text-sm font-semibold text-blue-600">Вставить</button>
                    <button type="button" onClick={() => setSelected(null)} className="text-sm">Назад</button>
                </div> : <>
                    <div className="mb-2 flex gap-2">
                        <input aria-label="Найти шаблон" placeholder="Найти шаблон…" value={search} onChange={(e) => setSearch(e.target.value)}
                            className="min-w-0 flex-1 rounded-lg border p-2 text-xs" />
                        <button type="button" disabled={busy} onClick={() => { setEditing({ title: '', text: '' }); setError(''); }}
                            className="text-xs font-semibold text-blue-600">+ iCORE</button>
                    </div>
                    {busy && <p className="text-xs text-slate-500">Загрузка…</p>}
                    <div className="max-h-60 overflow-y-auto">
                        {items.filter((item) => `${item.title} ${item.text}`.toLowerCase().includes(search.toLowerCase())).map((item) =>
                            <div key={`${item.source}:${item.id}`} className="flex gap-1 border-b border-slate-100 py-1">
                                <button type="button" disabled={!item.supported} title={item.unsupportedReason || item.text}
                                    onClick={() => { setValues({}); item.variables.length ? setSelected(item) : choose(item); }}
                                    className="min-w-0 flex-1 rounded p-1 text-left hover:bg-slate-50 disabled:opacity-50">
                                    <span className="block truncate text-xs font-semibold">{item.title} <span className="font-normal text-slate-400">{item.source === 'icore' ? 'iCORE' : 'Wazzup'}</span></span>
                                    <span className="block truncate text-xs text-slate-500">{item.text}</span>
                                </button>
                                {item.source === 'icore' && <>
                                    <button type="button" disabled={busy} aria-label={`Изменить ${item.title}`} onClick={() => setEditing(item)} className="px-1 text-slate-400"><Pencil size={13} /></button>
                                    <button type="button" disabled={busy} aria-label={`Удалить ${item.title}`} onClick={() => setDeleting(item)} className="px-1 text-slate-400"><Trash2 size={13} /></button>
                                </>}
                            </div>)}
                        {!busy && !items.length && <p className="py-3 text-xs text-slate-500">Для этого канала шаблонов пока нет.</p>}
                    </div>
                    {deleting && <div className="mt-2 rounded bg-rose-50 p-2 text-xs">
                        Удалить «{deleting.title}» для всей команды?
                        <button type="button" disabled={busy} onClick={remove} className="ml-2 font-semibold text-rose-700">Удалить</button>
                        <button type="button" disabled={busy} onClick={() => setDeleting(null)} className="ml-2">Отмена</button>
                    </div>}
                    {warnings.length > 0 && <details className="mt-2 text-[11px] text-slate-500"><summary>Доступность шаблонов</summary>
                        {warnings.map((warning) => <p key={warning} className="mt-1">{warning}</p>)}</details>}
                </>}
            </>}
        </div>}
    </div>;
}
