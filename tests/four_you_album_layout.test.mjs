// «Альбом» 4 You: раскладка книги (albumLayout.js) — развороты, ходы листа,
// окно готовых страниц, смена режима, фон и подписи. Чистая логика без DOM.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    PHOTO_ASPECT, albumMetrics, clampPlace, folioBand, pageLayout, pageSide, photoOfPlace, placeBackground,
    placeCount, placeLabel, placeOfPhoto, placePages, spreadPages, stackWidths, turnPlan, windowPages,
} from '../src/components/four_you/albumLayout.js';

const MODES = ['spread', 'single'];
const same = (a, b) => Boolean(a) && Boolean(b) && a.kind === b.kind && a.index === b.index;
const has = (list, page) => list.some((item) => same(item, page));

test('широкий экран — разворот, узкий — одна страница, книга влезает в сцену', () => {
    const wide = albumMetrics(1338, 760);
    assert.equal(wide.mode, 'spread');
    assert.equal(wide.width, wide.pageWidth * 2);
    assert.ok(wide.left >= 0 && wide.left + wide.width <= 1338);
    assert.ok(wide.top >= 0 && wide.top + wide.height <= 760);

    const phone = albumMetrics(390, 640);
    assert.equal(phone.mode, 'single');
    assert.ok(phone.left >= 0 && phone.left + phone.width <= 390);
    assert.ok(phone.top + phone.height <= 640);

    // Низкое широкое окно: страница уже 280 — разворот не читается.
    assert.equal(albumMetrics(1600, 380).mode, 'single');

    // Низкий телефон боком (Android 360 dp, бар справа): сцена 696×136.
    // Нижняя граница страницы не выше сцены — книга не заходит под листание.
    for (const [w, h] of [[696, 136], [600, 90], [854, 160], [300, 120]]) {
        const m = albumMetrics(w, h);
        assert.ok(m.top >= 0 && m.top + m.height <= h, `${w}x${h}: ${JSON.stringify(m)}`);
        assert.ok(m.left >= 0 && m.left + m.width <= w, `${w}x${h}: ${JSON.stringify(m)}`);
        // И не сплющена: рамка фото и разметка рассчитаны на страницу 0.7.
        assert.ok(Math.abs(m.pageWidth / m.height - 0.7) < 0.02, `${w}x${h}: ${JSON.stringify(m)}`);
    }
});

test('рамка фото — пропорции карточки ленты и холста редактора', () => {
    for (const [w, h] of [[560, 800], [439, 627], [300, 429], [366, 523]]) {
        const { photo, caption } = pageLayout(w, h);
        assert.ok(Math.abs(photo.width / photo.height - PHOTO_ASPECT) < 0.01, `${w}x${h}`);
        assert.ok(photo.left >= 0 && photo.left + photo.width <= w, `${w}x${h}`);
        assert.ok(photo.top + photo.height <= h, `${w}x${h}`);
        // Подпись кончается над полосой номера страницы: номер не ложится на текст.
        assert.ok(caption.height > 0, `${w}x${h}: подписи нужно место`);
        assert.ok(caption.top + caption.height + folioBand(h) <= h, `${w}x${h}`);
    }
});

test('развороты: форзац, фото по порядку, задний форзац только для пары', () => {
    assert.deepEqual(spreadPages(0), [{ kind: 'front' }, { kind: 'back' }]);
    assert.deepEqual(spreadPages(1), [{ kind: 'front' }, { kind: 'photo', index: 0 }]);
    for (let count = 0; count < 12; count += 1) {
        const pages = spreadPages(count);
        assert.equal(pages.length % 2, 0, `count=${count}`);
        assert.deepEqual(pages.filter((p) => p.kind === 'photo').map((p) => p.index), [...Array(count).keys()]);
        assert.equal(pages.filter((p) => p.kind === 'back').length, count % 2 ? 0 : 1, `count=${count}`);
    }
});

test('всё, что назовёт ход листа, уже лежит готовым на столе', () => {
    for (const mode of MODES) {
        for (let count = 1; count < 12; count += 1) {
            for (let place = 0; place < placeCount(mode, count); place += 1) {
                const ready = windowPages(mode, place, count);
                for (const forward of [true, false]) {
                    const plan = turnPlan(mode, place, forward, count);
                    if (!plan) continue;
                    // Второе быстрое нажатие в ту же сторону: прошлый ход досчитан
                    // мгновенно, место уже новое, а страницы ещё те, что были.
                    const next = turnPlan(mode, plan.target, forward, count);
                    for (const [label, step] of [['ход', plan], ['второй ход', next]]) {
                        if (!step) continue;
                        for (const role of ['leaf', 'under', 'still', 'flap']) {
                            if (step[role] === null && mode === 'single' && role === 'still') continue;
                            const where = `${mode} count=${count} place=${place} ${forward ? '→' : '←'} ${label} ${role}`;
                            assert.ok(step[role], where);
                            assert.ok(has(ready, step[role]), where);
                        }
                    }
                }
            }
        }
    }
});

test('листать некуда — плана нет; ход ведёт ровно на соседнее место', () => {
    for (const mode of MODES) {
        for (let count = 1; count < 10; count += 1) {
            const total = placeCount(mode, count);
            assert.equal(turnPlan(mode, 0, false, count), null);
            assert.equal(turnPlan(mode, total - 1, true, count), null);
            for (let place = 0; place < total - 1; place += 1) {
                assert.equal(turnPlan(mode, place, true, count).target, place + 1);
                assert.equal(turnPlan(mode, place + 1, false, count).target, place);
            }
        }
    }
    assert.equal(turnPlan('spread', 0, true, 0), null);
});

test('ход назад — тот же лист, что и вперёд, только от конца к началу', () => {
    for (const mode of MODES) {
        for (let count = 1; count < 10; count += 1) {
            for (let place = 0; place < placeCount(mode, count) - 1; place += 1) {
                const there = turnPlan(mode, place, true, count);
                const back = turnPlan(mode, place + 1, false, count);
                assert.ok(same(there.leaf, back.leaf), `${mode} ${count} ${place}: лист`);
                assert.ok(same(there.flap, back.flap), `${mode} ${count} ${place}: оборот`);
                if (mode === 'spread') {
                    // Вперёд под листом открывается следующая правая, назад она
                    // лежит под листом; левая прошлого места не двигается ни там, ни там.
                    assert.ok(same(there.under, placePages(mode, place + 1, count)[1]));
                    assert.ok(same(back.under, placePages(mode, place + 1, count)[1]));
                    assert.ok(same(there.still, back.still));
                    assert.ok(same(there.still, placePages(mode, place, count)[0]));
                }
            }
        }
    }
});

test('на развороте страницы стоят по своим сторонам', () => {
    for (let count = 1; count < 10; count += 1) {
        for (let place = 0; place < placeCount('spread', count); place += 1) {
            const [left, right] = placePages('spread', place, count);
            assert.equal(pageSide('spread', left), 'left', `count=${count} place=${place}`);
            assert.equal(pageSide('spread', right), 'right', `count=${count} place=${place}`);
        }
    }
    assert.equal(pageSide('single', { kind: 'photo', index: 3 }), 'single');
});

test('смена режима не теряет фото: книга остаётся на том же снимке', () => {
    for (const mode of MODES) {
        for (let count = 1; count < 12; count += 1) {
            for (let index = 0; index < count; index += 1) {
                const place = placeOfPhoto(mode, index);
                assert.ok(place < placeCount(mode, count), `${mode} ${count} ${index}`);
                assert.ok(has(placePages(mode, place, count), { kind: 'photo', index }), `${mode} ${count} ${index}`);
            }
            for (let place = 0; place < placeCount(mode, count); place += 1) {
                const photo = Math.min(count - 1, photoOfPlace(mode, place));
                const other = mode === 'spread' ? 'single' : 'spread';
                const moved = placeOfPhoto(other, photo);
                assert.ok(has(placePages(other, moved, count), { kind: 'photo', index: photo }));
            }
        }
    }
});

test('место за краем книги прижимается к краю', () => {
    assert.equal(clampPlace('spread', 99, 5), placeCount('spread', 5) - 1);
    assert.equal(clampPlace('single', -3, 5), 0);
    assert.equal(clampPlace('single', 2, 0), 0);
    assert.deepEqual(placePages('single', 0, 0), []);
    assert.deepEqual(windowPages('spread', 0, 0), []);
});

test('фон сцены: правое фото главнее, без фона — левое', () => {
    const bg = (value) => ({ annotations: { background: value } });
    const images = [bg('hearts'), bg('stars'), bg('none'), {}, bg('aurora')];
    assert.equal(placeBackground('spread', 0, images), 'hearts');       // форзац + фото 1
    assert.equal(placeBackground('spread', 1, images), 'stars');        // фото 2 и 3: у правого нет
    assert.equal(placeBackground('spread', 2, images), 'aurora');       // фото 4 и 5
    assert.equal(placeBackground('single', 2, images), 'none');
    assert.equal(placeBackground('single', 4, images), 'aurora');
    assert.equal(placeBackground('spread', 0, []), 'none');
});

test('подпись места — номера фото', () => {
    assert.equal(placeLabel('spread', 0, 18), '1 из 18');
    assert.equal(placeLabel('spread', 1, 18), '2–3 из 18');
    assert.equal(placeLabel('spread', 9, 18), '18 из 18');
    assert.equal(placeLabel('spread', 8, 17), '16–17 из 17');
    assert.equal(placeLabel('single', 4, 18), '5 из 18');
    assert.equal(placeLabel('single', 0, 0), '');
});

test('стопки страниц: слева пролистанное, справа оставшееся', () => {
    assert.deepEqual(stackWidths(0, 10), { left: 2, right: 8 });
    assert.deepEqual(stackWidths(9, 10), { left: 8, right: 2 });
    assert.deepEqual(stackWidths(0, 1), { left: 2, right: 8 });
});
