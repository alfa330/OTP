// Хэллоуин-2026: сроки слоя украшений, раскладка экрана входа и стайки мышей.
//
// Слой временный и висит поверх рабочего портала, поэтому стережём три вещи:
// он гаснет ровно в полночь 2 ноября по Казахстану и не грузится после срока;
// на экране входа ни одно украшение не ложится на поля, кнопку «Войти» и
// ссылку под ней и не выходит за край экрана; мыши летят там, где им отвели.
//
// Запуск: node --test tests/halloween_scene.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    HALLOWEEN_ENDS_AT,
    isHalloweenTime,
    shouldMountHalloween,
    watchHalloweenEnd,
} from '../src/components/seasonal/halloween/halloweenWindow.js';
import {
    ART,
    appCorners,
    loginLayout,
    makeBatFlock,
    nextBatDelay,
    nextSpiderDropDelay,
} from '../src/components/seasonal/halloween/halloweenScene.js';

const at = (iso) => Date.parse(iso);

test('слой живёт до конца 1 ноября по Астане и гаснет в полночь', () => {
    assert.equal(HALLOWEEN_ENDS_AT, at('2026-11-01T19:00:00Z'));
    assert.equal(isHalloweenTime(at('2026-10-30T15:00:00+05:00')), true);
    assert.equal(isHalloweenTime(at('2026-10-31T23:00:00+05:00')), true);
    assert.equal(isHalloweenTime(at('2026-11-01T23:59:59+05:00')), true);
    assert.equal(isHalloweenTime(at('2026-11-01T23:59:59.999+05:00')), true);
    assert.equal(isHalloweenTime(at('2026-11-02T00:00:00+05:00')), false);
    assert.equal(isHalloweenTime(at('2026-11-15T12:00:00+05:00')), false);
    assert.equal(isHalloweenTime(at('2027-10-31T12:00:00+05:00')), false);
    // Тот же момент глазами другого пояса: в Москве ещё 22:00 1 ноября, но слой уже погас.
    assert.equal(isHalloweenTime(at('2026-11-01T22:00:00+03:00')), false);
    assert.equal(isHalloweenTime(Number.NaN), false);
});

test('окно «Чатов ОП» в CRM и чужой фрейм украшений не получают', () => {
    const now = at('2026-10-31T12:00:00+05:00');
    assert.equal(shouldMountHalloween({ now, search: '' }), true);
    assert.equal(shouldMountHalloween({ now, search: '?view=tasks' }), true);
    assert.equal(shouldMountHalloween({ now, search: '?embed=1&crm_user=5' }), false);
    assert.equal(shouldMountHalloween({ now, search: '', framed: true }), false);
    assert.equal(shouldMountHalloween({ now: at('2026-11-02T00:00:01+05:00'), search: '' }), false);
});

test('после полуночи прежний вид возвращается и без слоя украшений', (t) => {
    /* Метку праздника (сайдбар, вход) ставит main.jsx, и снимает её тоже он:
       слой может не подняться — сбой его чанка, вкладка открыта за секунды до
       полуночи, — и тогда праздник жил бы до перезагрузки (так было на стенде). */
    let clock = at('2026-11-01T23:59:00+05:00');
    t.mock.method(Date, 'now', () => clock);
    const timers = [];
    t.mock.method(globalThis, 'setTimeout', (fn, ms) => timers.push({ kind: 'timeout', fn, ms }));
    t.mock.method(globalThis, 'setInterval', (fn, ms) => timers.push({ kind: 'interval', fn, ms }));
    t.mock.method(globalThis, 'clearTimeout', () => {});
    t.mock.method(globalThis, 'clearInterval', () => {});
    let ended = 0;
    watchHalloweenEnd(() => { ended += 1; });
    const timeout = timers.find((timer) => timer.kind === 'timeout');
    const poll = timers.find((timer) => timer.kind === 'interval');
    assert.ok(timeout.ms >= 60_000 && timeout.ms <= 60_100, `таймер — до полуночи, а не ${timeout.ms} мс`);
    assert.equal(poll.ms, 60_000, 'и проверка раз в минуту');
    poll.fn();
    assert.equal(ended, 0, 'до полуночи праздник идёт');
    // Ноутбук проспал полночь: таймер ещё спит, минутная проверка уже видит срок.
    clock = at('2026-11-02T08:30:00+05:00');
    poll.fn();
    assert.equal(ended, 1);
    timeout.fn();
    poll.fn();
    assert.equal(ended, 1, 'конец — один раз');
    // Вкладку открыли уже после срока — конец сразу, без таймеров.
    timers.length = 0;
    watchHalloweenEnd(() => { ended += 1; });
    assert.equal(ended, 2);
    assert.equal(timers.length, 0);

    const main = read('../src/main.jsx');
    assert.match(main, /const halloweenHere = shouldMountHalloween\(\{ search: window\.location\.search, framed: isFramedWindow\(\) \}\);/);
    const start = main.indexOf('if (halloweenHere) {');
    const block = main.slice(start, main.indexOf('\n}', start));
    assert.match(block, /setAttribute\('data-otp-halloween', ''\);\n\s*watchHalloweenEnd\(\(\) => document\.documentElement\.removeAttribute\('data-otp-halloween'\)\);/);
    assert.match(block, /import\('\.\/components\/seasonal\/halloween\/halloween-theme\.css'\)/);
    assert.match(read('../src/components/seasonal/halloween/useHalloweenActive.js'), /return watchHalloweenEnd\(\(\) => setActive\(false\)\);/);
    assert.match(read('../src/components/seasonal/halloween/mountHalloween.jsx'), /framed: isFramedWindow\(\)/);
});

/* Карточка входа так, как её ставит .auth-screen: ширина max-w-[380px] плюс
   паддинги, по центру; высота — как на стенде (444 на компьютере, 398 на
   телефоне). bottomInset — панель установки, которая поднимает карточку. */
const cardFor = ({ width, height, bottomInset = 0 }) => {
    const compact = width < 640;
    const cardW = compact ? width - 32 : 448;
    const cardH = compact ? 398 : 444;
    const free = height - bottomInset;
    const left = (width - cardW) / 2;
    const top = Math.max(24, (free - cardH) / 2);
    return { left, top, right: left + cardW, bottom: top + cardH, width: cardW, height: cardH };
};

/* Где на карточке живут поля, кнопка и ссылка «Забыл пароль?» (разметка
   экрана входа в App.jsx): поля и кнопка — во всю ширину за вычетом px-6, от
   верхнего поля py-7 до низа кнопки; низ кнопки — 62 px над низом карточки
   (поле 28 + строка ссылки 20 + отступ mt-3.5). Ссылка — по центру, ~120 px.
   К кнопке и ссылке — запас 6 px. */
const controlsOf = (card) => [
    { left: card.left + 24, right: card.right - 24, top: card.top + 28, bottom: card.bottom - 62 + 6 },
    { left: (card.left + card.right) / 2 - 66, right: (card.left + card.right) / 2 + 66, top: card.bottom - 56, bottom: card.bottom - 22 },
];

/* Непрозрачные части картинки тыкв (замер по альфе pumpkins.webp): фонарь
   слева во всю высоту, хвостик над ним, маленькая тыква справа ниже. */
const pumpkinSolids = (p) => [
    { left: p.left + p.width * 0.02, right: p.left + p.width * 0.665, top: p.top + p.height * 0.12, bottom: p.top + p.height },
    { left: p.left + p.width * 0.32, right: p.left + p.width * 0.39, top: p.top, bottom: p.top + p.height * 0.13 },
    { left: p.left + p.width * 0.665, right: p.left + p.width, top: p.top + p.height * 0.43, bottom: p.top + p.height },
];

const boxOf = (item) => ({ left: item.left, right: item.left + item.width, top: item.top, bottom: item.top + item.height });
const intersects = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

const SCREENS = [
    { width: 390, height: 844 },
    { width: 375, height: 667 },
    { width: 360, height: 640 },
    { width: 430, height: 932 },
    { width: 768, height: 1024 },
    { width: 1280, height: 720 },
    { width: 1440, height: 900 },
    { width: 1920, height: 1080 },
];

for (const screen of SCREENS) {
    test(`экран входа ${screen.width}×${screen.height}: украшения не трогают форму и не выходят за край`, () => {
        const card = cardFor(screen);
        const layout = loginLayout({ ...screen, card });
        assert.ok(layout, 'раскладка есть');
        const controls = controlsOf(card);
        const inside = (box, label) => {
            assert.ok(box.left >= 0 && box.top >= 0, `${label}: за левым/верхним краем`);
            assert.ok(box.right <= screen.width && box.bottom <= screen.height, `${label}: за правым/нижним краем`);
        };

        if (layout.web) {
            const web = { left: 0, top: 0, right: layout.web.size, bottom: layout.web.size };
            inside(web, 'паутина');
            assert.equal(intersects(web, card), false, 'паутина легла на карточку');
        }
        if (layout.spider) {
            const s = layout.spider;
            const lowest = { left: s.left, right: s.left + s.width, top: s.top, bottom: s.top + s.height + s.drop + 4 };
            inside(lowest, 'паук в нижней точке спуска');
            assert.equal(intersects(lowest, card), false, 'паук спускается на карточку');
            assert.ok(s.anchor < s.top, 'нить идёт сверху вниз');
        }
        if (layout.ghost) {
            const ghost = boxOf(layout.ghost);
            /* Качание ±7 px и тень на 26 px ниже картинки (.hw-ghost__shadow). */
            const swing = { ...ghost, top: ghost.top - 8, bottom: ghost.bottom + 26 };
            inside(swing, 'привидение с тенью');
            assert.equal(intersects(swing, card), false, 'привидение легло на карточку');
            if (layout.spider) {
                const s = layout.spider;
                const spiderBox = { left: s.left, right: s.left + s.width, top: 0, bottom: s.top + s.height + s.drop };
                assert.equal(intersects(swing, spiderBox), false, 'привидение и паук наехали друг на друга');
            }
        }
        assert.ok(layout.pumpkins, 'тыквы на экране входа есть всегда, когда хватает места');
        inside({ ...boxOf(layout.pumpkins), bottom: layout.pumpkins.top + layout.pumpkins.height + 9 }, 'тыквы с тенью');
        for (const solid of pumpkinSolids(layout.pumpkins)) {
            for (const zone of controls) {
                assert.equal(intersects(solid, zone), false, 'тыква закрывает поля, кнопку или ссылку');
            }
            assert.equal(intersects(solid, card), false, 'тыква заходит на карточку входа');
        }
        if (layout.bats) {
            assert.ok(layout.bats.bottom <= card.top, 'мыши летают над карточкой');
            assert.ok(layout.bats.bottom - layout.bats.top >= 70);
        }
    });
}

test('панель установки снизу: тыквы на неё не наезжают, а в тесноте мельчают', () => {
    for (const screen of [{ width: 390, height: 844 }, { width: 375, height: 667 }]) {
        for (const bottomInset of [180, 260, 300, 320]) {
            const card = cardFor({ ...screen, bottomInset });
            const layout = loginLayout({ ...screen, card, bottomInset });
            if (!layout.pumpkins) continue;
            const bottom = layout.pumpkins.top + layout.pumpkins.height;
            assert.ok(bottom <= screen.height - bottomInset - 8, `тыквы под панелью: ${bottom} > ${screen.height - bottomInset - 8}`);
            assert.ok(layout.pumpkins.width >= 104, 'тыквы мельче 104 px не показываем');
        }
    }
    /* 390×844 и панель в 260 px: под карточкой 75 px вместо 90 — тыквы есть, но меньше. */
    const screen = { width: 390, height: 844 };
    const roomy = loginLayout({ ...screen, card: cardFor(screen) }).pumpkins;
    const tight = loginLayout({ ...screen, card: cardFor({ ...screen, bottomInset: 260 }), bottomInset: 260 }).pumpkins;
    assert.ok(tight, 'в тесноте тыквы не пропадают');
    assert.ok(tight.width < roomy.width, `тыквы не уменьшились: ${tight.width} при ${roomy.width}`);
});

test('без карточки или окна раскладки нет', () => {
    assert.equal(loginLayout({ width: 1440, height: 900, card: null }), null);
    assert.equal(loginLayout({ width: 0, height: 900, card: cardFor({ width: 1440, height: 900 }) }), null);
});

/* Генератор с зерном — Mulberry32: одинаковый ряд на каждом прогоне. */
const seeded = (seed) => () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

test('стайка мышей: 2–3 мыши в одну сторону, в своей полосе, из-за края в край', () => {
    for (let seed = 1; seed <= 400; seed += 1) {
        const rand = seeded(seed);
        const zone = { zoneTop: 76, zoneBottom: 250, width: 1440, compact: false };
        const bats = makeBatFlock(rand, zone);
        assert.ok(bats.length >= 2 && bats.length <= 3, `стайка из ${bats.length}`);
        const direction = bats[0].leftToRight;
        for (const bat of bats) {
            const height = Math.round(bat.size / ART.bat);
            assert.equal(bat.leftToRight, direction, 'вся стайка летит в одну сторону');
            assert.ok(bat.y >= zone.zoneTop && bat.y + height <= zone.zoneBottom, `мышь вне полосы: ${bat.y}`);
            const [start, end] = direction ? [bat.from, bat.to] : [bat.to, bat.from];
            assert.ok(start <= -bat.size, 'вылетает из-за края');
            assert.ok(end >= zone.width, 'улетает за край');
            assert.ok(bat.duration >= 4 && bat.duration <= 12, `полёт ${bat.duration} с`);
            assert.ok(bat.flap >= 0.3 && bat.flap <= 0.6);
        }
    }
});

test('мыши не взлетают, если полоса слишком узкая', () => {
    assert.deepEqual(makeBatFlock(seeded(7), { zoneTop: 100, zoneBottom: 130, width: 1440 }), []);
    assert.deepEqual(makeBatFlock(seeded(7), { zoneTop: 100, zoneBottom: 300, width: 0 }), []);
});

test('в работе мыши редки, на входе — живее', () => {
    for (const r of [0, 0.5, 0.999]) {
        const rand = () => r;
        const appBats = nextBatDelay(rand, { login: false });
        const loginBats = nextBatDelay(rand, { login: true });
        assert.ok(appBats >= 4 * 60000 && appBats <= 7 * 60000, `мыши в портале через ${appBats} мс`);
        assert.ok(loginBats >= 15000 && loginBats <= 27000);
        assert.ok(nextSpiderDropDelay(rand, { login: false }) >= 38000);
    }
});

for (const screen of [{ width: 1280, height: 720 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
    test(`углы портала ${screen.width}×${screen.height}: паук в паутине, привидение у кромки, всё в окне`, () => {
        const orb = { left: screen.width - 80, top: screen.height - 152, right: screen.width - 24, bottom: screen.height - 96 };
        const c = appCorners({ ...screen, sidebar: 80, orb });
        assert.ok(c.webTop.size >= 110 && c.webTop.size <= 160);
        assert.equal(c.webBottom.left, 80, 'нижняя паутина — сразу за сайдбаром');
        // Паук висит внутри верхней паутины и в нижней точке спуска не уходит далеко под неё.
        const s = c.spider;
        const center = screen.width - s.right - s.width / 2;
        assert.ok(center > screen.width - c.webTop.size && center < screen.width, 'паук не под своей паутиной');
        assert.ok(s.anchor > 0 && s.anchor < s.top, 'нить идёт из паутины вниз');
        assert.ok(s.top + s.height + s.drop <= c.webTop.size + 60, 'паук спускается в рабочую область');
        assert.ok(screen.width - s.right - s.width >= 0);
        // Привидение выглядывает снизу и не заходит на шарик помощника.
        const g = c.ghost;
        assert.ok(g, 'привидение есть');
        assert.ok(g.peek > 0 && g.peek < g.width * 1.2);
        const ghostBox = { left: g.left, right: g.left + g.width, top: screen.height - g.peek, bottom: screen.height };
        const overlap = ghostBox.left < orb.right + 12 && orb.left - 12 < ghostBox.right
            && ghostBox.top < orb.bottom + 12 && orb.top - 12 < ghostBox.bottom;
        assert.equal(overlap, false, 'привидение на шарике помощника');
        assert.ok(g.left > 80 && g.left + g.width <= screen.width);
    });
}

test('шарик в правом нижнем углу — привидение левее него', () => {
    const screen = { width: 1440, height: 900 };
    const orb = { left: 1300, top: 820, right: 1356, bottom: 876 };
    const g = appCorners({ ...screen, sidebar: 80, orb }).ghost;
    assert.ok(g.left + g.width <= orb.left - 12, `привидение ${g.left}+${g.width} заходит на шарик ${orb.left}`);
});

test('«Чаты ОП», «Журнал оценок», «Аукцион смен»: справа ни паутины, ни паука', () => {
    const c = appCorners({ width: 1440, height: 900, sidebar: 80, busyRight: true });
    assert.equal(c.webTop, null);
    assert.equal(c.spider, null);
    assert.ok(c.webBottom, 'нижняя паутина у сайдбара остаётся');
});

test('узкое окно с раскрытым сайдбаром — привидению нет места', () => {
    const c = appCorners({ width: 600, height: 800, sidebar: 300, orb: { left: 520, top: 700, right: 576, bottom: 756 } });
    assert.equal(c.ghost, null);
    assert.equal(appCorners({ width: 0, height: 800 }), null);
});

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('слой грузится отдельным чанком и только до срока', () => {
    const main = read('../src/main.jsx');
    assert.match(main, /import \{\s*isFramedWindow,\s*isHalloweenTime,\s*shouldMountHalloween,\s*watchHalloweenEnd,\s*\} from '\.\/components\/seasonal\/halloween\/halloweenWindow';/);
    assert.match(main, /if \(halloweenHere && isHalloweenTime\(\)\) \{/);
    assert.match(main, /import\('\.\/components\/seasonal\/halloween\/mountHalloween'\)/);
    assert.match(main, /module\?\.mountHalloween\?\.\(\)/, 'погашенный сбой чанка отдаёт undefined — вызов без падения');
    assert.doesNotMatch(main, /import [^;]*from '\.\/components\/seasonal\/halloween\/(mountHalloween|HalloweenLayer)/,
        'статический импорт слоя затащил бы картинки и стили в основной бандл');
    const windowModule = readFileSync(new URL('../src/components/seasonal/halloween/halloweenWindow.js', import.meta.url), 'utf8');
    assert.doesNotMatch(windowModule, /^import /m, 'модуль сроков едет в основном бандле — без зависимостей');
});

test('метка на <html> — атрибут, а не класс корня слоя', () => {
    /* Класс .otp-halloween у корня — fixed 0×0. Тот же класс на <html>
       схлопывал всю страницу в точку (замер на стенде: #root шириной 0). */
    const layer = readFileSync(new URL('../src/components/seasonal/halloween/HalloweenLayer.jsx', import.meta.url), 'utf8');
    const css = readFileSync(new URL('../src/components/seasonal/halloween/halloween.css', import.meta.url), 'utf8');
    assert.match(layer, /setAttribute\('data-otp-halloween', ''\)/);
    assert.doesNotMatch(layer, /documentElement\.classList/);
    assert.doesNotMatch(css, /html\.otp-halloween/);
    assert.match(css, /\.otp-halloween \{\s*position: fixed;[^}]*width: 0;[^}]*height: 0;[^}]*z-index: 49;/);
});

test('сбой праздничного чанка не перезагружает портал', () => {
    /* staleBundleRecovery на vite:preloadError делает location.replace. Свой
       слушатель — на фазе перехвата, раньше него, и только для своих чанков. */
    const main = read('../src/main.jsx');
    const at = main.indexOf("window.addEventListener('vite:preloadError'");
    assert.ok(at > 0, 'нет слушателя vite:preloadError');
    const block = main.slice(at, main.indexOf('}, true);', at) + 9);
    assert.match(block, /HALLOWEEN_CHUNK_RE\.test/);
    assert.match(block, /event\.preventDefault\(\)/);
    assert.match(block, /event\.stopImmediatePropagation\(\)/);
    assert.match(block, /\}, true\);$/, 'слушатель обязан быть на фазе перехвата');
    for (const chunk of ['mountHalloween', 'PumpkinMascot', 'halloween-theme']) {
        assert.ok(/HALLOWEEN_CHUNK_RE = \/([^/]+)\//.exec(main)[1].includes(chunk), `чанк ${chunk} не прикрыт`);
    }
    // Метка и обрамление — до отрисовки, иначе логотип и сайдбар сменились бы на глазах.
    assert.ok(main.indexOf("setAttribute('data-otp-halloween', '')") < main.indexOf('createRoot('));
    assert.match(main, /import\('\.\/components\/seasonal\/halloween\/halloween-theme\.css'\)\.catch/);
});

test('разделы праздник не перекрашивает: тёмная тема по-прежнему личная', () => {
    /* Решение владельца 30.10: «стили самих разделов лучше не трогать». Тёмный
       слой — только для тех, кому он выдан, и main.jsx его не включает. */
    const util = read('../src/utils/darkTheme.js');
    assert.doesNotMatch(util, /isHalloweenTime|halloween/i);
    assert.match(util, /return localStorage\.getItem\(DARK_THEME_STORAGE_KEY\) === 'dark';/);
    assert.match(util, /return !!login && DARK_THEME_LOGINS\.includes\(login\);/);
    const main = read('../src/main.jsx');
    assert.doesNotMatch(main, /applyDarkTheme|readStoredDarkTheme/);
});

test('тыква в шарике — по выбранному облику, своим чанком, при сбое остаётся знак', () => {
    const orb = read('../src/components/assistant/Orb.jsx');
    assert.match(orb, /const wantsPumpkin = effect === PUMPKIN_APPEARANCE;/);
    assert.match(orb, /import\('\.\/pumpkin\/PumpkinMascot\.jsx'\)/);
    // В праздник (у всех) и после — у выбравших тыкву: грузится со стартом портала.
    assert.match(orb, /if \(typeof window !== 'undefined' && \(isHalloweenTime\(\) \|\| pumpkinChosenHere\(\)\)\) loadPumpkin\(\);/);
    assert.match(orb, /LoadedPumpkin = module\?\.default \|\| null;/);
    assert.match(orb, /: <IcoreAssistantMark /, 'без тыквы — прежний знак');
    assert.doesNotMatch(orb, /<Suspense|lazy\(/, 'Suspense показал бы знак кадр-другой перед тыквой');
    // Выбор облика: тыква в списке, превью — картинкой.
    const picker = read('../src/components/assistant/AssistantAppearancePicker.jsx');
    assert.match(picker, /ASSISTANT_APPEARANCES\.map/);
    assert.match(picker, /effect === PUMPKIN_APPEARANCE/);
});

test('слой не мешает работе: некликабелен, гаснет при окне, не встаёт на кнопки, снимается по сроку', () => {
    const layer = read('../src/components/seasonal/halloween/HalloweenLayer.jsx');
    const css = read('../src/components/seasonal/halloween/halloween.css');
    const mount = read('../src/components/seasonal/halloween/mountHalloween.jsx');
    assert.match(css, /\.otp-halloween \{[^}]*pointer-events: none;/);
    assert.match(css, /\.otp-halloween\.is-muted > \* \{\s*opacity: 0 !important;/);
    assert.match(layer, /otp-halloween\$\{screen\.modal \? ' is-muted' : ''\}/);
    assert.match(layer, /const modal = next\.mode !== 'none' && isModalOpen\(\);/);
    assert.match(layer, /controlsRef\.current\.spider = coversControl\(/);
    assert.match(layer, /controlsRef\.current\.ghost = coversControl\(/);
    assert.match(layer, /nextCorners\.spider\.blocked = controlsRef\.current\.spider;/);
    assert.match(layer, /nextCorners\.ghost\.blocked = controlsRef\.current\.ghost;/);
    assert.match(layer, /const remeasure = useCallback\(\(force = false\) => \{\n\s+if \(!isHalloweenTime\(\)\) \{\n\s+onExpire\(\);/);
    assert.match(layer, /return \(\) => root\.removeAttribute\('data-otp-halloween'\);/);
    assert.match(layer, /!screenRef\.current\.modal && !isMenuOpen\(\)/, 'мыши взлетают поверх окна или меню');
    assert.match(mount, /shouldMountHalloween\(\{ search: window\.location\.search, framed: isFramedWindow\(\) \}\)/);
    assert.match(read('../src/components/seasonal/halloween/halloweenWindow.js'), /return window\.self !== window\.top;/);
    assert.match(mount, /root\.unmount\(\);\n\s+host\.remove\(\);/);
});

test('праздничное обрамление — сайдбар, вход, логотип, колокол — и ни одного правила для разделов', () => {
    const css = read('../src/components/seasonal/halloween/halloween-theme.css');
    const selectors = css
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .split('{')
        .map((chunk) => chunk.split('}').pop().trim())
        .filter((sel) => sel && !sel.startsWith('@media'));
    assert.ok(selectors.length > 10);
    for (const sel of selectors) {
        assert.ok(sel.startsWith('html[data-otp-halloween]'), `правило без праздничной метки: ${sel}`);
        // Ни утилит Tailwind, ни раздела: только сайдбар, вход, логотип, бар телефона.
        assert.doesNotMatch(sel, /\.(bg|text|border|ring|from|to|via|fill|stroke)-/, `правило красит утилиту: ${sel}`);
        assert.doesNotMatch(sel, /main-content/, `правило красит раздел: ${sel}`);
        assert.match(sel, /body(:not\(\.mobile-shell\))?\s*$|\.sidebar|\.auth-screen|\.auth-card|\.mtb-item|\.mobile-bell-slot/, `правило вне обрамления: ${sel}`);
    }
    // Сайдбар — переменные на body: перекрывают обе темы без борьбы весов.
    // Только на компьютере: шторка телефона светлая со своей палитрой.
    assert.match(css, /html\[data-otp-halloween\] body:not\(\.mobile-shell\) \{[^}]*--otp-bar-bg: #0c0a09;[^}]*--otp-bar-select: #c2410c;/);
    assert.match(css, /html\[data-otp-halloween\] body \{\s*--mtb-accent: #ea580c;\s*\}/);
    // Вход: свои цвета, а не тёмный слой.
    assert.match(css, /\.auth-card button\[type='submit'\] \{\s*background-color: #ea580c;/);
    assert.match(css, /\.auth-card input:focus \{/);
    // Автозаполнение не оставляет светлой заплатки на ночной карточке.
    assert.match(css, /\.auth-card input:-webkit-autofill \{[^}]*-webkit-text-fill-color: #f5f5f4;[^}]*box-shadow: inset 0 0 0 100px #26212d;/);
    assert.doesNotMatch(css, /data-otp-theme/);
});

test('слой дёшев для слабого компьютера', () => {
    const layer = read('../src/components/seasonal/halloween/HalloweenLayer.jsx');
    const css = read('../src/components/seasonal/halloween/halloween.css');
    const pumpkinCss = read('../src/components/assistant/pumpkin/pumpkin-mascot.css');
    // Опрос только в видимой вкладке, кнопки под украшениями — раз в три секунды.
    assert.match(layer, /if \(document\.hidden && force !== true\) return;/);
    assert.match(layer, /tickRef\.current % CONTROL_CHECK_EVERY === 1/);
    // Уход от курсора — по прямоугольникам из опроса, без getBoundingClientRect на движении.
    const shy = layer.slice(layer.indexOf('/* Украшения уходят от курсора'), layer.indexOf('zoneRef.current = '));
    assert.doesNotMatch(shy, /getBoundingClientRect/);
    // Слабая машина: без пролётов, спусков и покачиваний.
    assert.match(layer, /if \(reduced \|\| LOW_END \|\| screen\.mode === 'none'\) return undefined;/);
    assert.match(layer, /if \(reduced \|\| LOW_END \|\| !hasSpider\) return undefined;/);
    assert.match(css, /\.otp-halloween\.is-lite \.hw-spider__bob,/);
    // Ни одного CSS-фильтра: свечение тыквы запечено в картинки.
    assert.doesNotMatch(css, /filter:/);
    assert.doesNotMatch(pumpkinCss, /filter:/);
    assert.match(pumpkinCss, /\.hw-mascot\.is-lite \.hw-mascot__breathe,/);
});
