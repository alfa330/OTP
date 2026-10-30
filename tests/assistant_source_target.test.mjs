// Источник под ответом помощника: куда ведёт чип и когда под ответом есть кнопка
// «Отправить супервайзеру» (src/components/assistant/sourceTarget.js).

import test from 'node:test';
import assert from 'node:assert/strict';

import {
    baigaFocusOf, canEscalateMessage, classifierPrefillOf, parkTarget, sourceDisabled, sourceDoor,
} from '../src/components/assistant/sourceTarget.js';

const ARTICLE = { slug: 'rent', title: 'Аренда', source_kind: 'article' };
// «Классификатор авто»: статья-калькулятор и машина из ответа (wiki/classifier.py).
const CAR = {
    slug: 'klassifikator-avto', tab: 'classifier', source_kind: 'classifier',
    ref_key: 'Toyota|Camry', ref_id: 2015, ref_city: 'almaty',
};
const OFFICE = { slug: '', tab: 'offices', ref_id: 8, ref_city: 'Шымкент', source_kind: 'office' };
const BAIGA = { slug: '', tab: 'baiga', ref_id: 3, ref_key: 'ZZ123456', source_kind: 'baiga' };
// Карточка «Парков» (wiki/park_cards.py): страница парка по слагу из ref_key.
const PARK = { slug: '', tab: 'parks', ref_id: 1, ref_key: 'itaxi', source_kind: 'park' };
const KINDS = ['answer', 'clarify', 'no_answer'];

test('у каждого источника своя дверь', () => {
    assert.equal(sourceDoor(ARTICLE), 'article');
    assert.equal(sourceDoor(OFFICE), 'directory');
    assert.equal(sourceDoor(BAIGA), 'baiga');
    // «Списки Байги» — раздел портала, а не вкладка вики: в справочник он не идёт.
    assert.notEqual(sourceDoor(BAIGA), sourceDoor(OFFICE));
    assert.equal(sourceDoor({ slug: '', tab: null }), null);
    assert.equal(sourceDoor(null), null);
});

test('карточка парка — справочник, а не статья', () => {
    // Слаг парка лежит в ref_key, а не в slug: в slug он открыл бы статью с этим
    // слагом, а у парка статьи нет.
    assert.equal(sourceDoor(PARK), 'directory');
    assert.equal(sourceDisabled(PARK), false);
    // Вкладку закрыли — чип из истории неактивен, как у офиса.
    assert.equal(sourceDoor({ ...PARK, available: false }), null);
    assert.equal(sourceDisabled({ ...PARK, available: false, tab: null }), true);
});

test('карточка парка ведёт на страницу парка, сводка — во вкладку «Парки»', () => {
    assert.deepEqual(parkTarget(PARK), { tab: 'library', park: 'itaxi' });
    // Слаг не влез в источник (длиннее 64) или это сводка всех парков.
    assert.deepEqual(parkTarget({ ...PARK, ref_key: null }), { tab: 'parks', park: null });
    assert.deepEqual(parkTarget({ ...PARK, ref_key: '  ' }), { tab: 'parks', park: null });
    assert.deepEqual(parkTarget(null), { tab: 'parks', park: null });
});

test('закрытый источник никуда не ведёт', () => {
    // Из истории: раздел закрыли или QR не подтверждён — сервер прячет вкладку,
    // но и с вкладкой на руках закрытый чип дверью не становится.
    assert.equal(sourceDoor({ ...BAIGA, available: false }), null);
    assert.equal(sourceDisabled({ ...BAIGA, available: false }), true);
    assert.equal(sourceDisabled({ slug: null, tab: null }), true);
    assert.equal(sourceDisabled(BAIGA), false);
    assert.equal(sourceDisabled(OFFICE), false);
    assert.equal(sourceDisabled(ARTICLE), false);
});

test('просьба разделу: неделя и водитель из источника', () => {
    assert.deepEqual(baigaFocusOf(BAIGA), { weekId: 3, query: 'ZZ123456' });
    // Фрагмент «Недели» водителя не несёт; старый источник — и недели.
    assert.deepEqual(baigaFocusOf({ tab: 'baiga', ref_id: 3, ref_key: null }), { weekId: 3, query: '' });
    assert.deepEqual(baigaFocusOf({ tab: 'baiga' }), { weekId: null, query: '' });
});

test('классификатор — статья, и открывается на машине из ответа', () => {
    // Дверь — статья: у источника есть слаг, и старый бандл откроет её как статью.
    assert.equal(sourceDoor(CAR), 'article');
    assert.equal(sourceDisabled(CAR), false);
    assert.deepEqual(classifierPrefillOf(CAR),
        { brand: 'Toyota', model: 'Camry', year: '2015', cityId: 'almaty' });
    // Год и город в вопросе не названы — калькулятор оставит свои.
    assert.deepEqual(classifierPrefillOf({ ...CAR, ref_id: null, ref_city: null }),
        { brand: 'Toyota', model: 'Camry', year: '', cityId: '' });
    // Марка со скобками и модель с пробелами доезжают как есть.
    assert.deepEqual(classifierPrefillOf({ ...CAR, ref_key: 'Lada (VAZ)|2121 (4x4)' }),
        { brand: 'Lada (VAZ)', model: '2121 (4x4)', year: '2015', cityId: 'almaty' });
});

test('заготовки нет, когда источник не про машину', () => {
    // Список моделей марки, «Города и тарифы»: машины в источнике нет.
    assert.equal(classifierPrefillOf({ ...CAR, ref_key: null }), null);
    assert.equal(classifierPrefillOf({ ...CAR, ref_key: 'Toyota|' }), null);
    assert.equal(classifierPrefillOf({ ...CAR, ref_key: '|Camry' }), null);
    // Чужой вид источника с похожим ключом заготовкой не становится.
    assert.equal(classifierPrefillOf(BAIGA), null);
    assert.equal(classifierPrefillOf(ARTICLE), null);
    assert.equal(classifierPrefillOf(null), null);
    // Статью закрыли — из истории она никуда не ведёт.
    assert.equal(classifierPrefillOf({ ...CAR, available: false }), null);
});

test('ответ за гейтами раздела супервайзеру не передают', () => {
    assert.equal(canEscalateMessage({ kind: 'answer' }, KINDS), true);
    assert.equal(canEscalateMessage({ kind: 'no_answer' }, KINDS), true);
    assert.equal(canEscalateMessage({ kind: 'answer', gated_by: 'baiga' }, KINDS), false);
    assert.equal(canEscalateMessage({ kind: 'answer', escalation: { id: 1, status: 'open' } }, KINDS), false);
    assert.equal(canEscalateMessage({ kind: 'supervisor' }, KINDS), false);
    assert.equal(canEscalateMessage(null, KINDS), false);
});
