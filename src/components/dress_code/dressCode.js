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

/* Пропорции снимка (ширина / высота) — по месту на экране между строкой подсказки и
   кнопками: кадр занимает его целиком, без чёрных полос сверху и снизу (владелец 13.11.2026:
   «очень большие поля снизу и сверху»). Не шире 3:4 (весь сенсор камеры в портрете) и не
   уже 9:16 (высокий телефон: от сенсора отрезаются только бока). */
export const PHOTO_ASPECT = Object.freeze({ min: 9 / 16, max: 3 / 4 });

export const photoAspect = (stageWidth, stageHeight) => {
    if (!(stageWidth > 0 && stageHeight > 0)) return PHOTO_ASPECT.max;
    return Math.min(PHOTO_ASPECT.max, Math.max(PHOTO_ASPECT.min, stageWidth / stageHeight));
};

/* Что из кадра камеры попадает на фото: [sx, sy, sw, sh] в пикселях кадра.
   На экране показывается РОВНО эта часть и ничего больше — что видно, то и снято (владелец:
   «не совсем понятно, снимается ли всё»). ВЫСОТА КАДРА НЕ РЕЖЕТСЯ НИКОГДА: камера на
   вытянутой руке видит человека едва до пояса, и любая обрезка сверху или снизу делает «фото
   по пояс» недостижимым — из-за неё автоснимок когда-то и пришлось разрешить по грудь. Если
   кадр шире, чем нужно (`aspect` — из photoAspect), отрезаются бока: по сторонам от человека
   на фото делать нечего. Кадр уже нужного берётся весь. */
export const photoRegion = (videoWidth, videoHeight, aspect = PHOTO_ASPECT.max) => {
    if (!(videoWidth > 0 && videoHeight > 0)) return [0, 0, 0, 0];
    if (videoWidth <= videoHeight * aspect) return [0, 0, videoWidth, videoHeight];
    const width = Math.round(videoHeight * aspect);
    return [Math.round((videoWidth - width) / 2), 0, width, videoHeight];
};

/* Где на экране стоит снимаемая часть: вписана целиком, без обрезки, в место между верхней
   строкой (закрыть, подсказка) и кнопками снизу. → { width, height } в пикселях экрана. */
export const fitStage = (regionWidth, regionHeight, stageWidth, stageHeight) => {
    if (!(regionWidth > 0 && regionHeight > 0 && stageWidth > 0 && stageHeight > 0)) return { width: 0, height: 0 };
    const scale = Math.min(stageWidth / regionWidth, stageHeight / regionHeight);
    return { width: Math.floor(regionWidth * scale), height: Math.floor(regionHeight * scale) };
};

/* Высота строк камеры на экране, px: сверху — закрыть и подсказка, снизу — кнопка снимка.
   Всё остальное — кадр (владелец: «очень большие поля снизу и сверху»). */
export const CAMERA_BARS = Object.freeze({ top: 56, bottom: 108 });

/* Доля → проценты для стенда: 0.234 → «23 %», −0.031 → «−3 %» (минус, а не дефис). */
export const pct = (value) => {
    const rounded = Math.round((Number(value) || 0) * 100);
    return `${rounded < 0 ? '−' : ''}${Math.abs(rounded)} %`;
};
