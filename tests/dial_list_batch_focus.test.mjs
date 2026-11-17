// Точечная обработка базы (владелец, 17.11.2026): «удобный выбор загруженных баз на точечную обработку, с
// возможностью выбрать несколько и отображать прогресс по каждой базе». Чистая логика экрана
// (src/components/dial_list/batchFocus.js) и проводка: ручка, сводка без второго запроса, этап журнала.
//
// Запуск: node --test tests/dial_list_batch_focus.test.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    PARKED_STAGE, batchProgress, batchRowState, focusErrorText, focusHint, focusStateText, focusSubtitleNote,
    focusWaitingText, focusedIds, goalLabel, plural, summaryMatchesScope, toggledFocus, uploadFocusNote,
    withFocusDraft,
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
    for (const empty of [undefined, null, {}, { progress: null }]) {
        assert.deepEqual(batchProgress(empty), {
            known: false, total: 0, open: 0, done: 0, goal: 0, ready: 0, pct: 0, goalShare: 0, restShare: 0,
        });
    }
    // Прогресс пришёл, но пустой или битый — известен: «водителей нет» правда.
    assert.deepEqual(batchProgress({ progress: { total: 'x', open: -2 } }), {
        known: true, total: 0, open: 0, done: 0, goal: 0, ready: 0, pct: 0, goalShare: 0, restShare: 0,
    });
});

test('строка файла: кружок только у обзваниваемого месяца, приглушён неотмеченный, нажать — с правом правки', () => {
    const on = { focused: true };
    const off = { focused: false };
    assert.deepEqual(batchRowState(on, { focusShown: true, focusOn: true, canEdit: true }),
        { mark: true, paused: false, toggle: true });
    assert.deepEqual(batchRowState(off, { focusShown: true, focusOn: true, canEdit: true }),
        { mark: false, paused: true, toggle: true });
    // Ничего не отмечено — никто не приглушён; без права правки — состояние видно, нажать нельзя.
    assert.deepEqual(batchRowState(off, { focusShown: true, focusOn: false, canEdit: false }),
        { mark: false, paused: false, toggle: false });
    // Месяц не обзванивается (или сервер отметок не знает) — ни кружка, ни нажатия.
    assert.deepEqual(batchRowState(on, { focusShown: false, focusOn: false, canEdit: true }),
        { mark: null, paused: false, toggle: false });
    assert.deepEqual(batchRowState(off), { mark: null, paused: false, toggle: false });
});

test('ответ на отметку применяется только к своему отделу и месяцу', () => {
    const data = { department_id: 560, period: '2026-11-01' };
    assert.equal(summaryMatchesScope(data, { departmentId: 560, period: '' }), true);
    assert.equal(summaryMatchesScope(data, { departmentId: '560', period: '2026-11' }), true);
    assert.equal(summaryMatchesScope(data, { departmentId: 560, period: '2026-10-01' }), false);   // сменили месяц
    assert.equal(summaryMatchesScope(data, { departmentId: 1954, period: '' }), false);           // сменили отдел
    assert.equal(summaryMatchesScope(null, { departmentId: 560 }), false);
    assert.equal(summaryMatchesScope(data, null), false);
});

test('ошибка отметки словами: текст сервера как есть, сеть и пустой ответ — по-русски', () => {
    assert.equal(focusErrorText(new Error('Этого файла нет в базе месяца — обновите страницу')),
        'Этого файла нет в базе месяца — обновите страницу');
    const fallback = 'Не удалось отметить файлы — проверьте связь и повторите';
    assert.equal(focusErrorText(new TypeError('Failed to fetch')), fallback);
    assert.equal(focusErrorText(new Error('HTTP 502')), fallback);
    assert.equal(focusErrorText(new Error('')), fallback);
    assert.equal(focusErrorText(null), fallback);
});

test('метка в шапке отдела — только пока отбор включён и месяц обзванивается', () => {
    const batches = [{ id: 'a', focused: true }, { id: 'b' }, { id: 'c', focused: true }];
    assert.equal(focusSubtitleNote({ batches }), 'в работе 2 из 3 файлов');
    assert.equal(focusSubtitleNote({ batches: [{ id: 'a', focused: true }] }), 'в работе 1 из 1 файла');
    assert.equal(focusSubtitleNote({ batches: [{ id: 'a' }] }), '');
    assert.equal(focusSubtitleNote({ batches, is_active_period: false }), '');
    assert.equal(focusSubtitleNote(null), '');
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
    assert.equal(focusWaitingText(summary({ pool_available: 1 })), '');               // и один — уже есть кому
    assert.equal(focusWaitingText(summary({ focus: { active: true, paused_ready: 0 } })), '');   // база пуста вообще
    assert.equal(focusWaitingText(summary({ focus: { active: false, paused_ready: 9 } })), '');
    assert.equal(focusWaitingText(summary({ is_active_period: false })), '');        // месяц не обзванивается
    assert.equal(focusWaitingText(null), '');
});

test('новый файл при отмеченных других — сказать, что он сам в работу не встал', () => {
    assert.match(uploadFocusNote({ focus: { active: true } }), /отметьте новый, чтобы операторы его получили/);
    assert.equal(uploadFocusNote({ focus: { active: false } }), '');
    assert.equal(uploadFocusNote(null), '');
    // Загрузка в другой месяц и файл, где не загрузилось ни строки, — без приписки.
    assert.equal(uploadFocusNote({ focus: { active: true } }, { isActive: false }), '');
    assert.equal(uploadFocusNote({ focus: { active: true } }, { failed: true }), '');
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
    assert.match(panel, /if \(typeof onSummary !== 'function'\) onChanged\?\.\(\);\s*else onSummary\(data\);/);
    assert.match(panel, /toast\(focusErrorText\(e\), 'error'\);/);
    // Опрос сводки, начатый до отметки, не возвращает прежние отметки: устаревший ответ не применяется; ответ
    // отметки — только к своему отделу и месяцу.
    assert.match(panel, /const applySummary = useCallback\(\(data\) => \{\s*if \(!summaryMatchesScope\(data, scopeRef\.current\)\) return false;\s*loadSeq\.current \+= 1;/);
    assert.match(panel, /scopeRef\.current = \{ departmentId, period \};/);
    // Смена отдела и месяца сбрасывают несохранённые отметки; строки — по общей функции состояния.
    assert.match(panel, /useEffect\(\(\) => \{ setFocusDraft\(null\); \}, \[period\]\);/);
    assert.match(panel, /const row = batchRowState\(b, \{ focusShown, focusOn, canEdit \}\);/);
    assert.match(panel, /onToggle=\{row\.toggle \? \(id\) => saveFocus\(toggledFocus\(batches, id\)\) : null\}/);
    assert.match(panel, /uploadFocusNote\(summary, \{ isActive, failed: Boolean\(failedAll\) \}\)/);
    // Строки «звонить некому» в файле нет: о ней говорит плашка над списком.
    assert.doesNotMatch(panel, /остальные ждут повтора/);
    const view = read('src/components/dial_list/DialListView.jsx');
    assert.match(view, /\? \(focusWaitingText\(dept\.summary\)/);
    assert.match(view, /\$\{focusNote \? `, \$\{focusNote\}` : ''\}/);
    assert.match(panel, /if \(my !== loadSeq\.current\) return;\s*setSettings/);
    // Отметки — только у обзваниваемого месяца и только у сервера, который их знает.
    assert.match(panel, /const focusShown = isActive && Boolean\(summary\?\.focus\);/);
    for (const where of ['src/components/dial_list/DialListView.jsx', 'src/components/sip/DialListSettings.jsx']) {
        assert.match(read(where), /onSummary=\{dept\.applySummary\}/, where);
    }
});
