import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import Backgrounds from './Backgrounds';
import {
    BACKGROUNDS, COLORS, EMOJI, FONTS, fontCss, userName, normalizeAnnotations,
} from './annotations';

const QUALITY = 2;
const clamp01 = (v) => Math.max(0, Math.min(1, v));
const nowIso = () => new Date().toISOString();

// Полноэкранный редактор разметки фото (рисунок / стикеры / текст / комментарии / фон).
// Открывается поверх приближённой карточки; оба пользователя могут редактировать.
const PhotoEditor = ({ image, annotations, user, onSave, onClose }) => {
    const base = useMemo(() => normalizeAnnotations(annotations), [annotations]);
    const [strokes, setStrokes] = useState(() => base.strokes.map((s) => ({ ...s, points: s.points.slice() })));
    const [stickers, setStickers] = useState(() => base.stickers.map((s) => ({ ...s })));
    const [texts, setTexts] = useState(() => base.texts.map((t) => ({ ...t })));
    const [comments, setComments] = useState(() => base.comments.slice());
    const [background, setBackground] = useState(base.background || 'none');

    const [tool, setTool] = useState('draw');
    const [color, setColor] = useState('#ff3b6b');
    const [brush, setBrush] = useState(0.01);
    const [sel, setSel] = useState(null); // { kind:'sticker'|'text', index }
    const [commentDraft, setCommentDraft] = useState('');
    const [saving, setSaving] = useState(false);

    const surfaceRef = useRef(null);
    const canvasRef = useRef(null);
    const liveStrokeRef = useRef(null);
    const dragRef = useRef(null);

    // ---- холст рисунка ----
    const redraw = useCallback(() => {
        const canvas = canvasRef.current;
        const surface = surfaceRef.current;
        if (!canvas || !surface) return;
        const w = surface.clientWidth;
        const h = surface.clientHeight;
        if (!w || !h) return;
        canvas.width = Math.round(w * QUALITY);
        canvas.height = Math.round(h * QUALITY);
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.setTransform(QUALITY, 0, 0, QUALITY, 0, 0);
        ctx.clearRect(0, 0, w, h);
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        const all = liveStrokeRef.current ? strokes.concat([liveStrokeRef.current]) : strokes;
        for (const stroke of all) {
            const points = stroke.points || [];
            if (!points.length) continue;
            ctx.beginPath();
            ctx.strokeStyle = stroke.color || '#fff';
            ctx.lineWidth = Math.max(0.75, (stroke.width || 0.006) * w);
            ctx.moveTo(points[0][0] * w, points[0][1] * h);
            if (points.length === 1) ctx.lineTo(points[0][0] * w + 0.1, points[0][1] * h);
            else for (let i = 1; i < points.length; i += 1) ctx.lineTo(points[i][0] * w, points[i][1] * h);
            ctx.stroke();
        }
    }, [strokes]);

    useEffect(() => { redraw(); }, [redraw]);
    useEffect(() => {
        const surface = surfaceRef.current;
        if (!surface || typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(redraw);
        ro.observe(surface);
        return () => ro.disconnect();
    }, [redraw]);

    const pointToNorm = (event) => {
        const rect = surfaceRef.current.getBoundingClientRect();
        return [clamp01((event.clientX - rect.left) / rect.width), clamp01((event.clientY - rect.top) / rect.height)];
    };

    const onSurfacePointerDown = (event) => {
        if (tool !== 'draw') { setSel(null); return; }
        event.currentTarget.setPointerCapture?.(event.pointerId);
        liveStrokeRef.current = { color, width: brush, points: [pointToNorm(event)] };
        redraw();
    };
    const onSurfacePointerMove = (event) => {
        if (tool !== 'draw' || !liveStrokeRef.current) return;
        liveStrokeRef.current.points.push(pointToNorm(event));
        redraw();
    };
    const onSurfacePointerUp = () => {
        if (!liveStrokeRef.current) return;
        const live = liveStrokeRef.current;
        liveStrokeRef.current = null;
        if (live.points.length) setStrokes((prev) => prev.concat([live]));
    };

    const undoStroke = () => setStrokes((prev) => prev.slice(0, -1));

    // ---- перетаскивание стикеров/текста ----
    useEffect(() => {
        const onMove = (event) => {
            const drag = dragRef.current;
            if (!drag) return;
            const rect = surfaceRef.current.getBoundingClientRect();
            const x = clamp01(drag.origX + (event.clientX - drag.startX) / rect.width);
            const y = clamp01(drag.origY + (event.clientY - drag.startY) / rect.height);
            if (drag.kind === 'sticker') setStickers((p) => p.map((s, i) => (i === drag.index ? { ...s, x, y } : s)));
            else setTexts((p) => p.map((t, i) => (i === drag.index ? { ...t, x, y } : t)));
        };
        const onUp = () => { dragRef.current = null; };
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
        return () => { window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); };
    }, []);

    const startDragItem = (event, kind, index, item) => {
        event.stopPropagation();
        setSel({ kind, index });
        dragRef.current = { kind, index, startX: event.clientX, startY: event.clientY, origX: item.x, origY: item.y };
    };

    // ---- добавление/изменение элементов ----
    const addSticker = (emoji) => {
        setStickers((p) => {
            setSel({ kind: 'sticker', index: p.length });
            return p.concat([{ emoji, x: 0.5, y: 0.5, size: 0.16, rot: 0 }]);
        });
    };
    const addText = () => {
        setTool('text');
        setTexts((p) => {
            setSel({ kind: 'text', index: p.length });
            return p.concat([{ text: 'Текст', x: 0.5, y: 0.5, size: 0.08, rot: 0, font: 'script', color: '#ffffff' }]);
        });
    };
    const updateSel = (patch) => {
        if (!sel) return;
        if (sel.kind === 'sticker') setStickers((p) => p.map((s, i) => (i === sel.index ? { ...s, ...patch } : s)));
        else setTexts((p) => p.map((t, i) => (i === sel.index ? { ...t, ...patch } : t)));
    };
    const deleteSel = () => {
        if (!sel) return;
        if (sel.kind === 'sticker') setStickers((p) => p.filter((_, i) => i !== sel.index));
        else setTexts((p) => p.filter((_, i) => i !== sel.index));
        setSel(null);
    };

    const addComment = () => {
        const text = commentDraft.trim();
        if (!text) return;
        setComments((p) => p.concat([{ by: Number(user?.id) || 0, text, at: nowIso() }]));
        setCommentDraft('');
    };

    const clearAll = () => {
        if (!window.confirm('Очистить всю разметку этого фото?')) return;
        setStrokes([]); setStickers([]); setTexts([]); setComments([]); setBackground('none'); setSel(null);
    };

    const handleSave = async () => {
        if (saving) return;
        setSaving(true);
        try {
            await onSave({ strokes, stickers, texts, comments, background });
        } catch (saveError) {
            // Ошибку уже показал родитель тостом — редактор оставляем открытым для повтора.
        } finally {
            setSaving(false);
        }
    };

    const selItem = sel ? (sel.kind === 'sticker' ? stickers[sel.index] : texts[sel.index]) : null;

    return (
        <div className="fy-editor" data-lenta-control>
            <div className="fy-editor-top">
                <span className="fy-editor-title"><FaIcon className="fas fa-pencil" /> Декор фото</span>
                <div className="fy-editor-top-actions">
                    <button type="button" className="fy-btn fy-btn-ghost" onClick={clearAll}>
                        <FaIcon className="fas fa-eraser" /><span>Очистить</span>
                    </button>
                    <button type="button" className="fy-btn fy-btn-primary" onClick={handleSave} disabled={saving}>
                        <FaIcon className={`fas ${saving ? 'fa-spinner fa-spin' : 'fa-check'}`} /><span>{saving ? 'Сохранение…' : 'Сохранить'}</span>
                    </button>
                    <button type="button" className="fy-btn fy-btn-ghost" onClick={onClose} aria-label="Закрыть">
                        <FaIcon className="fas fa-times" />
                    </button>
                </div>
            </div>

            <div className="fy-editor-stage">
                <Backgrounds bg={background} />
                <div
                    ref={surfaceRef}
                    className={`fy-edit-surface ${tool === 'draw' ? 'is-draw' : ''}`}
                    onPointerDown={onSurfacePointerDown}
                    onPointerMove={onSurfacePointerMove}
                    onPointerUp={onSurfacePointerUp}
                    onPointerCancel={onSurfacePointerUp}
                >
                    <img src={image.display_url || image.preview_url} alt="" className="fy-edit-photo" draggable="false" />
                    <canvas ref={canvasRef} className="fy-edit-canvas" style={{ pointerEvents: 'none' }} />
                    {stickers.map((s, i) => (
                        <span
                            key={`s${i}`}
                            className={`fy-edit-item fy-edit-sticker ${sel && sel.kind === 'sticker' && sel.index === i ? 'is-sel' : ''}`}
                            style={{ left: `${s.x * 100}%`, top: `${s.y * 100}%`, fontSize: `${s.size * 100}cqmin`, transform: `translate(-50%,-50%) rotate(${s.rot}deg)` }}
                            onPointerDown={(e) => startDragItem(e, 'sticker', i, s)}
                        >{s.emoji}</span>
                    ))}
                    {texts.map((t, i) => (
                        <span
                            key={`t${i}`}
                            className={`fy-edit-item fy-edit-text ${sel && sel.kind === 'text' && sel.index === i ? 'is-sel' : ''}`}
                            style={{ left: `${t.x * 100}%`, top: `${t.y * 100}%`, fontSize: `${t.size * 100}cqh`, color: t.color, fontFamily: fontCss(t.font), transform: `translate(-50%,-50%) rotate(${t.rot}deg)` }}
                            onPointerDown={(e) => startDragItem(e, 'text', i, t)}
                        >{t.text}</span>
                    ))}
                </div>
            </div>

            <div className="fy-editor-tools">
                {[
                    ['draw', 'fa-pencil', 'Рисунок'],
                    ['sticker', 'fa-heart', 'Стикеры'],
                    ['text', 'fa-font', 'Текст'],
                    ['bg', 'fa-image', 'Фон'],
                    ['comment', 'fa-comment', 'Коммент'],
                ].map(([key, icon, label]) => (
                    <button key={key} type="button" className={`fy-tool ${tool === key ? 'is-active' : ''}`} onClick={() => { setTool(key); setSel(null); }}>
                        <FaIcon className={`fas ${icon}`} /><span>{label}</span>
                    </button>
                ))}
            </div>

            <div className="fy-editor-panel">
                {tool === 'draw' && (
                    <div className="fy-panel-row">
                        <div className="fy-swatches">
                            {COLORS.map((c) => (
                                <button key={c} type="button" className={`fy-swatch ${color === c ? 'is-sel' : ''}`} style={{ background: c }} onClick={() => setColor(c)} aria-label={c} />
                            ))}
                        </div>
                        <label className="fy-slider">Толщина
                            <input type="range" min="0.004" max="0.06" step="0.002" value={brush} onChange={(e) => setBrush(Number(e.target.value))} />
                        </label>
                        <button type="button" className="fy-btn fy-btn-ghost" onClick={undoStroke} disabled={!strokes.length}>
                            <FaIcon className="fas fa-rotate-left" /><span>Отменить</span>
                        </button>
                    </div>
                )}

                {tool === 'sticker' && (
                    <div className="fy-emoji-grid">
                        {EMOJI.map((e) => (
                            <button key={e} type="button" className="fy-emoji" onClick={() => addSticker(e)}>{e}</button>
                        ))}
                    </div>
                )}

                {tool === 'text' && (
                    <div className="fy-panel-col">
                        <button type="button" className="fy-btn fy-btn-soft" onClick={addText}><FaIcon className="fas fa-plus" /><span>Добавить текст</span></button>
                        {sel && sel.kind === 'text' && selItem && (
                            <div className="fy-text-edit">
                                <input className="fy-input" value={selItem.text} maxLength={200} onChange={(e) => updateSel({ text: e.target.value })} placeholder="Введите текст" />
                                <div className="fy-fonts">
                                    {FONTS.map((f) => (
                                        <button key={f.key} type="button" className={`fy-font ${selItem.font === f.key ? 'is-sel' : ''}`} style={{ fontFamily: f.css }} onClick={() => updateSel({ font: f.key })}>{f.label}</button>
                                    ))}
                                </div>
                                <div className="fy-swatches">
                                    {COLORS.map((c) => (
                                        <button key={c} type="button" className={`fy-swatch ${selItem.color === c ? 'is-sel' : ''}`} style={{ background: c }} onClick={() => updateSel({ color: c })} />
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {tool === 'bg' && (
                    <div className="fy-bg-picker">
                        {BACKGROUNDS.map((b) => (
                            <button key={b.key} type="button" className={`fy-bg-swatch ${background === b.key ? 'is-sel' : ''}`} onClick={() => setBackground(b.key)}>
                                <i style={{ background: b.swatch }} />
                                <span>{b.label}</span>
                            </button>
                        ))}
                    </div>
                )}

                {tool === 'comment' && (
                    <div className="fy-comments">
                        <div className="fy-comment-list">
                            {comments.length === 0 && <div className="fy-comment-empty">Пока нет комментариев</div>}
                            {comments.map((c, i) => (
                                <div key={i} className="fy-comment">
                                    <b>{userName(c.by)}</b> {c.text}
                                </div>
                            ))}
                        </div>
                        <div className="fy-comment-add">
                            <input className="fy-input" value={commentDraft} maxLength={600} placeholder="Добавить комментарий…" onChange={(e) => setCommentDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') addComment(); }} />
                            <button type="button" className="fy-btn fy-btn-soft" onClick={addComment}><FaIcon className="fas fa-paper-plane" /></button>
                        </div>
                    </div>
                )}

                {/* Контролы выбранного стикера/текста: размер, поворот, удалить */}
                {sel && selItem && (tool === 'sticker' || tool === 'text') && (
                    <div className="fy-sel-controls">
                        <label className="fy-slider">Размер
                            <input type="range" min="0.04" max={tool === 'text' ? '0.32' : '0.5'} step="0.005" value={selItem.size} onChange={(e) => updateSel({ size: Number(e.target.value) })} />
                        </label>
                        <label className="fy-slider">Поворот
                            <input type="range" min="-180" max="180" step="1" value={selItem.rot} onChange={(e) => updateSel({ rot: Number(e.target.value) })} />
                        </label>
                        <button type="button" className="fy-btn fy-btn-danger" onClick={deleteSel}><FaIcon className="fas fa-trash-alt" /></button>
                    </div>
                )}
            </div>
        </div>
    );
};

export default PhotoEditor;
