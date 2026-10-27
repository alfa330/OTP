/*
 * Окно «Сформировать отчёт» в «Учете часов»: какие отчёты есть и кто в каждом.
 *
 * Раньше вид отчёта выбирали переключателем «По СВ / Общий» в ряду отбора, а
 * кнопка сразу качала файл. Переключатель стоял между группой и вкладками
 * «Активные / Уволенные» и читался как фильтр таблицы, хотя менял только файл,
 * а «Общий» ещё и гасил выбор группы. Владелец (27.10.2026): по нажатию на
 * отчёт выбирать в окне, какой отчёт, и описание видно сразу.
 *
 * Описания говорят, кто попадёт в файл при ТЕКУЩЕМ выборе на странице, — так
 * же решает сервер (/api/report/monthly_hours):
 *   «По СВ» — выбрана группа: её операторы за месяц, дни в другой группе
 *     помечены «др.» и в итоги не входят; группы нет — операторы выбранного СВ;
 *   «Общий» — все операторы отдела (у глобального админа и у человека без
 *     отдела — компании) с колонкой «Супервайзер»; главе отдела с часами СВ по
 *     Clockster и глобальному админу сервер добавляет строки таких СВ.
 */

const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];

/* «Отчёт за октябрь 2026»: у названий месяцев винительный падеж совпадает
   с именительным, «за октябрь», «за май». */
export function hoursReportTitle(month) {
    const match = /^(\d{4})-(\d{2})$/.exec(String(month || '').trim());
    const name = match ? MONTHS[Number(match[2]) - 1] : null;
    return name ? `Отчёт за ${name} ${match[1]}` : 'Отчёт';
}

/**
 * Варианты отчёта для окна.
 *   group — выбранная группа { name } или null. Имени может не быть: архивной
 *       группы и группы чужого отдела нет в списке групп, а открыть их можно
 *       щелчком по чужому дню переведённого — отчёт по ним сервер всё равно даёт;
 *   supervisor — СВ, по которому строится «По СВ» без группы: { name, isMe }
 *       или null (не выбран — тогда «По СВ» недоступен);
 *   companyWide — общий отчёт по всей компании (глобальный админ или человек без
 *       отдела), иначе по отделу;
 *   withSupervisorRows — в общий отчёт сервер добавит строки СВ.
 */
export function buildHoursReportOptions({
    group = null,
    supervisor = null,
    companyWide = false,
    withSupervisorRows = false,
} = {}) {
    const groupName = String(group?.name || '').trim();
    const supervisorName = String(supervisor?.name || '').trim();
    let bySv;
    if (group) {
        const who = groupName ? `Операторы группы «${groupName}».` : 'Операторы выбранной группы.';
        bySv = {
            description: `${who} Дни, когда оператор был в другой группе, помечены «др.» и в итоги не входят.`,
            disabled: false,
        };
    } else if (supervisor) {
        bySv = {
            description: supervisor.isMe
                ? 'Ваши операторы.'
                : (supervisorName ? `Операторы супервайзера ${supervisorName}.` : 'Операторы выбранного супервайзера.'),
            disabled: false,
        };
    } else {
        bySv = {
            description: 'Операторы одной группы. Сначала выберите группу на странице.',
            disabled: true,
        };
    }
    const everyone = companyWide ? 'Все операторы компании' : 'Все операторы отдела';
    const allDescription = `${everyone} с колонкой «Супервайзер».`
        + (withSupervisorRows ? ' Супервайзеры с часами по Clockster — отдельными строками.' : '');
    return [
        { value: 'by_sv', label: 'По СВ', ...bySv },
        { value: 'all', label: 'Общий', description: allDescription, disabled: false },
    ];
}

/* Выбранный вариант окна: прошлый выбор, если он сейчас доступен, иначе первый
   доступный. Без группы и СВ «По СВ» строить не по чему — остаётся «Общий». */
export function pickHoursReportScope(options, wanted) {
    const list = Array.isArray(options) ? options : [];
    const available = list.filter((option) => option && !option.disabled);
    if (available.some((option) => option.value === wanted)) return wanted;
    return available.length ? available[0].value : null;
}
