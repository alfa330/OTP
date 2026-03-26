import React, { useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';

const AVATAR_MAX_DIMENSION = 128;
const AVATAR_TARGET_BYTES = 40 * 1024;
const AVATAR_MAX_BYTES = 512 * 1024;
const AVATAR_CROP_PREVIEW_SIZE = 256;
const AVATAR_MIN_ZOOM = 1;
const AVATAR_MAX_ZOOM_CAP = 6;

const clampNumber = (value, min, max) => Math.min(max, Math.max(min, value));

const getAvatarMaxZoom = (sourceWidth, sourceHeight) => {
    const minSide = Math.max(1, Math.min(sourceWidth, sourceHeight));
    return clampNumber(minSide / 64, AVATAR_MIN_ZOOM, AVATAR_MAX_ZOOM_CAP);
};

const getAvatarCropMetrics = (sourceWidth, sourceHeight, zoom) => {
    const minSide = Math.max(1, Math.min(sourceWidth, sourceHeight));
    const cropSize = minSide / Math.max(AVATAR_MIN_ZOOM, zoom);
    const halfCrop = cropSize / 2;
    return { minSide, cropSize, halfCrop };
};

const normalizeAvatarCropState = (draft) => {
    if (!draft) return draft;
    const sourceWidth = Math.max(1, Number(draft.sourceWidth) || AVATAR_MAX_DIMENSION);
    const sourceHeight = Math.max(1, Number(draft.sourceHeight) || AVATAR_MAX_DIMENSION);
    const maxZoom = clampNumber(
        Number(draft.maxZoom) || getAvatarMaxZoom(sourceWidth, sourceHeight),
        AVATAR_MIN_ZOOM,
        AVATAR_MAX_ZOOM_CAP
    );
    const zoom = clampNumber(Number(draft.zoom) || AVATAR_MIN_ZOOM, AVATAR_MIN_ZOOM, maxZoom);
    const { cropSize, halfCrop } = getAvatarCropMetrics(sourceWidth, sourceHeight, zoom);
    const minCenterX = halfCrop;
    const maxCenterX = Math.max(halfCrop, sourceWidth - halfCrop);
    const minCenterY = halfCrop;
    const maxCenterY = Math.max(halfCrop, sourceHeight - halfCrop);
    const centerX = clampNumber(
        Number(draft.centerX) || sourceWidth / 2,
        minCenterX,
        maxCenterX
    );
    const centerY = clampNumber(
        Number(draft.centerY) || sourceHeight / 2,
        minCenterY,
        maxCenterY
    );
    return {
        ...draft,
        sourceWidth,
        sourceHeight,
        maxZoom,
        zoom,
        centerX,
        centerY,
        cropSize
    };
};

const canvasToBlob = (canvas, type, quality) => (
    new Promise((resolve, reject) => {
        canvas.toBlob((blob) => {
            if (blob) resolve(blob);
            else reject(new Error('Не удалось обработать изображение'));
        }, type, quality);
    })
);

const loadImageFromFile = (file) => (
    new Promise((resolve, reject) => {
        const objectUrl = URL.createObjectURL(file);
        const image = new Image();
        image.onload = () => {
            URL.revokeObjectURL(objectUrl);
            resolve(image);
        };
        image.onerror = () => {
            URL.revokeObjectURL(objectUrl);
            reject(new Error('Не удалось прочитать изображение'));
        };
        image.src = objectUrl;
    })
);

const detectPrimaryFaceCenter = async (image) => {
    try {
        const FaceDetectorCtor = typeof window !== 'undefined' ? window.FaceDetector : null;
        if (!FaceDetectorCtor) return null;
        const detector = new FaceDetectorCtor({
            fastMode: true,
            maxDetectedFaces: 3
        });
        const faces = await detector.detect(image);
        if (!Array.isArray(faces) || faces.length === 0) return null;
        const primaryFace = faces
            .map((face) => face?.boundingBox || null)
            .filter(Boolean)
            .sort((a, b) => (b.width * b.height) - (a.width * a.height))[0];
        if (!primaryFace) return null;
        return {
            x: primaryFace.x + primaryFace.width / 2,
            y: primaryFace.y + primaryFace.height / 2
        };
    } catch (_) {
        return null;
    }
};

const compressAvatarImageFile = async (sourceFile, cropState = null) => {
    const image = await loadImageFromFile(sourceFile);
    const width = image.naturalWidth || image.width || AVATAR_MAX_DIMENSION;
    const height = image.naturalHeight || image.height || AVATAR_MAX_DIMENSION;
    const normalizedCrop = normalizeAvatarCropState({
        sourceWidth: width,
        sourceHeight: height,
        maxZoom: getAvatarMaxZoom(width, height),
        zoom: cropState?.zoom ?? AVATAR_MIN_ZOOM,
        centerX: cropState?.centerX ?? (width / 2),
        centerY: cropState?.centerY ?? (height / 2)
    });
    const { cropSize } = getAvatarCropMetrics(width, height, normalizedCrop.zoom);
    const maxSx = Math.max(0, width - cropSize);
    const maxSy = Math.max(0, height - cropSize);
    const sx = clampNumber(normalizedCrop.centerX - (cropSize / 2), 0, maxSx);
    const sy = clampNumber(normalizedCrop.centerY - (cropSize / 2), 0, maxSy);

    const canvas = document.createElement('canvas');
    canvas.width = AVATAR_MAX_DIMENSION;
    canvas.height = AVATAR_MAX_DIMENSION;
    const context = canvas.getContext('2d', { alpha: false });
    if (!context) throw new Error('Не удалось подготовить canvas для сжатия');
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = 'high';
    context.drawImage(
        image,
        sx,
        sy,
        cropSize,
        cropSize,
        0,
        0,
        AVATAR_MAX_DIMENSION,
        AVATAR_MAX_DIMENSION
    );

    let quality = 0.9;
    let blob = await canvasToBlob(canvas, 'image/webp', quality);
    while (blob.size > AVATAR_TARGET_BYTES && quality > 0.42) {
        quality = Math.max(0.42, quality - 0.07);
        blob = await canvasToBlob(canvas, 'image/webp', quality);
    }

    const baseName = String(sourceFile.name || 'avatar')
        .replace(/\.[^/.]+$/, '')
        .replace(/[^a-zA-Z0-9_-]/g, '_')
        .slice(0, 64) || 'avatar';
    return new File([blob], `${baseName}.webp`, {
        type: 'image/webp',
        lastModified: Date.now()
    });
};

const AccountAvatarModal = ({
    isOpen,
    onClose,
    onSave,
    userName,
    avatarUrl
}) => {
    const avatarInputRef = useRef(null);
    const avatarObjectUrlRef = useRef('');
    const avatarCropObjectUrlRef = useRef('');
    const avatarCropDragRef = useRef(null);

    const [isLoading, setIsLoading] = useState(false);
    const [modalError, setModalError] = useState('');
    const [avatarPreviewUrl, setAvatarPreviewUrl] = useState('');
    const [avatarUploadFile, setAvatarUploadFile] = useState(null);
    const [avatarOriginalFile, setAvatarOriginalFile] = useState(null);
    const [avatarRemoveRequested, setAvatarRemoveRequested] = useState(false);
    const [avatarError, setAvatarError] = useState('');
    const [isAvatarProcessing, setIsAvatarProcessing] = useState(false);
    const [avatarCropState, setAvatarCropState] = useState(null);

    const revokeAvatarPreviewUrl = React.useCallback(() => {
        if (avatarObjectUrlRef.current && avatarObjectUrlRef.current.startsWith('blob:')) {
            URL.revokeObjectURL(avatarObjectUrlRef.current);
        }
        avatarObjectUrlRef.current = '';
    }, []);

    const revokeAvatarCropSourceUrl = React.useCallback(() => {
        if (avatarCropObjectUrlRef.current && avatarCropObjectUrlRef.current.startsWith('blob:')) {
            URL.revokeObjectURL(avatarCropObjectUrlRef.current);
        }
        avatarCropObjectUrlRef.current = '';
    }, []);

    const closeAvatarCropEditor = React.useCallback(() => {
        avatarCropDragRef.current = null;
        setAvatarCropState(null);
        revokeAvatarCropSourceUrl();
    }, [revokeAvatarCropSourceUrl]);

    useEffect(() => {
        if (!isOpen) return;
        revokeAvatarPreviewUrl();
        closeAvatarCropEditor();
        setIsLoading(false);
        setModalError('');
        setAvatarPreviewUrl((avatarUrl || '').trim());
        setAvatarUploadFile(null);
        setAvatarOriginalFile(null);
        setAvatarRemoveRequested(false);
        setAvatarError('');
        setIsAvatarProcessing(false);
        if (avatarInputRef.current) avatarInputRef.current.value = '';
    }, [isOpen, avatarUrl, revokeAvatarPreviewUrl, closeAvatarCropEditor]);

    useEffect(() => (() => {
        revokeAvatarPreviewUrl();
        revokeAvatarCropSourceUrl();
    }), [revokeAvatarPreviewUrl, revokeAvatarCropSourceUrl]);

    const applyAvatarPreviewFromFile = (file) => {
        revokeAvatarPreviewUrl();
        const objectUrl = URL.createObjectURL(file);
        avatarObjectUrlRef.current = objectUrl;
        setAvatarPreviewUrl(objectUrl);
    };

    const handleAvatarSelect = async (event) => {
        const input = event.target;
        const sourceFile = input?.files?.[0];
        if (!sourceFile) return;

        setModalError('');
        setAvatarError('');
        setIsAvatarProcessing(true);
        try {
            if (!String(sourceFile.type || '').startsWith('image/')) {
                throw new Error('Можно загружать только изображения');
            }
            const image = await loadImageFromFile(sourceFile);
            const sourceWidth = image.naturalWidth || image.width || AVATAR_MAX_DIMENSION;
            const sourceHeight = image.naturalHeight || image.height || AVATAR_MAX_DIMENSION;
            const faceCenter = await detectPrimaryFaceCenter(image);
            const nextCrop = normalizeAvatarCropState({
                sourceFile,
                sourceUrl: URL.createObjectURL(sourceFile),
                sourceWidth,
                sourceHeight,
                maxZoom: getAvatarMaxZoom(sourceWidth, sourceHeight),
                zoom: AVATAR_MIN_ZOOM,
                centerX: faceCenter?.x ?? (sourceWidth / 2),
                centerY: faceCenter?.y ?? (sourceHeight / 2)
            });
            revokeAvatarCropSourceUrl();
            avatarCropObjectUrlRef.current = nextCrop.sourceUrl;
            setAvatarCropState(nextCrop);
        } catch (error) {
            setAvatarError(error?.message || 'Не удалось обработать аватар');
        } finally {
            setIsAvatarProcessing(false);
            if (input) input.value = '';
        }
    };

    const handleAvatarCropZoomChange = (event) => {
        const nextZoom = Number(event.target.value);
        if (!Number.isFinite(nextZoom)) return;
        setAvatarCropState((prev) => normalizeAvatarCropState(prev ? { ...prev, zoom: nextZoom } : prev));
    };

    const handleAvatarCropPointerDown = (event) => {
        if (!avatarCropState) return;
        event.preventDefault();
        avatarCropDragRef.current = {
            pointerId: event.pointerId,
            lastX: event.clientX,
            lastY: event.clientY
        };
        event.currentTarget.setPointerCapture?.(event.pointerId);
    };

    const handleAvatarCropPointerMove = (event) => {
        const drag = avatarCropDragRef.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        setAvatarCropState((prev) => {
            if (!prev) return prev;
            const dx = event.clientX - drag.lastX;
            const dy = event.clientY - drag.lastY;
            drag.lastX = event.clientX;
            drag.lastY = event.clientY;
            const { minSide } = getAvatarCropMetrics(prev.sourceWidth, prev.sourceHeight, prev.zoom);
            const displayScale = (AVATAR_CROP_PREVIEW_SIZE * prev.zoom) / minSide;
            if (!Number.isFinite(displayScale) || displayScale <= 0) return prev;
            return normalizeAvatarCropState({
                ...prev,
                centerX: prev.centerX - (dx / displayScale),
                centerY: prev.centerY - (dy / displayScale)
            });
        });
    };

    const handleAvatarCropPointerUp = (event) => {
        const drag = avatarCropDragRef.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        avatarCropDragRef.current = null;
        event.currentTarget.releasePointerCapture?.(event.pointerId);
    };

    const handleAvatarCropWheel = (event) => {
        event.preventDefault();
        const zoomStep = event.deltaY < 0 ? 0.08 : -0.08;
        setAvatarCropState((prev) => normalizeAvatarCropState(prev ? { ...prev, zoom: prev.zoom + zoomStep } : prev));
    };

    const handleAvatarCropCancel = () => {
        closeAvatarCropEditor();
    };

    const handleAvatarCropApply = async () => {
        if (!avatarCropState?.sourceFile) return;
        setModalError('');
        setAvatarError('');
        setIsAvatarProcessing(true);
        try {
            const compressedAvatar = await compressAvatarImageFile(avatarCropState.sourceFile, avatarCropState);
            if (compressedAvatar.size > AVATAR_MAX_BYTES) {
                throw new Error('Аватар слишком большой после сжатия');
            }
            setAvatarUploadFile(compressedAvatar);
            setAvatarOriginalFile(avatarCropState.sourceFile);
            setAvatarRemoveRequested(false);
            applyAvatarPreviewFromFile(compressedAvatar);
            closeAvatarCropEditor();
        } catch (error) {
            setAvatarError(error?.message || 'Не удалось обработать аватар');
        } finally {
            setIsAvatarProcessing(false);
        }
    };

    const handleAvatarRemove = () => {
        revokeAvatarPreviewUrl();
        closeAvatarCropEditor();
        setModalError('');
        setAvatarPreviewUrl('');
        setAvatarUploadFile(null);
        setAvatarOriginalFile(null);
        setAvatarRemoveRequested(true);
        setAvatarError('');
        if (avatarInputRef.current) avatarInputRef.current.value = '';
    };

    const handleClose = () => {
        if (isLoading) return;
        setModalError('');
        closeAvatarCropEditor();
        onClose?.();
    };

    const handleSave = async () => {
        if (isAvatarProcessing) {
            setModalError('Дождитесь завершения обработки аватара.');
            return;
        }
        if (avatarCropState) {
            setModalError('Завершите обрезку аватара (Применить или Отмена).');
            return;
        }
        if (!avatarUploadFile && !avatarRemoveRequested) {
            setModalError('Сначала выберите новый аватар или удалите текущий.');
            return;
        }

        setModalError('');
        setIsLoading(true);
        try {
            await onSave?.({
                avatar_file: avatarUploadFile || null,
                avatar_original_file: avatarUploadFile ? (avatarOriginalFile || null) : null,
                avatar_remove: !!avatarRemoveRequested
            });
            closeAvatarCropEditor();
            onClose?.();
        } catch (error) {
            const serverMsg = error?.response?.data?.error || error?.message || 'Не удалось сохранить аватар.';
            setModalError(serverMsg);
        } finally {
            setIsLoading(false);
        }
    };

    const avatarInitial = String(userName || 'U').charAt(0).toUpperCase();
    const avatarDisabled = isLoading || isAvatarProcessing || !!avatarCropState;
    const avatarCropViewStyle = useMemo(() => {
        if (!avatarCropState) return null;
        const { minSide } = getAvatarCropMetrics(
            avatarCropState.sourceWidth,
            avatarCropState.sourceHeight,
            avatarCropState.zoom
        );
        const displayScale = (AVATAR_CROP_PREVIEW_SIZE * avatarCropState.zoom) / minSide;
        const width = avatarCropState.sourceWidth * displayScale;
        const height = avatarCropState.sourceHeight * displayScale;
        const left = (AVATAR_CROP_PREVIEW_SIZE / 2) - (avatarCropState.centerX * displayScale);
        const top = (AVATAR_CROP_PREVIEW_SIZE / 2) - (avatarCropState.centerY * displayScale);
        return {
            width: `${width}px`,
            height: `${height}px`,
            left: `${left}px`,
            top: `${top}px`
        };
    }, [avatarCropState]);

    if (!isOpen) return null;

    return (
        <>
            <div
                className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-300"
                onClick={handleClose}
                aria-hidden="true"
            />

            <div
                className="fixed inset-0 z-50 flex items-center justify-center p-4"
                tabIndex={-1}
                onKeyDown={(e) => {
                    if (e.key === 'Escape') {
                        handleClose();
                    }
                }}
            >
                <div
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="change-avatar-title"
                    className="pointer-events-auto w-full max-w-md bg-white/95 dark:bg-slate-900/95 rounded-2xl shadow-2xl overflow-hidden transform transition-all duration-300 animate-scale-in"
                    onClick={(e) => e.stopPropagation()}
                >
                    <div className="px-6 py-5">
                        <div className="flex items-start justify-between gap-4">
                            <h2 id="change-avatar-title" className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                                Смена аватара
                            </h2>
                            <button
                                type="button"
                                onClick={handleClose}
                                aria-label="Закрыть"
                                className="rounded-md p-2 text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-slate-800 transition"
                            >
                                <FaIcon className="fas fa-times text-lg" />
                            </button>
                        </div>

                        <p className="mt-2 text-sm text-gray-600 dark:text-gray-400">
                            Загрузите фото, при необходимости подрежьте и сохраните.
                        </p>

                        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
                            <div className="flex items-center gap-3">
                                <div className="h-16 w-16 overflow-hidden rounded-full border border-slate-200 bg-gradient-to-br from-blue-500 to-blue-700">
                                    {avatarPreviewUrl ? (
                                        <img
                                            src={avatarPreviewUrl}
                                            alt="Avatar preview"
                                            className="h-full w-full object-cover"
                                        />
                                    ) : (
                                        <div className="flex h-full w-full items-center justify-center text-xl font-bold text-white">
                                            {avatarInitial}
                                        </div>
                                    )}
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="text-sm font-medium text-slate-800">Аватар</div>
                                    <div className="text-xs text-slate-500">
                                        JPEG/PNG/WebP. Квадратная обрезка + зум, затем авто-сжатие до ~40 KB (128x128).
                                    </div>
                                    <div className="mt-2 flex flex-wrap gap-2">
                                        <label className={`inline-flex cursor-pointer items-center rounded-md px-3 py-1.5 text-xs font-medium ${avatarDisabled ? 'bg-slate-300 text-slate-600 cursor-not-allowed' : 'bg-blue-600 text-white hover:bg-blue-700'}`}>
                                            <input
                                                ref={avatarInputRef}
                                                type="file"
                                                accept="image/png,image/jpeg,image/webp,image/gif"
                                                className="hidden"
                                                onChange={handleAvatarSelect}
                                                disabled={avatarDisabled}
                                            />
                                            {isAvatarProcessing ? 'Обработка...' : (avatarPreviewUrl ? 'Заменить' : 'Загрузить')}
                                        </label>
                                        {avatarPreviewUrl && (
                                            <button
                                                type="button"
                                                onClick={handleAvatarRemove}
                                                className="inline-flex items-center rounded-md bg-white px-3 py-1.5 text-xs font-medium text-red-600 border border-red-200 hover:bg-red-50"
                                                disabled={avatarDisabled}
                                            >
                                                Удалить
                                            </button>
                                        )}
                                    </div>
                                </div>
                            </div>

                            {avatarUploadFile && (
                                <div className="mt-2 text-xs text-emerald-700">
                                    Подготовлен файл: {(avatarUploadFile.size / 1024).toFixed(0)} KB (WebP).
                                </div>
                            )}
                            {avatarRemoveRequested && !avatarUploadFile && (
                                <div className="mt-2 text-xs text-amber-700">
                                    Аватар будет удален после сохранения.
                                </div>
                            )}
                            {avatarError && (
                                <div className="mt-2 text-xs text-red-600">{avatarError}</div>
                            )}
                        </div>

                        <div aria-live="polite" className="mt-4 min-h-[1.25rem]">
                            {modalError && <p className="text-sm text-red-600 dark:text-red-400">{modalError}</p>}
                        </div>

                        <div className="mt-4 flex justify-end items-center gap-3">
                            <button
                                type="button"
                                onClick={handleClose}
                                className="px-4 py-2 bg-gray-200 text-gray-800 rounded-lg hover:bg-gray-300 transition-all duration-200 font-medium"
                                disabled={isLoading}
                            >
                                Отмена
                            </button>
                            <button
                                type="button"
                                onClick={handleSave}
                                disabled={isLoading || isAvatarProcessing}
                                className={`px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-all duration-200 font-medium flex items-center gap-2 ${(isLoading || isAvatarProcessing) ? 'opacity-60 cursor-not-allowed' : ''}`}
                            >
                                {(isLoading || isAvatarProcessing) ? (
                                    <>
                                        <FaIcon className="fas fa-spinner fa-spin" /> Сохранение...
                                    </>
                                ) : (
                                    'Сохранить'
                                )}
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            {avatarCropState && (
                <div className="fixed inset-0 z-[70] flex items-center justify-center p-4">
                    <div
                        className="absolute inset-0 bg-slate-900/65 backdrop-blur-[1px]"
                        onClick={handleAvatarCropCancel}
                        aria-hidden="true"
                    />
                    <div
                        className="relative w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-4 shadow-2xl"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <div className="text-base font-semibold text-slate-900">Обрезка аватара</div>
                                <div className="text-xs text-slate-500">
                                    Перетащите фото и выберите приближение.
                                </div>
                            </div>
                            <button
                                type="button"
                                onClick={handleAvatarCropCancel}
                                className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100"
                                aria-label="Закрыть обрезку"
                            >
                                <FaIcon className="fas fa-times" />
                            </button>
                        </div>

                        <div className="mt-3 flex justify-center">
                            <div
                                className="relative overflow-hidden rounded-2xl border border-slate-200 bg-slate-100 touch-none select-none"
                                style={{ width: `${AVATAR_CROP_PREVIEW_SIZE}px`, height: `${AVATAR_CROP_PREVIEW_SIZE}px`, cursor: 'grab' }}
                                onPointerDown={handleAvatarCropPointerDown}
                                onPointerMove={handleAvatarCropPointerMove}
                                onPointerUp={handleAvatarCropPointerUp}
                                onPointerCancel={handleAvatarCropPointerUp}
                                onWheel={handleAvatarCropWheel}
                            >
                                {avatarCropState?.sourceUrl && avatarCropViewStyle && (
                                    <img
                                        src={avatarCropState.sourceUrl}
                                        alt="Avatar crop source"
                                        className="pointer-events-none absolute max-w-none select-none"
                                        style={avatarCropViewStyle}
                                        draggable={false}
                                    />
                                )}
                                <div className="pointer-events-none absolute inset-0 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.65)]" />
                                <div className="pointer-events-none absolute inset-[9%] rounded-full border border-white/80" />
                            </div>
                        </div>

                        <div className="mt-4">
                            <div className="mb-1 flex items-center justify-between text-xs text-slate-600">
                                <span>Приближение</span>
                                <span>{avatarCropState.zoom.toFixed(2)}x</span>
                            </div>
                            <input
                                type="range"
                                min={AVATAR_MIN_ZOOM}
                                max={avatarCropState.maxZoom}
                                step="0.01"
                                value={avatarCropState.zoom}
                                onChange={handleAvatarCropZoomChange}
                                className="w-full accent-sky-500"
                            />
                        </div>

                        <div className="mt-4 flex justify-end gap-2">
                            <button
                                type="button"
                                onClick={handleAvatarCropCancel}
                                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                                disabled={isAvatarProcessing}
                            >
                                Отмена
                            </button>
                            <button
                                type="button"
                                onClick={handleAvatarCropApply}
                                className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                                disabled={isAvatarProcessing}
                            >
                                {isAvatarProcessing ? 'Обработка...' : 'Применить'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
};

export default AccountAvatarModal;
