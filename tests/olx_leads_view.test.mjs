/*
 * Раздел «Лиды OLX»: разметка и область видимости.
 *
 * Первый тест здесь появился по живому дефекту. Когда панели раздела научили
 * молчать в фоне (`visible`), тот же проп по невнимательности прочитали и в
 * выборе чатов для отбивки — а туда его никто не передавал. Компонент собирался
 * и отрисовывался, но на первом же эффекте падал с ReferenceError, и весь раздел
 * у глобального админа гас. Серверный рендер такое не ловит: `useEffect` в нём
 * не выполняется, — поэтому проверяем исходник.
 *
 * Правило простое: каждое имя в массиве зависимостей эффекта должно быть в
 * области видимости своего компонента — среди пропсов, среди локальных
 * объявлений или на уровне модуля. Имя, которого нет нигде, — это падение при
 * первом же показе.
 *
 * Остальные тесты стерегут решения по виду раздела: состояние кабинетов и выбор
 * канала уведомлений живут в модалке, а даты выбирают нашим пикером.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SOURCE = readFileSync(new URL('../src/components/olx/OlxLeadsView.jsx',
                                    import.meta.url), 'utf8');

/* ─── Разбор исходника ──────────────────────────────────────────────────── */

/** Имена, объявленные на уровне модуля: импорты и верхние `const`. */
const moduleScope = () => {
  const names = new Set(['React', 'window', 'document', 'console', 'Math', 'Date',
                         'Number', 'String', 'Boolean', 'Object', 'Array', 'JSON',
                         'URLSearchParams', 'Blob', 'setTimeout', 'clearTimeout',
                         'setInterval', 'clearInterval']);
  for (const [, inside] of SOURCE.matchAll(/import\s+\{([^}]+)\}\s+from/g)) {
    inside.split(',').forEach((part) => {
      const name = part.split(/\s+as\s+/).pop().trim();
      if (name) names.add(name);
    });
  }
  for (const [, name] of SOURCE.matchAll(/^import\s+(\w+)[\s,]/gm)) names.add(name);
  for (const [, name] of SOURCE.matchAll(/^(?:const|let|function)\s+(\w+)/gm)) names.add(name);
  return names;
};

/** Куски файла по верхнеуровневым объявлениям: [{ name, props, body }]. */
const components = () => {
  const starts = [...SOURCE.matchAll(/^(?:const|function)\s+(\w+)/gm)];
  return starts.map((match, index) => {
    const from = match.index;
    const to = index + 1 < starts.length ? starts[index + 1].index : SOURCE.length;
    const body = SOURCE.slice(from, to);
    /* Пропсы берём из первой же деструктуризации в сигнатуре — она может
       занимать несколько строк, поэтому точка не должна спотыкаться о перевод. */
    const signature = /^(?:const|function)\s+\w+\s*=?\s*\(\s*\{([\s\S]*?)\}\s*\)/.exec(body);
    const props = new Set();
    if (signature) {
      signature[1].split(',').forEach((part) => {
        const name = part.split('=')[0].trim();
        if (/^\w+$/.test(name)) props.add(name);
      });
    }
    return { name: match[1], props, body };
  });
};

/** Локальные имена компонента: useState, useCallback, обычные const/let. */
const locals = (body) => {
  const names = new Set();
  for (const [, inside] of body.matchAll(/const\s*\[([^\]]+)\]\s*=/g)) {
    inside.split(',').forEach((part) => {
      const name = part.trim();
      if (/^\w+$/.test(name)) names.add(name);
    });
  }
  for (const [, name] of body.matchAll(/(?:const|let)\s+(\w+)\s*=/g)) names.add(name);
  return names;
};

/** Имена из массивов зависимостей: `}, [load, visible]);`. */
const dependencies = (body) => {
  const names = new Set();
  for (const [, inside] of body.matchAll(/\}\s*,\s*\[([^\]]*)\]\s*\)/g)) {
    inside.split(',').forEach((part) => {
      const root = part.trim().split(/[.?[(]/)[0].trim();
      if (/^[A-Za-z_$][\w$]*$/.test(root)) names.add(root);
    });
  }
  return names;
};

/* ─── Тесты ─────────────────────────────────────────────────────────────── */

test('каждая зависимость эффекта есть в области видимости своего компонента', () => {
  const module_ = moduleScope();
  const missing = [];
  for (const { name, props, body } of components()) {
    const own = locals(body);
    for (const dependency of dependencies(body)) {
      if (props.has(dependency) || own.has(dependency) || module_.has(dependency)) continue;
      missing.push(`${name}: ${dependency}`);
    }
  }
  assert.deepEqual(missing, [],
    `имя из массива зависимостей нигде не объявлено — раздел упадёт на первом же показе:\n${missing.join('\n')}`);
});

test('даты выбирают нашим пикером, а не системным полем', () => {
  /* Системный `<input type="date">` рисует браузер: своя шапка, свои кнопки,
     чужая деталь рядом с нашими фильтрами. */
  assert.equal(/<input[^>]*type="date"/.test(SOURCE), false);
  assert.match(SOURCE, /IosDateRangePicker/);
  assert.match(SOURCE, /IosDatePicker/);
});

test('пресеты периода — модульная константа, а не литерал внутри рендера', () => {
  /* Новый массив на каждый рендер уходил бы пропсом в пикер. */
  assert.match(SOURCE, /^const DATE_PRESETS = \[/m);
  const presets = /^const DATE_PRESETS = \[([\s\S]*?)^\];/m.exec(SOURCE)[1];
  // Форма пресета у примитива одна: { label, range: () => ({ from, to }) }.
  for (const line of presets.trim().split('\n')) {
    assert.match(line, /label:.*range: \(\) => \(\{ from:.*to:/);
  }
});

test('кабинеты и канал уведомлений живут в модалке, а не на главном экране', () => {
  const modal = /^const SettingsModal = [\s\S]*?^\);/m.exec(SOURCE);
  assert.ok(modal, 'модалка настроек не найдена');
  assert.match(modal[0], /<CabinetsPanel/);
  assert.match(modal[0], /<AlertChatsPicker/);

  /* На главном экране — только кнопка. Если панель вернут в разметку раздела,
     считаться она будет здесь вторым вхождением вне модалки. */
  const outside = SOURCE.replace(modal[0], '');
  assert.equal(/<CabinetsPanel/.test(outside), false);
  assert.equal(/<AlertChatsPicker/.test(outside), false);
  assert.match(SOURCE, /<SettingsButton/);
});

test('кнопка настроек молчит, пока всё в порядке', () => {
  const button = /^const SettingsButton = [\s\S]*?^};/m.exec(SOURCE)[0];
  /* Тревога — единственное, что здесь красится: раскрась спокойное состояние,
     и тревожное перестанет отличаться. */
  assert.match(button, /troubled\(health\)/);
  assert.match(button, /bad \? '[^']*amber/);
  assert.match(button, /bad > 0 &&/);
});

test('в беду записываем и сломанные кабинеты, и замолчавшие', () => {
  // \r?\n: на Windows-машине исходник лежит с CRLF, и с одним \n exec отдавал
  // null — тест падал не на утверждении, а на чтении [0] у null.
  const troubled = /^const troubled = [\s\S]*?;\r?\n/m.exec(SOURCE)[0];
  assert.match(troubled, /is_enabled/);      // выключенный кабинет не проблема
  assert.match(troubled, /state !== 'ok'/);  // потерян доступ или была ошибка
  assert.match(troubled, /is_stale/);        // «тихий» простой из ТЗ
});
