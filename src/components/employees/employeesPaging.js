/*
 * «Учет сотрудников» — списки по страницам с сервера.
 *
 * Список не приходит целиком: GET /api/admin/employees/page отдаёт одну
 * страницу, числа на метках статусов и у «Операторов» итоги групп. Вкладку
 * статуса, отдел, поиск, порядок и LIMIT/OFFSET считает база
 * (employees/page.py) по тем же правилам, что раньше считал браузер.
 *
 * Здесь — параметры запроса и разметка ответа, одни на компьютер (App.jsx) и
 * телефон (EmployeesMobileView); сам запрос — useEmployeesPage.js.
 * Закреплены tests/employees_paging.test.mjs.
 */

// Строк на странице — как у «Сессий», «Касаний», «Посылок» и «Оплаты счетов».
export const EMPLOYEES_PAGE_SIZE = 50;

// Поиск уходит на сервер, когда человек перестал печатать, а не на каждую букву.
export const EMPLOYEES_SEARCH_DELAY_MS = 300;

/* Строка запроса страницы. Роли — по алфавиту: один и тот же список не
   должен давать два разных запроса из-за порядка в массиве. */
export const employeesPageQuery = ({
    list = 'users',
    roles = null,
    status = 'active',
    department = '',
    search = '',
    sort = 'name',
    dir = 'asc',
    group = '',
    page = 1,
    size = EMPLOYEES_PAGE_SIZE,
    ids = false,
} = {}) => {
    const params = new URLSearchParams();
    params.set('list', list);
    if (Array.isArray(roles)) params.set('roles', [...roles].sort().join(','));
    if (status) params.set('status', status);
    if (department !== '' && department != null) params.set('department', String(department));
    const query = String(search || '').trim();
    if (query) params.set('q', query);
    if (sort) params.set('sort', sort);
    if (dir) params.set('dir', dir);
    if (group) params.set('group', group);
    const current = Math.max(1, Math.floor(Number(page)) || 1);
    params.set('limit', String(size));
    params.set('offset', String((current - 1) * size));
    if (ids) params.set('ids', '1');
    return params.toString();
};

/* Ответ сервера → страница для разметки: { rows, page, pageCount, total,
   from, to, listTotal, counts, groups }. Номер страницы — тот, что отдал
   сервер: людей стало меньше, и пятая страница превратилась в третью. */
export const pageOfResponse = (data, size = EMPLOYEES_PAGE_SIZE) => {
    const rows = Array.isArray(data?.employees) ? data.employees : [];
    const total = Math.max(0, Number(data?.total) || 0);
    const limit = Math.max(1, Number(data?.limit) || size);
    const offset = Math.max(0, Number(data?.offset) || 0);
    const pageCount = Math.max(1, Math.ceil(total / limit));
    return {
        rows,
        page: Math.min(pageCount, Math.floor(offset / limit) + 1),
        pageCount,
        total,
        from: rows.length ? offset + 1 : 0,
        to: offset + rows.length,
        listTotal: Math.max(0, Number(data?.list_total) || 0),
        counts: data?.status_counts && typeof data.status_counts === 'object' ? data.status_counts : {},
        groups: Array.isArray(data?.groups) ? data.groups : null,
    };
};

/* Ключ списка: какой список и чей. Данные другого списка («Тренеры» и
   «Админы» — один list=users с разными ролями) или прежней учётки в той же
   вкладке с этим ключом не совпадут и на экран не попадут. */
export const listKeyOf = (params, owner) => (params
    ? [
        owner ?? '',
        params.list || 'users',
        Array.isArray(params.roles) ? [...params.roles].sort().join(',') : '*',
        params.group || '',
    ].join('|')
    : null);

/* «Выбрать всех»: id всех найденных одним запросом — фильтры те же, строк не надо. */
export const idsQueryOf = (query) => {
    const params = new URLSearchParams(query);
    params.set('ids', '1');
    params.set('limit', '1');
    params.set('offset', '0');
    return params.toString();
};

/*
 * Какой список раздела сейчас на экране и что в нём выбрано — параметры для
 * useEmployeesPage; null — списка нет, запрос не уходит. Условия — те же, под
 * которыми App.jsx рисует список, поэтому чужой раздел запрос не шлёт.
 * state — из App.jsx: кто смотрит, страница, порядок и выбор каждого списка
 * ({ status, department, search }).
 */
export const employeeListParamsFor = (view, state) => {
    if (!state.userId || state.embed) return null;
    const usersOrder = { sort: state.usersSort.field, dir: state.usersSort.dir, page: state.page };
    if (state.showsAdminLists) {
        if (view === 'sv_list') {
            const { status, department, search } = state.sv;
            return { list: 'supervisors', status, department, search, ...usersOrder };
        }
        if (view === 'manage_users' || view === 'employees') {
            const { status, department, search } = state.staff;
            return { list: 'users', roles: state.staffRoles, status, department, search, ...usersOrder };
        }
        if (view === 'manage_trainers') {
            const { status, search } = state.trainers;
            return { list: 'users', roles: ['trainer'], status, search, ...usersOrder };
        }
        if (view === 'manage_admins' && state.showsAdmins) {
            const { status, search } = state.admins;
            return { list: 'users', roles: ['admin'], status, search, ...usersOrder };
        }
    }
    if (view === 'manage_operators' && state.showsOperators) {
        const { status, department, search } = state.operators;
        return {
            list: 'users', roles: state.operatorRoles, group: 'operators', status, department, search,
            sort: state.operatorsSort.field, dir: state.operatorsSort.dir, page: state.page,
        };
    }
    return null;
};

/* «Операторы» у СВ и тренера: строки страницы уже стоят в общем порядке
   «свои → направления». Шапка группы — на каждой странице, где есть её люди,
   а число в шапке (total) — вся группа с сервера, а не её кусок здесь. */
export const groupPageRows = (rows, groups) => {
    const meta = new Map((groups || []).map((group) => [group?.key, group]));
    const shown = [];
    (rows || []).forEach((row) => {
        const key = row?.list_group || 'all';
        let last = shown[shown.length - 1];
        if (!last || last.key !== key) {
            const info = meta.get(key) || {};
            last = { key, title: info.title || '', total: Number(info.total) || 0, rows: [] };
            shown.push(last);
        }
        last.rows.push(row);
    });
    return shown;
};

const offsetOf = (query) => Number(new URLSearchParams(query).get('offset')) || 0;

const pageOfQuery = (query) => {
    const params = new URLSearchParams(query);
    const limit = Math.max(1, Number(params.get('limit')) || EMPLOYEES_PAGE_SIZE);
    return Math.floor((Number(params.get('offset')) || 0) / limit) + 1;
};

const withOffset = (query, offset) => {
    const params = new URLSearchParams(query);
    params.set('offset', String(offset));
    return params.toString();
};

const isCancel = (error) => error?.name === 'CanceledError' || error?.name === 'AbortError';

/*
 * Загрузчик страниц без React — его зовёт useEmployeesPage на каждое изменение
 * запроса: sync(query, listKey, refreshStamp).
 *   • Тот же запрос уже в пути или уже на экране — второй раз не уходит.
 *   • Новый запрос отменяет прежний: ответ по «Ив» не перепишет ответ по «Иванов».
 *   • refreshStamp — когда начался последний перечитанный запрос полного
 *     списка (performance.now()). Начался позже нашей страницы — была правка,
 *     страница перечитывается; раньше (запрос при входе в портал) — нет.
 *   • Сервер прижал номер к последней странице — onPageMoved(номер); запрос с
 *     этим номером уже на экране и не повторяется.
 *   • query = null — списка на экране нет: вернутся — перечитаем.
 *   • Не загрузилась другая страница того же выбора — номер возвращается к
 *     той, что на экране: иначе повторное нажатие того же номера ничего бы не
 *     сделало (номер в App.jsx уже стоит). retry(query, listKey) — запрос
 *     заново, мимо всех «уже есть».
 * Запрос уходит микрозадачей: App.jsx сбрасывает номер страницы в
 * useLayoutEffect, и запрос прежней страницы нового фильтра успевает
 * отмениться, не уйдя в сеть.
 */
export const createPageLoader = ({ load, onData, onLoading, onError, onPageMoved, now = () => performance.now() }) => {
    let startedAt = -Infinity;
    let served = null;
    let inflight = null;

    const cancel = () => {
        inflight?.controller.abort();
        inflight = null;
    };

    const sync = (query, listKey, refreshStamp = 0) => {
        if (!query) {
            cancel();
            served = null;
            onLoading(false);
            return;
        }
        const fresh = refreshStamp > startedAt;
        if (!fresh) {
            if (inflight?.query === query) return;
            if (served === query) {
                cancel();
                onLoading(false);
                return;
            }
        }
        cancel();
        const controller = new AbortController();
        inflight = { query, controller };
        startedAt = now();
        onLoading(true);
        Promise.resolve()
            .then(() => {
                if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
                return load(query, { signal: controller.signal });
            })
            .then((data) => {
                if (controller.signal.aborted) return;
                inflight = null;
                const servedOffset = Math.max(0, Number(data?.offset) || 0);
                served = withOffset(query, servedOffset);
                onData(listKey, data);
                onLoading(false);
                if (servedOffset !== offsetOf(query)) onPageMoved?.(pageOfResponse(data).page);
            })
            .catch((error) => {
                if (controller.signal.aborted || isCancel(error)) return;
                inflight = null;
                onLoading(false);
                onError?.(error, listKey);
                if (served && served !== query && withOffset(query, offsetOf(served)) === served) {
                    onPageMoved?.(pageOfQuery(served));
                }
            });
    };

    const retry = (query, listKey) => sync(query, listKey, Infinity);

    return { sync, retry, dispose: cancel };
};
