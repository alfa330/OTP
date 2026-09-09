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
}));

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
        styles.includes(`.sidebar.collapsed:has(.notifications-dropdown) {\n      width: ${width[1]}px;\n    }`),
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
    for (const rule of rules(styles)) {
        if (!marks.some((m) => rule.selector.includes(m))) continue;
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
        /sidebarSearchQueryRef\.current = '';\s+setShowSidebarSearch\(\(prev\) => !prev\);/.test(app),
        'запрос не сбрасывается при переключении поиска',
    );
    // Свернули сайдбар — поиск закрывается: поля не видно, а фильтр остался бы.
    assert.ok(
        app.includes('if (sidebarCollapsed && showSidebarSearch) handleToggleSidebarSearch();'),
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
    assert.match(bar.body, /background-color: var\(--sheet-bg,/);
    assert.match(bar.body, /background-image: none !important;/, 'синий градиент из разметки обязан гаситься');
    assert.match(bar.body, /color: var\(--sheet-text,/);
    for (const token of ['--sheet-bg', '--sheet-card', '--sheet-sep', '--sheet-active']) {
        assert.ok(shell.includes(`${token}:`), `${token} пропал из палитры шторки — сайдбар останется без цвета`);
    }
});
