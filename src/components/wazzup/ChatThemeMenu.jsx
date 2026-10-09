import React from 'react';
import { Palette } from 'lucide-react';
import { IosMenu, iosBtnGhost } from '../ui/ios';
import { CHAT_THEMES } from './chatThemes';

/* Выбор темы чатов в шапке раздела, рядом с «Обновить» (chatThemes.js).
 * Образец у пункта — маленькая переписка: подложка, чужой пузырь слева, свой
 * справа, теми же цветами, что красят окно. */
const swatchOf = (theme) => function ChatThemeSwatch() {
    return <span aria-hidden="true" className="relative h-[18px] w-[26px] shrink-0 overflow-hidden rounded-[5px] ring-1 ring-slate-900/10"
        style={{ background: theme.swatch || theme.wall }}>
        <span className="absolute left-[3px] top-[3px] h-[5px] w-[12px] rounded-[2px]" style={{ background: theme.incoming }} />
        <span className="absolute bottom-[3px] right-[3px] h-[5px] w-[12px] rounded-[2px]" style={{ background: theme.outgoing }} />
    </span>;
};
const SWATCHES = Object.fromEntries(CHAT_THEMES.map((theme) => [theme.id, swatchOf(theme)]));

export default function ChatThemeMenu({ selected, onChoose }) {
    return <IosMenu label="Тема чатов" trigger={{ icon: <Palette size={13} />, text: 'Тема', className: iosBtnGhost }}
        items={CHAT_THEMES.map((theme) => ({
            key: theme.id, label: theme.name, icon: SWATCHES[theme.id],
            checked: theme.id === selected, onSelect: () => onChoose(theme.id),
        }))} />;
}
