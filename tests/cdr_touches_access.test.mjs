import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    headedDepartmentId, isDepartmentHead, isSupervisorRole, normalizeRole,
} from '../src/utils/roles.js';

/**
 * Кому виден пункт «Касания» (canAccessTouchesSectionForUser в src/App.jsx).
 *
 * С 02.10.2026 сверх ролей есть поимённый допуск: двое из «Маркетинга», id 471
 * и 472, с 05.10.2026 — ещё и глава «Маркетинга», id 415. Их должность и отдел
 * предикат по ролям не пропускает (у главы роль admin, но назначение главой её
 * заменяет), поэтому строка поимённого списка обязана стоять ДО ролевых отсечек
 * — поставленная после `if (!isSupervisorRole(role)) return false;`, она не
 * сработала бы никогда, а текстовая проверка списка этого не заметила бы.
 * Поэтому гоняем настоящий предикат таблицей людей. Объявления достаём из
 * src/App.jsx: файл монолитный и не импортируется, а копия предиката в тесте
 * проверяла бы копию.
 * Серверную границу (cdr/access.py) и совпадение списков проверяет
 * tests/test_cdr_access.py.
 */
const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');

/* Объявление `const NAME ...;` целиком: от имени до точки с запятой на нулевой
   глубине скобок — тело предиката многострочное. */
const declarationOf = (name) => {
    const at = source.indexOf(`const ${name} `);
    assert.ok(at >= 0, `объявление ${name} не найдено — проверь тест`);
    let depth = 0;
    for (let i = at; i < source.length; i += 1) {
        const ch = source[i];
        if (ch === '(' || ch === '[' || ch === '{') depth += 1;
        else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
        else if (ch === ';' && depth === 0) return source.slice(at, i + 1);
    }
    throw new Error(`не нашёл конец объявления ${name} — проверь тест`);
};

const NAMES = [
    'AI_QA_OP_DEPARTMENT_ID',
    'normalizeDepartmentCode',
    'isOpSalesSupervisorForAiQa',
    'aiQaHeadDepartmentCodesOf',
    'TOUCHES_SECTION_DEPARTMENT_CODE',
    'TOUCHES_EXTRA_ACCESS_USER_IDS',
    'isTouchesSectionDepartmentHead',
    'canAccessTouchesSectionForUser',
];

const canAccessTouchesSectionForUser = new Function('deps', `
    const { headedDepartmentId, isDepartmentHead, isSupervisorRole, normalizeRole } = deps;
    ${NAMES.map(declarationOf).join('\n')}
    return canAccessTouchesSectionForUser;
`)({ headedDepartmentId, isDepartmentHead, isSupervisorRole, normalizeRole });

const OP = 367;
const MARKETING = 1041;

const PEOPLE = [
    // [кто, пользователь, виден ли пункт]
    ['поимённо: 471', { id: 471, role: 'marketing_manager', department_id: MARKETING, department_code: 'marketing' }, true],
    ['поимённо: 472', { id: 472, role: 'marketing_manager', department_id: MARKETING, department_code: 'marketing' }, true],
    ['поимённо, id строкой', { id: '472', role: 'marketing_manager', department_code: 'marketing' }, true],
    ['поимённо: 415, глава «Маркетинга»', {
        id: 415, role: 'admin', department_id: MARKETING, department_code: 'marketing',
        headed_department_id: MARKETING, headed_department_codes: ['marketing'],
    }, true],
    // Остальной «Маркетинг» допуска не получил. Главе раздел выдан как человеку,
    // а не как главе: тот же профиль с другим id пункта не видит.
    ['другой маркетолог', { id: 474, role: 'marketing_manager', department_id: MARKETING, department_code: 'marketing' }, false],
    ['другой глава «Маркетинга»', {
        id: 999, role: 'admin', department_id: MARKETING, department_code: 'marketing',
        headed_department_id: MARKETING, headed_department_codes: ['marketing'],
    }, false],
    // Прежний периметр — без изменений.
    ['супер-админ', { id: 1, role: 'super_admin' }, true],
    ['глобальный админ', { id: 2, role: 'admin' }, true],
    ['глава СЗоВ', { id: 3, role: 'admin', headed_department_id: 1, headed_department_codes: ['szov'] }, false],
    ['глава отдела продаж', { id: 4, role: 'admin', headed_department_id: OP, headed_department_codes: ['op'] }, true],
    ['СВ ОП по id отдела', { id: 5, role: 'sv', department_id: OP }, true],
    ['СВ ОП по коду отдела', { id: 6, role: 'sv', department_code: 'op' }, true],
    ['СВ СЗоВ', { id: 7, role: 'sv', department_code: 'szov' }, false],
    ['оператор ОП', { id: 8, role: 'operator', department_id: OP, department_code: 'op' }, false],
    ['тренер ОП', { id: 9, role: 'trainer', department_code: 'op' }, false],
    ['нет сессии', null, false],
];

test('пункт «Касания»: поимённые сверх ролей, остальной периметр прежний', () => {
    for (const [who, user, expected] of PEOPLE) {
        assert.equal(canAccessTouchesSectionForUser(user), expected, who);
    }
});

/* Предикат — полдела: флаг ещё должен дойти до пункта меню, ссылки, гарда раздела
   и экрана, и в каждом месте его легко подменить соседним. Рядом стоит флаг
   «Воронки ОП» — та же аудитория, но без поимённых: подмена закрыла бы раздел
   только им, а главу и СВ отдела продаж пускают оба предиката, и никто бы не
   заметил. Пробелы между строками — \s*: на Windows файл лежит в CRLF. */
test('флаг «Касаний» стоит на пункте меню, ссылке, гарде раздела и экране', () => {
    const wired = [
        ['флаг считает свой предикат',
            /const canAccessTouchesSection = canAccessTouchesSectionForUser\(user\);/],
        ['пункт меню',
            /\{canAccessTouchesSection && \(\s*<SidebarDeptScope section="touches"/],
        ['раздел по ссылке ?view=touches',
            /\(requestedViewFromUrl !== 'touches' \|\| canAccessTouchesSection\) &&/],
        ['гард раздела по отделу',
            /if \(view === 'touches' && canAccessTouchesSection\) return;/],
        ['экран раздела',
            /\{view === "touches" && canAccessTouchesSection && \(\s*<Suspense[\s\S]{0,300}?<TouchesView/],
    ];
    for (const [what, pattern] of wired) assert.match(source, pattern, what);
    // Второй пункт или второй экран мимо флага первая проверка не увидела бы.
    assert.equal(source.split('<SidebarDeptScope section="touches"').length - 1, 1, 'пункт меню один');
    assert.equal(source.split('<TouchesView').length - 1, 1, 'экран раздела один');
});
