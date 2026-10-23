import React from 'react';

/* Слой обоев под перепиской (chatWallpaper.js): картинка с видом человека —
 * яркостью, размытием, насыщенностью и выбранной видимой частью.
 *
 * Лежит первым ребёнком полотна (.wz-chat-pane) под всем остальным: полотно —
 * свой контекст наложения (isolate), слой — ниже его содержимого. Яркость — не
 * filter: brightness(), а вуаль чёрного или белого: так светлые места картинки
 * не выгорают, а тёмные не проваливаются в сплошную черноту.
 *
 * scale — во сколько раз слой меньше настоящего окна (превью, миниатюра у
 * админа): размытие задано в пикселях окна и уменьшается вместе с ним.
 * Размытая картинка шире слоя на два радиуса с каждой стороны — иначе края
 * просвечивали бы полупрозрачной каймой. */
export function wallpaperFilter({ blur = 0, saturation = 100 } = {}, scale = 1) {
    const parts = [];
    if (blur > 0) parts.push(`blur(${Math.round(blur * scale * 10) / 10}px)`);
    if (saturation !== 100) parts.push(`saturate(${saturation / 100})`);
    return parts.length ? parts.join(' ') : 'none';
}

export function wallpaperVeil(brightness = 0) {
    if (brightness < 0) return `rgba(0, 0, 0, ${Math.min(-brightness, 100) / 100})`;
    if (brightness > 0) return `rgba(255, 255, 255, ${Math.min(brightness, 100) / 100})`;
    return '';
}

export default function ChatWallpaperLayer({ url, settings, scale = 1, className = '' }) {
    if (!url || !settings) return null;
    const pad = settings.blur > 0 ? Math.ceil(settings.blur * scale * 2) : 0;
    const veil = wallpaperVeil(settings.brightness);
    return (
        <div aria-hidden="true" data-chat-wallpaper-layer=""
            className={`wz-wallpaper pointer-events-none absolute inset-0 overflow-hidden ${className}`}>
            <div className="absolute bg-cover bg-no-repeat"
                style={{ inset: -pad, backgroundImage: `url("${url}")`,
                    backgroundPosition: `${settings.x}% ${settings.y}%`, filter: wallpaperFilter(settings, scale) }} />
            {veil && <div className="absolute inset-0" style={{ background: veil }} />}
        </div>
    );
}
