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
    /* Оттенок один на все разделы — часть выбранной палитры «синяя плита»:
       цветные плитки на синем тонут или лезут вперёд. Цвет по блокам (--tint)
       остался мобильной шторке, у неё полотно светлое. */
    assert.match(tile.body, /background: var\(--otp-bar-tile,/, 'плитка красится не из палитры панели');
    assert.match(tile.body, /border-radius: 7px;/);
});

test('цвет плитки по блокам остался мобильной шторке', () => {
    /* Проход по списку в App.jsx раздаёт цвет по БЛОКАМ, и этим цветом
       красится шторка телефона. Круг из шести оттенков через nth-of-type там
       считал СТРОКИ, из-за чего цвета блоков не совпадали ни с чем; проход
       считает именно блоки. На компьютере плитки одноцветные — палитра
       «синяя плита», — но механизм общий, и ломать его нельзя. */
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

test('тёмная тема — графит, а не синяя плита', () => {
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
    /* В темноте панель графитовая: синий нужен против светлой страницы, а
       тёмная страница и так другая поверхность. */
    const at = dark.indexOf('--otp-bar-bg:');
    assert.ok(at > 0, 'в тёмной теме нет полотна панели');
    const block = dark.slice(at, at + 700);
    assert.match(block, /--otp-bar-bg: #000000;/, 'в темноте полотно перестало быть чёрным');
    assert.match(block, /--otp-bar-card: #1c1c1e;/, 'в темноте карточки перестали быть графитовыми');
});

test('у панели своя палитра, у шторки своя', () => {
    /* Настольная панель — «синяя плита портала», мобильная шторка — светлый
       список настроек телефона. Разъехались они не по недосмотру: шторка
       занимает весь экран, ей не нужно отделяться от страницы. Поэтому и
       наборы токенов разные, и пересекаться им нельзя — иначе правка одного
       экрана молча перекрашивает другой. */
    const bar = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar');
    assert.ok(bar, 'не найдено правило полотна сайдбара');
    assert.match(bar.body, /background-color: var\(--otp-bar-bg,/);
    assert.match(bar.body, /border-right: 1px solid var\(--otp-bar-edge,/);
    assert.match(bar.body, /background-image: none !important;/, 'синий градиент из разметки обязан гаситься');
    assert.match(bar.body, /color: var\(--otp-bar-ink,/);

    // Весь набор объявлен в светлой теме и переопределён в тёмной.
    const TOKENS = [
        '--otp-bar-bg', '--otp-bar-edge', '--otp-bar-card', '--otp-bar-ink',
        '--otp-bar-muted', '--otp-bar-sep', '--otp-bar-hover', '--otp-bar-tile',
        '--otp-bar-select', '--otp-bar-select-ink', '--otp-bar-danger',
        '--otp-bar-danger-wash', '--otp-bar-thumb',
    ];
    for (const token of TOKENS) {
        assert.ok(styles.includes(`${token}:`), `${token} не задан в светлой теме`);
        assert.ok(dark.includes(`${token}:`), `${token} не переопределён для тёмной темы`);
    }
    // Ни один настольный токен не протёк в шторку и наоборот.
    assert.ok(!/--otp-bar-/.test(shell), 'палитра настольной панели протекла в мобильную шторку');
    const desktop = rules(styles).filter((r) => r.selector.startsWith('body:not(.mobile-shell)'));
    for (const rule of desktop) {
        assert.ok(
            !/var\(--sheet-/.test(rule.body),
            `«${rule.selector}» красится палитрой шторки — правка телефона перекрасит панель`,
        );
    }
    for (const token of ['--sheet-bg', '--sheet-card', '--sheet-sep', '--sheet-active']) {
        assert.ok(shell.includes(`${token}:`), `${token} пропал из палитры шторки`);
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

test('в свёрнутом рельсе шапка — один колокол', () => {
    /* Решение владельца 09.09.2026: «в завернутом режиме лучше оставить
       колокол и сделать его в таком же размере как остальные блоки, а при
       раскрытии уже отображать лого и поиск как есть». Поэтому в рельсе
       логотип и кнопка поиска гаснут, а колокол становится карточкой ростом
       со строку раздела. Прячем прозрачностью, а не display: display не
       анимируется, и всё это должно проявляться плавно. */
    const RAIL = 'body:not(.mobile-shell) .sidebar.collapsed:not(:hover):not(:has(.sidebar-holds-open))';
    const railSearch = rules(styles).find((r) => r.selector === `${RAIL} .sidebar-search-btn`);
    assert.ok(railSearch, 'кнопка поиска в рельсе не спрятана — там остаётся только колокол');
    assert.match(railSearch.body, /opacity: 0;/);
    assert.match(railSearch.body, /visibility: hidden;/);
    const railLogo = rules(styles).find((r) => r.selector === `${RAIL} .sidebar-logo-full`);
    assert.ok(railLogo, 'логотип в рельсе не спрятан');
    assert.match(railLogo.body, /opacity: 0;/);
    for (const rule of [railSearch, railLogo]) {
        assert.ok(
            !/display: none/.test(rule.body),
            `«${rule.selector}» прячет через display — проявиться плавно уже не сможет`,
        );
    }
    // Поиск при этом никуда не делся: он разворачивает панель и закрывается сам.
    assert.ok(
        app.includes('if (!showSidebarSearch) setSidebarCollapsed(false);'),
        'поиск обязан разворачивать сайдбар — в рельсе его кнопки не видно',
    );
    assert.ok(
        app.includes('if ((sidebarCollapsed || isMobileShell) && showSidebarSearch) handleToggleSidebarSearch();'),
        'обратный ход потерян: свернули — поиск должен закрыться',
    );
});

test('колокол переезжает и меняет размер, а на ходу не принимает клик', () => {
    /* Место и габариты задаются одной парой свойств (left + width): при
       заданных сразу left и right блок над-задан, right игнорируется, и
       анимация шла бы рывком. 246 = 300 − 24 − 30 — те же 24 px запаса от
       кромки, где висит кнопка сворачивания. */
    const slot = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-bell-slot');
    assert.ok(slot, 'нет правила слота колокола');
    assert.match(slot.body, /position: absolute;/);
    for (const prop of ['left 0.3s ease', 'top 0.3s ease', 'width 0.3s ease', 'height 0.3s ease']) {
        assert.ok(slot.body.includes(prop), `в переезде колокола потерялось «${prop}»`);
    }
    assert.ok(!/right:/.test(slot.body), 'слот колокола задан и слева, и справа — анимация размера сорвётся');
    const open = { left: Number(/left: (\d+)px/.exec(slot.body)[1]), width: Number(/width: (\d+)px/.exec(slot.body)[1]) };
    assert.equal(open.left + open.width, 276, 'колокол у развёрнутой панели должен кончаться за 24 px до кромки');

    const RAIL = 'body:not(.mobile-shell) .sidebar.collapsed:not(:hover):not(:has(.sidebar-holds-open))';
    const railSlot = rules(styles).find((r) => r.selector === `${RAIL} .sidebar-bell-slot`);
    assert.ok(railSlot, 'нет положения колокола в рельсе');
    const railW = Number(/width: (\d+)px/.exec(railSlot.body)[1]);
    const railH = Number(/height: (\d+)px/.exec(railSlot.body)[1]);
    const railLeft = Number(/left: (\d+)px/.exec(railSlot.body)[1]);
    // Тот же размер, что у строки раздела: полоса 80 минус поля по 10.
    assert.equal(railLeft, 10, `колокол в рельсе на ${railLeft}px — не по краю карточек`);
    assert.equal(railW, 60, `колокол в рельсе шириной ${railW} — карточка раздела 60`);
    /* Высота шапки ОДНА на оба состояния: место под логотип держится всегда.
       Иначе при наведении логотип раздвигал шапку и уводил весь список вниз. */
    assert.ok(
        !rules(styles).some((r) => r.selector === `${RAIL} .sidebar-top-row` && /height:/.test(r.body)),
        'в рельсе у шапки снова своя высота — при наведении список поедет вниз',
    );
    const row = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-top-row');
    const rowH = Number(/height: (\d+)px/.exec(row.body)[1]);
    assert.equal(rowH, 54, `шапка ${rowH}px — под логотип нужно 54`);
    assert.ok(rowH >= railH, `колокол (${railH}px) выше шапки (${rowH}px)`);
    const railTop = Number(/top: (\d+)px/.exec(railSlot.body)[1]);
    assert.equal(railTop * 2 + railH, rowH, `колокол в рельсе стоит не по центру шапки: ${railTop} + ${railH} + ${railTop} ≠ ${rowH}`);

    // Плитка значка: 18 px у развёрнутой панели, 26 в рельсе — как у разделов.
    const tile = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-bell-slot > div > button > svg:first-child');
    assert.ok(tile, 'нет правила значка колокола');
    assert.match(tile.body, /width: 18px !important;/);
    assert.match(tile.body, /transition:[^;]*width 0\.3s/, 'значок меняет размер рывком');
    const railTile = rules(styles).find((r) => r.selector === `${RAIL} .sidebar-bell-slot > div > button > svg:first-child`);
    assert.ok(railTile, 'нет плитки колокола в рельсе');
    assert.match(railTile.body, /width: 26px !important;/);

    /* Пока колокол едет, он не принимает клик: курсор идёт к кнопке
       сворачивания у правой кромки, а колокол проезжает мимо этой точки — и
       панель уведомлений открывалась «сама». */
    assert.ok(styles.includes('@keyframes otp-bell-arm'), 'потеряна пауза на клик по едущему колоколу');
    const arm = rules(styles).find((r) => /\.sidebar\.collapsed:hover \.sidebar-bell-slot/.test(r.selector));
    assert.ok(arm && /animation: otp-bell-arm 0\.35s step-end;/.test(arm.body), 'пауза не навешена на переезд');
    assert.ok(
        /from \{ pointer-events: none; \}/.test(styles),
        'пауза должна снимать pointer-events, иначе клик по едущему колоколу останется',
    );

    // Строка заголовка растянута на всю шапку и обязана не принимать клики:
    // она лежит НАД кнопкой сворачивания (та absolute и стоит раньше).
    const logo = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-top-row > h1');
    assert.ok(logo, 'нет правила строки заголовка');
    assert.match(logo.body, /pointer-events: none;/);
    assert.match(logo.body, /overflow: hidden;/, 'без обрезки полное написание вылезает за узкую панель');
    assert.match(logo.body, /height: 100%;/, 'заголовок обязан быть ростом с шапку, иначе торчит над списком');
    const logoBtn = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-top-row > h1 button');
    assert.ok(logoBtn && /pointer-events: auto;/.test(logoBtn.body), 'вход в «4 You» внутри логотипа перестал кликаться');
});

test('кромки списка ничем не прикрыты', () => {
    /* Здесь стояли липкие полоски цвета полотна — сначала градиентом, потом
       ровной прозрачной полосой. На синей плите владелец их забраковал: полоса
       читалась тёмным пятном поверх первой строки. Сторожим, что их не
       вернули молча, и заодно что маску по-прежнему не берут: маска на списке
       обрежет выпадающие панели, они внутри строк на position: fixed. */
    for (const which of ['::before', '::after']) {
        const edge = rules(styles).find((r) => r.selector.includes(`.sidebar-menu-scroll${which}`));
        assert.ok(!edge, `вернулась полоска ${which} на кромке списка`);
    }
    const code = styles.replace(/\/\*[\s\S]*?\*\//g, '');
    assert.ok(
        !/mask-image|-webkit-mask/.test(code),
        'маска на списке обрежет выпадающие панели: они внутри строк на position: fixed',
    );
});

test('кнопка сворачивания непрозрачна', () => {
    /* Она висит на -right-4, то есть половиной лежит уже на странице. С цветом
       карточки (на синей плите это белый на 10%) её просвечивало насквозь. */
    const knob = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-collapse-btn');
    assert.ok(knob, 'нет правила кнопки сворачивания');
    assert.match(knob.body, /background: var\(--otp-bar-knob,/, 'кнопке вернули полупрозрачный цвет карточки');
    for (const token of ['--otp-bar-knob:', '--otp-bar-knob-ink:']) {
        assert.ok(styles.includes(token), `${token} не задан в светлой теме`);
        assert.ok(dark.includes(token), `${token} не переопределён для тёмной темы`);
    }
    /* Значение обязано быть непрозрачным: rgba с альфой ниже единицы —
       ровно тот дефект, из-за которого правило и переписано. */
    const value = /--otp-bar-knob: ([^;]+);/.exec(styles)[1];
    assert.ok(/^#[0-9a-f]{6}$/i.test(value.trim()), `цвет кнопки «${value}» не непрозрачный`);
});

test('на компьютере знак «4 You» в шапке не показывается', () => {
    /* В рельсе шапка это колокол, а у развёрнутой панели — полное написание.
       Знак остался только мобильной шторке, поэтому на компьютере он выключен
       совсем: иначе его невидимая кнопка ловила бы клики поверх логотипа. */
    const mini = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar .sidebar-logo-mini');
    assert.ok(mini, 'нет правила знака «4 You» для компьютера');
    assert.match(mini.body, /display: none !important;/);
    assert.ok(
        !/sidebar-logo-mini > button/.test(styles),
        'плитка знака осталась в стилях, хотя сам знак на компьютере выключен',
    );
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
    assert.match(on.body, /background: var\(--otp-bar-select,[^;]*\) !important;/);
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

test('логотип отсчитывается от верха шапки', () => {
    /* Логотип вынут из потока (он гаснет прозрачностью), а высота шапки у
       двух состояний разная: 54 px у развёрнутой панели и 42 в рельсе. С
       top: 50% он уезжал на середину этой высоты — прямо на кнопку. Отсчёт
       идёт от заголовка, у него же и обрезка. */
    const logo = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar .sidebar-logo-full');
    assert.ok(logo, 'не найдено правило логотипа');
    assert.match(logo.body, /position: absolute;/);
    assert.match(logo.body, /top: 0;/, 'логотип снова отсчитывается от середины шапки');
    assert.ok(!/top: 50%/.test(logo.body), 'top: 50% уводит логотип на кнопки шапки');
    assert.match(logo.body, /left: 0;/, 'логотип обязан вставать по краю заголовка, а тот уже отступает на 10 px');
    assert.match(logo.body, /height: 54px;/, 'у логотипа нет своей полосы — он поедет по высоте шапки');
    /* Проявляется ПОЗЖЕ, чем начинает разъезжаться панель: написание в 169 px
       иначе выглядывает за её кромку. */
    /* Проявляется он мягко и почти без задержки: за кромку узкой панели
       написание всё равно не вылезет — заголовок его обрезает. */
    const fade = /transition: opacity (0\.\d+)s ease( (0\.\d+)s)?/.exec(logo.body);
    assert.ok(fade, 'у логотипа нет перехода прозрачности');
    assert.ok(Number(fade[1]) >= 0.22, `логотип проявляется за ${fade[1]}с — слишком резко`);
});

test('карточка футера не обрезает меню аккаунта', () => {
    /* Меню «Аккаунта» раскрывается ВБОК — absolute left-full внутри своей
       строки, — и overflow: hidden на карточке футера съедал его целиком:
       панель открывалась, но её не было видно. Скругление дают сами строки. */
    const card = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-footer-menu');
    assert.ok(card, 'нет правила карточки футера');
    assert.ok(
        !/overflow:/.test(card.body),
        'на карточке футера снова обрезка — меню «Аккаунта» пропадёт целиком',
    );
    const first = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-footer-menu > li:first-child > button');
    const last = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-footer-menu > li:last-child > button');
    assert.ok(first && /border-top-left-radius/.test(first.body), 'верх карточки футера перестал скругляться');
    assert.ok(last && /border-bottom-left-radius/.test(last.body), 'низ карточки футера перестал скругляться');
});

test('кнопка поиска проявляется мягко и на ходу', () => {
    /* Раньше она проявлялась на месте, коротким фейдом и с задержкой — то
       есть выскакивала на готовой панели. Теперь едет из-под колокола и
       гаснет-проявляется втрое дольше; за кромку узкой панели при этом не
       выглядывает, потому что стартует слева. */
    const btn = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-search-btn');
    assert.ok(btn, 'нет правила кнопки поиска');
    const fade = /transition:[^;]*opacity (0\.\d+)s/.exec(btn.body);
    assert.ok(fade, 'у кнопки поиска нет перехода прозрачности');
    assert.ok(Number(fade[1]) >= 0.28, `поиск проявляется за ${fade[1]}с — слишком резко`);
    assert.match(btn.body, /transition:[^;]*left 0\.3s/, 'поиск больше не едет, а появляется на месте');
    const RAIL = 'body:not(.mobile-shell) .sidebar.collapsed:not(:hover):not(:has(.sidebar-holds-open))';
    const rail = rules(styles).find((r) => r.selector === `${RAIL} .sidebar-search-btn`);
    assert.ok(rail, 'нет положения кнопки поиска в рельсе');
    const left = Number(/left: (\d+)px/.exec(rail.body)?.[1]);
    assert.equal(left, 10, `поиск стартует с ${left}px — из-под колокола он должен выезжать с 10`);
});

test('счётчик уведомлений не уходит под кнопку сворачивания', () => {
    /* Кнопка сворачивания висит на absolute -right-4 и лежит выше по слою
       (z-index: 2) — то есть ровно в том углу, где счётчик. В рельсе он
       пропадал под ней целиком, поэтому там счётчик уезжает внутрь карточки,
       на угол самой плитки, а слой поднят у него в любом состоянии. */
    const badge = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar .sidebar-top-row .sidebar-surveys-collapsed-badge');
    assert.ok(badge, 'нет правила счётчика в шапке');
    const z = Number(/z-index: (\d+);/.exec(badge.body)?.[1]);
    const knob = rules(styles).find((r) => r.selector === 'body:not(.mobile-shell) .sidebar-collapse-btn');
    const knobZ = Number(/z-index: (\d+);/.exec(knob.body)?.[1]);
    assert.ok(Number.isFinite(z) && z > knobZ, `счётчик на слое ${z}, кнопка сворачивания на ${knobZ}`);

    const RAIL = 'body:not(.mobile-shell) .sidebar.collapsed:not(:hover):not(:has(.sidebar-holds-open))';
    const railBadge = rules(styles).find((r) => r.selector === `${RAIL} .sidebar-top-row .sidebar-surveys-collapsed-badge`);
    assert.ok(railBadge, 'нет положения счётчика в рельсе');
    const right = Number(/right: (\d+)px/.exec(railBadge.body)?.[1]);
    /* Правый край счётчика обязан остаться левее кнопки сворачивания: полоса
       80, карточка 10…70, кнопка от 64. */
    const badgeRight = 70 - right;
    assert.ok(badgeRight <= 64, `правый край счётчика на ${badgeRight}px — кнопка сворачивания начинается с 64`);
});
