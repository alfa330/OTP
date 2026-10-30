/* Хэллоуин-2026: раскладка украшений и параметры их движения.
 *
 * Только чистые функции без DOM: где на экране входа встают тыквы, привидение
 * и паук, как летит стайка летучих мышей и как часто что появляется. Их
 * проверяет tests/halloween_scene.test.mjs; сам слой — HalloweenLayer.jsx,
 * сроки — halloweenWindow.js.
 */

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/* Размеры картинок (по файлам в assets): ширина / высота. */
export const ART = {
    pumpkins: 440 / 265,
    ghost: 216 / 240,
    spider: 120 / 90,
    bat: 128 / 76,
};

/* Раскладка экрана входа. На вход — окно и прямоугольник карточки входа
 * (getBoundingClientRect), на выход — места украшений или null, если места
 * нет. Правила:
 *   - паутина в левом верхнем углу экрана, паук свисает с неё;
 *   - тыквы у левого нижнего угла карточки: на компьютере — снаружи слева,
 *     на телефоне — под карточкой, заходя на её угол не выше нижнего поля
 *     (кнопка «Войти» и ссылка под ней не закрываются никогда);
 *   - привидение справа от карточки, а если справа тесно — над ней;
 *   - мыши летают в полосе над карточкой.
 * Ничего не выходит за окно и не залезает на панель установки снизу.
 */
/* topInset — верхнее поле экрана входа вместе с вырезом «чёлки» (в
   установленном портале под ним часы), bottomInset — панель установки. */
export const loginLayout = ({ width, height, card, bottomInset = 0, topInset = 0 }) => {
    if (!card || !(width > 0) || !(height > 0)) return null;
    const compact = width < 640;
    const floor = height - bottomInset - 8;

    const webSize = Math.round(clamp(Math.min(width, height) * 0.22, 96, 200));
    let web = { left: 0, top: 0, size: webSize };

    const spiderW = compact ? 34 : 44;
    const spiderH = Math.round(spiderW / ART.spider);
    const spiderDrop = compact ? 26 : 44;
    const spiderX = Math.round(webSize * 0.6);
    const anchorY = Math.round(webSize * 0.36);
    let spiderTop = webSize + (compact ? 34 : 56);
    const spiderOverCard = spiderX + spiderW > card.left - 12;
    if (spiderOverCard) {
        spiderTop = Math.min(spiderTop, card.top - spiderH - spiderDrop - 14);
    }
    const spider = spiderTop - anchorY >= 24
        ? { left: spiderX, anchor: anchorY, top: spiderTop, width: spiderW, height: spiderH, drop: spiderDrop }
        : null;

    /* Сбоку от карточки (компьютер) — а если слева тесно (телефон, планшет),
       под её левым углом: заходят на угол не выше нижнего поля карточки. */
    let pumpkins = null;
    if (!compact) {
        const w = Math.round(clamp(card.width * 0.5, 168, 204));
        const h = Math.round(w / ART.pumpkins);
        const left = Math.round(card.left - w + 34);
        const top = Math.round(card.bottom - h + 20);
        if (left >= 8 && top + h <= floor) pumpkins = { left, top, width: w, height: h };
    }
    if (!pumpkins) {
        const top = Math.round(card.bottom - 24);
        /* В тесноте (панель установки снизу) тыквы мельчают, но не меньше 104 px. */
        const w = Math.min(Math.round(clamp(card.width * 0.42, 124, 168)), Math.floor((floor - top) * ART.pumpkins));
        if (w >= 104) pumpkins = { left: Math.round(card.left + 2), top, width: w, height: Math.round(w / ART.pumpkins) };
    }

    let ghost = null;
    const ghostH = compact ? 78 : 108;
    const ghostW = Math.round(ghostH * ART.ghost);
    const roomRight = width - card.right;
    if (roomRight >= ghostW + 48) {
        ghost = {
            left: Math.round(card.right + Math.min(96, (roomRight - ghostW) / 2)),
            top: Math.round(card.top + Math.min(64, card.height * 0.18)),
            width: ghostW,
            height: ghostH,
        };
    } else {
        const top = Math.round(card.top - ghostH - 38);
        const left = Math.round(Math.min(card.right - ghostW - 10, width - ghostW - 12));
        const clearOfSpider = !spider || left > spider.left + spider.width + 24;
        /* +10: привидение качается на 7 px вверх и не должно уходить за край. */
        if (top >= topInset + 10 && clearOfSpider) ghost = { left, top, width: ghostW, height: ghostH };
    }

    const batsTop = topInset + 6;
    const batsBottom = card.top - 22;
    const bats = batsBottom - batsTop >= 70 ? { top: batsTop, bottom: batsBottom } : null;

    if (card.top < web.size * 0.9 && card.left < web.size * 0.9) web = null;

    return { compact, web, spider, pumpkins, ghost, bats };
};

/* Стайка летучих мышей: 2–3 штуки летят через экран в одну сторону, каждая
 * своим ходом — разный размер, задержка, высота волны и такт взмахов. rand —
 * генератор [0, 1) (в тестах — свой, с зерном). */
export const makeBatFlock = (rand, { zoneTop, zoneBottom, width, compact = false }) => {
    if (!(zoneBottom - zoneTop >= 40) || !(width > 0)) return [];
    const count = 2 + Math.floor(rand() * 2);
    const leftToRight = rand() < 0.5;
    const base = compact ? 34 : 46;
    const crossing = (compact ? 5.2 : 7.4) + rand() * 1.8;
    const lane = zoneTop + rand() * Math.max(0, zoneBottom - zoneTop - base);
    const bats = [];
    for (let i = 0; i < count; i += 1) {
        const size = Math.round(base * (0.7 + rand() * 0.42));
        const height = Math.round(size / ART.bat);
        const y = Math.round(clamp(lane + (rand() - 0.5) * 56 + i * 10, zoneTop, zoneBottom - height));
        bats.push({
            key: i,
            size,
            y,
            from: leftToRight ? -size - 24 : width + 24,
            to: leftToRight ? width + 24 : -size - 24,
            leftToRight,
            delay: Number((i * (0.3 + rand() * 0.55)).toFixed(2)),
            duration: Number((crossing * (0.9 + rand() * 0.2)).toFixed(2)),
            wave: Math.round(8 + rand() * 16),
            waveDuration: Number((1.0 + rand() * 0.8).toFixed(2)),
            flap: Number((0.36 + rand() * 0.14).toFixed(2)),
            flapDelay: Number((rand() * 0.4).toFixed(2)),
        });
    }
    return bats;
};

/* Паузы между появлениями, мс. На экране входа живее, в работе — редко:
   портал рабочий, движение на краю взгляда не должно отвлекать. */
export const nextBatDelay = (rand, { login }) => (login ? 15000 + rand() * 12000 : 240000 + rand() * 180000);
export const nextGhostDelay = (rand) => 600000 + rand() * 300000;
export const nextSpiderDropDelay = (rand, { login }) => (login ? 9000 + rand() * 7000 : 38000 + rand() * 26000);
