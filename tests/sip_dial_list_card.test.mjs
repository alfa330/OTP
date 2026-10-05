/*
 * «Настройки SIP — Binotel»: карточку сотрудника подменяет подсказка «Отдел
 * работает через „Обзвон из телефона“» — и тогда в ней нет ни полей, ни кнопки
 * «Сохранить». Какие отделы под неё попадают, решает
 * src/components/sip/dialListDepartments.js (без React).
 *
 * Что защищаем. 01.10.2026 Тез КЦ подключили к разделу «Обзвон из телефона», режим
 * не включали. Панель считала «отдел на обзвоне» по самому списку отделов раздела,
 * и карточки всех 15 операторов Тез КЦ остались без полей: новому сотруднику нельзя
 * было завести ни SIP-логин, ни пароль, ни кабинет Binotel. Признак — включённый
 * режим обзвона у отдела, а не строка настроек:
 *   • подключён, режим выключен — операторы на обычном телефоне, карточка обычная;
 *   • режим включён — линию назначают в разделе, карточка показывает подсказку;
 *   • непонятный ответ ручки — в пользу обычной карточки: лишние поля никому не
 *     мешают, а запертая карточка останавливает работу отдела.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { dialListManagedDepartmentIds } from '../src/components/sip/dialListDepartments.js';

/* То, что отдаёт GET /api/dial_list/departments (dial_list.service._department_row);
   значения — как на проде 05.10.2026. */
const TEZ = {
    department_id: 560, department_name: 'Тез КЦ', code: 'tez', provider: 'binotel',
    configured: true, enabled: false, portion_size: 20, leads_total: 0, leads_open: 0,
};
const REMOTE_CC = {
    department_id: 1954, department_name: 'Удаленный КЦ', code: 'remote_cc', provider: 'binotel',
    configured: true, enabled: true, portion_size: 20, leads_total: 3, leads_open: 1,
};

test('подключён к разделу, но режим выключен — карточка сотрудника остаётся обычной', () => {
    const ids = dialListManagedDepartmentIds([TEZ, REMOTE_CC]);
    assert.equal(ids.has(560), false, 'Тез КЦ работает на обычном телефоне: учётку АТС вводят в карточке');
    assert.deepEqual([...ids], [1954]);
});

test('режим обзвона включён — карточку сотрудника ведёт раздел «Обзвон»', () => {
    assert.equal(dialListManagedDepartmentIds([REMOTE_CC]).has(1954), true);
    // Режим включили и у второго отдела — под подсказку попадают оба.
    const both = dialListManagedDepartmentIds([{ ...TEZ, enabled: true }, REMOTE_CC]);
    assert.deepEqual([...both].sort((a, b) => a - b), [560, 1954]);
});

test('режим выключили — отдел возвращается к обычной карточке', () => {
    assert.equal(dialListManagedDepartmentIds([{ ...REMOTE_CC, enabled: false }]).size, 0);
});

test('id сравнивается числом: карточка проверяет Number(department_id)', () => {
    const ids = dialListManagedDepartmentIds([{ ...REMOTE_CC, department_id: '1954' }]);
    assert.equal(ids.has(1954), true);
    assert.equal(ids.has('1954'), false);
});

test('непонятный ответ ручки не запирает ни одной карточки', () => {
    for (const payload of [undefined, null, '', 'oops', 0, {}, { departments: [REMOTE_CC] }]) {
        assert.equal(dialListManagedDepartmentIds(payload).size, 0, `ответ ${JSON.stringify(payload)}`);
    }
    const ids = dialListManagedDepartmentIds([
        null,
        undefined,
        'строка вместо отдела',
        { department_id: 7 },                          // признака режима нет вовсе
        { department_id: 8, enabled: null },
        { department_id: 9, enabled: 'true' },         // не булево — режим не подтверждён
        { department_id: 10, enabled: 1 },
        { department_id: 11, configured: true },       // «подключён» сам по себе ничего не значит
        { enabled: true },                             // включён, но без id
        { department_id: 'abc', enabled: true },
    ]);
    assert.equal(ids.size, 0);
});

test('панель строит набор отделов этим правилом, а не по списку раздела', () => {
    // Строки ищем целиком и без переводов строк: рабочее дерево на Windows в CRLF.
    const view = readFileSync(new URL('../src/components/sip/SipSettingsView.jsx', import.meta.url), 'utf8');
    assert.ok(view.includes("import { dialListManagedDepartmentIds } from './dialListDepartments';"));
    assert.ok(view.includes('setDialListDeptIds(dialListManagedDepartmentIds(data.departments))'));
    assert.ok(!view.includes('.map((d) => Number(d.department_id))'),
        'прежнее правило «любой отдел из списка раздела» вернулось');
    // Обе развилки карточки — подвал и содержимое — спрашивают один и тот же набор.
    const forks = view.split('isBinotel && dialListDeptIds.has(Number(editing?.department_id))').length - 1;
    assert.equal(forks, 2);
});
