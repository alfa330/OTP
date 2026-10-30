import React from 'react';
import { createRoot } from 'react-dom/client';
import HalloweenLayer from './HalloweenLayer';
import { isFramedWindow, shouldMountHalloween } from './halloweenWindow';

/* Свой корень React рядом с порталом, а не внутри него: упавшее украшение не
   уронит портал, а по сроку слой снимается целиком вместе с узлом. */
export function mountHalloween() {
    if (!shouldMountHalloween({ search: window.location.search, framed: isFramedWindow() })) return;
    if (document.getElementById('otp-halloween-root')) return;
    const host = document.createElement('div');
    host.id = 'otp-halloween-root';
    document.body.appendChild(host);
    const root = createRoot(host);
    let removed = false;
    const remove = () => {
        if (removed) return;
        removed = true;
        window.setTimeout(() => {
            root.unmount();
            host.remove();
        }, 0);
    };
    root.render(<HalloweenLayer onExpire={remove} />);
}
