import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import AnnotationLayer from './AnnotationLayer';
import Backgrounds from './Backgrounds';
import PhotoEditor from './PhotoEditor';
import { normalizeAnnotations, hasVisibleAnnotations, userName } from './annotations';
import './lenta.css';

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (value) => {
    const t = clamp(value, 0, 1);
    return t * t * (3 - 2 * t);
};

// Fisher–Yates: случайный порядок фото при каждом открытии ленты.
const shuffle = (input) => {
    const items = input.slice();
    for (let i = items.length - 1; i > 0; i -= 1) {
        const j = Math.floor(Math.random() * (i + 1));
        [items[i], items[j]] = [items[j], items[i]];
    }
    return items;
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

const Lenta = ({ user, apiBaseUrl, withAccessTokenHeader, showToast, onSeen }) => {
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
    const needsRenderRef = useRef(true);
    const loopRef = useRef(false);          // бесконечная прокрутка (когда фото достаточно)
    const countRef = useRef(0);
    const revealRef = useRef({});           // id → 0..1: появление «съезжанием» к середине
    const loadedRef = useRef({});           // id → true когда фото декодировано
    const revealActiveRef = useRef(false);  // идёт ли сейчас появление
    const pollCursorRef = useRef('');       // курсор поллинга разметки (annotations_updated_at)
    const editingIdRef = useRef(null);      // id фото, открытого в редакторе (его не трогает поллинг)

    const [images, setImages] = useState([]);
    const [activeIndex, setActiveIndex] = useState(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState('');
    const [canUpload, setCanUpload] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [uploadProgress, setUploadProgress] = useState(0);
    const [deletingId, setDeletingId] = useState('');
    const [selectMode, setSelectMode] = useState(false);
    const [selectedIds, setSelectedIds] = useState(() => new Set());
    const [isBulkDeleting, setIsBulkDeleting] = useState(false);
    const [editorOpen, setEditorOpen] = useState(false);

    countRef.current = images.length;

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
            const rows = shuffle(Array.isArray(response?.data?.images) ? response.data.images : []);
            setImages(rows);
            setCanUpload(Boolean(response?.data?.can_upload));
            hoverMixRef.current = new Array(rows.length).fill(0);
            pollCursorRef.current = rows.reduce(
                (max, row) => (row.annotations_updated_at && (!max || row.annotations_updated_at > max)
                    ? row.annotations_updated_at : max),
                '',
            );
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

    // Открытие раздела = «просмотрено»: гасим серверный счётчик новых фото и
    // бейдж в сайдбаре (как в «Ивентах»). Бейдж не критичен — сбои игнорируем.
    useEffect(() => {
        let cancelled = false;
        axios.post(`${apiBaseUrl}/api/four_you/seen`, {}, { headers: authHeaders() })
            .then(() => { if (!cancelled && typeof onSeen === 'function') onSeen(); })
            .catch(() => { /* noop */ });
        return () => { cancelled = true; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

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
        if (loopRef.current && countRef.current > 0) {
            // Прокрутка к ближайшей «копии» карточки в зацикленной ленте — без длинного отката.
            const period = countRef.current * PARAMS.step;
            const baseTarget = index * PARAMS.step;
            const k = Math.round((scrollRef.current - baseTarget) / period);
            targetRef.current = baseTarget + k * period;
        } else {
            targetRef.current = index * PARAMS.step;
        }
        setActiveIndex(index);
        setCardClasses();
    }, [setCardClasses]);

    const closeCard = useCallback(() => {
        expandedRef.current = false;
    }, []);

    useEffect(() => {
        if (!images.length) return undefined;
        let animationFrame = 0;
        // Кэш применённых z-index/display, чтобы не дёргать стили зря.
        const lastZ = new Array(images.length).fill(null);
        const lastDisplay = new Array(images.length).fill('');

        const computeCull = () => Math.ceil((window.innerWidth * 0.5 + 470) / PARAMS.dirX) + 2;

        const getRailScreenCenter = () => {
            const rect = railRef.current.getBoundingClientRect();
            return { x: rect.left + rect.width * 0.5, y: rect.top + rect.height * 0.5 };
        };

        const updateCards = () => {
            const scene = sceneRef.current;
            const rail = railRef.current;
            if (!scene || !rail) return;
            const n = images.length;
            const activeFloat = scrollRef.current / PARAMS.step;
            const railCenter = getRailScreenCenter();
            const hasActive = activeIndexRef.current !== null;
            // Окно видимости: карточки за краем экрана не рисуем (геометрия видимых не меняется).
            const cullRadius = computeCull();
            const loop = loopRef.current;
            const sceneRect = hasActive ? scene.getBoundingClientRect() : null;
            let stillRevealing = false;

            for (let index = 0; index < n; index += 1) {
                const card = cardRefs.current[index];
                if (!card) continue;
                // Зацикливание: дистанцию заворачиваем в (-n/2, n/2], шов всегда за окном видимости.
                let distance = index - activeFloat;
                if (loop) {
                    distance = ((distance % n) + n) % n;
                    if (distance > n / 2) distance -= n;
                }
                const absoluteDistance = Math.abs(distance);
                const isActive = activeIndexRef.current === index;

                if (!isActive && absoluteDistance > cullRadius) {
                    if (lastDisplay[index] !== 'none') { card.style.display = 'none'; lastDisplay[index] = 'none'; }
                    continue;
                }
                if (lastDisplay[index] !== '') { card.style.display = ''; lastDisplay[index] = ''; }

                const hoverTarget = hoveredRef.current === index && !expandedRef.current && !draggingRef.current ? 1 : 0;
                let hoverMix = lerp(hoverMixRef.current[index] || 0, hoverTarget, 0.15);
                if (hoverTarget === 0 && hoverMix < 0.001) hoverMix = 0;
                else if (hoverTarget === 1 && hoverMix > 0.999) hoverMix = 1;
                hoverMixRef.current[index] = hoverMix;

                const baseX = distance * PARAMS.dirX;
                const baseY = -distance * PARAMS.dirY;
                const baseZ = (distance * PARAMS.dirZ) - (absoluteDistance * PARAMS.depthFade);
                let x = baseX + hoverMix * PARAMS.hoverX;
                let y = baseY;
                let z = baseZ + hoverMix * PARAMS.hoverZComp;
                let rotateX = PARAMS.cardRotX;
                let rotateY = PARAMS.cardRotY;
                let rotateZ = PARAMS.cardRotZ;
                let scale = 1;
                let opacity = clamp(1.12 - absoluteDistance * 0.065, 0.18, 1);

                if (hasActive) {
                    if (isActive) {
                        const targetZ = PARAMS.selectedZ;
                        const projectionScale = PARAMS.perspective / (PARAMS.perspective - targetZ);
                        const perspectiveOriginX = sceneRect.left + sceneRect.width * 0.04;
                        const perspectiveOriginY = sceneRect.top - sceneRect.height * 1.20;
                        const targetX = (((window.innerWidth * 0.5 - perspectiveOriginX) / projectionScale)
                            + perspectiveOriginX - railCenter.x);
                        const targetY = (((window.innerHeight * 0.5 - perspectiveOriginY) / projectionScale)
                            + perspectiveOriginY - railCenter.y);
                        const sm = selectedMixRef.current;
                        x = lerp(baseX, targetX, sm);
                        y = lerp(baseY, targetY, sm);
                        z = lerp(baseZ, targetZ, sm);
                        rotateX = lerp(PARAMS.cardRotX, 0, sm);
                        rotateY = lerp(PARAMS.cardRotY, 0, sm);
                        rotateZ = lerp(PARAMS.cardRotZ, 0, sm);
                        scale = lerp(1, PARAMS.selectedScale, sm);
                        opacity = 1;
                    } else {
                        const leftSide = distance < 0;
                        const targetX = leftSide ? PARAMS.leftDownX : PARAMS.rightUpX;
                        const targetY = leftSide ? PARAMS.leftDownY : PARAMS.rightUpY;
                        const localOpen = smooth(expandMixRef.current);
                        x = lerp(baseX, targetX, localOpen);
                        y = lerp(baseY, targetY, localOpen);
                        z = lerp(baseZ, PARAMS.splitZ, localOpen);
                        opacity = lerp(opacity, 0, localOpen);
                    }
                }

                // Появление: пока фото карточки не загрузилось, держим её за своей
                // стороной (reveal=0, прозрачно); после загрузки reveal едет 0→1 и
                // карточка «съезжает» к центру вместе с уже готовой картинкой.
                if (!isActive) {
                    const id = images[index].id;
                    let reveal = revealRef.current[id] === undefined ? 0 : revealRef.current[id];
                    // Двигаем появление только для ВИДИМЫХ (прошедших куллинг) и уже
                    // загруженных карточек: ушедшая за экран и возвращённая карточка
                    // корректно «въезжает» со своей стороны, когда снова видна.
                    if (reveal < 1 && loadedRef.current[id]) {
                        reveal = Math.min(1, reveal + 0.035);
                        revealRef.current[id] = reveal;
                        if (reveal < 1) stillRevealing = true;
                    }
                    if (reveal < 1) {
                        const r = smooth(reveal);
                        const leftSide = distance < 0;
                        const sx = leftSide ? PARAMS.leftDownX : PARAMS.rightUpX;
                        const sy = leftSide ? PARAMS.leftDownY : PARAMS.rightUpY;
                        x = lerp(sx, x, r);
                        y = lerp(sy, y, r);
                        z = lerp(PARAMS.splitZ, z, r);
                        opacity *= r;
                    }
                }

                // Один transform вместо семи CSS-переменных — меньше записей за кадр.
                card.style.transform = `translate3d(${x}px, ${y}px, ${z}px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) rotateZ(${rotateZ}deg) scale(${scale})`;
                card.style.opacity = opacity;

                let zIndex = 1000 - Math.round(absoluteDistance * 10) + Math.round(hoverMix * 80);
                if (hasActive && !isActive) zIndex = 400 - Math.round(absoluteDistance * 4);
                if (isActive) zIndex = 3000;
                if (lastZ[index] !== zIndex) { card.style.zIndex = String(zIndex); lastZ[index] = zIndex; }
            }

            revealActiveRef.current = stillRevealing;

            let progress;
            if (loop) {
                const position = (((scrollRef.current / PARAMS.step) % n) + n) % n;
                progress = (position / n) * 100;
            } else {
                const max = (n - 1) * PARAMS.step;
                progress = max <= 0 ? 0 : (scrollRef.current / max) * 100;
            }
            if (progressRef.current) progressRef.current.style.width = `${clamp(progress, 0, 100)}%`;
        };

        // Лента «в покое»: ничего не движется — тяжёлую отрисовку пропускаем.
        const isSettled = () => {
            if (revealActiveRef.current) return false;
            if (scrollRef.current !== targetRef.current) return false;
            const expandTarget = expandedRef.current ? 1 : 0;
            if (expandMixRef.current !== expandTarget || selectedMixRef.current !== expandTarget) return false;
            const mixes = hoverMixRef.current;
            for (let i = 0; i < mixes.length; i += 1) {
                const want = (hoveredRef.current === i && !expandedRef.current && !draggingRef.current) ? 1 : 0;
                if ((mixes[i] || 0) !== want) return false;
            }
            return true;
        };

        const animate = () => {
            const cullRadius = computeCull();
            loopRef.current = images.length >= 2 * cullRadius;
            const max = (images.length - 1) * PARAMS.step;
            if (!loopRef.current) targetRef.current = clamp(targetRef.current, 0, max);
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
                needsRenderRef.current = true;
            }

            if (railRef.current) {
                railRef.current.style.transform = `translate3d(0,0,0) rotateX(${PARAMS.railRotX}deg) rotateY(${PARAMS.railRotY}deg) rotateZ(${PARAMS.railRotZ}deg)`;
            }

            if (needsRenderRef.current || !isSettled()) {
                setCardClasses();
                updateCards();
                // Фон затухает/появляется синхронно с активной карточкой: его
                // прозрачность = selectedMix (0 закрыто → 1 открыто). expandMix
                // (медленный) держит карточку «активной» дольше, чем длится
                // затухание, поэтому к моменту размонтирования фон уже невидим.
                if (sceneRef.current) {
                    sceneRef.current.style.setProperty('--fy-bg-opacity', String(selectedMixRef.current));
                }
                if (isSettled()) needsRenderRef.current = false;
            }
            animationFrame = window.requestAnimationFrame(animate);
        };

        const handleResize = () => { needsRenderRef.current = true; };
        window.addEventListener('resize', handleResize);
        needsRenderRef.current = true;
        animationFrame = window.requestAnimationFrame(animate);
        return () => {
            window.removeEventListener('resize', handleResize);
            window.cancelAnimationFrame(animationFrame);
        };
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

    // Near-real-time синхронизация разметки между двумя пользователями: лёгкий
    // поллинг «что изменилось после курсора» (+ при возврате фокуса на вкладку).
    useEffect(() => {
        let stopped = false;
        let inFlight = false;
        const poll = async () => {
            if (stopped || inFlight || typeof document !== 'undefined' && document.hidden) return;
            inFlight = true;
            try {
                const since = pollCursorRef.current || '';
                const response = await axios.get(`${apiBaseUrl}/api/four_you/annotations/poll`, {
                    headers: authHeaders(),
                    params: since ? { since } : {},
                });
                if (stopped) return;
                const items = Array.isArray(response?.data?.items) ? response.data.items : [];
                if (items.length) {
                    const byId = new Map(items.map((item) => [item.id, item]));
                    let cursor = pollCursorRef.current || '';
                    items.forEach((item) => {
                        if (item.annotations_updated_at && (!cursor || item.annotations_updated_at > cursor)) {
                            cursor = item.annotations_updated_at;
                        }
                    });
                    pollCursorRef.current = cursor;
                    setImages((prev) => prev.map((img) => (byId.has(img.id) && img.id !== editingIdRef.current
                        ? { ...img, annotations: byId.get(img.id).annotations, annotations_updated_at: byId.get(img.id).annotations_updated_at }
                        : img)));
                }
            } catch (pollError) {
                /* поллинг тихий — не мешаем работе при сетевых сбоях */
            } finally {
                inFlight = false;
            }
        };
        const interval = window.setInterval(poll, 3000);
        const onFocus = () => poll();
        window.addEventListener('focus', onFocus);
        return () => {
            stopped = true;
            window.clearInterval(interval);
            window.removeEventListener('focus', onFocus);
        };
    }, [apiBaseUrl, authHeaders]);

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
        // ВАЖНО: без setPointerCapture. Захват указателя перенаправлял бы
        // pointerup на <section>, и event.target переставал быть карточкой —
        // тогда openCard не вызывается и фото не открывается по клику.
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
        if (moved >= 8) return;
        const card = event.target.closest('[data-lenta-card-index]');
        if (selectMode) {
            if (card) toggleSelect(Number(card.dataset.lentaCardIndex));
            return;
        }
        if (card) {
            const index = Number(card.dataset.lentaCardIndex);
            if (expandedRef.current && activeIndexRef.current === index) closeCard();
            else {
                if (images[index]) revealRef.current[images[index].id] = 1;
                openCard(index);
            }
        } else if (expandedRef.current) {
            closeCard();
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
            const prevIds = new Set(images.map((item) => item.id));
            const added = rows.filter((item) => !prevIds.has(item.id));
            const next = [...images, ...added];
            hoverMixRef.current = new Array(next.length).fill(0);
            setImages(next);
            if (added.length) openCard(next.length - 1);
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

    const toggleSelect = (index) => {
        const image = images[index];
        if (!image) return;
        setSelectedIds((prev) => {
            const next = new Set(prev);
            if (next.has(image.id)) next.delete(image.id);
            else next.add(image.id);
            return next;
        });
    };

    const enterSelectMode = () => {
        expandedRef.current = false;
        setSelectedIds(new Set());
        setSelectMode(true);
    };

    const exitSelectMode = () => {
        setSelectMode(false);
        setSelectedIds(new Set());
    };

    const toggleSelectAll = () => {
        setSelectedIds((prev) => (
            prev.size === images.length ? new Set() : new Set(images.map((item) => item.id))
        ));
    };

    const deleteSelected = async () => {
        if (!canUpload || isBulkDeleting || selectedIds.size === 0) return;
        const ids = Array.from(selectedIds);
        if (!window.confirm(`Удалить выбранные фото (${ids.length})?`)) return;
        setIsBulkDeleting(true);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/four_you/images/delete_batch`, { ids }, {
                headers: authHeaders(),
            });
            const deletedSet = new Set(response?.data?.deleted_ids || ids);
            const next = images.filter((item) => !deletedSet.has(item.id));
            activeIndexRef.current = null;
            expandedRef.current = false;
            expandMixRef.current = 0;
            selectedMixRef.current = 0;
            setActiveIndex(null);
            setImages(next);
            hoverMixRef.current = new Array(next.length).fill(0);
            targetRef.current = clamp(targetRef.current, 0, Math.max(0, (next.length - 1) * PARAMS.step));
            setSelectedIds(new Set());
            setSelectMode(false);
            showToast?.(`Удалено фото: ${response?.data?.deleted_count ?? deletedSet.size}`, 'success');
        } catch (deleteError) {
            showToast?.(deleteError?.response?.data?.error || 'Не удалось удалить выбранные фото', 'error');
        } finally {
            setIsBulkDeleting(false);
        }
    };

    const saveAnnotations = async (annotations) => {
        const image = activeIndex == null ? null : images[activeIndex];
        if (!image) return;
        try {
            const response = await axios.put(
                `${apiBaseUrl}/api/four_you/images/${encodeURIComponent(image.id)}/annotations`,
                { annotations },
                { headers: authHeaders() },
            );
            const saved = response?.data?.annotations || annotations;
            const ts = response?.data?.annotations_updated_at;
            if (ts && (!pollCursorRef.current || ts > pollCursorRef.current)) pollCursorRef.current = ts;
            setImages((prev) => prev.map((img) => (img.id === image.id
                ? { ...img, annotations: saved, annotations_updated_at: ts || img.annotations_updated_at }
                : img)));
            editingIdRef.current = null;
            setEditorOpen(false);
            showToast?.('Разметка сохранена', 'success');
        } catch (saveError) {
            showToast?.(saveError?.response?.data?.error || 'Не удалось сохранить разметку', 'error');
            throw saveError;
        }
    };

    const activeImage = activeIndex == null ? null : images[activeIndex];
    const activeBackground = activeImage ? (activeImage.annotations?.background || 'none') : 'none';

    return (
        <section
            ref={sceneRef}
            className={`lenta-scene${activeBackground !== 'none' ? ` lenta-scene--bg lenta-scene--bg-${activeBackground}` : ''}`}
            aria-label="4 You"
            onWheel={handleWheel}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={cancelPointer}
        >
            <Backgrounds bg={activeBackground} />

            {canUpload && (
                <div className="lenta-admin-controls" data-lenta-control>
                    {!selectMode ? (
                        <>
                            <button type="button" onClick={() => fileInputRef.current?.click()} disabled={isUploading}>
                                <FaIcon className={`fas ${isUploading ? 'fa-spinner fa-spin' : 'fa-plus'}`} />
                                <span>{isUploading ? `${uploadProgress}%` : 'Добавить фото'}</span>
                            </button>
                            {images.length > 0 && (
                                <button type="button" onClick={enterSelectMode}>
                                    <FaIcon className="fas fa-check-square" />
                                    <span>Выбрать</span>
                                </button>
                            )}
                        </>
                    ) : (
                        <>
                            <button type="button" onClick={toggleSelectAll}>
                                <FaIcon className="fas fa-check-double" />
                                <span>{selectedIds.size === images.length && images.length > 0 ? 'Снять все' : 'Выбрать все'}</span>
                            </button>
                            <button
                                type="button"
                                className="lenta-danger"
                                onClick={deleteSelected}
                                disabled={selectedIds.size === 0 || isBulkDeleting}
                            >
                                <FaIcon className={`fas ${isBulkDeleting ? 'fa-spinner fa-spin' : 'fa-trash-alt'}`} />
                                <span>{isBulkDeleting ? 'Удаление…' : `Удалить (${selectedIds.size})`}</span>
                            </button>
                            <button type="button" onClick={exitSelectMode} disabled={isBulkDeleting}>
                                <FaIcon className="fas fa-times" />
                                <span>Отмена</span>
                            </button>
                        </>
                    )}
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
                                className="lenta-card-photo"
                                src={image.preview_url}
                                alt=""
                                loading={index < 4 ? 'eager' : 'lazy'}
                                decoding="async"
                                fetchPriority={index < 2 ? 'high' : 'auto'}
                                draggable="false"
                                onLoad={() => {
                                    loadedRef.current[image.id] = true;
                                    revealActiveRef.current = true;
                                    needsRenderRef.current = true;
                                }}
                                onError={() => {
                                    // даже если фото не загрузилось — показываем карточку (не зависаем невидимой)
                                    loadedRef.current[image.id] = true;
                                    revealActiveRef.current = true;
                                    needsRenderRef.current = true;
                                }}
                            />
                            {activeIndex === index && (
                                <img
                                    key={`hi-${image.id}`}
                                    className="lenta-card-photo lenta-card-photo-hi"
                                    src={image.display_url}
                                    alt=""
                                    decoding="async"
                                    fetchPriority="high"
                                    draggable="false"
                                    onLoad={(event) => event.currentTarget.classList.add('is-ready')}
                                />
                            )}
                            {hasVisibleAnnotations(image.annotations) && (
                                <AnnotationLayer annotations={normalizeAnnotations(image.annotations)} />
                            )}
                            {canUpload && selectMode && (
                                <span
                                    className={`lenta-card-check ${selectedIds.has(image.id) ? 'is-checked' : ''}`}
                                    aria-hidden="true"
                                >
                                    <i className="lenta-check-mark">{selectedIds.has(image.id) ? '✓' : ''}</i>
                                </span>
                            )}
                            {!selectMode && image.annotations?.comments?.length > 0 && (
                                <div className="lenta-card-comments" data-lenta-control>
                                    {image.annotations.comments.slice(-4).map((c, ci) => (
                                        <div key={ci} className="lenta-comment-line">
                                            <b>{userName(c.by)}</b> {c.text}
                                        </div>
                                    ))}
                                </div>
                            )}
                            {!selectMode && activeIndex === index && (
                                <div className="lenta-card-actions" data-lenta-control>
                                    <button
                                        type="button"
                                        className="lenta-card-action"
                                        onClick={(event) => { event.stopPropagation(); editingIdRef.current = image.id; setEditorOpen(true); }}
                                        title="Декорировать фото"
                                    >
                                        <FaIcon className="fas fa-pencil" />
                                        <span>Декор</span>
                                    </button>
                                    {canUpload && (
                                        <button
                                            type="button"
                                            className="lenta-card-action is-danger"
                                            onClick={(event) => { event.stopPropagation(); deleteActiveImage(); }}
                                            disabled={Boolean(deletingId)}
                                            title="Удалить это фото"
                                        >
                                            <FaIcon className={`fas ${deletingId ? 'fa-spinner fa-spin' : 'fa-trash-alt'}`} />
                                            <span>{deletingId ? 'Удаление…' : 'Удалить'}</span>
                                        </button>
                                    )}
                                </div>
                            )}
                        </article>
                    ))}
                </div>
            )}

            <div className="lenta-progress" aria-hidden="true"><span ref={progressRef} /></div>

            {editorOpen && activeImage && (
                <PhotoEditor
                    image={activeImage}
                    annotations={activeImage.annotations}
                    user={user}
                    onSave={saveAnnotations}
                    onClose={() => { editingIdRef.current = null; setEditorOpen(false); }}
                />
            )}
        </section>
    );
};

export default Lenta;
