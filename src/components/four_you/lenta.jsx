import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import './lenta.css';

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (value) => {
    const t = clamp(value, 0, 1);
    return t * t * (3 - 2 * t);
};

// Значения сохранены из unveil_scroll_demo_split_all_together_faster_selected.html.
const PARAMS = Object.freeze({
    perspective: 4000,
    step: 160,
    dirX: 160,
    dirY: 40,
    dirZ: -45,
    depthFade: 0,
    cardRotX: 0,
    cardRotY: -10,
    cardRotZ: 0,
    hoverX: 70,
    hoverZComp: 32,
    selectedZ: 620,
    selectedScale: 1.0,
    leftDownX: -2600,
    leftDownY: 1750,
    rightUpX: 2600,
    rightUpY: -1750,
    splitZ: -220,
    railRotX: 0,
    railRotY: 0,
    railRotZ: 0,
});

const Lenta = ({ user, apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const sceneRef = useRef(null);
    const railRef = useRef(null);
    const progressRef = useRef(null);
    const cardRefs = useRef([]);
    const fileInputRef = useRef(null);
    const targetRef = useRef(PARAMS.step * 2.8);
    const scrollRef = useRef(PARAMS.step * 2.8);
    const draggingRef = useRef(false);
    const dragStartXRef = useRef(0);
    const dragStartYRef = useRef(0);
    const dragStartTargetRef = useRef(0);
    const activeIndexRef = useRef(null);
    const expandedRef = useRef(false);
    const hoveredRef = useRef(null);
    const expandMixRef = useRef(0);
    const selectedMixRef = useRef(0);
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
            targetRef.current = PARAMS.step * 2.8;
            scrollRef.current = targetRef.current;
        } catch (requestError) {
            if (requestError?.code === 'ERR_CANCELED') return;
            setError(requestError?.response?.status === 403
                ? 'У вас нет доступа к разделу 4 You'
                : (requestError?.response?.data?.error || 'Не удалось загрузить изображения'));
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
        const preloaded = images.map((item) => {
            const image = new Image();
            image.decoding = 'async';
            image.src = item.preview_url;
            return image;
        });
        return () => preloaded.forEach((image) => { image.src = ''; });
    }, [images]);

    useEffect(() => {
        if (activeIndex == null) return undefined;
        const indexes = [activeIndex - 1, activeIndex, activeIndex + 1]
            .filter((index) => index >= 0 && index < images.length);
        const preloaded = indexes.map((index) => {
            const image = new Image();
            image.decoding = 'async';
            image.src = images[index].display_url;
            return image;
        });
        return () => preloaded.forEach((image) => { image.src = ''; });
    }, [activeIndex, images]);

    const setCardClasses = useCallback(() => {
        cardRefs.current.forEach((card, index) => {
            if (!card) return;
            card.classList.toggle(
                'is-selected',
                activeIndexRef.current === index && selectedMixRef.current > 0.02
            );
        });
    }, []);

    const openCard = useCallback((index) => {
        activeIndexRef.current = index;
        expandedRef.current = true;
        targetRef.current = index * PARAMS.step;
        setActiveIndex(index);
        setCardClasses();
    }, [setCardClasses]);

    const closeCard = useCallback(() => {
        expandedRef.current = false;
    }, []);

    useEffect(() => {
        if (!images.length) return undefined;
        let animationFrame = 0;

        const getRailScreenCenter = () => {
            const rect = railRef.current.getBoundingClientRect();
            return { x: rect.left + rect.width * 0.5, y: rect.top + rect.height * 0.5 };
        };

        const updateCards = () => {
            const scene = sceneRef.current;
            const rail = railRef.current;
            if (!scene || !rail) return;
            const max = (images.length - 1) * PARAMS.step;
            const activeFloat = scrollRef.current / PARAMS.step;
            const railCenter = getRailScreenCenter();

            for (let index = 0; index < images.length; index += 1) {
                const card = cardRefs.current[index];
                if (!card) continue;
                const distance = index - activeFloat;
                const absoluteDistance = Math.abs(distance);
                const isActive = activeIndexRef.current === index;
                const hasActive = activeIndexRef.current !== null;
                const hoverTarget = hoveredRef.current === index && !expandedRef.current && !draggingRef.current ? 1 : 0;
                hoverMixRef.current[index] = lerp(hoverMixRef.current[index] || 0, hoverTarget, 0.15);

                const baseX = distance * PARAMS.dirX;
                const baseY = -distance * PARAMS.dirY;
                const baseZ = (distance * PARAMS.dirZ) - (absoluteDistance * PARAMS.depthFade);
                let x = baseX + hoverMixRef.current[index] * PARAMS.hoverX;
                let y = baseY;
                let z = baseZ + hoverMixRef.current[index] * PARAMS.hoverZComp;
                let rotateX = PARAMS.cardRotX;
                let rotateY = PARAMS.cardRotY;
                let rotateZ = PARAMS.cardRotZ;
                let scale = 1;
                let opacity = clamp(1.12 - absoluteDistance * 0.065, 0.18, 1);

                if (hasActive) {
                    if (isActive) {
                        const targetZ = PARAMS.selectedZ;
                        const projectionScale = PARAMS.perspective / (PARAMS.perspective - targetZ);
                        const sceneRect = scene.getBoundingClientRect();
                        const perspectiveOriginX = sceneRect.left + sceneRect.width * 0.04;
                        const perspectiveOriginY = sceneRect.top - sceneRect.height * 1.20;
                        const targetX = (((window.innerWidth * 0.5 - perspectiveOriginX) / projectionScale)
                            + perspectiveOriginX - railCenter.x);
                        const targetY = (((window.innerHeight * 0.5 - perspectiveOriginY) / projectionScale)
                            + perspectiveOriginY - railCenter.y);

                        x = lerp(baseX, targetX, selectedMixRef.current);
                        y = lerp(baseY, targetY, selectedMixRef.current);
                        z = lerp(baseZ, targetZ, selectedMixRef.current);
                        rotateX = lerp(PARAMS.cardRotX, 0, selectedMixRef.current);
                        rotateY = lerp(PARAMS.cardRotY, 0, selectedMixRef.current);
                        rotateZ = lerp(PARAMS.cardRotZ, 0, selectedMixRef.current);
                        scale = lerp(1, PARAMS.selectedScale, selectedMixRef.current);
                        opacity = 1;
                    } else {
                        const leftSide = index < activeIndexRef.current;
                        const targetX = leftSide ? PARAMS.leftDownX : PARAMS.rightUpX;
                        const targetY = leftSide ? PARAMS.leftDownY : PARAMS.rightUpY;
                        const localOpen = smooth(expandMixRef.current);
                        x = lerp(baseX, targetX, localOpen);
                        y = lerp(baseY, targetY, localOpen);
                        z = lerp(baseZ, PARAMS.splitZ, localOpen);
                        opacity = lerp(opacity, 0, localOpen);
                    }
                }

                card.style.setProperty('--x', `${x}px`);
                card.style.setProperty('--y', `${y}px`);
                card.style.setProperty('--z', `${z}px`);
                card.style.setProperty('--rx', `${rotateX}deg`);
                card.style.setProperty('--ry', `${rotateY}deg`);
                card.style.setProperty('--rz', `${rotateZ}deg`);
                card.style.setProperty('--s', scale);
                card.style.opacity = opacity;

                let zIndex = 1000 - Math.round(absoluteDistance * 10) + Math.round(hoverMixRef.current[index] * 80);
                if (hasActive && !isActive) zIndex = 400 - Math.round(absoluteDistance * 4);
                if (isActive) zIndex = 3000;
                card.style.zIndex = String(zIndex);
            }

            const progress = max <= 0 ? 0 : (scrollRef.current / max) * 100;
            if (progressRef.current) progressRef.current.style.width = `${clamp(progress, 0, 100)}%`;
        };

        const animate = () => {
            const max = (images.length - 1) * PARAMS.step;
            targetRef.current = clamp(targetRef.current, 0, max);
            scrollRef.current = lerp(scrollRef.current, targetRef.current, 0.11);
            if (Math.abs(scrollRef.current - targetRef.current) < 0.025) scrollRef.current = targetRef.current;

            const expandTarget = expandedRef.current ? 1 : 0;
            expandMixRef.current = lerp(expandMixRef.current, expandTarget, 0.036);
            selectedMixRef.current = lerp(selectedMixRef.current, expandTarget, 0.13);
            if (Math.abs(expandMixRef.current - expandTarget) < 0.002) expandMixRef.current = expandTarget;
            if (Math.abs(selectedMixRef.current - expandTarget) < 0.002) selectedMixRef.current = expandTarget;

            if (!expandedRef.current && activeIndexRef.current !== null
                && expandMixRef.current <= 0.002 && selectedMixRef.current <= 0.002) {
                activeIndexRef.current = null;
                setActiveIndex(null);
            }

            if (railRef.current) {
                railRef.current.style.transform = `translate3d(0,0,0) rotateX(${PARAMS.railRotX}deg) rotateY(${PARAMS.railRotY}deg) rotateZ(${PARAMS.railRotZ}deg)`;
            }
            setCardClasses();
            updateCards();
            animationFrame = window.requestAnimationFrame(animate);
        };

        animationFrame = window.requestAnimationFrame(animate);
        return () => window.cancelAnimationFrame(animationFrame);
    }, [images, setCardClasses]);

    useEffect(() => {
        const handleKeyDown = (event) => {
            if (event.key === 'Escape' && expandedRef.current) closeCard();
            if (expandedRef.current) return;
            if (event.key === 'ArrowRight') targetRef.current += PARAMS.step;
            if (event.key === 'ArrowLeft') targetRef.current -= PARAMS.step;
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [closeCard]);

    const handleWheel = (event) => {
        if (expandedRef.current) return;
        const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
        targetRef.current += delta * 0.95;
    };

    const handlePointerDown = (event) => {
        if (event.target.closest('[data-lenta-control]')) return;
        draggingRef.current = true;
        dragStartXRef.current = event.clientX;
        dragStartYRef.current = event.clientY;
        dragStartTargetRef.current = targetRef.current;
        sceneRef.current?.classList.add('is-dragging');
        event.currentTarget.setPointerCapture?.(event.pointerId);
    };

    const handlePointerMove = (event) => {
        if (!draggingRef.current || expandedRef.current) return;
        targetRef.current = dragStartTargetRef.current - (event.clientX - dragStartXRef.current) * 1.7;
    };

    const handlePointerUp = (event) => {
        if (!draggingRef.current) return;
        const moved = Math.hypot(event.clientX - dragStartXRef.current, event.clientY - dragStartYRef.current);
        draggingRef.current = false;
        sceneRef.current?.classList.remove('is-dragging');
        if (moved < 8) {
            const card = event.target.closest('[data-lenta-card-index]');
            if (card) {
                const index = Number(card.dataset.lentaCardIndex);
                if (expandedRef.current && activeIndexRef.current === index) closeCard();
                else openCard(index);
            } else if (expandedRef.current) {
                closeCard();
            }
        }
    };

    const cancelPointer = () => {
        draggingRef.current = false;
        sceneRef.current?.classList.remove('is-dragging');
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
                    if (progressEvent.total) setUploadProgress(Math.round((progressEvent.loaded / progressEvent.total) * 100));
                },
            });
            const rows = Array.isArray(response?.data?.images) ? response.data.images : [];
            setImages(rows);
            hoverMixRef.current = new Array(rows.length).fill(0);
            if (rows.length) openCard(rows.length - 1);
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
            activeIndexRef.current = null;
            expandedRef.current = false;
            expandMixRef.current = 0;
            selectedMixRef.current = 0;
            setActiveIndex(null);
            setImages(nextImages);
            hoverMixRef.current = new Array(nextImages.length).fill(0);
            targetRef.current = clamp(targetRef.current, 0, Math.max(0, (nextImages.length - 1) * PARAMS.step));
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
            className="lenta-scene"
            aria-label="4 You"
            onWheel={handleWheel}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={cancelPointer}
        >
            {canUpload && (
                <div className="lenta-admin-controls" data-lenta-control>
                    {activeIndex != null && images[activeIndex] && (
                        <button type="button" onClick={deleteActiveImage} disabled={Boolean(deletingId)}>
                            <FaIcon className={`fas ${deletingId ? 'fa-spinner fa-spin' : 'fa-trash-alt'}`} />
                            <span>Удалить</span>
                        </button>
                    )}
                    <button type="button" onClick={() => fileInputRef.current?.click()} disabled={isUploading}>
                        <FaIcon className={`fas ${isUploading ? 'fa-spinner fa-spin' : 'fa-plus'}`} />
                        <span>{isUploading ? `${uploadProgress}%` : 'Добавить фото'}</span>
                    </button>
                    <input ref={fileInputRef} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden onChange={handleUpload} />
                </div>
            )}

            {isLoading ? (
                <div className="lenta-status" data-lenta-control>Загрузка…</div>
            ) : error ? (
                <div className="lenta-status lenta-error" data-lenta-control>{error}</div>
            ) : images.length === 0 ? (
                <div className="lenta-status lenta-empty" data-lenta-control>
                    <span>{canUpload ? 'Загрузите первые изображения' : 'Фотографии пока не загружены'}</span>
                    {canUpload && <button type="button" onClick={() => fileInputRef.current?.click()}>Выбрать изображения</button>}
                </div>
            ) : (
                <div ref={railRef} className="lenta-rail">
                    {images.map((image, index) => (
                        <article
                            key={image.id}
                            ref={(element) => { cardRefs.current[index] = element; }}
                            className="lenta-card"
                            data-lenta-card-index={index}
                            onMouseEnter={() => {
                                if (!expandedRef.current) hoveredRef.current = index;
                            }}
                            onMouseLeave={() => {
                                if (hoveredRef.current === index) hoveredRef.current = null;
                            }}
                        >
                            <img
                                src={activeIndex === index ? image.display_url : image.preview_url}
                                alt=""
                                loading={index < 4 ? 'eager' : 'lazy'}
                                decoding="async"
                                fetchPriority={index < 2 ? 'high' : 'auto'}
                                draggable="false"
                            />
                        </article>
                    ))}
                </div>
            )}

            <div className="lenta-progress" aria-hidden="true"><span ref={progressRef} /></div>
        </section>
    );
};

export default Lenta;
