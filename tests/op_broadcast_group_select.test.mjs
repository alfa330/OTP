import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/*
 * Выбор группы у получателя отбивки «Табло ОП» (владелец 23.09.2026): чату «Основы» —
 * показатели «Основы», руководству — весь отдел. Окно получателей общее для всех табло,
 * поэтому выбор обязан появляться только там, где сервер прислал группы, — у СЗоВ и Тез КЦ
 * групп нет, и лишний выпадающий список там был бы обещанием без содержания.
 */

const source = readFileSync(new URL('../src/components/monitoring/SzovWallboardView.jsx', import.meta.url), 'utf8');

test('список групп берётся из ответа сервера, своей копии каталога нет', () => {
    assert.match(source, /const groups = state\?\.groups \|\| \[\];/);
    assert.ok(!/Основа['"]\s*,\s*['"]ЯР/.test(source), 'подписи групп не зашиваются во фронт');
});

test('выбор показывается только при наличии групп — и в строке, и в форме добавления', () => {
    const shown = source.match(/\{groups\.length \? \(/g) || [];
    assert.equal(shown.length, 2);
});

test('«Весь отдел» — это пустое значение, а группа уходит числом', () => {
    assert.match(source, /<option value="">Весь отдел<\/option>/);
    assert.match(source, /onChange\(event\.target\.value \? Number\(event\.target\.value\) : null\)/);
});

test('новый получатель сохраняется с выбранной группой, а у табло без групп поле не шлётся', () => {
    assert.match(source, /\.\.\.\(groups\.length \? \{ group_id: draftGroup \|\| null \} : \{\}\)/);
});
