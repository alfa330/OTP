import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './AuthEntranceSplash.css';

const DUST_COLORS = ['#e8909a', '#f4c0c8', '#d89098', '#f0b0bc', '#c87888', '#fce0e8'];

const FLOWER_SVG_MARKUP = String.raw`<svg id="flower" viewBox="0 0 300 340" fill="none" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <radialGradient id="pg1" cx="50%" cy="20%" r="80%">
      <stop offset="0%" stop-color="#fce8ec"/>
      <stop offset="60%" stop-color="#e0849a"/>
      <stop offset="100%" stop-color="#a84060"/>
    </radialGradient>
    <radialGradient id="pg2" cx="50%" cy="20%" r="80%">
      <stop offset="0%" stop-color="#fdf0f3"/>
      <stop offset="60%" stop-color="#efaabb"/>
      <stop offset="100%" stop-color="#c06878"/>
    </radialGradient>
    <radialGradient id="pg3" cx="50%" cy="20%" r="80%">
      <stop offset="0%" stop-color="#fef5f7"/>
      <stop offset="60%" stop-color="#f8ccd4"/>
      <stop offset="100%" stop-color="#d898a4"/>
    </radialGradient>
    <radialGradient id="centerG" cx="45%" cy="35%" r="65%">
      <stop offset="0%" stop-color="#fff4d8"/>
      <stop offset="100%" stop-color="#d4943c"/>
    </radialGradient>
    <linearGradient id="leafG" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#7ab878"/>
      <stop offset="100%" stop-color="#2e5c2c"/>
    </linearGradient>
    <filter id="ps" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="1" stdDeviation="3" flood-color="#b05068" flood-opacity="0.15"/>
    </filter>
    <filter id="cg" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="6" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <radialGradient id="aura" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#e8a0b0" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="#e8a0b0" stop-opacity="0"/>
    </radialGradient>
  </defs>

  <ellipse cx="150" cy="140" rx="115" ry="105" fill="url(#aura)" opacity="0">
    <animate attributeName="opacity" values="0;1" dur="1.2s" begin="1.2s" fill="freeze"/>
  </ellipse>

  <path d="M150 318 Q145 280 143 250 Q140 210 150 172"
        stroke="#5a9058" stroke-width="4" stroke-linecap="round" fill="none"
        stroke-dasharray="152" stroke-dashoffset="152" opacity="0">
    <animate attributeName="opacity" values="0;1" dur="0.01s" begin="0.15s" fill="freeze"/>
    <animate attributeName="stroke-dashoffset" from="152" to="0"
             dur="1.1s" begin="0.15s" fill="freeze" calcMode="spline" keySplines="0.4 0 0.2 1"/>
  </path>

  <g opacity="0">
    <animate attributeName="opacity" values="0;1" dur="0.5s" begin="0.85s" fill="freeze"/>
    <animateTransform attributeName="transform" type="rotate"
                      values="-18 143 250; 0 143 250"
                      dur="0.7s" begin="0.85s" fill="freeze"
                      calcMode="spline" keySplines="0.16 1 0.3 1"/>
    <path d="M143 250 Q108 232 98 202 Q122 216 143 250Z" fill="url(#leafG)"/>
    <path d="M143 250 Q112 226 100 204" stroke="#a0cc9e" stroke-width="0.8" fill="none" opacity="0.55"/>
  </g>

  <g opacity="0">
    <animate attributeName="opacity" values="0;1" dur="0.5s" begin="1.05s" fill="freeze"/>
    <animateTransform attributeName="transform" type="rotate"
                      values="20 145 220; 0 145 220"
                      dur="0.7s" begin="1.05s" fill="freeze"
                      calcMode="spline" keySplines="0.16 1 0.3 1"/>
    <path d="M145 220 Q182 202 194 172 Q168 186 145 220Z" fill="url(#leafG)"/>
    <path d="M145 220 Q176 200 192 174" stroke="#a0cc9e" stroke-width="0.8" fill="none" opacity="0.55"/>
  </g>

  <g opacity="0">
    <animate attributeName="opacity" values="0;1" dur="0.5s" begin="1.25s" fill="freeze"/>
    <path d="M150 172 Q130 162 124 146 Q141 158 150 172Z" fill="#4e8c50"/>
    <path d="M150 172 Q170 162 176 146 Q159 158 150 172Z" fill="#4e8c50"/>
    <path d="M150 172 Q140 156 140 142 Q148 158 150 172Z" fill="#5ea060"/>
    <path d="M150 172 Q160 156 160 142 Q152 158 150 172Z" fill="#5ea060"/>
  </g>

  <g filter="url(#ps)">
    <g transform="rotate(0 150 140)"><path d="M150 140 Q131 108 136 82 Q150 100 164 82 Q169 108 150 140Z" fill="url(#pg1)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.4s" begin="1.5s" fill="freeze"/></path></g>
    <g transform="rotate(60 150 140)"><path d="M150 140 Q131 108 136 82 Q150 100 164 82 Q169 108 150 140Z" fill="url(#pg1)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.4s" begin="1.65s" fill="freeze"/></path></g>
    <g transform="rotate(120 150 140)"><path d="M150 140 Q131 108 136 82 Q150 100 164 82 Q169 108 150 140Z" fill="url(#pg1)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.4s" begin="1.8s" fill="freeze"/></path></g>
    <g transform="rotate(180 150 140)"><path d="M150 140 Q131 108 136 82 Q150 100 164 82 Q169 108 150 140Z" fill="url(#pg1)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.4s" begin="1.95s" fill="freeze"/></path></g>
    <g transform="rotate(240 150 140)"><path d="M150 140 Q131 108 136 82 Q150 100 164 82 Q169 108 150 140Z" fill="url(#pg1)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.4s" begin="2.1s" fill="freeze"/></path></g>
    <g transform="rotate(300 150 140)"><path d="M150 140 Q131 108 136 82 Q150 100 164 82 Q169 108 150 140Z" fill="url(#pg1)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.4s" begin="2.25s" fill="freeze"/></path></g>
  </g>

  <g filter="url(#ps)">
    <g transform="rotate(30 150 140)"><path d="M150 140 Q134 112 139 90 Q150 106 161 90 Q166 112 150 140Z" fill="url(#pg2)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.35s" begin="2.38s" fill="freeze"/></path></g>
    <g transform="rotate(90 150 140)"><path d="M150 140 Q134 112 139 90 Q150 106 161 90 Q166 112 150 140Z" fill="url(#pg2)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.35s" begin="2.5s" fill="freeze"/></path></g>
    <g transform="rotate(150 150 140)"><path d="M150 140 Q134 112 139 90 Q150 106 161 90 Q166 112 150 140Z" fill="url(#pg2)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.35s" begin="2.62s" fill="freeze"/></path></g>
    <g transform="rotate(210 150 140)"><path d="M150 140 Q134 112 139 90 Q150 106 161 90 Q166 112 150 140Z" fill="url(#pg2)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.35s" begin="2.74s" fill="freeze"/></path></g>
    <g transform="rotate(270 150 140)"><path d="M150 140 Q134 112 139 90 Q150 106 161 90 Q166 112 150 140Z" fill="url(#pg2)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.35s" begin="2.86s" fill="freeze"/></path></g>
    <g transform="rotate(330 150 140)"><path d="M150 140 Q134 112 139 90 Q150 106 161 90 Q166 112 150 140Z" fill="url(#pg2)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.35s" begin="2.98s" fill="freeze"/></path></g>
  </g>

  <g>
    <g transform="rotate(0 150 140)"><path d="M150 140 Q139 120 142 106 Q150 116 158 106 Q161 120 150 140Z" fill="url(#pg3)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.3s" begin="3.1s" fill="freeze"/></path></g>
    <g transform="rotate(72 150 140)"><path d="M150 140 Q139 120 142 106 Q150 116 158 106 Q161 120 150 140Z" fill="url(#pg3)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.3s" begin="3.18s" fill="freeze"/></path></g>
    <g transform="rotate(144 150 140)"><path d="M150 140 Q139 120 142 106 Q150 116 158 106 Q161 120 150 140Z" fill="url(#pg3)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.3s" begin="3.26s" fill="freeze"/></path></g>
    <g transform="rotate(216 150 140)"><path d="M150 140 Q139 120 142 106 Q150 116 158 106 Q161 120 150 140Z" fill="url(#pg3)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.3s" begin="3.34s" fill="freeze"/></path></g>
    <g transform="rotate(288 150 140)"><path d="M150 140 Q139 120 142 106 Q150 116 158 106 Q161 120 150 140Z" fill="url(#pg3)" opacity="0"><animate attributeName="opacity" values="0;1" dur="0.3s" begin="3.42s" fill="freeze"/></path></g>
  </g>

  <circle cx="150" cy="140" r="16" fill="url(#centerG)" filter="url(#cg)" opacity="0">
    <animate attributeName="opacity" values="0;1" dur="0.4s" begin="3.52s" fill="freeze"/>
  </circle>
  <circle cx="150" cy="140" r="8" fill="#fffbf0" opacity="0">
    <animate attributeName="opacity" values="0;1" dur="0.3s" begin="3.6s" fill="freeze"/>
  </circle>
  <g opacity="0">
    <animate attributeName="opacity" values="0;1" dur="0.3s" begin="3.65s" fill="freeze"/>
    <circle cx="150" cy="131" r="1.8" fill="#c8882c"/>
    <circle cx="157" cy="134" r="1.5" fill="#c8882c"/>
    <circle cx="143" cy="134" r="1.5" fill="#c8882c"/>
    <circle cx="155" cy="143" r="1.4" fill="#c8882c"/>
    <circle cx="145" cy="143" r="1.4" fill="#c8882c"/>
  </g>

  <g opacity="0">
    <animate attributeName="opacity" values="0;1" dur="0.6s" begin="3.7s" fill="freeze"/>
    <path d="M74 78 L76 72 L78 78 L84 80 L78 82 L76 88 L74 82 L68 80Z" fill="#f0a4ac" opacity="0.7"><animate attributeName="opacity" values="0.7;0.2;0.7" dur="3.2s" begin="3.9s" repeatCount="indefinite"/></path>
    <path d="M226 66 L228 61 L230 66 L235 68 L230 70 L228 75 L226 70 L221 68Z" fill="#e89098" opacity="0.6"><animate attributeName="opacity" values="0.6;0.15;0.6" dur="2.6s" begin="4.2s" repeatCount="indefinite"/></path>
    <path d="M52 162 L54 158 L56 162 L60 164 L56 166 L54 170 L52 166 L48 164Z" fill="#f4bcc4" opacity="0.5"><animate attributeName="opacity" values="0.5;0.1;0.5" dur="4s" begin="4.4s" repeatCount="indefinite"/></path>
    <path d="M244 182 L246 178 L248 182 L252 184 L248 186 L246 190 L244 186 L240 184Z" fill="#e8a4ac" opacity="0.5"><animate attributeName="opacity" values="0.5;0.1;0.5" dur="3.5s" begin="4.1s" repeatCount="indefinite"/></path>
  </g>

  <g opacity="0">
    <animate attributeName="opacity" values="0;1" dur="1.5s" begin="2s" fill="freeze"/>
    <circle cx="50" cy="240" r="5" fill="#e8909a" opacity="0.18"><animate attributeName="cy" values="240;226;240" dur="5s" repeatCount="indefinite" calcMode="spline" keySplines="0.4 0 0.6 1;0.4 0 0.6 1"/></circle>
    <circle cx="250" cy="88" r="3.5" fill="#f0b0bc" opacity="0.22"><animate attributeName="cy" values="88;75;88" dur="4.2s" repeatCount="indefinite" calcMode="spline" keySplines="0.4 0 0.6 1;0.4 0 0.6 1"/></circle>
    <circle cx="34" cy="112" r="3" fill="#d89098" opacity="0.15"><animate attributeName="cy" values="112;100;112" dur="6s" repeatCount="indefinite" calcMode="spline" keySplines="0.4 0 0.6 1;0.4 0 0.6 1"/></circle>
    <circle cx="266" cy="228" r="4" fill="#f4c0c8" opacity="0.18"><animate attributeName="cy" values="228;216;228" dur="4.8s" repeatCount="indefinite" calcMode="spline" keySplines="0.4 0 0.6 1;0.4 0 0.6 1"/></circle>
  </g>
</svg>`;

const buildDustParticles = () =>
    Array.from({ length: 18 }, () => ({
        left: `${Math.random() * 100}%`,
        size: 3 + Math.random() * 6,
        color: DUST_COLORS[Math.floor(Math.random() * DUST_COLORS.length)],
        dx: (Math.random() - 0.5) * 80,
        duration: 7 + Math.random() * 9,
        delay: Math.random() * 12,
        blur: Math.random() * 2
    }));

function AuthEntranceSplash({ open, onClose }) {
    const [isClosing, setIsClosing] = useState(false);
    const closeTimerRef = useRef(null);
    const particles = useMemo(() => buildDustParticles(), []);

    useEffect(() => {
        if (!open) return;
        setIsClosing(false);
        if (closeTimerRef.current) {
            window.clearTimeout(closeTimerRef.current);
            closeTimerRef.current = null;
        }
    }, [open]);

    useEffect(() => () => {
        if (closeTimerRef.current) {
            window.clearTimeout(closeTimerRef.current);
        }
    }, []);

    const handleClose = useCallback(() => {
        if (isClosing) return;
        setIsClosing(true);
        closeTimerRef.current = window.setTimeout(() => {
            closeTimerRef.current = null;
            if (typeof onClose === 'function') onClose();
        }, 1100);
    }, [isClosing, onClose]);

    const handleKeyDown = useCallback((event) => {
        if (event.key === 'Enter' || event.key === ' ' || event.key === 'Escape') {
            event.preventDefault();
            handleClose();
        }
    }, [handleClose]);

    if (!open) return null;

    return (
        <div
            className={`march8-splash${isClosing ? ' out' : ''}`}
            onClick={handleClose}
            onKeyDown={handleKeyDown}
            role="button"
            tabIndex={0}
            aria-label="Закрыть приветственный экран"
        >
            <div className="march8-splash__dust" aria-hidden="true">
                {particles.map((particle, index) => (
                    <div
                        key={`dust-${index}`}
                        className="march8-splash__particle"
                        style={{
                            left: particle.left,
                            width: `${particle.size}px`,
                            height: `${particle.size}px`,
                            background: particle.color,
                            '--dx': `${particle.dx}px`,
                            animationDuration: `${particle.duration}s`,
                            animationDelay: `${particle.delay}s`,
                            filter: `blur(${particle.blur}px)`
                        }}
                    />
                ))}
            </div>

            <div
                className="march8-splash__flower"
                aria-hidden="true"
                dangerouslySetInnerHTML={{ __html: FLOWER_SVG_MARKUP }}
            />

            <div className="march8-splash__caption">
                <h1>С <em>8</em> Марта</h1>
                <p>С днём весны и красоты</p>
                <div className="march8-splash__rule" />
            </div>

            <div className="march8-splash__hint">нажмите, чтобы продолжить</div>
        </div>
    );
}

export default AuthEntranceSplash;
