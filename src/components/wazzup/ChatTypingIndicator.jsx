import React, { useCallback, useLayoutEffect, useRef, useSyncExternalStore } from 'react';

// Three decorative dots, as in WhatsApp; the label next to them carries the text.
const typingDots = (className = '') => (
    <span aria-hidden="true" className={`inline-flex h-4 shrink-0 items-center gap-1 ${className}`.trim()}>
        <span className="wazzup-typing-dot" />
        <span className="wazzup-typing-dot" />
        <span className="wazzup-typing-dot" />
    </span>
);

export default function ChatTypingIndicator({ store, chatKey, fallback = null, bubble = false, threadBox }) {
    const keepBottom = useRef(false);
    const subscribe = useCallback((listener) => store.subscribe(chatKey, () => {
        // Measure before the bubble changes the viewport height. Keep readers
        // at the bottom there, without moving anyone reading earlier messages.
        const box = threadBox?.current;
        keepBottom.current = Boolean(box && box.scrollHeight - box.scrollTop - box.clientHeight < 4);
        listener();
    }), [store, chatKey, threadBox]);
    const snapshot = useCallback(() => store.snapshot(chatKey), [store, chatKey]);
    const label = useSyncExternalStore(subscribe, snapshot, () => '');
    useLayoutEffect(() => {
        const box = threadBox?.current;
        if (keepBottom.current && box) box.scrollTop = box.scrollHeight;
        keepBottom.current = false;
    }, [label, threadBox]);
    if (bubble) return (
        <div role="status" aria-live="polite" aria-atomic="true" data-testid="wazzup-thread-typing"
            className={label ? 'mx-auto flex w-full max-w-[1040px] shrink-0 justify-end px-3 pb-2 sm:px-4' : 'sr-only'}>
            <span className="sr-only">{label}</span>
            {label && <div aria-hidden="true" title={label}
                className="wazzup-message-outgoing wazzup-chat-typing inline-flex max-w-[85%] items-center gap-2.5 rounded-2xl rounded-br-md bg-[#dcf8c6] px-3 py-2 shadow-sm">
                <span className="min-w-0 truncate text-[12px] font-medium">{label.replace(/…$/, '')}</span>
                {typingDots()}
            </div>}
        </div>
    );
    // As in the thread: «Name печатает» and the dots. The verb is the last word:
    // a long name is truncated, the verb and the dots stay; the row keeps its height.
    const verb = label.lastIndexOf(' ');
    return <>
        <span className={label ? 'wazzup-chat-typing flex min-w-0 items-center font-medium' : 'sr-only'}
            title={label || undefined}
            data-testid="wazzup-chat-typing">
            {label && <>
                <span className="min-w-0 truncate">{label.slice(0, verb)}</span>
                <span className="shrink-0 whitespace-pre">{label.slice(verb).replace(/…$/, '')}</span>
                {typingDots('ml-1.5')}
            </>}
        </span>
        {!label && fallback}
    </>;
}
