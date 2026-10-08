export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const MAX_UPLOAD_IMAGE_BYTES = 5 * 1024 * 1024;
export const UPLOAD_ACCEPT = '.pdf,.jpg,.jpeg,.png,.webp,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip,.mp4,.3gp,.mp3,.m4a,.aac,.ogg,.amr';
const EXTENSIONS = new Set(UPLOAD_ACCEPT.split(','));
const IMAGES = new Set(['.jpg', '.jpeg', '.png', '.webp']);

export function uploadFileError(file) {
    if (!file?.name || !Number.isFinite(file.size) || file.size <= 0) return 'Выберите непустой файл.';
    const extension = '.' + file.name.split('.').pop().toLowerCase();
    if (!EXTENSIONS.has(extension)) return 'Этот формат не поддерживается. Выберите документ, изображение, аудио или видео.';
    if (file.size > (IMAGES.has(extension) ? MAX_UPLOAD_IMAGE_BYTES : MAX_UPLOAD_BYTES)) {
        return IMAGES.has(extension) ? 'Изображение должно быть не больше 5 МБ.' : 'Файл должен быть не больше 10 МБ.';
    }
    return '';
}

export function uploadSizeLabel(size) {
    if (!Number.isFinite(size) || size <= 0) return '';
    if (size < 1024 * 1024) return `${Math.max(1, Math.ceil(size / 1024))} КБ`;
    return `${(size / (1024 * 1024)).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} МБ`;
}

export function uploadedAttachment(value) {
    if (!value || typeof value.id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.id)
        || typeof value.name !== 'string' || !value.name || !Number.isFinite(value.size) || value.size <= 0) return null;
    return { id: value.id, name: value.name, size: value.size,
        mime: typeof value.mime === 'string' ? value.mime : '',
        expiresAt: typeof value.expiresAt === 'string' && Number.isFinite(Date.parse(value.expiresAt)) ? value.expiresAt : null };
}
