import { useCallback, useEffect, useRef, useState } from 'react';
import {
    canOpenPipWindow, mirrorDocumentChrome, mirrorDocumentStyles, pipWindowTaken,
} from '../../utils/pipWindow';

const closeWindow = (target) => {
    try { target?.close(); } catch { /* The browser may already have closed it. */ }
};

/** Owns only the PiP window. The viewer keeps its portal host and React state.
 *
 * onReturn({ dismissed }) moves the host back into the chat document. dismissed
 * means the person closed the window itself (its close button or the browser's
 * "back to tab"): onClosed follows, and the viewer closes with the window instead
 * of reopening as a modal over the chat (owner, 18.10.2026). The viewer's own
 * "В чат" button and unmounting return the host without dismissing. */
export default function useAttachmentPip({ onReturn, onClosed } = {}) {
    const [pipWindow, setPipWindow] = useState(null);
    const [opening, setOpening] = useState(false);
    const [error, setError] = useState('');
    const mounted = useRef(false);
    const generation = useRef(0);
    const pending = useRef(false);
    const owned = useRef(null);
    const returnCallback = useRef(onReturn);
    returnCallback.current = onReturn;
    const closedCallback = useRef(onClosed);
    closedCallback.current = onClosed;

    const release = useCallback((session, close = true, dismissed = false) => {
        if (!session || owned.current !== session) return;
        owned.current = null;
        session.window.removeEventListener('pagehide', session.onPageHide);
        session.themeObserver?.disconnect();
        session.stopStyles?.();
        // Move the existing portal host back before its document goes away.
        try { returnCallback.current?.({ dismissed }); } finally {
            if (close) closeWindow(session.window);
            if (mounted.current) setPipWindow(null);
            if (dismissed && mounted.current) closedCallback.current?.();
        }
    }, []);

    const returnToChat = useCallback(() => {
        generation.current += 1;
        pending.current = false;
        release(owned.current);
        if (mounted.current) {
            setOpening(false);
            setError('');
        }
    }, [release]);

    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            generation.current += 1;
            pending.current = false;
            release(owned.current);
        };
    }, [release]);

    const open = useCallback(async () => {
        if (!mounted.current) return null;
        if (owned.current && !owned.current.window.closed) {
            owned.current.window.focus();
            return owned.current.window;
        }
        if (pending.current) return null;
        if (!canOpenPipWindow()) {
            setError('Этот браузер не поддерживает окно «Картинка в картинке». Откройте чат в Chrome или Edge.');
            return null;
        }
        if (pipWindowTaken()) {
            setError('Окно «Картинка в картинке» занято другим виджетом. Закройте его и попробуйте снова.');
            return null;
        }

        const attempt = ++generation.current;
        pending.current = true;
        setOpening(true);
        setError('');
        let target, session;
        try {
            // Keep this call in the click handler's user activation.
            target = await window.documentPictureInPicture.requestWindow({ width: 960, height: 720 });
            if (!mounted.current || generation.current !== attempt || target.closed) {
                closeWindow(target);
                return null;
            }
            target.document.title = 'Вложения — Чаты ОП';
            session = { window: target, themeObserver: null, stopStyles: mirrorDocumentStyles(target), onPageHide: null };
            mirrorDocumentChrome(target);
            Object.assign(target.document.body.style, { margin: '0', height: '100vh', overflow: 'hidden' });

            // The window is gone by the person's own action: dismiss, not return.
            session.onPageHide = () => release(session, false, true);
            if (typeof MutationObserver !== 'undefined') {
                session.themeObserver = new MutationObserver(() => {
                    mirrorDocumentChrome(target);
                    if (!document.documentElement.hasAttribute('data-otp-theme')) {
                        target.document.documentElement.removeAttribute('data-otp-theme');
                    }
                    target.document.body.className = document.body.className;
                });
                session.themeObserver.observe(document.documentElement, {
                    attributes: true, attributeFilter: ['data-otp-theme'],
                });
                session.themeObserver.observe(document.body, {
                    attributes: true, attributeFilter: ['class'],
                });
            }
            target.addEventListener('pagehide', session.onPageHide);
            owned.current = session;
            setPipWindow(target);
            return target;
        } catch {
            session?.themeObserver?.disconnect();
            session?.stopStyles?.();
            closeWindow(target);
            if (mounted.current && generation.current === attempt) {
                setError('Не удалось открыть окно «Картинка в картинке». Попробуйте ещё раз.');
            }
            return null;
        } finally {
            if (generation.current === attempt) {
                pending.current = false;
                if (mounted.current) setOpening(false);
            }
        }
    }, [release]);

    return { supported: canOpenPipWindow(), pipWindow, opening, error, open, returnToChat };
}
