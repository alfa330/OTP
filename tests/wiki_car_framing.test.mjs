import assert from 'node:assert/strict';
import test from 'node:test';

import {
    readFrame, normalizeAzimuth, azimuthGap, azimuthError, targetAzimuth,
    BODY_RANGE, INSIDE_RANGE,
} from '../src/components/wiki/trainers/carFraming.js';

/* Сцена присылает сюда три числа, а человек получает ответ «принято» или
 * «переснимай». Проверять это мышкой по кругу — то же самое, что проверять
 * калькулятор счётами, поэтому правила зафиксированы здесь.
 */

test('азимут нормализуется в обе стороны', () => {
    // Камеру крутят пальцем без ограничителя: за третий круг набегает 1000°,
    // а после разворота влево — минус.
    assert.equal(normalizeAzimuth(0), 0);
    assert.equal(normalizeAzimuth(360), 0);
    assert.equal(normalizeAzimuth(-90), 270);
    assert.equal(normalizeAzimuth(1000), 280);
});

test('расстояние между азимутами считается по короткой дуге', () => {
    assert.equal(azimuthGap(10, 350), 20, 'через ноль дуга короткая, а не 340°');
    assert.equal(azimuthGap(0, 180), 180);
});

test('четыре стороны кузова узнаются по азимуту', () => {
    const at = (azimuth) => readFrame({ azimuth, distance: 5 }, {}).view;
    assert.equal(at(0), 'rear');
    assert.equal(at(90), 'right');
    assert.equal(at(180), 'front');
    assert.equal(at(270), 'left');
    assert.equal(at(359), 'rear', 'через ноль корма остаётся кормой');
});

test('между секторами кадра нет — это «три четверти», их заворачивают', () => {
    // 45° — ровно между кормой и правым бортом: ни то, ни другое.
    assert.equal(readFrame({ azimuth: 45, distance: 5 }, {}).view, null);
    assert.equal(readFrame({ azimuth: 135, distance: 5 }, {}).view, null);
});

test('борт строже носа: три четверти сбоку не проходят', () => {
    // Перед держится с 26°, борт — с 22°: боковое фото под углом превращается
    // в три четверти быстрее, чем фронтальное.
    assert.equal(readFrame({ azimuth: 180 - 25, distance: 5 }, {}).view, 'front');
    assert.equal(readFrame({ azimuth: 270 - 25, distance: 5 }, {}).view, null);
});

test('дальность делится на близко, норму и далеко', () => {
    const framing = (distance) => readFrame({ azimuth: 180, distance }, {}).framing;
    assert.equal(framing(BODY_RANGE.near - 0.5), 'close');
    assert.equal(framing(5), 'ok');
    assert.equal(framing(BODY_RANGE.far + 0.5), 'far');
});

test('салон виден только через открытую дверь и только вблизи', () => {
    const closed = readFrame({ azimuth: 292, distance: 2.4 }, {});
    assert.equal(closed.view, 'left', 'дверь закрыта — это просто левый борт');

    const opened = readFrame({ azimuth: 292, distance: 2.4 }, { doorFrontLeft: true });
    assert.equal(opened.view, 'inside_front');

    const faraway = readFrame({ azimuth: 292, distance: 6 }, { doorFrontLeft: true });
    assert.equal(faraway.view, 'left', 'с шести метров в проём не снять — это борт');
});

test('открытая дверь перебивает борт, а не наоборот', () => {
    // Азимут 292° попадает и в сектор левого борта, и в сектор салона.
    // Человек, подошедший вплотную к раскрытой двери, снимает салон.
    const frame = readFrame({ azimuth: 292, distance: 2.0 }, { doorFrontLeft: true });
    assert.equal(frame.view, 'inside_front');
});

test('задняя дверь и багажник имеют свои сектора', () => {
    assert.equal(readFrame({ azimuth: 250, distance: 2.4 }, { doorRearLeft: true }).view,
        'inside_rear');
    assert.equal(readFrame({ azimuth: 0, distance: 3.4 }, { trunkOpen: true }).view, 'trunk');
    assert.equal(readFrame({ azimuth: 0, distance: 3.4 }, {}).view, 'rear',
        'багажник закрыт — кадр остаётся кормой');
});

test('вплотную к салону — тоже брак', () => {
    const frame = readFrame({ azimuth: 292, distance: INSIDE_RANGE.near - 0.2 },
        { doorFrontLeft: true });
    assert.equal(frame.view, 'inside_front');
    assert.equal(frame.framing, 'close', 'уткнувшись в сиденье, ряд целиком не снять');
});

test('подсказка знает, куда встать, и насколько промахнулись', () => {
    assert.equal(targetAzimuth('front'), 180);
    assert.equal(targetAzimuth('trunk'), 0);
    assert.equal(targetAzimuth('несуществующий'), null);
    assert.equal(azimuthError('front', 150), 30);
    assert.equal(azimuthError('rear', 350), 10, 'через ноль ошибка тоже короткая');
});
