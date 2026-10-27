/*
 * «Переведённые» в «Учете часов»: кто ушёл из выбранной группы до конца месяца.
 *
 * Раньше такой оператор стоял среди «Активных» с жёлтым значком ⇄: состав
 * месяца группы — все, чьё членство пересекло месяц хоть на день, чтобы
 * отработанные в группе дни не терялись. Владелец (27.10.2026): переведённых —
 * отдельной вкладкой рядом с «Активными» и «Уволенными».
 *
 * Откуда данные. В режиме группы сервер (get_daily_hours_by_supervisor_month с
 * group_id) кладёт в строку group_segments — членства оператора внутри месяца,
 * уже обрезанные по его границам, — и помечает is_current те, что относятся к
 * ВЫБРАННОЙ группе. Это не «открытое членство», как в ответе «Моих часов»: там
 * поле значит другое. Без группы сегментов нет вовсе — список строится по
 * нынешнему СВ или отделу, и ушедших в нём просто нет.
 *
 * Правило — то же, что у вкладки «Переведённые» журнала оценок (#347): на
 * последний день месяца человека в группе нет. Пришедший ИЗ другой группы
 * остаётся в «Активных»: теперь он член этой группы.
 */

const monthLastDay = (month) => {
    const match = /^(\d{4})-(\d{2})$/.exec(String(month || '').trim());
    if (!match) return null;
    const mon = Number(match[2]);
    if (mon < 1 || mon > 12) return null;
    return new Date(Number(match[1]), mon, 0).getDate();
};

/* Членство «конец раньше начала» — перевод, отменённый в момент заведения, —
   не покрывает ни одного дня, и судить по нему об уходе нельзя. */
const monthSegments = (op) => (Array.isArray(op?.group_segments) ? op.group_segments : [])
    .map((seg) => ({
        inViewedGroup: seg?.is_current === true,
        startDay: Number(seg?.start_day),
        endDay: Number(seg?.end_day),
    }))
    .filter((seg) => Number.isFinite(seg.startDay)
        && Number.isFinite(seg.endDay)
        && seg.endDay >= seg.startDay);

/**
 * Ушёл ли оператор из выбранной группы до конца месяца.
 *   null — не уходил, или о группах ничего не известно;
 *   { day, movedToGroup } — day: первый день вне группы; movedToGroup — после
 *       ухода он числится в другой группе (false — из группы просто вывели).
 */
export function getHoursGroupLeave(op, month) {
    const lastDay = monthLastDay(month);
    if (!lastDay) return null;
    const segments = monthSegments(op);
    const here = segments.filter((seg) => seg.inViewedGroup);
    if (!here.length) return null;
    const lastDayHere = Math.max(...here.map((seg) => seg.endDay));
    if (lastDayHere >= lastDay) return null;
    return {
        day: lastDayHere + 1,
        movedToGroup: segments.some((seg) => !seg.inViewedGroup && seg.endDay > lastDayHere),
    };
}
