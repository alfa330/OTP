/**
 * «Офлайн» за простой в «Исходе» у отдела продаж Тез КЦ — настройка в карточке отдела «Настройки SIP».
 *
 * Владелец 17.11.2026: «добавь возможность эту настройку менять» — и «выключить сейчас». Правило
 * получает только отдел продаж Тез КЦ (техподдержке — свой возврат в «Активный»), поэтому блок есть
 * только в карточке Тез КЦ. Сервер: sip_department_config.idle_offline_enabled / idle_offline_sec
 * (не задано — выключено и 3 минуты); границы срока — SIP_IDLE_OFFLINE_SEC_MIN / _MAX в db/core.py,
 * сверяет тест. Читает правило телефон 3.22.57+ при обновлении настроек — раз в 10 минут.
 */

// Код отдела Тез КЦ (TEZ_WALLBOARD_DEPARTMENT_CODE на сервере). По коду, а не по id: id засеян.
export const IDLE_OFFLINE_DEPARTMENT_CODE = 'tez';
export const IDLE_OFFLINE_MIN_DEFAULT = 3;
export const IDLE_OFFLINE_MIN_MIN = 1;
export const IDLE_OFFLINE_MIN_MAX = 60;
// Слово «не задано» — то же, что SIP_INHERIT_TOKEN у сервера.
const INHERIT = 'inherit';

/** Есть ли у отдела эта настройка: только Тез КЦ. */
export const hasIdleOfflineSetting = (dept) => (
    String(dept?.department_code || '').trim().toLowerCase() === IDLE_OFFLINE_DEPARTMENT_CODE
);

/** Срок в минутах из строки отдела: свой или по умолчанию. */
export const idleOfflineMinutes = (dept) => (
    typeof dept?.idle_offline_sec === 'number' && dept.idle_offline_sec > 0
        ? Math.round(dept.idle_offline_sec / 60)
        : IDLE_OFFLINE_MIN_DEFAULT
);

/** Поля формы из строки отдела. Пустые минуты — «по умолчанию» (серая 3 в поле). */
export const idleOfflineFormFrom = (dept) => ({
    idle_offline_enabled: dept?.idle_offline_enabled === true,
    idle_offline_min: typeof dept?.idle_offline_sec === 'number' && dept.idle_offline_sec > 0
        ? String(Math.round(dept.idle_offline_sec / 60))
        : '',
});

/**
 * Поля для PUT карточки отдела. Пустые минуты — срок по умолчанию (inherit → NULL у сервера).
 * Не целое число и выход за 1–60 — ошибка с текстом для тоста: молча подставлять другой срок нельзя,
 * он решает, кого и когда выбросит из работы.
 */
export const idleOfflinePayload = (form) => {
    const raw = String(form?.idle_offline_min ?? '').trim();
    let seconds = INHERIT;
    if (raw !== '') {
        if (!/^\d+$/.test(raw)) {
            throw new Error(`Срок простоя — целое число минут, от ${IDLE_OFFLINE_MIN_MIN} до ${IDLE_OFFLINE_MIN_MAX}`);
        }
        const minutes = Number(raw);
        if (minutes < IDLE_OFFLINE_MIN_MIN || minutes > IDLE_OFFLINE_MIN_MAX) {
            throw new Error(`Срок простоя — от ${IDLE_OFFLINE_MIN_MIN} до ${IDLE_OFFLINE_MIN_MAX} минут`);
        }
        seconds = minutes * 60;
    }
    return { idle_offline_enabled: Boolean(form?.idle_offline_enabled), idle_offline_sec: seconds };
};

/** «Вернуть общие» снимает и это правило: не задано — выключено, срок по умолчанию. */
export const IDLE_OFFLINE_RESET = Object.freeze({ idle_offline_enabled: INHERIT, idle_offline_sec: INHERIT });

/**
 * Подпись в списке отделов — только когда правило включено: выключенное — умолчание, и слово в
 * строке было бы шумом (так же, как незаданный автоприём).
 */
export const idleOfflineLabel = (dept) => (
    hasIdleOfflineSetting(dept) && dept?.idle_offline_enabled === true
        ? `«Офлайн» за простой в «Исходе»: ${idleOfflineMinutes(dept)} мин`
        : null
);

/** Подпись у выключателя: что будет действовать после сохранения — словами, а не серой цифрой. */
export const idleOfflineHint = (form) => {
    if (!form?.idle_offline_enabled) return 'выключено: «Исход» без звонков не ограничен';
    const raw = String(form?.idle_offline_min ?? '').trim();
    if (raw === '') return `«Офлайн» через ${IDLE_OFFLINE_MIN_DEFAULT} мин без звонка (по умолчанию)`;
    const minutes = /^\d+$/.test(raw) ? Number(raw) : NaN;
    if (!(minutes >= IDLE_OFFLINE_MIN_MIN && minutes <= IDLE_OFFLINE_MIN_MAX)) {
        return `срок — от ${IDLE_OFFLINE_MIN_MIN} до ${IDLE_OFFLINE_MIN_MAX} минут`;
    }
    return `«Офлайн» через ${minutes} мин без звонка`;
};
