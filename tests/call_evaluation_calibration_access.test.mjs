import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/*
 * Вкладка «Калибровка» журнала оценок — руководителям и супервайзерам
 * (решение владельца 27.10.2026). С 16.06.2026 её видели только глобальный
 * админ и СВ, а все главы отделов на проде — админы, поэтому у руководителей
 * она пропала.
 *
 * Флаги прав живут прямо в теле компонента App, поэтому исполняем НАСТОЯЩИЙ
 * блок их объявлений из исходника: от `const userId` до первого useState.
 * Люди — те же, что в tests/test_call_calibration_access.py (зеркало
 * _calibration_role на сервере): разъедутся — вкладка нарисуется и отдаст 403.
 */

const source = readFileSync(new URL('../src/call_evaluation/main.jsx', import.meta.url), 'utf8');
const start = source.indexOf('    const userId = user?.id;');
const end = source.indexOf('    const [calls, setCalls] = useState([]);', start);
assert.ok(start > 0 && end > start, 'блок флагов прав App не найден');

const flagsFor = new Function(
    'user',
    `${source.slice(start, end)}
    return { canUseCalibration, canManageCalibrationRooms, isGlobalAdminRole, isSupervisorRole };`,
);

const managers = {
    'супер-админ': { id: 448, role: 'super_admin' },
    'админ без отдела': { id: 169, role: 'admin' },
    'глава СЗоВ (admin)': { id: 1, role: 'admin', headed_department_id: 1 },
    'глава с ролью оператора': { id: 700, role: 'operator', headed_department_id: 2008 },
    'глава с ролью тренера': { id: 701, role: 'trainer', headed_department_id: 1933 },
};
const supervisors = {
    'СВ': { id: 55, role: 'sv' },
    'СВ по старому имени роли': { id: 56, role: 'Supervisor' },
    'СВ — глава отдела': { id: 702, role: 'sv', headed_department_id: 367 },
};
const outsiders = {
    'оператор': { id: 126, role: 'operator' },
    'тренер': { id: 206, role: 'trainer' },
    'наблюдатель «Маркетинга»': { id: 474, role: 'marketing_manager' },
    'без входа': null,
};

test('руководители видят калибровку и ведут комнаты', () => {
    for (const [who, user] of Object.entries(managers)) {
        const flags = flagsFor(user);
        assert.equal(flags.canUseCalibration, true, who);
        assert.equal(flags.canManageCalibrationRooms, true, who);
        assert.equal(flags.isSupervisorRole, false, `${who}: не участник, в комнату не входит`);
    }
});

test('супервайзеры видят калибровку и участвуют в ней', () => {
    for (const [who, user] of Object.entries(supervisors)) {
        const flags = flagsFor(user);
        assert.equal(flags.canUseCalibration, true, who);
        assert.equal(flags.canManageCalibrationRooms, true, who);
        assert.equal(flags.isSupervisorRole, true, who);
    }
});

test('остальным вкладка не рисуется', () => {
    for (const [who, user] of Object.entries(outsiders)) {
        const flags = flagsFor(user);
        assert.equal(flags.canUseCalibration, false, who);
        assert.equal(flags.canManageCalibrationRooms, false, who);
    }
});

test('подвал калибровки склоняет число комнат', () => {
    const at = source.indexOf('const calibrationRoomsCountLabel = (count) => {');
    const stop = source.indexOf('\n};\n', at);
    assert.ok(at > 0 && stop > at, 'calibrationRoomsCountLabel не найден');
    const label = new Function(`${source.slice(at, stop + 3)}\nreturn calibrationRoomsCountLabel;`)();
    const cases = {
        0: '0 комнат', 1: '1 комната', 2: '2 комнаты', 4: '4 комнаты', 5: '5 комнат',
        11: '11 комнат', 12: '12 комнат', 14: '14 комнат', 21: '21 комната', 22: '22 комнаты',
        25: '25 комнат', 101: '101 комната', 111: '111 комнат',
    };
    for (const [count, expected] of Object.entries(cases)) {
        assert.equal(label(Number(count)), expected);
    }
});

test('удалять комнаты и звонки по-прежнему может только глобальный админ', () => {
    assert.equal(flagsFor(managers['админ без отдела']).isGlobalAdminRole, true);
    assert.equal(flagsFor(managers['супер-админ']).isGlobalAdminRole, true);
    assert.equal(flagsFor(managers['глава СЗоВ (admin)']).isGlobalAdminRole, false);
    assert.equal(flagsFor(managers['глава с ролью оператора']).isGlobalAdminRole, false);
});
