/*
 * Черновик конструктора опроса.
 *
 * Зачем он есть. Конструктор — длинная форма: название, окно теста, отбор по
 * стажу и направлениям, список операторов и сколько угодно вопросов с
 * вариантами. Всё это жило только в состоянии React, поэтому любая
 * перезагрузка страницы стирала работу целиком. А перезагрузка тут не редкость:
 * приложение само уходит в `?auth_reload=…`, когда не удалось обновить токен,
 * и человек возвращается на пустую форму, не поняв, что произошло.
 *
 * Что делает модуль. Хранит снимок незаконченного черновика рядом с браузером
 * (localStorage) и приводит его обратно к той же форме, какую строит
 * конструктор, — с теми же ограничениями типов вопросов, что и переключатель
 * «это тест». Чужой, обрезанный или устаревший снимок молча отбрасывается:
 * лучше пустая форма, чем форма, которая падает на первом клике.
 *
 * Чего он НЕ делает. Не хранит правку уже созданного опроса: там источник
 * истины — сервер, и повторное открытие карточки возвращает те же данные.
 * Снимок живёт до первого осознанного решения человека — «Продолжить»,
 * «Начать заново», отмена конструктора или успешное создание опроса.
 */

export const SURVEY_DRAFT_STORAGE_VERSION = 1;

// Неделя — верхняя граница правдоподобия. Снимок месячной давности человек
// уже не узнает, а предложение «продолжить» с ним читается как сбой.
export const SURVEY_DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const QUESTION_TYPE_OTHER_ONLY = 'other_only';
const QUESTION_TYPE_VALUES = new Set(['single', 'multiple', 'rating', QUESTION_TYPE_OTHER_ONLY]);

const MONTHS_GENITIVE = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
];

/** Ключ свой у каждого сотрудника: за одним браузером сидит смена, а не один человек. */
export const surveyDraftStorageKey = (userId) => {
    const id = userId == null || userId === '' ? 'anonymous' : String(userId);
    return `otp:surveys:builder-draft:v${SURVEY_DRAFT_STORAGE_VERSION}:${id}`;
};

export const createQuestionId = () => `q_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;

const asText = (value) => (typeof value === 'string' ? value : (value == null ? '' : String(value)));

const asStringList = (values) => (Array.isArray(values) ? values : [])
    .map((value) => asText(value).trim())
    .filter(Boolean);

const asNumberList = (values) => {
    const seen = new Set();
    (Array.isArray(values) ? values : []).forEach((value) => {
        const number = Number(value);
        if (Number.isFinite(number)) seen.add(number);
    });
    return Array.from(seen);
};

// Number(null) === 0, поэтому «нет исходного опроса» приходится отличать до
// приведения: иначе обычный черновик выдал бы себя за повтор опроса №0.
const asOptionalId = (value) => {
    if (value == null || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
};

const uniqueTrimmed = (values) => {
    const list = [];
    (Array.isArray(values) ? values : []).forEach((value) => {
        const text = asText(value).trim();
        if (text && !list.includes(text)) list.push(text);
    });
    return list;
};

const emptyQuestion = (makeId) => ({
    id: makeId(),
    text: '',
    type: 'single',
    required: true,
    allowOther: false,
    options: ['', ''],
    correctOptions: [],
    points: '1',
    partialCredit: false
});

const normalizeQuestionId = (raw, makeId) => {
    if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
    const text = asText(raw).trim();
    return text || makeId();
};

/**
 * Вопрос из снимка приводим к тем же правилам, по которым его собрал бы сам
 * конструктор. Главное — режим теста: рейтинг и «только Другое» в тесте не
 * живут, и снимок, сделанный до включения тумблера, не должен протащить их
 * обратно в форму.
 */
export const normalizeDraftQuestion = (raw, { isTest = false, makeId = createQuestionId } = {}) => {
    const source = raw && typeof raw === 'object' ? raw : {};
    let type = QUESTION_TYPE_VALUES.has(asText(source.type)) ? asText(source.type) : 'single';
    if (isTest && (type === 'rating' || type === QUESTION_TYPE_OTHER_ONLY)) type = 'single';

    const withoutOptions = type === 'rating' || type === QUESTION_TYPE_OTHER_ONLY;
    const rawOptions = (Array.isArray(source.options) ? source.options : []).map(asText);
    const options = withoutOptions ? [] : (rawOptions.length ? rawOptions : ['', '']);

    let correctOptions = type === QUESTION_TYPE_OTHER_ONLY
        ? []
        : uniqueTrimmed(source.correctOptions).filter((option) => uniqueTrimmed(options).includes(option));
    if (type === 'single' && correctOptions.length > 1) correctOptions = [correctOptions[0]];

    return {
        id: normalizeQuestionId(source.id, makeId),
        text: asText(source.text),
        type,
        required: source.required !== false,
        allowOther: type === QUESTION_TYPE_OTHER_ONLY
            ? true
            : (isTest || type === 'rating' ? false : source.allowOther === true),
        options,
        correctOptions,
        points: source.points == null || source.points === '' ? '1' : asText(source.points),
        partialCredit: type === 'multiple' && source.partialCredit === true
    };
};

/** Снимок → ровно та же форма, какую держит конструктор. Лишние поля отбрасываются. */
export const normalizeDraft = (raw, { makeId = createQuestionId } = {}) => {
    const source = raw && typeof raw === 'object' ? raw : {};
    const isTest = source.isTest === true;
    const questions = (Array.isArray(source.questions) ? source.questions : [])
        .map((question) => normalizeDraftQuestion(question, { isTest, makeId }));

    return {
        title: asText(source.title),
        description: asText(source.description),
        isTest,
        directionIds: asStringList(source.directionIds),
        groupIds: asNumberList(source.groupIds),
        tenureWeeksMin: asText(source.tenureWeeksMin),
        tenureWeeksMax: asText(source.tenureWeeksMax),
        operatorIds: asNumberList(source.operatorIds),
        questions: questions.length ? questions : [emptyQuestion(makeId)],
        startsAt: asText(source.startsAt),
        endsAt: asText(source.endsAt),
        singleAttempt: source.singleAttempt !== false,
        affectsQuality: source.affectsQuality === true
    };
};

/**
 * Стоит ли черновик того, чтобы о нём напоминать.
 *
 * Открыть конструктор и закрыть — обычное дело, и предлагать «продолжить»
 * пустую форму значит будить человека без повода. Считаем черновик стоящим,
 * только если в нём есть введённое руками: название, описание, отбор,
 * окно теста, лишний вопрос или хоть один заполненный вопрос/вариант.
 * Одни тумблеры («это тест», «одна попытка») сами по себе не в счёт.
 */
export const isDraftMeaningful = (draft) => {
    const source = draft && typeof draft === 'object' ? draft : {};
    if (asText(source.title).trim()) return true;
    if (asText(source.description).trim()) return true;
    if (asStringList(source.directionIds).length) return true;
    if (asNumberList(source.groupIds).length) return true;
    if (asNumberList(source.operatorIds).length) return true;
    if (asText(source.tenureWeeksMin).trim()) return true;
    if (asText(source.tenureWeeksMax).trim()) return true;
    if (asText(source.startsAt).trim()) return true;
    if (asText(source.endsAt).trim()) return true;

    const questions = Array.isArray(source.questions) ? source.questions : [];
    if (questions.length > 1) return true;
    return questions.some((question) => {
        const item = question && typeof question === 'object' ? question : {};
        if (asText(item.text).trim()) return true;
        if (uniqueTrimmed(item.options).length) return true;
        return uniqueTrimmed(item.correctOptions).length > 0;
    });
};

/** Снимок для хранилища. `repeatSourceSurveyId` держим, чтобы повтор остался повтором. */
export const buildDraftRecord = ({ draft, repeatSourceSurveyId = null, savedAt }) => ({
    version: SURVEY_DRAFT_STORAGE_VERSION,
    savedAt: Number.isFinite(Number(savedAt)) ? Number(savedAt) : 0,
    repeatSourceSurveyId: asOptionalId(repeatSourceSurveyId),
    draft: normalizeDraft(draft)
});

/**
 * Разбор того, что лежит в хранилище. Возвращает null на всём, чему нельзя
 * доверять: чужая версия, не JSON, протухший срок, пустая форма.
 */
export const parseDraftRecord = (raw, { now = 0, ttlMs = SURVEY_DRAFT_TTL_MS, makeId = createQuestionId } = {}) => {
    if (!raw) return null;
    let parsed = null;
    try {
        parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    } catch (_error) {
        return null;
    }
    if (!parsed || typeof parsed !== 'object') return null;
    if (Number(parsed.version) !== SURVEY_DRAFT_STORAGE_VERSION) return null;

    const savedAt = Number(parsed.savedAt);
    if (!Number.isFinite(savedAt) || savedAt <= 0) return null;
    // Снимок «из будущего» — переведённые часы или чужая машина: доверять
    // нечему, но и выбрасывать жалко, поэтому считаем его свежим.
    if (Number.isFinite(now) && now > 0 && now - savedAt > ttlMs) return null;

    const draft = normalizeDraft(parsed.draft, { makeId });
    if (!isDraftMeaningful(draft)) return null;

    return {
        version: SURVEY_DRAFT_STORAGE_VERSION,
        savedAt,
        repeatSourceSurveyId: asOptionalId(parsed.repeatSourceSurveyId),
        draft
    };
};

const plural = (count, one, few, many) => {
    const abs = Math.abs(Number(count) || 0);
    const lastTwo = abs % 100;
    if (lastTwo >= 11 && lastTwo <= 14) return many;
    const last = abs % 10;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

const formatSavedAt = (savedAt) => {
    const stamp = Number(savedAt);
    if (!Number.isFinite(stamp) || stamp <= 0) return '';
    const date = new Date(stamp);
    if (Number.isNaN(date.getTime())) return '';
    const pad = (value) => String(value).padStart(2, '0');
    return `${date.getDate()} ${MONTHS_GENITIVE[date.getMonth()]}, ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

/**
 * Текст напоминания. Человеку важно узнать свой черновик, а не услышать, что
 * «есть несохранённые данные»: поэтому название, вид (тест или опрос), число
 * вопросов и когда это было.
 */
export const describeDraftRecord = (record) => {
    const draft = record?.draft || {};
    const isTest = draft.isTest === true;
    const title = asText(draft.title).trim();
    const questionCount = Array.isArray(draft.questions) ? draft.questions.length : 0;
    const operatorCount = asNumberList(draft.operatorIds).length;
    const isRepeat = asOptionalId(record?.repeatSourceSurveyId) != null;

    const parts = [`${questionCount} ${plural(questionCount, 'вопрос', 'вопроса', 'вопросов')}`];
    if (operatorCount > 0) {
        parts.push(`${operatorCount} ${plural(operatorCount, 'оператор', 'оператора', 'операторов')}`);
    }
    const savedAtLabel = formatSavedAt(record?.savedAt);
    if (savedAtLabel) parts.push(`сохранено ${savedAtLabel}`);

    const kind = isTest ? 'тест' : 'опрос';
    const titleLabel = title || 'Без названия';
    return {
        isTest,
        isRepeat,
        summary: parts.join(' · '),
        headline: isRepeat
            ? `Вы уже начинали повтор — ${kind} «${titleLabel}»`
            : `Вы уже начинали создавать ${kind} «${titleLabel}»`
    };
};

/* ─── Хранилище ─── */

export const getBrowserStorage = () => {
    try {
        if (typeof window === 'undefined') return null;
        const storage = window.localStorage;
        if (!storage) return null;
        // Приватный режим и политики отдают объект, который падает на записи.
        const probe = '__otp_surveys_probe__';
        storage.setItem(probe, '1');
        storage.removeItem(probe);
        return storage;
    } catch (_error) {
        return null;
    }
};

export const readSurveyDraft = (storage, key, options = {}) => {
    if (!storage || !key) return null;
    try {
        return parseDraftRecord(storage.getItem(key), options);
    } catch (_error) {
        return null;
    }
};

export const writeSurveyDraft = (storage, key, record) => {
    if (!storage || !key || !record) return false;
    try {
        storage.setItem(key, JSON.stringify(record));
        return true;
    } catch (_error) {
        // Переполненное хранилище — не повод ломать форму.
        return false;
    }
};

export const clearSurveyDraft = (storage, key) => {
    if (!storage || !key) return false;
    try {
        storage.removeItem(key);
        return true;
    } catch (_error) {
        return false;
    }
};
