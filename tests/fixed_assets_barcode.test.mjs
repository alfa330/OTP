/*
 * Своё чтение штрихкода «Основных средств» (src/components/fixed_assets/barcodeScan.js): EAN-13 наклейки
 * бухгалтерии и Code 128 заводской этикетки — с синтетических «снимков»: мелкий модуль, размытие, шум,
 * наклон, перевёрнутая наклейка. Кодировщики — здесь, в тесте, независимо от декодера.
 *
 *     node --test tests/fixed_assets_barcode.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
    C128, C128_STOP, code128Text, decodeRuns, eanCheckOk, runsFromSamples, scanGray, scanStill,
} from '../src/components/fixed_assets/barcodeScan.js';

const L = [[3, 2, 1, 1], [2, 2, 2, 1], [2, 1, 2, 2], [1, 4, 1, 1], [1, 1, 3, 2],
    [1, 2, 3, 1], [1, 1, 1, 4], [1, 3, 1, 2], [1, 2, 1, 3], [3, 1, 1, 2]];
const FIRST = ['LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLG', 'LGLGLG', 'LGLGGL', 'LGGLGL'];
const bits = (widths, startBar) => widths.map((w, i) => ((i % 2 === 0) === startBar ? '1' : '0').repeat(w)).join('');

const ean13Modules = (code) => {
    const d = code.split('').map(Number);
    let out = '101';
    for (let i = 0; i < 6; i += 1) {
        const widths = FIRST[d[0]][i] === 'L' ? L[d[i + 1]] : [...L[d[i + 1]]].reverse();
        out += bits(widths, false);
    }
    out += '01010';
    for (let i = 7; i < 13; i += 1) out += bits(L[d[i]], true);
    return `${out}101`;
};

const code128Modules = (text) => {
    let values;
    if (/^(\d\d)+$/.test(text)) values = [105, ...text.match(/\d\d/g).map(Number)];
    else values = [104, ...text.split('').map((ch) => ch.charCodeAt(0) - 32)];
    let sum = values[0];
    values.slice(1).forEach((v, i) => { sum += v * (i + 1); });
    values.push(sum % 103);
    return values.map((v) => bits(C128[v], true)).join('') + bits(C128_STOP, true);
};

/* Модули → «снимок»: тёмные 40, светлые 215, поля по 12 модулей, модуль px пикселей; размытие,
   шум, наклон angle°, перевёрнутая наклейка flip. */
const render = (modules, { px = 3, angle = 0, blur = 1, noise = 12, height = 90, flip = false, seed = 7 } = {}) => {
    const full = '0'.repeat(12) + (flip ? [...modules].reverse().join('') : modules) + '0'.repeat(12);
    const lineWidth = Math.ceil(full.length * px);
    const width = lineWidth + 40;
    const total = height + Math.ceil(Math.abs(Math.tan((angle * Math.PI) / 180)) * width) + 20;
    const gray = new Uint8Array(width * total).fill(215);
    let state = seed;
    const random = () => { state = (state * 1103515245 + 12345) % 2147483648; return state / 2147483648; };
    const tan = Math.tan((angle * Math.PI) / 180);
    for (let y = 0; y < total; y += 1) {
        for (let x = 0; x < width; x += 1) {
            // Точка (x, y) в системе наклейки: сдвиг по наклону.
            const u = x - 20;
            const v = y - 10 - (x - width / 2) * tan - (total - height) / 2;
            let value = 215;
            if (v >= 0 && v < height && u >= 0 && u < lineWidth) {
                // Площадь пикселя под модулем — сглаживание как у настоящей камеры.
                const from = u / px;
                const to = (u + 1) / px;
                let dark = 0;
                for (let m = Math.floor(from); m < Math.ceil(to); m += 1) {
                    if (full[m] === '1') dark += Math.min(to, m + 1) - Math.max(from, m);
                }
                value = 215 - (175 * dark) / (to - from);
            }
            gray[y * width + x] = value;
        }
    }
    let out = gray;
    for (let pass = 0; pass < blur; pass += 1) {
        const next = new Uint8Array(out.length);
        for (let y = 0; y < total; y += 1) {
            for (let x = 0; x < width; x += 1) {
                const at = y * width + x;
                const left = out[at - (x > 0 ? 1 : 0)];
                const right = out[at + (x < width - 1 ? 1 : 0)];
                next[at] = (left + 2 * out[at] + right) / 4;
            }
        }
        out = next;
    }
    for (let i = 0; i < out.length; i += 1) out[i] = Math.max(0, Math.min(255, out[i] + (random() - 0.5) * 2 * noise));
    return { gray: out, width, height: total };
};

test('таблица Code 128 цела: 106 символов по 11 модулей, у каждого чётная сумма штрихов, все разные', () => {
    assert.equal(C128.length, 106);
    const seen = new Set();
    C128.forEach((pattern, value) => {
        assert.equal(pattern.length, 6, `символ ${value}`);
        assert.equal(pattern.reduce((a, b) => a + b, 0), 11, `символ ${value}`);
        assert.equal((pattern[0] + pattern[2] + pattern[4]) % 2, 0, `символ ${value}: сумма штрихов нечётная`);
        seen.add(pattern.join(''));
    });
    assert.equal(seen.size, 106);
    assert.equal(C128_STOP.reduce((a, b) => a + b, 0), 13);
});

test('контрольная цифра EAN', () => {
    assert.ok(eanCheckOk('2000000056784'));            // наклейка бухгалтерии
    assert.ok(eanCheckOk('4600702093001'));
    assert.ok(!eanCheckOk('2000000024218'));
    assert.ok(eanCheckOk('96385074'));
    assert.ok(!eanCheckOk('abc'));
});

test('Code 128: наборы A, B, C и переключения', () => {
    assert.equal(code128Text([20, 18, 35], 'C'), '201835');
    assert.equal(code128Text([43, 18, 21], 'B'), 'K25');
    assert.equal(code128Text([43, 99, 25, 3], 'B'), 'K2503');           // B → C
    assert.equal(code128Text([25, 100, 46], 'C'), '25N');               // C → B
});

const readImage = (image) => scanGray(image.gray, image.width, image.height);

test('EAN-13 наклейки читается: крупно, мелко, с наклоном, шумом и вверх ногами', () => {
    const modules = ean13Modules('2000000056784');
    for (const options of [{ px: 4 }, { px: 2.2 }, { px: 1.7, blur: 0 }, { px: 3, angle: 6 }, { px: 3, angle: -12 },
        { px: 3, noise: 30 }, { px: 3, flip: true }, { px: 2.6, blur: 2 },
        // Сильное размытие, как на снимке наклейки с телефона: узкий зазор не доходит до порога —
        // его находит второй способ, по перепадам.
        { px: 3, blur: 5 }, { px: 3.5, blur: 8, noise: 8 }]) {
        const found = readImage(render(modules, options));
        assert.deepEqual(found, { format: 'ean_13', text: '2000000056784' }, JSON.stringify(options));
    }
});

test('Code 128 заводской этикетки: серийный номер буквами и цифрами, чисто цифровой код', () => {
    for (const text of ['K2201N0012345', 'Book14-A7XZ', '2000000056784']) {
        for (const options of [{ px: 3 }, { px: 2.2 }, { px: 3, angle: 7, noise: 20 }, { px: 3, flip: true }]) {
            const found = readImage(render(code128Modules(text.length % 2 ? text : text), options));
            assert.deepEqual(found, { format: 'code_128', text }, `${text} ${JSON.stringify(options)}`);
        }
    }
});

test('пустой кадр и мусор ничего не находят', () => {
    const blank = { gray: new Uint8Array(400 * 120).fill(200), width: 400, height: 120 };
    assert.equal(readImage(blank), null);
    let state = 3;
    const noise = new Uint8Array(400 * 120).map(() => { state = (state * 1103515245 + 12345) % 2147483648; return state % 256; });
    assert.equal(scanGray(noise, 400, 120), null);
    // Половина наклейки (код обрезан краем кадра) — не читается, а не читается неверно.
    const modules = ean13Modules('2000000056784');
    assert.equal(readImage(render(modules.slice(0, 60), { px: 3 })), null);
});

test('ширины полос: светлое поле первым, тёмные — на нечётных местах', () => {
    const samples = [...new Array(20).fill(220), ...new Array(6).fill(30), ...new Array(6).fill(220),
        ...new Array(12).fill(30), ...new Array(20).fill(220)];
    const runs = runsFromSamples(samples);
    assert.ok(runs.length >= 5);
    assert.ok(Math.abs(runs[1] - 6) < 1.2 && Math.abs(runs[3] - 12) < 1.2, runs.join(','));
    assert.equal(decodeRuns(runs), null);
});

test('фото наклейки из файла: штрихкод у края кадра и снятый боком', () => {
    const sticker = render(ean13Modules('2000000001111'), { px: 3, height: 60 });
    const width = sticker.width + 300;
    const height = 700;
    const photo = new Uint8Array(width * height).fill(205);
    for (let y = 0; y < sticker.height; y += 1) {
        for (let x = 0; x < sticker.width; x += 1) photo[(y + 12) * width + x + 150] = sticker.gray[y * sticker.width + x];
    }
    assert.deepEqual(scanStill(photo, width, height), [{ format: 'ean_13', text: '2000000001111' }]);
    const turned = new Uint8Array(width * height);
    for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) turned[x * height + (height - 1 - y)] = photo[y * width + x];
    }
    assert.deepEqual(scanStill(turned, height, width), [{ format: 'ean_13', text: '2000000001111' }]);
});

test('на фото два штрихкода: крупная наклейка первой, мелкий заводской — за ней', () => {
    const big = render(ean13Modules('2000000056784'), { px: 3.2, height: 150 });
    const small = render(code128Modules('K2201N0012345'), { px: 2.2, height: 36 });
    const width = Math.max(big.width, small.width) + 80;
    const height = 520;
    const photo = new Uint8Array(width * height).fill(208);
    const paste = (image, top) => {
        for (let y = 0; y < image.height; y += 1) {
            for (let x = 0; x < image.width; x += 1) photo[(y + top) * width + x + 40] = image.gray[y * image.width + x];
        }
    };
    paste(small, 40);
    paste(big, 200);
    assert.deepEqual(scanStill(photo, width, height), [
        { format: 'ean_13', text: '2000000056784' },
        { format: 'code_128', text: 'K2201N0012345' },
    ]);
});

test('на фото заводская этикетка стоит прямо, а наклейка — боком: читаются обе', () => {
    const label = render(code128Modules('K2201N0012345'), { px: 2.6, height: 44 });
    const sticker = render(ean13Modules('2000000056784'), { px: 3, height: 120 });
    // Наклейку поворачиваем на 90°: ширина становится высотой.
    const side = { width: sticker.height, height: sticker.width, gray: new Uint8Array(sticker.gray.length) };
    for (let y = 0; y < sticker.height; y += 1) {
        for (let x = 0; x < sticker.width; x += 1) {
            side.gray[x * side.width + (side.width - 1 - y)] = sticker.gray[y * sticker.width + x];
        }
    }
    const width = Math.max(label.width, side.width) + 260;
    const height = label.height + side.height + 120;
    const photo = new Uint8Array(width * height).fill(206);
    const paste = (image, left, top) => {
        for (let y = 0; y < image.height; y += 1) {
            for (let x = 0; x < image.width; x += 1) photo[(y + top) * width + x + left] = image.gray[y * image.width + x];
        }
    };
    paste(label, 20, 20);
    paste(side, width - side.width - 40, label.height + 70);
    const texts = scanStill(photo, width, height).map((found) => found.text).sort();
    assert.deepEqual(texts, ['2000000056784', 'K2201N0012345']);
});

test('EAN-13 под сильным размытием читается у любого кода, а не у одного удачного', () => {
    // Размытый снимок — веб-камера ноутбука с постоянным фокусом у самой наклейки. Узкие полосы сливаются;
    // их возвращает проход резкости (SHARPEN_PASSES). Ни одного неверного прочтения быть не может.
    const codes = ['2000000056784', '2000000001012', '2000000001111', '2000000039275', '4601234567893',
        '4820001234565', '5901234123457', '2000000000008'];
    for (const code of codes) {
        assert.ok(eanCheckOk(code), code);
        for (const options of [{ px: 3, blur: 5 }, { px: 3.5, blur: 8, noise: 8 }]) {
            assert.deepEqual(readImage(render(ean13Modules(code), options)), { format: 'ean_13', text: code },
                `${code} ${JSON.stringify(options)}`);
        }
    }
});
