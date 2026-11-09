import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    GUEST_PRESETS,
    STATUS_META,
    bannerText,
    clampDate,
    daysLeftLabel,
    deadlineLabel,
    fmtDate,
    fmtDeadline,
    fmtTime,
    isEndOfDay,
    isPermanent,
    plural,
    presetLabel,
    presetsWithin,
    targetLabel,
    urgency,
} from '../src/components/wiki/guestAccess.js';
import { auditFacts } from '../src/components/wiki/auditEvents.js';

test('дата режется строкой, а не разбирается в Date', () => {
    // Сервер отдаёт наивное алматинское время без зоны. new Date() разберёт его
    // как локальное, и западнее Алматы «до 5 сентября» покажется истёкшим
    // четвёртого. Проверяем именно ту форму, в какой приходит ответ.
    assert.equal(fmtDate('2026-09-05T23:59:59'), '05.09.2026');
    assert.equal(fmtDate('2026-01-01T00:00:00'), '01.01.2026');
    assert.equal(fmtDate('2026-09-05'), '05.09.2026');
});

test('битую дату подписью не выдаём', () => {
    // Пустая подпись честнее «Invalid Date» и не ломает строку списка.
    for (const value of [null, undefined, '', 'вчера', '05.09.2026']) {
        assert.equal(fmtDate(value), '');
    }
});

test('в модуле нет ни одного new Date', () => {
    // Страж на главное правило файла: любая дата, разобранная в Date, уедет на
    // сутки у части людей — и уедет молча, только у них.
    const source = readFileSync(
        new URL('../src/components/wiki/guestAccess.js', import.meta.url), 'utf8');
    // Комментарии вычёркиваем: в шапке модуля ловушка разобрана словами и
    // ПОКАЗАНА кодом — сторожить надо исполняемые строки, а не объяснение.
    const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    assert.equal(/new Date\(/.test(code), false);
    assert.equal(/Date\.now\(/.test(code), false);
});

test('ноль дней — это «сегодня последний», а не «истёк»', () => {
    // Срок живёт до конца дня (wiki/guests.py: resolve_expiry). Скажи мы
    // «истёк», человек пошёл бы просить продление в день, когда доступ работает.
    assert.equal(daysLeftLabel(0), 'сегодня последний день');
    assert.equal(daysLeftLabel(1), 'остался 1 день');
    assert.equal(daysLeftLabel(2), 'осталось 2 дня');
    assert.equal(daysLeftLabel(5), 'осталось 5 дней');
    assert.equal(daysLeftLabel(11), 'осталось 11 дней');
    assert.equal(daysLeftLabel(14), 'осталось 14 дней');
});

test('истёкший срок считается в прошедшем времени', () => {
    assert.equal(daysLeftLabel(-1), 'истёк 1 день назад');
    assert.equal(daysLeftLabel(-3), 'истёк 3 дня назад');
    assert.equal(daysLeftLabel(null), '');
});

test('склонение по-русски, включая одиннадцать', () => {
    assert.equal(plural(1, 'день', 'дня', 'дней'), 'день');
    assert.equal(plural(11, 'день', 'дня', 'дней'), 'дней');
    assert.equal(plural(21, 'день', 'дня', 'дней'), 'день');
    assert.equal(presetLabel(1), '1 день');
    assert.equal(presetLabel(3), '3 дня');
    assert.equal(presetLabel(14), '14 дней');
});

test('тон срочности загорается за два дня до конца', () => {
    // Единственный момент, когда строку в списке надо заметить: дальше её либо
    // продлевают, либо она исчезнет.
    assert.equal(urgency(7), 'calm');
    assert.equal(urgency(3), 'calm');
    assert.equal(urgency(2), 'soon');
    assert.equal(urgency(0), 'soon');
    assert.equal(urgency(-1), 'gone');
});

test('предустановки не выходят за потолок сервера', () => {
    // Потолок приезжает из ответа (max_days), а не зашит во фронт: разойдись
    // они, форма предложила бы срок, который сервер отклонит.
    assert.deepEqual(presetsWithin(14), GUEST_PRESETS);
    assert.deepEqual(presetsWithin(7), [0, 1, 3, 7]);
    assert.deepEqual(presetsWithin(1), [0, 1]);
});

test('«сегодня» — тоже срок, и он нужен ради часа', () => {
    // Без нуля ближайший пресет — завтрашний день, и «сегодня до 18:00»
    // пришлось бы набирать датой. Ноль под любым потолком проходит.
    assert.equal(GUEST_PRESETS[0], 0);
    assert.equal(presetLabel(0), 'сегодня');
    assert.ok(presetsWithin(1).includes(0));
});

test('срок с часом читается вместе с часом, без часа — без него', () => {
    // Конец дня — умолчание, и «до 05.09.2026, 23:59» это тот же «до 5
    // сентября», только с шумом. А названный час и есть то, что человек выбрал.
    assert.equal(fmtDeadline('2026-09-05T23:59:59'), '05.09.2026');
    assert.equal(fmtDeadline('2026-08-25T18:00:00'), '25.08.2026, 18:00');
    assert.equal(fmtDeadline(''), '');
});

test('час режется строкой — и здесь тоже без Date', () => {
    assert.equal(fmtTime('2026-08-25T18:00:00'), '18:00');
    assert.equal(fmtTime('2026-08-25T09:05:00'), '09:05');
    assert.equal(fmtTime('2026-08-25'), '');
    assert.equal(isEndOfDay('2026-08-25T23:59:59'), true);
    assert.equal(isEndOfDay('2026-08-25T18:00:00'), false);
});

test('последний день с часом называет час, а не «до полуночи»', () => {
    // «Сегодня последний день» у выдачи до 18:00 звучит как «до полуночи», и
    // человек рассчитает время неверно — придёт читать в 19:00.
    assert.equal(daysLeftLabel(0, '2026-08-25T18:00:00'), 'сегодня до 18:00');
    assert.equal(daysLeftLabel(0, '2026-08-25T23:59:59'), 'сегодня последний день');
    // Дальше первого дня час уже не важен: там счёт идёт днями.
    assert.equal(daysLeftLabel(3, '2026-08-28T18:00:00'), 'осталось 3 дня');
});

test('дата подтягивается к границе — пресеты панели про min/max не знают', () => {
    // Ловушка из OfficeDayModal.jsx: кнопка «Сегодня» внутри IosDatePicker
    // отдаёт день мимо неактивных клеток.
    assert.equal(clampDate('2026-08-20', '2026-08-25', '2026-09-08'), '2026-08-25');
    assert.equal(clampDate('2026-09-30', '2026-08-25', '2026-09-08'), '2026-09-08');
    assert.equal(clampDate('2026-09-01', '2026-08-25', '2026-09-08'), '2026-09-01');
    assert.equal(clampDate('', '2026-08-25', '2026-09-08'), '');
});

test('подпись объекта говорит про подразделы', () => {
    // «Раздел» и «раздел со всем, что внутри» — разный объём доступа, и
    // отличать их надо в списке, а не в форме, где выдачу уже сделали.
    assert.equal(targetLabel({ kind: 'section', title: 'Регламент',
                               include_subsections: true }), 'Регламент и подразделы');
    assert.equal(targetLabel({ kind: 'section', title: 'Регламент',
                               include_subsections: false }), 'Регламент');
    // У статьи подразделов нет — приписка была бы неправдой.
    assert.equal(targetLabel({ kind: 'article', title: 'Тарифы',
                               include_subsections: true }), 'Тарифы');
});

test('выдача пространства называет себя пространством', () => {
    // «Тез КЦ» в списке выдач неотличимо от одноимённого раздела, а разница
    // между «открыт раздел» и «открыта вся вика клиента» — это ровно то, ради
    // чего на список и смотрят.
    assert.equal(
        targetLabel({ kind: 'space', title: 'Тез КЦ' }),
        'Пространство «Тез КЦ» целиком');
    // Имя берётся и из space_name: /ping отдаёт выдачу получателю именно так.
    assert.equal(
        targetLabel({ kind: 'space', space_name: 'iGroup' }),
        'Пространство «iGroup» целиком');
    // «И подразделы» к пространству не приклеивается: раскрывать в нём нечего,
    // а флаг на строке пространства сервер и не пишет.
    assert.equal(
        targetLabel({ kind: 'space', title: 'Тез КЦ', include_subsections: true }),
        'Пространство «Тез КЦ» целиком');
});

test('баннер выдачи на пространство говорит о пространстве', () => {
    const banner = bannerText([{
        kind: 'space', title: 'Тез КЦ',
        expires_at: '2026-09-18T23:59:59', days_left: 7,
    }]);
    assert.equal(banner.title, 'Гостевой доступ: Пространство «Тез КЦ» целиком');
    assert.equal(banner.detail, 'до 18.09.2026 · осталось 7 дней');
});

test('баннер одной выдачи называет, что именно открыто', () => {
    const banner = bannerText([{
        kind: 'section', title: 'Регламент СЗоВ', include_subsections: true,
        expires_at: '2026-09-05T23:59:59', days_left: 3,
    }]);
    assert.equal(banner.title, 'Гостевой доступ: Регламент СЗоВ и подразделы');
    assert.equal(banner.detail, 'до 05.09.2026 · осталось 3 дня');
});

test('баннер выдачи на пару часов называет час', () => {
    // Ради этого случая час и появился: «показать раздел на время созвона».
    const banner = bannerText([{
        kind: 'section', title: 'Регламент СЗоВ',
        expires_at: '2026-08-25T18:00:00', days_left: 0,
    }]);
    assert.equal(banner.detail, 'до 25.08.2026, 18:00 · сегодня до 18:00');
    assert.equal(banner.urgency, 'soon');
});

test('баннер нескольких выдач берёт БЛИЖАЙШИЙ срок, а не первый в ответе', () => {
    // Сервер отдаёт отсортированным, но полагаться на порядок нельзя:
    // сортировку однажды поменяют ради списка, и баннер молча начнёт показывать
    // самый дальний срок — то есть обещать доступ, который кончится раньше.
    const banner = bannerText([
        { kind: 'section', title: 'Дальний', expires_at: '2026-09-08T23:59:59', days_left: 14 },
        { kind: 'article', title: 'Ближний', expires_at: '2026-08-26T23:59:59', days_left: 1 },
    ]);
    assert.equal(banner.title, 'Гостевой доступ: 2 выдачи');
    assert.equal(banner.detail, 'ближайшая — до 26.08.2026 · остался 1 день');
    assert.equal(banner.urgency, 'soon');
});

test('без выдач баннера нет вовсе', () => {
    // Пустая полоса «гостевого доступа нет» — ровно тот шум, которого в разделе
    // быть не должно.
    assert.equal(bannerText([]), null);
    assert.equal(bannerText(null), null);
    assert.equal(bannerText(undefined), null);
});

test('состояния выдачи совпадают с серверными', () => {
    // Зеркало guests.grant_status: разойдись ключи — фильтр «Отозванные» стал бы
    // показывать пустой список на непустых данных.
    const python = readFileSync(new URL('../wiki/guests.py', import.meta.url), 'utf8');
    for (const key of Object.keys(STATUS_META)) {
        assert.ok(python.includes(`return '${key}'`), `нет статуса ${key} на сервере`);
    }
    assert.deepEqual(Object.keys(STATUS_META).sort(), ['active', 'expired', 'revoked']);
});

/* Бессрочная выдача (решение владельца 09.11.2026, wiki/guests.py:
 * PERMANENT_EXPIRES_AT). Сервер присылает признак permanent и НЕ присылает
 * дату: метка 9999-12-31 — способ сказать «никогда», а не срок. */
const PERMANENT_SPACE = {
    kind: 'space', title: 'Таксопарки', permanent: true, expires_at: null, days_left: null,
};

test('бессрочность узнаётся по признаку сервера, а не по дате', () => {
    assert.equal(isPermanent(PERMANENT_SPACE), true);
    assert.equal(isPermanent({ permanent: false, expires_at: '2026-09-05T23:59:59' }), false);
    // Без признака — обычная выдача, даже с дальней датой: угадывать по году
    // значило бы завести второй источник истины о сроке.
    assert.equal(isPermanent({ expires_at: '9999-12-31T23:59:59' }), false);
    assert.equal(isPermanent(null), false);
    assert.equal(isPermanent(undefined), false);
});

test('в списке выдач бессрочная читается словом, а не датой', () => {
    assert.equal(deadlineLabel(PERMANENT_SPACE), 'бессрочно');
    assert.equal(deadlineLabel({ expires_at: '2026-09-05T23:59:59' }), 'до 05.09.2026');
    assert.equal(deadlineLabel({ expires_at: '2026-08-25T18:00:00' }), 'до 25.08.2026, 18:00');
    // Подпись остатка у бессрочной пустая — второй строки под «бессрочно» нет.
    assert.equal(daysLeftLabel(PERMANENT_SPACE.days_left, PERMANENT_SPACE.expires_at), '');
    assert.equal(urgency(PERMANENT_SPACE.days_left), 'calm');
});

test('только бессрочные выдачи — баннера нет', () => {
    // Сообщать нечего: срока нет, а полоса «доступ есть» каждый день — шум.
    assert.equal(bannerText([PERMANENT_SPACE]), null);
    assert.equal(bannerText([PERMANENT_SPACE, { ...PERMANENT_SPACE, title: 'Тез' }]), null);
});

test('рядом со срочной баннер говорит только о срочной', () => {
    const banner = bannerText([
        PERMANENT_SPACE,
        { kind: 'section', title: 'Регламент СЗоВ', include_subsections: false,
          expires_at: '2026-09-05T23:59:59', days_left: 3 },
    ]);
    assert.equal(banner.title, 'Гостевой доступ: Регламент СЗоВ');
    assert.equal(banner.detail, 'до 05.09.2026 · осталось 3 дня');
});

test('журнал называет бессрочную выдачу словом, а не датой 31.12.9999', () => {
    const facts = auditFacts({
        action: 'guest.extend',
        details: { user_name: 'Сотрудник', permanent: true,
                   expires_at: '9999-12-31T23:59:59', reason: 'по решению владельца' },
    });
    assert.deepEqual(facts, ['Сотрудник', 'бессрочно', 'по решению владельца']);
    // Обычная выдача — по-прежнему с датой.
    assert.deepEqual(
        auditFacts({ action: 'guest.grant',
                     details: { user_name: 'Сотрудник', expires_at: '2026-09-05T23:59:59' } }),
        ['Сотрудник', 'до 05.09.2026']);
});

test('экран статьи и списка прячет срок бессрочной выдачи', () => {
    // Сами компоненты — модалка и статья с загрузкой по сети, серверный рендер
    // до подписи не доходит. Сторожим исходник: бейдж «до …» и остаток дней в
    // статье, кнопку «Продлить» в списке.
    const article = readFileSync(
        new URL('../src/components/wiki/WikiArticle.jsx', import.meta.url), 'utf8');
    assert.equal(
        (article.match(/article\.guest_access && !isPermanentGuest\(article\.guest_access\)/g)
            || []).length, 2);
    // «История» по-прежнему прячется у ЛЮБОЙ гостевой выдачи: сервер её гостю
    // не отдаёт (routes_edit._history_denied), бессрочный он или нет.
    assert.ok(article.includes('{!article.guest_access && ('));
    const list = readFileSync(
        new URL('../src/components/wiki/WikiGuests.jsx', import.meta.url), 'utf8');
    assert.ok(list.includes('{deadlineLabel(item)}'));
    assert.ok(/\{!isPermanent\(item\) && \(\s*<button[^>]*onClick=\{\(\) => onExtend\(item\)\}/.test(list));
});

test('потолок срока во фронте не зашит — он приезжает с сервера', () => {
    // Но предустановки обязаны в него укладываться: 14 — решение владельца
    // (schema.MAX_GUEST_DAYS), и пресет сверх него был бы кнопкой на отказ.
    const python = readFileSync(new URL('../wiki/schema.py', import.meta.url), 'utf8');
    const max = /MAX_GUEST_DAYS = (\d+)/.exec(python);
    assert.ok(max, 'MAX_GUEST_DAYS не найден в wiki/schema.py');
    assert.ok(Math.max(...GUEST_PRESETS) <= Number(max[1]));
});
