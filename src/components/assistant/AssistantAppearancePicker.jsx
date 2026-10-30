import React, { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, X } from 'lucide-react';
import IcoreAssistantMark from './IcoreAssistantMark.jsx';
import { ASSISTANT_APPEARANCES, DEFAULT_ASSISTANT_EFFECT, PUMPKIN_APPEARANCE } from './assistantAppearance.js';
import pumpkinLogo from './pumpkin/assets/mascot-logo.webp';
import './assistant-appearance.css';

/* Тыква в выборе — неподвижная картинка: живая тыква с эмоциями только в самом шарике. */
const AppearanceIcon = ({ effect, size, animated }) => (effect === PUMPKIN_APPEARANCE
    ? <img className="aorb-appearance-pumpkin" src={pumpkinLogo} alt="" width={size} height={size} draggable={false} />
    : <IcoreAssistantMark effect={effect} size={size} animated={animated} />);

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
                <AppearanceIcon effect={effect} size={26} animated={false} />
                <ChevronDown size={10} aria-hidden="true" />
            </button>
            {open && (
                <div ref={popupRef} id={id} className="aorb-appearance-popup" role="region" aria-label="Внешний вид помощника" onKeyDown={onKeyDown}>
                    <div className="aorb-appearance-heading">
                        <span id={`${id}-label`}>Иконка помощника</span>
                        <button type="button" onClick={() => close(true)} aria-label="Закрыть выбор иконки"><X size={15} /></button>
                    </div>
                    <div role="radiogroup" aria-labelledby={`${id}-label`}>
                        {ASSISTANT_APPEARANCES.map(item => (
                            <label key={item.id} className="aorb-appearance-option">
                                <AppearanceIcon effect={item.id} size={34} />
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
