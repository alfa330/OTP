import React, { useMemo } from 'react';

// Детерминированный псевдослучайный генератор — позиции частиц стабильны между
// рендерами (без дёрганья) и без зависимости от запрещённого Math.random в проде он тоже ок.
const seeded = (i, salt) => {
    const x = Math.sin((i + 1) * 12.9898 + salt * 78.233) * 43758.5453;
    return x - Math.floor(x);
};

const buildParticles = (bg) => {
    if (bg === 'hearts') {
        const emojis = ['❤️', '💖', '💕', '🩷', '💗'];
        return Array.from({ length: 24 }, (_, i) => {
            const depth = seeded(i, 1); // 0..1 — ближе/дальше (эффект глубины)
            return {
                kind: 'heart',
                emoji: emojis[Math.floor(seeded(i, 6) * emojis.length) % emojis.length],
                left: seeded(i, 2) * 100,
                size: 14 + depth * 34,
                delay: -seeded(i, 3) * 16,
                duration: 11 + seeded(i, 4) * 10 + (1 - depth) * 5,
                drift: (seeded(i, 5) - 0.5) * 90,
                opacity: 0.3 + depth * 0.55,
                blur: (1 - depth) * 1.8,
            };
        });
    }
    if (bg === 'bokeh') {
        return Array.from({ length: 16 }, (_, i) => {
            const depth = seeded(i, 1);
            return {
                kind: 'bokeh',
                left: seeded(i, 2) * 100,
                top: seeded(i, 7) * 100,
                size: 40 + depth * 150,
                delay: -seeded(i, 3) * 18,
                duration: 16 + seeded(i, 4) * 16,
                drift: (seeded(i, 5) - 0.5) * 120,
                opacity: 0.12 + depth * 0.3,
            };
        });
    }
    if (bg === 'stars') {
        return Array.from({ length: 64 }, (_, i) => ({
            kind: 'star',
            left: seeded(i, 2) * 100,
            top: seeded(i, 7) * 100,
            size: 1 + seeded(i, 1) * 2.6,
            delay: -seeded(i, 3) * 6,
            duration: 2.5 + seeded(i, 4) * 4,
            opacity: 0.3 + seeded(i, 5) * 0.7,
        }));
    }
    if (bg === 'sunset') {
        return Array.from({ length: 14 }, (_, i) => ({
            kind: 'heart',
            emoji: seeded(i, 6) > 0.5 ? '✨' : '🌸',
            left: seeded(i, 2) * 100,
            size: 12 + seeded(i, 1) * 20,
            delay: -seeded(i, 3) * 14,
            duration: 14 + seeded(i, 4) * 10,
            drift: (seeded(i, 5) - 0.5) * 70,
            opacity: 0.4 + seeded(i, 1) * 0.4,
            blur: 0,
        }));
    }
    return [];
};

// Анимированный декоративный фон сцены. Появляется плавно (opacity-переход в CSS),
// у каждого типа — свой эффект: сердечки с глубиной, аврора, боке, звёзды, закат.
const Backgrounds = ({ bg }) => {
    const particles = useMemo(() => buildParticles(bg), [bg]);
    if (!bg || bg === 'none') return null;

    return (
        <div className={`fy-bg fy-bg-${bg}`} aria-hidden="true">
            {bg === 'aurora' && (
                <>
                    <i className="fy-aurora-band b1" />
                    <i className="fy-aurora-band b2" />
                    <i className="fy-aurora-band b3" />
                </>
            )}
            {bg === 'sunset' && (
                <>
                    <i className="fy-sun" />
                    <i className="fy-sun-glow" />
                </>
            )}
            {particles.map((p, i) => {
                const style = {
                    left: `${p.left}%`,
                    '--fy-dur': `${p.duration}s`,
                    '--fy-delay': `${p.delay}s`,
                    '--fy-drift': `${p.drift || 0}px`,
                    opacity: p.opacity,
                };
                if (p.kind === 'heart') {
                    style.fontSize = `${p.size}px`;
                    style.filter = p.blur ? `blur(${p.blur}px)` : undefined;
                    return <span key={i} className="fy-pt fy-pt-heart" style={style}>{p.emoji}</span>;
                }
                if (p.kind === 'bokeh') {
                    style.top = `${p.top}%`;
                    style.width = `${p.size}px`;
                    style.height = `${p.size}px`;
                    return <span key={i} className="fy-pt fy-pt-bokeh" style={style} />;
                }
                // star
                style.top = `${p.top}%`;
                style.width = `${p.size}px`;
                style.height = `${p.size}px`;
                return <span key={i} className="fy-pt fy-pt-star" style={style} />;
            })}
        </div>
    );
};

export default React.memo(Backgrounds);
