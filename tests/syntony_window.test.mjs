// Окно Syntony (ТЗ Syntony от 09.11.2026): логика экрана — src/components/syntony/syntonyRules.js.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    ERROR_VIEWS, KNOWN_ERROR_CODES, LEADS_MIN_WIDTH, SECTIONS, accessRowState, describeError, driverPhones,
    emailProblem, exportFileName, formatWhen, isWindowUrl, managerNote, peopleCount, portalTheme, readError,
    sameEmail, saveBlob, windowRequestBody,
} from '../src/components/syntony/syntonyRules.js';

test('два раздела: «Чаты» просят inbox, «Заявки» — leads и ширину от 768', () => {
    assert.deepEqual(Object.keys(SECTIONS), ['syntony_chats', 'syntony_leads']);
    assert.equal(SECTIONS.syntony_chats.scope, 'inbox');
    assert.equal(SECTIONS.syntony_chats.minWidth, undefined);
    assert.equal(SECTIONS.syntony_leads.scope, 'leads');
    assert.equal(SECTIONS.syntony_leads.minWidth, 768);
    assert.equal(LEADS_MIN_WIDTH, 768);
});

test('в запросе окна только scope, phones и theme — оператора и canWrite страница не шлёт', () => {
    assert.deepEqual(windowRequestBody({ scope: 'inbox', theme: 'light' }), { scope: 'inbox', theme: 'light' });
    assert.deepEqual(windowRequestBody({ scope: 'leads', theme: 'dark', phones: [] }), { scope: 'leads', theme: 'dark' });
    assert.deepEqual(windowRequestBody({ scope: 'phone', phones: ['+77010190219'], theme: 'neon' }),
        { scope: 'phone', theme: 'light', phones: ['+77010190219'] });
    assert.deepEqual(windowRequestBody({}), { scope: 'inbox', theme: 'light' });
    const body = windowRequestBody({ scope: 'inbox', partnerUserId: '1', partnerUserEmail: 'a@b.kz', canWrite: false });
    assert.deepEqual(Object.keys(body).sort(), ['scope', 'theme']);
});

test('ошибка читается по error.code; общий отказ портала — без кода', () => {
    assert.deepEqual(readError({ error: { code: ' operator_not_linked ', message: 'Не связан' } }),
        { code: 'operator_not_linked', message: 'Не связан', retryAfter: null });
    assert.deepEqual(readError({ error: { code: 'rate_limited', message: 'x', details: { retryAfter: 2.2 } } }),
        { code: 'rate_limited', message: 'x', retryAfter: 3 });
    for (const data of [{ error: 'Unauthorized' }, { error: null }, {}, null, undefined, 'text']) {
        assert.deepEqual(readError(data), { code: '', message: '', retryAfter: null });
    }
});

test('оператор видит текст по коду, а не текст Syntony', () => {
    const view = describeError({ code: 'operator_not_linked', message: 'Operator is not linked' });
    assert.equal(view.text, 'Ваша учётная запись ещё не подключена к Syntony. Обратитесь к администратору.');
    assert.equal(view.note, '');
    // «Открыть заново» экран предлагает при любой ошибке — отдельного признака у ошибки нет.
    assert.deepEqual(Object.keys(view).sort(), ['code', 'note', 'text']);
    assert.match(describeError({ code: 'operator_lookup_unavailable' }).text, /через минуту/);
    assert.match(describeError({ code: 'icore_unreachable' }).text, /через минуту/);
    assert.equal(describeError({ code: 'connection_inactive' }).text,
        'Окно Syntony временно недоступно, обратитесь к администратору.');
});

test('причина на стороне ключа — оператору общий текст и код для администратора', () => {
    for (const code of ['invalid_key', 'key_revoked', 'key_expired', 'insufficient_scope', 'embed_disabled', 'connection_inactive']) {
        const view = describeError({ code });
        assert.equal(view.text, 'Окно Syntony временно недоступно, обратитесь к администратору.', code);
        assert.equal(view.note, `Код: ${code}`);
    }
    assert.equal(describeError({ code: 'icore_not_configured' }).note, 'Код: icore_not_configured');
    // Причина в самом операторе — код ему не показывают: он ничего не добавляет к тексту.
    for (const code of ['operator_not_linked', 'icore_no_access', 'icore_qr_required', 'rate_limited', 'transport_not_connected']) {
        assert.equal(describeError({ code }).note, '', code);
    }
});

test('незнакомый код и ошибка без кода — общее сообщение', () => {
    const unknown = describeError({ code: 'code_of_tomorrow', message: 'Something new' });
    assert.equal(unknown.text, 'Не удалось открыть окно Syntony. Попробуйте ещё раз.');
    assert.equal(unknown.note, 'Код: code_of_tomorrow');
    const bare = describeError({ code: '', message: '' });
    assert.equal(bare.text, 'Не удалось открыть окно Syntony. Попробуйте ещё раз.');
    assert.equal(bare.note, '');
    assert.equal(describeError(null).text, 'Не удалось открыть окно Syntony. Попробуйте ещё раз.');
});

test('«слишком много запросов» называет, через сколько повторить', () => {
    assert.match(describeError({ code: 'rate_limited', retryAfter: 1 }).text, /через 1 секунду\./);
    assert.match(describeError({ code: 'rate_limited', retryAfter: 3 }).text, /через 3 секунды\./);
    assert.match(describeError({ code: 'rate_limited', retryAfter: 12 }).text, /через 12 секунд\./);
    assert.match(describeError({ code: 'rate_limited', retryAfter: null }).text, /немного позже/);
});

test('ведущему список доступа сказано, что сделать самому', () => {
    const noAccess = describeError({ code: 'icore_no_access' }, { manager: true });
    assert.match(noAccess.text, /Добавьте себя кнопкой «Доступ»/);
    assert.equal(describeError({ code: 'icore_no_access' }).text, 'Окно Syntony вам не открыто.');
    assert.match(describeError({ code: 'invalid_key' }, { manager: true }).text, /Проверьте ключ на сервере/);
    assert.match(describeError({ code: 'icore_not_configured' }, { manager: true }).text, /не задан ключ Syntony/);
});

test('заметка над списком: что ответила Syntony, когда дело не в операторе', () => {
    assert.equal(managerNote('', 'x'), '');
    assert.match(managerNote('key_revoked'), /отозван/);
    assert.match(managerNote('invalid_request', 'Bad name'), /Bad name.*Проверьте ФИО и почту/);
    assert.match(managerNote('operator_lookup_unavailable'), /через минуту/);
    assert.match(managerNote('code_of_tomorrow', 'New'), /незнакомой ошибкой \(code_of_tomorrow\): New/);
});

test('тексты заведены на каждый код ТЗ и на каждый код портала', () => {
    const syntony = ['invalid_request', 'invalid_key', 'key_revoked', 'key_expired', 'insufficient_scope',
        'connection_inactive', 'operator_not_linked', 'transport_not_connected', 'rate_limited',
        'operator_lookup_unavailable', 'embed_disabled'];
    const icore = ['icore_no_access', 'icore_qr_required', 'icore_not_configured', 'icore_unreachable'];
    assert.deepEqual([...KNOWN_ERROR_CODES].sort(), [...syntony, ...icore].sort());
    for (const code of KNOWN_ERROR_CODES) assert.ok(ERROR_VIEWS[code].text.length > 10, code);
});

test('в src фрейма идёт только https-ссылка домена Syntony', () => {
    assert.equal(isWindowUrl('https://app.syntony.kz/ru/embed/one-time'), true);
    assert.equal(isWindowUrl('https://syntony.kz/embed/x'), true);
    for (const bad of ['javascript:alert(1)', 'http://app.syntony.kz/x', 'https://evil.example/x',
        'https://app.syntony.kz.evil.example/x', 'https://user:pw@app.syntony.kz/x', '//app.syntony.kz/x',
        'data:text/html,x', '', null, undefined, 5, {}]) {
        assert.equal(isWindowUrl(bad), false, String(bad));
    }
});

test('тема окна — по теме портала', () => {
    const root = (value) => ({ getAttribute: (name) => (name === 'data-otp-theme' ? value : null) });
    assert.equal(portalTheme(root('dark')), 'dark');
    assert.equal(portalTheme(root(null)), 'light');
    assert.equal(portalTheme(root('light')), 'light');
    assert.equal(portalTheme(null), 'light');
});

test('номера водителя: без повторов, только телефоны, не больше десяти', () => {
    assert.deepEqual(driverPhones('+7 701 019 02 19', '77010190219', '8 (701) 019-02-19', '+7  702 555 44 33'),
        ['+7 701 019 02 19', '+7 702 555 44 33']);
    // Номер уходит как записан: «8701…» остаётся «8701…», плюс не дописывается.
    assert.deepEqual(driverPhones('87010190219'), ['87010190219']);
    assert.deepEqual(driverPhones(null, undefined, '', 'driver_id_42', '12345', 'tg:77010190219'), []);
    assert.deepEqual(driverPhones(['+77010190219'], null), ['+77010190219']);
    const many = Array.from({ length: 14 }, (_, index) => `+7701000${String(index).padStart(4, '0')}`);
    assert.equal(driverPhones(...many).length, 10);
});

test('строка списка доступа: что мешает и что ответила Syntony', () => {
    const base = { working: true, email: 'a@icore.kz', link_state: 'unknown' };
    assert.deepEqual(accessRowState(base), { label: 'Не проверено', tone: 'slate', hint: '' });
    assert.equal(accessRowState({ ...base, link_state: 'linked' }).label, 'Подключён');
    assert.equal(accessRowState({ ...base, link_state: 'linked' }).tone, 'green');
    const waiting = accessRowState({ ...base, link_state: 'not_linked' });
    assert.equal(waiting.label, 'Ждёт связи');
    assert.match(waiting.hint, /администратор Syntony/);
    assert.equal(accessRowState({ ...base, email: '', link_state: 'not_linked' }).label, 'Нет почты');
    assert.equal(accessRowState({ ...base, working: false, link_state: 'linked' }).label, 'Уволен');
    assert.equal(accessRowState({ ...base, link_state: 'что-то новое' }).label, 'Не проверено');
});

test('почта в поле списка: пусто можно, не адрес — нельзя', () => {
    assert.equal(emailProblem(''), '');
    assert.equal(emailProblem('  '), '');
    assert.equal(emailProblem('aigerim@example.kz'), '');
    for (const bad of ['нет', 'a@b', 'a b@c.kz', 'a@@b.kz', '@b.kz']) {
        assert.equal(emailProblem(bad), 'Проверьте адрес почты', bad);
    }
    assert.equal(sameEmail(' Anna@ICORE.kz', 'anna@icore.kz '), true);
    assert.equal(sameEmail('anna@icore.kz', 'anna@syntony.kz'), false);
    assert.equal(sameEmail(null, ''), true);
});

test('подписи: время, число людей, имя файла', () => {
    assert.equal(formatWhen('2026-11-11T14:05:09'), '11.11.2026 14:05');
    assert.equal(formatWhen(null), '');
    assert.equal(peopleCount(1), '1 человек');
    assert.equal(peopleCount(3), '3 человека');
    assert.equal(peopleCount(5), '5 человек');
    assert.equal(peopleCount(12), '12 человек');
    assert.equal(peopleCount(22), '22 человека');
    assert.equal(exportFileName(new Date(2026, 10, 11)), 'Операторы iCORE для Syntony 11.11.2026.xlsx');
});

test('файл сохраняется временной ссылкой в этом же окне', () => {
    const clicks = [];
    const doc = {
        body: { appendChild: (node) => clicks.push(['append', node.download]) },
        createElement: () => ({ click() { clicks.push(['click', this.href]); }, remove() { clicks.push(['remove']); } }),
    };
    const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };
    URL.createObjectURL = () => 'blob:test';
    URL.revokeObjectURL = () => {};
    try {
        assert.equal(saveBlob({}, 'file.xlsx', doc), true);
        assert.deepEqual(clicks, [['append', 'file.xlsx'], ['click', 'blob:test'], ['remove']]);
        assert.equal(saveBlob({}, 'file.xlsx', null), false);
    } finally {
        URL.createObjectURL = original.create;
        URL.revokeObjectURL = original.revoke;
    }
});
