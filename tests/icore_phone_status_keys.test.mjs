import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';


/*
 * Статусы iCORE Phone у групп отдела продаж (ТЗ 05.10.2026): «Исход»,
 * «Соединение», «Автодозвон» и «Офлайн» приходят в часы новыми ключами.
 *
 * Словари статусов живут внутри App.jsx, поэтому тест вынимает из исходника
 * настоящие функции (как live_status_track.test.mjs) и проверяет их. Ошибка
 * здесь не падает, а тихо считает простой «Офлайн» на смене или красит его
 * так же, как оплачиваемую «Офлайн активность».
 */

const APP = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
// Модули табло тянут соседей без расширения (так их собирает vite), и node их
// не импортирует — каталоги стилей вынимаем из исходника так же, как App.jsx.
const OP_SHARED = readFileSync(new URL('../src/components/monitoring/opWallboardShared.js', import.meta.url), 'utf8');
const TEZ_SHARED = readFileSync(new URL('../src/components/monitoring/tezWallboardShared.js', import.meta.url), 'utf8');

function slice(source, start, end) {
    const from = source.indexOf(start);
    assert.notEqual(from, -1, `нет начала: ${start}`);
    const to = source.indexOf(end, from + start.length);
    assert.notEqual(to, -1, `нет конца: ${end}`);
    return source.slice(from, to);
}

const keys = slice(APP, 'const plannerStatusPad2 = (n) =>', 'const plannerStatusNormalizeOperatorName = (v) =>');
const tone = slice(APP, 'const getPlannerImportedStatusTone = (statusNameOrKey) => {', 'const PLANNER_IMPORTED_WORK_STATUS_KEYS = new Set([');
const sets = slice(APP, 'const PLANNER_IMPORTED_WORK_STATUS_KEYS = new Set([', 'const plannerIntervalsTotalMinutes = (arr = []) => (');

const {
    plannerStatusLabelFromKey,
    getPlannerImportedStatusTone,
    PLANNER_IMPORTED_WORK_STATUS_KEYS,
    PLANNER_IMPORTED_BREAK_STATUS_KEYS,
    PLANNER_IMPORTED_LATE_START_STATUS_KEYS,
    plannerImportedStatusCountsAsOnShift,
} = new Function(`
    ${keys}
    ${tone}
    ${sets}
    return {
        plannerStatusLabelFromKey,
        getPlannerImportedStatusTone,
        PLANNER_IMPORTED_WORK_STATUS_KEYS,
        PLANNER_IMPORTED_BREAK_STATUS_KEYS,
        PLANNER_IMPORTED_LATE_START_STATUS_KEYS,
        plannerImportedStatusCountsAsOnShift,
    };
`)();

test('подписи новых ключей — словами телефона', () => {
    assert.equal(plannerStatusLabelFromKey('исход'), 'Исход');
    assert.equal(plannerStatusLabelFromKey('соединение'), 'Соединение');
    assert.equal(plannerStatusLabelFromKey('автодозвон'), 'Автодозвон');
    assert.equal(plannerStatusLabelFromKey('Офлайн'), 'Офлайн');
    // Старые дни ОП остаются со своим «перезвоном» — подпись у него прежняя.
    assert.equal(plannerStatusLabelFromKey('перезвон'), 'Перезвон');
});

test('«Исход», набор и «Автодозвон» — работа; «Офлайн» — нет', () => {
    for (const key of ['исход', 'соединение', 'автодозвон']) {
        assert.ok(PLANNER_IMPORTED_WORK_STATUS_KEYS.has(key), key);
        assert.ok(PLANNER_IMPORTED_LATE_START_STATUS_KEYS.has(key), key);
        assert.ok(plannerImportedStatusCountsAsOnShift(key), key);
    }
    assert.ok(!PLANNER_IMPORTED_WORK_STATUS_KEYS.has('офлайн'));
    assert.ok(!PLANNER_IMPORTED_BREAK_STATUS_KEYS.has('офлайн'));
    assert.ok(!PLANNER_IMPORTED_LATE_START_STATUS_KEYS.has('офлайн'));
    // Простой «Офлайн» — не на смене для плана/факта по часам.
    assert.equal(plannerImportedStatusCountsAsOnShift('офлайн'), false);
    assert.equal(plannerImportedStatusCountsAsOnShift('Офлайн'), false);
});

test('у каждого нового статуса свой цвет, и по ключу, и по подписи', () => {
    const fallback = getPlannerImportedStatusTone('что-то незнакомое').bar;
    const bars = new Map();
    for (const key of ['исход', 'соединение', 'автодозвон', 'офлайн']) {
        const byKey = getPlannerImportedStatusTone(key);
        const byLabel = getPlannerImportedStatusTone(plannerStatusLabelFromKey(key));
        assert.notEqual(byKey.bar, fallback, `${key}: серый цвет по умолчанию`);
        assert.deepEqual(byLabel, byKey, `${key}: по подписи цвет другой`);
        bars.set(key, byKey.bar);
    }
    assert.equal(new Set(bars.values()).size, 4, 'два новых статуса слились по цвету');
    // «Офлайн» не путается ни с «Выключен», ни с перерывом, ни с работой.
    for (const other of ['выключен', 'перерыв', 'готов', 'без телефона']) {
        assert.notEqual(bars.get('офлайн'), getPlannerImportedStatusTone(other).bar, other);
    }
    assert.match(getPlannerImportedStatusTone('офлайн').chip, /red/);
});

const styleMap = (source, name) => {
    const body = slice(source, `export const ${name} = {`, '\n};').replace(`export const ${name} = `, '');
    return new Function(`return ${body}\n};`)();
};
const OP_STATUS_STYLE = styleMap(OP_SHARED, 'OP_STATUS_STYLE');
const TEZ_STATUS_STYLE = styleMap(TEZ_SHARED, 'TEZ_STATUS_STYLE');

test('табло: новые тоны с каталога сервера оформлены, а не падают в «прочее»', () => {
    for (const styles of [OP_STATUS_STYLE, TEZ_STATUS_STYLE]) {
        for (const [key, label] of [['connecting', 'Соединение'], ['autodial', 'Автодозвон'], ['idle_offline', 'Офлайн']]) {
            assert.ok(styles[key], key);
            assert.equal(styles[key].label, label);
            assert.notEqual(styles[key].chip, styles.other.chip, key);
        }
        // «Офлайн» (простой) и «Не в сети» (вышел) — разные вещи и разный цвет.
        assert.notEqual(styles.idle_offline.chip, styles.offline.chip);
        // «Соединение» — ещё гудки, не разговор.
        assert.notEqual(styles.connecting.chip, styles.talking.chip);
    }
});

test('плитка «Онлайн» табло ОП говорит, кого считает', () => {
    // Сервер считает онлайн с «Соединением» и «Автодозвоном» (op_wallboard/snapshot.py);
    // подсказка со старым «Свободны и в разговоре» расходилась бы с числом.
    const tile = slice(OP_SHARED, "key: 'op_online'", "key: 'op_free'");
    assert.ok(tile.includes("hint: 'Свободны, набирают, на автодозвоне и в разговоре'"));
});
