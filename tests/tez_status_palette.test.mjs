import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';


/*
 * Задача #416 (Алчинбаева Анель, Тез КЦ, 12.11.2026): «цветовое разграничение статусов
 * операторов на протяжении смены. Синий — «Активный», оранжевый — «Перерыв», остальные
 * статусы — отдельными цветами. Сейчас статус видно только при наведении курсора».
 *
 * В общей палитре у Тез КЦ три рабочих статуса телефона («Готов», «Занят», «Перезвон»)
 * горели одним голубым, а «Исход» и «Перерыв» — почти одним оранжевым. Палитра своя
 * только у Тез КЦ: у СЗоВ и ОП те же слова, и их цвета задача не трогает.
 *
 * Словари живут внутри App.jsx, поэтому тест вынимает из исходника настоящие функции
 * (как icore_phone_status_keys.test.mjs), а набор статусов телефона Тез — из каталога
 * табло на сервере: новый статус там без своего цвета здесь не пройдёт.
 */

const APP = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
const BOT = readFileSync(new URL('../bot_schedule2.py', import.meta.url), 'utf8');

function slice(source, start, end) {
    const from = source.indexOf(start);
    assert.notEqual(from, -1, `нет начала: ${start}`);
    const to = source.indexOf(end, from + start.length);
    assert.notEqual(to, -1, `нет конца: ${end}`);
    return source.slice(from, to);
}

const keys = slice(APP, 'const plannerStatusPad2 = (n) =>', 'const plannerStatusNormalizeOperatorName = (v) =>');
const tone = slice(APP, 'const getPlannerImportedStatusTone = (', 'const PLANNER_IMPORTED_WORK_STATUS_KEYS = new Set([');

const { plannerStatusLabelFromKey, getPlannerImportedStatusTone, plannerStatusLegendRank } = new Function(`
    ${keys}
    ${tone}
    return { plannerStatusLabelFromKey, getPlannerImportedStatusTone, plannerStatusLegendRank };
`)();

/* Ключи статусов телефона Тез — каталог табло (_TEZ_WALLBOARD_STATUS_CATALOG). Каталог
   общий с табло ОП, поэтому в нём есть и «Автодозвон» — статус групп ОП
   (oktell_guard/phone.py): у Тез КЦ его нет, автообзвон там и есть «Активный». */
const catalog = slice(BOT, '_TEZ_WALLBOARD_STATUS_CATALOG = {', '\n}');
const TEZ_PHONE_KEYS = [...catalog.matchAll(/^\s+'([^']+)':\s*\(/gm)].map((m) => m[1]);
const OP_ONLY_KEYS = new Set(['автодозвон']);

/* Тон по кругу 0–360 и насыщенность — чтобы проверять «синий» и «оранжевый» словами
   постановки, а не конкретной краской. */
function hsl(hex) {
    const n = parseInt(hex.replace('#', ''), 16);
    const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => v / 255);
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    const d = max - min;
    if (d === 0) return { h: 0, s: 0, l };
    const s = d / (1 - Math.abs(2 * l - 1));
    let h;
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    return { h: (h * 60 + 360) % 360, s, l };
}

/* Различимость на глаз — CIEDE2000 (ΔE): разные строки ещё не значат разные цвета.
   Около 2 — разница едва заметна, от 10 — видна сразу. */
function lab(hex) {
    const n = parseInt(hex.replace('#', ''), 16);
    const lin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => lin(v / 255));
    const x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047;
    const y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    const z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
    const f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
    return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
}
function deltaE(hexA, hexB) {
    const rad = Math.PI / 180;
    const [L1, a1, b1] = lab(hexA);
    const [L2, a2, b2] = lab(hexB);
    const Cb = (Math.hypot(a1, b1) + Math.hypot(a2, b2)) / 2;
    const G = 0.5 * (1 - Math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)));
    const a1p = (1 + G) * a1;
    const a2p = (1 + G) * a2;
    const C1p = Math.hypot(a1p, b1);
    const C2p = Math.hypot(a2p, b2);
    const h1p = ((Math.atan2(b1, a1p) / rad) + 360) % 360;
    const h2p = ((Math.atan2(b2, a2p) / rad) + 360) % 360;
    let dhp = h2p - h1p;
    if (C1p * C2p === 0) dhp = 0;
    else if (dhp > 180) dhp -= 360;
    else if (dhp < -180) dhp += 360;
    const dLp = L2 - L1;
    const dCp = C2p - C1p;
    const dHp = 2 * Math.sqrt(C1p * C2p) * Math.sin((dhp / 2) * rad);
    const Lbp = (L1 + L2) / 2;
    const Cbp = (C1p + C2p) / 2;
    let hbp = Math.abs(h1p - h2p) <= 180 ? (h1p + h2p) / 2 : (h1p + h2p + 360) / 2;
    if (C1p * C2p === 0) hbp = h1p + h2p;
    const T = 1 - 0.17 * Math.cos((hbp - 30) * rad) + 0.24 * Math.cos(2 * hbp * rad)
        + 0.32 * Math.cos((3 * hbp + 6) * rad) - 0.2 * Math.cos((4 * hbp - 63) * rad);
    const dth = 30 * Math.exp(-(((hbp - 275) / 25) ** 2));
    const Rc = 2 * Math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7));
    const Sl = 1 + (0.015 * (Lbp - 50) ** 2) / Math.sqrt(20 + (Lbp - 50) ** 2);
    const Sc = 1 + 0.045 * Cbp;
    const Sh = 1 + 0.015 * Cbp * T;
    const Rt = -Math.sin(2 * dth * rad) * Rc;
    return Math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh));
}

/* Перерывы ПО ГРАФИКУ дорожка «гр» рисует градиентом #fde68a → #f59e0b, «Мои смены» —
   жёлтым: тёплый цвет рядом с полосой статусов уже значит «перерыв». */
const PLANNED_BREAK_COLORS = ['#fde68a', '#f59e0b', '#facc15'];

const tez = (status) => getPlannerImportedStatusTone(status, 'tez');

test('каталог телефона Тез прочитан с сервера', () => {
    for (const key of ['готов', 'занят', 'перезвон', 'перерыв', 'тренинг', 'тех причина', 'офлайн', 'выключен']) {
        assert.ok(TEZ_PHONE_KEYS.includes(key), key);
    }
});

test('«Активный» (в графиках — «Готов») синий, «Перерыв» оранжевый — как просили', () => {
    for (const status of ['готов', 'Готов']) {
        const { h, s } = hsl(tez(status).bar);
        // Синий, а не прежний голубой (#0ea5e9 — тон 199°).
        assert.ok(h >= 215 && h <= 245 && s > 0.6, `«${status}»: тон ${h.toFixed(0)}° — не синий`);
        assert.match(tez(status).chip, /blue/);
    }
    for (const status of ['перерыв', 'Перерыв']) {
        const { h, s } = hsl(tez(status).bar);
        // Оранжевый, а не прежний янтарный (#f59e0b — тон 38°).
        assert.ok(h >= 15 && h <= 32 && s > 0.6, `«${status}»: тон ${h.toFixed(0)}° — не оранжевый`);
        assert.match(tez(status).chip, /orange/);
        assert.match(tez(status).row, /orange/);
    }
    // Дни до 11.09.2026 у Тез — из выгрузки Binotel: её «Активен» — тот же «Активный».
    for (const status of ['Активен', 'active']) assert.deepEqual(tez(status), tez('Готов'), status);
});

test('у каждого статуса телефона Тез свой цвет — и по ключу, и по подписи в графиках', () => {
    const fallback = getPlannerImportedStatusTone('что-то незнакомое', 'tez').bar;
    const byStatus = new Map();
    for (const key of TEZ_PHONE_KEYS.filter((k) => !OP_ONLY_KEYS.has(k))) {
        const byKey = tez(key);
        const byLabel = tez(plannerStatusLabelFromKey(key));
        assert.notEqual(byKey.bar, fallback, `${key}: серый цвет по умолчанию`);
        assert.deepEqual(byLabel, byKey, `${key}: по подписи «${plannerStatusLabelFromKey(key)}» цвет другой`);
        // «Перезвон» и «Исход» — один статус телефона («Исход»): цвет общий.
        byStatus.set(key === 'исход' ? 'перезвон' : key, byKey.bar);
    }
    const entries = [...byStatus.entries()];
    assert.equal(new Set(entries.map(([, bar]) => bar)).size, entries.length, 'два статуса слились по цвету');
    // Различимы на глаз: любые два — не ближе, чем синий «Готов» к серому «Выключен»
    // (пара задана постановкой и общей палитрой), а цветные между собой — ещё дальше.
    const floor = deltaE(tez('готов').bar, tez('выключен').bar);
    assert.ok(floor >= 13, `«Готов» и «Выключен» почти одного цвета: ΔE ${floor.toFixed(1)}`);
    for (let i = 0; i < entries.length; i += 1) {
        for (let j = i + 1; j < entries.length; j += 1) {
            const [a, barA] = entries[i];
            const [b, barB] = entries[j];
            const gap = deltaE(barA, barB);
            const need = [a, b].includes('выключен') ? floor : 18;
            assert.ok(gap >= need - 1e-9, `«${a}» и «${b}» почти одного цвета: ΔE ${gap.toFixed(1)} < ${need.toFixed(1)}`);
        }
    }
});

test('три рабочих статуса, сливавшихся в голубой, и пара «Исход» / «Перерыв» разведены', () => {
    const work = ['Готов', 'Занят', 'Перезвон'].map((label) => tez(label).bar);
    assert.equal(new Set(work).size, 3);
    for (const [a, b] of [['Готов', 'Занят'], ['Готов', 'Перезвон'], ['Занят', 'Перезвон'], ['Перезвон', 'Перерыв'], ['Исход', 'Перерыв']]) {
        assert.ok(deltaE(tez(a).bar, tez(b).bar) >= 18, `${a} / ${b}`);
    }
});

test('тёплый цвет в полосе — только у перерыва: остальное не похоже на перерыв по графику', () => {
    // Перерыв и должен быть «как по графику»: оранжевый рядом с янтарным планом.
    assert.ok(Math.min(...PLANNED_BREAK_COLORS.map((c) => deltaE(tez('Перерыв').bar, c))) < 18);
    for (const key of TEZ_PHONE_KEYS.filter((k) => !OP_ONLY_KEYS.has(k) && k !== 'перерыв')) {
        const nearest = Math.min(...PLANNED_BREAK_COLORS.map((c) => deltaE(tez(key).bar, c)));
        assert.ok(nearest >= 20, `«${key}» похож на перерыв по графику: ΔE ${nearest.toFixed(1)}`);
    }
});

/* Красно-зелёная слепота (Machado, Oliveira, Fernandes 2009, полная степень): матрицы
   переводят линейный sRGB в то, что видит протан и дейтеран. Проверяем пары, которые у Тез
   стоят на полосе РЯДОМ: автообзвон (Готов → Соединение → Занят), исход, перерыв, выброс
   в «Офлайн» после разговора, длинные тренинг и тех. пауза. */
const CVD = {
    протанопия: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
    дейтеранопия: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.01182, 0.04294, 0.968881]],
};
function simulate(hex, matrix) {
    const n = parseInt(hex.replace('#', ''), 16);
    const lin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const enc = (v) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);
    const rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => lin(v / 255));
    const out = matrix.map((row) => Math.min(1, Math.max(0, row[0] * rgb[0] + row[1] * rgb[1] + row[2] * rgb[2])));
    return `#${out.map((v) => Math.round(enc(v) * 255).toString(16).padStart(2, '0')).join('')}`;
}
const NEIGHBOURS = [
    ['Готов', 'Соединение'], ['Соединение', 'Занят'], ['Занят', 'Готов'], ['Перезвон', 'Соединение'],
    ['Перезвон', 'Занят'], ['Перерыв', 'Готов'], ['Перерыв', 'Занят'], ['Офлайн', 'Занят'], ['Офлайн', 'Готов'],
    ['Тренинг', 'Готов'], ['Тренинг', 'Соединение'], ['Тренинг', 'Тех причина'],
];

test('соседние на полосе статусы различимы и при красно-зелёной слепоте', () => {
    for (const [name, matrix] of Object.entries(CVD)) {
        for (const [a, b] of NEIGHBOURS) {
            const gap = deltaE(simulate(tez(a).bar, matrix), simulate(tez(b).bar, matrix));
            assert.ok(gap >= 7.5, `${name}: «${a}» и «${b}» сливаются, ΔE ${gap.toFixed(1)}`);
        }
    }
});

test('у остальных отделов палитра прежняя: отдел меняет цвета только у Тез КЦ', () => {
    const statuses = [
        ...TEZ_PHONE_KEYS, 'Готов', 'Занят', 'Перезвон', 'Перерыв', 'Тех причина', 'Тренинг',
        'вышел', 'без телефона', 'отключен', 'нет на месте', 'зарезервировано',
        'online', 'busy', 'holiday', 'break', 'study', 'login', 'logout', 'take chat',
        'active', 'work in crm', 'break in work', 'inactive', 'что-то незнакомое',
    ];
    for (const status of statuses) {
        const general = getPlannerImportedStatusTone(status);
        for (const code of ['', 'szov', 'op', 'front_office', null, undefined]) {
            assert.deepEqual(getPlannerImportedStatusTone(status, code), general, `${status} у отдела «${code}»`);
        }
    }
    // Те же цвета, что до задачи: СЗоВ и ОП видят полосу как раньше.
    assert.equal(getPlannerImportedStatusTone('Готов').bar, '#0ea5e9');
    assert.equal(getPlannerImportedStatusTone('Занят').bar, '#0ea5e9');
    assert.equal(getPlannerImportedStatusTone('Перезвон').bar, '#0ea5e9');
    assert.equal(getPlannerImportedStatusTone('Перерыв').bar, '#f59e0b');
    assert.equal(getPlannerImportedStatusTone('Исход').bar, '#fb923c');
});

test('код отдела читается без учёта регистра и пробелов', () => {
    for (const code of ['TEZ', ' tez ', 'Tez']) {
        assert.deepEqual(getPlannerImportedStatusTone('Готов', code), tez('Готов'));
    }
});

test('статусы, которые и так отличались, у Тез КЦ — общими цветами', () => {
    for (const status of ['Офлайн', 'Выключен']) {
        assert.deepEqual(tez(status), getPlannerImportedStatusTone(status), status);
    }
});

test('расшифровка цветов идёт в постоянном порядке: линия, паузы, выход, незнакомое — в конце', () => {
    const labels = ['Выключен', 'Незнакомый', 'Перерыв', 'Тех. причина', 'Перезвон', 'Соединение', 'Занят', 'Готов', 'Офлайн', 'Тренинг'];
    const sorted = [...labels].sort((a, b) => plannerStatusLegendRank(a) - plannerStatusLegendRank(b));
    assert.deepEqual(sorted, ['Готов', 'Занят', 'Соединение', 'Перезвон', 'Тренинг', 'Тех. причина', 'Перерыв', 'Офлайн', 'Выключен', 'Незнакомый']);
});

/* ── Проводка: цвет везде берётся по отделу оператора ─────────────────────── */

/* Вызовы функции цвета: аргументы верхнего уровня, без пробелов. Вызов в комментарии —
   не вызов; запятая или скобка в строке внутри аргумента не сбивает разбор. */
function skipQuoted(source, k) {
    const quote = source[k];
    k += 1;
    while (k < source.length && source[k] !== quote) {
        if (source[k] === '\\') k += 2;
        else if (quote === '`' && source.startsWith('${', k)) k = skipBraces(source, k + 1);
        else k += 1;
    }
    return k + 1;
}
function skipBraces(source, k) {
    let depth = 0;
    while (k < source.length) {
        const c = source[k];
        if (c === "'" || c === '"' || c === '`') { k = skipQuoted(source, k); continue; }
        if (c === '{') depth += 1;
        else if (c === '}' && --depth === 0) return k + 1;
        k += 1;
    }
    return k;
}
function toneCalls(source) {
    const marker = 'getPlannerImportedStatusTone';
    const calls = [];
    for (let i = source.indexOf(marker); i !== -1; i = source.indexOf(marker, i + marker.length)) {
        if (/[\w$]/.test(source[i - 1] || '') || source.slice(Math.max(0, i - 6), i) === 'const ') continue;
        if (source.lastIndexOf('/*', i) > source.lastIndexOf('*/', i)) continue;
        if (source.slice(source.lastIndexOf('\n', i) + 1, i).includes('//')) continue;
        let j = i + marker.length;
        if (source.startsWith('?.', j)) j += 2;
        if (source[j] !== '(') continue;
        const args = [];
        let depth = 1;
        let k = j + 1;
        let from = k;
        while (k < source.length && depth > 0) {
            const c = source[k];
            if (c === "'" || c === '"' || c === '`') { k = skipQuoted(source, k); continue; }
            if (c === '(' || c === '[' || c === '{') depth += 1;
            else if (c === ')' || c === ']' || c === '}') {
                depth -= 1;
                if (depth === 0) args.push(source.slice(from, k));
            } else if (c === ',' && depth === 1) { args.push(source.slice(from, k)); from = k + 1; }
            k += 1;
        }
        calls.push(args.map((a) => a.replace(/\s+/g, '')));
    }
    return calls;
}

/* Что вправе стоять вторым аргументом: отдел строки сетки (её оператор в каждом окне),
   отдел самого оператора в «Моих сменах» и отдел строки в расшифровке. Отдел из шапки,
   пустое значение или чужой объект здесь — дефект: строка другого отдела получит чужие
   цвета, а Тез КЦ — общие. */
const DEPARTMENT_ARGS = new Map([
    ['plannerStatusPaletteFor(op)', 'полоса «ст»'],
    ['plannerStatusPaletteFor(journalOp)', 'журнал статусов'],
    ['plannerStatusPaletteFor(modalPreviewOp)', 'окно смены'],
    ['plannerStatusPaletteFor(matchedPlannerOperator)', 'окно аномалий'],
    ['plannerStatusPaletteFor(operatorsById.get(Number(item?.operatorId)))', 'окно группировки'],
    ['plannerOwnDepartmentCode', '«Мои смены»'],
    ['paletteCode', 'расшифровка'],
]);

test('каждый вызов цвета статуса получает отдел оператора своей строки', () => {
    const calls = toneCalls(APP);
    assert.ok(calls.length >= 10, `вызовов меньше, чем мест рисования: ${calls.length}`);
    for (const args of calls) {
        assert.equal(args.length, 2, `вызов без отдела: getPlannerImportedStatusTone(${args.join(', ')})`);
        assert.ok(DEPARTMENT_ARGS.has(args[1]), `отдел не той строки: getPlannerImportedStatusTone(${args.join(', ')})`);
    }
    // Каждое место рисования на месте: ни одно не выпало и не подменено соседним.
    const used = new Set(calls.map((args) => args[1]));
    for (const [arg, where] of DEPARTMENT_ARGS) assert.ok(used.has(arg), `${where}: нет вызова с ${arg}`);
});

test('разбор вызовов не путается в комментариях и строках', () => {
    const sample = [
        '// было getPlannerImportedStatusTone(label) — без отдела',
        "const a = getPlannerImportedStatusTone(x || '(', plannerStatusPaletteFor(op));",
        '/* getPlannerImportedStatusTone(y) */',
        'const b = getPlannerImportedStatusTone?.(z);',
    ].join('\n');
    assert.deepEqual(toneCalls(sample), [["x||'('", 'plannerStatusPaletteFor(op)'], ['z']]);
});

/* Настоящий код раздела, вынутый из App.jsx: хуки React заменены прямым вызовом. */
const paletteFor = (options, scope) => new Function('plannerDepartmentOptions', 'plannerScopeDepartmentCode', `
    const useMemo = (fn) => fn();
    const useCallback = (fn) => fn;
    ${slice(APP, 'const plannerDepartmentCodeById = useMemo(', '// Фронт офисы не используют Oktell/Binotel/Chat2Desk')}
    return plannerStatusPaletteFor;
`)(options, scope);

test('отдел строки — её department_id, отдел шапки — только когда отдела у строки нет', () => {
    // value — строкой, как собирает plannerDepartmentOptions; department_id в ответе — число.
    const options = [{ value: '70', label: 'Тез КЦ', code: 'tez' }, { value: '1', label: 'СЗоВ', code: 'szov' }];
    for (const scope of ['', 'szov', 'tez']) {
        assert.equal(paletteFor(options, scope)({ department_id: 70 }), 'tez', `шапка «${scope}»`);
        assert.equal(paletteFor(options, scope)({ department_id: '70' }), 'tez', `шапка «${scope}», id строкой`);
    }
    assert.equal(paletteFor(options, 'tez')({ department_id: 1 }), 'szov', 'строка СЗоВ в шапке Тез — цвета СЗоВ');
    assert.equal(paletteFor(options, 'tez')({ department_id: 999 }), 'tez', 'отдел строки неизвестен — отдел шапки');
    assert.equal(paletteFor(options, '')({}), '', 'ни отдела строки, ни шапки — общая палитра');
    assert.equal(paletteFor(options, 'tez')(undefined), 'tez');
});

const operatorNames = slice(APP, 'const plannerStatusNormalizeOperatorName = (v) =>', 'const PLANNER_CHAT2DESK_STATUS_EVENT_MAP = {');
const legendFor = (env) => new Function(
    'getPlannerImportedStatusTone', 'plannerStatusLegendRank', 'env', `
    const useMemo = (fn) => fn();
    ${operatorNames}
    const { plannerStatusSpecialDayViewEnabled, isTezPlanner, visibleRange, filteredOperators,
        importedStatusTimelineByOperatorDateKey, plannerStatusPaletteFor, plannerScopeDepartmentCode = '' } = env;
    ${slice(APP, 'const plannerStatusLegendItems = useMemo(() => {', 'const getImportedStatusBarsForShiftContext = useCallback(')}
    return plannerStatusLegendItems;
`)(getPlannerImportedStatusTone, plannerStatusLegendRank, env);

const LEGEND_DAY = '2026-11-11';
const timeline = (rows) => new Map(rows.map(([name, labels]) => [
    `${name.toLowerCase()}|${LEGEND_DAY}`,
    labels.map((stateName, n) => ({ stateName, stateKey: stateName.toLowerCase(), startMin: n * 10, endMin: n * 10 + 5 })),
]));
const legendEnv = (extra = {}) => ({
    plannerStatusSpecialDayViewEnabled: true,
    isTezPlanner: true,
    visibleRange: [LEGEND_DAY],
    filteredOperators: [{ name: 'Первая', department_id: 70 }, { name: 'Вторая', department_id: 70 }],
    importedStatusTimelineByOperatorDateKey: timeline([
        ['Первая', ['Выключен', 'Готов', 'Соединение', 'Занят', 'Перерыв', 'Готов', 'Тех причина', 'Выключен']],
        ['Вторая', ['Выключен', 'Перезвон', 'Занят', 'Исход', 'Офлайн', 'Тренинг', 'Незнакомый']],
    ]),
    plannerStatusPaletteFor: () => 'tez',
    ...extra,
});

test('расшифровка: статусы всех показанных операторов, один цвет — один пункт, порядок постоянный', () => {
    const items = legendFor(legendEnv());
    assert.deepEqual(items.map((item) => item.label), [
        'Готов', 'Занят', 'Соединение', 'Перезвон / Исход', 'Тренинг', 'Тех причина', 'Перерыв', 'Офлайн', 'Выключен', 'Незнакомый',
    ]);
    // Квадратик — ровно тот цвет, что у полосы строки Тез.
    for (const item of items) assert.equal(item.bar, tez(item.label.split(' / ')[0]).bar, item.label);
    assert.equal(new Set(items.map((item) => item.bar)).size, items.length, 'два пункта одного цвета');
});

test('расшифровки нет вне режима статусов по дням и у других отделов', () => {
    assert.deepEqual(legendFor(legendEnv({ plannerStatusSpecialDayViewEnabled: false })), []);
    assert.deepEqual(legendFor(legendEnv({ isTezPlanner: false })), []);
    assert.deepEqual(legendFor(legendEnv({ visibleRange: [] })), []);
});

test('расшифровка — в пустом углу над колонкой ФИО, «Мои смены» перечитываются при смене отдела', () => {
    const corner = slice(APP, '<div className="w-64 sticky left-0 top-0 z-10 bg-white pr-2 border-r"', '<div className="flex-1" style={{ overflow: \'visible\' }}>');
    assert.ok(corner.includes('{plannerStatusLegendItems.length > 0 && ('));
    assert.ok(corner.includes('aria-label="Цвета статусов"'));
    assert.ok(corner.includes('style={{ background: item.bar }}'));
    // Сборка дня «Моих смен» зависит от отдела оператора — иначе останутся цвета прошлого входа.
    const myTrack = slice(APP, 'const getMyStatusTrackForDate = useCallback((dateKey) => {', 'const myCurrentDayStatusTrack = useMemo(');
    assert.match(myTrack, /\}, \[[^\]]*\bplannerOwnDepartmentCode\b[^\]]*\]\);\s*$/);
});

test('у каждой плашки палитры Тез есть правило тёмной темы', () => {
    const dark = readFileSync(new URL('../src/theme-dark.css', import.meta.url), 'utf8');
    const statuses = ['Готов', 'Соединение', 'Занят', 'Перезвон', 'Перерыв', 'Тренинг', 'Тех причина'];
    for (const status of statuses) {
        const { chip, row } = tez(status);
        for (const cls of `${chip} ${row}`.split(/\s+/).filter(Boolean)) {
            const selector = `html[data-otp-theme="dark"] .${cls.replace('/', '\\/')} {`;
            assert.ok(dark.includes(selector), `${status}: нет правила тёмной темы для ${cls}`);
        }
    }
});
