/* Кому открывают раздел — один список адресатов на всю форму выдачи.
 *
 * До 16.09.2026 адресата выбирали в два приёма: сперва селект «Тип субъекта»
 * (человек / группа / направление / отдел / роль), потом второй селект внутри
 * выбранного типа. И ровно один адресат за раз. Владелец попросил обратного
 * дословно: «чтобы я мог добавлять права сразу к группам людей, и по их
 * должности тоже» — то есть отметить нужных и выдать всем одно и то же одним
 * сохранением.
 *
 * Поэтому здесь список СПЛОШНОЙ, с заголовками групп: должности ветки, группы,
 * направления, отделы, люди, роли. Тип субъекта из формы исчез — он стал
 * заголовком над строкой, и отдельного выбора больше не требует.
 *
 * Модуль отдельный от WikiSectionAccess.jsx не ради порядка, а ради теста: сам
 * экран — модалка с загрузкой по сети, и серверный рендер до этого списка не
 * доходит (та же причина, по какой рядом живёт sectionGrants.js).
 */

/* Словарь портала, а не свой. Раньше здесь стояли «руководитель» и «директор»,
   которых больше нигде в системе нет: поиск по слову «админ» не находил никого,
   и выглядело это как «админов в списке нет» — хотя они были. Ровно эти же
   подписи отдаёт справочник ролей в /access/subjects. */
export const ROLE_TITLE = {
    operator: 'оператор', trainee: 'стажёр', trainer: 'тренер',
    sv: 'супервайзер', supervisor: 'супервайзер',
    admin: 'админ', super_admin: 'супер-админ',
};

/* Как называется тип адресата в строке уже выписанного правила. Выбирать тип
   в форме больше не нужно, а вот прочитать его у готового правила — нужно:
   «Кастек Гаухар группа Основа» без слова «Группа» читается как фамилия. */
export const SUBJECT_KIND_LABEL = {
    user: 'Конкретный человек',
    group: 'Группа',
    direction: 'Направление',
    department: 'Отдел',
    // Адресуется НАЗНАЧЕНИЮ, а не человеку: правило переезжает вместе со сменой
    // главы, и переставлять его руками не нужно.
    department_head: 'Глава отдела',
    wiki_role: 'Роль в вики',
    otp_role: 'Роль в системе',
};

/* Субъекты БЕЗ отдела: роль в системе носят сотрудники всех отделов сразу,
   роль вики — все, кому её назначили. Раздающему, у которого есть граница
   отдела, они закрыты (та же пара в wiki/access.py: COMPANY_WIDE_SUBJECTS), и
   в списке их нет вовсе: предложенная строка, на которую приходит отказ,
   читается как поломка, а не как правило. */
export const COMPANY_WIDE_KINDS = ['otp_role', 'wiki_role'];

/* «174 человека», «1 человек», «22 человека». Счётчик под адресатом отвечает
   на главный вопрос выдачи — кому именно я сейчас открываю раздел, — и
   склонение тут не украшение: «174 человек» читается как опечатка и роняет
   доверие ко всей строке. */
export const peopleLabel = (count) => {
    const ten = count % 10;
    const hundred = count % 100;
    if (ten === 1 && hundred !== 11) return `${count} человек`;
    if (ten >= 2 && ten <= 4 && (hundred < 12 || hundred > 14)) return `${count} человека`;
    return `${count} человек`;
};

/* Ключ адресата — его ПОЛНЫЙ адрес, все четыре измерения правила разом.
   Ни одно из них не лишнее: без job_title четыре должности «Маркетинга» (один
   отдел, один уровень 10) склеились бы в один ключ, а без min_role_level
   «СЗоВ целиком» и «СЗоВ не ниже супервайзера» стали бы одной строкой. Тот же
   состав, что у ключа уникальности в базе (uq_wiki_section_rule_subject_position). */
export const recipientKey = (subject) => [
    subject.subject_type,
    subject.subject_type === 'otp_role'
        ? (subject.subject_role || '')
        : (subject.subject_id == null ? '' : Number(subject.subject_id)),
    subject.min_role_level == null ? '' : Number(subject.min_role_level),
    subject.job_title || '',
].join('|');

/** Подпись с числом людей: «Группа Регионы · 20 человек». */
const withPeople = (label, people) => (
    typeof people === 'number' ? `${label} · ${peopleLabel(people)}` : label);

/**
 * Сплошной список адресатов для селекта «Кому».
 *
 * rows       — должности ветки (positions из GET /access/section-rules);
 * catalog    — справочник из /access/subjects;
 * people     — сотрудники из /access/people (уже обрезаны потолком и отделом);
 * bounded    — у раздающего есть граница отдела (grant_departments не null);
 * hasBranch  — над разделом есть ветка отдела.
 *
 * Должности показываются ТОЛЬКО внутри ветки отдела. Вне её строки должностей
 * адресуются самой роли и действуют по всей компании — их место в группе
 * «Роли в системе», которая и так ниже; двумя списками одного и того же
 * человек выбирал бы дважды.
 */
export function buildRecipients({ rows = [], catalog = {}, people = [],
                                  bounded = false, hasBranch = false } = {}) {
    const out = [];
    const push = (groupLabel, label, body, { disabled = false, note = '' } = {}) => {
        out.push({
            key: recipientKey(body),
            label: note ? `${label} · ${note}` : label,
            groupLabel,
            disabled,
            body,
        });
    };

    if (hasBranch) {
        rows.forEach((row) => {
            // Строку выше потолка ПОКАЗЫВАЕМ запертой, а не прячем: спрятанная
            // читается как «такой должности не бывает», запертая объясняет, что
            // выдача есть, но не отсюда. Заперто её считает сервер (locked).
            push('Должности отдела',
                 withPeople(row.label, row.people),
                 {
                     subject_type: row.subject_type,
                     subject_id: row.subject_id,
                     subject_role: row.subject_role || null,
                     min_role_level: row.min_role_level ?? null,
                     job_title: row.job_title || null,
                 },
                 { disabled: !!row.locked,
                   note: row.locked ? 'выдаёт вышестоящий' : '' });
        });
    }

    (catalog.group || []).forEach((item) => push(
        'Группы', withPeople(item.name, item.people),
        { subject_type: 'group', subject_id: item.id,
          min_role_level: null, job_title: null }));

    (catalog.direction || []).forEach((item) => push(
        'Направления', withPeople(item.name, item.people),
        { subject_type: 'direction', subject_id: item.id,
          min_role_level: null, job_title: null }));

    /* Отдел и его глава — одной группой. Заголовок «Главы отделов» над
       строкой «СЗоВ» повторял бы сам себя, а в перечне выбранного, где
       заголовков нет вовсе, «СЗоВ» и «СЗоВ» стояли бы двумя неразличимыми
       строками с разным смыслом. */
    (catalog.department || []).forEach((item) => {
        push('Отделы', withPeople(item.name, item.people),
             { subject_type: 'department', subject_id: item.id,
               min_role_level: null, job_title: null });
        push('Отделы', `Глава «${item.name}»`,
             { subject_type: 'department_head', subject_id: item.id,
               min_role_level: null, job_title: null });
    });

    /* Люди — ПОСЛЕДНИМИ, и это не мелочь: их 174, а групп 12. Стой они
       посередине, до ролей и отделов пришлось бы прокручивать полторы сотни
       строк. Поиск в списке есть, но порядок обязан работать и без него. */
    if (!bounded) {
        (catalog.wiki_role || []).forEach((item) => push(
            'Роли в вики', item.name,
            { subject_type: 'wiki_role', subject_id: item.id,
              min_role_level: null, job_title: null }));
        (catalog.otp_role || []).forEach((item) => push(
            // Роль не знает границ отдела — и подпись группы говорит об этом
            // прямо, а не мелким предупреждением где-то под списком.
            'Роли в системе — вся компания', item.name,
            { subject_type: 'otp_role', subject_id: null,
              subject_role: String(item.id), min_role_level: null, job_title: null }));
    }

    people.forEach((person) => push(
        'Люди',
        // Должность в подписи не для красоты: тёзки в списке из 174 человек
        // неразличимы, а ошибка тут выдаёт доступ не тому.
        [person.name, ROLE_TITLE[person.role] || person.role, person.department_name]
            .filter(Boolean).join(' · '),
        { subject_type: 'user', subject_id: person.id,
          min_role_level: null, job_title: null }));

    return out;
}

/** Ключи адресатов, которым раздел уже открыт: их выдача будет ЗАМЕНЕНА. */
export const grantedKeys = (rules = []) => new Set(
    rules.map((rule) => recipientKey({
        subject_type: rule.subject_type,
        subject_id: rule.subject_id,
        subject_role: rule.subject_role,
        min_role_level: rule.min_role_level ?? null,
        job_title: rule.job_title || null,
    })));
