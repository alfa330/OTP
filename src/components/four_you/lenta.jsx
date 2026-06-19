import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import './lenta.css';

const STEP = 160;
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const lerp = (from, to, amount) => from + (to - from) * amount;

const FourYouView = ({ user, apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const sceneRef = useRef(null);
    const railRef = useRef(null);
    const cardRefs = useRef([]);
    const fileInputRef = useRef(null);
    const targetRef = useRef(0);
    const scrollRef = useRef(0);
    const expandMixRef = useRef(0);
    const selectedMixRef = useRef(0);
    const dragRef = useRef({ active: false, x: 0, y: 0, target: 0 });
    const hoverIndexRef = useRef(null);
    const hoverMixRef = useRef([]);

    const [images, setImages] = useState([]);
    const [activeIndex, setActiveIndex] = useState(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState('');
    const [canUpload, setCanUpload] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [uploadProgress, setUploadProgress] = useState(0);
    const [deletingId, setDeletingId] = useState('');

    const authHeaders = useCallback(() => withAccessTokenHeader({
        'X-User-Id': String(user?.id || ''),
    }), [user?.id, withAccessTokenHeader]);

    const loadImages = useCallback(async (signal) => {
        setIsLoading(true);
        setError('');
        try {
            const response = await axios.get(`${apiBaseUrl}/api/four_you/images`, {
                headers: authHeaders(),
                signal,
            });
            const rows = Array.isArray(response?.data?.images) ? response.data.images : [];
            setImages(rows);
            setCanUpload(Boolean(response?.data?.can_upload));
            hoverMixRef.current = new Array(rows.length).fill(0);
            const initial = Math.min(Math.max(rows.length - 1, 0), 2.8) * STEP;
            targetRef.current = initial;
            scrollRef.current = initial;
        } catch (requestError) {
            if (requestError?.code === 'ERR_CANCELED') return;
            const message = requestError?.response?.status === 403
                ? 'У вас нет доступа к разделу 4 You'
                : (requestError?.response?.data?.error || 'Не удалось загрузить изображения');
            setError(message);
        } finally {
            setIsLoading(false);
        }
    }, [apiBaseUrl, authHeaders]);

    useEffect(() => {
        const controller = new AbortController();
        loadImages(controller.signal);
        return () => controller.abort();
    }, [loadImages]);

    useEffect(() => {
        if (!images.length) return undefined;
        const preloaded = images.map((item) => {
            const image = new Image();
            image.decoding = 'async';
            image.src = item.preview_url;
            return image;
        });
        return () => {
            preloaded.forEach((image) => { image.src = ''; });
        };
    }, [images]);

    useEffect(() => {
        if (activeIndex == null || !images.length) return undefined;
        const indexes = [activeIndex - 1, activeIndex, activeIndex + 1]
            .filter((index) => index >= 0 && index < images.length);
        const preloaded = indexes.map((index) => {
            const image = new Image();
            image.decoding = 'async';
            image.src = images[index].display_url;
            return image;
        });
        return () => {
            preloaded.forEach((image) => { image.src = ''; });
        };
    }, [activeIndex, images]);

    useEffect(() => {
        if (!images.length) return undefined;
        let frameId = 0;
        const animate = () => {
            const scene = sceneRef.current;
            const rail = railRef.current;
            if (!scene || !rail) return;

            const max = Math.max(0, (images.length - 1) * STEP);
            targetRef.current = clamp(targetRef.current, 0, max);
            scrollRef.current = lerp(scrollRef.current, targetRef.current, 0.11);
            if (Math.abs(scrollRef.current - targetRef.current) < 0.025) {
                scrollRef.current = targetRef.current;
            }

            const expanded = activeIndex != null;
            expandMixRef.current = lerp(expandMixRef.current, expanded ? 1 : 0, 0.05);
            selectedMixRef.current = lerp(selectedMixRef.current, expanded ? 1 : 0, 0.14);
            const activeFloat = scrollRef.current / STEP;
            const sceneRect = scene.getBoundingClientRect();
            const railRect = rail.getBoundingClientRect();
            const railCenterX = railRect.left + railRect.width / 2;
            const railCenterY = railRect.top + railRect.height / 2;
            const perspective = 4000;

            cardRefs.current.forEach((card, index) => {
                if (!card) return;
                const distance = index - activeFloat;
                const absoluteDistance = Math.abs(distance);
                const isSelected = activeIndex === index;
                const hoverTarget = hoverIndexRef.current === index && !expanded && !dragRef.current.active ? 1 : 0;
                hoverMixRef.current[index] = lerp(hoverMixRef.current[index] || 0, hoverTarget, 0.15);
                const hoverMix = hoverMixRef.current[index];

                const baseX = distance * 160;
                const baseY = -distance * 40;
                const baseZ = distance * -45;
                let x = baseX + hoverMix * 70;
                let y = baseY;
                let z = baseZ + hoverMix * 32;
                let rotateY = -10;
                let opacity = clamp(1.12 - absoluteDistance * 0.065, 0.18, 1);

                if (expanded && isSelected) {
                    const targetZ = 620;
                    const projectionScale = perspective / (perspective - targetZ);
                    const perspectiveOriginX = sceneRect.left + sceneRect.width * 0.04;
                    const perspectiveOriginY = sceneRect.top - sceneRect.height * 1.2;
                    const targetX = (((sceneRect.left + sceneRect.width / 2 - perspectiveOriginX) / projectionScale)
                        + perspectiveOriginX - railCenterX);
                    const targetY = (((sceneRect.top + sceneRect.height / 2 - perspectiveOriginY) / projectionScale)
                        + perspectiveOriginY - railCenterY);
                    x = lerp(baseX, targetX, selectedMixRef.current);
                    y = lerp(baseY, targetY, selectedMixRef.current);
                    z = lerp(baseZ, targetZ, selectedMixRef.current);
                    rotateY = lerp(-10, 0, selectedMixRef.current);
                    opacity = 1;
                } else if (expanded) {
                    const movesLeft = index < activeIndex;
                    x = lerp(baseX, movesLeft ? -2600 : 2600, expandMixRef.current);
                    y = lerp(baseY, movesLeft ? 1750 : -1750, expandMixRef.current);
                    z = lerp(baseZ, -220, expandMixRef.current);
                    opacity = lerp(opacity, 0, expandMixRef.current);
                }

                card.style.setProperty('--four-you-x', `${x}px`);
                card.style.setProperty('--four-you-y', `${y}px`);
                card.style.setProperty('--four-you-z', `${z}px`);
                card.style.setProperty('--four-you-ry', `${rotateY}deg`);
                card.style.opacity = String(opacity);
                card.style.zIndex = String(isSelected ? 3000 : 1000 - Math.round(absoluteDistance * 10));
                card.classList.toggle('is-selected', isSelected && selectedMixRef.current > 0.02);
            });

            const progress = scene.querySelector('[data-four-you-progress]');
            if (progress) {
                progress.style.width = `${max > 0 ? clamp((scrollRef.current / max) * 100, 0, 100) : 100}%`;
            }
            frameId = window.requestAnimationFrame(animate);
        };

        frameId = window.requestAnimationFrame(animate);
        return () => window.cancelAnimationFrame(frameId);
    }, [activeIndex, images]);

    const handleWheel = (event) => {
        if (activeIndex != null) return;
        event.preventDefault();
        const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
        targetRef.current += delta * 0.95;
    };

    const handlePointerDown = (event) => {
        if (event.target.closest('[data-four-you-control]')) return;
        dragRef.current = {
            active: true,
            x: event.clientX,
            y: event.clientY,
            target: targetRef.current,
        };
        event.currentTarget.setPointerCapture?.(event.pointerId);
    };

    const handlePointerMove = (event) => {
        if (!dragRef.current.active || activeIndex != null) return;
        targetRef.current = dragRef.current.target - (event.clientX - dragRef.current.x) * 1.7;
    };

    const handlePointerUp = (event) => {
        if (!dragRef.current.active) return;
        const moved = Math.hypot(event.clientX - dragRef.current.x, event.clientY - dragRef.current.y);
        dragRef.current.active = false;
        if (moved >= 8) return;
        const card = event.target.closest('[data-four-you-card-index]');
        if (!card) {
            setActiveIndex(null);
            return;
        }
        const index = Number(card.dataset.fourYouCardIndex);
        if (!Number.isInteger(index)) return;
        if (activeIndex === index) {
            setActiveIndex(null);
        } else {
            targetRef.current = index * STEP;
            setActiveIndex(index);
        }
    };

    const handleKeyDown = (event) => {
        if (event.key === 'Escape' && activeIndex != null) {
            setActiveIndex(null);
            return;
        }
        if (activeIndex != null) return;
        if (event.key === 'ArrowRight') targetRef.current += STEP;
        if (event.key === 'ArrowLeft') targetRef.current -= STEP;
    };

    const handleUpload = async (event) => {
        const selectedFiles = Array.from(event.target.files || []);
        event.target.value = '';
        if (!selectedFiles.length || !canUpload || isUploading) return;
        if (selectedFiles.length > 20) {
            showToast?.('За один раз можно загрузить не более 20 изображений', 'error');
            return;
        }
        const body = new FormData();
        selectedFiles.forEach((file) => body.append('images', file));
        setIsUploading(true);
        setUploadProgress(0);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/four_you/images`, body, {
                headers: authHeaders(),
                onUploadProgress: (progressEvent) => {
                    if (!progressEvent.total) return;
                    setUploadProgress(Math.round((progressEvent.loaded / progressEvent.total) * 100));
                },
            });
            const rows = Array.isArray(response?.data?.images) ? response.data.images : [];
            setImages(rows);
            hoverMixRef.current = new Array(rows.length).fill(0);
            setActiveIndex(rows.length ? rows.length - 1 : null);
            if (rows.length) targetRef.current = (rows.length - 1) * STEP;
            showToast?.(`Загружено изображений: ${selectedFiles.length}`, 'success');
        } catch (uploadError) {
            showToast?.(uploadError?.response?.data?.error || 'Не удалось загрузить изображения', 'error');
        } finally {
            setIsUploading(false);
            setUploadProgress(0);
        }
    };

    const deleteActiveImage = async () => {
        const image = activeIndex == null ? null : images[activeIndex];
        if (!image || !canUpload || deletingId) return;
        if (!window.confirm('Удалить это изображение из 4 You?')) return;
        setDeletingId(image.id);
        try {
            await axios.delete(`${apiBaseUrl}/api/four_you/images/${encodeURIComponent(image.id)}`, {
                headers: authHeaders(),
            });
            const nextImages = images.filter((item) => item.id !== image.id);
            setImages(nextImages);
            setActiveIndex(null);
            hoverMixRef.current = new Array(nextImages.length).fill(0);
            targetRef.current = clamp(targetRef.current, 0, Math.max(0, (nextImages.length - 1) * STEP));
            showToast?.('Изображение удалено', 'success');
        } catch (deleteError) {
            showToast?.(deleteError?.response?.data?.error || 'Не удалось удалить изображение', 'error');
        } finally {
            setDeletingId('');
        }
    };

    return (
        <section
            ref={sceneRef}
            className={`four-you-view ${dragRef.current.active ? 'is-dragging' : ''}`}
            tabIndex={0}
            aria-label="4 You"
            onWheel={handleWheel}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={() => { dragRef.current.active = false; }}
            onKeyDown={handleKeyDown}
        >
            <header className="four-you-header" data-four-you-control>
                <div className="four-you-title">
                    <span className="four-you-heart" aria-hidden="true">♥</span>
                    <span>4 You</span>
                </div>
                {canUpload && (
                    <div className="four-you-actions">
                        {activeIndex != null && images[activeIndex] && (
                            <button
                                type="button"
                                className="four-you-control four-you-delete"
                                onClick={deleteActiveImage}
                                disabled={Boolean(deletingId)}
                            >
                                <FaIcon className={`fas ${deletingId ? 'fa-spinner fa-spin' : 'fa-trash-alt'}`} />
                                <span>Удалить</span>
                            </button>
                        )}
                        <button
                            type="button"
                            className="four-you-control four-you-upload"
                            onClick={() => fileInputRef.current?.click()}
                            disabled={isUploading}
                        >
                            <FaIcon className={`fas ${isUploading ? 'fa-spinner fa-spin' : 'fa-plus'}`} />
                            <span>{isUploading ? `${uploadProgress}%` : 'Добавить фото'}</span>
                        </button>
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="image/jpeg,image/png,image/webp"
                            multiple
                            hidden
                            onChange={handleUpload}
                        />
                    </div>
                )}
            </header>

            {isLoading ? (
                <div className="four-you-status" data-four-you-control>
                    <span className="four-you-loader" />
                    <span>Загружаю 4 You…</span>
                </div>
            ) : error ? (
                <div className="four-you-status four-you-error" data-four-you-control>
                    <FaIcon className="fas fa-lock" />
                    <span>{error}</span>
                </div>
            ) : images.length === 0 ? (
                <div className="four-you-status four-you-empty" data-four-you-control>
                    <span className="four-you-empty-heart">♡</span>
                    <strong>Здесь появятся ваши фотографии</strong>
                    <span>{canUpload ? 'Загрузите первые изображения — без демонстрационных карточек.' : 'Фотографии пока не загружены.'}</span>
                    {canUpload && (
                        <button type="button" onClick={() => fileInputRef.current?.click()}>
                            Выбрать изображения
                        </button>
                    )}
                </div>
            ) : (
                <div ref={railRef} className="four-you-rail" aria-live="polite">
                    {images.map((image, index) => {
                        const isSelected = activeIndex === index;
                        return (
                            <article
                                key={image.id}
                                ref={(element) => { cardRefs.current[index] = element; }}
                                data-four-you-card-index={index}
                                className="four-you-card"
                                onMouseEnter={() => { hoverIndexRef.current = index; }}
                                onMouseLeave={() => {
                                    if (hoverIndexRef.current === index) hoverIndexRef.current = null;
                                }}
                                aria-label={`Фото ${index + 1} из ${images.length}`}
                            >
                                <img
                                    src={isSelected ? image.display_url : image.preview_url}
                                    alt=""
                                    loading={index < 4 ? 'eager' : 'lazy'}
                                    decoding="async"
                                    fetchPriority={index < 2 ? 'high' : 'auto'}
                                    draggable="false"
                                />
                                <span className="four-you-card-number">{String(index + 1).padStart(2, '0')}</span>
                            </article>
                        );
                    })}
                </div>
            )}

            <div className="four-you-progress" aria-hidden="true">
                <span data-four-you-progress />
            </div>
        </section>
    );
};

export default FourYouView;
