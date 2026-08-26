import assert from 'node:assert/strict';
import test from 'node:test';

import { selectionTouchesNode } from '../src/components/wiki/useCopyGuard.js';

/* Правило, по которому запрет копирования решает, его это событие или чужое.
 *
 * Слушатель висит на ДОКУМЕНТЕ — иначе он не увидит выделения, начатого выше
 * защищённой карточки. Значит через него проходит вообще всё копирование на
 * странице: адрес статьи кнопкой «Ссылка», сниппет из поиска, строка из
 * соседней панели. Ошибись эта функция в сторону «блокируем» — и портал молча
 * перестанет копировать что бы то ни было; ошибись в другую — тумблер не значит
 * ничего. Проверять её в браузере нечем, поэтому она вынесена из хука.
 */

const range = (over) => ({ intersectsNode: (node) => over.includes(node) });

const selection = (ranges) => ({
    isCollapsed: ranges.length === 0,
    rangeCount: ranges.length,
    getRangeAt: (i) => ranges[i],
});

const CARD = { name: 'article' };
const ELSEWHERE = { name: 'sidebar' };

test('выделение внутри карточки — это наше событие', () => {
    assert.equal(selectionTouchesNode(selection([range([CARD])]), CARD), true);
});

test('выделение мимо карточки не трогаем', () => {
    // Ровно этот случай — кнопка «Ссылка» и копирование из поиска. Блокировка
    // здесь выглядела бы как поломка портала, а не как защита статьи.
    assert.equal(selectionTouchesNode(selection([range([ELSEWHERE])]), CARD), false);
});

test('хватает одного диапазона из нескольких', () => {
    // Ctrl+клик по абзацам даёт мультивыделение: первый диапазон может лежать
    // вне карточки, а второй — внутри, и текст всё равно уедет в буфер.
    const sel = selection([range([ELSEWHERE]), range([CARD])]);
    assert.equal(selectionTouchesNode(sel, CARD), true);
});

test('схлопнутое выделение — это просто курсор', () => {
    // Ctrl+C без выделения копирует пустоту. Тост на него был бы враньём:
    // человек ничего не пытался унести.
    assert.equal(selectionTouchesNode(selection([]), CARD), false);
});

test('нет выделения или нет узла — не блокируем', () => {
    // Узел берётся из ref в момент события: карточка могла уже размонтироваться
    // (ушли на другую статью), а слушатель ещё живёт до уборки эффекта.
    assert.equal(selectionTouchesNode(null, CARD), false);
    assert.equal(selectionTouchesNode(selection([range([CARD])]), null), false);
});

test('без intersectsNode падаем на contains, а не на исключение', () => {
    // Мобильный Safari в режиме чтения и старые jsdom отдают выделение
    // объектом без intersectsNode. Исключение внутри слушателя copy съело бы
    // и запрет, и всё, что за ним, — без единой строки в консоли.
    const inside = { name: 'абзац' };
    const node = { contains: (candidate) => candidate === inside };
    const sel = {
        isCollapsed: false,
        rangeCount: 1,
        getRangeAt: () => ({ commonAncestorContainer: inside }),
    };
    assert.equal(selectionTouchesNode(sel, node), true);

    const outside = {
        isCollapsed: false,
        rangeCount: 1,
        getRangeAt: () => ({ commonAncestorContainer: { name: 'чужой' } }),
    };
    assert.equal(selectionTouchesNode(outside, node), false);
});

test('пустой диапазон в списке не роняет проверку', () => {
    const sel = { isCollapsed: false, rangeCount: 2, getRangeAt: (i) => (i === 0 ? null : range([CARD])) };
    assert.equal(selectionTouchesNode(sel, CARD), true);
});
