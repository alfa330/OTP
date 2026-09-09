import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { DEPARTMENT_VIEW_ALLOWLIST } from '../src/utils/departmentViews.js';

/**
 * Селектор отдела в сайдбаре и карта «раздел → отделы».
 *
 * У супер-админа в меню разделы всех отделов сразу, поэтому над списком стоит
 * селектор: выбрал отдел — видишь его разделы. Ломается это молча и в обе
 * стороны, и обе одинаково незаметны на своём рабочем месте:
 *
 *   * опечатка в section="..." у обёртки — фильтр для этого пункта просто
 *     перестаёт работать, пункт остаётся при любом выбранном отделе;
 *   * лишняя строка в карте — раздел исчезает у админа, выбравшего отдел,
 *     и найти его можно только вернувшись в «Все отделы».
 *
 * Поэтому проверяем не разметку, а два множества: чем помечены пункты и что
 * лежит в карте. Плюс поведение самой обёртки — таблицей.
 *
 * Объявления достаём из src/App.jsx: файл монолитный и не импортируется, а
 * переписать карту в тест значит проверять копию вместо кода.
 */
const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');

/* Объявление `const NAME ...;` целиком: от имени до точки с запятой на нулевой
   глубине скобок — тот же приём, что в verifier_chats_access.test.mjs. */
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

const evalDeclarations = (names, ret) => {
    const body = `${names.map(declarationOf).join('\n')}\nreturn ${ret};`;
    return new Function(body)();
};

const SECTION_DEPARTMENTS = evalDeclarations(
    ['SIDEBAR_SECTION_DEPARTMENTS'],
    'SIDEBAR_SECTION_DEPARTMENTS',
);

const SidebarDeptScope = evalDeclarations(
    ['SIDEBAR_SECTION_DEPARTMENTS', 'SidebarDeptScope'],
    'SidebarDeptScope',
);

// Чем помечены сами пункты меню.
const wrappedSections = [...source.matchAll(/<SidebarDeptScope section="([a-z_0-9]+)"/g)]
    .map((m) => m[1]);

test('каждая обёртка ссылается на существующую строку карты', () => {
    for (const section of wrappedSections) {
        assert.ok(
            Object.prototype.hasOwnProperty.call(SECTION_DEPARTMENTS, section),
            `section="${section}" нет в SIDEBAR_SECTION_DEPARTMENTS — фильтр отдела для этого пункта не работает`,
        );
    }
});

test('в карте нет строк без пункта меню', () => {
    const used = new Set(wrappedSections);
    for (const section of Object.keys(SECTION_DEPARTMENTS)) {
        assert.ok(
            used.has(section),
            `строка '${section}' в SIDEBAR_SECTION_DEPARTMENTS никого не фильтрует — либо пункт потерял обёртку, либо строка лишняя`,
        );
    }
});

test('каждый пункт помечен ровно один раз на ветку', () => {
    // Пункты, продублированные по ролевым ветвям, помечаются в каждой; админ
    // видит только свою копию, поэтому обёрток может быть больше одной, но
    // не больше, чем самих вхождений пункта в меню.
    for (const section of new Set(wrappedSections)) {
        const wraps = wrappedSections.filter((s) => s === section).length;
        assert.ok(wraps <= 2, `у '${section}' ${wraps} обёрток — в сайдбаре не бывает больше двух копий пункта`);
    }
});

test('в карте только настоящие коды отделов', () => {
    // Отделы без ограничений в DEPARTMENT_VIEW_ALLOWLIST не значатся, поэтому
    // СЗоВ добавлен отдельно — он и есть тот самый отдел по умолчанию.
    const known = new Set(['szov', ...Object.keys(DEPARTMENT_VIEW_ALLOWLIST)]);
    for (const [section, codes] of Object.entries(SECTION_DEPARTMENTS)) {
        assert.ok(Array.isArray(codes) && codes.length > 0, `у '${section}' пустой список отделов`);
        for (const code of codes) {
            assert.ok(known.has(code), `отдел '${code}' у раздела '${section}' не заведён — опечатка или новый отдел без карты разделов`);
        }
    }
});

test('обёртка пропускает пункт, пока отдел не выбран', () => {
    const item = { marker: 'пункт' };
    // Ни у одного не-админа активного отдела нет: фильтр для него выключен.
    assert.equal(SidebarDeptScope({ section: 'touches', activeCode: null, children: item }), item);
    assert.equal(SidebarDeptScope({ section: 'touches', activeCode: '', children: item }), item);
});

test('обёртка режет раздел чужого отдела и оставляет свой', () => {
    const item = { marker: 'пункт' };
    // «Касания» — раздел отдела продаж (звонки ОП из CDR АТС).
    assert.equal(SidebarDeptScope({ section: 'touches', activeCode: 'op', children: item }), item);
    assert.equal(SidebarDeptScope({ section: 'touches', activeCode: 'hr', children: item }), null);
    // «Табло Тез КЦ» — только ТЭЗ.
    assert.equal(SidebarDeptScope({ section: 'tez_wallboard', activeCode: 'tez', children: item }), item);
    assert.equal(SidebarDeptScope({ section: 'tez_wallboard', activeCode: 'szov', children: item }), null);
});

test('раздела нет в карте — селектор его не скрывает', () => {
    const item = { marker: 'пункт' };
    // Общефирменные разделы (Вики, Задачи, Ивенты, Отделы, Группы, Сессии)
    // в карте не значатся намеренно: забыть строку — значит оставить раздел
    // на виду, а не потерять его.
    assert.equal(SidebarDeptScope({ section: 'wiki', activeCode: 'hr', children: item }), item);
    assert.equal(SidebarDeptScope({ section: null, activeCode: 'hr', children: item }), item);
});

test('фильтр выключен у всех, кроме админов', () => {
    assert.equal(
        source.split('const activeDeptCode = isAdminLikeRole ? (sidebarDeptFilter || null) : null;').length - 1,
        1,
        'активный отдел должен считаться ровно один раз и только для админской роли',
    );
});

test('селектор стоит над прокручиваемым списком разделов', () => {
    // Панель селектора позиционируется обычным absolute, а не измеренными
    // координатами, ровно потому, что живёт ВНЕ .sidebar-menu-scroll
    // (у того overflow: auto — панель внутри была бы обрезана).
    const selector = source.indexOf('ref={sidebarDeptFilterRef}');
    const menu = source.indexOf('<ul ref={sidebarMenuScrollRef}');
    assert.ok(selector > 0, 'селектор отдела не найден');
    assert.ok(menu > 0, 'список разделов не найден');
    assert.ok(selector < menu, 'селектор отдела должен стоять до <ul> с разделами');
});

test('разделитель между блоками меню рисуется по содержимому блока', () => {
    assert.ok(
        source.includes('const renderDividerIfInner = (...flags) => ('),
        'renderDividerIfInner не найден — блоки меню снова разделяются безусловной чертой',
    );
    assert.ok(
        source.split('{renderDividerIfInner(').length - 1 >= 8,
        'условных разделителей стало меньше — проверь, не появилась ли лишняя черта у отдела с коротким меню',
    );
});
