// Правила блока «Мои данные» (задача #357): кому показывать и что уходит на сервер.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    ERROR_CARD,
    ERROR_PHONE,
    ERROR_TELEGRAM,
    buildChanges,
    canEditOwnData,
    canonicalCourse,
    courseLabel,
    draftFromData,
    formatCardInput,
    formatPhone,
    normalizePhone,
    normalizeTelegram,
} from '../src/components/profile/myData.js';

const DATA = {
    phone: '+77011234567',
    telegram_nick: 'legacy_nick',
    has_card: true,
    card_last4: '5678',
    study_place: 'КазНУ',
    study_specialty: 'Экономика',
    study_course: '3 курс',
};

test('блок видят только операторы СЗоВ, ОП и Тез', () => {
    for (const code of ['szov', 'op', 'tez', 'SZOV']) {
        assert.equal(canEditOwnData({ role: 'operator', department_code: code }), true, code);
    }
    assert.equal(canEditOwnData({ role: 'trainee', department_code: 'szov' }), false);
    assert.equal(canEditOwnData({ role: 'sv', department_code: 'szov' }), false);
    assert.equal(canEditOwnData({ role: 'operator', department_code: 'front_office' }), false);
    assert.equal(canEditOwnData({ role: 'operator' }), false);
    assert.equal(canEditOwnData(null), false);
});

test('телефон приводится к +7XXXXXXXXXX и показывается группами', () => {
    for (const raw of ['8 701 123 45 67', '+7 (701) 123-45-67', '7011234567']) {
        assert.deepEqual(normalizePhone(raw), { value: '+77011234567', error: null }, raw);
    }
    assert.equal(normalizePhone('+7 701').error, ERROR_PHONE);
    assert.equal(formatPhone('+77011234567'), '+7 701 123 45 67');
    assert.equal(formatPhone('87011234567'), '87011234567');
});

test('ник Telegram: одна «@», ссылка t.me разворачивается', () => {
    assert.deepEqual(normalizeTelegram('https://t.me/operator_one'), { value: '@operator_one', error: null });
    assert.deepEqual(normalizeTelegram('@@operator_one'), { value: '@operator_one', error: null });
    assert.equal(normalizeTelegram('abc').error, ERROR_TELEGRAM);
});

test('номер карты набирается группами по четыре; лишние цифры не срезаются молча', () => {
    assert.equal(formatCardInput('4400-43'), '4400 43');
    // 17-я цифра остаётся на виду — иначе опечатка сохранилась бы чужим номером.
    assert.equal(formatCardInput('44004301123456789'), '4400 4301 1234 5678 9');
    const draft = { ...draftFromData(DATA), card_number: formatCardInput('44004301123456789') };
    const { payload, errors } = buildChanges(DATA, draft);
    assert.equal(errors.card_number, ERROR_CARD);
    assert.equal('card_number' in payload, false);
});

test('черновик: карта всегда пустая — полного номера у интерфейса нет', () => {
    assert.equal(draftFromData(DATA).card_number, '');
});

test('нетронутые старые записи не переписываются сохранением соседнего поля', () => {
    const draft = { ...draftFromData(DATA), study_place: 'КБТУ' };
    const { payload, errors } = buildChanges(DATA, draft);
    assert.deepEqual(errors, {});
    assert.deepEqual(payload, { study_place: 'КБТУ' });
});

test('карта уходит только набранной целиком', () => {
    const empty = buildChanges(DATA, draftFromData(DATA));
    assert.deepEqual(empty.payload, {});

    const short = buildChanges(DATA, { ...draftFromData(DATA), card_number: '4400 4301 1234 567' });
    assert.equal(short.errors.card_number, ERROR_CARD);
    assert.equal('card_number' in short.payload, false);

    const full = buildChanges(DATA, { ...draftFromData(DATA), card_number: '4400 4301 1234 5678' });
    assert.deepEqual(full.payload, { card_number: '4400430112345678' });
});

test('стёрли университет — специальность не отправляется, её сотрёт сервер', () => {
    const draft = { ...draftFromData(DATA), study_place: '', study_specialty: 'Право' };
    assert.deepEqual(buildChanges(DATA, draft).payload, { study_place: null });
});

test('старая запись «3 курс» — это пункт «3», а не второй «3 курс» в списке', () => {
    assert.equal(canonicalCourse('3 курс'), '3');
    assert.equal(canonicalCourse('3'), '3');
    assert.equal(canonicalCourse('курс 4'), 'курс 4');
    assert.equal(canonicalCourse(''), '');
    assert.equal(draftFromData(DATA).study_course, '3');
    // Не тронутый курс не отправляется, даже если в базе он записан по-старому.
    assert.deepEqual(buildChanges(DATA, draftFromData(DATA)).payload, {});
    const picked = buildChanges(DATA, { ...draftFromData(DATA), study_course: '4' });
    assert.deepEqual(picked.payload, { study_course: '4' });
});

test('курс: цифра подписывается «N курс», пустой выбор стирает', () => {
    assert.equal(courseLabel('3'), '3 курс');
    assert.equal(courseLabel('Магистратура, 1 курс'), 'Магистратура, 1 курс');
    const { payload } = buildChanges(DATA, { ...draftFromData(DATA), study_course: '' });
    assert.deepEqual(payload, { study_course: null });
});

test('ошибка ника — у своего поля, остальные правки собраны', () => {
    const draft = { ...draftFromData(DATA), telegram_nick: 'ab', phone: '87019998877' };
    const { payload, errors } = buildChanges(DATA, draft);
    assert.equal(errors.telegram_nick, ERROR_TELEGRAM);
    assert.equal(payload.phone, '+77019998877');
});
