/*
 * «Расположение и доступ» — справка в статье: где она лежит в дереве и кому
 * открыта (решение владельца 06.10.2026). Кому — людьми, списком, «с
 * пагинацией, и поиск как в вики, который учитывает ошибки».
 *
 * Кто читает статью, считает сервер (wiki/readers.py, его сторожит
 * tests/test_wiki_article_access.py). Здесь — то, что с ответом делает экран, и
 * ошибки тут молчаливые: статья встанет не под тем разделом, человеку
 * припишут не те права, поиск не найдёт того, кто в списке есть, — и редактор
 * поверит экрану, потому что сверить его не с чем.
 *
 * Сам экран отрисовывается через react-dom/server: браузера в проекте нет, а
 * серверный рендер закрывает главное — что окно собирается из настоящего ответа
 * и говорит то, что должно. JSX здесь не используется: node --test гоняет .mjs
 * без сборки.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

import {
  ACCESS_LOADING, articleAccessUrl, buildPlaceTree, hiddenPlacesLabel, listHint, listTitle,
  loadArticleAccess, permissionsFromRights, personRows, positionTitle, rightsLabel,
  statusNotice, viaCaptions,
} from '../src/components/wiki/articleAccess.js';
import {
  SIMILARITY_THRESHOLD, matchScore, paginate, searchPeople, searchWords, trigrams,
  wordSimilarity,
} from '../src/components/wiki/peopleSearch.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');
const { mkdirSync, readFileSync } = require('node:fs');
const { join } = require('node:path');
const { fileURLToPath } = require('node:url');

const SPACE = { id: 11, name: 'Таксопарки', icon: '🚕' };

const step = (id, name, branch = false) => ({ id, name, branch });

const SZOV_PATH = [
  step(1, 'Коммерческий директор'), step(19, 'СЗоВ', true),
  step(2, 'Руководитель группы'), step(3, 'Супервайзер'), step(4, 'Оператор'),
];
const OP_PATH = [
  step(1, 'Коммерческий директор'), step(28, 'ОП', true),
  step(29, 'Руководитель группы'), step(30, 'Супервайзер'), step(31, 'Оператор'),
];

const place = (sectionId, path, extra = {}) => ({
  section_id: sectionId, name: path[path.length - 1]?.name, archived: false, space: SPACE,
  path, ...extra,
});

const human = (name, extra = {}) => ({
  name, role: 'operator', job_title: null, department: 'СЗоВ', rights: 'r', via: [], ...extra,
});

/* ── Запрос ─────────────────────────────────────────────────────────────── */

const HEADERS = { 'X-User-Id': '42' };
const ANSWER = { article: { id: 500 }, places: [], hidden_places: 0, people: [] };

/** Подмена axios.get: записывает вызовы и отвечает тем, что ей дали. */
const getter = (reply) => {
  const calls = [];
  const get = async (url, config) => {
    calls.push([url, config]);
    return reply(url);
  };
  return { get, calls };
};

test('запрос уходит в дверь статьи, с её номером и заголовками смотрящего', async () => {
  const { get, calls } = getter(() => ({ data: ANSWER }));
  const state = await loadArticleAccess(get, { base: '/api/wiki', articleId: 500,
                                               headers: HEADERS });
  assert.deepEqual(calls, [['/api/wiki/articles/500/access', { headers: HEADERS }]]);
  assert.deepEqual(state, { status: 'ready', data: ANSWER });
  assert.equal(articleAccessUrl('https://x.test/api/wiki', 7),
               'https://x.test/api/wiki/articles/7/access');
});

test('отказ сервера остаётся отказом и говорит его словами', async () => {
  const refuse = () => {
    throw Object.assign(new Error('Request failed with status code 403'), {
      response: { status: 403, data: { error: 'Статью видит тот, кто вправе её править' } },
    });
  };
  const state = await loadArticleAccess(getter(refuse).get, { base: '/api/wiki', articleId: 500 });
  assert.deepEqual(state, { status: 'failed',
                            error: 'Статью видит тот, кто вправе её править' });
});

test('обрыв сети — отказ по-русски, а не «Network Error»', async () => {
  /* У axios e.message всегда непустой и английский. Сбой, принятый за пустой
     ответ, был бы хуже всего: окно уверенно нарисовало бы «статья никому не
     открыта». */
  for (const message of ['Network Error', 'timeout of 30000ms exceeded',
                         'Request failed with status code 502']) {
    const fail = () => { throw new Error(message); };
    const state = await loadArticleAccess(getter(fail).get, { base: '/api/wiki', articleId: 500 });
    assert.deepEqual(state, { status: 'failed', error: 'Не удалось загрузить доступ' }, message);
  }
});

test('ответ не той формы — тоже отказ, а не пустое дерево и пустой список', async () => {
  for (const data of ['<html>502 Bad Gateway</html>', '', null, undefined, {},
                      { places: null, people: [] }, { places: [] }, { people: [] },
                      { places: [], people: 'все' }]) {
    const state = await loadArticleAccess(getter(() => ({ data })).get,
                                          { base: '/api/wiki', articleId: 500 });
    assert.equal(state.status, 'failed', JSON.stringify(data));
    assert.equal(state.error, 'Не удалось загрузить доступ');
  }
  // Статья без единого раздела и без читателей — настоящий ответ, а не сбой.
  const empty = await loadArticleAccess(getter(() => ({ data: { places: [], people: [] } })).get,
                                        { base: '/api/wiki', articleId: 500 });
  assert.equal(empty.status, 'ready');
  // Список людей смотрящему не положен — сервер говорит это словом null, и
  // это тоже ответ: окно нарисует одно дерево.
  const placesOnly = await loadArticleAccess(
    getter(() => ({ data: { places: [], people: null } })).get,
    { base: '/api/wiki', articleId: 500 });
  assert.equal(placesOnly.status, 'ready');
  assert.equal(placesOnly.data.people, null);
});

/* ── Дерево ─────────────────────────────────────────────────────────────── */

test('статья стоит под своим разделом, в конце пути от пространства', () => {
  const rows = buildPlaceTree([place(4, SZOV_PATH)]);
  assert.deepEqual(rows.map((r) => [r.kind, r.depth, r.name ?? null]), [
    ['space', 0, 'Таксопарки'],
    ['section', 1, 'Коммерческий директор'],
    ['section', 2, 'СЗоВ'],
    ['section', 3, 'Руководитель группы'],
    ['section', 4, 'Супервайзер'],
    ['section', 5, 'Оператор'],
    ['article', 6, null],
  ]);
  // Заметнее рисуется только раздел, в котором статья лежит.
  assert.deepEqual(rows.filter((r) => r.home).map((r) => r.name), ['Оператор']);
  assert.deepEqual(rows.filter((r) => r.branch).map((r) => r.name), ['СЗоВ']);
});

test('ветки-близнецы растут из одного ствола, а не дублируют его', () => {
  const rows = buildPlaceTree([place(4, SZOV_PATH), place(31, OP_PATH)]);
  const names = rows.filter((r) => r.kind === 'section').map((r) => r.name);
  assert.equal(names.filter((n) => n === 'Коммерческий директор').length, 1);
  assert.equal(names.filter((n) => n === 'Супервайзер').length, 2);
  assert.equal(rows.filter((r) => r.kind === 'article').length, 2);
  assert.equal(rows.filter((r) => r.kind === 'space').length, 1);
});

test('ключи строк дерева не повторяются у веток-близнецов', () => {
  /* «Супервайзер» у СЗоВ и «Супервайзер» у ОП — два разных раздела с одним
     именем. Ключ по имени склеил бы их, и React потерял бы одну из строк. */
  const rows = buildPlaceTree([place(4, SZOV_PATH), place(31, OP_PATH)]);
  assert.equal(new Set(rows.map((row) => row.key)).size, rows.length);
});

test('статья идёт раньше подразделов своего раздела', () => {
  /* Лежит в «Супервайзере» и в его подразделе «Оператор»: иначе она уехала бы
     под подраздел и читалась как лежащая только в нём. */
  const rows = buildPlaceTree([place(3, SZOV_PATH.slice(0, 4)), place(4, SZOV_PATH)]);
  const flat = rows.map((r) => (r.kind === 'article' ? '•статья' : r.name));
  assert.deepEqual(flat.slice(4), ['Супервайзер', '•статья', 'Оператор', '•статья']);
});

test('у каждого пространства своё дерево', () => {
  const other = { id: 12, name: 'Тез', icon: null };
  const rows = buildPlaceTree([place(4, SZOV_PATH), place(60, [step(60, 'Регламенты')],
                                                           { space: other })]);
  assert.deepEqual(rows.filter((r) => r.kind === 'space').map((r) => r.name),
                   ['Таксопарки', 'Тез']);
  assert.equal(rows.at(-2).name, 'Регламенты');
  // Без номера пространства места не слипаются в одно.
  const loose = buildPlaceTree([place(4, [step(4, 'А')], { space: { id: 1, name: 'Один' } }),
                                place(5, [step(5, 'Б')], { space: { id: 2, name: 'Два' } })]);
  assert.equal(loose.filter((r) => r.kind === 'space').length, 2);
});

test('архивное место помечено на своём разделе', () => {
  const rows = buildPlaceTree([place(42, [step(40, 'Маркетинг', true), step(42, 'Старые')],
                                     { archived: true })]);
  assert.deepEqual(rows.filter((r) => r.archived).map((r) => r.name), ['Старые']);
});

test('пустой ответ — пустое дерево, без исключения', () => {
  assert.deepEqual(buildPlaceTree([]), []);
  assert.deepEqual(buildPlaceTree(undefined), []);
});

test('место без пути не теряется: статья встаёт под пространством', () => {
  const rows = buildPlaceTree([place(4, [])]);
  assert.deepEqual(rows.map((r) => r.kind), ['space', 'article']);
});

/* ── Подписи окна ───────────────────────────────────────────────────────── */

test('скрытые разделы склоняются по-русски', () => {
  assert.equal(hiddenPlacesLabel(0), '');
  assert.equal(hiddenPlacesLabel(1), 'И ещё в одном разделе, который вам не виден.');
  assert.equal(hiddenPlacesLabel(2), 'И ещё в 2 разделах, которые вам не видны.');
  assert.equal(hiddenPlacesLabel(11), 'И ещё в 11 разделах, которые вам не видны.');
  assert.equal(hiddenPlacesLabel(21), 'И ещё в 21 разделе, который вам не виден.');
});

test('под списком людей скрытые разделы говорят и о своих читателях', () => {
  /* Люди, читающие статью только через такой раздел, в список не входят —
     без оговорки он выглядел бы полным. */
  assert.equal(hiddenPlacesLabel(0, true), '');
  assert.equal(hiddenPlacesLabel(1, true),
               'И ещё в одном разделе, который вам не виден, — его читателей в списке нет.');
  assert.equal(hiddenPlacesLabel(2, true),
               'И ещё в 2 разделах, которые вам не видны, — их читателей в списке нет.');
  assert.equal(hiddenPlacesLabel(21, true),
               'И ещё в 21 разделе, который вам не виден, — его читателей в списке нет.');
});

test('неопубликованная статья оговаривается, вышедшая — нет', () => {
  assert.equal(statusNotice('published'), null);
  assert.equal(statusNotice(undefined), null);
  assert.match(statusNotice('draft'), /не опубликована/);
  assert.match(statusNotice('on_approval'), /не опубликована/);
  assert.match(statusNotice('archived'), /в архиве/);
});

test('у невышедшей статьи список назван в будущем времени', () => {
  /* Список говорит, кто её откроет после выхода: «открыта» рядом с оговоркой
     «статья не опубликована» спорило бы с ней. */
  assert.equal(listTitle('published'), 'Кому открыта статья');
  assert.equal(listTitle(undefined), 'Кому открыта статья');
  for (const status of ['draft', 'on_approval', 'archived']) {
    assert.equal(listTitle(status), 'Кому откроется статья', status);
  }
});

test('подсказка говорит, кто в списке и что значит пометка', () => {
  const usual = listHint(false);
  assert.match(usual, /по правилам её раздела и разделов выше/);
  assert.match(usual, /гости и администраторы вики/);
  assert.match(usual, /если он не по отделу и должности/);
  // У статьи «по списку» раздел её не открывает — и в подсказке его нет.
  const byList = listHint(true);
  assert.match(byList, /по её списку, автор, гости и администраторы вики/);
  assert.doesNotMatch(byList, /раздел/);
});

/* ── Права ──────────────────────────────────────────────────────────────── */

test('права приезжают буквами и читаются в шесть флагов', () => {
  assert.deepEqual(permissionsFromRights('re'), {
    can_read: true, can_create: false, can_edit: true,
    can_publish: false, can_approve: false, can_delete: false,
  });
  assert.equal(Object.values(permissionsFromRights('rcepad')).every(Boolean), true);
  // Пустая строка и незнакомая буква прав не дают.
  assert.equal(Object.values(permissionsFromRights('')).some(Boolean), false);
  assert.equal(Object.values(permissionsFromRights('xyz')).some(Boolean), false);
  assert.equal(Object.values(permissionsFromRights(undefined)).some(Boolean), false);
});

test('как открыто — одним словом, теми же словами, что в «Доступе к разделу»', () => {
  const label = (rights) => rightsLabel(permissionsFromRights(rights));
  assert.deepEqual(label('r'), { value: 'Чтение', notes: null });
  assert.deepEqual(label('rce'), { value: 'Правка', notes: null });
  assert.deepEqual(label('rcepad'), { value: 'Полный доступ', notes: null });
});

test('право «создавать» про раздел, а не про эту статью', () => {
  /* Автор правит свою статью без права заводить новые — для неё это именно
     «Правка». И наоборот: право создавать без правки эту статью не меняет. */
  const label = (rights) => rightsLabel(permissionsFromRights(rights)).value;
  assert.equal(label('re'), 'Правка');
  assert.equal(label('rc'), 'Чтение');
  assert.equal(label('repad'), 'Полный доступ');
});

test('набор без удаления назван прямо, остальные ручные — расшифрованы', () => {
  const label = (rights) => rightsLabel(permissionsFromRights(rights));
  // Так выдают супервайзерам: десять строк «Свои права» подряд не сказали бы ничего.
  assert.deepEqual(label('rcepa'), { value: 'Всё, кроме удаления', notes: null });
  assert.deepEqual(label('repa'), { value: 'Всё, кроме удаления', notes: null });
  assert.deepEqual(label('rp'), { value: 'Свои права', notes: 'Читать · Публиковать' });
  assert.deepEqual(label('rced'), { value: 'Свои права', notes: 'Читать · Править · Удалять' });
  assert.deepEqual(label('rea'), { value: 'Свои права', notes: 'Читать · Править · Согласовывать' });
  // Удаление без правки — не «Чтение», публикация без согласования — не «Всё, кроме удаления».
  assert.deepEqual(label('rd'), { value: 'Свои права', notes: 'Читать · Удалять' });
  assert.deepEqual(label('rep'), { value: 'Свои права', notes: 'Читать · Править · Публиковать' });
});

/* ── Строка человека ────────────────────────────────────────────────────── */

test('должность — своя в бэк-офисе, иначе роль словами портала', () => {
  assert.equal(positionTitle({ role: 'operator' }), 'Оператор');
  assert.equal(positionTitle({ role: 'sv' }), 'Супервайзер');
  assert.equal(positionTitle({ role: 'admin' }), 'Админ');
  assert.equal(positionTitle({ role: 'marketing_manager', job_title: 'Видеограф' }), 'Видеограф');
  // Незнакомая роль остаётся как есть — лучше код, чем пустая строка.
  assert.equal(positionTitle({ role: 'auditor' }), 'Auditor');
  assert.equal(positionTitle({}), '');
  assert.equal(positionTitle(undefined), '');
});

test('пометка «откуда доступ» названа человеческими словами', () => {
  const caption = (kind, label = null) => viaCaptions([{ kind, label }]);
  assert.deepEqual(caption('user'), ['лично']);
  assert.deepEqual(caption('department_head'), ['как глава отдела']);
  assert.deepEqual(caption('group', 'Основа'), ['группа «Основа»']);
  assert.deepEqual(caption('direction', 'Регионы'), ['направление «Регионы»']);
  assert.deepEqual(caption('wiki_role', 'Редактор'), ['роль вики «Редактор»']);
  assert.deepEqual(caption('owner'), ['владелец раздела']);
  assert.deepEqual(caption('manual'), ['ручной доступ']);
  assert.deepEqual(caption('guest'), ['гостевой доступ']);
  assert.deepEqual(caption('article_rule'), ['правило статьи']);
  assert.deepEqual(caption('author'), ['автор статьи']);
  assert.deepEqual(caption('article_owner'), ['владелец статьи']);
  assert.deepEqual(caption('wiki_admin'), ['администратор вики']);
});

test('адресат без имени назван родом, незнакомая пометка не рисуется', () => {
  assert.deepEqual(viaCaptions([{ kind: 'group', label: null }]), ['по группе']);
  assert.deepEqual(viaCaptions([{ kind: 'direction' }]), ['по направлению']);
  assert.deepEqual(viaCaptions([{ kind: 'wiki_role' }]), ['по роли вики']);
  assert.deepEqual(viaCaptions([{ kind: 'телепатия' }, null, { kind: 'user' }]), ['лично']);
  assert.deepEqual(viaCaptions(undefined), []);
});

test('одна и та же пометка не повторяется', () => {
  assert.deepEqual(viaCaptions([{ kind: 'guest' }, { kind: 'guest' }, { kind: 'user' }]),
                   ['гостевой доступ', 'лично']);
});

test('строка человека: кто, должность и отдел, как открыто', () => {
  const [plain, special, nameless] = personRows([
    human('Абдрахманов Ерлан'),
    human('Қасымова Әлия', { role: 'sv', department: 'ОП', rights: 'rcepa',
                             via: [{ kind: 'user', label: null }, { kind: 'guest', label: null }] }),
    human(null, { department: null, role: 'super_admin', rights: 'rcepad' }),
  ]);
  assert.deepEqual([plain.title, plain.meta, plain.value, plain.notes],
                   ['Абдрахманов Ерлан', 'Оператор · СЗоВ', 'Чтение', null]);
  assert.deepEqual([special.title, special.meta, special.value],
                   ['Қасымова Әлия', 'Супервайзер · ОП · лично · гостевой доступ',
                    'Всё, кроме удаления']);
  // Без имени и без отдела строка не разваливается.
  assert.deepEqual([nameless.title, nameless.meta, nameless.value],
                   ['Без имени', 'Супер-админ', 'Полный доступ']);
  assert.deepEqual(personRows(undefined), []);
});

test('ручной набор прав в строке человека расшифрован', () => {
  const [row] = personRows([human('Яковлев Иван', { rights: 'rp' })]);
  assert.deepEqual([row.value, row.notes], ['Свои права', 'Читать · Публиковать']);
});

test('ключи строк людей не повторяются даже у тёзок', () => {
  const rows = personRows([human('Иванов Иван'), human('Иванов Иван'), human('Иванов Иван')]);
  assert.equal(new Set(rows.map((row) => row.key)).size, 3);
});

/* ── Поиск ──────────────────────────────────────────────────────────────── */

const PEOPLE = personRows([
  human('Абдрахманов Ерлан'),
  human('Алиев Арман', { role: 'sv' }),
  human('Алиева Алина'),
  human('Жумабеков Данияр', { department: 'ОП', via: [{ kind: 'user', label: null }] }),
  human('Иванов Иван', { role: 'marketing_manager', job_title: 'Видеограф', department: 'Маркетинг' }),
  human('Иванова Анна-Мария', { department: 'ОП' }),
  human('Қасымова Әлия', { department: 'ОП', via: [{ kind: 'guest', label: null }] }),
  human('Марк Аврелиев', { department: 'ОП' }),
  human('Салтанат Кызы', { department: 'Тез КЦ' }),
]);

const found = (query) => searchPeople(PEOPLE, query).map((row) => row.title);

test('триграммы режутся как в pg_trgm: два пробела перед словом, один после', () => {
  assert.deepEqual(trigrams('кот'), ['  к', ' ко', 'кот', 'от ']);
  assert.deepEqual(trigrams('а'), ['  а', ' а ']);
});

test('сходство считается той же мерой, что word_similarity в базе', () => {
  /* Значения сняты с pg_trgm 1.6 (PostgreSQL 16) запросом
     SELECT word_similarity(a, b) — поиск статей вики меряет опечатки именно
     ею (wiki/search.py), и здесь она обязана давать те же числа. */
  const reference = [
    ['иванв', 'иванов', 0.6667], ['абдр', 'абдрахманов', 0.8],
    ['абдрохманов', 'абдрахманов', 0.6], ['алия', 'алина', 0.6],
    ['петров', 'петрова', 0.8571], ['иванов', 'ивашов', 0.4286], ['иван', 'диван', 0.6],
    ['касымова', 'касымова', 1], ['жумабеков', 'жумабаев', 0.5],
    ['супервайзер', 'супервизор', 0.5], ['ерлан', 'нурлан', 0.5], ['аб', 'абаев', 0.6667],
    ['оператр', 'оператор', 0.75], ['zzzz', 'иванов', 0],
  ];
  for (const [needle, word, expected] of reference) {
    assert.equal(Math.round(wordSimilarity(needle, word) * 10000) / 10000, expected,
                 `${needle} ↔ ${word}`);
  }
  // Порог — тот же, что у поиска статей (wiki/search.py: _TRIGRAM_THRESHOLD).
  assert.equal(SIMILARITY_THRESHOLD, 0.45);
});

test('пустой запрос оставляет список как есть', () => {
  assert.equal(searchPeople(PEOPLE, ''), PEOPLE);
  assert.equal(searchPeople(PEOPLE, '   '), PEOPLE);
  assert.deepEqual(searchPeople(undefined, 'иванов'), []);
});

test('начало фамилии находит человека с первых букв', () => {
  assert.deepEqual(found('абдр'), ['Абдрахманов Ерлан']);
  assert.deepEqual(found('жу'), ['Жумабеков Данияр']);
  assert.deepEqual(found('ива'), ['Иванов Иван', 'Иванова Анна-Мария']);
});

test('опечатка прощается', () => {
  assert.deepEqual(found('иванв'), ['Иванов Иван', 'Иванова Анна-Мария']);
  assert.deepEqual(found('абдрохманов'), ['Абдрахманов Ерлан']);
  assert.deepEqual(found('жумабеов'), ['Жумабеков Данияр']);
  // Четырёх букв для этого уже хватает.
  assert.deepEqual(found('иваг'), ['Иванов Иван', 'Иванова Анна-Мария']);
});

test('регистр, ё и казахские буквы не различаются', () => {
  assert.deepEqual(found('КАСЫМОВА'), ['Қасымова Әлия']);
  assert.deepEqual(found('қасымова'), ['Қасымова Әлия']);
  assert.deepEqual(found('алия'), ['Қасымова Әлия']);
});

test('забытая раскладка и латиница чинятся', () => {
  assert.deepEqual(found('bdfyjd'), ['Иванов Иван', 'Иванова Анна-Мария']);
  assert.deepEqual(found('kasymova'), ['Қасымова Әлия']);
  assert.deepEqual(found('zhumabekov'), ['Жумабеков Данияр']);
});

test('слова запроса ищутся в любом порядке, и нужны все', () => {
  assert.deepEqual(found('ерлан абдр'), ['Абдрахманов Ерлан']);
  assert.deepEqual(found('абдр ерлан'), ['Абдрахманов Ерлан']);
  assert.deepEqual(found('иванов анна'), ['Иванова Анна-Мария']);
  assert.deepEqual(found('иванов пётр'), []);
});

test('ищется и по должности, отделу, пометке и правам', () => {
  assert.deepEqual(found('видеограф'), ['Иванов Иван']);
  assert.deepEqual(found('маркетинг'), ['Иванов Иван']);
  assert.deepEqual(found('супервайзер'), ['Алиев Арман']);
  assert.deepEqual(found('лично'), ['Жумабеков Данияр']);
  assert.deepEqual(found('гость'), ['Қасымова Әлия']);
  assert.deepEqual(found('иванов оп'), ['Иванова Анна-Мария']);
});

test('совпадение в имени стоит выше совпадения в отделе', () => {
  /* По слову «марк» первым должен быть Марк, а не весь отдел маркетинга. */
  assert.deepEqual(found('марк'), ['Марк Аврелиев', 'Иванов Иван']);
});

test('слово целиком стоит выше начала слова', () => {
  /* «оп» — и отдел ОП, и начало слова «оператор». Сперва те, у кого слово
     совпало целиком: иначе запрос по отделу отдавал бы весь список по алфавиту,
     и сотрудников ОП в нём пришлось бы искать глазами. */
  assert.deepEqual(found('оп'), [
    'Жумабеков Данияр', 'Иванова Анна-Мария', 'Қасымова Әлия', 'Марк Аврелиев',
    'Абдрахманов Ерлан', 'Алиева Алина', 'Салтанат Кызы',
  ]);
});

test('должность — не имя: тренер по должности стоит после Тренеровой', () => {
  const rows = personRows([human('Абаев Тимур', { role: 'trainer' }), human('Тренерова Алия')]);
  assert.deepEqual(searchPeople(rows, 'тренер').map((row) => row.title),
                   ['Тренерова Алия', 'Абаев Тимур']);
});

test('лучшее совпадение стоит первым, а не первое по алфавиту', () => {
  const titles = (rows, query) => searchPeople(personRows(rows), query).map((row) => row.title);
  // Началом слова — раньше, чем серединой.
  assert.deepEqual(titles([human('Диванов Пётр'), human('Иванов Иван')], 'иван'),
                   ['Иванов Иван', 'Диванов Пётр']);
  // Опечатка: ближе тот, у кого совпало больше.
  assert.deepEqual(titles([human('Жумабаев Тимур'), human('Жумабеков Данияр')], 'жумабеов'),
                   ['Жумабеков Данияр', 'Жумабаев Тимур']);
});

test('по слову о правах находятся те, кому так открыто', () => {
  const rows = personRows([
    human('Абаев Тимур'), human('Иванов Иван', { rights: 'rce' }),
    human('Яковлев Пётр', { rights: 'rcepad' }),
  ]);
  const titles = (query) => searchPeople(rows, query).map((row) => row.title);
  assert.deepEqual(titles('правка'), ['Иванов Иван']);
  assert.deepEqual(titles('полный'), ['Яковлев Пётр']);
  assert.deepEqual(titles('чтение'), ['Абаев Тимур']);
});

test('похожее идёт в ход, только когда точно не нашлось ничего', () => {
  /* «алиев» — и фамилия, и начало «Алиевой»: набравший без ошибок получает
     их, а не их вместе со всеми «почти такими же» (по сходству сюда попали бы
     ещё Әлия и Аврелиев). */
  assert.deepEqual(found('алиев'), ['Алиев Арман', 'Алиева Алина']);
  assert.deepEqual(found('мари'), ['Иванова Анна-Мария']);
  // Опечатался — получает ближайших, лучшие первыми.
  assert.deepEqual(found('алиеф'), ['Алиев Арман', 'Алиева Алина', 'Қасымова Әлия']);
});

test('в запросе из нескольких слов опечатка в одном не мешает остальным', () => {
  assert.deepEqual(found('иванв оп'), ['Иванова Анна-Мария']);
  assert.deepEqual(found('жумабеов данияр'), ['Жумабеков Данияр']);
});

test('двойные имена и фамилии ищутся по каждой части', () => {
  assert.deepEqual(found('мария'), ['Иванова Анна-Мария']);
  assert.deepEqual(found('анна-мария'), ['Иванова Анна-Мария']);
  assert.deepEqual(searchWords('Иванова Анна-Мария'), ['иванова', 'анна', 'мария']);
});

test('короткий запрос не находит всех подряд', () => {
  /* Две буквы «внутри слова» совпали бы с половиной списка — короткое слово
     ищется только началом. */
  assert.deepEqual(found('ан'), ['Иванова Анна-Мария']);
  assert.deepEqual(found('ов'), []);
  assert.deepEqual(found('zzzz'), []);
  /* Три буквы с опечаткой по сходству — это «те же две первые буквы»: «чта»
     нашла бы всех, у кого в строке «Чтение» (здесь — весь список), «доп» —
     всех с «Полным доступом». */
  assert.deepEqual(found('чта'), []);
  assert.deepEqual(found('ивн'), []);
  const full = personRows([human('Яковлев Пётр', { rights: 'rcepad' })]);
  assert.deepEqual(searchPeople(full, 'доп'), []);
});

test('оценка совпадения: начало слова, середина, похожее написание', () => {
  const words = searchWords('Абдрахманов Ерлан');
  assert.equal(matchScore(['ерлан'], words), 1);
  assert.equal(matchScore(['абдр'], words), 0.95);
  assert.equal(matchScore(['рахман'], words), 0.9);
  assert.equal(matchScore(['петров'], words), 0);
  // Похожее написание считается, только когда его разрешили; оценка — само
  // сходство.
  assert.equal(matchScore(['абдрохманов'], words), 0);
  assert.equal(matchScore(['абдрохманов'], words, true), 0.6);
  assert.equal(Math.round(matchScore(['абдрахманоф'], words, true) * 10000), 8333);
  // Оценка запроса — худшее из его слов; из вариантов написания — лучший.
  assert.equal(matchScore(['абдр рахман'], words), 0.9);
  assert.equal(matchScore(['петров', 'ерлан'], words), 1);
  assert.equal(matchScore(['абдр петров'], words, true), 0);
  assert.equal(matchScore([''], words), 0);
});

/* ── Страницы ───────────────────────────────────────────────────────────── */

test('страница списка: границы, счёт и номера', () => {
  const rows = Array.from({ length: 23 }, (_, i) => i + 1);
  assert.deepEqual(paginate(rows, 1, 10),
                   { items: rows.slice(0, 10), page: 1, pageCount: 3, total: 23, from: 1, to: 10 });
  assert.deepEqual(paginate(rows, 3, 10),
                   { items: [21, 22, 23], page: 3, pageCount: 3, total: 23, from: 21, to: 23 });
  assert.deepEqual(paginate(rows.slice(0, 10), 1, 10).pageCount, 1);
});

test('номер страницы прижимается к границам', () => {
  /* После поиска на пятой странице найденное может уместиться на одной —
     «страница 5 из 1» была бы пустым экраном без объяснения. */
  const rows = [1, 2, 3];
  assert.deepEqual(paginate(rows, 5, 10).items, [1, 2, 3]);
  assert.equal(paginate(rows, 5, 10).page, 1);
  assert.equal(paginate(rows, 0, 10).page, 1);
  assert.equal(paginate(rows, -3, 10).page, 1);
  assert.equal(paginate(rows, 'два', 10).page, 1);
});

test('пустой список — одна пустая страница, без деления на ноль', () => {
  assert.deepEqual(paginate([], 1, 10),
                   { items: [], page: 1, pageCount: 1, total: 0, from: 0, to: 0 });
  assert.deepEqual(paginate(undefined, 1, 10).items, []);
});

/* ── Экран ──────────────────────────────────────────────────────────────── */

async function loadView() {
  /* Собранный модуль кладём ВНУТРЬ проекта: из системной временной папки
     `import 'react'` не разрешается — node ищет node_modules вверх от файла. */
  const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
  mkdirSync(dir, { recursive: true });
  const outfile = join(dir, 'WikiArticleAccess.mjs');
  buildSync({
    entryPoints: [fileURLToPath(new URL('../src/components/wiki/WikiArticleAccess.jsx',
                                        import.meta.url))],
    bundle: true,
    format: 'esm',
    target: 'node18',
    outfile,
    external: ['react', 'react-dom', 'axios', 'lucide-react'],
    loader: { '.jsx': 'jsx' },
    logLevel: 'silent',
  });
  return import(`file://${outfile.replace(/\\/g, '/')}`);
}

const { default: WikiArticleAccess, ArticleAccessBody, ArticleAccessView } = await loadView();

const TITLE = 'Работа с разделом Акции';

/* Двадцать три человека: три страницы по десять. */
const CROWD = Array.from({ length: 20 }, (_, i) => human(
  `Сотрудник ${String(i + 1).padStart(2, '0')}`));

const DATA = {
  article: { id: 500, title: TITLE, status: 'published', by_list_only: false },
  places: [place(4, SZOV_PATH)],
  hidden_places: 0,
  people: [
    human('Абдрахманов Ерлан'),
    human('Алиев Арман', { role: 'sv', rights: 'rcepa' }),
    human('Қасымова Әлия', { department: 'ОП', via: [{ kind: 'guest', label: null }] }),
    ...CROWD,
  ],
};

const view = (data, props = {}) => renderToStaticMarkup(React.createElement(
  ArticleAccessView, { data, articleTitle: TITLE, ...props }));

/** Текст без разметки: названия разделов и статьи стоят в разных тегах. */
const text = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

/** Имена людей на странице, в порядке строк. */
const shownNames = (html) => [...html.matchAll(
  /<div class="break-words text-\[14px\] font-medium text-slate-900">([^<]*)<\/div>/g)]
  .map((match) => match[1]);

test('окно закрыто — ничего не рисуется', () => {
  const html = renderToStaticMarkup(React.createElement(WikiArticleAccess, {
    base: '/api/wiki', headers: {}, article: { id: 500, title: TITLE },
    open: false, onClose: () => {},
  }));
  assert.equal(html, '');
});

test('окно открыто — называет статью и ждёт ответа сервера', () => {
  const opened = (article) => renderToStaticMarkup(React.createElement(WikiArticleAccess, {
    base: '/api/wiki', headers: {}, article, open: true, onClose: () => {},
  }));
  const html = opened({ id: 500, title: TITLE, can_view_readers: true });
  assert.match(html, /Расположение и доступ/);
  assert.match(html, new RegExp(TITLE));
  assert.match(html, /Загружаем/);
  assert.match(html, /Готово/);
  // Редактору список людей не придёт — и окно его не обещает уже в заголовке.
  const plain = opened({ id: 500, title: TITLE });
  assert.match(text(plain), / Расположение /);
  assert.doesNotMatch(plain, /Расположение и доступ/);
  // Под две колонки окно шире; одному дереву хватает прежней ширины.
  assert.match(html, /max-w-5xl/);
  assert.match(plain, /max-w-xl/);
  assert.doesNotMatch(plain, /max-w-5xl/);
});

const body = (state) => renderToStaticMarkup(React.createElement(ArticleAccessBody, {
  state, articleTitle: TITLE, onRetry: () => {},
}));

test('отказ нарисован отказом: ни дерева, ни списка, ни «никому не открыта»', () => {
  const out = text(body({ status: 'failed', error: 'Не удалось загрузить доступ' }));
  assert.match(out, /Не удалось загрузить доступ/);
  assert.match(out, /Повторить/);
  assert.doesNotMatch(out, /Где лежит|не привязана|не открыта|Кому откр|Загружаем/);
});

test('пока ответа нет — «Загружаем», а не пустой экран и не вчерашний ответ', () => {
  const out = text(body(ACCESS_LOADING));
  assert.match(out, /Загружаем/);
  assert.doesNotMatch(out, /Где лежит|Повторить/);
});

test('ответ пришёл — окно показывает его, без «Загружаем» и «Повторить»', () => {
  const out = text(body({ status: 'ready', data: DATA }));
  assert.match(out, /Где лежит/);
  assert.match(out, /Кому открыта статья/);
  assert.doesNotMatch(out, /Загружаем|Повторить/);
});

test('дерево ведёт от пространства до статьи', () => {
  const out = text(view(DATA));
  assert.match(out, /Где лежит/);
  const order = ['Таксопарки', 'Коммерческий директор', 'СЗоВ', 'Руководитель группы',
                 'Супервайзер', 'Оператор', TITLE].map((name) => out.indexOf(name));
  assert.ok(order.every((at) => at >= 0), 'в дереве не хватает строки');
  assert.deepEqual([...order].sort((a, b) => a - b), order, 'строки дерева не по порядку');
});

test('ветка отдела и должность в ней нарисованы разными значками', () => {
  /* Те же значки, что в дереве «Структуры»: одно дерево в двух окнах не должно
     выглядеть двумя разными. В пути СЗоВ одна ветка отдела и четыре раздела. */
  const html = view(DATA);
  assert.equal((html.match(/lucide-building2/g) || []).length, 1);
  assert.equal((html.match(/lucide-folder/g) || []).length, 4);
});

test('строка статьи в дереве — отметка, а не кнопка', () => {
  /* Список людей у статьи один на все её места: выбирать между ними нечего, и
     кнопка без действия была бы обманкой. */
  const two = { ...DATA, places: [place(31, OP_PATH), DATA.places[0]] };
  const html = view(two);
  const tree = html.slice(0, html.indexOf('Кому открыта статья'));
  assert.doesNotMatch(tree, /<button|aria-pressed/);
  // Статья лежит в обоих местах — отмечены оба.
  assert.equal((tree.match(/bg-indigo-50/g) || []).length, 2);
  assert.equal((tree.match(new RegExp(TITLE, 'g')) || []).length, 2);
});

test('архивное место помечено в дереве', () => {
  const mixed = { ...DATA, places: [
    place(42, [step(42, 'Старые материалы')], { archived: true }), DATA.places[0],
  ] };
  assert.match(text(view(mixed)), /Старые материалы в архиве/);
  assert.doesNotMatch(text(view(DATA)), /в архиве/);
});

test('список людей: первая страница, счёт и поиск', () => {
  const html = view(DATA);
  assert.deepEqual(shownNames(html).slice(0, 4),
                   ['Абдрахманов Ерлан', 'Алиев Арман', 'Қасымова Әлия', 'Сотрудник 01']);
  assert.equal(shownNames(html).length, 10);
  const out = text(html);
  assert.match(out, /Кому открыта статья 23/);
  assert.match(out, /1–10 из 23/);
  assert.match(html, /placeholder="Имя, должность или отдел"/);
  // Пейджер стоит НАД списком, как в каталоге вики: до него не надо
  // прокручивать страницу, и после перехода человек видит её начало.
  assert.ok(html.indexOf('1–10 из 23') < html.indexOf('Абдрахманов Ерлан'));
  assert.ok(html.indexOf('placeholder=') < html.indexOf('1–10 из 23'));
  // Строка человека: должность и отдел слева, как открыто — справа.
  assert.match(out, /Алиев Арман Супервайзер · СЗоВ Всё, кроме удаления/);
  assert.match(out, /Қасымова Әлия Оператор · ОП · гостевой доступ Чтение/);
});

test('вторая и последняя страницы показывают своих людей', () => {
  const second = view(DATA, { defaultPage: 2 });
  assert.deepEqual(shownNames(second).slice(0, 2), ['Сотрудник 08', 'Сотрудник 09']);
  assert.match(text(second), /11–20 из 23/);
  const last = view(DATA, { defaultPage: 3 });
  assert.deepEqual(shownNames(last), ['Сотрудник 18', 'Сотрудник 19', 'Сотрудник 20']);
  assert.match(text(last), /21–23 из 23/);
});

test('поиск сужает список и пересчитывает страницы', () => {
  const html = view(DATA, { defaultQuery: 'абдрохманов' });
  assert.deepEqual(shownNames(html), ['Абдрахманов Ерлан']);
  // Одна страница — пейджера нет, а счёт в заголовке остаётся общим.
  assert.doesNotMatch(text(html), / из \d+/);
  assert.match(text(html), /Кому открыта статья 23/);

  const many = view(DATA, { defaultQuery: 'сотрудник' });
  assert.equal(shownNames(many).length, 10);
  assert.match(text(many), /1–10 из 20/);
});

test('страница за пределами найденного не оставляет пустого экрана', () => {
  const html = view(DATA, { defaultQuery: 'алиев', defaultPage: 3 });
  assert.deepEqual(shownNames(html), ['Алиев Арман']);
});

test('никого не нашлось — так и сказано, поле поиска остаётся', () => {
  const html = view(DATA, { defaultQuery: 'zzzz' });
  assert.match(text(html), /Никого не нашлось\./);
  assert.match(html, /placeholder="Имя, должность или отдел"/);
  assert.deepEqual(shownNames(html), []);
});

test('список людей слева, дерево справа; на узком экране дерево сверху', () => {
  /* Решение владельца 06.10.2026: «слева список людей, кому открыт доступ, с
     пагинацией, и справа дерево, где находится статья». В разметке дерево
     стоит первым — так колонки встают на телефоне, — а вправо его уводит
     порядок колонок. */
  const html = view({ ...DATA, article: { ...DATA.article, status: 'draft' } });
  assert.match(html, /class="grid gap-5 lg:grid-cols-\[minmax\(0,1fr\)_minmax\(0,380px\)\] lg:items-start"/);
  const tree = html.indexOf('lg:order-2');
  const list = html.indexOf('lg:order-1');
  assert.ok(tree > 0 && list > tree, 'дерево в разметке раньше списка');
  assert.ok(html.indexOf('Таксопарки') < list && html.indexOf('Абдрахманов Ерлан') > list);
  // Оговорка о невышедшей статье — про читателей, и стоит она в их колонке.
  assert.ok(html.indexOf('не опубликована') > html.indexOf('Кому откроется статья'));
  // У одного дерева колонок нет.
  assert.doesNotMatch(view({ ...DATA, people: null }), /lg:grid-cols|lg:order-/);
});

test('без списка людей окно — одно дерево', () => {
  /* Редактору сервер людей не присылает (people: null). Ни заголовка списка, ни
     поиска, ни «никому не открыта» — и ни слова про читателей: всё это было бы
     про список, которого у него нет. */
  const html = view({ ...DATA, people: null, hidden_places: 1,
                      article: { ...DATA.article, status: 'draft' } });
  const flat = text(html);
  assert.match(flat, /Таксопарки/);
  assert.match(flat, new RegExp(TITLE));
  assert.match(flat, /И ещё в одном разделе, который вам не виден\./);
  for (const absent of [/Кому откр/, /Где лежит/, /не открыта никому/, /не опубликована/,
                        /читателей/, /Абдрахманов/, /Никого не нашлось/]) {
    assert.doesNotMatch(flat, absent);
  }
  assert.doesNotMatch(html, /<input/);
  assert.doesNotMatch(html, /<button/);
});

test('короткий список — без поиска и без пейджера', () => {
  /* Над пятью строками поле поиска было бы лишним органом управления. */
  const few = { ...DATA, people: DATA.people.slice(0, 5) };
  const html = view(few);
  assert.doesNotMatch(html, /<input/);
  assert.doesNotMatch(text(html), / из \d+/);
  assert.equal(shownNames(html).length, 5);
  assert.match(text(html), /Кому открыта статья 5/);
  // Ровно страница — тоже без поиска: искать среди десяти строк на экране незачем.
  assert.doesNotMatch(view({ ...DATA, people: DATA.people.slice(0, 10) }), /<input/);
  assert.match(view({ ...DATA, people: DATA.people.slice(0, 11) }), /<input/);
});

test('статья никому не открыта — так и сказано, без нуля в заголовке', () => {
  const out = text(view({ ...DATA, people: [] }));
  assert.match(out, /Статья не открыта никому\./);
  assert.doesNotMatch(out, /Кому открыта статья 0/);
  assert.doesNotMatch(out, /Никого не нашлось/);
});

test('скрытые разделы посчитаны, но не названы', () => {
  const out = text(view({ ...DATA, hidden_places: 2 }));
  assert.match(out, /И ещё в 2 разделах, которые вам не видны, — их читателей в списке нет/);
  assert.doesNotMatch(text(view(DATA)), /не видн/);
});

test('ни одного видимого раздела — так и сказано', () => {
  const out = text(view({ ...DATA, places: [], hidden_places: 1 }));
  assert.match(out, /Разделы этой статьи вам не видны/);
  // Одной фразы достаточно: «и ещё в N разделах» под пустым деревом — повтор.
  assert.doesNotMatch(text(view({ ...DATA, places: [], hidden_places: 2 })), /И ещё в/);
  // Список при этом остаётся: автора и гостей статьи видно и без её разделов.
  assert.match(out, /Абдрахманов Ерлан/);

  const orphan = text(view({ ...DATA, places: [], hidden_places: 0 }));
  assert.match(orphan, /Статья не привязана ни к одному разделу/);
});

test('черновик оговорён, и список назван в будущем времени', () => {
  const out = text(view({ ...DATA, article: { ...DATA.article, status: 'draft' } }));
  assert.match(out, /Статья не опубликована/);
  assert.match(out, /Кому откроется статья 23/);
  assert.doesNotMatch(out, /Кому открыта статья/);
  assert.doesNotMatch(text(view(DATA)), /не опубликована|откроется/);
});

test('статья «только по списку» сказана одной плашкой над тем же списком', () => {
  const listed = { ...DATA, article: { ...DATA.article, by_list_only: true } };
  const out = text(view(listed));
  assert.match(out, /Статья открыта только по списку — правило раздела её не открывает/);
  assert.match(out, /Абдрахманов Ерлан/);
  // Дерево при этом остаётся: где статья лежит, от режима доступа не зависит.
  assert.match(out, /Коммерческий директор/);
  assert.doesNotMatch(text(view(DATA)), /только по списку/);
});

test('пояснение к списку спрятано под «i» у правого края строки', () => {
  const html = view(DATA);
  assert.match(html, /aria-label="Кто попадает в список"/);
  // Сам текст подсказки на экране не стоит — он нужен один раз.
  assert.doesNotMatch(text(html), /по правилам её раздела/);
});

test('окно — справка: кроме поиска и страниц, управлять в нём нечем', () => {
  const html = view(DATA);
  assert.doesNotMatch(html, /role="switch"/);
  assert.doesNotMatch(html, /<select|<textarea/);
  assert.equal((html.match(/<input/g) || []).length, 1);
  assert.doesNotMatch(text(html), /Выдать доступ|Сохранить|Удалить/);
});

test('эмодзи пространства показан, имя иконки — нет', () => {
  assert.match(view(DATA), /🚕/);
  const named = { ...DATA, places: [{ ...DATA.places[0],
                                      space: { ...SPACE, icon: 'book' } }] };
  assert.doesNotMatch(text(view(named)), /book/);
});

/* ── То, что видно только в браузере ─────────────────────────────────────── */

const strip = (source) => source
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/[^\n]*$/gm, '');

const SHEET_SRC = strip(readFileSync(
  fileURLToPath(new URL('../src/components/wiki/WikiArticleAccess.jsx', import.meta.url)),
  'utf8'));

test('подсказка «i» раскрывается влево от правого края строки', () => {
  /* Сразу за заголовком, с пузырьком вправо, «i» на телефоне стояла у края
     экрана: из 256 px пузырька тело окна срезало две трети. Серверный рендер
     пузырька не рисует вовсе, поэтому сторожим саму вёрстку: подсказка в окне
     одна, стоит у правого края и раскрывается влево. */
  assert.equal((SHEET_SRC.match(/<IosHint/g) || []).length, 1);
  assert.match(SHEET_SRC, /<IosHint align="right"/);
  const head = SHEET_SRC.slice(SHEET_SRC.indexOf('const GroupHead'),
                               SHEET_SRC.indexOf('<IosHint'));
  assert.match(head, /justify-between/);
});

test('окно: запрос — только при открытии, устаревший ответ выбрасывается', () => {
  /* Серверный рендер эффектов не исполняет, поэтому проводку окна сторожим по
     исходнику. Каждая строка здесь — отдельная молчаливая поломка: запрос на
     каждое открытие статьи без нажатия кнопки; ответ прошлой статьи в окне
     следующей; «Повторить» без действия; окно под сайдбаром портала. */
  const flat = SHEET_SRC.replace(/\s+/g, ' ');
  assert.match(flat, /const articleId = article\?\.id;/);
  assert.match(flat, /useEffect\(\(\) => \{ if \(!open\) return undefined; return load\(\); \}, \[open, load\]\);/);
  assert.match(flat, /loadArticleAccess\(axios\.get, \{ base, articleId, headers \}\) \.then\(\(next\) => \{ if \(!cancelled\) setState\(next\); \}\);/);
  assert.match(flat, /return \(\) => \{ cancelled = true; \};/);
  assert.match(flat, /if \(open !== wasOpen\) \{ setWasOpen\(open\); if \(open\) setState\(ACCESS_LOADING\); \}/);
  assert.match(flat, /<ArticleAccessBody state=\{state\} articleTitle=\{article\?\.title\} onRetry=\{load\} \/>/);
  assert.match(flat, /<button type="button" className=\{iosBtnSecondary\} onClick=\{onRetry\}>/);
  assert.match(flat, /<IosModal open=\{open\} onClose=\{onClose\}/);
  // Окно рисуется в body и не теряет кегль раздела на телефоне.
  assert.match(flat, /return createPortal\(<div className="wiki-scope contents">\{modal\}<\/div>, document\.body\);/);
});

test('окно: поиск начинает с первой страницы, страницы листает пейджер', () => {
  /* Набрал фамилию на третьей странице — и остался бы на третьей странице
     найденного, то есть на пустом месте. */
  const flat = SHEET_SRC.replace(/\s+/g, ' ');
  assert.match(flat, /onChange=\{\(event\) => \{ setQuery\(event\.target\.value\); setPage\(1\); \}\}/);
  assert.match(flat, /<IosPager page=\{shown\.page\} pageCount=\{shown\.pageCount\} total=\{shown\.total\} from=\{shown\.from\} to=\{shown\.to\} onPage=\{setPage\}/);
  assert.match(flat, /const found = useMemo\(\(\) => searchPeople\(people, query\), \[people, query\]\);/);
  assert.match(flat, /const shown = paginate\(found, page, PAGE_SIZE\);/);
});

/* ── Проводка в статье ──────────────────────────────────────────────────── */

const ARTICLE_SRC = readFileSync(
  fileURLToPath(new URL('../src/components/wiki/WikiArticle.jsx', import.meta.url)), 'utf8');

test('статья передаёт окну всё, без чего оно не откроется', () => {
  const flat = strip(ARTICLE_SRC).replace(/\s+/g, ' ');
  assert.match(flat, /<WikiArticleAccess base=\{base\} headers=\{headers\} article=\{article\} open=\{accessOpen\} onClose=\{\(\) => setAccessOpen\(false\)\} \/>/);
  assert.match(flat, /onClick=\{\(\) => setAccessOpen\(true\)\}/);
});

test('кнопка и окно названы по тому, что человек получит', () => {
  /* Редактору — где лежит статья («Расположение»); супер-админу — ещё и кому
     она открыта («Доступ»). Признак считает сервер (can_view_readers): по
     нему же он решает, отдавать ли список, и кнопка не обещает лишнего. */
  const flat = strip(ARTICLE_SRC).replace(/\s+/g, ' ');
  assert.match(flat, /const seesPlaces = !!\(article\?\.permissions\?\.can_edit \|\| article\?\.can_view_readers\);/);
  assert.match(flat, /title=\{article\.can_view_readers \? 'Где лежит статья и кому она открыта' : 'Где лежит статья'\}/);
  assert.match(flat, /\{article\.can_view_readers \? <><KeyRound size=\{14\} \/> Доступ<\/> : <><FolderTree size=\{14\} \/> Расположение<\/>\}/);
  assert.equal((flat.match(/\{seesPlaces && \(/g) || []).length, 2, 'и кнопка, и окно');
  assert.match(SHEET_SRC.replace(/\s+/g, ' '),
               /title=\{article\?\.can_view_readers \? 'Расположение и доступ' : 'Расположение'\}/);
});

test('кнопка «Доступ» стоит в панели статьи рядом с «Историей»', () => {
  const history = ARTICLE_SRC.indexOf('<History size={14} /> История');
  const access = ARTICLE_SRC.indexOf('<KeyRound size={14} /> Доступ');
  const edit = ARTICLE_SRC.indexOf('<Pencil size={14} /> Править');
  assert.ok(history > 0 && access > history && edit > access,
            'порядок кнопок: История → Доступ → … → Править');
});

test('Esc в режиме «Во весь экран» не сворачивает статью под открытым окном', () => {
  /* Человек жмёт Esc, чтобы убрать окно. Слушатель режима про окно не знал, и
     статья под затемнением молча выходила из полноэкранного вида. */
  const code = strip(ARTICLE_SRC);
  assert.match(code, /modalOpenRef\.current = historyOpen \|\| accessOpen;/);
  assert.match(code,
               /event\.key === 'Escape' && !modalOpenRef\.current\) setImmersive\(false\)/);
});
