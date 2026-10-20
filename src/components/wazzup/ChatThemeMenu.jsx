import React from 'react';
import { Palette } from 'lucide-react';
import { IosMenu, iosBtnGhost } from '../ui/ios';
import { CHAT_THEMES } from './chatThemes';

/* Выбор темы чатов в шапке раздела, рядом с «Обновить» (chatThemes.js).
 * Образец у пункта — маленькая переписка: подложка, чужой пузырь слева, свой
 * справа, теми же цветами, что красят окно. Список — темы этого человека
 * (общие и его личные); выбирать не из чего — кнопки нет. */
const swatchOf = (theme) => function ChatThemeSwatch() {
    return <span aria-hidden="true" className="relative h-[18px] w-[26px] shrink-0 overflow-hidden rounded-[5px] ring-1 ring-slate-900/10"
        style={{ background: theme.swatch || theme.wall }}>
        <span className="absolute left-[3px] top-[3px] h-[5px] w-[12px] rounded-[2px]" style={{ background: theme.incoming }} />
        <span className="absolute bottom-[3px] right-[3px] h-[5px] w-[12px] rounded-[2px]" style={{ background: theme.outgoing }} />
    </span>;
};
// Образец — компонент, и он обязан быть одним и тем же от рендера к рендеру.
const swatches = new WeakMap();
const swatchFor = (theme) => {
    if (!swatches.has(theme)) swatches.set(theme, swatchOf(theme));
    return swatches.get(theme);
};

export default function ChatThemeMenu({ selected, themes = CHAT_THEMES, onChoose }) {
    if (themes.length < 2) return null;
    return <IosMenu label="Тема чатов" trigger={{ icon: <Palette size={13} />, text: 'Тема', className: iosBtnGhost }}
        items={themes.map((theme) => ({
            key: theme.id, label: theme.name, icon: swatchFor(theme),
            checked: theme.id === selected, onSelect: () => onChoose(theme.id),
        }))} />;
}
