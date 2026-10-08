import React, { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, X } from 'lucide-react';
import IcoreAssistantMark from './IcoreAssistantMark.jsx';
import { DEFAULT_ASSISTANT_EFFECT, ICORE_ASSISTANT_EFFECTS } from './assistantAppearance.js';
import './assistant-appearance.css';

export default function AssistantAppearancePicker({ effect = DEFAULT_ASSISTANT_EFFECT, onChange }) {
    const [open, setOpen] = useState(false);
    const id = useId();
    const triggerRef = useRef(null);
    const popupRef = useRef(null);

    const close = (restoreFocus = false) => {
        setOpen(false);
        if (restoreFocus) triggerRef.current?.focus();
    };

    useEffect(() => {
        if (!open) return undefined;
        const doc = triggerRef.current.ownerDocument;
        // The panel can live in another window. Observe its actual document.
        const onOutside = event => {
            if (!triggerRef.current?.contains(event.target) && !popupRef.current?.contains(event.target)) {
                setOpen(false);
            }
        };
        popupRef.current?.querySelector('input:checked')?.focus();
        doc.addEventListener('pointerdown', onOutside);
        doc.addEventListener('focusin', onOutside);
        return () => {
            doc.removeEventListener('pointerdown', onOutside);
            doc.removeEventListener('focusin', onOutside);
        };
    }, [open]);

    const onKeyDown = event => {
        if (open && event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            close(true);
        }
    };

    return (
        <>
            <button
                ref={triggerRef}
                type="button"
                className="aorb-appearance-trigger"
                aria-label="Внешний вид помощника"
                title="Внешний вид помощника"
                aria-expanded={open}
                aria-controls={open ? id : undefined}
                onClick={() => setOpen(value => !value)}
                onKeyDown={onKeyDown}
            >
                <IcoreAssistantMark effect={effect} size={26} animated={false} />
                <ChevronDown size={10} aria-hidden="true" />
            </button>
            {open && (
                <div ref={popupRef} id={id} className="aorb-appearance-popup" role="region" aria-label="Внешний вид помощника" onKeyDown={onKeyDown}>
                    <div className="aorb-appearance-heading">
                        <span id={`${id}-label`}>Иконка помощника</span>
                        <button type="button" onClick={() => close(true)} aria-label="Закрыть выбор иконки"><X size={15} /></button>
                    </div>
                    <div role="radiogroup" aria-labelledby={`${id}-label`}>
                        {ICORE_ASSISTANT_EFFECTS.map(item => (
                            <label key={item.id} className="aorb-appearance-option">
                                <IcoreAssistantMark effect={item.id} size={34} />
                                <span>{item.name}</span>
                                <input type="radio" name={`${id}-effect`} value={item.id} checked={effect === item.id} onChange={() => onChange?.(item.id)} />
                            </label>
                        ))}
                    </div>
                    <p>Выбор сохраняется для вас в этом браузере.</p>
                </div>
            )}
        </>
    );
}
