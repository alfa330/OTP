/*
 * Кого предлагать во вкладке «Линии» раздела «Удаленный КЦ» при назначении линии.
 *
 * На линии удалённого КЦ может сидеть сотрудник любого отдела, но посадить и снять
 * ЧУЖОГО сотрудника вправе только глава СЗоВ и суперадмины (решение владельца
 * 07.10.2026), с 10.10.2026 — и СВ СЗоВ. Право считает сервер и отдаёт признаком can_seat_anyone вместе со
 * списком candidates — здесь оно только раскладывается по списку выбора. Остальным
 * руководителям раздела список остаётся прежним: сотрудники отдела линии.
 *
 * Здесь же мелкие правила того же раздела, которым нужна проверка исполнением:
 * сообщение после назначения, операторы для фильтра журнала, сверка названия
 * отдела с названием раздела.
 *
 * Логика без React: её сторожит tests/dial_list_line_picker.test.mjs.
 */

export const NO_DEPARTMENT_GROUP = 'Без отдела';
const OWN_GROUP_FALLBACK = 'Отдел линии';

const byName = (a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'ru');

const displayName = (person) => person.name || `#${person.id}`;

const toOption = (person, groupLabel) => ({
    value: String(person.id),
    // Логин — в самой подписи, как было до списка «по всей компании»: по нему
    // отличают полных тёзок, и общий список ищет только по подписи.
    label: `${displayName(person)}${person.login ? ` (@${person.login})` : ''}`,
    // Имя без логина — для сообщения после назначения.
    name: displayName(person),
    // Линия, на которой человек сидит сейчас: назначение пересадит его с неё.
    ...(person.sip_number ? { meta: `линия ${person.sip_number}` } : {}),
    ...(groupLabel ? { groupLabel } : {}),
});

/**
 * users — сотрудники отдела линии и те, кто уже сидит на его линиях (guest — человек
 * из другого отдела, department_name — его отдел); candidates — сотрудники других
 * отделов, которых можно посадить; departmentName — название отдела линии.
 * Возвращает options для CustomSelect: свои первыми, чужие — по отделам.
 */
export const buildLinePickerOptions = ({ users, candidates, canSeatAnyone, departmentName } = {}) => {
    const people = Array.isArray(users) ? users.filter((u) => u && u.id != null) : [];
    const own = people.filter((u) => !u.guest).sort(byName);
    // Без права сажать чужих список — как раньше: сотрудники отдела, без заголовков.
    if (!canSeatAnyone) return own.map((u) => toOption(u));

    const seen = new Set(own.map((u) => String(u.id)));
    const others = [];
    const extra = Array.isArray(candidates) ? candidates.filter((c) => c && c.id != null) : [];
    for (const person of [...people.filter((u) => u.guest), ...extra]) {
        const key = String(person.id);
        if (seen.has(key)) continue;
        seen.add(key);
        others.push(person);
    }
    const groupOf = (person) => String(person.department_name || '').trim() || NO_DEPARTMENT_GROUP;
    others.sort((a, b) => {
        const ga = groupOf(a);
        const gb = groupOf(b);
        if (ga !== gb) {
            // «Без отдела» — в самом низу: это не отдел, а его отсутствие.
            if (ga === NO_DEPARTMENT_GROUP) return 1;
            if (gb === NO_DEPARTMENT_GROUP) return -1;
            return ga.localeCompare(gb, 'ru');
        }
        return byName(a, b);
    });
    const ownGroup = String(departmentName || '').trim() || OWN_GROUP_FALLBACK;
    return [
        ...own.map((u) => toOption(u, ownGroup)),
        ...others.map((p) => toOption(p, groupOf(p))),
    ];
};

/** Кого подставить в выбор при открытии: первого свободного сотрудника отдела линии. */
export const defaultPickedUser = (users) => {
    const free = (Array.isArray(users) ? users : [])
        .filter((u) => u && u.id != null && !u.guest && !u.sip_number)
        .sort(byName);
    return free[0] ? String(free[0].id) : '';
};

/** Можно ли снять с линии её нынешнего человека: чужого — только с правом сажать чужих. */
export const canReleaseHolder = (holder, canSeatAnyone) => Boolean(holder) && (!holder.guest || Boolean(canSeatAnyone));

/**
 * Сообщение после назначения линии. Сотруднику другого отдела программа до этого
 * была не положена: кнопка «Скачать iCore Phone» приходит ему с профилем, то есть
 * появится после обновления страницы портала, — без этой фразы руководитель велит
 * человеку войти в программу, которую тому неоткуда взять.
 */
export const assignedToast = (lineNumber, name, guest) => (
    guest
        ? `Линия ${lineNumber} назначена: ${name}. Кнопка «Скачать iCore Phone» появится у сотрудника в меню портала после обновления страницы, вход в программу — логином iCORE`
        : `Линия ${lineNumber} назначена: ${name}. Сотруднику нужно войти в iCORE Phone заново`
);

/**
 * Операторы для фильтра журнала из ответа GET /api/dial_list/departments/<id>/users:
 * нынешний состав раздела и следом те, кто обзванивал базу раньше (former — сняли с
 * линии, уволили, перевели). Вторые помечены: в списке они приглушены, но отбор по
 * ним работает так же — их звонки в показателях отдела остаются.
 */
export const operatorsForFilter = (data) => {
    const current = Array.isArray(data?.users) ? data.users.filter((u) => u && u.id != null) : [];
    const seen = new Set(current.map((u) => String(u.id)));
    const former = (Array.isArray(data?.former) ? data.former : [])
        .filter((u) => u && u.id != null && !seen.has(String(u.id)))
        .map((u) => ({ ...u, former: true }));
    return [...current, ...former];
};

/**
 * Совпадает ли название отдела с названием раздела («Удаленный КЦ»). Раздел назван
 * так же, как сам отдел удалённого КЦ, и плашка единственного отдела в шапке не
 * должна повторять заголовок. «ё» и «е» не различаем: отдел могли завести и так, и так.
 */
export const sameSectionTitle = (a, b) => {
    const norm = (value) => String(value || '').trim().toLowerCase().replace(/ё/g, 'е');
    return norm(a) !== '' && norm(a) === norm(b);
};
