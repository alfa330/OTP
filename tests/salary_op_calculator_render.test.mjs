/*
 * Настоящие компоненты калькуляторов ОП «Верификатор» и «Яндекс Регистрация»
 * прогоняются через react-dom/server: браузерного окружения в проекте нет
 * (ни jsdom, ни puppeteer), а серверный рендер закрывает главное — что карточка
 * результата не падает и показывает те же суммы, что и формулы.
 *
 * Формулы сами по себе проверяют salary_verificator/salary_yandex_reg.test.mjs;
 * здесь — что до экрана доезжают именно они, а не «0 ТГ» из-за опечатки в поле.
 *
 * JSX не используется намеренно: node --test гоняет .mjs без сборки.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { transformSync } = require('esbuild');
const { readFileSync, writeFileSync, mkdirSync } = require('node:fs');
const { join } = require('node:path');
const { pathToFileURL } = require('node:url');

const SRC = new URL('../src/', import.meta.url);
// Собранные модули кладём ВНУТРЬ проекта: из системной временной папки
// `import 'react'` не разрешается — node ищет node_modules вверх от файла.
const CACHE = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
const FORMULA_URL = pathToFileURL(join(process.cwd(), 'src', 'utils', 'salaryFormula.js')).href;

mkdirSync(CACHE, { recursive: true });

// FaIcon тянет за собой весь набор иконок lucide-react — для проверки разметки
// он не нужен, подменяем простым span с тем же className.
const FA_ICON_STUB = "const FaIcon = (props) => React.createElement('span', props);";

function buildModule(relativePath, outName) {
  const jsx = readFileSync(new URL(relativePath, SRC), 'utf8');
  const { code } = transformSync(jsx, { loader: 'jsx', format: 'esm', target: 'node18' });
  // Кавычки после esbuild двойные, поэтому в шаблонах допускаем любые.
  const patched = code
    .replace(/import\s+FaIcon\s+from\s*['"][^'"]*FaIcon['"];?/, FA_ICON_STUB)
    .replace(/from\s*['"][^'"]*utils\/salaryFormula['"]/, `from "${FORMULA_URL}"`)
    .replace(
      /import\s+SalaryCalculationResult\s+from\s*['"]\.\/SalaryCalculationResult['"];?/,
      `import SalaryCalculationResult from "${pathToFileURL(join(CACHE, 'SalaryCalculationResult.mjs')).href}";`,
    );
  const file = join(CACHE, outName);
  writeFileSync(file, patched, 'utf8');
  return pathToFileURL(file).href;
}

// Карточку результата собираем первой — калькуляторы импортируют уже её сборку.
const resultUrl = buildModule('components/salary/SalaryCalculationResult.jsx', 'SalaryCalculationResult.mjs');
const verificatorUrl = buildModule('components/salary/SalaryCalculatorVerificator.jsx', 'SalaryCalculatorVerificator.mjs');
const yandexRegUrl = buildModule('components/salary/SalaryCalculatorYandexReg.jsx', 'SalaryCalculatorYandexReg.mjs');

const SalaryCalculationResult = (await import(resultUrl)).default;
const SalaryCalculatorVerificator = (await import(verificatorUrl)).default;
const SalaryCalculatorYandexReg = (await import(yandexRegUrl)).default;
const { calculateVerificatorSalary, calculateYandexRegSalary } = await import(FORMULA_URL);

// В разметке пробелы неразрывные (Intl ru-RU), поэтому сравниваем по нормализованной строке.
const plain = (html) => html.replace(/ | /g, ' ');

test('калькулятор «Верификатор» рисуется без данных и не падает', () => {
  const html = plain(renderToStaticMarkup(React.createElement(SalaryCalculatorVerificator, { month: '2026-08' })));
  assert.ok(html.includes('Модель: Оператор ОП «Верификатор»'));
  assert.ok(html.includes('Штраф за акции'), 'обе колонки штрафов из таблицы владельца должны быть на экране');
  assert.ok(html.includes('Итого баллов'), 'сумма «качество + премия за план» объясняется прямо в форме');
  // Норма 1 FTE августа (31 день → 22 раб. дня × 8 ч) подставляется сама.
  assert.ok(html.includes('value="176"'));
});

test('калькулятор «Яндекс Регистрация» рисуется без данных и не падает', () => {
  const html = plain(renderToStaticMarkup(React.createElement(SalaryCalculatorYandexReg, { month: '2026-08' })));
  assert.ok(html.includes('Модель: Оператор ОП «Яндекс Регистрация»'));
  assert.ok(html.includes('Поступило заявок (по группе)'));
  assert.ok(html.includes('Мои успешные заявки, шт'), 'бонус считается по личным успешкам');
  assert.ok(html.includes('value="50"'), 'целевая конверсия по схеме — 50%');
});

test('карточка результата «Верификатор» показывает сумму из формулы', () => {
  // Строка «Оператор со стажем» таблицы владельца: итог 172 656 ₸.
  const result = calculateVerificatorSalary({
    hoursWorked: 176,
    hoursNorm: 176,
    sales: 228,
    planPerFte: 440,
    normHoursFte: 176,
    quality: 96.2,
  });
  const html = plain(renderToStaticMarkup(
    React.createElement(SalaryCalculationResult, { salaryResult: result, label: 'Оператор ОП «Верификатор»' })
  ));

  assert.ok(html.includes('172 656,00 ТГ'), 'итог к выплате должен совпадать с таблицей владельца');
  assert.ok(html.includes('84 656,00 ТГ'), 'бонус за качество = оклад × % качества');
  assert.ok(html.includes('88 000,00 ТГ'), 'оклад = часы × ставку');
  assert.ok(html.includes('Сводка по часам, качеству и плану продаж'));
  assert.ok(html.includes('Бонус за план'));
});

test('карточка результата «Яндекс Регистрация» показывает сумму из формулы', () => {
  // Пример владельца: конверсия группы 41/100, план 82%, цена успешки 200 ₸,
  // качество 93% → удержание 10%. 60 личных успешек.
  const result = calculateYandexRegSalary({
    hoursWorked: 176,
    hoursNorm: 176,
    groupRequests: 100,
    groupSuccesses: 41,
    quality: 93,
    deals: 60,
  });
  const html = plain(renderToStaticMarkup(
    React.createElement(SalaryCalculationResult, { salaryResult: result, label: 'Оператор ОП «Яндекс Регистрация»' })
  ));

  assert.ok(html.includes('116 400,00 ТГ'), 'итог = оклад + бонус − удержание');
  assert.ok(html.includes('105 600,00 ТГ'), 'оклад = 176 × 600');
  assert.ok(html.includes('12 000,00 ТГ'), 'бонус = 60 успешек × 200 ₸');
  assert.ok(html.includes('Сводка по часам, конверсии группы и качеству звонков'));
  assert.ok(html.includes('Конверсия группы'));
});

test('карточка не путает модели ОП между собой', () => {
  const verificator = plain(renderToStaticMarkup(React.createElement(SalaryCalculationResult, {
    salaryResult: calculateVerificatorSalary({ hoursWorked: 176, hoursNorm: 176, sales: 440, planPerFte: 440, normHoursFte: 176, quality: 100 }),
  })));
  const yandexReg = plain(renderToStaticMarkup(React.createElement(SalaryCalculationResult, {
    salaryResult: calculateYandexRegSalary({ hoursWorked: 176, hoursNorm: 176, groupRequests: 100, groupSuccesses: 60, deals: 10, quality: 100 }),
  })));

  // У «Верификатора» удержания за качество нет вовсе — качество только добавляет бонус.
  assert.ok(!verificator.includes('Удержано за качество'));
  assert.ok(yandexReg.includes('Удержано за качество'));
  // А у ЯР нет плана продаж — вместо него конверсия группы.
  assert.ok(!yandexReg.includes('Бонус за план'));
  assert.ok(verificator.includes('Бонус за план'));
});
