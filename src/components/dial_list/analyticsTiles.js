/* Строки под числом у плиток «Аналитики» удалённого КЦ (владелец, 24.11.2026):
 *  — «Обзвонили»: «на обзвонили ещё доступно для обзвона и указать тех, по которым ещё не производился звонок и ещё не
 *    подписали, т.е. тех, кого ещё можно обзвонить, потом ждут повтора»;
 *  — «Успешки»: «добавить подписали сами и разделить его на до загрузки базы и после загрузки базы».
 * Числа считает сервер (dial_list/analytics.py: open_new, open_retry, self_signed, signed_before_upload); здесь — только
 * подписи. fmt — форматирование числа экрана («11 452»). */

const n = (v) => Math.max(0, Number(v) || 0);

/** «Обзвонили», вторая и третья строки: сколько обзвон ещё позвонит в этом месяце. Только обзваниваемый месяц
 *  (прошлую базу телефон не выдаёт) и только если сервер прислал числа (старый сервер — ничего). */
export const availableLines = (totals, fmt, active = true) => {
    if (!active || !totals || (totals.open_new == null && totals.open_retry == null)) return [];
    const fresh = n(totals.open_new);
    const retry = n(totals.open_retry);
    return [`ещё доступно — ${fmt(fresh + retry)}`, `не звонили ${fmt(fresh)} · ждут повтора ${fmt(retry)}`];
};

/** «Успешки», строки под «подписали всего»: подписали сами (без нашего разговора от 10 с до подписи) и ниже — до
 *  загрузки базы и после. Сами — 0: строк нет. */
export const selfSignedLines = (totals, fmt) => {
    const self = n(totals?.self_signed);
    if (!self) return [];
    const before = Math.min(self, n(totals?.signed_before_upload));
    return [`сами — ${fmt(self)}`, `до загрузки базы ${fmt(before)} · после ${fmt(self - before)}`];
};
