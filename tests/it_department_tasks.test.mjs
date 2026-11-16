import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { rankAndFileTasksAllowed } from '../src/utils/departmentViews.js';

/**
 * «Задачи» рядовым IT — решение владельца 16.11.2026.
 *
 * Отдел IT в карте разделов не значится, поэтому у оператора IT
 * departmentAllowsView отвечает true на всё, а departmentRestrictsViews — false:
 * старым двойным гейтом пункт «Задачи» у него не появлялся. Теперь рядовому
 * раздел выдаёт rankAndFileTasksAllowed: бэк-офису — по карте, IT — своим
 * набором. Проверяем поведение таблицей: кому пункт есть, кому нет, — и
 * подпись исполнителя в выпадашке (роль у рядового IT — «operator»).
 * Люди выдуманы.
 */

const person = (role, departmentCode, id = 1) => ({ id, role, department_code: departmentCode });

test('рядовой IT получает «Задачи», оператор линии — нет', () => {
    const cases = [
        // IT: рядовой отдела без своей должности.
        [person('operator', 'it'), true, 'специалист IT'],
        [person('trainee', 'it'), true, 'стажёр IT'],
        [person('operator', 'IT'), true, 'код отдела без учёта регистра'],
        // Должность бэк-офиса в IT — не его должность (сервер тоже не пустит).
        [person('hr_manager', 'it'), false, 'кадровик в IT'],
        // Бэк-офис и «Маркетинг» — по карте разделов, как и раньше.
        [person('hr_manager', 'hr'), true, 'кадровик'],
        [person('operator', 'hr'), true, 'старая учётка HR'],
        [person('accounting_manager', 'accounting'), true, 'бухгалтер'],
        [person('marketing_manager', 'marketing'), true, 'маркетолог'],
        // Отделы с линией и наборы «только это» — без «Задач».
        [person('operator', 'szov'), false, 'оператор линии СЗоВ'],
        [person('operator', 'op'), false, 'оператор ОП'],
        [person('operator', 'tez'), false, 'оператор Тез КЦ'],
        [person('operator', 'front_office'), false, 'сотрудник фронт-офиса'],
        [person('operator', 'remote_cc'), false, 'удалённый КЦ'],
        [person('operator', 'request_processing_department'), false, 'ООЗ'],
        [person('operator', 'development'), false, 'отдел-проект'],
        [person('operator', null), false, 'без отдела'],
        // Личный набор «только это» сильнее отдела.
        [person('operator', 'analytik', 540), false, 'личный набор «только Байга»'],
    ];
    for (const [user, expected, label] of cases) {
        assert.equal(rankAndFileTasksAllowed(user), expected, label);
    }
});

test('набор «только это» сильнее IT-пути', () => {
    // 540 — личный набор «только Байга» (PERSONAL_VIEW_ALLOWLIST). Окажись такой
    // человек в IT, пункта «Задачи» у него быть не должно: набор прячет всё,
    // чего в нём нет, — проверка IT не может стоять раньше него.
    assert.equal(rankAndFileTasksAllowed(person('operator', 'it', 540)), false);
    assert.equal(rankAndFileTasksAllowed(person('trainee', 'it', 540)), false);
});

test('код отдела IT сверяется без пробелов по краям — как на сервере', () => {
    // Сервер сверяет код через strip().lower(), SQL — через LOWER(BTRIM(...)):
    // иначе сервер раздел откроет, а пункта меню и экрана не будет.
    assert.equal(rankAndFileTasksAllowed(person('operator', ' it ')), true);
    assert.equal(rankAndFileTasksAllowed(person('operator', ' IT')), true);
});

test('руководителей IT предикат рядового не касается', () => {
    // СВ и админу раздел даёт их роль (ветка руководителя, canAccessTasks), а
    // этот предикат — только рядовому, как и сервер: _back_office_task_employee_role
    // пускает по отделу IT лишь 'operator' и 'trainee'.
    assert.equal(rankAndFileTasksAllowed(person('sv', 'it')), false);
    assert.equal(rankAndFileTasksAllowed(person('admin', 'it')), false);
});

/* Объявление `const NAME ...;` целиком — до точки с запятой на нулевой
   глубине скобок (тот же приём, что в sidebar_department_filter.test.mjs). */
const tasksView = readFileSync(new URL('../src/components/tasks/TasksView.jsx', import.meta.url), 'utf8')
    .split('\r\n').join('\n');
const declarationOf = (name) => {
    const at = tasksView.indexOf(`const ${name} `);
    assert.ok(at >= 0, `объявление ${name} не найдено — проверь тест`);
    let depth = 0;
    for (let i = at; i < tasksView.length; i += 1) {
        const ch = tasksView[i];
        if (ch === '(' || ch === '{' || ch === '[') depth += 1;
        if (ch === ')' || ch === '}' || ch === ']') depth -= 1;
        if (ch === ';' && depth === 0) return tasksView.slice(at, i + 1);
    }
    throw new Error(`не нашёл конец объявления ${name}`);
};

const { recipientRoleLabel } = new Function(
    `${['ROLE_LABELS', 'RECIPIENT_DEPARTMENT_LABELS', 'recipientRoleLabel'].map(declarationOf).join('\n')}
     return { recipientRoleLabel };`,
)();

test('исполнитель из IT подписан отделом, а не сырой ролью', () => {
    assert.equal(recipientRoleLabel({ role: 'operator', department_code: 'it' }), 'IT');
    assert.equal(recipientRoleLabel({ role: 'trainee', department_code: 'IT' }), 'IT');
    // Отдела нет (исполнитель взят из самой задачи) — не «operator».
    assert.equal(recipientRoleLabel({ role: 'operator' }), 'Сотрудник');
    // Старая учётка бэк-офиса на 'operator' — тем же словом, что коллеги по должности.
    assert.equal(recipientRoleLabel({ role: 'operator', department_code: 'hr' }), 'HR');
    assert.equal(recipientRoleLabel({ role: 'operator', department_code: 'accounting' }), 'Бухгалтерия');
    // Остальные роли — как раньше.
    assert.equal(recipientRoleLabel({ role: 'hr_manager', department_code: 'hr' }), 'HR');
    assert.equal(recipientRoleLabel({ role: 'admin', department_code: 'it' }), 'Админ');
    assert.equal(recipientRoleLabel({ role: 'sv' }), 'СВ');
    // Обе выпадашки исполнителей зовут эту подпись, своей копии нет.
    assert.equal(tasksView.split('(${recipientRoleLabel(person)})').length - 1, 2);
    assert.ok(!tasksView.includes('ROLE_LABELS[person.role] || person.role'));
});

/* Окно «Доски»: подпись кандидата в «Добавить участника». */
const projectsSheet = readFileSync(new URL('../src/components/tasks/TaskProjectsSheet.jsx', import.meta.url), 'utf8')
    .split('\r\n').join('\n');
const candidateDeclaration = (() => {
    const at = projectsSheet.indexOf('const candidateLabel ');
    assert.ok(at >= 0, 'candidateLabel не найден — проверь тест');
    const end = projectsSheet.indexOf('\n};\n', at);
    return projectsSheet.slice(at, end + 3);
})();
const candidateLabel = new Function(`${candidateDeclaration} return candidateLabel;`)();
const ROLE_LABELS = new Function(`${declarationOf('ROLE_LABELS')} return ROLE_LABELS;`)();

test('кандидат на доску из IT подписан отделом, как и все — своей подписью', () => {
    assert.equal(candidateLabel({ name: 'Иванов', role: 'operator', department: 'IT' }, ROLE_LABELS), 'Иванов (IT)');
    assert.equal(candidateLabel({ name: 'Петрова', role: 'hr_manager', department: 'HR' }, ROLE_LABELS), 'Петрова (HR)');
    assert.equal(candidateLabel({ name: 'Сидоров', role: 'sv', department: 'СЗоВ' }, ROLE_LABELS), 'Сидоров (СВ)');
    // Ни должности, ни отдела — одно имя, без пустых скобок.
    assert.equal(candidateLabel({ name: 'Ким', role: 'operator' }, ROLE_LABELS), 'Ким');
    assert.ok(projectsSheet.includes('label: candidateLabel(person, roleLabels),'));
});
