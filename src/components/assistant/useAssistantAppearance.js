import { useCallback, useEffect, useState } from 'react';
import { assistantAppearanceKey, normalizeAssistantEffect, readAssistantEffect } from './assistantAppearance.js';

// Keep ownership in AssistantOrb so moving the panel into PiP cannot reset it.
// Scope storage to the account: a shared workstation must not share preferences.
export default function useAssistantAppearance(userId) {
    const key = assistantAppearanceKey(userId);
    const [choice, setChoice] = useState(() => ({ key, effect: readAssistantEffect(key) }));

    useEffect(() => {
        setChoice({ key, effect: readAssistantEffect(key) });
        const onStorage = event => {
            if (event.key === key || event.key === null) {
                setChoice({ key, effect: readAssistantEffect(key) });
            }
        };
        window.addEventListener('storage', onStorage);
        return () => window.removeEventListener('storage', onStorage);
    }, [key]);

    const chooseEffect = useCallback(value => {
        const effect = normalizeAssistantEffect(value);
        setChoice({ key, effect });
        try {
            if (key) window.localStorage.setItem(key, effect);
        } catch {
            // A blocked/full storage must not prevent choosing for this session.
        }
    }, [key]);

    // Do not show the previous account's preference while its effect is pending.
    return [choice.key === key ? choice.effect : readAssistantEffect(key), chooseEffect];
}
