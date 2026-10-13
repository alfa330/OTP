/*
 * «Оплата счетов»: фото людей на карточках досок.
 *
 * Просьба владельца 13.10.2026: «сделай, чтобы аватарки в разделе отображались корректно».
 * На карточках стояли серые кружки с инициалами — раздел не знал о фото из профиля; в
 * «Задачах» на тех же местах стоят фото. Теперь доска отдаёт ссылки на фото
 * (tests/test_payments_faces.py), а здесь — что с ними делает интерфейс:
 *   - boardColumnsWithPhotos раскладывает ссылки из общего списка ответа по карточкам;
 *   - cardFaces привязывает фото к тому же человеку, чьё имя стоит на карточке;
 *   - Face рисует фото, а без него — инициалы; подразделение остаётся значком.
 * Чего без браузера не видно — фото не загрузилось (ссылка истекла) и вместо сломанной
 * картинки встают инициалы — снимает проход стенда.
 *
 * Запуск: node --test tests/payments_card_faces.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import { boardColumnsWithPhotos, cardFaces } from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешние react и
// lucide-react не резолвятся. Каталог .cache не отслеживается git.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-card-faces-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

let loadedUi = null;
async function ui() {
    if (loadedUi) return loadedUi;
    const stubPath = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stubPath, 'const never = () => new Promise(() => {});\nexport default { get: never, post: never, patch: never, delete: never };\n');
    const result = await esbuild.build({
        entryPoints: [new URL('src/components/payments/paymentsUi.jsx', ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
        bundle: true,
        write: false,
        format: 'esm',
        platform: 'node',
        target: 'node18',
        jsx: 'transform',
        loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty' },
        external: ['react', 'react-dom', 'lucide-react'],
        alias: { axios: stubPath },
        logLevel: 'silent',
    });
    const outPath = join(OUT_DIR, 'ui.mjs');
    writeFileSync(outPath, result.outputFiles[0].text);
    loadedUi = await import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
    return loadedUi;
}

const h = React.createElement;
const PHOTO_A = 'https://photos.example/bucket/avatars/10.jpg?sig=a';
const PHOTO_B = 'https://photos.example/bucket/avatars/11.png?sig=b';

test('ответ доски: ссылки на фото раскладываются по карточкам из общего списка', () => {
    const payload = {
        photos: { 10: PHOTO_A, 11: PHOTO_B },
        columns: [
            { key: 'new', label: 'Новые', count: 2, cards: [
                { id: 1, initiator_name: 'Иванова Айгерим', initiator_photo: 10, subtask: { kind: 'approval', state: 'open', assignee_name: 'Петров Данияр', role_label: 'Утвердитель', assignee_photo: 11, done_by_photo: null } },
                { id: 2, initiator_name: 'Иванова Айгерим', initiator_photo: 10, subtask: null },
            ] },
            { key: 'approved', label: 'Согласовано', count: 1, cards: [
                { id: 3, initiator_name: 'Карабек Ержан', initiator_photo: null, subtask: { kind: 'approval', state: 'done', done_by_name: 'Петров Данияр', assignee_photo: null, done_by_photo: 11 } },
            ] },
        ],
    };
    const [fresh, approved] = boardColumnsWithPhotos(payload);
    assert.equal(fresh.key, 'new');
    assert.equal(fresh.count, 2);
    assert.equal(fresh.cards[0].initiator_avatar_url, PHOTO_A);
    assert.equal(fresh.cards[0].subtask.assignee_avatar_url, PHOTO_B);
    assert.equal(fresh.cards[0].subtask.done_by_avatar_url, '');
    assert.equal(fresh.cards[0].subtask.assignee_name, 'Петров Данияр', 'остальное в карточке не тронуто');
    // Одно лицо на двух карточках — одна и та же ссылка; карточка без подзадачи остаётся без неё.
    assert.equal(fresh.cards[1].initiator_avatar_url, PHOTO_A);
    assert.equal(fresh.cards[1].subtask, null);
    assert.equal(approved.cards[0].initiator_avatar_url, '');
    assert.equal(approved.cards[0].subtask.done_by_avatar_url, PHOTO_B);
    // И дальше — обычным путём: лица карточки получают фото.
    assert.equal(cardFaces(fresh.cards[0]).from.avatar_url, PHOTO_A);
    assert.equal(cardFaces(fresh.cards[0]).to.avatar_url, PHOTO_B);
    assert.equal(cardFaces(approved.cards[0]).to.avatar_url, PHOTO_B);
    assert.equal('avatar_url' in cardFaces(approved.cards[0]).from, false);
    // Исходный ответ не меняется.
    assert.equal('initiator_avatar_url' in payload.columns[0].cards[0], false);
});

test('ответ доски без списка фото или с незнакомым номером — карточки без фото, а не ошибка', () => {
    const cards = [{ id: 1, initiator_name: 'Иванова Айгерим', initiator_photo: 10, subtask: { kind: 'approval', state: 'open', assignee_photo: 99 } }];
    // Прежний сервер (фронт выложился раньше бэкенда): списка нет вовсе.
    assert.equal(boardColumnsWithPhotos({ columns: [{ key: 'new', cards }] })[0].cards[0].initiator_avatar_url, '');
    // Номер есть, ссылки на него нет.
    const [column] = boardColumnsWithPhotos({ photos: { 10: PHOTO_A }, columns: [{ key: 'new', cards }] });
    assert.equal(column.cards[0].initiator_avatar_url, PHOTO_A);
    assert.equal(column.cards[0].subtask.assignee_avatar_url, '');
    assert.deepEqual(boardColumnsWithPhotos(null), []);
    assert.deepEqual(boardColumnsWithPhotos({}), []);
    assert.deepEqual(boardColumnsWithPhotos({ columns: [{ key: 'new' }] }), [{ key: 'new', cards: [] }]);
});

test('доска и окно колонки читают ответ через раскладку фото', () => {
    const board = source('src/components/payments/PaymentsBoard.jsx');
    assert.ok(board.includes('setColumns(boardColumnsWithPhotos(response.data));'));
    assert.ok(board.includes('const got = boardColumnsWithPhotos(response.data)[0] || { cards: [], count: 0 };'));
    assert.doesNotMatch(board, /response\.data\?\.columns/, 'мимо раскладки ответ доски никто не читает');
});

test('фото привязано к тому же человеку, чьё имя на карточке', () => {
    const card = {
        initiator_name: 'Иванова Айгерим', initiator_avatar_url: PHOTO_A, column: 'new',
        subtask: { kind: 'manager_approval', state: 'open', assignee_name: 'Петров Данияр', role_label: 'Непосредственный руководитель', assignee_avatar_url: PHOTO_B },
    };
    const faces = cardFaces(card);
    assert.deepEqual(faces.from, { name: 'Иванова Айгерим', title: 'Инициатор: Иванова Айгерим', role: false, avatar_url: PHOTO_A });
    assert.deepEqual(faces.to, { name: 'Петров Данияр', title: 'Согласует: Петров Данияр', role: false, avatar_url: PHOTO_B });
    // Этап закрыт — на карточке тот, кто его закрыл, со своим фото (не фото исполнителя).
    const done = cardFaces({ ...card, column: 'approved', subtask: { kind: 'approval', state: 'done', done_by_name: 'Карабек Ержан', role_label: 'Утвердитель', assignee_avatar_url: PHOTO_B, done_by_avatar_url: PHOTO_A } });
    assert.equal(done.to.title, 'Согласовал: Карабек Ержан');
    assert.equal(done.to.avatar_url, PHOTO_A);
});

test('без фото в профиле лицо остаётся прежним — поля avatar_url у него нет', () => {
    const faces = cardFaces({
        initiator_name: 'Иванова Айгерим', initiator_avatar_url: null,
        subtask: { kind: 'approval', state: 'open', assignee_name: 'Петров Данияр', role_label: 'Утвердитель', assignee_avatar_url: '   ' },
    });
    assert.deepEqual(faces.from, { name: 'Иванова Айгерим', title: 'Инициатор: Иванова Айгерим', role: false });
    assert.deepEqual(faces.to, { name: 'Петров Данияр', title: 'Согласует: Петров Данияр', role: false });
});

test('подразделению фото не приставить, и к имени из запасного поля — тоже', () => {
    // Счёт у бухгалтерии целиком: значок, даже если ссылка откуда-то взялась.
    const department = cardFaces({
        initiator_name: 'Иванова Айгерим',
        subtask: { kind: 'invoice_payment', state: 'open', assignee_name: 'Бухгалтерия', role_label: 'Бухгалтерия', assignee_avatar_url: PHOTO_B },
    });
    assert.deepEqual(department.to, { name: 'Бухгалтерия', title: 'Исполнитель: Бухгалтерия', role: true });
    const paid = cardFaces({
        initiator_name: 'Иванова Айгерим', column: 'paid',
        subtask: { kind: 'invoice_payment', state: 'done', done_by_name: 'Бухгалтерия', role_label: 'Бухгалтерия', done_by_avatar_url: PHOTO_B },
    });
    assert.deepEqual(paid.to, { name: 'Бухгалтерия', title: 'Оплатил: Бухгалтерия', role: true });
    // Имя взято из строки заявки (у подзадачи его нет) — фото подзадачи к нему не относится.
    const fallback = cardFaces({
        initiator_name: 'Иванова Айгерим', current_assignee_name: 'Петров Данияр',
        subtask: { kind: 'approval', state: 'open', assignee_name: '', role_label: 'Утвердитель', assignee_avatar_url: PHOTO_B },
    });
    assert.deepEqual(fallback.to, { name: 'Петров Данияр', title: 'Согласует: Петров Данияр', role: false });
});

test('вернули инициатору — одно лицо, его фото на месте', () => {
    const faces = cardFaces({
        initiator_name: 'Иванова Айгерим', initiator_avatar_url: PHOTO_A, column: 'clarification',
        subtask: { kind: 'invoice_payment', state: 'waiting', assignee_name: 'Бухгалтерия' },
    });
    assert.deepEqual(faces, { from: { name: 'Иванова Айгерим', title: 'Ждём ответа: Иванова Айгерим', role: false, avatar_url: PHOTO_A }, to: null });
});

test('кружок: фото из профиля, без фото — инициалы, подразделение — значок', async () => {
    const { Face } = await ui();
    const photo = renderToStaticMarkup(h(Face, { person: { name: 'Иванова Айгерим', title: 'Инициатор: Иванова Айгерим', role: false, avatar_url: PHOTO_A } }));
    assert.match(photo, /^<span title="Инициатор: Иванова Айгерим" data-face="photo" class="inline-grid shrink-0 place-items-center overflow-hidden rounded-full bg-slate-200 font-semibold text-slate-600" style="width:20px;height:20px;font-size:9\.5px">/);
    // Фото заполняет кружок целиком и обрезается по нему (размер — свой, в пикселях: «100 %» в сетке
    // кружка считался бы по самой картинке); подписи у картинки нет — имя в подсказке кружка.
    assert.match(photo, /<img src="https:\/\/photos\.example\/bucket\/avatars\/10\.jpg\?sig=a" alt="" class="block object-cover" style="width:20px;height:20px" loading="lazy" decoding="async" draggable="false"\/>/);
    const large = renderToStaticMarkup(h(Face, { size: 38, person: { name: 'Иванова Айгерим', role: false, avatar_url: PHOTO_A } }));
    assert.match(large, /<img [^>]*style="width:38px;height:38px"/);
    assert.doesNotMatch(photo, /ИА/);

    const initials = renderToStaticMarkup(h(Face, { person: { name: 'Иванова Айгерим', title: 'Инициатор: Иванова Айгерим', role: false } }));
    assert.match(initials, /data-face="initials"[^>]*>ИА<\/span>$/);
    assert.doesNotMatch(initials, /<img/);

    const group = renderToStaticMarkup(h(Face, { person: { name: 'Бухгалтерия', title: 'Исполнитель: Бухгалтерия', role: true, avatar_url: PHOTO_B } }));
    assert.match(group, /data-face="group"/);
    assert.match(group, /<svg/);
    assert.doesNotMatch(group, /<img/);
});

test('пара лиц на карточке: фото слева и справа, имена — в подписи для читалки', async () => {
    const { CardFaces } = await ui();
    const html = renderToStaticMarkup(h(CardFaces, { faces: cardFaces({
        initiator_name: 'Иванова Айгерим', initiator_avatar_url: PHOTO_A,
        subtask: { kind: 'approval', state: 'open', assignee_name: 'Петров Данияр', role_label: 'Утвердитель' },
    }) }));
    assert.match(html, /aria-label="Инициатор: Иванова Айгерим → Согласует: Петров Данияр"/);
    assert.equal((html.match(/data-face="photo"/g) || []).length, 1);
    assert.equal((html.match(/data-face="initials"/g) || []).length, 1);
    assert.match(html, /data-face="initials"[^>]*>ПД<\/span>/);
});

test('фото не загрузилось — встают инициалы; карточку тащат за любое место', () => {
    const ui = source('src/components/payments/paymentsUi.jsx');
    const face = ui.slice(ui.indexOf('export const Face = '), ui.indexOf('const FlowArrow = '));
    assert.ok(face.length > 600, 'разметка кружка не найдена');
    // Запоминается сама негодная ссылка: придёт свежая — фото вернётся без перезагрузки страницы.
    assert.ok(face.includes("const [brokenUrl, setBrokenUrl] = useState('');"));
    assert.ok(face.includes('const photo = Boolean(url) && url !== brokenUrl;'));
    assert.ok(face.includes('onError={() => setBrokenUrl(url)}'));
    // Картинка по умолчанию тащится сама — на доске с перетаскиванием это увело бы карточку из руки.
    assert.ok(face.includes('draggable={false}'));
    // Подразделению фото не положено ни при каких данных.
    assert.ok(face.includes("const url = person?.role ? '' : String(person?.avatar_url || '').trim();"));
});
