import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    DEPARTMENT_VIEW_ALLOWLIST, departmentAllowsView, departmentRestrictsViews,
} from '../src/utils/departmentViews.js';

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
/* Читаем с нормализацией переводов строк: на Windows core.autocrlf=true
   отдаёт файлы с CRLF, а сравнения ниже многострочные — без этого тест
   краснел бы от переводов строк, а не от правки. */
const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const source = readLf('src/App.jsx');

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

/**
 * Что видит РЯДОВОЙ сотрудник в меню — и чего он видеть не должен.
 *
 * Класс ошибки, из-за которого этот тест и появился: пункт гейтится одним
 * `departmentAllowsView(user, 'X')`. У отдела БЕЗ ограничений (СЗоВ — он не
 * значится в DEPARTMENT_VIEW_ALLOWLIST) allowlist'а нет вовсе, и
 * departmentAllowsView возвращает true на ЛЮБОЙ ключ. Оператор линии получал
 * «Задачи» в меню, а сам раздел и бэкенд ему отказывали — пункт, ведущий в
 * отказ, выглядит как сломанный портал, а не как закрытый доступ.
 *
 * Лечится вторым условием — departmentRestrictsViews(user) — ровно так, как
 * уже сделано у «Журнала оценок» и «Деления звонков». Поэтому проверяем не
 * один раздел, а ВЕСЬ набор: список того, что оператор линии видит, задан
 * здесь явно и любое пополнение обязано быть осознанным.
 */
const RANK_MARKER = "{isRankAndFileRole(currentUserRole) && !isScopedDepartmentHead && (";

// Первая половина — меню, вторая — рендер экранов; так же режут файл
// tests/test_back_office_department_scope.py и tests/test_marketing_department_scope.py.
const rankMenuBranch = () => {
    const parts = source.split(RANK_MARKER);
    assert.equal(parts.length, 3, 'ветки рядового сотрудника изменились — проверь тест');
    return parts[1];
};

// Разделы, которые оператор ЛИНИИ (отдел без ограничений — СЗоВ) видит в меню.
// Это и есть его рабочий набор: своё, опросы, конкурсы и калькулятор.
const LINE_OPERATOR_VIEWS = [
    'ai_feedback',
    'contests',
    'evaluation',
    'hours',
    'profile',
    'salary',
    'shift_auction',
    'surveys',
    'work_schedules',
];

test('оператор линии видит только свои разделы', () => {
    const menu = rankMenuBranch();
    // Пункты с ОДИНАРНЫМ гейтом: их видит и отдел без ограничений.
    const single = [...menu.matchAll(/\{departmentAllowsView\(user, '([a-z_0-9]+)'\) && \(/g)]
        .map((m) => m[1])
        .sort();
    assert.deepEqual(
        single,
        LINE_OPERATOR_VIEWS,
        'изменился набор разделов, видимых оператору линии. Если раздел выдан только отделам '
        + 'с ограничениями (бэк-офис, «Маркетинг»), гейт обязан быть '
        + "`departmentRestrictsViews(user) && departmentAllowsView(user, '...')`: без первой "
        + 'половины departmentAllowsView пропускает СЗоВ, у которого allowlist отсутствует',
    );
});

test('«Задачи» рядовому — только в отделах с ограничениями', () => {
    const menu = rankMenuBranch();
    assert.ok(
        menu.includes("{departmentRestrictsViews(user) && departmentAllowsView(user, 'tasks') && ("),
        'пункт «Задачи» в ветке рядового снова гейтится одним departmentAllowsView — оператор линии '
        + 'получит пункт, ведущий в отказ бэкенда',
    );
    // Гейт пункта, гейт раздела и гейт закреплённой задачи — три копии одного
    // правила; расходятся они молча, поэтому сверяем их между собой.
    assert.ok(
        source.includes("|| (isRankAndFileRole(currentUserRole) && departmentRestrictsViews(user) && departmentAllowsView(user, 'tasks'));"),
        'canUsePinnedTasks разошёлся с пунктом меню',
    );
    const tasksView = readLf('src/components/tasks/TasksView.jsx');
    assert.ok(
        tasksView.includes("|| (departmentRestrictsViews(user) && departmentAllowsView(user, 'tasks'));"),
        'гейт самого раздела «Задачи» разошёлся с пунктом меню',
    );
});

test('оператор линии проходит departmentAllowsView, но не departmentRestrictsViews', () => {
    // Поведенческая половина: показываем, ПОЧЕМУ одинарного гейта мало.
    const lineOperator = { id: 1, role: 'operator', department_code: 'szov' };
    const hrEmployee = { id: 2, role: 'hr_manager', department_code: 'hr' };
    assert.equal(departmentRestrictsViews(lineOperator), false);
    assert.equal(departmentAllowsView(lineOperator, 'tasks'), true, 'без allowlist разрешено всё — это и есть ловушка');
    assert.equal(departmentRestrictsViews(hrEmployee), true);
    assert.equal(departmentAllowsView(hrEmployee, 'tasks'), true);
});

/**
 * Полоса прокрутки меню: волосяная и только во время прокрутки.
 *
 * Ломается молча и не там, где ищут: Chrome с версии 121 ОТКЛЮЧАЕТ всю
 * ::-webkit-scrollbar-стилизацию элемента, если у него задано хоть одно из
 * scrollbar-width / scrollbar-color. Одна такая строка (в том числе в тёмной
 * теме) возвращает штатную «тонкую» полосу в ~11 px вместо трёх — при этом
 * правило `width: 3px` остаётся в файле и выглядит работающим.
 */
const stylesCss = readLf('src/styles.css');
const darkCss = readLf('src/theme-dark.css');

/* Диапазоны блоков @supports not selector(::-webkit-scrollbar) — внутри них
   стандартные свойства как раз и нужны (это ветка для Firefox). Считаем по
   балансу фигурных скобок, а не «до первой закрывающей»: внутри блока лежат
   вложенные правила. */
const supportsRanges = (css) => {
    const ranges = [];
    const marker = '@supports not selector(::-webkit-scrollbar)';
    let at = css.indexOf(marker);
    while (at >= 0) {
        let depth = 0;
        let i = css.indexOf('{', at);
        for (; i < css.length; i += 1) {
            if (css[i] === '{') depth += 1;
            else if (css[i] === '}') {
                depth -= 1;
                if (depth === 0) break;
            }
        }
        ranges.push([at, i]);
        at = css.indexOf(marker, i);
    }
    return ranges;
};

test('стандартные свойства полосы меню заданы только внутри @supports', () => {
    for (const [name, css] of [['styles.css', stylesCss], ['theme-dark.css', darkCss]]) {
        const ranges = supportsRanges(css);
        const blocks = [...css.matchAll(/([^{}]*?)\{([^{}]*)\}/g)];
        for (const block of blocks) {
            const [selector, body] = [block[1], block[2]];
            if (!/sidebar-menu-scroll/.test(selector)) continue;
            if (!/scrollbar-width|scrollbar-color/.test(body)) continue;
            const inside = ranges.some(([from, to]) => block.index > from && block.index < to);
            assert.ok(
                inside,
                `${name}: «${selector.trim()}» задаёт scrollbar-width/color вне @supports — Chrome отключит ::-webkit-scrollbar и полоса снова станет толстой`,
            );
        }
    }
});

test('полоса прокрутки меню шириной 3 px и без ползунка в покое', () => {
    assert.ok(
        stylesCss.includes('.sidebar-menu-scroll::-webkit-scrollbar {\n      width: 3px;\n    }'),
        'ширина полосы меню перестала быть 3 px',
    );
    assert.ok(
        stylesCss.includes('.sidebar-menu-scroll::-webkit-scrollbar-thumb {\n      background: transparent;'),
        'ползунок в покое обязан быть прозрачным — иначе полоса видна всегда',
    );
    assert.ok(
        stylesCss.includes('.sidebar-menu-scroll.sidebar-menu-scrolling::-webkit-scrollbar-thumb {'),
        'нет правила, зажигающего ползунок во время прокрутки',
    );
});

test('класс прокрутки ставит и снимает обработчик в App.jsx', () => {
    assert.ok(source.includes("el.classList.add('sidebar-menu-scrolling');"), 'класс не ставится');
    assert.ok(
        source.includes("hideTimer = setTimeout(() => el.classList.remove('sidebar-menu-scrolling'), 500);"),
        'класс не снимается по таймеру — полоса останется висеть после прокрутки',
    );
    assert.ok(
        source.includes("el.addEventListener('scroll', handleScroll, { passive: true });"),
        'слушатель прокрутки должен быть passive — иначе он тормозит саму прокрутку',
    );
    assert.ok(
        source.includes("el.removeEventListener('scroll', handleScroll);"),
        'слушатель не снимается при размонтировании',
    );
});
