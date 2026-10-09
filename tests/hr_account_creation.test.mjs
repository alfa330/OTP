import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import { build } from 'esbuild';
import { departmentCodeEmployeeRole } from '../src/utils/departmentViews.js';

const require = createRequire(import.meta.url);
const React = require('react');
const compiled = await build({
    entryPoints: [new URL('../src/components/modals/UserEditModal.jsx', import.meta.url).pathname],
    bundle: true, write: false, format: 'cjs', platform: 'node', packages: 'external',
    loader: { '.css': 'empty' },
});
const modalModule = { exports: {} };
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(
    require, modalModule, modalModule.exports,
);
const UserEditModal = modalModule.exports.default;
const departments = [
    { id: 1, code: 'szov', name: 'СЗоВ' },
    { id: 2, code: 'hr', name: 'HR' },
    { id: 3, code: 'accounting', name: 'Бухгалтерия' },
    { id: 4, code: 'it', name: 'IT' },
];
const hr = { id: 42, role: 'hr_manager', department_id: 2, department_code: 'hr' };
const appSource = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
const createStart = appSource.indexOf('const openCreateManageUsersEmployee = () => {');
const createEnd = appSource.indexOf('\n            };', createStart);
const openCreate = appSource.slice(createStart, createEnd + '\n            };'.length);

// Execute the real entry point: the initial role must agree with the department
// before the modal decides which required fields to display.
function initialDraft(user = hr, filter = '') {
    let draft;
    new Function('user', 'departments', 'manageUsersDeptFilter', 'isScopedDepartmentHead',
        'isEmployeeAccountingManager', 'departmentCodeEmployeeRole', 'setUserToEdit',
        'setShowUserEditModal', `${openCreate}; openCreateManageUsersEmployee();`)(
        user, departments, filter, !!user.headed_department_id, true,
        departmentCodeEmployeeRole, (value) => { draft = value; }, () => {},
    );
    return draft;
}

// Run the actual modal and its event handlers with persistent hook state.
// Child controls stay React elements; DOM effects (focus, images, back gesture)
// are irrelevant to department selection, validation and the submitted draft.
function form(draft = initialDraft(), user = hr) {
    const state = [];
    const saved = [];
    let cursor = 0;
    const dispatcher = {
        useState(initial) {
            const slot = cursor++;
            if (!(slot in state)) state[slot] = typeof initial === 'function' ? initial() : initial;
            return [state[slot], (next) => {
                state[slot] = typeof next === 'function' ? next(state[slot]) : next;
            }];
        },
        useRef(initial) { return this.useState(() => ({ current: initial }))[0]; },
        useMemo(factory) { return factory(); },
        useCallback(callback) { return callback; },
        useEffect() {},
    };
    const props = {
        isOpen: true, userToEdit: draft, user, departments, onClose() {},
        directions: [
            { id: 10, name: 'Основа', department_id: 1 },
            { id: 40, name: 'Разработка', department_id: 4 },
        ],
        groups: [
            { id: 100, name: 'Группа СЗоВ', status: 'active', department_id: 1, direction_id: 10 },
            { id: 400, name: 'Группа IT', status: 'active', department_id: 4, direction_id: 40 },
        ],
        onSave: async (value) => {
            saved.push(value);
            return { status: 'success', id: 900, login: 'test-login', password: 'test-password' };
        },
    };
    function render() {
        cursor = 0;
        const ref = React.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentDispatcher;
        const previous = ref.current;
        ref.current = dispatcher;
        try { return UserEditModal(props); }
        finally { ref.current = previous; }
    }
    function nodes(tree) {
        if (Array.isArray(tree)) return tree.flatMap(nodes);
        if (!tree || typeof tree !== 'object') return [];
        return [tree, ...nodes(tree.props?.children)];
    }
    function text(tree) {
        if (Array.isArray(tree)) return tree.map(text).join('');
        if (typeof tree === 'string' || typeof tree === 'number') return String(tree);
        return tree?.props ? text(tree.props.children) : '';
    }
    return {
        saved,
        field(label) {
            const box = nodes(render()).find((node) => React.Children.toArray(node.props?.children)
                .some((child) => child?.type === 'label' && text(child).trim() === label));
            return box && nodes(box).find((node) => typeof node.props?.onChange === 'function');
        },
        async click(label) {
            const button = nodes(render()).find((node) => node.type === 'button' && text(node).trim() === label);
            assert.ok(button, `Кнопка «${label}» доступна`);
            assert.ok(!button.props.disabled, `Кнопка «${label}» активна`);
            await button.props.onClick();
        },
        text: () => text(render()),
    };
}

test('HR без фильтра начинает с оператора СЗоВ, а с фильтром — с роли выбранного отдела', () => {
    for (const user of [hr, { ...hr, role: 'admin', headed_department_id: 2, headed_department_code: 'hr' }]) {
        assert.equal(initialDraft(user).role, 'operator');
        assert.equal(initialDraft(user, 2).role, 'hr_manager');
        assert.equal(initialDraft(user, 3).role, 'accounting_manager');
    }
});

test('HR видит доступный выбор всех отделов при создании и редактировании', async () => {
    for (const draft of [initialDraft(), { ...initialDraft(), id: 99 }]) {
        const ui = form(draft);
        await ui.click('Общее');
        const select = ui.field('Отдел');
        assert.ok(select, 'Поле «Отдел» видно кадровику');
        assert.equal(select.props.disabled, false);
        assert.deepEqual(select.props.options.map((option) => option.value), ['', 1, 2, 3, 4]);
        assert.ok(ui.field('Группа'), 'HR может назначить оператору группу');
        assert.ok(ui.field('Направление'), 'HR может назначить оператору направление');
    }
});

test('HR меняет бэк-офис на СЗоВ: обязательные поля появляются до отправки', async () => {
    const ui = form({ ...initialDraft(hr, 2), name: 'Тестовый сотрудник', hire_date: '2026-10-09' });
    await ui.click('Общее');
    assert.equal(ui.field('Группа'), undefined);
    const department = ui.field('Отдел');
    assert.ok(department);
    department.props.onChange(1);
    assert.ok(ui.field('Группа'));
    assert.ok(ui.field('Направление'));
    await ui.click('Создать');
    assert.equal(ui.saved.length, 0);
    assert.match(ui.text(), /Группа обязательна/);
    ui.field('Группа').props.onChange(100);
    ui.field('Направление').props.onChange(10);
    await ui.click('Создать');
    assert.equal(ui.saved.length, 1);
    assert.equal(ui.saved[0].role, 'operator');
    assert.equal(ui.saved[0].department_id, 1);
    assert.equal(ui.saved[0].group_id, 100);
    assert.equal(ui.saved[0].direction_id, 10);
    assert.match(ui.text(), /test-login/);
});

test('«Создать ещё» сохраняет выбранный отдел и не назначает HR супервайзером', async () => {
    const ui = form({ ...initialDraft(), name: 'Тестовый сотрудник', hire_date: '2026-10-09' });
    await ui.click('Общее');
    const department = ui.field('Отдел');
    assert.ok(department);
    department.props.onChange(3);
    assert.equal(ui.field('Группа'), undefined);
    assert.equal(ui.field('Направление'), undefined);
    await ui.click('Создать');
    assert.equal(ui.saved[0].role, 'accounting_manager');
    await ui.click('Создать ещё');
    await ui.click('Общее');
    assert.equal(ui.field('Отдел').props.value, 3);
    assert.equal(ui.field('Группа'), undefined);
    // Switch back to a line department: no stale back-office role or group.
    ui.field('Отдел').props.onChange(1);
    assert.ok(ui.field('Группа'));
    assert.equal(ui.field('Группа').props.value, '');
    await ui.click('Данные');
    ui.field('ФИО').props.onChange({ target: { value: 'Второй сотрудник' } });
    await ui.click('Корпоративное');
    ui.field('Дата найма').props.onChange({ target: { value: '2026-10-09' } });
    await ui.click('Общее');
    ui.field('Группа').props.onChange(100);
    ui.field('Направление').props.onChange(10);
    await ui.click('Создать');
    assert.equal(ui.saved.length, 2);
    assert.equal(ui.saved[1].role, 'operator');
    assert.ok(!ui.saved[1].supervisor_id);
});

test('обычный глава отдела по-прежнему ограничен своим отделом', async () => {
    const ui = form(initialDraft(), { id: 7, role: 'sv', department_id: 1, department_code: 'szov', headed_department_id: 1 });
    await ui.click('Общее');
    const select = ui.field('Отдел');
    assert.equal(select.props.disabled, true);
    assert.equal(select.props.value, 1);
});

test('выбор отдела не превращает тренера, супервайзера или стажёра в оператора', async () => {
    for (const role of ['trainer', 'sv', 'trainee']) {
        const ui = form({ ...initialDraft(hr, 2), role, name: 'Тестовый сотрудник', hire_date: '2026-10-09' });
        await ui.click('Общее');
        ui.field('Отдел').props.onChange(1);
        await ui.click('Создать');
        assert.equal(ui.saved.length, 1, role);
        assert.equal(ui.saved[0].role, role);
        assert.equal(ui.saved[0].department_id, 1);
    }
});

test('IT: HR и глава отдела создают сотрудника без группы или с выбранной группой', async () => {
    const itHead = { id: 7, role: 'admin', department_id: 4, department_code: 'it', headed_department_id: 4 };
    for (const user of [hr, itHead]) {
        for (const groupId of ['', 400]) {
            const ui = form({ ...initialDraft(hr, 4), name: 'Разработчик', hire_date: '2026-10-09', direction_id: 40 }, user);
            await ui.click('Общее');
            const group = ui.field('Группа');
            assert.ok(group);
            assert.equal(group.props.options[0].label, 'Без группы');
            group.props.onChange(groupId);
            await ui.click('Создать');
            assert.equal(ui.saved.length, 1);
            assert.equal(ui.saved[0].department_id, 4);
            assert.equal(ui.saved[0].group_id, groupId);
        }
    }
});

test('при смене IT на СЗоВ группа снова обязательна', async () => {
    const ui = form({ ...initialDraft(hr, 4), name: 'Сотрудник', hire_date: '2026-10-09', direction_id: 40 });
    await ui.click('Общее');
    ui.field('Отдел').props.onChange(1);
    assert.equal(ui.field('Группа').props.options[0].label, 'Выберите группу');
    ui.field('Направление').props.onChange(10);
    await ui.click('Создать');
    assert.equal(ui.saved.length, 0);
    assert.match(ui.text(), /Группа обязательна/);
});

test('IT: HR и глава отдела заводят сотрудника без группы и без направления', async () => {
    const itHead = { id: 7, role: 'admin', department_id: 4, department_code: 'it', headed_department_id: 4 };
    for (const user of [hr, itHead]) {
        const ui = form({ ...initialDraft(hr, 4), name: 'Разработчик', hire_date: '2026-10-09' }, user);
        await ui.click('Общее');
        assert.equal(ui.field('Группа').props.options[0].label, 'Без группы');
        assert.equal(ui.field('Направление').props.options[0].label, 'Без направления');
        await ui.click('Создать');
        assert.equal(ui.saved.length, 1, user.role);
        assert.equal(ui.saved[0].role, 'operator');
        assert.equal(ui.saved[0].department_id, 4);
        // Пустое App.jsx (saveUserChanges) отправляет на сервер как null.
        assert.ok(!ui.saved[0].group_id);
        assert.ok(!ui.saved[0].direction_id);
        assert.match(ui.text(), /test-login/);
    }
});

test('при смене IT на СЗоВ направление снова обязательно', async () => {
    const ui = form({ ...initialDraft(hr, 4), name: 'Сотрудник', hire_date: '2026-10-09' });
    await ui.click('Общее');
    ui.field('Отдел').props.onChange(1);
    assert.equal(ui.field('Направление').props.options[0].label, 'Выберите направление');
    ui.field('Группа').props.onChange(100);
    ui.field('Направление').props.onChange('');
    await ui.click('Создать');
    assert.equal(ui.saved.length, 0);
    assert.match(ui.text(), /Направление обязательно/);
});
