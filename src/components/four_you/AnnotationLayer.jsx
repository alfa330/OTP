import React, { useEffect, useRef } from 'react';
import { fontCss } from './annotations';

// Оверсэмплинг холста, чтобы линии оставались чёткими при зуме карточки.
const QUALITY = 2;

// Только-чтение наложение разметки поверх фото. Координаты нормализованы (0..1),
// размеры стикеров/текста — в container-query единицах, поэтому всё тянется вместе
// с карточкой (и превью, и зум) без пересчёта в JS.
const AnnotationLayer = ({ annotations }) => {
    const canvasRef = useRef(null);
    const wrapRef = useRef(null);
    const strokes = annotations?.strokes || [];
    const stickers = annotations?.stickers || [];
    const texts = annotations?.texts || [];

    useEffect(() => {
        const canvas = canvasRef.current;
        const wrap = wrapRef.current;
        if (!canvas || !wrap) return undefined;

        const draw = () => {
            const w = wrap.clientWidth;
            const h = wrap.clientHeight;
            if (!w || !h) return;
            canvas.width = Math.round(w * QUALITY);
            canvas.height = Math.round(h * QUALITY);
            const ctx = canvas.getContext('2d');
            if (!ctx) return;
            ctx.setTransform(QUALITY, 0, 0, QUALITY, 0, 0);
            ctx.clearRect(0, 0, w, h);
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            for (const stroke of strokes) {
                const points = stroke?.points || [];
                if (!points.length) continue;
                ctx.beginPath();
                ctx.strokeStyle = stroke.color || '#ffffff';
                ctx.lineWidth = Math.max(0.75, (stroke.width || 0.006) * w);
                const [sx, sy] = points[0];
                ctx.moveTo(sx * w, sy * h);
                if (points.length === 1) {
                    ctx.lineTo(sx * w + 0.1, sy * h);
                } else {
                    for (let i = 1; i < points.length; i += 1) {
                        ctx.lineTo(points[i][0] * w, points[i][1] * h);
                    }
                }
                ctx.stroke();
            }
        };

        draw();
        let observer = null;
        if (typeof ResizeObserver !== 'undefined') {
            observer = new ResizeObserver(draw);
            observer.observe(wrap);
        }
        return () => { if (observer) observer.disconnect(); };
    }, [strokes]);

    return (
        <div ref={wrapRef} className="fy-ann-layer" aria-hidden="true">
            <canvas ref={canvasRef} className="fy-ann-canvas" />
            {stickers.map((sticker, index) => (
                <span
                    key={`s${index}`}
                    className="fy-ann-sticker"
                    style={{
                        left: `${(sticker.x ?? 0.5) * 100}%`,
                        top: `${(sticker.y ?? 0.5) * 100}%`,
                        fontSize: `${(sticker.size || 0.12) * 100}cqmin`,
                        transform: `translate(-50%, -50%) rotate(${sticker.rot || 0}deg)`,
                    }}
                >
                    {sticker.emoji}
                </span>
            ))}
            {texts.map((text, index) => (
                <span
                    key={`t${index}`}
                    className="fy-ann-text"
                    style={{
                        left: `${(text.x ?? 0.5) * 100}%`,
                        top: `${(text.y ?? 0.5) * 100}%`,
                        fontSize: `${(text.size || 0.06) * 100}cqh`,
                        color: text.color || '#ffffff',
                        fontFamily: fontCss(text.font),
                        transform: `translate(-50%, -50%) rotate(${text.rot || 0}deg)`,
                    }}
                >
                    {text.text}
                </span>
            ))}
        </div>
    );
};

export default React.memo(AnnotationLayer);
