import React, { useEffect, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { isAdminLikeRole as isAdminLikeRoleFn, normalizeRole } from '../../utils/roles';

const PERIOD_STATUS_VALUES = new Set(['bs', 'sick_leave', 'annual_leave', 'dismissal']);
const DISMISSAL_REASON_WITH_END_DATE = 'Б/С на летний период';
const DISMISSAL_REASON_OPTIONS = [
    DISMISSAL_REASON_WITH_END_DATE,
    'Мошенничество',
    'Нарушение дисциплины',
    'Не может совмещать с учебой',
    'Не может совмещать с работой',
    'Не нравится работа',
    'Выгорание',
    'Не устраивает доход',
    'Перевод в другой отдел',
    'Переезд',
    'По состоянию здоровья',
    'Пропал',
    'Слабый/не выполняет kpi',
    'Забрали в армию',
    'Нашел работу по профессии',
    'По семейным обстоятельствам'
];

const AVATAR_MAX_DIMENSION = 128;
const AVATAR_TARGET_BYTES = 40 * 1024;
const AVATAR_MAX_BYTES = 512 * 1024;
const AVATAR_CROP_PREVIEW_SIZE = 256;
const AVATAR_MIN_ZOOM = 1;
const AVATAR_MAX_ZOOM_CAP = 6;
const KZ_PHONE_REGEX = /^\+7\d{10}$/;
const KZ_PHONE_PLACEHOLDER = '+7XXXXXXXXXX';
const getAlmatyDayOfMonth = (date = new Date()) => {
    try {
        const dayValue = new Intl.DateTimeFormat('en-US', {
            timeZone: 'Asia/Almaty',
            day: 'numeric'
        }).format(date);
        const parsed = Number(dayValue);
        return Number.isFinite(parsed) ? parsed : date.getDate();
    } catch (_) {
        return date.getDate();
    }
};

const isValidKzPhone = (value) => {
    const normalized = String(value || '').trim();
    if (!normalized) return true;
    return KZ_PHONE_REGEX.test(normalized);
};

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
            else reject(new Error("Не удалось обработать изображение"));
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
            reject(new Error("Не удалось прочитать изображение"));
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
    if (!context) throw new Error("Не удалось подготовить canvas для сжатия");
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

const UserEditModal = ({ isOpen, onClose, userToEdit, svList = [], directions = [], onSave, user }) => {
    const [editedUser, setEditedUser] = useState(userToEdit || {});
    const [isLoading, setIsLoading] = useState(false);
    const [modalError, setModalError] = useState("");
    const [createdCredentials, setCreatedCredentials] = useState(null); // { login, password }
    const [activeTab, setActiveTab] = useState("data");
    const nameRef = React.useRef(null);
    const avatarInputRef = React.useRef(null);
    const avatarObjectUrlRef = React.useRef('');
    const avatarCropObjectUrlRef = React.useRef('');
    const avatarCropDragRef = React.useRef(null);
    const [avatarPreviewUrl, setAvatarPreviewUrl] = useState("");
    const [avatarUploadFile, setAvatarUploadFile] = useState(null);
    const [avatarOriginalFile, setAvatarOriginalFile] = useState(null);
    const [avatarRemoveRequested, setAvatarRemoveRequested] = useState(false);
    const [avatarError, setAvatarError] = useState("");
    const [isAvatarProcessing, setIsAvatarProcessing] = useState(false);
    const [avatarCropState, setAvatarCropState] = useState(null);
    const toDateInputValue = (value) => {
        if (!value) return "";
        const str = String(value).trim();
        if (!str) return "";
        const datePart = str.split("T")[0];
        if (/^\d{4}-\d{2}-\d{2}$/.test(datePart)) return datePart;
        const match = datePart.match(/^(\d{2})[-./](\d{2})[-./](\d{4})$/);
        if (match) return `${match[3]}-${match[2]}-${match[1]}`;
        return "";
    };
    const todayInputDate = () => {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    };
    const isPeriodStatus = (value) => PERIOD_STATUS_VALUES.has(String(value || ''));
    const isDismissalLikeStatus = (value) => {
        const status = String(value || '').trim();
        return status === 'dismissal' || status === 'fired';
    };
    const usesScheduleStatusPeriodForm = (value) => isPeriodStatus(value) || String(value || '').trim() === 'fired';
    const getRoleValue = (draft) => {
        const resolvedRole = String(draft?.role || userToEdit?.role || '').trim().toLowerCase();
        if (resolvedRole) return resolvedRole;
        const isCreateDraft = !draft?.id && !userToEdit?.id;
        return isCreateDraft ? 'operator' : '';
    };
    const isTrainerDraft = (draft) => getRoleValue(draft) === 'trainer';
    const isOperatorDraft = (draft) => getRoleValue(draft) === 'operator';
    const isAdminLikeRequester = isAdminLikeRoleFn(user?.role);
    const requesterRole = normalizeRole(user?.role);
    const isSupervisorRequester = requesterRole === 'sv';
    const isExistingUserEdit = Boolean(userToEdit?.id);
    const isSupervisorRateEditDay = getAlmatyDayOfMonth() === 1;
    const isSupervisorRateLocked = isSupervisorRequester && isOperatorDraft(editedUser) && isExistingUserEdit && !isSupervisorRateEditDay;
    const canShowOperatorRateControls = isOperatorDraft(editedUser) && (isAdminLikeRequester || isSupervisorRequester);
    const normalizeModalStatusValue = (value) => {
        const status = String(value ?? '').trim();
        if (status === 'unpaid_leave') return 'bs';
        if (status === 'dismissal') return 'fired';
        return status || 'working';
    };
    const shouldShowStatusPeriodEndDate = (draft) => {
        if (!usesScheduleStatusPeriodForm(draft?.status)) return false;
        if (!isDismissalLikeStatus(draft?.status)) return true;
        if (draft?.status_period_is_blacklist) return false;
        return String(draft?.status_period_dismissal_reason || '').trim() === DISMISSAL_REASON_WITH_END_DATE;
    };

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
        // Устанавливаем defaults при открытии для режима создания
        const base = userToEdit || {};
        const baseRole = String(base.role || (!base.id ? 'operator' : '')).trim().toLowerCase();
        const isTrainerBase = baseRole === 'trainer';
        const isOperatorBase = baseRole === 'operator';
        const initialStatus = normalizeModalStatusValue(base.status);
        const initialDate = todayInputDate();
        const defaults = {
        rate: base.rate ?? 1.0,
        direction_id: isTrainerBase ? "" : (base.direction_id ?? ""),
        supervisor_id: isTrainerBase ? "" : (base.supervisor_id ?? (isAdminLikeRequester ? "" : (user?.id ?? ""))),
        status: initialStatus,
        gender: base.gender ?? "",
        birth_date: base.birth_date ?? "",
        status_period_start_date: initialDate,
        status_period_end_date: isDismissalLikeStatus(initialStatus) ? "" : initialDate,
        status_period_dismissal_reason: "",
        status_period_is_blacklist: !!(base.status_period_is_blacklist ?? base.isBlacklist ?? base.is_blacklist),
        status_period_comment: "",
        phone: base.phone ?? "",
        email: base.email ?? "",
        instagram: base.instagram ?? "",
        telegram_nick: base.telegram_nick ?? "",
        study_place: base.study_place ?? "",
        study_course: base.study_course ?? "",
        study_completed: !!base.study_completed,
        study_completion_year: base.study_completion_year ?? "",
        card_number: base.card_number ?? "",
        close_contact_1_relation: base.close_contact_1_relation ?? "",
        close_contact_1_full_name: base.close_contact_1_full_name ?? "",
        close_contact_1_phone: base.close_contact_1_phone ?? "",
        close_contact_2_relation: base.close_contact_2_relation ?? "",
        close_contact_2_full_name: base.close_contact_2_full_name ?? "",
        close_contact_2_phone: base.close_contact_2_phone ?? "",
        company_name: base.company_name ?? "",
        employment_type: base.employment_type ?? "",
        internship_in_company: !!base.internship_in_company,
        front_office_training: !!base.front_office_training,
        front_office_training_date: base.front_office_training_date ?? "",
        taxipro_id: base.taxipro_id ?? "",
        has_proxy: !!base.has_proxy,
        proxy_card_number: base.proxy_card_number ?? "",
        has_driver_license: !!base.has_driver_license,
        sip_number: base.sip_number ?? "",
        use_schedule_status_period: false,
        ...base,
        role: baseRole || String(base.role || '').trim().toLowerCase(),
        };
        if (isTrainerBase) {
            defaults.direction_id = "";
            defaults.supervisor_id = "";
        }
        defaults.phone = String(defaults.phone ?? '').trim();
        defaults.email = String(defaults.email ?? '').trim();
        defaults.instagram = String(defaults.instagram ?? '').trim();
        defaults.telegram_nick = String(defaults.telegram_nick ?? '').trim();
        defaults.study_place = String(defaults.study_place ?? '').trim();
        defaults.study_course = String(defaults.study_course ?? '').trim();
        defaults.study_completed = !!defaults.study_completed;
        defaults.study_completion_year = String(defaults.study_completion_year ?? '').trim();
        defaults.card_number = String(defaults.card_number ?? '').trim();
        defaults.close_contact_1_relation = String(defaults.close_contact_1_relation ?? '').trim();
        defaults.close_contact_1_full_name = String(defaults.close_contact_1_full_name ?? '').trim();
        defaults.close_contact_1_phone = String(defaults.close_contact_1_phone ?? '').trim();
        defaults.close_contact_2_relation = String(defaults.close_contact_2_relation ?? '').trim();
        defaults.close_contact_2_full_name = String(defaults.close_contact_2_full_name ?? '').trim();
        defaults.close_contact_2_phone = String(defaults.close_contact_2_phone ?? '').trim();
        defaults.company_name = String(defaults.company_name ?? '').trim();
        defaults.employment_type = ['gph', 'of'].includes(String(defaults.employment_type || '').trim().toLowerCase())
            ? String(defaults.employment_type || '').trim().toLowerCase()
            : "";
        defaults.internship_in_company = !!defaults.internship_in_company;
        defaults.front_office_training = !!defaults.front_office_training;
        defaults.front_office_training_date = defaults.front_office_training
            ? String(defaults.front_office_training_date ?? '').trim()
            : "";
        defaults.taxipro_id = String(defaults.taxipro_id ?? '').trim();
        defaults.has_proxy = !!defaults.has_proxy;
        defaults.proxy_card_number = String(defaults.proxy_card_number ?? '').trim();
        defaults.has_driver_license = !!defaults.has_driver_license;
        defaults.sip_number = isOperatorBase ? String(defaults.sip_number ?? '').trim() : "";
        if (defaults.status === 'unpaid_leave' || defaults.status === 'dismissal') {
            defaults.status = normalizeModalStatusValue(defaults.status);
            defaults.use_schedule_status_period = true;
            if (!defaults.status_period_start_date) defaults.status_period_start_date = initialDate;
            if (!defaults.status_period_end_date && !isDismissalLikeStatus(defaults.status)) defaults.status_period_end_date = initialDate;
        }
        setEditedUser(defaults);
        setModalError("");
        setCreatedCredentials(null);
        setActiveTab("data");
        revokeAvatarPreviewUrl();
        closeAvatarCropEditor();
        setAvatarPreviewUrl((base.avatar_url || '').trim());
        setAvatarUploadFile(null);
        setAvatarOriginalFile(null);
        setAvatarRemoveRequested(false);
        setAvatarError("");
        setIsAvatarProcessing(false);
        if (avatarInputRef.current) avatarInputRef.current.value = '';
    }, [userToEdit, user, revokeAvatarPreviewUrl, closeAvatarCropEditor]);

    useEffect(() => {
        if (isOpen) {
        setTimeout(() => {
            nameRef.current?.focus();
        }, 50);
        }
    }, [isOpen]);

    useEffect(() => (() => {
        revokeAvatarPreviewUrl();
        revokeAvatarCropSourceUrl();
    }), [revokeAvatarPreviewUrl, revokeAvatarCropSourceUrl]);

    const copyToClipboard = (text) => {
        if (!text) return;
        navigator.clipboard?.writeText(text).then(
        () => {
            // не критично — можно показать toast если есть реализация
        },
        () => {
            // fallback
        }
        );
    };

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

        setAvatarError("");
        setIsAvatarProcessing(true);
        try {
            if (!String(sourceFile.type || '').startsWith('image/')) {
                throw new Error("Можно загружать только изображения");
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
            setAvatarError(error?.message || "Не удалось обработать аватар");
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
        setAvatarError("");
        setIsAvatarProcessing(true);
        try {
            const compressedAvatar = await compressAvatarImageFile(avatarCropState.sourceFile, avatarCropState);
            if (compressedAvatar.size > AVATAR_MAX_BYTES) {
                throw new Error("Аватар слишком большой после сжатия");
            }
            setAvatarUploadFile(compressedAvatar);
            setAvatarOriginalFile(avatarCropState.sourceFile);
            setAvatarRemoveRequested(false);
            applyAvatarPreviewFromFile(compressedAvatar);
            closeAvatarCropEditor();
        } catch (error) {
            setAvatarError(error?.message || "Не удалось обработать аватар");
        } finally {
            setIsAvatarProcessing(false);
        }
    };

    const handleAvatarRemove = () => {
        revokeAvatarPreviewUrl();
        closeAvatarCropEditor();
        setAvatarPreviewUrl("");
        setAvatarUploadFile(null);
        setAvatarOriginalFile(null);
        setAvatarRemoveRequested(true);
        setAvatarError("");
        if (avatarInputRef.current) avatarInputRef.current.value = '';
    };

    const resetForCreate = () => {
        const createRole = userToEdit?.role || editedUser?.role || "operator";
        const isTrainerCreateRole = String(createRole || '').trim().toLowerCase() === 'trainer';
        revokeAvatarPreviewUrl();
        closeAvatarCropEditor();
        setEditedUser({
        name: "",
        rate: 1.0,
        hire_date: "",
        birth_date: "",
        gender: "",
        direction_id: "",
        supervisor_id: isTrainerCreateRole ? "" : (isAdminLikeRequester ? "" : (user?.id ?? "")),
        status: "working",
        role: createRole,
        status_period_start_date: todayInputDate(),
        status_period_end_date: todayInputDate(),
        status_period_dismissal_reason: "",
        status_period_is_blacklist: false,
        status_period_comment: "",
        phone: "",
        email: "",
        instagram: "",
        telegram_nick: "",
        study_place: "",
        study_course: "",
        study_completed: false,
        study_completion_year: "",
        card_number: "",
        close_contact_1_relation: "",
        close_contact_1_full_name: "",
        close_contact_1_phone: "",
        close_contact_2_relation: "",
        close_contact_2_full_name: "",
        close_contact_2_phone: "",
        company_name: "",
        employment_type: "",
        internship_in_company: false,
        front_office_training: false,
        front_office_training_date: "",
        taxipro_id: "",
        has_proxy: false,
        proxy_card_number: "",
        has_driver_license: false,
        sip_number: "",
        use_schedule_status_period: false,
        });
        setModalError("");
        setCreatedCredentials(null);
        setActiveTab("data");
        setAvatarPreviewUrl("");
        setAvatarUploadFile(null);
        setAvatarOriginalFile(null);
        setAvatarRemoveRequested(false);
        setAvatarError("");
        setIsAvatarProcessing(false);
        if (avatarInputRef.current) avatarInputRef.current.value = '';
        setTimeout(() => nameRef.current?.focus(), 50);
    };

    const handleSave = async () => {
        const isCreateMode = !editedUser?.id;
        const isTrainerUser = isTrainerDraft(editedUser);
        const isOperatorUser = isOperatorDraft(editedUser);

        // Простая локальная валидация
        if (!editedUser || !editedUser.name || editedUser.name.trim().length === 0) {
        setModalError("Имя обязательно.");
        return;
        }

        if (isCreateMode && isOperatorUser && isAdminLikeRequester && !editedUser.supervisor_id) {
        setModalError("Супервайзер обязателен.");
        return;
        }

        if (isOperatorUser && !editedUser.direction_id) {
        setModalError("Направление обязательно.");
        return;
        }

        if (!editedUser.hire_date) {
        setModalError("Дата найма обязательна.");
        return;
        }

        if (isAvatarProcessing) {
        setModalError("Дождитесь завершения обработки аватара.");
        return;
        }

        if (avatarCropState) {
        setModalError("Завершите обрезку аватара (Применить или Отмена).");
        return;
        }

        const normalizedEmail = String(editedUser?.email || '').trim();
        if (normalizedEmail && !normalizedEmail.includes('@')) {
        setModalError("Введите корректную почту.");
        return;
        }

        const normalizedPhone = String(editedUser?.phone || '').trim();
        if (!isValidKzPhone(normalizedPhone)) {
        setModalError(`Номер телефона должен быть в формате ${KZ_PHONE_PLACEHOLDER}`);
        return;
        }
        const normalizedCloseContact1Phone = String(editedUser?.close_contact_1_phone || '').trim();
        if (!isValidKzPhone(normalizedCloseContact1Phone)) {
        setModalError(`Телефон близкого контакта 1 должен быть в формате ${KZ_PHONE_PLACEHOLDER}`);
        return;
        }
        const normalizedCloseContact2Phone = String(editedUser?.close_contact_2_phone || '').trim();
        if (!isValidKzPhone(normalizedCloseContact2Phone)) {
        setModalError(`Телефон близкого контакта 2 должен быть в формате ${KZ_PHONE_PLACEHOLDER}`);
        return;
        }
        if (editedUser?.front_office_training && !toDateInputValue(editedUser?.front_office_training_date)) {
        setModalError("Если сотрудник был на обучении во фронт офисе, укажите дату.");
        return;
        }
        const studyCompletionYearRaw = String(editedUser?.study_completion_year || '').trim();
        let studyCompletionYear = null;
        if (studyCompletionYearRaw) {
            if (!/^\d{4}$/.test(studyCompletionYearRaw)) {
                setModalError("Год завершения учебы должен состоять из 4 цифр.");
                return;
            }
            const parsedStudyCompletionYear = Number(studyCompletionYearRaw);
            if (!Number.isInteger(parsedStudyCompletionYear) || parsedStudyCompletionYear < 1900 || parsedStudyCompletionYear > 2100) {
                setModalError("Год завершения учебы должен быть в диапазоне 1900-2100.");
                return;
            }
            studyCompletionYear = parsedStudyCompletionYear;
        }

        if (usesScheduleStatusPeriodForm(editedUser?.status) && editedUser?.use_schedule_status_period) {
        const startDate = String(editedUser?.status_period_start_date || "").trim();
        const endDate = String(editedUser?.status_period_end_date || "").trim();
        const isBlacklistDismissal = isDismissalLikeStatus(editedUser?.status) && !!editedUser?.status_period_is_blacklist;
        if (!startDate) {
            setModalError("Для статусного периода укажите дату начала.");
            return;
        }
        if (!isDismissalLikeStatus(editedUser?.status) && !endDate) {
            setModalError("Для статусного периода укажите дату окончания.");
            return;
        }
        if (isBlacklistDismissal && endDate) {
            setModalError("Для ЧС-увольнения дата окончания не используется.");
            return;
        }
        if (isDismissalLikeStatus(editedUser?.status)) {
            if (!String(editedUser?.status_period_dismissal_reason || "").trim()) {
                setModalError("Для увольнения укажите причину.");
                return;
            }
            if (!String(editedUser?.status_period_comment || "").trim()) {
                setModalError("Для увольнения комментарий обязателен.");
                return;
            }
        }
        }

        setModalError("");
        setIsLoading(true);

        try {
        const normalizedUserDraft = {
            ...editedUser,
            phone: String(editedUser?.phone || '').trim(),
            email: normalizedEmail,
            instagram: String(editedUser?.instagram || '').trim(),
            telegram_nick: String(editedUser?.telegram_nick || '').trim(),
            study_place: String(editedUser?.study_place || '').trim(),
            study_course: String(editedUser?.study_course || '').trim(),
            study_completed: !!editedUser?.study_completed,
            study_completion_year: studyCompletionYear,
            card_number: String(editedUser?.card_number || '').trim(),
            close_contact_1_relation: String(editedUser?.close_contact_1_relation || '').trim(),
            close_contact_1_full_name: String(editedUser?.close_contact_1_full_name || '').trim(),
            close_contact_1_phone: String(editedUser?.close_contact_1_phone || '').trim(),
            close_contact_2_relation: String(editedUser?.close_contact_2_relation || '').trim(),
            close_contact_2_full_name: String(editedUser?.close_contact_2_full_name || '').trim(),
            close_contact_2_phone: String(editedUser?.close_contact_2_phone || '').trim(),
            company_name: String(editedUser?.company_name || '').trim(),
            employment_type: ['gph', 'of'].includes(String(editedUser?.employment_type || '').trim().toLowerCase())
                ? String(editedUser?.employment_type || '').trim().toLowerCase()
                : '',
            internship_in_company: !!editedUser?.internship_in_company,
            front_office_training: !!editedUser?.front_office_training,
            front_office_training_date: !!editedUser?.front_office_training
                ? String(editedUser?.front_office_training_date || '').trim()
                : '',
            taxipro_id: String(editedUser?.taxipro_id || '').trim(),
            has_proxy: !!editedUser?.has_proxy,
            proxy_card_number: isOperatorUser && !!editedUser?.has_proxy ? String(editedUser?.proxy_card_number || '').trim() : '',
            has_driver_license: !!editedUser?.has_driver_license,
            sip_number: isOperatorUser ? String(editedUser?.sip_number || '').trim() : ''
        };
        const normalizedUser = isTrainerUser
            ? { ...normalizedUserDraft, supervisor_id: null, direction_id: null, proxy_card_number: '', sip_number: '' }
            : normalizedUserDraft;
        const result = await onSave({
            ...normalizedUser,
            avatar_file: avatarUploadFile || null,
            avatar_original_file: avatarUploadFile ? (avatarOriginalFile || null) : null,
            avatar_remove: !!avatarRemoveRequested
        }); // ожидаем, что onSave возвращает результат от бэка при создании

        // Если мы в режиме создания (нет id у редактируемого пользователя) — не закрываем модалку,
        // а показываем логин/пароль, если бэк их вернул
        if (!editedUser.id) {
            // Попытки найти креды в разных форматах ответа
            const login =
            result?.login ?? result?.data?.login ?? result?.credentials?.login ?? null;
            const password =
            result?.password ?? result?.data?.password ?? result?.credentials?.password ?? null;

            if (login || password) {
            setActiveTab("account");
            setCreatedCredentials({ login: login || "-", password: password || "-" });
            } else {
            // Если бэк не вернул креды — просто закрываем модалку / или можно показать сообщение
            // Здесь оставим сообщение и позволим админу закрыть вручную
            setModalError("Пользователь создан, но бэк не вернул логин/пароль.");
            // Можно вызвать fetchUsers по внешнему коду; предполагается, что onSave делает это.
            }
        } else {
            // режим редактирования — закрываем модалку после успешного onSave
            closeAvatarCropEditor();
            onClose();
        }
        } catch (error) {
        console.error("Error saving user:", error);
        const serverMsg =
            error?.response?.data?.error || error?.message || "Не удалось сохранить пользователя. Попробуйте ещё раз.";
        setModalError(serverMsg);
        } finally {
        setIsLoading(false);
        }
    };

    if (!isOpen) return null;

    const isCreateMode = !editedUser?.id;
    const tabs = [
        { id: "general", label: "Общее" },
        { id: "data", label: "Данные" },
        { id: "contacts", label: "Контакты" },
        { id: "corporate", label: "Корпоративное" },
        {
            id: "account",
            label: <FaIcon className="fa-solid fa-lock" aria-hidden="true" />,
            title: "Аккаунт"
        }
    ];
    const avatarInitial = String(editedUser?.name || 'U').charAt(0).toUpperCase();
    const avatarDisabled = isLoading || !!createdCredentials || isAvatarProcessing || !!avatarCropState;
    const avatarCropViewStyle = React.useMemo(() => {
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
    const renderAvatarEditor = () => (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
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
                    Аватар будет удалён после сохранения.
                </div>
            )}
            {avatarError && (
                <div className="mt-2 text-xs text-red-600">{avatarError}</div>
            )}
        </div>
    );

    return (
        <>
        {/* Backdrop */}
        <div
            className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-300"
            onClick={() => {
            setModalError("");
            setCreatedCredentials(null);
            closeAvatarCropEditor();
            onClose();
            }}
            aria-hidden="true"
        />

        {/* Modal container (catch Escape) */}
        <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            tabIndex={-1}
            onKeyDown={(e) => {
            if (e.key === "Escape") {
                    setModalError("");
                    setCreatedCredentials(null);
                    closeAvatarCropEditor();
                    onClose();
                    }
            }}
        >
            <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="edit-user-title"
            className="pointer-events-auto w-full max-w-lg bg-white/95 dark:bg-slate-900/95 rounded-2xl shadow-2xl overflow-hidden transform transition-all duration-300 animate-scale-in"
            onClick={(e) => e.stopPropagation()}
            >
            <div className="px-6 py-5 max-h-[88vh] overflow-y-auto">
                {/* Header */}
                <div className="flex items-start justify-between gap-4">
                <div className="flex flex-col gap-0.5">
                    <h2 id="edit-user-title" className="text-2xl font-bold text-gray-800 dark:text-gray-100 flex items-center gap-2">
                    {isCreateMode ?  <FaIcon className="fas fa-user-edit text-blue-600"></FaIcon> : <FaIcon className="fas fa-pen text-blue-600"></FaIcon>}
                    {isCreateMode ? "Добавить сотрудника" : "Редактировать сотрудника"}
                    </h2>
                    {editedUser?.name && !isCreateMode && (
                    <div className="mt-1 text-lg font-semibold text-blue-700 pl-9">{editedUser.name}</div>
                    )}
                </div>

                <button
                    type="button"
                    onClick={() => {
                    setModalError("");
                    setCreatedCredentials(null);
                    closeAvatarCropEditor();
                    onClose();
                    }}
                    aria-label="Закрыть"
                    className="rounded-md p-2 text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-slate-800 transition"
                >
                    <FaIcon className="fas fa-times text-lg" />
                </button>
                </div>

                <div className="mt-4 space-y-6">
                {!createdCredentials && (
                    <div className="grid grid-cols-5 gap-2 rounded-xl bg-gray-100 p-1">
                    {tabs.map((tab) => (
                        <button
                        key={tab.id}
                        type="button"
                        onClick={() => setActiveTab(tab.id)}
                        className={`px-3 py-2 rounded-lg text-sm font-medium transition ${
                            activeTab === tab.id
                            ? "bg-white text-blue-700 shadow-sm"
                            : "text-gray-600 hover:text-gray-900"
                        }`}
                        aria-pressed={activeTab === tab.id}
                        aria-label={tab.title || (typeof tab.label === 'string' ? tab.label : undefined)}
                        title={tab.title || undefined}
                        >
                        {tab.label}
                        </button>
                    ))}
                    </div>
                )}
                {isCreateMode &&(
                <>
                {activeTab === "data" && (
                    <>
                    {renderAvatarEditor()}
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Имя</label>
                        <input
                        ref={nameRef}
                        type="text"
                        value={editedUser?.name || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, name: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Пол</label>
                        <select
                        value={editedUser?.gender || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, gender: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        >
                        <option value="">Не указан</option>
                        <option value="male">Мужской</option>
                        <option value="female">Женский</option>
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Дата рождения</label>
                        <input
                        type="date"
                        value={toDateInputValue(editedUser?.birth_date)}
                        max={todayInputDate()}
                        onChange={(e) => setEditedUser({ ...editedUser, birth_date: e.target.value || null })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Место учебы</label>
                        <input
                        type="text"
                        value={editedUser?.study_place || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, study_place: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Курс</label>
                        <input
                        type="text"
                        value={editedUser?.study_course || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, study_course: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>

                    <div>
                        <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={!!editedUser?.study_completed}
                            onChange={(e) => setEditedUser({ ...editedUser, study_completed: e.target.checked })}
                            className="rounded border-gray-300"
                            disabled={isLoading || !!createdCredentials}
                        />
                        <span>Завершил учебу</span>
                        </label>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Год завершения/план</label>
                        <input
                        type="number"
                        min="1900"
                        max="2100"
                        step="1"
                        value={editedUser?.study_completion_year ?? ""}
                        onChange={(e) => setEditedUser({ ...editedUser, study_completion_year: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Номер карты</label>
                        <input
                        type="text"
                        value={editedUser?.card_number || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, card_number: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>
                    </>
                )}

                {activeTab === "contacts" && (
                    <>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Номер телефона</label>
                        <input
                        type="text"
                        value={editedUser?.phone || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, phone: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        placeholder={KZ_PHONE_PLACEHOLDER}
                        maxLength={12}
                        inputMode="tel"
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Почта</label>
                        <input
                        type="email"
                        value={editedUser?.email || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, email: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Инстаграм</label>
                        <input
                        type="text"
                        value={editedUser?.instagram || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, instagram: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Ник Telegram</label>
                        <input
                        type="text"
                        value={editedUser?.telegram_nick || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, telegram_nick: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        placeholder="@username"
                        />
                    </div>

                    <details className="rounded-lg border border-slate-200 bg-slate-50 p-3" open>
                        <summary className="cursor-pointer text-sm font-medium text-slate-700">Близкий контакт 1</summary>
                        <div className="mt-3 space-y-3">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Кем приходится</label>
                                <input
                                type="text"
                                value={editedUser?.close_contact_1_relation || ""}
                                onChange={(e) => setEditedUser({ ...editedUser, close_contact_1_relation: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading || !!createdCredentials}
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">ФИО</label>
                                <input
                                type="text"
                                value={editedUser?.close_contact_1_full_name || ""}
                                onChange={(e) => setEditedUser({ ...editedUser, close_contact_1_full_name: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading || !!createdCredentials}
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Номер</label>
                                <input
                                type="text"
                                value={editedUser?.close_contact_1_phone || ""}
                                onChange={(e) => setEditedUser({ ...editedUser, close_contact_1_phone: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading || !!createdCredentials}
                                placeholder={KZ_PHONE_PLACEHOLDER}
                                maxLength={12}
                                inputMode="tel"
                                />
                            </div>
                        </div>
                    </details>

                    <details className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                        <summary className="cursor-pointer text-sm font-medium text-slate-700">Близкий контакт 2</summary>
                        <div className="mt-3 space-y-3">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Кем приходится</label>
                                <input
                                type="text"
                                value={editedUser?.close_contact_2_relation || ""}
                                onChange={(e) => setEditedUser({ ...editedUser, close_contact_2_relation: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading || !!createdCredentials}
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">ФИО</label>
                                <input
                                type="text"
                                value={editedUser?.close_contact_2_full_name || ""}
                                onChange={(e) => setEditedUser({ ...editedUser, close_contact_2_full_name: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading || !!createdCredentials}
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Номер</label>
                                <input
                                type="text"
                                value={editedUser?.close_contact_2_phone || ""}
                                onChange={(e) => setEditedUser({ ...editedUser, close_contact_2_phone: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading || !!createdCredentials}
                                placeholder={KZ_PHONE_PLACEHOLDER}
                                maxLength={12}
                                inputMode="tel"
                                />
                            </div>
                        </div>
                    </details>
                    </>
                )}

                {activeTab === "corporate" && (
                    <>
                    <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Дата найма</label>
                    <input
                        type="date"
                        value={toDateInputValue(editedUser?.hire_date)}
                        onChange={(e) => setEditedUser({ ...editedUser, hire_date: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                    />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Наименование ТОО/ИП</label>
                        <input
                        type="text"
                        value={editedUser?.company_name || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, company_name: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Оформлен ГПХ/ОФ</label>
                        <select
                        value={editedUser?.employment_type || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, employment_type: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        >
                        <option value="">Не указано</option>
                        <option value="gph">ГПХ</option>
                        <option value="of">ОФ</option>
                        </select>
                    </div>

                    <div>
                        <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={!!editedUser?.internship_in_company}
                            onChange={(e) => setEditedUser({ ...editedUser, internship_in_company: e.target.checked })}
                            className="rounded border-gray-300"
                            disabled={isLoading || !!createdCredentials}
                        />
                        <span>Проходил практику в компании</span>
                        </label>
                    </div>

                    <div>
                        <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={!!editedUser?.front_office_training}
                            onChange={(e) => setEditedUser({
                                ...editedUser,
                                front_office_training: e.target.checked,
                                front_office_training_date: e.target.checked ? (editedUser?.front_office_training_date || "") : ""
                            })}
                            className="rounded border-gray-300"
                            disabled={isLoading || !!createdCredentials}
                        />
                        <span>Был во фронт офисе на обучении</span>
                        </label>
                    </div>

                    {editedUser?.front_office_training && (
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Когда был на обучении</label>
                        <input
                        type="date"
                        value={toDateInputValue(editedUser?.front_office_training_date)}
                        onChange={(e) => setEditedUser({ ...editedUser, front_office_training_date: e.target.value || "" })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>
                    )}

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">ID таксипро</label>
                        <input
                        type="text"
                        value={editedUser?.taxipro_id || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, taxipro_id: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>
                    </>
                )}

                {activeTab === "general" && (
                    <>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Статус</label>
                        <select
                        value={editedUser?.status || "working"}
                        onChange={(e) => {
                            const nextStatus = e.target.value;
                            const currentStart = editedUser?.status_period_start_date || todayInputDate();
                            setEditedUser({
                                ...editedUser,
                                status: nextStatus,
                                status_period_start_date: currentStart,
                                status_period_end_date: isDismissalLikeStatus(nextStatus) ? "" : (editedUser?.status_period_end_date || currentStart),
                                status_period_is_blacklist: isDismissalLikeStatus(nextStatus) ? !!editedUser?.status_period_is_blacklist : false,
                                use_schedule_status_period: usesScheduleStatusPeriodForm(nextStatus) ? true : editedUser?.use_schedule_status_period
                            });
                        }}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        >
                        <option value="working">Работает</option>
                        <option value="fired">Уволен</option>
                        <option value="bs">Б/С</option>
                        <option value="sick_leave">Больничный</option>
                        <option value="annual_leave">Ежегодный отпуск</option>
                        </select>
                    </div>

                    {usesScheduleStatusPeriodForm(editedUser?.status) && (
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-3">
                        <div className="text-xs text-slate-600">
                            Для этих статусов используется логика планировщика: статус сохраняется как период.
                        </div>
                        <div className={`grid grid-cols-1 ${shouldShowStatusPeriodEndDate(editedUser) ? 'sm:grid-cols-2' : ''} gap-3`}>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Дата начала</label>
                                <input
                                    type="date"
                                    value={editedUser?.status_period_start_date || ""}
                                    onChange={(e) => setEditedUser({
                                        ...editedUser,
                                        status_period_start_date: e.target.value,
                                        use_schedule_status_period: true,
                                        status_period_end_date: (!isDismissalLikeStatus(editedUser?.status) && (!editedUser?.status_period_end_date || editedUser.status_period_end_date < e.target.value))
                                            ? e.target.value
                                            : editedUser?.status_period_end_date
                                    })}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading || !!createdCredentials}
                                />
                            </div>
                            {shouldShowStatusPeriodEndDate(editedUser) && (
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">
                                    {isDismissalLikeStatus(editedUser?.status) ? 'Дата окончания (необ.)' : 'Дата окончания'}
                                </label>
                                <input
                                    type="date"
                                    value={editedUser?.status_period_end_date || ""}
                                    min={editedUser?.status_period_start_date || ""}
                                    onChange={(e) => setEditedUser({ ...editedUser, status_period_end_date: e.target.value, use_schedule_status_period: true })}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading || !!createdCredentials}
                                />
                            </div>
                            )}
                        </div>
                        {isDismissalLikeStatus(editedUser?.status) && (
                            <>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Причина увольнения</label>
                                <select
                                    value={editedUser?.status_period_dismissal_reason || ""}
                                    onChange={(e) => {
                                        const nextReason = e.target.value;
                                        setEditedUser((prev) => ({
                                            ...prev,
                                            status_period_dismissal_reason: nextReason,
                                            use_schedule_status_period: true,
                                            status_period_end_date: (!prev?.status_period_is_blacklist && nextReason === DISMISSAL_REASON_WITH_END_DATE)
                                                ? (prev?.status_period_end_date || '')
                                                : ''
                                        }));
                                    }}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading || !!createdCredentials}
                                >
                                    <option value="">Выберите причину</option>
                                    {DISMISSAL_REASON_OPTIONS.map(reason => (
                                        <option key={reason} value={reason}>{reason}</option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={!!editedUser?.status_period_is_blacklist}
                                        onChange={(e) => setEditedUser((prev) => ({
                                            ...prev,
                                            status_period_is_blacklist: e.target.checked,
                                            use_schedule_status_period: true,
                                            status_period_end_date: e.target.checked ? '' : prev?.status_period_end_date
                                        }))}
                                        className="rounded border-gray-300"
                                        disabled={isLoading || !!createdCredentials}
                                    />
                                    <span>ЧС (без возможности восстановления)</span>
                                </label>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Комментарий (обязательно)</label>
                                <textarea
                                    value={editedUser?.status_period_comment || ""}
                                    onChange={(e) => setEditedUser({ ...editedUser, status_period_comment: e.target.value, use_schedule_status_period: true })}
                                    rows={3}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading || !!createdCredentials}
                                />
                            </div>
                            <div className="text-xs text-slate-500">
                                Для увольнения обязательны дата начала, причина и комментарий.
                            </div>
                            </>
                        )}
                    </div>
                    )}

                    {isAdminLikeRequester && isOperatorDraft(editedUser) && (
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Супервайзер</label>
                        <select
                        value={editedUser?.supervisor_id || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, supervisor_id: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        >
                        <option value="">Выберите супервайзера</option>
                        {(svList || [])
                            .filter(sv => sv.status === 'working' || sv.status === 'unpaid_leave' || !sv.status)
                            .map((sv) => (
                            <option key={sv.id} value={sv.id}>
                                {sv.name}
                            </option>
                            ))}
                        </select>
                    </div>
                    )}

                    {(!isSupervisorRequester || isOperatorDraft(editedUser)) && (
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Ставка</label>
                        <select
                        value={editedUser?.rate ?? 1.0}
                        onChange={(e) => setEditedUser({ ...editedUser, rate: parseFloat(e.target.value) })}
                        className={`w-full px-3 py-2 border rounded-lg shadow-sm focus:outline-none transition-all ${
                            isSupervisorRateLocked
                                ? 'border-slate-200 bg-slate-100 text-slate-400 cursor-not-allowed dark:border-slate-700 dark:bg-slate-800/70 dark:text-slate-400'
                                : 'border-gray-300 focus:ring-2 focus:ring-blue-500 bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100'
                        }`}
                        disabled={isLoading || !!createdCredentials || isSupervisorRateLocked}
                        >
                        <option value={1.0}>1.00</option>
                        <option value={0.75}>0.75</option>
                        <option value={0.5}>0.50</option>
                        </select>
                        {isSupervisorRateLocked && (
                        <p className="mt-1 text-xs text-slate-500">
                            Для супервайзеров изменение ставки доступно только 1-го числа месяца.
                        </p>
                        )}
                    </div>
                    )}

                    {isOperatorDraft(editedUser) && (
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Направление</label>
                        <select
                        value={editedUser?.direction_id || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, direction_id: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        >
                        <option value="">Выберите направление</option>
                        {directions.map((dir) => (
                            <option key={dir.id} value={dir.id}>
                            {dir.name}
                            </option>
                        ))}
                        </select>
                    </div>
                    )}

                    {isOperatorDraft(editedUser) && (
                    <>
                    <div>
                        <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={!!editedUser?.has_proxy}
                            onChange={(e) => setEditedUser({ ...editedUser, has_proxy: e.target.checked })}
                            className="rounded border-gray-300"
                            disabled={isLoading || !!createdCredentials}
                        />
                        <span>Наличие прокси</span>
                        </label>
                    </div>
                    <div>
                        <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={!!editedUser?.has_driver_license}
                            onChange={(e) => setEditedUser({ ...editedUser, has_driver_license: e.target.checked })}
                            className="rounded border-gray-300"
                            disabled={isLoading || !!createdCredentials}
                        />
                        <span>Наличие водительских прав</span>
                        </label>
                    </div>
                    {editedUser?.has_proxy && (
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Номер прокси карты</label>
                        <input
                        type="text"
                        value={editedUser?.proxy_card_number || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, proxy_card_number: e.target.value })}
                        placeholder="Можно указать не полностью"
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>
                    )}
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">SIP номер</label>
                        <input
                        type="text"
                        value={editedUser?.sip_number || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, sip_number: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading || !!createdCredentials}
                        />
                    </div>
                    </>
                    )}
                    </>
                )}

                {activeTab === "account" && !createdCredentials && (
                    <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
                    Логин и пароль будут сгенерированы автоматически после создания сотрудника и показаны в этой модалке.
                    </div>
                )}
                </>
                )}
                

                {/* --- Режим редактирования: показываем остальные поля как раньше --- */}
                {!isCreateMode && (
                    <>
                    {activeTab === "data" && (
                        <>
                        {renderAvatarEditor()}
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Имя</label>
                            <input
                            ref={nameRef}
                            type="text"
                            value={editedUser?.name || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, name: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Пол</label>
                            <select
                            value={editedUser?.gender || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, gender: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            >
                            <option value="">Не указан</option>
                            <option value="male">Мужской</option>
                            <option value="female">Женский</option>
                            </select>
                        </div>

                        <div>
                            <label htmlFor="birthDate" className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">
                            Дата рождения
                            </label>
                            <div className="flex items-center gap-2">
                            <input
                                type="date"
                                id="birthDate"
                                value={toDateInputValue(editedUser?.birth_date)}
                                max={todayInputDate()}
                                onChange={(e) => setEditedUser({ ...editedUser, birth_date: e.target.value || null })}
                                className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading}
                            />
                            {editedUser?.birth_date && (
                                <span className="text-gray-600 text-xs whitespace-nowrap">
                                Текущая: {toDateInputValue(editedUser.birth_date)}
                                </span>
                            )}
                            </div>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Место учебы</label>
                            <input
                            type="text"
                            value={editedUser?.study_place || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, study_place: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Курс</label>
                            <input
                            type="text"
                            value={editedUser?.study_course || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, study_course: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>

                        <div>
                            <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={!!editedUser?.study_completed}
                                onChange={(e) => setEditedUser({ ...editedUser, study_completed: e.target.checked })}
                                className="rounded border-gray-300"
                                disabled={isLoading}
                            />
                            <span>Завершил учебу</span>
                            </label>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Год завершения/план</label>
                            <input
                            type="number"
                            min="1900"
                            max="2100"
                            step="1"
                            value={editedUser?.study_completion_year ?? ""}
                            onChange={(e) => setEditedUser({ ...editedUser, study_completion_year: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Номер карты</label>
                            <input
                            type="text"
                            value={editedUser?.card_number || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, card_number: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>
                        </>
                    )}

                    {activeTab === "contacts" && (
                        <>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Номер телефона</label>
                            <input
                            type="text"
                            value={editedUser?.phone || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, phone: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            placeholder={KZ_PHONE_PLACEHOLDER}
                            maxLength={12}
                            inputMode="tel"
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Почта</label>
                            <input
                            type="email"
                            value={editedUser?.email || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, email: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Инстаграм</label>
                            <input
                            type="text"
                            value={editedUser?.instagram || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, instagram: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Ник Telegram</label>
                            <input
                            type="text"
                            value={editedUser?.telegram_nick || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, telegram_nick: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            placeholder="@username"
                            />
                        </div>

                        <details className="rounded-lg border border-slate-200 bg-slate-50 p-3" open>
                            <summary className="cursor-pointer text-sm font-medium text-slate-700">Близкий контакт 1</summary>
                            <div className="mt-3 space-y-3">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Кем приходится</label>
                                    <input
                                    type="text"
                                    value={editedUser?.close_contact_1_relation || ""}
                                    onChange={(e) => setEditedUser({ ...editedUser, close_contact_1_relation: e.target.value })}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading}
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">ФИО</label>
                                    <input
                                    type="text"
                                    value={editedUser?.close_contact_1_full_name || ""}
                                    onChange={(e) => setEditedUser({ ...editedUser, close_contact_1_full_name: e.target.value })}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading}
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Номер</label>
                                    <input
                                    type="text"
                                    value={editedUser?.close_contact_1_phone || ""}
                                    onChange={(e) => setEditedUser({ ...editedUser, close_contact_1_phone: e.target.value })}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading}
                                    placeholder={KZ_PHONE_PLACEHOLDER}
                                    maxLength={12}
                                    inputMode="tel"
                                    />
                                </div>
                            </div>
                        </details>

                        <details className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                            <summary className="cursor-pointer text-sm font-medium text-slate-700">Близкий контакт 2</summary>
                            <div className="mt-3 space-y-3">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Кем приходится</label>
                                    <input
                                    type="text"
                                    value={editedUser?.close_contact_2_relation || ""}
                                    onChange={(e) => setEditedUser({ ...editedUser, close_contact_2_relation: e.target.value })}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading}
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">ФИО</label>
                                    <input
                                    type="text"
                                    value={editedUser?.close_contact_2_full_name || ""}
                                    onChange={(e) => setEditedUser({ ...editedUser, close_contact_2_full_name: e.target.value })}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading}
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Номер</label>
                                    <input
                                    type="text"
                                    value={editedUser?.close_contact_2_phone || ""}
                                    onChange={(e) => setEditedUser({ ...editedUser, close_contact_2_phone: e.target.value })}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading}
                                    placeholder={KZ_PHONE_PLACEHOLDER}
                                    maxLength={12}
                                    inputMode="tel"
                                    />
                                </div>
                            </div>
                        </details>
                        </>
                    )}

                    {activeTab === "corporate" && (
                        <>
                        <div>
                            <label htmlFor="hireDate" className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">
                            Дата найма
                            </label>
                            <div className="flex items-center gap-2">
                            <input
                                type="date"
                                id="hireDate"
                                value={toDateInputValue(editedUser?.hire_date)}
                                onChange={(e) => setEditedUser({ ...editedUser, hire_date: e.target.value || null })}
                                className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading}
                            />
                            {editedUser?.hire_date && (
                                <span className="text-gray-600 text-xs whitespace-nowrap">
                                Текущая: {toDateInputValue(editedUser.hire_date)}
                                </span>
                            )}
                            </div>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Наименование ТОО/ИП</label>
                            <input
                            type="text"
                            value={editedUser?.company_name || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, company_name: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Оформлен ГПХ/ОФ</label>
                            <select
                            value={editedUser?.employment_type || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, employment_type: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            >
                            <option value="">Не указано</option>
                            <option value="gph">ГПХ</option>
                            <option value="of">ОФ</option>
                            </select>
                        </div>

                        <div>
                            <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={!!editedUser?.internship_in_company}
                                onChange={(e) => setEditedUser({ ...editedUser, internship_in_company: e.target.checked })}
                                className="rounded border-gray-300"
                                disabled={isLoading}
                            />
                            <span>Проходил практику в компании</span>
                            </label>
                        </div>

                        <div>
                            <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={!!editedUser?.front_office_training}
                                onChange={(e) => setEditedUser({
                                    ...editedUser,
                                    front_office_training: e.target.checked,
                                    front_office_training_date: e.target.checked ? (editedUser?.front_office_training_date || "") : ""
                                })}
                                className="rounded border-gray-300"
                                disabled={isLoading}
                            />
                            <span>Был во фронт офисе на обучении</span>
                            </label>
                        </div>

                        {editedUser?.front_office_training && (
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Когда был на обучении</label>
                            <input
                            type="date"
                            value={toDateInputValue(editedUser?.front_office_training_date)}
                            onChange={(e) => setEditedUser({ ...editedUser, front_office_training_date: e.target.value || "" })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>
                        )}

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">ID таксипро</label>
                            <input
                            type="text"
                            value={editedUser?.taxipro_id || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, taxipro_id: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>
                        </>
                    )}

                    {activeTab === "general" && (
                        <>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Статус</label>
                            <select
                            value={editedUser?.status || "working"}
                            onChange={(e) => {
                                const nextStatus = e.target.value;
                                const currentStart = editedUser?.status_period_start_date || todayInputDate();
                                setEditedUser({
                                    ...editedUser,
                                    status: nextStatus,
                                    status_period_start_date: currentStart,
                                    status_period_end_date: isDismissalLikeStatus(nextStatus) ? "" : (editedUser?.status_period_end_date || currentStart),
                                    status_period_is_blacklist: isDismissalLikeStatus(nextStatus) ? !!editedUser?.status_period_is_blacklist : false,
                                    use_schedule_status_period: usesScheduleStatusPeriodForm(nextStatus) ? true : editedUser?.use_schedule_status_period
                                });
                            }}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            >
                            <option value="working">Работает</option>
                            <option value="fired">Уволен</option>
                            <option value="bs">Б/С</option>
                            <option value="sick_leave">Больничный</option>
                            <option value="annual_leave">Ежегодный отпуск</option>
                            </select>
                        </div>

                        {usesScheduleStatusPeriodForm(editedUser?.status) && (
                        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-3">
                            <div className="text-xs text-slate-600">
                                Статус будет сохранен как период графика (аналогично планировщику).
                            </div>
                            <div className={`grid grid-cols-1 ${shouldShowStatusPeriodEndDate(editedUser) ? 'sm:grid-cols-2' : ''} gap-3`}>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Дата начала</label>
                                    <input
                                        type="date"
                                        value={editedUser?.status_period_start_date || ""}
                                        onChange={(e) => setEditedUser({
                                            ...editedUser,
                                            status_period_start_date: e.target.value,
                                            use_schedule_status_period: true,
                                            status_period_end_date: (!isDismissalLikeStatus(editedUser?.status) && (!editedUser?.status_period_end_date || editedUser.status_period_end_date < e.target.value))
                                                ? e.target.value
                                                : editedUser?.status_period_end_date
                                        })}
                                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                        disabled={isLoading}
                                    />
                                </div>
                                {shouldShowStatusPeriodEndDate(editedUser) && (
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">
                                        {isDismissalLikeStatus(editedUser?.status) ? 'Дата окончания (необ.)' : 'Дата окончания'}
                                    </label>
                                    <input
                                        type="date"
                                        value={editedUser?.status_period_end_date || ""}
                                        min={editedUser?.status_period_start_date || ""}
                                        onChange={(e) => setEditedUser({ ...editedUser, status_period_end_date: e.target.value, use_schedule_status_period: true })}
                                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                        disabled={isLoading}
                                    />
                                </div>
                                )}
                            </div>
                            {isDismissalLikeStatus(editedUser?.status) && (
                                <>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Причина увольнения</label>
                                    <select
                                        value={editedUser?.status_period_dismissal_reason || ""}
                                    onChange={(e) => {
                                            const nextReason = e.target.value;
                                            setEditedUser((prev) => ({
                                                ...prev,
                                                status_period_dismissal_reason: nextReason,
                                                use_schedule_status_period: true,
                                                status_period_end_date: (!prev?.status_period_is_blacklist && nextReason === DISMISSAL_REASON_WITH_END_DATE)
                                                    ? (prev?.status_period_end_date || '')
                                                    : ''
                                            }));
                                        }}
                                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                        disabled={isLoading}
                                    >
                                        <option value="">Выберите причину</option>
                                        {DISMISSAL_REASON_OPTIONS.map(reason => (
                                            <option key={reason} value={reason}>{reason}</option>
                                        ))}
                                    </select>
                                </div>
                                <div>
                                    <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={!!editedUser?.status_period_is_blacklist}
                                            onChange={(e) => setEditedUser((prev) => ({
                                                ...prev,
                                                status_period_is_blacklist: e.target.checked,
                                                use_schedule_status_period: true,
                                                status_period_end_date: e.target.checked ? '' : prev?.status_period_end_date
                                            }))}
                                            className="rounded border-gray-300"
                                            disabled={isLoading}
                                        />
                                        <span>ЧС (без возможности восстановления)</span>
                                    </label>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Комментарий (обязательно)</label>
                                    <textarea
                                        value={editedUser?.status_period_comment || ""}
                                        onChange={(e) => setEditedUser({ ...editedUser, status_period_comment: e.target.value, use_schedule_status_period: true })}
                                        rows={3}
                                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                        disabled={isLoading}
                                    />
                                </div>
                                <div className="text-xs text-slate-500">
                                    Для увольнения обязательны дата начала, причина и комментарий.
                                </div>
                                </>
                            )}
                        </div>
                        )}

                        {userToEdit?.role !== "sv" && (
                            <>
                            {canShowOperatorRateControls && (
                                <div className="grid grid-cols-1 gap-4">
                                {isAdminLikeRequester && isOperatorDraft(editedUser) && (
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Супервайзер</label>
                                    <select
                                    value={editedUser?.supervisor_id || ""}
                                    onChange={(e) => setEditedUser({ ...editedUser, supervisor_id: e.target.value })}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                    disabled={isLoading}
                                    >
                                    <option value="">Выберите супервайзера</option>
                                    {(() => {
                                        const all = svList || [];
                                        const active = all.filter(sv => sv.status === 'working' || sv.status === 'unpaid_leave' || !sv.status);
                                        return (
                                            <>
                                                {active.map(sv => (
                                                <option key={sv.id} value={sv.id}>{sv.name}</option>
                                                ))}
                                            </>
                                        );
                                    })()}
                                    </select>
                                </div>
                                )}

                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Ставка</label>
                                    <select
                                    value={editedUser?.rate || 1.0}
                                    onChange={(e) => setEditedUser({ ...editedUser, rate: parseFloat(e.target.value) })}
                                    className={`w-full px-3 py-2 border rounded-lg shadow-sm focus:outline-none transition-all ${
                                        isSupervisorRateLocked
                                            ? 'border-slate-200 bg-slate-100 text-slate-400 cursor-not-allowed dark:border-slate-700 dark:bg-slate-800/70 dark:text-slate-400'
                                            : 'border-gray-300 focus:ring-2 focus:ring-blue-500 bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100'
                                    }`}
                                    disabled={isLoading || isSupervisorRateLocked}
                                    >
                                    <option value={1.0}>1.00</option>
                                    <option value={0.75}>0.75</option>
                                    <option value={0.5}>0.50</option>
                                    </select>
                                </div>
                                {isSupervisorRateLocked && (
                                    <div className="text-xs text-slate-500">
                                        Для супервайзеров изменение ставки доступно только 1-го числа месяца.
                                    </div>
                                )}
                                </div>
                            )}

                            {isOperatorDraft(editedUser) && (
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Направление</label>
                                <select
                                value={editedUser?.direction_id || ""}
                                onChange={(e) => setEditedUser({ ...editedUser, direction_id: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading}
                                >
                                <option value="">Выберите направление</option>
                                {directions.map((dir) => (
                                    <option key={dir.id} value={dir.id}>
                                    {dir.name}
                                    </option>
                                ))}
                                </select>
                            </div>
                            )}

                            {isOperatorDraft(editedUser) && (
                            <>
                            <div>
                                <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={!!editedUser?.has_proxy}
                                    onChange={(e) => setEditedUser({ ...editedUser, has_proxy: e.target.checked })}
                                    className="rounded border-gray-300"
                                    disabled={isLoading}
                                />
                                <span>Наличие прокси</span>
                                </label>
                            </div>
                            <div>
                                <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={!!editedUser?.has_driver_license}
                                    onChange={(e) => setEditedUser({ ...editedUser, has_driver_license: e.target.checked })}
                                    className="rounded border-gray-300"
                                    disabled={isLoading}
                                />
                                <span>Наличие водительских прав</span>
                                </label>
                            </div>
                            {editedUser?.has_proxy && (
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Номер прокси карты</label>
                                <input
                                type="text"
                                value={editedUser?.proxy_card_number || ""}
                                onChange={(e) => setEditedUser({ ...editedUser, proxy_card_number: e.target.value })}
                                placeholder="Можно указать не полностью"
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading}
                                />
                            </div>
                            )}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">SIP номер</label>
                                <input
                                type="text"
                                value={editedUser?.sip_number || ""}
                                onChange={(e) => setEditedUser({ ...editedUser, sip_number: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading}
                                />
                            </div>
                            </>
                            )}
                            </>
                        )}
                        </>
                    )}

                    {activeTab === "account" && (
                        <>
                        <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs text-gray-600">
                            Оставьте поле пустым, если менять логин или пароль не нужно.
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Новый логин</label>
                            <input
                            type="text"
                            value={editedUser?.new_login || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, new_login: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Новый пароль</label>
                            <input
                            type="password"
                            value={editedUser?.new_password || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, new_password: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            />
                        </div>
                        </>
                    )}
                    </>
                )}

                {/* Если создали — показываем креды */}
                {createdCredentials && (
                    <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
                    <div className="font-medium text-sm text-green-800 mb-2">Данные аккаунта</div>
                    <div className="text-sm text-gray-800">Логин: <span className="font-semibold">{createdCredentials.login}</span></div>
                    <div className="text-sm text-gray-800">Пароль: <span className="font-semibold">{createdCredentials.password}</span></div>

                    <div className="mt-3 flex gap-2">
                        <button
                        onClick={() => {
                            copyToClipboard(`Логин: ${createdCredentials.login}\nПароль: ${createdCredentials.password}`);
                        }}
                        className="px-3 py-1 bg-gray-200 rounded-md hover:bg-gray-300 text-sm"
                        >
                        Копировать
                        </button>

                        <button
                        onClick={() => {
                            // Сброс формы для создания ещё одного
                            resetForCreate();
                        }}
                        className="px-3 py-1 bg-blue-600 text-white rounded-md hover:bg-blue-700 text-sm"
                        >
                        Создать ещё
                        </button>

                        <button
                        onClick={() => {
                            setCreatedCredentials(null);
                            closeAvatarCropEditor();
                            onClose();
                        }}
                        className="px-3 py-1 bg-gray-100 rounded-md hover:bg-gray-200 text-sm"
                        >
                        Закрыть
                        </button>
                    </div>
                    </div>
                )}

                {/* Error message */}
                <div aria-live="polite" className="min-h-[1.25rem]">
                    {modalError && <p className="text-sm text-red-600 dark:text-red-400">{modalError}</p>}
                </div>

                {/* Actions */}
                {!createdCredentials && (
                    <div className="flex justify-end items-center gap-3 pt-2">
                    <button
                        onClick={() => {
                        setModalError("");
                        setCreatedCredentials(null);
                        closeAvatarCropEditor();
                        onClose();
                        }}
                        className="px-4 py-2 bg-gray-200 text-gray-800 rounded-lg hover:bg-gray-300 transition-all duration-200 font-medium"
                        disabled={isLoading}
                    >
                        Отмена
                    </button>

                    <button
                        onClick={handleSave}
                        disabled={isLoading || isAvatarProcessing}
                        className={`px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-all duration-200 font-medium flex items-center gap-2 ${
                        (isLoading || isAvatarProcessing) ? "opacity-60 cursor-not-allowed" : ""
                        }`}
                    >
                        {(isLoading || isAvatarProcessing) ? (
                        <>
                            <FaIcon className="fas fa-spinner fa-spin" /> Сохранение...
                        </>
                        ) : isCreateMode ? (
                        "Создать"
                        ) : (
                        "Сохранить"
                        )}
                    </button>
                    </div>
                )}

                {createdCredentials && (
                    <p className="mt-2 text-xs text-gray-400">
                    Учетные данные показаны выше — обязательно сохраните их, они видны только сейчас.
                    </p>
                )}

                {!createdCredentials && <p className="mt-2 text-xs text-gray-400">Нажмите Esc, кликните вне модалки или крестик вверху, чтобы закрыть.</p>}
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

export default UserEditModal;
