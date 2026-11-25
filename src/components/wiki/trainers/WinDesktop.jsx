import React, { useEffect, useState } from 'react';

import { IcoreIcon } from './IcorePhone.jsx';

/* Рабочий стол Windows для учебного рабочего места.
 *
 * Владелец, 25.11.2026: «при запуске открывалось как среда Windows и на весь
 * экран, как обычный браузер CRM выводится, и звонилка наш iCORE Phone»;
 * «удобно и аккуратно, в стиле Windows». Значит, вид — Windows 11, как на
 * компьютере оператора: браузер развёрнут на весь экран, поверх — окно
 * iCORE Phone, внизу панель задач: «Пуск», поиск, браузер, iCORE Phone,
 * проводник, справа язык, сеть, звук и часы.
 *
 * Слева на панели — «Учебная смена»: меню тренажёра (новый звонок, выход).
 * Это место виджетов в Windows 11, и учебная пометка там не мешает работе.
 */

const two = (n) => String(n).padStart(2, '0');

/* Значки панели задач — свои, обобщённые: чужих логотипов в тренажёре нет. */
const StartIcon = () => (
    <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true">
        <defs>
            <linearGradient id="wt-start" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stopColor="#36a3ff" />
                <stop offset="1" stopColor="#0063c7" />
            </linearGradient>
        </defs>
        <rect x="1" y="1" width="8.4" height="8.4" rx="1" fill="url(#wt-start)" />
        <rect x="10.6" y="1" width="8.4" height="8.4" rx="1" fill="url(#wt-start)" />
        <rect x="1" y="10.6" width="8.4" height="8.4" rx="1" fill="url(#wt-start)" />
        <rect x="10.6" y="10.6" width="8.4" height="8.4" rx="1" fill="url(#wt-start)" />
    </svg>
);
const SearchIcon = () => (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#1b1b1b" strokeWidth="1.7"
        strokeLinecap="round" aria-hidden="true">
        <circle cx="10.5" cy="10.5" r="6" /><path d="m15 15 5 5" />
    </svg>
);
const BrowserIcon = () => (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <circle cx="12" cy="12" r="10" fill="#1a73e8" />
        <path d="M2.6 9h18.8M2.6 15h18.8M12 2c2.8 3 2.8 17 0 20M12 2c-2.8 3-2.8 17 0 20"
            fill="none" stroke="#fff" strokeWidth="1.3" />
    </svg>
);
const FolderIcon = () => (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path d="M2.5 6.5a2 2 0 0 1 2-2h5l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-15a2 2 0 0 1-2-2Z" fill="#f4b73f" />
        <path d="M2.5 9.5h19v8a2 2 0 0 1-2 2h-15a2 2 0 0 1-2-2Z" fill="#ffd25a" />
    </svg>
);
const TrainerIcon = () => (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M2 9 12 4l10 5-10 5Z" /><path d="M6 11v5c3 2 9 2 12 0v-5" /><path d="M22 9v6" />
    </svg>
);
const TrayIcon = ({ name }) => {
    const d = {
        wifi: 'M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01',
        sound: 'M4 9h4l5-4v14l-5-4H4Zm12-1a5 5 0 0 1 0 8m2.5-10.5a9 9 0 0 1 0 13',
        chevron: 'M6 14l6-6 6 6',
        bell: 'M6 16V11a6 6 0 0 1 12 0v5l2 2H4Zm4 3a2 2 0 0 0 4 0',
    }[name];
    return (
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={d} />
        </svg>
    );
};

/* Часы — настоящие, как в трее: оператор смотрит на них, сколько длится звонок. */
const useClock = () => {
    const [now, setNow] = useState(() => new Date());
    useEffect(() => {
        const id = setInterval(() => setNow(new Date()), 15000);
        return () => clearInterval(id);
    }, []);
    return now;
};

/** Уведомление Windows — справа внизу над панелью задач. */
export const WinToast = ({ app = 'iCORE Phone', icon = null, title, text, onClose }) => (
    <div className="wt-wintoast" role="status">
        <div className="wt-wintoast__head">
            <span className="wt-wintoast__app">{icon || <IcoreIcon size={14} />}{app}</span>
            {onClose ? (
                <button type="button" className="wt-wintoast__x" aria-label="Закрыть уведомление" onClick={onClose}>×</button>
            ) : null}
        </div>
        {title ? <b className="wt-wintoast__title">{title}</b> : null}
        {text ? <span className="wt-wintoast__text">{text}</span> : null}
    </div>
);

/**
 * Рабочий стол целиком.
 *
 * browser  — окно браузера (развёрнуто на весь экран);
 * phone    — окно iCORE Phone (или null, если свёрнуто);
 * ring     — окно «Входящий вызов» (или null);
 * phoneOpen / onPhoneToggle — значок iCORE Phone на панели задач;
 * ringing  — звонок идёт: значок мигает, если окно свёрнуто;
 * menu     — пункты меню «Учебная смена»: [{ label, onClick, danger }];
 * buddy    — Барс-помощник (WinBuddy) или null;
 * toast    — уведомление { title, text } или null; onToastClose.
 */
export default function WinDesktop({
    browser, phone = null, ring = null, phoneOpen = true, onPhoneToggle = null, ringing = false,
    menu = [], toast = null, onToastClose = null, buddy = null,
}) {
    const now = useClock();
    const [menuOpen, setMenuOpen] = useState(false);
    const flash = ringing && !phoneOpen;

    return (
        <div className="wt-windesk">
            <div className="wt-windesk__screen" onPointerDown={() => menuOpen && setMenuOpen(false)}>
                {browser}
                {phone}
                {ring}
                {buddy}
                {toast ? (
                    <WinToast title={toast.title} text={toast.text} app={toast.app}
                        icon={toast.app === 'Тренажёр' ? <TrainerIcon /> : null} onClose={onToastClose} />
                ) : null}
            </div>

            <div className="wt-taskbar" role="toolbar" aria-label="Панель задач">
                <div className="wt-taskbar__left">
                    <button type="button" className={`wt-taskbar__widget${menuOpen ? ' is-open' : ''}`}
                        aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((v) => !v)}>
                        <TrainerIcon />
                        <span>
                            <b>Учебная смена</b>
                            <small>тренажёр · звонки ИИ</small>
                        </span>
                    </button>
                    {menuOpen ? (
                        <div className="wt-winmenu" role="menu">
                            {menu.map((item) => (
                                <button key={item.label} type="button" role="menuitem"
                                    className={item.danger ? 'is-danger' : ''}
                                    onClick={() => { setMenuOpen(false); item.onClick(); }}>
                                    {item.label}
                                    {item.hint ? <small>{item.hint}</small> : null}
                                </button>
                            ))}
                        </div>
                    ) : null}
                </div>

                <div className="wt-taskbar__apps">
                    <span className="wt-taskbar__app" title="Пуск"><StartIcon /></span>
                    <span className="wt-taskbar__app" title="Поиск"><SearchIcon /></span>
                    <span className="wt-taskbar__app is-open is-active" title="Браузер — учебная CRM"><BrowserIcon /></span>
                    <button type="button"
                        className={`wt-taskbar__app is-open${phoneOpen ? ' is-active' : ''}${flash ? ' is-flash' : ''}`}
                        title="iCORE Phone" aria-pressed={phoneOpen} onClick={onPhoneToggle || undefined}>
                        <IcoreIcon size={22} />
                    </button>
                    <span className="wt-taskbar__app" title="Проводник"><FolderIcon /></span>
                </div>

                <div className="wt-taskbar__tray">
                    <span className="wt-taskbar__tray-btn" aria-hidden="true"><TrayIcon name="chevron" /></span>
                    <span className="wt-taskbar__lang">РУС</span>
                    <span className="wt-taskbar__tray-btn" aria-hidden="true">
                        <TrayIcon name="wifi" /><TrayIcon name="sound" />
                    </span>
                    <span className="wt-taskbar__clock">
                        <span>{two(now.getHours())}:{two(now.getMinutes())}</span>
                        <span>{two(now.getDate())}.{two(now.getMonth() + 1)}.{now.getFullYear()}</span>
                    </span>
                    <span className="wt-taskbar__tray-btn" aria-hidden="true"><TrayIcon name="bell" /></span>
                </div>
            </div>
        </div>
    );
}
