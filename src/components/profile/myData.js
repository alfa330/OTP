// Правила блока «Мои данные» в «Профиле» (задача #357).
//
// Зеркало my_data/fields.py: кому блок открыт, список курсов и приведение
// телефона, ника и номера карты. Окончательно решает сервер — здесь те же
// правила, чтобы ошибка была видна у поля до отправки, а не тостом после.
// Совпадение списков сторожит tests/test_my_data.py.
//
// Расширения в импортах обязательны: модуль грузит напрямую Node в
// tests/my_data.test.mjs, а ESM без расширения путь не разрешает.
import { normalizeRole } from '../../utils/roles.js';
import { departmentCodeOf } from '../../utils/departmentViews.js';

export const MY_DATA_ROLE = 'operator';
export const MY_DATA_DEPARTMENT_CODES = ['szov', 'op', 'tez'];

export const COURSE_OPTIONS = [
    '1', '2', '3', '4', '5', '6',
    'Магистратура, 1 курс',
    'Магистратура, 2 курс',
];

export const CARD_DIGITS = 16;
// Сколько цифр поле карты вообще пропускает. Больше 16 — нарочно: лишняя
// цифра должна остаться на виду и дать ошибку, а не срезаться молча, иначе
// опечатка «нажал дважды» тихо сохраняется как чужой номер.
export const CARD_INPUT_MAX_DIGITS = 19;
export const TEXT_MAX_LENGTH = 255;

export const ERROR_PHONE = 'Телефон: +7 и 10 цифр, например +7 701 234 56 78';
export const ERROR_TELEGRAM = 'Ник в Telegram: латиница, цифры и «_», от 5 до 32 знаков';
export const ERROR_CARD = 'Номер карты — 16 цифр';
export const ERROR_TEXT_TOO_LONG = `Не длиннее ${TEXT_MAX_LENGTH} знаков`;

const TELEGRAM_LINK_PREFIX = /^(?:https?:\/\/)?(?:www\.)?(?:t|telegram)\.me\//i;
const TELEGRAM_USERNAME = /^[A-Za-z0-9_]{5,32}$/;

// Стажёрам блок не нужен (решение владельца 24.09.2026) — роль ровно одна.
export const canEditOwnData = (user) => (
    normalizeRole(user?.role) === MY_DATA_ROLE
    && MY_DATA_DEPARTMENT_CODES.includes(departmentCodeOf(user) || '')
);

const collapse = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();

// «3» → «3 курс»; магистратура и старые записи вроде «курс 4» — как есть.
export const courseLabel = (value) => {
    const text = collapse(value);
    if (!text) return '';
    return /^\d$/.test(text) ? `${text} курс` : text;
};

// Старая запись, которая читается ровно как пункт списка («3 курс» — это «3»),
// — это и есть тот пункт: иначе в списке стояли бы два одинаковых «3 курс».
// Что со списком не совпадает («курс 4», «2курс»), остаётся как было.
export const canonicalCourse = (value, options = COURSE_OPTIONS) => {
    const text = collapse(value);
    if (!text || options.includes(text)) return text;
    return options.find((option) => courseLabel(option) === text) ?? text;
};

export const normalizePhone = (value) => {
    const raw = collapse(value);
    if (!raw) return { value: null, error: null };
    let digits = raw.replace(/\D/g, '');
    if (digits.length === 11 && (digits[0] === '7' || digits[0] === '8')) digits = digits.slice(1);
    if (digits.length !== 10) return { value: null, error: ERROR_PHONE };
    return { value: `+7${digits}`, error: null };
};

// +77012345678 → +7 701 234 56 78. Чужой формат (старые записи) — как есть.
export const formatPhone = (value) => {
    const raw = String(value ?? '').trim();
    const match = /^\+7(\d{3})(\d{3})(\d{2})(\d{2})$/.exec(raw);
    return match ? `+7 ${match[1]} ${match[2]} ${match[3]} ${match[4]}` : raw;
};

export const normalizeTelegram = (value) => {
    const raw = collapse(value);
    if (!raw) return { value: null, error: null };
    const username = raw.replace(TELEGRAM_LINK_PREFIX, '').trim().replace(/^\/+|\/+$/g, '').replace(/^@+/, '').trim();
    if (!TELEGRAM_USERNAME.test(username)) return { value: null, error: ERROR_TELEGRAM };
    return { value: `@${username}`, error: null };
};

export const cardDigits = (value) => String(value ?? '').replace(/\D/g, '').slice(0, CARD_INPUT_MAX_DIGITS);

// Набор номера карты группами по четыре, как на самой карте.
export const formatCardInput = (value) => cardDigits(value).replace(/(\d{4})(?=\d)/g, '$1 ');

const normalizeText = (value) => {
    const text = collapse(value);
    if (!text) return { value: null, error: null };
    if (text.length > TEXT_MAX_LENGTH) return { value: null, error: ERROR_TEXT_TOO_LONG };
    return { value: text, error: null };
};

// Черновик формы из ответа сервера. Номер карты в черновике — всегда НОВЫЙ
// номер: полного текущего у интерфейса нет, пустое поле значит «не менял».
export const draftFromData = (data) => ({
    phone: formatPhone(data?.phone),
    telegram_nick: String(data?.telegram_nick ?? ''),
    card_number: '',
    study_place: String(data?.study_place ?? ''),
    study_specialty: String(data?.study_specialty ?? ''),
    study_course: canonicalCourse(data?.study_course),
});

// Что отправить на сервер. Поле уходит, только если человек его ТРОНУЛ:
// старые записи вида «nick» без «@» или «3 курс» не переписываются молча
// сохранением соседнего поля. Возвращает { payload, errors }.
export const buildChanges = (data, draft) => {
    const payload = {};
    const errors = {};
    const initial = draftFromData(data);
    const touched = (field) => collapse(draft?.[field]) !== collapse(initial[field]);

    const take = (field, result) => {
        if (result.error) errors[field] = result.error;
        else payload[field] = result.value;
    };

    if (touched('phone')) take('phone', normalizePhone(draft.phone));
    if (touched('telegram_nick')) take('telegram_nick', normalizeTelegram(draft.telegram_nick));
    if (collapse(draft?.card_number)) {
        const raw = String(draft.card_number);
        const digits = raw.replace(/\D/g, '');
        if (digits.length !== CARD_DIGITS || /[^\d\s-]/.test(raw)) {
            errors.card_number = ERROR_CARD;
        } else {
            payload.card_number = digits;
        }
    }
    if (touched('study_place')) take('study_place', normalizeText(draft.study_place));
    // Специальность без университета не хранится (задача #279): стёр
    // университет — сервер сотрёт и её, отправлять нечего.
    const placeLeft = collapse(draft?.study_place) !== '';
    if (placeLeft && touched('study_specialty')) take('study_specialty', normalizeText(draft.study_specialty));
    if (touched('study_course')) payload.study_course = collapse(draft.study_course) || null;

    return { payload, errors };
};
