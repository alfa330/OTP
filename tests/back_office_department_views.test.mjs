import assert from 'node:assert/strict';
import test from 'node:test';

import {
    DEPARTMENT_VIEW_ALLOWLIST,
    departmentAllowsView,
    departmentCodeHidesFrontOfficeTraining,
    departmentCodeHidesOperatorFields,
    departmentCodeUsesEmployeeCity,
    departmentCodeUsesEmployeeJobTitle,
    departmentHidesOperatorFields,
    departmentRestrictsViews,
    departmentUsesSimpleEmployeeAccounting,
    firstAllowedView,
} from '../src/utils/departmentViews.js';

/* Бэк-офис — отделы «Бухгалтерия» (accounting) и «HR» (hr). По решению
   владельца им оставлены «Учёт сотрудников», «Задачи» и «Вики» — и больше
   ничего. Проверяем не текст файла, а поведение предикатов: allowlist — карта
   из литералов, и опечатка в ключе роли не ломает синтаксис, она молча снимает
   ограничение (роли нет в конфиге => ограничений нет вовсе). */

const BACK_OFFICE_CODES = ['accounting', 'hr'];

const head = (code) => ({
    id: 1, role: 'admin', department_code: code, department_id: 42,
    headed_department_id: 42,
});
const supervisor = (code) => ({ id: 2, role: 'sv', department_code: code });
const employee = (code, role = 'operator') => ({ id: 3, role, department_code: code });

for (const code of BACK_OFFICE_CODES) {
    test(`${code}: у главы учёт сотрудников, задачи и QR доступ`, () => {
        const user = head(code);
        assert.equal(departmentRestrictsViews(user), true);

        assert.equal(departmentAllowsView(user, 'manage_operators'), true);
        // Псевдонимы: пункты выпадашки главы ведут в manage_users/sv_list.
        assert.equal(departmentAllowsView(user, 'manage_users'), true);
        assert.equal(departmentAllowsView(user, 'sv_list'), true);
        // «QR доступ» — не украшение: без него оператору бэк-офиса не открыть
        // «Вики» (подтверждает админ, СВ или глава, а СВ в отделе нет).
        assert.equal(departmentAllowsView(user, 'qr_access'), true);
        assert.equal(departmentAllowsView(user, 'tasks'), true);

        for (const denied of [
            'work_schedules', 'sv_hours', 'groups', 'salary', 'surveys',
            'call_evaluation', 'call_division', 'monitoring_scale', 'ai_qa',
            'trainings', 'technical_issues', 'shift_auction', 'contests',
            'resource_fte', 'departments', 'manage_admins',
        ]) {
            assert.equal(departmentAllowsView(user, denied), false, denied);
        }

        // Раздел по умолчанию — учёт сотрудников; App.jsx переводит его в
        // 'manage_users' (упрощённый учёт).
        assert.equal(firstAllowedView(user, []), 'manage_operators');
        assert.equal(departmentUsesSimpleEmployeeAccounting(user), true);
    });

    test(`${code}: у рядового сотрудника только профиль`, () => {
        for (const role of ['operator', 'trainee']) {
            const user = employee(code, role);
            assert.equal(departmentRestrictsViews(user), true);
            assert.equal(departmentAllowsView(user, 'profile'), true);
            assert.equal(firstAllowedView(user, []), 'profile');

            for (const denied of [
                'hours', 'work_schedules', 'salary', 'evaluation', 'surveys',
                'manage_operators', 'manage_users', 'sv_list', 'qr_access', 'tasks',
            ]) {
                assert.equal(departmentAllowsView(user, denied), false, `${role}/${denied}`);
            }
        }
    });

    test(`${code}: у супервайзера учёт сотрудников без QR доступа`, () => {
        // Супервайзеров в бэк-офисе не планируется, но роль обязана быть в
        // конфиге: без неё ограничение снимается целиком, и первый же
        // заведённый СВ увидел бы всё меню отдела продаж.
        const user = supervisor(code);
        assert.equal(departmentRestrictsViews(user), true);
        assert.equal(departmentAllowsView(user, 'manage_operators'), true);
        assert.equal(departmentAllowsView(user, 'tasks'), true);
        assert.equal(departmentAllowsView(user, 'qr_access'), false);
        assert.equal(departmentAllowsView(user, 'groups'), false);
        assert.equal(departmentAllowsView(user, 'work_schedules'), false);
        // Псевдоним manage_users работает только у главы.
        assert.equal(departmentAllowsView(user, 'manage_users'), false);
        assert.equal(firstAllowedView(user, []), 'manage_operators');
    });

    test(`${code}: «Ивенты» остаются общими`, () => {
        // UNIVERSAL_VIEWS: пункт рендерится вне ролевых ветвей, и без
        // исключения гард видимости выкидывал бы человека обратно в профиль.
        assert.equal(departmentAllowsView(employee(code), 'events'), true);
        assert.equal(departmentAllowsView(head(code), 'events'), true);
    });

    test(`${code}: «Вики» выдаётся не этой картой`, () => {
        // Раздел даёт отделу тумблер departments.wiki_enabled вместе с
        // пространством вики (wikiEnabledFor в App.jsx). Строка 'wiki' в
        // allowlist'е была бы второй проверкой того же — и однажды разошлась
        // бы с первой. Тест держит инвариант: здесь её нет.
        assert.equal(departmentAllowsView(head(code), 'wiki'), false);
        assert.equal(departmentAllowsView(employee(code), 'wiki'), false);
        for (const roleViews of Object.values(DEPARTMENT_VIEW_ALLOWLIST[code])) {
            assert.equal(roleViews.includes('wiki'), false);
        }
    });

    test(`${code}: в карточке нет операторских полей и отметки об обучении`, () => {
        // Группа, направление и SIP-номер — поля человека на линии; их в
        // бэк-офисе нет вовсе, и валидация «Группа обязательна» не давала бы
        // завести сотрудника. Отметку «Был во фронт офисе на обучении» тоже не
        // спрашиваем: бэк-офис на линию не выходит.
        assert.equal(departmentCodeHidesOperatorFields(code), true);
        assert.equal(departmentHidesOperatorFields(employee(code)), true);
        assert.equal(departmentCodeHidesFrontOfficeTraining(code), true);
        // «Город» — только у фронт-офисов: бэк-офис сидит в головном офисе.
        assert.equal(departmentCodeUsesEmployeeCity(code), false);
        // Зато есть «Должность»: ею в бэк-офисе и различают людей — направления
        // и группы, которыми различают на линии, там не заведены вовсе.
        assert.equal(departmentCodeUsesEmployeeJobTitle(code), true);
    });

    test(`${code}: админ без своего отдела и супер-админ не ограничиваются`, () => {
        assert.equal(departmentRestrictsViews({ id: 4, role: 'admin', department_code: code }), false);
        assert.equal(departmentRestrictsViews({ id: 5, role: 'super_admin', department_code: code }), false);
    });

    test(`${code}: роли 'trainer' в конфиге нет намеренно`, () => {
        // Тренеру ограничение выдавать нельзя: TRAINER_ALLOWED_VIEWS не
        // содержит ни 'profile', ни 'manage_users', и два гарда в App.jsx
        // (тренерский → 'surveys', отдельский → firstAllowedView) начали бы
        // перекидывать вид друг другу без остановки.
        assert.equal('trainer' in DEPARTMENT_VIEW_ALLOWLIST[code], false);
        assert.equal(departmentRestrictsViews({ id: 6, role: 'trainer', department_code: code }), false);
    });
}

test('бэк-офис не задел остальные отделы', () => {
    assert.deepEqual(
        Object.keys(DEPARTMENT_VIEW_ALLOWLIST),
        ['tez', 'op', 'front_office', 'accounting', 'hr'],
    );
    // Упрощённый учёт — у бэк-офиса и фронт-офисов; у ОП и ТЭЗ выпадашка
    // со «Супервайзерами» и «Тренерами» остаётся.
    assert.equal(departmentUsesSimpleEmployeeAccounting(head('front_office')), true);
    assert.equal(departmentUsesSimpleEmployeeAccounting(head('op')), false);
    assert.equal(departmentUsesSimpleEmployeeAccounting(head('tez')), false);
    // Регистр кода отдела не важен: departmentCodeOf приводит его к нижнему.
    assert.equal(departmentUsesSimpleEmployeeAccounting(head('HR')), true);
    assert.equal(departmentAllowsView(head('Accounting'), 'manage_operators'), true);
    // Отделы на линии операторские поля сохраняют: скрытие адресное, а не
    // «у всех, кроме ОП».
    for (const code of ['szov', 'op', 'tez', 'front_office']) {
        assert.equal(departmentCodeHidesOperatorFields(code), false, code);
        assert.equal(departmentCodeUsesEmployeeJobTitle(code), false, code);
    }
});
