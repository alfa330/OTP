// Расширение в пути обязательно: модуль грузит напрямую Node в
// tests/baiga_access.test.mjs, а ESM без расширения путь не разрешает.
import { ROLE_TITLE, peopleLabel } from '../wiki/accessRecipients.js';

/*
 * Лист «Доступ» раздела «Списки Байги» — всё, что в нём не разметка: уровни,
 * список адресатов и подписи строк.
 *
 * Отдельным модулем от BaigaAccessSheet.jsx ради теста, как accessRecipients.js
 * у вики: сам лист — модалка с загрузкой по сети, и до этих подписей серверный
 * рендер не доходит.
 *
 * Выдача только ДОБАВЛЯЕТ доступ к кругу раздела (baiga/access.py). Сам круг —
 * «открыт по умолчанию» — в листе стоит отдельным экраном: строки «должность в
 * отделе», у каждой правится уровень (решение владельца 08.10.2026). Состав
 * строк задаёт сервер, строка супер-админов не правится.
 */

/* Уровни — по возрастанию, каждый включает предыдущий. Зеркало LEVELS из
   baiga/access.py; тест сверяет ключи и порядок.

   `label` — слово в сегменте, `summary` — оно же в строке списка, `note` — чем
   уровень отличается от соседнего. Одно предложение: под «i» оно стоит после
   названия уровня (LEVEL_HINT). Строкой под сегментами его нет: пояснение
   нужно один раз, а место под полем занимало бы всегда. */
export const LEVELS = [
    { key: 'read', label: 'Чтение', summary: 'Чтение',
      note: 'Ищет и смотрит списки, файл не выгружает.' },
    { key: 'export', label: 'Выгрузка', summary: 'Чтение и выгрузка',
      note: 'Ищет, смотрит и выгружает выборку в Excel; каждая выгрузка остаётся в журнале.' },
    { key: 'full', label: 'Полный', summary: 'Полный доступ',
      note: 'Ведёт раздел: загружает, заменяет и удаляет недели, выгружает, видит журнал.' },
];

export const levelOf = (key) => LEVELS.find((level) => level.key === key) || null;

/* «Нет» — только у строк «открыт по умолчанию»: строку круга закрывают, а
   выдачу просто снимают. Зеркало LEVEL_NONE из baiga/access.py. */
export const NONE_LEVEL = {
    key: 'none', label: 'Нет',
    note: 'Раздел по умолчанию закрыт; отдельным людям и группам его открывают выдачей.',
};

/* Уровни строки круга: «Нет» и те же три. Зеркало CIRCLE_LEVELS на сервере. */
export const CIRCLE_LEVELS = [NONE_LEVEL, ...LEVELS];

export const circleLevelOf = (key) => CIRCLE_LEVELS.find((level) => level.key === key) || null;

/* Пояснение к выбору уровня под «i» — в том виде, что у выборов «Оплаты
   счетов»: зачем выбор, по строке на вариант и оговорка. Строки собраны из
   самих уровней: подсказка называет ровно те варианты, что стоят в сегментах. */
const QR_NOTE = 'Операторам, стажёрам, сотрудникам бухгалтерии и маркетинга раздел откроется после '
    + 'QR-подтверждения — при любом уровне.';

const hintOf = (levels, outro) => ({
    intro: 'Каждый уровень включает предыдущий.',
    options: levels.map((level) => [
        level.label, level.note.charAt(0).toLowerCase() + level.note.slice(1).replace(/\.$/, '')]),
    outro,
});

export const LEVEL_HINT = hintOf(LEVELS, QR_NOTE);

/* У строки круга про QR сказано только там, где его спросят: рядовым отдела.
   Главе, супервайзеру и названному поимённо раздел открыт без замка. */
const QR_ROW_NOTE = 'Раздел откроется после QR-подтверждения — при любом уровне.';

export const circleLevelHint = (row) => hintOf(CIRCLE_LEVELS, row?.qr ? QR_ROW_NOTE : '');

/* Сколько адресатов принимает одна выдача. Зеркало MAX_GRANT_SUBJECTS из
   baiga/access.py: без потолка в форме человек отмечает сто строк и получает
   отказ на заполненном экране. */
export const MAX_SUBJECTS = 50;

/* Как называется вид адресата в строке готовой выдачи: «Регионы» без слова
   «Группа» читается как фамилия. */
export const KIND_LABEL = { department: 'Отдел', group: 'Группа', user: 'Сотрудник' };

/* Должности бэк-офиса — в словаре вики их нет (там они адресуются должностью
   ветки), а в списке людей без должности тёзок не различить. */
const BACK_OFFICE_TITLE = {
    hr_manager: 'HR-менеджер',
    accounting_manager: 'менеджер бухгалтерии',
    marketing_manager: 'менеджер маркетинга',
};

export const roleTitle = (role) => {
    const key = String(role || '').trim().toLowerCase();
    return ROLE_TITLE[key] || BACK_OFFICE_TITLE[key] || '';
};

export const subjectKey = (type, id) => `${type}:${Number(id)}`;

const join = (parts) => parts.filter(Boolean).join(' · ');

const withPeople = (count) => (typeof count === 'number' ? peopleLabel(count) : '');

/**
 * Сплошной список адресатов для селекта «Кому» — с заголовками групп.
 *
 * catalog — { department, group, user } из GET /api/baiga/access.
 *
 * Люди — ПОСЛЕДНИМИ: их сотни, а групп и отделов десятки. Стой они первыми, до
 * групп пришлось бы прокручивать весь штат. Отдел в подписи группы и человека
 * не украшение: одноимённые группы разных отделов и тёзки иначе неразличимы, а
 * ошибка здесь открывает ФИО и номера ВУ не тем.
 */
export function buildRecipients(catalog = {}) {
    const out = [];
    const push = (groupLabel, type, item, label) => out.push({
        key: subjectKey(type, item.id),
        label,
        groupLabel,
        body: { type, id: Number(item.id) },
    });
    (catalog.group || []).forEach((item) => push(
        'Группы', 'group', item, join([item.name, item.detail, withPeople(item.people)])));
    (catalog.department || []).forEach((item) => push(
        'Отделы', 'department', item, join([item.name, withPeople(item.people)])));
    (catalog.user || []).forEach((item) => push(
        'Люди', 'user', item, join([item.name, roleTitle(item.role), item.detail])));
    return out;
}

/** Что адресатам уже выдано: ключ адресата → уровень. */
export const grantedLevels = (grants = []) => new Map(
    grants.map((grant) => [subjectKey(grant.subject_type, grant.subject_id), grant.level]));

/* Тела запросов листа — здесь, а не в разметке: их сверяет тест, а опечатка в
   ключе («recipients» вместо «subjects») на экране выглядит как отказ сервера.
   Сервер ждёт { subjects: [{ type, id }], level } и { level } (baiga/routes.py). */
export const grantBody = (selected = [], recipientByKey = new Map(), level = '') => ({
    subjects: selected.map((key) => recipientByKey.get(key)?.body).filter(Boolean),
    level,
});

export const levelBody = (level) => ({ level });

/* Адресата больше нет (группу удалили) — строка остаётся: выдачу надо видеть,
   чтобы снять. */
export const grantTitle = (grant) => grant?.label || 'Адресат удалён';

/* Почему выдача никому ничего не открывает — одним словом в строке. */
const GONE_NOTE = { user: 'уволен', group: 'в архиве', department: 'отдел закрыт' };

/** Вторая строка выдачи: вид адресата, отдел или должность, сколько людей. */
export const grantMeta = (grant) => {
    if (!grant) return '';
    const kind = KIND_LABEL[grant.subject_type] || grant.subject_type;
    if (!grant.label) return kind;
    return join([
        grant.subject_type === 'user' ? roleTitle(grant.role) || kind : kind,
        grant.detail,
        grant.subject_type === 'user' ? '' : withPeople(grant.people),
        grant.active === false ? GONE_NOTE[grant.subject_type] : '',
    ]);
};

/* Пункт меню у человека берётся из профиля, а профиль читается при загрузке
   страницы: без этой фразы раздающий говорит «я тебе открыл», а человек раздела
   не видит. Смены уровня это не касается — права раздел спрашивает сам. */
const RELOAD_NOTE = 'Раздел появится в меню после обновления страницы.';

/* Строки «открыт по умолчанию» — кому раздел открыт без выдач. Состав и порядок
   задаёт сервер (access.circle()): строка = должность в отделе или человек,
   названный поимённо; оттуда же названия отделов и имена. */
const SLOT_TITLE = { super_admin: 'Супер-админы', head: 'Глава отдела', sv: 'Супервайзеры', staff: 'Операторы' };

/* В «Маркетинге» рядовые — не операторы: там свои должности. */
const STAFF_TITLE_BY_CODE = { marketing: 'Сотрудники' };

export const slotTitle = (row) => {
    if (!row) return '';
    if (row.kind === 'named') return row.person || '';
    if (row.kind === 'staff') return STAFF_TITLE_BY_CODE[row.code] || SLOT_TITLE.staff;
    return SLOT_TITLE[row.kind] || '';
};

/* Чья строка — подзаголовок её экрана и заголовок секции. */
export const NAMED_SECTION = 'Поимённо';

export const slotOwner = (row) => {
    if (!row) return '';
    if (row.kind === 'named') return NAMED_SECTION;
    return row.department || '';
};

/* Открыта ли строка: уровень — один из трёх. «Нет» и незнакомое слово — закрыта. */
const slotOpen = (row) => Boolean(levelOf(row?.level));

/* Правая колонка строки: уровень и, у рядовых, «после QR». Уровень — тем же
   словом, что в сегментах её экрана (label): строка и выбор за ней читаются
   одинаково, а «Чтение и выгрузка · после QR» на телефоне в 360 px обрезало
   саму должность. Закрытой строке QR ни к чему. */
export const slotValue = (row) => join([
    (circleLevelOf(row?.level) || NONE_LEVEL).label,
    row?.qr && slotOpen(row) ? 'после QR' : '',
]);

/**
 * Экран «Открыт по умолчанию» секциями: супер-админы (не правятся), по секции
 * на отдел, названные поимённо. [{ key, title, rows: [{ slot, title, value,
 * muted, locked, row }] }] — row нужен экрану правки строки.
 */
export function circleSections(circle = []) {
    const sections = [];
    circle.forEach((row) => {
        const title = slotTitle(row);
        // Строка без подписи (незнакомый вид, человек без имени) — показывать нечего.
        if (!title) return;
        const key = row.locked ? 'always' : row.kind === 'named' ? 'named' : `department:${row.code}`;
        let section = sections.find((item) => item.key === key);
        if (!section) {
            section = { key, title: row.locked ? '' : slotOwner(row), rows: [] };
            sections.push(section);
        }
        section.rows.push({
            slot: row.slot, title, value: slotValue(row), muted: !slotOpen(row), locked: Boolean(row.locked), row,
        });
    });
    return sections;
}

/** Одна строка о круге для списка: кому раздел сейчас открыт по умолчанию. */
export const circleSummary = (circle = []) => {
    const names = [];
    circle.forEach((row) => {
        if (row.locked || !slotOpen(row) || !row.department) return;
        if (!names.includes(row.department)) names.push(row.department);
    });
    return ['супер-админы', ...names].join(', ');
};

/* Тело запроса правки строки: PATCH /api/baiga/access/circle — { slot, level }. */
export const circleBody = (slot, level) => ({ slot, level });

/* Строку открыли — у её людей появится пункт меню, а он читается при загрузке
   страницы (то же, что у новой выдачи). Закрыли — раздел перестаёт отвечать им
   сразу; смена уровня открытой строки перезагрузки тоже не требует. */
export const circleToast = (before, after) => {
    const was = Boolean(levelOf(before));
    const now = Boolean(levelOf(after));
    if (was === now) return 'Доступ изменён';
    return now ? `Доступ открыт. ${RELOAD_NOTE}` : 'Доступ закрыт';
};

/** У скольких из выбранных выдача уже есть И уровень другой — он сменится.
    Тот же уровень не в счёт: такую выдачу сервер не трогает вовсе. */
export const replacingCount = (selected = [], levels = new Map(), level = '') => (
    selected.filter((key) => levels.has(key) && levels.get(key) !== level).length);

/* Предупреждение перед сохранением: выдача поверх готовой меняет её уровень.
   Без числительного в падеже — «21 выбранным» читалось бы как опечатка. */
export const replacingNote = (count) => {
    if (!count) return '';
    return count === 1
        ? 'Одному из выбранных раздел уже выдан — его уровень сменится.'
        : `Раздел уже выдан ${count} из выбранных — их уровень сменится.`;
};

/** Что произошло — по ответу сервера, а не по числу отмеченных. */
export const grantToast = ({ granted = 0, changed = 0 } = {}) => {
    if (granted && changed) return `Доступ выдан: ${granted}, изменён: ${changed}. ${RELOAD_NOTE}`;
    if (granted) return `${granted === 1 ? 'Доступ выдан' : `Доступ выдан: ${granted}`}. ${RELOAD_NOTE}`;
    if (changed) return changed === 1 ? 'Доступ изменён' : `Доступ изменён: ${changed}`;
    return 'У выбранных этот доступ уже есть';
};
