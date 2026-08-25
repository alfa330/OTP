/*
 * Черновик конструктора опроса.
 *
 * Что защищаем. Форма создания опроса жила только в памяти вкладки, и любая
 * перезагрузка — в том числе та, которую приложение делает само, когда не
 * удалось обновить токен, — стирала работу целиком. Теперь незаконченный
 * черновик лежит в localStorage, а при следующем нажатии «Создать опрос»
 * человека спрашивают, продолжать его или начинать заново.
 *
 * Тихо сломаться тут может ровно две вещи: снимок вернётся в форму НЕ в той
 * форме, какую держит конструктор (и упадёт на первом клике), и напоминание
 * начнёт всплывать без повода — на пустой, чужой или протухшей заготовке.
 * Обе стороны здесь и сторожатся.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    SURVEY_DRAFT_STORAGE_VERSION,
    SURVEY_DRAFT_TTL_MS,
    buildDraftRecord,
    clearSurveyDraft,
    describeDraftRecord,
    isDraftMeaningful,
    normalizeDraft,
    parseDraftRecord,
    readSurveyDraft,
    surveyDraftStorageKey,
    writeSurveyDraft,
} from '../src/components/surveys/builderDraft.js';

// Идентификаторы вопросов в конструкторе случайные — в тестах делаем их
// предсказуемыми, иначе deepEqual сравнивать не с чем.
const makeIds = () => {
    let n = 0;
    return () => `q_test_${(n += 1)}`;
};

// Ровно то, что строит emptyDraft() в SurveysView.jsx: если формы разойдутся,
// восстановленный черновик начнёт падать на первом же клике по вопросу.
const EMPTY_DRAFT = {
    title: '',
    description: '',
    isTest: false,
    directionIds: [],
    groupIds: [],
    tenureWeeksMin: '',
    tenureWeeksMax: '',
    operatorIds: [],
    questions: [{
        id: 'q_test_1',
        text: '',
        type: 'single',
        required: true,
        allowOther: false,
        options: ['', ''],
        correctOptions: [],
        points: '1',
        partialCredit: false,
    }],
    startsAt: '',
    endsAt: '',
    singleAttempt: true,
    affectsQuality: false,
};

const NOW = new Date(2026, 7, 25, 14, 32).getTime();

/* ─── Форма черновика ─── */

test('пустой и битый снимок дают ту же форму, что emptyDraft()', () => {
    assert.deepEqual(normalizeDraft(null, { makeId: makeIds() }), EMPTY_DRAFT);
    assert.deepEqual(normalizeDraft({}, { makeId: makeIds() }), EMPTY_DRAFT);
    assert.deepEqual(normalizeDraft('строка вместо объекта', { makeId: makeIds() }), EMPTY_DRAFT);
    assert.deepEqual(
        normalizeDraft({ questions: [], directionIds: null, operatorIds: 'нет' }, { makeId: makeIds() }),
        EMPTY_DRAFT,
    );
});

test('лишние поля из снимка в форму не попадают', () => {
    const draft = normalizeDraft(
        { title: 'Аттестация', droppedField: 1, questions: [{ text: 'Вопрос', droppedToo: true }] },
        { makeId: makeIds() },
    );
    assert.deepEqual(Object.keys(draft).sort(), Object.keys(EMPTY_DRAFT).sort());
    assert.deepEqual(Object.keys(draft.questions[0]).sort(), Object.keys(EMPTY_DRAFT.questions[0]).sort());
});

test('типы полей приводятся к тем, которых ждёт форма', () => {
    const draft = normalizeDraft({
        title: 42,
        directionIds: [1, '2', '', null],
        groupIds: ['7', 'мусор', 9, 9],
        operatorIds: ['15', 15, 'нет'],
        tenureWeeksMin: 3,
        singleAttempt: false,
        affectsQuality: 'да',
    }, { makeId: makeIds() });

    assert.equal(draft.title, '42');
    assert.deepEqual(draft.directionIds, ['1', '2'], 'направления конструктор держит строками');
    assert.deepEqual(draft.groupIds, [7, 9], 'группы — числа, дубли не нужны');
    assert.deepEqual(draft.operatorIds, [15]);
    assert.equal(draft.tenureWeeksMin, '3', 'поле ввода стажа — строка');
    assert.equal(draft.singleAttempt, false);
    assert.equal(draft.affectsQuality, false, 'галочка включается только явным true');
});

test('в тесте не бывает рейтинга и «только Другое» — снимок их не протащит', () => {
    const draft = normalizeDraft({
        isTest: true,
        questions: [
            { text: 'Оцените', type: 'rating', options: [] },
            { text: 'Свободный ответ', type: 'other_only', allowOther: true },
        ],
    }, { makeId: makeIds() });

    assert.deepEqual(draft.questions.map((q) => q.type), ['single', 'single']);
    assert.deepEqual(draft.questions[0].options, ['', ''], 'вариантов не было — форма даёт две пустые строки');
    assert.equal(draft.questions[1].allowOther, false, 'в тесте «Другое» выключено');
});

test('вне теста рейтинг и «только Другое» остаются без вариантов ответа', () => {
    const draft = normalizeDraft({
        questions: [
            { text: 'Оцените', type: 'rating', options: ['1', '2'] },
            { text: 'Что улучшить', type: 'other_only', options: ['лишнее'] },
        ],
    }, { makeId: makeIds() });

    assert.deepEqual(draft.questions[0].options, []);
    assert.deepEqual(draft.questions[1].options, []);
    assert.equal(draft.questions[1].allowOther, true, '«только Другое» иначе теряет смысл');
});

test('верные варианты не переживают удаление самих вариантов', () => {
    const draft = normalizeDraft({
        isTest: true,
        questions: [
            { text: 'Один', type: 'single', options: ['Да', 'Нет'], correctOptions: ['Да', 'Нет', 'Ушёл'] },
            { text: 'Много', type: 'multiple', options: ['A', 'B'], correctOptions: ['B', 'C'], partialCredit: true },
        ],
    }, { makeId: makeIds() });

    assert.deepEqual(draft.questions[0].correctOptions, ['Да'], 'у «одного варианта» верный ровно один');
    assert.deepEqual(draft.questions[1].correctOptions, ['B'], '«C» из вариантов убрали — верным он быть не может');
    assert.equal(draft.questions[1].partialCredit, true);
    assert.equal(draft.questions[0].partialCredit, false, 'частичный зачёт живёт только у «нескольких вариантов»');
});

test('вопрос без идентификатора его получает — иначе список вопросов схлопнется', () => {
    const draft = normalizeDraft({ questions: [{ text: 'Раз' }, { text: 'Два' }] }, { makeId: makeIds() });
    assert.deepEqual(draft.questions.map((q) => q.id), ['q_test_1', 'q_test_2']);

    const kept = normalizeDraft({ questions: [{ id: 17, text: 'Раз' }, { id: 'q_abc', text: 'Два' }] }, { makeId: makeIds() });
    assert.deepEqual(kept.questions.map((q) => q.id), [17, 'q_abc'], 'свои id вопросов не переписываем');
});

/* ─── О чём стоит напоминать ─── */

test('пустая форма и одни тумблеры поводом для напоминания не считаются', () => {
    assert.equal(isDraftMeaningful(normalizeDraft(null, { makeId: makeIds() })), false);
    assert.equal(
        isDraftMeaningful(normalizeDraft({ isTest: true, singleAttempt: false, affectsQuality: true }, { makeId: makeIds() })),
        false,
        'открыл конструктор, щёлкнул тумблером и ушёл — напоминать не о чем',
    );
});

test('любая ручная работа делает черновик достойным напоминания', () => {
    const cases = {
        'название': { title: 'Аттестация' },
        'описание': { description: 'Раз в квартал' },
        'направление': { directionIds: ['3'] },
        'группа': { groupIds: [8] },
        'операторы': { operatorIds: [15, 16] },
        'стаж': { tenureWeeksMin: '4' },
        'окно теста': { isTest: true, startsAt: '2026-08-25T10:00' },
        'второй вопрос': { questions: [{ text: '' }, { text: '' }] },
        'текст вопроса': { questions: [{ text: 'Что улучшить?' }] },
        'вариант ответа': { questions: [{ text: '', options: ['Да', ''] }] },
    };
    for (const [label, patch] of Object.entries(cases)) {
        assert.equal(isDraftMeaningful(normalizeDraft(patch, { makeId: makeIds() })), true, label);
    }
});

/* ─── Что лежит в хранилище ─── */

const storedDraft = (patch = {}, savedAt = NOW - 60_000) => JSON.stringify(
    buildDraftRecord({ draft: { title: 'Аттестация', ...patch }, savedAt }),
);

test('свежий снимок разбирается и приходит с формой конструктора', () => {
    const record = parseDraftRecord(storedDraft(), { now: NOW, makeId: makeIds() });
    assert.ok(record);
    assert.equal(record.version, SURVEY_DRAFT_STORAGE_VERSION);
    assert.equal(record.draft.title, 'Аттестация');
    assert.equal(record.draft.questions.length, 1);
    assert.equal(record.repeatSourceSurveyId, null);
});

test('повтор остаётся повтором — иначе создастся новый опрос вместо запуска', () => {
    const raw = JSON.stringify(buildDraftRecord({
        draft: { title: 'Обратная связь' },
        repeatSourceSurveyId: '43',
        savedAt: NOW,
    }));
    assert.equal(parseDraftRecord(raw, { now: NOW, makeId: makeIds() }).repeatSourceSurveyId, 43);
});

test('чему нельзя доверять — то и не предлагаем', () => {
    const bad = {
        'пусто': null,
        'не JSON': '{сломано',
        'не объект': '"строка"',
        'чужая версия': JSON.stringify({ version: 99, savedAt: NOW, draft: { title: 'Аттестация' } }),
        'без времени': JSON.stringify({ version: SURVEY_DRAFT_STORAGE_VERSION, draft: { title: 'Аттестация' } }),
        'пустая форма': storedDraft({ title: '' }),
    };
    for (const [label, raw] of Object.entries(bad)) {
        assert.equal(parseDraftRecord(raw, { now: NOW, makeId: makeIds() }), null, label);
    }
});

test('протухший снимок молчит, а недельной давности — ещё нет', () => {
    const stale = storedDraft({}, NOW - SURVEY_DRAFT_TTL_MS - 1);
    assert.equal(parseDraftRecord(stale, { now: NOW, makeId: makeIds() }), null);

    const fresh = storedDraft({}, NOW - SURVEY_DRAFT_TTL_MS + 1000);
    assert.ok(parseDraftRecord(fresh, { now: NOW, makeId: makeIds() }));

    // Переведённые часы или чужая машина: снимок «из будущего» не выбрасываем.
    const future = storedDraft({}, NOW + 60 * 60 * 1000);
    assert.ok(parseDraftRecord(future, { now: NOW, makeId: makeIds() }));
});

test('ключ свой у каждого сотрудника — за одним браузером сидит смена', () => {
    assert.notEqual(surveyDraftStorageKey(15), surveyDraftStorageKey(16));
    assert.ok(surveyDraftStorageKey(15).endsWith(':15'));
    assert.equal(surveyDraftStorageKey(null), surveyDraftStorageKey(undefined));
});

/* ─── Текст напоминания ─── */

test('в напоминании человек узнаёт свой черновик, а не «несохранённые данные»', () => {
    const record = parseDraftRecord(JSON.stringify(buildDraftRecord({
        draft: {
            title: 'Аттестация СЗоВ',
            isTest: true,
            operatorIds: [15, 16, 17],
            questions: [{ text: 'Раз' }, { text: 'Два' }],
        },
        savedAt: NOW,
    })), { now: NOW, makeId: makeIds() });

    const info = describeDraftRecord(record);
    assert.equal(info.isTest, true);
    assert.equal(info.headline, 'Вы уже начинали создавать тест «Аттестация СЗоВ»');
    assert.equal(info.summary, '2 вопроса · 3 оператора · сохранено 25 августа, 14:32');
});

test('опрос называется опросом, повтор — повтором, безымянный черновик узнаваем', () => {
    const survey = describeDraftRecord(parseDraftRecord(storedDraft({ title: 'Обратная связь' }), { now: NOW, makeId: makeIds() }));
    assert.equal(survey.headline, 'Вы уже начинали создавать опрос «Обратная связь»');

    const repeat = describeDraftRecord(parseDraftRecord(JSON.stringify(buildDraftRecord({
        draft: { title: 'Обратная связь' },
        repeatSourceSurveyId: 43,
        savedAt: NOW,
    })), { now: NOW, makeId: makeIds() }));
    assert.equal(repeat.isRepeat, true);
    assert.equal(repeat.headline, 'Вы уже начинали повтор — опрос «Обратная связь»');

    const untitled = describeDraftRecord(parseDraftRecord(storedDraft({ title: '', questions: [{ text: 'Единственный' }] }, NOW), { now: NOW, makeId: makeIds() }));
    assert.equal(untitled.headline, 'Вы уже начинали создавать опрос «Без названия»');
    assert.equal(untitled.summary, '1 вопрос · сохранено 25 августа, 14:32');
});

test('числа склоняются по-русски', () => {
    const summaryFor = (count) => describeDraftRecord({
        savedAt: 0,
        draft: { title: 'Т', questions: Array.from({ length: count }, () => ({ text: '' })) },
    }).summary;
    assert.equal(summaryFor(1), '1 вопрос');
    assert.equal(summaryFor(3), '3 вопроса');
    assert.equal(summaryFor(5), '5 вопросов');
    assert.equal(summaryFor(11), '11 вопросов');
    assert.equal(summaryFor(21), '21 вопрос');
});

/* ─── Хранилище ─── */

const fakeStorage = () => {
    const map = new Map();
    return {
        map,
        getItem: (key) => (map.has(key) ? map.get(key) : null),
        setItem: (key, value) => map.set(key, String(value)),
        removeItem: (key) => map.delete(key),
    };
};

test('запись, чтение и стирание черновика ходят по одному ключу', () => {
    const storage = fakeStorage();
    const key = surveyDraftStorageKey(15);

    assert.equal(readSurveyDraft(storage, key, { now: NOW }), null);
    assert.equal(writeSurveyDraft(storage, key, buildDraftRecord({ draft: { title: 'Аттестация' }, savedAt: NOW })), true);

    const back = readSurveyDraft(storage, key, { now: NOW, makeId: makeIds() });
    assert.equal(back.draft.title, 'Аттестация');
    assert.equal(readSurveyDraft(storage, surveyDraftStorageKey(16), { now: NOW }), null, 'коллеге чужой черновик не виден');

    clearSurveyDraft(storage, key);
    assert.equal(readSurveyDraft(storage, key, { now: NOW }), null);
});

test('заблокированное или переполненное хранилище раздел не роняет', () => {
    const broken = {
        getItem: () => { throw new Error('SecurityError'); },
        setItem: () => { throw new Error('QuotaExceededError'); },
        removeItem: () => { throw new Error('SecurityError'); },
    };
    const key = surveyDraftStorageKey(15);
    assert.equal(readSurveyDraft(broken, key, { now: NOW }), null);
    assert.equal(writeSurveyDraft(broken, key, buildDraftRecord({ draft: { title: 'Т' }, savedAt: NOW })), false);
    assert.equal(clearSurveyDraft(broken, key), false);

    assert.equal(readSurveyDraft(null, key, { now: NOW }), null, 'приватный режим отдаёт null вместо хранилища');
    assert.equal(writeSurveyDraft(null, key, {}), false);
});

/* ─── Проводка в разделе ───
   Модуль выше можно оставить идеальным и всё равно потерять работу человека:
   достаточно, чтобы кнопка «Создать опрос» перестала спрашивать про черновик,
   чтобы форма обросла полем, о котором снимок не знает, или чтобы отложенная
   запись начала срабатывать в режиме правки. Всё это ломается молча, поэтому
   сторожится здесь. */

const VIEW_SOURCE = readFileSync(new URL('../src/components/surveys/SurveysView.jsx', import.meta.url), 'utf8');
const flat = VIEW_SOURCE.replace(/\s+/g, ' ');

const literalKeys = (name) => {
    const block = VIEW_SOURCE.match(new RegExp(`const ${name} = \\(\\) => \\(\\{([\\s\\S]*?)\\n\\}\\);`));
    assert.ok(block, `в SurveysView.jsx не нашёлся ${name}()`);
    return (block[1].match(/^ {4}(\w+):/gm) || []).map((line) => line.trim().slice(0, -1));
};

test('форма черновика повторяет emptyDraft() из конструктора', () => {
    // Новое поле в конструкторе, о котором не знает normalizeDraft, просто
    // исчезнет при восстановлении — без ошибки и без следа.
    assert.deepEqual(
        literalKeys('emptyDraft').sort(),
        Object.keys(normalizeDraft(null, { makeId: makeIds() })).sort(),
    );
});

test('форма вопроса повторяет emptyQuestion() из конструктора', () => {
    assert.deepEqual(
        literalKeys('emptyQuestion').sort(),
        Object.keys(normalizeDraft(null, { makeId: makeIds() }).questions[0]).sort(),
    );
});

test('кнопка «Создать опрос» сначала спрашивает про черновик', () => {
    assert.ok(flat.includes('{showBuilder ? \'Отменить\' : \'Создать опрос\'}'), 'кнопка на месте');
    assert.ok(
        flat.includes('if (showBuilder) { closeBuilder(); return; } requestBuilder();'),
        'кнопка снова открывает конструктор напрямую — напоминание о черновике не покажется',
    );
});

test('напоминание даёт оба ответа — продолжить и начать заново', () => {
    assert.ok(flat.includes('onClick={continueStoredDraft}'));
    assert.ok(flat.includes('onClick={discardStoredDraft}'));
    assert.ok(flat.includes('{canManage && pendingDraftInfo && ('), 'окно висит на найденном черновике');
});

test('черновик стирается закрытием конструктора, но не выходом из правки', () => {
    assert.ok(
        flat.includes('if (editingSurveyId == null) forgetStoredDraft();'),
        'правка существующего опроса не должна уносить чужой черновик создания',
    );
});

test('отложенная запись не работает в режиме правки и без прав', () => {
    assert.ok(flat.includes('if (!draftStorage || !canManage || !showBuilder || isEditMode) return undefined;'));
    assert.ok(flat.includes('window.addEventListener(\'pagehide\', flush)'), 'перезагрузка не ждёт таймера');
});

test('успешное создание закрывает конструктор — вместе с ним уходит и черновик', () => {
    assert.ok(flat.includes('notify(isRepeatMode ? \'Повтор опроса создан\' : \'Опрос создан\', \'success\'); } closeBuilder();'));
});

test('иконки напоминания нарисованы, а не подменены кружком', () => {
    // FaIcon отдаёт Circle на любое незнакомое имя — молча и без ошибки.
    const faSource = readFileSync(new URL('../src/components/common/FaIcon.jsx', import.meta.url), 'utf8');
    for (const icon of ['fa-file-signature', 'fa-rotate-left', 'fa-pen']) {
        assert.ok(faSource.includes(`'${icon}':`), `${icon} не заведён в FaIcon`);
    }
});
