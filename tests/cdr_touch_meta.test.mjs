import test from 'node:test';
import assert from 'node:assert/strict';

import {
    REPORTS, RESULT_CLIENT_HUNG_UP, RESULT_DROPPED, RESULT_TALK, amoAnsweredNote, amoMark,
    missedFilterShown, missedHint,
    exportFileName, hms, hours, inboundReportFileName, percent, fullDay, prettyPhone, reportParams,
    reportState, resultTone, ringSeconds, shortDay, shortTime, silence,
} from '../src/components/cdr/touchMeta.js';

/* Подписи раздела «Касания».
 *
 * Главное, ради чего файл существует: `hms` обязан совпадать с серверным
 * `cdr/report.py:hms`. Экран и выгрузка показывают одни и те же звонки, и если
 * один напишет «0:42», а второй «42 с», человек решит, что перед ним разные
 * цифры. Набор случаев здесь и в tests/test_cdr_touches.py связан — правя один,
 * правьте второй.
 *
 * Телефоны учебные (7XX555XXXX): настоящих в репозитории быть не должно.
 */

test('нулевой разговор — прочерк, а не 0:00', () => {
    // «0:00» читается как «разговор был и длился ноль», а его не было вовсе.
    assert.equal(hms(0), '—');
    assert.equal(hms(null), '—');
    assert.equal(hms(undefined), '—');
    assert.equal(hms(-5), '—');
    assert.equal(hms('мусор'), '—');
});

test('минуты и секунды — без часов, пока их нет', () => {
    assert.equal(hms(42), '0:42');
    assert.equal(hms(60), '1:00');
    assert.equal(hms(432), '7:12');
    assert.equal(hms(3599), '59:59');
});

test('от часа появляется третья группа с нулями', () => {
    assert.equal(hms(3600), '1:00:00');
    assert.equal(hms(3870), '1:04:30');
    assert.equal(hms(419947), '116:39:07');
});

test('часы округляются до одного знака', () => {
    assert.equal(hours(3600), 1);
    assert.equal(hours(419947), 116.7);
    assert.equal(hours(0), 0);
    assert.equal(hours(null), 0);
});

test('проценты не превращаются в NaN на пустом периоде', () => {
    assert.equal(percent(1413, 3396), 41.6);
    assert.equal(percent(0, 0), 0);
    assert.equal(percent(5, 0), 0);
    assert.equal(percent(1, 3), 33.3);
});

test('телефон показывается так, как его набирают', () => {
    assert.equal(prettyPhone('7015550001'), '+7 701 555 00 01');
});

test('непонятный номер показывается сырым, а не выдуманным', () => {
    // Выдумать формат для значения, которого мы не поняли, хуже, чем показать
    // его как есть: человек хотя бы увидит, что с данными что-то не то.
    assert.equal(prettyPhone('123'), '123');
    assert.equal(prettyPhone(''), '—');
    assert.equal(prettyPhone(null), '—');
});

test('время и день режутся из отметки без разбора даты', () => {
    assert.equal(shortTime('2026-08-24 09:05:41'), '09:05:41');
    assert.equal(shortTime(''), '—');
    assert.equal(shortDay('2026-08-24 09:05:41'), '24.08');
    assert.equal(shortDay('2026-08-24'), '24.08');
    assert.equal(shortDay(''), '—');
    assert.equal(shortDay(null), '—');
});

test('молчание моста показывается порядком величины', () => {
    // На третьи сутки молчания точное число минут ничего не сообщает.
    assert.equal(silence(0), '0 мин');
    assert.equal(silence(12), '12 мин');
    assert.equal(silence(59), '59 мин');
    assert.equal(silence(60), '1 ч');
    assert.equal(silence(200), '3 ч');
    assert.equal(silence(1440), '1 сут');
    assert.equal(silence(4321), '3 сут');
    assert.equal(silence(-5), '0 мин');
});

test('«сброс без разговора» отличается по цвету от «не ответил»', () => {
    // Это не «не дозвонились», а «дозвонились и бросили» — разница видна
    // руководителю, ради которого раздел и делался.
    assert.notEqual(resultTone(RESULT_DROPPED), resultTone('Не ответил'));
    assert.match(resultTone(RESULT_TALK), /emerald/);
    assert.match(resultTone(RESULT_DROPPED), /amber/);
});

test('незнакомый результат не остаётся без плашки', () => {
    assert.ok(resultTone('что-то новое'));
});

test('«клиент сбросил» — нейтральная плашка, без цвета вины', () => {
    // Клиент положил трубку, пока звонил телефон: оператор не виноват, красить нечего.
    assert.equal(RESULT_CLIENT_HUNG_UP, 'Клиент сбросил');
    assert.match(resultTone(RESULT_CLIENT_HUNG_UP), /slate/);
});

test('звон «клиент сбросил» — с десятыми, половина вверх, как в выгрузке', () => {
    // Двойник cdr/report.py:ring_seconds: 1250 мс и там, и здесь — 1,3 с.
    assert.equal(ringSeconds(1334), '1,3 с');
    assert.equal(ringSeconds(1250), '1,3 с');
    assert.equal(ringSeconds(1249), '1,2 с');
    assert.equal(ringSeconds(748), '0,7 с');
    assert.equal(ringSeconds(12000), '12,0 с');
    // Ноль — звон оборвался сразу; «0,0 с» читалось бы как ошибка.
    assert.equal(ringSeconds(0), 'меньше 0,1 с');
    assert.equal(ringSeconds(49), 'меньше 0,1 с');
    // Звона нет — подписи нет.
    assert.equal(ringSeconds(null), '');
    assert.equal(ringSeconds(undefined), '');
    assert.equal(ringSeconds(''), '');
    assert.equal(ringSeconds('мусор'), '');
    assert.equal(ringSeconds(-5), '');
});

test('имя файла собирает фронт — через CORS оно до нас не доходит', () => {
    // С годом: файл лежит в «Загрузках» месяцами, и от такого же за прошлый год
    // его иначе не отличить. Должно совпадать с cdr/report.py:report_filename.
    assert.equal(exportFileName('2026-08-24', '2026-08-24'), 'Касания 24.08.2026.xlsx');
    assert.equal(exportFileName('2026-08-01', '2026-08-31'),
                 'Касания 01.08.2026 — 31.08.2026.xlsx');
    assert.equal(fullDay('2026-08-24'), '24.08.2026');
    assert.equal(fullDay(''), '');
    // «Отчёт по входу» — то же правило; совпадает с cdr/inbound_report.py:report_filename.
    assert.equal(inboundReportFileName('2026-11-02', '2026-11-02'), 'Отчёт по входу 02.11.2026.xlsx');
    assert.equal(inboundReportFileName('2026-11-01', '2026-11-10'),
                 'Отчёт по входу 01.11.2026 — 10.11.2026.xlsx');
});

/* Окно «Отчёты» (владелец, 11.11.2026): прежняя выгрузка с описанием и «Отчёт по входу». */
test('в окне «Отчёты» два отчёта, у каждого описание и своя ручка', () => {
    assert.deepEqual(REPORTS.map((report) => report.title), ['Касания', 'Отчёт по входу']);
    assert.deepEqual(REPORTS.map((report) => report.path), ['/export', '/reports/inbound']);
    for (const report of REPORTS) assert.ok(report.text.length > 40, report.title);
    const [touches, inbound] = REPORTS;
    assert.equal(touches.fileName('2026-11-02', '2026-11-02'), 'Касания 02.11.2026.xlsx');
    assert.equal(inbound.fileName('2026-11-02', '2026-11-02'), 'Отчёт по входу 02.11.2026.xlsx');
    // В описании сказано главное отличие от ручного файла: автообзвон не считается.
    assert.match(inbound.text, /без автообзвона/);
});

test('фильтры раздела идут только в «Касания»', () => {
    const [touches, inbound] = REPORTS;
    const query = { date_from: '2026-11-02', date_to: '2026-11-10', page: 3, page_size: 50,
                    ext: '6474', call_type: 'Входящий' };
    assert.deepEqual(reportParams(touches, query),
                     { date_from: '2026-11-02', date_to: '2026-11-10', ext: '6474', call_type: 'Входящий' });
    assert.deepEqual(reportParams(inbound, query), { date_from: '2026-11-02', date_to: '2026-11-10' });
});

test('подпись под отчётом — только когда есть что сказать', () => {
    const [touches, inbound] = REPORTS;
    assert.deepEqual(reportState(touches, { total: 12, filtersCount: 2 }), { disabled: false, note: '' });
    // Под фильтры ничего не попало — пустой файл не выгружаем и говорим почему.
    const empty = reportState(touches, { total: 0, filtersCount: 2 });
    assert.equal(empty.disabled, true);
    assert.match(empty.note, /ничего не попало/);
    // «Отчёт по входу» от фильтров не зависит и доступен всегда; об этом сказано,
    // только если фильтры включены.
    assert.deepEqual(reportState(inbound, { total: 0, filtersCount: 0 }), { disabled: false, note: '' });
    assert.match(reportState(inbound, { total: 0, filtersCount: 1 }).note, /не действуют/);
});

/* Метка робота пропущенных (задача #291). Сервер отдаёт amo_state, экран решает, что
 * показать: ссылку, красную метку «не передан» или ничего. */
test('переданный звонок — ссылка на сделку', () => {
    const mark = amoMark({
        amo_state: 'sent', amo_lead_id: 57009999,
        amo_url: 'https://example.amocrm.ru/leads/detail/57009999',
        amo_note: 'За минуту после звонка клиенту не ответили',
    });
    assert.equal(mark.kind, 'sent');
    assert.equal(mark.label, 'в amoCRM');
    assert.equal(mark.href, 'https://example.amocrm.ru/leads/detail/57009999');
    assert.match(mark.title, /Сделка 57009999 в amoCRM\. За минуту/);
});

test('без ссылки метки нет, даже если сервер назвал звонок переданным', () => {
    assert.equal(amoMark({ amo_state: 'sent', amo_url: '' }), null);
});

test('сбой передачи видно, чтобы перезвонили руками', () => {
    const mark = amoMark({ amo_state: 'error', amo_note: '' });
    assert.equal(mark.kind, 'error');
    assert.equal(mark.label, 'не передан в amoCRM');
    assert.match(mark.title, /перезвоните клиенту вручную/);
});

test('дозвонившийся — без метки, причина только в подсказке', () => {
    const touch = { amo_state: 'answered', amo_note: 'Клиент перезвонил в 10:01:17 и поговорил с оператором' };
    assert.equal(amoMark(touch), null);
    assert.equal(amoAnsweredNote(touch),
                 'В amoCRM не передан: клиент перезвонил в 10:01:17 и поговорил с оператором');
    assert.equal(amoAnsweredNote({ amo_state: '' }), '');
    assert.equal(amoMark({}), null);
    assert.equal(amoMark(null), null);
});

/* Переадресация в Syntony (SALES-4): номера заявки Syntony не отдаёт — метка без ссылки;
 * сбой и «дозвонился» называют ту CRM, куда робот передавал. */
test('принятая Syntony заявка — метка без ссылки', () => {
    const mark = amoMark({
        amo_state: 'sent', missed_crm: 'syntony', amo_lead_id: null, amo_url: '',
        amo_note: 'За минуту после звонка клиенту не ответили',
    });
    assert.equal(mark.kind, 'sent');
    assert.equal(mark.label, 'в Syntony');
    assert.equal(mark.href, '');
    // Syntony не говорит, завела ли новую заявку или дописала комментарий в открытую, — подсказка
    // не обещает «заявку». Тег формы «Пропущенный входящий» стоит в обоих случаях (ответ Syntony
    // 26.11.2026) — по нему заявку и находят.
    assert.equal(mark.title, 'Передан в Syntony с тегом «Пропущенный входящий» — новой заявкой в очередь '
        + '«Продажи → Новая заявка» или комментарием в открытую заявку этого номера. '
        + 'За минуту после звонка клиенту не ответили');
});

test('сбой и «дозвонился» в Syntony называют Syntony', () => {
    const failed = amoMark({ amo_state: 'error', missed_crm: 'syntony', amo_note: '' });
    assert.equal(failed.label, 'не передан в Syntony');
    assert.equal(failed.title, 'Syntony не приняла заявку — перезвоните клиенту вручную');
    assert.equal(amoMark({ amo_state: 'error', missed_crm: 'syntony', amo_note: 'форма Syntony не найдена (HTTP 404)' }).title,
                 'форма Syntony не найдена (HTTP 404)');
    assert.equal(amoAnsweredNote({ amo_state: 'answered', missed_crm: 'syntony', amo_note: 'Клиент перезвонил в 10:01:17 и поговорил с оператором' }),
                 'В Syntony не передан: клиент перезвонил в 10:01:17 и поговорил с оператором');
});

test('сделка amoCRM остаётся ссылкой и рядом с переездом', () => {
    const mark = amoMark({ amo_state: 'sent', missed_crm: 'amo', amo_lead_id: 1, amo_url: 'https://example.amocrm.ru/leads/detail/1' });
    assert.equal(mark.label, 'в amoCRM');
    assert.equal(mark.href, 'https://example.amocrm.ru/leads/detail/1');
});

test('подпись под «Входящих»: сколько ушло в какую CRM', () => {
    assert.equal(missedHint({ incoming_missed: 12 }), 'не приняли 12');
    assert.equal(missedHint({ incoming_missed: 12, amo_transferred: 9 }), 'не приняли 12 · в amoCRM 9');
    assert.equal(missedHint({ incoming_missed: 1200, syntony_transferred: 1040 }),
                 `не приняли ${(1200).toLocaleString('ru-RU')} · в Syntony ${(1040).toLocaleString('ru-RU')}`);
    assert.equal(missedHint({ incoming_missed: 30, syntony_transferred: 7, amo_transferred: 20 }),
                 'не приняли 30 · в Syntony 7 · в amoCRM 20');
    assert.equal(missedHint(null), 'не приняли 0');
});

test('фильтр «Только переданные в CRM» — только там, где он может что-то найти', () => {
    assert.equal(missedFilterShown({ missed_amo: { enabled: true } }, {}, {}), true);
    assert.equal(missedFilterShown({}, { amo_transferred: 2 }, {}), true);
    // Робот выключен, а передачи за период были только в Syntony.
    assert.equal(missedFilterShown({ missed_amo: { enabled: false } }, { syntony_transferred: 3 }, {}), true);
    assert.equal(missedFilterShown(null, {}, { amoOnly: true }), true);
    assert.equal(missedFilterShown({ missed_amo: { enabled: false } }, {}, {}), false);
    assert.equal(missedFilterShown(undefined, undefined, undefined), false);
});
