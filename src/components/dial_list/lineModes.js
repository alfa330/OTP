/*
 * Режим линий (владелец 26.11.2026): «в удалённом КЦ уже есть линии — надо добавить возможность просто
 * переключать для них режимы… не меняя сотрудника: линии общие, а что отображается в iCORE Phone, зависит от
 * включённого режима». Чистая логика вкладки «Линии», без React; на неё — tests/dial_list_line_modes.test.mjs.
 *
 * Сервер (GET …/lines) присылает line_modes: {host: {id, name}, container: {id, name}, hosted, modes} и у каждой
 * занятой линии mode: 'activation' | 'host'. Переключение — POST …/lines/mode {mode, user_ids | all: true}.
 */

export const MODE_HOST = 'host';
export const MODE_ACTIVATION = 'activation';

/** Варианты переключателя: обзвон своей линии (имя отдела линий) и «Активация». */
export const lineModeOptions = (lineModes) => [
    { value: MODE_HOST, label: lineModes?.host?.name || 'Удаленный КЦ' },
    { value: MODE_ACTIVATION, label: 'Активация' },
];

/** Сколько занятых линий и сколько из них в «Активации». */
export const modeCounts = (lines = []) => {
    const taken = (Array.isArray(lines) ? lines : []).filter((l) => l && l.icore_user);
    return { taken: taken.length, activation: taken.filter((l) => l.mode === MODE_ACTIVATION).length };
};

/** Тост по ответу переключения: сколько переключено, кого пропустили и почему, сколько строк вернули. */
export const modeResultToast = (result, lineModes) => {
    const mode = result?.mode === MODE_ACTIVATION ? 'Активация водителей' : (lineModes?.host?.name || 'Удаленный КЦ');
    const changed = Number(result?.changed) || 0;
    const skipped = Array.isArray(result?.skipped) ? result.skipped : [];
    const released = Number(result?.released_rows) || 0;
    const parts = [];
    if (changed) parts.push(`${changed === 1 ? 'Линия переведена' : `Линий переведено: ${changed}`} в режим «${mode}»`);
    else if (!skipped.length) parts.push(`Уже в режиме «${mode}»`);
    if (released) parts.push(`водителей вернули в общий список: ${released}`);
    if (skipped.length) {
        const names = skipped.slice(0, 3).map((s) => s.name || `#${s.user_id}`).join(', ');
        parts.push(`не переключены (идёт звонок или не поставлен итог): ${names}${skipped.length > 3 ? '…' : ''}`);
    }
    const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : t);
    return { text: parts.map(cap).join('. '), kind: skipped.length && !changed ? 'error' : 'success' };
};
