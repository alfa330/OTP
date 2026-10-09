import { useCallback, useEffect, useRef, useState } from 'react';
import {
    canOpenPipWindow, cloneDocumentStyles, mirrorDocumentChrome, pipWindowTaken,
} from '../../utils/pipWindow';

const closeWindow = (target) => {
    try { target?.close(); } catch { /* The browser may already have closed it. */ }
};

const mirrorStyles = (target) => {
    const sourceStyles = () => Array.from(document.querySelectorAll('link[rel="stylesheet"], style'));
    const head = target.document.head;
    const previousNodes = new Set(head.childNodes);
    const sources = sourceStyles();
    cloneDocumentStyles(target);
    const clones = Array.from(head.childNodes).filter((node) => !previousNodes.has(node));
    const entries = new Map(sources.map((source, index) => [source, clones[index]]));
    const boundary = target.document.createComment('attachment-pip-styles');
    head.appendChild(boundary);
    let disposed = false;

    const sync = () => {
        if (disposed || target.closed) return;
        const current = sourceStyles();
        const available = new Set(current);
        entries.forEach((clone, source) => {
            if (!available.has(source)) {
                clone.remove();
                entries.delete(source);
            }
        });
        current.forEach((source) => {
            const clone = source.cloneNode(true);
            if (clone.tagName === 'LINK' && source.href) clone.href = source.href;
            const previous = entries.get(source);
            if (previous?.isEqualNode(clone)) return;
            previous?.remove();
            entries.set(source, clone);
        });
        // Preserve source order without detaching unchanged stylesheet links.
        let next = boundary;
        current.slice().reverse().forEach((source) => {
            const clone = entries.get(source);
            if (clone.nextSibling !== next) head.insertBefore(clone, next);
            next = clone;
        });
    };
    const observer = typeof MutationObserver !== 'undefined' ? new MutationObserver(sync) : null;
    observer?.observe(document.head, {
        childList: true, subtree: true, characterData: true, attributes: true,
    });
    return () => {
        disposed = true;
        observer?.disconnect();
        entries.forEach((clone) => clone.remove());
        entries.clear();
        boundary.remove();
    };
};

/** Owns only the PiP window. The viewer keeps its portal host and React state. */
export default function useAttachmentPip({ onReturn } = {}) {
    const [pipWindow, setPipWindow] = useState(null);
    const [opening, setOpening] = useState(false);
    const [error, setError] = useState('');
    const mounted = useRef(false);
    const generation = useRef(0);
    const pending = useRef(false);
    const owned = useRef(null);
    const returnCallback = useRef(onReturn);
    returnCallback.current = onReturn;

    const release = useCallback((session, close = true) => {
        if (!session || owned.current !== session) return;
        owned.current = null;
        session.window.removeEventListener('pagehide', session.onPageHide);
        session.themeObserver?.disconnect();
        session.stopStyles?.();
        // Move the existing portal host back before its document goes away.
        try { returnCallback.current?.(); } finally {
            if (close) closeWindow(session.window);
            if (mounted.current) setPipWindow(null);
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
            session = { window: target, themeObserver: null, stopStyles: mirrorStyles(target), onPageHide: null };
            mirrorDocumentChrome(target);
            Object.assign(target.document.body.style, { margin: '0', height: '100vh', overflow: 'hidden' });

            session.onPageHide = () => release(session, false);
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
