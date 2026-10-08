// Кандидаты в главы отдела — чистые правила списка в окне «Глава отдела»
// (DepartmentsView.jsx). Отдельным модулем, чтобы их исполнял node-тест:
// tests/remote_cc_department_views.test.mjs.
//
// Расширение в пути обязательно: модуль грузит напрямую Node, а ESM без
// расширения путь не разрешает (Vite разрешает и так).
import { normalizeRole } from '../../utils/roles.js';

const ROLE_LABELS = {
    super_admin: 'Супер-админ', admin: 'Админ', sv: 'Супервайзер',
    trainer: 'Тренер', operator: 'Оператор', trainee: 'Стажёр',
    hr_manager: 'HR-менеджер', accounting_manager: 'Менеджер бухгалтерии',
    marketing_manager: 'Менеджер маркетинга',
};

export const roleLabel = (role) => ROLE_LABELS[normalizeRole(role)] || role || '—';

// Сколько кандидатов показываем сразу; остальных находят поиском.
export const HEAD_CANDIDATES_LIMIT = 60;

const FIRED_STATUSES = new Set(['fired', 'dismissal']);

/* Люди из нескольких ручек одним списком, без повторов по id: одного и того же
   человека отдают и /api/admin/users, и /api/admin/sv_list. Первая запись
   побеждает; строки без id отбрасываются. */
export const mergePeople = (...lists) => {
    const byId = new Map();
    lists.forEach((list) => (Array.isArray(list) ? list : []).forEach((person) => {
        if (person?.id == null || person.id === '') return;
        const id = Number(person.id);
        if (!byId.has(id)) byId.set(id, person);
    }));
    return [...byId.values()];
};

/* Кого и в каком порядке показать в окне. Главой назначают и человека, который
   в отделе не числится (решение владельца 08.10.2026), поэтому отдел кандидата
   список не ограничивает — он только задаёт порядок:

     действующая глава  →  сотрудники самого отдела  →  остальные по алфавиту.

   Действующая глава первой намеренно: она может быть не из отдела, и без этого
   уезжала бы за обрез — в окне, которое её и снимает, её не было бы видно.
   Уволенных не показываем (кроме действующей главы): у человека нередко две
   учётки, рабочая и уволенная, и назначить главой уволенную — значит назначить
   никого.

   Возвращает { shown, total }: total — сколько подошло до обреза. */
export const pickHeadCandidates = ({
    people, department, query = '', departmentNameOf = () => '', limit = HEAD_CANDIDATES_LIMIT,
}) => {
    if (!department) return { shown: [], total: 0 };
    const text = String(query || '').trim().toLowerCase();
    const departmentId = Number(department.id);
    const currentHeadId = department.head_user_id == null || department.head_user_id === ''
        ? null
        : Number(department.head_user_id);
    const isCurrent = (person) => currentHeadId != null && Number(person?.id) === currentHeadId;
    const isOwn = (person) => {
        const own = person?.department_id ?? person?.departmentId;
        return own != null && own !== '' && Number(own) === departmentId;
    };
    const matched = (Array.isArray(people) ? people : [])
        .filter((person) => {
            if (!isCurrent(person) && FIRED_STATUSES.has(String(person?.status || '').toLowerCase())) return false;
            if (!text) return true;
            return [person?.name, roleLabel(person?.role), departmentNameOf(person)]
                .some((value) => String(value || '').toLowerCase().includes(text));
        })
        .sort((a, b) => (Number(isCurrent(b)) - Number(isCurrent(a)))
            || (Number(isOwn(b)) - Number(isOwn(a)))
            || String(a?.name || '').localeCompare(String(b?.name || ''), 'ru', { sensitivity: 'base' }));
    return { shown: matched.slice(0, limit), total: matched.length };
};
