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
const {
  calculateVerificatorSalary,
  calculateYandexRegSalary,
  VERIFICATOR_QUALITY_POINT_STEPS,
  VERIFICATOR_CHAT_POINT_STEPS,
} = await import(FORMULA_URL);

// Как карточка собирает подпись шкалы — повторяем здесь, чтобы тест ловил
// расхождение между шкалой в формулах и текстом на экране.
const stepsText = (steps) => steps.map((s) => `${s.label} → ${s.points}`).join(', ');

// В разметке пробелы неразрывные (Intl ru-RU), поэтому сравниваем по нормализованной строке.
const plain = (html) => html.replace(/ | /g, ' ');

test('калькулятор «Верификатор» рисуется без данных и не падает', () => {
  const html = plain(renderToStaticMarkup(React.createElement(SalaryCalculatorVerificator, { month: '2026-08' })));
  assert.ok(html.includes('Модель: Оператор ОП «Верификатор»'));
  assert.ok(html.includes('Штраф за акции'), 'обе колонки штрафов из файла заказчика должны быть на экране');
  assert.ok(html.includes('Чаты в час'), 'третья шкала баллов вводится отдельным полем');
  assert.ok(html.includes('Итого баллов'), 'сумма «качество + план + чаты» объясняется прямо в форме');
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
  // Строка «Ночник» файла заказчика: 176 ч, 198 продаж при ночном плане 220,
  // качество 91%, 27 чатов/час → 145 баллов, бонус 127 600 ₸, итог 215 600 ₸.
  const result = calculateVerificatorSalary({
    hoursWorked: 176,
    hoursNorm: 176,
    sales: 198,
    planPerFte: 440,
    normHoursFte: 176,
    nightShift: true,
    quality: 91,
    chatsPerHour: 27,
  });
  const html = plain(renderToStaticMarkup(
    React.createElement(SalaryCalculationResult, { salaryResult: result, label: 'Оператор ОП «Верификатор»' })
  ));

  assert.ok(html.includes('215 600,00 ТГ'), 'итог к выплате должен совпадать с файлом заказчика');
  assert.ok(html.includes('127 600,00 ТГ'), 'сумма бонусов = сумма за часы × итого баллов');
  assert.ok(html.includes('88 000,00 ТГ'), 'сумма за часы = часы × ставку');
  assert.ok(html.includes('Сводка по часам, баллам и плану продаж'));
  assert.ok(html.includes('Бонус за план'));
  assert.ok(html.includes('Бонус за чаты'), 'третья шкала показывается отдельной строкой');
});

test('подсказки карточки «Верификатор» собраны из самих шкал баллов', () => {
  // Страж от расхождения текста и формулы: раньше в тултипе висели ступени
  // премии за план 0/5/10/20/30, которых в модели давно нет.
  const result = calculateVerificatorSalary({
    hoursWorked: 176,
    hoursNorm: 176,
    sales: 300,
    planPerFte: 440,
    normHoursFte: 176,
    newbie: true,
    quality: 88,
    chatsPerHour: 22,
  });
  const html = plain(renderToStaticMarkup(
    React.createElement(SalaryCalculationResult, { salaryResult: result, label: 'Оператор ОП «Верификатор»' })
  ));

  assert.ok(
    html.includes(stepsText(VERIFICATOR_QUALITY_POINT_STEPS)),
    'подпись шкалы качества должна выводиться из VERIFICATOR_QUALITY_POINT_STEPS',
  );
  assert.ok(
    html.includes(stepsText(VERIFICATOR_CHAT_POINT_STEPS)),
    'подпись шкалы чатов должна выводиться из VERIFICATOR_CHAT_POINT_STEPS',
  );
  // Ни одной ступени прежней премии за план в разметке остаться не должно.
  assert.ok(!html.includes('0–79,9% → 0%'), 'старая шкала премии за план не должна остаться в тексте');
  // Регистр важен: в прежней карточке фраза начиналась с заглавной «Ступеней нет»,
  // и страж в нижнем регистре не сработал бы никогда.
  assert.ok(!/ступеней нет/i.test(html), 'качество больше не «прямой процент без ступеней»');
  assert.ok(!/оклад × % качества/i.test(html), 'бонус за качество больше не прямой процент оклада');
  // Баллы в подсказке — те же, что в результате: 20 за качество, 15 за чаты.
  assert.equal(result.qualityPoints, 20);
  assert.equal(result.chatPoints, 15);
  assert.ok(html.includes('120,23%'), 'итого баллов — с запятой, как и деньги рядом');
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
