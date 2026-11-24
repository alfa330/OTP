/* Доля повторов в пачке «Удаленного КЦ» (владелец, 24.11.2026: «таких повторов до окончания общего списка в
 * выдаваемых пачках должно быть не больше 20%… чтобы операторы не получали пачками повторы»). Правило — на
 * сервере (dial_list/service.py: repeat_quota, compose_portion); здесь — только поле настроек. */

export const REPEAT_SHARE_MAX = 100;
export const REPEAT_SHARE_DEFAULT = 20;

/** Значение поля → число для сервера: пусто или не число — прежнее значение (пустое поле не должно молча
 *  превратиться в 0 — «ни одного повтора»), иначе целое в пределах 0–100. */
export const repeatShareValue = (input, fallback = REPEAT_SHARE_DEFAULT) => {
    const text = String(input ?? '').trim();
    const n = Number(text);
    if (text === '' || !Number.isFinite(n)) return fallback;
    return Math.min(REPEAT_SHARE_MAX, Math.max(0, Math.round(n)));
};

/** Под полем — что значит число для пачки этого отдела: «до 20 из 100 в пачке», «без ограничения», «без повторов». */
export const repeatShareHint = (pct, portionSize) => {
    const share = repeatShareValue(pct, REPEAT_SHARE_MAX);
    if (share >= REPEAT_SHARE_MAX) return 'без ограничения';
    const size = Math.max(1, Math.round(Number(portionSize) || 0));
    const most = Math.floor((size * share) / 100);
    return most > 0 ? `до ${most} из ${size} в пачке` : 'без повторов, пока есть другие водители';
};
