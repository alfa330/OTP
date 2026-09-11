import React, { useCallback, useEffect, useState } from 'react';
import FaIcon from './FaIcon';
import {
    MOBILE_SHELL_CLASS,
    TAB_BAR_SIDE,
    readMobileShell,
    subscribeMobileShell,
} from '../../utils/mobileShell';
import { startMobileScreenExit } from '../../utils/mobileScreenExit';
import './mobile-shell.css';
/* Моторика — ПОСЛЕ вёрстки оболочки: часть её правил перекрывает длительности
   и кривые из mobile-shell.css при равном весе селектора, и порядок в бандле
   здесь условие работы, а не привычка. */
import './mobile-motion.css';

/* Нижний бар разделов — навигация портала на телефоне.
 *
 * Четыре кнопки — верхние разделы меню (какие именно, решает App: у каждой роли
 * своё меню, и бар обязан показывать её собственный верх, а не общий список),
 * пятая — аватар: она открывает шторку со всеми разделами.
 *
 * ПОЧЕМУ КНОПКА, А НЕ ССЫЛКА. Разделы портала — это не маршруты роутера, а
 * состояние view внутри App; переход делает та же функция, что и в сайдбаре
 * (handleSidebarViewNavigation), поэтому бар ничего не знает про навигацию и
 * просто зовёт onSelect. Иначе у одного перехода стало бы два разных пути —
 * и разошлись бы они молча, на разделах со своими побочными действиями.
 */

/**
 * Состояние мобильной оболочки для разметки плюс класс на <body> для CSS.
 *
 * Класс ставится ЗДЕСЬ, а не отдельным эффектом в App: иначе разметка (бар уже
 * есть) и стили (места под бар ещё нет) могли бы разъехаться на один кадр —
 * на медленном телефоне это заметная дрожь при первом входе.
 */
export const useMobileShell = () => {
    const [state, setState] = useState(() => (
        typeof window === 'undefined'
            ? { shell: false, side: TAB_BAR_SIDE.BOTTOM }
            : readMobileShell(window)
    ));

    useEffect(() => {
        if (typeof window === 'undefined') return undefined;
        /* Читаем ещё раз на монтировании: первое значение считано при создании
           состояния, а между ним и монтированием окно могло уже повернуться. */
        setState(readMobileShell(window));
        return subscribeMobileShell(window, (next) => {
            setState((prev) => (
                prev.shell === next.shell && prev.side === next.side ? prev : next
            ));
        });
    }, []);

    useEffect(() => {
        const { body } = document;
        if (!body) return undefined;
        body.classList.toggle(MOBILE_SHELL_CLASS, state.shell);
        body.dataset.tabbarSide = state.shell ? state.side : '';
        return () => {
            body.classList.remove(MOBILE_SHELL_CLASS);
            delete body.dataset.tabbarSide;
        };
    }, [state.shell, state.side]);

    /* Проводы экранов — здесь же, где живёт признак «мы на телефоне»: окна
       портала уезжают вправо, а разметку на время ухода почти никто из них не
       держит (см. src/utils/mobileScreenExit.js). На компьютере наблюдателя
       нет вовсе — там окна не ездят. */
    useEffect(() => {
        if (!state.shell || typeof document === 'undefined') return undefined;
        return startMobileScreenExit(document);
    }, [state.shell]);

    return state;
};

const Badge = ({ count }) => {
    if (!count || count <= 0) return null;
    return (
        <span className="mtb-badge" aria-hidden="true">{count > 9 ? '9+' : count}</span>
    );
};

export default function MobileTabBar({
    items = [],
    activeView,
    onSelect,
    menuOpen = false,
    onToggleMenu,
    user,
    side = TAB_BAR_SIDE.BOTTOM,
}) {
    /* Аватар может не загрузиться (файл удалён, сеть отвалилась) — тогда
       показываем первую букву имени, как в сайдбаре. Ошибку помним по адресу:
       без этого смена аватара в профиле не показала бы новую картинку. */
    const [brokenAvatar, setBrokenAvatar] = useState(null);
    const avatarUrl = user?.avatar_url;
    const avatarBroken = Boolean(avatarUrl) && brokenAvatar === avatarUrl;
    const handleAvatarError = useCallback(() => setBrokenAvatar(avatarUrl), [avatarUrl]);

    const pick = useCallback((event, item) => {
        /* Тап по УЖЕ открытому разделу — не переход: в Telegram он возвращает
           к началу списка, у нас же повторный вызов раздела перезагружал бы
           его данные на ровном месте. Молча ничего не делаем. */
        if (item.view === activeView && !menuOpen) return;
        onSelect?.(event, item.view);
    }, [activeView, menuOpen, onSelect]);

    return (
        <nav className="mobile-tabbar" data-side={side} aria-label="Разделы портала">
            {items.map((item) => {
                const active = !menuOpen && item.view === activeView;
                return (
                    <button
                        key={item.view}
                        type="button"
                        onClick={(event) => pick(event, item)}
                        className={`mtb-item${active ? ' is-active' : ''}`}
                        aria-current={active ? 'page' : undefined}
                        aria-label={item.badge > 0 ? `${item.label}, новых: ${item.badge}` : item.label}
                    >
                        <span className="mtb-item__inner">
                            <span className="mtb-item__icon">
                                <FaIcon className={item.icon} />
                                <Badge count={item.badge} />
                            </span>
                            <span className="mtb-item__label">{item.label}</span>
                        </span>
                    </button>
                );
            })}
            <button
                type="button"
                onClick={onToggleMenu}
                className={`mtb-item mtb-item--profile${menuOpen ? ' is-active' : ''}`}
                aria-expanded={menuOpen}
                aria-label={menuOpen ? 'Закрыть список разделов' : 'Профиль и все разделы'}
            >
                <span className="mtb-item__inner">
                    <span className="mtb-item__icon mtb-item__avatar">
                        {avatarUrl && !avatarBroken ? (
                            <img src={avatarUrl} alt="" onError={handleAvatarError} />
                        ) : (
                            <span className="mtb-item__initial">{(user?.name || 'U').charAt(0).toUpperCase()}</span>
                        )}
                    </span>
                    <span className="mtb-item__label">{menuOpen ? 'Закрыть' : 'Ещё'}</span>
                </span>
            </button>
        </nav>
    );
}
