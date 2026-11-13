/* «Дресс-код» (#418): подписи, тона и мелкие расчёты, общие для экрана сотрудника
 * и вкладки проверяющего в «Отметках». Без React — проверяется node-тестом
 * (tests/dress_code.test.mjs). Подписи — зеркало dress_code/__init__.py. */

export const AI_LABELS = Object.freeze({
    pending: 'Ожидает анализа',
    ok: 'Соответствует',
    violation: 'Предположительно не соответствует',
    unclear: 'Не удалось определить',
});

export const DECISION_LABELS = Object.freeze({
    confirmed: 'Подтверждено нарушение',
    no_violation: 'Нарушения нет',
    retake: 'Требуется повторное фото',
});

export const QUALITY_LABELS = Object.freeze({
    ok: 'Годный',
    no_person: 'Нет человека',
    several_people: 'Несколько человек',
    head_cropped: 'Обрезана голова',
    body_cropped: 'Не видно одежду',
    too_dark: 'Темно',
    blurry: 'Размыто',
    clothes_hidden: 'Одежда закрыта',
});

/* Цвет — только со смыслом: нарушение красное, «нет нарушения» и «соответствует»
   зелёные, спорное — жёлтое, ожидание — без цвета. */
export const aiTone = (status) => ({ ok: 'green', violation: 'red', unclear: 'amber' }[status] || 'slate');
export const decisionTone = (decision) => ({ confirmed: 'red', no_violation: 'green', retake: 'amber' }[decision] || 'slate');

/* Что видит сотрудник. До решения руководителя — нейтрально (ТЗ п. 2): спорный
   вывод ИИ сотруднику не показывается, пока его не подтвердили. */
export const EMPLOYEE_STATUS = Object.freeze({
    ok: { label: 'Соответствует', tone: 'green' },
    review: { label: 'Фото принято, его проверит руководитель', tone: 'slate' },
    confirmed: { label: 'Нарушение подтверждено', tone: 'red' },
    no_violation: { label: 'Нарушения нет', tone: 'green' },
    retake: { label: 'Руководитель просит переснять', tone: 'amber' },
});

export const employeeStatus = (status) => EMPLOYEE_STATUS[status] || EMPLOYEE_STATUS.review;

/* Фильтры вкладки проверяющего: по умолчанию — очередь «ждут решения». */
export const REVIEW_FILTERS = Object.freeze([
    { value: 'waiting', label: 'Ждут решения' },
    { value: 'all', label: 'Все фото' },
    { value: 'confirmed', label: 'Нарушения' },
    { value: 'missing', label: 'Без фото' },
]);

export const filterParams = (filter) => {
    if (filter === 'waiting') return { decision: 'waiting' };
    if (filter === 'confirmed') return { decision: 'confirmed' };
    return {};
};

const pad = (value) => String(value).padStart(2, '0');

/* Время сервера — по Алматы и без пояса («2026-11-13T09:03:12»): берём часы и минуты
   из строки, а не через Date, иначе браузер в другом поясе сдвинул бы время. */
export const fmtTime = (iso) => {
    const match = /T(\d{2}):(\d{2})/.exec(String(iso || ''));
    return match ? `${match[1]}:${match[2]}` : '';
};

export const fmtDay = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return match ? `${match[3]}.${match[2]}` : '';
};

export const fmtDayFull = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return match ? `${match[3]}.${match[2]}.${match[1]}` : '';
};

export const todayIso = (now = new Date()) => `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;

/* «N из M» — доля всегда с явным знаменателем (ТЗ п. 7). */
export const ofTotal = (part, whole) => (whole ? `${part} из ${whole}` : String(part));

/* Средняя яркость кадра 0..255 по RGBA (кадр заранее уменьшен). Сильно тёмный кадр
   отбивается сразу в камере, не дожидаясь ИИ: «Слишком темно» понятно и так. */
export const TOO_DARK_LUMA = 38;
export const meanLuma = (rgba) => {
    if (!rgba || !rgba.length) return 0;
    let sum = 0;
    let count = 0;
    for (let i = 0; i + 2 < rgba.length; i += 4) {
        sum += 0.2126 * rgba[i] + 0.7152 * rgba[i + 1] + 0.0722 * rgba[i + 2];
        count += 1;
    }
    return count ? sum / count : 0;
};

/* Рамка видоискателя (ТЗ п. 10): почти на весь экран, по центру. Верх — у макушки, низ —
   у пояса. На фото попадает РОВНО то, что в рамке (владелец 13.11.2026: «не совсем понятно,
   снимается ли всё») — поэтому всё вне рамки затемнено, а после снимка в ней же стоит фото.
   top/bottom — отступы от краёв экрана в пикселях (сверху — кнопка закрытия и подсказка,
   снизу — кнопка снимка), ширина — не больше maxWidth и не шире maxAspect от высоты:
   на компьютере и при повороте телефона рамка остаётся портретной. */
export const FRAME = Object.freeze({ top: 64, bottom: 118, sideShare: 0.92, maxWidth: 520, maxAspect: 0.8 });

/* Какая часть кадра камеры попадает на фото: [sx, sy, sw, sh] в пикселях кадра.
   Видео растянуто на экран как object-cover (обрезано по краям), рамка — прямоугольник
   поверх него: `box` и `rect` — их положения на экране. `mirrored` — превью зеркальное
   (фронтальная камера): по горизонтали кадр отсчитывается с другой стороны. */
export const frameRegion = (videoWidth, videoHeight, box, rect, mirrored = false) => {
    if (!videoWidth || !videoHeight || !box?.width || !box?.height || !rect?.width || !rect?.height) {
        return [0, 0, videoWidth || 0, videoHeight || 0];
    }
    const scale = Math.max(box.width / videoWidth, box.height / videoHeight);
    const offsetX = (videoWidth - box.width / scale) / 2;
    const offsetY = (videoHeight - box.height / scale) / 2;
    const left = mirrored ? (box.left + box.width) - (rect.left + rect.width) : rect.left - box.left;
    let sx = offsetX + left / scale;
    let sy = offsetY + (rect.top - box.top) / scale;
    let sw = rect.width / scale;
    let sh = rect.height / scale;
    sx = Math.min(Math.max(0, sx), videoWidth - 1);
    sy = Math.min(Math.max(0, sy), videoHeight - 1);
    sw = Math.max(1, Math.min(sw, videoWidth - sx));
    sh = Math.max(1, Math.min(sh, videoHeight - sy));
    return [sx, sy, sw, sh];
};
