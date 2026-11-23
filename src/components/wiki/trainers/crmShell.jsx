import React, { useEffect, useRef, useState } from 'react';

import { CRM_BADGES, activeMenuItem, menuSearch } from './crmModel.js';
import { Notifications, noNav, normYo } from './crmKit';

/* Оболочка учебной CRM: шапка, меню, подвал — разметкой рабочей CRM
 * (CoreUI: app-header, sidebar, nav-dropdown, app-footer), снятой 20.11.2026.
 *
 * Меню целиком, даже разделы, которых в учебной среде нет: по меню оператор
 * ищет, куда идти. Поиск раздела — алгоритмом CRM (crmModel.filterMenu),
 * найденное подсвечено <mark class="sidebar-search-mark">, Enter открывает
 * первый найденный пункт, стрелки ходят по найденному.
 */

/* Подсветка найденного в названии — как mark.sidebar-search-mark в CRM. */
const Marked = ({ text, words }) => {
    if (!words.length) return text;
    const low = normYo(text);
    const ranges = [];
    for (const word of words) {
        if (!word) continue;
        for (let at = low.indexOf(word); at !== -1; at = low.indexOf(word, at + word.length)) {
            ranges.push([at, at + word.length]);
        }
    }
    if (!ranges.length) return text;
    ranges.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const range of ranges) {
        const last = merged[merged.length - 1];
        if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
        else merged.push([...range]);
    }
    const parts = [];
    let cursor = 0;
    merged.forEach(([from, to], index) => {
        parts.push(text.slice(cursor, from));
        parts.push(<mark key={index} className="sidebar-search-mark">{text.slice(from, to)}</mark>);
        cursor = to;
    });
    parts.push(text.slice(cursor));
    return parts;
};

const Sidebar = ({ ui }) => {
    const { world } = ui;
    const query = world.crmMenuQuery || '';
    const searching = Boolean(query.trim());
    const { groups, words } = menuSearch(query);
    const open = new Set(world.crmGroups || []);
    const active = activeMenuItem(world);
    const found = searching ? groups.flatMap((group) => group.items) : [];
    const [cursor, setCursor] = useState(0);
    useEffect(() => setCursor(0), [query]);

    const openItem = (item) => {
        const [key, , view] = item;
        ui.menu(key, view);
    };

    return (
        <div className={`sidebar${searching ? ' sidebar-searching' : ''}`}>
            <form className="sidebar-form sidebar-search" role="search" onSubmit={noNav}>
                <i className="fa fa-search sidebar-search__icon" aria-hidden="true" />
                <input
                    type="search"
                    id="sidebar-search"
                    className="form-control form-control-sm"
                    placeholder="Поиск раздела"
                    title="Поиск по разделам: / или Ctrl+K"
                    aria-label="Поиск по разделам"
                    autoComplete="off"
                    spellCheck="false"
                    value={query}
                    onChange={(event) => ui.set({ crmMenuQuery: event.target.value })}
                    onKeyDown={(event) => {
                        if (event.key === 'Escape') { event.preventDefault(); ui.set({ crmMenuQuery: '' }); }
                        if (!found.length) return;
                        if (event.key === 'ArrowDown') { event.preventDefault(); setCursor((c) => (c + 1) % found.length); }
                        if (event.key === 'ArrowUp') { event.preventDefault(); setCursor((c) => (c - 1 + found.length) % found.length); }
                        if (event.key === 'Enter') { event.preventDefault(); openItem(found[cursor] || found[0]); }
                    }}
                />
                <kbd className="sidebar-search__hint" aria-hidden="true">/</kbd>
            </form>
            <nav className="sidebar-nav">
                <ul className="nav">
                    <li className="nav-item sidebar-search-empty" hidden={!searching || groups.length > 0}>Ничего не найдено</li>
                    {groups.map((group) => {
                        const isOpen = searching || open.has(group.key);
                        return (
                            <li key={group.key} className={`nav-item nav-dropdown${isOpen ? ' open' : ''}`}>
                                <a
                                    /* Пункт, который ждёт урок, спрятан в закрытой
                                       группе — подсвечена группа: сначала её раскрыть. */
                                    className={`nav-link nav-dropdown-toggle${ui.t(`nav_group_${group.key}`,
                                        ...(isOpen ? [] : group.items.map(([key]) => `nav_${key}`)))}`}
                                    href="#"
                                    onClick={(event) => {
                                        noNav(event);
                                        ui.touch(`nav_group_${group.key}`, {
                                            crmGroups: open.has(group.key)
                                                ? [...open].filter((key) => key !== group.key)
                                                : [...open, group.key],
                                        });
                                    }}
                                >
                                    <i className={`nav-icon ${group.icon}`} /> <Marked text={group.title} words={words} />
                                </a>
                                <ul className="nav-dropdown-items">
                                    {group.items.map((item) => {
                                        const [key, title, , , , icon] = item;
                                        const badge = CRM_BADGES[key];
                                        const cursorHere = searching && found[cursor] === item;
                                        return (
                                            <li key={key} className="nav-item">
                                                <a
                                                    className={`nav-link${key === active ? ' active' : ''}`
                                                        + `${cursorHere ? ' sidebar-search-active' : ''}${ui.t(`nav_${key}`)}`}
                                                    href="#"
                                                    onClick={(event) => { noNav(event); openItem(item); }}
                                                >
                                                    <i className={`nav-icon ${icon}`} /> <Marked text={title} words={words} />
                                                    {badge && badge[0] === 'count' ? (
                                                        <span className="badge badge-info" title="Мои в работе + очередь отдела">{badge[1]}</span>
                                                    ) : null}
                                                    {badge && badge[0] === 'alert' ? (
                                                        <span className="notification-badge">{badge[1]}</span>
                                                    ) : null}
                                                </a>
                                            </li>
                                        );
                                    })}
                                </ul>
                            </li>
                        );
                    })}
                    <hr />
                </ul>
            </nav>
            <button
                className="sidebar-minimizer brand-minimizer"
                type="button"
                onClick={(event) => {
                    /* Свернуть меню — как в CoreUI: классы на body документа CRM. */
                    const { body } = event.currentTarget.ownerDocument;
                    body.classList.toggle('sidebar-minimized');
                    body.classList.toggle('brand-minimized');
                }}
            />
        </div>
    );
};

const Header = ({ ui }) => {
    const [open, setOpen] = useState(false);
    const ref = useRef(null);
    useEffect(() => {
        if (!open) return undefined;
        const doc = ref.current && ref.current.ownerDocument;
        const away = (event) => { if (ref.current && !ref.current.contains(event.target)) setOpen(false); };
        doc.addEventListener('mousedown', away);
        return () => doc.removeEventListener('mousedown', away);
    }, [open]);
    const item = (id, icon, text) => (
        <a className={`dropdown-item${ui.t(id)}`} href="#"
            onClick={(event) => { noNav(event); setOpen(false); ui.act(id, { crmView: 'stub', crmStub: id }); }}>
            <i className={`fa ${icon}`} /> {text}
        </a>
    );
    return (
        <header className="app-header navbar">
            <button className="navbar-toggler sidebar-toggler d-lg-none" type="button" data-toggle="sidebar-show">
                <span className="navbar-toggler-icon" />
            </button>
            <a className="navbar-brand" href="#" onClick={noNav}> </a>
            <a href="#" onClick={noNav}>iTaxi</a>
            <ul className="nav navbar-nav ml-auto">
                <li className={`nav-item dropdown${open ? ' show' : ''}`} ref={ref}>
                    <a className="dropdown-toggle nav-link" role="button" href="#"
                        onClick={(event) => { noNav(event); setOpen(!open); }}>
                        <span>
                            <span className="avatar-initials">СТ</span>
                            {' '}
                            <span className="hidden-md-down">Стажёр</span>
                        </span>
                        <span className="caret" />
                    </a>
                    <div className={`dropdown-menu dropdown-menu-right${open ? ' show' : ''}`}>
                        <div className="dropdown-header text-center"><strong>Профиль</strong></div>
                        {item('crm_profile', 'fa-user', 'Profile')}
                        {item('crm_password', 'fa-key', 'Password')}
                        {item('crm_logout', 'fa-lock', 'Выйти')}
                    </div>
                </li>
            </ul>
        </header>
    );
};

/* Уведомление гаснет само, как vue-notification с duration 2000. */
const useNoteTimer = (ui) => {
    const note = ui.world.crmNote;
    const set = useRef(ui.set);
    set.current = ui.set;
    useEffect(() => {
        if (!note) return undefined;
        const timer = setTimeout(() => set.current({ crmNote: null }), 2600);
        return () => clearTimeout(timer);
    }, [note]);
};

/** Учебная CRM: шапка, меню, страница, подвал. */
export default function CrmShell({ ui, children }) {
    useNoteTimer(ui);
    return (
        <>
            <Header ui={ui} />
            <div className="app-body">
                <Sidebar ui={ui} />
                <main className="main">
                    <div className="container-fluid" id="app">
                        <div className="modals" />
                        <div><Notifications note={ui.world.crmNote} /></div>
                        {children}
                    </div>
                </main>
            </div>
            <footer className="app-footer">
                <div className="container-fluid">
                    <div className="container-xl">
                        <span className="pull-right">Powered by <a href="#" onClick={noNav}>Craftable</a></span>
                    </div>
                </div>
            </footer>
            {/* Учебная среда: скриншот без плашки неотличим от рабочей CRM. */}
            <div className="wt-crm-mark">Учебная CRM</div>
        </>
    );
}
