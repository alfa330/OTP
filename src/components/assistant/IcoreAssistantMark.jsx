import React from 'react';
import IcoreMark from '../common/IcoreMark.jsx';
import { DEFAULT_ASSISTANT_EFFECT, normalizeAssistantEffect } from './assistantAppearance.js';
import './icore-assistant-mark.css';

export { ICORE_ASSISTANT_EFFECTS } from './assistantAppearance.js';

// The existing brand SVG is both the mask and the edge. Only its contents move;
// the silhouette, open gap and separate centre always retain their exact shape.
export default function IcoreAssistantMark({ effect = DEFAULT_ASSISTANT_EFFECT, size = 56, animated = true, className = '' }) {
    const safeEffect = normalizeAssistantEffect(effect);
    return (
        <span
            className={`icore-assistant-mark icore-assistant-mark--${safeEffect}${animated ? '' : ' icore-assistant-mark--paused'} ${className}`}
            style={{ '--icore-mark-size': `${size}px` }}
            aria-hidden="true"
        >
            <span className="icore-assistant-mark__interior">
                <i className="icore-assistant-mark__glass" />
                <i className="icore-assistant-mark__color" />
                <i className="icore-assistant-mark__ribbon" />
                <i className="icore-assistant-mark__particles" />
                <i className="icore-assistant-mark__shine" />
            </span>
            <IcoreMark className="icore-assistant-mark__edge" />
            <IcoreMark className="icore-assistant-mark__light" />
        </span>
    );
}
