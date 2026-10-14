import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    AI_QA_SUBJECT_KINDS, buildEvalLink, formatEvalTarget, parseEvalTarget, readEvalLink, stripAiQaParams,
    syncEvalLink,
} from '../src/components/call_qa/evalLink.js';

/**
 * Ссылка на оценку «ИИ-оценки»: ?view=ai_qa&eval=<вид>-<id>. Вид обязателен — id у
 * звонков журнала, звонков из АТС и переписок независимые. Раздел открывает карточку
 * по адресу, держит адрес открытой карточки, а App.jsx снимает метку при уходе.
 */

test('виды — те же, что у сервера', () => {
    const py = readFileSync(new URL('../call_qa/config.py', import.meta.url), 'utf8');
    const names = py.match(/^SUBJECT_KINDS = \(([^)]*)\)/m)[1].split(',').map((s) => s.trim()).filter(Boolean);
    const kinds = names.map((name) => py.match(new RegExp(`^${name} = "([a-z0-9_]+)"`, 'm'))[1]);
    assert.deepEqual([...kinds].sort(), [...AI_QA_SUBJECT_KINDS].sort());
});

test('цель ↔ метка: вид и номер', () => {
    assert.equal(formatEvalTarget({ subject: 'imported_call', id: 8930 }), 'imported_call-8930');
    assert.deepEqual(parseEvalTarget('imported_call-8930'), { subject: 'imported_call', id: 8930 });
    assert.deepEqual(parseEvalTarget(' wz_episode-512 '), { subject: 'wz_episode', id: 512 });
    // Вид с цифрой — заявка Chat2Desk у СЗоВ.
    assert.deepEqual(parseEvalTarget('c2d_snapshot-3741'), { subject: 'c2d_snapshot', id: 3741 });
    for (const kind of AI_QA_SUBJECT_KINDS) {
        assert.deepEqual(parseEvalTarget(formatEvalTarget({ subject: kind, id: 7 })), { subject: kind, id: 7 });
    }
});

test('мусор ссылкой не становится', () => {
    for (const bad of ['', '8930', 'call-', 'call-0', 'call--5', 'cdr_touch-5', 'CALL-5', 'call-5x', 'call-5/6',
                       'call-1234567890123', null, undefined]) {
        assert.equal(parseEvalTarget(bad), null, String(bad));
    }
    assert.equal(formatEvalTarget({ subject: 'call', id: 0 }), '');
    assert.equal(formatEvalTarget({ subject: 'cdr_touch', id: 5 }), '');
    assert.equal(formatEvalTarget({ subject: 'call', id: 'abc' }), '');
    assert.equal(formatEvalTarget(null), '');
});

test('метку читает только адрес раздела', () => {
    assert.deepEqual(readEvalLink('?view=ai_qa&eval=call-40812'), { subject: 'call', id: 40812 });
    assert.equal(readEvalLink('?view=tasks&eval=call-40812'), null);
    assert.equal(readEvalLink('?eval=call-40812'), null);
    assert.equal(readEvalLink('?view=ai_qa'), null);
    assert.equal(readEvalLink(''), null);
});

test('ссылка — только раздел и оценка, путь портала сохраняется', () => {
    const link = buildEvalLink({ subject: 'imported_call', id: 8930 },
        'https://alfa330.github.io/OTP/?view=crm_tickets&ticket_id=812&task_id=5&v=17#top');
    const url = new URL(link);
    assert.equal(url.origin + url.pathname, 'https://alfa330.github.io/OTP/');
    assert.deepEqual([...url.searchParams.entries()], [['view', 'ai_qa'], ['eval', 'imported_call-8930']]);
    assert.equal(url.hash, '');
    assert.equal(buildEvalLink({ subject: 'cdr_touch', id: 1 }, 'https://x/'), '');
    assert.equal(buildEvalLink({ subject: 'call', id: 1 }, ''), '');
});

test('уход из раздела снимает метку', () => {
    const url = new URL('https://x/?view=ai_qa&eval=call-1&task_id=3');
    stripAiQaParams(url);
    assert.equal(url.search, '?view=ai_qa&task_id=3');
});

const fakeWindow = (href) => {
    const calls = [];
    const win = {
        location: new URL(href),
        history: {
            state: { key: 1 },
            replaceState(state, _title, next) {
                calls.push(next);
                win.location = new URL(next, win.location.href);
            },
        },
    };
    return { win, calls };
};

test('адрес повторяет открытую карточку и не трогает чужой раздел', () => {
    const previous = globalThis.window;
    try {
        const { win, calls } = fakeWindow('https://x/OTP/?view=ai_qa&v=9');
        globalThis.window = win;
        syncEvalLink({ subject: 'ca_episode', id: 77 });
        assert.equal(calls.at(-1), '/OTP/?view=ai_qa&eval=ca_episode-77');
        syncEvalLink({ subject: 'ca_episode', id: 77 });
        assert.equal(calls.length, 1, 'тот же адрес второй раз не пишется');
        syncEvalLink(null);
        assert.equal(calls.at(-1), '/OTP/?view=ai_qa');

        const other = fakeWindow('https://x/OTP/?view=wiki&article=a');
        globalThis.window = other.win;
        syncEvalLink({ subject: 'call', id: 1 });
        assert.equal(other.calls.length, 0, 'чужой раздел — адрес не трогаем');
    } finally {
        globalThis.window = previous;
    }
});

test('проводка: App снимает метку при уходе, раздел открывает и держит адрес', () => {
    const app = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
    assert.match(app, /import \{ stripAiQaParams \} from '\.\/components\/call_qa\/evalLink';/);
    assert.equal(app.match(/if \(nextView !== 'ai_qa'\) stripAiQaParams\(url\);/g)?.length, 2,
        'buildAppViewUrl и syncAppViewWithUrl');
    const view = readFileSync(new URL('../src/components/call_qa/CallQaView.jsx', import.meta.url), 'utf8');
    assert.match(view, /readEvalLink\(window\.location\.search\)/);
    assert.match(view, /if \(linkPending\.current\) return;\s*syncEvalLink\(selected \?/);
    assert.match(view, /adoptCardDepartment\(r\.data\.call\?\.department\)/);
    assert.match(view, /aria-label="Скопировать ссылку на оценку"/);
    // Ссылку открываем только когда известен отдел: без него запросы раздела смешали бы отделы.
    assert.match(view, /departments === null \|\| departmentsErr \|\| department === null\) return;/);
});
