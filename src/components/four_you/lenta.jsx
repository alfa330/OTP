import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import AlbumBackdrop from './AlbumBackdrop';
import AlbumView from './AlbumView';
import AnnotationLayer from './AnnotationLayer';
import Backgrounds from './Backgrounds';
import PhotoEditor from './PhotoEditor';
import { normalizeAnnotations, hasVisibleAnnotations, userName } from './annotations';
import { buildCards, copiesFor, cullFor, loopProgress } from './lentaLoop';
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
    // Номер открытой КАРТОЧКИ (слота), а не фото: у фото по кругу бывают копии.
    const activeIndexRef = useRef(null);
    const expandedRef = useRef(false);
    const hoveredRef = useRef(null);
    const expandMixRef = useRef(0);
    const selectedMixRef = useRef(0);
    const hoverMixRef = useRef([]);
    const needsRenderRef = useRef(true);
    const loopRef = useRef(false);          // бесконечная прокрутка (когда карточек достаточно)
    const countRef = useRef(0);             // сколько карточек (фото × копии)
    const imagesRef = useRef([]);
    const cardsRef = useRef([]);
    const revealRef = useRef({});           // id → 0..1: появление «съезжанием» к середине
    const loadedRef = useRef({});           // id → true когда фото декодировано
    const revealActiveRef = useRef(false);  // идёт ли сейчас появление
    const pollCursorRef = useRef('');       // курсор поллинга разметки (annotations_updated_at)
    // «Альбом»: пока он открыт, лента не листается и не открывает карточки,
    // а сами карточки разлетаются по сторонам (albumMix 0 → 1) и возвращаются.
    const albumOpenRef = useRef(false);
    const albumTargetRef = useRef(0);
    const albumMixRef = useRef(0);
    const serverOrderRef = useRef(new Map()); // id → место в порядке сервера (страницы альбома)

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
    const [editorImageId, setEditorImageId] = useState(null);
    const [albumOpen, setAlbumOpen] = useState(false);
    const [albumClosing, setAlbumClosing] = useState(false);
    const [albumBackground, setAlbumBackground] = useState('none');
    const [copies, setCopies] = useState(1);

    // Карточки ленты: фото по кругу, копиями, когда фото мало для круга без
    // шва на этой ширине экрана (lentaLoop.js). Номер карточки — слот.
    const cards = useMemo(() => buildCards(images, copies), [images, copies]);

    countRef.current = cards.length;
    imagesRef.current = images;
    cardsRef.current = cards;

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
            const served = Array.isArray(response?.data?.images) ? response.data.images : [];
            // Альбом листается в порядке сервера (как фото добавляли), лента — вразнобой.
            serverOrderRef.current = new Map(served.map((row, index) => [row.id, index]));
            const rows = shuffle(served);
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

    // Превью всех фото грузим сразу. Карточка появляется по загрузке своего фото
    // (loadedRef), а до этого стоит за краем экрана, — поэтому загрузку отмечает
    // и сам предзагрузчик: «ленивое» фото за краем браузер не грузит никогда, и
    // так в ленте оставались видны только первые 4 фото из 18.
    const previewKey = images.map((item) => `${item.id}\n${item.preview_url}`).join('\n');
    useEffect(() => {
        const preloaded = imagesRef.current.map((item) => {
            const image = new Image();
            image.decoding = 'async';
            const markLoaded = () => {
                if (loadedRef.current[item.id]) return;
                loadedRef.current[item.id] = true;
                revealActiveRef.current = true;
                needsRenderRef.current = true;
            };
            image.onload = markLoaded;
            image.onerror = markLoaded;
            image.src = item.preview_url;
            return image;
        });
        return () => preloaded.forEach((image) => {
            image.onload = null;
            image.onerror = null;
            image.src = '';
        });
    }, [previewKey]);

    // Копий для круга — от числа фото и ширины окна.
    useEffect(() => {
        const update = () => setCopies(copiesFor(imagesRef.current.length, window.innerWidth));
        update();
        window.addEventListener('resize', update);
        return () => window.removeEventListener('resize', update);
    }, [images.length]);

    // Копий стало меньше (окно сузили) — открытая карточка могла пропасть:
    // закрываем её сразу, без хвоста. Слот внутри новых границ — то же фото.
    useEffect(() => {
        const slot = activeIndexRef.current;
        if (slot === null || slot < cards.length) return;
        activeIndexRef.current = null;
        expandedRef.current = false;
        expandMixRef.current = 0;
        selectedMixRef.current = 0;
        setActiveIndex(null);
        needsRenderRef.current = true;
    }, [cards.length]);

    // Полноразмерные фото открытой карточки и соседей — заранее (по кругу).
    useEffect(() => {
        if (activeIndex == null || !images.length) return undefined;
        const count = images.length;
        const photo = cards[activeIndex]?.photo ?? 0;
        const photos = new Set([photo - 1, photo, photo + 1].map((index) => ((index % count) + count) % count));
        const preloaded = Array.from(photos).map((index) => {
            const image = new Image();
            image.decoding = 'async';
            image.src = images[index].display_url;
            return image;
        });
        return () => preloaded.forEach((image) => { image.src = ''; });
    }, [activeIndex, images, cards]);

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
        if (!cards.length) return undefined;
        let animationFrame = 0;
        // Кэш применённых z-index/display, чтобы не дёргать стили зря.
        const lastZ = new Array(cards.length).fill(null);
        // null — «не знаем»: после перезапуска (удалили фото, сменилось число
        // копий) карточки сдвигаются по номерам, а их display:none остаётся на
        // узле; первый проход обязан записать каждой её настоящий display.
        const lastDisplay = new Array(cards.length).fill(null);

        const computeCull = () => cullFor(window.innerWidth);

        const getRailScreenCenter = () => {
            const rect = railRef.current.getBoundingClientRect();
            return { x: rect.left + rect.width * 0.5, y: rect.top + rect.height * 0.5 };
        };

        const updateCards = () => {
            const scene = sceneRef.current;
            const rail = railRef.current;
            if (!scene || !rail) return;
            const n = cards.length;
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
                    const id = cards[index].image.id;
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

                // «Альбом»: карточки разлетаются по своим сторонам и гаснут, как
                // при открытии фото, — и тем же путём возвращаются.
                const away = albumMixRef.current;
                if (away > 0) {
                    const a = smooth(away);
                    const leftSide = distance < 0;
                    x = lerp(x, leftSide ? PARAMS.leftDownX : PARAMS.rightUpX, a);
                    y = lerp(y, leftSide ? PARAMS.leftDownY : PARAMS.rightUpY, a);
                    z = lerp(z, PARAMS.splitZ, a);
                    opacity *= 1 - a;
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
                progress = loopProgress(scrollRef.current / PARAMS.step, images.length);
            } else {
                const max = (n - 1) * PARAMS.step;
                progress = max <= 0 ? 0 : (scrollRef.current / max) * 100;
            }
            if (progressRef.current) {
                progressRef.current.style.width = `${clamp(progress, 0, 100)}%`;
                const bar = progressRef.current.parentElement;
                if (bar) bar.style.opacity = String(1 - albumMixRef.current);
            }
            // Ушедшие в альбом карточки не рисуются и не ловят указатель.
            rail.style.visibility = albumMixRef.current >= 1 ? 'hidden' : '';
        };

        // Лента «в покое»: ничего не движется — тяжёлую отрисовку пропускаем.
        const isSettled = () => {
            if (revealActiveRef.current) return false;
            if (albumMixRef.current !== albumTargetRef.current) return false;
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
            loopRef.current = cards.length >= 2 * cullRadius;
            const max = (cards.length - 1) * PARAMS.step;
            if (!loopRef.current) targetRef.current = clamp(targetRef.current, 0, max);
            scrollRef.current = lerp(scrollRef.current, targetRef.current, 0.11);
            if (Math.abs(scrollRef.current - targetRef.current) < 0.025) scrollRef.current = targetRef.current;

            const expandTarget = expandedRef.current ? 1 : 0;
            expandMixRef.current = lerp(expandMixRef.current, expandTarget, 0.036);
            selectedMixRef.current = lerp(selectedMixRef.current, expandTarget, 0.13);
            if (Math.abs(expandMixRef.current - expandTarget) < 0.002) expandMixRef.current = expandTarget;
            if (Math.abs(selectedMixRef.current - expandTarget) < 0.002) selectedMixRef.current = expandTarget;

            const albumTarget = albumTargetRef.current;
            albumMixRef.current = lerp(albumMixRef.current, albumTarget, 0.075);
            if (Math.abs(albumMixRef.current - albumTarget) < 0.002) albumMixRef.current = albumTarget;

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
                // Фон альбома — свои слои со своей прозрачностью (AlbumBackdrop).
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
    }, [cards, images.length, setCardClasses]);

    useEffect(() => {
        const handleKeyDown = (event) => {
            if (albumOpenRef.current) return;      // клавишами листается книга
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
                    // И фото, открытое в «Декоре»: редактор взял разметку при открытии и
                    // от пропсов не меняется, а пропущенная правка второго человека
                    // осталась бы на экране старой и стёрлась бы следующим сохранением.
                    setImages((prev) => prev.map((img) => (byId.has(img.id)
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
        if (expandedRef.current || albumOpenRef.current) return;
        const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
        targetRef.current += delta * 0.95;
    };

    const handlePointerDown = (event) => {
        if (albumOpenRef.current || event.target.closest('[data-lenta-control]')) return;
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
                if (cards[index]) revealRef.current[cards[index].image.id] = 1;
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
            // Список — свежий, а не снимок до загрузки: за десятки секунд загрузки
            // могли сохранить декор, прийти правки поллингом, удалить фото.
            const base = imagesRef.current;
            const known = new Set(base.map((item) => item.id));
            const added = rows.filter((item) => !known.has(item.id));
            const order = serverOrderRef.current;
            added.forEach((item) => { if (!order.has(item.id)) order.set(item.id, order.size); });
            setImages((prev) => {
                const ids = new Set(prev.map((item) => item.id));
                const extra = added.filter((item) => !ids.has(item.id));
                return extra.length ? [...prev, ...extra] : prev;
            });
            // В альбоме новые фото ложатся последними страницами, карточку не открываем.
            // Слот в первой копии равен номеру фото.
            if (added.length && !albumOpenRef.current) openCard(base.length + added.length - 1);
            showToast?.(`Загружено изображений: ${selectedFiles.length}`, 'success');
        } catch (uploadError) {
            showToast?.(uploadError?.response?.data?.error || 'Не удалось загрузить изображения', 'error');
        } finally {
            setIsUploading(false);
            setUploadProgress(0);
        }
    };

    // Удалить фото — с открытой карточки ленты или со страницы альбома.
    const deleteImage = async (image) => {
        if (!image || !canUpload || deletingId) return;
        if (!window.confirm('Удалить это изображение из 4 You?')) return;
        setDeletingId(image.id);
        try {
            await axios.delete(`${apiBaseUrl}/api/four_you/images/${encodeURIComponent(image.id)}`, {
                headers: authHeaders(),
            });
            activeIndexRef.current = null;
            expandedRef.current = false;
            expandMixRef.current = 0;
            selectedMixRef.current = 0;
            setActiveIndex(null);
            setImages((prev) => prev.filter((item) => item.id !== image.id));
            // Границы прокрутки держит цикл кадров (без круга — край ленты).
            showToast?.('Изображение удалено', 'success');
        } catch (deleteError) {
            showToast?.(deleteError?.response?.data?.error || 'Не удалось удалить изображение', 'error');
        } finally {
            setDeletingId('');
        }
    };

    const toggleSelect = (index) => {
        const image = cards[index]?.image;
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
            activeIndexRef.current = null;
            expandedRef.current = false;
            expandMixRef.current = 0;
            selectedMixRef.current = 0;
            setActiveIndex(null);
            setImages((prev) => prev.filter((item) => !deletedSet.has(item.id)));
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
        const image = editorImageId ? images.find((item) => item.id === editorImageId) : null;
        if (!image) return;
        try {
            const response = await axios.put(
                `${apiBaseUrl}/api/four_you/images/${encodeURIComponent(image.id)}/annotations`,
                { annotations },
                { headers: authHeaders() },
            );
            const saved = response?.data?.annotations || annotations;
            const ts = response?.data?.annotations_updated_at;
            // Курсор поллинга не двигаем: перескочив на время сохранения, он
            // пропустил бы ещё не опрошенные правки других фото. Следующий опрос
            // один раз вернёт это же сохранение — безвредно.
            setImages((prev) => prev.map((img) => (img.id === image.id
                ? { ...img, annotations: saved, annotations_updated_at: ts || img.annotations_updated_at }
                : img)));
            setEditorImageId(null);
            showToast?.('Разметка сохранена', 'success');
        } catch (saveError) {
            showToast?.(saveError?.response?.data?.error || 'Не удалось сохранить разметку', 'error');
            throw saveError;
        }
    };

    // «Декор» — с открытой карточки ленты или со страницы альбома: редактор один.
    const openEditor = useCallback((image) => {
        if (image) setEditorImageId(image.id);
    }, []);

    const closeEditor = () => setEditorImageId(null);

    /* ---------- «Альбом» ---------- */

    const reducedMotion = useMemo(() => (
        typeof window !== 'undefined' && typeof window.matchMedia === 'function'
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ), []);

    // Альбом листается в порядке сервера — как фото добавляли, а не вразнобой.
    const albumImages = useMemo(() => {
        const order = serverOrderRef.current;
        const rank = (item) => (order.has(item.id) ? order.get(item.id) : Number.MAX_SAFE_INTEGER);
        return images.slice().sort((a, b) => rank(a) - rank(b));
    }, [images]);

    const openAlbum = () => {
        if (!images.length || albumOpenRef.current) return;
        expandedRef.current = false;           // открытая карточка улетает со всеми
        hoveredRef.current = null;
        setSelectMode(false);
        setSelectedIds(new Set());
        albumOpenRef.current = true;
        albumTargetRef.current = 1;
        if (reducedMotion) albumMixRef.current = 1;
        needsRenderRef.current = true;
        setAlbumBackground('none');
        setAlbumClosing(false);
        setAlbumOpen(true);
    };

    const closeAlbum = useCallback(() => setAlbumClosing(true), []);

    // Книга начала уходить — карточки возвращаются ей навстречу.
    const handleAlbumLeave = useCallback(() => {
        albumTargetRef.current = 0;
        if (reducedMotion) albumMixRef.current = 0;
        needsRenderRef.current = true;
    }, [reducedMotion]);

    const handleAlbumExited = useCallback(() => {
        albumOpenRef.current = false;
        albumTargetRef.current = 0;
        needsRenderRef.current = true;
        setAlbumOpen(false);
        setAlbumClosing(false);
        setAlbumBackground('none');
    }, []);

    const activeImage = activeIndex == null ? null : (cards[activeIndex]?.image || null);
    const activeBackground = activeImage ? (activeImage.annotations?.background || 'none') : 'none';
    const editorImage = editorImageId ? images.find((item) => item.id === editorImageId) || null : null;

    return (
        <section
            ref={sceneRef}
            className={`lenta-scene${activeBackground !== 'none' ? ` lenta-scene--bg lenta-scene--bg-${activeBackground}` : ''}${albumOpen ? ' lenta-scene--album' : ''}`}
            aria-label="4 You"
            onWheel={handleWheel}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={cancelPointer}
        >
            {/* Фон открытой карточки ленты гаснет вместе с ней (selectedMix);
                у альбома свои слои с наплывом — они не обрываются ни при
                листании, ни при входе в альбом и выходе из него. */}
            <Backgrounds bg={activeBackground} />
            <AlbumBackdrop bg={albumOpen ? albumBackground : 'none'} />

            {(canUpload || images.length > 0) && (
                <div className="lenta-admin-controls" data-lenta-control>
                    {/* «Альбом» — всем, кому открыт раздел: смотреть и декорировать. */}
                    {!selectMode && images.length > 0 && (
                        <button
                            type="button"
                            onClick={albumOpen ? closeAlbum : openAlbum}
                            disabled={albumClosing}
                            // На телефоне подпись скрыта — имя кнопке даёт aria-label.
                            aria-label={albumOpen ? 'Вернуться в ленту' : 'Открыть альбом'}
                        >
                            <FaIcon className={`fas ${albumOpen ? 'fa-images' : 'fa-book'}`} />
                            <span>{albumOpen ? 'Лента' : 'Альбом'}</span>
                        </button>
                    )}
                    {canUpload && (!selectMode ? (
                        <>
                            <button type="button" onClick={() => fileInputRef.current?.click()} disabled={isUploading}>
                                <FaIcon className={`fas ${isUploading ? 'fa-spinner fa-spin' : 'fa-plus'}`} />
                                <span>{isUploading ? `${uploadProgress}%` : 'Добавить фото'}</span>
                            </button>
                            {images.length > 0 && !albumOpen && (
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
                    ))}
                    {canUpload && (
                        <input ref={fileInputRef} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden onChange={handleUpload} />
                    )}
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
                    {cards.map(({ image, key }, index) => (
                        <article
                            key={key}
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
                                        onClick={(event) => { event.stopPropagation(); openEditor(image); }}
                                        title="Декорировать фото"
                                    >
                                        <FaIcon className="fas fa-pencil" />
                                        <span>Декор</span>
                                    </button>
                                    {canUpload && (
                                        <button
                                            type="button"
                                            className="lenta-card-action is-danger"
                                            onClick={(event) => { event.stopPropagation(); deleteImage(image); }}
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

            {albumOpen && (
                <AlbumView
                    images={albumImages}
                    canDelete={canUpload}
                    deletingId={deletingId}
                    editorOpen={Boolean(editorImage)}
                    closing={albumClosing}
                    onDecorate={openEditor}
                    onDelete={deleteImage}
                    onBackground={setAlbumBackground}
                    onLeave={handleAlbumLeave}
                    onExited={handleAlbumExited}
                    onRequestClose={closeAlbum}
                />
            )}

            <div className="lenta-progress" aria-hidden="true"><span ref={progressRef} /></div>

            {editorImage && (
                <PhotoEditor
                    key={editorImage.id}
                    image={editorImage}
                    annotations={editorImage.annotations}
                    user={user}
                    onSave={saveAnnotations}
                    onClose={closeEditor}
                />
            )}
        </section>
    );
};

export default Lenta;
