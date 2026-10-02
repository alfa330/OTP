/*
 * «Табло ОП»: принятие лида в работу (владелец, 02.10.2026). Отдельным модулем без импортов, как
 * opWallboardGroups.js, чтобы node-тест (tests/op_wallboard_leads.test.mjs) загружал его напрямую:
 * opWallboardShared тянет соседей путями без расширения, их понимает только сборщик.
 */

/*
 * Сделки для показателя приходят из amoCRM инкрементом «Воронки ОП» раз в 3 минуты, а не мостом:
 * мост может быть жив, а список сделок — замереть, и плитка тихо считала бы по старым. Пять пропусков
 * подряд — повод сказать об этом (один-два бывают: amoCRM рвёт соединение, массовая правка
 * растягивает прогон). Свежий список — ни слова: шум.
 */
export const OP_LEAD_SYNC_WARN_SECONDS = 15 * 60;

/** Сколько секунд список сделок не обновлялся, если это уже повод предупредить; иначе null. */
export const opLeadSyncStaleSeconds = (snapshot) => {
    const age = snapshot?.lead_speed?.synced_age_seconds;
    if (age === null || age === undefined || !Number.isFinite(Number(age))) return null;
    return Number(age) > OP_LEAD_SYNC_WARN_SECONDS ? Number(age) : null;
};
