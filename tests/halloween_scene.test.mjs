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
} from '../src/components/seasonal/halloween/halloweenWindow.js';
import {
    ART,
    loginLayout,
    makeBatFlock,
    nextBatDelay,
    nextGhostDelay,
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
    /* 390×844 и панель в 320 px: под карточкой 79 px вместо 90 — тыквы есть, но меньше. */
    const screen = { width: 390, height: 844 };
    const roomy = loginLayout({ ...screen, card: cardFor(screen) }).pumpkins;
    const tight = loginLayout({ ...screen, card: cardFor({ ...screen, bottomInset: 320 }), bottomInset: 320 }).pumpkins;
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

test('в работе мыши и привидение редки, на входе — живее', () => {
    for (const r of [0, 0.5, 0.999]) {
        const rand = () => r;
        const appBats = nextBatDelay(rand, { login: false });
        const loginBats = nextBatDelay(rand, { login: true });
        assert.ok(appBats >= 4 * 60000 && appBats <= 7 * 60000, `мыши в портале через ${appBats} мс`);
        assert.ok(loginBats >= 15000 && loginBats <= 27000);
        const ghost = nextGhostDelay(rand);
        assert.ok(ghost >= 10 * 60000 && ghost <= 15 * 60000, `привидение через ${ghost} мс`);
        assert.ok(nextSpiderDropDelay(rand, { login: false }) >= 38000);
    }
});

test('слой грузится отдельным чанком и только до срока', () => {
    const main = readFileSync(new URL('../src/main.jsx', import.meta.url), 'utf8');
    assert.match(main, /import \{ isHalloweenTime \} from '\.\/components\/seasonal\/halloween\/halloweenWindow';/);
    assert.match(main, /if \(isHalloweenTime\(\)\) \{/);
    assert.match(main, /import\('\.\/components\/seasonal\/halloween\/mountHalloween'\)/);
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
