/* Справка в статье «где лежит и кому открыта» — чистая часть окна.
 *
 * Где лежит — видит тот, кто вправе статью править; кому открыта — только
 * супер-админ (решение владельца 06.10.2026). Кому что положено, решает сервер:
 * без списка в ответе people = null, и окно рисует одно дерево.
 *
 * Отдельным модулем, а не внутри WikiArticleAccess.jsx, по той же причине, что
 * sectionGrants.js и accessRecipients.js: окно грузит данные по сети, серверный
 * рендер до загруженного состояния не доходит, а ошибиться здесь можно молча —
 * статья встанет не под тем разделом или человеку припишут не те права, и
 * редактор поверит экрану.
 *
 * Данные приходят из GET /articles/<id>/access (wiki/article_access.py). Кому
 * статья открыта, СЧИТАЕТ СЕРВЕР — поимённо, тем же расчётом, что и сам доступ.
 * Здесь — только как это назвать и в каком порядке показать.
 */
import { PERMISSIONS, PRESETS } from './sectionGrants.js';
import { ROLE_TITLE } from './accessRecipients.js';
import { searchWords } from './peopleSearch.js';

/* ── Запрос ─────────────────────────────────────────────────────────────────
 *
 * Здесь, а не в окне, по той же причине, что и всё в модуле: окно ходит на
 * сервер в эффекте, а серверный рендер эффектов не исполняет. Худший исход при
 * этом молчаливый — сорвавшийся запрос, принятый за пустой ответ, выглядит
 * уверенным «статья нигде не лежит, раздел никому не открыт», и по такому
 * экрану пошли бы раздавать доступ, который уже выдан.
 */

/** Адрес двери. Правило маршрута — wiki/routes_articles.py. */
export const articleAccessUrl = (base, articleId) => `${base}/articles/${articleId}/access`;

export const ACCESS_LOADING = { status: 'loading' };

const ACCESS_FAILED = 'Не удалось загрузить доступ';

/**
 * Состояние окна по ответу двери: { status: 'ready', data } либо
 * { status: 'failed', error }. Не бросает — отказ здесь такое же состояние,
 * как успех, и окно обязано его нарисовать.
 *
 * get — axios.get или его подмена: (url, config) → Promise<{ data }>.
 */
export async function loadArticleAccess(get, { base, articleId, headers }) {
    try {
        const response = await get(articleAccessUrl(base, articleId), { headers });
        // Ответ не той формы (страница прокси вместо JSON) — тоже отказ: по
        // нему окно нарисовало бы пустое дерево и «никому не открыта» как
        // настоящий ответ. people = null — не поломка: список смотрящему не
        // положен, сервер так и говорит.
        const data = response?.data;
        if (!Array.isArray(data?.places)
            || !(data.people === null || Array.isArray(data.people))) {
            return { status: 'failed', error: ACCESS_FAILED };
        }
        return { status: 'ready', data: response.data };
    } catch (e) {
        // Текст сервера или русский запасной. e.message сюда не идёт: у axios
        // он всегда непустой и английский («Network Error»), и запасной текст
        // не показался бы ни разу.
        return { status: 'failed', error: e?.response?.data?.error || ACCESS_FAILED };
    }
}

/* ── Дерево ─────────────────────────────────────────────────────────────────
 *
 * Статья лежит сразу в нескольких разделах, и чаще всего это ветки-близнецы:
 * «Супервайзер» у СЗоВ и «Супервайзер» у ОП. Два пути списком читаются как две
 * разные статьи; одно дерево с общим стволом показывает то, что есть на самом
 * деле, — одну статью в двух местах.
 *
 * Строки идут в порядке обхода (родитель, потом его содержимое), как во всех
 * деревьях раздела (structureTree.js): отрисовка ходит по массиву, а не по
 * вложенной структуре.
 */

/**
 * Плоские строки дерева: пространство → разделы → статья.
 *
 * kind: 'space' | 'section' | 'article'. У раздела, в котором статья лежит,
 * стоит home — он рисуется заметнее разделов, через которые к нему идут.
 */
export function buildPlaceTree(places) {
    const spaces = new Map();          // id пространства → узел
    const node = (id, name) => ({ id, name, children: new Map(), place: null, branch: false });

    (places || []).forEach((place) => {
        const spaceId = place.space?.id ?? 0;
        if (!spaces.has(spaceId)) {
            spaces.set(spaceId, { ...node(spaceId, place.space?.name), space: place.space });
        }
        let current = spaces.get(spaceId);
        (place.path || []).forEach((step) => {
            if (!current.children.has(step.id)) {
                current.children.set(step.id, node(step.id, step.name));
            }
            current = current.children.get(step.id);
            current.branch = !!step.branch;
        });
        // Путь пуст только у битых данных: тогда статья встаёт прямо под
        // пространством, а не пропадает из дерева.
        current.place = place;
    });

    const rows = [];
    const walk = (item, depth) => {
        rows.push({
            key: `section:${item.id}`, kind: 'section', depth,
            name: item.name, branch: item.branch,
            home: !!item.place, archived: !!item.place?.archived,
        });
        // Статья — первой строкой внутри своего раздела, раньше подразделов:
        // иначе в разделе с подразделами она уезжала бы под чужую ветку и
        // читалась как лежащая в последнем из них.
        if (item.place) {
            rows.push({ key: `article:${item.id}`, kind: 'article', depth: depth + 1 });
        }
        item.children.forEach((child) => walk(child, depth + 1));
    };

    spaces.forEach((space) => {
        rows.push({
            key: `space:${space.id}`, kind: 'space', depth: 0,
            name: space.name, space: space.space || null,
        });
        if (space.place) {
            rows.push({ key: `article:space:${space.id}`, kind: 'article', depth: 1 });
        }
        space.children.forEach((child) => walk(child, 1));
    });
    return rows;
}

/* «в одном разделе», «в 2 разделах», «в 21 разделе». Предложный падеж: у него
   единственное число только на «один», «двадцать один» и так далее.

   withReaders — под деревом стоит список людей. Тогда про читателей сказано
   здесь же, одной фразой: те, кто читает статью только через такой раздел, в
   список не входят, и без оговорки он выглядел бы полным. */
export const hiddenPlacesLabel = (count, withReaders = false) => {
    const n = Number(count) || 0;
    if (n <= 0) return '';
    const single = n === 1 || (n % 10 === 1 && n % 100 !== 11);
    const where = single
        ? `И ещё в ${n === 1 ? 'одном' : n} разделе, который вам не виден`
        : `И ещё в ${n} разделах, которые вам не видны`;
    if (!withReaders) return `${where}.`;
    return `${where}, — ${single ? 'его' : 'их'} читателей в списке нет.`;
};

/* Статья, которую читатели пока не видят. Сказать об этом обязательно: список
   под неопубликованной статьёй иначе читается как «эти люди её уже видят».
   null — статья вышла, оговорка не нужна. */
export const statusNotice = (status) => {
    if (!status || status === 'published') return null;
    if (status === 'archived') return 'Статья в архиве — читателям она не видна.';
    return 'Статья не опубликована: пока её видят автор и те, кто вправе публиковать.';
};

/* Заголовок списка. У невышедшей статьи — в будущем времени: список говорит,
   кто её откроет после выхода, и «открыта» рядом с оговоркой выше спорило бы
   с ней. */
export const listTitle = (status) => (
    !status || status === 'published' ? 'Кому открыта статья' : 'Кому откроется статья');

/** Пояснение под «i»: кто попадает в список и что значит пометка в строке. */
export const listHint = (byListOnly) => [
    byListOnly
        ? 'В списке — все, кто откроет статью: по её списку, автор, гости и администраторы вики.'
        : 'В списке — все, кто откроет статью: по правилам её раздела и разделов выше, '
            + 'по правилам самой статьи, гости и администраторы вики.',
    'Пометка после отдела говорит, откуда доступ, если он не по отделу и должности.',
].join(' ');

/* ── Люди ───────────────────────────────────────────────────────────────────*/

const capitalize = (text) => {
    const value = String(text || '');
    return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
};

/* Права приходят строкой из букв — в порядке лестницы PERMISSIONS (сервер:
   article_access.RIGHT_LETTERS). */
const RIGHT_KEYS = {
    r: 'can_read', c: 'can_create', e: 'can_edit',
    p: 'can_publish', a: 'can_approve', d: 'can_delete',
};

export const permissionsFromRights = (rights) => Object.fromEntries(
    PERMISSIONS.map((p) => [p.key, false]).concat(
        [...String(rights || '')].filter((letter) => RIGHT_KEYS[letter])
            .map((letter) => [RIGHT_KEYS[letter], true])));

const presetSummary = (key) => PRESETS.find((preset) => preset.key === key).summary;

/**
 * Как человеку открыта статья: { value, notes }.
 *
 * value — одно-три слова в строке, теми же словами, что на экране «Доступ к
 * разделу»: «Чтение», «Правка», «Полный доступ». Отличие одно: право
 * «Создавать» здесь в счёт не идёт. Оно про раздел — заводить в нём НОВЫЕ
 * статьи, — а окно про эту статью: автор, который правит её без права
 * создавать, для неё именно «Правка», а не «Свои права».
 *
 * Набор без одного удаления назван прямо — «Всё, кроме удаления»: так выдают
 * супервайзерам, и десять строк «Свои права» подряд не сказали бы ничего.
 * Остальные ручные наборы расшифрованы строкой ниже.
 */
export function rightsLabel(permissions) {
    const edit = !!permissions.can_edit;
    const publish = !!permissions.can_publish;
    const approve = !!permissions.can_approve;
    const remove = !!permissions.can_delete;
    if (!edit && !publish && !approve && !remove) return { value: presetSummary('read'), notes: null };
    if (edit && !publish && !approve && !remove) return { value: presetSummary('write'), notes: null };
    if (edit && publish && approve) {
        return { value: remove ? presetSummary('full') : 'Всё, кроме удаления', notes: null };
    }
    return {
        value: 'Свои права',
        notes: PERMISSIONS.filter((p) => p.key !== 'can_create' && permissions[p.key])
            .map((p) => p.label).join(' · '),
    };
}

/** Должность человека: своя в бэк-офисе, иначе роль словами портала. */
export const positionTitle = (person) => (
    person?.job_title || capitalize(ROLE_TITLE[person?.role] || person?.role || ''));

/* Откуда доступ, если он не по отделу и должности (сервер: readers.VIA_ORDER).
   Какие пометки человеку нужны, решает сервер: обычный путь он не присылает
   вовсе, а остальное — только там, где оно что-то объясняет. Имя группы,
   направления и роли вики едет с сервера; без него остаётся род. */
const VIA_CAPTION = {
    user: () => 'лично',
    department_head: () => 'как глава отдела',
    group: (label) => (label ? `группа «${label}»` : 'по группе'),
    direction: (label) => (label ? `направление «${label}»` : 'по направлению'),
    wiki_role: (label) => (label ? `роль вики «${label}»` : 'по роли вики'),
    owner: () => 'владелец раздела',
    manual: () => 'ручной доступ',
    guest: () => 'гостевой доступ',
    article_rule: () => 'правило статьи',
    author: () => 'автор статьи',
    article_owner: () => 'владелец статьи',
    wiki_admin: () => 'администратор вики',
};

/** Пометки «откуда доступ» — без повторов и без незнакомых серверу слов. */
export const viaCaptions = (via) => {
    const out = [];
    (via || []).forEach((item) => {
        const caption = VIA_CAPTION[item?.kind]?.(item.label);
        if (caption && !out.includes(caption)) out.push(caption);
    });
    return out;
};

/**
 * Строки списка людей: [{ key, title, meta, notes, value, nameWords, words }].
 *
 * Вид строки тот же, что на экране «Доступ к разделу» (AccessRow): слева кто,
 * справа одно слово о том, как открыто. nameWords и words — то, по чему строку
 * ищут (peopleSearch.js): имя отдельно, чтобы совпадение в имени стояло выше
 * совпадения в отделе.
 */
export function personRows(people) {
    return (people || []).map((person, index) => {
        const position = positionTitle(person);
        const captions = viaCaptions(person.via);
        const { value, notes } = rightsLabel(permissionsFromRights(person.rights));
        const title = person.name || 'Без имени';
        return {
            key: `person:${index}`,
            title,
            meta: [position, person.department, ...captions].filter(Boolean).join(' · '),
            notes,
            value,
            nameWords: searchWords(title),
            words: searchWords(title, position, person.department, ...captions, value),
        };
    });
}
