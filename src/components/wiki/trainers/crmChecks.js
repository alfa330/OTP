/* Проверки работы в учебной CRM — общие для курса и аттестации.
 *
 * Чистый модуль без React. Каждая проверка возвращает null, если всё верно,
 * или ОДНУ фразу — что не так и почему. Порядок проверок — сверху вниз по
 * форме, как человек проверяет её глазами: первая же ошибка и называется.
 *
 * Что считается верным, знают только сценарии: в данных водителей (слепках)
 * ответов нет.
 */

import { categoryBranch, formLeaf, normalizePhone } from './crmModel.js';
import { categoryNode, pathLabel } from './crmCatalog.js';

const low = (text) => String(text || '').toLowerCase().replace(/ё/g, 'е');
export const compact = (text) => String(text || '').toUpperCase().replace(/\s+/g, '');

/** Комментарий формы обращения. */
export const formComment = (form) => String((form.info || {})['Комментарий'] || '').trim();

/* Ошибки категории, в которых путаются чаще всего: соседняя ветка («Обычный»
   против «Самозанятого водителя») и запрос с тикетом вместо консультации. */
const branchProblem = (node, branch) => {
    if (node.path[1] !== branch) {
        return branch === 'Самозанятый водитель'
            ? 'Это ветка «Обычный водитель», а водитель — самозанятый (СМЗ в его учётке): ветка «Самозанятый водитель».'
            : `Это ветка «${node.path[1]}», а водитель — парковый сотрудник (физлицо в его учётке): ветка «Обычный водитель».`;
    }
    return null;
};

/**
 * Разбор формы обращения.
 *   expect = { source, phone, phonePretty, park, city, leaf, branch, leafProblem(node),
 *              keyword, keywordHint, linked }
 * linked: номер тикета, к которому обращение обязано быть присоединено
 * (у водителя он уже открыт по той же категории); undefined — не важно.
 */
export const requestProblem = (form, expect) => {
    if (form.source !== expect.source) {
        return expect.source === 'Звонок'
            ? `Источник «${form.source}», а водитель позвонил. «Звонок/Чат» — «Звонок».`
            : `Источник «${form.source}», а водитель написал в ${expect.source}. «Звонок/Чат» — «${expect.source}».`;
    }
    if (normalizePhone(form.phone) !== normalizePhone(expect.phone)) {
        return form.phone
            ? `Номер «${form.phone}» не этого водителя. Его номер — ${expect.phonePretty}.`
            : `Не заполнен «Номер телефона». Номер водителя — ${expect.phonePretty}.`;
    }
    if (form.park !== expect.park) {
        return form.park
            ? `Таксопарк «${form.park}», а водитель из «${expect.park}» — так в его учётке.`
            : `Не выбран «Таксопарк». Водитель из «${expect.park}».`;
    }
    if (form.city !== expect.city) return `Город — «${expect.city}»: водитель работает там.`;
    const node = formLeaf(form);
    if (!node) return 'Категория не выбрана до конца: обращение сохраняется только с конечной категорией.';
    if (node.id !== expect.leaf) {
        const own = expect.leafProblem ? expect.leafProblem(node) : null;
        return own
            || (expect.branch ? branchProblem(node, expect.branch) : null)
            || `Не та категория. Нужна «${pathLabel(categoryNode(expect.leaf)).replace('Водитель › ', '')}».`;
    }
    if (expect.linked !== undefined && String(form.linked || '') !== String(expect.linked)) {
        return expect.linked
            ? `У водителя уже открыт тикет №${expect.linked} по этой же категории: обращение присоединяют к нему, второй тикет не заводят.`
            : 'Тикета по этой категории у водителя нет — обращение не к чему присоединять.';
    }
    const text = formComment(form);
    if (text.length < 15) {
        return 'Комментарий слишком короткий. Коллега должен понять без звонка: что спросил водитель и что ты ответил.';
    }
    if (expect.keyword && !expect.keyword.test(low(text))) return expect.keywordHint;
    return null;
};

/** Ожидания по форме обращения для водителя из его дела. */
export const expectFor = (item, extra) => ({
    phone: item.contractor.phone,
    phonePretty: item.call?.phone_pretty || item.contractor.phone_pretty,
    park: item.park.name,
    city: item.park.city,
    branch: categoryBranch(item),
    ...extra,
});

/* Текст обращения в Яндекс: поддержка не знает нашего водителя, поэтому
   нужны кто, что случилось и о чём просим. */
const ASK = /(прос|прошу|провер|верн|разбер|помоги|уточн)/;

export const yaTextProblem = (text, { about, aboutHint }) => {
    const value = String(text || '').trim();
    if (value.length < 60) {
        return 'Текст слишком короткий. Поддержке нужно понять три вещи: кто водитель, что случилось и о чём мы просим.';
    }
    if (!about.test(low(value))) return aboutHint;
    if (!ASK.test(low(value))) return 'В тексте не сказано, чего мы просим. Допиши: «Просим проверить…».';
    return null;
};

/**
 * Разбор обращения в Яндекс.
 *   expect = { park, topics: [тема, подтема], topicHint, subtopicHint, fields(values, files) }
 */
export const yaFormProblem = (form, expect) => {
    if (form.park !== expect.park) {
        return `Обращение уйдёт от кабинета «${form.park}» — там нет нашего водителя. Нужен «${expect.park}».`;
    }
    const [topic, subtopic] = expect.topics;
    if ((form.topics || [])[0] !== topic) return expect.topicHint;
    if ((form.topics || [])[1] !== subtopic) return expect.subtopicHint;
    return expect.fields(form.values || {}, form.files || []);
};
