/*
 * «Учет сотрудников» — списки по страницам.
 *
 * Список приходит с сервера одним ответом и нужен целиком: по нему считаются
 * числа на метках статусов, дни рождения, поиск, порядок и массовый выбор.
 * Страницы режут только то, что рисуется: таблица в несколько сотен строк с
 * фото строится долго, а её подвал уезжает далеко вниз.
 *
 * Одни правила на компьютер (App.jsx) и телефон (EmployeesMobileView).
 * Закреплены tests/employees_paging.test.mjs.
 */

// Строк на странице — как у «Сессий», «Касаний», «Посылок» и «Оплаты счетов».
export const EMPLOYEES_PAGE_SIZE = 50;

/* Страница списка: { rows, page, pageCount, total, from, to }. Номер вне
   диапазона прижимается к краю: человека уволили или перевели, строк стало
   меньше — и последняя страница не превращается в пустую. */
export const pageOfEmployees = (rows, page, size = EMPLOYEES_PAGE_SIZE) => {
    const list = Array.isArray(rows) ? rows : [];
    const total = list.length;
    const pageCount = Math.max(1, Math.ceil(total / size));
    const current = Math.min(Math.max(1, Math.floor(Number(page)) || 1), pageCount);
    const start = (current - 1) * size;
    const shown = list.slice(start, start + size);
    return { rows: shown, page: current, pageCount, total, from: shown.length ? start + 1 : 0, to: start + shown.length };
};

/* Список группами («Мои операторы» и направления у СВ и тренера): страница
   режет общий порядок строк, а не группы. Шапка группы стоит на каждой
   странице, где есть её люди, а число в шапке (total) — вся группа, а не
   её кусок на этой странице. */
export const pageOfEmployeeGroups = (groups, page, size = EMPLOYEES_PAGE_SIZE) => {
    const flat = [];
    (groups || []).forEach((group) => {
        (group?.rows || []).forEach((row) => flat.push({ group, row }));
    });
    const { rows, ...paging } = pageOfEmployees(flat, page, size);
    const shown = [];
    rows.forEach(({ group, row }) => {
        let last = shown[shown.length - 1];
        if (!last || last.key !== group.key) {
            last = { ...group, rows: [], total: group.rows.length };
            shown.push(last);
        }
        last.rows.push(row);
    });
    return { ...paging, groups: shown };
};
