import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { LayoutGrid, Loader2, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { absoluteFileUrl } from '../wiki/fileUrls';
import { frameStyle } from '../wiki/parkLogo';
import { channelInitials, loadChannelParks, matchChannelPark } from './chatChannelParks';

const storedCollapsed = (key) => {
    try { return localStorage.getItem(key) === '1'; } catch { return false; }
};

function ChannelLogo({ channel, park, base }) {
    const url = absoluteFileUrl(park?.logo_url, base);
    const [failedUrl, setFailedUrl] = useState(null);
    return (
        <span aria-hidden="true" className="relative grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-xl bg-slate-200/70 text-xs font-semibold text-slate-600">
            {url && url !== failedUrl
                ? <img src={url} alt="" width={40} height={40} loading="lazy" decoding="async"
                    draggable={false} onError={() => setFailedUrl(url)}
                    style={frameStyle(park.logo_frame)} className="h-full w-full object-cover" />
                : channelInitials(channel)}
        </span>
    );
}

export default function ChatChannelsSidebar({ channels = [], selectedChannelId = '', onSelect,
    apiBaseUrl, headers, user, account = 'op', loading = false }) {
    const identity = String(user?.id ?? user?.username ?? user?.login ?? 'session');
    const storageKey = `wazzup:channels-collapsed:${identity}:${account}`;
    const [collapseState, setCollapseState] = useState(() => ({ key: storageKey, value: storedCollapsed(storageKey) }));
    const collapsed = collapseState.key === storageKey ? collapseState.value : storedCollapsed(storageKey);
    const base = `${String(apiBaseUrl || '').replace(/\/$/, '')}/api/wiki`;
    const scope = `${base}:${identity}:${account}`;
    const [directory, setDirectory] = useState({ scope: '', parks: [] });
    const headersRef = useRef(headers);
    headersRef.current = headers;

    useEffect(() => {
        const controller = new AbortController();
        // Defer one turn so React StrictMode cleanup cancels its first pass.
        const timer = setTimeout(() => {
            loadChannelParks({ get: (...args) => axios.get(...args), base,
                headers: () => typeof headersRef.current === 'function' ? headersRef.current() : headersRef.current,
                signal: controller.signal })
                .then((parks) => { if (!controller.signal.aborted) setDirectory({ scope, parks }); })
                .catch(() => { if (!controller.signal.aborted) setDirectory({ scope, parks: [] }); });
        }, 0);
        return () => { clearTimeout(timer); controller.abort(); };
    }, [base, scope]);

    const parks = directory.scope === scope ? directory.parks : [];
    const rows = useMemo(() => (channels || []).map((channel) => ({
        channel, park: matchChannelPark(channel, parks),
    })), [channels, parks]);
    const toggle = () => {
        const value = !collapsed;
        setCollapseState({ key: storageKey, value });
        try { localStorage.setItem(storageKey, value ? '1' : '0'); } catch { /* Session-only preference. */ }
    };
    const toggleLabel = collapsed ? 'Развернуть каналы' : 'Свернуть каналы';
    const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose;
    const selectedClass = (selected) => selected
        ? 'bg-white font-semibold text-slate-900 shadow-sm ring-1 ring-slate-200/60'
        : 'text-slate-600 hover:bg-slate-100';

    return (
        <aside aria-label="Каналы чатов"
            className={`hidden shrink-0 flex-col border-r border-slate-100 bg-slate-50/70 md:flex ${collapsed ? 'w-[68px]' : 'w-56'}`}>
            <div className={`flex h-11 shrink-0 items-center px-2 ${collapsed ? 'justify-center' : 'justify-between pl-4'}`}>
                {!collapsed && <span className="text-xs font-semibold text-slate-500">Каналы</span>}
                <button type="button" onClick={toggle} title={toggleLabel} aria-label={toggleLabel}
                    aria-expanded={!collapsed} className="rounded-lg p-2 text-slate-500 transition hover:bg-slate-200/70 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400">
                    <ToggleIcon size={17} />
                </button>
            </div>
            <div className="wazzup-scrollbar flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-1.5 pb-2">
                <button type="button" onClick={() => onSelect('')} title="Все каналы" aria-label="Все каналы"
                    aria-pressed={!selectedChannelId}
                    className={`flex min-h-11 shrink-0 items-center rounded-lg text-[13px] transition ${collapsed ? 'justify-center px-1' : 'gap-2.5 px-3'} ${selectedClass(!selectedChannelId)}`}>
                    <LayoutGrid size={19} />{!collapsed && <span>Все каналы</span>}
                </button>
                {loading && <div role="status" aria-label="Загрузка каналов" className="flex items-center justify-center gap-2 py-3 text-xs text-slate-400">
                    <Loader2 size={14} className="animate-spin" />{!collapsed && 'Загрузка…'}
                </div>}
                {rows.map(({ channel, park }) => {
                    const name = channel.name || channel.plainId || channel.channelId;
                    const title = [name, channel.plainId, channel.chatsCount > 0 ? `Чатов: ${channel.chatsCount}` : ''].filter(Boolean).join(' · ');
                    const selected = selectedChannelId === channel.channelId;
                    return (
                        <button type="button" key={channel.channelId} onClick={() => onSelect(channel.channelId)}
                            title={title} aria-label={title} aria-pressed={selected}
                            className={`relative flex shrink-0 items-center rounded-xl py-2 text-left transition ${collapsed ? 'justify-center px-1' : 'gap-2.5 px-2'} ${selectedClass(selected)}`}>
                            <ChannelLogo channel={channel} park={park} base={base} />
                            {!collapsed && <span className="min-w-0 flex-1">
                                <span className="block truncate text-[13px] text-slate-800">{name}</span>
                                <span className="mt-0.5 flex items-center justify-between gap-1.5 text-[11px] text-slate-400">
                                    <span className="truncate">{channel.plainId || ''}</span>
                                    {channel.chatsCount > 0 && <span className="rounded-full bg-slate-200/80 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500">{channel.chatsCount}</span>}
                                </span>
                            </span>}
                        </button>
                    );
                })}
            </div>
        </aside>
    );
}
