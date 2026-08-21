/* Словарь журнала вики: как читается одна запись.
 *
 * Журнал пишется из десятка мест (routes_structure, routes_edit, routes_parks,
 * routes_offices, routes_ack, routes_import) и знает 36 действий. Интерфейс
 * знал восемь — остальные 28 выводились сырым ключом вида «office.day.set», а
 * подробности события лежали в details и не показывались вовсе. Отсюда и
 * «ничего не понятно»: журнал перечислял события, но не рассказывал их.
 *
 * Здесь описано ровно одно: как превратить запись в русскую фразу. Порядок
 * важен — сначала ЧТО произошло (подпись), потом С ЧЕМ (объект, его подставляет
 * сам компонент из entity_name), потом КТО и ПОДРОБНОСТИ (facts).
 *
 * Подписи именные, а не глагольные («Изменена статья», а не «изменил статью»):
 * пол автора события нам неизвестен, а причастие согласуется с объектом, род
 * которого известен всегда.
 */

import {
    Archive, ArrowRightLeft, Building2, CheckCircle2, Copy, FileDown, FilePlus2,
    FileText, FolderPlus, KeyRound, Layers, MapPin, PenLine, RotateCcw,
    ShieldAlert, Sparkles, Star, UserCheck,
} from 'lucide-react';

/* Тон несёт смысл, а не украшает: зелёный — появилось, янтарный — убрали или
   отобрали, синий — выдали доступ, красный — прошли мимо запрета, серый —
   рядовая правка. Пять тонов на 36 действий, больше цветов = шум. */
const CREATED = 'green';
const CHANGED = 'slate';
const REMOVED = 'amber';
const GRANTED = 'blue';
const ALARM = 'red';

export const ACTION_META = {
    // ── Доступы ─────────────────────────────────────────────────────────
    'rule.upsert': { label: 'Выдано право на раздел', tone: GRANTED, icon: KeyRound },
    'rule.delete': { label: 'Право на раздел отозвано', tone: REMOVED, icon: KeyRound },
    'article_rule.grant': { label: 'Выдано право на статью', tone: GRANTED, icon: KeyRound },
    // mode='deny' — не выдача, а именной запрет поверх общих правил.
    'article_rule.deny': { label: 'Запрет на статью', tone: REMOVED, icon: ShieldAlert },
    'article_rule.delete': { label: 'Право на статью отозвано', tone: REMOVED, icon: KeyRound },
    'article.strict_bypass': { label: 'Обход закрытого доступа', tone: ALARM, icon: ShieldAlert },

    // ── Структура ───────────────────────────────────────────────────────
    'space.create': { label: 'Создано пространство', tone: CREATED, icon: Layers },
    'space.update': { label: 'Изменено пространство', tone: CHANGED, icon: Layers },
    'space.archive': { label: 'Пространство в архиве', tone: REMOVED, icon: Archive },
    'section.create': { label: 'Создан раздел', tone: CREATED, icon: FolderPlus },
    'section.update': { label: 'Изменён раздел', tone: CHANGED, icon: PenLine },
    'section.archive': { label: 'Раздел в архиве', tone: REMOVED, icon: Archive },
    'section.move': { label: 'Раздел перемещён', tone: CHANGED, icon: ArrowRightLeft },

    // ── Статьи ──────────────────────────────────────────────────────────
    'article.create': { label: 'Создана статья', tone: CREATED, icon: FilePlus2 },
    'article.update': { label: 'Изменена статья', tone: CHANGED, icon: PenLine },
    'article.archive': { label: 'Статья в архиве', tone: REMOVED, icon: Archive },
    'article.restore': { label: 'Статья восстановлена из версии', tone: CHANGED, icon: RotateCcw },
    'article.adopt': { label: 'Статья добавлена в раздел', tone: CHANGED, icon: FileText },
    'article.fork': { label: 'Сделана копия статьи', tone: CREATED, icon: Copy },
    'article.import': { label: 'Загружен файл', tone: CHANGED, icon: FileDown },
    'article.ai_draft': { label: 'Черновик статьи от ИИ', tone: CHANGED, icon: Sparkles },
    'article.ai_update': { label: 'ИИ сверил статью с файлом', tone: CHANGED, icon: Sparkles },
    'article.ai_edit': { label: 'Правка статьи через ИИ', tone: CHANGED, icon: Sparkles },

    // ── Парки, акции, офисы ─────────────────────────────────────────────
    'park.create': { label: 'Создан таксопарк', tone: CREATED, icon: Building2 },
    'park.update': { label: 'Изменён таксопарк', tone: CHANGED, icon: Building2 },
    'park.archive': { label: 'Таксопарк в архиве', tone: REMOVED, icon: Archive },
    'promotion.create': { label: 'Создана акция', tone: CREATED, icon: Star },
    'promotion.update': { label: 'Изменена акция', tone: CHANGED, icon: Star },
    'promotion.archive': { label: 'Акция в архиве', tone: REMOVED, icon: Archive },
    'office.create': { label: 'Создан офис', tone: CREATED, icon: MapPin },
    'office.update': { label: 'Изменён офис', tone: CHANGED, icon: MapPin },
    'office.archive': { label: 'Офис в архиве', tone: REMOVED, icon: Archive },
    'office.day.set': { label: 'Отметка по офису на день', tone: CHANGED, icon: MapPin },
    'office.day.clear': { label: 'Отметка по офису снята', tone: REMOVED, icon: MapPin },

    // ── Ознакомление ────────────────────────────────────────────────────
    'ack.assign': { label: 'Назначено ознакомление', tone: GRANTED, icon: UserCheck },
    'ack.confirm': { label: 'Ознакомление подтверждено', tone: CREATED, icon: CheckCircle2 },
};

/* Группы — те же, что на сервере (structure.AUDIT_GROUPS): фильтр считает
   сервер, здесь только подписи чипов. */
export const AUDIT_GROUPS = [
    { key: 'all', label: 'Все' },
    { key: 'access', label: 'Доступы' },
    { key: 'structure', label: 'Структура' },
    { key: 'articles', label: 'Статьи' },
    { key: 'places', label: 'Парки и офисы' },
    { key: 'ack', label: 'Ознакомления' },
];

/* Как назвать объект, которого больше нет. На проде таких 13%: таблицу офисов
   пересоздали миграцией, а 41 запись журнала на них ссылается. Писать пустоту
   нельзя — выйдет «Изменён офис» без объекта, будто событие ни о чём. */
export const GONE_ENTITY = {
    article: 'удалена', section: 'удалён', space: 'удалено',
    park: 'удалён', office: 'удалён', promotion: 'удалена',
};

/* Словарь портала, а не свой: ровно эти подписи стоят в выдаче прав
   (WikiSectionAccess.jsx). Разойдутся — один и тот же человек будет называться
   в двух вкладках по-разному. */
const ROLE_TITLE = {
    super_admin: 'супер-админ', admin: 'админ', sv: 'супервайзер',
    supervisor: 'супервайзер', trainer: 'тренер', operator: 'оператор',
    trainee: 'стажёр',
};

const ROLE_LEVEL_LABEL = {
    10: 'от оператора', 20: 'от тренера', 30: 'от СВ',
    40: 'от руководителя', 50: 'супер-админ',
};

/* Тип субъекта словом перед именем: «Отдел продаж» без пояснения читается как
   раздел вики, а не как получатель права. */
const SUBJECT_PREFIX = {
    group: 'группа', direction: 'направление', department: 'отдел',
    department_head: 'глава отдела', wiki_role: 'роль в вики',
};

const NOT_SAVED = 'в вики не сохранено';

const PERMISSION_TITLE = {
    can_read: 'читать', can_create: 'создавать', can_edit: 'править',
    can_publish: 'публиковать', can_approve: 'согласовывать', can_delete: 'удалять',
};

/* Поля, которые правят. Ключ приходит из details как есть — на английском и
   иногда с _id на конце; без словаря строка «поля: parent_section_id,
   section_kind» ничего не сообщает. */
const FIELD_TITLE = {
    title: 'заголовок', name: 'название', content: 'текст', summary: 'описание',
    description: 'описание', status: 'статус', slug: 'адрес', icon: 'значок',
    position: 'порядок', article_type: 'тип статьи', ai_opt_out: 'участие в помощнике',
    owner_user_id: 'владелец', visibility_mode: 'видимость',
    visibility_scope: 'видимость', strict_mode: 'строгий доступ',
    cross_department: 'доступ другим отделам', parent_section_id: 'родительский раздел',
    department_id: 'отдел', section_kind: 'тип ветки', space_id: 'пространство',
    city: 'город', address: 'адрес', address_note: 'как найти', phone: 'телефон',
    website: 'сайт', commission: 'комиссия', map_url: 'карта', lat: 'широта',
    lon: 'долгота', kind: 'тип', head_office_id: 'главный офис',
    logo_file_id: 'логотип', banner_file_id: 'баннер', starts_at: 'начало',
    ends_at: 'окончание', state: 'состояние', day: 'дата',
    ai_index: 'индекс помощника', provider: 'поставщик ИИ', model: 'модель',
    duplicate_verdict: 'проверка на дубли', file: 'файл',
    instruction: 'указание', reason: 'причина', rule_id: 'правило',
    version_id: 'версия', source_article_id: 'исходная статья',
    section_name: 'раздел', sections_moved: 'перенесено разделов',
    assigned: 'назначено', requested: 'запрошено', changes: 'правок',
    questions: 'вопросов', warnings: 'замечаний', tables: 'таблиц',
    images: 'картинок', already_there: 'уже была в разделе',
    from_space_id: 'из пространства', to_space_id: 'в пространство',
    department_ids: 'кому видно', features: 'состав раздела',
};

const VALUE_TITLE = {
    status: {
        draft: 'черновик', published: 'опубликована', archived: 'в архиве',
        on_approval: 'на согласовании', requires_verification: 'требует проверки',
        expired: 'просрочена', active: 'активно',
    },
    visibility_scope: { restricted: 'по правилам доступа', public: 'всем сотрудникам' },
    visibility_mode: { inherit: 'как у раздела', restricted: 'только по правилам' },
    section_kind: { common: 'общая ветка', department: 'ветка отдела' },
    state: { open: 'открыт', closed: 'закрыт' },
    kind: { park: 'офис парка', partner: 'офис партнёра' },
    mode: { grant: 'выдача', deny: 'запрет' },
    ai_index: { indexed: 'обновлён', unchanged: 'без изменений', removed: 'убран' },
};

/* Ключи, которые уже показаны отдельной фразой либо служебные: в подробностях
   они были бы повтором. */
const HIDDEN_KEYS = new Set([
    'subject_type', 'subject_id', 'subject_role', 'min_role_level', 'rule_id',
    'mode', 'fields', 'title', 'name', 'slug',
    ...Object.keys(PERMISSION_TITLE),
]);

/* Обновление индекса помощника — побочный эффект сохранения, а не действие
   человека. В строке это шум, в подробностях — ответ на вопрос «почему статья
   пропала из помощника». */
const ROW_ONLY_HIDDEN = new Set(['ai_index']);

/* Что уже сказано фразой в строке. Повторять это в подробностях незачем: тогда
   раскрытие есть у каждой записи и не значит ничего. Ключи, которых здесь нет,
   в подробностях остаются — там их и ищут при разборе инцидента. */
const CONSUMED = {
    'article.create': ['status'],
    'article.restore': ['version_id'],
    'article.adopt': ['section_id', 'section_name', 'already_there'],
    'article.fork': ['section_id', 'section_name', 'source_article_id'],
    'article.import': ['file', 'kind', 'images'],
    'article.ai_draft': ['file', 'kind', 'tables', 'warnings', 'model'],
    'article.ai_update': ['file', 'kind', 'changes', 'questions', 'model'],
    'article.ai_edit': ['changes', 'instruction', 'model'],
    'article.strict_bypass': ['reason'],
    'section.create': ['space_id', 'visibility_scope', 'department_id'],
    'section.move': ['from_space_id', 'to_space_id', 'parent_section_id',
                     'sections_moved'],
    'office.day.set': ['day', 'state'],
    'office.day.clear': ['day'],
    'ack.assign': ['assigned', 'requested'],
};

export const fieldTitle = (key) => FIELD_TITLE[key] || key;

/* Границу и состав пространства пишем словами, а не структурой: в журнале
   лежат список отделов и объект тумблеров, и «кому видно: 1,367,909» вместе с
   «состав раздела: [object Object]» — это запись, которую нельзя прочитать.
   Отделы называем числом: их имена журналу неоткуда взять, а «трём отделам»
   отвечает на главный вопрос — сузили границу или расширили. */
const SPECIAL_VALUE = {
    department_ids: (value) => (Array.isArray(value) && value.length
        ? `отделов: ${value.length}` : 'всем отделам'),
    features: (value) => {
        if (!value || typeof value !== 'object') return '—';
        const off = Object.entries(value).filter(([, on]) => on === false).map(([key]) => key);
        return off.length ? `выключено: ${off.length}` : 'всё включено';
    },
};

const valueTitle = (key, value) => {
    if (SPECIAL_VALUE[key]) return SPECIAL_VALUE[key](value);
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'boolean') return value ? 'да' : 'нет';
    const dictionary = VALUE_TITLE[key];
    return (dictionary && dictionary[String(value)]) || String(value);
};

/** '2026-08-12' → '12.08.2026'. Остальное отдаём как есть. */
const dayTitle = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
    return match ? `${match[3]}.${match[2]}.${match[1]}` : String(value || '');
};

/** Кому выдали или у кого отобрали право — одной строкой. */
const subjectPhrase = (item) => {
    const details = item.details || {};
    const type = details.subject_type;
    if (!type) return null;
    if (type === 'otp_role') {
        return `роль: ${ROLE_TITLE[details.subject_role] || details.subject_role || '—'}`;
    }
    // subject_name резолвит сервер: в details лежит только идентификатор, а
    // «выдано право субъекту 367» — это не ответ на вопрос «кому».
    const name = item.subject_name
        || item.target_user_name
        || (details.subject_id ? `#${details.subject_id}` : null);
    if (!name) return null;
    const prefix = SUBJECT_PREFIX[type];
    if (!prefix) return name;
    // «отдел «Отдел продаж»» — заикание: у половины отделов слово уже в имени.
    return name.toLowerCase().startsWith(prefix.toLowerCase())
        ? `«${name}»` : `${prefix} «${name}»`;
};

/* У правила-запрета (article_rule.deny) те же can_* означают ЗАПРЕЩЕНО, а не
   выдано. Без слова «запрещено» перед перечислением строка читается ровно
   наоборот — и это самая опасная ошибка чтения, какая тут возможна. */
const permissionsPhrase = (details, deny = false) => {
    const marked = Object.keys(PERMISSION_TITLE).filter((key) => details[key] === true);
    if (!marked.length) return deny ? 'ничего не запрещено' : 'без прав';
    if (deny && details.can_read) return 'полный запрет';
    const list = marked.map((key) => PERMISSION_TITLE[key]).join(', ');
    return deny ? `запрещено: ${list}` : list;
};

const changedFieldsPhrase = (details) => {
    if (!Array.isArray(details.fields)) return null;
    if (!details.fields.length) return null;
    // Длинный список полей в ленте нечитаем; полный состав остаётся в
    // подробностях, где для него есть место.
    const shown = details.fields.slice(0, 5).map(fieldTitle).join(', ');
    const hidden = details.fields.length - 5;
    return `поля: ${shown}${hidden > 0 ? ` и ещё ${hidden}` : ''}`;
};

/* Действия, чей details — это разница, а не снимок. У них присутствие ключа
   само по себе значит «поле трогали», поэтому пустое значение показываем
   словом («описание: очищено»), а не прячем. park.update, наоборот, пишет ВСЕ
   поля парка разом, и там незаполненные превратились бы в строку из десятка
   прочерков. */
const DIFF_ACTIONS = new Set(['section.update', 'space.update']);

const CLEARED_TITLE = {
    parent_section_id: 'верхний уровень',
    department_id: 'без отдела',
};

/* Идентификаторы, которые дерево структуры умеет назвать словами. Без этого
   строка «родительский раздел: №19» требует от читателя знать нумерацию
   разделов наизусть. */
const ID_LOOKUP = {
    parent_section_id: 'section', space_id: 'space',
    from_space_id: 'space', to_space_id: 'space',
};

/* Снимок «ключ → новое значение» одной строкой. Идентификаторы оставляем: без
   них у section.update, где меняют только родителя, не остаётся вообще ничего
   («Изменён раздел» — и всё). */
const changedValuesPhrase = (details, keepEmpty = false, nameOf = null) => {
    const parts = [];
    Object.entries(details).forEach(([key, value]) => {
        if (HIDDEN_KEYS.has(key) || ROW_ONLY_HIDDEN.has(key)) return;
        const empty = value === null || value === undefined || value === '';
        if (empty && !keepEmpty) return;
        if (empty) {
            parts.push(`${fieldTitle(key)}: ${CLEARED_TITLE[key] || 'очищено'}`);
            return;
        }
        const named = ID_LOOKUP[key] && nameOf ? nameOf(ID_LOOKUP[key], value) : null;
        const shown = named ? `«${named}»`
            : (key.endsWith('_id') && typeof value === 'number'
                ? `№${value}` : valueTitle(key, value));
        parts.push(`${fieldTitle(key)}: ${shown}`);
    });
    return parts.length ? parts.join(' · ') : null;
};

/**
 * Подробности события отдельными кусочками для второй строки записи.
 * Возвращает массив коротких фраз — компонент разделит их точками.
 */
export function auditFacts(item, nameOf = null) {
    const details = item.details || {};
    /** Название пространства или раздела по идентификатору из details. */
    const named = (kind, id) => (nameOf && id != null ? nameOf(kind, id) : null);
    const action = item.action;
    const facts = [];

    switch (action) {
        case 'rule.upsert':
        case 'article_rule.grant':
        case 'article_rule.deny':
        case 'rule.delete':
        case 'article_rule.delete': {
            const subject = subjectPhrase(item);
            if (subject) facts.push(subject);
            if (details.min_role_level != null) {
                facts.push(ROLE_LEVEL_LABEL[details.min_role_level]
                    || `от уровня ${details.min_role_level}`);
            }
            // У правила, снятого до 19.08.2026, в журнале лежал один rule_id:
            // тогда сервер не записывал ни субъекта, ни прав.
            if (details.subject_type) {
                facts.push(permissionsPhrase(details, action === 'article_rule.deny'
                    || details.mode === 'deny'));
            } else if (action.endsWith('.delete')) {
                // Правила, снятые до 19.08.2026: сервер тогда записывал один
                // rule_id — ни субъекта, ни прав в журнале не осталось.
                facts.push(`правило №${details.rule_id}`);
            }
            break;
        }
        case 'article.strict_bypass':
            facts.push('статья открыта в обход правил доступа');
            if (details.reason) facts.push(String(details.reason));
            break;

        case 'article.create':
            if (details.status) facts.push(valueTitle('status', details.status));
            break;
        case 'article.update': {
            const changed = changedFieldsPhrase(details);
            if (changed) facts.push(changed);
            break;
        }
        case 'article.restore':
            if (details.version_id) facts.push(`версия №${details.version_id}`);
            break;
        case 'article.adopt':
            if (details.section_name) facts.push(`в раздел «${details.section_name}»`);
            if (details.already_there) facts.push('статья уже была там');
            break;
        case 'article.fork':
            if (details.section_name) facts.push(`в раздел «${details.section_name}»`);
            if (details.source_article_id) facts.push(`из статьи №${details.source_article_id}`);
            break;

        /* У загрузки файла и трёх шагов ИИ результат уходит человеку в
           редактор, а в вики не сохраняется (entity_id у них пуст). Без явной
           пометки строка читается как «залил статью». */
        case 'article.import':
            if (details.file) facts.push(details.file);
            if (details.kind) facts.push(String(details.kind));
            if (details.images) facts.push(`картинок: ${details.images}`);
            facts.push(NOT_SAVED);
            break;
        case 'article.ai_draft':
            if (details.file) facts.push(details.file);
            if (details.tables) facts.push(`таблиц: ${details.tables}`);
            if (details.warnings) facts.push(`замечаний: ${details.warnings}`);
            if (details.model) facts.push(String(details.model));
            facts.push(NOT_SAVED);
            break;
        case 'article.ai_update':
            if (details.file) facts.push(details.file);
            if (details.changes != null) facts.push(`правок: ${details.changes}`);
            if (details.questions) facts.push(`вопросов: ${details.questions}`);
            if (details.model) facts.push(String(details.model));
            facts.push(NOT_SAVED);
            break;
        case 'article.ai_edit':
            if (details.changes != null) facts.push(`правок: ${details.changes}`);
            if (details.instruction) facts.push(`указание: «${details.instruction}»`);
            facts.push(NOT_SAVED);
            break;

        case 'section.create': {
            const space = named('space', details.space_id);
            if (space) facts.push(`в пространстве «${space}»`);
            if (details.visibility_scope) {
                facts.push(valueTitle('visibility_scope', details.visibility_scope));
            }
            break;
        }
        case 'section.move': {
            const from = named('space', details.from_space_id);
            const to = named('space', details.to_space_id);
            if (from && to && from !== to) facts.push(`из «${from}» в «${to}»`);
            else if (to) facts.push(`в пространство «${to}»`);
            const parent = named('section', details.parent_section_id);
            facts.push(parent ? `внутрь раздела «${parent}»` : 'на верхний уровень');
            if (details.sections_moved > 1) {
                facts.push(`вместе с вложенными: ${details.sections_moved}`);
            }
            break;
        }

        case 'office.day.set':
            facts.push(dayTitle(details.day));
            facts.push(valueTitle('state', details.state));
            break;
        case 'office.day.clear':
            facts.push(dayTitle(details.day));
            break;

        case 'ack.assign':
            if (details.assigned != null) {
                facts.push(details.assigned === details.requested
                    ? `человек: ${details.assigned}`
                    : `назначено ${details.assigned} из ${details.requested}`);
            }
            break;

        default:
            break;
    }

    if (!facts.length) {
        const changed = changedFieldsPhrase(details)
            || changedValuesPhrase(details, DIFF_ACTIONS.has(action), nameOf);
        if (changed) facts.push(changed);
    }
    return facts;
}

/** Всё, что не поместилось в строку, — для раскрытия «Подробности». */
export function auditRest(item) {
    const details = item.details || {};
    const consumed = new Set(CONSUMED[item.action] || []);
    // Снимок значений целиком ушёл в строку — раскрывать нечего.
    const snapshotShown = !Array.isArray(details.fields)
        && !CONSUMED[item.action] && !details.subject_type;
    if (snapshotShown) return [];
    return Object.entries(details)
        .filter(([key, value]) => !consumed.has(key) && !HIDDEN_KEYS.has(key)
            && value !== null && value !== undefined && value !== '')
        .map(([key, value]) => [
            fieldTitle(key),
            Array.isArray(value)
                ? value.map(fieldTitle).join(', ')
                : (typeof value === 'object' ? JSON.stringify(value) : valueTitle(key, value)),
        ]);
}
