import { useCallback, useEffect, useState } from 'react';
import useHalloweenActive from '../seasonal/halloween/useHalloweenActive.js';
import {
    assistantAppearanceKey,
    isAssistantAppearanceKey,
    normalizeAssistantAppearance,
    readAssistantEffect,
    storeAssistantEffect,
} from './assistantAppearance.js';

// Keep ownership in AssistantOrb so moving the panel into PiP cannot reset it.
// Scope storage to the account: a shared workstation must not share preferences.
// Хэллоуин меняет облик по умолчанию (тыква) — в полночь облик перечитывается.
export default function useAssistantAppearance(userId) {
    const key = assistantAppearanceKey(userId);
    const halloween = useHalloweenActive();
    const [choice, setChoice] = useState(() => ({ key, halloween, effect: readAssistantEffect(key, halloween) }));

    useEffect(() => {
        setChoice({ key, halloween, effect: readAssistantEffect(key, halloween) });
        const onStorage = event => {
            if (event.key === null || isAssistantAppearanceKey(event.key, key)) {
                setChoice({ key, halloween, effect: readAssistantEffect(key, halloween) });
            }
        };
        window.addEventListener('storage', onStorage);
        return () => window.removeEventListener('storage', onStorage);
    }, [key, halloween]);

    const chooseEffect = useCallback(value => {
        const effect = normalizeAssistantAppearance(value);
        setChoice({ key, halloween, effect });
        try {
            storeAssistantEffect(key, effect, halloween);
        } catch {
            // A blocked/full storage must not prevent choosing for this session.
        }
    }, [key, halloween]);

    // Do not show the previous account's preference while its effect is pending.
    return [choice.key === key && choice.halloween === halloween ? choice.effect : readAssistantEffect(key, halloween), chooseEffect];
}
