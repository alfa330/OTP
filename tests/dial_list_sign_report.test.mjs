/*
 * «Удаленный КЦ» → «Аналитика» → «Отчёт в Telegram» (владелец, 19.10.2026): картинка «Подписание
 * водителей по городам» в выбранные группы в 10:00 и 19:00. Правила окна — в
 * src/components/dial_list/signReport.js (без React), картинку собирает сервер
 * (dial_list/sign_report.py).
 *
 * Что защищаем:
 *   • «Сохранить» загорается только при настоящей смене набора групп (порядок не важен);
 *   • о выбранных группах — только плохое: бота убрали из группы, последний отчёт не дошёл;
 *     всё в порядке — ни строки;
 *   • строка о последней отправке — по самой свежей группе, словами «сегодня / вчера»;
 *   • расписание в подписи окна — из ответа сервера;
 *   • кнопка стоит во вкладке «Аналитика» и ходит в ручки своего отдела.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    citiesSummary, fmtSentAt, lastSentLine, reportNotices, sameChatIds, scheduleText,
} from '../src/components/dial_list/signReport.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').split('\r\n').join('\n');

test('смена набора групп — без учёта порядка и повторов', () => {
    assert.equal(sameChatIds([-1, -2], [-2, -1]), true);
    assert.equal(sameChatIds(['-1', -2], [-2, -1, -1]), true);
    assert.equal(sameChatIds([-1], [-1, -2]), false);
    assert.equal(sameChatIds([], []), true);
    assert.equal(sameChatIds(undefined, []), true);
});

test('о группах — только то, что требует действия', () => {
    assert.deepEqual(reportNotices([{ chat_id: -1, title: 'Отчёты', is_available: true, last_error: '' }]), []);
    const notices = reportNotices([
        { chat_id: -1, title: 'Отчёты', is_available: false, last_error: 'Forbidden' },
        { chat_id: -2, title: 'Регионы', is_available: true, last_error: 'Bad Gateway' },
    ]);
    assert.deepEqual(notices, [
        { tone: 'amber', text: 'Бота больше нет в группе «Отчёты» — отчёт туда не дойдёт.' },
        { tone: 'rose', text: '«Регионы»: последний отчёт не дошёл — Bad Gateway' },
    ]);
    const two = reportNotices([
        { chat_id: -1, title: 'А', is_available: false },
        { chat_id: -2, title: 'Б', is_available: false },
    ]);
    assert.equal(two[0].text, 'Бота больше нет в группах «А», «Б» — отчёт туда не дойдёт.');
});

test('последняя отправка — по самой свежей группе', () => {
    const now = new Date(2026, 9, 19, 12, 0);
    const today10 = new Date(2026, 9, 19, 10, 0).toISOString();
    const yesterday19 = new Date(2026, 9, 18, 19, 0).toISOString();
    assert.equal(lastSentLine([{ last_sent_at: yesterday19 }, { last_sent_at: today10 }], now),
        'Последний отчёт ушёл сегодня, 10:00');
    assert.equal(lastSentLine([{ last_sent_at: yesterday19 }, { last_sent_at: null }], now),
        'Последний отчёт ушёл вчера, 19:00');
    assert.equal(lastSentLine([{ last_sent_at: null }], now), '');
    assert.equal(fmtSentAt(new Date(2026, 9, 17, 10, 0).toISOString(), now), '17.10, 10:00');
    assert.equal(fmtSentAt('мусор', now), '');
});

test('расписание — из ответа сервера', () => {
    assert.equal(scheduleText(['10:00', '19:00']), 'в 10:00 и 19:00');
    assert.equal(scheduleText(['09:00']), 'в 09:00');
    assert.equal(scheduleText(['08:00', '13:00', '19:00']), 'в 08:00, 13:00 и 19:00');
    assert.equal(scheduleText([]), '');
});

test('итог загрузки списка — одной фразой, только ненулевое', () => {
    assert.equal(
        citiesSummary({ matched: 23766, updated: 4, added_report_only: 5, not_in_report: 5, bad_iin: 0, no_city: 0 },
            'Октябрь 2026'),
        'Октябрь 2026: город у 23766 водителей, изменился у 4, без номера добавлено 5 — только для отчёта, '
            + 'не из списка 5 — в отчёт не входят',
    );
    assert.equal(
        citiesSummary({ matched: 3, updated: 1, added_report_only: 0, not_in_report: 0, bad_iin: 2, no_city: 1 }),
        'город у 3 водителей, изменился у 1, с ошибкой в ИИН 2, без города 1',
    );
    const panel = read('../src/components/sip/DialListSettings.jsx');
    assert.match(panel, /\/api\/dial_list\/departments\/\$\{departmentId\}\/leads\/cities/);
    assert.match(panel, /citiesSummary\(data, monthLabel\(data\.period \|\| shownPeriod\)\)/);
});

test('кнопка — во вкладке «Аналитика», ручки — своего отдела', () => {
    const analytics = read('../src/components/dial_list/DialListAnalytics.jsx');
    assert.match(analytics, /<DialListSignReport\b/);
    const view = read('../src/components/dial_list/DialListView.jsx');
    const block = view.slice(view.indexOf("{tab === 'analytics' && !tez && ("));
    assert.match(block.slice(0, block.indexOf('/>')), /showToast=\{showToast\}/);
    const modal = read('../src/components/dial_list/DialListSignReport.jsx');
    assert.match(modal, /\/api\/dial_list\/departments\/\$\{departmentId\}\/sign_report/);
    assert.match(modal, /`\$\{base\}\/chats`/);
    assert.match(modal, /`\$\{base\}\/preview`/);
    const routes = read('../dial_list/routes.py');
    assert.match(routes, /'\/api\/dial_list\/departments\/<int:department_id>\/sign_report\/chats'/);
    assert.match(routes, /'\/api\/dial_list\/departments\/<int:department_id>\/sign_report\/preview'/);
});
