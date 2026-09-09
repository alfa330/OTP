/* Сайдбар как список настроек: светлое полотно, карточки блоков, плитки.
 *
 * Вид собран СТИЛЯМИ поверх разметки, рассчитанной на тёмную полосу (белый
 * текст, синяя подсветка, утилиты Tailwind прямо на кнопках), плюс один
 * проход по DOM, который раздаёт блокам цвет и держит поиск по разделам.
 * Держатся эти правила на нескольких решениях, каждое из которых
 * разваливается молча — экран остаётся, просто выглядит собранным из
 * кусков. Их и сторожим.
 *
 * Читаем файлы ТЕКСТОМ и нормализуем переводы строк: в рабочей копии на
 * Windows они CRLF, а в репозитории LF, и литералы с \n иначе не находятся.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readLf = (rel) => readFileSync(path.join(ROOT, rel), 'utf8').split('\r\n').join('\n');

const app = readLf('src/App.jsx');
const styles = readLf('src/styles.css');
const dark = readLf('src/theme-dark.css');
const shell = readLf('src/components/common/mobile-shell.css');

/* Все правила файла как пары «селектор — тело». Комментарии выкусываем
   заранее: иначе запятая внутри комментария перед правилом читается как
   разделитель селекторов. Вложенных блоков (@media, @supports) этот разбор
   не разворачивает — он и не нужен: сторожим правила верхнего уровня. */
const rules = (css) => [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]*?)\{([^{}]*)\}/g)].map((m) => ({
    selector: m[1].trim(),
    body: m[2],
    at: m.index,
}));

/* Границы блоков @media (prefers-reduced-motion): правила внутри них
   намеренно общие для компьютера и телефона — «не двигай» относится ко всем,
   и запирать их в body:not(.mobile-shell) было бы ошибкой. */
const reducedMotionRanges = (css) => {
    const clean = css.replace(/\/\*[\s\S]*?\*\//g, '');
    const out = [];
    const marker = '@media (prefers-reduced-motion';
    let at = clean.indexOf(marker);
    while (at !== -1) {
        let depth = 0;
        let i = clean.indexOf('{', at);
        for (; i < clean.length; i += 1) {
            if (clean[i] === '{') depth += 1;
            else if (clean[i] === '}') {
                depth -= 1;
                if (depth === 0) break;
            }
        }
        out.push([at, i]);
        at = clean.indexOf(marker, i);
    }
    return out;
};

test('отступ контента равен ширине сайдбара', () => {
    /* --app-sidebar-offset читают и обычные разделы (margin-left), и слои,
       отрендеренные порталом в document.body. Разойдясь с шириной, они
       либо уезжают под сайдбар, либо оставляют полосу пустоты. */
    const width = /\.sidebar \{\s*\n\s*width: (\d+)px;/.exec(styles);
    assert.ok(width, 'не найдена ширина .sidebar');
    const offset = /:root \{\s*\n\s*--app-sidebar-offset: (\d+)px;/.exec(styles);
    assert.ok(offset, 'не найден --app-sidebar-offset на :root');
    assert.equal(
        offset[1],
        width[1],
        `отступ контента (${offset[1]}px) разошёлся с шириной сайдбара (${width[1]}px)`,
    );
    /* Развёрнутый по наведению рельс — та же ширина, иначе меню при
       наведении дёргается. */
    assert.ok(
        styles.includes(`.sidebar.collapsed:hover {\n      width: ${width[1]}px;\n    }`),
        'ширина развёрнутого по наведению сайдбара отстала от основной',
    );
    assert.ok(
        styles.includes(`.sidebar.collapsed:has(.sidebar-holds-open) {\n      width: ${width[1]}px;\n    }`),
        'ширина сайдбара с открытой панелью колокола отстала от основной',
    );
});

test('вид списка настроек не достаёт до мобильной шторки', () => {
    /* Мобильные правила стоят на body.mobile-shell и по специфичности равны
       настольным. Полагаться на порядок файлов нельзя: одно !important с
       любой стороны — и лист телефона поедет. Метки подобраны так, что все
       правила с ними завела эта переделка; общие для обоих экранов
       (.sidebar-menu-hidden, прокрутка, .sidebar-text) сюда не попадают. */
    const marks = [
        'sidebar-top-row',
        'sidebar-search',
        'sidebar-dept-filter',
        'sidebar-run-',
        'sidebar-menu-empty',
        'sidebar-menu-scroll >',
    ];
    const noMotion = reducedMotionRanges(styles);
    for (const rule of rules(styles)) {
        if (!marks.some((m) => rule.selector.includes(m))) continue;
        if (noMotion.some(([from, to]) => rule.at > from && rule.at < to)) continue;
        for (const part of rule.selector.split(',')) {
            assert.ok(
                part.trim().startsWith('body:not(.mobile-shell)'),
                `«${part.trim()}» не заперт в body:not(.mobile-shell) — правило дотянется до шторки телефона`,
            );
        }
    }
});

test('плитка значка перебивает и инлайн-размер, и flex-basis', () => {
    /* Размер значка приходит ИНЛАЙНОМ (FaIcon ставит width/height в 1em), а
       ширину в сайдбаре держит ещё и flex: 0 0 18px из общего правила. Без
       обоих перебиваний плитка остаётся 18-пиксельной точкой. */
    const tile = rules(styles).find((r) => r.selector.includes('.sidebar-menu-scroll > li > button > svg:first-child'));
    assert.ok(tile, 'не найдено правило плитки значка');
    assert.match(tile.body, /width: 26px !important;/);
    assert.match(tile.body, /height: 26px !important;/);
    assert.match(tile.body, /flex: 0 0 26px !important;/);
    assert.match(tile.body, /background: var\(--tint,/, 'цвет плитки обязан читаться из --tint');
    assert.match(tile.body, /border-radius: 7px;/);
});

test('цвет плитки один и тот же на телефоне и на компьютере', () => {
    /* Цвет раздаёт проход по списку в App.jsx — по БЛОКАМ. Круг из шести
       оттенков через nth-of-type считает СТРОКИ, и один раздел оказывался
       на телефоне и на компьютере разного цвета. */
    assert.ok(
        !/sidebar-menu-scroll > li:nth-of-type\(6n/.test(shell),
        'в мобильной шторке вернулся круг цветов по строкам — цвета разъедутся с настольными',
    );
    assert.ok(
        app.includes("row.style.setProperty('--tint', tint);"),
        'цвет ставится не на строку списка — шторка телефона наследует --tint именно оттуда',
    );
    const palette = /const SIDEBAR_BLOCK_TINTS = \[([\s\S]*?)\];/.exec(app);
    assert.ok(palette, 'не найдена палитра плиток');
    const colors = [...palette[1].matchAll(/'(#[0-9a-f]{6})'/g)].map((m) => m[1]);
    assert.ok(colors.length >= 12, `цветов в палитре ${colors.length}, а блоков у супер-админа 12`);
    assert.equal(new Set(colors).size, colors.length, 'в палитре повторяются цвета — соседние блоки сольются');
    assert.ok(
        app.includes('SIDEBAR_BLOCK_TINTS[index % SIDEBAR_BLOCK_TINTS.length]'),
        'палитра берётся без остатка по длине: меню длиннее палитры оставит блоки без цвета',
    );
});

test('скругления карточки считаются по видимым строкам', () => {
    /* Поиск гасит строки через display:none, и структурные `hr + li` и
       `li:has(+ hr)` начинают врать: скругление достаётся спрятанной
       строке, а видимая остаётся с прямым углом. */
    assert.ok(styles.includes('.sidebar-menu-scroll > li.sidebar-run-first > button'), 'нет скругления по классу первой строки');
    assert.ok(styles.includes('.sidebar-menu-scroll > li.sidebar-run-last > button'), 'нет скругления по классу последней строки');
    for (const rule of rules(styles)) {
        if (!rule.selector.startsWith('body:not(.mobile-shell)')) continue;
        assert.ok(
            !/hr \+ li|li:has\(\+ hr\)/.test(rule.selector),
            `«${rule.selector}» опирается на структуру, а поиск её ломает — нужны классы sidebar-run-*`,
        );
    }
    assert.ok(
        app.includes("row.classList.toggle('sidebar-run-first', row === first);"),
        'класс первой видимой строки блока не ставится',
    );
    assert.ok(
        app.includes("row.classList.toggle('sidebar-run-last', row === last);"),
        'класс последней видимой строки блока не ставится',
    );
});

test('поиск прячет и разделители опустевших блоков', () => {
    /* Иначе от блока, из которого поиск убрал все строки, остаётся щель, а
       над первым и под последним найденным — висячая черта. */
    assert.ok(app.includes("row.classList.toggle('sidebar-menu-hidden', !hit);"), 'строки не прячутся');
    assert.ok(
        app.includes("'sidebar-menu-hidden',\n                            !(block.shown && laterShown),"),
        'разделитель обязан оставаться только МЕЖДУ двумя видимыми блоками',
    );
    assert.ok(
        app.includes("strays.forEach((hr) => hr.classList.add('sidebar-menu-hidden'));"),
        'разделитель без строк перед ним обязан прятаться сам',
    );
    assert.ok(
        styles.includes('.sidebar-menu-hidden {\n      display: none !important;\n    }'),
        'класс скрытия обязан быть с !important: display у пунктов приходит из утилит Tailwind',
    );
    // «Ничего не нашлось» — только при непустом запросе, иначе оно висело бы
    // у роли, у которой меню и так пустое.
    assert.ok(
        app.includes('empty.hidden = !(query && !blocks.some((block) => block.shown));'),
        'подпись «ничего не нашлось» показывается не по запросу',
    );
});

test('поле поиска не пересобирает дерево меню', () => {
    /* Дерево сайдбара специально завёрнуто в useMemo: 45 пунктов в четырёх
       ролевых ветвях. Управляемое поле держало бы запрос в состоянии и
       пересобирало бы всё дерево на каждое нажатие клавиши. */
    assert.ok(app.includes('ref={sidebarSearchInputRef}'), 'поле поиска без ссылки');
    assert.ok(app.includes('defaultValue=""'), 'поле поиска стало управляемым');
    assert.ok(app.includes('onInput={handleSidebarSearchInput}'), 'ввод не доходит до фильтра');
    assert.ok(
        !/value=\{sidebarSearch/.test(app),
        'запрос уехал в состояние React — дерево меню будет пересобираться на каждую букву',
    );
    // Переключили строку поиска — запрос сбрасывается в любую сторону: иначе
    // список остаётся отфильтрованным по запросу, которого не видно.
    assert.ok(
        /sidebarSearchQueryRef\.current = '';\s+if \(!showSidebarSearch\) setSidebarCollapsed\(false\);\s+setShowSidebarSearch\(!showSidebarSearch\);/.test(app),
        'запрос не сбрасывается при переключении поиска',
    );
    // Свернули сайдбар — поиск закрывается: поля не видно, а фильтр остался бы.
    assert.ok(
        app.includes('if ((sidebarCollapsed || isMobileShell) && showSidebarSearch) handleToggleSidebarSearch();'),
        'при сворачивании сайдбара поиск не закрывается',
    );
    // Проход повторяется после каждой пересборки меню (сменилась роль, отдел
    // в селекторе, открылась строка поиска) — иначе классы и цвета теряются.
    assert.ok(
        app.includes('}, [paintSidebarMenu, sidebarTree]);'),
        'проход по списку не привязан к дереву меню',
    );
    assert.ok(
        app.includes('useLayoutEffect(() => {\n                paintSidebarMenu();'),
        'проход обязан быть useLayoutEffect: иначе первый кадр без плиток и с уже снятым фильтром',
    );
});

test('тёмная тема не оставляет за собой синюю плиту', () => {
    /* Полотно, карточки и разделители берутся из палитры --sheet-*, а она
       уже знает про тёмную тему. Градиенты сайдбара и футера в theme-dark
       перебивались бы карточкой и жили бы только как ложный след. */
    assert.ok(
        !/\.sidebar \{[^}]*linear-gradient/.test(dark),
        'в тёмной теме вернулся градиент сайдбара — его перебивает карточка списка',
    );
    assert.ok(
        !/\.sidebar-footer-menu \{[^}]*linear-gradient/.test(dark),
        'в тёмной теме вернулся градиент футера',
    );
    for (const token of ['--sheet-select:', '--sheet-select-ink:', '--sheet-danger-wash:']) {
        assert.ok(dark.includes(token), `${token} не переопределён для тёмной темы`);
        assert.ok(styles.includes(token), `${token} не задан в светлой теме`);
    }
});

test('полотно и карточки — из палитры шторки, без своих цветов', () => {
    /* Один и тот же язык на телефоне и на компьютере: палитра --sheet-*
       объявлена в mobile-shell.css на :root и там же переопределена для
       тёмной темы. Свой набор цветов здесь означал бы два экрана из разных
       приложений. */
    const bar = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar');
    assert.ok(bar, 'не найдено правило полотна сайдбара');
    /* Полотно панели — СВОЙ токен: общий --sheet-bg делят с мобильной
       шторкой, а голубизна нужна против светлой страницы, чего на телефоне
       нет вовсе (шторка занимает весь экран). */
    assert.match(bar.body, /background-color: var\(--otp-bar-bg,/);
    assert.match(bar.body, /border-right: 1px solid var\(--otp-bar-edge,/);
    for (const token of ['--otp-bar-bg:', '--otp-bar-edge:']) {
        assert.ok(styles.includes(token), `${token} не задан в светлой теме`);
        assert.ok(dark.includes(token), `${token} не переопределён для тёмной темы`);
    }
    assert.ok(
        !/--otp-bar-bg/.test(shell),
        'полотно настольной панели протекло в мобильную шторку — там свой --sheet-bg',
    );
    assert.match(bar.body, /background-image: none !important;/, 'синий градиент из разметки обязан гаситься');
    assert.match(bar.body, /color: var\(--sheet-text,/);
    for (const token of ['--sheet-bg', '--sheet-card', '--sheet-sep', '--sheet-active']) {
        assert.ok(shell.includes(`${token}:`), `${token} пропал из палитры шторки — сайдбар останется без цвета`);
    }
});

test('правила строк списка не достают до выпадашек', () => {
    /* Панели «Учета сотрудников» и «Расчета ресурсов» лежат ВНУТРИ своего
       <li>, поэтому селектор `> li > div > button` попадал в их пункты: те
       получали вид строки списка (белый фон, нулевое скругление, цветная
       плитка) и закрывали собой скругление панели — выпадашки выглядели
       квадратными. Строк вида `li > div > button` в меню нет вовсе, так что
       этот селектор может означать только выпадашку. */
    for (const rule of rules(styles)) {
        if (!rule.selector.startsWith('body:not(.mobile-shell)')) continue;
        assert.ok(
            !/> li[^,{]*> div > button/.test(rule.selector),
            `«${rule.selector}» красит кнопки выпадашки: панели живут внутри <li>, а строк li > div > button в меню нет`,
        );
    }
    // Тот же разбор для описательного селектора: он доставал до любой кнопки
    // внутри списка, включая пункты панелей.
    assert.ok(
        !/\.sidebar-menu-scroll button >/.test(styles),
        'селектор `.sidebar-menu-scroll button >` достаёт до кнопок выпадашек — нужен прямой потомок li',
    );
    // Панели без своей прокрутки обязаны обрезать содержимое по углам, иначе
    // выделенный пункт (bg-gray-100) закрывает угол квадратом.
    const clip = rules(styles).find((r) => /animate-dropdown:not\(\.overflow-y-auto\)/.test(r.selector));
    assert.ok(clip, 'нет правила, обрезающего содержимое выпадашек по их скруглению');
    assert.match(clip.body, /overflow: hidden;/);
    assert.ok(
        /:not\(\.overflow-y-auto\)/.test(clip.selector),
        'прокручиваемые панели (селектор отдела) обязаны быть исключены — overflow: hidden сломал бы им прокрутку',
    );
});

test('поиск и колокол видны и в свёрнутом рельсе', () => {
    /* Решение владельца 09.09.2026: до них нельзя было дотянуться, не
       разворачивая панель. Спрятано только ПОЛЕ поиска — в 60 px оно пустая
       строка, — а кнопка сама разворачивает сайдбар. */
    const hidden = rules(styles).filter((r) => /\.sidebar\.collapsed[^,{]*\.sidebar-search/.test(r.selector)
        && /display: none/.test(r.body));
    for (const rule of hidden) {
        assert.ok(
            !/\.sidebar-search-btn/.test(rule.selector),
            `«${rule.selector}» снова прячет кнопку поиска в рельсе`,
        );
    }
    assert.ok(
        app.includes('if (!showSidebarSearch) setSidebarCollapsed(false);'),
        'поиск в рельсе обязан разворачивать сайдбар — искать по невидимым подписям нельзя',
    );
    assert.ok(
        app.includes('if ((sidebarCollapsed || isMobileShell) && showSidebarSearch) handleToggleSidebarSearch();'),
        'обратный ход потерян: свернули — поиск должен закрыться',
    );
});

test('кнопки шапки переезжают анимацией и не встают под кнопку сворачивания', () => {
    /* Кнопка сворачивания висит на absolute top-4 -right-4: у рельса это
       (64…96, 16…48). Кнопки шапки едут по диагонали — вверх и вправо, — и
       когда их x доходит до правого края рельса, их y уже далеко ниже. Стоило
       колоколу приехать прямо под курсор, и панель уведомлений открывалась
       «сама». Здесь сторожим обе половины: и объявленную анимацию, и то, что
       в самом рельсе кнопки левее кнопки сворачивания. */
    const moving = rules(styles).find((r) => /\.sidebar-search-btn,\s*body:not\(\.mobile-shell\) \.sidebar-bell-slot/.test(r.selector));
    assert.ok(moving, 'кнопки шапки больше не позиционируются вместе');
    assert.match(moving.body, /position: absolute;/, 'переезд между двумя местами держится на absolute');
    assert.match(
        moving.body,
        /transition:[^;]*\btop 0\.3s ease\b/,
        'потерян плавный переезд наверх по вертикали (0,3 с — столько же едет ширина сайдбара)',
    );
    assert.match(moving.body, /transition:[^;]*\bright 0\.3s ease\b/, 'потерян переезд по горизонтали');
    assert.match(
        moving.body,
        /transition:[^;]*background-color/,
        'один transition затирает список из утилиты transition-colors — подсветка кнопки начнёт меняться рывком',
    );

    const RAIL = 80;
    const BTN = 30;
    // Левый край кнопки сворачивания у рельса: ширина минус её вылет (16 px).
    const COLLAPSE_LEFT = RAIL - 16;
    for (const name of ['sidebar-search-btn', 'sidebar-bell-slot']) {
        const rail = rules(styles).find((r) => r.selector.includes(`.sidebar.collapsed:not(:hover) .${name}`));
        assert.ok(rail, `нет положения ${name} в рельсе`);
        const right = Number(/right: (\d+)px/.exec(rail.body)?.[1]);
        const top = Number(/top: (\d+)px/.exec(rail.body)?.[1]);
        assert.ok(Number.isFinite(right) && Number.isFinite(top), `${name} в рельсе без координат`);
        assert.ok(RAIL - right <= COLLAPSE_LEFT, `${name} в рельсе залезает под кнопку сворачивания`);
        assert.ok(RAIL - right - BTN >= 0, `${name} в рельсе вылезает за левый край`);
        // По центру полосы: отступы слева и справа равны.
        assert.equal(RAIL - right - BTN, right, `${name} в рельсе стоит не по центру полосы`);
        assert.ok(top >= 54, `${name} в рельсе наезжает на логотип (top: ${top})`);
    }
    // Высота шапки в рельсе обязана держать место под столбик кнопок.
    const railRow = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar.collapsed:not(:hover) .sidebar-top-row');
    assert.ok(railRow, 'нет высоты шапки в рельсе');
    const railH = Number(/height: (\d+)px/.exec(railRow.body)?.[1]);
    const bellTop = Number(/top: (\d+)px/.exec(
        rules(styles).find((r) => r.selector.includes('.sidebar.collapsed:not(:hover) .sidebar-bell-slot')).body,
    )[1]);
    assert.ok(railH >= bellTop + BTN, `шапка в рельсе (${railH}px) не держит место под колокол (${bellTop}+${BTN})`);

    // Строка заголовка растянута на всю шапку и обязана не принимать клики:
    // она лежит НАД кнопкой сворачивания (та absolute и стоит раньше).
    const logo = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-top-row > h1');
    assert.ok(logo, 'нет правила строки заголовка');
    assert.match(logo.body, /pointer-events: none;/);
    const logoBtn = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-top-row > h1 button');
    assert.ok(logoBtn && /pointer-events: auto;/.test(logoBtn.body), 'вход в «4 You» внутри логотипа перестал кликаться');
});

test('в рельсе плитки по центру карточки', () => {
    /* Рядом с плиткой в строке остаются подпись и стрелка подменю — нулевой
       ширины, но всё ещё элементы флекса. Промежуток по 10 px до каждой и
       ml-auto у стрелки съедали свободное место, и плитка уезжала к левому
       краю: в строках с подменю и без — по-разному. */
    const railRow = rules(styles).find((r) => /\.sidebar\.collapsed:not\(:hover\) \.sidebar-menu-scroll > li > button/.test(r.selector));
    assert.ok(railRow, 'нет правила строки в рельсе');
    /* Центр набирается ОТСТУПОМ, а не justify-content: выравнивание
       применяется в тот же кадр, когда карточка ещё во всю ширину, и плитка
       улетала к центру 300-пиксельной строки, а потом «возвращалась» вместе
       со сжатием — владелец назвал это прыжками. Отступ едет вместе с
       шириной. Число обязано быть половиной разницы карточки и плитки. */
    assert.ok(
        !/justify-content: center;/.test(railRow.body),
        'выравнивание по центру возвращает прыжок плитки при уходе курсора',
    );
    const pad = Number(/padding-left: (\d+)px/.exec(railRow.body)?.[1]);
    assert.equal(pad, 17, `отступ строки в рельсе ${pad}px — плитка встанет не по центру карточки (60 − 26) / 2 = 17`);
    assert.match(railRow.body, /gap: 0;/, 'без gap: 0 промежутки до подписи сдвигают плитку');
    /* Отступ и подпись обязаны ЕХАТЬ, иначе рывок вернётся. */
    const moving = rules(styles).find((r) => /\.sidebar-menu-scroll > li > button,[\s\S]*\.sidebar-dept-filter > button/.test(r.selector)
        && /transition:/.test(r.body));
    assert.ok(moving, 'у строк нет перехода отступа');
    assert.match(moving.body, /transition:[^;]*padding-left 0\.3s/, 'отступ строки не едет — плитка снова будет прыгать');
    assert.match(moving.body, /overflow: hidden;/, 'без обрезки подпись вылезет за карточку в рельсе');
    /* Стрелку подменю в рельсе прячем совсем: 16 пикселей рядом с плиткой
       сдвигают её от центра карточки на 7, а появляется стрелка всё равно
       только по наведению, то есть уже у развёрнутой панели. */
    const arrow = rules(styles).find((r) => /\.sidebar\.collapsed:not\(:hover\)[^,{]*svg:not\(:first-child\)/.test(r.selector));
    assert.ok(arrow && /display: none;/.test(arrow.body), 'стрелка подменю в рельсе сдвинет плитку от центра');
    const back = rules(styles).find((r) => /:has\(\.sidebar-holds-open\)[^,{]*svg:not\(:first-child\)/.test(r.selector));
    assert.ok(back && /display: inline-block;/.test(back.body), 'в состоянии «панель открыта» стрелка обязана вернуться');
    const text = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar.collapsed:not(:hover) span.sidebar-text');
    /* Подпись в рельсе не растёт (иначе выдавит плитку из центра), но и не
       обнуляется по ширине: width: 0 обрезает текст в тот же кадр, и слова
       пропадали рывком вместо того, чтобы погаснуть. */
    assert.ok(text, 'нет правила подписи в рельсе');
    assert.match(text.body, /flex: 0 0 auto;/, 'подпись в рельсе снова растёт и выдавливает плитку');
    assert.match(text.body, /width: auto;/, 'подпись снова обнуляется по ширине — слова пропадут рывком');
    // Разделителей в рельсе нет: они начинаются от подписи, которой там нет.
    const sep = rules(styles).find((r) => /\.sidebar\.collapsed:not\(:hover\)[^,{]*:not\(\.sidebar-run-first\)::before/.test(r.selector));
    assert.ok(sep && /content: none;/.test(sep.body), 'в рельсе вернулась черта, висящая в воздухе');
});

test('селектор отдела открывается вбок и выглядит строкой', () => {
    /* Решение владельца 09.09.2026: вниз панель накрывала верх списка
       разделов — выбор отдела приходилось делать поверх того, что он меняет.
       На телефоне остаётся вниз: там шторка занимает весь экран. */
    assert.ok(
        app.includes("style={isMobileShell\n                                                    ? { position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 9999 }\n                                                    : { position: 'absolute', top: 0, left: '100%', marginLeft: 8, zIndex: 9999 }}"),
        'панель селектора отдела больше не открывается вбок на компьютере (и вниз на телефоне)',
    );
    assert.ok(
        app.includes("fas fa-chevron-${isMobileShell ? 'down' : 'right'}"),
        'стрелка селектора обязана показывать туда, куда откроется список',
    );
    const btn = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-dept-filter > button');
    assert.ok(btn, 'нет правила кнопки селектора отдела');
    assert.match(btn.body, /border: 0;/, 'с рамкой селектор читается полем ввода, а он строка');
    assert.match(btn.body, /border-radius: 11px;/);
    /* Общее правило строк ниже в файле не должно гасить это скругление:
       `border-radius: 0` вынесено в отдельное правило только для строк. */
    const shared = rules(styles).find((r) => /\.sidebar-menu-scroll > li > button,\s*body:not\(\.mobile-shell\) \.sidebar-dept-filter > button/.test(r.selector));
    assert.ok(shared, 'не найдено общее правило строки');
    assert.ok(
        !/border-radius/.test(shared.body),
        'общее правило снова задаёт border-radius и гасит скругления карточек и селектора',
    );
});

test('ни один селектор сайдбара не требует .sidebar внутри .sidebar', () => {
    /* Ровно на этом правило `.collapsed:not(:hover) .sidebar .sidebar-text`
       не срабатывало ни разу: класс .sidebar в портале один, вложенного нет.
       Такая опечатка не падает и не подсвечивается — правило просто молча
       ничего не красит. */
    const own = /\.sidebar(?![-\w])/g;
    for (const css of [styles, dark, shell]) {
        for (const rule of rules(css)) {
            for (const part of rule.selector.split(',')) {
                const hits = part.match(own);
                assert.ok(
                    !hits || hits.length < 2,
                    `«${part.trim()}» требует .sidebar внутри .sidebar — правило никогда не совпадёт`,
                );
            }
        }
    }
});

test('кнопка сворачивания лежит НАД шапкой', () => {
    /* Кнопка позиционирована (absolute) и стоит в разметке РАНЬШЕ шапки, а
       шапка тоже позиционирована — от неё считаются координаты поиска и
       колокола. Среди позиционированных элементов с z-index: auto порядок
       рисования и попадания курсора — порядок разметки, поэтому шапка
       накрывала кнопку и съедала ровно ту её половину, что лежит внутри
       полосы: 16 px из 32. Клик по ней не сворачивал сайдбар и даже не
       подсвечивал кнопку. */
    const btn = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-collapse-btn');
    assert.ok(btn, 'не найдено правило кнопки сворачивания');
    const z = Number(/z-index: (\d+)/.exec(btn.body)?.[1]);
    assert.ok(Number.isFinite(z) && z >= 1, 'у кнопки сворачивания нет z-index — шапка накроет её половину');
    // Но не выше выпадашек и панели колокола, иначе кнопка полезет на них.
    assert.ok(z < 40, `z-index кнопки сворачивания ${z} — она перекроет выпадашки (у них 40 и выше)`);
    const row = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-top-row');
    assert.ok(row && /position: relative;/.test(row.body), 'шапка перестала быть точкой отсчёта — координаты кнопок поедут от .sidebar');
    assert.ok(
        !/z-index/.test(row.body),
        'у шапки появился z-index: тогда сравнение с кнопкой сворачивания нужно пересчитать',
    );
});

test('поиск закрывается и когда окно сужается до шторки', () => {
    /* И кнопка, и поле закрыты гейтом !isMobileShell, а список разделов на
       телефоне остаётся — шторкой. Без сброса он оставался бы отфильтрованным
       по запросу, которого не видно; оболочка переключается на живой resize,
       то есть прямо под руками. */
    assert.ok(
        app.includes('if ((sidebarCollapsed || isMobileShell) && showSidebarSearch) handleToggleSidebarSearch();'),
        'поиск не закрывается при переходе в мобильную оболочку',
    );
    assert.ok(
        app.includes('}, [sidebarCollapsed, isMobileShell, showSidebarSearch, handleToggleSidebarSearch]);'),
        'в зависимостях эффекта нет isMobileShell — он не сработает на смене оболочки',
    );
});

test('тёмный слой правится в источнике, а не в собранном файле', () => {
    /* src/theme-dark.css СОБИРАЕТСЯ scripts/build_dark_theme.py из палитры и
       рукописной части scripts/dark_theme_chrome.css. Правка, сделанная
       только в собранном файле, живёт до первого прогона генератора — а он
       вернул бы и графитовый градиент сайдбара, и старый scrollbar-color.
       Поэтому участок «Сайдбар» в обоих файлах обязан совпадать дословно. */
    const chrome = readLf('scripts/dark_theme_chrome.css');
    const cut = (css, name) => {
        const from = css.indexOf('/* ── Сайдбар ──');
        const to = css.indexOf('/* ── Скелетоны загрузки ── */');
        assert.ok(from > 0 && to > from, `в ${name} не найден участок «Сайдбар»`);
        return css.slice(from, to).trim();
    };
    assert.equal(
        cut(dark, 'src/theme-dark.css'),
        cut(chrome, 'scripts/dark_theme_chrome.css'),
        'участок «Сайдбар» в собранном слое разошёлся с источником: прогон scripts/build_dark_theme.py откатит правку',
    );
});

test('включённый поиск не теряет акцент под курсором', () => {
    /* В тёмной теме есть правило `.sidebar button:hover` с !important — оно
       написано для белых выпадающих панелей, но по весу перекрывало и
       включённую кнопку поиска. */
    const on = rules(styles).find((r) => /\.sidebar-search-btn\.is-on:hover/.test(r.selector));
    assert.ok(on, 'нет правила для включённой кнопки поиска под курсором');
    assert.match(on.body, /background: var\(--sheet-select,[^;]*\) !important;/);
    for (const part of on.selector.split(',')) {
        assert.ok(
            part.includes('.sidebar '),
            `«${part.trim()}» без .sidebar в цепочке — в тёмной теме его перебьёт правило выпадашек`,
        );
    }
});

test('открытая панель держит рельс развёрнутым — и её признак висит на всех трёх', () => {
    /* Панели уведомлений, отделов и аккаунта прижаты к своим строкам
       (left: 100%), поэтому едут вместе с шириной сайдбара. Без удержания
       уход курсора с полосы схлопывал рельс, и панель прыгала на 220 px
       влево из-под курсора. Всплывающая карточка нового уведомления признак
       НЕ носит намеренно — она приходит сама. */
    const bell = readLf('src/components/notifications/NotificationsBell.jsx');
    assert.ok(
        /notifications-dropdown sidebar-holds-open/.test(bell),
        'панель уведомлений потеряла признак «держу рельс развёрнутым»',
    );
    assert.equal(
        (app.match(/sidebar-holds-open/g) || []).length,
        3,
        'признак должен стоять ровно на двух панелях App.jsx (отделы и аккаунт) и один раз в пояснении',
    );
    assert.ok(
        !/:has\(\.notifications-dropdown\)/.test(styles),
        'в правилах остался старый признак — список отделов снова будет прыгать',
    );
    /* Для состояния «панель открыта» вид обязан быть как у развёрнутого — во
       ВСЕХ трёх местах: строки списка, футер и селектор отдела. */
    const held = rules(styles).filter((r) => /:has\(\.sidebar-holds-open\)/.test(r.selector));
    assert.ok(held.length >= 10, `правил для состояния «панель открыта» всего ${held.length}`);
    const rowRule = held.find((r) => /justify-content: flex-start/.test(r.body)
        && r.selector.includes('.sidebar-menu-scroll > li > button'));
    assert.ok(rowRule, 'нет правила, возвращающего строкам выравнивание по левому краю');
    for (const mark of ['.sidebar-menu-scroll > li > button', '.sidebar-footer-menu > li > button', '.sidebar-dept-filter > button']) {
        assert.ok(rowRule.selector.includes(mark), `в состоянии «панель открыта» забыт ${mark} — он останется сжатым`);
    }
});

test('флекс достаётся подписи, а не стрелке подменю', () => {
    /* Класс sidebar-text носят оба: подпись (<span>) и стрелка (<svg>). С
       flex: 1 стрелка забирает половину строки и подпись обрезается вдвое. */
    for (const rule of rules(styles)) {
        // Внутри :not(...) класс стоит как исключение — такие правила не в счёт.
        const bare = rule.selector.replace(/:not\([^)]*\)/g, '');
        if (!/\.sidebar-text/.test(bare)) continue;
        if (!/flex:/.test(rule.body)) continue;
        assert.ok(
            /span\.sidebar-text/.test(rule.selector) || /\.sidebar \.sidebar-text/.test(rule.selector),
            `«${rule.selector}» задаёт flex всем .sidebar-text, включая стрелку подменю`,
        );
    }
});

test('карточка футера не разрывается утилитой space-y-2', () => {
    /* На самом <ul> стоит space-y-2 — второй строке достаётся margin-top: 8px.
       Внутри карточки это разрыв, а волосяная линия оказывается на его
       верхней кромке, в отрыве от строк. */
    const reset = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-footer-menu > *');
    assert.ok(reset, 'нет сброса отступов у строк футера');
    assert.match(reset.body, /margin-top: 0 !important;/);
});

test('«меньше движения» гасит и переезд шапки', () => {
    const at = styles.indexOf('@media (prefers-reduced-motion: reduce)');
    assert.ok(at > 0, 'нет блока prefers-reduced-motion');
    const block = styles.slice(at, styles.indexOf('\n    }\n', at) + 6);
    for (const sel of ['.sidebar-search-btn', '.sidebar-bell-slot', '.sidebar-top-row', '.sidebar,']) {
        assert.ok(block.includes(sel), `${sel} не гасится при «меньше движения» — кнопки продолжат ехать 76 px`);
    }
    assert.match(block, /transition: none !important;/);
});

test('«ничего не нашлось» объявляется программе чтения экрана', () => {
    /* Строку показывает и прячет проход по DOM, а не разметка: без role
       незрячий не узнаёт, что поиск ничего не дал. */
    assert.ok(
        app.includes('className="sidebar-menu-empty" role="status" hidden'),
        'у строки «ничего не нашлось» нет role="status"',
    );
});

test('логотип стоит в своей полосе, а не по середине шапки', () => {
    /* Оба начертания вынуты из потока (они перекрещиваются прозрачностью), и
       высота шапки у двух состояний разная: 54 px у развёрнутой панели и
       118 в рельсе, где под знаком стоят ещё поиск и колокол. С top: 50% знак
       уезжал на середину этой высоты — прямо на кнопку поиска, — да ещё к
       самому краю полосы: абсолютные координаты считаются от padding-бокса
       шапки, и её собственные 10 px отступа не прибавляются. */
    const logo = rules(styles).find((r) => /\.sidebar-logo-full,[\s\S]*\.sidebar-logo-mini/.test(r.selector)
        && /position: absolute/.test(r.body));
    assert.ok(logo, 'не найдено общее правило начертаний логотипа');
    assert.match(logo.body, /top: 0;/, 'знак снова отсчитывается от середины шапки');
    assert.ok(!/top: 50%/.test(logo.body), 'top: 50% уводит знак на кнопку поиска в рельсе');
    assert.match(logo.body, /height: 54px;/, 'у логотипа нет своей полосы — он поедет по высоте шапки');
    assert.match(logo.body, /left: 10px;/, 'знак прижат к кромке полосы вместо отступа карточек');
    const railLogo = rules(styles).find((r) => r.selector.includes('.sidebar.collapsed:not(:hover) .sidebar-logo-mini'));
    assert.ok(railLogo, 'нет положения знака в рельсе');
    const left = Number(/left: (\d+)px/.exec(railLogo.body)?.[1]);
    assert.equal(left, 20, `знак в рельсе на ${left}px — не по центру полосы (80 − 40) / 2 = 20`);
});
