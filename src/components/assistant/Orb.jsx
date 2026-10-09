import React from 'react';
import IcoreAssistantMark from './IcoreAssistantMark.jsx';
import './assistant-orb.css';

// One transparent mark for the floating button, panel avatar and empty chat.
// Keep the existing dimensions and shell so dragging/docking geometry is stable.

const VARIANT_CLASS = {
    orb: '',
    mini: 'aorb--mini',
    hero: 'aorb--hero',
};

const VARIANT_SIZE = { orb: 56, mini: 26, hero: 64 };

const Orb = ({ variant = 'orb', effect, animated = true, className = '' }) => (
    <span
        className={`aorb ${VARIANT_CLASS[variant] || ''} ${className}`}
        aria-hidden="true"
    >
        <span className="aorb-shell">
            <IcoreAssistantMark effect={effect} size={VARIANT_SIZE[variant] || 56} animated={animated} />
        </span>
    </span>
);

export default Orb;
