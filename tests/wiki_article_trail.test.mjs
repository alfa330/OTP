import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import {
  TRAIL_LIMIT,
  backLabel,
  openTrail,
  popTrail,
  pushTrail,
  trailBack,
  trailTop,
} from '../src/components/wiki/articleTrail.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel) => readFileSync(join(ROOT, rel), 'utf8');

/* ── Правила цепочки ──────────────────────────────────────────────────────── */

test('открытие из списка начинает цепочку заново', () => {
  const trail = pushTrail(openTrail('tarify'), 'shtrafy');
  assert.equal(trail.length, 2);
  // Открыли третью статью из оглавления — путь через «Тарифы» больше не при чём.
  const fresh = openTrail('grafiki');
  assert.deepEqual(fresh, [{ slug: 'grafiki' }]);
  assert.equal(trailBack(fresh), null);
});

test('пустой слаг закрывает статью', () => {
  assert.deepEqual(openTrail(null), []);
  assert.equal(trailTop([]), null);
});

test('переход по сети статей помнит, откуда ушли', () => {
  const trail = pushTrail(openTrail('tarify'), 'shtrafy',
                          { title: 'Тарифы', scrollTop: 1840 });
  assert.equal(trailTop(trail).slug, 'shtrafy');
  assert.equal(trailBack(trail).slug, 'tarify');
  assert.equal(trailBack(trail).title, 'Тарифы');
  // Позиция чтения нужна возврату: человек ушёл по ссылке из середины текста.
  assert.equal(trailBack(trail).scrollTop, 1840);
});

test('возврат снимает ровно один шаг, а с последнего уводит в список', () => {
  const trail = pushTrail(pushTrail(openTrail('a'), 'b'), 'c');
  const back = popTrail(trail);
  assert.equal(trailTop(back).slug, 'b');
  assert.equal(trailTop(popTrail(back)).slug, 'a');
  assert.deepEqual(popTrail(popTrail(back)), []);
  assert.deepEqual(popTrail([]), []);
});

test('подсветка и заготовка классификатора возвращаются вместе со статьёй', () => {
  const found = openTrail('tarify', { highlight: 'комиссия', prefill: { model: 'Vento' } });
  const deeper = pushTrail(found, 'shtrafy', { title: 'Тарифы' });
  const back = popTrail(deeper);
  assert.equal(trailTop(back).highlight, 'комиссия');
  assert.deepEqual(trailTop(back).prefill, { model: 'Vento' });
  // Шаг вперёд своей подсветки не наследует — в новой статье искать нечего.
  assert.equal(trailTop(deeper).highlight, undefined);
});

test('взаимная пара статей не наматывает цепочку, а обрезает её', () => {
  /* «Взаимная» связь в вике — штатное состояние (блок «Связанные материалы»
     помечает такие строки), поэтому ходить туда-обратно будут. */
  let trail = openTrail('tarify');
  for (let i = 0; i < 6; i += 1) {
    trail = pushTrail(trail, i % 2 === 0 ? 'shtrafy' : 'tarify');
    assert.ok(trail.length <= 2, `шаг ${i}: цепочка выросла до ${trail.length}`);
  }
  // Вернулись в «Тарифы» — путь закольцевался, дальше идти назад некуда.
  assert.deepEqual(trail.map((entry) => entry.slug), ['tarify']);
  assert.equal(trailBack(trail), null);
});

test('шаг на статью, которая уже на экране, ничего не меняет', () => {
  const trail = pushTrail(openTrail('tarify'), 'shtrafy');
  assert.deepEqual(pushTrail(trail, 'shtrafy').map((e) => e.slug), ['tarify', 'shtrafy']);
});

test('цепочка не растёт бесконечно — старое отрезается с хвоста', () => {
  let trail = openTrail('s0');
  for (let i = 1; i < TRAIL_LIMIT + 5; i += 1) trail = pushTrail(trail, `s${i}`);
  assert.equal(trail.length, TRAIL_LIMIT);
  assert.equal(trailTop(trail).slug, `s${TRAIL_LIMIT + 4}`);
  assert.equal(trail[0].slug, `s${5}`);
});

test('пустой слаг цепочку не трогает', () => {
  const trail = openTrail('tarify');
  assert.equal(pushTrail(trail, ''), trail);
  assert.equal(pushTrail(trail, null), trail);
});

/* ── Подпись кнопки ───────────────────────────────────────────────────────── */

test('без предыдущей статьи кнопка обещает список', () => {
  assert.equal(backLabel(null), 'К списку');
  assert.equal(backLabel({ slug: 'tarify' }), 'К списку');
});

test('с предыдущей статьёй кнопка называет её, длинный заголовок режется', () => {
  assert.equal(backLabel({ title: 'Тарифы' }), 'Назад: «Тарифы»');
  const long = backLabel({ title: 'Регламент подключения водителей на личном авто' });
  assert.ok(long.endsWith('…»'), long);
  assert.ok(long.length < 34, long);
});

/* ── Стражи: правило дороже реализации ───────────────────────────────────── */

test('витрина ведёт цепочку, а не одну открытую статью', () => {
  const src = read('src/components/wiki/WikiLibrary.jsx');
  assert.ok(src.includes("from './articleTrail'"), 'WikiLibrary обязана брать правила из articleTrail');
  // Возврат — шаг назад, а не «закрыть статью».
  assert.ok(src.includes('popTrail'), 'возврат обязан снимать шаг цепочки');
  assert.ok(!src.includes('setOpenSlug'),
            'открытая статья больше не отдельное значение — иначе цепочка разойдётся с экраном');
  // Переход из статьи и открытие из списка — РАЗНЫЕ действия.
  assert.ok(src.includes('onOpenArticle={openLinkedArticle}'),
            'ссылка из статьи обязана быть шагом вперёд, а не открытием с нуля');
});

test('подпись кнопки возврата приходит из цепочки, а не зашита в статью', () => {
  const src = read('src/components/wiki/WikiArticle.jsx');
  assert.ok(src.includes('backLabel(backTo)'),
            'подпись обязана считаться по цепочке, иначе кнопка обещает не то, что делает');
  assert.ok(!/>\s*К списку\s*</.test(src),
            'жёстко вписанная подпись «К списку» вернула бы кнопке старое обещание');
  // Заголовок статьи-источника — единственное, чем витрина может подписать кнопку.
  assert.ok(src.includes('onOpenArticle(target, { title:'),
            'переход обязан нести наверх заголовок статьи, из которой уходят');
});

test('список после статьи показывается сверху', () => {
  const lib = read('src/components/wiki/WikiLibrary.jsx');
  assert.ok(lib.includes('scrollPortalTo(0)'),
            'возврат в витрину обязан сбрасывать прокрутку — она остаётся от статьи');
  const article = read('src/components/wiki/WikiArticle.jsx');
  assert.ok(article.includes('restoreScroll'),
            'возврат в статью обязан ставить человека туда, где он оборвал чтение');
});

/* ── Дверь: вкладка, с которой открыли статью ─────────────────────────────── */

test('готовая подпись двери старше заголовка статьи', () => {
  assert.equal(backLabel({ label: 'К журналу' }), 'К журналу');
  // Дверь и предыдущая статья в одну кнопку не попадают, но правило должно быть
  // однозначным: пришла подпись — она и рисуется.
  assert.equal(backLabel({ label: 'К списку статей', title: 'Тарифы' }), 'К списку статей');
});

test('дверь живёт отдельно от цепочки — иначе её срежет потолок глубины', () => {
  const src = read('src/components/wiki/WikiLibrary.jsx');
  assert.ok(src.includes('const [door, setDoor]'),
            'дверь обязана быть своим состоянием: цепочка обрезается с самого старого конца');
  // Проверяем на самой цепочке: корень с меткой действительно уходит.
  let trail = openTrail('s0', { from: { tab: 'catalog' } });
  for (let i = 1; i < TRAIL_LIMIT + 2; i += 1) trail = pushTrail(trail, `s${i}`);
  assert.equal(trail[0].from, undefined, 'корень с меткой срезан — значит хранить её там нельзя');
});

test('выход из статьи один на кнопку и на архивацию', () => {
  const src = read('src/components/wiki/WikiLibrary.jsx');
  const archived = /onArchived=\{\(\) => \{[\s\S]*?\n {16}\}\}/.exec(src);
  assert.ok(archived, 'не нашли обработчик архивации');
  assert.ok(archived[0].includes('closeArticle()'),
            'архивация обязана уходить тем же выходом, что кнопка возврата');
  // Решение о переключении вкладки принимается ВНЕ апдейтера setTrail: внутри
  // React зовёт его в фазе рендера и повторяет в StrictMode.
  const close = /const closeArticle = useCallback\([\s\S]*?\}, \[[^\]]*\]\);/.exec(src);
  assert.ok(close, 'не нашли closeArticle');
  assert.ok(!/setTrail\(\(prev\)[\s\S]*returnTo/.test(close[0]),
            'возврат на вкладку нельзя решать внутри апдейтера состояния');
});

test('каждый вход в статью с другой вкладки несёт дверь', () => {
  const src = read('src/components/wiki/WikiView.jsx');
  const opens = src.match(/setSearchTarget\(\{[^}]*\}/g) || [];
  assert.ok(opens.length >= 7, `ожидали все входы, нашли ${opens.length}`);
  opens.forEach((call) => {
    assert.ok(call.includes('from'), `вход без двери: ${call}`);
  });
  assert.ok(src.includes('onReturnTo={returnFromArticle}'),
            'витрина обязана уметь вернуть человека на вкладку-источник');
  // Права могли сузиться, пока человек читал статью.
  assert.ok(/tabs\.some\(\(t\) => t\.key === exit\.tab/.test(src),
            'возврат обязан сверяться с набором доступных вкладок');
  // Прокрутку сбрасывает родитель: витрина размонтируется тем же коммитом.
  const back = /const returnFromArticle = useCallback\([\s\S]*?\}, \[[^\]]*\]\);/.exec(src);
  assert.ok(back && back[0].includes('scrollPortalTo(0)'),
            'вкладка-источник обязана открыться сверху, а не на прокрутке статьи');
});

test('место в каталоге переживает уход в статью', () => {
  const view = read('src/components/wiki/WikiView.jsx');
  assert.ok(view.includes('const [catalogSection, setCatalogSection]'),
            'выбранный раздел обязан жить выше вкладки — она размонтируется');
  assert.ok(view.includes('const [catalogOpenSections, setCatalogOpenSections]'),
            'без раскрытых веток выбранная строка вернётся невидимой');

  const cat = read('src/components/wiki/WikiCatalog.jsx');
  assert.ok(!/const \[selected, setSelected\] = useState/.test(cat),
            'выбор больше не локальное состояние каталога');
  assert.ok(!/const \[openSections, setOpenSections\] = useState/.test(cat),
            'раскрытые ветки больше не локальное состояние каталога');
  // Наверх уезжает идентификатор: снимок {name, path} пережил бы переименование.
  assert.ok(cat.includes('const selected = useMemo('),
            'имя и путь раздела обязаны считаться из свежего дерева');
  // Ветку раскрываем целиком, иначе выбранная строка прячется под свёрнутым предком.
  const select = /const selectSection = \([\s\S]*?\n {4}\};/.exec(cat);
  assert.ok(select, 'не нашли выбор раздела');
  assert.ok(select[0].includes('ancestorsOf(section)'),
            'раскрывать надо всю ветку до раздела, а не его одного');
});
