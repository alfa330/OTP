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
 * Выдача только ДОБАВЛЯЕТ доступ к кругу раздела (baiga/access.py): круг задан
 * правилом и отсюда не меняется, поэтому в листе он показан отдельно и без
 * единого органа управления.
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

/* Пояснение к выбору уровня под «i» — в том виде, что у выборов «Оплаты
   счетов»: зачем выбор, по строке на вариант и оговорка. Строки собраны из
   самих LEVELS: подсказка называет ровно те варианты, что стоят в сегментах. */
export const LEVEL_HINT = {
    intro: 'Каждый уровень включает предыдущий.',
    options: LEVELS.map((level) => [
        level.label, level.note.charAt(0).toLowerCase() + level.note.slice(1).replace(/\.$/, '')]),
    outro: 'Операторам, стажёрам, сотрудникам бухгалтерии и маркетинга раздел откроется после '
        + 'QR-подтверждения — при любом уровне.',
};

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

/* Строки круга раздела — кому он открыт и без выдач. Ключи — access.circle() на
   сервере; оттуда же названия отделов и имена названных поимённо. */
const quoted = (names = []) => names.map((name) => `«${name}»`).join(', ');

const CIRCLE_ROWS = {
    super_admin: () => ({ title: 'Супер-админы' }),
    head: (row) => ({ title: `Глава отдела ${quoted(row.departments)}` }),
    named: (row) => ({ title: (row.people || []).join(', '), meta: 'поимённо' }),
    lead: (row) => ({ title: 'Главы и супервайзеры', meta: (row.departments || []).join(', ') }),
    staff: (row) => ({ title: 'Операторы и сотрудники', meta: (row.departments || []).join(', ') }),
};

/** Круг раздела строками листа: [{ key, title, meta, value }]. */
export function circleRows(circle = []) {
    return circle.map((row) => {
        const build = CIRCLE_ROWS[row.key];
        if (!build) return null;
        const { title, meta = '' } = build(row);
        // Поимённая строка без имён (человека уволили) — показывать нечего.
        if (!title) return null;
        return {
            key: row.key,
            title,
            meta,
            // «После QR» — в правой колонке, рядом с уровнем: там строка не
            // обрезается, а в подписи слева длинные названия отделов её съедали.
            value: join([levelOf(row.level)?.summary || '', row.qr ? 'после QR' : '']),
        };
    }).filter(Boolean);
}

/** Одна строка о круге для списка: отделы круга через запятую. */
export const circleSummary = (circle = []) => {
    const names = [];
    circle.forEach((row) => (row.departments || []).forEach((name) => {
        if (!names.includes(name)) names.push(name);
    }));
    return ['супер-админы', ...names].join(', ');
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

/* Пункт меню у человека берётся из профиля, а профиль читается при загрузке
   страницы: без этой фразы раздающий говорит «я тебе открыл», а человек раздела
   не видит. Смены уровня это не касается — права раздел спрашивает сам. */
const RELOAD_NOTE = 'Раздел появится в меню после обновления страницы.';

/** Что произошло — по ответу сервера, а не по числу отмеченных. */
export const grantToast = ({ granted = 0, changed = 0 } = {}) => {
    if (granted && changed) return `Доступ выдан: ${granted}, изменён: ${changed}. ${RELOAD_NOTE}`;
    if (granted) return `${granted === 1 ? 'Доступ выдан' : `Доступ выдан: ${granted}`}. ${RELOAD_NOTE}`;
    if (changed) return changed === 1 ? 'Доступ изменён' : `Доступ изменён: ${changed}`;
    return 'У выбранных этот доступ уже есть';
};
