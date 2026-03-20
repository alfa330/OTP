import React, { useCallback, useEffect, useRef, useState } from 'react';
import './OrazAitSplash.css';

function OrazAitSplash({ open, onClose }) {
    const canvasRef = useRef(null);
    const animationFrameRef = useRef(0);
    const closeTimerRef = useRef(null);
    const particlesRef = useRef([]);
    const [isClosing, setIsClosing] = useState(false);

    useEffect(() => {
        if (!open) return undefined;

        setIsClosing(false);

        const canvas = canvasRef.current;
        if (!canvas) return undefined;

        const ctx = canvas.getContext('2d');
        if (!ctx) return undefined;

        let width = 0;
        let height = 0;

        const spawnParticles = () => {
            particlesRef.current = [];
            const amount = Math.min(Math.floor((width * height) / 5000), 160);
            for (let i = 0; i < amount; i += 1) {
                particlesRef.current.push({
                    x: Math.random() * width,
                    y: Math.random() * height,
                    r: 0.4 + Math.random() * 1.6,
                    vy: -(0.08 + Math.random() * 0.18),
                    vx: (Math.random() - 0.5) * 0.06,
                    phase: Math.random() * Math.PI * 2,
                    speed: 0.004 + Math.random() * 0.008,
                    gold: Math.random() > 0.4
                });
            }
        };

        const resize = () => {
            width = canvas.width = window.innerWidth;
            height = canvas.height = window.innerHeight;
            spawnParticles();
        };

        const draw = (timestamp) => {
            ctx.clearRect(0, 0, width, height);

            particlesRef.current.forEach((particle) => {
                particle.x += particle.vx;
                particle.y += particle.vy;
                if (particle.y < -4) {
                    particle.y = height + 4;
                    particle.x = Math.random() * width;
                }

                const opacity =
                    0.06 +
                    0.32 *
                        (0.5 + 0.5 * Math.sin(timestamp * 0.001 * particle.speed * 150 + particle.phase));

                ctx.beginPath();
                ctx.arc(particle.x, particle.y, particle.r, 0, Math.PI * 2);
                ctx.fillStyle = particle.gold
                    ? `rgba(185,140,50,${opacity * 1.6})`
                    : `rgba(100,80,40,${opacity * 0.7})`;
                ctx.fill();
            });

            animationFrameRef.current = window.requestAnimationFrame(draw);
        };

        resize();
        window.addEventListener('resize', resize);
        animationFrameRef.current = window.requestAnimationFrame(draw);

        return () => {
            window.removeEventListener('resize', resize);
            if (animationFrameRef.current) {
                window.cancelAnimationFrame(animationFrameRef.current);
                animationFrameRef.current = 0;
            }
        };
    }, [open]);

    useEffect(() => {
        return () => {
            if (closeTimerRef.current) {
                window.clearTimeout(closeTimerRef.current);
                closeTimerRef.current = null;
            }
        };
    }, []);

    const finishClose = useCallback(() => {
        if (closeTimerRef.current) {
            window.clearTimeout(closeTimerRef.current);
            closeTimerRef.current = null;
        }
        setIsClosing(false);
        if (typeof onClose === 'function') onClose();
    }, [onClose]);

    const handleClose = useCallback(() => {
        if (isClosing) return;
        setIsClosing(true);

        if (closeTimerRef.current) {
            window.clearTimeout(closeTimerRef.current);
        }

        closeTimerRef.current = window.setTimeout(() => {
            finishClose();
        }, 1500);
    }, [finishClose, isClosing]);

    const handleKeyDown = useCallback(
        (event) => {
            if (event.key === 'Escape' || event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                handleClose();
            }
        },
        [handleClose]
    );

    if (!open) return null;

    return (
        <div
            className={`oraza-splash${isClosing ? ' out' : ''}`}
            role="dialog"
            aria-modal="true"
            aria-label="Ораза айт құтты болсын"
            onKeyDown={handleKeyDown}
            tabIndex={0}
        >
            <div className="oraza-splash__corner-wash oraza-splash__corner-wash--tl" />
            <div className="oraza-splash__corner-wash oraza-splash__corner-wash--br" />
            <div className="oraza-splash__corner-wash oraza-splash__corner-wash--tr" />
            <div className="oraza-splash__pattern" />
            <div className="oraza-splash__center-glow" />
            <canvas ref={canvasRef} className="oraza-splash__canvas" />
            <div className="oraza-splash__frame" />

            <div className="oraza-splash__content">
                <div className="oraza-splash__moon-wrap" aria-hidden="true">
                    <svg className="oraza-splash__moon-svg" width="118" height="118" viewBox="0 0 118 118" fill="none">
                        <circle cx="59" cy="59" r="54" stroke="rgba(176,122,42,0.1)" strokeWidth="1" />
                        <circle cx="59" cy="59" r="46" stroke="rgba(176,122,42,0.12)" strokeWidth="0.6" />
                        <circle cx="59" cy="59" r="38" stroke="rgba(176,122,42,0.1)" strokeWidth="0.4" />
                        <defs>
                            <mask id="orazaMoonMask">
                                <circle cx="59" cy="59" r="35" fill="white" />
                                <circle cx="79" cy="48" r="29" fill="black" />
                            </mask>
                            <radialGradient id="orazaMoonFill" cx="38%" cy="35%" r="65%">
                                <stop offset="0%" stopColor="#FFF4C2" />
                                <stop offset="45%" stopColor="#E8C060" />
                                <stop offset="100%" stopColor="#B8882A" />
                            </radialGradient>
                            <radialGradient id="orazaMoonShade" cx="75%" cy="65%" r="55%">
                                <stop offset="0%" stopColor="rgba(120,80,20,0.18)" />
                                <stop offset="100%" stopColor="transparent" />
                            </radialGradient>
                        </defs>
                        <circle cx="59" cy="59" r="35" fill="url(#orazaMoonFill)" mask="url(#orazaMoonMask)" />
                        <circle cx="59" cy="59" r="35" fill="url(#orazaMoonShade)" mask="url(#orazaMoonMask)" opacity="0.6" />

                        <g transform="translate(84,34)">
                            <polygon
                                points="0,-8.5 2,-2 8.5,-2 3.6,2.2 5.2,8.5 0,5 -5.2,8.5 -3.6,2.2 -8.5,-2 -2,-2"
                                fill="#C99A3E"
                            />
                        </g>

                        <circle cx="25" cy="42" r="1.3" fill="rgba(185,140,50,.35)" />
                        <circle cx="90" cy="80" r="0.9" fill="rgba(185,140,50,.25)" />
                        <circle cx="34" cy="88" r="0.7" fill="rgba(185,140,50,.2)" />
                    </svg>
                </div>

                <div className="oraza-splash__divider" aria-hidden="true">
                    <div className="oraza-splash__dl" />
                    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                        <polygon
                            points="9,1 11,6.5 17,7.3 12.8,11.2 14,17 9,14 4,17 5.2,11.2 1,7.3 7,6.5"
                            stroke="#B07A2A"
                            strokeWidth="0.7"
                        />
                        <circle cx="9" cy="9" r="2" fill="#B07A2A" opacity="0.8" />
                    </svg>
                    <div className="oraza-splash__dl oraza-splash__dl--reverse" />
                </div>

                <div className="oraza-splash__headline-wrap">
                    <span className="oraza-splash__h1">Ораза айт</span>
                </div>
                <div className="oraza-splash__headline-wrap-second">
                    <span className="oraza-splash__h2">құтты болсын</span>
                </div>

                <p className="oraza-splash__sub">Eid Mubarak · عيد مبارك · Рамазан мүбәрак</p>

                <div className="oraza-splash__bottom">
                    <div className="oraza-splash__orn" aria-hidden="true">
                        <div className="oraza-splash__orn-line" />
                        <div className="oraza-splash__orn-dot" />
                        <div className="oraza-splash__orn-diamond" />
                        <div className="oraza-splash__orn-dot" />
                        <div className="oraza-splash__orn-line" />
                    </div>
                    <button type="button" className="oraza-splash__btn" onClick={handleClose}>
                        Кіру
                    </button>
                </div>
            </div>
        </div>
    );
}

export default OrazAitSplash;
