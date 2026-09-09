// Нижний бар разделов на телефоне: что в нём стоит и куда он уезжает при повороте.
//
// Две вещи, которые ломаются молча и обе — на чужом устройстве:
//
//   * состав бара. Он показывает четыре ЕЖЕДНЕВНЫХ раздела, а набор разделов у
//     каждой роли свой. Лишняя строка в реестре — и человек получает кнопку в
//     раздел, куда его не пустят (ровно это было с «Задачами» у оператора
//     линии, коммит 8adf8cac); пропущенная — и бар остаётся с двумя кнопками;
//   * сторона бара при повороте. Бар прибит к нижней грани КОРПУСА и боком
//     становится вертикальной полосой у края. Перепутанные 90 и 270 переносят
//     его к противоположной грани — под ту руку, которой его там не ждут.
//
// Запуск: node --test tests/mobile_tab_bar.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    MOBILE_SHELL_QUERY,
    TAB_BAR_SIDE,
    TAB_BAR_THICKNESS,
    mobileShellReservedBoxes,
    readMobileShell,
    readOrientationAngle,
    tabBarSideForAngle,
} from '../src/utils/mobileShell.js';

/* Читаем с нормализацией переводов строк — как в sidebar_department_filter:
   на Windows core.autocrlf отдаёт CRLF, и сравнения краснели бы от них. */
const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

const appSource = readLf('src/App.jsx');
const shellCss = readLf('src/components/common/mobile-shell.css');
const tabBarSource = readLf('src/components/common/MobileTabBar.jsx');

/* Объявление `const NAME ...;` целиком — тот же приём, что в
   sidebar_department_filter.test.mjs: App.jsx монолитен и не импортируется, а
   переписать реестр в тест значит проверять копию вместо кода. */
const declarationOf = (name) => {
    const at = appSource.indexOf(`const ${name} `);
    assert.ok(at >= 0, `объявление ${name} не найдено — проверь тест`);
    let depth = 0;
    for (let i = at; i < appSource.length; i += 1) {
        const ch = appSource[i];
        if (ch === '(' || ch === '[' || ch === '{') depth += 1;
        else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
        else if (ch === ';' && depth === 0) return appSource.slice(at, i + 1);
    }
    throw new Error(`не нашёл конец объявления ${name} — проверь тест`);
};

const { pickMobileTabs, MOBILE_TAB_SECTIONS, MOBILE_TAB_LIMIT } = new Function(
    `${['MOBILE_TAB_SECTIONS', 'MOBILE_TAB_LIMIT', 'pickMobileTabs'].map(declarationOf).join('\n')}
     return { pickMobileTabs, MOBILE_TAB_SECTIONS, MOBILE_TAB_LIMIT };`,
)();

/* Наборы доступов трёх типовых аудиторий. Значения ровно те, что считает App по
   флагам роли, — здесь важен не способ их получить, а результат. */
const ADMIN = { wiki: true, lms: true, groupLate: true, tasks: true, workSchedules: true, surveys: true };
const LINE_OPERATOR = { wiki: true, lms: true, groupLate: false, tasks: false, workSchedules: true, surveys: true };
const BACK_OFFICE = { wiki: true, lms: false, groupLate: false, tasks: true, workSchedules: false, surveys: true };

const labels = (tabs) => tabs.map((tab) => tab.label);

test('в баре ровно четыре раздела — пятое место занято аватаром', () => {
    assert.equal(MOBILE_TAB_LIMIT, 4);
    for (const access of [ADMIN, LINE_OPERATOR, BACK_OFFICE]) {
        assert.equal(pickMobileTabs(access).length, 4);
    }
});

test('в баре ежедневные разделы этой роли, а не первые строки меню', () => {
    /* Служебные разделы (QR-доступ, «Сессии», «Рекрутинг») в бар не попадают,
       даже когда стоят в самом верху сайдбара: порядок меню переставляют, и
       бар, повторяющий верх списка, менялся бы у людей под руками. */
    assert.deepEqual(labels(pickMobileTabs(ADMIN)), ['Вики', 'Задачи', 'Курсы', 'Ивенты']);
    // У оператора линии «Задач» нет — их место занимает следующий доступный
    // раздел, а не пустая кнопка.
    assert.deepEqual(labels(pickMobileTabs(LINE_OPERATOR)), ['Вики', 'Курсы', 'Ивенты', 'Графики']);
    assert.deepEqual(labels(pickMobileTabs(BACK_OFFICE)), ['Вики', 'Задачи', 'Ивенты', 'Опросы']);

    const views = MOBILE_TAB_SECTIONS.map((section) => section.view);
    for (const service of ['qr_access', 'admin_sessions', 'recruiting', 'manage_users']) {
        assert.ok(!views.includes(service), `служебный раздел ${service} попал в бар`);
    }
});

test('закрытый раздел в бар не попадает', () => {
    const noWiki = pickMobileTabs({ ...ADMIN, wiki: false });
    assert.ok(!labels(noWiki).includes('Вики'), 'кнопка ведёт в раздел, куда не пустят');
    assert.equal(noWiki.length, 4, 'место закрытого раздела должно занять следующий');
});

test('у каждого кандидата есть раздел, подпись и значок', () => {
    for (const section of MOBILE_TAB_SECTIONS) {
        assert.ok(section.view, 'кандидат без view — кнопка в никуда');
        assert.ok(section.label, `у ${section.view} нет подписи`);
        assert.match(section.icon, /^fas fa-/, `у ${section.view} нет значка`);
        assert.equal(typeof section.allowed, 'function');
    }
});

test('бейджи приходят из счётчиков колокола и не выдумываются', () => {
    const tabs = pickMobileTabs(BACK_OFFICE, { events: 3, tasks: 12, surveys: 0 });
    assert.equal(tabs.length, 4);
    const byLabel = Object.fromEntries(tabs.map((tab) => [tab.label, tab.badge]));
    assert.equal(byLabel['Ивенты'], 3);
    assert.equal(byLabel['Задачи'], 12);
    assert.equal(byLabel['Опросы'], 0);
    // Вики бейджа не имеет вовсе — нулём, а не undefined: значение уходит в разметку.
    assert.equal(byLabel['Вики'], 0);
    // Мусор в счётчике не должен доезжать до кнопки.
    const broken = pickMobileTabs(BACK_OFFICE, { tasks: 'много' });
    assert.equal(broken.find((tab) => tab.label === 'Задачи').badge, 0);
});

test('поворот переносит бар к той же грани корпуса', () => {
    assert.equal(tabBarSideForAngle(0), TAB_BAR_SIDE.BOTTOM);
    assert.equal(tabBarSideForAngle(180), TAB_BAR_SIDE.BOTTOM);
    // 90 — верх корпуса смотрит влево, значит его низ (и бар) справа.
    assert.equal(tabBarSideForAngle(90), TAB_BAR_SIDE.RIGHT);
    assert.equal(tabBarSideForAngle(270), TAB_BAR_SIDE.LEFT);
    // window.orientation у Safari даёт -90 вместо 270 — это тот же поворот.
    assert.equal(tabBarSideForAngle(-90), TAB_BAR_SIDE.LEFT);
    // Мусор и отсутствие данных — портрет: бар внизу, то есть привычно.
    assert.equal(tabBarSideForAngle(undefined), TAB_BAR_SIDE.BOTTOM);
    assert.equal(tabBarSideForAngle('боком'), TAB_BAR_SIDE.BOTTOM);
});

test('угол читается и там, где нет Screen Orientation API', () => {
    assert.equal(readOrientationAngle({ screen: { orientation: { angle: 90 } } }), 90);
    // Safari старше 16.4: только window.orientation.
    assert.equal(readOrientationAngle({ orientation: -90 }), -90);
    assert.equal(readOrientationAngle({}), 0);
    assert.equal(readOrientationAngle(null), 0);
});

test('оболочка ловит и узкий экран, и телефон боком', () => {
    // Телефон боком — 844×390: по ширине «настольный» экран. Без второй половины
    // запроса оболочка при повороте выключалась бы вместе с местом под бар.
    assert.match(MOBILE_SHELL_QUERY, /max-width: 768px/);
    assert.match(MOBILE_SHELL_QUERY, /orientation: landscape/);
    assert.match(MOBILE_SHELL_QUERY, /max-height: 540px/);

    const fakeWindow = (matches, angle) => ({
        matchMedia: (query) => {
            assert.equal(query, MOBILE_SHELL_QUERY);
            return { matches };
        },
        screen: { orientation: { angle } },
    });
    assert.deepEqual(readMobileShell(fakeWindow(true, 90)), { shell: true, side: TAB_BAR_SIDE.RIGHT });
    // Вне оболочки сторона не хранится: настольная вёрстка не должна зависеть
    // от угла, под которым стоит монитор.
    assert.deepEqual(readMobileShell(fakeWindow(false, 90)), { shell: false, side: TAB_BAR_SIDE.BOTTOM });
    assert.deepEqual(readMobileShell(null), { shell: false, side: TAB_BAR_SIDE.BOTTOM });
});

test('занятые навигацией полосы совпадают со стороной бара', () => {
    const viewport = { width: 400, height: 800 };
    const bottom = mobileShellReservedBoxes({ shell: true, side: TAB_BAR_SIDE.BOTTOM }, viewport);
    assert.deepEqual(bottom[0], { x: 0, y: 800 - TAB_BAR_THICKNESS, width: 400, height: TAB_BAR_THICKNESS });

    const right = mobileShellReservedBoxes({ shell: true, side: TAB_BAR_SIDE.RIGHT }, viewport);
    assert.deepEqual(right[0], { x: 400 - TAB_BAR_THICKNESS, y: 0, width: TAB_BAR_THICKNESS, height: 800 });

    const left = mobileShellReservedBoxes({ shell: true, side: TAB_BAR_SIDE.LEFT }, viewport);
    assert.deepEqual(left[0], { x: 0, y: 0, width: TAB_BAR_THICKNESS, height: 800 });

    // Колокол занимает правый верхний угол при любом повороте.
    assert.ok(bottom.some((box) => box.x + box.width === viewport.width && box.y === 0));
    // Вне оболочки запретных зон нет вовсе.
    assert.deepEqual(mobileShellReservedBoxes({ shell: false }, viewport), []);
});

test('толщина бара в CSS и в геометрии — одно число', () => {
    // Разъехавшись, они дают либо шарика на баре, либо дырку под ним.
    const declared = /--mtb-thickness:\s*(\d+)px;/.exec(shellCss);
    assert.ok(declared, 'в CSS нет переменной толщины бара');
    assert.equal(Number(declared[1]), TAB_BAR_THICKNESS);
});

test('повёрнутый бар разворачивает содержимое в ту же сторону, что и корпус', () => {
    /* ЗНАК ПОВОРОТА ПРОВЕРЕН ТЕЛЕФОНОМ В РУКАХ, а не рассуждением: при angle 90
       (бар справа) верх корпуса ушёл влево, значит его правая сторона теперь
       СВЕРХУ экрана — прежнее «слева направо» читается снизу вверх, то есть
       содержимое повёрнуто против часовой. Обратный знак даёт бар, который
       выглядит перевёрнутым на 180°; именно на это и была жалоба. */
    assert.match(shellCss, /\[data-side="right"\] \.mtb-item__inner \{\s*transform: rotate\(-90deg\);/);
    assert.match(shellCss, /\[data-side="left"\] \.mtb-item__inner \{\s*transform: rotate\(90deg\);/);
    /* Порядок кнопок обязан совпадать с направлением поворота: у правого бара
       первая кнопка внизу (column-reverse), у левого — сверху. Разъехавшись со
       знаком rotate, они дают подписи, читаемые в одну сторону, и порядок —
       в другую. */
    const right = shellCss.slice(shellCss.indexOf('.mobile-tabbar[data-side="right"] {'));
    assert.match(right.slice(0, 600), /flex-direction: column-reverse;/);
    const left = shellCss.slice(shellCss.indexOf('.mobile-tabbar[data-side="left"] {'));
    assert.match(left.slice(0, 600), /flex-direction: column;/);
    assert.doesNotMatch(left.slice(0, 600), /flex-direction: column-reverse;/);
});

test('бар не перехватывает повторный тап по открытому разделу', () => {
    // Повторный вызов раздела перезагружал бы его данные на ровном месте.
    assert.match(tabBarSource, /if \(item\.view === activeView && !menuOpen\) return;/);
});
