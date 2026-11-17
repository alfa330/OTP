// Точечная обработка базы (владелец, 17.11.2026): «удобный выбор загруженных баз на точечную обработку, с
// возможностью выбрать несколько и отображать прогресс по каждой базе». Чистая логика экрана
// (src/components/dial_list/batchFocus.js) и проводка: ручка, сводка без второго запроса, этап журнала.
//
// Запуск: node --test tests/dial_list_batch_focus.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    PARKED_STAGE, batchProgress, focusHint, focusStateText, focusWaitingText, focusedIds, goalLabel, plural,
    toggledFocus, uploadFocusNote, withFocusDraft,
} from '../src/components/dial_list/batchFocus.js';
import { TEZ_STAGES, TEZ_STAGE_ROW_LABEL } from '../src/components/dial_list/tezAutodial.js';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

test('прогресс файла: обработано = всего − в обзвоне, цель внутри обработанных', () => {
    const p = batchProgress({ progress: { total: 933, open: 512, goal: 37, ready: 400 } });
    assert.deepEqual([p.total, p.open, p.done, p.goal, p.ready, p.pct], [933, 512, 421, 37, 400, 45]);
    assert.ok(Math.abs(p.goalShare + p.restShare - 421 / 933) < 1e-9);
    // Сломанные данные не дают полосе вылезти за край: «в обзвоне» не больше всего, цель не больше обработанных.
    const bad = batchProgress({ progress: { total: 5, open: 9, goal: 3 } });
    assert.deepEqual([bad.total, bad.open, bad.done, bad.goal, bad.pct], [5, 5, 0, 0, 0]);
    for (const empty of [undefined, null, {}, { progress: null }, { progress: { total: 'x', open: -2 } }]) {
        assert.deepEqual(batchProgress(empty), {
            total: 0, open: 0, done: 0, goal: 0, ready: 0, pct: 0, goalShare: 0, restShare: 0,
        });
    }
});

test('процент не врёт по краям: 100 — только все, 0 — только никто', () => {
    const pct = (total, open) => batchProgress({ progress: { total, open } }).pct;
    assert.equal(pct(1000, 0), 100);
    assert.equal(pct(1000, 1), 99);            // 99,9 % — ещё не всё
    assert.equal(pct(1000, 999), 1);           // 0,1 % — уже что-то
    assert.equal(pct(1000, 1000), 0);
    assert.equal(pct(3, 2), 33);
    assert.equal(pct(0, 0), 0);
});

test('отметки: нажатие добавляет или снимает файл, черновик показывает выбор сразу', () => {
    const batches = [{ id: 'a', focused: true }, { id: 'b', focused: false }, { id: 'c' }];
    assert.deepEqual(focusedIds(batches), ['a']);
    assert.deepEqual(toggledFocus(batches, 'b'), ['a', 'b']);
    assert.deepEqual(toggledFocus(batches, 'a'), []);
    assert.deepEqual(focusedIds(null), []);
    const shown = withFocusDraft(batches, ['b', 'c']);
    assert.deepEqual(shown.map((b) => [b.id, b.focused]), [['a', false], ['b', true], ['c', true]]);
    assert.equal(batches[0].focused, true);    // исходный список не тронут
    assert.equal(withFocusDraft(batches, null), batches);
});

test('подпись над списком и цель словами отдела', () => {
    assert.equal(focusStateText([{ id: 'a' }, { id: 'b' }]), 'Обзванивается вся база');
    assert.equal(focusStateText([{ id: 'a', focused: true }, { id: 'b' }, { id: 'c', focused: true }]), 'В работе 2 из 3');
    assert.equal(goalLabel(true), 'вышли на линию');
    assert.equal(goalLabel(false), 'подписали');
    assert.match(focusHint(true), /перезвоны и «дожать» звонят в срок/);
    assert.doesNotMatch(focusHint(false), /дожать/);
    assert.match(focusHint(false), /ничего не отмечено — обзванивается вся база месяца/);
    assert.deepEqual([1, 2, 5, 11, 21, 104].map((n) => plural(n, 'файл', 'файла', 'файлов')),
        ['файл', 'файла', 'файлов', 'файлов', 'файл', 'файла']);
});

test('операторы ждут: в отмеченных звонить некому, а в остальных есть кому — только тогда предупреждаем', () => {
    const summary = (patch) => ({ is_active_period: true, pool_available: 0,
        focus: { active: true, files: 1, paused_ready: 31 }, ...patch });
    assert.equal(focusWaitingText(summary()), 'В отмеченных файлах сейчас звонить некому — операторы ждут. '
        + 'В остальных можно выдать 31 водителя: отметьте их или снимите отметки.');
    assert.match(focusWaitingText(summary({ focus: { active: true, paused_ready: 5 } })), /выдать 5 водителей/);
    assert.equal(focusWaitingText(summary({ pool_available: 3 })), '');               // звонить есть кому
    assert.equal(focusWaitingText(summary({ focus: { active: true, paused_ready: 0 } })), '');   // база пуста вообще
    assert.equal(focusWaitingText(summary({ focus: { active: false, paused_ready: 9 } })), '');
    assert.equal(focusWaitingText(summary({ is_active_period: false })), '');        // месяц не обзванивается
    assert.equal(focusWaitingText(null), '');
});

test('новый файл при отмеченных других — сказать, что он сам в работу не встал', () => {
    assert.match(uploadFocusNote({ focus: { active: true } }), /отметьте новый, чтобы операторы его получили/);
    assert.equal(uploadFocusNote({ focus: { active: false } }), '');
    assert.equal(uploadFocusNote(null), '');
});

test('этап «База на паузе» — в обоих журналах, точка полая, чип только с водителями', () => {
    assert.deepEqual({ ...PARKED_STAGE }, {
        value: 'parked', label: 'База на паузе', dot: 'bg-white ring-1 ring-inset ring-slate-400', hideIfZero: true,
    });
    assert.ok(TEZ_STAGES.includes(PARKED_STAGE));
    assert.equal(TEZ_STAGES.indexOf(PARKED_STAGE), TEZ_STAGES.findIndex((s) => s.value === 'waiting') + 1);
    assert.equal(TEZ_STAGE_ROW_LABEL.parked, 'База на паузе');
    const journal = read('src/components/dial_list/DialListJournal.jsx');
    assert.match(journal, /\{ value: 'waiting', label: 'Ждут повтора', dot: 'bg-amber-400' \},\s*\/\/[^\n]*\n\s*PARKED_STAGE,/);
    // Полоса этапов прячет пустой чип, но не выбранный — иначе фильтр было бы не снять.
    assert.match(journal, /&& \(!s\.hideIfZero \|\| value === s\.value \|\| Number\(counts\[s\.value\]\) > 0\)/);
    assert.match(journal, /case 'parked':[\s\S]{0,200}файл не отмечен в работу/);
    assert.match(journal, /parked: \{ Icon: CirclePause, tone: 'slate' \}/);
    // «Вернуть в список» у водителя на паузе нет: он и так в обзвоне, дело в файле.
    assert.doesNotMatch(journal.match(/const requeueStages = [^\n]*\n[^\n]*/)[0], /parked/);
});

test('проводка: отметки — PUT ручки раздела, ответ сразу в сводку, без второго запроса', () => {
    const panel = read('src/components/sip/DialListSettings.jsx');
    assert.match(panel, /\/batches\/focus`, \{\s*method: 'PUT',/);
    assert.match(panel, /body: JSON\.stringify\(\{ batch_ids: ids,/);
    assert.match(panel, /if \(typeof onSummary === 'function'\) onSummary\(data\);/);
    // Опрос сводки, начатый до отметки, не возвращает прежние отметки: устаревший ответ не применяется.
    assert.match(panel, /const applySummary = useCallback\(\(data\) => \{\s*loadSeq\.current \+= 1;/);
    assert.match(panel, /if \(my !== loadSeq\.current\) return;\s*setSettings/);
    // Отметки — только у обзваниваемого месяца и только у сервера, который их знает.
    assert.match(panel, /const focusShown = isActive && Boolean\(summary\?\.focus\);/);
    for (const where of ['src/components/dial_list/DialListView.jsx', 'src/components/sip/DialListSettings.jsx']) {
        assert.match(read(where), /onSummary=\{dept\.applySummary\}/, where);
    }
});
