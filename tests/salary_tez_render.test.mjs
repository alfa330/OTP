import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');
const cache = resolve('node_modules/.cache/tez-plan-tests');
mkdirSync(cache, { recursive: true });
function component(name) {
  const file = resolve(cache, name + '.cjs');
  buildSync({ entryPoints: [`src/components/salary/${name}.jsx`], outfile: file,
    bundle: true, format: 'cjs', platform: 'node', external: ['react', 'react-dom', 'axios'],
    plugins: [],
  });
  return require(file).default;
}
const Summary = component('TezDepartmentPlanSummary');
const Calculator = component('SalaryCalculatorTez');
const PlanCell = component('TezOpPlanCell');
const render = (Type, props) => renderToStaticMarkup(React.createElement(Type, props));
const data = { fte_total: 2, operators_count: 2, plan_per_fte: 200, norm_hours_fte: 160,
  coefficient: 0.8, plan_total: 320, actual_hours: 132, actual_fte: 0.825,
  actual_plan: 132, successes_total: 66, closure_pct: 50 };

test('отдел показывает четыре разных показателя', () => {
  const html = render(Summary, { summary: data });
  for (const text of ['Групповой план на месяц', 'Фактический план отдела', 'Факт продаж',
    'Выполнение фактического плана', '320', '132', '66', '50%']) assert.ok(html.includes(text), text);
});
test('нулевые часы отличаются от отсутствующего FTE или будущего месяца', () => {
  const zero = render(Summary, { summary: { ...data, actual_plan: 0, closure_pct: null } });
  assert.match(zero, />0<\/div>/);
  const noFte = render(Summary, { summary: { ...data, fte_total: 0, actual_plan: null, closure_pct: null } });
  assert.ok(noFte.includes('На 1 число в отделе нет FTE'));
  const future = render(Summary, { summary: { ...data, fte_total: null } });
  assert.ok(future.includes('FTE будет зафиксирован'));
});
test('ячейка индивидуального плана показывает факт с нормой FTE и коэффициентом новичка', () => {
  const props = { planPerFte: 200, normHoursFte: 160, normHours: 80, factHours: 40, month: '2026-10' };
  assert.match(render(PlanCell, props), />50<\/span>/);
  assert.match(render(PlanCell, { ...props, hireDate: '2026-10-15' }), />40<\/span>/);
  assert.match(render(PlanCell, { ...props, factHours: 0 }), />0<\/span>/);
});
test('обе модели TEZ рендерятся, норма FTE относится только к ОП', () => {
  assert.ok(render(Calculator, { model: 'tez_op' }).includes('Норма часов на 1 FTE'));
  assert.ok(!render(Calculator, { model: 'tez_line' }).includes('Норма часов на 1 FTE'));
});
